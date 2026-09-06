"""
backend/services/video/pipeline.py — Phase 2D video generation pipeline.

Orchestrates the full flow:
  1. Validate project + script.
  2. Ensure narration audio exists (generate if needed).
  3. Build scene cards (Pillow).
  4. Calculate scene durations.
  5. Build per-scene video clips with Ken Burns effect.
  6. Concatenate clips.
  7. Mix narration + optional music.
  8. Generate captions (faster-whisper tiny).
  9. Burn captions into video.
  10. Final encode (H.264/AAC/1080p30/faststart).
  11. Generate thumbnail.
  12. Verify output with ffprobe.
  13. Update job record throughout.

Progress milestones:
  0   queued
  5   preparing
  10  audio ready
  15  visuals building
  30  visuals done
  35  captions starting
  45  captions done
  50  assembly starting
  55  clips built
  70  clips concatenated
  80  audio mixed
  87  captions burned
  90  final encode done
  92  thumbnail done
  95  verification done
  100 completed
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Single-threaded executor so CPU-intensive work doesn't spawn unbounded threads
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video_pipeline")


# ── Public entry point ────────────────────────────────────────────────────────

async def run_pipeline(
    job_id: str,
    content_project_id: str,
    audio_id: Optional[str],
    width: int,
    height: int,
    fps: int,
    captions_enabled: bool,
    music_enabled: bool,
    progress_callback: Callable[[int, str], None],
) -> dict:
    """
    Run the full video generation pipeline in a thread executor.

    Returns a dict with output paths and probe results.
    The caller (router background task) is responsible for updating the DB.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        _run_pipeline_sync,
        job_id,
        content_project_id,
        audio_id,
        width,
        height,
        fps,
        captions_enabled,
        music_enabled,
        progress_callback,
    )


# ── Synchronous pipeline (runs in thread) ────────────────────────────────────

def _run_pipeline_sync(
    job_id: str,
    content_project_id: str,
    audio_id: Optional[str],
    width: int,
    height: int,
    fps: int,
    captions_enabled: bool,
    music_enabled: bool,
    progress_callback: Callable[[int, str], None],
) -> dict:
    from backend.services.video.exceptions import (
        PipelineConfigError, FFmpegError, VideoGenerationError,
    )
    from backend.services.video.media_utils import (
        get_temp_dir, output_video_path, output_thumbnail_path,
        output_caption_path, cleanup_temp, find_music_file,
    )
    from backend.services.video.visual_builder import generate_scene_card, generate_title_card
    from backend.services.video.thumbnail import generate_thumbnail
    from backend.services.video.caption_generator import (
        generate_captions, generate_fallback_captions,
    )
    from backend.services.video.ffmpeg_assembler import (
        calculate_scene_durations, SceneClip,
        build_scene_clip, concatenate_clips,
        mix_audio, burn_captions, final_encode, probe_video,
        _get_duration,
    )
    from backend.services.media.ffmpeg import check_ffmpeg, FFmpegNotFoundError

    t_start = time.perf_counter()
    temp_dir = get_temp_dir(job_id)
    step = progress_callback

    # ── 1. Verify FFmpeg ───────────────────────────────────────────────────
    step(5, "Verifying FFmpeg...")
    ffmpeg_status = check_ffmpeg()
    if not ffmpeg_status["available"]:
        raise PipelineConfigError(
            "FFmpeg is not available. Check FFMPEG_PATH configuration."
        )
    logger.info("FFmpeg OK: %s", ffmpeg_status.get("version", "")[:60])

    # ── 2. Load project + script ───────────────────────────────────────────
    step(5, "Loading project...")
    from backend.db import SessionLocal
    from backend.content_models import ContentProject, GeneratedScriptRecord
    from backend.tts_models import GeneratedAudio, AudioStatus

    db = SessionLocal()
    try:
        project = db.query(ContentProject).filter(
            ContentProject.id == content_project_id
        ).first()
        if not project:
            raise PipelineConfigError(
                f"Content project not found: {content_project_id}"
            )
        if not project.script:
            raise PipelineConfigError(
                "Content project has no generated script. Generate a script first."
            )

        script_rec = project.script
        scenes = script_rec.scenes_as_list()
        if not scenes:
            raise PipelineConfigError("Script has no scenes.")

        title      = script_rec.title
        hook       = script_rec.hook or ""
        language   = project.language or "en"

        # ── 3. Ensure audio ───────────────────────────────────────────────
        step(8, "Locating narration audio...")
        audio_record = None
        if audio_id:
            audio_record = db.query(GeneratedAudio).filter(
                GeneratedAudio.id == audio_id,
                GeneratedAudio.status == AudioStatus.COMPLETED,
            ).first()

        if not audio_record:
            # Find any completed audio for this project
            audio_record = (
                db.query(GeneratedAudio)
                .filter(
                    GeneratedAudio.content_project_id == content_project_id,
                    GeneratedAudio.status == AudioStatus.COMPLETED,
                )
                .order_by(GeneratedAudio.created_at.desc())
                .first()
            )

        if not audio_record:
            # Generate narration inline
            step(10, "Generating narration...")
            audio_record = _generate_narration_inline(
                db, project, script_rec, job_id, temp_dir
            )

        if not audio_record or not audio_record.file_path:
            raise PipelineConfigError(
                "No completed narration audio found and inline generation failed."
            )

        narration_path = Path(audio_record.file_path)
        if not narration_path.exists():
            raise PipelineConfigError(
                f"Narration audio file not found on disk: {narration_path}"
            )

        # Get audio duration
        audio_duration = _get_duration(str(narration_path))
        if audio_duration <= 0:
            # Estimate from text
            from backend.services.tts.narration import extract_narration_text, estimate_narration_duration
            try:
                text = extract_narration_text(script_rec)
                audio_duration = estimate_narration_duration(text)
            except Exception:
                audio_duration = script_rec.estimated_duration_seconds or 180.0

        logger.info("Audio duration: %.1fs", audio_duration)

    finally:
        db.close()

    step(10, "Narration ready.")

    # ── 4. Build scene images ──────────────────────────────────────────────
    step(15, "Building scene visuals...")
    scene_image_paths: list[Path] = []

    # Title card (scene 0)
    title_card_path = temp_dir / "scene_000_title.png"
    try:
        generate_title_card(
            title=title,
            hook=hook,
            output_path=title_card_path,
            width=width,
            height=height,
            topic_seed=title,
        )
        scene_image_paths.append(title_card_path)
    except Exception as exc:
        logger.warning("Title card generation failed: %s", exc)

    for i, scene in enumerate(scenes):
        card_path = temp_dir / f"scene_{i + 1:03d}.png"
        try:
            generate_scene_card(
                scene_number=scene.get("scene_number", i + 1),
                title=title,
                narration=scene.get("narration", ""),
                visual_description=scene.get("visual_description", ""),
                output_path=card_path,
                width=width,
                height=height,
                topic_seed=title,
            )
            scene_image_paths.append(card_path)
        except Exception as exc:
            logger.error("Scene card %d failed: %s", i + 1, exc)
            raise VideoGenerationError(f"Failed to generate scene card {i + 1}: {exc}") from exc

    step(30, f"Generated {len(scene_image_paths)} scene visuals.")

    # ── 5. Calculate scene durations ──────────────────────────────────────
    # Build duration list: one entry per image (title card + scenes)
    all_scene_data = [{"narration": hook or title}]  # title card
    all_scene_data.extend(scenes)
    durations = calculate_scene_durations(all_scene_data, audio_duration)

    # Pad/trim durations list to match image count
    while len(durations) < len(scene_image_paths):
        durations.append(audio_duration / max(len(scene_image_paths), 1))
    durations = durations[:len(scene_image_paths)]

    # ── 6. Build per-scene video clips ────────────────────────────────────
    step(35, "Building scene clips...")
    clip_paths: list[Path] = []
    n_clips = len(scene_image_paths)

    for i, (img_path, dur) in enumerate(zip(scene_image_paths, durations)):
        clip_path = temp_dir / f"clip_{i:03d}.mp4"
        sc = SceneClip(
            scene_number=i + 1,
            image_path=img_path,
            duration_seconds=dur,
        )
        build_scene_clip(sc, clip_path, width, height)
        clip_paths.append(clip_path)
        # Update progress between 35 and 55
        pct = 35 + int((i + 1) / n_clips * 20)
        step(pct, f"Built clip {i + 1}/{n_clips}...")

    step(55, "All scene clips built.")

    # ── 7. Concatenate clips ───────────────────────────────────────────────
    step(58, "Concatenating scene clips...")
    concat_path = temp_dir / "concat.mp4"
    concatenate_clips(clip_paths, concat_path, temp_dir)
    step(65, "Clips concatenated.")

    # ── 8. Mix audio ──────────────────────────────────────────────────────
    step(68, "Mixing audio...")
    music_path = find_music_file() if music_enabled else None
    if music_path:
        logger.info("Background music: %s", music_path.name)
    else:
        logger.info("No background music found — proceeding without music.")

    audio_mixed_path = temp_dir / "with_audio.mp4"
    mix_audio(concat_path, narration_path, music_path, audio_mixed_path)
    step(78, "Audio mixed.")

    # ── 9. Generate captions ──────────────────────────────────────────────
    caption_srt_path = output_caption_path(job_id)
    if captions_enabled:
        step(80, "Generating captions (Whisper tiny)...")
        try:
            generate_captions(
                audio_path=narration_path,
                output_srt_path=caption_srt_path,
                language=language[:2],
            )
            step(85, "Captions generated.")
        except Exception as exc:
            logger.warning(
                "Whisper transcription failed (%s) — using fallback captions.", exc
            )
            try:
                from backend.services.tts.narration import extract_narration_text
                db2 = SessionLocal()
                try:
                    p2 = db2.query(ContentProject).filter(
                        ContentProject.id == content_project_id
                    ).first()
                    narration_text = extract_narration_text(p2.script) if p2 else ""
                finally:
                    db2.close()
                generate_fallback_captions(narration_text, audio_duration, caption_srt_path)
                step(85, "Fallback captions generated.")
            except Exception as exc2:
                logger.warning("Fallback caption generation also failed: %s", exc2)
                step(85, "Captions skipped.")
    else:
        step(85, "Captions disabled.")

    # ── 10. Burn captions ──────────────────────────────────────────────────
    step(86, "Burning captions into video...")
    captioned_path = temp_dir / "captioned.mp4"
    if captions_enabled and caption_srt_path.exists() and caption_srt_path.stat().st_size > 0:
        try:
            burn_captions(audio_mixed_path, caption_srt_path, captioned_path, width, height)
            step(88, "Captions burned.")
        except Exception as exc:
            logger.warning("Caption burn failed (%s) — using video without captions.", exc)
            import shutil
            shutil.copy2(str(audio_mixed_path), str(captioned_path))
    else:
        import shutil
        shutil.copy2(str(audio_mixed_path), str(captioned_path))
        step(88, "Captions skipped.")

    # ── 11. Final encode ───────────────────────────────────────────────────
    step(89, "Final encode (H.264/AAC)...")
    final_path = output_video_path(job_id)
    final_encode(captioned_path, final_path, width, height, fps)
    step(93, "Video encoded.")

    # ── 12. Generate thumbnail ─────────────────────────────────────────────
    step(94, "Generating thumbnail...")
    thumb_path = output_thumbnail_path(job_id)
    try:
        generate_thumbnail(title=title, hook=hook, output_path=thumb_path)
    except Exception as exc:
        logger.warning("Thumbnail generation failed: %s", exc)
        thumb_path = None

    step(95, "Thumbnail done.")

    # ── 13. Verify output ──────────────────────────────────────────────────
    step(96, "Verifying output...")
    if not final_path.exists() or final_path.stat().st_size == 0:
        raise VideoGenerationError("Final MP4 was not created or is empty.")

    probe = probe_video(str(final_path))
    duration = probe.get("duration", 0.0)
    size_bytes = probe.get("size_bytes", 0)

    logger.info(
        "Video verified: %.1fs, %.1f MB, %d streams",
        duration, size_bytes / 1024 / 1024, len(probe.get("streams", [])),
    )

    # ── 14. Cleanup temp files ─────────────────────────────────────────────
    step(98, "Cleaning up...")
    cleanup_temp(job_id, keep_on_failure=False)

    elapsed = time.perf_counter() - t_start
    logger.info(
        "Pipeline completed in %.1fs: job_id=%s output=%s",
        elapsed, job_id, final_path.name,
    )
    step(100, "Complete!")

    return {
        "output_path": str(final_path),
        "thumbnail_path": str(thumb_path) if thumb_path else None,
        "caption_path": str(caption_srt_path) if caption_srt_path.exists() else None,
        "duration_seconds": duration,
        "file_size_bytes": size_bytes,
        "probe": probe,
        "elapsed_seconds": elapsed,
        "music_used": music_path is not None,
        "captions_generated": caption_srt_path.exists() if caption_srt_path else False,
    }


# ── Inline narration generation ───────────────────────────────────────────────

def _generate_narration_inline(db, project, script_rec, job_id: str, temp_dir: Path):
    """
    Generate narration audio using the existing TTS system when no audio
    record exists for the project.

    Returns a GeneratedAudio ORM record on success, None on failure.
    """
    import asyncio
    import uuid
    from backend.services.tts.factory import get_tts_provider
    from backend.services.tts.narration import extract_narration_text
    from backend.tts_models import GeneratedAudio, AudioStatus
    from backend.services.video.media_utils import get_data_dir

    logger.info("Generating narration inline for job %s", job_id)
    try:
        narration_text = extract_narration_text(script_rec)
        voice = os.getenv("TTS_DEFAULT_VOICE", "en-US-AriaNeural")
        output_dir = get_data_dir() / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)

        audio_id = str(uuid.uuid4())
        output_path = str(output_dir / f"{audio_id}.wav")

        provider = get_tts_provider()

        # Run async TTS in the current thread's event loop
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                provider.synthesize(narration_text, voice, output_path)
            )
        finally:
            loop.close()

        # Determine which provider was actually used
        if hasattr(provider, "last_used_provider"):
            used_provider = provider.last_used_provider
        else:
            used_provider = provider.provider_name

        audio_record = GeneratedAudio(
            id=audio_id,
            content_project_id=project.id,
            voice=voice,
            language=project.language or "en",
            text_length=len(narration_text),
            file_path=result.file_path,
            file_size_bytes=result.file_size_bytes,
            duration_seconds=result.duration_seconds,
            status=AudioStatus.COMPLETED,
            provider=used_provider,
        )
        db.add(audio_record)
        db.commit()
        db.refresh(audio_record)
        logger.info("Inline narration generated: %s", result.file_path)
        return audio_record

    except Exception as exc:
        logger.error("Inline narration generation failed: %s", exc)
        return None
