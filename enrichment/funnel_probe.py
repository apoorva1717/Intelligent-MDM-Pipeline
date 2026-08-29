"""Counting-only rejection-funnel probe (ticket 11).

**OFF by default.** Enabled only when ``FUNNEL_PROBE`` is truthy in the
environment; every event is then appended as one JSON line to the path in
``FUNNEL_PROBE_OUT`` (default ``logs/funnel_probe.jsonl``).

What it is for: `tier1_ror` / `tier1_lei` already record *guard* rejections
(``guard_rejections`` -> ``ProvenanceLog.reject``), but the funnel's first two
steps — "ROR returned no ``chosen`` candidate" and "``chosen`` scored below
``ROR_CONFIDENCE_THRESHOLD``" — are only ``logger.info`` lines, and the local
rescore rejection records the *local* score without ROR's own score beside it.
This probe records exactly those observations, and nothing else.

Contract, deliberately narrow:

* It never reads or writes a record, a flag, a provenance entry or a scoped
  field. It only appends to a file.
* It never influences a decision. Deleting every ``event(...)`` call site
  leaves the pipeline's behaviour bit-identical.
* It is inert unless ``FUNNEL_PROBE`` is set, and the enabled check is a
  module-level constant read once at import, so the disabled path is a single
  boolean test.
"""

from __future__ import annotations

import json
import os
import threading
from itertools import count
from typing import Any

_TRUTHY = {"1", "true", "yes", "on"}

ENABLED: bool = (os.getenv("FUNNEL_PROBE", "") or "").strip().lower() in _TRUTHY

_OUT_PATH = os.getenv("FUNNEL_PROBE_OUT", "logs/funnel_probe.jsonl")

_lock = threading.Lock()
_fh: Any = None
_call_ids = count(1)


def next_call_id() -> int:
    """A correlation id for one registry lookup. Cheap when disabled."""
    return next(_call_ids) if ENABLED else 0


def event(**fields: Any) -> None:
    """Append one JSON line. No-op unless ``FUNNEL_PROBE`` is set."""
    if not ENABLED:
        return
    global _fh
    with _lock:
        if _fh is None:
            directory = os.path.dirname(_OUT_PATH)
            if directory:
                os.makedirs(directory, exist_ok=True)
            _fh = open(_OUT_PATH, "a", encoding="utf-8")
        _fh.write(json.dumps(fields, default=str) + "\n")
        _fh.flush()
