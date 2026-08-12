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
import re
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import (
    TIER2_CANONICAL_SYSTEM_PROMPT,
    TIER2_CANONICAL_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

# A leading academic-unit prefix ("Department of", "Division of", …). Used to
# detect a DOWNGRADE — a canonical that is just the input with the unit prefix
# stripped ("Department of Biology" → "Biology"). The canonical direction is
# bare → "Department of X", never the reverse.
_UNIT_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:Department|Dept\.?|Division|Div\.?|School|Faculty|"
    r"Institute|Center|Centre|College|Office)\s+of\s+",
    re.IGNORECASE,
)


def _is_prefix_downgrade(original: str, cleaned: str) -> bool:
    """True when *cleaned* is *original* with only a leading unit prefix removed
    ("Department of Biology" → "Biology")."""
    m = _UNIT_PREFIX_RE.match(original.strip())
    if not m:
        return False
    remainder = original.strip()[m.end():].strip()
    return bool(remainder) and remainder.lower() == cleaned.strip().lower()


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

    # Guard against a DOWNGRADE: never accept a canonical that is just the input
    # with a unit prefix stripped ("Department of Biology" → "Biology"). The
    # canonical direction is bare → "Department of X", never the reverse.
    if _is_prefix_downgrade(name2, cleaned):
        logger.info(
            "[%s] Tier 2 canonical: rejecting downgrade '%s' → '%s'",
            record_id, name2, cleaned,
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
