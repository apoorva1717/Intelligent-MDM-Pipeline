"""Validate the VAT IDs in an SAP customer export. Excel in, Excel out.

Usage::

    python tools/vat_validate_xlsx.py tests/fixtures/vat/VAT_TEST_EXPORT_30.xlsx
    python tools/vat_validate_xlsx.py export.xlsx --out export_vat.xlsx
    python tools/vat_validate_xlsx.py export.xlsx --frozen --cache-dir logs/cache/vat

Reads sheet ``Data`` (falling back, with a warning, to the active sheet when
there is none), runs every row through the same code as
``POST /api/validate/vat`` — the workbook handling itself is ``vat/xlsx.py``,
shared with ``POST /api/validate/vat/file`` — and writes a copy with:

* the ``VAT_*`` result columns appended to the data sheet — every input cell,
  every other sheet and all formatting left as they were;
* a ``Summary`` sheet — counts per state, per Country/Region Key, and unique
  customers against rows.

The input file is never modified. Run on a file this tool already wrote, the
``VAT_*`` columns are overwritten in place rather than appended twice.

``--frozen`` sets CACHE_FROZEN: no VIES call is made, a number with no
recording is SERVICE_UNAVAILABLE / FROZEN_CACHE_MISS.

The VIES queue (``vat/vies_retry.py``, Step AF-2): a lookup asks VIES
nothing; its candidates are PENDING and queued. The runner asks VIES for
the queue's due numbers at start-up (left over from earlier runs) and at the
end of the run, in one batch, and writes the answers into the output
workbook. ``--no-vies-queue`` skips both (the rows stay PENDING; run
``tools/vat_vies_retry.py --workbook`` later). A frozen run never does.

``--lookup-missing {none,grounded,llm_web,llm_only}`` (default ``llm_web``;
``none`` is the behaviour from before the lookups; ``grounded`` needs
VAT_GROUNDED_FALLBACK=true) proposes VAT IDs for rows with an empty VAT Registration
No. and Country/Region Key DE in the ``VAT_Found_*`` columns — proposals only,
the VAT column is never written. ``llm_web`` has the model search the web and
checks the page it cites (``vat/lookup_web.py``). ``llm_only`` asks the model
from memory: an **experiment**, not for production use
(``vat/lookup_llm_only.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


async def run(input_path: Path, out_path: Path, client, lookup=None,
              vies_queue: bool = True, agent=None, scope: str = "missing",
              log_columns: bool = False) -> dict[str, Any]:
    """Validate *input_path* into *out_path*. Returns the summary. With
    *vies_queue*, the VIES queue is drained before the lookups and after
    them, and its answers are written into the output."""
    from openpyxl import load_workbook

    from vat.vies_retry import apply_to_workbook, drain, summary_line
    from vat.xlsx import validate_workbook

    if vies_queue:
        _, asked = await drain(client)
        if asked:
            print(f"VIES queue at start-up: {summary_line(asked)}")
    wb = load_workbook(input_path)
    try:
        summary = await validate_workbook(wb, client, source=str(input_path), lookup=lookup, agent=agent,
                                          scope=scope, log_columns=log_columns)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        if lookup is not None:
            await lookup.aclose()
        if agent is not None:
            await agent.aclose()
    if vies_queue and lookup is not None:
        queue, asked = await drain(client)
        changed = apply_to_workbook(wb, queue.answers())
        print(f"VIES queue at the end of the run: {summary_line(asked)}; {changed} row(s) updated")
    wb.save(out_path)
    return summary


def _ledger(args, lookup, input_path: Path, out_path: Path, started: datetime) -> None:
    """The run's cost lines on the log, one row in the cost ledger, and the
    ledger's total to date."""
    from vat import costs

    log = [*lookup.usage_log, *(getattr(args, "_agent_log", None) or [])]
    run = costs.run_costs(log, lookup.meter.table, lookup.meter.overrides)
    print("  cost: " + " ".join(f"{k}={run[k]:.4f}" for k in costs.LINES)
          + f" web_search_calls={run['web_search_calls']} cache_hits={run['cache_hits']}/{run['calls']}"
          + f" price_table={run['price_table_hash']}")
    for warning in costs.helper_reasoning_warnings(
            costs.raw_counts(c) for u in log for c in u.get("calls") or []):
        print(f"  WARNING helper call not at reasoning minimal: {warning}")
    row = costs.ledger_row(
        run, run_id=f"{started:%Y%m%dT%H%M%S}_{out_path.stem}", timestamp=started.isoformat(timespec="seconds"),
        step=args.step or out_path.stem, input_file=input_path.name,
        engine=f"{lookup.mode}/{getattr(lookup, 'query_mode', '')}".rstrip("/"))
    ledger = Path(args.ledger) if args.ledger else costs.LEDGER_FILE
    costs.append_ledger(row, ledger)
    print(f"  ledger {ledger}: {costs.ledger_total(ledger):.4f} USD spent to date")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="SAP export XLSX (sheet 'Data').")
    ap.add_argument("--out", help="Output XLSX (default: <input>_vat.xlsx next to the input).")
    ap.add_argument("--frozen", action="store_true",
                    help="Force CACHE_FROZEN=true — replay recorded VIES answers only.")
    ap.add_argument("--cache-dir", default=None,
                    help="Override EVIDENCE_CACHE_DIR (VIES answers go in <dir>/vies/).")
    ap.add_argument("--lookup-missing", choices=("none", "grounded", "llm_web", "llm_only"), default="llm_web",
                    help="Propose VAT IDs for rows with no VAT ID and key DE (llm_web: web "
                         "search; llm_only: from memory, an experiment, not for production use).")
    ap.add_argument("--usage-out", default=None,
                    help="llm_web: write each entity's tool calls, page fetches, tokens, seconds "
                         "and cost (from the API usage fields) to this JSON file.")
    ap.add_argument("--step", default=os.getenv("VAT_RUN_STEP", ""),
                    help="Label of this run in the cost ledger (default VAT_RUN_STEP).")
    ap.add_argument("--scope", choices=("missing", "all"), default=None,
                    help="missing: rows with no VAT ID; all: every DE row, VAT ID or not, compared "
                         "with SAP (default: all with VAT_LOOKUP_ALL_ROWS, else missing).")
    ap.add_argument("--agent-pass", action="store_true", default=None,
                    help="Step AG: the agentic pass on the rows the lookup left without a number "
                         "(default VAT_AGENT_PASS; stop limit VAT_AGENT_BUDGET_USD).")
    ap.add_argument("--no-vies-queue", action="store_true",
                    help="Do not ask VIES for the queued numbers (rows stay PENDING).")
    ap.add_argument("--ledger", default=None,
                    help="Cost ledger CSV (default logs/vat_cost_ledger.csv); one row per run.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Must be set before Settings is constructed.
    if args.frozen:
        os.environ["CACHE_FROZEN"] = "true"
    if args.cache_dir is not None:
        os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir

    from config import get_settings  # noqa: E402
    from vat.lookup_engines import make_lookup  # noqa: E402
    from vat.vies_client import VIESClient  # noqa: E402

    input_path = Path(args.input).resolve()
    out_path = Path(args.out).resolve() if args.out else input_path.with_name(
        f"{input_path.stem}_vat{input_path.suffix}",
    )
    if out_path == input_path:
        raise SystemExit("--out must differ from the input file; the input is never modified.")

    settings = get_settings()
    client = VIESClient(settings)
    lookup = make_lookup(args.lookup_missing, settings, client)
    agent = None
    if lookup is not None and args.lookup_missing == "llm_web" and (
            settings.vat_agent_pass if args.agent_pass is None else args.agent_pass):
        from vat.lookup_agent import make_agent
        agent = make_agent(settings, client)
    started = datetime.now(timezone.utc).astimezone()
    from vat.lookup import default_scope
    scope = args.scope or default_scope(settings)
    summary = asyncio.run(run(input_path, out_path, client, lookup, vies_queue=not args.no_vies_queue,
                              agent=agent, scope=scope, log_columns=settings.vat_log_columns))
    args._agent_log = agent.usage_log if agent is not None else []
    print(f"Wrote {out_path}")
    usage_log = getattr(lookup, "usage_log", None)
    if usage_log is not None and agent is not None:
        usage_log = [*usage_log, *({**u, "pass": "agent"} for u in agent.usage_log)]
    if args.usage_out and usage_log is not None:
        import json
        Path(args.usage_out).write_text(json.dumps(usage_log, indent=1, ensure_ascii=False),
                                        encoding="utf-8")
        print(f"Wrote {args.usage_out} ({len(usage_log)} entities, "
              f"new spend {lookup.meter.spent:.4f} USD)")
    if usage_log is not None:
        _ledger(args, lookup, input_path, out_path, started)
    print(f"  rows={summary['rows']} unique_customers={summary['unique_customers']} "
          f"vies_lookups={summary['unique_vies_lookups']} "
          f"conflicts={summary['customers_with_vat_conflict']}")
    print(f"  format: {summary['format_status']}")
    if "lookup" in summary:
        print(f"  lookup ({summary['lookup']['mode']}): {summary['lookup']['status']}")
        if "agent_pass" in summary["lookup"]:
            print(f"  agent pass: {summary['lookup']['agent_pass']}")
    print(f"  vies:   {summary['vies_status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
