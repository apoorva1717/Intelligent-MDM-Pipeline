"""Leading account/opaque code stripped from a name field; c/o routed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.preprocess import preprocess_record, _strip_leading_opaque_code


@pytest.mark.parametrize("value, expected", [
    ("B800000123 c/o Dr. Mark Adams", "c/o Dr. Mark Adams"),
    ("NT30 Division of Cardiology", "Division of Cardiology"),
    ("SAP-42 Purchasing", "Purchasing"),
    # Untouched: house number, name-numbers, and a lone code.
    ("10901 Roosevelt Blvd N", "10901 Roosevelt Blvd N"),
    ("3M Company", "3M Company"),
    ("21st Century Fox", "21st Century Fox"),
    ("100 Black Men of America", "100 Black Men of America"),
    ("A1 Plumbing", "A1 Plumbing"),
    ("B800000123", "B800000123"),   # lone code → UC 10 clears it elsewhere
])
def test_strip_leading_code(value, expected):
    assert _strip_leading_opaque_code(value) == expected


def test_code_removed_and_co_routed_to_care_of():
    pre = preprocess_record(
        name1="Acme Corp", name2="B800000123 c/o Dr. Mark Adams",
        name3=None, contact=None, email=None,
        street1=None, street2=None, street3=None,
    )
    assert pre.name2 is None                 # code removed, c/o consumed
    assert pre.care_of == "Dr. Mark Adams"   # c/o → Care Of
    assert 10 in pre.use_cases and 15 in pre.use_cases


def test_leading_code_stripped_from_all_name_slots():
    pre = preprocess_record(
        name1="B900012345 Research Partners",
        name2="X12345 Department of Physics",
        name3="NT99 Smith Lab",
        contact=None, email=None,
        street1=None, street2=None, street3=None,
    )
    assert pre.name1 == "Research Partners"
    assert pre.name2 == "Department of Physics"
    assert pre.name3 == "Smith Lab"


def test_house_number_in_name2_not_stripped_as_code():
    # Regression: "10901 …" is a house number, not an account code.
    pre = preprocess_record(
        name1="IDEXX Reference Labs", name2="10901 Roosevelt Blvd N",
        name3=None, contact=None, email=None,
        street1=None, street2=None, street3=None,
    )
    # The full address (with house number) is moved to a street slot.
    assert pre.street1 == "10901 Roosevelt Blvd N"
