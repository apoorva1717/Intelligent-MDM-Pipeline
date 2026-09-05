"""Run the 200-row dedup v2 fixture against the deployed model, and score it.

Every number in the v2 change report so far came from a test double — an
oracle that answers from the expectation tables, or an adversary that merges
everything. Those measure the deterministic machinery, which is what they are
for, and they say nothing at all about what the model does. This says that.

Usage::

    python tools/dedup_v2_real_model_run.py --record   # calls the deployment
    python tools/dedup_v2_real_model_run.py            # replays the recording

``--record`` writes every call to ``tests/fixtures/dedup_v2_llm_cache/``, which
is committed. The default mode replays it and refuses to call the model, so the
committed report can be reproduced exactly, and a prompt change surfaces as a
loud replay miss rather than as a quietly different measurement.

Sampling: ``DEDUP_REASONING_EFFORT`` is cleared so ``temperature=0.0`` is
actually sent (dedup/llm.py:228-237 makes the two mutually exclusive; with the
default "low" the deployment's own temperature applies instead).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

CACHE_DIR = _ROOT / "tests" / "fixtures" / "dedup_v2_llm_cache"
REPORT = _ROOT / "tests" / "fixtures" / "dedup_v2_real_model_report.json"


def _configure(record: bool) -> None:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    os.environ["ENV"] = "local"
    os.environ["MOCK_EXTERNAL_CALLS"] = "false"
    for flag in ("DEDUP_V2_BLOCKING", "DEDUP_V2_NAME2", "DEDUP_V2_ID_CONFLICT"):
        os.environ[flag] = "true"
    # temperature=0.0 is only sent while reasoning_effort is inactive.
    os.environ["DEDUP_REASONING_EFFORT"] = ""
    os.environ[CACHE_DIR_ENV] = str(CACHE_DIR)
    os.environ[CACHE_MODE_ENV] = "record" if record else "replay"


from dedup.cache import CACHE_DIR_ENV, CACHE_MODE_ENV  # noqa: E402


async def _run(record: bool) -> dict:
    from config import Settings
    from dedup.adjudicator import cluster_blocks
    from dedup.cache import wrap_if_enabled
    from dedup.llm import DedupLLM
    from tests.dedup_v2_support import fixture_dedup_rows, load_fixture

    fixture = load_fixture()
    rows = fixture_dedup_rows(fixture)
    llm = wrap_if_enabled(DedupLLM(Settings()))
    response = await cluster_blocks(rows, llm, settings=Settings())
    await llm.aclose()
    results = {row.row_id: row for row in response.rows}
    return {
        "fixture": fixture,
        "results": results,
        "summary": response.summary,
        "hits": getattr(llm, "hits", 0),
        "misses": getattr(llm, "misses", 0),
    }


def _oracle_run() -> dict:
    """The same fixture under the oracle, for the side-by-side."""
    from tests.dedup_v2_support import (
        SpecOracleLLM, fixture_dedup_rows, load_fixture, run_clustering,
    )

    fixture = load_fixture()
    rows = fixture_dedup_rows(fixture)
    results, summary = asyncio.run(run_clustering(rows, SpecOracleLLM(fixture)))
    return {"results": results, "summary": summary}


def _score(results: dict) -> dict:
    from tests.dedup_v2_support import (
        MODEL_JUDGEMENT, MUST_LINK, MUST_LINK_FOR_REVIEW, MUST_MERGE,
        MUST_NOT_MERGE, XFAIL,
    )

    def cluster(row_id):
        row = results.get(row_id)
        return None if row is None else row.cluster_id

    report: dict = {"merge": {}, "not_merge": {}, "link": {}, "link_for_review": {},
                    "xfail": {}, "model_judgement": {}}

    from tests.dedup_v2_support import ADDRESS_LESS_MERGE_GROUPS

    for group, ids in MUST_MERGE.items():
        clusters = {cluster(i) for i in ids}
        merged = bool(len(clusters) == 1 and None not in clusters)
        if group in ADDRESS_LESS_MERGE_GROUPS:
            routings = {results[i].routing for i in ids if i in results}
            merged = merged and routings == {"manual_review"}
        report["merge"][group] = merged

    for label, anchors, forbidden in MUST_NOT_MERGE:
        anchor_clusters = {cluster(i) for i in anchors} - {None}
        # Anchor against forbidden only — see test_must_not_merge for why.
        bad = [i for i in forbidden if cluster(i) and cluster(i) in anchor_clusters]
        report["not_merge"][label] = not bad

    def link(row_id):
        row = results.get(row_id)
        return None if row is None else getattr(row, "link_id", None)

    for group, spec in MUST_LINK.items():
        sites_apart = True
        for index, site in enumerate(spec.sites):
            here = {cluster(i) for i in site} - {None}
            for other in spec.sites[index + 1:]:
                if here & ({cluster(i) for i in other} - {None}):
                    sites_apart = False
        links = {link(i) for i in spec.rows}
        routings = {results[i].routing for i in spec.rows if i in results}
        routing_ok = True
        if spec.review is True:
            routing_ok = routings == {"manual_review"}
        elif spec.review is False:
            routing_ok = "manual_review" not in routings
        report["link"][group] = {
            "sites_apart": sites_apart,
            "shared_link_id": bool(len(links) == 1 and None not in links),
            "routing": routing_ok,
        }

    for group, spec in MUST_LINK_FOR_REVIEW.items():
        ids = list(spec.rows)
        clusters = {cluster(i) for i in ids}
        links = {link(i) for i in ids}
        routings = {results[i].routing for i in ids if i in results}
        if spec.shares_cluster:
            carried = bool(len(clusters) == 1 and None not in clusters)
        else:
            carried = bool(len(links) == 1 and None not in links) and (
                len(clusters) == len(ids) or clusters == {None}
            )
        report["link_for_review"][group] = {
            "finding_carried": carried,
            "all_manual_review": routings == {"manual_review"},
        }

    for group, ids in XFAIL.items():
        clusters = {cluster(i) for i in ids}
        report["xfail"][group] = bool(len(clusters) == 1 and None not in clusters)

    for label, ids, _why in MODEL_JUDGEMENT:
        report["model_judgement"][label] = {
            i: {"cluster": cluster(i), "routing":
                results[i].routing if i in results else None,
                "reasoning": (results[i].reasoning if i in results else None)}
            for i in ids
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true",
                        help="call the deployment and write the cache")
    args = parser.parse_args()

    _configure(record=args.record)
    real = asyncio.run(_run(record=args.record))
    oracle = _oracle_run()

    real_report = _score(real["results"])
    oracle_report = _score(oracle["results"])

    payload = {
        "model": real["summary"].model_dump() if hasattr(real["summary"], "model_dump") else {},
        "cache": {"hits": real["hits"], "misses": real["misses"],
                  "dir": str(CACHE_DIR.relative_to(_ROOT))},
        "real": real_report,
        "oracle": oracle_report,
        "llm_calls": {"real": real["summary"].llm_calls,
                      "oracle": oracle["summary"].llm_calls},
        "routing": {
            "real": _routing(real["results"]),
            "oracle": _routing(oracle["results"]),
        },
        "differences": _differences(real, oracle, real_report, oracle_report),
    }
    REPORT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    _print(payload)
    return 0


def _routing(results: dict) -> dict:
    from collections import Counter

    return dict(Counter(row.routing for row in results.values()))


def _differences(real, oracle, real_report, oracle_report) -> list:
    """Every expectation group the two runs disagree about, with the reasoning.

    The reasoning string is the point: a group that fails only under the real
    model is a prompt problem, and the sentence the model wrote is the evidence
    for which part of the prompt it is.
    """
    from tests.dedup_v2_support import MUST_LINK, MUST_LINK_FOR_REVIEW, MUST_MERGE

    out = []
    for section in ("merge", "not_merge", "xfail"):
        for group, ok in real_report[section].items():
            if ok != oracle_report[section][group]:
                out.append(_detail(section, group, real, oracle))
    for group, verdict in real_report["link_for_review"].items():
        if verdict != oracle_report["link_for_review"][group]:
            out.append(_detail("link_for_review", group, real, oracle))
    for group, verdict in real_report["link"].items():
        if verdict != oracle_report["link"][group]:
            out.append(_detail("link", group, real, oracle))
    return out


def _detail(section, group, real, oracle) -> dict:
    from tests.dedup_v2_support import (
        MUST_LINK, MUST_LINK_FOR_REVIEW, MUST_MERGE, MUST_NOT_MERGE, XFAIL,
    )

    ids: list[str] = []
    if section == "merge":
        ids = MUST_MERGE[group]
    elif section == "xfail":
        ids = XFAIL[group]
    elif section == "link_for_review":
        ids = list(MUST_LINK_FOR_REVIEW[group].rows)
    elif section == "link":
        ids = list(MUST_LINK[group].rows)
    else:
        for label, anchors, forbidden in MUST_NOT_MERGE:
            if label == group:
                ids = [*anchors, *forbidden]

    def snapshot(results):
        return {
            i: {
                "cluster": results[i].cluster_id if i in results else None,
                "routing": results[i].routing if i in results else None,
                "reasoning": results[i].reasoning if i in results else None,
            }
            for i in ids
        }

    return {
        "section": section, "group": group,
        "real": snapshot(real["results"]),
        "oracle": snapshot(oracle["results"]),
    }


def _print(payload: dict) -> None:
    def counts(section):
        values = payload["real"][section]
        total = len(values)
        if section in ("merge", "not_merge", "xfail"):
            passed = sum(1 for v in values.values() if v)
        else:
            passed = sum(1 for v in values.values() if all(
                x is True for x in v.values() if isinstance(x, bool)))
        return f"{passed}/{total}"

    print("=" * 72)
    print("REAL-MODEL RUN — 200-row dedup v2 fixture")
    print("=" * 72)
    print(f"cache: {payload['cache']['hits']} hits, {payload['cache']['misses']} misses"
          f"  ({payload['cache']['dir']})")
    print(f"LLM calls: real={payload['llm_calls']['real']} "
          f"oracle={payload['llm_calls']['oracle']}")
    print(f"routing real  : {payload['routing']['real']}")
    print(f"routing oracle: {payload['routing']['oracle']}")
    print()
    for section, label in (("merge", "MUST_MERGE"), ("not_merge", "MUST_NOT_MERGE"),
                           ("link", "MUST_LINK (sites apart)"),
                           ("link_for_review", "MUST_LINK_FOR_REVIEW"),
                           ("xfail", "XFAIL")):
        print(f"{label:26} real {counts(section)}")
    print()
    print("MODEL_JUDGEMENT")
    for label, rows in payload["real"]["model_judgement"].items():
        print(f"  {label}")
        for row_id, verdict in rows.items():
            print(f"    {row_id}: cluster={verdict['cluster']} routing={verdict['routing']}")
            if verdict["reasoning"]:
                print(f"        {verdict['reasoning'][:300]}")
    print()
    print(f"DIFFERENCES vs the oracle: {len(payload['differences'])}")
    for diff in payload["differences"]:
        print(f"  [{diff['section']}] {diff['group']}")
        for row_id, verdict in diff["real"].items():
            print(f"    {row_id}: cluster={verdict['cluster']} routing={verdict['routing']}")
            if verdict["reasoning"]:
                print(f"        REAL: {verdict['reasoning'][:400]}")
    print(f"\nfull report: {REPORT.relative_to(_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
