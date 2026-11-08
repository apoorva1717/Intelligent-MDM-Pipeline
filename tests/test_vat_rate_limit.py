"""Pacing of the Azure calls (vat/rate_limit.py) and the 429 handling in
VatLLM.search (vat/llm_cache.py), 2026-11-08: only *when* a call is sent
changes, never what is sent."""

from __future__ import annotations

import json

import pytest

from utils.cache import EvidenceCache
from vat import rate_limit
from vat.llm_cache import SearchLimits, VatLLM
from vat.rate_limit import RateLimiter, estimate_tokens, limiter_for, retry_after


class Clock:
    """A fake clock; sleeping moves it."""

    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def _limiter(tpm=None, rpm=None, **kw):
    clock = Clock()
    return RateLimiter(tpm, rpm, share=1.0, clock=clock, sleep=clock.sleep, **kw), clock


@pytest.fixture(autouse=True)
def _fresh_limiters(monkeypatch):
    for name in ("VAT_AZURE_TPM", "VAT_AZURE_RPM", "VAT_AZURE_QUOTA_SHARE"):
        monkeypatch.delenv(name, raising=False)
    rate_limit.reset()
    yield
    rate_limit.reset()


class TestTheBuckets:
    @pytest.mark.asyncio
    async def test_nothing_configured_never_waits(self):
        limiter, clock = _limiter()
        for _ in range(100):
            assert await limiter.acquire(50_000) == 0
        assert clock.slept == []

    @pytest.mark.asyncio
    async def test_tokens_per_minute(self):
        # 600 TPM: 10 tokens a second, a bucket of 10 s = 100 tokens.
        limiter, clock = _limiter(tpm=600)
        assert await limiter.acquire(100) == 0
        assert await limiter.acquire(50) == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_requests_per_minute(self):
        limiter, clock = _limiter(rpm=60)  # 1 a second, 10 in the bucket
        for _ in range(10):
            await limiter.acquire(0)
        assert clock.slept == []
        assert await limiter.acquire(0) == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_a_call_larger_than_the_bucket_waits_for_a_full_one(self):
        limiter, clock = _limiter(tpm=600)
        await limiter.acquire(100)
        assert await limiter.acquire(500) == pytest.approx(10.0)  # never forever

    @pytest.mark.asyncio
    async def test_the_real_count_replaces_the_estimate(self):
        limiter, clock = _limiter(tpm=600)
        await limiter.acquire(100)
        limiter.settle(100, 20)  # it used 20: 80 back
        assert await limiter.acquire(80) == 0

    @pytest.mark.asyncio
    async def test_a_429_pauses_every_caller(self):
        limiter, clock = _limiter(tpm=600_000)
        limiter.pause(7.5)
        assert await limiter.acquire(1) == pytest.approx(7.5)

    def test_the_quota_from_azures_headers(self):
        limiter, _ = _limiter()
        limiter.learn({"x-ratelimit-limit-tokens": "300000", "x-ratelimit-limit-requests": "1800"})
        assert limiter.limits == {"tpm": 300000, "rpm": 1800}
        configured, _ = _limiter(tpm=1000)
        configured.learn({"x-ratelimit-limit-tokens": "300000"})
        assert configured.limits["tpm"] == 1000  # the configuration wins

    def test_configured_per_deployment_with_the_share(self, monkeypatch):
        monkeypatch.setenv("VAT_AZURE_TPM_MDM_GPT_6_SOL", "1000000")
        monkeypatch.setenv("VAT_AZURE_RPM", "6000")
        assert limiter_for("MDM_gpt-6-sol").limits == {"tpm": 850000, "rpm": 5100}
        assert limiter_for("other").limits == {"tpm": None, "rpm": 5100}
        assert limiter_for("MDM_gpt-6-sol") is limiter_for("MDM_gpt-6-sol")  # one per deployment

    def test_retry_after(self):
        class E(Exception):
            def __init__(self, headers):
                self.response = type("R", (), {"headers": headers})()

        assert retry_after(E({"retry-after-ms": "1500"})) == 1.5
        assert retry_after(E({"retry-after": "12"})) == 12.0
        assert retry_after(E({})) is None and retry_after(ValueError()) is None

    def test_estimate(self):
        assert estimate_tokens("x" * 300, "y" * 300, 1500, 1) == 200 + 1500 + 8000
        assert estimate_tokens("", "", 500, 0) == 500


# ── VatLLM.search ────────────────────────────────────────────────────────────

ANSWER = {"vat_id": "DE811128135"}
USAGE = {"input_tokens": 9000, "output_tokens": 500}


class _HTTPError(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = type("R", (), {"headers": headers or {}})()


class Script:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    async def __call__(self, system, user, effort):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply


class TestSearch:
    @pytest.mark.asyncio
    async def test_a_429_waits_as_azure_asks_and_pauses_the_deployment(self, tmp_path, monkeypatch):
        clock = Clock()
        limiter = RateLimiter(share=1.0, clock=clock, sleep=clock.sleep)
        monkeypatch.setitem(rate_limit._LIMITERS, "dep", limiter)
        search = Script(_HTTPError(429, {"retry-after-ms": "2500"}), (json.dumps(ANSWER), [], USAGE))
        llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"), search=search)
        answer = await llm.search("s", "u", reasoning_effort="low",
                                  limits=SearchLimits(retry_backoff=0.0, model="dep"))
        assert answer.response == ANSWER and search.calls == 2
        assert clock.slept == [2.5] and limiter.pauses == 1
        assert answer.usage["http_retries"] == 1 and answer.usage["rate_limit_wait_s"] == 2.5

    @pytest.mark.asyncio
    async def test_retries_end_at_the_limit(self, tmp_path):
        search = Script(_HTTPError(503))
        llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"), search=search)
        with pytest.raises(RuntimeError):
            await llm.search("s", "u", reasoning_effort="low", limits=SearchLimits(retry_backoff=0.0, max_retries=3))
        assert search.calls == 4

    @pytest.mark.asyncio
    async def test_a_model_error_is_not_retried(self, tmp_path):
        search = Script(_HTTPError(400))
        llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"), search=search)
        with pytest.raises(RuntimeError):
            await llm.search("s", "u", reasoning_effort="low", limits=SearchLimits(retry_backoff=0.0))
        assert search.calls == 1

    @pytest.mark.asyncio
    async def test_a_replay_never_waits(self, tmp_path, monkeypatch):
        asked = []

        class Recording(RateLimiter):
            async def acquire(self, tokens):
                asked.append(tokens)
                return 0.0

        monkeypatch.setitem(rate_limit._LIMITERS, "dep", Recording())
        cache = EvidenceCache(tmp_path).namespace("vat_llm")
        limits = SearchLimits(retry_backoff=0.0, model="dep")
        await VatLLM(cache, search=Script((json.dumps(ANSWER), [], USAGE))).search(
            "s", "u", reasoning_effort="low", limits=limits)
        replay = await VatLLM(cache, search=Script(_HTTPError(500))).search(
            "s", "u", reasoning_effort="low", limits=limits)
        assert replay.from_cache and replay.response == ANSWER and len(asked) == 1
