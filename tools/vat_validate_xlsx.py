"""Validate the VAT IDs in an SAP customer export. Excel in, Excel out.

Usage::

    python tools/vat_validate_xlsx.py tests/fixtures/vat/VAT_TEST_EXPORT_30.xlsx
    python tools/vat_validate_xlsx.py export.xlsx --out export_vat.xlsx
    python tools/vat_validate_xlsx.py export.xlsx --frozen --cache-dir logs/cache/vat

Reads sheet ``Data`` (falling back, with a warning, to the active sheet when
there is none), runs every row through the same code as
``POST /api/validate/vat`` and writes a copy of the workbook with:

* the ``VAT_*`` result columns appended to the data sheet — every input cell,
  every other sheet and all formatting left as they were;
* a ``Summary`` sheet — counts per state, per Country/Region Key, and unique
  customers against rows.

The input file is never modified. Run on a file this tool already wrote, the
``VAT_*`` columns are overwritten in place rather than appended twice.

``--frozen`` sets CACHE_FROZEN: no VIES call is made, a number with no
recording is SERVICE_UNAVAILABLE / FROZEN_CACHE_MISS.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("tools.vat_validate_xlsx")

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
        raise SystemExit(f"Sheet {ws.title!r} has no header row.")

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
    from vat.name_match import MATCH, MISMATCH, NOT_AVAILABLE, PARTIAL
    from vat.normalize import FORMAT_ERROR, FORMAT_OK, MISSING, NOT_EU
    from vat.validate import NOT_CHECKED
    from vat.vies_client import ERROR, INVALID, SERVICE_UNAVAILABLE, VALID

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
    ])
    format_states = [FORMAT_OK, FORMAT_ERROR, MISSING, NOT_EU]
    vies_states = [VALID, INVALID, SERVICE_UNAVAILABLE, ERROR, NOT_CHECKED]
    section("Rows per format status", [(s, summary["format_status"].get(s, 0)) for s in format_states])
    section("Rows per VIES status", [(s, summary["vies_status"].get(s, 0)) for s in vies_states])
    name_states = [MATCH, PARTIAL, MISMATCH, NOT_AVAILABLE]
    section("Rows per VIES name match", [(s, summary["name_match"].get(s, 0)) for s in name_states])

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


async def run(input_path: Path, out_path: Path, client) -> dict[str, Any]:
    """Validate *input_path* into *out_path*. Returns the summary."""
    from openpyxl import load_workbook

    from vat.validate import VAT_COLUMNS, validate_rows

    wb = load_workbook(input_path)
    ws = _data_sheet(wb)
    header_idx, headers, rows = _read(ws, VAT_COLUMNS)
    if not rows:
        raise SystemExit(f"Sheet {ws.title!r} has no data rows.")

    results, summary = await validate_rows([r for _, r in rows], client)
    _write_results(ws, header_idx, headers, rows, results, VAT_COLUMNS)
    _write_summary(wb, summary, [
        ("Input", str(input_path)),
        ("Sheet", ws.title),
        ("Run at (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        ("Source", "VIES (European Commission)"),
        ("Cache frozen", "yes" if client.frozen else "no"),
    ])
    wb.save(out_path)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="SAP export XLSX (sheet 'Data').")
    ap.add_argument("--out", help="Output XLSX (default: <input>_vat.xlsx next to the input).")
    ap.add_argument("--frozen", action="store_true",
                    help="Force CACHE_FROZEN=true — replay recorded VIES answers only.")
    ap.add_argument("--cache-dir", default=None,
                    help="Override EVIDENCE_CACHE_DIR (VIES answers go in <dir>/vies/).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Must be set before Settings is constructed.
    if args.frozen:
        os.environ["CACHE_FROZEN"] = "true"
    if args.cache_dir is not None:
        os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir

    from config import get_settings  # noqa: E402
    from vat.vies_client import VIESClient  # noqa: E402

    input_path = Path(args.input).resolve()
    out_path = Path(args.out).resolve() if args.out else input_path.with_name(
        f"{input_path.stem}_vat{input_path.suffix}",
    )
    if out_path == input_path:
        raise SystemExit("--out must differ from the input file; the input is never modified.")

    summary = asyncio.run(run(input_path, out_path, VIESClient(get_settings())))
    print(f"Wrote {out_path}")
    print(f"  rows={summary['rows']} unique_customers={summary['unique_customers']} "
          f"vies_lookups={summary['unique_vies_lookups']} "
          f"conflicts={summary['customers_with_vat_conflict']}")
    print(f"  format: {summary['format_status']}")
    print(f"  vies:   {summary['vies_status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
