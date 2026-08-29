"""UC 6 — an organisation and its accounts-payable desk in ONE name field.

"McLaren HealthCare Corp/Acct Pay" is a customer AND a mail-routing desk
packed into Name 1. The desk has to come out into its own slot; replacing the
whole field with "Accounts Payable" — which is what a desk-only value gets —
would delete the only organisation the record has and leave nothing to enrich.

Also covers the clipped SAP spellings ("Acct Pay", "Accts Pay") that the
long-form patterns missed, and the guards: a desk-only value still collapses,
a hyphenated name is never split, and a bare "AP" is only read as the desk
when it is a delimited segment of its own.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record
from utils.text_utils import is_admin_unit


def _pp(**kw):
    return preprocess_record(
        name1=kw.get("name1"), name2=kw.get("name2"), name3=kw.get("name3"),
        name4=kw.get("name4"), name5=kw.get("name5"),
        contact=None, email=None,
        street1=None, street2=None, street3=None,
    )


class TestOrgPlusDeskSplit:
    @pytest.mark.parametrize("value", [
        "McLaren HealthCare Corp/Acct Pay",
        "McLaren HealthCare Corp / Acct Pay",
        "McLaren HealthCare Corp/Accounts Payable",
        "McLaren HealthCare Corp, Accts Pay",
        "McLaren HealthCare Corp - A/P",
        "McLaren HealthCare Corp | Accounts Payable Department",
    ])
    def test_organisation_survives_the_desk(self, value):
        r = _pp(name1=value)
        assert r.name1 == "McLaren HealthCare Corp"
        assert r.name2 == "Accounts Payable"

    def test_bare_ap_segment_is_the_desk(self):
        r = _pp(name1="Acme Corp/AP")
        assert r.name1 == "Acme Corp"
        assert r.name2 == "Accounts Payable"

    def test_desk_goes_to_the_first_empty_slot(self):
        r = _pp(name1="Acme Corp/Acct Pay", name2="Oncology Research Unit")
        assert r.name1 == "Acme Corp"
        assert r.name2 == "Oncology Research Unit"
        assert r.name3 == "Accounts Payable"

    def test_split_in_a_lower_slot(self):
        r = _pp(name1="Acme University", name2="Acme Medical Center/Acct Pay")
        assert r.name2 == "Acme Medical Center"
        assert r.name3 == "Accounts Payable"

    def test_name_block_full_leaves_the_value_intact(self):
        r = _pp(
            name1="Acme Corp/Acct Pay", name2="Oncology Research Unit",
            name3="Bioanalytical Methods Branch", name4="Cell Biology Group",
            name5="Mass Spectrometry Facility",
        )
        # Nowhere to put the desk — better a composite Name 1 than a lost one.
        assert r.name1 == "Acme Corp/Acct Pay"


class TestDeskOnlyStillCollapses:
    @pytest.mark.parametrize("value,expected", [
        ("Accounts Payable", "Accounts Payable"),
        ("Accounts Payable Department", "Accounts Payable"),
        ("Acct Pay", "Accounts Payable"),
        ("Accts Payable", "Accounts Payable"),
        ("A/P", "Accounts Payable"),
        ("AP Dept", "Accounts Payable"),
    ])
    def test_whole_field_normalised(self, value, expected):
        r = _pp(name1=value)
        assert r.name1 == expected
        assert not (r.name2 and r.name2.strip())


class TestGuards:
    @pytest.mark.parametrize("value", [
        "Coca-Cola Bottling Co",
        "AP Moller Maersk",
        "Acme Corp/Chemicals Division",
        "Applied Physics Laboratory",
    ])
    def test_never_split(self, value):
        r = _pp(name1=value)
        assert r.name1 == value
        assert not (r.name2 and r.name2.strip())


class TestAdminVocabulary:
    @pytest.mark.parametrize("value", [
        "Acct Pay", "Accts Pay", "Acct Payable", "Accts Payable",
    ])
    def test_clipped_spellings_are_admin_desks(self, value):
        assert is_admin_unit(value)

    @pytest.mark.parametrize("value", ["Acct Management", "Account Executive"])
    def test_other_acct_phrases_are_not(self, value):
        assert not is_admin_unit(value)


class TestASpaceIsEnoughOfASeparatorInName1:
    """Found by the golden set, 2026-08-29.

    `Wyss Inst Accounts Payable` is the same value as
    `McLaren HealthCare Corp/Acct Pay` with a space where the slash is. Without
    a delimiter the split never fired, the whole field matched as a desk, and
    the record shipped `Name 1 = "Accounts Payable"` — the worst outcome this
    rule exists to prevent, and the one its own comment names.
    """

    def test_the_organisation_survives_a_space_separated_desk(self):
        res = _pp(name1="Wyss Inst Accounts Payable")
        assert res.name1 == "Wyss Inst"

    def test_the_desk_is_dropped_when_another_slot_already_holds_it(self):
        # SAP repeats it routinely. Keeping the organisation and writing the
        # desk a second time would trade one defect for a duplicate.
        res = _pp(name1="Wyss Inst Accounts Payable", name2="Accounts Payable")
        assert res.name1 == "Wyss Inst"
        assert res.name2 == "Accounts Payable"
        assert not (res.name3 or "").strip()

    def test_the_desk_still_gets_a_slot_when_it_has_none(self):
        res = _pp(name1="Wyss Inst Accounts Payable")
        assert res.name2 == "Accounts Payable"

    @pytest.mark.parametrize("value", [
        "Genzyme Corp Accts Pay",
        "Acme Holdings Acct Payable",
        "Bruker Scientific Accounts Pay",
    ])
    def test_the_clipped_spellings_split_too(self, value):
        assert _pp(name1=value).name1 == value.rsplit(" ", 2)[0]


class TestASpaceIsNotEnoughInADepartmentSlot:
    """The other half of the same finding.

    Only Name 1 holds the organisation, which is what makes losing it
    catastrophic. In a department slot the words before the desk qualify it —
    `LSG Accts Payable` is the Life Science Group's accounts-payable desk —
    and splitting leaves `LSG` standing where a department belongs. That
    regression is what scoped the space split to Name 1.
    """

    def test_a_qualifier_is_not_an_organisation(self):
        res = _pp(name1="Bio-Rad Lab Inc", name2="LSG Accts Payable")
        assert res.name2 == "Accounts Payable"
        assert not (res.name3 or "").strip()

    def test_a_delimited_desk_still_splits_in_a_lower_slot(self):
        # A delimiter is evidence of two things in one field wherever it
        # appears; only the SPACE reading is Name-1-only.
        res = _pp(name1="Acme Corp", name2="Widgets Division/Acct Pay")
        assert res.name2 == "Widgets Division"

    def test_a_desk_only_value_still_collapses_in_name1(self):
        assert _pp(name1="Accounts Payable Department").name1 == "Accounts Payable"
