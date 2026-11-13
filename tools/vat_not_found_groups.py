"""Not found groups (vat/review_groups.py::not_found_group) on the review-groups workbook.

Usage::

    python tools/vat_not_found_groups.py

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_groups.xlsx, only read (it
holds the reviewer's coloured "Sample to check", which is kept as it is).
Output: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_ready.xlsx (not committed):
the same workbook with ``Review_Group`` on every Not found row; rows where no
VAT number is expected are moved to a new sheet "<prefix> - No VAT expected"
right after their Not found sheet, so Not found keeps only "search by hand".
No calls.
"""

from __future__ import annotations

import sys
from collections import Counter
from copy import copy
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_groups.xlsx"
OUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_ready.xlsx"
COLUMN = "Review_Group"
NOT_FOUND = " - Not found"
NO_VAT = " - No VAT expected"


def _put(ws, r: int, cells: list) -> None:
    """Write one row's values and styles (``cells`` from :func:`_take`) at row ``r``."""
    for j, (value, style) in enumerate(cells, start=1):
        cell = ws.cell(row=r, column=j, value=value)
        cell._style = copy(style)


def _take(ws, r: int, width: int) -> list:
    return [(ws.cell(row=r, column=j).value, ws.cell(row=r, column=j)._style) for j in range(1, width + 1)]


def main() -> int:
    from openpyxl import load_workbook

    from vat.review_groups import SEARCH, not_found_group

    wb = load_workbook(INPUT)
    counts: Counter = Counter()
    for ws in [w for w in wb.worksheets if w.title.endswith(NOT_FOUND)]:
        h = [c.value for c in ws[1]]
        while h and h[-1] is None:
            h.pop()
        if COLUMN not in h:
            cell = ws.cell(row=1, column=len(h) + 1, value=COLUMN)
            src = ws.cell(row=1, column=len(h))
            cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill),
                                                                 copy(src.alignment), copy(src.border))
            h.append(COLUMN)
        col, width = h.index(COLUMN) + 1, len(h)
        keep, move = [], []
        for r in range(2, ws.max_row + 1):
            row = {name: ws.cell(row=r, column=j + 1).value for j, name in enumerate(h)}
            if not any(v is not None for v in row.values()):
                continue
            group = not_found_group(row)
            ws.cell(row=r, column=col, value=group)
            counts[(ws.title, group)] += 1
            (keep if group == SEARCH else move).append(_take(ws, r, width))

        # The moved rows get their own sheet, laid out like Not found.
        new = wb.create_sheet(ws.title[:-len(NOT_FOUND)] + NO_VAT, index=wb.sheetnames.index(ws.title) + 1)
        _put(new, 1, _take(ws, 1, width))
        for i, cells in enumerate(move, start=2):
            _put(new, i, cells)
        for key, dim in ws.column_dimensions.items():
            new.column_dimensions[key].width = dim.width
        new.freeze_panes = ws.freeze_panes
        new.auto_filter.ref = new.dimensions

        # Not found keeps only the rows to search by hand, in their order.
        last = ws.max_row
        for i, cells in enumerate(keep, start=2):
            _put(ws, i, cells)
        if last > len(keep) + 1:
            ws.delete_rows(len(keep) + 2, last - len(keep) - 1)
        if ws.auto_filter.ref:
            ws.auto_filter.ref = ws.dimensions
    wb.save(OUT)

    for (sheet, g), n in sorted(counts.items()):
        print(f"  {sheet}: {g}: {n}")
    total = Counter()
    for (_, g), n in counts.items():
        total[g] += n
    print(f"Not found rows by group: {dict(total)}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
