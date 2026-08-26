"""Item 3/4: street values that mix an organisation/department with address
content route the org/dept to a Name slot and clean the street, end-to-end.

  * "Accounts Payable, 399 Revolution Dr" → Name + street
  * "ICB&DD … Laboratory, Lab 576, Chemistry Bldg" (no street) → Name + Room +
    Building, Street 1 empty
  * "Atnn: Suzanne W Friend, Chemistry Dept., | 6100 Main St." → Care Of + Name
    + street (note the misspelled "Atnn:")
  * "Finance/Procurement" → Name, Street 1 empty
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.lei_mock import MockLEIClient


class _NoSearch:
    async def search(self, q, num_results=5, *, country=None):
        return []


class _EmptyLLM:
    async def extract_json(self, s, u, **k):
        return {}

    async def aclose(self):
        pass


def _orch():
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": MockRORClient(st), "lei": MockLEIClient(st),
        "search": _NoSearch(), "page_fetcher": MockPageFetcher(), "llm": _EmptyLLM(),
    })


async def _run(street):
    rec = EnrichmentRecord(record_id="t", name1=None, name2=None,
                           street1=street, city="X", country="US")
    resp = await _orch().enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


class TestStreetToNameRouting:
    @pytest.mark.asyncio
    async def test_accounts_payable_plus_street(self):
        r = await _run("Accounts Payable, 399 Revolution Dr")
        assert r.name1_enriched == "Accounts Payable"
        assert r.street_cleaned == "399 Revolution Dr"

    @pytest.mark.asyncio
    async def test_org_room_building_no_street(self):
        r = await _run("ICB&DD Mass Spectrometry Laboratory, Lab 576, Chemistry Bldg")
        assert r.name1_enriched == "ICB&DD Mass Spectrometry Laboratory"
        assert r.room == "Lab 576"
        assert r.building == "Chemistry Bldg"
        assert r.street_cleaned is None

    @pytest.mark.asyncio
    async def test_misspelled_attn_plus_dept_plus_street(self):
        r = await _run("Atnn: Suzanne W Friend, Chemistry Dept., | 6100 Main St.")
        assert r.care_of_enriched == "Suzanne W Friend"
        names = [r.name1_enriched, r.name2_enriched]
        # Fix 4: output name fields are abbreviation-expanded, so the unit
        # lands as "Chemistry Department" — never the raw "Chemistry Dept.".
        assert any(n and "Chemistry" in n and "Dept" not in n for n in names)
        assert (r.street_cleaned or "").startswith("6100 Main St")

    @pytest.mark.asyncio
    async def test_functional_dept_no_street(self):
        r = await _run("Finance/Procurement")
        names = [r.name1_enriched, r.name2_enriched]
        assert "Finance/Procurement" in names
        assert r.street_cleaned is None
