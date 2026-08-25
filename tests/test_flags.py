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
* the three output fields stay consistent: ``flag_for_review`` is true **iff**
  ``flag_codes`` is non-empty, and ``flagged_fields`` scopes the doubt.
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
    async def search(self, q, num_results=5):
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


class TestFlagFieldsStayConsistent:
    """``flag_for_review`` is true if and only if ``flag_codes`` is non-empty."""

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
        what the field's confidence already said."""
        out = _finalised(**overrides)
        assert out["flag_for_review"] is (
            bool(out["flag_codes"]) or bool(out["flag_low_confidence"])
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
            raised = bool(r.flag_codes) or bool(r.flag_low_confidence)
            assert r.flag_for_review is raised
            # Every code is part of the published vocabulary, and every
            # flagged field is one a reviewer can open.
            assert set(r.flag_codes) <= set(flags.ALL_CODES)
            assert set(r.flagged_fields) <= set(flags.FIELD_LABELS)
            assert bool(r.flag_reason) is raised
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
        assert out["flag_for_review"] is True
        assert out["flagged_fields"] == ["domain"]
        assert out["flag_reason"].startswith("Domain:")

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
        assert result.flag_for_review is True
        assert "left exactly as supplied" not in result.flag_reason

    def test_render_names_the_rejected_domain_in_the_reason(self):
        """A reviewer told to "confirm the website" would have to rediscover
        which one; the guard knew, so the prose says it."""
        out = flags.render(
            {flags.DOMAIN_UNVERIFIED: {"domain"}},
            {flags.DOMAIN_UNVERIFIED: "meridianlabs.ai"},
        )
        assert out["flag_reason"] == (
            "Domain: a candidate website (meridianlabs.ai) was found but "
            "nothing tied it to this organisation — confirm meridianlabs.ai "
            "before using it"
        )
        assert out["flag_details"] == {flags.DOMAIN_UNVERIFIED: "meridianlabs.ai"}

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
