"""Text cleaning, domain extraction, and string normalisation helpers."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def clean_whitespace(text: str | None) -> str | None:
    """Collapse consecutive whitespace into single spaces and strip."""
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()


def is_blank(value: str | None) -> bool:
    """Return True if value is None or contains only whitespace."""
    return value is None or value.strip() == ""


def extract_domain(url: str | None) -> str | None:
    """Extract the registrable domain from a URL.

    'https://web.mit.edu/path' → 'mit.edu'
    'https://www.example.co.uk/page' → 'example.co.uk'
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.split(".")
        if len(parts) >= 2:
            # Handle two-part TLDs like .co.uk, .ac.jp
            known_two_part = {"co.uk", "ac.uk", "org.uk", "ac.jp", "co.jp",
                              "com.au", "edu.au", "org.au", "ac.in", "co.in",
                              "com.br", "org.br", "edu.br", "ac.nz", "co.nz",
                              "ac.za", "co.za"}
            if len(parts) >= 3:
                candidate = f"{parts[-2]}.{parts[-1]}"
                if candidate in known_two_part:
                    return f"{parts[-3]}.{parts[-2]}.{parts[-1]}"
            return f"{parts[-2]}.{parts[-1]}"
        return hostname
    except Exception:
        return None


def normalise_name(name: str | None) -> str | None:
    """Lowercase and strip for comparison purposes."""
    if not name:
        return None
    return name.strip().lower()


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending '…' if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


URL_PEOPLE_SIGNALS = [
    "people", "faculty", "staff", "person", "profile",
    "directory", "team", "researcher", "member", "bio",
]

SNIPPET_PEOPLE_SIGNALS = [
    "professor", "researcher", "scientist", "department",
    "phd", "dr.", "principal investigator",
]


def score_search_result(url: str, snippet: str) -> int:
    """Score a search result for likelihood of being a person/faculty page."""
    score = 0
    url_lower = url.lower()
    snippet_lower = snippet.lower()
    for signal in URL_PEOPLE_SIGNALS:
        if signal in url_lower:
            score += 1
    for signal in SNIPPET_PEOPLE_SIGNALS:
        if signal in snippet_lower:
            score += 1
    return score


def build_affiliation_string(record: object, include_name2: bool = False) -> str:
    """Build a rich affiliation string from all available record fields.

    Retained for backward compatibility / logging. The primary ROR lookup
    now uses the query parameter with a country filter instead.
    """
    parts: list[str] = []
    if getattr(record, "name1", None):
        parts.append(record.name1)
    if include_name2 and getattr(record, "name2", None):
        parts.append(record.name2)
    if getattr(record, "city", None):
        parts.append(record.city)
    if getattr(record, "state", None):
        parts.append(record.state)
    if getattr(record, "country", None):
        parts.append(record.country)
    return " ".join(p for p in parts if p and p.strip())


def safe_enriched_value(value: str | None) -> str | None:
    """Return value.strip() if non-empty, else None. Never returns empty string."""
    if value is not None and str(value).strip():
        return str(value).strip()
    return None


# ISO 3166-1 alpha-2 country code mappings for common research countries.
# Handles 2-letter codes, 3-letter codes, and full English names.
_COUNTRY_TO_ISO: dict[str, str] = {
    # Already ISO alpha-2 (passthrough)
    "US": "US", "DE": "DE", "GB": "GB", "FR": "FR", "JP": "JP",
    "CN": "CN", "CH": "CH", "CA": "CA", "AU": "AU", "IN": "IN",
    "KR": "KR", "IT": "IT", "ES": "ES", "NL": "NL", "SE": "SE",
    "BR": "BR", "IL": "IL", "AT": "AT", "BE": "BE", "DK": "DK",
    "FI": "FI", "NO": "NO", "PL": "PL", "PT": "PT", "RU": "RU",
    "SG": "SG", "TW": "TW", "MX": "MX", "NZ": "NZ", "IE": "IE",
    "CZ": "CZ", "HK": "HK", "ZA": "ZA", "HU": "HU", "TR": "TR",
    "CL": "CL", "CO": "CO", "AR": "AR", "MY": "MY", "TH": "TH",
    "GR": "GR", "RO": "RO", "SK": "SK", "SI": "SI", "HR": "HR",
    "BG": "BG", "LT": "LT", "LV": "LV", "EE": "EE", "LU": "LU",
    # ISO alpha-3 → alpha-2
    "USA": "US", "DEU": "DE", "GBR": "GB", "FRA": "FR", "JPN": "JP",
    "CHN": "CN", "CHE": "CH", "CAN": "CA", "AUS": "AU", "IND": "IN",
    "KOR": "KR", "ITA": "IT", "ESP": "ES", "NLD": "NL", "SWE": "SE",
    "BRA": "BR", "ISR": "IL", "AUT": "AT", "BEL": "BE", "DNK": "DK",
    "FIN": "FI", "NOR": "NO", "POL": "PL", "PRT": "PT", "RUS": "RU",
    "SGP": "SG", "TWN": "TW", "MEX": "MX", "NZL": "NZ", "IRL": "IE",
    "CZE": "CZ", "HKG": "HK", "ZAF": "ZA", "HUN": "HU", "TUR": "TR",
    # Common English names → alpha-2
    "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
    "GERMANY": "DE", "DEUTSCHLAND": "DE",
    "UNITED KINGDOM": "GB", "UK": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB",
    "FRANCE": "FR", "JAPAN": "JP", "CHINA": "CN",
    "SWITZERLAND": "CH", "CANADA": "CA", "AUSTRALIA": "AU",
    "INDIA": "IN", "SOUTH KOREA": "KR", "KOREA": "KR",
    "ITALY": "IT", "SPAIN": "ES", "NETHERLANDS": "NL",
    "SWEDEN": "SE", "BRAZIL": "BR", "ISRAEL": "IL",
    "AUSTRIA": "AT", "BELGIUM": "BE", "DENMARK": "DK",
    "FINLAND": "FI", "NORWAY": "NO", "POLAND": "PL",
    "PORTUGAL": "PT", "RUSSIA": "RU", "RUSSIAN FEDERATION": "RU",
    "SINGAPORE": "SG", "TAIWAN": "TW", "MEXICO": "MX",
    "NEW ZEALAND": "NZ", "IRELAND": "IE", "CZECH REPUBLIC": "CZ",
    "CZECHIA": "CZ", "HONG KONG": "HK", "SOUTH AFRICA": "ZA",
    "HUNGARY": "HU", "TURKEY": "TR", "TÜRKIYE": "TR",
    "CHILE": "CL", "COLOMBIA": "CO", "ARGENTINA": "AR",
    "MALAYSIA": "MY", "THAILAND": "TH", "GREECE": "GR",
    "ROMANIA": "RO", "SLOVAKIA": "SK", "SLOVENIA": "SI",
    "CROATIA": "HR", "BULGARIA": "BG", "LUXEMBOURG": "LU",
    "SAUDI ARABIA": "SA", "UAE": "AE",
    "UNITED ARAB EMIRATES": "AE", "EGYPT": "EG",
    "PAKISTAN": "PK", "PHILIPPINES": "PH", "INDONESIA": "ID",
    "VIETNAM": "VN", "NIGERIA": "NG", "KENYA": "KE",
    "SCOTLAND": "GB", "WALES": "GB", "NORTHERN IRELAND": "GB",
}


# Common abbreviation → full form mappings used in search queries.
# Uses (?=\s|$) instead of \b after optional period so "Dept." is fully
# consumed (including the dot) before the space.
_ABBREV_MAP: dict[str, str] = {
    r"\bDept\.?(?=\s|$)": "Department",
    r"\bUniv\.?(?=\s|$)": "University",
    r"\bUni(?=\s|$)": "University",
    r"\bLab\.?(?=\s|$)": "Laboratory",
    r"\bInst\.?(?=\s|$)": "Institute",
    r"\bCtr\.?(?=\s|$)": "Center",
    r"\bChem\.?(?=\s|$)": "Chemistry",
    r"\bBiol\.?(?=\s|$)": "Biology",
    r"\bPhys\.?(?=\s|$)": "Physics",
    r"\bSci\.?(?=\s|$)": "Science",
    r"\bEng\.?(?=\s|$)": "Engineering",
    r"\bMed\.?(?=\s|$)": "Medicine",
    r"\bOrg\.?(?=\s|$)": "Organization",
    r"\bAssoc\.?(?=\s|$)": "Association",
    r"\bTech\.?(?=\s|$)": "Technology",
    r"\bNatl\.?(?=\s|$)": "National",
    r"\bIntl\.?(?=\s|$)": "International",
    r"\bDiv\.?(?=\s|$)": "Division",
}

_COMPILED_ABBREVS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat, re.IGNORECASE), full)
    for pat, full in _ABBREV_MAP.items()
]


def expand_abbreviations(text: str | None) -> str | None:
    """Expand common academic/institutional abbreviations.

    "Dept of Radiology" → "Department of Radiology"
    "Univ of Florida"   → "University of Florida"
    """
    if not text or not text.strip():
        return text
    result = text
    for pattern, replacement in _COMPILED_ABBREVS:
        result = pattern.sub(replacement, result)
    return result.strip()


# Unit-type canonicalisation — maps every variant wording to the
# "X of Y" construction.  Keyed by the unit word; the tuple is
# (regex for suffix form, regex for prefix form).  Applied after
# abbreviation expansion, so "Dept" has already become "Department".
_UNIT_CANONICAL_FORMS: list[tuple[str, str]] = [
    ("Department", "of"),
    ("Division", "of"),
    ("School", "of"),
    ("Faculty", "of"),
    ("College", "of"),
    ("Institute", "of"),
    ("Center", "for"),
    ("Centre", "for"),
    ("Laboratory", "of"),
]


# Specific unit words — these name the actual organizational unit
# a person belongs to.  Parent-level words (School, College, Faculty)
# are handled separately because they usually describe the enclosing
# institution rather than the person's specific affiliation.
_SPECIFIC_UNIT_WORDS = {"Department", "Division", "Institute", "Center", "Centre", "Laboratory"}
_PARENT_UNIT_WORDS = {"School", "College", "Faculty"}


_RESEARCH_NAME_SIGNALS_RE = re.compile(
    r"\b(?:University|College|Institute|Hospital|Clinic|Research|"
    r"Medical\s+School|School\s+of|Faculty\s+of|College\s+of|"
    r"Laboratory|Observatory|Academy|"
    r"Health\s+System|Health\s+Center|Regional\s+Health|"
    r"Medical\s+Center|Cancer\s+Center|"
    r"Schule|Universit[aä]t|Université|Universidade)\b",
    re.IGNORECASE,
)


def looks_like_research_institution(name: str | None) -> bool:
    """Heuristic: does *name* read as a research institution?

    Used to route ROR-miss cases. Research-institution names that
    didn't match ROR should NOT be passed to the company canonical
    LLM (which would return a legal entity name like 'President and
    Fellows of Harvard College'). They should pass through and be
    flagged for manual review instead.
    """
    if not name or not name.strip():
        return False
    return bool(_RESEARCH_NAME_SIGNALS_RE.search(name))


def is_granular_unit(text: str | None) -> bool:
    """Return True when *text* names a unit that is too granular for
    UC 5 scope — labs, groups, centres, or facilities.

    UC 5 explicitly excludes these: department/division/school/college/
    faculty are the only levels a present Name2 may be corrected to.
    If the LLM canonicalises 'NMR Lab' to 'NMR Facility', we must not
    overwrite — leave the original name2 as-is.

    Important: if the name is already an in-scope construction
    (starts with 'Department of', 'Division of', 'School of',
    'College of', or 'Faculty of'), it is NEVER granular regardless
    of what words follow in the subject. Example:
    'Department of Pathology, Immunology and Laboratory Medicine' is
    a department, not a laboratory, even though the word 'laboratory'
    appears inside it.
    """
    if not text or not text.strip():
        return False
    cleaned = (expand_abbreviations(text) or text).strip()
    lowered = cleaned.lower()

    import re as _re

    # In-scope heads are never granular, regardless of subject content.
    if _re.match(
        r"^(?:department|division|school|college|faculty)\s+(?:of|for)\s+",
        lowered,
    ):
        return False

    # Granular head constructions — the unit word is the HEAD.
    # Form 1: "X Laboratory" / "X Lab" / "X Facility" / "X Group" /
    #         "X Center" / "X Core" (unit word as suffix).
    # Form 2: "Laboratory of X" / "Center for X" / "Centre for X"
    #         (unit word as prefix, no department/division qualifier
    #         before it).
    granular_words = [
        "laboratory", "laboratories",
        "lab",
        "facility", "facilities",
        "center", "centre",
        "core",
    ]
    # Group is handled specially — "NMR Group" is granular, but
    # "Research Group" inside "Department of X Research Group" was
    # already filtered by the in-scope check above.
    suffix_words = granular_words + ["group"]

    for word in suffix_words:
        # Suffix form: "… X Laboratory", "… NMR Lab"
        if _re.search(rf"\b\S+\s+{word}\b\.?$", lowered):
            return True

    # Prefix form: "Laboratory of X" (only when not preceded by a
    # dept/div/etc. qualifier — handled by the early return above).
    for word in granular_words:
        if _re.match(rf"^{word}\s+(?:of|for)\s+", lowered):
            return True

    return False


def is_unit_construction(text: str | None) -> bool:
    """Return True if *text* is (or can be canonicalised into) a
    recognised academic unit construction — e.g. 'Department of X',
    'Division of X', 'School of X', 'Chemistry Department'.

    Used to reject bare subject words ('Anesthesia', 'Chemistry')
    and job-title phrases ('Professor of Anesthesia') which are NOT
    department names.
    """
    if not text or not text.strip():
        return False
    cleaned = (expand_abbreviations(text) or text).strip()

    if re.match(r"^(?:professor|prof|dr|doctor|lecturer|chair|dean|director)\b",
                cleaned, re.IGNORECASE):
        return False

    unit_words = [u for u, _ in _UNIT_CANONICAL_FORMS]
    unit_alt = "|".join(unit_words)

    prefix_re = re.compile(
        rf"^(?:{unit_alt})\s+(?:of|for)\s+\S+",
        re.IGNORECASE,
    )
    suffix_re = re.compile(
        rf"^\S.*\s+(?:{unit_alt})\b\.?$",
        re.IGNORECASE,
    )
    return bool(prefix_re.match(cleaned) or suffix_re.match(cleaned))


def is_specific_unit_construction(text: str | None) -> bool:
    """Return True only if *text* is a SPECIFIC unit construction —
    i.e. Department/Division/Institute/Center/Laboratory of X.

    Rejects parent-level enclosing units like 'School of Medicine'
    or 'College of Engineering' which are almost never the answer
    to 'which department does this person work in'.
    """
    if not is_unit_construction(text):
        return False
    cleaned = (expand_abbreviations(text) or text).strip()

    # Check which unit word this construction uses
    for unit in _SPECIFIC_UNIT_WORDS:
        if re.match(rf"^{unit}\s+(?:of|for)\s+", cleaned, re.IGNORECASE):
            return True
        if re.match(rf"^\S.*\s+{unit}\b\.?$", cleaned, re.IGNORECASE):
            return True
    return False


def canonicalise_unit_name(text: str | None) -> str | None:
    """Normalise academic unit names to the 'Unit of/for Subject' form.

    Examples:
        "Chemistry Department"           → "Department of Chemistry"
        "Department of Chemistry"        → "Department of Chemistry"
        "Dept of Chemistry"              → "Department of Chemistry"
        "Radiology Dept"                 → "Department of Radiology"
        "Biology Division"               → "Division of Biology"
        "Medicine School"                → "School of Medicine"
        "Cancer Research Center"         → "Center for Cancer Research"

    Rules:
    1. Expands abbreviations first (Dept → Department, etc.).
    2. If the text already starts with "<Unit> of/for ...", returns as-is.
    3. If the text ends with a known unit word, rewrites as
       "<Unit> <connector> <rest>".
    4. Otherwise returns the text unchanged.
    """
    if not text or not text.strip():
        return text

    cleaned = expand_abbreviations(text) or text
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;.-")
    if not cleaned:
        return text

    for unit, connector in _UNIT_CANONICAL_FORMS:
        # Already canonical: "Department of X" / "Center for X"
        prefix_re = re.compile(
            rf"^{unit}\s+(?:of|for)\s+",
            re.IGNORECASE,
        )
        if prefix_re.match(cleaned):
            # Normalise the unit-word casing only
            return prefix_re.sub(f"{unit} {connector} ", cleaned, count=1)

    for unit, connector in _UNIT_CANONICAL_FORMS:
        # Suffix form: "X Department", "X Division", ...
        suffix_re = re.compile(
            rf"^(.+?)\s+{unit}\b\.?$",
            re.IGNORECASE,
        )
        m = suffix_re.match(cleaned)
        if m:
            subject = m.group(1).strip(" ,;.-")
            if subject:
                return f"{unit} {connector} {subject}"

    return cleaned


def strip_address_fragments(
    name: str | None,
    street: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> str | None:
    """Remove address fragments that leaked into a name field.

    Uses the record's own structured address fields to detect and
    strip substrings — no hardcoded street-suffix lists. All matches
    are whole-word (``\\b``-bounded) so short codes like "FL" cannot
    match inside "Florida".

    Conservatism rules (to avoid destroying legitimate names that
    happen to share words with the city/state):
      * ``street`` and ``zip`` are always stripped when present — they
        are unambiguous address noise.
      * ``city`` and ``state`` are stripped ONLY if ``street`` or
        ``zip`` was also present in the name (i.e. the address is
        clearly leaking as a group), AND removal still leaves a
        non-empty residue.

    Examples:
        "Johns Hopkins Hospital 600 N Wolfe St" + street "600 N Wolfe St"
            → "Johns Hopkins Hospital"
        "Stanford Uni" + city "Stanford"
            → "Stanford Uni"  (unchanged — no street/zip in name)
        "University of Florida" + state "FL"
            → "University of Florida"  (unchanged — word-bounded)
    """
    if not name or not name.strip():
        return name

    original = name.strip()

    def _strip_fragment(text: str, frag: str) -> str:
        pattern = re.compile(r"\b" + re.escape(frag) + r"\b", re.IGNORECASE)
        return pattern.sub(" ", text)

    result = original
    address_like_hit = False

    # Always strip street and zip (unambiguous noise)
    if street and street.strip():
        before = result
        result = _strip_fragment(result, street.strip())
        if result != before:
            address_like_hit = True
    if zip_code and zip_code.strip():
        before = result
        result = _strip_fragment(result, zip_code.strip())
        if result != before:
            address_like_hit = True

    # Strip standalone long digit runs (street numbers not in `street` field)
    if re.search(r"\b\d{3,}\b", result):
        address_like_hit = True
        result = re.sub(r"\b\d{3,}\b", " ", result)

    # Only strip city/state if we already saw address-like content
    if address_like_hit:
        for frag in (city, state):
            if frag and frag.strip():
                candidate = _strip_fragment(result, frag.strip())
                # Require the residue to still be non-trivial
                residue = re.sub(r"\s+", " ", candidate).strip(" ,;.-")
                if residue:
                    result = candidate

    # Collapse whitespace and trim trailing punctuation
    result = re.sub(r"\s+", " ", result).strip(" ,;.-")
    return result if result else original


def country_to_iso_code(country: str | None) -> str | None:
    """Convert a country name or code to a 2-letter ISO 3166-1 alpha-2 code.

    Returns None if the input is blank or not recognised.
    """
    if not country or not country.strip():
        return None
    key = country.strip().upper()
    return _COUNTRY_TO_ISO.get(key)
