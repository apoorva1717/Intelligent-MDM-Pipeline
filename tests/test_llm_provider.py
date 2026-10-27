"""LLM provider selection (config.llm_provider / llm.openai_client.get_openai_client).

Azure OpenAI when AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are both set;
otherwise OpenAI direct when OPENAI_API_KEY is set; otherwise Anthropic when
ANTHROPIC_API_KEY is set. No network: the clients
are only constructed, and call_openai gets a stand-in client.
"""

from __future__ import annotations

import asyncio

import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI

import config
from llm import openai_client as oc

_ALL = ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION", "AOAI_DEPLOYMENT_DEDUP", "OPENAI_API_KEY", "OPENAI_MODEL",
        "OPENAI_MODEL_DEDUP", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
        "ANTHROPIC_MODEL_DEDUP")


@pytest.fixture
def env(monkeypatch):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)

    def set_(**values):
        for k, v in values.items():
            monkeypatch.setenv(k, v)

    return set_


AZURE = dict(AZURE_OPENAI_API_KEY="az-key", AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com/",
             AZURE_OPENAI_DEPLOYMENT="MDM-test-deployment")
OPENAI = dict(OPENAI_API_KEY="sk-test", OPENAI_MODEL="gpt-test-model")


def _client():
    client = oc.get_openai_client()
    asyncio.run(client.close())
    return client


class TestSelection:
    def test_azure_when_key_and_endpoint_are_set(self, env):
        env(**AZURE)
        assert config.llm_provider() == "azure"
        assert isinstance(_client(), AsyncAzureOpenAI)
        assert config.llm_model() == "MDM-test-deployment"

    def test_azure_wins_when_both_are_configured(self, env):
        env(**AZURE, **OPENAI)
        assert config.llm_provider() == "azure"
        assert isinstance(_client(), AsyncAzureOpenAI)
        assert config.llm_model() == "MDM-test-deployment"

    def test_openai_direct_otherwise(self, env):
        env(**OPENAI)
        assert config.llm_provider() == "openai"
        client = _client()
        assert isinstance(client, AsyncOpenAI) and not isinstance(client, AsyncAzureOpenAI)
        assert str(client.base_url).startswith("https://api.openai.com")
        assert config.llm_model() == "gpt-test-model"

    def test_azure_key_without_endpoint_falls_back_to_openai(self, env):
        env(AZURE_OPENAI_API_KEY="az-key", **OPENAI)
        assert config.llm_provider() == "openai"

    def test_openai_base_url_override(self, env):
        env(**OPENAI, OPENAI_BASE_URL="https://proxy.example/v1")
        assert str(_client().base_url).startswith("https://proxy.example/v1")

    def test_nothing_configured_raises_naming_both_options(self, env):
        assert config.llm_provider() is None
        with pytest.raises(RuntimeError) as exc:
            oc.get_openai_client()
        assert "AZURE_OPENAI_API_KEY" in str(exc.value) and "OPENAI_API_KEY" in str(exc.value)

    def test_openai_default_model(self, env):
        env(OPENAI_API_KEY="sk-test")
        assert config.llm_model() == "gpt-5.4"


class TestDedupModel:
    def test_azure_dedup_override(self, env):
        env(**AZURE, AOAI_DEPLOYMENT_DEDUP="MDM-dedup")
        assert config.llm_model("dedup") == "MDM-dedup"

    def test_openai_dedup_override_and_fallback(self, env):
        env(**OPENAI)
        assert config.llm_model("dedup") == "gpt-test-model"
        env(OPENAI_MODEL_DEDUP="gpt-dedup")
        assert config.llm_model("dedup") == "gpt-dedup"

    def test_dedup_client_uses_it(self, env):
        from dedup.llm import DedupLLM
        env(**OPENAI)
        assert DedupLLM().model == "gpt-test-model"


class TestCacheKeysAndRequests:
    def test_azure_cache_identity_is_unchanged(self, env):
        env(**AZURE)
        assert oc.llm_cache_api_version() == oc.DEFAULT_AZURE_OPENAI_API_VERSION
        env(AZURE_OPENAI_API_VERSION="2025-01-01-preview")
        assert oc.llm_cache_api_version() == "2025-01-01-preview"

    def test_openai_answers_get_their_own_cache_identity(self, env):
        env(**OPENAI)
        assert oc.llm_cache_api_version() == oc.OPENAI_CACHE_API_VERSION

    @pytest.mark.parametrize("values, model", [(AZURE, "MDM-test-deployment"), (OPENAI, "gpt-test-model")])
    def test_call_openai_sends_the_providers_model(self, env, values, model):
        env(**values)
        sent = {}

        class _Completions:
            async def create(self, **kwargs):
                sent.update(kwargs)

                class _R:
                    choices = [type("C", (), {"message": type("M", (), {"content": '{"ok": true}'})()})()]
                return _R()

        class _Client:
            chat = type("Chat", (), {"completions": _Completions()})()

        raw = asyncio.run(oc.call_openai("sys", "user", client=_Client()))
        assert raw == '{"ok": true}'
        assert sent["model"] == model
        assert (sent["temperature"], sent["top_p"]) == (0.0, 1.0)
        assert sent["response_format"] == {"type": "json_object"}

    def test_settings_follow_the_provider(self, env):
        env(**OPENAI)
        s = config.Settings()
        assert (s.openai_model, s.openai_api_key) == ("gpt-test-model", "sk-test")
        env(**AZURE)
        s = config.Settings()
        assert (s.openai_model, s.openai_api_key) == ("MDM-test-deployment", "az-key")


# ── Anthropic: the third provider, only when neither OpenAI flavour is set ──

ANTHROPIC = dict(ANTHROPIC_API_KEY="sk-ant-test", ANTHROPIC_MODEL="claude-opus-5-5")


def _message(text, stop_reason="end_turn", content=None):
    from types import SimpleNamespace as NS
    return NS(
        content=content if content is not None else [NS(type="text", text=text)],
        stop_reason=stop_reason, stop_details=None, model="claude-opus-5-5",
        usage=NS(input_tokens=11, output_tokens=7),
    )


class _AnthropicStub:
    """Stands in for ``AsyncAnthropic``: ``beta.messages.create`` / ``stream``."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent: list[dict] = []
        stub = self

        class _Stream:
            def __init__(self, reply):
                self.reply = reply

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get_final_message(self):
                return self.reply

        class _Messages:
            async def create(self, **kwargs):
                stub.sent.append(kwargs)
                return stub.replies.pop(0)

            def stream(self, **kwargs):
                stub.sent.append(kwargs)
                return _Stream(stub.replies.pop(0))

        self.beta = type("Beta", (), {"messages": _Messages()})()

    async def close(self):
        pass


class TestAnthropic:
    def test_used_only_when_azure_and_openai_are_missing(self, env):
        from anthropic import AsyncAnthropic
        env(**ANTHROPIC)
        assert config.llm_provider() == "anthropic"
        assert isinstance(_client(), AsyncAnthropic)
        assert config.llm_model() == "claude-opus-5-5"
        assert oc.llm_cache_api_version() == oc.ANTHROPIC_CACHE_API_VERSION
        assert config.Settings().openai_api_key == "sk-ant-test"

    @pytest.mark.parametrize("values, provider", [(AZURE, "azure"), (OPENAI, "openai")])
    def test_azure_and_openai_win(self, env, values, provider):
        env(**ANTHROPIC, **values)
        assert config.llm_provider() == provider

    def test_default_and_dedup_model(self, env):
        env(ANTHROPIC_API_KEY="sk-ant-test")
        assert config.llm_model() == config.DEFAULT_ANTHROPIC_MODEL
        env(ANTHROPIC_MODEL_DEDUP="claude-sonnet-5-5")
        assert config.llm_model("dedup") == "claude-sonnet-5-5"

    def test_call_openai_goes_to_claude_without_sampling_params(self, env):
        env(**ANTHROPIC)
        client = _AnthropicStub(_message('Here you go:\n```json\n{"ok": true}\n```'))
        raw = asyncio.run(oc.call_openai("sys", "user", max_tokens=200, client=client))
        assert raw == '{"ok": true}'
        (sent,) = client.sent
        assert sent["model"] == "claude-opus-5-5"
        assert sent["max_tokens"] >= 16_000          # thinking counts against it
        assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
        assert not {"temperature", "top_p", "seed"} & set(sent)
        assert sent["system"].startswith("sys") and "JSON object" in sent["system"]

    @pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
    def test_incomplete_answer_raises(self, env, stop):
        env(**ANTHROPIC)
        client = _AnthropicStub(_message("", stop_reason=stop))
        with pytest.raises(RuntimeError):
            asyncio.run(oc.call_openai("sys", "user", client=client))

    def test_no_fallbacks_on_a_model_that_does_not_take_them(self, env):
        env(ANTHROPIC_API_KEY="sk-ant-test", ANTHROPIC_MODEL="claude-haiku-5-5")
        client = _AnthropicStub(_message("{}"))
        asyncio.run(oc.call_openai("sys", "user", client=client))
        assert "fallbacks" not in client.sent[0] and "betas" not in client.sent[0]

    def test_dedup_adjudicates_on_claude(self, env, monkeypatch):
        from dedup.llm import DedupLLM
        env(**ANTHROPIC, ANTHROPIC_MODEL_DEDUP="claude-sonnet-5-5")
        llm = DedupLLM()
        llm._client = _AnthropicStub(_message('{"verdict": "same"}'))
        result = asyncio.run(llm.adjudicate("sys", "user"))
        assert result.error is None and result.raw == '{"verdict": "same"}'
        assert (result.prompt_tokens, result.completion_tokens) == (11, 7)
        assert llm._client.sent[0]["model"] == "claude-sonnet-5-5"

    def test_web_search_continues_a_paused_turn_and_reports_visited_urls(self, env):
        from types import SimpleNamespace as NS
        from llm import anthropic_client
        env(**ANTHROPIC)
        search = NS(type="web_search_tool_result",
                    content=[NS(url="https://a.example/"), NS(url="https://b.example/impressum")])
        fetch = NS(type="web_fetch_tool_result", content=NS(url="https://b.example/impressum"))
        failed = NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded"))
        client = _AnthropicStub(
            _message("", stop_reason="pause_turn", content=[search]),
            _message("", content=[fetch, failed, NS(type="text", text='{"vat_id": "DE811128135"}')]),
        )
        text, visited = asyncio.run(anthropic_client.web_search(client, "sys", "user", "high"))
        assert text == '{"vat_id": "DE811128135"}'
        assert visited == ["https://a.example/", "https://b.example/impressum"]
        assert client.sent[0]["tools"] == anthropic_client.WEB_TOOLS
        assert client.sent[0]["output_config"] == {"effort": "high"}
        assert [m["role"] for m in client.sent[1]["messages"]] == ["user", "assistant"]
