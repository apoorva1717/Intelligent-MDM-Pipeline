"""Item 6: Tier 3 must not emit address content into name fields, and a blank
input Name 2 that only Tier 3 populated must be dropped unless high-confidence."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.orchestrator import finalise
from enrichment.tier3_llm import run_tier3, _is_address_like_name


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload

    async def extract_json(self, system, user):
        return self._payload


class TestAddressLikeNameDetector:
    @pytest.mark.parametrize("value,street", [
        ("ASTER HOUSE, 2A University ROAD, BELFAST BT7 1NH", None),      # postcode + Road
        ("Charité - University Medicine Berlin, Augustenburger Platz 1", None),  # Platz + number
        ("600 N Wolfe St", None),                                        # street + number
        ("Department of Physics, 100 Science Dr", "100 Science Dr"),     # overlap w/ street
    ])
    def test_address_like_rejected(self, value, street):
        assert _is_address_like_name(value, street) is True

    @pytest.mark.parametrize("value", [
        "Department of Physics",
        "Massachusetts Institute of Technology",
        "Scripps Research Institute",
    ])
    def test_real_names_pass(self, value):
        assert _is_address_like_name(value, None) is False


class TestRunTier3RejectsAddress:
    @pytest.mark.asyncio
    async def test_address_name2_rejected(self):
        llm = _FakeLLM({
            "name1_suggestion": None,
            "name2_suggestion": "ASTER HOUSE, 2A University ROAD, BELFAST BT7 1NH",
            "name3_suggestion": None,
            "confidence": "medium",
        })
        r = await run_tier3(
            record_id="x", name1="Queen's University Belfast", name2=None,
            name3=None, contact=None,
            street="ASTER HOUSE, 2A University ROAD", city="Belfast",
            state=None, zip_code="BT7 1NH", country="UK", llm_client=llm,
        )
        assert r.name2_suggestion is None
        assert r.flag_for_review is True
        assert "address-like" in r.flag_reason

    @pytest.mark.asyncio
    async def test_real_name2_kept(self):
        llm = _FakeLLM({
            "name1_suggestion": None,
            "name2_suggestion": "Department of Physics",
            "name3_suggestion": None,
            "confidence": "high",
        })
        r = await run_tier3(
            record_id="x", name1="MIT", name2=None, name3=None, contact=None,
            street="77 Massachusetts Ave", city="Cambridge", state="MA",
            zip_code="02139", country="US", llm_client=llm,
        )
        assert r.name2_suggestion == "Department of Physics"


class TestFinaliseBlankName2Guard:
    def _dict(self, **ov):
        base = {f: None for f in (
            "name1_enriched", "name2_enriched", "name3_enriched", "name4_enriched",
            "name1_original", "name2_original", "name3_original", "name4_original",
            "care_of_enriched", "contact_enriched", "email_enriched",
            "care_of_original", "contact_original", "email_original",
            "street1_original", "street2_original", "street3_original",
            "street4_original", "street5_original",
            "street_cleaned", "street_2_cleaned", "street_3_cleaned",
            "street_4_cleaned", "street_5_cleaned",
            "department_domain", "_search_term_1_original", "_ror_acronym",
            "website_url", "domain",
        )}
        base.update({
            "record_id": "X", "record_type": "research_institution",
            "search_term_1": None, "search_term_2": None,
            "flag_for_review": False, "flag_reason": None, "address_issues": [],
        })
        base.update(ov)
        return base

    def test_tier3_blank_name2_medium_dropped(self):
        # The guard only applies to a Name 2 that TIER 3 populated (flagged
        # _name2_from_tier3), not one preprocessing routed from the street.
        out = finalise(self._dict(
            tier_used=3, source="LLM", confidence="medium",
            name2_original=None, name2_enriched="St. Louis Site",
            _name2_from_tier3=True,
        ), time.perf_counter())
        assert out["name2_enriched"] is None
        assert out["flag_for_review"] is True

    def test_preprocess_routed_name2_not_dropped(self):
        # A Name 2 routed from the street by preprocessing (no _name2_from_tier3)
        # must survive even when Tier 3 ran and the input Name 2 was blank.
        out = finalise(self._dict(
            tier_used=3, source="LLM", confidence="medium",
            name2_original=None, name2_enriched="Finance/Procurement",
        ), time.perf_counter())
        assert out["name2_enriched"] == "Finance/Procurement"

    def test_tier3_blank_name2_high_kept(self):
        out = finalise(self._dict(
            tier_used=3, source="LLM", confidence="high",
            name2_original=None, name2_enriched="Department of Chemistry",
        ), time.perf_counter())
        assert out["name2_enriched"] == "Department of Chemistry"

    def test_tier3_populated_input_name2_not_dropped(self):
        # Input Name 2 was present → the guard does not apply.
        out = finalise(self._dict(
            tier_used=3, source="LLM", confidence="medium",
            name2_original="Chem Dept", name2_enriched="Department of Chemistry",
        ), time.perf_counter())
        assert out["name2_enriched"] == "Department of Chemistry"