"""
routers/tts.py — Text-to-Speech generation endpoints (Phase 2B).

GET    /api/tts/voices                              — list available voices
POST   /api/tts/generate                            — synthesize arbitrary text
GET    /api/tts/{audio_id}                          — get audio record status
GET    /api/tts/{audio_id}/audio                    — stream the MP3 file (safe)
DELETE /api/tts/{audio_id}                          — delete record + file
POST   /api/tts/generate-from-content/{project_id} — synthesize from script

Security:
  - Audio files are served via ID only — never by filesystem path.
  - Path traversal prevented in the provider layer.
  - No credentials or secrets are exposed in any response.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from backend.content_models import ContentProject, GeneratedScriptRecord
from backend.db import get_db
from backend.tts_models import (
    AudioResponse,
    AudioStatus,
    GeneratedAudio,
    TTSGenerateFromContentRequest,
    TTSGenerateRequest,
    TTSVoiceResponse,
    audio_to_response,
)
from backend.services.tts.base import (
    TTSConfigError,
    TTSException,
    TTSGenerationError,
    TTSNetworkError,
    TTSRateLimitError,
    TTSValidationError,
)
from backend.services.tts.factory import get_tts_provider
from backend.services.tts.manager import AutoTTSManager
from backend.services.tts.narration import extract_narration_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tts", tags=["tts"])

# ── Voice list cache ──────────────────────────────────────────────────────────
# Cache the voice list in memory to avoid fetching on every request.
# Invalidated on restart (acceptable — list rarely changes).
_voice_cache: list[TTSVoiceResponse] | None = None


# ── GET /api/tts/voices ───────────────────────────────────────────────────────

@router.get("/voices", response_model=list[TTSVoiceResponse])
async def list_voices(
    language: Optional[str] = Query(None, description="ISO 639-1 language code filter, e.g. 'en'"),
) -> list[TTSVoiceResponse]:
    """
    Return available TTS voices.

    Optionally filter by language code (e.g. ?language=en).
    Results are cached in memory until the backend restarts.
    """
    global _voice_cache

    try:
        provider = get_tts_provider()

        if _voice_cache is None:
            logger.info("Fetching TTS voice list from provider")
            voices = await provider.get_voices(language=None)
            _voice_cache = []
            for v in voices:
                # Determine provider tag: local voices start with "local-"
                prov = "local" if v.name.startswith("local-") else "edge"
                _voice_cache.append(TTSVoiceResponse(
                    name=v.name,
                    locale=v.locale,
                    language=v.language,
                    gender=v.gender,
                    provider=prov,
                ))
            logger.info("Cached %d TTS voices", len(_voice_cache))

        if language:
            lang = language.lower()[:2]
            return [v for v in _voice_cache if v.locale.lower().startswith(lang)]

        return _voice_cache

    except TTSConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except TTSNetworkError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except TTSException as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ── POST /api/tts/generate ────────────────────────────────────────────────────

@router.post("/generate", status_code=status.HTTP_202_ACCEPTED, response_model=AudioResponse)
def generate_tts(
    body: TTSGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AudioResponse:
    """
    Synthesize arbitrary text to speech.

    Creates an audio record, fires background synthesis, returns immediately.
    Poll GET /api/tts/{audio_id} for status.
    """
    provider = get_tts_provider()
    voice = body.voice or os.getenv("TTS_DEFAULT_VOICE", "en-US-AriaNeural")

    # Use a sentinel project ID for standalone (not linked to a content project)
    _STANDALONE = "standalone"

    audio = GeneratedAudio(
        id=str(uuid.uuid4()),
        content_project_id=_STANDALONE,
        voice=voice,
        language=body.language,
        text_length=len(body.text),
        status=AudioStatus.PENDING,
    )
    db.add(audio)
    db.commit()
    db.refresh(audio)

    background_tasks.add_task(
        _run_tts_task,
        audio_id=audio.id,
        text=body.text,
        voice=voice,
    )

    return audio_to_response(audio)


# ── POST /api/tts/generate-from-content/{project_id} ─────────────────────────

@router.post(
    "/generate-from-content/{project_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AudioResponse,
)
def generate_from_content(
    project_id: str,
    body: TTSGenerateFromContentRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AudioResponse:
    """
    Generate TTS audio from a content project's generated script.

    Extracts narration text from the script (hook + scene narrations),
    then synthesizes it to MP3.

    Returns immediately with a pending audio record.
    Poll GET /api/tts/{audio_id} for completion.

    If an identical (same project + voice) completed audio already exists,
    returns the existing record without regenerating.
    """
    # Load content project
    project = db.query(ContentProject).filter(ContentProject.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Content project not found: {project_id}",
        )

    # Validate script exists
    if not project.script:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This content project has no generated script. Generate a script first.",
        )

    voice = body.voice or os.getenv("TTS_DEFAULT_VOICE", "en-US-AriaNeural")

    # Check for existing completed audio with same project + voice
    existing = (
        db.query(GeneratedAudio)
        .filter(
            GeneratedAudio.content_project_id == project_id,
            GeneratedAudio.voice == voice,
            GeneratedAudio.status == AudioStatus.COMPLETED,
        )
        .order_by(GeneratedAudio.created_at.desc())
        .first()
    )
    if existing and existing.file_path and Path(existing.file_path).exists():
        logger.info(
            "Reusing existing audio for project=%s voice=%s audio_id=%s",
            project_id, voice, existing.id,
        )
        return audio_to_response(existing)

    # Extract narration text
    try:
        narration = extract_narration_text(project.script)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Create audio record
    audio = GeneratedAudio(
        id=str(uuid.uuid4()),
        content_project_id=project_id,
        voice=voice,
        language=project.language,
        text_length=len(narration),
        status=AudioStatus.PENDING,
    )
    db.add(audio)
    db.commit()
    db.refresh(audio)

    background_tasks.add_task(
        _run_tts_task,
        audio_id=audio.id,
        text=narration,
        voice=voice,
    )

    logger.info(
        "TTS generation queued: project=%s voice=%s audio_id=%s text_len=%d",
        project_id, voice, audio.id, len(narration),
    )

    return audio_to_response(audio)


# ── GET /api/tts/{audio_id} ───────────────────────────────────────────────────

@router.get("/{audio_id}", response_model=AudioResponse)
def get_audio(
    audio_id: str,
    db: Session = Depends(get_db),
) -> AudioResponse:
    """Poll the status of a TTS generation job."""
    audio = _get_audio_or_404(audio_id, db)
    return audio_to_response(audio)


# ── GET /api/tts/{audio_id}/audio ────────────────────────────────────────────

@router.get("/{audio_id}/audio")
def stream_audio(
    audio_id: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """
    Stream the generated MP3 file.

    Security: files are served by audio_id only — the client never
    specifies a filesystem path.  Path traversal is impossible.
    """
    audio = _get_audio_or_404(audio_id, db)

    if audio.status != AudioStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Audio is not ready yet. Current status: {audio.status}",
        )

    if not audio.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file path not recorded.",
        )

    file_path = Path(audio.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file not found on disk. It may have been deleted.",
        )

    # Determine media type from file extension
    ext = file_path.suffix.lower()
    media_type = "audio/wav" if ext == ".wav" else "audio/mpeg"

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=f"narration_{audio_id}{ext}",
    )


# ── DELETE /api/tts/{audio_id} ────────────────────────────────────────────────

@router.delete("/{audio_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_audio(
    audio_id: str,
    db: Session = Depends(get_db),
):
    """Delete the audio record and its MP3 file from disk."""
    audio = _get_audio_or_404(audio_id, db)

    # Delete file from disk first
    if audio.file_path:
        try:
            fp = Path(audio.file_path)
            if fp.exists():
                fp.unlink()
                logger.info("Deleted audio file: %s", fp)
        except OSError as exc:
            logger.warning("Could not delete audio file %s: %s", audio.file_path, exc)

    db.delete(audio)
    db.commit()


# ── Background task ────────────────────────────────────────────────────────────

def _run_tts_task(audio_id: str, text: str, voice: str) -> None:
    """
    Background task: run TTS synthesis and update the DB record.

    Uses its own SessionLocal (background tasks run outside request context).
    Runs the async synthesize() call inside a new event loop.
    """
    from backend.db import SessionLocal

    db = SessionLocal()
    try:
        audio = db.query(GeneratedAudio).filter(GeneratedAudio.id == audio_id).first()
        if not audio:
            return

        audio.status = AudioStatus.GENERATING
        db.commit()

        try:
            provider = get_tts_provider()
            output_dir = Path(os.getenv("TTS_OUTPUT_DIR", r"G:\youtube-uploader\data\audio"))
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"{audio_id}.mp3")

            # Run async synthesis in a new event loop
            result = asyncio.run(provider.synthesize(text, voice, output_path))

            # Determine which provider actually ran (auto-manager may have fallen back)
            used_provider = (
                provider.last_used_provider
                if isinstance(provider, AutoTTSManager)
                else provider.provider_name
            )

            audio.file_path        = result.file_path
            audio.file_size_bytes  = result.file_size_bytes
            audio.duration_seconds = result.duration_seconds
            audio.provider         = used_provider
            audio.status           = AudioStatus.COMPLETED
            db.commit()

            logger.info(
                "TTS task completed: audio_id=%s provider=%s duration=%.1fs size=%d bytes",
                audio_id, used_provider, result.duration_seconds or 0, result.file_size_bytes,
            )

        except TTSValidationError as exc:
            _fail_audio(audio, db, str(exc))
        except TTSConfigError as exc:
            _fail_audio(audio, db, str(exc))
        except TTSRateLimitError as exc:
            _fail_audio(audio, db, str(exc))
        except TTSNetworkError as exc:
            _fail_audio(audio, db, str(exc))
        except TTSGenerationError as exc:
            _fail_audio(audio, db, str(exc))
        except Exception as exc:
            logger.exception("Unexpected error in TTS background task audio_id=%s", audio_id)
            _fail_audio(audio, db, f"Unexpected error: {type(exc).__name__}: {str(exc)[:200]}")

    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_audio_or_404(audio_id: str, db: Session) -> GeneratedAudio:
    audio = db.query(GeneratedAudio).filter(GeneratedAudio.id == audio_id).first()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio record not found: {audio_id}",
        )
    return audio


def _fail_audio(audio: GeneratedAudio, db: Session, message: str) -> None:
    """Mark an audio record as failed."""
    try:
        audio.status = AudioStatus.FAILED
        audio.error_message = message[:500] if message else "Unknown error"
        db.commit()
    except Exception:
        pass
