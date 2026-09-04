"""What the text below Name 1 actually is (v2, ``DEDUP_V2_NAME2``).

v1 treats every populated slot below Name 1 as a department: ``department_text``
joins Names 2-5 and ``has_name2`` is true whenever the result is non-empty
(dedup/signatures.py:65-76, :109-117). The deterministic asymmetry rule then
says a record with a department can never be the same entity as one without —
which is right, and is exactly why the classification has to be right too.

In real SAP data that slot holds five other things, and each one costs a merge:

======================  ==============================  =====================
what the slot holds     example from the stress batch    v1 reads it as
======================  ==============================  =====================
a delivery desk         "Central Receiving"              a department
a trading name          "DBA Lee Health"                 a department
Name 1's own tail       "Institute, Inc"                 a department
the institution itself  "Case Western Reserve" under      a department
                        Name 1 "GHW23"
a person                "Emanuela Zacco - LCA Core"      a department
======================  ==============================  =====================

Every row in that table is one half of a duplicate pair the batch contains, and
in each case v1 put the two halves in different buckets and never compared
them. None of them names a sub-unit of anything.

So this module answers, per slot: institution, department, alias, hint — and
``has_name2`` becomes a statement about the department it found rather than
about whether the cell was blank.

The Phase 1 detectors are imported, never re-implemented: ``has_no_canonical_form``
(enrichment/search_terms.py:678) already owns "is this a back-office desk or a
phrase that names nothing", the DBA regexes own trading names
(enrichment/preprocess.py:613), ``_CO_ATTN_PREFIX_RE`` owns c/o and ATTN, and
``_person_candidate`` owns person shapes. Two answers to any of those questions
is one answer too many.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Literal, Optional, Sequence

from rapidfuzz.distance import JaroWinkler

from dedup.candidates import strip_legal_suffix

# Phase 1 authorities. Imported, with thin adapters below where the signature
# does not fit — never copied.
from enrichment.preprocess import (  # noqa: F401 — _normalise_dba is used via _is_dba
    _CO_ATTN_PREFIX_RE,
    _normalise_dba,
    _person_candidate,
)
from enrichment.search_terms import has_no_canonical_form

SlotKind = Literal[
    "none", "logistics", "alias", "overflow", "institution", "contact", "department"
]

#: Delivery, purchasing and back-office desks. Every site has one and none of
#: them says whose site it is, so two records that differ only by this are one
#: entity. Stated here as well as reached through ``has_no_canonical_form``
#: because the change request names them explicitly and the two vocabularies
#: were built for different questions.
LOGISTICS_TERMS: tuple[str, ...] = (
    "receiving", "shipping", "warehouse", "distribution", "central supply",
    "stores", "dock", "purchasing", "procurement", "accounts payable", "a/p",
    "invoicing", "billing", "mailroom", "materials management",
)

_LOGISTICS_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in LOGISTICS_TERMS) + r")\b",
    re.IGNORECASE,
)

#: "trading as", and the "A <something> Company" form a subsidiary uses to
#: state its parent. Neither is a sub-unit of the record's institution.
_TRADING_AS_RE = re.compile(r"\btrading\s+as\b", re.IGNORECASE)
_A_COMPANY_RE = re.compile(r"^an?\s+.+\s+company$", re.IGNORECASE)

#: Name 1 as an opaque internal code — "GHW23", "KMB3 LLC". Short, upper-case,
#: no lower-case letters: the shape of a customer key, not of a name.
_OPAQUE_NAME1_RE = re.compile(r"^[A-Z0-9]{3,6}(?: LLC| Inc\.?)?$")

#: A site tacked onto the end of a name — "… - Dallas", "… (Cambridge)".
_TRAILING_SITE_RE = re.compile(r"\s*(?:[-–—]\s*[^-–—()]+|\([^()]+\))\s*$")

#: Generic organisational nouns that continue a name but cannot start one.
#: A slot made only of these is Name 1's tail, not a unit: "Institute, Inc" is
#: the end of "EMD Serono Research & Development", not a department of it.
_CONTINUATION_NOUNS = frozenset({
    "institute", "institutes", "center", "centre", "centers", "centres",
    "laboratory", "laboratories", "labs", "lab", "university", "college",
    "hospital", "foundation", "association", "society", "trust", "holdings",
    "industries", "technologies", "systems", "solutions", "group", "company",
    "corporation", "partners", "ventures", "international", "worldwide",
    "works", "research",
})

#: Words a truncated Name 1 ends on. "Palo Alto Veterans Institute for" is not
#: a name that ended; it is a name that was cut off.
_DANGLING_CONNECTORS = frozenset({"for", "of", "and", "the", "&", "at", "in", "de"})

#: How close a rebuilt Name 1 must be to a real one in the block to count as
#: overflow. Higher than the candidate threshold on purpose: this rule ends
#: with two rows sharing a signature outright, with no model in between.
OVERFLOW_THRESHOLD = 0.92

#: How close the Name 2 of an opaque-coded row must be to a real Name 1.
INSTITUTION_THRESHOLD = 0.85


@dataclass
class SlotResult:
    """What the whole name block resolves to.

    ``institution`` and ``department`` are the two halves of the signature key.
    ``aliases`` are other names for the same institution — a trading name, the
    opaque code that used to sit in Name 1 — and are shown to the model, never
    keyed on. ``hints`` are text that identifies a person or a place rather
    than an organisation, and are shown for context only.
    """

    institution: str
    department: str = ""
    aliases: List[str] = field(default_factory=list)
    kind: SlotKind = "none"
    hints: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Adapters over the Phase 1 detectors
# ---------------------------------------------------------------------------

def _is_dba(value: str) -> bool:
    """Whether *value* carries a "doing business as" marker in any spelling.

    ``preprocess._normalise_dba`` answers this already — it returns a
    ``changed`` flag precisely when one of its patterns fired — so this is the
    adapter, not a seventh DBA regex.
    """
    _text, changed = _normalise_dba(value)
    return changed


def _is_contact(name1: str, value: str) -> Optional[str]:
    """The person *value* names, or None.

    Two shapes, and only two: a c/o or ATTN prefix (Phase 1 owns the marker),
    and a person name followed by a separator and their unit — "Emanuela Zacco
    - LCA Core".

    A bare two-word value is deliberately NOT read as a person, even though
    ``_person_candidate`` accepts one. "Fairchild Science" has the shape of a
    first and last name and is a Stanford building's department; reading it as
    a contact would empty the department and merge that record into the bare
    "Stanford University" rows — the exact trap this batch is built around.
    The error is asymmetric: mistaking a department for a person destroys a
    real distinction, while mistaking a person for a department only fails to
    merge. So the separator is required as evidence.
    """
    if _CO_ATTN_PREFIX_RE.match(value):
        return value.strip()
    head = re.split(r"\s*[-–—,/|]\s*", value, maxsplit=1)[0].strip()
    if head == value.strip() or len(head.split()) != 2 or not _person_candidate(head):
        return None
    # A unit of the record's own institution repeats a word of its name:
    # "United States Army" / "Army Contracting Command - Detroit Arsenal" has
    # the shape of a person followed by a site, and is neither.
    own = {token.lower().strip(".,") for token in name1.split()}
    if own & {token.lower().strip(".,") for token in head.split()}:
        return None
    return value.strip()


def _is_logistics(value: str) -> bool:
    return bool(_LOGISTICS_RE.search(value)) or has_no_canonical_form(value)


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

def _best_jw(value: str, others: Iterable[str]) -> float:
    stripped = strip_legal_suffix(value)
    if not stripped:
        return 0.0
    return max(
        (
            JaroWinkler.similarity(stripped, strip_legal_suffix(other))
            for other in others
            if strip_legal_suffix(other)
        ),
        default=0.0,
    )


def _ends_in_legal_suffix(name: str) -> bool:
    return strip_legal_suffix(name) != " ".join(
        re.sub(r"[^0-9a-z]+", " ", name.lower()).split()
    )


def _is_continuation(value: str) -> bool:
    """Whether *value* is only generic organisational nouns and legal forms."""
    remainder = strip_legal_suffix(value).split()
    return bool(remainder) and all(word in _CONTINUATION_NOUNS for word in remainder)


def _is_overflow(name1: str, name2: str, block_name1s: Sequence[str]) -> bool:
    """Whether Name 2 is the rest of Name 1 rather than a unit within it."""
    if not name1 or not name2 or _ends_in_legal_suffix(name1):
        return False
    others = [n for n in block_name1s if n and n.strip() != name1.strip()]
    if _best_jw(f"{name1} {name2}", others) >= OVERFLOW_THRESHOLD:
        return True
    if _is_continuation(name2):
        return True
    # A Name 1 that ends on "and", "for", "of" or "at" is not a name that
    # ended — it is one that was cut off at the field width, and the next slot
    # is the rest of it. "Shell International Exploration and" / "Production
    # Inc"; "Palo Alto Veterans Institute for" / "Research"; "American School
    # of Classical Studies at" / "Athens". No institution names a department
    # this way, so the connector alone carries the rule.
    tokens = name1.split()
    return bool(tokens) and tokens[-1].lower().strip(".,") in _DANGLING_CONNECTORS


def _is_institution(name1: str, name2: str, block_name1s: Sequence[str]) -> bool:
    """Whether Name 1 is an opaque code and Name 2 is the real institution."""
    if not _OPAQUE_NAME1_RE.match(name1.strip()):
        return False
    others = [n for n in block_name1s if n and n.strip() != name1.strip()]
    return _best_jw(name2, others) >= INSTITUTION_THRESHOLD


def _is_alias(name1: str, name2: str) -> bool:
    if _is_dba(name2) or _TRADING_AS_RE.search(name2) or _A_COMPANY_RE.match(name2.strip()):
        return True
    # Name 2 repeats Name 1 with a site tacked on: one name, not two things.
    without_site = _TRAILING_SITE_RE.sub("", name2).strip()
    if not without_site or without_site == name2.strip():
        return False
    return JaroWinkler.similarity(
        strip_legal_suffix(without_site), strip_legal_suffix(name1)
    ) >= INSTITUTION_THRESHOLD


def classify_slots(
    name1: Optional[str],
    name2: Optional[str] = None,
    name3: Optional[str] = None,
    name4: Optional[str] = None,
    name5: Optional[str] = None,
    block_name1s: Sequence[str] = (),
) -> SlotResult:
    """Resolve one row's name block into (institution, department, aliases).

    The four rules that can rewrite the institution — overflow, opaque Name 1,
    trading name, and the desk/person rules that empty the department — are
    asked in that order, and the order is load-bearing:

    * overflow before logistics, or PAVIR's "Research" is read as a facility
      function and its institution is never rebuilt;
    * opaque-Name-1 before everything, because when Name 1 is a customer code
      nothing below it can be a sub-unit of anything;
    * alias before department, so "A Kimball Electronics Company" states a
      parent rather than inventing a unit.

    Slots below the first are classified on their own terms, so a delivery desk
    in Name 3 is dropped from the department just as one in Name 2 is.
    """
    name1 = (name1 or "").strip()
    slots = [(value or "").strip() for value in (name2, name3, name4, name5)]
    slots = [value for value in slots if value]

    if not slots:
        return SlotResult(institution=name1, kind="none")

    head, tail = slots[0], slots[1:]
    aliases: List[str] = []
    hints: List[str] = []

    if _is_institution(name1, head, block_name1s):
        # Name 1 was a customer code. The institution is in Name 2, and the
        # code is worth keeping as an alias — it is how this site refers to
        # itself, and a later record may arrive carrying it again.
        return SlotResult(
            institution=head, aliases=[name1], kind="institution",
            hints=[value for value in tail],
        )

    if _is_overflow(name1, head, block_name1s):
        return SlotResult(
            institution=f"{name1} {head}".strip(),
            department=_department_of(tail, hints, aliases),
            aliases=aliases, kind="overflow", hints=hints,
        )

    department_parts: List[str] = []
    head_kind: SlotKind = "department"

    for index, value in enumerate(slots):
        slot_kind = _slot_kind(name1, value)
        if index == 0:
            head_kind = slot_kind
        if slot_kind == "alias":
            aliases.append(value)
        elif slot_kind == "contact":
            hints.append(value)
        elif slot_kind == "logistics":
            pass  # a desk is not a unit; it names no entity to be different from
        else:
            department_parts.append(value)

    # The reported kind describes the OUTCOME: any surviving department text
    # makes this a departmental record, whatever the first slot happened to be.
    return SlotResult(
        institution=name1,
        department=" / ".join(department_parts),
        aliases=aliases,
        kind="department" if department_parts else head_kind,
        hints=hints,
    )


def _slot_kind(name1: str, value: str) -> SlotKind:
    if _is_alias(name1, value):
        return "alias"
    if _is_logistics(value):
        return "logistics"
    if _is_contact(name1, value):
        return "contact"
    return "department"


def _department_of(
    values: Sequence[str], hints: List[str], aliases: List[str]
) -> str:
    """The department text in *values*, routing the rest to hints / aliases."""
    parts: List[str] = []
    for value in values:
        kind = _slot_kind("", value)
        if kind == "alias":
            aliases.append(value)
        elif kind == "contact":
            hints.append(value)
        elif kind == "department":
            parts.append(value)
    return " / ".join(parts)
