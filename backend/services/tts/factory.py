"""
backend/services/tts/factory.py — TTS provider factory.

Reads TTS_PROVIDER from environment and returns the appropriate provider.

Supported values:
    edge   — Edge-TTS only (no fallback). Requires internet.
    local  — Local Windows SAPI only (pyttsx3). No internet needed.
    auto   — Edge-TTS first; automatically falls back to local on
             network/rate-limit errors. Recommended for production.
"""

from __future__ import annotations

import logging
import os

from backend.services.tts.base import TTSConfigError, TTSProvider

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("edge", "local", "auto")


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

    if provider_name == "local":
        from backend.services.tts.local_provider import LocalTTSProvider
        return LocalTTSProvider()

    if provider_name == "auto":
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from backend.services.tts.manager import AutoTTSManager
        primary  = EdgeTTSProvider()
        fallback = LocalTTSProvider()
        return AutoTTSManager(primary=primary, fallback=fallback)

    raise TTSConfigError(
        f"Unknown TTS_PROVIDER: '{provider_name}'. "
        f"Supported: {', '.join(_SUPPORTED_PROVIDERS)}. "
        "Set TTS_PROVIDER in backend/.env."
    )
