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
    async def test_named_building_descriptor_kept(self):
        # "Bldg" here describes a named building — must NOT be dropped.
        assert await _street2("Research I Bldg") == "Research I Bldg"

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
