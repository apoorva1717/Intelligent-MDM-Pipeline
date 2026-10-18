"""VIES client: one VAT number → VALID / INVALID / SERVICE_UNAVAILABLE / ERROR.

The European Commission's VIES REST API, ``POST /check-vat-number``. Field
names are the ones in the published contract (``swagger_publicVAT.yaml``,
"Vies on-the-Web Endpoint" 1.0.0), verified against live responses on
2026-10-15:

* request ``CheckVatRequest`` — ``countryCode``, ``vatNumber``, optionally
  ``requesterMemberStateCode`` + ``requesterNumber``;
* answer ``CheckVatResponse`` — ``valid``, ``name``, ``address``,
  ``requestDate``, ``requestIdentifier`` (the "Consultation Number", empty
  unless a requester is sent);
* failure ``CommonResponse`` — ``actionSucceed: false`` + ``errorWrappers[]``
  of ``{error, message}``. **Delivered with HTTP 200**, so the body decides,
  never the status code.

Four outcomes, and the line between the middle two is the point of the module:
an answer VIES could not give is **never** reported as INVALID. ``INVALID``
means VIES answered ``valid: false``; every "member state down / busy /
timed out / rate-limited" answer is ``SERVICE_UNAVAILABLE`` and is retried
with backoff; anything else (bad input, bad requester, blocked) is ``ERROR``.

``name`` / ``address`` of ``"---"`` mean, in VIES's words, "the Member State
that issued the VAT number does not allow to disclose this information"
(Germany and Spain always). They come back as None with
``name_disclosed=False`` — a correct answer, not a failed lookup.

Caching follows the registry clients (``utils.cache.cached_registry_get``):
the raw response body is recorded, never the decision, and entries are
immutable. Only a VALID or INVALID body is recorded — every other outcome is
raised before the store is touched, so a "member state unavailable" is never
replayed. The store is this client's own ``vies`` namespace rather than the
process-wide active cache, so building a client cannot replace the store a
concurrent ``/enrich`` is using. ``CACHE_FROZEN`` applies: a miss is
SERVICE_UNAVAILABLE (``FROZEN_CACHE_MISS``) with no network call.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable

import httpx

from llm.openai_client import resolve_tls_verify
from utils.cache import DiskCache, EvidenceCache
from vat.normalize import VatFormat

logger = logging.getLogger(__name__)

# ── States ───────────────────────────────────────────────────────────────────
VALID = "VALID"
INVALID = "INVALID"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
ERROR = "ERROR"

SOURCE = "VIES"
NAMESPACE = "vies"

#: VIES's placeholder for a name/address the member state does not disclose.
NOT_DISCLOSED = "---"

#: ``errorWrappers[].error`` values that mean "ask again later" (WSDL fault
#: descriptions and the VIES web app's messages). Retried; SERVICE_UNAVAILABLE.
TRANSIENT_ERRORS: frozenset[str] = frozenset({
    "SERVICE_UNAVAILABLE",
    "MS_UNAVAILABLE",
    "TIMEOUT",
    "GLOBAL_MAX_CONCURRENT_REQ",
    "GLOBAL_MAX_CONCURRENT_REQ_TIME",
    "MS_MAX_CONCURRENT_REQ",
    "MS_MAX_CONCURRENT_REQ_TIME",
})
# Every other code — INVALID_INPUT, INVALID_REQUESTER_INFO, VAT_BLOCKED,
# IP_BLOCKED, or one not seen before — is ERROR and is not retried.

#: ``error_code`` for a CACHE_FROZEN run with no recording of this number.
FROZEN_CACHE_MISS = "FROZEN_CACHE_MISS"
#: ``error_code`` for a 2xx body that is neither an answer nor an error.
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"

_SEPARATORS = re.compile(r"[\s.\-]+")


@dataclass(frozen=True)
class ViesResult:
    """VIES's answer for one normalised VAT number."""

    status: str
    vat: str
    name: str | None = None
    address: str | None = None
    #: True / False on VALID (False: VIES returned "---"); None otherwise.
    name_disclosed: bool | None = None
    #: ``requestDate`` as VIES sent it (ISO date-time, UTC).
    request_date: str | None = None
    #: ``requestIdentifier`` — only issued when a requester VAT ID is sent.
    consultation_number: str | None = None
    #: ``errorWrappers[].error``, an httpx exception name, ``HTTP_<code>``,
    #: FROZEN_CACHE_MISS or MALFORMED_RESPONSE. None on VALID / INVALID.
    error_code: str | None = None
    #: HTTP calls made for this result (0 when replayed from the cache).
    attempts: int = 0
    from_cache: bool = False
    source: str = SOURCE


class _NotRecorded(Exception):
    """Any outcome other than VALID / INVALID. Raised before the store is
    written, which is what keeps a transient answer out of the cache."""

    def __init__(self, result: ViesResult) -> None:
        super().__init__(result.error_code)
        self.result = result


def _disclosed(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if not text or text == NOT_DISCLOSED else text


def parse_response(vat: str, body: Any) -> ViesResult:
    """A VIES JSON body → :class:`ViesResult`. Pure; no I/O."""
    if not isinstance(body, dict):
        return ViesResult(ERROR, vat, error_code=MALFORMED_RESPONSE)
    wrappers = body.get("errorWrappers")
    if body.get("actionSucceed") is False or wrappers:
        code = None
        if isinstance(wrappers, list) and wrappers and isinstance(wrappers[0], dict):
            code = wrappers[0].get("error")
        code = str(code or "UNKNOWN_ERROR")
        status = SERVICE_UNAVAILABLE if code in TRANSIENT_ERRORS else ERROR
        return ViesResult(status, vat, error_code=code)

    valid = body.get("valid")
    if not isinstance(valid, bool):
        return ViesResult(ERROR, vat, error_code=MALFORMED_RESPONSE)
    request_date = body.get("requestDate") or None
    consultation = str(body.get("requestIdentifier") or "").strip() or None
    if not valid:
        return ViesResult(
            INVALID, vat, request_date=request_date,
            consultation_number=consultation,
        )
    name = _disclosed(body.get("name"))
    return ViesResult(
        VALID, vat,
        name=name,
        address=_disclosed(body.get("address")),
        name_disclosed=name is not None,
        request_date=request_date,
        consultation_number=consultation,
    )


def requester_fields(requester_vat: str | None) -> dict[str, str]:
    """``VIES_REQUESTER_VAT`` → the two ``CheckVatRequest`` requester fields.

    Empty → ``{}`` (no requester; no consultation number). A value that is not
    a two-letter prefix plus a number is a configuration error and raises,
    rather than being sent and failing every row as INVALID_REQUESTER_INFO.
    """
    compact = _SEPARATORS.sub("", requester_vat or "").upper()
    if not compact:
        return {}
    if len(compact) < 4 or not compact[:2].isalpha():
        raise ValueError(
            "VIES_REQUESTER_VAT must be an EU VAT ID with its country prefix, "
            "e.g. DE123456789",
        )
    return {"requesterMemberStateCode": compact[:2], "requesterNumber": compact[2:]}


class VIESClient:
    """Checks normalised VAT numbers against VIES.

    Only :class:`~vat.normalize.VatFormat` values with ``status == FORMAT_OK``
    are sent, each unique normalised number once per :meth:`check_many` call.
    """

    def __init__(
        self,
        settings: Any,
        *,
        cache: EvidenceCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._url = settings.vies_api_base.rstrip("/") + "/check-vat-number"
        self._timeout = settings.vies_timeout_seconds
        self._max_attempts = max(1, settings.vies_max_attempts)
        self._backoff = settings.vies_backoff_seconds
        self._concurrency = max(1, settings.vies_max_concurrency)
        self._requester = requester_fields(settings.vies_requester_vat)
        if cache is None:
            root = (settings.evidence_cache_dir or "").strip() or None
            cache = EvidenceCache(root, frozen=settings.cache_frozen)
        self._cache = cache
        self._store: DiskCache = cache.namespace(NAMESPACE)
        self._transport = transport
        self._sleep = sleep

    @property
    def frozen(self) -> bool:
        return self._store.replay_only

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "headers": {"Accept": "application/json"},
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        else:
            # Same TLS trust resolution as the registry clients, so VIES
            # survives a TLS-inspecting corporate VPN.
            kwargs["verify"] = resolve_tls_verify()
        return httpx.AsyncClient(**kwargs)

    def _request_body(self, fmt: VatFormat) -> dict[str, str]:
        vat = fmt.normalized or ""
        return {"countryCode": vat[:2], "vatNumber": vat[2:], **self._requester}

    def cache_key(self, vat: str) -> str:
        """Keyed on the normalised number (and the requester, which decides
        whether the recorded answer carries a consultation number).

        Deliberately not ``http_disk_key(url, …)``: the URL adds ~85
        characters to every filename, which under a deep cache directory
        passes the Windows 260-character path limit — and a fixture that
        cannot be written is skipped silently by ``DiskCache.set``.
        """
        requester = "".join(self._requester.values())
        return f"{vat}@{requester}" if requester else vat

    async def _live(
        self, client: httpx.AsyncClient, vat: str, body: dict[str, str],
    ) -> Any:
        """One POST. Returns the body of a VALID / INVALID answer; raises
        :class:`_NotRecorded` for anything else."""
        try:
            resp = await client.post(self._url, json=body)
        except httpx.TransportError as exc:  # includes every httpx timeout
            raise _NotRecorded(
                ViesResult(SERVICE_UNAVAILABLE, vat, error_code=type(exc).__name__),
            ) from exc
        try:
            data = resp.json()
        except ValueError:
            data = None
        result = parse_response(vat, data)
        answered = result.status in (VALID, INVALID)
        if answered and resp.is_success:
            return data
        if not resp.is_success and (answered or result.error_code == MALFORMED_RESPONSE):
            # No VIES error code to read (or an answer on an error status,
            # which is not trusted): classify by the HTTP status alone.
            code = resp.status_code
            status = SERVICE_UNAVAILABLE if code >= 500 or code == 429 else ERROR
            result = ViesResult(status, vat, error_code=f"HTTP_{code}")
        raise _NotRecorded(result)

    async def _check(self, client: httpx.AsyncClient, fmt: VatFormat) -> ViesResult:
        vat = fmt.normalized or ""
        key = self.cache_key(vat)
        recorded = self._store.get(key)
        if recorded is not None:
            body = recorded.get("body") if isinstance(recorded, dict) else recorded
            return replace(parse_response(vat, body), from_cache=True)
        if self._store.replay_only:
            logger.info("VIES: frozen cache has no response for %s", vat)
            return ViesResult(SERVICE_UNAVAILABLE, vat, error_code=FROZEN_CACHE_MISS)

        request = self._request_body(fmt)
        attempt = 0
        while True:
            attempt += 1
            self._cache.note_network_call(NAMESPACE)
            try:
                data = await self._live(client, vat, request)
            except _NotRecorded as exc:
                result = replace(exc.result, attempts=attempt)
                if result.status != SERVICE_UNAVAILABLE or attempt >= self._max_attempts:
                    logger.info(
                        "VIES %s for %s after %d attempt(s): %s",
                        result.status, vat, attempt, result.error_code,
                    )
                    return result
                backoff = self._backoff * (2 ** (attempt - 1))
                logger.info(
                    "VIES transient (%s) for %s, retry %d/%d after %.1fs",
                    result.error_code, vat, attempt, self._max_attempts - 1, backoff,
                )
                await self._sleep(backoff)
                continue
            # Recorded only here: a VALID / INVALID body. The request body
            # sent is stored for the reader; the key is what replays it.
            self._store.set(key, {"url": self._url, "params": request, "body": data})
            return replace(parse_response(vat, data), attempts=attempt)

    async def check(self, fmt: VatFormat) -> ViesResult:
        """Check one value. It must be FORMAT_OK."""
        results = await self.check_many([fmt])
        if fmt.normalized not in results:
            raise ValueError(f"not eligible for VIES: {fmt.status}")
        return results[fmt.normalized]

    async def check_many(self, formats: Iterable[VatFormat]) -> dict[str, ViesResult]:
        """Check every unique FORMAT_OK value once. Keyed by normalised VAT;
        values that are not FORMAT_OK are skipped, never sent."""
        unique: dict[str, VatFormat] = {}
        for fmt in formats:
            if fmt.vies_eligible and fmt.normalized:
                unique.setdefault(fmt.normalized, fmt)
        if not unique:
            return {}
        semaphore = asyncio.Semaphore(self._concurrency)
        async with self._client() as client:
            async def run(fmt: VatFormat) -> ViesResult:
                async with semaphore:
                    return await self._check(client, fmt)

            results = await asyncio.gather(*(run(f) for f in unique.values()))
        return {r.vat: r for r in results}
