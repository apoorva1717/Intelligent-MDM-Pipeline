"""Org / department content that lands in a Street field is routed to the Name
fields, keeping only the real street address in the street.

Covers:
- Accounts Payable in a street field → Name 2 (department).
- A pipe-delimited org hierarchy + address ("Dept | Div | ... | 5100 Paint
  Branch Pkwy") → each org segment to a Name slot, address stays in the street.
- A comma-delimited value mixing org/department names with an address (incl. a
  German street) → org segments to Name slots, address stays in the street.
- Guard: a plain address ("51 Sleeper Street, 7th Floor"; "..., Boston, MA
  02210") is NEVER split.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record


def _pp(**kw):
    return preprocess_record(
        name1=kw.get("name1"), name2=kw.get("name2"), name3=kw.get("name3"),
        contact=None, email=None,
        street1=kw.get("street1"), street2=kw.get("street2"), street3=None,
    )


class TestAccountsPayableToName:
    def test_ap_in_street_moves_to_name2(self):
        r = _pp(name1="Acme University", street1="Accounts Payable")
        assert r.name2 == "Accounts Payable"
        assert not (r.street1 and r.street1.strip())

    def test_ap_abbrev_in_street_moves_to_name(self):
        r = _pp(name1="Acme University", street1="A/P Dept")
        assert (r.name2 or "").strip()
        assert not (r.street1 and r.street1.strip())


class TestPipeDelimitedStreet:
    def test_org_hierarchy_pipe_split(self):
        chain = (
            "Bioanalytical Methods Branch | Division of Bioanalytical Chemistry | "
            "Office of Regulatory Science (HFS-717) | Center for Food Safety and "
            "Applied Nutrition | U.S. Food and Drug Administration | "
            "5100 Paint Branch Pkwy"
        )
        r = _pp(name1=None, street1=chain)
        assert r.street1 == "5100 Paint Branch Pkwy"
        # The institution always takes Name 1, not the first (most granular) part.
        assert r.name1 == "U.S. Food and Drug Administration"
        # The departments were routed into the remaining name slots.
        names = [r.name2, r.name3, r.name4, r.name5]
        assert "Bioanalytical Methods Branch" in names
        assert "Division of Bioanalytical Chemistry" in names

    def test_name_overflow_raises_review_flag(self):
        # 6 org segments but only 5 name slots — the overflow must raise the
        # slots-full flag (→ flag_for_review) instead of being silently dropped.
        chain = (
            "Bioanalytical Methods Branch | Division of Bioanalytical Chemistry | "
            "Office of Regulatory Science (HFS-717) | Center for Food Safety and "
            "Applied Nutrition | Office of Analytics and Outreach | "
            "U.S. Food and Drug Administration | 5100 Paint Branch Pkwy"
        )
        r = _pp(name1=None, street1=chain)
        assert "name-slots-full" in r.flags


class TestCommaMixedStreet:
    def test_institute_faculty_german_street(self):
        r = _pp(
            name1=None,
            street1=(
                "Institute of Sustainable and Environmental Chemistry, "
                "Faculty of Sustainability, Scharnhorststraße 1 C13.217"
            ),
        )
        assert r.name1 == "Institute of Sustainable and Environmental Chemistry"
        assert r.name2 == "Faculty of Sustainability"
        assert r.street1 == "Scharnhorststraße 1 C13.217"

    def test_room_org_campus(self):
        r = _pp(
            name1="Durham University",
            street1="Room number: F107, Wolfson Research Institute, Queens Campus",
        )
        assert r.name2 == "Wolfson Research Institute"
        assert r.street1 == "Room number: F107"
        # A bare location fragment goes to the next street slot, not a name.
        assert r.street2 == "Queens Campus"


class TestPlainAddressNotSplit:
    def test_street_with_floor_not_split(self):
        r = _pp(name1="Acme", street1="51 Sleeper Street, 7th Floor")
        assert r.street1 == "51 Sleeper Street, 7th Floor"
        assert not (r.name2 and r.name2.strip())

    def test_street_city_state_zip_not_split(self):
        r = _pp(name1="Acme", street1="200 Clarendon Street, Boston, MA 02210")
        assert r.street1 == "200 Clarendon Street, Boston, MA 02210"
        assert not (r.name2 and r.name2.strip())
