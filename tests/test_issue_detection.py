"""Unit tests for the deterministic issue detector (Issue Catalogue v2).

Examples are drawn from the catalogue itself wherever possible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.issue_detection import (
    EMITTED_CODES,
    ISSUE_CATALOGUE,
    DERIVED_LOW_FLAG_CODE,
    FLAG_CODE_ISSUES,
    QUALITY_GROUPS,
    REDUCIBLE_GROUPS,
    VERIFICATION_GROUPS,
    provenance_is_low,
    split_flag_codes,
    detect_issues,
    issue_group,
    issue_name,
)


def _record(**fields) -> EnrichmentRecord:
    """Build a record that is 'clean' by default, then override fields.

    The required-field rules (G2-VAL-*) fire on blanks, so a baseline record
    populates every mandatory field. Individual tests blank or override what
    they need so a single rule fires in isolation.
    """
    base = {
        "Name 1": "Acme Corporation",
        "Name 2": "Engineering Department",
        "Postal Code": "12345",
        "Tax Jurisdiction": "TX0000000",
        "Region": "FL",
        "Language Key": "EN",
        "Search Term 1": "ACME",
        "Country/Region Key": "US",
    }
    base.update(fields)
    return EnrichmentRecord.model_validate(base)


# ---------------------------------------------------------------------------
# Catalogue integrity
# ---------------------------------------------------------------------------

def test_catalogue_declares_41_entries():
    assert len(ISSUE_CATALOGUE) == 41


def test_status_counts_match_catalogue_v2():
    """33 live, 7 withdrawn, 1 unlisted, and nothing left marked ``ndd``.

    Catalogue v2 declared 34 live. The three added since are the flag-derived
    codes — G6-RESOLVE-001, G7-CONFIRM-001, G8-VERIFY-001 — which report the
    pipeline's own review flags rather than record content; five entries were
    withdrawn on 2026-09-06, which is where the rest of the difference is.
    """
    from collections import Counter

    counts = Counter(entry.status for entry in ISSUE_CATALOGUE.values())
    assert counts == {"live": 33, "withdrawn": 7, "unlisted": 1}


def test_withdrawn_codes_are_declared_but_never_emitted():
    """Withdrawn entries stay declared for the audit trail — retaining them
    records that they existed and why — but nothing may emit them.

    The first two are struck through in Catalogue v2. The other five were
    withdrawn on 2026-09-06: a rule no deterministic detector can express, one
    that fired on nothing in 500 records, two required-field rules over fields
    outside master-data scope, and a routing code now carried by group
    membership.
    """
    for code in (
        "G2-CONTACT-008", "G2-CONTACT-009",
        "G1-ADDR-009", "G4-ADDR-025", "G2-VAL-003", "G2-VAL-006",
        "G7-VERIFY-001",
    ):
        assert ISSUE_CATALOGUE[code].status == "withdrawn"
        assert ISSUE_CATALOGUE[code].reason
        assert code not in EMITTED_CODES


def test_group_is_an_attribute_not_a_prefix():
    """G6 is a regrouping: four codes keep their original G2- identifiers, so
    slicing the prefix gives the wrong group."""
    for code in ("G2-VAL-001", "G2-VAL-003", "G2-VAL-006", "G2-NAME-012"):
        assert issue_group(code) == "G6"
        assert code.split("-")[0] == "G2"


def test_every_entry_has_a_valid_group_origin_and_status():
    for code, entry in ISSUE_CATALOGUE.items():
        assert entry.code == code
        assert entry.group in (*QUALITY_GROUPS, *VERIFICATION_GROUPS)
        assert entry.origin in ("DS", "API", "BOTH")
        assert entry.status in ("live", "withdrawn", "ndd", "unlisted")
        assert entry.name and entry.field
        # Anything not plainly live must say why.
        if entry.status != "live":
            assert entry.reason, f"{code} needs a reason for status={entry.status}"


def test_mandatory_maps_to_datashaper_severity():
    """Mandatory = Yes blocks the SAP load (Error); No is a Warning."""
    assert ISSUE_CATALOGUE["G2-VAL-002"].mandatory is True
    assert ISSUE_CATALOGUE["G2-VAL-002"].severity == "Error"
    assert ISSUE_CATALOGUE["G1-CROSS-001"].mandatory is False
    assert ISSUE_CATALOGUE["G1-CROSS-001"].severity == "Warning"


def test_origin_breakdown_of_live_quality_codes():
    """Catalogue v2 recorded 11 DS-only / 21 API-only / 2 BOTH over its 34 live
    G1-G6 codes. Three of those are now withdrawn — G1-ADDR-009 and
    G4-ADDR-025 (API), G2-VAL-003 and G2-VAL-006 (DS), less G1-ADDR-009 which
    was already ``ndd`` and outside the live count — leaving 9 / 19 / 2 over 30.
    The gap against v2 is the withdrawal, and must stay visible.

    The census is over the codes derived from record CONTENT, which is what v2
    counted. G6-RESOLVE-001 is a live quality code too, and is excluded here
    because including it would blur that comparison.
    """
    from collections import Counter

    flag_derived = set(FLAG_CODE_ISSUES.values())
    live_quality = [
        e for e in ISSUE_CATALOGUE.values()
        if e.status == "live"
        and e.group in QUALITY_GROUPS
        and e.code not in flag_derived
    ]
    assert len(live_quality) == 30
    assert Counter(e.origin for e in live_quality) == {"DS": 9, "API": 19, "BOTH": 2}


def test_reduction_groups_exclude_g6_g7_and_g8():
    assert "G6" not in REDUCIBLE_GROUPS
    assert "G7" not in REDUCIBLE_GROUPS
    assert "G8" not in REDUCIBLE_GROUPS
    assert set(REDUCIBLE_GROUPS) == {"G1", "G2", "G3", "G4", "G5"}
    assert set(VERIFICATION_GROUPS) == {"G7", "G8"}


def test_docstring_counts_match_the_catalogue():
    """The module docstring quotes catalogue figures; keep them honest.

    Counts are derived from the source here — the catalogue length, the
    per-status tallies, and the codes with a real emission site (a
    ``found.add("...")`` literal, or the ``_REQUIRED_FIELD_CODES`` table) — so
    a code added or retired without a docstring update fails here rather than
    silently making the docs wrong.
    """
    import ast
    from collections import Counter
    from pathlib import Path

    import enrichment.issue_detection as module
    from enrichment.issue_detection import _REQUIRED_FIELD_CODES

    source = Path(module.__file__).read_text()

    # Two emission sites are tables rather than `found.add("...")` literals:
    # the required-field rules, and the flag-code -> issue-code mapping.
    sited = {code for _field, code, _cond in _REQUIRED_FIELD_CODES}
    sited |= set(FLAG_CODE_ISSUES.values())
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("add", "update")
        ):
            for arg in node.args:
                for const in ast.walk(arg):
                    if (
                        isinstance(const, ast.Constant)
                        and isinstance(const.value, str)
                        and const.value in ISSUE_CATALOGUE
                    ):
                        sited.add(const.value)

    # Every code with an emission site is exactly the emittable set, and
    # nothing withdrawn or ndd has one.
    assert sited == set(EMITTED_CODES)

    status = Counter(e.status for e in ISSUE_CATALOGUE.values())
    doc = module.__doc__ or ""

    assert f"**{len(ISSUE_CATALOGUE)} declared**" in doc
    assert f"**{status['live']} live**" in doc
    assert f"**{status['unlisted']} unlisted**" in doc
    assert f"**{len(sited)} deterministically emitted**" in doc
    assert f"**{status['withdrawn']} withdrawn**" in doc
    assert f"**{status['ndd']} not deterministically detectable**" in doc


def test_clean_record_has_no_issues():
    assert detect_issues(_record()) == []


def test_output_is_ordered_by_catalogue():
    order = list(ISSUE_CATALOGUE)
    rec = _record(**{"Name 1": "", "Postal Code": ""})  # G2-VAL-001 + G2-VAL-002
    codes = detect_issues(rec)
    assert codes == sorted(codes, key=order.index)


# ---------------------------------------------------------------------------
# G1 — Data in Wrong Field
# ---------------------------------------------------------------------------

def test_g1_cross_001_address_in_name():
    assert "G1-CROSS-001" in detect_issues(_record(**{"Name 2": "10901 Roosevelt Blvd N"}))


def test_g1_cross_002_org_in_street():
    assert "G1-CROSS-002" in detect_issues(_record(**{"Street 1": "AGILENT TECHNOLOGIES"}))


def test_g1_cross_002_university_centre_not_flagged():
    # "University Centre" (and acronyms of centre) is a building name, not an
    # org name misplaced in the address field.
    for value in ("University Centre", "University Center", "University Ctr",
                  "University Ctre", "University Cntr", "UNIVERSITY CENT"):
        assert "G1-CROSS-002" not in detect_issues(_record(**{"Street 1": value})), value


def test_g1_cross_003_email_in_name():
    assert "G1-CROSS-003" in detect_issues(_record(**{"Name 2": "AP@plasmatherm.com"}))


def test_g1_addr_001_house_number_in_street():
    rec = _record(**{"Street 1": "10901 Roosevelt Blvd N"})  # no House Number field
    assert "G1-ADDR-001" in detect_issues(rec)


def test_g1_addr_003_sublocation_in_street():
    assert "G1-ADDR-003" in detect_issues(_record(**{"Street 1": "Main St Ste 390"}))


def test_g1_addr_004_po_box_in_street():
    assert "G1-ADDR-004" in detect_issues(_record(**{"Street 1": "PO BOX 115350"}))


def test_g1_addr_006_mail_code_in_street():
    assert "G1-ADDR-006" in detect_issues(_record(**{"Street 2": "MAIL CODE: SVC1039"}))


def test_g1_addr_011_department_label_in_street():
    assert "G1-ADDR-011" in detect_issues(_record(**{"Street 2": "Receiving Department"}))


def test_g1_name_001_name_overflow_across_fields():
    rec = _record(**{
        "Name 1": "Orlando Health Emergency Room",
        "Name 2": "and Medical Pavilion - Osceola",
    })
    assert "G1-NAME-001" in detect_issues(rec)


def test_g1_name_004_name2_empty_name3_populated():
    rec = _record(**{"Name 2": "", "Name 3": "Quality Control Dept"})
    assert "G1-NAME-004" in detect_issues(rec)


@pytest.mark.parametrize("fields", [
    # v2 renamed the rule to "Empty field in between populated name fields",
    # widening it from the one Name 2 / Name 3 pair to any gap in the block.
    {"Name 2": "", "Name 3": "Quality Control Dept"},
    {"Name 2": "Engineering", "Name 3": "", "Name 4": "Room 4"},
    {"Name 2": "", "Name 3": "", "Name 4": "Room 4"},
    {"Name 2": "Engineering", "Name 3": "", "Name 4": "", "Name 5": "Annex"},
])
def test_g1_name_004_fires_for_a_gap_at_any_slot(fields):
    assert "G1-NAME-004" in detect_issues(_record(**fields))


@pytest.mark.parametrize("fields", [
    # Trailing blanks are not a gap — nothing populated sits below them.
    {"Name 2": "Engineering", "Name 3": "", "Name 4": ""},
    {"Name 2": "", "Name 3": "", "Name 4": ""},
    # A blank Name 1 is a missing organisation name (G2-VAL-001), not a gap
    # in the block: nothing populated sits above it. Reporting it here would
    # double-count the same defect under two codes.
    {"Name 1": "", "Name 2": "Engineering Department"},
])
def test_g1_name_004_not_raised_without_a_populated_field_above_and_below(fields):
    assert "G1-NAME-004" not in detect_issues(_record(**fields))


def test_blank_name_1_is_reported_only_as_the_g6_missing_name_code():
    issues = detect_issues(_record(**{"Name 1": "", "Name 2": "Engineering"}))
    assert "G2-VAL-001" in issues
    assert "G1-NAME-004" not in issues


def test_g1_name_013_sap_code_in_name():
    assert "G1-NAME-013" in detect_issues(_record(**{"Name 2": "B800000345"}))


def test_g1_addr_009_is_withdrawn():
    """Live in Catalogue v2 and marked ``ndd`` here for as long as the entry
    claimed a rule: "unclassifiable" is the complement of every classifier, so
    no deterministic rule expresses it and none was ever written. A code that
    can never fire is not a rule, so it is withdrawn rather than carried as a
    permanent gap."""
    rec = _record(**{"Street 2": "Loading Dock - East Side"})
    assert "G1-ADDR-009" not in detect_issues(rec)
    assert ISSUE_CATALOGUE["G1-ADDR-009"].status == "withdrawn"
    assert ISSUE_CATALOGUE["G1-ADDR-009"].reason


# ---------------------------------------------------------------------------
# G2 — Missing Required Data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,code", [
    ("Name 1", "G2-VAL-001"),
    ("Postal Code", "G2-VAL-002"),
    ("Region", "G2-VAL-004"),          # baseline record is US
    ("Search Term 1", "G2-VAL-007"),
    ("Country/Region Key", "G2-VAL-008"),
])
def test_g2_missing_required_fields(field, code):
    assert code in detect_issues(_record(**{field: ""}))


@pytest.mark.parametrize(
    "country", ["US", "USA", "United States", "DE", "Germany", "GB", "FR"],
)
def test_g2_val_004_fires_for_a_blank_region_whatever_the_country(country):
    """Region Missing is a present-and-blank check, with no country condition.

    It previously carried ``lambda r: _is_us(r)``, justified by a "Catalogue v2
    gates this on US records only" claim that appears in no catalogue extract,
    Notion row or README table — while 03_ALGORITHMS.md documents the rule as
    plain "``region`` blank (column-gated)". Because every blank-Region record
    in the corpus is German, the predicate meant a mandatory DS-origin code
    could not fire on any file anybody ran, and a permanently dark rule reads
    exactly like a rule with nothing to report."""
    rec = _record(**{"Region": "", "Country/Region Key": country})
    assert "G2-VAL-004" in detect_issues(rec), country


def test_g2_val_004_fires_when_country_is_blank_or_unrecognised():
    """The rule does not consult the country at all, so an unknown one cannot
    suppress it either."""
    for country in ("", "Freedonia"):
        rec = _record(**{"Region": "", "Country/Region Key": country})
        assert "G2-VAL-004" in detect_issues(rec), country


def test_no_required_field_rule_carries_a_condition():
    """The per-code predicate is an extension point with no current user. A
    condition added here silently narrows a mandatory rule to nothing on some
    files, so it needs a catalogue source before it needs code."""
    from enrichment.issue_detection import _REQUIRED_FIELD_CODES

    conditional = [c for _f, c, cond in _REQUIRED_FIELD_CODES if cond is not None]
    assert conditional == []


def test_g2_name_012_research_institution_missing_department():
    rec = _record(**{"Name 1": "Florida State University", "Name 2": ""})
    assert "G2-NAME-012" in detect_issues(rec)


def test_g2_name_009_lab_without_department():
    rec = _record(**{"Name 1": "University of Florida", "Name 2": "Smith Lab"})
    assert "G2-NAME-009" in detect_issues(rec)


def test_missing_department_without_contact_raises_only_name_012():
    # A research institution with no department raises G2-NAME-012, and only
    # that: both G2-CONTACT-* codes are withdrawn in Catalogue v2, which is
    # exactly why G2-NAME-012 now sits in G6 — withdrawing them removed the
    # contact-based recovery path, so no automated route to a department
    # remains.
    rec = _record(**{
        "Name 1": "Florida State University", "Name 2": "", "Contact": "",
    })
    issues = detect_issues(rec)
    assert "G2-NAME-012" in issues
    assert "G2-CONTACT-009" not in issues


def test_g2_contact_009_withdrawn_even_when_its_old_gate_is_satisfied():
    """The exact record that used to raise it: research org, no department,
    exactly one contact. Withdrawn in Catalogue v2, so only G2-NAME-012 —
    now a G6 code — reports the missing department."""
    rec = _record(**{
        "Name 1": "Florida State University", "Name 2": "",
        "Contact": "Dr. Emily Carter",
    })
    issues = detect_issues(rec)
    assert "G2-CONTACT-009" not in issues
    assert "G2-CONTACT-008" not in issues
    assert "G2-NAME-012" in issues
    assert issue_group("G2-NAME-012") == "G6"


def test_missing_department_codes_not_raised_for_non_research_company():
    # A company with no department is normal — these codes must not fire.
    rec = _record(**{"Name 1": "Acme Corporation", "Name 2": "", "Contact": ""})
    issues = detect_issues(rec)
    assert "G2-NAME-012" not in issues
    assert "G2-CONTACT-009" not in issues


@pytest.mark.parametrize("name1", [
    "St. Mary's Hospital",
    "Downtown Clinic",
    "MD Anderson Cancer Center",
    "Regional Health System",
])
def test_missing_department_codes_not_raised_for_clinical_orgs(name1):
    # Clinical orgs routinely carry no department — the missing-department
    # codes must only fire for universities / research institutes.
    rec = _record(**{"Name 1": name1, "Name 2": "", "Contact": ""})
    issues = detect_issues(rec)
    assert "G2-NAME-012" not in issues
    assert "G2-CONTACT-009" not in issues


# ---------------------------------------------------------------------------
# G3 — Duplicate or Conflicting Data
# ---------------------------------------------------------------------------

def test_g3_name_003_dba_pattern():
    rec = _record(**{"Name 1": "Coastal Holdings Inc", "Name 2": "d/b/a Coastal Marine"})
    assert "G3-NAME-003" in detect_issues(rec)


def test_g3_name_005_duplicate_name_across_fields():
    rec = _record(**{"Name 1": "Tropical Pharma Inc", "Name 2": "Tropical Pharma Inc"})
    assert "G3-NAME-005" in detect_issues(rec)


def test_g3_addr_005_multiple_po_boxes():
    rec = _record(**{"Street 1": "PO BOX 4500", "PO Box": "4500", "Street 2": "PO Box 6789"})
    assert "G3-ADDR-005" in detect_issues(rec)


def test_g3_addr_012_duplicate_street_house_number_split():
    # Street 1 holds the street name with the number in House Number; Street 2
    # repeats the combined form — same address, must be flagged as a duplicate.
    rec = _record(**{
        "Street 1": "INNOVATION Blvd",
        "House Number": "500",
        "Street 2": "500 Innovation Blvd",
    })
    issues = detect_issues(rec)
    assert "G3-ADDR-012" in issues
    # Same address, so it must NOT also be reported as two distinct addresses.
    assert "G3-ADDR-013" not in issues


def test_g3_addr_012_exact_duplicate_street():
    rec = _record(**{
        "Street 1": "500 Innovation Blvd",
        "Street 2": "500 Innovation Blvd",
    })
    assert "G3-ADDR-012" in detect_issues(rec)


def test_g3_addr_012_not_raised_for_distinct_streets():
    rec = _record(**{"Street 1": "500 Main St", "Street 2": "250 Main St"})
    assert "G3-ADDR-012" not in detect_issues(rec)


def test_g3_addr_013_two_distinct_streets():
    rec = _record(**{"Street 1": "123 Main St", "Street 2": "250 Central Ave"})
    assert "G3-ADDR-013" in detect_issues(rec)


def test_g3_addr_014_po_box_and_street_both_present():
    rec = _record(**{"PO Box": "98765", "Street 1": "100 S Bayshore Blvd"})
    assert "G3-ADDR-014" in detect_issues(rec)


def test_g3_contact_007_multiple_contacts():
    rec = _record(**{"Contact": "Dr. Jane Smith; Prof. Bob Lee"})
    assert "G3-CONTACT-007" in detect_issues(rec)


# ---------------------------------------------------------------------------
# G4 — Invalid Format or Length
# ---------------------------------------------------------------------------

def test_g4_name_015_name_overflow_beyond_140():
    rec = _record(**{
        "Name 1": "A" * 60,
        "Name 2": "B" * 60,
        "Name 3": "C" * 60,
    })
    assert "G4-NAME-015" in detect_issues(rec)


def test_g4_addr_008_bare_sublocation_marker():
    assert "G4-ADDR-008" in detect_issues(_record(**{"Street 2": "Ste"}))


def test_g4_addr_026_postal_format_invalid():
    rec = _record(**{"Postal Code": "ABC123", "Country/Region Key": "US"})
    assert "G4-ADDR-026" in detect_issues(rec)


def test_g4_addr_027_country_not_iso2():
    assert "G4-ADDR-027" in detect_issues(_record(**{"Country/Region Key": "USA"}))


def test_g4_addr_027_iso2_country_is_clean():
    assert "G4-ADDR-027" not in detect_issues(_record(**{"Country/Region Key": "US"}))


def test_g4_addr_025_is_withdrawn_and_never_fires():
    """The record that used to raise it — five distinct sub-locations against
    the four slots Street 2..5 offers — now raises nothing.

    The rule was withdrawn on the evidence: no record in 500 carried more than
    four, and the spill a name block genuinely overruns is reported by the
    `overflow` flag instead."""
    rec = _record(**{
        "Street 2": "Bldg 4 Floor 3 Suite 9 Room 5",
        "Street 3": "Mail Stop 12",
    })
    assert "G4-ADDR-025" not in detect_issues(rec)


# ---------------------------------------------------------------------------
# G5 — Non-Standard Naming
# ---------------------------------------------------------------------------

def test_g5_name_001_org_not_official():
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": "Univ of Florida"}))


def test_g5_name_002_unit_not_official():
    rec = _record(**{"Name 1": "University of Florida", "Name 3": "Dept of Chem"})
    assert "G5-NAME-002" in detect_issues(rec)


# ---------------------------------------------------------------------------
# Multiple issues on one record
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Column-aware required-field detection
# ---------------------------------------------------------------------------

def test_missing_field_fires_when_column_present_but_blank():
    rec = _record(**{"Postal Code": ""})
    assert "G2-VAL-002" in detect_issues(rec, present_fields={"postal_code"})


def test_missing_field_skipped_when_column_absent():
    # The enriched export doesn't carry Postal Code at all → not "missing".
    rec = _record(**{"Postal Code": ""})
    present = {"name_1", "name_2"}  # postal_code intentionally absent
    assert "G2-VAL-002" not in detect_issues(rec, present_fields=present)


def test_present_fields_none_assumes_all_present():
    # Backwards-compatible default: blank field is flagged when no column
    # context is supplied.
    rec = _record(**{"Region": ""})
    assert "G2-VAL-004" in detect_issues(rec)


def test_multiple_issues_all_reported():
    rec = _record(**{
        "Name 1": "Univ of Florida",         # G5-NAME-001
        "Postal Code": "",                    # G2-VAL-002
        "Street 1": "PO BOX 115350",          # G1-ADDR-004
        "Country/Region Key": "USA",          # G4-ADDR-027
    })
    codes = detect_issues(rec)
    for expected in ("G1-ADDR-004", "G2-VAL-002", "G4-ADDR-027", "G5-NAME-001"):
        assert expected in codes


# ---------------------------------------------------------------------------
# G7 — Verification Required (enriched-record path only)
# ---------------------------------------------------------------------------

def test_g7_verify_is_withdrawn_and_a_flagged_record_no_longer_raises_it():
    """``flag_for_review`` is still accepted — the API passes it — but no
    detector reads it. Routing a record to a steward is carried by group
    membership now, so the one code the boolean drove is withdrawn."""
    assert "G7-VERIFY-001" not in detect_issues(_record(), flag_for_review=True)
    assert ISSUE_CATALOGUE["G7-VERIFY-001"].status == "withdrawn"


def test_g7_absent_from_a_raw_input_audit():
    """G7 was derived from enrichment *output*, not record content. A raw input
    record has no Flag for Review column, so ``flag_for_review`` is None; since
    the withdrawal the code cannot be raised on any input at all."""
    dirty = _record(**{
        "Name 1": "", "Name 2": "10901 Roosevelt Blvd N", "Postal Code": "",
        "Street 1": "PO BOX 115350", "Contact": "Dr. Jane Smith; Prof. Bob Lee",
    })
    assert "G7-VERIFY-001" not in detect_issues(dirty)
    assert "G7-VERIFY-001" not in detect_issues(dirty, flag_for_review=None)


def test_g7_not_raised_when_the_enriched_record_is_not_flagged():
    assert "G7-VERIFY-001" not in detect_issues(_record(), flag_for_review=False)


@pytest.mark.parametrize("cell,expected", [
    (True, True), (False, False), (None, False),
    ("TRUE", True), ("true", True), ("Yes", True), ("Y", True), ("X", True),
    ("1", True), (1, True),
    ("FALSE", False), ("No", False), ("", False), ("0", False), (0, False),
])
def test_flag_for_review_cell_spellings(cell, expected):
    from enrichment.issue_detection import flag_for_review_is_set

    assert flag_for_review_is_set(cell) is expected


def test_g7_is_not_a_quality_group():
    """It must never be swept into a quality-issue total by group iteration."""
    assert issue_group("G7-VERIFY-001") == "G7"
    assert "G7" not in QUALITY_GROUPS
    assert "G7" not in REDUCIBLE_GROUPS


# ---------------------------------------------------------------------------
# G6-RESOLVE-001 / G7-CONFIRM-001 / G8-VERIFY-001 — the flag-derived codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag,issue", sorted(FLAG_CODE_ISSUES.items()))
def test_every_mapped_flag_code_raises_its_issue(flag, issue):
    """The whole point of the mapping: a flag the pipeline raised must reach
    the Issues column. Parametrised over the table itself, so a flag added to
    it without an emission path fails here."""
    assert issue in detect_issues(_record(), flag_codes=[flag])


def test_the_three_doubts_are_three_different_queues():
    """They ask for different work — supply a value nothing can resolve,
    confirm one the pipeline wrote, establish one it could not — so a record
    carrying all three says all three."""
    codes = detect_issues(
        _record(),
        flag_codes=["opaque-code", "domain-unverified", "person-unresolved"],
    )
    assert codes == ["G6-RESOLVE-001", "G7-CONFIRM-001", "G8-VERIFY-001"]


def test_several_flags_mapping_to_one_issue_raise_it_once():
    codes = detect_issues(
        _record(),
        flag_codes=["opaque-code", "email-conflict", "multiple-contacts"],
    )
    assert codes == ["G6-RESOLVE-001"]


def test_flag_derived_codes_absent_from_a_raw_input_audit():
    """The same rule G7-VERIFY-001 follows, for the same reason: these report
    what enrichment concluded, and a raw file carries no Flag Codes column."""
    dirty = _record(**{
        "Name 1": "E004120188", "Name 2": "10901 Roosevelt Blvd N",
        "Contact": "Dr. Jane Smith; Prof. Bob Lee",
    })
    for code in ("G6-RESOLVE-001", "G7-CONFIRM-001", "G8-VERIFY-001"):
        assert code not in detect_issues(dirty)
        assert code not in detect_issues(dirty, flag_codes=None)
    # An enriched record the pipeline flagged nothing on is not the same state
    # as a raw one, and also raises none of them.
    assert detect_issues(_record(), flag_codes=[]) == []


def test_an_unmapped_flag_code_raises_nothing():
    """The pipeline's vocabulary is larger than the reviewer-facing catalogue.
    `overflow` is already reported as G1-NAME-001 from the record's own
    content; reporting it again from the flag would double-count it."""
    assert detect_issues(_record(), flag_codes=["overflow", "not-a-code"]) == []


@pytest.mark.parametrize("cell,expected", [
    ("opaque-code", ["opaque-code"]),
    ("opaque-code; no-match", ["opaque-code", "no-match"]),
    ("opaque-code, no-match", ["opaque-code", "no-match"]),
    (["opaque-code", "no-match"], ["opaque-code", "no-match"]),
    ("  opaque-code ;; ", ["opaque-code"]),
    ("", []), (None, []),
])
def test_flag_codes_cell_spellings(cell, expected):
    assert split_flag_codes(cell) == expected


@pytest.mark.parametrize("scalar,expected", [
    ("input:low", True),
    ("input:low+llm", True),
    # Two colons: a naive split puts the domain in the confidence slot.
    ("web:acme.com:low", True),
    ("ror:verified", False),
    ("llm:provisional", False),
    ("", False), (None, False), ("not provenance at all", False),
])
def test_provenance_is_low_reads_the_grammar(scalar, expected):
    assert provenance_is_low(scalar) is expected


def test_g8_covers_the_retired_low_confidence_token():
    """`low-confidence-unchanged` was retired as a flag code — it can never
    appear in Flag Codes again — and the state it named lives in the
    provenance columns. The caller supplies the token from there; the mapping
    must still honour it or G8 goes dark for the largest population it
    describes."""
    assert FLAG_CODE_ISSUES[DERIVED_LOW_FLAG_CODE] == "G8-VERIFY-001"
    assert "G8-VERIFY-001" in detect_issues(
        _record(), flag_codes=[DERIVED_LOW_FLAG_CODE],
    )


def test_flag_derived_codes_are_not_in_the_reduction_metric():
    """G6 is expected to persist; G7 and G8 are reported separately. None of
    the three may move the before/after percentage."""
    for code in ("G6-RESOLVE-001", "G7-CONFIRM-001", "G8-VERIFY-001"):
        assert issue_group(code) not in REDUCIBLE_GROUPS


# ---------------------------------------------------------------------------
# Origin filtering (9h)
# ---------------------------------------------------------------------------

def test_ds_only_codes_are_emitted_by_default_with_a_documented_reason():
    """The API raises DS-origin codes today. That is deliberate — /issues also
    runs standalone over a raw workbook — and the reason is on ``detect_issues``
    so the duplicate-in-DATAshaper consequence is not undocumented."""
    issues = detect_issues(_record(**{"Postal Code": ""}))
    assert "G2-VAL-002" in issues
    assert ISSUE_CATALOGUE["G2-VAL-002"].origin == "DS"
    assert "standalone" in (detect_issues.__doc__ or "")


def test_ds_only_codes_are_suppressed_for_a_datashaper_facing_feed():
    """``origins=("API", "BOTH")`` yields exactly the set the API should raise
    when DATAshaper already runs the DS rules itself."""
    rec = _record(**{"Postal Code": "", "Name 2": "10901 Roosevelt Blvd N"})
    issues = detect_issues(rec, origins=("API", "BOTH"))
    assert "G2-VAL-002" not in issues          # DS-only — DATAshaper's own rule
    assert "G1-CROSS-001" in issues            # API-origin — ours to raise
    assert all(ISSUE_CATALOGUE[c].origin in ("API", "BOTH") for c in issues)


def test_the_ds_only_live_codes_are_catalogue_v2s_less_the_withdrawn():
    """Catalogue v2's eleven, minus G2-VAL-003 and G2-VAL-006."""
    ds_only = sorted(
        code for code, e in ISSUE_CATALOGUE.items()
        if e.origin == "DS" and e.status == "live" and e.group in QUALITY_GROUPS
    )
    assert ds_only == [
        "G1-ADDR-001", "G2-NAME-012", "G2-VAL-001", "G2-VAL-002",
        "G2-VAL-004", "G2-VAL-007", "G2-VAL-008", "G4-ADDR-026",
        "G4-ADDR-027",
    ]


# ---------------------------------------------------------------------------
# Regression tests — defects found by diffing the detector against a
# hand-built answer key over the 50-record demo batch. Each test is named
# after the defect and uses the real value that exposed it.
# ---------------------------------------------------------------------------

def test_required_field_rules_all_have_a_reachable_column_mapping():
    """A G2-VAL-* rule keyed on a field EnrichmentRecord does not declare — or
    declares with no input alias — is skipped on every record and every file
    and looks exactly like a clean run. The gate is silent by construction, so
    the mismatch is caught at import instead."""
    from enrichment.issue_detection import _REQUIRED_FIELD_MAPPING_PROBLEMS

    assert _REQUIRED_FIELD_MAPPING_PROBLEMS == []


def test_region_is_reachable_from_the_region_column_header():
    """G2-VAL-004 firing zero times on a file full of blank Regions was first
    suspected to be a Region -> EnrichmentRecord mapping gap. It was not: the
    column maps and the gate passes, which is what localised the fault to the
    rule's own condition (see
    test_g2_val_004_fires_for_a_blank_region_whatever_the_country)."""
    from api.routes import _present_fields

    assert "region" in _present_fields(["Customer", "Name 1", "Region"])
    # Present-and-blank on a US record: the gate lets it through and it fires.
    assert "G2-VAL-004" in detect_issues(
        _record(**{"Region": ""}), {"region", "country_region_key"},
    )


@pytest.mark.parametrize("street", [
    "SCHELLINGSTR 24",           # 42000004, 42000005, 42000006
    "KEPLERSTR 7",               # 42000001, 42000002, 42000003
    "WERNER-VON-SIEMENS-STR 1",  # 40000010
])
def test_g1_addr_001_fires_for_a_german_compound_street_type(street):
    """German street types are a suffix on the street name, not a separate
    token, so the English standalone-token test could not see them and every
    German address read as "not a street"."""
    rec = _record(**{"Street 1": street, "House Number": ""})
    assert "G1-ADDR-001" in detect_issues(rec)


@pytest.mark.parametrize("street", [
    "1000 MANUFACTURING ROW",   # gerund ending in -ring
    "500 Engineering Plaza",
    "12 Spring Valley",
])
def test_german_suffix_rule_does_not_fire_on_english_lookalikes(street):
    """``-ring`` collides with every English gerund. German ring-roads put a
    consonant before the suffix and the gerunds put a vowel there, which is
    what separates "Ostring" from "Manufacturing"."""
    rec = _record(**{"Street 1": street, "House Number": ""})
    assert "G1-ADDR-001" not in detect_issues(rec)


def test_g1_addr_003_fires_for_a_gate_sublocation():
    """40000008. GATE was missing from the sub-location vocabulary."""
    rec = _record(**{"Street 1": "4500 SAN PABLO RD S GATE C"})
    assert "G1-ADDR-003" in detect_issues(rec)


def test_g1_addr_003_ignores_a_street_named_after_a_gate():
    """The marker needs an identifier-like value attached, which is what keeps
    a street *name* carrying the word out of it."""
    rec = _record(**{"Street 1": "1 GOLDEN GATE AVE"})
    assert "G1-ADDR-003" not in detect_issues(rec)


@pytest.mark.parametrize("name1", [
    "APEX CORP",                # 40000019
    "TROPICAL PHARMA INC",      # 41000005
    "LOCKHEED MARTIN CORP.",    # 42000009
    "Coastal Diagnostics, Inc", # 42000019
])
def test_g5_name_001_fires_for_an_abbreviated_legal_suffix(name1):
    """"Co" was in the token set and "Corp"/"Inc" were not, so "Smith Co."
    fired the rule and "Smith Corp." did not — an inconsistency, not a
    design choice."""
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": name1}))


@pytest.mark.parametrize("name1", [
    "BRIGHAM & WOMENS HOSP",    # 40000014
    "MAYO CLINIC FLA",          # 40000008
    "Cardinal Research GRP",    # 41000008
    "UNI STUTTGART",            # 42000001
])
def test_g5_name_001_fires_for_a_clipped_organisational_word(name1):
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": name1}))


def test_g5_name_001_fires_for_a_dotted_acronym():
    """40000006. Every letter is its own token, so the word-boundary token
    regex has no multi-character word to anchor on."""
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": "U.C.L.A"}))
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": "U.S.A."}))


@pytest.mark.parametrize("name1", [
    "Acme Corporation",     # the expanded form is what the rule asks for
    "Smith Incorporated",
    "University of Florida",
    "St. Louis Biosciences",  # "St." is two letters, not a dotted acronym
])
def test_g5_name_001_does_not_fire_on_an_expanded_name(name1):
    assert "G5-NAME-001" not in detect_issues(_record(**{"Name 1": name1}))


def test_g5_attribution_follows_the_slot_the_abbreviation_sits_in():
    """40000012. The abbreviation is in Name 2, so this is -002 and not -001;
    the answer key labels it -001."""
    rec = _record(**{"Name 1": "ADAMS AIR", "Name 2": "HYDRAULICS INC"})
    issues = detect_issues(rec)
    assert "G5-NAME-002" in issues
    assert "G5-NAME-001" not in issues


def test_g2_name_012_fires_when_name_2_is_blank_and_the_department_is_lower():
    """42000011 Yale University, Name 2 blank, Name 3 "Department of
    Chemistry". Scanning the whole block for a department suppressed the code
    exactly when a department sat in the wrong slot — which is the record a
    steward most needs to see. G1-NAME-004 reports the misplacement; this code
    reports that Name 2, the slot every downstream consumer reads a department
    from, is empty. Both are true of the record."""
    rec = _record(**{
        "Name 1": "Yale University",
        "Name 2": "",
        "Name 3": "Department of Chemistry",
    })
    issues = detect_issues(rec)
    assert "G2-NAME-012" in issues
    assert "G1-NAME-004" in issues


def test_g2_name_012_does_not_fire_on_a_company_named_research():
    """41000008 "Cardinal Research GRP" is a company. The bare token
    "Research" matched the university-or-research signal and raised a
    missing-department code against the org type that has no departments to
    miss."""
    rec = _record(**{"Name 1": "Cardinal Research GRP", "Name 2": ""})
    assert "G2-NAME-012" not in detect_issues(rec)


@pytest.mark.parametrize("name1", [
    "Research Institute of Molecular Pathology",
    "Delta Research Center",
    "Fraunhofer Research Laboratory",
])
def test_g2_name_012_still_fires_for_research_as_part_of_an_institution_phrase(name1):
    """Tightening the signal must not cost the real institutions: "Research"
    qualifies inside the phrases that name one."""
    assert "G2-NAME-012" in detect_issues(_record(**{"Name 1": name1, "Name 2": ""}))


@pytest.mark.parametrize("name1", [
    "Hochschule fuer Technik Stuttgart",   # 42000005
    "Fachhochschule Koeln",
])
def test_g2_name_012_fires_for_a_german_hochschule(name1):
    """The previous bare ``Schule`` alternative matched neither "Hochschule"
    nor its compounds — neither offers a word boundary before it."""
    assert "G2-NAME-012" in detect_issues(_record(**{"Name 1": name1, "Name 2": ""}))


@pytest.mark.parametrize("postal", ["7017", "701744", "D-70174", "70 174"])
def test_g4_addr_026_fires_for_an_invalid_german_postal_code(postal):
    """Before the DE format was added, no German postal code could be
    validated at all — a clean count meant "unchecked", not "correct"."""
    rec = _record(**{"Postal Code": postal, "Country/Region Key": "DE"})
    assert "G4-ADDR-026" in detect_issues(rec)


@pytest.mark.parametrize("postal", ["70174", "80333"])
def test_g4_addr_026_accepts_a_valid_german_postal_code(postal):
    rec = _record(**{"Postal Code": postal, "Country/Region Key": "DE"})
    assert "G4-ADDR-026" not in detect_issues(rec)


def test_postal_validation_is_silent_for_an_uncovered_country():
    """Coverage is US, CA and DE. Anything else is unchecked, not valid —
    read a clean G4-ADDR-026 count accordingly."""
    rec = _record(**{"Postal Code": "not-a-postcode", "Country/Region Key": "FR"})
    assert "G4-ADDR-026" not in detect_issues(rec)


@pytest.mark.parametrize("street", [
    "500 TECH DR STE 210 MS-4",                              # 40000015
    "2301 Erwin Rd Mail Stop 100",                           # 40000007
    "2200 LAKE BLVD STE 300 BLDG 4 WING C RM 412A MS K-12",  # 41000007
])
def test_g1_addr_006_fires_for_a_mail_stop_in_a_street_field(street):
    """The mail-code patterns recognised only the literal words "Mail Code"
    and two shapes with the digits welded to the letters, so every corpus
    value — hyphenated or space-separated — read as no mail code at all."""
    assert "G1-ADDR-006" in detect_issues(_record(**{"Street 1": street}))


@pytest.mark.parametrize("street", [
    "123 Main St MS",           # bare marker — Mississippi, no value
    "1 Main St, Jackson, MS 39201",   # state + ZIP, not a mail stop
    "400 Ms Johnson Way",       # honorific, value is not identifier-like
])
def test_g1_addr_006_does_not_fire_on_a_mississippi_or_honorific_ms(street):
    assert "G1-ADDR-006" not in detect_issues(_record(**{"Street 1": street}))
