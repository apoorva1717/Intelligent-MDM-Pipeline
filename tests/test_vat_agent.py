"""Step AG: the agentic pass (vat/lookup_agent.py). The model is faked; the
gates after it are the hybrid path's. Company data: public Impressum data
from the fixtures, or obviously fake."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from tests.test_vat_hybrid import BOSCH, IMPRESSUM, QUOTE, READ_USAGE, _settings, found
from utils.cache import EvidenceCache
from vat.llm_cache import VatLLM
from vat.lookup import AUTO_ACCEPT, CONF_LOW, MANUAL_REVIEW, NOT_FOUND, VALIDATION_PENDING, vies_pending
from vat.lookup_agent import (
    AGENT_HOST_NOT_ACCEPTED, AGENT_SOURCE_UNREADABLE, AGENT_SYSTEM_PROMPT, MAX_OPENS, MAX_SEARCHES, AgentLookup,
)
from vat.peppol import PeppolDirectory
from vat.prompts import LLM_WEB_READ_SYSTEM_PROMPT
from vat.vies_client import VIESClient

BODY = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen " + QUOTE
AGENT_USAGE = {"input_tokens": 40000, "cached_tokens": 0, "output_tokens": 3000, "reasoning_tokens": 2000,
               "tool_calls": 7, "search_calls": 3, "other_tool_calls": 4}
CTX = {BOSCH.customer: {"name_class": "CONTINUATION", "search_name": "Robert Bosch GmbH", "af_class": "E",
                        "af_why": "own Impressum read", "hosts_tried": ["www.bosch.example (own): no number"],
                        "sibling_vat_ids": ["DE811128135"]}}


def agent_answer(vat="DE811128135", url=IMPRESSUM, quote=QUOTE, name="Robert Bosch GmbH", relation="same_entity"):
    return {"vat_id": vat, "source_url": url, "evidence_quote": quote, "attributed_to": name,
            "published_address": None, "confidence": "high", "relation": relation,
            "pages_opened": [url] if url else [], "notes": None}


class Agent:
    def __init__(self, answer, visited=(IMPRESSUM,), reads=()):
        self.answer, self.visited, self.reads = answer, list(visited), list(reads)
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, system, user, effort):
        self.calls.append((system, user, effort))
        if system == AGENT_SYSTEM_PROMPT:
            return json.dumps(self.answer), self.visited, dict(AGENT_USAGE)
        assert system == LLM_WEB_READ_SYSTEM_PROMPT, system[:60]
        return json.dumps(self.reads.pop(0) if self.reads else found()), [], READ_USAGE


def _engine(tmp_path, model, pages):
    cache = EvidenceCache(tmp_path)
    s = _settings()
    vies = VIESClient(s, cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
    return AgentLookup(s, vies, context=CTX, llm=VatLLM(cache.namespace("vat_llm"), search=model),
                       fetcher=FakeWeb(pages), peppol=PeppolDirectory(cache, enabled=False))


class TestAgent:
    def test_prompt_carries_the_budget_the_record_and_the_context(self, tmp_path):
        e = _engine(tmp_path, Agent(agent_answer()), {})
        from vat.name_prep import prepare
        text = e.agent_prompt(BOSCH, prepare(BOSCH.names))
        assert f"at most {MAX_SEARCHES} web searches" in text and f"at most {MAX_OPENS} pages" in text
        assert "Name 1: Robert Bosch GmbH" in text and "Gerlingen" in text.title()
        assert "Name-2 class: CONTINUATION" in text and "(class E)" in text
        assert "- www.bosch.example (own): no number" in text and "records of this entity: DE811128135" in text
        assert "own knowledge" in text and "renamed" in text

    @pytest.mark.asyncio
    async def test_a_number_the_gates_confirm(self, tmp_path):
        model = Agent(agent_answer())
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.decision == AUTO_ACCEPT
        assert [c[2] for c in model.calls][0] == "medium"
        assert r.usage["agent_searches"] == 3 and r.usage["agent_opens"] == 4
        assert r.usage["costs"]["by_call_kind"]["agent"] > 0 if "by_call_kind" in r.usage["costs"] else True
        p = vies_pending(r)
        assert p.validation_status == VALIDATION_PENDING and p.decision == AUTO_ACCEPT

    @pytest.mark.asyncio
    async def test_an_unfetchable_page_is_low(self, tmp_path):
        r = await _engine(tmp_path, Agent(agent_answer()), {}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.decision == MANUAL_REVIEW and r.confidence == CONF_LOW
        assert "EVIDENCE_NOT_VERIFIED" in r.all_issues and AGENT_SOURCE_UNREADABLE in r.flags

    @pytest.mark.asyncio
    async def test_a_number_not_on_the_page_is_low(self, tmp_path):
        model = Agent(agent_answer(vat="DE129274235", quote="USt-IdNr. DE129274235"),
                      reads=[found(None, None, None, None)])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(BOSCH)
        assert r.value == "DE129274235" and r.confidence == CONF_LOW and "EVIDENCE_NOT_VERIFIED" in r.all_issues
        assert "PAGE_STATES:DE811128135" in r.flags

    @pytest.mark.asyncio
    async def test_no_answer(self, tmp_path):
        r = await _engine(tmp_path, Agent(agent_answer(None, None, None, None, "not_found")), {}).lookup(BOSCH)
        assert r.status == NOT_FOUND and r.value is None

    @pytest.mark.asyncio
    async def test_a_renamed_entity_is_a_successor_candidate(self, tmp_path):
        url = "https://www.neuname.example/impressum"
        body = "Impressum Neuname Industrietechnik GmbH Robert-Bosch-Platz 1 70839 Gerlingen " + QUOTE
        model = Agent(agent_answer(url=url, name="Neuname Industrietechnik GmbH", relation="renamed"),
                      visited=(url,), reads=[found(url=url, name="Neuname Industrietechnik GmbH")])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.decision == MANUAL_REVIEW
        assert "SUCCESSOR_CANDIDATE" in r.all_issues

    @pytest.mark.asyncio
    async def test_a_host_the_gates_did_not_accept_is_review(self, tmp_path):
        # Another company's Impressum: the host check never accepts it; the
        # agent's reading is judged on the page and stays a review candidate.
        url = "https://www.fremd.example/impressum"
        body = "Impressum Fremd Holding AG, Musterweg 1, 12345 Musterstadt " + QUOTE
        model = Agent(agent_answer(url=url), visited=(url,), reads=[found(url=url)])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(replace(BOSCH))
        assert r.value == "DE811128135" and r.decision != AUTO_ACCEPT
        assert AGENT_HOST_NOT_ACCEPTED in r.flags


# ── The agent pass after the lookup (validate_rows, the endpoints) ──────────

from vat.lookup import NO_VAT_INFERRED, FoundResult as _FR  # noqa: E402
from vat.lookup_agent import AGENT_TRIED, agent_eligible  # noqa: E402
from vat.validate import validate_rows  # noqa: E402


class _Hybrid:
    """The lookup: a number for Bosch-like names, a reason-coded NOT_FOUND,
    or NO_VAT_INFERRED for a public body."""

    mode = "llm_web"
    agent_version = "hybrid"

    async def lookup(self, inp):
        name = inp.name_1 or ""
        if name.startswith("Polizei"):
            return _FR("llm_web", NO_VAT_INFERRED)
        if name.startswith("Gefunden"):
            return _FR("llm_web", "FOUND_VERIFIED", "DE129274235", source_url="https://x.example/impressum")
        return _FR("llm_web", "NOT_FOUND:NO_NUMBER_ON_OWN_SITE", usage={
            "name_class": "OTHER", "search_name": name,
            "hosts": {"bosch.example": {"kind": "own", "answered": True, "fetched": [IMPRESSUM]}}})

    def stats(self):
        return {}

    async def aclose(self):
        pass


def _rows():
    base = {"Country/Region Key": "DE", "Street": "Robert-Bosch-Platz", "House Number": "1",
            "Postal Code": "70839", "City": "GERLINGEN"}
    return [
        {**base, "Customer": BOSCH.customer, "Name": "Robert Bosch GmbH", "VAT Registration No.": None},
        # a sibling record of the same entity that has a number in SAP
        {**base, "Customer": "2", "Name": "Robert Bosch GmbH", "VAT Registration No.": "DE811128135"},
        {**base, "Customer": "3", "Name": "Polizeipräsidium Musterstadt", "VAT Registration No.": None},
        {**base, "Customer": "4", "Name": "Gefunden GmbH", "VAT Registration No.": None},
    ]


class TestAgentPass:
    def test_who_is_eligible(self):
        assert agent_eligible(_FR("llm_web", "NOT_FOUND:OWN_SITE_BLOCKED"))
        assert agent_eligible(_FR("llm_web", "LOOKUP_INCOMPLETE:HTTP_403"))
        assert agent_eligible(_FR("llm_web", "NOT_FOUND"))
        assert not agent_eligible(_FR("llm_web", NO_VAT_INFERRED))
        assert not agent_eligible(_FR("llm_web", "NOT_FOUND_NO_VAT_EXPECTED:STATED"))
        assert not agent_eligible(_FR("llm_web", "FOUND_UNVERIFIED", "DE811128135"))
        assert not agent_eligible(None)

    @pytest.mark.asyncio
    async def test_only_the_not_found_rows_and_with_context(self, tmp_path):
        model = Agent(agent_answer())
        agent = _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)})
        cache = EvidenceCache(tmp_path / "v")
        vies = VIESClient(_settings(), cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
        out, summary = await validate_rows(_rows(), vies, lookup=_Hybrid(), agent=agent)
        agent_calls = [c for c in model.calls if c[0] == AGENT_SYSTEM_PROMPT]
        assert len(agent_calls) == 1                                       # Bosch only
        prompt = agent_calls[0][1]
        assert "records of this entity: DE811128135" in prompt            # the sibling's number
        assert "bosch.example (own)" in prompt and "Name-2 class: OTHER" in prompt
        bosch, _, police, found_ = out
        assert bosch["VAT_Found_Value"] == "DE811128135" and "AGENT" in bosch["VAT_Found_Flags"]
        assert bosch["VAT_Found_Validation_Status"] == "PENDING"           # VIES via the queue
        assert police["VAT_Found_Status"] == NO_VAT_INFERRED and found_["VAT_Found_Value"] == "DE129274235"
        assert summary["lookup"]["agent_pass"]["rows_eligible"] == 1
        assert summary["lookup"]["agent_pass"]["found"] == 1
        assert bosch["VAT_Found_Total_USD"] > 0                             # the agent's calls are on the row

    @pytest.mark.asyncio
    async def test_nothing_found_keeps_the_lookups_result(self, tmp_path):
        model = Agent(agent_answer(None, None, None, None, "not_found"))
        agent = _engine(tmp_path, model, {})
        vies = VIESClient(_settings(), cache=EvidenceCache(tmp_path / "v"),
                          transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
        out, _ = await validate_rows(_rows()[:1], vies, lookup=_Hybrid(), agent=agent)
        assert out[0]["VAT_Found_Status"] == "NOT_FOUND:NO_NUMBER_ON_OWN_SITE"
        assert AGENT_TRIED in out[0]["VAT_Found_Flags"]

    @pytest.mark.asyncio
    async def test_the_agent_budget_stops_it(self, tmp_path):
        model = Agent(agent_answer())
        agent = _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)})
        agent.meter.budget = 0.0001
        agent.meter.spent = 1.0
        vies = VIESClient(_settings(), cache=EvidenceCache(tmp_path / "v"),
                          transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
        out, summary = await validate_rows(_rows()[:1], vies, lookup=_Hybrid(), agent=agent)
        assert not [c for c in model.calls if c[0] == AGENT_SYSTEM_PROMPT]
        assert "AGENT_SKIPPED_BUDGET" in out[0]["VAT_Found_Flags"] and summary["lookup"]["agent_pass"]["skipped_budget"] == 1
