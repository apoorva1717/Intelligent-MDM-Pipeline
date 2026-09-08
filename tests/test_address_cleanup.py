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


class TestTheCareOfMarkerMatchesOnlyAsAWholeWord:
    """Record 13337073 shipped `Street 1 = "307 Bo"` and `Care Of = "Er Rd,
    Ste 1"`.

    `_CARE_OF_RE` is applied with `.search()` and its marker carried no word
    boundaries, so `att?n+` matched the "ATN" inside "BO**ATN**ER": the street
    was cut at the sixth character, everything after the false marker became a
    c/o payload, and `STE 1` went with it — so the suite was never extracted
    either. The marker is a substring of ordinary words, which is what makes
    the boundaries load-bearing rather than tidy.

    Both boundaries are needed. A leading one alone still admits "ATTNER",
    where the marker opens the word but does not end it. `_ATTN_RE` (UC 7)
    already had both and is the model these follow.
    """

    @pytest.mark.parametrize("street,house,suite", [
        ("307 BOATNER RD, STE 1", "307", "1"),
        ("9 PATTON DR", "9", None),
        ("40 CATTNER Blvd", "40", None),
        ("12 ATNAM ST", "12", None),
        ("1500 PATTERSON AVE", "1500", None),
    ])
    @pytest.mark.asyncio
    async def test_a_street_holding_the_letters_parses_whole(
        self, street, house, suite,
    ):
        res = await process_address(
            record_id="x", name1="Acme Corp", name2=None, name3=None,
            street=street, street_2=None, street_3=None,
            city="Eglin AFB", state="FL", zip_code="32542", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        )
        assert res.care_of_enriched is None
        assert res.suite == suite
        # The street survives intact — house number still attached, nothing
        # lopped off the front.
        assert res.street_cleaned is not None
        assert res.street_cleaned.startswith(house)

    @pytest.mark.parametrize("value,payload", [
        ("ATTN: HEMATOLOGY", "HEMATOLOGY"),
        ("Attn Receiving", "Receiving"),
        ("1201 NW 16TH ST ATTN HEMATOLOGY", "HEMATOLOGY"),
        ("c/o Jane Roe", "Jane Roe"),
        ("Atnn: Bob", "Bob"),          # the misspelling the marker still covers
        ("ATT: Payables", "Payables"),
    ])
    def test_a_real_marker_still_matches(self, value, payload):
        from enrichment.address_processing import _CARE_OF_RE

        m = _CARE_OF_RE.search(value)
        assert m is not None and m.group(1) == payload

    def test_a_word_that_merely_opens_with_the_marker_does_not(self):
        """The case a leading boundary alone would let through."""
        from enrichment.address_processing import _CARE_OF_RE
        from enrichment.preprocess import _CO_ATTN_PREFIX_RE

        assert _CARE_OF_RE.search("ATTNER Blvd") is None
        assert _CO_ATTN_PREFIX_RE.match("ATTNER Blvd") is None

    def test_the_two_stages_share_one_marker(self):
        """Not two copies that can drift — the address stage imports it."""
        from enrichment.address_processing import _CO_ATTN_MARKER as addr
        from enrichment.preprocess import _CO_ATTN_MARKER as pre

        assert addr is pre


# ---------------------------------------------------------------------------
# Named-building extraction (Item: named buildings in the secondary street
# slots). A named building written marker-last ("Heroy Bldg", "Equad A302")
# never reached the Building field: the `Bldg <id>` entry in
# ``_SUITE_PATTERNS`` is marker-FIRST, and ``_is_identifier_like`` rejects an
# alphabetic value of 3+ characters, so the name was left in the street slot
# while the trailing room code was pulled out from under it.
# ---------------------------------------------------------------------------


async def _named_building(value, name1="Acme Corp"):
    """Feed *value* through a SECONDARY slot (street_2) and return the result.

    Street 1 is left empty, so the cleaned remainder left-packs into
    ``street_cleaned`` — the same convention the tests above use.
    """
    return await process_address(
        record_id="nb", name1=name1, name2=None, name3=None,
        street=None, street_2=value, street_3=None,
        city="Tampa", state="FL", zip_code="33620", country="US",
        po_box=None, care_of_enriched=None, llm_client=None,
    )


class TestNamedBuildingMoves:
    """Values that MUST be routed into Building (+ Room), leaving the source
    street slot blank."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,building,room", [
        # The marker STAYS in the Building value — the established convention
        # (`_named_building_value` has always returned the full phrase, and
        # "Research I Bldg" above locks it in). It preserves the input and is
        # what the steward sees in SAP. The room-code-only shape has no marker
        # to keep, so it yields a bare name.
        ("Heroy Bldg/Rm 450", "Heroy Bldg", "450"),
        ("Heroy Bldg Rm 450", "Heroy Bldg", "450"),
        ("Equad A302", "Equad", "A302"),
        ("Fairchild Science Bldg", "Fairchild Science Bldg", None),
        ("Moore Hall, Room 12", "Moore Hall", "12"),
    ])
    async def test_named_building_and_room_are_extracted(self, value, building, room):
        res = await _named_building(value)
        assert res.building == building
        assert res.room == room
        # The source slot is emptied — a residual of only separators ("Heroy
        # Bldg/" → "/") must not survive as a street value.
        assert res.street_cleaned is None
        assert res.street_2_cleaned is None

    @pytest.mark.asyncio
    async def test_room_marker_is_never_taken_as_the_building(self):
        """Regression. The marker-first entry (`Bldg <id>`) captured the "Rm"
        of "Heroy Bldg Rm 450" as the building id — `_is_identifier_like`
        accepts it at two characters — yielding Building="Rm" and a street of
        "Heroy 450". The named-building path must run first."""
        res = await _named_building("Heroy Bldg Rm 450")
        assert res.building != "Rm"
        assert res.building == "Heroy Bldg"
        assert res.room == "450"
        assert res.street_cleaned is None

    @pytest.mark.asyncio
    async def test_marker_first_building_id_is_unchanged(self):
        """The existing marker-first rule ("Bldg 12" → Building=12) keeps
        working exactly as before."""
        res = await _named_building("Bldg 12")
        assert res.building == "12"
        assert res.room is None
        assert res.street_cleaned is None


class TestNamedBuildingLeavesAlone:
    """Values that must NOT be pulled into Building by the named-building
    rule. Each row names the property that disqualifies it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,name1", [
        ("Hall St", "Acme Corp"),                       # street-type word
        ("Hall St 305", "Acme Corp"),                   # street-type word + a
                                                        # trailing number: a
                                                        # street address, and
                                                        # "Hall" leads the
                                                        # value so there is no
                                                        # prefix to name a
                                                        # building with
        ("Dept of Chemistry Building", "Acme Corp"),    # department shape
        ("Attn: Dr Hall", "Acme Corp"),                 # person / contact shape
        ("SMU Bldg", "Southern Methodist University"),  # prefix is the org acronym
        ("Southern Methodist University Bldg",
         "Southern Methodist University"),              # prefix equals Name 1
        ("Main Hall", "Acme Corp"),                     # bare generic prefix
        ("North Wing", "Acme Corp"),                    # bare directional prefix
        ("Receiving Bldg", "Acme Corp"),                # logistics term
    ])
    async def test_building_is_not_set(self, value, name1):
        res = await _named_building(value, name1=name1)
        assert res.building is None

    @pytest.mark.asyncio
    async def test_house_number_and_street_type_keep_the_street(self):
        """"123 Main St Building 4" is a street address. The existing
        marker-first rule may take Building=4; the street must survive."""
        res = await _named_building("123 Main St Building 4")
        assert res.street_cleaned == "123 Main St"
        assert res.building in (None, "4")

    @pytest.mark.asyncio
    async def test_mail_code_shape_still_goes_to_mail_code(self):
        """Mail-code extraction runs BEFORE sub-location extraction and keeps
        precedence: "MC302" is a mail code, never a building + room."""
        res = await _named_building("MC302")
        assert res.mail_code == "MC302"
        assert res.building is None
        assert res.room is None


# ---------------------------------------------------------------------------
# Named building with a TRAILING IDENTIFIER ("Genomics Bldg 1219B-MA").
#
# `_split_building_remainder` only ever split on a separator (/ , " - ") or a
# room/suite/floor word. A named building whose identifier follows the marker
# with neither — "Genomics Bldg 1219B-MA", "Research Bldg 2" — matched no shape
# at all, so `_named_building_value` declined and the value fell through to the
# marker-FIRST `Bldg <id>` entry in `_SUITE_PATTERNS`. That entry took the
# identifier as the whole Building value and orphaned the building's NAME as a
# street residual, where the residual classifier then read it as a department
# and relocated it into a name slot (row 13341769: Building="1219B-MA",
# Name 4="Genomics", origin `preprocess:street`).
#
# Rule: once a named building is recognised, the segment runs to the next
# separator or room word; with neither, to the end of the slot. The trailing
# identifier is NOT split off — "Genomics Bldg 1219B-MA" is one building.
# ---------------------------------------------------------------------------


class TestNamedBuildingTrailingIdentifier:
    """A named building keeps the identifier that trails its marker."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,building", [
        ("Genomics Bldg 1219B-MA", "Genomics Bldg 1219B-MA"),  # row 13341769
        ("Research Bldg 2", "Research Bldg 2"),
        ("Science Hall 305", "Science Hall 305"),
    ])
    async def test_trailing_identifier_stays_in_the_building(
        self, value, building,
    ):
        res = await _named_building(value)
        assert res.building == building
        # The identifier belongs to the building, so nothing is left for Room
        # — and the source slot is emptied, not left holding the bare name.
        assert res.room is None
        assert res.street_cleaned is None
        assert res.street_2_cleaned is None


class TestNamedBuildingStopsAtASublocationMarker:
    """The end-of-slot extension is BOUNDED by any `_SUITE_PATTERNS` marker,
    not only by the six words in `_NAMED_BUILDING_SUBLOC_RE`.

    Every row here regressed in the first A/B of this change: the building
    segment ran to the end of the slot and swallowed a Room, a Suite or a Mail
    Code that a deterministic extractor already owned. Two shapes are the
    boundary — a tail of two or more tokens (a marker plus a value, or two
    values), and a marker GLUED to its value ("#5380"), which a token count
    alone cannot see.

    The expected values are the ones the existing extractors produce, taken
    from the control run. They are not a design for these rows; they are the
    behaviour that must not change.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,building,room,suite,mail_code", [
        # A room AND a mail code trail the marker — a tail, not an identifier.
        ("Genentech Hall S252 MC2140", None, "S252", None, "MC2140"),
        # "Lab <id>" is a room; `_SUITE_PATTERNS` owns it, this must not.
        ("Enders Bldg Lab 649", None, "Lab 649", None, None),
        # "#" is shorthand for Suite and is glued to its value.
        ("Student Services Bldg #5380", None, None, "5380", None),
        ("Administration Bldg #514", None, None, "514", None),
        # "CODE:" introduces a room. Note Building is "L", NOT "Mary Moody
        # Northern Building L": the marker-first `Bldg <id>` entry takes the
        # single character after "Building" (`_is_identifier_like` accepts a
        # 1-2 character token), and that is the pre-existing behaviour this
        # change is required to leave alone.
        ("Mary Moody Northern Building L CODE: L14", "L", "L14", None, None),
    ])
    async def test_sublocation_markers_bound_the_building(
        self, value, building, room, suite, mail_code,
    ):
        res = await _named_building(value)
        assert res.building == building
        assert res.room == room
        assert res.suite == suite
        assert res.mail_code == mail_code


class TestNamedBuildingSeparatorFormsUnchanged:
    """The shapes that DO carry a separator or a room word keep splitting
    exactly as before. These are the controls for the rule above: extending a
    building segment to the end of the slot must not reach past a boundary
    that is actually present."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,building,room", [
        ("Heroy Bldg/Rm 450", "Heroy Bldg", "450"),
        ("Moore Hall, Room 12", "Moore Hall", "12"),
    ])
    async def test_separator_and_room_word_still_split(
        self, value, building, room,
    ):
        res = await _named_building(value)
        assert res.building == building
        assert res.room == room
        assert res.street_cleaned is None

    @pytest.mark.asyncio
    async def test_marker_first_shape_is_untouched(self):
        """"Bldg 12" has no prefix before the marker, so it names no building.
        It stays with the marker-first `Bldg <id>` entry, which owns it."""
        res = await _named_building("Bldg 12")
        assert res.building == "12"
        assert res.room is None
        assert res.street_cleaned is None


class TestNamedBuildingDetector:
    """G1-ADDR-003 reports the shape on the RAW input, independently of what
    the extractor does with it. Street 1 stays detect-only: the value is
    reported and left in place, and Building is never set from it."""

    def test_trailing_identifier_in_street_1_is_reported(self):
        from api.models import EnrichmentRecord
        from enrichment.issue_detection import detect_issues

        record = EnrichmentRecord.model_validate({
            "Name 1": "University of California Riverside",
            "Street 1": "Genomics Bldg 1219B-MA",
            "Postal Code": "92521",
            "Region": "CA",
            "Language Key": "EN",
            "Search Term 1": "UCR",
            "Country/Region Key": "US",
            "Tax Jurisdiction": "CA0000000",
        })
        assert "G1-ADDR-003" in detect_issues(record)

    @pytest.mark.xfail(strict=True, reason=(
        "SECOND DEFECT, on the primary line, deliberately not fixed here. "
        "`allow_rest=False` for Street 1 returns from `_named_building_value` "
        "BEFORE `_split_building_remainder` is reached, so the end-of-slot "
        "rule never applies there: :263 still takes `1219B-MA` as the Building "
        "and rewrites Street 1 to `Genomics`. Making Building stay blank means "
        "suppressing :263 on Street 1, which changes Street 1 output — the "
        "first STOP condition of this change's gate, and outside its "
        "allow-list shape (input Street 2-5). It gets its own prompt and its "
        "own gate, which will need a deliberate exception for `Street 1 "
        "restored to the input verbatim on rows :263 currently splits`. "
        "G1-ADDR-003 already reports the value (sibling test), so the shape is "
        "not silent on Street 1 meanwhile."
    ))
    @pytest.mark.asyncio
    async def test_street_1_keeps_the_value_and_sets_no_building(self):
        res = await process_address(
            record_id="nb-s1", name1="University of California Riverside",
            name2=None, name3=None,
            street="Genomics Bldg 1219B-MA", street_2=None, street_3=None,
            city="Riverside", state="CA", zip_code="92521", country="US",
            po_box=None, care_of_enriched=None, llm_client=None,
        )
        assert res.building is None


# ---------------------------------------------------------------------------
# PO Box carried through to the enrichment output.
#
# The "PO Box" output column maps to `po_box_extracted`, which is only ever set
# by street extraction. A PO Box that arrives in the DEDICATED column was read
# as `po_box_present` (for the G3-ADDR-005 conflict check) and never reached the
# output, so all 62 such rows across S1-S5 + dedup_STRESS_200_v1 shipped blank.
#
# Preserve-on-blank holds in `usp_MergeLegacyEnriched` (the DB keeps its
# incumbent value) and was violated at the API response / enriched workbook.
# These fixtures pin the OUTPUT layer only.
#
# The input value is carried VERBATIM: no normaliser exists for it, and the two
# sources legitimately differ in shape — `_extract_po_box` returns the whole
# match ("PO Box 2000") while the dedicated column carries a bare id
# ("750162"). Normalising the column's shape is tracked separately.
# ---------------------------------------------------------------------------


async def _po_box(po_box=None, street_2=None, street="100 Main St"):
    return await process_address(
        record_id="pb", name1="Acme Corp", name2=None, name3=None,
        street=street, street_2=street_2, street_3=None,
        city="Tampa", state="FL", zip_code="33620", country="US",
        po_box=po_box, care_of_enriched=None, llm_client=None,
    )


class TestPoBoxCarriedToOutput:
    @pytest.mark.asyncio
    async def test_dedicated_column_reaches_the_output(self):
        """(a) The case that shipped blank on all 62 rows."""
        res = await _po_box(po_box="750162")
        assert res.po_box_extracted == "750162"
        assert "G3-ADDR-005" not in res.address_issues

    @pytest.mark.asyncio
    async def test_street_extraction_is_unchanged(self):
        """(b) Street extraction keeps its existing value AND shape — it
        returns the whole match, marker included."""
        res = await _po_box(street_2="PO Box 2000")
        assert res.po_box_extracted == "PO Box 2000"
        assert "G3-ADDR-014" in res.address_issues
        assert "G3-ADDR-005" not in res.address_issues

    @pytest.mark.asyncio
    async def test_both_present_and_equal_keeps_the_input_and_flags(self):
        """(c) Presence semantics are unchanged: a street PO Box alongside a
        populated column raises G3-ADDR-005 whether or not the two agree. The
        output is the input value."""
        res = await _po_box(po_box="2000", street_2="PO Box 2000")
        assert res.po_box_extracted == "2000"
        assert "G3-ADDR-005" in res.address_issues

    @pytest.mark.asyncio
    async def test_both_present_and_different_keeps_the_input(self):
        """(d) The input wins; the conflict is reported, not resolved."""
        res = await _po_box(po_box="750162", street_2="PO Box 2000")
        assert res.po_box_extracted == "750162"
        assert "G3-ADDR-005" in res.address_issues

    @pytest.mark.asyncio
    async def test_input_is_carried_verbatim(self):
        """(e) There is no normaliser for the dedicated column — nothing
        strips a marker or reshapes the value, so it arrives as written."""
        res = await _po_box(po_box="P.O. Box 750162")
        assert res.po_box_extracted == "P.O. Box 750162"

    @pytest.mark.asyncio
    async def test_non_po_box_content_is_carried_verbatim_too(self):
        """Four of the 62 carry a mail stop / mail code in the PO Box column
        ("M/S 643", "CODE 71740", "MC 151 NC", "V38"). Preserving the input is
        the rule; classifying misfiled content is a separate concern."""
        res = await _po_box(po_box="M/S 643")
        assert res.po_box_extracted == "M/S 643"

    @pytest.mark.asyncio
    async def test_blank_column_and_clean_street_stays_blank(self):
        """No PO Box anywhere invents one."""
        res = await _po_box()
        assert res.po_box_extracted is None


# ---------------------------------------------------------------------------
# The residual classifier is a READER, not an authority.
#
# `_extract_mail_code` is the deterministic owner of the Mail Code field. When
# it declines a value, the residual LLM used to be able to label it MAIL_CODE
# and PERFORM the placement — setting `mail_code` and blanking the street slot.
# That is the model inventing a field assignment after the deterministic rule
# already said no: "Dow 268" (a building and a room) was taken into Mail Code
# at confidence 0.95 on row 13185613.
#
# The label may FLAG. It may not MOVE. The MAIL_CODE branch is detect-only:
# the value stays in the slot it came from and Mail Code stays blank unless a
# deterministic extractor filled it.
# ---------------------------------------------------------------------------


class _ScriptedResidualLLM:
    """Minimal stand-in for OpenAIClient: every residual call returns the
    same classification."""

    def __init__(self, classification, confidence=0.95):
        self._cls = classification
        self._conf = confidence
        self.calls = 0

    async def extract_json(self, system, user, max_tokens=None):
        self.calls += 1
        return {"classification": self._cls, "confidence": self._conf}


class TestResidualMailCodeIsDetectOnly:
    @pytest.mark.asyncio
    async def test_classifier_mail_code_does_not_move_the_value(self):
        """Row 13185613. "Dow 268" stays in its street slot and Mail Code
        stays blank, however confident the label is."""
        llm = _ScriptedResidualLLM("MAIL_CODE", 0.95)
        res = await process_address(
            record_id="13185613", name1="Central Michigan University",
            name2="Dept of Chemistry", name3=None,
            street="EAST OTTAWA COURT", street_2="Dow 268", street_3=None,
            city="MOUNT PLEASANT", state="MI", zip_code="48859", country="US",
            po_box=None, care_of_enriched=None, llm_client=llm,
        )
        assert llm.calls == 1                     # the reader still runs
        assert res.mail_code is None              # it just does not place
        assert res.street_2_cleaned == "Dow 268"

    @pytest.mark.asyncio
    async def test_a_labelled_value_with_no_deterministic_shape_stays_put(self):
        """A residual the regex declines and the classifier calls MAIL_CODE,
        carrying no building/room shape either, simply stays in its slot."""
        llm = _ScriptedResidualLLM("MAIL_CODE", 0.99)
        res = await process_address(
            record_id="x", name1="Some University", name2=None, name3=None,
            street="100 Main St", street_2="FCDD-GVS-ES", street_3=None,
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=llm,
        )
        assert res.mail_code is None
        assert res.street_2_cleaned == "FCDD-GVS-ES"

    @pytest.mark.asyncio
    async def test_deterministic_mail_stop_is_untouched(self):
        """"MS 9161" is claimed by the deterministic mail-stop rule and never
        reaches the classifier."""
        llm = _ScriptedResidualLLM("MAIL_CODE", 0.99)
        res = await process_address(
            record_id="x", name1="Some University", name2=None, name3=None,
            street="100 Main St", street_2="MS 9161", street_3=None,
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=llm,
        )
        assert res.mail_stop == "9161"
        assert llm.calls == 0

    @pytest.mark.asyncio
    async def test_deterministic_bare_mail_code_is_untouched(self):
        """The bare form carries no space ("MC1940"); `_extract_mail_code`
        claims it in the secondary slots and the classifier never sees it."""
        llm = _ScriptedResidualLLM("MAIL_CODE", 0.99)
        res = await process_address(
            record_id="x", name1="Some University", name2=None, name3=None,
            street="100 Main St", street_2=None, street_3="MC1940",
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=llm,
        )
        assert res.mail_code == "MC1940"
        assert llm.calls == 0

    @pytest.mark.asyncio
    async def test_explicit_marker_mail_code_is_untouched(self):
        llm = _ScriptedResidualLLM("MAIL_CODE", 0.99)
        res = await process_address(
            record_id="x", name1="Some University", name2=None, name3=None,
            street="100 Main St", street_2=None, street_3="MAIL CODE: SVC1039",
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=llm,
        )
        assert res.mail_code == "SVC1039"
        assert llm.calls == 0

    @pytest.mark.asyncio
    async def test_department_branch_is_unchanged(self):
        """Only the MAIL_CODE branch becomes detect-only. DEPARTMENT still
        routes, so this change cannot be mistaken for a blanket ban on the
        classifier acting."""
        llm = _ScriptedResidualLLM("DEPARTMENT", 0.95)
        res = await process_address(
            record_id="x", name1="Some University", name2=None, name3=None,
            street="100 Main St", street_2="Chemistry Annex", street_3=None,
            city="Tampa", state="FL", zip_code="33620", country="US",
            po_box=None, care_of_enriched=None, llm_client=llm,
        )
        assert res.department_addendum == "Chemistry Annex"
        assert res.street_2_cleaned is None
        assert "G1-ADDR-011" in res.address_issues
