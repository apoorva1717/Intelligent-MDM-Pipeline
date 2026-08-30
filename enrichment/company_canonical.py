"""LLM-only canonicalisation of company name1 — UC 2/3 for companies.

Zero SerpAPI calls. Single LLM call. Only accepts high-confidence
answers. Falls through silently on any uncertainty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from enrichment.registry_match import _legal_forms, names_match_verbatim

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
    # EVERY usable name the model returned, whatever its stated confidence and
    # whatever the identity guard then decided (Fix 2). `proposed_name` is a
    # narrower thing — a high-confidence proposal the guard refused — and is
    # gated that way because it can buy a GLEIF call. This one buys nothing: it
    # exists so `unchanged-confirmed` can ask whether the model's independent
    # best answer reproduces the input, which is a real question even when the
    # model was only medium-confident about the wording it chose.
    returned_name: str | None = None
    returned_confidence: str = "none"


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

    # Recorded before either gate below. What the model returned is a fact
    # about the call; whether it is good enough to REWRITE Name 1 is a separate
    # decision, and both gates below are about the rewrite.
    result.returned_name = cleaned
    result.returned_confidence = confidence

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

    # A proposal that differs from the record ONLY by legal form is not a
    # canonicalisation. "Paper Money Guaranty" -> "Paper Money Guaranty, LLC"
    # adds a suffix the record did not state and nothing verified; the reverse
    # drops one the record did state. Either way the model has restated the
    # record's own name with the register's decoration, and the record is the
    # authority on its own spelling until a register says otherwise -- which is
    # a different lane, with an identifier behind it.
    #
    # Same rule the registry write applies in `_preferred_registry_variant`,
    # and for the same reason: the legal form is a suffix, not a distinguishing
    # token. A proposal carrying a DIFFERENT legal form is not this case and is
    # left alone -- "Smith Inc" and "Smith LLC" are two legal entities.
    if (
        names_match_verbatim(name1, cleaned)
        and _legal_forms(name1) != _legal_forms(cleaned)
    ):
        logger.info(
            "[%s] Company canonical: '%s' -> '%s' differs only by legal form; "
            "the record's own name stands",
            record_id, name1, cleaned,
        )
        return result

    result.success = True
    result.name1_enriched = cleaned
    result.confidence = confidence

    logger.info(
        "[%s] Company canonical: '%s' → '%s' (high)",
        record_id, name1, cleaned,
    )
    return result
