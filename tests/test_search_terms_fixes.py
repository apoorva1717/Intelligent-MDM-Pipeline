"""Deterministic acceptance tests for the search-term rewrite (sections 1-4)
and ROR acronym currency selection (section 2). Pure functions, fixed inputs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.search_terms import derive_search_terms
from enrichment.tier1_ror import _extract_org_fields
from utils.text_utils import acronym_matches_name, is_admin_unit, name_initials


def _st(**overrides):
    base = {
        "name1_enriched": None, "name1_original": None,
        "name2_enriched": None, "name2_original": None,
        "domain": None, "department_domain": None,
        "_ror_acronym": None, "_search_term_1_original": None,
        "_name1_was_person": False, "_dba_values": {},
        "flag_for_review": False, "flag_reason": None,
    }
    base.update(overrides)
    return base


class TestSearchTerm1:
    @pytest.mark.parametrize("result,expected", [
        (_st(_ror_acronym="NIST"), "NIST"),
        (_st(domain="massgeneralbrigham.org", name1_enriched="Mass General Brigham"),
         "MASSGENERALBRIGHAM"),
        (_st(name1_original="Verdox, Inc."), "VERDOX"),
        (_st(name1_original="Silverline Biotech"), "SILVERLINE BIOTECH"),
        (_st(name1_original="Precision Diagnostics"), "PRECISION DIAGNOSTICS"),
        (_st(name1_original="John F Florek", _name1_was_person=True), None),
        (_st(name1_original="Martin Gartmann", _name1_was_person=True), None),
        (_st(name1_original=None, _search_term_1_original="KS"), None),
        (_st(domain="uni-tuebingen.de", name1_enriched="University of Tübingen"),
         "UNI-TUEBINGEN"),
        (_st(_ror_acronym="UF", name1_enriched="University of Florida"), "UF"),
    ])
    def test_st1(self, result, expected):
        assert derive_search_terms(result)[0] == expected


class TestSearchTerm2:
    @pytest.mark.parametrize("result,expected", [
        (_st(name2_enriched="Accounts Payable"), "ADMIN"),
        (_st(name2_enriched="Office of Research"), "RESEARCH"),
        (_st(name2_enriched="Department of Chemistry",
             department_domain="chem.ufl.edu", domain="ufl.edu"), "CHEMISTRY"),
        (_st(name2_enriched="Electrical Engineering and Computer Science",
             department_domain="eecs.mit.edu", domain="mit.edu"), "EECS"),
        (_st(name2_enriched="Department of Physics",
             department_domain="physics.ufl.edu", domain="ufl.edu"), "PHYSICS"),
        (_st(name2_enriched="Department of Biology",
             department_domain="https://ufl.edu/departments/biology",
             domain="ufl.edu"), "BIOLOGY"),
        (_st(name2_enriched="Department of Neuroscience"), "NEUROSCIENCE"),
        (_st(name2_enriched="Division of MPP"), "MPP"),
        (_st(name2_enriched="Earth and Planetary Sciences"),
         "EARTH PLANETARY SCIENCES"),
    ])
    def test_st2(self, result, expected):
        assert derive_search_terms(result)[1] == expected

    def test_field_swap_nulls_search_term_2(self):
        """An institution in the Name 2 slot yields no unit handle.

        Search-term derivation no longer raises a review flag of its own —
        ``enrichment.flags`` is the single flag authority (Fix 8).
        """
        r = _st(name2_enriched="Tufts University",
                name1_original="John F Florek", _name1_was_person=True)
        st1, st2 = derive_search_terms(r)
        assert st2 is None
        assert r["flag_for_review"] is False

    def test_dba_name2_nulled(self):
        r = _st(name2_enriched="Coastal Marine",
                _dba_values={"name2": "DBA Coastal Marine"})
        assert derive_search_terms(r)[1] is None


class TestTerminalNormalisation:
    def test_uppercase_trimmed_and_capped(self):
        r = _st(name2_enriched="  organic process chemistry and analytical "
                               "technology development  ")
        _st1, st2 = derive_search_terms(r)
        assert st2 == "ORGANIC PROCESS CHEMISTRY"
        assert st2 == st2.strip() and st2 == st2.upper() and len(st2) <= 32

    def test_accepted_imperfection_sustainable(self):
        r = _st(name2_enriched="Institute of Sustainable and Environmental Chemistry")
        assert derive_search_terms(r)[1] == "SUSTAINABLE ENVIRONMENTAL"


class TestRorAcronymCurrency:
    def test_name_initials(self):
        assert name_initials("National Institute of Standards and Technology") == "NIST"
        assert name_initials("University of Florida") == "UF"

    def test_acronym_matches(self):
        assert acronym_matches_name("NIST", "National Institute of Standards and Technology")
        assert not acronym_matches_name("NBS", "National Institute of Standards and Technology")
        assert acronym_matches_name("UF", "University of Florida")
        assert not acronym_matches_name("PHS", "Mass General Brigham")

    def test_extract_org_fields_picks_current_acronym(self):
        org = {
            "id": "https://ror.org/x",
            "names": [
                {"types": ["ror_display"], "value": "National Institute of Standards and Technology"},
                {"types": ["acronym"], "value": "NBS"},
                {"types": ["acronym"], "value": "NIST"},
            ],
            "types": ["government"],
            "locations": [],
        }
        assert _extract_org_fields(org)["acronym"] == "NIST"

    def test_extract_org_fields_no_match_returns_none(self):
        org = {
            "id": "https://ror.org/y",
            "names": [
                {"types": ["ror_display"], "value": "Mass General Brigham"},
                {"types": ["acronym"], "value": "PHS"},
            ],
            "types": ["healthcare"],
            "locations": [],
        }
        assert _extract_org_fields(org)["acronym"] is None


class TestIsAdminUnit:
    @pytest.mark.parametrize("text,expected", [
        ("Accounts Payable", True), ("Accounts Receivable", True),
        ("Office of Finance", True), ("Billing", True), ("Procurement", True),
        ("Treasury", True), ("Shared Services", True), ("AP", True),
        ("Office of Research", False), ("Department of Chemistry", False),
    ])
    def test_admin(self, text, expected):
        assert is_admin_unit(text) is expected
