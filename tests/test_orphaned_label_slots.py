"""UC 13 — a name slot left holding only the label of a value taken out of it.

The structured extractors pull an e-mail into the Email column and an
``Attn:`` into Contact, and leave behind the word that introduced it. Measured
on the golden set and the S2/S3 sample, that residue ships as an organisation
name:

    Name 2 = "email to: GlobalAPUS@celanese.com"  ->  Name 2 = "Email To:"
    Name 2 = "REF# , Attn: RECG"                  ->  Name 2 = "REF#"

The rule: when the whole remainder is a label, the slot is empty. When any of
the payload survives, it is not residue and the slot keeps it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import _strip_dept_slot_junk, preprocess_record

_BLANK = dict(
    name1=None, name2=None, name3=None, contact=None, email=None,
    street1=None, street2=None, street3=None,
)


class TestTheLabelGoesWithItsValue:
    @pytest.mark.parametrize("residue", [
        "email to:", "Email To:", "e-mail:", "EMAIL", "email address",
        "REF#", "Ref#", "REF# ,", "Ref No.", "reference number",
        "Attn:", "ATTENTION", "c/o", "Care Of",
        "Mail To:", "ship to", "Bill To:", "remit to",
        "Contact:", "contact name", "Phone", "Tel.", "FAX",
    ])
    def test_a_label_with_nothing_left_empties_the_slot(self, residue):
        assert _strip_dept_slot_junk(residue) is None

    @pytest.mark.parametrize("kept", [
        # Still carrying a payload: the extractors have first claim, and
        # whatever they leave is not this rule's business.
        "Attn: Receiving Dock",
        "Contact Lens Division",
        "Reference Laboratory",
        "IDEXX Reference Laboratories",
        "Email Marketing Group",
        "Phone Systems Inc",
        "Fax Services of Ohio",
        # A real unit that merely mentions a label word.
        "Department of Contact Tracing",
    ])
    def test_a_label_with_a_payload_is_left_alone(self, kept):
        assert _strip_dept_slot_junk(kept) == kept


class TestTheMeasuredRecords:
    def test_the_celanese_email_label(self):
        """13158369. The address reaches the Email column; the label does not
        stay behind as Name 2."""
        res = preprocess_record(**{
            **_BLANK,
            "name1": "Celanese",
            "name2": "email to: GlobalAPUS@celanese.com",
        })
        assert res.name1 == "Celanese"
        assert not res.name2
        assert res.email == "GlobalAPUS@celanese.com"

    def test_the_merck_ref_label(self):
        """13342226 / 13342227. `Attn: RECG` reaches Contact; `REF#` does not
        stay behind as Name 2."""
        res = preprocess_record(**{
            **_BLANK,
            "name1": "Merck Sharp & Dohme Corp.",
            "name2": "REF# , Attn: RECG",
            "street1": "901 CALIFORNIA AVE",
        })
        assert res.name1 == "Merck Sharp & Dohme Corp."
        assert not res.name2
        assert res.contact == "RECG"


class TestName1IsNotTouched:
    """The rule is scoped to the slots below Name 1, like the rest of UC 13.
    Name 1 is the identity the record supplied: UC 10 flags a Name 1 that says
    nothing, rather than deleting it and leaving the record nameless."""

    def test_a_label_in_name1_survives_preprocessing(self):
        res = preprocess_record(**{**_BLANK, "name1": "REF#", "name2": "Acme Labs"})
        assert res.name1 == "REF#"
