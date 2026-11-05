"""Backfill the cost ledger from the runs made before it existed.

    python tools/vat_cost_backfill.py [--logs logs] [--ledger logs/vat_cost_ledger.csv] [--dry-run]

Every ``*usage*.json`` under ``--logs`` (not the caches) is re-priced with the
price table (``vat/costs.py``) and appended with its file time as timestamp
and ``backfilled = yes``. Runs whose usage JSON was not kept are rebuilt from
the answers their LLM cache recorded (each recorded answer is one billed call),
listed in :data:`CACHE_RUNS`. Running it twice adds nothing: a run_id already
in the ledger is skipped.

Older records are read as they are:

* Step X (model mode) has no per-call list: the entity's own usage fields are
  its one call (an ``agent`` call);
* a record without a search count is estimated from its ``searches`` field
  (``estimated = yes``);
* SerpAPI queries (the grounded fallback, Step AA) come from
  ``grounded_serp_network_calls``; the grounded reader's tokens were never
  recorded, which the row's note says.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from vat import costs  # noqa: E402

#: Steps by file name, first match wins.
_STEP_RE = [
    (re.compile(r"_x_codefetch", re.I), "X (code fetch)"),
    (re.compile(r"_x_usage_pass1", re.I), "X (pass 1)"),
    (re.compile(r"_x_usage", re.I), "X"),
    (re.compile(r"^aa_smoke", re.I), "AA smoke"),
    (re.compile(r"AA_53_replay", re.I), "AA replay"),
    (re.compile(r"AA_53", re.I), "AA"),
    (re.compile(r"^ab_smoke", re.I), "AB smoke"),
    (re.compile(r"AB_53", re.I), "AB"),
    (re.compile(r"AC_53", re.I), "AC"),
    (re.compile(r"^ad_smoke", re.I), "AD smoke"),
    (re.compile(r"AD_50", re.I), "AD 50"),
    (re.compile(r"AD_53", re.I), "AD 53 regression"),
]

#: Runs kept only in their LLM cache: (run_id, step, cache dir, first, last
#: file time of the window, all of them replays). AF-1's usage was written by
#: a script without per-call usage, and its re-run (fetches only) replayed all
#: 17 searches; the X smoke test wrote no usage JSON.
CACHE_RUNS = [
    ("backfill:cache/run_x_smoke", "X smoke", "run_x_smoke", None, None, False),
    ("backfill:cache/run_af1#AF-1", "AF-1", "run_af1", None, "2026-11-05T02:31:12", False),
    ("backfill:cache/run_af1#AF-1 rerun", "AF-1 rerun (fetches only)", "run_af1", None, "2026-11-05T02:31:12",
     True),
    ("backfill:cache/run_af1#AF-1b", "AF-1b", "run_af1", "2026-11-05T02:31:12", None, False),
]
#: When those cache-only runs finished (their output files' times).
_CACHE_RUN_TIMES = {
    "backfill:cache/run_af1#AF-1": "2026-11-05T02:31:12",
    "backfill:cache/run_af1#AF-1 rerun": "2026-11-05T02:32:49",
    "backfill:cache/run_af1#AF-1b": "2026-11-05T02:38:17",
}


def step_of(name: str) -> str:
    return next((step for rx, step in _STEP_RE if rx.search(name)), Path(name).stem)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def entity_calls(entity: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """An entity's calls as raw counts, and whether any count is estimated."""
    estimated = False
    calls = []
    for c in entity.get("calls") or []:
        calls.append(costs.raw_counts(c))
    if not entity.get("calls") and any(entity.get(k) for k in ("input_tokens", "output_tokens", "tool_calls")):
        one = dict(entity)
        if "search_calls" not in one:
            one["search_calls"], estimated = int(one.get("searches") or 0), True
        calls.append(costs.raw_counts(one, label="agent"))
    if entity.get("grounded_run") and not any(c["call_kind"] == costs.GROUNDED_READER for c in calls):
        if "grounded_serp_network_calls" in entity:
            serp = int(entity["grounded_serp_network_calls"] or 0)
            queries, hit = serp or int(entity.get("grounded_serp_queries") or 0), not serp
        else:
            # Step AA live logged no SerpAPI counters: one query per grounded run.
            queries, hit, estimated = 1, False, True
        calls.append(costs.raw_counts({"serpapi_queries": queries}, label="grounded", cache_hit=hit))
    return calls, estimated


def json_run(path: Path, table: costs.PriceTable, logs: Path) -> dict[str, Any]:
    log = json.loads(path.read_text(encoding="utf-8"))
    calls, estimated = [], False
    for entity in log:
        one, est = entity_calls(entity)
        calls += one
        estimated |= est
    run = {"entities": len(log), **costs.sum_lines(calls, table), **table.stamp}
    notes = []
    if any(e.get("grounded_run") for e in log):
        notes.append("grounded reader tokens not recorded")
    if not any(e.get("calls") for e in log):
        notes.append("model mode: one agent call per entity from entity usage")
    engine = "llm_web/" + ("model" if not any(e.get("calls") for e in log) else "hybrid")
    rel = path.relative_to(logs.parent).as_posix()
    row = costs.ledger_row(run, run_id=f"backfill:{rel}", timestamp=_iso(path.stat().st_mtime),
                           step=step_of(path.name), input_file=path.name, engine=engine,
                           backfilled=True, estimated=estimated, note="; ".join(notes))
    # Model calls this run billed (not SerpAPI): checked against its cache.
    llm = [c for c in calls if c["call_kind"] != costs.GROUNDED_READER and not c["cache_hit"]]
    row["_llm_billed"] = len(llm)
    row["_llm_new"] = sum(costs.call_lines(c, table)[costs.NEW_SPEND] for c in llm)
    return row


def cache_run(spec, table: costs.PriceTable, logs: Path) -> dict[str, Any] | None:
    run_id, step, folder, first, last, replay = spec
    d = logs / "cache" / folder / "vat_llm"
    files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime) if d.exists() else []
    lo = datetime.fromisoformat(first).timestamp() if first else None
    hi = datetime.fromisoformat(last).timestamp() if last else None
    calls, newest = [], 0.0
    for f in files:
        t = f.stat().st_mtime
        if (lo is not None and t <= lo) or (hi is not None and t > hi):
            continue
        usage = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {}).get("usage")
        if not usage:
            continue
        calls.append(costs.raw_counts(usage, label="search_1", cache_hit=replay))
        newest = max(newest, t)
    if not calls:
        return None
    run = {"entities": len(calls), **costs.sum_lines(calls, table), **table.stamp}
    ts = _CACHE_RUN_TIMES.get(run_id)
    return costs.ledger_row(
        run, run_id=run_id, timestamp=datetime.fromisoformat(ts).astimezone().isoformat(timespec="seconds")
        if ts else _iso(newest), step=step, input_file=f"cache/{folder}", engine="llm_web/hybrid",
        backfilled=True, note="rebuilt from the LLM cache (one recorded answer per billed call)")


#: The LLM cache each step's usage JSONs recorded into (their env files).
CACHE_OF_STEP = {"X (pass 1)": "run_x_live", "X": "run_x_live", "X (code fetch)": "run_x_live",
                 "AA smoke": "run_aa_live", "AA": "run_aa_live", "AA replay": "run_aa_live",
                 "AB smoke": "run_ab_live", "AB": "run_ab_live", "AC": "run_ab_live",
                 "AD 53 regression": "run_ab_live", "AD smoke": "run_ad_live", "AD 50": "run_ad_live"}


def unrecorded_rows(logs: Path, table: costs.PriceTable, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per cache: what its recorded answers cost beyond the billed calls of
    the usage JSONs that wrote into it — runs that kept no usage JSON (the
    first AA smoke test, earlier AB attempts). Each recorded answer is one
    billed call, priced from its own usage; answers recorded without usage
    (the grounded reader's) cannot be priced and are counted in the note."""
    out = []
    for folder in dict.fromkeys(CACHE_OF_STEP.values()):
        mine = [r for r in rows if CACHE_OF_STEP.get(r["step"]) == folder and "_llm_billed" in r]
        billed = sum(r["_llm_billed"] for r in mine)
        spent = sum(r["_llm_new"] for r in mine)
        files = sorted((logs / "cache" / folder / "vat_llm").glob("*.json"), key=lambda f: f.stat().st_mtime)
        calls, unpriced = [], 0
        for f in files:
            usage = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {}).get("usage")
            if usage:
                calls.append(costs.raw_counts(usage, label="search_1", cache_hit=False))
            else:
                unpriced += 1
        recorded = costs.sum_lines(calls, table)
        if unpriced:
            out.append(_reader_estimate(folder, unpriced, table, _iso(files[0].stat().st_mtime)))
        extra_calls, extra_usd = len(calls) - billed, recorded[costs.TOTAL] - spent
        if extra_calls <= 0 or extra_usd <= 0.0005:
            continue
        # The extra spend as one row, lines scaled from the cache's mix.
        scale = extra_usd / recorded[costs.TOTAL]
        scaled = lambda k: k in costs.LINES or k in costs.TOKEN_FIELDS or k in costs.COUNT_FIELDS \
            or k.startswith(("New_", "billed_"))  # noqa: E731
        tokens = round(recorded[costs.TOKENS] * scale, 6)
        run = {"entities": 0, **{k: round(v * scale, 6) for k, v in recorded.items() if scaled(k)},
               "calls": extra_calls, "cache_hits": 0, "calls_by_kind": {costs.SEARCH: extra_calls},
               "calls_by_deployment": {costs.DEFAULT_DEPLOYMENT: extra_calls},
               "tokens_by_kind": {costs.SEARCH: tokens}, "tokens_by_deployment": {costs.DEFAULT_DEPLOYMENT: tokens},
               "new_tokens_by_deployment": {costs.DEFAULT_DEPLOYMENT: tokens}, **table.stamp}
        run[costs.NEW_SPEND] = run[costs.TOTAL] = round(extra_usd, 6)
        out.append(costs.ledger_row(
            run, run_id=f"backfill:cache/{folder}#unrecorded", timestamp=_iso(files[0].stat().st_mtime),
            step=f"unrecorded runs ({folder})", input_file=f"cache/{folder}", engine="llm_web",
            backfilled=True, estimated=True,
            note=f"{extra_calls} recorded answers not in any usage JSON; lines scaled from the cache's mix"))
    return out


#: Tokens assumed per grounded-reader answer, whose client recorded no usage:
#: the page cut to VAT_WEB_PAGE_CHARS (8,000 characters, ~2,300 tokens) plus
#: the prompt in, a short JSON answer out.
GROUNDED_READER_ESTIMATE = {"input_tokens": 2500, "cached_tokens": 0, "output_tokens": 200}


def _reader_estimate(folder: str, answers: int, table: costs.PriceTable, timestamp: str) -> dict[str, Any]:
    """The grounded reader's answers in *folder*'s cache, priced at
    :data:`GROUNDED_READER_ESTIMATE` each (``estimated = yes``)."""
    calls = [costs.raw_counts(GROUNDED_READER_ESTIMATE, label="grounded", cache_hit=False)] * answers
    run = {"entities": 0, **costs.sum_lines(calls, table), **table.stamp}
    one = GROUNDED_READER_ESTIMATE
    return costs.ledger_row(
        run, run_id=f"backfill:cache/{folder}#grounded_reader_estimate", timestamp=timestamp,
        step=f"grounded reader, estimated ({folder})", input_file=f"cache/{folder}", engine="grounded",
        backfilled=True, estimated=True,
        note=f"{answers} grounded-reader answers recorded without usage; {one['input_tokens']} input + "
             f"{one['output_tokens']} output tokens assumed each")


def backfill_rows(logs: Path, table: costs.PriceTable) -> list[dict[str, Any]]:
    rows = [json_run(p, table, logs) for p in sorted(logs.glob("*usage*.json"))]
    rows += [r for r in (cache_run(s, table, logs) for s in CACHE_RUNS) if r]
    rows += unrecorded_rows(logs, table, rows)
    return sorted(rows, key=lambda r: r["timestamp"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default=str(_ROOT / "logs"))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--prices", default=None, help="Price table (default config/vat_prices.yaml).")
    ap.add_argument("--dry-run", action="store_true", help="Print the rows, append nothing.")
    args = ap.parse_args()
    table = costs.PriceTable.load(args.prices)
    ledger = Path(args.ledger) if args.ledger else costs.LEDGER_FILE
    added = 0
    for row in backfill_rows(Path(args.logs), table):
        appended = False if args.dry_run else costs.append_ledger(row, ledger)
        added += appended
        print(f"{'+' if appended else '='} {row['timestamp']} {row['step']:<26} entities {row['entities']:>4} "
              f"calls {row['api_calls']:>4} hits {row['cache_hits']:>4} "
              f"tokens {row[costs.TOKENS]:8.4f} search {row[costs.WEB_SEARCH]:7.4f} "
              f"serp {row[costs.SERPAPI]:6.4f} total {row[costs.TOTAL]:8.4f} new {row[costs.NEW_SPEND]:8.4f}")
    print(f"{added} rows appended to {ledger}; {costs.ledger_total(ledger):.4f} USD spent to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
