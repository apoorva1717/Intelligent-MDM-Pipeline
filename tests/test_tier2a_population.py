"""Tests for Tier 2A Mode A — contact lookup to populate missing name2."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings
from enrichment.tier2a_contact import run_tier2a
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


class TestTier2APopulation:
    """Test Mode A — name2 is null, contact present, find department."""

    @pytest.mark.asyncio
    async def test_population_success(self, settings, cache):
        """Contact found on page, department extracted → name2 populated."""
        result = await run_tier2a(
            record_id="TEST_001",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2=None,
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.mode == "2A_population"
        assert result.name2_enriched is not None
        assert "Department" in result.name2_enriched or "department" in result.name2_enriched.lower()
        assert result.source == "contact_lookup_found"
        assert result.confidence in ("high", "medium")

    @pytest.mark.asyncio
    async def test_population_also_extracts_group(self, settings, cache):
        """Mode A should also extract name3 (group) as a bonus when available."""
        result = await run_tier2a(
            record_id="TEST_002",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2=None,
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        # Group extraction is a bonus — may or may not be present
        if result.name3_enriched:
            assert len(result.name3_enriched) > 0

    @pytest.mark.asyncio
    async def test_population_no_search_results(self, settings, cache):
        """No SERP results → Tier 2A fails gracefully."""
        result = await run_tier2a(
            record_id="TEST_003",
            contact="Dr. Nobody Exists",
            institution="ZZZ_No_Match University",
            domain="zzz_no_match.edu",
            name2=None,
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is False
        assert result.name2_enriched is None

    @pytest.mark.asyncio
    async def test_population_page_fetch_failure(self, settings, cache):
        """Page fetch times out → try next candidate or fail."""
        result = await run_tier2a(
            record_id="TEST_004",
            contact="Dr. Timeout",
            institution="Error University",
            domain="timeout.edu",
            name2=None,
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is False
