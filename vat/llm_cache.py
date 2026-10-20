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
  format (unchanged, reused as is);
* ``utils.cache.llm_disk_key`` — every parameter that can change the answer;
* ``CACHE_FROZEN`` — a miss raises ``LLMUnavailableFrozen`` and makes no call.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Awaitable, Callable

from llm import openai_client
from llm.openai_client import (
    DEFAULT_AZURE_OPENAI_API_VERSION,
    LLM_SEED,
    LLM_TEMPERATURE,
    LLM_TOP_P,
    LLMUnavailableFrozen,
    _FENCE_RE,
    call_openai,
    get_openai_client,
)
from utils.cache import DiskCache, llm_disk_key
from vat.cache_guard import WriteFailures

logger = logging.getLogger(__name__)

#: ``complete(system_prompt, user_prompt, max_tokens) -> raw text``.
Complete = Callable[[str, str, int], Awaitable[str]]


class VatLLM:
    """JSON completions through *store*. ``complete`` is injectable for tests."""

    def __init__(
        self, store: DiskCache, *, complete: Complete | None = None,
        write_failures: WriteFailures | None = None,
    ) -> None:
        self._store = store
        self.write_failures = write_failures or WriteFailures()
        self._complete = complete
        self._client = None
        #: Completions actually requested from the model (not cache replays).
        self.network_calls = 0
        self.cache_hits = 0
        #: The date the last answer returned was recorded (UTC ISO date): the
        #: original recording's on a replay, so it is stable across re-runs.
        self.last_fetched_at: str | None = None

    async def _default_complete(self, system: str, user: str, max_tokens: int) -> str:
        if self._client is None:
            self._client = get_openai_client()
        return await call_openai(
            system, user, max_tokens=max_tokens, client=self._client,
            temperature=LLM_TEMPERATURE,
        )

    def _key(self, system: str, user: str, max_tokens: int) -> str:
        return llm_disk_key(
            deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
            api_version=(
                os.getenv("AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_OPENAI_API_VERSION
            ),
            temperature=LLM_TEMPERATURE,
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
        key = self._key(system, user, max_tokens)
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
            self.write_failures.set_checked(self._store, key, {
                "response": parsed,
                "system_head": system[:160],
                "user_head": user[:400],
            })
            self.last_fetched_at = self._store.fetched_at(key)
            return parsed
        raise ValueError("LLM JSON extraction failed after retries")  # pragma: no cover

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
