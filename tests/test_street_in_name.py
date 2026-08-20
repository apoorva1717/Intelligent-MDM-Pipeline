"""A street address that a tier wrote into a name field is moved into an
empty street slot by the address stage.

Regression: the Tier 3 LLM (or any post-preprocess stage) can place a
campus-style address such as "104 Rhines Hall" into Name 2 AFTER UC 9 ran
in preprocessing. ``process_address`` re-runs the same extraction so the
address ends up in a street field and the name output is cleaned.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time

from enrichment.address_processing import process_address, merge_into_result
from enrichment.orchestrator import finalise
from tests.conftest import make_record
from enrichment.search_terms import derive_search_terms


async def _run(**kw):
    base = dict(
        record_id="x",
        name1="University of Florida", name2=None, name3=None,
        street=None, street_2=None, street_3=None, street_4=None, street_5=None,
        city="Gainesville", state="FL", zip_code="32611", country="US",
        po_box=None, care_of_enriched=None, llm_client=None,
    )
    base.update(kw)
    return await process_address(**base)


class TestStreetInName:
    @pytest.mark.asyncio
    async def test_house_number_building_in_name2_moves_to_empty_street(self):
        # The reported case: Name 2 = "104 Rhines Hall", Street 1 already has
        # a real address — the building address lands in Street 2.
        res = await _run(name2="104 Rhines Hall", street="549 GALE LEMERAND Dr")
        assert res.street_cleaned == "549 GALE LEMERAND Dr"
        assert res.street_2_cleaned == "104 Rhines Hall"
        assert res.name_overrides == {"name2": None}

    @pytest.mark.asyncio
    async def test_address_in_name2_fills_street1_when_empty(self):
        res = await _run(name2="104 Rhines Hall", street=None)
        assert res.street_cleaned == "104 Rhines Hall"
        assert res.name_overrides == {"name2": None}

    @pytest.mark.asyncio
    async def test_standard_street_in_name2_moves(self):
        res = await _run(name2="123 Main St", street=None)
        assert res.street_cleaned == "123 Main St"
        assert res.name_overrides == {"name2": None}

    @pytest.mark.asyncio
    async def test_name2_remainder_kept_when_only_part_is_address(self):
        # A department name with a trailing building address: the department
        # stays in Name 2, the address moves to a street slot.
        res = await _run(name2="Department of Physics 100 Smith Hall", street=None)
        assert res.street_cleaned == "100 Smith Hall"
        assert res.name_overrides == {"name2": "Department of Physics"}

    @pytest.mark.asyncio
    async def test_plain_department_in_name2_untouched(self):
        res = await _run(name2="Department of Chemistry", street=None)
        assert res.street_cleaned is None
        assert res.name_overrides == {}

    @pytest.mark.asyncio
    async def test_institution_in_name1_never_moved(self):
        # Name 1 is the institution and must stay put even if it had digits.
        res = await _run(name1="University of Florida", name2=None, street=None)
        assert res.name_overrides == {}

    @pytest.mark.asyncio
    async def test_address_in_name3_moves(self):
        res = await _run(name2="Department of Chemistry", name3="104 Rhines Hall",
                         street=None)
        assert res.street_cleaned == "104 Rhines Hall"
        assert res.name_overrides == {"name3": None}

    @pytest.mark.asyncio
    async def test_merge_clears_name_field_and_marks_cleared(self):
        # merge_into_result writes the cleaned name back and records the slot
        # as cleared so finalise() does not restore the original.
        res = await _run(name2="104 Rhines Hall", street="549 GALE LEMERAND Dr")
        result_dict: dict = {
            "name2_enriched": "104 Rhines Hall",
            "name2_original": "104 Rhines Hall",
        }
        merge_into_result(result_dict, res)
        assert result_dict["name2_enriched"] is None
        assert "name2" in result_dict["_preprocess_cleared"]
        assert result_dict["street_2_cleaned"] == "104 Rhines Hall"


def _finalise_dict(**overrides):
    """A finalise() input dict pre-seeded with the fields it reads."""
    base = {
        f: None
        for f in (
            "name1_enriched", "name2_enriched", "name3_enriched", "name4_enriched",
            "name1_original", "name2_original", "name3_original", "name4_original",
            "care_of_enriched", "contact_enriched", "email_enriched",
            "street1_enriched", "street2_enriched", "street3_enriched",
            "street4_enriched", "street5_enriched",
            "care_of_original", "contact_original", "email_original",
            "street1_original", "street2_original", "street3_original",
            "street4_original", "street5_original",
            "street_cleaned", "street_2_cleaned", "street_3_cleaned",
            "street_4_cleaned", "street_5_cleaned",
            "department_domain", "_search_term_1_original", "_ror_acronym",
            "website_url", "domain",
        )
    }
    base.update({
        "record_id": "X", "record_type": "research_institution",
        "search_term_1": None, "search_term_2": None,
        "flag_for_review": False, "flag_reason": None, "address_issues": [],
    })
    base.update(overrides)
    return make_record(**base)


class TestFinaliseSafetyNet:
    def test_passthrough_restored_address_is_moved(self):
        # name2_enriched is None but the original (an address) is restored by
        # finalise's passthrough — the safety net must then move it to a
        # street slot and leave Name 2 empty.
        out = finalise(_finalise_dict(
            name1_enriched="University of Florida",
            name1_original="University of Florida",
            name2_original="104 Rhines Hall",
            street_cleaned="549 Gale Lemerand Dr",
        ), time.monotonic())
        assert out["name2_enriched"] is None
        assert out["street_cleaned"] == "549 Gale Lemerand Dr"
        assert out["street_2_cleaned"] == "104 Rhines Hall"
        # search_term_2 must not be mined from the moved address.
        assert out["search_term_2"] is None

    def test_tier_written_address_in_name2_is_moved(self):
        out = finalise(_finalise_dict(
            name1_enriched="University of Florida",
            name2_enriched="104 Rhines Hall",
        ), time.monotonic())
        assert out["name2_enriched"] is None
        assert out["street_cleaned"] == "104 Rhines Hall"

    def test_real_department_in_name2_is_kept(self):
        out = finalise(_finalise_dict(
            name1_enriched="University of Florida",
            name2_enriched="Department of Chemistry",
        ), time.monotonic())
        assert out["name2_enriched"] == "Department of Chemistry"
        assert out["street_cleaned"] is None

    def test_location_fragment_in_name3_is_moved(self):
        # "Annex D Pod 2" is a pure address sub-location, not a department —
        # _extract_addresses misses it, so the location-fragment branch must
        # catch it and move it to a street slot.
        out = finalise(_finalise_dict(
            name1_enriched="University of Florida",
            name3_enriched="Annex D Pod 2",
            street_cleaned="549 Gale Lemerand Dr",
        ), time.monotonic())
        assert out["name3_enriched"] is None
        assert out["street_cleaned"] == "549 Gale Lemerand Dr"
        assert out["street_2_cleaned"] == "Annex D Pod 2"

    def test_location_fragment_in_name2_fills_empty_street(self):
        out = finalise(_finalise_dict(
            name1_enriched="University of Florida",
            name2_enriched="Wing C",
        ), time.monotonic())
        assert out["name2_enriched"] is None
        assert out["street_cleaned"] == "Wing C"


class TestSearchTermAddressGuard:
    """search_term_2 is derived from the ENRICHED Name 2 only.

    finalise() retains the input value in the enriched slot wherever no tier
    changed it, so a blank enriched slot at derivation time means enrichment
    deliberately emptied the field (an address, an email, a contact name) —
    the pre-enrichment original is never mined for a handle.
    """

    def test_original_never_used_as_unit_handle(self):
        st1, st2 = derive_search_terms({
            "name1_enriched": "University of Florida",
            "name2_enriched": None,
            "name2_original": "104 Rhines Hall",
        })
        assert st2 is None

    def test_department_original_not_used_when_enrichment_cleared_it(self):
        st1, st2 = derive_search_terms({
            "name1_enriched": "University of Florida",
            "name2_enriched": None,
            "name2_original": "Department of Chemistry",
        })
        assert st2 is None

    def test_real_department_used_from_the_enriched_slot(self):
        st1, st2 = derive_search_terms({
            "name1_enriched": "University of Florida",
            "name2_enriched": "Department of Chemistry",
            "name2_original": "Department of Chemistry",
        })
        assert st2 == "CHEMISTRY"
