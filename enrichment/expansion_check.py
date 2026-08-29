"""Is the expanded form a name anyone uses? — the lane in front of Fix 4.

`finalise` expands organisational abbreviations in every non-registry output
name (``Lab`` -> ``Laboratory``, ``Ctr`` -> ``Center``), unconditionally. That
is right far more often than not, which is why it is the default — but it is
wrong in one recognisable situation, and the golden set holds both halves of
the pair:

=================================  ==========  =========================
value as supplied                   reference   why
=================================  ==========  =========================
``Bio-Rad Lab Inc``                 expand      the company IS Bio-Rad
                                                Laboratories, Inc.
``Orange County Public Health Lab`` expand      a public laboratory that
                                                publishes itself so
``Baytown Refinery Lab``            keep        an ExxonMobil site
``Zoetis Ref Lab Cincinnati``       keep        a Zoetis site
=================================  ==========  =========================

Nothing in the strings separates them. Neither do the registry or domain
signals — measured, both groups contain records with and without a ROR/LEI
identifier, and the ExxonMobil sites resolve a domain perfectly well. The
difference is whether the *thing named* has a published name at all, and
expanding an internal site designation manufactures one that exists nowhere:
there is no organisation called "Baytown Refinery Laboratory".

That is a research question rather than a spelling convention, so it is asked
rather than encoded. Like :mod:`enrichment.slot_router`, the answer is used in
one direction only — to **decline** an expansion. No verdict, a low-confidence
verdict, an unknown label and a failed call are all the same thing: the value
is expanded exactly as it is today.

Unlike the slot router this cannot run as a pre-pass, because the value it
judges does not exist until the tiers have settled. `_finalise_and_return` is
the single async convergence point before `finalise`, so it runs there and the
verdicts are handed into `finalise` as a dict.
"""

from __future__ import annotations

import logging
import re

from llm.prompts import (
    EXPANSION_CHECK_SYSTEM_PROMPT,
    EXPANSION_CHECK_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

#: The only verdict that changes anything. `expand` is the default and needs
#: no verdict to happen.
_DECLINE = "keep"

#: A verdict below this is discarded, and the prompt says so explicitly.
_ACTIONABLE = frozenset({"high", "medium"})


def expansion_key(value: str | None) -> str:
    """The key a verdict is stored and looked up under."""
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


async def check_expansions(
    llm_client,
    candidates: list[tuple[str, str, str]],
    *,
    organisation: str | None = None,
    city: str | None = None,
    domain: str | None = None,
) -> set[str]:
    """Judge each *(original, expanded, column_label)* candidate.

    Returns the set of :func:`expansion_key` values whose expansion should be
    **declined**. Everything else — including every failure mode — is absent
    from the set, and absence means "expand", which is the behaviour without
    this lane at all.

    Candidates are judged in the order given, which the caller sorts, so the
    request sequence is a pure function of the record.
    """
    declined: set[str] = set()
    if not candidates:
        return declined

    seen: set[str] = set()
    for original, expanded, column in candidates:
        key = expansion_key(original)
        if not key or key in seen:
            continue
        seen.add(key)
        prompt = EXPANSION_CHECK_USER_PROMPT_TEMPLATE.format(
            organisation=(organisation or "").strip() or "(not stated)",
            city=(city or "").strip() or "(not stated)",
            domain=(domain or "").strip() or "(none resolved)",
            column=column,
            original=original.strip(),
            expanded=expanded.strip(),
        )
        try:
            extraction = await llm_client.extract_json(
                EXPANSION_CHECK_SYSTEM_PROMPT, prompt,
            )
        except Exception as exc:
            logger.info("Expansion check: LLM failed for %r: %s", original, exc)
            continue

        verdict = str(extraction.get("verdict") or "").strip().lower()
        confidence = str(extraction.get("confidence") or "").strip().lower()
        if verdict == _DECLINE and confidence in _ACTIONABLE:
            declined.add(key)
            logger.info(
                "Expansion check: keeping %r as supplied rather than %r (%s)",
                original, expanded, confidence,
            )
        else:
            logger.info(
                "Expansion check: %r -> %r stands (verdict=%r conf=%r)",
                original, expanded, verdict, confidence,
            )
    return declined


def expansion_declined(value: str | None, declined: set[str] | None) -> bool:
    """True only when a verdict positively said to keep *value* as supplied."""
    if not declined:
        return False
    return expansion_key(value) in declined
