"""
backend/services/llm/factory.py — LLM provider factory.

Reads LLM_PROVIDER from environment and returns the appropriate LLMProvider.

Supported values:
    groq    — Groq Cloud API (requires GROQ_API_KEY)
    local   — Local LLM via Ollama (stub, not yet fully implemented)

The application code should call get_llm_provider() rather than
importing concrete provider classes directly.
"""

from __future__ import annotations

import logging
import os

from backend.services.llm.base import LLMConfigError, LLMProvider

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("groq", "local")


def get_llm_provider() -> LLMProvider:
    """
    Return a configured LLMProvider instance based on the LLM_PROVIDER env var.

    Raises:
        LLMConfigError: If the provider name is unsupported or configuration is missing.
    """
    provider_name = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    logger.info("LLM provider selected: %s", provider_name)

    if provider_name == "groq":
        # Import here to avoid loading httpx at startup when not using Groq
        from backend.services.llm.groq_provider import GroqProvider
        return GroqProvider()

    if provider_name == "local":
        from backend.services.llm.local_provider import LocalLLMProvider
        return LocalLLMProvider()

    raise LLMConfigError(
        f"Unknown LLM_PROVIDER: '{provider_name}'. "
        f"Supported providers: {', '.join(_SUPPORTED_PROVIDERS)}. "
        "Set LLM_PROVIDER in backend/.env."
    )
