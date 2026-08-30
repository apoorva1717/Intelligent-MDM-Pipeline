"""Is this the same organisation? — asked once, answered per purpose.

Thirteen call sites across nine files each roll their own fuzzy comparison with
their own threshold. Collapsing them into one boolean was the obvious move and
it is the wrong one: measured on the golden set, the strictness those sites use
is not drift, it is *fitted to the question each is asking*.

    the NAME question    "Kadlec Regional Med Ctr" vs "Kadlec Regional Medical
                         Center" — same organisation, and the strict guard that
                         refuses the rewrite is worth +7 cells, because the
                         reviewer wants the form the customer typed.
    the DOMAIN question  "Merck Sharp & Dohme Corp." vs "merck.com" — same
                         organisation, and the strict guard costs 28 records a
                         `domain-unverified` flag for sites that are plainly
                         theirs.

One comparator, then, but the *context* decides what counts as agreement. A
single boolean would have to pick one of those answers and be wrong about the
other.

This module starts with the context an agentic lane needs and cannot get
anywhere else.

``not_drifted``
    Did a proposal wander to a DIFFERENT organisation? This is not "are these
    the same name" — it is the much weaker "is there any anchor left". A
    bounded agent proposing a name is checked against a register, and a
    register confirms `Harvard University` for a record that says `Wyss Inst`
    because Harvard exists. Verification catches fabrication; it cannot catch
    drift to a real neighbour, which in customer master data is the more
    dangerous error — silently replacing a customer with its parent or its
    acquirer reads as successful enrichment.

    Measured on the agent bake-off, the six drifts were:

        Wyss Inst Accounts Payable  -> Harvard University
        Neptune Benson, Inc.        -> Evoqua Water Technologies
        UTSW Medical Center at RedBird -> (the main campus, site dropped)

    and the correct resolutions it must NOT block were:

        USGS GD              -> United States Geological Survey
        VAMC West LA Visn 22 -> VA West Los Angeles Medical Center
        UTSW Medical Center  -> UT Southwestern Medical Center

    `utils.text_utils.canonical_preserves_identity` rejects both groups — it is
    the NAME question's guard and is correctly strict for that. The drift
    question is answered by a much lower bar: the proposal must retain some
    anchor from what the record actually said, either a shared distinctive
    token or an acronym it expands.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from enrichment.registry_match import name_core
from utils.text_utils import canonical_preserves_identity

logger = logging.getLogger(__name__)

#: Words that describe an organisation rather than name it. Shared *generic*
#: words are not an anchor — "Medical Center" appears in thousands of names,
#: and treating it as evidence is what would let `Wyss Inst` drift to any
#: institute in the register.
_GENERIC = frozenset({
    "the", "of", "and", "for", "at", "in", "a", "an",
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "plc",
    "gmbh", "ag", "sa", "bv", "nv", "lp", "llp",
    "university", "college", "school", "institute", "institutes", "inst",
    "hospital", "clinic", "medical", "center", "centre", "health",
    "laboratory", "laboratories", "lab", "labs", "research", "sciences",
    "science", "department", "dept", "division", "div", "group", "services",
    "service", "systems", "technologies", "technology", "international",
    "national", "regional", "us", "usa", "united", "states", "america",
    "american", "north", "south", "east", "west", "central",
})

#: Contexts this comparator answers. Each is a different question; adding one
#: means stating what agreement MEANS for it, not tuning a number.
CONTEXTS = frozenset({"not_drifted", "adopt_name"})


@dataclass(frozen=True)
class IdentityVerdict:
    same: bool
    reason: str
    #: The anchor that carried the decision, when there was one.
    anchor: str | None = None


def _tokens(value: str | None) -> list[str]:
    return [t.lower() for t in name_core(value)]


def _distinctive(value: str | None) -> set[str]:
    return {t for t in _tokens(value) if t not in _GENERIC and len(t) > 1}


def _initials(words: list[str]) -> str:
    return "".join(w[0] for w in words if w)


def _is_acronym_of(short: str, phrase: str | None) -> bool:
    """Is *short* an acronym the words of *phrase* spell out?

    Tolerant of a prefix rather than the whole phrase, because a record's
    acronym usually covers the distinctive head and not the trailing
    "Medical Center": `UTSW` against "UT Southwestern Medical Center" spells
    out only the first two words.
    """
    letters = re.sub(r"[^a-z0-9]", "", (short or "").lower())
    if not (2 <= len(letters) <= 8):
        return False
    words = [w for w in _tokens(phrase)]
    if not words:
        return False
    for take in range(len(words), 0, -1):
        if _initials(words[:take]) == letters:
            return True
    # …and the same test with generic words dropped, so "USGS" matches
    # "United States Geological Survey" whether or not "United"/"States" are
    # treated as generic elsewhere.
    allw = [w for w in re.findall(r"[a-z0-9]+", (phrase or "").lower())]
    for take in range(len(allw), 0, -1):
        if _initials(allw[:take]) == letters:
            return True
    return False


def same_organisation(
    a: str | None,
    b: str | None,
    context: str = "not_drifted",
) -> IdentityVerdict:
    """Do *a* and *b* name the same organisation, for the purpose in *context*?

    *a* is what the record states; *b* is what something proposes. The order
    matters — the question is always whether *b* is still about *a*.
    """
    if context not in CONTEXTS:
        raise ValueError(
            f"unknown identity context {context!r}; "
            f"add it deliberately and say what agreement means for it",
        )

    a_s, b_s = (a or "").strip(), (b or "").strip()
    if not a_s or not b_s:
        return IdentityVerdict(False, "one side is empty")

    if context == "adopt_name":
        # The strict question, unchanged: may a proposal REPLACE the name the
        # record states? `canonical_preserves_identity` already owns this and
        # is measured to be worth +7 cells on the golden set. Named here so the
        # call sites can share one entry point, not so the answer changes.
        ok = canonical_preserves_identity(a_s, b_s)
        return IdentityVerdict(ok, "canonical_preserves_identity")

    # context == "not_drifted"
    da, db = _distinctive(a_s), _distinctive(b_s)
    shared = da & db
    if shared:
        return IdentityVerdict(
            True, "shares a distinctive token", anchor=sorted(shared)[0],
        )

    # An acronym in the record that the proposal spells out is an anchor even
    # though no token is shared: "USGS" and "United States Geological Survey"
    # have no word in common at all.
    for token in _tokens(a_s):
        if _is_acronym_of(token, b_s):
            return IdentityVerdict(True, "acronym expanded", anchor=token)
    # …and the reverse, for a record that states the long form.
    for token in _tokens(b_s):
        if _is_acronym_of(token, a_s):
            return IdentityVerdict(True, "acronym contracted", anchor=token)

    # An acronym whose letters come from INSIDE a compound word defeats every
    # initials test: `UTSW` is UT **S**outh**W**estern, `VAMC` is VA **M**edical
    # **C**enter. Those records carry no distinctive token but they do carry
    # context — "Medical Center", "West" — and the proposal repeats it. Generic
    # words are not an anchor on their own, which is the rule that keeps `Wyss
    # Inst` from reaching any institute in the register; paired with an
    # acronym-shaped token in the record they are enough to say the proposal is
    # still about this record.
    #
    # It stays closed for the drifts: `Wyss Inst` and `Harvard University` share
    # no generic word either ("inst" against "university"), nor do `Neptune
    # Benson, Inc.` and `Evoqua Water Technologies`.
    acronymish = [
        t for t in _tokens(a_s)
        if 2 <= len(t) <= 6 and t not in _GENERIC and not t.isdigit()
    ]
    if acronymish:
        ga = {t for t in _tokens(a_s) if t in _GENERIC}
        gb = {t for t in _tokens(b_s) if t in _GENERIC}
        shared_context = (ga & gb) - {"the", "of", "and", "for", "at", "in"}
        # TWO shared descriptors, not one. A single shared generic word is
        # coincidence and lets "Iso Group Inc" reach "CoStar Group" on the word
        # "group" — the exact drift `canonical_preserves_identity` names in its
        # own docstring. Two ("Medical Center") is context.
        if len(shared_context) >= 2:
            return IdentityVerdict(
                True, "acronym plus shared context",
                anchor=sorted(shared_context)[0],
            )

    # Nothing the record said survives in the proposal.
    if not da:
        # The record carried no distinctive token of its own to anchor on
        # (an all-generic name like "Medical Center"). Refusing here would
        # block a resolution the record cannot contradict, so this is not
        # treated as drift.
        return IdentityVerdict(True, "record has no distinctive anchor")

    return IdentityVerdict(
        False,
        f"no anchor: nothing of {sorted(da)!r} survives in the proposal",
    )
