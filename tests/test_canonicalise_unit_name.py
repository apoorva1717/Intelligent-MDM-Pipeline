"""canonicalise_unit_name: reorder real units, but never fabricate a
'Department of <X>' from a truncated subject ("Biomed")."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from utils.text_utils import canonicalise_unit_name as c


@pytest.mark.parametrize("value, expected", [
    # Real subjects → canonicalised normally.
    ("Chemistry Dept", "Department of Chemistry"),
    ("Chemistry Department", "Department of Chemistry"),
    ("Radiology Dept", "Department of Radiology"),
    ("Biology Division", "Division of Biology"),
    ("Biomedical Engineering Dept", "Department of Biomedical Engineering"),
    ("Department of Chemistry", "Department of Chemistry"),
    # Changed deliberately. The "<Subject> <Unit>" -> "<Unit> of <Subject>"
    # inversion now applies only to Department / Division / Faculty, the unit
    # words where the two forms are interchangeable. "Center", "Institute",
    # "Laboratory", "College" and "School" NAME an organisation when they
    # trail, and inverting them fabricated units nobody calls by that name:
    # "Texas Heart Institute" shipped as "Institute of Texas Heart", the Salk
    # Institute would have become the "Institute of Salk", and "Harvard
    # Medical School" shipped as "School of Harvard Medical". A record that
    # states one of those is stating a name, so it is left as supplied. See
    # tests/test_named_school_not_inverted.py for the School half.
    ("Cancer Research Center", "Cancer Research Center"),
    ("Harvard Medical School", "Harvard Medical School"),
    # Truncated subjects → left unchanged (no fabricated "Department of X").
    ("Biomed Dept", "Biomed Dept"),
    ("Biomed Department", "Biomed Department"),
    ("Neuro Dept", "Neuro Dept"),
])
def test_canonicalise_unit_name(value, expected):
    assert c(value) == expected
