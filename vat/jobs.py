"""Large VAT uploads as background jobs (2026-11-08).

``POST /api/validate/vat/file`` answers only when the whole workbook is done:
fine for a few hundred rows, impossible for 10,000 (hours; the Function's
HTTP front end drops a request after about 230 s). A job takes the same
upload and options, answers at once with its id, and works in the
background:

1. **Chunks.** The rows are cut into chunks of about ``VAT_JOB_CHUNK_ROWS``,
   only between :func:`vat.validate.row_groups` (all rows of a customer or a
   legal entity stay together). Each chunk runs through
   :func:`vat.validate.validate_rows` with fresh engines; its rows, counts
   and spend are saved before the next starts. This is the live work: every
   answer lands in the evidence / LLM cache.
2. **Assembly.** The finished file is :func:`vat.xlsx.validate_workbook` on
   the whole upload — exactly what ``/file`` runs — now answered from the
   cache: identical rows, one Summary sheet, $0 for everything already done.
   What is not cached (a call that failed, a page that timed out) is asked
   again here: the retry pass.
3. **VIES** (``vies_after``, default on): the VIES queue is asked and its
   answers written into the result (:func:`vat.vies_retry.apply_to_workbook`),
   as the batch tool does. Off: the result equals ``/file``'s exactly.

State is plain files in ``VAT_JOB_DIR/<job id>/`` — ``job.json``,
``input.xlsx``, ``chunks/NNN.json``, ``result.xlsx`` — on storage that
survives a restart (``/home`` on the Function app). A worker holds a lease
in ``job.json``, renewed every ``lease/3`` seconds; a job whose lease ran
out (a crash, a recycle, a deployment) is continued by :func:`resume_jobs`
(the Function's timer trigger) from its next unfinished chunk, and the
cache makes a half-done chunk replay for free. Jobs run one at a time per
process, so one rate limiter (``vat/rate_limit.py``) paces all their calls.

Budget: ``max_cost_usd`` (else ``VAT_WEB_BUDGET_USD``; 0 = none) is checked
between chunks, never inside one, so no row is cut off half-way
(``BUDGET_EXCEEDED``); a chunk can pass it by its own cost. The job then
stops as ``STOPPED_BUDGET`` and can be resumed with a higher cap.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

QUEUED, RUNNING, ASSEMBLING, DONE = "QUEUED", "RUNNING", "ASSEMBLING", "DONE"
FAILED, CANCELLED, STOPPED_BUDGET = "FAILED", "CANCELLED", "STOPPED_BUDGET"
#: A job in one of these is finished: never picked up again (STOPPED_BUDGET
#: until resumed with a higher cap).
FINAL = frozenset({DONE, FAILED, CANCELLED, STOPPED_BUDGET})

#: Cost per legal entity looked up (AD 50 live, 2026-11-04: $3.16 / 50),
#: for the estimate the upload answers with.
COST_PER_COMPANY_USD = 0.063
#: A chunk that failed this often fails the job.
MAX_CHUNK_ATTEMPTS = 3


def _now() -> float:
    return time.time()


def _iso(t: float | None) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if t else None


@dataclass
class Engines:
    """What one chunk (or the assembly) runs with; closed after it."""

    client: Any
    lookup: Any
    compare: Any = None
    agent: Any = None

    async def aclose(self) -> None:
        for engine in (self.lookup, self.compare, self.agent):
            if engine is not None:
                await engine.aclose()


#: ``make_engines(options) -> Engines``: the API's factories (patched in tests).
EngineFactory = Callable[[dict[str, Any]], Engines]


def _retrying(op: Callable[[], Any], job_id: str) -> Any:
    """*op* on ``job.json``, again for up to about 2 s while the file is
    briefly held open elsewhere (Windows: a reader, a virus scanner; Azure
    Files the same). A missing file is an unknown job."""
    for attempt in range(40):
        try:
            return op()
        except FileNotFoundError:
            raise KeyError(job_id) from None
        except (PermissionError, json.JSONDecodeError):
            if attempt == 39:
                raise
            time.sleep(0.05)


class JobStore:
    """The job folders under *root*. Every write of ``job.json`` replaces the
    file whole (write, then rename), so a crash never leaves half a file."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, job_id: str) -> Path:
        if not job_id or any(c not in "0123456789abcdef" for c in job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def read(self, job_id: str) -> dict[str, Any]:
        path = self.path(job_id) / "job.json"
        return _retrying(lambda: json.loads(path.read_text(encoding="utf-8")), job_id)

    def write(self, job: dict[str, Any]) -> None:
        folder = self.path(job["id"])
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"job.json.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(job, indent=1, default=str), encoding="utf-8")
        _retrying(lambda: os.replace(tmp, folder / "job.json"), job["id"])

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        job = self.read(job_id)
        job.update(fields)
        self.write(job)
        return job

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted((p.name for p in self.root.iterdir() if (p / "job.json").is_file()),
                      key=lambda i: (self.path(i) / "job.json").stat().st_mtime)

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.path(job_id), ignore_errors=True)


def _input_rows(contents: bytes) -> list[dict[str, Any]]:
    """The upload's data rows, read as ``/file`` reads them."""
    from openpyxl import load_workbook

    from vat.validate import ALL_RESULT_COLUMNS
    from vat.xlsx import _data_sheet, _read

    wb = load_workbook(io.BytesIO(contents))
    ws = _data_sheet(wb)
    _, _, rows = _read(ws, ALL_RESULT_COLUMNS)
    if not rows:
        raise ValueError(f"Sheet {ws.title!r} has no data rows.")
    return [r for _, r in rows]


def plan_chunks(rows: list[dict[str, Any]], scope: str, chunk_rows: int) -> tuple[list[list[int]], int]:
    """Row indexes per chunk (whole :func:`vat.validate.row_groups`, about
    *chunk_rows* each) and the number of legal entities to look up."""
    from vat.validate import row_groups

    groups, entities = row_groups(rows, scope)
    chunks: list[list[int]] = [[]]
    for group in groups:
        if chunks[-1] and len(chunks[-1]) + len(group) > max(1, chunk_rows):
            chunks.append([])
        chunks[-1].extend(group)
    return [c for c in chunks if c], entities


def create_job(store: JobStore, contents: bytes, filename: str, options: dict[str, Any],
               chunk_rows: int, max_cost_usd: float | None = None) -> dict[str, Any]:
    """A new QUEUED job for the upload *contents*. Raises ``ValueError`` for
    an unreadable workbook, no data rows or no VAT column."""
    rows = _input_rows(contents)
    chunks, entities = plan_chunks(rows, options.get("scope") or "missing", chunk_rows)
    job_id = uuid.uuid4().hex
    folder = store.path(job_id)
    (folder / "chunks").mkdir(parents=True, exist_ok=True)
    (folder / "input.xlsx").write_bytes(contents)
    job = {
        "id": job_id, "status": QUEUED, "filename": filename, "options": options,
        "created_at": _iso(_now()), "started_at": None, "finished_at": None,
        "rows": len(rows), "unique_companies": entities,
        "estimated_cost_usd": round(entities * COST_PER_COMPANY_USD, 2),
        "max_cost_usd": max_cost_usd or None,
        "chunks": [{"n": i, "rows": idx, "status": "pending", "attempts": 0} for i, idx in enumerate(chunks)],
        "spent_usd": 0.0, "companies_done": 0, "http_retries": 0, "rate_limit_wait_s": 0.0,
        "lease": None, "error": None, "cancel": False, "summary": None,
    }
    store.write(job)
    return job


def progress(job: dict[str, Any]) -> dict[str, Any]:
    """The job as the status endpoint reports it (no row indexes)."""
    chunks = job.get("chunks") or []
    done = [c for c in chunks if c["status"] == "done"]
    elapsed = sum(c.get("seconds") or 0 for c in done)
    rows_done = sum(len(c["rows"]) for c in done)
    rows_left = job["rows"] - rows_done
    per_row = elapsed / rows_done if rows_done else None
    out = {k: v for k, v in job.items() if k not in ("chunks", "lease", "cancel")}
    out.update(
        chunks_total=len(chunks), chunks_done=len(done), rows_done=rows_done,
        companies_per_minute=round(job["companies_done"] / elapsed * 60, 1) if elapsed else None,
        estimated_seconds_left=round(per_row * rows_left) if per_row is not None and job["status"] in (
            QUEUED, RUNNING) else None,
        statuses=dict(sum((Counter(c.get("statuses") or {}) for c in done), Counter())),
        worker=(job.get("lease") or {}).get("worker"),
    )
    return out


# ── the worker ─────────────────────────────────────────────────────────────

def _lease_free(job: dict[str, Any], worker: str) -> bool:
    lease = job.get("lease")
    return not lease or lease.get("worker") == worker or lease.get("until", 0) < _now()


def _take_lease(store: JobStore, job_id: str, worker: str, seconds: int) -> dict[str, Any] | None:
    """The job with *worker*'s lease, or None when another worker holds a
    live one. (One process works one job at a time: :data:`_RUNNING`.)"""
    job = store.read(job_id)
    if job["status"] in FINAL or not _lease_free(job, worker):
        return None
    job["lease"] = {"worker": worker, "until": _now() + seconds}
    store.write(job)
    again = store.read(job_id)  # another worker writing at the same moment: last one wins, the other backs off
    return again if (again.get("lease") or {}).get("worker") == worker else None


async def _heartbeat(store: JobStore, job_id: str, worker: str, seconds: int) -> None:
    while True:
        await asyncio.sleep(max(1.0, seconds / 3))
        try:
            job = store.read(job_id)
            if (job.get("lease") or {}).get("worker") != worker:
                return
            job["lease"]["until"] = _now() + seconds
            store.write(job)
        except Exception as exc:  # noqa: BLE001 - a missed beat only shortens the lease
            logger.warning("VAT job %s: lease renewal failed: %s", job_id, exc)


def _chunk_stats(summary: dict[str, Any], engines: Engines) -> dict[str, Any]:
    lookup = summary.get("lookup") or {}
    spent = float((lookup.get("cost") or {}).get("cost_usd") or 0)
    if engines.agent is not None:
        spent += float(((summary.get("lookup") or {}).get("agent_cost") or {}).get("cost_usd") or 0)
    log = getattr(engines.lookup, "usage_log", None) or []
    calls = [c for e in log for c in (e.get("calls") or []) if isinstance(c, dict) and not c.get("from_cache")]
    return {
        "statuses": lookup.get("status") or {},
        "spent_usd": round(spent, 4),
        "companies": len(log),
        "http_retries": sum(int(c.get("http_retries") or 0) for c in calls),
        "rate_limit_wait_s": round(sum(float(c.get("rate_limit_wait_s") or 0) for c in calls), 1),
    }


async def _run_chunk(store: JobStore, job: dict[str, Any], chunk: dict[str, Any],
                     rows: list[dict[str, Any]], make_engines: EngineFactory) -> dict[str, Any]:
    from vat.validate import validate_rows

    opts = job["options"]
    engines = make_engines(opts)
    started = time.perf_counter()
    try:
        out, summary = await validate_rows(
            [rows[i] for i in chunk["rows"]], engines.client, lookup=engines.lookup,
            compare_lookup=engines.compare, scope=opts.get("scope") or "missing", agent=engines.agent,
            log_columns=bool(opts.get("log_columns")), output_columns=opts.get("output_columns") or "review")
        stats = _chunk_stats(summary, engines)
    finally:
        await engines.aclose()
    stats["seconds"] = round(time.perf_counter() - started, 1)
    path = store.path(job["id"]) / "chunks" / f"{chunk['n']:04d}.json"
    path.write_text(json.dumps({"rows": chunk["rows"], "out": out}, default=str), encoding="utf-8")
    return stats


async def _assemble(store: JobStore, job: dict[str, Any], make_engines: EngineFactory) -> dict[str, Any]:
    """The whole upload through ``/file``'s code, answered from the cache."""
    from openpyxl import load_workbook

    from vat.xlsx import validate_workbook

    folder = store.path(job["id"])
    opts = job["options"]
    wb = load_workbook(folder / "input.xlsx")
    engines = make_engines(opts)
    try:
        summary = await validate_workbook(
            wb, engines.client, source=job["filename"], lookup=engines.lookup, compare_lookup=engines.compare,
            scope=opts.get("scope") or "missing", agent=engines.agent,
            log_columns=bool(opts.get("log_columns")), output_columns=opts.get("output_columns") or "review")
        if opts.get("vies_after", True) and engines.client.enabled:
            from vat.vies_retry import apply_to_workbook, drain

            queue, asked = await drain(engines.client)
            summary["vies_after"] = {"asked": len(asked), "rows_changed": apply_to_workbook(wb, queue.answers())}
        assembly = _chunk_stats(summary, engines)
    finally:
        await engines.aclose()
    tmp = folder / f"result.{uuid.uuid4().hex}.tmp"
    wb.save(tmp)
    os.replace(tmp, folder / "result.xlsx")
    return {"summary": _plain(summary), "assembly_spent_usd": assembly["spent_usd"]}


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


#: The job this process works on (one at a time).
_RUNNING: dict[str, asyncio.Task[Any]] = {}
_WORKER = f"{os.getenv('WEBSITE_INSTANCE_ID', 'local')[:12]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


async def run_job(store: JobStore, job_id: str, make_engines: EngineFactory, *, lease_seconds: int = 900,
                  worker: str = _WORKER) -> dict[str, Any] | None:
    """Work *job_id* to the end (or to a stop: cancel, budget, error). None
    when another worker holds its lease or it is finished."""
    job = _take_lease(store, job_id, worker, lease_seconds)
    if job is None:
        return None
    beat = asyncio.create_task(_heartbeat(store, job_id, worker, lease_seconds))
    try:
        return await _work(store, job_id, make_engines)
    finally:
        beat.cancel()
        try:
            job = store.read(job_id)
            if (job.get("lease") or {}).get("worker") == worker:
                job["lease"] = None
                store.write(job)
        except KeyError:
            pass  # deleted meanwhile


async def _work(store: JobStore, job_id: str, make_engines: EngineFactory) -> dict[str, Any]:
    job = store.update(job_id, status=RUNNING, started_at=store.read(job_id).get("started_at") or _iso(_now()),
                       error=None)
    rows = _input_rows((store.path(job_id) / "input.xlsx").read_bytes())
    for chunk in job["chunks"]:
        if chunk["status"] == "done":
            continue
        while True:
            job = store.read(job_id)
            if job.get("cancel"):
                return store.update(job_id, status=CANCELLED, finished_at=_iso(_now()))
            cap = job.get("max_cost_usd")
            if cap and job["spent_usd"] >= cap:
                return store.update(job_id, status=STOPPED_BUDGET, finished_at=_iso(_now()))
            try:
                stats = await _run_chunk(store, job, chunk, rows, make_engines)
                break
            except Exception as exc:  # noqa: BLE001 - recorded; the chunk is tried again
                logger.exception("VAT job %s: chunk %d failed", job_id, chunk["n"])
                job = store.read(job_id)
                c = job["chunks"][chunk["n"]]
                c["attempts"] += 1
                job["error"] = f"chunk {chunk['n']}: {exc}"
                if c["attempts"] >= MAX_CHUNK_ATTEMPTS:
                    job.update(status=FAILED, finished_at=_iso(_now()))
                store.write(job)
                if job["status"] == FAILED:
                    return job
        job = store.read(job_id)
        job["chunks"][chunk["n"]].update(status="done", **stats)
        job["spent_usd"] = round(job["spent_usd"] + stats["spent_usd"], 4)
        job["companies_done"] += stats["companies"]
        job["http_retries"] += stats["http_retries"]
        job["rate_limit_wait_s"] = round(job["rate_limit_wait_s"] + stats["rate_limit_wait_s"], 1)
        store.write(job)
        logger.info("VAT job %s: chunk %d/%d done (%d rows, $%.2f, %d retries)", job_id, chunk["n"] + 1,
                    len(job["chunks"]), len(chunk["rows"]), stats["spent_usd"], stats["http_retries"])
    store.update(job_id, status=ASSEMBLING)
    try:
        done = await _assemble(store, store.read(job_id), make_engines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("VAT job %s: assembly failed", job_id)
        job = store.read(job_id)
        job["error"] = f"assembly: {exc}"
        job["assembly_attempts"] = job.get("assembly_attempts", 0) + 1
        if job["assembly_attempts"] >= MAX_CHUNK_ATTEMPTS:
            job.update(status=FAILED, finished_at=_iso(_now()))
        store.write(job)
        return job
    job = store.read(job_id)
    job.update(status=DONE, finished_at=_iso(_now()), summary=done["summary"], error=None,
               spent_usd=round(job["spent_usd"] + done["assembly_spent_usd"], 4))
    store.write(job)
    return job


def _next_job(store: JobStore) -> str | None:
    """The oldest unfinished job nobody holds a live lease on."""
    for job_id in store.ids():
        try:
            job = store.read(job_id)
        except (KeyError, ValueError):
            continue
        if job["status"] not in FINAL and _lease_free(job, _WORKER):
            return job_id
    return None


async def _work_queue(store: JobStore, make_engines: EngineFactory, lease_seconds: int,
                      first: str | None) -> None:
    """Work *first*, then every other waiting job, one after another."""
    job_id = first
    while job_id is not None:
        try:
            await run_job(store, job_id, make_engines, lease_seconds=lease_seconds)
        except Exception:  # noqa: BLE001 - logged; the lease runs out and the timer retries
            logger.exception("VAT job %s: worker stopped", job_id)
            return
        job_id = _next_job(store)


def busy() -> bool:
    """This process is working on a job."""
    return any(not t.done() for t in _RUNNING.values())


def start(store: JobStore, make_engines: EngineFactory, lease_seconds: int,
          job_id: str | None = None) -> str | None:
    """Work *job_id* (else the next waiting job), then the rest of the queue,
    in the background of this process. Called on upload and by the timer
    trigger (:func:`resume_jobs`). Nothing when this process already works on
    a job: that worker takes the queue in order. The job started, or None."""
    if busy():
        return None
    job_id = job_id or _next_job(store)
    if job_id is None:
        return None
    _RUNNING.clear()
    _RUNNING["worker"] = asyncio.get_running_loop().create_task(
        _work_queue(store, make_engines, lease_seconds, job_id))
    return job_id


def resume_jobs(store: JobStore, make_engines: EngineFactory, lease_seconds: int) -> str | None:
    """The timer trigger's work (every minute): continue a job whose worker
    is gone (its lease ran out) or that waits. Returns at once; the job runs
    in the background."""
    return start(store, make_engines, lease_seconds)


async def wait_idle() -> None:
    """Until this process's worker is done (tests, shutdown)."""
    for task in list(_RUNNING.values()):
        await asyncio.gather(task, return_exceptions=True)


def partial_rows(store: JobStore, job_id: str) -> list[tuple[int, dict[str, Any]]]:
    """``(row index, output row)`` of every finished chunk, in input order:
    the partial result while a job runs or after it stopped."""
    folder = store.path(job_id) / "chunks"
    out: list[tuple[int, dict[str, Any]]] = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        out.extend(zip(data["rows"], data["out"]))
    return sorted(out, key=lambda p: p[0])


def partial_workbook(store: JobStore, job_id: str) -> bytes:
    """The finished chunks' rows as a workbook (sheet Data; no Summary).
    The final result is ``result.xlsx``, written by the assembly."""
    from openpyxl import Workbook

    from vat.xlsx import cell_value

    rows = [r for _, r in partial_rows(store, job_id)]
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    headers = list(dict.fromkeys(k for r in rows for k in r))
    ws.append(headers)
    for r in rows:
        ws.append([cell_value(r.get(h)) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
