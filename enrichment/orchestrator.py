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
from enrichment.company_canonical import run_company_canonical
from enrichment.overflow_check import run_overflow_check
from enrichment.preprocess import (
    find_suspicious_plain_names,
    llm_classify_plain_names_async,
    preprocess_record,
)
from enrichment.tier1_ror import RORClient, clear_ror_cache
from enrichment.tier2_canonical import run_tier2_canonical
from enrichment.tier2a_contact import Tier2AResult, run_tier2a
from enrichment.tier3_llm import Tier3Result, run_tier3
from llm.openai_client import OpenAIClient
from search.base import SearchClient
from search.duckduckgo_client import DuckDuckGoClient
from search.page_fetcher import PageFetcher
from search.serpapi_client import SerpAPIClient
from utils.cache import BatchCache
from utils.text_utils import (
    canonicalise_unit_name,
    country_to_iso_code,
    expand_abbreviations,
    is_blank,
    is_granular_unit,
    looks_like_research_institution,
    strip_address_fragments,
)

logger = logging.getLogger(__name__)


# ── Result helpers ────────────────────────────────────────────────────────────

def _init_result(record: EnrichmentRecord) -> dict[str, Any]:
    """Create a blank result dict with originals populated."""
    # Map the legacy single 'street' input into street1 if street1 is blank.
    street1_original = record.street1 or record.street
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
        "contact_original": record.contact,
        "contact_enriched": None,
        "contact_changed": False,
        "email_original": record.email,
        "email_enriched": None,
        "email_changed": False,
        "street1_original": street1_original,
        "street1_enriched": None,
        "street1_changed": False,
        "street2_original": record.street2,
        "street2_enriched": None,
        "street2_changed": False,
        "street3_original": record.street3,
        "street3_enriched": None,
        "street3_changed": False,
        "record_type": "unknown",
        "tier_used": 1,
        "tier2_mode": None,
        "confidence": "none",
        "source": "none",
        "ror_id": None,
        "source_url": None,
        "contact_used": False,
        "name2_match_result": "not_applicable",
        "use_cases_triggered": [],
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

    # Canonicalise academic unit names on name2/name3 only. name1
    # (the institution) is never a "Department of X", so we leave
    # it alone. This collapses "Chemistry Department",
    # "Dept of Chemistry", "Department of Chemistry" all to
    # "Department of Chemistry".
    # UC 5 scope: never canonicalise granular units (lab/group/
    # centre/facility) — leave them verbatim.
    for field in ("name2_enriched", "name3_enriched"):
        val = result.get(field)
        if val and not is_granular_unit(val):
            canonical = canonicalise_unit_name(val)
            if canonical and canonical != val:
                result[field] = canonical

    preprocess_cleared = result.get("_preprocess_cleared") or set()

    # Passthrough: if no tier enriched name2/name3 but the record had
    # one, retain the original value — UNLESS preprocessing deliberately
    # cleared the field (e.g. extracted an email, address, contact
    # name). Enrichment must never silently drop user-supplied fields
    # but must also respect preprocessing's decision to empty a field.
    for field in ("name2", "name3"):
        if result.get(f"{field}_enriched") is None and field not in preprocess_cleared:
            orig = result.get(f"{field}_original")
            if orig and str(orig).strip():
                result[f"{field}_enriched"] = str(orig).strip()

    # Passthrough for contact / email / street fields: any field that
    # preprocessing / tiers did not touch retains its original value
    # in the enriched slot. This means "enriched" always reflects the
    # final state after the pipeline runs, not only what changed.
    for base in ("contact", "email", "street1", "street2", "street3"):
        if result.get(f"{base}_enriched") is None:
            orig = result.get(f"{base}_original")
            if orig and str(orig).strip():
                result[f"{base}_enriched"] = str(orig).strip()

    # Compute all changed flags.
    def _changed(field: str) -> bool:
        enr = result.get(f"{field}_enriched")
        orig = result.get(f"{field}_original")
        return bool(enr and enr != orig)

    for f in ("name1", "name2", "name3", "contact", "email",
              "street1", "street2", "street3"):
        result[f"{f}_changed"] = _changed(f)

    result["duration_ms"] = int((time.monotonic() - start) * 1000)
    # Strip transient non-schema keys before pydantic validation.
    result.pop("_preprocess_cleared", None)
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
    # Only populate name3 if the input record originally had one.
    # Tier 2A opportunistically extracts groups/programs from the
    # contact's page — but we should not introduce a name3 the user
    # didn't ask for.
    if (
        tier2a.name3_enriched and tier2a.name3_enriched.strip()
        and result.get("name3_original")
        and str(result["name3_original"]).strip()
    ):
        result["name3_enriched"] = tier2a.name3_enriched.strip()


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

            # ── UC 0: Name 1 overflow check ──────────────────────────
            # Single LLM call. If Name1 + Name2 read as ONE continuous
            # organisation name, flag the record and return immediately
            # — no other tier runs, no auto-correction. Flagged records
            # go to manual review.
            if (
                record.name1 and record.name1.strip()
                and record.name2 and record.name2.strip()
            ):
                overflow = await run_overflow_check(
                    record_id=record.record_id,
                    name1=record.name1,
                    name2=record.name2,
                    llm_client=self._llm_client,
                )
                if overflow.is_overflow:
                    logger.info({
                        "record_id": record.record_id,
                        "step": "uc0_overflow_flagged",
                        "confidence": overflow.confidence,
                        "reasoning": overflow.reasoning,
                    })
                    # Pass through originals untouched. Flag only.
                    result["name1_enriched"] = record.name1.strip()
                    result["name2_enriched"] = record.name2.strip()
                    result["record_type"] = "unknown"
                    result["tier_used"] = 1
                    result["source"] = "pattern_match"
                    result["confidence"] = overflow.confidence
                    result["enrichment_status"] = "unresolved"
                    result["flag_for_review"] = True
                    result["flag_reason"] = (
                        f"UC 0: possible Name 1 overflow into Name 2 — "
                        f"{overflow.reasoning}"
                    )
                    result["use_cases_triggered"] = [0]
                    result = finalise(result, start)
                    return EnrichmentResult(**result)

            # ── PREPROCESS (UC 6, 7, 8, 9): deterministic cleanup ─────
            # Pulls emails, addresses, contact-person names, and AP
            # references out of name fields and moves them to the
            # correct slots. LLM is only called for plain-name
            # candidates (UC 7 Pattern B2) when the pattern is
            # suspicious — no title, no org signals, 2-3 capitalised
            # words.
            suspicious = find_suspicious_plain_names(
                record.name1, record.name2, record.name3,
            )
            person_verdicts = await llm_classify_plain_names_async(
                self._llm_client, suspicious,
            ) if suspicious else {}

            pre = preprocess_record(
                name1=record.name1,
                name2=record.name2,
                name3=record.name3,
                contact=record.contact,
                email=record.email,
                street1=result["street1_original"],
                street2=record.street2,
                street3=record.street3,
                llm_person_verdicts=person_verdicts,
            )

            if pre.use_cases:
                logger.info({
                    "record_id": record.record_id,
                    "step": "preprocess",
                    "use_cases": pre.use_cases,
                    "flags": pre.flags,
                })
            for uc in pre.use_cases:
                if uc not in result["use_cases_triggered"]:
                    result["use_cases_triggered"].append(uc)

            # Track which name fields were touched by preprocessing.
            # "cleared" = field was non-empty and is now None/empty.
            # "changed" = field value is different (includes cleared).
            # Both types prevent finalise() from restoring the original.
            preprocess_cleared: set[str] = set()
            for base, pre_val, orig in (
                ("name1", pre.name1, record.name1),
                ("name2", pre.name2, record.name2),
                ("name3", pre.name3, record.name3),
            ):
                orig_stripped = (orig or "").strip()
                pre_stripped = (pre_val or "").strip()
                if orig_stripped and not pre_stripped:
                    preprocess_cleared.add(base)
                elif orig_stripped and pre_stripped and orig_stripped != pre_stripped:
                    # Preprocessing changed (but didn't clear) the value
                    # — write it as the enriched value now so finalise()
                    # doesn't overwrite it with the original. Example:
                    # "Accounts Payable Dept" → "Accounts Payable".
                    result[f"{base}_enriched"] = pre_stripped
            result["_preprocess_cleared"] = preprocess_cleared

            # If preprocessing populated any contact/email/street/name
            # field, record the enriched value now. (Final passthrough
            # in finalise() will retain originals for untouched fields.)
            if pre.contact is not None and pre.contact != result["contact_original"]:
                result["contact_enriched"] = pre.contact
            if pre.email is not None and pre.email != result["email_original"]:
                result["email_enriched"] = pre.email
            for slot in ("street1", "street2", "street3"):
                v = getattr(pre, slot)
                if v is not None and v != result[f"{slot}_original"]:
                    result[f"{slot}_enriched"] = v

            # From here on, the tiers work with the PREPROCESSED names.
            pp_name1 = pre.name1
            pp_name2 = pre.name2
            pp_name3 = pre.name3
            pp_contact = pre.contact
            pp_street1 = pre.street1

            # Flag if preprocessing triggered a conflict
            if "contact-conflict" in pre.flags or "email-conflict" in pre.flags or "street-slots-full" in pre.flags:
                result["flag_for_review"] = True
                result["flag_reason"] = "; ".join(
                    f for f in pre.flags
                    if "conflict" in f or "slots-full" in f
                )

            institution_domain: str | None = None

            # ── TIER 1: ROR ──────────────────────────────────────────────

            if not is_blank(pp_name1):
                # Resolve country to ISO alpha-2 for the ROR query filter
                country_code = country_to_iso_code(record.country)

                # Clean any residual address fragments that leaked into
                # name1 using the record's own structured address fields
                # (preprocessing may have already pulled some out; this
                # is a second defensive pass that uses pp_street1 in
                # case preprocessing moved content there).
                name1_cleaned = strip_address_fragments(
                    pp_name1,
                    street=pp_street1 or record.street,
                    city=record.city,
                    state=record.state,
                    zip_code=record.zip,
                ) or (pp_name1 or "").strip()

                if name1_cleaned != (pp_name1 or "").strip():
                    logger.info({
                        "record_id": record.record_id,
                        "step": "tier1_name1_cleaned",
                        "original": pp_name1,
                        "cleaned": name1_cleaned,
                    })

                ror_parent = await self._ror_client.call(
                    name1_cleaned,
                    country_code=country_code,
                    country=record.country,
                    city=record.city,
                    state=record.state,
                )

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier1_ror_parent",
                    "query": name1_cleaned,
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

                    # Mark Tier 1 success and UC 2/3 triggered.
                    result["enrichment_status"] = "enriched"
                    if 2 not in result["use_cases_triggered"]:
                        result["use_cases_triggered"].append(2)
                    if 3 not in result["use_cases_triggered"]:
                        result["use_cases_triggered"].append(3)

                    # If there's no name2 and no contact, Tier 1 is
                    # the final answer — return immediately rather
                    # than letting Tier 3 LLM overwrite the canonical
                    # ROR name with a fabricated variant.
                    if not (pp_name2 and pp_name2.strip()) and not (
                        pp_contact and pp_contact.strip()
                    ):
                        result = finalise(result, start)
                        return EnrichmentResult(**result)

                    # ── Child match for name2 (local fuzzy against parent's children) ──
                    if pp_name2 and pp_name2.strip():
                        children = ror_parent.get("children", [])
                        # Expand abbreviations ("Dept of X" → "Department of X")
                        # so the fuzzy match against ROR's canonical children
                        # is deterministic and reliable.
                        name2_for_match = (
                            expand_abbreviations(pp_name2.strip())
                            or pp_name2.strip()
                        )
                        child_match = _match_child_locally(
                            name2_for_match, children,
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
                    # ── ROR miss ─────────────────────────────────────
                    # If the name LOOKS like a research institution
                    # (University / College / Hospital / Medical School
                    # / School of X, etc.) we do NOT call the company
                    # canonical LLM, which would return a legal entity
                    # name like 'President and Fellows of Harvard
                    # College' for 'Harvard Medical School'. Pass
                    # through the original and flag for review instead.
                    looks_research = looks_like_research_institution(name1_cleaned)

                    logger.info({
                        "record_id": record.record_id,
                        "step": "tier1_ror_miss",
                        "name1_cleaned": name1_cleaned,
                        "looks_research": looks_research,
                    })

                    if looks_research:
                        result["name1_enriched"] = name1_cleaned
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["tier_used"] = 1
                        result["record_type"] = "research_institution"
                        result["enrichment_status"] = "unresolved"
                        result["flag_for_review"] = True
                        result["flag_reason"] = (
                            "Research-institution name not found in ROR — "
                            "left unchanged for manual review"
                        )
                        institution_domain = None
                        # Short-circuit if no name2/contact to process.
                        if not (pp_name2 and pp_name2.strip()) and not (
                            pp_contact and pp_contact.strip()
                        ):
                            result = finalise(result, start)
                            return EnrichmentResult(**result)
                        # Otherwise fall through to Tier 2/3 for name2.
                        company_res = None
                    else:
                        company_res = await run_company_canonical(
                            record_id=record.record_id,
                            name1=name1_cleaned,
                            city=record.city,
                            state=record.state,
                            country=record.country,
                            llm_client=self._llm_client,
                        )

                    if company_res and company_res.success and company_res.name1_enriched:
                        result["name1_enriched"] = company_res.name1_enriched
                        result["source"] = "llm_canonical"
                        result["confidence"] = "high"
                        result["record_type"] = "company"
                        result["tier_used"] = 2
                        if 2 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(2)
                        if 3 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(3)
                        result["enrichment_status"] = "enriched"
                        result["flag_for_review"] = True
                        result["flag_reason"] = "LLM canonical company name — verify"

                        # If no name2, we're done — return immediately
                        # so Tier 3 doesn't overwrite the canonical name.
                        if not (pp_name2 and pp_name2.strip()):
                            result = finalise(result, start)
                            return EnrichmentResult(**result)
                    elif company_res is not None:
                        # Company canonical was attempted but failed.
                        result["name1_enriched"] = name1_cleaned
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["tier_used"] = 1
                        result["record_type"] = "unknown"
                    # else: research-institution passthrough already set above

                    institution_domain = None

            name2_already_filled = bool(pp_name2 and pp_name2.strip())
            has_dept_signal = (
                name2_already_filled
                or bool(pp_contact and pp_contact.strip())
            )

            # ── TIER 2 (canonical — UC 5): LLM-only canonicalization ───
            # Runs for BOTH research institutions and companies when
            # name2 is present and Tier 1 child matching did not
            # resolve it. Zero SerpAPI calls.
            # UC 5 scope: only accept dept/div/school/college/faculty.
            # Reject lab/group/centre/facility canonicalisations AND
            # short-circuit — the original name2 is left unchanged in
            # that case (out of scope rule).
            # Also skip when preprocessing already normalised name2
            # to a pattern_match value like "Accounts Payable" — that's
            # not a department to canonicalise.
            is_ap_normalised = (
                (pp_name2 or "").strip().lower() == "accounts payable"
            )

            # AP records are handled entirely by preprocessing. Don't
            # run the LLM canonicaliser on them, don't run Tier 3, and
            # don't fabricate departments — just write the normalised
            # value through and return. This preserves both the
            # "Accounts Payable" label and any contact extracted
            # earlier from the Attn prefix.
            if is_ap_normalised:
                result["name2_enriched"] = pp_name2
                result["tier_used"] = 2
                result["source"] = "pattern_match"
                result["confidence"] = "high"
                result["enrichment_status"] = "enriched"
                result["flag_for_review"] = True
                result["flag_reason"] = "Accounts Payable record — verify"
                if 6 not in result["use_cases_triggered"]:
                    result["use_cases_triggered"].append(6)
                result = finalise(result, start)
                return EnrichmentResult(**result)

            if (
                name2_already_filled
                and result["record_type"] in ("research_institution", "company")
                and result.get("name1_enriched")
            ):
                canonical = await run_tier2_canonical(
                    record_id=record.record_id,
                    institution=result["name1_enriched"],
                    name2=pp_name2,  # type: ignore[arg-type]
                    llm_client=self._llm_client,
                )
                logger.info({
                    "record_id": record.record_id,
                    "step": "tier2_canonical_result",
                    "found": canonical.success,
                    "confidence": canonical.confidence,
                    "name2_enriched": canonical.name2_enriched,
                })
                if canonical.success and canonical.name2_enriched:
                    # UC 5 scope filter: reject granular units AND
                    # short-circuit — leave original unchanged, do not
                    # fall through to Tier 3 which would overwrite.
                    if is_granular_unit(canonical.name2_enriched):
                        logger.info({
                            "record_id": record.record_id,
                            "step": "tier2_canonical_rejected_scope",
                            "name2_rejected": canonical.name2_enriched,
                            "reason": "lab/group/centre/facility out of scope for UC 5",
                        })
                        result["name2_enriched"] = pp_name2
                        result["tier_used"] = 2
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["enrichment_status"] = "unresolved"
                        result["flag_for_review"] = True
                        result["flag_reason"] = (
                            "name2 is a lab/group/centre/facility — out of "
                            "scope for canonicalisation, left unchanged"
                        )
                        result = finalise(result, start)
                        return EnrichmentResult(**result)
                    else:
                        result["name2_enriched"] = canonical.name2_enriched
                        result["tier_used"] = 2
                        result["source"] = "llm_canonical"
                        result["confidence"] = "high"
                        result["name2_match_result"] = "not_applicable"
                        result["enrichment_status"] = "enriched"
                        result["flag_for_review"] = True
                        result["flag_reason"] = "LLM canonical form — verify"
                        if 5 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(5)
                        result = finalise(result, start)
                        return EnrichmentResult(**result)

                # Canonical didn't return a confident answer — leave
                # name2 as-is, don't let Tier 3 fabricate something
                # else. For companies, this also avoids the Pfizer
                # "Global Research" → "Pfizer Global R&D" fabrication.
                result["name2_enriched"] = pp_name2
                result["tier_used"] = 2
                result["source"] = "passthrough"
                result["confidence"] = "low"
                result["enrichment_status"] = "unresolved"
                result["flag_for_review"] = True
                result["flag_reason"] = (
                    "name2 could not be canonicalised with high "
                    "confidence — left unchanged"
                )
                result = finalise(result, start)
                return EnrichmentResult(**result)

            # ── TIER 2A (contact lookup): 1 SerpAPI call, gated ────────
            # Runs only when name2 is null and we have a contact plus
            # an official domain. A single quoted-name on-domain query.
            # SERP candidates are deterministically filtered so only
            # results whose URL/title/snippet contain BOTH the first
            # name and the surname of the contact reach the page fetch
            # / LLM step. That blocks near-miss matches like
            # 'Sarah Knox' for a 'Sarah Chen' query.
            can_do_contact_lookup = (
                not name2_already_filled
                and result["record_type"] == "research_institution"
                and bool(pp_contact and pp_contact.strip())
                and bool(institution_domain)
            )

            logger.info({
                "record_id": record.record_id,
                "step": "tier_contact_decision",
                "attempting": can_do_contact_lookup,
                "name2_already_filled": name2_already_filled,
            })

            if can_do_contact_lookup:
                mode = "population"
                tier2a_result = await run_tier2a(
                    record_id=record.record_id,
                    contact=pp_contact,  # type: ignore[arg-type]
                    institution=result["name1_enriched"] or pp_name1 or "",
                    domain=institution_domain,
                    name2=pp_name2,
                    name3=pp_name3,
                    search_client=self._search_client,
                    page_fetcher=self._page_fetcher,
                    llm_client=self._llm_client,
                    cache=cache,
                    settings=self._settings,
                )

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier_contact_result",
                    "found": tier2a_result.success,
                    "confidence": tier2a_result.confidence,
                    "name2_enriched": tier2a_result.name2_enriched,
                })

                if tier2a_result.success:
                    # UC 4 scope: reject lab/group/centre/facility
                    # canonicalisations. If the raw Tier 2A answer is
                    # itself granular, skip the upgrade and the result.
                    if (
                        tier2a_result.name2_enriched
                        and is_granular_unit(tier2a_result.name2_enriched)
                    ):
                        logger.info({
                            "record_id": record.record_id,
                            "step": "tier_contact_rejected_scope",
                            "reason": "lab/group/centre/facility out of scope",
                            "rejected": tier2a_result.name2_enriched,
                        })
                    else:
                        # Canonicalise bare-subject answers like
                        # "Anesthesia" → full institutional name.
                        if tier2a_result.name2_enriched:
                            canon = await run_tier2_canonical(
                                record_id=record.record_id,
                                institution=result["name1_enriched"] or pp_name1 or "",
                                name2=tier2a_result.name2_enriched,
                                llm_client=self._llm_client,
                            )
                            if canon.success and canon.name2_enriched:
                                if is_granular_unit(canon.name2_enriched):
                                    logger.info({
                                        "record_id": record.record_id,
                                        "step": "tier_contact_canonical_rejected_scope",
                                        "rejected": canon.name2_enriched,
                                    })
                                else:
                                    logger.info({
                                        "record_id": record.record_id,
                                        "step": "tier_contact_canonicalised",
                                        "before": tier2a_result.name2_enriched,
                                        "after": canon.name2_enriched,
                                    })
                                    tier2a_result.name2_enriched = canon.name2_enriched
                        _apply_tier2a(result, tier2a_result, mode)
                        if 4 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(4)
                        result = finalise(result, start)
                        return EnrichmentResult(**result)

            # ── TIER 3: LLM INFERENCE ────────────────────────────────────

            logger.info({
                "record_id": record.record_id,
                "step": "tier3_start",
            })

            tier3_result: Tier3Result = await run_tier3(
                record_id=record.record_id,
                name1=pp_name1,
                name2=pp_name2,
                name3=pp_name3,
                contact=pp_contact,
                street=pp_street1 or record.street,
                city=record.city,
                state=record.state,
                zip_code=record.zip,
                country=record.country,
                llm_client=self._llm_client,
            )

            _apply_tier3(result, tier3_result)

            # Last resort: if nothing enriched name1, pass through the preprocessed original
            if not result.get("name1_enriched") and not is_blank(pp_name1):
                result["name1_enriched"] = (pp_name1 or "").strip()

            # Rule: if the input had no name2 and no contact, there is no
            # signal from which to infer a department — name2_enriched
            # must remain empty regardless of what any tier produced.
            if not has_dept_signal:
                result["name2_enriched"] = None

            # Rule: if preprocessing stripped name2 down to empty
            # (because it was actually a contact / email / address /
            # AP reference), do not let Tier 3 fabricate a replacement.
            if (
                record.name2 and record.name2.strip()
                and not (pp_name2 and pp_name2.strip())
            ):
                logger.info({
                    "record_id": record.record_id,
                    "step": "name2_cleared_by_preprocess",
                    "original": record.name2,
                })
                result["name2_enriched"] = None

            # Rule: name2_enriched should never echo name1_enriched.
            if (
                result.get("name2_enriched")
                and result.get("name1_enriched")
                and result["name2_enriched"].strip().lower()
                    == result["name1_enriched"].strip().lower()
            ):
                logger.info({
                    "record_id": record.record_id,
                    "step": "name2_equals_name1_dropped",
                    "value": result["name2_enriched"],
                })
                result["name2_enriched"] = None

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
