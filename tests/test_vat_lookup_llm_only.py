"""Mode B — LLM-only lookup (vat/lookup_llm_only.py). EXPERIMENT ONLY.

The model is a scripted double; VIES is the scripted stub. These tests pin
that the mode can never report a guess as verified, whatever VIES says.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

import vat.lookup_llm_only as llm_only_module
from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.llm_cache import VatLLM
from vat.lookup import FOUND_VERIFIED, LOOKUP_INCOMPLETE, LookupInput
from vat.lookup_llm_only import EXPERIMENT_FLAG, LlmOnlyLookup
from vat.validate import validate_rows
from vat.vies_client import VIESClient

BASF = LookupInput(
    customer="99000005", names=("BASF SE", None, None, None),
    street="Carl-Bosch-Strasse", house_number="38", postal_code="67056",
    city="Ludwigshafen", country="DE",
)


class _Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[tuple[str, str]] = []

    async def __call__(self, system, user, max_tokens):
        self.prompts.append((system, user))
        answer = self.answers[min(len(self.prompts), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)


def _engine(tmp_path, model, *, vies_reply=None, frozen=False):
    cache = EvidenceCache(tmp_path, frozen=frozen)
    settings = replace(get_settings(), vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                       vies_requester_vat="", cache_frozen=False)
    stub = ViesStub(vies_reply or vies_mock.echo_valid_undisclosed)
    vies = VIESClient(settings, cache=cache, transport=stub.transport)
    engine = LlmOnlyLookup(settings, vies, llm=VatLLM(cache.namespace("vat_llm"), complete=model))
    return engine, stub


class TestStatus:
    @pytest.mark.asyncio
    async def test_a_checksum_valid_vies_valid_guess_is_still_only_a_guess(self, tmp_path):
        engine, stub = _engine(tmp_path, _Model({"vat_id": "DE 149 145 247", "confidence": "high"}))
        r = await engine.lookup(BASF)
        assert r.status == "LLM_GUESS:FORMAT_OK:VALID"
        assert r.status != FOUND_VERIFIED
        assert (r.value, r.evidence, r.source_url, r.attributed_to) == ("DE149145247", "high", None, None)
        assert r.mode == "llm_only" and EXPERIMENT_FLAG in r.flags
        assert stub.requests == [{"countryCode": "DE", "vatNumber": "149145247"}]

    @pytest.mark.asyncio
    async def test_bad_checksum_is_not_sent_to_vies(self, tmp_path):
        engine, stub = _engine(tmp_path, _Model({"vat_id": "DE149145248", "confidence": "medium"}))
        r = await engine.lookup(BASF)
        assert r.status == "LLM_GUESS:FORMAT_ERROR:NOT_CHECKED"
        assert "FORMAT_ERROR:InvalidChecksum" in r.flags
        assert stub.requests == []

    @pytest.mark.asyncio
    async def test_null_answer(self, tmp_path):
        engine, stub = _engine(tmp_path, _Model({"vat_id": None, "confidence": "low"}))
        r = await engine.lookup(BASF)
        assert r.status == "LLM_GUESS:MISSING:NOT_CHECKED"
        assert "LLM_NO_ANSWER" in r.flags and r.value is None
        assert stub.requests == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply, vies_status", [
        (vies_mock.invalid("DE", "149145247"), "INVALID"),
        (vies_mock.error("MS_UNAVAILABLE"), "SERVICE_UNAVAILABLE"),
    ])
    async def test_vies_result_is_recorded_not_trusted(self, tmp_path, reply, vies_status):
        engine, _ = _engine(tmp_path, _Model({"vat_id": "DE149145247", "confidence": "high"}),
                            vies_reply=reply)
        r = await engine.lookup(BASF)
        assert r.status == f"LLM_GUESS:FORMAT_OK:{vies_status}"

    @pytest.mark.asyncio
    async def test_unknown_confidence_is_marked(self, tmp_path):
        engine, _ = _engine(tmp_path, _Model({"vat_id": "DE149145247", "confidence": "certain"}))
        assert (await engine.lookup(BASF)).evidence == "unstated:certain"

    @pytest.mark.asyncio
    async def test_llm_failure_is_incomplete(self, tmp_path):
        engine, _ = _engine(tmp_path, _Model(RuntimeError("OpenAI call failed")))
        r = await engine.lookup(BASF)
        assert r.status == LOOKUP_INCOMPLETE and EXPERIMENT_FLAG in r.flags


class TestPrompt:
    @pytest.mark.asyncio
    async def test_prompt_is_name_and_address_only(self, tmp_path):
        model = _Model({"vat_id": None, "confidence": "low"})
        engine, _ = _engine(tmp_path, model)
        await engine.lookup(BASF)
        (_, user), = model.prompts
        for value in ("BASF SE", "Carl-Bosch-Strasse 38", "67056", "Ludwigshafen", "DE"):
            assert value in user
        assert "What is this company's German VAT identification number (USt-IdNr.)?" in user
        assert "Answer null if you do not know." in user
        assert "Page" not in user and "http" not in user  # no page, no search results


class TestCache:
    @pytest.mark.asyncio
    async def test_frozen_replay_makes_no_call(self, tmp_path):
        first, _ = _engine(tmp_path, _Model({"vat_id": "DE149145247", "confidence": "high"}))
        r1 = await first.lookup(BASF)
        model = _Model({"vat_id": "DE000000000", "confidence": "low"})
        frozen, _ = _engine(tmp_path, model, frozen=True)
        r2 = await frozen.lookup(BASF)
        assert model.prompts == []
        assert (r2.status, r2.value, r2.date) == (r1.status, r1.value, r1.date)

    @pytest.mark.asyncio
    async def test_frozen_miss_is_incomplete(self, tmp_path):
        model = _Model({"vat_id": "DE149145247", "confidence": "high"})
        engine, _ = _engine(tmp_path, model, frozen=True)
        r = await engine.lookup(BASF)
        assert r.status == LOOKUP_INCOMPLETE and model.prompts == []


class TestLabelling:
    def test_module_says_experiment_only(self):
        doc = llm_only_module.__doc__
        assert "EXPERIMENT ONLY" in doc and "NOT FOR PRODUCTION USE" in doc
        assert "NOT to be written to SAP" in doc

    @pytest.mark.asyncio
    async def test_in_validate_rows_nothing_is_written_to_the_vat_column(self, tmp_path):
        engine, _ = _engine(tmp_path, _Model({"vat_id": "DE149145247", "confidence": "high"}))
        rows = [{"Customer": "99000005", "Name": "BASF SE", "Country/Region Key": "DE",
                 "VAT Registration No.": None, "City": "Ludwigshafen"}]
        out, summary = await validate_rows(rows, engine._vies, lookup=engine)
        assert out[0]["VAT Registration No."] is None
        assert (out[0]["VAT_Lookup_Mode"], out[0]["VAT_Found_Status"]) == (
            "LLM_ONLY", "LLM_GUESS:FORMAT_OK:VALID")
        assert summary["lookup"]["mode"] == "llm_only"
