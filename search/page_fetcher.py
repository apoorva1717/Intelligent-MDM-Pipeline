"""Fetch and extract clean text from web pages."""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form", "iframe"}


class PageFetcher:
    """Fetches a URL and returns cleaned text content."""

    def __init__(self, timeout: int = 10, max_chars: int = 3000) -> None:
        self._timeout = timeout
        self._max_chars = max_chars

    async def fetch_page_text(self, url: str) -> str | None:
        """Fetch a page and extract visible text.

        Uses requests (synchronous) via run_in_executor to keep
        the async event loop unblocked.  Returns None on any failure.
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, partial(self._sync_fetch, url)
            )
        except Exception:
            logger.exception("Page fetch failed: %s", url[:120])
            return None

    def _sync_fetch(self, url: str) -> str | None:
        resp = requests.get(
            url,
            timeout=self._timeout,
            headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove non-content elements
        for tag in soup.find_all(REMOVE_TAGS):
            tag.decompose()

        text = soup.get_text(separator=" ")
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()

        # Truncate to configured limit
        if len(text) > self._max_chars:
            text = text[: self._max_chars - 1] + "…"

        return text if text else None
