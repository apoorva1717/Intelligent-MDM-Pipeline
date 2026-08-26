"""Compact search-handle derivation for the enrichment response.

Produces two short, search-friendly strings per record so downstream
consumers can re-query (Google, internal search, dedup keys) without
re-deriving abbreviations or domains themselves.

Both terms are derived **after enrichment, from the enriched values
only** — never from the pre-enrichment SAP input. The input Search Term
columns are customer-maintained free text (stale abbreviations, typos,
person initials); once enrichment has settled Name 1/2, the domain and
the registry acronym, re-deriving from those is strictly better than
echoing what the input happened to carry.

Both terms are capped at **two terms**. A search term is something you
type into a search box, and past two words a handle stops being a query
and starts being a copy of the name — "Kellogg Battle Creek MI Plant"
retrieves nothing that "Kellogg" does not. Which two is not a
truncation: the head is kept (an organisation name is head-initial, so
the leading token says which organisation), and the second slot goes to
the first token that actually narrows the search, stepping over legal
forms, structural words, facility words, corporate scaffolding, place
names and anything the record's own address already states. An `&`
joining the two is kept and counts as neither — "Procter & Gamble" is
two terms, and P&G is what people search.

``search_term_1`` mirrors *name1* (an institution). Institutions
typically have well-known acronyms (MIT, UCLA, NASA), so the rule is
acronym-first, domain second, and — when neither exists — a handle
derived from the enriched Name 1 itself.

``search_term_2`` mirrors *name2* (a department / unit / lab). Units
rarely have well-known acronyms, so the rule is instead a
ready-to-search **text phrase**: extract an explicit parenthetical
acronym when given, otherwise reduce the enriched Name 2 to what
actually names the unit. Structural words are not part of that —
"Department", "Dept", "Division", "Div", "School", "Institute",
"Centre", "Laboratory", "Lab", "Office", "Group" are dropped wherever
they appear, so "Chemistry Dept" and "Department of Chemistry" both
search as CHEMISTRY. A unit-scoped subdomain/path is kept as a
last-resort fallback for the rare case where the textual form
collapses to empty.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from enrichment.locality import US_REGION_CODES
from utils.text_utils import (
    acronym_matches_name,
    is_admin_unit,
    is_granular_unit,
    looks_like_research_institution,
    seg_matches_needle,
)


# Stopwords excluded from acronym derivation (search_term_1 only).
_ACRONYM_STOPWORDS = {
    "of", "for", "the", "and", "in", "on", "at", "to",
    "a", "an", "de", "du", "des", "la", "le", "les",
    "&",
}

# Path segments that are too generic to identify a unit.
_GENERIC_PATH_SEGMENTS = {
    "about", "people", "faculty", "staff", "directory",
    "news", "contact", "departments", "dept", "home",
    "index", "search", "page", "pages", "en", "us",
}

# Acronym in trailing parentheses: "...Computer Science (CSAIL)".
_PAREN_ACRONYM_RE = re.compile(r"\(([A-Z][A-Z0-9&\-]{1,9})\)\s*$")

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&\-]*")

# Same as `_WORD_RE` but also matches a standalone "&". `_WORD_RE` requires a
# leading letter, so the ampersand in "Procter & Gamble" never reached
# `derive_acronym`'s loop at all — the acronym came out PG because the token
# was invisible, not because it was rejected.
_WORD_OR_AMP_RE = re.compile(r"&|[A-Za-z][A-Za-z0-9&\-]*")

# Two-part public-suffix TLDs that must be stripped together. Mirrors
# the set used by utils.text_utils.extract_domain so a "registrable
# domain" produced there can be inverted back to just the name.
_TWO_PART_TLDS = {
    "co.uk", "ac.uk", "org.uk",
    "ac.jp", "co.jp",
    "com.au", "edu.au", "org.au",
    "ac.in", "co.in",
    "com.br", "org.br", "edu.br",
    "ac.nz", "co.nz",
    "ac.za", "co.za",
}


def strip_tld(host: str | None) -> str | None:
    """Return the host with its TLD removed.

    ``'mit.edu'``         → ``'mit'``
    ``'example.co.uk'``   → ``'example'``
    ``'cs.mit.edu'``      → ``'cs.mit'``
    ``'mit'``             → ``'mit'``   (already TLD-less)
    """
    if not host or not host.strip():
        return None
    parts = host.strip().lower().split(".")
    if len(parts) >= 3:
        last_two = f"{parts[-2]}.{parts[-1]}"
        if last_two in _TWO_PART_TLDS:
            return ".".join(parts[:-2]) or None
    if len(parts) >= 2:
        return ".".join(parts[:-1]) or None
    return parts[0] or None

# Generic prefixes stripped from name2 to reveal the meaningful core.
# Matched case-insensitively at the start of the trimmed string and
# require a following space so we don't chop a substring out of a real
# word. Order matters: longer / more-specific phrases first.
_NAME2_PREFIXES = (
    "department of",
    "dept of",
    "dept.",
    "division of",
    "div of",
    "div.",
    "school of",
    "institute of",
    "inst of",
    "inst.",
    "center for",
    "centre for",
    "faculty of",
    "office of",
    "group of",
    "laboratory of",
    "lab of",
)

# Symmetric trailing forms ("Theoretical Physics Department" →
# "Theoretical Physics"). Matched case-insensitively, requires a
# preceding space.
_NAME2_SUFFIXES = (
    "department",
    "division",
    "school",
    "institute",
    "centre",
    "center",
    "laboratory",
    "lab",
    "group",
)


def derive_acronym(name: str | None) -> str | None:
    """Derive an uppercase acronym from *name* using capitalised words.

    Used for ``search_term_1`` only — institutions are the names that
    actually have meaningful acronyms.

    ``'Massachusetts Institute of Technology'`` → ``'MIT'``
    ``'International Business Machines'``       → ``'IBM'``
    ``'mit'``                                   → ``None``
    """
    if not name or not name.strip():
        return None

    paren = _PAREN_ACRONYM_RE.search(name)
    if paren:
        return paren.group(1)

    # Require every significant (non-stopword) token to start with an
    # uppercase letter. Mixed-case names like "Massachusetts institute
    # of Technology" would otherwise produce "MT" — a misleading
    # half-acronym.
    #
    # `&` is the one stopword that SURVIVES, because it is part of the
    # acronym people actually search: "Procter & Gamble" is P&G, not PG, and
    # "Johnson & Johnson" is J&J. It is emitted only between two initials —
    # a leading or trailing ampersand is punctuation, not a letter of the
    # handle — and it never counts towards the two-initial minimum, so
    # "Smith & Co" is still rejected for having one significant word.
    initials: list[str] = []
    pending_amp = False
    for token in _WORD_OR_AMP_RE.findall(name):
        if not token:
            continue
        if token == "&":
            pending_amp = bool(initials)
            continue
        if token.lower() in _ACRONYM_STOPWORDS:
            continue
        first = token[0]
        if not first.isalpha():
            continue
        if not first.isupper():
            return None
        if pending_amp:
            initials.append("&")
            pending_amp = False
        initials.append(first.upper())

    if len([c for c in initials if c != "&"]) < 2:
        return None
    return "".join(initials)


def _title_case_preserve_acronyms(text: str) -> str:
    """Title-case *text* word by word, preserving all-uppercase tokens.

    ``'computer science'``    → ``'Computer Science'``
    ``'MRI lab'``              → ``'MRI Lab'``
    ``'computer science'``     → ``'Computer Science'``
    """
    out: list[str] = []
    for word in text.split():
        if len(word) >= 2 and word.isupper():
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


_DEPT_CORE_OF_RE = re.compile(
    r"\b(?:department|dept|division|school|institute|center|centre|"
    r"faculty|college)\s+of\s+(.+)",
    re.IGNORECASE,
)
_DEPT_CORE_TRAILING_RE = re.compile(
    r"(.+?)\s+(?:department|dept|division|school|institute|center|"
    r"centre|faculty|college)\s*$",
    re.IGNORECASE,
)


def extract_dept_core(name2: str | None) -> str | None:
    """Pull the unit-distinguishing core out of a name2 string by
    stripping donor-name prefixes and unit-type suffixes.

    ``'Russell H. Morgan Department of Radiology and Radiological Science'``
        → ``'Radiology and Radiological Science'`` (donor strip)
    ``'Theoretical Physics Department'`` → ``'Theoretical Physics'``
    ``'Department of Chemistry'``        → ``'Chemistry'``
    ``'Earth and Planetary Sciences'``   → ``'Earth and Planetary Sciences'`` (no donor)
    """
    if not name2 or not name2.strip():
        return None
    text = name2.strip()
    m = _DEPT_CORE_OF_RE.search(text)
    if m:
        return m.group(1).strip()
    m2 = _DEPT_CORE_TRAILING_RE.match(text)
    if m2:
        return m2.group(1).strip()
    return text


def clean_name2_phrase(name2: str | None) -> str | None:
    """Strip generic unit prefix/suffix from *name2* and title-case.

    ``'Department of Computer Science'``   → ``'Computer Science'``
    ``'Dept of Chemistry'``                → ``'Chemistry'``
    ``'School of Engineering'``            → ``'Engineering'``
    ``'Theoretical Physics Department'``   → ``'Theoretical Physics'``
    ``'Analytical Sciences'``              → ``'Analytical Sciences'``
    ``'department of'``                    → ``None`` (nothing left)
    """
    if not name2 or not name2.strip():
        return None

    core = name2.strip()
    lowered = core.lower()

    for prefix in _NAME2_PREFIXES:
        if lowered.startswith(prefix + " "):
            core = core[len(prefix):].strip()
            lowered = core.lower()
            break
        if lowered == prefix:
            core = ""
            break

    for suffix in _NAME2_SUFFIXES:
        if lowered.endswith(" " + suffix):
            core = core[: -len(suffix)].strip()
            break
        if lowered == suffix:
            core = ""
            break

    if not core:
        return None
    return _title_case_preserve_acronyms(core)


def unit_domain_or_path(
    source_url: str | None, base_domain: str | None
) -> str | None:
    """Return a unit-scoped handle from *source_url* if it isn't the
    institution's bare domain.

    Returns the *name part* only — TLDs are dropped on the way out so
    the result is shaped like a search term, not a URL:

    ``cs.mit.edu`` against base ``mit.edu`` → ``'cs'``
    ``mit.edu/cs/people``                    → ``'/cs'``

    Used only as a last-resort fallback for ``search_term_2`` when the
    textual form of name2 collapses to nothing.
    """
    if not source_url:
        return None
    try:
        parsed = urlparse(source_url)
    except Exception:
        return None

    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]

    base = (base_domain or "").lower().strip() or None
    if base and host != base and host.endswith("." + base):
        # Subdomain of the institution domain: return only the prefix.
        # 'cs.mit.edu' / 'mit.edu' → 'cs'
        return host[: -len("." + base)] or None
    if base and host != base and not host.endswith(base):
        # Different registrable domain entirely — strip its own TLD.
        return strip_tld(host)

    for segment in (parsed.path or "").split("/"):
        seg = segment.strip().lower()
        if not seg:
            continue
        if seg in _GENERIC_PATH_SEGMENTS:
            continue
        if not re.match(r"^[a-z0-9][a-z0-9\-]*$", seg):
            continue
        return f"/{seg}"

    return None


_TERM2_STOPWORDS = {
    "of", "for", "the", "and", "in", "on", "at", "to",
    "a", "an", "de", "du", "des", "la", "le", "les", "&",
}


def _dept_domain_to_search_term(
    dept_domain: str | None, base_domain: str | None,
) -> str | None:
    """Compact search handle derived from a *dept_domain* host.

    Strips ``www.`` / ``web.`` prefixes, then either the institution
    base (when *dept_domain* is a subdomain) or the TLD.

    ``'cs.mit.edu'`` + base ``'mit.edu'``       → ``'cs'``
    ``'eecs.mit.edu'`` + base ``'mit.edu'``     → ``'eecs'``
    ``'web.astro.princeton.edu'`` + base        → ``'astro'``
    ``'hopkinsmedicine.org'`` + base ``'jhu.edu'`` (cross-domain)
                                                → ``'hopkinsmedicine'``
    """
    if not dept_domain or not dept_domain.strip():
        return None
    host = dept_domain.strip().lower()
    # A path-based dept page arrives as a full URL — reduce to the hostname.
    if "://" in host:
        from urllib.parse import urlparse
        host = (urlparse(host).hostname or "").lower()
    if not host:
        return None
    for prefix in ("www.", "web."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    base = (base_domain or "").lower().strip() or None
    if base and host.endswith("." + base):
        stripped = host[: -len("." + base)]
        return stripped or None
    return strip_tld(host) or host


def _first_two_significant_words(text: str | None) -> str | None:
    """Take up to 2 significant words from *text* (lowercase stopwords
    excluded) and title-case the result.

    ``'Computer Science'``                   → ``'Computer Science'``
    ``'Chemistry and Biochemistry'``         → ``'Chemistry Biochemistry'``
    ``'Earth and Planetary Sciences'``       → ``'Earth Planetary'``
    ``'Radiology'``                          → ``'Radiology'``
    """
    if not text or not text.strip():
        return None
    words: list[str] = []
    # `[A-Za-z]+` used to be the pattern here, which split "P&G" into two
    # words and threw the ampersand away. `&` is part of the handle.
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9&\-]*", text):
        if tok.lower() in _TERM2_STOPWORDS:
            continue
        words.append(tok)
        if len(words) >= 2:
            break
    if not words:
        return None
    return _title_case_preserve_acronyms(" ".join(words))


# Legal-entity suffixes dropped from the Name 1 text handle (search_term_1 rule
# 3) so "Verdox, Inc." → "VERDOX", not "VERDOX INC". Industry words
# ("Diagnostics", "Biotech") are NOT dropped — "Precision Diagnostics" keeps both.
_ST1_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "ag", "plc", "lp", "llp", "pllc", "sa", "srl",
    "bv", "nv", "se", "pty", "oy", "ab", "as",
}


def _name1_text_handle(name1: str) -> str | None:
    """A searchable handle derived from the enriched Name 1 — the fallback
    for search_term_1 when no acronym and no domain exist.

    Legal-entity suffixes are dropped and the rest is handed on WHOLE. The
    two-term cap in `_normalise_term` does the shortening, and it has to see
    every word to choose between them: filling greedily to 32 chars here first
    cut "Massachusetts Institute of Technology" down to "Massachusetts
    Institute" before the cap could tell that Technology was the half worth
    keeping and Institute was not.

    ``'Verdox, Inc.'``                            → ``'Verdox'``
    ``'Applied Thin Films, Inc.'``                → ``'Applied Thin Films'``
    ``'University of Florida'``                   → ``'University of Florida'``
    ``'Massachusetts Institute of Technology'``   → ``'Massachusetts Institute of Technology'``
    """
    words: list[str] = []
    for raw in name1.split():
        tok = raw.strip(" ,.;:()[]{}\"'")
        # Punctuation-only tokens ("/", "-", "–") separate words, they are
        # not words: "Bayer U.S. – Crop Science" must not carry the dash.
        # `&` is the exception — it is not punctuation between two names, it
        # is part of the name ("Procter & Gamble"), and dropping it here is
        # what turned that handle into "PROCTER GAMBLE".
        if tok == "&":
            if words:
                words.append(tok)
            continue
        if not tok or not any(c.isalnum() for c in tok):
            continue
        if tok.lower() in _ST1_LEGAL_SUFFIXES:
            continue
        words.append(tok)
    while words and words[-1] == "&":
        words.pop()          # a trailing "&" joins nothing
    if not words:
        return None
    return " ".join(words) or None


def _truncate_word_boundary(s: str, width: int = 32) -> str:
    """Truncate *s* to *width* chars on a word boundary (back off to the last
    space); a single over-long word is hard-cut."""
    if len(s) <= width:
        return s
    idx = s.rfind(" ", 0, width + 1)
    if idx <= 0:
        return s[:width]
    return s[:idx].rstrip()


# ---------------------------------------------------------------------------
# Which tokens actually identify an organisation
# ---------------------------------------------------------------------------
#
# A search term is something you can type into a search box and get this
# organisation back. Two things follow, and both are decided by one question
# asked of each token — *does this word narrow the search to this record?*
#
#   * the two-term cap (`_cap_to_two_terms`) keeps the head plus the first
#     token that does narrow it, so "Toyota Technical Center USA" searches as
#     TOYOTA TECHNICAL rather than carrying a facility word and a country;
#   * a phrase where NO token narrows it names nothing at all
#     (`has_identifying_token`), which is why "Central Receiving" and
#     "Corporate Headquarters" now produce an empty Search Term 2 instead of
#     shipping the words a thousand other records also carry.
#
# The head is exempt from the test. An organisation name in these registries
# is head-initial — the brand first, the division / product line / legal form
# after — the same property `registry_match.names_agree_by_containment` is
# built on, so the leading token is the one that says WHICH organisation even
# when the word itself is a common one ("General Mills", "Global Technical").

#: Words naming a building or an activity that happens inside one. Every site
#: has receiving, stores and a headquarters; none of them says whose site it is.
_FACILITY_WORDS: frozenset[str] = frozenset({
    "headquarters", "headquarter", "hq", "plant", "plants", "site", "sites",
    "works", "facility", "facilities", "campus", "warehouse", "warehousing",
    "depot", "dock", "docks", "terminal", "interplant", "receiving",
    "shipping", "stores", "store", "storeroom", "stockroom", "manufacturing",
    "production", "distribution", "logistics", "maintenance", "assembly",
    "packaging", "annex", "building", "bldg", "premises", "warehouses",
})

#: Corporate scaffolding and scope qualifiers. "Solutions", "Operations" and
#: "Corporate" attach to any company at all, so they cannot pick one out.
#: "Service"/"Services" is deliberately NOT here — it is half of real unit
#: names ("Food Service"), and an admin service desk is already caught earlier
#: by `is_admin_unit`.
_GENERIC_CORPORATE_WORDS: frozenset[str] = frozenset({
    "corporate", "central", "main", "global", "international", "worldwide",
    "national", "regional", "operations", "operation", "solutions", "systems",
    "partners", "holdings", "enterprises", "ventures", "group", "groups",
    "affiliates", "subsidiary", "subsidiaries", "general", "sales",
    "miscellaneous", "misc", "other", "various", "unknown",
})

#: Place words that are place words in every record. Two-letter US state codes
#: are NOT here — "GE", "GM", "HP", "BD" and "LG" are two-letter organisations,
#: and `in` / `or` / `me` / `de` are ordinary words as often as they are
#: Indiana, Oregon, Maine and Delaware. A state ABBREVIATION is only read as
#: one when the record's own address says so (`_record_geo_tokens`).
_GEO_WORDS: frozenset[str] = frozenset(
    {
        "usa", "us", "america", "americas", "canada", "canadian", "mexico",
        "europe", "european", "asia", "asian", "pacific", "africa",
        "north", "south", "east", "west",
        "northern", "southern", "eastern", "western", "midwest",
    }
    # Full US state names are unambiguous in a way the codes are not.
    | {name.lower() for name in US_REGION_CODES.values()}
)


def _record_geo_tokens(result: dict[str, Any]) -> frozenset[str]:
    """Tokens that merely repeat the record's OWN address.

    "Kellogg Battle Creek MI Plant" carries a city, a state abbreviation and a
    facility word, and the record already states all three in its address
    columns. A handle that repeats them has spent its two terms saying where
    the mail goes instead of who the organisation is, so the address is read
    once here and the words it contains stop counting as identifying.

    This is what lets the state ABBREVIATION be recognised without guessing:
    `MI` is Michigan in this handle because this record's region says Michigan,
    not because two letters were assumed to be a state code.
    """
    out: set[str] = set()
    for key in ("city", "region", "country_region_key"):
        value = (result.get(key) or "").strip().lower()
        if not value:
            continue
        for tok in re.findall(r"[A-Za-z]{2,}", value):
            out.add(tok)
        # A region given as a code also blocks the state's full name, and
        # vice versa, so "MI" and "Michigan" behave identically.
        expanded = US_REGION_CODES.get(value.strip(". "))
        if expanded:
            out.add(value.strip(". "))
            out.update(re.findall(r"[A-Za-z]{2,}", expanded.lower()))
    return frozenset(out)


#: Words that JOIN two terms rather than being one. A connector between the
#: two chosen terms is kept and counts as neither, so "University of Florida"
#: and "Procter & Gamble" are two-term handles, not three.
#:
#: They also change what the token after them means. A place name in trailing
#: position is an address — "Nucor Steel Florida" is a Florida site of Nucor
#: Steel — but a place name after "of" is part of the name itself: the
#: University OF Florida is not a Florida branch of some University. So a
#: token directly after "of"/"for" identifies, whatever list it appears on.
_CONNECTORS: frozenset[str] = frozenset({"&", "of", "for"})

#: Function words that carry no identity. `_TERM2_STOPWORDS` covers the
#: articles and conjunctions; these are the prepositions and connectives that
#: turn up in SAP free text ("Interplant Site Off E", "Bldg 4 Per Contract").
_FUNCTION_WORDS: frozenset[str] = frozenset({
    "off", "out", "per", "via", "plus", "from", "with", "by", "into", "onto",
    "over", "under", "near", "upon", "than", "then", "also", "etc", "and/or",
    "no", "not", "new", "old", "all", "any", "its",
})


def _token_key(token: str) -> str:
    """The comparable form of a handle token — lowercased, outer punctuation
    removed. `&` survives because it is part of the handle, not punctuation."""
    return token.strip(" .,;:()[]{}\"'").lower()


def is_identifying_token(
    token: str,
    geo: "frozenset[str] | None" = None,
    *,
    after_connector: bool = False,
) -> bool:
    """True when *token* narrows a search to this organisation.

    False for legal forms, structural unit words, facility words, corporate
    scaffolding, place words, the record's own address, bare stopwords, and
    anything with no letters in it (a lone "&" or a stray "E").

    *after_connector* marks a token that directly follows "of" or "for", which
    exempts it from every vocabulary above except the empty checks. See
    `_CONNECTORS`: "Florida" is an address in "Nucor Steel Florida" and the
    name itself in "University of Florida", and the connector is what tells
    the two apart.
    """
    key = _token_key(token)
    if not key or not any(c.isalpha() for c in key):
        return False
    if len(key) == 1:
        return False
    if after_connector:
        return True
    return not (
        key in _ST1_LEGAL_SUFFIXES
        or key in _UNIT_KEYWORDS
        or key in _FACILITY_WORDS
        or key in _GENERIC_CORPORATE_WORDS
        or key in _GEO_WORDS
        or key in _TERM2_STOPWORDS
        or key in _FUNCTION_WORDS
        or key in (geo or frozenset())
    )


def has_identifying_token(
    text: str | None, geo: "frozenset[str] | None" = None,
) -> bool:
    """True when *text* contains at least one token that names something.

    "Central Receiving", "Corporate Headquarters" and "Stores" contain none:
    every word is a qualifier or a facility function, and the phrase describes
    a loading bay rather than naming a unit. Shipping one as a search term
    hands a reviewer a query that matches every large employer in the country.
    """
    if not text or not text.strip():
        return False
    tokens = text.split()
    return any(
        is_identifying_token(
            tok, geo,
            after_connector=i > 0 and _token_key(tokens[i - 1]) in ("of", "for"),
        )
        for i, tok in enumerate(tokens)
    )


def _cap_to_two_terms(
    text: str, geo: "frozenset[str] | None" = None,
) -> str | None:
    """Reduce *text* to at most two terms: the head, plus the first token after
    it that identifies something.

    The head is taken as-is (see the head-initial note above); leading articles
    and legal forms are stepped over to find it. The second term is the first
    IDENTIFYING token, so the scaffolding between them is skipped rather than
    occupying the slot — "Novartis Institute Biomedical" reaches NOVARTIS
    BIOMEDICAL, not NOVARTIS INSTITUTE. When nothing after the head identifies
    anything, the head stands alone: "Kellogg North America" is KELLOGG,
    because NORTH is not the half of that name worth searching.

    An `&` that JOINS the two chosen terms is kept — "Procter & Gamble" is one
    two-term handle, not three terms — and counts towards neither.

        'Toyota Technical Center USA'      → 'Toyota Technical'
        'Robert Bosch Fuel Systems'        → 'Robert Bosch'
        'Kellogg North America'            → 'Kellogg'
        'Procter & Gamble'                 → 'Procter & Gamble'
        'The Goodyear Tire & Rubber Co'    → 'Goodyear Tire'
    """
    tokens = [t for t in text.split() if t.strip()]
    # Step over articles and legal forms to find the head. `_ARTICLES` is not
    # a separate set: "the" is already a stopword everywhere else here.
    head_idx = None
    for i, tok in enumerate(tokens):
        key = _token_key(tok)
        if not key or not any(c.isalpha() for c in key):
            continue
        if key in _TERM2_STOPWORDS or key in _ST1_LEGAL_SUFFIXES:
            continue
        head_idx = i
        break
    if head_idx is None:
        return None

    head = tokens[head_idx]
    for j in range(head_idx + 1, len(tokens)):
        prev = _token_key(tokens[j - 1]) if j > 0 else ""
        if not is_identifying_token(
            tokens[j], geo, after_connector=prev in ("of", "for"),
        ):
            continue
        # A connector is kept only when it sits directly between the two terms
        # being joined. "Massachusetts Institute of Technology" drops its "of"
        # because the word before it is Institute, not the head.
        if prev in _CONNECTORS and j - 1 == head_idx + 1:
            return f"{head} {tokens[j - 1]} {tokens[j]}"
        return f"{head} {tokens[j]}"
    return head


def _normalise_term(
    term: str | None, geo: "frozenset[str] | None" = None,
) -> str | None:
    """Terminal normalisation applied to BOTH search terms: strip, collapse
    internal whitespace, **cap at two terms**, uppercase, truncate to 32 chars
    on a word boundary (SAP SORT1/SORT2 width).

    The cap lives here because this is the one function both chains pass
    through, so neither can grow a third term by adding a branch. It is a cap,
    not a truncation: `_cap_to_two_terms` chooses WHICH two, and the 32-char
    width remains as the field's own hard limit behind it.
    """
    if not term or not term.strip():
        return None
    s = re.sub(r"\s+", " ", term.strip())
    s = _cap_to_two_terms(s, geo) or s
    return _truncate_word_boundary(s.upper(), 32) or None


def _fill_to_width(text: str | None, width: int = 32) -> str | None:
    """Greedily take significant words (stopwords dropped) until adding the next
    would exceed *width*. The first significant word is always included."""
    if not text:
        return None
    out: list[str] = []
    length = 0
    for tok in re.findall(r"[A-Za-z0-9&\-]+", text):
        # `&` is in `_TERM2_STOPWORDS` — correctly, for the acronym and
        # overlap tests that set was written for — but here it is part of the
        # handle: dropping it turned "Truck & Bus" into "TRUCK BUS". Kept only
        # between two words, never leading, and trimmed if nothing follows.
        if tok == "&":
            if out:
                out.append(tok)
                length += 2
            continue
        if tok.lower() in _TERM2_STOPWORDS:
            continue
        add = len(tok) + (1 if out else 0)
        if out and length + add > width:
            break
        out.append(tok)
        length += add
    while out and out[-1] == "&":
        out.pop()
    return " ".join(out) if out else None


# Structural unit words that carry no search value inside a unit handle.
# "Chemistry Dept" is searched as CHEMISTRY, "Div of Analytical Sciences" as
# ANALYTICAL SCIENCES. These are stripped wherever they appear in the phrase,
# not only at its edges (clean_name2_phrase only handles the edges), so a
# mid-string "Dept"/"Div" cannot reach the output either.
_UNIT_KEYWORDS = {
    "department", "departments", "dept", "depts", "depart",
    "division", "divisions", "div",
    "section", "sections", "unit", "units", "branch", "branches",
    "school", "schools", "college", "colleges", "faculty", "faculties",
    "office", "offices", "institute", "institutes", "inst",
    "center", "centre", "centers", "centres", "ctr",
    "laboratory", "laboratories", "lab", "labs",
    "group", "groups", "grp",
}


def _strip_unit_keywords(text: str | None) -> str | None:
    """Drop every structural unit word from *text*.

    ``'Chemistry Dept'``                  → ``'Chemistry'``
    ``'Analytical Sciences Division'``    → ``'Analytical Sciences'``
    ``'Office of Research'``              → ``'of Research'`` (stopword dropped later)
    ``'Laboratory'``                      → ``None`` (nothing meaningful left)
    """
    if not text:
        return None
    kept = [
        tok for tok in text.split()
        if tok.strip(" .,-()").lower() not in _UNIT_KEYWORDS
    ]
    out = " ".join(kept).strip(" ,-.")
    if not out:
        return None
    # A residue of nothing but stopwords ("of", "for the") is no handle either.
    if not any(
        t.strip(" .,-()").lower() not in _TERM2_STOPWORDS for t in out.split()
    ):
        return None
    return out


def _name2_is_unit_phrase(name2: str) -> bool:
    """True when name2 reads as a department/sub-unit (has a unit prefix/suffix
    or is a granular unit) — i.e. a unit of the institution, not an institution."""
    low = name2.strip().lower()
    if any(low == p or low.startswith(p + " ") for p in _NAME2_PREFIXES):
        return True
    if any(low == s or low.endswith(" " + s) for s in _NAME2_SUFFIXES):
        return True
    return is_granular_unit(name2)


def _subdomain_acronym(
    dept_domain: str | None, base_domain: str | None, name2: str | None,
) -> str | None:
    """The subdomain prefix as an acronym — only when it genuinely is one:
    2–6 chars, NOT a leading prefix of any Name 2 token, and its letters equal
    the initials of Name 2's significant words.

    ``eecs.mit.edu`` + ``Electrical Engineering and Computer Science`` → ``EECS``
    ``chem.ufl.edu`` + ``Department of Chemistry`` → ``None`` (truncated word)
    ``york.cuny.edu`` + ``Department of Geology``  → ``None`` (letters ≠ initials)
    """
    if not dept_domain or not name2:
        return None
    host = dept_domain.strip().lower()
    if "://" in host:
        host = (urlparse(host).hostname or "").lower()
    for pref in ("www.", "web."):
        if host.startswith(pref):
            host = host[len(pref):]
    base = (base_domain or "").strip().lower() or None
    if not base or not host.endswith("." + base):
        return None
    prefix = host[: -len("." + base)].split(".")[0]
    if not (2 <= len(prefix) <= 6):
        return None
    core = clean_name2_phrase(name2) or name2
    tokens = [
        t for t in re.findall(r"[A-Za-z]+", core)
        if t.lower() not in _TERM2_STOPWORDS
    ]
    if any(seg_matches_needle(prefix, t) for t in tokens):
        return None  # truncated word ("chem" ← "chemistry"), not an acronym
    if not acronym_matches_name(prefix, core):
        return None
    return prefix.upper()


def _acronym_names_the_record(acronym: str | None, name1: str) -> bool:
    """True when *acronym* is an acronym OF *name1* (or there is no name).

    Fix D(3). ``_ror_acronym`` is an acronym of whatever ROR record supplied
    it, and until this check existed nothing tied that record to the name the
    output actually ships. On the BIC Corp row ROR matched Centene on one run
    and Balchem on the next; GLEIF wrote the correct "BIC CORPORATION" into
    Name 1, and Search Term 1 was derived from the ROR candidate that had
    already been contradicted.

    ``acronym_matches_name`` is the same test ``tier1_ror`` already applies
    when it chooses which of several ROR acronyms is the current one — no new
    machinery, and no threshold at all.

    A blank Name 1 passes: there is nothing for the acronym to disagree WITH,
    and "absence is not conflict" is the rule this codebase applies everywhere
    else it compares two sources (the locality comparator, the cross-source
    gate). A record with an acronym and no Name 1 keeps the handle it had.
    """
    acronym = (acronym or "").strip()
    if not acronym:
        return False
    return not name1 or acronym_matches_name(acronym, name1)


def _derive_search_term_1(result: dict[str, Any]) -> str | None:
    """search_term_1 chain: ROR acronym → TLD-stripped domain → a handle
    derived from the enriched Name 1 → None — with every link in the chain
    required to name the SAME organisation the record ships.

    Every input is a post-enrichment value. The pre-enrichment SAP Search
    Term 1 is deliberately NOT in this chain: it is customer-maintained free
    text, and once enrichment has produced an official name, a domain or a
    registry acronym, echoing the input would ship a stale handle for a
    record whose name we just corrected.

    Fix D(3) adds the missing condition. The first two links are handles for
    whichever source supplied them, and the record's Name 1 may by now have
    come from a different source — or the consistency gate may have removed
    the source that supplied them. So each is used only if it is a handle for
    ``name1_enriched``; otherwise the chain falls through to Name 1 itself,
    which is by construction the identity that survived every gate.
    """
    name1 = (result.get("name1_enriched") or "").strip()
    ror_acronym = (result.get("_ror_acronym") or "").strip() or None
    if ror_acronym and _acronym_names_the_record(ror_acronym, name1):
        return ror_acronym
    domain = (result.get("domain") or "").strip() or None
    if domain:
        # No second name check on the domain, deliberately. The domain a
        # losing registry supplied is already GONE by the time this runs —
        # `enrichment.consistency` nulls it with the identifier, which is the
        # right place for that decision because it is the place that knows
        # which source lost. Re-asking the name question here instead would
        # reject domains the ownership guard legitimately accepted on
        # registry provenance or on email evidence rather than on name
        # similarity (`uni-tuebingen.de` for "University of Tübingen" scores
        # below the guard's own threshold and is still the university's
        # domain).
        return strip_tld(domain)
    # Rule 3 — a handle derived from the ENRICHED Name 1, and only from it.
    # `name1_enriched` IS the Name 1 column of the response: finalise has
    # already backfilled it from the preprocessed input wherever no tier
    # changed it, so a blank here means the output ships no institution at
    # all. There is then nothing to hand a search handle for, and reaching
    # back to `name1_original` would emit a term for a name the record does
    # not carry — the "ATTN CHARLES FARBER / MIT" case, where preprocessing
    # moved the person to Contact, no institution survived, and the raw input
    # string was still shipped as Search Term 1.
    #
    # This also subsumes the UC 7 person guard: a person lifted out of Name 1
    # leaves `name1_enriched` blank → None, while a Stage-2b-resolved
    # affiliation puts a real institution there → a handle is derived from it.
    if not name1:
        return None
    return _name1_text_handle(name1) or name1


def _derive_search_term_2(result: dict[str, Any]) -> str | None:
    """search_term_2 chain: admin override → subdomain acronym → Name 2 phrase
    (filled to 32) → department-domain host → None, with DBA and field-swap
    guards on Name 2.

    A Name 2 that identifies nothing returns None rather than a handle. Every
    large site has a central receiving bay, a corporate headquarters and a
    stores desk, so those words describe where a delivery goes and never which
    unit the record is — and a search term that matches every large employer
    in the country is worse than an empty field, because the empty field does
    not claim to have found something. `has_identifying_token` is the test;
    the admin override above it still runs first, so an accounts-payable desk
    keeps its ADMIN handle instead of being emptied.
    """
    geo = _record_geo_tokens(result)
    domain = (result.get("domain") or "").strip() or None
    # Enriched Name 2 only. finalise() has already retained the input value in
    # the enriched slot wherever no tier changed it, so a blank enriched slot
    # here means enrichment deliberately emptied the field (an address, an
    # email, a contact name lifted out by preprocessing) — the pre-enrichment
    # original must not be mined for a handle.
    name2 = (result.get("name2_enriched") or "").strip()

    # Field-content guards on Name 2.
    if name2:
        dba = result.get("_dba_values") or {}
        if "name2" in dba:
            name2 = ""  # UC 11 DBA trade name — never a unit handle
        elif looks_like_research_institution(name2) and not _name2_is_unit_phrase(name2):
            # An institution in the Name 2 slot → probable field swap, so it
            # is not a unit handle. Search-term derivation does not raise
            # review flags; enrichment.flags is the single flag authority.
            name2 = ""

    # 0. Admin override (accounts payable, finance, billing, …).
    if name2 and is_admin_unit(name2):
        return "ADMIN"

    # 0b. A phrase built entirely from facility and scaffolding words names no
    #     unit. Checked after the admin override, which recognises a real desk,
    #     and before the acronym and phrase branches, so neither can rebuild a
    #     handle out of words that were just found to identify nothing.
    if name2 and not has_identifying_token(name2, geo):
        name2 = ""

    dept_domain = (result.get("department_domain") or "").strip() or None

    # 1. Subdomain acronym from the department domain, when it truly is one.
    sub = _subdomain_acronym(dept_domain, domain, name2)
    if sub:
        return sub

    # 2. Name 2 phrase (explicit parenthetical acronym, else cleaned, stripped
    #    of structural unit words, and filled to the field width).
    if name2:
        paren = _PAREN_ACRONYM_RE.search(name2)
        if paren:
            return paren.group(1)
        cleaned = clean_name2_phrase(name2) or name2
        # A phrase that is nothing but unit words ("Laboratory", "Division")
        # names no unit — fall through rather than ship the keyword.
        core = _strip_unit_keywords(cleaned)
        filled = _fill_to_width(core, 32) if core else None
        if filled:
            return filled

    # 3. Department-domain fallback (host prefix / TLD-stripped host), unless
    #    the host segment is itself a structural or generic word — "dept" out
    #    of dept.example.edu is not a unit handle.
    if dept_domain:
        handle = _dept_domain_to_search_term(dept_domain, domain)
        if handle and handle.strip(" ./-").lower() not in (
            _UNIT_KEYWORDS | _GENERIC_PATH_SEGMENTS
        ):
            return handle
    return None


def derive_search_terms(
    result: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Compute ``(search_term_1, search_term_2)`` from a finalised result dict.

    Every input to both chains is a POST-enrichment value — the enriched
    names, the resolved domain, the registry acronym. The pre-enrichment SAP
    Search Term columns are never consulted.

    search_term_1 (institution handle):
        1. result["_ror_acronym"]     (currency-checked in tier1_ror)
        2. strip_tld(result["domain"])
        3. handle derived from the enriched Name 1 — never from
           name1_original, so a record whose Name 1 output is null gets no
           search term either
        4. None

    search_term_2 (unit handle):
        0. "ADMIN"                     (UC 6 admin desk)
        1. subdomain acronym of department_domain, when genuinely an acronym
        2. enriched Name 2 phrase, cleaned, stripped of structural unit words
           (dept / div / school / centre / lab …) and filled to 32 chars
        3. department_domain host prefix / TLD-stripped host, unless that
           segment is itself a structural or generic word
        4. None
        (guards: UC 11 DBA and institution-in-Name-2 field swap block Name 2;
         a Name 2 with no identifying token at all — "Central Receiving",
         "Corporate Headquarters", "Stores" — is emptied before the chain runs)

    Both terms then pass terminal normalisation: capped at two terms
    (`_cap_to_two_terms`), uppercased, trimmed, ≤32 chars.
    """
    geo = _record_geo_tokens(result)
    return (
        _normalise_term(_derive_search_term_1(result), geo),
        _normalise_term(_derive_search_term_2(result), geo),
    )
