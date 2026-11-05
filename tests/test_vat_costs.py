"""Step AE: the VAT cost lines, the price table, the ledger and the backfill."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from config import get_settings, llm_model
from vat import costs, llm_cache
from vat.costs import (
    NEW_SPEND, PAGE_OPEN, SERPAPI, TOKENS, TOTAL, WEB_SEARCH, PriceTable, PriceTableError,
)
from vat.llm_cache import CostMeter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import vat_cost_backfill  # noqa: E402

TABLE = {
    "version": 7,
    "deployments": {
        "MDM_gpt-6-sol": {"input_per_1m": 2.00, "cached_input_per_1m": 0.10, "output_per_1m": 10.00,
                          "deployment_type": "global", "multiplier": 1.0},
        "helper": {"input_per_1m": 0.75, "cached_input_per_1m": 0.075, "output_per_1m": 4.50,
                         "deployment_type": "global", "multiplier": 1.0},
        "sol-eu": {"input_per_1m": 2.00, "cached_input_per_1m": 0.10, "output_per_1m": 10.00,
                   "deployment_type": "datazone", "multiplier": 1.2},
    },
    "tools": {"web_search_per_call": 0.014, "page_open_per_call": 0.014, "serpapi_per_query": 0.01},
}


@pytest.fixture
def table():
    return PriceTable.from_dict(TABLE)


def _call(label="search_1", **usage):
    return costs.raw_counts(usage, label=label)


class TestPriceTable:
    def test_the_repo_table_loads_with_hash_and_version(self):
        t = PriceTable.load(get_settings().vat_prices_file or None)
        assert t.web_search_per_call == 0.014 and t.page_open_per_call == 0.014
        assert len(t.hash) == 12 and t.version == "1"
        assert {"own_fetch", "vies", "peppol", "wayback"} <= set(t.free)

    def test_the_deployments_named_in_the_config_are_priced(self):
        s = get_settings()
        t = PriceTable.load(s.vat_prices_file or None)
        helper = t.deployment(s.vat_helper_model)  # the helper model: GPT-5.4 mini
        assert (helper.input_per_1m, helper.cached_input_per_1m, helper.output_per_1m) == (0.75, 0.075, 4.50)
        # The calls are priced as the configured deployment, from the table.
        name = s.vat_price_deployment or llm_model()
        if name in t.deployments:
            assert CostMeter.from_settings(s).price == t.deployment(name)
        else:
            with pytest.raises(PriceTableError):
                CostMeter.from_settings(replace(s, mock_external_calls=False))

    def test_missing_deployment_is_an_error(self, table):
        with pytest.raises(PriceTableError, match="no price for deployment 'gpt-9'"):
            table.deployment("gpt-9")

    @pytest.mark.parametrize("broken", [
        {"tools": TABLE["tools"]},
        {"deployments": TABLE["deployments"]},
        {**TABLE, "deployments": {"d": {"input_per_1m": -1, "cached_input_per_1m": 0, "output_per_1m": 0}}},
        {**TABLE, "deployments": {"d": {"input_per_1m": "two", "cached_input_per_1m": 0, "output_per_1m": 0}}},
    ])
    def test_malformed_table_is_an_error(self, broken):
        with pytest.raises(PriceTableError):
            PriceTable.from_dict(broken)

    def test_a_changed_price_changes_the_hash(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text(json.dumps(TABLE), encoding="utf-8")  # JSON is YAML
        first = PriceTable.load(p).hash
        p.write_text(json.dumps({**TABLE, "tools": {**TABLE["tools"], "web_search_per_call": 0.01}}),
                     encoding="utf-8")
        assert PriceTable.load(p).hash != first

    def test_meter_from_settings_refuses_an_unpriced_deployment(self):
        s = replace(get_settings(), mock_external_calls=False, vat_price_deployment="not-in-the-table")
        with pytest.raises(PriceTableError):
            CostMeter.from_settings(s)
        # MOCK_EXTERNAL_CALLS bills nothing: priced at zero, not an error.
        meter = CostMeter.from_settings(replace(s, mock_external_calls=True))
        assert meter.cost({"input_tokens": 10_000, "output_tokens": 500}) == 0.0


class TestLines:
    def test_fixture_usage_gives_the_expected_lines(self, table):
        calls = [
            # Sol search call: reasoning tokens are part of the output tokens.
            _call(input_tokens=10_000, cached_tokens=4_000, output_tokens=800, reasoning_tokens=300,
                  search_calls=3),
            # Sol reader call, replayed from the cache: costs nothing new.
            _call(label="read_1", input_tokens=5_000, cached_tokens=0, output_tokens=200, from_cache=True),
            # One 5.4-mini helper call at reasoning minimal.
            _call(label="ranking", deployment="helper", input_tokens=2_000, cached_tokens=1_000,
                  output_tokens=100, reasoning_tokens=0),
        ]
        s = costs.sum_lines(calls, table)
        sol_search = (6_000 * 2.00 + 4_000 * 0.10 + 800 * 10.00) / 1e6
        reader = (5_000 * 2.00 + 200 * 10.00) / 1e6
        mini = (1_000 * 0.75 + 1_000 * 0.075 + 100 * 4.50) / 1e6
        assert s[WEB_SEARCH] == pytest.approx(0.042)  # 3 search calls x 0.014
        assert s[TOKENS] == pytest.approx(sol_search + reader + mini)
        assert s[PAGE_OPEN] == 0 and s[SERPAPI] == 0 and s["Other_USD"] == 0
        assert s[TOTAL] == pytest.approx(sol_search + reader + mini + 0.042)
        assert s[NEW_SPEND] == pytest.approx(s[TOTAL] - reader)
        assert s["tokens_by_kind"] == pytest.approx({"search": sol_search, "reader": reader, "ranking": mini})
        assert s["tokens_by_deployment"] == pytest.approx({"MDM_gpt-6-sol": sol_search + reader, "helper": mini})
        assert (s["calls"], s["cache_hits"], s["web_search_calls"], s["reasoning_tokens"]) == (3, 1, 3, 300)

    def test_data_zone_multiplier_applies_to_tokens_only(self, table):
        call = _call(deployment="sol-eu", input_tokens=1_000_000, cached_tokens=0, output_tokens=0, search_calls=1)
        lines = costs.call_lines(call, table)
        assert lines[TOKENS] == pytest.approx(2.40) and lines[WEB_SEARCH] == pytest.approx(0.014)

    def test_model_mode_page_opens_and_serpapi(self, table):
        lines = costs.call_lines(_call(label="agent", search_calls=2, other_tool_calls=3), table)
        assert lines[WEB_SEARCH] == pytest.approx(0.028) and lines[PAGE_OPEN] == pytest.approx(0.042)
        serp = costs.call_lines(_call(label="grounded", serpapi_queries=2), table)
        assert serp[SERPAPI] == pytest.approx(0.02) and serp[TOTAL] == pytest.approx(0.02)

    def test_call_kinds_from_labels(self):
        assert [costs.call_kind(x) for x in ("search_1", "search_2", "read_1", "ranking", "name2", "grounded", None)] \
            == ["search", "search", "reader", "ranking", "name2_classifier", "grounded_reader", "agent"]

    def test_helper_calls_must_run_at_reasoning_minimal(self):
        calls = [_call(label="ranking", reasoning_tokens=0), _call(label="name2", reasoning_tokens=900),
                 _call(label="search_1", reasoning_tokens=900)]
        assert costs.helper_reasoning_warnings(calls) == ["name2_classifier call reported 900 reasoning tokens"]


class _Usage:
    def __init__(self, data):
        self.data = data

    def model_dump(self):
        return self.data


class _Response:
    def __init__(self, usage):
        self.usage, self.output, self.status, self.incomplete_details = _Usage(usage), [], "completed", None


class TestCachedTokens:
    def test_responses_api_reads_input_tokens_details(self):
        u = llm_cache.response_usage(_Response({"input_tokens": 9000, "output_tokens": 10,
                                                "input_tokens_details": {"cached_tokens": 4000}}))
        assert u["cached_tokens"] == 4000

    def test_chat_completions_read_prompt_tokens_details(self):
        u = llm_cache.chat_usage(_Response({"prompt_tokens": 3000, "completion_tokens": 20,
                                            "prompt_tokens_details": {"cached_tokens": 1024}}))
        assert (u["input_tokens"], u["cached_tokens"], u["output_tokens"]) == (3000, 1024, 20)

    def test_missing_count_is_logged_once_and_priced_at_the_full_rate(self, caplog, monkeypatch, table):
        monkeypatch.setattr(llm_cache, "_CACHED_COUNT_MISSING_LOGGED", False)
        with caplog.at_level("WARNING", logger="vat.llm_cache"):
            first = llm_cache.response_usage(_Response({"input_tokens": 1_000_000, "output_tokens": 0}))
            llm_cache.response_usage(_Response({"input_tokens": 5, "output_tokens": 0, "input_tokens_details": {}}))
        assert first["cached_tokens"] == 0
        assert sum("cached_tokens" in r.message for r in caplog.records) == 1
        assert costs.call_lines(_call(**first), table)[TOKENS] == pytest.approx(2.00)


class TestLedger:
    def _row(self, table, run_id, usd, **kw):
        run = {"entities": 1, **costs.sum_lines([_call(input_tokens=int(usd * 500_000))], table), **table.stamp}
        return costs.ledger_row(run, run_id=run_id, timestamp="2026-11-05T10:00:00+01:00", step="T",
                                input_file="in.xlsx", engine="llm_web/hybrid", **kw)

    def test_append_and_running_total(self, tmp_path, table):
        ledger = tmp_path / "ledger.csv"
        assert costs.append_ledger(self._row(table, "r1", 1.0), ledger)
        assert costs.append_ledger(self._row(table, "r2", 0.5), ledger)
        assert costs.ledger_total(ledger) == pytest.approx(1.5)
        rows = costs.read_ledger(ledger)
        assert [r["run_id"] for r in rows] == ["r1", "r2"] and rows[0]["price_table_hash"] == table.hash
        assert list(rows[0]) == costs.LEDGER_FIELDS

    def test_same_run_id_is_not_added_twice(self, tmp_path, table):
        ledger = tmp_path / "ledger.csv"
        assert costs.append_ledger(self._row(table, "r1", 1.0), ledger)
        assert not costs.append_ledger(self._row(table, "r1", 1.0), ledger)
        assert len(costs.read_ledger(ledger)) == 1

    def test_a_new_price_table_is_noted(self, tmp_path, table):
        ledger = tmp_path / "ledger.csv"
        costs.append_ledger(self._row(table, "r1", 1.0), ledger)
        other = PriceTable.from_dict({**TABLE, "version": 8})
        costs.append_ledger(self._row(other, "r2", 1.0), ledger)
        assert "price table changed" in costs.read_ledger(ledger)[-1]["note"]


class TestBackfill:
    def _logs(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        hybrid = [{"customer": "1", "calls": [
            {"call": "search_1", "from_cache": False, "input_tokens": 1000, "cached_tokens": 0, "output_tokens": 100,
             "search_calls": 1, "tool_calls": 1},
            {"call": "read_1", "from_cache": True, "input_tokens": 500, "cached_tokens": 0, "output_tokens": 50}]}]
        (logs / "DE_VAT_TEST_AD_50_live_usage.json").write_text(json.dumps(hybrid), encoding="utf-8")
        # Step X style: no per-call list, one agent call from the entity usage.
        model = [{"customer": "2", "input_tokens": 2000, "cached_tokens": 0, "output_tokens": 200,
                  "search_calls": 2, "other_tool_calls": 1, "tool_calls": 3}]
        (logs / "DE_VAT_TEST_100_live_v3_x_usage.json").write_text(json.dumps(model), encoding="utf-8")
        return logs

    def test_backfill_is_idempotent(self, tmp_path, table):
        logs, ledger = self._logs(tmp_path), tmp_path / "ledger.csv"
        rows = vat_cost_backfill.backfill_rows(logs, table)
        assert [r["step"] for r in rows] and all(r["backfilled"] == "yes" for r in rows)
        assert sum(costs.append_ledger(r, ledger) for r in rows) == 2
        assert sum(costs.append_ledger(r, ledger) for r in vat_cost_backfill.backfill_rows(logs, table)) == 0
        assert len(costs.read_ledger(ledger)) == 2

    def test_old_records_are_priced(self, tmp_path, table):
        rows = {r["step"]: r for r in vat_cost_backfill.backfill_rows(self._logs(tmp_path), table)}
        x, ad = rows["X"], rows["AD 50"]
        assert x["calls_agent"] == 1 and x[PAGE_OPEN] == pytest.approx(0.014)
        assert x[WEB_SEARCH] == pytest.approx(0.028)
        assert (ad["api_calls"], ad["cache_hits"]) == (2, 1)
        assert ad[NEW_SPEND] == pytest.approx((1000 * 2 + 100 * 10) / 1e6 + 0.014)

    def test_missing_search_count_is_estimated(self, table):
        calls, estimated = vat_cost_backfill.entity_calls({"input_tokens": 100, "searches": 2})
        assert estimated and calls[0]["web_search_calls"] == 2
