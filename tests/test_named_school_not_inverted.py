"""A trailing "School" is the last word of a name, not a unit word.

Regression: Boston Children's 13141468 stated Name 2 "Harvard Medical School"
and shipped "School of Harvard Medical" — a form nobody calls it, no registry
lists, and which the record still carried the `input:low` provenance of,
because the inversion runs in finalise as a `transform` (it restyles a value
without changing its attribution). So the row read "left exactly as supplied"
beside a value that had not been.

Two separate defects, one row:

* `canonicalise_unit_name` inverted every "<X> School". Institute, Laboratory,
  Center and College were taken out of that rule earlier for exactly this
  reason; School belongs with them.
* the grounded lane CONFIRMED "Harvard Medical School" against the web, and
  the department fall-through — which reads `grounded.name2` and returns when
  there is no proposal — dropped the confirmation. The slot was flagged as
  unestablished by the same run that established it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _init_result, finalise
from tests.conftest import seed
from utils.text_utils import canonicalise_unit_name


# ── Named schools keep their name ────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "Harvard Medical School",
    "Harvard Business School",
    "Stanford Law School",
    "London Business School",
    "Wharton School",
    # The unit word trails a qualifier that is not a subject: "School of
    # Medical" and "School of Graduate" are not English.
    "Medical School",
    "Graduate School",
])
def test_a_trailing_school_is_left_as_supplied(value):
    assert canonicalise_unit_name(value) == value


@pytest.mark.parametrize("value,expected", [
    # The prefix pass still normalises an already-canonical form — a casing
    # and abbreviation fix, not an inversion.
    ("School of Medicine", "School of Medicine"),
    ("school of med", "School of Medicine"),
    ("Keck School of Medicine", "Keck School of Medicine"),
])
def test_an_already_canonical_school_still_normalises(value, expected):
    assert canonicalise_unit_name(value) == expected


@pytest.mark.parametrize("value,expected", [
    # The unit words that DO carry the "<Unit> of <Subject>" convention are
    # untouched by this change.
    ("Chemistry Department", "Department of Chemistry"),
    ("Chemistry Dept", "Department of Chemistry"),
    ("Biology Division", "Division of Biology"),
    ("Chemistry Faculty", "Faculty of Chemistry"),
    ("Div of Newborn Medicine", "Division of Newborn Medicine"),
])
def test_the_remaining_invertible_units_are_unchanged(value, expected):
    assert canonicalise_unit_name(value) == expected


# ── The record, through finalise ─────────────────────────────────────────────

def _boston_childrens(**extra):
    """13141468 in the state the tiers leave it: Name 1 resolved by ROR, Name 2
    the record's own "Harvard Medical School"."""
    result = _init_result(EnrichmentRecord(
        record_id="13141468", country="US", city="BOSTON", state="MA",
        name1="Boston Children's Hospital", name2="Harvard Medical School",
    ))
    seed(
        result,
        name1_enriched="Boston Children's Hospital",
        ror_id="https://ror.org/00dvg7y05",
        domain="childrenshospital.org",
    )
    seed(result, **extra)
    return finalise(result, time.monotonic())


def test_the_school_ships_as_the_record_states_it():
    # finalise applies UC 5 as a `transform`, which restyles a value without
    # changing its attribution — so the inversion put "School of Harvard
    # Medical" in the column while the provenance still read `input`.
    assert _boston_childrens()["name2_enriched"] == "Harvard Medical School"


def test_an_unconfirmed_department_is_still_flagged():
    """The other half of the pair below: without the confirmation the derived
    low-confidence flag stands, so the assertion after it means something."""
    out = _boston_childrens()
    assert out["flagged_fields"] == ["name2"]
    assert out["flag_for_review"] is True
    assert "could not be established" in out["flag_reason"]


def test_a_confirmed_department_drops_the_low_confidence_flag():
    """`_ev_input_confirmed` is what the dept fall-through now records. The
    lane read the web and produced the record's own value back, which
    establishes the canonical form rather than failing to."""
    out = _boston_childrens(_ev_input_confirmed={"name2"})
    assert out["name2_enriched"] == "Harvard Medical School"
    assert "name2" not in out["flagged_fields"]
    assert out["flag_for_review"] is False
    assert not out["flag_reason"]
