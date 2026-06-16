"""Deterministic data-quality issue detection (Issue Catalogue v2).

This module audits a single :class:`~api.models.EnrichmentRecord` against the
36-code Issue Catalogue and returns the codes that fire. It is the engine behind
the ``POST /issues`` endpoint.

Design constraints (per product owner):

* **Pure and deterministic** — regex / string checks only. No enrichment, no LLM
  call, no network/external I/O. The same rule set can therefore be run on a raw
  input file *and* on a post-pipeline output file, and the count delta is the
  story the catalogue is built around.
* **Reuse, don't reinvent** — wherever the enrichment pipeline already ships a
  deterministic detector (PO-box / sub-location / opaque-code / DBA / ISO-country
  …) we import and reuse its compiled patterns so detection stays consistent with
  what the pipeline actually does.

Coverage: 34 of the 36 catalogue codes are emitted. Two are intentionally never
emitted because they genuinely require the pipeline's LLM residual classifier and
cannot be decided deterministically from raw input — they are listed in
``ISSUE_CATALOGUE`` for completeness but documented as ``# LLM-only`` below:

* ``G1-ADDR-009`` Unclassified Residual in Address
* ``G4-ADDR-025`` Sub-location Overflow Beyond Street 5

Several G1-NAME / G2-NAME / G5 rules are inherently semantic; here they are
detected with conservative deterministic heuristics (documented inline). They err
toward precision (few false positives) over recall.
"""

from __future__ import annotations

import re

from api.models import EnrichmentRecord

# Reused deterministic detectors from the enrichment pipeline.
from enrichment.preprocess import (
    _CO_ATTN_PREFIX_RE,
    _EMAIL_RE,
    _PHONE_RE,
    _URL_RE,
    _extract_addresses,
    _has_legal_suffix,
    _is_opaque_code,
    _normalise_dba,
    _street_person_name,
    has_multiple_contacts,
)
from enrichment.address_processing import (
    _BARE_MARKER_RE,
    _PO_BOX_RE,
    _STREET_TYPE_WORD_RE,
    _SUITE_PATTERNS,
    _UNIVERSITY_CENTRE_RE,
    _extract_mail_code,
    _looks_like_department,
    _looks_like_street,
)
from utils.text_utils import (
    country_to_iso_code,
    is_blank,
    is_granular_unit,
    is_specific_unit_construction,
    is_unit_construction,
    looks_like_university_or_research_institute,
)


# ---------------------------------------------------------------------------
# Catalogue — code -> human name, in catalogue (G1→G5) order.
# Output codes are ordered by this mapping's key order.
# ---------------------------------------------------------------------------

ISSUE_CATALOGUE: dict[str, str] = {
    # G1 — Data in Wrong Field
    "G1-CROSS-001": "Address Content in Name Field",
    "G1-CROSS-002": "Org Name in Address Field",
    "G1-CROSS-003": "Contact Information in Wrong Field",
    "G1-ADDR-001": "House Number Embedded in Street",
    "G1-ADDR-003": "Sub-location Embedded in Street",
    "G1-ADDR-004": "PO Box Embedded in Street",
    "G1-ADDR-006": "Mail Code in Street Field",
    "G1-ADDR-011": "Department Label in Street Field",
    "G1-NAME-001": "Name Overflow Across Fields",
    "G1-NAME-004": "Name 2 Empty With Name 3 Populated",
    "G1-NAME-013": "SAP Internal Code in Name Field",
    "G1-ADDR-009": "Unclassified Residual in Address",  # LLM-only — never emitted
    # G2 — Missing Required Data
    "G2-VAL-001": "Name 1 Missing",
    "G2-VAL-002": "Postal Code Missing",
    "G2-VAL-003": "Tax Jurisdiction Missing",
    "G2-VAL-004": "Region Missing",
    "G2-VAL-006": "Language Missing",
    "G2-VAL-007": "Search Term 1 Missing",
    "G2-VAL-008": "Country Missing",
    "G2-NAME-009": "Lab Without Department",
    "G2-NAME-012": "Research Institution Missing Department",
    "G2-CONTACT-008": "No Contact and No Department",
    "G2-CONTACT-009": "Department Missing And Enrichable from Contact",
    # G3 — Duplicate or Conflicting Data
    "G3-NAME-003": "DBA Pattern in Name Field",
    "G3-NAME-005": "Duplicate Name Across Fields",
    "G3-ADDR-005": "Multiple PO Boxes on Record",
    "G3-ADDR-012": "Duplicate Street Across Fields",
    "G3-ADDR-013": "Two Distinct Street Addresses on Record",
    "G3-ADDR-014": "PO Box and Street Both Present",
    "G3-CONTACT-007": "Multiple Contacts on Record",
    # G4 — Invalid Format or Length
    "G4-NAME-015": "Name Overflow Beyond Name 4",
    "G4-ADDR-008": "Bare Sub-location Marker Without Value",
    "G4-ADDR-025": "Sub-location Overflow Beyond Street 5",  # LLM-only — never emitted
    "G4-ADDR-026": "Postal Code Format Invalid",
    "G4-ADDR-027": "Country Code Not ISO 2-letter",
    # G5 — Non-Standard Naming
    "G5-NAME-001": "Organisation Name Not in Official Form",
    "G5-NAME-002": "Unit Name Not in Official Form",
}

# SAP name-field length limit (Name 1–4 combined).
_SAP_NAME_LIMIT = 140

# Required-field rules (G2-VAL family): EnrichmentRecord field -> issue code.
# These are gated on column presence (see ``detect_issues``): a "missing"
# rule fires only when the column exists in the file but is blank. When the
# column is absent from the file entirely, the rule is skipped — otherwise an
# enriched export that simply doesn't carry a column (e.g. Postal Code) would
# be reported as "missing" it.
_REQUIRED_FIELD_CODES: list[tuple[str, str]] = [
    ("name_1", "G2-VAL-001"),
    ("postal_code", "G2-VAL-002"),
    ("tax_jurisdiction", "G2-VAL-003"),
    ("region", "G2-VAL-004"),
    ("language_key", "G2-VAL-006"),
    ("search_term_1", "G2-VAL-007"),
    ("country_region_key", "G2-VAL-008"),
]

# Continuation connectors that suggest Name 2 carries on from Name 1 as one
# org name (heuristic for G1-NAME-001).
_NAME_CONTINUATION_RE = re.compile(
    r"^\s*(?:and|&|of|for|the|de|der|und|et)\b|^\s*[a-z]",
)

# Common abbreviation tokens marking a non-canonical org / unit name
# (heuristic for G5). Anchored on word boundaries to avoid matching inside
# longer words ("Univ" not inside "University").
_ABBREV_TOKEN_RE = re.compile(
    r"\b(?:Univ|Dept|Dep|Div|Inst|Natl|Nat'l|Intl|Int'l|Assoc|Assn|Ctr|"
    r"Lab|Labs|Tech|Sch|Mgmt|Engrg|Eng|Sci|Med|Svcs|Svc|Co)\b\.?",
    re.IGNORECASE,
)

# Company/organisation words that, when sitting in a Street field with no
# street-type word, signal an org name in the address (heuristic for
# G1-CROSS-002, broader than the pipeline's narrow _ORG_KEYWORD_RE).
_ORG_IN_STREET_RE = re.compile(
    r"\b(?:University|Universit[äa]t|Institute|Institut|College|Faculty|"
    r"School|Hospital|Clinic|Corp(?:oration)?|Inc|Incorporated|LLC|Ltd|"
    r"Limited|Company|GmbH|Technolog(?:y|ies)|Systems|Solutions|"
    r"Laborator(?:y|ies)|Labs|Industries|Sciences|Instruments|"
    r"Pharmaceuticals?|Pharma)\b",
    re.IGNORECASE,
)

# Postal-code format checks keyed by ISO country (best-effort, common cases).
_POSTAL_FORMATS: dict[str, re.Pattern[str]] = {
    "US": re.compile(r"^\d{5}(?:-\d{4})?$"),
    "CA": re.compile(r"^[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d$"),
}


def _names(record: EnrichmentRecord) -> list[str | None]:
    return [record.name_1, record.name_2, record.name_3, record.name_4]


def _streets(record: EnrichmentRecord) -> list[str | None]:
    return [
        record.street_1,
        record.street_2,
        record.street_3,
        record.street_4,
        record.street_5,
    ]


def _norm(value: str | None) -> str:
    """Case/whitespace-folded value for equality comparison."""
    return re.sub(r"\s+", " ", value.strip().lower()) if value else ""


def _street_signature(
    value: str | None, house_number: str | None = None,
) -> tuple[frozenset[str], tuple[str, ...]] | None:
    """An order- and case-independent (numbers, words) signature for a street
    line, used to spot the same address repeated across slots.

    The dedicated House Number is folded in when the line carries no inline
    number, so "Innovation Blvd" + House Number "500" produces the same
    signature as a sibling slot "500 Innovation Blvd". Returns None for a
    blank line.
    """
    if not value or not value.strip():
        return None
    tokens = re.findall(r"[a-z]+|\d+", value.lower())
    if not tokens:
        return None
    nums = {t for t in tokens if t.isdigit()}
    words = tuple(sorted(t for t in tokens if not t.isdigit()))
    if not nums and house_number and house_number.strip():
        hn_nums = re.findall(r"\d+", house_number)
        if hn_nums:
            nums = set(hn_nums)
    return (frozenset(nums), words)


# ---------------------------------------------------------------------------
# G1 — Data in Wrong Field
# ---------------------------------------------------------------------------

def _detect_wrong_field(record: EnrichmentRecord, found: set[str]) -> None:
    names = _names(record)
    streets = _streets(record)

    # G1-CROSS-001 — address content (street / sub-location / PO box) in a Name.
    for nm in names:
        if nm and _extract_addresses(nm)[0]:
            found.add("G1-CROSS-001")
            break

    # G1-CROSS-002 — org / company name sitting in a Street field with no
    # street-type word to anchor it as a real address. "University Centre" (and
    # acronyms of centre) is a building name, not an org — strip it first.
    for st in streets:
        if not st:
            continue
        without_centre = _UNIVERSITY_CENTRE_RE.sub(" ", st)
        if (
            _ORG_IN_STREET_RE.search(without_centre)
            and not _STREET_TYPE_WORD_RE.search(st)
        ):
            found.add("G1-CROSS-002")
            break

    # G1-CROSS-003 — contact info (email / phone / URL / c-o-ATTN / person) in a
    # Name or Street field.
    for field in names + streets:
        if not field:
            continue
        if (
            _EMAIL_RE.search(field)
            or _PHONE_RE.search(field)
            or _URL_RE.search(field)
            or _CO_ATTN_PREFIX_RE.search(field)
        ):
            found.add("G1-CROSS-003")
            break
    else:
        for st in streets:
            if st and _street_person_name(st):
                found.add("G1-CROSS-003")
                break

    # G1-ADDR-001 — house number embedded in Street while the dedicated House
    # Number field is empty.
    if is_blank(record.house_number):
        for st in streets:
            if _looks_like_street(st):
                found.add("G1-ADDR-001")
                break

    # G1-ADDR-003 — sub-location (Suite / Floor / Bldg / Room …) inside Street.
    for st in streets:
        if st and any(pat.search(st) for pat, _ in _SUITE_PATTERNS):
            found.add("G1-ADDR-003")
            break

    # G1-ADDR-004 — PO Box pattern inside a Street field.
    for st in streets:
        if st and _PO_BOX_RE.search(st):
            found.add("G1-ADDR-004")
            break

    # G1-ADDR-006 — mail/drop code inside a Street field.
    for st in streets:
        if st and _extract_mail_code(st, allow_bare=True)[1]:
            found.add("G1-ADDR-006")
            break

    # G1-ADDR-011 — department label inside a Street field.
    for st in streets:
        if _looks_like_department(st):
            found.add("G1-ADDR-011")
            break

    # G1-NAME-001 — Name 1 and Name 2 read as one continuous org name.
    # Heuristic: Name 2 opens with a connector / lowercase word AND Name 1 has
    # no legal suffix that would close the entity. (True rule is LLM-only.)
    if (
        not is_blank(record.name_1)
        and not is_blank(record.name_2)
        and not _has_legal_suffix(record.name_1 or "")
        and _NAME_CONTINUATION_RE.search(record.name_2 or "")
    ):
        found.add("G1-NAME-001")

    # G1-NAME-004 — Name 2 blank while Name 3 is populated.
    if is_blank(record.name_2) and not is_blank(record.name_3):
        found.add("G1-NAME-004")

    # G1-NAME-013 — a Name field whose entire value is an internal/opaque code.
    for nm in names:
        if nm and _is_opaque_code(nm):
            found.add("G1-NAME-013")
            break

    # G1-ADDR-009 — unclassified residual in address: LLM-only, never emitted.


# ---------------------------------------------------------------------------
# G2 — Missing Required Data
# ---------------------------------------------------------------------------

def _detect_missing(
    record: EnrichmentRecord,
    found: set[str],
    present_fields: set[str] | None,
) -> None:
    # Required-field checks — gated on the column being present in the file.
    for field_name, code in _REQUIRED_FIELD_CODES:
        if present_fields is not None and field_name not in present_fields:
            continue
        if is_blank(getattr(record, field_name)):
            found.add(code)

    name2_blank = is_blank(record.name_2)

    # G2-NAME-012 — university / research institute (Name 1) with no department
    # in Name 2. Gated on the narrower university-or-research signal so clinical
    # orgs (hospitals, clinics, medical centres) — which routinely have no
    # department — are not flagged.
    if looks_like_university_or_research_institute(record.name_1) and name2_blank:
        found.add("G2-NAME-012")

    # G2-NAME-009 — a granular research group (Name 2) with no parent department
    # anywhere in the name block.
    if is_granular_unit(record.name_2) and not any(
        is_specific_unit_construction(x) or is_unit_construction(x)
        for x in (record.name_3, record.name_4)
    ):
        found.add("G2-NAME-009")

    # G2-CONTACT-008 / -009 — department missing; routed by whether a single
    # contact is available to enrich from. Only meaningful for universities and
    # research institutes, where a department is expected: a company with no
    # Name 2 is normal, not an issue, and clinical orgs routinely carry none.
    # Gate on the same university-or-research signal as G2-NAME-012 so these
    # codes are not raised across the board.
    #
    # G2-CONTACT-008 ("No Contact and No Department") shares G2-NAME-012's gate
    # exactly, so it would always double-count the missing department. Suppress
    # it whenever G2-NAME-012 already covers the record; only G2-CONTACT-009
    # adds new information (the department is enrichable from a single contact).
    if name2_blank and looks_like_university_or_research_institute(record.name_1):
        if is_blank(record.contact) and is_blank(record.care_of):
            if "G2-NAME-012" not in found:
                found.add("G2-CONTACT-008")
        elif not is_blank(record.contact) and not has_multiple_contacts(record.contact):
            found.add("G2-CONTACT-009")


# ---------------------------------------------------------------------------
# G3 — Duplicate or Conflicting Data
# ---------------------------------------------------------------------------

def _detect_duplicate(record: EnrichmentRecord, found: set[str]) -> None:
    names = _names(record)
    streets = _streets(record)

    # G3-NAME-003 — DBA pattern present in a Name field.
    for nm in names:
        if nm and _normalise_dba(nm)[1]:
            found.add("G3-NAME-003")
            break

    # G3-NAME-005 — same value in two adjacent name fields.
    if (_norm(record.name_1) and _norm(record.name_1) == _norm(record.name_2)) or (
        _norm(record.name_2) and _norm(record.name_2) == _norm(record.name_3)
    ):
        found.add("G3-NAME-005")

    # PO boxes across street slots + the dedicated PO Box field.
    po_box_count = sum(
        1 for st in streets if st and _PO_BOX_RE.search(st)
    )
    if not is_blank(record.po_box):
        po_box_count += 1

    # G3-ADDR-005 — more than one PO Box on the record.
    if po_box_count >= 2:
        found.add("G3-ADDR-005")

    # G3-ADDR-012 — the same street address appears in more than one slot.
    # Catches SAP's house-number split: Street 1 holds the street name with the
    # number in the dedicated House Number field, while another Street slot
    # repeats the combined "<number> <name>" — e.g. Street 1 "Innovation Blvd"
    # + House Number "500" duplicates Street 2 "500 Innovation Blvd". Plain
    # exact duplicates across slots are caught too.
    street_sigs = [
        sig
        for idx, st in enumerate(streets)
        # House Number conventionally pairs with Street 1 only.
        if (sig := _street_signature(st, record.house_number if idx == 0 else None))
        is not None
    ]
    if len(street_sigs) != len(set(street_sigs)):
        found.add("G3-ADDR-012")

    # G3-ADDR-013 — two distinct real street addresses across street slots.
    real_streets = [
        _norm(st) for st in streets if _looks_like_street(st)
    ]
    if len(set(real_streets)) >= 2:
        found.add("G3-ADDR-013")

    # G3-ADDR-014 — a PO Box and a real street both present on the record.
    if po_box_count >= 1 and any(_looks_like_street(st) for st in streets):
        found.add("G3-ADDR-014")

    # G3-CONTACT-007 — more than one contact in the Contact field.
    if has_multiple_contacts(record.contact):
        found.add("G3-CONTACT-007")


# ---------------------------------------------------------------------------
# G4 — Invalid Format or Length
# ---------------------------------------------------------------------------

def _detect_format(record: EnrichmentRecord, found: set[str]) -> None:
    # G4-NAME-015 — combined Name 1–4 exceeds the SAP 140-char limit.
    combined = sum(len(nm) for nm in _names(record) if nm)
    if combined > _SAP_NAME_LIMIT:
        found.add("G4-NAME-015")

    # G4-ADDR-008 — a bare sub-location marker ("Ste", "Floor") with no value.
    for st in _streets(record):
        if st and _BARE_MARKER_RE.search(st):
            found.add("G4-ADDR-008")
            break

    # G4-ADDR-026 — postal code present but does not match its country format.
    if not is_blank(record.postal_code):
        iso = country_to_iso_code(record.country_region_key)
        fmt = _POSTAL_FORMATS.get(iso) if iso else None
        if fmt and not fmt.match((record.postal_code or "").strip()):
            found.add("G4-ADDR-026")

    # G4-ADDR-027 — country present but not in canonical ISO 2-letter form.
    if not is_blank(record.country_region_key):
        raw = (record.country_region_key or "").strip()
        iso = country_to_iso_code(raw)
        if iso is None or raw.upper() != iso:
            found.add("G4-ADDR-027")

    # G4-ADDR-025 — sub-location overflow beyond Street 5: LLM-only, never emitted.


# ---------------------------------------------------------------------------
# G5 — Non-Standard Naming
# ---------------------------------------------------------------------------

def _detect_naming(record: EnrichmentRecord, found: set[str]) -> None:
    # G5-NAME-001 — organisation name (Name 1) abbreviated / non-canonical.
    if record.name_1 and _ABBREV_TOKEN_RE.search(record.name_1):
        found.add("G5-NAME-001")

    # G5-NAME-002 — a unit-level name (Name 2–4) abbreviated / non-canonical.
    for nm in (record.name_2, record.name_3, record.name_4):
        if nm and _ABBREV_TOKEN_RE.search(nm):
            found.add("G5-NAME-002")
            break


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_issues(
    record: EnrichmentRecord,
    present_fields: set[str] | None = None,
) -> list[str]:
    """Return every Issue-Catalogue code that fires for *record*.

    Pure and deterministic — no enrichment, LLM, or network access. Codes are
    returned in catalogue order (``ISSUE_CATALOGUE`` key order).

    *present_fields* is the set of ``EnrichmentRecord`` field names whose
    columns actually exist in the source file. When given, the required-field
    rules (``G2-VAL-*``) only fire for columns that are present-but-blank;
    columns absent from the file are skipped rather than reported as missing.
    When ``None`` (the default) every field is assumed present — i.e. the
    record is audited in isolation.
    """
    found: set[str] = set()
    _detect_wrong_field(record, found)
    _detect_missing(record, found, present_fields)
    _detect_duplicate(record, found)
    _detect_format(record, found)
    _detect_naming(record, found)
    return [code for code in ISSUE_CATALOGUE if code in found]
