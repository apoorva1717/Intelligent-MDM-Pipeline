"""LLM call wrapper for the dedup adjudicator.

Reuses the Phase 1 AI Foundry client construction (``get_openai_client`` in
``llm.openai_client``) — it does NOT build a new client. The only differences
from the Phase 1 tier calls are dedup-specific: a separate deployment
(``AOAI_DEPLOYMENT_DEDUP``), ``reasoning_effort``, bounded retries on 429/5xx,
and per-call token/latency capture for telemetry.

``temperature=0.0`` is sent when ``reasoning_effort`` is not in play, matching
the Phase 1 enrichment path so both phases make the same reproducibility claim
wherever the deployment permits it. On a reasoning deployment the two are
mutually exclusive — gpt-5.4 rejects any temperature but its default while
``reasoning_effort`` is set — so on such a deployment reasoning_effort wins and
temperature is not sent. A deployment that rejects temperature for any other
reason falls back to omitting it, exactly as ``reasoning_effort`` does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from llm.openai_client import (
    _FENCE_RE,
    LLM_SEED,
    LLM_TOP_P,
    _is_unsupported_param,
    get_openai_client,
)

logger = logging.getLogger(__name__)

# openai SDK exception types, imported defensively so a version skew can't
# break module import. Used only to decide whether a failure is retryable.
try:  # pragma: no cover - import guard
    from openai import APIConnectionError, APITimeoutError
except Exception:  # noqa: BLE001
    APIConnectionError = APITimeoutError = ()  # type: ignore[assignment]


def _is_unsupported_reasoning_effort(exc: Exception) -> bool:
    """True when an error looks like the deployment/API rejecting the
    ``reasoning_effort`` parameter (a 400 about an unknown/unsupported arg)."""
    return _is_unsupported_param(exc, "reasoning_effort", "reasoning effort")


def _is_unsupported_temperature(exc: Exception) -> bool:
    """True when an error looks like the deployment/API rejecting the
    ``temperature`` parameter.

    Reasoning deployments come in two flavours: those that reject the key
    outright, and those that accept it but only at the default value and
    return "does not support 0.0 ... only the default (1) is supported".
    Both are handled the same way — drop it and retry.
    """
    return _is_unsupported_param(exc, "temperature")


def _is_unsupported_seed(exc: Exception) -> bool:
    """True when an error looks like the deployment/API rejecting ``seed``."""
    return _is_unsupported_param(exc, "seed")


def _is_retryable(exc: Exception) -> bool:
    """Retry only transient failures: connection/timeout, 429, and 5xx."""
    if APIConnectionError and isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    try:
        code = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return code == 429 or 500 <= code < 600


@dataclass
class DedupLLMResult:
    """Outcome of one LLM call, with telemetry."""

    raw: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model_version: str = ""
    error: Optional[str] = None


def parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    """Defensively parse a model response as a JSON object.

    Handles plain JSON, fenced ```json blocks, and surrounding prose.
    Returns ``None`` when the text cannot be read as a JSON object — callers
    treat that as "uncertain" rather than failing the block.
    """
    if not raw:
        return None
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Last resort: grab the outermost {...} span and retry.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            return None
    return obj if isinstance(obj, dict) else None


class DedupLLM:
    """Adjudicator LLM client. Lazily reuses one ``AsyncAzureOpenAI`` instance
    (built by the shared ``get_openai_client``) for its lifetime."""

    # Default REST API version for the adjudicator. GPT-5.x reasoning models
    # and the ``reasoning_effort`` parameter require a newer version than the
    # Phase 1 default; override with AOAI_API_VERSION_DEDUP if your resource
    # exposes a different one.
    DEFAULT_API_VERSION = "2025-04-01-preview"

    # Sampling temperature for adjudication. Fixed at 0.0 rather than made
    # configurable: this is a reproducibility control, and an env knob would
    # let it drift silently between runs. Matches the Phase 1 enrichment path,
    # which hardcodes temperature=0.0 in ``llm.openai_client.call_openai``.
    TEMPERATURE = 0.0

    def __init__(self, settings: Any = None) -> None:
        # Prefer a dedup-specific deployment; otherwise reuse the Phase 1
        # deployment so a single configured deployment works for both phases.
        self._deployment = (
            os.getenv("AOAI_DEPLOYMENT_DEDUP")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT")
            or "gpt-5.4"
        )
        self._reasoning_effort = os.getenv("DEDUP_REASONING_EFFORT", "low")
        self._max_retries = int(os.getenv("DEDUP_MAX_RETRIES", "3"))
        self._api_version = (
            os.getenv("AOAI_API_VERSION_DEDUP")
            or os.getenv("AZURE_OPENAI_API_VERSION")
            or self.DEFAULT_API_VERSION
        )
        # Both disabled at runtime if the deployment rejects the parameter, so
        # a single bad-request doesn't sink every block.
        self._use_reasoning_effort = bool(self._reasoning_effort)
        self._use_temperature = True
        # Fix A — same one-shot fallback shape as the two above.
        self._use_seed = True
        self._client: Any = None
        logger.info(
            "Dedup LLM initialised (deployment=%s, api_version=%s, "
            "reasoning_effort=%s, temperature=%s)",
            self._deployment, self._api_version, self._reasoning_effort,
            # Report what will actually be sent, not the constant: while
            # reasoning_effort is active, temperature is suppressed.
            "not sent (reasoning_effort active)"
            if self._use_reasoning_effort else self.TEMPERATURE,
        )

    @property
    def model(self) -> str:
        return self._deployment

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = get_openai_client(api_version=self._api_version)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying client cleanly. Safe to call repeatedly."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def adjudicate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 4000,
    ) -> DedupLLMResult:
        """Make one adjudication call with bounded exponential-backoff retries.

        Never raises: on exhausted retries it returns a result with ``error``
        set so the caller can mark the affected signatures uncertain and
        continue (one bad call never fails a whole block).
        """
        client = self._get_client()
        last_error: Optional[str] = None

        for attempt in range(self._max_retries):
            start = time.perf_counter()
            params: dict[str, Any] = {
                "model": self._deployment,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
            # Fix A — the adjudication verdict gates a merge, so the call is
            # pinned exactly as the Phase 1 tiers are. `top_p` and `seed` are
            # orthogonal to `reasoning_effort` (unlike `temperature`, which the
            # reasoning deployments refuse alongside it), so both are sent on
            # every path; `seed` drops out at runtime if the deployment
            # rejects it, the same one-shot fallback the other two params get.
            params["top_p"] = LLM_TOP_P
            if self._use_seed:
                params["seed"] = LLM_SEED
            if self._use_reasoning_effort:
                params["reasoning_effort"] = self._reasoning_effort
            # Temperature and reasoning_effort are mutually exclusive on
            # reasoning deployments: gpt-5.4 answers a request carrying both
            # with "temperature does not support 0.0 ... only the default (1)
            # is supported". Sending it anyway would cost a guaranteed 400 and
            # a retry on the first call of every request, since the client is
            # built per request. So it is sent only when reasoning_effort is
            # not in play — including when it was disabled at runtime by the
            # fallback above, which is the case where it can actually apply.
            elif self._use_temperature:
                params["temperature"] = self.TEMPERATURE
            try:
                response = await client.chat.completions.create(**params)
                latency_ms = int((time.perf_counter() - start) * 1000)
                usage = getattr(response, "usage", None)
                return DedupLLMResult(
                    raw=response.choices[0].message.content or "",
                    prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                    latency_ms=latency_ms,
                    model_version=getattr(response, "model", None) or self._deployment,
                    error=None,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                # If the deployment/API version rejects reasoning_effort, drop
                # it and retry immediately rather than failing the block. The
                # parameter is a tuning preference, not a correctness gate.
                if self._use_reasoning_effort and _is_unsupported_reasoning_effort(exc):
                    logger.warning(
                        "Dedup LLM: deployment rejected reasoning_effort; "
                        "disabling it and retrying: %s", exc,
                    )
                    self._use_reasoning_effort = False
                    continue
                # Same fallback for temperature: a deployment that rejects it
                # (or only accepts its default) still adjudicates correctly
                # without it. Determinism is the goal, not a precondition.
                if self._use_temperature and _is_unsupported_temperature(exc):
                    logger.warning(
                        "Dedup LLM: deployment rejected temperature; "
                        "disabling it and retrying: %s", exc,
                    )
                    self._use_temperature = False
                    continue
                # And for `seed`. Caught once; the parameter is a
                # reproducibility aid, never a correctness gate.
                if self._use_seed and _is_unsupported_seed(exc):
                    logger.warning(
                        "Dedup LLM: deployment rejected seed; disabling it "
                        "and retrying: %s", exc,
                    )
                    self._use_seed = False
                    continue
                if _is_retryable(exc) and attempt < self._max_retries - 1:
                    delay = 0.5 * (2 ** attempt)
                    logger.warning(
                        "Dedup LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, self._max_retries, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Dedup LLM call failed (no more retries): %s", exc)
                break

        return DedupLLMResult(error=last_error or "LLM call failed")
