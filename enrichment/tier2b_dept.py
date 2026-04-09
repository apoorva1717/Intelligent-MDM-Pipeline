"""Tier 2B: Department/division search via SERP + LLM extraction.

Used when Tier 2A is not applicable (no contact, or person not found)
or for companies (which skip 2A entirely).

Includes a regex-based extraction fallback so departments can still be
found when the LLM is unavailable (e.g. quota exhausted).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from config import Settings
from llm.openai_client import OpenAIClient
from llm.prompts import TIER2B_SYSTEM_PROMPT, TIER2B_USER_PROMPT_TEMPLATE
from search.base import SearchClient, SearchResult
from search.page_fetcher import PageFetcher
from utils.cache import BatchCache
from utils.text_utils import expand_abbreviations, is_blank

logger = logging.getLogger(__name__)

# Regex patterns for common academic/corporate unit names.
# Two groups: PREFIX patterns ("Department of X") and SUFFIX patterns ("X Facility").
_DEPT_PATTERNS: list[re.Pattern[str]] = [
    # ── Prefix patterns: "Keyword of/for X" ───────────────────────────
    re.compile(
        r"(?:Department|Dept\.?)\s+of\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?",
        re.IGNORECASE,
    ),
    re.compile(r"School\s+of\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?", re.IGNORECASE),
    re.compile(r"Division\s+of\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?", re.IGNORECASE),
    re.compile(r"College\s+of\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?", re.IGNORECASE),
    re.compile(
        r"Institute\s+(?:of|for)\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"Cent(?:er|re)\s+for\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?",
        re.IGNORECASE,
    ),
    re.compile(r"Faculty\s+of\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?", re.IGNORECASE),
    re.compile(
        r"Lab(?:oratory)?\s+(?:of|for)\s+[\w &,\-/]+(?:\s+and\s+[\w &,\-/]+)?",
        re.IGNORECASE,
    ),

    # ── Suffix patterns: "X Facility", "X Lab", "X Center" ───────────
    # These catch names like "NMR Facility", "Biomolecular NMR Lab",
    # "Advanced Photon Source", "Proteomics Core", etc.
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Facilit(?:y|ies)", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Lab(?:oratory)?", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Cent(?:er|re)", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Unit\b", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Core\b", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Program\b", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Group\b", re.IGNORECASE),
    re.compile(r"[\w\-]+(?:\s+[\w\-]+){0,4}\s+Section\b", re.IGNORECASE),
]


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

    Strategy:
    1. Build SERP queries, run searches, rank results.
    2. Quick-check: try to extract department from search *titles/snippets*
       (no page fetch or LLM needed).
    3. Full path: fetch page → LLM extraction → regex fallback if LLM fails.
    """
    logger.info("[%s] Tier 2B starting for '%s' (%s)", record_id, name1[:40], record_type)
    result = Tier2BResult()

    queries = _build_queries(name1, name2, record_type, city, state, domain)
    candidates = await _search_and_rank(queries, name2, domain, search_client, cache)

    if not candidates:
        logger.info("[%s] Tier 2B: no candidates found", record_id)
        return result

    # ── Fast path: extract from search snippet/title (no LLM) ──────────
    snippet_hit = _extract_from_snippets(candidates, name1, name2)
    if snippet_hit:
        dept_name, source_url, match_info = snippet_hit
        result.success = True
        result.name2_enriched = dept_name
        result.source_url = source_url
        result.source = "dept_search"
        result.name2_match = match_info
        result.confidence = "low"
        result.flag_for_review = True
        result.flag_reason = "Extracted from search snippet — verify"
        result.enrichment_status = "enriched"

        logger.info(
            "[%s] Tier 2B snippet hit: name2='%s', url=%s",
            record_id, dept_name, source_url[:80] if source_url else "?",
        )
        return result

    # ── Full path: fetch pages → LLM → regex fallback ─────────────────
    for candidate in candidates[:3]:
        page_text = await page_fetcher.fetch_page_text(candidate.url)
        if not page_text:
            continue

        # Try LLM extraction first
        try:
            extraction = await _extract_department(
                llm_client, name1, name2 or "not recorded", page_text,
            )
        except Exception:
            logger.info("[%s] Tier 2B: LLM extraction failed, trying regex", record_id)
            extraction = None

        official_name: str | None = None

        if extraction:
            official_name = extraction.get("official_name")

        # Regex fallback when LLM failed or returned nothing
        if not official_name or not official_name.strip():
            regex_hit = _extract_department_from_text(name1, name2, page_text)
            if regex_hit:
                official_name = regex_hit
                logger.info(
                    "[%s] Tier 2B regex fallback: found '%s'",
                    record_id, official_name,
                )

        if not official_name or not official_name.strip():
            continue

        result.success = True
        result.name2_enriched = official_name.strip()
        result.source_url = candidate.url
        result.source = "dept_search"

        if extraction and extraction.get("name2_match"):
            result.name2_match = extraction.get("name2_match", "unknown")
            result.name2_match_score = float(extraction.get("name2_match_score", 0))
        elif name2:
            score = fuzz.token_sort_ratio(name2.lower(), official_name.strip().lower())
            result.name2_match = "exact" if score >= 90 else "partial" if score >= 60 else "no_match"
            result.name2_match_score = score

        url_on_official_domain = domain and domain in candidate.url.lower()
        if url_on_official_domain:
            result.confidence = "medium"
            result.flag_for_review = True
            result.flag_reason = "Department search — verify against official records"
            result.enrichment_status = "enriched"
        else:
            result.confidence = "low"
            result.flag_for_review = True
            result.flag_reason = "Non-official source"
            result.enrichment_status = "enriched"

        logger.info(
            "[%s] Tier 2B success: name2='%s', confidence=%s, url=%s",
            record_id, official_name, result.confidence, candidate.url[:80],
        )
        return result

    logger.info("[%s] Tier 2B: no valid extraction from any candidate", record_id)
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

    # Expand abbreviations: "Dept of Radiology" → "Department of Radiology"
    name2_expanded = expand_abbreviations(name2) if not is_blank(name2) else None
    name2_quoted = f'"{name2_expanded}"' if name2_expanded else ""

    if record_type == "research_institution":
        # Primary: institution + expanded name2 on official domain
        primary = f'"{name1}" {name2_quoted}'.strip()
        if domain:
            primary += f" site:{domain}"
        queries.append(primary)

        # Broader: without site restriction
        queries.append(f'"{name1}" {name2_quoted} {city_state}'.strip())

        # Unquoted name2 fallback — lets the search engine fuzzy-match
        if not is_blank(name2):
            queries.append(f'"{name1}" {name2_expanded or name2} {city or ""}'.strip())

        # Direct facility/lab/center query
        if not is_blank(name2):
            queries.append(f'"{name1}" {name2_expanded or name2} facility lab center'.strip())
    else:
        queries.append(f'"{name1}" {name2_quoted} division {city_state}'.strip())
        queries.append(f'"{name1}" {name2_expanded or name2 or ""} official'.strip())

    return queries


async def _search_and_rank(
    queries: list[str],
    name2: str | None,
    domain: str | None,
    search_client: SearchClient,
    cache: BatchCache,
) -> list[SearchResult]:
    """Execute queries and score results by relevance signals."""
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
        s = 0
        url_lower = sr.url.lower()
        if domain and domain in url_lower:
            s += 2
        for signal in ("people", "faculty", "directory", "department", "staff"):
            if signal in url_lower:
                s += 1
                break
        if not is_blank(name2) and name2.lower() in sr.snippet.lower():
            s += 1
        return s

    scored = [((_score(r), r)) for r in all_results]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_NOISE_STARTS = {"the", "a", "an", "our", "this", "that", "its", "is", "in", "at", "to", "on"}


def _extract_all_dept_names(text: str) -> list[str]:
    """Find all department-like names in *text* using regex patterns."""
    hits: list[str] = []
    seen_lower: set[str] = set()
    for pattern in _DEPT_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(0).strip().rstrip(",;.:")
            # Drop false positives where the match starts with a stop word
            first_word = name.split()[0].lower() if name else ""
            if first_word in _NOISE_STARTS:
                name = name[len(first_word):].strip()
            if name.lower() not in seen_lower and len(name) > 5:
                seen_lower.add(name.lower())
                hits.append(name)
    return hits


def _extract_from_snippets(
    candidates: list[SearchResult],
    name1: str,
    name2: str | None,
) -> tuple[str, str | None, str] | None:
    """Try to extract a department name directly from search titles/snippets.

    Returns ``(dept_name, source_url, match_type)`` or None.
    """
    for sr in candidates[:5]:
        combined = f"{sr.title} {sr.snippet}"
        dept_names = _extract_all_dept_names(combined)
        if not dept_names:
            continue

        if name2 and name2.strip():
            best_name = None
            best_score = 0.0
            name2_lower = name2.lower()
            for dn in dept_names:
                dn_lower = dn.lower()
                score = max(
                    fuzz.token_sort_ratio(name2_lower, dn_lower),
                    fuzz.partial_ratio(name2_lower, dn_lower),
                )
                if score > best_score:
                    best_score = score
                    best_name = dn
            if best_name and best_score >= 60:
                match_type = "exact" if best_score >= 90 else "partial"
                return (best_name, sr.url, match_type)
        else:
            # No name2 to compare — return the first dept found near name1
            for dn in dept_names:
                if name1.lower() in combined.lower():
                    return (dn, sr.url, "not_applicable")

    return None


def _extract_department_from_text(
    name1: str,
    name2: str | None,
    page_text: str,
) -> str | None:
    """Regex-based department extraction from page text (no LLM needed).

    Finds all department-like patterns, then picks the one that best
    matches name2 (if provided) or the first one associated with name1.
    """
    dept_names = _extract_all_dept_names(page_text)
    if not dept_names:
        return None

    if name2 and name2.strip():
        best_name: str | None = None
        best_score = 0.0
        name2_lower = name2.lower()
        for dn in dept_names:
            dn_lower = dn.lower()
            # Use the higher of token_sort and partial_ratio so abbreviated
            # names like "Chemistry Dept" still match "Department of Chemistry".
            score = max(
                fuzz.token_sort_ratio(name2_lower, dn_lower),
                fuzz.partial_ratio(name2_lower, dn_lower),
            )
            if score > best_score:
                best_score = score
                best_name = dn
        if best_name and best_score >= 55:
            return best_name
        return None

    # No name2 — return the first dept that appears near name1 in the text
    name1_lower = name1.lower()
    text_lower = page_text.lower()
    name1_pos = text_lower.find(name1_lower)
    if name1_pos == -1:
        return dept_names[0] if dept_names else None

    # Pick the dept name closest to name1 in the text
    best_dept: str | None = None
    best_dist = float("inf")
    for dn in dept_names:
        dn_pos = text_lower.find(dn.lower())
        if dn_pos != -1:
            dist = abs(dn_pos - name1_pos)
            if dist < best_dist:
                best_dist = dist
                best_dept = dn
    return best_dept


async def _extract_department(
    llm_client: OpenAIClient,
    name1: str,
    name2: str,
    page_text: str,
) -> dict:
    """Use OpenAI to extract official department name from page."""
    user_prompt = TIER2B_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        name2=name2,
        page_text=page_text,
    )
    return await llm_client.extract_json(TIER2B_SYSTEM_PROMPT, user_prompt)
