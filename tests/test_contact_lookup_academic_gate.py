"""Contact → department lookups run for academic institutions only.

Both contact paths are gated on the same Name 1 check as the UC 13 lab lookup
and G2-NAME-009/-012 (`looks_like_university_or_research_institute`), not on
`routing_type`. ROR types NASA ``government``, so it routes
``research_institution`` — but a contact at NASA has no academic department to
find, and the lookup would only be a web search for one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import enrichment.orchestrator as orch_mod
from api.models import EnrichmentOptions, EnrichmentRecord
from enrichment.orchestrator import Orchestrator
from enrichment.person_affiliation import PersonAffiliation
from enrichment.tier2a_contact import Tier2AResult

NASA = "National Aeronautics and Space Administration"


@pytest.fixture
def tier2a_calls(monkeypatch):
    calls: list[dict] = []

    async def _spy(*args, **kwargs):
        calls.append(kwargs)
        return Tier2AResult()

    monkeypatch.setattr(orch_mod, "run_tier2a", _spy)
    return calls


async def _enrich(test_settings, mock_clients, record):
    orchestrator = Orchestrator(test_settings, mock_clients=mock_clients)
    response = await orchestrator.enrich_batch(
        [record], EnrichmentOptions(max_concurrency=1),
    )
    return response.results[0]


class TestTier2AContactLookup:
    @pytest.mark.asyncio
    async def test_non_academic_research_institution_skips_lookup(
        self, test_settings, mock_clients, tier2a_calls,
    ):
        result = await _enrich(test_settings, mock_clients, EnrichmentRecord(
            record_id="T2A_AGENCY", name1=NASA, name2=None,
            contact="Jane Smith", city="Washington", state="DC", country="US",
        ))
        assert result.record_type == "research_institution"
        assert tier2a_calls == []

    @pytest.mark.asyncio
    async def test_university_runs_lookup(
        self, test_settings, mock_clients, tier2a_calls,
    ):
        await _enrich(test_settings, mock_clients, EnrichmentRecord(
            record_id="T2A_UNI", name1="Stanford University", name2=None,
            contact="Jane Smith", city="Stanford", state="CA", country="US",
        ))
        assert len(tier2a_calls) == 1


class TestPersonAffiliationDepartment:
    """Name 1 is a person, moved to Contact; the affiliation lookup proposes an
    institution and a department, and ROR confirms the institution."""

    @pytest.fixture
    def affiliation(self, monkeypatch):
        def _set(institution):
            async def _fake(**_kw):
                return PersonAffiliation(
                    institution=institution,
                    department="Department of Chemistry",
                    confidence="high",
                )
            monkeypatch.setattr(orch_mod, "run_person_affiliation", _fake)
        return _set

    @pytest.mark.asyncio
    async def test_non_academic_institution_gets_no_department(
        self, test_settings, mock_clients, tier2a_calls, affiliation,
    ):
        """The institution is still written — only the department is not
        looked for, and the proposed one is not written."""
        affiliation(NASA)
        result = await _enrich(test_settings, mock_clients, EnrichmentRecord(
            record_id="PA_AGENCY", name1="Dr. Jane Smith", name2=None,
            city="Washington", state="DC", country="US",
        ))
        assert result.name1_enriched == NASA
        assert result.name2_enriched is None
        assert tier2a_calls == []

    @pytest.mark.asyncio
    async def test_university_gets_department(
        self, test_settings, mock_clients, tier2a_calls, affiliation,
    ):
        affiliation("Stanford University")
        result = await _enrich(test_settings, mock_clients, EnrichmentRecord(
            record_id="PA_UNI", name1="Dr. Jane Smith", name2=None,
            city="Stanford", state="CA", country="US",
        ))
        assert result.name1_enriched == "Stanford University"
        assert len(tier2a_calls) == 1
        # Tier 2A (the spy) found nothing, so the proposed department stands.
        assert result.name2_enriched == "Department of Chemistry"
