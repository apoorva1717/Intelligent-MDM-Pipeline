"""STEP A — conservative normalization and signature collapsing (no LLM).

A *signature* is a distinct ``(norm_name1, norm_name2)`` key within a block.
100 byte-identical rows collapse to one signature; the LLM only ever works on
distinct signatures, never on raw rows. This is the blow-up guard.

The normalized key is internal only — it never reaches the LLM, which always
sees the original (un-normalized) name1/name2.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dedup.models import DedupRow

# Strip anything that is not a letter, digit, or whitespace. We deliberately
# do NOT strip legal forms (GmbH, AG, Inc.) or expand abbreviations here —
# that is the LLM's job. The key is a conservative collapse only.
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_key(value: Optional[str]) -> str:
    """Conservative normalized key: lowercase, trim, collapse internal
    whitespace, strip punctuation. Unicode-aware (accents folded to base
    letters so ``Universität`` and ``Universitat`` collapse together)."""
    if not value:
        return ""
    # Fold accents (NFKD) so visually-equivalent spellings collapse.
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    # Replace punctuation with a space so "u.s.a" -> "u s a", not "usa".
    text = _NON_WORD.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def derive_block_id(row: DedupRow) -> str:
    """Derive a stable block id from the normalized address tuple.

    Used when a row arrives without a ``block_id``. The hash keeps the
    derived id compact and safe to embed in a ``cluster_id``.
    """
    joined = "|".join(
        normalize_key(part)
        for part in (row.country, row.postal_code, row.street, row.house_no)
    )
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]
    return f"blk-{digest}"


@dataclass
class Signature:
    """A distinct ``(norm_name1, norm_name2)`` within a block."""

    signature_id: str
    norm_name1: str
    norm_name2: str
    # Original (un-normalized) names from the first row that produced this
    # signature — this is what the LLM sees.
    name1: str
    name2: str
    ror_id: Optional[str]
    row_ids: List[str] = field(default_factory=list)
    # Set when the LLM leaves this signature's merge unresolved.
    uncertain: bool = False

    @property
    def has_name2(self) -> bool:
        """Whether Name 2 is populated (after conservative normalization).

        Drives the deterministic asymmetry rule: an empty-Name 2 signature
        can never share an entity with a populated-Name 2 signature.
        """
        return bool(self.norm_name2)


def resolve_block_id(row: DedupRow) -> str:
    """The row's block id, or a derived one when absent/blank."""
    if row.block_id and row.block_id.strip():
        return row.block_id.strip()
    return derive_block_id(row)


def group_rows_by_block(rows: List[DedupRow]) -> "OrderedDict[str, List[DedupRow]]":
    """Group rows by (resolved) block id, preserving first-seen order."""
    blocks: "OrderedDict[str, List[DedupRow]]" = OrderedDict()
    for row in rows:
        block_id = resolve_block_id(row)
        blocks.setdefault(block_id, []).append(row)
    return blocks


def build_signatures(rows: List[DedupRow]) -> List[Signature]:
    """Collapse a block's rows into distinct signatures (STEP A).

    Signatures are returned in first-appearance order; their ids are
    ``s1``, ``s2`` … local to the block. Each signature accumulates the
    row_ids that share its key and adopts the first non-empty ror_id seen.
    """
    by_key: "OrderedDict[tuple[str, str], Signature]" = OrderedDict()
    for row in rows:
        n1 = normalize_key(row.name1)
        n2 = normalize_key(row.name2)
        key = (n1, n2)
        sig = by_key.get(key)
        if sig is None:
            sig = Signature(
                signature_id="",  # assigned below, once order is known
                norm_name1=n1,
                norm_name2=n2,
                name1=(row.name1 or "").strip(),
                name2=(row.name2 or "").strip(),
                ror_id=(row.ror_id or None),
                row_ids=[],
            )
            by_key[key] = sig
        sig.row_ids.append(row.row_id)
        # Adopt the first non-empty ror_id any row in the signature carries.
        if not sig.ror_id and row.ror_id:
            sig.ror_id = row.ror_id

    signatures = list(by_key.values())
    for index, sig in enumerate(signatures, start=1):
        sig.signature_id = f"s{index}"
    return signatures
