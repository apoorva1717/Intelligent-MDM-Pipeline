"""Ticket 19 supplement - GLEIF exact-vs-fuzzy, the name-side wins in detail,
the unadjudicable residue, and the corpus-A ceiling test."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / ".scratch/agentic-enrichment/scripts"))

from q19_report import AGGREGATORS, ORDER, load, verdicts  # noqa: E402
from q19_parent_test import axes  # noqa: E402
from q19_strategies import registrable  # noqa: E402


def main():
    lost, results = load()

    print("=== GLEIF: which phase produced the hit, per strategy ===")
    for strat in ORDER:
        per = results.get(strat)
        if not per:
            continue
        c = Counter(r["gleif"].get("strategy") for r in per.values()
                    if r["gleif"].get("matched"))
        if c:
            print("  %-14s %s" % (strat, dict(c)))
    print()

    print("=== every name-side (non-domain-first) decision the control misses ===")
    ctl = verdicts(lost, results["control"])
    ctl_correct = {k for k, t in ctl.items() if t[2] == "correct"}
    for strat in ORDER:
        if strat in ("control", "domain_first"):
            continue
        per = results.get(strat)
        if not per:
            continue
        v = verdicts(lost, per)
        rows = [(t[2], k) for k, t in v.items()
                if t[2] in ("correct", "wrong") and k not in ctl_correct]
        if not rows:
            continue
        print("  -- %s" % strat)
        for verdict, k in sorted(rows):
            r = per[k]
            who = r["ror"].get("official_name") or r["gleif"].get("legal_name")
            src = "ror" if r["ror"].get("matched") else "gleif"
            a, d = axes(lost[k]["control"], r["ror"].get("official_name") or "") \
                if src == "ror" else (None, "-")
            print("     [%-5s] %-3s %-38s q=%-38s -> %-38s (%s, dir=%s)"
                  % (verdict[:5], lost[k]["corpus"], repr(lost[k]["control"])[:38],
                     repr(r["query"])[:38], repr(who)[:38], src, d))
    print()

    print("=== the unadjudicable residue ===")
    unadj = {k: d for k, d in lost.items()
             if not d.get("domain") or d["domain"] in AGGREGATORS}
    print("  %d lost records have no resolved domain (or only an aggregator one),"
          % len(unadj))
    print("  so the domain-equality test cannot rule on them at all.")
    print("  by corpus: %s" % dict(Counter(d["corpus"] for d in unadj.values())))
    fired = []
    for strat in ORDER:
        if strat == "control":
            continue
        per = results.get(strat) or {}
        v = verdicts(lost, per)
        for k in unadj:
            if v.get(k, (None, None, None))[2]:
                fired.append((k, strat, per[k]))
    print("  strategies that return SOMETHING on them: %d" % len(fired))
    for k, strat, r in fired:
        who = r["ror"].get("official_name") or r["gleif"].get("legal_name")
        print("     %-3s %-38s [%s] -> %s"
              % (lost[k]["corpus"], repr(lost[k]["control"])[:38], strat, repr(who)))
    print("  These are reported here and counted in NEITHER the correct nor the "
          "wrong column.")
    print()

    print("=== corpus A - ticket 11's own corpus, the ceiling test ===")
    a_lost = {k: d for k, d in lost.items() if d["corpus"] == "A"}
    a_adj = {k for k, d in a_lost.items()
             if d.get("domain") and d["domain"] not in AGGREGATORS}
    print("  lost .................................. %d" % len(a_lost))
    print("  with a resolved, non-aggregator domain  %d" % len(a_adj))
    df = results.get("domain_first") or {}
    a_df = [k for k in a_adj if df.get(k, {}).get("ror", {}).get("matched")]
    print("  a ROR organisation registers that exact domain: %d" % len(a_df))
    hits = set()
    for strat in ORDER:
        if strat == "control":
            continue
        v = verdicts(lost, results.get(strat) or {})
        hits |= {k for k in a_lost if v.get(k, (None, None, None))[2] == "correct"}
    print("  recovered CORRECTLY by any strategy .... %d" % len(hits))
    for k in sorted(hits):
        for strat in ORDER:
            v = verdicts(lost, results.get(strat) or {})
            if v.get(k, (None, None, None))[2] == "correct":
                r = results[strat][k]
                who = r["ror"].get("official_name") or r["gleif"].get("legal_name")
                print("     %-34s dom=%-20s [%s] -> %s"
                      % (repr(lost[k]["control"])[:34], lost[k].get("domain"),
                         strat, repr(who)))
                break


if __name__ == "__main__":
    main()
