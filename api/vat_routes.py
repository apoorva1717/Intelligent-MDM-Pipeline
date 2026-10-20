"""VAT ID validation for rows in the SAP export format.

* ``POST /api/validate/vat`` — JSON rows in, JSON rows out.
* ``POST /api/validate/vat/file`` — an XLSX upload in, the same workbook out
  with the ``VAT_*`` columns and a ``Summary`` sheet (``vat/xlsx.py``, the same
  code ``tools/vat_validate_xlsx.py`` runs).
* ``POST /api/validate/vat/llm-only-experiment`` — the JSON endpoint with
  ``lookup_missing=llm_only``, so both lookup modes are easy to call side by
  side. **Experiment only**: never a production path, never written to SAP.

``lookup_missing`` (``none`` | ``grounded`` | ``llm_only``; default ``none``)
proposes VAT IDs for rows whose VAT Registration No. is empty and whose
Country/Region Key is DE, in the ``VAT_Found_*`` columns (``vat/lookup.py``).
``none`` is the behaviour from before the lookups existed, unchanged.

Standalone: not called by ``/enrich``, ADF or DATAshaper, and nothing it
returns is read by enrichment, issues, dedup or scoring. Like every other
route it has no auth of its own; access is whatever the Function App applies.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import Settings, get_settings
from vat.lookup import LOOKUP_COLUMNS, LookupEngine
from vat.lookup_engines import make_lookup
from vat.validate import VAT_COLUMNS, validate_rows
from vat.vies_client import VIESClient
from vat.xlsx import validate_workbook

logger = logging.getLogger(__name__)

router = APIRouter()

LookupMode = Literal["none", "grounded", "llm_only"]


class VatValidationRequest(BaseModel):
    """Rows exactly as exported: column header → cell value. The columns the
    validator reads are ``Customer``, ``Country/Region Key`` and ``VAT
    Registration No.``; every other column is passed through unchanged."""

    rows: list[dict[str, Any]] = Field(..., min_length=1)
    #: Propose VAT IDs for rows with an empty VAT Registration No. and key DE.
    #: ``llm_only`` is an experiment, never a production path.
    lookup_missing: LookupMode = "none"


class VatValidationResponse(BaseModel):
    #: Every input row, unchanged, with the VAT result columns appended.
    rows: list[dict[str, Any]]
    #: The appended columns, in order.
    columns: list[str] = Field(default_factory=lambda: list(VAT_COLUMNS))
    summary: dict[str, Any]


def _get_vies_client(settings: Settings | None = None) -> VIESClient:
    """Build the VIES client from config. Patched in tests."""
    return VIESClient(settings or get_settings())


def _client_or_500() -> VIESClient:
    try:
        return _get_vies_client()
    except ValueError as exc:  # VIES_REQUESTER_VAT misconfigured
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _get_lookup(mode: str, client: VIESClient) -> LookupEngine | None:
    """Build the missing-VAT lookup engine for *mode*. Patched in tests."""
    return make_lookup(mode, get_settings(), client)


async def _validate_json(rows: list[dict[str, Any]], mode: str) -> VatValidationResponse:
    client = _client_or_500()
    lookup = _get_lookup(mode, client)
    logger.info("VAT validation request received: %d rows, lookup_missing=%s", len(rows), mode)
    try:
        out, summary = await validate_rows(rows, client, lookup=lookup)
    except ValueError as exc:  # no VAT column
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if lookup is not None:
            await lookup.aclose()
    columns = VAT_COLUMNS + (LOOKUP_COLUMNS if lookup is not None else [])
    return VatValidationResponse(rows=out, columns=columns, summary=summary)


@router.post("/api/validate/vat", response_model=VatValidationResponse)
async def validate_vat(
    request: VatValidationRequest,
    lookup_missing: LookupMode | None = Query(
        default=None,
        description="Overrides the body's lookup_missing. Default none: unchanged behaviour.",
    ),
) -> VatValidationResponse:
    """Normalise and format-check every row's VAT Registration No., ask VIES
    once per unique valid-format number, and fan the answer out to every row
    of the customer. A VIES outage is ``SERVICE_UNAVAILABLE``, never
    ``INVALID``. ``lookup_missing`` (query or body) adds the ``VAT_Found_*``
    proposals for rows with no VAT ID."""
    return await _validate_json(request.rows, lookup_missing or request.lookup_missing)


@router.post("/api/validate/vat/llm-only-experiment", response_model=VatValidationResponse)
async def validate_vat_llm_only_experiment(request: VatValidationRequest) -> VatValidationResponse:
    """**EXPERIMENT ONLY — not for production use; results are not to be
    written to SAP.** The same as ``/api/validate/vat`` with
    ``lookup_missing=llm_only``: the model is asked for missing VAT IDs from
    memory, to measure how unreliable that is (``vat/lookup_llm_only.py``)."""
    return await _validate_json(request.rows, "llm_only")


@router.post("/api/validate/vat/file")
async def validate_vat_file(
    file: UploadFile = File(..., description="SAP export XLSX (sheet 'Data') with a VAT Registration No. column"),
    lookup_missing: LookupMode = Query(
        default="none", description="Missing-VAT lookup mode. Default none: unchanged behaviour.",
    ),
) -> StreamingResponse:
    """VAT validation for an uploaded workbook; returns the workbook.

    Reads sheet ``Data`` (or the active sheet when there is none), appends the
    ``VAT_*`` columns to it and adds a ``Summary`` sheet; every other sheet and
    every input cell is returned unchanged. Same rules as the JSON endpoint.
    ``X-VAT-Service-Unavailable`` carries the number of unique VAT IDs VIES
    did not answer — non-zero means a re-run is worthwhile, and it will ask
    VIES for only those.
    """
    from openpyxl import load_workbook

    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file must be an .xlsx (or .xlsm) workbook.",
        )
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        wb = load_workbook(io.BytesIO(contents))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a bad upload
        raise HTTPException(
            status_code=400, detail=f"Could not read uploaded file as XLSX: {exc}",
        ) from exc

    client = _client_or_500()
    lookup = _get_lookup(lookup_missing, client)
    logger.info("VAT validation file request received: %s, lookup_missing=%s",
                filename, lookup_missing)
    try:
        summary = await validate_workbook(wb, client, source=filename, lookup=lookup)
    except ValueError as exc:  # no header / no rows / no VAT column
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if lookup is not None:
            await lookup.aclose()

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "export"
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}_vat.xlsx"',
            "X-VAT-Rows": str(summary["rows"]),
            "X-VAT-Service-Unavailable": str(summary["vies_service_unavailable_vat_ids"]),
        },
    )
