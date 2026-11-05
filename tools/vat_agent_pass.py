"""Step AG: the agentic pass (``vat/lookup_agent.py``) on the rows the hybrid
method left without a number.

Reads the ``Still_no_number`` sheets of the baseline reports (NOT_FOUND and
LOOKUP_INCOMPLETE rows; the INFERRED public bodies are not on them), the
records from their input workbooks, what the hybrid run knew about each
entity from its usage JSON (Name-2 class, search name, hosts tried), and the
VAT IDs on sibling records of the same entity key in the full DE export (the
record itself excluded). Writes one row per record with the result, the
decision and tier, searches and opens used, cost and the value against VAT
Actual; the usage JSON; one cost-ledger row. Found numbers are queued for
VIES (PENDING), not checked.

    python tools/vat_agent_pass.py --set REPORT INPUT USAGE [--set ...] --export CMD.xlsx \\
        --out logs/DE_VAT_AG_19.xlsx [--step "AG agent pass"]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _norm(v: Any) -> str | None:
    return re.sub(r"[^0-9A-Z]", "", str(v or "").upper()) or None


def _sheet(path: Path, name: str = "Data") -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    ws = wb[name] if name in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def _inputs(rows: list[dict[str, Any]]):
    """``customer → LookupInput`` as ``vat.validate.validate_rows`` builds them."""
    from vat.lookup import LookupInput
    from vat.validate import _LOOKUP_ROLE_FIELDS, _cell, _flag_set, resolve_columns

    headers: dict[str, None] = {}
    for r in rows:
        headers.update(dict.fromkeys(r))
    cols, lcols = resolve_columns(headers), resolve_columns(headers, _LOOKUP_ROLE_FIELDS)
    out = {}
    for row in rows:
        customer = _cell(row, cols["customer"])
        out[customer] = LookupInput(
            customer=customer, names=tuple(_cell(row, cols[f"name_{n}"]) for n in range(1, 5)),
            street=_cell(row, lcols["street"]), house_number=_cell(row, lcols["house_number"]),
            postal_code=_cell(row, lcols["postal_code"]), city=_cell(row, lcols["city"]),
            country=_cell(row, cols["country"]) or "DE", website=_cell(row, lcols["website"]),
            email=_cell(row, lcols["email"]), register_number=_cell(row, lcols["register_number"]),
            deleted=_flag_set(_cell(row, lcols["deleted"])))
    return out


def _siblings(export: Path, targets: dict[str, Any]) -> dict[str, list[str]]:
    """``customer → VAT IDs`` on the other records of its entity key in the
    export (the record itself excluded)."""
    from vat.lookup import entity_key

    keys = {c: entity_key(inp) for c, inp in targets.items()}
    wanted = {k for k in keys.values() if k}
    rows = [r for r in _sheet(export) if r.get("VAT Registration No.")]
    vat_of = {str(r.get("Customer")): _norm(r.get("VAT Registration No.")) for r in rows}
    by_key: dict[str, set[str]] = {}
    for c, inp in _inputs(rows).items():
        k = entity_key(inp)
        if k in wanted:
            by_key.setdefault(k, set()).add(c)
    return {c: sorted({vat_of[o] for o in by_key.get(k, ()) if o != c and vat_of.get(o)})
            for c, k in keys.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", nargs=3, action="append", metavar=("REPORT", "INPUT", "USAGE"), required=True)
    ap.add_argument("--export", required=True, help="The full DE export, for sibling records' VAT IDs.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", default=os.getenv("VAT_RUN_STEP", "AG agent pass"))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--dry-run", action="store_true", help="Print the inputs and context; no model call.")
    ap.add_argument("--limit", type=int, default=0, help="Only the first N rows (a smoke test).")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from config import get_settings
    from vat import costs, llm_cache
    from vat.lookup import for_record, vies_pending
    from vat.lookup_agent import AgentLookup, hosts_tried
    from vat.vies_client import VIESClient
    from vat.vies_retry import ViesRetryQueue, enqueue

    llm_cache.WEB_SEARCH_TIMEOUT = 600.0  # 13 tool calls at medium effort

    targets, sap, af, context = {}, {}, {}, {}
    for report, inp_path, usage_path in args.set:
        records = _inputs(_sheet(Path(inp_path)))
        usage = json.loads(Path(usage_path).read_text(encoding="utf-8"))
        by_customer = {str(u.get("customer")): u for u in usage}
        by_name = {u.get("name_1"): u for u in usage}
        for row in _sheet(Path(report), "Still_no_number"):
            c = str(row["Customer"])
            inp = records[c]
            targets[c] = inp
            sap[c] = _norm(row.get("VAT Actual"))
            entry = by_customer.get(c) or by_name.get(inp.name_1) or {}
            af[c] = {"status": row.get("Status"), "class": row.get("A-F"), "why": row.get("Why"),
                     "set": Path(report).stem}
            context[c] = {"name_class": entry.get("name_class"), "search_name": entry.get("search_name"),
                          "af_class": row.get("A-F"), "af_why": row.get("Why"), "hosts_tried": hosts_tried(entry)}
    if args.limit:
        targets = dict(list(targets.items())[:args.limit])
    siblings = _siblings(Path(args.export), targets)
    for c in targets:
        context[c]["sibling_vat_ids"] = siblings.get(c, [])

    settings = get_settings()
    client = VIESClient(settings)
    engine = AgentLookup(settings, client, context=context)
    if args.dry_run:
        from vat.name_prep import prepare
        for c, inp in targets.items():
            print(f"{c} {inp.name_1} | {inp.city} | SAP {sap[c]} | {json.dumps(context[c], ensure_ascii=False)}")
        first = next(iter(targets.values()))
        print(engine.agent_prompt(first, prepare(first.names))[-1800:])
        return 0
    started = datetime.now(timezone.utc).astimezone()

    async def run_all():
        out = {}
        try:
            for c, inp in targets.items():
                out[c] = vies_pending(for_record(await engine.lookup(inp), inp))
                print(f"{c} {inp.name_1[:40]:40} {out[c].status:28} {out[c].value} "
                      f"spent {engine.meter.spent:.3f}", flush=True)
        finally:
            await engine.aclose()
        return out

    results = asyncio.run(run_all())
    if client.enabled and not client.frozen:
        queue = ViesRetryQueue.for_client(client)
        if enqueue(queue, results.values()):
            queue.save()

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Rows"
    head = ["Customer", "Set", "Name 1", "City", "A-F", "Hybrid status", "Siblings' VAT IDs", "Found", "Value",
            "VAT Actual", "vs SAP", "Decision", "Tier", "Status", "Issues", "Source URL", "Attributed to",
            "Agent value", "Agent source", "Agent confidence", "Agent relation", "Agent notes", "Searches",
            "Opens", "Pages opened", "Cost USD", "New spend USD", "Flags"]
    ws.append(head)
    log = {str(u["customer"]): u for u in engine.usage_log}
    for c, r in results.items():
        u = log.get(c, {})
        v, s = _norm(r.value), sap[c]
        tier = r.confidence
        ws.append([c, af[c]["set"], targets[c].name_1, targets[c].city, af[c]["class"], af[c]["status"],
                   ", ".join(context[c]["sibling_vat_ids"]), bool(v), r.value, s,
                   ("SAP_BLANK" if not s else "MATCHES_SAP" if v == s else "DIFFERS" if v else "NO_PROPOSAL"),
                   r.decision, tier, r.status, "; ".join(r.all_issues), r.source_url, r.attributed_to,
                   u.get("agent_value"), u.get("agent_source"), u.get("agent_confidence"), u.get("agent_relation"),
                   u.get("agent_notes"), u.get("agent_searches"), u.get("agent_opens"),
                   "\n".join(u.get("agent_pages_opened") or []),
                   (u.get("costs") or {}).get("Total_USD"), (u.get("costs") or {}).get("New_Spend_USD"),
                   "; ".join(r.flags)])
    out = Path(args.out)
    wb.save(out)
    usage_out = out.with_name(out.stem + "_usage.json")
    usage_out.write_text(json.dumps(engine.usage_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Wrote {out} and {usage_out}")

    run = costs.run_costs(engine.usage_log, engine.meter.table, engine.meter.overrides)
    print("  cost: " + " ".join(f"{k}={run[k]:.4f}" for k in costs.LINES)
          + f" web_search_calls={run['web_search_calls']} page_open_calls={run.get('page_open_calls')}")
    row = costs.ledger_row(run, run_id=f"{started:%Y%m%dT%H%M%S}_{out.stem}",
                           timestamp=started.isoformat(timespec="seconds"), step=args.step,
                           input_file="Still_no_number (AF 50 + 53)", engine="llm_web/agent")
    ledger = Path(args.ledger) if args.ledger else costs.LEDGER_FILE
    costs.append_ledger(row, ledger)
    print(f"  ledger {ledger}: {costs.ledger_total(ledger):.4f} USD spent to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
