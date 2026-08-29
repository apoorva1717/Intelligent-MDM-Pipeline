"""Text cleaning, domain extraction, and string normalisation helpers."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from rapidfuzz import fuzz

from utils.name_slots import DEPT_SLOTS

logger = logging.getLogger(__name__)


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

    ``include_name2`` adds the record's department text. It appends every
    populated department slot, not Name 2 alone — a record whose unit sits
    in Name 3 describes the same child affiliation and must produce the
    same string. The parameter keeps its name for callers that pass it by
    keyword.
    """
    parts: list[str] = []
    if getattr(record, "name1", None):
        parts.append(record.name1)
    if include_name2:
        for slot in DEPT_SLOTS:
            value = getattr(record, slot, None)
            if value and str(value).strip():
                parts.append(value)
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
    # Common misspellings of "University" — fixed first so the typo'd form
    # both matches ROR on rescore and is cleaned in passthrough output.
    r"\b(?:Universtiy|Univeristy|Univesity|Universty|University|Univercity)\b":
        "University",
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
    # "Med Ctr" / "Med Center" → "Medical Center" (must precede the generic
    # Med→Medicine rule; "Ctr" has already expanded to "Center" by this point).
    r"\bMed\.?(?=\s+(?:Center|Centre|Ctr)\b)": "Medical",
    r"\bMed\.?(?=\s|$)": "Medicine",
    r"\bOrg\.?(?=\s|$)": "Organization",
    r"\bAssoc\.?(?=\s|$)": "Association",
    # Organisational suffixes that survived into output name fields before
    # Fix 4 ("Cardinal Research GRP", "Coastal Analytical Svcs"). Unambiguous:
    # neither token has a competing expansion in an organisation name.
    r"\bGrp\.?(?=\s|$)": "Group",
    r"\bSvcs\.?(?=\s|$)": "Services",
    r"\bTech\.?(?=\s|$)": "Technology",
    r"\bNatl\.?(?=\s|$)": "National",
    r"\bIntl\.?(?=\s|$)": "International",
    r"\bDiv\.?(?=\s|$)": "Division",
    r"\bMgmt\.?(?=\s|$)": "Management",
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


# Connectors kept lowercase when title-casing an ALL-CAPS org name.
_TITLE_CASE_CONNECTORS = {"of", "and", "for", "the", "in", "at", "&"}

# A 3-letter token is ambiguous: "MRI"/"IBM"/"LLC" are acronyms (keep upper)
# but "BAY"/"NEW" are words and "INC" reads better title-cased. Length alone
# defaults short tokens to acronyms; these allowlists carve out the exceptions.
#
# Short tokens (≤3 letters) that should be title-cased despite the default:
# common short words and legal forms that read better capitalised.
_FORCE_TITLE_SHORT = {
    "INC", "LTD", "CO", "BAY", "NEW", "OLD", "SUN", "OAK", "BIG", "RED",
    "SKY", "SEA", "AIR", "SON", "TWO", "ONE", "KEY", "TOP", "BOX",
}
# ---------------------------------------------------------------------------
# Parent-organisation acronyms
# ---------------------------------------------------------------------------
# Acronyms that name a PARENT organisation whose sub-units are routinely
# prefixed with them in SAP master data ("USDA - Kerrville MLRA Office",
# "NASA Ames Research Center"). The value is the organisation's official full
# name.
#
# This map answers a question no heuristic can: is the short token an
# abbreviation OF the rest of the value, or the name of a DIFFERENT
# organisation that owns it? "CALIBR - California Institute for Biomedical
# Research" is the first (one entity, written twice); "USDA - Kerrville MLRA
# Office" is the second (a department and one of its field offices). Both look
# identical to `_acronym_matches_phrase` — neither acronym is an initialism of
# the phrase beside it — so `preprocess._strip_redundant_acronym` used to drop
# the acronym in both cases. Dropping it is right for CALIBR and loses the
# owning organisation for USDA.
#
# Membership here is therefore a deliberate assertion, not a lookup table of
# convenience: an entry says "this token names an organisation in its own
# right". Only add an acronym whose dominant use is as a parent prefix. Two
# kinds are deliberately EXCLUDED:
#   * ambiguous short forms that collide with ordinary words or postal codes
#     ("VA" is both Veterans Affairs and Virginia, "ARS" and "NPS" are short
#     enough to collide with unrelated initialisms);
#   * organisations that are normally written as the acronym and are not
#     parents of prefixed units ("CERN", "EMSL", "IEEE") — expanding those
#     would replace the name people actually use.
PARENT_ORG_ACRONYMS: dict[str, str] = {
    "USDA": "United States Department of Agriculture",
    "NASA": "National Aeronautics and Space Administration",
    "NOAA": "National Oceanic and Atmospheric Administration",
    "NIH": "National Institutes of Health",
    "FDA": "Food and Drug Administration",
    "CDC": "Centers for Disease Control and Prevention",
    "EPA": "United States Environmental Protection Agency",
    "USGS": "United States Geological Survey",
    "NSF": "National Science Foundation",
    "NIST": "National Institute of Standards and Technology",
    "FAA": "Federal Aviation Administration",
    "NRCS": "Natural Resources Conservation Service",
    "USACE": "United States Army Corps of Engineers",
    "DOE": "United States Department of Energy",
    "HHS": "United States Department of Health and Human Services",
    "CNRS": "Centre National de la Recherche Scientifique",
    "CSIRO": "Commonwealth Scientific and Industrial Research Organisation",
}


# Longer tokens (≥4 letters, vowel-bearing) that should stay uppercase.
_KEEP_UPPER_ACRONYMS = {
    "NASA", "NOAA", "NIH", "FDA", "USDA", "EMSL", "IEEE",
    # Vowel-bearing institution acronyms that the length/vowel heuristics would
    # otherwise title-case ("TUHH" → "Tuhh"). Extend as they come up.
    "NIST", "NJIT", "TUHH", "NREL", "SLAC", "CERN", "CNRS", "CSIRO", "CCSF",
    # University acronyms (4-6 chars, vowel-bearing) that the heuristics would
    # otherwise lower-case ("UCSF" → "Ucsf"). Surface directly from a street
    # field (e.g. "UCSF; 600 16th Ave") before ROR resolves them, so they must
    # keep their casing. The 3-char campuses (UCI, UCR, UCB, UCD) are already
    # kept by the length rule.
    "UCSF", "UCSD", "UCLA", "UCSB", "UCSC", "SUNY", "CUNY", "UMASS",
    "UPENN", "UCONN",
    # Found by the golden set, all three arriving already correct in the input
    # and shipping mangled — "UTSW Medical Center" -> "Utsw", "VAMC West LA"
    # -> "Vamc", "IDEXX Reference Laboratories" -> "Idexx". Each is
    # vowel-bearing and pronounceable, so `_unpronounceable` cannot reach them
    # by design; the allowlist is what that docstring points at for this case.
    "UTSW", "VAMC", "IDEXX", "VISN", "HCA", "JEOL",
}
# Every parent-org acronym is by definition an acronym, so it keeps its casing
# too. Folded in rather than duplicated: one edit to PARENT_ORG_ACRONYMS is
# enough, and the two lists can never disagree about whether "USDA" is a word.
_KEEP_UPPER_ACRONYMS |= set(PARENT_ORG_ACRONYMS)
_VOWELS = set("AEIOU")

# Consonant clusters that can BEGIN an English syllable. Used by the
# pronounceability test below; "Y" counts as a vowel throughout, which makes
# the test conservative ("XYLOS", "MYRRH" read as words, not acronyms).
_VALID_ONSETS = {
    "BL", "BR", "CH", "CL", "CR", "CZ", "DR", "DV", "DW", "FL", "FR", "GH",
    "GL", "GN", "GR", "KL", "KN", "KR", "KV", "MN", "PF", "PH", "PL", "PN",
    "PR", "PS", "QU", "RH", "SC", "SH", "SK", "SL", "SM", "SN", "SP", "SQ",
    "ST", "SV", "SW", "SZ", "TH", "TR", "TS", "TW", "TZ", "VL", "VR", "WH",
    "WR", "ZH", "ZL",
    "SCH", "SCR", "SHR", "SPH", "SPL", "SPR", "STR", "THR",
}


# Consonant runs that a borrowed proper noun opens with. English has no
# three-consonant onset outside `_VALID_ONSETS`, but names carried into
# English do — and mangling a surname is worse than leaving one acronym
# title-cased, so these are checked as PREFIXES rather than whole runs
# ("SCH" clears "SCHM" in Schmidt, "MC" clears "MCK" in McKay).
_NAME_ONSET_PREFIXES = ("SCH", "MAC", "MC")


def _unpronounceable(letters: str) -> bool:
    """True when *letters* (upper-case) cannot open an English syllable, and so
    cannot be a word however many vowels it carries.

    The allowlist above can only ever hold the acronyms someone has already
    hit. This rule generalises for the common case: an acronym's letters are
    the initials of unrelated words, so it routinely opens with a consonant run
    no word could ("MLRA", "NRLF", "SPTF"). A word cannot — every English onset
    is one consonant or one of the clusters in `_VALID_ONSETS`.

    Deliberately narrow, in three ways, because the cost of a false positive
    (a mangled surname) is higher than the cost of a false negative (an acronym
    that needs an allowlist entry):

    * only 3-5 letters — the band where a token is ambiguous at all. Longer
      all-caps tokens are overwhelmingly words or names ("SCHNEIDER",
      "MCDONALD"), and an acronym that long usually reads as one anyway;
    * only runs of THREE or more consonants. Two is where the borrowed proper
      nouns live ("DVORAK", "SVEN", "TSANG") and an onset list is a weaker
      guarantee there than the allowlist already is;
    * never after a name-onset prefix, so the German and Gaelic families that
      genuinely do open with three consonants are left alone.

    Not a claim about pronounceable acronyms: "NASA" and "MESA" are
    indistinguishable by this test and both read as words, which is exactly
    what `_KEEP_UPPER_ACRONYMS` is still for.
    """
    if not (3 <= len(letters) <= 5):
        return False
    if letters.startswith(_NAME_ONSET_PREFIXES):
        return False
    run = ""
    for ch in letters:
        if ch in _VOWELS or ch == "Y":
            break
        run += ch
    return len(run) >= 3 and run not in _VALID_ONSETS


# Whole-token known mixed forms — checked case-insensitively before the
# heuristics so a hyphenated proper noun whose segments would misfire is emitted
# exactly. Extend as they come up.
_CASE_EXCEPTIONS = {
    "bio-rad": "Bio-Rad",
    "abx-cro": "ABX-CRO",
    "dana-farber": "Dana-Farber",
    "at&t": "AT&T",
    # An initialism whose full stops hid it from the acronym rule: the token
    # is not all-caps letters, so it cased as an ordinary word and
    # "P.O. BOX 691787" shipped as "P.o. Box 691787". The dotless spelling
    # already cased correctly, which is why this only ever showed on the
    # records that punctuate it.
    "p.o.": "P.O.",
    "p.o": "P.O",
}


def _mc_name(word: str) -> str:
    """Restore the internal capital in an "Mc" surname ("Mcintyre" → "McIntyre").
    ``word`` is already Capitalized. "Mac" is intentionally left alone — blindly
    capitalising after it mangles ordinary words ("Macron", "Macmillan")."""
    m = re.match(r"^(Mc)([a-z])(.+)$", word)
    return f"{m.group(1)}{m.group(2).upper()}{m.group(3)}" if m else word


def _case_segment(seg: str) -> str:
    """Case one hyphen-free segment, preserving acronyms/connectors."""
    letters = re.sub(r"[^A-Za-z]", "", seg)
    upper = letters.upper()
    if not letters:
        return seg
    if seg.lower() in _TITLE_CASE_CONNECTORS:
        return seg.lower()
    if upper in _FORCE_TITLE_SHORT:
        return _mc_name(seg.capitalize())
    if upper in _KEEP_UPPER_ACRONYMS:
        return seg
    if len(letters) <= 3:
        return seg  # short → assume acronym: IBM, MRI, LLC, USA, HCA
    if len(letters) <= 5 and not (set(upper) & _VOWELS):
        return seg  # no-vowel 4-5 char acronym: MGMT, PLLC
    if _unpronounceable(upper):
        # Vowel-bearing but unsayable: "MLRA", "NRLF". Before this rule an
        # ALL-CAPS field lower-cased every acronym that was not in the
        # allowlist by name, so "USDA - KERRVILLE MLRA OFFICE" shipped
        # "Kerrville Mlra Office".
        return seg
    return _mc_name(seg.capitalize())


def smart_title_case(value: str | None) -> str | None:
    """Title-case an ALL-CAPS value, preserving acronyms and connectors.

    "CHEMISTRY DEPARTMENT" → "Chemistry Department"
    "SOUTH BAY HOSPITAL"   → "South Bay Hospital"      (word "Bay" cased)
    "STERLING INDUSTRY LLC" → "Sterling Industry LLC"  (acronym kept)
    "MRI DEPARTMENT"       → "MRI Department"          (acronym kept)
    "DANA-FARBER"          → "Dana-Farber"             (each hyphen segment cased)
    "MCINTYRE"             → "McIntyre"                (Mc surname preserved)

    Each hyphen-separated segment is cased independently, so the part after a
    hyphen is no longer lower-cased. Mixed-case input is returned unchanged, so
    canonical ROR / LLM names (never ALL-CAPS) are never altered.
    """
    if not value or not value.strip() or not value.isupper():
        return value
    out = []
    for w in value.split():
        key = w.lower()
        if key in _CASE_EXCEPTIONS:
            out.append(_CASE_EXCEPTIONS[key])
        elif "-" in w:
            out.append("-".join(_case_segment(s) if s else s for s in w.split("-")))
        else:
            out.append(_case_segment(w))
    return " ".join(out)


# ---------------------------------------------------------------------------
# Bracketed-span removal (every name field, input and output)
# ---------------------------------------------------------------------------
#
# A bracketed span in a name is never part of the name. It is a
# disambiguator a source system bolted on — a city ("3M (Detroit)",
# "3M Corporate (Saint Paul)"), a country (ROR's "Pfizer (United States)"),
# an acronym ("… Institute of Technology (MIT)") or plain noise
# ("(guest)"). None of those belong in the canonical name, and keeping
# them splits one organisation across several spellings, so the whole
# span goes — brackets and contents alike.

# One non-nested bracketed span. Applied repeatedly so nested spans are
# removed innermost-first: "A (B (C) D) E" -> "A E".
_BRACKETED_SPAN_RE = re.compile(r"\s*[(\[{][^(){}\[\]]*[)\]}]\s*")

# An opening bracket with no closing partner, to end of string. SAP name
# columns are 40 characters (see `name_repack.NAME_FIELD_WIDTH`), so "Bayer (Leverkusen Werk" — a truncated
# disambiguator — is as common as the closed form.
_UNCLOSED_BRACKET_RE = re.compile(r"\s*[(\[{][^(){}\[\]]*$")

# Residue left behind once a span is cut out ("Acme, (US)" -> "Acme,").
# Periods are NOT stripped: they are load-bearing for "Inc.".
_BRACKET_RESIDUE = " ,;:-/|"

# Cutting a span out mid-string leaves the separators that framed it stranded:
# "Dept of Physics (Rm 210), Bldg 6" -> "Dept of Physics , Bldg 6" (space before
# the comma) and "Acme, (US), Ltd" -> "Acme, , Ltd" (two commas in a row).
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;:])")
_REPEATED_PUNCT_RE = re.compile(r"([,;:])\s*(?=[,;:])")


def _tidy_bracket_residue(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _REPEATED_PUNCT_RE.sub("", cleaned)
    return cleaned.strip().strip(_BRACKET_RESIDUE).strip()


def strip_parentheticals(value: str | None) -> str | None:
    """Drop every bracketed span from a name, brackets and contents alike.

    "3M (Detroit)"                -> "3M"
    "3M Corporate (Saint Paul)"   -> "3M Corporate"
    "Pfizer (United States)"      -> "Pfizer"
    "Bayer (Leverkusen Werk"      -> "Bayer"        (unclosed, truncated)

    A value with no bracket is returned byte-identical — the rule must be a
    no-op on the overwhelming majority of names. A value that is ENTIRELY
    bracketed ("(Research Division)") is unwrapped rather than emptied: the
    brackets are still noise, but the text inside them is all the field has.
    """
    if value is None:
        return None
    text = str(value)
    if not any(ch in text for ch in "([{"):
        return value

    cleaned = text
    while True:
        collapsed = _BRACKETED_SPAN_RE.sub(" ", cleaned)
        if collapsed == cleaned:
            break
        cleaned = collapsed
    cleaned = _UNCLOSED_BRACKET_RE.sub(" ", cleaned)
    cleaned = _tidy_bracket_residue(cleaned)

    if not cleaned:
        # Nothing survived — keep the payload, lose only the brackets.
        cleaned = _tidy_bracket_residue(re.sub(r"[(){}\[\]]", " ", text))

    return cleaned or None


def clean_passthrough_org_name(name: str | None) -> str | None:
    """Normalise an org name that passed through enrichment uncanonicalised.

    ROR misses are returned verbatim from the source — often ALL-CAPS and
    full of abbreviations ("LARGO MEDICAL CTR", "Capital Regional Med Ctr").
    Title-case any ALL-CAPS form first, then expand common abbreviations, so
    the output is consistent with ROR-matched rows. Order matters: title-case
    must run before expansion, otherwise expanding "CTR"→"Center" would make
    the string mixed-case and defeat the ALL-CAPS title-case guard.
    """
    if not name or not name.strip():
        return name
    cleaned = smart_title_case(name) or name
    cleaned = expand_abbreviations(cleaned) or cleaned
    return cleaned


# ---------------------------------------------------------------------------
# Output casing normalisation (Finalization Rule 7)
# ---------------------------------------------------------------------------
#
# `smart_title_case` above is a WHOLE-STRING rule: it refuses any value that is
# not entirely upper-case, so a partly-corrected value like "500 TECH Dr MS-4"
# (the street-suffix map already cased "Dr") kept its uppercase "TECH". The
# normaliser below works TOKEN BY TOKEN, so each token is judged on its own.
#
# Per token:
#   * contains a digit          -> untouched      ("MS-4", "3M", "450")
#   * already mixed case        -> untouched      ("Dr", "GmbH", "McDonald")
#   * all-upper or all-lower    -> title-cased, subject to the tables below
#
# Casing changes letter case and NOTHING else. No character is ever added or
# removed — every apostrophe, comma, period, ampersand and hyphen survives
# exactly as written. `normalise_case` asserts that itself and returns the
# input unchanged if the invariant is ever broken.

# Tokens with a fixed canonical spelling, emitted exactly as written here when
# a token matches case-insensitively. Legal forms, acronyms, directional street
# prefixes and the vowel-less street/title abbreviations that the acronym guard
# below would otherwise leave upper-case ("DR" -> "Dr", not "DR").
_CANONICAL_TOKEN_FORMS: dict[str, str] = {
    # Legal forms.
    "inc": "Inc", "llc": "LLC", "ltd": "Ltd", "gmbh": "GmbH", "ag": "AG",
    "se": "SE", "bv": "BV", "nv": "NV", "kg": "KG", "sa": "SA", "spa": "SpA",
    "ab": "AB", "as": "AS", "oy": "Oy",
    # Acronyms.
    "mit": "MIT", "ucla": "UCLA", "nmr": "NMR", "it": "IT", "ai": "AI",
    "us": "US", "usa": "USA", "uk": "UK", "po": "PO", "r&d": "R&D",
    # Directional street prefixes.
    "n": "N", "s": "S", "e": "E", "w": "W",
    "ne": "NE", "nw": "NW", "sw": "SW",
    # Street types and personal titles. These are vowel-less, so without an
    # entry here the acronym guard would keep them upper-case. The street
    # stage's own map (address_processing.STREET_TYPE_ABBREVIATIONS) already
    # emits these forms — this table is what makes a slot that stage never
    # touched agree with one it did.
    "st": "St", "ave": "Ave", "blvd": "Blvd", "dr": "Dr", "rd": "Rd",
    "ln": "Ln", "ct": "Ct", "hwy": "Hwy", "pkwy": "Pkwy", "rte": "Rte",
    "pl": "Pl", "sq": "Sq", "ter": "Ter", "cir": "Cir",
    "mr": "Mr", "mrs": "Mrs", "prof": "Prof",
}

# Bounded, explicit list. A general Roman-numeral regex accepts ordinary words
# ("MIX" parses as 1009), so the numerals are enumerated instead.
_ROMAN_NUMERALS = {
    "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
}

# Lower-case particles that must stay lower-case mid-value. The English
# connectors are shared with `smart_title_case`; the rest keep a European
# institution or a surname readable ("Institut für Physik", "van der Waals").
#: Name particles — the Romance and Germanic articles and prepositions that
#: appear inside a personal or institution name. Kept apart from the English
#: connectors because only these carry their meaning in lower case: `la` is the
#: article, `LA` is Los Angeles. An English connector has no such distinction —
#: an upper-case `OF` in half-cased text is an accident, not a different word.
_NAME_PARTICLES = {
    "von", "van", "der", "den", "des", "dem", "du", "de", "del", "della",
    "di", "da", "das", "dos", "le", "la", "les", "und", "für", "fuer", "y",
}
_LOWERCASE_PARTICLES = _TITLE_CASE_CONNECTORS | _NAME_PARTICLES

_APOSTROPHES = ("'", "’")


def _plain_title(word: str) -> str:
    """Title-case one plain run of letters. Never `str.title()` or
    `str.capitalize()` on anything with an apostrophe — `str.title()` produces
    "Women'S". This helper only ever sees apostrophe-free, hyphen-free runs."""
    if not word:
        return word
    return _mc_name(word[0].upper() + word[1:].lower())


def _title_structural(core: str) -> str:
    """Title-case a token core, respecting Mc/Mac, hyphens and apostrophes.

    "MCDONALD"  -> "McDonald"     (internal capital restored)
    "JEAN-YVES"  -> "Jean-Yves"   (each side cased)
    "WOMEN'S"    -> "Women's"     (possessive s stays lower)
    "O'BRIEN"    -> "O'Brien"     (a real name segment is cased)
    """
    if not any(ch in core for ch in _APOSTROPHES):
        return _plain_title(core)
    parts = re.split(r"(['’])", core)
    out: list[str] = []
    after_sep = False
    for part in parts:
        if part in _APOSTROPHES:
            out.append(part)
            after_sep = True
            continue
        if not part:
            continue
        # A single letter after an apostrophe is a possessive/elision, not a
        # name segment: "WOMEN'S" -> "Women's", never "Women'S".
        out.append(part.lower() if after_sep and len(part) == 1
                   else _plain_title(part))
    return "".join(out)


def _case_core(
    core: str, *, mode: str, first: bool, mixed_source: bool = False,
) -> str:
    """Case one token core (leading/trailing punctuation already split off).

    *mixed_source* says the whole value this token came from was NOT entirely
    upper-case. See :func:`normalise_case`.
    """
    letters = [ch for ch in core if ch.isalpha()]
    if not letters:
        return core
    lowered = core.lower()
    upper = core.upper()
    is_upper = core == upper
    is_lower = core == lowered

    # Already mixed case — intentional casing, leave it ("Dr", "GmbH", "3M"
    # is caught by the digit rule in the caller, "McDonald").
    if not (is_upper or is_lower):
        return core

    if lowered in _CANONICAL_TOKEN_FORMS:
        return _CANONICAL_TOKEN_FORMS[lowered]
    if upper in _ROMAN_NUMERALS:
        return upper
    # A connector is only lower-cased mid-value; leading it, it is a word.
    #
    # ...and an UPPER-CASE *name particle* inside a value that is not itself
    # wholly upper-case is not a particle at all. `VAMC West LA Visn 22`
    # shipped as `Vamc West la Visn 22`: `LA` is Los Angeles, and everyone who
    # means the Romance article writes it `la`.
    #
    # Restricted to `_NAME_PARTICLES`, twice over. An upper-case token is a
    # weak signal in general — `500 TECH Dr` and `Adams Air HYDRAULICS INC`
    # are half-cased input this pass exists to clean. And an English connector
    # has no lower-case-only meaning to protect, so `THE University OF Texas`
    # must still yield `of`; reading that `OF` as deliberate was this rule's
    # first attempt and it was wrong.
    if lowered in _LOWERCASE_PARTICLES and not first:
        if not (is_upper and mixed_source and lowered in _NAME_PARTICLES):
            return lowered
    if upper in _KEEP_UPPER_ACRONYMS:
        return upper
    if is_upper:
        letter_count = len(letters)
        # Vowel-less short token: an acronym, not a word (IBM, MRI, PLLC).
        if letter_count <= 5 and not (set(upper) & _VOWELS):
            return core
        # Vowel-bearing but unsayable: "MLRA", "NRLF". Applied in both modes —
        # a token no English word could open is not a word in a street line
        # either. `smart_title_case` carries the same rule; the two casing
        # paths must agree, or a value lands cased differently depending on
        # which one reached it. That is exactly how "USDA - KERRVILLE MLRA
        # OFFICE" kept "MLRA" in Name 1 and shipped "Mlra" from Name 2.
        if _unpronounceable(upper):
            return core
        # Name fields keep the existing short-token default — a <=3-letter
        # token in an organisation name is an acronym (HCA, UCI, IBM) unless
        # allow-listed as a word. Address, city and person fields do not: there
        # a short token is a word far more often than an acronym ("WAY", "OAK",
        # "LAB"), and `_FORCE_TITLE_SHORT` cannot enumerate them all.
        if mode == "name" and letter_count <= 3 and upper not in _FORCE_TITLE_SHORT:
            return core

    # Hyphen- or ampersand-joined: case each side under the full rule set, so
    # an acronym on either side survives ("TECHNOLOGY-NIST" ->
    # "Technology-NIST", "ICB&DD" -> "ICB&DD" — each segment is short enough to
    # read as an acronym on its own).
    if "-" in core or "&" in core:
        segs = re.split(r"([-&])", core)
        out: list[str] = []
        seg_index = 0
        for seg in segs:
            if seg in ("-", "&") or not seg:
                out.append(seg)
                continue
            out.append(_case_core(
                seg, mode=mode, first=first and seg_index == 0,
                mixed_source=mixed_source,
            ))
            seg_index += 1
        return "".join(out)
    return _title_structural(core)


def _case_token(
    token: str, *, mode: str, first: bool, mixed_source: bool = False,
) -> str:
    """Case one whitespace-delimited token, preserving its punctuation.

    Leading and trailing punctuation is split off and re-attached verbatim, so
    "Inc." keeps its period and "Diagnostics," keeps its comma.
    """
    if any(ch.isdigit() for ch in token):
        return token  # "MS-4", "450", "3M" — never re-cased
    m = re.match(r"^(?P<pre>[^0-9A-Za-z]*)(?P<core>.*?)(?P<post>[^0-9A-Za-z]*)$", token)
    if not m or not m.group("core"):
        return token
    if token.lower() in _CASE_EXCEPTIONS:  # whole-token known forms: "AT&T"
        return _CASE_EXCEPTIONS[token.lower()]
    return (
        m.group("pre")
        + _case_core(
            m.group("core"), mode=mode, first=first, mixed_source=mixed_source,
        )
        + m.group("post")
    )


def normalise_case(
    value: str | None,
    *,
    mode: str = "text",
    continuation: bool = False,
) -> str | None:
    """Token-level casing for an output field.

    ``mode`` is ``"name"`` for Name 1-4 (a short upper-case token defaults to
    an acronym) and ``"text"`` for street, city, PO box, c/o and contact (a
    short upper-case token defaults to a word).

    ``continuation`` says this value is the middle of a name rather than the
    start of one — the second and later pieces of a name the UC-0 repack cut
    across columns. The first token is then cased as any other, so a piece
    beginning on a connector keeps it lower case: ``ExxonMobil Technology`` +
    ``and Engineering Company``, never ``And Engineering Company``. Without
    this the repack's own cut point manufactures a capital mid-name.

    Whitespace is preserved exactly — the value is split on whitespace runs and
    rejoined with the same runs, so nothing is collapsed. The length invariant
    is checked before returning: casing can only ever change letter case, so a
    length change means a bug, and the input is returned untouched.
    """
    if not value or not value.strip():
        return value
    # Whether the SOURCE value was written entirely in upper case. When it was
    # not, an upper-case token in it is a deliberate acronym rather than
    # something for the heuristics to guess at.
    mixed_source = value != value.upper()
    parts = re.split(r"(\s+)", value)
    out: list[str] = []
    first = not continuation
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        out.append(_case_token(
            part, mode=mode, first=first, mixed_source=mixed_source,
        ))
        first = False
    cased = "".join(out)
    if len(cased) != len(value):  # pragma: no cover — invariant guard
        logger.warning(
            "normalise_case changed the length of %r -> %r; returning input",
            value, cased,
        )
        return value
    return cased


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


#: Corporate legal-form tokens, for :func:`has_corporate_legal_suffix`.
#:
#: Deliberately NOT reused from ``tier1_lei._LEGAL_FORM_TOKENS``, which exists
#: to *strip* these tokens before a name comparison. Over-inclusion is nearly
#: free when stripping and expensive when classifying: a token wrongly stripped
#: costs a little match quality, a token wrongly matched asserts that a research
#: institute is a company. The two sets happen to overlap heavily; they are not
#: the same set, and merging them would make a change made for one purpose
#: silently alter the other.
_CORPORATE_LEGAL_FORMS: frozenset[str] = frozenset({
    "inc", "llc", "corp", "corporation", "company", "co", "ltd", "lp",
    "llp", "plc", "gmbh", "ag", "nv", "bv", "sa", "pty",
})

#: A legal name is TERMINATED by its legal form, so the token is only read in
#: final position — of the whole name, or of a segment before a comma or a
#: "doing business as" marker ("Value Plastics Inc DBA Nordson Medical", which
#: is a real shape in this SAP data). Position is what makes the short tokens
#: safe: ``co``, ``ag``, ``sa``, ``nv`` and ``bv`` are ordinary words elsewhere
#: in a name, and matching them anywhere claims "Co-operative Research Centre",
#: "AG Research Ltd Kenya Branch" and "Co Down Health Trust" as companies.
#: Measured on the 200 labelled records: any-position and segment-final both
#: score precision 1.000, so that sample cannot distinguish them — the
#: constraint is kept because the sample is 200 records and the pipeline runs
#: on ten thousand.
_LEGAL_SEGMENT_SPLIT_RE = re.compile(r",|\bd/?b/?a\b|\baka\b|\bt/a\b", re.IGNORECASE)
_LEGAL_TOKEN_RE = re.compile(r"[a-z0-9&]+")


def has_corporate_legal_suffix(name: str | None) -> bool:
    """Does *name* end in a corporate legal form (``Inc``, ``LLC``, ``GmbH``…)?

    The mirror image of :func:`looks_like_research_institution`, and the reason
    it exists. That predicate can only ever yield ``research_institution`` — a
    name not looking like an institution is not evidence of a company. But a
    legal-form suffix IS evidence of a company, positively and by definition:
    it is the entity's registered legal character, stated in its own name.
    The classifier had no symmetric source for it, so 21 records that say what
    they are in their own name shipped as ``unknown`` or, worse, as
    ``research_institution`` on a keyword read of "Laboratories".

    It is a fallback, not an authority: a registry verdict outranks it, because
    the suffix is read off the input rather than verified against anything.
    """
    if not name or not name.strip():
        return False
    for segment in _LEGAL_SEGMENT_SPLIT_RE.split(name):
        tokens = _LEGAL_TOKEN_RE.findall(segment.lower())
        if tokens and tokens[-1] in _CORPORATE_LEGAL_FORMS:
            return True
    return False


# A narrower signal than the ROR-miss one above: universities, research
# institutes, colleges and academies — the org types where a department
# is genuinely expected somewhere in the name block, so its absence from
# every slot is a reportable issue.
# Clinical types (hospitals, clinics, medical/cancer centres, health
# systems) and bare labs/observatories are deliberately excluded: they
# routinely carry no department and should not raise the missing-department
# issue codes.
# ``Research`` is deliberately NOT a bare alternative here. As a standalone
# token it is an ordinary commercial-name word — "Cardinal Research GRP" is a
# company, not a research institute — and matching it raised the
# missing-department codes against exactly the org type that has no departments
# to miss. It qualifies only inside the phrases that name an institution
# ("Research Institute", "Research Center", "Research Laboratory").
#
# ``Hochschule`` is matched with an optional compound prefix so the German
# forms that carry it as a suffix ("Fachhochschule", "Musikhochschule") are
# recognised; the previous bare ``Schule`` alternative matched neither those
# nor "Hochschule" itself, since neither offers a word boundary before it.
_UNIVERSITY_OR_RESEARCH_SIGNALS_RE = re.compile(
    r"\b(?:Universit(?:y|ies)|Universit[aä]t|Université|Universidade|"
    r"[A-Za-zÄÖÜäöüß]*[Hh]ochschule|"
    r"Institut(?:e|es|s)?|Academy|College|"
    r"Medical\s+School|School\s+of|Faculty\s+of|"
    r"Research\s+(?:Institut(?:e|es)?|Cent(?:er|re)|"
    r"Laborator(?:y|ies)|Council|Foundation))\b",
    re.IGNORECASE,
)


def looks_like_university_or_research_institute(name: str | None) -> bool:
    """Heuristic: does *name* read as a university, research institute,
    college or academy?

    Narrower than :func:`looks_like_research_institution` — it excludes
    clinical organisations (hospitals, clinics, medical/cancer centres,
    health systems) and standalone labs/observatories. Used to gate the
    missing-department issue codes so they only fire for org types where
    a department is actually expected.
    """
    if not name or not name.strip():
        return False
    return bool(_UNIVERSITY_OR_RESEARCH_SIGNALS_RE.search(name))


def is_granular_unit(text: str | None) -> bool:
    """Return True when *text* names a unit that is too granular for
    UC 5 scope — labs, groups, centres, or facilities.

    UC 5 explicitly excludes these: department/division/school/college/
    faculty are the only levels a present department slot may be
    corrected to. If the LLM canonicalises 'NMR Lab' to 'NMR Facility',
    we must not overwrite — leave the original value as-is.

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
    # unit/program/programme are suffix-only (rule A-15): "Trauma
    # Research Unit", "Alpha-1 Research Program". The prefix forms
    # "Unit of X" / "Program of X" are not idiomatic and would
    # over-match common phrases.
    suffix_words = granular_words + ["group", "unit", "program", "programme"]

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


# Unit words the name block is ORDERED on, most-senior slot first. The block
# writes the division above the department: "State Of Ohio" / "Division of
# Animal Health" / "Department of Agriculture", never the other way round.
# This is a slot-layout convention of this data, not a claim about which unit
# encloses which — it fixes where each value is written, never what it says.
# A value built on neither word is not ordered by this rule and keeps the slot
# it already has.
_ORDERED_UNIT_WORDS: tuple[str, ...] = ("Division", "Department")


def ordered_unit_word(text: str | None) -> str | None:
    """Return the unit word *text* is built on, or None.

    Both constructions count, because a slot can hold either: the canonical
    prefix form ("Division of Animal Health") and the suffix form the
    canonicaliser leaves alone on a granular unit ("Animal Health Division").
    Abbreviations are expanded first, so "Div Of Animal Health" answers
    "Division".

        "Division of Animal Health"   -> "Division"
        "Animal Health Div"           -> "Division"
        "Dept Of Agriculture"         -> "Department"
        "Ohio Veterinary Laboratory"  -> None
    """
    if not text or not text.strip():
        return None
    cleaned = (expand_abbreviations(text) or text).strip()
    for word in _ORDERED_UNIT_WORDS:
        if re.match(rf"^{word}\s+(?:of|for)\s+\S", cleaned, re.IGNORECASE):
            return word
        if re.match(rf"^\S.*\s+{word}\b\.?$", cleaned, re.IGNORECASE):
            return word
    return None


#: Rank of each ordered unit word — lower sits in the higher (earlier) slot.
UNIT_SLOT_RANK: dict[str, int] = {
    word: index for index, word in enumerate(_ORDERED_UNIT_WORDS)
}


# Truncated / abbreviated department subjects that must NOT be reordered into
# a fabricated "Department of <X>" (e.g. "Biomed" → there is no "Department of
# Biomed"; the real unit is Biomedical Engineering/Sciences/etc.). When the
# subject is one of these, canonicalise_unit_name leaves the value unchanged.
# Extend this set as more truncations are observed.
_TRUNCATED_SUBJECTS = {
    "biomed", "anesth", "ortho", "rehab", "neuro", "cardio", "derm",
    "psych", "ophth", "peds", "gastro", "endo", "pulm", "rad",
}


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
            if not subject:
                continue
            # Do NOT fabricate a "Department of <X>" when the subject is a
            # single truncated/abbreviated token ("Biomed Dept" → would give
            # the non-existent "Department of Biomed"). Leave the original
            # value unchanged instead. Real subjects ("Chemistry Dept" →
            # "Department of Chemistry") canonicalise normally.
            if " " not in subject and subject.lower() in _TRUNCATED_SUBJECTS:
                return text
            return f"{unit} {connector} {subject}"

    return cleaned


# Generic company words that carry no identity — ignored when comparing a
# canonicalised name against the original. Deliberately limited to legal /
# structural suffixes; distinctive words like "Technology", "International",
# or "Sciences" are NOT generic (dropping or swapping them changes the
# entity), so they must be preserved by a valid canonicalisation.
_GENERIC_COMPANY_WORDS = {
    "group", "inc", "incorporated", "llc", "llp", "lp", "corp", "corporation",
    "company", "co", "ltd", "limited", "holdings", "holding", "plc", "gmbh",
    "ag", "sa", "nv", "bv", "spa", "srl", "pty", "the", "and", "of", "for",
}


# Long-form legal designators → the abbreviation the pipeline normalises to.
# Collapsing these before comparison means "SAP Aktiengesellschaft" and
# "SAP AG" compare as the same entity (both reduce to {"sap"}), and the
# short form is what surfaces in output. Mirrors preprocess UC 17; kept here
# (the lower-level module) so the identity guard and finalise share it
# without a circular import. Case-insensitive; replacements are the
# conventionally-cased short forms.
_LONGFORM_LEGAL_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgesellschaft\s+mit\s+beschr[aä]nkter\s+haftung\b", re.IGNORECASE), "GmbH"),
    (re.compile(r"\blimited\s+liability\s+company\b", re.IGNORECASE), "LLC"),
    (re.compile(r"\blimited\s+liability\s+partnership\b", re.IGNORECASE), "LLP"),
    (re.compile(r"\baktiengesellschaft\b", re.IGNORECASE), "AG"),
    (re.compile(r"\bincorporated\b", re.IGNORECASE), "Inc"),
    (re.compile(r"\bcorporation\b", re.IGNORECASE), "Corp"),
]


def collapse_legal_suffix(text: str | None) -> str | None:
    """Collapse long-form legal suffixes to their abbreviation, preserving
    the casing of the rest of the name.

    "SAP Aktiengesellschaft" → "SAP AG", "Acme Incorporated" → "Acme Inc".
    Bare ambiguous words ("Limited", "Company") are left alone — they occur
    as real name components ("The Walt Disney Company"). Returns *text*
    unchanged when nothing matches.
    """
    if not text or not text.strip():
        return text
    out = text
    for pat, repl in _LONGFORM_LEGAL_SUBS:
        out = pat.sub(repl, out)
    return re.sub(r"\s+", " ", out).strip()


def _identity_tokens(name: str | None) -> set[str]:
    # Collapse long-form legal suffixes first so "SAP Aktiengesellschaft" and
    # "SAP AG" both reduce to {"sap"} rather than differing on a spurious
    # "aktiengesellschaft" token.
    text = name or ""
    for pat, repl in _LONGFORM_LEGAL_SUBS:
        text = pat.sub(repl, text)
    toks = re.findall(r"[A-Za-z0-9]+", text.lower())
    return {t for t in toks if len(t) >= 2 and t not in _GENERIC_COMPANY_WORDS}


# A logistics / shipping facility — a distribution or fulfillment centre,
# warehouse, depot, etc. These are unloading points (address logistics),
# NOT departments or organisations, even though "...Center/Ctr" reads as a
# unit. Used to keep "Southeast Distribution Ctr" out of the name block and
# route it to the unloading_point field instead.
_LOGISTICS_LOCATION_RE = re.compile(
    r"\b(?:Distribution|Fulfil?lment|Logistics)\s+(?:Center|Centre|Ctr|Warehouse)\b",
    re.IGNORECASE,
)


def is_logistics_location(value: str | None) -> bool:
    """True when *value* names a distribution/fulfillment/logistics facility."""
    return bool(value and _LOGISTICS_LOCATION_RE.search(value))


# Institution-type words a valid canonicalisation MAY add to complete a name
# ("Harvard" → "Harvard University", "Mayo" → "Mayo Clinic"). Adding any
# OTHER distinctive word — a brand/scope qualifier like "World" or "Global"
# ("Precision Instruments Co." → "World Precision Instruments") — signals a
# different entity and is rejected.
#: Unit-type words a DEPARTMENT string may legitimately gain, the way an
#: organisation name may gain "University". Kept separate from
#: `_ORG_TYPE_ADDABLE` rather than merged into it: these are the words that
#: carry a department's identity, and making them freely addable on Name 1 too
#: would change settled behaviour on a population nothing has measured.
#: Note this only ever permits ADDING one. Dropping a unit word is still
#: refused, which is what catches `Baytown Refinery Laboratory` ->
#: `Baytown Refinery`.
_UNIT_TYPE_ADDABLE = {
    "department", "departments", "division", "divisions", "office", "offices",
    "unit", "units", "branch", "branches", "section", "sections", "group",
    "groups", "team", "teams", "programme", "program", "service", "services",
    "facility", "facilities", "lab", "labs", "laboratory", "laboratories",
    "centre", "center", "institute", "faculty", "school",
}

_ORG_TYPE_ADDABLE = {
    "university", "universities", "college", "colleges", "school", "schools",
    "institute", "institutes", "laboratory", "laboratories", "foundation",
    "center", "centre", "centers", "hospital", "hospitals", "clinic",
    "academy", "conservatory", "seminary", "polytechnic",
}


def _token_covers(a: str, b: str) -> bool:
    """True if tokens *a* and *b* are the same word or an abbreviation of it
    (prefix relation, e.g. 'univ'↔'university', 'science'↔'sciences')."""
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a))


def department_preserves_identity(
    original: str | None,
    canonical: str | None,
    *,
    parent_name: str | None = None,
) -> bool:
    """:func:`canonical_preserves_identity`, for a Name 2 department string.

    Same question, one preparation step: both sides are run through
    :func:`expand_abbreviations` first. Department strings in SAP data are
    abbreviated far more often than organisation names are — ``Div``, ``Dept``,
    ``Lab``, ``Mech Eng`` — and the underlying comparator treats an
    abbreviation as a *distinctive token mismatch*, not as the same word. Used
    raw it therefore refuses the lane's best work:

    ==========================================  =====  ========
    proposal                                     raw    expanded
    ==========================================  =====  ========
    ``Weapons Div`` -> ``Weapons Division``      no     yes
    ``Dept of Chemistry`` -> ``Department of…``  no     yes
    ``Mech Eng Dept`` -> ``Department of Mech…`` no     yes
    ==========================================  =====  ========

    The first of those is a **registry-verified** answer carrying a real ROR
    identifier, so a guard that refused it would be discarding a correct
    resolution to prevent a wrong one.

    What it still refuses is what it is for — a changed or dropped unit type:
    ``Forensic Science Div`` -> ``Forensic Services Laboratory`` (a division
    became a laboratory) and ``Baytown Refinery Laboratory`` -> ``Baytown
    Refinery`` (the unit word dropped entirely, so the value now names the site
    rather than the lab). Both of those shipped.

    *parent_name* is the record's Name 1, and its words are addable. A
    department names a unit **of** something, so a proposal that spells out the
    organisation the unit belongs to has not changed which unit it is — it has
    stated context the Name 2 slot left implicit. Without this the guard refuses
    ``Weapons Div`` -> ``Naval Air Warfare Center Weapons Division``, which is a
    **registry-verified** answer carrying ``ror.org/03cap2a49``: the four
    "new" words are Name 1, sitting in the same record.

    This only ever permits ADDING the parent's words. Dropping the unit's own
    distinctive tokens is still refused, which is why it does not weaken any of
    the real failures — those all drop or swap a word rather than add one.

    Only the Name 2 guard expands. Doing the same for Name 1 may well be right,
    but it changes settled behaviour on a different population and needs its own
    measurement.
    """
    addable = set(_UNIT_TYPE_ADDABLE)
    if parent_name and parent_name.strip():
        addable |= _identity_tokens(
            expand_abbreviations(parent_name) or parent_name,
        )
    return canonical_preserves_identity(
        expand_abbreviations(original) or original,
        expand_abbreviations(canonical) or canonical,
        extra_addable=addable,
    )


def canonical_preserves_identity(
    original: str | None,
    canonical: str | None,
    *,
    extra_addable: "frozenset[str] | set[str] | None" = None,
) -> bool:
    """Return True if *canonical* plausibly names the SAME entity as *original*.

    Guards LLM canonicalisation against silently replacing a company with a
    completely different one (e.g. "Iso Group Inc" → "CoStar Group", or
    "Liberty Health Sciences" → "Liberty Science Center"). It accepts a
    result when:
      * EVERY distinctive (non-generic) token of the original is covered by
        the canonical AND the canonical adds no new distinctive word beyond
        generic suffixes or institution-type words — i.e. the change only
        reformats, adds a legal suffix / "University"-style word, or expands
        an abbreviation. It never drops, swaps, OR prepends a distinctive
        word. Sharing just one word ("Liberty"), or adding a brand qualifier
        ("World" in "World Precision Instruments"), is NOT enough. OR
      * it is a legitimate acronym expansion — the original is a single
        all-caps acronym (2–6 letters) whose letters match the initials of
        the canonical's words ("IBM" → "International Business Machines").

    *extra_addable* widens the set of new words the canonical may introduce.
    Default empty, so every existing caller is unaffected; the Name 2 guard
    passes the department unit-type words through it.

    Conservative: when the original has no distinctive tokens to compare
    (e.g. only generic words), it returns True so legitimate reformatting is
    never blocked. The aim is to catch identity *replacement*, not to police
    wording.
    """
    if not (original and original.strip()) or not (canonical and canonical.strip()):
        return True
    o = _identity_tokens(original)
    c = _identity_tokens(canonical)
    if not o or not c:
        return True
    # Every distinctive token of the original must survive in the canonical,
    # and the canonical must not introduce a new distinctive word other than
    # an institution-type word (a brand/scope qualifier like "World" changes
    # the entity).
    if all(any(_token_covers(t, u) for u in c) for t in o):
        extras = [u for u in c if not any(_token_covers(t, u) for t in o)]
        addable = _ORG_TYPE_ADDABLE | (extra_addable or frozenset())
        return all(u in addable for u in extras)
    # Acronym expansion: original is a single all-caps token (in the raw
    # string), 2–6 letters, matching the initials of the canonical's words.
    # Use ALL canonical words for the initials (a generic word like
    # "International" is still the "I" in "IBM"), dropping only a leading
    # article.
    raw = [t for t in re.findall(r"[A-Za-z&]+", original.strip())
           if t.lower() not in _GENERIC_COMPANY_WORDS]
    if len(raw) == 1 and raw[0].isupper() and 2 <= len(raw[0]) <= 6:
        acro = raw[0].lower()
        # Build initials from the canonical's words, skipping connector
        # stopwords ("University of Florida" → "uf", not "uof").
        _stop = {"the", "of", "and", "for", "de", "la", "le"}
        canon_words = [t for t in re.findall(r"[A-Za-z0-9]+", canonical)
                       if t.lower() not in _stop]
        initials = "".join(t[0].lower() for t in canon_words if t)
        if initials == acro or initials.startswith(acro):
            return True
    return False


# Minimum per-token fuzz ratio for two tokens to count as spelling variants of
# each other ("bayr" ↔ "bayer"). High enough that a distinct word ("bayer" ↔
# "baker") does not qualify.
_SPELLING_VARIANT_TOKEN_RATIO = 85.0


def _fuzzy_token_covers(a: str, b: str) -> bool:
    """Like ``_token_covers`` but also accepts a minor spelling difference.

    Two tokens cover each other when they are equal, in a prefix relation, OR
    (both ≥4 chars) their rapidfuzz ratio clears
    ``_SPELLING_VARIANT_TOKEN_RATIO`` — i.e. one is a typo of the other. The
    ≥4-char floor stops short tokens ("abc"↔"abd") from colliding.
    """
    if _token_covers(a, b):
        return True
    return (
        min(len(a), len(b)) >= 4
        and fuzz.ratio(a, b) >= _SPELLING_VARIANT_TOKEN_RATIO
    )


def canonical_is_spelling_variant(
    original: str | None, canonical: str | None,
) -> bool:
    """True if *canonical* is *original* modulo a minor spelling correction.

    A stricter, fuzzy cousin of :func:`canonical_preserves_identity`, used to
    gate registry re-verification of an LLM-proposed name: every distinctive
    token of the original must be covered by a canonical token exactly, by
    prefix, OR by a high fuzzy ratio (a typo fix like "Bayr"→"Bayer"), and the
    canonical may add only generic org-type words. Entity swaps where the
    tokens do not align ("Iso Group"→"CoStar Group") return False, so this can
    never launder an LLM hallucination — GLEIF confirmation is still required
    downstream, but only for proposals that pass this gate.
    """
    if not (original and original.strip()) or not (canonical and canonical.strip()):
        return False
    o = _identity_tokens(original)
    c = _identity_tokens(canonical)
    if not o or not c:
        return False
    # No exact/prefix-only match (that is canonical_preserves_identity's job);
    # require a genuine spelling difference in at least one token so this stays
    # a typo-correction gate, not a second identity check.
    if all(any(_token_covers(t, u) for u in c) for t in o):
        return False
    if not all(any(_fuzzy_token_covers(t, u) for u in c) for t in o):
        return False
    extras = [u for u in c if not any(_fuzzy_token_covers(t, u) for t in o)]
    return all(u in _ORG_TYPE_ADDABLE for u in extras)


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

    # A trailing ", <city>" / ", <state>" segment that matches the record's
    # own City/State is a location suffix on the org name — noise even when
    # no street/zip is present (e.g. "HCA Florida University Hospital, Davie"
    # with City "Davie"). Strip it as a trailing comma-delimited segment only,
    # so interior words (e.g. "Florida" in the org name) are never touched.
    # Repeated to peel "..., City, State". This does NOT set address_like_hit,
    # so it can't trigger the broader whole-word removal below.
    changed = True
    while changed:
        changed = False
        for frag in (city, state):
            if not (frag and frag.strip()):
                continue
            m = re.search(
                r",\s*" + re.escape(frag.strip()) + r"\s*$", result, re.IGNORECASE,
            )
            if m:
                candidate = result[: m.start()].strip(" ,;.-")
                if candidate:
                    result = candidate
                    changed = True

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


# ---------------------------------------------------------------------------
# Shared acronym / admin helpers (search-term derivation, ROR acronym currency,
# department-probe admin suppression). Kept here so search_terms.py,
# tier1_ror.py, and the orchestrator share one convention without a circular
# import.
# ---------------------------------------------------------------------------

# Stopwords skipped when reading the initials of a name (same convention as
# search_terms.derive_acronym).
_INITIALS_STOPWORDS = {
    "of", "for", "the", "and", "in", "on", "at", "to",
    "a", "an", "de", "du", "des", "la", "le", "les", "&",
}
_INITIALS_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&\-]*")


def name_initials(name: str | None) -> str:
    """Uppercase initials of *name*'s significant (non-stopword) words.

    ``'National Institute of Standards and Technology'`` → ``'NIST'``
    ``'University of Florida'``                          → ``'UF'``
    """
    if not name:
        return ""
    out: list[str] = []
    for tok in _INITIALS_WORD_RE.findall(name):
        if tok.lower() in _INITIALS_STOPWORDS:
            continue
        if tok[0].isalpha():
            out.append(tok[0].upper())
    return "".join(out)


def acronym_matches_name(acronym: str | None, name: str | None) -> bool:
    """True when *acronym*'s letters equal the initials of *name*.

    Used to pick the CURRENT acronym from ROR's several ``acronym`` entries
    (``NIST`` matches ``National Institute of Standards and Technology``; the
    historical ``NBS`` does not).
    """
    if not acronym or not name:
        return False
    letters = "".join(ch for ch in acronym.upper() if ch.isalpha())
    return bool(letters) and letters == name_initials(name)


def seg_matches_needle(seg: str | None, needle: str | None) -> bool:
    """Match a host/subdomain segment against a token/acronym: substring, or a
    shared leading prefix of ≥3 chars in either direction ("chem" ← "chemistry").
    Shared by the department probe and the subdomain-acronym search-term rule.
    """
    seg = (seg or "").lower()
    needle = (needle or "").lower()
    if not seg or not needle:
        return False
    if needle in seg:
        return True
    return (
        min(len(seg), len(needle)) >= 3
        and (needle.startswith(seg) or seg.startswith(needle))
    )


# Administrative / back-office units. English only (German deferred). Matched
# after stripping a leading "Office of" / "Department of" style prefix, so
# "Office of Finance" is admin but "Office of Research" is not.
#
# The test each entry has to pass: is there anything a reviewer could open to
# confirm this unit? A chemicals company has no web page, registry entry or
# institutional spelling for its accounts-payable desk or its receiving bay —
# the phrase says WHERE IN the customer the mail goes, not which unit the
# record is. Anything with a checkable existence (a named lab, a faculty, a
# product division) stays out.
_ADMIN_UNIT_TERMS = {
    # Finance / procurement back office.
    "accounts payable", "accounts receivable", "ap", "ar",
    # The clipped SAP spellings of the same desk. `_admin_token_stem` folds
    # "accts"→"acct", so one entry per phrase covers both plural forms.
    "acct pay", "acct payable", "acct receivable",
    "finance", "financial services", "billing", "invoicing",
    "invoice processing", "purchasing", "procurement", "controlling",
    "treasury", "bursar", "comptroller", "general accounting",
    "shared services",
    # Goods-in / goods-out and mail. Every site has these and none of them
    # says whose site it is. `search_terms.has_identifying_token` already
    # emptied Search Term 2 for most of them; listing them here extends the
    # same judgement to the review flags and the department-domain probe,
    # which that rule does not reach.
    "receiving", "shipping", "shipping and receiving",
    "shipping & receiving", "shipping/receiving",
    "stores", "storeroom", "stockroom",
    "mail", "mail room", "mailroom",
    # Undifferentiated administration. "Business Administration" and
    # "Administrative Sciences" survive: they carry a second word, so they
    # reduce to something this vocabulary does not state.
    "administration", "administrative", "admin",
}

#: Desks that are only desks WITH their generic word. "Business Office" is the
#: campus bursar; a bare "Business" is a school of business, and stripping the
#: trailing "Office" the way :func:`_admin_canonical` does would collapse the
#: two. These are matched on :func:`_admin_phrase_form` instead — prefix and
#: plurals removed, but the qualifier and the generic word both kept — so the
#: phrase has to arrive whole.
_ADMIN_UNIT_PHRASES = {
    "business office", "main office", "front office",
    "corporate office", "general office", "administrative office",
}
_ADMIN_PREFIXES = (
    "office of ", "department of ", "dept of ", "dept. ", "dept ",
    "division of ", "div of ", "div. ",
)

#: Generic organisational words that carry no meaning of their own in a unit
#: name. "Procurement" and "Procurement Services" are one desk; so are
#: "Accounts Payable" and "Accounts Payable Department". Stripped from the END
#: repeatedly, so "Billing Services Department" reduces to "billing".
_ADMIN_SUFFIX_WORDS = frozenset({
    "services", "service", "department", "departments", "dept", "office",
    "offices", "team", "teams", "group", "unit", "units", "division",
    "div", "desk", "section", "center", "centre", "centres", "centers",
})

#: Generic qualifiers that scope a desk without changing which desk it is —
#: "Central Purchasing" and "Corporate Finance" are the purchasing and finance
#: desks. Stripped from the FRONT, repeatedly.
_ADMIN_QUALIFIERS = frozenset({
    "central", "centralised", "centralized", "corporate", "global",
    "regional", "main", "general", "shared", "group",
})


def _admin_token_stem(token: str) -> str:
    """A token with a trailing plural "s" removed, so "accounts" and "account"
    — and "payables" and "payable" — are one word. Two-letter tokens are left
    alone: "ap" and "ar" are the abbreviations themselves."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _admin_canonical(text: str) -> str:
    """*text* reduced to the words that say WHICH desk it is.

    Leading "Office of"/"Department of", leading generic qualifiers, trailing
    generic organisational words, punctuation, and plurals all removed. What
    is left is compared against :data:`_ADMIN_UNIT_TERMS` in the same reduced
    form, so the vocabulary states each desk once instead of once per spelling
    a source system happens to use.
    """
    t = text.strip().lower()
    for pref in _ADMIN_PREFIXES:
        if t.startswith(pref):
            t = t[len(pref):].strip()
            break
    t = re.sub(r"[^a-z/& ]", " ", t)
    words = [w for w in t.split() if w]
    while len(words) > 1 and words[0] in _ADMIN_QUALIFIERS:
        words = words[1:]
    while len(words) > 1 and words[-1] in _ADMIN_SUFFIX_WORDS:
        words = words[:-1]
    return " ".join(_admin_token_stem(w) for w in words)


def _admin_phrase_form(text: str) -> str:
    """*text* normalised only as far as spelling, for :data:`_ADMIN_UNIT_PHRASES`.

    A leading "Office of"/"Department of", punctuation and plurals go; the
    leading qualifier and the trailing generic word STAY. "Business Office"
    stays "business office" here, where :func:`_admin_canonical` would reduce
    it to "business" and take a business school with it.
    """
    t = text.strip().lower()
    for pref in _ADMIN_PREFIXES:
        if t.startswith(pref):
            t = t[len(pref):].strip()
            break
    t = re.sub(r"[^a-z/& ]", " ", t)
    return " ".join(_admin_token_stem(w) for w in t.split() if w)


#: The vocabulary in the same reduced form the input is reduced to, built once.
_ADMIN_UNIT_STEMS = frozenset(
    _admin_canonical(term) for term in _ADMIN_UNIT_TERMS
) | {"a/p", "a/r"}

#: The whole-phrase vocabulary, in its own reduced form.
_ADMIN_UNIT_PHRASE_FORMS = frozenset(
    _admin_phrase_form(phrase) for phrase in _ADMIN_UNIT_PHRASES
)


def is_admin_unit(text: str | None) -> bool:
    """True when *text* names an administrative / back-office desk (accounts
    payable, finance, billing, procurement, treasury, …). English only.

    Matched on the words that say WHICH desk it is: a leading "Office of",
    a leading generic qualifier, a trailing generic organisational word and a
    plural are all removed first, so the vocabulary states each desk once
    rather than once per spelling an SAP operator used.

    ``'Accounts Payable'``            → True
    ``'Accounts Payable Department'`` → True
    ``'Account Payable'``             → True
    ``'Procurement Services'``        → True
    ``'Central Purchasing'``          → True
    ``'Office of Finance'``           → True
    ``'Central Receiving'``           → True
    ``'Business Office'``             → True
    ``'Office of Research'``          → False
    ``'School of Business'``          → False
    ``'Business Administration'``     → False
    ``'Oncology Lab'``                → False

    A second vocabulary (:data:`_ADMIN_UNIT_PHRASES`) holds the desks that are
    only desks with their generic word attached, and is matched on the whole
    phrase rather than on the reduced form.
    """
    if not text or not text.strip():
        return False
    if _admin_canonical(text) in _ADMIN_UNIT_STEMS:
        return True
    return _admin_phrase_form(text) in _ADMIN_UNIT_PHRASE_FORMS
