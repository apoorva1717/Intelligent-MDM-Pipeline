"""``POST /api/validate/vat`` — VAT ID validation for rows in the SAP export format.

Standalone: not called by ``/enrich``, ADF or DATAshaper, and nothing it
returns is read by enrichment, issues, dedup or scoring. Like every other
route it has no auth of its own; access is whatever the Function App applies.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import Settings, get_settings
from vat.validate import VAT_COLUMNS, validate_rows
from vat.vies_client import VIESClient

logger = logging.getLogger(__name__)

router = APIRouter()


class VatValidationRequest(BaseModel):
    """Rows exactly as exported: column header → cell value. The columns the
    validator reads are ``Customer``, ``Country/Region Key`` and ``VAT
    Registration No.``; every other column is passed through unchanged."""

    rows: list[dict[str, Any]] = Field(..., min_length=1)


class VatValidationResponse(BaseModel):
    #: Every input row, unchanged, with the VAT result columns appended.
    rows: list[dict[str, Any]]
    #: The appended columns, in order.
    columns: list[str] = Field(default_factory=lambda: list(VAT_COLUMNS))
    summary: dict[str, Any]


def _get_vies_client(settings: Settings | None = None) -> VIESClient:
    """Build the VIES client from config. Patched in tests."""
    return VIESClient(settings or get_settings())


@router.post("/api/validate/vat", response_model=VatValidationResponse)
async def validate_vat(request: VatValidationRequest) -> VatValidationResponse:
    """Normalise and format-check every row's VAT Registration No., ask VIES
    once per unique valid-format number, and fan the answer out to every row
    of the customer. A VIES outage is ``SERVICE_UNAVAILABLE``, never
    ``INVALID``."""
    try:
        client = _get_vies_client()
    except ValueError as exc:  # VIES_REQUESTER_VAT misconfigured
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("VAT validation request received: %d rows", len(request.rows))
    try:
        rows, summary = await validate_rows(request.rows, client)
    except ValueError as exc:  # no VAT column
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VatValidationResponse(rows=rows, summary=summary)
