"""Route definitions for the enrichment API."""

from __future__ import annotations

import io
import logging
from collections import Counter
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
from dedup.adjudicator import cluster_blocks
from dedup.llm import DedupLLM
from dedup.models import DedupRequest, DedupResponse
from enrichment.issue_detection import ISSUE_CATALOGUE, detect_issues
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


def _present_fields(headers: list[str]) -> set[str]:
    """Map a file's header row onto the set of EnrichmentRecord field names it
    actually carries.

    Used by the issue endpoints so the required-field rules only judge columns
    that exist in the file (an enriched export that drops, say, Postal Code is
    not reported as "missing" it).
    """
    alias_to_field = _input_alias_to_field()
    present: set[str] = set()
    for header in headers:
        field = alias_to_field.get(_norm_header(header))
        if field is not None:
            present.add(field)
    return present


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


def _build_issues_xlsx(
    headers: list[str],
    row_dicts: list[dict[str, str]],
    issues_per_row: list[list[str]],
) -> bytes:
    """Echo the uploaded sheet with one appended ``Issues`` column.

    The original header row and cell values are preserved verbatim (blanks
    filled where a cell was empty); a trailing ``Issues`` column holds the
    semicolon-joined issue codes detected for that row (empty when clean).
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append([*headers, "Issues"])

    for row_dict, codes in zip(row_dicts, issues_per_row):
        values = [row_dict.get(header, "") for header in headers]
        ws.append([*values, "; ".join(codes)])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _audit_upload(file: UploadFile) -> dict[str, list[str]]:
    """Validate, parse, and audit an XLSX upload into ``{record_id: [codes]}``.

    Detection is column-aware (see ``_present_fields``). Rows without a record
    id cannot be joined across files and are excluded (logged, not silently
    dropped). The first occurrence wins for a duplicated id.
    """
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail=f"{filename or 'Uploaded file'} must be an .xlsx (or .xlsm) workbook.",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail=f"{filename or 'Uploaded file'} is empty.")

    headers, row_dicts = _parse_xlsx(contents)
    records = _rows_to_records(row_dicts)
    present = _present_fields(headers)

    issue_map: dict[str, list[str]] = {}
    excluded = 0
    for record in records:
        rid = record.record_id
        if not rid:
            excluded += 1
            continue
        issue_map.setdefault(rid, detect_issues(record, present))

    if excluded:
        logger.info(
            "issues/compare: %s — %d row(s) without a record id excluded from join",
            filename,
            excluded,
        )
    return issue_map


def _build_comparison_xlsx(
    before_map: dict[str, list[str]],
    after_map: dict[str, list[str]],
) -> bytes:
    """Build the before/after issue-reduction report.

    Sheet 1 (Summary): headline totals + a per-code Before/After/Delta table.
    Sheet 2 (Per Record): every matched record's before codes, after codes,
    which issues were resolved, and which were newly introduced.
    Sheet 3 (Remaining Issues): for every issue still present after
    enrichment, the customer id of each record that still has it (one row
    per code/customer pairing).
    Comparison is over records present in BOTH files (joined by record id).
    """
    from openpyxl import Workbook

    matched_ids = [rid for rid in before_map if rid in after_map]
    only_before = [rid for rid in before_map if rid not in after_map]
    only_after = [rid for rid in after_map if rid not in before_map]

    total_before = total_after = total_resolved = total_introduced = 0
    code_before: Counter = Counter()
    code_after: Counter = Counter()
    per_record_rows: list[list[str]] = []

    for rid in matched_ids:
        before = before_map[rid]
        after = after_map[rid]
        bset, aset = set(before), set(after)
        # Order resolved/introduced by catalogue order for stable output.
        resolved = [c for c in ISSUE_CATALOGUE if c in bset - aset]
        introduced = [c for c in ISSUE_CATALOGUE if c in aset - bset]

        total_before += len(before)
        total_after += len(after)
        total_resolved += len(resolved)
        total_introduced += len(introduced)
        code_before.update(bset)
        code_after.update(aset)

        per_record_rows.append([
            rid,
            "; ".join(before),
            "; ".join(after),
            "; ".join(resolved),
            "; ".join(introduced),
        ])

    net = total_before - total_after
    pct = (net / total_before * 100) if total_before else 0.0

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append(["Issue Reduction Summary"])
    summary.append([])
    summary.append(["Records matched (joined by id)", len(matched_ids)])
    summary.append(["Records only in original", len(only_before)])
    summary.append(["Records only in enriched", len(only_after)])
    summary.append([])
    summary.append(["Total issues before", total_before])
    summary.append(["Total issues after", total_after])
    summary.append(["Issues resolved", total_resolved])
    summary.append(["Issues introduced", total_introduced])
    summary.append(["Net reduction", net])
    summary.append(["Reduction %", round(pct, 1)])
    summary.append([])
    summary.append(["Code", "Name", "Before", "After", "Delta"])
    for code, name in ISSUE_CATALOGUE.items():
        before_count = code_before.get(code, 0)
        after_count = code_after.get(code, 0)
        if before_count or after_count:
            summary.append([
                code, name, before_count, after_count, after_count - before_count,
            ])

    per_record = wb.create_sheet("Per Record")
    per_record.append(
        ["record_id", "Issues Before", "Issues After", "Resolved", "Introduced"]
    )
    for row in per_record_rows:
        per_record.append(row)

    # Sheet 3 — Remaining Issues. For every issue still present after
    # enrichment, list the customer id of each record that still has it.
    # One row per (code, customer) so the sheet can be filtered or pivoted
    # by either column. Ordered by catalogue order, then customer id.
    # Covers every record in the enriched file (matched + enriched-only),
    # so no remaining issue is omitted.
    remaining = wb.create_sheet("Remaining Issues")
    remaining.append(["Code", "Name", "Customer"])
    for code, name in ISSUE_CATALOGUE.items():
        ids = sorted(rid for rid, codes in after_map.items() if code in codes)
        for rid in ids:
            remaining.append([code, name, rid])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.post("/enrich/file")
async def enrich_file(
    file: UploadFile = File(..., description="XLSX file of customer master data records"),
    max_concurrency: int = Query(default=5, ge=1, le=20),
    serp_provider: Literal["serpapi", "duckduckgo"] = Query(default="serpapi"),
    skip_tier: Optional[int] = Query(default=None),
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


@router.post("/issues")
async def detect_file_issues(
    file: UploadFile = File(..., description="XLSX file of customer master data records"),
) -> StreamingResponse:
    """Audit an XLSX upload against the Issue Catalogue and return it annotated.

    Each data row is checked against the deterministic issue-detection rules
    (see ``enrichment.issue_detection``). The uploaded sheet is returned
    unchanged with a single appended ``Issues`` column listing every issue
    code found on that row (semicolon-separated, codes only). This is a pure
    audit: no enrichment, LLM, or external call is made.
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

    headers, row_dicts = _parse_xlsx(contents)
    records = _rows_to_records(row_dicts)
    present = _present_fields(headers)
    issues_per_row = [detect_issues(record, present) for record in records]

    logger.info(
        "Issues file request received: %s, %d records, %d with issues",
        filename,
        len(records),
        sum(1 for issues in issues_per_row if issues),
    )

    output_bytes = _build_issues_xlsx(headers, row_dicts, issues_per_row)

    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "records"
    out_name = f"{stem}_issues.xlsx"
    return StreamingResponse(
        io.BytesIO(output_bytes),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


@router.post("/issues/compare")
async def compare_file_issues(
    original: UploadFile = File(..., description="Raw (pre-enrichment) XLSX"),
    enriched: UploadFile = File(..., description="Enriched (post-pipeline) XLSX"),
) -> StreamingResponse:
    """Audit two files and report how many issues the pipeline reduced.

    Runs the deterministic issue detector over both the ``original`` and the
    ``enriched`` workbook, joins their rows by record id, and returns an XLSX
    report: a summary (totals + per-code Before/After/Delta) and a per-record
    breakdown of which issues were resolved vs newly introduced.

    Detection is column-aware, so a required-field rule never counts a column
    as "missing" in a file that simply doesn't carry it (e.g. the enriched
    export dropping Postal Code) — the before/after counts stay apples-to-apples.
    """
    before_map = await _audit_upload(original)
    after_map = await _audit_upload(enriched)

    logger.info(
        "Issues compare request: original=%s (%d records), enriched=%s (%d records)",
        original.filename,
        len(before_map),
        enriched.filename,
        len(after_map),
    )

    output_bytes = _build_comparison_xlsx(before_map, after_map)
    return StreamingResponse(
        io.BytesIO(output_bytes),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": 'attachment; filename="issue_reduction_report.xlsx"'
        },
    )


def _get_dedup_llm(settings: Settings):
    """Build the dedup adjudicator LLM, real or mock based on config.

    Mirrors ``_get_orchestrator`` — when ``MOCK_EXTERNAL_CALLS`` is set the
    mock client is used so the endpoint runs offline.
    """
    if settings.mock_external_calls:
        from tests.mocks.dedup_mock import MockDedupLLM
        logger.info("Mock mode enabled — using mock dedup LLM")
        return MockDedupLLM()
    return DedupLLM(settings)


@router.post("/api/dedup/cluster-block", response_model=DedupResponse)
async def dedup_cluster_block(request: DedupRequest) -> DedupResponse:
    """Phase 2 "Pass 2" deduplication adjudicator.

    Takes address-gated candidate rows (already cleared by DATAshaper's
    address gates — same country + postal code + street) grouped into one or
    more blocks, decides which rows refer to the same (institution,
    department) entity, and returns cluster assignments. Auth is inherited
    from the Azure Function App (same key/function-auth pattern as the other
    endpoints); the function is JSON in / JSON out and never reads files.
    """
    settings = get_settings()
    llm = _get_dedup_llm(settings)

    logger.info("Dedup request received: %d rows", len(request.rows))

    try:
        return await cluster_blocks(request.rows, llm, settings=settings)
    finally:
        # Release the cached AsyncAzureOpenAI HTTP client cleanly, matching
        # the orchestrator's lifecycle handling. Mocks expose aclose only
        # when present.
        aclose = getattr(llm, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception as exc:  # noqa: BLE001
                logger.info("Dedup LLM aclose failed (non-fatal): %s", exc)


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


@router.get("/diag/dedup-llm")
async def diag_dedup_llm() -> dict:
    """Diagnostic: make one real dedup adjudication call and return the raw
    outcome. Use this when /api/dedup/cluster-block returns everything as
    manual_review with errors>0 — it surfaces the actual Azure error string
    (e.g. an unsupported api-version or reasoning_effort rejection) that the
    per-block handler swallows to keep the batch alive.
    """
    import os

    env_snapshot = {
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT", "<unset>"),
        "AOAI_DEPLOYMENT_DEDUP": os.getenv("AOAI_DEPLOYMENT_DEDUP", "<unset>"),
        "AOAI_API_VERSION_DEDUP": os.getenv("AOAI_API_VERSION_DEDUP", "<unset>"),
        "DEDUP_REASONING_EFFORT": os.getenv("DEDUP_REASONING_EFFORT", "<unset>"),
        "AZURE_OPENAI_API_KEY_present": bool(os.getenv("AZURE_OPENAI_API_KEY")),
    }
    llm = DedupLLM(get_settings())
    try:
        result = await llm.adjudicate(
            "Return valid JSON only.",
            'Return STRICT JSON: {"ok": true}',
            max_tokens=200,
        )
        return {
            "status": "ok" if result.error is None else "failed",
            "raw": result.raw,
            "error": result.error,
            "model_version": result.model_version,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "api_version": llm._api_version,
            "reasoning_effort_used": llm._use_reasoning_effort,
            "env": env_snapshot,
        }
    finally:
        await llm.aclose()


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
