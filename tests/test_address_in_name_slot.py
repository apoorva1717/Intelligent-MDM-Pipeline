"""Address content in a name slot reaches an address field instead of being lost.

Two shapes the pipeline used to drop, both of them the record's own address
written into the name block:

* a street line carrying a delivery instruction — Princeton's "Crystalline Dr.
  Loading Dock" in Name 3. Nothing routed it, so it reached the grounded lane,
  which classified it `noise` and cleared the field. The record shipped with
  neither the street nor the dock, and `Flag for Review` False;
* a trailing site qualifier — "Merck Research Laboratories - Rahway, NJ",
  "Veracyte, Inc. - South San Francisco, CA". The site is what the address
  block already says, and carrying it in the name cost the name twice: no
  registry matched it, and the identity guard read the canonical form that
  drops it as naming a DIFFERENT unit, so the record shipped its own input
  back under "the canonical form could not be established".
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import name_gate
from enrichment.address_processing import process_address
from enrichment.flags import NAME_STATES_ANOTHER_SITE, compute_flags
from enrichment.locality import split_site_suffix
from enrichment.preprocess import (
    _split_street_access_qualifier,
    preprocess_record,
)
from utils.name_identity import DIFFERENT, SAME


# ── The street + delivery-instruction split ──────────────────────────────────

@pytest.mark.parametrize("value,street,qualifier", [
    ("Crystalline Dr. Loading Dock", "Crystalline Dr", "Loading Dock"),
    ("Crystalline Drive Receiving Bay", "Crystalline Drive", "Receiving Bay"),
    ("300 Tech Park Dr NEAR LOADING DOCK B", "300 Tech Park Dr",
     "NEAR LOADING DOCK B"),
    ("1100 Commerce Way BEHIND WAREHOUSE 4", "1100 Commerce Way",
     "BEHIND WAREHOUSE 4"),
    # A one-word instruction is enough when a positional preposition leads it.
    ("300 Tech Park Dr NEAR SHIPPING", "300 Tech Park Dr", "NEAR SHIPPING"),
])
def test_street_with_a_delivery_instruction_splits(value, street, qualifier):
    assert _split_street_access_qualifier(value) == (street, qualifier)


@pytest.mark.parametrize("value", [
    "Loading Dock B",          # no street in front — the logistics extractor's
    "Dock Loading Bay",        # nothing before the street-type word
    "Ivy Lane",                # a street and nothing else
    "Department of Chemistry",
    "Park Lane Shipping",      # a company whose last word is a logistics one
    "Massachusetts Institute of Technology",
])
def test_values_that_are_not_a_street_plus_instruction(value):
    assert _split_street_access_qualifier(value) is None


class TestStreetAndInstructionRouting:
    """Princeton 13361199 — Name 3 "Crystalline Dr. Loading Dock"."""

    def _run(self, **over):
        fields = dict(
            name1="Princeton University", name2=None,
            name3="Crystalline Dr. Loading Dock",
            contact=None, email=None,
            street1="IVY LANE", street2=None, street3=None,
            house_number="35", city="PRINCETON", region="NJ",
        )
        fields.update(over)
        return preprocess_record(**fields)

    def test_both_halves_reach_a_street_slot(self):
        res = self._run()
        assert res.name2 is None
        assert res.name3 is None
        assert res.street1 == "IVY LANE"
        assert res.street2 == "Crystalline Dr"
        assert res.street3 == "Loading Dock"

    def test_name1_is_never_split(self):
        res = self._run(name1="Crystalline Dr. Loading Dock", name3=None)
        assert res.name1 == "Crystalline Dr. Loading Dock"

    def test_nothing_moves_when_only_one_slot_is_free(self):
        # Half an address placed and half dropped is the same silent loss in a
        # second form — the value stays put and the record says so.
        res = self._run(street2="A St", street3="B St", street4="C St")
        assert res.name2 == "Crystalline Dr. Loading Dock"
        assert res.street5 is None
        assert "street-slots-full" in res.flags

    def test_the_address_stage_moves_the_instruction_to_unloading_point(self):
        res = self._run()
        addr = asyncio.run(process_address(
            record_id="13361199", name1=res.name1,
            name2=None, name3=None, name4=None, name5=None,
            street=res.street1, street_2=res.street2, street_3=res.street3,
            street_4=None, street_5=None,
            city="PRINCETON", state="NJ", zip_code="08540", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        ))
        assert addr.street_2_cleaned == "Crystalline Dr"
        assert addr.street_3_cleaned is None
        assert addr.unloading_point == "Loading Dock"


# ── The site qualifier ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,record,core,site", [
    # The region is the record's own.
    ("Merck Research Laboratories - Rahway, NJ", {"city": "RAHWAY", "region": "NJ"},
     "Merck Research Laboratories", "Rahway, NJ"),
    # A different site: accepted on the multi-word city alone.
    ("Veracyte, Inc. - South San Francisco, CA",
     {"city": "Pine Brook", "region": "NJ"},
     "Veracyte, Inc.", "South San Francisco, CA"),
    # The region spelled out.
    ("HCA Florida University Hospital - Davie, Florida",
     {"city": "Davie", "region": "FL"},
     "HCA Florida University Hospital", "Davie, Florida"),
])
def test_site_qualifier_splits(value, record, core, site):
    assert split_site_suffix(value, **record) == (core, site)


@pytest.mark.parametrize("value,record", [
    # "PA" is a professional association far more often than Pennsylvania, and
    # a one-word "city" this record cannot recognise is not a place.
    ("Smith - Jones, PA", {"city": "Newark", "region": "NJ"}),
    # The trailing segment names no region at all.
    ("Novartis - Global Drug Development", {"city": "Basel", "region": None}),
    # A hyphen inside a name is not a site separator.
    ("Wal-Mart Stores, AR", {"city": "Bentonville", "region": "AR"}),
    ("Coca-Cola", {}),
    ("Massachusetts Institute of Technology", {}),
])
def test_values_that_carry_no_site_qualifier(value, record):
    assert split_site_suffix(value, **record) is None


class TestSiteQualifierStripping:
    def test_a_unit_keeps_its_name_and_loses_the_site(self):
        """Merck 13348232 — Name 2 "Merck Research Laboratories - Rahway, NJ"."""
        res = preprocess_record(
            name1="Merck & Company",
            name2="Merck Research Laboratories - Rahway, NJ",
            name3=None, contact=None, email=None,
            street1="126 East Lincoln Ave BLDG", street2=None, street3=None,
            city="RAHWAY", region="NJ",
        )
        assert res.name2 == "Merck Research Laboratories"

    def test_an_alias_of_name1_collapses_once_the_site_is_off(self):
        """Veracyte 13348125 — Name 2 was Name 1 plus a site, nothing more."""
        res = preprocess_record(
            name1="Veracyte, Inc.",
            name2="Veracyte, Inc. - South San Francisco, CA",
            name3=None, contact=None, email=None,
            street1="25 Riverside Dr", street2=None, street3=None,
            city="Pine Brook", region="NJ",
        )
        assert res.name1 == "Veracyte, Inc."
        assert res.name2 is None

    def test_the_site_is_never_written_into_the_address_block(self):
        # "South San Francisco, CA" contradicts the record's own city and
        # state; recording it as a street line would corrupt the address.
        res = preprocess_record(
            name1="Veracyte, Inc.",
            name2="Veracyte, Inc. - South San Francisco, CA",
            name3=None, contact=None, email=None,
            street1="25 Riverside Dr", street2=None, street3=None,
            city="Pine Brook", region="NJ",
        )
        assert res.street2 is None
        assert any("South San Francisco, CA" in f for f in res.flags)


# ── Two places on one record ─────────────────────────────────────────────────

class TestConflictingSiteIsReported:
    """The name said one place, the address block says another. The qualifier
    comes off the name either way; which place the record is FOR is the
    steward's call, so it is handed up rather than dropped with the text."""

    def _run(self, name2, **over):
        fields = dict(
            name1="Acme Corp", name2=name2, name3=None,
            contact=None, email=None,
            street1="1 Main St", street2=None, street3=None,
            city="RAHWAY", region="NJ",
        )
        fields.update(over)
        return preprocess_record(**fields)

    def test_a_different_state_is_a_conflict(self):
        res = self._run(
            "Veracyte, Inc. - South San Francisco, CA",
            name1="Veracyte, Inc.", city="Pine Brook",
        )
        assert res.site_conflict == (
            "Name 2 states South San Francisco, CA; record says Pine Brook, NJ"
        )

    def test_a_different_city_in_the_same_state_is_a_conflict(self):
        res = self._run("Acme Corp - Kenilworth, NJ")
        assert res.site_conflict == (
            "Name 2 states Kenilworth, NJ; record says RAHWAY, NJ"
        )

    def test_the_same_place_is_not_a_conflict(self):
        res = self._run(
            "Merck Research Laboratories - Rahway, NJ",
            name1="Merck & Company",
        )
        assert res.name2 == "Merck Research Laboratories"
        assert res.site_conflict is None

    def test_a_record_stating_no_place_of_its_own_raises_nothing(self):
        # A contradiction needs two claims; one is not made out of a blank
        # column. Without a record place the suffix rule accepts only the
        # multi-word city, and it reports no conflict.
        res = self._run(
            "Acme Corp - South San Francisco, CA", city=None, region=None,
        )
        assert res.name2 is None
        assert res.site_conflict is None

    def test_the_flag_names_both_places_and_asks_for_a_review(self):
        result = {
            "record_id": "13348125",
            "name1_original": "Veracyte, Inc.",
            "name1_enriched": "Veracyte, Inc.",
            "name2_original": "Veracyte, Inc. - South San Francisco, CA",
            "name2_enriched": None,
            "domain": "veracyte.com",
            "lei_id": "529900ESWZRHXOW27Z37",
            "_ev_name_site_conflict": (
                "Name 2 states South San Francisco, CA; "
                "record says Pine Brook, NJ"
            ),
        }
        compute_flags(result)
        assert result["flag_codes"] == [NAME_STATES_ANOTHER_SITE]
        assert result["flagged_fields"] == ["address"]
        # Not advisory: the record contradicts itself, not an outside register.
        assert result["flag_for_review"] is True
        assert "South San Francisco, CA" in result["flag_reason"]
        assert "Pine Brook, NJ" in result["flag_reason"]
        # The transient evidence key never reaches the response model.
        assert "_ev_name_site_conflict" not in result


class TestNameGateReadsThroughTheSiteQualifier:
    """`_slot_input_value` hands the gate the RAW SAP value, so the gate has to
    take the suffix off itself — preprocessing stripping the field is not
    enough."""

    record = {"record_id": "13348232", "city": "RAHWAY", "region": "NJ"}

    def test_dropping_the_site_is_not_a_different_entity(self):
        decision = name_gate.evaluate(
            dict(self.record), "name2", "Merck Research Laboratories",
            incumbent="Merck Research Laboratories - Rahway, NJ",
            street="126 East Lincoln Ave", country="US",
        )
        assert decision.allow is True
        assert decision.verdict == SAME

    def test_a_subject_swap_is_still_refused(self):
        decision = name_gate.evaluate(
            dict(self.record), "name2", "Procurement Services",
            incumbent="Office of Purchasing - Rahway, NJ",
            street=None, country="US",
        )
        assert decision.allow is False
        assert decision.verdict == DIFFERENT


# ── The attention line that addresses a place ────────────────────────────────
#
# Dana-Farber 13190988 — Street 2 "Attn: Loading Dock". `_extract_care_of` runs
# before `_extract_logistics` in the street-slot loop, so the marked payload was
# taken as a care-of party and the record shipped `Care Of = "Loading Dock"`:
# the dock named as the recipient, and Unloading Point — the field that exists
# for exactly this — left blank. A BARE "Loading Dock" in the same slot has
# always reached Unloading Point; only the marker diverted it.

class TestLogisticsPayloadInAnAttentionLine:
    """Street-slot path only. The name-slot path (UC 15) is pinned below."""

    def _addr(self, street_2, unloading_seed=None):
        # A seed goes in the EARLIER slot: the loop walks s1…s5 in order, so
        # only a slot before the marked one has already claimed the field.
        if unloading_seed:
            street_2, street_3 = unloading_seed, street_2
        else:
            street_3 = None
        return asyncio.run(process_address(
            record_id="13190988", name1="Dana-Farber Cancer Inst Inc",
            name2=None, name3=None, name4=None, name5=None,
            street="LONGWOOD AVE", street_2=street_2, street_3=street_3,
            street_4=None, street_5=None,
            city="BOSTON", state="MA", zip_code="02215", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        ))

    @pytest.mark.parametrize("value", [
        "Attn: Loading Dock",
        "c/o Loading Dock",
    ])
    def test_a_marked_dock_reaches_unloading_point_not_care_of(self, value):
        addr = self._addr(value)
        assert addr.unloading_point == "Loading Dock"
        assert not addr.care_of_enriched
        assert addr.street_2_cleaned is None

    def test_only_the_dock_routes_not_the_rest_of_the_logistics_vocabulary(self):
        # Scope is the loading dock alone. Behind an Attn marker, "Receiving"
        # and its neighbours read as the desk handling the delivery — a party
        # — as often as a place, so they keep the Care Of behaviour they have.
        # The BARE forms still reach Unloading Point via `_extract_logistics`;
        # only the marked ones stay put.
        for value in ("Attn: Receiving", "Attn: Shipping", "Attn: Warehouse"):
            addr = self._addr(value)
            assert addr.care_of_enriched == value.split(": ", 1)[1], value
            assert not addr.unloading_point, value

    def test_a_person_behind_the_marker_is_still_a_care_of(self):
        # The guard is the logistics vocabulary, not the marker: an ordinary
        # attention line keeps the behaviour it has.
        addr = self._addr("Attn: Erin Murphy")
        assert addr.care_of_enriched == "Erin Murphy"
        assert not addr.unloading_point

    def test_recg_is_not_in_the_logistics_vocabulary(self):
        # The SAP receiving code normalises in the NAME slots (UC 6) and is not
        # a street-stage logistics keyword. Unchanged from HEAD: still a c/o.
        addr = self._addr("Attn: RECG")
        assert addr.care_of_enriched == "RECG"
        assert not addr.unloading_point

    def test_a_bare_dock_is_unchanged(self):
        addr = self._addr("Loading Dock")
        assert addr.unloading_point == "Loading Dock"
        assert not addr.care_of_enriched

    def test_a_trailing_identifier_rides_along(self):
        addr = self._addr("Attn: Loading Dock B")
        assert addr.unloading_point == "Loading Dock B"
        assert not addr.care_of_enriched

    def test_an_occupied_unloading_point_does_not_consume_the_slot(self):
        # Write and clear stand or fall together. With nowhere to put the
        # payload the slot keeps its text — the alternative is deleting it.
        addr = self._addr("Attn: Loading Dock", unloading_seed="Dock 7")
        assert addr.unloading_point == "Dock 7"
        # Still on the record, in a street slot. Which index is the slot
        # compaction's business, not this rule's — what matters is that the
        # text was not consumed into a field that refused to accept it.
        streets = [
            addr.street_cleaned, addr.street_2_cleaned, addr.street_3_cleaned,
            addr.street_4_cleaned, addr.street_5_cleaned,
        ]
        assert "Attn: Loading Dock" in streets
        assert not addr.care_of_enriched
        assert "G3-ADDR-011" in addr.address_issues


class TestTheNameSlotPathIsUntouched:
    """UC 15 owns the name slots and is not in scope — pinned so it stays so."""

    def _pre(self, name2):
        return preprocess_record(
            name1="Dana-Farber Cancer Inst Inc", name2=name2, name3=None,
            contact=None, email=None,
            street1="LONGWOOD AVE", street2=None, street3=None,
            house_number="360", city="BOSTON", region="MA",
        )

    def test_a_dock_in_a_name_slot_is_still_a_care_of(self):
        res = self._pre("Attn: Loading Dock")
        assert res.care_of == "Loading Dock"
        assert not res.contact

    def test_a_person_in_a_name_slot_still_reaches_contact(self):
        res = self._pre("Attn: Erin Murphy")
        assert res.contact == "Erin Murphy"
        assert res.care_of == "Erin Murphy"
