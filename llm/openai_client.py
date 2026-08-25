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
import ssl
from typing import Any

import certifi
import httpx
from openai import AsyncAzureOpenAI

from utils.cache import active_evidence_cache, llm_disk_key, note_network_call

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


# ---------------------------------------------------------------------------
# Fix A — determinism controls
# ---------------------------------------------------------------------------
#
# Every Phase 1 LLM call gates a decision or writes a field, so every one of
# them is pinned to the same three sampling parameters. They are module
# constants rather than env knobs on purpose: a reproducibility control that
# can be changed per environment is not a control. (`dedup/llm.py` makes the
# same argument for its own `TEMPERATURE`.)
#
# `temperature=0` alone is NOT determinism. It makes the sampler pick the
# arg-max token, but a tie between two equally-likely tokens is still broken
# by the server, and MoE routing/batching makes the logits themselves vary
# slightly between requests. Two runs of the identical chemspeed batch flipped
# three records' `confidence` self-reports between `self_high` and
# `self_medium` on exactly that. `seed` is what asks the service for a
# reproducible sampling path; `top_p=1` removes nucleus truncation as a second
# source of variation.
LLM_TEMPERATURE: float = 0.0
LLM_TOP_P: float = 1.0
#: Fixed request seed. The VALUE is arbitrary — only its fixity matters, and
#: it is recorded in `docs/thesis/04_PARAMETERS.md` so a re-run of a thesis
#: batch can be reproduced from the parameter table alone. Never derived from
#: the clock, the run id or the record.
LLM_SEED: int = 42

#: Set to False the first (and only) time the deployment rejects ``seed``.
#: Process-global and one-shot: the prompt for this fix is explicit that a
#: rejection is caught once and then the parameter is simply not sent again —
#: a per-call probe would pay a guaranteed 400 on every record.
_SEED_SUPPORTED: bool = True


def seed_supported() -> bool:
    """Whether ``seed`` is still being sent (diagnostics / tests)."""
    return _SEED_SUPPORTED


def reset_seed_support() -> None:
    """Re-enable ``seed`` (tests only — the runtime never re-probes)."""
    global _SEED_SUPPORTED
    _SEED_SUPPORTED = True


def _is_unsupported_param(exc: Exception, *names: str) -> bool:
    """True when an error looks like the deployment/API rejecting one of
    ``names`` (a 400 about an unknown/unsupported/out-of-range arg).

    Lives here rather than in ``dedup/llm.py`` (where it was written) because
    both phases talk to the same Azure resource: a parameter either exists on
    that deployment or does not, and two copies of the phrase list would drift.
    ``dedup.llm`` imports it from here.
    """
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    mentions_param = any(n in text for n in names)
    looks_like_bad_arg = any(
        phrase in text
        for phrase in (
            "unrecognized", "unsupported", "unknown", "not supported",
            "extra inputs",
            # Reasoning deployments that accept the argument but only at its
            # default phrase it as an unsupported *value*, not an unknown key.
            "does not support", "only the default",
        )
    )
    if mentions_param and (looks_like_bad_arg or status == 400):
        return True
    # Some SDKs raise TypeError for an unexpected kwarg before any HTTP call.
    return isinstance(exc, TypeError) and mentions_param


def _is_unsupported_seed(exc: Exception) -> bool:
    """True when an error reads as the deployment/API rejecting ``seed``."""
    return _is_unsupported_param(exc, "seed")

# CA-bundle env vars consulted (in order) for TLS verification, before
# falling back to certifi. A dedicated var comes first so the LLM client
# can be pointed at a corp CA without disturbing other tooling.
_CA_BUNDLE_ENV_VARS = ("AZURE_OPENAI_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def _env_bool(name: str, default: bool = True) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


#: Built TLS contexts, keyed by what was resolved. Building one from a corp CA
#: bundle costs tens of milliseconds — trivial per LLM call, and NOT trivial
#: once the registry clients construct a fresh `httpx.AsyncClient` for every
#: lookup: on a fully warm 100-record batch, where every registry response is
#: served from the evidence cache and no request is made at all, rebuilding the
#: context per lookup was the entire runtime (measured: 124 s, against 1.7 s
#: once cached). It is also what `verify=<path>` does internally on every
#: client, so caching it changes nothing about what is trusted.
_TLS_CONTEXT_CACHE: dict[str, "ssl.SSLContext | bool"] = {}


def resolve_tls_verify() -> "ssl.SSLContext | bool":
    """Resolve the httpx ``verify`` setting for outbound calls.

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
        if "off" not in _TLS_CONTEXT_CACHE:
            logger.warning(
                "LLM_SSL_VERIFY=false — TLS certificate verification is "
                "DISABLED for LLM calls. This is insecure; only use it when "
                "the corporate CA cannot be installed."
            )
            _TLS_CONTEXT_CACHE["off"] = False
        return False

    bundle = certifi.where()
    source = "certifi"
    for var in _CA_BUNDLE_ENV_VARS:
        path = os.getenv(var)
        if path and os.path.isfile(path):
            bundle, source = path, var
            break

    cached = _TLS_CONTEXT_CACHE.get(bundle)
    if cached is not None:
        return cached
    if source != "certifi":
        logger.info(
            "Using CA bundle from %s for TLS verification: %s", source, bundle,
        )
    context = ssl.create_default_context(cafile=bundle)
    _TLS_CONTEXT_CACHE[bundle] = context
    return context


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
    *,
    temperature: float = LLM_TEMPERATURE,
) -> str:
    """Call OpenAI with structured JSON output enforced.

    Pass ``client`` to reuse a long-lived ``AsyncAzureOpenAI`` instance. When
    omitted, a one-shot client is created (kept for the diagnostic
    scripts in ``llm/test_connection.py`` and ``scripts/verify_fixes``;
    the hot orchestrator path always supplies its cached client via
    ``OpenAIClient``).

    Fix A: the request always carries ``temperature`` (0 by default),
    ``top_p=1`` and a fixed ``seed``. If the deployment rejects ``seed`` the
    parameter is dropped **once, process-wide**, logged, and the call is
    retried without it — every later call goes straight out without it.

    Raises RuntimeError on failure so the orchestrator can log and
    escalate to the next tier cleanly.
    """
    global _SEED_SUPPORTED
    own_client = client is None
    if own_client:
        client = get_openai_client()

    def _params(with_seed: bool) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "top_p": LLM_TOP_P,
            "response_format": {"type": "json_object"},
        }
        if with_seed:
            params["seed"] = LLM_SEED
        return params

    try:
        try:
            response = await client.chat.completions.create(
                **_params(_SEED_SUPPORTED)
            )
        except Exception as exc:
            if not (_SEED_SUPPORTED and _is_unsupported_seed(exc)):
                raise
            # Caught once, for the life of the process. The seed is a
            # reproducibility aid, not a correctness gate: without it the
            # calls are still temperature-0, and the run is still far more
            # stable than it was — it just cannot claim seeded reproduction.
            _SEED_SUPPORTED = False
            logger.warning(
                "Azure OpenAI deployment rejected `seed` (%s). Disabling it "
                "for this process and proceeding with temperature=%s / "
                "top_p=%s only. Byte-identical re-runs are no longer "
                "guaranteed by the service.",
                exc, temperature, LLM_TOP_P,
            )
            response = await client.chat.completions.create(**_params(False))
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


class LLMUnavailableFrozen(RuntimeError):
    """A frozen run wanted a completion it has no recording of.

    Raised instead of calling the model, so the tier sees the failure it
    already knows how to handle — every caller of :meth:`OpenAIClient.
    extract_json` treats an exception as "the model did not answer" and
    proceeds without it. The miss is traced as ``evidence-unavailable-frozen``
    by the store, exactly like a missing page read.
    """


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
        temperature: float = LLM_TEMPERATURE,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Send a chat completion request and parse the response as JSON.

        Retries once if the first response is not valid JSON.
        Raises ValueError if JSON cannot be extracted after retry.

        ``temperature`` is now actually forwarded. It was accepted and then
        silently dropped, so every caller that passed one was configuring
        nothing — the value the service saw came from ``call_openai``'s own
        hardcoded default. Defaults to :data:`LLM_TEMPERATURE`, so no caller's
        behaviour changes.
        """
        # Fix B, extended to the model. An LLM answer is evidence the pipeline
        # reads, exactly as a page read or a SERP result is — and the service
        # does not guarantee reproducibility even at temperature 0 with a
        # fixed seed (`seed` is documented as best-effort, and this deployment
        # returns no `system_fingerprint` to detect a backend change with).
        # Measured: with the seed accepted and sent, two warm runs of the
        # chemspeed batch still differed on 10 of 100 rows, every one of them
        # an LLM decision. So the answer is recorded under a digest of
        # everything that could change it, and a re-run reads what the first
        # run was told.
        store = None
        cache = active_evidence_cache()
        if cache is not None:
            store = cache.namespace("llm")
        key = None
        if store is not None:
            key = llm_disk_key(
                deployment=self._model,
                api_version=(
                    os.getenv("AZURE_OPENAI_API_VERSION")
                    or DEFAULT_AZURE_OPENAI_API_VERSION
                ),
                temperature=temperature,
                top_p=LLM_TOP_P,
                seed=LLM_SEED if _SEED_SUPPORTED else None,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            recorded = store.get(key)
            if isinstance(recorded, dict) and "response" in recorded:
                return recorded["response"]
            if store.replay_only:
                raise LLMUnavailableFrozen(
                    "no recorded completion for this prompt"
                )

        client = self._get_client()
        for attempt in range(2):
            note_network_call("llm")
            raw = await call_openai(
                system_prompt, user_prompt,
                max_tokens=max_tokens, client=client,
                temperature=temperature,
            )
            if raw is None:
                raw = ""

            fence_match = _FENCE_RE.search(raw)
            if fence_match:
                raw = fence_match.group(1)

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("LLM returned invalid JSON, retrying (attempt 1)")
                    continue
                logger.error("LLM returned invalid JSON after retry: %s", raw[:200])
                raise ValueError(f"LLM returned invalid JSON: {raw[:200]}")

            if store is not None and key is not None:
                # The prompts themselves are NOT stored — they run to thousands
                # of characters and the key already identifies them exactly.
                # The two heads are there so a human opening the fixture
                # directory can tell what a file is about.
                store.set(key, {
                    "response": parsed,
                    "system_head": system_prompt[:160],
                    "user_head": user_prompt[:400],
                })
            return parsed

        raise ValueError("LLM JSON extraction failed after retries")  # pragma: no cover
