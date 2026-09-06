"""
backend/services/tts/edge_provider.py — Edge-TTS provider.

Uses Microsoft Edge's free online TTS service via the `edge-tts` library.

Characteristics:
  - No API key required.
  - Requires internet access.
  - High-quality neural voices.
  - Supports 100+ languages and voices.
  - Free for personal/educational use.
  - May be subject to Microsoft service limits without SLA guarantees.

Dependencies:
    edge-tts==6.1.12   (pip install edge-tts)

Output:
    MP3 files at the configured TTS_OUTPUT_DIR.
    Directory is created automatically if it does not exist.

Configuration (environment variables):
    TTS_DEFAULT_VOICE     — default voice (default: en-US-AriaNeural)
    TTS_OUTPUT_DIR        — output directory (default: G:\\youtube-uploader\\data\\audio)
    TTS_MAX_TEXT_LENGTH   — character limit before chunking (default: 5000)
    TTS_TIMEOUT_SECONDS   — per-request timeout (default: 60)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

from backend.services.tts.base import (
    TTSConfigError,
    TTSException,
    TTSGenerationError,
    TTSNetworkError,
    TTSProvider,
    TTSRateLimitError,
    TTSResult,
    TTSValidationError,
    TTSVoice,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_VOICE       = "en-US-AriaNeural"
_DEFAULT_OUTPUT_DIR  = r"G:\youtube-uploader\data\audio"
_DEFAULT_MAX_TEXT    = 5000   # chars — Edge-TTS handles long text but we chunk for safety
_DEFAULT_TIMEOUT     = 60     # seconds per synthesis call
_MAX_RETRIES         = 3
_RETRY_BASE_DELAY    = 2.0    # seconds


class EdgeTTSProvider(TTSProvider):
    """
    TTS provider backed by Microsoft Edge's free neural TTS service.

    Requires: pip install edge-tts==6.1.12
    Requires: active internet connection
    No API key needed.
    """

    def __init__(self) -> None:
        self._default_voice  = os.getenv("TTS_DEFAULT_VOICE", _DEFAULT_VOICE)
        self._output_dir     = Path(os.getenv("TTS_OUTPUT_DIR", _DEFAULT_OUTPUT_DIR))
        self._max_text_len   = int(os.getenv("TTS_MAX_TEXT_LENGTH", _DEFAULT_MAX_TEXT))
        self._timeout        = float(os.getenv("TTS_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT))
        # Create output directory if it doesn't exist (never on C:)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "EdgeTTSProvider initialized: voice=%s output_dir=%s",
            self._default_voice, self._output_dir,
        )

    @property
    def provider_name(self) -> str:
        return "edge"

    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        """
        Synthesize text to speech using Edge-TTS and save as MP3.

        For long text, splits into chunks at sentence boundaries and
        concatenates without FFmpeg (appends bytes directly for MP3).
        """
        _validate_text(text, self._max_text_len)

        # Ensure output path is absolute and within output_dir (security)
        out = Path(output_path)
        if not out.is_absolute():
            out = self._output_dir / out.name
        _check_path_safety(out, self._output_dir)

        # Ensure parent directory exists
        out.parent.mkdir(parents=True, exist_ok=True)

        cleaned = _clean_text_for_tts(text)
        chunks  = _split_into_chunks(cleaned, self._max_text_len)

        logger.info(
            "TTS synthesis started: voice=%s chunks=%d total_chars=%d output=%s",
            voice, len(chunks), len(cleaned), out,
        )

        if len(chunks) == 1:
            await self._synthesize_chunk(chunks[0], voice, str(out))
        else:
            # Generate each chunk to a temp file, then concatenate
            chunk_paths: list[Path] = []
            try:
                for i, chunk in enumerate(chunks):
                    chunk_path = out.with_suffix(f".chunk{i}.mp3")
                    await self._synthesize_chunk(chunk, voice, str(chunk_path))
                    chunk_paths.append(chunk_path)
                _concatenate_mp3(chunk_paths, out)
            finally:
                for cp in chunk_paths:
                    try:
                        cp.unlink(missing_ok=True)
                    except OSError:
                        pass

        file_size = out.stat().st_size if out.exists() else 0

        result = TTSResult(
            file_path=str(out),
            voice=voice,
            format="mp3",
            file_size_bytes=file_size,
            duration_seconds=_estimate_duration(cleaned),
            text_length=len(text),
        )

        logger.info(
            "TTS synthesis completed: %s size=%d bytes duration~%.1fs",
            out.name, result.file_size_bytes, result.duration_seconds or 0,
        )
        return result

    async def get_voices(self, language: Optional[str] = None) -> list[TTSVoice]:
        """Return available Edge-TTS voices, optionally filtered by language code."""
        try:
            import edge_tts
            voices_raw = await asyncio.wait_for(
                edge_tts.list_voices(), timeout=self._timeout
            )
        except ImportError as exc:
            raise TTSConfigError("edge-tts is not installed. Run: pip install edge-tts==6.1.12") from exc
        except asyncio.TimeoutError as exc:
            raise TTSNetworkError("Timed out fetching Edge-TTS voice list.") from exc
        except Exception as exc:
            raise TTSNetworkError(f"Failed to fetch Edge-TTS voices: {type(exc).__name__}") from exc

        voices: list[TTSVoice] = []
        for v in voices_raw:
            locale = v.get("Locale", "")
            lang_code = locale.split("-")[0].lower() if locale else ""
            if language and lang_code != language.lower()[:2]:
                continue
            voices.append(TTSVoice(
                name=v.get("ShortName", ""),
                locale=locale,
                language=v.get("FriendlyName", "").replace("Microsoft ", "").split(" Online")[0],
                gender=v.get("Gender", "Unknown"),
            ))
        return voices

    # ── Internal ───────────────────────────────────────────────────────────

    async def _synthesize_chunk(self, text: str, voice: str, output_path: str) -> None:
        """Synthesize a single text chunk with retry on transient errors."""
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSConfigError("edge-tts is not installed. Run: pip install edge-tts==6.1.12") from exc

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                communicate = edge_tts.Communicate(text, voice)
                await asyncio.wait_for(
                    communicate.save(output_path),
                    timeout=self._timeout,
                )
                return  # success
            except asyncio.TimeoutError as exc:
                last_exc = exc
                logger.warning("TTS attempt %d/%d timed out", attempt, _MAX_RETRIES)
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                exc_name = type(exc).__name__

                # Non-retryable: invalid voice
                if "voice" in exc_str and ("invalid" in exc_str or "not found" in exc_str):
                    raise TTSValidationError(
                        f"Invalid or unsupported voice: '{voice}'. "
                        "Use GET /api/tts/voices to list available voices."
                    ) from exc

                # Non-retryable: bad request
                if "400" in exc_str or "bad request" in exc_str:
                    raise TTSGenerationError(
                        f"Edge-TTS rejected the synthesis request: {exc_str[:200]}"
                    ) from exc

                logger.warning(
                    "TTS attempt %d/%d failed (%s): %s",
                    attempt, _MAX_RETRIES, exc_name, exc_str[:100],
                )

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))

        # All retries exhausted — classify the final error
        exc_str = str(last_exc).lower() if last_exc else ""
        # 403 from WSServerHandshakeError = Microsoft IP rate-limit / temp block (transient)
        if "403" in exc_str or "invalid response status" in exc_str:
            raise TTSNetworkError(
                "Edge-TTS returned 403 (service temporarily unavailable or rate-limited by IP). "
                "Wait a few minutes and try again."
            ) from last_exc
        if any(k in exc_str for k in ("timeout", "connect", "network", "refused", "ssl")):
            raise TTSNetworkError(
                f"Network error contacting Edge-TTS after {_MAX_RETRIES} attempts: "
                f"{str(last_exc)[:200]}"
            ) from last_exc
        if "429" in exc_str or "rate" in exc_str or "too many" in exc_str:
            raise TTSRateLimitError(
                "Edge-TTS rate limit exceeded. Wait a moment and try again."
            ) from last_exc
        raise TTSGenerationError(
            f"Edge-TTS synthesis failed after {_MAX_RETRIES} attempts: "
            f"{str(last_exc)[:200]}"
        ) from last_exc


# ── Module-level helpers ───────────────────────────────────────────────────────

def _validate_text(text: str, max_len: int) -> None:
    """Raise TTSValidationError for invalid input."""
    if not text or not text.strip():
        raise TTSValidationError("Text cannot be empty.")
    if len(text) > max_len * 4:   # absolute hard limit — 4× chunk size
        raise TTSValidationError(
            f"Text is too long ({len(text)} chars). "
            f"Maximum allowed: {max_len * 4} chars."
        )


def _clean_text_for_tts(text: str) -> str:
    """
    Clean text for TTS synthesis.

    Removes artifacts that should not be spoken while preserving
    natural punctuation that affects speech rhythm and intonation.
    """
    # Remove markdown bold/italic markers
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", text)
    # Remove markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove URLs (they sound terrible in TTS)
    text = re.sub(r"https?://\S+", "", text)
    # Remove markdown code fences
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    # Normalize multiple newlines to single (pause-friendly)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalize multiple spaces
    text = re.sub(r"  +", " ", text)
    # Strip leading/trailing whitespace
    return text.strip()


def _split_into_chunks(text: str, max_chunk_len: int) -> list[str]:
    """
    Split text into chunks at sentence/paragraph boundaries.

    Never cuts mid-sentence. Tries paragraphs first, then sentences.
    Returns at least one chunk even if the text is longer than max_chunk_len.
    """
    if len(text) <= max_chunk_len:
        return [text]

    chunks: list[str] = []
    # Split on paragraph boundaries first
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]

    current = ""
    for para in paragraphs:
        # If this paragraph alone is too long, split it at sentences
        if len(para) > max_chunk_len:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if len(current) + len(sentence) + 1 <= max_chunk_len:
                    current = (current + " " + sentence).strip() if current else sentence
                else:
                    if current:
                        chunks.append(current)
                    # If a single sentence exceeds max, include it as-is
                    current = sentence
        elif len(current) + len(para) + 2 <= max_chunk_len:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    return chunks if chunks else [text]


def _concatenate_mp3(chunk_paths: list[Path], output_path: Path) -> None:
    """
    Concatenate MP3 chunk files by appending bytes.

    This is a simple byte-level concatenation. For MP3 files, this
    produces a valid playable file in most players. If FFmpeg is available
    in a later phase, this can be replaced with a proper re-encode.
    """
    with open(output_path, "wb") as out_f:
        for cp in chunk_paths:
            with open(cp, "rb") as in_f:
                out_f.write(in_f.read())


def _estimate_duration(text: str) -> float:
    """
    Estimate spoken duration from word count.

    Average spoken English rate: ~150 words/minute.
    Returns duration in seconds.
    """
    word_count = len(text.split())
    return round(word_count / 150 * 60, 1)


def _check_path_safety(path: Path, allowed_dir: Path) -> None:
    """
    Prevent path traversal — ensure the output path stays within allowed_dir.
    """
    try:
        path.resolve().relative_to(allowed_dir.resolve())
    except ValueError as exc:
        raise TTSValidationError(
            f"Output path '{path}' is outside the allowed TTS output directory."
        ) from exc
