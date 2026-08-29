"""The call trace must be legible, and must never be load-bearing (ticket 21).

The trace exists because three failures on 2026-08-29 produced downstream
evidence byte-identical to "this organisation does not exist" and none was
visible in any log. Its value is entirely in what it *says*, so these tests
assert the content — the verbatim outbound string, the provider, cache HIT vs
MISS, and an outcome that separates *the provider failed* from *it found
nothing*. The rest assert the contract: off by default, secret-free, and
deletable without changing behaviour.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment import call_trace


@pytest.fixture
def tracing(tmp_path, monkeypatch):
    """A call_trace module reloaded with tracing ON, writing to tmp_path.

    Reloaded rather than monkeypatched because ``ENABLED`` is read once at
    import — which is the property that keeps the disabled path a single
    boolean test, and is therefore worth exercising as written.
    """
    text = tmp_path / "trace.log"
    js = tmp_path / "trace.jsonl"
    monkeypatch.setenv("CALL_TRACE", "true")
    monkeypatch.setenv("CALL_TRACE_OUT", str(text))
    monkeypatch.setenv("CALL_TRACE_JSON", str(js))
    module = importlib.reload(call_trace)
    yield module, text, js
    importlib.reload(call_trace)  # restore the OFF default for other tests


class TestOffByDefault:
    def test_the_module_is_inert_unless_the_env_var_is_set(self):
        assert call_trace.ENABLED is False

    def test_a_call_writes_nothing_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CALL_TRACE_OUT", str(tmp_path / "trace.log"))
        call_trace.call("serp", outcome=call_trace.OK, query="acme")
        assert not (tmp_path / "trace.log").exists()


class TestWhatALineSays:
    def test_it_carries_the_verbatim_outbound_string(self, tracing):
        """Not the record's input name. That difference is where three of the
        five failures lived, and a trace of the input shows nothing wrong in
        any of them."""
        module, text, _ = tracing
        module.call(
            "serp", provider="serpapi", outcome=module.OK,
            query='"Mass Inst of Tech" Cambridge', results=3,
        )
        line = text.read_text(encoding="utf-8")
        assert '"Mass Inst of Tech" Cambridge' in line

    def test_it_names_the_provider_not_just_the_lane(self, tracing):
        """"serp" is not an answer to "who said this organisation has no web
        presence"."""
        module, text, _ = tracing
        module.call("serp", provider="duckduckgo", outcome=module.EMPTY,
                    query="acme")
        assert "duckduckgo" in text.read_text(encoding="utf-8")

    def test_a_registry_query_is_read_off_the_params(self, tracing):
        module, text, _ = tracing
        module.call(
            "ror", url="https://api.ror.org/v2/organizations",
            params={"query": "1910 Genetics"}, outcome=module.OK, candidates=20,
        )
        assert 'query="1910 Genetics"' in text.read_text(encoding="utf-8")

    def test_cache_hit_and_network_miss_are_distinguishable(self, tracing):
        module, text, _ = tracing
        module.call("serp", outcome=module.OK, query="a", cache_hit=True)
        module.call("serp", outcome=module.OK, query="b", cache_hit=False)
        lines = text.read_text(encoding="utf-8").splitlines()
        assert "cache=HIT" in lines[0]
        assert "cache=MISS" in lines[1]

    def test_the_outcomes_are_four_distinct_things(self):
        """The vocabulary is the ticket. A provider that failed, one that
        answered "nothing", candidates that did not match, and a candidate a
        gate refused are four different facts that currently collapse into one
        indistinguishable "no match"."""
        outcomes = {
            call_trace.OK, call_trace.EMPTY, call_trace.PROVIDER_FAILED,
            call_trace.NO_CANDIDATE_MATCHED, call_trace.REJECTED_BY_GATE,
            call_trace.FROZEN,
        }
        assert len(outcomes) == 6

    def test_the_json_line_carries_the_same_event(self, tracing):
        module, _, js = tracing
        module.call("ror", url="https://api.ror.org/v2/organizations",
                    params={"affiliation": "Acme, Boston, US"},
                    outcome=module.EMPTY, candidates=0)
        event = json.loads(js.read_text(encoding="utf-8").strip())
        assert event["lane"] == "ror"
        assert event["outcome"] == "empty"
        assert event["params"]["affiliation"] == "Acme, Boston, US"


class TestSecretsNeverAppear:
    @pytest.mark.parametrize("name", [
        "api_key", "apiKey", "SERPAPI_KEY", "access_token", "client_secret",
        "password",
    ])
    def test_credential_shaped_params_are_redacted(self, tracing, name):
        module, text, js = tracing
        module.call("serp", url="https://serpapi.com/search",
                    params={name: "s3cr3t-value", "q": "acme"},
                    outcome=module.OK)
        written = text.read_text(encoding="utf-8") + js.read_text(encoding="utf-8")
        assert "s3cr3t-value" not in written
        assert "<redacted>" in written

    def test_a_non_secret_param_survives(self, tracing):
        module, _, js = tracing
        module.call("serp", params={"q": "acme labs"}, outcome=module.OK)
        assert json.loads(js.read_text(encoding="utf-8"))["params"]["q"] == "acme labs"


class TestTheManifest:
    def test_it_reports_the_resolved_provider_not_the_configuration(self):
        """The 2026-08-29 failure was a configured SerpAPI key that never
        reached a SerpAPI client. Reading the setting would have reported it
        healthy; reading the client reports the truth."""
        class _Ddg:
            provider_id = "duckduckgo"

        class _Settings:
            serpapi_key = "a-real-looking-key"

        out = call_trace.describe_run(_Settings(), _Ddg())
        assert out["serp_provider"] == "duckduckgo"

    def test_it_never_prints_the_key_itself(self):
        class _Settings:
            openai_api_key = "sk-do-not-print-me"

        out = call_trace.describe_run(_Settings())
        rendered = call_trace.manifest(out)
        assert "sk-do-not-print-me" not in rendered
        assert out["llm_key_present"] is True

    def test_it_renders_without_a_console_encoding_error(self):
        """Printed to a Windows console, whose default code page is cp1252 and
        cannot encode box-drawing characters."""
        rendered = call_trace.manifest({"a": 1, "bb": "two"})
        rendered.encode("cp1252")

    def test_it_is_not_gated_on_the_env_var(self):
        """A run that cannot say what it was talking to is the problem, so the
        manifest renders whether or not tracing is switched on. (That the
        default is OFF is asserted in TestOffByDefault; asserting it here too
        would depend on fixture teardown order.)"""
        assert "run manifest" in call_trace.manifest({"x": 1})


class TestItIsNeverLoadBearing:
    def test_no_trace_value_reaches_a_cache_key(self):
        """The trace is an output artefact. If a cache key could see it, a
        warm second run would stop being reproducible."""
        import inspect

        import utils.cache as cache_module

        # Every function that BUILDS a key, named explicitly rather than by
        # slicing the file: `cached_registry_get` and `cached_serp` legitimately
        # trace, and they live between the key builders.
        for name in ("serp_key", "legacy_serp_key", "serp_disk_key",
                     "lookup_key", "legacy_lookup_key", "http_disk_key"):
            source = inspect.getsource(getattr(cache_module, name))
            assert "call_trace" not in source, f"{name} can see the trace"
            assert "trace" not in source, f"{name} can see the trace"

    def test_a_trace_failure_cannot_break_a_request(self, tracing, monkeypatch):
        """`_result_count` describes a body it did not produce, so an
        unfamiliar shape must report 0, not raise."""
        module, _, _ = tracing
        from utils.cache import _result_count

        class _Weird:
            def __len__(self):
                raise RuntimeError("no")

        assert _result_count(_Weird()) == 0
        assert _result_count(None) == 0
        assert _result_count({"items": [1, 2, 3]}) == 3
