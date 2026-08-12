"""Item 9: smart_title_case must not destroy acronyms or the segment after a
hyphen, and must preserve Mc surnames."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.text_utils import smart_title_case as f


class TestCasingFixes:
    @pytest.mark.parametrize("value,expected", [
        ("ABX-CRO", "ABX-CRO"),
        ("BIO-RAD", "Bio-Rad"),
        ("DANA-FARBER", "Dana-Farber"),
        ("VIRGIN-DOWNEY", "Virgin-Downey"),
        ("TECHNOLOGY-NIST", "Technology-NIST"),
        ("TUHH", "TUHH"),
        ("NJIT", "NJIT"),
        ("MCINTYRE", "McIntyre"),
        # University acronyms surfaced from a street field must keep their casing.
        ("UCSF", "UCSF"),
        ("UCSD", "UCSD"),
        ("UCLA", "UCLA"),
        ("SUNY", "SUNY"),
        ("UMASS", "UMASS"),
    ])
    def test_fixed(self, value, expected):
        assert f(value) == expected


class TestExistingBehaviourPreserved:
    @pytest.mark.parametrize("value,expected", [
        ("SOUTH BAY HOSPITAL", "South Bay Hospital"),
        ("STERLING INDUSTRY LLC", "Sterling Industry LLC"),
        ("MRI DEPARTMENT", "MRI Department"),
        ("LARGO MEDICAL CENTER", "Largo Medical Center"),
        ("COLLEGE OF MEDICINE", "College of Medicine"),
    ])
    def test_unchanged_behaviour(self, value, expected):
        assert f(value) == expected

    def test_mac_not_overcapitalised(self):
        # "Mac" is left alone — capitalising after it mangles ordinary words.
        assert f("MACRON") == "Macron"

    @pytest.mark.parametrize("value", ["Bio-Rad", "University of Florida", "McIntyre"])
    def test_mixed_case_untouched(self, value):
        assert f(value) == value