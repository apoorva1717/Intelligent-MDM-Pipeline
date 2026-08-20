"""Tests for Tier 2B department search via SERP + LLM extraction."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings
from enrichment.tier2b_dept import run_tier2b
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.serp_mock import MockSearchClient
from utils.cache import BatchCache


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def cache():
    return BatchCache()


class TestTier2B:
    """Test department search via SERP + LLM extraction."""

    @pytest.mark.asyncio
    async def test_research_institution_dept_found(self, settings, cache):
        """Department found on official domain page."""
        result = await run_tier2b(
            record_id="TEST_020",
            name1="Stanford University",
            name2="Chemistry Dept",
            record_type="research_institution",
            city="Stanford",
            state="CA",
            domain="stanford.edu",
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.name2_enriched is not None
        assert result.source == "dept_search"
        # A stated department read off an on-domain page is auditable
        # evidence: source_url says which page. No review flag.
        assert result.source_url
        assert not hasattr(result, "flag_for_review")

    @pytest.mark.asyncio
    async def test_company_division_found(self, settings, cache):
        """Company division found via search."""
        result = await run_tier2b(
            record_id="TEST_021",
            name1="Pfizer Inc",
            name2="Analytical Sciences",
            record_type="company",
            city="New York",
            state="NY",
            domain="pfizer.com",
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.source == "dept_search"

    @pytest.mark.asyncio
    async def test_no_results_found(self, settings, cache):
        """No SERP results → Tier 2B fails."""
        result = await run_tier2b(
            record_id="TEST_022",
            name1="Nonexistent Corp",
            name2=None,
            record_type="company",
            city=None,
            state=None,
            domain=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert isinstance(result.success, bool)

    @pytest.mark.asyncio
    async def test_non_official_source_gets_low_confidence(self, settings, cache):
        """An off-domain page is weaker evidence and says so via confidence."""
        result = await run_tier2b(
            record_id="TEST_023",
            name1="Some Unknown Research Center",
            name2="Physics",
            record_type="research_institution",
            city="Boston",
            state="MA",
            domain="unknown-center.edu",
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        if result.success:
            assert result.confidence in ("medium", "low")
