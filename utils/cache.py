"""Caches for ROR, SERP and page-read results.

Three scopes:

* :class:`BatchCache` — created once per enrichment batch. Holds the ROR
  cache (kept per-batch to avoid stale failures) and the per-batch SERP
  cache.
* :class:`SerpCache` — an in-memory, process-level SERP cache shared
  across every batch an orchestrator handles. It lets repeated or
  overlapping queries reuse a previous SERP result instead of re-hitting
  the search API, for as long as the process is alive. No file I/O — it
  lives entirely in memory.
* :class:`PageCache` — the same shape for Fix 3's page reads, keyed on the
  registrable domain, and additionally backed by an on-disk fixture store.
  A page read is a claim about what a website said on a given day, so unlike
  a SERP result it has to survive the process: re-running a thesis batch must
  reproduce the corroboration decisions rather than re-litigate them against
  whatever the site says today.

Cache keys
----------

Every namespace keys on a *normalised* form of the query plus the country,
built here by :func:`lookup_key` / :func:`serp_key`. Lowercasing alone collapses
"MIT" and "mit" but not ``Coastal Diagnostics, Inc.`` against
``Coastal Diagnostics Inc``, so within one batch the same organisation was
looked up under several keys, got several outcomes, and the batch emitted
contradictory records for one entity.

The normaliser is :func:`dedup.signatures.normalize_key` — reused rather than
reimplemented: lowercase, trim, collapse whitespace, strip punctuation, fold
accents (``Universität`` → ``universitat``). It deliberately does **not** strip
legal forms or expand abbreviations, which is the right conservatism for a
cache key too.

Three conditions hold for every key built here, and the first is what keeps the
punctuation stripping safe:

1. The normalised key is used ONLY as a dictionary key for cache lookup.
2. The value SENT to the ROR / LEI / SERP APIs is always the original,
   unnormalised string. The key decides *whether* a call is made; it is never
   the payload. Pinned by ``tests/test_cache_normalisation.py``.
3. The key never reaches output, never reaches an LLM prompt, and never enters
   ``_compute_name_score()`` or any other scoring path.

Country is part of every key: a name-only key lets two genuinely distinct
organisations that share a name in different countries share a cache entry.
(It does not separate a same-country name collision — two US "Cardinal
Instruments" still collide, which is out of scope here.)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional, Tuple

from dedup.signatures import normalize_key

# Key type shared by the ROR and LEI namespaces. Spelled with typing.Tuple
# because this alias is evaluated at runtime (the `annotations` future import
# only defers real annotations).
CacheKey = Tuple[str, Optional[str]]


def _country_part(country_code: str | None) -> str | None:
    return (country_code or "").strip().upper() or None


def lookup_key(name: str | None, country_code: str | None = None) -> CacheKey:
    """Cache key for a registry (ROR / LEI) lookup of *name* in *country_code*.

    INTERNAL CACHE KEY ONLY — see the module docstring. The unnormalised
    *name* is what actually reaches the API.
    """
    return (normalize_key(name), _country_part(country_code))


def legacy_lookup_key(name: str | None, country_code: str | None = None) -> CacheKey:
    """The pre-fix key (lowercase + strip only).

    Kept solely to measure ``cache_hits_after_normalisation`` — how many
    lookups the normalised key saved that the lowercased key would have
    missed. Never used to store or retrieve a value.
    """
    return ((name or "").strip().lower(), _country_part(country_code))


def serp_key(query: str, country: str | None = None) -> tuple[str, bool, str | None]:
    """Cache key for a SERP query.

    Same normalisation as :func:`lookup_key`, plus one extra component: whether
    the query carried a quoted exact phrase. ``normalize_key`` strips the quote
    characters, which would make an exact-phrase query and its unquoted retry
    (website_resolver §8) collide — the retry would be served the very results
    it exists to get away from. Quoting changes what is searched, so it is part
    of the identity of the query rather than noise to fold away.
    """
    return (normalize_key(query), '"' in (query or ""), _country_part(country))


def legacy_serp_key(query: str, country: str | None = None) -> tuple[str, bool, str | None]:
    """The pre-fix SERP key. Measurement only — see :func:`legacy_lookup_key`."""
    return ((query or "").strip().lower(), '"' in (query or ""), _country_part(country))


class SerpCache:
    """Process-level in-memory SERP cache, shared across batches.

    Survives for the lifetime of the orchestrator (i.e. the process), so
    the same query issued in a later batch reuses the earlier result
    rather than calling the search API again. Not persisted to disk.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, bool, str | None], Any] = {}

    def get(self, query: str, country: str | None = None) -> Any | None:
        return self._store.get(serp_key(query, country))

    def set(self, query: str, result: Any, country: str | None = None) -> None:
        self._store[serp_key(query, country)] = result

    @property
    def size(self) -> int:
        return len(self._store)


class PageCache:
    """Process-level page-read cache keyed on domain, with a disk fixture store.

    Two layers, in this order:

    1. **In memory**, for the life of the orchestrator — the same role
       :class:`SerpCache` plays, so two records naming the same organisation
       cost one fetch.
    2. **On disk**, one JSON file per domain under *fixture_dir* — the layer
       :class:`SerpCache` does not have. It is what makes a re-run of a thesis
       batch reproduce its corroboration decisions: the second run reads the
       page the first run actually saw, not whatever the site serves now.
       Recording is a pure side effect of a live fetch; nothing is fetched to
       populate it.

    ``replay_only`` refuses to fetch anything that is not already recorded —
    the mode a CI run or an offline re-analysis wants, where a missing fixture
    should surface as ``fetch_unavailable`` rather than as a silent new
    network call. The caller enforces it; this class only reports a miss.

    Payloads are plain JSON dicts (``{"status", "url", "title", "h1", "text",
    …}``); this class does not interpret them.
    """

    def __init__(
        self,
        fixture_dir: "str | Path | None" = None,
        *,
        replay_only: bool = False,
    ) -> None:
        self._store: dict[str, Any] = {}
        self._dir = Path(fixture_dir) if fixture_dir else None
        self.replay_only = replay_only
        self.disk_hits = 0
        self.memory_hits = 0
        self.recorded = 0

    @staticmethod
    def _key(domain: str | None) -> str:
        return (domain or "").strip().lower().rstrip(".")

    def _path(self, domain: str) -> "Path":
        # The domain itself, sanitised — a fixture directory a human can read
        # is worth more here than an opaque digest, and the file records the
        # domain it was keyed on so a sanitisation collision cannot be missed.
        safe = re.sub(r"[^a-z0-9._-]", "_", self._key(domain))[:100]
        return self._dir / f"page_{safe}.json"

    def get(self, domain: str | None) -> Any | None:
        key = self._key(domain)
        if not key:
            return None
        if key in self._store:
            self.memory_hits += 1
            return self._store[key]
        if self._dir is None:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt fixture is a miss
            return None
        if raw.get("domain") != key:
            return None
        payload = raw.get("payload")
        self._store[key] = payload
        self.disk_hits += 1
        return payload

    def set(self, domain: str | None, payload: Any) -> None:
        key = self._key(domain)
        if not key:
            return
        self._store[key] = payload
        if self._dir is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(
                json.dumps({"domain": key, "payload": payload}, indent=1),
                encoding="utf-8",
            )
            self.recorded += 1
        except Exception:  # noqa: BLE001 — a fixture we cannot write is not fatal
            pass

    @property
    def stats(self) -> dict[str, int]:
        return {
            "page_entries": len(self._store),
            "page_memory_hits": self.memory_hits,
            "page_disk_hits": self.disk_hits,
            "page_recorded": self.recorded,
        }


class BatchCache:
    """Dict-based cache scoped to a single enrichment batch.

    When constructed with a ``shared_serp`` store, SERP lookups fall
    through to it on a per-batch miss and writes propagate to it, so
    results are reused across batches within the same process.
    """

    def __init__(self, shared_serp: SerpCache | None = None) -> None:
        self._serp: dict[tuple[str, bool, str | None], Any] = {}
        self._shared_serp = shared_serp
        # Legacy (lowercase-only) SERP keys that have actually been queried.
        # A normalised hit whose legacy key was never queried is a lookup the
        # old key would have missed — that count is the
        # `cache_hits_after_normalisation` telemetry, nothing more.
        self._serp_legacy_seen: set[tuple[str, bool, str | None]] = set()
        self._serp_normalised_hits = 0
        # Per-batch cache of a resolved institution host (redirect-followed /
        # subdomain-aware) so the department probe costs one resolution per
        # institution, not one per stage.
        self._resolved_host: dict[str, str] = {}

    # -- Resolved institution host (department probe base) --------------------

    def get_resolved_host(self, key: str) -> str | None:
        return self._resolved_host.get((key or "").strip().lower())

    def set_resolved_host(self, key: str, value: str) -> None:
        self._resolved_host[(key or "").strip().lower()] = value

    # -- SERP ----------------------------------------------------------------
    #
    # There is no ROR namespace here. `get_ror`/`set_ror` existed but had no
    # callers in the whole codebase — ROR lookups have always consulted the
    # module-level `_ror_cache` in enrichment/tier1_ror.py, which is the cache
    # that Step 1's normalisation had to reach. The dead pair is gone rather
    # than left to imply a layer that never ran.

    def get_serp(self, query: str, country: str | None = None) -> Any | None:
        """Retrieve a cached SERP result by normalised query + country.

        Checks the per-batch cache first, then the shared process-level
        cache (promoting a shared hit into the batch cache).
        """
        key = serp_key(query, country)
        legacy = legacy_serp_key(query, country)
        hit = None
        if key in self._serp:
            hit = self._serp[key]
        elif self._shared_serp is not None:
            data = self._shared_serp.get(query, country)
            if data is not None:
                self._serp[key] = data
                hit = data
        if hit is not None and legacy not in self._serp_legacy_seen:
            # Served under the normalised key; the lowercased key would have
            # missed and paid for another SERP call.
            self._serp_normalised_hits += 1
        return hit

    def set_serp(self, query: str, result: Any, country: str | None = None) -> None:
        """Cache a SERP result in the batch cache and the shared cache."""
        self._serp[serp_key(query, country)] = result
        self._serp_legacy_seen.add(legacy_serp_key(query, country))
        if self._shared_serp is not None:
            self._shared_serp.set(query, result, country)

    # -- Diagnostics ---------------------------------------------------------

    @property
    def normalised_hits(self) -> int:
        """SERP lookups the normalised key served that the old lowercased key
        would have missed."""
        return self._serp_normalised_hits

    @property
    def stats(self) -> dict[str, int]:
        return {
            "serp_entries": len(self._serp),
            "serp_normalised_hits": self._serp_normalised_hits,
        }
