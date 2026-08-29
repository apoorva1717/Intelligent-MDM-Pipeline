"""Ticket 11 — aggregate a FUNNEL_PROBE jsonl into the per-gate loss table.

Input is the file written by ``enrichment/funnel_probe.py`` during a run of
``scripts/run_batch.py`` with ``FUNNEL_PROBE=true``. One ``enter`` event per
registry lookup; every later event with the same ``call`` id is a gate that
lookup passed or died at.

Usage::

    FUNNEL_PROBE=true FUNNEL_PROBE_OUT=logs/funnel.jsonl \
      .venv/Scripts/python.exe scripts/run_batch.py <xlsx> --out ... --json ...
    .venv/Scripts/python.exe .scratch/agentic-enrichment/scripts/aggregate_funnel.py \
      logs/funnel.jsonl [--run-json logs/run.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

# Terminal gates, in funnel order. Everything else is an intermediate note.
ROR_ORDER = [
    "aff_accept",
    "aff_no_chosen",
    "aff_chosen_below_ror_threshold",
    "aff_local_rescore_reject",
    "aff_country_reject",
    "aff_short_name_reject",
    "query_no_items",
    "query_ambiguity_reject",
    "query_below_threshold",
    "query_short_name_reject",
    "query_accept",
    "frozen_miss",
    "error",
]
GLEIF_ORDER = [
    "exact_accept",
    "exact_no_verified_candidate",
    "fuzzy_no_completions",
    "fuzzy_no_verified_candidate",
    "fuzzy_accept",
    "frozen_miss",
    "error",
]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--run-json", default=None,
                    help="run_batch --json artefact, to join outcomes per record")
    args = ap.parse_args()

    events = load(Path(args.jsonl))
    by_call: dict[int, list[dict]] = collections.defaultdict(list)
    for e in events:
        by_call[e.get("call")].append(e)

    for registry, order in (("ror", ROR_ORDER), ("gleif", GLEIF_ORDER)):
        calls = {
            cid: evs for cid, evs in by_call.items()
            if evs and evs[0].get("registry") == registry
        }
        print(f"\n=== {registry.upper()} — {len(calls)} lookups "
              f"({sum(1 for e in calls.values() if e[0].get('cache_hit'))} memory-cache hits) ===")
        # Two views. `seen` = lookups in which the gate fired at least once
        # (a lookup can hit `aff_no_chosen` twice: raw, then acronym-expanded).
        # `terminal` = the gate the lookup actually ended on.
        seen: collections.Counter = collections.Counter()
        terminal: collections.Counter = collections.Counter()
        for evs in calls.values():
            gates = [e["gate"] for e in evs if e["gate"] != "enter"]
            for g in set(gates):
                seen[g] += 1
            terminal[gates[-1] if gates else "(no terminal event)"] += 1
        print(f"  {'gate':34s} {'lookups hitting':>15s} {'ended here':>11s}")
        for gate in order + sorted((set(seen) | set(terminal)) - set(order)):
            if gate in ("query_country_dropped",):
                continue
            if seen.get(gate) or terminal.get(gate):
                print(f"  {gate:34s} {seen.get(gate, 0):15d} "
                      f"{terminal.get(gate, 0):11d}")
        for gate in sorted(terminal):
            if gate not in order and gate != "query_country_dropped":
                pass
        if seen.get("query_country_dropped"):
            print(f"  {'query_country_dropped (note only)':34s} "
                  f"{seen['query_country_dropped']:15d}")

    # ── gate 3: the paired scores ────────────────────────────────────────
    pairs = [e for e in events if e["gate"] == "aff_local_rescore_reject"]
    print(f"\n=== GATE 3 — chosen by ROR, refused by the local rescore "
          f"({len(pairs)}) ===")
    for e in pairs:
        print(f"  ror={e['ror_score']:.3f}  local={e['local_score']:.3f}  "
              f"caps={','.join(e.get('caps') or [])}\n"
              f"      query={e['query']!r}\n"
              f"      ror chose={e['candidate']!r} ({e['candidate_id']})")

    # ── the guards inside the surviving rejections ───────────────────────
    caps = collections.Counter()
    for e in events:
        if e["gate"] in ("query_below_threshold", "aff_local_rescore_reject"):
            caps[tuple(e.get("caps") or ("(uncapped)",))] += 1
    print("\n=== which local guard capped the score ===")
    for k, v in caps.most_common():
        print(f"  {'+'.join(k):34s} {v:4d}")

    gl = collections.Counter()
    for e in events:
        for r in e.get("rejections") or ():
            gl[(e.get("strategy"), r.get("guard"))] += 1
    print("\n=== GLEIF candidate-level guard rejections ===")
    for (strategy, guard), v in sorted(gl.items()):
        print(f"  {strategy or '-':8s} {guard:28s} {v:4d}")

    if args.run_json:
        data = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
        got_ror = sum(1 for r in data["results"] if r.get("ror_id"))
        got_lei = sum(1 for r in data["results"] if r.get("lei_id"))
        n = len(data["results"])
        print(f"\n=== outcome ({n} records) ===")
        print(f"  ror_id populated {got_ror}   lei_id populated {got_lei}   "
              f"either {sum(1 for r in data['results'] if r.get('ror_id') or r.get('lei_id'))}")


if __name__ == "__main__":
    sys.exit(main())
