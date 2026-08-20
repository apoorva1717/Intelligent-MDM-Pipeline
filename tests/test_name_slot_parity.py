"""Every name rule applies to every name slot, not just Name 1 / Name 2.

The rules in this file were each written against one or two specific slots
and later generalised to the whole block. The tests assert the generalised
behaviour at a LOWER slot than the one the rule was born on — that is the
part a future narrowing would break first.
"""

from __future__ import annotations

import pytest

from api.models import EnrichmentRecord
from api.output_columns import RESPONSE_COLUMNS
from enrichment.flags import compute_flags
from enrichment.issue_detection import detect_issues
from enrichment.preprocess import preprocess_record
from utils.name_slots import (
    ADJACENT_NAME_PAIRS,
    DEPT_SLOTS,
    NAME_SLOTS,
    RECORD_NAME_FIELDS,
)


def _pp(**kw):
    """Run preprocessing with only the name fields that matter."""
    return preprocess_record(
        name1=kw.get("name1"),
        name2=kw.get("name2"),
        name3=kw.get("name3"),
        contact=kw.get("contact"),
        email=kw.get("email"),
        street1=kw.get("street1"),
        street2=None,
        street3=None,
        name4=kw.get("name4"),
        name5=kw.get("name5"),
        llm_person_verdicts=kw.get("verdicts") or {},
    )


def _record(**columns) -> EnrichmentRecord:
    return EnrichmentRecord.model_validate({"Customer": "T1", **columns})


# ── The vocabulary itself ─────────────────────────────────────────────────

class TestSlotVocabulary:
    def test_every_slot_is_a_model_field_and_an_output_column(self):
        for slot, field in zip(NAME_SLOTS, RECORD_NAME_FIELDS):
            assert field in EnrichmentRecord.model_fields, field
            assert f"{slot}_enriched" in RESPONSE_COLUMNS, slot

    def test_dept_slots_are_the_block_below_name1(self):
        assert NAME_SLOTS[0] == "name1"
        assert DEPT_SLOTS == NAME_SLOTS[1:]

    def test_adjacent_pairs_cover_every_boundary(self):
        assert len(ADJACENT_NAME_PAIRS) == len(NAME_SLOTS) - 1
        assert ADJACENT_NAME_PAIRS[0] == ("name1", "name2")
        assert ADJACENT_NAME_PAIRS[-1] == (NAME_SLOTS[-2], NAME_SLOTS[-1])


# ── Preprocessing rules ───────────────────────────────────────────────────

class TestPreprocessAppliesToEverySlot:
    def test_co_attn_person_extracted_from_a_lower_slot(self):
        """UC 15's 5-case classifier was Name 2-only; a c/o prefix in Name 4
        routes exactly the same way."""
        res = _pp(
            name1="Stanford University",
            name2="Department of Chemistry",
            name3="NMR Facility",
            name4="Attn: Dr. Jane Smith",
        )
        assert res.contact == "Jane Smith"
        assert res.care_of == "Dr. Jane Smith"
        assert res.name4 is None

    def test_co_attn_email_extracted_from_a_lower_slot(self):
        res = _pp(
            name1="Stanford University",
            name2="Department of Chemistry",
            name5="c/o jsmith@stanford.edu",
        )
        assert res.email == "jsmith@stanford.edu"
        assert res.name5 is None

    def test_second_co_attn_payload_is_reported_not_dropped(self):
        """Two slots each yielding a c/o payload: the first wins and the
        loser is flagged rather than silently discarded."""
        res = _pp(
            name1="Stanford University",
            name2="c/o Acquisitions Manager",
            name3="c/o Facilities Director",
        )
        assert res.care_of == "Acquisitions Manager"
        assert "care-of-conflict" in res.flags

    def test_junk_stripped_from_name2(self):
        """The junk strip started on Name 3; Name 2 collects the same
        URL / phone / code residue."""
        res = _pp(
            name1="Stanford University",
            name2="Department of Chemistry 555-123-4567",
        )
        assert res.name2 == "Department of Chemistry"

    def test_junk_stripped_from_the_last_slot(self):
        res = _pp(
            name1="Stanford University",
            name2="Department of Chemistry",
            name5="Spectroscopy Core https://chem.stanford.edu",
            name3="NMR Facility",
            name4="Cryo-EM Suite",
        )
        assert res.name5 == "Spectroscopy Core"

    def test_duplicate_cleared_against_any_slot_above(self):
        """UC 12 compared adjacent slots only; a Name 5 duplicating Name 2
        is cleared just as a Name 3 duplicating Name 2 is."""
        res = _pp(
            name1="Stanford University",
            name2="Department of Chemistry",
            name3="NMR Facility",
            name4="Spectroscopy Core",
            name5="Department of Chemistry",
        )
        assert res.name5 is None
        assert res.name2 == "Department of Chemistry"

    def test_gap_in_the_block_is_packed_leftward(self):
        res = _pp(name1="Stanford University", name5="Department of Chemistry")
        assert res.name2 == "Department of Chemistry"
        assert res.name5 is None


# ── Issue detection ───────────────────────────────────────────────────────

class TestIssueDetectionAppliesToEverySlot:
    def test_continuation_detected_at_a_lower_boundary(self):
        """G1-NAME-001 was the Name 1 / Name 2 pair; the same continuation
        between Name 3 and Name 4 is the same defect."""
        rec = _record(**{
            "Name 1": "Stanford University",
            "Name 2": "School of Medicine",
            "Name 3": "Department of Molecular",
            "Name 4": "and Cellular Physiology",
        })
        assert "G1-NAME-001" in detect_issues(rec)

    def test_adjacent_duplicate_detected_at_the_last_boundary(self):
        rec = _record(**{
            "Name 1": "Stanford University",
            "Name 2": "School of Medicine",
            "Name 3": "Department of Genetics",
            "Name 4": "Smith Lab",
            "Name 5": "Smith Lab",
        })
        assert "G3-NAME-005" in detect_issues(rec)

    def test_blank_gap_detected_below_name2(self):
        rec = _record(**{
            "Name 1": "Stanford University",
            "Name 2": "Department of Genetics",
            "Name 3": "",
            "Name 4": "Smith Lab",
        })
        assert "G1-NAME-004" in detect_issues(rec)

    def test_department_in_a_lower_slot_is_not_reported_missing(self):
        """G2-NAME-012 read Name 2 alone, so a department in Name 3 with a
        blank Name 2 was reported as missing. It is not missing."""
        rec = _record(**{
            "Name 1": "Stanford University",
            "Name 2": "",
            "Name 3": "Department of Genetics",
        })
        assert "G2-NAME-012" not in detect_issues(rec)

    def test_abbreviated_unit_detected_in_the_last_slot(self):
        rec = _record(**{
            "Name 1": "Stanford University",
            "Name 5": "Cardinal Rsch Grp Dept",
        })
        assert "G5-NAME-002" in detect_issues(rec)


# ── Flags ─────────────────────────────────────────────────────────────────

class TestFlagScopeCoversEverySlot:
    @pytest.mark.parametrize("slot", DEPT_SLOTS)
    def test_unverified_inference_scopes_to_the_slot_tier3_wrote(self, slot):
        result = {
            "name1_enriched": "Stanford University",
            f"{slot}_enriched": "Department of Genetics",
            f"{slot}_original": None,
            f"{slot}_changed": True,
            "_ev_tier3_wrote": {slot},
        }
        compute_flags(result)
        assert "unverified-inference" in result["flag_codes"]
        assert result["flagged_fields"] == [slot]
