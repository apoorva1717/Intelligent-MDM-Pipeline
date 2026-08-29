"""Which column does a value belong in? — the LLM lane behind the predicates.

`preprocess` decides, deterministically, that a value sitting in a STREET
column is really an organisation or a unit and moves it into the name block.
It decides that from wording alone (``_street_is_org_name``,
``_street_is_department``), and wording is not enough:

===========================================  ==========================
value in a street column                      what it actually is
===========================================  ==========================
``Scott & White Hospital Modul C``            a building
``Davie Medical Ctr``                         a building
``Comm. Bruker Scientific LLC``               a routing comment
``THE UNIVERSITY OF TEXAS M``                 a truncated fragment
===========================================  ==========================

All four read as organisations or units because they say Hospital, Medical
Center, LLC, University. All four were moved into the name block on the golden
set, and on 13189969 the building took **Name 1** and pushed the real
organisation down to Name 3.

This module asks the model the one question the regex cannot answer — what kind
of thing is this? — and the answer is used in exactly one direction: to **stop**
a move `preprocess` would otherwise make. It never chooses a destination, never
moves anything itself, and a value it declines to classify keeps the behaviour
it has today. A lane that can only subtract from what the predicates do cannot
invent a placement, which is what makes it safe to put in front of them.

Shaped like the UC 7 Pattern B2 person classifier, for the same reason: the
preprocessing stage is deterministic and synchronous by design, so the
orchestrator runs this as an async pre-pass and hands the verdicts in as a
plain dict keyed by the lower-cased value. A value that no longer matches by the
time preprocess reaches it simply has no verdict, and the old path runs.
"""

from __future__ import annotations

import logging
import re

from llm.prompts import (
    SLOT_ROUTER_SYSTEM_PROMPT,
    SLOT_ROUTER_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

#: Kinds that belong in the name block. A verdict outside this set, at
#: sufficient confidence, vetoes the move.
NAME_BLOCK_KINDS = frozenset({"organisation", "unit"})

#: Kinds the lane will act on. Anything else the model returns is treated as
#: no answer — a new label must be added here deliberately, not inherited.
KNOWN_KINDS = NAME_BLOCK_KINDS | {
    "building", "room", "street", "duplicate", "noise",
}

#: A verdict below this is discarded. `medium` is the same bar the overflow
#: check uses, and the prompt is explicit that `low` means "discard me".
_ACTIONABLE = frozenset({"high", "medium"})


def verdict_key(value: str | None) -> str:
    """The key a verdict is stored and looked up under.

    Whitespace-collapsed and lower-cased, so the value preprocess sees after
    its own tidying still finds the verdict taken on the raw string.
    """
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


async def classify_slot_values(
    llm_client,
    candidates: list[tuple[str, str]],
    *,
    organisation: str | None = None,
    city: str | None = None,
) -> dict[str, str]:
    """Classify each *(value, column_label)* candidate.

    Returns ``{verdict_key(value): kind}`` for actionable verdicts only —
    a low-confidence answer, an unknown label and a failed call are all
    omitted, so the caller cannot tell them apart and treats all three the
    same way: leave the existing behaviour alone.

    Candidates are classified in the order given, which the caller sorts, so
    the sequence of requests is a pure function of the record.
    """
    out: dict[str, str] = {}
    if not candidates:
        return out

    for value, column in candidates:
        key = verdict_key(value)
        if not key or key in out:
            continue
        prompt = SLOT_ROUTER_USER_PROMPT_TEMPLATE.format(
            organisation=(organisation or "").strip() or "(not stated)",
            city=(city or "").strip() or "(not stated)",
            column=column,
            value=value.strip(),
        )
        try:
            extraction = await llm_client.extract_json(
                SLOT_ROUTER_SYSTEM_PROMPT, prompt,
            )
        except Exception as exc:
            logger.info("Slot router: LLM failed for %r: %s", value, exc)
            continue

        kind = str(extraction.get("kind") or "").strip().lower()
        confidence = str(extraction.get("confidence") or "").strip().lower()
        if kind not in KNOWN_KINDS or confidence not in _ACTIONABLE:
            logger.info(
                "Slot router: no actionable verdict for %r (kind=%r conf=%r)",
                value, kind, confidence,
            )
            continue
        out[key] = kind
        logger.info(
            "Slot router: %r in %s -> %s (%s)", value, column, kind, confidence,
        )
    return out


def belongs_in_name_block(
    value: str | None, verdicts: dict[str, str] | None,
) -> bool:
    """False only when a verdict positively says this is not a name.

    No verdict means no opinion, and the caller keeps the behaviour it has
    without the lane — which is what makes an unavailable model, a refused
    answer and a value the lane never saw all equivalent.
    """
    if not verdicts:
        return True
    kind = verdicts.get(verdict_key(value))
    if kind is None:
        return True
    return kind in NAME_BLOCK_KINDS
