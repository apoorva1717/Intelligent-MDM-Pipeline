"""What the VAT lookups have cost to date, from the cost ledger.

    python tools/vat_cost_summary.py [--ledger logs/vat_cost_ledger.csv]

Total spent (New_Spend_USD: what the runs billed; replays cost nothing), by
step, by month, by deployment and by line (tokens / search calls / page opens
/ SerpAPI / other), plus tokens and search calls to date.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from vat import costs  # noqa: E402


#: What the ledger leaves out, printed with every summary.
NOT_IN_LEDGER = ("runs before Step X (before 2026-11-02, cache logs/cache/run_fluffy_please_19, 184 answers): "
                 "their answers were recorded without usage and cannot be priced.")


def _f(row: dict, key: str) -> float:
    return float(row.get(key) or 0)


def _pairs(text: str) -> dict[str, float]:
    return {k: float(v) for k, v in (p.split("=", 1) for p in (text or "").split(";") if "=" in p)}


def summary(rows: list[dict]) -> dict:
    """The ledger's sums, billed only: a replay's recorded cost and counts
    are not spent again (the ``New_*`` and ``billed_*`` columns)."""
    by_step: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    by_dep: dict[str, float] = defaultdict(float)
    by_line: dict[str, float] = defaultdict(float)
    totals = defaultdict(float)
    for r in rows:
        new = _f(r, costs.NEW_SPEND)
        by_step[r["step"]] += new
        by_month[r["timestamp"][:7]] += new
        for dep, usd in _pairs(r.get("new_tokens_by_deployment")).items():
            by_dep[dep] += usd
        for line in costs.BILLED_LINES:
            by_line[line] += _f(r, f"New_{line}")
        for k in (*costs.TOKEN_FIELDS, *costs.COUNT_FIELDS):
            totals[k] += _f(r, f"billed_{k}")
        totals["api_calls"] += _f(r, "api_calls")
        totals["cache_hits"] += _f(r, "cache_hits")
        totals["billed_calls"] += _f(r, "api_calls") - _f(r, "cache_hits")
    return {"total": round(sum(by_step.values()), 4), "runs": len(rows), "by_step": dict(by_step),
            "by_month": dict(by_month), "by_deployment": dict(by_dep), "by_line": dict(by_line),
            "totals": dict(totals)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=None)
    args = ap.parse_args()
    rows = costs.read_ledger(args.ledger)
    s = summary(rows)
    print(f"Spent to date: {s['total']:.4f} USD over {s['runs']} runs")
    for title, key in (("By step", "by_step"), ("By month", "by_month"), ("By deployment (tokens)", "by_deployment"),
                       ("By line", "by_line")):
        print(f"\n{title}")
        for k, v in s[key].items():
            print(f"  {k:<32} {v:9.4f}")
    t = s["totals"]
    print("\nBilled to date: " + ", ".join(f"{k} {int(t[k])}" for k in (
        "billed_calls", *costs.TOKEN_FIELDS, *costs.COUNT_FIELDS)))
    print(f"Replayed from cache: {int(t['cache_hits'])} of {int(t['api_calls'])} calls")
    estimated = [r for r in rows if r.get("estimated") == "yes"]
    if estimated:
        print(f"Rows with an estimated part ({len(estimated)}; see their note): "
              + "; ".join(f"{r['step']}: {r['note']}" for r in estimated))
    print(f"\nNot included: {NOT_IN_LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
