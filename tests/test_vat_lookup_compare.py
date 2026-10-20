"""tools/vat_lookup_compare.py — outcome classification, totals and sheets.

The two lookup engines are fakes that propose a fixed value per Customer, so
every outcome class is exercised deterministically; VIES (the audit check) is
the scripted stub. The fixture is tests/fixtures/vat/VAT_TEST_EXPORT_30.xlsx.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from openpyxl import load_workbook

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vies_mock import ViesStub
from tools import vat_lookup_compare as compare_tool
from utils.cache import EvidenceCache
from vat.lookup import FoundResult
from vat.vies_client import VIESClient

FIXTURE = Path(__file__).parent / "fixtures" / "vat" / "VAT_TEST_EXPORT_30.xlsx"

# Rows 2–7: Google DE (2), Siemens (3), Bosch x2 (4, 5), Zalando (6), BASF (7).
A_ANSWERS = {
    "99000001": FoundResult("grounded", "FOUND_VERIFIED", value="DE811569869",
                            flags=("ATTRIBUTION_MATCH", "CANDIDATE:DE811569869|Google Germany GmbH|google.de|FOUND_VERIFIED")),
    "99000002": FoundResult("grounded", "FOUND_UNVERIFIED", value="DE129274235",  # another Siemens? fake
                            flags=("ATTRIBUTION_PARTIAL", "CANDIDATE:a", "CANDIDATE:b")),
    "99000003": FoundResult("grounded", "FOUND_VERIFIED", value="DE129274235",    # verified but wrong
                            flags=("ATTRIBUTION_MATCH",)),
    "99000004": FoundResult("grounded", "NOT_FOUND", flags=("NO_IMPRESSUM_PAGE",)),
    "99000005": FoundResult("grounded", "FOUND_UNVERIFIED", value="DE149145248",  # bad checksum
                            flags=("FORMAT_ERROR",)),
}
B_ANSWERS = {
    "99000001": FoundResult("llm_only", "LLM_GUESS:FORMAT_OK:VALID", value="DE811569869", evidence="high"),
    "99000002": FoundResult("llm_only", "LLM_GUESS:FORMAT_OK:VALID", value="DE811128135", evidence="high"),
    "99000003": FoundResult("llm_only", "LLM_GUESS:FORMAT_ERROR:NOT_CHECKED", value="DE811128136", evidence="medium"),
    "99000004": FoundResult("llm_only", "LLM_GUESS:FORMAT_OK:INVALID", value="DE136695976", evidence="low"),
    "99000005": FoundResult("llm_only", "LLM_GUESS:MISSING:NOT_CHECKED", value=None, evidence=None),
}


class _Fake:
    def __init__(self, mode, answers):
        self.mode, self.answers, self.seen = mode, answers, []

    async def lookup(self, inp):
        self.seen.append(inp.customer)
        return self.answers.get(inp.customer) or FoundResult(self.mode, "NOT_FOUND")

    def stats(self):
        return {"lookups": len(self.seen)}

    async def aclose(self):
        pass


@pytest_asyncio.fixture
async def report(tmp_path):
    settings = replace(get_settings(), vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                       vies_requester_vat="", cache_frozen=False)
    engines = {"grounded": _Fake("grounded", A_ANSWERS), "llm_only": _Fake("llm_only", B_ANSWERS)}

    def make_client():
        return VIESClient(settings, cache=EvidenceCache(tmp_path),
                          transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)

    wb = load_workbook(FIXTURE)
    result = await compare_tool.compare(
        wb, settings, rows=set(range(2, 8)), make_client=make_client,
        make_engine=lambda mode, s, c: engines[mode],
    )
    return wb, result, engines


class TestClassification:
    @pytest.mark.asyncio
    async def test_outcomes_per_row(self, report):
        _, result, _ = report
        rows = {e["sheet_row"]: e for e in result["rows"]}
        a = {r: rows[r]["grounded"]["outcome"] for r in rows}
        b = {r: rows[r]["llm_only"]["outcome"] for r in rows}
        assert a == {2: "CORRECT", 3: "WRONG_VIES_VALID", 4: "WRONG_VIES_VALID", 5: "WRONG_VIES_VALID",
                     6: "NULL", 7: "WRONG_FORMAT", 20: "NO_TRUTH"}
        assert b == {2: "CORRECT", 3: "WRONG_VIES_VALID", 4: "WRONG_FORMAT", 5: "WRONG_FORMAT",
                     6: "WRONG_PLAUSIBLE", 7: "NULL", 20: "NO_TRUTH"}

    @pytest.mark.asyncio
    async def test_mode_a_attribution_and_candidates(self, report):
        _, result, _ = report
        row3 = next(e for e in result["rows"] if e["sheet_row"] == 3)["grounded"]
        assert (row3["attribution"], row3["candidates"]) == ("ATTRIBUTION_PARTIAL", 2)

    @pytest.mark.asyncio
    async def test_gate_failed_wrong_values_get_an_audit_vies_check(self, report):
        _, result, _ = report
        row3 = next(e for e in result["rows"] if e["sheet_row"] == 3)["grounded"]
        assert row3["vies"] == "VALID"  # Mode A never asked; the audit did
        assert result["costs"]["audit"]["vies_checks"] >= 1


class TestTotals:
    @pytest.mark.asyncio
    async def test_each_customer_once(self, report):
        _, result, engines = report
        assert engines["grounded"].seen.count("99000003") == 1  # Bosch: 2 rows, 1 lookup
        a, b = result["totals"]["grounded"], result["totals"]["llm_only"]
        assert a["customers"] == b["customers"] == 5
        assert (a["CORRECT"], a["WRONG_VIES_VALID"], a["WRONG_FORMAT"], a["NULL"]) == (1, 2, 1, 1)
        assert (a["verified"], a["verified_but_wrong"]) == (2, 1)
        assert (b["CORRECT"], b["WRONG_VIES_VALID"], b["WRONG_PLAUSIBLE"], b["WRONG_FORMAT"], b["NULL"]) == (
            1, 1, 1, 1, 1)
        assert b["wrong_passes_format"] == 2

    @pytest.mark.asyncio
    async def test_correctness_by_confidence(self, report):
        _, result, _ = report
        c = result["confidence"]
        assert (c["high"]["customers"], c["high"]["CORRECT"], c["high"]["WRONG_VIES_VALID"]) == (2, 1, 1)
        assert (c["medium"]["WRONG_FORMAT"], c["low"]["WRONG_PLAUSIBLE"], c["null"]["NULL"]) == (1, 1, 1)


class TestWorkbook:
    @pytest.mark.asyncio
    async def test_sheets_and_untouched_data(self, report):
        wb, _, _ = report
        assert {"Comparison", "Cost", "Mode A rows", "Mode B rows"} <= set(wb.sheetnames)
        original = load_workbook(FIXTURE)["Data"]
        assert [r for r in wb["Data"].iter_rows(values_only=True)] == [
            r for r in original.iter_rows(values_only=True)]
        text = [c for r in wb["Comparison"].iter_rows(values_only=True) for c in r if c]
        assert any("EXPERIMENT" in str(c) for c in text)
        assert any("not an independent measure" in str(c) for c in text)
        header = next(r for r in wb["Comparison"].iter_rows(values_only=True) if r[0] == "Sheet row")
        for col in ("A attribution", "A candidates read", "B confidence", "B check digit ok", "B correct?"):
            assert col in header

    def test_parse_rows(self):
        assert compare_tool.parse_rows("2-4,9") == {2, 3, 4, 9}
        assert compare_tool.parse_rows(None) == set()
