"""Async OpenAI client for LLM calls.

FIX(Bug 6): replaced Azure OpenAI with direct OpenAI client for local testing.
For production at Bruker, replace AsyncOpenAI with AsyncAzureOpenAI and add
azure_endpoint and api_version. The rest of the code stays identical.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import certifi
import httpx
from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AttributeError noise filter — Python 3.13 + httpx 0.28 + openai 2.x
# ---------------------------------------------------------------------------
# openai's `AsyncHttpxClientWrapper.__del__` schedules `aclose()` as a
# fire-and-forget asyncio task whenever the wrapper is garbage collected.
# In httpx 0.28+ on Python 3.13 that aclose() crashes with
#     AttributeError: 'AsyncHttpxClientWrapper' object has no attribute '_transport'
# Because the task is fire-and-forget, the exception is never awaited, and
# asyncio's default handler dumps "Task exception was never retrieved" with
# a full traceback into the logs — once per LLM call. The connection is
# already torn down; the error is functionally harmless. We install a
# loop-level exception handler that swallows only this specific error and
# delegates everything else to the previous handler.
_FILTER_INSTALLED_FLAG = "_httpx_aclose_filter_installed"


def install_httpx_aclose_noise_filter(
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Idempotently install the noise filter on *loop* (or the running
    loop if *loop* is None)."""
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    if getattr(loop, _FILTER_INSTALLED_FLAG, False):
        return

    prior = loop.get_exception_handler()

    def _filtered(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if (
            isinstance(exc, AttributeError)
            and "_transport" in str(exc)
        ):
            return
        if prior is not None:
            prior(_loop, context)
        else:
            _loop.default_exception_handler(context)

    loop.set_exception_handler(_filtered)
    setattr(loop, _FILTER_INSTALLED_FLAG, True)

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def get_openai_client() -> AsyncAzureOpenAI:
    """Returns Azure OpenAI async client.

    The httpx client is constructed explicitly with ``verify=certifi.where()``
    so that a bogus ``SSL_CERT_FILE`` env var (a common gotcha when a
    .env file contains a placeholder corp-CA path that no longer
    exists) cannot break TLS context construction. By providing our
    own ``http_client``, we also bypass openai SDK's
    ``AsyncHttpxClientWrapper``, sidestepping its noisy ``__del__``
    aclose-as-task behaviour on Python 3.13 + httpx 0.28.
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_version = "2024-08-01-preview"
    if not api_key or not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set. "
            "Copy .env.example to .env and add your values, "
            "then restart the server."
        )
    http_client = httpx.AsyncClient(
        verify=certifi.where(),
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    return AsyncAzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
        http_client=http_client,
    )


async def call_openai(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 500,
    client: AsyncAzureOpenAI | None = None,
) -> str:
    """Call OpenAI with structured JSON output enforced.

    Pass ``client`` to reuse a long-lived ``AsyncAzureOpenAI`` instance. When
    omitted, a one-shot client is created (kept for the diagnostic
    scripts in ``llm/test_connection.py`` and ``scripts/verify_fixes``;
    the hot orchestrator path always supplies its cached client via
    ``OpenAIClient``).

    Raises RuntimeError on failure so the orchestrator can log and
    escalate to the next tier cleanly.
    """
    own_client = client is None
    if own_client:
        client = get_openai_client()
    try:
        response = await client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=max_tokens,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"OpenAI call failed: {str(e)}") from e
    finally:
        # Eagerly close one-shot clients so we don't rely on GC and
        # trigger the Python 3.13 / httpx 0.28 aclose() AttributeError
        # storm.
        if own_client and client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass


class OpenAIClient:
    """Thin async wrapper that returns parsed JSON dicts.

    Caches a single ``AsyncAzureOpenAI`` instance for its lifetime so every
    LLM call reuses the same connection pool. Avoids the
    AsyncHttpxClientWrapper aclose() AttributeError noise that
    otherwise floods logs (one per LLM call) on Python 3.13 + recent
    httpx + recent openai SDK.
    """

    def __init__(self, settings: Any = None) -> None:
        self._model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4")
        if settings and hasattr(settings, "openai_model"):
            self._model = settings.openai_model
        # Lazy: only build the AsyncAzureOpenAI on first use. This keeps
        # construction cheap and avoids initialising a network client
        # in code paths that end up using a mock instead.
        self._client: AsyncAzureOpenAI | None = None
        logger.info("LLM client initialised with Azure OpenAI (deployment=%s)", self._model)

    def _get_client(self) -> AsyncAzureOpenAI:
        if self._client is None:
            self._client = get_openai_client()
        return self._client

    async def aclose(self) -> None:
        """Close the underlying AsyncAzureOpenAI client cleanly. Safe to
        call multiple times; safe to call when no client was built."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Send a chat completion request and parse the response as JSON.

        Retries once if the first response is not valid JSON.
        Raises ValueError if JSON cannot be extracted after retry.
        """
        client = self._get_client()
        for attempt in range(2):
            raw = await call_openai(
                system_prompt, user_prompt,
                max_tokens=max_tokens, client=client,
            )
            if raw is None:
                raw = ""

            fence_match = _FENCE_RE.search(raw)
            if fence_match:
                raw = fence_match.group(1)

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("LLM returned invalid JSON, retrying (attempt 1)")
                    continue
                logger.error("LLM returned invalid JSON after retry: %s", raw[:200])
                raise ValueError(f"LLM returned invalid JSON: {raw[:200]}")

        raise ValueError("LLM JSON extraction failed after retries")  # pragma: no cover
