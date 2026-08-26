"""UC 0 — repair a name split across SAP fields, then rewrite it back.

The SAP name block is a row of fixed-width columns, so a single organisation
name that is longer than one column arrives cut in two: ``Name 1 = "US Army"``,
``Name 2 = "Corps of Engineers"``. :mod:`enrichment.overflow_check` is what
notices; this module is what acts on the finding.

Two halves of one operation, run at opposite ends of the pipeline:

``merge_split_runs``
    Before enrichment. Joins every run of slots the check reported as one
    continuous value back into a single string and packs the survivors
    leftward, so the tiers see ``"US Army Corps of Engineers"`` — the name the
    record was always about — rather than two fragments neither of which any
    registry can match.

``repack_name_block``
    After enrichment. Cuts the settled names back into column-width pieces and
    lays them out across the block again. The cut is taken at
    :data:`NAME_FIELD_WIDTH` characters, moved back to the last word boundary
    that fits so a piece never ends mid-word; a single token longer than the
    column is the one case with no word boundary to retreat to, and is cut
    where the column ends.

Only a record whose block was merged is repacked. A record that arrived whole
keeps whatever slot layout the pipeline gave it — widening the rewrite to every
row would re-split names that ship correctly today.
"""

from __future__ import annotations

import os
import re

from utils.name_slots import NAME_SLOTS

# The width one SAP name column is cut to. The columns themselves hold 35
# characters (see `utils.text_utils`); the rewrite targets 32 so a value has
# room for the trailing punctuation a downstream consumer may add without
# spilling into the next column again.
NAME_FIELD_WIDTH = int(os.getenv("NAME_FIELD_WIDTH", "32"))


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def merge_split_runs(
    names: dict[str, str | None],
    pairs: list[tuple[str, ...]],
) -> tuple[dict[str, str | None], list[list[str]]]:
    """Join the slots *pairs* reports as continuations, and pack leftward.

    *pairs* is the ``(upper, lower)`` tuple of every overflowing adjacent pair
    — :attr:`OverflowBlockResult.pairs`. Consecutive pairs chain: a name that
    spilled Name 1 → Name 2 → Name 3 is reported as two pairs and merges into
    one value, not two.

    Returns the rewritten block and the runs that were merged (each run being
    the slot names it consumed, so the caller can log what it did). A run of
    one slot is not a merge and is not returned.

    Values are joined with a single space. A column boundary in this data falls
    between words — SAP's own writers wrap rather than cut — so the space is
    what the split removed and what putting it back restores.
    """
    joined = {tuple(pair) for pair in pairs if len(pair) == 2}

    runs: list[list[str]] = []
    current: list[str] = []
    for index, slot in enumerate(NAME_SLOTS):
        if not _norm(names.get(slot)):
            # A blank breaks any run: the check only ever pairs two populated
            # slots, so nothing can continue across a gap.
            if current:
                runs.append(current)
                current = []
            continue
        if current and (NAME_SLOTS[index - 1], slot) in joined:
            current.append(slot)
        else:
            if current:
                runs.append(current)
            current = [slot]
    if current:
        runs.append(current)

    merged_values = [
        " ".join(_norm(names.get(slot)) for slot in run) for run in runs
    ]
    merged: dict[str, str | None] = {
        slot: (merged_values[i] if i < len(merged_values) else None)
        for i, slot in enumerate(NAME_SLOTS)
    }
    return merged, [run for run in runs if len(run) > 1]


def chunk_name(value: str, width: int = NAME_FIELD_WIDTH) -> list[str]:
    """Cut *value* into pieces of at most *width* characters.

    The cut is taken at *width* and then retreated to the last word boundary
    that fits, so a piece never ends mid-word. A single token longer than
    *width* has no boundary to retreat to and is cut at the column edge.
    """
    text = _norm(value)
    if not text:
        return []

    chunks: list[str] = []
    current = ""
    for word in text.split(" "):
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            chunks.append(current)
            current = word
        # An over-long token: emit its full-width pieces and carry the
        # remainder, which can still take the words that follow it.
        while len(current) > width:
            chunks.append(current[:width])
            current = current[width:]
    if current:
        chunks.append(current)
    return chunks


def repack_name_block(
    values: list[str | None],
    width: int = NAME_FIELD_WIDTH,
) -> tuple[list[str | None], list[str], dict[int, int]]:
    """Lay *values* back across the name block in column-width pieces.

    *values* is the settled enriched block in slot order. Every populated value
    is chunked and the pieces fill the block from Name 1 down, in order, so the
    organisation name always keeps the slots it needs and the department slots
    take what is left.

    Returns the new block, the pieces that did not fit (the caller flags them —
    they are content the field split cannot place), and a map from destination
    slot index to the source index it came from, so the caller can carry
    per-field state (registry ownership) across the move.
    """
    pieces: list[tuple[str, int]] = []
    for source, value in enumerate(values):
        pieces.extend(
            (chunk, source) for chunk in chunk_name(value or "", width)
        )

    packed: list[str | None] = [None] * len(NAME_SLOTS)
    origin: dict[int, int] = {}
    for index, (chunk, source) in enumerate(pieces[: len(NAME_SLOTS)]):
        packed[index] = chunk
        origin[index] = source
    dropped = [chunk for chunk, _ in pieces[len(NAME_SLOTS) :]]
    return packed, dropped, origin
