"""A name fetched out of a street field is normalised, not shipped verbatim.

Row 13056385 of the government-labs batch arrived with an empty Name 2 and its
unit written into a street line. Preprocessing's street→name router did the
right thing and moved it up:

    Name 1  US Army
    Name 2  Center For Def Scnce Studies

and then the value shipped exactly like that — the address line's own casing,
flagged `low-confidence-unchanged` ("left exactly as supplied"). Nobody
supplied it. `finalise` skipped its unit canonicaliser because UC 5 leaves a
GRANULAR unit ("Center for X", "X Lab") verbatim, and that protection exists so
the pipeline never rewords a department someone typed into the name block.

The rule these tests pin: UC 5's protection follows where the value came from.
A value the routers lifted out of a street is the pipeline's own, so it is
canonicalised like any other name the pipeline writes — and a value that was
supplied in the name block, or that a tier has since answered for, is left
alone exactly as before.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _init_result, finalise
from enrichment.preprocess import preprocess_record
from tests.conftest import seed


def _pp(**fields):
    base = dict(
        name1=None, name2=None, name3=None, name4=None, name5=None,
        contact=None, email=None,
        street1=None, street2=None, street3=None,
    )
    base.update(fields)
    return preprocess_record(**base)


def _finalised(inputs, enriched, from_street=None):
    """Finalise one record whose tiers settled on *enriched*.

    *from_street* is the marker preprocessing hands to finalise: the name slots
    it filled from a street field, mapped to the value it put there.
    """
    r = _init_result(EnrichmentRecord(record_id="t", country="US", **inputs))
    seed(r, **{f"{slot}_enriched": value for slot, value in enriched.items()})
    seed(r, _names_from_street=dict(from_street or {}))
    return finalise(r, time.monotonic())


class TestPreprocessMarksWhatItFetched:
    def test_the_army_row(self):
        # The row that prompted this: the unit is in a street line and the
        # name block has only the organisation.
        r = _pp(
            name1="US Army",
            street1="Center For Def Scnce Studies",
            street2="Picatinny Arsenal",
        )
        assert r.name2 == "Center For Def Scnce Studies"
        assert r.names_from_street == {"name2"}
        # The street line it came from is emptied; the real address stays.
        assert r.street1 is None
        assert r.street2 == "Picatinny Arsenal"

    def test_an_organisation_in_a_street_is_marked_too(self):
        r = _pp(street1="University of Miami Hospital")
        assert r.name1 == "University of Miami Hospital"
        assert r.names_from_street == {"name1"}

    def test_a_value_supplied_in_the_name_block_is_not_marked(self):
        r = _pp(name1="US Army", name2="Center For Def Scnce Studies")
        assert r.name2 == "Center For Def Scnce Studies"
        assert r.names_from_street == set()

    def test_nothing_is_marked_on_a_record_with_a_plain_address(self):
        r = _pp(name1="Stanford University", street1="450 Serra Mall")
        assert r.names_from_street == set()

    def test_the_mark_follows_the_value_when_packing_moves_it(self):
        # The router places the department in the first empty slot; UC 14 then
        # packs the block leftward and the value changes slot. A slot name
        # recorded at routing time would name the wrong value by the end —
        # the mark is resolved from the value, after packing.
        r = _pp(
            name1="Stanford University",
            name3="Department of Genetics",
            street1="Cancer Research Center",
        )
        block = [r.name2, r.name3, r.name4]
        assert "Cancer Research Center" in block
        slot = f"name{block.index('Cancer Research Center') + 2}"
        assert r.names_from_street == {slot}


class TestFinaliseCanonicalisesWhatCameFromAStreet:
    def test_the_army_row_ships_canonicalised(self):
        r = _finalised(
            {"name1": "US Army", "name2": None},
            {"name1": "US Army", "name2": "Center For Def Scnce Studies"},
            from_street={"name2": "Center For Def Scnce Studies"},
        )
        assert r["name2_enriched"] == "Center for Def Scnce Studies"

    def test_a_suffix_form_is_reordered(self):
        """The canonicaliser's full job — for the units that really invert.

        Changed deliberately. "Center", like "Institute", "Laboratory" and
        "College", NAMES an organisation when it trails, so reordering it
        fabricates a unit nobody uses: "Texas Heart Institute" shipped as
        "Institute of Texas Heart". Reordering is now scoped to Department /
        Division / School / Faculty, where the two forms are interchangeable.
        A street-sourced value is still canonicalised — this asserts the same
        rule on a unit word that genuinely carries it.
        """
        r = _finalised(
            {"name1": "Stanford University", "name2": None},
            {"name1": "Stanford University", "name2": "CHEMISTRY DEPARTMENT"},
            from_street={"name2": "CHEMISTRY DEPARTMENT"},
        )
        assert r["name2_enriched"] == "Department of Chemistry"

    def test_an_entity_naming_unit_word_is_not_reordered(self):
        """The other half of the same rule: a trailing "Center" is a name."""
        r = _finalised(
            {"name1": "Stanford University", "name2": None},
            {"name1": "Stanford University", "name2": "CANCER RESEARCH CENTER"},
            from_street={"name2": "CANCER RESEARCH CENTER"},
        )
        assert r["name2_enriched"] == "Cancer Research Center"

    def test_a_granular_unit_supplied_in_the_name_block_is_still_verbatim(self):
        # UC 5, unchanged: nobody may reword a unit someone typed.
        r = _finalised(
            {"name1": "US Army", "name2": "Center For Def Scnce Studies"},
            {"name1": "US Army", "name2": "Center For Def Scnce Studies"},
        )
        assert r["name2_enriched"] == "Center For Def Scnce Studies"

    def test_a_value_a_tier_replaced_keeps_the_tier_s_wording(self):
        # The slot was filled from a street, but a tier has since answered for
        # it. The tier is the authority on its own wording — the mark names a
        # slot, and this is no longer the value that was marked.
        r = _finalised(
            {"name1": "US Army", "name2": None},
            {"name1": "US Army", "name2": "Armaments Research Center"},
            from_street={"name2": "Center For Def Scnce Studies"},
        )
        assert r["name2_enriched"] == "Armaments Research Center"

    @pytest.mark.parametrize("value", [
        "Department of Chemistry",   # already canonical
        "Office of the Director",    # no unit word the canonicaliser rewrites
    ])
    def test_a_street_sourced_value_with_nothing_to_fix_is_untouched(self, value):
        r = _finalised(
            {"name1": "Stanford University", "name2": None},
            {"name1": "Stanford University", "name2": value},
            from_street={"name2": value},
        )
        assert r["name2_enriched"] == value


def test_the_marker_never_reaches_the_output():
    """It is a transient, like the other underscore keys finalise strips."""
    r = _finalised(
        {"name1": "US Army", "name2": None},
        {"name1": "US Army", "name2": "Center For Def Scnce Studies"},
        from_street={"name2": "Center For Def Scnce Studies"},
    )
    assert "_names_from_street" not in r
