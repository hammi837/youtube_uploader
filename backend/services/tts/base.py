"""
backend/services/tts/base.py — Abstract TTS provider interface.

All concrete providers must implement TTSProvider.
The rest of the application depends only on this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSVoice:
    """Metadata about a single TTS voice."""
    name: str          # e.g. "en-US-AriaNeural"
    locale: str        # e.g. "en-US"
    language: str      # e.g. "English"
    gender: str        # "Female" | "Male" | "Unknown"


@dataclass
class TTSResult:
    """Result returned after a successful synthesis."""
    file_path: str              # absolute path to the generated MP3
    voice: str                  # voice name used
    format: str                 # "mp3"
    file_size_bytes: int        # size of the generated file
    duration_seconds: Optional[float]  # None if duration could not be determined
    text_length: int            # character count of the input text


class TTSProvider(ABC):
    """Abstract base class for all TTS providers."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        """
        Synthesize text to speech and save as MP3.

        Args:
            text:        The narration text to synthesize.
            voice:       Provider-specific voice identifier.
            output_path: Absolute path where the MP3 should be saved.

        Returns:
            TTSResult with file metadata.

        Raises:
            TTSConfigError:     Provider misconfigured.
            TTSNetworkError:    Network failure contacting provider.
            TTSRateLimitError:  Provider rate-limited.
            TTSGenerationError: Provider returned an error during synthesis.
            TTSValidationError: Input validation failed (empty text, bad voice, etc.).
        """
        ...

    @abstractmethod
    async def get_voices(self, language: Optional[str] = None) -> list[TTSVoice]:
        """
        Return available voices, optionally filtered by language code.

        Args:
            language: Optional ISO 639-1 language code filter (e.g. 'en').

        Returns:
            List of TTSVoice objects.

        Raises:
            TTSNetworkError:    Cannot reach voice list endpoint.
            TTSConfigError:     Provider misconfigured.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name."""
        ...


# ── TTS exceptions ────────────────────────────────────────────────────────────

class TTSException(Exception):
    """Base class for all TTS errors."""


class TTSConfigError(TTSException):
    """Provider is misconfigured."""


class TTSNetworkError(TTSException):
    """Network failure — temporary, retry may succeed."""


class TTSRateLimitError(TTSException):
    """Provider is rate-limiting requests."""


class TTSGenerationError(TTSException):
    """Provider returned an error during synthesis."""


class TTSValidationError(TTSException):
    """Input validation failed (empty text, invalid voice, text too long, etc.)."""
