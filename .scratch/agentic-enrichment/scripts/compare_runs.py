"""Before/after over the same 100 chemspeed records.

before = .scratch/agentic-enrichment/tmp/run100b.json  (12:07 today, post
         search-fix, pre tickets 17/20/21/24)
after  = logs/compare/after.json                       (current code)

Joins on (name1_original, city) -- the same key tools/run_diff.py uses -- so a
reordering cannot be read as a change.
"""
import json
import sys
from collections import Counter

BEFORE = ".scratch/agentic-enrichment/tmp/run100b.json"
AFTER = "logs/compare/after.json"


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    results = d["results"]
    inputs = d.get("inputs") or []
    rows = {}
    for i, r in enumerate(results):
        src = inputs[i] if i < len(inputs) else {}
        key = (
            (src.get("name1_original") or src.get("name1") or "").strip().lower(),
            (src.get("city") or r.get("city") or "").strip().lower(),
        )
        rows[key] = r
    return d["summary"], rows


sb, before = load(BEFORE)
sa, after = load(AFTER)
shared = sorted(set(before) & set(after))
print(f"before {len(before)}  after {len(after)}  joined {len(shared)}\n")


def rate(rows, pred):
    return sum(1 for k in shared if pred(rows[k]))


def has(r, field):
    return bool((r.get(field) or "").strip()) if isinstance(r.get(field), str) \
        else bool(r.get(field))


METRICS = [
    ("registry identity (ROR or LEI)", lambda r: has(r, "ror_id") or has(r, "lei_id")),
    ("  ROR id", lambda r: has(r, "ror_id")),
    ("  LEI id", lambda r: has(r, "lei_id")),
    ("domain", lambda r: has(r, "domain")),
    ("department_domain", lambda r: has(r, "department_domain")),
    ("name2 populated", lambda r: has(r, "name2_enriched")),
    ("record_type known", lambda r: (r.get("record_type") or "") not in ("", "unknown")),
    ("  = company", lambda r: r.get("record_type") == "company"),
    ("  = research_institution", lambda r: r.get("record_type") == "research_institution"),
    ("flagged for review", lambda r: bool(r.get("flag_for_review"))),
]

print(f"{'metric':36s} {'before':>7s} {'after':>7s} {'delta':>7s}")
print("-" * 61)
for label, pred in METRICS:
    b, a = rate(before, pred), rate(after, pred)
    mark = "" if b == a else ("  <<<" if a > b else "  >>>")
    print(f"{label:36s} {b:7d} {a:7d} {a - b:+7d}{mark}")

# record_type source -- ticket 17's lane, read off provenance
print("\nrecord_type provenance")
for name, rows in (("before", before), ("after", after)):
    c = Counter((rows[k].get("record_type_provenance") or "-") for k in shared)
    print(f"  {name:6s} {dict(c.most_common())}")

# Per-record record_type changes
print("\nrecord_type changes")
changes = []
for k in shared:
    b, a = before[k].get("record_type"), after[k].get("record_type")
    if b != a:
        changes.append((k[0], b, a, after[k].get("record_type_provenance")))
for name, b, a, prov in changes[:25]:
    print(f"  {name[:44]:44s} {str(b):20s} -> {str(a):20s} {prov}")
print(f"  ({len(changes)} records changed type)")

# Name 2 changes
print("\nname2 changes")
n2 = []
for k in shared:
    b = (before[k].get("name2_enriched") or "").strip()
    a = (after[k].get("name2_enriched") or "").strip()
    if b != a:
        n2.append((k[0], b, a))
for name, b, a in n2[:20]:
    print(f"  {name[:38]:38s} {b[:30]!r:32s} -> {a[:30]!r}")
print(f"  ({len(n2)} records changed name2)")

# Any name1 change is worth seeing -- none of today's tickets should move it.
print("\nname1 changes (none expected)")
n1 = [(k[0], (before[k].get("name1_enriched") or ""), (after[k].get("name1_enriched") or ""))
      for k in shared
      if (before[k].get("name1_enriched") or "") != (after[k].get("name1_enriched") or "")]
for name, b, a in n1[:15]:
    print(f"  {name[:38]:38s} {b[:32]!r:34s} -> {a[:32]!r}")
print(f"  ({len(n1)} records changed name1)")

print("\nsummary counters")
for key in ("tier1_resolved", "tier1_lei_count", "tier3_count", "enriched",
            "evidence_network_calls", "evidence_cache_hits",
            "domain_from_registry", "domain_from_serp", "page_reads_attempted",
            "wikidata_matched", "routing_type_mismatch_count"):
    b, a = sb.get(key), sa.get(key)
    if b != a:
        print(f"  {key:32s} {b} -> {a}")
