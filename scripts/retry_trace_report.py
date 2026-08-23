"""Classify a RETRY_TRACE run into the four Stage 5 buckets.

Reads the JSON lines written by ``enrichment.trace.retry`` (see
``scripts/run_batch.py --retry-trace``) and prints the counter totals plus the
per-record table for every row whose Name 1 was authored by an LLM — the
population Stage 5 exists to rescue.

The four buckets are mutually exclusive and exhaustive over that population:

1. ``wiring``          — the retry was never invoked on that code path.
2. ``normalize_key``   — skipped because the "corrected" name is the queried
                         name modulo punctuation / case / accents.
3. ``guard``           — fired, and a guard refused the candidate.
4. ``registry_miss``   — fired, and the registry genuinely had no match.

Usage::

    python scripts/retry_trace_report.py logs/runs/A_retry_trace.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def bucket(trace: dict) -> str:
    if not trace.get("called"):
        return "1-wiring"
    if trace.get("skipped_reason") == "normalize_key_equal":
        return "2-normalize_key"
    if not trace.get("fired"):
        return f"skip:{trace.get('skipped_reason')}"
    if trace.get("hit"):
        return "0-hit"
    # Fired and missed. A guard rejection only explains the miss when the
    # rejected candidate was plausibly the right entity; the raw presence of
    # rejections does not, since a fulltext registry search returns unrelated
    # names on every query. Reported separately below.
    return "4-registry_miss"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trace", help="retry trace JSONL")
    ap.add_argument("--md", help="write the per-record table as markdown here")
    args = ap.parse_args()

    traces = [
        json.loads(line)
        for line in Path(args.trace).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    print(f"records traced            : {len(traces)}")
    print(f"retry reached (called)    : {sum(1 for t in traces if t['called'])}")
    print(f"retry_eligible            : {sum(1 for t in traces if t.get('eligible'))}")
    print(f"retry_fired               : {sum(1 for t in traces if t['fired'])}")
    print(f"retry_hit                 : {sum(1 for t in traces if t['hit'])}")
    print()
    print("retry_skipped_reason (all records):")
    for reason, n in collections.Counter(
        t["skipped_reason"] for t in traces
    ).most_common():
        print(f"  {str(reason):26s} {n}")
    print()
    print("retry_registry_queried:")
    print(
        "  ror   ",
        sum(1 for t in traces if "ror" in t["registries_queried"]),
    )
    print(
        "  gleif ",
        sum(1 for t in traces if "gleif" in t["registries_queried"]),
    )
    print()

    llm = [t for t in traces if (t.get("name1_provenance") or "").startswith("llm")]
    hits = [t for t in traces if t["hit"]]
    population = llm + [t for t in hits if t not in llm]
    print(f"LLM-authored Name 1 rows  : {len(llm)}")
    print(f"retry hits (now registry) : {len(hits)}")
    print()
    print("buckets over the LLM-changed population:")
    for name, n in collections.Counter(bucket(t) for t in population).most_common():
        print(f"  {name:20s} {n}")

    rows = []
    for t in sorted(population, key=lambda t: (t.get("name1_original") or "")):
        guards = t["guard_rejections"]
        best = max(
            (g for g in guards if isinstance(g.get("score"), (int, float))),
            key=lambda g: g["score"], default=None,
        )
        rows.append({
            "input": t.get("name1_original"),
            "final": t.get("name1_final"),
            "prov": t.get("name1_provenance"),
            "bucket": bucket(t),
            "queried": ",".join(t["registries_queried"]) or "-",
            "guards": len(guards),
            # ROR scores on 0-1, GLEIF's name verification on 0-100 — render
            # each on its own scale rather than rounding one of them away.
            "best_reject": (
                f"{best['candidate']} ({best['score']:.3g}/{best['threshold']:.3g}"
                f", {best['guard']})" if best else "-"
            ),
            "id": t.get("ror_id") or t.get("lei_id") or "-",
        })

    if args.md:
        lines = [
            "| Input Name 1 | Final Name 1 | Provenance | Bucket | Registries queried "
            "| Guard rejections | Highest-scoring rejected candidate | Identifier |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                f"| {r['input']} | {r['final']} | `{r['prov']}` | {r['bucket']} "
                f"| {r['queried']} | {r['guards']} | {r['best_reject']} | {r['id']} |"
            )
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nmarkdown table -> {args.md}")
    else:
        for r in rows:
            print(r)


if __name__ == "__main__":
    main()
