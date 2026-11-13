"""The Conflict picker (vat/conflict_picker.py) on a sample of the Conflict rows.

Usage::

    python tools/vat_conflict_pick.py count               # Conflict rows, no calls
    python tools/vat_conflict_pick.py test --budget 1     # 20 random rows: fetch, ask, check, VIES

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_ready.xlsx, only read. The
sample (seed ``--seed``) is :data:`SAMPLE` random Conflict rows, one per
proposed number. Per row: the source page is opened again (``vat_pages``),
every DE number on it is listed, the helper model (mini only) names the
customer's, and :func:`vat.conflict_picker.outcome` checks the answer; a
PICKED number is checked in VIES. Nothing in the workbook changes. Output:
data/VAT/DE_VAT_CONFLICT_pick_test.xlsx (not committed), with empty columns
for the reviewer's verdict. Model calls go through the LLM cache in
``--cache-dir`` and the cost ledger (step "CONFLICT test").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_ready.xlsx"
TEST_OUT = _ROOT / "data" / "VAT" / "DE_VAT_CONFLICT_pick_test.xlsx"
SAMPLE = 20
NAME_FIELDS = ("Name", "Name 2", "Name 3", "Name 4")
OUT_COLUMNS = ["Sheet", "Customer", *NAME_FIELDS, "Street", "House Number", "Postal Code", "City", "Old_Proposed",
               "Old_Found_Name", "Source_URL", "Numbers_On_Page", "Picked", "Same_As_Old", "Quote", "Confidence",
               "Model_Reason", "Outcome", "Outcome_Why", "VIES", "Your verdict (right / wrong / unsure)", "Your note"]


def conflict_rows() -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(INPUT, read_only=True)
    out = []
    for ws in wb.worksheets:
        if not ws.title.endswith(" - Conflict"):
            continue
        it = ws.iter_rows(values_only=True)
        headers = list(next(it))
        out += [dict(zip(headers, r), _sheet=ws.title) for r in it if any(v is not None for v in r)]
    wb.close()
    return out


async def pick_one(engine: Any, row: dict[str, Any], lim: Any) -> dict[str, Any]:
    """One row: the page again, its numbers, the model's pick, the checks, VIES."""
    from enrichment.page_corroborator import _readable_text
    from vat import conflict_picker as cp
    from vat.lookup import FoundResult
    from vat.name_prep import prepare
    from vat.normalize import assess_format

    url = row.get("VAT_Found_Source_URL")
    got: dict[str, Any] = {"Numbers_On_Page": None, "Picked": None, "Quote": None, "Confidence": None,
                           "Model_Reason": None, "VIES": None}
    flags: set[str] = set()
    page = await engine._pages._fetch(url, flags) if url else None
    if page is not None and not (page.ok and page.content):
        page = await engine._pages.fetch_browser(url, flags)
    if page is None or not (page.ok and page.content):
        return {**got, "Outcome": cp.UNSURE, "Outcome_Why": f"page not readable ({','.join(sorted(flags)) or 'none'})"}
    text = _readable_text(page.content)
    numbers = cp.numbers_on_page(text)
    got["Numbers_On_Page"] = "; ".join(v for v, _ in numbers)
    if len(numbers) < 2 or len(numbers) > cp.MAX_NUMBERS:
        return {**got, "Outcome": cp.UNSURE, "Outcome_Why": f"page now lists {len(numbers)} numbers"}
    names = [row.get(k) for k in NAME_FIELDS]
    street = " ".join(str(x) for x in (row.get("Street"), row.get("House Number")) if x) or None
    record = cp.record_text(names, street, row.get("Postal Code"), row.get("City"))
    usage: dict[str, Any] = {}
    out = await engine._call(cp.LABEL, *cp.prompt(record, url, numbers), lim, usage, effort=cp.EFFORT)
    verdict = None if isinstance(out, FoundResult) else cp.verdict_of(out[0])
    engine.usage_log.append({"customer": row.get("Customer"), "name_1": row.get("Name"),
                             "status": "OK" if verdict else "FAILED", "calls": usage.get("calls") or []})
    if verdict:
        got.update(Picked=verdict["number"], Quote=verdict["quote"], Confidence=verdict["confidence"],
                   Model_Reason=verdict["reason"])
    result, why = cp.outcome(verdict, text, numbers, prepare(names).acceptable)
    if result == cp.PICKED:
        fmt = assess_format(verdict["number"], "DE")
        if not fmt.vies_eligible:
            return {**got, "VIES": fmt.status, "Outcome": cp.UNSURE, "Outcome_Why": "number fails the format check"}
        vies = await engine._vies.check(fmt)
        got["VIES"] = vies.status
        if vies.status == "INVALID":
            result, why = cp.UNSURE, "VIES says invalid"
    return {**got, "Outcome": result, "Outcome_Why": why}


async def run(engine: Any, rows: list[dict[str, Any]], concurrency: int = 5) -> list[dict[str, Any]]:
    from tools.vat_person_test import mini_only
    from vat import conflict_picker as cp

    mini_only(engine)
    lim = replace(cp.limits(engine.helper_model, engine.limits.retry_backoff), max_retries=engine.limits.max_retries)
    sem = asyncio.Semaphore(concurrency)

    async def guarded(row):
        async with sem:
            return await pick_one(engine, row, lim)

    results = await asyncio.gather(*(guarded(r) for r in rows))
    deployments = {c.get("deployment") for u in engine.usage_log for c in u["calls"]}
    if deployments - {engine.helper_model}:
        raise SystemExit(f"Calls went to {deployments}, not only {engine.helper_model}: stopped.")
    return results


def write(rows: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Conflict pick test"
    ws.append(OUT_COLUMNS)
    for r, g in zip(rows, results):
        values = {**r, "Sheet": r["_sheet"], "Old_Proposed": r.get("VAT_Proposed"),
                  "Old_Found_Name": r.get("Found_Name"), "Source_URL": r.get("VAT_Found_Source_URL"), **g,
                  "Same_As_Old": None if not g.get("Picked") else g["Picked"] == r.get("VAT_Proposed")}
        ws.append([values.get(c) for c in OUT_COLUMNS])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(TEST_OUT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("count", "test"))
    ap.add_argument("--budget", type=float, default=0.0, help="Cost cap in USD (VAT_WEB_BUDGET_USD).")
    ap.add_argument("--cache-dir", default=r"C:\vatc\conflict")
    ap.add_argument("--seed", type=int, default=20261112)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    rows = conflict_rows()
    unique = list({r.get("VAT_Proposed") or id(r): r for r in rows}.values())
    print(f"Conflict rows: {len(rows)} ({len(unique)} proposed numbers); "
          f"with a source page: {sum(1 for r in rows if r.get('VAT_Found_Source_URL'))}")
    if args.mode == "count":
        return 0
    if args.budget <= 0:
        raise SystemExit("--budget is required for a live run.")

    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir
    from config import get_settings
    from tools.vat_person_test import mini_only
    from vat import costs
    from vat.lookup_web import WebSearchLookup
    from vat.vies_client import VIESClient

    settings = replace(get_settings(), vat_web_budget_usd=args.budget, vat_vies_in_lookup=False)
    engine = WebSearchLookup(settings, VIESClient(settings))
    mini_only(engine)
    sample = random.Random(args.seed).sample(unique, min(SAMPLE, len(unique)))
    started = datetime.now(timezone.utc).astimezone()
    print(f"Ledger before: {costs.ledger_total():.4f} USD. Picking on {len(sample)} rows with "
          f"{engine.helper_model}, cap {args.budget} USD ...")
    results = asyncio.run(run(engine, sample))
    run_cost = costs.run_costs(engine.usage_log, engine.meter.table, engine.meter.overrides)
    costs.append_ledger(costs.ledger_row(run_cost, run_id=f"{started:%Y%m%dT%H%M%S}_conflict_test",
                                         timestamp=started.isoformat(timespec="seconds"), step="CONFLICT test",
                                         input_file=INPUT.name, engine="name2_conflict"))
    print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {run_cost['New_Spend_USD']:.4f}); "
          f"calls {len(engine.usage_log)}, failed {sum(1 for u in engine.usage_log if u['status'] != 'OK')}")
    (_ROOT / "logs" / "vat_conflict_test_usage.json").write_text(
        json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Outcomes: {dict(Counter(g['Outcome'] for g in results))}")
    print(f"Why: {dict(Counter(g['Outcome_Why'] for g in results))}")
    write(sample, results)
    print(f"Wrote {TEST_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
