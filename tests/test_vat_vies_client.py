"""VIES client (vat/vies_client.py) against mocked VIES responses.

No network: every client gets an ``httpx.MockTransport`` and its own evidence
cache under ``tmp_path`` (the conftest cache root is shared across tests, and a
recorded VIES answer must not leak from one test into the next).
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.normalize import assess_format
from vat.vies_client import (
    ERROR,
    FROZEN_CACHE_MISS,
    INVALID,
    SERVICE_UNAVAILABLE,
    TRANSIENT_ERRORS,
    VALID,
    VIESClient,
    parse_response,
    requester_fields,
)

DE_OK = assess_format("DE136695976", "DE")
FR_OK = assess_format("FR40303265045", "FR")
EL_OK = assess_format("GR094014201", "GR")


def _settings(**overrides):
    base = dict(
        vies_max_attempts=3, vies_backoff_seconds=1.0,
        vies_max_concurrency=1, vies_requester_vat="", cache_frozen=False,
    )
    return replace(get_settings(), **{**base, **overrides})


class _Sleeps(list):
    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


def _client(stub, tmp_path, *, frozen=False, **settings):
    sleeps = _Sleeps()
    client = VIESClient(
        _settings(**settings),
        cache=EvidenceCache(tmp_path, frozen=frozen),
        transport=stub.transport,
        sleep=sleeps,
    )
    return client, sleeps


def _recorded(tmp_path) -> list:
    return sorted((tmp_path / "vies").glob("*.json")) if (tmp_path / "vies").exists() else []


# ---------------------------------------------------------------------------
# VALID / INVALID
# ---------------------------------------------------------------------------

class TestAnswers:
    @pytest.mark.asyncio
    async def test_de_valid_with_undisclosed_name(self, tmp_path):
        stub = ViesStub(vies_mock.valid_undisclosed("DE", "136695976"))
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert r.status == VALID
        assert (r.name, r.address, r.name_disclosed) == (None, None, False)
        assert r.request_date == vies_mock.REQUEST_DATE
        assert r.consultation_number is None
        assert r.source == "VIES"
        assert (r.attempts, r.error_code) == (1, None)

    @pytest.mark.asyncio
    async def test_valid_with_disclosed_name_and_consultation_number(self, tmp_path):
        stub = ViesStub(vies_mock.valid_disclosed(
            "FR", "40303265045", request_identifier="WAPIAAAAZ0123456",
        ))
        client, _ = _client(stub, tmp_path, vies_requester_vat="DE 136.695.976")
        r = await client.check(FR_OK)
        assert (r.status, r.name, r.name_disclosed) == (VALID, "EXAMPLE TRADING SA", True)
        assert r.address == "1 RUE D'EXEMPLE\n75000 PARIS"
        assert r.consultation_number == "WAPIAAAAZ0123456"

    @pytest.mark.asyncio
    async def test_invalid(self, tmp_path):
        stub = ViesStub(vies_mock.invalid("DE", "136695976"))
        client, sleeps = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.name_disclosed, r.attempts) == (INVALID, None, 1)
        assert sleeps == []


# ---------------------------------------------------------------------------
# SERVICE_UNAVAILABLE — retried, never INVALID
# ---------------------------------------------------------------------------

class TestServiceUnavailable:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", sorted(TRANSIENT_ERRORS))
    async def test_every_transient_code_retries_then_reports_unavailable(self, tmp_path, code):
        stub = ViesStub(vies_mock.error(code))
        client, sleeps = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code, r.attempts) == (SERVICE_UNAVAILABLE, code, 3)
        assert len(stub.requests) == 3
        assert sleeps == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_transient_then_valid(self, tmp_path):
        stub = ViesStub(
            vies_mock.error("MS_MAX_CONCURRENT_REQ"),
            vies_mock.valid_undisclosed("DE", "136695976"),
        )
        client, sleeps = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.attempts) == (VALID, 2)
        assert sleeps == [1.0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [
        httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.ConnectError("down"),
    ])
    async def test_network_failures(self, tmp_path, exc):
        stub = ViesStub(exc)
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code, r.attempts) == (
            SERVICE_UNAVAILABLE, type(exc).__name__, 3,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_http_errors_without_a_vies_body(self, tmp_path, status):
        stub = ViesStub(httpx.Response(status, text="<html>busy</html>"))
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code) == (SERVICE_UNAVAILABLE, f"HTTP_{status}")

    @pytest.mark.asyncio
    async def test_500_with_a_common_response_body_uses_its_code(self, tmp_path):
        stub = ViesStub(httpx.Response(500, json=vies_mock.error("MS_UNAVAILABLE")))
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code) == (SERVICE_UNAVAILABLE, "MS_UNAVAILABLE")

    @pytest.mark.asyncio
    async def test_max_attempts_is_configurable(self, tmp_path):
        stub = ViesStub(vies_mock.error("TIMEOUT"))
        client, sleeps = _client(stub, tmp_path, vies_max_attempts=1)
        r = await client.check(DE_OK)
        assert (r.status, r.attempts, sleeps) == (SERVICE_UNAVAILABLE, 1, [])


# ---------------------------------------------------------------------------
# ERROR — not retried
# ---------------------------------------------------------------------------

class TestError:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [
        "INVALID_INPUT", "INVALID_REQUESTER_INFO", "VAT_BLOCKED", "IP_BLOCKED",
        "SOMETHING_NEW",
    ])
    async def test_non_transient_codes(self, tmp_path, code):
        stub = ViesStub(vies_mock.error(code))
        client, sleeps = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code, r.attempts) == (ERROR, code, 1)
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_malformed_200(self, tmp_path):
        stub = ViesStub({"hello": "world"})
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code) == (ERROR, "MALFORMED_RESPONSE")

    @pytest.mark.asyncio
    async def test_http_404(self, tmp_path):
        stub = ViesStub(httpx.Response(404, text="not found"))
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code, r.attempts) == (ERROR, "HTTP_404", 1)

    @pytest.mark.asyncio
    async def test_answer_on_an_error_status_is_not_trusted(self, tmp_path):
        stub = ViesStub(httpx.Response(503, json=vies_mock.valid_undisclosed("DE", "1")))
        client, _ = _client(stub, tmp_path)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code) == (SERVICE_UNAVAILABLE, "HTTP_503")
        assert _recorded(tmp_path) == []


# ---------------------------------------------------------------------------
# Cache and frozen mode
# ---------------------------------------------------------------------------

class TestCache:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [
        vies_mock.valid_undisclosed("DE", "136695976"),
        vies_mock.invalid("DE", "136695976"),
    ])
    async def test_valid_and_invalid_are_recorded_and_replayed(self, tmp_path, answer):
        first, _ = _client(ViesStub(answer), tmp_path)
        r1 = await first.check(DE_OK)
        assert len(_recorded(tmp_path)) == 1

        stub = ViesStub(vies_mock.error("SERVICE_UNAVAILABLE"))
        second, _ = _client(stub, tmp_path)  # new process, same directory
        r2 = await second.check(DE_OK)
        assert stub.requests == []
        assert (r2.status, r2.from_cache, r2.attempts) == (r1.status, True, 0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply", [
        vies_mock.error("MS_UNAVAILABLE"),
        vies_mock.error("INVALID_REQUESTER_INFO"),
        httpx.ReadTimeout("slow"),
        httpx.Response(503, text="busy"),
    ])
    async def test_nothing_else_is_recorded(self, tmp_path, reply):
        client, _ = _client(ViesStub(reply), tmp_path)
        await client.check(DE_OK)
        assert _recorded(tmp_path) == []

    @pytest.mark.asyncio
    async def test_frozen_miss_is_unavailable_and_makes_no_call(self, tmp_path):
        stub = ViesStub(vies_mock.valid_undisclosed("DE", "136695976"))
        client, _ = _client(stub, tmp_path, frozen=True)
        r = await client.check(DE_OK)
        assert (r.status, r.error_code, r.attempts) == (
            SERVICE_UNAVAILABLE, FROZEN_CACHE_MISS, 0,
        )
        assert stub.requests == []

    @pytest.mark.asyncio
    async def test_frozen_hit_replays(self, tmp_path):
        live, _ = _client(ViesStub(vies_mock.valid_undisclosed("DE", "136695976")), tmp_path)
        await live.check(DE_OK)
        stub = ViesStub(vies_mock.error("TIMEOUT"))
        frozen, _ = _client(stub, tmp_path, frozen=True)
        r = await frozen.check(DE_OK)
        assert (r.status, r.from_cache) == (VALID, True)
        assert stub.requests == []

    def test_key_is_the_normalised_vat_and_requester(self, tmp_path):
        a, _ = _client(ViesStub(), tmp_path)
        b, _ = _client(ViesStub(), tmp_path, vies_requester_vat="DE136695976")
        assert a.cache_key("DE136695976") == "DE136695976"
        assert a.cache_key("DE136695976") != a.cache_key("FR40303265045")
        assert a.cache_key("DE136695976") != b.cache_key("DE136695976")

    @pytest.mark.asyncio
    async def test_recorded_file_is_named_by_the_vat_and_short(self, tmp_path):
        # A URL-based key overflowed the Windows path limit under a deep cache
        # directory, and DiskCache.set skips an unwritable file silently.
        client, _ = _client(ViesStub(vies_mock.valid_undisclosed("DE", "136695976")), tmp_path)
        await client.check(DE_OK)
        assert [p.name for p in _recorded(tmp_path)] == ["vies_de136695976.json"]


# ---------------------------------------------------------------------------
# What is sent, and how often
# ---------------------------------------------------------------------------

class TestRequests:
    @pytest.mark.asyncio
    async def test_request_body_uses_the_contract_field_names(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        client, _ = _client(stub, tmp_path)
        await client.check(EL_OK)
        assert stub.requests == [{"countryCode": "EL", "vatNumber": "094014201"}]

    @pytest.mark.asyncio
    async def test_requester_is_sent_when_configured(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        client, _ = _client(stub, tmp_path, vies_requester_vat="de136695976")
        await client.check(FR_OK)
        assert stub.requests == [{
            "countryCode": "FR", "vatNumber": "40303265045",
            "requesterMemberStateCode": "DE", "requesterNumber": "136695976",
        }]

    @pytest.mark.asyncio
    async def test_once_per_unique_normalised_vat_and_only_format_ok(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        client, _ = _client(stub, tmp_path)
        formats = [
            assess_format("DE136695976", "DE"),
            assess_format("DE 136.695.976", "DE"),   # same number, other spelling
            assess_format("136695976", "DE"),        # same number, prefix added
            FR_OK,
            assess_format("DE136695977", "DE"),      # FORMAT_ERROR
            assess_format("12-3456789", "US"),       # NOT_EU
            assess_format(None, "DE"),               # MISSING
        ]
        results = await client.check_many(formats)
        assert sorted(results) == ["DE136695976", "FR40303265045"]
        assert sorted(r["countryCode"] for r in stub.requests) == ["DE", "FR"]

    @pytest.mark.asyncio
    async def test_check_refuses_a_value_that_is_not_format_ok(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        client, _ = _client(stub, tmp_path)
        with pytest.raises(ValueError):
            await client.check(assess_format("DE136695977", "DE"))
        assert stub.requests == []


class TestPureHelpers:
    def test_parse_never_turns_an_error_into_invalid(self):
        for code in sorted(TRANSIENT_ERRORS):
            assert parse_response("DE1", vies_mock.error(code)).status == SERVICE_UNAVAILABLE

    def test_requester_fields(self):
        assert requester_fields("") == {}
        assert requester_fields(None) == {}
        assert requester_fields(" DE 136.695.976 ") == {
            "requesterMemberStateCode": "DE", "requesterNumber": "136695976",
        }
        with pytest.raises(ValueError):
            requester_fields("136695976")
