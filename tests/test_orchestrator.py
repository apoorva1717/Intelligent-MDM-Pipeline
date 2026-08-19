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
from tests.mocks.openai_mock import MockOpenAIClient


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
        """Institution with wrong name2 and contact → Tier 2A Mode B.

        Regression guard for the two gates that between them made
        verification mode unreachable: the contact-lookup gate used to
        require a blank Name 2, and the Tier 2 canonical short-circuit
        used to return before the gate was even evaluated. If either
        closes again this assertion fails.
        """
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
        assert result.tier2_mode == "2A_verification"
        assert result.name2_enriched == "Department of Chemistry"
        assert result.name2_match_result == "no_match"
        assert result.source == "contact_lookup_corrected"

    @pytest.mark.asyncio
    async def test_tier2a_verification_survives_canonical_short_circuit(
        self, orchestrator, default_options,
    ):
        """Canonicalisation running on Name 2 must not pre-empt Tier 2A.

        This record canonicalises successfully at Tier 2, which used to
        return immediately. It has a single contact, a research-institution
        classification and a resolved domain, so contact verification must
        still get its turn.
        """
        record = EnrichmentRecord(
            record_id="ORCH_005",
            name1="Johns Hopkins University",
            name2="Dept of Radiology",
            contact="Dr. Robert Chen",
            city="Baltimore",
            state="MD",
            country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_type == "research_institution"
        assert result.tier2_mode == "2A_verification"
        assert result.name2_enriched == "Department of Radiology"
        assert result.name2_match_result == "partial"
        assert result.source == "contact_lookup_found"

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
    async def test_typod_company_name_recovered_via_gleif_reverify(
        self, orchestrator, default_options,
    ):
        """A misspelled company name ("Bayr AG") is corrected and gets an LEI.

        GLEIF's name search is not typo-tolerant, so ROR and the raw-name LEI
        lookup both miss. The company-canonical LLM proposes "Bayer AG"; the
        identity guard blocks it (a typo is not a prefix/acronym), but the
        proposal is a spelling variant, so it is re-verified against GLEIF —
        which confirms BAYER AG (DE) and attaches its LEI. Previously this
        record fell to Tier 3 with the typo unchanged.
        """
        record = EnrichmentRecord(
            record_id="ORCH_BAYER",
            name1="Bayr AG",
            name2=None,
            contact=None,
            street1="Kaiser-Wilhelm-Allee 1",
            city="Leverkusen",
            country="DE",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_type == "company"
        # GLEIF's legal name "BAYER AG" is title-cased by finalise(); the key
        # point is the typo is gone and it's the real Bayer.
        assert result.name1_enriched == "Bayer AG"
        assert result.lei_id == "3157002JBAOA57BQAT84"
        assert result.tier_used == 1
        assert result.source == "gleif"

    @pytest.mark.asyncio
    async def test_hq_address_resolves_ambiguous_company_name(
        self, orchestrator, default_options,
    ):
        """The HQ street address resolves a name too ambiguous on its own.

        "Bayr" (no legal form) is ambiguous by name — the LLM only resolves it
        to "Bayer AG" once the street identifies Bayer's HQ. The same record
        WITHOUT the street stays unresolved. Proves the address is doing the
        work, and the spelling-variant gate + GLEIF re-verify still gate it.
        """
        base = dict(
            record_id="ORCH_BAYR2", name1="Bayr", name2=None, contact=None,
            city="Leverkusen", country="DE", postal_code="51373",
        )
        # Without the HQ street: unresolved (name alone is ambiguous).
        no_addr = await orchestrator.enrich_batch(
            [EnrichmentRecord(**base)], default_options,
        )
        assert no_addr.results[0].lei_id is None

        # With the HQ street: the address resolves it and attaches the LEI.
        with_addr = await orchestrator.enrich_batch(
            [EnrichmentRecord(**base, street1="Kaiser-Wilhelm-Allee 1")],
            default_options,
        )
        result = with_addr.results[0]
        assert result.record_type == "company"
        assert result.name1_enriched == "Bayer AG"
        assert result.lei_id == "3157002JBAOA57BQAT84"
        assert result.source == "gleif"

    @pytest.mark.asyncio
    async def test_abbreviated_institution_adopts_ror_official_name(
        self, orchestrator, default_options,
    ):
        """An abbreviated institution name adopts ROR's fuller official name.

        "Uni Stuttgart" resolves to ROR's University of Stuttgart; the input is
        normalised to the official name even though "Uni" (3 chars) is below
        the identity guard's per-token 4-char floor for "University" — the
        abbreviation-expanded comparison bridges it.
        """
        record = EnrichmentRecord(
            record_id="ORCH_UNISTG",
            name1="Uni Stuttgart",
            name2=None,
            city="Stuttgart",
            country="DE",
            postal_code="70569",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]

        assert result.record_type == "research_institution"
        assert result.ror_id == "https://ror.org/04vnq7t77"
        assert result.name1_enriched == "University of Stuttgart"

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


class _Tier2AConfidenceStub(MockOpenAIClient):
    """Forces the Tier 2A extraction confidence; delegates everything else."""

    def __init__(self, confidence: str) -> None:
        self._confidence = confidence

    async def extract_json(
        self, system_prompt, user_prompt, *, temperature=0.0, max_tokens=1024,
    ):
        if "extract affiliation for:" in user_prompt.lower():
            return {
                "person_found": True,
                "official_dept": "Department of Chemistry",
                "official_group": None,
                "title": "Professor",
                "confidence": self._confidence,
            }
        return await super().extract_json(
            system_prompt, user_prompt,
            temperature=temperature, max_tokens=max_tokens,
        )


class TestTier2AVerificationMergeLayer:
    """The sub-80 confidence split, observed end-to-end.

    Whether the record keeps its own Name 2 is decided by the merge
    layer's non-blank filter, so it is only visible from the orchestrator.
    "Department of Basket Weaving" is used because it does not fuzzy-match
    any ROR child, which would otherwise rewrite Name 2 at Tier 1 and mask
    what Tier 2A did.
    """

    def _record(self):
        return EnrichmentRecord(
            record_id="MERGE_001",
            name1="Massachusetts Institute of Technology",
            name2="Department of Basket Weaving",
            contact="Dr. Jane Smith",
            city="Cambridge",
            state="MA",
            country="US",
        )

    @pytest.mark.asyncio
    async def test_low_score_medium_confidence_keeps_record_value(
        self, settings, mock_clients, default_options,
    ):
        clients = {**mock_clients, "llm": _Tier2AConfidenceStub("medium")}
        orchestrator = Orchestrator(settings, mock_clients=clients)
        response = await orchestrator.enrich_batch([self._record()], default_options)
        result = response.results[0]

        assert result.tier2_mode == "2A_verification"
        assert result.name2_enriched == "Department of Basket Weaving"
        assert result.name2_match_result == "no_match"
        assert result.enrichment_status == "unresolved"
        assert result.flag_for_review is True

    @pytest.mark.asyncio
    async def test_low_score_high_confidence_overwrites_record_value(
        self, settings, mock_clients, default_options,
    ):
        clients = {**mock_clients, "llm": _Tier2AConfidenceStub("high")}
        orchestrator = Orchestrator(settings, mock_clients=clients)
        response = await orchestrator.enrich_batch([self._record()], default_options)
        result = response.results[0]

        assert result.tier2_mode == "2A_verification"
        assert result.name2_enriched == "Department of Chemistry"
        assert result.name2_match_result == "no_match"
        assert result.source == "contact_lookup_corrected"
