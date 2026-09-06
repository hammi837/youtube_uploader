"""
backend/services/llm/local_provider.py — Local LLM provider stub (future Ollama).

This is a placeholder that satisfies the LLMProvider interface.
Replace the NotImplementedError bodies with real Ollama/llama.cpp calls
when local hardware permits (Phase 2B or later).

Configuration (environment variables):
    OLLAMA_BASE_URL  — Ollama API base URL (default: http://localhost:11434)
    OLLAMA_MODEL     — Model name (default: phi3:mini)

No API key required — Ollama runs fully locally.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.services.llm.base import (
    LLMConfigError,
    LLMProvider,
    LLMProviderError,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "phi3:mini"


class LocalLLMProvider(LLMProvider):
    """
    Local LLM provider using Ollama (or any compatible local inference server).

    Currently a stub — raises LLMConfigError until implemented.
    To enable, install Ollama (https://ollama.com), pull a model, and
    implement the HTTP calls below.

    Free? Yes. Open source? Yes. No internet required after model download.
    Recommended model for i5-6300U: phi3:mini (3.8B Q4, ~2.5 GB, ~2 tok/s)
    """

    def __init__(self) -> None:
        self._base_url = os.getenv("OLLAMA_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
        self._model = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)
        logger.info("LocalLLMProvider configured: url=%s model=%s", self._base_url, self._model)

    @property
    def provider_name(self) -> str:
        return "local"

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        raise LLMConfigError(
            "Local LLM provider (Ollama) is not yet implemented. "
            "Set LLM_PROVIDER=groq and configure GROQ_API_KEY to use the Groq provider, "
            "or implement this class to use a local Ollama model."
        )

    def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        raise LLMConfigError(
            "Local LLM provider (Ollama) is not yet implemented. "
            "Set LLM_PROVIDER=groq and configure GROQ_API_KEY to use the Groq provider."
        )

    # ── Future implementation guide ────────────────────────────────────────
    # When implementing Ollama support:
    #
    # 1. Install ollama Python SDK: pip install ollama
    # 2. Or use direct HTTP to http://localhost:11434/api/generate
    # 3. Replace the raise statements above with actual calls
    # 4. JSON generation: use ollama's format="json" parameter
    # 5. Test with: ollama pull phi3:mini
    #
    # Example (not yet active):
    # import ollama
    # response = ollama.chat(
    #     model=self._model,
    #     messages=[{"role": "user", "content": prompt}],
    #     format="json",
    # )
    # return json.loads(response["message"]["content"])
