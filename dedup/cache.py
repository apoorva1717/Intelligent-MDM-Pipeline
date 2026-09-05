"""A record/replay cache for adjudicator calls (opt-in, off by default).

Phase 2 had no cache, no fixture layer and no replay mode: every
``/api/dedup/file`` run made fresh model calls
(docs/13_CLUSTERING_DOSSIER.md §6.1). That makes a real-model measurement
unrepeatable — a number in a report that nobody, including its author, can
reproduce next week.

This is the missing layer. It is a plain content-addressed store: the key is a
digest of everything that determines the answer (deployment, both prompts, the
token budget and the sampling parameters actually in force), and the value is
the model's verbatim response. Recording once and replaying thereafter makes a
run reproducible without pretending the model is deterministic — the recording
is evidence of what it said, not a claim about what it would say again.

Off unless ``DEDUP_FIXTURE_CACHE_DIR`` is set, so nothing about the shipped
path changes. Two modes:

``record`` (default)
    Serve a hit; on a miss call the model and write the answer down.
``replay``
    Serve a hit; on a miss REFUSE, loudly. This is the mode a re-run of a
    committed measurement uses: a miss means the prompt changed, and silently
    calling the model would quietly re-measure something else while reporting
    it under the old run's name.

Errored calls are never cached. A 429 or a socket timeout is a fact about the
afternoon, not about the question.

Blocks run concurrently, so two of them asking an identical question can both
miss and both write the same key — billed twice, recorded once. Left unlocked
deliberately: the write is idempotent, and a lock would serialise every call in
the run to save one duplicate in thirty-eight.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from dedup.llm import DedupLLMResult

logger = logging.getLogger(__name__)

CACHE_DIR_ENV = "DEDUP_FIXTURE_CACHE_DIR"
CACHE_MODE_ENV = "DEDUP_FIXTURE_CACHE_MODE"


class ReplayMiss(RuntimeError):
    """A replay-mode run asked something the recording does not answer."""


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachedDedupLLM:
    """Wraps a ``DedupLLM`` (or any object with the same ``adjudicate``).

    Deliberately a wrapper rather than a branch inside ``DedupLLM``: the
    adjudicator should not be able to tell whether it is talking to a model or
    to a recording, and the shipped client should carry no cache code at all.
    """

    def __init__(self, inner: Any, cache_dir: str | Path, mode: str = "record") -> None:
        self._inner = inner
        self._dir = Path(cache_dir)
        self._mode = mode
        self.hits = 0
        self.misses = 0
        if mode not in ("record", "replay"):
            raise ValueError(f"unknown cache mode {mode!r}; expected record or replay")
        if mode == "record":
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def model(self) -> str:
        return self._inner.model

    def _key(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        inner = self._inner
        # Everything that can change the answer. The sampling flags are read
        # off the client because it disables them one at a time when the
        # deployment rejects one (dedup/llm.py:255-280) — a recording made
        # with seed suppressed answers a different question from one made with
        # it sent, and the key has to say so.
        return _digest({
            "deployment": getattr(inner, "_deployment", None) or inner.model,
            "system": system_prompt,
            "user": user_prompt,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": (
                getattr(inner, "_reasoning_effort", None)
                if getattr(inner, "_use_reasoning_effort", False) else None
            ),
            "temperature": (
                getattr(inner, "TEMPERATURE", None)
                if getattr(inner, "_use_temperature", False)
                and not getattr(inner, "_use_reasoning_effort", False)
                else None
            ),
            "seed": 42 if getattr(inner, "_use_seed", False) else None,
        })

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    async def adjudicate(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int = 4000
    ) -> DedupLLMResult:
        key = self._key(system_prompt, user_prompt, max_tokens)
        path = self._path(key)

        if path.exists():
            self.hits += 1
            record = json.loads(path.read_text(encoding="utf-8"))
            return DedupLLMResult(
                raw=record["raw"],
                prompt_tokens=record.get("prompt_tokens", 0),
                completion_tokens=record.get("completion_tokens", 0),
                latency_ms=record.get("latency_ms", 0),
                model_version=record.get("model_version", ""),
                error=None,
            )

        self.misses += 1
        if self._mode == "replay":
            raise ReplayMiss(
                f"no recording for this call (key {key[:12]}). The prompt or a "
                f"sampling parameter changed since the recording was made — "
                f"re-record with DEDUP_FIXTURE_CACHE_MODE=record rather than "
                f"reporting the new answers under the old run's name.\\n"
                f"--- system ---\\n{system_prompt[:400]}\\n"
                f"--- user ---\\n{user_prompt[:800]}"
            )

        result = await self._inner.adjudicate(
            system_prompt, user_prompt, max_tokens=max_tokens
        )
        if result.error is None and result.raw:
            # The prompts are stored beside the answer. They cost space and
            # earn it: without them a cache entry is an unreadable hash, and
            # nobody can review what the model was actually asked.
            path.write_text(
                json.dumps({
                    "raw": result.raw,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "latency_ms": result.latency_ms,
                    "model_version": result.model_version,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "max_completion_tokens": max_tokens,
                }, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return result

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()


def wrap_if_enabled(llm: Any) -> Any:
    """``llm``, wrapped in the cache when one is configured; else unchanged."""
    cache_dir = os.getenv(CACHE_DIR_ENV, "").strip()
    if not cache_dir:
        return llm
    mode = os.getenv(CACHE_MODE_ENV, "record").strip().lower() or "record"
    logger.info("Dedup fixture cache %s at %s", mode, cache_dir)
    return CachedDedupLLM(llm, cache_dir, mode=mode)
