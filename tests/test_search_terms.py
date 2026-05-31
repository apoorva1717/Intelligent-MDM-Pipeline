"""Tests for the search_term_1 / search_term_2 derivation helper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.search_terms import (
    clean_name2_phrase,
    derive_acronym,
    derive_department_domain,
    derive_search_terms,
    strip_tld,
    unit_domain_or_path,
)


class TestDeriveDepartmentDomain:
    def test_subdomain_returns_full_host(self):
        assert derive_department_domain(
            "Computer Science",
            "https://cs.mit.edu/people/foo",
            "mit.edu",
        ) == "cs.mit.edu"

    def test_multi_level_subdomain(self):
        assert derive_department_domain(
            "Computer Science",
            "https://web.cs.mit.edu/",
            "mit.edu",
        ) == "web.cs.mit.edu"

    def test_strips_www(self):
        assert derive_department_domain(
            "Computer Science",
            "https://www.cs.mit.edu/",
            "mit.edu",
        ) == "cs.mit.edu"

    def test_bare_institution_domain_returns_none(self):
        assert derive_department_domain(
            "Computer Science",
            "https://mit.edu/cs/people",
            "mit.edu",
        ) is None

    def test_different_registrable_domain_now_accepted(self):
        # Relaxed: cross-domain departments like medical schools that
        # live on a separate brand domain are accepted (e.g. JHU →
        # hopkinsmedicine.org). The only host rejected is the bare
        # institution domain itself.
        assert derive_department_domain(
            "Department of Radiology",
            "https://www.hopkinsmedicine.org/profiles/details/robert-chen",
            "jhu.edu",
        ) == "hopkinsmedicine.org"

    def test_name2_absent_returns_none(self):
        assert derive_department_domain(
            None, "https://cs.mit.edu/", "mit.edu"
        ) is None
        assert derive_department_domain(
            "", "https://cs.mit.edu/", "mit.edu"
        ) is None

    def test_missing_source_url_returns_none(self):
        assert derive_department_domain(
            "Computer Science", None, "mit.edu"
        ) is None

    def test_missing_base_domain_returns_none(self):
        assert derive_department_domain(
            "Computer Science", "https://cs.mit.edu/", None
        ) is None


class TestStripTld:
    def test_two_part_domain(self):
        assert strip_tld("mit.edu") == "mit"

    def test_three_part_known_tld(self):
        assert strip_tld("example.co.uk") == "example"

    def test_subdomain_keeps_subdomain(self):
        # strip_tld only drops the trailing TLD, not subdomains.
        assert strip_tld("cs.mit.edu") == "cs.mit"

    def test_already_tld_less(self):
        assert strip_tld("mit") == "mit"

    def test_empty_and_none(self):
        assert strip_tld("") is None
        assert strip_tld(None) is None


class TestDeriveAcronym:
    """Acronym derivation is used only for search_term_1."""

    def test_basic_caps(self):
        assert derive_acronym("Massachusetts Institute of Technology") == "MIT"

    def test_parenthetical_acronym_wins(self):
        assert derive_acronym("Computer Science (CS)") == "CS"

    def test_lowercase_name_returns_none(self):
        assert derive_acronym("mit") is None

    def test_single_word_returns_none(self):
        assert derive_acronym("Stanford") is None

    def test_empty_and_none(self):
        assert derive_acronym("") is None
        assert derive_acronym(None) is None

    def test_skips_stopwords(self):
        assert derive_acronym("International Business Machines") == "IBM"

    def test_mixed_case_yields_none(self):
        # Partial capitalisation must NOT produce a half-acronym.
        assert derive_acronym("Massachusetts institute of Technology") is None


class TestCleanName2Phrase:
    def test_strips_department_of(self):
        assert clean_name2_phrase("Department of Computer Science") == "Computer Science"

    def test_strips_dept_of(self):
        assert clean_name2_phrase("Dept of Chemistry") == "Chemistry"

    def test_strips_school_of(self):
        assert clean_name2_phrase("School of Engineering") == "Engineering"

    def test_strips_division_of(self):
        assert clean_name2_phrase("Division of Cardiology") == "Cardiology"

    def test_strips_center_for(self):
        assert clean_name2_phrase("Center for Neuroscience") == "Neuroscience"

    def test_strips_trailing_department(self):
        assert clean_name2_phrase("Theoretical Physics Department") == "Theoretical Physics"

    def test_strips_trailing_lab(self):
        assert clean_name2_phrase("Candelario Lab") == "Candelario"

    def test_title_cases_lowercase_input(self):
        assert clean_name2_phrase("dept of chemistry") == "Chemistry"

    def test_preserves_all_caps_tokens(self):
        assert clean_name2_phrase("MRI lab") == "MRI"

    def test_already_clean_returned_verbatim(self):
        assert clean_name2_phrase("Analytical Sciences") == "Analytical Sciences"

    def test_only_prefix_returns_none(self):
        assert clean_name2_phrase("Department of") is None

    def test_empty_and_none(self):
        assert clean_name2_phrase("") is None
        assert clean_name2_phrase(None) is None


class TestUnitDomainOrPath:
    def test_subdomain_prefix_only(self):
        assert unit_domain_or_path(
            "https://cs.mit.edu/people/foo", "mit.edu"
        ) == "cs"

    def test_multi_level_subdomain_prefix(self):
        assert unit_domain_or_path(
            "https://web.cs.mit.edu/", "mit.edu"
        ) == "web.cs"

    def test_path_segment_when_host_matches_base(self):
        assert unit_domain_or_path(
            "https://mit.edu/cs/people/foo", "mit.edu"
        ) == "/cs"

    def test_skips_generic_path_segments(self):
        assert unit_domain_or_path("https://mit.edu/about/", "mit.edu") is None

    def test_different_registrable_domain_strips_tld(self):
        assert unit_domain_or_path(
            "https://pfizerlabs.com/", "mit.edu"
        ) == "pfizerlabs"

    def test_empty_inputs(self):
        assert unit_domain_or_path(None, "mit.edu") is None
        assert unit_domain_or_path("https://mit.edu", None) is None


class TestDeriveSearchTerms:
    def test_ror_acronym_preferred(self):
        result = {
            "_ror_acronym": "MIT",
            "domain": "mit.edu",
            "name1_enriched": "Massachusetts Institute of Technology",
            "name2_enriched": None,
            "name2_original": None,
            "source_url": None,
        }
        st1, st2 = derive_search_terms(result)
        assert st1 == "MIT"
        assert st2 is None

    def test_domain_fallback_strips_tld(self):
        result = {
            "_ror_acronym": None,
            "domain": "stanford.edu",
            "name1_enriched": "Stanford University",
            "name2_enriched": None,
            "name2_original": None,
            "source_url": None,
        }
        st1, _ = derive_search_terms(result)
        assert st1 == "stanford"

    def test_caps_fallback_when_no_acronym_or_domain(self):
        result = {
            "_ror_acronym": None,
            "domain": None,
            "name1_enriched": "Massachusetts Institute of Technology",
            "name2_enriched": None,
            "name2_original": None,
            "source_url": None,
        }
        st1, _ = derive_search_terms(result)
        assert st1 == "MIT"

    def test_search_term_2_uses_department_domain_subdomain(self):
        result = {
            "_ror_acronym": "MIT",
            "domain": "mit.edu",
            "name1_enriched": "MIT",
            "name2_enriched": "Department of Computer Science",
            "name2_original": "Department of Computer Science",
            "department_domain": "eecs.mit.edu",
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "eecs"

    def test_search_term_2_strips_web_prefix(self):
        result = {
            "_ror_acronym": None,
            "domain": "princeton.edu",
            "name1_enriched": "Princeton University",
            "name2_enriched": "Department of Astrophysical Sciences",
            "name2_original": "Department of Astrophysical Sciences",
            "department_domain": "web.astro.princeton.edu",
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "astro"

    def test_search_term_2_crossdomain_strips_tld(self):
        result = {
            "_ror_acronym": "JHU",
            "domain": "jhu.edu",
            "name1_enriched": "Johns Hopkins University",
            "name2_enriched": "Department of Radiology",
            "name2_original": "Department of Radiology",
            "department_domain": "hopkinsmedicine.org",
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "hopkinsmedicine"

    def test_search_term_2_falls_back_to_two_words(self):
        # No department_domain → use first 2 significant words.
        result = {
            "_ror_acronym": None,
            "domain": "harvard.edu",
            "name1_enriched": "Harvard University",
            "name2_enriched": "Department of Earth and Planetary Sciences",
            "name2_original": "Department of Earth and Planetary Sciences",
            "department_domain": None,
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "Earth Planetary"

    def test_search_term_2_fallback_two_words_chemistry(self):
        result = {
            "_ror_acronym": None,
            "domain": "ucla.edu",
            "name1_enriched": "UCLA",
            "name2_enriched": "Department of Chemistry and Biochemistry",
            "name2_original": "Department of Chemistry and Biochemistry",
            "department_domain": None,
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "Chemistry Biochemistry"

    def test_search_term_2_fallback_single_word_dept(self):
        result = {
            "_ror_acronym": None,
            "domain": "cern.ch",
            "name1_enriched": "CERN",
            "name2_enriched": "Department of Radiology",
            "name2_original": "Department of Radiology",
            "department_domain": None,
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "Radiology"

    def test_search_term_2_paren_acronym_when_no_dept_domain(self):
        result = {
            "_ror_acronym": "MIT",
            "domain": "mit.edu",
            "name1_enriched": "MIT",
            "name2_enriched": "Computer Science and AI Lab (CSAIL)",
            "name2_original": "Computer Science and AI Lab (CSAIL)",
            "department_domain": None,
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "CSAIL"

    def test_search_term_2_title_cases_lowercase_input(self):
        result = {
            "_ror_acronym": "UCLA",
            "domain": "ucla.edu",
            "name1_enriched": "UCLA",
            "name2_enriched": "dept of chemistry",
            "name2_original": "dept of chemistry",
            "department_domain": None,
        }
        _, st2 = derive_search_terms(result)
        assert st2 == "Chemistry"

    def test_search_term_2_none_when_name2_absent_and_no_dept_domain(self):
        result = {
            "_ror_acronym": "MIT",
            "domain": "mit.edu",
            "name1_enriched": "MIT",
            "name2_enriched": None,
            "name2_original": None,
            "department_domain": None,
        }
        _, st2 = derive_search_terms(result)
        assert st2 is None

    def test_falls_back_to_name1_original_when_no_enriched(self):
        result = {
            "_ror_acronym": None,
            "domain": None,
            "name1_enriched": None,
            "name1_original": "International Business Machines",
            "name2_enriched": None,
            "name2_original": None,
            "source_url": None,
        }
        st1, _ = derive_search_terms(result)
        assert st1 == "IBM"
