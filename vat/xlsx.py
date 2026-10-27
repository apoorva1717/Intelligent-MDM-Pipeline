"""Workbook in, workbook out: the Excel half of VAT validation.

Shared by ``tools/vat_validate_xlsx.py`` (a file on disk) and
``POST /api/validate/vat/file`` (an upload), so both produce the same workbook:

* sheet ``Data`` (or, with a warning, the active sheet when there is none):
  every input cell, every other sheet and all formatting left as they were,
  the ``VAT_*`` result columns appended — or overwritten in place when the
  workbook already carries them (a re-run on an earlier output);
* a ``Summary`` sheet, replaced on every run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from vat.lookup import COMPARE_COLUMNS, LOOKUP_COLUMNS, LookupEngine
from vat.name_match import MATCH, MISMATCH, NOT_AVAILABLE, PARTIAL
from vat.normalize import FORMAT_ERROR, FORMAT_OK, MISSING, NOT_EU
from vat.validate import ALL_RESULT_COLUMNS, NOT_CHECKED, VAT_COLUMNS, result_columns, validate_rows
from vat.vies_client import ERROR, INVALID, SERVICE_UNAVAILABLE, VALID, VIESClient

logger = logging.getLogger(__name__)

DATA_SHEET = "Data"
SUMMARY_SHEET = "Summary"


def _data_sheet(wb):
    if DATA_SHEET in wb.sheetnames:
        return wb[DATA_SHEET]
    ws = wb.active
    logger.warning(
        "No sheet named %r (sheets: %s); using the active sheet %r.",
        DATA_SHEET, ", ".join(wb.sheetnames), ws.title,
    )
    return ws


def _read(ws, result_columns: list[str]):
    """``(header_row_index, headers, [(row_index, row_dict)])``.

    The first non-empty row is the header row, as in the upload reader. Blank
    rows are skipped. Existing result columns are not part of the input.
    """
    header_idx, headers = None, []
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            header_idx, headers = idx, cells
            break
    if header_idx is None:
        raise ValueError(f"Sheet {ws.title!r} has no header row.")

    rows: list[tuple[int, dict[str, Any]]] = []
    for idx, row in enumerate(
        ws.iter_rows(min_row=header_idx + 1, values_only=True), start=header_idx + 1,
    ):
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        rows.append((idx, {
            h: v for h, v in zip(headers, row) if h and h not in result_columns
        }))
    return header_idx, headers, rows


def _write_results(ws, header_idx, headers, rows, results, result_columns) -> None:
    col_of: dict[str, int] = {}
    next_col = len(headers) + 1
    for name in result_columns:
        if name in headers:
            col_of[name] = headers.index(name) + 1
        else:
            col_of[name] = next_col
            next_col += 1
        ws.cell(row=header_idx, column=col_of[name], value=name)
    for (row_idx, _), result in zip(rows, results):
        for name in result_columns:
            ws.cell(row=row_idx, column=col_of[name], value=result[name])


def _write_summary(wb, summary: dict[str, Any], meta: list[tuple[str, Any]]) -> None:
    if SUMMARY_SHEET in wb.sheetnames:
        del wb[SUMMARY_SHEET]
    ws = wb.create_sheet(SUMMARY_SHEET)

    def section(title: str, pairs) -> None:
        if ws.max_row > 1:
            ws.append([])
        ws.append([title])
        for pair in pairs:
            ws.append(list(pair))

    section("Run", meta)
    section("Totals", [
        ("Rows", summary["rows"]),
        ("Unique customers", summary["unique_customers"]),
        ("Rows without Customer", summary["rows_without_customer"]),
        ("Customers with conflicting VAT values", summary["customers_with_vat_conflict"]),
        ("Unique VAT numbers sent to VIES", summary["unique_vies_lookups"]),
        ("VIES HTTP calls (incl. retries)", summary["vies_network_calls"]),
        ("VIES answers replayed from cache", summary["vies_from_cache"]),
        ("Unique VAT IDs SERVICE_UNAVAILABLE", summary["vies_service_unavailable_vat_ids"]),
        ("Rows SERVICE_UNAVAILABLE", summary["vies_service_unavailable_rows"]),
        ("Cache write failures (answers not recorded)", summary.get("cache_write_failures", 0)),
    ])
    format_states = [FORMAT_OK, FORMAT_ERROR, MISSING, NOT_EU]
    vies_states = [VALID, INVALID, SERVICE_UNAVAILABLE, ERROR, NOT_CHECKED]
    section("Rows per format status", [(s, summary["format_status"].get(s, 0)) for s in format_states])
    section("Rows per VIES status", [(s, summary["vies_status"].get(s, 0)) for s in vies_states])
    name_states = [MATCH, PARTIAL, MISMATCH, NOT_AVAILABLE]
    section("Rows per VIES name match", [(s, summary["name_match"].get(s, 0)) for s in name_states])

    # Not cached, so re-running on the same input asks VIES again for exactly
    # these numbers; every VALID / INVALID is replayed.
    ws.append([])
    ws.append(["VAT IDs VIES did not answer (re-queried on re-run; all other answers come from cache)"])
    ws.append(["VAT_Normalized", "VIES status", "VIES error", "Rows"])
    for u in summary["vies_unanswered"]:
        ws.append([u["vat"], u["status"], u["error_code"], u["rows"]])
    if not summary["vies_unanswered"]:
        ws.append(["(none)"])

    lookup = summary.get("lookup")
    if lookup:
        ws.append([])
        ws.append([f"Missing-VAT lookup — mode {lookup['mode']} (proposals only; "
                   "VAT Registration No. is never written)"])
        if lookup["mode"] == "llm_only":
            ws.append(["EXPERIMENT ONLY — not for production use; results are not to be written to SAP."])
        if lookup["mode"] == "llm_web":
            ws.append(["LLM web search (never from memory): every number is checked against the page "
                       "the model read it on; one without such a page is dropped."])
        scope_text = ("every DE row, VAT ID or not — evaluation against SAP"
                      if lookup.get("scope") == "all" else "VAT blank, Country/Region Key DE")
        ws.append([f"Rows in scope ({scope_text})", lookup["rows_in_scope"]])
        for status, n in sorted(lookup["status"].items()):
            ws.append([status, n])
        if lookup.get("vs_sap"):
            ws.append(["VAT_Found_vs_SAP (proposal against the existing SAP VAT ID)"])
            for value, n in sorted(lookup["vs_sap"].items()):
                ws.append([value, n])
        ws.append(["Cost"])
        for key, n in lookup["cost"].items():
            ws.append([key, n])
    compare = summary.get("compare_lookup")
    if compare:
        ws.append([])
        ws.append([f"Side by side: {compare['mode']} lookup on the same rows "
                   "(VAT_Grounded_* columns; proposals only)"])
        for status, n in sorted(compare["status"].items()):
            ws.append([status, n])
        ws.append(["VAT_LLM_vs_Grounded"])
        for value, n in sorted(compare["agreement"].items()):
            ws.append([value, n])
        if compare.get("vs_sap"):
            ws.append(["VAT_Grounded_vs_SAP (proposal against the existing SAP VAT ID)"])
            for value, n in sorted(compare["vs_sap"].items()):
                ws.append([value, n])
        ws.append(["Cost"])
        for key, n in compare["cost"].items():
            ws.append([key, n])

    ws.append([])
    ws.append(["Rows per Country/Region Key"])
    ws.append(["Country/Region Key", "Rows", *format_states, *vies_states])
    for country, counts in summary["by_country"].items():
        fmt, vies = counts["format_status"], counts["vies_status"]
        ws.append([
            country, sum(fmt.values()),
            *(fmt.get(s, 0) for s in format_states),
            *(vies.get(s, 0) for s in vies_states),
        ])


async def validate_workbook(
    wb, client: VIESClient, *, source: str, lookup: LookupEngine | None = None,
    compare_lookup: LookupEngine | None = None, scope: str = "missing",
) -> dict[str, Any]:
    """Validate *wb* in place (result columns + Summary sheet). Returns the
    summary. *source* names the input on the Summary sheet. With a *lookup*
    engine the ``VAT_Found_*`` columns are written too.

    Raises ``ValueError`` for a sheet with no header row, no data rows, or no
    VAT Registration No. column.
    """
    ws = _data_sheet(wb)
    # Earlier result columns are never read back as input, lookup ones included.
    header_idx, headers, rows = _read(ws, ALL_RESULT_COLUMNS)
    if not rows:
        raise ValueError(f"Sheet {ws.title!r} has no data rows.")

    results, summary = await validate_rows(
        [r for _, r in rows], client, lookup=lookup, compare_lookup=compare_lookup, scope=scope,
    )
    written = result_columns(results)
    _write_results(ws, header_idx, headers, rows, results, written)
    _write_summary(wb, summary, [
        ("Input", source),
        ("Sheet", ws.title),
        ("Run at (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        ("Source", "VIES (European Commission)" if client.enabled else "none — VIES not called"),
        ("Cache frozen", "yes" if client.frozen else "no"),
    ])
    summary["sheet"] = ws.title
    return summary
