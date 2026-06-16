"""Tests for the enrichment orchestrator — full pipeline end-to-end with mocks.

Covers:
- ROR-based classification, query+filter call() interface
- Web search fallback (Tier 1B) when ROR fails
- name1 always populated
- Regex-based department extraction in Tier 2B
- Empty string guards
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def orchestrator(settings, mock_clients):
    return Orchestrator(settings, mock_clients=mock_clients)


@pytest.fixture
def default_options():
    return EnrichmentOptions(max_concurrency=1)


class TestOrchestrator:
    """End-to-end orchestrator tests with mock clients."""

    @pytest.mark.asyncio
    async def test_tier1_full_resolution(self, orchestrator, default_options):
        """Institution with name2 matching child org → Tier 1 resolves via ROR+child."""
        record = EnrichmentRecord(
            record_id="ORCH_001",
            name1="Massachusetts Institute of Technology",
            name2="Department of Chemistry",
            city="Cambridge",
            state="MA",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.name1_enriched == "Massachusetts Institute of Technology"
        assert result.confidence == "high"
        assert result.record_type == "research_institution"

    @pytest.mark.asyncio
    async def test_tier1_to_tier2a_population(self, orchestrator, default_options):
        """Institution with null name2 and contact → escalates to Tier 2A Mode A."""
        record = EnrichmentRecord(
            record_id="ORCH_002",
            name1="Massachusetts Institute of Technology",
            name2=None,
            contact="Dr. Jane Smith",
            city="Cambridge",
            state="MA",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_type == "research_institution"
        assert result.name1_enriched is not None

    @pytest.mark.asyncio
    async def test_tier1_to_tier2a_verification(self, orchestrator, default_options):
        """Institution with wrong name2 and contact → Tier 2A Mode B."""
        record = EnrichmentRecord(
            record_id="ORCH_003",
            name1="Massachusetts Institute of Technology",
            name2="Dept of AI",
            contact="Dr. Jane Smith",
            city="Cambridge",
            state="MA",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_type == "research_institution"
        assert result.name1_enriched is not None

    @pytest.mark.asyncio
    async def test_company_skips_tier2a(self, orchestrator, default_options):
        """Company records should never attempt Tier 2A."""
        record = EnrichmentRecord(
            record_id="ORCH_004",
            name1="Pfizer Inc",
            name2="Analytical Sciences",
            contact="Dr. Someone",
            city="New York",
            state="NY",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_type == "company"
        assert result.tier2_mode != "2A_population"
        assert result.tier2_mode != "2A_verification"

    @pytest.mark.asyncio
    async def test_unknown_institution_reaches_tier3(self, orchestrator, default_options):
        """Unknown institution with no contact → falls to Tier 3 but name1 is populated."""
        record = EnrichmentRecord(
            record_id="ORCH_005",
            name1="Completely Unknown Org XYZ",
            name2=None,
            contact=None,
            city="Nowhere",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        # name1 should always be populated even when ROR fails
        assert result.name1_enriched is not None
        assert result.name1_enriched != ""

    @pytest.mark.asyncio
    async def test_batch_processing(self, orchestrator):
        """Multiple records processed concurrently."""
        records = [
            EnrichmentRecord(
                record_id="BATCH_001", name1="MIT", name2="Department of Physics",
                city="Cambridge", state="MA", country="US",
            ),
            EnrichmentRecord(
                record_id="BATCH_002", name1="Pfizer Inc", name2="R&D",
                city="New York", state="NY",
            ),
            EnrichmentRecord(
                record_id="BATCH_003", name1="UCLA", name2=None, contact="Dr. John Doe",
                city="Los Angeles", state="CA", country="US",
            ),
        ]
        options = EnrichmentOptions(max_concurrency=3)
        response = await orchestrator.enrich_batch(records, options)

        assert len(response.results) == 3
        assert response.summary.total == 3
        assert all(r.record_id.startswith("BATCH_") for r in response.results)

    @pytest.mark.asyncio
    async def test_classification_from_ror(self, orchestrator, default_options):
        """Record type is derived from ROR org types, not keyword matching."""
        records = [
            EnrichmentRecord(record_id="CLS_001", name1="Harvard University",
                             city="Cambridge", state="MA", country="US"),
            EnrichmentRecord(record_id="CLS_002", name1="Novartis",
                             city="Basel", country="CH"),
        ]
        response = await orchestrator.enrich_batch(records, default_options)

        assert response.results[0].record_type == "research_institution"
        assert response.results[1].record_type == "company"

    @pytest.mark.asyncio
    async def test_name1_enriched_preserved(self, orchestrator, default_options):
        """Name1 enrichment is written immediately after ROR match (Bug 9)."""
        record = EnrichmentRecord(
            record_id="PRES_001",
            name1="UCLA",
            name2=None,
            city="Los Angeles",
            state="CA",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.name1_enriched is not None
        assert result.name1_enriched != ""

    @pytest.mark.asyncio
    async def test_enriched_never_empty_string(self, orchestrator, default_options):
        """Enriched fields must never be empty string (Bug 5)."""
        record = EnrichmentRecord(
            record_id="EMPTY_001",
            name1="MIT",
            name2=None,
            city="Cambridge",
            state="MA",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        for field in ("name1_enriched", "name2_enriched", "name3_enriched"):
            val = getattr(result, field)
            if val is not None:
                assert val.strip() != "", f"{field} is empty string"

    @pytest.mark.asyncio
    async def test_null_name1_handled(self, orchestrator, default_options):
        """Record with null name1 does not crash."""
        record = EnrichmentRecord(
            record_id="NULL_001",
            name1=None,
            name2=None,
            contact="Dr. Test",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_id == "NULL_001"
        assert result.error is None or result.enrichment_status in ("failed", "unresolved")

    @pytest.mark.asyncio
    async def test_web_search_fallback_for_name1(self, orchestrator, default_options):
        """When ROR fails, web search should resolve name1 and extract domain."""
        record = EnrichmentRecord(
            record_id="WEB_001",
            name1="Comet Therapeutics",
            name2=None,
            city="San Francisco",
            state="CA",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.name1_enriched == "Comet Therapeutics"
        assert result.record_type == "company"  # .com domain → company
        assert result.source in ("web_search", "dept_search", "LLM")

    @pytest.mark.asyncio
    async def test_name1_always_populated(self, orchestrator, default_options):
        """name1_enriched must never be None when the original name1 is present."""
        records = [
            EnrichmentRecord(
                record_id="POP_001", name1="Comet Therapeutics",
                city="San Francisco", country="US",
            ),
            EnrichmentRecord(
                record_id="POP_002", name1="Completely Unknown Org XYZ",
                city="Nowhere", country="US",
            ),
            EnrichmentRecord(
                record_id="POP_003", name1="MIT",
                city="Cambridge", state="MA", country="US",
            ),
        ]
        response = await orchestrator.enrich_batch(records, default_options)

        for r in response.results:
            assert r.name1_enriched is not None, (
                f"{r.record_id}: name1_enriched should not be None"
            )
            assert r.name1_enriched != "", (
                f"{r.record_id}: name1_enriched should not be empty"
            )

    @pytest.mark.asyncio
    async def test_web_search_determines_record_type(self, orchestrator, default_options):
        """Web search should infer record_type from domain heuristics."""
        record = EnrichmentRecord(
            record_id="TYPE_001",
            name1="Comet Therapeutics",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        # comettherapeutics.com → company (not research_institution)
        assert result.record_type == "company"

    @pytest.mark.asyncio
    async def test_name1_hyphenated_person_moved_to_contact(
        self, orchestrator, default_options,
    ):
        """A hyphenated person name in Name 1 (no org) → moved to Contact, Name 1 cleared.

        Regression: the old plain-name pattern could not match "Macdonald-Korth"
        (capital after the hyphen), so UC 7 never saw it as a person.
        """
        record = EnrichmentRecord(
            record_id="PERSON_001",
            name1="Emily Macdonald-Korth",
            city="Miami",
            state="FL",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        # Person routed to Contact; Name 1 cleared (no organisation supplied).
        assert result.contact_enriched == "Emily Macdonald-Korth"
        assert not (result.name1_enriched or "").strip()

    @pytest.mark.asyncio
    async def test_name1_person_with_existing_contact_conflict(
        self, orchestrator, default_options,
    ):
        """Person in Name 1 with Contact already populated → contact-conflict flag."""
        record = EnrichmentRecord(
            record_id="PERSON_002",
            name1="David Johnson",
            contact="Alice Existing",
            city="Miami",
            state="FL",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        # Existing contact preserved (person not overwritten); record flagged.
        assert result.flag_for_review is True
        assert result.contact_enriched == "Alice Existing"

    @pytest.mark.asyncio
    async def test_eponymous_company_not_treated_as_person(
        self, orchestrator, default_options,
    ):
        """A person-shaped company name (classified 'organisation') stays in Name 1."""
        record = EnrichmentRecord(
            record_id="PERSON_003",
            name1="Robert Bosch",
            city="Farmington Hills",
            state="MI",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        # Not moved to contact — kept as the organisation name.
        assert result.contact_enriched != "Robert Bosch"
        assert (result.name1_enriched or "") != ""
