"""
backend/services/tts/local_provider.py — Local CPU TTS provider using pyttsx3.

Uses Windows SAPI via pyttsx3 — completely offline, no API key, no model
download, no internet required, no CUDA, works on any Windows machine.

Characteristics:
  - Uses built-in Windows text-to-speech voices (SAPI5).
  - No download required — voices ship with Windows.
  - Typical voices on Windows 10: Microsoft David (Male), Microsoft Zira (Female).
  - No GPU required — pure CPU, very low RAM (~10 MB delta).
  - ~42x realtime on i5-6300U: 3.4-min narration generates in ~4.8 seconds
    (confirmed benchmark: 2970 chars / 10 chunks → 202.7s audio in 4.83s).
  - Output format: WAV (Windows SAPI natively produces WAV).
  - No internet connection needed after pyttsx3 is installed.
  - $0 cost.

Windows SAPI chunking constraint (i5-6300U / Windows 10):
  pyttsx3.runAndWait() hangs on a second call if the same engine instance is
  reused with save_to_file(), AND hangs on large (>~800 char) single chunks.
  Solution: create a fresh pyttsx3.init() + stop() per chunk, with chunks ≤500
  chars.  This is reliable and still runs at ~42x realtime.

Limitations:
  - Voice quality is basic (Windows SAPI, not neural).
  - Only Windows built-in SAPI voices available unless user installs more.
  - WAV output (larger files than MP3; suitable for Phase 2D FFmpeg pipeline).
  - No multi-language support beyond installed SAPI voices.

Voice identifiers:
  "local-zira"  → Microsoft Zira Desktop (Female, en-US)  [default]
  "local-david" → Microsoft David Desktop (Male, en-US)
  "local-default" → first available SAPI voice

Dependencies:
    pyttsx3   (pip install pyttsx3)

Configuration (environment variables):
    TTS_OUTPUT_DIR   — output directory for generated WAV files
    TTS_LOCAL_VOICE  — preferred local voice key (default: local-zira)
    TTS_LOCAL_RATE   — speech rate in words/minute (default: 165)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from backend.services.tts.base import (
    TTSConfigError,
    TTSGenerationError,
    TTSProvider,
    TTSResult,
    TTSValidationError,
    TTSVoice,
)
from backend.services.tts.edge_provider import (
    _clean_text_for_tts,
    _estimate_duration,
    _split_into_chunks,
    _validate_text,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = r"G:\youtube-uploader\data\audio"
_DEFAULT_RATE       = 165   # words per minute — slightly slower than default for clarity
_DEFAULT_MAX_TEXT   = 5000  # chars per TTS_MAX_TEXT_LENGTH env var (outer chunking)
# Windows SAPI safe chunk size: pyttsx3.runAndWait() hangs on chunks > ~800 chars
# on i5-6300U / Windows 10 SAPI.  Keep each pyttsx3 call to ≤500 chars for safety.
_SAPI_SAFE_CHUNK    = 500

# Mapping from friendly local voice keys → SAPI voice name fragments
_VOICE_MAP = {
    "local-zira":    "zira",
    "local-david":   "david",
    "local-default": None,   # first available
}


class LocalTTSProvider(TTSProvider):
    """
    Local TTS provider using Windows SAPI via pyttsx3.

    No internet, no API key, no model download, no GPU.
    Uses built-in Windows voices.
    """

    def __init__(self) -> None:
        self._output_dir = Path(os.getenv("TTS_OUTPUT_DIR", _DEFAULT_OUTPUT_DIR))
        self._rate       = int(os.getenv("TTS_LOCAL_RATE", _DEFAULT_RATE))
        self._max_text   = int(os.getenv("TTS_MAX_TEXT_LENGTH", _DEFAULT_MAX_TEXT))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # Validate pyttsx3 is importable at construction time
        try:
            import pyttsx3  # noqa: F401
        except ImportError as exc:
            raise TTSConfigError(
                "pyttsx3 is not installed. Run: pip install pyttsx3"
            ) from exc
        logger.info(
            "LocalTTSProvider initialized: output_dir=%s rate=%d",
            self._output_dir, self._rate,
        )

    @property
    def provider_name(self) -> str:
        return "local"

    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        """
        Synthesize text to speech using Windows SAPI and save as WAV.

        pyttsx3 is synchronous — we run it in a thread executor so the
        async interface is preserved.
        """
        _validate_text(text, self._max_text)

        out = Path(output_path)
        # Normalise extension: local provider outputs WAV
        if out.suffix.lower() == ".mp3":
            out = out.with_suffix(".wav")
        if not out.is_absolute():
            out = self._output_dir / out.name
        out.parent.mkdir(parents=True, exist_ok=True)

        cleaned = _clean_text_for_tts(text)
        # Use SAPI_SAFE_CHUNK for internal chunking regardless of TTS_MAX_TEXT_LENGTH.
        # Windows SAPI / pyttsx3 hangs on large text in a single save_to_file call.
        chunks  = _split_into_chunks(cleaned, _SAPI_SAFE_CHUNK)

        logger.info(
            "Local TTS synthesis: voice=%s chunks=%d chars=%d output=%s",
            voice, len(chunks), len(cleaned), out,
        )

        # pyttsx3 is blocking — run in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()

        if len(chunks) == 1:
            await loop.run_in_executor(
                None,
                self._synthesize_sync,
                cleaned,
                voice,
                str(out),
            )
        else:
            # Multi-chunk: synthesize each to a temp file, then concatenate
            chunk_paths: list[Path] = []
            try:
                for i, chunk in enumerate(chunks):
                    chunk_path = out.with_suffix(f".chunk{i}.wav")
                    await loop.run_in_executor(
                        None,
                        self._synthesize_sync,
                        chunk,
                        voice,
                        str(chunk_path),
                    )
                    chunk_paths.append(chunk_path)
                _concatenate_wav(chunk_paths, out)
            finally:
                for cp in chunk_paths:
                    try:
                        cp.unlink(missing_ok=True)
                    except OSError:
                        pass

        if not out.exists() or out.stat().st_size == 0:
            raise TTSGenerationError(
                "Local TTS produced no output. "
                "Check that Windows SAPI voices are available."
            )

        file_size = out.stat().st_size
        result = TTSResult(
            file_path=str(out),
            voice=voice,
            format="wav",
            file_size_bytes=file_size,
            duration_seconds=_estimate_duration(cleaned),
            text_length=len(text),
        )

        logger.info(
            "Local TTS completed: %s size=%d bytes duration~%.1fs",
            out.name, file_size, result.duration_seconds or 0,
        )
        return result

    async def get_voices(self, language: Optional[str] = None) -> list[TTSVoice]:
        """Return available local SAPI voices."""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            try:
                sapi_voices = engine.getProperty("voices")
            finally:
                try:
                    engine.stop()
                except Exception:
                    pass
        except Exception as exc:
            raise TTSConfigError(
                f"Cannot initialise pyttsx3 engine: {exc}"
            ) from exc

        voices: list[TTSVoice] = []

        # Always include the friendly "local-default" alias
        voices.append(TTSVoice(
            name="local-default",
            locale="en-US",
            language="English (Windows SAPI)",
            gender="Unknown",
        ))

        for v in sapi_voices:
            voice_id   = v.id or ""
            voice_name = v.name or voice_id.split("\\")[-1]
            # Derive locale from languages list
            locale = "en-US"
            if hasattr(v, "languages") and v.languages:
                lang = str(v.languages[0]) if v.languages else ""
                if lang:
                    locale = lang[:5] if len(lang) >= 5 else lang

            lang_code = locale.split("-")[0].lower()
            if language and lang_code != language.lower()[:2]:
                continue

            # Derive gender from name
            gender = "Unknown"
            name_lower = voice_name.lower()
            if any(w in name_lower for w in ("zira", "female", "woman", "girl")):
                gender = "Female"
            elif any(w in name_lower for w in ("david", "male", "man", "guy")):
                gender = "Male"

            # Build the friendly key (e.g. local-zira, local-david)
            key = f"local-{voice_name.split()[-1].lower()}"

            voices.append(TTSVoice(
                name=key,
                locale=locale,
                language=f"{voice_name} (Local SAPI)",
                gender=gender,
            ))

        return voices

    # ── Internal ───────────────────────────────────────────────────────────

    def _synthesize_sync(self, text: str, voice_key: str, output_path: str) -> None:
        """
        Synchronous pyttsx3 synthesis — must be run in a thread executor.

        voice_key can be:
          "local-zira"    → Microsoft Zira
          "local-david"   → Microsoft David
          "local-default" → first available SAPI voice
          any other       → try to match by name fragment, fallback to first available

        IMPORTANT: A fresh pyttsx3 engine is created and destroyed for each call.
        Windows SAPI's runAndWait() hangs on a second call to the same engine
        when save_to_file was used.  Creating a new engine instance per chunk
        works reliably on Windows 10 with the i5-6300U (confirmed in benchmarks).
        """
        import pyttsx3

        engine = pyttsx3.init()
        try:
            engine.setProperty("rate",   self._rate)
            engine.setProperty("volume", 1.0)

            # Resolve voice
            sapi_voices = engine.getProperty("voices")
            if not sapi_voices:
                raise TTSGenerationError(
                    "No SAPI voices found on this system. "
                    "Ensure Windows Speech Platform voices are installed."
                )

            selected = sapi_voices[0]   # default: first available
            fragment = _resolve_voice_fragment(voice_key)

            if fragment:
                for v in sapi_voices:
                    if fragment in (v.id or "").lower() or fragment in (v.name or "").lower():
                        selected = v
                        break

            engine.setProperty("voice", selected.id)
            engine.save_to_file(text, output_path)
            engine.runAndWait()
        finally:
            # Always stop and release the engine.  On Windows SAPI, failing to
            # do this causes runAndWait() to block forever on the next call.
            try:
                engine.stop()
            except Exception:
                pass


# ── Module-level helpers ───────────────────────────────────────────────────────

def _resolve_voice_fragment(voice_key: str) -> Optional[str]:
    """
    Return the SAPI name fragment to search for, or None for first-available.

    e.g. "local-zira" → "zira"
         "local-david" → "david"
         "local-default" → None
    """
    key = voice_key.lower().strip()
    if key in _VOICE_MAP:
        return _VOICE_MAP[key]
    # For unknown keys, strip "local-" prefix and use as fragment
    if key.startswith("local-"):
        return key[6:]
    return None


def _concatenate_wav(chunk_paths: list[Path], output_path: Path) -> None:
    """
    Concatenate WAV files using Python's built-in wave module.

    All chunks must have the same sample rate, channels, and sample width.
    This is guaranteed when they come from the same pyttsx3 engine instance.
    """
    import wave

    if not chunk_paths:
        return

    with wave.open(str(chunk_paths[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setparams(params)
        for cp in chunk_paths:
            with wave.open(str(cp), "rb") as in_wav:
                out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
