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


def _wrap_caption_lines(
    words: list[str],
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> list[str]:
    """Wrap words into at most max_lines lines without dropping words.

    Returns lines for a single cue, or raises ValueError if words cannot fit.
    """
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        if current and len(trial) > max_chars_per_line:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) >= max_lines:
                raise ValueError("exceeds max_lines")
        else:
            current.append(word)
    if current:
        if len(lines) >= max_lines:
            raise ValueError("exceeds max_lines")
        lines.append(" ".join(current))
    return lines


def _format_caption_display(text: str, max_chars_per_line: int = 42, max_lines: int = 2) -> str:
    """
    Wrap text for a single cue (≤2 lines). Long text must go through
    _split_caption_cues so no words are dropped.
    """
    words = _clean_caption_text(text).split()
    if not words:
        return ""
    try:
        return "\n".join(_wrap_caption_lines(words, max_chars_per_line, max_lines))
    except ValueError:
        # First cue only — full preservation requires _split_caption_cues
        cues = _split_caption_cues(text, 0.0, 1.0, max_chars_per_line, max_lines)
        return cues[0][2] if cues else ""


def _split_caption_cues(
    text: str,
    start: float,
    end: float,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> list[tuple[float, float, str]]:
    """
    P0.6.1: Split long narration into consecutive ≤2-line cues.

    Every word is preserved. Timing is proportional to word count within
    the parent [start, end) window so cues stay synced to narration.
    """
    words = _clean_caption_text(text).split()
    if not words:
        return []

    duration = max(0.05, float(end) - float(start))
    chunks: list[list[str]] = []
    current: list[str] = []
    for word in words:
        trial = current + [word]
        try:
            _wrap_caption_lines(trial, max_chars_per_line, max_lines)
            current = trial
        except ValueError:
            if current:
                chunks.append(current)
            current = [word]
            # Single oversized token: still emit it on its own line
            try:
                _wrap_caption_lines(current, max_chars_per_line, max_lines)
            except ValueError:
                chunks.append(current)
                current = []
    if current:
        chunks.append(current)

    if not chunks:
        return []

    total_words = sum(len(c) for c in chunks)
    cues: list[tuple[float, float, str]] = []
    cursor = float(start)
    for i, chunk in enumerate(chunks):
        share = len(chunk) / total_words
        cue_end = float(end) if i == len(chunks) - 1 else cursor + duration * share
        # Keep a tiny gap-free continuum; clamp monotonicity
        if cue_end <= cursor:
            cue_end = cursor + 0.05
        display = "\n".join(_wrap_caption_lines(chunk, max_chars_per_line, max_lines))
        cues.append((cursor, cue_end, display))
        cursor = cue_end
    return cues


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
    cue_i = 1
    for seg in segments:
        for cue_start, cue_end, text in _split_caption_cues(seg.text, seg.start, seg.end):
            if not text.strip():
                continue
            srt_lines.append(
                f"{cue_i}\n"
                f"{_seconds_to_srt_time(cue_start)} --> {_seconds_to_srt_time(cue_end)}\n"
                f"{text}\n"
            )
            cue_i += 1

    srt_content = "\n".join(srt_lines)
    out.write_text(srt_content, encoding="utf-8")
    logger.info(
        "Captions written: %s (%d whisper segs → %d cues, detected lang=%s)",
        out.name, len(segments), cue_i - 1, info.language,
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

    Splits the text into readable ≤2-line cues and distributes them evenly
    over the audio duration.  Timestamps will not be word-accurate but
    are better than no captions.
    """
    out = Path(output_srt_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    words = narration_text.split()
    if not words:
        out.write_text("", encoding="utf-8")
        return out

    cues = _split_caption_cues(" ".join(words), 0.0, max(0.1, float(audio_duration)))
    srt_lines: list[str] = []
    for i, (start, end, display) in enumerate(cues, start=1):
        srt_lines.append(
            f"{i}\n"
            f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}\n"
            f"{display}\n"
        )

    out.write_text("\n".join(srt_lines), encoding="utf-8")
    logger.info("Fallback captions written: %s (%d cues)", out.name, len(cues))
    return out
