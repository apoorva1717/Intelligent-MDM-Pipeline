"""A person name in Name 1 (instead of a company/university) is moved to the
Contact field.

Covers the formats that previously slipped through: title + credentials,
"Last, First", and "Name, credentials" / "Name MD". The plain-name and
reordered/credentialed forms rely on the LLM person verdict the orchestrator
supplies via find_suspicious_plain_names → llm_classify_plain_names_async.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record, find_suspicious_plain_names


def _pp(name1, verdicts=None):
    return preprocess_record(
        name1=name1, name2=None, name3=None, contact=None, email=None,
        street1=None, street2=None, street3=None,
        llm_person_verdicts=verdicts or {},
    )


class TestPersonInName1:
    def test_title_prefix(self):
        r = _pp("Dr. Jane Smith")
        assert r.contact == "Dr. Jane Smith"
        assert not (r.name1 and r.name1.strip())

    def test_title_plus_credentials(self):
        r = _pp("Dr. Jane Smith, PhD")
        assert r.contact == "Dr. Jane Smith"
        assert not (r.name1 and r.name1.strip())

    def test_plain_name_with_verdict(self):
        r = _pp("John Anderson", {"john anderson": "person"})
        assert r.contact == "John Anderson"
        assert not (r.name1 and r.name1.strip())

    def test_last_first_reordered(self):
        r = _pp("Smith, John", {"john smith": "person"})
        assert r.contact == "John Smith"
        assert not (r.name1 and r.name1.strip())

    def test_name_with_credentials(self):
        r = _pp("John Anderson, PhD", {"john anderson": "person"})
        assert r.contact == "John Anderson"
        assert not (r.name1 and r.name1.strip())

    def test_name_md_suffix(self):
        r = _pp("Jane Smith MD", {"jane smith": "person"})
        assert r.contact == "Jane Smith"
        assert not (r.name1 and r.name1.strip())

    def test_real_institution_untouched(self):
        r = _pp("University of Florida")
        assert r.name1 == "University of Florida"
        assert not (r.contact and r.contact.strip())


class TestSuspiciousSurfacing:
    def test_last_first_surfaced_for_llm(self):
        assert "John Smith" in find_suspicious_plain_names("Smith, John", None, None)

    def test_credentialed_surfaced_for_llm(self):
        assert "John Anderson" in find_suspicious_plain_names("John Anderson, PhD", None, None)

    def test_institution_not_surfaced(self):
        assert find_suspicious_plain_names("University of Florida", None, None) == []


class TestAllCapsPersonNames:
    """Item 1: ALL-CAPS person names in Name 1 must be detected (case-insensitive)
    and normalised to title case before the classifier and the Contact write."""

    def test_all_caps_surfaced_titlecased(self):
        assert find_suspicious_plain_names("ALBERT KAKKIS", None, None) == ["Albert Kakkis"]

    def test_all_caps_two_word(self):
        assert "Sean Bailey" in find_suspicious_plain_names("SEAN BAILEY", None, None)

    def test_all_caps_with_middle_initial_and_trailing_comma(self):
        # "JOHN F FLOREK," → trailing comma stripped, title-cased.
        assert find_suspicious_plain_names("JOHN F FLOREK,", None, None) == ["John F Florek"]

    def test_all_caps_moved_to_contact_titlecased(self):
        r = _pp("ALBERT KAKKIS", {"albert kakkis": "person"})
        assert r.contact == "Albert Kakkis"
        assert not (r.name1 and r.name1.strip())

    def test_all_caps_trailing_comma_moved_to_contact(self):
        r = _pp("JOHN F FLOREK,", {"john f florek": "person"})
        assert r.contact == "John F Florek"
        assert not (r.name1 and r.name1.strip())

    def test_org_guard_preserved_all_caps(self):
        # ALL-CAPS org (contains "LABS") must NOT surface as a person.
        assert find_suspicious_plain_names("ACME LABS", None, None) == []

    def test_mc_surname_in_contact(self):
        # Item 9 interaction: an ALL-CAPS person contact keeps the Mc capital.
        r = _pp("KATHLEEN MCINTYRE", {"kathleen mcintyre": "person"})
        assert r.contact == "Kathleen McIntyre"

    def test_hyphenated_surname_in_contact(self):
        r = _pp("BRETT VIRGIN-DOWNEY", {"brett virgin-downey": "person"})
        assert r.contact == "Brett Virgin-Downey"
