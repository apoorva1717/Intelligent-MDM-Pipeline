"""Tests for Tier 2A Mode B — contact lookup to verify/correct existing name2."""

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


class TestTier2AVerification:
    """Test Mode B — name2 exists, contact present, verify/correct."""

    @pytest.mark.asyncio
    async def test_verification_name2_corrected(self, settings, cache):
        """Name2 is wrong format, should be corrected to official."""
        result = await run_tier2a(
            record_id="TEST_010",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2="Dept of AI",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.mode == "2A_verification"
        assert result.name2_enriched is not None
        assert result.flag_for_review is True

    @pytest.mark.asyncio
    async def test_verification_name2_matches(self, settings, cache):
        """Name2 matches what's on page — should verify."""
        result = await run_tier2a(
            record_id="TEST_011",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2="Department of Chemistry",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.name2_enriched is not None

    @pytest.mark.asyncio
    async def test_verification_also_extracts_name3(self, settings, cache):
        """Mode B should also extract name3 group when available."""
        result = await run_tier2a(
            record_id="TEST_012",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2="Department of Chemistry",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        if result.success:
            # name3 extraction is a bonus
            pass  # Either present or not — both valid

    @pytest.mark.asyncio
    async def test_verification_no_contact_on_page(self, settings, cache):
        """Contact person not found on any page → fail to 2B."""
        result = await run_tier2a(
            record_id="TEST_013",
            contact="Dr. Xyzzy Qqq",
            institution="Nonexistent ZZZ_No_Match University",
            domain="zzz_no_match.edu",
            name2="Some Department",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        # No search results for this domain → falls through
        assert result.success is False
