"""ROR-local US state-abbreviation expansion.

"Fla State Univ" must expand to "Florida State Univ" for the ROR query so it
resolves to Florida State University instead of matching the generic
"State University" tokens of an unrelated org (e.g. Kent State University).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier1_ror import _expand_state_abbrevs


class TestStateAbbrevExpansion:
    @pytest.mark.parametrize("value,expected", [
        ("Fla State Univ", "Florida State Univ"),
        ("Wash State Univ", "Washington State Univ"),
        ("Penn State Univ", "Pennsylvania State Univ"),
        ("Calif Inst of Tech", "California Inst of Tech"),
        ("Tenn Tech Univ", "Tennessee Tech Univ"),
    ])
    def test_expands_state_abbrev(self, value, expected):
        assert _expand_state_abbrevs(value) == expected

    @pytest.mark.parametrize("value", [
        "Univ of Florida",            # no abbrev present
        "Kent State University",       # full form, no abbrev
        "University of Washington",    # full form
        "Massachusetts Institute of Technology",
    ])
    def test_no_false_expansion(self, value):
        assert _expand_state_abbrevs(value) == value
