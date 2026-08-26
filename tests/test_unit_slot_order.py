"""A division is written above the department in the name block.

The rule these tests pin:

* whichever order the tiers filled the department slots in, the slot holding a
  "Division of X" is written ABOVE the one holding a "Department of X" — for
  "State Of Ohio" / "Dept Of Agriculture" / "Div Of Animal Health" that means
  Name 2 and Name 3 come out switched;
* both wordings count: the canonical prefix form ("Division of Animal Health")
  and the suffix form ("Animal Health Division"), and the abbreviations
  ("Div", "Dept") they arrive as;
* Name 1 never takes part — it holds the organisation, not a unit;
* a slot holding neither construction (a branch, a lab, an overflow fragment)
  keeps the position packing gave it: the rule reorders the division/department
  slots among THEMSELVES and shuffles nothing else;
* two units of the same kind keep the order the tiers produced;
* the values are unchanged — only which slot each sits in — so the reorder is a
  transform and attribution follows the value into its new slot.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _init_result, finalise
from tests.conftest import seed
from utils.text_utils import ordered_unit_word


def _finalised(inputs: dict[str, str | None], enriched: dict[str, str | None]):
    """Finalise one record whose tiers settled on *enriched*."""
    r = _init_result(EnrichmentRecord(record_id="t", country="US", **inputs))
    seed(r, **{f"{slot}_enriched": value for slot, value in enriched.items()})
    return finalise(r, time.monotonic())


class TestOrderedUnitWord:
    @pytest.mark.parametrize("value,expected", [
        ("Division of Animal Health", "Division"),
        ("Div Of Animal Health", "Division"),
        ("Div. of Cardiology", "Division"),
        ("Animal Health Division", "Division"),
        ("Department of Agriculture", "Department"),
        ("Dept Of Agriculture", "Department"),
        ("Agriculture Dept", "Department"),
        # Neither construction — not ordered by this rule.
        ("Bioanalytical Methods Branch", None),
        ("Office of the Director", None),
        ("State Of Ohio", None),
        ("Animal Health", None),
        (None, None),
        ("   ", None),
    ])
    def test_construction_is_recognised(self, value, expected):
        assert ordered_unit_word(value) == expected

    def test_the_leading_construction_wins(self):
        """A slot holding a whole hierarchy answers for the unit it opens
        with, so it orders as that unit rather than as neither."""
        assert ordered_unit_word(
            "Department of Agriculture Division of Animal Health"
        ) == "Department"


class TestDivisionIsWrittenAboveDepartment:
    def test_the_ohio_block_switches_name_2_and_name_3(self):
        out = _finalised(
            {"name_1": "State Of Ohio", "name_2": "Dept Of Agriculture"},
            {
                "name1": "State of Ohio",
                "name2": "Department of Agriculture",
                "name3": "Division of Animal Health",
            },
        )
        assert out["name1_enriched"] == "State of Ohio"
        assert out["name2_enriched"] == "Division of Animal Health"
        assert out["name3_enriched"] == "Department of Agriculture"

    def test_a_block_already_in_order_is_left_alone(self):
        out = _finalised(
            {"name_1": "State Of Ohio"},
            {
                "name1": "State of Ohio",
                "name2": "Division of Animal Health",
                "name3": "Department of Agriculture",
            },
        )
        assert out["name2_enriched"] == "Division of Animal Health"
        assert out["name3_enriched"] == "Department of Agriculture"

    def test_the_suffix_wording_orders_too(self):
        """UC 5 leaves a granular unit's suffix form alone, so the rule has to
        read it where it stands."""
        out = _finalised(
            {"name_1": "Acme Corp"},
            {
                "name1": "Acme Corp",
                "name2": "Quality Department",
                "name3": "Metrology Division",
            },
        )
        assert out["name2_enriched"] == "Division of Metrology"
        assert out["name3_enriched"] == "Department of Quality"

    def test_name_1_never_takes_part(self):
        """Name 1 holds the organisation. A department sitting below a
        DIVISION-named institution must not be lifted into it."""
        out = _finalised(
            {"name_1": "Metrology Division"},
            {
                "name1": "Metrology Division",
                "name2": "Department of Quality",
            },
        )
        assert out["name1_enriched"] == "Metrology Division"
        assert out["name2_enriched"] == "Department of Quality"

    def test_an_unordered_slot_keeps_its_position(self):
        """Only the division/department slots move, and only among
        themselves — Name 2's branch is not shuffled around them."""
        out = _finalised(
            {"name_1": "US Food and Drug Administration"},
            {
                "name1": "US Food and Drug Administration",
                "name2": "Bioanalytical Methods Branch",
                "name3": "Department of Agriculture",
                "name4": "Division of Bioanalytical Chemistry",
            },
        )
        assert out["name2_enriched"] == "Bioanalytical Methods Branch"
        assert out["name3_enriched"] == "Division of Bioanalytical Chemistry"
        assert out["name4_enriched"] == "Department of Agriculture"

    def test_two_divisions_keep_the_order_they_arrived_in(self):
        out = _finalised(
            {"name_1": "Acme Corp"},
            {
                "name1": "Acme Corp",
                "name2": "Division of Animal Health",
                "name3": "Division of Plant Health",
            },
        )
        assert out["name2_enriched"] == "Division of Animal Health"
        assert out["name3_enriched"] == "Division of Plant Health"

    def test_the_values_are_only_moved_never_rewritten(self):
        out = _finalised(
            {"name_1": "State Of Ohio", "name_2": "Dept Of Agriculture"},
            {
                "name1": "State of Ohio",
                "name2": "Department of Agriculture",
                "name3": "Division of Animal Health",
            },
        )
        assert {out["name2_enriched"], out["name3_enriched"]} == {
            "Division of Animal Health", "Department of Agriculture",
        }
        # A moved value still carries the provenance of whatever produced it,
        # so it survives the admissibility gate in its new slot.
        assert out["name3_enriched"] == "Department of Agriculture"
        assert out["name2_provenance"]
