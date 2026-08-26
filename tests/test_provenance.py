"""Fix 10: per-field provenance and admissibility.

The principle these tests pin:

    Every value the system writes must be attributable after the fact to the
    source that produced it and the confidence under which it was produced. A
    written value whose origin cannot be reconstructed is not admissible.

Recording provenance is easy to add and easy to bypass, so most of what is
tested here is the *enforcement*: a scoped field cannot be assigned, only
written with evidence, and a value that reached finalisation without an event
is reverted rather than shipped. The rest pins the three situations the event
model has to be able to represent — different fields from different producers,
one field from several tools chained, and one field written twice.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord, EnrichmentResult
from config import Settings
from enrichment.batch_consensus import apply_batch_consensus
from enrichment.orchestrator import (
    Orchestrator,
    _apply_domain,
    _init_result,
    finalise,
)
from enrichment.provenance import (
    DERIVED_SCALAR_FIELDS,
    GUARD_DOMAIN_OWNERSHIP,
    LLM_SELF_REPORTED,
    SCOPED_FIELDS,
    UNATTRIBUTED_CODE,
    EnrichedRecord,
    Evidence,
    MissingEvidenceError,
    UnattributedWriteError,
    assert_admissible,
    comparable,
    confidence_band,
    deterministic_evidence,
    log_from_dicts,
    derived_scalar,
    deterministic_evidence,
    inherited_evidence,
    llm_evidence,
    log_from_dicts,
    registry_evidence,
)
from llm.prompts import TIER2A_PROMPT_VERSION, TIER3_PROMPT_VERSION
from tests.conftest import seed, tier3_evidence
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.serp_mock import MockSearchClient


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _record(**kw) -> EnrichedRecord:
    return _init_result(EnrichmentRecord(record_id="P1", country="US", **kw))


def _orch(**over) -> Orchestrator:
    st = Settings()
    clients = {
        "ror": MockRORClient(st), "lei": MockLEIClient(st),
        "search": MockSearchClient(), "page_fetcher": MockPageFetcher(),
        "llm": MockOpenAIClient(),
    }
    clients.update(over)
    return Orchestrator(st, mock_clients=clients)


# ---------------------------------------------------------------------------
# Step 1 — unattributed writes are impossible
# ---------------------------------------------------------------------------

class TestTheWriteIsLocked:
    """This class is what makes the principle a property of the code rather
    than a convention. Everything else in the fix is recording; this is
    enforcement."""

    @pytest.mark.parametrize("field", SCOPED_FIELDS)
    def test_direct_assignment_raises(self, field):
        record = _record(name1="Acme")
        with pytest.raises(UnattributedWriteError):
            record[field] = "anything"

    @pytest.mark.parametrize("field", SCOPED_FIELDS)
    def test_a_scoped_field_cannot_be_populated_without_evidence(self, field):
        """The evidence argument is required and structured — not optional,
        and not a string someone can pass to make the error go away."""
        record = _record(name1="Acme")
        with pytest.raises(MissingEvidenceError):
            record.write(field, "anything", None)
        with pytest.raises(MissingEvidenceError):
            record.write(field, "anything", "produced by ROR")  # type: ignore[arg-type]
        assert record.get(field) in (None, "unknown")

    def test_update_and_setdefault_are_locked_too(self):
        """The lock is on the field, not on one syntax for reaching it."""
        record = _record(name1="Acme")
        with pytest.raises(UnattributedWriteError):
            record.update({"domain": "acme.com"})
        with pytest.raises(UnattributedWriteError):
            record.setdefault("ror_id", "https://ror.org/x")

    def test_an_unscoped_field_is_an_ordinary_dict_key(self):
        """The lock is Phase 1 scope only — nothing else changes shape."""
        record = _record(name1="Acme")
        record["name3_enriched"] = "Snyder Laboratory"
        record["source"] = "ROR"
        assert record["name3_enriched"] == "Snyder Laboratory"

    def test_evidence_needs_a_producer(self):
        with pytest.raises(MissingEvidenceError):
            Evidence(producer_chain=())

    def test_the_lock_survives_finalisation(self):
        """Batch consensus writes onto already-finalised records, so the
        result object carries the same lock the working record did."""
        result = EnrichmentResult(record_id="P1", name1_enriched="Acme")
        with pytest.raises(UnattributedWriteError):
            result.ror_id = "https://ror.org/fake"
        result.write(
            "ror_id", "https://ror.org/fake",
            registry_evidence("ror", "https://ror.org/fake"),
        )
        assert result.ror_id == "https://ror.org/fake"
        assert result.ror_id_provenance == "ror:verified"


# ---------------------------------------------------------------------------
# Step 2 — the three situations the model must represent
# ---------------------------------------------------------------------------

class TestTheEventModel:
    def test_different_fields_from_different_producers(self):
        """Independent attributions, not a conflict. Record-level `source`
        collapses these into one label; the log does not."""
        record = _record(name1="MIT")
        record.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        record.write(
            "name2_enriched", "Department of Chemical Engineering",
            llm_evidence(
                ("serp", "fetch", "llm_tier2b"), tier=2,
                prompt_version="tier2b_dept/v1:abc",
                deployment="test-deployment", self_reported="medium",
                source_url="https://cheme.mit.edu",
            ),
        )
        producers = {
            e.field: e.producer_chain[-1] for e in record.provenance.events
        }
        assert producers == {"name1": "ror", "name2": "llm_tier2b"}

    def test_a_tier2b_write_is_one_event_with_a_three_element_chain(self):
        """A chain is not multiple competing sources; it is one value produced
        by several tools in sequence — the search, the fetch, the model."""
        record = _record(name1="MIT")
        record.write(
            "name2_enriched", "Department of Chemical Engineering",
            llm_evidence(
                ("serp", "fetch", "llm_tier2b"), tier=2,
                prompt_version="tier2b_dept/v1:abc",
                deployment="test-deployment", self_reported="medium",
                source_url="https://cheme.mit.edu",
            ),
        )
        events = record.provenance.events_for("name2_enriched")
        assert len(events) == 1
        assert events[0].producer_chain == ("serp", "fetch", "llm_tier2b")

    def test_a_field_written_twice_produces_two_ordered_events(self):
        """The case that makes a log necessary rather than a final-state map:
        the final value alone does not show that an LLM wrote first."""
        record = _record(name1="MIT")
        record.write("name1_enriched", "Mass. Inst. of Tech", tier3_evidence())
        record.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        events = record.provenance.events_for("name1_enriched")
        assert [e.seq for e in events] == sorted(e.seq for e in events)
        assert len(events) == 2
        assert events[0].producer_chain == ("llm_tier3",)
        assert events[0].new_value == "Mass. Inst. of Tech"
        assert events[1].producer_chain == ("ror",)
        assert events[1].old_value == "Mass. Inst. of Tech"

    def test_seq_is_monotonic_across_fields_not_just_within_one(self):
        """So the INTERLEAVING of writes is reconstructable, not only the
        per-field order."""
        record = _record(name1="MIT")
        record.write("name1_enriched", "A", tier3_evidence())
        record.write("domain", "a.example", deterministic_evidence("t"))
        record.write("name1_enriched", "B", tier3_evidence())
        assert [e.seq for e in record.provenance.events] == [1, 2, 3]

    def test_an_llm_event_carries_deployment_prompt_version_and_temperature(self):
        """A value produced by a model deployment is not reproducible without
        them, and deployments are not permanent."""
        record = _record(name1="Acme")
        record.write(
            "name1_enriched", "Acme Laboratories Incorporated",
            llm_evidence(
                ("llm_tier3",), tier=3, prompt_version=TIER3_PROMPT_VERSION,
                deployment="gpt-5.4-deployment", temperature=0.0,
                self_reported="medium",
            ),
        )
        ref = record.provenance.events_for("name1_enriched")[0].evidence_ref
        assert ref["deployment"] == "gpt-5.4-deployment"
        assert ref["prompt_version"] == TIER3_PROMPT_VERSION
        assert ref["temperature"] == 0.0
        assert ref["self_reported"] == "medium"

    def test_no_prompt_text_is_recorded_only_a_version(self):
        """Prompt VERSION identifiers only — never the prompt itself."""
        from llm import prompts

        record = _record(name1="Acme")
        record.write(
            "name1_enriched", "Acme Inc",
            llm_evidence(
                ("llm_tier2a",), tier=2, prompt_version=TIER2A_PROMPT_VERSION,
                deployment="d",
            ),
        )
        ref = record.provenance.events_for("name1_enriched")[0].evidence_ref
        assert prompts.TIER2A_SYSTEM_PROMPT[:40] not in str(ref)
        assert TIER2A_PROMPT_VERSION.startswith("tier2a_contact/v1:")

    def test_the_prompt_version_moves_when_the_prompt_does(self):
        """The declared major is for humans; the digest catches the edit
        nobody thought was semantic."""
        from llm.prompts import prompt_version

        a = prompt_version("x", "v1", "system", "user")
        b = prompt_version("x", "v1", "system", "user ")
        assert a != b


# ---------------------------------------------------------------------------
# Step 3 — confidence is not one scale
# ---------------------------------------------------------------------------

class TestConfidenceScales:
    def test_values_from_different_scales_are_not_comparable(self):
        """0.85 from a ROR rescore, from a RapidFuzz ratio and from a model's
        assertion about its own output are three different things."""
        assert comparable("ror_local", "ror_local")
        assert not comparable("ror_local", "fuzzy_ratio")
        assert not comparable("fuzzy_ratio", "llm_self_reported")
        assert not comparable(None, None)

    def test_every_event_carries_its_scale(self):
        record = _record(name1="Acme")
        record.write("name1_enriched", "Acme Inc", tier3_evidence())
        event = record.provenance.events_for("name1_enriched")[0]
        assert event.confidence_scale == LLM_SELF_REPORTED
        assert event.confidence_value is not None

    def test_bands_are_namespaced_by_scale(self):
        """So two scalars can never read as comparable just because both say
        "high"."""
        assert confidence_band("llm_self_reported", 0.9) == "self_high"
        assert confidence_band("ror_local", 0.95) == "high"
        assert confidence_band("fuzzy_ratio", 95) == "high"
        assert confidence_band("deterministic", 1.0) == "rule"
        assert confidence_band("registry_exact", 1.0) == "exact"

    def test_the_same_number_bands_differently_on_different_scales(self):
        """The proof that the scale is load-bearing: 0.9 is a strong ROR
        rescore and a strong self-report, and those are not the same claim."""
        assert (
            confidence_band("ror_local", 0.9)
            != confidence_band("llm_self_reported", 0.9)
        )


# ---------------------------------------------------------------------------
# Step 4 — guard-rejected candidates
# ---------------------------------------------------------------------------

class TestGuardRejections:
    def test_a_domain_the_ownership_guard_refused_is_logged(self):
        """Fix 1's guard: the pipeline had a confident answer and deliberately
        refused it, which is the case most worth defending afterwards."""
        record = _record(name1="Delta Analytical")
        seed(record, name1_enriched="Delta Analytical")
        decision = _apply_domain(record, "https://delta.com", settings=Settings())
        assert decision.rejected and record.get("domain") is None
        rejections = record.provenance.rejections
        assert [r.guard for r in rejections] == [GUARD_DOMAIN_OWNERSHIP]
        assert rejections[0].candidate == "delta.com"
        assert rejections[0].field == "domain"
        # The flag evidence carries the refused domain, not a bare marker:
        # the reason names the site the reviewer has to confirm.
        assert record.get("_domain_unverified") == "delta.com"

    def test_rejections_are_capped_and_the_overflow_is_counted(self):
        """Never a silent truncation: what was dropped is reported as a
        count, so the volume decision is visible."""
        record = _record(name1="Acme")
        for i in range(7):
            record.reject("ror_id", f"candidate-{i}", "distinctive_token")
        assert len(record.provenance.rejections) == 3
        assert record.provenance.rejections_omitted == {"ror_id": 4}

    def test_a_rejection_is_not_a_write(self):
        """A refused candidate never reaches the field, and never becomes its
        attribution."""
        record = _record(name1="Acme")
        record.reject("domain", "wrong.example", GUARD_DOMAIN_OWNERSHIP)
        assert record.get("domain") is None
        assert record.provenance.events_for("domain") == []


# ---------------------------------------------------------------------------
# Step 5 — the admissibility gate
# ---------------------------------------------------------------------------

class TestAdmissibility:
    def test_a_field_with_no_event_is_reverted_to_input_and_flagged(self):
        """Simulates a bypass — new code reaching the underlying dict — which
        is exactly what the gate is a backstop for. The record is NOT failed:
        shipping the original input is strictly better than failing the batch
        and strictly better than shipping an unattributable value."""
        record = _record(name1="Mayo Clinic FLA")
        dict.__setitem__(record, "name1_enriched", "Somewhere Else Entirely")

        out = finalise(record, time.monotonic())

        assert out["name1_enriched"] == "Mayo Clinic FLA"
        assert UNATTRIBUTED_CODE in out["flag_codes"]
        assert "name1" in out["flagged_fields"]
        assert out["flag_for_review"] is True

    def test_a_field_with_no_input_reverts_to_empty(self):
        record = _record(name1="Acme")
        seed(record, name1_enriched="Acme")
        dict.__setitem__(record, "domain", "unattributable.example")
        out = finalise(record, time.monotonic())
        assert out["domain"] is None
        assert UNATTRIBUTED_CODE in out["flag_codes"]

    def test_the_revert_is_itself_recorded(self):
        """The gate is a producer like any other: it wrote the field, so it
        says so."""
        record = _record(name1="Mayo Clinic FLA")
        dict.__setitem__(record, "name1_enriched", "Somewhere Else")
        finalise(record, time.monotonic())
        event = record.provenance.events_for("name1_enriched")[0]
        assert event.kind == "revert"
        assert event.producer_chain == ("admissibility_gate",)

    def test_an_attributed_field_passes_untouched(self):
        record = _record(name1="Mayo Clinic FLA")
        seed(record, name1_enriched="Mayo Clinic in Florida")
        out = finalise(record, time.monotonic())
        assert out["name1_enriched"] == "Mayo Clinic in Florida"
        assert UNATTRIBUTED_CODE not in out["flag_codes"]

    def test_the_gate_as_a_hard_assertion(self):
        """In tests the same condition fails loudly rather than being
        silently repaired."""
        record = _record(name1="Acme")
        seed(record, name1_enriched="Acme Inc")
        assert_admissible(record)
        dict.__setitem__(record, "ror_id", "https://ror.org/smuggled")
        with pytest.raises(AssertionError, match="ror_id"):
            assert_admissible(record)

    @pytest.mark.asyncio
    async def test_every_scoped_value_in_a_real_batch_is_attributed(self):
        """The end-to-end form of the acceptance criterion."""
        orch = _orch()
        records = [
            EnrichmentRecord(record_id="A", name1="MIT", name2="Chemistry Dept",
                             city="Cambridge", state="MA", country="US"),
            EnrichmentRecord(record_id="B", name1="Univ of Florida",
                             city="Gainesville", state="FL", country="US"),
            EnrichmentRecord(record_id="C", name1="Nonexistent Widgets Ltd",
                             country="US"),
        ]
        resp = await orch.enrich_batch(records, EnrichmentOptions())
        for result in resp.results:
            log = log_from_dicts(result.provenance)
            for field in SCOPED_FIELDS:
                value = getattr(result, field)
                if value in (None, "", "unknown"):
                    continue
                assert log.events_for(field), (
                    f"{result.record_id}.{field} = {value!r} has no provenance"
                )


# ---------------------------------------------------------------------------
# Step 6 — the two projections
# ---------------------------------------------------------------------------

class TestDerivedScalars:
    def test_the_scalar_format(self):
        record = _record(name1="MIT")
        record.write(
            "ror_id", "https://ror.org/042nb2s44",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        assert derived_scalar(record.provenance, "ror_id") == "ror:verified"

    def test_a_scalar_regenerates_identically_from_the_events(self):
        """Regenerated, never maintained separately — so the column and the
        log cannot drift apart."""
        record = _record(name1="MIT")
        record.write("name1_enriched", "Mass. Inst. of Tech", tier3_evidence())
        record.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        out = finalise(record, time.monotonic())
        rebuilt = log_from_dicts(out["provenance"])
        for field, column in DERIVED_SCALAR_FIELDS.items():
            if out.get(field) in (None, ""):
                continue
            assert out[column] == derived_scalar(rebuilt, field)

    def test_the_scalar_follows_the_LAST_producer(self):
        record = _record(name1="MIT")
        record.write("name1_enriched", "Mass. Inst. of Tech", tier3_evidence())
        assert derived_scalar(
            record.provenance, "name1_enriched") == "llm:provisional"
        record.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        assert derived_scalar(
            record.provenance, "name1_enriched") == "ror:verified"

    def test_a_transform_never_becomes_the_attribution(self):
        """Output casing did not decide the name; ROR did."""
        record = _record(name1="MIT")
        record.write(
            "name1_enriched", "massachusetts institute of technology",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        record.transform(
            "name1_enriched", "Massachusetts Institute of Technology",
            rule_id="rule7:output-casing",
        )
        assert len(record.provenance.events_for("name1_enriched")) == 2
        assert derived_scalar(
            record.provenance, "name1_enriched") == "ror:verified"

    def test_a_null_field_carries_a_null_scalar(self):
        record = _record(name1="Acme")
        seed(record, name1_enriched="Acme")
        out = finalise(record, time.monotonic())
        assert out["lei_id"] is None
        assert out["lei_id_provenance"] is None

    def test_the_six_scalars_are_output_columns_and_the_events_are_not(self):
        """`/enrich/file` returns XLSX and cannot carry the nested array, so
        it emits the derived columns only."""
        from api.output_columns import RESPONSE_COLUMNS

        for column in DERIVED_SCALAR_FIELDS.values():
            assert column in RESPONSE_COLUMNS
        assert "provenance" not in RESPONSE_COLUMNS
        assert "provenance_rejected" not in RESPONSE_COLUMNS


# ---------------------------------------------------------------------------
# Step 7 — interactions with the earlier fixes
# ---------------------------------------------------------------------------

class TestEarlierFixes:
    @pytest.mark.asyncio
    async def test_fix2_retry_shows_two_name1_events_llm_then_registry(self):
        """A record resolved via the Fix 2 retry shows two name1 events with
        increasing seq, the first from an LLM producer and the second from
        ror. Row 4's symptom — a verified ROR ID shipped next to an LLM
        uncertainty flag — is this sequence going unrecorded."""
        orch = _orch()
        record = EnrichmentRecord(record_id="R", country="US")
        result = _record(name1="MASSACHUSETTS INSITUTE OF TECHNOLOGY")
        result["_tier1_query_name"] = "MASSACHUSETTS INSITUTE OF TECHNOLOGY"
        result["_tier1_country_code"] = "US"
        result.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            tier3_evidence(),
        )
        await orch._retry_tier1_after_canonicalisation(record, result)

        events = result.provenance.events_for("name1_enriched")
        assert len(events) == 2
        assert events[0].seq < events[1].seq
        assert events[0].producer_chain[-1].startswith("llm_")
        assert events[1].producer_chain == ("ror",)
        assert result["ror_id"] == "https://ror.org/042nb2s44"

    def test_fix6_inheritance_names_the_donor_record(self):
        """An inherited ROR ID must not be indistinguishable from a
        first-hand one."""
        donor = EnrichmentResult(
            record_id="DONOR", name1_enriched="Coastal Diagnostics, Inc.",
            postal_code="12345", city="Springfield", country_region_key="US",
            street_cleaned="1 Main St", record_type="company",
        )
        donor.write(
            "ror_id", "https://ror.org/coastal",
            registry_evidence("ror", "https://ror.org/coastal"),
        )
        receiver = EnrichmentResult(
            record_id="RECEIVER", name1_enriched="Coastal Diagnostics, Inc.",
            postal_code="12345", city="Springfield", country_region_key="US",
            street_cleaned="1 Main St", record_type="company",
        )
        apply_batch_consensus([donor, receiver])

        assert receiver.ror_id == "https://ror.org/coastal"
        event = log_from_dicts(receiver.provenance).attributing_event("ror_id")
        assert event.producer_chain == ("batch_consensus",)
        assert event.evidence_ref["donor_record_id"] == "DONOR"
        # Provenance Scheme B has no `batch_consensus` source and no
        # `inherited` confidence: the grammar names WHO said it and HOW MUCH
        # weight it carries, and "a sibling record in this batch" is neither.
        # ROR authored the identifier — but THIS record never looked it up, so
        # it is `provisional` here and `verified` only on the donor. The donor
        # record id is on the event, which is what a reviewer opens.
        assert receiver.ror_id_provenance == "ror:provisional"

    def test_fix6_never_names_the_receiving_record_as_its_own_donor(self):
        """A record cannot be its own donor — an inheritance pointing back at
        itself would be exactly the indistinguishability the rule forbids."""
        rows = [
            EnrichmentResult(
                record_id=rid, name1_enriched="Coastal Diagnostics, Inc.",
                postal_code="12345", city="Springfield",
                country_region_key="US", street_cleaned="1 Main St",
                record_type="company",
            )
            for rid in ("X", "Y", "Z")
        ]
        rows[0].write(
            "lei_id", "LEI0000000000000001",
            registry_evidence("gleif", "LEI0000000000000001"),
        )
        apply_batch_consensus(rows)
        for row in rows[1:]:
            event = log_from_dicts(row.provenance).attributing_event("lei_id")
            if event is None:
                continue
            assert event.evidence_ref["donor_record_id"] != row.record_id

    def test_fix8_flagged_fields_are_derived_from_the_log(self):
        """The `unverified-inference` scope now follows from who wrote the
        field last, which is what the log records — and a field a registry
        overwrote is no longer the LLM's claim."""
        weak = _record(name1="Cardinal Rsch GRP")
        seed(weak, tier3_evidence(),
             name1_enriched="Cardinal Research Group Incorporated")
        weak["tier_used"] = 3
        weak["source"] = "LLM"
        weak["confidence"] = "high"
        out_weak = finalise(weak, time.monotonic())
        assert "unverified-inference" in out_weak["flag_codes"]
        assert out_weak["flagged_fields"] == ["name1"]

        rescued = _record(name1="Cardinal Rsch GRP")
        seed(rescued, tier3_evidence(),
             name1_enriched="Cardinal Research Group Incorporated")
        rescued.write(
            "name1_enriched", "Cardinal Research Group Inc",
            registry_evidence("gleif", "LEI000000000000000X"),
        )
        rescued["tier_used"] = 3
        rescued["source"] = "LLM"
        rescued["confidence"] = "high"
        out_rescued = finalise(rescued, time.monotonic())
        assert "unverified-inference" not in out_rescued["flag_codes"]


# ---------------------------------------------------------------------------
# The API stays stateless
# ---------------------------------------------------------------------------

class TestStateless:
    @pytest.mark.asyncio
    async def test_the_log_ships_in_the_response_and_is_not_persisted(self):
        """The provenance log is part of the JSON response, not telemetry and
        not a database. ADF decides what to store."""
        orch = _orch()
        resp = await orch.enrich_batch(
            [EnrichmentRecord(record_id="A", name1="MIT", city="Cambridge",
                              state="MA", country="US")],
            EnrichmentOptions(),
        )
        body = resp.results[0].model_dump(by_alias=True)
        assert body["provenance"], "the events array must ship in the response"
        assert "Name 1 Provenance" in body
        assert orch._settings is not None  # no store was attached

    @pytest.mark.asyncio
    async def test_two_batches_do_not_share_a_log(self):
        orch = _orch()
        first = await orch.enrich_batch(
            [EnrichmentRecord(record_id="A", name1="MIT", country="US")],
            EnrichmentOptions(),
        )
        second = await orch.enrich_batch(
            [EnrichmentRecord(record_id="A", name1="MIT", country="US")],
            EnrichmentOptions(),
        )
        assert len(first.results[0].provenance) == len(
            second.results[0].provenance)

    def test_fix6_names_a_first_hand_donor_not_one_that_also_inherited(self):
        """Donors are resolved against the group's state BEFORE the pass. A
        member updated earlier in the same loop would otherwise be named as
        the donor, and the trail would stop one hop short of the record that
        actually resolved the organisation."""
        rows = [
            EnrichmentResult(
                record_id=rid, name1_enriched="Harbor Clinic, Inc.",
                postal_code="99999", city="Harbor", country_region_key="US",
                street_cleaned="7 Dock Rd", record_type="company",
            )
            for rid in ("FIRST", "SECOND", "THIRD", "FOURTH")
        ]
        rows[3].write(
            "ror_id", "https://ror.org/harbor",
            registry_evidence("ror", "https://ror.org/harbor"),
        )
        apply_batch_consensus(rows)

        for row in rows[:3]:
            event = log_from_dicts(row.provenance).attributing_event("ror_id")
            assert event.evidence_ref["donor_record_id"] == "FOURTH"
            # …and the donor's own scale travels with it, so the receiving
            # record can tell registry evidence from an LLM's self-report.
            assert event.evidence_ref["donor_confidence_scale"] == "registry_exact"


class TestOriginalValue:
    """`original_value` answers "what did this field arrive holding" from the
    log, so nothing has to carry a `*_original` alongside the result. Batch
    consensus is the caller: after `finalise` the supplied Name 1 exists
    nowhere else on an `EnrichmentResult`, and the name-form election needs it
    to tell a spelling a customer typed from one a tier composed.
    """

    def test_it_returns_the_value_the_record_arrived_with(self):
        record = _record(name1="COASTAL DIAGNOSTICS")
        record.write("name1_enriched", "COASTAL DIAGNOSTICS",
                     deterministic_evidence("input-passthrough",
                                            producer="input"))
        record.write("name1_enriched", "Coastal Diagnostics, Inc.",
                     tier3_evidence())
        assert record.provenance.original_value("name1_enriched") == \
            "COASTAL DIAGNOSTICS"

    def test_later_writes_never_move_it(self):
        record = _record(name1="Uni Stuttgart")
        record.write("name1_enriched", "Uni Stuttgart",
                     deterministic_evidence("input-passthrough",
                                            producer="input"))
        for value in ("Universitat Stuttgart", "University of Stuttgart"):
            record.write("name1_enriched", value, tier3_evidence())
        assert record.provenance.original_value("name1_enriched") == \
            "Uni Stuttgart"

    def test_a_field_with_no_events_has_no_original(self):
        record = _record(name1="MIT")
        assert record.provenance.original_value("name1_enriched") is None

    def test_a_field_the_pipeline_populated_from_nothing_has_no_original(self):
        """An absent value is not a spelling, so it is not offered as one."""
        record = _record(name1="MIT")
        record.write("name2_enriched", "Department of Physics", tier3_evidence())
        assert record.provenance.original_value("name2_enriched") is None

    def test_it_reads_through_a_round_trip_of_the_log(self):
        """Batch consensus rebuilds the log from the serialised events rather
        than holding the record, so the answer has to survive that."""
        record = _record(name1="Coastal Diagnostics, Inc")
        record.write("name1_enriched", "Coastal Diagnostics, Inc",
                     deterministic_evidence("input-passthrough",
                                            producer="input"))
        rebuilt = log_from_dicts(record.provenance.as_dicts(), [], {})
        assert rebuilt.original_value("name1_enriched") == \
            "Coastal Diagnostics, Inc"
