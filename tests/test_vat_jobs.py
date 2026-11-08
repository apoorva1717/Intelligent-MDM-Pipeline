"""Large uploads as background jobs (vat/jobs.py, the /file/jobs endpoints),
2026-11-08: the result equals /file's, chunks never split a customer or an
entity, a stopped or crashed job continues from its next chunk, the cap is
checked between chunks."""

from __future__ import annotations

import io
import time
from dataclasses import replace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook, load_workbook

from api import vat_routes
from api.app import app
from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vies_mock import ViesStub
from tests.test_vat_agent import _Hybrid
from utils.cache import EvidenceCache
from vat import jobs
from vat.jobs import CANCELLED, DONE, STOPPED_BUDGET, Engines, JobStore, create_job, plan_chunks, run_job
from vat.validate import row_groups
from vat.vies_client import VIESClient

URL = "/api/validate/vat/file/jobs"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = ["Customer", "Name", "City", "Postal Code", "Country/Region Key", "VAT Registration No."]


def _rows():
    """10 rows: customer 1 twice, two customers of one entity (Robert Bosch
    GmbH, Gerlingen), a sibling with a number, the rest on their own."""
    return [
        ["1", "Robert Bosch GmbH", "GERLINGEN", "70839", "DE", None],
        ["2", "Gefunden GmbH", "STUTTGART", "70173", "DE", None],
        ["1", "Robert Bosch GmbH", "GERLINGEN", "70839", "DE", None],
        ["3", "Polizeipräsidium Musterstadt", "MUSTERSTADT", "12345", "DE", None],
        ["4", "Robert Bosch GmbH", "GERLINGEN", "70839", "DE", "DE811128135"],
        ["5", "Muster Eins GmbH", "BERLIN", "10115", "DE", None],
        ["6", "Muster Zwei GmbH", "BERLIN", "10115", "DE", None],
        ["7", "Muster Drei GmbH", "BERLIN", "10115", "DE", None],
        ["8", "Gefunden GmbH", "STUTTGART", "70173", "DE", None],
        ["9", "Muster Vier AG", "HAMBURG", "20095", "DE", None],
    ]


def _book(rows=None):
    wb = Workbook()
    wb.active.title = "Data"
    wb.active.append(HEADERS)
    for r in rows or _rows():
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _dicts(rows=None):
    return [dict(zip(HEADERS, r)) for r in rows or _rows()]


class Counting(_Hybrid):
    """The test lookup, counting who it was asked for, and its spend."""

    asked: list[str] = []
    cost_per_call = 0.0

    def __init__(self):
        self.usage_log = []
        self.spent = 0.0

    async def lookup(self, inp):
        Counting.asked.append(inp.customer)
        self.usage_log.append({"calls": [{"from_cache": False, "http_retries": 1}]})
        self.spent += Counting.cost_per_call
        return await super().lookup(inp)

    def stats(self):
        return {"cost_usd": self.spent}


@pytest.fixture(autouse=True)
def _reset():
    Counting.asked = []
    Counting.cost_per_call = 0.0
    jobs._RUNNING.clear()
    yield
    jobs._RUNNING.clear()


@pytest.fixture
def settings(tmp_path):
    return replace(get_settings(), openai_api_key="", vies_backoff_seconds=0.0, vies_min_interval_ms=0, vies_requester_vat="",
                   cache_frozen=False, vat_log_columns=False, vat_lookup_all_rows=False, vat_agent_pass=False,
                   vat_output_columns="review", vat_job_dir=str(tmp_path / "jobs"), vat_job_chunk_rows=3,
                   vat_job_lease_seconds=60, vat_web_budget_usd=0.0)


@pytest_asyncio.fixture
async def client(settings, tmp_path, monkeypatch):
    stub = ViesStub(vies_mock.echo_valid_undisclosed)
    monkeypatch.setattr(vat_routes, "_get_vies_client",
                        lambda _s=None: VIESClient(settings, cache=EvidenceCache(tmp_path / "c"),
                                                   transport=stub.transport))
    monkeypatch.setattr(vat_routes, "_get_lookup", lambda mode, client: Counting() if mode != "none" else None)
    monkeypatch.setattr(vat_routes, "get_settings", lambda: settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _data(content: bytes):
    ws = load_workbook(io.BytesIO(content))["Data"]
    return [list(r) for r in ws.iter_rows(values_only=True)]


class TestChunks:
    def test_rows_of_a_customer_or_an_entity_stay_together(self):
        groups, entities = row_groups(_dicts())
        assert [0, 2, 4] in groups  # customer 1 twice, and customer 4 is the same entity
        assert [1, 8] in groups      # Gefunden GmbH, Stuttgart: one entity, two customers
        assert entities == 7         # in scope (no VAT): Bosch, Gefunden, Polizei, 4 × Muster

    def test_chunks_are_cut_only_between_groups(self):
        chunks, _ = plan_chunks(_dicts(), "missing", 3)
        assert sorted(i for c in chunks for i in c) == list(range(10))
        assert any({0, 2, 4} <= set(c) for c in chunks) and any({1, 8} <= set(c) for c in chunks)


class TestTheEndpoint:
    @pytest.mark.asyncio
    async def test_the_result_equals_file(self, client):
        resp = await client.post(URL + "?vies_after=false", files={"file": ("export.xlsx", _book(), XLSX)})
        assert resp.status_code == 202, resp.text
        job = resp.json()
        assert (job["rows"], job["unique_companies"], job["chunks_total"]) == (10, 7, 4)
        await jobs.wait_idle()
        status = (await client.get(f"{URL}/{job['id']}")).json()
        assert status["status"] == DONE and status["chunks_done"] == 4 and status["http_retries"] == 7
        result = await client.get(f"{URL}/{job['id']}/result")
        direct = await client.post("/api/validate/vat/file", files={"file": ("export.xlsx", _book(), XLSX)})
        assert _data(result.content) == _data(direct.content)
        assert "Summary" in load_workbook(io.BytesIO(result.content)).sheetnames

    @pytest.mark.asyncio
    async def test_each_entity_is_looked_up_once_in_the_chunks(self, client):
        job = (await client.post(URL, files={"file": ("export.xlsx", _book(), XLSX)})).json()
        await jobs.wait_idle()
        chunk_phase = Counting.asked[:7]  # then the assembly asks again (the cache answers, live)
        assert len(set(chunk_phase)) == 7 and job["estimated_cost_usd"] == round(7 * jobs.COST_PER_COMPANY_USD, 2)

    @pytest.mark.asyncio
    async def test_bad_uploads(self, client):
        assert (await client.post(URL, files={"file": ("x.csv", b"x", XLSX)})).status_code == 400
        wb = Workbook()
        wb.active.title = "Data"
        wb.active.append(["Customer", "Name"])
        wb.active.append(["1", "X GmbH"])
        buf = io.BytesIO()
        wb.save(buf)
        no_vat = buf.getvalue()
        assert (await client.post(URL, files={"file": ("x.xlsx", no_vat, XLSX)})).status_code == 422
        assert (await client.get(f"{URL}/0123abcd")).status_code == 404
        assert (await client.get(f"{URL}/../etc")).status_code == 404

    @pytest.mark.asyncio
    async def test_the_cap_stops_between_chunks_and_resume_continues(self, client):
        Counting.cost_per_call = 1.0
        job = (await client.post(URL + "?max_cost_usd=2", files={"file": ("e.xlsx", _book(), XLSX)})).json()
        await jobs.wait_idle()
        stopped = (await client.get(f"{URL}/{job['id']}")).json()
        assert stopped["status"] == STOPPED_BUDGET and stopped["chunks_done"] < 4 and stopped["spent_usd"] >= 2
        assert (await client.get(f"{URL}/{job['id']}/result")).status_code == 409
        partial = await client.get(f"{URL}/{job['id']}/result?partial=true")
        assert 1 < len(_data(partial.content)) < 11
        await client.post(f"{URL}/{job['id']}/resume?max_cost_usd=0")
        await jobs.wait_idle()
        assert (await client.get(f"{URL}/{job['id']}")).json()["status"] == DONE

    @pytest.mark.asyncio
    async def test_cancel_a_waiting_job(self, client, settings):
        store = JobStore(settings.vat_job_dir)
        job = create_job(store, _book(), "e.xlsx", {"mode": "llm_web", "scope": "missing"}, 3)
        resp = await client.delete(f"{URL}/{job['id']}")
        assert resp.json()["status"] == CANCELLED
        assert (await client.delete(f"{URL}/{job['id']}?purge=true")).json()["deleted"]


class TestCrashAndResume:
    def _engines(self, settings, tmp_path, crash_on: int | None = None):
        made = []

        def make(options):
            made.append(1)
            if crash_on is not None and len(made) == crash_on:
                raise KeyboardInterrupt  # the process dies: nothing is recorded, the lease stays
            client = VIESClient(settings, cache=EvidenceCache(tmp_path / "c"),
                                transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
            return Engines(client, Counting())
        return make

    @pytest.mark.asyncio
    async def test_a_dead_worker_is_continued_from_the_next_chunk(self, settings, tmp_path):
        store = JobStore(settings.vat_job_dir)
        job = create_job(store, _book(), "e.xlsx", {"mode": "llm_web", "scope": "missing", "vies_after": False}, 3)
        with pytest.raises(KeyboardInterrupt):
            await run_job(store, job["id"], self._engines(settings, tmp_path, crash_on=3), worker="dead")
        after_crash = store.read(job["id"])
        assert [c["status"] for c in after_crash["chunks"]] == ["done", "done", "pending", "pending"]
        done_rows = {i for c in after_crash["chunks"][:2] for i in c["rows"]}
        # The dead worker's lease still holds: nobody takes the job.
        store.update(job["id"], lease={"worker": "dead", "until": time.time() + 60})
        assert await run_job(store, job["id"], self._engines(settings, tmp_path), worker="new") is None
        # It runs out: the timer's worker continues where it stopped.
        store.update(job["id"], lease={"worker": "dead", "until": time.time() - 1})
        Counting.asked = []
        await run_job(store, job["id"], self._engines(settings, tmp_path), worker="new")
        done = store.read(job["id"])
        assert done["status"] == DONE and done["lease"] is None
        rows = _dicts()
        redone = {r["Customer"] for i, r in enumerate(rows) if i in done_rows}
        chunk_phase = Counting.asked[:len(Counting.asked) - 7]
        assert chunk_phase and not set(chunk_phase) & redone  # finished chunks were not run again

    @pytest.mark.asyncio
    async def test_the_timer_picks_up_a_waiting_job(self, settings, tmp_path):
        store = JobStore(settings.vat_job_dir)
        job = create_job(store, _book(), "e.xlsx", {"mode": "llm_web", "scope": "missing", "vies_after": False}, 3)
        assert jobs.resume_jobs(store, self._engines(settings, tmp_path), 60) == job["id"]
        assert jobs.resume_jobs(store, self._engines(settings, tmp_path), 60) is None  # busy: one at a time
        await jobs.wait_idle()
        assert store.read(job["id"])["status"] == DONE
        assert jobs.resume_jobs(store, self._engines(settings, tmp_path), 60) is None  # nothing left
