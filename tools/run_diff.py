"""Diff two enrichment runs of the same batch. The reproducibility gate.

Two runs of the identical 101-row chemspeed batch on the identical codebase
produced seven substantively different records — including two silent
wrong-entity acceptances. Nothing in the repository could have told you that:
the runs were compared by eye. This is the tool that makes "the pipeline is
reproducible" a claim you can fail.

Usage::

    python tools/run_diff.py logs/runs/run1.json logs/runs/run2.json
    python tools/run_diff.py run1.json run2.json --json report.json

Both inputs are the ``--json`` artefact ``scripts/run_batch.py`` writes: the
full ``EnrichmentResult`` for every record plus the batch summary. The enriched
XLSX is deliberately NOT accepted — it carries only the *output* Name 1, and
the join key has to be input-side.

The join key
------------

``(name1_original, city)``, both normalised for case and whitespace only.

Emphatically **not** Search Term 1, which is the obvious-looking candidate and
is wrong: Search Term 1 is pipeline-written, and Fix D(3) exists precisely
because it could be derived from a registry entity that lost a consistency
check. Joining on a column the pipeline writes means two runs that disagree
about a record can fail to line that record up at all, and the diff then
reports it as one deletion and one insertion instead of as the difference it
is. ``record_id`` would also be stable, but the batch is identified to a
reader by name and city, and a duplicated customer number should surface as a
duplicate key rather than silently pair the wrong rows.

What is compared
----------------

Every column in ``api.output_columns.RESPONSE_COLUMNS`` — the shipped output
schema, which is also exactly what lands in the workbook and in the ``/enrich``
response body. Nothing is excluded, whitelisted or normalised away. (``None``
and ``""`` compare equal, and only that, because the two spellings of "empty"
are the same value in every consumer of this schema.)

``duration_ms`` is not in that schema and so is not compared; it measures wall
clock and is the one output that SHOULD differ between two runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from api.output_columns import RESPONSE_COLUMNS  # noqa: E402
from enrichment.confidence import (  # noqa: E402
    PROVENANCE_RE,
)
from enrichment.orchestrator import PROVENANCE_COLUMNS  # noqa: E402

# Windows consoles default to cp1252, and registry names carry accents and
# em-dashes. A diff that crashes on the character set of its own evidence is
# not a gate.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a stream that cannot be reconfigured
        pass

#: The join key's components. Input-side, both of them.
KEY_FIELDS: tuple[str, ...] = ("name1_original", "city")


def _norm_key_part(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def record_key(row: dict[str, Any]) -> str:
    return "|".join(_norm_key_part(row.get(f)) for f in KEY_FIELDS)


def _keyed_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Every result with its INPUT-side name and city attached.

    ``EnrichmentResult`` marks the ``*_original`` fields ``exclude=True`` —
    they are working state, not output — so the artefact's `results` array
    carries no input name at all. `scripts/run_batch.py` therefore emits a
    parallel `inputs` array, and this is where the two are joined: by position
    WITHIN one run (the orchestrator returns results in input order), so that
    the join BETWEEN the two runs can be on the name and city, which is the
    whole point.

    An older artefact without `inputs` falls back to whatever the result rows
    carry, and says so rather than silently keying on an empty string.
    """
    results = raw["results"]
    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != len(results):
        if any(r.get("name1_original") for r in results):
            return results
        print(
            "warning: this artefact carries no input-side name (no `inputs` "
            "array and no `name1_original`); rows will be keyed on city and "
            "customer number alone. Re-run with the current "
            "scripts/run_batch.py for a full key.",
            file=sys.stderr,
        )
        return results
    merged: list[dict[str, Any]] = []
    for row, src in zip(results, inputs):
        merged.append({
            **row,
            "name1_original": src.get("name1"),
            "city": src.get("city", row.get("city")),
            "record_id": src.get("record_id") or row.get("record_id"),
        })
    return merged


def _norm_value(value: Any) -> Any:
    """Compare-ready form of one cell.

    Only two normalisations, and both are identity rather than tolerance:
    ``None`` and ``""`` are the same absence, and a list is compared by its
    contents (``flag_codes`` ships as a list in JSON and as a joined string in
    the workbook). Nothing else is folded — a case change, a spacing change or
    a reordered list is a real difference and this tool exists to see it.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return [_norm_value(v) for v in value]
    if isinstance(value, str):
        return value
    return value


def load_run(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """``({key: row}, summary)`` for one run artefact."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "results" not in raw:
        raise SystemExit(
            f"{path}: not a run_batch --json artefact (no 'results' key). "
            "The enriched XLSX cannot be diffed — it has no input-side name."
        )
    results = _keyed_rows(raw)

    # (name, city) is not unique on every batch — the chemspeed rows whose
    # Name 1 is blank (a person lifted out by preprocessing, an opaque code)
    # collapse onto their city. A duplicate key must never silently merge two
    # records into one comparison, so a key that repeats WITHIN a run is
    # extended with the customer number. That is still input-side and still
    # stable across runs, and it is used only where the primary key cannot
    # separate the rows on its own.
    counts: Counter[str] = Counter(record_key(r) for r in results)
    with_id = Counter(
        f"{record_key(r)}#{_norm_key_part(r.get('record_id'))}"
        for r in results
    )
    rows: dict[str, dict[str, Any]] = {}
    by_id = by_position = 0
    for index, row in enumerate(results):
        key = record_key(row)
        if counts[key] > 1:
            key = f"{key}#{_norm_key_part(row.get('record_id'))}"
            by_id += 1
            if with_id[key] > 1:
                # Nine chemspeed rows carry neither a Name 1 nor a customer
                # number. The row's ordinal position in the input file is the
                # only input-side identity they have left, and it IS stable:
                # `enrich_batch` returns results in input order. Dropping them
                # instead would quietly shrink the batch the gate measures.
                key = f"{key}@{index}"
                by_position += 1
        rows[key] = row
    if by_id:
        print(
            f"note: {path.name} — {by_id} row(s) share a (name, city) key; "
            f"disambiguated by customer number"
            + (f", and {by_position} of those by input row position" if by_position else "")
            + ".",
            file=sys.stderr,
        )
    assert len(rows) == len(results), "join keys are not unique"
    return rows, raw.get("summary") or {}


# ---------------------------------------------------------------------------
# Provenance grammar
# ---------------------------------------------------------------------------

def provenance_grammar(rows: Iterable[dict[str, Any]]) -> str:
    """``"B"`` | ``"A"`` | ``"empty"`` | ``"mixed"`` for one run's artefact.

    Scheme B is ``source:confidence[+witness]``; scheme A was the
    ``producer:tier:method`` form this repository shipped before the
    provenance migration, alongside ``web:{domain}:extracted:{date}``.

    This exists because the gate is otherwise actively misleading across the
    migration. Seven of the sixty-seven output columns are provenance, and
    every one of them changes on almost every row — so diffing a pre-migration
    run against a post-migration one reports the whole batch as differing and
    buries the question the gate is asked: *did anything DECIDE differently?*
    A reader who sees "rows differing 100" concludes the pipeline broke, when
    what changed is the spelling of a column.

    Two artefacts in different grammars are therefore incomparable, and this
    says so rather than producing a number that can only be misread. To
    compare behaviour across the migration, use
    ``tools/provenance_invariance.py``, which partitions the columns instead
    of refusing.
    """
    seen: set[str] = set()
    for row in rows:
        for column in PROVENANCE_COLUMNS:
            value = row.get(column)
            if value in (None, ""):
                continue
            seen.add("B" if PROVENANCE_RE.match(str(value)) else "A")
    if not seen:
        return "empty"
    if len(seen) > 1:
        return "mixed"
    return seen.pop()


def diff_runs(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare two keyed runs over the whole output schema."""
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    shared = sorted(set(left) & set(right))

    per_column: Counter[str] = Counter()
    differing_rows: list[dict[str, Any]] = []
    by_column: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for key in shared:
        a, b = left[key], right[key]
        deltas = []
        for field, label in RESPONSE_COLUMNS.items():
            va, vb = _norm_value(a.get(field)), _norm_value(b.get(field))
            if va == vb:
                continue
            per_column[label] += 1
            delta = {"column": label, "field": field, "run1": va, "run2": vb}
            deltas.append(delta)
            by_column[label].append({"key": key, **delta})
        if deltas:
            differing_rows.append({"key": key, "deltas": deltas})

    return {
        "rows_run1": len(left),
        "rows_run2": len(right),
        "rows_compared": len(shared),
        "rows_only_in_run1": only_left,
        "rows_only_in_run2": only_right,
        "rows_differing": len(differing_rows),
        "differences_total": sum(per_column.values()),
        "per_column": dict(per_column.most_common()),
        "rows": differing_rows,
        "by_column": {k: v for k, v in by_column.items()},
    }


def _fmt(value: Any, width: int = 60) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def render(report: dict[str, Any], *, verbose: bool) -> str:
    out: list[str] = []
    out.append("=" * 72)
    out.append("RUN DIFF")
    out.append("=" * 72)
    out.append(f"rows in run 1      : {report['rows_run1']}")
    out.append(f"rows in run 2      : {report['rows_run2']}")
    out.append(f"rows compared      : {report['rows_compared']}")
    out.append(f"rows differing     : {report['rows_differing']}")
    out.append(f"cell differences   : {report['differences_total']}")

    if report["rows_only_in_run1"] or report["rows_only_in_run2"]:
        out.append("")
        out.append("UNMATCHED KEYS (a join failure, not a value difference)")
        for key in report["rows_only_in_run1"]:
            out.append(f"  only in run 1: {key}")
        for key in report["rows_only_in_run2"]:
            out.append(f"  only in run 2: {key}")

    if report["per_column"]:
        out.append("")
        out.append("PER-COLUMN COUNTS")
        width = max(len(c) for c in report["per_column"])
        for column, count in report["per_column"].items():
            out.append(f"  {column.ljust(width)}  {count}")

    if report["rows"] and verbose:
        out.append("")
        out.append("SUBSTANTIVE DIFFERENCES")
        for row in report["rows"]:
            out.append(f"  ── {row['key']}")
            for delta in row["deltas"]:
                out.append(f"       {delta['column']}")
                out.append(f"         run 1: {_fmt(delta['run1'])}")
                out.append(f"         run 2: {_fmt(delta['run2'])}")

    out.append("")
    verdict = (
        "PASS — the two runs are identical across every enrichment column."
        if report["rows_differing"] == 0
        and not report["rows_only_in_run1"]
        and not report["rows_only_in_run2"]
        else f"FAIL — {report['rows_differing']} row(s) differ."
    )
    out.append(verdict)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run1", help="First run's --json artefact.")
    ap.add_argument("run2", help="Second run's --json artefact.")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="Write the full report as JSON here.")
    ap.add_argument("--quiet", action="store_true",
                    help="Counts only; omit the per-row detail.")
    args = ap.parse_args()

    left, summary1 = load_run(Path(args.run1))
    right, summary2 = load_run(Path(args.run2))

    grammar1 = provenance_grammar(left.values())
    grammar2 = provenance_grammar(right.values())
    if "mixed" in (grammar1, grammar2):
        print(
            f"INCOMPARABLE — {Path(args.run1).name} carries "
            f"{grammar1} provenance and {Path(args.run2).name} carries "
            f"{grammar2}. A single run holding two provenance grammars is a "
            "bug in the run that produced it, not a difference between runs.",
            file=sys.stderr,
        )
        return 2
    if "empty" not in (grammar1, grammar2) and grammar1 != grammar2:
        print(
            "=" * 72 + "\n"
            "INCOMPARABLE — these two runs use different provenance "
            "grammars\n" + "=" * 72 + "\n"
            f"  {Path(args.run1).name:<32} scheme {grammar1}\n"
            f"  {Path(args.run2).name:<32} scheme {grammar2}\n\n"
            "Scheme A is `producer:tier:method` (`ror:1:exact`); scheme B is\n"
            "`source:confidence[+witness]` (`ror:verified`). Seven of the\n"
            "sixty-seven columns are provenance and all seven change between\n"
            "the two, so this diff would report nearly every row as differing\n"
            "and tell you nothing about whether the pipeline decided\n"
            "differently. That is the question you are asking, so ask it of\n"
            "the tool that can answer it:\n\n"
            f"    python tools/provenance_invariance.py "
            f"{args.run1} {args.run2}\n",
            file=sys.stderr,
        )
        return 2

    report = diff_runs(left, right)
    report["provenance_grammar_run1"] = grammar1
    report["provenance_grammar_run2"] = grammar2
    report["summary_run1"] = summary1
    report["summary_run2"] = summary2

    print(render(report, verbose=not args.quiet))

    # The evidence that run 2 was a WARM run. A second run that went to the
    # network is not comparing the same evidence the first one saw, so a clean
    # diff from it would not mean what the gate needs it to mean.
    calls = summary2.get("evidence_network_calls")
    if calls is not None:
        print(f"\nrun 2 network calls: {calls}")

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, indent=1, default=str), encoding="utf-8",
        )
        print(f"report -> {path}")

    ok = (
        report["rows_differing"] == 0
        and not report["rows_only_in_run1"]
        and not report["rows_only_in_run2"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
