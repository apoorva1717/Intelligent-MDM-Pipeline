"""A dept proposal may not drop the unit word the record itself stated.

`complete_unit_construction` restores a unit construction the record states
and can do nothing else: the unit word and its preposition are read off the
supplied value, so the rule cannot invent one where the input had none and
cannot exchange one for another.
"""
from __future__ import annotations

import pytest

from utils.text_utils import carries_unit_word, complete_unit_construction


class TestCompletesFromTheRecordsOwnWords:
    @pytest.mark.parametrize(("supplied", "proposal", "expected"), [
        # The SAP overflow case: one department across two columns, reassembled
        # bare by the lane that read it off the faculty page.
        ("ENGINEERING Dept of Mechanical", "Mechanical Engineering",
         "Department of Mechanical Engineering"),
        ("ENGINEERING Dept of Industrial and", "Industrial and Systems Engineering",
         "Department of Industrial and Systems Engineering"),
        ("Dept of Nuclear Engineering &", "Nuclear Engineering & Radiological",
         "Department of Nuclear Engineering & Radiological"),
        # The preposition is the record's, not a default.
        ("Inst for Memory Impairments", "Memory Impairments",
         "Institute for Memory Impairments"),
        ("Div of Animal Health", "Animal Health", "Division of Animal Health"),
    ])
    def test_the_input_construction_is_restored(self, supplied, proposal, expected):
        assert complete_unit_construction(supplied, proposal) == expected


class TestItCannotInventOrExchange:
    def test_bare_input_and_bare_proposal_are_untouched(self):
        assert complete_unit_construction(
            "Mechanical Engineering", "Mechanical Engineering",
        ) == "Mechanical Engineering"

    def test_a_proposal_that_already_states_its_unit_is_untouched(self):
        assert complete_unit_construction(
            "Dept of Chemistry", "Department of Chemistry",
        ) == "Department of Chemistry"

    def test_a_trailing_unit_word_is_not_a_prefix_construction(self):
        """"Baytown Refinery Lab" does not become "Laboratory of Baytown Refinery".

        The record states the construction the other way round. Turning it
        around is rewriting it, not restoring it — and the reversal reads as
        a name nobody uses.
        """
        assert complete_unit_construction(
            "Baytown Refinery Lab", "Baytown Refinery",
        ) == "Baytown Refinery"
        assert complete_unit_construction(
            "d/b/a Dairy Diagnostics Laboratory", "Dairy Diagnostics",
        ) == "Dairy Diagnostics"

    def test_no_unit_word_anywhere_is_a_no_op(self):
        assert complete_unit_construction(
            "Social Sciences", "Philosophy",
        ) == "Philosophy"

    def test_empty_inputs_are_returned_unchanged(self):
        assert complete_unit_construction(None, "Chemistry") == "Chemistry"
        assert complete_unit_construction("Dept of Chemistry", None) is None
        assert complete_unit_construction("Dept of Chemistry", "") == ""


class TestCarriesUnitWord:
    @pytest.mark.parametrize("text", [
        "Department of Chemistry", "Div of Animal Health", "Baytown Refinery Lab",
        "School of Medicine", "Institute for Memory Impairments",
    ])
    def test_true_when_the_kind_is_stated(self, text):
        assert carries_unit_word(text) is True

    @pytest.mark.parametrize("text", [
        "Mechanical Engineering", "Accounts Payable", "Social Sciences", "",
    ])
    def test_false_when_it_is_not(self, text):
        assert carries_unit_word(text) is False
