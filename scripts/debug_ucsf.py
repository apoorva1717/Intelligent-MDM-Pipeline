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
        # ── Reported issues only ───────────────────────────────────────
        EnrichmentRecord(
            record_id="ISSUE_1_harvard_med",
            name1="Harvard Medical School",
            city="Boston", state="MA", zip="02115", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_2_yale_med",
            name1="Yale School of Medicine",
            city="New Haven", state="CT", zip="06510", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_3_merck_co",
            name1="Merck & Co",
            city="Rahway", state="NJ", zip="07065", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_4_email_in_name3",
            name1="Genentech",
            name3="orders@gene.com",
            city="South San Francisco", state="CA", zip="94080", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_5_addr_with_suite",
            name1="Pfizer",
            name2="235 E 42nd St Suite 1200",
            city="New York", state="NY", zip="10017", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_6_addr_in_name3",
            name1="Scripps Research",
            name3="10550 N Torrey Pines Rd Bldg 4",
            city="La Jolla", state="CA", zip="92037", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_12_miami_cem",
            name1="University of Miami Miller School of Medicine",
            name2="Attn: Cem Murdun",
            city="Miami", state="FL", zip="33136", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_11_uf_darlene",
            name1="University of Florida",
            name2="Attn: Darlene Bailey",
            city="Gainesville", state="FL", zip="32611", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_10_uf_karen_cox",
            name1="University of Florida",
            name2="Attn: Karen Cox",
            city="Gainesville", state="FL", zip="32611", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_9_lakeland_email",
            name1="LAKELAND REGIONAL HEALTH",
            name2="EMAIL: APINVOICE@MYLRH.ORG",
            city="Lakeland", state="FL", zip="33805", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_8_ap_with_attn",
            name1="University of Florida",
            name2="Accounts Payable - ATTN: Christina Boske",
            city="Gainesville", state="FL", zip="32611", country="US",
        ),
        EnrichmentRecord(
            record_id="ISSUE_7_wisconsin_madison",
            name1="University of Wisconsin Madison",
            city="Madison", state="WI", zip="53706", country="US",
        ),
    ]
    _unused_full_records = [
        # UC 2/3/5: Stanford Uni + Radiology Dept — research, LLM canonical
        EnrichmentRecord(
            record_id="BSP_A_research_canonical",
            name1="Stanford Uni",
            name2="Radiology Dept",
            contact="Dr. Michael Torres",
            street="300 Pasteur Dr",
            city="Stanford", state="CA", zip="94305", country="US",
        ),
        # UC 4: UCSF + Sarah Chen, no name2 — contact SERP lookup
        EnrichmentRecord(
            record_id="BSP_B_contact_lookup",
            name1="Univ of California San Francisco",
            contact="Prof. Sarah Chen",
            street="505 Parnassus Ave",
            city="San Francisco", state="CA", zip="94143", country="US",
        ),
        # UC 9 + 5: address fragment in name1, name2 canonicalise
        EnrichmentRecord(
            record_id="BSP_C_address_leak",
            name1="Johns Hopkins Hospital 600 N Wolfe St",
            name2="Dept Neurology",
            contact="Prof. Robert Kim",
            street="600 N Wolfe St",
            city="Baltimore", state="MD", zip="21287", country="US",
        ),
        # name1/name2/name3 full trio, name3 retained
        EnrichmentRecord(
            record_id="BSP_D_full_trio",
            name1="Stanford Univ Med Ctr",
            name2="Dept of Biochemistry",
            name3="Protein NMR Group",
            contact="Dr. Angela Morris",
            street="291 Campus Dr",
            city="Stanford", state="CA", zip="94305", country="US",
        ),
        # UC 5 scope: lab should NOT be overwritten
        EnrichmentRecord(
            record_id="BSP_E_scope_lab",
            name1="Stanford University",
            name2="NMR Lab",
            city="Stanford", state="CA", zip="94305", country="US",
        ),
        # UC 5 scope: facility should NOT be overwritten
        EnrichmentRecord(
            record_id="BSP_F_scope_facility",
            name1="University of Florida",
            name2="Magnetic Resonance Facility",
            city="Gainesville", state="FL", zip="32611", country="US",
        ),
        # UC 6: Accounts Payable normalisation
        EnrichmentRecord(
            record_id="BSP_G_ap_normalise",
            name1="MIT",
            name2="AP Invoice Dept",
            city="Cambridge", state="MA", zip="02139", country="US",
        ),
        # UC 7 Pattern A: Attn: prefix
        EnrichmentRecord(
            record_id="BSP_H_attn_contact",
            name1="Harvard University",
            name2="ATTN: Alan Brown / 321-674-7433",
            city="Cambridge", state="MA", zip="02138", country="US",
        ),
        # UC 7 Pattern B1: title prefix in name field
        EnrichmentRecord(
            record_id="BSP_I_title_contact",
            name1="Yale University",
            name2="Prof. Kevin Zhang",
            city="New Haven", state="CT", zip="06520", country="US",
        ),
        # UC 8: email in name field
        EnrichmentRecord(
            record_id="BSP_J_email_in_name",
            name1="Princeton University",
            name2="Dept of Chemistry / jsmith@princeton.edu",
            city="Princeton", state="NJ", zip="08544", country="US",
        ),
        # UC 9: PO Box in name field
        EnrichmentRecord(
            record_id="BSP_K_pobox",
            name1="Caltech",
            name2="PO Box 118550",
            city="Pasadena", state="CA", zip="91125", country="US",
        ),
        # UC 2/3 company: abbreviated company name → LLM canonical
        EnrichmentRecord(
            record_id="BSP_L_company_abbr",
            name1="ADAMS Air HYDRAUL",
            city="Kansas City", state="MO", zip="64116", country="US",
        ),
        # UC 2/3 company: Pfizer abbreviation
        EnrichmentRecord(
            record_id="BSP_M_company_pfizer",
            name1="Pfizer",
            name2="Global Research",
            city="New York", state="NY", zip="10017", country="US",
        ),
        # No name2, no contact — name2 stays null
        EnrichmentRecord(
            record_id="BSP_N_bare_name1",
            name1="MIT",
            city="Cambridge", state="MA", zip="02139", country="US",
        ),
        # UC 0 overflow: clear split — should flag, no auto-correct
        EnrichmentRecord(
            record_id="BSP_O0_overflow",
            name1="Adams Air",
            name2="Hydraulics Inc",
            city="Kansas City", state="MO", zip="64116", country="US",
        ),
        # UC 0: two separate entities — should NOT flag
        EnrichmentRecord(
            record_id="BSP_O0_not_overflow",
            name1="Pfizer",
            name2="Global Research",
            city="New York", state="NY", zip="10017", country="US",
        ),
        # UC 0: long university name split across fields — should flag
        EnrichmentRecord(
            record_id="BSP_O0_long_name",
            name1="University of Texas Southwestern",
            name2="Medical Center",
            city="Dallas", state="TX", zip="75390", country="US",
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
        if d.get('name3_original') or d.get('name3_enriched'):
            print(f"  name3: {d['name3_original']!r} → {d['name3_enriched']!r}")
        if d.get('contact_original') or d.get('contact_enriched'):
            print(f"  contact: {d['contact_original']!r} → {d['contact_enriched']!r}")
        if d.get('email_original') or d.get('email_enriched'):
            print(f"  email: {d['email_original']!r} → {d['email_enriched']!r}")
        for slot in ("street1", "street2", "street3"):
            if d.get(f'{slot}_original') or d.get(f'{slot}_enriched'):
                print(f"  {slot}: {d[f'{slot}_original']!r} → {d[f'{slot}_enriched']!r}")
        print(f"  record_type: {d['record_type']}, tier: {d['tier_used']}, source: {d['source']}, conf: {d['confidence']}")
        print(f"  use_cases: {d.get('use_cases_triggered', [])}")
        if d.get('flag_for_review'):
            print(f"  flag: {d.get('flag_reason')}")


if __name__ == "__main__":
    asyncio.run(main())
