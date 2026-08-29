"""Run a handful of records and see what actually happened (ticket 21, C).

The stated need: *run a set of examples and enrich them and see where things
are going wrong.* ``run_batch.py`` answers "what did the pipeline produce";
this answers "what did the pipeline **do**" — every external request, the
verbatim string that went out, which provider answered, cache or network, and
an outcome that distinguishes *the provider failed* from *it found nothing*
from *it found things and none was right*.

    python scripts/explain.py docs/thesis/chemspeed_us_100.xlsx --limit 5

Reuses ``_parse_xlsx`` / ``_rows_to_records`` from ``api/routes.py``, the same
helpers ``POST /enrich/file`` and ``run_batch.py`` use, so there is no second
idea of what a row is.

It prints the **run manifest** first, unconditionally. All three of the silent
failures of 2026-08-29 — a shadowed ``SERPAPI_KEY``, a poisoned SERP cache, a
provider that was never the one configured — would have been visible on sight
in that block, and none of them was visible anywhere else.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Input XLSX (SAP columns).")
    ap.add_argument("--limit", type=int, default=10,
                    help="Rows to run (default 10). This is the small-batch "
                         "entry point; use run_batch.py for a real batch.")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="Default 1, so the trace reads in record order. "
                         "Raise it only if you are aggregating the JSONL.")
    ap.add_argument("--trace-out", default="logs/explain/call_trace.log")
    ap.add_argument("--trace-json", default="logs/explain/call_trace.jsonl")
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--frozen", action="store_true",
                    help="CACHE_FROZEN: a miss is recorded, never fetched.")
    ap.add_argument("--manifest-only", action="store_true",
                    help="Print the manifest and exit without enriching. Use "
                         "this to check what a run WOULD talk to.")
    return ap.parse_args()


async def _main() -> int:
    args = _parse_args()

    # Set before anything imports the trace module: ENABLED is read once, at
    # import, which is what keeps the disabled path a single boolean test.
    os.environ["CALL_TRACE"] = "true"
    os.environ["CALL_TRACE_OUT"] = args.trace_out
    os.environ["CALL_TRACE_JSON"] = args.trace_json
    if args.frozen:
        os.environ["CACHE_FROZEN"] = "true"
    if args.cache_dir:
        os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir

    from api.models import EnrichmentOptions
    from api.routes import _parse_xlsx, _rows_to_records
    from config import Settings
    from enrichment import call_trace
    from enrichment.orchestrator import Orchestrator

    settings = Settings()
    orchestrator = Orchestrator(settings)

    # The RESOLVED client, not the configuration that was meant to build it.
    search_client = getattr(orchestrator, "_search_client", None)
    print(call_trace.manifest(call_trace.describe_run(settings, search_client)))
    print()
    if args.manifest_only:
        return 0

    contents = Path(args.input).read_bytes()
    _headers, row_dicts = _parse_xlsx(contents)
    row_dicts = row_dicts[: args.limit]
    # `record_id` is a read-only property derived from the Customer column
    # (api/models.py:243), and this workbook leaves it blank -- so every
    # trace line would be attributed to "" and the per-record grouping the
    # trace exists for would be lost. Seed the column where it is empty.
    #
    # Safe HERE and nowhere else: explain.py writes no workbook and no JSON
    # artefact, so a synthetic id cannot escape into data. run_batch.py must
    # never do this.
    for index, row in enumerate(row_dicts, start=1):
        if not str(row.get("Customer") or "").strip():
            row["Customer"] = f"row-{index}"
    records = _rows_to_records(row_dicts)
    print(f"{len(records)} records\n")

    started = time.time()
    response = await orchestrator.enrich_batch(
        records, EnrichmentOptions(max_concurrency=args.concurrency),
    )
    elapsed = time.time() - started

    print(f"== one line per record ==  ({elapsed:.1f}s)")
    for record in response.results:
        name1 = getattr(record, "name1_enriched", "") or ""
        flags = getattr(record, "flag_codes", None) or ()
        print(
            f"  {name1[:44]:44s} "
            f"type={getattr(record, 'record_type', '') or '-':20s} "
            f"ror={'y' if getattr(record, 'ror_id', None) else '-'} "
            f"lei={'y' if getattr(record, 'lei_id', None) else '-'} "
            f"domain={(getattr(record, 'domain', None) or '-')[:28]:28s} "
            f"flags={len(flags)}"
        )

    print(f"\ntrace     -> {args.trace_out}")
    print(f"trace json-> {args.trace_json}")
    stats = getattr(orchestrator, "_evidence_cache", None)
    if stats is not None:
        print(f"network calls: {stats.network_calls}   cache hits: {stats.hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
