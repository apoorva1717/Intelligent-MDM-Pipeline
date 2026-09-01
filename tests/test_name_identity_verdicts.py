"""The three-verdict identity comparison (§1b).

`classify_name_change` replaces a boolean gate that discarded correct answers.
Its contract is asymmetric on purpose: SAME and UNDECIDABLE both WRITE, and
only DIFFERENT refuses, so the rows that matter most here are the DIFFERENT
ones — every one of them is a name the pipeline must never ship.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from utils.name_identity import (
    DIFFERENT,
    SAME,
    UNDECIDABLE,
    classify_name_change,
    is_pure_repair,
)


# ── The five verdicts named in the specification ─────────────────────────────

@pytest.mark.parametrize("original, proposal, expected", [
    ("Bio-Rad Lab Inc", "Bio-Rad Laboratories, Inc.", SAME),
    ("US Environmental Protection Agency",
     "United States Environmental Protection Agency", SAME),
    ("VA MC West LA Visn 22",
     "VA Greater Los Angeles Healthcare System", UNDECIDABLE),
    ("Bio-Rad Lab Inc",
     "State Key Laboratory of Digital Medical Engineering", DIFFERENT),
    ("Aramco Services Company",
     "Saudi Aramco Medical Services Organization", DIFFERENT),
])
def test_specified_verdicts(original, proposal, expected):
    assert classify_name_change(original, proposal) == expected


# ── Every rejection row the boolean guard already enforced ───────────────────
# These are the hallucination wall. `canonical_preserves_identity` rejects each
# one and keeps doing so (tests/test_canonical_identity.py); the three-verdict
# function must reach the same conclusion, because UNDECIDABLE would WRITE it.

@pytest.mark.parametrize("original, proposal", [
    ("Iso Group Inc", "CoStar Group"),
    ("Liberty Health Sciences", "Liberty Science Center"),
    ("USDA Agricultural Research Service", "Agricultural Research Service"),
    ("Precision Instruments Co.", "World Precision Instruments"),
    ("Global NMR Solutions", "Global Solutions for Infectious Diseases"),
    ("Ibero-American Research Foundation",
     "American Hearing Research Foundation"),
    ("NASA Jet Propulsion Laboratory", "Jet Propulsion Laboratory"),
    ("Acme Widgets LLC", "Globex Corporation"),
    ("International Paper", "International Business Machines"),
])
def test_entity_swaps_are_different(original, proposal):
    assert classify_name_change(original, proposal) == DIFFERENT


# ── Repairs the old 4-char floor discarded ───────────────────────────────────

@pytest.mark.parametrize("original, proposal", [
    # `lab` ↔ `laboratories`: min length 3, so `_token_covers` failed it.
    ("Bio-Rad Lab Inc", "Bio-Rad Laboratories"),
    # `us` ↔ `united states`: min length 2.
    ("US Geological Survey", "United States Geological Survey"),
    # A truncation the SAP field imposed, completed.
    ("Palo Alto Veterans Institute for Researc",
     "Palo Alto Veterans Institute for Research"),
    # A misspelling corrected.
    ("Thermo Fisher Scientifc", "Thermo Fisher Scientific"),
    # Reformatting and legal-suffix conventions.
    ("Thermal Scientific Inc", "Thermal Scientific, Incorporated"),
    ("Renovo Solutions, LLC", "Renovo Solutions LLC"),
])
def test_repairs_are_same(original, proposal):
    assert classify_name_change(original, proposal) == SAME


def test_acronym_expansion_is_not_a_new_word():
    # UTSW → "UT Southwestern": the expansion must not read as an invented
    # distinctive token, or every abbreviated record is rejected.
    assert classify_name_change(
        "UTSW Medical Center", "UT Southwestern Medical Center",
    ) == SAME


@pytest.mark.parametrize("original, proposal", [
    ("California Dept of Public Health", "California Department of Public Health"),
    ("Exxonmobil Research & Engineering Co",
     "ExxonMobil Research and Engineering Company"),
])
def test_expansion_of_the_records_own_abbreviations(original, proposal):
    assert classify_name_change(original, proposal) == SAME


# ── UNDECIDABLE: unmatchable, but nothing contradicted ───────────────────────

@pytest.mark.parametrize("original, proposal", [
    # Opaque internal codes the canonical name has no duty to repeat.
    ("VAMC West LA Visn 22", "VA Greater Los Angeles Healthcare System"),
    ("Wyss Inst Accounts Payable", "Wyss Institute"),
])
def test_unmatchable_codes_are_undecidable_not_rejected(original, proposal):
    assert classify_name_change(original, proposal) in (SAME, UNDECIDABLE)
    assert classify_name_change(original, proposal) != DIFFERENT


# ── Permissive at the edges ──────────────────────────────────────────────────

@pytest.mark.parametrize("original, proposal", [
    (None, "Anything"),
    ("Anything", None),
    ("", "Anything"),
])
def test_blank_sides_never_block(original, proposal):
    assert classify_name_change(original, proposal) == SAME


# ── is_pure_repair drives flag selectivity, not the write ────────────────────

@pytest.mark.parametrize("original, proposal, expected", [
    ("Bio-Rad Lab Inc", "Bio-Rad Laboratories, Inc.", True),
    ("California Dept of Public Health",
     "California Department of Public Health", True),
    ("US Geological Survey", "United States Geological Survey", True),
    # A substantial rewrite — the reviewer should see this one.
    ("VA MC West LA Visn 22", "VA Greater Los Angeles Healthcare System", False),
])
def test_pure_repair(original, proposal, expected):
    assert is_pure_repair(original, proposal) is expected
