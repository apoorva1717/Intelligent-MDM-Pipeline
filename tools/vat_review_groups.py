"""Review groups (vat/review_groups.py) on the latest workbook copy.

Usage::

    python tools/vat_review_groups.py

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_persons_longnum.xlsx, only read.
Output: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_groups.xlsx (not
committed): the same workbook with ``Review_Group`` and ``Review_Group_Why``
on every Review sheet row, and a last sheet "Sample to check" with
:data:`SAMPLE` random rows of each group (one per proposed number) and empty columns for the
reviewer's verdict. No calls.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from copy import copy
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_persons_longnum.xlsx"
OUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_groups.xlsx"
NEW = ["Review_Group", "Review_Group_Why"]
SAMPLE = 20
SHEET = "Sample to check"
SAMPLE_COLUMNS = ["Review_Group", "Review_Group_Why", "Sheet", "Customer", "Name", "Name 2", "Street",
                  "House Number", "Postal Code", "City", "VAT_Proposed", "Found_Name", "Found_Address",
                  "Name_Check", "Address_Check", "Issue_Flags", "VAT_Found_Source_URL", "VAT_Found_Evidence",
                  "VAT_Why"]
VERDICT = ["Your verdict (right / wrong / unsure)", "Your note"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=20261112)
    args = ap.parse_args()

    from openpyxl import load_workbook

    from vat.review_groups import GROUPS, group_of

    wb = load_workbook(INPUT)
    counts: Counter = Counter()
    by_group: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for ws in wb.worksheets:
        if not ws.title.endswith(" - Review"):
            continue
        h = [c.value for c in ws[1]]
        while h and h[-1] is None:
            h.pop()
        cols = {}
        for name in NEW:
            if name not in h:
                cell = ws.cell(row=1, column=len(h) + 1, value=name)
                src = ws.cell(row=1, column=len(h))
                cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill),
                                                                     copy(src.alignment), copy(src.border))
                h.append(name)
            cols[name] = h.index(name) + 1
        for r in range(2, ws.max_row + 1):
            row = {name: ws.cell(row=r, column=j + 1).value for j, name in enumerate(h)}
            if not any(v is not None for v in row.values()):
                continue
            group, why = group_of(row)
            ws.cell(row=r, column=cols["Review_Group"], value=group)
            ws.cell(row=r, column=cols["Review_Group_Why"], value=why)
            counts[(ws.title, group)] += 1
            by_group[group].append(dict(row, Review_Group=group, Review_Group_Why=why, Sheet=ws.title))
        if ws.auto_filter.ref:
            ws.auto_filter.ref = ws.dimensions

    # One row per proposed number (records of one company share it), then a
    # simple random sample per group, all four groups.
    rng = random.Random(args.seed)
    sample = []
    for g in GROUPS:
        unique = list({r.get("VAT_Proposed") or id(r): r for r in by_group[g]}.values())
        sample += rng.sample(unique, min(SAMPLE, len(unique)))
    ss = wb.create_sheet(SHEET)
    ss.append(SAMPLE_COLUMNS + VERDICT)
    src = wb.worksheets[0].cell(row=1, column=1)
    for j in range(len(SAMPLE_COLUMNS) + len(VERDICT)):
        cell = ss.cell(row=1, column=j + 1)
        cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill), copy(src.alignment),
                                                             copy(src.border))
    for r in sample:
        ss.append([r.get(c) for c in SAMPLE_COLUMNS] + [None, None])
    ss.freeze_panes = "A2"
    ss.auto_filter.ref = ss.dimensions
    wb.save(OUT)

    total = Counter()
    for (_, g), n in counts.items():
        total[g] += n
    print(f"Review rows by group: {dict(sorted(total.items()))} (total {sum(total.values())})")
    for (sheet, g), n in sorted(counts.items()):
        print(f"  {sheet}: {g}: {n}")
    print(f"Sample: {dict(Counter(r['Review_Group'] for r in sample))}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
