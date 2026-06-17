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

import certifi
import httpx
from rapidfuzz import fuzz

from utils.text_utils import expand_abbreviations, extract_domain

logger = logging.getLogger(__name__)

# Classification derived from ROR org types, not keyword matching.
ROR_RESEARCH_TYPES = {
    "education", "healthcare", "government",
    "facility", "nonprofit", "archive", "other",
}

# Module-level cache keyed by (name_lower, country_code).
_ror_cache: dict[tuple[str, str | None], dict[str, Any]] = {}


def clear_ror_cache() -> None:
    """Reset the module-level ROR cache (useful between test runs)."""
    _ror_cache.clear()


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


def _compute_name_score(query: str, org_names: list[dict[str, Any]]) -> float:
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

    query_tokens = set(query_lower.split())
    significant_query_tokens = {t for t in query_tokens if len(t) >= 4}

    # Scoring values: exclude short acronym-like variants for fuzz
    scoring_values = [v for v in all_values if len(v) >= 5]
    if not scoring_values:
        return 0.0

    def _length_ok(a: str, b: str, ratio: float = 0.6) -> bool:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        return longer > 0 and shorter / longer >= ratio

    # Step 2 + 3: subset and substring only against CANONICAL names.
    # The substring rule is tight (≥90% length similarity) to prevent
    # a short canonical name from matching a longer query that merely
    # contains it — e.g. "Regional Health" inside "LAKELAND REGIONAL
    # HEALTH" should NOT produce a perfect score.
    for val in canonical_values:
        val_tokens = set(val.split())
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
    q_identifiers = _extract_identifier_tokens(query)
    canonical_scoring = [v for v in canonical_values if len(v) >= 5]
    best = 0.0
    for val in canonical_scoring:
        token_ratio = fuzz.token_sort_ratio(query_lower, val) / 100.0
        if token_ratio <= best:
            continue
        v_tokens = set(val.split())
        # Distinctive-token check
        q_distinctive = {t for t in query_tokens if len(t) >= 5 and t not in _COMMON_DOMAIN_WORDS}
        if q_distinctive and not (q_distinctive & v_tokens):
            # No distinctive token shared — cap at 0.7 so it cannot
            # cross the 0.8 match threshold.
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


def _score_org(query: str, org: dict[str, Any]) -> float:
    """Wrapper: extract org names and compute match score."""
    return _compute_name_score(query, org.get("names", []))


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

    acronym = None
    for name_entry in org_names:
        if "acronym" in name_entry.get("types", []):
            value = (name_entry.get("value") or "").strip()
            if value:
                acronym = value
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
    cache_key = (name.lower().strip(), country_code)
    if cache_key in _ror_cache:
        return _ror_cache[cache_key]

    if base_url is None:
        base_url = os.getenv("ROR_API_BASE", "https://api.ror.org/v2/organizations")

    threshold = float(os.getenv("ROR_CONFIDENCE_THRESHOLD", "0.8"))

    # Build a rich affiliation string: "name, city, state, country"
    aff_parts = [name]
    for part in (city, state, country):
        if part and part.strip():
            aff_parts.append(part.strip())
    affiliation_string = ", ".join(aff_parts)

    def _no_match(score: float = 0.0) -> dict[str, Any]:
        r: dict[str, Any] = {"matched": False, "score": score}
        _ror_cache[cache_key] = r
        return r

    try:
        # verify=certifi.where() — match the OpenAI client's pattern so
        # ROR is immune to a bogus SSL_CERT_FILE / REQUESTS_CA_BUNDLE
        # env var (a common Windows gotcha where a placeholder corp-CA
        # path is set but the file doesn't exist). Without this, every
        # ROR call fails and downstream gets `domain: null`.
        async with httpx.AsyncClient(
            timeout=15.0, verify=certifi.where(),
        ) as client:
            # ── Strategy A: affiliation endpoint with location context ──
            logger.info(
                "ROR affiliation request: '%s'", affiliation_string[:120],
            )
            resp_aff = await client.get(
                base_url, params={"affiliation": affiliation_string},
            )
            resp_aff.raise_for_status()
            aff_data = resp_aff.json()

            chosen = next(
                (it for it in aff_data.get("items", []) if it.get("chosen") is True),
                None,
            )

            if chosen and chosen.get("score", 0.0) >= threshold:
                org = chosen["organization"]
                # Re-validate locally — ROR's affiliation scorer is
                # fuzzy enough to return e.g. "ASL Analytical" as a
                # confident match for "EMSL Analytical, Inc." (the
                # shared 'Analytical' token dominates the score). The
                # local check applies the identifier-token guard that
                # ROR's scorer lacks.
                expanded_name = expand_abbreviations(name) or name
                local_score = max(
                    _score_org(name, org),
                    _score_org(expanded_name, org),
                )
                if local_score < threshold:
                    logger.info(
                        "ROR affiliation chosen '%s' rejected by local "
                        "rescore (%.2f < %.2f), falling through to query",
                        (org.get("names") or [{}])[0].get("value", "?")[:60],
                        local_score, threshold,
                    )
                else:
                    fields = _extract_org_fields(org)
                    score = chosen["score"]
                    result: dict[str, Any] = {
                        "matched": True,
                        "score": score,
                        **{k: v for k, v in fields.items() if k != "org_names"},
                        "query_used": name,
                        "affiliation_used": affiliation_string,
                        "country_filter": country_code,
                        "strategy": "affiliation",
                    }
                    _ror_cache[cache_key] = result
                    logger.info(
                        "ROR affiliation matched '%s' → '%s' (score=%.2f)",
                        affiliation_string[:80], fields["official_name"], score,
                    )
                    return result

            logger.info(
                "ROR affiliation no confident match for '%s' (chosen=%s, score=%.2f), trying query",
                affiliation_string[:80],
                chosen is not None,
                chosen["score"] if chosen else 0.0,
            )

            # ── Strategy B: query endpoint with country filter ─────────
            query_params: dict[str, str] = {"query": name}
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
                resp_q = await client.get(base_url, params={"query": name})
                resp_q.raise_for_status()
                q_data = resp_q.json()

            if not q_data.get("items"):
                logger.info("ROR: 0 items for '%s' across both strategies", name[:80])
                return _no_match()

            # Score ALL items and pick the best. Expand abbreviations
            # in the query first so 'Stanford Uni' exact-matches
            # 'Stanford University' rather than tying with every other
            # variant that contains 'Stanford'. Prefer exact display
            # name match over mere token-subset matches.
            expanded_query = expand_abbreviations(name) or name
            exp_lower = expanded_query.strip().lower()

            def _rank_key(item: dict) -> tuple:
                s = _score_org(expanded_query, item)
                s2 = _score_org(name, item)
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

            ranked = sorted(q_data["items"][:10], key=_rank_key, reverse=True)
            best_org = ranked[0] if ranked else None
            best_score = 0.0
            if best_org:
                best_score = max(
                    _score_org(expanded_query, best_org),
                    _score_org(name, best_org),
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
