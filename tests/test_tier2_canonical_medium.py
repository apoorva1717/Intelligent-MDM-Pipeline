"""A MEDIUM-confidence Tier 2 canonical answer is kept only when it is a
verified PURE RE-WORDING of the input — the subject the record supplied,
re-cast into "<Unit> of <Subject>" form, with nothing added and nothing
dropped but a bare building/room code.

The case that motivated it: "Marine Biology, OCSB" at Texas A&M University at
Galveston. The model answers "Department of Marine Biology" but rates itself
medium, because it cannot confirm TAMUG's exact unit wording — so the old
high-only gate threw the right answer away and shipped "Marine Biology, Ocsb".
Every medium answer that swaps or invents a subject must still be rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier2_canonical import (
    _is_pure_recanonicalisation,
    run_tier2_canonical,
)


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload

    async def extract_json(self, system, user):
        return self._payload


class TestPureRecanonicalisation:
    @pytest.mark.parametrize("original,cleaned", [
        # The motivating case — a trailing building code is the only loss.
        ("Marine Biology, OCSB", "Department of Marine Biology"),
        # Bare subject re-cast, nothing dropped at all.
        ("Marine Biology", "Department of Marine Biology"),
        # Abbreviation expansion is not a content change.
        ("Chemical Eng", "Department of Chemical Engineering"),
        # A code sitting anywhere in the value is droppable, not just at the end.
        ("Biological Sciences, ENB", "Department of Biological Sciences"),
        # A leading faculty code is a code like any other.
        ("FAS Human Evolutionary Biology",
         "Department of Human Evolutionary Biology"),
        # Re-ordering the unit word is not a content change.
        ("Marine Biology Dept", "Department of Marine Biology"),
    ])
    def test_accepted(self, original, cleaned):
        assert _is_pure_recanonicalisation(original, cleaned) is True

    @pytest.mark.parametrize("original,cleaned", [
        # Subject swapped outright — the fabrication the gate exists to stop.
        ("Office of Purchasing", "Department of Procurement Services"),
        ("National Vehicle & Fuel Emissions",
         "Office of Transportation and Air Quality"),
        ("Hanscom Air Force Base",
         "Department of Air Force Research Information"),
        # Subject words added.
        ("& Health Sciences", "School of Arts and Sciences"),
        ("Chemical & Biomedical Eng",
         "Department of Chemical, Biological and Materials Engineering"),
        # A real second subject dropped — not a code.
        ("Chemistry, Biology", "Department of Chemistry"),
        # An ALL-CAPS value gives the code heuristic nothing to work with, so
        # no token may be dropped as a code.
        ("CHEMISTRY, BIOLOGY", "Department of Chemistry"),
        # Not in canonical "<Unit> of <Subject>" form — `canonicalise_unit_name`
        # owns those deterministically.
        ("Fire Dept", "Fire Department"),
        ("Peninsula", "Peninsula Center"),
        ("Central Receiving", "Central Receiving"),
    ])
    def test_rejected(self, original, cleaned):
        assert _is_pure_recanonicalisation(original, cleaned) is False


class TestMediumConfidenceGate:
    @pytest.mark.asyncio
    async def test_verified_medium_is_kept(self):
        llm = _FakeLLM({
            "official_name": "Department of Marine Biology",
            "confidence": "medium",
            "reasoning": "OCSB is a building, not a unit.",
        })
        r = await run_tier2_canonical(
            record_id="13064119",
            institution="Texas A&M University at Galveston",
            name2="Marine Biology, OCSB",
            llm_client=llm,
        )
        assert r.success is True
        assert r.name2_enriched == "Department of Marine Biology"
        # The self-reported level is recorded verbatim — the guard makes the
        # answer safe to keep, it does not make the model more confident.
        assert r.confidence == "medium"

    @pytest.mark.asyncio
    async def test_unverified_medium_is_still_rejected(self):
        """The subject swap is still refused — at the gate, not in the tier.

        Changed deliberately (§1a/§2). The tier no longer decides: it returns
        what the model said, and `enrichment.name_gate` asks the one identity
        question, once, at the write point. The protection this test exists for
        is unchanged and is asserted where it now lives — "Office of
        Purchasing" is not "Procurement Services", and the proposal reaches the
        reviewer in the flag detail instead of vanishing.
        """
        from enrichment.name_gate import evaluate, REASON_DIFFERENT_ENTITY
        from utils.name_identity import DIFFERENT

        llm = _FakeLLM({
            "official_name": "Procurement Services",
            "confidence": "medium",
            "reasoning": "",
        })
        r = await run_tier2_canonical(
            record_id="x", institution="Ohio State University",
            name2="Office of Purchasing", llm_client=llm,
        )
        assert r.success is True
        decision = evaluate(
            {"record_id": "x"}, "name2", r.name2_enriched,
            incumbent="Office of Purchasing",
        )
        assert decision.allow is False
        assert decision.verdict == DIFFERENT
        assert decision.reason == REASON_DIFFERENT_ENTITY
        assert decision.suggestion == "Procurement Services"

    @pytest.mark.asyncio
    async def test_low_confidence_is_never_kept(self):
        """A low-confidence answer is now KEPT — changed deliberately (§1a).

        The old contract made confidence a write gate, so this answer was
        discarded and the record shipped "Marine Biology, OCSB" under a flag
        saying the canonical form could not be established. The model was in
        fact right; it was only unsure of Texas A&M's exact unit wording, which
        is a statement about wording, not about which unit. Confidence now
        travels as `self_reported` provenance and decides how selectively the
        record is flagged. Identity decides the write, at the gate.
        """
        llm = _FakeLLM({
            "official_name": "Department of Marine Biology",
            "confidence": "low",
            "reasoning": "",
        })
        r = await run_tier2_canonical(
            record_id="x", institution="Texas A&M University at Galveston",
            name2="Marine Biology, OCSB", llm_client=llm,
        )
        assert r.success is True
        assert r.name2_enriched == "Department of Marine Biology"
        assert r.confidence == "low"   # …and the doubt is still recorded

    @pytest.mark.asyncio
    async def test_low_confidence_is_rejected_under_the_legacy_flag(self):
        """LLM_FALLBACK_AUTHORITATIVE off restores the discard — the A/B arm."""
        llm = _FakeLLM({
            "official_name": "Department of Marine Biology",
            "confidence": "low",
            "reasoning": "",
        })
        r = await run_tier2_canonical(
            record_id="x", institution="Texas A&M University at Galveston",
            name2="Marine Biology, OCSB", llm_client=llm,
            authoritative=False,
        )
        assert r.success is False

    @pytest.mark.asyncio
    async def test_high_confidence_path_unchanged(self):
        llm = _FakeLLM({
            "official_name": "Department of Procurement Services",
            "confidence": "high",
            "reasoning": "",
        })
        r = await run_tier2_canonical(
            record_id="x", institution="Ohio State University",
            name2="Office of Purchasing", llm_client=llm,
        )
        assert r.success is True
        assert r.name2_enriched == "Department of Procurement Services"
