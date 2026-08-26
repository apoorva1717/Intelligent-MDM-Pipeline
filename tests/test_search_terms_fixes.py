"""Deterministic acceptance tests for the search-term rewrite (sections 1-4)
and ROR acronym currency selection (section 2). Pure functions, fixed inputs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.search_terms import derive_acronym, derive_search_terms
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
         "EARTH PLANETARY"),
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
        # The two-term cap now binds long before the 32-char field width does.
        assert st2 == "ORGANIC PROCESS"
        assert st2 == st2.strip() and st2 == st2.upper() and len(st2) <= 32
        assert len(st2.split()) <= 2

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

    @pytest.mark.parametrize("text", [
        "Central Receiving", "Receiving", "Receiving Department",
        "Shipping and Receiving", "Shipping & Receiving", "Shipping/Receiving",
        "Stores", "Central Stores", "Storeroom", "Stockroom",
        "Mail Room", "Mailroom", "Mail Services",
        "Administration", "Office of Administration", "Administrative Services",
    ])
    def test_goods_and_mail_desks_are_admin(self, text):
        """A receiving bay, a stores desk and a mail room are as unverifiable
        as an accounts-payable desk: no registry entry, no web page, no
        institutional spelling to be wrong about."""
        assert is_admin_unit(text) is True

    @pytest.mark.parametrize("text,expected", [
        # Only a desk WITH its generic word. A bare "Business" is a school of
        # business, so the phrase has to arrive whole.
        ("Business Office", True), ("Main Office", True),
        ("Corporate Office", True), ("Administrative Office", True),
        ("Department of Business", False), ("School of Business", False),
        ("College of Business", False), ("Business Administration", False),
        ("Department of Business Administration", False),
        ("Business Development", False), ("Administrative Sciences", False),
    ])
    def test_whole_phrase_desks(self, text, expected):
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
    still reads as something you would type into a search box — and, since it
    has to BE a query rather than a copy of the name, at most two terms."""

    @pytest.mark.parametrize("name1,expected", [
        # An "of" joins two terms into one handle and counts as neither, so
        # this is two terms and not three. It also makes Florida part of the
        # name rather than an address, which is why it survives at all.
        ("University of Florida", "UNIVERSITY OF FLORIDA"),
        ("Verdox, Inc.", "VERDOX"),
        ("Mussel Polymers, Inc.", "MUSSEL POLYMERS"),
        ("NovaBio", "NOVABIO"),
        # Third term dropped.
        ("Applied Thin Films, Inc.", "APPLIED THIN"),
        # "Labs" is a structural word; there is no third term to promote.
        ("Atlantic Testing Labs", "ATLANTIC TESTING"),
        # Structural words are stepped over rather than occupying the second
        # slot: Technology and Standards say more than Institute does.
        ("Massachusetts Institute of Technology", "MASSACHUSETTS TECHNOLOGY"),
        ("National Institute of Standards and Technology",
         "NATIONAL STANDARDS"),
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


class TestAmpersandSurvives:
    """`&` is part of the handle, not punctuation between two names.

    Three separate places dropped it — the acronym loop never saw it (its
    regex required a leading letter), the Name 1 handle discarded it as a
    non-alphanumeric token, and `_fill_to_width` filtered it as a stopword —
    so "Procter & Gamble" reached the output as PROCTER GAMBLE and its
    acronym as PG. P&G is what people actually search.
    """

    @pytest.mark.parametrize("name1,expected", [
        ("Procter & Gamble", "PROCTER & GAMBLE"),
        ("Johnson & Johnson", "JOHNSON & JOHNSON"),
        ("Bausch & Lomb", "BAUSCH & LOMB"),
        # Already one token — nothing to join, nothing to lose.
        ("P&G", "P&G"),
        ("AT&T", "AT&T"),
    ])
    def test_name1_handle_keeps_it(self, name1, expected):
        assert derive_search_terms(_st(name1_enriched=name1))[0] == expected

    @pytest.mark.parametrize("name,expected", [
        ("Procter & Gamble", "P&G"),
        ("Johnson & Johnson", "J&J"),
        ("Bausch & Lomb", "B&L"),
        # No ampersand → unchanged.
        ("International Business Machines", "IBM"),
        ("Massachusetts Institute of Technology", "MIT"),
    ])
    def test_acronym_keeps_it(self, name, expected):
        assert derive_acronym(name) == expected

    def test_ampersand_does_not_count_as_a_term(self):
        # "Procter & Gamble" is TWO terms joined by a connector, so the cap
        # must not read it as three and drop Gamble.
        st1 = derive_search_terms(_st(name1_enriched="Procter & Gamble"))[0]
        assert st1 == "PROCTER & GAMBLE"

    def test_ampersand_survives_in_name2(self):
        r = _st(name1_enriched="General Motors", name2_enriched="Truck & Bus")
        assert derive_search_terms(r)[1] == "TRUCK & BUS"

    def test_leading_or_trailing_ampersand_is_dropped(self):
        # An ampersand joining nothing is punctuation again.
        r = _st(name1_enriched="Verdox &")
        assert derive_search_terms(r)[0] == "VERDOX"


class TestTwoTermCap:
    """A search term is something you type into a search box. Past two words a
    handle stops being a query and becomes a copy of the name."""

    @pytest.mark.parametrize("name1,city,region,expected", [
        # The record's own address is not part of its identity: the city, the
        # state abbreviation and the facility word all come off, and Kellogg
        # is what is left worth searching.
        ("Kellogg Battle Creek MI Plant", "Battle Creek", "MI", "KELLOGG"),
        # Trailing place names, with no help from the address columns.
        ("Nucor Steel Florida", "", "", "NUCOR STEEL"),
        ("Dow Chemical USA", "", "", "DOW CHEMICAL"),
        ("Sanofi Vaccines US", "", "", "SANOFI VACCINES"),
        ("LG Chemistry Michigan", "", "", "LG CHEMISTRY"),
        # NOTHING after the head identifies anything, so the head stands
        # alone. "KELLOGG NORTH" would be worse than "KELLOGG", not better.
        ("Kellogg North America", "", "", "KELLOGG"),
        # Corporate scaffolding in the second slot is stepped over.
        ("General Mills Operations", "", "", "GENERAL MILLS"),
        ("Owens Corning Sales", "", "", "OWENS CORNING"),
        ("Roche Sequencing Solutions", "", "", "ROCHE SEQUENCING"),
        ("Robert Bosch Fuel Systems", "", "", "ROBERT BOSCH"),
        ("Halliburton Technology Partners", "", "", "HALLIBURTON TECHNOLOGY"),
        # Facility and structural words likewise.
        ("Toyota Technical Center USA", "", "", "TOYOTA TECHNICAL"),
        ("Novartis Institute Biomedical", "", "", "NOVARTIS BIOMEDICAL"),
        # Ordinary three-word names keep their first two.
        ("Nestle Purina Pet Care", "", "", "NESTLE PURINA"),
        ("Saint-Gobain Ceramic Materials", "", "", "SAINT-GOBAIN CERAMIC"),
    ])
    def test_st1_capped(self, name1, city, region, expected):
        r = _st(name1_enriched=name1)
        r["city"], r["region"] = city, region
        assert derive_search_terms(r)[0] == expected

    @pytest.mark.parametrize("name1", [
        "Schlumberger Technology", "Northrop Grumman", "Stryker Orthopaedics",
        "Corteva Agriscience", "Bayer Pharmaceuticals", "Medtronic Minimed",
        "Sherwin-Williams", "Pfizer", "Apple", "Dow",
    ])
    def test_already_short_names_are_untouched(self, name1):
        assert derive_search_terms(_st(name1_enriched=name1))[0] == name1.upper()

    def test_head_is_kept_even_when_the_word_is_a_common_one(self):
        # An organisation name is head-initial, so the leading token says
        # WHICH organisation even when it would fail the test on its own.
        # Dropping it would leave "MILLS", which names nobody.
        r = _st(name1_enriched="General Mills Operations")
        assert derive_search_terms(r)[0] == "GENERAL MILLS"

    def test_place_after_of_is_part_of_the_name(self):
        # "Nucor Steel Florida" is a Florida site of Nucor Steel; the
        # University OF Florida is not a Florida branch of some University.
        # The connector is the whole difference.
        assert derive_search_terms(
            _st(name1_enriched="University of Florida"))[0] == "UNIVERSITY OF FLORIDA"
        assert derive_search_terms(
            _st(name1_enriched="Bank of America"))[0] == "BANK OF AMERICA"
        assert derive_search_terms(
            _st(name1_enriched="Nucor Steel Florida"))[0] == "NUCOR STEEL"

    def test_records_own_state_name_and_code_behave_alike(self):
        for region in ("MI", "Michigan"):
            r = _st(name1_enriched="Acme Polymers Michigan")
            r["region"] = region
            assert derive_search_terms(r)[0] == "ACME POLYMERS"

    def test_st2_is_capped_too(self):
        r = _st(name1_enriched="KLA-Tencor", name2_enriched="VLSI Standards Inc")
        assert derive_search_terms(r)[1] == "VLSI STANDARDS"
        r = _st(name1_enriched="J&J", name2_enriched="J&J Regenerative Therapeutics")
        assert derive_search_terms(r)[1] == "J&J REGENERATIVE"

    def test_no_term_ever_exceeds_two_words(self):
        for name in [
            "Kellogg Battle Creek MI Plant",
            "National Institute of Standards and Technology",
            "The Goodyear Tire & Rubber Company",
            "Toyota Technical Center USA",
        ]:
            st1 = derive_search_terms(_st(name1_enriched=name))[0]
            # A connector ("of", "&") joins two terms and is not one itself.
            words = [w for w in st1.split() if w not in ("OF", "FOR", "&")]
            assert len(words) <= 2, f"{name} -> {st1}"


class TestPhraseThatIdentifiesNothing:
    """Every large site has a central receiving bay, a corporate headquarters
    and a stores desk. Those words describe where a delivery goes, never which
    unit the record is — and a search term matching every large employer in the
    country is worse than an empty field, because the empty field does not
    claim to have found something."""

    @pytest.mark.parametrize("name2", [
        "Central Receiving",
        "Corporate Headquarters",
        "Stores",
        "Manufacturing",
        "Central Warehouse",
        "Main Plant",
        "Shipping Dock",
        "Interplant Site Off E",
        "Distribution Center",
    ])
    def test_emptied(self, name2):
        r = _st(name1_enriched="Dow Chemical", name2_enriched=name2)
        assert derive_search_terms(r)[1] is None

    @pytest.mark.parametrize("name2", ["Central Receiving", "Stores"])
    def test_a_goods_desk_is_emptied_not_sent_out_as_admin(self, name2):
        """`is_admin_unit` covers these too — they carry no verifiable claim,
        so they suppress the review flags. Search Term 2 is still EMPTY rather
        than "ADMIN": the sentinel says a back-office desk was identified, and
        a phrase every large site carries identified nothing."""
        r = _st(name1_enriched="Dow Chemical", name2_enriched=name2)
        assert derive_search_terms(r)[1] is None

    @pytest.mark.parametrize("name2,expected", [
        # One identifying token is enough to keep the phrase.
        ("Food Service Systems", "FOOD SERVICE"),
        ("Abbott Nutrition", "ABBOTT NUTRITION"),
        ("Global Technical", "GLOBAL TECHNICAL"),
        ("Truck & Bus", "TRUCK & BUS"),
        # The existing chain is untouched: structural words still strip, and
        # a finance or procurement desk carries an identifying token, so it
        # survives this rule and still reaches the admin override below it.
        ("Department of Chemistry", "CHEMISTRY"),
        ("Analytical Sciences Division", "ANALYTICAL SCIENCES"),
        ("Accounts Payable", "ADMIN"),
        ("Central Purchasing", "ADMIN"),
        ("Office of Finance", "ADMIN"),
    ])
    def test_real_units_survive(self, name2, expected):
        r = _st(name1_enriched="Acme", name2_enriched=name2)
        assert derive_search_terms(r)[1] == expected

    def test_search_term_1_is_not_subject_to_this_rule(self):
        # The rule guards the UNIT slot. Name 1 is the organisation, and a
        # company legitimately named "Central Stores Ltd" must keep a handle
        # rather than have the pipeline decide its name is not a name.
        r = _st(name1_enriched="Central Stores Ltd")
        assert derive_search_terms(r)[0] is not None
