"""
backend/services/tts/manager.py — TTS Provider Manager with automatic fallback.

When TTS_PROVIDER=auto:
  1. Attempt synthesis with Edge-TTS (primary).
  2. If Edge-TTS raises TTSNetworkError or TTSRateLimitError (transient failures),
     immediately fall back to LocalTTSProvider (pyttsx3/Windows SAPI).
  3. Non-transient errors (TTSValidationError, TTSConfigError) are NOT
     caught by the manager — they bubble up as-is so the caller gets a
     clear error message.

This design ensures:
  - A temporary Microsoft 403 / IP block does NOT stop the pipeline.
  - Invalid input still raises immediately (no silent degradation).
  - The provider that actually generated the audio is recorded.
  - Edge is always tried first in auto mode (best quality when available).

Provider selection (TTS_PROVIDER env var):
    edge  — Edge-TTS only, no fallback.
    local — Local SAPI only, Edge never called.
    auto  — Edge first, local fallback on network/rate-limit errors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from backend.services.tts.base import (
    TTSNetworkError,
    TTSProvider,
    TTSRateLimitError,
    TTSResult,
    TTSVoice,
)

logger = logging.getLogger(__name__)


class AutoTTSManager(TTSProvider):
    """
    Provider manager that tries Edge-TTS and falls back to local on failure.

    Implements TTSProvider so it is a drop-in replacement anywhere a
    TTSProvider is expected.
    """

    def __init__(
        self,
        primary: TTSProvider,
        fallback: TTSProvider,
    ) -> None:
        self._primary  = primary
        self._fallback = fallback
        # Track which provider was used in the last synthesize() call
        self._last_used_provider: str = primary.provider_name

    @property
    def provider_name(self) -> str:
        return "auto"

    @property
    def last_used_provider(self) -> str:
        """Name of the provider that generated the most recent audio."""
        return self._last_used_provider

    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        """
        Try primary (Edge), fall back to local on transient failures.

        Transient failures that trigger fallback:
          - TTSNetworkError  (403, timeout, connection refused, etc.)
          - TTSRateLimitError

        Errors that do NOT trigger fallback (bubble up immediately):
          - TTSValidationError  (empty text, text too long, invalid voice)
          - TTSConfigError      (missing dependency, misconfiguration)
          - TTSGenerationError  (provider-side synthesis error)
        """
        try:
            logger.info(
                "AutoTTSManager: attempting primary provider '%s' for text_len=%d",
                self._primary.provider_name, len(text),
            )
            result = await self._primary.synthesize(text, voice, output_path)
            self._last_used_provider = self._primary.provider_name
            logger.info(
                "AutoTTSManager: primary provider '%s' succeeded (file=%s, size=%d bytes)",
                self._primary.provider_name,
                Path(result.file_path).name,
                result.file_size_bytes,
            )
            return result

        except (TTSNetworkError, TTSRateLimitError) as primary_exc:
            logger.warning(
                "AutoTTSManager: primary provider '%s' failed with transient error "
                "(%s: %s) — switching to fallback '%s'",
                self._primary.provider_name,
                type(primary_exc).__name__,
                str(primary_exc)[:120],
                self._fallback.provider_name,
            )

            # Adjust output path for the fallback provider's format
            # (local produces WAV, not MP3)
            fallback_path = _adjust_path_for_provider(output_path, self._fallback)

            logger.info(
                "AutoTTSManager: attempting fallback synthesis with '%s' → %s",
                self._fallback.provider_name, fallback_path,
            )
            logger.info(
                "AutoTTSManager: calling fallback.synthesize() with text_len=%d voice=%s",
                len(text), _local_voice(voice),
            )
            result = await self._fallback.synthesize(text, _local_voice(voice), fallback_path)
            logger.info(
                "AutoTTSManager: fallback.synthesize() returned successfully",
            )
            self._last_used_provider = self._fallback.provider_name
            logger.info(
                "AutoTTSManager: fallback provider '%s' succeeded (file=%s, size=%d bytes)",
                self._fallback.provider_name,
                Path(result.file_path).name,
                result.file_size_bytes,
            )
            return result

    async def get_voices(self, language: Optional[str] = None) -> list[TTSVoice]:
        """
        Return voices from both primary and fallback providers combined.

        Primary voices are listed first. Fallback (local) voices are
        appended so the frontend can show them as a separate group.
        """
        voices: list[TTSVoice] = []

        try:
            primary_voices = await self._primary.get_voices(language=language)
            voices.extend(primary_voices)
        except Exception as exc:
            logger.warning(
                "AutoTTSManager: could not load primary voices (%s): %s",
                type(exc).__name__, str(exc)[:80],
            )

        try:
            fallback_voices = await self._fallback.get_voices(language=language)
            voices.extend(fallback_voices)
        except Exception as exc:
            logger.warning(
                "AutoTTSManager: could not load fallback voices (%s): %s",
                type(exc).__name__, str(exc)[:80],
            )

        return voices


# ── Module-level helpers ───────────────────────────────────────────────────────

def _local_voice(voice: str) -> str:
    """
    Map an Edge voice name to an appropriate local voice key.

    If the requested voice is an Edge voice (e.g. "en-US-AriaNeural"),
    use the local default instead of passing an incompatible name.
    If it is already a local voice key (e.g. "local-zira"), pass it through.
    """
    if voice.startswith("local-"):
        return voice
    # Any Edge voice → use local-default (falls back to first SAPI voice)
    return "local-default"


def _adjust_path_for_provider(output_path: str, provider: TTSProvider) -> str:
    """
    Adjust the output file extension based on the provider's format.

    Local provider produces WAV; Edge produces MP3.
    """
    from pathlib import Path
    p = Path(output_path)
    if provider.provider_name == "local" and p.suffix.lower() == ".mp3":
        return str(p.with_suffix(".wav"))
    return output_path
