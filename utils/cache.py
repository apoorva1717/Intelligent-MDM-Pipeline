"""Per-batch in-memory cache for ROR and SERP results.

Each enrichment batch creates one BatchCache instance.  Keyed by
lowercased query string so repeated lookups within a batch are free.
"""

from __future__ import annotations

from typing import Any


class BatchCache:
    """Simple dict-based cache scoped to a single enrichment batch."""

    def __init__(self) -> None:
        self._ror: dict[str, Any] = {}
        self._serp: dict[str, Any] = {}

    # -- ROR -----------------------------------------------------------------

    def get_ror(self, name1: str) -> Any | None:
        """Retrieve cached ROR result by lowercased name1."""
        return self._ror.get(name1.strip().lower())

    def set_ror(self, name1: str, result: Any) -> None:
        """Cache a ROR result keyed by lowercased name1."""
        self._ror[name1.strip().lower()] = result

    # -- SERP ----------------------------------------------------------------

    def get_serp(self, query: str) -> Any | None:
        """Retrieve cached SERP result by lowercased query."""
        return self._serp.get(query.strip().lower())

    def set_serp(self, query: str, result: Any) -> None:
        """Cache a SERP result keyed by lowercased query."""
        self._serp[query.strip().lower()] = result

    # -- Diagnostics ---------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        return {"ror_entries": len(self._ror), "serp_entries": len(self._serp)}
