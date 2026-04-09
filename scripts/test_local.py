"""Local integration test script — runs the API and exercises all fixtures.

Usage:
    python scripts/test_local.py --mock       # no API keys needed
    python scripts/test_local.py --live       # uses real external APIs
    python scripts/test_local.py --fixture acronym_name1.json  # single fixture
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PORT = 8765
BASE_URL = f"http://localhost:{PORT}"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
EXPECTED_FILE = FIXTURES_DIR / "expected_outcomes.json"

# All fixture files (excluding expected_outcomes)
FIXTURE_FILES = [
    "research_missing_name2_with_contact.json",
    "research_wrong_name2_with_contact.json",
    "research_no_contact_name2_present.json",
    "company_with_name2.json",
    "acronym_name1.json",
    "fully_blank_name2_no_contact.json",
]


def start_server(mock: bool) -> threading.Thread:
    """Start FastAPI in a background thread."""
    os.environ["ENV"] = "local"
    if mock:
        os.environ["MOCK_EXTERNAL_CALLS"] = "true"
    else:
        os.environ.pop("MOCK_EXTERNAL_CALLS", None)

    def _run():
        import uvicorn
        uvicorn.run("api.app:app", host="0.0.0.0", port=PORT, log_level="warning")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def wait_for_health(timeout: int = 10) -> bool:
    """Poll /health until the server is ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    return False


def load_expected() -> dict:
    """Load expected outcomes."""
    with open(EXPECTED_FILE) as f:
        return json.load(f)


def run_fixture(fixture_name: str, expected: dict) -> tuple[bool, str, dict | None]:
    """Run a single fixture and compare against expected outcomes.

    Returns (passed, reason, result_data).
    """
    fixture_path = FIXTURES_DIR / fixture_name
    if not fixture_path.exists():
        return False, f"Fixture file not found: {fixture_name}", None

    with open(fixture_path) as f:
        payload = json.load(f)

    try:
        resp = requests.post(f"{BASE_URL}/enrich", json=payload, timeout=60)
    except requests.RequestException as e:
        return False, f"Request failed: {e}", None

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}", None

    data = resp.json()
    result = data["results"][0]

    # Derive fixture key (without .json extension)
    key = fixture_name.replace(".json", "")
    exp = expected.get(key)
    if not exp:
        return True, "No expected outcome defined — skipped validation", result

    # Compare fields
    failures: list[str] = []

    def _check(field: str, actual, expect):
        if actual != expect:
            failures.append(f"  {field}: got {actual!r}, expected {expect!r}")

    _check("tier_used", result.get("tier_used"), exp.get("expected_tier_used"))
    _check("tier2_mode", result.get("tier2_mode"), exp.get("expected_tier2_mode"))
    _check("contact_used", result.get("contact_used"), exp.get("expected_contact_used"))
    _check("flag_for_review", result.get("flag_for_review"), exp.get("expected_flag_for_review"))
    _check("enrichment_status", result.get("enrichment_status"), exp.get("expected_enrichment_status"))

    if exp.get("expected_name2_changed") is not None:
        _check("name2_changed", result.get("name2_changed"), exp["expected_name2_changed"])

    if failures:
        return False, "Field mismatches:\n" + "\n".join(failures), result

    return True, "All assertions passed", result


def print_table(rows: list[dict]) -> None:
    """Print a formatted results table."""
    headers = ["record_id", "type", "tier", "mode", "source", "conf", "status", "review"]
    widths = [16, 6, 4, 6, 12, 6, 10, 6]

    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    separator = "-+-".join("-" * w for w in widths)

    print(f"\n{header_line}")
    print(separator)

    for row in rows:
        values = [
            str(row.get("record_id", ""))[:16],
            ("inst" if row.get("record_type") == "research_institution" else "comp")[:6],
            str(row.get("tier_used", "-"))[:4],
            (row.get("tier2_mode", "-") or "-")[:6],
            (row.get("source", "-") or "-")[:12],
            (row.get("confidence", "-") or "-")[:6],
            (row.get("enrichment_status", "-") or "-")[:10],
            ("yes" if row.get("flag_for_review") else "no")[:6],
        ]
        print(" | ".join(v.ljust(w) for v, w in zip(values, widths)))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local integration test for enrichment API")
    parser.add_argument("--mock", action="store_true", help="Use mock external calls")
    parser.add_argument("--live", action="store_true", help="Use real external APIs")
    parser.add_argument("--fixture", type=str, help="Run a single fixture file")
    args = parser.parse_args()

    use_mock = not args.live  # Default to mock
    if args.mock:
        use_mock = True

    print(f"Starting server (mock={use_mock}) on port {PORT}...")
    start_server(use_mock)

    if not wait_for_health():
        print("ERROR: Server failed to start within 10 seconds")
        sys.exit(1)

    print("Server ready. Running fixtures...\n")

    expected = load_expected()
    fixtures = [args.fixture] if args.fixture else FIXTURE_FILES
    results_table: list[dict] = []
    pass_count = 0
    fail_count = 0

    for fixture_name in fixtures:
        passed, reason, result_data = run_fixture(fixture_name, expected)
        status_marker = "PASSED" if passed else "FAILED"
        print(f"  [{status_marker}] {fixture_name}")
        if not passed:
            print(f"    Reason: {reason}")
            fail_count += 1
        else:
            pass_count += 1

        if result_data:
            results_table.append(result_data)

    print_table(results_table)
    print(f"Results: {pass_count} passed, {fail_count} failed out of {len(fixtures)}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
