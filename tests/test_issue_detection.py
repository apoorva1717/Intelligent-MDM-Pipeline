"""Unit tests for the deterministic issue detector (Issue Catalogue v2).

Examples are drawn from the catalogue itself wherever possible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.dept_block import classify
from utils.text_utils import _ADMIN_UNIT_PHRASES, _ADMIN_UNIT_TERMS
from enrichment.issue_detection import (
    EMITTED_CODES,
    ISSUE_CATALOGUE,
    FLAG_CODE_ISSUES,
    QUALITY_GROUPS,
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

def test_catalogue_declares_43_entries():
    assert len(ISSUE_CATALOGUE) == 43


def test_status_counts_match_catalogue_v2():
    """33 live, 10 withdrawn, nothing unlisted, nothing left marked ``ndd``.

    Catalogue v2 declared 34 live and the figure reached 34 again by a
    different route: two flag-derived codes added (G6-CONFIRM-001,
    G7-UNCHANGED-001), two codes added on 2026-09-06 (G3-NAME-006,
    G3-CONTACT-010), G3-ADDR-012 resolved from unlisted to live, and seven
    entries withdrawn that day. G1-NAME-001 was withdrawn on 2026-09-07,
    taking it to 33.
    """
    from collections import Counter

    counts = Counter(entry.status for entry in ISSUE_CATALOGUE.values())
    assert counts == {"live": 33, "withdrawn": 10}


def test_withdrawn_codes_are_declared_but_never_emitted():
    """Withdrawn entries stay declared for the audit trail — retaining them
    records that they existed and why — but nothing may emit them.

    The first two are struck through in Catalogue v2. The next six were
    withdrawn on 2026-09-06: a rule no deterministic detector can express, one
    that fired on nothing in 500 records, two required-field rules over fields
    outside master-data scope, a routing code now carried by group membership,
    and one whose true hits were outweighed by false positives it had no source
    to rule out. G1-NAME-001 followed on 2026-09-07.
    """
    for code in (
        "G2-CONTACT-008", "G2-CONTACT-009",
        "G1-ADDR-009", "G4-ADDR-008", "G4-ADDR-025", "G2-VAL-003",
        "G2-VAL-006", "G7-VERIFY-001",
        "G1-NAME-001",
    ):
        assert ISSUE_CATALOGUE[code].status == "withdrawn"
        assert ISSUE_CATALOGUE[code].reason
        assert code not in EMITTED_CODES


def test_group_is_an_attribute_not_a_prefix():
    """The old G6 was a regrouping: codes kept their original G2- identifiers,
    so slicing the prefix gives the wrong group.

    The 2026-09-06 renumber dissolved that group and returned its two live
    members to G2, so only its withdrawn members still carry the mismatch.
    The invariant is unchanged — read the attribute, never the prefix — and
    these two are what still exercises it.
    """
    for code in ("G2-VAL-003", "G2-VAL-006"):
        assert issue_group(code) == "G6"
        assert code.split("-")[0] == "G2"
    # The two that came back to G2 now agree with their prefix, by accident
    # rather than by rule.
    for code in ("G2-VAL-001", "G2-NAME-012"):
        assert issue_group(code) == "G2"


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
    G1-G6 codes. The gap against v2 is the 2026-09-06 rework and must stay
    visible: four withdrawals off the live count, two codes added, and four
    codes moved from API to BOTH when a ``Flag Codes`` path was mapped onto a
    detector that already existed. G1-NAME-001 came off the API-only count
    when it was withdrawn on 2026-09-07.

    The census is over the codes derived from record CONTENT, which is what v2
    counted. The exclusion is ``raised="enriched"`` — a code with no content
    path at all — and not "has a flag mapping": G1-NAME-013 and
    G3-CONTACT-007 are reached both ways and are content codes in this census.
    """
    from collections import Counter

    live_quality = [
        e for e in ISSUE_CATALOGUE.values()
        if e.status == "live"
        and e.group in QUALITY_GROUPS
        and e.raised != "enriched"
    ]
    assert len(live_quality) == 30
    assert Counter(e.origin for e in live_quality) == {"DS": 8, "API": 15, "BOTH": 7}


def test_the_group_constants_are_labels_not_metric_rules():
    """They name the catalogue's shape for the census and the docstring.

    Nothing in the comparison report keys off them any more — ``segment()``
    reads ``remedy`` and ``raised``. ``REDUCIBLE_GROUPS`` and
    ``PERSISTENT_GROUP``, which did key off the group, were deleted with the
    2026-09-06 rework and must not come back.
    """
    import enrichment.issue_detection as module

    assert set(QUALITY_GROUPS) == {"G1", "G2", "G3", "G4", "G5"}
    assert set(VERIFICATION_GROUPS) == {"G6", "G7"}
    assert not hasattr(module, "REDUCIBLE_GROUPS")
    assert not hasattr(module, "PERSISTENT_GROUP")


# The reference table this rework was specified against. Written out rather
# than derived so a change to a code's remedy has to be made here too, in
# front of a reader, instead of quietly moving a code in or out of the
# headline percentage.
REDUCTION_METRIC_CODES = {
    "G1-CROSS-001", "G1-CROSS-002", "G1-CROSS-003",
    "G1-ADDR-001", "G1-ADDR-003", "G1-ADDR-004", "G1-ADDR-006", "G1-ADDR-011",
    "G1-NAME-004",
    "G2-VAL-007", "G2-VAL-008", "G2-NAME-009",
    "G3-NAME-003", "G3-NAME-005", "G3-ADDR-012",
    "G4-ADDR-027",
    "G5-NAME-001", "G5-NAME-002",
}


def test_every_emittable_entry_declares_raised_and_remedy():
    """A code that can fire must say what file it fires on and who fixes it.

    ``segment()`` in the comparison report reads exactly these two, so a code
    missing either would fall through to whichever branch its ``None`` happens
    not to match — silently landing in the reduction metric. Withdrawn entries
    are the deliberate exception: they never fire, and giving them a value
    would enrol them in a set they cannot contribute to.
    """
    for code in EMITTED_CODES:
        entry = ISSUE_CATALOGUE[code]
        assert entry.raised in ("raw", "enriched", "both"), code
        assert entry.remedy in ("rule", "enrichment", "steward"), code
    for entry in ISSUE_CATALOGUE.values():
        if entry.status == "withdrawn":
            assert entry.raised is None and entry.remedy is None, entry.code


def test_reduction_metric_set_is_the_reference_table():
    """The codes the headline percentage is computed over."""
    actual = {
        code for code, entry in ISSUE_CATALOGUE.items()
        if entry.remedy in ("rule", "enrichment")
    }
    assert actual == REDUCTION_METRIC_CODES
    assert len(actual) == 18


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


@pytest.mark.parametrize("fields", [
    # Dash form — what preprocessing strips via split_site_suffix.
    {"Name 2": "UCSD Moores Cancer Center - La Jolla, CA"},
    # Comma form — two commas and a state code.
    {"Name 2": "Bruker BioSpin, Billerica, MA"},
    # Bare state+zip tail.
    {"Name 1": "Thermo Fisher Scientific San Jose CA 95134"},
])
def test_g1_cross_001_fires_on_a_trailing_site_qualifier(fields):
    """A site qualifier is address content in a name field. _extract_addresses
    looks for a street and finds none, but the pipeline recognises the shape
    and strips it (preprocess.py:2405), so the detector must report it."""
    assert "G1-CROSS-001" in detect_issues(_record(**fields))


@pytest.mark.parametrize("fields", [
    # A place with no state code is part of the name.
    {"Name 1": "University of California, San Diego"},
    {"Name 1": "Boston Children's Hospital"},
    {"Name 2": "Department of Chemistry"},
    # The trap: "St. Louis" is in the university's own name, and there is no
    # state code after it.
    {"Name 1": "Washington University in St. Louis"},
    # One comma never reaches the region test — "PA" here is a professional
    # association, which is the false positive split_site_suffix guards.
    {"Name 1": "Jones, PA"},
    {"Name 1": "Smith, Miller & Jones, LLP"},
])
def test_g1_cross_001_does_not_fire_without_a_state_code(fields):
    assert "G1-CROSS-001" not in detect_issues(_record(**fields))


def test_g1_cross_002_org_in_street():
    assert "G1-CROSS-002" in detect_issues(_record(**{"Street 1": "AGILENT TECHNOLOGIES"}))


def test_g1_cross_002_university_centre_not_flagged():
    # "University Centre" (and acronyms of centre) is a building name, not an
    # org name misplaced in the address field.
    for value in ("University Centre", "University Center", "University Ctr",
                  "University Ctre", "University Cntr", "UNIVERSITY CENT"):
        assert "G1-CROSS-002" not in detect_issues(_record(**{"Street 1": value})), value


@pytest.mark.parametrize("fields", [
    # Mid-field — the shape the prefix anchor missed. UC 7 Pattern A finds this
    # clause and routes "Christina Boske" out to Contact.
    {"Name 2": "Accounts Payable - ATTN: Christina Boske"},
    {"Name 3": "Receiving, attn J. Doe"},
    # Prefix forms — unchanged.
    {"Name 2": "ATTN: Christina Boske"},
    {"Name 2": "c/o Jane Smith"},
])
def test_g1_cross_003_finds_the_attn_clause_anywhere_in_the_field(fields):
    assert "G1-CROSS-003" in detect_issues(_record(**fields))


@pytest.mark.parametrize("name2", [
    # The trap. The literal UC 7 Pattern A regex (_ATTN_RE) DOES fire here,
    # because it spells out "attention" as an alternative; the shared marker
    # does not. See the Step Q report.
    "Attention to Detail Labs",
    "Accounts Payable",
])
def test_g1_cross_003_does_not_fire_on_an_ordinary_name(name2):
    assert "G1-CROSS-003" not in detect_issues(_record(**{"Name 2": name2}))


@pytest.mark.parametrize("value", ["307 BOATNER RD", "40 CATTNER Blvd", "9 PATTON DR"])
def test_g1_cross_003_marker_boundaries_survive_being_unanchored(value):
    """Unanchoring is only safe because ``_CO_ATTN_MARKER`` carries ``\b`` on
    both sides. Unbounded, ``att?n+`` matches the "ATN" inside "BOATNER" —
    the defect that comment documents (preprocess.py:1143-1152)."""
    assert "G1-CROSS-003" not in detect_issues(_record(**{"Street 1": value}))


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


@pytest.mark.parametrize("street2", ["Rm. EC2614", "Suite EC2614"])
def test_g1_addr_006_does_not_fire_on_a_markers_own_value(street2):
    """The bare-code pattern sees "EC2614" and never looks left at the "Rm."
    that names it. The token is the sub-location's value, and G1-ADDR-003
    already reports it."""
    issues = detect_issues(_record(**{"Street 2": street2}))
    assert "G1-ADDR-003" in issues
    assert "G1-ADDR-006" not in issues


@pytest.mark.parametrize("street2", ["EC2614", "SVC1039"])
def test_g1_addr_006_still_fires_on_a_genuinely_bare_code(street2):
    assert "G1-ADDR-006" in detect_issues(_record(**{"Street 2": street2}))


def test_g1_addr_006_and_003_both_fire_when_both_are_present():
    """Two different things in one slot: a room, and a mail stop behind its own
    marker. Neither suppresses the other."""
    issues = detect_issues(_record(**{"Street 2": "Rm 200, MS K-12"}))
    assert "G1-ADDR-003" in issues
    assert "G1-ADDR-006" in issues


def test_g1_addr_006_bare_form_is_street_2_and_below_only():
    """``allow_bare`` follows the pipeline (address_processing:1183): a bare
    token in Street 1 is never extracted, so reporting it overstated the raw
    count against a value the pipeline would leave alone."""
    assert "G1-ADDR-006" not in detect_issues(_record(**{"Street 1": "SVC1039 MAIN ST"}))
    assert "G1-ADDR-006" in detect_issues(_record(**{"Street 2": "SVC1039 MAIN ST"}))


def test_g1_addr_006_explicit_form_fires_in_street_1():
    """The slot restriction is on the bare form only. A value that says what it
    is carries its own marker and is reported wherever it sits."""
    assert "G1-ADDR-006" in detect_issues(_record(**{"Street 1": "MAIL CODE: SVC1039"}))
    assert "G1-ADDR-006" in detect_issues(_record(**{"Street 1": "500 Tech Dr MS-4"}))


def test_g1_addr_011_department_label_in_street():
    assert "G1-ADDR-011" in detect_issues(_record(**{"Street 2": "Receiving Department"}))


def test_g1_name_001_is_withdrawn_and_an_overflow_no_longer_raises_it():
    rec = _record(**{
        "Name 1": "Orlando Health Emergency Room",
        "Name 2": "and Medical Pavilion - Osceola",
    })
    assert "G1-NAME-001" not in detect_issues(rec)


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


def test_g2_name_012_fires_when_name_2_holds_only_an_admin_desk():
    """An occupied Name 2 is not the same as a department. "Central Receiving"
    is a loading dock: the record is exactly as short of a department as one
    with Name 2 blank, and a blank test reports nothing about it."""
    rec = _record(**{
        "Name 1": "University of Texas Medical Branch",
        "Name 2": "Central Receiving",
    })
    assert "G2-NAME-012" in detect_issues(rec)


def test_g2_name_012_blank_name_2_still_fires():
    rec = _record(**{"Name 1": "Michigan Tech University", "Name 2": ""})
    assert "G2-NAME-012" in detect_issues(rec)


@pytest.mark.parametrize("desk", sorted(_ADMIN_UNIT_TERMS | _ADMIN_UNIT_PHRASES))
def test_g2_name_012_fires_for_every_desk_in_dept_blocks_admin_lexicon(desk):
    """Read from ``dept_block``'s own vocabulary rather than restated here, so
    the rule cannot drift from the lexicon it delegates to: a term added or
    removed there changes this test's parameters on the next run."""
    assert classify(desk) == "admin", desk
    rec = _record(**{"Name 1": "Florida State University", "Name 2": desk})
    assert "G2-NAME-012" in detect_issues(rec)


def test_g2_name_012_does_not_fire_on_a_real_department():
    rec = _record(**{
        "Name 1": "University of Florida", "Name 2": "Department of Chemistry",
    })
    assert "G2-NAME-012" not in detect_issues(rec)


def test_g2_name_012_leaves_a_granular_unit_to_g2_name_009():
    """"Smith Lab" is a real named unit below department level. It classifies
    as ``granular``, which is G2-NAME-009's business and not this code's."""
    rec = _record(**{"Name 1": "University of Florida", "Name 2": "Smith Lab"})
    issues = detect_issues(rec)
    assert "G2-NAME-012" not in issues
    assert "G2-NAME-009" in issues


def test_g2_name_012_does_not_fire_on_an_admin_desk_at_a_non_research_org():
    """The research gate is unchanged: a company's Accounts Payable desk is
    normal and reports nothing."""
    rec = _record(**{"Name 1": "Acme Corp", "Name 2": "Accounts Payable"})
    assert "G2-NAME-012" not in detect_issues(rec)


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
    exactly one contact. Withdrawn in Catalogue v2, so only G2-NAME-012
    reports the missing department."""
    rec = _record(**{
        "Name 1": "Florida State University", "Name 2": "",
        "Contact": "Dr. Emily Carter",
    })
    issues = detect_issues(rec)
    assert "G2-CONTACT-009" not in issues
    assert "G2-CONTACT-008" not in issues
    assert "G2-NAME-012" in issues
    assert issue_group("G2-NAME-012") == "G2"


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


def test_g3_addr_013_folds_the_house_number_into_street_1():
    """SAP splits "140 Commonwealth Ave" across House Number and Street 1, so
    Street 1 alone carries no number and does not read as a street. The record
    holds two genuinely different addresses and reported neither."""
    rec = _record(**{
        "House Number": "140",
        "Street 1": "COMMONWEALTH AVE",
        "Street 2": "129 Lake Street 250",
    })
    issues = detect_issues(rec)
    assert "G3-ADDR-013" in issues
    assert "G3-ADDR-012" not in issues


def test_g3_addr_013_needs_a_house_number_to_fold():
    """With House Number blank, Street 1 is a street NAME and nothing more —
    one address on the record, not two."""
    rec = _record(**{
        "House Number": "",
        "Street 1": "COMMONWEALTH AVE",
        "Street 2": "129 Lake Street 250",
    })
    assert "G3-ADDR-013" not in detect_issues(rec)


def test_g3_addr_013_a_sub_location_is_not_a_second_address():
    rec = _record(**{
        "House Number": "140",
        "Street 1": "COMMONWEALTH AVE",
        "Street 2": "Suite 250",
    })
    assert "G3-ADDR-013" not in detect_issues(rec)


def test_g3_addr_012_and_013_are_mutually_exclusive_on_a_folded_pair():
    """Both read the same ``_street_signature``: slots it cannot tell apart are
    one address (-012), slots it can are two (-013). Never both."""
    for fields in (
        {"House Number": "500", "Street 1": "Innovation Blvd",
         "Street 2": "500 Innovation Blvd"},
        {"House Number": "140", "Street 1": "COMMONWEALTH AVE",
         "Street 2": "129 Lake Street 250"},
        {"Street 1": "500 Innovation Blvd", "Street 2": "500 Innovation Blvd"},
    ):
        issues = detect_issues(_record(**fields))
        assert not ({"G3-ADDR-012", "G3-ADDR-013"} <= set(issues)), fields


def test_fold_house_number_leaves_a_line_that_has_its_own_number():
    """A line carrying a digit is complete; prepending would invent an address
    neither field states. Same condition ``_street_signature`` applies."""
    from enrichment.issue_detection import _fold_house_number
    assert _fold_house_number("COMMONWEALTH AVE", "140") == "140 COMMONWEALTH AVE"
    assert _fold_house_number("500 Innovation Blvd", "500") == "500 Innovation Blvd"
    assert _fold_house_number("COMMONWEALTH AVE 250", "140") == "COMMONWEALTH AVE 250"
    assert _fold_house_number("COMMONWEALTH AVE", "") == "COMMONWEALTH AVE"
    assert _fold_house_number("COMMONWEALTH AVE", None) == "COMMONWEALTH AVE"
    assert _fold_house_number(None, "140") is None


def test_g3_addr_012_directional_spellings_are_one_address():
    """"S Main St" + House Number "301" and "301 South Main St" are one
    address. Raw-token comparison called them two ("s" != "south") and -013
    reported a second address that is not there."""
    rec = _record(**{
        "House Number": "301",
        "Street 1": "S Main St",
        "Street 2": "301 South Main St",
    })
    issues = detect_issues(rec)
    assert "G3-ADDR-012" in issues
    assert "G3-ADDR-013" not in issues


def test_g3_addr_013_opposite_directionals_are_two_addresses():
    """The canonicalisation folds spellings together, not directions: S and N
    are different places and stay different signatures."""
    rec = _record(**{
        "House Number": "301",
        "Street 1": "S Main St",
        "Street 2": "301 N Main St",
    })
    issues = detect_issues(rec)
    assert "G3-ADDR-013" in issues
    assert "G3-ADDR-012" not in issues


def test_g3_addr_012_street_type_spellings_are_one_address():
    rec = _record(**{
        "House Number": "500",
        "Street 1": "Innovation Blvd",
        "Street 2": "500 Innovation Boulevard",
    })
    issues = detect_issues(rec)
    assert "G3-ADDR-012" in issues
    assert "G3-ADDR-013" not in issues


@pytest.mark.parametrize("a,b", [
    ("Main St", "Main Street"),
    ("Main Ave", "Main Avenue"),
    ("S Main St", "South Main Street"),
    ("NE Center Blvd", "Northeast Center Boulevard"),
])
def test_street_signature_folds_spelling_variants(a, b):
    from enrichment.issue_detection import _street_signature
    assert _street_signature(a) == _street_signature(b), (a, b)


@pytest.mark.parametrize("a,b", [
    ("S Main St", "N Main St"),
    ("NE Center Blvd", "SW Center Blvd"),
])
def test_street_signature_keeps_opposite_directions_apart(a, b):
    from enrichment.issue_detection import _street_signature
    assert _street_signature(a) != _street_signature(b), (a, b)


def test_street_signature_keeps_a_mixed_letter_digit_token_whole():
    """The tokeniser this replaced shredded "72B20" into "72", "b" and "20",
    putting two numbers into the digit set that the address does not contain."""
    from enrichment.issue_detection import _street_signature
    nums, words = _street_signature("72B20 520 I St")
    assert nums == frozenset({"520"})
    assert "72b20" in words


@pytest.mark.parametrize("hn", ["809-C", "45A"])
def test_house_number_fold_reads_digits_not_the_street_key(hn):
    """An alphanumeric House Number contributes its digits and no word — the
    fold never goes through ``_norm_street_key``, so -012 and -013 keep
    comparing the same signature."""
    from enrichment.issue_detection import _street_signature
    nums, words = _street_signature("COMMONWEALTH AVE", hn)
    assert words == ("ave", "commonwealth")
    assert nums == frozenset({hn.split("-")[0].rstrip("A")}) or nums == frozenset(
        {"".join(c for c in hn if c.isdigit())}
    )


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


def test_overflow_slots_still_scans_every_pair_and_raises_nothing():
    """``_overflow_slots`` survives the withdrawal because G5 reads it to
    know WHICH slots are halves. It still scans every adjacent pair, and no
    pair it finds raises G1-NAME-001 any more."""
    from enrichment.issue_detection import _overflow_slots
    cases = [
        {"Name 1": "University of Florida"},
        {"Name 1": "Univ of Florida", "Name 2": "and Materials Science"},
        {"Name 1": "Coastal Holdings Inc", "Name 2": "and Marine"},   # legal suffix closes it
        {"Name 1": "University of Florida", "Name 2": "Sch of Chemical Engineering &",
         "Name 3": "and Materials Science"},
        {"Name 1": "University of Florida", "Name 2": "Department of Chemistry"},
        # Head arm.
        {"Name 1": "University of Florida", "Name 2": "Dept of Chemical Engineering &",
         "Name 3": "Materials Science"},
        {"Name 1": "Univ of Florida College of", "Name 2": "Engineering"},
        {"Name 1": "Orlando Health Emergency Room and", "Name 2": "Medical Pavilion"},
        # Tail arm, previously dead.
        {"Name 1": "University of Florida", "Name 2": "Dept of Chemical Engineering",
         "Name 3": "& Materials Science"},
        # Neither arm.
        {"Name 1": "University of Florida", "Name 2": "Dept of Chemical Engineering",
         "Name 3": "Materials Science"},
        {"Name 1": "Andover Medical Center", "Name 2": "Radiology"},
        {"Name 1": "Bruker BioSpin", "Name 2": ""},
    ]
    for fields in cases:
        rec = _record(**fields)
        _overflow_slots(rec)  # still callable on every shape
        assert "G1-NAME-001" not in detect_issues(rec), fields


def test_overflow_slots_returns_both_halves_of_the_pair():
    from enrichment.issue_detection import _overflow_slots
    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Sch of Chemical Engineering &",
        "Name 3": "and Materials Science",
    })
    assert _overflow_slots(rec) == {"name_2", "name_3"}


@pytest.mark.parametrize("fields", [
    # Head arm — the upper slot trails off on a connector. The tail starts with
    # a capital in every one of these, which is why the tail arm alone missed
    # them.
    {"Name 2": "Dept of Chemical Engineering &", "Name 3": "Materials Science"},
    {"Name 1": "Univ of Florida College of", "Name 2": "Engineering"},
    {"Name 1": "Orlando Health Emergency Room and", "Name 2": "Medical Pavilion"},
    # A trailing period does not close a dangling connector.
    {"Name 1": "Univ of Florida College of.", "Name 2": "Engineering"},
    # Upper-cased block: the head arm is case-insensitive.
    {"Name 1": "ORLANDO HEALTH EMERGENCY ROOM AND", "Name 2": "MEDICAL PAVILION"},
    # A legal suffix does not close a value that ends mid-phrase.
    {"Name 1": "Acme Corp &", "Name 2": "Sons"},
    # Tail arm on a bare ampersand — dead until the \b was removed from the
    # symbol branch.
    {"Name 2": "Dept of Chemical Engineering", "Name 3": "& Materials Science"},
])
def test_g1_name_001_no_longer_fires_on_a_seam_at_either_end(fields):
    assert "G1-NAME-001" not in detect_issues(_record(**fields))


@pytest.mark.parametrize("fields", [
    # No connector at either end.
    {"Name 2": "Dept of Chemical Engineering", "Name 3": "Materials Science"},
    # "Andover" must not be read as "and" — the word connectors keep their \b.
    {"Name 1": "Andover Medical Center", "Name 2": "Radiology"},
    # A pair needs two populated slots.
    {"Name 1": "Bruker BioSpin", "Name 2": ""},
    # Words that merely CONTAIN a connector.
    {"Name 1": "Cabinet Systems Group", "Name 2": "Fabrication"},
    {"Name 1": "Theodore Roosevelt Institute", "Name 2": "Neurology"},
])
def test_g1_name_001_does_not_fire_without_a_seam(fields):
    assert "G1-NAME-001" not in detect_issues(_record(**fields))


@pytest.mark.parametrize("name2", [
    "accountspayable@calstatela.edu",   # opens lowercase — the [a-z] branch
    "ap-einvoice@uthscsa.edu",
    "c/o Jane Smith",
    "attn: Accounts Payable",
    "www.calstate.edu",
    "tel 555 123 4567",
])
def test_g1_name_001_does_not_read_contact_content_as_a_continuation(name2):
    """An email in Name 2 opens lowercase, which satisfied the tail arm's
    ``[a-z]`` branch. It is not the second half of an organisation name — it
    is contact content in a name field, which G1-CROSS-003 already reports."""
    rec = _record(**{"Name 1": "California State University LA", "Name 2": name2})
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G1-CROSS-003" in issues


def test_g1_name_001_no_longer_fires_on_a_real_continuation():
    rec = _record(**{
        "Name 1": "Orlando Health Emergency Room", "Name 2": "and Medical Pavilion",
    })
    assert "G1-NAME-001" not in detect_issues(_record(**{
        "Name 1": "Orlando Health Emergency Room", "Name 2": "and Medical Pavilion",
    }))
    assert "G1-CROSS-003" not in detect_issues(rec)


def test_the_contact_exclusion_is_tail_arm_only():
    """The head arm is unaffected: if the upper slot trails off on a connector
    the pair is a split value regardless of what landed in the slot below."""
    rec = _record(**{"Name 1": "Coastal Marine Inc &", "Name 2": "c/o Jane Smith"})
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G1-CROSS-003" in issues


def test_g1_cross_003_and_the_tail_arm_ask_the_same_question():
    """Both read ``_is_contact_content``, so a value cannot be contact content
    for one rule and name content for the other."""
    from enrichment.issue_detection import _is_contact_content
    for value in ("jane@acme.com", "c/o Jane Smith", "ATTN: AP", "www.acme.com"):
        rec = _record(**{"Name 1": "Coastal Marine Inc", "Name 2": value})
        assert _is_contact_content(value), value
        assert "G1-CROSS-003" in detect_issues(rec), value
        assert "G1-NAME-001" not in detect_issues(rec), value


def test_g5_does_not_judge_a_head_arm_half_value():
    """Step J's suppression applies to both arms. These are the shapes that
    motivated it: the head trails off, so neither half is a whole name."""
    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Dept of Chemical Engineering &",
        "Name 3": "Materials Science",
    })
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-002" not in issues

    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Sch of Chemical Engineering &",
        "Name 3": "Materials Science",
    })
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-002" not in issues

    rec = _record(**{"Name 1": "Univ of Florida College of", "Name 2": "Engineering"})
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-001" not in issues


def test_g5_name_002_does_not_judge_the_form_of_half_a_value():
    """"Sch of Chemical Engineering &" is not a name in a non-official form,
    it is half of a name, and G5 has no question to ask of a fragment."""
    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Sch of Chemical Engineering &",
        "Name 3": "and Materials Science",
    })
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-002" not in issues


def test_g5_name_002_judges_the_form_when_there_is_no_overflow():
    """The same Name 2 with a tail that does not read as a continuation: no
    pair, so the slot holds a whole value and its form is judged."""
    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Sch of Chemical Engineering",
        "Name 3": "Materials Science",
    })
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-002" in issues


def test_g5_name_002_judges_the_rejoined_value_on_the_enriched_side():
    """What the enriched-side run sees after UC 0 has rejoined the halves: one
    populated slot, no overflow pair, and the abbreviation in the joined text
    surfaces exactly as it should."""
    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Sch of Chemical Engineering & Materials Science",
    })
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-002" in issues


def test_g5_name_001_does_not_judge_name_1_when_it_is_an_overflow_head():
    """Name 1 follows the same rule when it is the head of an overflow into
    Name 2."""
    rec = _record(**{"Name 1": "Univ of Florida College of", "Name 2": "and Engineering"})
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-001" not in issues


def test_g5_skips_only_the_overflow_slots_not_the_whole_block():
    """An overflow lower in the block does not silence G5 on a slot that is
    not part of it: Name 2 is a whole value and is judged."""
    rec = _record(**{
        "Name 1": "University of Florida",
        "Name 2": "Sch of Medicine",
        "Name 3": "Center for Advanced Study &",
        "Name 4": "and Applied Research",
    })
    issues = detect_issues(rec)
    assert "G1-NAME-001" not in issues
    assert "G5-NAME-002" in issues


def test_g5_name_002_unit_not_official():
    rec = _record(**{"Name 1": "University of Florida", "Name 3": "Sch of Med"})
    assert "G5-NAME-002" in detect_issues(rec)


def test_g5_name_002_exempts_the_accepted_unit_forms():
    """Dept, Div and Inst are accepted SAP forms for a unit name, so they are
    dropped from the lexicon the unit slots are matched against."""
    for name3 in ("Dept of Chemistry", "Div of Cardiology", "Inst for Advanced Study"):
        rec = _record(**{"Name 1": "University of Florida", "Name 3": name3})
        assert "G5-NAME-002" not in detect_issues(rec), name3


def test_g5_name_001_exempts_inst_and_nothing_else():
    """Name 1 has its own, smaller exemption. "Inst" heads an organisation, so
    it is an official org form; "Dept" and "Div" name a part of one and stay
    abbreviations when they sit in Name 1."""
    assert "G5-NAME-001" not in detect_issues(_record(**{"Name 1": "Inst of Technology"}))
    for name1 in ("Dept of Chemistry", "Div of Cardiology", "Univ of Florida"):
        assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": name1})), name1


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


def test_the_verification_groups_are_not_quality_groups():
    """Neither may be swept into a quality-issue total by group iteration."""
    assert issue_group("G6-CONFIRM-001") == "G6"
    assert issue_group("G7-UNCHANGED-001") == "G7"
    for group in ("G6", "G7"):
        assert group not in QUALITY_GROUPS


# ---------------------------------------------------------------------------
# G6-RESOLVE-001 / G6-CONFIRM-001 / G7-UNCHANGED-001 — the flag-derived codes
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
    assert codes == ["G1-NAME-013", "G6-CONFIRM-001", "G7-UNCHANGED-001"]


def test_several_flags_mapping_to_one_issue_raise_it_once():
    """Five flags name one thing to do — confirm a value the pipeline wrote."""
    codes = detect_issues(
        _record(),
        flag_codes=[
            "domain-unverified", "unverified-inference", "dept-via-lab",
            "dept-via-contact", "relocated-unverified",
        ],
    )
    assert codes == ["G6-CONFIRM-001"]


def test_a_code_reached_by_both_paths_is_reported_once():
    """G1-NAME-013, G3-CONTACT-007 and G3-CONTACT-010 each have a content
    detector *and* a flag that maps onto them. A record that trips both routes
    has one defect, not two, and ``detect_issues`` accumulates into a set."""
    rec = _record(**{
        "Name 2": "E004120188",                     # opaque code, by content
        "Contact": "Dr. Jane Smith; Prof. Bob Lee",  # two contacts, by content
        "Email": "a@acme.com; b@acme.com",           # two emails, by content
    })
    flagged = detect_issues(
        rec, flag_codes=["opaque-code", "multiple-contacts", "email-conflict"],
    )
    for code in ("G1-NAME-013", "G3-CONTACT-007", "G3-CONTACT-010"):
        assert flagged.count(code) == 1
        # ...and each route reaches it on its own.
        assert code in detect_issues(rec)
    assert set(detect_issues(
        _record(), flag_codes=["opaque-code", "multiple-contacts", "email-conflict"],
    )) == {"G1-NAME-013", "G3-CONTACT-007", "G3-CONTACT-010"}


def test_flag_derived_codes_absent_from_a_raw_input_audit():
    """The same rule G7-VERIFY-001 follows, for the same reason: these report
    what enrichment concluded, and a raw file carries no Flag Codes column.

    Only the ``raised="enriched"`` codes are checked. The others a flag can
    raise have a content path as well, and are *expected* on this record.
    """
    dirty = _record(**{
        "Name 1": "E004120188", "Name 2": "10901 Roosevelt Blvd N",
        "Contact": "Dr. Jane Smith; Prof. Bob Lee",
    })
    for code in ("G3-NAME-006", "G6-CONFIRM-001", "G7-UNCHANGED-001"):
        assert code not in detect_issues(dirty)
        assert code not in detect_issues(dirty, flag_codes=None)
    # An enriched record the pipeline flagged nothing on is not the same state
    # as a raw one, and also raises none of them.
    assert detect_issues(_record(), flag_codes=[]) == []


def test_an_unmapped_flag_code_raises_nothing():
    """The pipeline's vocabulary is larger than the reviewer-facing catalogue.
    `name3-not-demoted` is already reported as G4-NAME-015 from the record's
    own content; reporting it again from the flag would double-count it. A
    token this module has never been taught raises nothing either."""
    assert detect_issues(
        _record(), flag_codes=["name3-not-demoted", "not-a-code"],
    ) == []


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


def test_g7_unchanged_comes_from_the_token_in_flag_codes():
    """`low-confidence-unchanged` is a flag code the pipeline emits, and the
    mapping is the whole of how G7-UNCHANGED-001 is raised.

    It was withdrawn from the vocabulary once, and while it was gone `/issues`
    re-derived it from `Name 1 / Name 2 Provenance`. That path is deleted: the
    token is in `Flag Codes` again, and an audit reports what the file says.
    """
    assert FLAG_CODE_ISSUES["low-confidence-unchanged"] == "G7-UNCHANGED-001"
    assert "G7-UNCHANGED-001" in detect_issues(
        _record(), flag_codes=["low-confidence-unchanged"],
    )


def test_flag_derived_codes_are_not_in_the_reduction_metric():
    """Both are raised BY enrichment and reported separately; neither may move
    the before/after percentage."""
    for code in ("G6-CONFIRM-001", "G7-UNCHANGED-001"):
        assert code not in REDUCTION_METRIC_CODES
        assert ISSUE_CATALOGUE[code].remedy == "steward"
        assert ISSUE_CATALOGUE[code].raised == "enriched"


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
    """Catalogue v2's eleven, minus G2-VAL-003 and G2-VAL-006, and minus
    G2-NAME-012 — which became BOTH when its trigger grew an API-side arm
    (``dept_block.classify``) that no native DS rule expresses."""
    ds_only = sorted(
        code for code, e in ISSUE_CATALOGUE.items()
        if e.origin == "DS" and e.status == "live" and e.group in QUALITY_GROUPS
    )
    assert ds_only == [
        "G1-ADDR-001", "G2-VAL-001", "G2-VAL-002", "G2-VAL-004",
        "G2-VAL-007", "G2-VAL-008", "G4-ADDR-026", "G4-ADDR-027",
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
def test_g5_name_001_does_not_fire_for_an_abbreviated_legal_suffix(name1):
    """RE-PIN. This test used to assert the opposite.

    The inconsistency it was written for was real — "Co" was in the token set
    and "Corp"/"Inc" were not, so "Smith Co." fired and "Smith Corp." did not
    — but it was ended in the wrong direction. Firing on all four made the
    rule report the legal form itself, and G5 asks whether a name is in its
    official form, which "APEX CORP" and "LOCKHEED MARTIN CORP." are. A
    trailing legal suffix is now exempt from both arms; the four records still
    stand as the witnesses, with their answer reversed.
    """
    assert "G5-NAME-001" not in detect_issues(_record(**{"Name 1": name1}))


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
    """RE-PIN of the witness, not of the mechanism.

    Attribution by slot is unchanged. The record this was written on
    (40000012, "ADAMS AIR" / "HYDRAULICS INC") no longer demonstrates it,
    because its only mark was a trailing legal suffix and that is now exempt
    in every slot — ``test_g5_name_002_carries_the_same_suffix_exemption``
    pins the record's new answer. The witness here is S2 13128841, where
    Name 1 is clean and Name 2 carries "Lab", a live token in the unit set.
    """
    rec = _record(**{"Name 1": "ExxonMobil", "Name 2": "Park St. Lab"})
    issues = detect_issues(rec)
    assert "G5-NAME-002" in issues
    assert "G5-NAME-001" not in issues


# ---------------------------------------------------------------------------
# G5 — a legal-entity suffix is not a non-canonical name
# ---------------------------------------------------------------------------
# "Inc", "Corp", "Ltd" and "Co" were added to the lexicon to end an
# inconsistency ("Smith Co." fired, "Smith Corp." did not). Ending it that way
# made the rule fire on the legal form itself, which is the opposite of what
# G5 asks — and on the enriched side it fired hardest, because that is where
# GLEIF has written the registered name. A trailing legal suffix is now
# discarded from BOTH arms of ``_is_non_canonical_name``; a suffix that is not
# trailing, and every other abbreviation, is untouched.
#
# Position is the whole of it. "Inc" in "Value Plastics Inc dba Nordson
# Medical" is not the entity's legal form, it is a word in the middle of a
# name, so it still raises.


@pytest.mark.parametrize("name1", [
    "Pfizer Inc.",
    "Celgene Corp",
    "Veracyte, Inc.",
    "Dow Chemical Co",             # "Co" is exempt as a suffix; see below
    "Merck & Co., Inc.",           # two suffixes in the trailing run
    "ExxonMobil Research & Engineering Co.,",   # trailing punctuation
])
def test_g5_name_001_does_not_fire_on_a_trailing_legal_suffix(name1):
    assert "G5-NAME-001" not in detect_issues(_record(**{"Name 1": name1}))


@pytest.mark.parametrize("name1", [
    "Infineum USA L.P.",
    "CVG Ferrominera Orinoco, C.A.",
    "E.R. Squibb & Sons, L.L.C.",   # the L.L.C. half only; E.R. still fires
])
def test_g5_name_001_does_not_fire_on_a_trailing_dotted_legal_suffix(name1):
    """The dotted arm carries the same exemption as the token arm — a
    punctuated legal form ("L.L.C.", "C.A.") is a legal form, not an
    acronym in the trade name."""
    from enrichment.issue_detection import (
        _NONCANON_TOKENS_ORG, _is_non_canonical_name,
    )
    # the suffix alone must not be a mark of a non-official name
    suffix = name1.rsplit(",", 1)[-1].strip() if "," in name1 else name1.split()[-1]
    assert not _is_non_canonical_name(suffix, _NONCANON_TOKENS_ORG)


@pytest.mark.parametrize("name1", [
    "E.R. Squibb & Sons, L.L.C.",              # E.R. — dotted, mid-name
    "J.M. Smucker Company",                    # J.M. — the suffix is expanded
    "Harvard T.H. Chan School of Public Healt",  # T.H.
    "U.C.L.A",                                 # not a legal form at all
    "U.S.A.",
])
def test_g5_name_001_still_fires_on_a_dotted_acronym_in_the_trade_name(name1):
    """The exemption is positional, not a blanket amnesty for dotted text: a
    dotted acronym that is not a trailing legal suffix is still a mark of a
    non-official name."""
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": name1}))


@pytest.mark.parametrize("name1", [
    "Univ of Texas",
    "BRIGHAM & WOMENS HOSP",
    "MAYO CLINIC FLA",
    "Cardinal Research GRP",
    "UNI STUTTGART",
])
def test_g5_name_001_still_fires_on_a_name_abbreviation(name1):
    """Nothing outside the four legal-suffix tokens changed."""
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": name1}))


@pytest.mark.parametrize("name1", [
    "Value Plastics Inc dba Nordson Medical",
    "JAMES ELECTRONICS, LTD DBA JAMECO E",
    "E&S TECHNOLOGIES, INC. DBA HARRINGT",
])
def test_g5_name_001_still_fires_on_a_legal_suffix_that_is_not_trailing(name1):
    """All three are DBA constructions. The suffix belongs to the first name
    in the string and the string does not end on it, so it is not the legal
    form of the thing this record names."""
    assert "G5-NAME-001" in detect_issues(_record(**{"Name 1": name1}))


def test_g5_name_001_leaves_a_name_that_is_exempt_by_design_alone():
    """"Inst" is exempt in Name 1 by a deliberate decision
    (``_ORG_EXEMPT_TOKENS``) — it heads an organisation in its own right. This
    name therefore raises nothing before the suffix exemption and nothing
    after it, and the fixture exists to pin that the change did not disturb
    it."""
    assert "G5-NAME-001" not in detect_issues(
        _record(**{"Name 1": "Dallas County Inst Of"})
    )


def test_g5_name_002_carries_the_same_suffix_exemption():
    """The unit-slot arm has the same defect for the same reason, so it takes
    the same fix; ``HYDRAULICS INC`` is the attribution fixture's name and no
    longer raises on the suffix alone."""
    rec = _record(**{"Name 1": "ADAMS AIR", "Name 2": "HYDRAULICS INC"})
    assert "G5-NAME-002" not in detect_issues(rec)


def test_g5_name_002_still_fires_on_a_mid_name_suffix_in_a_unit_slot():
    """"Div" is an accepted unit form and is exempt; "Corp" is a legal suffix
    but sits mid-name, so the slot still raises."""
    rec = _record(
        **{"Name 1": "Acme Corporation",
           "Name 2": "Div of Panasonic Corp of North America"}
    )
    assert "G5-NAME-002" in detect_issues(rec)


def test_g5_name_002_known_gap_care_of_payload_in_a_name_slot():
    """KNOWN AND FLAGGED, not a special case. "C/o Glover Systems, Inc." is a
    Care Of payload sitting in a name slot; its only non-canonical mark was
    the trailing "Inc.", so the suffix exemption clears -002 for it. The
    misplacement is G1-CROSS-* territory and is reported there — G5 was never
    the code that made this row visible."""
    rec = _record(
        **{"Name 1": "Acme Corporation", "Name 2": "C/o Glover Systems, Inc."}
    )
    assert "G5-NAME-002" not in detect_issues(rec)


# The thirteen S2 rows where enrichment wrote the registered form and the
# detector scored it as LESS canonical than the input — G5-NAME-001 raised on
# the enriched side and not on the input side. Every one of them is a trailing
# legal suffix, and every one clears.
S2_POST_ONLY_ENRICHED_NAMES = [
    ("13011411", "ExxonMobil Research & Engineering Co.,"),
    ("13333439", "McKesson Medical-Surgical Inc."),
    ("13333855", "McKesson Medical-Surgical Inc."),
    ("13333415", "McKesson Medical-Surgical Inc."),
    ("13338508", "McKesson Medical-Surgical Inc."),
    ("13348125", "Veracyte, Inc."),
    ("13348232", "Merck & Co., Inc."),
    ("13017121", "San Francisco Bay Aggregates, Inc."),
    ("13017225", "Marine Reef International, Inc."),
    ("13162651", "CVG Ferrominera Orinoco, C.A."),
    ("13336901", "Charles River Laboratories, Inc."),
    ("13343511", "Varian Medical Systems, Inc."),
    ("13035575", "Bayer Healthcare Pharmaceuticals Inc."),
]


@pytest.mark.parametrize(
    "customer,name1", S2_POST_ONLY_ENRICHED_NAMES,
    ids=[c for c, _ in S2_POST_ONLY_ENRICHED_NAMES],
)
def test_g5_name_001_clears_the_s2_post_only_raisers(customer, name1):
    assert "G5-NAME-001" not in detect_issues(_record(**{"Name 1": name1}))


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


# ---------------------------------------------------------------------------
# G1-ADDR-003 and the named-building rule.
#
# The named-building matcher is guarded (`_named_building_prefix_ok`), and the
# DETECTOR must consult the same guard rather than a bare pattern search — a
# value the extractor refuses to move ("Hall St", "Main Hall") must not start
# reporting a sub-location that nothing will ever extract.
#
# Street 1 is detect-only: the extractor never moves a named building out of
# Street 1, but the detector still reports it so a steward sees it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("street1", [
    "Hall St",                      # street-type word, not a building
    "Main Hall",                    # bare generic prefix
    "Dept of Chemistry Building",   # department shape
])
def test_named_building_guard_rejects_do_not_raise_g1_addr_003(street1):
    assert "G1-ADDR-003" not in detect_issues(_record(**{"Street 1": street1}))


def test_named_building_in_street_1_is_detected_but_not_extracted():
    """Street 1 is detect-only. The code is raised; the extractor leaves the
    value in place and Building stays blank."""
    import asyncio

    from enrichment.address_processing import process_address

    assert "G1-ADDR-003" in detect_issues(
        _record(**{"Street 1": "Heroy Bldg/Rm 450"})
    )

    res = asyncio.run(process_address(
        record_id="s1", name1="Acme Corp", name2=None, name3=None,
        street="Heroy Bldg/Rm 450", street_2=None, street_3=None,
        city="Tampa", state="FL", zip_code="33620", country="US",
        po_box=None, care_of_enriched=None, llm_client=None,
    ))
    assert res.building is None


def test_g1_addr_004_still_fires_for_a_street_only_po_box():
    """Fixture (b) of the PO-Box carry-through change. G1-ADDR-004 is a
    DETECTOR code (a PO Box pattern inside a Street field), not an
    address-stage one, so it is asserted here rather than alongside the
    address fixtures. Carrying the dedicated column to the output does not
    touch it."""
    codes = detect_issues(_record(**{
        "Street 1": "100 Main St", "Street 2": "PO Box 2000",
    }))
    assert "G1-ADDR-004" in codes
    assert "G3-ADDR-005" not in codes


def test_g3_addr_005_fires_when_both_sources_carry_a_po_box():
    codes = detect_issues(_record(**{
        "Street 1": "100 Main St", "Street 2": "PO Box 2000", "PO Box": "750162",
    }))
    assert "G3-ADDR-005" in codes
