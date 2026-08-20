"""Deterministic data-quality issue detection (Issue Catalogue v2).

This module audits a single :class:`~api.models.EnrichmentRecord` against the
Issue Catalogue and returns the codes that fire. It is the engine behind the
``POST /issues`` and ``POST /issues/compare`` endpoints.

Design constraints (per product owner):

* **Pure and deterministic** — regex / string checks only. No enrichment, no LLM
  call, no network/external I/O. The same rule set can therefore be run on a raw
  input file *and* on a post-pipeline output file, and the count delta is the
  story the catalogue is built around.
* **Reuse, don't reinvent** — wherever the enrichment pipeline already ships a
  deterministic detector (PO-box / sub-location / opaque-code / DBA / ISO-country
  …) we import and reuse its compiled patterns so detection stays consistent with
  what the pipeline actually does.

Catalogue shape
---------------
``ISSUE_CATALOGUE`` maps each declared code to an :class:`IssueDefinition`
carrying ``group``, ``name``, ``field``, ``mandatory``, ``origin``, ``status``
and ``reason``. Two consequences worth stating outright:

* **The group is an attribute, not a prefix.** Catalogue v2's G6 ("Not
  Resolvable by Enrichment") is a *regrouping* of four codes that keep their
  original ``G2-`` identifiers, so ``code.split("-")[0]`` is no longer a group.
  Read ``ISSUE_CATALOGUE[code].group`` (or ``issue_group(code)``).
* **``mandatory`` is the DATAshaper severity.** ``True`` blocks the SAP load
  (*Error*); ``False`` is a *Warning*. See ``IssueDefinition.severity`` and
  README's integration table.

Counts — all derived from the source below, never asserted
----------------------------------------------------------
* **38 declared** catalogue entries.
* **34 live** — emitted by this detector. 33 of them are quality issues
  (G1-G6) and one is ``G7-VERIFY-001``.
* **1 unlisted** — ``G3-ADDR-012``, emitted here but absent from Catalogue v2,
  left unchanged pending a human decision.
* **35 deterministically emitted** = the 34 live plus the unlisted one; this is
  ``EMITTED_CODES``.
* **2 withdrawn** — ``G2-CONTACT-008``, ``G2-CONTACT-009``. Struck through in
  Catalogue v2; declared here for the audit trail, never emitted.
* **1 not deterministically detectable** — ``G1-ADDR-009``. Live in Catalogue v2
  but no deterministic rule can express it; see the entry's ``reason``.

Origin breakdown of the 33 live quality codes: 11 DS-only, 20 API-only, 2 BOTH
(Catalogue v2's 21 API figure includes ``G1-ADDR-009``, which is ``ndd`` here).
``detect_issues`` emits every origin by default — including DS-only codes — for
the reason documented on that function; pass ``origins=("API", "BOTH")`` for a
DATAshaper-facing feed that must not duplicate a native DS rule.

These figures are asserted against the source by
``tests/test_issue_detection.py::test_docstring_counts_match_the_catalogue``, so
adding or retiring a code fails the suite until this docstring is updated. How
many of the 35 actually fire on any given batch is a property of that data, not
of the rule set.

Several G1-NAME / G2-NAME / G5 rules are inherently semantic; here they are
detected with conservative deterministic heuristics (documented inline). They err
toward precision (few false positives) over recall.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

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
    _extract_sublocations,
    _looks_like_department,
    _looks_like_street,
)
from utils.name_slots import ADJACENT_RECORD_NAME_PAIRS, RECORD_NAME_FIELDS
from utils.text_utils import (
    country_to_iso_code,
    is_blank,
    is_granular_unit,
    is_specific_unit_construction,
    is_unit_construction,
    looks_like_university_or_research_institute,
)


# ---------------------------------------------------------------------------
# Catalogue — one explicit entry per declared code, in catalogue order.
#
# Every attribute Catalogue v2 carries is modelled here rather than inferred:
#
# ``group``      G1..G7. **Independent of the code prefix.** v2's G6 ("Not
#                Resolvable by Enrichment") is a regrouping of four codes that
#                keep their original ``G2-`` identifiers, so parsing the prefix
#                to get the group is a bug — read ``.group``.
# ``mandatory``  True  = blocks the SAP load (DATAshaper severity *Error*)
#                False = warning. Mirrors README's integration table.
# ``origin``     "DS"   native DATAshaper rule
#                "API"  raised by this Enrichment API
#                "BOTH" either path can raise it
#                A DS-origin rule raised here produces a *duplicate* issue in
#                DATAshaper, which sees it from both paths — see ``detect_issues``
#                and its ``origins`` filter.
# ``status``     "live"       emitted by this detector
#                "withdrawn"  struck through in Catalogue v2; declaration kept
#                             for the audit trail, never emitted
#                "ndd"        live in the catalogue but *not deterministically
#                             detectable*; see ``reason``
#                "unlisted"   emitted here but absent from Catalogue v2, pending
#                             a human decision; see ``reason``
# ``reason``     Why a non-"live" code is in that state. Required for every
#                status except "live" (asserted by the test suite).
# ---------------------------------------------------------------------------

Origin = Literal["DS", "API", "BOTH"]
Status = Literal["live", "withdrawn", "ndd", "unlisted"]


@dataclass(frozen=True)
class IssueDefinition:
    """One declared Issue-Catalogue entry."""

    code: str
    group: str
    name: str
    field: str
    mandatory: bool
    origin: Origin
    status: Status = "live"
    reason: str = ""

    @property
    def severity(self) -> str:
        """DATAshaper issue severity, derived from ``mandatory``."""
        return "Error" if self.mandatory else "Warning"


def _d(code, group, name, field, mandatory, origin, status="live", reason="") -> tuple[str, IssueDefinition]:
    return code, IssueDefinition(
        code, group, name, field, mandatory, origin, status, reason,
    )


ISSUE_CATALOGUE: dict[str, IssueDefinition] = dict([
    # -- G1 — Data in Wrong Field ------------------------------------------
    _d("G1-CROSS-001", "G1", "Address Content in Name Field", "Name 1", False, "API"),
    _d("G1-CROSS-002", "G1", "Org Name in Address Field", "Street", False, "API"),
    _d("G1-CROSS-003", "G1", "Contact Information in Wrong Field", "varies", False, "API"),
    _d("G1-ADDR-001", "G1", "House Number Embedded in Street", "Street", False, "DS"),
    _d("G1-ADDR-003", "G1", "Sub-location Embedded in Street", "Street 2", False, "API"),
    _d("G1-ADDR-004", "G1", "PO Box Embedded in Street", "Street", False, "API"),
    _d("G1-ADDR-006", "G1", "Mail Code in Street Field", "Street 2", False, "API"),
    _d("G1-ADDR-011", "G1", "Department Label in Street Field", "Street 2", False, "API"),
    _d("G1-NAME-001", "G1", "Name Overflow Across Fields", "Name 1", False, "API"),
    # v2 renamed this from "Name 2 Empty With Name 3 Populated"; the rename is
    # a scope change — any blank slot *between* two populated ones fires it,
    # not just the Name 2 / Name 3 pair.
    _d("G1-NAME-004", "G1", "Empty field in between populated name fields", "Name 2", False, "API"),
    _d("G1-NAME-013", "G1", "SAP Internal Code in Name Field", "Name 2", False, "API"),
    _d(
        "G1-ADDR-009", "G1", "Unclassified Residual in Address", "Street 2", False, "API",
        status="ndd",
        reason=(
            "\"Unclassifiable\" is defined as the complement of every classifier the "
            "pipeline runs, so no positive pattern can express it. Any deterministic "
            "proxy (\"street text matching no known pattern\") fires on ordinary "
            "unremarkable address lines and is a false-positive generator. The real "
            "rule needs the LLM residual classifier, which /issues may not call."
        ),
    ),
    # -- G2 — Missing Required Data ----------------------------------------
    _d("G2-VAL-002", "G2", "Postal Code Missing", "Postal Code", True, "DS"),
    _d("G2-VAL-004", "G2", "Region Missing", "Region", True, "DS"),
    _d("G2-VAL-007", "G2", "Search Term 1 Missing", "Search Term 1", True, "DS"),
    _d("G2-VAL-008", "G2", "Country Missing", "Country", True, "DS"),
    _d("G2-NAME-009", "G2", "Lab Without Department", "Name 2", False, "API"),
    _d(
        "G2-CONTACT-008", "G2", "No Contact and No Department", "Name 2", False, "API",
        status="withdrawn",
        reason=(
            "Struck through in Catalogue v2. Its gate was identical to G2-NAME-012's, "
            "so it could never carry information the latter had not already reported."
        ),
    ),
    _d(
        "G2-CONTACT-009", "G2", "Department Missing And Enrichable from Contact",
        "Name 2", False, "API",
        status="withdrawn",
        reason=(
            "Struck through in Catalogue v2. Withdrawing it removed the contact-based "
            "(Tier 2A) department recovery path, which is why G2-NAME-012 now sits in "
            "G6 — no automated route to a department remains."
        ),
    ),
    # -- G3 — Duplicate or Conflicting Data --------------------------------
    _d("G3-NAME-003", "G3", "DBA Pattern in Name Field", "Name 1", False, "BOTH"),
    _d("G3-NAME-005", "G3", "Duplicate Name Across Fields", "Name 2", False, "API"),
    _d("G3-ADDR-005", "G3", "Multiple PO Boxes on Record", "PO Box", False, "API"),
    _d(
        "G3-ADDR-012", "G3", "Duplicate Street Across Fields", "Street", False, "API",
        status="unlisted",
        reason=(
            "Implemented and emitting here, but absent from the Catalogue v2 G3 table. "
            "Either it was withdrawn and this detector should stop emitting it, or v2 "
            "omits it and Notion needs the row added. Left emitting, unchanged, "
            "pending that decision — see docs/thesis/00_OPEN_ITEMS.md."
        ),
    ),
    _d("G3-ADDR-013", "G3", "Two Distinct Street Addresses on Record", "Street", False, "API"),
    _d("G3-ADDR-014", "G3", "PO Box and Street Both Present", "PO Box", False, "BOTH"),
    _d("G3-CONTACT-007", "G3", "Multiple Contacts on Record", "Name 2", False, "API"),
    # -- G4 — Invalid Format or Length -------------------------------------
    # v2 names this "Name Overflow Beyond Name 4". The name block is five slots
    # wide as of the five-name-slot change, so the slot-agnostic wording is kept
    # here and the divergence is reported for a Notion correction.
    _d("G4-NAME-015", "G4", "Name Overflow Beyond the Name Block", "Name 4", True, "API"),
    _d("G4-ADDR-008", "G4", "Bare Sub-location Marker Without Value", "Street 2", False, "API"),
    _d("G4-ADDR-025", "G4", "Sub-location Overflow Beyond Street 5", "Street 5", False, "API"),
    _d("G4-ADDR-026", "G4", "Postal Code Format Invalid", "Postal Code", False, "DS"),
    _d("G4-ADDR-027", "G4", "Country Code Not ISO 2-letter", "Country", True, "DS"),
    # -- G5 — Non-Standard Naming ------------------------------------------
    _d("G5-NAME-001", "G5", "Organisation Name Not in Official Form", "Name 1", False, "API"),
    _d("G5-NAME-002", "G5", "Unit Name Not in Official Form", "Name 2-4", False, "API"),
    # -- G6 — Not Resolvable by Enrichment ---------------------------------
    # A regrouping, not new codes: these four keep their original G2-
    # identifiers. Expected to persist from raw to enriched — that persistence
    # is correct behaviour, not a pipeline failure.
    _d("G2-VAL-001", "G6", "Name 1 Missing", "Name 1", True, "DS"),
    _d("G2-VAL-003", "G6", "Tax Jurisdiction Missing", "Tax Jurisdiction", True, "DS"),
    _d("G2-VAL-006", "G6", "Language Missing", "Language", True, "DS"),
    _d("G2-NAME-012", "G6", "Research Institution Missing Department", "Name 2", False, "DS"),
    # -- G7 — Verification Required ----------------------------------------
    # Not a quality issue: raised *by* successful enrichment so DATAshaper can
    # route the record to a steward through the Category dropdown. Reported
    # separately and never counted in the before/after reduction metric.
    _d("G7-VERIFY-001", "G7", "Enriched Record Requires Verification", "Flag for Review", False, "API"),
])

# Codes this detector can actually raise.
EMITTED_CODES: tuple[str, ...] = tuple(
    code for code, d in ISSUE_CATALOGUE.items() if d.status in ("live", "unlisted")
)

# Quality-issue groups. G7 is deliberately absent: it is not a quality issue.
QUALITY_GROUPS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6")

# The groups the before/after reduction percentage is computed over. G6 is
# excluded because its codes have no automated remediation path and are
# *expected* to persist; G7 because counting it would inflate the post-pipeline
# total in proportion to how well enrichment performed.
REDUCIBLE_GROUPS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5")

# Codes whose persistence across the comparison is correct behaviour.
PERSISTENT_GROUP = "G6"
VERIFICATION_GROUP = "G7"


def issue_name(code: str) -> str:
    """Human name for *code* (empty string for an unknown code)."""
    entry = ISSUE_CATALOGUE.get(code)
    return entry.name if entry else ""


def issue_group(code: str) -> str:
    """Catalogue v2 group for *code*.

    Read this rather than slicing the prefix: G6 holds four ``G2-`` codes.
    """
    entry = ISSUE_CATALOGUE.get(code)
    return entry.group if entry else code.split("-", 1)[0]


# SAP name-field length limit (the whole name block combined).
_SAP_NAME_LIMIT = 140

# Street 2..5 — the slots a sub-location can be packed into once Street 1
# holds the street proper. Anything beyond this overflows (G4-ADDR-025).
_SUBLOCATION_SLOTS = 4

# Required-field rules (G2-VAL family): EnrichmentRecord field -> issue code.
# These are gated on column presence (see ``detect_issues``): a "missing"
# rule fires only when the column exists in the file but is blank. When the
# column is absent from the file entirely, the rule is skipped — otherwise an
# enriched export that simply doesn't carry a column (e.g. Postal Code) would
# be reported as "missing" it.
#
# The third element is an optional *predicate* on the record: the rule fires
# only when it returns True. Catalogue v2 attaches conditions to individual
# codes ("G2-VAL-004 Region Missing / only for US"), which a uniform loop
# cannot express — a blank Region on a German record is not a defect, and
# emitting it there is a false positive. G2-VAL-004 is the only v2 entry in
# this table carrying such a condition; the rest are unconditional.
_REQUIRED_FIELD_CODES: list[tuple[str, str, Callable[[EnrichmentRecord], bool] | None]] = [
    ("name_1", "G2-VAL-001", None),
    ("postal_code", "G2-VAL-002", None),
    ("tax_jurisdiction", "G2-VAL-003", None),
    ("region", "G2-VAL-004", lambda r: _is_us(r)),
    ("language_key", "G2-VAL-006", None),
    ("search_term_1", "G2-VAL-007", None),
    ("country_region_key", "G2-VAL-008", None),
]


def _is_us(record: EnrichmentRecord) -> bool:
    """True only when the record's country resolves to ISO ``US``.

    A blank or unrecognised country is *not* treated as US: Region is only
    mandatory for US records, so firing on an unknown country would recreate
    the false positive this predicate exists to remove.
    """
    return country_to_iso_code(record.country_region_key) == "US"

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
    return [getattr(record, f, None) for f in RECORD_NAME_FIELDS]


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

    # G1-NAME-001 — two adjacent Name fields read as one continuous org
    # name. Heuristic: the lower field opens with a connector / lowercase
    # word AND the upper has no legal suffix that would close the entity.
    # (True rule is LLM-only.) Checked at every slot boundary: the SAP field
    # split can drop a continuation anywhere in the block, not only after
    # Name 1.
    for upper, lower in ADJACENT_RECORD_NAME_PAIRS:
        upper_val = getattr(record, upper, None)
        lower_val = getattr(record, lower, None)
        if (
            not is_blank(upper_val)
            and not is_blank(lower_val)
            and not _has_legal_suffix(upper_val or "")
            and _NAME_CONTINUATION_RE.search(lower_val or "")
        ):
            found.add("G1-NAME-001")
            break

    # G1-NAME-004 — "Empty field in between populated name fields". A blank
    # slot is only a *gap* when something populated sits both above and below
    # it: Name 1 blank with Name 2 populated is a missing organisation name
    # (G2-VAL-001), not a gap in the block, and reporting it here double-counts
    # it. Scanned across the whole block, so Name 3 blank under a populated
    # Name 2 with Name 4 populated fires exactly as the Name 2 / Name 3 pair
    # does — the v2 rename widened the scope from one specific pair to any gap.
    populated = [not is_blank(nm) for nm in names]
    for idx in range(1, len(names) - 1):
        if (
            not populated[idx]
            and any(populated[:idx])
            and any(populated[idx + 1:])
        ):
            found.add("G1-NAME-004")
            break

    # G1-NAME-013 — a Name field whose entire value is an internal/opaque code.
    for nm in names:
        if nm and _is_opaque_code(nm):
            found.add("G1-NAME-013")
            break

    # G1-ADDR-009 — unclassified residual in address. Declared with
    # ``status="ndd"`` (not deterministically detectable) and never emitted;
    # the reason is on the catalogue entry.


# ---------------------------------------------------------------------------
# G2 — Missing Required Data
# ---------------------------------------------------------------------------

def _detect_missing(
    record: EnrichmentRecord,
    found: set[str],
    present_fields: set[str] | None,
) -> None:
    # Required-field checks — gated on the column being present in the file.
    for field_name, code, condition in _REQUIRED_FIELD_CODES:
        if present_fields is not None and field_name not in present_fields:
            continue
        if condition is not None and not condition(record):
            continue
        if is_blank(getattr(record, field_name)):
            found.add(code)

    # "No department" means the whole block below Name 1 is empty — a
    # department sitting in Name 3 with Name 2 blank is still a department
    # the record carries, so reading Name 2 alone would report it missing.
    dept_values = _names(record)[1:]
    no_department = all(is_blank(v) for v in dept_values)

    # G2-NAME-012 — university / research institute (Name 1) with no
    # department anywhere in the block. Gated on the narrower
    # university-or-research signal so clinical orgs (hospitals, clinics,
    # medical centres) — which routinely have no department — are not flagged.
    if looks_like_university_or_research_institute(record.name_1) and no_department:
        found.add("G2-NAME-012")

    # G2-NAME-009 — a granular research group in any department slot with no
    # parent department anywhere else in the name block.
    for i, value in enumerate(dept_values):
        if not is_granular_unit(value):
            continue
        others = [v for j, v in enumerate(dept_values) if j != i]
        if not any(
            is_specific_unit_construction(x) or is_unit_construction(x)
            for x in others
        ):
            found.add("G2-NAME-009")
            break

    # G2-CONTACT-008 / G2-CONTACT-009 are **withdrawn** in Catalogue v2 and are
    # deliberately not emitted here. Both are still declared in
    # ``ISSUE_CATALOGUE`` with ``status="withdrawn"`` so the audit trail records
    # that they existed and why they went — see their ``reason`` text. The
    # consequence is recorded in the catalogue: withdrawing them removed the
    # contact-based (Tier 2A) department recovery path, which is why
    # G2-NAME-012 now sits in G6 rather than G2.


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

    # G3-NAME-005 — same value in two adjacent name fields, at any slot
    # boundary in the block.
    for upper, lower in ADJACENT_RECORD_NAME_PAIRS:
        upper_norm = _norm(getattr(record, upper, None))
        if upper_norm and upper_norm == _norm(getattr(record, lower, None)):
            found.add("G3-NAME-005")
            break

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
    # G4-NAME-015 — the combined name block exceeds the SAP 140-char limit.
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

    # G4-ADDR-025 — more sub-locations on the record than Street 2..5 can hold.
    # Deterministic approximation of "too many sub-locations to fit Street 2-5":
    # reuse the pipeline's own ``_extract_sublocations`` on every street line
    # and compare the distinct (kind, value) count against the four slots
    # available below Street 1. Going through the pipeline extractor rather
    # than re-walking ``_SUITE_PATTERNS`` matters: it consumes each match as it
    # goes, so overlapping patterns ("Bldg 4 Floor" matching both the building
    # and the value-before-marker floor rule) cannot inflate the count.
    # Counting distinct pairs rather than distinct kinds is what
    # "sub-locations" means here — two different suites need two slots.
    sublocations: set[tuple[str, str]] = set()
    for st in _streets(record):
        if not st:
            continue
        _remaining, extracted, _bare = _extract_sublocations(st)
        for kind, value in extracted.items():
            sublocations.add((kind, value.strip().lower()))
    if len(sublocations) > _SUBLOCATION_SLOTS:
        found.add("G4-ADDR-025")


# ---------------------------------------------------------------------------
# G5 — Non-Standard Naming
# ---------------------------------------------------------------------------

def _detect_naming(record: EnrichmentRecord, found: set[str]) -> None:
    # G5-NAME-001 — organisation name (Name 1) abbreviated / non-canonical.
    if record.name_1 and _ABBREV_TOKEN_RE.search(record.name_1):
        found.add("G5-NAME-001")

    # G5-NAME-002 — a unit-level name (Name 2..N) abbreviated / non-canonical.
    for nm in _names(record)[1:]:
        if nm and _ABBREV_TOKEN_RE.search(nm):
            found.add("G5-NAME-002")
            break


# ---------------------------------------------------------------------------
# G7 — Verification Required (enriched-record path only)
# ---------------------------------------------------------------------------

# Spreadsheet spellings of a true "Flag for Review" cell. Everything else —
# including a blank, "FALSE", "N", "0" — is false.
_TRUTHY = frozenset({"true", "yes", "y", "x", "1"})


def flag_for_review_is_set(value: object) -> bool:
    """Interpret a ``Flag for Review`` cell as a boolean.

    Accepts the real spellings an XLSX round-trip produces: a Python ``bool``
    from a checkbox cell, ``1``/``0`` from a numeric one, and the string forms
    openpyxl hands back for text cells.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in _TRUTHY


def _detect_verification(found: set[str], flag_for_review: bool | None) -> None:
    """G7-VERIFY-001 — the one code derived from enrichment *output*.

    Every other code in the catalogue is derived from record content, so it can
    be computed on a raw input file and on an enriched file alike. This one
    cannot: it fires when the pipeline set ``flag_for_review`` on the record it
    produced, which a raw input record has no way to carry. ``flag_for_review``
    is therefore ``None`` for a raw audit (no such column) and the code can
    never be raised there.
    """
    if flag_for_review:
        found.add("G7-VERIFY-001")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_issues(
    record: EnrichmentRecord,
    present_fields: set[str] | None = None,
    *,
    flag_for_review: bool | None = None,
    origins: Iterable[str] | None = None,
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

    *flag_for_review* carries the enriched record's ``Flag for Review`` value
    and drives ``G7-VERIFY-001``. Leave it ``None`` (the default) when auditing
    raw input: G7 is raised *by* successful enrichment, never by record
    content, so a raw audit must never produce it.

    *origins* optionally restricts the result to codes with those Catalogue v2
    origins (``"DS"``, ``"API"``, ``"BOTH"``). The default emits every origin,
    including the 11 DS-only codes. That is deliberate and is the documented
    reason required by the catalogue's origin rule: ``/issues`` is also run
    standalone over a raw workbook with DATAshaper nowhere in the loop, and the
    before/after reduction narrative is defined over the whole G1-G6 set — of
    which G6 is entirely DS-origin. Passing ``origins=("API", "BOTH")`` yields
    exactly the set a DATAshaper-facing feed should carry, so a DS-origin rule
    is not reported twice once that decision is taken.
    """
    found: set[str] = set()
    _detect_wrong_field(record, found)
    _detect_missing(record, found, present_fields)
    _detect_duplicate(record, found)
    _detect_format(record, found)
    _detect_naming(record, found)
    _detect_verification(found, flag_for_review)

    if origins is not None:
        allowed = set(origins)
        found = {c for c in found if ISSUE_CATALOGUE[c].origin in allowed}
    return [code for code in ISSUE_CATALOGUE if code in found]
