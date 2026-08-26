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

    async def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        country: str | None = None,
    ) -> list[SearchResult]:
        """Execute a DuckDuckGo text search in a thread executor.

        *country* is accepted and NOT used. DDG's equivalent control is
        ``region``, which is a country-LANGUAGE pair (``us-en``, ``de-de``) —
        deriving one from a country alone means inventing the language half,
        and a wrong guess ("be-fr" for a Flemish organisation) narrows the
        search on a fact the record never stated. The country still does its
        real work downstream, where
        `utils.domain_resolver.country_conflict` judges the domains that come
        back regardless of how they were ranked.
        """
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
