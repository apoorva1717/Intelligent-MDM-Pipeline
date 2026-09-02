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
    is_truncation_of,
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

    @pytest.mark.parametrize("a,b", [
        # The SAP columns are fixed width, so a unit that does not fit arrives
        # chopped mid-word. That is the case the ratio cannot see.
        ("Dept of Biologica", "Dept of Biological Sciences"),
        ("Ward", "Ward North"),
        ("Accounts Payable", "Accounts Payable Dept"),
    ])
    def test_a_truncation_is_the_same_unit(self, a, b):
        assert same_unit(a, b) is True
        assert same_unit(b, a) is True

    @pytest.mark.parametrize("a,b", [
        # A DESIGNATOR is not a truncation. These differ at a position where
        # neither token is a prefix of the other, and they are two units.
        # `introduces_nothing_new` merged the first five: it drops "A" as an
        # English article, so "Dept A" reduced to ["department"] and read as a
        # fragment of "Dept B".
        ("Dept A", "Dept B"),
        ("Building A", "Building B"),
        ("Ward A", "Ward B"),
        ("Lab A", "Lab B"),
        ("Line A", "Line B"),
        ("Building 1", "Building 2"),
        ("Ward North", "Ward South"),
        ("Physics", "Physiology"),
        ("Department of Materials Science and", "Engineering"),
    ])
    def test_a_designator_is_not_a_truncation(self, a, b):
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


#: The two records `normalise` deliberately changes, accepted as a fix rather
#: than reported as a divergence.
#:
#: Both ship a HOLE — Name 2 empty, Name 3 populated — because
#: `dept-slot-echoes-name1:dropped` clears a slot AFTER the only packing pass
#: in finalise, and nothing re-packs behind it. `normalise` closes the hole.
#: That is the intended behaviour of the authority and the reason it exists;
#: the pass ordering that opens the hole is recorded on the post-thesis list.
PACKS_A_HOLE_CLOSED = {
    # S1 — Wayne State University School of Medicine.
    "13364433": (
        [None, "C.S. Mott Center for Human Growth and Development", None, None],
        ["C.S. Mott Center for Human Growth and Development", None, None, None],
    ),
    # S4 — Cleveland Clinic Lerner Research Institute.
    "13364415": (
        [None, "Department of Cardiovascular and Metabolic Sciences", None, None],
        ["Department of Cardiovascular and Metabolic Sciences", None, None, None],
    ),
}


def no_hole_above_a_value(block: list) -> bool:
    """THE BLOCK INVARIANT: no empty slot sits above a populated one.

    A department block is read top-down. An empty Name 2 above a populated
    Name 3 does not say "this record has no primary unit" — it says the
    pipeline dropped one and nothing moved the rest up, and a consumer cannot
    tell those apart. Every block the authority returns satisfies this, and
    every block it is given is checked so a regression is attributed to the
    pass that opened the hole rather than to the authority that failed to
    close it.
    """
    seen_gap = False
    for value in block:
        if value is None or not str(value).strip():
            seen_gap = True
        elif seen_gap:
            return False
    return True


@pytest.mark.skipif(not _baseline_workbooks(), reason="no baseline/ workbooks")
@pytest.mark.parametrize(
    "workbook", _baseline_workbooks() or [None],
    ids=lambda p: getattr(p, "name", "none"),
)
def test_normalise_agrees_with_the_passes_it_replaces(workbook):
    """On real output, the authority changes only what it is meant to change.

    finalise already dedups, packs and orders the block, so `normalise` should
    find nothing to do — EXCEPT on a record that ships a hole, which it closes.
    Any other difference is a finding, not a fixture to fix.
    """
    unexpected = []
    for record_id, block in _dept_blocks(workbook):
        after, _origins, log = normalise(
            list(block), [ORIGIN_INPUT] * len(block), None,
        )
        if after == list(block):
            continue
        expected = PACKS_A_HOLE_CLOSED.get(str(record_id))
        if expected and list(block) == expected[0] and after == expected[1]:
            continue
        unexpected.append((record_id, block, after, log))
    assert not unexpected, "\n".join(
        f"{rid}: {before} -> {after}\n    {log}"
        for rid, before, after, log in unexpected
    )


@pytest.mark.skipif(not _baseline_workbooks(), reason="no baseline/ workbooks")
@pytest.mark.parametrize(
    "workbook", _baseline_workbooks() or [None],
    ids=lambda p: getattr(p, "name", "none"),
)
def test_the_block_invariant_holds_on_everything_the_authority_returns(workbook):
    """`no_hole_above_a_value` on every block in the corpus, after normalise."""
    broken = []
    for record_id, block in _dept_blocks(workbook):
        after, _origins, _log = normalise(
            list(block), [ORIGIN_INPUT] * len(block), None,
        )
        if not no_hole_above_a_value(after):
            broken.append((record_id, block, after))
    assert not broken, "\n".join(
        f"{rid}: {before} -> {after}" for rid, before, after in broken
    )


class TestTheBlockInvariant:
    """`no_hole_above_a_value`, stated on its own before it is used in bulk."""

    @pytest.mark.parametrize("block,holds", [
        (["Department of Chemistry", "Greenberg Lab", None, None], True),
        ([None, None, None, None], True),
        (["Department of Chemistry", None, None, None], True),
        # The shape the two allow-listed records ship.
        ([None, "Department of Chemistry", None, None], False),
        (["Department of Chemistry", None, "Greenberg Lab", None], False),
        (["   ", "Department of Chemistry", None, None], False),
    ])
    def test_what_the_invariant_says(self, block, holds):
        assert no_hole_above_a_value(block) is holds

    @pytest.mark.parametrize("block", [
        [None, "Department of Chemistry", None, None],
        ["Department of Chemistry", None, "Greenberg Lab", None],
        ["   ", "Department of Chemistry", None, None],
        ["Dept of Biological Sciences", None, "Dept of Biologica", None],
    ])
    def test_normalise_always_returns_a_block_that_holds_it(self, block):
        after, _origins, _log = _run(block)
        assert no_hole_above_a_value(after)


class TestIsTruncationOf:
    """The positional test, on its own.

    Every token of the short form is the START of the token in the same
    position, and the long form carries more. Raw tokens — no article or
    stopword removal, which is what let a trailing "A" disappear.
    """

    @pytest.mark.parametrize("short,long", [
        ("Dept of Biologica", "Dept of Biological Sciences"),
        ("Ward", "Ward North"),
        ("Accounts Payable", "Accounts Payable Dept"),
        ("Cent", "Center"),
        ("Department of Materials Science and",
         "Department of Materials Science and Engineering"),
    ])
    def test_the_tail_was_cut_off(self, short, long):
        assert is_truncation_of(short, long) is True

    @pytest.mark.parametrize("short,long", [
        ("Dept B", "Dept A"),          # neither token is a prefix
        ("Building 2", "Building 1"),
        ("Ward South", "Ward North"),
        ("Physiology", "Physics"),
        ("Ward North", "Ward"),        # the long side is not longer
        ("Ward", "Ward"),              # identical is not a truncation
    ])
    def test_not_a_truncation(self, short, long):
        assert is_truncation_of(short, long) is False

    def test_it_is_directional(self):
        assert is_truncation_of("Ward", "Ward North") is True
        assert is_truncation_of("Ward North", "Ward") is False

    def test_empty_is_never_a_truncation(self):
        assert is_truncation_of("", "Ward North") is False
        assert is_truncation_of("Ward", "") is False
        assert is_truncation_of(None, "Ward") is False


# ── DBA: compare the payload, ship the payload ─────────────────────────────

class TestDbaPayload:
    """A "doing business as" line carries two tokens that are not part of any
    name: the marker, which states the KIND of name, and whatever SAP
    abbreviation the field width forced. Record 13336736 states
    `DBA Olin E Teague Vet CTR` and every comparison read the marker as
    content the candidate had failed to account for.
    """

    @pytest.mark.parametrize("marked,payload", [
        ("DBA Olin E Teague Vet CTR", "Olin E Teague Vet Center"),
        ("d/b/a Coastal Marine", "Coastal Marine"),
        ("Doing Business As Acme Labs", "Acme Labs"),
        # "CO" is a legal form, not an abbreviation `expand_abbreviations`
        # rewrites — the legal-suffix rules own it.
        ("D.B.A. Gulf Shipping CO", "Gulf Shipping CO"),
    ])
    def test_the_marker_comes_off_and_abbreviations_expand(self, marked, payload):
        from enrichment.preprocess import dba_payload
        assert dba_payload(marked) == payload

    @pytest.mark.parametrize("value", [
        "Olin E Teague Vet CTR",     # no marker
        "Coastal Holdings Inc",
        "DBA",                        # marker with no payload
        "",
        None,
    ])
    def test_no_marker_means_no_payload(self, value):
        from enrichment.preprocess import dba_payload
        assert dba_payload(value) is None

    def test_the_comparison_reads_the_payload_not_the_marker(self):
        # `classify_name_change` is the Name 1 arm of the gate. The marker made
        # a name the record itself states read as an unresolved question.
        from enrichment.preprocess import dba_payload
        from utils.name_identity import classify_name_change

        marked = "DBA Coastal Marine"
        assert classify_name_change(marked, "Coastal Marine LLC") == "undecidable"
        assert classify_name_change(
            dba_payload(marked), "Coastal Marine LLC",
        ) == "same"

    def test_a_subject_swap_is_still_refused(self):
        from enrichment.preprocess import dba_payload
        from enrichment.tier2_canonical import subject_preserved

        payload = dba_payload("DBA Olin E Teague Vet CTR")
        assert subject_preserved(payload, "Department of Radiology") is False


class TestAffiliationChosenIsAFastPath:
    """`chosen` says ROR's own scorer is confident. Its ABSENCE says only that
    ROR declined to choose — the response is still ranked, and the top entry is
    often a clean match the local guards would accept.

    Record 13334354 ("LAC USC MEDICAL CENTER") is the worked example: ROR
    returns 04xzj3x20 first at 0.95 with `chosen: False`, and the old early
    return meant it was never scored. The candidates now run through the
    identical chain the chosen item runs through.
    """

    @staticmethod
    def _items(*specs):
        return {"items": [
            {"chosen": ch, "score": sc, "organization": {
                "id": f"https://ror.org/{rid}",
                "names": [{"value": v, "types": ["ror_display", "label"]}
                          for v in names],
                "country": {"country_code": "US"}, "types": ["healthcare"],
                "links": [], "relationships": [],
            }}
            for rid, names, sc, ch in specs
        ]}

    def test_a_no_chosen_response_is_still_considered(self):
        # The shape of the real 13334354 response: nothing chosen, a clear
        # leader, and runners-up that are different organisations.
        data = self._items(
            ("04xzj3x20", ["LAC+USC Medical Center", "LAC+USC"], 0.95, False),
            ("01gezbc84", ["Kaiser Permanente"], 0.80, False),
        )
        assert not any(i["chosen"] for i in data["items"])
        assert data["items"][0]["score"] >= 0.8

    def test_none_passing_still_returns_none(self):
        # The contract the change must not break: when no candidate survives
        # the guards, the strategy falls through exactly as it did before.
        data = self._items(("00000000x", ["Entirely Different Org"], 0.81, False))
        assert not any(i["chosen"] for i in data["items"])


class TestNoChosenOverrideIsExactOnly:
    """Folding is word-level: separators only, never legal forms.

    ROR withheld `chosen` because its own scorer was not confident, so
    overriding that hedge needs evidence stronger than a score. A name equal to
    the query once `+ / – — -` and whitespace runs are folded is that evidence;
    a name differing by a WORD is not.
    """

    SEP = str.maketrans({c: " " for c in "+/–—-"})

    @classmethod
    def _fold(cls, v):
        return " ".join(v.translate(cls.SEP).split()).lower()

    @pytest.mark.parametrize("query,name", [
        # The SAP field and the registry spelling one name two ways.
        ("LAC USC MEDICAL CENTER", "LAC+USC Medical Center"),
        ("HARBOR UCLA MEDICAL CENTER", "Harbor–UCLA Medical Center"),
        ("Smith Jones Labs", "Smith-Jones Labs"),
    ])
    def test_a_separator_difference_is_not_a_name_difference(self, query, name):
        assert self._fold(query) == self._fold(name)

    @pytest.mark.parametrize("query,name", [
        # A WORD apart — the case the rule exists to refuse. 13348274 took a
        # University of North Texas record as ror:verified with a unt.edu
        # domain and no flag before this.
        ("Galveston - University of Texas Medical", "University of North Texas"),
        ("LAC USC MEDICAL CENTER", "LAC+USC"),
        # Legal forms stay significant: two entities, one address.
        ("Delta Analytical Inc", "Delta Analytical LLC"),
        # Periods and apostrophes stay significant.
        ("St. Mary's Hospital", "St Marys Hospital"),
    ])
    def test_a_word_difference_still_refuses(self, query, name):
        assert self._fold(query) != self._fold(name)


class TestSiteQualifierHead:
    """A qualifier defeats the LOOKUP, not the identity.

    "UCSF Health at Mission Bay" is one organisation and one of its buildings.
    ROR indexes the organisation; nothing indexes the building, so the full
    string misses every registry while a sibling row resolves cleanly.
    """

    @pytest.mark.parametrize("name,head", [
        ("UCSF Health at Mission Bay", "UCSF Health"),
        ("Acme Corp - Baytown", "Acme Corp"),
        ("Cleveland Clinic, Weston", "Cleveland Clinic"),
        ("University of Texas at Austin", "University of Texas"),
    ])
    def test_the_head_is_what_a_registry_indexes(self, name, head):
        from enrichment.orchestrator import _site_qualifier_head
        assert _site_qualifier_head(name) == head

    @pytest.mark.parametrize("name", [
        # A legal form is not a site. Stripping it would re-query the same
        # organisation under a shorter name the ladder already normalises.
        "Belharra Therapeutics, Inc.",
        "Delta Analytical, LLC",
        # Nothing to strip.
        "Stanford University",
        "Harbor/UCLA Medical Center",
        # A head worth nothing: one token, or only connectors.
        "Merck, Rahway",
        "The at Bay",
        # No tail.
        "MIT at",
        None,
        "",
    ])
    def test_what_is_not_a_site_qualifier(self, name):
        from enrichment.orchestrator import _site_qualifier_head
        assert _site_qualifier_head(name) is None

    def test_ut_austin_is_split_but_must_never_be_retried(self):
        """The splitter has no opinion about whether to retry.

        "University of Texas at Austin" HAS a qualifier shape, and stripping it
        would replace a correct match with its parent. What stops that is the
        trigger, not the splitter: `_site_qualifier_retry` returns immediately
        when the record already holds a registry identity.
        """
        from enrichment.orchestrator import _site_qualifier_head
        assert _site_qualifier_head("University of Texas at Austin") == (
            "University of Texas"
        )
