"""The stable cluster key — shared by the adjudicator (which mints it) and the
scorer (which detects a partial cluster by re-deriving it).

Kept in its own tiny, dependency-free module so ``dedup.scoring`` can import it
without pulling in the LLM stack that ``dedup.adjudicator`` carries.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

CLUSTER_ID_PREFIX = "c_"
LINK_ID_PREFIX = "l_"


def cluster_hash(row_ids: Iterable[str]) -> str:
    """``c_`` + first 12 hex of sha256 over the sorted member row_ids.

    Same membership -> same id across runs, machines, and input orderings; a
    membership change -> a new id. String end-to-end (never an int/float).
    """
    joined = ";".join(sorted(row_ids))
    return CLUSTER_ID_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def link_hash(row_ids: Iterable[str]) -> str:
    """``l_`` + first 12 hex of sha256 over the sorted member row_ids.

    Same shape as :func:`cluster_hash` and deliberately a different prefix: a
    reader glancing at a cell must be able to tell a "these are the same
    record" id from a "these are the same organisation" id without consulting
    a legend.
    """
    joined = ";".join(sorted(row_ids))
    return LINK_ID_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
