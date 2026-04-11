"""Tier 2B: Department/division search via SERP + LLM extraction.

Used when Tier 2A is not applicable (no contact, or person not found),
for companies (which skip 2A entirely), or when name2 is already filled
and needs normalization against the institution's official source.

Strictly no hardcoded department name patterns: official naming is
determined by the LLM reading pages fetched from the institution's
official domain. If no official-domain extraction succeeds, Tier2B
returns failure and the pipeline falls through to Tier3 (LLM inference).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz

from config import Settings
from llm.openai_client import OpenAIClient
from llm.prompts import TIER2B_SYSTEM_PROMPT, TIER2B_USER_PROMPT_TEMPLATE
from search.base import SearchClient, SearchResult
from search.page_fetcher import PageContent, PageFetcher
from utils.cache import BatchCache
from utils.text_utils import expand_abbreviations, is_blank

logger = logging.getLogger(__name__)


@dataclass
class Tier2BResult:
    """Outcome of a Tier 2B department search."""
    success: bool = False
    name2_enriched: str | None = None
    name2_match: str = "not_applicable"
    name2_match_score: float = 0.0
    confidence: str = "none"
    source_url: str | None = None
    flag_for_review: bool = True
    flag_reason: str | None = None
    enrichment_status: str = "failed"
    source: str = "none"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_tier2b(
    record_id: str,
    name1: str,
    name2: str | None,
    record_type: str,
    city: str | None,
    state: str | None,
    domain: str | None,
    search_client: SearchClient,
    page_fetcher: PageFetcher,
    llm_client: OpenAIClient,
    cache: BatchCache,
    settings: Settings,
) -> Tier2BResult:
    """Execute Tier 2B department search.

    Strategy (official-sources only):
    1. Build SERP queries biased toward the institution's official domain.
    2. Rank results — on-domain results first.
    3. For each candidate page: fetch → LLM extracts official_name.
    4. Accept only extractions from the official domain (confidence=medium)
       or clearly identified on other authoritative pages (confidence=low).
    """
    logger.info("[%s] Tier 2B starting for '%s' (%s)", record_id, name1[:40], record_type)
    result = Tier2BResult()

    queries = _build_queries(name1, name2, record_type, city, state, domain)
    candidates = await _search_and_rank(queries, domain, search_client, cache)

    if not candidates:
        logger.info("[%s] Tier 2B: no candidates found", record_id)
        return result

    # Collect successful extractions from ALL top-3 candidates, then pick
    # the best one deterministically. This prevents the answer from
    # flipping between runs based on which page the fetcher happened to
    # return first.
    extractions: list[dict] = []
    for candidate in candidates[:3]:
        page_content = await page_fetcher.fetch_page_content(candidate.url)
        if page_content is None or page_content.is_empty():
            continue

        try:
            extraction = await _extract_department(
                llm_client, name1, page_content,
            )
        except Exception as exc:
            logger.info("[%s] Tier 2B: LLM extraction error: %s", record_id, exc)
            continue

        if not extraction:
            continue

        official_name = extraction.get("official_name")
        if not official_name or not str(official_name).strip():
            continue

        extractions.append({
            "official_name": str(official_name).strip(),
            "url": candidate.url,
            "on_domain": bool(domain and domain in candidate.url.lower()),
            "raw": extraction,
        })

    if not extractions:
        logger.info("[%s] Tier 2B: no valid LLM extraction from any candidate", record_id)
        return result

    def _rank(item: dict) -> tuple:
        """Deterministic ranking: on-domain first, then closeness to
        input name2 (if provided), then longer canonical form, then
        alphabetical for a final stable tiebreaker."""
        on_domain = 1 if item["on_domain"] else 0
        official = item["official_name"]
        if name2 and name2.strip():
            name_score = fuzz.token_sort_ratio(name2.lower(), official.lower())
        else:
            name_score = 0
        return (on_domain, name_score, len(official), official.lower())

    extractions.sort(key=_rank, reverse=True)
    best = extractions[0]
    official_name = best["official_name"]
    chosen_url = best["url"]
    url_on_official_domain = best["on_domain"]
    extraction = best["raw"]

    result.success = True
    result.name2_enriched = official_name
    result.source_url = chosen_url
    result.source = "dept_search"

    if extraction.get("name2_match"):
        result.name2_match = extraction.get("name2_match", "unknown")
        try:
            result.name2_match_score = float(extraction.get("name2_match_score", 0))
        except (TypeError, ValueError):
            result.name2_match_score = 0.0
    elif name2:
        score = fuzz.token_sort_ratio(name2.lower(), official_name.lower())
        result.name2_match = (
            "exact" if score >= 90 else "partial" if score >= 60 else "no_match"
        )
        result.name2_match_score = score

    if url_on_official_domain:
        result.confidence = "medium"
        result.flag_reason = "Extracted by LLM from official domain page"
    else:
        result.confidence = "low"
        result.flag_reason = "Extracted by LLM from non-official source"
    result.flag_for_review = True
    result.enrichment_status = "enriched"

    logger.info(
        "[%s] Tier 2B success: name2='%s' (from %d candidates), confidence=%s, url=%s",
        record_id, official_name, len(extractions), result.confidence, chosen_url[:80],
    )
    return result


# ---------------------------------------------------------------------------
# SERP query building and ranking
# ---------------------------------------------------------------------------

def _build_queries(
    name1: str,
    name2: str | None,
    record_type: str,
    city: str | None,
    state: str | None,
    domain: str | None,
) -> list[str]:
    """Build primary and fallback SERP queries for department search.

    Abbreviations in name2 are expanded ("Dept" → "Department") so that
    exact-quoted searches match actual website text.
    """
    queries: list[str] = []
    city_state = " ".join(filter(None, [city, state]))

    name2_expanded = expand_abbreviations(name2) if not is_blank(name2) else None
    name2_quoted = f'"{name2_expanded}"' if name2_expanded else ""

    if record_type == "research_institution":
        primary = f'"{name1}" {name2_quoted}'.strip()
        if domain:
            primary += f" site:{domain}"
        queries.append(primary)

        queries.append(f'"{name1}" {name2_quoted} {city_state}'.strip())

        if not is_blank(name2):
            queries.append(f'"{name1}" {name2_expanded or name2} {city or ""}'.strip())
    else:
        queries.append(f'"{name1}" {name2_quoted} division {city_state}'.strip())
        queries.append(f'"{name1}" {name2_expanded or name2 or ""} official'.strip())

    return queries


async def _search_and_rank(
    queries: list[str],
    domain: str | None,
    search_client: SearchClient,
    cache: BatchCache,
) -> list[SearchResult]:
    """Execute queries and rank results, preferring the official domain."""
    all_results: list[SearchResult] = []
    seen_urls: set[str] = set()

    for query in queries:
        cached = cache.get_serp(query)
        if cached is not None:
            results = cached
        else:
            results = await search_client.search(query, num_results=5)
            cache.set_serp(query, results)

        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                all_results.append(r)

    def _score(sr: SearchResult) -> int:
        return 1 if domain and domain in sr.url.lower() else 0

    scored = [(_score(r), r) for r in all_results]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

async def _extract_department(
    llm_client: OpenAIClient,
    name1: str,
    page_content: "PageContent",
) -> dict:
    """Use OpenAI to extract the official unit name from structured
    authoritative page elements only (title, h1, breadcrumb, URL path).

    The input name2 is intentionally NOT passed to the LLM — we don't
    want the model to echo the user's abbreviated input when the page
    itself uses the canonical form.
    """
    user_prompt = TIER2B_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        url_path=page_content.url_path or "(none)",
        page_title=page_content.page_title or "(none)",
        h1=page_content.h1 or "(none)",
        breadcrumb=page_content.breadcrumb or "(none)",
    )
    return await llm_client.extract_json(TIER2B_SYSTEM_PROMPT, user_prompt)
