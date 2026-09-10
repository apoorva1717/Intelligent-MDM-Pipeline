"""UC 6 — "RECG" is the receiving desk, and nothing else is.

SAP exports carry the receiving dock as a bare four-letter code. It is not a
word: `utils.text_utils` already has to tell "GAS" from "RECG" precisely
because a consumer reading a Name column sees an unexplained code and no
department. The code means one thing, so it ships as the one thing it means.

The canonical string is "Receiving", not "Receiving Dept", and the reason is
mechanical rather than stylistic: `expand_abbreviations` turns "Dept" into
"Department" and `canonicalise_unit_name` inverts an "X Department" suffix,
so "Receiving Dept" would ship as "Department of Receiving". "Receiving" is a
fixed point of both passes — the same property that lets "Accounts Payable"
hold without a suffix — so no invariant has to be restated downstream to keep
it. "receiving" is already in `_FUNCTIONAL_UNIT_WORDS`, so the placement
routers read the normalised value as a department with no further wiring.

Matching is whole-field and exact. "RECG Warehouse" is a named facility and
"Building RECG" is a building; neither is the desk, and a value that merely
CONTAINS the code is never rewritten. That is the whole difference from the
accounts-payable detector, which matches anywhere in a field and needs a
suffix splitter and a segment list to undo the damage that causes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record

RECEIVING = "Receiving"


def _pp(**kw):
    fields = dict(
        name1=None, name2=None, name3=None, name4=None, name5=None,
        contact=None, email=None, street1=None, street2=None, street3=None,
    )
    fields.update(kw)
    return preprocess_record(**fields)


def _names(r):
    return [r.name1, r.name2, r.name3, r.name4, r.name5]


def _streets(r):
    return [r.street1, r.street2, r.street3]


class TestNameSlot:
    """The code sits in a name slot already — it normalises where it stands."""

    @pytest.mark.parametrize("value", ["RECG", "recg", "Recg", "  RECG  "])
    def test_whole_field_normalises_in_place(self, value):
        r = _pp(name1="Acme Corp", name2=value)
        assert r.name1 == "Acme Corp"
        assert r.name2 == RECEIVING
        assert _names(r)[2:] == [None, None, None]

    def test_lower_slot_normalises_too(self):
        r = _pp(name1="Acme Corp", name2="Oncology Research Unit", name3="RECG")
        assert _names(r) == ["Acme Corp", "Oncology Research Unit", RECEIVING,
                             None, None]


class TestStreetSlot:
    """The code sits in a street slot — it is not an address, so it moves."""

    def test_moves_to_the_first_empty_name_slot(self):
        r = _pp(name1="Acme Corp", street1="RECG")
        assert _names(r) == ["Acme Corp", RECEIVING, None, None, None]
        assert _streets(r) == [None, None, None]

    def test_skips_an_occupied_slot(self):
        r = _pp(name1="Acme Corp", name2="Beta Holdings Inc", street2="RECG")
        assert _names(r) == ["Acme Corp", "Beta Holdings Inc", RECEIVING,
                             None, None]
        assert _streets(r) == [None, None, None]

    def test_redundant_when_the_name_block_already_has_a_department(self):
        # A department is already stated; a second one from a street field
        # adds nothing, and the router drops it rather than stack two.
        r = _pp(name1="Acme Corp", name2="Oncology Research Unit", street1="RECG")
        assert _names(r) == ["Acme Corp", "Oncology Research Unit",
                             None, None, None]
        assert _streets(r) == [None, None, None]

    def test_name_block_full_leaves_the_value_in_the_street(self):
        # Nowhere to put the desk. Better an unmoved street than a dropped
        # one — the same call the AP router makes.
        r = _pp(
            name1="Acme Corp", name2="Beta Holdings Inc",
            name3="Gamma Trading Ltd", name4="Delta Partners LLC",
            name5="Epsilon Ventures Inc", street1="RECG",
        )
        assert _names(r) == ["Acme Corp", "Beta Holdings Inc",
                             "Gamma Trading Ltd", "Delta Partners LLC",
                             "Epsilon Ventures Inc"]
        assert r.street1 == "RECG"
        assert RECEIVING not in _names(r)


class TestAttnIsNeverAContact:
    """"Attn: RECG" addresses a desk. No person is named, so no contact is."""

    @pytest.mark.parametrize("value", ["Attn: RECG", "ATTN: RECG", "Attn RECG"])
    def test_attn_payload_lands_in_a_name_slot(self, value):
        r = _pp(name1="Acme Corp", name2=value)
        assert r.name2 == RECEIVING
        assert not (r.contact and r.contact.strip())

    def test_not_extracted_as_a_contact_from_a_bare_slot(self):
        r = _pp(name1="Acme Corp", name2="RECG")
        assert not (r.contact and r.contact.strip())


class TestPartialMatchesAreLeftAlone:
    """The code has to be the WHOLE field. Anything else is a real value."""

    @pytest.mark.parametrize("value", [
        "RECG Warehouse",
        "RECG Dock 3",
        "Acme Corp RECG",
        "RECGA",
        "PRECG",
    ])
    def test_name_slot_value_is_not_rewritten(self, value):
        r = _pp(name1="Acme Corp", name2=value)
        assert r.name2 == value
        assert RECEIVING not in _names(r)

    def test_building_recg_is_a_building(self):
        # The named-building router owns this value and routes it to the
        # street. What matters here is that it is never read as the desk.
        r = _pp(name1="Acme Corp", name2="Building RECG")
        assert RECEIVING not in _names(r)
        assert "Building RECG" in [v for v in _names(r) + _streets(r) if v]

    def test_partial_match_in_a_street_slot_is_not_rewritten(self):
        r = _pp(name1="Acme Corp", street1="RECG Warehouse")
        assert RECEIVING not in _names(r)
        assert r.street1 == "RECG Warehouse"
