"""DuckDuckGo search client — free fallback when no SerpAPI key."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from duckduckgo_search import DDGS

from search.base import SearchClient, SearchResult, SearchUnavailable

logger = logging.getLogger(__name__)


class DuckDuckGoClient(SearchClient):
    """Search via duckduckgo-search library (no API key required)."""

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        """Execute a DuckDuckGo text search in a thread executor."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                partial(self._sync_search, query, num_results),
            )
        except Exception as exc:
            # See `SearchUnavailable`: a transport failure must not be
            # recorded as "this query has no results".
            logger.warning(
                "DuckDuckGo search failed for query %r: %s", query[:100], exc,
            )
            raise SearchUnavailable(str(exc)) from exc

    def _sync_search(self, query: str, num_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=num_results):
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        snippet=r.get("body", ""),
                    )
                )
        return results[:num_results]
