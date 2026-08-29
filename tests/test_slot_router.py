"""The slot router: an LLM lane that can only STOP a routing move.

`preprocess` moves a street value into the name block when its *wording* says
organisation or unit. Wording is not enough — measured on the golden set:

    Street 2 = "Scott & White Hospital Modul C"   -> took Name 1, and pushed
                                                     the real organisation to
                                                     Name 3
    Street 2 = "Davie Medical Ctr"                -> became Name 2
    Street 2 = "Comm. Bruker Scientific LLC"      -> became Name 3

The lane asks what kind of thing the value names, and the answer is used in one
direction only: to decline a move. Everything here pins that asymmetry, because
it is what makes an unavailable model, a refused answer, and a value the lane
never saw all mean the same thing — the deterministic behaviour, unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import (
    find_ambiguous_street_routings,
    preprocess_record,
)
from enrichment.slot_router import (
    belongs_in_name_block,
    classify_slot_values,
    verdict_key,
)

_BLANK = dict(
    name1=None, name2=None, name3=None, contact=None, email=None,
    street1=None, street2=None, street3=None,
)


class _LLM:
    """Answers from a table keyed by the value that appears in the prompt."""

    def __init__(self, answers: dict, *, raises: bool = False):
        self.answers = answers
        self.raises = raises
        self.prompts: list[str] = []

    async def extract_json(self, system_prompt, user_prompt, **kw):
        self.prompts.append(user_prompt)
        if self.raises:
            raise RuntimeError("service unavailable")
        for value, answer in self.answers.items():
            if value in user_prompt:
                return answer
        return {"kind": "organisation", "confidence": "low"}


# ---------------------------------------------------------------------------
# The candidate finder
# ---------------------------------------------------------------------------

class TestOnlyAClaimedValueIsACandidate:
    def test_a_value_a_predicate_would_move_is_offered(self):
        assert find_ambiguous_street_routings(
            street1="AIRPORT RD", street2="Scott & White Hospital Modul C",
        ) == [("Scott & White Hospital Modul C", "Street 2")]

    def test_a_plain_street_is_not_offered(self):
        """The lane second-guesses a move; it does not look for new ones."""
        assert find_ambiguous_street_routings(
            street1="IVY LANE", street2="3476 S UNIVERSITY DR",
        ) == []

    def test_the_order_is_a_function_of_the_record_not_of_dict_order(self):
        """`tests/test_determinism.py` requires the request sequence to be
        stable. Sorted by value, so two records with the same content ask the
        same questions in the same order."""
        forwards = find_ambiguous_street_routings(
            street2="Scott & White Hospital Modul C",
            street3="Comm. Bruker Scientific LLC",
        )
        backwards = find_ambiguous_street_routings(
            street3="Comm. Bruker Scientific LLC",
            street2="Scott & White Hospital Modul C",
        )
        assert forwards == backwards
        assert [v for v, _ in forwards] == sorted(v for v, _ in forwards)

    def test_the_same_value_twice_is_asked_once(self):
        found = find_ambiguous_street_routings(
            street2="Davie Medical Ctr", street3="Davie Medical Ctr",
        )
        assert len(found) == 1


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

class TestOnlyAnActionableVerdictIsKept:
    @pytest.mark.asyncio
    async def test_a_confident_verdict_is_returned(self):
        llm = _LLM({"Scott & White": {"kind": "building", "confidence": "high"}})
        out = await classify_slot_values(
            llm, [("Scott & White Hospital Modul C", "Street 2")],
        )
        assert out == {"scott & white hospital modul c": "building"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [
        {"kind": "building", "confidence": "low"},   # the prompt says discard
        {"kind": "warehouse", "confidence": "high"},  # a label we do not know
        {"kind": "", "confidence": "high"},
        {},
    ])
    async def test_anything_else_is_no_answer(self, answer):
        llm = _LLM({"Scott & White": answer})
        out = await classify_slot_values(
            llm, [("Scott & White Hospital Modul C", "Street 2")],
        )
        assert out == {}

    @pytest.mark.asyncio
    async def test_a_failed_call_is_no_answer(self):
        llm = _LLM({}, raises=True)
        out = await classify_slot_values(llm, [("Anything Ltd", "Street 2")])
        assert out == {}

    @pytest.mark.asyncio
    async def test_no_candidates_makes_no_calls(self):
        llm = _LLM({})
        assert await classify_slot_values(llm, []) == {}
        assert llm.prompts == []

    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_record_context(self):
        llm = _LLM({})
        await classify_slot_values(
            llm, [("Davie Medical Ctr", "Street 2")],
            organisation="HCA Florida University Hospital", city="DAVIE",
        )
        assert "HCA Florida University Hospital" in llm.prompts[0]
        assert "DAVIE" in llm.prompts[0]
        assert "Street 2" in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_missing_context_does_not_render_none(self):
        llm = _LLM({})
        await classify_slot_values(llm, [("Acme Ltd", "Street 2")])
        assert "None" not in llm.prompts[0]


class TestTheVerdictKeySurvivesTidying:
    @pytest.mark.parametrize("written", [
        "Davie Medical Ctr", "  Davie   Medical Ctr ", "DAVIE MEDICAL CTR",
    ])
    def test_the_same_value_written_differently_finds_its_verdict(self, written):
        verdicts = {verdict_key("Davie Medical Ctr"): "building"}
        assert belongs_in_name_block(written, verdicts) is False


# ---------------------------------------------------------------------------
# The asymmetry
# ---------------------------------------------------------------------------

class TestSilenceMeansTheDeterministicBehaviour:
    @pytest.mark.parametrize("verdicts", [None, {}, {"something else": "building"}])
    def test_no_verdict_is_not_a_veto(self, verdicts):
        assert belongs_in_name_block("Davie Medical Ctr", verdicts) is True

    @pytest.mark.parametrize("kind", ["organisation", "unit"])
    def test_a_name_kind_is_not_a_veto(self, kind):
        assert belongs_in_name_block(
            "Acme Labs", {verdict_key("Acme Labs"): kind},
        ) is True

    @pytest.mark.parametrize("kind", ["building", "room", "street",
                                      "duplicate", "noise"])
    def test_every_other_kind_vetoes(self, kind):
        assert belongs_in_name_block(
            "Acme Labs", {verdict_key("Acme Labs"): kind},
        ) is False


# ---------------------------------------------------------------------------
# End to end through preprocess
# ---------------------------------------------------------------------------

class TestTheVetoReachesTheRouting:
    def test_a_building_no_longer_takes_name1(self):
        """13189969. Without the veto the hospital-shaped building becomes
        Name 1 and the real organisation is pushed down the block."""
        verdicts = {verdict_key("Scott & White Hospital Modul C"): "building"}
        res = preprocess_record(**{
            **_BLANK,
            "name1": "Texas A&M System Health Science Ctr",
            "name2": "Inst for Regenerative Medicine",
            "street1": "AIRPORT RD",
            "street2": "Scott & White Hospital Modul C",
        }, llm_slot_verdicts=verdicts)
        assert res.name1 == "Texas A&M System Health Science Ctr"
        assert "Scott & White" not in " ".join(
            v or "" for v in (res.name1, res.name2, res.name3, res.name4)
        )

    def test_without_the_verdict_the_old_behaviour_stands(self):
        res = preprocess_record(**{
            **_BLANK,
            "name1": "Texas A&M System Health Science Ctr",
            "name2": "Inst for Regenerative Medicine",
            "street1": "AIRPORT RD",
            "street2": "Scott & White Hospital Modul C",
        })
        block = " ".join(
            v or "" for v in (res.name1, res.name2, res.name3, res.name4)
        )
        assert "Scott & White" in block

    def test_a_unit_shaped_building_is_vetoed_too(self):
        """13335676. `Davie Medical Ctr` is claimed by the department
        predicate rather than the organisation one, so both call sites need
        the check."""
        verdicts = {verdict_key("Davie Medical Ctr"): "building"}
        res = preprocess_record(**{
            **_BLANK,
            "name1": "HCA Florida University Hospital",
            "street1": "3476 S UNIVERSITY DR",
            "street2": "Davie Medical Ctr",
        }, llm_slot_verdicts=verdicts)
        assert not res.name2

    def test_an_organisation_verdict_still_moves(self):
        """The lane subtracts only. A value the model agrees is an
        organisation routes exactly as it did before."""
        verdicts = {verdict_key("University of Miami Hospital"): "organisation"}
        res = preprocess_record(**{
            **_BLANK,
            "name1": "Department of Radiology",
            "street1": "1400 NW 12TH AVE",
            "street2": "University of Miami Hospital",
        }, llm_slot_verdicts=verdicts)
        block = " ".join(
            v or "" for v in (res.name1, res.name2, res.name3, res.name4)
        )
        assert "University of Miami Hospital" in block

    def test_the_vetoed_value_is_not_destroyed(self):
        """Declining the move leaves the value where it was. The lane never
        deletes content — a reviewer can still see it."""
        verdicts = {verdict_key("Davie Medical Ctr"): "building"}
        res = preprocess_record(**{
            **_BLANK,
            "name1": "HCA Florida University Hospital",
            "street1": "3476 S UNIVERSITY DR",
            "street2": "Davie Medical Ctr",
        }, llm_slot_verdicts=verdicts)
        assert res.street2 == "Davie Medical Ctr"
