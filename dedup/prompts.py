"""LLM prompt strings and builders for the Phase 2 dedup adjudicator.

Centralised so they can be versioned and reviewed. ``PROMPT_VERSION`` is
logged on every decision and echoed in every output row.
"""

from __future__ import annotations

import json
from typing import List

# Bumped whenever the prompt wording changes in a way that could shift
# decisions. Logged per LLM call and emitted in every result row.
PROMPT_VERSION = "p2-dedup-v3"

#: v2 (``DEDUP_V2_NAME2``). A different prompt is a different experiment, so it
#: gets its own version: the fields per signature changed, the core rule was
#: rewritten around a classified department, and the response may carry a
#: ``department_relation``. Any output produced under one of these is not
#: comparable with output produced under the other, and the version column is
#: how a reader tells which they are holding.
PROMPT_VERSION_V2 = "p2-dedup-v4"


# Shared by both modes. Describes the two-level identity model and the
# adjudication rules. Address matching is already done upstream.
SYSTEM_PROMPT = """\
You are an entity-resolution adjudicator for SAP customer master data at Bruker, a scientific-instruments company. Customers are research institutions, universities, hospitals, companies, and their internal departments.

Every record you receive already shares the same physical address (country, postal code, street). Address matching is done. Your only job is to decide, from the names, which records refer to the SAME real-world customer entity.

Identity has TWO levels:
- Name 1 = the institution or company (e.g. "University of Stuttgart", "Siemens AG").
- Name 2 = a department, faculty, institute, or sub-unit within it (may be empty).
An entity is a specific (institution, department) pair.

Rules:
- Same institution AND same department, or both Name 2 empty → SAME entity.
- Same institution but DIFFERENT departments → DIFFERENT entities. Never merge them. Example: "Uni Stuttgart, Dept of Chemistry" and "Uni Stuttgart, Dept of Mechanical Engineering" are two distinct entities.
- Different institutions that happen to share one address (shared campus or building) → DIFFERENT entities.
- A shared ROR ID means same INSTITUTION only. It does not mean same department and never by itself makes two records the same entity — you must still compare Name 2.
- A shared LEI (Legal Entity Identifier) means the records are the same legal entity (typically a company). Treat it like ROR: a strong same-INSTITUTION signal, but it still does not by itself merge records with DIFFERENT Name 2 departments, and you must still compare Name 2. Conversely, DIFFERENT non-empty LEIs are a strong signal of different entities.

Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms ("Dept" = "Department", "Mech Eng" = "Mechanical Engineering"), word reordering, legal-form suffixes (GmbH, AG, Inc., Ltd, e.V.), historical renames or restructures, and spelling variants/typos.

If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome.\
"""


#: v2 system prompt. Two things changed and both were costing merges.
#:
#: The identity model now speaks of an INSTITUTION and a DEPARTMENT rather than
#: of "Name 1" and "Name 2", because under v2 those cells no longer map to
#: those roles: the department is what the slot classifier found, and a record
#: whose Name 2 read "Central Receiving" arrives here with no department at
#: all. Telling the model to "compare Name 2" would be telling it to compare a
#: field it can no longer see.
#:
#: And the core rule is stated as a biconditional — when SAME, when DIFFERENT —
#: rather than as a list of same-cases with "never merge" attached to one of
#: them. v3 said "same institution AND same department → SAME", which is silent
#: on the case this batch is full of: one record with a department and one
#: without, at one door. That silence was being resolved toward merging.
SYSTEM_PROMPT_V2 = """\
You are an entity-resolution adjudicator for SAP customer master data at Bruker, a scientific-instruments company. Customers are research institutions, universities, hospitals, companies, and their internal departments.

Every record you receive is already at the SAME delivery point — same country, postcode and house number. Address matching is done, and it is not your question. Your only job is to decide, from the names, which records refer to the SAME real-world customer entity.

An entity is a specific (institution, delivery point, department-if-any).

Each record gives you:
- institution: the organisation or company.
- department: a faculty, institute, division or sub-unit within it. EMPTY means the record names no department. Text that turned out to be a delivery desk ("Central Receiving"), a trading name, a person, or the rest of a truncated institution name has already been taken out of this field — so an empty department means the record genuinely names none.
- aliases, operating_name, suggested_name: other names for the SAME institution.
- record_type, ror_id, lei_id: what Phase 1 resolved.
- street_match: how the two street strings compared. "differs" means only that the strings did not match — the delivery point already matched on postcode and house number, so it is NEVER a reason to call two records different entities.

SAME ENTITY when: same institution at this delivery point AND
  (a) both have no department, or
  (b) both have a department and one is a variant, abbreviation, or sub-unit of the other.
DIFFERENT ENTITY when: one has a department and the other has none; or the departments are
different organisational units; or the institutions are different organisations (a brand,
subsidiary, or successor company is a different organisation unless an alias field says otherwise).
Acronyms, abbreviations, truncations, legal-suffix differences, spelling variants and renames
are the SAME institution. Shared ROR/LEI means same institution, not same entity.

Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms ("Dept" = "Department", "Mech Eng" = "Mechanical Engineering"), word reordering, legal-form suffixes (GmbH, AG, Inc., Ltd, e.V.), historical renames or restructures, and spelling variants/typos.

If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome.\
"""


def system_prompt() -> str:
    """The system prompt for the flag setting in force."""
    from dedup.flags import v2_name2

    return SYSTEM_PROMPT_V2 if v2_name2() else SYSTEM_PROMPT


def prompt_version() -> str:
    """The prompt version for the flag setting in force."""
    from dedup.flags import v2_name2

    return PROMPT_VERSION_V2 if v2_name2() else PROMPT_VERSION


def build_mode_a_user_prompt(signatures: List[dict]) -> str:
    """Mode A (partition) user message.

    ``signatures`` is a list of dicts with keys: signature_id, name1, name2,
    ror_id, lei_id. The LLM always sees the original (un-normalized) names.
    """
    listing = json.dumps({"signatures": signatures}, ensure_ascii=False, indent=2)
    return (
        "Group the following signatures into entities. "
        "Return STRICT JSON only, no other text:\n"
        '{"entities":[{"signature_ids":["s1","s3"],"institution":"<short label>",'
        '"department":"<short label or empty>","confidence":<0-1>,'
        '"reasoning":"<1-2 sentences>"}],"uncertain_signature_ids":["s7"]}\n'
        "Every input signature_id must appear exactly once, across either "
        "entities[].signature_ids or uncertain_signature_ids.\n\n"
        f"Signatures:\n{listing}"
    )


def build_mode_a_user_prompt_v2(signatures: List[dict]) -> str:
    """Mode A (partition) user message, v2.

    Same JSON contract as v1 — every signature_id appears exactly once — with
    one optional informational field added per merged pair. ``department_relation``
    is not enforced anywhere: it is there so a reviewer reading the workbook can
    see WHICH of the two same-entity arms the model thought it was applying.
    """
    listing = json.dumps({"signatures": signatures}, ensure_ascii=False, indent=2)
    return (
        "Group the following records into entities. "
        "Return STRICT JSON only, no other text:\n"
        '{"entities":[{"signature_ids":["s1","s3"],"institution":"<short label>",'
        '"department":"<short label or empty>","department_relation":'
        '"same"|"variant"|"different"|"n/a","confidence":<0-1>,'
        '"reasoning":"<1-2 sentences>"}],"uncertain_signature_ids":["s7"]}\n'
        "Every input signature_id must appear exactly once, across either "
        "entities[].signature_ids or uncertain_signature_ids.\n\n"
        f"Records:\n{listing}"
    )


def build_mode_b_user_prompt(candidate: dict, canonicals: List[dict]) -> str:
    """Mode B (incremental assignment) user message.

    ``candidate`` is a dict with keys signature_id, name1, name2, ror_id, lei_id.
    ``canonicals`` is a list of dicts with keys entity_id, institution,
    department, name1, name2, ror_id, lei_id (example name1/name2 of the entity).
    """
    payload = json.dumps(
        {"candidate": candidate, "entities": canonicals},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Decide whether the candidate signature is the same entity as one of "
        "the listed entities, or a new entity. Return STRICT JSON only:\n"
        '{"decision":"match"|"new"|"uncertain","matched_entity_id":"<id or null>",'
        '"confidence":<0-1>,"reasoning":"<1-2 sentences>"}\n\n'
        f"{payload}"
    )


def build_mode_b_user_prompt_v2(candidate: dict, canonicals: List[dict]) -> str:
    """Mode B / residue user message, v2. Same contract, richer records."""
    payload = json.dumps(
        {"candidate": candidate, "entities": canonicals},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Decide whether the candidate record is the same entity as one of "
        "the listed entities, or a new entity. Return STRICT JSON only:\n"
        '{"decision":"match"|"new"|"uncertain","matched_entity_id":"<id or null>",'
        '"department_relation":"same"|"variant"|"different"|"n/a",'
        '"confidence":<0-1>,"reasoning":"<1-2 sentences>"}\n\n'
        f"{payload}"
    )
