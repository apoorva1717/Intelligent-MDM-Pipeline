"""A floor written number-first ("5th Fl") leaves the name block with the
street line beside it.

Regression: row 13340804 supplied Name 2 "10920 Wilshire Blvd, 5th Fl". UC 9
extracted the street and left "5th Fl" behind, because the sub-location
pattern only knew the marker-first form ("Floor 3"). Name 2 shipped "5th Fl"
flagged low-confidence-unchanged. "Fl" is also Florida, so it only counts next
to a 1-3 digit floor number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import _extract_addresses, preprocess_record


def test_ucla_row_leaves_no_floor_in_name2():
    res = preprocess_record(
        "Univ of Calif Los Angeles -", "10920 Wilshire Blvd, 5th Fl", None,
        None, None, "10920 WILSHIRE BLVD, 5TH", None, None,
        city="Los Angeles", region="CA",
    )
    assert res.name2 is None
    streets = [res.street2, res.street3, res.street4, res.street5]
    assert "5th Fl" in streets
    assert "10920 Wilshire Blvd" in streets


@pytest.mark.parametrize("value", [
    "5th Fl", "22nd Floor", "3 Fl", "1st Fl.", "Fl 5", "Fl. 3rd",
])
def test_floor_forms_are_extracted(value):
    found, rest = _extract_addresses(value)
    assert found and rest == ""


def test_floor_after_department_is_split_off():
    found, rest = _extract_addresses("Dept of Surgery 5th Floor")
    assert found == ["5th Floor"]
    assert rest == "Dept of Surgery"


@pytest.mark.parametrize("value", [
    "Univ of FL Gainesville",
    "FL Dept of Health",
    "Fl Atlantic Univ",
    "Tampa, FL 33601",
    "21st Century Fox",
    "Area 51 Lab",
])
def test_florida_and_digit_names_are_not_floors(value):
    found, rest = _extract_addresses(value)
    assert found == []
    assert rest == value
