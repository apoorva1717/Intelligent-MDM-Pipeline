"""Doubles for the missing-VAT lookups: search, web, LLM reader.

All company data here is the fixture's public Impressum data or obviously fake.
"""

from __future__ import annotations

import json
import re
from typing import Any

from search.base import SearchResult
from search.page_fetcher import PageContent, PageFetchResult


class FakeSearch:
    """``search(query)`` → the URLs of the first entry whose key is in the query."""

    def __init__(self, routes: dict[str, list[str]] | None = None) -> None:
        self.routes = routes or {}
        self.queries: list[str] = []

    async def search(self, query: str, num_results: int = 5, *, country: str | None = None):
        self.queries.append(query)
        for key, urls in self.routes.items():
            if key.lower() in query.lower():
                return [SearchResult(title=u, url=u, snippet="") for u in urls[:num_results]]
        return []


def page(url: str, body: str, *, title: str = "Impressum", footer: str = "") -> PageFetchResult:
    return PageFetchResult(url=url, status=200, content=PageContent(
        url=url, url_path=re.sub(r"^https?://[^/]+", "", url) or "/", page_title=title,
        h1=title, breadcrumb="", body_text=body, footer_text=footer,
    ))


class FakeWeb:
    """``fetch_page_result(url)``: a registered page, an exception's status, or 404."""

    def __init__(self, pages: dict[str, Any] | None = None) -> None:
        self.pages = pages or {}
        self.fetched: list[str] = []

    async def fetch_page_result(self, url: str, timeout: int | None = None) -> PageFetchResult:
        self.fetched.append(url)
        hit = self.pages.get(url)
        if hit is None:
            return PageFetchResult(url=url, status=404)
        if isinstance(hit, int):
            return PageFetchResult(url=url, status=hit)
        if hit == "timeout":
            return PageFetchResult(url=url, error="ReadTimeout")
        return hit


class FakeReader:
    """The LLM: answers per page URL (found in the prompt). An answer is a
    dict, a callable(prompt) → dict, or an Exception to raise."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.prompts: list[str] = []

    async def __call__(self, system: str, user: str, max_tokens: int) -> str:
        self.prompts.append(user)
        url = re.search(r"Page URL: (\S+)", user)
        answer: Any = self.answers.get(url.group(1) if url else "", {"vat_id": None})
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(user)
        return json.dumps(answer)


def reading(vat_id: str | None, attributed_to: str | None = None,
            quote: str | None = None, others: list[str] | None = None) -> dict[str, Any]:
    return {"vat_id": vat_id, "attributed_to": attributed_to,
            "evidence_quote": quote, "other_vat_ids_on_page": others or []}

