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


def country_to_iso_code(country: str | None) -> str | None:
    """Convert a country name or code to a 2-letter ISO 3166-1 alpha-2 code.

    Returns None if the input is blank or not recognised.
    """
    if not country or not country.strip():
        return None
    key = country.strip().upper()
    return _COUNTRY_TO_ISO.get(key)
