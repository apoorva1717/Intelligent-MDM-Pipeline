"""A name that differs only by legal form has not been corrected.

Two lanes could each replace the name the record states with a register's or a
model's rendering of the same name: the registry write and the Tier 2 company
canonicaliser. Both went through a stretch of shipping "Quest Diagnostics
Incorporated" for a record that said "Quest Diagnostics", "Celanese
Corporation" for "Celanese", and "Paper Money Guaranty, LLC" for "Paper Money
Guaranty".

None of those is a correction. `enrichment.registry_match.names_match_verbatim`
already states the principle it turns on -- the legal form is the register's
suffix, not a distinguishing token -- so a difference confined to it means the
two strings are one name written twice, and the record wrote it first.

Two boundaries keep this from becoming "never correct a name":

* a **different** legal form is a different legal entity ("Neptune Benson,
  Inc." against "Neptune-Benson, LLC"), and the register is the authority that
  says so;
* a **case or punctuation** difference is not a legal-form difference at all.
  ROR's "University of California, Riverside" against SAP's comma-less
  spelling is the registry spelling its own name, and there the display name
  must win -- which is why the test is `_legal_forms(a) != _legal_forms(b)`
  and not `names_match_verbatim` alone.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.company_canonical import run_company_canonical
from enrichment.orchestrator import (
    _init_result,
    _preferred_registry_variant,
    _write_registry_name,
    finalise,
)

BRIGHAM_IN = "Brigham and Women" + chr(39) + "s Hospital Inc"
BRIGHAM_REG = "Brigham and Women" + chr(39) + "s Hospital"


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload

    async def extract_json(self, system, user, **kwargs):
        return self._payload


# ---------------------------------------------------------------------------
# The preference itself
# ---------------------------------------------------------------------------

class TestPreferredRegistryVariant:
    @pytest.mark.parametrize("incumbent,display", [
        ("Quest Diagnostics", "QUEST DIAGNOSTICS INCORPORATED"),
        ("Celanese", "Celanese Corporation"),
        ("Quick-Med Technologies Inc", "Quick-Med Technologies"),
        (BRIGHAM_IN, BRIGHAM_REG),
        ("Dow Chemical Co", "Dow Chemical"),
    ])
    def test_a_legal_form_difference_keeps_the_records_name(
        self, incumbent, display,
    ):
        assert _preferred_registry_variant(incumbent, display, []) == incumbent

    @pytest.mark.parametrize("incumbent,display", [
        # A different legal form -- two legal entities.
        ("Neptune Benson, Inc.", "Neptune-Benson, LLC"),
        # Punctuation only: the registry is spelling its own name.
        ("University of California Riverside",
         "University of California, Riverside"),
        # Case only.
        ("Southwest Gas Corporation", "SOUTHWEST GAS CORPORATION"),
        # A name the register does not publish at all -- the whole point of
        # the registry name write.
        ("Mayo Clinic FLA", "Mayo Clinic in Florida"),
        # A broader entity is not the same name.
        ("M.D. Anderson Cancer Center",
         "The University of Texas MD Anderson Cancer Center"),
    ])
    def test_everything_else_still_takes_the_registry_name(
        self, incumbent, display,
    ):
        assert _preferred_registry_variant(incumbent, display, []) == display

    def test_a_published_variant_still_wins_over_the_display_name(self):
        """The pre-existing rule is unchanged: when the record's name is one
        the registry publishes, that variant is written."""
        assert _preferred_registry_variant(
            "NASA",
            "National Aeronautics and Space Administration",
            ["National Aeronautics and Space Administration", "NASA"],
        ) == "NASA"


# ---------------------------------------------------------------------------
# What the record ships, and who it is attributed to
# ---------------------------------------------------------------------------

class TestTheKeptNameIsAttributedToTheRecord:
    def _result(self, name1):
        return _init_result(EnrichmentRecord(
            record_id="t", country="US", name1=name1,
        ))

    def test_the_registry_does_not_get_credit_for_the_records_spelling(self):
        r = self._result("Quest Diagnostics")
        _write_registry_name(
            r, "name1", "QUEST DIAGNOSTICS INCORPORATED", "GLEIF",
            identifier="8MCWUBXQ0WE04KMXBX50",
            incumbent="Quest Diagnostics",
            variants=["QUEST DIAGNOSTICS INCORPORATED"],
        )
        assert r["name1_enriched"] == "Quest Diagnostics"
        # GLEIF never published this string, so it must not be attributed to
        # GLEIF -- that is the defect this area keeps producing.
        event = r.provenance.attributing_event("name1_enriched")
        assert event.producer_chain == ("input",)
        # ...and the field is not registry-owned, so the ordinary output
        # passes still treat it as the input value it is.
        assert "name1" not in (r.get("_registry_name_fields") or set())

    def test_a_shouted_input_is_still_cased_on_the_way_out(self):
        """Because the kept name is not registry-owned, `finalise` cases it
        like any other input value."""
        r = self._result("QUEST DIAGNOSTICS")
        _write_registry_name(
            r, "name1", "QUEST DIAGNOSTICS INCORPORATED", "GLEIF",
            identifier="8MCWUBXQ0WE04KMXBX50",
            incumbent="QUEST DIAGNOSTICS",
            variants=["QUEST DIAGNOSTICS INCORPORATED"],
        )
        out = finalise(r, time.monotonic())
        assert out["name1_enriched"] == "Quest Diagnostics"

    def test_the_registry_still_owns_the_name_it_actually_supplies(self):
        r = self._result("Mayo Clinic FLA")
        _write_registry_name(
            r, "name1", "Mayo Clinic in Florida", "ROR",
            identifier="https://ror.org/03zzw1w08",
            incumbent="Mayo Clinic FLA",
            variants=["Mayo Clinic in Florida"],
        )
        assert r["name1_enriched"] == "Mayo Clinic in Florida"
        assert "name1" in (r.get("_registry_name_fields") or set())


# ---------------------------------------------------------------------------
# The same rule, in the Tier 2 company canonicaliser
# ---------------------------------------------------------------------------

async def _canon(name1, llm):
    return await run_company_canonical(
        record_id="t", name1=name1, llm_client=llm,
        city="Sarasota", state="FL", country="United States",
    )


class TestCompanyCanonicalDeclinesALegalFormOnlyRewrite:
    @pytest.mark.asyncio
    async def test_adding_a_suffix_the_record_did_not_state_is_declined(self):
        res = await _canon("Paper Money Guaranty",
            _FakeLLM({
                "official_name": "Paper Money Guaranty, LLC",
                "confidence": "high",
            }),
        )
        assert res.success is False
        assert res.name1_enriched is None

    @pytest.mark.asyncio
    async def test_dropping_one_the_record_did_state_is_declined(self):
        res = await _canon("Quick-Med Technologies Inc",
            _FakeLLM({
                "official_name": "Quick-Med Technologies",
                "confidence": "high",
            }),
        )
        assert res.success is False

    @pytest.mark.asyncio
    async def test_a_real_canonicalisation_is_still_accepted(self):
        """The control -- the lane must keep doing its job."""
        res = await _canon("Bio-Rad Lab Inc",
            _FakeLLM({
                "official_name": "Bio-Rad Laboratories, Inc.",
                "confidence": "high",
            }),
        )
        assert res.success is True
        assert res.name1_enriched == "Bio-Rad Laboratories, Inc."
