"""SerpAPI search client implementation."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from serpapi import GoogleSearch

from search.base import SearchClient, SearchResult, SearchUnavailable

logger = logging.getLogger(__name__)


class SerpAPIClient(SearchClient):
    """Search via SerpAPI (Google Search Results)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        country: str | None = None,
    ) -> list[SearchResult]:
        """Execute a Google search via SerpAPI.

        *country* becomes Google's ``gl`` parameter — the country the search is
        run FOR. Without it every record was searched from SerpAPI's default
        locale, so a US record's query and a German one's returned the same
        ranking and the country the record actually stated only ever reached
        the cache key.

        ``hl`` is deliberately left alone: it selects a LANGUAGE, and a country
        does not determine one (Belgium has three official languages, Canada
        two). Guessing it would narrow results on a fact the record never
        stated.

        The underlying library is synchronous, so we run it in a thread executor.
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                partial(self._sync_search, query, num_results, country),
            )
        except Exception as exc:
            # A dropped connection is not "no results". Raising lets
            # `cached_serp` decline to RECORD the failure; the caller still
            # sees an empty list and behaves exactly as it did before.
            logger.warning(
                "SerpAPI search failed for query %r: %s", query[:100], exc,
            )
            raise SearchUnavailable(str(exc)) from exc

    def _sync_search(
        self, query: str, num_results: int, country: str | None = None,
    ) -> list[SearchResult]:
        params = {
            "q": query,
            "num": num_results,
            "api_key": self._api_key,
            "engine": "google",
        }
        # `gl` takes the lowercase alpha-2 code. Only a well-formed one is
        # sent: `cached_serp` normalises before calling, and anything it could
        # not resolve arrives as None rather than as a guess worth passing on.
        if country and len(country) == 2 and country.isalpha():
            params["gl"] = country.lower()
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
