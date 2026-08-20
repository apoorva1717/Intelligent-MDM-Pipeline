"""Address Stage 1 — clean, extract, route, cross-check, classify, normalise.

Runs after name enrichment inside ``POST /enrich``. The single entry point
is :func:`process_address`, which takes the post-name-enrichment record
state plus an LLM client and returns an :class:`AddressResult`. No
network or external API call is issued beyond an optional LLM residual
classification on ambiguous values in the secondary street slots
(street_2 … street_5).

Pipeline order per record:

    1. Clean    — trim/collapse whitespace, strip stray punctuation
    2. Extract  — regex pulls PO Box, sub-locations, c/o, logistics,
                  mail codes from street/street_2 … street_5; splits a
                  full address jammed into one field into components
    3. Cross    — flag-only checks on duplicates, conflicts, org/name
                  bleed-through (no field moves)
    4. LLM      — GPT-4o-mini classifies residual values that don't
                  look like real street addresses; high-confidence
                  classifications act on the value, low confidence
                  raises an information code only
    5. Normalise — abbreviation/directional whole-word replacements on
                  the cleaned street fields
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from llm.openai_client import OpenAIClient
from llm.prompts import (
    ADDRESS_RESIDUAL_SYSTEM_PROMPT,
    ADDRESS_RESIDUAL_USER_PROMPT_TEMPLATE,
)
# Shared junk patterns / person detector — preprocess does not import this
# module, so this import is acyclic. Used to scrub street values so the
# cleaned-street OUTPUT is free of URLs, phone numbers, emails and stray
# person references (the email/contact VALUES are captured upstream in
# preprocessing; here we only clean the street string).
from enrichment.preprocess import (
    _EMAIL_RE,
    _PHONE_RE,
    _URL_RE,
    _extract_addresses,
    _street_person_name,
)
from utils.name_slots import DEPT_SLOTS
from utils.text_utils import is_logistics_location, smart_title_case

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abbreviation lookup tables
# ---------------------------------------------------------------------------

STREET_TYPE_ABBREVIATIONS: dict[str, str] = {
    "STREET": "St", "STR": "St",
    "AVENUE": "Ave", "AVE": "Ave",
    "BOULEVARD": "Blvd", "BLVD": "Blvd",
    "DRIVE": "Dr", "DR": "Dr",
    "ROAD": "Rd", "RD": "Rd",
    "LANE": "Ln", "LN": "Ln",
    "COURT": "Ct", "CT": "Ct",
    "HIGHWAY": "Hwy", "HWY": "Hwy",
    "PARKWAY": "Pkwy", "PKWY": "Pkwy",
    "ROUTE": "Rte", "RT": "Rte",
}

DIRECTIONAL_ABBREVIATIONS: dict[str, str] = {
    "NORTHWEST": "NW", "NORTHEAST": "NE",
    "SOUTHWEST": "SW", "SOUTHEAST": "SE",
    "NORTH": "N", "SOUTH": "S",
    "EAST": "E", "WEST": "W",
}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class AddressResult:
    street_cleaned: str | None = None
    street_2_cleaned: str | None = None
    street_3_cleaned: str | None = None
    street_4_cleaned: str | None = None
    street_5_cleaned: str | None = None
    suite: str | None = None
    building: str | None = None
    floor: str | None = None
    room: str | None = None
    unit: str | None = None
    mail_stop: str | None = None
    po_box_extracted: str | None = None
    care_of_enriched: str | None = None
    unloading_point: str | None = None
    mail_code: str | None = None
    # Department detected in an address field (via c/o + Attn payload or
    # LLM residual). The orchestrator places it into the first empty
    # name slot (name2, then name3); see ``merge_into_result``.
    department_addendum: str | None = None
    # Inferred city/state/zip when a full address was crammed into one
    # field. The orchestrator only populates the record's empty slots.
    city_inferred: str | None = None
    state_inferred: str | None = None
    zip_inferred: str | None = None
    unclear_address_info: str | None = None
    # Name-field values rewritten by the address stage. When a street
    # address that a later tier wrote into a name field (e.g. "104 Rhines
    # Hall" in Name 2) is pulled out into a street slot, the cleaned
    # remainder — or ``None`` when the whole value was an address — is
    # recorded here so the orchestrator can update the name output and
    # mark the slot cleared. See ``merge_into_result``.
    name_overrides: dict[str, str | None] = field(default_factory=dict)
    address_issues: list[str] = field(default_factory=list)

    def issue(self, code: str) -> None:
        if code not in self.address_issues:
            self.address_issues.append(code)


# ---------------------------------------------------------------------------
# Step 1 — Cleaning
# ---------------------------------------------------------------------------

_STRAY_DOTS_RE = re.compile(r"\.{2,}")
_MULTI_SPACE_RE = re.compile(r"\s+")
# Leading connector words that prefix a secondary address line in some
# exports ("ALSO 250 Central Ave", "AND 350 Central Ave"). They carry no
# address meaning and are stripped.
_LEADING_CONNECTOR_RE = re.compile(r"^(?:also|and|or|plus|&)\b[\s:,\-]*", re.IGNORECASE)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value)
    v = v.replace("\t", " ").replace(" ", " ")
    v = _STRAY_DOTS_RE.sub(".", v)
    v = _MULTI_SPACE_RE.sub(" ", v).strip()
    # Strip leading/trailing punctuation but keep meaningful interior
    # markers (c/o, P.O., M6-0744).
    v = v.strip(",;|")
    # Trailing comma left after collapse.
    while v.endswith(",") or v.endswith(";"):
        v = v[:-1].rstrip()
    # Leading/trailing single dashes (a "lone" dash, not embedded).
    v = re.sub(r"^-+\s*", "", v)
    v = re.sub(r"\s*-+$", "", v)
    # Drop a leading connector word ("ALSO", "AND", …).
    v = _LEADING_CONNECTOR_RE.sub("", v)
    v = v.strip()
    return v or None


def _scrub_street(value: str | None) -> str | None:
    """Remove non-address junk from a street value: URLs, phone/fax
    numbers, emails, and a whole-value person reference.

    This guarantees the cleaned-street OUTPUT is junk-free regardless of
    whether the orchestrator handed us the preprocessed value or fell back
    to the raw original. The email/contact themselves are captured in
    preprocessing; here we only tidy the street string.
    """
    if not value:
        return None
    # A street slot that is entirely a person reference → drop it (the
    # contact is captured upstream).
    if _street_person_name(value):
        return None
    out = _URL_RE.sub(" ", value)
    out = _PHONE_RE.sub(" ", out)
    out = _EMAIL_RE.sub(" ", out)
    out = _strip_residue(out)
    return out or None


# ---------------------------------------------------------------------------
# Step 2 — Extractors
# ---------------------------------------------------------------------------
#
# All extractors share one shape: given a working string, return
# ``(remaining_string, extracted_value_or_None)``. They consume the first
# match from left to right, remove it from the source, and tidy
# whitespace/punctuation residue.


def _strip_residue(text: str) -> str:
    """Collapse whitespace and trim stray separators after a removal."""
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    text = text.strip(" ,;|-")
    text = re.sub(r"\s*,\s*,+", ", ", text)
    return text.strip()


_PO_BOX_RE = re.compile(
    r"\b(?:P\.?\s*O\.?\s*Box|POB|Post\s+Office\s+Box)\s+(\w+)\b",
    re.IGNORECASE,
)


def _extract_po_box(text: str) -> tuple[str, str | None]:
    if not text:
        return text, None
    m = _PO_BOX_RE.search(text)
    if not m:
        return text, None
    value = m.group(0).strip()
    new = text[: m.start()] + text[m.end():]
    return _strip_residue(new), value


# Sub-location markers. Order matters: most specific first so "Mail Stop"
# wins over a bare "MS".
_SUITE_PATTERNS = [
    # "Campus Box 7212" / "Campus Box 90" — a mail stop; keep the full phrase.
    (re.compile(r"\b(Campus\s+Box\s+[\w\-]+)\b", re.IGNORECASE), "mail_stop"),
    (re.compile(r"\b(?:Mail\s+Stop|MS)\s+(\w[\w\-]*)\b", re.IGNORECASE), "mail_stop"),
    (re.compile(r"\b(?:Suite|Ste\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "suite"),
    (re.compile(r"\b(?:Bldg\.?|Building)\s+(\w[\w\-]*)\b", re.IGNORECASE), "building"),
    # Marker-before-value ("Floor 3", "Fl. 3").
    (re.compile(r"\b(?:Floor|Fl\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "floor"),
    # Value-before-marker ("7th Floor", "22nd Floor", "3 Fl"). The ordinal
    # suffix is optional; only the number is kept as the floor value.
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)?\s+(?:Floor|Fl)\b\.?", re.IGNORECASE), "floor"),
    (re.compile(r"\b(\d+)F\b"), "floor"),
    # A bare trailing ordinal as its own segment ("51 Sleeper St, 7th") is a
    # floor. Anchored to a comma/start on the left and end on the right so a
    # street name ("5th Ave") is never matched.
    (re.compile(r"(?:^|,)\s*(\d+)(?:st|nd|rd|th)\s*$", re.IGNORECASE), "floor"),
    # Room with optional filler between the marker and the value:
    # "Room 12", "Rm. 5", "Room number: F107", "Room No. 3", "Room #4".
    (re.compile(
        r"\b(?:Room|Rm)\b\.?\s*(?:number|no|nr)?\.?\s*[:#]?\s*(\w[\w\-]*)\b",
        re.IGNORECASE,
    ), "room"),
    # "Lab 576" / "Lab A12" — a lab room (Lab + a numeric id). Keeps the full
    # phrase. "Smith Lab" (no id) is left alone — it is a named lab, not a room.
    (re.compile(r"\b(Lab\.?\s+\w*\d[\w\-]*)\b", re.IGNORECASE), "room"),
    # A trailing room code ("1500 Graduate Ln A104", "…C13.217", "…F107"): a
    # letter followed by 2+ digits (optionally .digits) as the final token. The
    # street name + house number stays; only the room code is pulled.
    (re.compile(r"(?:^|\s)([A-Za-z]\d{2,}(?:\.\d+)?)\s*$"), "room"),
    (re.compile(r"\bUnit\s+(\w[\w\-]*)\b", re.IGNORECASE), "unit"),
    # "#" alone is shorthand for Suite.
    (re.compile(r"#\s*(\w[\w\-]*)\b"), "suite"),
]

# Bare marker (no value) — used after the value patterns fail to detect
# "Ste" or "Suite" with nothing after them.
_BARE_MARKER_RE = re.compile(
    r"\b(?:Suite|Ste|Bldg|Building|Floor|Fl|Room|Rm|Unit|Mail\s+Stop|MS)\b\s*$",
    re.IGNORECASE,
)
# Subset of bare markers safe to DELETE when value-less: interior
# sub-locations that are meaningless without a number ("Ste", "Room").
# "Building"/"Bldg" is excluded — it can be a descriptor for a named
# building ("Research I Bldg"), so it is flagged but kept.
_BARE_MARKER_DELETE_RE = re.compile(
    r"\b(?:Suite|Ste|Floor|Fl|Room|Rm|Unit|Mail\s+Stop|MS)\b\s*$",
    re.IGNORECASE,
)


def _is_identifier_like(token: str) -> bool:
    """A sub-location value (suite/building/floor/room/unit/mail-stop
    identifier) must look like an identifier, not a regular word.

    Accepted: contains a digit (``400``, ``A5``, ``12B``) OR is 1-2
    characters long (``C``, ``A``, ``NW``). Rejected: alphabetic words
    of 3+ characters such as ``Annex``, ``Penthouse`` — those indicate a
    descriptive phrase and the marker should be left in the residual for
    the LLM step to classify.
    """
    if not token:
        return False
    if any(c.isdigit() for c in token):
        return True
    if len(token) <= 2:
        return True
    return False


def _extract_sublocations(text: str) -> tuple[str, dict[str, str], bool]:
    """Walk the sub-location patterns and pull every match.

    Returns ``(remaining, found, bare_marker)`` where ``found`` maps the
    target field name to the extracted value (the first match wins for a
    given field) and ``bare_marker`` is True when a marker word was
    detected but had no value attached.
    """
    if not text:
        return text, {}, False
    found: dict[str, str] = {}
    work = text
    for pat, target in _SUITE_PATTERNS:
        while True:
            m = pat.search(work)
            if not m:
                break
            captured = m.group(1).strip() if m.groups() else m.group(0).strip()
            # Skip non-identifier captures: e.g. "Building Annex" should
            # NOT be parsed as building="Annex". Stop searching this
            # pattern so the phrase stays in the residual.
            if not _is_identifier_like(captured):
                break
            if target not in found:
                found[target] = captured
            work = work[: m.start()] + work[m.end():]
            work = _strip_residue(work)
    # A marker word with no value attached ("Ste", "Room" on its own) is
    # noise — flag it, and drop the value-requiring ones from the residual
    # so they do not linger in the cleaned street value. "Building"/"Bldg"
    # is kept (it may name a building, e.g. "Research I Bldg").
    bare = bool(_BARE_MARKER_RE.search(work))
    del_m = _BARE_MARKER_DELETE_RE.search(work)
    if del_m:
        work = _strip_residue(work[: del_m.start()] + work[del_m.end():])
    return work, found, bare


# ``att?n+`` also catches the common misspelling "Atnn:" (and "attnn").
_CARE_OF_RE = re.compile(
    r"(?:c\s*/\s*o|att?n+(?:ention|tion)?|ATT)\s*[:\-\.]?\s*(.+)",
    re.IGNORECASE,
)

# Payload after a c/o or Attn prefix that names a department/division — we
# route it to the next empty name field rather than ``care_of_enriched``.
_DEPARTMENT_PAYLOAD_RE = re.compile(
    r"\b(?:Department|Dept\.?|Division|Div\.?)\b",
    re.IGNORECASE,
)


def _looks_like_department(value: str | None) -> bool:
    return bool(value and _DEPARTMENT_PAYLOAD_RE.search(value))


def _extract_care_of(text: str) -> tuple[str, str | None]:
    if not text:
        return text, None
    m = _CARE_OF_RE.search(text)
    if not m:
        return text, None
    payload = m.group(1)
    before = text[: m.start()]
    # If the payload runs on into a street address ("Att. Bayard Huck 200
    # Clarendon Street 22nd Floor"), only the leading person/company part is the
    # care_of; the street (and everything after it) is returned to the working
    # string so the street / sub-location extractors handle it downstream.
    addr_m = _STREET_START_RE.search(payload)
    if addr_m and addr_m.start() > 0:
        care = payload[: addr_m.start()].strip(" ,;-.")
        tail = payload[addr_m.start():].strip()
        remaining = _strip_residue((before.rstrip(" ,;-") + " " + tail).strip())
        return remaining, (care or None)
    value = payload.strip(" ,;-.")
    new = before.rstrip(" ,;-")
    return _strip_residue(new), value or None


_LOGISTICS_KEYWORD_RE = re.compile(
    r"^\s*(?:Loading\s+Dock|Dock|Gate|Warehouse|Shipping|Receiving)"
    r"(?:\s*[:\-]?\s*(.+))?$",
    re.IGNORECASE,
)
_DELIVER_TO_RE = re.compile(
    r"\bDeliver\s+to\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)


def _extract_logistics(text: str) -> tuple[str, str | None]:
    if not text:
        return text, None
    m = _DELIVER_TO_RE.search(text)
    if m:
        value = m.group(1).strip(" ,;-")
        new = text[: m.start()].rstrip(" ,;-")
        return _strip_residue(new), value or None
    # Keyword-at-start variant — the whole value is logistics if it
    # starts with one of the dock/gate/warehouse keywords.
    m2 = _LOGISTICS_KEYWORD_RE.match(text)
    if m2:
        return "", text.strip()
    # A named logistics facility ("Southeast Distribution Ctr") anywhere in
    # the value — the whole value is an unloading point.
    if is_logistics_location(text):
        return "", text.strip()
    return text, None


_MAIL_CODE_EXPLICIT_RE = re.compile(
    r"\bMAIL\s*CODE\s*[:\-]?\s*([\w\-]+)\b",
    re.IGNORECASE,
)
_MAIL_CODE_COMPLEX_RE = re.compile(r"\b([A-Z]\d-\d{4})\b")
# Bare-token mail-code candidate: ALL-CAPS letters then digits, used only
# in street_2 / street_3 when the token doesn't double as a street-type
# abbreviation (St, Ave, Blvd, etc.).
_MAIL_CODE_BARE_RE = re.compile(r"\b([A-Z]{2,4}\d{1,4})\b")
_STREET_TYPE_ABBREVS = {
    "ST", "AVE", "BLVD", "DR", "RD", "LN", "CT", "HWY", "PKWY", "RTE",
    "STR", "PL", "TER", "PKY", "CIR", "SQ", "WAY",
}


def _extract_mail_code(text: str, allow_bare: bool) -> tuple[str, str | None]:
    if not text:
        return text, None
    m = _MAIL_CODE_EXPLICIT_RE.search(text)
    if m:
        value = m.group(1).strip()
        new = text[: m.start()] + text[m.end():]
        return _strip_residue(new), value
    m = _MAIL_CODE_COMPLEX_RE.search(text)
    if m:
        value = m.group(1).strip()
        new = text[: m.start()] + text[m.end():]
        return _strip_residue(new), value
    if allow_bare:
        for m in _MAIL_CODE_BARE_RE.finditer(text):
            token = m.group(1)
            if token.upper() in _STREET_TYPE_ABBREVS:
                continue
            new = text[: m.start()] + text[m.end():]
            return _strip_residue(new), token
    return text, None


_FULL_ADDRESS_RE = re.compile(
    r"^(.+?),\s*([A-Za-z\s]+?),?\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$",
)


def _split_full_address(
    text: str,
) -> tuple[str, str | None, str | None, str | None]:
    """If *text* contains a full ``street, city, ST zip`` form, return the
    isolated street part plus the inferred city/state/zip components.
    Returns ``(street, city, state, zip)``; any unmatched component is
    None and the original text is returned unchanged.
    """
    if not text:
        return text, None, None, None
    m = _FULL_ADDRESS_RE.match(text.strip())
    if not m:
        return text, None, None, None
    street, city, state, zipc = m.groups()
    return (
        street.strip(),
        city.strip() or None,
        state.strip() or None,
        zipc.strip() or None,
    )


# ---------------------------------------------------------------------------
# Step 3 — Cross-field checks
# ---------------------------------------------------------------------------

_STREET_TYPE_WORD_RE = re.compile(
    r"\b(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Way|Pl|Place|Ter|Terrace)\b\.?",
    re.IGNORECASE,
)
_HOUSE_NUMBER_RE = re.compile(r"\b\d+\b")
_ORG_KEYWORD_RE = re.compile(
    r"\b(?:University|Universit[äa]t|Institute|Institut|"
    r"Corp(?:oration)?|Inc\.?|LLC|Ltd\.?|Limited|Hospital|"
    r"College|Faculty|School)\b",
    re.IGNORECASE,
)
# "University Centre" (and acronyms of centre) is a building / place name that
# legitimately belongs in a Street field — not an org name in the address. Strip
# these phrases before applying the org-keyword checks so they don't trigger
# G1-CROSS-002.
_UNIVERSITY_CENTRE_RE = re.compile(
    r"\bUniversit(?:y|[äa]t)\s+(?:Centre|Center|Ctr|Ctre|Cntr|Cent)\b\.?",
    re.IGNORECASE,
)
_NAME_STREET_LIKE_RE = re.compile(
    r"\b\d+\s+\w+\s+(?:St|Ave|Blvd|Rd|Dr|Ln|Hwy|Pkwy)\b\.?",
    re.IGNORECASE,
)


# Splitting a street value that carries a trailing access / location
# qualifier after the street address: "300 Tech Park Dr NEAR LOADING DOCK B",
# "1100 Commerce Way BEHIND WAREHOUSE 4", "750 Industrial Pkwy GATE C EAST
# ENTRANCE". The qualifier starts right after the street-type word with a
# positional preposition or an access/logistics noun, and is moved verbatim
# to the next empty street slot (kept as a street line, NOT pulled into
# unloading_point — a bare "Loading Dock B" with no street is still routed
# there by _extract_logistics).
_STREET_TYPES_ALT = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Way|Pl|Place|Ter|Terrace|Cir|Circle"
)
# Start of a street address inside a longer string: a house number, then one or
# more words, then a street-type word. Used by ``_extract_care_of`` to split the
# street part off a c/o / Attn payload. Defined here (after _STREET_TYPES_ALT)
# but only USED at call time, so definition order is fine.
_STREET_START_RE = re.compile(
    rf"\b\d+\s+.*?\b(?:{_STREET_TYPES_ALT})\b\.?",
    re.IGNORECASE,
)
_LOC_QUALIFIER_LEAD = (
    r"Near|Behind|Beside|Adjacent|Across|Opposite|Next\s+To|In\s+Front\s+Of|"
    r"Gate|Dock|Loading|Unloading|Warehouse|Entrance|Entry|Bay|Ramp|Elevator"
)
_STREET_QUALIFIER_SPLIT_RE = re.compile(
    rf"^(?P<street>.*?\b(?:{_STREET_TYPES_ALT})\b\.?)\s+"
    rf"(?P<qual>(?:{_LOC_QUALIFIER_LEAD})\b.*)$",
    re.IGNORECASE,
)


def _split_location_qualifier(value: str | None) -> tuple[str, str] | None:
    """Split "<street address> <location qualifier>" into (street, qualifier)
    or return None when the value is not that shape."""
    if not value or not value.strip():
        return None
    m = _STREET_QUALIFIER_SPLIT_RE.match(value.strip())
    if not m:
        return None
    street = m.group("street").strip(" ,;-")
    qual = m.group("qual").strip(" ,;-")
    if not street or not qual or not _HOUSE_NUMBER_RE.search(street):
        return None
    return street, qual


def _looks_like_street(value: str | None) -> bool:
    if not value:
        return False
    return bool(
        _HOUSE_NUMBER_RE.search(value)
        and _STREET_TYPE_WORD_RE.search(value)
    )


# A UK postcode ("BT7 1NH", "SW1A 1AA") — used to recognise a location-only
# street segment ("Belfast BT7 1NH") that belongs in the postal field.
_UK_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b", re.IGNORECASE)
# A named building: a descriptor phrase ending in a building word. Covers the
# "House"/"Hall" forms the scope table lists as buildings ("Aster House",
# "Polaris House") plus the "... Building"/"... Bldg" suffix form ("The Sherard
# Bldg", "Emerging Technologies Building"). A leading house number makes it a
# street address, not a bare building.
_BUILDING_SUFFIX_RE = re.compile(
    r"\S.*\s(?:Building|Bldg|House|Hall|Pavilion|Tower)\.?\s*$",
    re.IGNORECASE,
)
_CAMPUS_FRAGMENT_RE = re.compile(
    r"\b(?:Campus|Science\s+Park|Research\s+Park|Technology\s+Park|"
    r"Business\s+Park|Innovation\s+Campus|Science\s+&\s+Innovation)\b",
    re.IGNORECASE,
)


def _named_building_value(seg: str | None) -> str | None:
    """Return the segment when it names a building (routed to the Building
    field), else None. A leading house number or a full street pattern means it
    is a street address, not a bare building."""
    if not seg or not seg.strip():
        return None
    s = seg.strip()
    if re.match(r"^\d+\s", s):
        return None
    if _looks_like_street(s):
        return None
    if _looks_like_department(s):
        return None
    return s if _BUILDING_SUFFIX_RE.match(s) else None


def _is_campus_fragment(seg: str | None) -> bool:
    """True for a campus / science-park / site fragment (its own street slot)."""
    return bool(seg and _CAMPUS_FRAGMENT_RE.search(seg) and not _looks_like_street(seg))


def _segment_is_location_only(
    seg: str, city: str | None, state: str | None, zip_code: str | None,
) -> bool:
    """True when a segment is only a city / region / postcode already carried in
    its own field ("Belfast BT7 1NH", "San Francisco") — dropped from the street.
    """
    if not seg or not seg.strip() or _looks_like_street(seg):
        return False
    s = seg.strip()
    low = s.lower()
    for own in (city, state, zip_code):
        if own and low == own.strip().lower():
            return True
    has_postcode = bool(_UK_POSTCODE_RE.search(s)) or bool(re.search(r"\b\d{5}(?:-\d{4})?\b", s))
    tmp = _UK_POSTCODE_RE.sub(" ", s)
    tmp = re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", tmp)
    for own in (city, state):
        if own and own.strip():
            tmp = re.sub(re.escape(own.strip()), " ", tmp, flags=re.IGNORECASE)
    tmp = re.sub(r"[,\s]+", " ", tmp).strip()
    # Nothing left once the postcode + own city/region are removed → location only.
    return has_postcode and tmp == ""


def _cross_field_checks(
    res: AddressResult,
    street_cleaned: str | None,
    street_2_cleaned: str | None,
    name1: str | None,
    dept_names: list[str | None],
    po_box_present: bool,
) -> None:
    # Street == Street_2 duplicate.
    if (
        street_cleaned and street_2_cleaned
        and street_cleaned.strip().lower()
            == street_2_cleaned.strip().lower()
    ):
        res.issue("G3-ADDR-012")  # duplicate street/street_2

    # Both Street and Street_2 look like real addresses.
    if _looks_like_street(street_cleaned) and _looks_like_street(street_2_cleaned):
        res.issue("G3-ADDR-013")

    # PO Box + Street populated (informational).
    if po_box_present and street_cleaned and street_cleaned.strip():
        res.issue("G3-ADDR-014")

    # Name fields contain street-like pattern.
    for nm in (name1, *dept_names):
        if nm and _NAME_STREET_LIKE_RE.search(nm):
            res.issue("G1-CROSS-001")
            break

    # Street has org keyword without any street-type word. "University Centre"
    # (and acronyms) is a building name, not an org — strip it before checking.
    if street_cleaned:
        without_centre = _UNIVERSITY_CENTRE_RE.sub(" ", street_cleaned)
        if (
            _ORG_KEYWORD_RE.search(without_centre)
            and not _STREET_TYPE_WORD_RE.search(street_cleaned)
        ):
            res.issue("G1-CROSS-002")


# ---------------------------------------------------------------------------
# Step 4 — LLM residual classification
# ---------------------------------------------------------------------------

_RESIDUAL_CONFIDENCE_THRESHOLD = 0.85


async def _classify_residual(
    value: str,
    name1: str | None,
    street: str | None,
    city: str | None,
    country: str | None,
    llm_client: OpenAIClient,
) -> tuple[str | None, float]:
    """Return ``(classification, confidence)`` or ``(None, 0.0)`` on error."""
    prompt = ADDRESS_RESIDUAL_USER_PROMPT_TEMPLATE.format(
        value=value,
        name1=name1 or "",
        street=street or "",
        city=city or "",
        country=country or "",
    )
    try:
        extraction = await llm_client.extract_json(
            ADDRESS_RESIDUAL_SYSTEM_PROMPT, prompt,
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Address residual LLM call failed: %s", exc)
        return None, 0.0
    cls = extraction.get("classification")
    raw_conf = extraction.get("confidence")
    try:
        conf = float(raw_conf) if raw_conf is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    if not isinstance(cls, str):
        return None, conf
    return cls.strip().upper(), conf


def _looks_unambiguous(value: str | None) -> bool:
    """A residual is unambiguous (no LLM needed) when it already looks
    like a street address: contains a house number AND a street-type
    word."""
    return _looks_like_street(value)


async def _apply_residual_llm(
    res: AddressResult,
    secondary: dict[str, str | None],
    name1: str | None,
    street: str | None,
    city: str | None,
    country: str | None,
    llm_client: OpenAIClient | None,
) -> dict[str, str | None]:
    """Run residual classification on whatever remains in the secondary
    street slots (street_2 … street_5) after extraction. ``secondary``
    maps slot name → value; returns the same dict, possibly mutated
    (a slot reclassified as department/mail-code/logistics is blanked)."""
    if llm_client is None:
        return secondary

    for slot_name, current in list(secondary.items()):
        if not current or not current.strip():
            continue
        if _looks_unambiguous(current):
            continue
        cls, conf = await _classify_residual(
            current, name1, street, city, country, llm_client,
        )
        if cls is None:
            res.issue("G1-ADDR-009")
            continue
        if conf < _RESIDUAL_CONFIDENCE_THRESHOLD:
            res.issue("G1-ADDR-009")
            continue

        if cls == "DEPARTMENT":
            res.issue("G1-ADDR-011")
            if res.department_addendum is None:
                res.department_addendum = current.strip()
            secondary[slot_name] = None
        elif cls == "PERSON_NAME":
            res.issue("G1-CROSS-003")
        elif cls == "ORG_NAME":
            res.issue("G1-CROSS-002")
        elif cls == "MAIL_CODE":
            if not res.mail_code:
                res.mail_code = current.strip()
            secondary[slot_name] = None
        elif cls == "LOGISTICS":
            if not res.unloading_point:
                res.unloading_point = current.strip()
            secondary[slot_name] = None
        elif cls == "STREET_ADDRESS":
            pass
        elif cls == "UNCLEAR":
            res.issue("G1-ADDR-009")
            if not res.unclear_address_info:
                res.unclear_address_info = current.strip()
        else:
            res.issue("G1-ADDR-009")

    return secondary


# ---------------------------------------------------------------------------
# Step 5 — Abbreviation normalisation
# ---------------------------------------------------------------------------

_STREET_TYPE_NORMALISE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in STREET_TYPE_ABBREVIATIONS) + r")\b\.?",
    re.IGNORECASE,
)
_DIRECTIONAL_TOKENS = list(DIRECTIONAL_ABBREVIATIONS.keys())
_DIRECTIONAL_START_RE = re.compile(
    r"^(" + "|".join(_DIRECTIONAL_TOKENS) + r")\b",
    re.IGNORECASE,
)
_DIRECTIONAL_END_RE = re.compile(
    r"\b(" + "|".join(_DIRECTIONAL_TOKENS) + r")$",
    re.IGNORECASE,
)


def _normalise_street_value(value: str | None) -> str | None:
    if value is None:
        return None
    out = value

    def _swap_type(m: re.Match[str]) -> str:
        return STREET_TYPE_ABBREVIATIONS[m.group(1).upper()]
    out = _STREET_TYPE_NORMALISE_RE.sub(_swap_type, out)

    def _swap_dir(m: re.Match[str]) -> str:
        return DIRECTIONAL_ABBREVIATIONS[m.group(1).upper()]
    out = _DIRECTIONAL_START_RE.sub(_swap_dir, out)
    out = _DIRECTIONAL_END_RE.sub(_swap_dir, out)
    return _strip_residue(out) or None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _reduce_primary_street(
    res: AddressResult,
    primary: str | None,
    *,
    city: str | None,
    state: str | None,
    zip_code: str | None,
) -> list[str] | None:
    """Per-segment reduction of a mixed primary street value, per the scope
    table. Runs only for the complex cases — a c/o line, a named building, or a
    pipe separator present — so simple "51 Sleeper St, 7th" values keep their
    existing per-value extraction path.

    Splits the value on pipe/comma and routes each segment: a c/o line →
    Care Of, a named building → Building (a second building overflows to the
    next street slot), a city/region/postcode → dropped, a campus/park →
    its own street slot. Returns the ordered street lines (main street(s) first,
    then campus fragments, then overflow buildings/residue), or None when the
    value is a simple case the caller should handle as before.
    """
    if not primary:
        return None
    has_pipe = "|" in primary
    segs = [s.strip(" ;-") for s in re.split(r"[|,]", primary) if s and s.strip(" ;-")]
    if len(segs) < 2:
        return None
    has_building = any(_named_building_value(s) for s in segs)
    has_care_of = any(_CARE_OF_RE.match(s) for s in segs)
    if not (has_pipe or has_building or has_care_of):
        return None

    addresses: list[str] = []
    campuses: list[str] = []
    overflow: list[str] = []
    for seg in segs:
        # A bare campus mail code ("3120 TAMU"): digits + an all-caps code.
        if re.match(r"^\d{3,4}\s+[A-Z]{2,5}$", seg):
            if res.mail_code is None:
                res.mail_code = seg
            continue
        # A numbered building ("5045 Emerging Technologies Building"): the
        # leading number is a building number, not a street house number (there
        # is no street-type word). Restricted to Building/Bldg so a campus
        # street like "104 Rhines Hall" stays a street address.
        num_bldg = re.match(r"^\d+\s+(.+\b(?:Building|Bldg))\.?$", seg, re.IGNORECASE)
        if num_bldg and not _STREET_TYPE_WORD_RE.search(seg):
            b = smart_title_case(num_bldg.group(1)) or num_bldg.group(1)
            if res.building is None:
                res.building = b
            else:
                overflow.append(b)
            continue
        m = _CARE_OF_RE.match(seg)
        if m:
            payload = m.group(1).strip(" ,;-.")
            if _looks_like_department(payload):
                if res.department_addendum is None:
                    res.department_addendum = payload
            elif not (res.care_of_enriched and res.care_of_enriched.strip()):
                res.care_of_enriched = payload or None
            continue
        building = _named_building_value(seg)
        if building:
            building = smart_title_case(building) or building
            if res.building is None:
                res.building = building
            else:
                overflow.append(building)  # 2nd building → next free street slot
            continue
        if _segment_is_location_only(seg, city, state, zip_code):
            continue
        if _is_campus_fragment(seg):
            campuses.append(seg)
            continue
        # A department segment ("Chemistry Dept.") → a Name slot (via the
        # department addendum), not the street.
        if _looks_like_department(seg):
            if res.department_addendum is None:
                res.department_addendum = seg.strip(" .")
            else:
                overflow.append(seg)
            continue
        # The main street line: a house-numbered street OR a bare street name
        # ("North Star Avenue") — its street-type word marks it as the street.
        if _looks_like_street(seg) or _STREET_TYPE_WORD_RE.search(seg):
            addresses.append(seg)
            continue
        overflow.append(seg)

    if len(addresses) >= 2:
        res.issue("G3-ADDR-013")  # a second, distinct street address
    return addresses + campuses + overflow


async def process_address(
    *,
    record_id: str,
    name1: str | None,
    name2: str | None,
    name3: str | None,
    street: str | None,
    street_2: str | None,
    street_3: str | None,
    name4: str | None = None,
    name5: str | None = None,
    street_4: str | None = None,
    street_5: str | None = None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
    country: str | None,
    po_box: str | None,
    care_of_enriched: str | None,
    llm_client: OpenAIClient | None,
) -> AddressResult:
    """Run cleaning, extraction, routing, cross-checks, LLM residual,
    and normalisation. See module docstring for the full sequence.

    Parameters mirror the post-name-enrichment record state so the
    orchestrator can pass the already-preprocessed name/street values
    in directly.
    """
    res = AddressResult()
    if care_of_enriched and care_of_enriched.strip():
        res.care_of_enriched = care_of_enriched.strip()

    # Step 1 — clean every input we care about, then scrub non-address
    # junk (URLs, phones, emails, stray person references) so the cleaned
    # street output is never polluted, even when the orchestrator fell back
    # to the raw original street value.
    slots: dict[str, str | None] = {
        "s1": _scrub_street(_clean(street)),
        "s2": _scrub_street(_clean(street_2)),
        "s3": _scrub_street(_clean(street_3)),
        "s4": _scrub_street(_clean(street_4)),
        "s5": _scrub_street(_clean(street_5)),
    }

    # Step 1b — pull a street address that a tier wrote into a name field.
    # Preprocessing (UC 9) already routes addresses embedded in name fields
    # to street slots, but a later tier (e.g. the Tier 3 LLM) can write a
    # campus-style address such as "104 Rhines Hall" into Name 2 AFTER
    # preprocessing ran. Re-run the same extraction on the final name values
    # so the address lands in an empty street slot instead of the name
    # output. Name 1 is the institution and is left untouched.
    _slot_order = ("s1", "s2", "s3", "s4", "s5")
    _dept_values: dict[str, str | None] = {
        "name2": name2, "name3": name3, "name4": name4, "name5": name5,
    }
    for _name_field in DEPT_SLOTS:
        _name_val = _dept_values.get(_name_field)
        if not (_name_val and _name_val.strip()):
            continue
        _addrs, _cleaned = _extract_addresses(_name_val)
        if not _addrs:
            continue
        _placed_all = True
        for _addr in _addrs:
            _target = next((k for k in _slot_order if not slots[k]), None)
            if _target is None:
                _placed_all = False
                break
            slots[_target] = _scrub_street(_clean(_addr)) or _addr
        # Only rewrite the name field when every fragment found a home, so a
        # full-slots record never silently drops part of an address.
        if _placed_all:
            res.name_overrides[_name_field] = _cleaned or None
            _dept_values[_name_field] = _cleaned or None

    # Step 2a — full address jammed into street1 → split.
    if slots["s1"]:
        split_street, city_inf, state_inf, zip_inf = _split_full_address(slots["s1"])
        if city_inf or state_inf or zip_inf:
            slots["s1"] = split_street
            if city_inf and not (city and city.strip()):
                res.city_inferred = city_inf
            if state_inf and not (state and state.strip()):
                res.state_inferred = state_inf
            if zip_inf and not (zip_code and zip_code.strip()):
                res.zip_inferred = zip_inf

    po_box_present = bool(po_box and po_box.strip())

    # Step 2a.5 — per-segment reduction of a mixed primary street (c/o line,
    # named building, or pipe). Routes buildings / c-o / campus / location-only
    # segments per the scope table and rebuilds the street slots from the
    # ordered street lines it returns (main street → Street 1, a second street →
    # Street 2, campus/overflow buildings → later slots). Existing populated
    # secondary slots are appended after. Simple values return None and fall
    # through to the per-value extractors below unchanged.
    reduced = _reduce_primary_street(
        res, slots["s1"], city=city, state=state, zip_code=zip_code,
    )
    if reduced is not None:
        tail = [slots[k] for k in ("s2", "s3", "s4", "s5") if slots[k]]
        lines = reduced + tail
        keys = ("s1", "s2", "s3", "s4", "s5")
        for i, k in enumerate(keys):
            slots[k] = lines[i] if i < len(lines) else None
        if len(lines) > len(keys):
            res.issue("G3-ADDR-011")  # street slots full — content left over

    # Step 2b — extractors over each street slot.
    for slot_name in ("s1", "s2", "s3", "s4", "s5"):
        value = slots[slot_name]
        if not value:
            continue

        # A whole slot that is a named building ("Chemistry Bldg", "Aster
        # House") → Building field (the first one; a second building is left in
        # its street slot per the scope table).
        nb = _named_building_value(value)
        if nb and res.building is None:
            res.building = smart_title_case(nb) or nb
            slots[slot_name] = None
            continue

        work = value

        # PO Box. Spec: if a po_box is already populated AND we find
        # ANOTHER one in a street field → conflict. Otherwise populate.
        work, pob = _extract_po_box(work)
        if pob:
            if res.po_box_extracted or po_box_present:
                res.issue("G3-ADDR-005")
            else:
                res.po_box_extracted = pob
                po_box_present = True

        # c/o + ATTN. A payload that names a department/division goes to
        # the next empty name slot (handled by merge_into_result), not
        # to care_of_enriched.
        work, co = _extract_care_of(work)
        if co:
            if _looks_like_department(co):
                if res.department_addendum is None:
                    res.department_addendum = co.strip()
            elif res.care_of_enriched and res.care_of_enriched.strip():
                if co.strip().lower() != res.care_of_enriched.strip().lower():
                    res.care_of_enriched = (
                        f"{res.care_of_enriched.strip()} | {co.strip()}"
                    )
            else:
                res.care_of_enriched = co.strip()

        # Logistics / unloading point.
        work, logistics = _extract_logistics(work)
        if logistics and not res.unloading_point:
            res.unloading_point = logistics.strip()

        # Mail codes. Bare-token form only allowed in the secondary
        # street slots (street_2 … street_5), never in street_1.
        allow_bare = slot_name != "s1"
        work, code = _extract_mail_code(work, allow_bare=allow_bare)
        if code and not res.mail_code:
            res.mail_code = code

        # Sub-locations.
        work, found, bare_marker = _extract_sublocations(work)
        for target, val in found.items():
            if getattr(res, target) is None:
                setattr(res, target, val)
        if bare_marker:
            res.issue("G4-ADDR-008")

        # Persist the trimmed remainder back to the slot.
        slots[slot_name] = _strip_residue(work) or None

    # Step 3 — cross-field checks (flag only).
    _cross_field_checks(
        res,
        street_cleaned=slots["s1"],
        street_2_cleaned=slots["s2"],
        name1=name1,
        dept_names=[_dept_values[slot] for slot in DEPT_SLOTS],
        po_box_present=po_box_present,
    )

    # Step 4 — LLM residual classification on the secondary slots
    # (street_2 … street_5).
    secondary = await _apply_residual_llm(
        res,
        {k: slots[k] for k in ("s2", "s3", "s4", "s5")},
        name1=name1,
        street=slots["s1"],
        city=city,
        country=country,
        llm_client=llm_client,
    )
    slots.update(secondary)

    # Step 4b — split a trailing access/location qualifier off a street value
    # into the next empty street slot ("300 Tech Park Dr NEAR LOADING DOCK B"
    # → "300 Tech Park Dr" + "NEAR LOADING DOCK B"). Runs after extraction so
    # the qualifier stays a street line rather than being pulled into
    # unloading_point, and only when an empty slot is available.
    _slot_keys = ("s1", "s2", "s3", "s4", "s5")
    for slot_name in [k for k in _slot_keys if slots[k]]:
        split = _split_location_qualifier(slots[slot_name])
        if not split:
            continue
        street_part, qualifier = split
        target = next((k for k in _slot_keys if not slots[k]), None)
        if target is None:
            continue  # no free slot — leave the value combined rather than lose it
        slots[slot_name] = street_part
        slots[target] = qualifier

    # Step 5 — normalise abbreviations on the cleaned street fields.
    res.street_cleaned = _normalise_street_value(slots["s1"])
    res.street_2_cleaned = _normalise_street_value(slots["s2"])
    res.street_3_cleaned = _normalise_street_value(slots["s3"])
    res.street_4_cleaned = _normalise_street_value(slots["s4"])
    res.street_5_cleaned = _normalise_street_value(slots["s5"])

    # Step 6 — dedupe identical street lines across slots. SAP exports often
    # copy the same address line into Street 1-5; keep the first occurrence
    # and blank later slots that merely repeat it so the output does not show
    # the same street more than once. G3-ADDR-012 records that a duplicate was
    # dropped.
    _seen: set[str] = set()
    for _slot in ("street_cleaned", "street_2_cleaned", "street_3_cleaned",
                  "street_4_cleaned", "street_5_cleaned"):
        _val = getattr(res, _slot)
        if not _val:
            continue
        _key = re.sub(r"\s+", " ", _val.strip()).lower()
        if _key in _seen:
            setattr(res, _slot, None)
            res.issue("G3-ADDR-012")  # duplicate street value across slots
        else:
            _seen.add(_key)

    # Step 7 — left-pack the street fields so there are no gaps (an empty
    # Street 1 with a populated Street 2 moves up; a slot blanked by extraction
    # or dedupe is closed). Relative order is preserved.
    _street_slots = ("street_cleaned", "street_2_cleaned", "street_3_cleaned",
                     "street_4_cleaned", "street_5_cleaned")
    _packed = [getattr(res, s) for s in _street_slots if getattr(res, s)]
    _packed += [None] * (len(_street_slots) - len(_packed))
    for _slot, _val in zip(_street_slots, _packed):
        setattr(res, _slot, _val)

    logger.info({
        "record_id": record_id,
        "step": "address_stage1",
        "issues": res.address_issues,
        "po_box": bool(res.po_box_extracted),
        "suite": bool(res.suite),
        "mail_code": bool(res.mail_code),
        "care_of": bool(res.care_of_enriched),
        "unloading_point": bool(res.unloading_point),
    })
    return res


# ---------------------------------------------------------------------------
# Result merge helper — called by the orchestrator
# ---------------------------------------------------------------------------

def merge_into_result(
    result_dict: dict[str, Any],
    addr: AddressResult,
) -> None:
    """Copy AddressResult into the EnrichmentResult dict in-place.

    ``care_of_enriched`` is appended-into rather than overwritten: the
    name-enrichment stage may already have populated it, in which case
    the address stage's contribution is merged with ``" | "``.
    """
    for field_name in (
        "street_cleaned", "street_2_cleaned", "street_3_cleaned",
        "street_4_cleaned", "street_5_cleaned",
        "suite", "building", "floor", "room", "unit", "mail_stop",
        "po_box_extracted", "unloading_point", "mail_code",
        "unclear_address_info",
    ):
        val = getattr(addr, field_name)
        if val is not None:
            result_dict[field_name] = val

    # ``addr.care_of_enriched`` was seeded from result_dict at the start
    # of process_address and any new c/o token found in a street field
    # has already been appended with " | " inside the address pipeline.
    # Assign directly; the orchestrator hands us the authoritative value.
    if addr.care_of_enriched is not None:
        result_dict["care_of_enriched"] = addr.care_of_enriched

    # Apply name-field rewrites: a street address that was sitting in a name
    # field (e.g. "104 Rhines Hall" in Name 2) has been moved into a street
    # slot above, leaving the cleaned remainder (or None) here. Mark a now-
    # empty slot as cleared so finalise() does not restore the original.
    for name_field, new_val in addr.name_overrides.items():
        result_dict[f"{name_field}_enriched"] = new_val
        if not (new_val and str(new_val).strip()):
            cleared = result_dict.setdefault("_preprocess_cleared", set())
            cleared.add(name_field)

    # Place a detected department into the first empty name slot, walking
    # the block downward from name2. A slot is "empty" only when both the
    # enriched and the original value are blank. If every department slot is
    # filled, the address_issues flag is the only record of the finding.
    if addr.department_addendum:
        for target in DEPT_SLOTS:
            enr = result_dict.get(f"{target}_enriched")
            orig = result_dict.get(f"{target}_original")
            slot_empty = (
                not (enr and str(enr).strip())
                and not (orig and str(orig).strip())
            )
            if slot_empty:
                result_dict[f"{target}_enriched"] = addr.department_addendum
                break

    if addr.address_issues:
        existing_issues = result_dict.get("address_issues") or []
        merged = list(existing_issues)
        for code in addr.address_issues:
            if code not in merged:
                merged.append(code)
        result_dict["address_issues"] = merged
