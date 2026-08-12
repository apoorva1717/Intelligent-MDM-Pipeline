"""Async Azure OpenAI client for LLM calls.

Azure OpenAI is the only LLM backend, in every environment — there is no
direct-OpenAI / "local" path. The deployment is read from
``AZURE_OPENAI_DEPLOYMENT`` (the dedup adjudicator may override it via
``AOAI_DEPLOYMENT_DEDUP``); the endpoint and key come from
``AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_API_KEY``.
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


# Default Azure OpenAI REST API version for the Phase 1 enrichment tiers.
# Reasoning models (GPT-5.x) and the ``reasoning_effort`` parameter need a
# newer version — see ``get_openai_client(api_version=...)`` callers.
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-08-01-preview"

# CA-bundle env vars consulted (in order) for TLS verification, before
# falling back to certifi. A dedicated var comes first so the LLM client
# can be pointed at a corp CA without disturbing other tooling.
_CA_BUNDLE_ENV_VARS = ("AZURE_OPENAI_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def _env_bool(name: str, default: bool = True) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def resolve_tls_verify() -> str | bool:
    """Resolve the httpx ``verify`` setting for outbound LLM calls.

    Corporate VPNs frequently terminate TLS with their own root CA (SSL
    inspection / MITM proxy). When that happens, verifying the connection
    against certifi's public bundle fails the handshake and every LLM call
    hangs or errors out the moment the VPN is connected. To survive that:

    1. ``LLM_SSL_VERIFY=false`` disables verification entirely. Insecure —
       a last resort for locked-down machines where the corp CA cannot be
       installed. Logged loudly.
    2. Otherwise, if a CA bundle is configured via one of
       ``AZURE_OPENAI_CA_BUNDLE`` / ``REQUESTS_CA_BUNDLE`` / ``SSL_CERT_FILE``
       and the file exists, that bundle is used (point it at the corp CA
       exported as a ``.pem``).
    3. Otherwise certifi's bundle is used (the normal, non-VPN path).
    """
    if not _env_bool("LLM_SSL_VERIFY", default=True):
        logger.warning(
            "LLM_SSL_VERIFY=false — TLS certificate verification is DISABLED "
            "for LLM calls. This is insecure; only use it when the corporate "
            "CA cannot be installed."
        )
        return False

    for var in _CA_BUNDLE_ENV_VARS:
        path = os.getenv(var)
        if path and os.path.isfile(path):
            logger.info(
                "Using CA bundle from %s for LLM TLS verification: %s", var, path
            )
            return path

    return certifi.where()


def get_openai_client(api_version: str | None = None) -> AsyncAzureOpenAI:
    """Returns Azure OpenAI async client.

    The httpx client is constructed with an explicit ``verify`` resolved by
    :func:`resolve_tls_verify`, so a corporate VPN that intercepts TLS with
    its own CA can be accommodated (set ``AZURE_OPENAI_CA_BUNDLE`` /
    ``REQUESTS_CA_BUNDLE`` to the corp CA ``.pem``), while a bogus
    ``SSL_CERT_FILE`` placeholder still falls back to certifi. ``trust_env``
    is left enabled so the VPN's ``HTTPS_PROXY`` / ``HTTP_PROXY`` /
    ``NO_PROXY`` settings are honored. By providing our own ``http_client``,
    we also bypass openai SDK's ``AsyncHttpxClientWrapper``, sidestepping its
    noisy ``__del__`` aclose-as-task behaviour on Python 3.13 + httpx 0.28.

    ``api_version`` overrides the REST API version; when omitted it falls back
    to ``AZURE_OPENAI_API_VERSION`` then ``DEFAULT_AZURE_OPENAI_API_VERSION``.
    Phase 1 callers pass nothing and keep the historical version; the dedup
    adjudicator passes a newer version that supports ``reasoning_effort``.
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_version = (
        api_version
        or os.getenv("AZURE_OPENAI_API_VERSION")
        or DEFAULT_AZURE_OPENAI_API_VERSION
    )
    if not api_key or not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set. "
            "Copy .env.example to .env and add your values, "
            "then restart the server."
        )
    # Connect timeout is generous because a VPN tunnel can add real latency
    # to the initial handshake; override via LLM_HTTP_CONNECT_TIMEOUT if needed.
    connect_timeout = float(os.getenv("LLM_HTTP_CONNECT_TIMEOUT", "30"))
    read_timeout = float(os.getenv("LLM_HTTP_TIMEOUT", "60"))
    http_client = httpx.AsyncClient(
        verify=resolve_tls_verify(),
        timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
        trust_env=True,
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
