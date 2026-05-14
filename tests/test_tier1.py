"""Tests for Tier 1 ROR API lookup — updated for query-based call() interface."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings
from enrichment.tier1_ror import ROR_RESEARCH_TYPES
from tests.mocks.ror_mock import MockRORClient


@pytest.fixture
def ror_client():
    return MockRORClient(Settings())


class TestTier1ROR:
    """Test ROR lookup with mock client — using call(name, country_code) interface."""

    @pytest.mark.asyncio
    async def test_high_confidence_institution(self, ror_client):
        """ROR returns high confidence for known institution."""
        result = await ror_client.call("Massachusetts Institute of Technology", country_code="US")
        assert result["matched"] is True
        assert result["official_name"] == "Massachusetts Institute of Technology"
        assert result["domain"] == "mit.edu"
        assert result["ror_id"] is not None
        assert result["score"] > 0.8

    @pytest.mark.asyncio
    async def test_acronym_resolution(self, ror_client):
        """ROR resolves common acronyms."""
        result = await ror_client.call("UCLA", country_code="US")
        assert result["matched"] is True
        assert result["official_name"] == "University of California, Los Angeles"
        assert result["domain"] == "ucla.edu"

    @pytest.mark.asyncio
    async def test_result_includes_children(self, ror_client):
        """ROR result includes child organisations for local matching."""
        result = await ror_client.call("Massachusetts Institute of Technology")
        assert result["matched"] is True
        assert len(result["children"]) > 0
        child_names = [c["name"] for c in result["children"]]
        assert "Department of Chemistry" in child_names

    @pytest.mark.asyncio
    async def test_no_match_unknown_institution(self, ror_client):
        """ROR returns no match for unknown institutions."""
        result = await ror_client.call("Nonexistent University of Nowhere")
        assert result["matched"] is False

    @pytest.mark.asyncio
    async def test_company_returns_matched(self, ror_client):
        """ROR matches company above threshold."""
        result = await ror_client.call("Pfizer", country_code="US")
        assert result["matched"] is True
        assert result["is_research_institution"] is False

    @pytest.mark.asyncio
    async def test_research_institution_classification(self, ror_client):
        """Education type → is_research_institution=True."""
        result = await ror_client.call("Stanford University", country_code="US")
        assert result["matched"] is True
        assert result["is_research_institution"] is True
        assert any(t in ROR_RESEARCH_TYPES for t in result["org_types"])

    @pytest.mark.asyncio
    async def test_company_classification(self, ror_client):
        """Company type → is_research_institution=False."""
        result = await ror_client.call("Novartis")
        assert result["matched"] is True
        assert result["is_research_institution"] is False

    @pytest.mark.asyncio
    async def test_result_has_domain(self, ror_client):
        """Result includes institution domain for Tier 2A."""
        result = await ror_client.call("MIT", country_code="US")
        assert result["matched"] is True
        assert result["domain"] == "mit.edu"

    @pytest.mark.asyncio
    async def test_country_code_passed_through(self, ror_client):
        """Country code is included in the result for traceability."""
        result = await ror_client.call("UCLA", country_code="US")
        assert result["matched"] is True
        assert result["country_filter"] == "US"

    @pytest.mark.asyncio
    async def test_no_country_code(self, ror_client):
        """Query works without a country code."""
        result = await ror_client.call("Harvard University")
        assert result["matched"] is True
        assert result["official_name"] == "Harvard University"

    @pytest.mark.asyncio
    async def test_abbreviation_resolution(self, ror_client):
        """Common abbreviations like 'Univ of Florida' resolve correctly."""
        result = await ror_client.call("Univ of Florida", country_code="US")
        assert result["matched"] is True
        assert result["official_name"] == "University of Florida"
        assert result["domain"] == "ufl.edu"

    @pytest.mark.asyncio
    async def test_full_name_resolution(self, ror_client):
        """Full institution name resolves correctly."""
        result = await ror_client.call("University of Florida", country_code="US")
        assert result["matched"] is True
        assert result["official_name"] == "University of Florida"
        assert len(result["children"]) > 0


class TestNameScoring:
    """Unit tests for the local name-scoring guard rails."""

    def test_acronym_mismatch_capped(self) -> None:
        """'EMSL Analytical, Inc.' must not match 'ASL Analytical'.

        Production bug: ROR returned ASL Analytical (ror.org/03w5ry680)
        as the chosen affiliation match for EMSL — the shared
        'Analytical' token dominates token_sort_ratio (~0.9), masking
        the one-letter difference in the leading acronym. The
        identifier-token guard caps such matches below threshold.
        """
        from enrichment.tier1_ror import _score_org

        asl_org = {
            "names": [
                {"value": "ASL Analytical", "types": ["ror_display", "label"]},
                {"value": "ASL Analytical, Inc.", "types": ["alias"]},
            ],
        }
        score = _score_org("EMSL Analytical, Inc.", asl_org)
        assert score < 0.8, f"EMSL→ASL should be capped, got {score}"

    def test_matching_acronym_still_scores_high(self) -> None:
        """Legitimate matches with the same acronym still score 1.0."""
        from enrichment.tier1_ror import _score_org

        asl_org = {
            "names": [
                {"value": "ASL Analytical", "types": ["ror_display", "label"]},
                {"value": "ASL Analytical, Inc.", "types": ["alias"]},
            ],
        }
        assert _score_org("ASL Analytical, Inc.", asl_org) == 1.0

    def test_acronym_alias_exact_match_unaffected(self) -> None:
        """Acronym-only queries still match via alias exact match (step 1)."""
        from enrichment.tier1_ror import _score_org

        ucla_org = {
            "names": [
                {"value": "University of California, Los Angeles", "types": ["ror_display", "label"]},
                {"value": "UCLA", "types": ["alias", "acronym"]},
            ],
        }
        assert _score_org("UCLA", ucla_org) == 1.0
