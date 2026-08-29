"""Ticket 19 - adjudicate every strategy and compute the union.

Reads ``tmp/q19/strategy_results.json`` (+ ``wikidata_results.json`` when
present) and prints the per-strategy table the ticket asks for:
recovered / correct / wrong / unadjudicated, per registry and per record.

Adjudication:

* **ROR** - ticket 15's domain-equality test.  A hit is *correct* only when the
  returned organisation's own ROR-registered domain (``domains[]`` / ``links[]``
  reduced to the registrable domain) equals the record's resolved registrable
  domain.  Different -> *wrong*.  Either side missing -> *unadjudicated*, counted
  and reported separately, never folded into either column.
* **GLEIF** - GLEIF publishes no website, so domain equality is unavailable.
  The weaker stand-in used here is stated as such: the record's own domain brand
  label must appear inside the returned legal name (or the legal name's leading
  token inside the brand).
* **Second axis, ROR only** - does the returned organisation's registered
  country match the record's?  A domain-equal hit on a foreign sibling
  ("Shell Global Solutions Us Inc", Texas -> ``Shell (Netherlands)``) passes
  domain equality and still attaches a different legal entity.

The control is the *direct-drive* control (ticket 11's harness C/D):
``preprocess`` -> ``strip_address_fragments`` -> ``call_ror`` / ``call_lei``,
Stage 0 NOT run, exactly as ``drive_tier1.py`` documents.  Every delta is
measured record-by-record against it.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
OUT = _ROOT / ".scratch/agentic-enrichment/tmp/q19"

sys.path.insert(0, str(_ROOT / ".scratch/agentic-enrichment/scripts"))
from q19_strategies import adjudicate_domain, adjudicate_gleif, registrable  # noqa: E402

ORDER = ["control", "raw", "expand_query", "nosuffix", "noloc",
         "name1_name2", "slashfix", "wikidata", "domain_first"]

#: Hosts nobody's organisation "owns" - a resolved domain equal to one of these
#: cannot adjudicate anything, and domain-first must not query on it.
AGGREGATORS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "wikipedia.org",
    "instagram.com", "youtube.com", "bloomberg.com", "crunchbase.com",
    "glassdoor.com", "indeed.com", "zoominfo.com", "dnb.com", "yelp.com",
    "manta.com", "bbb.org", "mapquest.com", "google.com", "amazonaws.com",
}


def load():
    blob = json.loads((OUT / "strategy_results.json").read_text(encoding="utf-8"))
    lost, results = blob["lost"], blob["results"]

    wpath = OUT / "wikidata_results.json"
    if wpath.exists():
        wd = json.loads(wpath.read_text(encoding="utf-8"))
        conv = {}
        for k, v in wd.items():
            if k not in lost:
                continue
            conv[k] = {
                "query": v.get("query"),
                "ror": {"matched": bool(v.get("ror_id")), "ror_id": v.get("ror_id"),
                        "official_name": v.get("label"), "domain": v.get("website"),
                        "country_code": None, "strategy": "wikidata_crosswalk"},
                "gleif": {"matched": bool(v.get("lei_id")), "lei_id": v.get("lei_id"),
                          "legal_name": v.get("label"), "strategy": "wikidata_crosswalk"},
                "_outcome": v.get("outcome"),
            }
        results["wikidata"] = conv

    if "domain_first" in results and "items" in next(
            iter(results["domain_first"].values()), {}):
        conv = {}
        for k, v in results["domain_first"].items():
            items = v.get("items") or []
            want = registrable(v.get("domain"))
            # domain-first means "the org whose OWN registered domain is this
            # one".  ROR's advanced query is a text search and also returns
            # near-misses (`m3.com` for `3m.com`, `3m.com.pt` for `3m.com`), so
            # the equality is enforced here rather than trusted from the rank.
            top = None
            n_exact = 0
            for it in items:
                cand = {registrable(d) for d in (it.get("domains") or [])}
                cand |= {registrable(l) for l in (it.get("links") or [])}
                if want and want in cand:
                    n_exact += 1
                    if top is None:
                        top = it
            if want in AGGREGATORS:
                top = None          # nobody owns linkedin.com
            conv[k] = {
                "query": 'domains:"%s"' % v.get("domain"),
                "ror": {
                    "matched": bool(top),
                    "ror_id": (top or {}).get("ror_id"),
                    "official_name": (top or {}).get("official_name"),
                    "domain": ((top or {}).get("domains") or [None])[0],
                    "country_code": (top or {}).get("country_code"),
                    "strategy": "domain_advanced",
                    "n_items": len(items), "n_domain_exact": n_exact,
                },
                "gleif": {"matched": False},
            }
        results["domain_first"] = conv
    return lost, results


def verdicts(lost, per):
    """-> {key: (ror_verdict|None, gleif_verdict|None, record_verdict|None)}"""
    out = {}
    for key, r in per.items():
        rec = lost[key]
        rd = rec.get("domain")
        if rd in AGGREGATORS:
            rd = None               # an aggregator domain adjudicates nothing
        rv = gv = None
        if r["ror"].get("matched"):
            rv = adjudicate_domain(rd, r["ror"].get("domain") or r["ror"].get("website"))
        if r["gleif"].get("matched"):
            gv = adjudicate_gleif(rd, r["gleif"].get("legal_name"))
        vs = [v for v in (rv, gv) if v]
        # A record whose two registries disagree is NOT a recovery: both
        # values would be written, so the wrong one is written too.  It gets
        # its own bucket rather than being absorbed into "correct".
        has_c = "correct" in vs
        has_w = any(v == "wrong" for v in vs)
        if not vs:
            rec_v = None
        elif has_c and has_w:
            rec_v = "mixed"
        elif has_c:
            rec_v = "correct"
        elif has_w:
            rec_v = "wrong"
        else:
            rec_v = "unadjudicated"
        out[key] = (rv, gv, rec_v)
    return out


def table(lost, results):
    n = len(lost)
    with_dom = sum(1 for d in lost.values()
                   if d.get("domain") and d["domain"] not in AGGREGATORS)
    print("lost population: %d records" % n)
    print("   adjudicable (a resolved, non-aggregator domain): %d" % with_dom)
    print("   NOT adjudicable ................................: %d" % (n - with_dom))
    print()
    hdr = ("%-14s %8s %8s %8s %8s %8s %8s %8s"
           % ("strategy", "differs", "fires", "correct", "WRONG", "mixed",
              "ror-hit", "gleif-hit"))
    print(hdr)
    print("-" * len(hdr))
    allv = {}
    for strat in ORDER:
        per = results.get(strat)
        if not per:
            continue
        v = verdicts(lost, per)
        allv[strat] = v
        differs = sum(
            1 for k, r in per.items()
            if strat in ("domain_first", "wikidata")
            or (r.get("query") or "").strip().lower()
            != (lost[k]["control"] or "").strip().lower()
        )
        fires = [k for k, t in v.items() if t[2]]
        print("%-14s %8d %8d %8d %8d %8d %8d %8d" % (
            strat, differs, len(fires),
            sum(1 for k in fires if v[k][2] == "correct"),
            sum(1 for k in fires if v[k][2] == "wrong"),
            sum(1 for k in fires if v[k][2] == "mixed"),
            sum(1 for k, r in per.items() if r["ror"].get("matched")),
            sum(1 for k, r in per.items() if r["gleif"].get("matched")),
        ))
    return allv


def _is(v, k, want):
    t = v.get(k)
    return bool(t) and t[2] == want


def marginal(allv):
    ctl = allv.get("control", {})
    print()
    print("=== DELTA against the direct-drive control, per record ===")
    hdr = "%-14s %11s %11s %13s %6s" % (
        "strategy", "new-correct", "new-WRONG", "fixes-ctl-FP", "net")
    print(hdr)
    print("-" * len(hdr))
    rank = []
    for strat in ORDER:
        if strat == "control" or strat not in allv:
            continue
        v = allv[strat]
        keys = set(v) | set(ctl)
        new_c = [k for k in keys
                 if _is(v, k, "correct") and not _is(ctl, k, "correct")]
        new_w = [k for k in keys
                 if _is(v, k, "wrong") and not _is(ctl, k, "wrong")
                 and not _is(ctl, k, "correct")]
        fixed = [k for k in keys if _is(v, k, "correct") and _is(ctl, k, "wrong")]
        net = len(new_c) - len(new_w)
        rank.append((net, len(new_c), len(new_w), len(fixed), strat))
        print("%-14s %11d %11d %13d %6d"
              % (strat, len(new_c), len(new_w), len(fixed), net))
    print()
    print("ranked by (new-correct - new-wrong):")
    for net, c, w, f, strat in sorted(rank, reverse=True):
        print("   %-14s net=%+d  new-correct=%d  new-wrong=%d  fixes-control-FP=%d"
              % (strat, net, c, w, f))
    return {k for k, t in ctl.items() if t[2] == "correct"}, rank


def union(lost, allv, ctl_correct):
    print()
    print("=== UNION (best combination of the non-control strategies) ===")
    correct_by, wrong_by = defaultdict(list), defaultdict(list)
    for strat, v in allv.items():
        if strat == "control":
            continue
        for k, t in v.items():
            if t[2] == "correct":
                correct_by[k].append(strat)
            elif t[2] == "wrong":
                wrong_by[k].append(strat)
    n = len(lost)
    new_correct = {k for k in correct_by if k not in ctl_correct}
    only_wrong = {k for k in wrong_by if k not in correct_by and k not in ctl_correct}
    print("lost population ......................................... %d" % n)
    print("recovered CORRECTLY by at least one strategy ............ %d  (%.0f%% of lost)"
          % (len(new_correct), 100.0 * len(new_correct) / n))
    print("a strategy fires but every answer is domain-WRONG ....... %d" % len(only_wrong))
    adj = {k for k, d in lost.items()
           if d.get("domain") and d["domain"] not in AGGREGATORS}
    unadj = set(lost) - adj
    print()
    print("split by whether the record has a domain to adjudicate against:")
    print("   adjudicable ..... %3d lost -> %3d correct (%.0f%%), %3d only-wrong"
          % (len(adj), len(new_correct & adj),
             100.0 * len(new_correct & adj) / max(len(adj), 1),
             len(only_wrong & adj)))
    fired_unadj = {k for k in unadj
                   if any(allv[s].get(k, (None, None, None))[2]
                          for s in allv if s != "control")}
    print("   NOT adjudicable . %3d lost -> counted in NEITHER column; %d of them "
          "have a strategy that returns something (unverifiable)"
          % (len(unadj), len(fired_unadj)))
    print()
    print("greedy cumulative build (adds the strategy with the best marginal net):")
    covered_c, covered_w = set(ctl_correct), set()
    pool = [s for s in ORDER if s != "control" and s in allv]
    chosen = []
    while pool:
        best = None
        for s in pool:
            v = allv[s]
            nc = {k for k, t in v.items() if t[2] == "correct"} - covered_c
            nw = {k for k, t in v.items() if t[2] == "wrong"} - covered_c - covered_w
            score = len(nc) - len(nw)
            if best is None or score > best[0]:
                best = (score, s, nc, nw)
        score, s, nc, nw = best
        if score <= 0 and chosen:
            print("   (stop - no remaining strategy has a positive marginal net)")
            break
        chosen.append(s)
        covered_c |= nc
        covered_w |= nw
        pool.remove(s)
        print("   + %-14s net %+d -> cumulative correct %d, cumulative wrong %d"
              % (s, score, len(covered_c - ctl_correct), len(covered_w)))
    return new_correct, only_wrong


def per_corpus(lost, allv, ctl_correct):
    print()
    print("=== by corpus ===")
    correct_by, wrong_by = defaultdict(list), defaultdict(list)
    for strat, v in allv.items():
        if strat == "control":
            continue
        for k, t in v.items():
            if t[2] == "correct":
                correct_by[k].append(strat)
            elif t[2] == "wrong":
                wrong_by[k].append(strat)
    for c in ("A", "S2", "S3"):
        keys = [k for k, d in lost.items() if d["corpus"] == c]
        adj = [k for k in keys
               if lost[k].get("domain") and lost[k]["domain"] not in AGGREGATORS]
        nc = [k for k in keys if k in correct_by and k not in ctl_correct]
        ow = [k for k in keys if k in wrong_by and k not in correct_by
              and k not in ctl_correct]
        print("  %-3s lost=%-4d adjudicable=%-4d union-correct=%-4d only-wrong=%d"
              % (c, len(keys), len(adj), len(nc), len(ow)))


def country_axis(lost, results):
    print()
    print("=== SECOND AXIS: does the recovered ROR org sit in the record's country? ===")
    for strat in ORDER:
        per = results.get(strat)
        if not per:
            continue
        ok = bad = unk = 0
        examples = []
        for k, r in per.items():
            if not r["ror"].get("matched"):
                continue
            rd = lost[k].get("domain")
            if rd in AGGREGATORS:
                rd = None
            if adjudicate_domain(
                    rd, r["ror"].get("domain") or r["ror"].get("website")) != "correct":
                continue
            cc_rec = (lost[k].get("cc") or "").upper()
            cc_cand = (r["ror"].get("country_code") or "").upper()
            if not cc_rec or not cc_cand:
                unk += 1
            elif cc_rec == cc_cand:
                ok += 1
            else:
                bad += 1
                if len(examples) < 6:
                    examples.append("%s (%s) -> %s (%s)"
                                    % (lost[k]["control"], cc_rec,
                                       r["ror"].get("official_name"), cc_cand))
        if ok or bad or unk:
            print("  %-14s domain-correct ROR hits: same country %d, "
                  "DIFFERENT country %d, unknown %d" % (strat, ok, bad, unk))
            for e in examples:
                print("        %s" % e)


def domain_quality(lost, allv):
    """The provenance of the domains that domain-first depends on."""
    v = allv.get("domain_first")
    if not v:
        return
    print()
    print("=== how trustworthy is the domain domain-first keys on? ===")
    from collections import Counter
    c = Counter()
    for k, t in v.items():
        if t[2] != "correct":
            continue
        prov = (lost[k].get("domain_provenance") or "none")
        c[prov.rsplit(":", 1)[-1]] += 1
    print("   confidence tier of the resolved domain, over domain-first's "
          "domain-correct recoveries:")
    for tier, n in c.most_common():
        print("      %-14s %d" % (tier, n))


def samples(lost, results, allv, ctl_correct, strat, limit=30):
    v = allv.get(strat)
    if not v:
        return
    print()
    print("--- %s: records it decides that the control does not get right ---" % strat)
    shown = 0
    for k, t in sorted(v.items()):
        if t[2] not in ("correct", "wrong") or k in ctl_correct:
            continue
        r = results[strat][k]
        who = r["ror"].get("official_name") or r["gleif"].get("legal_name")
        cd = r["ror"].get("domain") or r["ror"].get("website")
        print("  [%-5s] %-3s %-40s dom=%-24s -> %-42s cand=%s"
              % (t[2][:5], lost[k]["corpus"], repr(lost[k]["control"])[:40],
                 str(lost[k].get("domain"))[:24], repr(who)[:42], registrable(cd)))
        shown += 1
        if shown >= limit:
            print("  ... (more not shown)")
            break


def main():
    lost, results = load()
    allv = table(lost, results)
    ctl_correct, rank = marginal(allv)
    union(lost, allv, ctl_correct)
    per_corpus(lost, allv, ctl_correct)
    country_axis(lost, results)
    domain_quality(lost, allv)
    for _, _, _, _, strat in sorted(rank, reverse=True)[:4]:
        samples(lost, results, allv, ctl_correct, strat)


if __name__ == "__main__":
    main()
