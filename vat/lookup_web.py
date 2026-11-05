"""LLM_WEB missing-VAT lookup: the model searches the web — never from memory.

For one in-scope customer (see ``vat/lookup.py``):

1. **Search.** The model (``llm_model()``; gpt-6-astra / gpt-6-sol) is given
   the SAP name and address and the Responses API ``web_search`` tool, at
   reasoning effort :data:`REASONING_EFFORT` — the way ChatGPT and Claude
   answer the same question. It returns the USt-IdNr. it read, the page it
   read it on (``source_url``), a verbatim quote and the name the page
   attributes it to — or null (``vat/prompts.py``). Alongside the answer the
   API reports every URL the search visited.
2. **Proof, not trust.** The answer is used only as a pointer to a page:

   * no ``source_url`` → the number is dropped (``ANSWER_WITHOUT_SOURCE``):
     ``NOT_FOUND``, no value;
   * the code fetches ``source_url`` itself (``vat_pages``) and runs the
     grounded mode's gates a–f, VIES and tier on it
     (:meth:`vat.lookup_grounded.GroundedLookup.judge_page`): the quote and
     the digits must be on the page *as fetched here*, the check digit must
     hold, the page's name must match, the address is checked;
   * the ID's digits are not on that page → the number is dropped
     (``NOT_FOUND`` with ``NOT_ON_SOURCE_PAGE`` and the failed gate): it
     came from somewhere other than the page;
   * ``source_url`` not among the visited URLs → ``SOURCE_NOT_VISITED`` and
     the tier is ``REVIEW``. Recorded, not blocking: the page our own code
     fetched is the evidence;
   * the page cannot be fetched here (403, 5xx, timeout, 404): a visited
     source keeps its number as ``FOUND_UNVERIFIED`` with
     ``SOURCE_UNREADABLE`` and issue ``EVIDENCE_NOT_VERIFIED``; an unvisited
     one is dropped (``NOT_FOUND`` with ``SOURCE_UNREADABLE``) — nothing shows
     the number was on any page;
   * a marketplace or social network (``REJECTED_SOURCE``), or an archive
     snapshot for a record that is not deleted (``ARCHIVE_NOT_USED``) → the
     number is dropped.
3. **Peppol Directory** (``vat/peppol.py``). When the web result is not
   ``AUTO_ACCEPT``, the entity is looked up in the Peppol Directory (class A).
   An accepted Peppol entry replaces the web result; one with another number
   than an exact-entity web result is a conflict; with no web candidate at
   all, any Peppol candidate is used.

Statuses and tiers are the grounded mode's, so both modes are scored the same
way. Every row carries ``LLM_WEB_SEARCH`` and ``SEARCH_VISITED:<n>``.

The search and the answer are cached in ``vat_llm`` (``VatLLM.search_json``):
``CACHE_FROZEN`` replays them; a live run asks again and, unlike a chat
completion at temperature 0, may get a different answer.
"""

from __future__ import annotations

import re
import time

import httpx
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from config import llm_model
from llm.openai_client import LLMUnavailableFrozen
from vat.entity_match import (
    CONFLICTING_SOURCES,
    EVIDENCE_NOT_VERIFIED,
    NAME_EXACT,
    NAME_MISMATCH,
    TRUSTED_CLASSES,
    company_domain,
    domain_match,
    is_procurement_host,
    parse_name,
    source_type,
    vat_candidates,
)
from vat.llm_cache import VatLLM
from vat.lookup import (
    AGGREGATOR_FALLBACK,
    AMBIGUOUS_SAME_NAME,
    AUTO_ACCEPT,
    FOUND_UNVERIFIED,
    GROUNDED,
    LLM_WEB,
    LOOKUP_INCOMPLETE,
    NOT_FOUND,
    NO_VAT_INFERRED,
    NO_VAT_STATED,
    NOT_RUN,
    SOURCE_NOT_VISITED,
    TIER_REVIEW,
    FoundResult,
    LookupInput,
    today,
)
from enrichment.page_corroborator import _readable_text
from vat.entity_match import domain_resembles, entity_name_on_page, has_address, is_third_party_host, registrable_domain
from vat.lookup_grounded import (
    _VAT_KEYWORD_RE as _VAT_WORD_RE,
    FROZEN_CACHE_MISS,
    LLM_UNAVAILABLE,
    GroundedLookup,
    Reading,
    deterministic_reading,
    id_on_page,
    is_pdf_url,
    section_first_text,
    unusable_source,
    _ws,
)
from vat.peppol import PeppolDirectory, peppol_result, search_name as peppol_name
from vat.costs import LINES, NEW_SPEND, TOTAL, entity_costs, run_costs, run_stamp
from vat.llm_cache import CostMeter, MalformedOutput, SearchLimits
from vat.normalize import assess_format
from vat.prompts import (
    LLM_WEB_ANSWER_SCHEMA,
    LLM_WEB_HYBRID_PROMPT_VERSION,
    LLM_WEB_PROMPT_VERSION,
    LLM_WEB_READ_SCHEMA,
    LLM_WEB_READ_SYSTEM_PROMPT,
    LLM_WEB_READ_USER_PROMPT_TEMPLATE,
    LLM_WEB_RUN_SCHEMA,
    LLM_WEB_RUN_SYSTEM_PROMPT,
    LLM_WEB_RUN_USER_PROMPT_TEMPLATE,
    LLM_WEB_SECOND_SCHEMA,
    LLM_WEB_SECOND_SYSTEM_PROMPT,
    LLM_WEB_SECOND_USER_PROMPT_TEMPLATE,
    LLM_WEB_SYSTEM_PROMPT,
    LLM_WEB_ZERO_RESULTS_RULE,
    LLM_WEB_USER_PROMPT_TEMPLATE,
)
from vat.prompts import LLM_WEB_RANK_SCHEMA, LLM_WEB_RANK_SYSTEM_PROMPT, LLM_WEB_RANK_USER_PROMPT_TEMPLATE
from vat.vies_client import NOT_CHECKED, VIESClient
from vat import name2_classifier
from vat.name_prep import GENERIC_NAME, NamePlan, distinctive_tokens, prepare
from vat.web_pages import (
    CANDIDATE,
    INSTITUTION,
    OWN,
    PARENT,
    PARENT_CANDIDATE,
    POINTER,
    UNVERIFIED,
    VERIFY,
    block_numbers,
    follow_targets,
    form_difference,
    heuristic_rank,
    host_check,
    host_kind,
    host_of,
    impressum_probes,
    initials_match,
    institutional_kind,
    is_empty_legal_page,
    is_gate_page,
    is_legal_page,
    is_university,
    name_on_page,
    never_fetch,
    page_key,
    select_pages,
    with_scheme,
)
from vat.web_query import no_vat_statement, page_sections, query_for

#: Reasoning effort of the search call when settings carry none
#: (``VAT_WEB_REASONING_EFFORT``, default medium).
REASONING_EFFORT = "medium"
#: The model's output was not a JSON object (never retried).
MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
#: The run's cost limit (VAT_WEB_BUDGET_USD) was reached: no call made.
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
#: Every search allowed (VAT_WEB_MAX_SEARCHES) was made. Page opens are not searches.
SEARCH_LIMIT_REACHED = "SEARCH_LIMIT_REACHED"
#: "model" mode: the model searched more often than the prompt allows (the
#: API caps searches + opens together, not searches alone).
SEARCH_LIMIT_EXCEEDED = "SEARCH_LIMIT_EXCEEDED"
#: "model" mode: the model opened every page it was allowed (VAT_WEB_MAX_OPENS).
OPEN_LIMIT_REACHED = "OPEN_LIMIT_REACHED"
#: The answer was cut off at VAT_WEB_MAX_OUTPUT_TOKENS.
OUTPUT_LIMIT_REACHED = "OUTPUT_LIMIT_REACHED"
#: The per-entity page-fetch limit (VAT_WEB_MAX_FETCHES) was reached.
FETCH_LIMIT_REACHED = "FETCH_LIMIT_REACHED"
#: The number was read by our code on an own-domain page from the search
#: results, after the model gave none (no model call, no extra search).
CODE_FETCH = "CODE_FETCH"

_IMPRINT_PATH_RE = re.compile(r"impressum|imprint|legal|rechtlich|anbieterkennzeichnung", re.IGNORECASE)
_PROCUREMENT_PATH_RE = re.compile(
    r"einkauf|beschaffung|lieferant|supplier|procurement|rechnung|invoice|agb|vergabe", re.IGNORECASE)
#: Paths tried on the entity's own host when the results hold no imprint URL.
PROBE_PATHS = ("/impressum", "/imprint")

# ── "hybrid" mode (VAT_WEB_QUERY_MODE=hybrid) ────────────────────────────────
HYBRID = "hybrid"
#: Prefix: which search's results held the page (``FOUND_BY_SEARCH:1`` / ``:2``).
FOUND_BY_SEARCH = "FOUND_BY_SEARCH"
#: Prefix: the engine that found the number (``ENGINE:llm_web`` / ``:grounded``
#: / ``:peppol``).
ENGINE = "ENGINE"
#: The model ran another query than the one search 1 gave it.
QUERY_NOT_RUN_AS_GIVEN = "QUERY_NOT_RUN_AS_GIVEN"
#: The number was read from a PDF / an archive snapshot.
PDF_SOURCE = "PDF_SOURCE"
ARCHIVE_SOURCE = "ARCHIVE_SOURCE"
#: An own host refused its pages (403 / 429 / 5xx) and nothing was found:
#: status ``LOOKUP_INCOMPLETE:HTTP_<code>``, not NOT_FOUND.
HTTP_BLOCKED = "HTTP"
#: Azure's content filter stopped a call: ``LOOKUP_INCOMPLETE:CONTENT_FILTER``.
CONTENT_FILTER = "CONTENT_FILTER"
#: The page names the entity by its initials ("HP" for Hewlett-Packard).
NAME_ABBREVIATED = "NAME_ABBREVIATED"
#: The reader could not tell which of a page's numbers is the entity's.
AMBIGUOUS_ON_PAGE = "AMBIGUOUS_ON_PAGE"
#: An own-host page under 500 characters after stripping: rendered by script
#: (no renderer yet; counted).
PAGE_NEEDS_JS = "PAGE_NEEDS_JS"
NEEDS_JS_CHARS = 500
#: The reader named another number than the block that names the entity.
BLOCK_SELECTED = "BLOCK_SELECTED"
#: Why NOT_FOUND_NO_VAT_EXPECTED: the page says so / a school or authority.
NO_VAT_STATEMENT = "NO_VAT_STATEMENT"
PUBLIC_BODY = "PUBLIC_BODY"
WAYBACK_API = "https://archive.org/wayback/available"
# ── Step AF ─────────────────────────────────────────────────────────────────
#: VAT_WEB_RANKING: the helper model ranks the results (8b), or rule 8 alone.
MODEL_RANKING, HEURISTIC_RANKING = "model", "heuristic"
RANK_MAX_OUTPUT_TOKENS = 250
#: The lowest effort GPT-5.4 mini takes ("minimal" is refused).
HELPER_EFFORT = "none"
#: Flags: how the pages were ordered; the ranking call failed.
RANKED_BY_MODEL = "RANKED_BY_MODEL"
RANKING_FALLBACK = "RANKING_FALLBACK"
#: Search 2's domain_hint led to the page the number was read on.
DOMAIN_HINT = "DOMAIN_HINT"
#: The number was read on a directory entry (vat.lookup.AGGREGATOR_FALLBACK).
AGGREGATOR_SITE = "unternehmensverzeichnis.org"
#: The directory entry carries another name the record's points to.
SUCCESSOR_CANDIDATE = "SUCCESSOR_CANDIDATE"
#: Every own host refused us.
ALL_OWN_HOSTS_BLOCKED = "ALL_OWN_HOSTS_BLOCKED"
#: Usage fields summed over an entity's calls.
_SUM_KEYS = ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens",
             "reasoning_tokens", "tool_calls", "search_calls", "other_tool_calls")


def umbrella_entities(path: str | None = None) -> list[dict[str, Any]]:
    """vat/umbrella_entities.yaml: ``[{"name": …, "aliases": […]}]`` (a
    minimal reader for its one shape; no YAML dependency)."""
    import os

    path = path or os.path.join(os.path.dirname(__file__), "umbrella_entities.yaml")
    out: list[dict[str, Any]] = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].rstrip()
        if line.startswith("- name:"):
            out.append({"name": line.split(":", 1)[1].strip(), "aliases": []})
        elif line.strip().startswith("aliases:") and out:
            inner = line.split(":", 1)[1].strip().strip("[]")
            out[-1]["aliases"] = [a.strip().strip("'\"") for a in inner.split(",") if a.strip()]
    return out


_UMBRELLAS = umbrella_entities()


def umbrella_of(attributed: str | None, plan: NamePlan) -> dict[str, Any] | None:
    """The umbrella the number belongs to, when the record is one of its
    institutes: the page attributes the number to the umbrella and the
    record's name carries one of its aliases (Fraunhofer, Max-Planck)."""
    if not attributed:
        return None
    from vat.entity_match import fold as _fold

    record = _fold(" ".join(n for n in (plan.entity, *plan.names) if n))
    for u in _UMBRELLAS:
        keys = [u["name"], *u["aliases"]]
        if any(_fold(k) and _fold(k) in _fold(attributed) for k in keys) and \
                any(_fold(a) in record for a in u["aliases"]):
            return u
    return None


def _answered(pages: dict[str, dict[str, Any]], entry: dict[str, Any]) -> bool:
    """A page of this host came back with an HTTP answer that is not a
    refusal or a server error (a stale link's 404: the host is up)."""
    for url in entry.get("fetched") or []:
        status = (pages.get(url) or {}).get("status")
        if isinstance(status, int) and status < 500 and status not in (401, 403, 429):
            return True
    return False


def _blocked_code(page: Any, flags: set[str]) -> int | None:
    """403 / 429 / 5xx: the host refused us (a 404 is an answer)."""
    codes = [page.status] if page is not None and page.status else []
    codes += [int(f.split(":")[1]) for f in flags if f.startswith("PAGE_UNAVAILABLE:") and f.split(":")[1].isdigit()]
    code = next((c for c in codes if c in (401, 403, 429) or 500 <= c < 600), None)
    if code is None and any(f.startswith("PAGE_UNAVAILABLE:") and "Timeout" in f for f in flags):
        return "TIMEOUT"  # bmwgroup.com: a bot-throttled host times out instead of a 403
    return code


def own_domain_pages(visited: list[str], inp: LookupInput, exclude: tuple[str, ...] = ()) -> list[str]:
    """Pages on the entity's own domain worth reading, best first: imprint
    URLs from the search results, then procurement / supplier pages, then
    ``/impressum`` and ``/imprint`` on its own host when the results held no
    imprint URL from it. Third-party hosts are never in the list."""
    core = parse_name(inp.name_1, sap=True).core
    own_domain = company_domain(inp.website, inp.email)
    seen = {_url_key(u) for u in exclude}
    own: list[str] = []
    for url in visited:
        if not url or is_third_party_host(url) or _url_key(url) in seen:
            continue
        if domain_match(url, own_domain) or domain_resembles(url, core):
            own.append(url)
            seen.add(_url_key(url))
    imprint = [u for u in own if _IMPRINT_PATH_RE.search(urlparse(u).path)]
    procurement = [u for u in own if u not in imprint and _PROCUREMENT_PATH_RE.search(urlparse(u).path)]
    probes: list[str] = []
    for url in own:
        p = urlparse(url)
        if any(urlparse(i).hostname == p.hostname for i in imprint):
            continue
        for path in PROBE_PATHS:
            probe = f"{p.scheme or 'https'}://{p.hostname}{path}"
            if _url_key(probe) not in seen:
                seen.add(_url_key(probe))
                probes.append(probe)
    return imprint + procurement + probes

# ── Flags (VAT_Found_Flags) ──────────────────────────────────────────────────
#: Every row of this mode.
LLM_WEB_SEARCH = "LLM_WEB_SEARCH"
#: Prefix: how many URLs the search visited.
SEARCH_VISITED = "SEARCH_VISITED"
#: The model found no number on any page.
LLM_WEB_NO_ANSWER = "LLM_WEB_NO_ANSWER"
#: The model gave a number but no page for it — dropped.
ANSWER_WITHOUT_SOURCE = "ANSWER_WITHOUT_SOURCE"
#: source_url could not be fetched here, so the number could not be checked.
SOURCE_UNREADABLE = "SOURCE_UNREADABLE"
#: The number's digits are not on source_url as fetched here — dropped.
NOT_ON_SOURCE_PAGE = "NOT_ON_SOURCE_PAGE"


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text.lower() not in ("null", "none", "unknown") else None


def _url_key(url: str) -> tuple[str, str]:
    """Host without ``www.`` and path without a trailing slash: the same page
    reached as http / https, with or without www, query or fragment."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    return host, (p.path or "/").rstrip("/") or "/"


def was_visited(url: str, visited: list[str]) -> bool:
    key = _url_key(url)
    return any(_url_key(v) == key for v in visited)


class WebSearchLookup:
    """LLM_WEB. Clients are injectable; defaults are the repo's own."""

    mode = LLM_WEB

    def __init__(
        self, settings: Any, vies: VIESClient, *, llm: VatLLM | None = None, fetcher: Any = None,
        peppol: PeppolDirectory | None = None, grounded: GroundedLookup | None = None,
    ) -> None:
        self._vies = vies
        #: Step AF-2: VIES is asked by the queue, not here (VAT_VIES_IN_LOOKUP).
        self.vies_in_lookup = bool(getattr(settings, "vat_vies_in_lookup", False))
        self._llm = llm or VatLLM(vies.evidence_cache.namespace("vat_llm"))
        self._llm.write_failures = vies.write_failures
        # The grounded mode's page fetch, gates, VIES and tiers, so a number
        # found this way is held to exactly the same standard. It never
        # searches: only judge_page is used.
        self._pages = GroundedLookup(settings, vies, search_client=False, fetcher=fetcher,
                                     llm=self._llm)
        # Off under MOCK_EXTERNAL_CALLS unless a test injects its own.
        self._peppol = peppol or PeppolDirectory(
            vies.evidence_cache, write_failures=vies.write_failures,
            enabled=bool(getattr(settings, "vat_peppol_enabled", True))
            and not getattr(settings, "mock_external_calls", False),
        )
        # Hard cost limits (config.Settings.vat_web_*).
        g = lambda name, default: getattr(settings, name, default)  # noqa: E731
        self.effort = g("vat_web_reasoning_effort", REASONING_EFFORT) or REASONING_EFFORT
        self.query_mode = (g("vat_web_query_mode", HYBRID) or HYBRID).lower()
        self.limits = SearchLimits(
            max_searches=int(g("vat_web_max_searches", 2)),
            max_opens=int(g("vat_web_max_opens", 3)),
            max_output_tokens=int(g("vat_web_max_output_tokens", 1500)),
            schema=LLM_WEB_ANSWER_SCHEMA,
            retry_backoff=float(g("vat_web_retry_backoff_seconds", 10.0)),
        )
        self.max_fetches = int(g("vat_web_max_fetches", 3))
        #: Step AF-2: the fetch budget of an entity with many own hosts.
        self.max_fetches_many_hosts = int(g("vat_web_max_fetches_many_hosts", 6))
        self.many_own_hosts = int(g("vat_web_many_own_hosts", 3))
        self.max_follows = int(g("vat_web_max_follows", 2))
        self.page_chars = int(g("vat_web_page_chars", 8000))
        self.section_window = int(g("vat_web_section_window", 1500))
        self.sections_chars = int(g("vat_web_sections_chars", 12000))
        # "hybrid": each search call may make exactly one search and open
        # nothing; the reader has no tools. Our code does every fetch.
        self._run_search = replace(self.limits, max_searches=1, max_opens=0, schema=LLM_WEB_RUN_SCHEMA)
        self._own_search = replace(self.limits, max_searches=1, max_opens=0, schema=LLM_WEB_SECOND_SCHEMA)
        self._reader = replace(self.limits, max_searches=0, max_opens=0, schema=LLM_WEB_READ_SCHEMA,
                               cache_key="vat_llm_web_read")
        # Step AF helper calls on the small model (no tools, reasoning "none"):
        # the ranking call (8b) and the Name-2 classifier (10b).
        self.helper_model = g("vat_helper_model", "") or None
        self.ranking = (g("vat_web_ranking", MODEL_RANKING) or MODEL_RANKING).lower()
        self.name2_enabled = bool(g("vat_name2_classifier", True)) and bool(self.helper_model)
        self.aggregator_enabled = bool(g("vat_aggregator_fallback", True))
        self._rank_limits = SearchLimits(max_searches=0, max_opens=0, max_output_tokens=RANK_MAX_OUTPUT_TOKENS,
                                         schema=LLM_WEB_RANK_SCHEMA, cache_key="vat_rank",
                                         retry_backoff=self.limits.retry_backoff, model=self.helper_model)
        self._name2_limits = name2_classifier.limits(self.helper_model or "", self.limits.retry_backoff)
        # The SerpAPI engine, run once with search 1 for an entity still
        # NOT_FOUND (only when VAT_GROUNDED_FALLBACK is set: lookup_engines).
        self._grounded = grounded
        self.meter = CostMeter.from_settings(settings)
        #: One entry per entity looked up: who, what was found, and its usage.
        self.usage_log: list[dict[str, Any]] = []
        self._asks = 0
        self._peppol_asks = 0

    @property
    def agent_version(self) -> str:
        if self.query_mode == HYBRID:
            return f"{LLM_WEB}|{LLM_WEB_HYBRID_PROMPT_VERSION}|{llm_model()}"
        return f"{LLM_WEB}|{LLM_WEB_PROMPT_VERSION}|{llm_model()}"

    def stats(self) -> dict[str, int]:
        pages = self._pages.stats()
        return {
            "llm_web_searches": self._asks,
            "llm_network_calls": self._llm.network_calls,
            "llm_cache_hits": self._llm.cache_hits,
            "page_attempts": pages["page_attempts"],
            "pages_fetched": pages["pages_fetched"],
            "page_cache_hits": pages["page_cache_hits"],
            "vies_checks": pages["vies_checks"],
            "peppol_searches": self._peppol_asks,
            "peppol_network_calls": self._peppol.network_calls,
            "peppol_cache_hits": self._peppol.cache_hits,
            "cost_usd": round(self.meter.spent, 4),
            "cache_write_failures": self._vies.write_failures.count,
            **self.cost_lines(),
        }

    def cost_lines(self) -> dict[str, Any]:
        """The run's cost lines over every entity so far (``vat/costs.py``),
        flat: one Summary-sheet row each."""
        run = run_costs(self.usage_log, self.meter.table, self.meter.overrides)
        return {
            **{k: run[k] for k in LINES},
            **{f"Tokens_{kind}_USD": usd for kind, usd in run["tokens_by_kind"].items()},
            **{f"Tokens_{dep}_USD": usd for dep, usd in run["tokens_by_deployment"].items()},
            "api_calls": run["calls"], "api_cache_hits": run["cache_hits"],
            "web_search_calls": run["web_search_calls"], "page_open_calls": run["page_open_calls"],
            "serpapi_queries": run["serpapi_queries"], **run_stamp(run),
        }

    async def aclose(self) -> None:
        await self._llm.aclose()

    @staticmethod
    def prompt(inp: LookupInput, max_searches: int = 2, max_opens: int = 3) -> str:
        names = list(inp.names) + [None] * (4 - len(inp.names))
        street = " ".join(p for p in (inp.street, inp.house_number) if p)
        return LLM_WEB_USER_PROMPT_TEMPLATE.format(
            max_searches=max_searches, max_opens=max_opens,
            name_1=names[0] or "", name_2=names[1] or "", name_3=names[2] or "",
            name_4=names[3] or "", street=street, postal_code=inp.postal_code or "",
            city=inp.city or "", country=inp.country or "",
            domain=company_domain(inp.website, inp.email) or "",
            register_number=inp.register_number or "",
            record_status=("deleted in our system (an archived snapshot of its "
                           "website may be used)" if inp.deleted else "active"),
        )

    async def lookup(self, inp: LookupInput) -> FoundResult:
        started = time.perf_counter()
        result = await self._lookup(inp)
        # The entity's cost lines, written to the workbook and the usage JSON.
        result = replace(result, usage={**(result.usage or {}),
                                        "costs": entity_costs(result.usage or {}, self.meter.table,
                                                              self.meter.overrides)})
        self.usage_log.append({
            "customer": inp.customer, "name_1": inp.name_1, "city": inp.city,
            "status": result.status, "value": result.value, "decision": result.decision,
            "issues": list(result.all_issues), "flags": list(result.flags),
            "seconds": round(time.perf_counter() - started, 2), **(result.usage or {}),
        })
        return result

    async def _lookup(self, inp: LookupInput) -> FoundResult:
        if not inp.name_1:
            return FoundResult(LLM_WEB, NOT_RUN, flags=(LLM_WEB_SEARCH, "NO_NAME"))
        if self.query_mode == HYBRID:
            # Step AD: the entity Name 1–4 describe (vat/name_prep.py) is
            # what is searched and matched; the record keeps its own names.
            plan = prepare(inp.names)
            usage: dict[str, Any] = {"fetches": 0, "cost_usd": 0.0}
            plan = await self._name2(inp, plan, usage)
            m_inp = replace(inp, names=(plan.entity, None, None, None))
            web = await self._web(m_inp, plan, usage)
            result = await self._finish(m_inp, await self._with_peppol(m_inp, web), plan)
            return self._with_plan(result, plan)
        web = await self._web(inp)
        if web.decision == AUTO_ACCEPT:
            return web
        return await self._with_peppol(inp, web)

    async def _with_peppol(self, inp: LookupInput, web: FoundResult) -> FoundResult:
        """One Peppol query per entity ("model" mode: only when the web result
        is not AUTO_ACCEPT; "hybrid": always, and whether it had a hit is
        logged), combined with the web result."""
        self._peppol_asks += 1
        usage = dict(web.usage or {}, peppol_queries=1)
        if self.query_mode == HYBRID:
            entries = await self._peppol.search(peppol_name(inp.name_1))
            usage["peppol_entries"] = len(entries or [])
        web = replace(web, usage=usage)
        peppol = await peppol_result(
            self._peppol, self._vies if self.vies_in_lookup else None,
            inp, LLM_WEB)
        usage["peppol_hit"] = peppol is not None
        if peppol is None:
            return web
        base = tuple(f for f in web.flags if f.startswith((LLM_WEB_SEARCH, SEARCH_VISITED)))
        peppol = replace(peppol, flags=(*peppol.flags, *base), usage=usage)
        exact_web = (web.value and web.name_match == NAME_EXACT
                     and web.source_class in TRUSTED_CLASSES)
        if exact_web and web.value != peppol.value and peppol.name_match == NAME_EXACT:
            # Two trusted sources, two numbers for the same entity.
            ids = ",".join(sorted({web.value, peppol.value}))
            return replace(peppol, flags=(*peppol.flags, f"{CONFLICTING_SOURCES}:{ids}"))
        if web.decision == AUTO_ACCEPT:
            return web
        if peppol.decision == AUTO_ACCEPT or not web.value:
            return replace(peppol, flags=(*peppol.flags, f"{ENGINE}:peppol"))
        return web

    async def _name2(self, inp: LookupInput, plan: NamePlan, usage: dict[str, Any]) -> NamePlan:
        """Rule 10b: the Name-2 classifier on the helper model, for a record
        it applies to (:func:`vat.name2_classifier.eligible`); the plan it
        leads to. Its call is on the entity's usage (cost line
        ``name2_classifier``); a failed call keeps the rule layer's plan."""
        if not self.name2_enabled or not name2_classifier.eligible(plan, inp.names):
            return plan
        system, user = name2_classifier.prompt(inp.names, inp.city, plan)
        out = await self._call(name2_classifier.LABEL, system, user, self._name2_limits, usage,
                               effort=name2_classifier.EFFORT)
        verdict = None if isinstance(out, FoundResult) else name2_classifier.verdict_of(out[0])
        new = name2_classifier.apply(plan, verdict)
        usage.update(name2_rule_class=plan.cls, name2_verdict=verdict, name2_applied=new is not plan)
        return new

    async def _web(self, inp: LookupInput, plan: NamePlan | None = None,
                   usage: dict[str, Any] | None = None) -> FoundResult:
        """The web-search answer, judged; with the entity's usage and the
        limit flags. A model error or malformed output is LOOKUP_INCOMPLETE,
        never retried; NOT_FOUND is final."""
        usage = usage if usage is not None else {"fetches": 0, "cost_usd": 0.0}
        if self.query_mode == HYBRID:
            result = await self._hybrid(inp, usage, plan or prepare(inp.names))
        else:
            result = await self._web_core(inp, usage)
        flags = list(result.flags)
        searches = usage.get("search_calls") or 0
        if searches > self.limits.max_searches:
            flags.append(SEARCH_LIMIT_EXCEEDED)
        elif searches >= self.limits.max_searches:
            flags.append(SEARCH_LIMIT_REACHED)
        opens = usage.get("other_tool_calls") or 0
        if self.query_mode != HYBRID and self.limits.max_opens and opens >= self.limits.max_opens:
            flags.append(OPEN_LIMIT_REACHED)
        if usage.get("incomplete_reason") == "max_output_tokens":
            flags.append(OUTPUT_LIMIT_REACHED)
        if usage["fetches"] >= self._fetch_cap(usage):
            flags.append(FETCH_LIMIT_REACHED)
        return replace(result, flags=tuple(flags), usage=usage)

    def _fetch_cap(self, usage: dict[str, Any]) -> int:
        """The entity's page-fetch budget: VAT_WEB_MAX_FETCHES, or
        VAT_WEB_MAX_FETCHES_MANY_HOSTS once search 1 showed many own hosts."""
        return usage.get("max_fetches") or self.max_fetches

    def _account(self, usage: dict[str, Any], call_usage: dict[str, Any], from_cache: bool) -> None:
        """The call's usage and cost on the entity. A replayed answer reports
        what it cost when recorded, but adds nothing to this run's spend."""
        usage.update(call_usage, from_cache=from_cache)
        usage["cost_usd"] = round(self._call_record(usage, "agent", call_usage, from_cache), 5)

    async def _code_fetch(
        self, inp: LookupInput, visited: list[str], base: tuple[str, ...], usage: dict[str, Any],
        fallback: FoundResult, exclude: tuple[str, ...] = (),
    ) -> FoundResult:
        """The model gave no usable number: our code reads the entity's own
        pages from the search results itself (:func:`own_domain_pages`),
        within the page-fetch limit, without a model and without another
        search. A page is read deterministically (one VAT ID on it, the
        entity named on it) and held to every gate, the address check and
        VIES. The first AUTO_ACCEPT wins; else the first candidate; else
        *fallback*."""
        best: FoundResult | None = None
        for url in own_domain_pages(visited, inp, exclude):
            if usage["fetches"] >= self._fetch_cap(usage):
                break
            usage["fetches"] += 1
            flags: set[str] = set()
            page = await self._pages._fetch(url, flags)
            if page is None or not (page.ok and page.content):
                continue
            text = section_first_text(_readable_text(page.content), self.page_chars)
            reading = deterministic_reading(text, inp.names)
            if reading is None:
                continue
            checked = await self._pages.judge_page(url, reading, inp, flags, max_chars=self.page_chars)
            if checked is None or not checked[1]:
                continue
            result = replace(checked[0], mode=LLM_WEB, flags=(*checked[0].flags, CODE_FETCH, *base))
            if best is None or (result.decision == AUTO_ACCEPT and best.decision != AUTO_ACCEPT):
                best = result
            if best.decision == AUTO_ACCEPT:
                break
        usage["code_fetch"] = bool(best)
        return best or fallback

    async def _web_core(self, inp: LookupInput, usage: dict[str, Any]) -> FoundResult:
        if self.meter.exhausted:
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, BUDGET_EXCEEDED))
        self._asks += 1
        try:
            answer = await self._llm.search(
                LLM_WEB_SYSTEM_PROMPT, self.prompt(inp, self.limits.max_searches, self.limits.max_opens),
                reasoning_effort=self.effort, limits=self.limits,
            )
        except LLMUnavailableFrozen:
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, FROZEN_CACHE_MISS))
        except MalformedOutput as e:
            self._account(usage, e.usage, False)
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, MALFORMED_OUTPUT))
        except (RuntimeError, ValueError):
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, LLM_UNAVAILABLE))
        self._account(usage, answer.usage, answer.from_cache)
        visited = answer.visited
        answer = answer.response

        base = (LLM_WEB_SEARCH, f"{SEARCH_VISITED}:{len(visited)}")
        reading = Reading(
            vat_id=_clean(answer.get("vat_id")),
            attributed_to=_clean(answer.get("attributed_to")),
            evidence_quote=_clean(answer.get("evidence_quote")),
            published_address=_clean(answer.get("published_address")),
        )
        source = _clean(answer.get("source_url"))
        if not reading.vat_id:
            return await self._code_fetch(
                inp, visited, base, usage, FoundResult(LLM_WEB, NOT_FOUND, flags=(*base, LLM_WEB_NO_ANSWER)))
        if not source:
            return FoundResult(LLM_WEB, NOT_FOUND, flags=(*base, ANSWER_WITHOUT_SOURCE))
        unusable = unusable_source(source, inp)
        if unusable:
            return FoundResult(LLM_WEB, NOT_FOUND, source_url=source, flags=(*base, unusable))

        visited_ok = was_visited(source, visited)
        page_flags: set[str] = set()
        usage["fetches"] += 1  # the source page: our only fetch per entity
        checked = await self._pages.judge_page(source, reading, inp, page_flags,
                                               max_chars=self.page_chars)
        unavailable = tuple(sorted(page_flags))
        if checked is None:
            if not visited_ok:
                return FoundResult(LLM_WEB, NOT_FOUND, source_url=source,
                                   flags=(*base, SOURCE_UNREADABLE, SOURCE_NOT_VISITED, *unavailable))
            fmt = assess_format(reading.vat_id, inp.country)
            own = domain_match(source, company_domain(inp.website, inp.email))
            return FoundResult(
                LLM_WEB, FOUND_UNVERIFIED, value=fmt.normalized or reading.vat_id,
                source_url=source, attributed_to=reading.attributed_to,
                evidence=reading.evidence_quote, date=self._llm.last_fetched_at or today(),
                flags=(SOURCE_UNREADABLE, *base, *unavailable), tier=TIER_REVIEW,
                # Nothing on the page could be checked here: no name, address
                # or score — only where the model says it read the number.
                source_type=source_type(source, impressum=False, own_domain=own,
                                        core=parse_name(inp.name_1, sap=True).core),
                validation_status=NOT_CHECKED, issues=(EVIDENCE_NOT_VERIFIED,),
            )
        judged, on_page = checked
        if not on_page:
            dropped = FoundResult(LLM_WEB, NOT_FOUND, source_url=source,
                                  flags=(NOT_ON_SOURCE_PAGE, judged.flags[0], *base))
            return await self._code_fetch(inp, visited, base, usage, dropped, exclude=(source,))
        flags = judged.flags if visited_ok else (*judged.flags, SOURCE_NOT_VISITED)
        # replace(): every piece of evidence the page check recorded is kept.
        return replace(judged, mode=LLM_WEB, flags=(*flags, *base),
                       tier=judged.tier if visited_ok else TIER_REVIEW)

    # ── "hybrid" mode ────────────────────────────────────────────────────────

    def _add_call(self, usage: dict[str, Any], label: str, call: dict[str, Any], from_cache: bool) -> None:
        """One API call's usage summed into the entity's, and listed per call.
        A replayed call reports what it cost when recorded, but adds nothing
        to this run's spend."""
        cost = self._call_record(usage, label, call, from_cache)
        for k in _SUM_KEYS:
            usage[k] = (usage.get(k) or 0) + (call.get(k) or 0)
        usage["cost_usd"] = round((usage.get("cost_usd") or 0) + cost, 5)
        usage["from_cache"] = usage.get("from_cache", True) and from_cache
        if call.get("incomplete_reason"):
            usage["incomplete_reason"] = call["incomplete_reason"]

    def _call_record(self, usage: dict[str, Any], label: str, call: dict[str, Any], from_cache: bool,
                     **extra: Any) -> float:
        """The call listed on the entity with its raw counts and cost lines
        (``vat/costs.py``); its total, added to the run's spend unless it was
        replayed from the cache."""
        counts = self.meter.counts({**call, "http_status": call.get("http_status", 200), **extra},
                                   label=label, cache_hit=from_cache)
        lines = self.meter.lines(counts)
        if not from_cache:
            self.meter.spent += lines[TOTAL]
        usage.setdefault("calls", []).append({
            "call": label, "from_cache": from_cache, "cost_usd": round(lines[TOTAL], 5),
            "new_spend_usd": round(lines[NEW_SPEND], 5), **counts,
            **{k: call.get(k) for k in (*_SUM_KEYS, "queries", "api_status")},
        })
        return lines[TOTAL]

    async def _call(
        self, label: str, system: str, user: str, limits: SearchLimits, usage: dict[str, Any],
        *, effort: str | None = None,
    ) -> tuple[dict[str, Any], list[str]] | FoundResult:
        """One model call; on failure the entity's LOOKUP_INCOMPLETE result.
        *effort*: a helper call's reasoning effort; *limits.model* its deployment."""
        if self.meter.exhausted:
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, BUDGET_EXCEEDED))
        if limits.max_searches:
            self._asks += 1
        on = {"deployment": limits.model} if limits.model else {}
        for attempt in range(2):
            try:
                answer = await self._llm.search(system, user, reasoning_effort=effort or self.effort, limits=limits)
                break
            except LLMUnavailableFrozen:
                return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, FROZEN_CACHE_MISS))
            except MalformedOutput as e:
                self._add_call(usage, label, {**e.usage, **on}, False)
                if e.usage.get("incomplete_reason") != "content_filter":
                    return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, MALFORMED_OUTPUT))
                if attempt:  # stopped twice: never NOT_FOUND
                    return FoundResult(LLM_WEB, f"{LOOKUP_INCOMPLETE}:{CONTENT_FILTER}",
                                       flags=(LLM_WEB_SEARCH, CONTENT_FILTER))
            except (RuntimeError, ValueError):
                return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, LLM_UNAVAILABLE))
        self._add_call(usage, label, {**answer.usage, **on}, answer.from_cache)
        return answer.response, answer.visited

    async def _search(
        self, n: int, system: str, user: str, usage: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]] | FoundResult:
        """Search *n*: one web search, no page opened (``max_tool_calls`` 1).
        ``"agg"``: the aggregator search, run as given like search 1."""
        limits = self._run_search if n in (1, "agg") else self._own_search
        out = await self._call(f"search_{n}", system, user, limits, usage)
        if isinstance(out, FoundResult):
            return out
        usage["searches"] = usage.get("search_calls") or 0
        queries = usage["calls"][-1].get("queries") or []
        usage[f"query_{n}_run"] = queries[0] if queries else None
        usage[f"results_{n}"] = len(out[1])
        usage[f"result_urls_{n}"] = list(out[1])
        return out

    async def _get(
        self, url: str, n: int | str, via: str, kind: str, inp: LookupInput, usage: dict[str, Any],
        pages: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """One page: fetched (``vat_pages``), its full text, its sections, the
        VAT-ID-shaped strings on the whole page and a no-VAT statement.
        *pages* records every attempt with how it was reached."""
        flags: set[str] = set()
        page = await self._pages._fetch(url, flags)
        info: dict[str, Any] = {"url": url, "round": n, "via": via, "kind": kind, "pdf": is_pdf_url(url)}
        pages[url] = info
        if page is None or not (page.ok and page.content):
            info["status"] = (page.status if page is not None else None) or ",".join(sorted(flags)) or "ERROR"
            code = _blocked_code(page, flags)
            if code is None or is_pdf_url(url):
                return None
            # A host refused it: once more as a browser. Still refused: the
            # caller moves the remaining budget to the next own host (rule 2);
            # the archive comes only when every own host refused.
            usage.setdefault("blocked", []).append({"url": url, "code": code, "kind": kind})
            info["blocked"] = code
            page = await self._pages.fetch_browser(url, flags)
            if page is None:
                return None
            info.update(status=page.status, via=f"{via}+browser", blocked=None)
            usage["blocked"][-1]["recovered"] = info["via"]
        if info["pdf"]:
            usage["pdf_fetches"] = (usage.get("pdf_fetches") or 0) + 1
        text = _readable_text(page.content)
        sections = page_sections(text, window=self.section_window, limit=self.sections_chars)
        hits = vat_candidates(text)
        info.update(status=page.status, chars=len(text), sections_chars=len(sections), hits=hits,
                    hits_in_sections=[h for h in hits if id_on_page(h, sections)],
                    no_vat=no_vat_statement(text))
        # A short page that still states a number or an address is a small
        # site, not one rendered by script.
        if len(text) < NEEDS_JS_CHARS and not info["pdf"] and kind != VERIFY and not hits \
                and not has_address(text):
            info["needs_js"] = True
            usage["needs_js"] = (usage.get("needs_js") or 0) + 1
        return {**info, "text": text, "sections": sections, "links": list(page.content.links or [])}

    async def _fetch_hosts(
        self, n: int, picked: list[tuple[str, str]], inp: LookupInput, usage: dict[str, Any],
        pages: dict[str, dict[str, Any]], hosts: dict[str, dict[str, Any]], result_urls: list[str],
        alt_names: tuple[str, ...] = (), plan: NamePlan | None = None,
    ) -> list[dict[str, Any]]:
        """One page per picked host, within VAT_WEB_MAX_FETCHES (Step AF).

        * A legal page that does not name the entity rejects its host: no
          further fetch there, never a source (:func:`vat.web_pages.host_check`).
          Another legal form does not reject: the page is marked and its
          number is a candidate for review (LEGAL_FORM_DIFFERS).
        * A host that refuses us (403 / 5xx / timeout, also as a browser) is
          left: the next own host in the list gets the remaining budget (rule 2).
        * On an accepted host whose page states no VAT ID, Impressum / Steuern
          / Lieferanten / Einkauf / AGB / Kontakt links are followed
          (VAT_WEB_MAX_FOLLOWS per entity, not fetches).
        * One probe per entity, one fetch (``/impressum``, else ``/imprint``),
          only on an OWN host that answered (HTTP 200, or a stale link's 404:
          the host is up; never one that refused us, timed out or is down)
          and showed no Impressum-type page (rule 1): never a pointer, parent
          or unverified host.
        * A host "answered" when an Impressum-type page (rule 9: impressum,
          imprint, legal, rechtliche hinweise, steuer, agb; never Kontakt or
          Über uns) was read on it with HTTP 200, not rejected, not empty.
        * An unverified host (another host's Impressum, an institutional parent
          domain, a host the ranking model vouched for) counts as own only once
          an Impressum-type page on it names the entity; else nothing read on
          it is used."""
        plan = plan or prepare(inp.names)
        university = is_university(plan)
        read: list[dict[str, Any]] = []
        for url, kind in picked:
            host = host_of(url)
            if host in hosts and (hosts[host].get("rejected") or hosts[host].get("blocked")):
                continue
            if usage["fetches"] >= self._fetch_cap(usage):
                usage.setdefault("not_fetched", []).append({"url": url, "why": "fetch limit"})
                continue
            usage["fetches"] += 1
            entry = hosts.setdefault(host, {"kind": kind, "round": n, "fetched": [], "rejected": None,
                                            "answered": False})
            entry["fetched"].append(url)
            fetched_keys = {page_key(url)}
            queue: list[str] = []
            got = await self._get(url, n, "rank", kind, inp, usage, pages)
            if got is None and pages.get(url, {}).get("blocked"):
                entry["blocked"] = pages[url]["blocked"]  # rule 2: the next own host
                continue
            mine: list[dict[str, Any]] = []
            impressum_read = host_answered = False
            while True:
                if got is not None:
                    status = got.get("status") if isinstance(got.get("status"), int) else None
                    host_answered = host_answered or status == 200
                    empty = is_empty_legal_page(got["url"], got["text"]) or bool(got.get("needs_js"))
                    # A page that came back empty (rendered by script) says
                    # nothing about whose host it is: never rejects.
                    reason, named = (None, "NOT_CHECKED") if empty else host_check(
                        got["text"], got["url"], inp, kind, alt_names, plan=plan)
                    pages[got["url"]]["name_on_page"] = got["name_on_page"] = named
                    differs = None if empty else form_difference(got["text"], plan)
                    if differs:
                        pages[got["url"]]["form_differs"] = got["form_differs"] = differs
                    if reason:
                        entry["rejected"] = reason
                        pages[got["url"]]["rejected"] = reason
                        break
                    gate = is_gate_page(got["url"]) and not empty and status is not None and 200 <= status < 300
                    if kind in UNVERIFIED and gate and named not in ("ABSENT", "NOT_CHECKED", PARENT_CANDIDATE) \
                            and (kind != CANDIDATE or not plan.generic
                                 or name_on_page(got["text"], plan.acceptable, strict=True)):
                        entry["kind"] = kind = OWN  # its Impressum names the entity
                        pages[got["url"]]["kind"] = got["kind"] = OWN
                    if named == "ABBREVIATED":
                        entry["abbreviated"] = True
                    pages[got["url"]]["empty"] = empty
                    if gate:
                        impressum_read = entry["answered"] = True
                    mine.append(got)
                    if got["hits"]:
                        break
                    if kind != VERIFY and usage["follows"] < self.max_follows:
                        targets = follow_targets(got["url"], got["links"],
                                                 [u for u in result_urls if host_of(u) == host], fetched_keys,
                                                 university=university)
                        queue = [t for t in targets if page_key(t) not in fetched_keys]
                if kind == VERIFY:
                    break
                nxt, via = None, None
                if queue and usage["follows"] < self.max_follows:
                    nxt, via = queue.pop(0), "follow"
                elif kind == OWN and (host_answered or _answered(pages, entry)) and not impressum_read \
                        and not usage.get("probes") \
                        and usage["fetches"] < self._fetch_cap(usage):
                    # An own host that answered but showed no Impressum (TIB:
                    # script-rendered): <host>/impressum, else /imprint; one fetch.
                    probes = impressum_probes(url, fetched_keys)
                    nxt, via = (probes[0], "probe") if probes else (None, None)
                if nxt is None:
                    break
                if via == "follow":
                    usage["follows"] += 1
                else:
                    usage["probes"] = 1
                    usage["fetches"] += 1
                entry["fetched"].append(nxt)
                fetched_keys.add(page_key(nxt))
                got = await self._get(nxt, n, via, kind, inp, usage, pages)
                if got is None and via == "probe":
                    second = impressum_probes(url, fetched_keys)
                    if second and second[0].endswith("/imprint"):  # the same probe, no second fetch
                        entry["fetched"].append(second[0])
                        fetched_keys.add(page_key(second[0]))
                        got = await self._get(second[0], n, "probe", kind, inp, usage, pages)
            if entry["kind"] in UNVERIFIED:
                if entry.get("rejected") is None:
                    entry["rejected"] = "NOT_VERIFIED"  # nothing on it named the entity
                for p in mine:
                    pages[p["url"]]["unverified"] = True
                continue
            if entry.get("rejected") is None:
                read += mine
        return read

    async def _read(
        self, n: int | str, read: list[dict[str, Any]], inp: LookupInput, usage: dict[str, Any],
        base: tuple[str, ...], plan: NamePlan | None = None,
    ) -> FoundResult | None:
        """The reader on one round's pages; the number it names, judged on
        the page it was read from. None when it names none (or one that is
        not on the page); a LOOKUP_INCOMPLETE result when the call failed."""
        worth = [p for p in read if p["hits"] or p["no_vat"] or _VAT_WORD_RE.search(p["sections"])]
        if not worth:
            return None
        listing = "\n\n".join(
            f"Page {i}: {p['url']}\n"
            f"VAT-ID-shaped strings on the whole page: {', '.join(p['hits']) or 'none'}\n"
            f"Extracts:\n---\n{p['sections']}\n---"
            for i, p in enumerate(worth, 1))
        plan = plan or prepare(inp.names)
        alt = tuple(x for x in (usage.get("name_used"),) if x)
        user = LLM_WEB_READ_USER_PROMPT_TEMPLATE.format(
            name=" / ".join(plan.acceptable), pages=listing)
        out = await self._call(f"read_{n}", LLM_WEB_READ_SYSTEM_PROMPT, user, self._reader, usage)
        if isinstance(out, FoundResult):
            return out
        usage["readers"] = (usage.get("readers") or 0) + 1
        answer = out[0]
        statement = _clean(answer.get("no_vat_statement"))
        for p in worth:  # the page's own words, else the reader's verbatim copy
            quote = p["no_vat"] or (statement if statement and _ws(statement) in _ws(p["text"]) else None)
            # Only the entity speaking for itself: its own site, or a page
            # that names it with its legal form. A tax adviser's article on
            # Kleinunternehmer says nothing about this entity.
            own = not is_third_party_host(p["url"]) and (
                p["url"] in own_domain_pages([p["url"]], inp) or entity_name_on_page(p["text"], inp.names))
            if quote and own and not usage.get("no_vat"):
                usage["no_vat"] = {"url": p["url"], "quote": quote}
        vat_id = _clean(answer.get("vat_id"))
        ambiguous: tuple[str, ...] = ()
        extracted = False
        named_pages = [p for p in worth if p.get("name_on_page") in ("EXACT", "ABBREVIATED", "NAME_USED")]
        if not vat_id:
            # The reader named none. Rule 12: a page that names the entity
            # and states exactly one number → that number
            # (EXTRACTED_BY_PATTERN); two or more → all of them as
            # candidates for a person (CONFLICT), never one picked by pattern.
            for p in named_pages:
                passing, every = block_numbers(p["text"], plan.acceptable, alt)
                if len(every) == 1:
                    answer = dict(answer, vat_id=every[0], source_url=p["url"], evidence_quote=None,
                                  attributed_to=None)
                    vat_id, extracted = every[0], True
                    break
                if len(every) >= 2:
                    pick = passing[0] if len(passing) == 1 else every[0]
                    answer = dict(answer, vat_id=pick, source_url=p["url"], evidence_quote=None,
                                  attributed_to=None)
                    vat_id, ambiguous = pick, tuple(sorted(every))
                    break
        if not vat_id:
            picked = self._candidate(answer, worth, inp)
            if picked is None:
                return None
            answer, ambiguous = picked
            vat_id = answer["vat_id"]
        source = _clean(answer.get("source_url"))
        by_url = {_url_key(p["url"]): p for p in worth}
        page = by_url.get(_url_key(source)) if source else None
        if page is None or not id_on_page(vat_id, page["text"]):
            # The reader named another page (or none): the page that states it.
            page = next((p for p in worth if id_on_page(vat_id, p["text"])), None)
        if page is None:
            usage.setdefault("dropped", []).append({"vat_id": vat_id, "why": NOT_ON_SOURCE_PAGE})
            return None
        # A group page (chain, holding, industrial park): the number in the
        # block that names the entity, never just the first one.
        passing, every = block_numbers(page["text"], plan.acceptable, alt)
        block_flag = None
        if len(every) >= 2 and not ambiguous:
            norm = assess_format(vat_id, "DE").normalized or vat_id
            if len(passing) == 1 and passing[0] != norm:
                answer = dict(answer, vat_id=passing[0], evidence_quote=None, attributed_to=None)
                vat_id, block_flag = passing[0], BLOCK_SELECTED
            elif not passing or len(passing) > 1:
                ambiguous = tuple(sorted(passing or every))
        if page.get("via") != "wayback" and unusable_source(page["url"], inp):
            usage.setdefault("dropped", []).append({"vat_id": vat_id, "why": unusable_source(page["url"], inp)})
            return None
        reading = Reading(
            vat_id=vat_id, attributed_to=_clean(answer.get("attributed_to")),
            evidence_quote=_clean(answer.get("evidence_quote")),
            other_vat_ids=tuple(answer.get("other_vat_ids_on_page") or ()),
            published_address=_clean(answer.get("published_address")),
        )
        flags: set[str] = set()
        checked = await self._pages.judge_page(page["url"], reading, inp, flags)
        if checked is None or not checked[1]:
            usage.setdefault("dropped", []).append({"vat_id": vat_id, "why": "PAGE_CHECK_FAILED"})
            return None
        judged = self._reconcile_name(checked[0], plan, page)
        from vat.entity_match import (
            EXTRACTED_BY_PATTERN, LEGAL_FORM_DIFFERS, OTHER_VAT_IDS_ON_PAGE as _OTHERS, PARENT_ENTITY,
            PARENT_ENTITY_CANDIDATE as _PARENT_CAND,
        )
        extra_issues: list[str] = []
        if extracted:
            if any(i.startswith(_OTHERS) for i in judged.issues):
                # Never both (rule 12): the page has more than this number.
                ambiguous = tuple(sorted({vat_id, *every}))
            else:
                extra_issues.append(EXTRACTED_BY_PATTERN)
        # Rule 13: the page's notice is another company's: its number is the
        # parent's (an umbrella of vat/umbrella_entities.yaml may be AUTO).
        if page.get("name_on_page") == PARENT_CANDIDATE and PARENT_ENTITY not in judged.issues:
            extra_issues.append(_PARENT_CAND)
        # Both sides state a legal form and they differ: a candidate, never
        # dropped by the name reconcile.
        if page.get("form_differs") and not any(i.startswith(LEGAL_FORM_DIFFERS) for i in judged.issues):
            extra_issues.append(f"{LEGAL_FORM_DIFFERS}:{page['form_differs']}")
        if extra_issues:
            judged = replace(judged, issues=tuple(dict.fromkeys([*judged.issues, *extra_issues])))
        if page.get("via") == "wayback" or n == "archive":
            from vat.entity_match import archive_snapshot_date
            judged = replace(judged, date=archive_snapshot_date(page["url"]) or judged.date)
        extra = [f"{FOUND_BY_SEARCH}:{n}", f"{ENGINE}:{LLM_WEB}"]
        if block_flag:
            extra.append(block_flag)
        if page["pdf"]:
            extra.append(PDF_SOURCE)
        if n == "archive" or page.get("via") == "wayback":
            extra.append(ARCHIVE_SOURCE)
        if ambiguous:
            # A person picks: every number on the page, each with its company.
            extra.append(f"{AMBIGUOUS_SAME_NAME}:{','.join(ambiguous)}")
            extra.append(AMBIGUOUS_ON_PAGE)
        usage.update(found_by=n, source_page=page["url"], pdf_used=page["pdf"],
                     found_via=page.get("via"), found_kind=page.get("kind"))
        return replace(judged, mode=LLM_WEB, flags=(*judged.flags, *extra, *base))

    @staticmethod
    def _with_plan(result: FoundResult, plan: NamePlan) -> FoundResult:
        """The plan's issues (SUB_ENTITY, DEPARTMENT, BRANCH) on a found
        number, GENERIC_NAME as a flag (it needs the address EXACT for AUTO),
        and the plan in the usage log."""
        usage = dict(result.usage or {}, name_class=plan.cls, entity=plan.entity,
                     search_name=plan.search_name, acceptable_names=list(plan.acceptable),
                     plan_issues=list(plan.issues), generic=plan.generic, public=plan.public,
                     contact=list(plan.contact), name_context=list(plan.context), plan_form=plan.form)
        if not result.value:
            return replace(result, usage=usage)
        issues = [i for i in plan.issues if i != GENERIC_NAME]
        flags = list(result.flags)
        if plan.generic and GENERIC_NAME not in flags:
            flags.append(GENERIC_NAME)
        return replace(result, issues=tuple(dict.fromkeys([*result.issues, *issues])), flags=tuple(flags),
                       usage=usage)

    def _reconcile_name(self, result: FoundResult, plan: NamePlan, page: dict[str, Any]) -> FoundResult:
        """The matcher judged the page's name against the entity name alone.
        Another acceptable name (a CONTINUATION's Name 1), the entity's
        initials (BMW for Bayerische Motoren Werke: NAME_ABBREVIATED), or an
        umbrella parent (PARENT_ENTITY) can still make it EXACT; a page that
        names the entity but gives another organisation's number is
        PARENT_ENTITY_CANDIDATE (review)."""
        from vat.entity_match import (
            ISSUE_NAME_PARTIAL, LEGAL_FORM_DIFFERS, LEGAL_FORM_MISSING_IN_SAP, NAME_MISMATCH as _MISMATCH,
            PARENT_ENTITY, PARENT_ENTITY_CANDIDATE, name_match, parse_name as _pn,
        )

        if result.name_match == NAME_EXACT or not result.attributed_to:
            return result
        drop = (ISSUE_NAME_PARTIAL, LEGAL_FORM_DIFFERS)
        keep = tuple(i for i in result.issues if not i.startswith(drop))
        umbrella = umbrella_of(result.attributed_to, plan)
        if umbrella:
            return replace(result, name_match=NAME_EXACT,
                           issues=tuple(dict.fromkeys([*keep, PARENT_ENTITY])))
        for name in plan.acceptable[1:]:
            verdict = name_match(result.attributed_to, (name, None, None, None))
            if verdict.match == NAME_EXACT:
                return replace(result, name_match=NAME_EXACT,
                               issues=tuple(dict.fromkeys([*keep, *verdict.issues])))
        for name in plan.acceptable:
            if initials_match(_pn(result.attributed_to).core, _pn(name, sap=True).core):
                extra = [LEGAL_FORM_MISSING_IN_SAP] if not plan.form and _pn(result.attributed_to).form else []
                return replace(result, name_match=NAME_EXACT, issues=tuple(dict.fromkeys([*keep, *extra])),
                               flags=(*result.flags, NAME_ABBREVIATED))
        if result.name_match == _MISMATCH and page.get("name_on_page") in ("EXACT", "ABBREVIATED", "NAME_USED"):
            return replace(result, issues=tuple(dict.fromkeys([*result.issues, PARENT_ENTITY_CANDIDATE])))
        return result

    @staticmethod
    def _candidate(answer: dict[str, Any], worth: list[dict[str, Any]], inp: LookupInput):
        """The reader named no number but listed the page's numbers with their
        companies (Hochschulen Fresenius: three entities, three numbers): the
        one whose company best matches the record, for a person to confirm
        against the others. ``(answer for it, every number)`` or None."""
        from vat.entity_match import NAME_PARTIAL, name_match

        cands = [c for c in answer.get("candidates") or [] if _clean(c.get("vat_id"))
                 and any(id_on_page(c["vat_id"], p["text"]) for p in worth)]
        if not cands:
            return None
        rank = {NAME_EXACT: 0, NAME_PARTIAL: 1}
        best = min(cands, key=lambda c: rank.get(name_match(c.get("attributed_to"), inp.names).match, 2))
        ids = tuple(sorted({assess_format(c["vat_id"], "DE").normalized or c["vat_id"] for c in cands}))
        chosen = dict(answer, vat_id=best["vat_id"], source_url=best.get("source_url"),
                      attributed_to=best.get("attributed_to"), evidence_quote=None)
        return chosen, ids if len(ids) > 1 else ()

    @staticmethod
    def _usable(result: FoundResult | None) -> bool:
        """A candidate that settles the entity: search 2 is not needed."""
        return bool(result and result.value and result.name_match != NAME_MISMATCH)

    @staticmethod
    def _better(a: FoundResult | None, b: FoundResult | None) -> FoundResult | None:
        if a is None or not a.value:
            return b if b is not None and b.value else a
        if b is None or not b.value:
            return a
        rank = lambda r: (r.decision != AUTO_ACCEPT, r.name_match == NAME_MISMATCH)  # noqa: E731
        return b if rank(b) < rank(a) else a

    def _results_listing(self, results: list[dict[str, Any]], skipped: list[tuple[str, str]]) -> str:
        why = dict(skipped)
        lines = [f"- {r['url']}" + (f" ({r['title']})" if r.get("title") else "")
                 + (f" [{why[r['url']]}]" if r["url"] in why else "") for r in results[:12]]
        return "\n".join(lines) or "- (no results)"

    @staticmethod
    def _results(visited: list[str], answer: dict[str, Any]) -> list[dict[str, Any]]:
        """The API's result URLs, in order, with the title and directory
        website link the model transcribed; results only the model listed
        come after (marked)."""
        listed = {_url_key(r.get("url") or ""): r for r in answer.get("results") or [] if r.get("url")}
        out = []
        for url in visited:
            r = listed.pop(_url_key(url), {})
            out.append({"url": url, "title": r.get("title"), "website": r.get("website")})
        out += [{"url": r["url"], "title": r.get("title"), "website": r.get("website"), "model_only": True}
                for r in listed.values()]
        return out

    async def _hybrid(self, inp: LookupInput, usage: dict[str, Any], plan: NamePlan) -> FoundResult:
        """Search 1 (name + city, built by code) → one page per own / parent
        host, links followed → the reader. Search 2 (the model's query) only
        when search 1 showed no own host that passed the host check. At most
        VAT_WEB_MAX_SEARCHES searches (each call one search, no page opens),
        VAT_WEB_MAX_FETCHES fetches and VAT_WEB_MAX_FOLLOWS link follows."""
        names = list(plan.names) + [None] * (4 - len(plan.names))
        q1 = query_for(plan, inp.city)
        usage.update(query_1=q1, name_2_appended=plan.cls == "CONTINUATION", searches=0, readers=0, follows=0,
                     pdf_fetches=0, archive_fetches=0, reasoning_effort=self.effort)
        pages: dict[str, dict[str, Any]] = {}
        hosts: dict[str, dict[str, Any]] = {}
        usage["pages"], usage["hosts"] = pages, hosts

        out = await self._search(1, LLM_WEB_RUN_SYSTEM_PROMPT,
                                 LLM_WEB_RUN_USER_PROMPT_TEMPLATE.format(query=q1), usage)
        if isinstance(out, FoundResult):
            return out
        results_1 = self._results(out[1], out[0])
        base = (LLM_WEB_SEARCH, f"{SEARCH_VISITED}:{len(out[1])}")
        if usage.get("query_1_run") and _ws(usage["query_1_run"]) != _ws(q1):
            base = (*base, QUERY_NOT_RUN_AS_GIVEN)
        picked, skipped = select_pages(results_1, inp, plan=plan)
        # Step AF-2: three or more own hosts in the results → 6 fetches, not 4.
        usage["own_hosts_1"] = len({host_of(u) for u, kind in picked if kind == OWN})
        usage["max_fetches"] = (self.max_fetches_many_hosts if usage["own_hosts_1"] >= self.many_own_hosts
                                else self.max_fetches)
        picked = await self._rank(1, results_1, picked, inp, plan, usage)
        usage["picked_1"], usage["skipped_1"] = picked, skipped
        read = await self._fetch_hosts(1, picked, inp, usage, pages, hosts, [r["url"] for r in results_1],
                                       plan=plan)
        best = await self._read(1, read, inp, usage, base, plan)
        if isinstance(best, FoundResult) and best.status == LOOKUP_INCOMPLETE:
            return best

        own_found = any(h["rejected"] is None and any(pages.get(u, {}).get("chars") for u in h["fetched"])
                        for h in hosts.values())
        usage["own_host_found_1"] = own_found
        # Search 2 only when no own host ANSWERED: an Impressum-type page read
        # with HTTP 200, not rejected, not empty. A blocked, unreachable,
        # rejected or script-rendered host does not count (J&M: j-m.de dead).
        answered_1 = any(h.get("answered") and not h.get("rejected") for h in hosts.values())
        usage["own_host_seen_1"] = any(kind in (OWN, POINTER, PARENT) for _, kind in picked)
        usage["own_host_answered_1"] = answered_1
        n_results = len(results_1)
        if not self._usable(best) and not answered_1 and self.limits.max_searches > 1:
            user = LLM_WEB_SECOND_USER_PROMPT_TEMPLATE.format(
                query_1=q1, results_1=self._results_listing(results_1, skipped),
                zero_results_rule=LLM_WEB_ZERO_RESULTS_RULE if not results_1 else "",
                name_1=plan.entity, name_2=names[1] or "", name_3=names[2] or "",
                name_4=names[3] or "", city=inp.city or "",
                domain=company_domain(inp.website, inp.email) or "")
            out = await self._search(2, LLM_WEB_SECOND_SYSTEM_PROMPT, user, usage)
            if isinstance(out, FoundResult):
                return replace(best, flags=(*best.flags, *out.flags)) if best and best.value else out
            answer = out[0]
            usage.update(query_2=_clean(answer.get("query")), name_used=_clean(answer.get("name_used")),
                         why_this_query=_clean(answer.get("why_this_query")))
            results_2 = self._results(out[1], answer)
            n_results += len(results_2)
            base = (LLM_WEB_SEARCH, f"{SEARCH_VISITED}:{n_results}", *base[2:])
            picked, skipped = select_pages(results_2, inp, plan=plan, done_hosts=frozenset(hosts))
            picked = await self._rank(2, results_2, picked, inp, plan, usage, done_hosts=frozenset(hosts))
            picked = self._with_domain_hint(_clean(answer.get("domain_hint")), picked, inp, plan, usage, hosts)
            usage["picked_2"], usage["skipped_2"] = picked, skipped
            read = await self._fetch_hosts(2, picked, inp, usage, pages, hosts,
                                           [r["url"] for r in results_2],
                                           alt_names=tuple(x for x in (usage.get("name_used"),) if x), plan=plan)
            second = await self._read(2, read, inp, usage, base, plan)
            if isinstance(second, FoundResult) and second.status == LOOKUP_INCOMPLETE:
                return replace(best, flags=(*best.flags, *second.flags)) if best and best.value else second
            best = self._better(best, second)
            hint_host = usage.get("domain_hint_host")
            if hint_host:
                h = hosts.get(hint_host) or {}
                usage["domain_hint_passed"] = h.get("kind") == OWN and not h.get("rejected")
                if best and best.value and best.source_url and host_of(best.source_url) == hint_host:
                    best = replace(best, flags=(*best.flags, DOMAIN_HINT))
        usage["visited"] = n_results
        usage["own_host_seen"] = any(h["kind"] in (OWN, POINTER) for h in hosts.values())

        usage["impressum_read"] = any(h.get("answered") and not h.get("rejected") for h in hosts.values())
        if not (best and best.value) and inp.deleted:
            best = self._better(best, await self._archive(inp, pages, usage, base, plan))
        if best and best.value:
            if any(h.get("abbreviated") and best.source_url and host_of(best.source_url) == host
                   for host, h in hosts.items()):
                best = replace(best, flags=(*best.flags, NAME_ABBREVIATED))
            return best
        # Rule 2: LOOKUP_INCOMPLETE only when EVERY own host refused us (also
        # as a browser); one archive snapshot of the first one's Impressum first.
        own_hosts = [h for h in hosts.values() if h["kind"] in (OWN, POINTER)]
        all_blocked = bool(own_hosts) and all(h.get("blocked") for h in own_hosts)
        usage["own_hosts_all_blocked"] = all_blocked
        if all_blocked:
            found = await self._blocked_archive(inp, hosts, pages, usage, base, plan)
            if found is not None:
                return found
            code = next(h["blocked"] for h in own_hosts)
            return FoundResult(LLM_WEB, f"{LOOKUP_INCOMPLETE}:{HTTP_BLOCKED}_{code}",
                               flags=(*base, f"{HTTP_BLOCKED}_{code}", ALL_OWN_HOSTS_BLOCKED))
        return FoundResult(LLM_WEB, NOT_FOUND, flags=(*base, LLM_WEB_NO_ANSWER))

    async def _blocked_archive(
        self, inp: LookupInput, hosts: dict[str, dict[str, Any]], pages: dict[str, dict[str, Any]],
        usage: dict[str, Any], base: tuple[str, ...], plan: NamePlan,
    ) -> FoundResult | None:
        """Every own host refused us: one Internet Archive snapshot of the first
        one's ``/impressum`` (archive budget 1 per entity; class C, review)."""
        if (usage.get("archive_fetches") or 0) >= 1:
            return None
        host = next(h for h, e in hosts.items() if e.get("blocked"))
        snapshot = await self._wayback(f"https://{host}/impressum")
        if not snapshot:
            return None
        usage["archive_fetches"] = (usage.get("archive_fetches") or 0) + 1
        got = await self._get(snapshot, "archive", "wayback", OWN, inp, usage, pages)
        if got is None:
            return None
        got["name_on_page"] = pages[got["url"]]["name_on_page"] = host_check(
            got["text"], "/impressum", inp, OWN, plan=plan)[1]
        result = await self._read("archive", [got], inp, usage, base, plan)
        return result if isinstance(result, FoundResult) and result.value else None

    async def _rank(
        self, n: int, results: list[dict[str, Any]], picked: list[tuple[str, str]], inp: LookupInput,
        plan: NamePlan, usage: dict[str, Any], done_hosts: frozenset[str] = frozenset(),
    ) -> list[tuple[str, str]]:
        """Rule 8b: the helper model orders the results (at most 3, each typed
        and with belongs_to_entity); its picks come first, then rule 8's. A
        host it says belongs to the entity (yes / likely) is a CANDIDATE even
        without a token or initials match: it still needs its Impressum to name
        the entity. The model never decides anything else. A failed or empty
        answer leaves rule 8's order (``ranking_<n>`` in the usage)."""
        if self.ranking != MODEL_RANKING or not self.helper_model or not results:
            usage[f"ranking_{n}"] = HEURISTIC_RANKING
            return picked
        lines = []
        for r in results[:15]:
            why = never_fetch(r["url"])
            if why and not why.startswith("directory"):
                continue  # the blocklist applies before the model sees them
            lines.append(f"- {r['url']}" + (f" | {r['title']}" if r.get("title") else "")
                         + (f" | website: {r['website']}" if r.get("website") else ""))
        if not lines:
            usage[f"ranking_{n}"] = HEURISTIC_RANKING
            return picked
        user = LLM_WEB_RANK_USER_PROMPT_TEMPLATE.format(
            search_name=plan.search_name, acceptable=" / ".join(plan.acceptable), city=inp.city or "",
            segment=name2_classifier.segment_of(plan), results="\n".join(lines))
        out = await self._call(f"rank_{n}", LLM_WEB_RANK_SYSTEM_PROMPT, user, self._rank_limits, usage,
                               effort=HELPER_EFFORT)
        if isinstance(out, FoundResult) or not isinstance((out[0] or {}).get("ranked"), list):
            usage[f"ranking_{n}"] = f"{HEURISTIC_RANKING} ({RANKING_FALLBACK})"
            return picked
        by_url = {_url_key(r["url"]): r for r in results}
        heuristic = {host_of(u): (u, k) for u, k in picked}
        chosen: list[tuple[str, str]] = []
        log = []
        for item in out[0]["ranked"][:3]:
            r = by_url.get(_url_key(str(item.get("url") or "")))
            entry = {k: item.get(k) for k in ("url", "type", "belongs_to_entity", "reason")}
            log.append(entry)
            if r is None:
                entry["used"] = "not in the results"
                continue
            url, belongs = r["url"], item.get("belongs_to_entity")
            why = never_fetch(url)
            if why or item.get("type") == "directory_pointer":
                # A directory entry only points: its website, never the entry itself.
                website = with_scheme(r.get("website"))
                if website.startswith(("http://", "https://")) and not never_fetch(website):
                    url, kind = website, POINTER
                else:
                    entry["used"] = f"never fetched ({why or 'a directory page'})"
                    continue
            else:
                kind = host_kind(url, inp, title=r.get("title"), plan=plan) or institutional_kind(url, plan)
                if kind is None and belongs in ("yes", "likely"):
                    kind = CANDIDATE
            host = host_of(url)
            if kind is None or host in done_hosts or any(host_of(u) == host for u, _ in chosen):
                entry["used"] = "not own" if kind is None else "host already listed"
                continue
            if host in heuristic:
                # Rule 8 already picked this host: its best path, the stronger kind.
                url = heuristic[host][0]
                kind = heuristic[host][1] if heuristic[host][1] in (OWN, POINTER) else kind
            chosen.append((url, kind))
            entry["used"] = kind
        usage[f"ranking_{n}"] = MODEL_RANKING
        usage[f"ranked_{n}"] = log
        return chosen + [p for p in picked if host_of(p[0]) not in {host_of(u) for u, _ in chosen}]

    def _with_domain_hint(
        self, hint: str | None, picked: list[tuple[str, str]], inp: LookupInput, plan: NamePlan,
        usage: dict[str, Any], hosts: dict[str, dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Rule 11: search 2's domain_hint → ``<hint>/impressum`` as a candidate
        (one fetch), after the own hosts the results gave. Own only when that
        page names the entity; never a source class by itself."""
        usage["domain_hint"] = hint
        host = host_of(f"https://{re.sub(r'^https?://', '', hint or '').split('/')[0]}") if hint else ""
        if not host or "." not in host or host in hosts or any(host_of(u) == host for u, _ in picked) \
                or never_fetch(f"https://{host}/"):
            usage["domain_hint_fetched"] = False
            return picked
        # A bare domain ("uni-heidelberg.de") as its www host; redirects are followed.
        url = f"https://{'www.' if host.count('.') == 1 else ''}{host}/impressum"
        host = host_of(url)
        usage["domain_hint_host"] = host
        kind = OWN if host_kind(url, inp, plan=plan) == OWN else CANDIDATE
        usage.update(domain_hint_host=host, domain_hint_fetched=True)
        own = [p for p in picked if p[1] in (OWN, POINTER)]
        return [*own, (url, kind), *[p for p in picked if p not in own]]

    async def _archive(
        self, inp: LookupInput, pages: dict[str, dict[str, Any]], usage: dict[str, Any],
        base: tuple[str, ...], plan: NamePlan | None = None,
    ) -> FoundResult | None:
        """A deleted record whose own host is gone (DNS / connection error, or
        404 / 410 on its Impressum): one Internet Archive snapshot of
        ``<host>/impressum``, read like any page (class C, review; the
        snapshot date is VAT_Found_Date)."""
        if (usage.get("archive_fetches") or 0) >= 1:
            return None
        gone = next((u for u, i in pages.items() if i.get("kind") in (OWN, POINTER) and (
            i.get("status") in (404, 410) or "ConnectionError" in str(i.get("status"))
            or "NameResolution" in str(i.get("status")) or "ERROR" == i.get("status"))), None)
        if gone is None:
            return None
        p = urlparse(gone)
        snapshot = await self._wayback(f"{p.scheme or 'https'}://{p.hostname}/impressum")
        if not snapshot:
            return None
        usage["archive_fetches"] = (usage.get("archive_fetches") or 0) + 1
        got = await self._get(snapshot, "archive", "archive", OWN, inp, usage, pages)
        if got is not None:
            got["name_on_page"] = pages[got["url"]]["name_on_page"] = host_check(
                got["text"], "/impressum", inp, OWN, plan=plan)[1]
        result = await self._read("archive", [got] if got else [], inp, usage, base, plan)
        return result if isinstance(result, FoundResult) and result.value else None

    async def _wayback(self, url: str) -> str | None:
        store = self._vies.evidence_cache.namespace("vat_wayback")
        recorded = store.get(url)
        if isinstance(recorded, dict):
            return recorded.get("snapshot")
        if store.replay_only:
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(WAYBACK_API, params={"url": url})
            closest = ((resp.json() or {}).get("archived_snapshots") or {}).get("closest") or {}
        except (httpx.HTTPError, ValueError):
            return None
        snapshot = closest.get("url") if closest.get("available") else None
        self._vies.write_failures.set_checked(store, url, {"snapshot": snapshot})
        return snapshot

    async def _finish(self, inp: LookupInput, result: FoundResult, plan: NamePlan | None = None) -> FoundResult:
        """Still NOT_FOUND after the web and Peppol: the grounded (SerpAPI)
        engine once with search 1, when it is switched on; then
        NOT_FOUND_NO_VAT_EXPECTED when a page says the entity has no
        USt-IdNr., or it is a school / authority."""
        usage = dict(result.usage or {})
        usage.setdefault("engine", LLM_WEB if result.value else None)
        if result.value and any(f == f"{ENGINE}:peppol" for f in result.flags):
            usage["engine"] = "peppol"
        if result.status != NOT_FOUND:
            return replace(result, usage=usage)
        base = tuple(f for f in result.flags if f.startswith((LLM_WEB_SEARCH, SEARCH_VISITED)))
        if self._grounded is not None and usage.get("query_1"):
            before = self._grounded.stats()
            g = await self._grounded.lookup(inp, query=usage["query_1"], max_fetches=self.max_fetches)
            after = self._grounded.stats()
            usage.update(grounded_run=True, grounded_status=g.status, grounded_value=g.value,
                         **{f"grounded_{k}": after[k] - before[k] for k in (
                             "serp_queries", "serp_network_calls", "llm_reads", "llm_network_calls",
                             "page_attempts")})
            # SerpAPI bills each query sent (a replayed one is free). The
            # grounded reader's tokens are not reported by its client.
            sent = usage["grounded_serp_network_calls"]
            usage["cost_usd"] = round((usage.get("cost_usd") or 0) + self._call_record(
                usage, "grounded", {}, not sent, serpapi_queries=sent or usage["grounded_serp_queries"]), 5)
            if g.value:
                usage["engine"] = GROUNDED
                return replace(g, flags=(*g.flags, f"{ENGINE}:{GROUNDED}", *base), usage=usage)
        no_vat = usage.get("no_vat")
        if no_vat:
            return FoundResult(LLM_WEB, NO_VAT_STATED, source_url=no_vat["url"],
                               evidence=no_vat["quote"], flags=(*base, NO_VAT_STATEMENT), usage=usage)
        if self.aggregator_enabled and self.query_mode == HYBRID and usage.get("query_1"):
            found = await self._aggregator(inp, plan or prepare(inp.names), usage, base)
            if found is not None:
                return replace(found, usage=usage)
        public = plan.public if plan is not None else False
        if public and usage.get("impressum_read"):
            # A public body whose own Impressum was read and states no number.
            return FoundResult(LLM_WEB, NO_VAT_INFERRED, flags=(*base, PUBLIC_BODY), usage=usage)
        return replace(result, usage=usage)

    async def _aggregator(
        self, inp: LookupInput, plan: NamePlan, usage: dict[str, Any], base: tuple[str, ...],
    ) -> FoundResult | None:
        """Rule 17, the last step for an entity still NOT_FOUND after its own
        site, the links, the domain hint and Peppol: one search on
        unternehmensverzeichnis.org (the one directory that shows the
        USt-IdNr.; northdata, companyhouse and firmenwissen stay pointers),
        at most 2 entries fetched. Only an entry whose heading the matcher
        accepts for this entity is read (EXACT or PARTIAL; the form compared
        only when both state one); other companies' entries are never
        scanned, except that the first entry in the record's town is checked
        for the record's name among its former names (a successor:
        NAME_PARTIAL + SUCCESSOR_CANDIDATE). The entry's own
        "Umsatzsteuer-ID-Nr." is the candidate: class C, SECONDARY_SOURCE,
        MANUAL_REVIEW, never AUTO; never shared to the entity's other records
        (vat.lookup.run_lookups)."""
        from vat.entity_match import (
            ISSUE_NAME_PARTIAL, NAME_MISMATCH as _MISMATCH, NAME_PARTIAL as _PARTIAL, SECONDARY_SOURCE,
            SUCCESSOR_CANDIDATE as _SUCCESSOR, entity_name_on_page, fold as _fold, name_match,
        )
        from vat.web_query import search_city

        q = f'"{plan.search_name}" {search_city(inp.city)} site:{AGGREGATOR_SITE}'
        usage.update(aggregator_query=q, aggregator_fetches=0, aggregator_entries=[])
        out = await self._search("agg", LLM_WEB_RUN_SYSTEM_PROMPT, LLM_WEB_RUN_USER_PROMPT_TEMPLATE.format(query=q),
                                 usage)
        if isinstance(out, FoundResult):
            usage["aggregator"] = "search failed"
            return None
        names = plan.match_names

        def heading(text: str | None) -> str:
            # "… GmbH - Frankfurt am Main 60528 | unternehmensverzeichnis.org" → "… GmbH"
            text = re.sub(r"\s*\|\s*unternehmensverzeichnis\.org\s*$", "", text or "", flags=re.I)
            return re.sub(r"\s+-\s+[^-]*\b\d{5}\b.*$", "", text).strip()

        def slug(url: str) -> str:
            return re.sub(r"-+\d{10,}$", "", urlparse(url).path.strip("/")).replace("-", " ")

        entries = []
        for r in self._results(out[1], out[0]):
            if host_of(r["url"]).endswith(AGGREGATOR_SITE) and re.search(r"-\d{10,}/?$", urlparse(r["url"]).path):
                h = heading(r.get("title")) or slug(r["url"])
                entries.append((r["url"], h, name_match(h, names).match))
        first_word = next(iter(distinctive_tokens(plan.entity)), None) or (_fold(plan.entity).split() or [""])[0]
        matching = [e for e in entries if e[2] == NAME_EXACT or (
            e[2] == _PARTIAL and first_word and first_word in _fold(e[1]))][:2]
        # A successor check only for an entry whose name differs from the
        # record's in its first word alone (LX / LG International).
        record_words = _fold(plan.entity).split()
        others = [] if matching else [e for e in entries if len(record_words) >= 3 and
                                      _fold(e[1]).split()[1:] == record_words[1:]][:1]
        log = usage["aggregator_entries"]
        log += [{"url": u, "heading": h, "match": m, "fetched": False} for u, h, m in entries]
        for url, h, m in [*matching, *others]:
            flags: set[str] = set()
            page = await self._pages._fetch(url, flags)
            usage["aggregator_fetches"] += 1
            item = next(x for x in log if x["url"] == url)
            item["fetched"] = True
            if page is None or not (page.ok and page.content):
                item["why"] = f"unreadable ({page.status if page is not None else ','.join(sorted(flags))})"
                continue
            text = _readable_text(page.content)
            title = heading(page.content.page_title) or h
            verdict = name_match(title, names)
            former = re.search(r"Ehemalige Firmennamen:\s*(.+?)(?=Ehemalige|DAS UNTERNEHMENSVERZEICHNIS|$)", text)
            successor = verdict.match != NAME_EXACT and bool(former) and bool(
                entity_name_on_page(former.group(1), names))
            item.update(heading=title, match=verdict.match, successor=successor)
            if verdict.match == _MISMATCH and not successor:
                item["why"] = "another company's entry: not scanned"
                continue
            number = re.search(r"Umsatzsteuer-ID-Nr\.?:\s*(DE[\s\d]{9,13})", text)
            if not number:
                item["why"] = "no USt-IdNr. on the entry"
                continue
            vat_id = "DE" + re.sub(r"\D", "", number.group(1))[:9]
            reading = Reading(vat_id=vat_id, attributed_to=title, evidence_quote=number.group(0).strip(),
                              other_vat_ids=(), published_address=None)
            checked = await self._pages.judge_page(url, reading, inp, flags)
            if checked is None or not checked[1]:
                item["why"] = "page check failed"
                continue
            found = checked[0]
            issues = [*found.issues, SECONDARY_SOURCE]
            if successor:
                found = replace(found, name_match=_PARTIAL)
                issues += [ISSUE_NAME_PARTIAL, _SUCCESSOR]
            item["why"] = f"candidate {vat_id}"
            usage.update(engine="aggregator", source_page=url, found_via="aggregator")
            return replace(found, mode=LLM_WEB, issues=tuple(dict.fromkeys(issues)),
                           flags=(*found.flags, AGGREGATOR_FALLBACK, f"{ENGINE}:aggregator", *base))
        return None
