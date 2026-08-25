"""A person-only Name 1 is moved to Contact. When the affiliation lookup finds
nothing (no search results here), Name 1 is left empty and the record is flagged
for a manual org lookup — with NO Tier 3 fabrication (the pipeline
short-circuits before Tier 3). See test_person_affiliation_guard for the
confirmed / wrong-country paths."""

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


class _NoMatchROR(MockRORClient):
    async def call(self, name, country_code=None, country=None, city=None,
                   state=None, **_ctx):
        return {"matched": False, "score": 0.0}


class _NoResultSearch:
    async def search(self, query, num_results=5):
        return []


class _EmptyLLM:
    async def extract_json(self, system, user, **kw):
        return {}

    async def aclose(self):
        pass


def _orch():
    settings = Settings()
    return Orchestrator(settings, mock_clients={
        "ror": _NoMatchROR(settings),
        "lei": MockLEIClient(settings),
        "search": _NoResultSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": _EmptyLLM(),
    })


class TestPersonInName1FlagOnly:
    @pytest.mark.asyncio
    async def test_person_flagged_not_fabricated(self):
        rec = EnrichmentRecord(
            record_id="P1", name1="Dr. Jane Smith", name2=None,
            city="Boston", country="US",
        )
        resp = await _orch().enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
        r = resp.results[0]
        assert r.contact_enriched == "Dr. Jane Smith"
        assert r.name1_enriched is None       # not fabricated by Tier 3
        assert r.name2_enriched is None
        assert r.flag_codes == ["person-unresolved"]
        assert r.flagged_fields == ["name1"]
        assert "identify the organisation manually" in (r.flag_reason or "")
