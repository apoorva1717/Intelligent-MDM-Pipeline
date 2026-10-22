"""LLM provider selection (config.llm_provider / llm.openai_client.get_openai_client).

Azure OpenAI when AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are both set;
otherwise OpenAI direct when OPENAI_API_KEY is set. No network: the clients
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
        "OPENAI_MODEL_DEDUP", "OPENAI_BASE_URL")


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
