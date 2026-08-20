"""Tier 3: LLM inference — last resort, always flagged for review."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import TIER3_SYSTEM_PROMPT, TIER3_USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

# Address-shaped content that must never appear in a NAME suggestion.
_STREET_SUFFIX_RE = re.compile(
    r"\b(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Pl|Place|Ter|Terrace|Sq|Square|Cir|"
    r"Circle|Platz|Stra(?:ße|sse)|Weg|Allee|Gasse)\b\.?",
    re.IGNORECASE,
)
# US 5-digit zip or a UK-style postcode ("BT7 1NH", "SW1A 1AA").
_POSTAL_RE = re.compile(
    r"\b\d{5}(?:-\d{4})?\b|\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z]{3,}", text.lower())}


def _is_address_like_name(value: str | None, street: str | None) -> bool:
    """True when a Tier 3 NAME suggestion is actually address content — a
    postal code, a house-number + street-type pattern, or a high token overlap
    with the record's own street field (i.e. copied wholesale from the street).
    """
    if not value or not value.strip():
        return False
    v = value.strip()
    if _POSTAL_RE.search(v):
        return True
    if _STREET_SUFFIX_RE.search(v) and re.search(r"\d", v):
        return True
    if street and street.strip():
        vt, st = _tokens(v), _tokens(street)
        if vt and st and len(vt & st) / len(vt) >= 0.5:
            return True
    return False


@dataclass
class Tier3Result:
    """Outcome of Tier 3 LLM inference."""
    success: bool = False
    name1_suggestion: str | None = None
    name2_suggestion: str | None = None
    name3_suggestion: str | None = None
    confidence: str = "none"
    enrichment_status: str = "unresolved"
    source: str = "LLM"


async def run_tier3(
    record_id: str,
    name1: str | None,
    name2: str | None,
    name3: str | None,
    contact: str | None,
    street: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
    country: str | None,
    llm_client: OpenAIClient,
) -> Tier3Result:
    """Execute Tier 3 LLM inference.

    Decision points:
    - High/medium confidence: write suggestions, status=unresolved
    - Low confidence: do NOT overwrite originals, status=unresolved

    Tier 3 raises no review flag of its own. It reports what it inferred and
    how confident it was; ``enrichment.flags`` decides what that means once
    the record is final — a value Tier 3 wrote reads as ``unverified-inference``
    unless a later authority (Fix 2's Tier 1 retry) overwrote it.
    """
    logger.info("[%s] Tier 3 starting — LLM inference last resort", record_id)
    result = Tier3Result()

    user_prompt = TIER3_USER_PROMPT_TEMPLATE.format(
        name1=name1 or "not recorded",
        name2=name2 or "not recorded",
        name3=name3 or "not recorded",
        contact=contact or "not recorded",
        street=street or "",
        city=city or "",
        state=state or "",
        zip=zip_code or "",
        country=country or "",
    )

    try:
        extraction = await llm_client.extract_json(TIER3_SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("[%s] Tier 3: LLM call failed", record_id)
        result.confidence = "none"
        result.enrichment_status = "failed"
        return result

    llm_confidence = extraction.get("confidence", "low")
    result.confidence = llm_confidence

    if llm_confidence in ("high", "medium"):
        result.success = True
        # FIX(Bug 5): never assign empty string to suggestion fields
        n1 = extraction.get("name1_suggestion")
        result.name1_suggestion = n1.strip() if n1 and str(n1).strip() else None
        n2 = extraction.get("name2_suggestion")
        result.name2_suggestion = n2.strip() if n2 and str(n2).strip() else None
        n3 = extraction.get("name3_suggestion")
        result.name3_suggestion = n3.strip() if n3 and str(n3).strip() else None
        result.enrichment_status = "unresolved"

        # Deterministic guard: reject any name suggestion that is actually
        # address content (street/postal/high overlap with the record's street).
        # Setting it to None keeps the original; the caller never overwrites a
        # name with a dropped suggestion.
        rejected = []
        for attr in ("name1_suggestion", "name2_suggestion", "name3_suggestion"):
            val = getattr(result, attr)
            if val and _is_address_like_name(val, street):
                setattr(result, attr, None)
                rejected.append(attr)
        if rejected:
            # The suggestion is dropped, so the original stands and there is
            # nothing new to verify — finalisation describes the record by
            # what it holds, not by what was rejected on the way.
            logger.info(
                "[%s] Tier 3: rejected address-like name suggestion(s): %s",
                record_id, rejected,
            )

        logger.info(
            "[%s] Tier 3 result: name1='%s', name2='%s', confidence=%s",
            record_id,
            result.name1_suggestion,
            result.name2_suggestion,
            llm_confidence,
        )
    else:
        # Low confidence — do NOT overwrite originals
        result.success = False
        result.enrichment_status = "unresolved"
        logger.info("[%s] Tier 3: low confidence, not overwriting", record_id)

    return result
