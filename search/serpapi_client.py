"""SerpAPI search client implementation."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from serpapi import GoogleSearch

from search.base import SearchClient, SearchResult

logger = logging.getLogger(__name__)


class SerpAPIClient(SearchClient):
    """Search via SerpAPI (Google Search Results)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        """Execute a Google search via SerpAPI.

        The underlying library is synchronous, so we run it in a thread executor.
        """
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                partial(self._sync_search, query, num_results),
            )
            return raw
        except Exception:
            logger.exception("SerpAPI search failed for query: %s", query[:100])
            return []

    def _sync_search(self, query: str, num_results: int) -> list[SearchResult]:
        params = {
            "q": query,
            "num": num_results,
            "api_key": self._api_key,
            "engine": "google",
        }
        search = GoogleSearch(params)
        data = search.get_dict()
        results: list[SearchResult] = []
        for item in data.get("organic_results", []):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                )
            )
        return results[:num_results]
