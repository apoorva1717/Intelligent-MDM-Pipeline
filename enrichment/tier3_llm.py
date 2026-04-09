"""Tier 3: LLM inference — last resort, always flagged for review."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import TIER3_SYSTEM_PROMPT, TIER3_USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


@dataclass
class Tier3Result:
    """Outcome of Tier 3 LLM inference."""
    success: bool = False
    name1_suggestion: str | None = None
    name2_suggestion: str | None = None
    name3_suggestion: str | None = None
    confidence: str = "none"
    flag_for_review: bool = True
    flag_reason: str = "LLM inference — requires verification"
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
    - Always flag for review (requires_verification = true)
    - High/medium confidence: write suggestions, status=unresolved
    - Low confidence: do NOT overwrite originals, status=unresolved
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
        result.flag_reason = "LLM call failed"
        return result

    llm_confidence = extraction.get("confidence", "low")
    result.confidence = llm_confidence

    if llm_confidence in ("high", "medium"):
        # Write suggestions but still flag for review
        result.success = True
        # FIX(Bug 5): never assign empty string to suggestion fields
        n1 = extraction.get("name1_suggestion")
        result.name1_suggestion = n1.strip() if n1 and str(n1).strip() else None
        n2 = extraction.get("name2_suggestion")
        result.name2_suggestion = n2.strip() if n2 and str(n2).strip() else None
        n3 = extraction.get("name3_suggestion")
        result.name3_suggestion = n3.strip() if n3 and str(n3).strip() else None
        result.enrichment_status = "unresolved"
        result.flag_for_review = True
        result.flag_reason = "LLM inference — requires verification"
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
        result.flag_for_review = True
        result.flag_reason = "LLM low confidence — manual review required"
        logger.info("[%s] Tier 3: low confidence, not overwriting", record_id)

    return result
