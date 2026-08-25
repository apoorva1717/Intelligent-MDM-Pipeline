"""The provenance migration's core gate: values invariant, provenance changed.

The migration to Provenance Scheme B is a REPRESENTATION change. No resolution
decision, guard, threshold or acceptance behaviour may move with it — the same
inputs must produce the same enrichment values as before, and only the
provenance columns and the flag columns may differ.

That is a claim you can fail, and this is the tool that fails it::

    python tools/provenance_invariance.py before.json after.json

Both inputs are the ``--json`` artefact ``scripts/run_batch.py`` writes. Run
the batch twice against the SAME frozen evidence — once before the migration,
once after — so that the only variable is the code::

    git stash                       # or check out the pre-migration commit
    python scripts/run_batch.py <batch> --out b.xlsx --json before.json --frozen
    git stash pop
    python scripts/run_batch.py <batch> --out a.xlsx --json after.json  --frozen
    python tools/provenance_invariance.py before.json after.json

What is compared
----------------

Every column in ``api.output_columns.RESPONSE_COLUMNS``, partitioned into three
classes rather than one:

``value columns``
    Names, domains, identifiers, record types, operating names, addresses,
    search terms — everything the pipeline is FOR. **Any** difference here
    fails the gate. This is the invariant the migration claims.

``provenance columns``
    Expected to change on nearly every row; reported as a mapping census
    (old string → new string, with counts) rather than as diffs, because a
    per-row diff of a column that changed everywhere is unreadable.

``flag columns``
    Allowed to differ, but only in the ways the derivation rule permits. Rows
    whose ``Flag for Review`` boolean moved are listed individually with their
    codes before and after, because that is the one flag change a downstream
    consumer feels.

Why positional join
-------------------

``tools/run_diff.py`` joins on ``(name1_original, city)`` because it compares
two runs that may disagree about a record, and an input-side key is the only
honest one there. This tool is comparing two runs of the same batch through
the same deterministic pipeline, where record *i* is record *i* — and that key
is not unique on the chemspeed batch (100 rows, 91 distinct pairs), so a keyed
join would silently collapse nine records. The row count and each row's
``name1_original`` are asserted to match, which is what makes the positional
join safe rather than merely convenient.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from api.output_columns import RESPONSE_COLUMNS  # noqa: E402
from enrichment.orchestrator import PROVENANCE_COLUMNS  # noqa: E402

#: Columns the migration is allowed to change, beyond the provenance ones.
FLAG_COLUMNS: tuple[str, ...] = (
    "flag_for_review", "flag_codes", "flag_reason", "flagged_fields",
)

#: Not in the shipped schema and not comparable: the event log itself, which
#: carries the same information the columns do and is expected to change shape.
_LOG_COLUMNS: tuple[str, ...] = (
    "provenance", "provenance_rejected", "provenance_rejected_omitted",
)


def _norm(value: Any) -> Any:
    """``None`` and ``""`` are the same value to every consumer of this
    schema, and only those two."""
    return "" if value is None else value


def load(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("results") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected a run artefact with `results`")
    return rows


def compare(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(before) != len(after):
        raise SystemExit(
            f"row counts differ ({len(before)} vs {len(after)}) — these are "
            "not two runs of the same batch",
        )

    value_columns = [
        c for c in RESPONSE_COLUMNS
        if c not in PROVENANCE_COLUMNS
        and c not in FLAG_COLUMNS
        and c not in _LOG_COLUMNS
    ]

    value_diffs: list[dict[str, Any]] = []
    mapping: dict[str, Counter] = {c: Counter() for c in PROVENANCE_COLUMNS}
    flag_moves: list[dict[str, Any]] = []
    reason_changes = 0

    for index, (a, b) in enumerate(zip(before, after)):
        if _norm(a.get("name1_original")) != _norm(b.get("name1_original")):
            raise SystemExit(
                f"row {index}: the two runs are not aligned "
                f"({a.get('name1_original')!r} vs {b.get('name1_original')!r})",
            )
        for column in value_columns:
            if _norm(a.get(column)) != _norm(b.get(column)):
                value_diffs.append({
                    "row": index,
                    "name1_original": a.get("name1_original"),
                    "column": column,
                    "before": a.get(column),
                    "after": b.get(column),
                })
        for column in PROVENANCE_COLUMNS:
            mapping[column][(a.get(column), b.get(column))] += 1
        if bool(a.get("flag_for_review")) != bool(b.get("flag_for_review")):
            flag_moves.append({
                "row": index,
                "name1_original": a.get("name1_original"),
                "before_flagged": bool(a.get("flag_for_review")),
                "after_flagged": bool(b.get("flag_for_review")),
                "before_codes": list(a.get("flag_codes") or ()),
                "after_codes": list(b.get("flag_codes") or ()),
                "before_reason": a.get("flag_reason"),
                "after_reason": b.get("flag_reason"),
            })
        if _norm(a.get("flag_reason")) != _norm(b.get("flag_reason")):
            reason_changes += 1

    return {
        "rows": len(before),
        "value_columns_compared": len(value_columns),
        "value_differences": value_diffs,
        "provenance_mapping": {
            column: [
                {"before": old, "after": new, "count": n}
                for (old, new), n in sorted(
                    counts.items(), key=lambda kv: (-kv[1], str(kv[0])),
                )
            ]
            for column, counts in mapping.items()
        },
        "flag_status_changes": flag_moves,
        "flag_reason_changes": reason_changes,
        "passed": not value_diffs,
    }


def render(report: dict[str, Any], *, verbose: bool) -> str:
    out: list[str] = []
    add = out.append
    add("=" * 72)
    add("PROVENANCE MIGRATION — BEHAVIOUR INVARIANCE")
    add("=" * 72)
    add(f"rows compared            : {report['rows']}")
    add(f"value columns compared   : {report['value_columns_compared']}")
    add(f"value differences        : {len(report['value_differences'])}")
    add(f"flag status changes      : {len(report['flag_status_changes'])}")
    add(f"flag reason changes      : {report['flag_reason_changes']}")
    add("")

    if report["value_differences"]:
        add("FAIL — enrichment values moved. This is not a representation")
        add("migration; something decided differently.")
        add("")
        for diff in report["value_differences"][:40]:
            add(
                f"  row {diff['row']:>3} {diff['column']:<28} "
                f"{diff['before']!r} -> {diff['after']!r}",
            )
        if len(report["value_differences"]) > 40:
            add(f"  ... {len(report['value_differences']) - 40} more")
    else:
        add("PASS — every enrichment value is byte-identical.")
    add("")

    add("Provenance mapping applied")
    add("-" * 72)
    for column, entries in report["provenance_mapping"].items():
        moved = [e for e in entries if e["before"] != e["after"]]
        if not moved and not verbose:
            continue
        add(f"  {column}")
        shown = entries if verbose else moved
        for entry in shown:
            marker = " " if entry["before"] == entry["after"] else "*"
            add(
                f"   {marker} {entry['count']:>4}  {entry['before']!r}"
                f"  ->  {entry['after']!r}",
            )
    add("")

    if report["flag_status_changes"]:
        add("Rows whose Flag for Review changed")
        add("-" * 72)
        for move in report["flag_status_changes"]:
            add(
                f"  row {move['row']:>3} {move['name1_original']!r} "
                f"{move['before_flagged']} -> {move['after_flagged']}",
            )
            add(f"        codes {move['before_codes']} -> {move['after_codes']}")
    else:
        add("No row changed its Flag for Review status.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before", help="Pre-migration run --json artefact.")
    ap.add_argument("after", help="Post-migration run --json artefact.")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="Write the full report as JSON.")
    ap.add_argument("--verbose", action="store_true",
                    help="Show unchanged provenance mappings too.")
    args = ap.parse_args()

    report = compare(load(Path(args.before)), load(Path(args.after)))
    print(render(report, verbose=args.verbose))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8",
        )
        print(f"\nreport -> {args.json_out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
