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

    def test_initialism_matches_expansion(self) -> None:
        """An org referenced by its initials matches its expanded canonical name.

        Production case: "JAH VA Hospital" (Name 1) is the James A. Haley
        Veterans' Hospital. fuzz scores the pair ~0.58 — too low — because it
        cannot bridge the acronym to its expansion. The initialism fallback
        recovers it.
        """
        from enrichment.tier1_ror import _score_org

        jah_org = {
            "names": [
                {"value": "James A. Haley Veterans' Hospital",
                 "types": ["ror_display", "label"]},
            ],
        }
        assert _score_org("JAH VA Hospital", jah_org) >= 0.8

    def test_initialism_requires_matching_org_type(self) -> None:
        """An initialism only matches when the org-type word agrees."""
        from enrichment.tier1_ror import _score_org

        bank_org = {
            "names": [
                {"value": "James A. Haley Veterans Bank",
                 "types": ["ror_display", "label"]},
            ],
        }
        # "JAH Hospital" must not resolve to a bank.
        assert _score_org("JAH Hospital", bank_org) < 0.8

    def test_initialism_does_not_match_unrelated_acronym(self) -> None:
        """A non-matching acronym is not rescued by the initialism path."""
        from enrichment.tier1_ror import _initialism_score, _normalise_for_tokens

        cv = [_normalise_for_tokens("James A. Haley Veterans' Hospital")]
        assert _initialism_score("XYZ Hospital", cv) == 0.0
        assert _initialism_score("JAH VA Hospital", cv) == 1.0

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


class TestLegalSuffixCanonicalisation:
    """US legal-entity suffix variants of one org must score identically.

    Production bug: two SAP rows at the same address with names that
    differ only by legal form (e.g. "Carl Zeiss AG" vs "Carl Zeiss
    Aktiengesellschaft") diverged — one matched ROR and enriched, the
    other missed and was flagged unresolved. The same class of bug
    affects US suffixes: long-form / abbreviated / dotted variants
    ("Corporation" vs "Corp." vs "Corp") must all resolve to the same
    ROR org. _normalise_for_tokens now strips '.'/',' and canonicalises
    these suffixes before scoring.
    """

    def _org(self, canonical: str) -> dict:
        return {"names": [{"value": canonical, "types": ["ror_display", "label"]}]}

    @pytest.mark.parametrize(
        "query, canonical",
        [
            ("Acme Corp.", "Acme Corporation"),
            ("Acme Corp", "Acme Corporation"),
            ("Acme, Inc.", "Acme Incorporated"),
            ("Acme Inc", "Acme Incorporated"),
            ("Globex LLC", "Globex L.L.C."),
            ("Globex Limited Liability Company", "Globex LLC"),
            ("Initech Co.", "Initech Company"),
            ("Initech Ltd.", "Initech Limited"),
        ],
    )
    def test_suffix_variants_score_perfect(self, query: str, canonical: str) -> None:
        from enrichment.tier1_ror import _score_org

        score = _score_org(query, self._org(canonical))
        assert score == 1.0, f"{query!r} vs {canonical!r} should match, got {score}"

    def test_divergent_pair_now_agrees(self) -> None:
        """Both legal-form variants of the same org resolve equally."""
        from enrichment.tier1_ror import _score_org

        org = self._org("Acme Corporation")
        assert _score_org("Acme Corp.", org) == _score_org("Acme Corporation", org) == 1.0

    def test_normalisation_collapses_suffix(self) -> None:
        from enrichment.tier1_ror import _normalise_for_tokens

        assert _normalise_for_tokens("Acme, Inc.") == "acme inc"
        assert _normalise_for_tokens("Acme Incorporated") == "acme inc"
        assert _normalise_for_tokens("Globex L.L.C.") == "globex llc"
        assert _normalise_for_tokens("Globex Limited Liability Company") == "globex llc"

    def test_distinct_orgs_still_do_not_match(self) -> None:
        """Suffix canonicalisation must not cause unrelated orgs to match."""
        from enrichment.tier1_ror import _score_org

        assert _score_org("Acme Corp.", self._org("Globex Corporation")) < 0.8
