"""Score an S2/S3 pair on the thesis metrics, and diff it against a baseline.

The metrics are the ones `merge_samples.py` appends and the ones the
then-vs-now note reports, computed the same way so the numbers are comparable:

  record_type   correct / wrong / undecided  vs the `record_type_hint` the
                source workbooks carry as ground truth. `undecided` stays
                distinct from `wrong` -- an honest abstention is not an error.
  identity      a ROR id or an LEI id
  domain        populated, and its provenance confidence
  name 2        populated, and its provenance confidence
  flags         records flagged, and total flag codes

Usage:
    python score_samples.py NEW_S2 NEW_S3 [--vs OLD_S2 OLD_S3]
"""
import argparse
import sys
from collections import Counter

import openpyxl

COLS = ("Name 1", "Name 2", "Record Type", "record_type_hint", "ROR ID",
        "LEI ID", "Domain", "Domain Provenance", "Name 2 Provenance",
        "Flag Codes")


def _conf(prov: str) -> str:
    for level in ("verified", "provisional", "low"):
        if f":{level}" in prov:
            return level
    return "-"


def score(path: str) -> Counter:
    ws = openpyxl.load_workbook(path, read_only=True).active
    it = ws.iter_rows(values_only=True)
    head = list(next(it))
    ix = {h: i for i, h in enumerate(head)}
    missing = [c for c in COLS if c not in ix]
    if missing:
        sys.exit(f"{path}: missing columns {missing}")

    t = Counter()
    for row in it:
        g = lambda c: (row[ix[c]] or "").strip() if isinstance(
            row[ix[c]], str) else (row[ix[c]] or "")
        if not g("Name 1"):
            continue
        t["rows"] += 1

        rtype, hint = g("Record Type"), g("record_type_hint")
        if rtype in ("", "unknown"):
            t["type.undecided"] += 1
        elif rtype == hint:
            t["type.correct"] += 1
        else:
            t["type.wrong"] += 1

        if g("ROR ID") or g("LEI ID"):
            t["identity"] += 1
        if g("Domain"):
            t["domain"] += 1
            t[f"domain.{_conf(g('Domain Provenance'))}"] += 1
        if g("Name 2"):
            t["name2"] += 1
            t[f"name2.{_conf(g('Name 2 Provenance'))}"] += 1

        codes = [c for c in g("Flag Codes").replace(";", ",").split(",")
                 if c.strip()]
        if codes:
            t["flagged"] += 1
        t["flag_codes"] += len(codes)
    return t


ROWS = [
    ("rows",              "records"),
    ("type.correct",      "record_type correct"),
    ("type.wrong",        "  ... decided but wrong"),
    ("type.undecided",    "  ... undecided"),
    ("identity",          "registry identity (ROR/LEI)"),
    ("domain",            "domain populated"),
    ("domain.verified",   "  ... verified"),
    ("domain.provisional","  ... provisional"),
    ("domain.low",        "  ... low"),
    ("name2",             "name 2 populated"),
    ("name2.verified",    "  ... verified"),
    ("name2.provisional", "  ... provisional"),
    ("name2.low",         "  ... low"),
    ("flagged",           "records flagged"),
    ("flag_codes",        "flag codes total"),
]


def table(title, new, old=None):
    print(f"\n## {title}\n")
    if old is None:
        print(f"| metric | value |\n|---|---:|")
        for key, label in ROWS:
            print(f"| {label} | {new[key]} |")
        return
    print("| metric | before | now | |\n|---|---:|---:|---|")
    for key, label in ROWS:
        d = new[key] - old[key]
        mark = "" if d == 0 else f"{d:+d}"
        print(f"| {label} | {old[key]} | {new[key]} | {mark} |")


ap = argparse.ArgumentParser()
ap.add_argument("new", nargs=2)
ap.add_argument("--vs", nargs=2)
a = ap.parse_args()

new = [score(p) for p in a.new]
old = [score(p) for p in a.vs] if a.vs else [None, None]

for tag, n, o in zip(("S2 — large corporate", "S3 — government labs"), new, old):
    table(tag, n, o)
table("ALL 200", sum(new, Counter()),
      sum(old, Counter()) if a.vs else None)
