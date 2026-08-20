"""STEP A — conservative normalization and signature collapsing (no LLM).

A *signature* is a distinct ``(norm_name1, norm_department)`` key within a
block. 100 byte-identical rows collapse to one signature; the LLM only ever
works on distinct signatures, never on raw rows. This is the blow-up guard.

The department half of the key reads the WHOLE name block below Name 1, not
Name 2 alone: two rows at one address whose units differ only in Name 3 are
two departments, and collapsing them would merge records that name different
things.

The normalized key is internal only — it never reaches the LLM, which always
sees the original (un-normalized) names.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dedup.models import DedupRow
from utils.name_slots import DEPT_SLOTS

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


def department_text(row: DedupRow) -> str:
    """The row's whole department block joined into one string.

    Every populated slot below Name 1, in block order, separated by " / ".
    This is what both the signature key and the LLM see as "Name 2" — the
    unit the row names, wherever the SAP entry happened to put it.
    """
    parts = [
        str(getattr(row, slot, None) or "").strip()
        for slot in DEPT_SLOTS
    ]
    return " / ".join(p for p in parts if p)


@dataclass
class Signature:
    """A distinct ``(norm_name1, norm_name2)`` within a block.

    ``norm_name2`` / ``name2`` carry the row's whole department block (see
    :func:`department_text`), not the Name 2 column alone.
    """

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
    # GLEIF LEI hint (companies). Like ror_id, a soft same-entity signal for
    # the LLM — never a deterministic cluster key. Defaulted last to keep
    # positional construction stable.
    lei_id: Optional[str] = None
    # The adjudication reasoning/confidence for THIS signature's membership
    # (the merge that brought it into its entity). Set per-signature so the
    # output row carries the rationale for its own membership, never a blob
    # copied across the block. None => fall back to the entity-level value.
    merge_reasoning: Optional[str] = None
    merge_confidence: Optional[float] = None

    @property
    def has_name2(self) -> bool:
        """Whether the row names any department at all (after conservative
        normalization of the whole block below Name 1).

        Drives the deterministic asymmetry rule: a signature with no
        department can never share an entity with one that has any.
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
    row_ids that share its key and adopts the first non-empty ror_id / lei_id
    seen.
    """
    by_key: "OrderedDict[tuple[str, str], Signature]" = OrderedDict()
    for row in rows:
        n1 = normalize_key(row.name1)
        departments = department_text(row)
        n2 = normalize_key(departments)
        key = (n1, n2)
        sig = by_key.get(key)
        if sig is None:
            sig = Signature(
                signature_id="",  # assigned below, once order is known
                norm_name1=n1,
                norm_name2=n2,
                name1=(row.name1 or "").strip(),
                name2=departments,
                ror_id=(row.ror_id or None),
                row_ids=[],
                lei_id=(row.lei_id or None),
            )
            by_key[key] = sig
        sig.row_ids.append(row.row_id)
        # Adopt the first non-empty ror_id / lei_id any row in the signature carries.
        if not sig.ror_id and row.ror_id:
            sig.ror_id = row.ror_id
        if not sig.lei_id and row.lei_id:
            sig.lei_id = row.lei_id

    signatures = list(by_key.values())
    for index, sig in enumerate(signatures, start=1):
        sig.signature_id = f"s{index}"
    return signatures
