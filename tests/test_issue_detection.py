"""Unit tests for the deterministic issue detector (Issue Catalogue v2).

Examples are drawn from the catalogue itself wherever possible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.issue_detection import ISSUE_CATALOGUE, detect_issues


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

def test_catalogue_has_36_codes():
    assert len(ISSUE_CATALOGUE) == 36


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


def test_g1_name_013_sap_code_in_name():
    assert "G1-NAME-013" in detect_issues(_record(**{"Name 2": "B800000345"}))


def test_g1_addr_009_never_emitted():
    # LLM-only rule — must never be produced by the deterministic detector.
    rec = _record(**{"Street 2": "Loading Dock - East Side"})
    assert "G1-ADDR-009" not in detect_issues(rec)


# ---------------------------------------------------------------------------
# G2 — Missing Required Data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,code", [
    ("Name 1", "G2-VAL-001"),
    ("Postal Code", "G2-VAL-002"),
    ("Tax Jurisdiction", "G2-VAL-003"),
    ("Region", "G2-VAL-004"),
    ("Language Key", "G2-VAL-006"),
    ("Search Term 1", "G2-VAL-007"),
    ("Country/Region Key", "G2-VAL-008"),
])
def test_g2_missing_required_fields(field, code):
    assert code in detect_issues(_record(**{field: ""}))


def test_g2_name_012_research_institution_missing_department():
    rec = _record(**{"Name 1": "Florida State University", "Name 2": ""})
    assert "G2-NAME-012" in detect_issues(rec)


def test_g2_name_009_lab_without_department():
    rec = _record(**{"Name 1": "University of Florida", "Name 2": "Smith Lab"})
    assert "G2-NAME-009" in detect_issues(rec)


def test_g2_contact_008_no_contact_no_department():
    rec = _record(**{"Name 2": "", "Contact": "", "Name 1": "Acme Corp"})
    assert "G2-CONTACT-008" in detect_issues(rec)


def test_g2_contact_009_department_enrichable_from_contact():
    rec = _record(**{"Name 2": "", "Contact": "Dr. Emily Carter"})
    issues = detect_issues(rec)
    assert "G2-CONTACT-009" in issues
    assert "G2-CONTACT-008" not in issues  # mutually exclusive


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


def test_g4_addr_025_never_emitted():
    rec = _record(**{"Street 2": "Bldg 4 Floor 3 Wing A Suite 9 Room 5"})
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
