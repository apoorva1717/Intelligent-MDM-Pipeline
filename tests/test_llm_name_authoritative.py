"""The name write gate (§2) and the acceptance policy switch (§4).

One gate for every Name 1 / Name 2 candidate, whatever produced it. What it
refuses it refuses out loud: the candidate travels to the flag detail, so a
record never ships "the canonical form could not be established" about a form
the pipeline established and declined.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from config import Settings
from enrichment import name_gate
from enrichment.locality import CONTRADICTED
from utils.name_identity import DIFFERENT, SAME, UNDECIDABLE


def _settings(**over):
    s = Settings()
    for k, v in over.items():
        object.__setattr__(s, k, v)
    return s


class TestTheHallucinationWall:
    """`different` is the only verdict that refuses a model's answer."""

    def test_a_different_entity_is_refused_and_named(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1",
            "State Key Laboratory of Digital Medical Engineering",
            incumbent="Bio-Rad Lab Inc", settings=_settings(),
        )
        assert d.allow is False
        assert d.verdict == DIFFERENT
        assert d.reason == name_gate.REASON_DIFFERENT_ENTITY
        # Never silently discarded — the flag detail gets it verbatim.
        assert d.suggestion == (
            "State Key Laboratory of Digital Medical Engineering"
        )

    def test_a_repair_is_written_unflagged(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1", "Bio-Rad Laboratories, Inc.",
            incumbent="Bio-Rad Lab Inc", settings=_settings(),
        )
        assert (d.allow, d.verdict, d.flagged) == (True, SAME, False)

    def test_an_undecidable_answer_is_written_and_flagged(self):
        """The VA case: unmatchable codes, but nothing contradicted."""
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1",
            "VA Greater Los Angeles Healthcare System",
            incumbent="VA MC West LA Visn 22", settings=_settings(),
        )
        assert d.allow is True
        assert d.verdict == UNDECIDABLE
        assert d.flagged is True


class TestTheDeterministicRefusals:

    def test_an_empty_answer_writes_nothing(self):
        d = name_gate.evaluate({"record_id": "r"}, "name1", "  ")
        assert (d.allow, d.reason) == (False, name_gate.REASON_EMPTY)

    def test_an_address_shaped_name_is_refused(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1", "1000 Alfred Nobel Drive 94547",
            incumbent="Bio-Rad Lab Inc",
        )
        assert (d.allow, d.reason) == (False, name_gate.REASON_ADDRESS_LIKE)

    def test_a_superseded_entity_never_overwrites_name1(self):
        d = name_gate.evaluate(
            {"record_id": "r", "_ev_entity_superseded": "merged into X"},
            "name1", "Successor Corp", incumbent="Legacy Corp",
        )
        assert (d.allow, d.reason) == (False, name_gate.REASON_SUPERSEDED)


class TestCountry:
    """§2 — the registry-side question the match never asked."""

    def test_a_contradicted_country_blocks_the_name(self):
        """The Dow case: a UK-registered entity on a Michigan record."""
        result = {
            "record_id": "13115148",
            "_src_locality_gleif": {
                "verdict": CONTRADICTED,
                "scope": "country",
                "detail": "states country GB; record says US",
                "tier": "exact",
            },
        }
        d = name_gate.evaluate(
            result, "name1", "Dow Silicones UK Limited",
            incumbent="Dow Chemical Co", country="US",
            registry="GLEIF", from_registry=True, settings=_settings(),
        )
        assert d.allow is False
        assert d.reason == name_gate.REASON_COUNTRY_CONFLICT
        assert d.suggestion == "Dow Silicones UK Limited"

    def test_a_city_difference_stays_advisory(self):
        """A plant against a head office is not a doubt about identity."""
        result = {
            "record_id": "r",
            "_src_locality_gleif": {
                "verdict": CONTRADICTED, "scope": "city",
                "detail": "states city Midland; record says Freeport",
                "tier": "exact",
            },
        }
        d = name_gate.evaluate(
            result, "name1", "Dow Chemical Company",
            incumbent="Dow Chemical Co", country="US",
            registry="GLEIF", from_registry=True, settings=_settings(),
        )
        assert d.allow is True

    @pytest.mark.parametrize("country", [None, "", "Unknown", "United States?"])
    def test_no_usable_country_refuses_a_fuzzy_registry_accept(self, country):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1", "Some Registered Entity Ltd",
            incumbent="Some Entity", country=country,
            registry="GLEIF", from_registry=True, exact_name_match=False,
            settings=_settings(),
        )
        assert d.allow is False
        assert d.reason == name_gate.REASON_NO_COUNTRY_FUZZY

    def test_an_exact_name_match_survives_a_missing_country(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1", "Some Registered Entity Ltd",
            incumbent="Some Registered Entity Ltd", country=None,
            registry="GLEIF", from_registry=True, exact_name_match=True,
            settings=_settings(),
        )
        assert d.allow is True

    def test_a_registry_identification_is_not_re_litigated(self):
        """ROR matched; the identity verdict does not overrule it.

        The company comparator reads "Jacksonville" as a distinctive token
        ROR's "Mayo Clinic in Florida" drops. A registry has already decided
        WHICH entity this is — that is what the identifier attaches to — so
        the wall that stops a model swapping entities does not apply here.
        """
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1", "Mayo Clinic in Florida",
            incumbent="Mayo Clinic Jacksonville", country="US",
            registry="ROR", from_registry=True, exact_name_match=False,
            settings=_settings(),
        )
        assert d.allow is True


class TestTheAcceptancePolicySwitch:
    """§4 — LLM_FALLBACK_AUTHORITATIVE is the thesis A/B."""

    def test_undecidable_is_held_back_under_the_legacy_flag(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1",
            "VA Greater Los Angeles Healthcare System",
            incumbent="VA MC West LA Visn 22",
            settings=_settings(llm_fallback_authoritative=False),
        )
        assert d.allow is False

    def test_undecidable_writes_can_be_switched_off_alone(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name1",
            "VA Greater Los Angeles Healthcare System",
            incumbent="VA MC West LA Visn 22",
            settings=_settings(undecidable_writes=False),
        )
        assert d.allow is False
        # …while a proven-same rewrite still writes.
        assert name_gate.evaluate(
            {"record_id": "r"}, "name1", "Bio-Rad Laboratories, Inc.",
            incumbent="Bio-Rad Lab Inc",
            settings=_settings(undecidable_writes=False),
        ).allow is True


class TestDepartmentSlotsAskTheDepartmentQuestion:

    def test_a_unit_rewrite_is_not_judged_by_the_company_comparator(self):
        """"DEDMAN Dept of Chemistry" → "Department of Chemistry" is a
        re-wording, and the company vocabulary has no word for it."""
        d = name_gate.evaluate(
            {"record_id": "r"}, "name2", "Department of Chemistry",
            incumbent="DEDMAN Dept of Chemistry", settings=_settings(),
        )
        assert d.allow is True

    def test_a_subject_swap_is_still_refused(self):
        d = name_gate.evaluate(
            {"record_id": "r"}, "name2", "Procurement Services",
            incumbent="Office of Purchasing", settings=_settings(),
        )
        assert (d.allow, d.verdict) == (False, DIFFERENT)
