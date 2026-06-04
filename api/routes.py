"""Route definitions for the enrichment API."""

from __future__ import annotations

import io
import logging
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, ValidationError

from api.models import (
    EnrichmentOptions,
    EnrichmentRecord,
    EnrichmentRequest,
    EnrichmentResponse,
    EnrichmentResult,
    HealthResponse,
    TierConfigResponse,
)
from api.output_columns import RESPONSE_COLUMNS
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


# ---------------------------------------------------------------------------
# XLSX <-> records helpers for the /enrich/file endpoint
# ---------------------------------------------------------------------------


def _norm_header(name: str) -> str:
    """Normalise a column header for tolerant matching.

    Real spreadsheet exports vary in case, surrounding/internal whitespace,
    and punctuation (e.g. "Name 1" vs "name1" vs "NAME 1 "). Collapsing
    those differences lets enriched values land back on the right original
    column instead of being appended as a duplicate.
    """
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _input_alias_to_field() -> dict[str, str]:
    """Reverse-map every accepted input header/alias (normalised) to its
    model field name."""
    alias_to_field: dict[str, str] = {}
    for field_name, field in EnrichmentRecord.model_fields.items():
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names = list(alias.choices)
        elif isinstance(alias, str):
            names = [alias]
        else:
            names = []
        names.append(field_name)  # populate_by_name is enabled
        for name in names:
            alias_to_field.setdefault(_norm_header(name), field_name)
    return alias_to_field


def _parse_xlsx(contents: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Parse an uploaded XLSX into (headers, row dicts).

    The first non-empty row is the header row; its cells map onto the SAP
    column names accepted by EnrichmentRecord (e.g. "Name 1", "Customer").
    Each subsequent non-blank row becomes a dict keyed by header.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise HTTPException(
            status_code=500,
            detail="openpyxl is required to parse XLSX uploads but is not installed.",
        ) from exc

    try:
        workbook = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a bad upload
        raise HTTPException(
            status_code=400,
            detail=f"Could not read uploaded file as XLSX: {exc}",
        ) from exc

    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)

    # Locate the header row (skip leading fully-empty rows).
    headers: list[str] = []
    for raw_header in rows:
        if raw_header is None:
            continue
        candidate = [
            str(cell).strip() if cell is not None else "" for cell in raw_header
        ]
        if any(candidate):
            headers = candidate
            break

    if not headers:
        workbook.close()
        raise HTTPException(status_code=400, detail="Uploaded XLSX has no header row.")

    row_dicts: list[dict[str, str]] = []
    for raw_row in rows:
        if raw_row is None:
            continue
        row_dict: dict[str, str] = {}
        for header, cell in zip(headers, raw_row):
            if not header or cell is None:
                continue
            value = str(cell).strip()
            if value:
                row_dict[header] = value
        if row_dict:  # skip entirely blank rows
            row_dicts.append(row_dict)

    workbook.close()

    if not row_dicts:
        raise HTTPException(
            status_code=400, detail="No data rows found in uploaded XLSX."
        )

    return headers, row_dicts


def _rows_to_records(row_dicts: list[dict[str, str]]) -> list[EnrichmentRecord]:
    """Validate row dicts into EnrichmentRecords using the shared model.

    Headers are normalised to their canonical model field name first, so a
    column written as "NAME 1", "Name1", or " name 1 " is still recognised
    as the same input field (model_validate alone only honours the exact
    declared aliases).
    """
    alias_to_field = _input_alias_to_field()
    records: list[EnrichmentRecord] = []
    errors: list[str] = []
    for index, row_dict in enumerate(row_dicts, start=1):
        # Map each raw header onto the model field it populates; keep the
        # first non-empty value when several headers resolve to one field.
        normalised: dict[str, str] = {}
        for header, value in row_dict.items():
            field = alias_to_field.get(_norm_header(header))
            key = field if field is not None else header
            if key not in normalised:
                normalised[key] = value
        try:
            records.append(EnrichmentRecord.model_validate(normalised))
        except ValidationError as exc:
            errors.append(f"row {index}: {exc.errors()[0].get('msg', str(exc))}")

    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Some rows failed validation", "errors": errors},
        )
    return records


def _build_output_xlsx(results: list[EnrichmentResult]) -> bytes:
    """Build the result workbook.

    One column per field in the /enrich response body (see
    api.output_columns.RESPONSE_COLUMNS), one row per enriched record.
    """
    from openpyxl import Workbook

    fields = list(RESPONSE_COLUMNS.keys())
    headers = [RESPONSE_COLUMNS[f] for f in fields]

    wb = Workbook()
    ws = wb.active
    ws.append(headers)

    for result in results:
        data = result.model_dump()
        ws.append([data.get(field) for field in fields])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.post("/enrich/file")
async def enrich_file(
    file: UploadFile = File(..., description="XLSX file of customer master data records"),
    max_concurrency: int = Query(default=5, ge=1, le=20),
    serp_provider: Literal["serpapi", "duckduckgo"] = Query(default="serpapi"),
    skip_tier: Optional[int] = Query(default=None),
    dry_run: bool = Query(default=False),
) -> StreamingResponse:
    """Enrichment endpoint that takes an XLSX upload and returns an XLSX.

    The spreadsheet uses the same input fields as the /enrich request
    records (SAP column headers). Each data row is turned into one
    EnrichmentRecord, the records are enriched exactly as the JSON /enrich
    endpoint does, and the results are written to a workbook with one column
    per field in the /enrich response body (see api.output_columns).
    """
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file must be an .xlsx (or .xlsm) workbook.",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    _headers, row_dicts = _parse_xlsx(contents)
    records = _rows_to_records(row_dicts)
    options = EnrichmentOptions(
        max_concurrency=max_concurrency,
        serp_provider=serp_provider,
        skip_tier=skip_tier,
        dry_run=dry_run,
    )
    request = EnrichmentRequest(records=records, options=options)

    settings = get_settings()
    orchestrator = _get_orchestrator(settings)

    logger.info(
        "Enrichment file request received: %s, %d records, concurrency=%d",
        filename,
        len(request.records),
        request.options.max_concurrency,
    )

    response = await orchestrator.enrich_batch(request.records, request.options)

    output_bytes = _build_output_xlsx(response.results)

    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "records"
    out_name = f"{stem}_enriched.xlsx"
    return StreamingResponse(
        io.BytesIO(output_bytes),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


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
