"""Tests for street-field cleanup in address_processing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.address_processing import process_address


async def _street2(value):
    # The value is fed via Street 2 (to exercise secondary-slot cleanup), but
    # the cleaned result left-packs into Street 1 when Street 1 is empty.
    res = await process_address(
        record_id="x",
        name1="Acme Corp", name2=None, name3=None,
        street=None, street_2=value, street_3=None,
        city="Tampa", state="FL", zip_code="33620", country="US",
        po_box=None, care_of_enriched=None, llm_client=None,
    )
    return res.street_cleaned


class TestStreetCleanup:
    @pytest.mark.asyncio
    async def test_leading_also_connector_stripped(self):
        assert await _street2("ALSO 250 CENTRAL Ave") == "250 CENTRAL Ave"

    @pytest.mark.asyncio
    async def test_leading_and_connector_stripped(self):
        assert await _street2("AND 350 CENTRAL Ave") == "350 CENTRAL Ave"

    @pytest.mark.asyncio
    async def test_orphan_suite_marker_dropped(self):
        assert await _street2("Ste") is None

    @pytest.mark.asyncio
    async def test_orphan_marker_after_name_dropped(self):
        assert await _street2("Pinellas Bus Ctr Ste") == "Pinellas Bus Ctr"

    @pytest.mark.asyncio
    async def test_named_building_routed_to_building_field(self):
        # Item 3 scope table: a named building ("Research I Bldg") is routed to
        # the Building field, not left in the street (and never dropped).
        res = await process_address(
            record_id="t", name1="Acme Corp", name2=None, name3=None,
            street=None, street_2="Research I Bldg", street_3=None,
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        )
        assert res.building == "Research I Bldg"
        assert res.street_cleaned is None

    @pytest.mark.asyncio
    async def test_real_address_unchanged(self):
        assert await _street2("440 NICKERSON Rd") == "440 NICKERSON Rd"

    @pytest.mark.asyncio
    async def test_numbered_unit_still_extracted(self):
        # A suite WITH a number is still parsed (not treated as bare).
        res = await process_address(
            record_id="x", name1="Acme", name2=None, name3=None,
            street=None, street_2="Pinellas Bus Ctr, Ste 400D", street_3=None,
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        )
        assert res.suite == "400D"
        # Street 1 empty → the cleaned remainder left-packs into Street 1.
        assert res.street_cleaned == "Pinellas Bus Ctr"


async def _addr(street):
    return await process_address(
        record_id="x", name1="Acme", name2=None, name3=None,
        street=street, street_2=None, street_3=None,
        city="Boston", state="MA", zip_code="02210", country="US",
        po_box=None, care_of_enriched=None, llm_client=None,
    )


class TestFloorRoomCareOf:
    @pytest.mark.asyncio
    async def test_ordinal_floor_before_marker_extracted(self):
        # "7th Floor" (value-before-marker) → floor 7, and the orphan is not
        # left dangling in the street.
        res = await _addr("51 Sleeper Street, 7th Floor")
        assert res.floor == "7"
        assert res.street_cleaned == "51 Sleeper St"

    @pytest.mark.asyncio
    async def test_ordinal_floor_22nd(self):
        res = await _addr("100 Main Street 22nd Floor")
        assert res.floor == "22"
        assert res.street_cleaned == "100 Main St"

    @pytest.mark.asyncio
    async def test_marker_before_value_floor_still_works(self):
        res = await _addr("100 Main Street Floor 3")
        assert res.floor == "3"

    @pytest.mark.asyncio
    async def test_room_number_filler_word(self):
        # "Room number: F107" → room F107 (the filler "number" is skipped).
        res = await _addr("Room number: F107, 100 Main Street")
        assert res.room == "F107"

    @pytest.mark.asyncio
    async def test_room_no_dot_filler(self):
        res = await _addr("100 Main Street, Room No. 3")
        assert res.room == "3"

    @pytest.mark.asyncio
    async def test_plain_room_still_works(self):
        res = await _addr("100 Main Street, Room 12")
        assert res.room == "12"

    @pytest.mark.asyncio
    async def test_attn_person_then_street_split(self):
        # "Att. <person> <street> <floor>" → care_of person, street separated,
        # floor extracted (not all swallowed into care_of).
        res = await _addr("Att. Bayard Huck 200 Clarendon Street 22nd Floor")
        assert res.care_of_enriched == "Bayard Huck"
        assert res.street_cleaned == "200 Clarendon St"
        assert res.floor == "22"

    @pytest.mark.asyncio
    async def test_care_of_without_street_unchanged(self):
        # A plain c/o with no street still routes the whole payload to care_of.
        res = await _addr("c/o Dr. Jane Smith")
        assert res.care_of_enriched == "Dr. Jane Smith"


class TestThePrimaryStreetIsElectedByShapeNotSlot:
    """Records 13333689 and 13337503 are the same site with the same two lines
    in opposite street slots:

        13333689  Street 1 "LABORATORY/STE 150"    Street 2 "10300 CAMPUS POINT DRIVE"
        13337503  Street 1 "10300 CAMPUS POINT DRIVE"  Street 2 "Laboratory/Ste 150"

    Which slot a line arrived in says nothing about what it IS, but Street 1 is
    treated as the primary street throughout — it feeds the block id, it takes
    a different `allow_bare` mail-code path, and it is what the queries are
    built from. So the two rows diverged the whole way down from a difference
    that is pure data entry.

    The line that PARSES as an address (leading house number plus a street-type
    word) becomes Street 1; the residue routes through the existing addendum
    rules. Slot position stays the tiebreak among address-shaped lines only.
    """

    @staticmethod
    async def _run(street, street_2):
        return await process_address(
            record_id="x",
            name1="CALM/UCSD", name2=None, name3=None,
            street=street, street_2=street_2, street_3=None,
            city="SAN DIEGO", state="CA", zip_code="92121", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        )

    @pytest.mark.asyncio
    async def test_the_pair_produces_one_primary_street(self):
        a = await self._run("LABORATORY/STE 150", "10300 CAMPUS POINT DRIVE")
        b = await self._run("10300 CAMPUS POINT DRIVE", "Laboratory/Ste 150")
        assert a.street_cleaned == b.street_cleaned
        # Abbreviation normalisation runs here; the output casing pass runs
        # later, so the value is still in the record's own case at this point.
        assert a.street_cleaned == "10300 CAMPUS POINT Dr"

    @pytest.mark.asyncio
    async def test_the_suite_is_extracted_from_either_slot(self):
        a = await self._run("LABORATORY/STE 150", "10300 CAMPUS POINT DRIVE")
        b = await self._run("10300 CAMPUS POINT DRIVE", "Laboratory/Ste 150")
        assert a.suite == b.suite
        assert a.suite and "150" in a.suite

    @pytest.mark.asyncio
    async def test_a_record_whose_street_1_is_already_the_address_is_untouched(self):
        # 13343608's shape: the address is where it belongs, and the residue
        # stays behind it. Nothing to elect, so nothing moves.
        res = await self._run("1000 W CARSON ST", "Supply Chain Oper. Warehouse")
        assert res.street_cleaned == "1000 W CARSON ST"

    @pytest.mark.asyncio
    async def test_a_record_with_no_address_shaped_line_keeps_its_order(self):
        # Nothing to elect: neither line carries a house number, so the
        # partition is a no-op and the record is left exactly as it arrived.
        res = await self._run("Campus Point Dr", "Torrey Pines Rd")
        assert res.street_cleaned == "Campus Point Dr"
        assert res.street_2_cleaned == "Torrey Pines Rd"

    @pytest.mark.asyncio
    async def test_slot_position_is_the_tiebreak_among_address_shaped_lines(self):
        # Two real addresses: the election has no opinion between them, so the
        # slot they arrived in still decides.
        res = await self._run("10300 Campus Point Dr", "500 Torrey Pines Rd")
        assert res.street_cleaned == "10300 Campus Point Dr"
        assert res.street_2_cleaned == "500 Torrey Pines Rd"


class TestSplitResidueIsTrimmed:
    """13337503 shipped Name 2 as "Laboratory/".

    The suite came out of "Laboratory/Ste 150" and the slash it was attached to
    stayed behind. That separator is the split's own residue — punctuation
    whose other half the pipeline removed — not the record's text, and a
    fragment that is nothing BUT residue is not a name at all.
    """

    @pytest.mark.parametrize("fragment,expected", [
        ("Laboratory/", "Laboratory"),
        ("/Laboratory", "Laboratory"),
        ("Laboratory", "Laboratory"),
        ("  Ste /  ", "Ste"),
        ("A/B Lab", "A/B Lab"),      # an interior joiner is part of the text
        ("/", None),
        ("-, ", None),
        (None, None),
    ])
    def test_dangling_separators_are_stripped_at_both_ends(self, fragment, expected):
        from enrichment.address_processing import _trim_fragment

        assert _trim_fragment(fragment) == expected
