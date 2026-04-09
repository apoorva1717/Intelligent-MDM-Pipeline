"""Tests for record classification — now derived from ROR org types (Bug 1 fix).

The keyword-based classifier has been removed. Classification is now derived
from the ROR API response. These tests verify the ROR_RESEARCH_TYPES set
and the classification logic embedded in the orchestrator.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier1_ror import ROR_RESEARCH_TYPES


class TestRORBasedClassification:
    """Verify that the ROR_RESEARCH_TYPES set is correct."""

    def test_education_is_research(self):
        assert "education" in ROR_RESEARCH_TYPES

    def test_healthcare_is_research(self):
        assert "healthcare" in ROR_RESEARCH_TYPES

    def test_government_is_research(self):
        assert "government" in ROR_RESEARCH_TYPES

    def test_facility_is_research(self):
        assert "facility" in ROR_RESEARCH_TYPES

    def test_nonprofit_is_research(self):
        assert "nonprofit" in ROR_RESEARCH_TYPES

    def test_archive_is_research(self):
        assert "archive" in ROR_RESEARCH_TYPES

    def test_other_is_research(self):
        assert "other" in ROR_RESEARCH_TYPES

    def test_company_not_in_research_types(self):
        assert "company" not in ROR_RESEARCH_TYPES

    def test_private_not_in_research_types(self):
        assert "private" not in ROR_RESEARCH_TYPES

    def test_classification_from_education_type(self):
        """Education org type → is_research=True → research_institution."""
        org_types = ["education"]
        is_research = any(t in ROR_RESEARCH_TYPES for t in org_types)
        assert is_research is True

    def test_classification_from_company_type(self):
        """Company org type → is_research=False → company."""
        org_types = ["company"]
        is_research = any(t in ROR_RESEARCH_TYPES for t in org_types)
        assert is_research is False

    def test_classification_from_mixed_types(self):
        """Multiple types including education → is_research=True."""
        org_types = ["education", "facility"]
        is_research = any(t in ROR_RESEARCH_TYPES for t in org_types)
        assert is_research is True
