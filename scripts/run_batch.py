"""Run an XLSX batch through the enrichment pipeline, offline.

Does exactly what ``POST /enrich/file`` does — the same ``_parse_xlsx`` /
``_rows_to_records`` / ``_build_output_xlsx`` helpers, the same orchestrator —
without standing up the API, so a thesis batch can be re-run and diffed from
the command line.

Three artefacts per run:

* ``--out`` — the enriched workbook (identical schema to ``/enrich/file``).
* ``--json`` — every ``EnrichmentResult`` plus the batch summary, so the
  reports can be rebuilt from a completed run without paying for it twice.
* ``--trace-out`` — the raw JSON lines from the diagnostic trace loggers
  (``enrichment.trace.retry``, ``enrichment.trace.website``), one per line.

Usage::

    python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
        --out docs/thesis/chemspeed_us_100_enriched.xlsx \
        --json logs/chemspeed_run.json \
        --retry-trace --trace-out logs/retry_trace.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class _CaptureHandler(logging.Handler):
    """Mirror every emitted trace line to a file (and keep it in memory)."""

    def __init__(self, file_path: Path) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = file_path.open("w", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        line = record.getMessage()
        self.lines.append(line)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        finally:
            super().close()


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Input XLSX (SAP columns).")
    ap.add_argument("--out", required=True, help="Output enriched XLSX.")
    ap.add_argument("--json", dest="json_out", help="Dump results + summary as JSON.")
    ap.add_argument("--trace-out", default="logs/trace.jsonl",
                    help="Where the diagnostic trace lines are written.")
    ap.add_argument("--retry-trace", action="store_true",
                    help="Force RETRY_TRACE=true for this run.")
    ap.add_argument("--website-trace", action="store_true",
                    help="Force WEBSITE_TRACE=true for this run.")
    ap.add_argument("--wikidata-trace", action="store_true",
                    help="Force WIKIDATA_TRACE=true for this run.")
    ap.add_argument("--no-wikidata", action="store_true",
                    help="Force WIKIDATA_ENABLED=false (the A/B baseline).")
    ap.add_argument("--frozen", action="store_true",
                    help="Force CACHE_FROZEN=true — a cache miss is recorded "
                         "as evidence-unavailable-frozen instead of going to "
                         "the network. The evaluation freeze switch.")
    ap.add_argument("--cache-dir", default=None,
                    help="Override EVIDENCE_CACHE_DIR for this run.")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None,
                    help="Only enrich the first N rows (smoke runs).")
    args = ap.parse_args()

    # Trace flags must be set before Settings is constructed anywhere.
    if args.retry_trace:
        os.environ["RETRY_TRACE"] = "true"
    if args.website_trace:
        os.environ["WEBSITE_TRACE"] = "true"
    if args.wikidata_trace:
        os.environ["WIKIDATA_TRACE"] = "true"
    if args.no_wikidata:
        os.environ["WIKIDATA_ENABLED"] = "false"
    if args.frozen:
        os.environ["CACHE_FROZEN"] = "true"
    if args.cache_dir:
        os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir

    from api.models import EnrichmentOptions  # noqa: E402
    from api.routes import (  # noqa: E402
        _build_output_xlsx,
        _parse_xlsx,
        _rows_to_records,
    )
    from config import Settings  # noqa: E402
    from enrichment.orchestrator import Orchestrator  # noqa: E402

    settings = Settings()

    handler = _CaptureHandler(_ROOT / args.trace_out)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for name in (
        "enrichment.trace.retry",
        "enrichment.trace.website",
        "enrichment.trace.page",
        "enrichment.trace.wikidata",
        # Fix B — `evidence-unavailable-frozen`, one line per record that a
        # CACHE_FROZEN run left short of a piece of evidence.
        "enrichment.trace.cache",
        # Fix D — `source-conflict` / `registry-location-mismatch`, one line
        # per record the cross-source gate acted on.
        "enrichment.trace.consistency",
    ):
        trace_log = logging.getLogger(name)
        trace_log.setLevel(logging.INFO)
        trace_log.addHandler(handler)
        trace_log.propagate = False  # keep raw JSON out of the app log

    contents = Path(args.input).read_bytes()
    headers, row_dicts = _parse_xlsx(contents)
    if args.limit:
        row_dicts = row_dicts[: args.limit]
    records = _rows_to_records(row_dicts)
    print(f"{len(records)} records parsed from {args.input}")

    orchestrator = Orchestrator(settings)
    started = time.time()
    try:
        response = await orchestrator.enrich_batch(
            records, EnrichmentOptions(max_concurrency=args.concurrency),
        )
    finally:
        handler.close()

    elapsed = time.time() - started
    print(f"batch done in {elapsed:.1f}s")

    out_path = _ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(
        _build_output_xlsx(response.results, headers, row_dicts, source_contents=contents)
    )
    print(f"workbook  -> {out_path}")

    if args.json_out:
        json_path = (
            _ROOT / args.json_out
            if not Path(args.json_out).is_absolute()
            else Path(args.json_out)
        )
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                {
                    "summary": response.summary.model_dump(),
                    # The INPUT side of every row, in input order. The
                    # `*_original` fields are `exclude=True` on
                    # `EnrichmentResult` (they are working state, not output),
                    # so without this the artefact carries no input-side name
                    # at all — and `tools/run_diff.py` needs one, because the
                    # only alternative is joining two runs on a column the
                    # pipeline itself writes. Parallel to `results` by
                    # construction: `enrich_batch` returns results in input
                    # order.
                    "inputs": [
                        {
                            "record_id": r.record_id,
                            "name1": r.name1,
                            "city": r.city,
                        }
                        for r in records
                    ],
                    "results": [
                        r.model_dump(by_alias=False) for r in response.results
                    ],
                },
                indent=1, default=str,
            ),
            encoding="utf-8",
        )
        print(f"json      -> {json_path}")

    print(f"trace     -> {_ROOT / args.trace_out} ({len(handler.lines)} lines)")
    print(json.dumps(response.summary.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_main())
