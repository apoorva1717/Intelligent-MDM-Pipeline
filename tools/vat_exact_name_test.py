"""The exact legal-name search (VAT_EXACT_NAME_SEARCH, Step AL) on "No website found" rows.

Usage::

    python tools/vat_exact_name_test.py count              # eligible rows, no calls
    python tools/vat_exact_name_test.py test --budget 3    # 20 random rows, looked up again

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_ready_v2.xlsx, only read.
Rows: Not found with Not_Found_Reason NO_WEBSITE_FOUND and a legal form in
Name 1–4. The sample (seed ``--seed``) is looked up again by the hybrid
lookup with the exact-name search on (search 2 = the exact legal name, no
town, when search 1 gives no candidate in SAP's town), with the permanent
rule for invalid numbers; no agent pass (these rows already had one), no
VIES. Nothing in the workbook changes. Output:
data/VAT/DE_VAT_EXACT_NAME_test.xlsx (not committed), old and new side by
side, with empty columns for the reviewer's verdict. Model calls go through
the LLM cache in ``--cache-dir`` and the cost ledger (step "EXACT test").
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

from tools.vat_long_number_rerun import new_columns  # noqa: E402
from tools.vat_step_al import lookup_inputs  # noqa: E402

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_ready_v2.xlsx"
OUT = _ROOT / "data" / "VAT" / "DE_VAT_EXACT_NAME_test.xlsx"
SAMPLE = 20
NAME_FIELDS = ("Name", "Name 2", "Name 3", "Name 4")
COLUMNS = ["Sheet", "Customer", *NAME_FIELDS, "Street", "House Number", "Postal Code", "City", "Search name",
           "Exact query", "New decision", "New number", "New found name", "New Name_Check", "New Address_Check",
           "New Issue_Flags", "New Not_Found_Reason", "New source URL", "New evidence", "New why", "Cost USD",
           "Your verdict (right / wrong / unsure)", "Your note"]


def eligible() -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    from vat.name_prep import legal_form_of

    wb = load_workbook(INPUT, read_only=True)
    out = []
    for ws in wb.worksheets:
        if not ws.title.endswith(" - Not found"):
            continue
        it = ws.iter_rows(values_only=True)
        headers = list(next(it))
        for r in it:
            d = dict(zip(headers, r), _sheet=ws.title)
            if d.get("Not_Found_Reason") == "NO_WEBSITE_FOUND" \
                    and legal_form_of(" ".join(str(d.get(k) or "") for k in NAME_FIELDS)):
                out.append(d)
    wb.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("count", "test"))
    ap.add_argument("--budget", type=float, default=0.0, help="Cost cap in USD (VAT_WEB_BUDGET_USD).")
    ap.add_argument("--cache-dir", default=r"C:\vatc\exact")
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--extended-probes", action="store_true",
                    help="Also VAT_WEB_EXTENDED_PROBES; output DE_VAT_EXACT_NAME_probes_test.xlsx.")
    args = ap.parse_args()
    global OUT
    if args.extended_probes:
        OUT = OUT.with_name("DE_VAT_EXACT_NAME_probes_test.xlsx")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    rows = eligible()
    print(f"Eligible rows (NO_WEBSITE_FOUND with a legal form): {len(rows)}")
    if args.mode == "count":
        return 0
    if args.budget <= 0:
        raise SystemExit("--budget is required for a live run.")

    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir
    from openpyxl import Workbook

    from config import get_settings
    from vat import costs
    from vat.lookup import reject_invalid_number, run_lookups
    from vat.lookup_web import WebSearchLookup
    from vat.name_prep import prepare
    from vat.vies_client import VIESClient
    from vat.lookup_web import exact_name_query

    sample = random.Random(args.seed).sample(rows, min(SAMPLE, len(rows)))
    inputs = lookup_inputs(sample)
    settings = replace(get_settings(), vat_web_budget_usd=args.budget, vat_vies_in_lookup=False,
                       vat_web_query_mode="hybrid", vat_exact_name_search=True,
                       vat_web_extended_probes=args.extended_probes)
    engine = WebSearchLookup(settings, VIESClient(settings))
    started = datetime.now(timezone.utc).astimezone()
    print(f"Ledger before: {costs.ledger_total():.4f} USD. Running {len(inputs)} rows, cap {args.budget} USD ...")

    async def run():
        try:
            return await run_lookups(engine, inputs)
        finally:
            await engine.aclose()

    results = asyncio.run(run())
    results = [reject_invalid_number(r, inp.country) if r is not None else r for r, inp in zip(results, inputs)]
    run_c = costs.run_costs(engine.usage_log, engine.meter.table, engine.meter.overrides)
    costs.append_ledger(costs.ledger_row(run_c, run_id=f"{started:%Y%m%dT%H%M%S}_exact_test",
                                         timestamp=started.isoformat(timespec="seconds"), step="EXACT test",
                                         input_file=INPUT.name, engine="llm_web/hybrid"))
    (_ROOT / "logs" / ("vat_exact_probes_test_usage.json" if args.extended_probes else "vat_exact_test_usage.json")).write_text(
        json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {run_c['New_Spend_USD']:.4f})")

    wb = Workbook()
    ws = wb.active
    ws.title = "Exact name test"
    ws.append(COLUMNS)
    got = Counter()
    for row, res, inp in zip(sample, results, inputs):
        plan = prepare([row.get(k) for k in NAME_FIELDS])
        cols = new_columns(res, inp, row) if res is not None else {}
        got[cols.get("VAT_Decision") or "no result"] += 1
        ws.append([row["_sheet"], row.get("Customer"), *[row.get(k) for k in NAME_FIELDS], row.get("Street"),
                   row.get("House Number"), row.get("Postal Code"), row.get("City"), plan.search_name,
                   exact_name_query(plan), cols.get("VAT_Decision"), cols.get("VAT_Proposed"),
                   cols.get("Found_Name"), cols.get("Name_Check"), cols.get("Address_Check"),
                   cols.get("Issue_Flags"), cols.get("Not_Found_Reason"), cols.get("VAT_Found_Source_URL"),
                   cols.get("VAT_Found_Evidence"), cols.get("VAT_Why"), cols.get("VAT_Found_Total_USD"), None, None])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(OUT)
    print(f"New decisions: {dict(got)}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
