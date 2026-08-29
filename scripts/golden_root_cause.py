"""Bucket the golden-set failures by the *shape* of the disagreement.

244 failing cells is a number nobody can act on. What a reviewer needs is
"these 43 are one defect, these 21 are the reference disagreeing with a
documented rule, these 9 need a human". This groups them by a set of detectors
applied in order, most specific first, so each failure lands in exactly one
bucket and the buckets can be counted.

A bucket is a *shape*, not a verdict. `abbreviation-direction` says the two
sides disagree about whether to expand `Ctr`; it does not say who is right —
that is written up per bucket in the report, against the documented rule.

    python scripts/golden_root_cause.py --eval logs/golden/golden_eval.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

#: Both directions of every pair the pipeline's own maps rewrite, plus the
#: name-side ones from `utils.text_utils.expand_abbreviations`. Used only to
#: recognise that two strings differ *by an abbreviation*, never to rewrite.
ABBREVIATION_PAIRS = [
    ("street", "st"), ("avenue", "ave"), ("boulevard", "blvd"),
    ("drive", "dr"), ("road", "rd"), ("lane", "ln"), ("court", "ct"),
    ("highway", "hwy"), ("parkway", "pkwy"), ("route", "rte"),
    ("north", "n"), ("south", "s"), ("east", "e"), ("west", "w"),
    ("northwest", "nw"), ("northeast", "ne"),
    ("southwest", "sw"), ("southeast", "se"),
    ("university", "univ"), ("department", "dept"), ("institute", "inst"),
    ("laboratory", "lab"), ("laboratories", "labs"), ("center", "ctr"),
    ("centre", "ctr"), ("medical", "med"), ("services", "svcs"),
    ("group", "grp"), ("national", "natl"), ("international", "intl"),
    ("administration", "admin"), ("management", "mgmt"),
    ("building", "bldg"), ("division", "div"), ("reference", "ref"),
]

LEGAL_FORMS = {
    "inc", "incorporated", "llc", "l.l.c.", "ltd", "limited", "corp",
    "corporation", "co", "company", "plc", "gmbh", "ag", "sa", "nv", "bv",
    "lp", "llp", "pllc", "pc",
}

_ABBREV_TO_FULL: dict[str, str] = {}
for _full, _short in ABBREVIATION_PAIRS:
    _ABBREV_TO_FULL[_short] = _full
    _ABBREV_TO_FULL[_full] = _full


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9&]+", text.casefold())


def strip_legal(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in LEGAL_FORMS]


def fold_abbreviations(tokens: list[str]) -> list[str]:
    return [_ABBREV_TO_FULL.get(t, t) for t in tokens]


def classify(expected: str, actual: str) -> str:
    """Which shape of disagreement this is. Most specific test first."""
    if not expected and actual:
        return "produced-a-value-the-reference-says-is-blank"
    if expected and not actual:
        return "produced-nothing-where-a-value-is-expected"

    exp_w, act_w = words(expected), words(actual)

    if exp_w == act_w:
        # Same words, so the difference is only how they are written.
        if expected.casefold() == actual.casefold():
            return "punctuation-or-spacing-only"
        return "casing-only"

    # A prefix: the produced value is the expected one cut short (or the
    # reverse). This is the column-width cut, and it is worth separating from
    # a genuine disagreement about the name.
    if act_w and exp_w[:len(act_w)] == act_w:
        return "truncated-produced-is-a-prefix-of-expected"
    if exp_w and act_w[:len(exp_w)] == exp_w:
        return "extended-produced-continues-past-expected"

    if strip_legal(exp_w) == strip_legal(act_w):
        return "legal-form-only"

    exp_f, act_f = fold_abbreviations(exp_w), fold_abbreviations(act_w)
    if exp_f == act_f:
        return "abbreviation-direction"
    if strip_legal(exp_f) == strip_legal(act_f):
        return "abbreviation-direction-and-legal-form"

    if set(exp_f) & set(act_f):
        return "same-entity-different-form"
    return "different-value-entirely"


ORDER = [
    "produced-nothing-where-a-value-is-expected",
    "produced-a-value-the-reference-says-is-blank",
    "truncated-produced-is-a-prefix-of-expected",
    "extended-produced-continues-past-expected",
    "casing-only",
    "punctuation-or-spacing-only",
    "legal-form-only",
    "abbreviation-direction",
    "abbreviation-direction-and-legal-form",
    "same-entity-different-form",
    "different-value-entirely",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", default="logs/golden/golden_eval.json")
    parser.add_argument("--column", default=None,
                        help="Only report this column.")
    parser.add_argument("--bucket", default=None,
                        help="Only report this bucket, with every example.")
    parser.add_argument("--examples", type=int, default=4)
    args = parser.parse_args()

    data = json.loads((_ROOT / args.eval).read_text(encoding="utf-8"))
    failures = [c for c in data["cells"] if c["verdict"] == "mismatch"]
    if args.column:
        failures = [c for c in failures if c["column"] == args.column]

    buckets: dict[str, list[dict]] = defaultdict(list)
    for cell in failures:
        cell["bucket"] = classify(cell["expected"], cell["actual"])
        buckets[cell["bucket"]].append(cell)

    if args.bucket:
        rows = buckets.get(args.bucket, [])
        print(f"{args.bucket}: {len(rows)}")
        for cell in rows:
            print(f"  [{cell['column']}] {cell['customer']}")
            print(f"     exp={cell['expected']!r}")
            print(f"     got={cell['actual']!r}")
        return

    print(f"{len(failures)} failing cells\n")
    print(f"{'bucket':46}{'n':>5}  columns")
    print("-" * 96)
    for name in ORDER:
        rows = buckets.get(name)
        if not rows:
            continue
        columns = Counter(c["column"] for c in rows)
        top = ", ".join(f"{k}({v})" for k, v in columns.most_common(4))
        print(f"{name:46}{len(rows):>5}  {top}")
    print("-" * 96)
    print(f"{'TOTAL':46}{len(failures):>5}")

    print("\n\nExamples")
    for name in ORDER:
        rows = buckets.get(name)
        if not rows:
            continue
        print(f"\n## {name}  ({len(rows)})")
        for cell in rows[: args.examples]:
            print(f"  [{cell['column']}] {cell['customer']}")
            print(f"     exp={cell['expected']!r}")
            print(f"     got={cell['actual']!r}")


if __name__ == "__main__":
    main()
