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

from utils.cache import DiskCache, note_network_call

logger = logging.getLogger(__name__)

REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form", "iframe"}


def _log_fetch_failure(url: str, exc: Exception) -> None:
    """Log a fetch failure without the alarming multi-line traceback.

    Most fetch failures are *expected* and already handled by the caller
    (return ``None``/``[]``): a guessed department subdomain that doesn't
    resolve (DNS failure), or a publisher that blocks our user-agent
    (HTTP 403/404). Those are logged as a single concise DEBUG line so
    they don't drown the terminal. Anything genuinely unexpected is
    surfaced at WARNING — still without a full stack trace, since the
    pipeline degrades gracefully either way.
    """
    target = url[:120]
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(exc.response, "status_code", "?")
        logger.debug("Page fetch skipped (HTTP %s): %s", status, target)
    elif isinstance(exc, (requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout)):
        # DNS failures, refused connections, timeouts — expected for
        # probe candidates and unreachable hosts.
        logger.debug("Page fetch skipped (unreachable): %s", target)
    else:
        logger.warning("Page fetch failed (%s): %s", type(exc).__name__, target)


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


@dataclass
class PageFetchResult:
    """One fetch attempt, with the status code kept.

    ``fetch_page_content`` collapses every failure to ``None``, which is the
    right answer for callers that only want the text. Fix 3's corroborator
    needs the difference: a 403 or a bot-challenge means *we could not look*,
    and must never be read as evidence for or against the record, whereas a
    404 on ``/impressum`` simply means try the next path.
    """
    url: str
    status: int | None = None
    content: "PageContent | None" = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def blocked(self) -> bool:
        """The server answered, and the answer was "not to you"."""
        return self.status in (401, 403, 429) or self.status == 451


class PageFetcher:
    """Fetches a URL and returns structured page content.

    Fix B - every entry point below goes through one URL-keyed evidence cache
    (*store*, the ``fetch`` namespace). Before that only the corroborator's
    root read was recorded, by ``page_corroborator.fetch_pages`` at the
    *domain* level; the department probe's subdomain HEADs, its link scrape,
    its candidate verifications, Tier 2A's profile pages and Tier 2B's
    department pages all went to the live web on every run. A batch could
    therefore be re-run against a warm cache and still make hundreds of
    requests, each of which could answer differently.

    Under a frozen store a miss makes no request and returns this method's
    "could not look" value - ``None`` / ``False`` / ``[]`` / a
    ``PageFetchResult`` with no status. The miss is traced by the store.
    """

    def __init__(
        self,
        timeout: int = 10,
        max_chars: int = 1500,
        store: "DiskCache | None" = None,
    ) -> None:
        self._timeout = timeout
        self._max_chars = max_chars
        self._store = store

    # -- the cache seam ------------------------------------------------------

    async def _cached(self, key, live, decode, on_frozen_miss):
        """Serve *key* from the store, else run *live* and record it.

        *decode* turns a recorded JSON payload back into the method's return
        type; *live* returns ``(value, payload)`` - the value to return and the
        JSON to record (``None`` records nothing).
        """
        if self._store is None:
            value, _ = await live()
            return value
        recorded = self._store.get(key)
        if recorded is not None:
            return decode(recorded)
        if self._store.replay_only:
            return on_frozen_miss()
        note_network_call("fetch")
        value, payload = await live()
        if payload is not None:
            self._store.set(key, payload)
        return value

    @staticmethod
    def _content_to_json(content):
        if content is None:
            return None
        return {
            "url": content.url, "url_path": content.url_path,
            "page_title": content.page_title, "h1": content.h1,
            "breadcrumb": content.breadcrumb, "body_text": content.body_text,
        }

    @staticmethod
    def _content_from_json(raw):
        if not isinstance(raw, dict):
            return None
        return PageContent(
            url=raw.get("url") or "", url_path=raw.get("url_path") or "",
            page_title=raw.get("page_title") or "", h1=raw.get("h1") or "",
            breadcrumb=raw.get("breadcrumb") or "",
            body_text=raw.get("body_text") or "",
        )

    async def fetch_page_result(
        self, url: str, timeout: int | None = None,
    ) -> PageFetchResult:
        """Fetch *url*, keeping the HTTP status alongside the content.

        Never raises: a transport failure comes back as ``status=None`` with
        ``error`` set, which the corroborator treats as "could not look".
        """
        async def _live():
            loop = asyncio.get_running_loop()
            try:
                res = await loop.run_in_executor(
                    None, partial(self._sync_fetch_result, url, timeout),
                )
            except Exception as exc:  # noqa: BLE001 - never fail a record
                _log_fetch_failure(url, exc)
                res = PageFetchResult(url=url, error=type(exc).__name__)
            return res, {
                "url": res.url, "status": res.status, "error": res.error,
                "content": self._content_to_json(res.content),
            }

        def _decode(raw):
            return PageFetchResult(
                url=raw.get("url") or url, status=raw.get("status"),
                error=raw.get("error"),
                content=self._content_from_json(raw.get("content")),
            )

        return await self._cached(
            "result:" + url, _live, _decode,
            lambda: PageFetchResult(url=url, error="frozen_miss"),
        )

    def _sync_fetch_result(
        self, url: str, timeout: int | None = None,
    ) -> PageFetchResult:
        try:
            resp = requests.get(
                url,
                timeout=timeout or self._timeout,
                headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
                allow_redirects=True,
                # No explicit `verify`: requests then honours REQUESTS_CA_BUNDLE,
                # which config._sanitize_ssl_env points at the corp CA bundle.
                # Pinning certifi here (as the probe helpers do, where the
                # targets are institution hosts) fails every fetch behind the
                # TLS-inspecting VPN — measured at 47 of 54 domains on the
                # chemspeed batch, all SSLError. `_sync_fetch_structured` has
                # always omitted it for the same reason.
            )
        except Exception as exc:  # noqa: BLE001
            _log_fetch_failure(url, exc)
            return PageFetchResult(url=url, error=type(exc).__name__)

        if not (200 <= resp.status_code < 300):
            return PageFetchResult(url=resp.url or url, status=resp.status_code)
        return PageFetchResult(
            url=resp.url or url,
            status=resp.status_code,
            content=self._parse(resp.url or url, resp.text),
        )

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
        async def _live():
            loop = asyncio.get_running_loop()
            try:
                content = await loop.run_in_executor(
                    None, partial(self._sync_fetch_structured, url)
                )
            except Exception as exc:
                _log_fetch_failure(url, exc)
                content = None
            return content, {"content": self._content_to_json(content)}

        return await self._cached(
            "content:" + url, _live,
            lambda raw: self._content_from_json(raw.get("content")),
            lambda: None,
        )

    async def subdomain_exists(self, host: str, timeout: int = 5) -> bool:
        """HEAD-probe ``https://<host>/``. Returns ``True`` for any
        2xx/3xx response, ``False`` for 4xx/5xx/timeout/DNS failure.

        Used to verify candidate department subdomains (e.g.
        ``radiology.jhu.edu``, ``eps.harvard.edu``) before falling
        through to SERP-based discovery.
        """
        async def _live():
            loop = asyncio.get_running_loop()
            try:
                exists = await loop.run_in_executor(
                    None, partial(self._sync_subdomain_exists, host, timeout),
                )
            except Exception:
                exists = False
            return exists, {"exists": bool(exists)}

        return await self._cached(
            "head:" + host, _live,
            lambda raw: bool(raw.get("exists")),
            lambda: False,
        )

    async def resolve_final_url(self, url: str, timeout: int = 5) -> str | None:
        """Follow *url*'s redirect chain once and return the FINAL URL (or None
        on failure). Lets the department probe key off the live host when ROR's
        website is stale (``dur.ac.uk`` → ``durham.ac.uk``)."""
        async def _live():
            loop = asyncio.get_running_loop()
            try:
                final = await loop.run_in_executor(
                    None, partial(self._sync_resolve_final_url, url, timeout),
                )
            except Exception:
                final = None
            return final, {"final_url": final}

        return await self._cached(
            "final:" + url, _live,
            lambda raw: raw.get("final_url"),
            lambda: None,
        )

    def _sync_resolve_final_url(self, url: str, timeout: int) -> str | None:
        try:
            resp = requests.head(
                url, timeout=timeout, allow_redirects=True,
                headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
                verify=certifi.where(),
            )
            if resp.status_code >= 400:
                # Some servers reject HEAD — retry with a streamed GET.
                resp = requests.get(
                    url, timeout=timeout, allow_redirects=True, stream=True,
                    headers={"User-Agent": "BrukerMDM-Enrichment/1.0"},
                    verify=certifi.where(),
                )
                final = resp.url
                resp.close()
                return final or None
            return resp.url or None
        except Exception:
            return None

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
        async def _live():
            loop = asyncio.get_running_loop()
            try:
                links = await loop.run_in_executor(
                    None,
                    partial(self._sync_fetch_outgoing_links, url, base_domain),
                )
            except Exception as exc:
                _log_fetch_failure(url, exc)
                links = []
            return links, {"links": [[t, u] for t, u in links]}

        return await self._cached(
            "links:" + url + "|" + base_domain, _live,
            lambda raw: [
                (str(pair[0]), str(pair[1]))
                for pair in (raw.get("links") or ())
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            ],
            lambda: [],
        )

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
        return self._parse(url, resp.text)

    def _parse(self, url: str, html: str) -> PageContent:
        """Structured slices of one fetched page. The extraction rules live
        here so both fetch entry points produce identical content."""
        soup = BeautifulSoup(html, "html.parser")

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
