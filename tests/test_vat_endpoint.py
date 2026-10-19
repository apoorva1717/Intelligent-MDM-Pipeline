"""POST /api/validate/vat (api/vat_routes.py + vat/validate.py), VIES mocked."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook, load_workbook

from api import vat_routes
from api.app import app
from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.validate import CUSTOMER_VAT_CONFLICT, VAT_COLUMNS
from vat.vies_client import VIESClient

URL = "/api/validate/vat"

#: The 28 SAP export columns + VAT Registration No., in export order.
EXPORT_HEADERS = [
    "Customer", "Central Deletion Flag", "Central delivery block", "Company Code",
    "Sales Organization", "Distribution Channel", "Division", "Name", "Name 2",
    "Name 3", "Name 4", "PO Box", "House Number", "Street", "Street 2", "Street 3",
    "Street 4", "City", "Postal Code", "Region", "Country/Region Key",
    "Account group", "Reconciliation acct", "Comments", "Terms of Payment",
    "Sales District", "Created On", "Created By", "VAT Registration No.",
]


def _row(customer, country, vat, **extra):
    row = dict.fromkeys(EXPORT_HEADERS)
    row.update({
        "Customer": customer, "Country/Region Key": country,
        "VAT Registration No.": vat, "Name": f"Example {customer}",
        "Created By": "VATTEST",
    })
    row.update(extra)
    return row


@pytest.fixture
def stub():
    return ViesStub(vies_mock.echo_valid_undisclosed)


@pytest_asyncio.fixture
async def client(stub, tmp_path, monkeypatch):
    settings = replace(
        get_settings(), vies_backoff_seconds=0.0, vies_min_interval_ms=0,
        vies_requester_vat="", cache_frozen=False,
    )

    def factory(_settings=None):
        return VIESClient(settings, cache=EvidenceCache(tmp_path), transport=stub.transport)

    monkeypatch.setattr(vat_routes, "_get_vies_client", factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _post(client, rows):
    resp = await client.post(URL, json={"rows": rows})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestOutputShape:
    @pytest.mark.asyncio
    async def test_input_columns_unchanged_and_vat_columns_appended(self, client):
        row = _row("99000001", "DE", "DE 136.695-976", **{"Company Code": 1000, "Street": " x "})
        body = await _post(client, [row])
        out = body["rows"][0]
        assert list(out) == EXPORT_HEADERS + VAT_COLUMNS
        assert {k: out[k] for k in EXPORT_HEADERS} == row
        assert body["columns"] == VAT_COLUMNS

    @pytest.mark.asyncio
    async def test_de_valid_with_undisclosed_name(self, client):
        out = (await _post(client, [_row("1", "DE", "DE136695976")]))["rows"][0]
        assert out["VAT_Normalized"] == "DE136695976"
        assert out["VAT_Format_Status"] == "FORMAT_OK"
        assert out["VAT_VIES_Status"] == "VALID"
        assert (out["VAT_VIES_Name"], out["VAT_VIES_Address"]) == (None, None)
        assert out["VAT_Name_Disclosed"] is False
        assert out["VAT_Request_Date"] == vies_mock.REQUEST_DATE
        assert out["VAT_Consultation_No"] is None
        assert out["VAT_Name_Match"] == "NOT_AVAILABLE"
        assert out["VAT_Flags"] == "NAME_MATCH:NOT_DISCLOSED"

    @pytest.mark.asyncio
    async def test_headers_are_matched_tolerantly(self, client):
        rows = [{"customer": "1", "country_region_key": "DE", "VAT REGISTRATION NO": "136695976"}]
        out = (await _post(client, rows))["rows"][0]
        assert (out["VAT_Normalized"], out["VAT_Flags"]) == (
            "DE136695976", "PREFIX_FROM_COUNTRY_KEY; NAME_MATCH:NOT_DISCLOSED",
        )


class TestFanOut:
    @pytest.mark.asyncio
    async def test_customer_split_is_checked_once(self, client, stub):
        rows = [
            _row("99000003", "DE", "DE136695976", **{"Company Code": "1000"}),
            _row("99000003", "DE", "DE136695976", **{"Company Code": "1100"}),
        ]
        body = await _post(client, rows)
        assert [r["VAT_VIES_Status"] for r in body["rows"]] == ["VALID", "VALID"]
        assert len(stub.requests) == 1
        assert body["summary"]["unique_vies_lookups"] == 1

    @pytest.mark.asyncio
    async def test_same_number_spelled_differently_is_not_a_conflict(self, client, stub):
        rows = [_row("7", "DE", "DE136695976"), _row("7", "DE", "de 136 695 976")]
        body = await _post(client, rows)
        assert all(CUSTOMER_VAT_CONFLICT not in r["VAT_Flags"] for r in body["rows"])
        assert len(stub.requests) == 1

    @pytest.mark.asyncio
    async def test_conflicting_values_are_flagged_and_neither_is_picked(self, client):
        rows = [_row("99000006", "DE", "DE136695976"), _row("99000006", "DE", "DE136695977")]
        body = await _post(client, rows)
        a, b = body["rows"]
        assert (a["VAT_Normalized"], a["VAT_VIES_Status"]) == ("DE136695976", "VALID")
        assert (b["VAT_Normalized"], b["VAT_Format_Status"], b["VAT_VIES_Status"]) == (
            "DE136695977", "FORMAT_ERROR", "NOT_CHECKED",
        )
        assert CUSTOMER_VAT_CONFLICT in a["VAT_Flags"]
        assert CUSTOMER_VAT_CONFLICT in b["VAT_Flags"]
        assert body["summary"]["customers_with_vat_conflict"] == 1

    @pytest.mark.asyncio
    async def test_value_on_one_row_and_blank_on_another_is_a_conflict(self, client):
        rows = [_row("8", "DE", "DE136695976"), _row("8", "DE", None)]
        body = await _post(client, rows)
        assert all(CUSTOMER_VAT_CONFLICT in r["VAT_Flags"] for r in body["rows"])

    @pytest.mark.asyncio
    async def test_customer_is_grouped_as_read_no_zero_stripping(self, client):
        rows = [_row("0099000006", "DE", "DE136695976"), _row("99000006", "DE", None)]
        body = await _post(client, rows)
        assert all(r["VAT_Flags"] is None or CUSTOMER_VAT_CONFLICT not in r["VAT_Flags"]
                   for r in body["rows"])
        assert body["summary"]["unique_customers"] == 2


class TestStates:
    @pytest.mark.asyncio
    async def test_format_error_is_never_sent(self, client, stub):
        out = (await _post(client, [_row("1", "DE", "DE136695977")]))["rows"][0]
        assert (out["VAT_Format_Status"], out["VAT_VIES_Status"]) == ("FORMAT_ERROR", "NOT_CHECKED")
        assert out["VAT_Flags"] == "FORMAT_ERROR:InvalidChecksum; NAME_MATCH:VIES_NOT_VALID"
        assert stub.requests == []

    @pytest.mark.asyncio
    async def test_us_row_and_missing_row(self, client, stub):
        body = await _post(client, [_row("1", "US", None), _row("2", "DE", None)])
        us, de = body["rows"]
        assert (us["VAT_Format_Status"], us["VAT_VIES_Status"]) == ("NOT_EU", "NOT_CHECKED")
        assert (de["VAT_Format_Status"], de["VAT_VIES_Status"]) == ("MISSING", "NOT_CHECKED")
        assert stub.requests == []

    @pytest.mark.asyncio
    async def test_prefix_country_mismatch_is_flagged(self, client):
        out = (await _post(client, [_row("1", "DE", "FR40303265045")]))["rows"][0]
        assert (out["VAT_Normalized"], out["VAT_VIES_Status"]) == ("FR40303265045", "VALID")
        assert out["VAT_Flags"] == "PREFIX_COUNTRY_MISMATCH; NAME_MATCH:NOT_DISCLOSED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", ["MS_UNAVAILABLE", "GLOBAL_MAX_CONCURRENT_REQ"])
    async def test_service_unavailable_is_not_invalid(self, client, stub, code):
        stub.replies = [vies_mock.error(code)]
        out = (await _post(client, [_row("1", "DE", "DE136695976")]))["rows"][0]
        assert out["VAT_VIES_Status"] == "SERVICE_UNAVAILABLE"
        assert out["VAT_Flags"] == f"VIES:{code}; NAME_MATCH:VIES_NOT_VALID"
        assert out["VAT_Name_Match"] == "NOT_AVAILABLE"

    @pytest.mark.asyncio
    async def test_disclosed_name_and_address(self, client, stub):
        stub.replies = [vies_mock.valid_disclosed("FR", "40303265045")]
        out = (await _post(client, [_row("1", "FR", "FR40303265045")]))["rows"][0]
        assert (out["VAT_VIES_Name"], out["VAT_Name_Disclosed"]) == ("EXAMPLE TRADING SA", True)
        assert (out["VAT_Name_Match"], out["VAT_Flags"]) == ("MISMATCH", "NAME_MATCH:NO_MATCH")

    @pytest.mark.asyncio
    async def test_name_match_reads_name_and_name_2_to_4(self, client, stub):
        stub.replies = [vies_mock.valid_disclosed("FR", "40303265045", name="SA EXAMPLE TRADING")]
        row = _row("1", "FR", "FR40303265045", Name="Example", **{"Name 2": "Trading S.A."})
        out = (await _post(client, [row]))["rows"][0]
        assert (out["VAT_Name_Match"], out["VAT_Flags"]) == ("MATCH", "NAME_MATCH:EXACT_WITH_NAME_2_4")


class TestSummaryAndErrors:
    @pytest.mark.asyncio
    async def test_summary_counts(self, client):
        rows = [
            _row("1", "DE", "DE136695976"), _row("1", "DE", "DE136695976"),
            _row("2", "DE", "DE136695977"), _row("3", "US", None),
        ]
        s = (await _post(client, rows))["summary"]
        assert (s["rows"], s["unique_customers"], s["unique_vies_lookups"]) == (4, 3, 1)
        assert s["format_status"] == {"FORMAT_OK": 2, "FORMAT_ERROR": 1, "NOT_EU": 1}
        assert s["vies_status"] == {"VALID": 2, "NOT_CHECKED": 2}
        assert s["by_country"]["US"] == {
            "format_status": {"NOT_EU": 1}, "vies_status": {"NOT_CHECKED": 1},
        }

    @pytest.mark.asyncio
    async def test_no_vat_column_is_422(self, client):
        resp = await client.post(URL, json={"rows": [{"Customer": "1", "Country/Region Key": "DE"}]})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_batch_is_422(self, client):
        resp = await client.post(URL, json={"rows": []})
        assert resp.status_code == 422



# ---------------------------------------------------------------------------
# POST /api/validate/vat/file
# ---------------------------------------------------------------------------

FILE_URL = "/api/validate/vat/file"
FIXTURE = Path(__file__).parent / "fixtures" / "vat" / "VAT_TEST_EXPORT_30.xlsx"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _upload(client, name, content):
    return await client.post(FILE_URL, files={"file": (name, content, XLSX)})


def _book(rows):
    wb = Workbook()
    wb.active.title = "Data"
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet(wb, title):
    rows = list(wb[title].iter_rows(values_only=True))
    return list(rows[0]), [dict(zip(rows[0], r)) for r in rows[1:]]


class TestFileEndpoint:
    @pytest.mark.asyncio
    async def test_fixture_round_trip(self, client, stub):
        resp = await _upload(client, "VAT_TEST_EXPORT_30.xlsx", FIXTURE.read_bytes())
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == XLSX
        assert resp.headers["content-disposition"] == 'attachment; filename="VAT_TEST_EXPORT_30_vat.xlsx"'
        assert (resp.headers["x-vat-rows"], resp.headers["x-vat-service-unavailable"]) == ("30", "0")

        out = load_workbook(io.BytesIO(resp.content), read_only=True)
        src = load_workbook(FIXTURE, read_only=True)
        assert out.sheetnames == ["Data", "Test_Notes", "Summary"]
        in_headers, in_rows = _sheet(src, "Data")
        out_headers, out_rows = _sheet(out, "Data")
        assert out_headers == in_headers + VAT_COLUMNS
        assert [{h: r[h] for h in in_headers} for r in out_rows] == in_rows
        assert _sheet(out, "Test_Notes") == _sheet(src, "Test_Notes")
        assert [r["VAT_Format_Status"] for r in out_rows].count("FORMAT_OK") == 26
        assert len(stub.requests) == 24  # once per unique VAT, not per row

    @pytest.mark.asyncio
    async def test_service_unavailable_count_header(self, client, stub):
        stub.replies = [vies_mock.error("MS_UNAVAILABLE")]
        content = _book([["Customer", "Country/Region Key", "VAT Registration No."],
                         ["1", "DE", "DE136695976"], ["2", "FR", "FR40303265045"]])
        resp = await _upload(client, "x.xlsx", content)
        assert resp.headers["x-vat-service-unavailable"] == "2"

    @pytest.mark.asyncio
    async def test_active_sheet_fallback(self, client):
        wb = Workbook()
        wb.active.title = "Export"
        wb.active.append(["Customer", "Country/Region Key", "VAT Registration No."])
        wb.active.append(["1", "DE", "DE136695976"])
        buf = io.BytesIO()
        wb.save(buf)
        resp = await _upload(client, "export.xlsx", buf.getvalue())
        assert resp.status_code == 200
        _, rows = _sheet(load_workbook(io.BytesIO(resp.content)), "Export")
        assert rows[0]["VAT_VIES_Status"] == "VALID"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name, content, status", [
        ("export.csv", b"Customer,VAT", 400),   # wrong extension
        ("export.xlsx", b"", 400),                       # empty
        ("export.xlsx", b"not a workbook", 400),         # unreadable
    ])
    async def test_bad_uploads(self, client, name, content, status):
        resp = await _upload(client, name, content)
        assert resp.status_code == status

    @pytest.mark.asyncio
    async def test_no_vat_column_is_422(self, client):
        resp = await _upload(client, "x.xlsx", _book([["Customer", "Name"], ["1", "Acme"]]))
        assert resp.status_code == 422
        assert "VAT Registration No." in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_no_data_rows_is_422(self, client):
        resp = await _upload(client, "x.xlsx", _book([["Customer", "VAT Registration No."]]))
        assert resp.status_code == 422
