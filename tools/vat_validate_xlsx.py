"""Validate the VAT IDs in an SAP customer export. Excel in, Excel out.

Usage::

    python tools/vat_validate_xlsx.py tests/fixtures/vat/VAT_TEST_EXPORT_30.xlsx
    python tools/vat_validate_xlsx.py export.xlsx --out export_vat.xlsx
    python tools/vat_validate_xlsx.py export.xlsx --frozen --cache-dir logs/cache/vat

Reads sheet ``Data`` (falling back, with a warning, to the active sheet when
there is none), runs every row through the same code as
``POST /api/validate/vat`` — the workbook handling itself is ``vat/xlsx.py``,
shared with ``POST /api/validate/vat/file`` — and writes a copy with:

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
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


async def run(input_path: Path, out_path: Path, client) -> dict[str, Any]:
    """Validate *input_path* into *out_path*. Returns the summary."""
    from openpyxl import load_workbook

    from vat.xlsx import validate_workbook

    wb = load_workbook(input_path)
    try:
        summary = await validate_workbook(wb, client, source=str(input_path))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
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
