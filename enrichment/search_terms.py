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


def derive_department_domain(
    name2: str | None,
    source_url: str | None,
    base_domain: str | None,
) -> str | None:
    """Return the unit-scoped host (with TLD) from *source_url*.

    Accepts both subdomains of the institution domain and related but
    distinct registrable domains (e.g. medical schools that live on a
    separate brand domain like ``hopkinsmedicine.org`` for Johns
    Hopkins). The only thing rejected is the bare institution domain
    itself — that's not department-specific.

    ``source_url='https://cs.mit.edu/people', base='mit.edu'``           →
        ``'cs.mit.edu'``
    ``source_url='https://hopkinsmedicine.org/...', base='jhu.edu'``    →
        ``'hopkinsmedicine.org'``
    ``source_url='https://mit.edu/cs/people', base='mit.edu'``           →
        ``None`` (bare institution host — no dept-specific URL)
    ``name2`` empty                                                      →
        ``None``

    Independent from ``search_term_2``: this field is the *real*
    department domain (full host), not a compacted search handle.
    """
    if not name2 or not name2.strip():
        return None
    if not source_url or not base_domain:
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

    base = base_domain.lower().strip()
    if not base or host == base:
        return None
    return host


def derive_search_terms(
    result: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Compute ``(search_term_1, search_term_2)`` from a finalised result dict.

    search_term_1 (institution handle, acronym-shaped):
        1. result["_ror_acronym"]                       (ROR acronym variant)
        2. strip_tld(result["domain"])                  ('mit.edu' → 'mit')
        3. derive_acronym(name1_enriched or name1_original)
        4. None

    search_term_2 (unit handle, text-phrase-shaped; null when name2 absent):
        1. Parenthetical acronym in name2              (e.g. "(CSAIL)" → "CSAIL")
        2. clean_name2_phrase(name2)                   (strip "Department of …" /
                                                        "… Department", title-case)
        3. unit_domain_or_path(source_url, domain)     (last-resort fallback)
        4. None
    """
    ror_acronym = (result.get("_ror_acronym") or "").strip() or None
    domain = (result.get("domain") or "").strip() or None
    name1 = result.get("name1_enriched") or result.get("name1_original")

    search_term_1: str | None
    if ror_acronym:
        search_term_1 = ror_acronym
    elif domain:
        # Just the name part of the domain — no TLD ('mit.edu' → 'mit').
        search_term_1 = strip_tld(domain)
    else:
        search_term_1 = derive_acronym(name1)

    name2_enriched = (result.get("name2_enriched") or "").strip()
    name2_original = (result.get("name2_original") or "").strip()
    name2 = name2_enriched or name2_original

    search_term_2: str | None = None
    if name2:
        paren = _PAREN_ACRONYM_RE.search(name2)
        if paren:
            search_term_2 = paren.group(1)
        else:
            search_term_2 = clean_name2_phrase(name2)
            if not search_term_2:
                search_term_2 = unit_domain_or_path(
                    result.get("source_url"), domain
                )

    return search_term_1, search_term_2
