"""Resolve the official website URL for a Name 1 organisation.

Two paths are exposed:

* :func:`resolve_website_via_serp` (Path B) — runs for *any* record
  type that did not match in ROR. The query shape varies by record
  type: research institutions get ``"name" official website`` (with
  ``.edu``/``.gov``/``.org`` results promoted to high confidence);
  companies / unknowns get ``"name" official website city state``
  with no TLD bias.
* :func:`infer_website_via_llm` (Path C) — fallback when Path B
  returns nothing usable. One LLM call; the orchestrator always
  flags Path C results for manual review.

Path A (ROR's authoritative ``links[]`` website) is handled inline by
the orchestrator using
:func:`enrichment.tier1_ror.extract_website_from_ror` — no module here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from llm.openai_client import OpenAIClient
from llm.prompts import (
    WEBSITE_INFERENCE_SYSTEM_PROMPT,
    WEBSITE_INFERENCE_USER_PROMPT_TEMPLATE,
)
from search.base import SearchClient, SearchResult
from utils.cache import BatchCache, cached_serp
from utils.domain_resolver import country_conflict
from utils.text_utils import acronym_matches_name, extract_domain

logger = logging.getLogger(__name__)

# Diagnostic-only per-candidate trace (see config.WEBSITE_TRACE). Records are
# emitted ONLY when the caller passes ``trace=True``; with tracing off this
# logger never fires, so default log volume and resolution behaviour are
# unchanged. Each record is a single JSON line for grep/parse.
trace_logger = logging.getLogger("enrichment.trace.website")


# Domains to exclude from SERP candidate selection — directories,
# social networks, review sites, employment aggregators. The official
# institution / company site is never one of these.
DOMAIN_BLACKLIST: frozenset[str] = frozenset({
    "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com",
    "x.com", "instagram.com", "youtube.com", "ratemyprofessors.com",
    "glassdoor.com", "yelp.com", "bbb.org", "crunchbase.com",
    "bloomberg.com", "indeed.com", "ziprecruiter.com",
})

# TLDs we treat as authoritative for research-institution / public
# bodies. An on-blacklist match is rejected before this is consulted.
_OFFICIAL_TLDS: frozenset[str] = frozenset({"edu", "gov", "org"})

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class WebsiteResolution:
    """Outcome of a website resolution attempt.

    ``confidence='high'`` → orchestrator writes ``website_url`` with no
    review flag.
    ``confidence='low'``  → orchestrator writes ``website_url`` AND
    sets ``flag_for_review``.
    ``confidence='none'`` → orchestrator leaves the field empty.
    """
    url: str | None = None
    confidence: str = "none"  # "high" | "low" | "none"
    source: str = "none"      # "serp" | "llm" | "none"
    # Title of the SERP result the URL came from. Read-only evidence for the
    # domain ownership guard's on-domain condition (utils/domain_resolver.py);
    # it never influences selection here.
    title: str | None = None


# ---------------------------------------------------------------------------
# Path B — SERP-based resolution
# ---------------------------------------------------------------------------

def _is_blacklisted(url: str) -> bool:
    domain = extract_domain(url) or ""
    return any(domain == bad or domain.endswith("." + bad) for bad in DOMAIN_BLACKLIST)


def _tld(url: str) -> str | None:
    domain = extract_domain(url)
    if not domain:
        return None
    return domain.rsplit(".", 1)[-1].lower()


def _significant_tokens(name1: str) -> set[str]:
    return {t for t in re.split(r"\W+", name1.lower()) if len(t) >= 4}


def _name_overlap(name1: str, candidate: SearchResult) -> bool:
    """True if any significant token of name1 appears in URL or title."""
    haystack = f"{candidate.url} {candidate.title}".lower()
    return any(t in haystack for t in _significant_tokens(name1))


# Generic industry words that do NOT distinctively identify an organisation —
# a stranger's domain must not be validated just because it shares one of these
# (§7a / W1). Mirrors the distinctive-token guard ROR applies upstream.
_GENERIC_NAME_TOKENS: frozenset[str] = frozenset({
    "research", "therapeutics", "diagnostics", "medical", "instruments",
    "sciences", "science", "laboratories", "laboratory", "labs",
    "technologies", "technology", "solutions", "systems", "group", "holdings",
    "international", "global", "pharma", "pharmaceutical", "bio", "biotech",
    "health", "healthcare", "services", "consulting", "partners", "associates",
})


def _distinctive_tokens(name1: str) -> set[str]:
    """Significant name1 tokens minus the generic industry blocklist."""
    return {t for t in _significant_tokens(name1) if t not in _GENERIC_NAME_TOKENS}


def _name_in_domain(name1: str, url: str) -> bool:
    """True if a significant name1 token is a substring of the host."""
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    return any(t in domain for t in _significant_tokens(name1))


def _distinctive_in_host(name1: str, url: str) -> bool:
    """True if a DISTINCTIVE (non-generic) name1 token is a substring of the
    host. A generic-token-only match (e.g. 'research' in a stranger's host) is
    not sufficient to validate a candidate (§7a)."""
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    return any(t in domain for t in _distinctive_tokens(name1))


def _acronym_in_host(name1: str, url: str) -> bool:
    """True when the host's first label equals the institution's initials
    ('fit.edu' ↔ 'Florida Institute of Technology'). Lets acronym-domain
    institutions pass the host-match requirement without a name word in the
    host, while a stranger's host ('scup.org' for 'Bayfront Research') fails."""
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    return acronym_matches_name(domain.split(".")[0], name1)


def _has_host_match(name1: str, url: str, record_type: str | None) -> bool:
    """§7a/§7b host-match test used by ranking. A distinctive name token in the
    host, OR (research institutions only) an acronym-in-host match."""
    if _distinctive_in_host(name1, url):
        return True
    if record_type == "research_institution" and _acronym_in_host(name1, url):
        return True
    return False


def _domain_introduces_foreign_brand(name1: str, url: str) -> bool:
    """True if the host's primary label carries a distinctive word absent
    from *name1* — the mark of a subsidiary / sub-brand domain.

    "Siemens AG" → ``siemens.com`` is clean (label is just "siemens"), but
    ``siemens-healthineers.com`` introduces "healthineers", a distinctive word
    the name never had → a *different* (sub-)entity's site. Only hyphen/
    underscore-separated labels are inspected: a single concatenated label
    ("thermofisher", "bankofamerica") can't be split reliably without a word
    list, so it is treated as clean to avoid false positives on legitimate
    multi-word company domains.
    """
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    label = domain.split(".")[0]
    parts = [p for p in re.split(r"[-_]", label) if p]
    if len(parts) <= 1:
        return False  # single label — cannot detect a foreign word safely
    tokens = _significant_tokens(name1)
    for part in parts:
        if len(part) < 4:
            continue  # short connector ("of", "and") — never distinctive
        if not any(part.startswith(t) or t.startswith(part) for t in tokens):
            return True  # a distinctive label part the name never carried
    return False


def _wrong_country(url: str, country: str | None) -> str | None:
    """The country a candidate's ccTLD claims when it contradicts *country*.

    The guard the SERP layer never had. Every other test here asks whether the
    NAME fits the host, and a multinational's name fits its host in every
    country it trades in: "Unilever Trumbull Research Services Inc" (US, TX)
    passes `_distinctive_in_host` against `unilever.be` on the token
    "unilever", because it genuinely is a Unilever site. Only the country
    separates it from the record's.

    ``country`` of ``None`` disables the test — that is how the orchestrator
    turns the gate off (`DOMAIN_COUNTRY_GATE_ENABLED=false`) without a second
    parameter travelling beside it.
    """
    return country_conflict(extract_domain(url), country)


def _candidate_key(sr: SearchResult) -> str:
    """The canonical id of a SERP candidate, for Fix C(1)'s tiebreak.

    The registrable domain first, then the full URL — two results on the same
    site order by URL, and two different sites order by domain, so the key is
    total and does not change when the search API reshuffles equally-ranked
    results between runs.
    """
    return f"{extract_domain(sr.url) or ''}|{(sr.url or '').lower()}"


def _best_candidate(
    valid: list[SearchResult], rank: "Callable[[SearchResult], int]",
) -> SearchResult:
    """The highest-ranked candidate, ties broken by canonical id ASC.

    Fix C(1). This was ``max(valid, key=_rank)``, which returns the FIRST
    maximum and therefore inherited the search API's own ordering for every
    tie. SERP order is not reproducible between runs — one chemspeed record
    changed its candidate domain between two runs of the identical batch on
    exactly this — so the tie is now broken by the candidate's own identity
    instead. Nothing about which candidates are *eligible* changes.
    """
    return min(valid, key=lambda sr: (-rank(sr), _candidate_key(sr)))


def _root_url(url: str) -> str:
    """Reduce a URL to scheme://host (drop path/query/fragment)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return url


# ---------------------------------------------------------------------------
# Diagnostic trace assembly (read-only — never affects resolution)
# ---------------------------------------------------------------------------

def _overlap_detail(
    name1: str, candidate: SearchResult, host: str,
) -> tuple[str | None, str | None]:
    """Re-derive which Name 1 token satisfied ``_name_overlap`` and where.

    Mirrors ``_name_overlap`` (haystack = URL + title) exactly, but returns the
    matched token and whether it hit the ``host`` (netloc), the ``title``, or
    only the URL path (``url``). Tokens are scanned in a deterministic (sorted)
    order. Returns ``(None, None)`` when overlap fails.
    """
    title = (candidate.title or "").lower()
    url = (candidate.url or "").lower()
    haystack = f"{candidate.url} {candidate.title}".lower()
    for token in sorted(_significant_tokens(name1)):
        if token in haystack:
            if token in host:
                return token, "host"
            if token in title:
                return token, "title"
            if token in url:
                return token, "url"
            return token, "url"
    return None, None


def _foreign_label(name1: str, url: str) -> str | None:
    """Re-derive the host label part that triggers ``_domain_introduces_foreign_
    brand`` (the distinctive word the name never carried). Read-only."""
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return None
    label = domain.split(".")[0]
    parts = [p for p in re.split(r"[-_]", label) if p]
    if len(parts) <= 1:
        return None
    tokens = _significant_tokens(name1)
    for part in parts:
        if len(part) < 4:
            continue
        if not any(part.startswith(t) or t.startswith(part) for t in tokens):
            return part
    return None


def _assemble_path_b_trace(
    *,
    record_id: str,
    name1: str,
    record_type: str | None,
    query: str,
    num_results: int,
    results: list[SearchResult],
    chosen: WebsiteResolution,
    error: str | None = None,
    attempt: str = "quoted",
    country: str | None = None,
) -> dict:
    """Build the per-candidate Path B trace record.

    Purely read-only: it re-evaluates the same pure guards (``_URL_RE``,
    ``_is_blacklisted``, ``_name_overlap``, ``_rank``) that
    ``select_website_from_serp`` used, in the SAME short-circuit order, to
    attribute each candidate's ``rejected_by`` to the FIRST guard that fired.
    It never mutates state or changes what was resolved.
    """
    # Recompute valid + chosen exactly as select_website_from_serp does, so the
    # per-candidate `chosen`/`rank` fields match the real decision.
    valid = [
        sr for sr in results
        if sr.url and _URL_RE.match(sr.url)
        and not _is_blacklisted(sr.url)
        and _name_overlap(name1, sr)
        and not _wrong_country(sr.url, country)
    ]
    ranks: dict[int, int] = {}
    chosen_sr: SearchResult | None = None
    if valid:
        for sr in valid:
            ranks[id(sr)] = (
                0 if not _has_host_match(name1, sr.url, record_type)
                else (1 if _domain_introduces_foreign_brand(name1, sr.url) else 2)
            )
        best = _best_candidate(valid, lambda sr: ranks[id(sr)])
        if ranks[id(best)] != 0:
            chosen_sr = best

    candidates: list[dict] = []
    for i, sr in enumerate(results, 1):
        host = ""
        if sr.url:
            try:
                host = urlparse(sr.url).netloc.lower()
            except Exception:
                host = ""
        entry: dict = {
            "position": i,
            "url": sr.url,
            "host": host,
            "title": (sr.title or "")[:120],
            "rejected_by": None,
            "matched_token": None,
            "matched_in": None,
            "foreign_label": None,
            "domain_country": None,
            "rank": None,
            "chosen": False,
        }
        # First-firing guard, in the real short-circuit order.
        if not (sr.url and _URL_RE.match(sr.url)):
            entry["rejected_by"] = "url_shape"
        elif _is_blacklisted(sr.url):
            entry["rejected_by"] = "blacklist"
        else:
            token, where = _overlap_detail(name1, sr, host)
            claimed = _wrong_country(sr.url, country)
            if token is None:
                entry["rejected_by"] = "name_overlap"
            elif claimed:
                entry["matched_token"] = token
                entry["matched_in"] = where
                entry["domain_country"] = claimed
                entry["rejected_by"] = "country_mismatch"
            else:
                entry["matched_token"] = token
                entry["matched_in"] = where
                rank = ranks.get(id(sr))
                entry["rank"] = rank
                if rank == 0:
                    entry["rejected_by"] = "rank_0"
                elif rank == 1:
                    entry["foreign_label"] = _foreign_label(name1, sr.url)
                entry["chosen"] = sr is chosen_sr
        if entry["chosen"]:
            entry["confidence"] = chosen.confidence
        candidates.append(entry)

    record: dict = {
        "phase": "path_b",
        "attempt": attempt,
        "record_id": record_id,
        "name1": name1,
        "record_type": record_type,
        "query": query,
        "num_results": num_results,
        "record_country": country,
        "results_returned": len(results),
        "candidates": candidates,
        "chosen_url": chosen.url,
        "confidence": chosen.confidence if chosen.url else None,
        "flagged": bool(chosen.url) and chosen.confidence != "high",
        "fell_through_to_path_c": chosen.url is None,
    }
    if error is not None:
        record["error"] = error
    return record


def select_website_from_serp(
    name1: str,
    results: list[SearchResult],
    record_type: str | None = None,
    *,
    country: str | None = None,
) -> WebsiteResolution:
    """Pick the best official-website candidate from ranked SERP results.

    *country* is the record's own country. A candidate whose ccTLD places it
    somewhere else is not eligible at all — not demoted, not accepted at low
    confidence — because the question it fails is not "how good a match is
    this" but "could this be the record's site". Country-neutral TLDs and
    worldwide ccTLDs are unaffected (:func:`_wrong_country`); passing ``None``
    disables the test and restores the previous behaviour exactly.

    Confidence rules:
      * Research institution: first non-blacklisted, name-overlapping hit;
        ``high`` if the URL is on ``.edu``/``.gov``/``.org``, else ``low``.
      * Company / unknown: among non-blacklisted, name-overlapping hits,
        prefer the host whose label matches name1 WITHOUT introducing a
        foreign brand word — ``siemens.com`` beats ``siemens-healthineers.com``
        for "Siemens AG" even when the subsidiary ranks higher. ``high`` when
        the chosen host cleanly contains a name token; ``low`` when only a
        sub-brand / weak host is available.
      * ``none`` — no usable candidate.
    """
    valid = [
        sr for sr in results
        if sr.url and _URL_RE.match(sr.url)
        and not _is_blacklisted(sr.url)
        and _name_overlap(name1, sr)
        and not _wrong_country(sr.url, country)
    ]
    if not valid:
        return WebsiteResolution()

    # Both branches now rank 0/1/2 on WHERE the name matched (§7b):
    #   2 = distinctive/acronym host match, no foreign brand word (clean)
    #   1 = host match but the label adds a foreign brand (sub-brand)
    #   0 = name only overlaps the title, not the host → rejected
    def _rank(sr: SearchResult) -> int:
        if not _has_host_match(name1, sr.url, record_type):
            return 0
        return 1 if _domain_introduces_foreign_brand(name1, sr.url) else 2

    best = _best_candidate(valid, _rank)
    best_rank = _rank(best)
    if best_rank == 0:
        # No candidate has a distinctive name token (or, for institutions, the
        # acronym) in its HOST — the overlap is only a word in the title. Too
        # weak to trust ('scup.org' for 'Bayfront Research'); defer to Path C.
        return WebsiteResolution()

    if record_type == "research_institution":
        # §7c: an authoritative TLD grants HIGH only with a clean host match.
        high = best_rank == 2 and _tld(best.url) in _OFFICIAL_TLDS
    else:
        high = best_rank == 2
    return WebsiteResolution(
        url=_root_url(best.url),
        confidence="high" if high else "low",
        source="serp",
        title=best.title,
    )


def _build_serp_query(
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    record_type: str | None,
    *,
    quoted: bool = True,
) -> str:
    """Construct the website-search query for *name1*.

    Research institutions: the bare ``"name" official website`` form
    works well — the SERP ranker already surfaces the institution's
    homepage as the top hit, and the post-filter promotes
    ``.edu``/``.gov``/``.org`` results to high confidence.

    Companies / unknown: include city/state to disambiguate common
    company names ("Smith Industries" exists in many cities), and
    fall back on country only when neither is available.
    """
    base = f'"{name1}" official website' if quoted else f"{name1} official website"
    if record_type == "research_institution":
        if country and country.strip():
            return f"{base} {country.strip()}"
        return base
    # company / unknown
    geo = " ".join(p.strip() for p in (city, state) if p and p.strip())
    if geo:
        return f"{base} {geo}"
    if country and country.strip():
        return f"{base} {country.strip()}"
    return base


async def resolve_website_via_serp(
    record_id: str,
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    record_type: str | None,
    search_client: SearchClient,
    cache: BatchCache,
    *,
    prefetched_results: list[SearchResult] | None = None,
    trace: bool = False,
    country_gate: bool = True,
) -> WebsiteResolution:
    """Path B: find the official site for *name1* via SERP.

    Runs for any record type. Confidence is decided by
    :func:`select_website_from_serp` based on the candidate URL's TLD
    and name overlap.

    If *prefetched_results* is provided (the orchestrator already ran a
    Tier 2B search for the same record), they are reused directly — no
    additional SERP call is made.

    When *trace* is True, a single per-candidate JSON diagnostic record is
    emitted on the ``enrichment.trace.website`` logger. Tracing is read-only:
    the resolution is computed exactly as before and the trace is assembled
    from the same results afterwards.

    *country* does two separate jobs here and they must not be confused. It
    shapes the QUERY and (via ``cached_serp``) the provider's geo ranking,
    which is a hint the provider may ignore; and when *country_gate* is on it
    also DISQUALIFIES candidates whose ccTLD contradicts it, which is a
    decision this module takes itself. ``country_gate=False`` turns off only
    the second — the query keeps its country either way.
    """
    num_results = 10
    if not name1 or not name1.strip():
        return WebsiteResolution()

    # The one value the gate reads. Held separately from `country` so the
    # kill switch cannot accidentally strip the country out of the query too.
    gate_country = country if country_gate else None

    if prefetched_results is not None:
        chosen = select_website_from_serp(
            name1, prefetched_results, record_type, country=gate_country,
        )
        logger.info(
            "[%s] website Path B (reused SERP): url=%s confidence=%s",
            record_id, chosen.url, chosen.confidence,
        )
        if trace:
            trace_logger.info(json.dumps(_assemble_path_b_trace(
                record_id=record_id, name1=name1, record_type=record_type,
                query="(reused Tier 2B SERP results)", num_results=num_results,
                results=prefetched_results, chosen=chosen, attempt="quoted",
                country=gate_country,
            )))
        return chosen

    async def _run(query: str, attempt: str) -> WebsiteResolution:
        # Country is part of the SERP cache key so two same-named orgs in
        # different countries cannot share an entry. The quoted and unquoted
        # forms stay distinct keys (utils.cache.serp_key), so §8's retry still
        # issues a real second search instead of being served the phrase
        # results it exists to escape.
        try:
            results = await cached_serp(
                cache, search_client, query,
                num_results=num_results, country=country,
            )
        except Exception as exc:
            logger.info("[%s] website Path B: SERP call failed: %s", record_id, exc)
            if trace:
                trace_logger.info(json.dumps(_assemble_path_b_trace(
                    record_id=record_id, name1=name1, record_type=record_type,
                    query=query, num_results=num_results, results=[],
                    chosen=WebsiteResolution(), error=f"serp_call_failed: {exc}",
                    attempt=attempt, country=gate_country,
                )))
            return WebsiteResolution()
        chosen = select_website_from_serp(
            name1, results, record_type, country=gate_country,
        )
        logger.info(
            "[%s] website Path B (%s): query=%r url=%s confidence=%s",
            record_id, attempt, query[:80], chosen.url, chosen.confidence,
        )
        if trace:
            trace_logger.info(json.dumps(_assemble_path_b_trace(
                record_id=record_id, name1=name1, record_type=record_type,
                query=query, num_results=num_results, results=results,
                chosen=chosen, attempt=attempt, country=gate_country,
            )))
        return chosen

    quoted_query = _build_serp_query(name1, city, state, country, record_type)
    chosen = await _run(quoted_query, "quoted")
    if chosen.url:
        return chosen

    # §8: one unquoted retry when the exact-phrase query found no valid
    # candidate — the site may brand itself slightly differently ("…Labs" vs
    # "…Laboratories"). Only runs on a first-pass miss; one retry maximum.
    unquoted_query = _build_serp_query(
        name1, city, state, country, record_type, quoted=False,
    )
    if unquoted_query != quoted_query:
        return await _run(unquoted_query, "unquoted_retry")
    return chosen


# ---------------------------------------------------------------------------
# Path C — LLM inference (Path B fallback)
# ---------------------------------------------------------------------------

def _looks_like_url(value: str | None) -> bool:
    """Cheap shape check — must start with http(s):// and have a host."""
    if not value or not isinstance(value, str):
        return False
    if not _URL_RE.match(value.strip()):
        return False
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    return bool(parsed.netloc and "." in parsed.netloc)


async def infer_website_via_llm(
    record_id: str,
    name1: str,
    city: str | None,
    state: str | None,
    country: str | None,
    llm_client: OpenAIClient,
    *,
    trace: bool = False,
    country_gate: bool = True,
) -> WebsiteResolution:
    """Path C: ask the LLM for an organisation's official website.

    Result is always returned as ``confidence='low'`` when a URL is
    produced — the orchestrator writes it to ``website_url`` and flags
    the record for manual review. Path C runs as the Path B fallback
    for any record type, including research institutions.

    When *trace* is True, a single JSON diagnostic record is emitted on the
    ``enrichment.trace.website`` logger (inputs, raw response, sentinel/URL-
    shape outcome, final value). Read-only — behaviour is unchanged.

    The country gate applies here too, and has to. Path B rejecting a foreign
    candidate is exactly what makes Path C run, so a gate that covered only
    Path B would hand the same domain back through the fallback it opened.
    The prompt already states the country; this is what happens when the model
    answers past it.
    """
    if not name1 or not name1.strip():
        return WebsiteResolution()

    user_prompt = WEBSITE_INFERENCE_USER_PROMPT_TEMPLATE.format(
        name1=name1,
        city=city or "(unknown)",
        state=state or "(unknown)",
        country=country or "(unknown)",
    )

    def _emit(**extra: object) -> None:
        if not trace:
            return
        rec = {
            "phase": "path_c",
            "record_id": record_id,
            "name1": name1,
            "inputs": {
                "city": city or "(unknown)",
                "state": state or "(unknown)",
                "country": country or "(unknown)",
            },
        }
        rec.update(extra)
        trace_logger.info(json.dumps(rec))

    try:
        payload = await llm_client.extract_json(
            WEBSITE_INFERENCE_SYSTEM_PROMPT, user_prompt,
        )
    except Exception as exc:
        logger.info(
            "[%s] website Path C: LLM call failed: %s", record_id, exc,
        )
        _emit(llm_error=str(exc), raw_response=None, treated_as_sentinel=None,
              url_shape_ok=False, final_value=None)
        return WebsiteResolution()

    raw_response = payload.get("website_url") if isinstance(payload, dict) else None
    raw = raw_response
    treated_as_sentinel = False
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.lower() in {"", "null", "none", "unknown", "n/a", "na"}:
            raw = None
            treated_as_sentinel = True

    if not _looks_like_url(raw):
        logger.info(
            "[%s] website Path C: LLM returned no usable URL for %r",
            record_id, name1[:60],
        )
        _emit(raw_response=raw_response, treated_as_sentinel=treated_as_sentinel,
              url_shape_ok=False, final_value=None)
        return WebsiteResolution()

    claimed = _wrong_country(raw, country) if country_gate else None
    if claimed:
        logger.info(
            "[%s] website Path C: LLM proposed %s for %r — rejected, its ccTLD "
            "places it in %s and the record is in %s",
            record_id, raw, name1[:60], claimed, country,
        )
        _emit(raw_response=raw_response, treated_as_sentinel=treated_as_sentinel,
              url_shape_ok=True, final_value=None,
              rejected_by="country_mismatch", domain_country=claimed)
        return WebsiteResolution()

    logger.info(
        "[%s] website Path C: LLM proposed %s for %r",
        record_id, raw, name1[:60],
    )
    _emit(raw_response=raw_response, treated_as_sentinel=treated_as_sentinel,
          url_shape_ok=True, final_value=raw)
    return WebsiteResolution(url=raw, confidence="low", source="llm")
