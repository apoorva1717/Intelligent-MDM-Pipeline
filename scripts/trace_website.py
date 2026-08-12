"""Standalone diagnostic for Path B / Path C website resolution.

Exercises ONLY the website resolver (no full pipeline, no enrichment tiers, no
ROR — Path A is skipped entirely). For each name it runs Path B (SERP) exactly
as the orchestrator would, and if Path B yields nothing it runs Path C (LLM),
mirroring ``Orchestrator._maybe_resolve_website_bc``.

  * Forces ``WEBSITE_TRACE=true`` for the run.
  * Uses a FRESH, unshared ``BatchCache`` per record, so every query hits the
    wire (no stale SERP cache can hide the live result set).
  * Prints a readable per-candidate table to stdout.
  * Writes the raw JSON trace lines to ``logs/website_trace.json``.

Usage:
    python scripts/trace_website.py                 # 3 failing + 3 control records
    python scripts/trace_website.py --record "Verdox, Inc." --country US
    python scripts/trace_website.py --record "Foo Corp" --country US --type company
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Force the trace flag ON before Settings is constructed, and put the repo root
# on the path so `config`, `enrichment`, etc. import cleanly.
os.environ["WEBSITE_TRACE"] = "true"
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from config import Settings  # noqa: E402
from enrichment.website_resolver import (  # noqa: E402
    infer_website_via_llm,
    resolve_website_via_serp,
)
from llm.openai_client import OpenAIClient  # noqa: E402
from utils.cache import BatchCache  # noqa: E402


# The three records that resolve to an empty website/domain but should not.
FAILING: list[tuple[str, str]] = [
    ("Atlantic Testing Labs", "US"),
    ("Fine Organics Limited", "GB"),
    ("Verdox, Inc.", "US"),
]
# Controls that currently succeed via Path B — must be unchanged.
CONTROLS: list[tuple[str, str]] = [
    ("Silverline Biotech", "US"),
    ("Belharra Therapeutics, Inc.", "US"),
    ("Suncoast Medical", "US"),
]


class _CaptureHandler(logging.Handler):
    """Collects each emitted JSON trace line (as a parsed dict) and mirrors it
    verbatim to the log file."""

    def __init__(self, file_path: Path) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[dict] = []
        self._fh = file_path.open("w", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        line = record.getMessage()
        self._fh.write(line + "\n")
        self._fh.flush()
        try:
            self.records.append(json.loads(line))
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._fh.close()
        finally:
            super().close()


def _build_search_client(settings: Settings):
    if settings.serpapi_key.strip():
        from search.serpapi_client import SerpAPIClient
        return SerpAPIClient(settings.serpapi_key)
    from search.duckduckgo_client import DuckDuckGoClient
    return DuckDuckGoClient()


async def _resolve_one(
    name1: str, country: str, record_type: str,
    settings: Settings, search_client, llm_client,
) -> None:
    """Run Path B then (if needed) Path C for a single name, with a fresh cache."""
    cache = BatchCache()  # unshared → every query hits the wire
    rid = name1
    serp_res = await resolve_website_via_serp(
        record_id=rid, name1=name1, city=None, state=None, country=country,
        record_type=record_type, search_client=search_client, cache=cache,
        trace=settings.website_trace,
    )
    if serp_res.url:
        return
    await infer_website_via_llm(
        record_id=rid, name1=name1, city=None, state=None, country=country,
        llm_client=llm_client, trace=settings.website_trace,
    )


def _print_table(records: list[dict]) -> None:
    by_b = [r for r in records if r.get("phase") == "path_b"]
    by_c = {r["record_id"]: r for r in records if r.get("phase") == "path_c"}

    for b in by_b:
        rid = b["record_id"]
        print("\n" + "=" * 100)
        print(f"{b['name1']}  (record_type={b['record_type']})")
        print(f"  query           : {b['query']}")
        print(f"  num_results     : {b['num_results']}   results_returned: {b['results_returned']}")
        if b.get("error"):
            print(f"  ERROR           : {b['error']}")
        print("  " + "-" * 96)
        print(f"  {'#':<2} {'rejected_by':<12} {'rank':<4} {'chosen':<6} "
              f"{'matched(token@where)':<26} host")
        print("  " + "-" * 96)
        for c in b["candidates"]:
            match = ""
            if c["matched_token"]:
                match = f"{c['matched_token']}@{c['matched_in']}"
            if c.get("foreign_label"):
                match += f"  foreign:{c['foreign_label']}"
            rej = c["rejected_by"] or ("CHOSEN" if c["chosen"] else "-")
            rank = "" if c["rank"] is None else str(c["rank"])
            chosen = "yes" if c["chosen"] else ""
            print(f"  {c['position']:<2} {rej:<12} {rank:<4} {chosen:<6} "
                  f"{match:<26} {c['host']}")
            print(f"       url: {c['url']}")
        print("  " + "-" * 96)
        print(f"  OUTCOME: chosen_url={b['chosen_url']!r} confidence={b['confidence']!r} "
              f"flagged={b['flagged']} fell_through_to_path_c={b['fell_through_to_path_c']}")
        c = by_c.get(rid)
        if c:
            print(f"  PATH C : raw_response={c.get('raw_response')!r} "
                  f"sentinel={c.get('treated_as_sentinel')} "
                  f"url_shape_ok={c.get('url_shape_ok')} "
                  f"final_value={c.get('final_value')!r}"
                  + (f" llm_error={c.get('llm_error')!r}" if c.get('llm_error') else ""))


async def _main() -> None:
    ap = argparse.ArgumentParser(description="Trace website resolution (diagnostic only).")
    ap.add_argument("--record", help="Run a single ad-hoc name instead of the default set.")
    ap.add_argument("--country", default="US", help="Country code for --record (default US).")
    ap.add_argument("--type", default="company", dest="record_type",
                    help="record_type for --record (default company).")
    args = ap.parse_args()

    settings = Settings()
    if not settings.website_trace:  # defensive — we forced it on above
        raise SystemExit("WEBSITE_TRACE did not take effect; aborting.")

    logs_dir = _ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    out_file = logs_dir / "website_trace.json"

    handler = _CaptureHandler(out_file)
    handler.setFormatter(logging.Formatter("%(message)s"))
    trace_log = logging.getLogger("enrichment.trace.website")
    trace_log.setLevel(logging.INFO)
    trace_log.addHandler(handler)
    trace_log.propagate = False  # keep the raw JSON out of the root/app logs

    search_client = _build_search_client(settings)
    llm_client = OpenAIClient(settings)

    if args.record:
        work = [(args.record, args.country, args.record_type)]
    else:
        work = [(n, c, "company") for n, c in FAILING + CONTROLS]

    try:
        for name1, country, rtype in work:
            await _resolve_one(name1, country, rtype, settings,
                               search_client, llm_client)
    finally:
        aclose = getattr(llm_client, "aclose", None)
        if aclose:
            try:
                await aclose()
            except Exception:
                pass
        handler.close()
        trace_log.removeHandler(handler)

    _print_table(handler.records)
    print(f"\nRaw JSON trace written to: {out_file}")


if __name__ == "__main__":
    asyncio.run(_main())
