"""Tests for the Phase 2 dedup adjudicator (POST /api/dedup/cluster-block).

Most cases drive ``cluster_blocks`` directly with a scripted fake LLM so the
deterministic algorithm and the LLM-merge paths can be exercised precisely.
The signature parser and route wiring have their own focused tests.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from dedup.adjudicator import cluster_blocks
from dedup.llm import (
    DedupLLM,
    DedupLLMResult,
    _is_unsupported_reasoning_effort,
    parse_json_object,
)
from dedup.models import DedupRow
from dedup.signatures import build_signatures, normalize_key


# ---------------------------------------------------------------------------
# Scripted fake LLM
# ---------------------------------------------------------------------------

class ScriptedLLM:
    """Fake adjudicator LLM: a responder maps (system, user) -> raw JSON str.

    ``calls`` counts how many times the model was actually consulted, so tests
    can assert that deterministic decisions never hit the model.
    """

    model = "gpt-5.4-test"

    def __init__(self, responder):
        self._responder = responder
        self.calls = 0
        self.prompts: list[str] = []

    async def adjudicate(self, system_prompt, user_prompt, *, max_tokens=4000):
        self.calls += 1
        self.prompts.append(user_prompt)
        raw = self._responder(system_prompt, user_prompt)
        return DedupLLMResult(
            raw=raw,
            prompt_tokens=10,
            completion_tokens=10,
            latency_ms=1,
            model_version="gpt-5.4-test",
            error=None,
        )


def _row(row_id, name1, name2=None, *, block_id="b1", ror_id=None, **kw):
    return DedupRow(
        row_id=row_id, name1=name1, name2=name2, block_id=block_id, ror_id=ror_id, **kw
    )


def _by_row(response):
    return {r.row_id: r for r in response.rows}


# ---------------------------------------------------------------------------
# STEP A — signature collapsing
# ---------------------------------------------------------------------------

def test_normalize_key_collapses_punctuation_and_whitespace():
    assert normalize_key("  University   of  Stuttgart!! ") == "university of stuttgart"
    assert normalize_key("Universität") == "universitat"  # accent folded
    assert normalize_key(None) == ""
    assert normalize_key("") == ""


def test_build_signatures_collapses_identical_rows():
    rows = [_row(f"r{i}", "Acme GmbH", "Chemistry") for i in range(100)]
    sigs = build_signatures(rows)
    assert len(sigs) == 1
    assert len(sigs[0].row_ids) == 100
    assert sigs[0].signature_id == "s1"


@pytest.mark.asyncio
async def test_100_identical_rows_one_cluster_no_llm():
    """STEP A: 100 identical rows → 1 signature → 1 cluster of 100, no LLM."""
    rows = [_row(f"r{i}", "Acme GmbH", "Chemistry") for i in range(100)]
    llm = ScriptedLLM(lambda s, u: "{}")

    resp = await cluster_blocks(rows, llm)

    assert llm.calls == 0
    assert resp.summary.distinct_signatures == 1
    assert resp.summary.clusters == 1
    assert resp.summary.rows_clustered == 100
    assert resp.summary.llm_calls == 0
    cluster_ids = {r.cluster_id for r in resp.rows}
    assert cluster_ids == {1}
    assert all(r.routing == "cluster" for r in resp.rows)
    assert all(r.llm_flag is False for r in resp.rows)


# ---------------------------------------------------------------------------
# Two-level identity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_institution_different_department_not_merged():
    """Same Name1, different Name2 (Chemistry vs Mech Eng, same ROR) →
    two entities, NOT merged."""
    rows = [
        _row("r1", "University of Stuttgart", "Department of Chemistry", ror_id="ror:abc"),
        _row("r2", "University of Stuttgart", "Department of Mechanical Engineering", ror_id="ror:abc"),
    ]

    def responder(system, user):
        # LLM correctly keeps them as two distinct entities.
        return json.dumps({
            "entities": [
                {"signature_ids": ["s1"], "institution": "Uni Stuttgart",
                 "department": "Chemistry", "confidence": 0.9, "reasoning": "x"},
                {"signature_ids": ["s2"], "institution": "Uni Stuttgart",
                 "department": "Mech Eng", "confidence": 0.9, "reasoning": "y"},
            ],
            "uncertain_signature_ids": [],
        })

    llm = ScriptedLLM(responder)
    resp = await cluster_blocks(rows, llm)
    by = _by_row(resp)

    assert resp.summary.clusters == 0
    assert by["r1"].cluster_id is None and by["r1"].routing == "unique"
    assert by["r2"].cluster_id is None and by["r2"].routing == "unique"


# ---------------------------------------------------------------------------
# Cross-language / abbreviation merge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_language_abbreviation_merge():
    """'Dept of Mechanical Eng' vs 'Department of Mechanical Engineering' →
    one cluster."""
    rows = [
        _row("r1", "TU München", "Dept of Mechanical Eng"),
        _row("r2", "TU München", "Department of Mechanical Engineering"),
    ]

    def responder(system, user):
        return json.dumps({
            "entities": [
                {"signature_ids": ["s1", "s2"], "institution": "TUM",
                 "department": "Mechanical Engineering", "confidence": 0.95,
                 "reasoning": "abbreviation"},
            ],
            "uncertain_signature_ids": [],
        })

    llm = ScriptedLLM(responder)
    resp = await cluster_blocks(rows, llm)
    by = _by_row(resp)

    assert llm.calls == 1
    assert resp.summary.clusters == 1
    assert by["r1"].cluster_id == 1 == by["r2"].cluster_id
    assert by["r1"].routing == "cluster" and by["r2"].routing == "cluster"
    assert by["r1"].llm_flag is True and by["r2"].llm_flag is True
    assert by["r1"].confidence == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Name 2 asymmetry — deterministic, no LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_name2_asymmetry_never_merged_even_if_llm_says_merge():
    """Empty vs populated Name 2 → never merged. Deterministic: the
    empty/populated pair is never even sent to the LLM."""
    rows = [
        _row("r1", "Siemens AG", None),
        _row("r2", "Siemens AG", "Healthineers Division"),
    ]

    def responder(system, user):
        # Mock tries to merge everything into one entity.
        return json.dumps({
            "entities": [
                {"signature_ids": ["s1", "s2"], "institution": "Siemens",
                 "department": "", "confidence": 0.99, "reasoning": "merge!"},
            ],
            "uncertain_signature_ids": [],
        })

    llm = ScriptedLLM(responder)
    resp = await cluster_blocks(rows, llm)
    by = _by_row(resp)

    # Two singleton buckets → no LLM call needed at all.
    assert llm.calls == 0
    assert resp.summary.clusters == 0
    assert by["r1"].cluster_id is None
    assert by["r2"].cluster_id is None
    assert by["r1"].cluster_id != by["r2"].cluster_id or by["r1"].cluster_id is None


@pytest.mark.asyncio
async def test_name2_asymmetry_split_when_llm_violates_within_bucket():
    """If the LLM ever returns a mixed empty/populated entity, the split is
    enforced in code (safety net)."""
    # Two populated + one empty, so the populated bucket has a real call,
    # and we force the LLM to wrongly fold the empty one in via a crafted
    # response is impossible here (empty is in its own bucket). Instead this
    # exercises _enforce_name2_split directly.
    from dedup.adjudicator import Entity, _enforce_name2_split
    from dedup.signatures import Signature

    empty = Signature("s1", "siemens ag", "", "Siemens AG", "", None, ["r1"])
    pop = Signature("s2", "siemens ag", "healthineers", "Siemens AG", "Healthineers", None, ["r2"])
    mixed = Entity(entity_id="e1", signatures=[empty, pop])

    out, _ = _enforce_name2_split([mixed], next_index=2)
    assert len(out) == 2
    groups = [{s.signature_id for s in e.signatures} for e in out]
    assert {"s1"} in groups and {"s2"} in groups


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_singleton_is_unique_no_cluster():
    rows = [_row("r1", "Lone Institute", "Some Lab")]
    llm = ScriptedLLM(lambda s, u: "{}")
    resp = await cluster_blocks(rows, llm)
    by = _by_row(resp)

    assert llm.calls == 0
    assert by["r1"].cluster_id is None
    assert by["r1"].routing == "unique"
    assert by["r1"].llm_flag is False
    assert by["r1"].confidence is None
    assert resp.summary.rows_unique == 1


# ---------------------------------------------------------------------------
# Uncertain signature → manual_review, identical rows still cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uncertain_signature_routes_manual_review_but_still_clusters():
    rows = [
        _row("r1", "Ambiguous Org", "Unit X"),
        _row("r2", "Ambiguous Org", "Unit X"),   # identical to r1 → same sig
        _row("r3", "Other Org", "Unit Y"),
    ]

    def responder(system, user):
        # s1 (Org/Unit X) is uncertain; s2 (Other/Unit Y) is its own entity.
        return json.dumps({
            "entities": [
                {"signature_ids": ["s2"], "institution": "Other",
                 "department": "Unit Y", "confidence": 0.8, "reasoning": "z"},
            ],
            "uncertain_signature_ids": ["s1"],
        })

    llm = ScriptedLLM(responder)
    resp = await cluster_blocks(rows, llm)
    by = _by_row(resp)

    # r1 & r2 share the uncertain signature: they still cluster together,
    # but route to manual_review.
    assert by["r1"].routing == "manual_review"
    assert by["r2"].routing == "manual_review"
    assert by["r1"].cluster_id == by["r2"].cluster_id is not None
    # r3 is a lone entity → unique.
    assert by["r3"].routing == "unique"
    assert by["r3"].cluster_id is None
    assert resp.summary.rows_manual_review == 2


# ---------------------------------------------------------------------------
# Mode switch — N above threshold uses canonical assignment (Mode B)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_b_canonical_assignment_produces_correct_clusters():
    """N just above threshold triggers Mode B and still produces correct
    N-way clusters."""
    # 3 distinct signatures, two of which are the same entity. threshold=2 so
    # N=3 > threshold → Mode B.
    rows = [
        _row("r1", "Helmholtz Zentrum", "Institute A"),
        _row("r2", "Helmholtz Centre", "Institut A"),  # same entity as r1
        _row("r3", "Helmholtz Zentrum", "Institute B"),  # different dept
    ]

    import re
    sig_re = re.compile(r'"signature_id"\s*:\s*"([^"]+)"')
    ent_re = re.compile(r'"entity_id"\s*:\s*"([^"]+)"')

    def responder(system, user):
        # Only the candidate carries a signature_id in a Mode B prompt.
        candidate_id = sig_re.search(user).group(1)
        if candidate_id == "s2":
            # s2 ("Institut A") matches the first canonical entity (e1 = s1).
            target = ent_re.search(user).group(1)
            return json.dumps({"decision": "match", "matched_entity_id": target,
                               "confidence": 0.9, "reasoning": "same institute"})
        return json.dumps({"decision": "new", "matched_entity_id": None,
                           "confidence": 0.7, "reasoning": "different"})

    llm = ScriptedLLM(responder)
    resp = await cluster_blocks(rows, llm, threshold=2)
    by = _by_row(resp)

    # Mode B ran: at least 2 LLM calls (s2 and s3 compared).
    assert llm.calls >= 2
    # r1 and r2 should be the same cluster; r3 separate.
    assert by["r1"].cluster_id == by["r2"].cluster_id is not None
    assert by["r1"].llm_flag is True
    assert by["r3"].cluster_id != by["r1"].cluster_id
    assert by["r3"].routing == "unique"


@pytest.mark.asyncio
async def test_mode_b_respects_name2_boundary_without_llm():
    """In Mode B an empty-Name2 candidate is never compared to populated
    canonicals — it starts a new entity with no LLM call for that decision."""
    rows = [
        _row("r1", "Org One", "Dept A"),
        _row("r2", "Org Two", "Dept B"),
        _row("r3", "Org Three", None),  # empty Name2 — incompatible with all
    ]

    def responder(system, user):
        return json.dumps({"decision": "new", "matched_entity_id": None,
                           "confidence": 0.5, "reasoning": "new"})

    llm = ScriptedLLM(responder)
    resp = await cluster_blocks(rows, llm, threshold=2)
    by = _by_row(resp)
    # r3 incompatible with both populated canonicals → no clusters at all.
    assert resp.summary.clusters == 0
    assert by["r3"].routing == "unique"


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('here is the answer: {"a": 1} ok') == {"a": 1}
    assert parse_json_object("not json at all") is None
    assert parse_json_object("") is None
    assert parse_json_object("[1, 2, 3]") is None  # not an object


@pytest.mark.asyncio
async def test_malformed_llm_response_marks_signatures_uncertain():
    """A bad LLM call never fails the block — affected signatures go to
    manual_review."""
    rows = [
        _row("r1", "Org A", "Dept One"),
        _row("r2", "Org A", "Dept Two"),
    ]
    llm = ScriptedLLM(lambda s, u: "this is not json")
    resp = await cluster_blocks(rows, llm)

    assert resp.summary.errors == 1
    assert all(r.routing == "manual_review" for r in resp.rows)


@pytest.mark.asyncio
async def test_llm_error_result_marks_uncertain():
    """When the LLM client returns an error result (exhausted retries), the
    block still completes with the signatures flagged."""

    class ErrorLLM:
        model = "gpt-5.4-test"

        def __init__(self):
            self.calls = 0

        async def adjudicate(self, system, user, *, max_tokens=4000):
            self.calls += 1
            return DedupLLMResult(error="429 rate limited")

    rows = [_row("r1", "Org A", "Dept One"), _row("r2", "Org A", "Dept Two")]
    llm = ErrorLLM()
    resp = await cluster_blocks(rows, llm)

    assert resp.summary.errors == 1
    assert all(r.routing == "manual_review" for r in resp.rows)


# ---------------------------------------------------------------------------
# reasoning_effort fallback (the "everything is manual_review, errors>0" bug)
# ---------------------------------------------------------------------------

class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeUsage:
    prompt_tokens = 5
    completion_tokens = 5


class _FakeResponse:
    model = "gpt-5.4-2025"

    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    async def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if "reasoning_effort" in kwargs:
            raise RuntimeError(
                "Unrecognized request argument supplied: reasoning_effort"
            )
        return _FakeResponse('{"decision": "new"}')


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self)})()

    async def close(self):
        return None


def test_is_unsupported_reasoning_effort_detection():
    assert _is_unsupported_reasoning_effort(
        RuntimeError("Unrecognized request argument supplied: reasoning_effort")
    )
    assert _is_unsupported_reasoning_effort(
        TypeError("create() got an unexpected keyword argument 'reasoning_effort'")
    )
    # A plain rate-limit error is not a reasoning_effort problem.
    assert not _is_unsupported_reasoning_effort(RuntimeError("429 too many requests"))


@pytest.mark.asyncio
async def test_adjudicate_drops_reasoning_effort_and_recovers():
    """A deployment that rejects reasoning_effort should not sink the call:
    the param is dropped and the retry succeeds."""
    llm = DedupLLM()
    llm._client = _FakeClient()  # inject fake to avoid real network/credentials
    assert llm._use_reasoning_effort is True

    result = await llm.adjudicate("sys", "user", max_tokens=100)

    assert result.error is None
    assert result.raw == '{"decision": "new"}'
    assert llm._use_reasoning_effort is False  # disabled after the rejection
    # First attempt included the param (rejected), second omitted it (ok).
    assert "reasoning_effort" in llm._client.calls[0]
    assert "reasoning_effort" not in llm._client.calls[1]


# ---------------------------------------------------------------------------
# Block derivation + multi-block independence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocks_processed_independently_and_block_id_derived():
    rows = [
        # Block A (explicit id), identical pair → cluster.
        _row("r1", "Acme", "X", block_id="A"),
        _row("r2", "Acme", "X", block_id="A"),
        # Block with no id → derived from address; lone row → unique.
        DedupRow(row_id="r3", name1="Beta", name2="Y", block_id=None,
                 country="DE", postal_code="70173", street="Hauptstr", house_no="1"),
    ]
    llm = ScriptedLLM(lambda s, u: "{}")
    resp = await cluster_blocks(rows, llm)
    by = _by_row(resp)

    assert resp.summary.blocks == 2
    assert by["r1"].cluster_id == 1 == by["r2"].cluster_id
    assert by["r3"].cluster_id is None
    assert by["r3"].block_id.startswith("blk-")


# ---------------------------------------------------------------------------
# Route wiring (mock mode)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    from api.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_route_cluster_block_identical_rows(client):
    payload = {
        "rows": [
            {"row_id": f"r{i}", "block_id": "b1", "name1": "Acme GmbH",
             "name2": "Chemistry"}
            for i in range(5)
        ]
    }
    resp = await client.post("/api/dedup/cluster-block", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["distinct_signatures"] == 1
    assert data["summary"]["clusters"] == 1
    assert data["summary"]["rows_clustered"] == 5
    assert all(r["routing"] == "cluster" for r in data["rows"])
    assert all(r["prompt_version"] == "p2-dedup-v3" for r in data["rows"])


@pytest.mark.asyncio
async def test_route_empty_rows_rejected(client):
    resp = await client.post("/api/dedup/cluster-block", json={"rows": []})
    assert resp.status_code == 422
