"""The VAT lookups' LLM call, cached in their own ``vat_llm`` namespace.

``llm.openai_client.OpenAIClient.extract_json`` caches through the
*process-wide* active evidence cache, which only the enrichment Orchestrator
installs. Outside a batch it therefore has no cache and no frozen mode, and
inside one it would file VAT answers under enrichment's ``llm`` namespace —
and installing this package's cache as the active one would swap the store a
concurrent ``/enrich`` is reading. So this is the same call, the same cache
key, the same fence stripping and the same one retry on bad JSON, against a
store passed in:

* ``call_openai`` — temperature 0, ``top_p`` 1, fixed seed, JSON response
  format (reused as is), except that a model which rejects temperature 0
  (GPT-6 accepts only its default) gets no temperature from then on — VAT
  only; enrichment and dedup still fail on such a model rather than run
  non-deterministically;
* ``utils.cache.llm_disk_key`` — every parameter that can change the answer;
* ``CACHE_FROZEN`` — a miss raises ``LLMUnavailableFrozen`` and makes no call.

:meth:`VatLLM.search_json` is the LLM_WEB call (``vat/lookup_web.py``): the
Responses API with the ``web_search`` tool and a reasoning effort, no
temperature (reasoning models take none). It returns the model's JSON and the
URLs the search actually visited (every ``web_search_call``'s sources and
opened pages), cached together under the same kind of key with the tool and
effort in it. A web search is not reproducible run to run; the cache is what
makes a frozen re-run reproduce it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from llm import openai_client
from config import ANTHROPIC, llm_model, llm_provider
from llm.openai_client import (
    LLM_SEED,
    LLM_TEMPERATURE,
    LLM_TOP_P,
    LLMUnavailableFrozen,
    _FENCE_RE,
    _is_unsupported_param,
    call_openai,
    get_openai_client,
    llm_cache_api_version,
)
from utils.cache import DiskCache, llm_disk_key
from vat.cache_guard import WriteFailures

logger = logging.getLogger(__name__)

#: ``complete(system_prompt, user_prompt, max_tokens) -> raw text``.
Complete = Callable[[str, str, int], Awaitable[str]]
#: ``search(system_prompt, user_prompt, reasoning_effort) -> (raw text, visited URLs)``.
Search = Callable[[str, str, str], Awaitable[tuple[str, list[str]]]]

#: Seconds one web-search call may take: a high-effort reasoning model that
#: searches several times needs far more than a chat completion's read timeout.
WEB_SEARCH_TIMEOUT = 300.0

#: Process-wide, like ``openai_client._SEED_SUPPORTED``: the model does not
#: change mid-process, so the first rejection of temperature 0 drops it for
#: every later VAT call instead of paying a 400 per row.
_TEMPERATURE_SUPPORTED: bool = True


def _temperature() -> float | None:
    """The temperature VAT calls send now; ``None`` is the model's default."""
    return LLM_TEMPERATURE if _TEMPERATURE_SUPPORTED else None


def reset_temperature_support() -> None:
    """Re-enable temperature 0 (tests only — the runtime never re-probes)."""
    global _TEMPERATURE_SUPPORTED
    _TEMPERATURE_SUPPORTED = True


class VatLLM:
    """JSON completions through *store*. ``complete`` is injectable for tests."""

    def __init__(
        self, store: DiskCache, *, complete: Complete | None = None,
        search: Search | None = None, write_failures: WriteFailures | None = None,
    ) -> None:
        self._store = store
        self.write_failures = write_failures or WriteFailures()
        self._complete = complete
        self._search = search
        self._client = None
        #: Completions actually requested from the model (not cache replays).
        self.network_calls = 0
        self.cache_hits = 0
        #: The date the last answer returned was recorded (UTC ISO date): the
        #: original recording's on a replay, so it is stable across re-runs.
        self.last_fetched_at: str | None = None

    async def _default_complete(self, system: str, user: str, max_tokens: int) -> str:
        global _TEMPERATURE_SUPPORTED
        if self._client is None:
            self._client = get_openai_client()
        sent = _temperature()
        try:
            return await call_openai(
                system, user, max_tokens=max_tokens, client=self._client,
                temperature=sent,
            )
        except RuntimeError as exc:
            cause = exc.__cause__ or exc
            # Judged on what THIS call sent, not the flag: rows run
            # concurrently, so several calls can be in flight at temperature 0
            # when the first rejection flips it — each must still retry.
            if sent is None or not _is_unsupported_param(cause, "temperature"):
                raise
            if _TEMPERATURE_SUPPORTED:
                _TEMPERATURE_SUPPORTED = False
                logger.warning(
                    "Model %s rejected temperature=%s (%s). VAT lookups now use the "
                    "model's default temperature for this process; their answers are "
                    "not reproducible run to run.", llm_model(), LLM_TEMPERATURE, cause,
                )
            return await call_openai(
                system, user, max_tokens=max_tokens, client=self._client,
                temperature=None,
            )

    def _key(
        self, system: str, user: str, max_tokens: int, temperature: float | None,
    ) -> str:
        return llm_disk_key(
            deployment=llm_model(),
            api_version=llm_cache_api_version(),
            temperature=temperature,
            top_p=LLM_TOP_P,
            # Read at call time: call_openai drops the seed process-wide the
            # first time the deployment rejects it.
            seed=LLM_SEED if openai_client._SEED_SUPPORTED else None,
            max_tokens=max_tokens,
            system_prompt=system,
            user_prompt=user,
        )

    async def extract_json(
        self, system: str, user: str, *, max_tokens: int = 800,
    ) -> dict[str, Any]:
        """The model's JSON answer. Raises ``LLMUnavailableFrozen`` on a frozen
        miss, ``RuntimeError`` / ``ValueError`` when the model fails."""
        # A fresh process starts at temperature 0 and only learns the model
        # rejects it on its first call, so a frozen re-run would never look
        # under the default-temperature key it recorded with. Read both.
        for temperature in dict.fromkeys((_temperature(), None)):
            key = self._key(system, user, max_tokens, temperature)
            recorded = self._store.get(key)
            if isinstance(recorded, dict) and "response" in recorded:
                self.cache_hits += 1
                self.last_fetched_at = self._store.fetched_at(key)
                return recorded["response"]
        if self._store.replay_only:
            raise LLMUnavailableFrozen("no recorded completion for this prompt")

        complete = self._complete or self._default_complete
        for attempt in range(2):
            self.network_calls += 1
            raw = await complete(system, user, max_tokens) or ""
            fence = _FENCE_RE.search(raw)
            if fence:
                raw = fence.group(1)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("VAT LLM returned invalid JSON, retrying once")
                    continue
                raise ValueError(f"LLM returned invalid JSON: {raw[:200]}")
            # Filed under the temperature actually sent, which the call
            # above may just have dropped.
            key = self._key(system, user, max_tokens, _temperature())
            self.write_failures.set_checked(self._store, key, {
                "response": parsed,
                "system_head": system[:160],
                "user_head": user[:400],
            })
            self.last_fetched_at = self._store.fetched_at(key)
            return parsed
        raise ValueError("LLM JSON extraction failed after retries")  # pragma: no cover

    async def _default_search(self, system: str, user: str, effort: str) -> tuple[str, list[str]]:
        if self._client is None:
            self._client = get_openai_client()
        if llm_provider() == ANTHROPIC:
            # web_search + web_fetch server tools (llm/anthropic_client.py).
            from llm import anthropic_client
            try:
                return await anthropic_client.web_search(self._client, system, user, effort)
            except Exception as e:
                raise RuntimeError(f"Anthropic web search call failed: {e}") from e
        try:
            response = await self._client.with_options(timeout=WEB_SEARCH_TIMEOUT).responses.create(
                model=llm_model(),
                instructions=system,
                input=user,
                tools=[{"type": "web_search"}],
                reasoning={"effort": effort},
                include=["web_search_call.action.sources"],
            )
        except Exception as e:
            raise RuntimeError(f"OpenAI web search call failed: {e}") from e
        return response.output_text or "", visited_urls(response.output)

    def _search_key(self, system: str, user: str, effort: str) -> str:
        return llm_disk_key(
            deployment=llm_model(),
            api_version=llm_cache_api_version(),
            temperature=None,
            top_p=LLM_TOP_P,
            seed=None,
            max_tokens=0,
            system_prompt=f"[responses web_search reasoning={effort}]\n{system}",
            user_prompt=user,
        )

    async def search_json(
        self, system: str, user: str, *, reasoning_effort: str,
    ) -> tuple[dict[str, Any], list[str]]:
        """``(the model's JSON answer, the URLs its search visited)``. Raises
        ``LLMUnavailableFrozen`` on a frozen miss, ``RuntimeError`` /
        ``ValueError`` when the model fails."""
        key = self._search_key(system, user, reasoning_effort)
        recorded = self._store.get(key)
        if isinstance(recorded, dict) and "response" in recorded:
            self.cache_hits += 1
            self.last_fetched_at = self._store.fetched_at(key)
            return recorded["response"], list(recorded.get("visited_urls") or [])
        if self._store.replay_only:
            raise LLMUnavailableFrozen("no recorded web search for this prompt")

        search = self._search or self._default_search
        for attempt in range(2):
            self.network_calls += 1
            raw, visited = await search(system, user, reasoning_effort)
            raw = raw or ""
            fence = _FENCE_RE.search(raw)
            if fence:
                raw = fence.group(1)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("VAT web search returned invalid JSON, retrying once")
                    continue
                raise ValueError(f"LLM returned invalid JSON: {raw[:200]}")
            if not isinstance(parsed, dict):
                raise ValueError(f"LLM returned JSON that is not an object: {raw[:200]}")
            self.write_failures.set_checked(self._store, key, {
                "response": parsed,
                "visited_urls": visited,
                "system_head": system[:160],
                "user_head": user[:400],
            })
            self.last_fetched_at = self._store.fetched_at(key)
            return parsed, visited
        raise ValueError("LLM JSON extraction failed after retries")  # pragma: no cover

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


def visited_urls(output: Any) -> list[str]:
    """Every URL a Responses API ``output`` shows the web search visiting: each
    ``web_search_call``'s action URL (an opened page / a find in page) and the
    sources of its searches, in order, without repeats."""
    urls: list[str] = []
    for item in output or []:
        d = item.model_dump() if hasattr(item, "model_dump") else item
        if not isinstance(d, dict) or d.get("type") != "web_search_call":
            continue
        action = d.get("action") or {}
        candidates = [action.get("url")] + [
            s.get("url") for s in action.get("sources") or [] if isinstance(s, dict)]
        for url in candidates:
            if url and url not in urls:
                urls.append(url)
    return urls
