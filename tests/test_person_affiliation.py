"""Unit tests for the Stage 2b person-affiliation proposer.

The module only PROPOSES an institution from grounded search snippets; the
orchestrator confirms it against ROR (see test_person_affiliation_guard).
Here we test the proposer in isolation with fake search/LLM clients.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings
from enrichment.person_affiliation import (
    _build_queries,
    _email_domain,
    run_person_affiliation,
)
from search.base import SearchResult


class _FakeSearch:
    def __init__(self, results=None):
        self._results = results or []
        self.queries: list[str] = []

    async def search(self, query, num_results=5, *, country=None):
        self.queries.append(query)
        return self._results


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    async def extract_json(self, system, user, **kw):
        self.calls += 1
        self._last_user = user
        return self._payload

    async def aclose(self):
        pass


_ST = Settings()


def _hit(title="Prof. Jane Smith — MIT", url="https://mit.edu/jane",
         snippet="Jane Smith is a professor in the Department of Chemistry at "
                 "the Massachusetts Institute of Technology."):
    return SearchResult(title=title, url=url, snippet=snippet)


class TestEmailDomain:
    def test_extracts_domain(self):
        assert _email_domain("j.smith@mit.edu") == "mit.edu"

    def test_none_for_blank(self):
        assert _email_domain(None) is None
        assert _email_domain("not-an-email") is None


class TestBuildQueries:
    def test_corporate_email_domain_queried_first(self):
        qs = _build_queries("Jane Smith", "Boston", "MA", "US", "j@mit.edu")
        assert qs[0] == '"Jane Smith" mit.edu'

    def test_freemail_domain_skipped(self):
        qs = _build_queries("Jane Smith", "Boston", "MA", "US", "jane@gmail.com")
        assert not any("gmail.com" in q for q in qs)

    def test_location_query_included(self):
        qs = _build_queries("Jane Smith", "Boston", "MA", "US", None)
        assert any("Boston, MA, US" in q for q in qs)


class TestRunPersonAffiliation:
    @pytest.mark.asyncio
    async def test_proposes_from_snippets(self):
        search = _FakeSearch([_hit()])
        llm = _FakeLLM({
            "institution": "Massachusetts Institute of Technology",
            "department": "Department of Chemistry",
            "confidence": "high",
        })
        out = await run_person_affiliation(
            contact="Jane Smith", city="Boston", region="MA", country="US",
            email=None, search_client=search, llm_client=llm, settings=_ST,
        )
        assert out.institution == "Massachusetts Institute of Technology"
        assert out.department == "Department of Chemistry"
        assert out.confidence == "high"

    @pytest.mark.asyncio
    async def test_no_results_returns_empty(self):
        search = _FakeSearch([])           # nothing found
        llm = _FakeLLM({"institution": "Should Not Be Used"})
        out = await run_person_affiliation(
            contact="Nobody Atall", city=None, region=None, country="US",
            email=None, search_client=search, llm_client=llm, settings=_ST,
        )
        assert out.institution is None
        assert llm.calls == 0              # LLM not even called without snippets

    @pytest.mark.asyncio
    async def test_llm_null_institution_returns_empty(self):
        search = _FakeSearch([_hit()])
        llm = _FakeLLM({"institution": None, "confidence": "low"})
        out = await run_person_affiliation(
            contact="Jane Smith", city="Boston", region="MA", country="US",
            email=None, search_client=search, llm_client=llm, settings=_ST,
        )
        assert out.institution is None

    @pytest.mark.asyncio
    async def test_search_error_is_swallowed(self):
        class _BoomSearch:
            async def search(self, query, num_results=5, *, country=None):
                raise RuntimeError("network down")
        out = await run_person_affiliation(
            contact="Jane Smith", city="Boston", region="MA", country="US",
            email=None, search_client=_BoomSearch(),
            llm_client=_FakeLLM({}), settings=_ST,
        )
        assert out.institution is None
