"""A campus / site label in a name slot is routed to a street slot.

Regression: "Sarasota Memorial Hospital" / "Sarasota Campus" arrived with the
campus in Name 2 and shipped that way. A campus says WHERE the organisation
sits, not which part of it the record is about, so it is a street line.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.address_processing import process_address
from enrichment.preprocess import _site_fragment, preprocess_record


@pytest.mark.parametrize("value", [
    "Sarasota Campus",
    "SARASOTA CAMPUS",
    "West Campus",
    "Golden Colorado Campus",
    "Queens Campus",
    "Oxford Science Park",
    "Cambridge Science Park",
])
def test_site_labels_are_detected(value):
    assert _site_fragment(value) == value


@pytest.mark.parametrize("value", [
    "Campus Health Services",      # a department that sits on a campus
    "Campus Store",
    "Campus Police Department",
    "North Campus Research Complex",
    "Department of Campus Planning",
    "Campus Drive",                # a street name
    "1700 Campus Dr",
    "Campus Box 7212",             # a mail stop
    "Campus",                      # no qualifier — not a site label
    "Sarasota Memorial Hospital",
])
def test_non_site_values_stay_in_the_name_block(value):
    assert _site_fragment(value) is None


class TestPreprocessRouting:
    def test_campus_in_name2_moves_to_the_next_street_slot(self):
        res = preprocess_record(
            name1="Sarasota Memorial Hospital", name2="Sarasota Campus",
            name3=None, contact=None, email=None,
            street1="S Tamiami Trail", street2=None, street3=None,
            house_number="1700",
        )
        assert res.name1 == "Sarasota Memorial Hospital"
        assert res.name2 is None
        assert res.street1 == "S Tamiami Trail"
        assert res.street2 == "Sarasota Campus"

    def test_a_department_named_after_a_campus_keeps_its_slot(self):
        res = preprocess_record(
            name1="Sarasota Memorial Hospital", name2="Campus Health Services",
            name3=None, contact=None, email=None,
            street1="S Tamiami Trail", street2=None, street3=None,
        )
        assert res.name2 == "Campus Health Services"
        assert res.street2 is None

    def test_name1_is_never_emptied(self):
        res = preprocess_record(
            name1="West Campus", name2=None, name3=None,
            contact=None, email=None,
            street1="S Tamiami Trail", street2=None, street3=None,
        )
        assert res.name1 == "West Campus"


class TestAddressStageSafetyNet:
    """A tier that writes a campus back into a name field after preprocessing
    ran is corrected by the address stage."""

    def test_campus_written_into_name2_by_a_tier_is_pulled_out(self):
        res = asyncio.run(process_address(
            record_id="x", name1="Sarasota Memorial Hospital",
            name2="Sarasota Campus", name3=None,
            street="1700 S Tamiami Trail", street_2=None, street_3=None,
            city="Sarasota", state="FL", zip_code="34239", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        ))
        assert res.street_cleaned == "1700 S Tamiami Trail"
        assert res.street_2_cleaned == "Sarasota Campus"
        assert res.name_overrides["name2"] is None

    def test_campus_already_in_a_street_slot_survives_the_address_stage(self):
        res = asyncio.run(process_address(
            record_id="x", name1="Sarasota Memorial Hospital",
            name2=None, name3=None,
            street="1700 S Tamiami Trail", street_2="Sarasota Campus",
            street_3=None,
            city="Sarasota", state="FL", zip_code="34239", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        ))
        assert res.street_2_cleaned == "Sarasota Campus"

    def test_a_full_name_block_leaves_the_campus_where_it_is(self):
        """Street slots full → the campus is not dropped."""
        res = asyncio.run(process_address(
            record_id="x", name1="Org", name2="West Campus", name3=None,
            street="1 A St", street_2="2 B St", street_3="3 C St",
            street_4="4 D St", street_5="5 E St",
            city="X", state="FL", zip_code="00000", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        ))
        assert "name2" not in res.name_overrides
