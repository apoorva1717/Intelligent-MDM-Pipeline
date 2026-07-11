"""Tests for the deterministic scoring + golden-record election.

Offline pytest — no network, no LLM. Drives ``score_row`` /
``elect_golden_records`` directly for the arithmetic, the routes via httpx
for the HTTP contract, and ``score_workbook`` for the XLSX round-trip.

UNCONFIRMED values flagged in the model (verify with Bernd before go-live):
- combined_presence_bonus = 10 (Bernd gave no number)
- sales_order_partner_count tiers (assumed to mirror sales_order_count)
- the tie-break ordering (score, last_order_year, equipment_count,
  company_code_count, lowest row_id)
"""

from __future__ import annotations

import io
import os
import random
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from dedup.scoring import (
    DuplicateRowIdError,
    ScoringRow,
    derived_counts,
    elect_golden_records,
    load_weights,
    score_row,
)
from dedup.scoring_xlsx import (
    BREAKDOWN_COLUMNS,
    score_workbook,
)


WEIGHTS = load_weights()


def _score(field_values: dict) -> tuple[dict, list]:
    row = ScoringRow(row_id="1", **field_values)
    return score_row(row, WEIGHTS)


def _points(field_values: dict, criterion: str) -> int:
    breakdown, _ = _score(field_values)
    return breakdown[criterion]


def _by_row(results):
    return {r.row_id: r for r in results}


# ---------------------------------------------------------------------------
# Band boundaries
# ---------------------------------------------------------------------------

class TestBands:
    @pytest.mark.parametrize("year,expected", [
        (2026, 20), (2025, 15), (2024, 10), (2023, 5),
        (2022, 0), (2021, 0), (None, 0),
    ])
    def test_sales_order_last_used(self, year, expected):
        assert _points({"last_order_year": year}, "sales_order_last_used") == expected

    @pytest.mark.parametrize("count,expected", [
        (0, 5), (5, 5), (6, 15), (10, 15), (11, 25), (100, 25), (None, 0),
    ])
    def test_sales_order_count(self, count, expected):
        assert _points({"order_count": count}, "sales_order_count") == expected

    @pytest.mark.parametrize("year,expected", [
        (2026, 20), (2025, 15), (2024, 10), (2023, 5), (2020, 0), (None, 0),
    ])
    def test_partner_last_used(self, year, expected):
        assert _points(
            {"partner_last_order_year": year}, "sales_order_partner_last_used"
        ) == expected

    # UNCONFIRMED: partner count tiers mirror sales_order_count. CONFIRM w/ Bernd.
    @pytest.mark.parametrize("count,expected", [
        (0, 5), (5, 5), (6, 15), (10, 15), (11, 25), (None, 0),
    ])
    def test_partner_count(self, count, expected):
        assert _points(
            {"partner_order_count": count}, "sales_order_partner_count"
        ) == expected

    @pytest.mark.parametrize("count,expected", [
        (0, 5), (3, 5), (4, 12), (8, 12), (9, 20), (15, 20), (16, 30), (None, 0),
    ])
    def test_equipment_count(self, count, expected):
        assert _points({"equipment_count": count}, "equipment_count") == expected

    @pytest.mark.parametrize("band,expected", [
        ("No", 15), ("3-4", 5), (">5", 0), (None, 0),
    ])
    def test_sleeping_bands(self, band, expected):
        assert _points({"sleeping_band": band}, "sleeping_customer") == expected

    @pytest.mark.parametrize("status,expected", [
        ("active", 10), ("blocked", 0), (None, 0),
    ])
    def test_customer_status(self, status, expected):
        assert _points({"customer_status": status}, "customer_status") == expected

    @pytest.mark.parametrize("group,expected", [
        ("DRIT", 20), ("0002", 15), ("SHIP2", 15), ("0003", 10), ("0004", 10),
        ("0005", 5), ("MLIEF", 5),
        ("DBRU", 0),  # parked -> 0 (explicit anything-else band)
        ("XXXX", 0), (None, 0),
    ])
    def test_account_group(self, group, expected):
        assert _points({"account_group": group}, "account_group") == expected

    @pytest.mark.parametrize("codes,expected", [
        (None, 0),                       # 0 codes -> 0
        ("1001", 5),                     # 1 -> 5
        ("1001;1002", 15),               # 2 -> 15
        ("1001;1002;1003;1004", 15),     # company-code edge: 4 sits in the 15 band
        ("1001;1002;1003;1004;1005", 25),  # 5 -> 25
    ])
    def test_company_code_count(self, codes, expected):
        assert _points(
            {"company_code_consolidated": codes}, "company_code_count"
        ) == expected

    # UNCONFIRMED bonus value (10); sales org has no standalone tier.
    def test_combined_presence_bonus(self):
        assert _points(
            {"company_code_consolidated": "1001", "sales_org_consolidated": "2001"},
            "combined_presence_bonus",
        ) == 10
        assert _points(
            {"company_code_consolidated": "1001"}, "combined_presence_bonus"
        ) == 0
        assert _points(
            {"sales_org_consolidated": "2001"}, "combined_presence_bonus"
        ) == 0

    def test_salesforce_instances_x10_non_empty_only(self):
        breakdown, _ = _score({
            "salesforce_ids": ["a", None, "", "  ", "b", None, "c", None],
        })
        assert breakdown["salesforce_instance_count"] == 30


# ---------------------------------------------------------------------------
# Coercion — permissive, never raises
# ---------------------------------------------------------------------------

class TestCoercion:
    def test_all_none_scores_zero_no_exception(self):
        breakdown, warnings = _score({})
        assert sum(breakdown.values()) == 0
        assert warnings == []
        # Every criterion key is present for a column-stable audit trail.
        assert set(breakdown) == set(BREAKDOWN_COLUMNS)

    def test_unrecognized_enums_warn_not_422(self):
        breakdown, warnings = _score({
            "sleeping_band": "Yes", "customer_status": "n/a",
        })
        assert breakdown["sleeping_customer"] == 0
        assert breakdown["customer_status"] == 0
        assert any("sleeping_band 'Yes' unrecognized" in w for w in warnings)
        assert any("customer_status 'n/a' unrecognized" in w for w in warnings)

    @pytest.mark.parametrize("status", [" Active ", "ACTIVE", "active"])
    def test_status_whitespace_case_variants(self, status):
        assert _points({"customer_status": status}, "customer_status") == 10

    @pytest.mark.parametrize("band", ["no", "NO", " No "])
    def test_sleeping_case_variants(self, band):
        assert _points({"sleeping_band": band}, "sleeping_customer") == 15

    def test_excel_float_hits_year_band(self):
        assert _points({"last_order_year": 2026.0}, "sales_order_last_used") == 20
        assert _points({"last_order_year": "2026.0"}, "sales_order_last_used") == 20

    def test_non_numeric_scores_zero_with_warning(self):
        breakdown, warnings = _score({"order_count": "lots"})
        assert breakdown["sales_order_count"] == 0
        assert any("order_count" in w for w in warnings)

    def test_absence_is_not_activity(self):
        # Absent status / sleeping never default into a scoring band.
        breakdown, _ = _score({})
        assert breakdown["customer_status"] == 0
        assert breakdown["sleeping_customer"] == 0


# ---------------------------------------------------------------------------
# Derived counts
# ---------------------------------------------------------------------------

class TestDerivedCounts:
    @pytest.mark.parametrize("value,expected", [
        (None, 0), ("", 0), ("1003;1017;1042", 3),
        ("1003;1017;", 2),   # trailing ";" ignored
        (" 1003 ; ;1017", 2),
    ])
    def test_company_code_split(self, value, expected):
        row = ScoringRow(row_id="1", company_code_consolidated=value)
        assert derived_counts(row)[0] == expected

    def test_sales_org_and_sf_counts(self):
        row = ScoringRow(
            row_id="1",
            sales_org_consolidated="2001;2002",
            salesforce_ids=[None, "x", "", "y", None, None, None, None],
        )
        assert derived_counts(row) == (0, 2, 2)


# ---------------------------------------------------------------------------
# Election
# ---------------------------------------------------------------------------

def _cluster_row(row_id, cluster_id="C1", **kw):
    return ScoringRow(row_id=row_id, cluster_id=cluster_id, **kw)


class TestElection:
    def test_highest_score_wins(self):
        results = elect_golden_records([
            _cluster_row("1", last_order_year=2026, order_count=20),   # 20+25
            _cluster_row("2", last_order_year=2023),                   # 5
        ], WEIGHTS)
        by = _by_row(results)
        assert by["1"].is_golden_record is True
        assert by["1"].golden_record_id == "1"
        assert by["2"].is_golden_record is False
        assert by["2"].golden_record_id == "1"
        assert by["1"].election_status == "proposed"
        assert by["2"].election_status == "proposed"

    def test_unique_row_self_references(self):
        results = elect_golden_records(
            [ScoringRow(row_id="9", cluster_id=None)], WEIGHTS
        )
        (r,) = results
        assert r.is_golden_record is True
        assert r.golden_record_id == "9"
        assert r.election_status == "unique"

    def test_single_member_cluster_degrades_to_unique(self):
        results = elect_golden_records([_cluster_row("9", cluster_id="C1")], WEIGHTS)
        (r,) = results
        assert r.is_golden_record is True
        assert r.golden_record_id == "9"
        assert r.election_status == "unique"

    def test_blocked_scores_zero_but_can_win(self):
        results = elect_golden_records([
            _cluster_row("1", customer_status="blocked",
                         last_order_year=2026, order_count=20),
            _cluster_row("2", customer_status="active"),
        ], WEIGHTS)
        by = _by_row(results)
        assert by["1"].score_breakdown["customer_status"] == 0
        assert by["1"].is_golden_record is True     # eligibility unaffected
        assert by["1"].election_status == "proposed"  # not all blocked

    def test_all_blocked_cluster_manual_review(self):
        results = elect_golden_records([
            _cluster_row("1", customer_status="blocked", last_order_year=2026),
            _cluster_row("2", customer_status="BLOCKED "),
        ], WEIGHTS)
        by = _by_row(results)
        assert by["1"].is_golden_record is True     # still elects a winner
        assert by["1"].election_status == "manual_review"
        assert by["2"].election_status == "manual_review"

    def test_duplicate_row_id_raises(self):
        with pytest.raises(DuplicateRowIdError) as exc:
            elect_golden_records([
                ScoringRow(row_id="7"), ScoringRow(row_id="7"),
            ], WEIGHTS)
        assert exc.value.row_ids == ["7"]

    def test_empty_rows(self):
        assert elect_golden_records([], WEIGHTS) == []

    # UNCONFIRMED tie-break ordering (confirm with Bernd): total score, most
    # recent last_order_year, equipment_count, company_code_count, lowest
    # row_id (numeric when all ids in the cluster parse as ints).

    def test_tiebreak_recent_year_wins_on_equal_score(self):
        # 2021 and 2022 both score 0 -> equal total, more recent year wins.
        rows = [
            _cluster_row("1", last_order_year=2021),
            _cluster_row("2", last_order_year=2022),
        ]
        by = _by_row(elect_golden_records(rows, WEIGHTS))
        assert by["2"].is_golden_record is True

    def test_tiebreak_equipment_on_equal_score_and_year(self):
        # 4 and 8 sit in the same 12-point band -> equal score and year,
        # higher raw equipment_count wins.
        rows = [
            _cluster_row("1", last_order_year=2022, equipment_count=4),
            _cluster_row("2", last_order_year=2022, equipment_count=8),
        ]
        by = _by_row(elect_golden_records(rows, WEIGHTS))
        assert by["2"].is_golden_record is True

    def test_tiebreak_company_codes_on_equal_score(self):
        # Row 1: 1 code (5) + active status (10) = 15.
        # Row 2: 2 codes (15). Equal totals, equal year/equipment (absent) ->
        # higher company_code_count wins.
        rows = [
            _cluster_row("1", company_code_consolidated="1001",
                         customer_status="active"),
            _cluster_row("2", company_code_consolidated="1001;1002"),
        ]
        by = _by_row(elect_golden_records(rows, WEIGHTS))
        assert by["2"].is_golden_record is True

    def test_tiebreak_lowest_numeric_row_id_last(self):
        # Everything equal -> lowest row_id compared NUMERICALLY
        # ("3" < "20" as ints even though "20" < "3" lexically).
        rows = [
            _cluster_row("20", last_order_year=2026, equipment_count=2),
            _cluster_row("3", last_order_year=2026, equipment_count=2),
        ]
        by = _by_row(elect_golden_records(rows, WEIGHTS))
        assert by["3"].is_golden_record is True

    def test_tiebreak_lexical_when_non_numeric_ids(self):
        rows = [
            _cluster_row("BP-10", last_order_year=2026),
            _cluster_row("BP-2", last_order_year=2026),
        ]
        by = _by_row(elect_golden_records(rows, WEIGHTS))
        # "BP-10" < "BP-2" lexically.
        assert by["BP-10"].is_golden_record is True

    def test_winner_invariant_under_shuffle(self):
        rows = [
            _cluster_row(str(i), cluster_id="C1",
                         last_order_year=2023 + (i % 4),
                         order_count=i, equipment_count=i % 7)
            for i in range(1, 12)
        ] + [
            ScoringRow(row_id=str(i)) for i in range(100, 105)
        ] + [
            _cluster_row(str(i), cluster_id="C2", last_order_year=2026)
            for i in range(200, 204)
        ]
        baseline = {
            r.row_id: (r.is_golden_record, r.golden_record_id, r.election_status)
            for r in elect_golden_records(rows, WEIGHTS)
        }
        rng = random.Random(42)
        for _ in range(10):
            shuffled = rows[:]
            rng.shuffle(shuffled)
            outcome = {
                r.row_id: (r.is_golden_record, r.golden_record_id, r.election_status)
                for r in elect_golden_records(shuffled, WEIGHTS)
            }
            assert outcome == baseline

    def test_table_invariant(self):
        rows = [
            _cluster_row("1", last_order_year=2026),
            _cluster_row("2"),
            _cluster_row("3", cluster_id="C2", equipment_count=20),
            _cluster_row("4", cluster_id="C2"),
            ScoringRow(row_id="5"),
            _cluster_row("6", cluster_id="C3"),  # degrades to unique
        ]
        results = elect_golden_records(rows, WEIGHTS)
        by = _by_row(results)
        for r in results:
            if r.is_golden_record:
                assert r.golden_record_id == r.row_id  # survivors self-reference
            else:
                target = by[r.golden_record_id]
                assert target.is_golden_record is True
                assert target.cluster_id == r.cluster_id  # resolves within cluster

    def test_score_equals_breakdown_sum(self):
        results = elect_golden_records([
            _cluster_row("1", last_order_year=2026, order_count=12,
                         sleeping_band="No", customer_status="active",
                         account_group="DRIT",
                         company_code_consolidated="1;2;3",
                         sales_org_consolidated="9",
                         salesforce_ids=["a", "b"]),
        ], WEIGHTS)
        (r,) = results
        assert r.score == sum(r.score_breakdown.values())
        # year 20 + orders 25 + partner 0/0 + equipment 0 + sleeping 15
        # + status 10 + DRIT 20 + 3 codes 15 + bonus 10 + 2 SF ids 20
        assert r.score == 135


# ---------------------------------------------------------------------------
# JSON endpoint
# ---------------------------------------------------------------------------

from api.app import app  # noqa: E402


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest_asyncio.fixture
async def client(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestScoreEndpoint:
    @pytest.mark.asyncio
    async def test_duplicate_row_id_400_lists_ids(self, client):
        resp = await client.post("/api/dedup/score", json={"rows": [
            {"row_id": "7"}, {"row_id": "7"}, {"row_id": "8"}, {"row_id": "8"},
        ]})
        assert resp.status_code == 400
        assert "7" in resp.json()["detail"]
        assert "8" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_rows_200_zeroed_summary(self, client):
        resp = await client.post("/api/dedup/score", json={"rows": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == []
        assert data["summary"]["rows_in"] == 0
        assert data["summary"]["clusters"] == 0

    @pytest.mark.asyncio
    async def test_dirty_values_do_not_422(self, client):
        resp = await client.post("/api/dedup/score", json={"rows": [
            {"row_id": "1", "cluster_id": "C1", "sleeping_band": "Yes",
             "customer_status": "parked", "last_order_year": "n/a"},
            {"row_id": "2", "cluster_id": "C1", "last_order_year": 2026.0},
        ]})
        assert resp.status_code == 200
        data = resp.json()
        by = {r["row_id"]: r for r in data["rows"]}
        assert by["1"]["warnings"]  # unrecognised values surfaced, not fatal
        assert by["2"]["is_golden_record"] is True
        assert data["summary"]["rows_with_warnings"] == 1

    @pytest.mark.asyncio
    async def test_summary_counts(self, client):
        resp = await client.post("/api/dedup/score", json={"rows": [
            {"row_id": "1", "cluster_id": "C1", "last_order_year": 2026},
            {"row_id": "2", "cluster_id": "C1"},
            {"row_id": "3"},
            {"row_id": "4", "cluster_id": "C2", "customer_status": "blocked"},
            {"row_id": "5", "cluster_id": "C2", "customer_status": "blocked"},
        ]})
        assert resp.status_code == 200
        s = resp.json()["summary"]
        assert s["rows_in"] == 5
        assert s["clusters"] == 2
        assert s["rows_elected"] == 2
        assert s["rows_duplicates"] == 2
        assert s["rows_unique"] == 1
        assert s["rows_manual_review"] == 2
        assert s["all_blocked_clusters"] == 1
        assert s["errors"] == 0


# ---------------------------------------------------------------------------
# File endpoint / workbook round-trip
# ---------------------------------------------------------------------------

# The 45 original columns of the scoring workbook (SAP export + Phase 1/2
# expectations) — must survive the round-trip untouched.
ORIGINAL_COLUMNS = [
    "Customer", "ECC Customer Number", "Central Deletion Flag", "Comments",
    "Account group", "Company Code", "Sales Organization",
    "Distribution Channel", "Division", "Name 1", "Name 2", "Name 3", "Name 4",
    "Contact", "Street 1", "House Number", "Street 2", "Street 3", "Street 4",
    "Street 5", "PO Box", "Country/Region Key", "Postal Code", "City", "Region",
    "Language Key", "Reconciliation acct", "Tax Jurisdiction",
    "Central delivery block", "Delivery Priority", "Shipping Conditions",
    "Delivering Plant", "Created On", "Created By", "VAT Registration No.",
    "Search Term 1", "Search Term 2", "Terms of Payment", "ror_id", "lei_id",
    "classification", "expected_cluster", "expected_routing",
    "expected_llm_flag", "test_category",
]

CRM_COLUMNS = [
    "Sales_Order_Last_Used", "Sales_Order_Total_Count",
    "Sales_Order_Partner_Last_Used", "Sales_Order_Partner_Total_Count",
    "Equipment_Total_Count", "SleepingCustomer", "CustomerStatus", "Is_ZFIS",
    "Company_Code_Consolidated", "Company_Code_Count", "Sales_Org_Consolidated",
    "Sales_Org_Count", "SF_ID_Biosystems", "SF_ID_AXS", "SF_ID_3", "SF_ID_4",
    "SF_ID_5", "SF_ID_6", "SF_ID_7", "SF_ID_8", "Salesforce_Instance_Count",
]

SCORE_COLUMNS = [
    "score_SalesOrderLastUsed", "score_SalesOrderCount",
    "score_SalesOrderPartnerLastUsed", "score_SalesOrderPartnerCount",
    "score_EquipmentCount", "score_SleepingCustomer", "score_CustomerStatus",
    "score_AccountGroup", "score_CompanyCodeCount", "score_CombinedPresence",
    "score_SalesforceInstances", "score_final",
]

ALL_HEADERS = ORIGINAL_COLUMNS + CRM_COLUMNS + SCORE_COLUMNS


def _data_row(customer, *, cluster="unique", routing="unique", year=None,
              orders=None, p_year=None, p_orders=None, equipment=None,
              sleeping=None, status=None, group="DRIT", codes=None, orgs=None,
              sf=(None,) * 8, name=None):
    values = {h: None for h in ALL_HEADERS}
    values.update({
        "Customer": customer, "Account group": group,
        "Name 1": name or f"Firm {customer}",
        "Street 1": "Hauptstrasse 1", "Postal Code": "10115", "City": "Berlin",
        "Country/Region Key": "DE",
        "expected_cluster": cluster, "expected_routing": routing,
        "Sales_Order_Last_Used": year, "Sales_Order_Total_Count": orders,
        "Sales_Order_Partner_Last_Used": p_year,
        "Sales_Order_Partner_Total_Count": p_orders,
        "Equipment_Total_Count": equipment, "SleepingCustomer": sleeping,
        "CustomerStatus": status, "Is_ZFIS": 0,
        "Company_Code_Consolidated": codes, "Sales_Org_Consolidated": orgs,
        "SF_ID_Biosystems": sf[0], "SF_ID_AXS": sf[1], "SF_ID_3": sf[2],
        "SF_ID_4": sf[3], "SF_ID_5": sf[4], "SF_ID_6": sf[5], "SF_ID_7": sf[6],
        "SF_ID_8": sf[7],
    })
    return [values[h] for h in ALL_HEADERS]


def _weights_sheet_rows():
    rows = [("Criterion", "Band", "Points", "Note")]
    for criterion, bands in WEIGHTS.items():
        for band, points in bands.items():
            rows.append((criterion, band, points, None))
    return rows


def _build_workbook(data_rows, *, weights_rows=None, drop_weights_sheet=False):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(ALL_HEADERS)
    for row in data_rows:
        ws.append(row)
    if not drop_weights_sheet:
        weights = wb.create_sheet("Weights")
        for row in (weights_rows or _weights_sheet_rows()):
            weights.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class TestScoreWorkbook:
    def test_round_trip_preserves_weights_sheet_and_45_columns(self):
        original = _build_workbook([
            _data_row("72000001", cluster="A1", routing="cluster", year=2026,
                      orders=12, sleeping="No", status="active", codes="1001",
                      orgs="2001"),
            _data_row("72000002", cluster="A1", routing="cluster", year=2023),
            _data_row("72000003"),
        ])
        output, summary = score_workbook(original)
        wb = load_workbook(io.BytesIO(output))
        assert "Weights" in wb.sheetnames  # sheet survived

        weights_ws = wb["Weights"]
        assert [c.value for c in weights_ws[1]] == ["Criterion", "Band", "Points", "Note"]

        ws = wb["Sheet1"]
        headers = [c.value for c in ws[1]]
        assert headers[: len(ORIGINAL_COLUMNS)] == ORIGINAL_COLUMNS  # all 45, in place
        # Original cell values untouched.
        assert ws.cell(row=2, column=headers.index("Customer") + 1).value == "72000001"
        assert ws.cell(row=2, column=headers.index("Name 1") + 1).value == "Firm 72000001"
        assert ws.cell(row=2, column=headers.index("expected_cluster") + 1).value == "A1"

        # Election columns appended and filled; native Excel booleans.
        def col(h):
            return headers.index(h) + 1

        assert ws.cell(row=2, column=col("is_golden_record")).value is True
        assert ws.cell(row=3, column=col("is_golden_record")).value is False
        assert ws.cell(row=3, column=col("golden_record_id")).value == "72000001"
        assert ws.cell(row=2, column=col("election_status")).value == "proposed"
        assert ws.cell(row=4, column=col("election_status")).value == "unique"
        assert ws.cell(row=4, column=col("golden_record_id")).value == "72000003"

        # score_final is a plain value equal to the sum of the score_* cells.
        for r in (2, 3, 4):
            parts = sum(
                ws.cell(row=r, column=col(h)).value or 0
                for h in SCORE_COLUMNS if h != "score_final"
            )
            final = ws.cell(row=r, column=col("score_final")).value
            assert isinstance(final, int)
            assert final == parts

        # Derived counts written as plain values.
        assert ws.cell(row=2, column=col("Company_Code_Count")).value == 1
        assert ws.cell(row=2, column=col("Sales_Org_Count")).value == 1
        assert ws.cell(row=2, column=col("Salesforce_Instance_Count")).value == 0

        assert summary.rows_in == 3
        assert summary.clusters == 1
        assert summary.warnings == []  # intact Weights sheet accepted silently

    def test_corrupted_weights_sheet_falls_back_wholesale(self):
        # Drop one band -> the WHOLE sheet must be ignored (never merged),
        # scoring falls back to dedup/weights.json, and the summary warns.
        broken = [r for r in _weights_sheet_rows() if r[1] != "6-10"]
        original = _build_workbook(
            [_data_row("1", year=2026)], weights_rows=broken
        )
        output, summary = score_workbook(original)
        assert any("Weights sheet ignored" in w for w in summary.warnings)
        ws = load_workbook(io.BytesIO(output))["Sheet1"]
        headers = [c.value for c in ws[1]]
        assert ws.cell(row=2, column=headers.index("score_SalesOrderLastUsed") + 1).value == 20

    def test_weights_sheet_override_applies_wholesale(self):
        retuned = [
            (c, b, (40 if (c, b) == ("sales_order_last_used", "2026") else p), n)
            for c, b, p, n in _weights_sheet_rows()
        ]
        output, summary = score_workbook(
            _build_workbook([_data_row("1", year=2026)], weights_rows=retuned)
        )
        assert summary.warnings == []
        ws = load_workbook(io.BytesIO(output))["Sheet1"]
        headers = [c.value for c in ws[1]]
        assert ws.cell(row=2, column=headers.index("score_SalesOrderLastUsed") + 1).value == 40

    def test_blank_customer_skipped_and_counted(self):
        rows = [
            _data_row("1", year=2026),
            _data_row(None, year=2024),  # blank Customer -> summary.errors
            _data_row("2", year=2023),
        ]
        output, summary = score_workbook(_build_workbook(rows))
        assert summary.errors == 1
        assert summary.rows_in == 3
        ws = load_workbook(io.BytesIO(output))["Sheet1"]
        headers = [c.value for c in ws[1]]
        # The skipped row's score cells stay empty; others are filled
        # (year band + the helper's default DRIT account group = 20).
        col = headers.index("score_final") + 1
        assert ws.cell(row=2, column=col).value == 40   # 2026 (20) + DRIT (20)
        assert ws.cell(row=3, column=col).value is None
        assert ws.cell(row=4, column=col).value == 25   # 2023 (5) + DRIT (20)

    def test_manual_review_routing_keeps_cluster_membership(self):
        # Adjudicator semantics: manual_review means membership certain,
        # only a merge was uncertain -> expected_cluster is used.
        rows = [
            _data_row("1", cluster="M1", routing="manual_review", year=2026),
            _data_row("2", cluster="M1", routing="manual_review"),
        ]
        _, summary = score_workbook(_build_workbook(rows))
        assert summary.clusters == 1
        assert summary.rows_elected == 1
        assert summary.rows_duplicates == 1

    def test_duplicate_customer_raises(self):
        rows = [_data_row("1"), _data_row("1")]
        with pytest.raises(DuplicateRowIdError):
            score_workbook(_build_workbook(rows))

    def test_production_cluster_columns(self):
        # The dedup stage appends "Routing" + "Cluster ID" (integer ids);
        # score/file must consume its output directly.
        wb = Workbook()
        ws = wb.active
        ws.append(["Customer", "Sales_Order_Last_Used", "Routing", "Cluster ID"])
        ws.append(["1", 2026, "cluster", 3])
        ws.append(["2", 2023, "cluster", 3])
        ws.append(["3", 2025, "unique", None])
        buffer = io.BytesIO()
        wb.save(buffer)

        _, summary = score_workbook(buffer.getvalue())
        assert summary.clusters == 1
        assert summary.rows_elected == 1
        assert summary.rows_duplicates == 1
        assert summary.rows_unique == 1

    def test_production_pair_beats_expected_pair(self):
        # A dedup output run on the test fixture carries BOTH pairs; the
        # production Routing/Cluster ID pair must win (and never be mixed
        # with the expected_* pair).
        wb = Workbook()
        ws = wb.active
        ws.append(["Customer", "expected_routing", "expected_cluster",
                   "Routing", "Cluster ID"])
        ws.append(["1", "cluster", "A1", "unique", None])
        ws.append(["2", "cluster", "A1", "unique", None])
        buffer = io.BytesIO()
        wb.save(buffer)

        _, summary = score_workbook(buffer.getvalue())
        # expected_* said cluster, production said unique -> unique wins.
        assert summary.clusters == 0
        assert summary.rows_unique == 2

    @pytest.mark.asyncio
    async def test_file_endpoint_end_to_end(self, client):
        contents = _build_workbook([
            _data_row("1", cluster="A1", routing="cluster", year=2026),
            _data_row("2", cluster="A1", routing="cluster"),
        ])
        resp = await client.post(
            "/api/dedup/score/file",
            files={"file": ("scores.xlsx", contents,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200
        assert "scores_scored.xlsx" in resp.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(resp.content))
        assert "Weights" in wb.sheetnames

    @pytest.mark.asyncio
    async def test_file_endpoint_duplicate_customer_400(self, client):
        contents = _build_workbook([_data_row("1"), _data_row("1")])
        resp = await client.post(
            "/api/dedup/score/file",
            files={"file": ("scores.xlsx", contents,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 400
        assert "1" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Pipeline field preservation: /enrich/file -> /api/dedup/file -> score/file
# ---------------------------------------------------------------------------

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestPipelineFieldPreservation:
    """Each stage's output feeds the next, so no stage may drop the CRM
    scoring columns or the Weights sheet before they reach score/file."""

    @pytest.mark.asyncio
    async def test_enrich_dedup_score_chain(self, client):
        # Two rows share a name + address -> identical signature -> the dedup
        # stage clusters them deterministically (no LLM merge needed, so the
        # conservative mock is fine). The third row is unique.
        rows = [
            _data_row("72000001", name="Acme Labs GmbH", year=2026, orders=12,
                      sleeping="No", status="active", codes="1001;1002",
                      orgs="2001"),
            _data_row("72000002", name="Acme Labs GmbH", year=2023),
            _data_row("72000003", name="Zeta Institut", year=2025),
        ]
        contents = _build_workbook(rows)

        # Stage 1 — enrichment must carry the unconsumed CRM columns and the
        # Weights sheet through to its output.
        r1 = await client.post(
            "/enrich/file", files={"file": ("pipe.xlsx", contents, XLSX_MEDIA)}
        )
        assert r1.status_code == 200
        wb1 = load_workbook(io.BytesIO(r1.content))
        assert "Weights" in wb1.sheetnames
        ws1 = wb1.worksheets[0]
        h1 = [c.value for c in ws1[1]]
        for header in ("Sales_Order_Last_Used", "Sales_Order_Total_Count",
                       "SleepingCustomer", "CustomerStatus",
                       "Company_Code_Consolidated", "Sales_Org_Consolidated",
                       "SF_ID_Biosystems", "SF_ID_8"):
            assert header in h1, f"enrichment dropped {header}"
        col1 = {h: i + 1 for i, h in enumerate(h1)}
        assert str(ws1.cell(2, col1["Sales_Order_Last_Used"]).value) == "2026"
        assert ws1.cell(2, col1["SleepingCustomer"]).value == "No"

        # Stage 2 — dedup echoes every column, appends Routing/Cluster ID,
        # and keeps the Weights sheet.
        r2 = await client.post(
            "/api/dedup/file",
            files={"file": ("pipe_enriched.xlsx", r1.content, XLSX_MEDIA)},
        )
        assert r2.status_code == 200
        wb2 = load_workbook(io.BytesIO(r2.content))
        assert "Weights" in wb2.sheetnames
        ws2 = wb2.worksheets[0]
        h2 = [c.value for c in ws2[1]]
        assert "Routing" in h2 and "Cluster ID" in h2
        assert "Sales_Order_Last_Used" in h2

        # Stage 3 — scoring consumes the dedup output directly, using the
        # production Routing/Cluster ID pair.
        r3 = await client.post(
            "/api/dedup/score/file",
            files={"file": ("pipe_dedup.xlsx", r2.content, XLSX_MEDIA)},
        )
        assert r3.status_code == 200
        wb3 = load_workbook(io.BytesIO(r3.content))
        assert "Weights" in wb3.sheetnames
        ws3 = wb3.worksheets[0]
        h3 = [c.value for c in ws3[1]]
        col3 = {h: i + 1 for i, h in enumerate(h3)}
        by = {}
        for r in range(2, ws3.max_row + 1):
            rid = str(ws3.cell(r, col3["Customer"]).value)
            by[rid] = {
                h: ws3.cell(r, col3[h]).value
                for h in ("is_golden_record", "golden_record_id",
                          "election_status", "score_final")
            }
        assert by["72000001"]["election_status"] == "proposed"
        assert by["72000002"]["election_status"] == "proposed"
        assert by["72000001"]["is_golden_record"] is True   # higher score
        assert by["72000002"]["is_golden_record"] is False
        assert by["72000002"]["golden_record_id"] == "72000001"
        assert by["72000003"]["election_status"] == "unique"
        # CRM values survived both hops and actually scored.
        assert by["72000001"]["score_final"] >= 20 + 25 + 15 + 10  # year+orders+sleeping+status
