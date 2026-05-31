"""Route definitions for the enrichment API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.models import (
    EnrichmentRequest,
    EnrichmentResponse,
    HealthResponse,
    TierConfigResponse,
)
from config import Settings, get_settings
from enrichment.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_orchestrator(settings: Settings | None = None) -> Orchestrator:
    """Build an Orchestrator with real or mock clients based on config."""
    if settings is None:
        settings = get_settings()

    mock_clients = None
    if settings.mock_external_calls:
        from tests.mocks.openai_mock import MockOpenAIClient
        from tests.mocks.page_mock import MockPageFetcher
        from tests.mocks.ror_mock import MockRORClient
        from tests.mocks.serp_mock import MockSearchClient

        mock_clients = {
            "ror": MockRORClient(settings),
            "search": MockSearchClient(),
            "page_fetcher": MockPageFetcher(),
            "llm": MockOpenAIClient(),
        }
        logger.info("Mock mode enabled — using mock clients")

    return Orchestrator(settings, mock_clients=mock_clients)


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for ADF and monitoring."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        env=settings.env,
        mock_mode=settings.mock_external_calls,
        tiers_available=[1, 2, 3],
    )


@router.post("/enrich", response_model=EnrichmentResponse)
async def enrich_records(request: EnrichmentRequest) -> EnrichmentResponse:
    """Main enrichment endpoint — processes a batch of customer master data records.

    Always returns HTTP 200 for valid requests.  Per-record errors are
    reported in each result's `error` field.  HTTP 422 for validation
    errors (Pydantic).  HTTP 400 only for empty records array (handled
    by Pydantic min_length=1).
    """
    settings = get_settings()
    orchestrator = _get_orchestrator(settings)

    logger.info(
        "Enrichment request received: %d records, concurrency=%d",
        len(request.records),
        request.options.max_concurrency,
    )

    response = await orchestrator.enrich_batch(request.records, request.options)
    return response


@router.get("/diag/llm")
async def diag_llm() -> dict:
    """Diagnostic: make one LLM call and return the raw outcome.

    Use this on Azure when you can't see logs — the actual exception
    string is returned in the HTTP response body.
    """
    import os

    env_snapshot = {
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT", "<unset>"),
        "AZURE_OPENAI_DEPLOYMENT": os.getenv("AZURE_OPENAI_DEPLOYMENT", "<unset>"),
        "AZURE_OPENAI_API_KEY_present": bool(os.getenv("AZURE_OPENAI_API_KEY")),
        "AZURE_OPENAI_API_KEY_length": len(os.getenv("AZURE_OPENAI_API_KEY", "")),
    }
    try:
        from llm.openai_client import call_openai
        raw = await call_openai(
            system_prompt="Return valid JSON only.",
            user_prompt='Return {"ok": true}',
            max_tokens=50,
        )
        return {"status": "ok", "raw": raw, "env": env_snapshot}
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "env": env_snapshot,
        }


@router.get("/tiers", response_model=TierConfigResponse)
async def get_tier_config() -> TierConfigResponse:
    """Return current tier thresholds and configuration."""
    settings = get_settings()
    # FIX(Bug 1): single ROR threshold for all record types
    return TierConfigResponse(
        ror_confidence_threshold=settings.ror_confidence_threshold,
        fuzzy_match_threshold=settings.fuzzy_match_threshold,
        max_page_content_chars=settings.max_page_content_chars,
        page_fetch_timeout_seconds=settings.page_fetch_timeout_seconds,
        default_max_concurrency=settings.default_max_concurrency,
        serp_provider="serpapi" if settings.serpapi_key else "duckduckgo",
        mock_mode=settings.mock_external_calls,
    )
