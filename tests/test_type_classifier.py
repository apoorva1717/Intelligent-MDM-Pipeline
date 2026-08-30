"""The record_type LLM lane may speak over the two weakest rungs, and nothing
else.

`enrichment.classifier` ranks its evidence: ROR org types, then GLEIF entity
metadata, then a corporate legal-form suffix, then a keyword heuristic, then
`unknown`. Measured on the 200 labelled records, the bottom two rungs produced
70 of the 101 record_type errors -- 51 records nothing resolved at all, and 19
classified `research_institution` because a single word in the name said
"Research" or "Laboratory" (`Exxonmobil Research & Engineering Co` on
exxonmobil.com; `VA Medical Center` on va.gov).

The remaining 30 errors carry a verified ROR match and are deliberately out of
scope: the label set's disagreements there are judgement calls about its own
conventions (it labels `Scripps Research Institute` a company), and a lane that
overturned a registry to chase them would be re-litigating verified evidence.

So the rule these tests pin, the same shape as `enrichment.expansion_check`:

* asked only when the deterministic source is `keyword` or `unresolved`;
* a registry- or legal-form-decided type is never offered and never changed;
* no verdict, a low-confidence verdict, an `unknown` label, an unparseable
  label and a failed call are all one thing -- the deterministic answer stands.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _classify_record, _init_result, finalise
from enrichment.type_classifier import (
    OVERRIDABLE_SOURCES,
    VALID_TYPES,
    classify_record_type,
    may_override,
)
from tests.conftest import seed


class _LLM:
    """Returns one canned payload, and records that it was asked."""

    def __init__(self, payload=None, raises=False):
        self._payload = payload or {}
        self._raises = raises
        self.calls = 0

    async def extract_json(self, system, user, **kwargs):
        self.calls += 1
        if self._raises:
            raise RuntimeError("deployment unavailable")
        return self._payload

    async def aclose(self):
        pass


def _answer(record_type, confidence="high"):
    return {"record_type": record_type, "confidence": confidence,
            "reasoning": "because"}


# ---------------------------------------------------------------------------
# What the lane is allowed to speak over
# ---------------------------------------------------------------------------

class TestTheGate:
    @pytest.mark.parametrize("source", sorted(OVERRIDABLE_SOURCES))
    def test_the_two_weakest_rungs_are_overridable(self, source):
        assert may_override(source) is True

    @pytest.mark.parametrize("source", ["ror", "gleif", "legal_form"])
    def test_registry_and_legal_form_are_not(self, source):
        assert may_override(source) is False

    def test_an_unrecognised_source_is_not_overridable(self):
        """Fail closed: a rung added later is out of scope until it is opted
        in here deliberately."""
        assert may_override("something_new") is False
        assert may_override(None) is False


# ---------------------------------------------------------------------------
# Every failure mode is the same failure mode
# ---------------------------------------------------------------------------

class TestEveryFailureCollapsesToNoAnswer:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [
        {},                                    # nothing at all
        _answer("company", "low"),             # below the actionable band
        _answer("unknown"),                    # the model abstaining
        _answer("charity"),                    # a label outside the set
        {"record_type": "company"},            # no confidence stated
        {"confidence": "high"},                # no verdict stated
    ])
    async def test_no_usable_answer_returns_none(self, payload):
        assert await classify_record_type(
            _LLM(payload), name1="Acme Corp", domain="acme.com",
        ) is None

    @pytest.mark.asyncio
    async def test_a_failed_call_returns_none(self):
        assert await classify_record_type(
            _LLM(raises=True), name1="Acme Corp",
        ) is None

    @pytest.mark.asyncio
    async def test_a_blank_name_is_never_even_asked(self):
        llm = _LLM(_answer("company"))
        assert await classify_record_type(llm, name1="  ") is None
        assert llm.calls == 0


class TestAUsableAnswerIsReturned:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("label", sorted(VALID_TYPES))
    @pytest.mark.parametrize("confidence", ["high", "medium"])
    async def test_every_valid_label_at_an_actionable_confidence(
        self, label, confidence,
    ):
        assert await classify_record_type(
            _LLM(_answer(label, confidence)),
            name1="Exxonmobil Research & Engineering Co",
            domain="exxonmobil.com",
        ) == label


# ---------------------------------------------------------------------------
# How the verdict lands on the record
# ---------------------------------------------------------------------------

class TestTheVerdictOnTheRecord:
    def _result(self, name1, **fields):
        r = _init_result(EnrichmentRecord(
            record_id="t", country="US", name1=name1,
        ))
        return seed(r, **fields) if fields else r

    def test_a_keyword_decision_is_overridden(self):
        """`Zoetis Reference Laboratory Cincinnati` trips the keyword
        heuristic on "Laboratory". It is a company site, on
        zoetisdiagnostics.com."""
        r = self._result("Zoetis Reference Laboratory Cincinnati")
        _classify_record(r, llm_record_type="company")
        assert r["record_type"] == "company"
        assert r["record_type_source"] == "llm"

    def test_an_unresolved_record_is_decided(self):
        r = self._result("Sacramento County PH Lab")
        _classify_record(r, llm_record_type="government")
        assert r["record_type"] == "government"
        assert r["record_type_source"] == "llm"

    def test_a_ror_decision_is_left_alone(self):
        """The registry outranks the lane. ROR said research; the lane is not
        consulted and could not win if it were."""
        r = self._result("Stanford University")
        r["_ror_is_research"] = True
        _classify_record(r, llm_record_type="company")
        assert r["record_type"] == "research_institution"
        assert r["record_type_source"] == "ror"

    def test_no_verdict_leaves_the_deterministic_answer(self):
        """The control: without the lane, exactly today's behaviour."""
        r = self._result("Zoetis Reference Laboratory Cincinnati")
        _classify_record(r, llm_record_type=None)
        assert r["record_type"] == "research_institution"
        assert r["record_type_source"] == "keyword"

    def test_a_legal_form_decision_is_left_alone(self):
        """`Exxonmobil Research & Engineering Co` reaches `company` at the
        legal-form rung, above the keyword. The lane is not consulted."""
        r = self._result("Exxonmobil Research & Engineering Co")
        _classify_record(r, llm_record_type="government")
        assert r["record_type"] == "company"
        assert r["record_type_source"] == "legal_form"

    def test_the_type_survives_a_full_finalise(self):
        r = self._result("Sacramento County PH Lab")
        out = finalise(r, time.monotonic(), llm_record_type="government")
        assert out["record_type"] == "government"

    def test_finalise_without_a_verdict_is_unchanged(self):
        r = self._result("Sacramento County PH Lab")
        out = finalise(r, time.monotonic())
        assert out["record_type"] == "unknown"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestThePromptCarriesNoRunState:
    @pytest.mark.asyncio
    async def test_the_same_record_renders_the_same_prompt(self):
        """No clock, no run id, no record id -- two calls for one record must
        be byte-identical, which is what makes the evidence cache a pure
        function of the request."""
        seen = []

        class _Capture(_LLM):
            async def extract_json(self, system, user, **kwargs):
                seen.append(user)
                return _answer("company")

        for _ in range(2):
            await classify_record_type(
                _Capture(), name1="Acme Corp", name2="Widgets Division",
                domain="acme.com", city="Peabody", state="MA", country="US",
            )
        assert seen[0] == seen[1]
        # And it carries only fields of the record itself — nothing the
        # evidence cache key would have to vary on.
        assert "Acme Corp" in seen[0] and "acme.com" in seen[0]
