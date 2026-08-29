"""Ticket 19 - the union under BOTH adjudications.

Two verdicts per hit, both computed, never eyeballed:

**Lenient (ticket 15's criterion, as briefed)** - a hit counts when the
returned organisation's own registered domain equals the record's resolved
registrable domain.

**Strict** - the lenient test PLUS the requirement that the returned
organisation is the entity the record names rather than a broader one: the two
names must be token-equal (after ROR's bracketed qualifier is stripped and
US / U.S. / United States collapsed) or the record's tokens must be a strict
subset of ROR's (a truncated SAP name).  A parent, a sibling site or an
unrelated org registered on the same corporate domain fails.

The gap between the two numbers is the answer to "is the organisation in the
registry, or is its parent?".
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / ".scratch/agentic-enrichment/scripts"))

from q19_report import AGGREGATORS, ORDER, load, verdicts  # noqa: E402
from q19_parent_test import SAME, axes  # noqa: E402


def strict_verdict(lost, per, k, t):
    """t is (ror_verdict, gleif_verdict, record_verdict) from the lenient pass."""
    rv, gv, _ = t
    r = per[k]
    ok_ror = ok_gleif = False
    if rv == "correct":
        _a, d = axes(lost[k]["control"], r["ror"].get("official_name") or "")
        ok_ror = d in SAME
    if gv == "correct":
        # GLEIF's legal name gets the same entity test against the record name.
        _a, d = axes(lost[k]["control"], r["gleif"].get("legal_name") or "")
        ok_gleif = d in SAME
    wrong = (rv == "wrong") or (gv == "wrong")
    if (ok_ror or ok_gleif) and not wrong:
        return "correct"
    if rv or gv:
        return "wrong" if wrong or rv == "correct" or gv == "correct" else None
    return None


PARENT_OK = SAME | {"ror_broader"}


def parent_verdict(lost, per, k, t):
    """Intermediate reading: the record's own entity OR the parent company it
    is a site/division of ("ExxonMobil" for "ExxonMobil Refinery").  Whether
    that is acceptable is the parent-vs-child policy question ticket 11 already
    raised for the VA VISN networks; it is NOT a retrieval question."""
    rv, gv, _ = t
    r = per[k]
    ok = False
    if rv == "correct":
        _a, d = axes(lost[k]["control"], r["ror"].get("official_name") or "")
        ok = ok or d in PARENT_OK
    if gv == "correct":
        _a, d = axes(lost[k]["control"], r["gleif"].get("legal_name") or "")
        ok = ok or d in PARENT_OK
    wrong = (rv == "wrong") or (gv == "wrong")
    return "correct" if (ok and not wrong) else None


def main():
    lost, results = load()
    allv = {s: verdicts(lost, results[s]) for s in ORDER if s in results}
    ctl_len = {k for k, t in allv["control"].items() if t[2] == "correct"}
    ctl_str = {k for k, t in allv["control"].items()
               if strict_verdict(lost, results["control"], k, t) == "correct"}
    ctl_par = {k for k, t in allv["control"].items()
               if parent_verdict(lost, results["control"], k, t) == "correct"}

    print("%-14s %14s %14s %14s"
          % ("strategy", "lenient", "own-entity+parent", "own-entity only"))
    print("-" * 60)
    len_by, str_by, par_by = {}, {}, {}
    for s in ORDER:
        if s == "control" or s not in allv:
            continue
        v = allv[s]
        lc = {k for k, t in v.items() if t[2] == "correct"} - ctl_len
        sc = {k for k, t in v.items()
              if strict_verdict(lost, results[s], k, t) == "correct"} - ctl_str
        pc = {k for k, t in v.items()
              if parent_verdict(lost, results[s], k, t) == "correct"} - ctl_par
        len_by[s], str_by[s], par_by[s] = lc, sc, pc
        print("%-14s %14d %14d %14d" % (s, len(lc), len(pc), len(sc)))

    lenient = set().union(*len_by.values()) if len_by else set()
    strict = set().union(*str_by.values()) if str_by else set()
    parent = set().union(*par_by.values()) if par_by else set()
    n = len(lost)
    print()
    print("UNION over the %d currently-lost records" % n)
    print("   lenient (domain equality only, ticket 15's criterion) : %d  (%.0f%%)"
          % (len(lenient), 100.0 * len(lenient) / n))
    print("   own entity OR its parent company (policy call)        : %d  (%.0f%%)"
          % (len(parent), 100.0 * len(parent) / n))
    print("   strict  (the record's OWN entity only)                 : %d  (%.0f%%)"
          % (len(strict), 100.0 * len(strict) / n))
    print()
    for c in ("A", "S2", "S3"):
        keys = {k for k, d in lost.items() if d["corpus"] == c}
        print("   %-3s lost=%-4d lenient=%-4d own+parent=%-4d own-only=%d"
              % (c, len(keys), len(lenient & keys), len(parent & keys),
                 len(strict & keys)))
    print()
    print("the %d records the strict union recovers:" % len(strict))
    for k in sorted(strict):
        who = None
        for s in ORDER:
            if s == "control" or k not in str_by.get(s, ()):
                continue
            r = results[s][k]
            who = (s, r["ror"].get("official_name") or r["gleif"].get("legal_name"))
            break
        print("   %-3s %-40s dom=%-22s [%s] -> %s"
              % (lost[k]["corpus"], repr(lost[k]["control"])[:40],
                 str(lost[k].get("domain"))[:22], who[0], repr(who[1])))
    print()
    print("records the LENIENT union counts but the strict one does not "
          "(a parent / sibling / unrelated org on the same domain): %d"
          % len(lenient - strict))
    print(dict(Counter(lost[k]["corpus"] for k in (lenient - strict))))


if __name__ == "__main__":
    main()
