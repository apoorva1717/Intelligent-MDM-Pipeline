"""Mock GLEIF/LEI client for testing and local development without API access.

Mirrors :class:`MockRORClient`: returns deterministic results from curated
data, matching the ``LEIClient.call(name, country_code)`` interface and the
``call_lei`` result-dict shape.
"""

from __future__ import annotations

import logging
from typing import Any

from config import Settings
from enrichment.tier1_lei import LEIClient, _name_match_score

logger = logging.getLogger(__name__)

# Curated mock data keyed by a lowercased name substring. Both "Pfizer AG"
# and "Pfizer" resolve to the SAME LEI (the whole point of the registry
# step for dedup convergence).
#
# `category` / `legal_form_id` mirror what the live GLEIF API actually returns
# and are what enrichment.classifier reads — an LEI on its own is not evidence
# of commercial status. Verified against api.gleif.org: every entity sampled,
# research and commercial alike, carries category "GENERAL", so the ISO 20275
# legal-form code is what discriminates. MVII = "Company limited by shares"
# (CH), 6QQB = "Aktiengesellschaft" (DE).
_MOCK_LEI: dict[str, dict[str, Any]] = {
    "pfizer": {
        "lei_id": "549300ZZDOU0WGVYS169",
        "legal_name": "PFIZER AG",
        "country": "CH",
        "status": "ACTIVE",
        "strategy": "exact",
        "confidence": "high",
        "score": 100.0,
        "category": "GENERAL",
        "sub_category": None,
        "legal_form_id": "MVII",
        "legal_form_other": None,
    },
    "novartis": {
        "lei_id": "549300I83CO4VENK1N73",
        "legal_name": "NOVARTIS AG",
        "country": "CH",
        "status": "ACTIVE",
        "strategy": "exact",
        "confidence": "high",
        "score": 100.0,
        "category": "GENERAL",
        "sub_category": None,
        "legal_form_id": "MVII",
        "legal_form_other": None,
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
        "category": "GENERAL",
        "sub_category": None,
        "legal_form_id": "6QQB",
        "legal_form_other": None,
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
        *,
        # Fix C(3) / D(2) record context. Accepted and unused: a mock
        # stands in for the registry, not for the client-side guards
        # that read these.
        city: str | None = None,
        state: str | None = None,
        record_domain: str | None = None,
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
            # Classification evidence, exactly as call_lei surfaces it.
            "category": mock.get("category"),
            "sub_category": mock.get("sub_category"),
            "legal_form_id": mock.get("legal_form_id"),
            "legal_form_other": mock.get("legal_form_other"),
        }
        logger.info(
            "[MOCK] LEI: '%s' → matched, LEI=%s, name='%s'",
            name[:40], result["lei_id"], result["legal_name"],
        )
        return result

    async def call_by_id(
        self,
        lei: str,
        query_name: str,
        country_code: str | None = None,
        *,
        # Fix C(3) / D(2) record context. Accepted and unused: a mock
        # stands in for the registry, not for the client-side guards
        # that read these.
        city: str | None = None,
        state: str | None = None,
        record_domain: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a known LEI, with the name-verification guard unchanged.

        The by-identifier path the [Wikidata crosswalk
        lane](`enrichment.wikidata`) follows. The pointer buys the lookup; the
        guard still decides. ``_name_match_score`` is the production scorer, so
        a pointer to a real LEI whose legal name is a different company's is
        refused here exactly as it would be live — which is what
        ``test_wikidata.py``'s LEI-crosswalk test asserts.
        """
        self.call_count += 1
        identifier = (lei or "").strip().upper()
        for data in _MOCK_LEI.values():
            if data.get("lei_id", "").upper() != identifier:
                continue
            score = _name_match_score(query_name or "", data["legal_name"])
            if score < float(self._threshold):
                logger.info(
                    "[MOCK] LEI by-id: %s → '%s' rejected by name guard "
                    "(%.1f < %.1f)",
                    identifier, data["legal_name"], score, self._threshold,
                )
                return {
                    "matched": False, "strategy": "by_id", "score": score,
                    "guard_rejections": [{
                        "guard": "gleif_name_verification",
                        "candidate_name": data["legal_name"],
                        "candidate_id": data["lei_id"],
                        "score": score,
                        "threshold": self._threshold,
                        "detail": (
                            "legal-name match score below the guard threshold"
                        ),
                        "query": query_name,
                    }],
                }
            return {
                "matched": True,
                "strategy": "by_id",
                "confidence": "high",
                "score": score,
                "guard_rejections": [],
                "lei_id": data["lei_id"],
                "legal_name": data["legal_name"],
                "country": data["country"],
                "status": data["status"],
                "category": data.get("category"),
                "sub_category": data.get("sub_category"),
                "legal_form_id": data.get("legal_form_id"),
                "legal_form_other": data.get("legal_form_other"),
            }
        logger.info("[MOCK] LEI by-id: %s not found", identifier)
        return {"matched": False, "strategy": "by_id", "score": 0.0}
