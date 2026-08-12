"""Item 7: Tier 2 Canonical must not downgrade a department name to its bare
subject ("Department of Biology" → "Biology"). The canonical direction is
bare → "Department of X", never the reverse."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier2_canonical import run_tier2_canonical, _is_prefix_downgrade


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload

    async def extract_json(self, system, user):
        return self._payload


class TestPrefixDowngradeGuard:
    @pytest.mark.parametrize("original,cleaned", [
        ("Department of Biology", "Biology"),
        ("Division of Cardiology", "Cardiology"),
        ("Dept of Chemistry", "Chemistry"),
        ("School of Medicine", "Medicine"),
    ])
    def test_downgrade_detected(self, original, cleaned):
        assert _is_prefix_downgrade(original, cleaned) is True

    @pytest.mark.parametrize("original,cleaned", [
        ("Biology", "Department of Biology"),        # correct direction
        ("Department of Biology", "Department of Molecular Biology"),
        ("Chemistry", "Chemistry"),
    ])
    def test_non_downgrade(self, original, cleaned):
        assert _is_prefix_downgrade(original, cleaned) is False

    @pytest.mark.asyncio
    async def test_downgrade_rejected_by_tier(self):
        # LLM returns the bare subject — must be rejected, not accepted.
        llm = _FakeLLM({"official_name": "Biology", "confidence": "high", "reasoning": ""})
        r = await run_tier2_canonical(
            record_id="x", institution="Scripps College",
            name2="Department of Biology", llm_client=llm,
        )
        assert r.success is False
        assert r.name2_enriched is None

    @pytest.mark.asyncio
    async def test_upgrade_accepted(self):
        # Correct direction (bare → Department of X) is accepted.
        llm = _FakeLLM({"official_name": "Department of Biology", "confidence": "high", "reasoning": ""})
        r = await run_tier2_canonical(
            record_id="x", institution="Scripps College",
            name2="Biology", llm_client=llm,
        )
        assert r.success is True
        assert r.name2_enriched == "Department of Biology"