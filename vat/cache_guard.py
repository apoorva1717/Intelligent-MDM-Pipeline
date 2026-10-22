"""Detect cache writes that silently did not happen.

``utils.cache.DiskCache.set`` swallows a write it cannot make. On Windows that
includes every file whose full path passes 260 characters, so a deep
``EVIDENCE_CACHE_DIR`` loses recordings with no error — and a CACHE_FROZEN
re-run then quietly has nothing to replay. Seen live on 2026-10-19: under a
189-character cache directory every ``vat_serp``, ``vat_llm`` and long-URL
``vat_pages`` entry was dropped.

This is a **workaround in the VAT code** for that DiskCache behaviour
(``docs/VAT_VALIDATION.md``, follow-up #9), not a fix of it: after each write
the VAT code checks the file exists, logs a warning when it does not, and
counts it, so the miss shows up in the summary instead of disappearing.
"""

from __future__ import annotations

import logging
import os
from collections import Counter

from utils.cache import DiskCache

logger = logging.getLogger(__name__)


class WriteFailures:
    """Writes that did not reach disk, per namespace."""

    def __init__(self) -> None:
        self.by_namespace: Counter[str] = Counter()

    @property
    def count(self) -> int:
        return sum(self.by_namespace.values())

    def check(self, store: DiskCache, key: str) -> bool:
        """True when *key*'s file exists (or the store is memory-only, where
        nothing is expected on disk). Counts and warns otherwise."""
        if store._dir is None:
            return True
        path = store._path(store._key(key))
        if path.is_file():
            return True
        self.by_namespace[store.namespace] += 1
        full = os.path.abspath(str(path))  # MAX_PATH applies to the absolute path
        logger.warning(
            "VAT cache: %s entry was NOT written to disk (path %d chars; "
            "Windows MAX_PATH is 260 — shorten EVIDENCE_CACHE_DIR). "
            "A frozen re-run will not have it: %s",
            store.namespace, len(full), full,
        )
        return False

    def set_checked(self, store: DiskCache, key: str, payload: object) -> bool:
        store.set(key, payload)
        return self.check(store, key)
