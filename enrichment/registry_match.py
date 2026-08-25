"""Fix C — the rules that decide WHICH registry candidate wins, and whether any does.

Three properties, all of them shared by ROR and GLEIF so the two registries
cannot drift apart on the question:

**A total order.** Both clients used to pick their winner with a stable sort on
score alone, or with ``max()``, which returns the first maximum. Both inherit
the API's own response order for every tie — and response order is not stable
between runs. On the chemspeed batch that produced a record that matched a
*different* registry entity on each of two runs of the identical code.
:func:`rank_key` gives one total order: score descending, then the candidate's
canonical registry id ascending. The id is arbitrary as a preference and
perfectly stable as a tiebreak, which is exactly what is wanted.

**A near-tie is a no-match.** Ordering a tie deterministically only makes the
wrong answer reproducible. When the top two candidates are within
:data:`REGISTRY_AMBIGUITY_MARGIN` the evidence has not distinguished them, and
:func:`ambiguity_verdict` refuses both — the same rule the Wikidata lane
already applies when two items survive its gauntlet, and for the same reason:
choosing on the higher fuzzy score is a tiebreak the evidence has not earned.

**A short name needs a second signal.** "BHS" and "BIC" are three characters.
Registry name matching at any threshold is collision-prone on strings that
short — "BHS" fuzzy-matches Berkshire Health Systems and Behavioral Health
Systems equally well, and the chemspeed batch oscillated between them across
runs. :func:`is_collision_prone` marks such names, and a match on one is
accepted only when a *second, independent* signal agrees: the registry's
registered locality matches the record's city/state, or the candidate's own
website is the domain the record already carries. Otherwise: no match. Nothing
is expanded, guessed, or accepted at a lower bar.
"""

from __future__ import annotations

import re
from typing import Any

from enrichment.locality import CONSISTENT

#: How close two candidate scores have to be before the registry is treated as
#: having failed to identify one organisation. On the 0-100 rapidfuzz scale
#: that both registries' name guards use (ROR's local rescore is 0-1 and is
#: converted by :func:`scaled_margin`).
#:
#: DERIVATION. The margin has to be wide enough to cover the difference a
#: single character makes and no wider. ``token_sort_ratio`` is a normalised
#: edit distance: on the 15-30 character legal names these registries return, a
#: one-character difference between two candidates moves the score by roughly
#: 2 points (2/(2*20) * 100 ≈ 2.5 for a 20-character pair). Two candidates that
#: close are separated by a punctuation or spelling variant in the registry's
#: own record, not by evidence about the organisation — and which of them ranks
#: higher can flip when the registry re-indexes. Above ~5 the margin starts
#: refusing pairs that a whole distinguishing token separates ("Analytical
#: Sales" vs "Analytical Sales and Services" differ by far more than that), so
#: it would be refusing matches the evidence *does* distinguish, which this fix
#: is explicitly not allowed to do in reverse either. 2.0 is the smallest value
#: that covers the one-character case.
#:
#: ONE constant, deliberately: the Wikidata lane has no numeric margin (its
#: ambiguity rule is "more than one candidate survived the gauntlet", which
#: needs no threshold), so there was none to reuse and there is now exactly one
#: to reuse from.
REGISTRY_AMBIGUITY_MARGIN: float = 2.0

#: Legal-form tokens dropped before a name is measured for collision-proneness.
#: "BIC Corp" is "BIC" for this purpose. Imported rather than re-listed would
#: be better, but ``tier1_lei`` imports nothing from here and this module must
#: import nothing from it (``tier1_lei`` uses these rules), so the set is
#: derived from that one at import time.
def _legal_form_tokens() -> frozenset[str]:
    from enrichment.tier1_lei import _LEGAL_FORM_TOKENS

    return frozenset(_LEGAL_FORM_TOKENS)


_TOKEN_RE = re.compile(r"[A-Za-z0-9&]+")

#: A single all-caps token this long or shorter reads as an acronym rather than
#: as a word ("BHS", "NABCO"). Longer all-caps single tokens are distinctive
#: enough that a same-country registry collision is not the expected case, and
#: firing on them would catch every SAP record whose name is simply typed in
#: capitals.
_ACRONYM_MAX_LEN = 5

#: A name whose significant characters number this or fewer is collision-prone
#: whatever its case.
_SHORT_NAME_MAX_LEN = 4


def name_core(name: str | None) -> list[str]:
    """*name*'s significant tokens — legal forms dropped, case preserved."""
    legal = _legal_form_tokens()
    return [
        t for t in _TOKEN_RE.findall(name or "")
        if t.lower() not in legal
    ]


def is_collision_prone(name: str | None) -> bool:
    """True when a registry name match on *name* needs a corroborating signal.

    Two shapes qualify, both measured after legal forms are dropped:

    * **four significant characters or fewer**, in any case — "BHS", "BIC",
      "3M". There is not enough string there for a name match to identify one
      organisation among a registry's millions;
    * **a single all-caps token of five characters or fewer** — the shape of an
      acronym. Restricted to a single token on purpose: a great many SAP
      records carry their whole name in capitals ("LARGO MEDICAL CTR"), and
      treating every one of those as an acronym would apply the guard to most
      of the batch.

    A name that is neither is left alone. This function widens no guard and
    narrows no threshold; it only decides which matches must show a second
    signal before they are accepted.
    """
    tokens = name_core(name)
    if not tokens:
        return False
    if sum(len(t) for t in tokens) <= _SHORT_NAME_MAX_LEN:
        return True
    return (
        len(tokens) == 1
        and tokens[0].isupper()
        and len(tokens[0]) <= _ACRONYM_MAX_LEN
    )


def scaled_margin(scale_max: float = 100.0) -> float:
    """:data:`REGISTRY_AMBIGUITY_MARGIN` on a 0-*scale_max* scale.

    ROR's local rescore is 0-1 and GLEIF's guard is 0-100; the margin means the
    same proportion of the scale on both.
    """
    return REGISTRY_AMBIGUITY_MARGIN * (scale_max / 100.0)


def rank_key(score: float, candidate_id: str | None, *prefix: Any) -> tuple:
    """Fix C(1)'s total order — sort ASCENDING and the winner is first.

    ``(*prefix, -score, candidate_id)``. *prefix* holds any stronger
    discriminator the caller ranks on first, already in ascending-comparison
    form (ROR passes ``-exact_match`` so an exact display-name match sorts
    ahead). The canonical registry id is always last, so the order is total and
    never falls back to the order the API answered in.
    """
    return (*prefix, -float(score), (candidate_id or ""))


def ambiguity_verdict(
    scores: list[float], *, scale_max: float = 100.0,
) -> bool:
    """True when the top two *scores* are too close to have identified one.

    Fewer than two candidates is never ambiguous — there is nothing to confuse
    the winner with.
    """
    if len(scores) < 2:
        return False
    ordered = sorted(scores, reverse=True)
    return (ordered[0] - ordered[1]) < scaled_margin(scale_max)


def second_signal(
    *,
    location_verdict: str | None,
    candidate_domain: str | None,
    record_domain: str | None,
) -> str | None:
    """The corroborating signal for a collision-prone name, or None.

    Exactly the two Fix C(3) allows: the locality comparator agreeing (a
    *neutral* verdict is not agreement — silence is not evidence, which is the
    rule the whole locality comparator is built on), or the candidate's own
    website being the domain the record already carries.
    """
    if location_verdict == CONSISTENT:
        return "location"
    def _host(value: str | None) -> str:
        host = (value or "").strip().lower().rstrip("/")
        return host[4:] if host.startswith("www.") else host

    cand, rec = _host(candidate_domain), _host(record_domain)
    if cand and rec and cand == rec:
        return "domain"
    return None


# ── How strong was the name match? ────────────────────────────────────────
#
# `registry-location-mismatch` fires on a contradicted locality, and on its own
# that is a bad trigger: on the chemspeed batch it flagged Arkema Inc. (the
# record names a plant in NC, the registry the head office in PA) exactly as
# loudly as it flagged a fuzzy match to a different organisation. The two are
# not the same finding. A registry entity whose name the record states
# VERBATIM has been identified by the name; a contradicted address on top of
# that is a fact about the organisation's geography, not a doubt about which
# organisation it is. A match reached any weaker way has no such anchor, and
# there the address disagreeing is the second signal that the match is wrong —
# which is what the flag is for.
#
# So the flag needs the strength of the name match, and these four tiers name
# it. Only :data:`EXACT_TIER` suppresses the advisory.

#: The record states the registry's name, verbatim (modulo case, punctuation
#: and a legal form the record omits). Nothing weaker qualifies.
EXACT_TIER = "exact"

#: A scored match: the guard's threshold was cleared, the strings differ.
FUZZY_TIER = "fuzzy"

#: The name is collision-prone (:func:`is_collision_prone`) — an acronym or a
#: four-character string. Below exact tier however well it scored: the string
#: is too short for verbatim equality to identify one organisation, which is
#: the whole premise of Fix C(3).
SHORT_NAME_TIER = "short_name"

#: Routed by identifier rather than by name — the Wikidata crosswalk following
#: a P6782/P1278 pointer. There was no name comparison to be exact at, and a
#: stale pointer is precisely how a record acquires another organisation's
#: registry entry, so this can never be exact tier.
CROSSWALK_TIER = "crosswalk"


def _legal_forms(name: str | None) -> frozenset[str]:
    """The legal-form tokens present in *name*, lowercased."""
    legal = _legal_form_tokens()
    return frozenset(
        t.lower() for t in _TOKEN_RE.findall(name or "") if t.lower() in legal
    )


def names_match_verbatim(a: str | None, b: str | None) -> bool:
    """True when *a* and *b* are one name written two ways.

    Two things are forgiven, and only two:

    * **case and punctuation** — GLEIF returns "ADVANSIX INC." and the record
      says "AdvanSix Inc."; those are the same string typed twice;
    * **a legal form one side omits** — "Arkema" against "ARKEMA INC.". The
      legal form is the register's suffix, not a distinguishing token, which is
      the same licence :func:`name_core` and ``_name_match_score`` already
      operate under.

    Two DIFFERENT legal forms are not forgiven: "Smith Inc" and "Smith LLC" are
    two legal entities and a register is the authority that says so.
    """
    core_a = [t.lower() for t in name_core(a)]
    core_b = [t.lower() for t in name_core(b)]
    if not core_a or core_a != core_b:
        return False
    forms_a, forms_b = _legal_forms(a), _legal_forms(b)
    return not forms_a or not forms_b or forms_a == forms_b


def name_match_tier(
    queries: list[str | None],
    candidates: list[str | None],
    *,
    crosswalk: bool = False,
) -> str:
    """Which tier the accepted match was reached at.

    *queries* are the names the record was searched under, *candidates* every
    name variant the registry publishes for the entity it returned. The tiers
    are tested in the order that makes the weakest claim win: a crosswalk has
    no name comparison at all, a collision-prone name cannot be exact however
    it compares, and only then does verbatim equality count.
    """
    if crosswalk:
        return CROSSWALK_TIER
    if any(is_collision_prone(q) for q in queries if q):
        return SHORT_NAME_TIER
    for query in queries:
        if not query:
            continue
        for candidate in candidates:
            if candidate and names_match_verbatim(query, candidate):
                return EXACT_TIER
    return FUZZY_TIER


def is_exact_tier(tier: str | None) -> bool:
    """True only for :data:`EXACT_TIER`. A missing tier is not exact."""
    return tier == EXACT_TIER
