"""LLM-only canonicalisation of company name1 — UC 2/3 for companies.

Zero SerpAPI calls. Single LLM call.

Confidence is no longer a write gate (§1a). It was: an answer below ``high``
was discarded, which threw away correct canonical names for every record whose
input the model could read but not certify — and the record then shipped its
raw input under a flag saying the canonical form "could not be established",
which was not what had happened. What the model returned is recorded as
``self_reported`` provenance and drives how selectively the record is flagged;
whether the answer may be WRITTEN is decided by identity alone, in
:mod:`enrichment.name_gate`, and only a ``different`` verdict refuses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import (
    COMPANY_CANONICAL_SYSTEM_PROMPT,
    COMPANY_CANONICAL_USER_PROMPT_TEMPLATE,
)
from utils.name_identity import DIFFERENT, SAME, classify_name_change
from dedup.signatures import normalize_key

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
    #: ``same`` / ``undecidable`` / ``different`` for the returned name against
    #: the record's own. Read by the caller to decide the flag, never to decide
    #: the write — the gate does that.
    verdict: str = SAME


async def run_company_canonical(
    record_id: str,
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    llm_client: OpenAIClient,
    street: str | None = None,
    postal_code: str | None = None,
    authoritative: bool = True,
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

    # Identity is the only write gate. A canonical form must still name the
    # SAME company — reformatting, an abbreviation expansion or a repaired
    # truncation, not a different entity. Three verdicts rather than a
    # boolean: `undecidable` (the record states codes the canonical name has
    # no duty to repeat) writes and is flagged, where the boolean guard
    # discarded it exactly as it discarded "Iso Group Inc" → "CoStar Group".
    verdict = classify_name_change(name1, cleaned)
    result.verdict = verdict

    if verdict == DIFFERENT:
        logger.warning(
            "[%s] Company canonical: REJECTED '%s' → '%s' "
            "(different entity — identity not preserved)",
            record_id, name1, cleaned,
        )
        # Surfaced so the orchestrator can re-verify a plausible spelling
        # correction against GLEIF, and so the flag can name the proposal
        # instead of leaving the reviewer to rediscover it. success stays
        # False — this is NOT an accepted name.
        result.proposed_name = cleaned
        return result

    if not authoritative and confidence != "high":
        # Legacy acceptance policy, kept for the A/B baseline.
        logger.info(
            "[%s] Company canonical: rejecting '%s' (confidence=%s, legacy)",
            record_id, cleaned, confidence,
        )
        return result

    result.success = True
    result.name1_enriched = cleaned
    result.confidence = confidence
    # Surfaced on an ACCEPTED answer too. The orchestrator re-queries GLEIF on
    # a spelling correction to attach the LEI; under the old gate that only
    # happened because the identity guard had refused the name, so accepting
    # the correction would have silently cost the record its identifier. The
    # re-verification is an upgrade on a written value, never a veto (§1f).
    if normalize_key(cleaned) != normalize_key(name1 or ""):
        result.proposed_name = cleaned

    logger.info(
        "[%s] Company canonical: '%s' → '%s' (%s, %s)",
        record_id, name1, cleaned, confidence, verdict,
    )
    return result
