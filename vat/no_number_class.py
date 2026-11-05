"""Why an entity has no number: the A–F classes of the run reports.

Judged from the entity's usage record (``tools/vat_validate_xlsx.py
--usage-out``) and SAP's number, if any:

  A  no own host answered (none in the results, or none reachable)
  B  an own host was in the results but not fetched (fetch limit), and no own
     Impressum-type page was read
  C  SAP's number is on a fetched page, outside the sections the reader saw
  D  SAP's number is in the sections, not extracted
  E  an own Impressum-type page was fetched and read; SAP's number is on no
     page fetched
  F  SAP's number was read and dropped, or is on a rejected host's page

A fetched-and-read own Impressum can only be C, D or E (Step AF rule 15):
hosts left unfetched are noted, never the class.
"""

from __future__ import annotations

import re
from typing import Any

CLASSES = ("A", "B", "C", "D", "E", "F")


def _norm(v: Any) -> str | None:
    return re.sub(r"[^0-9A-Z]", "", str(v or "").upper()) or None


def classify(usage: dict[str, Any], sap_vat: str | None) -> tuple[str, str]:
    """``(class, why)`` for an entity with no number."""
    known = _norm(sap_vat)
    pages, hosts = usage.get("pages") or {}, usage.get("hosts") or {}
    on = [(url, i) for url, i in pages.items() if known and known in (i.get("hits") or [])]
    answered = any(h.get("answered") and not h.get("rejected") for h in hosts.values())
    dropped = [d for d in usage.get("dropped") or [] if _norm(d.get("vat_id")) == known]
    not_fetched = usage.get("not_fetched") or []
    if dropped:
        return "F", f"read, dropped: {dropped[0]['why']}"
    rejected = [(u, i) for u, i in on if i.get("rejected")]
    if rejected:
        return "F", f"on {rejected[0][0]}, host rejected ({rejected[0][1]['rejected']})"
    if any(known in (i.get("hits_in_sections") or []) for _, i in on):
        return "D", f"in the sections of {on[0][0]}, not extracted"
    if on:
        return "C", f"on {on[0][0]}, outside the sections"
    if answered:
        why = ("own Impressum-type page read; SAP's number on no page fetched" if known
               else "own Impressum-type page read; no number (SAP has none either)")
        if not_fetched:
            why += "; also not fetched: " + ", ".join(n["url"] for n in not_fetched)
        return "E", why
    if not_fetched:
        return "B", "own host(s) in the results not fetched: " + ", ".join(n["url"] for n in not_fetched)
    return "A", "no own host in the results" if not hosts else "own host(s) rejected / blocked / unreadable"
