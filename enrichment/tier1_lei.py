"""Tier 1 (company): GLEIF / LEI registry lookup.

The company counterpart to the ROR institution lookup (``tier1_ror``).
For company-type records this resolves the official legal name and a
Legal Entity Identifier from the free GLEIF API (no auth, JSON:API
format) and uses it as the canonical ``name1`` BEFORE the LLM company
canonicalization fallback.

Strategy (mirrors the ROR client's primary-then-fallback shape):

1. **Exact-ish (precise):** ``lei-records`` filtered by
   ``filter[entity.legalName]``, ``filter[entity.status]=ACTIVE`` and a
   country filter (``filter[entity.legalAddress.country]`` = the record's
   ISO alpha-2). NOTE: GLEIF's legalName filter is *fulltext*, not exact
   equality — "Pfizer" returns "PFIZER AG", "PFIZER INC.", etc. — so the
   verification guard below is mandatory even on this "precise" path.
2. **Fuzzy (recall):** ``fuzzycompletions?field=entity.legalName`` then
   resolve the candidate to its full ``lei-record``. Best-effort: GLEIF's
   typeahead frequently returns nothing, so a no-op here is normal and
   simply means a miss.

VERIFICATION GUARD (required): every candidate's ``legalName`` is scored
against the input name with rapidfuzz ``token_sort_ratio`` (both
lowercased — GLEIF returns names UPPERCASE). Candidates below
``LEI_NAME_MATCH_THRESHOLD`` are rejected. GLEIF fuzzy is statistical;
without this guard it fabricates matches.

COUNTRY GUARD (required): when a country is known, candidates whose
``legalAddress.country`` differs from it are rejected during selection on
BOTH paths. GLEIF's country filter only constrains the precise request, and
``fuzzycompletions`` cannot be country-filtered at the API — so without this
post-filter the fuzzy path returns same-name companies from the wrong
country (a different legal entity). See ``_best_verified_candidate``.

A GLEIF failure (timeout / 5xx / malformed) must NEVER fail the record —
every error path returns a miss/error dict and the orchestrator falls
through to the existing LLM path unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from rapidfuzz import fuzz

from llm.openai_client import resolve_tls_verify
from utils.cache import CacheKey, legacy_lookup_key, lookup_key

logger = logging.getLogger(__name__)

# JSON:API content type GLEIF speaks.
_GLEIF_ACCEPT = "application/vnd.api+json"

# Legal-form / entity-suffix tokens stripped before name verification, so an
# input that omits the legal form ("Novartis") still verifies against the
# official name that carries it ("NOVARTIS AG"). Deliberately ONLY true legal
# forms — never descriptive words like "products" / "holdings" / "group",
# which distinguish a subsidiary from its parent and must NOT be collapsed.
_LEGAL_FORM_TOKENS = {
    "ag", "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "corporation", "co", "company", "gmbh", "sa", "sas", "sarl", "nv",
    "bv", "plc", "spa", "srl", "ab", "oyj", "oy", "as", "kg", "kgaa",
    "se", "pty", "llp", "lp", "pllc", "pc", "aps", "kk", "ulc",
}

_TOKEN_SPLIT_RE = re.compile(r"[a-z0-9]+")


def _normalise_legal_name(s: str) -> str:
    """Lowercase, tokenise, and drop legal-form suffix tokens."""
    return " ".join(
        t for t in _TOKEN_SPLIT_RE.findall((s or "").lower())
        if t not in _LEGAL_FORM_TOKENS
    )

# Module-level cache — mirrors the ROR client's per-process cache and, like it,
# is keyed by ``utils.cache.lookup_key(name, country_code)``: the name
# normalised (lowercase, trim, collapse whitespace, strip punctuation, fold
# accents) plus the country filter. This namespace already carried country;
# what it gained is the punctuation/accent collapse, so "Lockheed Martin Corp."
# and "Lockheed Martin Corp" cost one GLEIF call, not two.
#
# Cache key only: `name` reaches GLEIF unnormalised, and _name_match_score()
# — the name-verification guard — never sees the key.
_lei_cache: "dict[CacheKey, dict[str, Any]]" = {}
_lei_legacy_seen: "set[CacheKey]" = set()
_lei_normalised_hits = 0


def clear_lei_cache() -> None:
    """Reset the module-level LEI cache (called per batch / between tests)."""
    global _lei_normalised_hits
    _lei_cache.clear()
    _lei_legacy_seen.clear()
    _lei_normalised_hits = 0


def lei_normalised_hits() -> int:
    """Lookups the normalised cache key saved that lowercasing would not."""
    return _lei_normalised_hits


def _name_match_score(query: str, legal_name: str) -> float:
    """Name-verification score (0-100) between the input and a candidate.

    The metric is rapidfuzz ``token_sort_ratio`` (per the design), taken as
    the max of the raw score and the score with legal-form suffixes stripped
    (AG / Inc / Ltd / GmbH …). The legal-form strip lets an input that omits
    the legal form ("Novartis") verify against the official name that carries
    it ("NOVARTIS AG") — WITHOUT the subset-collapse a ``token_set_ratio``
    would introduce: token_set scores ANY contained substring 100, which
    would wrongly accept "Personalvorsorgestiftung der Pfizer AG in
    Liquidation" (a different entity) for "Pfizer AG". token_sort stays
    length-sensitive, so that wrong entity scores ~21 and is rejected.

    GLEIF returns legal names UPPERCASE; both sides are lowercased.
    """
    q = (query or "").strip().lower()
    n = (legal_name or "").strip().lower()
    if not q or not n:
        return 0.0
    raw = fuzz.token_sort_ratio(q, n)
    nq, nn = _normalise_legal_name(query), _normalise_legal_name(legal_name)
    stripped = fuzz.token_sort_ratio(nq, nn) if (nq and nn) else 0.0
    return float(max(raw, stripped))


def _record_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields we care about from a GLEIF lei-record dict."""
    attrs = record.get("attributes", {}) or {}
    entity = attrs.get("entity", {}) or {}
    legal_name = (entity.get("legalName", {}) or {}).get("name")
    legal_address = entity.get("legalAddress", {}) or {}
    return {
        "lei_id": record.get("id"),
        "legal_name": legal_name,
        "status": entity.get("status"),
        "country": legal_address.get("country"),
    }


def _best_verified_candidate(
    name: str,
    records: list[dict[str, Any]],
    threshold: float,
    country_code: str | None = None,
) -> tuple[dict[str, Any] | None, float]:
    """Pick the best name-verified candidate from a list of lei-records.

    Prefers ACTIVE entities, then highest name-match score. Returns
    ``(fields, score)`` for the best candidate at/above *threshold*, or
    ``(None, best_score)`` when nothing qualifies (best_score aids logging).

    When *country_code* is given, candidates whose legal-address country does
    not match it are rejected outright. This is the country guard: GLEIF's
    ``filter[entity.legalAddress.country]`` only constrains the *exact* path,
    and the ``fuzzycompletions`` typeahead cannot be country-filtered at the
    API at all — so without this post-filter the fuzzy path (and any
    server-side filter slip) can return a same-name company from the wrong
    country, which is fundamentally a different legal entity.
    """
    want_country = (country_code or "").strip().upper() or None
    best_fields: dict[str, Any] | None = None
    best_rank: tuple[int, float] = (-1, -1.0)
    best_score = 0.0
    for rec in records:
        fields = _record_fields(rec)
        if not fields.get("lei_id") or not fields.get("legal_name"):
            continue
        if want_country:
            cand_country = (fields.get("country") or "").strip().upper()
            if cand_country != want_country:
                logger.info(
                    "GLEIF: rejecting '%s' (LEI=%s) — country %s != requested %s",
                    fields.get("legal_name"), fields.get("lei_id"),
                    cand_country or "?", want_country,
                )
                continue
        score = _name_match_score(name, fields["legal_name"])
        best_score = max(best_score, score)
        if score < threshold:
            continue
        is_active = 1 if (fields.get("status") == "ACTIVE") else 0
        rank = (is_active, score)
        if rank > best_rank:
            best_rank = rank
            best_fields = fields
    return best_fields, best_score


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str] | None,
    max_retries: int,
) -> dict[str, Any]:
    """GET *url* and parse JSON, retrying transient errors with backoff.

    Raises on a final failure so the caller's except blocks can classify
    it as an error (vs. a clean miss).
    """
    attempt = 0
    while True:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # Only retry transient failures (network, timeout, 5xx). A 4xx
            # is not going to get better on retry.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status is None or status >= 500
            attempt += 1
            if not transient or attempt > max_retries:
                raise
            backoff = 0.5 * (2 ** (attempt - 1))
            logger.info(
                "GLEIF transient error (%s), retry %d/%d after %.1fs",
                status or type(exc).__name__, attempt, max_retries, backoff,
            )
            await asyncio.sleep(backoff)


async def call_lei(
    name: str,
    country_code: str | None = None,
    base_url: str = "https://api.gleif.org/api/v1",
    timeout: float = 15.0,
    max_retries: int = 2,
    threshold: float = 88.0,
) -> dict[str, Any]:
    """Look up a company's official legal name + LEI from GLEIF.

    Returns a dict:
      * verified match — ``{"matched": True, "lei_id", "legal_name",
        "country", "status", "strategy": "exact"|"fuzzy",
        "confidence": "high"|"medium", "score"}``
      * miss / below-threshold — ``{"matched": False, "strategy", "score"}``
      * API error / timeout — ``{"matched": False, "error": True}``

    Never raises — a GLEIF failure must not fail the record.
    """
    if not name or not name.strip():
        return {"matched": False, "strategy": None, "score": 0.0}

    # Cache key only — the GLEIF request below uses `name` verbatim.
    global _lei_normalised_hits
    cache_key = lookup_key(name, country_code)
    legacy_key = legacy_lookup_key(name, country_code)
    if cache_key in _lei_cache:
        if legacy_key not in _lei_legacy_seen:
            _lei_normalised_hits += 1
        return _lei_cache[cache_key]
    _lei_legacy_seen.add(legacy_key)

    base = base_url.rstrip("/")
    records_url = f"{base}/lei-records"

    def _cache(result: dict[str, Any]) -> dict[str, Any]:
        _lei_cache[cache_key] = result
        return result

    try:
        # verify=resolve_tls_verify() — reuse the OpenAI client's TLS trust
        # resolution so GLEIF survives a corporate TLS-inspecting VPN. On
        # such a VPN the public certifi bundle fails the handshake
        # ("CERTIFICATE_VERIFY_FAILED: unable to get local issuer
        # certificate"); resolve_tls_verify() prefers a configured corp CA
        # bundle (AZURE_OPENAI_CA_BUNDLE / REQUESTS_CA_BUNDLE / SSL_CERT_FILE)
        # and falls back to certifi off-VPN.
        async with httpx.AsyncClient(
            timeout=timeout, verify=resolve_tls_verify(),
            headers={"Accept": _GLEIF_ACCEPT},
        ) as client:
            # ── Strategy A: precise filter (legalName + ACTIVE + country) ──
            params: dict[str, str] = {
                "filter[entity.legalName]": name,
                "filter[entity.status]": "ACTIVE",
                "page[size]": "10",
            }
            if country_code:
                params["filter[entity.legalAddress.country]"] = country_code

            logger.info(
                "GLEIF exact request: name='%s' country=%s",
                name[:80], country_code,
            )
            data = await _get_json(client, records_url, params, max_retries)
            records = data.get("data", []) or []

            fields, best_score = _best_verified_candidate(
                name, records, threshold, country_code=country_code,
            )
            if fields is not None:
                result = {
                    "matched": True,
                    "strategy": "exact",
                    "confidence": "high",
                    "score": best_score,
                    **fields,
                }
                logger.info(
                    "GLEIF exact matched '%s' → '%s' (LEI=%s, score=%.1f)",
                    name[:60], fields["legal_name"], fields["lei_id"], best_score,
                )
                return _cache(result)

            logger.info(
                "GLEIF exact: no verified candidate for '%s' "
                "(%d records, best score=%.1f < %.1f), trying fuzzy",
                name[:60], len(records), best_score, threshold,
            )

            # ── Strategy B: fuzzycompletions fallback (best-effort) ────────
            fuzzy_result = await _fuzzy_lookup(
                client, base, records_url, name, country_code,
                max_retries, threshold,
            )
            if fuzzy_result is not None:
                return _cache(fuzzy_result)

            return _cache({"matched": False, "strategy": "fuzzy", "score": best_score})

    except httpx.HTTPStatusError as exc:
        logger.error(
            "GLEIF HTTP %d for '%s': %s",
            exc.response.status_code, name[:80], exc.response.text[:200],
        )
        return {"matched": False, "error": True}
    except Exception:
        logger.exception("GLEIF lookup failed for '%s'", name[:80])
        return {"matched": False, "error": True}


async def _fuzzy_lookup(
    client: httpx.AsyncClient,
    base: str,
    records_url: str,
    name: str,
    country_code: str | None,
    max_retries: int,
    threshold: float,
) -> dict[str, Any] | None:
    """fuzzycompletions → resolve candidate → verify. Returns a match dict
    or ``None`` (no usable/verified candidate). Best-effort: GLEIF's
    typeahead often returns nothing, which is a normal miss."""
    fuzzy_url = f"{base}/fuzzycompletions"
    data = await _get_json(
        client, fuzzy_url,
        {"field": "entity.legalName", "q": name},
        max_retries,
    )
    completions = data.get("data", []) or []
    if not completions:
        return None

    # Resolve each completion to its full lei-record, verify, keep the best.
    seen: set[str] = set()
    candidate_records: list[dict[str, Any]] = []
    for comp in completions[:5]:
        rel = (
            ((comp.get("relationships") or {}).get("lei-records") or {})
            .get("data") or {}
        )
        lei = rel.get("id")
        if not lei or lei in seen:
            continue
        seen.add(lei)
        try:
            rec_data = await _get_json(
                client, f"{records_url}/{lei}", None, max_retries,
            )
        except Exception:
            logger.info("GLEIF fuzzy: failed to resolve LEI %s", lei)
            continue
        rec = rec_data.get("data")
        if isinstance(rec, dict):
            candidate_records.append(rec)

    fields, best_score = _best_verified_candidate(
        name, candidate_records, threshold, country_code=country_code,
    )
    if fields is None:
        logger.info(
            "GLEIF fuzzy: no verified candidate for '%s' (best score=%.1f)",
            name[:60], best_score,
        )
        return None

    logger.info(
        "GLEIF fuzzy matched '%s' → '%s' (LEI=%s, score=%.1f)",
        name[:60], fields["legal_name"], fields["lei_id"], best_score,
    )
    return {
        "matched": True,
        "strategy": "fuzzy",
        "confidence": "medium",
        "score": best_score,
        **fields,
    }


class LEIClient:
    """Thin wrapper around call_lei() for dependency injection and mocking
    (mirrors RORClient)."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._base_url = settings.gleif_api_base
        self._timeout = settings.gleif_timeout_seconds
        self._max_retries = settings.lei_max_retries
        self._threshold = settings.lei_name_match_threshold

    async def call(
        self,
        name: str,
        country_code: str | None = None,
    ) -> dict[str, Any]:
        """Look up a company name via GLEIF with an optional country filter."""
        return await call_lei(
            name,
            country_code=country_code,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=self._max_retries,
            threshold=self._threshold,
        )
