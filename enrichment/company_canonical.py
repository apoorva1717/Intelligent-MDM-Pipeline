"""LLM-only canonicalisation of company name1 — UC 2/3 for companies.

Zero SerpAPI calls. Single LLM call. Only accepts high-confidence
answers. Falls through silently on any uncertainty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import (
    COMPANY_CANONICAL_SYSTEM_PROMPT,
    COMPANY_CANONICAL_USER_PROMPT_TEMPLATE,
)
from utils.text_utils import canonical_preserves_identity

logger = logging.getLogger(__name__)


@dataclass
class CompanyCanonicalResult:
    success: bool = False
    name1_enriched: str | None = None
    confidence: str = "none"
    # The LLM's high-confidence canonical name when the identity guard
    # rejected it as not-clearly-the-same-entity (success stays False). The
    # orchestrator may re-verify this against GLEIF to recover a typo'd
    # company name ("Bayr AG" → "Bayer AG") the guard blocks. None otherwise.
    proposed_name: str | None = None


async def run_company_canonical(
    record_id: str,
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    llm_client: OpenAIClient,
    street: str | None = None,
    postal_code: str | None = None,
) -> CompanyCanonicalResult:
    result = CompanyCanonicalResult()
    if not name1 or not name1.strip():
        return result

    user_prompt = COMPANY_CANONICAL_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        street=street or "unknown",
        postal_code=postal_code or "unknown",
        city=city or "unknown",
        state=state or "unknown",
        country=country or "unknown",
    )

    try:
        extraction = await llm_client.extract_json(
            COMPANY_CANONICAL_SYSTEM_PROMPT, user_prompt,
        )
    except Exception as exc:
        logger.info("[%s] Company canonical: LLM failed: %s", record_id, exc)
        return result

    official_name = extraction.get("official_name")
    confidence = (extraction.get("confidence") or "low").lower()

    if not official_name or not str(official_name).strip():
        return result
    cleaned = str(official_name).strip()
    if cleaned.lower() in {"null", "none", "n/a", "na"}:
        return result
    if confidence != "high":
        logger.info(
            "[%s] Company canonical: rejecting '%s' (confidence=%s)",
            record_id, cleaned, confidence,
        )
        return result

    # Identity guard: a canonical form must still be the SAME company —
    # reformatting or acronym expansion, not a different entity. Blocks LLM
    # hallucinations like "Iso Group Inc" → "CoStar Group".
    if not canonical_preserves_identity(name1, cleaned):
        logger.warning(
            "[%s] Company canonical: REJECTED '%s' → '%s' "
            "(different entity — identity not preserved)",
            record_id, name1, cleaned,
        )
        # Surface the high-confidence proposal so the orchestrator can, for a
        # plausible spelling correction, re-verify it against GLEIF rather
        # than discard it outright. success stays False — this is NOT an
        # accepted name.
        result.proposed_name = cleaned
        return result

    result.success = True
    result.name1_enriched = cleaned
    result.confidence = confidence

    logger.info(
        "[%s] Company canonical: '%s' → '%s' (high)",
        record_id, name1, cleaned,
    )
    return result
