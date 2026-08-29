"""Ticket 11 — drive Tier 1 ROR/GLEIF directly over an SAP XLSX, offline.

The cheap generality check next to a full ``scripts/run_batch.py`` run: it
reproduces the *entry conditions* of the Tier 1 stage (preprocess ->
``strip_address_fragments`` -> ``country_to_iso_code``) exactly as
``orchestrator._enrich_one`` builds them, then calls the real ``call_ror`` /
``call_lei``. No LLM and no SERP, so it costs only registry calls.

Deviations from a full pipeline run, stated so the numbers are read correctly:

* ``llm_person_verdicts`` is empty (the orchestrator supplies LLM verdicts for
  *suspicious plain names* only; with none, preprocessing keeps its regex
  answer).
* Stage 0 (``overflow_check``) is not run.
* GLEIF is called on the same condition the orchestrator's company branch uses
  (``not looks_like_research_institution(name1_cleaned)``), but without ROR's
  own company/institution verdict feeding back in.

Run with ``FUNNEL_PROBE=true FUNNEL_PROBE_OUT=<path>`` and aggregate with
``aggregate_funnel.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    from api.routes import _parse_xlsx, _rows_to_records
    from enrichment.classifier import looks_like_research_institution
    from enrichment.preprocess import preprocess_record
    from enrichment.tier1_lei import call_lei
    from enrichment.tier1_ror import call_ror
    from utils.text_utils import country_to_iso_code, strip_address_fragments

    records = []
    for path in args.inputs:
        _headers, rows = _parse_xlsx(Path(path).read_bytes())
        records.extend(_rows_to_records(rows))
    print(f"{len(records)} records")

    sem = asyncio.Semaphore(args.concurrency)
    stats = {"ror_hit": 0, "lei_hit": 0, "gleif_called": 0, "skipped_blank": 0}

    async def one(rec) -> None:
        pre = preprocess_record(
            name1=rec.name1, name2=rec.name2, name3=rec.name3,
            name4=rec.name4, name5=rec.name5, contact=rec.contact,
            email=rec.email, street1=rec.street, street2=rec.street2,
            street3=rec.street3, street4=rec.street4, street5=rec.street5,
            house_number=rec.house_number, llm_person_verdicts={},
        )
        pp_name1 = (pre.name1 or "").strip()
        if not pp_name1:
            stats["skipped_blank"] += 1
            return
        name1_cleaned = strip_address_fragments(
            pp_name1, street=(pre.street1 or rec.street), city=rec.city,
            state=rec.state, zip_code=rec.zip,
        ) or pp_name1
        cc = country_to_iso_code(rec.country)
        async with sem:
            ror = await call_ror(
                name1_cleaned, country_code=cc, country=rec.country,
                city=rec.city, state=rec.state,
            )
            if ror.get("matched"):
                stats["ror_hit"] += 1
            if not looks_like_research_institution(name1_cleaned):
                stats["gleif_called"] += 1
                lei = await call_lei(
                    name1_cleaned, country_code=cc,
                    city=rec.city, state=rec.state,
                )
                if lei.get("matched"):
                    stats["lei_hit"] += 1

    await asyncio.gather(*(one(r) for r in records))
    print(stats)


if __name__ == "__main__":
    asyncio.run(main())
