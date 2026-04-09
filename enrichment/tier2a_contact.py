"""Tier 2A: Contact person lookup on institution website.

Supports two modes:
  MODE A (population) — name2 is null, find the department from contact's page
  MODE B (verification) — name2 exists, verify/correct it against the page
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz

from config import Settings
from llm.openai_client import OpenAIClient
from llm.prompts import TIER2A_SYSTEM_PROMPT, TIER2A_USER_PROMPT_TEMPLATE
from search.base import SearchClient, SearchResult
from search.page_fetcher import PageFetcher
from utils.cache import BatchCache
from utils.text_utils import is_blank, score_search_result

logger = logging.getLogger(__name__)


@dataclass
class Tier2AResult:
    """Outcome of a Tier 2A contact lookup."""
    success: bool = False
    name2_enriched: str | None = None
    name3_enriched: str | None = None
    title: str | None = None
    mode: str | None = None  # "2A_population" or "2A_verification"
    name2_match: str = "not_applicable"  # exact|partial|no_match|unknown
    name2_match_score: float = 0.0
    confidence: str = "none"  # high|medium|low
    source_url: str | None = None
    flag_for_review: bool = True
    flag_reason: str | None = None
    enrichment_status: str = "failed"
    source: str = "none"


async def run_tier2a(
    record_id: str,
    contact: str,
    institution: str,
    domain: str | None,
    name2: str | None,
    name3: str | None,
    search_client: SearchClient,
    page_fetcher: PageFetcher,
    llm_client: OpenAIClient,
    cache: BatchCache,
    settings: Settings,
) -> Tier2AResult:
    """Execute Tier 2A contact person lookup.

    Decision points:
    - name2 is blank → Mode A (population): search for contact, extract dept
    - name2 exists → Mode B (verification): verify/correct name2 via contact page
    """
    mode = "2A_population" if is_blank(name2) else "2A_verification"
    logger.info("[%s] Tier 2A starting in %s mode", record_id, mode)

    result = Tier2AResult(mode=mode)

    # Step 1 — Build SERP queries
    queries = _build_queries(contact, institution, domain)

    # Step 2 — Search and filter for people pages
    candidates = await _search_and_rank(queries, search_client, cache)
    if not candidates:
        logger.info("[%s] Tier 2A: no candidate pages found", record_id)
        return result

    # Step 3 — Fetch pages and extract with LLM (try top 3)
    for candidate in candidates[:3]:
        page_text = await page_fetcher.fetch_page_text(candidate.url)
        if not page_text:
            logger.info("[%s] Tier 2A: page fetch failed for %s", record_id, candidate.url[:80])
            continue

        # Step 4 — LLM extraction
        try:
            extraction = await _extract_affiliation(
                llm_client, contact, institution,
                name2 or "not recorded",
                name3 or "not recorded",
                page_text,
            )
        except Exception:
            logger.exception("[%s] Tier 2A: LLM extraction failed", record_id)
            continue

        # Step 5 — Apply extraction results
        if not extraction.get("person_found", False):
            logger.info("[%s] Tier 2A: person not found on page %s", record_id, candidate.url[:80])
            continue

        llm_confidence = extraction.get("confidence", "low")
        if llm_confidence == "low":
            logger.info("[%s] Tier 2A: LLM confidence too low", record_id)
            continue

        # Person found with acceptable confidence — apply Mode A or B logic
        result.source_url = candidate.url
        result.confidence = llm_confidence
        official_dept = extraction.get("official_dept")
        official_group = extraction.get("official_group")
        result.title = extraction.get("title")

        if mode == "2A_population":
            # MODE A: populate name2 from discovered department
            result = _apply_mode_a(result, official_dept, official_group, llm_confidence)
        else:
            # MODE B: verify/correct existing name2
            result = _apply_mode_b(
                result, name2, official_dept, official_group,
                extraction, settings.fuzzy_match_threshold,
            )

        if result.success:
            logger.info(
                "[%s] Tier 2A success: name2='%s', name3='%s', match=%s",
                record_id,
                result.name2_enriched,
                result.name3_enriched,
                result.name2_match,
            )
            return result

    logger.info("[%s] Tier 2A: all candidates exhausted, falling through", record_id)
    return result


def _build_queries(contact: str, institution: str, domain: str | None) -> list[str]:
    """Build primary and fallback SERP queries for contact lookup."""
    queries = []
    if domain:
        queries.append(f'"{contact}" "{institution}" site:{domain}')
    queries.append(f'"{contact}" "{institution}" faculty staff profile')
    return queries


async def _search_and_rank(
    queries: list[str],
    search_client: SearchClient,
    cache: BatchCache,
) -> list[SearchResult]:
    """Execute SERP queries and rank results by people-page signals."""
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

    # Score and sort by relevance to people/faculty pages
    scored = [(score_search_result(r.url, r.snippet), r) for r in all_results]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:3]]


async def _extract_affiliation(
    llm_client: OpenAIClient,
    contact: str,
    institution: str,
    name2: str,
    name3: str,
    page_text: str,
) -> dict:
    """Use OpenAI to extract person affiliation from page text."""
    user_prompt = TIER2A_USER_PROMPT_TEMPLATE.format(
        contact=contact,
        institution=institution,
        name2=name2,
        name3=name3,
        page_text=page_text,
    )
    return await llm_client.extract_json(TIER2A_SYSTEM_PROMPT, user_prompt)


def _apply_mode_a(
    result: Tier2AResult,
    official_dept: str | None,
    official_group: str | None,
    llm_confidence: str,
) -> Tier2AResult:
    """Mode A: name2 was null — populate from discovered department.

    Success: name2_enriched = official_dept, name3_enriched = official_group (bonus)
    Flag for review if confidence is medium.
    """
    if not official_dept or not official_dept.strip():
        return result  # No department found, stay unsuccessful

    result.success = True
    # FIX(Bug 5): never assign empty string to enriched fields
    result.name2_enriched = official_dept.strip() if official_dept and official_dept.strip() else None
    result.name3_enriched = official_group.strip() if official_group and official_group.strip() else None
    result.source = "contact_lookup_found"
    result.name2_match = "not_applicable"  # No pre-existing name2 to compare

    if llm_confidence == "high":
        result.flag_for_review = False
        result.flag_reason = None
        result.enrichment_status = "enriched"
    else:  # medium
        result.flag_for_review = True
        result.flag_reason = "Medium confidence — recommend review"
        result.enrichment_status = "enriched"

    return result


def _apply_mode_b(
    result: Tier2AResult,
    existing_name2: str | None,
    official_dept: str | None,
    official_group: str | None,
    extraction: dict,
    fuzzy_threshold: int,
) -> Tier2AResult:
    """Mode B: name2 exists — verify or correct via contact page.

    Uses rapidfuzz to compare existing name2 against official_dept from the page.
    ≥ 80 exact/partial: normalise to official format
    < 80: name2 is wrong, replace with page version
    """
    if not official_dept or not official_dept.strip():
        return result

    result.success = True

    # Use LLM-provided match info as primary signal
    llm_match = extraction.get("name2_match", "unknown")
    llm_score = extraction.get("name2_match_score", 0)

    # Also run our own fuzzy match for consistency
    if existing_name2:
        our_score = fuzz.token_sort_ratio(existing_name2, official_dept)
    else:
        our_score = 0.0

    # Prefer the higher score between LLM and our own fuzzy matching
    effective_score = max(float(llm_score), our_score)

    if effective_score >= fuzzy_threshold:
        # FIX(Bug 5): guard against empty string
        result.name2_enriched = official_dept.strip() if official_dept and official_dept.strip() else None
        result.name2_match_score = effective_score

        if effective_score >= 95:
            # Near-exact match
            result.name2_match = "exact"
            result.enrichment_status = "verified"
            result.flag_for_review = False
            result.flag_reason = None
            result.source = "contact_lookup_found"
        else:
            # Partial match — normalise but flag
            result.name2_match = "partial"
            result.enrichment_status = "enriched"
            result.flag_for_review = True
            result.flag_reason = "Partial match — confirm enriched Name 2"
            result.source = "contact_lookup_found"
    else:
        # No match — name2 is wrong, replace with page version
        # FIX(Bug 5): guard against empty string
        result.name2_enriched = official_dept.strip() if official_dept and official_dept.strip() else None
        result.name2_match = "no_match"
        result.name2_match_score = effective_score
        result.enrichment_status = "enriched"
        result.flag_for_review = True
        result.flag_reason = "Name 2 corrected — did not match contact page affiliation"
        result.source = "contact_lookup_corrected"

    # FIX(Bug 5): guard against empty string for name3
    result.name3_enriched = official_group.strip() if official_group and official_group.strip() else None
    return result
