"""The golden-set grader — the rules it applies, and their precedence.

`tools/golden_eval` grades the pipeline's output against the solved reference
in `docs/SAMPLE_DATA/`. The grader is the thing that decides whether a run
passed, so a bug in it is worse than a bug in what it grades: it either hides a
real regression or manufactures a failure that sends someone after a defect
that is not there.

Two properties matter most and are pinned hardest:

* a `skip` is *no claim*, never a silent pass — it must be visibly excluded
  from the denominator, not counted as a match;
* a record the run did not produce must fail, not score zero mismatches.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.golden_eval import (  # noqa: E402
    EXACT,
    EXACT_ABBREV,
    fold_abbreviations,
    EXACT_CI,
    MATCH,
    MISMATCH,
    SKIP,
    SKIPPED,
    Reference,
    Rule,
    by_column,
    compare,
    compare_cell,
    normalise,
    parse_note,
)


class TestNormalise:
    """Both sides are rendered the same way before any rule is applied — a
    difference of type is not a difference of value."""

    @pytest.mark.parametrize("value, expected", [
        (None, ""),
        ("", ""),
        ("  US  Army  ", "US Army"),
        (2139, "2139"),
        (2139.0, "2139"),          # Excel hands back a float for a number
        (True, "true"),
        (["a", "b"], "a; b"),
    ])
    def test_it_renders_a_cell_as_one_string(self, value, expected):
        assert normalise(value) == expected

    def test_a_date_compares_as_its_day(self):
        import datetime as dt
        assert normalise(dt.datetime(2025, 7, 2, 0, 0)) == "2025-07-02"
        assert normalise(dt.date(2025, 7, 2)) == "2025-07-02"

    def test_invisible_characters_are_not_a_difference(self):
        assert normalise("Ivy Ln") == normalise("Ivy Ln")


class TestParseNote:
    def test_a_bare_skip(self):
        assert parse_note("skip").kind == SKIP

    def test_a_skip_carries_its_reason(self):
        rule = parse_note("skip — 'Drawer 1' routing is a judgement call")
        assert rule.kind == SKIP
        assert "Drawer 1" in rule.reason

    def test_an_any_of_widens_the_check(self):
        rule = parse_note("any_of: Equad | E-Quad | EQuad")
        assert rule.kind == EXACT
        assert rule.alternatives == ("Equad", "E-Quad", "EQuad")

    def test_a_blank_note_says_nothing(self):
        assert parse_note("") is None
        assert parse_note(None) is None
        assert parse_note("any_of:") is None


class TestCompareCell:
    def test_exact_is_exact_after_trimming(self):
        rule = Rule(kind=EXACT)
        assert compare_cell("Ivy Ln", "  Ivy Ln ", rule) == MATCH
        assert compare_cell("Ivy Ln", "ivy ln", rule) == MISMATCH

    def test_exact_ci_forgives_casing_only(self):
        rule = Rule(kind=EXACT_CI)
        assert compare_cell("OLDEN STREET", "Olden Street", rule) == MATCH
        assert compare_cell("OLDEN STREET", "Olden St", rule) == MISMATCH

    def test_skip_grades_nothing_in_either_direction(self):
        rule = Rule(kind=SKIP)
        assert compare_cell("a", "b", rule) == SKIPPED
        assert compare_cell("a", "a", rule) == SKIPPED

    def test_an_empty_expected_cell_in_a_graded_column_is_an_assertion(self):
        # The reference says this column should be blank. Producing a value
        # is a failure, not a bonus.
        assert compare_cell(None, "Equad", Rule(kind=EXACT)) == MISMATCH
        assert compare_cell(None, None, Rule(kind=EXACT)) == MATCH

    def test_any_of_accepts_every_listed_alternative(self):
        rule = parse_note("any_of: Equad | E-Quad | EQuad")
        for value in ("Equad", "E-Quad", "EQuad"):
            assert compare_cell("Equad", value, rule) == MATCH
        assert compare_cell("Equad", "Engineering Quad", rule) == MISMATCH

    def test_the_empty_token_makes_a_blank_acceptable(self):
        rule = parse_note("any_of: RECG | (empty)")
        assert compare_cell("RECG", "RECG", rule) == MATCH
        assert compare_cell("RECG", None, rule) == MATCH
        assert compare_cell("RECG", "other", rule) == MISMATCH

    def test_an_any_of_under_exact_ci_still_forgives_casing(self):
        rule = Rule(kind=EXACT_CI, alternatives=("Olden Street",))
        assert compare_cell(None, "OLDEN STREET", rule) == MATCH


class TestExactAbbrev:
    """The rule that neutralises one documented convention, and nothing else.

    The pipeline abbreviates street types by design and the reference expects
    the long form. Skipping the street columns would have discarded real signal
    to silence that; folding the abbreviation keeps grading everything else.
    """

    def test_the_street_convention_is_forgiven_in_both_directions(self):
        rule = Rule(kind=EXACT_ABBREV)
        assert compare_cell("OLDEN STREET", "Olden St", rule) == MATCH
        assert compare_cell("Olden St", "OLDEN STREET", rule) == MATCH
        assert compare_cell("EAST OTTAWA COURT", "E Ottawa Ct", rule) == MATCH
        assert compare_cell("N TORREY PINES ROAD", "N Torrey Pines Rd", rule) == MATCH

    def test_a_different_street_still_fails(self):
        rule = Rule(kind=EXACT_ABBREV)
        assert compare_cell("OLDEN STREET", "Nassau St", rule) == MISMATCH
        assert compare_cell("1400 TOWNSEND DR", "1401 Townsend Dr", rule) == MISMATCH
        assert compare_cell("IVY LANE", "Ivy Ln Suite 2", rule) == MISMATCH

    def test_a_lost_plural_is_still_caught(self):
        # `Bio-Rad Laboratories` shipping as `Bio-Rad Laboratory` is a real
        # defect, and folding must not forgive it.
        assert fold_abbreviations("Bio-Rad Laboratories") != (
            fold_abbreviations("Bio-Rad Laboratory")
        )
        assert compare_cell(
            "Bio-Rad Laboratories", "Bio-Rad Laboratory", Rule(kind=EXACT_ABBREV),
        ) == MISMATCH

    def test_labs_and_laboratories_are_the_same_word(self):
        assert fold_abbreviations("Baytown Refinery Labs") == (
            fold_abbreviations("Baytown Refinery Laboratories")
        )


def _reference(**kw) -> Reference:
    base = dict(
        columns=["Customer", "Name 1", "Domain"],
        inputs={},
        expected={"1": {"Customer": "1", "Name 1": "Acme", "Domain": "x.com"}},
        rules={
            "Customer": Rule(kind=EXACT),
            "Name 1": Rule(kind=EXACT),
            "Domain": Rule(kind=SKIP),
        },
        notes={},
    )
    base.update(kw)
    return Reference(**base)


class TestRulePrecedence:
    def test_a_cell_note_beats_the_column_rule(self):
        ref = _reference(notes={("1", "Name 1"): Rule(kind=SKIP,
                                                      source="cell-note")})
        assert ref.rule_for("1", "Name 1").kind == SKIP
        assert ref.rule_for("1", "Customer").kind == EXACT

    def test_a_note_applies_only_to_its_own_customer(self):
        ref = _reference(notes={("2", "Name 1"): Rule(kind=SKIP)})
        assert ref.rule_for("1", "Name 1").kind == EXACT

    def test_a_column_absent_from_match_rules_is_not_graded(self):
        # Silence is not an assertion.
        ref = _reference()
        rule = ref.rule_for("1", "Unlisted Column")
        assert rule.kind == SKIP
        assert rule.source == "default"


class TestCompare:
    def test_a_clean_run_passes_and_skips_do_not_inflate_it(self):
        results, summary = compare(_reference(), {
            "1": {"Customer": "1", "Name 1": "Acme", "Domain": "WRONG"},
        })
        assert summary["records_passed"] == 1
        assert summary["cells_failed"] == 0
        # Domain is skipped, so it is out of the denominator entirely.
        assert summary["cells_graded"] == 2
        assert {r.column for r in results if r.verdict == SKIPPED} == {"Domain"}

    def test_one_bad_cell_fails_its_record(self):
        _, summary = compare(_reference(), {
            "1": {"Customer": "1", "Name 1": "Acme Inc", "Domain": "x.com"},
        })
        assert summary["records_failed"] == 1
        assert summary["cells_failed"] == 1

    def test_a_record_the_run_did_not_produce_is_missing_not_passing(self):
        # The failure mode this guards: scoring an absent row as "no
        # mismatches" would report a perfect record for a record that does
        # not exist.
        _, summary = compare(_reference(), {})
        assert summary["records_missing"] == ["1"]
        assert summary["records_produced"] == 0
        assert summary["records_passed"] == 0
        assert summary["record_accuracy"] == 0.0

    def test_by_column_ranks_the_worst_first(self):
        ref = _reference(expected={
            "1": {"Customer": "1", "Name 1": "Acme", "Domain": ""},
            "2": {"Customer": "2", "Name 1": "Beta", "Domain": ""},
        })
        results, _ = compare(ref, {
            "1": {"Customer": "1", "Name 1": "WRONG", "Domain": ""},
            "2": {"Customer": "2", "Name 1": "WRONG", "Domain": ""},
        })
        ranked = by_column(results)
        assert ranked[0]["column"] == "Name 1"
        assert ranked[0]["failed"] == 2
        assert ranked[0]["accuracy"] == 0.0
        # A skipped column is graded zero times and cannot rank.
        assert {r["column"]: r["graded"] for r in ranked}["Domain"] == 0


REFERENCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs" / "SAMPLE_DATA"
    / "testall100_SOLVED_REFERENCE_v1 (1) (1).xlsx"
)


@pytest.fixture(scope="module")
def reference():
    if not REFERENCE_PATH.exists():
        pytest.skip("the golden set is not present in this checkout")
    from tools.golden_eval import load_reference
    return load_reference(str(REFERENCE_PATH))


class TestTheOverrides:
    """The shipped overrides must apply, and must not widen anything silently."""

    OVERRIDES = (
        Path(__file__).resolve().parent.parent
        / "docs" / "SAMPLE_DATA" / "reference_overrides.json"
    )

    def test_they_are_applied_and_reported(self, reference):
        from tools.golden_eval import load_reference
        if not self.OVERRIDES.exists():
            pytest.skip("overrides file not present")
        with_overrides = load_reference(
            str(REFERENCE_PATH), overrides=str(self.OVERRIDES),
        )
        assert with_overrides.overrides_applied
        assert with_overrides.rules["Email"].kind == EXACT_CI
        assert with_overrides.rules["Street 1"].kind == EXACT_ABBREV
        # The authored reference is untouched by loading the overrides.
        assert reference.rules["Email"].kind == EXACT
        assert reference.rules["Street 1"].kind == EXACT_CI

    def test_no_override_turns_a_graded_column_into_a_skip(self, reference):
        # Silencing a column would raise the score by measuring less. Every
        # override must keep grading it.
        from tools.golden_eval import load_reference
        if not self.OVERRIDES.exists():
            pytest.skip("overrides file not present")
        after = load_reference(str(REFERENCE_PATH), overrides=str(self.OVERRIDES))
        assert set(after.graded_columns) == set(reference.graded_columns)


class TestTheRealReferenceLoads:
    """The shipped reference must parse, and every note must grade something."""

    def test_it_pairs_every_record(self, reference):
        assert len(reference.expected) == 99
        assert set(reference.inputs) == set(reference.expected)

    def test_no_cell_note_grades_a_column_or_customer_that_does_not_exist(
        self, reference,
    ):
        assert reference.orphan_notes == []

    def test_the_registry_dependent_columns_are_all_skipped(self, reference):
        # The reference is explicit that it makes no claim about these, and a
        # grader that scored them would be measuring the weather.
        for column in (
            "ROR ID", "LEI ID", "Domain", "Record Type", "Operating Name",
            "Search Term 1", "Search Term 2", "Flag Codes", "Flag Reason",
            "Name 1 Provenance", "Domain Provenance",
        ):
            assert reference.rules[column].kind == SKIP, column

    def test_the_deterministic_columns_are_graded(self, reference):
        for column in (
            "Name 1", "Name 2", "Care Of", "Contact", "Email", "PO Box",
            "House Number", "Building", "Room", "Postal Code", "City",
        ):
            assert reference.rules[column].graded, column
