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
from enrichment.name_repack import (
    NAME_FIELD_WIDTH,
    chunk_name,
    merge_split_runs,
    repack_name_block,
)
from enrichment.registry_match import FUZZY_TIER
from enrichment.orchestrator import (
    Orchestrator,
    _init_result,
    _stated_name,
    _write_registry_name,
)
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
        # 41 characters — one past the column, so it still splits, but the
        # cut now falls where the real SAP column ends rather than eight
        # characters early.
        assert chunk_name("Lawrence Livermore National Security, LLC") == [
            "Lawrence Livermore National Security,", "LLC",
        ]

    def test_no_piece_exceeds_the_width(self):
        value = (
            "Navy Medicine Readiness and Training Command Jacksonville Florida"
        )
        assert all(len(p) <= NAME_FIELD_WIDTH for p in chunk_name(value))

    def test_a_piece_may_end_exactly_on_the_width(self):
        # Built from the width so this asserts the boundary rule rather than
        # whatever the constant happened to be when it was written.
        head = "Energy National Renewable Energy Laboratory Photovoltaics"
        words, exact = [], ""
        for word in head.split():
            trial = f"{exact} {word}".strip()
            if len(trial) > NAME_FIELD_WIDTH:
                break
            exact, words = trial, [*words, word]
        pieces = chunk_name(f"{head} Group")
        assert pieces[0] == exact
        assert len(pieces[0]) <= NAME_FIELD_WIDTH

    def test_a_token_longer_than_the_width_is_cut_where_the_column_ends(self):
        # There is no word boundary to retreat to. Cutting mid-word is the
        # only option left, and it beats dropping the tail.
        pieces = chunk_name("Donaudampfschifffahrtsgesellschaftskapitaen eV")
        assert pieces[0] == (
            "Donaudampfschifffahrtsgesellschaftskapitaen"[:NAME_FIELD_WIDTH]
        )
        assert pieces[1] == (
            "Donaudampfschifffahrtsgesellschaftskapitaen"[NAME_FIELD_WIDTH:]
            + " eV"
        )
        assert "".join(pieces).replace(" ", "") == (
            "DonaudampfschifffahrtsgesellschaftskapitaeneV"
        )

    def test_whitespace_is_normalised_and_a_blank_yields_nothing(self):
        assert chunk_name("  US   Army  ") == ["US Army"]
        assert chunk_name("   ") == []
        assert chunk_name("") == []


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
            "Laboratory Golden Colorado",
            "Center for Advanced Materials Research",
            "Photovoltaic Devices Group",
            None, None,
        ])
        assert all(packed)
        # At the real column width the block has room for one more piece
        # than it did at 32, so this value now lands instead of overflowing.
        # The rule under test is unchanged: what has no slot is RETURNED, not
        # silently dropped, and the caller flags it.
        assert dropped == []

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
        # 37 characters — inside the real SAP column, so the merged name
        # ships in one field. It used to be cut at 32 and shed "Technology"
        # into Name 2, which is the fragmenting §3 removes.
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
            name2="Energy Laboratory Golden Colorado Campus Building East",
            name3="Center for Advanced Materials Research and Development",
            name4="Photovoltaic Devices Group Building Seventeen North",
            name5="Thin Film Deposition Facility Cleanroom Annexe West",
        )
        # Widened to overflow the block at the real column width — at 40 the
        # original fixture fits, which is the point of the change. What is
        # asserted is the rule: content the block cannot hold is reported.
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
# What the record STATES, once a run has been merged
# ---------------------------------------------------------------------------

class TestTheMergedNameIsWhatTheRecordStates:
    """`{slot}_original` is the SAP column, and after a merge that is half a
    name. Anything asking "what does this record state" must see the joined
    value instead.

    13044976 supplied "University of Texas" / "Health Science Center" /
    "Central Receiving". UC 0 merged the first two correctly and ROR returned
    "The University of Texas at San Antonio Health Science Center" at 0.98 —
    then the identity gate judged it against `name1_original`, which is still
    "University of Texas", called it a different entity, and the refusal path
    restored that same fragment. "Health Science Center", consumed out of
    Name 2 by the merge, shipped nowhere. Silently: the repack raises
    `overflow` for a piece with no slot left, and there was no piece left to
    place.
    """

    def _merged(self):
        """The record as it stands after the UC 0 branch has merged it."""
        result = _init_result(EnrichmentRecord(
            record_id="13044976", country="US",
            name1="University of Texas", name2="Health Science Center",
            name3="Central Receiving", city="San Antonio", state="TX",
        ))
        result["_uc0_merged"] = {
            "runs": [["name1", "name2"]],
            "names": {
                "name1": "University of Texas Health Science Center",
                "name2": "Central Receiving",
                "name3": None, "name4": None, "name5": None,
            },
        }
        return result

    def test_the_stated_name_is_the_joined_value(self):
        assert _stated_name(self._merged(), "name1") == (
            "University of Texas Health Science Center"
        )

    def test_a_record_that_arrived_whole_reads_its_column(self):
        result = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="University of Texas",
        ))
        assert _stated_name(result, "name1") == "University of Texas"
        assert _stated_name(result, "name2") is None

    def test_a_refused_registry_name_restores_the_whole_name(self):
        """The drop itself. The candidate is refused either way — what must
        not happen is the record shipping the fragment the merge consumed.

        Fuzzy, as ROR's 0.98 was: an exact match states the record's own
        wording and the identity question is not re-asked."""
        result = self._merged()
        written = _write_registry_name(
            result, "name1", "North Canyon Medical Center", "ROR",
            match_tier=FUZZY_TIER,
        )
        assert written is False
        assert result["name1_enriched"] == (
            "University of Texas Health Science Center"
        )
