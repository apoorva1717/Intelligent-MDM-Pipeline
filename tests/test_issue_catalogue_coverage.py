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


def _build(overrides: dict) -> tuple[EnrichmentRecord, bool | None]:
    """Baseline record + this case's overrides, and its flag_for_review value.

    ``_flag_for_review`` is not an SAP column — it is the enriched record's
    ``Flag for Review`` value, which reaches the detector as an argument rather
    than as record content (that is precisely what makes G7 different).
    """
    fields = dict(_BASELINE)
    flag = overrides.get("_flag_for_review")
    fields.update({k: v for k, v in overrides.items() if not k.startswith("_")})
    return EnrichmentRecord.model_validate(fields), flag


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
    record, flag = _build(case["positive"])
    assert case["code"] in detect_issues(record, flag_for_review=flag)


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_negative_case_does_not_raise_its_code(case):
    record, flag = _build(case["negative"])
    assert case["code"] not in detect_issues(record, flag_for_review=flag)


def test_baseline_record_is_clean():
    """The shared baseline must raise nothing, or a case's positive result
    could come from the baseline rather than from its own overrides."""
    record, _flag = _build({})
    assert detect_issues(record) == []
