"""Resolve the official website URL for a Name 1 organisation.

Two paths are exposed:

* :func:`resolve_website_via_serp` (Path B) — runs for *any* record
  type that did not match in ROR. The query shape varies by record
  type: research institutions get ``"name" official website`` (with
  ``.edu``/``.gov``/``.org`` results promoted to high confidence);
  companies / unknowns get ``"name" official website city state``
  with no TLD bias.
* :func:`infer_website_via_llm` (Path C) — fallback when Path B
  returns nothing usable. One LLM call; the orchestrator always
  flags Path C results for manual review.

Path A (ROR's authoritative ``links[]`` website) is handled inline by
the orchestrator using
:func:`enrichment.tier1_ror.extract_website_from_ror` — no module here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from llm.openai_client import OpenAIClient
from llm.prompts import (
    WEBSITE_INFERENCE_SYSTEM_PROMPT,
    WEBSITE_INFERENCE_USER_PROMPT_TEMPLATE,
)
from search.base import SearchClient, SearchResult
from utils.cache import BatchCache
from utils.text_utils import extract_domain

logger = logging.getLogger(__name__)


# Domains to exclude from SERP candidate selection — directories,
# social networks, review sites, employment aggregators. The official
# institution / company site is never one of these.
DOMAIN_BLACKLIST: frozenset[str] = frozenset({
    "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com",
    "x.com", "instagram.com", "youtube.com", "ratemyprofessors.com",
    "glassdoor.com", "yelp.com", "bbb.org", "crunchbase.com",
    "bloomberg.com", "indeed.com", "ziprecruiter.com",
})

# TLDs we treat as authoritative for research-institution / public
# bodies. An on-blacklist match is rejected before this is consulted.
_OFFICIAL_TLDS: frozenset[str] = frozenset({"edu", "gov", "org"})

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class WebsiteResolution:
    """Outcome of a website resolution attempt.

    ``confidence='high'`` → orchestrator writes ``website_url`` with no
    review flag.
    ``confidence='low'``  → orchestrator writes ``website_url`` AND
    sets ``flag_for_review``.
    ``confidence='none'`` → orchestrator leaves the field empty.
    """
    url: str | None = None
    confidence: str = "none"  # "high" | "low" | "none"
    source: str = "none"      # "serp" | "llm" | "none"


# ---------------------------------------------------------------------------
# Path B — SERP-based resolution
# ---------------------------------------------------------------------------

def _is_blacklisted(url: str) -> bool:
    domain = extract_domain(url) or ""
    return any(domain == bad or domain.endswith("." + bad) for bad in DOMAIN_BLACKLIST)


def _tld(url: str) -> str | None:
    domain = extract_domain(url)
    if not domain:
        return None
    return domain.rsplit(".", 1)[-1].lower()


def _significant_tokens(name1: str) -> set[str]:
    return {t for t in re.split(r"\W+", name1.lower()) if len(t) >= 4}


def _name_overlap(name1: str, candidate: SearchResult) -> bool:
    """True if any significant token of name1 appears in URL or title."""
    haystack = f"{candidate.url} {candidate.title}".lower()
    return any(t in haystack for t in _significant_tokens(name1))


def _name_in_domain(name1: str, url: str) -> bool:
    """True if a significant name1 token is a substring of the host."""
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    return any(t in domain for t in _significant_tokens(name1))


def _domain_introduces_foreign_brand(name1: str, url: str) -> bool:
    """True if the host's primary label carries a distinctive word absent
    from *name1* — the mark of a subsidiary / sub-brand domain.

    "Siemens AG" → ``siemens.com`` is clean (label is just "siemens"), but
    ``siemens-healthineers.com`` introduces "healthineers", a distinctive word
    the name never had → a *different* (sub-)entity's site. Only hyphen/
    underscore-separated labels are inspected: a single concatenated label
    ("thermofisher", "bankofamerica") can't be split reliably without a word
    list, so it is treated as clean to avoid false positives on legitimate
    multi-word company domains.
    """
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    label = domain.split(".")[0]
    parts = [p for p in re.split(r"[-_]", label) if p]
    if len(parts) <= 1:
        return False  # single label — cannot detect a foreign word safely
    tokens = _significant_tokens(name1)
    for part in parts:
        if len(part) < 4:
            continue  # short connector ("of", "and") — never distinctive
        if not any(part.startswith(t) or t.startswith(part) for t in tokens):
            return True  # a distinctive label part the name never carried
    return False


def _root_url(url: str) -> str:
    """Reduce a URL to scheme://host (drop path/query/fragment)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return url


def select_website_from_serp(
    name1: str,
    results: list[SearchResult],
    record_type: str | None = None,
) -> WebsiteResolution:
    """Pick the best official-website candidate from ranked SERP results.

    Confidence rules:
      * Research institution: first non-blacklisted, name-overlapping hit;
        ``high`` if the URL is on ``.edu``/``.gov``/``.org``, else ``low``.
      * Company / unknown: among non-blacklisted, name-overlapping hits,
        prefer the host whose label matches name1 WITHOUT introducing a
        foreign brand word — ``siemens.com`` beats ``siemens-healthineers.com``
        for "Siemens AG" even when the subsidiary ranks higher. ``high`` when
        the chosen host cleanly contains a name token; ``low`` when only a
        sub-brand / weak host is available.
      * ``none`` — no usable candidate.
    """
    valid = [
        sr for sr in results
        if sr.url and _URL_RE.match(sr.url)
        and not _is_blacklisted(sr.url)
        and _name_overlap(name1, sr)
    ]
    if not valid:
        return WebsiteResolution()

    if record_type == "research_institution":
        sr = valid[0]
        high = _tld(sr.url) in _OFFICIAL_TLDS
        return WebsiteResolution(
            url=_root_url(sr.url),
            confidence="high" if high else "low",
            source="serp",
        )

    # Company / unknown: rank so a clean root-domain match wins over a
    # subsidiary domain that merely contains the name token.
    #   2 = name token in host AND no foreign brand word (clean match)
    #   1 = name token in host but the label adds a foreign brand (sub-brand)
    #   0 = name only overlaps the title, not the host
    def _rank(sr: SearchResult) -> int:
        if not _name_in_domain(name1, sr.url):
            return 0
        return 1 if _domain_introduces_foreign_brand(name1, sr.url) else 2

    best = max(valid, key=_rank)  # first max preserves SERP order on ties
    best_rank = _rank(best)
    if best_rank == 0:
        # No candidate has a name token in its HOST — the overlap is only a
        # word in the title/snippet (a neighbour business, a listings page).
        # Too weak to trust as the official site: return nothing and let
        # Path C (LLM) try, rather than emit a stranger's domain like
        # "universitysurgical.com" for "Sign A Rama USA".
        return WebsiteResolution()
    confidence = "high" if best_rank == 2 else "low"
    return WebsiteResolution(url=_root_url(best.url), confidence=confidence, source="serp")


def _build_serp_query(
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    record_type: str | None,
) -> str:
    """Construct the website-search query for *name1*.

    Research institutions: the bare ``"name" official website`` form
    works well — the SERP ranker already surfaces the institution's
    homepage as the top hit, and the post-filter promotes
    ``.edu``/``.gov``/``.org`` results to high confidence.

    Companies / unknown: include city/state to disambiguate common
    company names ("Smith Industries" exists in many cities), and
    fall back on country only when neither is available.
    """
    base = f'"{name1}" official website'
    if record_type == "research_institution":
        if country and country.strip():
            return f"{base} {country.strip()}"
        return base
    # company / unknown
    geo = " ".join(p.strip() for p in (city, state) if p and p.strip())
    if geo:
        return f"{base} {geo}"
    if country and country.strip():
        return f"{base} {country.strip()}"
    return base


async def resolve_website_via_serp(
    record_id: str,
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    record_type: str | None,
    search_client: SearchClient,
    cache: BatchCache,
    *,
    prefetched_results: list[SearchResult] | None = None,
) -> WebsiteResolution:
    """Path B: find the official site for *name1* via SERP.

    Runs for any record type. Confidence is decided by
    :func:`select_website_from_serp` based on the candidate URL's TLD
    and name overlap.

    If *prefetched_results* is provided (the orchestrator already ran a
    Tier 2B search for the same record), they are reused directly — no
    additional SERP call is made.
    """
    if not name1 or not name1.strip():
        return WebsiteResolution()

    if prefetched_results is not None:
        chosen = select_website_from_serp(name1, prefetched_results, record_type)
        logger.info(
            "[%s] website Path B (reused SERP): url=%s confidence=%s",
            record_id, chosen.url, chosen.confidence,
        )
        return chosen

    query = _build_serp_query(name1, city, state, country, record_type)
    cached = cache.get_serp(query)
    if cached is not None:
        results = cached
    else:
        try:
            results = await search_client.search(query, num_results=5)
        except Exception as exc:
            logger.info(
                "[%s] website Path B: SERP call failed: %s",
                record_id, exc,
            )
            return WebsiteResolution()
        cache.set_serp(query, results)

    chosen = select_website_from_serp(name1, results, record_type)
    logger.info(
        "[%s] website Path B: query=%r url=%s confidence=%s",
        record_id, query[:80], chosen.url, chosen.confidence,
    )
    return chosen


# ---------------------------------------------------------------------------
# Path C — LLM inference (Path B fallback)
# ---------------------------------------------------------------------------

def _looks_like_url(value: str | None) -> bool:
    """Cheap shape check — must start with http(s):// and have a host."""
    if not value or not isinstance(value, str):
        return False
    if not _URL_RE.match(value.strip()):
        return False
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    return bool(parsed.netloc and "." in parsed.netloc)


async def infer_website_via_llm(
    record_id: str,
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    llm_client: OpenAIClient,
) -> WebsiteResolution:
    """Path C: ask the LLM for an organisation's official website.

    Result is always returned as ``confidence='low'`` when a URL is
    produced — the orchestrator writes it to ``website_url`` and flags
    the record for manual review. Path C runs as the Path B fallback
    for any record type, including research institutions.
    """
    if not name1 or not name1.strip():
        return WebsiteResolution()

    user_prompt = WEBSITE_INFERENCE_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        city=city or "(unknown)",
        state=state or "(unknown)",
        country=country or "(unknown)",
    )
    try:
        payload = await llm_client.extract_json(
            WEBSITE_INFERENCE_SYSTEM_PROMPT, user_prompt,
        )
    except Exception as exc:
        logger.info(
            "[%s] website Path C: LLM call failed: %s", record_id, exc,
        )
        return WebsiteResolution()

    raw = payload.get("website_url") if isinstance(payload, dict) else None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.lower() in {"", "null", "none", "unknown", "n/a", "na"}:
            raw = None

    if not _looks_like_url(raw):
        logger.info(
            "[%s] website Path C: LLM returned no usable URL for %r",
            record_id, name1[:60],
        )
        return WebsiteResolution()

    logger.info(
        "[%s] website Path C: LLM proposed %s for %r",
        record_id, raw, name1[:60],
    )
    return WebsiteResolution(url=raw, confidence="low", source="llm")
