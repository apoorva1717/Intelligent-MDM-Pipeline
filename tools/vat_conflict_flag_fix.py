"""The flags of the Conflict rows, checked on the page (2026-11-13).

Usage::

    python tools/vat_conflict_flag_fix.py

Input: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_ready.xlsx, only read.
Output: data/VAT/DE_VAT_FINAL_einvoicing_ALL_review_ready_v2.xlsx (not
committed). Every Conflict row's source page is opened again (``vat_pages``
in ``--cache-dir``; no model calls) and its DE numbers are listed in a new
column ``Page_DE_Numbers``. A row flagged SEVERAL_NUMBERS_ON_PAGE whose page
shows exactly one number, the proposed one, gets NUMBER_NOT_CONFIRMED
instead, and its VAT_Why sentence is replaced (vat/review_columns.py). A
page that shows two or more numbers, none, another one, or does not open,
keeps its flag. No row moves; nothing else changes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from collections import Counter
from copy import copy
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

INPUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_ready.xlsx"
OUT = _ROOT / "data" / "VAT" / "DE_VAT_FINAL_einvoicing_ALL_review_ready_v2.xlsx"
COLUMN = "Page_DE_Numbers"
NOT_READABLE = "page not readable now"
_SENTENCE_RE = re.compile(r"The page lists several VAT IDs; check that the proposed one belongs to '.*?'\.")


def one_number(flags: str | None, why: str | None) -> tuple[str | None, str | None]:
    """Issue_Flags and VAT_Why of a row whose page shows one number:
    SEVERAL_NUMBERS_ON_PAGE becomes NUMBER_NOT_CONFIRMED, in the same place,
    and so does its sentence."""
    from vat.review_columns import NOT_CONFIRMED_WHY, NUMBER_NOT_CONFIRMED, SEVERAL_NUMBERS_ON_PAGE

    parts = [f.strip() for f in str(flags or "").split(";") if f.strip()]
    parts = [NUMBER_NOT_CONFIRMED if p.split(":")[0] == SEVERAL_NUMBERS_ON_PAGE else p for p in parts]
    return "; ".join(parts) or None, _SENTENCE_RE.sub(NOT_CONFIRMED_WHY, why) if why else why


async def page_numbers(engine, urls: list[str], concurrency: int = 10) -> dict[str, list[str] | None]:
    """The DE numbers on each page as it reads now; None when it does not open."""
    from enrichment.page_corroborator import _readable_text
    from vat.entity_match import vat_candidates

    sem = asyncio.Semaphore(concurrency)

    async def one(url):
        async with sem:
            flags: set[str] = set()
            try:
                page = await engine._pages._fetch(url, flags)
                if page is not None and not (page.ok and page.content):
                    page = await engine._pages.fetch_browser(url, flags)
            except Exception:  # noqa: BLE001 — a page that fails keeps its row as it was
                return url, None
            if page is None or not (page.ok and page.content):
                return url, None
            return url, vat_candidates(_readable_text(page.content))

    return dict(await asyncio.gather(*(one(u) for u in urls)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=r"C:\vatc\conflict")
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    os.environ["EVIDENCE_CACHE_DIR"] = args.cache_dir

    from openpyxl import load_workbook

    from config import get_settings
    from vat.lookup_web import WebSearchLookup
    from vat.review_columns import SEVERAL_NUMBERS_ON_PAGE
    from vat.vies_client import VIESClient

    wb = load_workbook(INPUT)
    sheets = [ws for ws in wb.worksheets if ws.title.endswith(" - Conflict")]
    urls = set()
    for ws in sheets:
        col = [c.value for c in ws[1]].index("VAT_Found_Source_URL") + 1
        urls |= {ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)}
    urls.discard(None)
    settings = replace(get_settings(), vat_vies_in_lookup=False)
    engine = WebSearchLookup(settings, VIESClient(settings))
    print(f"Opening {len(urls)} pages ...")
    found = asyncio.run(page_numbers(engine, sorted(urls)))

    counts: Counter = Counter()
    for ws in sheets:
        h = [c.value for c in ws[1]]
        while h and h[-1] is None:
            h.pop()
        if COLUMN not in h:
            cell = ws.cell(row=1, column=len(h) + 1, value=COLUMN)
            src = ws.cell(row=1, column=len(h))
            cell.font, cell.fill, cell.alignment, cell.border = (copy(src.font), copy(src.fill),
                                                                 copy(src.alignment), copy(src.border))
            h.append(COLUMN)
        col = {name: h.index(name) + 1
               for name in (COLUMN, "Issue_Flags", "VAT_Why", "VAT_Found_Source_URL", "VAT_Proposed")}
        for r in range(2, ws.max_row + 1):
            if not any(ws.cell(row=r, column=j).value is not None for j in range(1, len(h))):
                continue
            url = ws.cell(row=r, column=col["VAT_Found_Source_URL"]).value
            flags = ws.cell(row=r, column=col["Issue_Flags"]).value
            numbers = found.get(url)
            ws.cell(row=r, column=col[COLUMN], value=NOT_READABLE if numbers is None else "; ".join(numbers) or "none")
            if SEVERAL_NUMBERS_ON_PAGE not in str(flags or ""):
                counts["no several-numbers flag (unchanged)"] += 1
            elif numbers is None:
                counts["flag kept: page not readable now"] += 1
            elif len(numbers) >= 2:
                counts["flag kept: page shows 2+ numbers"] += 1
            elif numbers != [re.sub(r"\s", "", str(ws.cell(row=r, column=col["VAT_Proposed"]).value or ""))]:
                counts["flag kept: page shows no number or another one now"] += 1
            else:
                new_flags, new_why = one_number(flags, ws.cell(row=r, column=col["VAT_Why"]).value)
                ws.cell(row=r, column=col["Issue_Flags"], value=new_flags)
                ws.cell(row=r, column=col["VAT_Why"], value=new_why)
                counts[f"flag replaced: page shows {len(numbers)} number(s)"] += 1
        if ws.auto_filter.ref:
            ws.auto_filter.ref = ws.dimensions
    wb.save(OUT)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
