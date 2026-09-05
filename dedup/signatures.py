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
    # signature — this is what the LLM sees. Under v2 these hold the CLASSIFIED
    # institution and department (dedup/name_slots.py) rather than the raw
    # Name 1 and the Name 2..5 join; ``institution`` / ``department`` below are
    # the same two values under the names the v2 prompt uses.
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
    # --- v2 (DEDUP_V2_NAME2). All defaulted, so v1 construction is unchanged.
    #: The classified institution and department. Equal to name1 / name2.
    institution: str = ""
    department: str = ""
    #: Other names for the SAME institution — a trading name, an opaque code
    #: that sat in Name 1. Shown to the model; never part of the key.
    aliases: List[str] = field(default_factory=list)
    #: Text shown to the model for context and never matched on: a person, a
    #: place, a word left beside a delivery desk, or a fragment of a name this
    #: record split across two slots. Distinct from ``aliases``, which are
    #: other names for the whole institution and ARE matched on.
    hints: List[str] = field(default_factory=list)
    #: Phase 1 columns the file route used to drop (C.4).
    operating_name: Optional[str] = None
    suggested_name: Optional[str] = None
    record_type: Optional[str] = None
    ror_provenance: Optional[str] = None
    lei_provenance: Optional[str] = None
    #: How this signature's slot text was read. Reported, never keyed on.
    slot_kind: str = "none"
    #: The model's verdict on whether this signature's INSTITUTION is the same
    #: organisation as the entity it was compared with — "same", "different",
    #: "uncertain", or None when it was never asked. Separate from the entity
    #: decision on purpose: two records can be one institution and two
    #: entities, and the Link ID needs the first answer while the Cluster ID
    #: needs the second.
    institution_relation: Optional[str] = None
    #: The first member row's parsed delivery point, for the street label.
    address: Optional[object] = None

    @property
    def has_name2(self) -> bool:
        """Whether the row names any department at all (after conservative
        normalization of the whole block below Name 1).

        Drives the deterministic asymmetry rule: a signature with no
        department can never share an entity with one that has any.

        Under v2 this is a statement about the DEPARTMENT the slot classifier
        found, not about whether a cell below Name 1 was populated: a record
        whose Name 2 reads "Central Receiving" names no department, and putting
        it on the far side of this rule from its own institution is how the
        pair stopped being compared.
        """
        return bool(self.norm_name2)


def resolve_block_id(row: DedupRow) -> str:
    """The row's block id, or a derived one when absent/blank."""
    if row.block_id and row.block_id.strip():
        return row.block_id.strip()
    return derive_block_id(row)


@dataclass
class Block:
    """One address block: the rows in it, and whether its address is verified.

    ``unverified`` marks a block built from rows that name no usable house
    number (dedup/address.py). Those rows still cluster with each other, but a
    cluster they form is routed to manual review rather than asserted — the
    delivery point behind it was never established.
    """

    block_id: str
    rows: List[DedupRow]
    unverified: bool = False


def _v1_blocks(rows: List[DedupRow]) -> "OrderedDict[str, Block]":
    """v1 blocking: one key per row, first-seen order, nothing unverified."""
    blocks: "OrderedDict[str, Block]" = OrderedDict()
    for row in rows:
        block_id = resolve_block_id(row)
        block = blocks.get(block_id)
        if block is None:
            block = Block(block_id=block_id, rows=[])
            blocks[block_id] = block
        block.rows.append(row)
    return blocks


def _v2_blocks(rows: List[DedupRow]) -> "OrderedDict[str, Block]":
    """Delivery-point blocking: union the blocks a row's keys connect.

    A row can carry more than one key (zip+house and city+house), so the keys
    form a graph and a block is one connected component of it. Union-find is
    iterative and driven off sorted keys, so the components — and the block ids
    derived from them — do not depend on the order the rows arrived in.

    A caller-supplied ``block_id`` still wins, exactly as in v1: an upstream
    address gate that has already decided the blocks is better evidence than
    anything re-derived from the columns here.
    """
    from dedup.address import block_keys, parse_address

    parent: dict[str, str] = {}

    def find(key: str) -> str:
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:  # path compression, iteratively
            parent[key], key = root, parent[key]
        return root

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            lo, hi = (a, b) if a < b else (b, a)
            parent[hi] = lo

    keys_per_row: List[List[str]] = []
    for row in rows:
        if row.block_id and row.block_id.strip():
            keys = [f"g:{row.block_id.strip()}"]
        else:
            keys = block_keys(parse_address(row))
        keys_per_row.append(keys)
        for key in keys:
            parent.setdefault(key, key)

    for keys in keys_per_row:
        for key in keys[1:]:
            union(keys[0], key)

    members: dict[str, List[DedupRow]] = {}
    verified: dict[str, bool] = {}
    for row, keys in zip(rows, keys_per_row):
        root = find(keys[0])
        members.setdefault(root, []).append(row)
        # A key space cannot mix: house-less rows emit only "f:" keys and
        # house-bearing rows never do, so this is a property of the component,
        # not a vote among its members.
        verified[root] = verified.get(root, True) and not keys[0].startswith("f:")

    blocks: "OrderedDict[str, Block]" = OrderedDict()
    for root in sorted(members):
        digest = hashlib.sha1(root.encode("utf-8")).hexdigest()[:12]
        blocks[f"blk-{digest}"] = Block(
            block_id=f"blk-{digest}",
            # Sorted by row_id so signature ids, bucket order and every LLM
            # call sequence below are independent of the input's row order.
            rows=sorted(members[root], key=lambda r: r.row_id),
            unverified=not verified[root],
        )
    return blocks


def build_blocks(rows: List[DedupRow]) -> "OrderedDict[str, Block]":
    """Group rows into address blocks, v1 or v2 depending on the flag."""
    from dedup.flags import v2_blocking

    return _v2_blocks(rows) if v2_blocking() else _v1_blocks(rows)


def group_rows_by_block(rows: List[DedupRow]) -> "OrderedDict[str, List[DedupRow]]":
    """Group rows by block id. Kept as the plain-mapping view of
    :func:`build_blocks` for callers that do not care about verification."""
    return OrderedDict(
        (block_id, block.rows) for block_id, block in build_blocks(rows).items()
    )


def _resolve_slots(row: DedupRow, block_name1s: List[str]):
    """The (institution, department, aliases, kind, hints) this row states.

    v1 is the identity: Name 1 is the institution and everything below it is
    the department. v2 asks dedup.name_slots what the text below Name 1
    actually is first.
    """
    from dedup.flags import v2_name2

    if not v2_name2():
        from dedup.name_slots import SlotResult

        departments = department_text(row)
        return SlotResult(
            institution=(row.name1 or "").strip(),
            department=departments,
            kind="department" if departments else "none",
        )

    from dedup.name_slots import classify_slots

    return classify_slots(
        row.name1, row.name2, row.name3, row.name4, row.name5,
        block_name1s=block_name1s,
    )


def build_signatures(rows: List[DedupRow]) -> List[Signature]:
    """Collapse a block's rows into distinct signatures (STEP A).

    Signatures are returned in first-appearance order; their ids are
    ``s1``, ``s2`` … local to the block. Each signature accumulates the
    row_ids that share its key and adopts the first non-empty ror_id / lei_id
    seen.
    """
    from dedup.flags import v2_blocking, v2_name2

    # Every Name 1 in the block, which is what lets the classifier tell a
    # truncated institution from a department: "Institute, Inc" is Name 1's
    # tail only because another row here spells the whole name.
    block_name1s = [(row.name1 or "").strip() for row in rows]
    parse_address = None
    if v2_blocking():
        from dedup.address import parse_address

    by_key: "OrderedDict[tuple[str, str], Signature]" = OrderedDict()
    for row in rows:
        slots = _resolve_slots(row, block_name1s)
        n1 = normalize_key(slots.institution)
        departments = slots.department
        n2 = normalize_key(departments)
        key = (n1, n2)
        sig = by_key.get(key)
        if sig is None:
            sig = Signature(
                signature_id="",  # assigned below, once order is known
                norm_name1=n1,
                norm_name2=n2,
                name1=slots.institution,
                name2=departments,
                ror_id=(row.ror_id or None),
                row_ids=[],
                lei_id=(row.lei_id or None),
                institution=slots.institution,
                department=departments,
                aliases=list(slots.aliases),
                hints=list(slots.hints),
                operating_name=(row.operating_name or None) if v2_name2() else None,
                suggested_name=(row.suggested_name or None) if v2_name2() else None,
                record_type=(row.record_type or None) if v2_name2() else None,
                ror_provenance=(row.ror_id_provenance or None) if v2_name2() else None,
                lei_provenance=(row.lei_id_provenance or None) if v2_name2() else None,
                slot_kind=slots.kind,
                address=parse_address(row) if parse_address else None,
            )
            by_key[key] = sig
        sig.row_ids.append(row.row_id)
        # Adopt the first non-empty ror_id / lei_id any row in the signature carries.
        if not sig.ror_id and row.ror_id:
            sig.ror_id = row.ror_id
        if not sig.lei_id and row.lei_id:
            sig.lei_id = row.lei_id
        if v2_name2():
            # Aliases accumulate across the rows behind one signature: two
            # records that collapsed to the same institution may each carry a
            # different other-name for it, and both are worth showing.
            for alias in slots.aliases:
                if alias not in sig.aliases:
                    sig.aliases.append(alias)
            for hint in slots.hints:
                if hint not in sig.hints:
                    sig.hints.append(hint)
            for attr, value in (
                ("operating_name", row.operating_name),
                ("suggested_name", row.suggested_name),
                ("record_type", row.record_type),
                ("ror_provenance", row.ror_id_provenance),
                ("lei_provenance", row.lei_id_provenance),
            ):
                if not getattr(sig, attr) and value:
                    setattr(sig, attr, value)

    signatures = list(by_key.values())
    for index, sig in enumerate(signatures, start=1):
        sig.signature_id = f"s{index}"
    return signatures
