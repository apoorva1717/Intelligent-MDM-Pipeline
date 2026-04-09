"""Orchestrator: tier escalation, record_type derivation, and result assembly.

This is the main entry point for enrichment logic.  It instantiates all
clients (or mock clients when MOCK_EXTERNAL_CALLS=true), manages the
per-batch cache, and coordinates asyncio concurrency.

ROR lookup uses a hybrid strategy: ``?affiliation=`` first (handles
abbreviations like "Univ of Florida"), then ``?query=`` with a country
filter as fallback.  Child matching is done locally against the parent's
relationships list using rapidfuzz, saving a second API call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rapidfuzz import fuzz

from api.models import (
    EnrichmentOptions,
    EnrichmentRecord,
    EnrichmentResponse,
    EnrichmentResult,
    EnrichmentSummary,
)
from config import Settings
from enrichment.tier1_ror import RORClient, clear_ror_cache
from enrichment.tier2a_contact import Tier2AResult, run_tier2a
from enrichment.tier2b_dept import Tier2BResult, run_tier2b
from enrichment.tier3_llm import Tier3Result, run_tier3
from llm.openai_client import OpenAIClient
from search.base import SearchClient
from search.duckduckgo_client import DuckDuckGoClient
from search.page_fetcher import PageFetcher
from search.serpapi_client import SerpAPIClient
from utils.cache import BatchCache
from utils.text_utils import country_to_iso_code, extract_domain, is_blank

logger = logging.getLogger(__name__)


# ── Result helpers ────────────────────────────────────────────────────────────

def _init_result(record: EnrichmentRecord) -> dict[str, Any]:
    """Create a blank result dict with originals populated."""
    return {
        "record_id": record.record_id,
        "name1_original": record.name1,
        "name2_original": record.name2,
        "name3_original": record.name3,
        "name1_enriched": None,
        "name2_enriched": None,
        "name3_enriched": None,
        "name1_changed": False,
        "name2_changed": False,
        "name3_changed": False,
        "record_type": "unknown",
        "tier_used": 1,
        "tier2_mode": None,
        "confidence": "none",
        "source": "none",
        "ror_id": None,
        "source_url": None,
        "contact_used": False,
        "name2_match_result": "not_applicable",
        "flag_for_review": False,
        "flag_reason": None,
        "enrichment_status": "failed",
        "duration_ms": 0,
        "error": None,
    }


def finalise(result: dict[str, Any], start: float) -> dict[str, Any]:
    """Apply empty-string guards and compute changed flags.

    FIX(Bug 5): enriched name fields must NEVER be empty string "".
    They must be a non-empty string or None.

    FIX(Bug 8): changed flags are True only when enriched is not None
    AND enriched differs from original.
    """
    for field in ("name1_enriched", "name2_enriched", "name3_enriched"):
        val = result.get(field)
        if val is not None and not str(val).strip():
            result[field] = None

    result["name1_changed"] = bool(
        result.get("name1_enriched")
        and result["name1_enriched"] != result.get("name1_original")
    )
    result["name2_changed"] = bool(
        result.get("name2_enriched")
        and result["name2_enriched"] != result.get("name2_original")
    )
    result["name3_changed"] = bool(
        result.get("name3_enriched")
        and result["name3_enriched"] != result.get("name3_original")
    )

    result["duration_ms"] = int((time.monotonic() - start) * 1000)
    return result


# ── Child matching helper ─────────────────────────────────────────────────────

_CHILD_MATCH_THRESHOLD = 70  # rapidfuzz token_sort_ratio minimum


def _match_child_locally(
    name2: str,
    children: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match *name2* against the parent org's children list using rapidfuzz.

    Returns the best-matching child dict (with added ``score`` key) if the
    score meets the threshold, otherwise None.  This avoids a second ROR API
    call for child matching.
    """
    if not children or not name2.strip():
        return None

    name2_lower = name2.strip().lower()
    best: dict[str, Any] | None = None
    best_score = 0.0

    for child in children:
        child_name = child.get("name", "")
        ratio = fuzz.token_sort_ratio(name2_lower, child_name.lower())
        if ratio > best_score:
            best_score = ratio
            best = child

    if best and best_score >= _CHILD_MATCH_THRESHOLD:
        return {**best, "score": best_score}
    return None


# ── Tier result application helpers ───────────────────────────────────────────

def _apply_tier2a(result: dict, tier2a: Tier2AResult, mode: str) -> None:
    """Transfer Tier 2A outcome into the result dict."""
    result["tier_used"] = 2
    result["tier2_mode"] = tier2a.mode
    result["contact_used"] = True
    result["source"] = tier2a.source
    result["source_url"] = tier2a.source_url
    result["confidence"] = tier2a.confidence
    result["flag_for_review"] = tier2a.flag_for_review
    result["flag_reason"] = tier2a.flag_reason
    result["enrichment_status"] = tier2a.enrichment_status
    result["name2_match_result"] = tier2a.name2_match

    if tier2a.name2_enriched and tier2a.name2_enriched.strip():
        result["name2_enriched"] = tier2a.name2_enriched.strip()
    if tier2a.name3_enriched and tier2a.name3_enriched.strip():
        result["name3_enriched"] = tier2a.name3_enriched.strip()


def _apply_tier2b(result: dict, tier2b: Tier2BResult) -> None:
    """Transfer Tier 2B outcome into the result dict."""
    result["tier_used"] = 2
    result["tier2_mode"] = "2B"
    result["source"] = tier2b.source
    result["source_url"] = tier2b.source_url
    result["confidence"] = tier2b.confidence
    result["flag_for_review"] = tier2b.flag_for_review
    result["flag_reason"] = tier2b.flag_reason
    result["enrichment_status"] = tier2b.enrichment_status
    result["name2_match_result"] = tier2b.name2_match

    if tier2b.name2_enriched and tier2b.name2_enriched.strip():
        result["name2_enriched"] = tier2b.name2_enriched.strip()


def _apply_tier3(result: dict, tier3: Tier3Result) -> None:
    """Transfer Tier 3 outcome into the result dict."""
    result["tier_used"] = 3
    result["source"] = "LLM"
    result["confidence"] = tier3.confidence
    result["flag_for_review"] = tier3.flag_for_review
    result["flag_reason"] = tier3.flag_reason
    result["enrichment_status"] = tier3.enrichment_status

    if tier3.success:
        if tier3.name1_suggestion and tier3.name1_suggestion.strip():
            result["name1_enriched"] = tier3.name1_suggestion.strip()
        if tier3.name2_suggestion and tier3.name2_suggestion.strip():
            result["name2_enriched"] = tier3.name2_suggestion.strip()
        if tier3.name3_suggestion and tier3.name3_suggestion.strip():
            result["name3_enriched"] = tier3.name3_suggestion.strip()


# ── Web search name1 resolution (Tier 1B) ─────────────────────────────────────

_RESEARCH_DOMAIN_PATTERNS = (
    ".edu", ".ac.", ".gov", ".gov.", ".ac.uk", ".ac.jp", ".edu.au",
    ".edu.br", ".ac.in", ".ac.nz", ".ac.za",
)


def _infer_record_type_from_domain(domain: str | None) -> str:
    """Heuristic: .edu / .ac.* / .gov → research_institution, else company."""
    if not domain:
        return "unknown"
    domain_lower = domain.lower()
    for pattern in _RESEARCH_DOMAIN_PATTERNS:
        if pattern in domain_lower:
            return "research_institution"
    return "company"


async def _web_resolve_name1(
    name1: str,
    city: str | None,
    state: str | None,
    search_client: SearchClient,
    cache: BatchCache,
) -> dict[str, Any]:
    """Use web search to resolve name1 when ROR has no match.

    Searches for the quoted org name, scores results by title similarity,
    and extracts the domain from the best-matching result.  Returns a dict
    with ``found``, ``domain``, ``source_url``, and ``record_type``.
    """
    location = " ".join(filter(None, [city, state]))
    queries = [f'"{name1}"']
    if location:
        queries.append(f'"{name1}" {location}')

    best_url: str | None = None
    best_score = 0.0

    for query in queries:
        cached = cache.get_serp(query)
        if cached is not None:
            results = cached
        else:
            results = await search_client.search(query, num_results=5)
            cache.set_serp(query, results)

        for sr in results:
            score = fuzz.partial_ratio(name1.lower(), sr.title.lower())
            if score > best_score:
                best_score = score
                best_url = sr.url

    if best_url and best_score >= 50:
        domain = extract_domain(best_url)
        return {
            "found": True,
            "domain": domain,
            "source_url": best_url,
            "record_type": _infer_record_type_from_domain(domain),
        }

    return {"found": False}


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:
    """Coordinates the multi-tier enrichment pipeline for a batch of records."""

    def __init__(self, settings: Settings, mock_clients: dict[str, Any] | None = None) -> None:
        self._settings = settings
        self._mock_clients = mock_clients

        if mock_clients:
            self._ror_client: RORClient = mock_clients.get("ror", RORClient(settings))
            self._search_client: SearchClient = mock_clients.get(
                "search", self._build_search_client(settings))
            self._page_fetcher: PageFetcher = mock_clients.get("page_fetcher", PageFetcher(
                timeout=settings.page_fetch_timeout_seconds,
                max_chars=settings.max_page_content_chars,
            ))
            self._llm_client: OpenAIClient = mock_clients.get("llm", OpenAIClient(settings))
        else:
            self._ror_client = RORClient(settings)
            self._search_client = self._build_search_client(settings)
            self._page_fetcher = PageFetcher(
                timeout=settings.page_fetch_timeout_seconds,
                max_chars=settings.max_page_content_chars,
            )
            self._llm_client = OpenAIClient(settings)

    @staticmethod
    def _build_search_client(settings: Settings) -> SearchClient:
        """Select SERP provider based on configuration."""
        key = (settings.serpapi_key or "").strip()
        if key:
            logger.info("Using SerpAPI search provider")
            return SerpAPIClient(key)
        logger.warning(
            "SERPAPI_KEY not set — using DuckDuckGo (lower quality results). "
            "Set SERPAPI_KEY in .env for reliable department search."
        )
        return DuckDuckGoClient()

    async def enrich_batch(
        self,
        records: list[EnrichmentRecord],
        options: EnrichmentOptions,
    ) -> EnrichmentResponse:
        """Process a batch of records with concurrency control."""
        batch_start = time.perf_counter()
        clear_ror_cache()  # fresh cache per batch to avoid stale failures
        cache = BatchCache()
        semaphore = asyncio.Semaphore(options.max_concurrency)

        async def _process_with_semaphore(record: EnrichmentRecord) -> EnrichmentResult:
            async with semaphore:
                return await self._enrich_single(record, options, cache)

        results = await asyncio.gather(
            *[_process_with_semaphore(r) for r in records],
            return_exceptions=True,
        )

        final_results: list[EnrichmentResult] = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(
                    "Unhandled exception for record %s: %s",
                    records[i].record_id, str(res),
                )
                final_results.append(EnrichmentResult(
                    record_id=records[i].record_id,
                    name1_original=records[i].name1,
                    name2_original=records[i].name2,
                    name3_original=records[i].name3,
                    enrichment_status="failed",
                    error=str(res),
                ))
            else:
                final_results.append(res)

        batch_ms = int((time.perf_counter() - batch_start) * 1000)
        summary = self._build_summary(final_results, batch_ms)

        logger.info(
            "Batch complete: %d records, %d enriched, %d failed in %dms",
            summary.total, summary.enriched, summary.failed, batch_ms,
        )

        return EnrichmentResponse(results=final_results, summary=summary)

    async def _enrich_single(
        self,
        record: EnrichmentRecord,
        options: EnrichmentOptions,
        cache: BatchCache,
    ) -> EnrichmentResult:
        """Run the full tier-escalation pipeline for one record."""
        result = _init_result(record)
        start = time.monotonic()

        try:
            if options.dry_run:
                result["enrichment_status"] = "unresolved"
                result = finalise(result, start)
                return EnrichmentResult(**result)

            institution_domain: str | None = None

            # ── TIER 1: ROR ──────────────────────────────────────────────

            if not is_blank(record.name1):
                # Resolve country to ISO alpha-2 for the ROR query filter
                country_code = country_to_iso_code(record.country)

                ror_parent = await self._ror_client.call(
                    record.name1.strip(),
                    country_code=country_code,
                    country=record.country,
                    city=record.city,
                    state=record.state,
                )

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier1_ror_parent",
                    "query": record.name1.strip(),
                    "country": record.country,
                    "country_filter": country_code,
                    "matched": ror_parent["matched"],
                    "score": ror_parent.get("score"),
                    "official_name": ror_parent.get("official_name"),
                    "is_research": ror_parent.get("is_research_institution"),
                    "domain": ror_parent.get("domain"),
                })

                if ror_parent["matched"]:
                    # Write name1 enrichment IMMEDIATELY so later tier
                    # failures don't lose it.
                    official = ror_parent.get("official_name")
                    if official and official.strip():
                        result["name1_enriched"] = official.strip()

                    result["ror_id"] = ror_parent["ror_id"]
                    result["tier_used"] = 1
                    result["source"] = "ROR"
                    result["confidence"] = "high"

                    result["record_type"] = (
                        "research_institution"
                        if ror_parent.get("is_research_institution")
                        else "company"
                    )
                    institution_domain = ror_parent.get("domain")

                    # ── Child match for name2 (local fuzzy against parent's children) ──
                    if record.name2 and record.name2.strip():
                        children = ror_parent.get("children", [])
                        child_match = _match_child_locally(
                            record.name2.strip(), children,
                        )

                        logger.info({
                            "record_id": record.record_id,
                            "step": "tier1_child_local_match",
                            "name2": record.name2,
                            "num_children": len(children),
                            "best_child": child_match.get("name") if child_match else None,
                            "best_score": child_match.get("score") if child_match else 0,
                        })

                        if child_match:
                            child_name = child_match["name"]
                            if child_name and child_name.strip():
                                result["name2_enriched"] = child_name.strip()
                            result["name2_match_result"] = (
                                "exact" if child_match["score"] >= 90 else "partial"
                            )
                            result["source"] = "ROR+child"
                            result["enrichment_status"] = "enriched"
                            result = finalise(result, start)
                            return EnrichmentResult(**result)

                else:
                    # ── TIER 1B: Web search fallback for name1 ────────────
                    web_res = await _web_resolve_name1(
                        record.name1.strip(),
                        record.city, record.state,
                        self._search_client, cache,
                    )

                    logger.info({
                        "record_id": record.record_id,
                        "step": "tier1b_web_resolve",
                        "found": web_res["found"],
                        "domain": web_res.get("domain"),
                        "record_type": web_res.get("record_type"),
                        "source_url": web_res.get("source_url"),
                    })

                    # Always pass through name1 — better than leaving it None
                    result["name1_enriched"] = record.name1.strip()
                    result["source"] = "web_search" if web_res["found"] else "passthrough"
                    result["confidence"] = "low"
                    result["tier_used"] = 1

                    if web_res["found"]:
                        institution_domain = web_res.get("domain")
                        result["source_url"] = web_res.get("source_url")
                        result["record_type"] = web_res.get("record_type", "unknown")
                    else:
                        result["record_type"] = "unknown"
                        institution_domain = None

            # ── TIER 2A: CONTACT LOOKUP ──────────────────────────────────

            can_do_2a = (
                result["record_type"] == "research_institution"
                and bool(record.contact and record.contact.strip())
                and bool(institution_domain)
            )

            logger.info({
                "record_id": record.record_id,
                "step": "tier2a_decision",
                "attempting": can_do_2a,
                "record_type": result["record_type"],
                "has_contact": bool(record.contact and record.contact.strip()),
                "has_domain": bool(institution_domain),
                "reason": (
                    "attempting" if can_do_2a
                    else f"skipped — record_type is {result['record_type']}"
                    if result["record_type"] != "research_institution"
                    else "skipped — no contact"
                    if not (record.contact and record.contact.strip())
                    else "skipped — no domain"
                ),
            })

            if can_do_2a:
                mode = "population" if not record.name2 else "verification"
                tier2a_result = await run_tier2a(
                    record_id=record.record_id,
                    contact=record.contact,  # type: ignore[arg-type]
                    institution=result["name1_enriched"] or record.name1 or "",
                    domain=institution_domain,
                    name2=record.name2,
                    name3=record.name3,
                    search_client=self._search_client,
                    page_fetcher=self._page_fetcher,
                    llm_client=self._llm_client,
                    cache=cache,
                    settings=self._settings,
                )

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier2a_result",
                    "found": tier2a_result.success,
                    "mode": mode,
                    "confidence": tier2a_result.confidence,
                    "name2_enriched": tier2a_result.name2_enriched,
                })

                if tier2a_result.success:
                    _apply_tier2a(result, tier2a_result, mode)
                    result = finalise(result, start)
                    return EnrichmentResult(**result)

            # ── TIER 2B: DEPT SEARCH ─────────────────────────────────────

            if not is_blank(record.name1):
                logger.info({
                    "record_id": record.record_id,
                    "step": "tier2b_start",
                    "name1": result["name1_enriched"] or record.name1,
                    "name2": record.name2,
                    "record_type": result["record_type"],
                })

                tier2b_result = await run_tier2b(
                    record_id=record.record_id,
                    name1=result["name1_enriched"] or record.name1 or "",
                    name2=record.name2,
                    record_type=result["record_type"],
                    city=record.city,
                    state=record.state,
                    domain=institution_domain,
                    search_client=self._search_client,
                    page_fetcher=self._page_fetcher,
                    llm_client=self._llm_client,
                    cache=cache,
                    settings=self._settings,
                )

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier2b_result",
                    "found": tier2b_result.success,
                    "confidence": tier2b_result.confidence,
                })

                if tier2b_result.success:
                    _apply_tier2b(result, tier2b_result)
                    result = finalise(result, start)
                    return EnrichmentResult(**result)

            # ── TIER 3: LLM INFERENCE ────────────────────────────────────

            logger.info({
                "record_id": record.record_id,
                "step": "tier3_start",
            })

            tier3_result: Tier3Result = await run_tier3(
                record_id=record.record_id,
                name1=record.name1,
                name2=record.name2,
                name3=record.name3,
                contact=record.contact,
                street=record.street,
                city=record.city,
                state=record.state,
                zip_code=record.zip,
                country=record.country,
                llm_client=self._llm_client,
            )

            _apply_tier3(result, tier3_result)

            # Last resort: if nothing enriched name1, pass through the original
            if not result.get("name1_enriched") and not is_blank(record.name1):
                result["name1_enriched"] = record.name1.strip()

            result = finalise(result, start)
            return EnrichmentResult(**result)

        except Exception as exc:
            logger.error({
                "record_id": record.record_id,
                "step": "orchestrator_error",
                "error": str(exc),
            })
            result["enrichment_status"] = "failed"
            result["error"] = str(exc)
            result = finalise(result, start)
            return EnrichmentResult(**result)

    @staticmethod
    def _build_summary(results: list[EnrichmentResult], batch_ms: int) -> EnrichmentSummary:
        """Aggregate individual results into batch-level summary statistics."""
        summary = EnrichmentSummary(
            total=len(results),
            processing_time_ms=batch_ms,
        )
        for r in results:
            if r.enrichment_status == "enriched":
                summary.enriched += 1
            elif r.enrichment_status == "verified":
                summary.verified += 1
            elif r.enrichment_status == "unresolved":
                summary.unresolved += 1
            else:
                summary.failed += 1

            if r.record_type == "research_institution":
                summary.research_institution_count += 1
            elif r.record_type == "company":
                summary.company_count += 1

            if r.tier_used == 1:
                summary.tier1_resolved += 1
            elif r.tier_used == 2:
                if r.tier2_mode == "2A_population":
                    summary.tier2a_population_count += 1
                elif r.tier2_mode == "2A_verification":
                    summary.tier2a_verification_count += 1
                elif r.tier2_mode == "2B":
                    summary.tier2b_count += 1
            elif r.tier_used == 3:
                summary.tier3_count += 1

            if r.contact_used:
                summary.contact_lookup_attempted += 1
                if r.enrichment_status in ("enriched", "verified"):
                    summary.contact_lookup_success += 1

        return summary
