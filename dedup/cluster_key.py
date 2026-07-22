"""The stable cluster key — shared by the adjudicator (which mints it) and the
scorer (which detects a partial cluster by re-deriving it).

Kept in its own tiny, dependency-free module so ``dedup.scoring`` can import it
without pulling in the LLM stack that ``dedup.adjudicator`` carries.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

CLUSTER_ID_PREFIX = "c_"


def cluster_hash(row_ids: Iterable[str]) -> str:
    """``c_`` + first 12 hex of sha256 over the sorted member row_ids.

    Same membership -> same id across runs, machines, and input orderings; a
    membership change -> a new id. String end-to-end (never an int/float).
    """
    joined = ";".join(sorted(row_ids))
    return CLUSTER_ID_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
