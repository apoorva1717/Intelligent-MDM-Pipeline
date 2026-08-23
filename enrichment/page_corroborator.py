"""Fix 3 — read the candidate website and see whether it names this record.

The pipeline finds a candidate domain for most records and then decides
whether to keep it *without ever opening it*. Both halves of that go wrong on
the chemspeed batch:

* 20 records carry `domain-unverified` — a candidate was found, nothing on the
  record tied it to the organisation, and it was discarded. The one source that
  could have tied them, the site itself, was never consulted.
* One record kept a domain that is plainly wrong: `johnsoncontrols.com` for
  "AB Controls, Inc." in Irvine CA. The page says Johnson Controls, Milwaukee.
  A single read settles it.

So this module fetches the candidate, has the LLM read what the page *states*,
and compares that statement with the record. Three rules shape everything here:

**A page is a witness, never an author.** Nothing in this module writes
``name1_enriched``. An extracted identity goes to :data:`OPERATING_NAME` — a
new field — and the record's Name 1 is left exactly as the rest of the pipeline
decided it. A website is evidence about a name; it is not the customer master's
source of truth for one, and a site that trades under a brand
("Acme Labs") while the record holds the legal entity ("Acme Laboratories
Holdings Inc") is the normal case, not an error to be corrected.

**Silence is not evidence.** A page that states no address neither corroborates
nor contradicts the record's address. Only a *stated* address in a different
place is a contradiction. The same applies to the fetch itself: a 403, a bot
challenge or a timeout means we could not look, and "could not look" must never
be scored as either outcome.

**No new similarity machinery.** Name comparison reuses
:func:`enrichment.tier1_lei._name_match_score` — ``token_sort_ratio``, taken as
the max of the raw score and the score with legal-form suffixes stripped — at
the threshold that guard already uses. See :data:`PAGE_NAME_MATCH_THRESHOLD`
in ``config.py`` for the derivation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urljoin

from enrichment.tier1_lei import _name_match_score
from enrichment.tier1_ror import _US_POSTAL_CODES
from llm.openai_client import OpenAIClient
from llm.prompts import (
    PAGE_READ_PROMPT_VERSION,
    PAGE_READ_SYSTEM_PROMPT,
    PAGE_READ_USER_PROMPT_TEMPLATE,
)
from search.page_fetcher import PageFetcher
from utils.cache import PageCache

logger = logging.getLogger(__name__)

# One JSON line per corroboration attempt, on its own logger — the same shape
# as `enrichment.trace.website` and `enrichment.trace.retry`. A page read is
# the evidence behind a domain being kept, withdrawn or unflagged, so it has to
# be recoverable as data rather than as prose in an application log. The logger
# has no handler unless a caller attaches one, so this costs a `json.dumps` and
# nothing else.
trace_logger = logging.getLogger("enrichment.trace.page")

#: The output field an extracted identity is written to. Never ``name1``.
OPERATING_NAME = "operating_name"

#: Paths tried after the root, in order, stopping at the first that answers
#: 2xx. `/impressum` first because a German-law imprint is the single most
#: reliable statement of legal identity any site carries; the English-language
#: equivalents follow in decreasing order of how formal they usually are.
IMPRINT_PATHS: tuple[str, ...] = ("/impressum", "/legal", "/about", "/contact")

#: Outcomes. Exhaustive and mutually exclusive over records that reach the
#: corroborator.
CORROBORATED = "corroborated"          # name consistent, location not contradicted
CONTRADICTED = "contradicted"          # name consistent, location contradicted
NAME_MISMATCH = "name_mismatch"        # the page names a different organisation
FETCH_UNAVAILABLE = "fetch_unavailable"  # blocked, unreachable, or replay miss
NO_IDENTITY = "no_identity"            # page states no organisation identity
PARKED = "parked"                      # parked / for-sale placeholder

#: Markers of a parked or for-sale domain. Matched case-insensitively against
#: the page title and the first slice of body text. These are the phrases the
#: parking services themselves render; a real company page does not carry them.
_PARKING_MARKERS: tuple[str, ...] = (
    "this domain is for sale", "domain is for sale", "buy this domain",
    "domain for sale", "parked free", "parked domain",
    "courtesy of godaddy", "hugedomains", "sedo", "dan.com",
    "afternic", "namecheap parking", "future home of something quite cool",
    "this web page is parked", "domain parking",
)

#: Markers of an interstitial bot challenge. The server answered 200, so the
#: status code alone does not reveal that we were refused.
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "checking your browser", "enable javascript and cookies to continue",
    "verify you are human", "just a moment", "attention required",
    "ddos protection by", "access denied", "request blocked",
    "captcha", "cf-browser-verification",
)

#: A page with less than this much text states nothing. Not a tuned threshold:
#: it is the length below which the LLM has no sentence to read, and the
#: purpose is to skip an LLM call that can only return nulls.
_MIN_CONTENT_CHARS = 120

_WS_RE = re.compile(r"\s+")


@dataclass
class PageStatement:
    """What the page said about itself, as the reader reported it."""
    stated_org_name: str | None = None
    stated_city: str | None = None
    stated_region: str | None = None
    stated_country: str | None = None
    stated_postal_code: str | None = None
    legal_form_present: bool = False

    @property
    def states_identity(self) -> bool:
        return bool(self.stated_org_name and self.stated_org_name.strip())

    @property
    def states_location(self) -> bool:
        return any((
            self.stated_city, self.stated_region,
            self.stated_country, self.stated_postal_code,
        ))


@dataclass
class Corroboration:
    """One record's page-read outcome, and the evidence behind it."""
    outcome: str
    domain: str
    source_url: str | None = None
    statement: PageStatement | None = None
    name_score: float | None = None
    name_consistent: bool = False
    #: ``"consistent"`` | ``"contradicted"`` | ``"neutral"`` — never a bare
    #: boolean, because "the page said nothing" is a third answer.
    location: str = "neutral"
    location_detail: str | None = None
    #: The granularity the location verdict was reached at — ``postal`` |
    #: ``city`` | ``region`` | ``country``. Two cities in one state are a
    #: weaker disagreement than two states; the withdrawal rule reads this.
    location_scope: str | None = None
    fetched_paths: list[str] = field(default_factory=list)
    from_fixture: bool = False

    @property
    def corroborated(self) -> bool:
        return self.outcome == CORROBORATED

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "domain": self.domain,
            "source_url": self.source_url,
            "corroborated": self.corroborated,
            "name_score": self.name_score,
            "name_consistent": self.name_consistent,
            "location": self.location,
            "location_detail": self.location_detail,
            "location_scope": self.location_scope,
            "stated_org_name": (
                self.statement.stated_org_name if self.statement else None
            ),
            "stated_city": self.statement.stated_city if self.statement else None,
            "stated_region": (
                self.statement.stated_region if self.statement else None
            ),
            "stated_country": (
                self.statement.stated_country if self.statement else None
            ),
            "legal_form_present": bool(
                self.statement and self.statement.legal_form_present
            ),
            "fetched_paths": list(self.fetched_paths),
            "from_fixture": self.from_fixture,
        }


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _looks_parked(title: str, text: str) -> bool:
    haystack = f"{title} {text[:800]}".lower()
    return any(marker in haystack for marker in _PARKING_MARKERS)


def _looks_challenged(title: str, text: str) -> bool:
    haystack = f"{title} {text[:800]}".lower()
    return any(marker in haystack for marker in _CHALLENGE_MARKERS)


async def fetch_pages(
    domain: str,
    fetcher: PageFetcher,
    cache: PageCache,
    *,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Read *domain*, returning the JSON payload the cache stores.

    One root fetch, and — only if the root answered — one imprint page: the
    first of :data:`IMPRINT_PATHS` that returns 2xx. The imprint text is
    appended to the root's rather than replacing it, so a name stated only in
    a footer copyright line and an address stated only on ``/contact`` are both
    available to the reader in a single call.

    Cached and fixture-recorded on the **domain**, so a batch that names the
    same organisation twice reads it once, and a re-run reads what the first
    run saw. ``cache.replay_only`` refuses to go to the network at all: a
    missing fixture is reported as ``fetch_unavailable``, never silently
    re-fetched.
    """
    cached = cache.get(domain)
    if cached is not None:
        cached = dict(cached)
        cached["from_fixture"] = True
        return cached

    if cache.replay_only:
        return {
            "status": None, "blocked": False, "error": "replay_only_miss",
            "url": None, "title": "", "h1": "", "text": "", "paths": [],
        }

    root = f"https://{domain}/"
    result = await fetcher.fetch_page_result(root, timeout=timeout)
    payload: dict[str, Any] = {
        "status": result.status,
        "blocked": result.blocked,
        "error": result.error,
        "url": result.url,
        "title": "", "h1": "", "text": "", "paths": [],
    }
    if not result.ok or result.content is None:
        cache.set(domain, payload)
        return payload

    content = result.content
    payload.update({
        "title": content.page_title,
        "h1": content.h1,
        "text": content.body_text,
        "paths": ["/"],
    })

    # The imprint probe. Stops at the first 2xx; a 404 on /impressum is not a
    # failure, it is a site that does not use that path.
    for path in IMPRINT_PATHS:
        sub = await fetcher.fetch_page_result(
            urljoin(root, path.lstrip("/")), timeout=timeout,
        )
        if not sub.ok or sub.content is None:
            continue
        payload["paths"].append(path)
        payload["text"] = _WS_RE.sub(
            " ",
            f"{payload['text']}\n\n[{path}] "
            f"{sub.content.page_title} {sub.content.h1} {sub.content.body_text}",
        ).strip()
        break

    cache.set(domain, payload)
    return payload


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "na", "unknown", "-"}:
        return None
    return text


async def read_page(
    payload: dict[str, Any], llm_client: OpenAIClient, record_id: str,
) -> PageStatement | None:
    """Ask the reader what the fetched text states. ``None`` when it states
    nothing — which is the answer the prompt is written to make available."""
    text = (payload.get("text") or "").strip()
    if len(text) < _MIN_CONTENT_CHARS:
        return None

    prompt = PAGE_READ_USER_PROMPT_TEMPLATE.format(
        url=payload.get("url") or "",
        title=payload.get("title") or "",
        h1=payload.get("h1") or "",
        text=text,
    )
    try:
        raw = await llm_client.extract_json(PAGE_READ_SYSTEM_PROMPT, prompt)
    except Exception as exc:  # noqa: BLE001 — a page read must never fail a record
        logger.info("[%s] page read: LLM failed: %s", record_id, exc)
        return None
    if not isinstance(raw, dict):
        return None

    statement = PageStatement(
        stated_org_name=_clean(raw.get("stated_org_name")),
        stated_city=_clean(raw.get("stated_city")),
        stated_region=_clean(raw.get("stated_region")),
        stated_country=_clean(raw.get("stated_country")),
        stated_postal_code=_clean(raw.get("stated_postal_code")),
        legal_form_present=bool(raw.get("legal_form_present")),
    )
    return statement if statement.states_identity else None


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def _norm(value: str | None) -> str:
    return _WS_RE.sub(" ", (value or "").strip().lower())


def _norm_region(value: str | None) -> str:
    """A US region normalised so "CA" and "California" compare equal.

    Reuses ``tier1_ror._US_POSTAL_CODES`` — the existing two-letter code map —
    rather than a second table. The map is ROR-local because expanding those
    codes inside a *name* is ambiguous ("IN Laboratories"); here the value is a
    Region field, where a bare two-letter token can only be the state, and the
    result is used for a comparison and never written anywhere. The same
    licence ``utils.domain_resolver`` already takes with
    ``_normalise_for_tokens``.

    Without this, `San Francisco, California` on a page "contradicts"
    `San Francisco, CA` on the record — measured on the chemspeed batch, where
    it produced a false contradiction on Anresco Laboratories.
    """
    text = _norm(value).strip(". ")
    if not text:
        return ""
    # The map's values are title-cased ("ca" -> "California"); both sides of
    # the comparison are lowercase here.
    return _US_POSTAL_CODES.get(text, text).lower()


def _postal_matches(stated: str | None, record: str | None) -> bool:
    """Postal codes compared on digits/letters only — "12345-6789" and "12345"
    are the same place written two ways, and a 5-digit ZIP is the part both
    sides always carry."""
    a = re.sub(r"[^a-z0-9]", "", (stated or "").lower())[:5]
    b = re.sub(r"[^a-z0-9]", "", (record or "").lower())[:5]
    return bool(a) and a == b


def compare_location(
    statement: PageStatement,
    *,
    city: str | None,
    region: str | None,
    country: str | None,
    postal_code: str | None,
) -> tuple[str, str | None, str | None]:
    """``("consistent" | "contradicted" | "neutral", detail, scope)``.

    Neutral is the default and the common case: most company pages state a
    name and no address, and a site that does not publish its address tells
    us nothing about where the customer is. Only a *stated* place that differs
    is a contradiction — which is precisely the AB Controls signal (the page
    states Milwaukee, the record says Irvine).

    *scope* names the granularity the verdict was reached at — ``"postal"``,
    ``"city"``, ``"region"`` or ``"country"`` — because the granularities are
    not equally strong evidence. Two cities in the same state are routinely one
    company's plant and head office (Houston / Baytown, Texas); two states or
    two countries are not. The withdrawal rule in
    ``Orchestrator._corroborate_domain`` reads this, and only a region- or
    country-level contradiction is allowed to take a domain back.
    """
    if not statement.states_location:
        return "neutral", None, None

    if _postal_matches(statement.stated_postal_code, postal_code):
        return "consistent", f"postal {statement.stated_postal_code}", "postal"

    stated_city, record_city = _norm(statement.stated_city), _norm(city)
    stated_region = _norm_region(statement.stated_region)
    record_region = _norm_region(region)
    stated_country, record_country = _norm(statement.stated_country), _norm(country)

    # Country first, and only when both sides state one: a different country is
    # the strongest disagreement available and is not softened by a city that
    # happens to share a name.
    if stated_country and record_country and stated_country != record_country:
        return "contradicted", (
            f"page states country {statement.stated_country}; "
            f"record says {country}"
        ), "country"

    if stated_region and record_region and stated_region != record_region:
        return "contradicted", (
            f"page states region {statement.stated_region}; record says {region}"
        ), "region"

    if stated_city and record_city:
        if stated_city == record_city:
            return "consistent", f"city {statement.stated_city}", "city"
        return "contradicted", (
            f"page states city {statement.stated_city}; record says {city}"
        ), "city"

    if stated_region and record_region:
        return "consistent", f"region {statement.stated_region}", "region"

    # A stated country that matches, with nothing finer, is too coarse to
    # corroborate a US SMB — every candidate in a US batch would pass.
    return "neutral", None, None


def compare(
    statement: PageStatement,
    *,
    name1: str,
    city: str | None = None,
    region: str | None = None,
    country: str | None = None,
    postal_code: str | None = None,
    threshold: float,
) -> tuple[str, float, str, str | None, str | None]:
    """``(outcome, name_score, location, location_detail, location_scope)``.

    Location is computed even when the name does not match, because the
    withdrawal rule needs both answers: a name difference alone is far more
    often a brand-vs-legal-name variant ("AquaPhoenix" for "AquaPhoenix
    Scientific, Inc.") than a wrong site.
    """
    score = _name_match_score(name1 or "", statement.stated_org_name or "")
    location, detail, scope = compare_location(
        statement, city=city, region=region,
        country=country, postal_code=postal_code,
    )
    if score < threshold:
        return NAME_MISMATCH, score, location, detail, scope
    if location == "contradicted":
        return CONTRADICTED, score, location, detail, scope
    return CORROBORATED, score, location, detail, scope


# ---------------------------------------------------------------------------
# The whole step
# ---------------------------------------------------------------------------

async def corroborate(
    *,
    record_id: str,
    domain: str,
    name1: str,
    city: str | None,
    region: str | None,
    country: str | None,
    postal_code: str | None,
    fetcher: PageFetcher,
    cache: PageCache,
    llm_client: OpenAIClient,
    threshold: float,
    timeout: int | None = None,
) -> Corroboration:
    """Fetch, read and compare — one candidate domain, one verdict."""
    payload = await fetch_pages(domain, fetcher, cache, timeout=timeout)
    from_fixture = bool(payload.get("from_fixture"))
    url = payload.get("url")
    paths = list(payload.get("paths") or ())

    def _out(outcome: str, **kw) -> Corroboration:
        result = Corroboration(
            outcome=outcome, domain=domain, source_url=url,
            fetched_paths=paths, from_fixture=from_fixture, **kw,
        )
        line = {
            "record_id": record_id,
            "step": "page_corroboration",
            "name1": name1,
            "record_city": city,
            "record_region": region,
            **result.as_dict(),
        }
        logger.info(line)
        trace_logger.info(json.dumps(line, default=str))
        return result

    status = payload.get("status")
    title, text = payload.get("title") or "", payload.get("text") or ""

    # Could not look. Never evidence in either direction.
    if status is None or payload.get("blocked") or not (200 <= int(status) < 300):
        return _out(FETCH_UNAVAILABLE)
    if _looks_challenged(title, text):
        return _out(FETCH_UNAVAILABLE)

    if _looks_parked(title, text) or not text.strip():
        return _out(PARKED)

    statement = await read_page(payload, llm_client, record_id)
    if statement is None:
        return _out(NO_IDENTITY)

    outcome, score, location, detail, scope = compare(
        statement, name1=name1, city=city, region=region,
        country=country, postal_code=postal_code, threshold=threshold,
    )
    return _out(
        outcome, statement=statement, name_score=score,
        name_consistent=outcome in (CORROBORATED, CONTRADICTED),
        location=location, location_detail=detail, location_scope=scope,
    )


def operating_name_provenance(domain: str, when: date | None = None) -> str:
    """``web:{domain}:extracted:{date}`` — the provenance string for an
    extracted identity. Deliberately NOT the ``producer:tier:band`` shape the
    six scoped fields use: this value did not come from the enrichment tiers,
    it came from a page on a day, and the day is the part that decays."""
    return f"web:{domain}:extracted:{(when or date.today()).isoformat()}"
