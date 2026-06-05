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
    ("Cancer Research Center", "Center for Cancer Research"),
    # Truncated subjects → left unchanged (no fabricated "Department of X").
    ("Biomed Dept", "Biomed Dept"),
    ("Biomed Department", "Biomed Department"),
    ("Neuro Dept", "Neuro Dept"),
])
def test_canonicalise_unit_name(value, expected):
    assert c(value) == expected
