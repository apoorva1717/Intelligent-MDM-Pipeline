"""Cleanup of org names that pass through enrichment uncanonicalised.

When ROR misses, the raw source value is kept verbatim — often ALL-CAPS and
abbreviated. These helpers title-case and expand it so passthrough rows are
consistent with ROR-matched ones, without touching canonical (mixed-case)
names.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from utils.text_utils import (
    clean_passthrough_org_name,
    expand_abbreviations,
    smart_title_case,
)


# ---------------------------------------------------------------------------
# smart_title_case
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    # ALL-CAPS multi-word names → title case.
    ("ACADEMIC PUBLICATION SERVICES", "Academic Publication Services"),
    ("BRANDON HOSPITAL", "Brandon Hospital"),
    ("LAKELAND REGIONAL HEALTH", "Lakeland Regional Health"),
    ("MIAMI EXPRESS IMPORT EXPORT", "Miami Express Import Export"),
    ("DAVID JOHNSON", "David Johnson"),
    # Connectors lowercased.
    ("SECRETARY OF STATE", "Secretary of State"),
    # 3-letter real word cased; legal-form acronym kept.
    ("SOUTH BAY HOSPITAL", "South Bay Hospital"),
    ("STERLING INDUSTRY LLC", "Sterling Industry LLC"),
    # Acronyms preserved.
    ("MRI DEPARTMENT", "MRI Department"),
    ("IBM", "IBM"),
    ("USA", "USA"),
    ("NASA", "NASA"),
    ("EMSL", "EMSL"),
    # Legal forms that read better title-cased.
    ("INC", "Inc"),
    ("E.R. SQUIBB AND SONS, L.L.C.", "E.R. Squibb and Sons, L.L.C."),
])
def test_smart_title_case(value, expected):
    assert smart_title_case(value) == expected


@pytest.mark.parametrize("value", [
    "University of Florida",          # mixed-case canonical
    "NASA Kennedy Space Center",
    "EMSL Analytical, Inc.",
    "MedLab Diagnostics",
    "BioVenture Labs",
])
def test_smart_title_case_leaves_mixed_case_untouched(value):
    assert smart_title_case(value) == value


# ---------------------------------------------------------------------------
# expand_abbreviations — new rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    # "Med Ctr" must become "Medical Center", not "Medicine Center".
    ("Capital Regional Med Ctr", "Capital Regional Medical Center"),
    ("Largo Medical Ctr", "Largo Medical Center"),
    # Generic Med (not before a centre word) still expands to Medicine.
    ("School of Med", "School of Medicine"),
    # Common University misspellings.
    ("Universtiy of Florida", "University of Florida"),
    ("Univeristy of Miami", "University of Miami"),
])
def test_expand_abbreviations_new_rules(value, expected):
    assert expand_abbreviations(value) == expected


# ---------------------------------------------------------------------------
# clean_passthrough_org_name — title-case + expand, correct order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("LARGO MEDICAL CTR", "Largo Medical Center"),
    ("Capital Regional Med Ctr", "Capital Regional Medical Center"),
    ("UNIVERSTIY OF FLORIDA", "University of Florida"),
    ("STERLING INDUSTRY LLC", "Sterling Industry LLC"),
    ("SOUTH BAY HOSPITAL", "South Bay Hospital"),
])
def test_clean_passthrough_org_name(value, expected):
    assert clean_passthrough_org_name(value) == expected


@pytest.mark.parametrize("value", [None, "", "   "])
def test_clean_passthrough_org_name_blank(value):
    assert clean_passthrough_org_name(value) == value
