"""A trailing access/location qualifier on a street value is split into the
next empty street slot (not crammed in one field, not lost to unloading_point)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.address_processing import process_address, _split_location_qualifier


@pytest.mark.parametrize("value, street, qual", [
    ("300 TECH PARK Dr NEAR LOADING DOCK B", "300 TECH PARK Dr", "NEAR LOADING DOCK B"),
    ("1100 COMMERCE WAY BEHIND WAREHOUSE 4", "1100 COMMERCE WAY", "BEHIND WAREHOUSE 4"),
    ("750 INDUSTRIAL Pkwy GATE C EAST ENTRANCE", "750 INDUSTRIAL Pkwy", "GATE C EAST ENTRANCE"),
])
def test_split_detector(value, street, qual):
    assert _split_location_qualifier(value) == (street, qual)


@pytest.mark.parametrize("value", [
    "100 Loading Dock Rd",   # "Loading Dock" is part of the street name
    "300 Main St Suite 5",   # suite, not a location qualifier
    "LOADING DOCK B",        # no street → handled by unloading_point
    "300 Tech Park Dr",      # plain street
])
def test_no_false_split(value):
    assert _split_location_qualifier(value) is None


def _run(s1, s2=None):
    return asyncio.run(process_address(
        record_id="X", name1="Acme", name2=None, name3=None,
        street=s1, street_2=s2, street_3=None,
        city=None, state=None, zip_code=None, country=None,
        po_box=None, care_of_enriched=None, llm_client=None,
    ))


def test_qualifier_goes_to_next_empty_street_slot():
    r = _run("300 TECH PARK Dr NEAR LOADING DOCK B")
    assert r.street_cleaned == "300 TECH PARK Dr"
    assert r.street_2_cleaned == "NEAR LOADING DOCK B"
    assert r.unloading_point is None   # kept as a street line, not unloading


def test_qualifier_skips_occupied_slot():
    # Street 2 already holds a real address → qualifier lands in Street 3.
    r = _run("750 INDUSTRIAL Pkwy GATE C EAST ENTRANCE", s2="200 Oak Ave")
    assert r.street_cleaned == "750 INDUSTRIAL Pkwy"
    assert r.street_2_cleaned == "200 Oak Ave"
    assert r.street_3_cleaned == "GATE C EAST ENTRANCE"


def test_standalone_logistics_still_unloading_point():
    r = _run("LOADING DOCK B")
    assert r.unloading_point == "LOADING DOCK B"


def test_distribution_centre_classified_as_unloading_point():
    r = _run("SOUTHEAST DISTRIBUTION CTR")
    assert r.unloading_point == "SOUTHEAST DISTRIBUTION CTR"
    assert r.street_cleaned is None


# ── Street fields left-pack (no gaps) ────────────────────────────────────────

def _addr(street=None, street_2=None, street_3=None):
    return asyncio.run(process_address(
        record_id="X", name1="Acme", name2=None, name3=None,
        street=street, street_2=street_2, street_3=street_3,
        city=None, state=None, zip_code=None, country=None,
        po_box=None, care_of_enriched=None, llm_client=None,
    ))


def test_empty_street1_pulls_street2_up():
    r = _addr(street_2="100 Main St", street_3="200 Oak Ave")
    assert r.street_cleaned == "100 Main St"
    assert r.street_2_cleaned == "200 Oak Ave"
    assert r.street_3_cleaned is None


def test_middle_gap_closed():
    r = _addr(street="100 Main St", street_3="200 Oak Ave")
    assert r.street_cleaned == "100 Main St"
    assert r.street_2_cleaned == "200 Oak Ave"
    assert r.street_3_cleaned is None
