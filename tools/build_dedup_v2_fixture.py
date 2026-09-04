"""Freeze the 200-row dedup stress workbook into a JSON test fixture.

Usage::

    python tools/build_dedup_v2_fixture.py
    python tools/build_dedup_v2_fixture.py --check   # regenerate + diff, no write

Source: ``docs/thesis/dedup_STRESS_200_v1_enriched_dedup.xlsx``, sheet ``Sheet``
— the enriched 200-record stress batch after one v1 ``/api/dedup/file`` run.
The workbook is untracked (it is thesis evidence, not source), so the fixture
is what the test suite reads; this script is how the fixture is regenerated
when the workbook changes.

What is split out
-----------------

The sheet carries the dedup run's own output in its five trailing columns
(``Cluster ID``, ``Routing``, ``LLM Flag``, ``Confidence``, ``Reasoning``).
Those are the v1 BASELINE, not input, so they go under ``"v1"`` — never under
``"rows"``, which must hold only what the adjudicator was given.

Two earlier columns, ``Reasoning`` (index 7) and ``Cluster ID`` (index 9), hold
a verbatim copy of that same output (verified cell-for-cell against the
trailing pair). They are dropped for the same reason: leaving them in ``rows``
would ship the answer inside the question. Neither binds to a ``DedupRow``
field (``api/routes.py:_DEDUP_HEADER_ALIASES``), so dropping them cannot change
an adjudication — it only stops the fixture lying about its provenance.

Blank cells are omitted per row, which is exactly what ``_parse_xlsx``
(api/routes.py:274-293) does with them: no key, so the ``DedupRow`` default
applies. Every value is stringified the way ``_parse_xlsx`` stringifies it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

WORKBOOK = _ROOT / "docs" / "thesis" / "dedup_STRESS_200_v1_enriched_dedup.xlsx"
SHEET = "Sheet"
FIXTURE = _ROOT / "tests" / "fixtures" / "dedup_v2_stress_200.json"

#: The dedup run's own output block, in sheet order. Split into ``"v1"``.
RESULT_COLUMNS = ["Cluster ID", "Routing", "LLM Flag", "Confidence", "Reasoning"]

#: Column indices holding a duplicate copy of the run's output (see module
#: docstring). Indices, not names, because the names collide with the trailing
#: result block.
MIRRORED_OUTPUT_INDICES = (7, 9)

#: The key column. "Customer" is what ``_DEDUP_HEADER_ALIASES`` maps to
#: ``row_id``, and it is the join key for the whole output workbook.
KEY_COLUMN = "Customer"


def _cell(value: Any) -> str:
    """Stringify one cell the way ``_parse_xlsx`` does (api/routes.py:282)."""
    if value is None:
        return ""
    return str(value).strip()


def build() -> dict[str, Any]:
    import openpyxl

    wb = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    ws = wb[SHEET]
    raw = ws.iter_rows(values_only=True)
    headers = [_cell(h) for h in next(raw)]

    result_start = len(headers) - len(RESULT_COLUMNS)
    if headers[result_start:] != RESULT_COLUMNS:
        raise SystemExit(
            f"expected the sheet to end with {RESULT_COLUMNS}; "
            f"found {headers[result_start:]}"
        )

    input_indices = [
        i
        for i in range(result_start)
        if headers[i] and i not in MIRRORED_OUTPUT_INDICES
    ]
    # A repeated header is not an error: ``_parse_xlsx`` builds a dict keyed by
    # header, so the last non-empty cell under a repeated name wins and the
    # column list is the de-duplicated one. Mirror that exactly rather than
    # inventing a shape the parser cannot produce. ("Central delivery block"
    # appears twice in this sheet; both copies are empty in all 200 rows.)
    input_columns = list(dict.fromkeys(headers[i] for i in input_indices))

    order: list[str] = []
    rows: dict[str, dict[str, str]] = {}
    v1: dict[str, dict[str, Any]] = {}

    for raw_row in raw:
        cells = list(raw_row)
        key = _cell(cells[headers.index(KEY_COLUMN)])
        if not key:
            continue
        if key in rows:
            raise SystemExit(f"duplicate {KEY_COLUMN} {key!r} — the fixture key is not unique")

        # Mirror check: the two copies of the output must agree, or the
        # assumption in the module docstring is wrong and the drop is unsafe.
        for mirror, canonical in zip(MIRRORED_OUTPUT_INDICES, (result_start + 4, result_start)):
            if _cell(cells[mirror]) != _cell(cells[canonical]):
                raise SystemExit(
                    f"row {key}: column {mirror} ({headers[mirror]!r}) is not a copy "
                    f"of column {canonical} — refusing to drop it"
                )

        order.append(key)
        rows[key] = {
            headers[i]: _cell(cells[i]) for i in input_indices if _cell(cells[i])
        }
        raw_flag = cells[result_start + 2]
        raw_conf = cells[result_start + 3]
        v1[key] = {
            "Cluster ID": _cell(cells[result_start]) or None,
            "Routing": _cell(cells[result_start + 1]) or None,
            "LLM Flag": bool(raw_flag) if raw_flag is not None else None,
            "Confidence": float(raw_conf) if raw_conf not in (None, "") else None,
            "Reasoning": _cell(cells[result_start + 4]) or None,
        }

    return {
        "source": {
            "workbook": str(WORKBOOK.relative_to(_ROOT)),
            "sheet": SHEET,
            "sha256": hashlib.sha256(WORKBOOK.read_bytes()).hexdigest(),
            "generated_by": "tools/build_dedup_v2_fixture.py",
            "note": (
                "rows = the adjudicator's input; v1 = the recorded v1 output of "
                "one /api/dedup/file run over exactly those rows."
            ),
        },
        "input_columns": input_columns,
        "order": order,
        "rows": rows,
        "v1": v1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild and compare against the committed fixture; write nothing",
    )
    args = parser.parse_args()

    built = build()
    text = json.dumps(built, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    if args.check:
        if not FIXTURE.exists():
            print(f"MISSING: {FIXTURE}")
            return 1
        current = FIXTURE.read_text(encoding="utf-8")
        if current != text:
            print(f"STALE: {FIXTURE} differs from a fresh build of {WORKBOOK.name}")
            return 1
        print(f"OK: {FIXTURE.relative_to(_ROOT)} matches {WORKBOOK.name}")
        return 0

    FIXTURE.write_text(text, encoding="utf-8")
    print(
        f"wrote {FIXTURE.relative_to(_ROOT)}: {len(built['rows'])} rows, "
        f"{len(built['input_columns'])} input columns"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
