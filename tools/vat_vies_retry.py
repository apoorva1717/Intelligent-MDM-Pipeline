"""Verify the proposed VAT IDs queued for VIES (``vat/vies_retry.py``).

Asks VIES for every queued number that is due (at once, then hourly for
24 h after it was queued, then daily for 7 days), records the answers in the
queue, and — with ``--workbook`` — writes them into a workbook the lookup
produced: VALID clears ``VIES_PENDING`` and sets the validation status and
date; INVALID makes the row MANUAL_REVIEW with ``VIES_INVALID``. Only rows
whose ``*_Validation_Status`` is PENDING (or UNAVAILABLE) are touched. The
batch runner (``tools/vat_validate_xlsx.py``) does the same at start-up and
at the end of every run.

Schedule this hourly (cron, a timer trigger). ``--all`` ignores the schedule
and asks for every pending number.

    python tools/vat_vies_retry.py [--all] [--workbook out_vat.xlsx [--out file]] [--cache-dir DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _apply_to_workbook(path: Path, out: Path, answers) -> int:
    from openpyxl import load_workbook

    from vat.vies_retry import apply_to_workbook

    wb = load_workbook(path)
    changed = apply_to_workbook(wb, answers)
    wb.save(out)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Ask for every pending number, due or not.")
    ap.add_argument("--workbook", help="A lookup output workbook to write the answers into.")
    ap.add_argument("--out", help="Output workbook (default: overwrite --workbook).")
    ap.add_argument("--cache-dir", default=None, help="Override EVIDENCE_CACHE_DIR.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.cache_dir is not None:
        os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir

    from config import get_settings  # noqa: E402
    from vat.vies_client import VIESClient  # noqa: E402
    from vat.vies_retry import drain, summary_line  # noqa: E402

    client = VIESClient(get_settings())
    if client.frozen:
        raise SystemExit("CACHE_FROZEN is set: a frozen run never asks VIES or writes the queue.")
    queue, asked = asyncio.run(drain(client, everything=args.all))
    print(f"VIES queue: {summary_line(asked)}")
    if args.workbook:
        path = Path(args.workbook).resolve()
        out = Path(args.out).resolve() if args.out else path
        changed = _apply_to_workbook(path, out, queue.answers())
        print(f"Updated {changed} row(s) in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
