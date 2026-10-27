"""LLM_WEB — the model searches the web; never from memory (vat/lookup_web.py).

The web search is a double that returns a fixed answer and visited-URL list;
the web, VIES and the cache are the same doubles as the grounded tests. Each
test gets its own evidence cache under tmp_path. Company data: the fixture's
public Impressum IDs, or obviously fake.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.llm_cache import VatLLM, visited_urls
from vat.lookup import FOUND_UNVERIFIED, FOUND_VERIFIED, LOOKUP_INCOMPLETE, NOT_FOUND, LookupInput
from vat.lookup_engines import make_lookup
from vat.lookup_web import (
    ANSWER_WITHOUT_SOURCE,
    LLM_WEB_NO_ANSWER,
    LLM_WEB_SEARCH,
    NOT_ON_SOURCE_PAGE,
    REASONING_EFFORT,
    SOURCE_NOT_VISITED,
    SOURCE_UNREADABLE,
    WebSearchLookup,
    was_visited,
)
from vat.vies_client import VIESClient

BOSCH = LookupInput(
    customer="99000003", names=("Robert Bosch GmbH", None, None, None),
    street="Robert-Bosch-Platz", house_number="1", postal_code="70839",
    city="Gerlingen-Schillerhoehe", country="DE",
)
IMPRESSUM = "https://www.bosch.example/impressum"
QUOTE = "Umsatzsteuer-Identifikationsnummer: DE 811 128 135"
BODY = ("Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen-Schillerhöhe "
        "Registergericht: Amtsgericht Stuttgart, HRB 14000 " + QUOTE)


def answer(vat_id="DE811128135", source=IMPRESSUM, quote=QUOTE, attributed="Robert Bosch GmbH"):
    return {"vat_id": vat_id, "source_url": source, "evidence_quote": quote,
            "attributed_to": attributed}


class FakeSearch:
    """``search(system, user, effort)`` → (JSON text, visited URLs)."""

    def __init__(self, reply=None, visited=(IMPRESSUM,), error=None):
        self.reply, self.visited, self.error = reply or answer(), list(visited), error
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, system, user, effort):
        self.calls.append((system, user, effort))
        if self.error:
            raise self.error
        return json.dumps(self.reply), self.visited


def _settings():
    return replace(get_settings(), vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                   vies_requester_vat="", cache_frozen=False)


def _engine(tmp_path, search=None, web=None, frozen=False, vies_reply=None):
    cache = EvidenceCache(tmp_path, frozen=frozen)
    vies = VIESClient(_settings(), cache=cache,
                      transport=ViesStub(vies_reply or vies_mock.echo_valid_undisclosed).transport)
    search = search or FakeSearch()
    engine = WebSearchLookup(
        _settings(), vies,
        llm=VatLLM(cache.namespace("vat_llm"), search=search),
        fetcher=web if web is not None else FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY)}),
    )
    return engine, search


class TestFound:
    @pytest.mark.asyncio
    async def test_found_on_the_cited_page_is_proposed(self, tmp_path):
        engine, search = _engine(tmp_path)
        r = await engine.lookup(BOSCH)
        assert (r.mode, r.status, r.value, r.source_url, r.tier) == (
            "llm_web", FOUND_VERIFIED, "DE811128135", IMPRESSUM, "PROPOSED")
        assert LLM_WEB_SEARCH in r.flags and "SEARCH_VISITED:1" in r.flags
        assert "ATTRIBUTION_MATCH" in r.flags and "ADDRESS_MATCH" in r.flags
        assert search.calls[0][2] == REASONING_EFFORT == "high"
        assert "70839" in search.calls[0][1]  # the model is given the address to search with

    @pytest.mark.asyncio
    async def test_same_name_other_town_is_review(self, tmp_path):
        # The ACL case: name matches, the cited page is in another town.
        acl = LookupInput("13141598", ("ACL GmbH", None, None, None), None, None,
                          "72108", "ROTTENBURG", "DE")
        url = "https://www.acl.example/de/unternehmen/impressum"
        quote = "USt-IdNr.: DE 191570067"
        engine, _ = _engine(
            tmp_path, search=FakeSearch(answer("DE191570067", url, quote, "ACL GmbH"), visited=[url]),
            web=FakeWeb({url: page(url, "ACL GmbH Apelsteinallee 5 04416 Markkleeberg " + quote)}))
        r = await engine.lookup(acl)
        assert "ADDRESS_MISMATCH" in r.flags and r.tier == "REVIEW"


class TestNeverFromMemory:
    @pytest.mark.asyncio
    async def test_a_number_without_a_page_is_dropped(self, tmp_path):
        engine, _ = _engine(tmp_path, search=FakeSearch(answer(source=None)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value, r.tier) == (NOT_FOUND, None, None)
        assert ANSWER_WITHOUT_SOURCE in r.flags

    @pytest.mark.asyncio
    async def test_a_number_that_is_not_on_the_cited_page_is_dropped(self, tmp_path):
        # The page is real and visited, but the number came from elsewhere.
        other = "Umsatzsteuer-Identifikationsnummer: DE 129 274 202"
        engine, _ = _engine(tmp_path, search=FakeSearch(answer("DE129274202", quote=other)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value) == (NOT_FOUND, None)
        assert r.flags[:2] == (NOT_ON_SOURCE_PAGE, "QUOTE_NOT_IN_PAGE")

    @pytest.mark.asyncio
    async def test_quote_paraphrased_but_number_on_page_is_kept_for_review(self, tmp_path):
        engine, _ = _engine(tmp_path, search=FakeSearch(answer(quote="USt-IdNr. DE811128135")))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value, r.tier) == (FOUND_UNVERIFIED, "DE811128135", "REVIEW")
        assert r.flags[0] == "QUOTE_NOT_IN_PAGE"

    @pytest.mark.asyncio
    async def test_a_source_the_search_never_visited_is_review(self, tmp_path):
        engine, _ = _engine(tmp_path, search=FakeSearch(visited=["https://elsewhere.example/"]))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value, r.tier) == (FOUND_VERIFIED, "DE811128135", "REVIEW")
        assert SOURCE_NOT_VISITED in r.flags

    @pytest.mark.asyncio
    async def test_visited_but_unreadable_here_is_kept_for_review(self, tmp_path):
        engine, _ = _engine(tmp_path, web=FakeWeb({IMPRESSUM: 403}))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value, r.tier) == (FOUND_UNVERIFIED, "DE811128135", "REVIEW")
        assert r.flags[0] == SOURCE_UNREADABLE and "PAGE_UNAVAILABLE:403" in r.flags

    @pytest.mark.asyncio
    async def test_unvisited_and_unreadable_is_dropped(self, tmp_path):
        engine, _ = _engine(tmp_path, search=FakeSearch(visited=[]), web=FakeWeb({IMPRESSUM: 403}))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value) == (NOT_FOUND, None)
        assert {SOURCE_UNREADABLE, SOURCE_NOT_VISITED, "PAGE_UNAVAILABLE:403"} <= set(r.flags)

    @pytest.mark.asyncio
    async def test_no_answer(self, tmp_path):
        engine, _ = _engine(tmp_path, search=FakeSearch({"vat_id": None, "source_url": None}))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value) == (NOT_FOUND, None) and LLM_WEB_NO_ANSWER in r.flags


class TestFailuresAndCache:
    @pytest.mark.asyncio
    async def test_model_failure_is_incomplete(self, tmp_path):
        engine, _ = _engine(tmp_path, search=FakeSearch(error=RuntimeError("403 model_not_found")))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags) == (LOOKUP_INCOMPLETE, (LLM_WEB_SEARCH, "LLM_UNAVAILABLE"))

    @pytest.mark.asyncio
    async def test_frozen_replay_reproduces_the_search(self, tmp_path):
        live, search = _engine(tmp_path)
        first = await live.lookup(BOSCH)
        frozen, replay = _engine(tmp_path, frozen=True, search=FakeSearch(error=AssertionError("called")))
        second = await frozen.lookup(BOSCH)
        assert (second.value, second.tier, second.flags) == (first.value, first.tier, first.flags)
        assert replay.calls == [] and frozen.stats()["llm_cache_hits"] == 1

    @pytest.mark.asyncio
    async def test_frozen_miss_is_incomplete(self, tmp_path):
        engine, search = _engine(tmp_path, frozen=True)
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags[-1]) == (LOOKUP_INCOMPLETE, "FROZEN_CACHE_MISS")
        assert search.calls == []


class TestHelpers:
    def test_visited_urls_from_search_and_opened_pages(self):
        output = [
            {"type": "reasoning"},
            {"type": "web_search_call", "action": {"type": "search", "query": "q", "sources": [
                {"type": "url", "url": "https://a.example/impressum"},
                {"type": "url", "url": "https://b.example/"}]}},
            {"type": "web_search_call", "action": {"type": "open_page", "url": "https://a.example/impressum"}},
            {"type": "message", "content": []},
        ]
        assert visited_urls(output) == ["https://a.example/impressum", "https://b.example/"]

    @pytest.mark.parametrize("url", [
        "https://bosch.example/impressum/", "http://www.bosch.example/impressum?x=1",
        "https://WWW.Bosch.example/impressum#top",
    ])
    def test_same_page_however_written(self, url):
        assert was_visited(url, [IMPRESSUM])

    def test_other_path_is_not_the_same_page(self):
        assert not was_visited("https://www.bosch.example/kontakt", [IMPRESSUM])

    @pytest.mark.asyncio  # VIESClient needs a running loop on Python 3.9
    async def test_factory_builds_it(self, tmp_path):
        vies = VIESClient(_settings(), cache=EvidenceCache(tmp_path),
                          transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
        assert isinstance(make_lookup("llm_web", _settings(), vies), WebSearchLookup)
