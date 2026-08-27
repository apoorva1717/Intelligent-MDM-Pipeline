"""A slot preprocessing filled from another input field is not a blank slot.

Row 13035748 of the government-labs batch arrived with one name:

    Name 1  USDA - Kerrville MLRA Office
    Name 2  (empty)

The parent-org split does the right thing with it — Name 1 becomes "United
States Department of Agriculture" and "Kerrville MLRA Office" moves down to
Name 2 — and then the office shipped nowhere at all. Two rules downstream ask
"was this slot blank in the input?" and both read `{slot}_original`, which the
split leaves empty:

* Tier 3's subject guard only protects a POPULATED slot, so the model was free
  to answer with a different unit — "Natural Resources Conservation Service",
  true of USDA and stated nowhere in the record.
* finalise §6c then dropped that as a Tier-3 guess invented into a blank slot,
  and the department passthrough restored `name2_original` — blank.

The rule these tests pin: "blank in the input" means the record states nothing
for the slot, not that the raw SAP column was empty. A value preprocessing
moved there out of another input field (the parent-org split, the street→name
router, the UC 14 leftward pack) is the record's own and both rules must see
it. A slot the record genuinely says nothing about is untouched — Tier 3 may
still invent into it, and §6c still drops the guess.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _apply_tier3, _init_result, finalise
from enrichment.preprocess import preprocess_record
from enrichment.tier3_llm import Tier3Result
from tests.conftest import seed


def _finalised(inputs, enriched, populated=None, from_tier3=(), cleared=()):
    """Finalise one record whose tiers settled on *enriched*.

    *populated* is the marker preprocessing hands to finalise: the name slots
    it filled that the input left blank, mapped to the value it put there.
    *cleared* is its counterpart — the slots preprocessing emptied, which the
    department passthrough must not refill from `{slot}_original`.
    """
    r = _init_result(EnrichmentRecord(record_id="t", country="US", **inputs))
    seed(r, **enriched)
    seed(r, _preprocess_populated=dict(populated or {}))
    seed(r, _preprocess_cleared=set(cleared))
    seed(r, tier_used=3, confidence="medium")
    for slot in from_tier3:
        seed(r, **{f"_{slot}_from_tier3": True})
    return finalise(r, time.monotonic())


class TestPreprocessRecordsWhatItMoved:
    def test_the_usda_row_splits_the_office_out_of_name1(self):
        pre = preprocess_record(
            name1="USDA - Kerrville MLRA Office",
            name2=None, name3=None, contact=None, email=None,
            street1="Memorial Blvd 2104", street2=None, street3=None,
        )
        assert pre.name1 == "United States Department of Agriculture"
        assert pre.name2 == "Kerrville MLRA Office"


class TestTier3SubjectGuard:
    def test_a_moved_unit_is_guarded_like_a_supplied_one(self):
        # name2_original is blank — the office came out of Name 1 — so the
        # guard has to read the moved value or it protects nothing.
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="USDA - Kerrville MLRA Office",
        ))
        seed(r, name2_enriched="Kerrville MLRA Office")
        seed(r, _preprocess_populated={"name2": "Kerrville MLRA Office"})
        _apply_tier3(r, Tier3Result(
            success=True, confidence="medium",
            name2_suggestion="Natural Resources Conservation Service",
        ))
        assert r["name2_enriched"] == "Kerrville MLRA Office"
        assert not r.get("_name2_from_tier3")

    def test_a_rewording_of_the_moved_unit_is_still_accepted(self):
        # The guard asks whether Tier 3 answered with a DIFFERENT unit, not
        # whether it changed the wording — canonicalising is what it is for.
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="USDA - Kerrville MLRA Office",
        ))
        seed(r, name2_enriched="Kerrville MLRA Office")
        seed(r, _preprocess_populated={"name2": "Kerrville MLRA Office"})
        _apply_tier3(r, Tier3Result(
            success=True, confidence="medium",
            name2_suggestion="Kerrville MLRA Soil Survey Office",
        ))
        assert r["name2_enriched"] == "Kerrville MLRA Soil Survey Office"

    def test_a_genuinely_blank_slot_is_still_open_to_inference(self):
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="United States Department of Agriculture",
        ))
        _apply_tier3(r, Tier3Result(
            success=True, confidence="medium",
            name2_suggestion="Natural Resources Conservation Service",
        ))
        assert r["name2_enriched"] == "Natural Resources Conservation Service"
        assert r["_name2_from_tier3"] is True


class TestFinaliseDropsOnlyInventions:
    def test_a_moved_unit_survives_a_medium_confidence_tier3_answer(self):
        out = _finalised(
            {"name1": "USDA - Kerrville MLRA Office"},
            {
                "name1_enriched": "United States Department of Agriculture",
                "name2_enriched": "Kerrville MLRA Soil Survey Office",
            },
            populated={"name2": "Kerrville MLRA Office"},
            from_tier3=("name2",),
        )
        assert out["name2_enriched"] == "Kerrville MLRA Soil Survey Office"

    def test_a_guess_in_a_genuinely_blank_slot_is_still_dropped(self):
        out = _finalised(
            {"name1": "United States Department of Agriculture"},
            {
                "name1_enriched": "United States Department of Agriculture",
                "name2_enriched": "Natural Resources Conservation Service",
            },
            populated={},
            from_tier3=("name2",),
        )
        assert out["name2_enriched"] is None

    def test_the_marker_does_not_leak_into_the_output(self):
        out = _finalised(
            {"name1": "USDA - Kerrville MLRA Office"},
            {"name1_enriched": "United States Department of Agriculture",
             "name2_enriched": "Kerrville MLRA Office"},
            populated={"name2": "Kerrville MLRA Office"},
        )
        assert "_preprocess_populated" not in out


class TestTheUC14LeftwardPack:
    """The same defect reached through UC 14 instead of the parent-org split.

    Row 13036137 of the government-labs batch arrived with a gap in the block:

        Name 1  Naval Info Warfare Center
        Name 2  (empty)
        Name 3  PAC Receiving Bldg

    UC 14 packs the block leftward, so the building lands in Name 2 with
    `name2_original` empty — the same starting state the USDA split produces,
    reached by a different route. It shipped in the 2026-08-27 00:35 export and
    was gone from the next one; nothing about the record changed, only whether
    Tier 3 happened to answer for Name 2 that run. When it did, the guard let
    the answer through and §6c dropped it as an invention into a blank slot,
    and the building went nowhere.
    """

    def test_uc14_moves_the_building_up_into_name2(self):
        pre = preprocess_record(
            name1="Naval Info Warfare Center", name2=None,
            name3="PAC Receiving Bldg", contact=None, email=None,
            street1="Pacific Hwy", street2=None, street3=None,
            house_number="4297",
        )
        assert 14 in pre.use_cases
        assert pre.name2 == "PAC Receiving Bldg"
        assert pre.name3 is None

    def test_the_packed_building_is_guarded_from_a_different_unit(self):
        r = _init_result(EnrichmentRecord(
            record_id="13036137", country="US",
            name1="Naval Info Warfare Center", name3="PAC Receiving Bldg",
        ))
        seed(r, name2_enriched="PAC Receiving Bldg")
        seed(r, _preprocess_populated={"name2": "PAC Receiving Bldg"})
        _apply_tier3(r, Tier3Result(
            success=True, confidence="medium",
            name2_suggestion="Naval Information Warfare Systems Command",
        ))
        assert r["name2_enriched"] == "PAC Receiving Bldg"
        assert not r.get("_name2_from_tier3")

    def test_the_packed_building_survives_finalise(self):
        # name3 is in `_preprocess_cleared` — UC 14 emptied it — so the
        # passthrough cannot put the building back there either. If §6c drops
        # Name 2, the value is gone from the record entirely.
        out = _finalised(
            {"name1": "Naval Info Warfare Center", "name3": "PAC Receiving Bldg"},
            {
                "name1_enriched": "Naval Info Warfare Center",
                "name2_enriched": "PAC Receiving Bldg",
            },
            populated={"name2": "PAC Receiving Bldg"},
            from_tier3=("name2",),
            cleared=("name3",),
        )
        assert out["name2_enriched"] == "PAC Receiving Bldg"
