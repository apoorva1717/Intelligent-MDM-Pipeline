"""Tests for Tier 3 LLM inference."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier3_llm import run_tier3
from tests.mocks.openai_mock import MockOpenAIClient


class TestTier3:
    """Test LLM inference — last resort, always flagged."""

    @pytest.mark.asyncio
    async def test_tier3_with_sufficient_info(self):
        """Name1 present → LLM provides medium confidence suggestion."""
        result = await run_tier3(
            record_id="TEST_030",
            name1="Some Unknown Lab",
            name2=None,
            name3=None,
            contact=None,
            street="123 Research Dr",
            city="Boston",
            state="MA",
            zip_code="02101",
            country="US",
            llm_client=MockOpenAIClient(),
        )
        assert result.enrichment_status == "unresolved"
        assert result.source == "LLM"
        if result.success:
            assert result.confidence in ("high", "medium")

    @pytest.mark.asyncio
    async def test_tier3_minimal_info(self):
        """Very little info → LLM returns low confidence."""
        result = await run_tier3(
            record_id="TEST_031",
            name1=None,
            name2=None,
            name3=None,
            contact=None,
            street=None,
            city=None,
            state=None,
            zip_code=None,
            country=None,
            llm_client=MockOpenAIClient(),
        )
        assert result.enrichment_status == "unresolved"
        # With no info, mock returns low confidence
        assert result.success is False

    @pytest.mark.asyncio
    async def test_tier3_raises_no_flag_of_its_own(self):
        """Tier 3 reports confidence; it never sets a review flag.

        Flagging is finalisation's job (enrichment/flags.py) — see
        ``test_flags.py`` for the rule that a value Tier 3 wrote becomes
        ``unverified-inference`` regardless of the confidence reported here.
        """
        result = await run_tier3(
            record_id="TEST_032",
            name1="Massachusetts Institute of Technology",
            name2="Chemistry",
            name3=None,
            contact="Dr. Smith",
            street="77 Mass Ave",
            city="Cambridge",
            state="MA",
            zip_code="02139",
            country="US",
            llm_client=MockOpenAIClient(),
        )
        assert not hasattr(result, "flag_for_review")
        assert not hasattr(result, "flag_reason")
        assert result.confidence in ("high", "medium", "low", "none")

    @pytest.mark.asyncio
    async def test_tier3_preserves_originals_on_low_confidence(self):
        """Low confidence → should not overwrite originals."""
        result = await run_tier3(
            record_id="TEST_033",
            name1=None,
            name2=None,
            name3=None,
            contact="Dr. Ghost",
            street=None,
            city=None,
            state=None,
            zip_code=None,
            country=None,
            llm_client=MockOpenAIClient(),
        )
        if not result.success:
            assert result.name1_suggestion is None
            assert result.name2_suggestion is None
            assert result.name3_suggestion is None
