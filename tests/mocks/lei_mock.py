"""Mock GLEIF/LEI client for testing and local development without API access.

Mirrors :class:`MockRORClient`: returns deterministic results from curated
data, matching the ``LEIClient.call(name, country_code)`` interface and the
``call_lei`` result-dict shape.
"""

from __future__ import annotations

import logging
from typing import Any

from config import Settings
from enrichment.tier1_lei import LEIClient

logger = logging.getLogger(__name__)

# Curated mock data keyed by a lowercased name substring. Both "Pfizer AG"
# and "Pfizer" resolve to the SAME LEI (the whole point of the registry
# step for dedup convergence).
_MOCK_LEI: dict[str, dict[str, Any]] = {
    "pfizer": {
        "lei_id": "549300ZZDOU0WGVYS169",
        "legal_name": "PFIZER AG",
        "country": "CH",
        "status": "ACTIVE",
        "strategy": "exact",
        "confidence": "high",
        "score": 100.0,
    },
    "novartis": {
        "lei_id": "549300I83CO4VENK1N73",
        "legal_name": "NOVARTIS AG",
        "country": "CH",
        "status": "ACTIVE",
        "strategy": "exact",
        "confidence": "high",
        "score": 100.0,
    },
    # Keyed by "bayer" (not "bayr"): the typo'd raw name "Bayr AG" misses
    # here, mirroring real GLEIF, and only the LLM-corrected "Bayer AG"
    # resolves — the typo-recovery re-verify path.
    "bayer": {
        "lei_id": "3157002JBAOA57BQAT84",
        "legal_name": "BAYER AG",
        "country": "DE",
        "status": "ACTIVE",
        "strategy": "exact",
        "confidence": "high",
        "score": 100.0,
    },
    # A name that GLEIF returns a statistically-close but WRONG entity for —
    # the verification guard rejects it, so the client reports a miss.
    "closebrand": {"__reject__": True},
    # A name that triggers a GLEIF API error (timeout / 5xx) — the client
    # reports an error and the orchestrator falls through unharmed.
    "errorco": {"__error__": True},
}


class MockLEIClient(LEIClient):
    """Mock GLEIF/LEI client returning deterministic curated results."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.call_count = 0
        self.last_name: str | None = None

    async def call(
        self,
        name: str,
        country_code: str | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.last_name = name
        name_lower = (name or "").strip().lower()

        mock = _MOCK_LEI.get(name_lower)
        if mock is None:
            for key, data in _MOCK_LEI.items():
                if key in name_lower:
                    mock = data
                    break

        if mock is None:
            logger.info("[MOCK] LEI: no match for '%s'", name[:80])
            return {"matched": False, "strategy": "fuzzy", "score": 0.0}

        if mock.get("__error__"):
            logger.info("[MOCK] LEI: simulated API error for '%s'", name[:80])
            return {"matched": False, "error": True}

        if mock.get("__reject__"):
            logger.info(
                "[MOCK] LEI: candidate rejected by verification guard for '%s'",
                name[:80],
            )
            return {"matched": False, "strategy": "exact", "score": 50.0}

        result = {
            "matched": True,
            "strategy": mock["strategy"],
            "confidence": mock["confidence"],
            "score": mock["score"],
            "lei_id": mock["lei_id"],
            "legal_name": mock["legal_name"],
            "country": mock["country"],
            "status": mock["status"],
        }
        logger.info(
            "[MOCK] LEI: '%s' → matched, LEI=%s, name='%s'",
            name[:40], result["lei_id"], result["legal_name"],
        )
        return result
