"""Pacing of the VAT lookups' Azure OpenAI calls (2026-11-08).

Every live call of :meth:`vat.llm_cache.VatLLM.search` waits here first, so
many lookups in parallel queue for quota instead of running into HTTP 429
(which, before, cost the entity its answer: LOOKUP_INCOMPLETE). A replay from
the cache never waits. The limiter only decides *when* a call is sent, never
what is sent, so results are unchanged.

* One limiter per deployment, process-wide (:func:`limiter_for`): every
  request and job in the process shares the deployment's quota.
* Two token buckets, requests per minute and tokens per minute. Azure checks
  the quota over short windows, so a bucket holds at most
  :data:`BURST_SECONDS` worth of quota; it refills continuously.
* The limits: ``VAT_AZURE_TPM_<DEPLOYMENT>`` / ``VAT_AZURE_RPM_<DEPLOYMENT>``
  (else ``VAT_AZURE_TPM`` / ``VAT_AZURE_RPM``), else what Azure reports in
  its ``x-ratelimit-limit-*`` response headers (:meth:`RateLimiter.learn`);
  either times ``VAT_AZURE_QUOTA_SHARE`` (0.85). Nothing known yet: no
  pacing, only the 429 handling.
* A call reserves an estimate of its tokens (:func:`estimate_tokens`) and is
  settled with the real count once it has answered.
* A 429 pauses the whole deployment for the time Azure asks
  (:meth:`RateLimiter.pause`), so the other lookups stop too instead of
  piling up retries.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: A bucket holds this many seconds of quota (Azure enforces RPM/TPM over
#: short windows, not the full minute).
BURST_SECONDS = 10.0
#: The share of the quota used; the rest is headroom for other callers.
QUOTA_SHARE = 0.85


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        logger.warning("%s=%r is not a number; ignored", name, raw)
        return None


def _env_key(deployment: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", deployment).strip("_").upper()


def estimate_tokens(system: str, user: str, max_output_tokens: int, max_searches: int) -> int:
    """A call's tokens before it is sent: its prompt (about 3 characters a
    token for German text), its output cap, and about 8,000 tokens of search
    results per search (AD 50 live: search calls about 15k tokens in all)."""
    return (len(system) + len(user)) // 3 + max_output_tokens + 8000 * max_searches


class _Bucket:
    def __init__(self, per_minute: float) -> None:
        self.rate = per_minute / 60.0
        self.capacity = max(1.0, self.rate * BURST_SECONDS)
        self.level = self.capacity

    def refill(self, elapsed: float) -> None:
        self.level = min(self.capacity, self.level + elapsed * self.rate)

    def wait_for(self, amount: float) -> float:
        """Seconds until *amount* can be taken. More than the bucket holds:
        it waits for a full bucket and then goes below zero."""
        need = min(amount, self.capacity)
        return 0.0 if self.level >= need else (need - self.level) / self.rate


class RateLimiter:
    def __init__(self, tpm: float | None = None, rpm: float | None = None, *, name: str = "",
                 share: float = QUOTA_SHARE, configured: bool | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        self.name = name
        self.share = share
        self._clock, self._sleep = clock, sleep
        self._tokens = _Bucket(tpm * share) if tpm else None
        self._requests = _Bucket(rpm * share) if rpm else None
        #: Limits given in the configuration are never replaced by the headers.
        self.configured = bool(tpm or rpm) if configured is None else configured
        self._last = clock()
        self._paused_until = 0.0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: Any = None
        self.waited_s = 0.0
        self.pauses = 0

    @property
    def limits(self) -> dict[str, float | None]:
        """The limits in force (after the share), per minute."""
        return {"tpm": round(self._tokens.rate * 60) if self._tokens else None,
                "rpm": round(self._requests.rate * 60) if self._requests else None}

    def _refill(self) -> float:
        now = self._clock()
        for b in (self._tokens, self._requests):
            if b is not None:
                b.refill(now - self._last)
        self._last = now
        return now

    async def acquire(self, tokens: int) -> float:
        """Wait until the call may be sent; reserve *tokens* and one request.
        Callers are served in order. Returns the seconds waited."""
        waited = 0.0
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:  # one lock per event loop
            self._lock, self._lock_loop = asyncio.Lock(), loop
        async with self._lock:
            while True:
                now = self._refill()
                wait = max(0.0, self._paused_until - now)
                if not wait:
                    wait = max(self._tokens.wait_for(tokens) if self._tokens else 0.0,
                               self._requests.wait_for(1) if self._requests else 0.0)
                if wait <= 0:
                    if self._tokens:
                        self._tokens.level -= tokens
                    if self._requests:
                        self._requests.level -= 1
                    self.waited_s += waited
                    return waited
                await self._sleep(wait)
                waited += wait

    def settle(self, reserved: int, actual: int | None) -> None:
        """The call's real token count replaces its estimate."""
        if self._tokens is not None and actual is not None:
            self._tokens.level = min(self._tokens.capacity, self._tokens.level + reserved - actual)

    def pause(self, seconds: float) -> None:
        """Azure answered 429: nothing is sent on this deployment for
        *seconds*, and the buckets restart empty."""
        self.pauses += 1
        self._refill()
        self._paused_until = max(self._paused_until, self._clock() + seconds)
        for b in (self._tokens, self._requests):
            if b is not None:
                b.level = min(b.level, 0.0)

    def learn(self, headers: Any) -> None:
        """Limits from Azure's ``x-ratelimit-limit-tokens`` /
        ``-requests`` headers, when none were configured. Logged once."""
        if self.configured or headers is None:
            return
        tpm = _header_number(headers, "x-ratelimit-limit-tokens")
        rpm = _header_number(headers, "x-ratelimit-limit-requests")
        if not (tpm or rpm):
            return
        self._tokens = _Bucket(tpm * self.share) if tpm else None
        self._requests = _Bucket(rpm * self.share) if rpm else None
        self.configured = True
        logger.info("Azure quota of %s: %s tokens/min, %s requests/min; pacing at %.0f%%",
                    self.name or "the deployment", tpm, rpm, self.share * 100)


def _header_number(headers: Any, name: str) -> float | None:
    try:
        raw = headers.get(name)
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError, AttributeError):
        return None


def retry_after(exc: BaseException) -> float | None:
    """Seconds Azure asked to wait (``retry-after-ms``, else ``retry-after``),
    from an API error's response headers."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    ms = _header_number(headers, "retry-after-ms")
    if ms is not None:
        return ms / 1000.0
    return _header_number(headers, "retry-after")


_LIMITERS: dict[str, RateLimiter] = {}


def limiter_for(deployment: str) -> RateLimiter:
    """The process-wide limiter of *deployment*."""
    limiter = _LIMITERS.get(deployment)
    if limiter is None:
        key = _env_key(deployment)
        tpm = _env_float(f"VAT_AZURE_TPM_{key}") or _env_float("VAT_AZURE_TPM")
        rpm = _env_float(f"VAT_AZURE_RPM_{key}") or _env_float("VAT_AZURE_RPM")
        share = _env_float("VAT_AZURE_QUOTA_SHARE") or QUOTA_SHARE
        limiter = _LIMITERS[deployment] = RateLimiter(tpm, rpm, name=deployment, share=share)
    return limiter


def reset() -> None:
    """Forget every limiter (tests)."""
    _LIMITERS.clear()
