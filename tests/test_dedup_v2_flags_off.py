"""Flags off, nothing moves: the v2 code path must reproduce v1 exactly.

Every v2 change is gated on ``DEDUP_V2_BLOCKING`` / ``DEDUP_V2_NAME2`` /
``DEDUP_V2_ID_CONFLICT``, all default-false. With all three off, the 200-row
stress fixture must cluster and route exactly as the recorded v1 run did.

The model is held constant by ``V1ReplayLLM`` (see tests/dedup_v2_support.py):
it answers with the very grouping the recorded ``Cluster ID`` column shows, so
the verdicts cannot drift and any difference in the output is this
repository's. What the test therefore pins is the deterministic half of the
pipeline — block derivation, signature keys, bucket order, the residue pass's
nomination and union-find, the guards, and emission.

Two columns are asserted, the two the change request names: ``Cluster ID`` and
``Routing``. ``Reasoning`` is model prose and ``Confidence`` is the model's
number; neither is recoverable by replay, and asserting a reconstruction of
them would be asserting the double rather than the code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from tests.dedup_v2_support import (  # noqa: E402
    V2_FLAGS,
    V1ReplayLLM,
    describe,
    fixture_dedup_rows,
    load_fixture,
    run_clustering,
)


@pytest.fixture
def flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three v2 flags explicitly off — never merely absent.

    Explicit beats absent here: a flag read with a truthy default would still
    pass an "unset" test and fail in production the moment the deployment
    stopped setting it.
    """
    for flag in V2_FLAGS:
        monkeypatch.setenv(flag, "false")


@pytest.fixture(scope="module")
def fixture() -> dict:
    return load_fixture()


@pytest.mark.asyncio
async def test_flags_off_reproduces_v1_cluster_and_routing(flags_off, fixture) -> None:
    rows = fixture_dedup_rows(fixture)
    assert len(rows) == len(fixture["order"]), "the binder dropped rows"

    results, _summary = await run_clustering(rows, V1ReplayLLM(fixture))

    mismatches: list[str] = []
    for row_id in fixture["order"]:
        expected = fixture["v1"][row_id]
        result = results.get(row_id)
        if result is None:
            mismatches.append(f"{row_id}: missing from the output entirely")
            continue
        if result.cluster_id != expected["Cluster ID"]:
            mismatches.append(
                f"{row_id}: Cluster ID {result.cluster_id!r} != v1 {expected['Cluster ID']!r}"
            )
        if result.routing != expected["Routing"]:
            mismatches.append(
                f"{row_id}: Routing {result.routing!r} != v1 {expected['Routing']!r}"
            )

    assert not mismatches, (
        f"{len(mismatches)} row(s) differ from the recorded v1 output:\n"
        + "\n".join(mismatches[:40])
        + ("\n  …" if len(mismatches) > 40 else "")
    )


@pytest.mark.asyncio
async def test_flags_off_reproduces_the_v1_cluster_membership(flags_off, fixture) -> None:
    """The same assertion stated as membership, so a failure names the group.

    A cluster id is a hash of its members, so a single row joining or leaving
    changes the id for everyone in the cluster. The per-row test above would
    then report the whole cluster as broken; this one says which cluster and
    who moved.
    """
    rows = fixture_dedup_rows(fixture)
    results, _summary = await run_clustering(rows, V1ReplayLLM(fixture))

    def membership(get) -> dict[str, frozenset[str]]:
        groups: dict[str, set[str]] = {}
        for row_id in fixture["order"]:
            cluster = get(row_id)
            if cluster:
                groups.setdefault(cluster, set()).add(row_id)
        return {cid: frozenset(members) for cid, members in groups.items()}

    expected = membership(lambda rid: fixture["v1"][rid]["Cluster ID"])
    actual = membership(
        lambda rid: results[rid].cluster_id if rid in results else None
    )

    expected_sets = set(expected.values())
    actual_sets = set(actual.values())
    lost = expected_sets - actual_sets
    gained = actual_sets - expected_sets
    report = ""
    for group in sorted(lost, key=sorted):
        report += "\nLOST v1 cluster " + ", ".join(sorted(group)) + "\n" + describe(results, sorted(group))
    for group in sorted(gained, key=sorted):
        report += "\nNEW cluster " + ", ".join(sorted(group)) + "\n" + describe(results, sorted(group))
    assert not lost and not gained, f"cluster membership changed with the flags off:{report}"


@pytest.mark.asyncio
async def test_flags_off_routing_totals_match_v1(flags_off, fixture) -> None:
    """The headline counts, so a regression shows up as one readable number."""
    rows = fixture_dedup_rows(fixture)
    results, _summary = await run_clustering(rows, V1ReplayLLM(fixture))

    def totals(routing_of) -> dict[str, int]:
        counts = {"cluster": 0, "unique": 0, "manual_review": 0}
        for row_id in fixture["order"]:
            routing = routing_of(row_id)
            if routing:
                counts[routing] += 1
        return counts

    assert totals(lambda rid: results[rid].routing if rid in results else None) == totals(
        lambda rid: fixture["v1"][rid]["Routing"]
    )
