"""ROR/LEI conflict routing — ``DEDUP_V2_ID_CONFLICT`` (change D).

v1 answers a hard-identifier conflict by exploding the entity into singletons
and flagging each one (dedup/adjudicator.py `_enforce_identity_split`). That is
the one outcome that cannot be right. Two records at one delivery point
carrying two different ROR ids is a finding — either they are the same
organisation and Phase 1 resolved one of them wrong, or they are different and
the merge was wrong — and the PAIR is the evidence for it. Split apart they
become two unremarkable unique rows and the finding is gone.

v2 keeps the entity and its Cluster ID, routes it to review, and names both ids
in the Reasoning.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from dedup.adjudicator import Entity, _enforce_identity_split  # noqa: E402
from dedup.signatures import Signature  # noqa: E402
from tests.dedup_v2_support import (  # noqa: E402
    V2_FLAGS,
    SpecOracleLLM,
    fixture_dedup_rows,
    load_fixture,
    run_clustering,
)


@pytest.fixture
def flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in V2_FLAGS:
        monkeypatch.setenv(flag, "true")


@pytest.fixture
def id_conflict_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only D, so the change is shown to stand on its own."""
    monkeypatch.setenv("DEDUP_V2_BLOCKING", "false")
    monkeypatch.setenv("DEDUP_V2_NAME2", "false")
    monkeypatch.setenv("DEDUP_V2_ID_CONFLICT", "true")


@pytest.fixture
def flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in V2_FLAGS:
        monkeypatch.setenv(flag, "false")


@pytest.fixture(scope="module")
def fixture() -> dict:
    return load_fixture()


def _conflicting_entity(
    left_provenance: str | None = None, right_provenance: str | None = None,
) -> list[Entity]:
    def sig(sid, name, ror, provenance):
        return Signature(
            signature_id=sid, norm_name1=name.lower(), norm_name2="",
            name1=name, name2="", ror_id=ror, row_ids=[sid],
            institution=name, ror_provenance=provenance,
        )

    return [Entity(entity_id="e1", signatures=[
        sig("r1", "Scripps", "https://ror.org/04v7hvq31", left_provenance),
        sig("r2", "Scripps Research Institute", "https://ror.org/02dxx6824",
            right_provenance),
    ])]


def test_the_entity_survives_the_conflict(id_conflict_only) -> None:
    entities, _index, fired = _enforce_identity_split(_conflicting_entity(), 2)
    assert len(entities) == 1, "the pair IS the finding; splitting it loses it"
    assert len(entities[0].signatures) == 2
    assert fired is False, "nothing was split"


def test_every_member_routes_to_review_with_both_ids_named(id_conflict_only) -> None:
    entities, _index, _fired = _enforce_identity_split(_conflicting_entity(), 2)
    signatures = entities[0].signatures
    assert all(sig.uncertain for sig in signatures)
    assert all(
        sig.merge_reasoning == "id conflict: ROR 04v7hvq31 vs 02dxx6824"
        for sig in signatures
    )


def test_the_ids_are_named_in_block_order_not_response_order(id_conflict_only) -> None:
    """The reason must read the same on every run.

    Two orderings could leak in: the order the model happened to list the
    signature ids, and — worse — set iteration order, since the v1 helper
    returns a set and hashing decides how it comes out. Both are re-imposed
    from the signature ids.
    """
    entities = _conflicting_entity()
    entities[0].signatures.reverse()
    result, _index, _fired = _enforce_identity_split(entities, 2)
    assert result[0].signatures[0].merge_reasoning == (
        "id conflict: ROR 04v7hvq31 vs 02dxx6824"
    )


def test_an_inferred_id_from_a_short_name_is_called_out(id_conflict_only) -> None:
    """A one-token Name 1 is a brand, and a resolver picking one member of a
    brand family is choosing rather than resolving. When the provenance says
    the id was not registry-verified, the conflict has a likely cause and the
    reviewer is told it."""
    entities, _index, _fired = _enforce_identity_split(
        _conflicting_entity(left_provenance="llm:provisional"), 2,
    )
    reason = entities[0].signatures[0].merge_reasoning
    assert reason.startswith("id conflict: ROR 04v7hvq31 vs 02dxx6824 — ")
    assert "was inferred (llm:provisional) from a 1-token name ('Scripps')" in reason


def test_a_verified_id_is_not_called_out(id_conflict_only) -> None:
    """Silence is the honest answer when the column says the id was verified."""
    entities, _index, _fired = _enforce_identity_split(
        _conflicting_entity(left_provenance="ror:verified"), 2,
    )
    assert entities[0].signatures[0].merge_reasoning == (
        "id conflict: ROR 04v7hvq31 vs 02dxx6824"
    )


def test_a_missing_provenance_is_not_called_out(id_conflict_only) -> None:
    """An absent provenance is not evidence of inference."""
    entities, _index, _fired = _enforce_identity_split(_conflicting_entity(None), 2)
    assert "inferred" not in (entities[0].signatures[0].merge_reasoning or "")


def test_the_flag_off_path_still_explodes_the_entity(flags_off) -> None:
    """v1's behaviour, unchanged and asserted, so the flag is a real switch."""
    entities, _index, fired = _enforce_identity_split(_conflicting_entity(), 2)
    assert fired is True
    assert len(entities) == 2, "v1 splits into singletons"
    assert all(len(ent.signatures) == 1 for ent in entities)
    assert all(
        (ent.signatures[0].merge_reasoning or "").startswith("Split: different")
        for ent in entities
    )


def test_scripps_activity_end_to_end(flags_on, fixture) -> None:
    """D's acceptance case, on the real batch.

    9060 Activity Rd, San Diego: "Scripps" carrying ROR 04v7hvq31 and "Scripps
    Research Institute" carrying 02dxx6824. One delivery point, one entity, two
    registry ids that disagree.
    """
    results, _summary = asyncio.run(
        run_clustering(fixture_dedup_rows(fixture), SpecOracleLLM(fixture))
    )
    left, right = results["13335883"], results["13336451"]
    assert left.cluster_id is not None and left.cluster_id == right.cluster_id
    assert left.link_id is not None and left.link_id == right.link_id
    assert left.routing == right.routing == "manual_review"
    assert left.reasoning == "id conflict: ROR 04v7hvq31 vs 02dxx6824"


def test_the_contradiction_guard_is_untouched(flags_on) -> None:
    """§4.4's whole-block demotion is not D's to change."""
    from dedup.adjudicator import _reasoning_disowns_membership

    entity = Entity(
        entity_id="e1",
        signatures=[
            Signature(signature_id="s1", norm_name1="a", norm_name2="", name1="A",
                      name2="", ror_id=None, row_ids=["r1"]),
            Signature(signature_id="s2", norm_name1="b", norm_name2="", name1="B",
                      name2="", ror_id=None, row_ids=["r2"]),
        ],
        reasoning="These should not be merged.",
    )
    assert _reasoning_disowns_membership([entity]) is True
