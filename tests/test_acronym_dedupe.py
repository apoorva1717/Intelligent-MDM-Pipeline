"""When Name 1 carries both an acronym and its full form for the same entity,
keep only the full form."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import (
    preprocess_record,
    _strip_redundant_acronym,
    _syllabic_dash_abbrev,
)


def _n1(name1):
    return preprocess_record(
        name1=name1, name2=None, name3=None, contact=None, email=None,
        street1=None, street2=None, street3=None,
    ).name1


class TestAcronymDedupe:
    @pytest.mark.parametrize("value,expected", [
        ("MIT Massachusetts Institute of Technology",
         "Massachusetts Institute of Technology"),
        ("Massachusetts Institute of Technology (MIT)",
         "Massachusetts Institute of Technology"),
        ("MIT (Massachusetts Institute of Technology)",
         "Massachusetts Institute of Technology"),
        ("Massachusetts Institute of Technology MIT",
         "Massachusetts Institute of Technology"),
        ("University of California, Los Angeles (UCLA)",
         "University of California, Los Angeles"),
        ("International Business Machines (IBM)",
         "International Business Machines"),
        ("BMW Bayerische Motoren Werke", "Bayerische Motoren Werke"),
    ])
    def test_keeps_full_form(self, value, expected):
        assert _n1(value) == expected

    @pytest.mark.parametrize("value", [
        "UC Berkeley",
        "3M Company",
        "University of Florida",
        "AT&T",
        "IT University of Copenhagen",
        "US Army Corps of Engineers",
    ])
    def test_unrelated_tokens_untouched(self, value):
        assert _strip_redundant_acronym(value) == value


class TestDashAcronymDedupe:
    """Item 8: dash-delimited acronym + full form."""

    @pytest.mark.parametrize("value,expected_contains", [
        ("NATIONAL INSTITUTE OF STANDARDS AND TECHNOLOGY-NIST",
         "National Institute of Standards and Technology"),
        ("FDA - Food & Drug Administration", "Food and Drug Administration"),
        ("Njit - New Jersey Institute of Technology",
         "New Jersey Institute of Technology"),
        ("Ccsf - City College of San Francisco",
         "City College of San Francisco"),
    ])
    def test_dash_form_keeps_full(self, value, expected_contains):
        # finalise title-cases later; compare case-insensitively.
        assert _strip_redundant_acronym(value).lower() == expected_contains.lower()

    @pytest.mark.parametrize("value", [
        "Heriot-Watt University",
        "Hahn-Schickard-Gesellschaft",
        "Bio-Rad",
        "Dana-Farber",
        "Dana-Farber Cancer Institute",
    ])
    def test_hyphenated_proper_nouns_untouched(self, value):
        assert _strip_redundant_acronym(value) == value

    @pytest.mark.parametrize("value,full", [
        ("CALIBR - California Institute for Biomedical Research",
         "California Institute for Biomedical Research"),
        ("Tuhh - Hamburg University of Technology",
         "Hamburg University of Technology"),
    ])
    def test_syllabic_resolved_to_full_form(self, value, full):
        # Never keep both — resolve to the full form...
        assert _strip_redundant_acronym(value) == full
        # ...but flag it (not a verified initialism).
        assert _syllabic_dash_abbrev(value) is not None

    def test_syllabic_raises_preprocess_flag(self):
        r = preprocess_record(
            name1="CALIBR - California Institute for Biomedical Research",
            name2=None, name3=None, contact=None, email=None,
            street1=None, street2=None, street3=None,
        )
        assert r.name1 == "California Institute for Biomedical Research"
        assert any("acronym-ambiguous" in fl for fl in r.flags)

    def test_verified_dash_no_flag(self):
        r = preprocess_record(
            name1="Njit - New Jersey Institute of Technology",
            name2=None, name3=None, contact=None, email=None,
            street1=None, street2=None, street3=None,
        )
        assert r.name1.lower() == "new jersey institute of technology"
        assert not any("acronym-ambiguous" in fl for fl in r.flags)

    def test_all_caps_fda_and_casing(self):
        from utils.text_utils import smart_title_case
        stripped = _strip_redundant_acronym("FDA - FOOD & DRUG ADMINISTRATION")
        # "&" → "AND" (upper) so the ALL-CAPS full form still title-cases cleanly.
        assert smart_title_case(stripped) == "Food and Drug Administration"
