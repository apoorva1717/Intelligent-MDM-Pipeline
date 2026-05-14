"""UC 15 — c/o + ATTN extraction from Name 2 (5-case classifier).

Bernd-approved test data covering:
  A — Person (with or without title; (guest) noise stripped)
  B — Company (legal suffix)
  C — Department / functional unit (keyword or trailing "Services")
  D — Email (with or without prefix)
  E — Job title / unclassified fallback
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record


def _run(name1: str, name2: str | None, llm_verdicts: dict[str, str] | None = None):
    return preprocess_record(
        name1=name1,
        name2=name2,
        name3=None,
        contact=None,
        email=None,
        street1=None,
        street2=None,
        street3=None,
        llm_person_verdicts=llm_verdicts or {},
    )


class TestCaseAPerson:
    def test_title_prefix(self):
        res = _run("BioMed Solutions Inc.", "c/o Dr. Steven Park")
        assert res.care_of == "Dr. Steven Park"
        assert res.contact == "Steven Park"
        assert res.name2 is None
        assert res.trigger_dept_lookup is True

    def test_plain_name_with_guest_noise(self):
        res = _run("Mayo Foundation", "C/O Ethan Payne (guest)")
        assert res.care_of == "Ethan Payne"
        assert res.contact == "Ethan Payne"
        assert res.name2 is None
        assert res.trigger_dept_lookup is True

    def test_plain_name_attn_prefix(self):
        res = _run("University of Florida", "Attn: Victoria Babinetz")
        assert res.care_of == "Victoria Babinetz"
        assert res.contact == "Victoria Babinetz"
        assert res.name2 is None
        assert res.trigger_dept_lookup is True


class TestCaseBCompany:
    def test_llc_suffix(self):
        res = _run("Fisher Scientific Co. LLC", "C/O Act Transport SciMed Global LLC")
        assert res.care_of == "Act Transport SciMed Global LLC"
        assert res.contact is None
        assert res.name2 is None
        assert res.trigger_dept_lookup is False

    def test_inc_suffix(self):
        res = _run("C.V.G. International America, Inc.", "c/o Clover Systems, Inc.")
        assert res.care_of == "Clover Systems, Inc."
        assert res.contact is None
        assert res.name2 is None


class TestCaseCDepartment:
    def test_department_of(self):
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


class TestCaseDEmail:
    def test_with_attn_prefix(self):
        res = _run("University of Florida", "Attn: UFL.invoices@edmgroup.com")
        assert res.email == "UFL.invoices@edmgroup.com"
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 is None

    def test_without_prefix(self):
        res = _run("NCH Healthcare", "Apinvoices@nchmd.org")
        assert res.email == "Apinvoices@nchmd.org"
        assert res.care_of is None
        assert res.contact is None
        assert res.name2 is None


class TestCaseEFallback:
    def test_job_title_no_prefix(self):
        res = _run("EMSL Analytical, Inc.", "Asbestos Lab Manager")
        assert res.care_of == "Asbestos Lab Manager"
        assert res.contact is None
        assert res.name2 is None
        assert res.trigger_dept_lookup is False
