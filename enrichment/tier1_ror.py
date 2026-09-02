"""Tier 1: ROR v2 hybrid lookup — affiliation-first, query-fallback.

Primary strategy: use the ``?affiliation=`` parameter which handles
abbreviations (e.g. "Univ of Florida" → "University of Florida"),
misspellings, and real-world name variants.  It returns scored results
with a ``chosen`` flag.

Fallback: if the affiliation endpoint yields no confident match, retry
with the ``?query=`` parameter plus an optional country filter.  The
query endpoint uses Elasticsearch full-text search and works well for
exact/full names combined with geographic filtering.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from rapidfuzz import fuzz

from enrichment.locality import US_REGION_CODES, compare_registry_addresses
from enrichment.registry_match import (
    CROSSWALK_TIER,
    ambiguity_verdict,
    is_collision_prone,
    name_match_tier,
    rank_key,
    second_signal,
)
from llm.openai_client import resolve_tls_verify
from utils.cache import (
    CacheKey,
    RegistryUnavailableFrozen,
    cached_registry_get,
    legacy_lookup_key,
    lookup_key,
)
from utils.text_utils import (
    _fuzzy_token_covers,
    acronym_matches_name,
    expand_abbreviations,
    extract_domain,
    strip_parentheticals,
)

logger = logging.getLogger(__name__)

# Classification derived from ROR org types, not keyword matching.
ROR_RESEARCH_TYPES = {
    "education", "healthcare", "government",
    "facility", "nonprofit", "archive", "other",
}

# Module-level cache — the ONE cache ROR lookups actually consult. (BatchCache
# in utils/cache.py used to expose a get_ror/set_ror pair that nothing ever
# called; it has been removed.) Keyed by
# ``utils.cache.lookup_key(name, country_code)``: the name normalised via
# dedup.signatures.normalize_key (lowercase, trim, collapse whitespace, strip
# punctuation, fold accents) plus the country filter, so "Coastal Diagnostics,
# Inc." and "Coastal Diagnostics Inc" resolve to one entry and one API call.
#
# The key is a dictionary key and nothing else: ``name`` — unnormalised — is
# what is sent to ROR below and what every scoring path sees.
_ror_cache: "dict[CacheKey, dict[str, Any]]" = {}
# Legacy lowercase-only keys already queried, and the count of lookups the
# normalised key served that the legacy key would have missed. Telemetry only.
_ror_legacy_seen: "set[CacheKey]" = set()
_ror_normalised_hits = 0


def clear_ror_cache() -> None:
    """Reset the module-level ROR cache (per batch / between test runs)."""
    global _ror_normalised_hits
    _ror_cache.clear()
    _ror_legacy_seen.clear()
    _ror_normalised_hits = 0


def ror_normalised_hits() -> int:
    """Lookups the normalised cache key saved that lowercasing would not."""
    return _ror_normalised_hits


# Institution acronyms that ROR does NOT carry as an alias, so a bare-acronym
# query ("HFT Stuttgart") returns unrelated same-city orgs instead of the
# institution. Mapped to the full institution name and used ONLY to build a
# fallback ROR affiliation request — kept ROR-local (not in the global
# expand_abbreviations map) so it never affects search terms or output names.
# Extend with further institution acronyms as they come up.
_INSTITUTION_ACRONYMS: dict[str, str] = {
    "hft": "Hochschule für Technik",
    # Multi-token key. "GA Tech" must NOT go through the bounded two-letter
    # postal expansion below: that yields "Georgia Tech", which ROR resolves
    # to "Georgia Tech Foundation" (ror.org/00adhzq59) — a different legal
    # entity from the university (ror.org/01zkghx44). Mapping the whole
    # phrase to the full official name resolves the institute directly.
    "ga tech": "Georgia Institute of Technology",
}

# Built from the map's keys so only KNOWN acronyms can match. Multi-token keys
# are matched with flexible whitespace; longest key first so a phrase key wins
# over a single-token key that is a prefix of it.
_INSTITUTION_ACRONYM_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(k).replace(r"\ ", r"\s+")
        for k in sorted(_INSTITUTION_ACRONYMS, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)


def _expand_institution_acronyms(name: str) -> str:
    """Replace known institution acronyms in *name* with their full form.

    Token-scoped and case-insensitive: "HFT Stuttgart" → "Hochschule für
    Technik Stuttgart", "GA Tech" → "Georgia Institute of Technology".
    Unknown tokens are left untouched.
    """
    def _sub(m: "re.Match[str]") -> str:
        key = _WS_RE.sub(" ", m.group(1).lower()).strip()
        return _INSTITUTION_ACRONYMS.get(key, m.group(0))
    return _INSTITUTION_ACRONYM_RE.sub(_sub, name)


# US state-name abbreviations (traditional newspaper forms) → full state name.
# ROR-LOCAL only — applied when building the ROR query so "Fla State Univ"
# resolves to "Florida State University" (not "Kent State University", whose
# only shared tokens are the generic "State"/"University"). Kept out of the
# global expand_abbreviations map so it never touches output names or search
# terms. Two-letter postal codes are intentionally excluded (too collision-prone).
_US_STATE_ABBREVS: dict[str, str] = {
    "fla": "Florida", "calif": "California", "ariz": "Arizona",
    "colo": "Colorado", "conn": "Connecticut", "tenn": "Tennessee",
    "wisc": "Wisconsin", "minn": "Minnesota", "okla": "Oklahoma",
    "nebr": "Nebraska", "mich": "Michigan", "tex": "Texas",
    "wash": "Washington", "penn": "Pennsylvania", "ill": "Illinois",
    "ind": "Indiana", "mass": "Massachusetts", "miss": "Mississippi",
    "ore": "Oregon", "kan": "Kansas", "ark": "Arkansas", "ala": "Alabama",
}
_US_STATE_ABBREV_RE = re.compile(r"\b([A-Za-z]{3,5})\b\.?")

# ── Bounded two-letter postal codes ──────────────────────────────────────
# Two-letter postal codes stay OUT of the map above: on their own "IN", "OR",
# "ME" and "OK" are ordinary words, so expanding them wholesale is exactly the
# collision README warns about. They are expanded ONLY when the tokens that
# immediately follow put the code beyond doubt — "FL State Univ" can only mean
# Florida State University. Everything else ("IN Laboratories", "OR
# Diagnostics") is left untouched.
# The map itself now lives in `enrichment.locality` — Fix D(2) applies the
# same US-region normalisation to registry localities as the page read
# does, and one map serving both is the point. Re-exported under the
# historical private name so the structural test that pins "ROR-local
# expansion maps never touch an output name" keeps testing this object.
_US_POSTAL_CODES: dict[str, str] = US_REGION_CODES

# The closed set of following contexts. Nothing outside these four fires.
_TWO_LETTER_CONTEXT = (
    r"State\s+Univ(?:ersity)?|Institute\s+of\s+Technology|Tech"
)
_TWO_LETTER_STATE_RE = re.compile(
    rf"\b([A-Za-z]{{2}})\b(?=\s+({_TWO_LETTER_CONTEXT})\b)",
    re.IGNORECASE,
)

# Codes that are also ordinary English words. Safe before "State Univ…" —
# "Hi State University" names nothing — but not before the bare "Tech"
# contexts, where "Hi Tech" / "In Tech" are real company names.
_WORDLIKE_POSTAL_CODES = {"hi", "in", "or", "ok", "me", "la", "de"}


def _expand_state_abbrevs(name: str) -> str:
    """Expand a US state-name abbreviation token to its full form
    ("Fla State Univ" → "Florida State Univ"). ROR-local; token-scoped.

    Two-letter postal codes are expanded only inside the bounded contexts of
    :data:`_TWO_LETTER_CONTEXT` ("FL State Univ" → "Florida State Univ").
    """
    def _sub(m: "re.Match[str]") -> str:
        return _US_STATE_ABBREVS.get(m.group(1).lower(), m.group(0))

    def _sub_two(m: "re.Match[str]") -> str:
        code = m.group(1).lower()
        context = _WS_RE.sub(" ", m.group(2).lower()).strip()
        # A phrase the institution-acronym map owns is left for its retry,
        # which expands to the exact official name ("GA Tech" → "Georgia
        # Institute of Technology", never the ambiguous "Georgia Tech").
        if f"{code} {context}" in _INSTITUTION_ACRONYMS:
            return m.group(0)
        if not context.startswith("state") and code in _WORDLIKE_POSTAL_CODES:
            return m.group(0)
        return _US_POSTAL_CODES.get(code, m.group(0))

    expanded = _TWO_LETTER_STATE_RE.sub(_sub_two, name)
    return _US_STATE_ABBREV_RE.sub(_sub, expanded)


_DASH_RE = re.compile(r"[\u2010-\u2015\-]+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_PUNCT_RE = re.compile(r"[.,]")
_WS_RE = re.compile(r"\s+")

# Identifier extraction tokenises differently from `_WORD_RE`: a token may
# START with a digit as long as it contains a letter, so "3M" survives as one
# token. `_WORD_RE` requires a leading letter and would yield "M" — a
# single character, below the acronym floor — which made the one token that
# says WHICH company invisible to the guard below. A pure number ("2020",
# "3") has no letter and is not an identifier.
_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*")

# US legal-entity suffixes: long form (and dotted abbreviations, once the
# dots have been stripped to spaces) \u2192 canonical abbreviation. Applied
# symmetrically to the query and every ROR name variant so that
# "Acme Corporation", "Acme Corp." and "Acme Corp" all compare equal.
# Order matters: multi-word phrases must run before their constituent
# single words ("limited liability company" before "limited"/"company").
_LEGAL_SUFFIX_SUBS = [
    (re.compile(r"\blimited liability company\b"), "llc"),
    (re.compile(r"\blimited liability partnership\b"), "llp"),
    (re.compile(r"\bl l c\b"), "llc"),   # from "L.L.C." after dot removal
    (re.compile(r"\bl l p\b"), "llp"),
    (re.compile(r"\bincorporated\b"), "inc"),
    (re.compile(r"\bcorporation\b"), "corp"),
    (re.compile(r"\bcompany\b"), "co"),
    (re.compile(r"\blimited\b"), "ltd"),
]


def _extract_identifier_tokens(text: str) -> set[str]:
    """Short all-caps tokens that act as distinguishing identifiers.

    Acronyms like 'EMSL', 'ASL', 'NASA', 'IBM' (length 2-5, originally
    all-uppercase) are the part of a name that separates otherwise
    similar organisations \u2014 'EMSL Analytical' vs 'ASL Analytical'
    share the descriptive 'Analytical' suffix and differ only by their
    leading acronym. Token-sort fuzz weights the long shared word
    heavily and produces ~0.9 similarity, crossing the match
    threshold; treating these acronyms as required tokens catches the
    mismatch. Returned tokens are lowercased.

    A digit-carrying mark ('3M', 'P66') counts too. It is an identifier by
    exactly the same argument — in "3M Corporate" the only token that says
    WHICH company is "3M", and without it the query subset-matches any org
    with "Corporate" in its name (ROR returns "Corporate Executive Board" and
    "Corporate Communications Group" for it, both at a false 1.0).
    """
    tokens: set[str] = set()
    for tok in _IDENTIFIER_TOKEN_RE.findall(text):
        if 2 <= len(tok) <= 5 and tok.isupper():
            tokens.add(tok.lower())
    return tokens


def _has_case_contrast(text: str) -> bool:
    """True when capitalisation in *text* carries information.

    An upper-case token only *stands out* as an acronym if something around
    it is not upper case. "HFT Stuttgart" has that contrast; "BRIGHAM &
    WOMENS HOSP" does not — there, upper case is the storage convention of
    the whole field, not a statement about any one token.
    """
    return any(c.islower() for c in text)


def _guard_identifier_tokens(text: str) -> set[str]:
    """Query acronyms that may *block* a match (the identifier-token guard).

    Same extraction as `_extract_identifier_tokens`, but gated on case
    CONTRAST rather than on upper case alone.

    SAP master data is frequently stored entirely in upper case. In a query
    like "BRIGHAM & WOMENS HOSP" *every* short token is an all-caps token, so
    an upper-case-only test reads ordinary words as acronyms and demands each
    appear literally in the candidate. "HOSP" does not appear in "Brigham and
    Women's Hospital", so the exact/subset/substring shortcuts were blocked
    and the fuzzy branch — raw ratio 0.82, comfortably over the 0.8
    threshold — was capped to 0.7 and rejected. The organisation is in ROR
    (`ror.org/04b6nzv94`) and ROR's own affiliation scorer returned it at
    1.0; only the local guard stood in the way. That is systemic across every
    all-caps input, not a property of this one record.

    When the query has no case contrast the casing carries no signal, so the
    guard does not fire and scoring falls through to the other branches. The
    distinctive-token guard and the country guard are untouched and still
    apply, on all-caps queries as much as any other.

    The guard stays fully active for mixed-case queries, which is the signal
    it was designed for: "HFT Stuttgart" must still be kept from
    subset-matching Marienhospital Stuttgart on the shared city token.

    `_initialism_score` deliberately keeps using `_extract_identifier_tokens`
    instead: it can only *raise* a score, never cap one, so an all-caps
    "JAH VA HOSPITAL" should still be able to reach James A. Haley.
    """
    if not _has_case_contrast(text):
        return set()
    return _extract_identifier_tokens(text)


def _extract_location_tokens(*parts: str | None) -> set[str]:
    """Significant (>=4 char) tokens drawn from the address context
    (city / state / country).

    These identify *where* an org is, never *which* org it is, so they must
    not on their own justify a perfect name match. Without this, a query
    whose only significant name token is the city — e.g. "Uni Stuttgart",
    where the 3-char "Uni" is dropped as insignificant, leaving only
    "stuttgart" — subset-matches EVERY same-city org ("Marienhospital
    Stuttgart", "Klinikum Stuttgart", …) and returns a false 1.0. The
    winner among the tied same-city orgs is then arbitrary/ROR-order
    dependent. Returned tokens are lowercased and normalised the same way
    as name tokens so they compare equal.
    """
    tokens: set[str] = set()
    for part in parts:
        if not part:
            continue
        for tok in _normalise_for_tokens(part).split():
            if len(tok) >= 4:
                tokens.add(tok)
    return tokens


def _normalise_for_tokens(text: str) -> str:
    """Normalise text for tokenisation.

    Replaces hyphens / en-dashes / em-dashes with spaces so that
    'University of Wisconsin–Madison' tokenises to
    {'university', 'of', 'wisconsin', 'madison'} rather than
    treating 'wisconsin–madison' as a single token.

    Also strips '.'/',' and canonicalises US legal-entity suffixes to
    their abbreviated form (Incorporated→inc, Corporation→corp, …) so
    that legal-form variants of the same org ("Acme, Inc." vs
    "Acme Incorporated") compare equal during matching.
    """
    t = _DASH_RE.sub(" ", text.lower())
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    for pat, repl in _LEGAL_SUFFIX_SUBS:
        t = pat.sub(repl, t)
    return t


# Name types that represent the canonical display / label of the org.
# Subset matches are only trusted against these — aliases often name
# parent organisations or include historical variants that cause
# false positives.
_CANONICAL_NAME_TYPES = {"ror_display", "label"}


def _ror_name_variants(org: dict[str, Any]) -> list[str]:
    """Every name ROR publishes for *org*, cleaned the way the display name is.

    Display name, labels, aliases and acronyms, each through the SAME bracket
    strip :func:`_extract_org_fields` applies to ``ror_display`` — ROR's
    "(United States)" / "(Detroit)" qualifier is its keyspace disambiguating
    two records, not part of what the organisation is called.

    Order is ROR's own, so the display name comes first and a caller that
    takes the first match prefers it. De-duplicated case-insensitively.
    """
    variants: list[str] = []
    seen: set[str] = set()
    for name_entry in org.get("names") or []:
        value = strip_parentheticals(
            _strip_ror_country_suffix(name_entry.get("value") or ""),
        ).strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        variants.append(value)
    return variants


def _compute_name_score(
    query: str,
    org_names: list[dict[str, Any]],
    location_tokens: set[str] | None = None,
    caps: set[str] | None = None,
) -> float:
    """Score how well *query* matches any of the organisation's name variants.

    Strategy:
    1. Exact match against any variant → 1.0.
    2. Token-subset match on a CANONICAL name (ror_display/label):
       every significant (≥4-char) query token appears as a whole
       word → 1.0.  Aliases are excluded from this rule because they
       often reference the parent org and cause child entries to
       wrongly score 1.0 (e.g. CIMSS has an alias 'University of
       Wisconsin Madison Cooperative Institute ...').
    3. Length-guarded substring match (shorter side ≥60% of longer)
       against canonical names only.
    4. Fuzz ratios across all variants (token_sort_ratio always;
       partial_ratio only when length-guarded).

    Short acronym variants (≤4 chars) are excluded from fuzz scoring.
    Hyphens and en/em dashes are normalised to spaces so
    'University of Wisconsin–Madison' tokenises correctly.
    """
    query_lower = _normalise_for_tokens(query.strip())
    if not query_lower:
        return 0.0

    # Partition variants into canonical (ror_display/label) vs alias.
    canonical_values: list[str] = []
    all_values: list[str] = []
    for n in org_names:
        val = n.get("value")
        if not val:
            continue
        norm = _normalise_for_tokens(val)
        all_values.append(norm)
        types = set(n.get("types") or [])
        if types & _CANONICAL_NAME_TYPES:
            canonical_values.append(norm)

    # Step 1: exact match against any variant (acronyms allowed here)
    for val in all_values:
        if query_lower == val:
            return 1.0

    location_tokens = location_tokens or set()
    query_tokens = set(query_lower.split())
    significant_query_tokens = {t for t in query_tokens if len(t) >= 4}
    # Significant tokens that actually identify the ORG (not its city/state).
    # A subset/substring match resting entirely on location tokens matches
    # every same-city org, so it must not award a perfect score.
    distinctive_query_tokens = significant_query_tokens - location_tokens

    # Scoring values: exclude short acronym-like variants for fuzz
    scoring_values = [v for v in all_values if len(v) >= 5]
    if not scoring_values:
        return 0.0

    def _length_ok(a: str, b: str, ratio: float = 0.6) -> bool:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        return longer > 0 and shorter / longer >= ratio

    # Identifier-token guard (also applied in step 4): short all-caps
    # acronyms in the query (e.g. "HFT", "EMSL", "ASL") are the
    # distinguishing element when the rest of the name is a generic city
    # or descriptor. The subset/substring shortcuts below only count the
    # ≥4-char "significant" tokens, so "HFT Stuttgart" would otherwise
    # subset-match ANY "… Stuttgart" org (Marienhospital Stuttgart,
    # Stuttgart Observatory …) on the shared "stuttgart" token alone and
    # return a false 1.0. Require every query acronym to appear in the
    # candidate before the shortcut can fire.
    #
    # Gated on case CONTRAST, not upper case alone — see
    # _guard_identifier_tokens. An entirely upper-case query (SAP's usual
    # storage form, e.g. "BRIGHAM & WOMENS HOSP") has no contrast to read, so
    # the guard would treat every short word as an acronym and block a true
    # match; there it does not fire at all.
    q_identifiers = _guard_identifier_tokens(query)

    # Step 2 + 3: subset and substring only against CANONICAL names.
    # The substring rule is tight (≥90% length similarity) to prevent
    # a short canonical name from matching a longer query that merely
    # contains it — e.g. "Regional Health" inside "LAKELAND REGIONAL
    # HEALTH" should NOT produce a perfect score.
    for val in canonical_values:
        val_tokens = set(val.split())
        if q_identifiers and not q_identifiers.issubset(val_tokens):
            # Query carries a distinguishing acronym the candidate lacks —
            # the shortcut cannot fire; defer to the guarded fuzz path.
            continue
        if significant_query_tokens and not distinctive_query_tokens:
            # The query's only significant tokens are location (city/state)
            # tokens — a subset/substring hit here would match every
            # same-city org. Defer to the guarded fuzz path.
            continue
        if significant_query_tokens and significant_query_tokens.issubset(val_tokens):
            return 1.0
        if _length_ok(query_lower, val, ratio=0.9) and (
            query_lower in val or val in query_lower
        ):
            return 1.0

    # Step 4: token_sort_ratio against CANONICAL names only.
    # Aliases are excluded here because short sibling aliases like
    # "SIU School of Medicine" share 3 of 4 tokens with "Yale School
    # of Medicine" and score 0.85, causing a false match. The
    # canonical ror_display is the only form we trust for fuzzy
    # scoring — aliases contribute only via exact-match in step 1.
    #
    # Distinctive-token guard: the query contains domain words
    # ("regional", "health", "medical", "center", "research") that
    # appear in dozens of unrelated orgs. A fuzzy match ≥0.8 is
    # trustworthy only if the matched variant also shares a
    # DISTINCTIVE token (length ≥5, not a common domain word) with
    # the query. Otherwise orgs like "Newman Regional Health" would
    # match "Lakeland Regional Health" at 0.83.
    #
    # Identifier-token guard: short all-caps acronyms in the query
    # (e.g. "EMSL", "ASL") are the distinguishing element when the
    # rest of the name is descriptive — every such acronym must
    # appear in the candidate's tokens, or the score is capped.
    # (q_identifiers computed above for the step-2 shortcut guard.)
    canonical_scoring = [v for v in canonical_values if len(v) >= 5]
    best = 0.0
    for val in canonical_scoring:
        token_ratio = fuzz.token_sort_ratio(query_lower, val) / 100.0
        if token_ratio <= best:
            continue
        v_tokens = set(val.split())
        # Distinctive-token check. Location tokens (city/state) are excluded
        # too — a fuzz hit that shares only the city ("… Stuttgart") is not a
        # trustworthy same-org signal, so it must not save the score.
        q_distinctive = {
            t for t in query_tokens
            if len(t) >= _DISTINCTIVE_TOKEN_MIN_LEN
            and t not in _COMMON_DOMAIN_WORDS
            and t not in _CONNECTOR_WORDS
            and t not in location_tokens
        }
        if q_distinctive and not all(
            any(_fuzzy_token_covers(t, u) for u in v_tokens)
            for t in q_distinctive
        ):
            # A distinctive token of the query is not covered by the candidate —
            # cap at 0.7 so it cannot cross the 0.8 match threshold.
            #
            # EVERY distinctive token must be covered, not merely one of them.
            # Sharing one is not enough: "Coastal Analytical Services" and
            # "Analytical Services" (ANSER, ror.org/04g2rbh88) share
            # "analytical" and token_sort to 0.83, yet the query's leading
            # "coastal" — the token that says WHICH organisation — appears
            # nowhere in the candidate. This is the non-acronym twin of the
            # identifier-token guard below: "EMSL"/"ASL" are caught there
            # because they are short and capitalised, "Coastal" is not.
            #
            # Coverage is `_fuzzy_token_covers`, not set membership, so the
            # guard does not undo what fuzzy matching is for: a prefix
            # ("univ"↔"university") and a typo ("insitute"↔"institute",
            # "lüneborg"↔"lüneburg") still count as covered. Only a token with
            # no counterpart at all caps the score.
            token_ratio = min(token_ratio, 0.7)
            # Which guard capped this candidate — read out by the caller so a
            # rejection can name the rule that made it, rather than reporting
            # a bare "below threshold". Recording only; the cap itself is
            # unchanged.
            if caps is not None:
                caps.add("distinctive_token")
        # Identifier-token check
        if q_identifiers and not q_identifiers.issubset(v_tokens):
            token_ratio = min(token_ratio, 0.7)
            if caps is not None:
                caps.add("identifier_token")
        if token_ratio > best:
            best = token_ratio

    # Initialism fallback: recover orgs referenced only by their initials
    # ("JAH VA Hospital" → "James A. Haley Veterans' Hospital"), which fuzz
    # alone scores too low to match.
    return max(best, _initialism_score(query, canonical_values))


_COMMON_DOMAIN_WORDS = {
    "regional", "health", "medical", "center", "centre", "research",
    "hospital", "clinic", "system", "systems", "services", "care",
    "university", "college", "institute", "school", "department",
    "division", "faculty", "laboratory", "laboratories", "labs", "group",
    "company", "inc", "corporation", "corp", "ltd", "llc", "gmbh", "kgaa",
    "sarl", "intl", "international", "national", "american", "united",
    "global",
}

# Minimum length for a query token to count as DISTINCTIVE in the step-4
# guard. Four, matching `significant_query_tokens` and
# `_extract_location_tokens` above — every other length test in this module
# treats ≥4 as significant, and the guard has no reason to be the exception.
#
# A five-char floor silently exempted every organisation whose distinguishing
# word is four letters — Acme, Duke, Yale, Mayo, Ohio, Iowa — from the guard
# entirely: with the discriminating token invisible to `q_distinctive`, all
# that remained was a shared generic word, and the raw token_sort_ratio stood.
# "Acme Biotech" (Tampa FL) scored 0.87 against ROR's "AUM BioTech"
# (Philadelphia PA) on the shared "biotech" alone and was written as a
# verified Tier 1 match, name and domain and ror_id together. At four, "acme"
# is distinctive, "aum" does not cover it, and the candidate caps at 0.7.
#
# Dropping the floor pulls generic four-letter tokens into scope, so the
# non-distinguishing ones are named in `_COMMON_DOMAIN_WORDS` above: legal
# forms ("gmbh", "kgaa", "sarl") and the abbreviations `_fuzzy_token_covers`
# cannot bridge because they are not prefixes of their expansion ("labs" ↛
# "laboratories", "intl" ↛ "international"). Abbreviations that ARE prefixes
# ("univ", "inst", "hosp", "dept", "tech") need no entry — coverage already
# accepts them.
_DISTINCTIVE_TOKEN_MIN_LEN = 4

# Articles, prepositions and conjunctions. A connector never says WHICH
# organisation, so it must not be able to cap a candidate no matter how long
# it is — this is a separate exclusion from `_COMMON_DOMAIN_WORDS`, which
# holds words that *are* about the org but describe its type.
#
# Most connectors are under the length floor already and never reach the
# distinctive set. The one that bites is German "fuer": SAP stores names in
# ASCII, so row 9 arrives as "Hochschule fuer Technik Stuttgart" while ROR
# holds "Hochschule für Technik Stuttgart" (ror.org/039gdg280). The pair
# fuzzes to 0.95 and ROR's own affiliation scorer returns it at 0.97, but
# "fuer" ↔ "für" is not a coverable pair — `_fuzzy_token_covers` needs both
# tokens at ≥4 characters before it will accept a spelling difference, and
# "für" is three.
#
# This list is the narrow fix for the connectors, NOT for transliteration in
# general. `_fuzzy_token_covers` bridges a transliterated umlaut only when the
# two spellings fuzz above 85, which is the exception: "universitaet" ↔
# "universität" clears it at 86.96, but "koeln" ↔ "köln" (66.67), "muenchen" ↔
# "münchen" (80.00) and "strasse" ↔ "straße" (76.92) do not. Those are all ≥5
# characters, so they were already capping matches under the old floor — a
# pre-existing gap this change neither caused nor closes. Folding ü→ue / ö→oe
# / ä→ae / ß→ss in `_normalise_for_tokens` would close the whole class; it is
# left alone deliberately, because no record in the demo batch exercises it
# and the change would move every German match at once.
_CONNECTOR_WORDS = {
    # English
    "the", "of", "and", "for", "in", "at", "on",
    # German
    "fuer", "für", "und", "der", "die", "das", "des", "dem", "den",
    "von", "vom", "zur", "zum", "im",
    # French
    "de", "du", "la", "le", "les", "et", "pour", "aux",
    # Spanish / Portuguese / Italian
    "del", "della", "delle", "degli", "dei", "da", "do", "dos", "das",
    "y", "e", "en",
    # Dutch
    "van", "het", "voor",
}


def _initialism_score(query: str, canonical_values: list[str]) -> float:
    """Score a query that refers to an org by its initials.

    fuzz cannot bridge an acronym to its expansion ("JAH" → "James A.
    Haley"), so an org referenced only by initials never reaches the match
    threshold on fuzz alone — e.g. "JAH VA Hospital" vs. "James A. Haley
    Veterans' Hospital" scores ~0.58. When a short all-caps acronym in the
    query (≥3 letters) equals a contiguous run of the leading initials of a
    canonical name's words, and the query's organisation-type word
    (Hospital, University, …) is shared with that name, treat it as a
    confident match.

    Guards against coincidence: the acronym must be ≥3 letters, must map to a
    run that includes a distinctive (non-common, ≥4-letter) word, and the
    org-type word must match so "JAH Hospital" can only resolve to another
    hospital. ``canonical_values`` are already lowercased / dash-normalised.
    """
    acronyms = {a for a in _extract_identifier_tokens(query) if len(a) >= 3}
    if not acronyms:
        return 0.0
    q_tokens = set(_normalise_for_tokens(query).split())
    q_type_words = q_tokens & _COMMON_DOMAIN_WORDS
    for val in canonical_values:
        words = [w for w in val.split() if w]
        if len(words) < 2:
            continue
        if q_type_words and not (q_type_words & set(words)):
            continue
        initials = "".join(w[0] for w in words)
        for acro in acronyms:
            start = initials.find(acro)
            if start < 0:
                continue
            # The matched run must contain a distinctive word, so an acronym
            # is not satisfied purely by short/common words.
            run = words[start:start + len(acro)]
            if any(len(w) >= 4 and w not in _COMMON_DOMAIN_WORDS for w in run):
                return 1.0
    return 0.0


def _score_org(
    query: str,
    org: dict[str, Any],
    location_tokens: set[str] | None = None,
    caps: set[str] | None = None,
) -> float:
    """Wrapper: extract org names and compute match score.

    ``caps`` is an optional out-parameter: the names of the guards that capped
    this candidate's score, for the provenance rejection log (Fix 10 Step 4).
    """
    return _compute_name_score(query, org.get("names", []), location_tokens, caps)


_ROR_COUNTRY_SUFFIX_RE = re.compile(
    r"\s*\(\s*(?:United States|USA|United Kingdom|UK|Germany|France|"
    r"Japan|China|Canada|Australia|Switzerland|Netherlands|Spain|"
    r"Italy|Sweden|Denmark|Norway|Finland|Belgium|Austria|Ireland|"
    r"Poland|Israel|Singapore|Brazil|India|Mexico|New Zealand|"
    r"South Korea|Russia|Portugal|Czech Republic|Greece|Turkey|"
    r"South Africa|Hong Kong|Taiwan)\s*\)\s*$",
    re.IGNORECASE,
)

# ROR sometimes appends a full ", City, State, Country" tail to the
# display name — e.g. "Merck & Co., Inc., Rahway, NJ, USA". Strip it
# back to the clean legal name.
_ROR_ADDRESS_SUFFIX_RE = re.compile(
    r",\s*[A-Z][A-Za-z .'-]+,\s*[A-Z]{2},\s*(?:USA|United States|"
    r"UK|United Kingdom|Canada|Germany|France|Japan|Australia|"
    r"Switzerland|Netherlands|China|India|Brazil|Italy|Spain|Sweden)\s*$",
    re.IGNORECASE,
)


def _strip_ror_country_suffix(name: str | None) -> str | None:
    if not name:
        return name
    cleaned = _ROR_COUNTRY_SUFFIX_RE.sub("", name)
    cleaned = _ROR_ADDRESS_SUFFIX_RE.sub("", cleaned)
    return cleaned.strip() or name


def extract_website_from_ror(ror_org: dict[str, Any]) -> str | None:
    """Extract the official website URL from a ROR organisation dict.

    Returns the first ``links[]`` entry whose ``type == 'website'``,
    or None if none is present. ROR's website link is the
    authoritative org homepage — write directly to ``website_url``.
    """
    for link in ror_org.get("links", []) or []:
        if link.get("type") == "website" and link.get("value"):
            return link["value"]
    return None


def _org_country_code(org: dict[str, Any]) -> str | None:
    """ISO alpha-2 country code of an org's primary location, uppercased.

    Reads ``locations[0].geonames_details.country_code`` (ROR v2). Returns
    None when the org carries no location / country code.
    """
    locations = org.get("locations") or []
    if not locations:
        return None
    cc = (locations[0].get("geonames_details", {}) or {}).get("country_code")
    return cc.strip().upper() if cc else None


def _country_ok(org: dict[str, Any], want_country_code: str | None) -> bool:
    """Country guard: True if no country was requested, or the org's country
    matches the requested ISO alpha-2 (case-insensitive).

    Rejects on a definite mismatch AND when the requested country is known but
    the org has no country code — ROR's affiliation scorer and the no-filter
    query retry both happily return same-name orgs from the wrong country
    (e.g. a US "BASF" for a German record), which is a different entity.
    """
    if not want_country_code:
        return True
    return _org_country_code(org) == want_country_code.strip().upper()


def _extract_org_fields(org: dict[str, Any]) -> dict[str, Any]:
    """Extract common fields (name, website, types, children, country) from an org dict."""
    org_names = org.get("names", [])

    display_name = None
    for name_entry in org_names:
        if "ror_display" in name_entry.get("types", []):
            display_name = name_entry["value"]
            break
    if not display_name and org_names:
        display_name = org_names[0]["value"]

    # ROR appends a bracketed qualifier to disambiguate orgs that share a
    # name — a country ("Pfizer (United States)"), or a city for the sites
    # of one company ("3M (Detroit)", "3M Corporate (Saint Paul)"). The
    # qualifier belongs to ROR's keyspace, not to the organisation, so the
    # whole span goes before the name reaches matching or the output.
    # `_strip_ror_country_suffix` still runs for the un-bracketed form it
    # also handles (a trailing ", City, ST, Country" address tail).
    display_name = strip_parentheticals(_strip_ror_country_suffix(display_name))

    # ROR may carry SEVERAL acronym entries, including historical ones ("NBS"
    # for NIST, "PHS" for Mass General Brigham). Take the one whose letters are
    # the initials of the CURRENT official name; if none match, take no acronym
    # rather than the first (stale) one.
    acronym = None
    acronym_candidates = [
        (name_entry.get("value") or "").strip()
        for name_entry in org_names
        if "acronym" in name_entry.get("types", [])
        and (name_entry.get("value") or "").strip()
    ]
    for cand in acronym_candidates:
        if acronym_matches_name(cand, display_name):
            acronym = cand
            break

    website = extract_website_from_ror(org)
    domain = extract_domain(website) if website else None

    org_types = [t.lower() for t in org.get("types", [])]
    is_research = any(t in ROR_RESEARCH_TYPES for t in org_types)

    children = [
        {"name": r["label"], "id": r["id"]}
        for r in org.get("relationships", [])
        if r.get("type", "").lower() == "child"
    ]

    country = None
    # The registered locality. Fix D(2) compares it against the record's
    # city/state with the same comparator the page read uses.
    #
    # EVERY location ROR publishes, not just the first. ROR's `locations[]` is
    # a list and a multi-site organisation genuinely carries several — the
    # aggregating comparator is built to take a SET, and handing it one member
    # of a set is how a record naming a real site of the organisation gets
    # reported as contradicting "the" registered address. GLEIF's two blocks
    # were given to the comparator for exactly this reason; ROR's list was
    # still being truncated to `locations[0]`, so the two registries were
    # answering the same question from different amounts of evidence.
    #
    # The flat `city` / `region` / `country` keys still name the PRIMARY
    # location, unchanged: they are output fields and telemetry, and the
    # comparison reads `addresses`.
    city = region = None
    addresses: list[dict[str, Any]] = []
    for index, location in enumerate(org.get("locations") or []):
        geo = (location or {}).get("geonames_details", {}) or {}
        entry = {
            "kind": "registered" if index == 0 else "location",
            "city": (geo.get("name") or "").strip() or None,
            "region": (
                (geo.get("country_subdivision_name") or "").strip()
                or (geo.get("country_subdivision_code") or "").strip()
                or None
            ),
            "country": geo.get("country_name"),
        }
        if index == 0:
            country, city, region = entry["country"], entry["city"], entry["region"]
        if not any(v for k, v in entry.items() if k != "kind"):
            continue
        # A duplicate would double-count one statement in the trace without
        # changing the verdict — the same de-duplication GLEIF's two blocks get.
        if any(
            {k: v for k, v in entry.items() if k != "kind"}
            == {k: v for k, v in seen.items() if k != "kind"}
            for seen in addresses
        ):
            continue
        addresses.append(entry)

    return {
        "addresses": addresses,
        "ror_id": org["id"],
        "official_name": display_name,
        "city": city,
        "region": region,
        "acronym": acronym,
        "org_types": org_types,
        "is_research_institution": is_research,
        "domain": domain,
        "website": website,
        "children": children,
        "country": country,
        "country_code": _org_country_code(org),
        "org_names": org_names,
        # The cleaned name strings — small enough to travel in the result
        # payload and the cache, unlike the full `org_names` blob, which is
        # stripped at every return. `_write_registry_name` reads these to see
        # whether the record already states one of ROR's own names.
        "name_variants": _ror_name_variants(org),
    }


async def call_ror(
    name: str,
    country_code: str | None = None,
    country: str | None = None,
    city: str | None = None,
    state: str | None = None,
    base_url: str | None = None,
    *,
    record_domain: str | None = None,
) -> dict[str, Any]:
    """Hybrid ROR lookup: affiliation-first with query-fallback.

    1. Try ``?affiliation=name, city, state, country`` — the affiliation
       endpoint handles abbreviations, misspellings, and uses geographic
       context to disambiguate (e.g. "University of Melbourne, Australia"
       vs. "University of Melbourne, FL").
    2. If no confident match, try ``?query=name`` with a country filter,
       scoring locally via ``_compute_name_score``.

    Parameters
    ----------
    name : str
        Organisation name (e.g. "Univ of Florida").
    country_code : str | None
        ISO alpha-2 code for the query-endpoint country filter.
    country : str | None
        Raw country text from the record (included in the affiliation string).
    city : str | None
        City from the record (included in the affiliation string).
    state : str | None
        State/province from the record (included in the affiliation string).
    """
    # Cache key only — the ROR request below is built from `name` verbatim.
    global _ror_normalised_hits
    cache_key = lookup_key(name, country_code)
    legacy_key = legacy_lookup_key(name, country_code)
    if cache_key in _ror_cache:
        if legacy_key not in _ror_legacy_seen:
            _ror_normalised_hits += 1
        return _ror_cache[cache_key]
    _ror_legacy_seen.add(legacy_key)

    if base_url is None:
        base_url = os.getenv("ROR_API_BASE", "https://api.ror.org/v2/organizations")

    threshold = float(os.getenv("ROR_CONFIDENCE_THRESHOLD", "0.8"))

    # Expand a US state-name abbreviation for the ROR query only ("Fla State
    # Univ" → "Florida State Univ"), so the distinctive geographic token
    # survives and ROR does not fall back to a generic "State University" match.
    ror_name = _expand_state_abbrevs(name)

    # Build a rich affiliation string: "name, city, state, country"
    aff_parts = [ror_name]
    for part in (city, state, country):
        if part and part.strip():
            aff_parts.append(part.strip())
    affiliation_string = ", ".join(aff_parts)

    # Address tokens that must never, on their own, justify a name match —
    # otherwise a query whose only significant name token is the city (e.g.
    # "Uni Stuttgart", where "Uni" is too short to count) matches every
    # same-city org. Passed into every local rescore below.
    location_tokens = _extract_location_tokens(city, state, country)

    # Guard rejections (Fix 10 Step 4). A candidate the country guard, the
    # distinctive-token guard or the identifier-token guard refused: ROR was
    # confident and the pipeline deliberately declined it, which is the case
    # most worth being able to defend afterwards. Collected here and returned
    # with the result; the orchestrator writes them to the record's log.
    # Recording only — no decision changes.
    guard_rejections: list[dict[str, Any]] = []

    def _note_rejection(
        guard: str, org: dict[str, Any], score: float, detail: str,
    ) -> None:
        names = org.get("names") or [{}]
        guard_rejections.append({
            "guard": guard,
            "candidate_name": (names[0].get("value") if names else None),
            "candidate_id": org.get("id"),
            "score": score,
            "threshold": threshold,
            "detail": detail,
            "query": name,
        })

    def _cache(result: dict[str, Any]) -> dict[str, Any]:
        """Memory-cache the decision for the rest of this batch.

        The DECISION is memory-only and dies with the batch. What outlives the
        process is the registry's raw response, recorded by
        `utils.cache.cached_registry_get` — so a change to the selection rules
        below is re-applied on every run instead of being frozen along with the
        evidence. See that function.
        """
        _ror_cache[cache_key] = result
        return result

    def _no_match(score: float = 0.0, refused_by: str | None = None) -> dict[str, Any]:
        return _cache({
            "matched": False, "score": score,
            "guard_rejections": guard_rejections,
            "refused_by": refused_by,
        })

    def _locality(
        fields: dict[str, Any],
    ) -> tuple[str, str | None, str | None, list[str]]:
        """Fix D(2) — the ROR record's registered locality against this record's.

        Compared, carried, and never acted on here: a contradiction keeps the
        match (same-country relocations are common) and the orchestrator raises
        `registry-location-mismatch` on it. The one place it does decide
        something is Fix C(3) below, where a short name with a contradicting
        locality has nothing left to stand on.

        EVERY location ROR publishes, through the same aggregating comparator
        GLEIF's two addresses go through, so the two registries cannot drift
        apart either on the granularity rule (a city difference inside an
        agreeing region is a note, not a contradiction) or on how much of what
        the registry says gets consulted.
        """
        return compare_registry_addresses(
            fields.get("addresses") or [],
            city=city, region=state, country=country,
        )

    def _name_tier(org: dict[str, Any], queries: list[str]) -> str:
        """How strongly the NAME identified *org* — see `registry_match`.

        Every name variant ROR publishes counts, not only the display name:
        ROR carries aliases and former names, and a record that states one of
        them verbatim has named this organisation exactly as surely as one
        that states the display name. That is the same set `_is_exact` ranks
        on in the query path.

        Each variant goes through the SAME bracket strip `_extract_org_fields`
        applies to the display name. ROR's "(United States)" / "(Detroit)"
        qualifier is its keyspace disambiguating two records, not part of the
        organisation's name, and it must not be part of what the record is
        asked to state verbatim — leaving it in reported "Sekisui Xenotech" →
        "Sekisui XenoTech (United States)" as a fuzzy match and flagged three
        exact matches on the chemspeed batch.
        """
        return name_match_tier(list(queries), _ror_name_variants(org))

    def _short_name_ok(
        fields: dict[str, Any], score: float, org: dict[str, Any],
    ) -> bool:
        """Fix C(3) — a collision-prone name needs a corroborating signal.

        "BHS" fuzzy-matches Berkshire Health Systems and Behavioral Health
        Systems equally well, and the chemspeed batch matched a different one
        on each run. A name this short is not evidence on its own: either the
        registry's locality agrees with the record's, or the candidate's own
        website is the domain the record already carries, or it is a no match.
        No acronym is expanded and no threshold moves.
        """
        if not is_collision_prone(name):
            return True
        signal = second_signal(
            location_verdict=fields.get("location_verdict"),
            candidate_domain=fields.get("domain"),
            record_domain=record_domain,
        )
        if signal is not None:
            fields["corroborated_by"] = signal
            return True
        logger.info(
            "ROR: refusing '%s' → '%s' (%s) — the name is too short to "
            "identify an organisation on its own and nothing corroborates it "
            "(location=%s)",
            name[:60], fields.get("official_name"), fields.get("ror_id"),
            fields.get("location_verdict"),
        )
        _note_rejection(
            "short_name_uncorroborated", org, score,
            "collision-prone name with no corroborating signal "
            f"(location={fields.get('location_verdict')})",
        )
        return False

    try:
        # verify=resolve_tls_verify() — reuse the OpenAI client's TLS trust
        # resolution so ROR survives a corporate TLS-inspecting VPN. The
        # public certifi bundle fails the handshake on such a VPN
        # ("CERTIFICATE_VERIFY_FAILED: unable to get local issuer
        # certificate"); resolve_tls_verify() prefers a configured corp CA
        # bundle (AZURE_OPENAI_CA_BUNDLE / REQUESTS_CA_BUNDLE / SSL_CERT_FILE)
        # and falls back to certifi off-VPN. Without this every ROR call
        # fails and downstream gets `ror_id: null` / `domain: null`.
        async with httpx.AsyncClient(
            timeout=15.0, verify=resolve_tls_verify(),
        ) as client:
            # ── Strategy A: affiliation endpoint with location context ──
            # Run as a reusable attempt so we can try the raw name first and,
            # on a miss, retry with institution acronyms expanded ("HFT
            # Stuttgart" → "Hochschule für Technik Stuttgart") — ROR carries
            # no "HFT" alias, so the bare acronym otherwise returns unrelated
            # same-city orgs.
            expanded_query = expand_abbreviations(ror_name) or ror_name
            exp_lower = expanded_query.strip().lower()

            def _item_score(item: dict) -> float:
                return max(
                    _score_org(expanded_query, item, location_tokens),
                    _score_org(name, item, location_tokens),
                )

            #: Separators the SAP field and the registry spell differently for
            #: the same name: "LAC USC" against ROR's "LAC+USC", "HARBOR UCLA"
            #: against "Harbor–UCLA" (U+2013). Folded to a space before the
            #: exact test, along with whitespace runs.
            #:
            #: Periods and apostrophes are NOT in the set and stay significant
            #: — they distinguish names ("St. Mary's" is not "St Marys" for
            #: acceptance purposes), and the point of an exact test is that it
            #: is exact about words.
            _SEPARATORS = str.maketrans({c: " " for c in "+/\u2013\u2014-"})

            def _fold_separators(value: str) -> str:
                return " ".join(value.translate(_SEPARATORS).split()).lower()

            def _is_exact_by_words(item: dict) -> bool:
                """A name variant equal to the query once separators are folded.

                The no-chosen override's evidence test, and deliberately NOT
                `normalize_key`: that folds legal forms, and this codebase
                already records why a dedup-GROUPING equivalence must not
                decide identity ACCEPTANCE — see `batch_consensus._name_parts`
                on "Delta Analytical Inc" against "Delta Analytical LLC", two
                potentially distinct legal entities at one address. Folding a
                hyphen is a spelling difference; folding "Inc" is not.

                "University of Texas" against "University of North Texas"
                still fails: that differs by a WORD, which is the difference
                this test exists to catch.
                """
                want = _fold_separators(expanded_query)
                return any(
                    _fold_separators(ne.get("value") or "") == want
                    for ne in item.get("names", [])
                )

            def _is_exact(item: dict) -> bool:
                """A name variant equal to the expanded query, verbatim."""
                return any(
                    (ne.get("value") or "").strip().lower() == exp_lower
                    for ne in item.get("names", [])
                )

            def _token_diff(item: dict) -> int:
                """How far the display name's token count is from the query's.

                ROR's own weak tiebreaker, and the thing that separates
                "AstraZeneca" from "AZHC Foundation" when the scorer has
                saturated at 1.0 for both.
                """
                display_name = ""
                for ne in item.get("names", []):
                    if "ror_display" in ne.get("types", []):
                        display_name = ne.get("value") or ""
                if not display_name and item.get("names"):
                    display_name = item["names"][0].get("value", "")
                return abs(
                    len(display_name.split()) - len(expanded_query.split())
                )


            async def _try_affiliation(
                aff_str: str, rescore_names: list[str], strategy: str,
            ) -> dict[str, Any] | None:
                logger.info("ROR affiliation request: '%s'", aff_str[:120])

                async def _fetch_affiliation() -> dict[str, Any]:
                    resp = await client.get(
                        base_url, params={"affiliation": aff_str},
                    )
                    resp.raise_for_status()
                    return resp.json()

                data = await cached_registry_get(
                    "ror", base_url, {"affiliation": aff_str},
                    _fetch_affiliation,
                )
                items = data.get("items", []) or []
                ch = next(
                    (it for it in items if it.get("chosen") is True), None,
                )
                if ch and ch.get("score", 0.0) >= threshold:
                    return _evaluate(ch, aff_str, rescore_names, strategy)

                # `chosen` is a FAST PATH, not a prerequisite.
                #
                # ROR sets `chosen` only when its own scorer is confident it
                # has identified one organisation. Where it declines to choose,
                # the response is not empty — it is ranked, and the top entry
                # is often a clean match the guards below would accept. Record
                # 13334354 ("LAC USC MEDICAL CENTER") is the worked example:
                # ROR returns 04xzj3x20 first at 0.95 with `chosen: False`, and
                # the old early return meant nothing was ever scored.
                #
                # The candidates run through the IDENTICAL chain the chosen one
                # runs through — same local rescore, same country guard, same
                # short-name guard, same threshold. Nothing here is a new rule;
                # the only change is which items get to be asked.
                # Exact-only, by design. ROR withheld `chosen` because its own
                # scorer was not confident, and overriding that hedge needs
                # evidence stronger than a score — a name variant equal to the
                # query, verbatim. Record 13348274 is why: "Galveston -
                # University of Texas Medical" scored a University of North
                # Texas record above the threshold, and a fuzzy override made
                # it `ror:verified` with a `unt.edu` domain and no flag. A
                # silently-wrong registry identity is the costliest failure
                # this pipeline has; three correct matches do not pay for one.
                eligible = [
                    it for it in items
                    if it.get("score", 0.0) >= threshold
                    and it.get("organization")
                    and _is_exact_by_words(it["organization"])
                ][:3]
                if not eligible:
                    logger.info(
                        "ROR affiliation no confident match for '%s' "
                        "(chosen=%s, items=%d)",
                        aff_str[:80], ch is not None, len(items),
                    )
                    return None

                # Ambiguity among the candidates themselves, on the tiering the
                # query strategy already uses: peers are candidates NOTHING but
                # the score separates from the leader. An exact name match or a
                # better token fit is a deterministic discriminator, and where
                # one has spoken the registry has identified an organisation.
                best = eligible[0]
                best_org = best["organization"]
                _tier = (_is_exact(best_org), _token_diff(best_org))
                _peers = [
                    it for it in eligible[1:]
                    if (_is_exact(it["organization"]),
                        _token_diff(it["organization"])) == _tier
                ]
                if _peers and ambiguity_verdict(
                    [best.get("score", 0.0), _peers[0].get("score", 0.0)],
                    scale_max=1.0,
                ):
                    runner_up = _extract_org_fields(_peers[0]["organization"])
                    best_named = _extract_org_fields(best_org)
                    logger.info(
                        "ROR affiliation: refusing '%s' — '%s' (%s) and '%s' "
                        "(%s) are within the ambiguity margin",
                        aff_str[:60], best_named.get("official_name"),
                        best_named.get("ror_id"),
                        runner_up.get("official_name"), runner_up.get("ror_id"),
                    )
                    _note_rejection(
                        "registry_ambiguity", best_org,
                        best.get("score", 0.0),
                        "within the ambiguity margin of "
                        f"{runner_up.get('official_name')} "
                        f"({runner_up.get('ror_id')})",
                    )
                    return None

                for candidate in eligible:
                    accepted = _evaluate(
                        candidate, aff_str, rescore_names, strategy,
                    )
                    if accepted is not None:
                        return accepted
                return None

            def _evaluate(
                ch: dict[str, Any],
                aff_str: str,
                rescore_names: list[str],
                strategy: str,
            ) -> dict[str, Any] | None:
                """One affiliation item through the guard chain.

                Lifted verbatim out of `_try_affiliation` so the chosen item
                and a no-chosen candidate go through the same code, not
                through two copies of it that can drift.
                """
                org = ch["organization"]
                # Re-validate locally — ROR's affiliation scorer is fuzzy
                # enough to return e.g. "ASL Analytical" as a confident match
                # for "EMSL Analytical, Inc." (the shared 'Analytical' token
                # dominates). The local check applies the identifier-token
                # guard that ROR's scorer lacks.
                caps: set[str] = set()
                local_score = max(
                    _score_org(n, org, location_tokens, caps)
                    for n in rescore_names
                )
                if local_score < threshold:
                    logger.info(
                        "ROR affiliation chosen '%s' rejected by local "
                        "rescore (%.2f < %.2f)",
                        (org.get("names") or [{}])[0].get("value", "?")[:60],
                        local_score, threshold,
                    )
                    for _guard in sorted(caps) or ["local_rescore"]:
                        _note_rejection(
                            _guard, org, local_score,
                            "local rescore below the match threshold",
                        )
                    return None
                # Country guard — ROR's affiliation scorer ignores the country
                # context in the affiliation string often enough to return a
                # same-name org from the wrong country (e.g. a US "BASF" for a
                # German record). Reject it so we fall through to the
                # country-filtered query endpoint instead.
                if not _country_ok(org, country_code):
                    logger.info(
                        "ROR affiliation chosen '%s' rejected — country %s != "
                        "requested %s",
                        (org.get("names") or [{}])[0].get("value", "?")[:60],
                        _org_country_code(org), country_code,
                    )
                    _note_rejection(
                        "ror_country", org, ch["score"],
                        f"candidate country {_org_country_code(org)} != "
                        f"requested {country_code}",
                    )
                    return None
                fields = _extract_org_fields(org)
                (
                    fields["location_verdict"],
                    fields["location_detail"],
                    fields["location_scope"],
                    fields["location_notes"],
                ) = _locality(fields)
                fields["name_match_tier"] = _name_tier(org, rescore_names)
                if not _short_name_ok(fields, local_score, org):
                    return None
                res: dict[str, Any] = {
                    "matched": True,
                    "score": ch["score"],
                    "guard_rejections": guard_rejections,
                    **{k: v for k, v in fields.items() if k != "org_names"},
                    "query_used": name,
                    "affiliation_used": aff_str,
                    "country_filter": country_code,
                    "strategy": strategy,
                }
                _cache(res)
                logger.info(
                    "ROR affiliation matched '%s' → '%s' (score=%.2f)",
                    aff_str[:80], fields["official_name"], ch["score"],
                )
                return res

            expanded_name = expand_abbreviations(name) or name
            # Include the state-expanded forms among the rescore names so the
            # local re-validation of ROR's chosen org uses the distinctive
            # geographic token too.
            ror_expanded = expand_abbreviations(ror_name) or ror_name
            rescore_names = list(dict.fromkeys(
                [name, expanded_name, ror_name, ror_expanded]
            ))
            result_a = await _try_affiliation(
                affiliation_string, rescore_names, "affiliation",
            )
            if result_a is not None:
                return result_a

            # Retry with institution acronyms expanded, when that changes the
            # name. Only an additive attempt — names that already resolve
            # never reach here.
            acr_name = _expand_institution_acronyms(name)
            if acr_name.strip().lower() != name.strip().lower():
                acr_parts = [acr_name]
                for part in (city, state, country):
                    if part and part.strip():
                        acr_parts.append(part.strip())
                acr_aff_string = ", ".join(acr_parts)
                result_a2 = await _try_affiliation(
                    acr_aff_string,
                    [name, acr_name, expand_abbreviations(acr_name) or acr_name],
                    "affiliation_acronym",
                )
                if result_a2 is not None:
                    return result_a2

            logger.info(
                "ROR affiliation no confident match for '%s', trying query",
                affiliation_string[:80],
            )

            # ── Strategy B: query endpoint with country filter ─────────
            # Use the state-expanded name so "Fla State Univ" queries as
            # "Florida State Univ" rather than matching every "State University".
            query_params: dict[str, str] = {"query": ror_name}
            if country_code:
                query_params["filter"] = (
                    f"locations.geonames_details.country_code:{country_code}"
                )

            async def _fetch_query(params: dict[str, str]) -> dict[str, Any]:
                async def _go() -> dict[str, Any]:
                    resp_q = await client.get(base_url, params=params)
                    resp_q.raise_for_status()
                    return resp_q.json()

                return await cached_registry_get("ror", base_url, params, _go)

            q_data = await _fetch_query(query_params)

            # Retry without country filter if empty
            if not q_data.get("items") and country_code:
                logger.info(
                    "ROR query returned 0 items for '%s' with country=%s, retrying without filter",
                    name[:60], country_code,
                )
                q_data = await _fetch_query({"query": ror_name})

            items = q_data.get("items") or []

            # Country guard: never accept a wrong-country org — not even from
            # the no-filter retry above. Applied to the candidate set before
            # ranking so a correct-country org can still win.
            if country_code:
                kept = [it for it in items if _country_ok(it, country_code)]
                for _dropped in items:
                    if _dropped not in kept:
                        _note_rejection(
                            "ror_country", _dropped, 0.0,
                            f"candidate country {_org_country_code(_dropped)} "
                            f"!= requested {country_code}",
                        )
                if len(kept) != len(items):
                    logger.info(
                        "ROR query: dropped %d/%d wrong-country candidate(s) for "
                        "'%s' (requested %s)",
                        len(items) - len(kept), len(items), name[:60], country_code,
                    )
                items = kept

            if not items:
                logger.info("ROR: 0 items for '%s' across both strategies", name[:80])
                return _no_match()

            # Score ALL items and pick the best. Expand abbreviations
            # in the query first so 'Stanford Uni' exact-matches
            # 'Stanford University' rather than tying with every other
            # variant that contains 'Stanford'. Prefer exact display
            # name match over mere token-subset matches.
            def _rank_key(item: dict) -> tuple:
                # Fix C(1) — one TOTAL order, sorted ascending:
                #   (exact desc, score desc, token_diff asc, ROR id asc)
                #
                # The first three components are the ranking this function has
                # always used, in that order; only the ROR id at the end is
                # new. It was `sorted(items[:10], key=…, reverse=True)`, which
                # left every full tie in the order ROR answered in, and ROR's
                # order is not stable between runs. The `[:10]` truncation is
                # gone too — it was another way for response order to choose
                # which candidates were even scored, and local scoring costs
                # nothing next to the call that fetched them.
                return rank_key(
                    _item_score(item), item.get("id"),
                    -int(_is_exact(item)),
                ) + (_token_diff(item),)

            # `rank_key` puts the id last so the order is total; the token-count
            # tiebreaker sits between the score and the id, which is where it
            # has always been, so it is appended here rather than passed as a
            # prefix (a prefix would rank it ABOVE the score).
            def _sort_key(item: dict) -> tuple:
                exact, neg_score, ror_id, tdiff = _rank_key(item)
                return (exact, neg_score, tdiff, ror_id)

            ranked = sorted(items, key=_sort_key)
            best_org = ranked[0] if ranked else None
            best_score = _item_score(best_org) if best_org else 0.0

            if best_org is None:
                return _no_match()

            # Fix C(2) — a near-tie is a no-match. Two ROR records this close
            # are separated by a spelling variant in ROR's own keyspace, not by
            # evidence about the organisation, and which one ranks higher can
            # flip when ROR re-indexes. BHS oscillated between two plausible
            # expansions exactly this way.
            #
            # Only when the winner would otherwise HAVE been a match. Two
            # candidates that are both below the match threshold are both
            # rejected anyway, and reporting them as an ambiguity would replace
            # the guard rejection that actually explains the miss (the
            # distinctive-token cap, say) with one that does not.
            # Peers only — candidates that NOTHING except the score has
            # separated from the winner. An exact display-name match, and a
            # display name whose token count fits the query better, are both
            # deterministic discriminators that ROR's ranking already applies;
            # where one of them has spoken, the registry HAS identified one
            # organisation and the margin has nothing to add. (Same shape as
            # GLEIF comparing only within one registration-status tier.)
            #
            # This matters because ROR's local scorer saturates: it returns
            # 1.0 for "every significant query token appears as a whole word",
            # so ties at the ceiling are common and are not evidence of
            # genuine confusability. Without the tiering, "AstraZeneca" was
            # refused because "AZHC Foundation" also scored 1.0.
            _tier = (_is_exact(best_org), _token_diff(best_org))
            _peers = [
                i for i in ranked[1:]
                if (_is_exact(i), _token_diff(i)) == _tier
            ]
            if _peers and best_score >= threshold and ambiguity_verdict(
                [best_score, _item_score(_peers[0])], scale_max=1.0,
            ):
                runner_up = _extract_org_fields(_peers[0])
                best_named = _extract_org_fields(best_org)
                logger.info(
                    "ROR: refusing '%s' — '%s' (%s) and '%s' (%s) are within "
                    "the ambiguity margin; ROR has not identified one org",
                    name[:60], best_named.get("official_name"),
                    best_named.get("ror_id"), runner_up.get("official_name"),
                    runner_up.get("ror_id"),
                )
                _note_rejection(
                    "registry_ambiguity", best_org, best_score,
                    "within the ambiguity margin of "
                    f"{runner_up.get('official_name')} "
                    f"({runner_up.get('ror_id')})",
                )
                return _no_match(best_score, refused_by="ambiguous")

            org = best_org
            fields = _extract_org_fields(org)
            score = best_score

            if score < threshold:
                logger.info(
                    "ROR query: best score %.2f below threshold %.2f for '%s' (best: '%s')",
                    score, threshold, name[:60],
                    fields["official_name"] or "?",
                )
                _caps: set[str] = set()
                _score_org(expanded_query, best_org, location_tokens, _caps)
                _score_org(name, best_org, location_tokens, _caps)
                for _guard in sorted(_caps):
                    _note_rejection(
                        _guard, best_org, score,
                        "best candidate capped below the match threshold",
                    )
                return _no_match(score)

            (
                fields["location_verdict"],
                fields["location_detail"],
                fields["location_scope"],
                fields["location_notes"],
            ) = _locality(fields)
            fields["name_match_tier"] = _name_tier(
                org, [name, expanded_query],
            )
            if not _short_name_ok(fields, score, org):
                return _no_match(score, refused_by="short_name_uncorroborated")

            result = {
                "matched": True,
                "score": score,
                "guard_rejections": guard_rejections,
                **{k: v for k, v in fields.items() if k != "org_names"},
                "query_used": name,
                "country_filter": country_code,
                "strategy": "query",
            }
            _cache(result)
            logger.info(
                "ROR query matched '%s' → '%s' (score=%.2f)",
                name[:60], fields["official_name"], score,
            )
            return result

    except RegistryUnavailableFrozen:
        # CACHE_FROZEN and nothing recorded for this request. A clean miss,
        # already traced as `evidence-unavailable-frozen`; NOT cached, because
        # "we were not allowed to look" is not an answer about the name.
        logger.info("ROR: frozen cache has no response for '%s'", name[:80])
        return {"matched": False, "score": 0.0, "guard_rejections": []}
    except httpx.HTTPStatusError as exc:
        logger.error(
            "ROR API HTTP %d for '%s': %s",
            exc.response.status_code, name[:80], exc.response.text[:200],
        )
        return _no_match()
    except Exception:
        logger.exception("ROR API call failed for '%s'", name[:80])
        return _no_match()


def _bare_ror_id(ror_id: str | None) -> str | None:
    """``https://ror.org/02y3ad647`` → ``02y3ad647``. Accepts either form."""
    raw = (ror_id or "").strip().rstrip("/")
    if not raw:
        return None
    return raw.rsplit("/", 1)[-1] or None


async def call_ror_by_id(
    ror_id: str,
    country_code: str | None = None,
    base_url: str | None = None,
    *,
    country: str | None = None,
    city: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Resolve a **known ROR ID** to its ROR record, country guard applied.

    The [Wikidata crosswalk lane](`enrichment.wikidata`) is the only caller: a
    Wikidata item carrying ``P6782`` supplies a lookup key, and this follows it.

    There is no name scoring on this path and that is not a relaxed guard — it
    is the absence of a comparison to make. ``_compute_name_score`` and the
    distinctive-/identifier-token guards exist to decide *which* of several
    search results is the organisation; an identifier names exactly one record,
    so there is no candidate set to rank. What still applies, and is applied
    here, is the **country guard**: a US customer record must not take the
    identity of a same-named organisation in another country, and a stale or
    wrong Wikidata pointer is exactly how that would happen.

    *country* / *city* / *state* are the record's own location, and are used
    for exactly one thing: comparing the ROR record's registered locality
    against it, the way every other ROR path does. Omitting them is safe and
    only means that comparison has nothing to compare.

    Returns the same result shape as :func:`call_ror` with
    ``strategy = "by_id"``. Never raises.
    """
    identifier = _bare_ror_id(ror_id)
    if not identifier:
        return {"matched": False, "score": 0.0, "guard_rejections": []}

    if base_url is None:
        base_url = os.getenv("ROR_API_BASE", "https://api.ror.org/v2/organizations")

    # Its own cache namespace — a ROR ID is not a name, and the two keyspaces
    # must not serve each other.
    cache_key = (f"rorid:{identifier.lower()}", (country_code or "").upper() or None)
    if cache_key in _ror_cache:
        return _ror_cache[cache_key]
    guard_rejections: list[dict[str, Any]] = []

    def _cache(result: dict[str, Any]) -> dict[str, Any]:
        _ror_cache[cache_key] = result
        return result

    by_id_url = f"{base_url.rstrip('/')}/{identifier}"
    try:
        async with httpx.AsyncClient(
            timeout=15.0, verify=resolve_tls_verify(),
        ) as client:
            logger.info("ROR by-id request: %s", identifier)

            async def _fetch_by_id() -> dict[str, Any]:
                resp = await client.get(by_id_url)
                if resp.status_code == 404:
                    # A pointer to a ROR record that does not exist. Recorded
                    # as the empty body it is — a 404 IS the registry's answer,
                    # unlike a 5xx, and re-asking will not change it.
                    return {}
                resp.raise_for_status()
                return resp.json()

            org = await cached_registry_get("ror", by_id_url, None, _fetch_by_id)
            if not org:
                logger.info("ROR by-id: %s not found", identifier)
                return _cache({
                    "matched": False, "score": 0.0, "guard_rejections": [],
                })
    except RegistryUnavailableFrozen:
        logger.info("ROR by-id: frozen cache has no response for %s", identifier)
        return {"matched": False, "score": 0.0, "guard_rejections": []}
    except httpx.HTTPStatusError as exc:
        logger.error(
            "ROR by-id HTTP %d for %s", exc.response.status_code, identifier,
        )
        return {"matched": False, "score": 0.0, "guard_rejections": []}
    except Exception:
        logger.exception("ROR by-id lookup failed for %s", identifier)
        return {"matched": False, "score": 0.0, "guard_rejections": []}

    if not isinstance(org, dict) or not org.get("id"):
        return _cache({"matched": False, "score": 0.0, "guard_rejections": []})

    if not _country_ok(org, country_code):
        names = org.get("names") or [{}]
        guard_rejections.append({
            "guard": "ror_country",
            "candidate_name": (names[0].get("value") if names else None),
            "candidate_id": org.get("id"),
            "score": None,
            "threshold": None,
            "detail": (
                f"candidate country {_org_country_code(org) or '?'} != "
                f"requested {(country_code or '').upper()}"
            ),
            "query": identifier,
        })
        logger.info(
            "ROR by-id: rejecting %s — country %s != requested %s",
            identifier, _org_country_code(org) or "?", country_code,
        )
        return _cache({
            "matched": False, "score": 0.0, "guard_rejections": guard_rejections,
        })

    fields = _extract_org_fields(org)
    # Fix D(2) on the crosswalk lane too. This path previously compared no
    # locality at all, which meant the ONE route that picks an organisation
    # without ever looking at its name was also the one route whose address
    # was never checked. It is compared here and, as everywhere else, never
    # acted on in the client.
    (
        fields["location_verdict"],
        fields["location_detail"],
        fields["location_scope"],
        fields["location_notes"],
    ) = compare_registry_addresses(
        fields.get("addresses") or [],
        city=city, region=state, country=country,
    )
    result = {
        "matched": True,
        # A registry answered with the record its own identifier names. Not
        # scored — returned. Same claim `registry_exact` makes in provenance.
        "score": 1.0,
        "guard_rejections": guard_rejections,
        **{k: v for k, v in fields.items() if k != "org_names"},
        "query_used": identifier,
        "country_filter": country_code,
        "strategy": "by_id",
        # No name comparison happened on this path — there was no candidate
        # set to rank. Below exact tier by construction.
        "name_match_tier": CROSSWALK_TIER,
    }
    logger.info(
        "ROR by-id matched %s → '%s'", identifier, fields["official_name"],
    )
    return _cache(result)


class RORClient:
    """Thin wrapper around call_ror() for dependency injection and mocking."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._base_url = settings.ror_api_base

    async def call(
        self,
        name: str,
        country_code: str | None = None,
        country: str | None = None,
        city: str | None = None,
        state: str | None = None,
        *,
        record_domain: str | None = None,
    ) -> dict[str, Any]:
        """Look up an organisation name via ROR with location context."""
        return await call_ror(
            name,
            country_code=country_code,
            country=country,
            city=city,
            state=state,
            base_url=self._base_url,
            record_domain=record_domain,
        )

    async def call_by_id(
        self,
        ror_id: str,
        country_code: str | None = None,
        *,
        country: str | None = None,
        city: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a known ROR ID, with the country guard unchanged."""
        return await call_ror_by_id(
            ror_id, country_code=country_code, base_url=self._base_url,
            country=country, city=city, state=state,
        )
