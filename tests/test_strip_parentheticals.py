"""A bracketed span is never part of a name — it is dropped from every slot.

Source systems bolt a disambiguator onto the name to tell two records of the
same organisation apart: a city ("3M (Detroit)", "3M Corporate (Saint Paul)"),
a country (ROR's "Pfizer (United States)"), an acronym ("… Institute of
Technology (MIT)") or plain noise ("(guest)"). The qualifier belongs to the
SOURCE's keyspace, not to the organisation, and keeping it splits one company
across as many spellings as it has sites.

The rule these tests pin:

* the whole span goes — brackets AND contents, in every name field;
* both ends of the pipeline: preprocessing (UC 12) strips the INPUT, finalise
  strips the OUTPUT, so a value a tier introduced after preprocessing ran is
  caught too — including a registry name, which is the source that adds it;
* a name with no bracket is returned byte-identical;
* a field that is ENTIRELY bracketed is unwrapped, not emptied — the brackets
  are noise but the text inside them is all the field has.
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
from enrichment.orchestrator import Orchestrator, _init_result, finalise
from enrichment.preprocess import preprocess_record
from enrichment.tier1_ror import _extract_org_fields
from tests.conftest import seed
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.page_mock import MockPageFetcher
from utils.name_slots import ENRICHED_NAME_FIELDS
from utils.text_utils import strip_parentheticals


# ---------------------------------------------------------------------------
# Harness — a pipeline that resolves nothing, so every name ships by
# passthrough and the only thing that can have changed it is this rule.
# ---------------------------------------------------------------------------

class _NoSearch:
    async def search(self, q, num_results=5):
        return []


class _EmptyLLM:
    async def extract_json(self, s, u, **k):
        return {}

    async def aclose(self):
        pass


class _NoMatchROR:
    async def call(self, name, country_code=None, country=None,
                   city=None, state=None, **_ctx) -> dict[str, Any]:
        return {"matched": False, "score": 0.0}


def _orch() -> Orchestrator:
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": _NoMatchROR(),
        "lei": MockLEIClient(st),
        "search": _NoSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": _EmptyLLM(),
    })


async def _run(**record_kw):
    rec = EnrichmentRecord(record_id="t", country="US", **record_kw)
    resp = await _orch().enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------

class TestStripParentheticals:
    @pytest.mark.parametrize("value,expected", [
        # The reported cases: a site city appended to the company name.
        ("3M (Detroit)", "3M"),
        ("3M Corporate (Saint Paul)", "3M Corporate"),
        # ROR's country disambiguator.
        ("Pfizer (United States)", "Pfizer"),
        # An acronym restating the name it follows.
        ("Massachusetts Institute of Technology (MIT)", "Massachusetts Institute of Technology"),
        # Mid-string spans close up rather than leaving a double space.
        ("Bayer (DE) Pharma", "Bayer Pharma"),
        ("Foo(Bar)Baz", "Foo Baz"),
        # Square and curly brackets are brackets too.
        ("Siemens [Munich]", "Siemens"),
        ("Roche {Basel}", "Roche"),
        # Nested spans: removed innermost-first, so the outer one still goes.
        ("Acme (Group (EU) Holdings) Ltd", "Acme Ltd"),
        # Several spans in one value.
        ("Acme (US) Labs (East)", "Acme Labs"),
    ])
    def test_span_is_dropped(self, value, expected):
        assert strip_parentheticals(value) == expected

    @pytest.mark.parametrize("value,expected", [
        # SAP name columns are 35 characters, so the closing bracket is
        # routinely truncated away. Strip to end of string.
        ("Bayer AG (Leverkusen Werk", "Bayer AG"),
        ("Universitaetsklinikum Essen (Klin", "Universitaetsklinikum Essen"),
    ])
    def test_unclosed_bracket_is_dropped_to_end_of_string(self, value, expected):
        assert strip_parentheticals(value) == expected

    @pytest.mark.parametrize("value,expected", [
        ("Acme, (US)", "Acme"),
        ("Acme - (US)", "Acme"),
        ("Acme (US) |", "Acme"),
        # A span cut out mid-string leaves the separators that framed it
        # stranded — no orphaned space before a comma, no doubled comma.
        ("Dept. of Physics (Rm 210), Bldg 6", "Dept. of Physics, Bldg 6"),
        ("Acme, (US), Ltd", "Acme, Ltd"),
    ])
    def test_residual_punctuation_is_tidied(self, value, expected):
        assert strip_parentheticals(value) == expected

    def test_trailing_period_survives(self):
        """Load-bearing for the legal suffix — "Inc." is not "Inc" residue."""
        assert strip_parentheticals("Coastal Diagnostics, Inc. (US)") == "Coastal Diagnostics, Inc."

    @pytest.mark.parametrize("value", [
        "3M", "Massachusetts Institute of Technology", "Department of Chemistry",
        "University of California, Davis", "Smith, Jones & Co.", "  odd   spacing  ",
    ])
    def test_a_name_without_a_bracket_is_untouched(self, value):
        assert strip_parentheticals(value) is value

    @pytest.mark.parametrize("value,expected", [
        ("(Research Division)", "Research Division"),
        ("[Chemistry]", "Chemistry"),
    ])
    def test_a_fully_bracketed_value_is_unwrapped_not_emptied(self, value, expected):
        assert strip_parentheticals(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "()", "( )"])
    def test_empty_and_contentless_values(self, value):
        out = strip_parentheticals(value)
        assert out is None or not out.strip()


# ---------------------------------------------------------------------------
# Input side — preprocessing (UC 12)
# ---------------------------------------------------------------------------

class TestPreprocessStripsEveryNameSlot:
    def _pp(self, **names):
        return preprocess_record(
            name1=names.get("name1"), name2=names.get("name2"),
            name3=names.get("name3"), name4=names.get("name4"),
            name5=names.get("name5"),
            contact=None, email=None,
            street1=None, street2=None, street3=None,
        )

    def test_every_slot_is_covered_not_just_name1(self):
        res = self._pp(
            name1="3M (Detroit)",
            name2="Chemistry Department (Bldg 4)",
            name3="Mass Spectrometry Facility (MSF)",
            name4="Analytics Group (Team B)",
            name5="Sample Intake (Dock C)",
        )
        assert res.name1 == "3M"
        assert res.name2 == "Chemistry Department"
        assert res.name3 == "Mass Spectrometry Facility"
        assert res.name4 == "Analytics Group"
        assert res.name5 == "Sample Intake"

    def test_the_rule_is_noted_against_uc12(self):
        res = self._pp(name1="3M Corporate (Saint Paul)")
        assert res.name1 == "3M Corporate"
        assert 12 in res.use_cases
        assert any("parenthetical dropped from name1" in f for f in res.flags)

    def test_a_bracketed_email_still_reaches_the_email_field(self):
        """UC 12 runs after UC 8, so the email is routed before the span goes."""
        res = self._pp(name2="Purchasing Department (orders@meridianlabs.com)")
        assert res.email == "orders@meridianlabs.com"
        assert res.name2 == "Purchasing Department"

    def test_a_leading_bracketed_org_is_rescued_not_dropped(self):
        """"(Org) street" is the opposite shape to "Org (City)".

        The typist opened with the organisation and ran the address on after
        it (record shape observed in the checked workbook), so dropping the
        span would keep the street and throw the institution away.
        """
        res = self._pp(
            name1="Lehrstuhl fuer Chemie",
            name2="(Julius-Maximilians Universität Würzburg) Gieshügeler Str.46",
        )
        assert res.name2 == "Julius-Maximilians Universität Würzburg"
        assert res.street1 == "Gieshügeler Str.46"

    def test_a_leading_bracket_that_is_not_a_name_is_still_dropped(self):
        """Position alone does not rescue a span — the content has to be a
        name. "(guest)" is noise wherever it sits."""
        res = self._pp(name2="(guest) John Smith")
        assert res.name2 == "John Smith"

    @pytest.mark.parametrize("value,expected", [
        ("3M (Detroit)", "3M"),
        ("Lonza (formerly Bend Research Inc.)", "Lonza"),
        ("AURIGA POLYMERS INC. (INDORAMA VENTURES)", "AURIGA POLYMERS INC."),
    ])
    def test_a_trailing_span_is_never_rescued(self, value, expected):
        """The rescue is keyed on the bracket LEADING. A trailing span holds
        the disambiguator, however name-like it reads ("Indorama Ventures" is
        the parent company, "Bend Research Inc." the former name)."""
        assert self._pp(name1=value).name1 == expected

    def test_stripping_exposes_a_duplicate_across_slots(self):
        """"3M (Detroit)" over "3M" is one value written twice. Collapse it."""
        res = self._pp(name1="3M (Detroit)", name2="3M")
        assert res.name1 == "3M"
        assert not (res.name2 and res.name2.strip())


# ---------------------------------------------------------------------------
# Registry side — ROR's own disambiguator
# ---------------------------------------------------------------------------

class TestRorDisplayName:
    @pytest.mark.parametrize("ror_name,expected", [
        ("3M (Detroit)", "3M"),
        ("3M Corporate (Saint Paul)", "3M Corporate"),
        ("Pfizer (United States)", "Pfizer"),
    ])
    def test_display_name_is_stripped_at_extraction(self, ror_name, expected):
        fields = _extract_org_fields({
            "names": [{"value": ror_name, "types": ["ror_display"]}],
            "id": "https://ror.org/test",
        })
        assert fields["official_name"] == expected

    def test_a_campus_qualifier_is_not_a_bracket_and_survives(self):
        fields = _extract_org_fields({
            "names": [{"value": "University of California, Davis",
                       "types": ["ror_display"]}],
            "id": "https://ror.org/test",
        })
        assert fields["official_name"] == "University of California, Davis"


# ---------------------------------------------------------------------------
# Output side — end to end
# ---------------------------------------------------------------------------

class TestNoBracketReachesTheOutput:
    @pytest.mark.asyncio
    async def test_reported_records_ship_without_the_city_qualifier(self):
        r1 = await _run(name_1="3M (Detroit)", city="Detroit")
        r2 = await _run(name_1="3M Corporate (Saint Paul)", city="Saint Paul")
        assert r1.name1_enriched == "3M"
        assert r2.name1_enriched == "3M Corporate"

    @pytest.mark.asyncio
    async def test_no_enriched_name_field_ships_a_bracket(self):
        res = await _run(
            name_1="Meridian Labs (Boston)",
            name_2="Chemistry Department (Bldg 4)",
            name_3="NMR Facility (Level 2)",
            city="Boston",
        )
        for field in ENRICHED_NAME_FIELDS:
            val = getattr(res, field, None)
            if val:
                assert not any(ch in val for ch in "()[]{}"), f"{field} = {val!r}"

    def test_a_registry_name_is_stripped_too(self):
        """The abbreviation and casing passes defer to ROR/GLEIF on spelling.
        This rule does not — a bracketed qualifier is not spelling, and the
        registries are exactly the source that appends one."""
        r = _init_result(EnrichmentRecord(record_id="t", country="US", name1="3M"))
        seed(r, name1_enriched="3M (Detroit)", _registry_name_fields={"name1"})
        out = finalise(r, time.monotonic())
        assert out["name1_enriched"] == "3M"

    def test_dropping_the_span_counts_as_a_change(self):
        """The output differs from the input in more than case, so the
        `*_changed` flag must say so."""
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1="3M (Detroit)",
        ))
        seed(r, name1_enriched="3M (Detroit)")
        out = finalise(r, time.monotonic())
        assert out["name1_enriched"] == "3M"
        assert out["name1_changed"] is True
