"""Step AL (2026-11-11): a second look at the "other name in another town" rows.

The first DE run (7,358 rows) left 689 Review / Conflict rows whose page names
another or a similar company in another town (Name_Check SIMILAR_NAME /
DIFFERENT_NAME, Address_Check OTHER_TOWN): mostly a parent's or sister's
legal notice, or a namesake elsewhere, found by search 1 (name + town). Each
entity is looked up again by routes that do not lean on the town, and a
number counts only when it passes a gate our code decides.

Routes, in order; the first that yields a candidate passing :func:`gate` wins:

* **B** — the customer's email domain (a company domain, not a mailbox
  provider's): ``/impressum``, else ``/imprint``, else the legal-notice link
  of the home page. The host counts as own only when its legal notice names
  the entity. No search.
* **A** — the exact legal name (only with a legal form): one search
  ``"<full legal name>" Impressum USt-IdNr``, no town
  (:func:`vat.lookup_web.exact_name_query`), then the hybrid path. When a
  host of that round is the entity's or its group's but its legal notice
  does not name it, one ``site:<host> "<full legal name>"`` search and its top
  result.
* **C** — SAP's address: one search ``<search name> <street no> <postcode>
  <city>``, unquoted; a directory result's website link is followed as a
  pointer, as in search 1.

The old page and number are not excluded: they are judged again like any
other. Per entity at most 2 searches (3 with route A's site search), 5
fetches, 2 follows, 1 probe; Peppol as in the hybrid lookup. No model
search 2, no aggregator, no agent pass, no VIES.

Outcome per entity (``usage["al"]["outcome"]``):

* ``candidate`` — a number passed the gate: the normal decision, tier and flags;
* ``nothing_passes`` — every route ran cleanly and none passed: the old
  number is withdrawn (:func:`withdrawn_result`);
* ``not_rechecked`` — a route failed technically (model call, budget, an own
  host that refused us or timed out): the old row is kept unchanged. A
  technical failure never withdraws a number.

Reader and ranking prompts, models and decision rules are the hybrid
lookup's. No SAP VAT ID or sibling number is used.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Any

from enrichment.page_corroborator import _readable_text
from vat.entity_match import (
    ADDRESS_EXACT,
    CLASS_C,
    CONFLICTING_SOURCES,
    EXTRACTED_BY_PATTERN,
    LEGAL_FORM_DIFFERS,
    NAME_CONTAINED,
    NAME_EXACT,
    PARENT_ENTITY_CANDIDATE,
    SUCCESSOR_CANDIDATE,
    TRUSTED_CLASSES,
    company_domain,
    issue_code,
)
from vat.lookup import (
    AMBIGUOUS_SAME_NAME,
    LLM_WEB,
    LOOKUP_INCOMPLETE,
    NOT_FOUND,
    NOT_RUN,
    ONLY_OTHER_COMPANY_FOUND,
    WITHDRAWN,
    FoundResult,
    LookupInput,
    reject_invalid_number,
)
from vat.lookup_web import (
    BUDGET_EXCEEDED,
    HTTP_BLOCKED,
    LLM_WEB_NO_ANSWER,
    LLM_WEB_SEARCH,
    WebSearchLookup,
    exact_name_query,
    with_not_found_reason,
)
from vat.name_prep import NamePlan, prepare
from vat.normalize import assess_format
from vat.peppol import peppol_result, search_name as peppol_name
from vat.prompts import LLM_WEB_RUN_SYSTEM_PROMPT, LLM_WEB_RUN_USER_PROMPT_TEMPLATE
from vat.web_pages import (
    CANDIDATE,
    OWN,
    PARENT,
    PARENT_CANDIDATE,
    POINTER,
    block_numbers,
    follow_targets,
    host_check,
    host_of,
    is_gate_page,
    name_on_page,
    page_key,
)

STEP = "AL"
#: Per-entity budget (the prompt's step 2).
MAX_SEARCHES = 2
MAX_SEARCHES_WITH_SITE = 3
MAX_FETCHES = 5
MAX_FOLLOWS = 2

CANDIDATE_OUTCOME, NOTHING_PASSES, NOT_RECHECKED = "candidate", "nothing_passes", "not_rechecked"
ROUTE_B, ROUTE_A, ROUTE_A_SITE, ROUTE_C, ROUTE_PEPPOL = "B", "A", "A_site", "C", "peppol"

# ── The gate ────────────────────────────────────────────────────────────────
#: Why a number did not pass (``usage["al"]["attempts"][i]["gate"]``).
NAME_NOT_EXACT = "NAME_NOT_EXACT"
LEGAL_FORM_CONTRADICTS = "LEGAL_FORM_CONTRADICTS"
OTHER_COMPANY_BLOCK = "OTHER_COMPANY_BLOCK"
SEVERAL_NUMBERS = "SEVERAL_NUMBERS"
NOT_QUOTED = "NOT_QUOTED"
DIRECTORY_NOT_AT_SAP_ADDRESS = "DIRECTORY_NOT_AT_SAP_ADDRESS"
SOURCE_NOT_TRUSTED = "SOURCE_NOT_TRUSTED"
INVALID_NUMBER = "INVALID_NUMBER"

#: A page that states the company was converted, renamed or succeeded
#: (Formwechsel, vormals …): a legal-form difference is then a review, not a
#: rejection (MAYBE_RENAMED_OR_SUCCESSOR).
_SUCCESSION_RE = re.compile(
    r"\b(?:vormals|ehemals|fr(?:ü|ue)her(?:e)?\s+(?:firma|firmierend|name)|formerly|umfirmiert\w*|umfirmierung|"
    r"formwechsel\w*|umgewandelt|umwandlung|rechtsnachfolge\w*|gesamtrechtsnachfolge\w*|hervorgegangen|"
    r"firmierte)\b", re.IGNORECASE)


def succession_stated(text: str | None) -> bool:
    return bool(_SUCCESSION_RE.search(text or ""))


def _norm(vat: str | None) -> str:
    return assess_format(vat, "DE").normalized or re.sub(r"[^0-9A-Z]", "", str(vat or "").upper())


def gate(r: FoundResult | None, plan: NamePlan, page_text: str | None = None) -> tuple[FoundResult | None, str | None]:
    """``(the candidate, None)`` when *r*'s number may replace the old one,
    else ``(None, why)``. ALL of:

    * the name EXACT or NAME_CONTAINED against the prepared SAP name;
    * the legal form not contradicting SAP's — unless the page states a
      conversion or succession: then SUCCESSOR_CANDIDATE (review);
    * the number in the legal-notice block that carries that name, read with
      a verified quote: no EXTRACTED_BY_PATTERN, no number from another
      company's block, not one of several the reader could not attribute;
    * source class A or B; a directory entry (class C) only at SAP's street
      and postcode (address EXACT; review, FROM_DIRECTORY);
    * the number passes the format check.

    The town is not checked here: it sets the decision (another town:
    review) as for every row."""
    if r is None or not r.value or r.status.startswith(LOOKUP_INCOMPLETE):
        return None, None
    if reject_invalid_number(r, "DE").value is None:
        return None, INVALID_NUMBER
    codes = {issue_code(i) for i in r.issues}
    if any(f.startswith((AMBIGUOUS_SAME_NAME, CONFLICTING_SOURCES)) for f in r.flags):
        return None, SEVERAL_NUMBERS
    if PARENT_ENTITY_CANDIDATE in codes:
        return None, OTHER_COMPANY_BLOCK
    if EXTRACTED_BY_PATTERN in codes or not (r.evidence or "").strip() \
            or any(f.split(":", 1)[0] in ("QUOTE_NOT_IN_PAGE", "ID_NOT_IN_PAGE") for f in r.flags):
        return None, NOT_QUOTED
    if page_text:
        passing, every = block_numbers(page_text, plan.acceptable)
        if len(every) >= 2 and _norm(r.value) not in {_norm(p) for p in passing}:
            return None, OTHER_COMPANY_BLOCK
    successor = False
    if LEGAL_FORM_DIFFERS in codes:
        if not succession_stated(page_text):
            return None, LEGAL_FORM_CONTRADICTS
        successor = True
    if r.name_match != NAME_EXACT and NAME_CONTAINED not in codes and not successor:
        return None, NAME_NOT_EXACT
    if r.source_class == CLASS_C:
        if r.address_match != ADDRESS_EXACT:
            return None, DIRECTORY_NOT_AT_SAP_ADDRESS
    elif r.source_class not in TRUSTED_CLASSES:
        return None, SOURCE_NOT_TRUSTED
    if successor and SUCCESSOR_CANDIDATE not in codes:
        r = replace(r, issues=(*r.issues, SUCCESSOR_CANDIDATE))
    return r, None


def withdrawn_result(old: dict[str, Any], usage: dict[str, Any] | None = None) -> FoundResult:
    """NOT_FOUND:ONLY_OTHER_COMPANY_FOUND for a row whose old number no route
    confirmed: the old number, its page name, source and address in
    ``usage["withdrawn"]`` (vat/review_columns.py writes VAT_Withdrawn_* and
    VAT_Why from it)."""
    number = old.get("VAT_Proposed")
    flags_old = set((old.get("Issue_Flags") or "").split("; "))
    withdrawn = {"number": number, "found_name": old.get("Found_Name"), "source_url": old.get("VAT_Found_Source_URL"),
                 "found_address": old.get("Found_Address"), "parent": "MAYBE_PARENT_COMPANY" in flags_old}
    return FoundResult(LLM_WEB, f"{NOT_FOUND}:{ONLY_OTHER_COMPANY_FOUND}",
                       flags=(LLM_WEB_SEARCH, f"{WITHDRAWN}:{number}"),
                       usage={**(usage or {}), "withdrawn": withdrawn})


class SecondLook(WebSearchLookup):
    """The Step AL lookup: routes B, A, C and the gate, on the hybrid
    lookup's own machinery (fetch, host check, ranking, reader)."""

    @property
    def agent_version(self) -> str:
        return f"{super().agent_version}|step_al_v1"

    async def _lookup(self, inp: LookupInput) -> FoundResult:
        if not inp.name_1:
            return FoundResult(LLM_WEB, NOT_RUN, flags=(LLM_WEB_SEARCH, "NO_NAME"))
        plan = prepare(inp.names)
        usage: dict[str, Any] = {"fetches": 0, "cost_usd": 0.0, "follows": 0, "searches": 0, "readers": 0,
                                 "pdf_fetches": 0, "archive_fetches": 0, "max_fetches": MAX_FETCHES}
        plan = await self._name2(inp, plan, usage)
        m_inp = replace(inp, names=(plan.entity, None, None, None))
        peppol = asyncio.create_task(self._peppol.search(peppol_name(m_inp.name_1)))
        try:
            chosen, al = await self._routes(m_inp, plan, usage)
        except BaseException:
            peppol.cancel()
            raise
        base = (LLM_WEB_SEARCH,)
        # Peppol, as in the hybrid lookup (one query, sent alongside), its
        # entry (class A) held to the same gate.
        self._peppol_asks += 1
        entries = await peppol
        usage.update(peppol_queries=1, peppol_entries=len(entries or []))
        pep = await peppol_result(self._peppol, None, m_inp, LLM_WEB)
        usage["peppol_hit"] = pep is not None
        if pep is not None and pep.value:
            passed, why = gate(pep, plan, None)
            al["attempts"].append({"route": ROUTE_PEPPOL, "value": pep.value, "gate": why, "name": pep.attributed_to,
                                   "name_match": pep.name_match, "address": pep.address_match})
            if passed is not None and chosen is None:
                chosen, al["route"] = replace(passed, flags=(*passed.flags, f"STEP_AL_ROUTE:{ROUTE_PEPPOL}")), \
                    ROUTE_PEPPOL
            elif passed is not None and chosen is not None and _norm(passed.value) != _norm(chosen.value):
                # Two trusted sources, two numbers: a person decides.
                ids = ",".join(sorted({_norm(chosen.value), _norm(passed.value)}))
                chosen = replace(chosen, flags=(*chosen.flags, f"{CONFLICTING_SOURCES}:{ids}"))
        usage["al"] = al
        if chosen is not None:
            al["outcome"] = CANDIDATE_OUTCOME
            result = replace(chosen, usage=usage)
        else:
            al["outcome"] = NOT_RECHECKED if al["failures"] else NOTHING_PASSES
            status = NOT_FOUND
            flags = (*base, LLM_WEB_NO_ANSWER)
            if al["failures"]:
                status = f"{LOOKUP_INCOMPLETE}:{al['failures'][0]}"
                flags = (*base, *al["failures"])
            result = FoundResult(LLM_WEB, status, flags=flags, usage=usage)
        return with_not_found_reason(self._with_plan(result, plan))

    async def _routes(self, inp: LookupInput, plan: NamePlan, usage: dict[str, Any]
                      ) -> tuple[FoundResult | None, dict[str, Any]]:
        """Routes B, A, C in order; the first candidate passing :func:`gate`."""
        pages: dict[str, dict[str, Any]] = {}
        hosts: dict[str, dict[str, Any]] = {}
        usage["pages"], usage["hosts"] = pages, hosts
        al: dict[str, Any] = {"routes": [], "attempts": [], "failures": [], "route": None}
        base = (LLM_WEB_SEARCH,)
        domain = company_domain(inp.website, inp.email)
        steps: list[tuple[str, Any]] = []
        if domain:
            steps.append((ROUTE_B, lambda: self._route_b(domain, inp, plan, usage, pages, hosts, base)))
        query_a = exact_name_query(plan)
        if query_a:
            steps.append((ROUTE_A, lambda: self._named_search(ROUTE_A, query_a, inp, usage, pages, hosts, base, plan)))
        query_c = address_query(plan, inp)
        steps.append((ROUTE_C, lambda: self._named_search(ROUTE_C, query_c, inp, usage, pages, hosts, base, plan)))
        for route, run in steps:
            if route != ROUTE_B and (usage.get("search_calls") or 0) >= MAX_SEARCHES_WITH_SITE:
                break
            al["routes"].append(route)
            before = set(hosts)
            found = await run()
            chosen = await self._judge(route, found, plan, al)
            if chosen is None and route == ROUTE_A and not al["failures"]:
                site = self._group_host(hosts, pages, before)
                if site and (usage.get("search_calls") or 0) < MAX_SEARCHES_WITH_SITE:
                    al["routes"].append(ROUTE_A_SITE)
                    found = await self._site_name_search(site, inp, plan, usage, pages, hosts, base)
                    chosen = await self._judge(ROUTE_A_SITE, found, plan, al)
            self._blocked(hosts, before, al)
            if chosen is not None:
                al["route"] = route if al["routes"][-1] != ROUTE_A_SITE else ROUTE_A_SITE
                return chosen, al
            if al["failures"] and any(f.startswith((BUDGET_EXCEEDED, "LLM_UNAVAILABLE")) for f in al["failures"]):
                break  # no model call will work now
        return None, al

    async def _judge(self, route: str, found: FoundResult | None, plan: NamePlan,
                     al: dict[str, Any]) -> FoundResult | None:
        if isinstance(found, FoundResult) and found.status.startswith(LOOKUP_INCOMPLETE):
            why = found.status.partition(":")[2] or next(
                (f for f in found.flags if f not in (LLM_WEB_SEARCH,)), LOOKUP_INCOMPLETE)
            al["failures"].append(why)
            al["attempts"].append({"route": route, "value": None, "gate": f"FAILED:{why}"})
            return None
        if found is None or not found.value:
            al["attempts"].append({"route": route, "value": None, "gate": "NO_NUMBER"})
            return None
        text = await self._page_text(found.source_url)
        passed, why = gate(found, plan, text)
        al["attempts"].append({"route": route, "value": found.value, "gate": why, "name": found.attributed_to,
                               "name_match": found.name_match, "address": found.address_match,
                               "source": found.source_url, "class": found.source_class})
        return replace(passed, flags=(*passed.flags, f"STEP_AL_ROUTE:{route}")) if passed is not None else None

    async def _page_text(self, url: str | None) -> str | None:
        """The source page's text as fetched (from the page cache)."""
        if not url:
            return None
        page = await self._pages._fetch(url, set())
        return _readable_text(page.content) if page is not None and page.ok and page.content else None

    @staticmethod
    def _blocked(hosts: dict[str, dict[str, Any]], before: set[str], al: dict[str, Any]) -> None:
        """An own host of this route refused us or timed out: a technical
        failure (we cannot tell whether it states the entity's number)."""
        for host, h in hosts.items():
            if host not in before and h.get("blocked") and h.get("kind") in (OWN, POINTER) \
                    and not h.get("al_counted"):
                h["al_counted"] = True
                al["failures"].append(f"{HTTP_BLOCKED}_{h['blocked']}")

    @staticmethod
    def _group_host(hosts: dict[str, dict[str, Any]], pages: dict[str, dict[str, Any]],
                    before: set[str]) -> str | None:
        """Route A's own or group host whose legal notice does not name the
        entity: rejected NAME_NOT_ON_PAGE, or a notice that names another
        company (a group's page: :data:`PARENT_CANDIDATE`)."""
        for host, h in hosts.items():
            if host in before or h.get("blocked") or h.get("kind") not in (OWN, POINTER, PARENT):
                continue
            parent_notice = any((pages.get(u) or {}).get("name_on_page") == PARENT_CANDIDATE
                                for u in h.get("fetched") or [])
            if h.get("rejected") == "NAME_NOT_ON_PAGE" or parent_notice:
                return host
        return None

    async def _site_name_search(
        self, host: str, inp: LookupInput, plan: NamePlan, usage: dict[str, Any],
        pages: dict[str, dict[str, Any]], hosts: dict[str, dict[str, Any]], base: tuple[str, ...],
    ) -> FoundResult | None:
        """``site:<host> "<full legal name>"``: its top result on that host,
        read like an own page (one fetch, within the entity's budget)."""
        q = f'site:{host} "{plan.entity}"'
        out = await self._call(f"search_{ROUTE_A_SITE}", LLM_WEB_RUN_SYSTEM_PROMPT,
                               LLM_WEB_RUN_USER_PROMPT_TEMPLATE.format(query=q), self._run_search, usage)
        if isinstance(out, FoundResult):
            return out
        usage["searches"] = usage.get("search_calls") or 0
        results = self._results(out[1], out[0])
        done = {page_key(u) for h in hosts.values() for u in h.get("fetched") or []}
        urls = [r["url"] for r in results if (host_of(r["url"]) == host or host_of(r["url"]).endswith("." + host))
                and page_key(r["url"]) not in done]
        usage[f"query_{ROUTE_A_SITE}"] = q
        usage[f"results_{ROUTE_A_SITE}"] = len(results)
        if not urls:
            return None
        old = hosts.pop(host)  # the notice did not name it; this page may
        try:
            read = await self._fetch_hosts(ROUTE_A_SITE, [(urls[0], OWN)], inp, usage, pages, hosts, urls[:1],
                                           plan=plan)
        finally:
            new = hosts.get(host) or {}
            hosts[host] = {**old, **new, "fetched": [*(old.get("fetched") or []), *(new.get("fetched") or [])]}
        return await self._read(ROUTE_A_SITE, read, inp, usage, base, plan) if read else None

    async def _route_b(
        self, domain: str, inp: LookupInput, plan: NamePlan, usage: dict[str, Any],
        pages: dict[str, dict[str, Any]], hosts: dict[str, dict[str, Any]], base: tuple[str, ...],
    ) -> FoundResult | None:
        """The customer's email domain: ``/impressum``, else ``/imprint``,
        else the legal-notice link of the home page. Own only when its legal
        notice names the entity; then the reader on that page."""
        root = f"https://{'www.' if domain.count('.') == 1 else ''}{domain}"
        host = host_of(root)
        entry = hosts.setdefault(host, {"kind": CANDIDATE, "round": ROUTE_B, "fetched": [], "rejected": None,
                                        "answered": False, "email_domain": domain})
        legal = None
        for path in ("/impressum", "/imprint", "/"):
            if usage["fetches"] >= MAX_FETCHES:
                break
            url = root + path
            usage["fetches"] += 1
            entry["fetched"].append(url)
            got = await self._get(url, ROUTE_B, "email", CANDIDATE, inp, usage, pages)
            if got is None:
                if (pages.get(url) or {}).get("blocked"):
                    entry["blocked"] = pages[url]["blocked"]
                    entry["kind"] = OWN  # its own email domain refused us: a failure, not an answer
                    return None
                continue
            if path == "/":
                targets = [t for t in follow_targets(got["url"], got["links"], [], {page_key(got["url"])})
                           if is_gate_page(t)]
                if not targets or usage["follows"] >= MAX_FOLLOWS:
                    break
                usage["follows"] += 1
                entry["fetched"].append(targets[0])
                got = await self._get(targets[0], ROUTE_B, "follow", CANDIDATE, inp, usage, pages)
                if got is None:
                    if (pages.get(targets[0]) or {}).get("blocked"):
                        entry.update(blocked=pages[targets[0]]["blocked"], kind=OWN)
                        return None
                    break
            legal = got
            break
        if legal is None:
            entry["rejected"] = "NO_LEGAL_NOTICE"
            return None
        reason, named = host_check(legal["text"], legal["url"], inp, CANDIDATE, plan=plan)
        pages[legal["url"]]["name_on_page"] = legal["name_on_page"] = named
        entry["answered"] = True
        if reason or named in ("ABSENT", "NOT_CHECKED") or (
                named == PARENT_CANDIDATE and not name_on_page(legal["text"], plan.acceptable, strict=True)):
            entry["rejected"] = reason or "NAME_NOT_ON_PAGE"
            pages[legal["url"]]["rejected"] = entry["rejected"]
            return None
        entry["kind"] = legal["kind"] = pages[legal["url"]]["kind"] = OWN
        return await self._read(ROUTE_B, [legal], inp, usage, base, plan)


def address_query(plan: NamePlan, inp: LookupInput) -> str:
    """Route C: ``<search name> <street with number> <postcode> <city>``, unquoted."""
    street = " ".join(p for p in (inp.street, inp.house_number) if p)
    parts = [plan.search_name.replace('"', ""), street, inp.postal_code or "", inp.city or ""]
    return " ".join(" ".join(p.split()) for p in parts if p and p.strip())


# ── Rows: the input check, the test sample, each row's outcome ──────────────

#: The group's expected size and values (the prompt's step 1).
EXPECTED_ROWS = 689
EXPECTED_DECISIONS = {"Review": 301, "Conflict": 388}
GROUP_NAMES = frozenset({"SIMILAR_NAME", "DIFFERENT_NAME"})
GROUP_ADDRESS = "OTHER_TOWN"

#: The columns this step writes on a row besides the reviewer's.
AL_COLUMNS = ["AL_Outcome", "AL_Route", "AL_Attempts", "AL_Not_Rechecked_Reason", "AL_Cost_USD"]


def check_input(rows: list[dict[str, Any]]) -> list[str]:
    """What is wrong with the input file; empty when it is the 689-row group."""
    from collections import Counter

    problems = []
    if len(rows) != EXPECTED_ROWS:
        problems.append(f"{len(rows)} rows, expected {EXPECTED_ROWS}")
    decisions = Counter(r.get("VAT_Decision") for r in rows)
    if dict(decisions) != EXPECTED_DECISIONS:
        problems.append(f"decisions {dict(decisions)}, expected {EXPECTED_DECISIONS}")
    bad_name = sum(1 for r in rows if r.get("Name_Check") not in GROUP_NAMES)
    bad_town = sum(1 for r in rows if r.get("Address_Check") != GROUP_ADDRESS)
    if bad_name:
        problems.append(f"{bad_name} rows with Name_Check not SIMILAR_NAME / DIFFERENT_NAME")
    if bad_town:
        problems.append(f"{bad_town} rows with Address_Check not OTHER_TOWN")
    keys = Counter((r.get("Customer"), r.get("Source sheet")) for r in rows)
    dup = [k for k, n in keys.items() if n > 1]
    if dup:
        problems.append(f"{len(dup)} Customer + Source sheet pairs occur more than once")
    return problems


def _flags_of(row: dict[str, Any]) -> set[str]:
    return set((row.get("Issue_Flags") or "").split("; ")) - {""}


def stratum_of(row: dict[str, Any], has_form: bool) -> str:
    """The test sample's stratum of a row (the prompt's 7b)."""
    if row.get("Check_Email_Domain") in ("Matches source site", "Differs from source site"):
        return "email_match" if row["Check_Email_Domain"] == "Matches source site" else "email_other"
    if has_form and _flags_of(row) & {"MAYBE_PARENT_COMPANY", "LEGAL_FORM_DIFFERS"}:
        return "form_parent_or_form"
    return "form_plain" if has_form else "no_form"


#: Test sample: stratum → rows (40 in all; email rows 5, at least 2 matching).
TEST_SAMPLE = {"form_parent_or_form": 15, "form_plain": 10, "no_form": 10, "email_match": 2, "email_other": 3}


def pick_test_rows(rows: list[dict[str, Any]], keys: list[str | None], forms: list[bool],
                   seed: int = 20261111) -> list[int]:
    """Row indexes of the 40-row test sample: per stratum, rows of distinct
    entities (*keys*), drawn with a fixed seed. An email-domain row is drawn
    for its email stratum, whatever its form."""
    import random

    rng = random.Random(seed)
    by: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        by.setdefault(stratum_of(row, forms[i]), []).append(i)
    picked: list[int] = []
    seen: set[Any] = set()
    for stratum, n in TEST_SAMPLE.items():
        pool = sorted(by.get(stratum, []))
        rng.shuffle(pool)
        got = 0
        for i in pool:
            if got >= n:
                break
            if keys[i] in seen:
                continue
            seen.add(keys[i])
            picked.append(i)
            got += 1
    return sorted(picked)


def row_outcome(result: FoundResult | None, old: dict[str, Any], sap_name: str | None, postal_code: str | None,
                city: str | None) -> dict[str, Any]:
    """The reviewer's columns and :data:`AL_COLUMNS` of one row after the
    second look: the new candidate's; the old number withdrawn; or the old
    row unchanged ("not re-checked")."""
    from vat.review_columns import REVIEW_COLUMNS, review_columns

    al = (result.usage or {}).get("al") or {} if result is not None else {}
    outcome = al.get("outcome") or NOT_RECHECKED
    if result is None:
        outcome = NOT_RECHECKED
    if outcome == CANDIDATE_OUTCOME:
        cols = review_columns(result, sap_name, postal_code, city)
    elif outcome == NOTHING_PASSES:
        cols = review_columns(withdrawn_result(old, result.usage), sap_name, postal_code, city)
    else:
        cols = {c: old.get(c) for c in REVIEW_COLUMNS}
    attempts = "; ".join(
        f"{a['route']}:{a.get('value') or '-'}:{a.get('gate') or 'PASS'}" for a in al.get("attempts") or [])
    confirmed = outcome == CANDIDATE_OUTCOME and _norm(cols.get("VAT_Proposed")) == _norm(old.get("VAT_Proposed"))
    costs = (result.usage or {}).get("costs") or {} if result is not None else {}
    return {**cols,
            "AL_Outcome": ("old number confirmed" if confirmed else "new number" if outcome == CANDIDATE_OUTCOME
                           else "withdrawn" if outcome == NOTHING_PASSES else "not re-checked"),
            "AL_Route": al.get("route"),
            "AL_Attempts": attempts or None,
            "AL_Not_Rechecked_Reason": ", ".join(al.get("failures") or []) or (
                None if outcome != NOT_RECHECKED else "no result"),
            "AL_Cost_USD": round(float(costs.get("Total_USD") or 0.0), 5) if costs else None}
