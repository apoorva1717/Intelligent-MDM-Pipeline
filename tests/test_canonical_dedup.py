"""UC 12 dedup recognises surface variants of the same department."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.preprocess import preprocess_record, _collapse_repeated_phrase


@pytest.mark.parametrize("value, expected", [
    ("Department of Central Receiving Department of Central Receiving",
     "Department of Central Receiving"),
    ("Receiving Receiving", "Receiving"),
    ("100 Main St 100 Main St", "100 Main St"),
    # Not a pure repetition — left unchanged.
    ("Department of Physics", "Department of Physics"),
    ("Department of Central Receiving Department of Physics",
     "Department of Central Receiving Department of Physics"),
])
def test_collapse_repeated_phrase(value, expected):
    assert _collapse_repeated_phrase(value) == expected


def test_repeated_phrase_collapsed_in_name_field():
    r = preprocess_record(
        name1="University of Florida",
        name2="Department of Central Receiving Department of Central Receiving",
        name3=None, contact=None, email=None,
        street1=None, street2=None, street3=None,
    )
    assert r.name2 == "Department of Central Receiving"


def _run(name2, name3):
    return preprocess_record(
        name1="University of Florida", name2=name2, name3=name3,
        contact=None, email=None, street1=None, street2=None, street3=None,
    )


@pytest.mark.parametrize("name3", [
    "Department of Main Receiving",   # exact
    "Main Receiving Department",      # reversed order
    "Main Receiving Dept",            # abbreviation
    "MAIN RECEIVING DEPT",            # abbreviation + case
])
def test_duplicate_department_variant_cleared(name3):
    r = _run("Department of Main Receiving", name3)
    assert r.name2 == "Department of Main Receiving"
    assert r.name3 is None


def test_distinct_departments_both_kept():
    r = _run("Department of Physics", "Department of Chemistry")
    assert r.name2 == "Department of Physics"
    assert r.name3 == "Department of Chemistry"


def test_typo_near_duplicate_cleared():
    # Name 3 is the same department with a one-character typo.
    r = _run("Department of Main Receiving", "Department of Main Receivingt")
    assert r.name2 == "Department of Main Receiving"
    assert r.name3 is None


def test_similar_but_distinct_departments_kept():
    # Physics vs Physiology are similar but NOT the same — both kept.
    r = _run("Department of Physics", "Department of Physiology")
    assert r.name2 == "Department of Physics"
    assert r.name3 == "Department of Physiology"
