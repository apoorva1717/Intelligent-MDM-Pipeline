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
        # 41 characters. A cut taken at 32 would land inside "Security".
        assert chunk_name("Lawrence Livermore National Security, LLC") == [
            "Lawrence Livermore National", "Security, LLC",
        ]

    def test_no_piece_exceeds_the_width(self):
        value = (
            "Navy Medicine Readiness and Training Command Jacksonville Florida"
        )
        assert all(len(p) <= NAME_FIELD_WIDTH for p in chunk_name(value))

    def test_a_piece_may_end_exactly_on_the_width(self):
        value = "Energy National Renewable Energy Laboratory"
        pieces = chunk_name(value)
        assert pieces[0] == "Energy National Renewable Energy"
        assert len(pieces[0]) == NAME_FIELD_WIDTH

    def test_a_token_longer_than_the_width_is_cut_where_the_column_ends(self):
        # There is no word boundary to retreat to. Cutting mid-word is the
        # only option left, and it beats dropping the tail.
        pieces = chunk_name("Donaudampfschifffahrtsgesellschaftskapitaen eV")
        assert pieces[0] == "Donaudampfschifffahrtsgesellscha"
        assert pieces[1] == "ftskapitaen eV"
        assert "".join(pieces).replace(" ", "") == (
            "DonaudampfschifffahrtsgesellschaftskapitaeneV"
        )

    def test_whitespace_is_normalised_and_a_blank_yields_nothing(self):
        assert chunk_name("  US   Army  ") == ["US Army"]
        assert chunk_name("   ") == []
        assert chunk_name("") == []


class TestTheCutNeverStrandsAConnector:
    """Ticket 28 part A.

    The repack used to cut at the last word boundary that fits and stop, which
    landed on exactly the boundary SAP's own writer had used — so a name UC 0
    had just merged and enriched came back out split identically, and every
    probe reading the output concluded UC 0 never fired.
    """

    @pytest.mark.parametrize("value, expected", [
        # The five shapes measured on the S2/S3 200-record sample.
        ("Exxonmobil Research & Engineering Co",
         ["Exxonmobil Research", "& Engineering Co"]),
        ("ExxonMobil Technology and Engineering Company",
         ["ExxonMobil Technology", "and Engineering Company"]),
        ("Expeditors International of Washington, Inc.",
         ["Expeditors International", "of Washington, Inc."]),
        ("Novartis Institute for BioMedical Research Inc",
         ["Novartis Institute", "for BioMedical Research Inc"]),
        ("Florida Cancer Specialists & Research Institute",
         ["Florida Cancer Specialists", "& Research Institute"]),
    ])
    def test_the_measured_shapes_cut_before_the_connector(self, value, expected):
        assert chunk_name(value) == expected

    def test_no_piece_but_the_last_ends_on_a_connector(self):
        value = "National Technology & Engineering Solutions of Sandia"
        pieces = chunk_name(value)
        assert pieces == [
            "National Technology", "& Engineering Solutions", "of Sandia",
        ]
        assert not any(
            _is_connector(piece.split(" ")[-1]) for piece in pieces[:-1]
        )

    def test_a_run_of_connectors_retreats_whole(self):
        # The dense cut ends the first piece on "of the"; both words move,
        # rather than retreating one and leaving a piece ending on "of".
        value = "Genetics Institute of the Massachusetts General Hospital"
        assert chunk_name(value, avoid_connector_endings=False)[0] == (
            "Genetics Institute of the"
        )
        pieces = chunk_name(value)
        assert pieces[0] == "Genetics Institute"
        assert pieces[1].startswith("of the ")

    def test_a_name_ending_on_a_connector_keeps_it(self):
        # Nothing follows the last piece to carry the word to, and the name
        # genuinely ends that way.
        assert chunk_name("Smith and") == ["Smith and"]

    def test_the_retreat_is_declined_rather_than_cut_a_word_in_half(self):
        # Carrying "of" forward would leave no room for the long token that
        # follows, and a mid-word cut is worse than a connector at the edge.
        value = "Bundesanstalt Materialforschung of " + "X" * 31
        pieces = chunk_name(value)
        assert all(len(p) <= NAME_FIELD_WIDTH for p in pieces)
        assert "X" * 31 in pieces

    def test_it_is_never_paid_for_in_dropped_content(self):
        # The tidier cut costs a slot here, and a piece with no slot is lost.
        # Content wins: the denser cut is taken instead.
        packed, dropped, _ = repack_name_block([
            "United States Department of Energy National Renewable Energy "
            "Laboratory Golden Colorado",
            "Center for Advanced Materials Research",
            "Photovoltaic Devices Group",
            None, None,
        ])
        assert all(packed)
        assert dropped == ["Photovoltaic Devices Group"]

    def test_the_dense_cut_is_still_available_explicitly(self):
        assert chunk_name(
            "Exxonmobil Research & Engineering Co",
            avoid_connector_endings=False,
        ) == ["Exxonmobil Research &", "Engineering Co"]


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
        assert r.flagged_fields == ["name4"]
        assert packed[NAME_SLOTS.index("name4")] == "LLC"
        assert "Name 4:" in r.flag_reason

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

    Cutting before the connector puts a lower-case word at the head of a slot,
    and the output casing pass capitalises the first token of a name — so
    `ExxonMobil Technology` + `and Engineering Company` shipped as
    `And Engineering Company`. The repack's own cut point was manufacturing a
    capital in the middle of a name.
    """

    @pytest.mark.asyncio
    async def test_the_connector_leading_a_slot_stays_lower_case(self):
        r = await _run(
            _orch(llm=_SplitLLM({("Name 1", "Name 2")})),
            name1="ExxonMobil Technology and", name2="Engineering Company",
        )
        assert _block(r)[:2] == [
            "ExxonMobil Technology", "and Engineering Company",
        ]

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
            "Lawrence Livermore National", "Security, LLC",
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
            "Laboratory Golden Colorado",
            "Center for Advanced Materials Research",
            "Photovoltaic Devices Group",
            None, None,
        ])
        assert all(packed)
        assert dropped == ["Photovoltaic Devices Group"]

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
        # 37 characters of official ROR name, written back across two columns.
        # The cut falls before "of", not after it: a piece ending on a
        # connector is the shape UC 0 exists to repair, and a repack that
        # produces one hands back the defect the merge just removed.
        assert _block(r)[:2] == ["Massachusetts Institute", "of Technology"]

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
            "Lawrence Livermore National", "Security, LLC",
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
            name2="Energy Laboratory Golden Colorado Campus Building",
            name3="Center for Advanced Materials Research",
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
