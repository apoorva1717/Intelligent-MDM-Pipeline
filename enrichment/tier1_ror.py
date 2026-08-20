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

from llm.openai_client import resolve_tls_verify
from utils.cache import CacheKey, legacy_lookup_key, lookup_key
from utils.text_utils import (
    _fuzzy_token_covers,
    acronym_matches_name,
    expand_abbreviations,
    extract_domain,
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
_US_POSTAL_CODES: dict[str, str] = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut",
    "de": "Delaware", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii",
    "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine",
    "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan",
    "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
    "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico",
    "ny": "New York", "nc": "North Carolina", "nd": "North Dakota",
    "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon",
    "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington",
    "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
}

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
    """
    tokens: set[str] = set()
    for tok in _WORD_RE.findall(text):
        if 2 <= len(tok) <= 5 and tok.isupper():
            tokens.add(tok.lower())
    return tokens


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


def _compute_name_score(
    query: str,
    org_names: list[dict[str, Any]],
    location_tokens: set[str] | None = None,
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
    q_identifiers = _extract_identifier_tokens(query)

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
            if len(t) >= 5
            and t not in _COMMON_DOMAIN_WORDS
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
        # Identifier-token check
        if q_identifiers and not q_identifiers.issubset(v_tokens):
            token_ratio = min(token_ratio, 0.7)
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
    "division", "faculty", "laboratory", "group", "company", "inc",
    "corporation", "corp", "ltd", "llc", "international", "national",
    "american", "united", "global",
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
) -> float:
    """Wrapper: extract org names and compute match score."""
    return _compute_name_score(query, org.get("names", []), location_tokens)


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

    # ROR sometimes appends a country suffix to disambiguate orgs with
    # the same name in different countries — e.g. "Pfizer (United
    # States)". Strip it for downstream consumers; the canonical
    # company name is cleaner without it.
    display_name = _strip_ror_country_suffix(display_name)

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
    if org.get("locations"):
        country = (
            org["locations"][0]
            .get("geonames_details", {})
            .get("country_name")
        )

    return {
        "ror_id": org["id"],
        "official_name": display_name,
        "acronym": acronym,
        "org_types": org_types,
        "is_research_institution": is_research,
        "domain": domain,
        "website": website,
        "children": children,
        "country": country,
        "country_code": _org_country_code(org),
        "org_names": org_names,
    }


async def call_ror(
    name: str,
    country_code: str | None = None,
    country: str | None = None,
    city: str | None = None,
    state: str | None = None,
    base_url: str | None = None,
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

    def _no_match(score: float = 0.0) -> dict[str, Any]:
        r: dict[str, Any] = {"matched": False, "score": score}
        _ror_cache[cache_key] = r
        return r

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
            async def _try_affiliation(
                aff_str: str, rescore_names: list[str], strategy: str,
            ) -> dict[str, Any] | None:
                logger.info("ROR affiliation request: '%s'", aff_str[:120])
                resp = await client.get(
                    base_url, params={"affiliation": aff_str},
                )
                resp.raise_for_status()
                data = resp.json()
                ch = next(
                    (it for it in data.get("items", []) if it.get("chosen") is True),
                    None,
                )
                if not (ch and ch.get("score", 0.0) >= threshold):
                    logger.info(
                        "ROR affiliation no confident match for '%s' "
                        "(chosen=%s, score=%.2f)",
                        aff_str[:80], ch is not None,
                        ch["score"] if ch else 0.0,
                    )
                    return None
                org = ch["organization"]
                # Re-validate locally — ROR's affiliation scorer is fuzzy
                # enough to return e.g. "ASL Analytical" as a confident match
                # for "EMSL Analytical, Inc." (the shared 'Analytical' token
                # dominates). The local check applies the identifier-token
                # guard that ROR's scorer lacks.
                local_score = max(
                    _score_org(n, org, location_tokens) for n in rescore_names
                )
                if local_score < threshold:
                    logger.info(
                        "ROR affiliation chosen '%s' rejected by local "
                        "rescore (%.2f < %.2f)",
                        (org.get("names") or [{}])[0].get("value", "?")[:60],
                        local_score, threshold,
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
                    return None
                fields = _extract_org_fields(org)
                res: dict[str, Any] = {
                    "matched": True,
                    "score": ch["score"],
                    **{k: v for k, v in fields.items() if k != "org_names"},
                    "query_used": name,
                    "affiliation_used": aff_str,
                    "country_filter": country_code,
                    "strategy": strategy,
                }
                _ror_cache[cache_key] = res
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

            resp_q = await client.get(base_url, params=query_params)
            resp_q.raise_for_status()
            q_data = resp_q.json()

            # Retry without country filter if empty
            if not q_data.get("items") and country_code:
                logger.info(
                    "ROR query returned 0 items for '%s' with country=%s, retrying without filter",
                    name[:60], country_code,
                )
                resp_q = await client.get(base_url, params={"query": ror_name})
                resp_q.raise_for_status()
                q_data = resp_q.json()

            items = q_data.get("items") or []

            # Country guard: never accept a wrong-country org — not even from
            # the no-filter retry above. Applied to the candidate set before
            # ranking so a correct-country org can still win.
            if country_code:
                kept = [it for it in items if _country_ok(it, country_code)]
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
            expanded_query = expand_abbreviations(ror_name) or ror_name
            exp_lower = expanded_query.strip().lower()

            def _rank_key(item: dict) -> tuple:
                s = _score_org(expanded_query, item, location_tokens)
                s2 = _score_org(name, item, location_tokens)
                score = max(s, s2)

                # Inspect all name variants for an EXACT match against
                # the expanded query — that's the strongest signal and
                # beats any token-subset score.
                exact_match = 0
                display_name = ""
                for ne in item.get("names", []):
                    val = (ne.get("value") or "").strip().lower()
                    if val == exp_lower:
                        exact_match = 1
                    if "ror_display" in ne.get("types", []):
                        display_name = ne.get("value") or ""
                if not display_name and item.get("names"):
                    display_name = item["names"][0].get("value", "")

                # Tiebreaker: prefer display name with closest token
                # count to the expanded query.
                token_diff = abs(
                    len(display_name.split()) - len(expanded_query.split())
                )

                # (exact_match desc, score desc, token_diff asc)
                return (exact_match, score, -token_diff)

            ranked = sorted(items[:10], key=_rank_key, reverse=True)
            best_org = ranked[0] if ranked else None
            best_score = 0.0
            if best_org:
                best_score = max(
                    _score_org(expanded_query, best_org, location_tokens),
                    _score_org(name, best_org, location_tokens),
                )

            if best_org is None:
                return _no_match()

            org = best_org
            fields = _extract_org_fields(org)
            score = best_score

            if score < threshold:
                logger.info(
                    "ROR query: best score %.2f below threshold %.2f for '%s' (best: '%s')",
                    score, threshold, name[:60],
                    fields["official_name"] or "?",
                )
                return _no_match(score)

            result = {
                "matched": True,
                "score": score,
                **{k: v for k, v in fields.items() if k != "org_names"},
                "query_used": name,
                "country_filter": country_code,
                "strategy": "query",
            }
            _ror_cache[cache_key] = result
            logger.info(
                "ROR query matched '%s' → '%s' (score=%.2f)",
                name[:60], fields["official_name"], score,
            )
            return result

    except httpx.HTTPStatusError as exc:
        logger.error(
            "ROR API HTTP %d for '%s': %s",
            exc.response.status_code, name[:80], exc.response.text[:200],
        )
        return _no_match()
    except Exception:
        logger.exception("ROR API call failed for '%s'", name[:80])
        return _no_match()


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
    ) -> dict[str, Any]:
        """Look up an organisation name via ROR with location context."""
        return await call_ror(
            name,
            country_code=country_code,
            country=country,
            city=city,
            state=state,
            base_url=self._base_url,
        )
