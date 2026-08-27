"""Fix 8: the review flag is a triage signal, rebuilt from final state.

Before this fix, 47 of the 50 demo records were flagged, the reason named the
tier that ran rather than the doubt that remained, and the flag was
record-level even when the doubt was about one field. These tests pin the
replacement model:

* flags are computed **once**, in ``finalise``, from what the record holds —
  never appended as tiers execute, so a record rescued by Fix 2's Tier 1 retry
  carries no trace of the tiers it passed through on the way;
* evidence-backed results are **not** flagged — a verified registry match, a
  department read off a page that ``source_url`` names, a deterministic
  normalisation, an absent department;
* the three output fields stay consistent: ``flag_for_review`` is true iff a
  code outside :data:`~enrichment.flags.ADVISORY_CODES` was raised or a core
  field landed at ``low`` confidence, ``flag_reason`` renders whatever codes
  there are either way, and ``flagged_fields`` scopes the doubt.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment import flags
from enrichment.orchestrator import Orchestrator, _init_result, finalise
from tests.conftest import seed, tier3_evidence
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.page_mock import MockPageFetcher


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _NoSearch:
    async def search(self, q, num_results=5, *, country=None):
        return []


class _EmptyLLM:
    async def extract_json(self, s, u, **k):
        return {}

    async def aclose(self):
        pass


class _StubROR:
    """ROR client resolving exactly the queries a test names."""

    def __init__(self, matches: dict[str, dict[str, Any]]) -> None:
        self.matches = matches
        self.queries: list[str] = []

    async def call(self, name, country_code=None, country=None,
                   city=None, state=None, **_ctx) -> dict[str, Any]:
        self.queries.append(name)
        hit = self.matches.get(name.strip().lower())
        if hit is None:
            return {"matched": False, "score": 0.0}
        return {"matched": True, "score": 1.0, **hit}


def _orch(mock_clients: dict[str, Any] | None = None) -> Orchestrator:
    st = Settings()
    clients: dict[str, Any] = {
        "lei": MockLEIClient(st),
        "search": _NoSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": _EmptyLLM(),
    }
    clients.update(mock_clients or {})
    return Orchestrator(st, mock_clients=clients)


def _finalised(record_kw: dict[str, Any] | None = None, **overrides: Any) -> dict:
    """Run ``finalise`` over a record in a stated post-tier state.

    Exercises the real finalisation path — passthrough restoration, the
    ``*_changed`` rules and ``compute_flags`` — rather than calling
    ``compute_flags`` on a hand-built dict, so the tests pin the flag a
    record actually ships with.
    """
    result = _init_result(EnrichmentRecord(
        record_id="F1", country="US", **(record_kw or {"name1": "Acme Labs"}),
    ))
    seed(result, **overrides)
    # A test that declares "Tier 3 wrote these fields" writes them as Tier 3:
    # the flag is derived from the provenance log, not from the marker.
    tier3_wrote = overrides.get("_ev_tier3_wrote") or ()
    for slot in tier3_wrote:
        field = f"{slot}_enriched"
        if field in overrides:
            seed(result, tier3_evidence(), **{field: overrides[field]})
    return finalise(result, time.monotonic())


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------

class _Flagged:
    """The attribute surface `flags.retract` needs, and nothing else — the
    real caller passes an `EnrichmentResult`, which is far more than these
    tests are about."""

    def __init__(self, rendered: dict[str, Any]) -> None:
        self.record_id = "t"
        for key, value in rendered.items():
            setattr(self, key, value)


def _queues_for_review(codes: Any, low: Any) -> bool:
    """The `flag_for_review` contract, stated once for the tests that check it.

    Not "there is a code": an advisory code states a finding without asking
    anyone to act on it, and a core field at `low` asks without carrying a
    code. See `enrichment.flags.render`.
    """
    return bool(set(codes or ()) - flags.ADVISORY_CODES) or bool(low)


class TestFlagFieldsStayConsistent:
    """``flag_for_review`` tracks the codes that actually ask for review."""

    @pytest.mark.parametrize("overrides", [
        # Clean verified match — no codes.
        {"name1_enriched": "Stanford University",
         "ror_id": "https://ror.org/00f54p054", "domain": "stanford.edu",
         "enrichment_status": "enriched"},
        # One condition.
        {"name1_enriched": "Acme Labs", "_ev_person_unresolved": True},
        {"name1_enriched": "Acme Labs", "_ev_overflow": True},
        {"name1_enriched": "Acme Labs", "_domain_unverified": True},
        {"name1_enriched": "Acme Labs",
         "_domain_unverified": "meridianlabs.ai"},
        {"name1_enriched": "Acme Labs", "_ev_email_conflict": True},
        # Several at once.
        {"name1_enriched": "Acme Labs", "_ev_overflow": True,
         "_domain_unverified": True, "_ev_email_conflict": True},
        # Nothing at all — the total miss.
        {"name1_enriched": "Acme Labs"},
    ])
    def test_boolean_matches_the_codes_and_the_derived_low(self, overrides):
        """The authorised contract change. Before the provenance migration
        `flag_for_review` was true iff `flag_codes` was non-empty; now a core
        field at `low` raises it with no code attached, because
        `low-confidence-unchanged` was a code that existed only to restate
        what the field's confidence already said. It has since come apart in
        the other direction too — an `ADVISORY_CODES` code renders its prose
        without asking for review."""
        out = _finalised(**overrides)
        assert out["flag_for_review"] is _queues_for_review(
            out["flag_codes"], out["flag_low_confidence"],
        )

    @pytest.mark.asyncio
    async def test_invariant_holds_across_a_batch(self):
        records = [
            EnrichmentRecord(record_id="B1", name1="Stanford University",
                             city="Stanford", state="CA", country="US"),
            EnrichmentRecord(record_id="B2", name1="Dr. Jane Smith",
                             city="Boston", country="US"),
            EnrichmentRecord(record_id="B3", name1="Nonexistent Widgets Ltd",
                             country="US"),
        ]
        resp = await _orch().enrich_batch(
            records, EnrichmentOptions(max_concurrency=1),
        )
        for r in resp.results:
            assert r.flag_for_review is _queues_for_review(
                r.flag_codes, r.flag_low_confidence,
            )
            # Every code is part of the published vocabulary, and every
            # flagged field is one a reviewer can open.
            assert set(r.flag_codes) <= set(flags.ALL_CODES)
            assert set(r.flagged_fields) <= set(flags.FIELD_LABELS)
            # The prose tracks the CODES, not the queue membership: an
            # advisory-only row has a reason and no review request.
            assert bool(r.flag_reason) is (
                bool(r.flag_codes) or bool(r.flag_low_confidence)
            )
            # The retired token can never come back as a code.
            assert flags.LOW_CONFIDENCE_UNCHANGED not in r.flag_codes

    def test_codes_are_deduplicated_and_ordered(self):
        """Two conditions touching the same field yield two codes, once each,
        in the module's declared order."""
        out = _finalised(
            name1_enriched="Acme Labs",
            _ev_overflow=True, _domain_unverified=True,
        )
        assert out["flag_codes"] == ["overflow", "domain-unverified"]
        # A bare True is preprocessing's slots-full signal: it names no pair,
        # so the whole name block is the scope.
        assert out["flagged_fields"] == [
            "name1", "name2", "name3", "name4", "name5", "domain",
        ]

    def test_overflow_scopes_to_the_reported_pair(self):
        """UC 0 names the two slots whose contents ran together, and the flag
        points at exactly those — not at the whole block."""
        out = _finalised(
            name1_enriched="Acme Labs",
            _ev_overflow=["name3", "name4"],
        )
        assert out["flag_codes"] == ["overflow"]
        assert out["flagged_fields"] == ["name3", "name4"]


# ---------------------------------------------------------------------------
# Evidence-backed results are not flagged
# ---------------------------------------------------------------------------

class TestEvidenceIsNotFlagged:
    @pytest.mark.asyncio
    async def test_verified_tier1_match_produces_no_flag(self):
        """A ROR match that passed its verification guard ships the registry's
        name, id and domain. There is nothing for a reviewer to do."""
        ror = _StubROR({"stanford university": {
            "ror_id": "https://ror.org/00f54p054",
            "official_name": "Stanford University",
            "is_research_institution": True,
            "domain": "stanford.edu",
            "website": "https://www.stanford.edu",
            "children": [],
            "country": "United States",
        }})
        resp = await _orch({"ror": ror}).enrich_batch(
            [EnrichmentRecord(record_id="T1", name1="Stanford University",
                              city="Stanford", state="CA", country="US")],
            EnrichmentOptions(max_concurrency=1),
        )
        r = resp.results[0]
        assert r.ror_id == "https://ror.org/00f54p054"
        assert r.flag_for_review is False
        assert r.flag_codes == []
        assert r.flag_reason is None

    def test_tier2b_stated_department_with_a_source_url_is_not_flagged(self):
        """A department STATED on a page of the organisation's own domain is
        auditable: ``source_url`` names the page. This replaces the README's
        blanket "Tier 2B results are always flagged"."""
        out = _finalised(
            {"name1": "University of Michigan", "name2": "Radiation Oncology"},
            name1_enriched="University of Michigan",
            name2_enriched="Department of Radiation Oncology",
            tier_used=2, tier2_mode="2B", source="dept_search",
            confidence="medium", enrichment_status="enriched",
            source_url="https://medschool.umich.edu/departments/radiation-oncology",
            domain="umich.edu",
            department_domain="medschool.umich.edu",
        )
        assert out["flag_codes"] == []
        assert out["flag_for_review"] is False

    def test_a_research_institution_with_no_department_is_not_flagged(self):
        """8d. A record without a department is not a defect — the old rule
        fired on a fifth of the batch and gave a reviewer nothing to do."""
        out = _finalised(
            {"name1": "Stanford University"},
            name1_enriched="Stanford University",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            routing_type="research_institution",
            enrichment_status="enriched", confidence="high",
            _has_dept_signal=False, _multi_contact=False,
        )
        assert out["flag_codes"] == []

    def test_an_empty_field_that_stayed_empty_is_not_flagged(self):
        """8e. Nothing was dropped: the input Name 2 was blank."""
        out = _finalised(
            {"name1": "Stanford University", "name2": None},
            name1_enriched="Stanford University",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            enrichment_status="enriched",
            _ev_low_conf_unchanged={"name2", "name3"},
        )
        assert out["name2_enriched"] is None
        assert "name2" not in out["flagged_fields"]
        assert out["flag_codes"] == []

    def test_case_only_normalisation_is_not_flagged(self):
        """Casing, abbreviation expansion and unit canonicalisation are
        deterministic — they change no content and raise no doubt."""
        out = _finalised(
            {"name1": "STANFORD UNIVERSITY"},
            name1_enriched="Stanford University",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            enrichment_status="enriched",
        )
        assert out["name1_changed"] is False
        assert out["flag_codes"] == []


# ---------------------------------------------------------------------------
# What IS flagged
# ---------------------------------------------------------------------------

class TestFlaggedConditions:
    @pytest.mark.asyncio
    async def test_uc13_inferred_parent_department_is_dept_via_lab(
        self, test_settings, mock_clients,
    ):
        """UC 13 read the parent department off the *lab's* page rather than
        from a stated department, so the claim needs checking — and the doubt
        is about the department slots, not the institution."""
        resp = await Orchestrator(
            test_settings, mock_clients=mock_clients,
        ).enrich_batch(
            [EnrichmentRecord(
                record_id="UC13", name1="Stanford University",
                name2="Smith Research Program", name3=None,
                city="Stanford", state="CA", country="US",
            )],
            EnrichmentOptions(max_concurrency=1),
        )
        r = resp.results[0]
        assert 13 in r.use_cases_triggered
        assert r.name2_enriched == "Department of Chemistry"
        assert r.name3_enriched == "Smith Research Program"
        assert "dept-via-lab" in r.flag_codes
        assert r.flagged_fields == ["name2", "name3"]
        assert "name1" not in r.flagged_fields

    def test_name3_not_demoted_when_name3_was_populated(self):
        out = _finalised(
            {"name1": "Stanford University", "name2": "Smith Lab",
             "name3": "Existing Group"},
            name1_enriched="Stanford University",
            name2_enriched="Department of Chemistry",
            name3_enriched="Existing Group",
            _ev_dept_via_lab=True, _ev_name3_not_demoted=True,
        )
        assert out["flag_codes"] == ["dept-via-lab", "name3-not-demoted"]

    def test_tier3_written_value_is_unverified_inference(self):
        """8f. A value Tier 3 wrote rests on the LLM's training data alone.
        Flagged regardless of the confidence it reported — a confident
        unverifiable claim is the more dangerous case."""
        out = _finalised(
            {"name1": "Cardinal Rsch GRP"},
            name1_enriched="Cardinal Research Group Incorporated",
            tier_used=3, source="LLM", confidence="high",
            _ev_tier3_wrote={"name1"},
        )
        assert out["flag_codes"] == ["unverified-inference"]
        assert out["flagged_fields"] == ["name1"]
        assert "confirm against an authoritative source" in out["flag_reason"]

    def test_a_corroborated_department_is_not_unverified_inference(self):
        """Row 37. Tier 3 named the department, and the department probe then
        located it on the organisation's own web presence. ``ROR ID`` settles
        Name 1 and ``department_domain`` settles Name 2 — both auditable
        columns, so nothing is left resting on the LLM alone."""
        out = _finalised(
            {"name1": "Yale Univ", "name2": None},
            name1_enriched="Yale University",
            name2_enriched="Department of Chemistry",
            ror_id="https://ror.org/03v76x132", domain="yale.edu",
            department_domain="chem.yale.edu",
            tier_used=3, source="LLM", confidence="high",
            _registry_name_fields={"name1"},
            _ev_tier3_wrote={"name2"},
        )
        assert out["department_domain"] == "https://chem.yale.edu"
        assert out["flag_codes"] == []

    def test_corroboration_does_not_clear_a_wording_doubt(self):
        """A department domain says the unit is real, not that the record
        spells it the way the institution does. Rows 14 / 38: Name 2 was left
        exactly as supplied, so the wording is still unconfirmed."""
        out = _finalised(
            {"name1": "Stanford Univ", "name2": "Department of Physics"},
            name1_enriched="Stanford University",
            name2_enriched="Department of Physics",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            department_domain="physics.stanford.edu",
            _registry_name_fields={"name1"},
            _ev_low_conf_unchanged={"name2"},
        )
        # Name 2 is a department slot, and the marker is the only thing that
        # can speak for it: there is no corroborating evidence class for a
        # unit name. The code is retired, so the doubt arrives as the derived
        # low instead — same field, same prose, no token.
        assert out["flag_codes"] == []
        assert out["flag_low_confidence"] == ["name2"]
        assert out["flag_for_review"] is True
        assert out["flagged_fields"] == ["name2"]

    def test_an_uncorroborated_tier3_department_is_still_flagged(self):
        """The same record without the probe's corroboration keeps the flag,
        scoped to Name 2 — Name 1 is settled by the registry."""
        out = _finalised(
            {"name1": "Yale Univ", "name2": None},
            name1_enriched="Yale University",
            name2_enriched="Department of Chemistry",
            ror_id="https://ror.org/03v76x132", domain="yale.edu",
            tier_used=3, source="LLM", confidence="high",
            _registry_name_fields={"name1"},
            _ev_tier3_wrote={"name2"},
        )
        assert out["flag_codes"] == ["unverified-inference"]
        assert out["flagged_fields"] == ["name2"]

    def test_tier3_that_changed_nothing_is_not_unverified_inference(self):
        """When Tier 3 leaves the value unchanged there is no new claim."""
        out = _finalised(
            {"name1": "Acme Labs"},
            name1_enriched="Acme Labs",
            tier_used=3, source="LLM", confidence="low",
            _ev_tier3_wrote={"name1"},
            _ev_low_conf_unchanged={"name1"},
        )
        assert "unverified-inference" not in out["flag_codes"]
        assert out["flag_codes"] == []
        assert out["flag_low_confidence"] == ["name1"]
        assert out["flag_for_review"] is True

    def test_the_derived_low_is_scoped_per_field(self):
        """Per field, because that is how the doubt is actually shaped: the
        institute name is not in question, the department spelling is."""
        out = _finalised(
            {"name1": "Some Unknown Institute", "name2": "Chemistry Bits"},
            name1_enriched="Some Unknown Institute",
            name2_enriched="Chemistry Bits",
            _ev_low_conf_unchanged={"name2"},
        )
        assert out["flag_codes"] == []
        assert out["flag_low_confidence"] == ["name2"]
        assert out["flagged_fields"] == ["name2"]

    def test_opaque_code_in_name1(self):
        """UC 10. Preprocessing clears a code out of Name 2-4 but never out of
        Name 1, so the record leaves with no organisation name at all."""
        out = _finalised(
            {"name1": "B800000070"},
            name1_enriched="B800000070",
        )
        assert "opaque-code" in out["flag_codes"]
        assert "name1" in out["flagged_fields"]

    def test_multiple_contacts_only_when_tier2a_could_not_act(self):
        blocked = _finalised(
            {"name1": "Stanford University", "contact": "Jane Smith and John Doe"},
            name1_enriched="Stanford University",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            enrichment_status="enriched",
            _multi_contact=True, contact_used=False,
        )
        assert blocked["flag_codes"] == ["multiple-contacts"]
        assert blocked["flagged_fields"] == ["name2", "contact"]

        acted = _finalised(
            {"name1": "Stanford University", "contact": "Jane Smith and John Doe"},
            name1_enriched="Stanford University",
            name2_enriched="Department of Chemistry",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            enrichment_status="enriched",
            _multi_contact=True, contact_used=True,
        )
        assert acted["flag_codes"] == []

    def test_no_match_when_nothing_was_enriched(self):
        out = _finalised(
            {"name1": "Nonexistent Widgets Ltd"},
            name1_enriched="Nonexistent Widgets Ltd",
        )
        assert out["flag_codes"] == ["no-match"]

    def test_no_match_yields_to_a_more_specific_code(self):
        """``no-match`` means "nothing to go on"; when there IS something
        specific to say, that is the actionable code and no-match would only
        add noise."""
        out = _finalised(
            {"name1": "Dr. Jane Smith"},
            _ev_person_unresolved=True,
        )
        assert out["flag_codes"] == ["person-unresolved"]

    def test_a_record_with_several_conditions_carries_several_codes(self):
        out = _finalised(
            {"name1": "Cardinal Rsch GRP", "name2": "Smith Lab",
             "email": "orders@cardinal.example"},
            name1_enriched="Cardinal Research Group",
            name2_enriched="Department of Chemistry",
            name3_enriched="Smith Lab",
            tier_used=3, source="LLM", confidence="medium",
            _ev_tier3_wrote={"name1"},
            _ev_dept_via_lab=True,
            _ev_email_conflict=True,
            _domain_unverified=True,
        )
        assert out["flag_codes"] == [
            "unverified-inference", "dept-via-lab", "email-conflict",
            "domain-unverified",
        ]
        assert out["flagged_fields"] == [
            "name1", "name2", "name3", "domain", "email",
        ]
        # One clause per code, each naming its own scope.
        assert out["flag_reason"].count(";") == 3


# ---------------------------------------------------------------------------
# Rebuilt from final state, not appended as tiers run
# ---------------------------------------------------------------------------

class TestRebuiltFromFinalState:
    def test_fix2_retry_clears_the_earlier_tier_flag(self):
        """8c. Tier 3 wrote the name, then Fix 2's Tier 1 retry resolved that
        name in ROR and overwrote it with the registry's official form. The
        record holds a verified identifier, so it carries no Tier 3 doubt."""
        out = _finalised(
            {"name1": "MASSACHUSETTS INSITUTE OF TECHNOLOGY"},
            name1_enriched="Massachusetts Institute of Technology",
            tier_used=1, source="ROR", confidence="high",
            enrichment_status="enriched",
            ror_id="https://ror.org/042nb2s44", domain="mit.edu",
            _ev_tier3_wrote={"name1"},
            _registry_name_fields={"name1"},
        )
        assert out["flag_codes"] == []
        assert out["flag_for_review"] is False

    def test_a_verified_identifier_beside_an_uncertain_department(self):
        """8b. Rows 12-14 / 36 / 38: Name 1 is settled by ROR and the doubt is
        entirely about Name 2. The scope is what tells a reviewer this is a
        one-field check."""
        out = _finalised(
            {"name1": "Stanford University", "name2": "Chem Dept Bits"},
            name1_enriched="Stanford University",
            name2_enriched="Chem Dept Bits",
            ror_id="https://ror.org/00f54p054", domain="stanford.edu",
            enrichment_status="enriched",
            _registry_name_fields={"name1"},
            _ev_low_conf_unchanged={"name2"},
        )
        assert out["flagged_fields"] == ["name2"]
        assert out["flag_reason"].startswith("Name 2:")

    def test_no_reason_names_a_tier_that_ran(self):
        """8c. The reason states the doubt, never the code path."""
        forbidden = ("tier", "llm", "ror", "gleif", "serp", "uc ")
        for code, prose in flags._REASONS.items():
            lowered = prose.lower()
            for token in forbidden:
                assert token not in lowered, (code, token)

    def test_evidence_keys_never_reach_the_output(self):
        out = _finalised(
            {"name1": "Acme Labs"},
            name1_enriched="Acme Labs",
            _ev_overflow=True, _ev_person_unresolved=True,
            _ev_dept_via_lab=True, _ev_name3_not_demoted=True,
            _ev_low_conf_unchanged={"name2"}, _ev_tier3_wrote={"name1"},
            _ev_email_conflict=True, _domain_unverified=True,
            _multi_contact=True, _has_dept_signal=False,
        )
        assert not [k for k in out if k.startswith("_")]


class TestAdvisoryCodesStateWithoutQueueing:
    """An `ADVISORY_CODES` code says something about a record without asking
    anyone to look at it.

    `registry-location-mismatch` is the case that motivated the split. A
    register holds the addresses of a legal ENTITY — GLEIF publishes two — and
    a large organisation operates from many more sites than that, so a
    contradicted registry address is the ordinary condition of a multi-site
    company rather than a defect. It is still worth stating, and stating it is
    all the code now does.
    """

    _DETAIL = "GLEIF states region PA; record says NC"

    def _rendered(self, *extra_codes: str) -> dict[str, Any]:
        scopes: dict[str, set[str]] = {flags.REGISTRY_LOCATION_MISMATCH: {"address"}}
        for code in extra_codes:
            scopes[code] = set()
        return flags.render(
            scopes, {flags.REGISTRY_LOCATION_MISMATCH: self._DETAIL}, {}, [],
        )

    def test_the_code_the_scope_and_the_prose_all_survive(self):
        """Only the boolean changed. A consumer reading any of the other
        three columns sees exactly what it saw before."""
        out = self._rendered()
        assert out["flag_codes"] == [flags.REGISTRY_LOCATION_MISMATCH]
        assert out["flagged_fields"] == ["address"]
        assert out["flag_scopes"] == {
            flags.REGISTRY_LOCATION_MISMATCH: ["address"],
        }
        assert self._DETAIL in out["flag_reason"]
        assert out["flag_reason"].startswith("Address:")

    def test_it_does_not_put_the_record_in_the_queue(self):
        assert self._rendered()["flag_for_review"] is False

    def test_a_populated_reason_with_the_boolean_false_is_a_valid_row(self):
        """The combination the old invariant made unreachable, asserted
        directly: this is now a state DATAshaper must expect."""
        out = self._rendered()
        assert out["flag_reason"] and out["flag_for_review"] is False

    def test_a_substantive_code_alongside_it_still_queues(self):
        """The advisory code suppresses nothing but itself — it is subtracted
        from the set that raises the boolean, not consulted as a veto."""
        out = self._rendered(flags.NO_MATCH)
        assert out["flag_for_review"] is True
        assert set(out["flag_codes"]) == {
            flags.NO_MATCH, flags.REGISTRY_LOCATION_MISMATCH,
        }

    def test_a_low_confidence_core_field_alongside_it_still_queues(self):
        """The other half of the derivation is untouched: `low` raises the
        boolean whether or not the only code present is advisory."""
        out = flags.render(
            {flags.REGISTRY_LOCATION_MISMATCH: {"address"}},
            {flags.REGISTRY_LOCATION_MISMATCH: self._DETAIL}, {}, ["name1"],
        )
        assert out["flag_for_review"] is True

    def test_every_advisory_code_is_a_real_code(self):
        """The set names codes from the published vocabulary — an entry that
        no site can raise would silently do nothing."""
        assert flags.ADVISORY_CODES <= set(flags.ALL_CODES)

    def test_not_every_code_is_advisory(self):
        """A guard on the obvious mistake: emptying the review queue by
        widening this set is not a change any test would otherwise catch."""
        assert set(flags.ALL_CODES) - flags.ADVISORY_CODES


class TestRenderAndRetract:
    """`render` is the one place the four flag columns are built, and
    `retract` is the only way a code leaves a record after `compute_flags`
    has run — the escape hatch for a batch-level pass that changes a field
    the per-record decision had already described. Both are pinned here at
    the module boundary; their one caller is tested in test_batch_consensus.
    """

    def test_compute_flags_publishes_the_scope_map_it_rendered_from(self):
        out = _finalised(
            {"name1": "Acme Labs", "name2": "Chemistry"},
            name1_enriched="Acme Labs", name2_enriched="Chemistry",
            _ev_low_conf_unchanged={"name2"}, _domain_unverified=True,
        )
        # The retired code is not in the scope map, because it is not a code.
        # The field it concerned is in `flag_low_confidence`, and
        # `flagged_fields` is still the union of both halves — which is what a
        # consumer of that column actually reads.
        assert out["flag_scopes"] == {flags.DOMAIN_UNVERIFIED: ["domain"]}
        assert out["flag_low_confidence"] == ["name2"]
        assert out["flagged_fields"] == ["name2", "domain"]

    def test_render_emits_the_four_columns_consistently(self):
        out = flags.render({flags.DOMAIN_UNVERIFIED: {"domain"}})
        assert out["flag_codes"] == [flags.DOMAIN_UNVERIFIED]
        assert out["flagged_fields"] == ["domain"]
        assert out["flag_reason"].startswith("Domain:")
        # Consistent does not mean identical: `domain-unverified` is advisory,
        # so the three descriptive columns are populated and the queue column
        # is not. That IS the contract — see `flags.ADVISORY_CODES`.
        assert out["flag_for_review"] is False

    def test_render_of_nothing_is_the_unflagged_record(self):
        # `flag_notes` joined `flag_details` with Fix 3 — a second internal
        # map, empty for the same reason: nothing was raised, so nothing was
        # detailed and nothing was annotated.
        # `flag_low_confidence` joined them with the provenance migration — a
        # third internal list, empty for the same reason: nothing was raised,
        # so no field is in doubt and none is named.
        assert flags.render({}) == {
            "flag_codes": [], "flagged_fields": [],
            "flag_for_review": False, "flag_reason": None, "flag_scopes": {},
            "flag_details": {}, "flag_notes": {}, "flag_low_confidence": [],
        }

    def test_render_orders_codes_by_emission_order_not_input_order(self):
        out = flags.render({
            flags.DOMAIN_UNVERIFIED: {"domain"},
            flags.OVERFLOW: {"name1", "name2"},
        })
        assert out["flag_codes"] == [flags.OVERFLOW, flags.DOMAIN_UNVERIFIED]
        assert out["flag_reason"].startswith("Name 1 and Name 2:")

    def test_a_record_level_code_renders_without_a_scope_clause(self):
        out = flags.render({flags.OVERFLOW: set()})
        assert out["flagged_fields"] == []
        assert out["flag_reason"] == flags._REASONS[flags.OVERFLOW]

    def test_retract_drops_the_derived_low_and_re_renders(self):
        """The derived low is withdrawn the way a code is, and reported under
        the retired code's name — the STATEMENT withdrawn is the one that code
        used to make, and its prose is what was rendered, so a telemetry line
        saying anything else would describe a different withdrawal."""
        result = _Flagged(flags.render(
            {flags.DOMAIN_UNVERIFIED: {"domain"}},
            low_confidence=["name1"],
        ))
        assert "left exactly as supplied" in result.flag_reason
        assert flags.retract(result, [], "name1") == (
            flags.LOW_CONFIDENCE_UNCHANGED,
        )
        assert result.flag_codes == [flags.DOMAIN_UNVERIFIED]
        assert result.flagged_fields == ["domain"]
        assert result.flag_low_confidence == []
        # The derived low was the only thing asking for review; withdrawing it
        # leaves an advisory code, which does not. The code and its prose
        # survive the retraction unchanged, which is what this test is about.
        assert result.flag_for_review is False
        assert "left exactly as supplied" not in result.flag_reason
        assert result.flag_reason.startswith("Domain:")

    def test_render_names_the_unverified_domain_in_the_reason(self):
        """The domain is in the column, and the reason still names it: a
        reviewer reading `Flag Reason` in a filtered export should not have to
        go and find which value the sentence is about. It describes a value
        that is PRESENT — no "before using it", because it is already there."""
        out = flags.render(
            {flags.DOMAIN_UNVERIFIED: {"domain"}},
            {flags.DOMAIN_UNVERIFIED: "meridianlabs.ai"},
        )
        assert out["flag_reason"] == (
            "Domain: the domain shown (meridianlabs.ai) was found on the web "
            "but nothing independently tied it to this organisation — "
            "confirm it"
        )
        assert out["flag_details"] == {flags.DOMAIN_UNVERIFIED: "meridianlabs.ai"}

    def test_the_reason_does_not_read_as_a_suggestion(self):
        """A guard on the wording, not a restatement of it. The prose used to
        propose a value the row did not carry; now the row carries it, and a
        sentence telling a reviewer to go and get one would describe a state
        the record is not in."""
        for prose in (
            flags._REASONS[flags.DOMAIN_UNVERIFIED],
            flags._DETAILED_REASONS[flags.DOMAIN_UNVERIFIED],
        ):
            assert "before using it" not in prose
            assert "a candidate website" not in prose

    def test_render_falls_back_to_the_generic_prose_without_a_detail(self):
        out = flags.render({flags.DOMAIN_UNVERIFIED: {"domain"}})
        assert out["flag_reason"] == (
            f"Domain: {flags._REASONS[flags.DOMAIN_UNVERIFIED]}"
        )
        assert out["flag_details"] == {}

    def test_render_drops_details_for_codes_it_did_not_render(self):
        """The detail map is scoped to what shipped, so a stale entry cannot
        outlive the code it described."""
        out = flags.render(
            {flags.NO_MATCH: {"name1"}},
            {flags.DOMAIN_UNVERIFIED: "meridianlabs.ai"},
        )
        assert out["flag_details"] == {}

    def test_retract_keeps_the_wording_of_the_codes_it_keeps(self):
        rendered = flags.render(
            {flags.NO_MATCH: {"name1"}, flags.DOMAIN_UNVERIFIED: {"domain"}},
            {flags.DOMAIN_UNVERIFIED: "meridianlabs.ai"},
        )
        result = _Flagged(rendered)
        flags.retract(result, [flags.NO_MATCH], "name1")
        assert result.flag_codes == [flags.DOMAIN_UNVERIFIED]
        assert "meridianlabs.ai" in result.flag_reason

    def test_retract_narrows_a_multi_field_scope_instead_of_dropping(self):
        """`multiple-contacts` stands in for what
        `low-confidence-unchanged` used to demonstrate here: withdrawal is per
        field, so a code scoped to two keeps the one that was not written."""
        result = _Flagged(flags.render({
            flags.MULTIPLE_CONTACTS: {"contact", "name2"},
        }))
        flags.retract(result, [flags.MULTIPLE_CONTACTS], "contact")
        assert result.flag_codes == [flags.MULTIPLE_CONTACTS]
        assert result.flagged_fields == ["name2"]
        assert result.flag_scopes == {flags.MULTIPLE_CONTACTS: ["name2"]}

    def test_the_retired_code_cannot_be_raised_as_a_code(self):
        """A caller that has not been migrated fails loudly. Silently
        discarding its scope would lose a real doubt about a real field."""
        with pytest.raises(ValueError, match="retired"):
            flags.render({flags.LOW_CONFIDENCE_UNCHANGED: {"name1"}})

    def test_retracting_the_last_code_clears_the_record(self):
        result = _Flagged(flags.render({flags.NO_MATCH: {"name1"}}))
        flags.retract(result, [flags.NO_MATCH], "name1")
        assert result.flag_codes == []
        assert result.flag_for_review is False
        assert result.flag_reason is None
        assert result.flag_scopes == {}

    @pytest.mark.parametrize("codes, field", [
        ([flags.NO_MATCH], "name1"),          # code not present
        ([flags.DOMAIN_UNVERIFIED], "name1"),  # present, but on another field
    ])
    def test_retract_is_a_no_op_when_nothing_matches(self, codes, field):
        rendered = flags.render({flags.DOMAIN_UNVERIFIED: {"domain"}})
        result = _Flagged(rendered)
        assert flags.retract(result, codes, field) == ()
        assert result.flag_codes == rendered["flag_codes"]
        assert result.flag_reason == rendered["flag_reason"]

    def test_retract_on_a_record_with_no_scope_map_does_nothing(self):
        """A hand-built or legacy result carries no map, so there is nothing
        to withdraw from — it is left exactly as it is rather than cleared."""
        result = _Flagged({
            "flag_codes": [flags.LOW_CONFIDENCE_UNCHANGED],
            "flag_for_review": True, "flagged_fields": ["name1"],
            "flag_reason": "Name 1: ...", "flag_scopes": {},
        })
        assert flags.retract(result, [flags.LOW_CONFIDENCE_UNCHANGED], "name1") == ()
        assert result.flag_codes == [flags.LOW_CONFIDENCE_UNCHANGED]


class TestAnAdminDeskInName2NeedsNoVerification:
    """"Accounts Payable" is not a claim anything could check.

    A department in Name 2 normally carries a real doubt: the pipeline may
    have inferred a unit that does not exist, or spelled it differently from
    the institution. An administrative desk carries neither. There is no
    registry entry, no web presence and no page for the accounts-payable desk
    of a chemicals company — the phrase names where in the customer an invoice
    goes, not a unit whose existence is in question.

    The pipeline already knows this in two other places: `search_term_2` is
    "ADMIN" (or, for a desk built entirely of generic words, empty) for
    exactly these rows, and the department-domain probe skips them before it
    spends a fetch. Flagging afterwards asked a reviewer to confirm what the
    pipeline itself had declined to look for.

    The same reasoning covers the goods-in / goods-out and mail desks and the
    undifferentiated "Business Office": every large site has them, none has a
    page for them, and none has an institutional spelling to get wrong.
    """

    ADMIN = [
        "Accounts Payable", "Account Payable", "Accounts Payable Department",
        "Procurement Services", "Central Purchasing", "Purchasing Dept",
        "Billing Services", "Office of Finance", "AP", "Shared Services",
        "Central Receiving", "Receiving Department", "Shipping and Receiving",
        "Central Stores", "Stockroom", "Mail Room",
        "Business Office", "Main Office", "Office of Administration",
    ]
    NOT_ADMIN = [
        "Oncology Lab", "Department of Chemistry", "Materials Science",
        "Office of Research", "Analytical Services",
        # "Business Office" is a desk; a business SCHOOL is a real unit.
        "School of Business", "Business Administration",
    ]

    @pytest.mark.parametrize("name2", ADMIN)
    def test_an_inferred_admin_desk_is_not_flagged(self, name2):
        """Tier 3 wrote it and nothing corroborates it — and it still does not
        flag, because there is nothing there to corroborate."""
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": name2},
            name1_enriched="Acme Chemicals",
            name2_enriched=name2.upper(),
            _ev_tier3_wrote=("name2",),
        )
        assert flags.UNVERIFIED_INFERENCE not in (out.get("flag_codes") or [])
        assert "name2" not in (out.get("flagged_fields") or [])

    @pytest.mark.parametrize("name2", NOT_ADMIN)
    def test_a_real_unit_is_still_flagged(self, name2):
        """The twin of the case above with one thing changed: Name 2 names a
        unit whose existence IS a question. The doubt is unchanged."""
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": name2},
            name1_enriched="Acme Chemicals",
            name2_enriched=name2.upper() + " GROUP",
            _ev_tier3_wrote=("name2",),
        )
        assert flags.UNVERIFIED_INFERENCE in (out.get("flag_codes") or [])

    def test_the_low_confidence_half_is_cleared_too(self):
        """`department_domain` clears only `unverified-inference` — it says the
        unit exists, not that the record spells it the institution's way. An
        admin desk has no institutional spelling to be wrong about, so the
        admin rule clears both halves of the doubt."""
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": "Accounts Payable"},
            name1_enriched="Acme Chemicals",
            name2_enriched="Accounts Payable",
            _ev_low_conf_unchanged=["name2"],
        )
        assert "name2" not in (out.get("flagged_fields") or [])

    def test_a_non_admin_unit_keeps_its_low_confidence(self):
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": "Oncology Lab"},
            name1_enriched="Acme Chemicals",
            name2_enriched="Oncology Lab",
            _ev_low_conf_unchanged=["name2"],
        )
        assert "name2" in (out.get("flagged_fields") or [])

    def test_name1_doubts_are_untouched_by_an_admin_name2(self):
        """The rule is scoped to Name 2. A record whose NAME 1 is unverifiable
        still says so, whatever sits in the department slot."""
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": "Accounts Payable"},
            name1_enriched="Acme Chemicals International",
            name2_enriched="Accounts Payable",
            _ev_tier3_wrote=("name1",),
        )
        assert flags.UNVERIFIED_INFERENCE in (out.get("flag_codes") or [])
        assert "name1" in (out.get("flagged_fields") or [])

    # ── The derived half ────────────────────────────────────────────────
    #
    # `test_the_low_confidence_half_is_cleared_too` above exercises the
    # `_ev_low_conf_unchanged` MARKER, and the marker branch always consulted
    # the admin rule. The provenance-DERIVED low is a second, independent
    # route to the same flag — `low_confidence_core_fields` reads `input:low`
    # off the write history — and it consulted nothing. An "Accounts Payable"
    # left exactly as supplied went out asking a reviewer to establish a
    # canonical form it does not have. These pin the route the marker tests
    # could not reach.

    @staticmethod
    def _unchanged_name2(name2: str):
        """A record whose Name 1 a registry settled and whose Name 2 was
        RETAINED with nothing corroborating it — `input:low` on name2, and no
        marker anywhere. The real not-canonicalised state."""
        from enrichment.provenance import deterministic_evidence, registry_evidence

        result = _init_result(EnrichmentRecord(
            record_id="AD1", country="US", name1="Acme Chemicals", name2=name2,
        ))
        # A settled Name 1, so `no-match` cannot fire and the only question
        # left on the record is the one under test.
        seed(result, registry_evidence("ror", "https://ror.org/00x"),
             name1_enriched="Acme Chemicals", ror_id="https://ror.org/00x")
        seed(result, deterministic_evidence("passthrough"), name2_enriched=name2)
        return finalise(result, time.monotonic())

    @pytest.mark.parametrize("name2", ADMIN)
    def test_an_unchanged_admin_desk_does_not_ask_for_review(self, name2):
        out = self._unchanged_name2(name2)
        assert out["flag_for_review"] is False
        assert out["flag_low_confidence"] == []
        assert "name2" not in (out.get("flagged_fields") or [])

    @pytest.mark.parametrize("name2", NOT_ADMIN)
    def test_an_unchanged_real_unit_still_does(self, name2):
        """The twin, with one thing changed. A unit that HAS an institutional
        spelling can be spelled wrong, so the doubt stands."""
        out = self._unchanged_name2(name2)
        assert out["flag_for_review"] is True
        assert out["flag_low_confidence"] == ["name2"]

    @pytest.mark.parametrize(
        "name2", ["Central Warehouse", "Main Plant", "Corporate Headquarters"],
    )
    def test_a_phrase_that_names_no_unit_is_covered_by_the_same_rule(self, name2):
        """Not a back-office desk, so not in `is_admin_unit`'s vocabulary —
        but it fails the same test for the same reason, and the loading bay of
        a chemicals company has no canonical form either."""
        assert self._unchanged_name2(name2)["flag_for_review"] is False

    @pytest.mark.parametrize("name2", ADMIN[:4])
    def test_the_provenance_still_says_low(self, name2):
        """The exemption is about the REVIEW REQUEST, not about the evidence.
        Nothing corroborated the value and the column must keep saying so —
        suppressing the flag by inflating the confidence would be lying about
        the record to make a queue shorter."""
        assert self._unchanged_name2(name2)["name2_provenance"] == "input:low"


class TestAPhraseThatNamesNoUnitNeedsNoVerification:
    """"Central Warehouse" is not a claim anything could check either.

    The twin of the admin-desk rule, reached from the other side. A back-office
    desk is a real desk with no web presence; a phrase built entirely from
    facility functions and scope qualifiers is not even a desk. Every large
    site has a warehouse, a plant and a headquarters, and none of them says
    whose site it is — there is no page to open, no registry entry to match
    and no institutional spelling to get wrong.

    `search_terms.identifies_nothing` is the same test that already empties
    Search Term 2 for these rows and skips the department-domain probe before
    it spends a fetch. Flagging afterwards asked a reviewer to confirm a
    phrase the pipeline had just declined to search for.
    """

    NAMES_NOTHING = [
        "Central Warehouse", "Main Plant", "Corporate Headquarters",
        "Distribution Center", "Shipping Dock", "Manufacturing",
        "Global Operations", "Production Facility", "Logistics",
        "Interplant Site Off E",
    ]
    NAMES_A_UNIT = [
        "Advanced Manufacturing", "Polymer Production", "Vaccine Distribution",
        "Neuroscience Institute", "Food Service Systems", "Global Technical",
    ]

    @pytest.mark.parametrize("name2", NAMES_NOTHING)
    def test_an_inferred_facility_phrase_is_not_flagged(self, name2):
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": name2},
            name1_enriched="Acme Chemicals",
            name2_enriched=name2.upper(),
            _ev_tier3_wrote=("name2",),
        )
        assert flags.UNVERIFIED_INFERENCE not in (out.get("flag_codes") or [])
        assert "name2" not in (out.get("flagged_fields") or [])

    @pytest.mark.parametrize("name2", NAMES_NOTHING)
    def test_the_low_confidence_half_is_cleared_too(self, name2):
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": name2},
            name1_enriched="Acme Chemicals",
            name2_enriched=name2,
            _ev_low_conf_unchanged=["name2"],
        )
        assert "name2" not in (out.get("flagged_fields") or [])

    @pytest.mark.parametrize("name2", NAMES_A_UNIT)
    def test_one_identifying_token_keeps_the_doubt(self, name2):
        """The boundary. "Manufacturing" alone names nothing; "Advanced
        Manufacturing" names a unit, and a unit's existence is a question."""
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": name2},
            name1_enriched="Acme Chemicals",
            name2_enriched=name2.upper() + " GROUP",
            _ev_tier3_wrote=("name2",),
        )
        assert flags.UNVERIFIED_INFERENCE in (out.get("flag_codes") or [])

    def test_name1_doubts_are_untouched(self):
        """Scoped to Name 2, exactly as the admin rule is."""
        out = _finalised(
            {"name1": "Acme Chemicals", "name2": "Central Warehouse"},
            name1_enriched="Acme Chemicals International",
            name2_enriched="Central Warehouse",
            _ev_tier3_wrote=("name1",),
        )
        assert flags.UNVERIFIED_INFERENCE in (out.get("flag_codes") or [])
        assert "name1" in (out.get("flagged_fields") or [])

    def test_a_blank_name2_is_not_a_phrase_that_identifies_nothing(self):
        """`identifies_nothing("")` is False: an empty slot is no phrase, and
        the callers keep their own handling of one."""
        from enrichment.search_terms import identifies_nothing
        assert identifies_nothing("") is False
        assert identifies_nothing(None) is False

    def test_the_records_own_address_does_not_identify_a_unit(self):
        """A Name 2 that only repeats the record's own city is a location, not
        a department — the geo half of the same test."""
        from enrichment.search_terms import identifies_nothing
        rec = {"city": "Midland", "region": "MI"}
        assert identifies_nothing("Midland Site", rec) is True
        assert identifies_nothing("Midland Site", {}) is False


class TestAnAdminDeskIsNotSentForCanonicalisation:
    """The other half of "just normalise them and let them be".

    Not flagging an admin desk was only half the problem: the pipeline was
    still asking a model to canonicalise one. "Accounts Payable" IS the
    canonical spelling of the accounts-payable desk, and "Office of
    Purchasing" is not a draft of "Procurement Services" — an answer the
    identity guard in `tier2_canonical` then has to refuse, which is a worked
    example in its own docstring. The record spent an LLM call to arrive back
    where it started, and shipped `input:low` for its trouble. Preprocessing
    has already fixed the casing and expanded the abbreviations, which is all
    these values ever needed.

    Pinned on the PREDICATE rather than through a batch run: the three stages
    that consult it — the search-term derivation, the Tier 2 skip and the flag
    exemption — must give one answer for one phrase, and that shared answer is
    the thing worth pinning. `TestAnAdminDeskInName2NeedsNoVerification` above
    covers the flag consequence end to end.
    """

    NO_CANONICAL_FORM = [
        # Back-office desks — `is_admin_unit`.
        "Accounts Payable", "Central Receiving", "Procurement Services",
        "Office of Purchasing", "Purchasing Dept", "Mail Room",
        "Business Office", "Shipping and Receiving",
        # Facility function + scope qualifier — `identifies_nothing`.
        "Central Warehouse", "Main Plant", "Corporate Headquarters",
    ]
    HAS_A_CANONICAL_FORM = [
        "Dept of Chemistry", "Oncology Lab", "School of Business",
        "Office of Research", "Materials Science",
    ]

    @pytest.mark.parametrize("name2", NO_CANONICAL_FORM)
    def test_a_phrase_with_no_official_spelling_is_recognised(self, name2):
        from enrichment.search_terms import has_no_canonical_form

        assert has_no_canonical_form(name2) is True

    @pytest.mark.parametrize("name2", HAS_A_CANONICAL_FORM)
    def test_a_real_unit_is_not(self, name2):
        """The guard on the skip: a unit with an institutional spelling is
        exactly what canonicalisation is for, and must still go."""
        from enrichment.search_terms import has_no_canonical_form

        assert has_no_canonical_form(name2) is False

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_a_blank_slot_is_not_a_phrase(self, value):
        """False, so callers keep their own handling of an empty slot rather
        than inheriting a skip meant for a phrase."""
        from enrichment.search_terms import has_no_canonical_form

        assert has_no_canonical_form(value) is False

    @pytest.mark.parametrize("name2", NO_CANONICAL_FORM)
    def test_the_flag_exemption_reads_the_same_predicate(self, name2):
        """The two stages cannot drift: one function answers for both."""
        assert flags.name2_needs_no_verification(
            {"name2_enriched": name2},
        ) is True
