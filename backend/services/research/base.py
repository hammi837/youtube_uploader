"""
backend/services/research/base.py — Abstract research provider interface.

All concrete providers must implement this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ResearchSource:
    """A single research source found during topic research."""
    title: str
    url: str
    snippet: str
    key_points: list[str] = field(default_factory=list)


@dataclass
class ResearchResult:
    """Complete research result for a topic."""
    topic: str
    sources: list[ResearchSource]
    key_facts: list[str]
    research_summary: str


class ResearchProvider(ABC):
    """Abstract base class for research providers."""

    @abstractmethod
    def research(
        self,
        topic: str,
        *,
        language: str = "en",
        max_sources: int = 5,
        depth: str = "standard",
    ) -> ResearchResult:
        """
        Research a topic and return structured results.

        Args:
            topic:       The topic to research.
            language:    ISO language code (e.g. 'en', 'es').
            max_sources: Maximum number of sources to return.
            depth:       Research depth: 'quick', 'standard', or 'deep'.

        Returns:
            ResearchResult with sources, key_facts, and research_summary.

        Raises:
            ResearchEmptyTopicError:  Topic is blank or invalid.
            ResearchNoSourcesError:   Search succeeded but returned zero usable results.
            ResearchNetworkError:     Network unavailable.
            ResearchRateLimitError:   Provider rate-limited the request.
            ResearchError:            Other general failure.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name."""
        ...


# ── Research-specific exceptions ──────────────────────────────────────────────

class ResearchError(Exception):
    """Base class for all research errors."""


class ResearchNetworkError(ResearchError):
    """
    Raised when the research provider cannot reach the network.
    Temporary — retry may succeed.
    """


class ResearchEmptyTopicError(ResearchError):
    """Raised when the topic is empty, whitespace-only, or too long."""


class ResearchNoSourcesError(ResearchError):
    """
    Raised when the search executed successfully but returned zero usable sources.

    This is distinct from a network failure:
      - Network failure  → temporary, retry may help
      - No sources       → the topic query itself returned nothing; rephrasing may help

    Script generation MUST NOT proceed when this error is raised.
    """


class ResearchRateLimitError(ResearchError):
    """Raised when the search provider rate-limits the request."""
