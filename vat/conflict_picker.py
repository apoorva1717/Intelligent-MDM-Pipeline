"""The Conflict picker (2026-11-12), on the helper model.

A Conflict row's page lists several VAT numbers (a group: parent,
subsidiaries, sister companies). The helper model (``VAT_HELPER_MODEL``,
GPT-5.4 mini, reasoning "none") is shown every number on the page with the
text around it and the SAP record, and names the one that is the
customer's, with a quote, or null.

The model's answer moves nothing by itself (:func:`outcome`). A number is
PICKED only when it is one of the page's numbers, the quote is on the page
word for word and holds the number, and the text before the number (since
the previous number) names the customer with every distinctive word
(:func:`vat.web_pages.name_on_page`, strict).
A null with confidence ≥ :data:`MIN_CONFIDENCE` is NONE (the page is another
company's); anything else is UNSURE and the row stays in Conflict. VIES is
checked by the caller. Answers are cached by the prompt (``vat_llm``, cache
key ``vat_conflict``).
"""

from __future__ import annotations

import re
from typing import Any

from vat.entity_match import vat_candidates
from vat.llm_cache import SearchLimits
from vat.prompts import CONFLICT_SCHEMA, CONFLICT_SYSTEM_PROMPT, CONFLICT_USER_PROMPT_TEMPLATE
from vat.web_pages import _number_re, name_on_page

PICKED = "PICKED"
NONE = "NONE"
UNSURE = "UNSURE"
MIN_CONFIDENCE = 0.8
MAX_OUTPUT_TOKENS = 300
EFFORT = "none"
#: "name2_…": costed as a helper classifier call (vat.costs.call_kind).
LABEL = "name2_conflict"
#: A page with more numbers is not asked (TOO_MANY_NUMBERS).
MAX_NUMBERS = 20
#: Characters of page text shown before and after each number.
BEFORE, AFTER = 400, 100
#: How far back from a number its block may reach for the name check.
BLOCK = 600


def _squash(text: str | None) -> str:
    return " ".join((text or "").split())


def numbers_on_page(text: str) -> list[tuple[str, str]]:
    """``(number, the text around it)`` for every DE number on the page, in page order."""
    out = []
    for vat in vat_candidates(text):
        hit = _number_re(vat).search(text)
        if hit:
            out.append((vat, _squash(text[max(0, hit.start() - BEFORE):hit.end() + AFTER])))
    return out


def block_before(text: str, vat: str) -> str:
    """The page text before *vat* since the previous number (at most :data:`BLOCK` characters)."""
    hit = _number_re(vat).search(text)
    if not hit:
        return ""
    region = text[max(0, hit.start() - BLOCK):hit.start()]
    ends = [m.end() for v in vat_candidates(region) for m in _number_re(v).finditer(region)]
    return region[max(ends):] if ends else region


def quote_on_page(quote: str, text: str) -> bool:
    """Every piece of *quote* is on the page word for word, in page order;
    pieces are joined by "…" (the model leaves out text between a name and
    its number: "KIMW Forschungs-gGmbH … USt.-IdNr.: DE 291 405 409")."""
    page, at = _squash(text).casefold(), 0
    for piece in (p.strip().casefold() for p in re.split(r"…|\.\.\.", quote)):
        if not piece:
            continue
        at = page.find(piece, at)
        if at < 0:
            return False
        at += len(piece)
    return True


def record_text(names, street: str | None, postal_code: str | None, city: str | None) -> str:
    parts = [" ".join(str(n).split()) for n in names if n and str(n).strip()]
    place = " ".join(str(p) for p in (postal_code, city) if p)
    return " | ".join([*parts, *([str(street)] if street else []), *([place] if place else [])])


def limits(model: str, retry_backoff: float = 10.0) -> SearchLimits:
    return SearchLimits(max_searches=0, max_opens=0, max_output_tokens=MAX_OUTPUT_TOKENS, schema=CONFLICT_SCHEMA,
                        cache_key="vat_conflict", retry_backoff=retry_backoff, model=model)


def prompt(record: str, url: str, numbers: list[tuple[str, str]]) -> tuple[str, str]:
    """``(system, user)`` for one row."""
    listed = "\n".join(f"{i}. {vat}: …{ctx}…" for i, (vat, ctx) in enumerate(numbers, start=1))
    return CONFLICT_SYSTEM_PROMPT, CONFLICT_USER_PROMPT_TEMPLATE.format(record=record, url=url, numbers=listed)


def verdict_of(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """The model's answer, checked for shape; None when unusable."""
    if not isinstance(answer, dict):
        return None
    try:
        confidence = max(0.0, min(1.0, float(answer.get("confidence"))))
    except (TypeError, ValueError):
        return None
    number = re.sub(r"[\s./-]", "", str(answer.get("number") or "")).upper() or None
    return {"number": number, "quote": _squash(answer.get("quote")) or None, "confidence": confidence,
            "reason": str(answer.get("reason") or "")[:200]}


def outcome(verdict: dict[str, Any] | None, text: str, numbers: list[tuple[str, str]],
            names) -> tuple[str, str]:
    """``(PICKED / NONE / UNSURE, why)`` of one answer on one page; *names*:
    the acceptable names of the customer (a NamePlan's)."""
    if verdict is None:
        return UNSURE, "no usable answer"
    if verdict["number"] is None:
        if verdict["confidence"] >= MIN_CONFIDENCE:
            return NONE, "no number on the page is the customer's"
        return UNSURE, "model unsure, no number"
    if verdict["number"] not in {v for v, _ in numbers}:
        return UNSURE, "number not on the page"
    if verdict["confidence"] < MIN_CONFIDENCE:
        return UNSURE, "model confidence below 0.8"
    quote = verdict["quote"] or ""
    if not quote or not quote_on_page(quote, text):
        return UNSURE, "quote not on the page"
    if not _number_re(verdict["number"]).search(quote):
        return UNSURE, "quote does not hold the number"
    # strict: every distinctive word, as block_numbers on group pages; a
    # sister or parent shares the first word ("Musterwerk Holding AG").
    if name_on_page(block_before(text, verdict["number"]), names, strict=True) not in ("EXACT", "ABBREVIATED"):
        return UNSURE, "customer's name not next to the number"
    return PICKED, "number next to the customer's name"
