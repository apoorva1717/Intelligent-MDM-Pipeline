"""Record the Wikidata lane's fixtures for a workbook, serially and politely.

The lane's live calls are the part of a thesis batch that is rate-limited: the
first 100-row run at concurrency 3 took `HTTP 429` on 28 of 68 invocations.
Recording the fixtures **first**, one request at a time with a pause between
them, turns the measured run into a fixture replay — no rate limiting, no
network variance, and a re-run that reproduces the same matching decisions
rather than re-litigating them against whatever Wikidata says today. That is
the same argument `PAGE_FIXTURE_DIR` already makes for page reads.

**Every** row's Name 1 is warmed, not only the rows the lane would reach. The
script cannot know which records ROR and GLEIF will resolve without running the
pipeline, and over-recording is the cheaper error: the surplus fixtures are
never read, whereas a missing one costs a live call inside the measured run.
Anything already on disk is skipped, so re-running this is cheap and fills only
the gaps a rate-limited run left behind.

Usage::

    python scripts/wikidata_warm_fixtures.py docs/thesis/chemspeed_us_100.xlsx
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Input XLSX (SAP columns).")
    ap.add_argument("--pause", type=float, default=2.0,
                    help="Seconds between live requests (default 2).")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from api.routes import _parse_xlsx, _rows_to_records  # noqa: E402
    from config import Settings  # noqa: E402
    from enrichment.wikidata import WikidataClient, resolve  # noqa: E402
    from utils.cache import PageCache  # noqa: E402
    from utils.text_utils import strip_address_fragments  # noqa: E402

    settings = Settings()
    cache = PageCache(
        (settings.wikidata_fixture_dir or "").strip() or None,
        prefix="wikidata",
    )
    client = WikidataClient(settings, cache=cache)

    _, rows = _parse_xlsx(Path(args.input).read_bytes())
    records = _rows_to_records(rows)
    if args.limit:
        records = records[: args.limit]

    warmed = live = 0
    for record in records:
        name = (record.name1 or "").strip()
        if not name:
            continue
        # The same cleanup Tier 1 applies before it queries, so the fixture is
        # keyed on the string the lane will actually ask for.
        query = strip_address_fragments(
            name, street=record.street, city=record.city,
            state=record.state, zip_code=record.zip,
        ) or name
        before = client.calls
        try:
            outcome = await resolve(
                record_id=record.record_id or "-", name=query,
                city=record.city, region=record.state, client=client,
                threshold=settings.lei_name_match_threshold,
            )
        except Exception as exc:  # noqa: BLE001 — warming must not abort
            print(f"  !! {query[:50]:50s} {type(exc).__name__}: {exc}")
            continue
        spent = client.calls - before
        warmed += 1
        if spent:
            live += 1
            print(f"  {query[:50]:50s} {outcome.outcome:12s} ({spent} calls)")
            time.sleep(args.pause)

    print(f"\n{warmed} queries warmed, {live} needed the network, "
          f"{client.calls} live calls total")


if __name__ == "__main__":
    asyncio.run(_main())
