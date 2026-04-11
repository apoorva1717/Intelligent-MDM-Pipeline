"""Debug harness: run ONE record (UCSF / Sarah Chen) through the
pipeline with verbose logging so we can see exactly what each tier
fetches and what the LLM returns.

Usage:
    python3 scripts/debug_ucsf.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from api.models import EnrichmentRecord, EnrichmentOptions  # noqa: E402
from enrichment.orchestrator import Orchestrator  # noqa: E402
from config import Settings  # noqa: E402


async def main() -> None:
    records = [
        # Case 1: name1 needs cleanup (Stanford Uni → Stanford University),
        #         name2 needs canonicalisation (Radiology Dept → Department of Radiology)
        EnrichmentRecord(
            record_id="BSP_1000003",
            name1="Stanford Uni",
            name2="Radiology Dept",
            name3=None,
            contact="Dr. Michael Torres",
            street="300 Pasteur Dr",
            city="Stanford", state="CA", zip="94305", country="US",
        ),
        # Case 2: contact-based lookup — name2 null, must find via SERP
        EnrichmentRecord(
            record_id="BSP_1000002",
            name1="Univ of California San Francisco",
            name2=None,
            name3=None,
            contact="Prof. Sarah Chen",
            street="505 Parnassus Ave",
            city="San Francisco", state="CA", zip="94143", country="US",
        ),
        # Case 3: address fragments leaked into name1; name2 canonicalise
        EnrichmentRecord(
            record_id="BSP_1000006",
            name1="Johns Hopkins Hospital 600 N Wolfe St",
            name2="Dept Neurology",
            name3=None,
            contact="Prof. Robert Kim",
            street="600 N Wolfe St",
            city="Baltimore", state="MD", zip="21287", country="US",
        ),
        # Case 4: full trio — name1/name2/name3 with a user-supplied group
        EnrichmentRecord(
            record_id="BSP_1000011",
            name1="Stanford Univ Med Ctr",
            name2="Dept of Biochemistry",
            name3="Protein NMR Group",
            contact="Dr. Angela Morris",
            street="291 Campus Dr",
            city="Stanford", state="CA", zip="94305", country="US",
        ),
        # Case 5: no name2, no contact — name2 MUST remain null
        EnrichmentRecord(
            record_id="BSP_1000020",
            name1="MIT",
            name2=None,
            name3=None,
            contact=None,
            street="77 Massachusetts Ave",
            city="Cambridge", state="MA", zip="02139", country="US",
        ),
        # Case 6: name2 present, no contact — should canonicalise without SERP
        EnrichmentRecord(
            record_id="BSP_1000021",
            name1="Harvard University",
            name2="Chemistry Department",
            name3=None,
            contact=None,
            street="1 Oxford St",
            city="Cambridge", state="MA", zip="02138", country="US",
        ),
        # Case 7: Stanford subsidiary where name3 present & name2 has contact
        EnrichmentRecord(
            record_id="BSP_1000003B",
            name1="Stanford Medicine",
            name2="Department of Biochemistry",
            name3="Structural Biology Lab",
            contact=None,
            street="279 Campus Dr",
            city="Stanford", state="CA", zip="94305", country="US",
        ),
    ]

    orchestrator = Orchestrator(Settings())
    options = EnrichmentOptions(max_concurrency=1)

    results = await orchestrator.enrich_batch(records, options)
    print("\n" + "=" * 70)
    print("FINAL RESULTS:")
    print("=" * 70)
    for r in results.results:
        d = r.model_dump()
        print(f"\n-- {d['record_id']} --")
        print(f"  name1: {d['name1_original']!r} → {d['name1_enriched']!r}")
        print(f"  name2: {d['name2_original']!r} → {d['name2_enriched']!r}")
        print(f"  name3: {d['name3_original']!r} → {d['name3_enriched']!r}")
        print(f"  tier_used: {d['tier_used']}, source: {d['source']}, confidence: {d['confidence']}")
        print(f"  flag_for_review: {d['flag_for_review']}, reason: {d.get('flag_reason')}")


if __name__ == "__main__":
    asyncio.run(main())
