"""Candidate nomination for the dedup residue pass (STEP B widening).

Mode A (bucket partition) and Mode B (incremental) already adjudicate every
signature pair WITHIN a ``has_name2`` bucket. What they never compare are pairs
the deterministic Name-2 asymmetry rule keeps apart: an empty-Name2 signature
vs a populated-Name2 one, and a signature alone in its bucket. Those pairs
bypass the LLM entirely and default to ``unique`` with no reasoning.

This module NOMINATES such residue pairs for LLM adjudication when there is a
same-entity signal — converging ROR/LEI, suffix-stripped name similarity, or
token-set overlap. Nomination is candidacy ONLY: it never merges. The LLM
verdict and the two-level identity rule still decide.

Everything here is deterministic and pure (no LLM, no network): the same units
in any order yield the same candidate list, so the LLM call sequence is stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from rapidfuzz.distance import JaroWinkler

# Legal-form suffixes as DATA (not inlined in the matcher). Stripped only for
# candidate-similarity computation — never from the canonical signature itself.
# Multi-token / punctuated forms (G.m.b.H., & Co., S.p.A.) are normalised the
# same way as names before matching, so they match as trailing token runs.
LEGAL_SUFFIXES: Tuple[str, ...] = (
    "AG", "Aktiengesellschaft", "GmbH", "G.m.b.H.", "mbH",
    "Inc", "Inc.", "Incorporated", "Corp", "Corp.", "Corporation",
    "Ltd", "Ltd.", "Limited", "LLC", "PLC",
    "BV", "B.V.", "NV", "N.V.", "SA", "S.A.", "SE", "SAS", "SARL",
    "S.r.l.", "SpA", "S.p.A.", "Oy", "AB", "A/S",
    "KG", "KGaA", "OHG", "e.V.", "Co", "Co.", "& Co.",
)


def _normalize_tokens(text: Optional[str]) -> List[str]:
    """Lowercase, drop punctuation, split on whitespace → token list.

    Punctuation-tolerant: "G.m.b.H." → [g, m, b, h], "A/S" → [a, s],
    "& Co." → [co]. Shared by names and suffixes so they match consistently.
    """
    if not text:
        return []
    lowered = re.sub(r"[^0-9a-z]+", " ", str(text).lower())
    return lowered.split()


# Suffix token-tuples, longest first so greedy stripping removes the longest
# trailing match (e.g. "Aktiengesellschaft" before a bare token).
_SUFFIX_TUPLES: Tuple[Tuple[str, ...], ...] = tuple(sorted(
    {tuple(toks) for s in LEGAL_SUFFIXES if (toks := _normalize_tokens(s))},
    key=lambda t: (-len(t), t),
))


def strip_legal_suffix(name: Optional[str]) -> str:
    """Normalised name with trailing legal-form suffix token(s) removed.

    Trailing-only and repeated ("GmbH & Co. KG" → all three go); a suffix word
    that is not trailing is kept ("AG Berlin Services" is unchanged). If
    stripping would empty the name, the normalised full name is kept instead so
    a name that IS just a legal form still compares as something.
    """
    tokens = _normalize_tokens(name)
    stripped = list(tokens)
    changed = True
    while changed and stripped:
        changed = False
        for suf in _SUFFIX_TUPLES:
            k = len(suf)
            if k <= len(stripped) and tuple(stripped[-k:]) == suf:
                del stripped[-k:]
                changed = True
                break
    return " ".join(stripped) if stripped else " ".join(tokens)


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    """Jaccard over token SETS (word-order-insensitive)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# Candidate units + nomination
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateUnit:
    """A block-local unit (an entity after Mode A/B) that can be nominated."""

    index: int
    name: str
    ror_id: Optional[str]
    lei_id: Optional[str]
    has_name2: bool
    adjudicated: bool  # already went through the LLM in Mode A/B
    # The rows behind this unit. Only the v2 address gate reads them, and it is
    # passed in by the caller, so this stays a plain tag on the unit rather
    # than a second address parser living here. Defaulted last so every
    # existing construction is unchanged.
    row_ids: Tuple[str, ...] = ()
    # Other names for the same institution (v2). Read only by the cross_slot
    # rule, which is off unless the block is large enough for Mode B.
    aliases: Tuple[str, ...] = ()
    operating_name: Optional[str] = None
    suggested_name: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    """A nominated pair, a<b, with the rule that nominated it and a sort score."""

    a: int
    b: int
    rule: str   # "id" | "name" | "token"
    score: float

    @property
    def sort_key(self) -> tuple:
        # id-convergence first, then name similarity descending, then token
        # overlap descending; (a, b) breaks any remaining tie deterministically.
        # id first, then the three name-shaped rules in descending strength,
        # then token overlap. The v1 rules keep their relative order, so a
        # block that nominates none of the v2 rules sorts exactly as before.
        rank = {
            "id": 0, "name": 1, "acronym": 2, "cross_slot": 3, "token": 4,
        }[self.rule]
        return (rank, -self.score, self.a, self.b)


def _ids_converge(x: CandidateUnit, y: CandidateUnit) -> bool:
    return bool(
        (x.lei_id and x.lei_id == y.lei_id)
        or (x.ror_id and x.ror_id == y.ror_id)
    )


#: Words dropped before taking initials: "University of Texas" initialises to
#: UT, not UOT, and every acronym in the wild agrees.
_ACRONYM_STOPWORDS = frozenset({"of", "the", "and", "for", "&"})

#: An acronym is a short string. Six characters is "UTSWMC" — past that the
#: initials of a long name start matching short names by accident.
ACRONYM_MAX_LEN = 6
ACRONYM_THRESHOLD = 0.8
CROSS_SLOT_THRESHOLD = 0.85


def _initials(name: str) -> str:
    """"University of Texas" → "ut"."""
    return "".join(
        token[0]
        for token in strip_legal_suffix(name).split()
        if token and token not in _ACRONYM_STOPWORDS
    )


def _acronym_score(x: CandidateUnit, y: CandidateUnit) -> float:
    """How well either name's initials match the other, short, name.

    Directional on purpose, then taken both ways: "GES Inc" is the acronym of
    "Global Equipment Services", and only the short side is allowed to BE the
    acronym — otherwise every pair of short names in a block matches every
    other on three letters of noise.
    """
    best = 0.0
    for long_unit, short_unit in ((x, y), (y, x)):
        short = strip_legal_suffix(short_unit.name)
        if not short or len(short) > ACRONYM_MAX_LEN:
            continue
        initials = _initials(long_unit.name)
        if len(initials) < 2:
            continue
        best = max(best, JaroWinkler.similarity(initials, short))
    return best


def _cross_slot_score(x: CandidateUnit, y: CandidateUnit) -> float:
    """The best match between one side's OTHER names and the other's institution.

    A trading name, an operating name and Phase 1's suggested name all name the
    same organisation as the institution beside them, and any of the three can
    be the only spelling the two records have in common.
    """
    best = 0.0
    for source, target in ((x, y), (y, x)):
        others = [*source.aliases, source.operating_name, source.suggested_name]
        target_name = strip_legal_suffix(target.name)
        if not target_name:
            continue
        for other in others:
            stripped = strip_legal_suffix(other) if other else ""
            if stripped:
                best = max(best, JaroWinkler.similarity(stripped, target_name))
    return best


def nominate(
    x: CandidateUnit,
    y: CandidateUnit,
    *,
    name_threshold: float,
    token_threshold: float,
    extra_rules: bool = False,
) -> Optional[Candidate]:
    """Nominate the pair (x, y) if any rule fires, else None.

    Priority when several fire: id-convergence > name similarity > acronym >
    cross-slot > token overlap (a merge is never implied — this only picks the
    LLM candidate).

    ``extra_rules`` turns on the two v2 rules. They are off for small blocks
    because a small block's signatures are all compared in one Mode A partition
    call anyway: nominating them again would buy a second opinion on a question
    already asked, at one LLM call each. In a Mode B block nothing guarantees
    that, which is where an acronym or an alias is the only bridge left.
    """
    a, b = (x, y) if x.index < y.index else (y, x)

    if _ids_converge(x, y):
        return Candidate(a.index, b.index, "id", 1.0)

    sa, sb = strip_legal_suffix(x.name), strip_legal_suffix(y.name)
    jw = JaroWinkler.similarity(sa, sb) if sa and sb else 0.0
    if jw >= name_threshold:
        return Candidate(a.index, b.index, "name", jw)

    if extra_rules:
        acronym = _acronym_score(x, y)
        if acronym >= ACRONYM_THRESHOLD:
            return Candidate(a.index, b.index, "acronym", acronym)
        cross = _cross_slot_score(x, y)
        if cross >= CROSS_SLOT_THRESHOLD:
            return Candidate(a.index, b.index, "cross_slot", cross)

    jac = _jaccard(sa.split(), sb.split())
    if jac >= token_threshold:
        return Candidate(a.index, b.index, "token", jac)

    return None


def _eligible(
    x: CandidateUnit,
    y: CandidateUnit,
    address_gate: Optional[Callable[[CandidateUnit, CandidateUnit], bool]] = None,
) -> bool:
    """A residue pair worth considering: one the bucketed adjudication skipped.

    Same-``has_name2`` pairs where BOTH units already went through the LLM were
    compared in Mode A/B — no need to re-adjudicate. Everything else (across the
    Name-2 boundary, or involving an un-adjudicated singleton) is residue.

    ``address_gate`` (v2, ``DEDUP_V2_BLOCKING``) drops pairs whose delivery
    points are incompatible. Blocking on zip+house lets a city-key union bring
    two doors into one block on purpose, to survive a zip typo; this is the
    check that stops that widening from also becoming a licence to merge them.
    It filters rather than replaces the same-bucket test above, which is not an
    address question at all — it says the pair was already put to the model
    once, and asking again would buy nothing but a second bill.
    """
    if address_gate is not None and not address_gate(x, y):
        return False
    if x.has_name2 == y.has_name2 and x.adjudicated and y.adjudicated:
        return False
    return True


def generate_candidate_pairs(
    units: Sequence[CandidateUnit],
    *,
    name_threshold: float,
    token_threshold: float,
    address_gate: Optional[Callable[[CandidateUnit, CandidateUnit], bool]] = None,
    extra_rules: bool = False,
) -> List[Candidate]:
    """All nominated residue pairs, deterministically ordered (priority first).

    O(n^2) over units — cheap string ops only; the LLM-call cap is applied by
    the caller against this ordered list, so id-convergence pairs are retained
    before name/token pairs when the cap trips.
    """
    candidates: List[Candidate] = []
    n = len(units)
    for i in range(n):
        for j in range(i + 1, n):
            x, y = units[i], units[j]
            if not _eligible(x, y, address_gate):
                continue
            cand = nominate(
                x, y, name_threshold=name_threshold, token_threshold=token_threshold,
                extra_rules=extra_rules,
            )
            if cand is not None:
                candidates.append(cand)
    candidates.sort(key=lambda c: c.sort_key)
    return candidates
