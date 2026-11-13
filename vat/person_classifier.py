"""The person classifier (2026-11-11), on the helper model.

Is the customer a private person, a person trading under their own name, or
an organisation? The first-name list in :mod:`vat.name_prep` misses names
from outside Germany ("Li Wei", "Mehmet Yıldız"), so the helper model
(``VAT_HELPER_MODEL``, GPT-5.4 mini, reasoning "none", at most 150 output
tokens) decides.

Not asked (:func:`skip`): a record with a legal form in Name 1–4 (GmbH, AG,
e.K., …: an organisation, or a sole trader with a registered firm) and a
university ("Universi…", Hochschule, TU / BTU / FH: a researcher
reached there is billed through it).

Use (:func:`unnamed_person`): a candidate for a private person is kept only when the
page's company name contains every word of the person's name ("Nord-Apotheke
Inhaber: Dennis Saul" for Dennis Saul); "Hohmann GmbH" for Jens Hohmann is
another company. Answers are cached by the prompt (``vat_llm``, cache key
``vat_person``).
"""

from __future__ import annotations

import re
from typing import Any

from vat.entity_match import fold
from vat.llm_cache import SearchLimits
from vat.name_prep import legal_form_of
from vat.prompts import PERSON_SCHEMA, PERSON_SYSTEM_PROMPT, PERSON_USER_PROMPT_TEMPLATE

PERSON = "PERSON"
PERSON_BUSINESS = "PERSON_BUSINESS"
ORGANISATION = "ORGANISATION"
MIN_CONFIDENCE = 0.8
MAX_OUTPUT_TOKENS = 150
EFFORT = "none"
#: "name2_…": costed as a helper classifier call (vat.costs.call_kind).
LABEL = "name2_person"

LEGAL_FORM = "LEGAL_FORM"
UNIVERSITY = "UNIVERSITY"
#: Not "TH" ("Th. Bauer") nor a degree's "(FH)" ("Dipl.-Inform. (FH) Daniel Miericke").
_UNIVERSITY_RE = re.compile(r"universi|hochschule|(?<!\()\b(?:b?tu|fh)\b(?!\))", re.IGNORECASE)
#: Salutations and titles: never part of the name a page must show.
_NOT_NAME = frozenset("herr herrn frau dr med prof dipl ing rer nat phil jur dent vet".split())


def _names(names) -> list[str]:
    return [" ".join(str(n).split()) for n in names if n and str(n).strip()]


def skip(names) -> str | None:
    """Why the record is not asked (LEGAL_FORM / UNIVERSITY), None to ask."""
    text = " ".join(_names(names))
    if legal_form_of(text):
        return LEGAL_FORM
    if _UNIVERSITY_RE.search(text):
        return UNIVERSITY
    return None


def record_text(names, city: str | None) -> str:
    return " | ".join([*_names(names), *([city] if city else [])])


def limits(model: str, retry_backoff: float = 10.0) -> SearchLimits:
    return SearchLimits(max_searches=0, max_opens=0, max_output_tokens=MAX_OUTPUT_TOKENS, schema=PERSON_SCHEMA,
                        cache_key="vat_person", retry_backoff=retry_backoff, model=model)


def prompt(names, city: str | None) -> tuple[str, str]:
    """``(system, user)`` for one record."""
    return PERSON_SYSTEM_PROMPT, PERSON_USER_PROMPT_TEMPLATE.format(record=record_text(names, city))


def verdict_of(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    """The model's answer, checked; None when unusable."""
    if not isinstance(answer, dict) or answer.get("class") not in (PERSON, PERSON_BUSINESS, ORGANISATION):
        return None
    try:
        confidence = float(answer.get("confidence"))
    except (TypeError, ValueError):
        return None
    name = " ".join(str(answer.get("person_name") or "").split()) or None
    if answer["class"] != ORGANISATION and not name:
        return None
    return {"class": answer["class"], "person_name": name, "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(answer.get("reason") or "")[:200]}


def is_person(verdict: dict[str, Any] | None) -> bool:
    return bool(verdict) and verdict["class"] in (PERSON, PERSON_BUSINESS) \
        and verdict["confidence"] >= MIN_CONFIDENCE


def _words(text: str | None) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", fold(text or "")) if w not in _NOT_NAME and len(w) > 1]


def names_person(found_name: str | None, person_name: str | None) -> bool:
    """Every word of the person's name is in the page's company name."""
    person = _words(person_name)
    found = set(_words(found_name))
    return bool(person) and all(w in found for w in person)


def unnamed_person(verdict: dict[str, Any] | None, found_name: str | None) -> bool:
    """A candidate to withdraw: the customer is a private person (PERSON)
    and the page's company name does not name that person. A
    PERSON_BUSINESS is never withdrawn: its page shows the business, often
    without the full name ("Laserprint Nagel GmbH & Co. KG" for "Laserprint -
    Dietmar Nagel"; first full run, 2026-11-11)."""
    return is_person(verdict) and verdict["class"] == PERSON \
        and not names_person(found_name, verdict["person_name"])


def customer_type(skipped: str | None, verdict: dict[str, Any] | None) -> str:
    """The reviewer's Customer_Type: PERSON / PERSON_BUSINESS / ORGANISATION
    (asked, confident), ORGANISATION_LEGAL_FORM / ORGANISATION_UNIVERSITY (not
    asked), UNSURE (below MIN_CONFIDENCE), NOT_CLASSIFIED (the call failed)."""
    if skipped:
        return f"{ORGANISATION}_{skipped}"
    if not verdict:
        return "NOT_CLASSIFIED"
    return verdict["class"] if verdict["confidence"] >= MIN_CONFIDENCE else "UNSURE"
