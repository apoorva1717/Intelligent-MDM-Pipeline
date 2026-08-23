"""Fix 2: the three states an unchanged Name 1 can be in.

The defect these tests pin shut is not "the flag was wrong" but "the same
evidence produced two different outcomes". Before the split, whether a retained
Name 1 was flagged depended on which branch reached the passthrough — Tier 3's
per-slot marker and the ROR-miss research path set it, the company branch's
failed-canonicalisation path did not — so on the chemspeed batch four records
with an ownership-guard-verified domain passed silently while records with the
same evidence class were flagged.

So the properties under test are: each state is produced by its own condition
and only by it; the state is visible in provenance as well as in the flag; the
reason text for the flagged state is unchanged to the byte; and no state ever
alters the value of Name 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.flags import LOW_CONFIDENCE_UNCHANGED, _REASONS
from enrichment.orchestrator import finalise
from enrichment.provenance import (
    INPUT_CORROBORATED,
    INPUT_SELF_CONSISTENT,
    deterministic_evidence,
    llm_evidence,
)
from enrichment.unchanged_state import (
    UNCHANGED_CONFIRMED,
    UNCHANGED_UNRESOLVED,
    UNCHANGED_VERIFIED,
    resolve,
)
from llm.prompts import COMPANY_CANONICAL_PROMPT_VERSION
from tests.conftest import make_record

#: The reason text a reviewer sees for the flagged state. Copied here as a
#: literal on purpose: Fix 2 must not re-word it, and a test that read the
#: string from the module it is guarding could not detect a re-wording.
UNRESOLVED_REASON = (
    "Name 1: left exactly as supplied — the canonical form could not be "
    "established with enough confidence to rewrite it; confirm the value "
    "is correct"
)


def _kept(name1: str = "Aixelo Inc", **fields):
    """A record whose Name 1 the pipeline kept, Tier 1 having been queried."""
    record = make_record(
        record_id="R1",
        name1_original=name1,
        country_region_key="US",
        **{k: v for k, v in fields.items() if not k.startswith("_")},
    )
    record.write(
        "name1_enriched", name1,
        deterministic_evidence(
            "tier2:company-canonical-failed-passthrough",
            producer="input", tier=1,
        ),
    )
    record["_tier1_query_name"] = name1
    for key, value in fields.items():
        if key.startswith("_"):
            record[key] = value
    return record


def _run(record):
    return finalise(record, 0.0)


# ---------------------------------------------------------------------------
# Each state comes from its own condition
# ---------------------------------------------------------------------------

class TestTheThreeStates:
    def test_domain_tied_to_name1_by_the_ownership_guard_is_verified(self):
        """The regression anchor. `Advanced Composites Inc` +
        advancedcomposites.com passed the guard on name similarity, and the
        record shipped unflagged. Fix 2 must keep it unflagged and say why."""
        record = _kept("Advanced Composites Inc")
        record.write(
            "domain", "advancedcomposites.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        record["domain_verified_by"] = "name"

        outcome = resolve(record)
        assert outcome.state == UNCHANGED_VERIFIED
        assert outcome.evidence == "domain:name"
        assert outcome.flagged is False

        out = _run(record)
        assert LOW_CONFIDENCE_UNCHANGED not in out["flag_codes"]
        assert out["name1_provenance"] == "input:1:verified"

    def test_on_domain_search_evidence_also_verifies(self):
        """`Aixelo Inc` cleared the guard on condition 4, not on name
        similarity — every significant Name-1 token appeared in the title of a
        result on that very domain. That ties the site to the name just as
        firmly, so it is the same state."""
        record = _kept("Aixelo Inc")
        record.write(
            "domain", "aixelo.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        record["domain_verified_by"] = "serp"

        assert resolve(record).state == UNCHANGED_VERIFIED
        assert _run(record)["name1_provenance"] == "input:1:verified"

    def test_email_verified_domain_does_not_corroborate_the_name(self):
        """A non-generic address says which organisation the RECORD belongs
        to. It says nothing about whether the Name 1 string is that
        organisation's name — which is the question — so it is not evidence
        here even though it is enough to keep the domain."""
        record = _kept("Meridian Labs")
        record.write(
            "domain", "meridianlabs.com",
            deterministic_evidence("test", producer="record_email"),
        )
        record["domain_verified_by"] = "email"

        assert resolve(record).state == UNCHANGED_UNRESOLVED

    def test_canonical_proposal_equal_under_normalize_key_is_confirmed(self):
        """The model was asked what the organisation is called, was never shown
        the record's answer, and returned it. Punctuation-only difference —
        the same equality Stage 5 uses to decide a "correction" is not one."""
        record = _kept("Anresco Laboratories")
        record["_canonical_proposal"] = "Anresco Laboratories, Inc"

        # Not equal under normalize_key — "Inc" is a new token, not punctuation.
        assert resolve(record).state == UNCHANGED_UNRESOLVED

        record["_canonical_proposal"] = "ANRESCO, LABORATORIES"
        outcome = resolve(record)
        assert outcome.state == UNCHANGED_CONFIRMED
        assert outcome.evidence == "canonical_proposal"

        out = _run(record)
        assert LOW_CONFIDENCE_UNCHANGED not in out["flag_codes"]
        assert out["name1_provenance"] == "input:1:confirmed"

    def test_nothing_came_back_is_unresolved_and_flagged(self):
        record = _kept("Apollo Organic Synthesis")
        assert resolve(record).state == UNCHANGED_UNRESOLVED

        out = _run(record)
        assert LOW_CONFIDENCE_UNCHANGED in out["flag_codes"]
        assert "name1" in out["flagged_fields"]

    def test_evidence_outranks_a_second_opinion(self):
        """Both conditions hold. The domain wins, because a reviewer can open
        it and cannot open the model's agreement."""
        record = _kept("Aroma Creations Inc")
        record.write(
            "domain", "aromacreations.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        record["domain_verified_by"] = "name"
        record["_canonical_proposal"] = "Aroma Creations, Inc"

        assert resolve(record).state == UNCHANGED_VERIFIED


# ---------------------------------------------------------------------------
# What the split must NOT change
# ---------------------------------------------------------------------------

class TestNothingElseMoves:
    def test_flag_reason_for_unresolved_is_byte_identical(self):
        out = _run(_kept("Apollo Organic Synthesis"))
        assert out["flag_reason"] == UNRESOLVED_REASON
        assert _REASONS[LOW_CONFIDENCE_UNCHANGED] in out["flag_reason"]

    @pytest.mark.parametrize(
        "setup",
        [
            lambda r: r.update({"_canonical_proposal": "AIXELO INC"}),
            lambda r: r.update({"domain_verified_by": "name"}),
            lambda r: None,
        ],
    )
    def test_name1_value_is_never_altered_by_the_state(self, setup):
        """The states are a claim about the value, never a change to it."""
        record = _kept("Aixelo Inc")
        record.write(
            "domain", "aixelo.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        setup(record)
        assert _run(record)["name1_enriched"] == "Aixelo Inc"

    def test_a_rewritten_name1_is_not_one_of_the_three_states(self):
        record = make_record(
            record_id="R1", name1_original="ABGENT", country_region_key="US",
        )
        record.write(
            "name1_enriched", "Abgent, Inc.",
            llm_evidence(
                ("llm_company_canonical",), tier=2,
                prompt_version=COMPANY_CANONICAL_PROMPT_VERSION,
                deployment="test", self_reported="high",
            ),
        )
        record["_tier1_query_name"] = "ABGENT"
        assert resolve(record) is None
        assert _run(record)["name1_provenance"].startswith("llm_company_canonical")

    def test_a_stage0_short_circuit_is_not_classified(self):
        """UC 0 returns before Tier 1 is queried. "Nothing came back" would be
        a false account of a question that was never put, and the record
        already carries the structural code that IS actionable."""
        record = _kept("Adams Air")
        del record["_tier1_query_name"]
        assert resolve(record) is None

    def test_department_slots_keep_their_existing_behaviour(self):
        """The three states are about an organisation's identity. Name 2 has no
        corroborating evidence class, so its marker is untouched."""
        record = _kept("Apollo Organic Synthesis", name2_original="Chemistry")
        record.write(
            "name2_enriched", "Chemistry",
            deterministic_evidence("passthrough:input-retained", producer="input"),
        )
        record["_ev_low_conf_unchanged"] = {"name2"}

        out = _run(record)
        assert LOW_CONFIDENCE_UNCHANGED in out["flag_codes"]
        assert set(out["flagged_fields"]) >= {"name1", "name2"}

    def test_verified_row_does_not_fall_through_to_no_match(self):
        """Withdrawing `low-confidence-unchanged` must not let `no-match` —
        "no source could identify this organisation" — take its place on a row
        the pipeline just corroborated."""
        record = _kept("Advanced Composites Inc")
        record.write(
            "domain", "advancedcomposites.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        record["domain_verified_by"] = "name"

        out = _run(record)
        assert out["flag_codes"] == []
        assert out["flag_for_review"] is False


# ---------------------------------------------------------------------------
# The provenance projection
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_the_state_ships_as_the_band_of_the_derived_scalar(self):
        cases = {
            "input:1:verified": ("domain_verified_by", "name"),
            "input:1:confirmed": ("_canonical_proposal", "AIXELO, INC"),
            "input:1:rule": (None, None),
        }
        for expected, (key, value) in cases.items():
            record = _kept("Aixelo Inc")
            if key == "domain_verified_by":
                record.write(
                    "domain", "aixelo.com",
                    deterministic_evidence("t", producer="website_resolver"),
                )
            if key:
                record[key] = value
            assert _run(record)["name1_provenance"] == expected

    def test_the_corroborating_event_records_what_corroborated(self):
        record = _kept("Advanced Composites Inc")
        record.write(
            "domain", "advancedcomposites.com",
            deterministic_evidence("t", producer="website_resolver"),
        )
        record["domain_verified_by"] = "name"
        out = _run(record)

        event = [
            e for e in out["provenance"]
            if e["field"] == "name1" and e["confidence_scale"] == INPUT_CORROBORATED
        ][-1]
        assert event["rule_id"] == "fix2:unchanged-verified"
        assert event["evidence_ref"]["corroborated_by"] == "domain:name"
        assert event["evidence_ref"]["evidence_ref"] == "advancedcomposites.com"

    def test_the_confirming_event_names_the_proposal(self):
        record = _kept("Aixelo Inc")
        record["_canonical_proposal"] = "AIXELO, INC"
        out = _run(record)

        event = [
            e for e in out["provenance"]
            if e["field"] == "name1"
            and e["confidence_scale"] == INPUT_SELF_CONSISTENT
        ][-1]
        assert event["rule_id"] == "fix2:unchanged-confirmed"
        assert event["evidence_ref"]["proposal"] == "AIXELO, INC"
        # The value stays the record's own string, not the model's punctuation.
        assert event["new_value"] == "Aixelo Inc"
