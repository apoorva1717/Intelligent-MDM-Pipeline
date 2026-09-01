"""Three-verdict identity comparison for a proposed canonical name.

The acceptance path's question is not "is this answer certain?" but "has
anything DISPROVED it?". :func:`classify_name_change` answers that with three
verdicts instead of the boolean
:func:`utils.text_utils.canonical_preserves_identity` returns:

``SAME``
    Every distinctive token the input supplies maps onto the proposal, and the
    proposal introduces nothing the input does not account for. A reformat, an
    abbreviation expansion, a truncation completed, a typo repaired.

``UNDECIDABLE``
    The input carries tokens the proposal does not account for, but nothing
    contradicts it — the unmatched tokens are opaque codes, directional or
    locality qualifiers, or bare numerals, and the proposal adds only
    organisation-type or locality words. The record said something the
    proposal neither confirms nor denies. ``VA MC West LA Visn 22`` →
    ``VA Greater Los Angeles Healthcare System`` is the case this verdict
    exists for.

``DIFFERENT``
    The proposal names another entity: it drops a *substantive* distinctive
    word the input stated (or a parent organisation), or introduces a
    substantive distinctive word the input cannot account for. The only
    verdict that refuses a write.

Why the boolean was not enough: :func:`canonical_preserves_identity` collapses
``UNDECIDABLE`` into its rejection branch, so a correct answer for an
abbreviated record is discarded exactly as a hallucination is. It also compares
raw tokens, and its ``_token_covers`` helper imposes a four-character floor
that makes ``lab``↔``laboratories`` and ``us``↔``united`` fail — two of the
commonest legitimate expansions in the corpus.

The comparison here runs in NORMALISED space on both sides
(:func:`~utils.text_utils.expand_abbreviations`, the misspelling patterns it
carries, and :func:`~utils.text_utils.collapse_legal_suffix`), takes prefix and
truncation cover at any length, accepts a per-token spelling variant at
``_SPELLING_VARIANT_TOKEN_RATIO``, and resolves acronyms against the tokens
they expand to, so ``US``→``United States`` and ``UTSW``→``UT Southwestern``
are read as the expansions they are rather than as new words.

The boolean guard is left in place, unchanged: it still backs
``canonical_is_spelling_variant`` and the GLEIF typo re-verification, whose
question really is binary.
"""

from __future__ import annotations

import re
from typing import Iterable

from rapidfuzz import fuzz

from utils.text_utils import (
    _GENERIC_COMPANY_WORDS,
    _SPELLING_VARIANT_TOKEN_RATIO,
    PARENT_ORG_ACRONYMS,
    collapse_legal_suffix,
    expand_abbreviations,
)

# The three verdicts. Plain strings so they survive a trip through a result
# dict, a log line and a flag detail without a custom encoder.
SAME = "same"
UNDECIDABLE = "undecidable"
DIFFERENT = "different"

#: Words that name what KIND of organisation this is rather than WHICH one.
#: A proposal may add them freely — "Harvard" → "Harvard University" does not
#: change the entity. Extends the narrower `_ORG_TYPE_ADDABLE` in text_utils
#: with the health-system and public-body vocabulary the corpus needs, which
#: is what lets "VA Greater Los Angeles Healthcare System" read as an
#: elaboration of "VA MC …" rather than as a different body.
_ORG_TYPE_WORDS = {
    "university", "universities", "college", "colleges", "school", "schools",
    "institute", "institutes", "institution", "laboratory", "laboratories",
    "foundation", "center", "centre", "centers", "centres", "hospital",
    "hospitals", "clinic", "clinics", "academy", "conservatory", "seminary",
    "polytechnic", "system", "systems", "network", "healthcare",
    "medical", "medicine", "services", "service", "organization",
    "organisation", "administration", "agency", "authority", "department",
    "division", "office", "bureau", "board", "trust", "association",
    "society", "council", "district", "campus", "branch", "facility",
}

#: Compass, scope and locality qualifiers. Dropping or adding one of these
#: relocates a description of the same body; it does not name a different one.
#: "West LA" → "Greater Los Angeles" turns on this set.
_LOCALITY_WORDS = {
    "north", "south", "east", "west", "northern", "southern", "eastern",
    "western", "northeast", "northwest", "southeast", "southwest",
    "central", "greater", "metropolitan", "metro", "upper", "lower",
    "national", "international", "regional", "local", "global",
}

#: Connectors carrying no identity.
_CONNECTORS = {
    "the", "of", "and", "for", "in", "at", "on", "to", "de",
    "des", "du", "von", "van", "der", "den", "a", "an", "&",
}

#: Closed multi-token expansions. The acronym resolver below already derives
#: most of these from the proposal itself; this map states the ones the
#: pipeline relies on explicitly, so a change in the resolver cannot silently
#: turn "US" → "United States" into a dropped token.
_MULTI_TOKEN_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "us": ("united", "states"),
    "usa": ("united", "states", "america"),
    "uk": ("united", "kingdom"),
    "va": ("veterans", "affairs"),
    "ut": ("university", "texas"),
    "uc": ("university", "california"),
    "mc": ("medical", "center"),
    "hq": ("headquarters",),
}

#: Short words that are ordinary English rather than opaque codes, so dropping
#: one IS a contradiction even though it is under the length floor. Keeps the
#: substantive test honest without shipping a dictionary.
_SHORT_REAL_WORDS = {
    "paper", "water", "power", "steel", "glass", "wood", "food", "gas",
    "oil", "air", "sea", "bank", "farm", "gene", "cell", "bone", "eye",
    "ear", "skin", "hair", "milk", "corn", "rice", "seed", "fish", "bird",
    "iron", "gold", "coal", "salt", "silk", "wine", "beer", "toys", "arts",
    "law", "tax", "gift", "home", "park", "lake", "hill", "rock", "sand",
    "star", "moon", "sun", "sky", "fire", "ice", "snow", "rain", "wind",
    "heat", "life", "mind", "body", "care", "aid", "help", "hope", "peace",
    "youth", "child", "women", "men", "boys", "girls", "kids", "baby",
}

#: Mailroom and desk vocabulary. These name a FUNCTION the record routed mail
#: to, never the organisation, so a canonical name that omits them contradicts
#: nothing — the slot router moves them to a department slot instead.
_ROLE_NOISE_WORDS = {
    "accounts", "account", "payable", "payables", "receivable", "receivables",
    "attn", "attention", "invoice", "invoices", "billing", "purchasing",
    "procurement", "payment", "payments", "remittance", "mailroom",
    "shipping", "receiving", "warehouse", "dept", "care",
}

#: Generic organisational descriptors `_GENERIC_COMPANY_WORDS` does not carry.
#: "3M Corporate" and "3M Company" are one entity described two ways.
_GENERIC_EXTRA = {
    "corporate", "worldwide", "enterprises", "enterprise", "industries",
    "international", "global", "holdings", "partners", "ventures",
}

#: Connectors a name never ends on. A value that does was cut off by the
#: field width, so the tail the proposal supplies is a completion.
_DANGLING_TAIL = {"of", "for", "and", "the", "at", "in", "on", "to", "&", "de"}

#: Ending pairs that make two forms of ONE word rather than two words.
#: "laboratory"/"laboratories" and "science"/"sciences" belong together;
#: "thermal"/"thermo" and "national"/"nations" do not.
_INFLECTIONS: list[set[str]] = [
    {"", "s"}, {"", "es"}, {"y", "ies"}, {"e", "es"}, {"", "d"},
    {"", "ed"}, {"", "ing"}, {"e", "ing"}, {"um", "a"}, {"us", "i"},
    {"is", "es"}, {"", "al"}, {"", "es"}, {"on", "a"}, {"ex", "ices"},
]

_TOKEN_RE = re.compile(r"[A-Za-z0-9&]+")

#: Below this length a token with no dictionary standing reads as a code
#: ("NMR", "VISN", "RECG") rather than as a word, and its absence from a
#: proposal is not evidence the proposal is about something else.
_SUBSTANTIVE_MIN_LEN = 5


def _normalise(text: str | None) -> str:
    """Expand abbreviations and collapse long legal forms, on BOTH sides.

    Comparing raw strings is what made ``Lab``↔``Laboratories`` and
    ``Thermal Scientific Inc``↔``Thermal Scientific, Incorporated`` look like
    different entities. Both sides get the same treatment so the comparison
    asks about identity rather than about spelling conventions.
    """
    if not text or not text.strip():
        return ""
    out = expand_abbreviations(text) or text
    out = collapse_legal_suffix(out) or out
    return out


def _tokens(text: str | None) -> list[str]:
    """Identity-bearing tokens of *text*, in order, lower-cased."""
    return [
        t.lower() for t in _TOKEN_RE.findall(_normalise(text))
        if t.lower() not in _CONNECTORS
        and t.lower() not in _GENERIC_COMPANY_WORDS
        and t.lower() not in _GENERIC_EXTRA
    ]


def _covers(a: str, b: str) -> bool:
    """True when tokens *a* and *b* name the same word.

    Three ways, and the first is where the old guard's bug lived: a prefix
    relation at ANY length, so ``lab``↔``laboratories``, ``ut``↔``utsw`` and a
    truncation the record shipped (``researc``↔``research``) all cover. The
    four-character floor the boolean guard imposes is exactly what made those
    fail.
    """
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return True
    # Two inflections of one stem. "laboratory"/"laboratories" share nine
    # characters but score 81.8 on the typo ratio, so the ratio alone reads a
    # plural as a different word.
    #
    # The shared stem is necessary and not sufficient: "thermal" and "thermo"
    # share five characters too, and they are different words — accepting them
    # let "Thermal Scientific Inc" be rewritten to "Thermo Scientific". So the
    # endings have to form a real inflection pair, not merely differ.
    stem = 0
    for x, y in zip(a, b):
        if x != y:
            break
        stem += 1
    if stem >= 5 and min(len(a), len(b)) >= 6:
        ends = {a[stem:], b[stem:]}
        if ends in _INFLECTIONS:
            return True
    return (
        min(len(a), len(b)) >= 4
        and fuzz.ratio(a, b) >= _SPELLING_VARIANT_TOKEN_RATIO
    )


def _acronym_expansion(acronym: str, proposal: list[str], start: int) -> int:
    """How many *proposal* tokens from *start* spell out *acronym*, or 0.

    An acronym is expanded by consecutive tokens whose letters it walks in
    order — one letter per token in the ordinary case ("nasa" → "national
    aeronautics space administration"), a run of letters inside one token when
    that token is itself a compound ("sw" → "south-western"), or a whole
    leading chunk when the proposal spells part of the acronym outright
    ("utsw" → "ut" + "southwestern").

    The search backtracks. A greedy walk takes "n" and "a" out of "national"
    and then cannot place the "s", so NASA fails to expand and its own name
    reads as four invented words — which is precisely the discard this module
    exists to stop. Consecutive by design: allowing gaps would let an acronym
    pick its letters out of an unrelated name.
    """
    if not acronym.isalpha() or not 2 <= len(acronym) <= 6:
        return 0

    def walk(rest: str, i: int) -> int | None:
        """Tokens consumed from *i* to spell *rest*, or None."""
        if not rest:
            return 0
        if i >= len(proposal):
            return None
        token = proposal[i]
        # The proposal spells a whole leading chunk of the acronym.
        if len(token) >= 2 and rest.startswith(token):
            nxt = walk(rest[len(token):], i + 1)
            if nxt is not None:
                return 1 + nxt
        if not token.startswith(rest[0]):
            return None
        # Take k of the remaining letters, in order, from inside this token.
        # One is the ordinary case; more than one needs a token long enough to
        # really contain a compound.
        limit = 1
        if len(token) >= 5:
            pos = 0
            for ch in rest:
                found = token.find(ch, pos)
                if found < 0:
                    break
                pos, limit = found + 1, limit + 1
            limit -= 1
        for k in range(1, min(limit, len(rest)) + 1):
            nxt = walk(rest[k:], i + 1)
            if nxt is not None:
                return 1 + nxt
        return None

    used = walk(acronym, start)
    return used or 0


def _is_substantive(token: str) -> bool:
    """True when *token* is a real word rather than a code or a numeral.

    Dropping a substantive word contradicts the input; dropping ``VISN`` or
    ``22`` does not, because neither states anything a canonical name is
    obliged to repeat.
    """
    if token.isdigit():
        return False
    if token in _LOCALITY_WORDS or token in _ORG_TYPE_WORDS:
        return False
    if token in _ROLE_NOISE_WORDS:
        return False
    if token in _SHORT_REAL_WORDS:
        return True
    return len(token) >= _SUBSTANTIVE_MIN_LEN


def _is_parent_org(token: str) -> bool:
    """True for an acronym that names a PARENT organisation ("USDA", "NASA").

    Dropping one is an identity change however short the token is: "NASA Jet
    Propulsion Laboratory" → "Jet Propulsion Laboratory" reassigns the lab.
    """
    return token.upper() in PARENT_ORG_ACRONYMS


def _is_truncated(text: str | None) -> bool:
    """True when *text* was cut off by the SAP field width.

    Detected from the value itself — a name that ends on a connector it can
    never end on ("Expeditors International of"), so no width constant is
    assumed here.
    """
    toks = _TOKEN_RE.findall(text or "")
    return bool(toks) and toks[-1].lower() in _DANGLING_TAIL


def classify_name_change(
    original: str | None,
    proposal: str | None,
    context: Iterable[str | None] = (),
) -> str:
    """Return ``SAME``, ``UNDECIDABLE`` or ``DIFFERENT`` for *proposal*.

    Permissive by construction — the pipeline writes unless disproven, so an
    empty side, or an input with no identity-bearing tokens, returns ``SAME``
    rather than blocking a legitimate rewrite.

    *context* supplies words the RECORD states elsewhere — its city and
    region. A token that appears there is geography, not identity: ROR names
    the Jacksonville site "Mayo Clinic in Florida", and neither the dropped
    "Jacksonville" nor the added "Florida" says the two are different
    organisations. Without it a registry's own name reads as an entity swap
    on every record whose name carries a place.
    """
    if not (original and original.strip()):
        return SAME
    if not (proposal and proposal.strip()):
        return SAME

    orig = _tokens(original)
    prop = _tokens(proposal)
    if not orig or not prop:
        return SAME

    # Geography the record states elsewhere. Compared with the same cover
    # relation as everything else, so "FLA" and "Florida" meet.
    place: list[str] = []
    for item in context:
        place.extend(_tokens(item))

    # Proposal tokens accounted for by something the input said. Seeded with
    # nothing; filled as each input token finds its match below.
    derived: set[int] = set()
    unmatched: list[str] = []

    for token in orig:
        # 1. An acronym the proposal spells out over SEVERAL tokens ("utsw" →
        #    "ut southwestern"). Tried first: "utsw" also prefix-covers "ut"
        #    alone, and stopping there would leave the expansion looking like
        #    an invented word.
        spelled = False
        for start in range(len(prop)):
            used = _acronym_expansion(token, prop, start)
            if used >= 2:
                derived.update(range(start, start + used))
                spelled = True
                break
        if spelled:
            continue
        # 2. A direct cover — same word, prefix/truncation, or spelling variant.
        hit = [i for i, u in enumerate(prop) if _covers(token, u)]
        if hit:
            derived.update(hit)
            continue
        # 3. A declared multi-token expansion ("us" → "united states").
        expansion = _MULTI_TOKEN_EXPANSIONS.get(token)
        if expansion:
            idx = [
                i for i, u in enumerate(prop)
                if any(_covers(w, u) for w in expansion)
            ]
            if idx:
                derived.update(idx)
                continue
        unmatched.append(token)

    # A truncated input ends on a dangling connector; everything the proposal
    # supplies past the last token the input accounted for is the completion
    # of the name, not a new claim about which entity it is.
    tail_from = len(prop)
    if _is_truncated(original) and derived:
        tail_from = max(derived) + 1

    novel = [
        u for i, u in enumerate(prop)
        if i not in derived and i < tail_from
    ]

    # A substantive word on either side that the other cannot account for is
    # the contradiction — this is the hallucination wall.
    def _is_place(token: str) -> bool:
        return any(_covers(token, w) for w in place)

    dropped_substantive = [
        t for t in unmatched
        if (_is_substantive(t) or _is_parent_org(t)) and not _is_place(t)
    ]
    novel_substantive = [
        u for u in novel if _is_substantive(u) and not _is_place(u)
    ]
    if dropped_substantive or novel_substantive:
        return DIFFERENT

    if not unmatched and not novel:
        return SAME
    return UNDECIDABLE


def _full_tokens(text: str | None) -> list[str]:
    """Every word of *text* in normalised space, connectors aside.

    Unlike :func:`_tokens` this KEEPS the generic organisational words.
    Identity does not turn on them — "Acme" and "Acme Inc" are one company —
    but a repair does: adding "Group" to a unit name is new information about
    the record, and calling it a repair is what would let a substantive
    rewrite ship unflagged.
    """
    return [
        t.lower() for t in _TOKEN_RE.findall(_normalise(text))
        if t.lower() not in _CONNECTORS
    ]


def is_pure_repair(original: str | None, proposal: str | None) -> bool:
    """True when *proposal* only repairs *original* — no new information.

    An expansion of the record's own abbreviations, a completed truncation or
    a corrected misspelling, and nothing else. What separates a rewrite worth
    a reviewer's attention from one that just spells the record properly:
    §1(f) flags the former and leaves the latter clean.

    Every token of the proposal must be accounted for by one the input
    supplied — by cover, or as part of an acronym the input stated and the
    proposal spells out ("US" → "United States"). A word that appears from
    nowhere means the proposal said something the record did not.
    """
    if not (original and original.strip()) or not (proposal and proposal.strip()):
        return False
    o, p = _full_tokens(original), _full_tokens(proposal)
    if not o or not p:
        return False

    derived: set[int] = set()
    for token in o:
        spelled = False
        for start in range(len(p)):
            used = _acronym_expansion(token, p, start)
            if used >= 2:
                derived.update(range(start, start + used))
                spelled = True
                break
        if spelled:
            continue
        hit = [i for i, u in enumerate(p) if _covers(token, u)]
        if hit:
            derived.update(hit)
            continue
        expansion = _MULTI_TOKEN_EXPANSIONS.get(token)
        if expansion:
            idx = [
                i for i, u in enumerate(p)
                if any(_covers(w, u) for w in expansion)
            ]
            if idx:
                derived.update(idx)
                continue
        # An input word the proposal does not carry: it was dropped, which is
        # a change to the value and not a repair of it.
        return False
    return len(derived) == len(p)


def introduces_nothing_new(original: str | None, proposal: str | None) -> bool:
    """True when every word of *proposal* is accounted for by *original*.

    The one-directional half of :func:`is_pure_repair`. It asks only "did this
    rewrite ADD anything the record does not support" and says nothing about
    words the rewrite dropped.

    That is the right question for `unverified-inference`, which reports an
    unsupported CLAIM. It is also the only question answerable where the name
    block is compared mid-flight: the flag is computed before a retained slot
    has been restored, so the block legitimately looks shorter than the input
    ("Clinton Twp Facility" is not back in Name 3 yet) and a both-directions
    test reads that as invented content. Loss is a real concern and has its
    own guards — the identity verdict and the unit-word post-check.
    """
    if not (original and original.strip()) or not (proposal and proposal.strip()):
        return False
    o, p = _full_tokens(original), _full_tokens(proposal)
    if not o or not p:
        return False

    derived: set[int] = set()
    for token in o:
        for start in range(len(p)):
            used = _acronym_expansion(token, p, start)
            if used >= 2:
                derived.update(range(start, start + used))
                break
        else:
            derived.update(i for i, u in enumerate(p) if _covers(token, u))
            expansion = _MULTI_TOKEN_EXPANSIONS.get(token)
            if expansion:
                derived.update(
                    i for i, u in enumerate(p)
                    if any(_covers(w, u) for w in expansion)
                )
    return len(derived) == len(p)


def verdict_detail(original: str | None, proposal: str | None) -> str:
    """The ``{detail}`` string a flag renders for a written-but-unverified name.

    Names the value the record arrived with, so one steward click reverts it.
    """
    was = (original or "").strip()
    return f"was '{was}'" if was else "no value was supplied"


def any_verdict(
    original: str | None, proposals: Iterable[str | None],
) -> str:
    """The most permissive verdict across *proposals* — used where a stage
    offers several forms of the same answer."""
    best = DIFFERENT
    order = {SAME: 2, UNDECIDABLE: 1, DIFFERENT: 0}
    for proposal in proposals:
        v = classify_name_change(original, proposal)
        if order[v] > order[best]:
            best = v
    return best
