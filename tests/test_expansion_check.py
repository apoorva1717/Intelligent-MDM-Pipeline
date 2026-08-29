"""The expansion check: an LLM lane that can only decline an expansion.

`finalise` expands organisational abbreviations in every non-registry output
name, unconditionally. The golden set holds both halves of the pair that shows
why one rule cannot serve:

    Bio-Rad Lab Inc                  -> Bio-Rad Laboratories, Inc.   expand
    Orange County Public Health Lab  -> ... Laboratory               expand
    Baytown Refinery Lab             -> Baytown Refinery Lab         keep
    Zoetis Ref Lab Cincinnati        -> Zoetis Ref Lab Cincinnati    keep

Nothing in the strings separates them, and neither do the registry or domain
signals. The difference is whether the thing named has a published name at
all — expanding an internal site designation invents one.

The lane is subtract-only, like the slot router: it can decline an expansion
and nothing else, so every failure mode lands on today's behaviour.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.expansion_check import (
    check_expansions,
    expansion_declined,
    expansion_key,
)
from enrichment.orchestrator import _init_result, finalise
from tests.conftest import fixture_evidence, seed


class _LLM:
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
        return {"verdict": "expand", "confidence": "high"}


# ---------------------------------------------------------------------------
# The lane
# ---------------------------------------------------------------------------

class TestOnlyAConfidentKeepDeclines:
    @pytest.mark.asyncio
    async def test_a_confident_keep_declines_the_expansion(self):
        llm = _LLM({"Baytown": {"verdict": "keep", "confidence": "high"}})
        out = await check_expansions(
            llm, [("Baytown Refinery Lab", "Baytown Refinery Laboratory",
                   "Name 2")],
        )
        assert out == {expansion_key("Baytown Refinery Lab")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [
        {"verdict": "keep", "confidence": "low"},     # the prompt says discard
        {"verdict": "expand", "confidence": "high"},  # the default anyway
        {"verdict": "maybe", "confidence": "high"},   # a label we do not know
        {},
    ])
    async def test_anything_else_leaves_the_expansion_standing(self, answer):
        llm = _LLM({"Baytown": answer})
        out = await check_expansions(
            llm, [("Baytown Refinery Lab", "Baytown Refinery Laboratory",
                   "Name 2")],
        )
        assert out == set()

    @pytest.mark.asyncio
    async def test_a_failed_call_leaves_the_expansion_standing(self):
        llm = _LLM({}, raises=True)
        out = await check_expansions(
            llm, [("Acme Lab", "Acme Laboratory", "Name 1")],
        )
        assert out == set()

    @pytest.mark.asyncio
    async def test_no_candidates_makes_no_calls(self):
        llm = _LLM({})
        assert await check_expansions(llm, []) == set()
        assert llm.prompts == []

    @pytest.mark.asyncio
    async def test_the_prompt_shows_both_forms_and_the_evidence(self):
        llm = _LLM({})
        await check_expansions(
            llm, [("Baytown Refinery Lab", "Baytown Refinery Laboratory",
                   "Name 2")],
            organisation="Exxonmobil Research & Engineering Co",
            city="BAYTOWN", domain="exxonmobil.com",
        )
        prompt = llm.prompts[0]
        assert "Baytown Refinery Lab" in prompt
        assert "Baytown Refinery Laboratory" in prompt
        assert "exxonmobil.com" in prompt
        assert "None" not in prompt

    @pytest.mark.asyncio
    async def test_the_same_value_twice_is_asked_once(self):
        llm = _LLM({})
        await check_expansions(llm, [
            ("Acme Lab", "Acme Laboratory", "Name 2"),
            ("Acme Lab", "Acme Laboratory", "Name 3"),
        ])
        assert len(llm.prompts) == 1


class TestSilenceMeansTodaysBehaviour:
    @pytest.mark.parametrize("declined", [None, set(), {"something else"}])
    def test_no_verdict_is_not_a_decline(self, declined):
        assert expansion_declined("Acme Lab", declined) is False

    @pytest.mark.parametrize("written", [
        "Baytown Refinery Lab", "  Baytown   Refinery Lab ",
        "BAYTOWN REFINERY LAB",
    ])
    def test_the_key_survives_tidying(self, written):
        declined = {expansion_key("Baytown Refinery Lab")}
        assert expansion_declined(written, declined) is True


# ---------------------------------------------------------------------------
# Through finalise
# ---------------------------------------------------------------------------

def _finalised(declined=None, **overrides):
    result = _init_result(EnrichmentRecord(record_id="E1", country="US",
                                           name1="Acme"))
    seed(result, fixture_evidence(), **overrides)
    return finalise(result, time.monotonic(),
                    expansion_declined_keys=declined)


class TestTheDeclineReachesTheOutput:
    def test_a_declined_name_keeps_the_abbreviation_it_arrived_with(self):
        r = _finalised(
            declined={expansion_key("Baytown Refinery Lab")},
            name1_enriched="Exxonmobil Research",
            name2_enriched="Baytown Refinery Lab",
        )
        assert r["name2_enriched"] == "Baytown Refinery Lab"

    def test_without_a_verdict_it_expands_as_it_always_has(self):
        r = _finalised(
            name1_enriched="Exxonmobil Research",
            name2_enriched="Baytown Refinery Lab",
        )
        assert r["name2_enriched"] == "Baytown Refinery Laboratory"

    def test_a_decline_is_scoped_to_the_value_it_names(self):
        """One slot declined must not silence the others."""
        r = _finalised(
            declined={expansion_key("Baytown Refinery Lab")},
            name1_enriched="Acme Lab",
            name2_enriched="Baytown Refinery Lab",
        )
        assert r["name1_enriched"] == "Acme Laboratory"
        assert r["name2_enriched"] == "Baytown Refinery Lab"

    def test_finalise_still_takes_two_arguments(self):
        """The keyword is optional, so every existing caller and test that
        calls `finalise(result, start)` is unaffected."""
        result = _init_result(EnrichmentRecord(record_id="E2", country="US",
                                               name1="Acme"))
        seed(result, fixture_evidence(), name1_enriched="Acme Lab")
        assert finalise(result, time.monotonic())["name1_enriched"] == (
            "Acme Laboratory"
        )
