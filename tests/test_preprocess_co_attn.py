"""UC 15 — c/o and ATTN extraction from Name 2 (5-case classifier).

Covers the Bernd-approved test cases: Case A (person), B (company),
C (department), D (email), E (job title / fallback).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import (
    find_suspicious_plain_names,
    preprocess_record,
)


def _run(name1, name2, *, verdicts=None, contact=None, email=None):
    return preprocess_record(
        name1=name1,
        name2=name2,
        name3=None,
        contact=contact,
        email=email,
        street1=None,
        street2=None,
        street3=None,
        llm_person_verdicts=verdicts or {},
    )


# ── Case A — Person ──────────────────────────────────────────────────────────

class TestCaseAPerson:
    def test_title_prefixed_person(self):
        res = _run("BioMed Solutions Inc.", "c/o Dr. Steven Park")
        assert res.care_of == "Dr. Steven Park"
        assert res.contact == "Steven Park"
        assert res.name2 is None
        assert res.trigger_dept_lookup is True

    def test_person_with_guest_noise(self):
        verdicts = {"ethan payne": "person"}
        res = _run("Mayo Foundation", "C/O Ethan Payne (guest)", verdicts=verdicts)
        assert res.care_of == "Ethan Payne"
        assert res.contact == "Ethan Payne"
        assert res.name2 is None
        assert res.trigger_dept_lookup is True

    def test_attn_prefixed_plain_name(self):
        verdicts = {"victoria babinetz": "person"}
        res = _run("University of Florida", "Attn: Victoria Babinetz", verdicts=verdicts)
        assert res.care_of == "Victoria Babinetz"
        assert res.contact == "Victoria Babinetz"
        assert res.name2 is None
        assert res.trigger_dept_lookup is True

    def test_slash_separated_person_flagged(self):
        verdicts = {"yanping zhang": "person"}
        res = _run(
            "BioCorp",
            "c/o Yanping Zhang/Synkine NanoString P",
            verdicts=verdicts,
        )
        assert res.care_of == "Yanping Zhang"
        assert res.contact == "Yanping Zhang"
        assert res.name2 is None
        assert any("slash" in f for f in res.flags)


# ── Case B — Company ─────────────────────────────────────────────────────────

class TestCaseBCompany:
    def test_company_with_llc(self):
        res = _run("Fisher Scientific Co. LLC", "C/O Act Transport SciMed Global LLC")
        assert res.care_of == "Act Transport SciMed Global LLC"
        assert res.contact is None
        assert res.name2 is None
        assert res.trigger_dept_lookup is False

    def test_company_with_inc(self):
        res = _run("C.V.G. International America, Inc.", "c/o Clover Systems, Inc.")
        assert res.care_of == "Clover Systems, Inc."
        assert res.contact is None
        assert res.name2 is None


# ── Case C — Department / Functional Unit ────────────────────────────────────

class TestCaseCDepartment:
    def test_department_of_keyword(self):
        res = _run("Stanford University", "c/o Department of Chemistry")
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 == "Department of Chemistry"

    def test_accounts_payable(self):
        res = _run("Florida Institute of Technology", "Attn: Accounts Payable")
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 == "Accounts Payable"

    def test_trailing_services(self):
        res = _run("Florida Power & Light Co.", "c/o: Fabrication Outage Services")
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 == "Fabrication Outage Services"


# ── Case D — Email ───────────────────────────────────────────────────────────

class TestCaseDEmail:
    def test_prefixed_email(self):
        res = _run("University of Florida", "Attn: UFL.invoices@edmgroup.com")
        assert res.email == "UFL.invoices@edmgroup.com"
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 is None

    def test_unprefixed_email(self):
        res = _run("NCH Healthcare", "Apinvoices@nchmd.org")
        assert res.email == "Apinvoices@nchmd.org"
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 is None


# ── Case E — Job title / Fallback ────────────────────────────────────────────

class TestCaseEFallback:
    def test_unprefixed_job_title(self):
        res = _run("EMSL Analytical, Inc.", "Asbestos Lab Manager")
        assert res.care_of == "Asbestos Lab Manager"
        assert res.contact is None
        assert res.name2 is None


# ── Misc edge cases ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_name2(self):
        res = _run("Some Company", None)
        assert res.care_of is None
        assert res.name2 is None

    def test_prefix_only_clears(self):
        res = _run("Some Company", "Attn:")
        assert res.name2 is None

    def test_no_prefix_unrelated_name2_passes_through(self):
        # Generic name2 with no prefix and no job-title pattern must
        # NOT be moved — downstream tiers (ROR child match, Tier 2/3)
        # treat it as a sub-org / department.
        res = _run("MIT", "Department of Chemistry")
        assert res.name2 == "Department of Chemistry"
        assert res.care_of is None

    def test_email_does_not_overwrite_existing(self):
        res = _run(
            "Some Org",
            "Attn: foo@bar.com",
            email="existing@example.com",
        )
        assert res.email == "existing@example.com"
        assert "email-conflict" in res.flags


# ── UC 8 — email stripped from name3 ─────────────────────────────────────────

class TestUC8EmailStrip:
    def test_email_removed_from_name3(self):
        """An email in Name 3 is captured to the email field and removed
        from Name 3 so the cleaned value isn't polluted."""
        from enrichment.preprocess import preprocess_record

        res = preprocess_record(
            "Acme Corp", "Department of Chemistry",
            "Research Lab jsmith@acme.com",
            None, None, None, None, None,
        )
        assert res.email == "jsmith@acme.com"
        assert res.name3 == "Research Lab"

    def test_name3_only_email_becomes_none(self):
        from enrichment.preprocess import preprocess_record

        res = preprocess_record(
            "Acme Corp", "Dept", "jsmith@acme.com",
            None, None, None, None, None,
        )
        assert res.email == "jsmith@acme.com"
        assert res.name3 is None

    def test_conflicting_email_kept_for_review(self):
        """A different email already on file → source field is left intact
        and a conflict is flagged (not silently stripped)."""
        from enrichment.preprocess import preprocess_record

        res = preprocess_record(
            "Acme Corp", "Department of Chemistry",
            "Research Lab foo@x.com",
            None, "existing@y.com", None, None, None,
        )
        assert res.email == "existing@y.com"
        assert "foo@x.com" in (res.name3 or "")
        assert "email-conflict" in res.flags


# ── UC 13 — Name 3 residual junk cleanup ─────────────────────────────────────

class TestUC13Name3Junk:
    @staticmethod
    def _name3(value):
        from enrichment.preprocess import preprocess_record
        return preprocess_record(
            "Acme Corp", "Department of Chemistry", value,
            None, None, None, None, None,
        ).name3

    def test_url_stripped(self):
        assert self._name3("Research Lab www.acme.com") == "Research Lab"

    def test_phone_stripped(self):
        assert self._name3("Logistics Tel: 727-555-1234") == "Logistics"

    def test_opaque_code_stripped(self):
        assert self._name3("Billing NT30 800000070") == "Billing"

    def test_legit_department_survives(self):
        assert self._name3("Center for Advanced Materials") == (
            "Center for Advanced Materials"
        )

    def test_duplicate_of_name1_cleared(self):
        from enrichment.preprocess import preprocess_record
        res = preprocess_record(
            "Acme Corporation", "Department of Chemistry", "Acme Corporation",
            None, None, None, None, None,
        )
        assert res.name3 is None


# ── UC 13 — Street junk cleanup ──────────────────────────────────────────────

class TestUC13StreetJunk:
    @staticmethod
    def _street1(value):
        from enrichment.preprocess import preprocess_record
        return preprocess_record(
            "Acme Corp", "Dept", None, None, None, value, None, None,
        ).street1

    def test_street_number_survives(self):
        assert self._street1("10901 Roosevelt Blvd N") == "10901 Roosevelt Blvd N"

    def test_url_stripped_number_kept(self):
        assert self._street1("10901 Roosevelt Blvd www.acme.com") == (
            "10901 Roosevelt Blvd"
        )

    def test_phone_stripped(self):
        assert self._street1("Dock B Tel: 727-555-1234") == "Dock B"

    def test_email_moved_to_email_field(self):
        from enrichment.preprocess import preprocess_record
        res = preprocess_record(
            "Acme Corp", "Dept", None, None, None,
            "Receiving dept ap@acme.com", None, None,
        )
        assert res.email == "ap@acme.com"
        assert res.street1 == "Receiving dept"

    def test_multiple_address_numbers_survive(self):
        assert self._street1("123 Main St Suite 456") == "123 Main St Suite 456"


# ── UC 13 — Person in street → Contact ───────────────────────────────────────

class TestUC13StreetPerson:
    @staticmethod
    def _run(street2):
        from enrichment.preprocess import preprocess_record
        return preprocess_record(
            "Acme Corp", "Dept", None, None, None, None, street2, None,
        )

    def test_person_with_role_moved_to_contact(self):
        res = self._run("Dr Sarah Johnson - Lab Director")
        assert res.contact == "Dr Sarah Johnson"  # role suffix dropped
        assert res.street2 is None

    def test_person_only_moved_to_contact(self):
        res = self._run("Prof. Alan Turing")
        assert res.contact == "Prof. Alan Turing"
        assert res.street2 is None

    def test_person_named_street_kept(self):
        # A real street named after a person must stay in the street.
        res = self._run("Dr Martin Luther King Jr Blvd")
        assert res.contact is None
        assert res.street2 == "Dr Martin Luther King Jr Blvd"

    def test_person_named_avenue_kept(self):
        res = self._run("Dr Sarah Johnson Ave")
        assert res.contact is None
        assert res.street2 == "Dr Sarah Johnson Ave"

    def test_conflict_keeps_street_and_flags(self):
        from enrichment.preprocess import preprocess_record
        res = preprocess_record(
            "Acme Corp", "Dept", None,
            "Existing Person", None, None, "Dr Jane Doe", None,
        )
        assert res.contact == "Existing Person"
        assert res.street2 == "Dr Jane Doe"
        assert "contact-conflict" in res.flags


# ── UC 16 — Institution + embedded department split in Name 1 ─────────────────

class TestUC16DepartmentSplit:
    @staticmethod
    def _run(name1, name2=None, name3=None, name4=None):
        from enrichment.preprocess import preprocess_record
        return preprocess_record(
            name1, name2, name3, None, None, None, None, None, name4=name4,
        )

    def test_institution_department_split(self):
        res = self._run("University of Miami Department of Chemistry")
        assert res.name1 == "University of Miami"
        assert res.name2 == "Department of Chemistry"

    def test_division_split(self):
        res = self._run("Bruker Life Sciences, Inc. Division of Spectroscopy")
        assert res.name1 == "Bruker Life Sciences, Inc."
        assert res.name2 == "Division of Spectroscopy"

    def test_government_agency_not_split(self):
        # No institution keyword in the prefix → leave intact.
        res = self._run("United States Department of Agriculture")
        assert res.name1 == "United States Department of Agriculture"
        assert res.name2 is None

    def test_state_agency_not_split(self):
        res = self._run("Florida Department of Health")
        assert res.name1 == "Florida Department of Health"
        assert res.name2 is None

    def test_split_goes_to_name3_when_name2_full(self):
        res = self._run(
            "University of Miami Department of Chemistry", "Existing Dept",
        )
        # name2 occupied → department goes to the next free slot (name3).
        assert res.name1 == "University of Miami"
        assert res.name2 == "Existing Dept"
        assert res.name3 == "Department of Chemistry"

    def test_split_goes_to_name4_when_name2_and_name3_full(self):
        res = self._run(
            "University of Miami Department of Chemistry",
            "Existing Dept", "Another Dept",
        )
        # name2 and name3 occupied → department goes to name4 (the next
        # free dept slot, which now follows the same rules as name2/name3).
        assert res.name1 == "University of Miami"
        assert res.name2 == "Existing Dept"
        assert res.name3 == "Another Dept"
        assert res.name4 == "Department of Chemistry"

    def test_no_split_when_all_name_slots_full(self):
        res = self._run(
            "University of Miami Department of Chemistry",
            "Existing Dept", "Another Dept", "Third Dept",
        )
        # No free slot (name2/name3/name4 all full) → name1 left intact,
        # flagged for review.
        assert res.name1 == "University of Miami Department of Chemistry"
        assert "name1-embedded-department" in res.flags

    def test_unit_substring_not_extracted_as_address(self):
        # "Unit" inside "United" must NOT be ripped out as a sub-location.
        # ("Corporation" → "Corp" is the separate UC 17 legal-suffix
        # normalisation; the point here is that "United" stays intact.)
        res = self._run("United Technologies Corporation")
        assert res.name1 == "United Technologies Corp"

    def test_person_with_existing_contact_flagged(self):
        res = _run(
            "Some Org",
            "c/o Dr. Steven Park",
            contact="Alice Existing",
        )
        # care_of still populated, but contact-conflict raised
        assert res.care_of == "Dr. Steven Park"
        assert res.contact == "Alice Existing"
        assert "contact-conflict" in res.flags


# ── find_suspicious_plain_names integration ──────────────────────────────────

class TestSuspiciousPlainNameCollection:
    def test_attn_payload_surfaced_for_llm(self):
        out = find_suspicious_plain_names(
            "University of Florida", "Attn: Victoria Babinetz", None,
        )
        assert "Victoria Babinetz" in out

    def test_co_payload_with_guest_noise(self):
        out = find_suspicious_plain_names(
            "Mayo Foundation", "C/O Ethan Payne (guest)", None,
        )
        assert "Ethan Payne" in out

    def test_slash_separated_leading_name_surfaced(self):
        out = find_suspicious_plain_names(
            "BioCorp", "c/o Yanping Zhang/Synkine NanoString P", None,
        )
        assert "Yanping Zhang" in out

    def test_department_payload_not_surfaced(self):
        out = find_suspicious_plain_names(
            "Stanford University", "c/o Department of Chemistry", None,
        )
        # "Department of Chemistry" contains org signals — not a person.
        assert out == []


# ── Parametrised Bernd test matrix ───────────────────────────────────────────

_BERND_CASES = [
    # Case A — Person
    ("BioMed Solutions Inc.", "c/o Dr. Steven Park",
     {"care_of": "Dr. Steven Park", "contact": "Steven Park", "name2": None,
      "trigger_dept_lookup": True, "email": None}, {}),
    ("Mayo Foundation", "C/O Ethan Payne (guest)",
     {"care_of": "Ethan Payne", "contact": "Ethan Payne", "name2": None,
      "trigger_dept_lookup": True, "email": None},
     {"ethan payne": "person"}),
    ("University of Florida", "Attn: Victoria Babinetz",
     {"care_of": "Victoria Babinetz", "contact": "Victoria Babinetz", "name2": None,
      "trigger_dept_lookup": True, "email": None},
     {"victoria babinetz": "person"}),
    # Case B — Company
    ("Fisher Scientific Co. LLC", "C/O Act Transport SciMed Global LLC",
     {"care_of": "Act Transport SciMed Global LLC", "contact": None,
      "name2": None, "trigger_dept_lookup": False, "email": None}, {}),
    ("C.V.G. International America, Inc.", "c/o Clover Systems, Inc.",
     {"care_of": "Clover Systems, Inc.", "contact": None, "name2": None,
      "trigger_dept_lookup": False, "email": None}, {}),
    # Case C — Department
    ("Stanford University", "c/o Department of Chemistry",
     {"care_of": None, "contact": None, "name2": "Department of Chemistry",
      "trigger_dept_lookup": False, "email": None}, {}),
    ("Florida Institute of Technology", "Attn: Accounts Payable",
     {"care_of": None, "contact": None, "name2": "Accounts Payable",
      "trigger_dept_lookup": False, "email": None}, {}),
    ("Florida Power & Light Co.", "c/o: Fabrication Outage Services",
     {"care_of": None, "contact": None, "name2": "Fabrication Outage Services",
      "trigger_dept_lookup": False, "email": None}, {}),
    # Case D — Email
    ("University of Florida", "Attn: UFL.invoices@edmgroup.com",
     {"care_of": None, "contact": None, "name2": None,
      "trigger_dept_lookup": False, "email": "UFL.invoices@edmgroup.com"}, {}),
    ("NCH Healthcare", "Apinvoices@nchmd.org",
     {"care_of": None, "contact": None, "name2": None,
      "trigger_dept_lookup": False, "email": "Apinvoices@nchmd.org"}, {}),
    # Case E — Job title / fallback
    ("EMSL Analytical, Inc.", "Asbestos Lab Manager",
     {"care_of": "Asbestos Lab Manager", "contact": None, "name2": None,
      "trigger_dept_lookup": False, "email": None}, {}),
]


@pytest.mark.parametrize("name1,name2,expect,verdicts", _BERND_CASES)
def test_bernd_matrix(name1, name2, expect, verdicts):
    res = _run(name1, name2, verdicts=verdicts)
    for field, expected in expect.items():
        assert getattr(res, field) == expected, (
            f"name2={name2!r}: field {field} expected {expected!r}, "
            f"got {getattr(res, field)!r}"
        )
