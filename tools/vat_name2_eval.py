"""Evaluate the Name-2 classifier (Step AF rule 10b) against the rule layer.

    python tools/vat_name2_eval.py data/VAT/CMD_DE_2026-10-21.xlsx --out logs/name2_eval.xlsx [--dry-run]

Scope: in-scope records (Country/Region Key DE, VAT Registration No. empty)
with Name 1 ≥ 34 characters and a non-empty Name 2. Each gets the rule
layer's class (vat/name_prep.py) and the helper model's answer (one call per
distinct Name 1–4 + city; no lookups). Writes the comparison, a 100-row
sample stratified by rule class × model class for hand-checking, the
agreement per class and the cost; appends the run to the cost ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

SAMPLE_SIZE = 100
CONCURRENCY = 4


def records(path: Path) -> list[dict]:
    from openpyxl import load_workbook

    ws = load_workbook(path, read_only=True)["Data"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    ix = {k: header.index(k) for k in ("Customer", "Name", "Name 2", "Name 3", "Name 4", "City",
                                       "Country/Region Key", "VAT Registration No.")}
    out = []
    for r in rows:
        name_1, name_2 = (r[ix["Name"]] or "").strip(), (r[ix["Name 2"]] or "").strip()
        if r[ix["Country/Region Key"]] != "DE" or r[ix["VAT Registration No."]]:
            continue
        if len(name_1) >= 34 and name_2:
            out.append({"customer": str(r[ix["Customer"]]), "names": tuple(r[ix[k]] for k in (
                "Name", "Name 2", "Name 3", "Name 4")), "city": r[ix["City"]]})
    return out


def rule_family(cls: str) -> str:
    return "CONTINUATION" if cls.startswith("CONTINUATION") else cls


async def classify_all(recs: list[dict], settings) -> tuple[list[dict], list[dict]]:
    from utils.cache import EvidenceCache
    from vat import costs, name2_classifier
    from vat.llm_cache import VatLLM
    from vat.name_prep import prepare

    llm = VatLLM(EvidenceCache(settings.evidence_cache_dir).namespace("vat_llm"))
    limits = name2_classifier.limits(settings.vat_helper_model)
    sem = asyncio.Semaphore(CONCURRENCY)
    answers: dict[str, asyncio.Task] = {}
    calls: list[dict] = []

    async def ask(system: str, user: str):
        async with sem:
            try:
                a = await llm.search(system, user, reasoning_effort=name2_classifier.EFFORT, limits=limits)
            except Exception as e:  # noqa: BLE001 - one record's failure is reported, not fatal
                return None, f"{type(e).__name__}: {e}"[:200]
            calls.append(costs.raw_counts({**a.usage, "deployment": settings.vat_helper_model}, label="name2",
                                          cache_hit=a.from_cache))
            return a.response, None

    rows = []
    for rec in recs:
        plan = prepare(rec["names"])
        system, user = name2_classifier.prompt(rec["names"], rec["city"], plan)
        if user not in answers:
            answers[user] = asyncio.ensure_future(ask(system, user))
        rows.append({**rec, "plan": plan, "user": user})
    for row in rows:
        answer, error = await answers[row["user"]]
        verdict = name2_classifier.verdict_of(answer)
        applied = name2_classifier.apply(row["plan"], verdict) if name2_classifier.eligible(
            row["plan"], row["names"]) else row["plan"]
        row.update(verdict=verdict, error=error, applied=applied)
    return rows, calls


def write(rows: list[dict], calls: list[dict], table, out: Path) -> dict:
    from openpyxl import Workbook

    from vat import costs

    head = ["Customer", "Name 1", "Name 2", "Name 3", "Name 4", "City", "Rule class", "Rule entity",
            "Model class", "Confidence", "legal_name", "Reason", "Agree (family)", "Search name used",
            "Classifier applies", "Error"]

    def line(r):
        v = r["verdict"] or {}
        return [r["customer"], *r["names"], r["city"], r["plan"].cls, r["plan"].entity, v.get("class"),
                v.get("confidence"), v.get("legal_name"), v.get("reason"),
                (rule_family(r["plan"].cls) == v.get("class")) if v else None, r["applied"].search_name,
                r["plan"].cls in ("OTHER", "CONTINUATION_TRUNCATED"), r["error"]]

    wb = Workbook()
    ws = wb.active
    ws.title = "All"
    ws.append(head)
    for r in rows:
        ws.append(line(r))
    # 100 rows stratified by rule class × model class, proportional, at least one per cell.
    cells = defaultdict(list)
    for r in rows:
        cells[(r["plan"].cls, (r["verdict"] or {}).get("class"))].append(r)
    rng = random.Random(20261105)
    quota = {k: max(1, round(SAMPLE_SIZE * len(v) / len(rows))) for k, v in cells.items()}
    while sum(quota.values()) > SAMPLE_SIZE:
        biggest = max(quota, key=quota.get)
        quota[biggest] -= 1
    sample = [r for k, v in cells.items() for r in rng.sample(v, min(quota[k], len(v)))]
    ws = wb.create_sheet("Sample_100")
    ws.append([*head, "Hand check: correct class? (y/n)", "Hand check: correct legal name? (y/n)"])
    for r in sample:
        ws.append(line(r))
    agreement = {}
    ws = wb.create_sheet("Agreement")
    ws.append(["Rule class", "Records", "Model CONTINUATION", "Model DEPARTMENT", "Model SUB_ENTITY", "Model OTHER",
               "No answer", "Agree (family)", "Agree %", "Classifier applies", "Search name changed"])
    by_rule = defaultdict(list)
    for r in rows:
        by_rule[r["plan"].cls].append(r)
    for cls, rs in sorted(by_rule.items()):
        c = Counter((r["verdict"] or {}).get("class") for r in rs)
        agree = sum(1 for r in rs if r["verdict"] and rule_family(cls) == r["verdict"]["class"])
        changed = sum(1 for r in rs if r["applied"].search_name != r["plan"].search_name)
        agreement[cls] = {"records": len(rs), "agree": agree, "model": dict(c), "search_name_changed": changed}
        ws.append([cls, len(rs), c.get("CONTINUATION", 0), c.get("DEPARTMENT", 0), c.get("SUB_ENTITY", 0),
                   c.get("OTHER", 0), c.get(None, 0), agree, round(100 * agree / len(rs), 1),
                   cls in ("OTHER", "CONTINUATION_TRUNCATED"), changed])
    run = {"entities": len(rows), **costs.sum_lines(calls, table), **table.stamp}
    ws = wb.create_sheet("Cost")
    for k in (*costs.LINES, "calls", "cache_hits", "input_tokens", "output_tokens", "reasoning_tokens"):
        ws.append([k, run[k]])
    wb.save(out)
    return {"agreement": agreement, "run": run, "sample": len(sample)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", default=str(_ROOT / "logs" / "name2_eval.xlsx"))
    ap.add_argument("--dry-run", action="store_true", help="Count the scope and the rule classes; no calls.")
    args = ap.parse_args()

    from config import get_settings
    from vat import costs
    from vat.name_prep import prepare

    recs = records(Path(args.input))
    distinct = {(r["names"], r["city"]) for r in recs}
    print(f"in scope: {len(recs)} records, {len(distinct)} distinct Name 1-4 + city")
    print("rule classes:", dict(Counter(prepare(r["names"]).cls for r in recs)))
    if args.dry_run:
        return 0
    settings = get_settings()
    table = costs.PriceTable.load(settings.vat_prices_file or None)
    started = datetime.now(timezone.utc).astimezone()
    rows, calls = asyncio.run(classify_all(recs, settings))
    out = write(rows, calls, table, Path(args.out))
    run = out["run"]
    print(f"Wrote {args.out}: sample {out['sample']} rows")
    for cls, a in out["agreement"].items():
        print(f"  {cls:24} {a['records']:4}  agree {a['agree']:4}  model {a['model']}  "
              f"search name changed {a['search_name_changed']}")
    print("  cost: " + " ".join(f"{k}={run[k]:.4f}" for k in costs.LINES) + f" calls={run['calls']} "
          f"hits={run['cache_hits']} reasoning_tokens={run['reasoning_tokens']}")
    row = costs.ledger_row(run, run_id=f"{started:%Y%m%dT%H%M%S}_name2_eval", timestamp=started.isoformat(
        timespec="seconds"), step="AF name2 evaluation", input_file=Path(args.input).name, engine="name2_classifier")
    costs.append_ledger(row)
    print(f"  ledger: {costs.ledger_total():.4f} USD spent to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
