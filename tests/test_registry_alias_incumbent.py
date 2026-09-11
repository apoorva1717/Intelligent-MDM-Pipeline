"""A record that already states one of the registry's OWN names keeps it.

The failure: ``ror.org/054962n91`` displays "Siemens Healthcare (United
States)" and carries "Siemens Healthineers" — the name the company has traded
under since 2016 — as an alias. A record saying "Siemens Healthineers" matched
that ROR record at *exact* tier and then shipped as "Siemens Healthcare",
because the registry name write took ROR's display name unconditionally.

ROR's display name is a keyspace decision, not a branding one: it is the
string ROR disambiguates its own records by, which is why it carries the
"(United States)" qualifier at all. So the rule Fix 4 established stands with
one carve-out that is *not* a second threshold — the registry still owns the
name, and the string still comes from the registry:

* the record states a name the registry publishes  → that variant ships;
* the record states anything else ("Mayo Clinic FLA") → the display name ships.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator, _preferred_registry_variant
from enrichment.tier1_ror import _extract_org_fields, _ror_name_variants
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.page_mock import MockPageFetcher


# ---------------------------------------------------------------------------
# Harness — a ROR client returning one canned org for a named query
# ---------------------------------------------------------------------------

class _NoSearch:
    async def search(self, q, num_results=5, *, country=None):
        return []


class _EmptyLLM:
    async def extract_json(self, s, u, **k):
        return {}

    async def aclose(self):
        pass


#: The live ROR v2 payload for Siemens Healthineers, trimmed to the fields the
#: extractor reads. The alias list is verbatim from the registry.
_SIEMENS_ORG: dict[str, Any] = {
    "id": "https://ror.org/054962n91",
    "types": ["company"],
    "names": [
        {"types": ["ror_display", "label"], "value": "Siemens Healthcare (United States)"},
        {"types": ["alias"], "value": "Siemens Healthineers"},
        {"types": ["alias"], "value": "Siemens Healthineers USA"},
        {"types": ["alias"], "value": "Siemens Medical Solutions USA"},
        {"types": ["alias"], "value": "Siemens Medical Solutions USA, Inc"},
    ],
    "links": [{"type": "website", "value": "https://www.siemens-healthineers.com"}],
    "locations": [{"geonames_details": {
        "country_code": "US", "country_name": "United States",
        "country_subdivision_code": "PA", "country_subdivision_name": "Pennsylvania",
        "name": "Malvern",
    }}],
    "relationships": [],
}


class _StubROR:
    def __init__(self, matches: dict[str, dict[str, Any]]) -> None:
        self.matches = matches

    async def call(self, name, country_code=None, country=None,
                   city=None, state=None, **_ctx) -> dict[str, Any]:
        org = self.matches.get(name.strip().lower())
        if org is None:
            return {"matched": False, "score": 0.0}
        fields = _extract_org_fields(org)
        return {
            "matched": True, "score": 1.0,
            **{k: v for k, v in fields.items() if k != "org_names"},
        }


def _orch(ror_client):
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": ror_client,
        "lei": MockLEIClient(st),
        "search": _NoSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": _EmptyLLM(),
    })


async def _run(orch, **record_kw):
    rec = EnrichmentRecord(record_id="t", country="US", **record_kw)
    resp = await orch.enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


# ---------------------------------------------------------------------------
# The registry's variant list
# ---------------------------------------------------------------------------

class TestNameVariants:
    def test_every_published_name_is_carried(self):
        assert _ror_name_variants(_SIEMENS_ORG) == [
            "Siemens Healthcare",          # display, bracket qualifier stripped
            "Siemens Healthineers",
            "Siemens Healthineers USA",
            "Siemens Medical Solutions USA",
            "Siemens Medical Solutions USA, Inc",
        ]

    def test_variants_travel_on_the_extracted_fields(self):
        fields = _extract_org_fields(_SIEMENS_ORG)
        assert fields["official_name"] == "Siemens Healthcare"
        assert "Siemens Healthineers" in fields["name_variants"]

    def test_display_name_leads_so_a_first_match_prefers_it(self):
        assert _ror_name_variants(_SIEMENS_ORG)[0] == "Siemens Healthcare"


# ---------------------------------------------------------------------------
# The choice itself
# ---------------------------------------------------------------------------

class TestPreferredVariant:
    VARIANTS = _ror_name_variants(_SIEMENS_ORG)

    def test_incumbent_that_is_a_published_alias_is_kept(self):
        assert _preferred_registry_variant(
            "Siemens Healthineers", "Siemens Healthcare", self.VARIANTS,
        ) == "Siemens Healthineers"

    def test_registry_spelling_wins_over_sap_casing(self):
        # The variant ships, not the incumbent — the string still comes from
        # the registry, so SAP's shouted input never reaches the output.
        assert _preferred_registry_variant(
            "SIEMENS HEALTHINEERS", "Siemens Healthcare", self.VARIANTS,
        ) == "Siemens Healthineers"

    def test_legal_form_difference_is_forgiven(self):
        # `names_match_verbatim` forgives a legal form one side omits, so the
        # record's "… USA Inc." reaches ROR's "… USA" — the first variant it
        # matches, and the one the registry states without the suffix.
        assert _preferred_registry_variant(
            "Siemens Medical Solutions USA Inc.", "Siemens Healthcare",
            self.VARIANTS,
        ) == "Siemens Medical Solutions USA"

    @pytest.mark.parametrize("incumbent", [
        "Mayo Clinic FLA",          # an abbreviation the registry corrects
        "Siemens",                  # a shorter name ROR does not publish
        "Siemens Healthineers Diagnostics",  # a name ROR does not publish
        "", None,
    ])
    def test_anything_else_takes_the_display_name(self, incumbent):
        assert _preferred_registry_variant(
            incumbent, "Siemens Healthcare", self.VARIANTS,
        ) == "Siemens Healthcare"

    def test_no_variants_is_the_old_behaviour(self):
        for variants in (None, [], [""]):
            assert _preferred_registry_variant(
                "Siemens Healthineers", "Siemens Healthcare", variants,
            ) == "Siemens Healthcare"


# ---------------------------------------------------------------------------
# An acronym variant is never kept — the display name spells it out
# ---------------------------------------------------------------------------

#: ror.org/042nb2s44, trimmed. ROR lists the acronym among the names, which is
#: what let a record saying "MIT" match it verbatim and keep the acronym.
_MIT_ORG: dict[str, Any] = {
    "id": "https://ror.org/042nb2s44",
    "types": ["education"],
    "names": [
        {"types": ["ror_display", "label"],
         "value": "Massachusetts Institute of Technology"},
        {"types": ["acronym"], "value": "MIT"},
    ],
    "links": [{"type": "website", "value": "https://web.mit.edu"}],
    "locations": [{"geonames_details": {
        "country_code": "US", "country_name": "United States",
        "country_subdivision_code": "MA",
        "country_subdivision_name": "Massachusetts", "name": "Cambridge",
    }}],
    "relationships": [],
}


class TestAcronymVariant:
    @pytest.mark.parametrize("incumbent, display, variants", [
        ("MIT", "Massachusetts Institute of Technology",
         ["Massachusetts Institute of Technology", "MIT"]),
        ("m.i.t.", "Massachusetts Institute of Technology",
         ["Massachusetts Institute of Technology", "MIT"]),
        # A historical acronym spells nothing in the current name and is
        # still an abbreviation.
        ("NBS", "National Institute of Standards and Technology",
         ["National Institute of Standards and Technology", "NIST", "NBS"]),
        # An acronym leading a longer variant, spelling the display out.
        ("UC San Diego", "University of California San Diego",
         ["University of California San Diego", "UC San Diego", "UCSD"]),
        ("NIWC Pacific", "Naval Information Warfare Center Pacific",
         ["Naval Information Warfare Center Pacific", "NIWC Pacific"]),
    ])
    def test_acronym_incumbent_takes_the_display_name(
        self, incumbent, display, variants,
    ):
        assert _preferred_registry_variant(
            incumbent, display, variants,
        ) == display

    def test_an_acronym_that_spells_nothing_in_the_name_is_kept(self):
        # "USA" is not an abbreviation of Siemens Healthcare; the variant is a
        # trading name the record states.
        assert _preferred_registry_variant(
            "Siemens Healthineers USA", "Siemens Healthcare",
            TestPreferredVariant.VARIANTS,
        ) == "Siemens Healthineers USA"

    def test_a_display_name_that_is_an_acronym_is_the_name(self):
        assert _preferred_registry_variant(
            "IBM", "IBM", ["IBM", "International Business Machines"],
        ) == "IBM"

    def test_a_shouting_registry_word_is_not_an_acronym(self):
        assert _preferred_registry_variant(
            "Bayer", "BAYER AKTIENGESELLSCHAFT",
            ["BAYER AKTIENGESELLSCHAFT", "BAYER"],
        ) == "BAYER"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_siemens_healthineers_is_not_renamed_to_siemens_healthcare(self):
        r = await _run(
            _orch(_StubROR({"siemens healthineers": _SIEMENS_ORG})),
            name1="Siemens Healthineers", city="Malvern", state="PA",
        )
        assert r.ror_id == "https://ror.org/054962n91"
        assert r.name1_enriched == "Siemens Healthineers"

    @pytest.mark.asyncio
    async def test_a_name_ror_does_not_publish_still_gets_the_display_name(self):
        """The Fix 4 rule is untouched where it was doing its job."""
        mayo = {
            "id": "https://ror.org/03zzw1w08",
            "types": ["healthcare"],
            "names": [{"types": ["ror_display", "label"],
                       "value": "Mayo Clinic in Florida"}],
            "links": [{"type": "website", "value": "https://www.mayoclinic.org"}],
            "locations": [{"geonames_details": {
                "country_code": "US", "country_name": "United States",
                "country_subdivision_code": "FL",
                "country_subdivision_name": "Florida", "name": "Jacksonville",
            }}],
            "relationships": [],
        }
        r = await _run(
            _orch(_StubROR({"mayo clinic fla": mayo})),
            name1="Mayo Clinic FLA", city="Jacksonville", state="Florida",
        )
        assert r.ror_id == "https://ror.org/03zzw1w08"
        assert r.name1_enriched == "Mayo Clinic in Florida"

    @pytest.mark.asyncio
    async def test_a_ror_hit_on_an_acronym_ships_the_full_name(self):
        r = await _run(
            _orch(_StubROR({"mit": _MIT_ORG})),
            name1="MIT", city="Cambridge", state="MA",
        )
        assert r.ror_id == "https://ror.org/042nb2s44"
        assert r.name1_enriched == "Massachusetts Institute of Technology"

    @pytest.mark.asyncio
    async def test_a_bare_parent_acronym_no_registry_matched_is_expanded(self):
        r = await _run(_orch(_StubROR({})), name1="NASA", city="Houston",
                       state="TX")
        assert r.ror_id is None
        assert r.name1_enriched == (
            "National Aeronautics and Space Administration"
        )

    @pytest.mark.asyncio
    async def test_an_acronym_with_no_known_expansion_is_left_alone(self):
        r = await _run(_orch(_StubROR({})), name1="ZKTW", city="Houston",
                       state="TX")
        assert r.name1_enriched == "ZKTW"
