"""Build the per-row tables and the before/after delta for Fixes 2 and 3.

Reads two completed runs of ``scripts/run_batch.py`` (the JSON dumps, not the
workbooks — the dumps carry the provenance events) plus the page trace, and
prints the markdown the reports embed. Every number it prints comes from a run;
nothing here computes an expectation.

Usage::

    python scripts/fix_reports.py --before logs/runs/A_baseline_traced.json \
        --after logs/runs/D_final.json --page-trace logs/runs/D_trace.jsonl \
        --input docs/thesis/chemspeed_us_100.xlsx
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

STATE_BY_BAND = {
    "verified": "unchanged-verified",
    "confirmed": "unchanged-confirmed",
    "rule": "unchanged-unresolved",
}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _input_names(path: str) -> list[str]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    rows = list(wb.active.iter_rows(values_only=True))
    wb.close()
    idx = {h: n for n, h in enumerate(rows[0]) if h}
    return [r[idx["Name 1"]] for r in rows[1:]]


def _state(result: dict) -> str | None:
    """The unchanged state, read from the shipped provenance scalar.

    Deliberately derived from `name1_provenance` rather than from the internal
    `unchanged_name1_state` field: the scalar is what a consumer of the output
    actually sees, so a report built from it is a report on what shipped.
    """
    scalar = result.get("name1_provenance") or ""
    if not scalar.startswith("input:"):
        return None
    return STATE_BY_BAND.get(scalar.rsplit(":", 1)[-1])


def _evidence(result: dict) -> str:
    """What the corroborating event recorded, for the per-row table."""
    for event in reversed(result.get("provenance") or []):
        if event.get("field") != "name1":
            continue
        ref = event.get("evidence_ref") or {}
        if event.get("rule_id") == "fix2:unchanged-verified":
            return f"{ref.get('corroborated_by')} ({ref.get('evidence_ref')})"
        if event.get("rule_id") == "fix2:unchanged-confirmed":
            return f"canonical proposal {ref.get('proposal')!r}"
    return "-"


def _flag_counts(results: list[dict]) -> collections.Counter:
    counter: collections.Counter = collections.Counter()
    for r in results:
        for code in r.get("flag_codes") or ():
            counter[code] += 1
    return counter


def _domain_counts(results: list[dict]) -> dict[str, int]:
    accepted = sum(1 for r in results if r.get("domain"))
    unverified = sum(
        1 for r in results
        if "domain-unverified" in (r.get("flag_codes") or ())
    )
    withdrawn = sum(
        1 for r in results
        for e in (r.get("provenance") or [])
        if e.get("rule_id") == "fix3:page-read-withdraws-domain"
    )
    return {"accepted": accepted, "unverified": unverified, "withdrawn": withdrawn}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--page-trace")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", default="logs/runs")
    args = ap.parse_args()

    before, after = _load(args.before), _load(args.after)
    names = _input_names(args.input)
    out_dir = _ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Fix 2: the three states, per row ──────────────────────────────────
    rows = []
    for name, result in zip(names, after["results"]):
        state = _state(result)
        if state is None:
            continue
        rows.append((name, result, state))

    lines = [
        "| Input Name 1 | Shipped Name 1 | State | Provenance | Evidence used "
        "| Flagged |",
        "|---|---|---|---|---|---|",
    ]
    for name, result, state in sorted(rows, key=lambda r: r[0] or ""):
        flagged = (
            "yes" if "low-confidence-unchanged" in (result.get("flag_codes") or ())
            else "no"
        )
        lines.append(
            f"| {name} | {result.get('name1_enriched')} | `{state}` "
            f"| `{result.get('name1_provenance')}` | {_evidence(result)} "
            f"| {flagged} |"
        )
    (out_dir / "unchanged_rows.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"unchanged population: {len(rows)}")
    for state, n in collections.Counter(s for _, _, s in rows).most_common():
        print(f"  {state:24s} {n}")
    flagged_in_pop = sum(
        1 for _, r, _ in rows
        if "low-confidence-unchanged" in (r.get("flag_codes") or ())
    )
    print(f"  of which flagged        {flagged_in_pop}")

    # ── Fix 3: per-row page outcomes ──────────────────────────────────────
    if args.page_trace:
        traces = [
            json.loads(line)
            for line in Path(args.page_trace).read_text(encoding="utf-8").splitlines()
            if line.strip() and '"step": "page_corroboration"' in line
        ]
        print(f"\npage reads attempted: {len(traces)}")
        for outcome, n in collections.Counter(
            t["outcome"] for t in traces
        ).most_common():
            print(f"  {outcome:20s} {n}")

        plines = [
            "| Name 1 | Candidate domain | Outcome | Page states | Stated location "
            "| Name score | Location |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in sorted(traces, key=lambda t: (t.get("name1") or "")):
            score = t.get("name_score")
            where = ", ".join(
                p for p in (t.get("stated_city"), t.get("stated_region")) if p
            ) or "-"
            plines.append(
                f"| {t.get('name1')} | {t.get('domain')} | `{t['outcome']}` "
                f"| {t.get('stated_org_name') or '-'} | {where} "
                f"| {'-' if score is None else f'{score:.1f}'} "
                f"| {t.get('location')} |"
            )
        (out_dir / "page_rows.md").write_text(
            "\n".join(plines) + "\n", encoding="utf-8",
        )

    # ── Combined delta ────────────────────────────────────────────────────
    b, a = before["results"], after["results"]
    bs, as_ = before["summary"], after["summary"]
    print("\n── combined delta ──")
    print(f"{'metric':38s} {'before':>8s} {'after':>8s}")

    def row(label: str, x, y) -> None:
        print(f"{label:38s} {x:>8} {y:>8}")

    row("records", len(b), len(a))
    row("ror_id present", sum(1 for r in b if r.get("ror_id")),
        sum(1 for r in a if r.get("ror_id")))
    row("lei_id present", sum(1 for r in b if r.get("lei_id")),
        sum(1 for r in a if r.get("lei_id")))
    row("registry id (either)",
        sum(1 for r in b if r.get("ror_id") or r.get("lei_id")),
        sum(1 for r in a if r.get("ror_id") or r.get("lei_id")))
    row("records flagged",
        sum(1 for r in b if r.get("flag_for_review")),
        sum(1 for r in a if r.get("flag_for_review")))
    fb, fa = _flag_counts(b), _flag_counts(a)
    for code in sorted(set(fb) | set(fa)):
        row(f"  flag: {code}", fb.get(code, 0), fa.get(code, 0))
    db, da = _domain_counts(b), _domain_counts(a)
    for key in ("accepted", "unverified", "withdrawn"):
        row(f"  domain: {key}", db[key], da[key])
    row("operating_name written",
        sum(1 for r in b if r.get("operating_name")),
        sum(1 for r in a if r.get("operating_name")))
    for key in ("unchanged_verified", "unchanged_confirmed", "unchanged_unresolved"):
        row(key, bs.get(key, 0), as_.get(key, 0))
    for key in ("enriched", "verified", "unresolved", "failed"):
        row(f"status: {key}", bs.get(key, 0), as_.get(key, 0))
    for key in (
        "tier1_retry_attempts", "tier1_retry_hits_ror", "tier1_retry_hits_lei",
        "page_reads_attempted", "page_corroborated", "page_contradicted",
        "page_name_mismatch", "page_fetch_unavailable", "page_no_identity",
        "page_parked", "page_domains_withdrawn", "page_flags_cleared",
    ):
        row(key, bs.get(key, 0), as_.get(key, 0))

    # Name 1 stability: the acceptance assertion, measured on the batch.
    changed = [
        (n, x.get("name1_enriched"), y.get("name1_enriched"))
        for n, x, y in zip(names, b, a)
        if x.get("name1_enriched") != y.get("name1_enriched")
    ]
    print(f"\nName 1 differs between runs: {len(changed)}")
    for n, x, y in changed:
        print(f"  {n!r}: {x!r} -> {y!r}")

    print(f"\ntables -> {out_dir}")


if __name__ == "__main__":
    main()
