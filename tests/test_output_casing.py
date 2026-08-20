"""Fix 5: one finalisation normaliser on every exit path.

Casing was applied inconsistently across the output. `smart_title_case` is a
whole-string rule — it refuses any value that is not entirely upper-case — and
it only ever ran on Name 1. Everything else was cased by accident or not at
all: City shipped "GAINESVILLE", Street 1 shipped "450 INDUSTRIAL WAY", and a
value the street-suffix map had partly corrected shipped half-cased as
"500 TECH Dr MS-4". Name 2, Care Of, Contact and Email were never cased.

The rule these tests pin:

* one normaliser, `normalise_output_fields`, invoked on every exit path —
  the normal path through `finalise`, the UC 0 overflow early return that runs
  no tiers, and the batch-level fail-safe that never reaches `finalise` at all;
* casing is decided TOKEN by token, so a partly-corrected value is finished
  rather than skipped;
* casing changes letter case and nothing else — no apostrophe, comma, period,
  ampersand or hyphen is ever added, removed or rewritten;
* a registry-owned name is never re-cased;
* casing does not set a `*_changed` flag (Finalization Rule 5 option (b)) —
  the flags keep their meaning of "this field was enriched".
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
from tests.conftest import seed
from enrichment.orchestrator import (
    Orchestrator,
    _init_result,
    finalise,
    normalise_output_fields,
)
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.page_mock import MockPageFetcher
from utils.text_utils import normalise_case


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


class _OverflowLLM:
    """Answers the UC 0 overflow prompt with a confident "yes" and every other
    prompt with nothing, so a record stops at Stage 0."""

    async def extract_json(self, system, user, **k):
        if "overflow" in system.lower() or "one continuous" in system.lower():
            return {
                "is_overflow": True,
                "confidence": "high",
                "reasoning": "Name 1 + Name 2 read as one company name",
            }
        return {}

    async def aclose(self):
        pass


class _CountingROR:
    """A ROR client that answers nothing and records every query, so a test can
    assert that no tier ran."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def call(self, name, country_code=None, country=None,
                   city=None, state=None) -> dict[str, Any]:
        self.queries.append(name)
        return {"matched": False, "score": 0.0}


def _orch(ror=None, llm=None):
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": ror if ror is not None else _CountingROR(),
        "lei": MockLEIClient(st),
        "search": _NoSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": llm if llm is not None else _EmptyLLM(),
    })


async def _run(orch, **record_kw):
    rec = EnrichmentRecord(record_id="t", country="US", **record_kw)
    resp = await orch.enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


# ---------------------------------------------------------------------------
# Step 2 — the token caser
# ---------------------------------------------------------------------------

class TestTokenCasing:
    @pytest.mark.parametrize("value,expected", [
        # The apostrophe cases. str.title() gives "Women'S" — the reason the
        # caser is written out rather than delegated to the stdlib.
        ("WOMEN'S HOSPITAL", "Women's Hospital"),
        ("O'BRIEN LABORATORIES", "O'Brien Laboratories"),
        # Mc prefix keeps its internal capital.
        ("MCDONALD RESEARCH", "McDonald Research"),
        # Plain upper-case street and city.
        ("450 INDUSTRIAL WAY", "450 Industrial Way"),
        ("GAINESVILLE", "Gainesville"),
        # Token level, not string level: "TECH" is cased even though "Dr" is
        # already correct, and "MS-4" carries a digit so it is left alone.
        ("500 TECH Dr MS-4", "500 Tech Dr MS-4"),
        # Hyphenated tokens are cased on each side.
        ("JEAN-YVES DUPONT", "Jean-Yves Dupont"),
    ])
    def test_text_mode(self, value, expected):
        assert normalise_case(value, mode="text") == expected

    @pytest.mark.parametrize("value,expected", [
        ("ACME CORP GMBH", "Acme Corp GmbH"),
        ("HYDRAULICS INC", "Hydraulics Inc"),
        ("STERLING INDUSTRY LLC", "Sterling Industry LLC"),
        ("BRIGHAM AND WOMEN'S HOSPITAL", "Brigham and Women's Hospital"),
        # Acronyms survive.
        ("MRI DEPARTMENT", "MRI Department"),
        ("ICB&DD MASS SPECTROMETRY", "ICB&DD Mass Spectrometry"),
        ("AT&T LABS", "AT&T Labs"),
    ])
    def test_name_mode(self, value, expected):
        assert normalise_case(value, mode="name") == expected

    @pytest.mark.parametrize("value", [
        "Massachusetts Institute of Technology",
        "Brigham and Women's Hospital",
        "Coastal Diagnostics, Inc.",
        "Hochschule fuer Technik Stuttgart",
        "McIntyre",
        "500 Tech Dr MS-4",
    ])
    def test_mixed_case_input_is_not_recased(self, value):
        """A token that already carries mixed case is intentional. Leave it."""
        assert normalise_case(value, mode="name") == value
        assert normalise_case(value, mode="text") == value

    @pytest.mark.parametrize("value", ["MS-4", "3M", "450", "24TH", "B-52"])
    def test_a_token_with_a_digit_is_untouched(self, value):
        assert normalise_case(value, mode="text") == value

    @pytest.mark.parametrize("value,expected", [
        ("COASTAL DIAGNOSTICS, INC.", "Coastal Diagnostics, Inc."),
        ("ADAMS AIR HYDRAULICS INC", "Adams Air Hydraulics Inc"),
        ("SMITH, JONES & CO.", "Smith, Jones & Co."),
        ("ST. LUKE'S MEDICAL CENTER", "St. Luke's Medical Center"),
    ])
    def test_punctuation_is_preserved_exactly(self, value, expected):
        assert normalise_case(value, mode="name") == expected

    @pytest.mark.parametrize("value", [
        "WOMEN'S HOSPITAL", "O'BRIEN LABORATORIES", "COASTAL DIAGNOSTICS, INC.",
        "500 TECH Dr MS-4", "SMITH, JONES & CO.", "  SPACED   OUT  VALUE  ",
    ])
    def test_no_character_is_added_or_removed(self, value):
        """Casing changes letter case only — same length, same characters
        case-folded, same whitespace."""
        for mode in ("name", "text"):
            out = normalise_case(value, mode=mode)
            assert len(out) == len(value)
            assert out.lower() == value.lower()

    def test_directional_prefixes_and_legal_forms_keep_their_written_form(self):
        assert normalise_case("123 NW HIGH ST", mode="text") == "123 NW High St"
        assert normalise_case("PFIZER AG", mode="name") == "Pfizer AG"
        assert normalise_case("NOVO OY", mode="name") == "Novo Oy"


# ---------------------------------------------------------------------------
# Step 3 — field coverage
# ---------------------------------------------------------------------------

class TestFieldCoverage:
    def _result(self, **over: Any) -> dict[str, Any]:
        base = _init_result(EnrichmentRecord(record_id="t", country="US"))
        return seed(base, **over)

    def test_every_free_text_output_field_is_cased(self):
        r = self._result(
            name1_enriched="ADAMS AIR", name2_enriched="HYDRAULICS INC",
            name3_enriched="SNYDER LABORATORY", name4_enriched="BUILDING C WING",
            care_of_enriched="DR EMILY CARTER", contact_enriched="CHARLES FARBER",
            email_enriched="ORDERS@MERIDIANLABS.COM",
            street_cleaned="450 INDUSTRIAL WAY", street_2_cleaned="500 TECH Dr MS-4",
            street_3_cleaned="SUITE 200 EAST TOWER", street_4_cleaned="PARK PLACE",
            street_5_cleaned="LOADING DOCK B", city="GAINESVILLE",
            po_box_extracted="PO BOX 1200",
        )
        normalise_output_fields(r)
        assert r["name1_enriched"] == "Adams Air"
        assert r["name2_enriched"] == "Hydraulics Inc"
        assert r["name3_enriched"] == "Snyder Laboratory"
        assert r["name4_enriched"] == "Building C Wing"
        assert r["care_of_enriched"] == "Dr Emily Carter"
        assert r["contact_enriched"] == "Charles Farber"
        assert r["email_enriched"] == "orders@meridianlabs.com"
        assert r["street_cleaned"] == "450 Industrial Way"
        assert r["street_2_cleaned"] == "500 Tech Dr MS-4"
        assert r["street_3_cleaned"] == "Suite 200 East Tower"
        assert r["street_4_cleaned"] == "Park Place"
        assert r["street_5_cleaned"] == "Loading Dock B"
        assert r["city"] == "Gainesville"
        assert r["po_box_extracted"] == "PO Box 1200"

    def test_code_fields_are_left_as_codes(self):
        codes = {
            "country_region_key": "US", "region": "FL", "language_key": "EN",
            "postal_code": "32611", "account_group": "DRIT",
            "ecc_customer_number": "90000167",
        }
        r = self._result(**codes)
        normalise_output_fields(r)
        for k, v in codes.items():
            assert r[k] == v

    def test_a_registry_name_is_never_recased(self):
        r = self._result(
            name1_enriched="Massachusetts Institute of Technology",
            name2_enriched="DEPARTMENT OF CHEMISTRY",
            _registry_name_fields={"name1"},
        )
        normalise_output_fields(r)
        # Byte-identical: title-casing would give "Institute Of Technology".
        assert r["name1_enriched"] == "Massachusetts Institute of Technology"
        # A non-registry name in the same record is still cased.
        assert r["name2_enriched"] == "Department of Chemistry"

    def test_registry_name_survives_a_full_finalise(self):
        r = self._result(
            name1_enriched="Brigham and Women's Hospital",
            _registry_name_fields={"name1"},
            ror_id="https://ror.org/04b6nzv94",
        )
        out = finalise(r, time.monotonic())
        assert out["name1_enriched"] == "Brigham and Women's Hospital"


# ---------------------------------------------------------------------------
# Step 4 — casing does not set a changed flag
# ---------------------------------------------------------------------------

class TestChangedFlags:
    def test_a_casing_only_difference_does_not_set_the_flag(self):
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="GAINESVILLE MEDICAL",
        ))
        seed(r, name1_enriched="GAINESVILLE MEDICAL")
        out = finalise(r, time.monotonic())
        assert out["name1_enriched"] == "Gainesville Medical"
        assert out["name1_changed"] is False

    def test_a_substantive_change_still_sets_the_flag(self):
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="Mayo Clinic FLA",
        ))
        seed(r, name1_enriched="Mayo Clinic in Florida",
             _registry_name_fields={"name1"})
        out = finalise(r, time.monotonic())
        assert out["name1_changed"] is True


# ---------------------------------------------------------------------------
# Step 1 — every exit path
# ---------------------------------------------------------------------------

class TestExitPaths:
    @pytest.mark.asyncio
    async def test_uc0_overflow_early_return_is_normalised(self):
        """Row 33: "Adams Air" + "HYDRAULICS INC" is the README's own UC 0
        example. The record still stops at Stage 0 — flagged, no tier run —
        and it is now cased on the way out."""
        ror = _CountingROR()
        r = await _run(
            _orch(ror=ror, llm=_OverflowLLM()),
            name1="Adams Air", name2="HYDRAULICS INC",
            street1="450 INDUSTRIAL WAY", city="GAINESVILLE",
        )
        # Still flagged as an overflow — only the reason text changed.
        assert r.flag_for_review is True
        assert r.flag_codes == ["overflow"]
        assert r.flagged_fields == ["name1", "name2"]
        assert r.record_type == "unknown"
        # No tier ran.
        assert ror.queries == []
        assert r.ror_id is None and r.lei_id is None
        # ...and the record is normalised anyway.
        assert r.name1_enriched == "Adams Air"
        assert r.name2_enriched == "Hydraulics Inc"
        assert r.street_cleaned == "450 Industrial Way"
        assert r.city == "Gainesville"

    @pytest.mark.asyncio
    async def test_the_normal_path_is_normalised(self):
        r = await _run(
            _orch(),
            name1="COASTAL DIAGNOSTICS, INC.",
            street1="500 TECH Dr MS-4", city="GAINESVILLE",
            email="ORDERS@MERIDIANLABS.COM", contact="CHARLES FARBER",
        )
        # The comma survives, and so does the casing of "Inc". The trailing
        # period does NOT: `strip_address_fragments` removes it during
        # preprocessing, which predates this fix and is out of its scope. Fix 5
        # only guarantees that CASING adds and removes nothing — see
        # `test_no_character_is_added_or_removed`.
        assert r.name1_enriched == "Coastal Diagnostics, Inc"
        # "MS-4" is a mail stop and is routed to its own field, so the
        # cleaned street keeps only the street itself.
        assert r.street_cleaned == "500 Tech Dr"
        assert r.mail_stop == "4"
        assert r.city == "Gainesville"
        assert r.email_enriched == "orders@meridianlabs.com"
        assert r.contact_enriched == "Charles Farber"

    @pytest.mark.asyncio
    async def test_the_error_path_is_normalised(self):
        """An orchestrator exception returns the record rather than raising.
        It funnels through `finalise`, so it is normalised too."""
        class _Boom(_CountingROR):
            async def call(self, *a, **k):
                raise RuntimeError("ROR exploded")

        r = await _run(_orch(ror=_Boom()), name1="ACME CORP GMBH",
                       city="GAINESVILLE", street1="450 INDUSTRIAL WAY")
        assert r.enrichment_status == "failed"
        assert r.error
        assert r.city == "Gainesville"
        assert r.street_cleaned == "450 Industrial Way"
        # `name1_enriched` is None here: Rule 4 passthrough restores name2-4,
        # never name1, so a record that died inside Tier 1 has no output name.
        # That predates this fix and is not casing's to solve — the point of
        # this test is that the path reaches the normaliser at all.

    @pytest.mark.asyncio
    async def test_the_batch_fail_safe_path_is_normalised(self):
        """`enrich_batch` builds a result for a record whose task raised
        outright. That path never reaches `finalise` — it calls the normaliser
        itself."""
        orch = _orch()

        async def _explode(record, options, cache):
            raise RuntimeError("task died")

        orch._enrich_single = _explode  # type: ignore[assignment]
        rec = EnrichmentRecord(record_id="t", country="US",
                               name1="ACME CORP GMBH", city="GAINESVILLE")
        resp = await orch.enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
        r = resp.results[0]
        assert r.enrichment_status == "failed"
        assert r.city == "Gainesville"

    @pytest.mark.asyncio
    async def test_no_output_field_is_fully_uppercase(self):
        """Coverage check: after the fix, the only upper-case output values are
        the code fields and genuine acronyms."""
        r = await _run(
            _orch(),
            name1="MERIDIAN LABS LLC", name2="HYDRAULICS INC",
            care_of="DR EMILY CARTER", contact="EMILY CARTER",
            email="ORDERS@MERIDIANLABS.COM",
            street1="450 INDUSTRIAL WAY", city="GAINESVILLE",
        )
        for field in ("name1_enriched", "name2_enriched", "care_of_enriched",
                      "contact_enriched", "email_enriched", "street_cleaned",
                      "city"):
            val = getattr(r, field)
            if not val:
                continue
            assert val != val.upper() or not any(c.isalpha() for c in val), (
                f"{field} is still fully upper-case: {val!r}"
            )
