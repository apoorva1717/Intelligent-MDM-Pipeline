"""Async OpenAI client for LLM calls.

FIX(Bug 6): replaced Azure OpenAI with direct OpenAI client for local testing.
For production at Bruker, replace AsyncOpenAI with AsyncAzureOpenAI and add
azure_endpoint and api_version. The rest of the code stays identical.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def get_openai_client() -> AsyncOpenAI:
    """Returns direct OpenAI async client for local testing.

    For production at Bruker, replace with:
        from openai import AsyncAzureOpenAI
        return AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version="2024-08-01-preview"
        )
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. "
            "Copy .env.example to .env and add your key, "
            "then restart the server."
        )
    return AsyncOpenAI(api_key=api_key)


async def call_openai(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 500,
) -> str:
    """Call OpenAI with structured JSON output enforced.

    Raises RuntimeError on failure so orchestrator can log and escalate
    to next tier cleanly.
    """
    client = get_openai_client()
    try:
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"OpenAI call failed: {str(e)}") from e


class OpenAIClient:
    """Thin async wrapper that returns parsed JSON dicts.

    Provides the extract_json() interface used by tier2a, tier2b, tier3 modules.
    Uses the standalone call_openai() function under the hood.
    """

    def __init__(self, settings: Any = None) -> None:
        self._model = os.getenv("OPENAI_MODEL", "gpt-4o")
        if settings and hasattr(settings, "openai_model"):
            self._model = settings.openai_model
        logger.info("LLM client initialised with OpenAI (model=%s)", self._model)

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
        for attempt in range(2):
            raw = await call_openai(system_prompt, user_prompt, max_tokens=max_tokens)
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
