"""The person classifier (vat/person_classifier.py) on a sample of the submission workbook.

Usage::

    python tools/vat_person_test.py count                # rows asked / skipped, no calls
    python tools/vat_person_test.py test --budget 1      # the sample: classify, then the rule

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL.xlsx, only read. The sample (seed
``--seed``) is three strata of the rows the classifier is asked about:

* ``rule_person`` — the first-name rule (vat.name_prep._is_person) says person;
* ``name_shaped`` — 2–3 capitalised words, no organisation word, not on the
  rule (where non-German names hide);
* ``other`` — the rest.

For each row: the model's verdict, and what the rule would do to its
candidate (``keep`` / ``withdraw``; ``-`` for a Not found row). Nothing in
the workbook changes. Output: data/VAT/DE_VAT_PERSON_test.xlsx (not
committed). Model calls go through the LLM cache in ``--cache-dir`` and the
cost ledger (step "PERSON test").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

FINAL = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL.xlsx"
TEST_OUT = _ROOT / "data" / "VAT" / "DE_VAT_PERSON_test.xlsx"
SAMPLE = {"rule_person": 15, "name_shaped": 15, "other": 30}
FULL_OUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_persons.xlsx"
NAME_FIELDS = ("Name", "Name 2", "Name 3", "Name 4")
PERSON_NOT_NAMED = "PERSON_NOT_NAMED_ON_PAGE"
NEW_COLUMNS = ["Customer_Type", "Person_Name", "VAT_Withdrawn_Number", "VAT_Withdrawn_Found_Name",
               "VAT_Withdrawn_Source_URL"]
PERSON_SHEET_COLUMNS = ["Source sheet", "Customer", "Name", "Name 2", "Name 3", "Name 4", "Street", "House Number",
                        "Postal Code", "City", "VAT Registration No.", "Customer_Type", "Person_Name",
                        "VAT_Decision", "VAT_Proposed", "Found_Name", "Not_Found_Reason",
                        "VAT_Withdrawn_Number", "VAT_Withdrawn_Found_Name", "VAT_Why"]
_ORG_WORD_RE = re.compile(r"apothek|praxis|labor|institut|zentrum|klinik|amt\b|stadt|kreis|verband|verein|"
                          r"gesellschaft|service|technik|bau|handel|feuerwehr|polizei", re.IGNORECASE)


def final_rows() -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(FINAL, read_only=True)
    out = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        headers = list(next(it))
        out += [dict(zip(headers, r), _sheet=ws.title, _row=i) for i, r in enumerate(it, start=2)
                if any(v is not None for v in r)]
    wb.close()
    return out


def names_of(row: dict[str, Any]) -> list[Any]:
    return [row.get(k) for k in NAME_FIELDS]


def stratum_of(row: dict[str, Any]) -> str:
    from vat.name_prep import _is_person, prepare

    if _is_person(prepare(names_of(row)).entity or ""):
        return "rule_person"
    words = str(row.get("Name") or "").split()
    if 2 <= len(words) <= 3 and all(w[:1].isupper() for w in words) and not _ORG_WORD_RE.search(
            " ".join(str(n) for n in names_of(row) if n)):
        return "name_shaped"
    return "other"


def pick(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["_stratum"], []).append(r)
    return [r for s, n in SAMPLE.items() for r in rng.sample(by.get(s, []), min(n, len(by.get(s, []))))]


def mini_only(engine: Any) -> None:
    """Stop unless every call goes to the helper (mini) deployment."""
    if "mini" not in (engine.helper_model or "").lower():
        raise SystemExit(f"VAT_HELPER_MODEL is {engine.helper_model!r}, not a mini deployment: stopped.")


async def classify(engine: Any, rows: list[dict[str, Any]], concurrency: int = 5) -> list[dict[str, Any] | None]:
    """One call per distinct record text (duplicates share the answer)."""
    from vat import person_classifier as pc
    from vat.lookup import FoundResult

    mini_only(engine)
    lim = replace(pc.limits(engine.helper_model, engine.limits.retry_backoff),
                  max_retries=engine.limits.max_retries)
    prompts = [pc.prompt(names_of(r), r.get("City")) for r in rows]
    first = {}
    for r, p in zip(rows, prompts):
        first.setdefault(p, r)

    async def one(p, row):
        usage: dict[str, Any] = {}
        out = await engine._call(pc.LABEL, *p, lim, usage, effort=pc.EFFORT)
        verdict = None if isinstance(out, FoundResult) else pc.verdict_of(out[0])
        engine.usage_log.append({"customer": row.get("Customer"), "name_1": row.get("Name"),
                                 "status": "OK" if verdict else "FAILED", "calls": usage.get("calls") or []})
        return verdict

    sem = asyncio.Semaphore(concurrency)

    async def guarded(p, row):
        async with sem:
            return await one(p, row)

    answers = dict(zip(first, await asyncio.gather(*(guarded(p, r) for p, r in first.items()))))
    deployments = {c.get("deployment") for u in engine.usage_log for c in u["calls"]}
    if deployments - {engine.helper_model}:
        raise SystemExit(f"Calls went to {deployments}, not only {engine.helper_model}: stopped.")
    return [answers[p] for p in prompts]


def action_of(row: dict[str, Any], verdict: dict[str, Any] | None) -> str:
    from vat import person_classifier as pc

    if not row.get("VAT_Proposed"):
        return "-"
    return "withdraw" if pc.unnamed_person(verdict, row.get("Found_Name")) else "keep"


def ledger(engine: Any, started: datetime, step: str = "PERSON test") -> float:
    from vat import costs

    run = costs.run_costs(engine.usage_log, engine.meter.table, engine.meter.overrides)
    costs.append_ledger(costs.ledger_row(run, run_id=f"{started:%Y%m%dT%H%M%S}_person_{step.split()[-1]}",
                                         timestamp=started.isoformat(timespec="seconds"), step=step,
                                         input_file=FINAL.name, engine="name2_person"))
    return run["New_Spend_USD"]


def withdrawn_values(v: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """A row's columns once its candidate is withdrawn: Not found, the old
    number kept in the withdrawn columns."""
    from vat.lookup import NO_PROPOSAL, SAP_BLANK

    found = v.get("Found_Name")
    return {
        "VAT_Proposed": None, "VAT_Decision": "Not found", "VAT_Found_Confidence": "NOT_FOUND",
        "VAT_Found_Match_Score": None,
        "VAT_Why": f"The customer is a person ({verdict['person_name']}); the only number found belongs to "
                   f"'{found}', which does not name that person.",
        "Found_Name": None, "Found_Address": None, "Name_Check": None, "Address_Check": None,
        "Issue_Flags": f"WITHDRAWN:{v.get('VAT_Proposed')}", "Not_Found_Reason": PERSON_NOT_NAMED,
        "Agent_Pass": None, "VIES": None, "VAT_Found_Source_URL": None, "VAT_Found_Source_Type": None,
        "VAT_Found_Source_Class": None, "VAT_Found_Page_Title": None, "VAT_Found_Evidence": None,
        "VAT_Found_vs_SAP": SAP_BLANK if v.get("VAT_Found_vs_SAP") == SAP_BLANK else NO_PROPOSAL,
        "VAT_Withdrawn_Number": v.get("VAT_Proposed"), "VAT_Withdrawn_Found_Name": found,
        "VAT_Withdrawn_Source_URL": v.get("VAT_Found_Source_URL"),
    }


def apply_to_copy(rows: list[dict[str, Any]], verdicts: list[Any], out: Path) -> dict[str, Any]:
    """The submission workbook, saved as *out*: Customer_Type and Person_Name
    on every row; a person's candidate whose page does not name the person
    withdrawn and moved to "<prefix> - Not found"; a last sheet "Persons"."""
    from copy import copy

    from openpyxl import load_workbook

    from vat import person_classifier as pc

    wb = load_workbook(FINAL)
    headers: dict[str, list[str]] = {}
    for ws in wb.worksheets:
        h = [c.value for c in ws[1]]
        while h and h[-1] is None:
            h.pop()
        for name in NEW_COLUMNS:
            if name not in h:
                cell = ws.cell(row=1, column=len(h) + 1, value=name)
                src = ws.cell(row=1, column=len(h))
                cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill),
                                                                     copy(src.alignment), copy(src.border))
                h.append(name)
        headers[ws.title] = h
    by_key = {(r["_sheet"], r["_row"]): (r, v) for r, v in zip(rows, verdicts)}
    moves: dict[str, list[int]] = {}
    persons, moved, withdrawn = [], Counter(), []
    for title in list(headers):
        ws, h = wb[title], headers[title]
        for i in range(2, ws.max_row + 1):
            if (title, i) not in by_key:
                continue
            row, verdict = by_key[(title, i)]
            values = {name: ws.cell(row=i, column=j + 1).value for j, name in enumerate(h)}
            ctype = pc.customer_type(pc.skip(names_of(row)), verdict)
            values["Customer_Type"] = ctype
            values["Person_Name"] = verdict["person_name"] if pc.is_person(verdict) else None
            old_decision = values.get("VAT_Decision")
            if values.get("VAT_Proposed") and pc.unnamed_person(verdict, values.get("Found_Name")):
                values.update(withdrawn_values(values, verdict))
                withdrawn.append((title, old_decision, values))
            if pc.is_person(verdict):
                persons.append((old_decision, values))
            target = f"{title.split(' - ')[0]} - {values['VAT_Decision']}"
            if target == title:
                for j, name in enumerate(h):
                    ws.cell(row=i, column=j + 1).value = values.get(name)
                continue
            dest, th = wb[target], headers[target]
            new_r = dest.max_row + 1
            for j, name in enumerate(th):
                cell = dest.cell(row=new_r, column=j + 1, value=values.get(name))
                if name in h:
                    cell._style = copy(ws.cell(row=i, column=h.index(name) + 1)._style)
            moves.setdefault(title, []).append(i)
            moved[(title, target)] += 1
    for title, rs in moves.items():
        for i in sorted(rs, reverse=True):
            wb[title].delete_rows(i)
    ps = wb.create_sheet("Persons")
    ref = wb.worksheets[0]
    cols = ["Old_Decision", *[c for c in PERSON_SHEET_COLUMNS]]
    ps.append(cols)
    for j in range(len(cols)):
        src = ref.cell(row=1, column=1)
        cell = ps.cell(row=1, column=j + 1)
        cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill), copy(src.alignment),
                                                             copy(src.border))
    for old, values in sorted(persons, key=lambda p: (str(p[1].get("Source sheet")), str(p[1].get("Name")))):
        ps.append([old, *[values.get(c) for c in PERSON_SHEET_COLUMNS]])
    ps.freeze_panes = "A2"
    ps.auto_filter.ref = ps.dimensions
    for ws in wb.worksheets:
        if ws.auto_filter.ref:
            ws.auto_filter.ref = ws.dimensions
    wb.save(out)
    return {"moved": {f"{a} -> {b}": n for (a, b), n in moved.items()},
            "withdrawn_by_old_decision": dict(Counter(d for _, d, _ in withdrawn)),
            "withdrawn_matching_sap": sum(1 for *_, v in withdrawn
                                          if str(v.get("VAT Registration No.") or "").replace(" ", "")
                                          == str(v.get("VAT_Withdrawn_Number") or "")),
            "persons": len(persons), "sheets": {ws.title: ws.max_row - 1 for ws in wb.worksheets}}


def write(rows: list[dict[str, Any]], verdicts: list[Any]) -> None:
    from openpyxl import Workbook

    cols = ["Stratum", "Sheet", "Customer", *NAME_FIELDS, "City", "Model_Class", "Person_Name", "Confidence",
            "Model_Reason", "VAT_Decision", "VAT_Proposed", "Found_Name", "Rule_Action"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Person test"
    ws.append(cols)
    for r, v in zip(rows, verdicts):
        v = v or {}
        ws.append([r["_stratum"], r["_sheet"], r.get("Customer"), *names_of(r), r.get("City"), v.get("class"),
                   v.get("person_name"), v.get("confidence"), v.get("reason"), r.get("VAT_Decision"),
                   r.get("VAT_Proposed"), r.get("Found_Name"), action_of(r, v or None)])
    ws.freeze_panes = "A2"
    wb.save(TEST_OUT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("count", "test", "full"))
    ap.add_argument("--budget", type=float, default=0.0, help="Cost cap in USD (VAT_WEB_BUDGET_USD).")
    ap.add_argument("--cache-dir", default=r"C:\vatc\person")
    ap.add_argument("--seed", type=int, default=20261111)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    from vat import person_classifier as pc

    rows = final_rows()
    skipped = Counter(pc.skip(names_of(r)) or "ASK" for r in rows)
    print(f"Rows: {len(rows)}; {dict(skipped)}")
    asked = [dict(r, _stratum=stratum_of(r)) for r in rows if not pc.skip(names_of(r))]
    print(f"Asked, by stratum: {dict(Counter(r['_stratum'] for r in asked))}")
    if args.mode == "count":
        return 0
    if args.budget <= 0:
        raise SystemExit("--budget is required for a live run.")

    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir
    from config import get_settings
    from vat import costs
    from vat.lookup_web import WebSearchLookup
    from vat.vies_client import VIESClient

    settings = replace(get_settings(), vat_web_budget_usd=args.budget, vat_vies_in_lookup=False)
    engine = WebSearchLookup(settings, VIESClient(settings))
    mini_only(engine)
    if args.mode == "full":
        before = costs.ledger_total()
        started = datetime.now(timezone.utc).astimezone()
        print(f"Ledger before: {before:.4f} USD. Classifying {len(asked)} rows on {engine.helper_model}, "
              f"cap {args.budget} USD ...")
        got = asyncio.run(classify(engine, asked, concurrency=10))
        spent = ledger(engine, started, "PERSON full")
        print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {spent:.4f}); "
              f"calls {len(engine.usage_log)}, failed {sum(1 for u in engine.usage_log if u['status'] != 'OK')}")
        (_ROOT / "logs" / "vat_person_full_usage.json").write_text(
            json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        verdict_of = {(r["_sheet"], r["_row"]): v for r, v in zip(asked, got)}
        verdicts = [verdict_of.get((r["_sheet"], r["_row"])) for r in rows]
        print(f"Customer_Type: {dict(Counter(pc.customer_type(pc.skip(names_of(r)), v) for r, v in zip(rows, verdicts)))}")
        print(f"Applied: {apply_to_copy(rows, verdicts, FULL_OUT)}")
        print(f"Wrote {FULL_OUT}")
        return 0
    sample = pick(asked, args.seed)
    before = costs.ledger_total()
    started = datetime.now(timezone.utc).astimezone()
    print(f"Ledger before: {before:.4f} USD. Classifying {len(sample)} rows, cap {args.budget} USD ...")
    verdicts = asyncio.run(classify(engine, sample))
    spent = ledger(engine, started)
    print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {spent:.4f}; "
          f"{spent / max(len(sample), 1):.5f}/row; all {len(asked)} asked rows ≈ "
          f"{spent / max(len(sample), 1) * len(asked):.2f} USD)")
    (_ROOT / "logs" / "vat_person_test_usage.json").write_text(
        json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    for s in SAMPLE:
        part = [(r, v) for r, v in zip(sample, verdicts) if r["_stratum"] == s]
        print(f"{s}: {dict(Counter((v or {}).get('class', 'FAILED') for _, v in part))}; "
              f"rule: {dict(Counter(action_of(r, v) for r, v in part))}")
    write(sample, verdicts)
    print(f"Wrote {TEST_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
