"""Item 3/4: the late address stage reduces a mixed street value to one main
street line, routing each segment to its own field per the scope table:

  * c/o line              → Care Of (only the first segment, not a greedy tail)
  * named building        → Building (a SECOND building → next free street slot)
  * campus / science park → next free street slot
  * city / region / postcode already in own fields → dropped
  * the main street line   → Street 1

Tested directly against ``process_address`` with the inline example strings.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.address_processing import process_address


async def _run(street, **kw):
    return await process_address(
        record_id="t",
        name1=kw.pop("name1", "Some Org"),
        name2=None, name3=None,
        street=street, street_2=None, street_3=None,
        city=kw.pop("city", None), state=kw.pop("state", None),
        zip_code=kw.pop("zip_code", None), country=kw.pop("country", None),
        po_box=None, care_of_enriched=None, llm_client=None,
    )


class TestScopeTableReduction:
    @pytest.mark.asyncio
    async def test_row212_pipe_mix(self):
        # 90000337 remainder after preprocessing routes the org to a Name slot.
        res = await _run(
            "C/O RCUK SSC Ltd, Polaris House, North Star House, North Star Avenue",
            city="Swindon", country="GB",
        )
        assert res.care_of_enriched == "RCUK SSC Ltd"      # first segment only
        assert res.building == "Polaris House"             # first building
        assert "Star Ave" in (res.street_cleaned or "")    # main street → Street 1
        # Second building is NOT dropped — it goes to the next street slot.
        assert "Star House" in (res.street_2_cleaned or "")

    @pytest.mark.asyncio
    async def test_named_building_and_dropped_location(self):
        res = await _run(
            "ASTER HOUSE, 2A UNIVERSITY ROAD, BELFAST BT7 1NH",
            city="Belfast", country="GB",
        )
        assert res.building == "Aster House"
        assert "university" in (res.street_cleaned or "").lower()
        # City + UK postcode dropped from the street (already in their fields).
        assert "BELFAST" not in (res.street_cleaned or "").upper()
        assert "BT7" not in (res.street_cleaned or "")
        assert res.street_2_cleaned is None

    @pytest.mark.asyncio
    async def test_building_and_campus_split(self):
        res = await _run(
            "The Sherard Bldg, Edmund Halley Rd, Oxford Science Park",
            city="Oxford", country="GB",
        )
        assert res.building == "The Sherard Bldg"
        assert res.street_cleaned == "Edmund Halley Rd"
        assert res.street_2_cleaned == "Oxford Science Park"   # campus → next slot

    @pytest.mark.asyncio
    async def test_simple_suite_case_unchanged(self):
        # No c/o, building, or pipe → the per-segment reducer must not fire; the
        # existing suite extractor still runs.
        res = await _run("100 Main St, Suite 400", city="Boston", country="US")
        assert res.street_cleaned == "100 Main St"
        assert res.suite == "400"

    @pytest.mark.asyncio
    async def test_bare_ordinal_floor(self):
        res = await _run("51 Sleeper St, 7th", city="Boston", country="US")
        assert res.street_cleaned == "51 Sleeper St"
        assert res.floor == "7"

    @pytest.mark.asyncio
    async def test_trailing_room_code(self):
        res = await _run("1500 Graduate Ln A104", city="X", country="US")
        assert res.street_cleaned == "1500 Graduate Ln"
        assert res.room == "A104"

    @pytest.mark.asyncio
    async def test_campus_box_mail_stop(self):
        res = await _run("2721 Sullivan Dr Campus Box 7212", city="X", country="US")
        assert res.street_cleaned == "2721 Sullivan Dr"
        assert res.mail_stop == "Campus Box 7212"

    @pytest.mark.asyncio
    async def test_tamu_mail_code_and_numbered_building(self):
        res = await _run("3120 TAMU | 5045 Emerging Technologies Building",
                         city="College Station", country="US")
        assert res.mail_code == "3120 TAMU"
        assert res.building == "Emerging Technologies Building"
