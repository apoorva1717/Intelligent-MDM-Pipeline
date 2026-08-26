"""Orchestrator Stage 2b guard: a person-only Name 1 gets its institution from a
web lookup, but ONLY when ROR confirms it in the record's country.

  * confirmed  → Name 1 = ROR's official name, ROR's domain, flagged "verify".
  * wrong country → NOT accepted; Name 1 empty, flagged; Tier 3 never fabricates.

Uses a title-prefixed person so detection is deterministic (no LLM verdict
needed), a fake search that always returns a hit, a routing fake LLM that
answers the affiliation prompt, and a country-aware ROR that enforces the
country filter the real ROR applies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.lei_mock import MockLEIClient
from search.base import SearchResult


class _CountryAwareROR(MockRORClient):
    """Simulate ROR's country filter: an org only matches in its own country."""

    _DB = {
        "massachusetts institute of technology": ("US", {
            "score": 0.97, "ror_id": "https://ror.org/042nb2s44",
            "official_name": "Massachusetts Institute of Technology",
            "domain": "mit.edu", "website": "https://web.mit.edu",
            "org_types": ["education"], "is_research_institution": True,
            "country": "United States",
        }),
        "university of galway": ("IE", {
            "score": 0.95, "ror_id": "https://ror.org/03bea9k73",
            "official_name": "University of Galway",
            "domain": "universityofgalway.ie",
            "website": "https://www.universityofgalway.ie",
            "org_types": ["education"], "is_research_institution": True,
            "country": "Ireland",
        }),
    }

    async def call(self, name, country_code=None, country=None, city=None,
                   state=None, **_ctx):
        key = name.strip().lower()
        for k, (cc, data) in self._DB.items():
            if k in key or key in k:
                if country_code and cc != country_code:
                    return {"matched": False, "score": 0.0}
                return {"matched": True, **data}
        return {"matched": False, "score": 0.0}


class _FakeSearch:
    def __init__(self, hits):
        self._hits = hits

    async def search(self, query, num_results=5, *, country=None):
        return self._hits


class _RoutingLLM:
    """Answers the affiliation prompt; returns nothing for any other prompt."""

    def __init__(self, affiliation):
        self._affiliation = affiliation

    async def extract_json(self, system, user, **kw):
        if "employer" in system.lower():
            return self._affiliation
        return {}

    async def aclose(self):
        pass


def _orch(affiliation, hits):
    settings = Settings()
    return Orchestrator(settings, mock_clients={
        "ror": _CountryAwareROR(settings),
        "lei": MockLEIClient(settings),
        "search": _FakeSearch(hits),
        "page_fetcher": MockPageFetcher(),
        "llm": _RoutingLLM(affiliation),
    })


class TestAffiliationConfirmed:
    @pytest.mark.asyncio
    async def test_confirmed_sets_ror_name_and_domain(self):
        hits = [SearchResult(
            title="Dr. Jane Smith — MIT",
            url="https://mit.edu/chemistry/jane-smith",
            snippet="Jane Smith is a professor at the Massachusetts Institute "
                    "of Technology, Department of Chemistry.",
        )]
        affil = {
            "institution": "Massachusetts Institute of Technology",
            "department": "Department of Chemistry",
            "confidence": "high",
        }
        rec = EnrichmentRecord(
            record_id="A1", name1="Dr. Jane Smith", name2=None,
            city="Cambridge", state="MA", country="US",
        )
        r = (await _orch(affil, hits).enrich_batch(
            [rec], EnrichmentOptions(max_concurrency=1))).results[0]

        assert r.contact_enriched == "Dr. Jane Smith"
        # Name 1 = ROR's official name, ROR's domain — never a guessed one.
        assert r.name1_enriched == "Massachusetts Institute of Technology"
        assert r.domain == "mit.edu"
        assert r.name2_enriched == "Department of Chemistry"
        # A verified Tier 1 match: ROR's name, id and domain, through ROR's
        # own country and distinctive-token guards. Nothing to review.
        assert r.flag_for_review is False
        assert r.flag_codes == []


class TestAffiliationWrongCountry:
    @pytest.mark.asyncio
    async def test_wrong_country_rejected_not_fabricated(self):
        # Person in Belfast, GB; the web proposes an Irish university.
        hits = [SearchResult(
            title="Madhurima Chaudhuri — University of Galway",
            url="https://universityofgalway.ie/people/mchaudhuri",
            snippet="Madhurima Chaudhuri, University of Galway.",
        )]
        affil = {
            "institution": "University of Galway",
            "department": None,
            "confidence": "high",
        }
        rec = EnrichmentRecord(
            record_id="B1", name1="Dr. Madhurima Chaudhuri", name2=None,
            city="Belfast", country="GB",
        )
        r = (await _orch(affil, hits).enrich_batch(
            [rec], EnrichmentOptions(max_concurrency=1))).results[0]

        assert r.contact_enriched == "Dr. Madhurima Chaudhuri"
        assert r.name1_enriched is None       # not accepted, not fabricated
        assert r.name2_enriched is None
        assert r.flag_codes == ["person-unresolved"]
        assert r.flagged_fields == ["name1"]


class TestAffiliationNotFound:
    @pytest.mark.asyncio
    async def test_no_results_flags_manual_lookup(self):
        affil = {"institution": None, "confidence": "low"}
        rec = EnrichmentRecord(
            record_id="C1", name1="Dr. Nobody Atall", name2=None,
            city="Nowhere", country="US",
        )
        r = (await _orch(affil, []).enrich_batch(
            [rec], EnrichmentOptions(max_concurrency=1))).results[0]

        assert r.contact_enriched == "Dr. Nobody Atall"
        assert r.name1_enriched is None
        assert r.flag_codes == ["person-unresolved"]
        assert r.flagged_fields == ["name1"]
