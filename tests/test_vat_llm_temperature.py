"""VAT-only temperature fallback (vat/llm_cache.py).

A model that rejects temperature 0 (GPT-6 accepts only its default) gets no
temperature on VAT calls from the first rejection on, and the answer is cached
under the temperature actually sent — so a frozen re-run still finds it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import vat.llm_cache as llm_cache
from utils.cache import EvidenceCache
from vat.llm_cache import VatLLM

_REJECT = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' "
    "does not support 0.0 with this model. Only the default (1) value is supported.\"}}"
)


class _Rejects0:
    """A ``call_openai`` double for a model that only takes its default temperature."""

    def __init__(self):
        self.temperatures: list[float | None] = []

    async def __call__(self, system, user, *, max_tokens, client, temperature):
        self.temperatures.append(temperature)
        if temperature is not None:
            raise RuntimeError(f"OpenAI call failed: {_REJECT}") from ValueError(_REJECT)
        return json.dumps({"vat_id": "DE123456789"})


@pytest.fixture
def rejects0(monkeypatch):
    llm_cache.reset_temperature_support()
    model = _Rejects0()
    monkeypatch.setattr(llm_cache, "call_openai", model)
    monkeypatch.setattr(llm_cache, "get_openai_client", lambda: object())
    yield model
    llm_cache.reset_temperature_support()


@pytest.mark.asyncio
async def test_rejected_temperature_is_dropped_once(tmp_path, rejects0):
    llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"))
    assert await llm.extract_json("sys", "row 1") == {"vat_id": "DE123456789"}
    assert await llm.extract_json("sys", "row 2") == {"vat_id": "DE123456789"}
    # One 400, then the default temperature for the rest of the process.
    assert rejects0.temperatures == [0.0, None, None]


@pytest.mark.asyncio
async def test_concurrent_rejections_all_retry(tmp_path, monkeypatch):
    """Rows run concurrently: every call already in flight at temperature 0
    when the first rejection flips the flag must still retry."""
    llm_cache.reset_temperature_support()
    in_flight = asyncio.Event()
    sent: list[float | None] = []

    async def model(system, user, *, max_tokens, client, temperature):
        sent.append(temperature)
        if temperature is not None:
            if sent.count(0.0) == 3:
                in_flight.set()
            await in_flight.wait()  # all three are out before any 400 lands
            raise RuntimeError(f"OpenAI call failed: {_REJECT}") from ValueError(_REJECT)
        return json.dumps({"vat_id": "DE123456789"})

    monkeypatch.setattr(llm_cache, "call_openai", model)
    monkeypatch.setattr(llm_cache, "get_openai_client", lambda: object())
    llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"))
    answers = await asyncio.gather(*(llm.extract_json("sys", f"row {i}") for i in range(3)))
    assert answers == [{"vat_id": "DE123456789"}] * 3
    assert sent.count(None) == 3
    llm_cache.reset_temperature_support()


@pytest.mark.asyncio
async def test_frozen_rerun_finds_default_temperature_answer(tmp_path, rejects0):
    await VatLLM(EvidenceCache(tmp_path).namespace("vat_llm")).extract_json("sys", "row 1")

    llm_cache.reset_temperature_support()  # a fresh process
    frozen = VatLLM(EvidenceCache(tmp_path, frozen=True).namespace("vat_llm"))
    assert await frozen.extract_json("sys", "row 1") == {"vat_id": "DE123456789"}
    assert frozen.network_calls == 0


@pytest.mark.asyncio
async def test_other_errors_still_raise(tmp_path, monkeypatch):
    llm_cache.reset_temperature_support()

    async def down(*a, **k):
        raise RuntimeError("OpenAI call failed: Connection error")

    monkeypatch.setattr(llm_cache, "call_openai", down)
    monkeypatch.setattr(llm_cache, "get_openai_client", lambda: object())
    with pytest.raises(RuntimeError):
        await VatLLM(EvidenceCache(tmp_path).namespace("vat_llm")).extract_json("sys", "row")
    assert llm_cache._TEMPERATURE_SUPPORTED
