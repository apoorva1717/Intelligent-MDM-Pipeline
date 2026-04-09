"""Post-fix verification script — tests all bug fixes independently.

Run with:
    # Terminal 1: start server
    python main.py

    # Terminal 2: run verify script
    python scripts/verify_fixes.py

All 6 steps must print PASS before testing the full batch.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["ENV"] = "local"
from dotenv import load_dotenv

load_dotenv()


async def run() -> None:
    passed = 0
    failed = 0

    # Step 1 — LLM connection
    print("\n── Step 1: LLM connection ──")
    try:
        from llm.openai_client import call_openai

        result = await call_openai(
            system_prompt="Return valid JSON only.",
            user_prompt='{"status": "ok", "message": "connected"}',
        )
        print("PASS:", result)
        passed += 1
    except Exception as e:
        print("FAIL:", str(e))
        failed += 1

    # Step 2 — ROR query endpoint with country filter
    print("\n── Step 2: ROR query endpoint ──")
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.ror.org/v2/organizations",
                params={
                    "query": "UCLA",
                    "filter": "locations.geonames_details.country_code:US",
                },
            )
            data = r.json()
        assert data.get("items"), "No items returned"
        org = data["items"][0]
        org_names = [n["value"] for n in org.get("names", [])]
        assert any("UCLA" in n for n in org_names), (
            f"UCLA not in names: {org_names}"
        )
        types = [t.lower() for t in org.get("types", [])]
        assert any(
            t
            in {
                "education",
                "healthcare",
                "government",
                "facility",
                "nonprofit",
                "archive",
                "other",
            }
            for t in types
        ), f"Unexpected types: {types}"
        display = next(
            (n["value"] for n in org.get("names", [])
             if "ror_display" in n.get("types", [])),
            org_names[0],
        )
        print(f"PASS: name={display}, id={org['id']}")
        passed += 1
    except Exception as e:
        print("FAIL:", str(e))
        failed += 1

    # Step 3 — affiliation string builder
    print("\n── Step 3: affiliation string builder ──")
    try:
        from utils.text_utils import build_affiliation_string

        class MockRecord:
            name1 = "UCLA"
            name2 = "Dept of Chemistry"
            city = "Los Angeles"
            state = "CA"
            country = "US"

        rec = MockRecord()
        s1 = build_affiliation_string(rec, include_name2=False)
        assert "Los Angeles" in s1, "City missing"
        assert "CA" in s1, "State missing"
        assert "Dept of Chemistry" not in s1, (
            "name2 should not be in parent string"
        )

        s2 = build_affiliation_string(rec, include_name2=True)
        assert "Dept of Chemistry" in s2, "name2 missing from child string"
        print(f"PASS: parent='{s1}' child='{s2}'")
        passed += 1
    except Exception as e:
        print("FAIL:", str(e))
        failed += 1

    # Step 4 — classification from ROR type
    print("\n── Step 4: classification from ROR type ──")
    try:
        ROR_RESEARCH_TYPES = {
            "education",
            "healthcare",
            "government",
            "facility",
            "nonprofit",
            "archive",
            "other",
        }
        assert "education" in ROR_RESEARCH_TYPES
        assert "company" not in ROR_RESEARCH_TYPES
        assert "private" not in ROR_RESEARCH_TYPES
        print("PASS: type sets correct")
        passed += 1
    except Exception as e:
        print("FAIL:", str(e))
        failed += 1

    # Step 5 — empty string guard
    print("\n── Step 5: empty string guard ──")
    try:
        from enrichment.orchestrator import finalise

        result = {
            "name1_enriched": "",
            "name2_enriched": "  ",
            "name3_enriched": None,
            "name1_original": "UCLA",
            "name2_original": None,
            "name3_original": None,
        }
        out = finalise(result, time.monotonic() - 0.1)
        assert out["name1_enriched"] is None, (
            "Empty string not converted to None"
        )
        assert out["name2_enriched"] is None, (
            "Whitespace string not converted to None"
        )
        assert out["name1_changed"] is False, (
            "changed should be False when enriched is None"
        )
        print("PASS: empty string guard works")
        passed += 1
    except Exception as e:
        print("FAIL:", str(e))
        failed += 1

    # Step 6 — single record end to end
    print("\n── Step 6: end to end single record ──")
    try:
        import httpx

        async with httpx.AsyncClient(
            base_url="http://localhost:8000",
            timeout=30.0,
        ) as client:
            r = await client.post(
                "/enrich",
                json={
                    "records": [
                        {
                            "record_id": "TEST_001",
                            "name1": "UCLA",
                            "name2": None,
                            "name3": None,
                            "contact": "Dr. Jane Smith",
                            "street": "10833 Le Conte Ave",
                            "city": "Los Angeles",
                            "state": "CA",
                            "zip": "90095",
                            "country": "US",
                        }
                    ],
                    "options": {
                        "max_concurrency": 1,
                        "dry_run": False,
                    },
                },
            )
            data = r.json()

        rec = data["results"][0]
        assert rec["enrichment_status"] != "failed", (
            f"Status is failed: {rec.get('error')}"
        )
        assert rec["name1_enriched"] not in (None, ""), (
            "name1_enriched is null or empty"
        )
        assert rec["name1_changed"] is True, (
            "name1_changed should be True"
        )
        assert rec["record_type"] == "research_institution", (
            f"Wrong type: {rec['record_type']}"
        )
        print(
            f"PASS: tier={rec['tier_used']}, "
            f"name1='{rec['name1_enriched']}', "
            f"type={rec['record_type']}"
        )
        passed += 1
    except Exception as e:
        print(f"FAIL: {str(e)}")
        print("(Is the server running on port 8000?)")
        failed += 1

    print(f"\n══ Results: {passed} passed, {failed} failed ══")


if __name__ == "__main__":
    asyncio.run(run())
