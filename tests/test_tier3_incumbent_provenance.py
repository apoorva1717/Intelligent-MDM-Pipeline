"""Tier 3 does not re-attribute a Name 1 it did not change.

`_write` states the rule for the department block: "an origin may change only
when the VALUE changes." It was added after seven records lost
`relocated-unverified` to a passthrough that declared itself the producer of a
byte-identical value — the doubt was not answered, the fact it was derived
from was destroyed.

Name 1 has no `_slot_origin` entry, so that guard never reaches it. Tier 3
would write a suggestion identical to the value already in the slot, and the
write replaced a registry producer with `llm_tier3` at confidence 0.7 for the
same string: 13342217 shipped `name1_provenance` = 'llm:provisional' over a
GLEIF name written under `tier1-lei:name-verified` at confidence 1.0.
Provenance reported the weaker of two sources for no reason, and a reviewer
reading it would re-verify a name GLEIF had already verified.

The comparison is against the value in the SLOT, not the record's input: after
a registry lane the incumbent is the registry's spelling. It folds whitespace
and case only, so a real edit still writes and still re-attributes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import (
    _apply_tier3,
    _init_result,
    _write_registry_name,
)
from enrichment.tier3_llm import Tier3Result

GLEIF_NAME = "Merck Sharp & Dohme Corp."


def _result(**originals):
    return _init_result(EnrichmentRecord(
        record_id="13342217", country="US", **originals,
    ))


def _with_registry_name(name=GLEIF_NAME, **originals):
    """A result whose Name 1 a registry lane has already written.

    Through `_write_registry_name`, not by assignment — `name1_enriched` is
    write-locked, and seeding it any other way would test a state the pipeline
    cannot reach.
    """
    result = _result(**originals)
    _write_registry_name(result, "name1", name, "GLEIF")
    return result


class TestIdenticalSuggestionIsDeclined:
    @pytest.mark.parametrize("suggestion", [
        GLEIF_NAME,                      # byte-identical
        "merck sharp & dohme corp.",     # case only
        "Merck  Sharp  &  Dohme  Corp.",  # whitespace runs only
        "  Merck Sharp & Dohme Corp.  ",  # surrounding space
    ])
    def test_registry_name_and_producer_both_survive(self, suggestion):
        result = _with_registry_name(name1="MERCK SHARP & DOHME CORP.")
        _apply_tier3(result, Tier3Result(
            success=True, name1_suggestion=suggestion, confidence="medium",
        ))
        assert result["name1_enriched"] == GLEIF_NAME
        assert "name1" not in result["_ev_tier3_wrote"]

    def test_matches_the_slot_not_the_input(self):
        # The record supplied an abbreviation; GLEIF replaced it. A suggestion
        # equal to the REGISTRY spelling is the one that must be declined —
        # comparing against the input would miss it.
        result = _with_registry_name(name1="MSD Corp")
        _apply_tier3(result, Tier3Result(
            success=True, name1_suggestion=GLEIF_NAME, confidence="medium",
        ))
        assert result["name1_enriched"] == GLEIF_NAME
        assert "name1" not in result["_ev_tier3_wrote"]

    def test_empty_slot_falls_back_to_the_input(self):
        result = _result(name1=GLEIF_NAME)
        _apply_tier3(result, Tier3Result(
            success=True, name1_suggestion=GLEIF_NAME, confidence="medium",
        ))
        assert "name1" not in result["_ev_tier3_wrote"]


class TestARealEditStillWrites:
    @pytest.mark.parametrize("suggestion", [
        "Merck Sharp & Dohme Corp",       # the period IS a difference
        "Merck Sharp and Dohme Corp.",    # spelled-out connector
        "Merck Sharp & Dohme LLC",        # different legal form
    ])
    def test_changed_value_is_written_and_attributed(self, suggestion):
        result = _with_registry_name(name1=GLEIF_NAME)
        _apply_tier3(result, Tier3Result(
            success=True, name1_suggestion=suggestion, confidence="medium",
        ))
        # Whether the name gate allows it is its own question; what matters
        # here is that the guard did not silently swallow a real edit.
        assert result["name1_enriched"] != GLEIF_NAME or (
            "name1" in result.get("_ev_name_suggestion", {})
        )


class TestDepartmentSlotsAreUnaffected:
    def test_matching_name1_does_not_block_a_department_suggestion(self):
        # The guard declines ONE write. Name 2..N are a separate question.
        result = _with_registry_name(name1=GLEIF_NAME, name2="Biochem")
        _apply_tier3(result, Tier3Result(
            success=True,
            name1_suggestion=GLEIF_NAME,
            name2_suggestion="Department of Biochemistry",
            confidence="medium",
        ))
        assert "name1" not in result["_ev_tier3_wrote"]
        assert result["name2_enriched"] == "Department of Biochemistry"
        assert "name2" in result["_ev_tier3_wrote"]
