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

from enrichment.locality import compare_locality
from enrichment.tier1_lei import _name_match_score
from llm.openai_client import OpenAIClient
from llm.prompts import (
    PAGE_READ_PROMPT_VERSION,
    PAGE_READ_SYSTEM_PROMPT,
    PAGE_READ_USER_PROMPT_TEMPLATE,
)
from search.page_fetcher import PageFetcher
from utils.cache import PageCache

logger = logging.getLogger(__name__)

from enrichment.confidence import (
    PROVISIONAL,
    render as confidence_render,
    web_source as confidence_web_source,
)

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
    #: ISO date the page was fetched, from the cache entry (Fix B(4)). What
    #: `operating_name_provenance` stamps — never the date of THIS run.
    fetched_at: str | None = None

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
            "fetched_at": self.fetched_at,
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
    entry = cache.get_entry(domain)
    if entry is not None and entry.get("payload") is not None:
        cached = dict(entry["payload"])
        cached["from_fixture"] = True
        # Fix B(4) — the date the page was READ, carried out of the cache
        # entry. The provenance string is a claim about a day, so re-running
        # against a warm cache has to reproduce the original day rather than
        # stamp today's.
        cached["fetched_at"] = entry.get("fetched_at")
        return cached

    if cache.replay_only:
        return {
            "status": None, "blocked": False, "error": "replay_only_miss",
            "url": None, "title": "", "h1": "", "text": "", "paths": [],
            "fetched_at": None,
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
        payload["fetched_at"] = cache.fetched_at(domain)
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
    payload["fetched_at"] = cache.fetched_at(domain)
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

def compare_location(
    statement: PageStatement,
    *,
    city: str | None,
    region: str | None,
    country: str | None,
    postal_code: str | None,
) -> tuple[str, str | None, str | None]:
    """``("consistent" | "contradicted" | "neutral", detail, scope)``.

    A thin adapter over :func:`enrichment.locality.compare_locality`, which is
    where these rules now live — Fix D(2) applies the same comparator to ROR
    and GLEIF localities, and the registry clients cannot import this module
    (it imports them). The rules are unchanged in the move; see that module's
    docstring for them and for why *scope* is part of the answer.

    Neutral is the default and the common case: most company pages state a
    name and no address, and a site that does not publish its address tells
    us nothing about where the customer is. Only a *stated* place that differs
    is a contradiction — which is precisely the AB Controls signal (the page
    states Milwaukee, the record says Irvine).

    The withdrawal rule in ``Orchestrator._corroborate_domain`` reads *scope*,
    and only a region- or country-level contradiction is allowed to take a
    domain back.
    """
    if not statement.states_location:
        return "neutral", None, None
    verdict, detail, scope = compare_locality(
        stated_city=statement.stated_city,
        stated_region=statement.stated_region,
        stated_country=statement.stated_country,
        stated_postal_code=statement.stated_postal_code,
        city=city, region=region, country=country, postal_code=postal_code,
    )
    # The corroborator's own prose has always led with "page states …"; the
    # shared comparator is source-agnostic and says "states …".
    if detail and detail.startswith("states "):
        detail = f"page {detail}"
    return verdict, detail, scope


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
    fetched_at = payload.get("fetched_at")

    def _out(outcome: str, **kw) -> Corroboration:
        result = Corroboration(
            outcome=outcome, domain=domain, source_url=url,
            fetched_paths=paths, from_fixture=from_fixture,
            fetched_at=fetched_at, **kw,
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


def operating_name_provenance(domain: str) -> str:
    """``web:{domain}:provisional`` — the provenance of an extracted identity.

    Provenance Scheme B, the same grammar and the same confidence table the
    six scoped fields use (:mod:`enrichment.confidence`). Two things changed
    from ``web:{domain}:extracted:{date}``, and both are the scheme's point:

    ``extracted`` was a METHOD, and a method is not a confidence. What a
    reviewer needs from this column is how much weight the name carries, and
    the answer is ``provisional``: one source read it off one page, and the
    page it was read from is the domain in the source token — which is one
    source, not two, under hard rule 4. It reaches ``verified`` only if an
    independent system agrees, and this path has no such agreement to report.

    The DATE is gone from the string and lives where it can be acted on: on
    the evidence-cache entry (``PageCache.fetched_at``, which is also what
    made the string reproducible under Fix B(4)) and on the
    ``operating_name_extracted`` trace line the orchestrator emits at the
    write site. It was removed from the column because a decaying token in an
    exported field is read as part of the claim, and eleven rows of the two
    diffed chemspeed runs once differed in nothing else.
    """
    return confidence_render(
        confidence_web_source(domain), PROVISIONAL,
    )
