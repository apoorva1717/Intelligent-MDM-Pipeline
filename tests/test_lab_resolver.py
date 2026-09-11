"""Tests for rule A-15 / UC 13: lab → parent department.

Covers the granularity-detection keywords in is_granular_unit, the narrower
lab vocabulary in is_lab_unit that gates UC 13, and the orchestrator path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from enrichment.orchestrator import Orchestrator
from utils.text_utils import is_granular_unit, is_lab_unit


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


class TestIsLabUnit:
    """The lab vocabulary that gates UC 13 — labs only."""

    @pytest.mark.parametrize("name", [
        "Smith Lab",
        "Smith Lab.",
        "NMR Labs",
        "Candelario-Jalil Laboratory",
        "Bronson Diagnostic Laboratories",
        "Laboratory of Genetics",
        "Lab of Genetics",
        # SAP's 35-character field cut the word short.
        "FERMI NATIONAL ACCELERATOR LABORATO",
    ])
    def test_detects_labs(self, name):
        assert is_lab_unit(name) is True, f"expected lab: {name!r}"

    @pytest.mark.parametrize("name", [
        # Granular, but not labs — UC 13 no longer looks these up.
        "Anagnostopoulos Research Group",
        "AMPAC Centre",
        "Center for Genomics",
        "Proteomics Core",
        "Animal Research Facility",
        "Trauma Research Unit",
        "Alpha-1 Research Program",
        # Lab words that do not name a lab.
        "Department of Pathology, Immunology and Laboratory Medicine",
        "Laboratory Medicine",
        "Dept of Labor & Industries",
        "LabCorp",
        "Lab",
        "Lab 204",
    ])
    def test_rejects_non_labs(self, name):
        assert is_lab_unit(name) is False, f"expected non-lab: {name!r}"

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_blank_inputs(self, name):
        assert is_lab_unit(name) is False


class TestLabResolverOrchestrator:
    """End-to-end orchestrator path for rule A-15."""

    @pytest.fixture
    def orchestrator(self, test_settings, mock_clients):
        return Orchestrator(test_settings, mock_clients=mock_clients)

    @pytest.fixture
    def options(self):
        return EnrichmentOptions(max_concurrency=1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("name2", "demoted"), [
        # Preprocessing has already expanded "Lab" by the time it is demoted.
        ("Smith Lab", "Smith Laboratory"),
        ("Smith Labs", "Smith Labs"),
        ("Smith Laboratories", "Smith Laboratories"),
    ])
    async def test_lab_keyword_triggers_lookup(
        self, orchestrator, options, name2, demoted,
    ):
        """A lab Name 2 at a research institution triggers the lab resolver
        and gets promoted to the parent department, with the original lab
        name moved to Name 3."""
        record = EnrichmentRecord(
            record_id="A15_LAB",
            name1="Stanford University",
            name2=name2,
            name3=None,
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.record_type == "research_institution"
        assert result.name2_enriched == "Department of Chemistry"
        assert result.name3_enriched == demoted
        # The parent department was inferred from the lab's page, not read
        # from a stated department — and the doubt is about the department
        # slots, not the institution.
        assert result.flag_codes == ["dept-via-lab"]
        assert result.flagged_fields == ["name2", "name3"]
        assert 13 in result.use_cases_triggered

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name2", [
        "Smith Research Program", "Bhatt Research Group", "Proteomics Core",
    ])
    async def test_non_lab_granular_unit_skips_lookup(
        self, orchestrator, options, name2,
    ):
        """Groups, centres, cores, facilities and programmes are granular but
        are not labs — UC 13 does not look up a parent for them."""
        record = EnrichmentRecord(
            record_id="A15_NOT_A_LAB",
            name1="Stanford University",
            name2=name2,
            name3=None,
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert 13 not in result.use_cases_triggered
        assert "dept-via-lab" not in result.flag_codes
        assert result.name3_enriched is None

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
    async def test_name3_occupied_demotes_to_the_next_free_slot(
        self, orchestrator, options,
    ):
        """When Name 3 already has a value, the lab name must NOT overwrite
        it — it moves down to the next free slot instead."""
        record = EnrichmentRecord(
            record_id="A15_NAME3_OCCUPIED",
            name1="Stanford University",
            name2="Bhatt Lab",
            name3="Existing Value Here",
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.name3_enriched == "Existing Value Here"
        assert result.name4_enriched == "Bhatt Laboratory"
        # Demoted, so the parent/child split is intact — the only flag is
        # the dept-via-lab inference, scoped to where the lab actually landed.
        assert result.flag_for_review is True
        assert result.flag_reason and "Name 4" in result.flag_reason
        assert "name3-not-demoted" not in result.flag_codes

    @pytest.mark.asyncio
    async def test_all_dept_slots_occupied_flags_instead_of_overwrite(
        self, orchestrator, options,
    ):
        """With every slot below Name 2 full there is nowhere to demote the
        lab name to — flag the record rather than drop it silently."""
        record = EnrichmentRecord(
            record_id="A15_ALL_SLOTS_OCCUPIED",
            name1="Stanford University",
            name2="Bhatt Lab",
            name3="Existing Value Here",
            name4="Another Value",
            name5="Third Value",
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        assert result.name3_enriched == "Existing Value Here"
        assert result.name4_enriched == "Another Value"
        assert result.name5_enriched == "Third Value"
        assert result.flag_for_review is True
        assert "name3-not-demoted" in result.flag_codes

    @pytest.mark.asyncio
    async def test_parent_that_is_name1_again_is_rejected(
        self, orchestrator, options,
    ):
        """A "parent department" that just repeats the institution is not a
        department, and must not be written or flagged.

        Record 13348245: the lab page for "Gene Expression Laboratory" gave
        back "Salk Institute for Biological Studies" — Name 1. Finalise
        dropped it (`dept-slot-echoes-name1:dropped`) and the block authority
        pulled the lab name back up into Name 2, so the record shipped
        exactly as it arrived — carrying `dept-via-lab` pointing a reviewer
        at a lab name and an empty Name 3. Rejected at the source instead.
        """
        orchestrator._llm_client._mock_lab_parent = (
            lambda user_prompt, prompt_lower: {
                "parent_department": "Stanford University",
                "confidence": "high",
                "reasoning": "Mock: parent is the institution itself",
            }
        )
        record = EnrichmentRecord(
            record_id="A15_PARENT_ECHOES_NAME1",
            name1="Stanford University",
            name2="Smith Lab",
            name3=None,
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], options)
        result = response.results[0]

        # Nothing was demoted, because nothing was promoted over it.
        assert result.name3_enriched is None
        assert result.name2_enriched != "Stanford University"
        assert "dept-via-lab" not in result.flag_codes
        # Still a granular Name 2 the resolver was asked about — the record
        # falls through to the later tiers, which is what UC 13 records.
        assert 13 in result.use_cases_triggered
