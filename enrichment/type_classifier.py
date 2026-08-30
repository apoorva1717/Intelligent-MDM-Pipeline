"""What kind of organisation is this? — the lane behind the weakest two rungs
of :mod:`enrichment.classifier`.

`classifier.py` decides ``record_type`` from ranked evidence: ROR's org types,
then GLEIF's entity metadata, then a corporate legal-form suffix read off the
name, then a keyword heuristic, then ``unknown``. The top two rungs are
registries and are not in question. The bottom two are the problem, and the
200-record labelled set says so precisely:

===============================  ====  ==========================================
outcome                          n     decided by
===============================  ====  ==========================================
``unknown``                      51    nothing at all — every source abstained
wrong ``research_institution``   19    the keyword heuristic
wrong ``research_institution``   30    ``ror:verified``
===============================  ====  ==========================================

The first two rows are 70 of the 101 errors, and both are cases where the
deterministic layer either said nothing or reasoned from a single word.
``Exxonmobil Research & Engineering Co`` on ``exxonmobil.com`` is a company;
the heuristic sees "Research" and says otherwise. ``VA Medical Center`` on
``va.gov`` is a public body; the heuristic sees "Medical" and says otherwise.

The third row is NOT this lane's business. Those records carry a verified ROR
match, and the label set's disagreements there are genuine judgement calls
about its own conventions — it labels ``Scripps Research Institute`` a company.
A lane that overturned a registry to chase them would be re-litigating verified
evidence, which is the one thing every guard in this pipeline exists to stop.

So the rule, and it is the same shape as :mod:`enrichment.expansion_check`:

* the lane is asked **only** when the deterministic answer came from
  ``unresolved`` or ``keyword`` — the two weakest rungs;
* it may not touch a record classified by ROR, GLEIF or a legal form;
* no verdict, a low-confidence verdict, an ``unknown`` label, an unparseable
  label and a failed call are all the same thing: the deterministic answer
  stands, exactly as it does today.

Unlike the expansion check this cannot run as a pre-pass either — the type is
decided at the very end of ``finalise``, from a name later tiers may have
corrected and a domain the ownership guard may have withdrawn. It runs from
``_finalise_and_return``, the single async convergence point, and the verdict
is handed into ``finalise`` as a value.
"""

from __future__ import annotations

import logging

from llm.prompts import (
    RECORD_TYPE_SYSTEM_PROMPT,
    RECORD_TYPE_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

#: The sources this lane is allowed to speak over. Both are named in
#: `classifier.py`: `keyword` is its rung 4 and `unresolved` its rung 5.
OVERRIDABLE_SOURCES = frozenset({"keyword", "unresolved"})

#: Labels the lane may return. `unknown` is accepted from the model and then
#: discarded — it is how the prompt lets it abstain without inventing a class.
VALID_TYPES = frozenset({"company", "government", "research_institution"})

#: A verdict below this is discarded, and the prompt says so explicitly.
_ACTIONABLE = frozenset({"high", "medium"})

#: Reported in the provenance so a reviewer can see which rule wrote the type.
SOURCE_LLM = "llm"


def may_override(source: str | None) -> bool:
    """Is *source* one of the two rungs this lane is permitted to speak over?"""
    return (source or "").strip().lower() in OVERRIDABLE_SOURCES


async def classify_record_type(
    llm_client,
    *,
    name1: str | None,
    name2: str | None = None,
    domain: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
) -> str | None:
    """Ask what kind of organisation this record names.

    Returns one of :data:`VALID_TYPES`, or ``None`` — and ``None`` means "no
    answer", which the caller must treat as "keep what the deterministic
    layer decided". Every failure mode collapses to ``None``.
    """
    name = (name1 or "").strip()
    if not name:
        return None

    prompt = RECORD_TYPE_USER_PROMPT_TEMPLATE.format(
        name1=name,
        name2=(name2 or "").strip() or "(none)",
        domain=(domain or "").strip() or "(none resolved)",
        city=(city or "").strip() or "(not stated)",
        state=(state or "").strip() or "(not stated)",
        country=(country or "").strip() or "(not stated)",
    )

    try:
        extraction = await llm_client.extract_json(
            RECORD_TYPE_SYSTEM_PROMPT, prompt,
        )
    except Exception as exc:  # noqa: BLE001 — a lane may never fail a record
        logger.info("Record type: LLM failed for %r: %s", name, exc)
        return None

    verdict = str(extraction.get("record_type") or "").strip().lower()
    confidence = str(extraction.get("confidence") or "").strip().lower()

    if verdict not in VALID_TYPES or confidence not in _ACTIONABLE:
        logger.info(
            "Record type: no usable answer for %r (verdict=%r conf=%r)",
            name, verdict, confidence,
        )
        return None

    logger.info(
        "Record type: %r -> %s (%s, domain=%r)",
        name, verdict, confidence, (domain or "") or None,
    )
    return verdict
