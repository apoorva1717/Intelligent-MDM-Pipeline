"""Confidence scoring and enrichment status derivation.

Flag rules used to live here too, in ``should_flag_for_review`` — the
function the README's Flag Rules table described. Nothing ever called it:
every tier set ``flag_for_review`` inline as it ran, which is how the code
came to contradict its own documented spec (a high-confidence Tier 2
canonicalisation was flagged "LLM canonical form — verify" even though the
table said no flag). The flag model now lives in ``enrichment.flags`` and
is applied from exactly one place, ``finalise``.
"""

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
