"""Mode A — GROUNDED missing-VAT lookup: search → Impressum → reader → gates → VIES.

For one in-scope customer (see ``vat/lookup.py``):

1. **Candidate pages.** SerpAPI ``"<Name 1> <City> Impressum"`` and
   ``"<Name 1> <City> Imprint"`` (``gl=DE``); the first
   :data:`MAX_RESULT_URLS` distinct result URLs, taken **alternately** from
   the two result lists so one noisy query cannot crowd out the other. A result that is not itself an
   Impressum is replaced by the first of :data:`FALLBACK_PATHS` on the same
   host that is. At most :data:`MAX_FETCHES` page attempts per customer.
2. **Reader.** The LLM reads each Impressum's fetched text — a reader, never a
   source (``vat/prompts.py``). Long pages are cut to the text around
   USt / VAT / Umsatzsteuer / Steuernummer (:func:`reader_text`).
3. **Gates**, in order; the first failure makes the candidate
   ``FOUND_UNVERIFIED`` with the gate's name in ``VAT_Found_Flags``:

   a. ``QUOTE_NOT_IN_PAGE`` — the evidence quote is not in the page text
      (whitespace-normalised, otherwise verbatim);
   b. ``ID_NOT_IN_PAGE`` — the ID's characters are not in the page text
      (spaces / dots / dashes ignored) — the guard against a model that
      "completes" a number;
   c. ``FORMAT_ERROR`` — ``vat.normalize`` rejects it;
   d. **attribution** — the name the page attributes the ID to, against
      Name / Name + Name 2–4 (tokens from ``vat.name_match.name_tokens``).
      Strict, because for a German number it is the *only* name-to-number
      link (VIES discloses no name and ignores trader fields for DE):

      * same token set → passes (``ATTRIBUTION_MATCH``);
      * the SAP name is the page name followed by a site / department
        qualifier phrase (:data:`SITE_QUALIFIERS`; "Siemens AG Werk
        Erlangen" vs page "Siemens AG") → passes with
        ``ATTRIBUTION_QUALIFIER`` — the steward sees it is the parent
        entity's ID;
      * any other overlap — the page name has extra words ("Robert Bosch
        Hausgeräte GmbH" for "Robert Bosch GmbH"), the SAP name has
        non-qualifier extras ("Bosch Rexroth AG" vs "Robert Bosch GmbH"), or
        only a fuzzy resemblance → ``ATTRIBUTION_PARTIAL``;
      * no overlap → ``ATTRIBUTION_MISMATCH``.
   e. ``DIRECTORY_DOMAIN`` — the page is on a company-directory host
      (:data:`DIRECTORY_DOMAINS`), not the company's own site.

4. **VIES.** A candidate past every gate is checked with the existing client:
   ``FOUND_VERIFIED`` only on VALID, else ``FOUND_UNVERIFIED:VIES_<status>``.
   (For DE, VIES discloses no name and ignores trader fields — VALID proves the
   number exists, not whose it is. That is gate d's and the quote's job.)

Pages are taken one result URL at a time and the lookup stops only at a
verified ``ATTRIBUTION_MATCH``; anything less (a qualifier match, a partial,
a failed gate) keeps it going through the remaining result URLs within the
fetch cap. The best candidate wins (verified match > verified qualifier >
VIES-unverified > gate-failed) and every candidate read is listed in
``VAT_Found_Flags`` as ``CANDIDATE:<value>|<attributed_to>|<host>|<outcome>``.
No VAT ID on any page → ``NOT_FOUND``; nothing found while part of the lookup
could not run → ``LOOKUP_INCOMPLETE``. A page that answered 403 / 5xx / timed
out is part of the lookup that could not run (``PAGE_UNAVAILABLE:<status>``):
"blocked" is never reported as "not found".

Every external answer is cached in the VIES client's own evidence cache, so
``CACHE_FROZEN`` covers all of it: ``vat_serp`` (via ``utils.cache.cached_serp``),
``vat_pages`` (keyed on URL; only 2xx and 404/410 are recorded — a timeout or
a 5xx is never replayed to a live run) and ``vat_llm`` (``vat/llm_cache.py``).
Unavailable answers go to ``vat_pages_unavailable``, which **only a frozen
replay reads**: it lets ``CACHE_FROZEN`` reproduce ``PAGE_UNAVAILABLE`` exactly,
while a live run never reads it and always asks the site again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from enrichment.page_corroborator import _readable_text
from llm.openai_client import LLMUnavailableFrozen
from search.page_fetcher import PageFetcher, PageFetchResult
from search.serpapi_client import SerpAPIClient
from rapidfuzz import fuzz

from utils.cache import BatchCache, SerpCache, cached_serp, serp_disk_key
from vat.llm_cache import VatLLM
from vat.lookup import (
    FOUND_UNVERIFIED,
    FOUND_VERIFIED,
    GROUNDED,
    LOOKUP_INCOMPLETE,
    NOT_FOUND,
    NOT_RUN,
    FoundResult,
    LookupInput,
    today,
)
from vat.name_match import FUZZY_THRESHOLD, name_tokens
from vat.normalize import FORMAT_OK, assess_format
from vat.prompts import IMPRESSUM_READ_SYSTEM_PROMPT, IMPRESSUM_READ_USER_PROMPT_TEMPLATE
from vat.vies_client import VALID, VIESClient

# ── Limits (module constants by design; not settings) ────────────────────────
MAX_RESULT_URLS = 3
MAX_FETCHES = 6
SERP_RESULTS_PER_QUERY = 5
FALLBACK_PATHS = ("/impressum", "/imprint", "/legal-notice", "/de/impressum")
#: Characters kept per fetched page. Much larger than enrichment's page budget
#: (MAX_PAGE_CONTENT_CHARS): the USt-IdNr. sits near the END of an Impressum,
#: and PageFetcher truncates from the end.
PAGE_MAX_CHARS = 200_000
#: Characters of page text the reader is shown, and how they are chosen.
READER_TEXT_LIMIT = 12_000
READER_HEAD_CHARS = 2_000
READER_WINDOW = 1_500

#: Company-directory hosts. A VAT ID read there is a third party's copy, not
#: the company's own statement. Matched against the dot-separated labels of
#: the page's host (see :func:`is_directory_host`), so ``northdata`` covers
#: northdata.de and northdata.com, and ``branchenbuch`` covers
#: branchenbuch-deutschland.de.
DIRECTORY_DOMAINS: frozenset[str] = frozenset({
    "northdata", "firmenwissen", "wlw", "dnb", "companyhouse",
    "unternehmensregister", "implisense", "creditreform", "kompass",
    "yellowpages", "gelbeseiten",
    "dasoertliche", "11880", "cylex", "firmenabc", "meinestadt",
    "werliefertwas", "firmeneintrag", "branchenbuch",
})

#: Words that turn a company name into one of its sites or departments. The
#: SAP name "<page name> <qualifier> <rest>" is that company's site, and the
#: VAT ID is the company's (attribution ATTRIBUTION_QUALIFIER). Compared as
#: ``name_tokens`` (so "Fakultät" → "fakultaet", "Klinik für" → two tokens).
SITE_QUALIFIERS: tuple[str, ...] = (
    "Werk", "Standort", "Niederlassung", "Zweigniederlassung", "Filiale",
    "Betrieb", "Zentrale", "Institut", "Lehrstuhl", "Abteilung", "Fakultät",
    "Klinik für", "Department", "Plant", "Site", "Branch", "Division",
)
_QUALIFIER_SEQS: tuple[tuple[str, ...], ...] = tuple(
    tuple(name_tokens(q)) for q in SITE_QUALIFIERS
)

# ── Gate / reason flags (VAT_Found_Flags) ────────────────────────────────────
QUOTE_NOT_IN_PAGE = "QUOTE_NOT_IN_PAGE"
ID_NOT_IN_PAGE = "ID_NOT_IN_PAGE"
FORMAT_ERROR = "FORMAT_ERROR"
ATTRIBUTION_MATCH = "ATTRIBUTION_MATCH"
ATTRIBUTION_QUALIFIER = "ATTRIBUTION_QUALIFIER"
ATTRIBUTION_PARTIAL = "ATTRIBUTION_PARTIAL"
ATTRIBUTION_MISMATCH = "ATTRIBUTION_MISMATCH"
DIRECTORY_DOMAIN = "DIRECTORY_DOMAIN"
NO_SERPAPI_KEY = "NO_SERPAPI_KEY"
NO_SEARCH_RESULTS = "NO_SEARCH_RESULTS"
NO_IMPRESSUM_PAGE = "NO_IMPRESSUM_PAGE"
NO_VAT_ID_ON_PAGE = "NO_VAT_ID_ON_PAGE"
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
FROZEN_CACHE_MISS = "FROZEN_CACHE_MISS"
#: Prefix: a page answered something other than 2xx / 404 / 410 (403, 5xx,
#: a timeout). ``PAGE_UNAVAILABLE:403``, ``PAGE_UNAVAILABLE:ReadTimeout``.
PAGE_UNAVAILABLE = "PAGE_UNAVAILABLE"

_IMPRESSUM_RE = re.compile(
    r"impressum|imprint|legal[\s_-]*notice|anbieterkennzeichnung|rechtliche[\s_-]*hinweise",
    re.IGNORECASE,
)
_VAT_KEYWORD_RE = re.compile(
    r"\bust\b|ust-?id|umsatzsteuer|steuer-?nummer|\bvat\b|\buid\b", re.IGNORECASE,
)
_PAGE_SEPARATORS = re.compile(r"[\s.\-/ ]+")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def is_directory_host(url: str) -> bool:
    """A host label equals a :data:`DIRECTORY_DOMAINS` entry, or — for entries
    of six characters or more — contains it (``branchenbuch-deutschland``)."""
    host = (urlparse(url).hostname or "").lower()
    for label in host.split("."):
        for entry in DIRECTORY_DOMAINS:
            if label == entry or (len(entry) >= 6 and entry in label):
                return True
    return False


def is_impressum(res: PageFetchResult) -> bool:
    c = res.content
    head = " ".join([
        urlparse(res.url).path, c.page_title if c else "", c.h1 if c else "",
        (c.body_text[:300] if c else ""),
    ])
    return bool(_IMPRESSUM_RE.search(head))


def reader_text(text: str, limit: int = READER_TEXT_LIMIT) -> str:
    """*text*, or — when longer than *limit* — its head plus a window around
    every USt / VAT / Umsatzsteuer / Steuernummer mention, in page order."""
    if len(text) <= limit:
        return text
    spans = [(0, READER_HEAD_CHARS)]
    for m in _VAT_KEYWORD_RE.finditer(text):
        spans.append((max(0, m.start() - READER_WINDOW), m.end() + READER_WINDOW))
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts, used = [], 0
    for start, end in merged:
        piece = text[start:end]
        if used + len(piece) > limit:
            piece = piece[: max(0, limit - used)]
        if piece:
            parts.append(piece)
            used += len(piece)
        if used >= limit:
            break
    return " … ".join(parts)


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def _id_body(vat_id: str) -> str:
    compact = re.sub(r"[^0-9A-Za-z]", "", vat_id).upper()
    return compact[2:] if compact[:2].isalpha() else compact


@dataclass(frozen=True)
class Reading:
    """What the reader returned for one page."""

    vat_id: str | None
    attributed_to: str | None
    evidence_quote: str | None
    other_vat_ids: tuple[str, ...] = ()


def parse_reading(raw: Any) -> Reading:
    raw = raw if isinstance(raw, dict) else {}

    def clean(v: Any) -> str | None:
        text = str(v).strip() if v is not None else ""
        return text if text and text.lower() not in ("null", "none") else None

    others = raw.get("other_vat_ids_on_page") or []
    if not isinstance(others, list):
        others = [others]
    return Reading(
        vat_id=clean(raw.get("vat_id")),
        attributed_to=clean(raw.get("attributed_to")),
        evidence_quote=clean(raw.get("evidence_quote")),
        other_vat_ids=tuple(o for o in (clean(x) for x in others) if o),
    )


_ATTR_RANK = {ATTRIBUTION_MATCH: 0, ATTRIBUTION_QUALIFIER: 1,
              ATTRIBUTION_PARTIAL: 2, ATTRIBUTION_MISMATCH: 3}


def _attribution_one(page: list[str], sap: list[str]) -> str:
    if set(page) == set(sap):
        return ATTRIBUTION_MATCH
    for q in range(1, len(sap)):
        if any(seq and tuple(sap[q:q + len(seq)]) == seq for seq in _QUALIFIER_SEQS):
            if set(sap[:q]) == set(page):
                return ATTRIBUTION_QUALIFIER
            break  # only the first qualifier starts the site phrase
    overlap = set(page) & set(sap)
    if overlap or fuzz.token_sort_ratio(" ".join(page), " ".join(sap)) >= FUZZY_THRESHOLD:
        return ATTRIBUTION_PARTIAL
    return ATTRIBUTION_MISMATCH


def attribution(attributed_to: str | None, names: tuple[str | None, ...]) -> str:
    """Gate d's verdict for the page's *attributed_to* against Name alone and
    Name + Name 2–4 joined; the better of the two."""
    page = name_tokens(attributed_to) if attributed_to else []
    if not page:
        return ATTRIBUTION_MISMATCH
    parts = [str(n) for n in names if n and str(n).strip()]
    candidates = []
    if names and names[0]:
        candidates.append(name_tokens(names[0]))
    if len(parts) > 1 or not candidates:
        candidates.append(name_tokens(" ".join(parts)))
    verdicts = [_attribution_one(page, sap) for sap in candidates if sap]
    return min(verdicts, key=_ATTR_RANK.__getitem__) if verdicts else ATTRIBUTION_MISMATCH


def run_gates(reading: Reading, page_text: str, page_url: str, inp: LookupInput):
    """Gates a–e in order. Returns ``(failed_gate | None, VatFormat | None,
    extra_flags)``; a passing attribution's flag (MATCH / QUALIFIER) is in
    *extra_flags*."""
    extra: list[str] = []
    vat_id = reading.vat_id or ""
    if not reading.evidence_quote or _ws(reading.evidence_quote) not in _ws(page_text):
        return QUOTE_NOT_IN_PAGE, None, extra
    body = _id_body(vat_id)
    if not body or body not in _PAGE_SEPARATORS.sub("", page_text).upper():
        return ID_NOT_IN_PAGE, None, extra
    fmt = assess_format(vat_id, inp.country)
    if fmt.status != FORMAT_OK:
        if fmt.format_error:
            extra.append(f"FORMAT_ERROR:{fmt.format_error}")
        return FORMAT_ERROR, fmt, extra
    verdict = attribution(reading.attributed_to, inp.names)
    if verdict in (ATTRIBUTION_PARTIAL, ATTRIBUTION_MISMATCH):
        return verdict, fmt, extra
    extra.append(verdict)
    if is_directory_host(page_url):
        return DIRECTORY_DOMAIN, fmt, extra
    return None, fmt, extra


# ---------------------------------------------------------------------------
# The lookup
# ---------------------------------------------------------------------------

@dataclass
class _Stats:
    serp_queries: int = 0
    serp_network_calls: int = 0
    page_attempts: int = 0
    pages_fetched: int = 0          # network
    page_cache_hits: int = 0
    llm_reads: int = 0
    vies_checks: int = 0


class _Budget:
    """One customer's page-fetch budget. Per call, so customers looked up at
    the same time cannot spend each other's."""

    def __init__(self) -> None:
        self.used = 0

    @property
    def left(self) -> int:
        return MAX_FETCHES - self.used


def _rank(result: FoundResult) -> int:
    """0 verified match (stop) · 1 verified qualifier · 2 gates passed but VIES
    not VALID · 3 a gate failed."""
    if result.status == FOUND_VERIFIED:
        return 0 if ATTRIBUTION_MATCH in result.flags else 1
    if result.status.startswith(FOUND_UNVERIFIED + ":"):
        return 2
    return 3


class GroundedLookup:
    """Mode A. Clients are injectable; defaults are the repo's own."""

    mode = GROUNDED

    def __init__(
        self,
        settings: Any,
        vies: VIESClient,
        *,
        search_client: Any = None,
        fetcher: Any = None,
        llm: VatLLM | None = None,
    ) -> None:
        cache = vies.evidence_cache
        self._vies = vies
        if search_client is None:
            key = (getattr(settings, "serpapi_key", "") or "").strip()
            search_client = SerpAPIClient(key) if key else None
        # Mode A requires SerpAPI. No DuckDuckGo fallback: an experiment must
        # not change search engine depending on which key happens to be set.
        self._search = search_client
        self._serp_disk = cache.namespace("vat_serp")
        self._serp = BatchCache(SerpCache(self._serp_disk))
        self._pages = cache.namespace("vat_pages")
        # Read only by a frozen replay; see the module docstring.
        self._unavailable = cache.namespace("vat_pages_unavailable")
        self._fetcher = fetcher or PageFetcher(
            timeout=getattr(settings, "page_fetch_timeout_seconds", 10),
            max_chars=PAGE_MAX_CHARS, store=None,
        )
        self._llm = llm or VatLLM(cache.namespace("vat_llm"))
        # One counter for every write into this cache, injected reader or not.
        self._llm.write_failures = vies.write_failures
        self._stats = _Stats()

    def stats(self) -> dict[str, int]:
        s = self._stats
        return {
            "serp_queries": s.serp_queries,
            "serp_network_calls": s.serp_network_calls,
            "page_attempts": s.page_attempts,
            "pages_fetched": s.pages_fetched,
            "page_cache_hits": s.page_cache_hits,
            "llm_reads": s.llm_reads,
            "llm_network_calls": self._llm.network_calls,
            "llm_cache_hits": self._llm.cache_hits,
            "vies_checks": s.vies_checks,
            "cache_write_failures": self._vies.write_failures.count,
        }

    async def aclose(self) -> None:
        await self._llm.aclose()

    # -- external answers, cached -------------------------------------------

    async def _search_urls(self, inp: LookupInput) -> list[str]:
        per_query: list[list[str]] = []
        base = " ".join(p for p in (inp.name_1, inp.city) if p)
        for suffix in ("Impressum", "Imprint"):
            query = f"{base} {suffix}"
            self._stats.serp_queries += 1
            if self._serp.get_serp(query, "DE") is None and not self._serp.serp_frozen:
                self._stats.serp_network_calls += 1
            was_recorded = self._serp.get_serp(query, "DE") is not None
            results = await cached_serp(
                self._serp, self._search, query,
                num_results=SERP_RESULTS_PER_QUERY, country="DE",
            )
            if results and not was_recorded:
                self._vies.write_failures.check(self._serp_disk, serp_disk_key(query, "DE"))
            per_query.append([
                u for u in (
                    getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
                    for r in results or []
                ) if u
            ])
        # Alternate: Impressum #1, Imprint #1, Impressum #2, … — so a query
        # that returned noise cannot fill all the slots on its own.
        urls: list[str] = []
        for rank in range(max((len(q) for q in per_query), default=0)):
            for q in per_query:
                if rank < len(q) and q[rank] not in urls:
                    urls.append(q[rank])
        return urls[:MAX_RESULT_URLS]

    async def _fetch(
        self, url: str, flags: set[str], budget: "_Budget | None" = None,
    ) -> PageFetchResult | None:
        """One page attempt through ``vat_pages``. None when nothing usable."""
        self._stats.page_attempts += 1
        if budget is not None:
            budget.used += 1
        recorded = self._pages.get(url)
        if isinstance(recorded, dict):
            self._stats.page_cache_hits += 1
            return PageFetchResult(
                url=recorded.get("url") or url, status=recorded.get("status"),
                content=PageFetcher._content_from_json(recorded.get("content")),
            )
        if self._pages.replay_only:
            seen = self._unavailable.get(url)
            if isinstance(seen, dict):
                flags.add(f"{PAGE_UNAVAILABLE}:{seen.get('reason')}")
            else:
                flags.add(FROZEN_CACHE_MISS)
            return None
        self._stats.pages_fetched += 1
        res = await self._fetcher.fetch_page_result(url)
        status = res.status or 0
        if 200 <= status < 300 or status in (404, 410):
            # Definitive answers only. A timeout / 5xx / transport error is
            # not recorded, so the next live run asks again.
            self._vies.write_failures.set_checked(self._pages, url, {
                "url": res.url, "status": res.status,
                "content": PageFetcher._content_to_json(res.content),
            })
            return res
        reason = str(res.status) if res.status else (res.error or "unknown")
        flags.add(f"{PAGE_UNAVAILABLE}:{reason}")
        self._vies.write_failures.set_checked(self._unavailable, url, {
            "url": url, "reason": reason,
        })
        return None

    async def _impressum_for(
        self, url: str, flags: set[str], budget: _Budget,
    ) -> PageFetchResult | None:
        """The Impressum for one result URL: the URL itself, else the first
        fallback path on its host that is one. Directory hosts get no
        fallback probes (their Impressum is the directory's own). None when
        none is found within the fetch cap."""
        host = (urlparse(url).hostname or "").lower()
        tries = [url]
        if not is_directory_host(url):
            scheme = urlparse(url).scheme or "https"
            tries += [f"{scheme}://{host}{path}" for path in FALLBACK_PATHS]
            tries = list(dict.fromkeys(tries))  # the result URL may itself be /impressum
        for candidate in tries:
            if budget.left <= 0:
                return None
            res = await self._fetch(candidate, flags, budget)
            if res is not None and res.ok and res.content and is_impressum(res):
                return res
        return None

    async def _read(self, page: PageFetchResult, text: str) -> Reading:
        self._stats.llm_reads += 1
        prompt = IMPRESSUM_READ_USER_PROMPT_TEMPLATE.format(
            url=page.url, title=page.content.page_title if page.content else "",
            text=reader_text(text),
        )
        return parse_reading(await self._llm.extract_json(
            IMPRESSUM_READ_SYSTEM_PROMPT, prompt, max_tokens=600,
        ))

    # -- one customer ---------------------------------------------------------

    async def lookup(self, inp: LookupInput) -> FoundResult:
        if self._search is None:
            return FoundResult(GROUNDED, NOT_RUN, flags=(NO_SERPAPI_KEY,))
        if not inp.name_1:
            return FoundResult(GROUNDED, NOT_RUN, flags=("NO_NAME",))

        budget = _Budget()
        flags: set[str] = set()
        urls = await self._search_urls(inp)
        if not urls:
            status = LOOKUP_INCOMPLETE if self._serp.serp_frozen else NOT_FOUND
            reason = FROZEN_CACHE_MISS if self._serp.serp_frozen else NO_SEARCH_RESULTS
            return FoundResult(GROUNDED, status, flags=(reason,))

        best: FoundResult | None = None
        candidates: list[str] = []
        impressum_seen = False
        hosts_done: set[str] = set()
        for url in urls:
            host = (urlparse(url).hostname or "").lower()
            if host in hosts_done:
                continue
            if budget.left <= 0:
                break
            page = await self._impressum_for(url, flags, budget)
            if page is None:
                continue
            hosts_done.add(host)
            impressum_seen = True
            text = _readable_text(page.content)
            try:
                reading = await self._read(page, text)
            except LLMUnavailableFrozen:
                flags.add(FROZEN_CACHE_MISS)
                continue
            except (RuntimeError, ValueError):
                flags.add(LLM_UNAVAILABLE)
                continue
            if not reading.vat_id:
                continue
            result = await self._judge(page, text, reading, inp)
            outcome = result.status
            if result.status == FOUND_UNVERIFIED:
                outcome = f"{FOUND_UNVERIFIED}:{result.flags[0]}"
            candidates.append(
                f"CANDIDATE:{result.value}|{reading.attributed_to}|{host}|{outcome}"
            )
            if best is None or _rank(result) < _rank(best):
                best = result
            if _rank(best) == 0:
                break  # a verified exact attribution: nothing can beat it

        unavailable = sorted(f for f in flags if f.startswith(PAGE_UNAVAILABLE + ":"))
        if best is not None:
            return FoundResult(
                best.mode, best.status, best.value, best.source_url,
                best.attributed_to, best.evidence, best.date,
                flags=(*best.flags, *candidates, *unavailable),
            )
        if unavailable or flags & {FROZEN_CACHE_MISS, LLM_UNAVAILABLE}:
            # Part of the lookup could not run — "blocked" is not "not found".
            return FoundResult(GROUNDED, LOOKUP_INCOMPLETE, flags=tuple(sorted(flags)))
        if not impressum_seen:
            return FoundResult(GROUNDED, NOT_FOUND, flags=(NO_IMPRESSUM_PAGE,))
        return FoundResult(GROUNDED, NOT_FOUND, flags=(NO_VAT_ID_ON_PAGE,))

    async def _judge(
        self, page: PageFetchResult, text: str, reading: Reading, inp: LookupInput,
    ) -> FoundResult:
        failed, fmt, extra = run_gates(reading, text, page.url, inp)
        flags = list(extra)
        if reading.other_vat_ids:
            flags.append("OTHER_VAT_IDS_ON_PAGE:" + ",".join(reading.other_vat_ids))
        date = self._pages.fetched_at(page.url) or today()
        common = dict(
            value=fmt.normalized if fmt else reading.vat_id,
            source_url=page.url,
            attributed_to=reading.attributed_to,
            evidence=reading.evidence_quote,
            date=date,
        )
        if failed:
            return FoundResult(GROUNDED, FOUND_UNVERIFIED, flags=(failed, *flags), **common)

        self._stats.vies_checks += 1
        vies = await self._vies.check(fmt)
        if vies.error_code:
            flags.append(f"VIES:{vies.error_code}")
        status = FOUND_VERIFIED if vies.status == VALID else f"{FOUND_UNVERIFIED}:VIES_{vies.status}"
        return FoundResult(GROUNDED, status, flags=tuple(flags), **common)
