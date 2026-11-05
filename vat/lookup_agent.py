"""Step AG: the second-tier agentic pass on the entities the hybrid method
left without a number (``vat/lookup_web.py``).

The model searches and opens pages itself (model mode on the LLM
deployment): at most :data:`MAX_SEARCHES` searches and :data:`MAX_OPENS`
page opens, reasoning :data:`EFFORT`. It is given the full record, what the
pipeline already knows (Name-2 class, search name, segment, the A–F class
and the hosts already tried, VAT IDs on sibling records of the entity), and
is told to use its own knowledge of the organisation's domain, current name,
parent or successor to decide where to look. A number may still only come
from a page it opened.

The gates after the loop are the hybrid path's: our code fetches the cited
URL itself (``_fetch_hosts``: the host check on the legal-notice block, the
legal form), the hybrid reader reads it (``_read``: the page check, address
match, source class, rule 12/13), then the decision and the tiers. VIES is
not asked here: the result is PENDING (``vat.lookup.vies_pending``).

* The cited page is fetched but its host is not accepted by the host check
  (an unverified host whose Impressum does not name the record, a rejected
  host): the agent's own reading is judged on the page as fetched
  (``judge_page``) and kept for review with ``AGENT_HOST_NOT_ACCEPTED``,
  never AUTO.
* The cited page cannot be fetched, or the agent's number is not on it: the
  number is kept, ``EVIDENCE_NOT_VERIFIED`` (review, LOW); a number our
  reader read there instead is in the flags (``PAGE_STATES``).
* The agent says the record was renamed or taken over and the page's name is
  not the record's: ``SUCCESSOR_CANDIDATE`` (review).

Every search and page open is billed (``web_search_calls``,
``page_open_calls``; cost line ``agent``)."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from vat.entity_match import (
    EVIDENCE_NOT_VERIFIED,
    NAME_EXACT,
    PARENT_ENTITY_CANDIDATE,
    SUCCESSOR_CANDIDATE,
)
from vat.lookup import (
    AUTO_ACCEPT,
    FOUND_UNVERIFIED,
    LLM_WEB,
    LOOKUP_INCOMPLETE,
    NOT_FOUND,
    NOT_RUN,
    TIER_REVIEW,
    FoundResult,
    LookupInput,
    today,
)
from vat.lookup_grounded import Reading, id_on_page
from vat.lookup_web import (
    BUDGET_EXCEEDED,
    ENGINE,
    LLM_WEB_SEARCH,
    NOT_ON_SOURCE_PAGE,
    SEARCH_VISITED,
    WebSearchLookup,
    _clean,
    company_domain,
    was_visited,
)
from vat.name2_classifier import segment_of
from vat.name_prep import NamePlan, prepare
from vat.normalize import assess_format
from vat.prompts import LLM_WEB_USER_PROMPT_TEMPLATE
from vat.web_pages import CANDIDATE, PARENT_CANDIDATE, host_kind, host_of, institutional_kind

MAX_SEARCHES = 5
MAX_OPENS = 8
EFFORT = "medium"
#: Reasoning tokens count against it; medium effort over 13 tool calls needs room.
MAX_OUTPUT_TOKENS = 12000
LABEL = "agent"

#: Flags.
AGENT = "AGENT"
AGENT_NO_ANSWER = "AGENT_NO_ANSWER"
#: The cited page was read, but the host check did not accept its host.
AGENT_HOST_NOT_ACCEPTED = "AGENT_HOST_NOT_ACCEPTED"
#: The cited page could not be fetched by our code.
AGENT_SOURCE_UNREADABLE = "AGENT_SOURCE_UNREADABLE"
#: Our reader read another number on the cited page than the agent reported.
AGENT_VALUE_DIFFERS = "AGENT_VALUE_DIFFERS"

AGENT_SYSTEM_PROMPT = (
    "You find a German organisation's VAT identification number (USt-IdNr.) "
    "by searching the web and opening pages, the way a careful researcher "
    "would. Use what you know about the organisation (its own domain, its "
    "current name if it was renamed, its parent organisation or successor) "
    "to decide where to look. A number may only come from a page you opened "
    "in this search, never from memory. Return valid JSON only. No markdown."
)

_V3_RULES = LLM_WEB_USER_PROMPT_TEMPLATE[
    LLM_WEB_USER_PROMPT_TEMPLATE.index("Rules:\n"):
    LLM_WEB_USER_PROMPT_TEMPLATE.index("Organisation (from our customer master data):")]
_V3_RECORD = LLM_WEB_USER_PROMPT_TEMPLATE[
    LLM_WEB_USER_PROMPT_TEMPLATE.index("Organisation (from our customer master data):"):]

AGENT_USER_PROMPT_TEMPLATE = (
    "Find a page where the organisation at the end of this message itself "
    "states its USt-IdNr. and report it. Our automated pipeline searched for "
    "it and failed; what it tried is listed at the end.\n"
    "Budget: at most {max_searches} web searches, and at most {max_opens} "
    "pages opened on top of them; opening a page is not a search. A further "
    "call is refused. When the budget is used up, answer with what you "
    "have; if no page you opened states the number, vat_id and source_url "
    "are null.\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "source_url": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "published_address": "str or null",\n'
    '  "confidence": "high | medium | low",\n'
    '  "relation": "same_entity | renamed | successor | parent | not_found",\n'
    '  "pages_opened": ["str"],\n'
    '  "notes": "str or null"\n'
    "}}\n"
    + _V3_RULES.rstrip("\n") + "\n"
    "9. Use your own knowledge of this organisation to plan: its own domain "
    "(open it directly), its current name if it was renamed or merged, the "
    "legal entity a department, institute, clinic or site belongs to (rule "
    "2: report that entity's number), its parent. A renamed company is the "
    "same legal entity: its number under the new name is the right one "
    "(relation renamed; attributed_to is the name the page gives). A "
    "successor that took over the business is another entity: report its "
    "number only when the page says it continues this one (relation "
    "successor). relation parent: the number is the parent's, as rule 2 "
    "allows for a department or site.\n"
    "10. The pipeline context below lists the hosts already tried and why "
    "they failed: try something else first (another host, a deeper page, "
    "the procurement or invoicing page, a PDF). VAT IDs on our other records "
    "of the same entity are context only, never a source: report a number "
    "only from a page you opened that states it.\n"
    "11. confidence: high when the page is the organisation's own legal "
    "notice and names it; medium when it is another page of its own or its "
    "parent's site; low otherwise. pages_opened: every URL you opened, in "
    "order. notes: at most 30 words on how you found it or why not.\n\n"
    + _V3_RECORD + "\n\n"
    "Pipeline context:\n"
    "Name-2 class: {name_class}\n"
    "Search name used: {search_name}\n"
    "Segment: {segment}\n"
    "Why the pipeline found nothing (class {af_class}): {af_why}\n"
    "Hosts already tried:\n{hosts_tried}\n"
    "VAT IDs on our other records of this entity: {sibling_vat_ids}"
)

AGENT_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["vat_id", "source_url", "evidence_quote", "attributed_to", "published_address",
                 "confidence", "relation", "pages_opened", "notes"],
    "properties": {
        **{k: {"type": ["string", "null"]} for k in (
            "vat_id", "source_url", "evidence_quote", "attributed_to", "published_address", "notes")},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "relation": {"type": "string", "enum": ["same_entity", "renamed", "successor", "parent", "not_found"]},
        "pages_opened": {"type": "array", "items": {"type": "string"}},
    },
}


class AgentLookup(WebSearchLookup):
    """The agentic pass. *context*: per customer, the pipeline's knowledge
    (``name_class``, ``search_name``, ``af_class``, ``af_why``,
    ``hosts_tried`` as lines, ``sibling_vat_ids``)."""

    def __init__(self, settings: Any, vies: Any, *, context: dict[str, dict[str, Any]] | None = None,
                 **kw: Any) -> None:
        super().__init__(replace(settings, vat_web_query_mode="hybrid"), vies, **kw)
        self.context = context or {}
        self._agent = replace(self.limits, max_searches=MAX_SEARCHES, max_opens=MAX_OPENS,
                              max_output_tokens=MAX_OUTPUT_TOKENS, schema=AGENT_SCHEMA, cache_key="vat_agent")

    @property
    def agent_version(self) -> str:
        return f"{LLM_WEB}|vat_agent_ag_v1|{super().agent_version.rsplit('|', 1)[-1]}"

    def agent_prompt(self, inp: LookupInput, plan: NamePlan) -> str:
        ctx = self.context.get(str(inp.customer), {})
        names = list(inp.names) + [None] * (4 - len(inp.names))
        street = " ".join(p for p in (inp.street, inp.house_number) if p)
        return AGENT_USER_PROMPT_TEMPLATE.format(
            max_searches=MAX_SEARCHES, max_opens=MAX_OPENS,
            name_1=names[0] or "", name_2=names[1] or "", name_3=names[2] or "", name_4=names[3] or "",
            street=street, postal_code=inp.postal_code or "", city=inp.city or "", country=inp.country or "",
            domain=company_domain(inp.website, inp.email) or "", register_number=inp.register_number or "",
            record_status=("deleted in our system (an archived snapshot of its website may be used)"
                           if inp.deleted else "active"),
            name_class=ctx.get("name_class") or plan.cls, search_name=ctx.get("search_name") or plan.search_name,
            segment=segment_of(plan), af_class=ctx.get("af_class") or "?", af_why=ctx.get("af_why") or "",
            hosts_tried="\n".join(f"- {h}" for h in ctx.get("hosts_tried") or []) or "- none",
            sibling_vat_ids=", ".join(ctx.get("sibling_vat_ids") or []) or "none",
        )

    async def lookup(self, inp: LookupInput) -> FoundResult:
        started = time.perf_counter()
        result = await self._agent_lookup(inp)
        from vat.costs import entity_costs
        result = replace(result, usage={**(result.usage or {}), "costs": entity_costs(
            result.usage or {}, self.meter.table, self.meter.overrides)})
        self.usage_log.append({
            "customer": inp.customer, "name_1": inp.name_1, "city": inp.city,
            "status": result.status, "value": result.value, "decision": result.decision,
            "issues": list(result.all_issues), "flags": list(result.flags),
            "seconds": round(time.perf_counter() - started, 2), **(result.usage or {}),
        })
        return result

    async def _agent_lookup(self, inp: LookupInput) -> FoundResult:
        if not inp.name_1:
            return FoundResult(LLM_WEB, NOT_RUN, flags=(AGENT, "NO_NAME"))
        usage: dict[str, Any] = {"fetches": 0, "cost_usd": 0.0, "follows": 0, "pdf_fetches": 0,
                                 "archive_fetches": 0, "readers": 0}
        plan = prepare(inp.names)
        if self.meter.exhausted:
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(AGENT, BUDGET_EXCEEDED), usage=usage)
        out = await self._call(LABEL, AGENT_SYSTEM_PROMPT, self.agent_prompt(inp, plan), self._agent, usage,
                               effort=EFFORT)
        if isinstance(out, FoundResult):
            return replace(out, flags=(*out.flags, AGENT), usage=usage)
        answer, visited = out
        usage.update(agent_searches=usage.get("search_calls") or 0, agent_opens=usage.get("other_tool_calls") or 0,
                     agent_confidence=answer.get("confidence"), agent_relation=answer.get("relation"),
                     agent_pages_opened=list(answer.get("pages_opened") or []), agent_notes=_clean(answer.get("notes")),
                     agent_value=_clean(answer.get("vat_id")), agent_source=_clean(answer.get("source_url")),
                     agent_visited=list(visited))
        base = (LLM_WEB_SEARCH, f"{SEARCH_VISITED}:{len(visited)}", AGENT, f"{ENGINE}:agent")
        m_inp = replace(inp, names=(plan.entity, None, None, None))
        result = await self._judge_agent(m_inp, plan, answer, visited, usage, base)
        return self._with_plan(replace(result, usage=usage), plan)

    async def _judge_agent(self, inp: LookupInput, plan: NamePlan, answer: dict[str, Any], visited: list[str],
                           usage: dict[str, Any], base: tuple[str, ...]) -> FoundResult:
        vat_id, source = _clean(answer.get("vat_id")), _clean(answer.get("source_url"))
        if not vat_id:
            return FoundResult(LLM_WEB, NOT_FOUND, flags=(*base, AGENT_NO_ANSWER))
        reading = Reading(vat_id=vat_id, attributed_to=_clean(answer.get("attributed_to")),
                          evidence_quote=_clean(answer.get("evidence_quote")),
                          published_address=_clean(answer.get("published_address")))
        if not source:
            return self._unverified(inp, reading, None, (*base, AGENT_SOURCE_UNREADABLE))
        if not was_visited(source, visited):
            base = (*base, "SOURCE_NOT_VISITED")
        # The hybrid path on the cited page: our fetch and host check, then our reader.
        kind = host_kind(source, inp, plan=plan) or institutional_kind(source, plan) or CANDIDATE
        pages: dict[str, dict[str, Any]] = {}
        hosts: dict[str, dict[str, Any]] = {}
        usage["pages"], usage["hosts"] = pages, hosts
        read = await self._fetch_hosts(LABEL, [(source, kind)], inp, usage, pages, hosts, [source], plan=plan)
        best = await self._read(LABEL, read, inp, usage, base, plan) if read else None
        if isinstance(best, FoundResult) and best.status.startswith(LOOKUP_INCOMPLETE):
            return best
        if isinstance(best, FoundResult) and best.value:
            norm = assess_format(vat_id, "DE").normalized or vat_id
            if best.value == norm:
                return self._relation(best, answer)
            if not any(id_on_page(vat_id, p.get("text") or "") for p in read):
                # The agent's number is not on the page it cites: kept, LOW.
                return self._unverified(inp, reading, source, (
                    *base, NOT_ON_SOURCE_PAGE, f"PAGE_STATES:{best.value}"))
            return self._relation(replace(best, flags=(*best.flags, f"{AGENT_VALUE_DIFFERS}:{norm}")), answer)
        # Not accepted by the hybrid gates: the agent's own reading, judged on the page as fetched here.
        entry = hosts.get(host_of(source)) or {}
        flags: set[str] = set()
        checked = await self._pages.judge_page(source, reading, inp, flags, max_chars=self.page_chars)
        if checked is None:
            return self._unverified(inp, reading, source, (*base, AGENT_SOURCE_UNREADABLE, *sorted(flags)))
        judged, on_page = checked
        if not on_page:
            return self._unverified(inp, reading, source, (*base, NOT_ON_SOURCE_PAGE))
        page = pages.get(source) or next(iter(pages.values()), {})
        judged = self._reconcile_name(judged, plan, page)
        extra = [PARENT_ENTITY_CANDIDATE] if page.get("name_on_page") == PARENT_CANDIDATE \
            and PARENT_ENTITY_CANDIDATE not in judged.issues else []
        judged = replace(judged, mode=LLM_WEB, issues=tuple(dict.fromkeys([*judged.issues, *extra])),
                         flags=(*judged.flags, *base, AGENT_HOST_NOT_ACCEPTED,
                                f"HOST_CHECK:{entry.get('rejected') or ('blocked' if entry.get('blocked') else 'unverified')}"),
                         tier=TIER_REVIEW)
        judged = self._relation(judged, answer)
        if judged.decision == AUTO_ACCEPT:  # never AUTO from a host the gates did not accept
            judged = replace(judged, issues=(*judged.issues, EVIDENCE_NOT_VERIFIED))
        return judged

    @staticmethod
    def _relation(result: FoundResult, answer: dict[str, Any]) -> FoundResult:
        if answer.get("relation") in ("renamed", "successor") and result.name_match != NAME_EXACT \
                and SUCCESSOR_CANDIDATE not in result.issues:
            return replace(result, issues=(*result.issues, SUCCESSOR_CANDIDATE))
        return result

    def _unverified(self, inp: LookupInput, reading: Reading, source: str | None,
                    flags: tuple[str, ...]) -> FoundResult:
        """A number the agent reported that our code could not check on its page."""
        fmt = assess_format(reading.vat_id, inp.country)
        return FoundResult(LLM_WEB, FOUND_UNVERIFIED, value=fmt.normalized or reading.vat_id, source_url=source,
                           attributed_to=reading.attributed_to, evidence=reading.evidence_quote, date=today(),
                           flags=flags, tier=TIER_REVIEW, validation_status="NOT_CHECKED",
                           issues=(EVIDENCE_NOT_VERIFIED,))


# ── The agent pass after the hybrid lookup (endpoint, batch runner) ──────────

#: Flags on a hybrid result the agent pass ran on: it found nothing either /
#: the run's agent budget was used up before this entity.
AGENT_TRIED = "AGENT_TRIED"
AGENT_SKIPPED_BUDGET = "AGENT_SKIPPED_BUDGET"


def agent_eligible(result: FoundResult | None) -> bool:
    """A number is expected but none was found: NOT_FOUND (any reason) or
    LOOKUP_INCOMPLETE. Never NOT_FOUND_NO_VAT_EXPECTED (a page says there is
    none, or a public body), never a candidate, never a row not looked up
    (Step AG's input: the Still_no_number rows)."""
    if result is None or result.value:
        return False
    return result.status.split(":", 1)[0] in (NOT_FOUND, LOOKUP_INCOMPLETE)


def hosts_tried(usage: dict[str, Any]) -> list[str]:
    """The hybrid lookup's hosts, as the agent prompt lists them: kind, what
    happened, the pages fetched; then the results it did not fetch."""
    lines = []
    for host, h in (usage.get("hosts") or {}).items():
        if h.get("blocked"):
            why = f"blocked (HTTP {h['blocked']})"
        elif h.get("rejected"):
            why = f"rejected by the host check ({h['rejected']})"
        elif h.get("answered"):
            why = "its Impressum-type page was read: no number for this entity"
        else:
            why = "fetched, no Impressum-type page with content"
        lines.append(f"{host} ({h.get('kind')}): {why}; pages: {', '.join(h.get('fetched') or [])[:300]}")
    for n in usage.get("not_fetched") or []:
        lines.append(f"{n.get('url')}: in the results, not fetched ({n.get('why')})")
    return lines


def context_for(result: FoundResult, sap_vat: str | None, sibling_vat_ids: list[str]) -> dict[str, Any]:
    """What the hybrid lookup knew about the entity, for the agent prompt."""
    from vat.no_number_class import classify

    usage = result.usage or {}
    af_class, af_why = classify(usage, sap_vat)
    if result.status != NOT_FOUND:
        af_why = f"{result.status}; {af_why}"
    return {"name_class": usage.get("name_class"), "search_name": usage.get("search_name"),
            "af_class": af_class, "af_why": af_why, "hosts_tried": hosts_tried(usage),
            "sibling_vat_ids": sibling_vat_ids}


def make_agent(settings: Any, vies: Any) -> AgentLookup:
    """The agent engine with its own stop limit (``VAT_AGENT_BUDGET_USD``)
    and a 600 s web-search timeout (13 tool calls at medium effort)."""
    from vat import llm_cache

    llm_cache.WEB_SEARCH_TIMEOUT = max(llm_cache.WEB_SEARCH_TIMEOUT, 600.0)
    agent = AgentLookup(settings, vies)
    agent.meter.budget = float(getattr(settings, "vat_agent_budget_usd", 8.0) or 0)
    return agent


async def agent_pass(
    agent: AgentLookup, inputs: list[LookupInput | None], found: list[FoundResult | None],
    *, sap: list[str | None] | None = None, siblings: dict[str, list[str]] | None = None,
) -> tuple[list[FoundResult | None], dict[str, Any]]:
    """Step AG on the hybrid results: the agent looks up every entity whose
    result is :func:`agent_eligible` (once per entity, like the hybrid
    lookup), with that result as context; its gates are the hybrid path's
    and its numbers go to the VIES queue (PENDING). A number replaces the
    hybrid result; otherwise the hybrid result stays, flagged
    ``AGENT_TRIED`` (or ``AGENT_SKIPPED_BUDGET``). Either way the row's
    cost lines include the agent's calls. Returns the results and a summary."""
    from vat.costs import entity_costs
    from vat.lookup import run_lookups

    sap = sap or [None] * len(inputs)
    picked = [inp if inp is not None and agent_eligible(r) else None for inp, r in zip(inputs, found)]
    agent.context = {str(inp.customer): context_for(r, s, (siblings or {}).get(str(inp.customer), []))
                     for inp, r, s in zip(picked, found, sap) if inp is not None}
    spent_before = agent.meter.spent
    ran = await run_lookups(agent, picked, concurrency=1) if agent.context else [None] * len(inputs)
    out: list[FoundResult | None] = []
    for hybrid, a in zip(found, ran):
        if a is None:
            out.append(hybrid)
            continue
        calls = [*(hybrid.usage or {}).get("calls", []), *(a.usage or {}).get("calls", [])]
        costs = entity_costs({"calls": calls}, agent.meter.table, agent.meter.overrides)
        if a.value:
            out.append(replace(a, usage={**(a.usage or {}), "costs": costs, "hybrid_status": hybrid.status}))
        else:
            flag = AGENT_SKIPPED_BUDGET if BUDGET_EXCEEDED in a.flags else AGENT_TRIED
            out.append(replace(hybrid, flags=(*hybrid.flags, flag),
                               usage={**(hybrid.usage or {}), "costs": costs, "agent_notes":
                                      (a.usage or {}).get("agent_notes")}))
    tried = [r for r, a in zip(out, ran) if a is not None]
    summary = {
        "rows_eligible": len(tried),
        "entities": len(agent.context),
        "found": sum(1 for r in tried if r.value),
        "decision": {d: sum(1 for r in tried if r.value and r.decision == d)
                     for d in ("AUTO_ACCEPT", "MANUAL_REVIEW", "CONFLICT")},
        "skipped_budget": sum(1 for r in tried if AGENT_SKIPPED_BUDGET in r.flags),
        "new_spend_usd": round(agent.meter.spent - spent_before, 4),
        "budget_usd": agent.meter.budget,
    }
    return out, summary
