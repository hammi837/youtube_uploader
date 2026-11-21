"""
backend/services/tts/factory.py — TTS provider factory.

Reads TTS_PROVIDER from environment and returns the appropriate provider.

Supported values:
    edge   — Edge-TTS only (no fallback). Requires internet.
    piper  — Piper local neural TTS only (CPU). Requires piper-tts + voice model.
    local  — Local Windows SAPI only (pyttsx3). No internet needed.
    auto   — Edge → Piper → local SAPI (recommended).
"""

from __future__ import annotations

import logging
import os

from backend.services.tts.base import TTSConfigError, TTSProvider

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("edge", "piper", "local", "auto")


def get_tts_provider() -> TTSProvider:
    """
    Return a configured TTSProvider instance.

    Raises:
        TTSConfigError: If the provider name is unsupported or required
                        dependencies are missing.
    """
    provider_name = os.getenv("TTS_PROVIDER", "auto").strip().lower()
    logger.info("TTS provider selected: %s", provider_name)

    if provider_name == "edge":
        from backend.services.tts.edge_provider import EdgeTTSProvider
        return EdgeTTSProvider()

    if provider_name == "piper":
        from backend.services.tts.piper_provider import PiperTTSProvider
        return PiperTTSProvider()

    if provider_name == "local":
        from backend.services.tts.local_provider import LocalTTSProvider
        return LocalTTSProvider()

    if provider_name == "auto":
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from backend.services.tts.manager import AutoTTSManager

        chain: list[TTSProvider] = [EdgeTTSProvider()]

        # Piper is optional in the chain — missing package or model falls through
        try:
            from backend.services.tts.piper_provider import PiperTTSProvider
            piper = PiperTTSProvider()
            if piper.is_available():
                chain.append(piper)
                logger.info(
                    "Auto TTS chain: Edge → Piper (%s) → local SAPI",
                    os.getenv("PIPER_VOICE", "en_US-ljspeech-medium"),
                )
            else:
                logger.warning(
                    "Piper package loaded but voice model not found under "
                    "PIPER_MODEL_DIR — auto chain is Edge → local SAPI. "
                    "See docs/PIPER_TTS.md to install a voice."
                )
        except TTSConfigError as exc:
            logger.warning(
                "Piper unavailable (%s) — auto chain is Edge → local SAPI",
                str(exc)[:120],
            )

        chain.append(LocalTTSProvider())
        return AutoTTSManager(providers=chain)

    raise TTSConfigError(
        f"Unknown TTS_PROVIDER: '{provider_name}'. "
        f"Supported: {', '.join(_SUPPORTED_PROVIDERS)}. "
        "Set TTS_PROVIDER in backend/.env."
    )
