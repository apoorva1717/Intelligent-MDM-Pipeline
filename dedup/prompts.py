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

Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms ("Dept" = "Department", "Mech Eng" = "Mechanical Engineering"), word reordering, legal-form suffixes (GmbH, AG, Inc., Ltd, e.V.), historical renames or restructures, and spelling variants/typos.

If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome.\
"""


def build_mode_a_user_prompt(signatures: List[dict]) -> str:
    """Mode A (partition) user message.

    ``signatures`` is a list of dicts with keys: signature_id, name1, name2,
    ror_id. The LLM always sees the original (un-normalized) names.
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


def build_mode_b_user_prompt(candidate: dict, canonicals: List[dict]) -> str:
    """Mode B (incremental assignment) user message.

    ``candidate`` is a dict with keys signature_id, name1, name2, ror_id.
    ``canonicals`` is a list of dicts with keys entity_id, institution,
    department, name1, name2, ror_id (example name1/name2 of the entity).
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
