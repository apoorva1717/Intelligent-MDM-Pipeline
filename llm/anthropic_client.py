"""Anthropic (Claude) as the third LLM provider.

Used only when neither Azure OpenAI nor OpenAI direct is configured and
``ANTHROPIC_API_KEY`` is set (:func:`config.llm_provider`). The model is
``ANTHROPIC_MODEL`` (default :data:`config.DEFAULT_ANTHROPIC_MODEL`).

The three call shapes the pipeline uses map onto the Messages API:

* :func:`complete` — one JSON answer: ``call_openai`` (Phase 1 tiers, VAT
  reader / LLM-only) and the dedup adjudicator route here.
* :func:`web_search` — the VAT LLM_WEB lookup (``vat/llm_cache.py``): the
  ``web_search`` and ``web_fetch`` server tools in place of the Responses API
  ``web_search`` tool, with every URL they visited.

Differences from the OpenAI path, all deliberate:

* No ``temperature`` / ``top_p`` / ``seed``: current Claude models reject
  sampling parameters, and there is no seed. Reproducibility comes from the
  evidence cache, as it already does for gpt-6 (which only takes its default
  temperature).
* No JSON response format: the system prompt is extended with a JSON-only
  instruction and :func:`json_text` cuts the object out of the reply.
* Thinking is always on in current models and counts against ``max_tokens``,
  so the cap sent is at least :data:`MIN_MAX_TOKENS` (the caller's value still
  keys the cache, so recordings stay comparable across providers).
* ``fallbacks="default"`` on the models that accept it: a safety-classifier
  decline is re-run server-side on Anthropic's recommended fallback model
  instead of failing the row.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from config import llm_model

#: Effort for a plain JSON answer. Claude Opus 5.5 defaults to medium; it is
#: set explicitly so a model with a different default behaves the same.
COMPLETE_EFFORT = "medium"
#: Thinking tokens count against max_tokens; below this a short JSON answer
#: can be cut off mid-thought (the gpt-6-sol empty-answer failure).
MIN_MAX_TOKENS = 16_000
#: Output cap of one web-search turn (streamed, so no HTTP timeout concern).
WEB_SEARCH_MAX_TOKENS = 64_000
#: ``pause_turn`` continuations allowed in one web search before giving up.
MAX_PAUSE_CONTINUATIONS = 5

#: Models that take ``fallbacks="default"`` (beta ``server-side-fallback-2026-07-01``).
_FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

JSON_ONLY = (
    "\n\nRespond with exactly one JSON object and nothing else: no prose before "
    "or after it, no markdown code fence."
)

WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


def get_anthropic_client() -> AsyncAnthropic:
    """The async client, with the same TLS / proxy handling as the OpenAI one."""
    from llm.openai_client import resolve_tls_verify  # import cycle

    connect_timeout = float(os.getenv("LLM_HTTP_CONNECT_TIMEOUT", "30"))
    read_timeout = float(os.getenv("LLM_HTTP_TIMEOUT", "60"))
    return AsyncAnthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"].strip(),
        # Thinking makes a JSON answer slower than a chat completion, so the
        # read timeout is at least the SDK default's order, not 60 s.
        timeout=anthropic.Timeout(max(read_timeout, 300.0), connect=connect_timeout),
        http_client=DefaultAsyncHttpxClient(verify=resolve_tls_verify(), trust_env=True),
    )


def _fallback_kwargs(model: str) -> dict[str, Any]:
    if model in _FALLBACK_MODELS:
        return {"fallbacks": "default", "betas": [_FALLBACK_BETA]}
    return {}


def _check_stop(message: Any) -> None:
    """Raise for an answer that is not a complete answer."""
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise RuntimeError(
            f"Claude declined the request (category={getattr(details, 'category', None)})"
        )
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Claude hit max_tokens before finishing its answer")


def _text(message: Any) -> str:
    return "".join(b.text for b in message.content if b.type == "text")


def json_text(text: str) -> str:
    """The JSON object in *text*: from the first ``{`` to the last ``}``, which
    also drops a code fence or a sentence around it. Unchanged when there is
    no object, so the caller's ``json.loads`` reports the real reply."""
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else text


async def complete(
    client: AsyncAnthropic, system_prompt: str, user_prompt: str, max_tokens: int,
    *, model: str | None = None,
) -> Completion:
    """One JSON answer from *model* (default ``llm_model()``). Raises
    ``anthropic.APIError`` / RuntimeError on failure."""
    model = model or llm_model()
    message = await client.beta.messages.create(
        model=model,
        max_tokens=max(max_tokens, MIN_MAX_TOKENS),
        system=system_prompt + JSON_ONLY,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"effort": COMPLETE_EFFORT},
        **_fallback_kwargs(model),
    )
    _check_stop(message)
    return Completion(
        text=json_text(_text(message)),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        model=message.model,
    )


def visited_urls(content: Any) -> list[str]:
    """Every URL a web_search result or web_fetch listed, in order, no repeats."""
    urls: list[str] = []
    for block in content:
        if block.type == "web_search_tool_result":
            # A list on success; an error object (no URLs) otherwise.
            found = [r.url for r in block.content] if isinstance(block.content, list) else []
        elif block.type == "web_fetch_tool_result":
            found = [getattr(block.content, "url", None)]
        else:
            continue
        for url in found:
            if url and url not in urls:
                urls.append(url)
    return urls


async def web_search(
    client: AsyncAnthropic, system_prompt: str, user_prompt: str, effort: str,
    *, max_uses: int | None = None, max_fetches: int | None = None, max_tokens: int | None = None,
    with_usage: bool = False,
):
    """``(the final answer's JSON text, every URL visited)`` for one search task;
    with *with_usage* also the usage. *max_uses* caps web_search and
    *max_fetches* web_fetch (default: *max_uses*), each separately: past it
    the tool answers ``max_uses_exceeded`` and Claude has to answer with what
    it has. *max_fetches* 0 leaves web_fetch out."""
    model = llm_model()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    visited: list[str] = []
    caps = {"web_search": max_uses, "web_fetch": max_uses if max_fetches is None else max_fetches}
    tools = [dict(t, max_uses=caps[t["name"]]) if caps.get(t["name"]) else t
             for t in WEB_TOOLS if caps.get(t["name"]) != 0]
    totals = {"input_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
              "reasoning_tokens": 0, "tool_calls": 0, "search_calls": 0, "other_tool_calls": 0}
    for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
        async with client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens or WEB_SEARCH_MAX_TOKENS,
            system=system_prompt + JSON_ONLY,
            messages=messages,
            tools=tools,
            output_config={"effort": effort},
            **_fallback_kwargs(model),
        ) as stream:
            message = await stream.get_final_message()
        visited += [u for u in visited_urls(message.content) if u not in visited]
        u = message.usage
        server = getattr(u, "server_tool_use", None)
        searches = getattr(server, "web_search_requests", 0) or 0
        fetches = getattr(server, "web_fetch_requests", 0) or 0
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        written = getattr(u, "cache_creation_input_tokens", 0) or 0
        totals["input_tokens"] += (u.input_tokens or 0) + cached + written
        totals["cached_tokens"] += cached
        totals["cache_write_tokens"] += written
        totals["output_tokens"] += u.output_tokens or 0
        totals["search_calls"] += searches
        totals["other_tool_calls"] += fetches
        totals["tool_calls"] += searches + fetches
        if message.stop_reason != "pause_turn":
            _check_stop(message)
            text = json_text(_text(message))
            if with_usage:
                return text, visited, {**totals, "api_status": message.stop_reason}
            return text, visited
        # A long server-tool turn paused: send it back to let Claude continue.
        messages = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": message.content},
        ]
    raise RuntimeError(f"Claude web search still paused after {MAX_PAUSE_CONTINUATIONS} continuations")
