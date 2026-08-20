"""Batch consensus pass (Fix 6) — one identity per organisation per address.

After Fixes 1-5, rows that share an organisation AND an address could still
leave the batch with different identities, because each was enriched in
isolation and some resolved against a registry while others did not. Fix 2's
Tier 1 re-lookup catches most of it at source; this pass is the safety net for
the rest.

The pass is field propagation, never a merge: the record count is unchanged
and `tier_used` is unchanged. What it must get right is where the boundaries
sit — a different address, an incompatible legal form, or two conflicting
registry identities all mean "propagate nothing".

It raises no flag. It withdraws exactly the codes its own write to
`name1_enriched` falsified, and `TestFlagsFalsifiedByPropagation` pins both
halves of that: what goes, and — the larger half — what stays.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentResult
from enrichment.batch_consensus import (
    NEVER_PROPAGATED,
    PROPAGATED_FIELDS,
    _address_block_id,
    _name_parts,
    apply_batch_consensus,
)
from enrichment.flags import (
    DOMAIN_UNVERIFIED,
    LOW_CONFIDENCE_UNCHANGED,
    NO_MATCH,
    UNVERIFIED_INFERENCE,
    render as render_flags,
)

# The demo batch's Coastal trio address (rows 15-17).
TAMPA = {
    "street_cleaned": "500 Bayshore Blvd",
    "postal_code": "33602",
    "city": "Tampa",
    "country_region_key": "US",
}
BOSTON = {
    "street_cleaned": "800 Boylston St",
    "postal_code": "02199",
    "city": "Boston",
    "country_region_key": "US",
}


def _result(record_id: str, name1: str, **kw) -> EnrichmentResult:
    """An already-finalised result. Defaults to the Tampa address so a test
    only states the address when the address is what it is testing."""
    fields = dict(TAMPA)
    fields.update(kw)
    return EnrichmentResult(record_id=record_id, name1_enriched=name1, **fields)


def _flagged(record_id: str, name1: str, scopes: dict, **kw) -> EnrichmentResult:
    """A result carrying real flags — rendered by the flag module itself, so a
    test states the code/scope map and never hand-writes the four columns."""
    return _result(record_id, name1, **{**render_flags(scopes), **kw})


class TestPropagation:
    def test_single_registry_identity_reaches_every_member(self):
        """One resolved row carries the whole group."""
        rows = [
            _result("r15", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    domain="coastaldiagnostics.com",
                    website_url="https://coastaldiagnostics.com",
                    record_type="company", tier_used=1, source="ROR"),
            _result("r16", "Coastal Diagnostics", tier_used=3, source="LLM"),
            _result("r17", "Coastal Diagnostics Inc", tier_used=3,
                    source="passthrough"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 1
        assert telemetry.conflicts == 0
        assert telemetry.records_updated == 2  # the donor inherits nothing
        for row in rows:
            assert row.ror_id == "ror.org/01abc"
            assert row.domain == "coastaldiagnostics.com"
            assert row.website_url == "https://coastaldiagnostics.com"
            assert row.record_type == "company"
            assert row.name1_enriched == "Coastal Diagnostics Inc"

    def test_inheriting_records_are_marked_and_the_donor_is_not(self):
        rows = [
            _result("r19", "Lockheed Martin Corp", lei_id="LEI-1", source="gleif"),
            _result("r20", "Lockheed Martin Corp", source="passthrough"),
        ]
        apply_batch_consensus(rows)

        assert rows[0].source == "gleif", "the donor did not inherit anything"
        assert rows[1].source == "batch_consensus"

    def test_a_record_that_already_agrees_is_not_counted_as_updated(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    source="ROR"),
            _result("b", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    source="ROR"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.records_updated == 0
        assert all(r.source == "ROR" for r in rows)

    def test_a_null_donor_value_never_erases_a_resolved_one(self):
        """The donor holds the id; another member holds the only domain."""
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc"),
            _result("b", "Coastal Diagnostics Inc",
                    domain="coastaldiagnostics.com",
                    website_url="https://coastaldiagnostics.com"),
        ]
        apply_batch_consensus(rows)

        assert rows[0].domain == "coastaldiagnostics.com"
        assert rows[1].domain == "coastaldiagnostics.com"
        assert rows[1].ror_id == "ror.org/01abc"

    def test_unknown_record_type_is_absent_not_conflicting(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    record_type="company"),
            _result("b", "Coastal Diagnostics Inc", record_type="unknown"),
        ]
        apply_batch_consensus(rows)

        assert rows[1].record_type == "company"

    def test_a_group_with_no_registry_identity_invents_no_identity(self):
        """Two unresolved rows converge on what the group already agrees about
        (see TestNameFormConsensus) but no registry id is ever conjured."""
        rows = [
            _result("a", "Coastal Diagnostics Inc", domain="one.com"),
            _result("b", "Coastal Diagnostics Inc"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 1
        assert telemetry.conflicts == 0
        assert all(r.ror_id is None and r.lei_id is None for r in rows)


class TestConflicts:
    def test_two_conflicting_registry_identities_propagate_nothing(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    domain="one.com", record_type="company", source="ROR"),
            _result("b", "Coastal Diagnostics Inc", ror_id="ror.org/09xyz",
                    source="ROR"),
            _result("c", "Coastal Diagnostics Inc", source="passthrough"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.conflicts == 1
        assert telemetry.records_updated == 0
        assert rows[1].ror_id == "ror.org/09xyz", "left exactly as it was"
        assert rows[2].ror_id is None
        assert rows[2].domain is None
        assert all(r.source != "batch_consensus" for r in rows)

    def test_a_conflict_writes_no_flag(self):
        """The flagging model is being redesigned separately — telemetry only."""
        rows = [
            _result("a", "Coastal Diagnostics Inc", lei_id="LEI-1"),
            _result("b", "Coastal Diagnostics Inc", lei_id="LEI-2"),
        ]
        apply_batch_consensus(rows)

        assert all(r.flag_for_review is False for r in rows)
        assert all(r.flag_reason is None for r in rows)

    def test_conflicting_lei_is_a_conflict_even_when_ror_agrees(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    lei_id="LEI-1"),
            _result("b", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    lei_id="LEI-2"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.conflicts == 1
        assert telemetry.records_updated == 0


class TestLegalFormCompatibility:
    def test_inc_and_no_legal_form_group_together(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc"),
            _result("b", "Coastal Diagnostics"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 1
        assert rows[1].ror_id == "ror.org/01abc"

    def test_punctuated_and_bare_inc_are_identical_after_canonicalisation(self):
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc"),
            _result("b", "Coastal Diagnostics Inc"),
        ]
        apply_batch_consensus(rows)

        assert rows[1].ror_id == "ror.org/01abc"

    def test_inc_and_llc_do_not_group(self):
        """Potentially distinct legal entities — exactly Phase 2's judgement."""
        rows = [
            _result("a", "Delta Analytical Inc", ror_id="ror.org/01abc"),
            _result("b", "Delta Analytical LLC"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 0, "two singleton groups, neither propagates"
        assert rows[1].ror_id is None

    def test_an_absent_form_between_two_competing_forms_inherits_nothing(self):
        """Compatibility is not transitive; assigning the bare row to one of
        the two forms would be a guess."""
        rows = [
            _result("a", "Delta Analytical Inc", ror_id="ror.org/01abc"),
            _result("b", "Delta Analytical LLC", ror_id="ror.org/09xyz"),
            _result("c", "Delta Analytical"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 0
        assert telemetry.conflicts == 0
        assert rows[2].ror_id is None

    def test_spelled_out_and_abbreviated_forms_are_the_same_form(self):
        """_normalise_for_tokens canonicalises Corporation → corp."""
        assert _name_parts("Lockheed Martin Corporation") == _name_parts(
            "Lockheed Martin Corp"
        )
        assert _name_parts("Acme Company") == _name_parts("Acme Co")


class TestNeverPropagated:
    def test_name2_and_department_domain_are_never_propagated(self):
        """Rows 12-14: Stanford at one address, three departments."""
        rows = [
            _result("r12", "Stanford University", ror_id="ror.org/00f54p054",
                    domain="stanford.edu", name2_enriched="Department of Chemistry",
                    department_domain="chemistry.stanford.edu"),
            _result("r13", "Stanford University",
                    name2_enriched="Department of Chemistry",
                    department_domain="chemistry.stanford.edu"),
            _result("r14", "Stanford University",
                    name2_enriched="Department of Physics",
                    department_domain="physics.stanford.edu"),
        ]
        apply_batch_consensus(rows)

        assert all(r.ror_id == "ror.org/00f54p054" for r in rows)
        assert all(r.domain == "stanford.edu" for r in rows)
        assert [r.department_domain for r in rows] == [
            "chemistry.stanford.edu",
            "chemistry.stanford.edu",
            "physics.stanford.edu",
        ]
        assert rows[2].name2_enriched == "Department of Physics"

    def test_differing_name2_does_not_prevent_propagation(self):
        rows = [
            _result("a", "Stanford University", ror_id="ror.org/00f54p054",
                    name2_enriched="Department of Chemistry"),
            _result("b", "Stanford University", name2_enriched="Department of Physics"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 1
        assert rows[1].ror_id == "ror.org/00f54p054"

    def test_record_level_fields_survive_untouched(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    contact_enriched="Jane Doe",
                    email_enriched="jane@coastaldiagnostics.com",
                    care_of_enriched="Accounts Payable",
                    search_term_2="CHEMISTRY"),
            _result("b", "Coastal Diagnostics Inc"),
        ]
        apply_batch_consensus(rows)

        for name in NEVER_PROPAGATED:
            assert getattr(rows[1], name) == getattr(
                _result("b", "Coastal Diagnostics Inc"), name
            ), f"{name} must not be propagated"

    def test_the_two_field_lists_do_not_overlap(self):
        assert not set(PROPAGATED_FIELDS) & set(NEVER_PROPAGATED)


class TestAddressBoundary:
    def test_same_name_at_different_addresses_does_not_group(self):
        """Rows 43 and 44: two 'Cardinal Instruments', two cities."""
        rows = [
            _result("r43", "Cardinal Instruments", ror_id="ror.org/01abc",
                    **{**TAMPA, "street_cleaned": "200 Bayshore Blvd"}),
            _result("r44", "Cardinal Instruments", **BOSTON),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 0
        assert rows[1].ror_id is None

    def test_a_record_with_no_address_signal_never_groups(self):
        rows = [
            EnrichmentResult(record_id="a", name1_enriched="Coastal Diagnostics Inc",
                             ror_id="ror.org/01abc"),
            EnrichmentResult(record_id="b", name1_enriched="Coastal Diagnostics Inc"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 0
        assert rows[1].ror_id is None

    def test_the_block_id_is_the_phase_2_derivation(self):
        """Reused from dedup/signatures.py, not a second address key."""
        from dedup.models import DedupRow
        from dedup.signatures import derive_block_id

        row = _result("a", "Coastal Diagnostics Inc")
        assert _address_block_id(row) == derive_block_id(
            DedupRow(row_id="a", street="500 Bayshore Blvd", postal_code="33602",
                     city="Tampa", country="US")
        )


class TestInvariants:
    def test_an_inherited_record_keeps_its_original_tier_used(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    tier_used=1),
            _result("b", "Coastal Diagnostics Inc", tier_used=3),
            _result("c", "Coastal Diagnostics", tier_used=2),
        ]
        apply_batch_consensus(rows)

        assert [r.tier_used for r in rows] == [1, 3, 2]
        assert rows[1].source == "batch_consensus"

    def test_the_record_count_and_order_are_unchanged(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc"),
            _result("b", "Coastal Diagnostics"),
            _result("c", "Cardinal Instruments", **BOSTON),
        ]
        apply_batch_consensus(rows)

        assert [r.record_id for r in rows] == ["a", "b", "c"]

    def test_a_flag_on_a_field_this_pass_did_not_write_is_untouched(self):
        """Inheriting a name says nothing about the record's domain."""
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc"),
            _flagged("b", "Coastal Diagnostics", {DOMAIN_UNVERIFIED: {"domain"}}),
        ]
        before = rows[1].flag_reason
        apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Coastal Diagnostics Inc"  # it did inherit
        assert rows[1].flag_for_review is True
        assert rows[1].flag_codes == [DOMAIN_UNVERIFIED]
        assert rows[1].flag_reason == before
        assert rows[0].flag_for_review is False

    def test_the_grouping_key_never_appears_in_any_output_field(self):
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc",
                    domain="coastaldiagnostics.com", record_type="company"),
            _result("b", "Coastal Diagnostics"),
        ]
        block_id = _address_block_id(rows[0])
        base, legal_form = _name_parts(rows[0].name1_enriched)
        apply_batch_consensus(rows)

        for row in rows:
            values = [str(v) for v in row.model_dump(by_alias=True).values()
                      if v is not None]
            assert block_id not in values
            assert not any(block_id in v for v in values)
            assert base not in values          # "coastal diagnostics", lowercased
            assert legal_form not in values    # "inc"

    def test_propagated_values_are_copied_verbatim(self):
        """They come from an already-finalised record, so no casing or
        abbreviation-expansion pass may run over them again."""
        rows = [
            _result("a", "Massachusetts Institute of Technology",
                    ror_id="ror.org/042nb2s44", domain="mit.edu"),
            _result("b", "Massachusetts Institute of Technology"),
        ]
        apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Massachusetts Institute of Technology"


class TestTelemetry:
    def test_counters_describe_the_batch(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    domain="coastaldiagnostics.com", record_type="company"),
            _result("b", "Coastal Diagnostics"),
            _result("c", "Cardinal Instruments", lei_id="LEI-1", **BOSTON),
            _result("d", "Cardinal Instruments", lei_id="LEI-2", **BOSTON),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 2
        assert telemetry.conflicts == 1
        assert telemetry.records_updated == 1
        assert telemetry.fields_propagated == {
            "ror_id": 1, "name1_enriched": 1, "domain": 1, "record_type": 1,
        }

    def test_a_field_that_did_not_move_is_not_counted(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc", ror_id="ror.org/01abc",
                    record_type="company"),
            _result("b", "Coastal Diagnostics Inc", record_type="company"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert "record_type" not in telemetry.fields_propagated
        assert telemetry.fields_propagated == {"ror_id": 1}


class TestWiredIntoTheBatch:
    """The pass has to actually run inside `enrich_batch`, after finalisation
    and before serialisation, and its counters have to reach the summary."""

    @pytest.mark.asyncio
    async def test_a_batch_reports_consensus_telemetry(self, mock_clients):
        from api.models import EnrichmentOptions, EnrichmentRecord
        from config import Settings
        from enrichment.orchestrator import Orchestrator

        orchestrator = Orchestrator(Settings(), mock_clients=mock_clients)
        records = [
            EnrichmentRecord(
                record_id=f"CONS_{i}",
                name1=name,
                street_1="77 Massachusetts Ave",
                postal_code="02139",
                city="Cambridge",
                country="US",
            )
            for i, name in enumerate(
                ["Massachusetts Institute of Technology",
                 "Massachusetts Institute of Technology"]
            )
        ]
        response = await orchestrator.enrich_batch(
            records, EnrichmentOptions(max_concurrency=1)
        )

        assert len(response.results) == 2, "field propagation never drops a record"
        assert response.summary.consensus_groups >= 1
        assert isinstance(response.summary.consensus_fields_propagated, dict)
        assert response.summary.consensus_conflicts == 0
        # Both rows leave the batch with the same organisation identity.
        assert len({r.ror_id for r in response.results}) == 1
        assert len({r.name1_enriched for r in response.results}) == 1
        assert len({r.record_type for r in response.results}) == 1

    @pytest.mark.asyncio
    async def test_no_tier_used_changes_as_a_result_of_the_pass(self, mock_clients):
        from api.models import EnrichmentOptions, EnrichmentRecord
        from config import Settings
        from enrichment.orchestrator import Orchestrator
        from enrichment import batch_consensus

        orchestrator = Orchestrator(Settings(), mock_clients=mock_clients)
        records = [
            EnrichmentRecord(record_id="TIER_A",
                             name1="Massachusetts Institute of Technology",
                             street_1="77 Massachusetts Ave", postal_code="02139",
                             city="Cambridge", country="US"),
            EnrichmentRecord(record_id="TIER_B", name1="MASSACHUSETTS INSTITUTE OF TECHNOLOGY",
                             street_1="77 Massachusetts Ave", postal_code="02139",
                             city="Cambridge", country="US"),
        ]
        seen: dict[str, int] = {}
        original = batch_consensus.apply_batch_consensus

        def _spy(results):
            seen.update({r.record_id: r.tier_used for r in results})
            return original(results)

        batch_consensus.apply_batch_consensus = _spy
        try:
            # The orchestrator imported the symbol directly, so patch there too.
            import enrichment.orchestrator as orch_mod
            orch_mod.apply_batch_consensus = _spy
            response = await orchestrator.enrich_batch(
                records, EnrichmentOptions(max_concurrency=1)
            )
        finally:
            batch_consensus.apply_batch_consensus = original
            import enrichment.orchestrator as orch_mod
            orch_mod.apply_batch_consensus = original

        assert seen, "the pass never ran"
        for result in response.results:
            assert result.tier_used == seen[result.record_id]


class TestNameFormConsensus:
    """A group with NO registry identity converges the spelling of the name its
    members already share — and nothing else. Every member holds the same
    organisation name (that is what grouped them); only the legal form's
    spelling, punctuation or casing differs, so electing one asserts no new
    fact. Anything that WOULD assert one stays put."""

    def test_the_modal_name_form_wins(self):
        """Rows 15-17: three unresolved Coastal rows, two spellings."""
        rows = [
            _result("r15", "Coastal Diagnostics, Inc."),
            _result("r16", "Coastal Diagnostics"),
            _result("r17", "Coastal Diagnostics, Inc."),
        ]
        telemetry = apply_batch_consensus(rows)

        assert {r.name1_enriched for r in rows} == {"Coastal Diagnostics, Inc."}
        assert telemetry.records_updated == 1
        assert telemetry.fields_propagated == {"name1_enriched": 1}
        assert rows[1].source == "batch_consensus"

    def test_a_unanimous_value_fills_a_gap(self):
        """One member resolved a domain, the other resolved nothing. The batch
        does not disagree, so the gap is filled."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.",
                    domain="coastaldiagnostics.com",
                    website_url="https://coastaldiagnostics.com",
                    record_type="company"),
            _result("b", "Coastal Diagnostics"),
        ]
        apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Coastal Diagnostics, Inc."
        assert rows[1].domain == "coastaldiagnostics.com"
        assert rows[1].website_url == "https://coastaldiagnostics.com"
        assert rows[1].record_type == "company"
        assert rows[1].ror_id is None and rows[1].lei_id is None

    def test_competing_domains_propagate_neither(self):
        """The weaker tier fills gaps; it never picks a winner. Without a
        registry there is nothing to break the tie with."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", domain="one.com",
                    website_url="https://one.com"),
            _result("b", "Coastal Diagnostics", domain="two.com",
                    website_url="https://two.com"),
            _result("c", "Coastal Diagnostics, Inc."),
        ]
        apply_batch_consensus(rows)

        assert [r.domain for r in rows] == ["one.com", "two.com", None]
        assert [r.website_url for r in rows] == [
            "https://one.com", "https://two.com", None,
        ]
        # …but the name form, whose variants are not competing facts, converges.
        assert {r.name1_enriched for r in rows} == {"Coastal Diagnostics, Inc."}

    def test_competing_record_types_propagate_neither(self):
        rows = [
            _result("a", "Harbor Clinic, Inc.", record_type="company"),
            _result("b", "Harbor Clinic", record_type="research_institution"),
            _result("c", "Harbor Clinic, Inc."),
        ]
        apply_batch_consensus(rows)

        assert [r.record_type for r in rows] == [
            "company", "research_institution", "unknown",
        ]

    def test_a_registry_donor_outranks_the_unanimity_rule(self):
        """With a registry present the donor decides, even against a majority."""
        rows = [
            _result("a", "Harbor Clinic, Inc.", ror_id="ror.org/01abc",
                    domain="harborclinic.org", record_type="company"),
            _result("b", "Harbor Clinic, Inc.", domain="wrong.com"),
            _result("c", "Harbor Clinic, Inc.", domain="wrong.com"),
        ]
        apply_batch_consensus(rows)

        assert {r.domain for r in rows} == {"harborclinic.org"}

    def test_a_group_that_already_agrees_is_a_no_op(self):
        rows = [
            _result("a", "Coastal Diagnostics Inc"),
            _result("b", "Coastal Diagnostics Inc"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.records_updated == 0
        assert telemetry.fields_propagated == {}
        assert all(r.source != "batch_consensus" for r in rows)

    def test_ties_break_on_tier_then_batch_order(self):
        """Two spellings, one row each — the earlier tier wins, not the
        earlier row."""
        rows = [
            _result("a", "Coastal Diagnostics", tier_used=3),
            _result("b", "Coastal Diagnostics, Inc.", tier_used=2),
        ]
        apply_batch_consensus(rows)

        assert {r.name1_enriched for r in rows} == {"Coastal Diagnostics, Inc."}

    def test_a_registry_group_still_propagates_everything(self):
        """The weaker tier must not have weakened the stronger one."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc",
                    domain="coastaldiagnostics.com", record_type="company"),
            _result("b", "Coastal Diagnostics"),
        ]
        apply_batch_consensus(rows)

        assert rows[1].ror_id == "ror.org/01abc"
        assert rows[1].domain == "coastaldiagnostics.com"
        assert rows[1].record_type == "company"

    def test_incompatible_legal_forms_still_do_not_share_a_name(self):
        rows = [
            _result("a", "Delta Analytical Inc"),
            _result("b", "Delta Analytical LLC"),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.groups == 0
        assert rows[0].name1_enriched == "Delta Analytical Inc"
        assert rows[1].name1_enriched == "Delta Analytical LLC"

    def test_different_addresses_still_do_not_share_a_name(self):
        rows = [
            _result("r43", "CARDINAL INSTRUMENTS",
                    **{**TAMPA, "street_cleaned": "200 Bayshore Blvd"}),
            _result("r44", "Cardinal Instruments", **BOSTON),
        ]
        apply_batch_consensus(rows)

        assert rows[0].name1_enriched == "CARDINAL INSTRUMENTS"
        assert rows[1].name1_enriched == "Cardinal Instruments"

    def test_name_form_consensus_keeps_tier_used_and_unrelated_flags(self):
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", tier_used=1),
            _flagged("b", "Coastal Diagnostics",
                     {UNVERIFIED_INFERENCE: {"name2"}}, tier_used=3),
        ]
        apply_batch_consensus(rows)

        assert [r.tier_used for r in rows] == [1, 3]
        assert rows[1].flag_for_review is True
        assert rows[1].flag_codes == [UNVERIFIED_INFERENCE]
        assert rows[1].flagged_fields == ["name2"]

    def test_departments_survive_name_form_consensus(self):
        rows = [
            _result("a", "Harbor Clinic, Inc.", name2_enriched="Radiology",
                    department_domain="rad.harborclinic.com"),
            _result("b", "Harbor Clinic", name2_enriched="Cardiology",
                    department_domain="cardio.harborclinic.com"),
        ]
        apply_batch_consensus(rows)

        assert {r.name1_enriched for r in rows} == {"Harbor Clinic, Inc."}
        assert [r.name2_enriched for r in rows] == ["Radiology", "Cardiology"]
        assert [r.department_domain for r in rows] == [
            "rad.harborclinic.com", "cardio.harborclinic.com",
        ]


class TestFlagsFalsifiedByPropagation:
    """The one flag interaction this pass has, and its limits.

    A flag is a statement about a field's value. Propagation replaces the
    value, so a statement that described the replaced one can end up simply
    false — `low-confidence-unchanged` reads "left exactly as supplied" on a
    record that was not left as supplied. The demo batch showed it plainly:
    rows 15-17 spell one company three ways, converge on one Name 1, and one
    of the three identical names still carried that reason.

    Withdrawal is bookkeeping about this pass's own write, not a judgement
    about the record, so it stops where the write stops. Most of the tests
    below are about where that is.
    """

    def test_low_confidence_unchanged_goes_when_the_name_is_replaced(self):
        """The demo's Coastal trio: three spellings, one name, no flag left
        claiming the value was never touched."""
        rows = [
            _result("r15", "Coastal Diagnostics, Inc."),
            _flagged("r16", "Coastal Diagnostics",
                     {LOW_CONFIDENCE_UNCHANGED: {"name1"}}),
            _result("r17", "Coastal Diagnostics, Inc."),
        ]
        telemetry = apply_batch_consensus(rows)

        assert {r.name1_enriched for r in rows} == {"Coastal Diagnostics, Inc."}
        assert all(r.flag_for_review is False for r in rows)
        assert all(r.flag_codes == [] for r in rows)
        assert all(r.flag_reason is None for r in rows)
        assert all(r.flagged_fields == [] for r in rows)
        assert telemetry.flags_retracted == 1

    def test_it_goes_under_registry_consensus_too(self):
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc"),
            _flagged("b", "Coastal Diagnostics",
                     {LOW_CONFIDENCE_UNCHANGED: {"name1"}}),
        ]
        apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Coastal Diagnostics, Inc."
        assert rows[1].flag_codes == []

    def test_a_name_that_does_not_move_keeps_its_flag(self):
        """No write, nothing falsified. The group agrees on the spelling the
        flagged record already holds, so its doubt is untouched."""
        rows = [
            _result("a", "Coastal Diagnostics", ror_id="ror.org/01abc"),
            _flagged("b", "Coastal Diagnostics",
                     {LOW_CONFIDENCE_UNCHANGED: {"name1"}}),
        ]
        telemetry = apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Coastal Diagnostics"
        assert rows[1].flag_codes == [LOW_CONFIDENCE_UNCHANGED]
        assert telemetry.flags_retracted == 0

    def test_the_donor_keeps_its_own_flag(self):
        rows = [
            _flagged("a", "Coastal Diagnostics, Inc.",
                     {LOW_CONFIDENCE_UNCHANGED: {"name1"}},
                     ror_id="ror.org/01abc"),
            _result("b", "Coastal Diagnostics"),
        ]
        apply_batch_consensus(rows)

        assert rows[0].flag_codes == [LOW_CONFIDENCE_UNCHANGED]
        assert rows[1].flag_codes == []

    def test_registry_consensus_answers_no_match_and_unverified_inference(self):
        """A registry identity arrives with the name, so "nothing identified
        this organisation" and "rests on no external evidence" both stop being
        true — and the donor's record id is in the receiver's provenance."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc"),
            _flagged("b", "Coastal Diagnostics",
                     {NO_MATCH: {"name1"}, UNVERIFIED_INFERENCE: {"name1"}}),
        ]
        telemetry = apply_batch_consensus(rows)

        assert rows[1].ror_id == "ror.org/01abc"
        assert rows[1].flag_codes == []
        assert telemetry.flags_retracted == 2

    def test_name_form_consensus_answers_neither(self):
        """Electing the batch's modal spelling introduces no evidence, so a
        doubt about evidence survives it. Only the "unchanged" claim goes."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc."),
            _flagged("b", "Coastal Diagnostics", {
                NO_MATCH: {"name1"},
                UNVERIFIED_INFERENCE: {"name1"},
                LOW_CONFIDENCE_UNCHANGED: {"name1"},
            }),
        ]
        apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Coastal Diagnostics, Inc."
        assert rows[1].flag_codes == [NO_MATCH, UNVERIFIED_INFERENCE]
        assert rows[1].flagged_fields == ["name1"]

    def test_a_code_scoped_to_two_fields_keeps_the_other_one(self):
        """Withdrawal is per field, not per code: name1's half goes and
        name2's half stays, because nothing was written to name2."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc"),
            _flagged("b", "Coastal Diagnostics",
                     {LOW_CONFIDENCE_UNCHANGED: {"name1", "name2"}},
                     name2_enriched="Radiology"),
        ]
        apply_batch_consensus(rows)

        assert rows[1].flag_codes == [LOW_CONFIDENCE_UNCHANGED]
        assert rows[1].flagged_fields == ["name2"]
        assert rows[1].flag_reason.startswith("Name 2:")

    def test_a_conflicting_group_withdraws_nothing(self):
        """Two registry identities means propagate nothing, so no flag can
        have been falsified."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc"),
            _result("b", "Coastal Diagnostics, Inc.", ror_id="ror.org/09xyz"),
            _flagged("c", "Coastal Diagnostics",
                     {LOW_CONFIDENCE_UNCHANGED: {"name1"}}),
        ]
        telemetry = apply_batch_consensus(rows)

        assert telemetry.conflicts == 1
        assert telemetry.flags_retracted == 0
        assert rows[2].flag_codes == [LOW_CONFIDENCE_UNCHANGED]

    def test_the_three_flag_columns_stay_consistent_after_withdrawal(self):
        """flag_for_review iff flag_codes, and flag_reason renders the same
        codes — the contract holds through a retraction as well as a raise."""
        rows = [
            _result("a", "Coastal Diagnostics, Inc.", ror_id="ror.org/01abc"),
            _flagged("b", "Coastal Diagnostics", {
                LOW_CONFIDENCE_UNCHANGED: {"name1"},
                DOMAIN_UNVERIFIED: {"domain"},
            }),
        ]
        apply_batch_consensus(rows)

        row = rows[1]
        assert row.flag_codes == [DOMAIN_UNVERIFIED]
        assert row.flag_for_review is (len(row.flag_codes) > 0)
        assert row.flagged_fields == ["domain"]
        assert row.flag_reason.startswith("Domain:")
        assert "left exactly as supplied" not in row.flag_reason
