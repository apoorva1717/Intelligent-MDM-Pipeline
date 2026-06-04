"""Tests for API routes using httpx AsyncClient."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["ENV"] = "local"
os.environ["MOCK_EXTERNAL_CALLS"] = "true"
# FIX(Bug 6): use direct OpenAI env vars, not Azure
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from api.app import app


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest_asyncio.fixture
async def client(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestRoutes:
    """Test API endpoints."""

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert data["mock_mode"] is True

    @pytest.mark.asyncio
    async def test_tiers(self, client):
        resp = await client.get("/tiers")
        assert resp.status_code == 200
        data = resp.json()
        # FIX(Bug 1): single threshold now
        assert "ror_confidence_threshold" in data
        assert data["mock_mode"] is True

    @pytest.mark.asyncio
    async def test_enrich_single_record(self, client):
        payload = {
            "records": [
                {
                    "record_id": "ROUTE_001",
                    "name1": "Massachusetts Institute of Technology",
                    "name2": "Department of Chemistry",
                }
            ],
            "options": {"max_concurrency": 1},
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["record_id"] == "ROUTE_001"
        assert data["summary"]["total"] == 1

    @pytest.mark.asyncio
    async def test_enrich_validation_error(self, client):
        """Empty records list should return 422."""
        payload = {"records": [], "options": {}}
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_enrich_record_without_identifier_accepted(self, client):
        """No field is mandatory: a record with no customer identifier is
        accepted (record_id falls back to empty string)."""
        payload = {
            "records": [{"name1": "MIT"}],
            "options": {},
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["record_id"] == ""

    @pytest.mark.asyncio
    async def test_enrich_batch(self, client):
        """Batch of 3 records returns 3 results."""
        payload = {
            "records": [
                {"record_id": "R1", "name1": "MIT", "name2": "Department of Physics"},
                {"record_id": "R2", "name1": "Pfizer Inc", "name2": "R&D"},
                {"record_id": "R3", "name1": "UCLA"},
            ],
            "options": {"max_concurrency": 2},
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 3
        assert data["summary"]["total"] == 3

    @pytest.mark.asyncio
    async def test_enrich_always_200(self, client):
        """Valid request always returns 200, errors in result.error."""
        payload = {
            "records": [
                {"record_id": "ERR_001", "name1": None},
            ],
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200

    @staticmethod
    def _xlsx_bytes(header: list, *rows: list) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.append(header)
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def _xlsx_upload(self, data: bytes, filename: str = "input.xlsx"):
        return {
            "file": (
                filename,
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }

    @staticmethod
    def _col(header: list, field: str) -> int:
        """Index of a result field's column, resolved via its output header."""
        from api.output_columns import RESPONSE_COLUMNS
        return header.index(RESPONSE_COLUMNS[field])

    @pytest.mark.asyncio
    async def test_enrich_file_returns_xlsx(self, client):
        """XLSX upload is enriched and returned as an XLSX file whose columns
        match the /enrich response body."""
        from api.output_columns import RESPONSE_COLUMNS

        data = self._xlsx_bytes(
            ["Customer", "Name 1", "Name 2"],
            ["ROUTE_001", "Massachusetts Institute of Technology", "Department of Chemistry"],
            [12345, "Pfizer Inc", "R&D"],  # numeric customer is coerced to str
            [None, None, None],  # blank row is skipped
        )
        resp = await client.post(
            "/enrich/file?max_concurrency=2", files=self._xlsx_upload(data)
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "attachment" in resp.headers["content-disposition"]

        wb = load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        header = [c.value for c in ws[1]]
        rows = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]

        # Columns are exactly the response-body fields, in order.
        assert header == list(RESPONSE_COLUMNS.values())
        # Two data rows, blank row skipped; record_id is the first column.
        assert len(rows) == 2
        assert rows[0][0] == "ROUTE_001"
        assert rows[1][0] == "12345"

    @pytest.mark.asyncio
    async def test_enrich_file_columns_match_response_body(self, client):
        """Output columns are driven by RESPONSE_COLUMNS: every key is a real
        result field and the header row is exactly the mapping's values."""
        from api.models import EnrichmentResult
        from api.output_columns import RESPONSE_COLUMNS

        data = self._xlsx_bytes(["Customer", "Name 1"], ["R1", "MIT"])
        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        header = [c.value for c in load_workbook(io.BytesIO(resp.content)).active[1]]
        # Every mapped key is a valid serialised result field (curatable
        # subset/order — not required to match the model one-to-one).
        result_fields = set(EnrichmentResult(record_id="x").model_dump().keys())
        assert set(RESPONSE_COLUMNS.keys()) <= result_fields
        assert header == list(RESPONSE_COLUMNS.values())

    @pytest.mark.asyncio
    async def test_enrich_file_tolerates_messy_headers(self, client):
        """Headers differing only by case/spacing still map to input fields,
        so the record is enriched."""
        data = self._xlsx_bytes(
            ["Customer ", "NAME 1", "Name  2 "],
            ["R1", "MIT", "Department of Chemistry"],
        )
        from api.output_columns import RESPONSE_COLUMNS

        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        header = [c.value for c in ws[1]]
        row = [c.value for c in ws[2]]
        # Recognised on input despite messy headers → name1 was enriched.
        name1_col = header.index(RESPONSE_COLUMNS["name1_enriched"])
        assert row[name1_col] == "Massachusetts Institute of Technology"

    @pytest.mark.asyncio
    async def test_enrich_file_dedupes_repeated_streets(self, client):
        """A street repeated across Street 1/2/3 is kept once; the repeats
        are blanked rather than echoed in every cleaned-street column."""
        data = self._xlsx_bytes(
            ["Customer", "Name 1", "Street 1", "Street 2", "Street 3"],
            ["R1", "Acme Corp", "235 East 42nd Street", "235 East 42nd Street", "235 East 42nd Street"],
        )
        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        header = [c.value for c in ws[1]]
        row = [c.value for c in ws[2]]
        assert row[self._col(header, "street_cleaned")]  # first occurrence kept
        assert row[self._col(header, "street_2_cleaned")] is None  # duplicate dropped
        assert row[self._col(header, "street_3_cleaned")] is None  # duplicate dropped

    @pytest.mark.asyncio
    async def test_enrich_file_dedupes_address_from_name_field(self, client):
        """An address in Name 2 that duplicates Street 1 (incl. trailing
        direction) is extracted in full and deduped, not echoed as a near-
        duplicate street."""
        data = self._xlsx_bytes(
            ["Customer", "Name 1", "Name 2", "Street 1", "Street 2"],
            [
                "13344992",
                "IDEXX Reference Labs",
                "10901 Roosevelt Blvd N",       # address living in Name 2
                "10901 ROOSEVELT BLVD N",        # same address in Street 1
                "Pinellas Bus Ctr, Ste 400D",
            ],
        )
        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        header = [c.value for c in ws[1]]
        row = [c.value for c in ws[2]]
        assert row[self._col(header, "street_cleaned")]            # Street 1 kept
        assert row[self._col(header, "street_3_cleaned")] is None  # Name 2 dup dropped

    @pytest.mark.asyncio
    async def test_enrich_file_street_junk_reaches_output(self, client):
        """End-to-end: URL / email / person / connector / orphan-marker junk
        in a street column is cleaned in the OUTPUT street_2_cleaned (not
        just in the helper functions)."""
        data = self._xlsx_bytes(
            ["Customer", "Name 1", "Street 1", "Street 2"],
            ["A", "Acme", "100 Main St", "ALSO 250 CENTRAL Ave"],
            ["B", "Acme", "100 Main St", "Ste"],
            ["C", "Acme", "100 Main St", "Dr Sarah Johnson - Lab Director"],
            ["D", "Acme", "100 Main St", "http://www.lockheedmartin.com/us/supplie"],
            ["E", "Acme", "100 Main St", "harrisapinvoices@harris.com"],
            ["F", "Acme", "100 Main St", "440 NICKERSON Rd"],
        )
        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        h = [c.value for c in ws[1]]
        rows = {
            r[self._col(h, "record_id")]: r
            for r in ws.iter_rows(min_row=2, values_only=True)
        }
        s2 = self._col(h, "street_2_cleaned")
        contact = self._col(h, "contact_enriched")
        email = self._col(h, "email_enriched")

        assert rows["A"][s2] == "250 CENTRAL Ave"   # connector stripped
        assert rows["B"][s2] is None                # orphan marker dropped
        assert rows["C"][s2] is None                # person removed...
        assert rows["C"][contact] == "Dr Sarah Johnson"  # ...routed to contact
        assert rows["D"][s2] is None                # URL removed
        assert rows["E"][s2] is None                # email removed...
        assert rows["E"][email] == "harrisapinvoices@harris.com"  # ...to email
        assert rows["F"][s2] == "440 NICKERSON Rd"  # real address kept

    @pytest.mark.asyncio
    async def test_enrich_file_rejects_non_xlsx(self, client):
        resp = await client.post(
            "/enrich/file",
            files={"file": ("data.txt", b"not a spreadsheet", "text/plain")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_enrich_file_row_without_identifier_accepted(self, client):
        """No field is mandatory: a row with no customer identifier is
        processed rather than rejected."""
        data = self._xlsx_bytes(["Name 1"], ["MIT"])
        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_enrich_file_no_data_rows(self, client):
        """Header only, no data rows → 400."""
        data = self._xlsx_bytes(["Customer", "Name 1"])
        resp = await client.post("/enrich/file", files=self._xlsx_upload(data))
        assert resp.status_code == 400

    # ── /issues endpoint ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_issues_echoes_input_and_appends_column(self, client):
        """The uploaded sheet is returned unchanged with an appended
        ``Issues`` column listing the detected codes per row."""
        data = self._xlsx_bytes(
            ["Customer", "Name 1", "Name 2", "Postal Code", "Country/Region Key"],
            ["R1", "Acme Corp", "PO BOX 115350", "12345", "US"],   # has issues
            ["R2", "Acme Corp", "Engineering", "12345", "US"],     # clean-ish
        )
        resp = await client.post("/issues", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "attachment" in resp.headers["content-disposition"]

        ws = load_workbook(io.BytesIO(resp.content)).active
        header = [c.value for c in ws[1]]
        # Original headers preserved, "Issues" appended last.
        assert header == [
            "Customer", "Name 1", "Name 2", "Postal Code",
            "Country/Region Key", "Issues",
        ]

        rows = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
        assert len(rows) == 2
        # Original cell values are echoed verbatim.
        assert rows[0][:5] == ["R1", "Acme Corp", "PO BOX 115350", "12345", "US"]
        # PO Box in a name field surfaces as address-content-in-name.
        assert "G1-CROSS-001" in (rows[0][-1] or "")

    @pytest.mark.asyncio
    async def test_issues_multiple_codes_semicolon_joined(self, client):
        data = self._xlsx_bytes(
            ["Name 1", "Postal Code", "Country/Region Key"],
            ["Univ of Florida", "", "USA"],  # G5-NAME-001 + G2-VAL-002 + G4-ADDR-027
        )
        resp = await client.post("/issues", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        issues_cell = [c.value for c in ws[2]][-1]
        codes = {c.strip() for c in (issues_cell or "").split(";")}
        assert {"G2-VAL-002", "G4-ADDR-027", "G5-NAME-001"} <= codes

    @pytest.mark.asyncio
    async def test_issues_rejects_non_xlsx(self, client):
        resp = await client.post(
            "/issues",
            files={"file": ("data.txt", b"not a spreadsheet", "text/plain")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_issues_column_aware_skips_absent_columns(self, client):
        """A file that doesn't carry Postal Code is not reported as missing it."""
        data = self._xlsx_bytes(["Customer", "Name 1", "Name 2"], ["R1", "Acme Corp", "Sales"])
        resp = await client.post("/issues", files=self._xlsx_upload(data))
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.content)).active
        issues_cell = [c.value for c in ws[2]][-1] or ""
        assert "G2-VAL-002" not in issues_cell  # Postal Code column absent

    # ── /issues/compare endpoint ────────────────────────────────────────

    @staticmethod
    def _summary(ws) -> dict:
        """Read the label/value pairs from the Summary sheet."""
        out = {}
        for row in ws.iter_rows(values_only=True):
            if row and row[0] is not None and len(row) >= 2 and row[1] is not None:
                out[row[0]] = row[1]
        return out

    @pytest.mark.asyncio
    async def test_issues_compare_reports_reduction(self, client):
        # Original: PO box sits in a name field (G1-CROSS-001) + missing postal.
        original = self._xlsx_bytes(
            ["Customer", "Name 1", "Name 2", "Postal Code", "Country/Region Key"],
            ["R1", "Acme Corp", "10901 Roosevelt Blvd N", "", "US"],
        )
        # Enriched: address moved out of the name, postal filled. Enriched
        # schema omits Postal Code entirely (column-aware → not counted missing).
        enriched = self._xlsx_bytes(
            ["record_id", "Name 1", "Name 2", "Country/Region Key"],
            ["R1", "Acme Corporation", "Sales Department", "US"],
        )
        resp = await client.post(
            "/issues/compare",
            files={
                "original": ("original.xlsx", original,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                "enriched": ("enriched.xlsx", enriched,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            },
        )
        assert resp.status_code == 200
        assert "attachment" in resp.headers["content-disposition"]

        wb = load_workbook(io.BytesIO(resp.content))
        assert wb.sheetnames == ["Summary", "Per Record"]

        summary = self._summary(wb["Summary"])
        assert summary["Records matched (joined by id)"] == 1
        assert summary["Total issues before"] > summary["Total issues after"]
        assert summary["Net reduction"] >= 1
        assert summary["Issues resolved"] >= 1

        per_record = [
            [c.value for c in row] for row in wb["Per Record"].iter_rows(min_row=2)
        ]
        assert per_record[0][0] == "R1"
        assert "G1-CROSS-001" in (per_record[0][3] or "")  # resolved column

    @pytest.mark.asyncio
    async def test_issues_compare_rejects_non_xlsx(self, client):
        good = self._xlsx_bytes(["Customer", "Name 1"], ["R1", "Acme"])
        resp = await client.post(
            "/issues/compare",
            files={
                "original": ("a.txt", b"nope", "text/plain"),
                "enriched": ("enriched.xlsx", good,
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            },
        )
        assert resp.status_code == 400
