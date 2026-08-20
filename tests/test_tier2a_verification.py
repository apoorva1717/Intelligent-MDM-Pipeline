"""Tests for Tier 2A Mode B — contact lookup to verify/correct existing name2."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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


class _ConfidenceStubOpenAIClient(MockOpenAIClient):
    """MockOpenAIClient with a forced confidence on the Tier 2A extraction.

    Every curated Tier 2A entry in the shared mock returns
    ``confidence: "high"``, so the medium-confidence half of the sub-80
    band is unreachable through it. This stub overrides only the Tier 2A
    affiliation prompt and delegates everything else to the real mock, so
    the surrounding pipeline behaves identically.
    """

    def __init__(self, confidence: str, official_dept: str) -> None:
        self._forced_confidence = confidence
        self._forced_dept = official_dept

    async def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        if "extract affiliation for:" in user_prompt.lower():
            return {
                "person_found": True,
                "official_dept": self._forced_dept,
                "official_group": None,
                "title": "Professor",
                "confidence": self._forced_confidence,
            }
        return await super().extract_json(
            system_prompt, user_prompt,
            temperature=temperature, max_tokens=max_tokens,
        )


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
        assert result.name2_enriched == "Department of Chemistry"
        # "Dept of AI" vs "Department of Chemistry" scores far below the
        # threshold, and the curated mock reports high confidence, so the
        # record is treated as wrong and overwritten.
        assert result.name2_match == "no_match"
        assert result.name2_match_score < settings.fuzzy_match_threshold
        assert result.enrichment_status == "enriched"
        assert result.source == "contact_lookup_corrected"
        # The correction is backed by the contact's page (source_url), so
        # nothing is left unsettled for a reviewer.
        assert result.low_conf_unchanged == set()

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
        assert result.name2_enriched == "Department of Chemistry"
        # Exact band (>= 95): the record already agreed with the page, so
        # it is marked verified and needs no review.
        assert result.name2_match == "exact"
        assert result.name2_match_score >= 95
        assert result.enrichment_status == "verified"
        assert result.source == "contact_lookup_found"
        assert result.low_conf_unchanged == set()

    @pytest.mark.asyncio
    async def test_verification_partial_band(self, settings, cache):
        """80 <= score < 95 — normalise to the page wording.

        "Dept of Radiology" against "Department of Radiology" scores in
        the partial band: same unit, non-canonical wording.
        """
        result = await run_tier2a(
            record_id="TEST_014",
            contact="Dr. Robert Chen",
            institution="Johns Hopkins University",
            domain="jhu.edu",
            name2="Dept of Radiology",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=MockOpenAIClient(),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.mode == "2A_verification"
        assert settings.fuzzy_match_threshold <= result.name2_match_score < 95
        assert result.name2_match == "partial"
        assert result.name2_enriched == "Department of Radiology"
        assert result.enrichment_status == "enriched"
        assert result.source == "contact_lookup_found"
        # Same unit, canonical wording, page-backed — no flag.
        assert result.low_conf_unchanged == set()

    @pytest.mark.asyncio
    async def test_verification_also_extracts_name3(self, settings, cache):
        """Mode B also extracts the name3 group from the contact's page."""
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
        assert result.success is True
        assert result.name3_enriched == "NMR Spectroscopy Group"

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


class TestTier2AVerificationLowScoreSplit:
    """Sub-80 band splits on extraction confidence.

    A score below the threshold means the contact page disagrees
    substantially with the record. Either the record is wrong or the wrong
    page was found — so only a high-confidence extraction is trusted
    enough to overwrite what the customer supplied.
    """

    @pytest.mark.asyncio
    async def test_low_score_high_confidence_overwrites(self, settings, cache):
        """High confidence + low score → correct the record."""
        result = await run_tier2a(
            record_id="TEST_015",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2="Dept of AI",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=_ConfidenceStubOpenAIClient("high", "Department of Chemistry"),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.mode == "2A_verification"
        assert result.name2_match == "no_match"
        assert result.name2_match_score < settings.fuzzy_match_threshold
        # The page value replaces the record's value.
        assert result.name2_enriched == "Department of Chemistry"
        assert result.enrichment_status == "enriched"
        assert result.source == "contact_lookup_corrected"
        assert result.low_conf_unchanged == set()

    @pytest.mark.asyncio
    async def test_low_score_medium_confidence_preserves_original(
        self, settings, cache,
    ):
        """Medium confidence + low score → report, do not correct.

        ``name2_enriched`` is left unset so the orchestrator's non-blank
        merge filter keeps the record's own value.
        """
        result = await run_tier2a(
            record_id="TEST_016",
            contact="Dr. Jane Smith",
            institution="Massachusetts Institute of Technology",
            domain="mit.edu",
            name2="Dept of AI",
            name3=None,
            search_client=MockSearchClient(),
            page_fetcher=MockPageFetcher(),
            llm_client=_ConfidenceStubOpenAIClient("medium", "Department of Chemistry"),
            cache=cache,
            settings=settings,
        )
        assert result.success is True
        assert result.mode == "2A_verification"
        assert result.name2_match == "no_match"
        assert result.name2_match_score < settings.fuzzy_match_threshold
        # The disagreement is reported, not acted on.
        assert result.name2_enriched is None
        assert result.enrichment_status == "unresolved"
        assert result.source == "contact_lookup_found"
        # Attempted, below threshold, input left in place — the one Tier 2A
        # outcome that leaves something for a reviewer, scoped to Name 2.
        assert result.low_conf_unchanged == {"name2"}
