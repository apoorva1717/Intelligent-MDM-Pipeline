"""Mock dedup adjudicator LLM for offline runs (MOCK_EXTERNAL_CALLS=true).

Returns conservative, deterministic decisions: it never merges across distinct
signatures. In Mode A every signature becomes its own entity; in Mode B every
candidate is a "new" entity. This keeps the endpoint runnable offline without
inventing merges — real merge decisions require the live model.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from dedup.llm import DedupLLMResult

logger = logging.getLogger(__name__)

_SIG_ID_RE = re.compile(r'"signature_id"\s*:\s*"([^"]+)"')


class MockDedupLLM:
    """Duck-typed stand-in for ``dedup.llm.DedupLLM``."""

    model = "mock-dedup"

    async def adjudicate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 4000,
    ) -> DedupLLMResult:
        sig_ids = _SIG_ID_RE.findall(user_prompt)

        if "Decide whether the candidate" in user_prompt:
            # Mode B — never match, always start a new entity.
            payload = {
                "decision": "new",
                "matched_entity_id": None,
                "confidence": 0.5,
                "reasoning": "Mock: conservative no-merge default.",
            }
        else:
            # Mode A — each signature is its own entity (no merges).
            entities = [
                {
                    "signature_ids": [sid],
                    "institution": "mock",
                    "department": "",
                    "confidence": 0.5,
                    "reasoning": "Mock: conservative no-merge default.",
                }
                for sid in sig_ids
            ]
            payload = {"entities": entities, "uncertain_signature_ids": []}

        return DedupLLMResult(
            raw=json.dumps(payload),
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            model_version="mock-dedup",
            error=None,
        )

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None
