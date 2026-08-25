"""Build the numbers behind `wikidata_lane_report.md` from two `run_batch.py` runs.

The lane is an A/B change, so the measurement is an A/B: one run with
``WIKIDATA_ENABLED=false`` and one with it on, over the same workbook, diffed
field by field. Anything the lane did shows up as a difference between the two.
Anything that differs for another reason — LLM nondeterminism on the tiers
below — shows up too, and is reported rather than hidden.

Two joins, both of which need explaining because the chemspeed workbook makes
them awkward:

* **Records** are paired by **batch position**. ``enrich_batch`` returns the
  batch in the order it received it, and the workbook carries no populated
  ``record_id`` — every row's is the empty string, so a keyed join would
  collapse all 100 rows into one.
* **Traces** are joined to records through ``dedup.signatures.normalize_key``
  of the trace's ``query`` (the string Tier 1 was given) against the input
  Name 1. Rows the join cannot place are counted and printed rather than
  dropped.

Only the **shipped** fields are compared — the ones `/enrich` and
`/enrich/file` actually emit. `tier_used`, `source`, `confidence` and
`enrichment_status` are ``exclude=True`` on the response model and are not in
either artefact; the unchanged-Name-1 state they would show is visible anyway
through the serialised ``name1_provenance`` scalar (`input:verified+web` vs
`input:low` — Provenance Scheme B, see :mod:`enrichment.confidence`).

Usage::

    python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
        --out logs/wd_baseline.xlsx --json logs/wd_baseline.json \
        --trace-out logs/wd_baseline_trace.jsonl --no-wikidata
    python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
        --out logs/wd_on.xlsx --json logs/wd_on.json \
        --trace-out logs/wd_on_trace.jsonl --wikidata-trace
    python scripts/wikidata_lane_report.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dedup.signatures import normalize_key  # noqa: E402

#: Counters the lane owns, in the order the README lists them.
COUNTERS: tuple[str, ...] = (
    "wikidata_queried",
    "wikidata_matched",
    "wikidata_no_match",
    "wikidata_ambiguous",
    "wikidata_unavailable",
    "wikidata_type_rejected",
    "wikidata_country_rejected",
    "wikidata_crosswalk_ror",
    "wikidata_crosswalk_lei",
    "wikidata_crosswalk_registry_hit",
    "wikidata_superseded_flagged",
    "wikidata_witness_only",
    "wikidata_domain_corroborated",
    "wikidata_domain_disagree",
)

#: Every shipped field an enrichment consumer reads.
COMPARED: tuple[str, ...] = (
    "name1_enriched", "name2_enriched", "name3_enriched", "name4_enriched",
    "name5_enriched", "operating_name", "operating_name_provenance",
    "domain", "department_domain", "ror_id", "lei_id", "record_type",
    "flag_for_review", "flag_codes", "flagged_fields", "flag_reason",
    "search_term_1", "search_term_2",
    "name1_provenance", "name2_provenance", "domain_provenance",
    "ror_id_provenance", "lei_id_provenance", "record_type_provenance",
)


def _load(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["summary"], data["results"]


def _input_names(path: Path) -> list[str]:
    """Input Name 1 per row, parsed by the SAME helpers the batch run uses, so
    the ordering and the row set cannot drift from the results being diffed."""
    from api.routes import _parse_xlsx, _rows_to_records

    _, rows = _parse_xlsx(path.read_bytes())
    return [(r.name1 or "") for r in _rows_to_records(rows)]


def _traces(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("step") == "wikidata":
            out.append(payload)
    return out


def _diff(before: dict, after: dict) -> dict[str, tuple]:
    return {
        field: (before.get(field), after.get(field))
        for field in COMPARED
        if before.get(field) != after.get(field)
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="docs/thesis/chemspeed_us_100.xlsx")
    ap.add_argument("--baseline", default="logs/wd_baseline.json")
    ap.add_argument("--after", default="logs/wd_on.json")
    ap.add_argument("--trace", default="logs/wd_on_trace.jsonl")
    args = ap.parse_args()

    base_summary, base_rows = _load(_ROOT / args.baseline)
    after_summary, after_rows = _load(_ROOT / args.after)
    traces = _traces(_ROOT / args.trace)
    names = _input_names(_ROOT / args.input)

    assert len(base_rows) == len(after_rows), "the two runs differ in length"

    # Trace → batch position, through the normalised Tier 1 query.
    by_key: dict[str, list[int]] = {}
    for i, name in enumerate(names[: len(base_rows)]):
        by_key.setdefault(normalize_key(name), []).append(i)
    placed: dict[int, dict] = {}
    unplaced: list[dict] = []
    for payload in traces:
        hits = by_key.get(normalize_key(payload.get("query") or ""))
        if hits and len(hits) == 1 and hits[0] not in placed:
            placed[hits[0]] = payload
        else:
            unplaced.append(payload)

    print("## Lane counters (run with WIKIDATA_ENABLED=true)\n")
    for key in COUNTERS:
        print(f"{key:38s} {after_summary.get(key, 0)}")
    print(f"{'trace lines':38s} {len(traces)}")
    print(f"{'traces joined to a row':38s} {len(placed)}")
    print(f"{'traces NOT joined':38s} {len(unplaced)}")

    print("\n## Baseline counters (lane off) — all must be zero\n")
    nonzero = {k: base_summary.get(k, 0) for k in COUNTERS if base_summary.get(k, 0)}
    print(nonzero or "all zero")

    print("\n## Outcomes over the trace\n")
    print(json.dumps(Counter(p["outcome"] for p in traces), indent=1))

    print("\n## Live API operations per record (the budget number)\n")
    ops = Counter(p.get("api_calls", 0) for p in traces)
    print(json.dumps({str(k): v for k, v in sorted(ops.items())}, indent=1))
    print(f"total operations: {sum(p.get('api_calls', 0) for p in traces)}")
    print(f"total HTTP requests (retries included): "
          f"{sum(p.get('http_requests', 0) for p in traces)}")
    print(f"records served entirely from fixtures: "
          f"{sum(1 for p in traces if p.get('from_fixture'))}")

    print("\n## Candidate rejection reasons (per candidate, not per record)\n")
    print(json.dumps(Counter(
        (c.get("rejected_by") or "survived")
        for p in traces for c in (p.get("candidates") or ())
    ), indent=1))

    print("\n## Matched rows\n")
    for i, payload in sorted(placed.items()):
        if payload["outcome"] != "matched":
            continue
        print(json.dumps({
            "row": i + 1,
            "input_name1": names[i],
            **{k: payload.get(k) for k in (
                "qid", "label", "name_score", "ror_id", "lei_id", "website",
                "superseded", "supersession", "api_calls",
            )},
            "changed": _diff(base_rows[i], after_rows[i]),
        }, default=str))

    print("\n## Every row whose shipped output moved\n")
    moved = 0
    for i in range(len(base_rows)):
        delta = _diff(base_rows[i], after_rows[i])
        if not delta:
            continue
        moved += 1
        payload = placed.get(i) or {}
        print(json.dumps({
            "row": i + 1,
            "input_name1": names[i] if i < len(names) else None,
            "wikidata_outcome": payload.get("outcome"),
            "qid": payload.get("qid"),
            "changed": delta,
        }, default=str))
    print(f"\nrows moved: {moved} / {len(base_rows)}")

    print("\n## Summary deltas (everything except the lane's own counters)\n")
    for key, value in sorted(after_summary.items()):
        if key.startswith("wikidata_") or key == "processing_time_ms":
            continue
        if base_summary.get(key) != value:
            print(f"{key:38s} {base_summary.get(key)} -> {value}")

    if unplaced:
        print("\n## Traces the join could not place\n")
        for payload in unplaced:
            print(json.dumps({
                k: payload.get(k) for k in ("query", "outcome", "qid")
            }))


if __name__ == "__main__":
    main()
