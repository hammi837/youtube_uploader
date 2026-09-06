"""
backend/services/llm/groq_provider.py — Groq Cloud LLM provider.

Uses the Groq REST API (free tier available).
Requires: GROQ_API_KEY environment variable.

Security:
  - API key is NEVER logged or included in any exception message.
  - Never expose the key in responses.

Rate limiting:
  - The free tier has limits; we handle 429 responses gracefully.
  - Do NOT design the application assuming unlimited calls.

Model selection:
  - Configured via GROQ_MODEL env var (default: llama3-8b-8192).
  - Other good free options: mixtral-8x7b-32768, gemma-7b-it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from backend.services.llm.base import (
    LLMConfigError,
    LLMJSONError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
)

logger = logging.getLogger(__name__)

_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_DEFAULT_MODEL = "openai/gpt-oss-20b"
_REQUEST_TIMEOUT = 60.0  # seconds


class GroqProvider(LLMProvider):
    """
    LLM provider backed by the Groq Cloud API.

    Configuration (environment variables):
        GROQ_API_KEY   — required; your Groq API key.
        GROQ_MODEL     — optional; model name (default: llama3-8b-8192).
    """

    def __init__(self) -> None:
        self._model = os.getenv("GROQ_MODEL", _DEFAULT_MODEL)
        # Validate config eagerly so errors surface at provider-selection time,
        # not mid-request. Key is stored privately and never logged.
        self._api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not self._api_key:
            raise LLMConfigError(
                "GROQ_API_KEY is not set. "
                "Add it to backend/.env: GROQ_API_KEY=your_key_here"
            )

    @property
    def provider_name(self) -> str:
        return "groq"

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        messages = _build_messages(prompt, system_prompt)
        response_data = self._call_api(messages, temperature=temperature, max_tokens=max_tokens)
        return _extract_text(response_data)

    def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        # Append JSON instruction to system prompt to maximise compliance
        json_system = (
            (system_prompt + "\n\n" if system_prompt else "")
            + "IMPORTANT: Respond with valid JSON only. "
            "Do not include markdown code fences, explanation, or any text outside the JSON object."
        )
        messages = _build_messages(prompt, json_system)
        response_data = self._call_api(messages, temperature=temperature, max_tokens=max_tokens)
        raw = _extract_text(response_data)
        return _parse_json(raw)

    # ── Internal ───────────────────────────────────────────────────────────

    def _call_api(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Make the HTTP request to Groq. Raises typed exceptions on failure."""
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            # Use a fresh client per call — fine for a batch/background workload
            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                response = client.post(_GROQ_API_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMProviderError("Groq API request timed out.") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"Network error contacting Groq API: {type(exc).__name__}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "unknown")
            raise LLMRateLimitError(
                f"Groq rate limit exceeded. Retry after: {retry_after}s. "
                "Consider spacing out requests or switching to a local provider."
            )

        if response.status_code == 401:
            # Do NOT include the key in the error message
            raise LLMConfigError("Groq API key is invalid or expired. Check GROQ_API_KEY.")

        if not response.is_success:
            # Extract error detail without leaking auth info
            try:
                detail = response.json().get("error", {}).get("message", response.text[:200])
            except Exception:
                detail = f"HTTP {response.status_code}"
            raise LLMProviderError(f"Groq API error: {detail}")

        try:
            return response.json()
        except Exception as exc:
            raise LLMProviderError("Groq returned non-JSON response.") from exc


# ── Module-level helpers ───────────────────────────────────────────────────────

def _build_messages(prompt: str, system_prompt: str | None) -> list[dict]:
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def _extract_text(response_data: dict[str, Any]) -> str:
    try:
        return response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMProviderError("Unexpected Groq response structure.") from exc


def _parse_json(raw: str) -> dict[str, Any]:
    """
    Parse JSON from a model response.

    Models sometimes wrap output in markdown fences; we strip those first.
    """
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Find the first {...} block in case there's any preamble
    brace_match = re.search(r"\{[\s\S]+\}", text)
    if brace_match:
        text = brace_match.group(0)

    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned malformed JSON (first 300 chars): %s", raw[:300])
        raise LLMJSONError(
            f"Model returned malformed JSON: {exc}. "
            "This can happen with complex prompts — retry may succeed."
        ) from exc

    if not isinstance(result, dict):
        raise LLMJSONError(f"Expected a JSON object, got {type(result).__name__}.")

    return result
