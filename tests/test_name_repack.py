"""UC 0 — a name split across SAP columns is repaired, not reported.

The rule these tests pin:

* a run of slots the overflow check reports as one continuous name is MERGED
  before enrichment, and the merged value is what the tiers are asked about —
  "US Army" matches nothing, "US Army Corps of Engineers" does;
* the settled names are written back across the block in pieces of at most
  ``NAME_FIELD_WIDTH`` characters, cut at a word boundary so a piece never ends
  mid-word;
* pieces fill the block from Name 1 down, so the organisation name keeps the
  slots it needs and the department slots take what is left;
* a piece with nowhere to go is flagged (`overflow`) and never silently
  dropped;
* a repaired split raises NO code of its own — the record carries only the
  flags its own enrichment earned;
* only a merged record is repacked. A record that arrived whole keeps the slot
  layout the pipeline gave it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from types import SimpleNamespace

from enrichment.flags import (
    DOMAIN_UNVERIFIED,
    ENTITY_SUPERSEDED,
    UNVERIFIED_INFERENCE,
    relabel_name_slots,
    render,
)
from utils.name_slots import NAME_SLOTS
from utils.text_utils import normalise_case
from enrichment.name_repack import (
    NAME_FIELD_WIDTH,
    _is_connector,
    chunk_name,
    merge_split_runs,
    repack_name_block,
)
from enrichment.orchestrator import Orchestrator
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.ror_mock import MockRORClient


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _NoSearch:
    async def search(self, q, num_results=5, *, country=None):
        return []


class _SplitLLM:
    """Reports the given adjacent pairs as one continuous name, and answers
    every other prompt with nothing so no tier can invent anything."""

    def __init__(self, pairs: set[tuple[str, str]] | None = None) -> None:
        # Slot labels as the prompt renders them, e.g. ("Name 1", "Name 2").
        self.pairs = pairs

    async def extract_json(self, system, user, **k):
        if "overflow" not in system.lower() and "one continuous" not in system.lower():
            return {}
        if self.pairs is not None and not any(
            f"{upper}:" in user and f"{lower}:" in user
            for upper, lower in self.pairs
        ):
            return {"is_overflow": False, "confidence": "high"}
        return {
            "is_overflow": True,
            "confidence": "high",
            "reasoning": "the two fields read as one name",
        }

    async def aclose(self):
        pass


class _RecordingROR(MockRORClient):
    """The curated ROR mock, plus the queries it was asked."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.queries: list[str] = []

    async def call(self, name, *a, **k) -> dict[str, Any]:
        self.queries.append(name)
        return await super().call(name, *a, **k)


def _orch(llm=None, ror=None):
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": ror if ror is not None else _RecordingROR(st),
        "lei": MockLEIClient(st),
        "search": _NoSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": llm if llm is not None else _SplitLLM(),
    })


async def _run(orch, **record_kw):
    rec = EnrichmentRecord(record_id="t", country="US", **record_kw)
    resp = await orch.enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


def _block(result) -> list[str | None]:
    return [getattr(result, f"name{i}_enriched") for i in range(1, 6)]


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------

class TestChunkName:
    def test_a_name_that_fits_is_one_piece(self):
        assert chunk_name("US Army Corps of Engineers") == [
            "US Army Corps of Engineers",
        ]

    def test_the_cut_retreats_to_a_word_boundary(self):
        # 41 characters. A cut taken at the width lands inside "LLC".
        assert chunk_name("Lawrence Livermore National Security, LLC") == [
            "Lawrence Livermore National Security,", "LLC",
        ]

    def test_no_piece_exceeds_the_width(self):
        value = (
            "Navy Medicine Readiness and Training Command Jacksonville Florida"
        )
        assert all(len(p) <= NAME_FIELD_WIDTH for p in chunk_name(value))

    def test_a_piece_may_end_exactly_on_the_width(self):
        value = "Energy National Renewable Energy Systems Laboratory"
        pieces = chunk_name(value)
        assert pieces[0] == "Energy National Renewable Energy Systems"
        assert len(pieces[0]) == NAME_FIELD_WIDTH

    def test_a_token_longer_than_the_width_is_cut_where_the_column_ends(self):
        # There is no word boundary to retreat to. Cutting mid-word is the
        # only option left, and it beats dropping the tail.
        value = "Donaudampfschifffahrtsgesellschaftskapitaen eV"
        pieces = chunk_name(value)
        assert pieces[0] == value[:NAME_FIELD_WIDTH]
        assert pieces[1] == value[NAME_FIELD_WIDTH:]
        assert "".join(pieces).replace(" ", "") == (
            "DonaudampfschifffahrtsgesellschaftskapitaeneV"
        )

    def test_whitespace_is_normalised_and_a_blank_yields_nothing(self):
        assert chunk_name("  US   Army  ") == ["US Army"]
        assert chunk_name("   ") == []
        assert chunk_name("") == []


class TestTheCutIsDense:
    """Ticket 28 part A, as it settled.

    The cut takes every word that fits, and may end a piece on a connector.
    That is the corpus's own convention: the reference workbook writes
    `Department of Materials Science and`, `Department of Chemical Engineering
    and` and `Department of Molecular, Cell and` on three independent records.

    Retreating off the connector was the first answer to ticket 28, when
    NAME_FIELD_WIDTH was wrongly 32 and the cut kept reproducing SAP's own
    split. The width was the cause -- see TestTheWidthIsTheColumn.
    """

    @pytest.mark.parametrize("value, expected", [
        # The five shapes measured on the S2/S3 200-record sample. One of the
        # five no longer needs cutting at all at the real column width, and
        # the rest cut where the reference expects -- which is the clearest
        # statement of what ticket 28 actually was.
        ("Exxonmobil Research & Engineering Co",
         ["Exxonmobil Research & Engineering Co"]),
        ("ExxonMobil Technology and Engineering Company",
         ["ExxonMobil Technology and Engineering", "Company"]),
        ("Expeditors International of Washington, Inc.",
         ["Expeditors International of Washington,", "Inc."]),
        ("Novartis Institute for BioMedical Research Inc",
         ["Novartis Institute for BioMedical", "Research Inc"]),
        ("Florida Cancer Specialists & Research Institute",
         ["Florida Cancer Specialists & Research", "Institute"]),
    ])
    def test_the_measured_shapes(self, value, expected):
        assert chunk_name(value) == expected

    def test_a_piece_may_end_on_a_connector(self):
        # The reference expects exactly this, on three separate records.
        assert chunk_name("Department of Materials Science and Engineering") \
            == ["Department of Materials Science and", "Engineering"]

    def test_a_name_ending_on_a_connector_keeps_it(self):
        assert chunk_name("Smith and") == ["Smith and"]


class TestTheWidthIsTheColumn:
    """NAME_FIELD_WIDTH is measured, not chosen.

    No name cell in any raw input corpus exceeds 40 characters, and two
    records arrive truncated mid-word at exactly 40. A column that cuts a word
    at 40 is a column of 40.
    """

    def test_the_width_is_forty(self):
        assert NAME_FIELD_WIDTH == 40

    @pytest.mark.parametrize("truncated", [
        "The Salk Institute for Biological Studie",   # "Studies", cut
        "Palo Alto Veterans Institute for Researc",   # "Research", cut
    ])
    def test_the_corpus_truncations_sit_exactly_on_the_width(self, truncated):
        assert len(truncated) == NAME_FIELD_WIDTH


class TestTheConnectorRetreatIsStillAvailable:
    """Off by default, but honoured -- the consideration is real, and turning
    it back on is one argument."""

    def test_it_retreats_off_the_connector(self):
        assert chunk_name(
            "Department of Materials Science and Engineering",
            avoid_connector_endings=True,
        ) == ["Department of Materials Science", "and Engineering"]

    def test_no_piece_but_the_last_ends_on_a_connector(self):
        value = "National Institute of Standards and Technology"
        pieces = chunk_name(value, avoid_connector_endings=True)
        assert pieces == ["National Institute of Standards", "and Technology"]
        assert not any(
            _is_connector(piece.split(" ")[-1]) for piece in pieces[:-1]
        )

    def test_a_run_of_connectors_retreats_whole(self):
        # The dense cut ends the first piece on "of the"; both words move,
        # rather than retreating one and leaving a piece ending on "of".
        value = "Advanced Genetics Institute of the Massachusetts General Hospital"
        assert chunk_name(value)[0] == "Advanced Genetics Institute of the"
        pieces = chunk_name(value, avoid_connector_endings=True)
        assert pieces[0] == "Advanced Genetics Institute"
        assert pieces[1].startswith("of the ")

    def test_the_retreat_is_declined_rather_than_cut_a_word_in_half(self):
        # Carrying "of" forward would leave no room for the long token that
        # follows, and a mid-word cut is worse than a connector at the edge.
        value = "Bundesanstalt Materialforschung of " + "X" * 39
        pieces = chunk_name(value, avoid_connector_endings=True)
        assert all(len(p) <= NAME_FIELD_WIDTH for p in pieces)
        assert "X" * 39 in pieces

    def test_it_is_never_paid_for_in_dropped_content(self):
        # The tidier cut costs a slot here, and a piece with no slot is lost.
        # Content wins: the denser cut is taken instead.
        block = [
            "United States Department of Energy National Renewable Energy "
            "Laboratory Golden Colorado Campus Site",
            "Center for Advanced Materials and Interface Research",
            "Photovoltaic Devices Group",
            None, None,
        ]
        tidy, tidy_dropped, _ = repack_name_block(
            block, avoid_connector_endings=True,
        )
        assert all(tidy)
        # The retreat would have cost the last unit a slot, so it was declined.
        assert tidy_dropped == repack_name_block(block)[1]


class TestTheFlagFollowsTheValue:
    """Ticket 28 part B.

    `compute_flags` runs before the repack, so the scope map named the slots
    as the merged block had them. The repack then moved the values and left
    the scope standing — a flag raised about one string, displayed against
    another.
    """

    @staticmethod
    def _flagged(**rendered):
        r = SimpleNamespace(record_id="t")
        for key, value in render(**rendered).items():
            setattr(r, key, value)
        return r

    def test_the_measured_sandia_case(self):
        # What compute_flags saw: the merged block, where Name 2 held "LLC".
        merged, _ = merge_split_runs({
            "name1": "National Technology &",
            "name2": "Engineering Solutions of Sandia",
            "name3": "LLC",
            "name4": None, "name5": None,
        }, [("name1", "name2")])
        assert merged["name2"] == "LLC"

        r = self._flagged(scopes={}, low_confidence=["name2"])
        assert r.flagged_fields == ["name2"]

        packed, _, origin = repack_name_block(
            [merged[s] for s in NAME_SLOTS],
        )
        moved: dict[str, tuple[str, ...]] = {}
        for dest, source in sorted(origin.items()):
            moved.setdefault(NAME_SLOTS[source], ())
            moved[NAME_SLOTS[source]] += (NAME_SLOTS[dest],)

        assert relabel_name_slots(r, moved) is True
        # The flag now names the slot that holds the string it is about.
        assert r.flagged_fields == ["name3"]
        assert packed[NAME_SLOTS.index("name3")] == "LLC"
        assert "Name 3:" in r.flag_reason

    def test_a_name_cut_across_three_columns_scopes_to_all_three(self):
        r = self._flagged(scopes={DOMAIN_UNVERIFIED: ["name1", "domain"]})
        relabel_name_slots(r, {"name1": ("name1", "name2", "name3")})
        assert r.flagged_fields == ["name1", "name2", "name3", "domain"]

    def test_a_field_outside_the_name_block_is_untouched(self):
        r = self._flagged(scopes={DOMAIN_UNVERIFIED: ["domain"]})
        assert relabel_name_slots(r, {"name1": ("name2",)}) is False
        assert r.flagged_fields == ["domain"]

    def test_a_dropped_source_leaves_the_scope_and_empties_the_code(self):
        # name3's content had no slot to go to. Nothing is left to confirm,
        # so the code goes with it rather than pointing at a vanished value.
        r = self._flagged(scopes={UNVERIFIED_INFERENCE: ["name3"]})
        assert relabel_name_slots(r, {"name3": ()}) is True
        assert r.flag_codes == []
        assert r.flagged_fields == []

    def test_a_record_level_code_keeps_its_empty_scope(self):
        r = self._flagged(scopes={ENTITY_SUPERSEDED: []})
        relabel_name_slots(r, {"name1": ("name2",)})
        assert r.flag_codes == [ENTITY_SUPERSEDED]
        assert r.flagged_fields == []

    def test_an_identity_move_changes_nothing(self):
        r = self._flagged(scopes={UNVERIFIED_INFERENCE: ["name1"]})
        before = r.flag_reason
        assert relabel_name_slots(r, {"name1": ("name1",)}) is False
        assert r.flag_reason == before


class TestAContinuationPieceIsNotTheStartOfAName:
    """Measured on the golden set, 2026-08-29.

    A cut that puts a lower-case word at the head of a slot meets an output
    casing pass that capitalises the first token of a name -- so
    `ExxonMobil Technology` + `and Engineering Company` shipped as
    `And Engineering Company`. The repack's own cut point was manufacturing a
    capital in the middle of a name.

    That cut was the connector retreat, which is now off (TestTheCutIsDense),
    and at NAME_FIELD_WIDTH = 40 no name in any corpus reaches this shape --
    the scan over all 555 distinct name strings in the three sample workbooks
    finds zero. The dense cut can still produce one, so the guard stays; its
    fixture is synthetic rather than measured, and it is pinned at the seam
    where the rule lives rather than end to end, because the tiers reshape any
    name long enough to reach it.
    """

    def test_the_repack_can_still_lead_a_slot_with_a_connector(self):
        assert chunk_name("Southwest Research Institute Department of Chemistry") \
            == ["Southwest Research Institute Department", "of Chemistry"]

    def test_a_continuation_slot_keeps_its_leading_connector_lower_case(self):
        assert normalise_case(
            "of Chemistry", mode="name", continuation=True,
        ) == "of Chemistry"

    def test_the_same_string_starting_a_name_is_capitalised(self):
        assert normalise_case(
            "of Chemistry", mode="name", continuation=False,
        ) == "Of Chemistry"

    @pytest.mark.asyncio
    async def test_a_slot_that_starts_a_name_is_still_capitalised(self):
        # The continuation rule must not leak to Name 1, or to a department
        # slot holding a value of its own.
        r = await _run(
            _orch(llm=_SplitLLM(set())),
            name1="us army corps", name2="of engineers",
        )
        assert _block(r)[0].startswith("Us ") or _block(r)[0].startswith("US ")


# ---------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------

class TestMergeSplitRuns:
    def _names(self, *values):
        return {f"name{i}": v for i, v in enumerate(values, start=1)}

    def test_one_pair_is_joined_and_the_block_packs_leftward(self):
        merged, runs = merge_split_runs(
            self._names("US Army", "Corps of Engineers", "Radiology", None, None),
            [("name1", "name2")],
        )
        assert merged["name1"] == "US Army Corps of Engineers"
        assert merged["name2"] == "Radiology"
        assert merged["name3"] is None
        assert runs == [["name1", "name2"]]

    def test_consecutive_pairs_chain_into_one_value(self):
        # Reported as two pairs; it is one name, not two.
        merged, runs = merge_split_runs(
            self._names("Navy Medicine", "Readiness and Training", "Command", None, None),
            [("name1", "name2"), ("name2", "name3")],
        )
        assert merged["name1"] == "Navy Medicine Readiness and Training Command"
        assert merged["name2"] is None
        assert runs == [["name1", "name2", "name3"]]

    def test_two_separate_runs_stay_separate(self):
        merged, runs = merge_split_runs(
            self._names("City of", "Hope", "Department of", "Radiation Oncology", None),
            [("name1", "name2"), ("name3", "name4")],
        )
        assert merged["name1"] == "City of Hope"
        assert merged["name2"] == "Department of Radiation Oncology"
        assert merged["name3"] is None
        assert runs == [["name1", "name2"], ["name3", "name4"]]

    def test_an_unreported_pair_is_left_alone(self):
        merged, runs = merge_split_runs(
            self._names("Stanford University", "Department of Genetics", None, None, None),
            [],
        )
        assert merged["name1"] == "Stanford University"
        assert merged["name2"] == "Department of Genetics"
        assert runs == []

    def test_a_blank_slot_does_not_swallow_the_value_below_it(self):
        merged, _ = merge_split_runs(
            self._names("Stanford University", None, "Department of Genetics", None, None),
            [],
        )
        assert merged["name1"] == "Stanford University"
        assert merged["name2"] == "Department of Genetics"


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------

class TestRepackNameBlock:
    def test_the_name_takes_the_slots_it_needs_and_the_unit_moves_down(self):
        packed, dropped, _ = repack_name_block([
            "Lawrence Livermore National Security, LLC",
            "Department of Chemistry", None, None, None,
        ])
        assert packed == [
            "Lawrence Livermore National Security,", "LLC",
            "Department of Chemistry", None, None,
        ]
        assert dropped == []

    def test_two_values_never_share_a_slot(self):
        # The tail of Name 1 must not absorb the head of the department: they
        # are different values and a reviewer reading one column would see a
        # unit that does not exist.
        packed, _, _ = repack_name_block(
            ["Acme Corporation", "Research Division", None, None, None],
        )
        assert packed[0] == "Acme Corporation"
        assert packed[1] == "Research Division"

    def test_a_piece_with_no_slot_is_returned_rather_than_dropped(self):
        packed, dropped, _ = repack_name_block([
            "United States Department of Energy National Renewable Energy "
            "Laboratory Golden Colorado Campus Site Building Seven",
            "Center for Advanced Materials and Interface Research",
            "Photovoltaic Devices Group",
            None, None,
        ])
        assert all(packed)
        # The organisation name is served first; what is squeezed out is
        # always the lowest-priority unit.
        assert dropped == ["Interface Research", "Photovoltaic Devices Group"]

    def test_the_origin_map_names_the_slot_each_piece_came_from(self):
        _, _, origin = repack_name_block([
            "Lawrence Livermore National Security, LLC",
            "Department of Chemistry", None, None, None,
        ])
        # Both halves of Name 1 came from Name 1; the department from Name 2.
        assert origin == {0: 0, 1: 0, 2: 1}



# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestSplitRecordsAreEnriched:
    @pytest.mark.asyncio
    async def test_the_merged_name_is_what_the_registry_is_asked(self):
        """The point of the whole change. "Massachusetts Institute" resolves
        to nothing; the name it was cut out of resolves to MIT."""
        st = Settings()
        ror = _RecordingROR(st)
        r = await _run(
            _orch(ror=ror),
            name1="Massachusetts Institute", name2="of Technology",
        )
        assert "Massachusetts Institute of Technology" in ror.queries
        assert r.ror_id == "https://ror.org/042nb2s44"
        # 37 characters of official ROR name -- inside one SAP column, so the
        # repair is visible in the output: two slots in, one slot out.
        assert _block(r)[:2] == ["Massachusetts Institute of Technology", None]

    @pytest.mark.asyncio
    async def test_a_merged_name_that_fits_empties_the_slot_below_it(self):
        r = await _run(_orch(), name1="US Army", name2="Corps of Engineers")
        assert r.name1_enriched == "US Army Corps of Engineers"
        assert r.name2_enriched is None

    @pytest.mark.asyncio
    async def test_a_repaired_split_raises_no_flag_of_its_own(self):
        r = await _run(_orch(), name1="City of Hope", name2="National Medical Center")
        assert "overflow" not in (r.flag_codes or [])

    @pytest.mark.asyncio
    async def test_the_department_below_the_split_survives_the_rewrite(self):
        r = await _run(
            _orch(llm=_SplitLLM(pairs={("Name 1", "Name 2")})),
            name1="Lawrence Livermore", name2="National Security, LLC",
            name3="Department of Chemistry",
        )
        assert _block(r)[:3] == [
            "Lawrence Livermore National Security,", "LLC",
            "Department of Chemistry",
        ]

    @pytest.mark.asyncio
    async def test_a_record_that_arrived_whole_is_not_repacked(self):
        """The rewrite is scoped to merged records. A 41-character name that
        nothing reported as split keeps its column."""
        r = await _run(
            _orch(llm=_SplitLLM(pairs=set())),
            name1="Lawrence Livermore National Security, LLC",
        )
        assert r.name1_enriched == "Lawrence Livermore National Security, LLC"
        assert r.name2_enriched is None

    @pytest.mark.asyncio
    async def test_content_with_no_slot_left_is_flagged(self):
        r = await _run(
            _orch(),
            name1="United States Department of Energy National Renewable",
            name2="Energy Laboratory Golden Colorado Campus Building Seven",
            name3="Center for Advanced Materials and Interface Research",
            name4="Photovoltaic Devices Group",
            name5="Thin Film Deposition Facility",
        )
        assert "overflow" in (r.flag_codes or [])
        assert r.flag_for_review is True
        assert all(_block(r))

    @pytest.mark.asyncio
    async def test_no_output_name_exceeds_the_field_width(self):
        r = await _run(
            _orch(),
            name1="Navy Medicine Readiness and",
            name2="Training Command Jacksonville",
        )
        assert all(len(v) <= NAME_FIELD_WIDTH for v in _block(r) if v)


# ---------------------------------------------------------------------------
# The one over-width case an unmerged record is repaired for
# ---------------------------------------------------------------------------

class TestAnOverWidthDepartmentIsCut:
    """The repack is gated on the merge, because re-cutting every row would
    re-split names that ship correctly today. One case escapes that gate.

    Preprocess expands abbreviations in a department slot — "Dept of Materials
    Science & Eng" becomes "Department of Materials Science and Engineering",
    46 characters into a 40-character field — and nothing afterwards can put
    it back. The reference writes exactly the dense cut for this record.

    Name 1 is deliberately not a trigger: measured on the golden set, gating
    on *any* over-width value cost 12 cells to win 3, because an over-width
    Name 1 is nearly always a wrong name rather than a mis-split one, and
    cutting it spreads one wrong value across two cells. 40 is also not a
    limit the reference imposes on its own output — it writes a 41-character
    Name 1 on two records.
    """

    @pytest.mark.asyncio
    async def test_a_department_too_long_for_its_column_is_cut(self):
        r = await _run(
            _orch(llm=_SplitLLM(pairs=set())),
            name1="University of California, Riverside",
            name2="Department of Materials Science and Engineering",
        )
        block = _block(r)
        assert all(len(v) <= NAME_FIELD_WIDTH for v in block if v)
        assert block[0] == "University of California, Riverside"
        assert block[1] == "Department of Materials Science and"
        assert block[2] == "Engineering"

    @pytest.mark.asyncio
    async def test_a_block_that_fits_is_left_exactly_as_it_is(self):
        """The control: no merge, nothing over width, no rewrite."""
        r = await _run(
            _orch(llm=_SplitLLM(pairs=set())),
            name1="University of California, Riverside",
            name2="Department of Chemistry",
        )
        assert _block(r)[:2] == [
            "University of California, Riverside", "Department of Chemistry",
        ]

    @pytest.mark.asyncio
    async def test_an_over_width_name1_is_not_cut(self):
        """An over-long Name 1 is a wrong name, not a mis-split one. Leaving
        it whole keeps the error in one cell instead of cascading it down the
        block and displacing the department that follows."""
        r = await _run(
            _orch(llm=_SplitLLM(pairs=set())),
            name1="The University of Texas MD Anderson Cancer Center",
            name2="Accounts Payable",
        )
        block = _block(r)
        assert len(block[0]) > NAME_FIELD_WIDTH
        assert block[1] == "Accounts Payable"
