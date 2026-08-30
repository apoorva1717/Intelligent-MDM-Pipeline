"""An orphaned building name is a building, not a department.

The residual classifier is an LLM lane over whatever is left in a secondary
street slot after extraction, and `DEPARTMENT` is the one verdict it can return
that writes into the NAME block. Measured on the golden set, it kept reading an
orphaned BUILDING NAME as a department -- because that is exactly what the
extractor leaves behind:

    "Equad A302"                        -> Room "A302",       orphan "Equad"
    "Genomics Bldg 1219B-MA"            -> Building "1219B-MA", orphan "Genomics"
    "Mary Moody Northern Building L"    -> Building "L",      orphan "Mary Moody
                                                                Northern Code:"

Each orphan shipped in a name slot the reference wants empty, and the reference
names each one as the Building: Building "Equad" / Room "A302"; Building
"Genomics" / Room "1219B-MA". The extractor had the two columns the wrong way
round -- it kept the identifier and orphaned the name.

Two rules, and the second is what makes the first pay:

* a `DEPARTMENT` verdict needs `_looks_like_department` to agree before it may
  write into a name slot -- corroboration, on the lane that authors into the
  most valuable columns in the record;
* an orphan that reads as a NAME, on a record whose Building holds only a
  CODE, takes the Building column and sends the code to Room.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.address_processing import (
    _is_bare_code,
    _looks_like_department,
    _orphaned_building_name,
)


class TestWhatCountsAsACode:
    @pytest.mark.parametrize("value", ["1219B-MA", "L", "A302", "B-14", "22"])
    def test_an_identifier_has_no_word_in_it(self, value):
        assert _is_bare_code(value) is True

    @pytest.mark.parametrize("value", [
        "Equad", "Genomics", "Mary Moody Northern", "Wing C",
    ])
    def test_a_name_does(self, value):
        assert _is_bare_code(value) is False


class TestTheOrphanedName:
    @pytest.mark.parametrize("residual,expected", [
        ("Equad", "Equad"),
        ("Genomics", "Genomics"),
        # The label belonged to the code the extractor already took, so it
        # leaves with it.
        ("Mary Moody Northern Code:", "Mary Moody Northern"),
        ("Wilson Hall MS", "Wilson Hall"),
    ])
    def test_a_wordy_residual_is_a_building_name(self, residual, expected):
        assert _orphaned_building_name(residual) == expected

    @pytest.mark.parametrize("residual", [
        "1219B-MA",     # an identifier, not a name
        "L",
        "Suite 400",    # carries its own digits -- a second code
        "Room 12",
        "",
        None,
    ])
    def test_anything_carrying_a_code_is_not(self, residual):
        assert _orphaned_building_name(residual) is None

    @pytest.mark.parametrize("value", ["Williams", "Systems", "Commons"])
    def test_a_word_merely_ENDING_in_a_label_is_untouched(self, value):
        """The label strip is word-bounded. Without that, "Williams" loses its
        "ms" and ships as "Willia"."""
        assert _orphaned_building_name(value) == value


class TestTheCorroborationItself:
    @pytest.mark.parametrize("value", [
        "Chemistry Dept.", "Department of Chemistry",
    ])
    def test_a_real_department_still_passes(self, value):
        assert _looks_like_department(value) is True

    @pytest.mark.parametrize("value", [
        "Equad", "Genomics", "Mary Moody Northern",
    ])
    def test_an_orphaned_building_name_does_not(self, value):
        """This is the gate. The LLM called each of these a department; the
        deterministic predicate refuses to corroborate, so none of them
        reaches a name slot."""
        assert _looks_like_department(value) is False
