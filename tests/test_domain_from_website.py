"""The institution ``domain`` is derived from a resolved ``website_url`` when
no tier (ROR) supplied one.

Regression: records resolved via the website resolver (SERP / LLM Path B/C)
carry a ``website_url`` but no ``domain``. The department-domain probe gates
on ``domain``, so without this fallback the probe bailed for every such row
and ``department_domain`` was always empty.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from config import Settings
from enrichment.orchestrator import Orchestrator, _init_result
from api.models import EnrichmentRecord
from utils.cache import BatchCache


def _orch():
    from tests.mocks.openai_mock import MockOpenAIClient
    from tests.mocks.page_mock import MockPageFetcher
    from tests.mocks.ror_mock import MockRORClient
    from tests.mocks.serp_mock import MockSearchClient

    s = Settings()
    return Orchestrator(s, mock_clients={
        "ror": MockRORClient(s),
        "search": MockSearchClient(),
        "page_fetcher": MockPageFetcher(),
        "llm": MockOpenAIClient(),
    })


def _seed(**overrides):
    rec = EnrichmentRecord(
        record_id="X", name1="University of Florida",
        city="Gainesville", state="FL", zip="32611", country="US",
    )
    result = _init_result(rec)
    result.update({
        "name1_enriched": "University of Florida",
        "record_type": "research_institution",
        "website_url": "https://www.ufl.edu",
        "domain": None,
    })
    result.update(overrides)
    return rec, result


class TestDomainFromWebsite:
    @pytest.mark.asyncio
    async def test_domain_derived_from_website_url(self):
        orch = _orch()
        rec, result = _seed(name2_enriched="Department of Chemistry")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.domain == "ufl.edu"

    @pytest.mark.asyncio
    async def test_probe_runs_once_domain_is_present(self):
        # With the domain now populated, the probe is no longer gated out and
        # resolves the department host (chem.ufl.edu in the mock page graph).
        orch = _orch()
        rec, result = _seed(name2_enriched="Department of Chemistry")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.department_domain is not None
        assert "ufl.edu" in out.department_domain

    @pytest.mark.asyncio
    async def test_existing_domain_not_overwritten(self):
        orch = _orch()
        rec, result = _seed(domain="example.edu",
                            website_url="https://www.ufl.edu")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.domain == "example.edu"

    @pytest.mark.asyncio
    async def test_no_website_leaves_domain_none(self):
        orch = _orch()
        rec, result = _seed(website_url=None, domain=None,
                            name2_enriched="Department of Chemistry")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.domain is None


class TestProbeSkipsAddressLikeName2:
    """The probe must not spend a SERP call (or resolve a wrong host) when
    name2 is actually a street address or location fragment that the
    name→street cleanup will move out."""

    @pytest.mark.asyncio
    async def test_street_address_name2_skips_probe(self):
        orch = _orch()
        rec, result = _seed(name2_enriched="104 Rhines Hall",
                            street1_enriched="549 Gale Lemerand Dr")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.department_domain is None
        assert out.name2_enriched is None  # moved to a street field

    @pytest.mark.asyncio
    async def test_location_fragment_name2_skips_probe(self):
        orch = _orch()
        rec, result = _seed(name2_enriched="Annex D Pod 2")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.department_domain is None

    @pytest.mark.asyncio
    async def test_real_department_still_resolves(self):
        orch = _orch()
        rec, result = _seed(name2_enriched="Department of Chemistry")
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.department_domain is not None
