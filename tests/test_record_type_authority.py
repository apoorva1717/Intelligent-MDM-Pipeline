"""``record_type`` has one authority: :mod:`enrichment.classifier`.

It used to be written by whichever tier ran last — ROR org types, then an LEI
hit, then company canonicalisation — so MIT came out ``company`` because it
holds an LEI, a hospital came out ``company`` because it took the company
branch, and ``unknown`` sat on 21 of 50 demo records without anything having
decided so.

The split these tests pin:

* ``routing_type`` — provisional, written by the tiers, gates which tiers run.
* ``record_type`` — final, decided once in ``finalise`` from ranked evidence.

Changing the first must not change which tiers run; changing the second must
not require re-running anything.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from config import Settings
from enrichment.classifier import (
    COMPANY,
    RESEARCH,
    UNKNOWN,
    TypeEvidence,
    classify,
)
from enrichment.elf_codes import COMMERCIAL_ELF, NON_COMMERCIAL_ELF
from enrichment.orchestrator import Orchestrator, _init_result, finalise
from tests.conftest import seed
from utils.cache import BatchCache

# Real ISO 20275 codes, verified against api.gleif.org.
ELF_NONPROFIT_CORP = "9I4Y"      # Mayo Clinic — "Non-Profit Corporation"
ELF_NONSTOCK_CORP = "7W53"       # Yale — "Nonstock Corporation"
ELF_EV = "QZ3L"                  # Max-Planck — "eingetragener Verein"
ELF_CORPORATION = "XTIQ"         # Pfizer / Bruker — "Corporation"
ELF_AG = "6QQB"                  # Siemens — "Aktiengesellschaft"
ELF_OTHER = "8888"               # catch-all; MIT and Pfizer Canada both use it


# ---------------------------------------------------------------------------
# The ELF table itself
# ---------------------------------------------------------------------------

class TestELFCodes:
    @pytest.mark.parametrize("code", [
        ELF_NONPROFIT_CORP, ELF_NONSTOCK_CORP, ELF_EV,
        "7VK5",  # Cleveland Clinic — "Corporation (Nonprofit)"
    ])
    def test_non_commercial_forms(self, code):
        assert code in NON_COMMERCIAL_ELF
        assert code not in COMMERCIAL_ELF

    @pytest.mark.parametrize("code", [
        ELF_CORPORATION, ELF_AG,
        "HLR4",  # Lockheed — "Stock Corporation"
        "SGST",  # BASF — "Europäische Aktiengesellschaft"
        "MVII",  # Novartis — "Company limited by shares"
    ])
    def test_commercial_forms(self, code):
        assert code in COMMERCIAL_ELF
        assert code not in NON_COMMERCIAL_ELF

    @pytest.mark.parametrize("code", [
        ELF_OTHER, "9999",  # catch-alls carry no meaning of their own
        "14OD",             # "credit union" — non-commercial but not research
        "Y1ZD",             # "Savings and Loan Association"
        "BOEX",             # "Business Trust"
    ])
    def test_forms_that_must_carry_no_signal(self, code):
        """A form that is neither clearly a trading entity nor clearly a
        non-profit must fall through to the next evidence source rather than
        being guessed at."""
        assert code not in NON_COMMERCIAL_ELF

    def test_nonprofit_wording_is_never_read_as_commercial(self):
        """'Nonstock Corporation' and 'Corporation (Nonprofit)' both contain
        'Corporation' — a naive substring rule would call them companies."""
        for code in (ELF_NONSTOCK_CORP, "7VK5", "47LQ"):
            assert code not in COMMERCIAL_ELF

    def test_for_profit_public_benefit_is_not_non_commercial(self):
        """'For-Profit Public Benefit Corporation' says both things; the
        for-profit half wins."""
        assert "I3Z9" not in NON_COMMERCIAL_ELF


# ---------------------------------------------------------------------------
# Evidence ranking
# ---------------------------------------------------------------------------

class TestEvidenceRanking:
    def test_ror_wins_over_gleif(self):
        """ROR types take precedence over GLEIF metadata when both exist."""
        t, src = classify(TypeEvidence(
            name1="Some Institute",
            ror_is_research=True,
            lei_id="X" * 20,
            gleif_category="GENERAL",
            gleif_legal_form_id=ELF_CORPORATION,   # GLEIF says commercial
        ))
        assert (t, src) == (RESEARCH, "ror")

    def test_ror_company_verdict_also_wins(self):
        t, src = classify(TypeEvidence(
            name1="Bruker Corporation",
            ror_is_research=False,
            lei_id="X" * 20,
            gleif_legal_form_id=ELF_NONPROFIT_CORP,
        ))
        assert (t, src) == (COMPANY, "ror")

    def test_gleif_wins_over_the_keyword_heuristic(self):
        """A name with no institutional keyword is classified by GLEIF, not
        left to fall through."""
        t, src = classify(TypeEvidence(
            name1="Acme Holdings",
            lei_id="X" * 20,
            gleif_category="GENERAL",
            gleif_legal_form_id=ELF_AG,
        ))
        assert (t, src) == (COMPANY, "gleif")

    def test_gleif_non_commercial_form_yields_research(self):
        t, src = classify(TypeEvidence(
            name1="Bayfront Trustees",
            lei_id="X" * 20,
            gleif_legal_form_id=ELF_EV,
        ))
        assert (t, src) == (RESEARCH, "gleif")

    def test_keyword_is_the_last_resort(self):
        t, src = classify(TypeEvidence(name1="University of Stuttgart"))
        assert (t, src) == (RESEARCH, "keyword")

    def test_general_category_carries_no_signal(self):
        """MIT and Pfizer both carry category GENERAL — on its own it decides
        nothing, and an unrecognised legal form must not either."""
        t, src = classify(TypeEvidence(
            name1="Acme Holdings",
            lei_id="X" * 20,
            gleif_category="GENERAL",
            gleif_legal_form_id="ZZZZ",
        ))
        assert (t, src) == (UNKNOWN, "unresolved")

    def test_unknown_only_when_every_source_is_silent(self):
        t, src = classify(TypeEvidence(name1="Acme Holdings"))
        assert (t, src) == (UNKNOWN, "unresolved")
        # …and any one source speaking up removes it.
        assert classify(TypeEvidence(
            name1="Acme Holdings", ror_is_research=False))[0] == COMPANY
        assert classify(TypeEvidence(
            name1="Acme Holdings", lei_id="X" * 20,
            gleif_legal_form_id=ELF_AG))[0] == COMPANY
        assert classify(TypeEvidence(name1="Acme University"))[0] == RESEARCH

    def test_unknown_always_reports_unresolved(self):
        for ev in (
            TypeEvidence(),
            TypeEvidence(name1="Acme Holdings"),
            TypeEvidence(name1="Acme Holdings", lei_id="X" * 20),
            TypeEvidence(name1="Acme Holdings", lei_id="X" * 20,
                         gleif_legal_form_id=ELF_OTHER),
        ):
            t, src = classify(ev)
            assert (t == UNKNOWN) == (src == "unresolved")


# ---------------------------------------------------------------------------
# The LEI guard
# ---------------------------------------------------------------------------

class TestLEIGuard:
    def test_an_lei_alone_never_yields_company(self):
        """An LEI proves legal registration, not commercial status."""
        t, src = classify(TypeEvidence(
            name1="Massachusetts Institute of Technology",
            lei_id="DLZO3A31IADZ27B62557",
        ))
        assert t == RESEARCH
        assert src == "keyword"

    def test_mit_real_gleif_shape_is_research(self):
        """MIT's live record: category GENERAL, legalForm 8888 / 'INSTITUTE'.
        Neither field is a commercial signal, and the name settles it."""
        t, _ = classify(TypeEvidence(
            name1="Massachusetts Institute of Technology",
            lei_id="DLZO3A31IADZ27B62557",
            gleif_category="GENERAL",
            gleif_legal_form_id=ELF_OTHER,
            gleif_legal_form_other="INSTITUTE",
        ))
        assert t == RESEARCH

    def test_commercial_form_is_withheld_for_an_institution_name(self):
        """A university incorporated under an ordinary trading form is still a
        university: the commercial verdict is withheld and the keyword source
        answers."""
        t, src = classify(TypeEvidence(
            name1="Riverside University",
            lei_id="X" * 20,
            gleif_legal_form_id=ELF_CORPORATION,
        ))
        assert (t, src) == (RESEARCH, "keyword")

    def test_hospital_with_an_lei_is_not_a_company(self):
        t, _ = classify(TypeEvidence(
            name1="Brigham and Women's Hospital",
            lei_id="X" * 20,
            gleif_legal_form_id=ELF_OTHER,
            gleif_legal_form_other="Hospital",
        ))
        assert t == RESEARCH

    def test_pfizer_is_not_overcorrected(self):
        """The guard must not drag a genuine company across."""
        t, src = classify(TypeEvidence(
            name1="Pfizer Inc.",
            lei_id="765LHXWGK1KXCLTFYQ30",
            gleif_category="GENERAL",
            gleif_legal_form_id=ELF_CORPORATION,
        ))
        assert (t, src) == (COMPANY, "gleif")

    def test_pfizer_canada_catch_all_form_still_reads_commercial(self):
        """8888 with other='INCORPORATED / INCORPOREE' — the code says nothing,
        the free text does."""
        t, _ = classify(TypeEvidence(
            name1="Pfizer Canada Inc.",
            lei_id="549300NPEN3DIEAYWM33",
            gleif_legal_form_id=ELF_OTHER,
            gleif_legal_form_other="INCORPORATED / INCORPOREE",
        ))
        assert t == COMPANY


# ---------------------------------------------------------------------------
# Tier 3 contributes nothing
# ---------------------------------------------------------------------------

class TestTier3ContributesNoEvidence:
    def test_a_tier3_record_is_classified_by_name_or_not_at_all(self):
        """Tier 3 is a last-resort name guesser with no classification signal.
        A record that reached it is classified from the evidence that exists —
        never from having been to Tier 3."""
        result = _base_result(name1="Comet Therapeutics", tier_used=3,
                              source="LLM", routing_type="unknown")
        out = finalise(result, time.monotonic())
        assert out["record_type"] == UNKNOWN
        assert out["record_type_source"] == "unresolved"

    def test_tier3_cannot_turn_a_hospital_into_a_company(self):
        result = _base_result(name1="Brigham and Women's Hospital",
                              tier_used=3, source="LLM", routing_type="company")
        out = finalise(result, time.monotonic())
        assert out["record_type"] == RESEARCH
        assert out["record_type_source"] == "keyword"


# ---------------------------------------------------------------------------
# finalise() integration
# ---------------------------------------------------------------------------

def _base_result(**overrides):
    rec = EnrichmentRecord(
        record_id=overrides.pop("record_id", "T1"),
        name1=overrides.get("name1", "Acme"),
        country="US",
    )
    result = _init_result(rec)
    seed(result, name1_enriched=overrides.pop("name1", "Acme"), **overrides)
    return result


class TestFinaliseIsTheOnlyWriter:
    def test_routing_type_does_not_leak_into_record_type(self):
        """A record routed as a company but named like an institution comes out
        research_institution — and the mismatch is flagged, not hidden."""
        result = _base_result(name1="Universität Stuttgart",
                              routing_type="company")
        out = finalise(result, time.monotonic())
        assert out["record_type"] == RESEARCH
        assert out["routing_type"] == "company"
        assert out["routing_type_mismatch"] is True

    def test_agreement_is_not_a_mismatch(self):
        result = _base_result(name1="Universität Stuttgart",
                              routing_type="research_institution")
        out = finalise(result, time.monotonic())
        assert out["routing_type_mismatch"] is False

    def test_unknown_routing_is_not_a_mismatch(self):
        """A record that was never routed anywhere in particular has nothing to
        disagree with."""
        result = _base_result(name1="Universität Stuttgart",
                              routing_type="unknown")
        out = finalise(result, time.monotonic())
        assert out["record_type"] == RESEARCH
        assert out["routing_type_mismatch"] is False

    def test_mit_keeps_its_lei_and_is_research(self):
        """Row 24: an LEI and a research classification are not in conflict."""
        result = _base_result(
            name1="Massachusetts Institute of Technology",
            routing_type="company",
            lei_id="DLZO3A31IADZ27B62557",
            _gleif_category="GENERAL",
            _gleif_legal_form_id=ELF_OTHER,
            _gleif_legal_form_other="INSTITUTE",
        )
        out = finalise(result, time.monotonic())
        assert out["record_type"] == RESEARCH
        assert out["lei_id"] == "DLZO3A31IADZ27B62557"

    def test_ror_evidence_from_the_tier1_retry_is_honoured(self):
        result = _base_result(name1="Universität Stuttgart",
                              routing_type="unknown",
                              _ror_is_research=True,
                              ror_id="https://ror.org/04vnq7t77")
        out = finalise(result, time.monotonic())
        assert (out["record_type"], out["record_type_source"]) == (RESEARCH, "ror")

    def test_evidence_keys_do_not_reach_the_output(self):
        result = _base_result(name1="Acme", _ror_is_research=False,
                              _gleif_category="GENERAL")
        out = finalise(result, time.monotonic())
        for key in ("_ror_is_research", "_gleif_category", "_gleif_sub_category",
                    "_gleif_legal_form_id", "_gleif_legal_form_other"):
            assert key not in out

    def test_variant_spellings_of_one_org_share_a_type(self):
        """Rows 15/16/17: three name forms of "Coastal Diagnostics" must not
        come out with three different types."""
        types = set()
        for name, routing in (("Coastal Diagnostics", "unknown"),
                              ("Coastal Diagnostics, Inc.", "company"),
                              ("Coastal Diagnostics Inc", "unknown")):
            out = finalise(_base_result(name1=name, routing_type=routing),
                           time.monotonic())
            types.add(out["record_type"])
        assert len(types) == 1


# ---------------------------------------------------------------------------
# Routing is unchanged
# ---------------------------------------------------------------------------

class TestRoutingUnchanged:
    @pytest.mark.asyncio
    async def test_research_routing_still_reaches_the_department_probe(self):
        """Tier 2B's precondition now reads routing_type. A record routed as a
        research institution must still reach it, on the same preconditions."""
        from tests.mocks.openai_mock import MockOpenAIClient
        from tests.mocks.page_mock import MockPageFetcher
        from tests.mocks.serp_mock import MockSearchClient

        orch = Orchestrator(Settings(), mock_clients={
            "search": MockSearchClient(), "page_fetcher": MockPageFetcher(),
            "llm": MockOpenAIClient(),
        })
        rec = EnrichmentRecord(
            record_id="X", name1="University of Florida", city="Gainesville",
            state="FL", zip="32611", country="US", email="registrar@ufl.edu",
        )
        result = _init_result(rec)
        seed(
            result,
            name1_enriched="University of Florida",
            name2_enriched="Department of Chemistry",
            routing_type="research_institution",
            domain="ufl.edu",
            website_url="https://ufl.edu",
        )
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.department_domain is not None
        # …and the record still classifies as a research institution.
        assert out.record_type == RESEARCH

    @pytest.mark.asyncio
    async def test_company_routing_still_skips_the_department_probe(self):
        from tests.mocks.openai_mock import MockOpenAIClient
        from tests.mocks.page_mock import MockPageFetcher
        from tests.mocks.serp_mock import MockSearchClient

        orch = Orchestrator(Settings(), mock_clients={
            "search": MockSearchClient(), "page_fetcher": MockPageFetcher(),
            "llm": MockOpenAIClient(),
        })
        rec = EnrichmentRecord(record_id="Y", name1="Bruker Corporation",
                               country="US")
        result = _init_result(rec)
        seed(
            result,
            name1_enriched="Bruker Corporation",
            name2_enriched="Department of Chemistry",
            routing_type="company",
            domain="bruker.com",
        )
        out = await orch._finalise_and_return(
            result, time.monotonic(), rec, BatchCache())
        assert out.department_domain is None
