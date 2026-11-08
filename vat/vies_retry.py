"""VIES verification of proposed VAT IDs, out of the lookup path (Step AF-2).

The lookup never asks VIES itself. Every candidate leaves it with
``VAT_Found_Validation_Status = PENDING`` and the issue ``VIES_PENDING``
(:func:`vat.lookup.vies_pending`; a candidate VIES did not answer under the
old inline check, ``UNAVAILABLE``, is treated the same) and its number is
queued here. It keeps its decision, but nothing is written to SAP while it is
PENDING (:func:`vat.lookup.sap_update_eligible`). A queued number is due at
once, then, while VIES gives no answer:

* hourly for the first :data:`HOURLY_FOR` after the number was queued;
* then daily for :data:`DAILY_FOR`;
* then it expires (the row stays PENDING).

:meth:`ViesRetryQueue.process` asks VIES for every number that is due, in
one batch (the client's concurrency and pacing; VALID and INVALID answers
land in the VIES cache, so a re-run of the workbook replays them), and
:func:`apply_answers` writes the answers into result rows: VALID clears the
issue, sets the validation status and date and recomputes the review tier;
INVALID turns the row into MANUAL_REVIEW, LOW, with ``VIES_INVALID``; no
answer leaves it PENDING. The batch runner (``tools/vat_validate_xlsx.py``)
runs the queue at start-up and at the end of every run;
``tools/vat_vies_retry.py`` runs it alone (a scheduler, hourly).

The queue is one JSON file under the evidence cache root
(``<root>/vies_retry/queue.json``); without a root it lives in memory only.
A frozen run never writes it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from vat.entity_match import VIES_INVALID, VIES_PENDING, VIES_UNAVAILABLE, sort_issues
from vat.lookup import (
    CONF_LOW,
    CONFLICT,
    FOUND_UNVERIFIED,
    FOUND_VERIFIED,
    MANUAL_REVIEW,
    VALIDATION_PENDING,
    VALIDATION_UNAVAILABLE,
    review_tier,
)
from vat.normalize import assess_format
from vat.vies_client import INVALID, VALID, VIESClient

logger = logging.getLogger(__name__)

#: Row validation states the queue answers.
QUEUED_STATES = frozenset({VALIDATION_PENDING, VALIDATION_UNAVAILABLE})
HOURLY_FOR = timedelta(hours=24)
DAILY_FOR = timedelta(days=7)
PENDING = "PENDING"
EXPIRED = "EXPIRED"


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def next_attempt(queued_at: datetime, now: datetime) -> datetime | None:
    """When to ask next, or None once the schedule is over."""
    step = timedelta(hours=1) if now - queued_at < HOURLY_FOR else timedelta(days=1)
    nxt = now + step
    return nxt if nxt <= queued_at + HOURLY_FOR + DAILY_FOR else None


@dataclass
class RetryEntry:
    vat: str
    queued_at: str
    next_at: str | None
    attempts: int = 0
    #: PENDING, EXPIRED, or VIES's answer (VALID / INVALID).
    state: str = PENDING
    request_date: str | None = None


class ViesRetryQueue:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: dict[str, RetryEntry] = {}
        if path is not None and path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.entries = {k: RetryEntry(**v) for k, v in raw.items()}
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("VIES retry queue %s unreadable, starting empty: %s", path, exc)

    @classmethod
    def for_client(cls, client: VIESClient) -> "ViesRetryQueue":
        root = client.evidence_cache.root
        return cls(Path(root) / "vies_retry" / "queue.json" if root else None)

    def add(self, vat: str, now: datetime | None = None) -> bool:
        """Queue *vat* (normalised), due at once. True when it was not already
        pending."""
        now = now or datetime.now(timezone.utc)
        current = self.entries.get(vat)
        if current is not None and current.state == PENDING:
            return False
        self.entries[vat] = RetryEntry(vat, _iso(now), _iso(now))
        return True

    def due(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        return sorted(v for v, e in self.entries.items()
                      if e.state == PENDING and e.next_at and _parse(e.next_at) <= now)

    def record(self, vat: str, status: str, now: datetime | None = None,
               request_date: str | None = None) -> None:
        """VIES's answer for *vat*: VALID / INVALID end its schedule; anything
        else moves it to the next slot, or expires it."""
        now = now or datetime.now(timezone.utc)
        entry = self.entries[vat]
        entry.attempts += 1
        if status in (VALID, INVALID):
            entry.state, entry.next_at, entry.request_date = status, None, request_date
            return
        nxt = next_attempt(_parse(entry.queued_at), now)
        entry.next_at = _iso(nxt) if nxt else None
        if nxt is None:
            entry.state = EXPIRED

    def answers(self) -> dict[str, tuple[str, str | None]]:
        """``vat → (VALID | INVALID, request date)`` for every answered entry."""
        return {v: (e.state, e.request_date) for v, e in self.entries.items()
                if e.state in (VALID, INVALID)}

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: asdict(v) for k, v in self.entries.items()},
                                        indent=1, sort_keys=True), encoding="utf-8")

    async def process(self, client: VIESClient, now: datetime | None = None,
                      *, everything: bool = False) -> dict[str, str]:
        """Ask VIES for every due number (every pending one with
        *everything*), in one batch. Returns ``vat → status`` for the numbers
        asked; nothing when VIES is switched off."""
        now = now or datetime.now(timezone.utc)
        vats = sorted(v for v, e in self.entries.items() if e.state == PENDING) if everything \
            else self.due(now)
        if not vats or not client.enabled:
            return {}
        formats = {vat: assess_format(vat, "DE") for vat in vats}
        results = await client.check_many(formats.values())
        asked: dict[str, str] = {}
        for vat, fmt in formats.items():
            result = results.get(fmt.normalized or vat)
            status = result.status if result else "ERROR"
            self.record(vat, status, now, result.request_date if result else None)
            asked[vat] = status
        return asked


def enqueue(queue: ViesRetryQueue, results: Iterable[Any], now: datetime | None = None) -> int:
    """Queue every proposal VIES has not answered (PENDING, UNAVAILABLE).
    Returns how many numbers were newly queued."""
    added = 0
    for r in results:
        if r is not None and r.value and r.validation_status in QUEUED_STATES:
            added += queue.add(r.value, now)
    return added


_PREFIXES = ("VAT_Found_", "VAT_Grounded_")


def apply_answers(rows: list[dict[str, Any]], answers: dict[str, tuple[str, str | None]]) -> int:
    """Write VIES's answers into result rows, in place. Only rows whose
    validation status is PENDING (or UNAVAILABLE) are touched; a number VIES
    has not answered stays so. Returns how many rows changed."""
    changed = 0
    for row in rows:
        for p in _PREFIXES:
            value = row.get(f"{p}Value")
            if not value or row.get(f"{p}Validation_Status") not in QUEUED_STATES:
                continue
            canon = assess_format(value, "DE").normalized or value
            if canon not in answers:
                continue
            status, date = answers[canon]
            issues = [i for i in (row.get(f"{p}Issues") or "").split("; ")
                      if i and i not in (VIES_UNAVAILABLE, VIES_PENDING)]
            row[f"{p}Validation_Status"] = status
            row[f"{p}Validation_Date"] = date
            if status == VALID:
                if str(row.get(f"{p}Status") or "").startswith(FOUND_UNVERIFIED + ":VIES_"):
                    row[f"{p}Status"] = FOUND_VERIFIED
                if row.get(f"{p}Decision") == MANUAL_REVIEW:
                    row[f"{p}Confidence"] = review_tier(
                        row.get(f"{p}Name_Match"), row.get(f"{p}Address_Match"),
                        row.get(f"{p}Source_Class"), issues, row.get(f"{p}Evidence"))
            else:
                issues.append(VIES_INVALID)
                row[f"{p}Status"] = f"{FOUND_UNVERIFIED}:VIES_{INVALID}"
                if row.get(f"{p}Decision") != CONFLICT:
                    row[f"{p}Decision"] = MANUAL_REVIEW
                    row[f"{p}Confidence"] = CONF_LOW
            row[f"{p}Issues"] = "; ".join(sort_issues(issues)) or None
            changed += 1
    return changed


def apply_to_workbook(wb: Any, answers: dict[str, tuple[str, str | None]]) -> int:
    """:func:`apply_answers` on the data sheet of a lookup output workbook,
    in place (only the cells that change). Returns how many rows changed."""
    from vat.xlsx import _data_sheet

    ws = _data_sheet(wb)
    rows = list(ws.iter_rows())
    if not rows:
        return 0
    headers = ["" if c.value is None else str(c.value).strip() for c in rows[0]]
    changed = 0
    from vat.review_columns import apply_vies
    from vat.validate import _LOOKUP_ROLE_FIELDS, resolve_columns

    present = dict.fromkeys(h for h in headers if h)
    cols, lcols = resolve_columns(present), resolve_columns(present, _LOOKUP_ROLE_FIELDS)
    for cells in rows[1:]:
        row = {h: c.value for h, c in zip(headers, cells) if h}
        before = dict(row)
        # The technical columns (VAT_OUTPUT_COLUMNS=full) and the reviewer's (both modes).
        raw = apply_answers([row], answers)
        sap_name = " ".join(str(row[cols[f"name_{n}"]]) for n in range(1, 5)
                            if cols.get(f"name_{n}") and row.get(cols[f"name_{n}"]))
        review = apply_vies(row, answers, sap_name or None, row.get(lcols["city"]) if lcols.get("city") else None)
        if raw or review:
            changed += 1
            for h, c in zip(headers, cells):
                if h and row.get(h) != before.get(h):
                    c.value = row[h]
    return changed


async def drain(client: VIESClient, queue: ViesRetryQueue | None = None, *,
                everything: bool = False, now: datetime | None = None) -> tuple[ViesRetryQueue, dict[str, str]]:
    """Ask VIES for the queue's due numbers and save it. Nothing on a frozen
    run or with VIES off. Returns the queue and ``vat → status`` asked."""
    queue = queue or ViesRetryQueue.for_client(client)
    if client.frozen or not client.enabled:
        return queue, {}
    asked = await queue.process(client, now, everything=everything)
    queue.save()
    return queue, asked


def summary_line(asked: dict[str, str]) -> str:
    return (f"{len(asked)} number(s) asked: {sum(s == VALID for s in asked.values())} VALID, "
            f"{sum(s == INVALID for s in asked.values())} INVALID, "
            f"{sum(s not in (VALID, INVALID) for s in asked.values())} still PENDING")
