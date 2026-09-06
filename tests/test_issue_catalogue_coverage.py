"""Fixture coverage for the deterministic issue detector.

Every code the detector can emit must have a record that raises it and a
near-miss record that does not. The cases live in
``tests/fixtures/issue_catalogue_coverage.json`` so the coverage set is data,
not test code, and ``test_every_emittable_code_has_a_fixture`` fails the suite
when a code is added without one.

This closes items 170 and 171 of ``docs/thesis/00_OPEN_ITEMS.md``:
``G1-NAME-001`` and ``G3-ADDR-013`` were reachable with no repository record
satisfying them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.issue_detection import EMITTED_CODES, ISSUE_CATALOGUE, detect_issues

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "issue_catalogue_coverage.json").read_text()
)
_BASELINE: dict = _FIXTURE["baseline"]
_CASES: list[dict] = _FIXTURE["cases"]


def _build(overrides: dict) -> tuple[EnrichmentRecord, bool | None, list | None]:
    """Baseline record + this case's overrides, and its enrichment-output args.

    ``_flag_for_review`` and ``_flag_codes`` are not SAP columns — they are the
    enriched record's ``Flag for Review`` and ``Flag Codes`` values, which
    reach the detector as arguments rather than as record content. That is
    precisely what separates the four output-derived codes from every other
    code in the catalogue, so the underscore prefix marks them as not-fields
    and they are passed through, never validated onto the record.
    """
    fields = dict(_BASELINE)
    flag = overrides.get("_flag_for_review")
    codes = overrides.get("_flag_codes")
    fields.update({k: v for k, v in overrides.items() if not k.startswith("_")})
    return EnrichmentRecord.model_validate(fields), flag, codes


def _ids(cases):
    return [c["code"] for c in cases]


def test_every_emittable_code_has_a_fixture():
    covered = {case["code"] for case in _CASES}
    assert covered == set(EMITTED_CODES), (
        f"uncovered: {sorted(set(EMITTED_CODES) - covered)}; "
        f"stale: {sorted(covered - set(EMITTED_CODES))}"
    )


def test_no_fixture_covers_a_withdrawn_or_undetectable_code():
    for case in _CASES:
        entry = ISSUE_CATALOGUE[case["code"]]
        assert entry.status in ("live", "unlisted"), (
            f"{case['code']} is {entry.status} and must not have a positive case"
        )


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_positive_case_raises_its_code(case):
    record, flag, codes = _build(case["positive"])
    assert case["code"] in detect_issues(
        record, flag_for_review=flag, flag_codes=codes,
    )


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_negative_case_does_not_raise_its_code(case):
    record, flag, codes = _build(case["negative"])
    assert case["code"] not in detect_issues(
        record, flag_for_review=flag, flag_codes=codes,
    )


def test_baseline_record_is_clean():
    """The shared baseline must raise nothing, or a case's positive result
    could come from the baseline rather than from its own overrides."""
    record, _flag, _codes = _build({})
    assert detect_issues(record) == []


# ---------------------------------------------------------------------------
# G5-NAME-002 — Dept / Div / Inst are accepted unit forms
# ---------------------------------------------------------------------------
#
# The single positive/negative pair above cannot carry a whole exemption, so
# the three exempt tokens and the slot asymmetry get their own cases.

@pytest.mark.parametrize("name2", [
    "Dept of Chemistry",
    "dept of chemistry",
    "Dept. of Chemistry",
    "Div of Cardiology",
    "Inst for Advanced Study",
])
def test_g5_name_002_accepts_dept_div_inst_as_unit_forms(name2):
    """Dept, Div and Inst are the forms SAP writes for a unit name, so a unit
    name already in official form must not be reported as not being in it."""
    record, _flag, _codes = _build({"Name 1": "University of Florida", "Name 2": name2})
    assert "G5-NAME-002" not in detect_issues(record)


@pytest.mark.parametrize("name2", [
    "Sch of Medicine",
    "Ctr for Materials Research",
    "Zale Receiving - Labs",
])
def test_g5_name_002_still_fires_for_a_non_exempt_token(name2):
    record, _flag, _codes = _build({"Name 1": "University of Florida", "Name 2": name2})
    assert "G5-NAME-002" in detect_issues(record)


def test_g5_name_002_still_fires_for_a_dotted_acronym_in_a_unit_slot():
    """The exemption is to the token lexicon only. "U.C.L.A" is no more an
    official unit name than it is an official organisation name."""
    record, _flag, _codes = _build({"Name 1": "University of Florida", "Name 2": "U.C.L.A"})
    assert "G5-NAME-002" in detect_issues(record)


def test_g5_name_001_accepts_inst_as_an_organisation_form():
    """Name 1 has its own exemption, and it is "Inst" alone: "Inst of
    Technology" names an organisation in its own right, so it is already in
    official form."""
    record, _flag, _codes = _build({"Name 1": "Inst of Technology"})
    assert "G5-NAME-001" not in detect_issues(record)


@pytest.mark.parametrize("name1", [
    "Dept of Chemistry",   # names a part of an organisation, not one
    "Div of Cardiology",
    "Univ of Florida",     # never exempt in any slot
])
def test_g5_name_001_still_fires_for_a_token_exempt_only_below_name_1(name1):
    """The two exempt sets are not the same set. Dept and Div are accepted
    *unit* forms and are not accepted organisation forms, so they keep raising
    -001 in Name 1 while raising nothing in Name 2-5."""
    record, _flag, _codes = _build({"Name 1": name1})
    assert "G5-NAME-001" in detect_issues(record)


@pytest.mark.parametrize("slot,name", [
    ("Name 2", "Dept of Chemistry"),
    ("Name 3", "Dept of Chemistry"),
    ("Name 4", "Div of Cardiology"),
    ("Name 5", "Inst for Materials"),
])
def test_g5_name_002_exempts_every_unit_slot_not_just_name_2(slot, name):
    """The unit slots are checked in one loop over Name 2..5, so the exemption
    reaches all four. Pinned per slot because a future rewrite that unrolls the
    loop is exactly where one slot would be left on the full lexicon."""
    record, _flag, _codes = _build({"Name 1": "University of Florida", slot: name})
    assert "G5-NAME-002" not in detect_issues(record)


@pytest.mark.parametrize("slot", ["Name 2", "Name 3", "Name 4", "Name 5"])
def test_g5_name_002_fires_from_any_unit_slot(slot):
    record, _flag, _codes = _build(
        {"Name 1": "University of Florida", slot: "Sch of Medicine"}
    )
    assert "G5-NAME-002" in detect_issues(record)


def test_g5_name_002_does_not_fire_on_dept_of_chem_engr():
    """Mixed case. Neither "Chem" nor "Engr" is in ``_NONCANON_TOKENS`` — the
    set carries "Eng" and "Engrg", and the word boundary keeps both out of
    "Engr" — so once "Dept" is exempt nothing in this name is left to fire."""
    record, _flag, _codes = _build(
        {"Name 1": "University of Florida", "Name 2": "Dept of Chem Engr"}
    )
    assert "G5-NAME-002" not in detect_issues(record)
