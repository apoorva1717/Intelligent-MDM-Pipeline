"""When Name 1 is only a person, the organisation must come from the record's
OWN street fields (not a name-slot leftover or an address fragment):

  1. Org in the street (comma form)  → Name 1 (not Name 2).
  2. A pure address with "University Road" + a UK postcode → stays in Street 1,
     never routed to a Name field.
  3. An institution acronym before the address (semicolon form) → Name 1.

Tested at the preprocessing layer against inline example strings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import (
    preprocess_record,
    _street_is_org_name,
    _segment_is_address,
    _segment_is_org,
    _split_semicolon_street,
)


def _pp(name1, street1, verdicts=None):
    return preprocess_record(
        name1=name1, name2=None, name3=None, contact=None, email=None,
        street1=street1, street2=None, street3=None,
        llm_person_verdicts=verdicts or {},
    )


class TestOrgFromStreetToName1:
    def test_comma_org_goes_to_name1_not_name2(self):
        # 90000286: "JOHN F FLOREK," + "Tufts University, 15 Ryan Dr"
        r = _pp("JOHN F FLOREK,", "Tufts University, 15 Ryan Dr",
                {"john f florek": "person"})
        assert r.contact == "John F Florek"
        assert r.name1 == "Tufts University"
        assert not (r.name2 and r.name2.strip())   # NOT parked in Name 2
        assert r.street1 == "15 Ryan Dr"

    def test_acronym_semicolon_org_goes_to_name1(self):
        # 90000029: "QINHENG ZHENG" + "UCSF; 600 16th Avenue"
        r = _pp("QINHENG ZHENG", "UCSF; 600 16th Avenue",
                {"qinheng zheng": "person"})
        assert r.contact == "Qinheng Zheng"
        assert r.name1 == "UCSF"
        assert not (r.name2 and r.name2.strip())
        assert r.street1 == "600 16th Avenue"


class TestPureAddressStaysInStreet:
    def test_uk_address_with_university_road_not_an_org(self):
        # 90000331: "MADHURIMA CHAUDHURI" + a pure UK address whose street is
        # "University Road" and whose tail is a UK postcode. Nothing may land
        # in a Name field.
        addr = "ASTER HOUSE, 2A UNIVERSITY ROAD, BELFAST BT7 1NH"
        r = _pp("MADHURIMA CHAUDHURI", addr, {"madhurima chaudhuri": "person"})
        assert r.contact == "Madhurima Chaudhuri"
        assert not (r.name1 and r.name1.strip())
        assert not (r.name2 and r.name2.strip())
        assert not (r.name3 and r.name3.strip())
        assert r.street1 == addr                    # untouched, stays in street

    def test_university_road_is_not_org(self):
        assert _street_is_org_name("ASTER HOUSE, 2A UNIVERSITY ROAD, BELFAST BT7 1NH") is False
        assert _segment_is_org("2A UNIVERSITY ROAD") is False

    def test_uk_postcode_is_address(self):
        assert _segment_is_address("BELFAST BT7 1NH") is True

    def test_real_institution_in_street_still_routes(self):
        # Guard against over-masking: a genuine institution in a street field
        # (no street-type suffix after "University") is still an org.
        assert _street_is_org_name("University of Miami Hospital") is True


class TestSemicolonSplit:
    def test_two_address_lines_not_split(self):
        # A semicolon between two address lines must NOT route anything to names.
        assert _split_semicolon_street("600 16th Ave; San Francisco, CA 94118") is None

    def test_org_before_address_splits(self):
        out = _split_semicolon_street("UCSF; 600 16th Avenue")
        assert out is not None
        address, name_parts, _ = out
        assert name_parts == ["UCSF"]
        assert address == "600 16th Avenue"


class TestCompactionGating:
    def test_bare_department_not_promoted_to_name1(self):
        # A person + a department (no institution) must NOT promote the dept to
        # Name 1 — Name 1 stays empty so the record flags for an org lookup.
        r = preprocess_record(
            name1="JANE SMITH", name2="Department of Chemistry", name3=None,
            contact=None, email=None, street1=None, street2=None, street3=None,
            llm_person_verdicts={"jane smith": "person"},
        )
        assert r.contact == "Jane Smith"
        assert not (r.name1 and r.name1.strip())
        assert r.name2 == "Department of Chemistry"
