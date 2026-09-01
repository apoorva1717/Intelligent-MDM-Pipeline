"""`enrichment.dept_block` — the single authority for Name 2..5.

Behavioural, on lists. The module is a pure function over a block, so these
tests state what a block MEANS and never how the pipeline reaches it.

The row that prompted the module:

    Name 1  Wayne State University Dept of Biologica
    Name 2  Dept of Biological Sciences
    Name 3  Greenberg Lab

`Department of Biologica` shipped in Name 4 — a unit that does not exist. The
ratio scores the fragment against the fuller form at 82, under the 92
threshold both old passes used, so neither of them saw a duplicate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.dept_block import (
    ORIGIN_INPUT,
    ORIGIN_LLM,
    ORIGIN_REGISTRY,
    RESOLVED_ORIGINS,
    classify,
    normalise,
    same_unit,
)


def _run(block, origins=None, result=None):
    origins = origins or [ORIGIN_INPUT] * len(block)
    return normalise(list(block), list(origins), result)


# ── same_unit ────────────────────────────────────────────────────────────────

class TestSameUnit:
    @pytest.mark.parametrize("a,b", [
        ("Department of Chemistry", "Chemistry Department"),
        ("Department of Main Receiving", "Main Receiving Dept"),
        ("Department of Main Receiving", "Department of Main Receivingt"),
        ("Dept of Biologica", "Dept of Biological Sciences"),
    ])
    def test_one_unit_written_two_ways(self, a, b):
        assert same_unit(a, b) is True
        assert same_unit(b, a) is True

    @pytest.mark.parametrize("a,b", [
        ("Physics", "Physiology"),
        ("Department of Materials Science and", "Engineering"),
        ("Greenberg Lab", "Department of Biological Sciences"),
    ])
    def test_two_units(self, a, b):
        assert same_unit(a, b) is False
        assert same_unit(b, a) is False

    def test_nothing_is_the_same_unit_as_nothing(self):
        assert same_unit(None, "Department of Chemistry") is False
        assert same_unit("   ", "Department of Chemistry") is False


# ── classify ─────────────────────────────────────────────────────────────────

class TestClassify:
    @pytest.mark.parametrize("value,expected", [
        ("", "empty"),
        ("   ", "empty"),
        ("Accounts Payable", "admin"),
        ("Accounts Payable Dept", "admin"),
        ("Central Warehouse", "identifies_nothing"),
        ("Greenberg Lab", "granular"),
        ("Cancer Research Center", "granular"),
        ("Department of Chemistry", "unit"),
        ("Division of Animal Health", "unit"),
    ])
    def test_what_the_value_names(self, value, expected):
        assert classify(value) == expected


# ── normalise ────────────────────────────────────────────────────────────────

class TestTruncatedDuplicates:
    """The Biologica shape, both ways round."""

    def test_the_wayne_state_block(self):
        block, origins, log = _run(
            ["Dept of Biological Sciences", "Greenberg Lab",
             "Dept of Biologica", None],
        )
        assert block == ["Dept of Biological Sciences", "Greenberg Lab",
                         None, None]
        assert origins[:2] == [ORIGIN_INPUT, ORIGIN_INPUT]
        dropped = [e for e in log if e["step"] == "drop"]
        assert len(dropped) == 1
        assert dropped[0]["value"] == "Dept of Biologica"
        assert dropped[0]["reason"] == "covered-by slot 0"
        assert dropped[0]["kept"] == "Dept of Biological Sciences"

    def test_the_fragment_arrives_first(self):
        # Slot order decides which arrives first, and that is not a statement
        # about which is right: the fuller form wins and takes slot 0.
        block, _origins, log = _run(
            ["Dept of Biologica", "Dept of Biological Sciences"],
        )
        assert block == ["Dept of Biological Sciences", None]
        assert [e["step"] for e in log] == ["replace"]
        assert log[0]["kept"] == "Dept of Biological Sciences"


class TestOriginFollowsTheValue:
    def test_a_fuller_form_from_a_model_keeps_its_own_origin(self):
        # The fragment is the record's own word; the completed form is the
        # model's answer. Inheriting `input` here would tell Phase 5 that a
        # resolved value is still an open question.
        _block, origins, _log = _run(
            ["Dept of Biologica", "Dept of Biological Sciences"],
            [ORIGIN_INPUT, ORIGIN_LLM],
        )
        assert origins[0] == ORIGIN_LLM
        assert origins[0] in RESOLVED_ORIGINS

    def test_a_fuller_form_from_the_input_inherits_the_authority_it_completes(self):
        # The reverse: a registry spelled the fragment, the record completed
        # it. The value is still the registry's — the record only supplied the
        # letters the field width cut off.
        _block, origins, _log = _run(
            ["Dept of Biologica", "Dept of Biological Sciences"],
            [ORIGIN_REGISTRY, ORIGIN_INPUT],
        )
        assert origins[0] == ORIGIN_REGISTRY

    def test_origins_travel_with_values_through_the_pack(self):
        block, origins, _log = _run(
            [None, "Department of Chemistry", None, "Greenberg Lab"],
            [ORIGIN_INPUT, ORIGIN_REGISTRY, ORIGIN_INPUT, ORIGIN_LLM],
        )
        assert block == ["Department of Chemistry", "Greenberg Lab", None, None]
        assert origins[:2] == [ORIGIN_REGISTRY, ORIGIN_LLM]


class TestTheEquivalenceBoundary:
    def test_an_admin_desk_written_twice_is_one_slot(self):
        block, _origins, _log = _run(["Accounts Payable", "Accounts Payable Dept"])
        assert block == ["Accounts Payable", None]

    def test_two_units_that_merely_look_alike_are_both_kept(self):
        # UC 12's own worked example. Ratio 82 — under the threshold, and the
        # word-coverage arm does not fire either: neither accounts for the
        # other.
        block, _origins, log = _run(["Physics", "Physiology"])
        assert block == ["Physics", "Physiology"]
        assert log == []

    def test_a_continuation_is_never_dropped(self):
        # One unit split across two columns by the field width. Nothing here
        # covers anything — "Engineering" is the REST of the value above it,
        # not a duplicate of it — so both halves stay until something joins
        # them.
        block, _origins, log = _run(
            ["Department of Materials Science and", "Engineering"],
        )
        assert block == ["Department of Materials Science and", "Engineering"]
        assert log == []


class TestUnitOrdering:
    def test_a_division_is_written_above_a_department(self):
        block, origins, log = _run(
            ["Department of Agriculture", "Division of Animal Health"],
            [ORIGIN_INPUT, ORIGIN_REGISTRY],
        )
        assert block == ["Division of Animal Health", "Department of Agriculture"]
        assert origins[:2] == [ORIGIN_REGISTRY, ORIGIN_INPUT]
        assert [e["step"] for e in log] == ["order", "order"]

    def test_a_slot_the_rule_has_no_opinion_about_is_left_where_it_is(self):
        # Only the ranked constructions take part. The lab is not one, so it
        # keeps the slot packing gave it while the two around it swap.
        block, _origins, _log = _run(
            ["Department of Agriculture", "Greenberg Lab",
             "Division of Animal Health", None],
        )
        assert block == ["Division of Animal Health", "Greenberg Lab",
                         "Department of Agriculture", None]

    def test_equal_ranks_keep_the_order_they_arrived_in(self):
        block, _origins, log = _run(
            ["Division of Animal Health", "Division of Plant Health"],
        )
        assert block == ["Division of Animal Health", "Division of Plant Health"]
        assert log == []


class TestEmptyAndWhitespace:
    def test_a_whitespace_slot_is_an_empty_slot(self):
        block, _origins, log = _run(["Department of Chemistry", "   ", "", None])
        assert block == ["Department of Chemistry", None, None, None]
        assert [e["step"] for e in log] == ["empty", "empty"]

    def test_an_empty_block_is_returned_unchanged(self):
        block, _origins, log = _run([None, None, None, None])
        assert block == [None, None, None, None]
        assert log == []


class TestIdempotence:
    """Running the authority twice must say the same thing as running it once.

    This is what lets finalise call it after the old passes, and preprocess
    call it again at exit, without the second call undoing the first.
    """

    @pytest.mark.parametrize("block", [
        ["Dept of Biological Sciences", "Greenberg Lab", "Dept of Biologica", None],
        ["Dept of Biologica", "Dept of Biological Sciences"],
        ["Accounts Payable", "Accounts Payable Dept"],
        ["Physics", "Physiology"],
        ["Department of Materials Science and", "Engineering"],
        ["Department of Agriculture", "Division of Animal Health"],
        ["Department of Agriculture", "Greenberg Lab", "Division of Animal Health", None],
        ["Department of Chemistry", "   ", "", None],
        [None, None, None, None],
    ])
    def test_twice_is_once(self, block):
        once, once_origins, _log = _run(block)
        twice, twice_origins, twice_log = normalise(
            list(once), list(once_origins), None,
        )
        assert twice == once
        assert twice_origins == once_origins
        assert twice_log == []


class TestTheContract:
    def test_a_mismatched_origins_list_is_refused(self):
        with pytest.raises(ValueError):
            normalise(["Department of Chemistry", None], [ORIGIN_INPUT], None)

    def test_the_input_block_is_not_mutated(self):
        block = ["Dept of Biologica", "Dept of Biological Sciences"]
        origins = [ORIGIN_INPUT, ORIGIN_LLM]
        normalise(block, origins, None)
        assert block == ["Dept of Biologica", "Dept of Biological Sciences"]
        assert origins == [ORIGIN_INPUT, ORIGIN_LLM]


# ── Parity with the passes this module is meant to replace ──────────────────

BASELINE_DIR = Path(
    os.environ.get("DEPT_BLOCK_BASELINE_DIR")
    or Path(__file__).resolve().parent.parent / "baseline"
)


def _baseline_workbooks():
    if not BASELINE_DIR.is_dir():
        return []
    return sorted(p for p in BASELINE_DIR.glob("*.xlsx") if not p.name.startswith("~$"))


def _dept_blocks(path):
    """(record id, [Name 2..5]) for every row of an enriched workbook."""
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True).active
    rows = ws.iter_rows(values_only=True)
    header = list(next(rows))
    idx = {h: i for i, h in enumerate(header)}
    cols = [idx[f"Name {n}"] for n in (2, 3, 4, 5) if f"Name {n}" in idx]
    for row in rows:
        if all(c is None for c in row):
            continue
        yield row[0], [row[c] for c in cols]


@pytest.mark.skipif(not _baseline_workbooks(), reason="no baseline/ workbooks")
@pytest.mark.parametrize(
    "workbook", _baseline_workbooks() or [None],
    ids=lambda p: getattr(p, "name", "none"),
)
def test_normalise_is_a_superset_of_the_passes_it_replaces(workbook):
    """On real output, the authority must find nothing left to do.

    finalise already dedups, packs and orders the block. If `normalise`
    changes a shipped block, it does not yet agree with the passes it is meant
    to replace — and the difference is the finding, not the fixture.
    """
    differences = []
    for record_id, block in _dept_blocks(workbook):
        after, _origins, log = normalise(
            list(block), [ORIGIN_INPUT] * len(block), None,
        )
        if after != list(block):
            differences.append((record_id, block, after, log))
    assert not differences, "\n".join(
        f"{rid}: {before} -> {after}\n    {log}"
        for rid, before, after, log in differences
    )
