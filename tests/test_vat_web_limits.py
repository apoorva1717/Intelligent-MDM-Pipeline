"""The llm_web engine's hard cost limits (config.Settings.vat_web_*).

Two tool calls per entity (the API refuses a third), the output cap and strict
JSON schema, the static prompt prefix, no retry on a model error or malformed
output, retries on HTTP 429 / 5xx (VAT_AZURE_MAX_RETRIES), the page cut to 8,000 characters with the
VAT section first, usage and cost per entity, and the run budget.
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
from vat.llm_cache import CostMeter, SearchLimits, VatLLM
from vat.lookup import LOOKUP_INCOMPLETE, NOT_FOUND, LookupInput
from vat.lookup_grounded import section_first_text
from vat.lookup_web import (
    BUDGET_EXCEEDED,
    MALFORMED_OUTPUT,
    SEARCH_LIMIT_REACHED,
    WebSearchLookup,
)
from vat.peppol import PeppolDirectory
from vat.prompts import LLM_WEB_ANSWER_SCHEMA
from vat.vies_client import VIESClient

BOSCH = LookupInput(
    customer="99000003", names=("Robert Bosch GmbH", None, None, None),
    street="Robert-Bosch-Platz", house_number="1", postal_code="70839",
    city="Gerlingen-Schillerhoehe", country="DE",
)
IMPRESSUM = "https://www.bosch.example/impressum"
QUOTE = "Umsatzsteuer-Identifikationsnummer: DE 811 128 135"
BODY = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen-Schillerhöhe " + QUOTE
ANSWER = {"vat_id": "DE811128135", "source_url": IMPRESSUM, "evidence_quote": QUOTE,
          "attributed_to": "Robert Bosch GmbH", "published_address": None}
USAGE = {"input_tokens": 9000, "cached_tokens": 4000, "cache_write_tokens": 0, "output_tokens": 500,
         "tool_calls": 2, "search_calls": 2, "other_tool_calls": 0, "status": "completed"}


def _settings(**kw):
    base = replace(get_settings(), vat_web_query_mode="model", vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                   vies_requester_vat="", cache_frozen=False, vat_web_retry_backoff_seconds=0.0)
    return replace(base, **kw)


class Script:
    """search(system, user, effort): replies in order; an Exception is raised."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    async def __call__(self, system, user, effort):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply


def _engine(tmp_path, search, *, body=BODY, **settings):
    cache = EvidenceCache(tmp_path)
    s = _settings(**settings)
    vies = VIESClient(s, cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
    llm = VatLLM(cache.namespace("vat_llm"), search=search)
    return WebSearchLookup(s, vies, llm=llm, fetcher=FakeWeb({IMPRESSUM: page(IMPRESSUM, body)}),
                           peppol=PeppolDirectory(cache, enabled=False))


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class TestDefaults:
    def test_config(self):
        s = get_settings()
        assert (s.vat_web_max_searches, s.vat_web_max_fetches, s.vat_web_page_chars,
                s.vat_web_reasoning_effort, s.vat_web_max_output_tokens) == (2, 4, 8000, "medium", 1500)

    def test_prompt_static_prefix_customer_last(self):
        a = WebSearchLookup.prompt(BOSCH, 2)
        b = WebSearchLookup.prompt(replace(BOSCH, names=("Other GmbH", None, None, None)), 2)
        head = a.split("Organisation (from our customer master data):")[0]
        assert b.startswith(head) and len(head) > 2000
        assert "at most 2 web searches, and at most 3 pages opened" in head and "opening a page is not a search" in head
        assert a.rstrip().endswith("Record status: active")


class TestApiCall:
    @pytest.mark.asyncio
    async def test_limits_are_sent_to_the_api(self, tmp_path):
        sent = {}

        class Responses:
            async def create(self, **kw):
                sent.update(kw)

                class R:
                    output_text, output, status, usage, incomplete_details = "{}", [], "completed", None, None
                return R()

        class Client:
            def with_options(self, **kw):
                sent["options"] = kw
                return self

            responses = Responses()

        llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"))
        llm._client = Client()
        limits = SearchLimits(max_searches=2, max_output_tokens=1500, schema=LLM_WEB_ANSWER_SCHEMA)
        await llm._default_search("sys", "user", "medium", limits)
        assert sent["max_tool_calls"] == 2 and sent["max_output_tokens"] == 1500
        assert sent["text"]["format"]["type"] == "json_schema" and sent["text"]["format"]["strict"]
        assert sent["reasoning"] == {"effort": "medium"} and sent["prompt_cache_key"]
        assert sent["options"]["max_retries"] == 0

    @pytest.mark.asyncio
    async def test_page_opens_have_their_own_allowance(self, tmp_path):
        # One API cap for searches + opens: 2 searches + 3 opens = 5 calls.
        sent = {}

        class Responses:
            async def create(self, **kw):
                sent.update(kw)

                class R:
                    output_text, output, status, usage, incomplete_details = "{}", [], "completed", None, None
                return R()

        class Client:
            def with_options(self, **kw):
                return self

            responses = Responses()

        llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"))
        llm._client = Client()
        await llm._default_search("sys", "user", "medium", SearchLimits(max_searches=2, max_opens=3))
        assert sent["max_tool_calls"] == 5 and sent["tools"] == [{"type": "web_search"}]
        sent.clear()
        await llm._default_search("sys", "user", "medium", SearchLimits(max_searches=0))
        assert "tools" not in sent and "max_tool_calls" not in sent  # the reader: no tools

    @pytest.mark.asyncio
    async def test_opens_do_not_count_as_searches(self, tmp_path):
        one_search_three_opens = dict(USAGE, tool_calls=4, search_calls=1, other_tool_calls=3)
        r = await _engine(tmp_path, Script((json.dumps(ANSWER), [IMPRESSUM], one_search_three_opens))
                          ).lookup(BOSCH)
        assert SEARCH_LIMIT_REACHED not in r.flags and "OPEN_LIMIT_REACHED" in r.flags

    @pytest.mark.asyncio
    async def test_more_searches_than_allowed_is_flagged(self, tmp_path):
        three = dict(USAGE, tool_calls=3, search_calls=3, other_tool_calls=0)
        r = await _engine(tmp_path, Script((json.dumps(ANSWER), [IMPRESSUM], three))).lookup(BOSCH)
        assert "SEARCH_LIMIT_EXCEEDED" in r.flags


class TestRetries:
    @pytest.mark.asyncio
    async def test_malformed_output_is_incomplete_and_not_retried(self, tmp_path):
        search = Script(("not json", [], USAGE))
        r = await _engine(tmp_path, search).lookup(BOSCH)
        assert (r.status, search.calls) == (LOOKUP_INCOMPLETE, 1)
        assert MALFORMED_OUTPUT in r.flags

    @pytest.mark.asyncio
    async def test_model_error_is_not_retried(self, tmp_path):
        search = Script(_HTTPError(400))
        r = await _engine(tmp_path, search).lookup(BOSCH)
        assert (r.status, search.calls) == (LOOKUP_INCOMPLETE, 1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["model", "hybrid"])
    async def test_a_failed_model_call_is_logged_with_azures_message(self, tmp_path, caplog, mode):
        search = Script(_HTTPError(404))
        with caplog.at_level("WARNING", logger="vat.lookup_web"):
            r = await _engine(tmp_path, search, vat_web_query_mode=mode).lookup(BOSCH)
        assert r.status.startswith(LOOKUP_INCOMPLETE) and "LLM_UNAVAILABLE" in r.flags
        assert any("failed" in m and "HTTP 404" in m for m in caplog.messages)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_retried_on_429_or_5xx(self, tmp_path, status):
        search = Script(_HTTPError(status), _HTTPError(status), (json.dumps(ANSWER), [IMPRESSUM], USAGE))
        r = await _engine(tmp_path, search).lookup(BOSCH)
        assert (r.value, search.calls, r.usage["http_retries"]) == ("DE811128135", 3, 2)

    @pytest.mark.asyncio
    async def test_never_more_than_the_retries_allowed(self, tmp_path):
        search = Script(_HTTPError(429), _HTTPError(429), (json.dumps(ANSWER), [IMPRESSUM], USAGE))
        r = await _engine(tmp_path, search, vat_azure_max_retries=1).lookup(BOSCH)
        assert (r.status, search.calls) == (LOOKUP_INCOMPLETE, 2)

    @pytest.mark.asyncio
    async def test_not_found_is_final(self, tmp_path):
        search = Script((json.dumps(dict.fromkeys(ANSWER)), [], USAGE))
        r = await _engine(tmp_path, search).lookup(BOSCH)
        assert (r.status, search.calls) == (NOT_FOUND, 1)


def _prices(tmp_path, *, web_search=0.01, input_=2.0, cached=0.2, output=10.0):
    """A price table for deployment "test-dep" (vat/costs.py), as settings."""
    path = tmp_path / "prices.yaml"
    path.write_text(
        "version: test\n"
        "deployments:\n"
        f"  test-dep: {{input_per_1m: {input_}, cached_input_per_1m: {cached}, output_per_1m: {output}}}\n"
        f"tools: {{web_search_per_call: {web_search}, page_open_per_call: {web_search}, serpapi_per_query: 0.01}}\n",
        encoding="utf-8")
    return {"vat_prices_file": str(path), "vat_price_deployment": "test-dep"}


class TestUsageAndBudget:
    @pytest.mark.asyncio
    async def test_usage_cost_and_limit_flag(self, tmp_path):
        engine = _engine(tmp_path, Script((json.dumps(ANSWER), [IMPRESSUM], USAGE)), **_prices(tmp_path))
        r = await engine.lookup(BOSCH)
        expected = (5000 * 2.0 + 4000 * 0.2 + 500 * 10.0) / 1e6 + 2 * 0.01
        assert r.usage["cost_usd"] == pytest.approx(expected, abs=1e-5)
        assert r.usage["costs"]["Total_USD"] == pytest.approx(expected, abs=1e-5)
        assert r.usage["costs"]["Azure_WebSearch_USD"] == pytest.approx(0.02)
        assert (r.usage["tool_calls"], r.usage["fetches"]) == (2, 1)
        assert SEARCH_LIMIT_REACHED in r.flags and r.decision == "AUTO_ACCEPT"

    @pytest.mark.asyncio
    async def test_budget_stops_further_calls(self, tmp_path):
        # The stop limit counts the search calls, not only the tokens.
        search = Script((json.dumps(ANSWER), [IMPRESSUM], USAGE))
        engine = _engine(tmp_path, search, **_prices(tmp_path, web_search=1.0, input_=0, cached=0, output=0),
                         vat_web_budget_usd=1.5)
        await engine.lookup(BOSCH)
        r = await engine.lookup(replace(BOSCH, names=("Other GmbH", None, None, None)))
        assert (r.status, search.calls) == (LOOKUP_INCOMPLETE, 1) and BUDGET_EXCEEDED in r.flags

    def test_cost_meter_prices_cache_writes_as_input(self):
        from vat.costs import PriceTable
        table = PriceTable.from_dict({"deployments": {"d": {"input_per_1m": 2, "cached_input_per_1m": 0.2,
                                                            "output_per_1m": 10}},
                                      "tools": {"web_search_per_call": 0, "page_open_per_call": 0,
                                                "serpapi_per_query": 0}})
        m = CostMeter(table, deployment="d")
        cost = m.cost({"input_tokens": 8163, "cached_tokens": 0, "cache_write_tokens": 4054,
                       "output_tokens": 107})
        assert cost == pytest.approx((8163 * 2 + 107 * 10) / 1e6)


class TestPageCut:
    def test_vat_section_kept_when_page_is_long(self):
        text = "Navigation " * 3000 + "Impressum Robert Bosch GmbH " + QUOTE + " Footer " * 500
        cut = section_first_text(text, 8000)
        assert QUOTE in cut and len(cut) <= 8000 + 20

    def test_short_page_unchanged(self):
        assert section_first_text("Impressum " + QUOTE, 8000) == "Impressum " + QUOTE

    @pytest.mark.asyncio
    async def test_engine_reads_the_cut_page(self, tmp_path):
        body = "Menü " * 4000 + BODY
        r = await _engine(tmp_path, Script((json.dumps(ANSWER), [IMPRESSUM], USAGE)), body=body).lookup(BOSCH)
        assert r.value == "DE811128135" and r.decision == "AUTO_ACCEPT"


# ── Code-side fetch: the model gave no number ──────────────────────────────

from vat.lookup_web import CODE_FETCH, own_domain_pages  # noqa: E402

MUELLER = LookupInput("1", ("Müller Maschinenbau GmbH", None, None, None), "Hauptstraße", "5",
                      "70839", "Gerlingen", "DE")
M_SITE = "https://www.mueller-maschinenbau.example"
M_BODY = "Impressum Müller Maschinenbau GmbH Hauptstraße 5 70839 Gerlingen USt-IdNr.: DE 811 128 135"
NULL = json.dumps(dict.fromkeys(ANSWER))


def _code_engine(tmp_path, visited, pages, **settings):
    cache = EvidenceCache(tmp_path)
    s = _settings(**settings)
    vies = VIESClient(s, cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
    search = Script((NULL, visited, USAGE))
    web = FakeWeb(pages)
    engine = WebSearchLookup(s, vies, llm=VatLLM(cache.namespace("vat_llm"), search=search),
                             fetcher=web, peppol=PeppolDirectory(cache, enabled=False))
    return engine, search, web


class TestCodeFetch:
    def test_page_order_and_third_parties(self):
        visited = ["https://www.northdata.de/Mueller+Maschinenbau", f"{M_SITE}/kontakt",
                   f"{M_SITE}/de/einkauf", f"{M_SITE}/impressum", "https://other.example/impressum"]
        assert own_domain_pages(visited, MUELLER) == [f"{M_SITE}/impressum", f"{M_SITE}/de/einkauf"]

    def test_probe_when_no_imprint_in_results(self):
        assert own_domain_pages([f"{M_SITE}/kontakt"], MUELLER) == [
            "https://www.mueller-maschinenbau.example/impressum",
            "https://www.mueller-maschinenbau.example/imprint"]

    @pytest.mark.asyncio
    async def test_reads_the_imprint_from_the_results(self, tmp_path):
        url = f"{M_SITE}/impressum"
        engine, search, web = _code_engine(tmp_path, ["https://www.northdata.de/x", url],
                                           {url: page(url, M_BODY)})
        r = await engine.lookup(MUELLER)
        assert (r.value, r.decision, r.source_url, search.calls) == ("DE811128135", "AUTO_ACCEPT", url, 1)
        assert CODE_FETCH in r.flags and "https://www.northdata.de/x" not in web.fetched

    @pytest.mark.asyncio
    async def test_probes_impressum_on_the_own_host(self, tmp_path):
        url = f"{M_SITE}/impressum"
        engine, _, web = _code_engine(tmp_path, [f"{M_SITE}/kontakt"], {url: page(url, M_BODY)})
        r = await engine.lookup(MUELLER)
        assert (r.value, r.decision) == ("DE811128135", "AUTO_ACCEPT") and web.fetched[0] == url

    @pytest.mark.asyncio
    async def test_page_with_several_ids_is_not_read(self, tmp_path):
        url = f"{M_SITE}/impressum"
        body = M_BODY + " Müller Holding GmbH USt-IdNr.: DE129273398"
        engine, _, _ = _code_engine(tmp_path, [url], {url: page(url, body)})
        assert (await engine.lookup(MUELLER)).status == NOT_FOUND

    @pytest.mark.asyncio
    async def test_entity_not_named_on_the_page_is_not_accepted(self, tmp_path):
        url = f"{M_SITE}/impressum"
        body = "Impressum Ganz Andere GmbH Hauptstraße 5 70839 Gerlingen USt-IdNr.: DE 811 128 135"
        engine, _, _ = _code_engine(tmp_path, [url], {url: page(url, body)})
        r = await engine.lookup(MUELLER)
        assert r.decision != "AUTO_ACCEPT"

    @pytest.mark.asyncio
    async def test_fetch_limit_holds(self, tmp_path):
        visited = [f"{M_SITE}/impressum", f"{M_SITE}/de/impressum", f"{M_SITE}/en/imprint",
                   f"{M_SITE}/legal", f"{M_SITE}/einkauf"]
        engine, _, web = _code_engine(tmp_path, visited, {}, vat_web_max_fetches=3)
        r = await engine.lookup(MUELLER)
        assert r.status == NOT_FOUND and len(web.fetched) == 3 and r.usage["fetches"] == 3


class TestCodeFetchAttribution:
    @pytest.mark.asyncio
    async def test_brand_mention_on_another_entitys_imprint_is_not_accepted(self, tmp_path):
        # k-labor GmbH: dekra.k-labor.de/impressum is DEKRA's legal notice.
        inp = LookupInput("1", ("k-labor GmbH", None, None, None), "Unidekstr", "1", "75015", "Bretten", "DE")
        url = "https://dekra.k-labor.de/impressum/"
        body = ("Impressum k-labor Labor für Kunststoffe Unidekstr 1 75015 Bretten. Anbieter: DEKRA "
                "Automobil GmbH, Amtsgericht Stuttgart, HRB-Nr. 21039 "
                "Umsatzsteuer-Identifikationsnummer: DE 811 297 970")
        engine, _, _ = _code_engine(tmp_path, [url], {url: page(url, body)})
        r = await engine.lookup(inp)
        assert r.decision != "AUTO_ACCEPT"
