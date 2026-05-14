"""Tests for rule A-15: lab/group/centre/unit/program → parent department.

Covers the granularity-detection keywords in is_granular_unit and the
orchestrator-level path for a 'Research Program' record.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from enrichment.orchestrator import Orchestrator
from utils.text_utils import is_granular_unit


class TestIsGranularUnit:
    """Detection keywords required by rule A-15."""

    @pytest.mark.parametrize("name", [
        "Candelario Lab",
        "Smith Lab",
        "Candelario-Jalil Laboratory",
        "Bronson Diagnostic Laboratories",
        "Anagnostopoulos Research Group",
        "Zhang Group",
        "Advanced Materials Processing and Analysis Center",
        "AMPAC Centre",
        "Trauma Research Unit",
        "Alpha-1 Research Program",
        "Pulmonary Programme",
        "Animal Research Facility",
        "Proteomics Core",
    ])
    def test_detects_granular_units(self, name):
        assert is_granular_unit(name) is True, f"expected granular: {name!r}"

    @pytest.mark.parametrize("name", [
        "Department of Chemistry",
        "Division of Pulmonary Critical Care and Sleep Medicine",
        "School of Medicine",
        "College of Engineering",
        "Faculty of Arts and Sciences",
        # Department-level constructions that incidentally contain a
        # granular keyword in the subject must NOT be flagged.
        "Department of Pathology, Immunology and Laboratory Medicine",
    ])
    def test_excludes_department_level(self, name):
        assert is_granular_unit(name) is False, f"expected non-granular: {name!r}"

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_blank_inputs(self, name):
        assert is_granular_unit(name) is False


class TestLabResolverOrchestrator:
    """End-to-end orchestrator path for rule A-15."""

    @pytest.fixture
    def orchestrator(self, test_settings, mock_clients):
        return Orchestrator(test_settings, mock_clients=mock_clients)

    @pytest.fixture
    def options(self):
        return EnrichmentOptions(max_concurrency=1)

    @pytest.mark.asyncio
    async def test_program_keyword_triggers_lookup(self, orchestrator, options):
        """A 'Research Program' Name 2 at a research institution triggers
        the lab resolver and gets promoted to the parent department,
        with the original program name moved to Name 3."""
        record = EnrichmentRecord(
            record_id="A15_PROGRAM",
            name1="Stanford University",
            name2="Smith Research Program",
            name3=None,
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.record_type == "research_institution"
        assert result.name2_enriched == "Department of Chemistry"
        assert result.name3_enriched == "Smith Research Program"
        assert result.flag_for_review is True
        assert 13 in result.use_cases_triggered

    @pytest.mark.asyncio
    async def test_department_name2_skips_lookup(self, orchestrator, options):
        """Already at department level — must NOT trigger UC 13.
        (Downstream tiers may still touch name2; the only guarantee
        here is that the lab resolver did not run.)"""
        record = EnrichmentRecord(
            record_id="A15_SKIP_DEPT",
            name1="Stanford University",
            name2="Department of Chemistry",
            name3=None,
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.name3_enriched is None
        assert 13 not in result.use_cases_triggered

    @pytest.mark.asyncio
    async def test_company_skips_lookup(self, orchestrator, options):
        """Companies must never trigger UC 13 even if Name 2 is granular."""
        record = EnrichmentRecord(
            record_id="A15_SKIP_COMPANY",
            name1="Pfizer Inc",
            name2="Testing Laboratory",
            name3=None,
            city="New York", state="NY",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.record_type == "company"
        assert 13 not in result.use_cases_triggered

    @pytest.mark.asyncio
    async def test_name3_occupied_flags_instead_of_overwrite(
        self, orchestrator, options,
    ):
        """When Name 3 already has a value, the lab name must NOT be
        moved into Name 3 — flag the record instead."""
        record = EnrichmentRecord(
            record_id="A15_NAME3_OCCUPIED",
            name1="Stanford University",
            name2="Bhatt Research Group",
            name3="Existing Value Here",
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.name3_enriched == "Existing Value Here"
        assert result.flag_for_review is True
        assert result.flag_reason and "Name 3" in result.flag_reason
