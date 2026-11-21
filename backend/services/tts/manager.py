"""
backend/services/tts/manager.py — TTS Provider Manager with automatic fallback.

When TTS_PROVIDER=auto (default chain):
  1. Edge-TTS (best quality when reachable)
  2. Piper local neural TTS (CPU ONNX) when installed + model present
  3. Local Windows SAPI / pyttsx3 (last-resort robotic fallback)

Transient / degraded failures advance to the next provider:
  - TTSNetworkError, TTSRateLimitError
  - TTSConfigError (e.g. Piper model missing)
  - TTSGenerationError (provider crash during synthesis)

TTSValidationError always bubbles immediately (bad input — do not degrade).

The provider that actually generated the audio is recorded via last_used_provider.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from backend.services.tts.base import (
    TTSConfigError,
    TTSGenerationError,
    TTSNetworkError,
    TTSProvider,
    TTSRateLimitError,
    TTSResult,
    TTSVoice,
)

logger = logging.getLogger(__name__)

# Errors that justify trying the next provider in the chain
_CHAIN_FALLBACK_EXCEPTIONS = (
    TTSNetworkError,
    TTSRateLimitError,
    TTSConfigError,
    TTSGenerationError,
)


class AutoTTSManager(TTSProvider):
    """
    Ordered provider chain. Implements TTSProvider as a drop-in.

    Backward compatible with AutoTTSManager(primary=..., fallback=...).
    Prefer AutoTTSManager(providers=[edge, piper, local]) for P0.4.
    """

    def __init__(
        self,
        primary: TTSProvider | None = None,
        fallback: TTSProvider | None = None,
        providers: Sequence[TTSProvider] | None = None,
    ) -> None:
        if providers is not None:
            chain = list(providers)
        else:
            chain = [p for p in (primary, fallback) if p is not None]
        if not chain:
            raise TTSConfigError("AutoTTSManager requires at least one TTS provider.")
        self._providers = chain
        # Backward-compat attributes used by existing tests
        self._primary = chain[0]
        self._fallback = chain[-1] if len(chain) > 1 else chain[0]
        self._last_used_provider: str = chain[0].provider_name

    @property
    def provider_name(self) -> str:
        return "auto"

    @property
    def last_used_provider(self) -> str:
        """Name of the provider that generated the most recent audio."""
        return self._last_used_provider

    @property
    def providers(self) -> list[TTSProvider]:
        return list(self._providers)

    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        last_exc: Exception | None = None
        n = len(self._providers)

        for i, provider in enumerate(self._providers):
            is_last = i == n - 1
            mapped_voice = _map_voice_for_provider(voice, provider)
            out_path = _adjust_path_for_provider(output_path, provider)

            try:
                logger.info(
                    "AutoTTSManager: attempting provider '%s' (%d/%d) text_len=%d voice=%s",
                    provider.provider_name, i + 1, n, len(text), mapped_voice,
                )
                result = await provider.synthesize(text, mapped_voice, out_path)
                self._last_used_provider = provider.provider_name
                logger.info(
                    "AutoTTSManager: provider '%s' succeeded (file=%s, size=%d bytes)",
                    provider.provider_name,
                    Path(result.file_path).name,
                    result.file_size_bytes,
                )
                return result

            except _CHAIN_FALLBACK_EXCEPTIONS as exc:
                last_exc = exc
                if is_last:
                    logger.error(
                        "AutoTTSManager: final provider '%s' failed (%s: %s)",
                        provider.provider_name, type(exc).__name__, str(exc)[:160],
                    )
                    raise
                logger.warning(
                    "AutoTTSManager: provider '%s' failed (%s: %s) — trying next",
                    provider.provider_name, type(exc).__name__, str(exc)[:120],
                )
                continue

        # Unreachable, but keeps type-checkers happy
        if last_exc:
            raise last_exc
        raise TTSGenerationError("AutoTTSManager: no providers available.")

    async def get_voices(self, language: Optional[str] = None) -> list[TTSVoice]:
        """Return voices from all providers in chain order."""
        voices: list[TTSVoice] = []
        for provider in self._providers:
            try:
                voices.extend(await provider.get_voices(language=language))
            except Exception as exc:
                logger.warning(
                    "AutoTTSManager: could not load voices from '%s' (%s): %s",
                    provider.provider_name, type(exc).__name__, str(exc)[:80],
                )
        return voices


# ── Module-level helpers ───────────────────────────────────────────────────────

def _map_voice_for_provider(voice: str, provider: TTSProvider) -> str:
    """Map a requested voice name to something the target provider understands."""
    name = provider.provider_name
    if name == "local":
        return _local_voice(voice)
    if name == "piper":
        return _piper_voice(voice)
    return voice


def _local_voice(voice: str) -> str:
    """Map Edge/Piper voice names to a local SAPI key."""
    if voice.startswith("local-"):
        return voice
    return "local-default"


def _piper_voice(voice: str) -> str:
    """Pass through piper-* keys; otherwise let PiperTTSProvider use its default."""
    if voice.startswith("piper-"):
        return voice
    if voice.startswith("en_") or voice.endswith((".onnx",)):
        return voice
    # Edge / SAPI names → provider resolves PIPER_VOICE default
    return voice


def _adjust_path_for_provider(output_path: str, provider: TTSProvider) -> str:
    """
    Adjust the output file extension based on the provider's format.

    Piper and local produce WAV; Edge produces MP3.
    """
    p = Path(output_path)
    if provider.provider_name in ("local", "piper") and p.suffix.lower() == ".mp3":
        return str(p.with_suffix(".wav"))
    return output_path
