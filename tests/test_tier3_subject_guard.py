"""Tier 3 must not answer a POPULATED department slot with a different unit.

The defect this pins, from record 13102674 of the government-labs batch:

    Name 2 in : "Edwards Air Force Base"
    Name 2 out: "412th Test Wing"      provenance llm:provisional
                                       flag       unverified-inference

The 412th Test Wing is the host wing at Edwards AFB, so the answer is true of
the world and stated nowhere in the record. Tier 3 read it out of the model's
training data and overwrote what the source system said. `unverified-inference`
flagged the row, but the invented value was already sitting in the output
field — and a flag is not a substitute for not writing it.

Name 1 has been guarded against exactly this since Fix 2
(`canonical_preserves_identity`, "never let the LLM swap name1 for a different
entity"). The department slots took every non-empty suggestion unconditionally.
These tests pin the missing half:

* a populated slot accepts only a re-wording of the unit it already names;
* a rejected suggestion leaves the INPUT value standing, with input rather
  than llm provenance — the same end state as Tier 3 declining to answer;
* a BLANK slot is untouched by the guard: inventing into one is what the
  prompt asks for, and finalise §6c already governs it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _apply_tier3, _init_result
from enrichment.tier2_canonical import subject_preserved
from enrichment.tier3_llm import Tier3Result


def _result(**originals):
    """The real result object `_apply_tier3` receives, with the name slots
    holding the values the source system supplied."""
    return _init_result(EnrichmentRecord(
        record_id="13102674", country="US", **originals,
    ))


class TestSubjectPreserved:
    @pytest.mark.parametrize("original,suggestion", [
        ("Engineering", "Department of Engineering"),
        ("Neuroscience", "Department of Neuroscience"),
        ("Biochem", "Department of Biochemistry"),
        ("Cardiology", "Division of Cardiology"),
        # A bare building/room code the record stapled on is droppable —
        # the same allowance Tier 2's medium-confidence check makes.
        ("Marine Biology, OCSB", "Department of Marine Biology"),
        # Nothing to preserve: pure unit words carry no subject.
        ("Department", "Department of Physics"),
        (None, "Department of Physics"),
    ])
    def test_rewording_accepted(self, original, suggestion):
        assert subject_preserved(original, suggestion) is True

    @pytest.mark.parametrize("original,suggestion", [
        ("Edwards Air Force Base", "412th Test Wing"),
        ("Radiology", "Department of Neuroscience"),
        ("Office of Purchasing", "Procurement Services"),
        ("Accounts Payable", "Finance Department"),
        ("Quality Control", "Department of Quality Assurance"),
    ])
    def test_subject_swap_rejected(self, original, suggestion):
        assert subject_preserved(original, suggestion) is False

    def test_company_guard_would_reject_every_rewording(self):
        """Why this function exists rather than reusing the Name 1 guard.

        `canonical_preserves_identity` is tuned for company names: its
        addable vocabulary carries "University" and "Institute", not
        "Department" or "Division". Pointing it at a department slot would
        block the legitimate Tier 3 rewrite along with the invention.
        """
        from utils.text_utils import canonical_preserves_identity
        assert canonical_preserves_identity(
            "Engineering", "Department of Engineering",
        ) is False
        assert subject_preserved("Engineering", "Department of Engineering")


class TestApplyTier3DepartmentGuard:
    def test_edwards_afb_is_not_overwritten(self):
        result = _result(name1="US Air Force", name2="Edwards Air Force Base")
        _apply_tier3(result, Tier3Result(
            success=True,
            name2_suggestion="412th Test Wing",
            confidence="medium",
        ))
        # Not written, and not recorded as a Tier 3 authorship — finalise's
        # department passthrough restores the input value with `input`
        # provenance, which `_from_tier3` would otherwise contradict.
        assert result["name2_enriched"] is None
        assert "name2" not in result["_ev_tier3_wrote"]
        assert result.get("_name2_from_tier3") is not True

    def test_rewording_of_populated_slot_still_accepted(self):
        result = _result(name1="Rice University", name2="Biochem")
        _apply_tier3(result, Tier3Result(
            success=True,
            name2_suggestion="Department of Biochemistry",
            confidence="medium",
        ))
        assert result["name2_enriched"] == "Department of Biochemistry"
        assert "name2" in result["_ev_tier3_wrote"]

    def test_blank_slot_is_not_guarded(self):
        """The guard is about contradicting the record, not about inventing.

        A blank slot states nothing to contradict; finalise §6c is what
        decides whether the guess survives.
        """
        result = _result(name1="Harvard Medical School", name2=None)
        _apply_tier3(result, Tier3Result(
            success=True,
            name2_suggestion="Department of Neuroscience",
            confidence="medium",
        ))
        assert result["name2_enriched"] == "Department of Neuroscience"
        assert result["_name2_from_tier3"] is True

    def test_guard_applies_to_every_department_slot(self):
        """A fabricated Name 4 is no more defensible than a fabricated Name 2."""
        result = _result(
            name1="US Air Force", name2="Edwards Air Force Base",
            name3="Flight Test Center", name4="Propulsion Branch",
        )
        _apply_tier3(result, Tier3Result(
            success=True,
            name2_suggestion="412th Test Wing",
            name3_suggestion="Air Force Test Center",
            name4_suggestion="Rocket Lab",
            confidence="medium",
        ))
        assert result["name2_enriched"] is None
        assert result["name3_enriched"] is None
        assert result["name4_enriched"] is None
        assert result["_ev_tier3_wrote"] == set()

    def test_name1_guard_is_unchanged(self):
        result = _result(name1="Iso Group Inc")
        _apply_tier3(result, Tier3Result(
            success=True,
            name1_suggestion="CoStar Group",
            confidence="high",
        ))
        assert result["name1_enriched"] is None
