"""Caches for ROR and SERP results.

Two scopes:

* :class:`BatchCache` — created once per enrichment batch. Holds the ROR
  cache (kept per-batch to avoid stale failures) and the per-batch SERP
  cache.
* :class:`SerpCache` — an in-memory, process-level SERP cache shared
  across every batch an orchestrator handles. It lets repeated or
  overlapping queries reuse a previous SERP result instead of re-hitting
  the search API, for as long as the process is alive. No file I/O — it
  lives entirely in memory.

All keys are the lowercased, stripped query string.
"""

from __future__ import annotations

from typing import Any


def _normalise_query(query: str) -> str:
    return query.strip().lower()


class SerpCache:
    """Process-level in-memory SERP cache, shared across batches.

    Survives for the lifetime of the orchestrator (i.e. the process), so
    the same query issued in a later batch reuses the earlier result
    rather than calling the search API again. Not persisted to disk.
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, query: str) -> Any | None:
        return self._store.get(_normalise_query(query))

    def set(self, query: str, result: Any) -> None:
        self._store[_normalise_query(query)] = result

    @property
    def size(self) -> int:
        return len(self._store)


class BatchCache:
    """Dict-based cache scoped to a single enrichment batch.

    When constructed with a ``shared_serp`` store, SERP lookups fall
    through to it on a per-batch miss and writes propagate to it, so
    results are reused across batches within the same process.
    """

    def __init__(self, shared_serp: SerpCache | None = None) -> None:
        self._ror: dict[str, Any] = {}
        self._serp: dict[str, Any] = {}
        self._shared_serp = shared_serp
        # Per-batch cache of a resolved institution host (redirect-followed /
        # subdomain-aware) so the department probe costs one resolution per
        # institution, not one per stage.
        self._resolved_host: dict[str, str] = {}

    # -- Resolved institution host (department probe base) --------------------

    def get_resolved_host(self, key: str) -> str | None:
        return self._resolved_host.get((key or "").strip().lower())

    def set_resolved_host(self, key: str, value: str) -> None:
        self._resolved_host[(key or "").strip().lower()] = value

    # -- ROR -----------------------------------------------------------------

    def get_ror(self, name1: str) -> Any | None:
        """Retrieve cached ROR result by lowercased name1."""
        return self._ror.get(name1.strip().lower())

    def set_ror(self, name1: str, result: Any) -> None:
        """Cache a ROR result keyed by lowercased name1."""
        self._ror[name1.strip().lower()] = result

    # -- SERP ----------------------------------------------------------------

    def get_serp(self, query: str) -> Any | None:
        """Retrieve a cached SERP result by lowercased query.

        Checks the per-batch cache first, then the shared process-level
        cache (promoting a shared hit into the batch cache).
        """
        key = _normalise_query(query)
        if key in self._serp:
            return self._serp[key]
        if self._shared_serp is not None:
            data = self._shared_serp.get(query)
            if data is not None:
                self._serp[key] = data
                return data
        return None

    def set_serp(self, query: str, result: Any) -> None:
        """Cache a SERP result in the batch cache and the shared cache."""
        self._serp[_normalise_query(query)] = result
        if self._shared_serp is not None:
            self._shared_serp.set(query, result)

    # -- Diagnostics ---------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        return {"ror_entries": len(self._ror), "serp_entries": len(self._serp)}
