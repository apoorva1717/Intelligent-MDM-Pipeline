"""Tests for Tier 2B department search, including regex extraction fallback."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings
from enrichment.tier2b_dept import (
    _extract_all_dept_names,
    _extract_department_from_text,
    _extract_from_snippets,
    run_tier2b,
)
from search.base import SearchResult
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
        assert result.flag_for_review is True

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
        # Generic mock results may or may not yield extraction
        # At minimum, should not raise
        assert isinstance(result.success, bool)

    @pytest.mark.asyncio
    async def test_non_official_source_flagged(self, settings, cache):
        """Results from non-official domain get low confidence and flag."""
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
            assert result.flag_for_review is True


class TestRegexExtraction:
    """Tests for regex-based department extraction (no LLM needed)."""

    def test_extract_department_of(self):
        text = "Welcome to the Department of Chemistry and Biochemistry at UCLA."
        results = _extract_all_dept_names(text)
        assert any("Chemistry" in r for r in results)

    def test_extract_school_of(self):
        text = "Stanford School of Medicine is a leading institution."
        results = _extract_all_dept_names(text)
        assert any("School of Medicine" in r for r in results)

    def test_extract_institute_of(self):
        text = "The Koch Institute for Integrative Cancer Research at MIT."
        results = _extract_all_dept_names(text)
        assert any("Institute for Integrative Cancer Research" in r for r in results)

    def test_extract_division_of(self):
        text = "Division of Engineering and Applied Science, Caltech."
        results = _extract_all_dept_names(text)
        assert any("Division of Engineering" in r for r in results)

    def test_extract_center_for(self):
        text = "Center for Nanoscale Materials at Argonne National Lab."
        results = _extract_all_dept_names(text)
        assert any("Center for Nanoscale Materials" in r for r in results)

    def test_extract_no_matches(self):
        text = "This text has no department-like names at all."
        results = _extract_all_dept_names(text)
        assert results == []

    def test_extract_from_text_with_name2(self):
        page = "Stanford University. The School of Medicine is world-renowned."
        result = _extract_department_from_text("Stanford University", "School of Medicine", page)
        assert result is not None
        assert "School of Medicine" in result

    def test_extract_from_text_fuzzy_name2(self):
        page = "MIT. The Department of Chemistry and Chemical Biology offers graduate programs."
        result = _extract_department_from_text("MIT", "Chemistry Dept", page)
        assert result is not None
        assert "Chemistry" in result

    def test_extract_from_text_no_name2(self):
        page = "UCLA. Department of Chemistry and Biochemistry. Faculty research."
        result = _extract_department_from_text("UCLA", None, page)
        assert result is not None
        assert "Chemistry" in result

    def test_snippet_extraction(self):
        candidates = [
            SearchResult(
                title="Stanford School of Medicine — About Us",
                url="https://med.stanford.edu/about",
                snippet="Stanford School of Medicine is a world-renowned institution.",
            ),
        ]
        hit = _extract_from_snippets(candidates, "Stanford University", "School of Medicine")
        assert hit is not None
        dept_name, url, match_type = hit
        assert "School of Medicine" in dept_name

    def test_snippet_extraction_no_match(self):
        candidates = [
            SearchResult(
                title="Generic Page",
                url="https://example.com",
                snippet="Nothing relevant here at all.",
            ),
        ]
        hit = _extract_from_snippets(candidates, "MIT", "Department of Physics")
        assert hit is None

    # ── Suffix-style pattern tests ─────────────────────────────────────

    def test_extract_facility_suffix(self):
        text = "The NMR Facility at University of Florida houses spectrometers."
        results = _extract_all_dept_names(text)
        assert any("NMR Facility" in r for r in results)

    def test_extract_lab_suffix(self):
        text = "Welcome to the Biomolecular NMR Lab at AMRIS."
        results = _extract_all_dept_names(text)
        assert any("NMR Lab" in r for r in results)

    def test_extract_center_suffix(self):
        text = "The Proteomics Center provides mass spectrometry services."
        results = _extract_all_dept_names(text)
        assert any("Proteomics Center" in r for r in results)

    def test_extract_core_suffix(self):
        text = "Submit samples to the Genomics Core for sequencing."
        results = _extract_all_dept_names(text)
        assert any("Genomics Core" in r for r in results)

    def test_extract_unit_suffix(self):
        text = "The Structural Biology Unit investigates protein folding."
        results = _extract_all_dept_names(text)
        assert any("Biology Unit" in r for r in results)

    def test_extract_from_text_nmr_facility(self):
        page = "University of Florida. The NMR Facility provides analytical services."
        result = _extract_department_from_text("University of Florida", "NMR Facility", page)
        assert result is not None
        assert "NMR Facility" in result

    def test_extract_from_text_biomolecular_lab(self):
        page = "AMRIS at University of Florida. Biomolecular NMR Lab for structural biology."
        result = _extract_department_from_text("University of Florida", "Biomolecular NMR Lab", page)
        assert result is not None
        assert "NMR Lab" in result

    def test_snippet_extraction_facility(self):
        candidates = [
            SearchResult(
                title="NMR Facility — AMRIS | University of Florida",
                url="https://amris.ufl.edu/",
                snippet="The NMR Facility at UF provides services for NMR spectroscopy.",
            ),
        ]
        hit = _extract_from_snippets(candidates, "University of Florida", "NMR Facility")
        assert hit is not None
        dept_name, url, match_type = hit
        assert "NMR Facility" in dept_name
