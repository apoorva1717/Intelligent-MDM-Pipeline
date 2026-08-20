"""Compact search-handle derivation for the enrichment response.

Produces two short, search-friendly strings per record so downstream
consumers can re-query (Google, internal search, dedup keys) without
re-deriving abbreviations or domains themselves.

``search_term_1`` mirrors *name1* (an institution). Institutions
typically have well-known acronyms (MIT, UCLA, NASA), so the rule is
acronym-first, domain fallback, caps-derived acronym last.

``search_term_2`` mirrors *name2* (a department / unit / lab). Units
rarely have well-known acronyms, so the rule is instead a
ready-to-search **text phrase**: extract an explicit parenthetical
acronym when given, otherwise strip the generic unit prefix/suffix
("Department of …", "… Department") to reveal the meaningful core,
otherwise return the cleaned input verbatim. A unit-scoped
subdomain/path is kept as a last-resort fallback for the rare case
where the textual form collapses to empty.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from enrichment.preprocess import _extract_addresses
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
    initials: list[str] = []
    for token in _WORD_RE.findall(name):
        if not token:
            continue
        if token.lower() in _ACRONYM_STOPWORDS:
            continue
        first = token[0]
        if not first.isalpha():
            continue
        if not first.isupper():
            return None
        initials.append(first.upper())

    if len(initials) < 2:
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
    for tok in re.findall(r"[A-Za-z]+", text):
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
    """First two significant words of Name 1 (stopwords + legal suffixes
    dropped) — the required-handle fallback for search_term_1."""
    words: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9&]+", name1):
        low = tok.lower()
        if low in _TERM2_STOPWORDS or low in _ST1_LEGAL_SUFFIXES:
            continue
        words.append(tok)
        if len(words) >= 2:
            break
    return " ".join(words) if words else None


def _truncate_word_boundary(s: str, width: int = 32) -> str:
    """Truncate *s* to *width* chars on a word boundary (back off to the last
    space); a single over-long word is hard-cut."""
    if len(s) <= width:
        return s
    idx = s.rfind(" ", 0, width + 1)
    if idx <= 0:
        return s[:width]
    return s[:idx].rstrip()


def _normalise_term(term: str | None) -> str | None:
    """Terminal normalisation applied to BOTH search terms: strip, collapse
    internal whitespace, uppercase, truncate to 32 chars on a word boundary
    (SAP SORT1/SORT2 width)."""
    if not term or not term.strip():
        return None
    s = re.sub(r"\s+", " ", term.strip()).upper()
    return _truncate_word_boundary(s, 32) or None


def _fill_to_width(text: str | None, width: int = 32) -> str | None:
    """Greedily take significant words (stopwords dropped) until adding the next
    would exceed *width*. The first significant word is always included."""
    if not text:
        return None
    out: list[str] = []
    length = 0
    for tok in re.findall(r"[A-Za-z0-9&\-]+", text):
        if tok.lower() in _TERM2_STOPWORDS:
            continue
        add = len(tok) + (1 if out else 0)
        if out and length + add > width:
            break
        out.append(tok)
        length += add
    return " ".join(out) if out else None


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


def _derive_search_term_1(result: dict[str, Any]) -> str | None:
    """search_term_1 chain: ROR acronym (currency-checked upstream) → TLD-
    stripped domain → required handle (SAP term / Name 1 words) when Name 1 is
    usable → None."""
    ror_acronym = (result.get("_ror_acronym") or "").strip() or None
    if ror_acronym:
        return ror_acronym
    domain = (result.get("domain") or "").strip() or None
    if domain:
        return strip_tld(domain)
    # Rule 3 — required handle, only when Name 1 is USABLE: non-blank AND not an
    # unresolved person. A person extracted by UC 7 leaves name1_enriched blank
    # (name1_original still holds the person name); a Stage-2b-resolved
    # affiliation puts an institution in name1_enriched → usable.
    name1_enriched = (result.get("name1_enriched") or "").strip()
    name1 = name1_enriched or (result.get("name1_original") or "").strip()
    was_person = bool(result.get("_name1_was_person"))
    usable = bool(name1) and not (was_person and not name1_enriched)
    if not usable:
        return None
    original = (result.get("_search_term_1_original") or "").strip()
    if original:
        return original
    return _name1_text_handle(name1) or name1


def _derive_search_term_2(result: dict[str, Any]) -> str | None:
    """search_term_2 chain: admin override → subdomain acronym → Name 2 phrase
    (filled to 32) → department-domain host → None, with DBA and field-swap
    guards on Name 2."""
    domain = (result.get("domain") or "").strip() or None
    name2 = (result.get("name2_enriched") or "").strip()
    if not name2:
        # Original name2 — but not when it was actually a street address
        # ("104 Rhines Hall") that enrichment moved into a street field.
        orig = (result.get("name2_original") or "").strip()
        if orig:
            _addrs, _remainder = _extract_addresses(orig)
            if not (_addrs and not _remainder.strip()):
                name2 = orig

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

    dept_domain = (result.get("department_domain") or "").strip() or None

    # 1. Subdomain acronym from the department domain, when it truly is one.
    sub = _subdomain_acronym(dept_domain, domain, name2)
    if sub:
        return sub

    # 2. Name 2 phrase (explicit parenthetical acronym, else cleaned + filled).
    if name2:
        paren = _PAREN_ACRONYM_RE.search(name2)
        if paren:
            return paren.group(1)
        cleaned = clean_name2_phrase(name2) or name2
        filled = _fill_to_width(cleaned, 32)
        if filled:
            return filled

    # 3. Department-domain fallback (host prefix / TLD-stripped host).
    if dept_domain:
        return _dept_domain_to_search_term(dept_domain, domain)
    return None


def derive_search_terms(
    result: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Compute ``(search_term_1, search_term_2)`` from a finalised result dict.

    search_term_1 (institution handle):
        1. result["_ror_acronym"]     (currency-checked in tier1_ror)
        2. strip_tld(result["domain"])
        3. required handle (SAP Search Term 1, else Name 1 words) — only when
           Name 1 is usable (non-blank AND not an unresolved person)
        4. None

    search_term_2 (unit handle):
        0. "ADMIN"                     (UC 6 admin desk)
        1. subdomain acronym of department_domain, when genuinely an acronym
        2. Name 2 phrase, cleaned and filled to 32 chars
        3. department_domain host prefix / TLD-stripped host
        4. None
        (guards: UC 11 DBA and institution-in-Name-2 field swap block Name 2)

    Both terms then pass terminal normalisation: uppercase, trimmed, ≤32 chars.
    """
    return (
        _normalise_term(_derive_search_term_1(result)),
        _normalise_term(_derive_search_term_2(result)),
    )
