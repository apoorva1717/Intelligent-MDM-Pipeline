"""Country guard for the ROR client (call_ror), HTTP-path tests.

Reproduces the reported bug — a same-name org in the WRONG country being
returned for a record (e.g. a US "BASF" for a German record) — on both ROR
strategies: the affiliation endpoint (whose scorer ignores the country in the
affiliation string) and the query endpoint's no-filter retry. Uses
``httpx.MockTransport`` to stand in for the ROR API.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import tier1_ror
from enrichment.tier1_ror import call_ror, clear_ror_cache


def _org(ror_id: str, name: str, country_code: str, country_name: str) -> dict:
    """Minimal ROR v2 organisation dict."""
    return {
        "id": ror_id,
        "names": [{"value": name, "types": ["ror_display"]}],
        "types": ["company"],
        "locations": [{
            "geonames_details": {
                "country_code": country_code,
                "country_name": country_name,
            }
        }],
        "relationships": [],
        "links": [],
    }


US_BASF = "https://ror.org/002yzpx87"
DE_BASF = "https://ror.org/01q8f6705"


@pytest.fixture(autouse=True)
def _clear():
    clear_ror_cache()
    yield
    clear_ror_cache()


def _patch_ror(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(tier1_ror.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_affiliation_wrong_country_rejected(monkeypatch):
    """Affiliation returns a confident US org for a DE request → rejected;
    query has no DE org → clean miss (no wrong-country ror_id)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "affiliation" in request.url.params:
            return httpx.Response(200, json={"items": [
                {"chosen": True, "score": 1.0,
                 "organization": _org(US_BASF, "BASF", "US", "United States")},
            ]})
        # query endpoint — only the US org exists
        return httpx.Response(200, json={"items": [_org(US_BASF, "BASF", "US", "United States")]})

    _patch_ror(monkeypatch, handler)
    res = await call_ror("BASF", country_code="DE", country="Germany", city="Ludwigshafen")
    assert res["matched"] is False
    assert res.get("ror_id") is None


@pytest.mark.asyncio
async def test_affiliation_right_country_accepted(monkeypatch):
    """Affiliation returns the DE org for a DE request → accepted."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "affiliation" in request.url.params:
            return httpx.Response(200, json={"items": [
                {"chosen": True, "score": 1.0,
                 "organization": _org(DE_BASF, "BASF", "DE", "Germany")},
            ]})
        return httpx.Response(200, json={"items": []})

    _patch_ror(monkeypatch, handler)
    res = await call_ror("BASF", country_code="DE", country="Germany")
    assert res["matched"] is True
    assert res["ror_id"] == DE_BASF
    assert res["country_code"] == "DE"


@pytest.mark.asyncio
async def test_query_no_filter_retry_wrong_country_rejected(monkeypatch):
    """The retry-without-filter leak: filtered query returns 0, the no-filter
    retry returns a US org, and the country guard drops it → miss."""
    calls = {"query": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "affiliation" in params:
            return httpx.Response(200, json={"items": []})  # no affiliation match
        # query endpoint
        calls["query"] += 1
        if "filter" in params:
            return httpx.Response(200, json={"items": []})       # DE-filtered: empty
        return httpx.Response(200, json={"items": [_org(US_BASF, "BASF", "US", "United States")]})

    _patch_ror(monkeypatch, handler)
    res = await call_ror("BASF", country_code="DE", country="Germany")
    assert res["matched"] is False
    assert calls["query"] == 2  # filtered query + no-filter retry both happened


@pytest.mark.asyncio
async def test_no_country_code_keeps_match(monkeypatch):
    """With no country requested, the guard does not filter (backwards-compatible)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "affiliation" in request.url.params:
            return httpx.Response(200, json={"items": [
                {"chosen": True, "score": 1.0,
                 "organization": _org(US_BASF, "BASF", "US", "United States")},
            ]})
        return httpx.Response(200, json={"items": []})

    _patch_ror(monkeypatch, handler)
    res = await call_ror("BASF", country_code=None)
    assert res["matched"] is True
    assert res["ror_id"] == US_BASF
