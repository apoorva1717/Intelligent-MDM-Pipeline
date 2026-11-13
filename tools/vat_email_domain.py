"""Step AM: the email domain for Not found rows (vat/email_look.py, vat/email_domain.py).

Usage::

    python tools/vat_email_domain.py count               # rows and why a domain is (not) used, no calls
    python tools/vat_email_domain.py test --budget 3     # 40 rows, route B only

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_persons.xlsx, only read. Rows: the
Not found sheets' rows with reason NO_WEBSITE_FOUND, not a private person
(Customer_Type PERSON), whose email domain is the customer's own.

Output (test): data/VAT/DE_VAT_EMAIL_test40.xlsx (not committed). Every model
call goes through the LLM cache in ``--cache-dir`` and the cost ledger (step
"AM test"). No search, no Peppol, no VIES.
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

from tools.vat_step_al import lookup_inputs  # noqa: E402

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_persons.xlsx"
TEST_OUT = _ROOT / "data" / "VAT" / "DE_VAT_EMAIL_test40.xlsx"
TEST_ROWS = 40
EMAIL_FIELDS = ("E-Mail Address", "Clrk's internet add.")


def read_rows() -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(INPUT, read_only=True)
    out = []
    for ws in wb.worksheets:
        if ws.title == "Persons":
            continue
        it = ws.iter_rows(values_only=True)
        headers = list(next(it))
        out += [dict(zip(headers, r), _sheet=ws.title, _row=i) for i, r in enumerate(it, start=2)
                if any(v is not None for v in r)]
    wb.close()
    return out


def eligible(row: dict[str, Any]) -> bool:
    return row["_sheet"].endswith("Not found") and row.get("Not_Found_Reason") == "NO_WEBSITE_FOUND" \
        and row.get("Customer_Type") != "PERSON"


def select(rows: list[dict[str, Any]], inputs: list[Any]) -> tuple[list[int], dict[int, str], Counter]:
    """Indices of the rows to look up, their domain, and why the others are not."""
    from vat.email_domain import own_domain, shared_domains, why_not
    from vat.lookup import entity_key

    emails = [[r.get(k) for k in EMAIL_FIELDS] for r in rows]
    shared = shared_domains((entity_key(i), e) for i, e in zip(inputs, emails))
    picked, domains, why = [], {}, Counter()
    for i, r in enumerate(rows):
        if not eligible(r):
            continue
        d = own_domain(emails[i], shared)
        why[why_not(emails[i], shared)] += 1
        if d:
            picked.append(i)
            domains[i] = d
    return picked, domains, why


def write_test(rows, inputs, results, cols) -> None:
    from openpyxl import Workbook

    from vat.email_look import EM_COLUMNS

    head = ["Sheet", "Customer", "Name", "Name 2", "Street", "Postal Code", "City", "Customer_Type", *EM_COLUMNS,
            "VAT_Decision", "VAT_Proposed", "Found_Name", "Found_Address", "Name_Check", "Address_Check",
            "Issue_Flags", "VAT_Found_Source_URL", "VAT_Found_Evidence", "VAT_Why"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Email test"
    ws.append(head)
    for r, c in zip(rows, cols):
        ws.append([r["_sheet"], r.get("Customer"), r.get("Name"), r.get("Name 2"), r.get("Street"),
                   r.get("Postal Code"), r.get("City"), r.get("Customer_Type"),
                   *[c.get(k) for k in head[8:]]])
    ws.freeze_panes = "A2"
    wb.save(TEST_OUT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("count", "test"))
    ap.add_argument("--budget", type=float, default=0.0, help="Cost cap in USD (VAT_WEB_BUDGET_USD).")
    ap.add_argument("--cache-dir", default=r"C:\vatc\am")
    ap.add_argument("--seed", type=int, default=20261112)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    rows = read_rows()
    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir
    inputs = lookup_inputs(rows)
    picked, domains, why = select(rows, inputs)
    print(f"Not found / NO_WEBSITE_FOUND, not a private person: {sum(why.values())} rows; {dict(why)}")
    print(f"Domains used: {len(picked)} rows, {len(set(domains.values()))} domains")
    if args.mode == "count":
        return 0
    if args.budget <= 0:
        raise SystemExit("--budget is required for a live run.")

    from config import get_settings
    from vat import costs
    from vat.email_look import EmailLook, row_outcome
    from vat.lookup import run_lookups
    from vat.vies_client import VIESClient

    idx = sorted(random.Random(args.seed).sample(picked, min(TEST_ROWS, len(picked))))
    sample = [rows[i] for i in idx]
    sample_inputs = [replace(inputs[i], website=None, email=f"info@{domains[i]}") for i in idx]
    settings = replace(get_settings(), vat_web_budget_usd=args.budget, vat_vies_in_lookup=False,
                       vat_web_query_mode="hybrid")
    engine = EmailLook(settings, VIESClient(settings))
    before = costs.ledger_total()
    started = datetime.now(timezone.utc).astimezone()
    print(f"Ledger before: {before:.4f} USD. Running {len(sample)} rows, cap {args.budget} USD ...")

    async def run():
        try:
            return await run_lookups(engine, sample_inputs)
        finally:
            await engine.aclose()

    results = asyncio.run(run())
    cols = [row_outcome(res, r, domains[i], r.get("Name"), r.get("Postal Code"), r.get("City"))
            for i, r, res in zip(idx, sample, results)]
    run_c = costs.run_costs(engine.usage_log, engine.meter.table, engine.meter.overrides)
    costs.append_ledger(costs.ledger_row(run_c, run_id=f"{started:%Y%m%dT%H%M%S}_step_am_test",
                                         timestamp=started.isoformat(timespec="seconds"), step="AM test",
                                         input_file=INPUT.name, engine="llm_web/step_am"))
    spent = run_c["New_Spend_USD"]
    (_ROOT / "logs" / "vat_step_am_test_usage.json").write_text(
        json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    deployments = Counter(c.get("deployment") for u in engine.usage_log for c in u.get("calls") or [])
    print(f"Ledger after: {costs.ledger_total():.4f} USD (this run {spent:.4f}; "
          f"{spent / max(len(sample), 1):.4f}/row; all {len(picked)} rows x 1.3 ≈ "
          f"{spent / max(len(sample), 1) * len(picked) * 1.3:.2f} USD); calls by deployment {dict(deployments)}")
    print(f"Outcomes: {dict(Counter(c['EM_Outcome'] for c in cols))}")
    print(f"New numbers by decision: {dict(Counter(c['VAT_Decision'] for c in cols if c['EM_Outcome'] == 'new number'))}")
    print(f"Attempts: {dict(Counter(a.split(':', 1)[1] for c in cols for a in (c['EM_Attempts'] or '').split('; ') if ':' in a))}")
    print(f"Not re-checked: {dict(Counter(c['EM_Not_Rechecked_Reason'] for c in cols if c['EM_Outcome'] == 'not re-checked'))}")
    write_test(sample, sample_inputs, results, cols)
    print(f"Wrote {TEST_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
