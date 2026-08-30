"""`FLAG_REVIEW_ON_LOW_CONFIDENCE` — capping the review queue, and its cost.

A core field at `low` confidence raises `flag_for_review` with no code
attached. That is the default and it is the behaviour every other rule in
`enrichment.flags` assumes.

A deployment that has to cap the queue can turn it off. These tests pin what
that does and, just as important, what it does NOT do:

* the flag stops being raised;
* `flag_reason` still carries the prose, so nothing leaves the record — only
  the review request;
* a substantive (non-advisory) code still queues the row, whatever the setting.

Measured on the golden set: flagged 46 -> 19, but SILENT failures 29 -> 47.
Precision barely moves; recall halves. The switch shortens the queue, it does
not improve the pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment import flags as F


@pytest.fixture
def cap(monkeypatch):
    def _set(enabled: bool):
        monkeypatch.setattr(
            F, "_low_confidence_raises_review", lambda: enabled,
        )
    return _set


class TestLowConfidenceAlone:
    def test_queues_a_review_by_default(self, cap):
        cap(True)
        out = F.render({}, low_confidence=["name1"])
        assert out["flag_for_review"] is True

    def test_does_not_when_the_cap_is_on(self, cap):
        cap(False)
        out = F.render({}, low_confidence=["name1"])
        assert out["flag_for_review"] is False

    def test_the_prose_survives_the_cap(self, cap):
        """The record keeps what it knows; only the request goes. A consumer
        reading `flag_reason` sees the same sentence either way."""
        cap(False)
        out = F.render({}, low_confidence=["name1"])
        assert out["flag_reason"]
        assert "name 1" in out["flag_reason"].lower()
        assert out["flag_low_confidence"] == ["name1"]


class TestASubstantiveCodeAlwaysQueues:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_cap_never_suppresses_a_code(self, cap, enabled):
        cap(enabled)
        out = F.render({F.ENTITY_SUPERSEDED: ["name1"]})
        assert out["flag_for_review"] is True

    @pytest.mark.parametrize("enabled", [True, False])
    def test_an_advisory_code_still_queues_nothing(self, cap, enabled):
        """`domain-unverified` was advisory before this switch existed and is
        unaffected by it — the two mechanisms are independent."""
        cap(enabled)
        out = F.render({F.DOMAIN_UNVERIFIED: ["domain"]})
        assert out["flag_for_review"] is False
        assert F.DOMAIN_UNVERIFIED in (out["flag_codes"] or [])

    @pytest.mark.parametrize("enabled", [True, False])
    def test_a_code_beside_low_confidence_still_queues(self, cap, enabled):
        cap(enabled)
        out = F.render(
            {F.UNVERIFIED_INFERENCE: ["name1"]}, low_confidence=["name2"],
        )
        assert out["flag_for_review"] is True


class TestTheDefaultIsUnchanged:
    def test_the_setting_defaults_to_raising(self):
        from config import Settings
        assert Settings().flag_review_on_low_confidence is True
