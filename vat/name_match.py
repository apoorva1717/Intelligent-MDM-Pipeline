"""VAT_Name_Match: does the name VIES holds for the number match the export's?

Deterministic, no LLM. Compares ``VAT_VIES_Name`` with the record's ``Name``
alone and with ``Name`` + ``Name 2``–``Name 4`` joined, and keeps the better
outcome. Both sides are normalised the same way:

1. German transliteration (ä→ae, ö→oe, ü→ue, ß→ss), so ``Universität`` and
   VIES's ``UNIVERSITAET`` agree;
2. :func:`dedup.signatures.normalize_key` — accents folded, lowercase,
   punctuation to spaces;
3. runs of single letters re-joined, so the dotted forms ``S.à r.l.`` and
   ``S.C.A.`` become the tokens ``sarl`` / ``sca``;
4. legal-form tokens dropped wherever they stand (VIES writes
   ``SA SANOFI …`` with the form in front): the GLEIF name guard's list
   (:data:`enrichment.tier1_lei._LEGAL_FORM_TOKENS`), the dedup suffix list
   (:data:`dedup.candidates.LEGAL_SUFFIXES`), and the few EU forms neither
   carries (:data:`_EXTRA_LEGAL_FORMS`).

States, best first:

=============  ==============================================================
MATCH          same token set (word order ignored)
PARTIAL        one side's tokens contain the other's, or token_sort_ratio ≥ 88
MISMATCH       neither
NOT_AVAILABLE  VIES status is not VALID, VIES did not disclose a name (DE, ES),
               or the row has no name
=============  ==============================================================

The rule that decided is written to ``VAT_Flags`` as ``NAME_MATCH:<rule>``.
88 is ``LEI_NAME_MATCH_THRESHOLD``'s value, inherited for the same reason the
page corroborator inherits it: this too is a supplied-name-vs-registered-name
comparison. PARTIAL and MISMATCH are prompts for a steward, never a
correction — nothing here rewrites a name.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from dedup.candidates import LEGAL_SUFFIXES
from dedup.signatures import normalize_key
from enrichment.tier1_lei import _LEGAL_FORM_TOKENS

MATCH = "MATCH"
PARTIAL = "PARTIAL"
MISMATCH = "MISMATCH"
NOT_AVAILABLE = "NOT_AVAILABLE"

#: token_sort_ratio (0–100) at or above which two names are PARTIAL.
FUZZY_THRESHOLD = 88.0

# ── Rules (VAT_Flags: "NAME_MATCH:<rule>") ───────────────────────────────────
EXACT = "EXACT"                      # Name alone, same token set
EXACT_WITH_NAME_2_4 = "EXACT_WITH_NAME_2_4"  # Name + Name 2–4 joined, same token set
TOKEN_SUBSET = "TOKEN_SUBSET"        # one side's tokens ⊆ the other's
FUZZY = "FUZZY"                      # token_sort_ratio ≥ FUZZY_THRESHOLD
NO_MATCH = "NO_MATCH"                # MISMATCH
VIES_NOT_VALID = "VIES_NOT_VALID"    # NOT_AVAILABLE: VIES status not VALID
NOT_DISCLOSED = "NOT_DISCLOSED"      # NOT_AVAILABLE: VIES returned "---"
NO_INPUT_NAME = "NO_INPUT_NAME"      # NOT_AVAILABLE: Name … Name 4 all blank

#: EU legal forms in neither shared list (lowercase, single token after step 3).
_EXTRA_LEGAL_FORMS = frozenset({
    "sca", "scs", "sl", "slu", "sau", "ug", "eg", "bvba", "sprl", "sro",
})

_LEGAL_TOKENS: frozenset[str] = frozenset(
    _LEGAL_FORM_TOKENS
    | {normalize_key(s).replace(" ", "") for s in LEGAL_SUFFIXES}
    | _EXTRA_LEGAL_FORMS
)

_TRANSLITERATE = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
})


@dataclass(frozen=True)
class NameMatch:
    status: str
    rule: str
    score: float | None = None

    @property
    def flag(self) -> str:
        return f"NAME_MATCH:{self.rule}"


def name_tokens(name: str | None) -> list[str]:
    """Normalised comparison tokens for *name* (see module docstring)."""
    words = normalize_key((name or "").translate(_TRANSLITERATE)).split()
    joined: list[str] = []
    run = ""
    for word in [*words, ""]:
        if len(word) == 1:
            run += word
            continue
        if run:
            joined.append(run)
            run = ""
        if word:
            joined.append(word)
    kept = [t for t in joined if t not in _LEGAL_TOKENS]
    # A name that is nothing but a legal form still compares as something.
    return kept or joined


def _compare(vies: list[str], ours: list[str]) -> tuple[str, str, float]:
    a, b = set(vies), set(ours)
    score = fuzz.token_sort_ratio(" ".join(vies), " ".join(ours))
    if a == b:
        return MATCH, EXACT, score
    if a <= b or b <= a:
        return PARTIAL, TOKEN_SUBSET, score
    if score >= FUZZY_THRESHOLD:
        return PARTIAL, FUZZY, score
    return MISMATCH, NO_MATCH, score


_RANK = {MATCH: 0, PARTIAL: 1, MISMATCH: 2}


def match_name(
    vies_status: str | None,
    vies_name: str | None,
    name: str | None,
    extra_names: list[str | None] = (),
) -> NameMatch:
    """Compare VIES's name with ``Name`` and with ``Name`` + ``Name 2``–``4``."""
    if vies_status != "VALID":
        return NameMatch(NOT_AVAILABLE, VIES_NOT_VALID)
    vies = name_tokens(vies_name)
    if not vies_name or not vies:
        return NameMatch(NOT_AVAILABLE, NOT_DISCLOSED)

    parts = [p for p in [name, *extra_names] if p and str(p).strip()]
    if not parts:
        return NameMatch(NOT_AVAILABLE, NO_INPUT_NAME)

    candidates: list[tuple[str, str, float]] = []
    if name and str(name).strip():
        candidates.append(_compare(vies, name_tokens(name)))
    if len(parts) > 1 or not candidates:
        status, rule, score = _compare(vies, name_tokens(" ".join(map(str, parts))))
        candidates.append((status, EXACT_WITH_NAME_2_4 if rule == EXACT else rule, score))
    status, rule, score = min(candidates, key=lambda c: (_RANK[c[0]], -c[2]))
    return NameMatch(status, rule, round(score, 1))
