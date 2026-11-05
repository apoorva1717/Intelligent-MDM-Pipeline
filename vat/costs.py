"""What the VAT lookups cost: per API call, per entity, per run, and to date.

Prices come from ``config/vat_prices.yaml`` (:class:`PriceTable`), loaded at
the start of a run; its version and hash go into every entity's ``costs`` in
the usage JSON and into the run's ledger row. Per call::

    tokens = ((input - cached) * input_per_1m + cached * cached_input_per_1m
              + output * output_per_1m) / 1e6 * multiplier
    search = web_search_calls * web_search_per_call
    opens  = page_open_calls * page_open_per_call      (model mode only)
    serp   = serpapi_queries * serpapi_per_query

Reasoning tokens are part of the output tokens; cache writes are uncached
input. The lines are kept apart, never merged: :data:`LINES`. A call replayed
from the cache (``cache_hit``) still reports what it cost when recorded in
``Total_USD`` but adds nothing to ``New_Spend_USD``, what the run billed.

The ledger (``logs/vat_cost_ledger.csv``) gets one row per run
(:func:`append_ledger`); ``tools/vat_cost_summary.py`` sums it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

PRICES_FILE = Path(__file__).resolve().parent.parent / "config" / "vat_prices.yaml"
LEDGER_FILE = Path(__file__).resolve().parent.parent / "logs" / "vat_cost_ledger.csv"
#: The deployment of every VAT run so far: the one a usage record without a
#: ``deployment`` field was made with.
DEFAULT_DEPLOYMENT = "MDM_gpt-6-sol"

TOKENS = "Azure_LLM_Tokens_USD"
WEB_SEARCH = "Azure_WebSearch_USD"
PAGE_OPEN = "Azure_PageOpen_USD"
SERPAPI = "SerpAPI_USD"
OTHER = "Other_USD"
TOTAL = "Total_USD"
NEW_SPEND = "New_Spend_USD"
LINES = (TOKENS, WEB_SEARCH, PAGE_OPEN, SERPAPI, OTHER, TOTAL, NEW_SPEND)
#: The lines that add up to the total; each is also kept as billed only
#: (``New_<line>``: replays left out), which is what the ledger sums.
BILLED_LINES = (TOKENS, WEB_SEARCH, PAGE_OPEN, SERPAPI, OTHER)

SEARCH, READER, RANKING, NAME2, GROUNDED_READER, AGENT = (
    "search", "reader", "ranking", "name2_classifier", "grounded_reader", "agent")
CALL_KINDS = (SEARCH, READER, RANKING, NAME2, GROUNDED_READER, AGENT)
#: Helper calls on the small model, run with reasoning minimal.
HELPER_KINDS = (RANKING, NAME2)
#: Reasoning tokens a helper call may report and still count as "minimal".
HELPER_REASONING_MAX = 64

TOKEN_FIELDS = ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")
COUNT_FIELDS = ("web_search_calls", "page_open_calls", "serpapi_queries")


class PriceTableError(ValueError):
    """The price table is missing, malformed, or has no price for a deployment."""


@dataclass(frozen=True)
class DeploymentPrice:
    input_per_1m: float
    cached_input_per_1m: float
    output_per_1m: float
    deployment_type: str = "global"
    multiplier: float = 1.0


ZERO_PRICE = DeploymentPrice(0.0, 0.0, 0.0, "none", 1.0)


@dataclass(frozen=True)
class PriceTable:
    deployments: dict[str, DeploymentPrice]
    web_search_per_call: float
    page_open_per_call: float
    serpapi_per_query: float
    free: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    hash: str = ""
    path: str = ""

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PriceTable":
        import yaml

        p = Path(path or PRICES_FILE)
        try:
            raw = p.read_bytes()
        except OSError as e:
            raise PriceTableError(f"price table {p} not readable: {e}") from e
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as e:
            raise PriceTableError(f"price table {p} is not valid YAML: {e}") from e
        return cls.from_dict(data, digest=hashlib.sha256(raw).hexdigest()[:12], path=str(p))

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, digest: str = "", path: str = "") -> "PriceTable":
        def number(where: str, value: Any) -> float:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise PriceTableError(f"price table {path or '(dict)'}: {where} must be a number >= 0, not {value!r}")
            return float(value)

        deps = data.get("deployments")
        if not isinstance(deps, dict) or not deps:
            raise PriceTableError(f"price table {path or '(dict)'}: no deployments")
        prices = {}
        for name, d in deps.items():
            if not isinstance(d, dict):
                raise PriceTableError(f"price table {path or '(dict)'}: deployment {name!r} is not a mapping")
            prices[str(name)] = DeploymentPrice(
                *(number(f"{name}.{k}", d.get(k)) for k in ("input_per_1m", "cached_input_per_1m", "output_per_1m")),
                deployment_type=str(d.get("deployment_type") or "global"),
                multiplier=number(f"{name}.multiplier", d.get("multiplier", 1.0)))
        tools = data.get("tools")
        if not isinstance(tools, dict):
            raise PriceTableError(f"price table {path or '(dict)'}: no tools section")
        if not digest:
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]
        return cls(
            deployments=prices,
            web_search_per_call=number("tools.web_search_per_call", tools.get("web_search_per_call")),
            page_open_per_call=number("tools.page_open_per_call", tools.get("page_open_per_call")),
            serpapi_per_query=number("tools.serpapi_per_query", tools.get("serpapi_per_query")),
            free=tuple(data.get("free") or ()), meta=dict(data.get("meta") or {}),
            version=str(data.get("version", "")), hash=digest, path=path,
        )

    def deployment(self, name: str | None) -> DeploymentPrice:
        name = name or DEFAULT_DEPLOYMENT
        try:
            return self.deployments[name]
        except KeyError:
            raise PriceTableError(
                f"no price for deployment {name!r} in {self.path or 'the price table'} "
                f"(has: {', '.join(self.deployments)})") from None

    @property
    def stamp(self) -> dict[str, str]:
        return {"price_table_version": self.version, "price_table_hash": self.hash}


def call_kind(label: str | None) -> str:
    """The kind of a call from its label in the usage JSON (``search_1``,
    ``read_2``, ...); a call with no label is the model-mode agent."""
    label = (label or "").lower()
    for prefix, kind in (("search", SEARCH), ("read", READER), ("rank", RANKING), ("name2", NAME2),
                         ("grounded", GROUNDED_READER)):
        if label.startswith(prefix):
            return kind
    return AGENT


def raw_counts(call: dict[str, Any], *, deployment: str | None = None, label: str | None = None,
               cache_hit: bool | None = None) -> dict[str, Any]:
    """The raw counts of one API response, in the usage JSON's names. Older
    records name the search count ``search_calls`` and page opens
    ``other_tool_calls``; both are read."""
    hit = bool(call.get("cache_hit", call.get("from_cache", False))) if cache_hit is None else cache_hit
    return {
        "deployment": call.get("deployment") or deployment or DEFAULT_DEPLOYMENT,
        "call_kind": call.get("call_kind") or call_kind(label or call.get("call")),
        **{k: int(call.get(k) or 0) for k in TOKEN_FIELDS},
        "web_search_calls": int(call.get("web_search_calls", call.get("search_calls")) or 0),
        "page_open_calls": int(call.get("page_open_calls", call.get("other_tool_calls")) or 0),
        "serpapi_queries": int(call.get("serpapi_queries") or 0),
        "http_status": call.get("http_status"),
        "seconds": call.get("seconds"),
        "cache_hit": hit,
    }


def call_lines(call: dict[str, Any], table: PriceTable, price: DeploymentPrice | None = None) -> dict[str, float]:
    """The cost lines of one call (:func:`raw_counts` fields)."""
    p = price or table.deployment(call.get("deployment"))
    i, c, o = (int(call.get(k) or 0) for k in ("input_tokens", "cached_tokens", "output_tokens"))
    tokens = (max(0, i - c) * p.input_per_1m + c * p.cached_input_per_1m + o * p.output_per_1m) / 1e6 * p.multiplier
    lines = {
        TOKENS: tokens,
        WEB_SEARCH: int(call.get("web_search_calls") or 0) * table.web_search_per_call,
        PAGE_OPEN: int(call.get("page_open_calls") or 0) * table.page_open_per_call,
        SERPAPI: int(call.get("serpapi_queries") or 0) * table.serpapi_per_query,
        OTHER: 0.0,
    }
    lines[TOTAL] = sum(lines.values())
    lines[NEW_SPEND] = 0.0 if call.get("cache_hit") else lines[TOTAL]
    return lines


def _round(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 6) for k, v in d.items()}


def sum_lines(calls: Iterable[dict[str, Any]], table: PriceTable,
              overrides: dict[str, DeploymentPrice] | None = None) -> dict[str, Any]:
    """The lines summed over *calls* (:func:`raw_counts` fields), with the
    token line split by call kind and by deployment, and the counts.
    *overrides*: a deployment's price instead of the table's (a meter's own,
    zero under MOCK_EXTERNAL_CALLS)."""
    lines = dict.fromkeys(LINES, 0.0)
    new = dict.fromkeys(BILLED_LINES, 0.0)
    by_kind: dict[str, float] = {}
    by_dep: dict[str, float] = {}
    new_by_dep: dict[str, float] = {}
    calls_by_kind: dict[str, int] = {}
    calls_by_dep: dict[str, int] = {}
    counts = dict.fromkeys((*TOKEN_FIELDS, *COUNT_FIELDS), 0)
    billed = dict.fromkeys((*TOKEN_FIELDS, *COUNT_FIELDS), 0)
    hits = n = 0
    for call in calls:
        n += 1
        one = call_lines(call, table, (overrides or {}).get(call["deployment"]))
        for k in LINES:
            lines[k] += one[k]
        kind, dep = call["call_kind"], call["deployment"]
        by_kind[kind] = by_kind.get(kind, 0.0) + one[TOKENS]
        by_dep[dep] = by_dep.get(dep, 0.0) + one[TOKENS]
        calls_by_kind[kind] = calls_by_kind.get(kind, 0) + 1
        calls_by_dep[dep] = calls_by_dep.get(dep, 0) + 1
        for k in counts:
            counts[k] += int(call.get(k) or 0)
        if call.get("cache_hit"):
            hits += 1
            continue
        # What was billed, line by line: replays are left out.
        for line in BILLED_LINES:
            new[line] += one[line]
        new_by_dep[dep] = new_by_dep.get(dep, 0.0) + one[TOKENS]
        for k in billed:
            billed[k] += int(call.get(k) or 0)
    return {**_round(lines), "tokens_by_kind": _round(by_kind), "tokens_by_deployment": _round(by_dep),
            "calls": n, "cache_hits": hits, "calls_by_kind": calls_by_kind, "calls_by_deployment": calls_by_dep,
            **counts, **{f"New_{k}": v for k, v in _round(new).items()},
            "new_tokens_by_deployment": _round(new_by_dep), **{f"billed_{k}": v for k, v in billed.items()}}


def entity_costs(usage: dict[str, Any], table: PriceTable,
                 overrides: dict[str, DeploymentPrice] | None = None) -> dict[str, Any]:
    """An entity's lines from its ``calls``, stamped with the price table."""
    calls = [raw_counts(c) for c in usage.get("calls") or []]
    return {**sum_lines(calls, table, overrides), **table.stamp}


def run_costs(usage_log: Iterable[dict[str, Any]], table: PriceTable,
              overrides: dict[str, DeploymentPrice] | None = None) -> dict[str, Any]:
    """A run's lines: every call of every entity."""
    log = list(usage_log)
    calls = [raw_counts(c) for u in log for c in u.get("calls") or []]
    return {"entities": len(log), **sum_lines(calls, table, overrides), **table.stamp}


#: Per-entity cost columns of the output workbook, one per line.
COST_COLUMNS = {line: f"VAT_Found_{line}" for line in LINES}


def cost_columns(costs: dict[str, Any] | None) -> dict[str, Any]:
    """An entity's :data:`COST_COLUMNS` cells (blank for a row not looked up)."""
    return {col: (costs or {}).get(line) if costs else None for line, col in COST_COLUMNS.items()}


def run_stamp(costs: dict[str, Any]) -> dict[str, str]:
    """The price table a :func:`run_costs` result was priced with."""
    return {k: costs.get(k, "") for k in ("price_table_version", "price_table_hash")}


def helper_reasoning_warnings(calls: Iterable[dict[str, Any]]) -> list[str]:
    """Helper calls (ranking, name2) that did not run with reasoning minimal."""
    return [f"{c['call_kind']} call reported {c['reasoning_tokens']} reasoning tokens"
            for c in calls if c.get("call_kind") in HELPER_KINDS
            and int(c.get("reasoning_tokens") or 0) > HELPER_REASONING_MAX]


# ── The ledger ──────────────────────────────────────────────────────────────

LEDGER_FIELDS = [
    "run_id", "timestamp", "step", "input_file", "engine", "entities", "api_calls", "cache_hits",
    *(f"calls_{k}" for k in CALL_KINDS), "calls_by_deployment",
    *TOKEN_FIELDS, *COUNT_FIELDS,
    TOKENS, *(f"Tokens_{k}_USD" for k in CALL_KINDS), "tokens_by_deployment",
    WEB_SEARCH, PAGE_OPEN, SERPAPI, OTHER, TOTAL, NEW_SPEND,
    *(f"New_{k}" for k in BILLED_LINES), "new_tokens_by_deployment",
    *(f"billed_{k}" for k in (*TOKEN_FIELDS, *COUNT_FIELDS)),
    "price_table_version", "price_table_hash", "backfilled", "estimated", "note",
]


def ledger_row(costs: dict[str, Any], *, run_id: str, timestamp: str, step: str, input_file: str,
               engine: str, backfilled: bool = False, estimated: bool = False, note: str = "") -> dict[str, Any]:
    """One ledger row from :func:`run_costs`."""
    pair = lambda d: ";".join(f"{k}={v}" for k, v in sorted(d.items()))  # noqa: E731
    row = {
        "run_id": run_id, "timestamp": timestamp, "step": step, "input_file": input_file, "engine": engine,
        "entities": costs.get("entities", 0), "api_calls": costs.get("calls", 0),
        "cache_hits": costs.get("cache_hits", 0),
        **{f"calls_{k}": costs.get("calls_by_kind", {}).get(k, 0) for k in CALL_KINDS},
        "calls_by_deployment": pair(costs.get("calls_by_deployment", {})),
        **{k: costs.get(k, 0) for k in (*TOKEN_FIELDS, *COUNT_FIELDS)},
        TOKENS: costs.get(TOKENS, 0.0),
        **{f"Tokens_{k}_USD": costs.get("tokens_by_kind", {}).get(k, 0.0) for k in CALL_KINDS},
        "tokens_by_deployment": pair(costs.get("tokens_by_deployment", {})),
        **{k: costs.get(k, 0.0) for k in (WEB_SEARCH, PAGE_OPEN, SERPAPI, OTHER, TOTAL, NEW_SPEND)},
        **{f"New_{k}": costs.get(f"New_{k}", 0.0) for k in BILLED_LINES},
        "new_tokens_by_deployment": pair(costs.get("new_tokens_by_deployment", {})),
        **{f"billed_{k}": costs.get(f"billed_{k}", 0) for k in (*TOKEN_FIELDS, *COUNT_FIELDS)},
        "price_table_version": costs.get("price_table_version", ""),
        "price_table_hash": costs.get("price_table_hash", ""),
        "backfilled": "yes" if backfilled else "no", "estimated": "yes" if estimated else "no", "note": note,
    }
    return row


def read_ledger(path: str | Path | None = None) -> list[dict[str, str]]:
    p = Path(path or LEDGER_FILE)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_ledger(row: dict[str, Any], path: str | Path | None = None) -> bool:
    """Append *row*; False (nothing written) when its run_id is already
    there, so a backfill run twice adds nothing. A price table whose hash
    differs from the last row's gets a note."""
    p = Path(path or LEDGER_FILE)
    rows = read_ledger(p)
    if any(r["run_id"] == row["run_id"] for r in rows):
        return False
    if rows and rows[-1].get("price_table_hash") and row.get("price_table_hash") \
            and rows[-1]["price_table_hash"] != row["price_table_hash"]:
        change = f"price table changed {rows[-1]['price_table_hash']} -> {row['price_table_hash']}"
        row = {**row, "note": f"{row.get('note')}; {change}" if row.get("note") else change}
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
    return True


def ledger_total(path: str | Path | None = None) -> float:
    """What every run in the ledger billed (sum of New_Spend_USD)."""
    return round(sum(float(r.get(NEW_SPEND) or 0) for r in read_ledger(path)), 4)
