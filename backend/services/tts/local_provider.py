"""
backend/services/tts/local_provider.py — Local CPU TTS provider using pyttsx3.

Uses Windows SAPI via pyttsx3 — completely offline, no API key, no model
download, no internet required, no CUDA, works on any Windows machine.

Root-cause fix (Phase 3B queue stuck after Edge→Local fallback):
  When the queue worker calls _do_tts() it creates a NEW asyncio event loop
  via asyncio.new_event_loop() and calls loop.run_until_complete(synthesize()).
  Inside synthesize(), asyncio.get_running_loop() correctly returns that new
  loop, and run_in_executor() submits pyttsx3 work to it.  This is safe.

  The previous bug was asyncio.set_event_loop(loop) which corrupted the
  global event loop and caused test hangs.  That is now removed.

Chunking constraint (i5-6300U / Windows 10):
  pyttsx3.runAndWait() hangs on chunks > ~800 chars or on a second call to
  the same engine instance.  Solution: fresh pyttsx3.init() + stop() per
  chunk, ≤500 chars each.  Benchmark: ~42x realtime.

Timeout:
  Each chunk runs in a thread executor with asyncio.wait_for(timeout=N).
  Default 120s per chunk (LOCAL_TTS_CHUNK_TIMEOUT_S env var).
  On timeout, TTSGenerationError is raised; the queue job retries/fails cleanly.

Configuration (environment variables):
    TTS_OUTPUT_DIR          — output directory for generated WAV files
    TTS_LOCAL_VOICE         — preferred local voice key (default: local-zira)
    TTS_LOCAL_RATE          — speech rate in words/minute (default: 165)
    LOCAL_TTS_CHUNK_TIMEOUT_S — seconds before a single chunk is considered hung
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

_DEFAULT_OUTPUT_DIR  = r"G:\youtube-uploader\data\audio"
_DEFAULT_RATE        = 165   # words per minute
_DEFAULT_MAX_TEXT    = 5000  # chars (outer chunking limit)
_SAPI_SAFE_CHUNK     = 500   # max chars per pyttsx3 call
_DEFAULT_CHUNK_TIMEOUT = 120.0  # seconds per chunk before giving up

_VOICE_MAP = {
    "local-zira":    "zira",
    "local-david":   "david",
    "local-default": None,
}


async def _run_chunk_with_timeout(
    loop: asyncio.AbstractEventLoop,
    fn,
    args: tuple,
    timeout_s: float,
) -> None:
    """
    Submit fn(*args) to the thread executor and wait up to timeout_s seconds.

    Raises TTSGenerationError on timeout so callers get a clean error.
    NOTE: asyncio.wait_for cancels the Future but the underlying thread keeps
    running.  The engine.stop() in _synthesize_sync's finally block will
    eventually free the SAPI COM object.
    """
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, fn, *args),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise TTSGenerationError(
            f"Local TTS chunk timed out after {timeout_s:.0f}s. "
            "Windows SAPI may be unresponsive. "
            "Adjust LOCAL_TTS_CHUNK_TIMEOUT_S in backend/.env."
        ) from exc


class LocalTTSProvider(TTSProvider):
    """
    Local TTS provider using Windows SAPI via pyttsx3.
    No internet, no API key, no model download, no GPU.
    """

    def __init__(self) -> None:
        self._output_dir    = Path(os.getenv("TTS_OUTPUT_DIR", _DEFAULT_OUTPUT_DIR))
        self._rate          = int(os.getenv("TTS_LOCAL_RATE", _DEFAULT_RATE))
        self._max_text      = int(os.getenv("TTS_MAX_TEXT_LENGTH", _DEFAULT_MAX_TEXT))
        self._chunk_timeout = float(os.getenv("LOCAL_TTS_CHUNK_TIMEOUT_S", _DEFAULT_CHUNK_TIMEOUT))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        try:
            import pyttsx3  # noqa: F401
        except ImportError as exc:
            raise TTSConfigError(
                "pyttsx3 is not installed. Run: pip install pyttsx3"
            ) from exc
        logger.info(
            "LocalTTSProvider initialized: output_dir=%s rate=%d chunk_timeout=%.0fs",
            self._output_dir, self._rate, self._chunk_timeout,
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

        Each chunk is run via run_in_executor so the async interface is
        preserved.  asyncio.get_running_loop() is used (not get_event_loop())
        to correctly obtain the loop that is actively executing this coroutine.
        """
        logger.info(
            "LocalTTSProvider.synthesize() CALLED: voice=%s text_len=%d output=%s",
            voice, len(text), output_path,
        )
        _validate_text(text, self._max_text)

        out = Path(output_path)
        if out.suffix.lower() == ".mp3":
            out = out.with_suffix(".wav")
        if not out.is_absolute():
            out = self._output_dir / out.name
        out.parent.mkdir(parents=True, exist_ok=True)

        cleaned  = _clean_text_for_tts(text)
        chunks   = _split_into_chunks(cleaned, _SAPI_SAFE_CHUNK)
        n_chunks = len(chunks)

        logger.info(
            "Local TTS synthesis STARTED: voice=%s chunks=%d chars=%d output=%s",
            voice, n_chunks, len(cleaned), out,
        )

        # CRITICAL: use get_running_loop() — this coroutine is always called
        # from within a running event loop (either asyncio.run() in tests or
        # loop.run_until_complete() in the queue worker thread).
        loop = asyncio.get_running_loop()

        if n_chunks == 1:
            logger.info("Local TTS: Single chunk mode (%d chars)", len(cleaned))
            await _run_chunk_with_timeout(
                loop, self._synthesize_sync, (cleaned, voice, str(out)),
                self._chunk_timeout,
            )
            logger.info("Local TTS: Single chunk COMPLETED → %s", out.name)
        else:
            chunk_paths: list[Path] = []
            try:
                for i, chunk in enumerate(chunks):
                    chunk_path = out.with_suffix(f".chunk{i}.wav")
                    logger.info(
                        "Local TTS: Chunk %d/%d STARTED (%d chars) → %s",
                        i + 1, n_chunks, len(chunk), chunk_path.name,
                    )
                    await _run_chunk_with_timeout(
                        loop, self._synthesize_sync,
                        (chunk, voice, str(chunk_path)),
                        self._chunk_timeout,
                    )
                    logger.info("Local TTS: Chunk %d/%d COMPLETED", i + 1, n_chunks)
                    chunk_paths.append(chunk_path)
                logger.info("Local TTS: Assembling %d chunks → %s", n_chunks, out.name)
                _concatenate_wav(chunk_paths, out)
                logger.info("Local TTS: WAV assembly COMPLETED")
            finally:
                logger.debug("Local TTS: Cleaning up %d temporary chunk files", len(chunk_paths))
                for cp in chunk_paths:
                    try:
                        cp.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.warning("Local TTS: Failed to delete chunk file %s: %s", cp, exc)

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
            raise TTSConfigError(f"Cannot initialise pyttsx3 engine: {exc}") from exc

        voices: list[TTSVoice] = [TTSVoice(
            name="local-default",
            locale="en-US",
            language="English (Windows SAPI)",
            gender="Unknown",
        )]

        for v in sapi_voices:
            voice_id   = v.id or ""
            voice_name = v.name or voice_id.split("\\")[-1]
            locale = "en-US"
            if hasattr(v, "languages") and v.languages:
                lang = str(v.languages[0]) if v.languages else ""
                if lang:
                    locale = lang[:5] if len(lang) >= 5 else lang

            lang_code = locale.split("-")[0].lower()
            if language and lang_code != language.lower()[:2]:
                continue

            gender = "Unknown"
            name_lower = voice_name.lower()
            if any(w in name_lower for w in ("zira", "female", "woman", "girl")):
                gender = "Female"
            elif any(w in name_lower for w in ("david", "male", "man", "guy")):
                gender = "Male"

            key = f"local-{voice_name.split()[-1].lower()}"
            voices.append(TTSVoice(
                name=key, locale=locale,
                language=f"{voice_name} (Local SAPI)", gender=gender,
            ))
        return voices

    def _synthesize_sync(self, text: str, voice_key: str, output_path: str) -> None:
        """
        Synchronous pyttsx3 synthesis — always run inside a thread executor.

        A FRESH pyttsx3 engine is created and destroyed for every call.
        Reusing an engine causes runAndWait() to hang on Windows SAPI.
        engine.stop() is called in finally to release the COM object.
        """
        import pyttsx3

        logger.info(
            "Local TTS chunk synthesis STARTED: voice=%s chars=%d path=%s",
            voice_key, len(text), Path(output_path).name,
        )

        engine = pyttsx3.init()
        try:
            logger.debug("Local TTS: Setting rate=%d volume=1.0", self._rate)
            engine.setProperty("rate", self._rate)
            engine.setProperty("volume", 1.0)

            logger.debug("Local TTS: Getting SAPI voices...")
            sapi_voices = engine.getProperty("voices")
            if not sapi_voices:
                raise TTSGenerationError(
                    "No SAPI voices found. Ensure Windows Speech Platform voices are installed."
                )

            selected = sapi_voices[0]
            fragment = _resolve_voice_fragment(voice_key)
            if fragment:
                for v in sapi_voices:
                    if fragment in (v.id or "").lower() or fragment in (v.name or "").lower():
                        selected = v
                        break

            logger.info("Local TTS: Selected SAPI voice='%s'", selected.name)
            engine.setProperty("voice", selected.id)

            logger.info("Local TTS: Calling save_to_file(%s chars)...", len(text))
            engine.save_to_file(text, output_path)
            logger.info("Local TTS: save_to_file() completed, calling runAndWait()...")
            engine.runAndWait()
            logger.info("Local TTS: runAndWait() COMPLETED → %s", Path(output_path).name)
        finally:
            try:
                logger.debug("Local TTS: Calling engine.stop()...")
                engine.stop()
                logger.info("Local TTS: engine.stop() completed")
            except Exception as exc:
                logger.warning("Local TTS: engine.stop() raised: %s", exc)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _resolve_voice_fragment(voice_key: str) -> Optional[str]:
    key = voice_key.lower().strip()
    if key in _VOICE_MAP:
        return _VOICE_MAP[key]
    if key.startswith("local-"):
        return key[6:]
    return None


def _concatenate_wav(chunk_paths: list[Path], output_path: Path) -> None:
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
