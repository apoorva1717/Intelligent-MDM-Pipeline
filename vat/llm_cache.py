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

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
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
from vat.rate_limit import estimate_tokens, limiter_for, retry_after
from vat.costs import (
    DEFAULT_DEPLOYMENT, TOTAL, ZERO_PRICE, DeploymentPrice, PriceTable, PriceTableError, call_lines, raw_counts,
)

logger = logging.getLogger(__name__)

#: ``complete(system_prompt, user_prompt, max_tokens) -> raw text``.
Complete = Callable[[str, str, int], Awaitable[str]]
#: ``search(system_prompt, user_prompt, reasoning_effort) -> (raw text, visited URLs)``.
Search = Callable[[str, str, str], Awaitable[tuple[str, list[str]]]]

#: Seconds one web-search call may take: a high-effort reasoning model that
#: searches several times needs far more than a chat completion's read timeout.
WEB_SEARCH_TIMEOUT = 300.0

#: Backoff cap of one retry, and of all retries of one call together.
MAX_BACKOFF = 60.0
MAX_RETRY_WAIT = 180.0

#: Process-wide, like ``openai_client._SEED_SUPPORTED``: the model does not
#: change mid-process, so the first rejection of temperature 0 drops it for
#: every later VAT call instead of paying a 400 per row.
_TEMPERATURE_SUPPORTED: bool = True


#: The LLM_WEB answer's fields: keys recorded with that schema carry no
#: field list, so they replay as before.
_ANSWER_FIELDS = ("vat_id", "source_url", "evidence_quote", "attributed_to", "published_address")


@dataclass(frozen=True)
class SearchLimits:
    """Hard limits of one web-search call (``config.Settings.vat_web_*``).
    *max_searches* 0: a plain call with no web search tool (the reader)."""

    max_searches: int = 2
    #: Pages the model may open on top of the searches. The API has one cap
    #: for both (``max_tool_calls`` = searches + opens); the split is stated
    #: in the prompt and checked afterwards from the usage.
    max_opens: int = 0
    max_output_tokens: int = 1500
    #: Strict JSON schema for the answer (structured output); None: free text.
    schema: dict[str, Any] | None = None
    #: ``prompt_cache_key``: one key for every customer, so the static prefix
    #: is served from the prompt cache.
    cache_key: str = "vat_llm_web"
    #: First wait after an HTTP 429 / 5xx without a Retry-After; doubled per
    #: retry up to :data:`MAX_BACKOFF` (VAT_WEB_RETRY_BACKOFF_SECONDS).
    retry_backoff: float = 5.0
    #: Retries after HTTP 429 / 5xx (VAT_AZURE_MAX_RETRIES); all of them
    #: together wait at most :data:`MAX_RETRY_WAIT` seconds.
    max_retries: int = 6
    #: The deployment for this call (the helper model); None: the LLM
    #: deployment. Part of the cache key.
    model: str | None = None


@dataclass
class SearchAnswer:
    response: dict[str, Any]
    visited: list[str]
    #: input_tokens, cached_tokens, cache_write_tokens, output_tokens,
    #: reasoning_tokens, search_calls, other_tool_calls, status,
    #: incomplete_reason, seconds, http_retries.
    usage: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False


class MalformedOutput(ValueError):
    """The call answered, but not with a JSON object. Never retried."""

    def __init__(self, message: str, usage: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.usage = usage or {}


#: Logged once per process: an answer without a prompt-cache read count.
_CACHED_COUNT_MISSING_LOGGED = False


def _cached_tokens(details: Any, where: str) -> int:
    """Prompt-cache reads from *details* (``input_tokens_details`` of a
    Responses API usage, ``prompt_tokens_details`` of a chat completion's).
    Missing: logged once and 0, so the input is priced at the full rate."""
    global _CACHED_COUNT_MISSING_LOGGED
    cached = (details or {}).get("cached_tokens") if isinstance(details, dict) else None
    if cached is None:
        if not _CACHED_COUNT_MISSING_LOGGED:
            _CACHED_COUNT_MISSING_LOGGED = True
            logger.warning("API usage has no %s.cached_tokens; its input is priced at the full input rate", where)
        return 0
    return int(cached)


def chat_usage(response: Any) -> dict[str, Any]:
    """The usage fields of a chat completion, in :func:`response_usage`'s names."""
    u = response.usage.model_dump() if getattr(response, "usage", None) else {}
    return {
        "input_tokens": u.get("prompt_tokens", 0),
        "cached_tokens": _cached_tokens(u.get("prompt_tokens_details"), "prompt_tokens_details"),
        "output_tokens": u.get("completion_tokens", 0),
        "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
        "tool_calls": 0, "search_calls": 0, "other_tool_calls": 0,
    }


def response_usage(response: Any) -> dict[str, Any]:
    """The usage fields of a Responses API answer, and its tool calls."""
    u = response.usage.model_dump() if getattr(response, "usage", None) else {}
    calls = [o for o in getattr(response, "output", None) or [] if getattr(o, "type", "") == "web_search_call"]
    actions = [getattr(c, "action", None) for c in calls]
    kinds = [getattr(a, "type", None) for a in actions]
    incomplete = getattr(response, "incomplete_details", None)
    return {
        "input_tokens": u.get("input_tokens", 0),
        "cached_tokens": _cached_tokens(u.get("input_tokens_details"), "input_tokens_details"),
        "cache_write_tokens": (u.get("input_tokens_details") or {}).get("cache_write_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
        "tool_calls": len(calls),
        "search_calls": sum(k == "search" for k in kinds),
        "other_tool_calls": sum(k != "search" for k in kinds),
        "queries": [getattr(a, "query", None) for a in actions if getattr(a, "type", None) == "search"],
        "api_status": getattr(response, "status", None),
        "incomplete_reason": getattr(incomplete, "reason", None) if incomplete else None,
    }


def _status(exc: BaseException) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


def _retryable(exc: BaseException) -> bool:
    """HTTP 429 or 5xx from the API — the only errors retried, once."""
    status = _status(exc)
    return status is not None and (status == 429 or 500 <= status < 600)


class CostMeter:
    """Running cost of a run's calls, tokens and tool calls together, priced
    by the table (``vat/costs.py``): the stop limit is checked against
    ``Total_USD``, never tokens alone."""

    def __init__(self, table: PriceTable | None = None, *, deployment: str | None = None,
                 budget: float = 0.0, price: DeploymentPrice | None = None) -> None:
        """No *table*: everything is free (tests). *price*: the deployment's
        price instead of the table's (MOCK_EXTERNAL_CALLS)."""
        self.table = table or PriceTable(deployments={}, web_search_per_call=0.0, page_open_per_call=0.0,
                                         serpapi_per_query=0.0)
        self.deployment = deployment or DEFAULT_DEPLOYMENT
        self.price = price or (self.table.deployment(self.deployment) if table is not None else ZERO_PRICE)
        self.budget = budget
        self.spent = 0.0
        self._stop_logged = False

    @classmethod
    def from_settings(cls, settings: Any) -> "CostMeter":
        """The table at ``vat_prices_file``, priced as ``vat_price_deployment``
        (the LLM deployment). A deployment the table has no price for is an
        error, except under MOCK_EXTERNAL_CALLS, where nothing is billed."""
        table = PriceTable.load(getattr(settings, "vat_prices_file", "") or None)
        deployment = getattr(settings, "vat_price_deployment", "") or llm_model()
        budget = float(getattr(settings, "vat_web_budget_usd", 0) or 0)
        try:
            return cls(table, deployment=deployment, budget=budget)
        except PriceTableError:
            if not getattr(settings, "mock_external_calls", False):
                raise
            return cls(table, deployment=deployment, budget=budget, price=ZERO_PRICE)

    @property
    def overrides(self) -> dict[str, DeploymentPrice]:
        """This meter's price for its deployment, for :func:`vat.costs.sum_lines`."""
        return {self.deployment: self.price}

    def counts(self, usage: dict[str, Any], *, label: str | None = None,
               cache_hit: bool | None = None) -> dict[str, Any]:
        """One API response's raw counts (``vat.costs.raw_counts``), on this
        meter's deployment unless the usage names one."""
        return raw_counts(usage, deployment=self.deployment, label=label, cache_hit=cache_hit)

    def lines(self, usage: dict[str, Any]) -> dict[str, float]:
        """The cost lines of one call; *usage* in the API's or the raw names."""
        counts = usage if "web_search_calls" in usage else self.counts(usage)
        price = self.price if counts["deployment"] == self.deployment else None
        return call_lines(counts, self.table, price)

    def cost(self, usage: dict[str, Any]) -> float:
        """USD for one call: tokens + search calls + page opens + SerpAPI."""
        return self.lines(usage)[TOTAL]

    def add(self, usage: dict[str, Any]) -> float:
        cost = self.cost(usage)
        self.spent += cost
        return cost

    @property
    def exhausted(self) -> bool:
        stop = self.budget > 0 and self.spent >= self.budget
        if stop and not self._stop_logged:
            self._stop_logged = True
            logger.warning("VAT run stop limit reached: %.4f USD spent of %.2f (tokens + search calls); "
                           "no further model calls this run", self.spent, self.budget)
        return stop


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

    async def _default_search(
        self, system: str, user: str, effort: str, limits: "SearchLimits | None" = None,
    ) -> tuple[str, list[str], dict[str, Any]]:
        if self._client is None:
            self._client = get_openai_client()
        if llm_provider() == ANTHROPIC:
            # web_search + web_fetch server tools (llm/anthropic_client.py).
            from llm import anthropic_client
            try:
                return await anthropic_client.web_search(
                    self._client, system, user, effort,
                    max_uses=limits.max_searches if limits else None,
                    max_fetches=limits.max_opens if limits else None,
                    max_tokens=limits.max_output_tokens if limits else None, with_usage=True)
            except Exception as e:
                raise RuntimeError(f"Anthropic web search call failed: {e}") from e
        kwargs: dict[str, Any] = dict(tools=[{"type": "web_search"}],
                                      include=["web_search_call.action.sources"])
        if limits is not None:
            # max_tool_calls: the API refuses a further web_search call (a
            # search or a page open) and the model has to answer with what
            # it has.
            kwargs.update(max_output_tokens=limits.max_output_tokens,
                          prompt_cache_key=limits.cache_key)
            if limits.max_searches:
                kwargs["max_tool_calls"] = limits.max_searches + limits.max_opens
            else:
                kwargs.pop("tools"), kwargs.pop("include")
            if limits.schema:
                kwargs["text"] = {"format": {"type": "json_schema", "name": "vat_lookup",
                                             "schema": limits.schema, "strict": True}}
        deployment = limits.model if limits is not None and limits.model else llm_model()
        responses = self._client.with_options(timeout=WEB_SEARCH_TIMEOUT, max_retries=0).responses
        create = dict(model=deployment, instructions=system, input=user, reasoning={"effort": effort}, **kwargs)
        raw_api = getattr(responses, "with_raw_response", None)
        if raw_api is not None:
            # The raw response carries Azure's x-ratelimit-* headers: the
            # deployment's quota for the limiter (vat/rate_limit.py).
            raw = await raw_api.create(**create)
            limiter_for(deployment).learn(getattr(raw, "headers", None))
            response = raw.parse()
        else:
            response = await responses.create(**create)
        return response.output_text or "", visited_urls(response.output), response_usage(response)

    def _search_key(self, system: str, user: str, effort: str,
                    limits: "SearchLimits | None" = None) -> str:
        tag = f"[responses web_search reasoning={effort}]"
        if limits is not None:
            # "2" as before page opens had their own cap, so those recordings replay.
            calls = (f"{limits.max_searches}+{limits.max_opens}" if limits.max_opens
                     else str(limits.max_searches))
            tag = (f"[responses web_search reasoning={effort} max_tool_calls={calls} "
                   f"max_output_tokens={limits.max_output_tokens} schema={bool(limits.schema)}]")
            fields = tuple((limits.schema or {}).get("required") or ())
            if fields and fields != _ANSWER_FIELDS:
                # Another answer shape is another call ("schema=True" alone
                # replayed search 2 with the answer schema's recording).
                tag += f"[fields={','.join(fields)}]"
        return llm_disk_key(
            deployment=(limits.model if limits is not None and limits.model else llm_model()),
            api_version=llm_cache_api_version(),
            temperature=None,
            top_p=LLM_TOP_P,
            seed=None,
            max_tokens=0,
            system_prompt=f"{tag}\n{system}",
            user_prompt=user,
        )

    async def search_json(
        self, system: str, user: str, *, reasoning_effort: str,
    ) -> tuple[dict[str, Any], list[str]]:
        """``(the model's JSON answer, the URLs its search visited)``; see
        :meth:`search`."""
        answer = await self.search(system, user, reasoning_effort=reasoning_effort)
        return answer.response, answer.visited

    @staticmethod
    def _backoff(exc: BaseException, limits: "SearchLimits | None", attempt: int) -> float:
        """Seconds before retry *attempt* + 1: Azure's Retry-After, else the
        doubling backoff with ±20% jitter (so waiting callers spread out)."""
        asked = retry_after(exc)
        if asked is not None:
            return min(asked, MAX_BACKOFF)
        base = limits.retry_backoff if limits is not None else 0.0
        return min(base * 2 ** attempt, MAX_BACKOFF) * random.uniform(0.8, 1.2)

    async def search(
        self, system: str, user: str, *, reasoning_effort: str,
        limits: "SearchLimits | None" = None,
    ) -> "SearchAnswer":
        """One web-search call: the model's JSON answer, the URLs its search
        visited and the API usage. Raises ``LLMUnavailableFrozen`` on a frozen
        miss, ``RuntimeError`` when the call fails, ``ValueError`` when the
        output is not a JSON object.

        No retry on a model error or malformed output. With *limits*, a live
        call first waits for its deployment's quota (``vat/rate_limit.py``),
        and HTTP 429 / 5xx is retried up to ``limits.max_retries`` times:
        after the Retry-After Azure sends, else ``limits.retry_backoff``
        doubled per retry (at most :data:`MAX_BACKOFF`), all waits together
        at most :data:`MAX_RETRY_WAIT`. A 429 pauses the deployment for every
        caller. Only the timing changes, never what is sent."""
        key = self._search_key(system, user, reasoning_effort, limits)
        recorded = self._store.get(key)
        if isinstance(recorded, dict) and "response" in recorded:
            self.cache_hits += 1
            self.last_fetched_at = self._store.fetched_at(key)
            return SearchAnswer(recorded["response"], list(recorded.get("visited_urls") or []),
                                dict(recorded.get("usage") or {}), from_cache=True)
        if self._store.replay_only:
            raise LLMUnavailableFrozen("no recorded web search for this prompt")

        search = self._search or self._default_search
        attempts = 1 + max(0, limits.max_retries) if limits is not None else 1
        limiter = limiter_for(limits.model if limits is not None and limits.model else llm_model())
        reserved = estimate_tokens(system, user, limits.max_output_tokens if limits else 0,
                                   limits.max_searches if limits else 0)
        started = time.perf_counter()
        queued = backoff_total = slept = 0.0
        for attempt in range(attempts):
            queued += await limiter.acquire(reserved)
            self.network_calls += 1
            try:
                out = await (search(system, user, reasoning_effort, limits)
                             if search == self._default_search else search(system, user, reasoning_effort))
                break
            except Exception as e:  # noqa: BLE001
                limiter.settle(reserved, 0)
                wait = self._backoff(e, limits, attempt)
                if attempt + 1 < attempts and _retryable(e) and backoff_total + wait <= MAX_RETRY_WAIT:
                    logger.warning("VAT web search got %s; retry %d of %d in %.0fs", _status(e),
                                   attempt + 1, attempts - 1, wait)
                    backoff_total += wait
                    if _status(e) == 429:
                        limiter.pause(wait)  # every caller of the deployment waits (in acquire)
                    else:
                        await asyncio.sleep(wait)
                        slept += wait
                    continue
                if isinstance(e, (RuntimeError, ValueError, LLMUnavailableFrozen)):
                    raise
                raise RuntimeError(f"web search call failed: {e}") from e
        raw, visited = out[0] or "", out[1]
        usage = dict(out[2]) if len(out) > 2 and out[2] else {}
        limiter.settle(reserved, (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                       if usage.get("input_tokens") is not None else None)
        usage["seconds"] = round(time.perf_counter() - started, 2)
        usage["http_retries"] = attempt
        if queued or slept:
            usage["rate_limit_wait_s"] = round(queued + slept, 2)
        fence = _FENCE_RE.search(raw)
        if fence:
            raw = fence.group(1)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise MalformedOutput(f"LLM returned invalid JSON: {raw[:200]}", usage) from None
        if not isinstance(parsed, dict):
            raise MalformedOutput(f"LLM returned JSON that is not an object: {raw[:200]}", usage)
        self.write_failures.set_checked(self._store, key, {
            "response": parsed,
            "visited_urls": visited,
            "usage": usage,
            "system_head": system[:160],
            "user_head": user[:400],
        })
        self.last_fetched_at = self._store.fetched_at(key)
        return SearchAnswer(parsed, visited, usage)

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
