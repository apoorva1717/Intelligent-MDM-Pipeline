"""Item 5: an address fragment extracted from a name field must not be written
to a street slot when it duplicates an existing street field (or that field +
House Number)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record, _duplicates_existing_street


def _pp(**kw):
    return preprocess_record(
        name1=kw.get("name1"), name2=kw.get("name2"), name3=kw.get("name3"),
        contact=None, email=None,
        street1=kw.get("street1"), street2=kw.get("street2"), street3=None,
        house_number=kw.get("house_number"),
    )


class TestFragmentDedup:
    def test_row179_duplicate_discarded(self):
        r = _pp(name1="Photon Labs 4200 Research Blvd Suite 210",
                street1="RESEARCH BLVD", house_number="4200")
        assert r.name1 == "Photon Labs"
        streets = [r.street1, r.street2, r.street3, r.street4, r.street5]
        # "4200 Research Blvd" was NOT re-added anywhere.
        assert not any(s and "4200 Research Blvd".lower() == s.lower() for s in streets)
        # The genuine suite fragment still lands in a slot.
        assert "Suite 210" in [s for s in streets if s]

    def test_genuinely_different_second_address_kept(self):
        r = _pp(name1="Acme 500 Oak Ave", street1="RESEARCH BLVD", house_number="4200")
        assert r.street2 == "500 Oak Ave"

    def test_dup_detector_matches_with_house_number(self):
        r = _pp(street1="RESEARCH BLVD", house_number="4200")
        assert _duplicates_existing_street("4200 Research Blvd", r, "4200") is True

    def test_dup_detector_matches_verbatim(self):
        r = _pp(street1="600 N Wolfe St")
        assert _duplicates_existing_street("600 N Wolfe St", r, None) is True

    def test_dup_detector_no_false_positive(self):
        r = _pp(street1="RESEARCH BLVD", house_number="4200")
        assert _duplicates_existing_street("500 Oak Ave", r, "4200") is False
