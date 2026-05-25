"""Fetch web pages and extract structured/authoritative elements.

The extractor pulls out the parts of the page that institutions
actually use to name themselves (title tag, H1, breadcrumb, URL path)
so downstream LLM calls can work on a small, reliable, deterministic
slice of the page — not the full body prose where casual phrasing
would otherwise get mixed in with canonical names.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from functools import partial
from urllib.parse import urljoin, urlparse

import certifi
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form", "iframe"}


@dataclass
class PageContent:
    """Structured, authoritative slices of a fetched page."""
    url: str
    url_path: str
    page_title: str
    h1: str
    breadcrumb: str
    body_text: str  # kept for debugging / legacy callers

    def is_empty(self) -> bool:
        return not any([self.page_title, self.h1, self.breadcrumb, self.body_text])


class PageFetcher:
    """Fetches a URL and returns structured page content."""

    def __init__(self, timeout: int = 10, max_chars: int = 1500) -> None:
        self._timeout = timeout
        self._max_chars = max_chars

    async def fetch_page_text(self, url: str) -> str | None:
        """Legacy API: return a flat body-text string.

        Retained for Tier2A and any other callers that still expect
        a plain string. New callers (Tier2B) should use
        ``fetch_page_content`` to get the structured slices.
        """
        content = await self.fetch_page_content(url)
        if content is None:
            return None
        return content.body_text or None

    async def fetch_page_content(self, url: str) -> PageContent | None:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, partial(self._sync_fetch_structured, url)
            )
        except Exception:
            logger.exception("Page fetch failed: %s", url[:120])
            return None

    async def subdomain_exists(self, host: str, timeout: int = 5) -> bool:
        """HEAD-probe ``https://<host>/``. Returns ``True`` for any
        2xx/3xx response, ``False`` for 4xx/5xx/timeout/DNS failure.

        Used to verify candidate department subdomains (e.g.
        ``radiology.jhu.edu``, ``eps.harvard.edu``) before falling
        through to SERP-based discovery.
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, partial(self._sync_subdomain_exists, host, timeout),
            )
        except Exception:
            return False

    def _sync_subdomain_exists(self, host: str, timeout: int) -> bool:
        try:
            resp = requests.head(
                f"https://{host}/",
                timeout=timeout,
                allow_redirects=True,
                headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
                verify=certifi.where(),
            )
        except Exception:
            return False
        return 200 <= resp.status_code < 400

    async def fetch_outgoing_links(
        self, url: str, base_domain: str,
    ) -> list[tuple[str, str]]:
        """Fetch *url* and return ``(anchor_text, absolute_href)`` tuples
        for every link whose host differs from *base_domain*.

        Includes both subdomains of the institution domain (e.g.
        ``cs.mit.edu`` for base ``mit.edu``) AND cross-domain links
        (e.g. ``hopkinsmedicine.org`` linked from ``jhu.edu``). The bare
        institution host is excluded — those are intra-site links, not
        candidates for a department-specific home.

        Used by the orchestrator's department-URL probe.
        Returns an empty list on fetch failure or no matching links.
        """
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, partial(self._sync_fetch_outgoing_links, url, base_domain),
            )
        except Exception:
            logger.exception("Outgoing link extraction failed: %s", url[:120])
            return []

    def _sync_fetch_outgoing_links(
        self, url: str, base_domain: str,
    ) -> list[tuple[str, str]]:
        # verify=certifi.where() — bypass a bogus SSL_CERT_FILE /
        # REQUESTS_CA_BUNDLE env var (Windows corp-CA gotcha already
        # handled in tier1_ror and the OpenAI client).
        resp = requests.get(
            url,
            timeout=self._timeout,
            headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
            verify=certifi.where(),
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        base = base_domain.lower().strip()
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            absolute = urljoin(url, a["href"])
            try:
                host = (urlparse(absolute).hostname or "").lower()
            except Exception:
                continue
            if not host:
                continue
            if host.startswith("www."):
                host = host[4:]
            if host == base:
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            text = (a.get_text(" ", strip=True) or "")[:200]
            out.append((text, absolute))
        return out

    def _sync_fetch_structured(self, url: str) -> PageContent | None:
        resp = requests.get(
            url,
            timeout=self._timeout,
            headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title tag (before decomposing anything)
        title_tag = soup.find("title")
        page_title = title_tag.get_text(" ", strip=True) if title_tag else ""

        # Breadcrumb detection BEFORE removing <nav> — breadcrumbs
        # usually live inside <nav> but are authoritative navigation.
        breadcrumb = _extract_breadcrumb(soup)

        # H1 heading
        h1_tag = soup.find("h1")
        h1 = h1_tag.get_text(" ", strip=True) if h1_tag else ""

        # URL path, cleaned
        parsed = urlparse(url)
        url_path = parsed.path or ""

        # Remove non-content elements and extract body text
        for tag in soup.find_all(REMOVE_TAGS):
            tag.decompose()
        body_text = soup.get_text(separator=" ")
        body_text = re.sub(r"\s+", " ", body_text).strip()
        if len(body_text) > self._max_chars:
            body_text = body_text[: self._max_chars - 1] + "…"

        return PageContent(
            url=url,
            url_path=url_path,
            page_title=page_title[:300],
            h1=h1[:300],
            breadcrumb=breadcrumb[:300],
            body_text=body_text,
        )


def _extract_breadcrumb(soup: BeautifulSoup) -> str:
    """Try several common breadcrumb patterns.

    Looks for elements with aria-label="breadcrumb", role="navigation"
    containing "breadcrumb", or class names containing "breadcrumb".
    Returns joined text or an empty string.
    """
    candidates = []

    # Semantic: aria-label or role
    for el in soup.find_all(attrs={"aria-label": re.compile(r"breadcrumb", re.I)}):
        candidates.append(el)
    for el in soup.find_all(attrs={"role": "navigation"}):
        if "breadcrumb" in " ".join(el.get("class") or []).lower():
            candidates.append(el)

    # Class-based fallback
    for el in soup.find_all(class_=re.compile(r"breadcrumb", re.I)):
        candidates.append(el)

    for el in candidates:
        items = [li.get_text(" ", strip=True) for li in el.find_all(["li", "a", "span"])]
        items = [i for i in items if i]
        if items:
            return " › ".join(items)
        text = el.get_text(" ", strip=True)
        if text:
            return text

    return ""
