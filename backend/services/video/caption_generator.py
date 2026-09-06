"""
backend/services/video/caption_generator.py — SRT caption generation.

Uses faster-whisper (tiny model, CPU-only) to transcribe narration audio
and produce a valid SRT subtitle file.

Design:
  - Model is loaded once and cached in memory.
  - Model files are stored on G: (never C:).
  - Falls back gracefully if faster-whisper is unavailable.
  - The abstraction allows swapping to a different transcription provider later.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cached model instance — loaded on first use
_whisper_model = None
_whisper_model_size: Optional[str] = None


def _get_model(model_size: str = "tiny", models_dir: Optional[str] = None):
    """Return a cached WhisperModel, loading it on first call."""
    global _whisper_model, _whisper_model_size

    if _whisper_model is not None and _whisper_model_size == model_size:
        return _whisper_model

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ImportError(
            "faster-whisper is not installed. "
            "Run: pip install faster-whisper==1.1.1"
        ) from exc

    from backend.services.video.media_utils import get_whisper_models_dir
    cache_dir = models_dir or str(get_whisper_models_dir())

    logger.info(
        "Loading Whisper model '%s' (CPU). Cache: %s — first load may take a moment.",
        model_size, cache_dir,
    )
    _whisper_model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",          # lightest option, good on i5
        download_root=cache_dir,
    )
    _whisper_model_size = model_size
    logger.info("Whisper model '%s' loaded.", model_size)
    return _whisper_model


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert float seconds to SRT timestamp: HH:MM:SS,mmm"""
    s = max(0.0, seconds)
    hours   = int(s // 3600)
    minutes = int((s % 3600) // 60)
    secs    = int(s % 60)
    millis  = int(round((s - int(s)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _clean_caption_text(text: str) -> str:
    """Light cleanup of Whisper transcript text."""
    text = text.strip()
    # Remove any leading/trailing whitespace or newlines within a segment
    text = re.sub(r"\s+", " ", text)
    return text


def generate_captions(
    audio_path: str | Path,
    output_srt_path: str | Path,
    model_size: str = "tiny",
    language: str = "en",
) -> Path:
    """
    Transcribe audio and write an SRT subtitle file.

    Args:
        audio_path:      Path to the narration audio (WAV or MP3).
        output_srt_path: Where to write the .srt file.
        model_size:      Whisper model size ('tiny' recommended for CPU).
        language:        Language code (default 'en').

    Returns:
        Path to the written SRT file.

    Raises:
        CaptionGenerationError: If transcription fails.
    """
    from backend.services.video.exceptions import CaptionGenerationError

    audio_path = Path(audio_path)
    out = Path(output_srt_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        raise CaptionGenerationError(
            f"Audio file not found for caption generation: {audio_path}"
        )

    logger.info("Generating captions from: %s", audio_path.name)

    try:
        model = _get_model(model_size)
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=1,          # fastest
            vad_filter=True,      # skip silence
            word_timestamps=False,
        )
        segments = list(segments)
    except Exception as exc:
        raise CaptionGenerationError(
            f"Whisper transcription failed: {type(exc).__name__}: {str(exc)[:200]}"
        ) from exc

    if not segments:
        logger.warning("Whisper produced no segments — writing empty SRT")
        out.write_text("", encoding="utf-8")
        return out

    srt_lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        text = _clean_caption_text(seg.text)
        if not text:
            continue
        start = _seconds_to_srt_time(seg.start)
        end   = _seconds_to_srt_time(seg.end)
        srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")

    srt_content = "\n".join(srt_lines)
    out.write_text(srt_content, encoding="utf-8")
    logger.info(
        "Captions written: %s (%d segments, detected lang=%s)",
        out.name, len(segments), info.language,
    )
    return out


def generate_fallback_captions(
    narration_text: str,
    audio_duration: float,
    output_srt_path: str | Path,
) -> Path:
    """
    Generate approximate SRT captions from narration text when Whisper
    is unavailable or fails.

    Splits the text into ~8-second segments and distributes them evenly
    over the audio duration.  Timestamps will not be word-accurate but
    are better than no captions.
    """
    out = Path(output_srt_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    import textwrap
    words = narration_text.split()
    if not words:
        out.write_text("", encoding="utf-8")
        return out

    # Aim for ~8 words per caption segment
    chunk_size = 8
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
    total_chunks = len(chunks)
    seg_duration = audio_duration / max(total_chunks, 1)

    srt_lines: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        start = (i - 1) * seg_duration
        end   = i * seg_duration
        srt_lines.append(
            f"{i}\n"
            f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}\n"
            f"{chunk}\n"
        )

    out.write_text("\n".join(srt_lines), encoding="utf-8")
    logger.info("Fallback captions written: %s (%d segments)", out.name, total_chunks)
    return out
