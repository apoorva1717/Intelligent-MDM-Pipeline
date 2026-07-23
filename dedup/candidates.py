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
from typing import List, Optional, Sequence, Tuple

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
        rank = {"id": 0, "name": 1, "token": 2}[self.rule]
        return (rank, -self.score, self.a, self.b)


def _ids_converge(x: CandidateUnit, y: CandidateUnit) -> bool:
    return bool(
        (x.lei_id and x.lei_id == y.lei_id)
        or (x.ror_id and x.ror_id == y.ror_id)
    )


def nominate(
    x: CandidateUnit,
    y: CandidateUnit,
    *,
    name_threshold: float,
    token_threshold: float,
) -> Optional[Candidate]:
    """Nominate the pair (x, y) if any rule fires, else None.

    Priority when several fire: id-convergence > name similarity > token
    overlap (a merge is never implied — this only picks the LLM candidate).
    """
    a, b = (x, y) if x.index < y.index else (y, x)

    if _ids_converge(x, y):
        return Candidate(a.index, b.index, "id", 1.0)

    sa, sb = strip_legal_suffix(x.name), strip_legal_suffix(y.name)
    jw = JaroWinkler.similarity(sa, sb) if sa and sb else 0.0
    if jw >= name_threshold:
        return Candidate(a.index, b.index, "name", jw)

    jac = _jaccard(sa.split(), sb.split())
    if jac >= token_threshold:
        return Candidate(a.index, b.index, "token", jac)

    return None


def _eligible(x: CandidateUnit, y: CandidateUnit) -> bool:
    """A residue pair worth considering: one the bucketed adjudication skipped.

    Same-``has_name2`` pairs where BOTH units already went through the LLM were
    compared in Mode A/B — no need to re-adjudicate. Everything else (across the
    Name-2 boundary, or involving an un-adjudicated singleton) is residue.
    """
    if x.has_name2 == y.has_name2 and x.adjudicated and y.adjudicated:
        return False
    return True


def generate_candidate_pairs(
    units: Sequence[CandidateUnit],
    *,
    name_threshold: float,
    token_threshold: float,
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
            if not _eligible(x, y):
                continue
            cand = nominate(
                x, y, name_threshold=name_threshold, token_threshold=token_threshold
            )
            if cand is not None:
                candidates.append(cand)
    candidates.sort(key=lambda c: c.sort_key)
    return candidates
