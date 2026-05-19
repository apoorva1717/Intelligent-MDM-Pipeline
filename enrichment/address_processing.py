"""Address Stage 1 — clean, extract, route, cross-check, classify, normalise.

Runs after name enrichment inside ``POST /enrich``. The single entry point
is :func:`process_address`, which takes the post-name-enrichment record
state plus an LLM client and returns an :class:`AddressResult`. No
network or external API call is issued beyond an optional LLM residual
classification on ambiguous values in street_2/street_3.

Pipeline order per record:

    1. Clean    — trim/collapse whitespace, strip stray punctuation
    2. Extract  — regex pulls PO Box, sub-locations, c/o, logistics,
                  mail codes from street/street_2/street_3; splits a
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
    address_issues: list[str] = field(default_factory=list)

    def issue(self, code: str) -> None:
        if code not in self.address_issues:
            self.address_issues.append(code)


# ---------------------------------------------------------------------------
# Step 1 — Cleaning
# ---------------------------------------------------------------------------

_STRAY_DOTS_RE = re.compile(r"\.{2,}")
_MULTI_SPACE_RE = re.compile(r"\s+")


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
    v = v.strip()
    return v or None


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
    (re.compile(r"\b(?:Mail\s+Stop|MS)\s+(\w[\w\-]*)\b", re.IGNORECASE), "mail_stop"),
    (re.compile(r"\b(?:Suite|Ste\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "suite"),
    (re.compile(r"\b(?:Bldg\.?|Building)\s+(\w[\w\-]*)\b", re.IGNORECASE), "building"),
    (re.compile(r"\b(?:Floor|Fl\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "floor"),
    (re.compile(r"\b(\d+)F\b"), "floor"),
    (re.compile(r"\b(?:Room|Rm\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "room"),
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
    bare = bool(_BARE_MARKER_RE.search(work))
    return work, found, bare


_CARE_OF_RE = re.compile(
    r"(?:c\s*/\s*o|Attn(?:ention)?|ATT)\s*[:\-]?\s*(.+)",
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
    value = m.group(1).strip(" ,;-")
    new = text[: m.start()].rstrip(" ,;-")
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
_NAME_STREET_LIKE_RE = re.compile(
    r"\b\d+\s+\w+\s+(?:St|Ave|Blvd|Rd|Dr|Ln|Hwy|Pkwy)\b\.?",
    re.IGNORECASE,
)


def _looks_like_street(value: str | None) -> bool:
    if not value:
        return False
    return bool(
        _HOUSE_NUMBER_RE.search(value)
        and _STREET_TYPE_WORD_RE.search(value)
    )


def _cross_field_checks(
    res: AddressResult,
    street_cleaned: str | None,
    street_2_cleaned: str | None,
    name1: str | None,
    name2: str | None,
    name3: str | None,
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
    for nm in (name1, name2, name3):
        if nm and _NAME_STREET_LIKE_RE.search(nm):
            res.issue("G1-CROSS-001")
            break

    # Street has org keyword without any street-type word.
    if (
        street_cleaned
        and _ORG_KEYWORD_RE.search(street_cleaned)
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
    street_2: str | None,
    street_3: str | None,
    name1: str | None,
    street: str | None,
    city: str | None,
    country: str | None,
    llm_client: OpenAIClient | None,
) -> tuple[str | None, str | None]:
    """Run residual classification on whatever remains in street_2 /
    street_3 after extraction. Returns the possibly-mutated
    ``(street_2, street_3)`` strings."""
    if llm_client is None:
        return street_2, street_3

    for slot_name, current in (("street_2", street_2), ("street_3", street_3)):
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
            if slot_name == "street_2":
                street_2 = None
            else:
                street_3 = None
        elif cls == "PERSON_NAME":
            res.issue("G1-CROSS-003")
        elif cls == "ORG_NAME":
            res.issue("G1-CROSS-002")
        elif cls == "MAIL_CODE":
            if not res.mail_code:
                res.mail_code = current.strip()
            if slot_name == "street_2":
                street_2 = None
            else:
                street_3 = None
        elif cls == "LOGISTICS":
            if not res.unloading_point:
                res.unloading_point = current.strip()
            if slot_name == "street_2":
                street_2 = None
            else:
                street_3 = None
        elif cls == "STREET_ADDRESS":
            pass
        elif cls == "UNCLEAR":
            res.issue("G1-ADDR-009")
            if not res.unclear_address_info:
                res.unclear_address_info = current.strip()
        else:
            res.issue("G1-ADDR-009")

    return street_2, street_3


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

async def process_address(
    *,
    record_id: str,
    name1: str | None,
    name2: str | None,
    name3: str | None,
    street: str | None,
    street_2: str | None,
    street_3: str | None,
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

    # Step 1 — clean every input we care about.
    s1 = _clean(street)
    s2 = _clean(street_2)
    s3 = _clean(street_3)

    # Step 2a — full address jammed into street1 → split.
    if s1:
        split_street, city_inf, state_inf, zip_inf = _split_full_address(s1)
        if city_inf or state_inf or zip_inf:
            s1 = split_street
            if city_inf and not (city and city.strip()):
                res.city_inferred = city_inf
            if state_inf and not (state and state.strip()):
                res.state_inferred = state_inf
            if zip_inf and not (zip_code and zip_code.strip()):
                res.zip_inferred = zip_inf

    po_box_present = bool(po_box and po_box.strip())

    # Step 2b — extractors over each street slot.
    for slot_name, value in (("s1", s1), ("s2", s2), ("s3", s3)):
        if not value:
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

        # Mail codes. Bare-token form only allowed in street_2 / street_3.
        allow_bare = slot_name in ("s2", "s3")
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
        cleaned_remainder = _strip_residue(work) or None
        if slot_name == "s1":
            s1 = cleaned_remainder
        elif slot_name == "s2":
            s2 = cleaned_remainder
        else:
            s3 = cleaned_remainder

    # Step 3 — cross-field checks (flag only).
    _cross_field_checks(
        res,
        street_cleaned=s1,
        street_2_cleaned=s2,
        name1=name1,
        name2=name2,
        name3=name3,
        po_box_present=po_box_present,
    )

    # Step 4 — LLM residual classification on street_2 / street_3.
    s2, s3 = await _apply_residual_llm(
        res,
        street_2=s2,
        street_3=s3,
        name1=name1,
        street=s1,
        city=city,
        country=country,
        llm_client=llm_client,
    )

    # Step 5 — normalise abbreviations on the cleaned street fields.
    res.street_cleaned = _normalise_street_value(s1)
    res.street_2_cleaned = _normalise_street_value(s2)
    res.street_3_cleaned = _normalise_street_value(s3)

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

    # Place a detected department into the first empty name slot (name2,
    # then name3). A slot is "empty" only when both the enriched and the
    # original value are blank. If both name2 and name3 are filled, the
    # address_issues flag is the only record of the finding.
    if addr.department_addendum:
        for target in ("name2", "name3"):
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
