"""UC 0 — Flag possible Name 1 overflow into Name 2.

Single LLM call, zero SerpAPI. Runs as the very first step of the
pipeline. If the LLM judges Name 1 + Name 2 to read as one continuous
organisation name, the record is flagged for manual review and NO
further enrichment runs (per spec: "flag only, never auto-correct").

Only triggers when BOTH Name 1 and Name 2 are non-blank — there is
nothing to check otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import (
    OVERFLOW_CHECK_SYSTEM_PROMPT,
    OVERFLOW_CHECK_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


@dataclass
class OverflowCheckResult:
    is_overflow: bool = False
    confidence: str = "none"
    reasoning: str = ""


async def run_overflow_check(
    record_id: str,
    name1: str,
    name2: str,
    llm_client: OpenAIClient,
) -> OverflowCheckResult:
    result = OverflowCheckResult()

    if not (name1 and name1.strip()) or not (name2 and name2.strip()):
        return result

    user_prompt = OVERFLOW_CHECK_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        name2=name2,
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
        logger.info(
            "[%s] Overflow detected: name1=%r, name2=%r, conf=%s",
            record_id, name1, name2, confidence,
        )
    else:
        logger.info(
            "[%s] Overflow check: not flagged (is_overflow=%s, conf=%s)",
            record_id, is_overflow, confidence,
        )
    return result
