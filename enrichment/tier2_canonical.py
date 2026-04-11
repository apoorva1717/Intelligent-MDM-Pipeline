"""Tier 2 — LLM-only canonicalization of a user-supplied department name.

Runs when name2 is present but Tier 1 ROR child matching did not find
a match in the institution's ROR children list. Uses the LLM's existing
knowledge of well-known institutions to return the canonical form the
institution itself uses on its own website. Zero SerpAPI calls.

The result is ONLY used when confidence=high. Any other confidence
level (or a null answer) means we could not canonicalise, and the
caller falls through to the next tier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import (
    TIER2_CANONICAL_SYSTEM_PROMPT,
    TIER2_CANONICAL_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


@dataclass
class Tier2CanonicalResult:
    success: bool = False
    name2_enriched: str | None = None
    confidence: str = "none"
    reasoning: str = ""


async def run_tier2_canonical(
    record_id: str,
    institution: str,
    name2: str,
    llm_client: OpenAIClient,
) -> Tier2CanonicalResult:
    """Single LLM call. No page fetch, no SERP."""
    result = Tier2CanonicalResult()

    if not institution or not name2:
        return result

    user_prompt = TIER2_CANONICAL_USER_PROMPT_TEMPLATE.format(
        institution=institution,
        name2=name2,
    )

    try:
        extraction = await llm_client.extract_json(
            TIER2_CANONICAL_SYSTEM_PROMPT, user_prompt,
        )
    except Exception as exc:
        logger.info("[%s] Tier 2 canonical: LLM call failed: %s", record_id, exc)
        return result

    official_name = extraction.get("official_name")
    confidence = (extraction.get("confidence") or "low").lower()
    reasoning = extraction.get("reasoning", "")

    if not official_name or not str(official_name).strip():
        logger.info("[%s] Tier 2 canonical: LLM returned no name", record_id)
        return result

    # Guard against literal "null" / "none" strings
    cleaned = str(official_name).strip()
    if cleaned.lower() in {"null", "none", "n/a", "na"}:
        return result

    # Only trust high-confidence answers — this tier is authoritative
    # for well-known institutions, not a best-effort guess.
    if confidence != "high":
        logger.info(
            "[%s] Tier 2 canonical: rejecting '%s' (confidence=%s)",
            record_id, cleaned, confidence,
        )
        return result

    result.success = True
    result.name2_enriched = cleaned
    result.confidence = confidence
    result.reasoning = reasoning

    logger.info(
        "[%s] Tier 2 canonical: '%s' → '%s' (high confidence)",
        record_id, name2, cleaned,
    )
    return result
