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
      * no overlap → ``ATTRIBUTION_MISMATCH``;
      * a MATCH or QUALIFIER whose legal form differs from the record's
        ("Example GmbH" vs "Example AG", "… GmbH" vs "… GmbH & Co. KG") is
        PARTIAL, flagged ``LEGAL_FORM_DIFFERS:<record>/<page>``: another
        legal form is another legal entity (``vat/entity_match.py``).
   e. ``DIRECTORY_DOMAIN`` — the page is on a company-directory host
      (:data:`DIRECTORY_DOMAINS`), not the company's own site.
   f. **address** — run on every candidate read, gate failure or not, and
      recorded as a flag; it never changes the status. The page contains the
      SAP Postal Code, or the SAP City (umlaut-folded, case-insensitive), or
      the City's main token in an address block (``<5-digit PLZ> <Town>``) →
      ``ADDRESS_MATCH``; the page has an address block but none of these →
      ``ADDRESS_MISMATCH``; no address block → ``ADDRESS_UNKNOWN``. The guard
      against a different company of the same name ("ACL GmbH" Markkleeberg
      for "ACL GmbH" Rottenburg).

4. **VIES.** A candidate past every gate is checked with the existing client:
   ``FOUND_VERIFIED`` only on VALID, else ``FOUND_UNVERIFIED:VIES_<status>``.
   (For DE, VIES discloses no name and ignores trader fields — VALID proves the
   number exists, not whose it is. That is gate d's and the quote's job.)

5. **Tier** (``VAT_Found_Tier``; ``VAT_Grounded_Tier`` side by side):

   * ``PROPOSED`` — attribution MATCH or QUALIFIER and ``ADDRESS_MATCH``;
   * ``LIKELY`` — PARTIAL and ``ADDRESS_MATCH``, or MATCH / QUALIFIER and
     ``ADDRESS_UNKNOWN``;
   * ``REVIEW`` — every other candidate: MISMATCH, ``ADDRESS_MISMATCH``,
     PARTIAL with ``ADDRESS_UNKNOWN``, a failed gate a–c or e, or VIES INVALID.

Pages are taken one result URL at a time and the lookup stops at the first
``PROPOSED`` candidate; anything less keeps it going through the remaining
result URLs within the fetch cap. The best candidate wins: highest tier, then
``ADDRESS_MATCH`` > ``ADDRESS_UNKNOWN`` > ``ADDRESS_MISMATCH``, then MATCH >
QUALIFIER > PARTIAL > MISMATCH, then verified > VIES-unverified > gate-failed.
When the winning tier is PROPOSED or LIKELY and another candidate in that tier
has a different number, the result drops to ``REVIEW`` with
``AMBIGUOUS_SAME_NAME:<id>,<id>…`` (two companies of the same name). Every
candidate read is listed in ``VAT_Found_Flags`` as
``CANDIDATE:<value>|<attributed_to>|<host>|<outcome>|<tier>``.
When another candidate names the exact entity on a primary source, at an
address that is not someone else's, and states a different number, the
result carries ``CONFLICTING_SOURCES:<id>,<id>…`` (decision ``CONFLICT``).

6. **Evidence** for the decision and the audit trail (design paper §5, §10;
   ``vat/entity_match.py``): source type (§4 hierarchy), page title, the
   published address, name and address match levels, domain and register
   number checks, the weighted match score, and VIES's answer and its date.
   The decision itself is :func:`vat.lookup.decide`.

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
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

from enrichment.page_corroborator import _readable_text
from llm.openai_client import LLMUnavailableFrozen
from search.page_fetcher import REMOVE_TAGS, PageFetcher, PageFetchResult
from search.serpapi_client import SerpAPIClient
from rapidfuzz import fuzz

from config import llm_model
from utils.cache import BatchCache, SerpCache, cached_serp, serp_disk_key
from vat.entity_match import (
    ADDRESS_BLOCK_RE as _ADDRESS_BLOCK_RE,
    DOMAIN_MATCH,
    DOMAIN_MISMATCH,
    LEGAL_FORM_DIFFERS,
    NAME_BRANCH,
    NAME_EXACT,
    NAME_MISMATCH,
    NAME_PARTIAL,
    PRIMARY_SOURCES,
    REGISTER_MATCH,
    REGISTER_MISMATCH,
    address_match,
    city_on_page,
    company_domain,
    domain_match,
    is_register_host,
    legal_forms_differ,
    match_score,
    postal_code_on_page,
    register_match,
    source_type,
    vat_candidates,
)
from vat.llm_cache import VatLLM
from vat.lookup import (
    AMBIGUOUS_SAME_NAME,
    CONFLICTING_SOURCES,
    FOUND_UNVERIFIED,
    FOUND_VERIFIED,
    GROUNDED,
    LOOKUP_INCOMPLETE,
    NOT_FOUND,
    NOT_RUN,
    TIER_LIKELY,
    TIER_PROPOSED,
    TIER_REVIEW,
    FoundResult,
    LookupInput,
    today,
)
from vat.name_match import FUZZY_THRESHOLD, name_tokens
from vat.normalize import FORMAT_OK, assess_format
from vat.prompts import (
    IMPRESSUM_READ_PROMPT_VERSION,
    IMPRESSUM_READ_SYSTEM_PROMPT,
    IMPRESSUM_READ_USER_PROMPT_TEMPLATE,
)
from vat.vies_client import NOT_CHECKED, VALID, VIESClient

# ── Limits (module constants by design; not settings) ────────────────────────
MAX_RESULT_URLS = 3
MAX_FETCHES = 6
SERP_RESULTS_PER_QUERY = 5
FALLBACK_PATHS = ("/impressum", "/imprint", "/legal-notice", "/de/impressum")
#: Characters kept per fetched page. Much larger than enrichment's page budget
#: (MAX_PAGE_CONTENT_CHARS): the USt-IdNr. sits near the END of an Impressum,
#: and PageFetcher truncates from the end.
PAGE_MAX_CHARS = 200_000
#: The page fetcher's REMOVE_TAGS without ``<header>``: WordPress themes
#: (Hello Elementor, molit.eu) wrap the whole Impressum in
#: ``<header class="entry-header">``, and deleting it deleted the USt-IdNr.
#: VAT only — enrichment keeps the default, so its LLM inputs are unchanged.
VAT_REMOVE_TAGS = frozenset(REMOVE_TAGS - {"header"})
#: Characters of page text the reader is shown, and how they are chosen.
READER_TEXT_LIMIT = 12_000
READER_HEAD_CHARS = 2_000
READER_WINDOW = 1_500

#: Company-directory hosts (the design paper's tier 4, secondary sources). A
#: VAT ID read there is a third party's copy, not the company's own
#: statement. Matched against the dot-separated labels of the page's host
#: (see :func:`is_directory_host`), so ``northdata`` covers northdata.de and
#: northdata.com, and ``branchenbuch`` covers branchenbuch-deutschland.de.
#: Official registers (Unternehmensregister, Handelsregister …) are tier 2,
#: not directories: ``vat.entity_match.REGISTER_DOMAINS``.
DIRECTORY_DOMAINS: frozenset[str] = frozenset({
    "northdata", "firmenwissen", "wlw", "dnb", "companyhouse",
    "implisense", "creditreform", "kompass",
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
ADDRESS_MATCH = "ADDRESS_MATCH"
ADDRESS_MISMATCH = "ADDRESS_MISMATCH"
ADDRESS_UNKNOWN = "ADDRESS_UNKNOWN"
#: Prefix: VAT-ID-shaped strings on the page (the design paper's pattern)
#: other than the candidate.
OTHER_VAT_IDS_ON_PAGE = "OTHER_VAT_IDS_ON_PAGE"

#: Gate d's verdict → VAT_Found_Name_Match.
_NAME_MATCH = {
    ATTRIBUTION_MATCH: NAME_EXACT, ATTRIBUTION_QUALIFIER: NAME_BRANCH,
    ATTRIBUTION_PARTIAL: NAME_PARTIAL, ATTRIBUTION_MISMATCH: NAME_MISMATCH,
}

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


def id_on_page(vat_id: str | None, page_text: str) -> bool:
    """Gate b's test alone: the ID's characters are in the page text (spaces /
    dots / dashes ignored)."""
    body = _id_body(vat_id or "")
    return bool(body) and body in _PAGE_SEPARATORS.sub("", page_text or "").upper()


@dataclass(frozen=True)
class Reading:
    """What the reader returned for one page."""

    vat_id: str | None
    attributed_to: str | None
    evidence_quote: str | None
    other_vat_ids: tuple[str, ...] = ()
    published_address: str | None = None


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
        published_address=clean(raw.get("published_address")),
    )


def _compact(text: str | None) -> str:
    return re.sub(r"[\W_]+", "", (text or "").replace(" ", " ")).lower()


def address_on_page(address: str | None, page_text: str) -> str | None:
    """The reader's published address, kept only when its characters are on
    the page (punctuation and spacing ignored): an address the reader did
    not copy from the page is not evidence."""
    return address if address and _compact(address) in _compact(page_text) else None


_ATTR_RANK = {ATTRIBUTION_MATCH: 0, ATTRIBUTION_QUALIFIER: 1,
              ATTRIBUTION_PARTIAL: 2, ATTRIBUTION_MISMATCH: 3}
_TIER_RANK = {TIER_PROPOSED: 0, TIER_LIKELY: 1, TIER_REVIEW: 2}
_ADDRESS_RANK = {ADDRESS_MATCH: 0, ADDRESS_UNKNOWN: 1, ADDRESS_MISMATCH: 2}


def address_verdict(page_text: str, postal_code: str | None, city: str | None) -> str:
    """Gate f: ``ADDRESS_MATCH`` / ``ADDRESS_MISMATCH`` / ``ADDRESS_UNKNOWN``
    for the SAP Postal Code and City against one page's text. (The finer
    street-level match the decision reads is ``entity_match.address_match``.)"""
    text = page_text or ""
    if postal_code_on_page(text, postal_code) or city_on_page(text, city):
        return ADDRESS_MATCH
    return ADDRESS_MISMATCH if _ADDRESS_BLOCK_RE.search(text) else ADDRESS_UNKNOWN


def candidate_tier(flags: tuple[str, ...], status: str, page_url: str) -> str:
    """A candidate's tier, from its flags (attribution + address verdicts) and
    status. A failed gate a–c, a directory host or VIES INVALID is REVIEW."""
    if status.endswith(":VIES_INVALID") or is_directory_host(page_url):
        return TIER_REVIEW
    attr = next((f for f in flags if f in _ATTR_RANK), None)
    address = next((f for f in flags if f in _ADDRESS_RANK), ADDRESS_UNKNOWN)
    if attr is None or (status == FOUND_UNVERIFIED and flags[0] not in _ATTR_RANK):
        return TIER_REVIEW  # gate a–c (or e) failed before or after attribution
    strong = attr in (ATTRIBUTION_MATCH, ATTRIBUTION_QUALIFIER)
    if strong and address == ADDRESS_MATCH:
        return TIER_PROPOSED
    if (attr == ATTRIBUTION_PARTIAL and address == ADDRESS_MATCH) or (
            strong and address == ADDRESS_UNKNOWN):
        return TIER_LIKELY
    return TIER_REVIEW


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


def legal_form_conflict(attributed_to: str | None, names: tuple[str | None, ...]) -> str | None:
    """``LEGAL_FORM_DIFFERS:<record>/<page>`` when the page's legal form is
    not the record's (Name + Name 2–4 joined: the form is often in Name 2)."""
    joined = " ".join(str(n) for n in names if n and str(n).strip())
    differs = legal_forms_differ(attributed_to, joined)
    return f"{LEGAL_FORM_DIFFERS}:{differs[0]}/{differs[1]}" if differs else None


def attribution(attributed_to: str | None, names: tuple[str | None, ...]) -> str:
    """Gate d's verdict for the page's *attributed_to* against Name alone and
    Name + Name 2–4 joined; the better of the two. A MATCH / QUALIFIER with
    another legal form is PARTIAL."""
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
    verdict = min(verdicts, key=_ATTR_RANK.__getitem__) if verdicts else ATTRIBUTION_MISMATCH
    if verdict in (ATTRIBUTION_MATCH, ATTRIBUTION_QUALIFIER) and legal_form_conflict(
            attributed_to, names):
        return ATTRIBUTION_PARTIAL
    return verdict


def run_gates(reading: Reading, page_text: str, page_url: str, inp: LookupInput):
    """Gates a–e in order. Returns ``(failed_gate | None, VatFormat | None,
    extra_flags)``; a passing attribution's flag (MATCH / QUALIFIER) is in
    *extra_flags*."""
    extra: list[str] = []
    vat_id = reading.vat_id or ""
    if not reading.evidence_quote or _ws(reading.evidence_quote) not in _ws(page_text):
        return QUOTE_NOT_IN_PAGE, None, extra
    if not id_on_page(vat_id, page_text):
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
    """0 verified match · 1 verified qualifier · 2 gates passed but VIES not
    VALID · 3 a gate failed."""
    if result.status == FOUND_VERIFIED:
        return 0 if ATTRIBUTION_MATCH in result.flags else 1
    if result.status.startswith(FOUND_UNVERIFIED + ":"):
        return 2
    return 3


def _choice_key(result: FoundResult) -> tuple[int, int, int, int]:
    """Lower is better: tier, address, attribution, then :func:`_rank`."""
    address = next((f for f in result.flags if f in _ADDRESS_RANK), ADDRESS_UNKNOWN)
    attr = next((f for f in result.flags if f in _ATTR_RANK), None)
    return (_TIER_RANK[result.tier or TIER_REVIEW], _ADDRESS_RANK[address],
            _ATTR_RANK.get(attr, len(_ATTR_RANK)), _rank(result))


def conflicting_sources(best: FoundResult, judged: list[FoundResult]) -> tuple[str, ...]:
    """``(CONFLICTING_SOURCES:<ids>,)`` when another candidate that passed
    gates a–c, on a primary source, names this exact entity (or a site of
    it) at an address that is not someone else's, and states a different
    number: the design paper's CONFLICT ("trusted sources show different
    numbers"). A same-name company in another town is not a conflict; the
    address already tells them apart."""
    values = {
        r.value for r in judged
        if r.value and r.value != best.value and r.status != FOUND_UNVERIFIED
        and r.name_match in (NAME_EXACT, NAME_BRANCH) and r.source_type in PRIMARY_SOURCES
        and ADDRESS_MISMATCH not in r.flags
    }
    if not values:
        return ()
    return (f"{CONFLICTING_SOURCES}:{','.join(sorted(values | {best.value}))}",)


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
            max_chars=PAGE_MAX_CHARS, store=None, remove_tags=VAT_REMOVE_TAGS,
        )
        self._llm = llm or VatLLM(cache.namespace("vat_llm"))
        # One counter for every write into this cache, injected reader or not.
        self._llm.write_failures = vies.write_failures
        self._stats = _Stats()

    @property
    def agent_version(self) -> str:
        return f"{GROUNDED}|{IMPRESSUM_READ_PROMPT_VERSION}|{llm_model()}"

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
        fallback path on its host that is one. Directory and register hosts
        get no fallback probes (their Impressum is their own). None when
        none is found within the fetch cap."""
        host = (urlparse(url).hostname or "").lower()
        tries = [url]
        if not (is_directory_host(url) or is_register_host(url)):
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
        judged: list[FoundResult] = []
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
                f"CANDIDATE:{result.value}|{reading.attributed_to}|{host}|{outcome}|{result.tier}"
            )
            judged.append(result)
            if best is None or _choice_key(result) < _choice_key(best):
                best = result
            if best.tier == TIER_PROPOSED:
                break  # name and address both tie it to this customer

        unavailable = sorted(f for f in flags if f.startswith(PAGE_UNAVAILABLE + ":"))
        if best is not None:
            tier, ambiguous = best.tier, ()
            if tier in (TIER_PROPOSED, TIER_LIKELY):
                values = sorted({r.value for r in judged if r.tier == tier and r.value})
                if len(values) > 1:
                    tier = TIER_REVIEW
                    ambiguous = (f"{AMBIGUOUS_SAME_NAME}:{','.join(values)}",)
            if not ambiguous:
                ambiguous = conflicting_sources(best, judged)
            return replace(best, tier=tier,
                           flags=(*best.flags, *ambiguous, *candidates, *unavailable))
        if unavailable or flags & {FROZEN_CACHE_MISS, LLM_UNAVAILABLE}:
            # Part of the lookup could not run — "blocked" is not "not found".
            return FoundResult(GROUNDED, LOOKUP_INCOMPLETE, flags=tuple(sorted(flags)))
        if not impressum_seen:
            return FoundResult(GROUNDED, NOT_FOUND, flags=(NO_IMPRESSUM_PAGE,))
        return FoundResult(GROUNDED, NOT_FOUND, flags=(NO_VAT_ID_ON_PAGE,))

    async def judge_page(
        self, url: str, reading: Reading, inp: LookupInput, flags: set[str],
    ) -> tuple[FoundResult, bool] | None:
        """Gates a–f, VIES and the tier for a *reading* someone else made of the
        page at *url*, checked against that page as fetched HERE (``vat_pages``,
        no fallback paths), and whether the ID's digits are on it. None when
        the page cannot be read: a 403 / 5xx / timeout adds
        ``PAGE_UNAVAILABLE:<status>`` to *flags*, a 404 / 410
        ``PAGE_UNAVAILABLE:404``. Used by ``vat/lookup_web.py``."""
        page = await self._fetch(url, flags)
        if page is None:
            return None
        if not (page.ok and page.content):
            flags.add(f"{PAGE_UNAVAILABLE}:{page.status}")
            return None
        text = _readable_text(page.content)
        return await self._judge(page, text, reading, inp), id_on_page(reading.vat_id, text)

    async def _judge(
        self, page: PageFetchResult, text: str, reading: Reading, inp: LookupInput,
    ) -> FoundResult:
        failed, fmt, extra = run_gates(reading, text, page.url, inp)
        flags = list(extra)
        value = fmt.normalized if fmt else reading.vat_id
        others = list(reading.other_vat_ids)
        known = {_compact(o) for o in others} | {_compact(value)}
        others += [c for c in vat_candidates(text) if _compact(c) not in known]
        if others:
            flags.append(f"{OTHER_VAT_IDS_ON_PAGE}:" + ",".join(others))
        date = self._pages.fetched_at(page.url) or today()
        common = dict(
            value=value,
            source_url=page.url,
            attributed_to=reading.attributed_to,
            evidence=reading.evidence_quote,
            date=date,
            **self._evidence(page, text, reading, inp, flags),
        )
        flags.append(address_verdict(text, inp.postal_code, inp.city))
        if failed:
            flags = [failed, *flags]
            return FoundResult(GROUNDED, FOUND_UNVERIFIED, flags=tuple(flags),
                               tier=candidate_tier(tuple(flags), FOUND_UNVERIFIED, page.url),
                               validation_status=NOT_CHECKED, **common)

        if self._vies.enabled:
            self._stats.vies_checks += 1
        vies = await self._vies.check(fmt)  # NOT_CHECKED when VIES is off
        if vies.error_code:
            flags.append(f"VIES:{vies.error_code}")
        status = FOUND_VERIFIED if vies.status == VALID else f"{FOUND_UNVERIFIED}:VIES_{vies.status}"
        return FoundResult(GROUNDED, status, flags=tuple(flags),
                           tier=candidate_tier(tuple(flags), status, page.url),
                           validation_status=vies.status, validation_date=vies.request_date,
                           **common)

    @staticmethod
    def _evidence(
        page: PageFetchResult, text: str, reading: Reading, inp: LookupInput, flags: list[str],
    ) -> dict[str, Any]:
        """The design paper's entity-resolution evidence (§5) and audit
        fields (§10) for one candidate. Appends the domain, register and
        legal-form flags to *flags*; decides nothing."""
        name = _NAME_MATCH[attribution(reading.attributed_to, inp.names)]
        legal = legal_form_conflict(reading.attributed_to, inp.names)
        if legal:
            flags.append(legal)
        address, elements = address_match(
            text, inp.street, inp.house_number, inp.postal_code, inp.city)
        domain = company_domain(inp.website, inp.email)
        elements["domain"] = domain_match(page.url, domain)
        elements["register"] = register_match(text, inp.register_number)
        if elements["domain"] is not None:
            flags.append(DOMAIN_MATCH if elements["domain"] else DOMAIN_MISMATCH)
        if elements["register"] is not None:
            flags.append(REGISTER_MATCH if elements["register"] else REGISTER_MISMATCH)
        return dict(
            source_type=source_type(page.url, directory=is_directory_host(page.url),
                                    impressum=is_impressum(page), own_domain=elements["domain"]),
            page_title=(page.content.page_title or None) if page.content else None,
            published_address=address_on_page(reading.published_address, text),
            name_match=name,
            address_match=address,
            match_score=match_score(name, elements),
        )
