"""UC 0 — Detect a name field overflowing into the one below it.

One LLM call per adjacent name pair, zero SerpAPI. Runs as the very first
step of the pipeline. If the LLM judges an upper Name + the Name below it
to read as one continuous organisation name, the run is REPAIRED rather
than merely reported: :func:`enrichment.name_repack.merge_split_runs` joins
the fragments back into the one name they were cut from, enrichment runs on
that name like any other record's, and the settled result is written back
across the block in column-width pieces.

This module only decides *whether* a pair is one value. It does not merge,
does not rewrite, and raises no flag of its own — a repaired split is not a
defect the reviewer has to act on, so a merged record carries only the flags
its enrichment earns.

The check is not specific to Name 1 / Name 2. The SAP field split can drop
a continuation into any slot boundary — a long institution name spilling
Name 2 → Name 3, a department spilling Name 3 → Name 4 — so every adjacent
pair in ``NAME_SLOTS`` is checked, upper slot first.

A pair only triggers when BOTH its fields are non-blank — there is nothing
to check otherwise.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from llm.openai_client import OpenAIClient
from llm.prompts import (
    OVERFLOW_CHECK_SYSTEM_PROMPT,
    OVERFLOW_CHECK_USER_PROMPT_TEMPLATE,
)
from utils.name_slots import ADJACENT_NAME_PAIRS, slot_label

logger = logging.getLogger(__name__)

_CONFIDENCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class OverflowCheckResult:
    is_overflow: bool = False
    confidence: str = "none"
    reasoning: str = ""
    # The slot pair the verdict concerns, e.g. ("name2", "name3"). Empty
    # when nothing overflowed.
    fields: tuple[str, ...] = ()


@dataclass
class OverflowBlockResult:
    """Every overflowing pair found across the name block."""

    overflows: list[OverflowCheckResult] = field(default_factory=list)

    @property
    def is_overflow(self) -> bool:
        return bool(self.overflows)

    @property
    def confidence(self) -> str:
        """The strongest confidence across the overflowing pairs."""
        if not self.overflows:
            return "none"
        return max(
            (o.confidence for o in self.overflows),
            key=lambda c: _CONFIDENCE_RANK.get(c, 0),
        )

    @property
    def reasoning(self) -> str:
        return " | ".join(o.reasoning for o in self.overflows if o.reasoning)

    @property
    def pairs(self) -> list[tuple[str, ...]]:
        """The ``(upper, lower)`` slot pair of each overflowing pair.

        What :func:`enrichment.name_repack.merge_split_runs` consumes: the
        flat ``fields`` list below loses which slot continues into which,
        and two separate spills are not one four-slot name.
        """
        return [o.fields for o in self.overflows if len(o.fields) == 2]

    @property
    def fields(self) -> list[str]:
        """Every slot involved, in block order, without duplicates."""
        seen: list[str] = []
        for overflow in self.overflows:
            for slot in overflow.fields:
                if slot not in seen:
                    seen.append(slot)
        return seen


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


async def run_overflow_check(
    record_id: str,
    name1: str,
    name2: str,
    llm_client: OpenAIClient,
    upper_slot: str = "name1",
    lower_slot: str = "name2",
) -> OverflowCheckResult:
    """Check one adjacent pair. *name1* is the upper slot, *name2* the lower."""
    result = OverflowCheckResult()

    if not (name1 and name1.strip()) or not (name2 and name2.strip()):
        return result

    user_prompt = OVERFLOW_CHECK_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        name2=name2,
        upper_label=slot_label(upper_slot),
        lower_label=slot_label(lower_slot),
    )

    try:
        extraction = await llm_client.extract_json(
            OVERFLOW_CHECK_SYSTEM_PROMPT, user_prompt,
        )
    except Exception as exc:
        logger.info("[%s] Overflow check: LLM failed: %s", record_id, exc)
        return result

    is_overflow = bool(extraction.get("is_overflow"))
    confidence = (extraction.get("confidence") or "low").lower()
    reasoning = str(extraction.get("reasoning") or "")

    # Only flag on medium/high confidence — low confidence is too
    # noisy given the spec explicitly accepts false negatives.
    if is_overflow and confidence in ("high", "medium"):
        result.is_overflow = True
        result.confidence = confidence
        result.reasoning = reasoning
        result.fields = (upper_slot, lower_slot)
        logger.info(
            "[%s] Overflow detected: %s=%r, %s=%r, conf=%s",
            record_id, upper_slot, name1, lower_slot, name2, confidence,
        )
    else:
        logger.info(
            "[%s] Overflow check %s→%s: not flagged (is_overflow=%s, conf=%s)",
            record_id, upper_slot, lower_slot, is_overflow, confidence,
        )
    return result


async def run_overflow_check_block(
    record_id: str,
    names: dict[str, str | None],
    llm_client: OpenAIClient,
) -> OverflowBlockResult:
    """Check every adjacent name pair in the block.

    *names* maps slot name → value. A pair whose two values are equal once
    case- and whitespace-normalised is skipped: two equal strings are
    duplicates, not an overflow split (UC 12 dedup in preprocess handles
    them).
    """
    block = OverflowBlockResult()

    for upper, lower in ADJACENT_NAME_PAIRS:
        upper_val = names.get(upper)
        lower_val = names.get(lower)
        if not (upper_val and upper_val.strip()):
            continue
        if not (lower_val and lower_val.strip()):
            continue
        if _norm(upper_val) == _norm(lower_val):
            continue

        outcome = await run_overflow_check(
            record_id=record_id,
            name1=upper_val,
            name2=lower_val,
            llm_client=llm_client,
            upper_slot=upper,
            lower_slot=lower,
        )
        if outcome.is_overflow:
            block.overflows.append(outcome)

    return block
