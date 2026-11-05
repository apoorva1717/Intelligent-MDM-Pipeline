"""The grounded lookup (SerpAPI) is off unless VAT_GROUNDED_FALLBACK is set
(vat/lookup_engines.py): llm_web is the default, and grounded runs — as a
mode, side by side, or as llm_web's fallback — only with the flag."""

from __future__ import annotations

from dataclasses import replace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api import vat_routes
from config import get_settings
from api.app import app
from tests.mocks import vies_mock
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.lookup import FOUND_VERIFIED, LOOKUP_INCOMPLETE, NOT_FOUND, FoundResult, LookupInput
from vat.lookup_engines import (
    GROUNDED_FALLBACK,
    GroundedDisabled,
    WithGroundedFallback,
    make_lookup,
)
from vat.lookup_grounded import LLM_UNAVAILABLE, GroundedLookup
from vat.lookup_web import WebSearchLookup
from vat.vies_client import VIESClient

ACME = LookupInput(customer="1", names=("Acme GmbH", None, None, None), street=None,
                   house_number=None, postal_code=None, city=None, country="DE")


def _settings(flag: bool):
    return replace(get_settings(), vat_web_query_mode="model", vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                   vies_requester_vat="", cache_frozen=False, vat_grounded_fallback=flag)


def _vies(tmp_path, settings):
    return VIESClient(settings, cache=EvidenceCache(tmp_path),
                      transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)


class _Fixed:
    def __init__(self, mode, result):
        self.mode, self.result, self.calls, self.closed = mode, result, 0, False
        self.agent_version = f"{mode}|v"

    async def lookup(self, inp):
        self.calls += 1
        return self.result

    def stats(self):
        return {"calls": self.calls}

    async def aclose(self):
        self.closed = True


class TestFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("VAT_GROUNDED_FALLBACK", raising=False)
        assert get_settings().vat_grounded_fallback is False

    @pytest.mark.asyncio
    async def test_off_grounded_is_refused_and_llm_web_has_no_fallback(self, tmp_path):
        settings = _settings(False)
        vies = _vies(tmp_path, settings)
        with pytest.raises(GroundedDisabled, match="VAT_GROUNDED_FALLBACK"):
            make_lookup("grounded", settings, vies)
        assert isinstance(make_lookup("llm_web", settings, vies), WebSearchLookup)

    @pytest.mark.asyncio
    async def test_on_grounded_runs_and_llm_web_falls_back(self, tmp_path):
        settings = _settings(True)
        vies = _vies(tmp_path, settings)
        assert isinstance(make_lookup("grounded", settings, vies), GroundedLookup)
        engine = make_lookup("llm_web", settings, vies)
        assert isinstance(engine, WithGroundedFallback) and engine.mode == "llm_web"


class TestFallback:
    @pytest.mark.asyncio
    async def test_runs_only_when_the_web_search_was_unavailable(self):
        unavailable = FoundResult("llm_web", LOOKUP_INCOMPLETE, flags=("LLM_WEB_SEARCH", LLM_UNAVAILABLE))
        web = _Fixed("llm_web", unavailable)
        grounded = _Fixed("grounded", FoundResult("grounded", FOUND_VERIFIED, value="DE811128135"))
        engine = WithGroundedFallback(web, grounded)
        result = await engine.lookup(ACME)
        assert (result.mode, result.status, result.value) == ("grounded", FOUND_VERIFIED, "DE811128135")
        assert GROUNDED_FALLBACK in result.flags and LLM_UNAVAILABLE in result.flags
        assert result.agent_version == "grounded|v"
        assert engine.stats()["grounded_fallbacks"] == 1 and engine.stats()["fallback_calls"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [
        FoundResult("llm_web", NOT_FOUND, flags=("LLM_WEB_NO_ANSWER",)),
        FoundResult("llm_web", FOUND_VERIFIED, value="DE811128135"),
        FoundResult("llm_web", LOOKUP_INCOMPLETE, flags=("FROZEN_CACHE_MISS",)),
    ])
    async def test_any_other_answer_is_final(self, result):
        grounded = _Fixed("grounded", FoundResult("grounded", FOUND_VERIFIED))
        engine = WithGroundedFallback(_Fixed("llm_web", result), grounded)
        assert await engine.lookup(ACME) is result
        assert grounded.calls == 0

    @pytest.mark.asyncio
    async def test_closes_both(self):
        web = _Fixed("llm_web", FoundResult("llm_web", NOT_FOUND))
        grounded = _Fixed("grounded", FoundResult("grounded", NOT_FOUND))
        await WithGroundedFallback(web, grounded).aclose()
        assert web.closed and grounded.closed


class TestEndpoints:
    @pytest_asyncio.fixture
    async def client(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VAT_GROUNDED_FALLBACK", raising=False)
        settings = _settings(False)
        monkeypatch.setattr(vat_routes, "_get_vies_client", lambda _s=None: _vies(tmp_path, settings))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", [
        "/api/validate/vat?lookup_missing=grounded",
        "/api/validate/vat/llm-only-experiment?with_grounded=true",
    ])
    async def test_grounded_while_off_is_400(self, client, url):
        rows = [{"Customer": "1", "Name": "Acme GmbH", "Country/Region Key": "DE",
                 "VAT Registration No.": None}]
        resp = await client.post(url, json={"rows": rows})
        assert resp.status_code == 400
        assert "VAT_GROUNDED_FALLBACK" in resp.json()["detail"]
