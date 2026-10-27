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
     the tier is ``REVIEW``, whatever the gates say;
   * the page cannot be fetched here (403, 5xx, timeout, 404): a visited
     source keeps its number as ``FOUND_UNVERIFIED`` with
     ``SOURCE_UNREADABLE`` and tier ``REVIEW``; an unvisited one is dropped
     (``NOT_FOUND`` with ``SOURCE_UNREADABLE``) — nothing shows the number was
     on any page.

Statuses and tiers are the grounded mode's, so both modes are scored the same
way. Every row carries ``LLM_WEB_SEARCH`` and ``SEARCH_VISITED:<n>``.

The search and the answer are cached in ``vat_llm`` (``VatLLM.search_json``):
``CACHE_FROZEN`` replays them; a live run asks again and, unlike a chat
completion at temperature 0, may get a different answer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from llm.openai_client import LLMUnavailableFrozen
from vat.llm_cache import VatLLM
from vat.lookup import (
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
from vat.lookup_grounded import FROZEN_CACHE_MISS, LLM_UNAVAILABLE, GroundedLookup, Reading
from vat.normalize import assess_format
from vat.prompts import LLM_WEB_SYSTEM_PROMPT, LLM_WEB_USER_PROMPT_TEMPLATE
from vat.vies_client import VIESClient

#: Reasoning effort of the search call (module constant by design).
REASONING_EFFORT = "high"

# ── Flags (VAT_Found_Flags) ──────────────────────────────────────────────────
#: Every row of this mode.
LLM_WEB_SEARCH = "LLM_WEB_SEARCH"
#: Prefix: how many URLs the search visited.
SEARCH_VISITED = "SEARCH_VISITED"
#: The model found no number on any page.
LLM_WEB_NO_ANSWER = "LLM_WEB_NO_ANSWER"
#: The model gave a number but no page for it — dropped.
ANSWER_WITHOUT_SOURCE = "ANSWER_WITHOUT_SOURCE"
#: source_url is not one of the URLs the search visited.
SOURCE_NOT_VISITED = "SOURCE_NOT_VISITED"
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
    ) -> None:
        self._vies = vies
        self._llm = llm or VatLLM(vies.evidence_cache.namespace("vat_llm"))
        self._llm.write_failures = vies.write_failures
        # The grounded mode's page fetch, gates, VIES and tiers, so a number
        # found this way is held to exactly the same standard. It never
        # searches: only judge_page is used.
        self._pages = GroundedLookup(settings, vies, search_client=False, fetcher=fetcher,
                                     llm=self._llm)
        self._asks = 0

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
            "cache_write_failures": self._vies.write_failures.count,
        }

    async def aclose(self) -> None:
        await self._llm.aclose()

    @staticmethod
    def prompt(inp: LookupInput) -> str:
        names = list(inp.names) + [None] * (4 - len(inp.names))
        street = " ".join(p for p in (inp.street, inp.house_number) if p)
        return LLM_WEB_USER_PROMPT_TEMPLATE.format(
            name_1=names[0] or "", name_2=names[1] or "", name_3=names[2] or "",
            name_4=names[3] or "", street=street, postal_code=inp.postal_code or "",
            city=inp.city or "", country=inp.country or "",
        )

    async def lookup(self, inp: LookupInput) -> FoundResult:
        if not inp.name_1:
            return FoundResult(LLM_WEB, NOT_RUN, flags=(LLM_WEB_SEARCH, "NO_NAME"))
        self._asks += 1
        try:
            answer, visited = await self._llm.search_json(
                LLM_WEB_SYSTEM_PROMPT, self.prompt(inp), reasoning_effort=REASONING_EFFORT,
            )
        except LLMUnavailableFrozen:
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, FROZEN_CACHE_MISS))
        except (RuntimeError, ValueError):
            return FoundResult(LLM_WEB, LOOKUP_INCOMPLETE, flags=(LLM_WEB_SEARCH, LLM_UNAVAILABLE))

        base = (LLM_WEB_SEARCH, f"{SEARCH_VISITED}:{len(visited)}")
        reading = Reading(
            vat_id=_clean(answer.get("vat_id")),
            attributed_to=_clean(answer.get("attributed_to")),
            evidence_quote=_clean(answer.get("evidence_quote")),
        )
        source = _clean(answer.get("source_url"))
        if not reading.vat_id:
            return FoundResult(LLM_WEB, NOT_FOUND, flags=(*base, LLM_WEB_NO_ANSWER))
        if not source:
            return FoundResult(LLM_WEB, NOT_FOUND, flags=(*base, ANSWER_WITHOUT_SOURCE))

        visited_ok = was_visited(source, visited)
        page_flags: set[str] = set()
        checked = await self._pages.judge_page(source, reading, inp, page_flags)
        unavailable = tuple(sorted(page_flags))
        if checked is None:
            if not visited_ok:
                return FoundResult(LLM_WEB, NOT_FOUND, source_url=source,
                                   flags=(*base, SOURCE_UNREADABLE, SOURCE_NOT_VISITED, *unavailable))
            fmt = assess_format(reading.vat_id, inp.country)
            return FoundResult(
                LLM_WEB, FOUND_UNVERIFIED, value=fmt.normalized or reading.vat_id,
                source_url=source, attributed_to=reading.attributed_to,
                evidence=reading.evidence_quote, date=self._llm.last_fetched_at or today(),
                flags=(SOURCE_UNREADABLE, *base, *unavailable), tier=TIER_REVIEW,
            )
        judged, on_page = checked
        if not on_page:
            return FoundResult(LLM_WEB, NOT_FOUND, source_url=source,
                               flags=(NOT_ON_SOURCE_PAGE, judged.flags[0], *base))
        flags = judged.flags if visited_ok else (*judged.flags, SOURCE_NOT_VISITED)
        return FoundResult(
            LLM_WEB, judged.status, judged.value, judged.source_url, judged.attributed_to,
            judged.evidence, judged.date, flags=(*flags, *base),
            tier=judged.tier if visited_ok else TIER_REVIEW,
        )
