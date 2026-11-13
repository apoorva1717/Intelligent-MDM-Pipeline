"""Step AM (2026-11-12): the customer's email domain for Not found rows.

A Not found row with reason NO_WEBSITE_FOUND whose email domain is the
customer's own (:mod:`vat.email_domain`) is looked up once more by Step AL's
route B alone (:meth:`vat.step_al.SecondLook._route_b`): the domain's
``/impressum``, else ``/imprint``, else the home page's legal-notice link;
the host counts only when its legal notice names the entity; then the
reader on that page and Step AL's :func:`vat.step_al.gate`.

No search, no Peppol, no agent pass, no VIES. Per entity at most
:data:`vat.step_al.MAX_FETCHES` fetches and :data:`vat.step_al.MAX_FOLLOWS`
follows, one reader call.

Outcome per entity (``usage["al"]["outcome"]``): ``candidate`` (a number
passed the gate: the normal decision and flags), ``nothing_passes`` (the
row stays Not found) or ``not_rechecked`` (the domain refused us, timed out
or a model call failed: the row stays as it was).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from vat.entity_match import company_domain
from vat.lookup import LLM_WEB, LOOKUP_INCOMPLETE, NOT_FOUND, NOT_RUN, FoundResult, LookupInput
from vat.lookup_web import LLM_WEB_NO_ANSWER, LLM_WEB_SEARCH, with_not_found_reason
from vat.name_prep import NamePlan, prepare
from vat.step_al import (
    CANDIDATE_OUTCOME, MAX_FETCHES, NOT_RECHECKED, NOTHING_PASSES, ROUTE_B, SecondLook,
)

STEP = "AM"
EM_COLUMNS = ["EM_Domain", "EM_Outcome", "EM_Attempts", "EM_Not_Rechecked_Reason", "EM_Cost_USD"]


class EmailLook(SecondLook):
    """Route B alone, on the email domain the caller put in ``inp.email``."""

    @property
    def agent_version(self) -> str:
        return f"{super().agent_version}|step_am_v1"

    async def _lookup(self, inp: LookupInput) -> FoundResult:
        if not inp.name_1:
            return FoundResult(LLM_WEB, NOT_RUN, flags=(LLM_WEB_SEARCH, "NO_NAME"))
        plan = prepare(inp.names)
        usage: dict[str, Any] = {"fetches": 0, "cost_usd": 0.0, "follows": 0, "searches": 0, "readers": 0,
                                 "pdf_fetches": 0, "archive_fetches": 0, "max_fetches": MAX_FETCHES}
        plan = await self._name2(inp, plan, usage)
        m_inp = replace(inp, names=(plan.entity, None, None, None))
        chosen, al = await self._routes(m_inp, plan, usage)
        usage["al"] = al
        base = (LLM_WEB_SEARCH,)
        if chosen is not None:
            al["outcome"] = CANDIDATE_OUTCOME
            result = replace(chosen, usage=usage)
        else:
            al["outcome"] = NOT_RECHECKED if al["failures"] else NOTHING_PASSES
            status, flags = NOT_FOUND, (*base, LLM_WEB_NO_ANSWER)
            if al["failures"]:
                status, flags = f"{LOOKUP_INCOMPLETE}:{al['failures'][0]}", (*base, *al["failures"])
            result = FoundResult(LLM_WEB, status, flags=flags, usage=usage)
        return with_not_found_reason(self._with_plan(result, plan))

    async def _routes(self, inp: LookupInput, plan: NamePlan, usage: dict[str, Any]
                      ) -> tuple[FoundResult | None, dict[str, Any]]:
        pages: dict[str, dict[str, Any]] = {}
        hosts: dict[str, dict[str, Any]] = {}
        usage["pages"], usage["hosts"] = pages, hosts
        al: dict[str, Any] = {"routes": [], "attempts": [], "failures": [], "route": None}
        domain = company_domain(inp.website, inp.email)
        if not domain:
            return None, al
        al["routes"].append(ROUTE_B)
        found = await self._route_b(domain, inp, plan, usage, pages, hosts, (LLM_WEB_SEARCH,))
        chosen = await self._judge(ROUTE_B, found, plan, al)
        self._blocked(hosts, set(), al)
        if chosen is None and not al["failures"]:
            entry = next(iter(hosts.values()), {})
            statuses = [str((pages.get(u) or {}).get("status") or "") for u in entry.get("fetched") or []]
            if statuses and all(s.startswith("PAGE_UNAVAILABLE") for s in statuses):
                # Not one page came back (TLS refused, connection error): a
                # technical failure, not "no legal notice".
                al["failures"].append(f"UNREACHABLE_{statuses[0].rpartition(':')[2]}")
                al["attempts"].append({"route": ROUTE_B, "value": None, "gate": al["failures"][-1]})
            elif entry.get("rejected"):
                al["attempts"].append({"route": ROUTE_B, "value": None, "gate": entry["rejected"]})
        if chosen is not None:
            al["route"] = ROUTE_B
        return chosen, al


def row_outcome(result: FoundResult | None, old: dict[str, Any], domain: str, sap_name: str | None,
                postal_code: str | None, city: str | None) -> dict[str, Any]:
    """The reviewer's columns and :data:`EM_COLUMNS` of one row: the new
    candidate's, else the old row's unchanged."""
    from vat.review_columns import REVIEW_COLUMNS, review_columns

    al = ((result.usage or {}).get("al") or {}) if result is not None else {}
    outcome = al.get("outcome") or NOT_RECHECKED
    if outcome == CANDIDATE_OUTCOME:
        cols = review_columns(result, sap_name, postal_code, city)
    else:
        cols = {c: old.get(c) for c in REVIEW_COLUMNS}
    attempts = "; ".join(f"{a.get('value') or '-'}:{a.get('gate') or 'PASS'}" for a in al.get("attempts") or [])
    costs = ((result.usage or {}).get("costs") or {}) if result is not None else {}
    return {**cols,
            "EM_Domain": domain,
            "EM_Outcome": {CANDIDATE_OUTCOME: "new number", NOTHING_PASSES: "nothing found"}.get(
                outcome, "not re-checked"),
            "EM_Attempts": attempts or None,
            "EM_Not_Rechecked_Reason": ", ".join(al.get("failures") or []) or (
                None if outcome != NOT_RECHECKED else "no result"),
            "EM_Cost_USD": round(float(costs.get("Total_USD") or 0.0), 5) if costs else None}
