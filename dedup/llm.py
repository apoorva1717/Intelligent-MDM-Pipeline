"""LLM call wrapper for the dedup adjudicator.

Reuses the Phase 1 AI Foundry client construction (``get_openai_client`` in
``llm.openai_client``) — it does NOT build a new client. The only differences
from the Phase 1 tier calls are dedup-specific: a separate deployment
(``AOAI_DEPLOYMENT_DEDUP``), ``reasoning_effort`` instead of temperature
(reasoning models may ignore temperature), bounded retries on 429/5xx, and
per-call token/latency capture for telemetry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from llm.openai_client import _FENCE_RE, get_openai_client

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
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    mentions_param = "reasoning_effort" in text or "reasoning effort" in text
    looks_like_bad_arg = any(
        phrase in text
        for phrase in ("unrecognized", "unsupported", "unknown", "not supported", "extra inputs")
    )
    if mentions_param and (looks_like_bad_arg or status == 400):
        return True
    # Some SDKs raise TypeError for an unexpected kwarg before any HTTP call.
    return isinstance(exc, TypeError) and mentions_param


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

    def __init__(self, settings: Any = None) -> None:
        self._deployment = os.getenv("AOAI_DEPLOYMENT_DEDUP", "gpt-5.4")
        self._reasoning_effort = os.getenv("DEDUP_REASONING_EFFORT", "low")
        self._max_retries = int(os.getenv("DEDUP_MAX_RETRIES", "3"))
        self._api_version = (
            os.getenv("AOAI_API_VERSION_DEDUP")
            or os.getenv("AZURE_OPENAI_API_VERSION")
            or self.DEFAULT_API_VERSION
        )
        # Disabled at runtime if the deployment rejects the parameter, so a
        # single bad-request doesn't sink every block.
        self._use_reasoning_effort = bool(self._reasoning_effort)
        self._client: Any = None
        logger.info(
            "Dedup LLM initialised (deployment=%s, api_version=%s, reasoning_effort=%s)",
            self._deployment, self._api_version, self._reasoning_effort,
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
            if self._use_reasoning_effort:
                params["reasoning_effort"] = self._reasoning_effort
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
