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
        "_dba_values": {},
        "flag_for_review": False, "flag_reason": None,
    }
    base.update(overrides)
    return base


class TestSearchTerm1:
    @pytest.mark.parametrize("result,expected", [
        (_st(_ror_acronym="NIST"), "NIST"),
        (_st(domain="massgeneralbrigham.org", name1_enriched="Mass General Brigham"),
         "MASSGENERALBRIGHAM"),
        (_st(name1_enriched="Verdox, Inc."), "VERDOX"),
        (_st(name1_enriched="Silverline Biotech"), "SILVERLINE BIOTECH"),
        (_st(name1_enriched="Precision Diagnostics"), "PRECISION DIAGNOSTICS"),
        # A person lifted out of Name 1 leaves the enriched slot blank, so
        # the output ships no institution and no handle for one.
        (_st(name1_original="John F Florek"), None),
        (_st(name1_original="Martin Gartmann"), None),
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
                name1_original="John F Florek")
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


class TestDerivedAfterEnrichmentOnly:
    """Both terms come from post-enrichment values; the pre-enrichment SAP
    Search Term columns are never consulted."""

    def test_st1_ignores_input_sap_term_in_favour_of_the_enriched_name(self):
        # The input carried a stale handle for a name enrichment has since
        # corrected. The corrected name wins.
        r = _st(name1_enriched="Riverside Diagnostics",
                name1_original="RIVRSIDE DIAG",
                _search_term_1_original="RVSD")
        assert derive_search_terms(r)[0] == "RIVERSIDE DIAGNOSTICS"

    def test_st1_prefers_enriched_name_over_original(self):
        r = _st(name1_enriched="Fine Organics",
                name1_original="Fine Organics Limited")
        assert derive_search_terms(r)[0] == "FINE ORGANICS"

    def test_st1_is_null_when_no_institution_survived_enrichment(self):
        """The reported case: "ATTN CHARLES FARBER / MIT" — preprocessing
        moved the person to Contact, no institution reached Name 1, and the
        raw input string still shipped as Search Term 1. Name 1 is null in
        the response, so Search Term 1 must be too."""
        r = _st(name1_enriched=None, name1_original="ATTN CHARLES FARBER / MIT")
        assert derive_search_terms(r)[0] is None

    def test_st2_ignores_input_sap_term(self):
        r = _st(name2_enriched="Department of Chemistry",
                _search_term_1_original="CHEM DEPT")
        assert derive_search_terms(r)[1] == "CHEMISTRY"


class TestName1DerivedHandle:
    """Rule 3: no acronym and no domain → a handle derived from the name that
    still reads as something you would type into a search box."""

    @pytest.mark.parametrize("name1,expected", [
        # The whole name is kept when it fits the 32-char field — dropping the
        # connectives would make the handle unsearchable.
        ("University of Florida", "UNIVERSITY OF FLORIDA"),
        ("Applied Thin Films, Inc.", "APPLIED THIN FILMS"),
        ("Verdox, Inc.", "VERDOX"),
        ("Mussel Polymers, Inc.", "MUSSEL POLYMERS"),
        ("Atlantic Testing Labs", "ATLANTIC TESTING LABS"),
        ("NovaBio", "NOVABIO"),
        # Over the field width → stopwords dropped, filled to the boundary.
        ("Massachusetts Institute of Technology", "MASSACHUSETTS INSTITUTE"),
        ("National Institute of Standards and Technology",
         "NATIONAL INSTITUTE STANDARDS"),
    ])
    def test_handle(self, name1, expected):
        assert derive_search_terms(_st(name1_enriched=name1))[0] == expected

    def test_person_still_emits_nothing(self):
        # UC 7 moved the person to Contact; nothing reached the Name 1 output.
        r = _st(name1_original="Kittipan Siwawannapong")
        assert derive_search_terms(r)[0] is None


class TestName2StructuralWordsDropped:
    """search_term_2 names the unit, not the org-chart level it sits at."""

    @pytest.mark.parametrize("name2,expected", [
        ("Chemistry Dept", "CHEMISTRY"),
        ("Chemistry Department", "CHEMISTRY"),
        ("Department of Chemistry", "CHEMISTRY"),
        ("Div of Analytical Sciences", "ANALYTICAL SCIENCES"),
        ("Analytical Sciences Division", "ANALYTICAL SCIENCES"),
        # Mid-string structural words — clean_name2_phrase only strips edges,
        # so these are what the token-level pass exists for.
        ("Chemistry Dept Analytical Div", "CHEMISTRY ANALYTICAL"),
        ("Materials Science Lab Group", "MATERIALS SCIENCE"),
        ("Neuroscience Research Unit", "NEUROSCIENCE RESEARCH"),
        ("Molecular Biology Section", "MOLECULAR BIOLOGY"),
        ("Cancer Research Center", "CANCER RESEARCH"),
        ("School of Public Health", "PUBLIC HEALTH"),
    ])
    def test_strips(self, name2, expected):
        assert derive_search_terms(_st(name2_enriched=name2))[1] == expected

    @pytest.mark.parametrize("name2", [
        "Laboratory", "Division", "Department", "Lab", "The Department",
    ])
    def test_pure_structural_phrase_yields_no_handle(self, name2):
        # Nothing meaningful survives, so the keyword itself is not shipped.
        assert derive_search_terms(_st(name2_enriched=name2))[1] is None

    def test_pure_structural_phrase_falls_through_to_department_domain(self):
        r = _st(name2_enriched="Laboratory",
                department_domain="chemistry.ufl.edu", domain="ufl.edu")
        assert derive_search_terms(r)[1] == "CHEMISTRY"

    def test_generic_department_domain_host_is_not_a_handle(self):
        r = _st(name2_enriched=None,
                department_domain="dept.example.edu", domain="example.edu")
        assert derive_search_terms(r)[1] is None
