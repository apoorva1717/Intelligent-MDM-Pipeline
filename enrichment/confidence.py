"""Confidence scoring, flag rules, and enrichment status derivation."""

from __future__ import annotations

from typing import Literal


def determine_enrichment_status(
    confidence: Literal["high", "medium", "low", "none"],
    name2_match_result: str,
    tier_used: int,
    source: str,
) -> Literal["enriched", "verified", "unresolved", "failed"]:
    """Derive the enrichment_status from confidence, match result, and tier.

    - Tier 1 ROR match with high confidence → enriched
    - Contact lookup exact match → verified
    - Contact lookup partial match → enriched
    - Low confidence or tier 3 → unresolved
    - No data → failed
    """
    if confidence == "none":
        return "failed"

    # Exact match from contact lookup = verification
    if name2_match_result == "exact" and source in (
        "contact_lookup_found", "contact_lookup_corrected",
    ):
        return "verified"

    if confidence in ("high", "medium"):
        if tier_used == 3:
            return "unresolved"  # Tier 3 always requires review
        return "enriched"

    # Low confidence
    return "unresolved"


def should_flag_for_review(
    confidence: Literal["high", "medium", "low", "none"],
    tier_used: int,
    tier2_mode: str | None,
    name2_match_result: str,
    source: str,
) -> tuple[bool, str | None]:
    """Decide whether a result needs human review and why.

    Returns (flag_for_review, flag_reason).
    """
    # Tier 3 always flagged
    if tier_used == 3:
        if confidence == "low":
            return True, "LLM low confidence — manual review required"
        return True, "LLM inference — requires verification"

    # Tier 1 high confidence ROR match — no flag needed
    if tier_used == 1 and confidence == "high":
        return False, None

    # Tier 2A exact match — verified, no flag
    if tier2_mode in ("2A_population", "2A_verification") and name2_match_result == "exact":
        return False, None

    # Tier 2A partial match — flag for review
    if tier2_mode in ("2A_population", "2A_verification") and name2_match_result == "partial":
        return True, "Partial match — confirm enriched Name 2"

    # Tier 2A no match — name2 was corrected
    if source == "contact_lookup_corrected":
        return True, "Name 2 corrected — did not match contact page affiliation"

    # Tier 2B with non-official source
    if tier2_mode == "2B" and confidence == "low":
        return True, "Non-official source"

    # Tier 2B department search — generally flagged
    if tier2_mode == "2B":
        return True, "Department search — verify against official records"

    # Medium confidence from Tier 2A population
    if confidence == "medium":
        return True, "Medium confidence — recommend review"

    # Default: no flag
    return False, None
