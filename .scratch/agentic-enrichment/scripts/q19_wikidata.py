"""Ticket 19 strategy 7 - the Wikidata crosswalk, run over the lost population.

Uses the real lane (``enrichment.wikidata.resolve`` with a real
``WikidataClient``), the real gauntlet and the real thresholds.  Counts how
many lost records reach a ``ror_id`` / ``lei_id`` *pointer* through Wikidata
that the ROR/GLEIF name searches never reached, and records the item's own
website so the pointer can be adjudicated by the same domain-equality test.

Writes ``tmp/q19/wikidata_results.json``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

OUT = _ROOT / ".scratch/agentic-enrichment/tmp/q19"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--corpus", default="")
    args = ap.parse_args()

    from config import get_settings
    from enrichment import wikidata as wd
    from utils.cache import build_evidence_cache, set_active_evidence_cache

    settings = get_settings()
    ev = build_evidence_cache(settings)
    set_active_evidence_cache(ev)
    cache = ev.namespace(
        "wikidata",
        directory=settings.wikidata_fixture_dir,
        replay_only=False,          # this probe is allowed to fetch
    )
    client = wd.WikidataClient(settings, cache=cache)

    pop = json.loads((OUT / "population.json").read_text(encoding="utf-8"))
    lost = [d for d in pop if d["lost"] and d["control"]]
    if args.corpus:
        keep = set(args.corpus.split(","))
        lost = [d for d in lost if d["corpus"] in keep]
    print("lost records: %d" % len(lost))

    sem = asyncio.Semaphore(args.concurrency)
    out = {}

    async def one(d):
        key = "%s:%s" % (d["corpus"], d["idx"])
        async with sem:
            try:
                res = await wd.resolve(
                    record_id=key, name=d["control"], city=d["city"],
                    region=d["state"], client=client,
                    threshold=settings.lei_name_match_threshold,
                )
            except Exception as exc:  # noqa: BLE001
                out[key] = {"outcome": "exception", "error": str(exc)}
                return
        item = res.item
        out[key] = {
            "outcome": res.outcome,
            "error": res.error,
            "query": res.query,
            "name_score": res.name_score,
            "reasons": sorted(res.reasons),
            "qid": item.qid if item else None,
            "label": item.label if item else None,
            "ror_id": item.ror_id if item else None,
            "lei_id": item.lei_id if item else None,
            "website": item.website if item else None,
        }

    todo = list(lost)
    for s in range(0, len(todo), 20):
        await asyncio.gather(*(one(d) for d in todo[s:s + 20]))
        print("  ... %d/%d" % (min(s + 20, len(todo)), len(todo)), flush=True)

    path = OUT / "wikidata_results.json"
    blob = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    blob.update(out)
    path.write_text(json.dumps(blob, indent=1), encoding="utf-8")

    from collections import Counter
    print(Counter(v["outcome"] for v in out.values()))
    print("with ror_id pointer:", sum(1 for v in out.values() if v.get("ror_id")))
    print("with lei_id pointer:", sum(1 for v in out.values() if v.get("lei_id")))


if __name__ == "__main__":
    asyncio.run(main())
