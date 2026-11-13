"""Rerun the rows whose search name changed with the long-number rule (vat/name_prep.py, 2026-11-12).

Usage::

    python tools/vat_long_number_rerun.py list                 # the rows, no calls
    python tools/vat_long_number_rerun.py run --budget 6       # look them up, write the copy

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_persons.xlsx, only read. Rows: those
whose rule-layer search name or entity changes when a run of six or more digits
is no longer read as part of the name, and whose decision is not Auto-accept.

Each is looked up by the hybrid lookup (vat.lookup_web.WebSearchLookup) as in
the first run, with the same permanent rules (invalid numbers, another
company in another town, a private person's number naming someone else); no
agent pass, no VIES (numbers stay Pending). A row's new result replaces the
old one only when its decision is better (Auto-accept > Review > Conflict >
Not found); else the old row stays.

Output: data/VAT/DE_VAT_FINAL_einvoicing_ALL_persons_longnum.xlsx (not
committed): the input with the replaced rows moved to the sheet of their new
decision, and a last sheet "Long-number rerun" with every rerun row, old and
new side by side. Model calls go through the LLM cache in ``--cache-dir`` and
the cost ledger (step "LONGNUM rerun").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from collections import Counter
from copy import copy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools.vat_step_al import lookup_inputs  # noqa: E402

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_persons.xlsx"
OUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_persons_longnum.xlsx"
NAME_FIELDS = ("Name", "Name 2", "Name 3", "Name 4")
RANK = {"Not found": 0, "Conflict": 1, "Review": 2, "Auto-accept": 3}
SHEET = "Long-number rerun"
REVIEW_SHEET_COLUMNS = [
    "Action", "Sheet", "Customer", "Name", "Name 2", "Name 3", "Name 4", "City", "Customer_Type",
    "Old search name", "New search name",
    "Old decision", "Old number", "Old found name", "Old Not_Found_Reason",
    "New decision", "New number", "New found name", "New Name_Check", "New Address_Check", "New Issue_Flags",
    "New Not_Found_Reason", "New source URL", "New evidence", "New why", "Cost USD",
]


def changed(rows: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """``(index, old search name, new search name)`` of the rows to rerun."""
    import vat.name_prep as np

    new_re, never = np._LONG_NUMBER_RE, re.compile(r"(?!x)x")
    out = []
    for i, r in enumerate(rows):
        names = [r.get(k) for k in NAME_FIELDS]
        if r["_sheet"].endswith("Auto-accept") or not new_re.search(" ".join(str(n) for n in names if n)):
            continue
        try:
            np._LONG_NUMBER_RE = never
            old = np.prepare(names)
        finally:
            np._LONG_NUMBER_RE = new_re
        new = np.prepare(names)
        if (old.search_name, old.entity) != (new.search_name, new.entity):
            out.append((i, old.search_name, new.search_name))
    return out


def new_columns(result: Any, inp: Any, row: dict[str, Any]) -> dict[str, Any]:
    """The row's lookup columns from the new result (as vat/validate.py writes them)."""
    from vat.costs import cost_columns
    from vat.lookup import FOUND_VS_SAP, vs_sap
    from vat.normalize import assess_format
    from vat.review_columns import review_columns

    cols = dict(result.columns())
    cols.update(review_columns(result, " ".join(n for n in inp.names if n), inp.postal_code, inp.city))
    cols.update(cost_columns((result.usage or {}).get("costs")))
    fmt = assess_format(row.get("VAT Registration No."), row.get("Country/Region Key"))
    cols[FOUND_VS_SAP] = vs_sap(result.value, fmt.normalized, inp.country)
    for c in ("VAT_Withdrawn_Number", "VAT_Withdrawn_Found_Name", "VAT_Withdrawn_Source_URL"):
        cols[c] = None
    return cols


def person_withdraws(row: dict[str, Any], cols: dict[str, Any]) -> bool:
    """The persons rule (vat/person_classifier.py) on the new candidate."""
    from vat import person_classifier as pc

    if row.get("Customer_Type") != pc.PERSON or not cols.get("VAT_Proposed"):
        return False
    return not pc.names_person(cols.get("Found_Name"), row.get("Person_Name"))


def write(rows, picks, results, inputs, decisions) -> dict[str, Any]:
    from openpyxl import load_workbook

    wb = load_workbook(INPUT)
    headers = {ws.title: [c.value for c in ws[1]] for ws in wb.worksheets}
    for h in headers.values():
        while h and h[-1] is None:
            h.pop()
    moves: dict[str, list[int]] = {}
    moved: Counter = Counter()
    review = []
    for (i, old_s, new_s), res, inp, (action, cols) in zip(picks, results, inputs, decisions):
        row = rows[i]
        title, r = row["_sheet"], row["_row"]
        review.append([action, title, row.get("Customer"), *[row.get(k) for k in NAME_FIELDS], row.get("City"),
                       row.get("Customer_Type"), old_s, new_s,
                       row.get("VAT_Decision"), row.get("VAT_Proposed"), row.get("Found_Name"),
                       row.get("Not_Found_Reason"),
                       cols.get("VAT_Decision"), cols.get("VAT_Proposed"), cols.get("Found_Name"),
                       cols.get("Name_Check"), cols.get("Address_Check"), cols.get("Issue_Flags"),
                       cols.get("Not_Found_Reason"), cols.get("VAT_Found_Source_URL"),
                       cols.get("VAT_Found_Evidence"), cols.get("VAT_Why"), cols.get("VAT_Found_Total_USD")])
        if action != "replaced":
            continue
        ws, h = wb[title], headers[title]
        values = {name: ws.cell(row=r, column=j + 1).value for j, name in enumerate(h)}
        values.update({k: v for k, v in cols.items() if k in h})
        target = f"{title.split(' - ')[0]} - {values['VAT_Decision']}"
        if target == title:
            for j, name in enumerate(h):
                ws.cell(row=r, column=j + 1).value = values.get(name)
            continue
        dest, th = wb[target], headers[target]
        new_r = dest.max_row + 1
        for j, name in enumerate(th):
            cell = dest.cell(row=new_r, column=j + 1, value=values.get(name))
            if name in h:
                cell._style = copy(ws.cell(row=r, column=h.index(name) + 1)._style)
        moves.setdefault(title, []).append(r)
        moved[(title, target)] += 1
    for title, rs in moves.items():
        for r in sorted(rs, reverse=True):
            wb[title].delete_rows(r)
    ws = wb.create_sheet(SHEET)
    ws.append(REVIEW_SHEET_COLUMNS)
    src = wb.worksheets[0].cell(row=1, column=1)
    for j in range(len(REVIEW_SHEET_COLUMNS)):
        cell = ws.cell(row=1, column=j + 1)
        cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill), copy(src.alignment),
                                                             copy(src.border))
    for line in sorted(review, key=lambda x: (x[0] != "replaced", str(x[3]))):
        ws.append(line)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for w in wb.worksheets:
        if w.auto_filter.ref and w.title != SHEET:
            w.auto_filter.ref = w.dimensions
    wb.save(OUT)
    return {"moved": {f"{a} -> {b}": n for (a, b), n in moved.items()},
            "sheets": {w.title: w.max_row - 1 for w in wb.worksheets}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("list", "run"))
    ap.add_argument("--budget", type=float, default=0.0, help="Cost cap in USD (VAT_WEB_BUDGET_USD).")
    ap.add_argument("--cache-dir", default=r"C:\vatc\ln")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    rows = _read(INPUT)
    picks = changed(rows)
    print(f"Rows to rerun: {len(picks)}; by decision {dict(Counter(rows[i]['VAT_Decision'] for i, _, _ in picks))}")
    if args.mode == "list":
        for i, o, n in picks:
            print(f"  {rows[i]['VAT_Decision']}: {o}  =>  {n}")
        return 0
    if args.budget <= 0:
        raise SystemExit("--budget is required for a live run.")

    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir
    from config import get_settings
    from vat import costs
    from vat.lookup import reject_invalid_number, reject_other_name_other_town, run_lookups
    from vat.lookup_web import WebSearchLookup
    from vat.vies_client import VIESClient

    inputs = lookup_inputs([rows[i] for i, _, _ in picks])
    settings = replace(get_settings(), vat_web_budget_usd=args.budget, vat_vies_in_lookup=False,
                       vat_web_query_mode="hybrid")
    engine = WebSearchLookup(settings, VIESClient(settings))
    before = costs.ledger_total()
    started = datetime.now(timezone.utc).astimezone()
    print(f"Ledger before: {before:.4f} USD. Running {len(inputs)} rows, cap {args.budget} USD ...")

    async def run():
        try:
            return await run_lookups(engine, inputs)
        finally:
            await engine.aclose()

    results = asyncio.run(run())
    results = [reject_invalid_number(r, inp.country) if r is not None else r for r, inp in zip(results, inputs)]
    if settings.vat_reject_other_name_other_town:
        results = [reject_other_name_other_town(r) if r is not None else r for r in results]

    decisions = []
    for (i, _, _), res, inp in zip(picks, results, inputs):
        row = rows[i]
        if res is None:
            decisions.append(("kept old: no result", {}))
            continue
        cols = new_columns(res, inp, row)
        new, old = cols.get("VAT_Decision") or "Not found", row.get("VAT_Decision")
        if person_withdraws(row, cols):
            decisions.append(("kept old: page does not name the person", cols))
        elif RANK.get(new, 0) > RANK.get(old, 0):
            decisions.append(("replaced", cols))
        elif cols.get("VAT_Proposed") and cols.get("VAT_Proposed") != row.get("VAT_Proposed"):
            decisions.append(("kept old: same or lower decision, other number", cols))
        else:
            decisions.append(("kept old: not better", cols))

    run_c = costs.run_costs(engine.usage_log, engine.meter.table, engine.meter.overrides)
    costs.append_ledger(costs.ledger_row(run_c, run_id=f"{started:%Y%m%dT%H%M%S}_longnum_rerun",
                                         timestamp=started.isoformat(timespec="seconds"), step="LONGNUM rerun",
                                         input_file=INPUT.name, engine="llm_web/hybrid"))
    (_ROOT / "logs" / "vat_longnum_rerun_usage.json").write_text(
        json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {run_c['New_Spend_USD']:.4f}); "
          f"calls by deployment {run_c.get('calls_by_deployment')}")
    print(f"Actions: {dict(Counter(a for a, _ in decisions))}")
    print(f"Old -> new decision: {dict(Counter((rows[i]['VAT_Decision'], c.get('VAT_Decision')) for (i, _, _), (_, c) in zip(picks, decisions)))}")
    print(f"Written: {write(rows, picks, results, inputs, decisions)}")
    print(f"Wrote {OUT}")
    return 0


def _read(path: Path) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    out = []
    for ws in wb.worksheets:
        if ws.title in ("Persons", SHEET):
            continue
        it = ws.iter_rows(values_only=True)
        headers = list(next(it))
        out += [dict(zip(headers, r), _sheet=ws.title, _row=i) for i, r in enumerate(it, start=2)
                if any(v is not None for v in r)]
    wb.close()
    return out


if __name__ == "__main__":
    raise SystemExit(main())
