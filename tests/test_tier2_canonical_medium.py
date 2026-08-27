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
        llm = _FakeLLM({
            "official_name": "Procurement Services",
            "confidence": "medium",
            "reasoning": "",
        })
        r = await run_tier2_canonical(
            record_id="x", institution="Ohio State University",
            name2="Office of Purchasing", llm_client=llm,
        )
        assert r.success is False
        assert r.name2_enriched is None

    @pytest.mark.asyncio
    async def test_low_confidence_is_never_kept(self):
        """The relaxation is medium-only: a low answer that would pass the
        re-wording check is still rejected."""
        llm = _FakeLLM({
            "official_name": "Department of Marine Biology",
            "confidence": "low",
            "reasoning": "",
        })
        r = await run_tier2_canonical(
            record_id="x", institution="Texas A&M University at Galveston",
            name2="Marine Biology, OCSB", llm_client=llm,
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
