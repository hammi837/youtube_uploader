"""
backend/services/research/web_provider.py — DuckDuckGo Search research provider.

Uses the `duckduckgo-search` Python library (pip install duckduckgo-search).
  - No API key required.
  - No authentication.
  - Does NOT bypass CAPTCHAs, paywalls, or access controls.
  - Does NOT scrape full page content — only uses search result snippets.
  - Performs 1–3 lightweight search queries per topic.
  - Respects polite delays between queries.
  - Returns a clear structured error if zero results are found.
  - Never fabricates sources, URLs, or citations.

Why duckduckgo-search instead of the DDG Instant Answer API:
  The Instant Answer API only returns results for topics that have a
  Wikipedia/Wikidata "instant answer" box.  Normal questions like
  "Why do cats purr?" return nothing.  The duckduckgo-search library
  uses DDG's actual web-search endpoint and returns real search results
  (title + URL + snippet) for any topic — the same results a user would
  see in their browser.

Configuration (environment variables):
    RESEARCH_MAX_QUERIES   — max search queries per topic (default: 3)
    RESEARCH_MAX_SOURCES   — max sources to return (default: 5)
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from typing import Any

from backend.services.research.base import (
    ResearchEmptyTopicError,
    ResearchError,
    ResearchNetworkError,
    ResearchNoSourcesError,
    ResearchProvider,
    ResearchRateLimitError,
    ResearchResult,
    ResearchSource,
)

logger = logging.getLogger(__name__)

# Import DDGS at module level so it can be patched in tests.
# If duckduckgo-search is not installed, raise a clear error at import time.
try:
    from duckduckgo_search import DDGS
    from duckduckgo_search.exceptions import DuckDuckGoSearchException, RatelimitException
except ImportError as _ddgs_import_error:
    raise ImportError(
        "duckduckgo-search is not installed. "
        "Run: pip install duckduckgo-search==6.3.7"
    ) from _ddgs_import_error

_MAX_TOPIC_LENGTH = 300
_POLITE_DELAY_SECONDS = 1.2   # seconds between sequential queries
_DEFAULT_MAX_QUERIES = 3
_DEFAULT_MAX_SOURCES = 5
_MIN_SNIPPET_LENGTH = 20       # discard snippets shorter than this


class WebResearchProvider(ResearchProvider):
    """
    Research provider backed by DuckDuckGo web search.

    Generates 1–3 query variations for the topic, runs each through
    DuckDuckGo search, deduplicates results by URL, and returns the
    top N sources.  Raises ResearchNoSourcesError if nothing is found.
    """

    def __init__(self) -> None:
        self._max_queries = int(os.getenv("RESEARCH_MAX_QUERIES", _DEFAULT_MAX_QUERIES))
        self._max_sources = int(os.getenv("RESEARCH_MAX_SOURCES", _DEFAULT_MAX_SOURCES))

    @property
    def provider_name(self) -> str:
        return "web"

    def research(
        self,
        topic: str,
        *,
        language: str = "en",
        max_sources: int | None = None,
        depth: str = "standard",
    ) -> ResearchResult:
        topic = topic.strip()
        if not topic:
            raise ResearchEmptyTopicError("Topic cannot be empty.")
        if len(topic) > _MAX_TOPIC_LENGTH:
            raise ResearchEmptyTopicError(
                f"Topic is too long ({len(topic)} chars). Maximum: {_MAX_TOPIC_LENGTH}."
            )

        effective_max = max_sources if max_sources is not None else self._max_sources
        max_queries = self._max_queries if depth != "quick" else 1

        logger.info(
            "Research started: topic='%s' depth=%s max_queries=%d max_sources=%d",
            topic, depth, max_queries, effective_max,
        )

        # Build query variations
        queries = _build_queries(topic, max_queries)

        # Run searches
        all_sources: list[ResearchSource] = []
        seen_urls: set[str] = set()

        for i, query in enumerate(queries):
            if len(all_sources) >= effective_max:
                break
            if i > 0:
                time.sleep(_POLITE_DELAY_SECONDS)

            needed = effective_max - len(all_sources)
            try:
                results = self._search(query, max_results=needed + 2, language=language)
            except ResearchNetworkError:
                if i == 0:
                    raise  # If the first query fails with a network error, propagate
                logger.warning("Query %d/%d network error, continuing with existing results", i + 1, len(queries))
                break
            except ResearchRateLimitError:
                if i == 0:
                    # First query rate-limited — wait and retry once
                    logger.warning("Rate limited on first query, waiting 5s and retrying...")
                    time.sleep(5.0)
                    try:
                        results = self._search(query, max_results=needed + 2, language=language)
                    except ResearchRateLimitError:
                        raise  # Still rate-limited after retry
                else:
                    logger.warning("Query %d/%d rate-limited, stopping early", i + 1, len(queries))
                    break
            except ResearchError as exc:
                logger.warning("Query %d/%d failed: %s", i + 1, len(queries), exc)
                continue

            for src in results:
                if src.url in seen_urls:
                    continue
                if not _is_valid_url(src.url):
                    continue
                seen_urls.add(src.url)
                all_sources.append(src)
                if len(all_sources) >= effective_max:
                    break

            logger.info(
                "Query %d/%d '%s' → %d new sources (total so far: %d)",
                i + 1, len(queries), query, len(results), len(all_sources),
            )

        # ── Enforce the no-fabrication rule ───────────────────────────────
        if not all_sources:
            logger.warning("No research sources found for topic: '%s'", topic)
            raise ResearchNoSourcesError(
                f"No reliable web sources were found for: '{topic}'. "
                "Try rephrasing the topic or using different keywords."
            )

        key_facts = _extract_key_facts(all_sources)

        summary_parts = [f"Topic: {topic}", f"Found {len(all_sources)} source(s)."]
        for src in all_sources[:3]:
            if src.snippet:
                summary_parts.append(f"- {src.title}: {src.snippet[:200]}")
        research_summary = "\n".join(summary_parts)

        logger.info(
            "Research completed: topic='%s' sources=%d facts=%d",
            topic, len(all_sources), len(key_facts),
        )

        return ResearchResult(
            topic=topic,
            sources=all_sources,
            key_facts=key_facts,
            research_summary=research_summary,
        )

    # ── Internal ───────────────────────────────────────────────────────────

    def _search(
        self,
        query: str,
        max_results: int,
        language: str,
    ) -> list[ResearchSource]:
        """
        Run a single DuckDuckGo search query and return sources.

        Uses the duckduckgo_search library (pip install duckduckgo-search).
        Returns search result snippets — does NOT fetch full page content.
        """
        region = _language_to_ddg_region(language)

        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(
                    query,
                    region=region,
                    safesearch="moderate",
                    max_results=max_results,
                ))
        except RatelimitException as exc:
            raise ResearchRateLimitError(
                "DuckDuckGo rate-limited the search request. "
                "Wait a few seconds and try again."
            ) from exc
        except DuckDuckGoSearchException as exc:
            exc_str = str(exc).lower()
            if any(k in exc_str for k in ("timeout", "connect", "network", "refused",
                                           "unreachable", "ssl", "name resolution")):
                raise ResearchNetworkError(
                    f"Network error during DuckDuckGo search: {str(exc)[:200]}"
                ) from exc
            raise ResearchError(
                f"DuckDuckGo search failed: {str(exc)[:200]}"
            ) from exc
        except Exception as exc:
            exc_name = type(exc).__name__
            exc_str = str(exc).lower()
            # Fallback classification for unexpected exception types
            if "ratelimit" in exc_str or "202" in exc_str or "too many" in exc_str:
                raise ResearchRateLimitError(
                    "DuckDuckGo rate-limited the search request. "
                    "Wait a few seconds and try again."
                ) from exc
            if any(k in exc_str for k in ("timeout", "connect", "network", "refused",
                                           "unreachable", "ssl", "name resolution")):
                raise ResearchNetworkError(
                    f"Network error during DuckDuckGo search ({exc_name})."
                ) from exc
            raise ResearchError(
                f"DuckDuckGo search failed ({exc_name}): {str(exc)[:200]}"
            ) from exc

        sources: list[ResearchSource] = []
        for item in raw_results:
            title   = (item.get("title")   or "").strip()
            url     = (item.get("href")    or "").strip()
            snippet = (item.get("body")    or "").strip()

            if not title or not url or not snippet:
                continue
            if len(snippet) < _MIN_SNIPPET_LENGTH:
                continue

            sources.append(ResearchSource(
                title=title,
                url=url,
                snippet=snippet[:500],
                key_points=_extract_points_from_text(snippet),
            ))

        return sources


# ── Module-level helpers ───────────────────────────────────────────────────────

def _build_queries(topic: str, max_queries: int) -> list[str]:
    """
    Generate a small set of search query variations for a topic.

    The first query is always the raw topic.
    Additional queries are variations that may surface different sources.
    """
    queries: list[str] = [topic]

    if max_queries < 2:
        return queries

    # Second query: add "explained" or "facts" to get educational content
    topic_lower = topic.lower()
    if topic_lower.startswith(("why ", "how ", "what ", "when ", "where ", "who ")):
        queries.append(f"{topic} explained")
    else:
        queries.append(f"{topic} facts")

    if max_queries < 3:
        return queries

    # Third query: science / explanation angle
    if any(topic_lower.startswith(w) for w in ("why ", "how ", "what ")):
        queries.append(f"{topic} scientific explanation")
    else:
        queries.append(f"{topic} overview")

    return queries[:max_queries]


def _language_to_ddg_region(lang: str) -> str:
    """Map ISO 639-1 language code to DuckDuckGo region parameter."""
    mapping = {
        "en": "us-en", "es": "es-es", "fr": "fr-fr",
        "de": "de-de", "pt": "br-pt", "it": "it-it",
        "ja": "jp-jp", "ko": "kr-ko", "zh": "cn-zh",
        "ar": "xa-ar", "ru": "ru-ru",
    }
    return mapping.get(lang.lower()[:2], "us-en")


def _is_valid_url(url: str) -> bool:
    """Basic URL sanity check — must be http/https with a domain."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _extract_points_from_text(text: str) -> list[str]:
    """Split a snippet into short key-point sentences."""
    if not text:
        return []
    sentences = [s.strip() for s in text.split(".") if len(s.strip()) > 20]
    return sentences[:3]


def _extract_key_facts(sources: list[ResearchSource]) -> list[str]:
    """Collect deduplicated key_points from all sources."""
    seen: set[str] = set()
    facts: list[str] = []
    for src in sources:
        for point in src.key_points:
            normalized = point.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                facts.append(normalized)
    return facts[:20]
