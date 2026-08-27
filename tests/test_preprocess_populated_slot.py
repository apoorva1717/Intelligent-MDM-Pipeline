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


def _finalised(inputs, enriched, populated=None, from_tier3=()):
    """Finalise one record whose tiers settled on *enriched*.

    *populated* is the marker preprocessing hands to finalise: the name slots
    it filled that the input left blank, mapped to the value it put there.
    """
    r = _init_result(EnrichmentRecord(record_id="t", country="US", **inputs))
    seed(r, **enriched)
    seed(r, _preprocess_populated=dict(populated or {}))
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
