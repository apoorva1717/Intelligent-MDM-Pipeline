"""Speed fixes: VIES /check-status pre-check, one VIES concurrency limit across
parallel calls, customers looked up in parallel, a fetch budget per customer."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeSearch, FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.lookup import FoundResult, LookupInput, run_lookups
from vat.lookup_grounded import MAX_FETCHES, GroundedLookup
from vat.normalize import assess_format
from vat.vies_client import MS_UNAVAILABLE_STATUS, SERVICE_UNAVAILABLE, STATUS_TTL_SECONDS, VALID, VIESClient


def _settings(**kw):
    base = dict(vies_backoff_seconds=0.0, vies_min_interval_ms=0, vies_max_concurrency=1,
                vies_requester_vat="", cache_frozen=False)
    return replace(get_settings(), **{**base, **kw})


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _client(tmp_path, stub, *, frozen=False, clock=None, **kw):
    return VIESClient(_settings(**kw), cache=EvidenceCache(tmp_path, frozen=frozen),
                      transport=stub.transport, clock=clock or _Clock())


DE1, DE2 = assess_format("DE136695976", "DE"), assess_format("DE811128135", "DE")
FR = assess_format("FR40303265045", "FR")


class TestStatusPrecheck:
    @pytest.mark.asyncio
    async def test_unavailable_member_state_is_not_called(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        stub.status = {"DE": "Unavailable", "FR": "Available"}
        client = _client(tmp_path, stub)
        results = await client.check_many([DE1, DE2, FR])
        assert results["DE136695976"].status == SERVICE_UNAVAILABLE
        assert results["DE136695976"].error_code == MS_UNAVAILABLE_STATUS
        assert results["DE136695976"].attempts == 0
        assert results["FR40303265045"].status == VALID
        assert [r["countryCode"] for r in stub.requests] == ["FR"]  # no DE call at all
        assert stub.status_requests == 1                            # asked once for all three

    @pytest.mark.asyncio
    @pytest.mark.parametrize("availability", ["Available", "Monitoring Disabled"])
    async def test_anything_but_unavailable_lets_the_call_go_ahead(self, tmp_path, availability):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        stub.status = {"DE": availability}
        r = await _client(tmp_path, stub).check(DE1)
        assert r.status == VALID and len(stub.requests) == 1

    @pytest.mark.asyncio
    async def test_a_failed_status_call_does_not_block_checks(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)

        def handler(request):
            if request.url.path.endswith("/check-status"):
                return httpx.Response(503, text="down")
            return stub(request)

        client = VIESClient(_settings(), cache=EvidenceCache(tmp_path),
                            transport=httpx.MockTransport(handler), clock=_Clock())
        assert (await client.check(DE1)).status == VALID

    @pytest.mark.asyncio
    async def test_never_asked_in_a_frozen_run(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        stub.status = {"DE": "Unavailable"}
        r = await _client(tmp_path, stub, frozen=True).check(DE1)
        assert (r.error_code, stub.status_requests) == ("FROZEN_CACHE_MISS", 0)

    @pytest.mark.asyncio
    async def test_status_is_refreshed_after_the_ttl(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        stub.status = {"DE": "Unavailable"}
        clock = _Clock()
        client = _client(tmp_path, stub, clock=clock)
        assert (await client.check(DE1)).error_code == MS_UNAVAILABLE_STATUS
        stub.status = {"DE": "Available"}          # 05:00: Germany comes back
        clock.now += STATUS_TTL_SECONDS + 1
        assert (await client.check(DE2)).status == VALID
        assert stub.status_requests == 2

    @pytest.mark.asyncio
    async def test_a_recorded_answer_needs_no_status_call(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        await _client(tmp_path, stub).check(DE1)
        stub2 = ViesStub(vies_mock.echo_valid_undisclosed)
        stub2.status = {"DE": "Unavailable"}
        r = await _client(tmp_path, stub2).check(DE1)
        assert (r.status, r.from_cache, stub2.status_requests) == (VALID, True, 0)


class TestViesConcurrencyAcrossCalls:
    @pytest.mark.asyncio
    async def test_parallel_check_calls_share_one_limit(self, tmp_path):
        in_flight, peak = 0, 0

        async def handler(request):
            nonlocal in_flight, peak
            if request.url.path.endswith("/check-status"):
                return httpx.Response(200, json={"countries": []})
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            body = json.loads(request.content)
            return httpx.Response(200, json=vies_mock.valid_undisclosed(body["countryCode"], body["vatNumber"]))

        client = VIESClient(_settings(vies_max_concurrency=1), cache=EvidenceCache(tmp_path),
                            transport=httpx.MockTransport(handler), clock=_Clock())
        formats = [assess_format(v, "DE") for v in ("DE136695976", "DE811128135", "DE149145247")]
        results = await asyncio.gather(*(client.check(f) for f in formats))
        assert all(r.status == VALID for r in results)
        assert peak == 1


class _SlowEngine:
    mode = "grounded"

    def __init__(self):
        self.in_flight = self.peak = 0
        self.calls: list[str] = []

    async def lookup(self, inp):
        self.calls.append(inp.customer)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return FoundResult("grounded", "NOT_FOUND", value=inp.customer)

    def stats(self):
        return {}

    async def aclose(self):
        pass


def _inp(customer):
    return LookupInput(customer=customer, names=(f"Firma {customer} GmbH", None, None, None),
                       street=None, house_number=None, postal_code=None, city="Berlin", country="DE")


class TestParallelLookups:
    @pytest.mark.asyncio
    async def test_order_once_per_customer_and_the_limit(self):
        engine = _SlowEngine()
        inputs = [_inp(str(i % 8)) for i in range(16)] + [None]   # 8 customers, each twice
        out = await run_lookups(engine, inputs, concurrency=3)
        assert [r.value if r else None for r in out] == [str(i % 8) for i in range(16)] + [None]
        assert sorted(engine.calls) == sorted(str(i) for i in range(8))  # once each
        assert engine.peak == 3

    @pytest.mark.asyncio
    async def test_each_customer_gets_its_own_fetch_budget(self, tmp_path):
        hosts = {c: [f"https://site{c}{i}.example/" for i in range(3)] for c in ("A", "B")}
        web = FakeWeb({u: page(u, "x", title="Home") for us in hosts.values() for u in us})
        search = FakeSearch({f"Firma {c}": us for c, us in hosts.items()})
        vies = VIESClient(_settings(), cache=EvidenceCache(tmp_path),
                          transport=ViesStub().transport, clock=_Clock())
        engine = GroundedLookup(_settings(), vies, search_client=search, fetcher=web,
                                llm=None)
        out = await run_lookups(engine, [_inp("A"), _inp("B")], concurrency=2)
        assert [r.status for r in out] == ["NOT_FOUND", "NOT_FOUND"]
        assert engine.stats()["page_attempts"] == 2 * MAX_FETCHES   # 6 each, not 6 shared
