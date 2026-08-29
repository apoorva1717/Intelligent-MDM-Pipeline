"""Ticket 19 - measure every query formulation against ROR and GLEIF.

For each record that currently leaves with NO registry identity, re-issue the
Tier 1 lookups with a different query string, holding *everything else*
constant: same ``call_ror`` / ``call_lei``, same gates, same thresholds, same
city/state/country context.  The only variable is the string.

Strategies (see ticket 19):

    control        the preprocessed name the pipeline sends today
    raw            Name 1 verbatim, no preprocessing
    expand_query   ``expand_abbreviations`` applied to the QUERY (today it is
                   applied only to the rescore list, never to the query)
    nosuffix       legal-form suffix stripped
    noloc          city/state/country tokens removed from the name
    name1_name2    Name 1 + Name 2 recombined (Stage-0 split records)
    slashfix       '/' replaced by a space (ROR returns HTTP 500 on '/')
    domain_first   ROR ``query.advanced=domains:"<resolved domain>"``

Adjudication is the ticket-15 domain-equality test, plus a second axis:

    correct        the returned org's own registered domain == the record's
                   resolved registrable domain
    wrong          both domains present and different
    unadjudicated  the record or the candidate has no domain

Writes ``tmp/q19/strategy_results.json``.  Registry calls go through
``utils.cache``, so a repeat run is free.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

OUT = _ROOT / ".scratch/agentic-enrichment/tmp/q19"


def registrable(domain):
    if not domain:
        return None
    d = str(domain).strip().lower().rstrip("/")
    if "//" in d:
        d = d.split("//", 1)[1]
    d = d.split("/")[0].split("?")[0]
    if d.startswith("www."):
        d = d[4:]
    parts = [p for p in d.split(".") if p]
    if len(parts) < 2:
        return None
    multi = {"co", "com", "org", "net", "gov", "ac", "edu"}
    if len(parts) >= 3 and parts[-2] in multi and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def adjudicate_domain(record_domain, candidate_domain):
    rd, cd = registrable(record_domain), registrable(candidate_domain)
    if not rd:
        return "unadjudicated_no_record_domain"
    if not cd:
        return "unadjudicated_no_candidate_domain"
    return "correct" if rd == cd else "wrong"


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def adjudicate_gleif(record_domain, legal_name):
    """GLEIF publishes no website, so domain equality is unavailable.  The
    weaker stand-in, stated as such: the brand label of the record's own
    resolved domain must appear inside the returned legal name.

    One direction only.  The reverse ("the legal name's head token appears
    inside the brand") accepted `ALLCHEMY INC` / allchemy.net -> `ALLCHEM, LLC`
    at GLEIF fuzzy 93.3, which is a different company; it is not used."""
    rd = registrable(record_domain)
    if not rd:
        return "unadjudicated_no_record_domain"
    if not legal_name:
        return "unadjudicated_no_candidate_domain"
    brand = _norm(rd.split(".")[0])
    ln = _norm(legal_name)
    if len(brand) < 3:
        return "unadjudicated_no_record_domain"
    return "correct" if brand in ln else "wrong"


async def ror_by_domain(client, domain):
    """The domain-first retrieval ROR supports but the pipeline never issues."""
    from utils.cache import cached_registry_get
    base = "https://api.ror.org/v2/organizations"
    params = {"query.advanced": 'domains:"%s"' % domain}

    async def _go():
        r = await client.get(base, params=params)
        r.raise_for_status()
        return r.json()

    try:
        data = await cached_registry_get("ror", base, params, _go)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "items": []}
    out = []
    for org in (data.get("items") or [])[:5]:
        names = [n["value"] for n in org.get("names", [])
                 if "ror_display" in n.get("types", [])]
        locs = org.get("locations") or []
        cc = None
        if locs:
            cc = ((locs[0] or {}).get("geonames_details") or {}).get("country_code")
        out.append({
            "ror_id": org.get("id"),
            "official_name": names[0] if names else None,
            "domains": org.get("domains") or [],
            "links": [l.get("value") for l in (org.get("links") or [])],
            "types": org.get("types") or [],
            "country_code": cc,
        })
    return {"items": out}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--strategies", default="")
    ap.add_argument("--corpus", default="")
    args = ap.parse_args()

    import httpx
    from enrichment.tier1_lei import call_lei
    from enrichment.tier1_ror import call_ror
    from llm.openai_client import resolve_tls_verify

    pop = json.loads((OUT / "population.json").read_text(encoding="utf-8"))
    lost = [d for d in pop if d["lost"] and d["control"]]
    if args.corpus:
        keep = set(args.corpus.split(","))
        lost = [d for d in lost if d["corpus"] in keep]
    for d in lost:
        d["key"] = "%s:%s" % (d["corpus"], d["idx"])
    print("lost records: %d" % len(lost))

    wanted = set(args.strategies.split(",")) if args.strategies else None

    # (strategy, record-index) -> query string
    jobs = []
    for i, d in enumerate(lost):
        for strat, q in d["variants"].items():
            if wanted and strat not in wanted:
                continue
            jobs.append((strat, i, q))
    print("string-query jobs: %d" % len(jobs))

    sem = asyncio.Semaphore(args.concurrency)
    results = {}

    async def run_one(strat, i, q):
        d = lost[i]
        async with sem:
            try:
                ror = await call_ror(q, country_code=d["cc"], country=d["country"],
                                     city=d["city"], state=d["state"])
            except Exception as exc:  # noqa: BLE001
                ror = {"matched": False, "exception": str(exc)}
            try:
                lei = await call_lei(q, country_code=d["cc"], city=d["city"],
                                     state=d["state"])
            except Exception as exc:  # noqa: BLE001
                lei = {"matched": False, "exception": str(exc)}
        results.setdefault(strat, {})[lost[i]["key"]] = {
            "query": q,
            "ror": {
                "matched": bool(ror.get("matched")),
                "ror_id": ror.get("ror_id"),
                "official_name": ror.get("official_name"),
                "domain": ror.get("domain"),
                "website": ror.get("website"),
                "score": ror.get("score"),
                "strategy": ror.get("strategy"),
                "country_code": ror.get("country_code"),
                "types": ror.get("org_types"),
                "error": ror.get("error") or ror.get("exception"),
            },
            "gleif": {
                "matched": bool(lei.get("matched")),
                "lei_id": lei.get("lei_id"),
                "legal_name": lei.get("legal_name"),
                "score": lei.get("score"),
                "strategy": lei.get("strategy"),
                "country": lei.get("country"),
                "error": lei.get("error") or lei.get("exception"),
            },
        }

    done = 0
    tasks = [run_one(s, i, q) for s, i, q in jobs]
    for chunk_start in range(0, len(tasks), 40):
        await asyncio.gather(*tasks[chunk_start:chunk_start + 40])
        done = min(chunk_start + 40, len(tasks))
        print("  ... %d/%d" % (done, len(tasks)), flush=True)

    # ---- domain-first -----------------------------------------------------
    if not wanted or "domain_first" in wanted:
        dom_recs = [(i, d) for i, d in enumerate(lost) if d["domain"]]
        print("domain-first jobs: %d" % len(dom_recs))
        async with httpx.AsyncClient(timeout=20.0, verify=resolve_tls_verify()) as cl:
            df = {}

            async def one_dom(i, d):
                async with sem:
                    df[d["key"]] = {"domain": d["domain"],
                                  **(await ror_by_domain(cl, d["domain"]))}

            for s in range(0, len(dom_recs), 20):
                await asyncio.gather(*(one_dom(i, d) for i, d in dom_recs[s:s + 20]))
                print("  ... %d/%d" % (min(s + 20, len(dom_recs)), len(dom_recs)),
                      flush=True)
        results["domain_first"] = df

    path = OUT / "strategy_results.json"
    blob = {"lost": {}, "results": {}}
    if path.exists():
        blob = json.loads(path.read_text(encoding="utf-8"))
    for d in lost:
        blob["lost"][d["key"]] = d
    for strat, per in results.items():
        blob["results"].setdefault(strat, {}).update(per)
    path.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    print("wrote strategy_results.json (%d lost, %d strategies)"
          % (len(blob["lost"]), len(blob["results"])))


if __name__ == "__main__":
    asyncio.run(main())
