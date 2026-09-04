"""The Phase 2 v2 feature flags.

Three independent switches, all default-false, each gating one change:

``DEDUP_V2_BLOCKING``
    Delivery-point blocking (dedup/address.py) in place of the raw
    ``country|postal|street|house`` hash.
``DEDUP_V2_NAME2``
    Classifying the text below Name 1 before it is treated as a department.
``DEDUP_V2_ID_CONFLICT``
    Routing an ROR/LEI conflict to review instead of exploding the entity.

They must work independently and together, and with all three off the output
must be byte-identical to v1 — which is what tests/test_dedup_v2_flags_off.py
asserts against a recorded run.

Read from the environment on every call rather than captured at import: a
process that imported this module before a test set the variable would
otherwise be stuck with the value at import time, and the flags-off suite runs
in the same process as the flags-on one.
"""

from __future__ import annotations

import os

#: Values that mean "on". Anything else — including "0", "no", "off", the empty
#: string and an unset variable — means off, so a typo fails safe to v1.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

BLOCKING = "DEDUP_V2_BLOCKING"
NAME2 = "DEDUP_V2_NAME2"
ID_CONFLICT = "DEDUP_V2_ID_CONFLICT"


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def v2_blocking() -> bool:
    """Delivery-point blocking (change B)."""
    return _enabled(BLOCKING)


def v2_name2() -> bool:
    """Name-2 slot classification (change C)."""
    return _enabled(NAME2)


def v2_id_conflict() -> bool:
    """ROR/LEI conflict routing (change D)."""
    return _enabled(ID_CONFLICT)
