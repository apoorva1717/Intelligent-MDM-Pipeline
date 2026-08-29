"""Like-for-like: your S2/S3 sample workbooks, then vs now.

"then" = the values shipped IN docs/results/*_enriched.xlsx
"now"  = logs/compare/s{2,3}_now.json, the same 200 records through current code

Ground truth is the eval metadata the workbooks carry (record_type_hint).
Joined on row order, which is preserved by run_batch (`enrich_batch` returns
results in input order) and checked here rather than assumed.
"""
import json
from collections import Counter

import openpyxl

SETS = [
    ("S2", "docs/results/demo_S2_large_corporate_100_v1 (1)_enriched.xlsx",
     "logs/compare/s2_now.json"),
    ("S3", "docs/results/demo_S3_government_labs_100_v1 (1)_enriched.xlsx",
     "logs/compare/s3_now.json"),
]


def read_then(path):
    ws = openpyxl.load_workbook(path, read_only=True).active
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    ix = {h: i for i, h in enumerate(hdr)}
    out = []
    for r in it:
        if r[ix["Name 1"]] is None:
            continue
        out.append({
            "name1": r[ix["Name 1"]],
            "name2": r[ix["Name 2"]],
            "type": (r[ix["Record Type"]] or "").strip(),
            "domain": (r[ix["Domain"]] or "").strip(),
            "dept_domain": (r[ix["Department Domain"]] or "").strip(),
            "ror": (r[ix["ROR ID"]] or "").strip(),
            "lei": (r[ix["LEI ID"]] or "").strip(),
            "contact": (r[ix["Contact"]] or "").strip() if "Contact" in ix else "",
            "codes": [c.strip() for c in
                      str(r[ix["Flag Codes"]] or "").replace(",", ";").split(";")
                      if c.strip()],
            "review": bool(r[ix["Flag for Review"]]),
            "hint": (r[ix["record_type_hint"]] or "").strip(),
            "n2prov": (r[ix["Name 2 Provenance"]] or "").strip(),
        })
    return out


def read_now(path):
    rows = json.load(open(path, encoding="utf-8"))["results"]
    return [{
        "name1": r.get("name1_enriched"),
        "name2": r.get("name2_enriched"),
        "type": (r.get("record_type") or "").strip(),
        "domain": (r.get("domain") or "").strip(),
        "dept_domain": (r.get("department_domain") or "").strip(),
        "ror": (r.get("ror_id") or "").strip(),
        "lei": (r.get("lei_id") or "").strip(),
        "contact": (r.get("contact_enriched") or "").strip(),
        "codes": list(r.get("flag_codes") or []),
        "review": bool(r.get("flag_for_review")),
        "n2prov": (r.get("name2_provenance") or "").strip(),
    } for r in rows]


def pct(rows, pred):
    return sum(1 for r in rows if pred(r))


for tag, then_path, now_path in SETS:
    then = read_then(then_path)
    now = read_now(now_path)
    n = min(len(then), len(now))
    then, now = then[:n], now[:n]
    hints = [r["hint"] for r in then]

    print(f"\n{'='*74}\n{tag}  —  {n} records\n{'='*74}")
    rows = [
        ("registry identity (ROR or LEI)", lambda r: bool(r["ror"] or r["lei"])),
        ("  ROR id", lambda r: bool(r["ror"])),
        ("  LEI id", lambda r: bool(r["lei"])),
        ("domain", lambda r: bool(r["domain"])),
        ("department domain", lambda r: bool(r["dept_domain"])),
        ("name2 populated", lambda r: bool(r["name2"])),
        ("contact populated", lambda r: bool(r["contact"])),
        ("record_type decided", lambda r: r["type"] not in ("", "unknown")),
        ("flagged for review", lambda r: r["review"]),
    ]
    print(f"{'metric':34s} {'then':>6s} {'now':>6s} {'delta':>7s}")
    print("-" * 56)
    for label, pred in rows:
        b, a = pct(then, pred), pct(now, pred)
        mark = "" if a == b else ("  <<<" if a > b else "  >>>")
        print(f"{label:34s} {b:6d} {a:6d} {a-b:+7d}{mark}")

    # correctness against the label
    tb = sum(1 for r, h in zip(then, hints) if r["type"] == h)
    ta = sum(1 for r, h in zip(now, hints) if r["type"] == h)
    print(f"{'record_type EXACT vs hint':34s} {tb:6d} {ta:6d} {ta-tb:+7d}")

    # a decided value that is WRONG is worse than unknown
    wb = sum(1 for r, h in zip(then, hints)
             if r["type"] not in ("", "unknown") and r["type"] != h)
    wa = sum(1 for r, h in zip(now, hints)
             if r["type"] not in ("", "unknown") and r["type"] != h)
    print(f"{'  of which DECIDED BUT WRONG':34s} {wb:6d} {wa:6d} {wa-wb:+7d}")

    fb = sum(len(r["codes"]) for r in then)
    fa = sum(len(r["codes"]) for r in now)
    print(f"{'flag codes (total)':34s} {fb:6d} {fa:6d} {fa-fb:+7d}")

    print("\n  name2 provenance")
    for name, rr in (("then", then), ("now", now)):
        c = Counter((r["n2prov"] or "(empty)").split("+")[0] for r in rr)
        print(f"    {name:5s} {dict(c.most_common(6))}")

    print("\n  flag codes")
    cb, ca = Counter(), Counter()
    for r in then:
        cb.update(r["codes"])
    for r in now:
        ca.update(r["codes"])
    for code in sorted(set(cb) | set(ca), key=lambda k: -(cb.get(k, 0) + ca.get(k, 0))):
        print(f"    {code:32s} {cb.get(code,0):5d} -> {ca.get(code,0):5d}")

    # Where the remaining loss is
    print("\n  still unresolved (no ROR, no LEI, no verified domain)")
    stuck = [r for r in now
             if not (r["ror"] or r["lei"]) and ":verified" not in r["domain"]]
    print(f"    {len(stuck)} records")
    for r in stuck[:8]:
        print(f"      {str(r['name1'])[:44]:44s} type={r['type']:20s} dom={r['domain'][:24]}")
