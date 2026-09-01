"""Every email a record states reaches the Email column.

Regression: Shell 13047466 carried "USE6-INVOICES@SHELL.COM" in Name 3,
"USG5-INVOICES@SHELL.COM" in Name 4 and "email invoices to:
USG5-Invoices@Shell.com" in a street line. UC 8 and UC 15 both kept the FIRST
address and abandoned the rest — UC 8 left its one in the name slot, where
nothing downstream looks for an email, and UC 15 cleared the slot without
writing anything at all. `email-conflict` was raised either way, telling a
reviewer two addresses disagreed while showing them one.

Both addresses are the record's own and the pipeline has no basis for choosing
between them, so both ship in the column that means "email" and the flag asks
a steward which one the mail is for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.flags import EMAIL_CONFLICT, compute_flags
from enrichment.preprocess import PreprocessResult, preprocess_record
from utils.domain_resolver import (
    EMAIL_SEPARATOR,
    email_domain,
    first_email,
    split_emails,
)


# ── The column format ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("a@x.com; b@y.com", ["a@x.com", "b@y.com"]),
    ("a@x.com, b@y.com", ["a@x.com", "b@y.com"]),
    ("a@x.com b@y.com", ["a@x.com", "b@y.com"]),
    ("<a@x.com>, b@y.com", ["a@x.com", "b@y.com"]),
    ("a@x.com", ["a@x.com"]),
    # A label the source wrote beside the address is not an address.
    ("email invoices to:  USG5-Invoices@Shell.com", ["USG5-Invoices@Shell.com"]),
    ("no email here", []),
    ("", []),
    (None, []),
])
def test_split_emails(value, expected):
    assert split_emails(value) == expected


def test_a_two_address_column_still_resolves_one_domain():
    """The consumers that need ONE address take the first. Without this a
    `rsplit("@")` over the whole string answers with the LAST one."""
    assert first_email("a@acme.com; b@other.com") == "a@acme.com"
    assert email_domain("a@acme.com; b@other.com") == "acme.com"


# ── Accumulating ─────────────────────────────────────────────────────────────

class TestAddEmail:
    def test_the_first_address_populates_an_empty_column(self):
        res = PreprocessResult()
        assert res.add_email("a@x.com") == "new"
        assert res.email == "a@x.com"

    def test_a_different_address_is_appended(self):
        res = PreprocessResult(email="a@x.com")
        assert res.add_email("b@y.com") == "conflict"
        assert res.email == f"a@x.com{EMAIL_SEPARATOR}b@y.com"

    def test_the_same_address_again_changes_nothing(self):
        res = PreprocessResult(email="a@x.com")
        assert res.add_email("A@X.COM") == "duplicate"
        assert res.email == "a@x.com"

    def test_a_third_address_joins_the_first_two(self):
        res = PreprocessResult(email="a@x.com")
        res.add_email("b@y.com")
        assert res.add_email("c@z.com") == "conflict"
        assert res.email == "a@x.com; b@y.com; c@z.com"

    def test_an_unparseable_existing_value_is_not_overwritten(self):
        # Whatever the record states is still what it states.
        res = PreprocessResult(email="see contract")
        assert res.add_email("a@x.com") == "conflict"
        assert res.email == "see contract; a@x.com"


# ── The record ───────────────────────────────────────────────────────────────

class TestShellRecord:
    """13047466 — two invoice addresses across three fields."""

    def _run(self):
        return preprocess_record(
            name1="Shell Global Solutions Us Inc",
            name2="Accounts Payable Dept",
            name3="USE6-INVOICES@SHELL.COM",
            name4="USG5-INVOICES@SHELL.COM",
            contact=None, email=None,
            street1="PO BOX 4282",
            street2="email invoices to:  USG5-Invoices@Shell.com",
            street3=None, city="HOUSTON", region="TX",
        )

    def test_both_addresses_ship_in_the_email_column(self):
        res = self._run()
        assert split_emails(res.email) == [
            "USE6-INVOICES@SHELL.COM", "USG5-INVOICES@SHELL.COM",
        ]

    def test_the_repeated_address_is_not_added_twice(self):
        # The street line restates Name 4's address; the column holds two.
        assert len(split_emails(self._run().email)) == 2

    def test_no_name_slot_is_left_holding_an_email(self):
        res = self._run()
        assert res.name3 is None
        assert res.name4 is None

    def test_the_label_left_behind_is_cleared(self):
        # "email invoices to:" with the address gone introduces nothing.
        assert self._run().street2 is None

    def test_the_conflict_is_flagged(self):
        assert "email-conflict" in self._run().flags


def test_two_addresses_in_one_field_are_both_taken():
    res = preprocess_record(
        "Acme Corp", "Dept", "Invoices ap@acme.com / ap2@acme.com",
        None, None, None, None, None,
    )
    assert split_emails(res.email) == ["ap@acme.com", "ap2@acme.com"]
    assert "email-conflict" in res.flags


def test_one_address_raises_no_conflict():
    res = preprocess_record(
        "Acme Corp", "Dept", "Research Lab jsmith@acme.com",
        None, None, None, None, None,
    )
    assert res.email == "jsmith@acme.com"
    assert res.name3 == "Research Lab"
    assert "email-conflict" not in res.flags


def test_the_flag_says_both_are_kept():
    result = {
        "record_id": "13047466",
        "name1_original": "Shell Global Solutions Us Inc",
        "name1_enriched": "Shell Global Solutions Us Inc",
        "email_enriched": "USE6-INVOICES@SHELL.COM; USG5-INVOICES@SHELL.COM",
        "domain": "shell.com",
        "_ev_email_conflict": True,
    }
    compute_flags(result)
    assert result["flag_codes"] == [EMAIL_CONFLICT]
    assert result["flagged_fields"] == ["email"]
    assert "more than one email address" in result["flag_reason"]
    assert "all of them kept in this column" in result["flag_reason"]
    # The clause separator stays the code separator.
    assert result["flag_reason"].count(";") == 0
