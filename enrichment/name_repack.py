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

    The cut is dense: it takes every word that fits. It may therefore leave a
    piece ending on a connector — ``Department of Materials Science and`` —
    and that is deliberate, because the SAP column is what it is and the
    reference corpus writes it exactly that way on three independent records.

    It was not always so. While the width was wrongly set to 32 the cut kept
    landing on precisely the boundary SAP's own writer had used:
    ``Exxonmobil Research &`` went in, was merged and enriched as
    ``Exxonmobil Research & Engineering Co``, and came back out as
    ``Exxonmobil Research &`` again — byte-identical to the input, so the
    repair was invisible and every probe reading the output concluded UC 0 had
    never fired. Retreating off the connector (:data:`CUT_STOPWORDS`) was the
    first answer to that, and it was treating the symptom: the coincidence was
    the width. Cutting at the real width instead, the repack reproduces its
    input block on 5 of the golden set's 64 multi-slot records rather than 16 —
    and four of those five are not split names at all (a name beside its own
    acronym, a name beside a department, a duplicated row). The retreat then
    cost six graded cells and contradicted the corpus, so it is off.

    :func:`chunk_name` and :func:`repack_name_block` still take
    ``avoid_connector_endings`` and still honour it — the consideration is
    real, and re-enabling it is one argument. A tidier cut can cost a slot, and
    a piece with no slot is lost, so with it on ``repack_name_block`` falls
    back to the denser cut rather than push a name out of the block.

    Which slot a piece lands in is per-slot state that other rules hold too.
    Registry ownership and the review flags both follow the value across the
    move — see ``origin`` below, and ``flags.relabel_name_slots``.

Only a record whose block was merged is repacked. A record that arrived whole
keeps whatever slot layout the pipeline gave it — widening the rewrite to every
row would re-split names that ship correctly today.
"""

from __future__ import annotations

import os
import re

from utils.name_slots import NAME_SLOTS

# The width one SAP name column holds, and so the width the rewrite cuts to.
#
# Measured from the source data rather than assumed. Across every raw input
# corpus — `docs/thesis/chemspeed_us_100.xlsx`,
# `docs/SAMPLE_DATA/test-all-100-original.xlsx` and the golden set's own INPUT
# rows — no name cell exceeds 40 characters, and two independent records are
# visibly truncated *mid-word* at exactly 40:
#
#     "The Salk Institute for Biological Studie"   (40, "Studies" cut)
#     "Palo Alto Veterans Institute for Researc"   (40, "Research" cut)
#
# A column that cuts a word at 40 is a column of 40. This constant was 32 for
# most of the project's life, derived from a comment asserting the columns held
# 35 with three characters held back as margin; the data contradicts the 35, and
# a margin against a width that was never real only re-split names that fit.
# Cutting to the full 40 is what SAP itself does.
NAME_FIELD_WIDTH = int(os.getenv("NAME_FIELD_WIDTH", "40"))

# Words a name piece must not end on. A coordinating conjunction, preposition
# or article at the end of a column reads as a name cut mid-phrase — which is
# precisely the shape UC 0 exists to repair, so a repack that produces it hands
# back the defect the merge just removed. `Exxonmobil Research &` is the case
# that named this: the merge joined it, enrichment resolved the whole name, and
# the cut put the fragment back byte-for-byte.
#
# Only whole words belong here. A trailing comma or full stop is not a
# continuation — `Security, LLC` cuts cleanly after `Security,` — so
# punctuation is stripped before the test rather than being a signal itself.
CUT_STOPWORDS = frozenset({
    "&", "+", "-", "/",
    "a", "an", "and", "at", "de", "del", "der", "des", "die", "et", "for",
    "in", "of", "on", "or", "the", "to", "und", "van", "von", "y",
})


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _is_connector(word: str) -> bool:
    return word.strip(".,;:").casefold() in CUT_STOPWORDS


def _retreat_from_connector(chunk: str) -> tuple[str, str]:
    """Split *chunk* so it does not end on a connector.

    Returns ``(emitted, carried)`` — the piece to write, and the words moved
    off its end to lead the next piece. A run of connectors retreats whole
    (``Department of the`` carries ``of the``), and a piece of one word has
    nothing to retreat to and is emitted as it stands.
    """
    words = chunk.split(" ")
    carried: list[str] = []
    while len(words) > 1 and _is_connector(words[-1]):
        carried.insert(0, words.pop())
    return " ".join(words), " ".join(carried)


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


def chunk_name(
    value: str,
    width: int = NAME_FIELD_WIDTH,
    *,
    avoid_connector_endings: bool = False,
) -> list[str]:
    """Cut *value* into pieces of at most *width* characters.

    The cut is taken at *width* and then retreated to the last word boundary
    that fits, so a piece never ends mid-word. A single token longer than
    *width* has no boundary to retreat to and is cut at the column edge.

    The boundary retreats a second time when it would leave a piece ending on
    a connector (:data:`CUT_STOPWORDS`) — that word leads the next piece
    instead. The retreat is declined when carrying the connector forward would
    push the following piece past *width*, because cutting a word in half is
    worse than a connector at a column edge.

    The final piece is never retreated: nothing follows it to carry a word to,
    and a name that genuinely ends on a connector ends on one.

    *avoid_connector_endings* turns the second retreat off. It costs a slot
    often enough to matter — a shorter piece is a piece that fits less — and
    :func:`repack_name_block` falls back to the denser cut rather than let a
    tidier one push a name out of the block entirely.
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
            emitted, carried = current, ""
            if avoid_connector_endings:
                emitted, carried = _retreat_from_connector(current)
                if carried and len(carried) + 1 + len(word) > width:
                    emitted, carried = current, ""
            chunks.append(emitted)
            current = f"{carried} {word}" if carried else word
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
    *,
    avoid_connector_endings: bool = False,
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

    The cut is dense by default — see the module docstring for why the
    connector-aware cut was measured and turned off. With
    ``avoid_connector_endings`` on it is preferred but never paid for in
    content: a piece that ends on a connector reads badly, but a piece with no
    slot to go to is *lost*, so when avoiding the connector needs a slot the
    block does not have, the denser cut is taken instead.
    """
    def _cut(avoid: bool) -> list[tuple[str, int]]:
        pieces: list[tuple[str, int]] = []
        for source, value in enumerate(values):
            pieces.extend(
                (chunk, source) for chunk in chunk_name(
                    value or "", width, avoid_connector_endings=avoid,
                )
            )
        return pieces

    pieces = _cut(avoid_connector_endings)
    if avoid_connector_endings and len(pieces) > len(NAME_SLOTS):
        dense = _cut(False)
        if len(dense) < len(pieces):
            pieces = dense

    packed: list[str | None] = [None] * len(NAME_SLOTS)
    origin: dict[int, int] = {}
    for index, (chunk, source) in enumerate(pieces[: len(NAME_SLOTS)]):
        packed[index] = chunk
        origin[index] = source
    dropped = [chunk for chunk, _ in pieces[len(NAME_SLOTS) :]]
    return packed, dropped, origin
