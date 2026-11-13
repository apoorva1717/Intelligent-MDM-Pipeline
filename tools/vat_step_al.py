"""Step AL: the second look at the "other name in another town" rows (vat/step_al.py).

Usage::

    python tools/vat_step_al.py check                 # input check, no calls
    python tools/vat_step_al.py test --budget 10      # the 40-row test + agent measurement
    python tools/vat_step_al.py full --budget <usd>   # all 689 rows, then the two workbooks

Input: data/VAT/DE_VAT_FINAL_different_name_other_town.xlsx (689 rows). The
submission workbook data/VAT/DE_VAT_FINAL_einvoicing_ALL.xlsx is only read,
to list linked rows and to merge the results into a copy (``full``):

* data/VAT/DE_VAT_STEP_AL_different_name_other_town.xlsx — the 689 rows, the
  old and new reviewer columns side by side;
* data/VAT/DE_VAT_FINAL_einvoicing_ALL_v2.xlsx — the submission workbook with
  these rows replaced (matched by Customer + Source sheet), moved to the
  sheet of their new decision; every other row unchanged.

No VIES call is made (numbers stay Pending). Every model call goes through
the LLM cache in ``--cache-dir`` (default ``C:\\vatc\\al``, short against the
Windows path limit) and the cost ledger (step "AL test" / "AL full"); the
``full`` run replays the test's calls from the cache at no cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_different_name_other_town.xlsx"
FINAL = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL.xlsx"
GROUP_OUT = _ROOT / "data" / "VAT" / "DE_VAT_STEP_AL_different_name_other_town.xlsx"
FINAL_OUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_v2.xlsx"
TEST_OUT = _ROOT / "data" / "VAT" / "DE_VAT_STEP_AL_test40.xlsx"
#: "Source sheet" of the group file → the submission workbook's sheet prefix.
SHEET_PREFIX = {"BP w email": "With email", "BP wo email": "Without email"}
WITHDRAWN_COLUMNS = ["VAT_Withdrawn_Number", "VAT_Withdrawn_Found_Name", "VAT_Withdrawn_Source_URL"]


def read_rows(path: Path, sheet: str | None = None) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    headers = list(next(it))
    rows = [dict(zip(headers, r)) for r in it if any(v is not None for v in r)]
    wb.close()
    return rows


def lookup_inputs(rows: list[dict[str, Any]]) -> list[Any]:
    """Each row as the lookup reads it (vat/validate.py's own mapping)."""
    from vat.normalize import assess_format
    from vat.validate import _LOOKUP_ROLE_FIELDS, _cell, _lookup_input, resolve_columns

    headers: dict[str, None] = {}
    for r in rows:
        headers.update(dict.fromkeys(k for k in r if k))
    cols, lcols = resolve_columns(headers), resolve_columns(headers, _LOOKUP_ROLE_FIELDS)
    out = []
    for r in rows:
        fmt = assess_format(_cell(r, cols["vat"]), _cell(r, cols["country"]))
        out.append(_lookup_input(r, cols, lcols, _cell(r, cols["customer"]), fmt))
    return out


def final_rows() -> list[tuple[str, dict[str, Any]]]:
    from openpyxl import load_workbook

    wb = load_workbook(FINAL, read_only=True)
    out = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        headers = list(next(it))
        out += [(ws.title, dict(zip(headers, r))) for r in it if any(v is not None for v in r)]
    wb.close()
    return out


def check(rows: list[dict[str, Any]], inputs: list[Any]) -> dict[str, Any]:
    """The input check, the company email domains, and the final workbook's
    rows of the group's entities that are not in the group."""
    from vat.entity_match import company_domain
    from vat.lookup import entity_key
    from vat.step_al import check_input

    problems = check_input(rows)
    domains = sum(1 for i in inputs if company_domain(i.website, i.email))
    keys = {entity_key(i) for i in inputs} - {None}
    group = {(str(r["Customer"]), SHEET_PREFIX.get(r["Source sheet"])) for r in rows}
    fr = final_rows()
    f_inputs = lookup_inputs([r for _, r in fr])
    outside = []
    for (sheet, r), inp in zip(fr, f_inputs):
        if entity_key(inp) in keys and (str(r.get("Customer")), sheet.split(" - ")[0]) not in group:
            outside.append({"sheet": sheet, "customer": r.get("Customer"), "name": r.get("Name"),
                            "city": r.get("City"), "decision": r.get("VAT_Decision"), "proposed": r.get("VAT_Proposed")})
    return {"problems": problems, "rows": len(rows), "entities": len(keys), "company_email_domain": domains,
            "outside": outside}


async def run_second_look(settings: Any, client: Any, inputs: list[Any]):
    from vat.lookup import run_lookups
    from vat.step_al import SecondLook

    engine = SecondLook(settings, client)
    try:
        results = await run_lookups(engine, inputs)
    finally:
        await engine.aclose()
    return engine, results


async def run_agent_test(settings: Any, client: Any, inputs: list[Any], results: list[Any], rows: list[dict],
                         budget: float):
    """Step 5: the agent pass on the test entities that ended "nothing
    passes" and have a legal form, told the old number is another company's;
    its answer held to the same gate."""
    from vat.lookup import run_lookups
    from vat.lookup_agent import hosts_tried, make_agent
    from vat.name_prep import prepare
    from vat.step_al import NOTHING_PASSES, gate

    picked, ctx = [], {}
    for inp, r, row in zip(inputs, results, rows):
        al = (r.usage or {}).get("al") or {} if r is not None else {}
        if al.get("outcome") != NOTHING_PASSES or not prepare(inp.names).form:
            picked.append(None)
            continue
        picked.append(inp)
        ctx[str(inp.customer)] = {
            "name_class": (r.usage or {}).get("name_class"), "search_name": (r.usage or {}).get("search_name"),
            "af_class": "AL",
            "af_why": (f"The only number found so far, {row.get('VAT_Proposed')} on {row.get('VAT_Found_Source_URL')}, "
                       f"belongs to another company ({row.get('Found_Name')}): do not report it. Searches by the "
                       "exact legal name and by the street address found no page that gives this "
                       "organisation's own number."),
            "hosts_tried": hosts_tried(r.usage or {}), "sibling_vat_ids": []}
    agent = make_agent(settings, client)
    agent.meter.budget = max(budget, 0.0)
    agent.context = ctx
    if not ctx:
        return agent, [None] * len(inputs), []
    try:
        ran = await run_lookups(agent, picked, concurrency=10)
    finally:
        await agent.aclose()
    report = []
    seen = set()
    for inp, a in zip(picked, ran):
        if inp is None or a is None or a.entity in seen:
            continue
        seen.add(a.entity)
        plan = prepare(inp.names)
        text = None
        if a.value and a.source_url:
            page = await agent._pages._fetch(a.source_url, set())
            from enrichment.page_corroborator import _readable_text
            text = _readable_text(page.content) if page is not None and page.ok and page.content else None
        passed, why = gate(a, plan, text) if a.value else (None, "NO_NUMBER")
        report.append({"customer": inp.customer, "name": inp.name_1, "value": a.value, "found_name": a.attributed_to,
                       "address": a.address_match, "source": a.source_url, "decision": a.decision,
                       "gate": why or "PASS", "status": a.status,
                       "cost": round(float(((a.usage or {}).get("costs") or {}).get("Total_USD") or 0), 4)})
    return agent, ran, report


def outcomes(rows: list[dict], inputs: list[Any], results: list[Any]) -> list[dict[str, Any]]:
    from vat.step_al import row_outcome

    return [row_outcome(r, row, " ".join(n for n in inp.names if n), inp.postal_code, inp.city)
            for row, inp, r in zip(rows, inputs, results)]


def ledger(lookups: list[Any], step: str, input_name: str, started: datetime) -> float:
    from vat import costs

    log = [u for e in lookups for u in e.usage_log]
    table, overrides = lookups[0].meter.table, lookups[0].meter.overrides
    run = costs.run_costs(log, table, overrides)
    row = costs.ledger_row(run, run_id=f"{started:%Y%m%dT%H%M%S}_step_al_{step.split()[-1]}",
                           timestamp=started.isoformat(timespec="seconds"), step=step, input_file=input_name,
                           engine="llm_web/step_al")
    costs.append_ledger(row)
    print(f"  cost: Total={run['Total_USD']:.4f} New_Spend={run['New_Spend_USD']:.4f} "
          f"web_search_calls={run['web_search_calls']} cache_hits={run['cache_hits']}/{run['calls']}")
    return run["New_Spend_USD"]


def write_test(path: Path, rows, inputs, results, cols, agent_report) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Test 40"
    head = ["Customer", "Source sheet", "SAP name", "SAP town", "Stratum", "Old number", "Old Found_Name",
            "Old decision", "Route", "New number", "New Found_Name", "New Found_Address", "New source URL",
            "New decision", "Name_Check", "Address_Check", "Issue_Flags", "AL_Outcome", "Not re-checked reason",
            "Attempts", "VAT_Why", "Cost USD"]
    ws.append(head)
    for row, inp, c in zip(rows, inputs, cols):
        ws.append([row.get("Customer"), row.get("Source sheet"), " ".join(n for n in inp.names if n), inp.city,
                   row.get("_stratum"), row.get("VAT_Proposed"), row.get("Found_Name"), row.get("VAT_Decision"),
                   c.get("AL_Route"), c.get("VAT_Proposed") if c["AL_Outcome"] != "not re-checked" else None,
                   c.get("Found_Name") if c["AL_Outcome"] in ("new number", "old number confirmed") else None,
                   c.get("Found_Address") if c["AL_Outcome"] in ("new number", "old number confirmed") else None,
                   c.get("VAT_Found_Source_URL") if c["AL_Outcome"] in ("new number", "old number confirmed") else None,
                   c.get("VAT_Decision"), c.get("Name_Check"), c.get("Address_Check"), c.get("Issue_Flags"),
                   c.get("AL_Outcome"), c.get("AL_Not_Rechecked_Reason"), c.get("AL_Attempts"), c.get("VAT_Why"),
                   c.get("AL_Cost_USD")])
    if agent_report:
        wa = wb.create_sheet("Agent test")
        keys = list(agent_report[0])
        wa.append(keys)
        for a in agent_report:
            wa.append([a.get(k) for k in keys])
    wb.save(path)


def write_group(path: Path, rows: list[dict], cols: list[dict]) -> None:
    """The 689 rows: the SAP columns, then each reviewer column old and new side by side."""
    from openpyxl import Workbook

    from vat.review_columns import REVIEW_COLUMNS
    from vat.step_al import AL_COLUMNS

    result_cols = [c for c in REVIEW_COLUMNS]
    sap_cols = [k for k in rows[0] if k not in result_cols and not str(k).startswith(("VAT_Found_", "Check_"))]
    wb = Workbook()
    ws = wb.active
    ws.title = "Step AL"
    head = [*sap_cols, *AL_COLUMNS]
    for c in result_cols:
        head += [f"Old {c}", f"New {c}"]
    ws.append(head)
    for row, c in zip(rows, cols):
        line = [row.get(k) for k in sap_cols] + [c.get(k) for k in AL_COLUMNS]
        for k in result_cols:
            line += [row.get(k), c.get(k)]
        ws.append(line)
    ws.freeze_panes = "B2"
    wb.save(path)


def merge_final(rows: list[dict], cols: list[dict], out: Path) -> dict[str, Any]:
    """The submission workbook with the group's rows replaced: matched by
    Customer + Source sheet (each exactly once, else stop), moved to the sheet
    of their new decision; the withdrawn columns added to every sheet."""
    from copy import copy

    from openpyxl import load_workbook

    from vat.review_columns import REVIEW_COLUMNS

    wb = load_workbook(FINAL)
    want = {(str(r["Customer"]), SHEET_PREFIX[r["Source sheet"]]): c for r, c in zip(rows, cols)}
    headers: dict[str, list[str]] = {}
    for ws in wb.worksheets:
        h = [c.value for c in ws[1]]
        for name in WITHDRAWN_COLUMNS:
            if name not in h:
                cell = ws.cell(row=1, column=len(h) + 1, value=name)
                src = ws.cell(row=1, column=len(h))
                cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill),
                                                                     copy(src.alignment), copy(src.border))
                h.append(name)
        headers[ws.title] = h
    found: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for ws in wb.worksheets:
        ci = headers[ws.title].index("Customer")
        prefix = ws.title.split(" - ")[0]
        for r in range(2, ws.max_row + 1):
            v = ws.cell(row=r, column=ci + 1).value
            if v is not None and (str(v), prefix) in want:
                found.setdefault((str(v), prefix), []).append((ws.title, r))
    bad = {k: len(found.get(k, [])) for k in want if len(found.get(k, [])) != 1}
    if bad:
        raise SystemExit(f"{len(bad)} rows do not match exactly once in {FINAL.name}: {list(bad.items())[:5]}")
    write_cols = [c for c in REVIEW_COLUMNS]
    moves: dict[str, list[int]] = {}
    moved = Counter()
    for key, c in want.items():
        sheet, r = found[key][0]
        ws = wb[sheet]
        target = f"{key[1]} - {c['VAT_Decision'] or 'Not found'}"
        h = headers[sheet]
        values = [ws.cell(row=r, column=j + 1).value for j in range(len(h))]
        styles = [ws.cell(row=r, column=j + 1)._style for j in range(len(h))]
        for name in write_cols:
            if name in h:
                values[h.index(name)] = c.get(name)
        if target == sheet:
            for j, v in enumerate(values):
                ws.cell(row=r, column=j + 1).value = v
            continue
        dest = wb[target]
        th = headers[target]
        new_r = dest.max_row + 1
        for j, name in enumerate(th):
            cell = dest.cell(row=new_r, column=j + 1, value=values[h.index(name)] if name in h else None)
            if name in h:
                cell._style = copy(styles[h.index(name)])
        moves.setdefault(sheet, []).append(r)
        moved[(sheet, target)] += 1
    for sheet, rs in moves.items():
        for r in sorted(rs, reverse=True):
            wb[sheet].delete_rows(r)
    for ws in wb.worksheets:
        if ws.auto_filter.ref:
            ws.auto_filter.ref = ws.dimensions
    wb.save(out)
    return {"moved": {f"{a} -> {b}": n for (a, b), n in moved.items()},
            "sheets": {ws.title: ws.max_row - 1 for ws in wb.worksheets}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("check", "test", "full"))
    ap.add_argument("--budget", type=float, default=0.0, help="Cost cap in USD (VAT_WEB_BUDGET_USD) for the run.")
    ap.add_argument("--cache-dir", default=r"C:\vatc\al")
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--no-agent", action="store_true", help="test: skip the agent measurement.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    rows = read_rows(INPUT)
    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir
    inputs = lookup_inputs(rows)
    report = check(rows, inputs)
    print(f"Input: {report['rows']} rows, {report['entities']} entities; "
          f"{report['company_email_domain']} rows with a company email domain")
    print(f"Problems: {report['problems'] or 'none'}")
    print(f"Rows of these entities outside the 689 in the final workbook: {len(report['outside'])}")
    for o in report["outside"]:
        print(f"  {o['sheet']}: {o['customer']} {o['name']} ({o['city']}) {o['decision']} {o['proposed']}")
    if report["problems"]:
        return 1
    if args.mode == "check":
        return 0
    if args.budget <= 0:
        raise SystemExit("--budget is required for a live run.")

    from config import get_settings
    from vat import costs
    from vat.lookup import entity_key
    from vat.name_prep import prepare
    from vat.step_al import pick_test_rows, stratum_of
    from vat.vies_client import VIESClient

    settings = replace(get_settings(), vat_web_budget_usd=args.budget, vat_vies_in_lookup=False,
                       vat_web_query_mode="hybrid")
    client = VIESClient(settings)
    keys = [entity_key(i) for i in inputs]
    forms = [bool(prepare(i.names).form) for i in inputs]
    if args.mode == "test":
        idx = pick_test_rows(rows, keys, forms, args.seed)
        rows = [dict(rows[i], _stratum=stratum_of(rows[i], forms[i])) for i in idx]
        inputs = [inputs[i] for i in idx]
    before = costs.ledger_total()
    started = datetime.now(timezone.utc).astimezone()
    print(f"Ledger before: {before:.4f} USD. Running {len(inputs)} rows, cap {args.budget} USD ...")
    engine, results = asyncio.run(run_second_look(settings, client, inputs))
    cols = outcomes(rows, inputs, results)
    engines, agent_report = [engine], []
    if args.mode == "test" and not args.no_agent:
        agent, _, agent_report = asyncio.run(run_agent_test(
            settings, client, inputs, results, rows, args.budget - engine.meter.spent))
        engines.append(agent)
    step = "AL test" if args.mode == "test" else "AL full"
    new_spend = ledger(engines, step, INPUT.name, started)
    usage_out = _ROOT / "logs" / f"vat_step_al_{args.mode}_usage.json"
    usage_out.write_text(json.dumps([u for e in engines for u in e.usage_log], indent=1, ensure_ascii=False,
                                    default=str), encoding="utf-8")
    print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {new_spend:.4f}); usage {usage_out.name}")

    c = Counter(x["AL_Outcome"] for x in cols)
    routes = Counter(x["AL_Route"] for x in cols if x["AL_Outcome"] in ("new number", "old number confirmed"))
    new_dec = Counter((x["AL_Outcome"], x["VAT_Decision"]) for x in cols)
    print(f"Outcomes (rows): {dict(c)}")
    print(f"Answering route (rows): {dict(routes)}")
    print(f"Outcome x decision: {dict(new_dec)}")
    print(f"Not re-checked reasons: {dict(Counter(x['AL_Not_Rechecked_Reason'] for x in cols if x['AL_Not_Rechecked_Reason']))}")
    entities = len({k for k in keys if k}) if args.mode == "full" else len(set(entity_key(i) for i in inputs))
    print(f"Entities: {entities}; cost per entity {new_spend / max(entities, 1):.4f} USD (new spend)")
    if args.mode == "test":
        lookup_cost = engine.meter.spent
        n = len(set(entity_key(i) for i in inputs))
        all_entities = len({k for k in keys if k})
        print(f"Second look: {lookup_cost:.4f} USD for {n} entities = {lookup_cost / max(n, 1):.4f}/entity; "
              f"projected for all {all_entities} entities x 1.3: {lookup_cost / max(n, 1) * all_entities * 1.3:.2f} USD")
        if agent_report:
            hits = sum(1 for a in agent_report if a["gate"] == "PASS")
            spent = engines[1].meter.spent
            print(f"Agent test: {len(agent_report)} entities, {hits} passed the gate, "
                  f"{spent:.4f} USD = {spent / max(len(agent_report), 1):.4f}/entity")
            for a in agent_report:
                print(f"  {a}")
        write_test(TEST_OUT, rows, inputs, results, cols, agent_report)
        print(f"Wrote {TEST_OUT}")
    else:
        write_group(GROUP_OUT, rows, cols)
        print(f"Wrote {GROUP_OUT}")
        print(f"Merge: {merge_final(rows, cols, FINAL_OUT)}")
        print(f"Wrote {FINAL_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
