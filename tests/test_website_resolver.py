"""Tests for Path A/B/C website resolution.

Path A — extract_website_from_ror (sync helper, ROR links[]).
Path B — select_website_from_serp + resolve_website_via_serp.
Path C — infer_website_via_llm (uses MockOpenAIClient).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator
from enrichment.tier1_ror import extract_website_from_ror
from enrichment.website_resolver import (
    DOMAIN_BLACKLIST,
    WebsiteResolution,
    infer_website_via_llm,
    resolve_website_via_serp,
    select_website_from_serp,
)
from search.base import SearchResult
from utils.cache import BatchCache


# ---------------------------------------------------------------------------
# Path A — extract_website_from_ror
# ---------------------------------------------------------------------------

class TestExtractWebsiteFromROR:
    def test_returns_first_website_link(self):
        org = {
            "links": [
                {"type": "website", "value": "https://www.stanford.edu"},
                {"type": "wikipedia", "value": "https://en.wikipedia.org/wiki/Stanford_University"},
            ],
        }
        assert extract_website_from_ror(org) == "https://www.stanford.edu"

    def test_skips_non_website_links(self):
        org = {
            "links": [
                {"type": "wikipedia", "value": "https://en.wikipedia.org/wiki/Foo"},
                {"type": "website", "value": "http://www.ufl.edu"},
            ],
        }
        assert extract_website_from_ror(org) == "http://www.ufl.edu"

    def test_no_links_returns_none(self):
        assert extract_website_from_ror({"links": []}) is None
        assert extract_website_from_ror({}) is None

    def test_website_link_without_value_returns_none(self):
        org = {"links": [{"type": "website"}]}
        assert extract_website_from_ror(org) is None


# ---------------------------------------------------------------------------
# Path B — select_website_from_serp
# ---------------------------------------------------------------------------

def _sr(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


class TestSelectWebsiteFromSERP:
    def test_research_official_edu_top_hit_is_high_confidence(self):
        results = [
            _sr(
                "Florida Institute of Technology",
                "https://www.fit.edu/",
                "FIT homepage",
            ),
        ]
        chosen = select_website_from_serp(
            "Florida Institute of Technology", results,
            record_type="research_institution",
        )
        assert chosen.url == "https://www.fit.edu"
        assert chosen.confidence == "high"
        assert chosen.source == "serp"

    def test_blacklisted_hosts_are_skipped(self):
        results = [
            _sr(
                "Florida Institute of Technology - Wikipedia",
                "https://en.wikipedia.org/wiki/Florida_Institute_of_Technology",
            ),
            _sr(
                "Florida Institute of Technology",
                "https://www.fit.edu/",
            ),
        ]
        chosen = select_website_from_serp(
            "Florida Institute of Technology", results,
            record_type="research_institution",
        )
        assert chosen.url == "https://www.fit.edu"
        assert chosen.confidence == "high"

    def test_research_non_official_tld_is_low_confidence(self):
        results = [
            _sr("Acme Research Foundation", "https://www.acmeres.com/about"),
        ]
        chosen = select_website_from_serp(
            "Acme Research Foundation", results,
            record_type="research_institution",
        )
        assert chosen.url == "https://www.acmeres.com"
        assert chosen.confidence == "low"

    def test_company_token_in_host_is_high_confidence(self):
        # Company rule: name token is a substring of the host → high.
        results = [
            _sr("Fisher Scientific", "https://www.fishersci.com/"),
        ]
        chosen = select_website_from_serp(
            "Fisher Scientific Co. LLC", results, record_type="company",
        )
        assert chosen.url == "https://www.fishersci.com"
        assert chosen.confidence == "high"

    def test_company_token_not_in_host_is_low_confidence(self):
        # Company rule: name token NOT in the host → low.
        results = [
            _sr("Pittsburgh PA Listings — Page 4", "https://www.example.com/co/listings"),
        ]
        chosen = select_website_from_serp(
            "Acme Pittsburgh", results, record_type="company",
        )
        assert chosen.confidence == "low"

    def test_no_name_overlap_skipped(self):
        results = [
            _sr("Some Other Site", "https://www.some-other.edu/page"),
        ]
        chosen = select_website_from_serp(
            "Florida Institute of Technology", results,
            record_type="research_institution",
        )
        assert chosen.url is None
        assert chosen.confidence == "none"

    def test_empty_results_returns_none(self):
        chosen = select_website_from_serp("Anything", [])
        assert chosen == WebsiteResolution()

    def test_blacklist_covers_expected_aggregators(self):
        # Sanity check the constant — covers the spec's required hosts.
        for host in {
            "wikipedia.org", "linkedin.com", "facebook.com",
            "ratemyprofessors.com", "bloomberg.com", "indeed.com",
        }:
            assert host in DOMAIN_BLACKLIST


# ---------------------------------------------------------------------------
# Path B — resolve_website_via_serp (async, with mock SERP)
# ---------------------------------------------------------------------------

class _ListSearchClient:
    """Returns a fixed list of SearchResult on every search() call."""

    def __init__(self, results: list[SearchResult]):
        self._results = results
        self.calls: list[str] = []

    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        self.calls.append(query)
        return self._results[:num_results]


class TestResolveWebsiteViaSERP:
    @pytest.mark.asyncio
    async def test_runs_serp_when_no_prefetch(self):
        client = _ListSearchClient([
            _sr("Florida Institute of Technology", "https://www.fit.edu/"),
        ])
        cache = BatchCache()
        res = await resolve_website_via_serp(
            record_id="T1", name1="Florida Institute of Technology",
            city="Melbourne", state="FL", country="US",
            record_type="research_institution",
            search_client=client, cache=cache,
        )
        assert res.url == "https://www.fit.edu"
        assert res.confidence == "high"
        assert len(client.calls) == 1
        assert "Florida Institute of Technology" in client.calls[0]

    @pytest.mark.asyncio
    async def test_company_query_includes_city_state(self):
        client = _ListSearchClient([
            _sr("Acme Co — Pittsburgh", "https://www.acmeco.com/"),
        ])
        cache = BatchCache()
        res = await resolve_website_via_serp(
            record_id="T1B", name1="Acme Co",
            city="Pittsburgh", state="PA", country="US",
            record_type="company",
            search_client=client, cache=cache,
        )
        assert res.url == "https://www.acmeco.com"
        # "acme" is a substring of "acmeco.com" → high confidence
        # under the company rule.
        assert res.confidence == "high"
        assert "Pittsburgh PA" in client.calls[0]

    @pytest.mark.asyncio
    async def test_uses_prefetched_results_when_provided(self):
        client = _ListSearchClient([])  # would return nothing if called
        prefetched = [
            _sr("Florida Institute of Technology", "https://www.fit.edu/"),
        ]
        cache = BatchCache()
        res = await resolve_website_via_serp(
            record_id="T2", name1="Florida Institute of Technology",
            city=None, state=None, country=None,
            record_type="research_institution",
            search_client=client, cache=cache,
            prefetched_results=prefetched,
        )
        assert res.url == "https://www.fit.edu"
        assert client.calls == []  # no SERP call made

    @pytest.mark.asyncio
    async def test_blank_name1_returns_none(self):
        client = _ListSearchClient([])
        res = await resolve_website_via_serp(
            record_id="T3", name1="",
            city=None, state=None, country=None,
            record_type=None,
            search_client=client, cache=BatchCache(),
        )
        assert res.url is None
        assert client.calls == []


# ---------------------------------------------------------------------------
# Path C — infer_website_via_llm (uses mock_llm_client fixture)
# ---------------------------------------------------------------------------

class TestInferWebsiteViaLLM:
    @pytest.mark.asyncio
    async def test_known_company_returns_url_low_confidence(self, mock_llm_client):
        res = await infer_website_via_llm(
            record_id="C1", name1="Fisher Scientific Co. LLC",
            city="Pittsburgh", state="PA", country="US",
            llm_client=mock_llm_client,
        )
        # Mock returns a URL for "fisher scientific"; the resolver
        # always returns confidence="low" because LLM inference is
        # never considered authoritative — the orchestrator flags it.
        assert res.url == "https://www.fishersci.com"
        assert res.confidence == "low"
        assert res.source == "llm"

    @pytest.mark.asyncio
    async def test_unknown_company_returns_none(self, mock_llm_client):
        res = await infer_website_via_llm(
            record_id="C2", name1="BioMed Solutions Inc.",
            city="Tampa", state="FL", country="US",
            llm_client=mock_llm_client,
        )
        assert res.url is None
        assert res.confidence == "none"


# ---------------------------------------------------------------------------
# Orchestrator end-to-end — Path A wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def orchestrator(mock_clients):
    return Orchestrator(Settings(), mock_clients=mock_clients)


@pytest.fixture
def default_options():
    return EnrichmentOptions(max_concurrency=1)


class TestOrchestratorWebsiteFields:
    @pytest.mark.asyncio
    async def test_path_a_stanford_no_flag(self, orchestrator, default_options):
        record = EnrichmentRecord(
            record_id="WEB_A1", name1="Stanford University",
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.website_url == "https://www.stanford.edu"
        # Path A (ROR) is authoritative — no website-driven review flag.
        assert (result.flag_reason or "").lower().find("website") == -1

    @pytest.mark.asyncio
    async def test_path_a_university_of_florida(self, orchestrator, default_options):
        record = EnrichmentRecord(
            record_id="WEB_A2", name1="University of Florida",
            city="Gainesville", state="FL", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.website_url == "https://www.ufl.edu"

    @pytest.mark.asyncio
    async def test_path_b_company_via_serp_no_flag(
        self, orchestrator, default_options,
    ):
        # "Thermo Fisher Scientific Inc." — not in ROR mock; SERP mock
        # has a fixture with thermofisher.com → Path B fires, name token
        # in domain → high confidence → no website-driven review flag.
        record = EnrichmentRecord(
            record_id="WEB_B1", name1="Thermo Fisher Scientific Inc.",
            city="Waltham", state="MA", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.website_url == "https://www.thermofisher.com"
        assert "website" not in (result.flag_reason or "").lower()

    @pytest.mark.asyncio
    async def test_path_c_company_falls_through_to_llm(
        self, orchestrator, default_options,
    ):
        # "Fisher Scientific Co. LLC" — not in ROR mock; the SERP mock
        # has no entry for it (generic results don't share name tokens),
        # so Path B yields nothing and Path C (LLM) fires.
        record = EnrichmentRecord(
            record_id="WEB_C1", name1="Fisher Scientific Co. LLC",
            city="Pittsburgh", state="PA", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.website_url == "https://www.fishersci.com"
        # LLM-sourced URLs are always flagged for verification.
        assert result.flag_for_review is True
        assert "website" in (result.flag_reason or "").lower()

    @pytest.mark.asyncio
    async def test_unknown_company_leaves_website_empty(
        self, orchestrator, default_options,
    ):
        # No SERP mock entry, LLM mock returns null for this name.
        record = EnrichmentRecord(
            record_id="WEB_C2", name1="BioMed Solutions Inc.",
            city="Tampa", state="FL", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.website_url is None

    @pytest.mark.asyncio
    async def test_dry_run_leaves_website_empty(self, orchestrator):
        record = EnrichmentRecord(
            record_id="WEB_DRY", name1="Stanford University",
        )
        opts = EnrichmentOptions(max_concurrency=1, dry_run=True)
        response = await orchestrator.enrich_batch([record], opts)
        result = response.results[0]
        assert result.website_url is None
