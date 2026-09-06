"""
backend/services/research/factory.py — Research provider factory.

Reads RESEARCH_PROVIDER from environment and returns the appropriate provider.

Supported values:
    web   — DuckDuckGo Instant Answer API (default, no key required)
"""

from __future__ import annotations

import logging
import os

from backend.services.research.base import ResearchError, ResearchProvider

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("web",)


def get_research_provider() -> ResearchProvider:
    """
    Return a configured ResearchProvider instance.

    Raises:
        ResearchError: If the provider name is unsupported.
    """
    provider_name = os.getenv("RESEARCH_PROVIDER", "web").strip().lower()
    logger.info("Research provider selected: %s", provider_name)

    if provider_name == "web":
        from backend.services.research.web_provider import WebResearchProvider
        return WebResearchProvider()

    raise ResearchError(
        f"Unknown RESEARCH_PROVIDER: '{provider_name}'. "
        f"Supported: {', '.join(_SUPPORTED_PROVIDERS)}."
    )
