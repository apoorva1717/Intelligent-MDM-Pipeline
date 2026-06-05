"""Named building in a name field is routed to the Building output."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.preprocess import (
    preprocess_record, _named_building, _location_fragment,
)


def _run(name2, name3=None, name4=None):
    return preprocess_record(
        name1="HCA Florida University Hospital",
        name2=name2, name3=name3, name4=name4,
        contact=None, email=None, street1=None, street2=None, street3=None,
    )


# ── Detector ────────────────────────────────────────────────────────────────

def test_detects_named_building():
    assert _named_building("Neil Armstrong Operations and Checkout Building") \
        == "Neil Armstrong Operations and Checkout Building"
    assert _named_building("Engineering Building") == "Engineering Building"
    assert _named_building("Research I Bldg") == "Research I Bldg"


def test_rejects_non_buildings():
    assert _named_building("Department of Chemistry") is None
    assert _named_building("Building 5") is None          # identifier form
    assert _named_building("Bldg") is None                # bare marker
    assert _named_building("") is None
    assert _named_building(None) is None


# ── Routing through preprocess ───────────────────────────────────────────────

def test_building_moved_out_of_name2():
    pre = _run("Neil Armstrong Operations and Checkout Building")
    assert pre.building == "Neil Armstrong Operations and Checkout Building"
    assert pre.name2 is None
    # The person-like prefix is NOT misextracted as a contact.
    assert pre.contact is None


def test_building_in_name3_routed_and_name2_kept():
    pre = _run("Department of Physics", name3="Engineering Building")
    assert pre.building == "Engineering Building"
    assert pre.name2 == "Department of Physics"
    assert pre.name3 is None


def test_named_building_in_street_routed_to_building_field():
    pre = preprocess_record(
        name1="NASA", name2=None, name3=None,
        contact=None, email=None,
        street1="Neil Armstrong Operations and Checkout Building",
        street2=None, street3=None,
    )
    assert pre.building == "Neil Armstrong Operations and Checkout Building"
    assert pre.street1 is None


def test_named_building_in_later_street_slot():
    pre = preprocess_record(
        name1="NASA", name2=None, name3=None,
        contact=None, email=None,
        street1="100 Main St", street2="Engineering Building", street3=None,
    )
    assert pre.building == "Engineering Building"
    assert pre.street1 == "100 Main St"
    assert pre.street2 is None


def test_numbered_hall_in_street_not_routed_to_building():
    pre = preprocess_record(
        name1="University of Florida", name2=None, name3=None,
        contact=None, email=None,
        street1="100 Rhines Hall", street2=None, street3=None,
    )
    assert pre.building is None
    assert pre.street1 == "100 Rhines Hall"


def test_real_department_not_treated_as_building():
    pre = _run("Department of Chemistry")
    assert pre.building is None
    assert pre.name2 == "Department of Chemistry"


# ── Numbered hall/building address → street slot (not the Building field) ─────

def _run_streets(name2, street1=None, street2=None, street3=None):
    return preprocess_record(
        name1="University of Florida", name2=name2, name3=None,
        contact=None, email=None,
        street1=street1, street2=street2, street3=street3,
    )


def test_numbered_hall_goes_to_first_empty_street():
    pre = _run_streets("100 Rhines Hall")
    assert pre.name2 is None
    assert pre.street1 == "100 Rhines Hall"
    assert pre.building is None  # NOT the Building field — it has a house number


def test_numbered_hall_cascades_when_street1_occupied():
    pre = _run_streets("100 Rhines Hall", street1="123 Main St")
    assert pre.street1 == "123 Main St"
    assert pre.street2 == "100 Rhines Hall"


def test_numbered_hall_cascades_to_street3():
    pre = _run_streets("104 Rhines Hall", street1="123 Main St", street2="Suite 5")
    assert pre.street3 == "104 Rhines Hall"


@pytest.mark.parametrize("value", [
    "Rhines Hall 104",      # number trails the building name
    "RHINES HALL 104",
    "Smith Building 200",
    "Rhines Hall 104B",
])
def test_number_after_hall_routed_to_street(value):
    pre = _run_streets(value)
    assert pre.name2 is None
    assert pre.street1 == value


@pytest.mark.parametrize("value", ["Rhines Hall", "Carnegie Hall"])
def test_bare_hall_without_number_stays_in_name(value):
    # No number → not treated as a street address.
    pre = _run_streets(value)
    assert pre.name2 == value
    assert pre.street1 is None


def test_numbered_building_goes_to_street_not_building_field():
    pre = _run_streets("20 Smith Building")
    assert pre.street1 == "20 Smith Building"
    assert pre.building is None


def test_org_starting_with_number_not_an_address():
    # "100 Black Men of America" has no building/street word — leave it alone.
    pre = _run_streets("100 Black Men of America")
    assert pre.street1 is None
    assert pre.name2 == "100 Black Men of America"


# ── Location fragments (Wing/Annex/Pod…) in a name field → street slot ────────

def _run_name3(name3, street1=None, street2=None, street3=None):
    return preprocess_record(
        name1="University of Florida", name2="Department of Physics",
        name3=name3, contact=None, email=None,
        street1=street1, street2=street2, street3=street3,
    )


@pytest.mark.parametrize("value", [
    "Wing C", "Annex D Pod 2", "Bay 4", "Floor 3 Room 12", "Suite 400",
    "Wing-C", "Pod-2", "WING-C",   # hyphen separator
])
def test_location_fragment_detected(value):
    assert _location_fragment(value) == value


@pytest.mark.parametrize("value", [
    "Wing Commander",          # second token is a word, not an identifier
    "Department of Chemistry",
    "Chemistry Lab",
    "Smith Hall",              # named hall, no descriptor+id structure
])
def test_non_location_fragment_ignored(value):
    assert _location_fragment(value) is None


def test_location_fragment_moved_to_first_empty_street():
    pre = _run_name3("Wing C")
    assert pre.name3 is None
    assert pre.street1 == "Wing C"
    assert pre.name2 == "Department of Physics"   # the real dept is untouched


def test_multi_part_fragment_kept_as_one_unit():
    # "Annex D Pod 2" must land in ONE street cell, not be split.
    pre = _run_name3("Annex D Pod 2", street1="100 Main St")
    assert pre.street1 == "100 Main St"
    assert pre.street2 == "Annex D Pod 2"
    assert pre.name3 is None
