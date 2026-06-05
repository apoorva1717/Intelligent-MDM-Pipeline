"""An organisation name sitting in a street field moves to the name block."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.preprocess import (
    preprocess_record, _street_is_org_name, _street_is_department,
)


def _run(**kw):
    base = dict(
        name1=None, name2=None, name3=None, contact=None, email=None,
        street1=None, street2=None, street3=None,
    )
    base.update(kw)
    return preprocess_record(**base)


@pytest.mark.parametrize("value, expected", [
    ("University of Miami Hospital", True),
    ("Acme Corporation Inc", True),
    ("100 University Ave", False),   # address, not org
    ("1 Hospital Dr", False),        # address
    ("University Ave", False),       # street name
    ("300 Main St", False),
])
def test_detector(value, expected):
    assert _street_is_org_name(value) is expected


def test_becomes_name1_when_name1_blank_and_dept_present():
    r = _run(name2="Department of Cardiology", street1="University of Miami Hospital")
    assert r.name1 == "University of Miami Hospital"
    assert r.name2 == "Department of Cardiology"   # department untouched
    assert r.street1 is None


def test_becomes_name1_and_pushes_department_down():
    r = _run(name1="Department of Cardiology", street1="University of Miami Hospital")
    assert r.name1 == "University of Miami Hospital"
    assert r.name2 == "Department of Cardiology"   # pushed out of Name 1
    assert r.street1 is None


def test_goes_to_empty_name_slot_when_institution_present():
    r = _run(name1="University of Miami", street1="University of Miami Hospital")
    assert r.name1 == "University of Miami"
    assert r.name2 == "University of Miami Hospital"
    assert r.street1 is None


def test_real_street_left_alone():
    r = _run(name1="Acme", street1="300 Main St")
    assert r.name1 == "Acme"
    assert r.street1 == "300 Main St"


# ── Department in a street field ─────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("Department of Neuroscience", True),
    ("Smith Lab", True),
    ("100 Main St", False),
    ("Department Dr", False),                 # street, not a department
    ("University of Miami Hospital", False),   # institution, not a department
])
def test_department_detector(value, expected):
    assert _street_is_department(value) is expected


def test_street_department_removed_when_one_already_present():
    r = _run(
        name1="University of Miami", name2="Department of Cardiology",
        street1="Department of Neuroscience",
    )
    assert r.name2 == "Department of Cardiology"   # existing dept kept
    assert r.street1 is None                       # redundant one removed


def test_street_department_removed_when_group_present():
    r = _run(
        name1="University of Miami", name2="Smith Lab",
        street1="Department of Neuroscience",
    )
    assert r.name2 == "Smith Lab"
    assert r.street1 is None


def test_street_department_moved_when_none_present():
    r = _run(name1="University of Miami", street1="Department of Neuroscience")
    assert r.name2 == "Department of Neuroscience"
    assert r.street1 is None


# ── Logistics facility in a street stays out of the name block ───────────────

@pytest.mark.parametrize("value", [
    "SOUTHEAST DISTRIBUTION CTR",
    "Memphis Distribution Center",
    "Acme Fulfillment Ctr",
])
def test_distribution_centre_not_treated_as_department(value):
    # It reads as a "...Center" unit but is a logistics/unloading location.
    assert _street_is_department(value) is False
    assert _street_is_org_name(value) is False
    r = _run(name1="Acme Corp", street1=value)
    assert r.name2 is None          # NOT moved to a name slot
    assert r.street1 == value       # stays in the address


def test_warehouse_road_is_a_street_not_logistics():
    r = _run(name1="Acme Corp", street1="100 Warehouse Rd")
    assert r.street1 == "100 Warehouse Rd"


@pytest.mark.parametrize("institution", [
    "Scripps Research Institute",   # reads as a unit construction
    "Harvard Medical School",
    "Moffitt Cancer Center",        # reads as granular
    "University of Florida",
])
def test_street_department_moved_not_removed_under_unit_like_name1(institution):
    # Regression: a unit-like institution in Name 1 must NOT be mistaken for an
    # existing department — the street department is moved, not dropped.
    r = _run(name1=institution, street1="CHEMISTRY DEPARTMENT")
    assert r.street1 is None
    assert r.name2 == "Chemistry Department"   # title-cased; finalise → "Department of Chemistry"
