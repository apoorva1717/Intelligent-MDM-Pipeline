"""The Name-2 classifier (Step AF rule 10b), on the helper model.

Only for a record whose Name 1 is at SAP's 35-character limit and whose rule
layer (:mod:`vat.name_prep`) lands on OTHER or CONTINUATION_TRUNCATED, with
something in Name 2–4. The helper model (``VAT_HELPER_MODEL``, GPT-5.4 mini,
reasoning "none", at most 150 output tokens) says CONTINUATION / DEPARTMENT /
SUB_ENTITY / OTHER and, for a CONTINUATION, the full legal name.

It only changes what is searched for (:func:`apply`): CONTINUATION with
confidence ≥ 0.8 and a legal name made of Name 1–4's words only → search
name = legal_name, acceptable names = legal_name and Name 1; a CONTINUATION
below 0.8 → OTHER (Name 1 alone); DEPARTMENT / SUB_ENTITY / OTHER with
confidence ≥ 0.8 on a record the rules joined (CONTINUATION_TRUNCATED) →
Name 1 alone (the join undone; Step AF evaluation: the model was right on
those); any other answer → the rule layer's plan. It never touches
matching or decisions.
Answers are cached by the prompt, which is the record's names, city and
segment (``vat_llm``, cache key ``vat_name2``).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from vat.entity_match import fold
from vat.llm_cache import SearchLimits
from vat.name_prep import (
    CONTINUATION, CONTINUATION_TRUNCATED, DEPARTMENT, OTHER, NamePlan, clean, is_generic, is_public_body,
    legal_form_of,
)
from vat.prompts import NAME2_SCHEMA, NAME2_SYSTEM_PROMPT, NAME2_USER_PROMPT_TEMPLATE

#: The rule-layer classes the classifier may refine.
ELIGIBLE_CLASSES = (OTHER, CONTINUATION_TRUNCATED)
MIN_NAME_1 = 34
MIN_CONFIDENCE = 0.8
MAX_OUTPUT_TOKENS = 150
#: The lowest effort GPT-5.4 mini takes ("minimal" is refused).
EFFORT = "none"
LABEL = "name2"


def eligible(plan: NamePlan, names) -> bool:
    cleaned, _ = clean(names)
    return len((cleaned[0] or "").strip()) >= MIN_NAME_1 and plan.cls in ELIGIBLE_CLASSES \
        and any(cleaned[1:])


def segment_of(plan: NamePlan) -> str:
    text = " ".join(n for n in plan.names if n).lower()
    if any(w in text for w in ("universit", "hochschule", "klinik", "charit")):
        return "university / hospital"
    if plan.public:
        return "public body"
    if any(w in text for w in ("institut", "forschung", "fraunhofer", "max-planck", "helmholtz", "leibniz")):
        return "research institute"
    return "company"


def record_text(names, city: str | None, segment: str) -> str:
    cleaned, _ = clean(names)
    return " | ".join([*(n for n in cleaned if n), *([city] if city else []), f"segment: {segment}"])


def limits(model: str, retry_backoff: float = 10.0) -> SearchLimits:
    return SearchLimits(max_searches=0, max_opens=0, max_output_tokens=MAX_OUTPUT_TOKENS, schema=NAME2_SCHEMA,
                        cache_key="vat_name2", retry_backoff=retry_backoff, model=model)


def prompt(names, city: str | None, plan: NamePlan) -> tuple[str, str]:
    """``(system, user)`` for one record."""
    return NAME2_SYSTEM_PROMPT, NAME2_USER_PROMPT_TEMPLATE.format(record=record_text(names, city, segment_of(plan)))


def verdict_of(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """The model's answer, checked; None when unusable."""
    if not isinstance(answer, dict) or answer.get("class") not in ("CONTINUATION", "DEPARTMENT", "SUB_ENTITY",
                                                                   "OTHER"):
        return None
    try:
        confidence = float(answer.get("confidence"))
    except (TypeError, ValueError):
        return None
    name = " ".join(str(answer.get("legal_name") or "").split()) or None
    return {"class": answer["class"], "legal_name": name, "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(answer.get("reason") or "")[:200]}


def _from_the_record(name: str, plan: NamePlan) -> bool:
    """Every word of the model's legal name is in Name 1–4 (a word cut at
    35 characters may be mended across fields): it never adds a word."""
    record = re.sub(r"[^a-z0-9]", "", fold(" ".join(n for n in plan.names if n)))
    return all(w in record for w in re.findall(r"[a-z0-9]+", fold(name)))


def apply(plan: NamePlan, verdict: dict[str, Any] | None) -> NamePlan:
    """The plan the search uses after the classifier (see the module docstring)."""
    if not verdict:
        return plan
    name_1 = plan.acceptable[-1] if plan.cls == CONTINUATION_TRUNCATED else plan.entity
    if verdict["class"] != CONTINUATION:
        if plan.cls == CONTINUATION_TRUNCATED and verdict["confidence"] >= MIN_CONFIDENCE:
            # The model reads Name 2 as a department / site / other entity:
            # the rule layer's join is undone, Name 1 alone is searched.
            cls = DEPARTMENT if verdict["class"] == "DEPARTMENT" else OTHER
            return replace(plan, cls=cls, entity=name_1, search_name=name_1, acceptable=(name_1,),
                           form=legal_form_of(name_1, sap=True), complete=False,
                           issues=tuple(dict.fromkeys([*plan.issues, *([DEPARTMENT] if cls == DEPARTMENT else [])])))
        return plan
    if verdict["confidence"] >= MIN_CONFIDENCE and verdict["legal_name"] \
            and _from_the_record(verdict["legal_name"], plan):
        name = verdict["legal_name"]
        form = legal_form_of(name)
        return replace(plan, cls=CONTINUATION_TRUNCATED, entity=name, search_name=name,
                       acceptable=tuple(dict.fromkeys((name, name_1))), form=form, complete=True,
                       generic=is_generic(name), public=is_public_body(name))
    # Not sure it runs on: Name 1 alone.
    return replace(plan, cls=OTHER, entity=name_1, search_name=name_1, acceptable=(name_1,),
                   form=legal_form_of(name_1, sap=True), complete=False)
