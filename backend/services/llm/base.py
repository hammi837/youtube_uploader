"""
backend/services/llm/base.py — Abstract LLM provider interface.

All concrete providers must implement this interface.
The script-generation service depends ONLY on this abstraction,
never on a specific provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """
    Abstract base class for all LLM providers.

    Concrete implementations: GroqProvider, LocalLLMProvider (future Ollama).
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """
        Generate a text response from the model.

        Args:
            prompt:        The user prompt / instruction.
            system_prompt: Optional system-level instruction.
            temperature:   Sampling temperature (0.0–1.0).
            max_tokens:    Maximum tokens in the response.

        Returns:
            Generated text string.

        Raises:
            LLMConfigError:      Provider is misconfigured (e.g. missing API key).
            LLMRateLimitError:   Rate limit exceeded.
            LLMProviderError:    Other provider-level error.
        """
        ...

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """
        Generate a JSON response from the model and parse it.

        Args:
            prompt:        The user prompt / instruction.
            system_prompt: Optional system-level instruction.
            temperature:   Lower temp recommended for structured output.
            max_tokens:    Maximum tokens in the response.

        Returns:
            Parsed dict from the model's JSON output.

        Raises:
            LLMConfigError:      Provider misconfigured.
            LLMRateLimitError:   Rate limit exceeded.
            LLMJSONError:        Model returned malformed JSON.
            LLMProviderError:    Other provider-level error.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name (e.g. 'groq', 'ollama')."""
        ...


# ── Provider-specific exceptions ──────────────────────────────────────────────

class LLMError(Exception):
    """Base class for all LLM provider errors."""


class LLMConfigError(LLMError):
    """Raised when the provider is misconfigured (missing key, bad URL, etc.)."""


class LLMRateLimitError(LLMError):
    """Raised when the provider returns a rate-limit response."""


class LLMJSONError(LLMError):
    """Raised when the model returns a response that cannot be parsed as JSON."""


class LLMProviderError(LLMError):
    """General provider error (network failure, server error, etc.)."""
