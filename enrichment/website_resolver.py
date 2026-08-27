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
from utils.text_utils import acronym_matches_name, extract_domain, name_initials

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


# Legal-form suffixes. They are part of the registered name and routinely part
# of the domain ("emservicesllc.com"), but they identify a company's *form*,
# never the company — so they may close out a host label without being what
# matched it.
_LEGAL_FORM_TOKENS: frozenset[str] = frozenset({
    "llc", "llp", "lp", "inc", "incorporated", "corp", "corporation", "co",
    "company", "ltd", "limited", "plc", "gmbh", "mbh", "ag", "kg", "bv", "nv",
    "sa", "sas", "srl", "spa", "ab", "as", "oy", "aps", "pte", "pty", "pvt",
    "kk", "kft", "zrt", "doo",
})

# Contractions a company routinely uses in its own domain. These are NOT
# prefixes of the word they stand for ("mfg" ← "manufacturing"), so no amount
# of prefix matching finds them; they have to be listed.
_TOKEN_ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    "manufacturing": ("mfg", "mfr", "manu"),
    "manufacturers": ("mfrs", "mfg"),
    "technology": ("tech",), "technologies": ("tech",),
    "services": ("svcs", "svc", "serv"), "service": ("svc", "serv"),
    "international": ("intl",),
    "engineering": ("engr", "eng"), "engineers": ("engr", "eng"),
    "laboratories": ("labs", "lab"), "laboratory": ("lab",),
    "association": ("assoc", "assn"), "associates": ("assoc",),
    "management": ("mgmt", "mgt"),
    "industries": ("inds", "ind"), "industrial": ("ind",),
    "systems": ("sys",), "solutions": ("soln", "sol"),
    "group": ("grp",),
    "pharmaceuticals": ("pharma",), "pharmaceutical": ("pharma",),
    "equipment": ("equip",), "products": ("prods", "prod"),
    "construction": ("constr",), "development": ("dev",),
    "instruments": ("instr",), "resources": ("res",),
}


def _host_label_covered_by_name(name1: str, url: str) -> bool:
    """True when the host's primary label is ENTIRELY spelled out by *name1*'s
    words, read left to right, allowing each word to appear contracted.

    This is the test that catches the domains §7a's token rule cannot see:

        "EM Services LLC"   -> emservicesllc   em|services|llc      -> covered
        "KNT Manufacturing" -> kntmfg          knt|mfg              -> covered

    Neither has a *distinctive* token in the host ("EM"/"KNT" fall under the
    4-character significance floor, "Services" is generic), so both are rank 0
    on the token rule while being the company's own site.

    Full coverage is the whole guard, and it is why this can be permissive
    about short words without re-opening §7a. A label only matches when nothing
    is left over, so a stranger's host that merely *starts* with a name word is
    still rejected: "Precision Research" against ``researchgate.net`` consumes
    "research" and strands "gate", and ``scup.org`` for "Bayfront Research"
    never starts at all.
    """
    domain = (extract_domain(url) or "").lower()
    if not domain:
        return False
    label = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    if not label:
        return False

    rest = label
    matched_any = False
    for token in (t.lower() for t in re.findall(r"[A-Za-z]+", name1)):
        if not rest:
            break
        hit = None
        for form in (token, *_TOKEN_ABBREVIATIONS.get(token, ())):
            if rest.startswith(form):
                hit = form
                break
        # A truncation the abbreviation table does not carry ("labora" ->
        # "laboratories"). Only for words long enough that a >=3-char prefix
        # still identifies them.
        if hit is None and len(token) >= 5:
            for n in range(len(token) - 1, 2, -1):
                if rest.startswith(token[:n]):
                    hit = token[:n]
                    break
        if hit:
            rest = rest[len(hit):]
            matched_any = True

    # A legal form may close out the label even when the name omitted it
    # ("EM Services" -> emservicesllc.com).
    while rest:
        for form in _LEGAL_FORM_TOKENS:
            if rest.startswith(form):
                rest = rest[len(form):]
                break
        else:
            break

    return matched_any and not rest


def _has_host_match(name1: str, url: str, record_type: str | None) -> bool:
    """§7a/§7b host-match test used by ranking. Any of: a distinctive name
    token in the host, an acronym-in-host match, or a host label spelled out
    in full by the name's words (:func:`_host_label_covered_by_name`)."""
    if _distinctive_in_host(name1, url):
        return True
    # Acronym hosts are not an institution phenomenon — "Milton Keynes Play
    # Association" is on mkpa.co.uk exactly as "Florida Institute of
    # Technology" is on fit.edu. The initials must match in full, so a
    # stranger's host still fails ("scup" is not the initials of "Bayfront
    # Research"); requiring three of them keeps two-letter coincidences out.
    if len(name_initials(name1)) >= 3 and _acronym_in_host(name1, url):
        return True
    if _host_label_covered_by_name(name1, url):
        return True
    return False



# ── Region / country evidence ──────────────────────────────────────────────
#
# Region and country were already in play here, but only in the two weakest
# places available: the country shapes the QUERY, and `_wrong_country`
# disqualifies a candidate whose ccTLD contradicts it. Neither can separate two
# campuses of one institution inside one country — and that is the case this
# exists for. "University of Texas" is not one university; it is a system of a
# dozen, and the record's city is the only field that says which one this row
# is.
#
# The corroborating unit is the CITY, not the region. `enrichment.locality`
# settled that for the page corroborator already — a match no finer than the
# region "is too coarse to corroborate", because every candidate in a
# single-state batch clears it. Not hypothetical here: "Texas" occurs in
# `texaslonghorns.com`, `texasalmanac.com` and `comptroller.texas.gov` alike,
# so region-level corroboration would rank precisely nothing. The region earns
# its keep in the QUERY instead, where it changes which candidates come back at
# all (`_build_serp_query`).


def _record_city_token(city: str | None) -> str | None:
    """The record's city normalised for a substring test, or ``None`` when it
    is too short to be evidence — "Ada", "Rye" and "Erie" occur inside ordinary
    words and would corroborate anything."""
    token = re.sub(r"\s+", " ", (city or "").strip().lower())
    return token if len(token) >= 4 else None


def _geo_corroborated(city: str | None, candidate: SearchResult) -> bool:
    """True when the candidate's SERP text names the record's city.

    Title AND snippet: a campus qualifier usually sits in the title ("The
    University of Texas at El Paso"), but a homepage often states the place
    only in the snippet.
    """
    token = _record_city_token(city)
    if not token:
        return False
    return token in f"{candidate.title or ''} {candidate.snippet or ''}".lower()


def _name_phrase_in_title(name1: str, title: str | None) -> bool:
    """True when *title* carries name1 as a contiguous phrase, punctuation and
    spacing ignored.

    Far stricter than `_name_overlap`'s any-token test, and it has to be: it is
    what tells "UTEP: The University of Texas at El Paso" from "Texas Colleges
    and Universities".
    """
    def _flat(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()

    needle, hay = _flat(name1), _flat(title or "")
    return bool(needle) and needle in hay


# An abbreviation domain cannot pass `_has_host_match`: "utep" carries neither
# "university" nor "texas", and the initials of "University of Texas" are two
# letters — below the acronym rule's floor of three. So the one candidate that
# IS the record's institution ranks 0 and is discarded, while `texasalmanac.com`
# — a reference site whose SERP title is character-for-character identical to
# utep.edu's — ranks 2 on the substring "texas".
#
# The host test cannot separate those two, and no tightening of it can: the same
# permissive substring rule is the only thing keeping `utexas.edu` eligible as
# well (`utexas` does not start with "texas", so coverage-style matching drops
# the right answer along with the wrong one). Only evidence the host never
# carried can do it. All three of these must hold, and each rules out a real
# candidate seen on this SERP:
#
#   .edu / .gov      — `texasalmanac.com` is out. `.org` is deliberately NOT
#                      here: it is the loosest of the authoritative TLDs and
#                      the one `scup.org` sits on, the stranger domain the
#                      host test exists to reject.
#   full name phrase — `comptroller.texas.gov` ("Texas Colleges and
#                      Universities") is out.
#   city corroborated — every campus that is not the record's is out.
_RESCUE_TLDS: frozenset[str] = frozenset({"edu", "gov"})


def _geo_rescues_host_miss(
    name1: str,
    candidate: SearchResult,
    city: str | None,
    record_type: str | None,
) -> bool:
    """True when locality evidence promotes a rank-0 institution candidate.

    Institutions only. A company's site is not identified by its city appearing
    beside its name, and `.edu`/`.gov` is not where a company lives.
    """
    if record_type != "research_institution":
        return False
    if _tld(candidate.url) not in _RESCUE_TLDS:
        return False
    if not _name_phrase_in_title(name1, candidate.title):
        return False
    return _geo_corroborated(city, candidate)


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


def _host_depth(url: str) -> int:
    """Label count of the host, ``www.`` ignored — 2 for ``harvard.edu``, 3 for
    ``college.harvard.edu``.

    An organisation lives at the root of its host; a subdomain is one of its
    units. Both are the same registrable domain, so `_candidate_key` could not
    tell them apart and sorted them as strings: "college.harvard.edu" precedes
    "www.harvard.edu", and Harvard University resolved to Harvard College.
    """
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host.count(".") + 1 if host else 99


def _sort_key(
    sr: SearchResult,
    rank: int,
    position: int,
    *,
    record_type: str | None,
    city: str | None,
) -> tuple:
    """Total order over eligible candidates, best first.

    Fix C(1) replaced ``max(valid, key=_rank)`` — which inherited the search
    API's ordering for every tie — with a tiebreak on the candidate's own id,
    because SERP order is not reproducible between runs and one chemspeed
    record changed its domain because of it. That fixed the determinism and
    introduced a different bug: `_candidate_key` sorts ALPHABETICALLY, so every
    rank tie resolved to whichever domain happened to sort first.
    "texaslonghorns.com" < "utexas.edu", so UT Austin's athletics site beat the
    university's own homepage — deterministically, which is why it was every
    University of Texas record in the batch rather than some of them.

    The answer is not to go back to SERP order, but to put MEANINGFUL keys in
    front of the alphabetical one. It stays as the final backstop, so the
    ordering is still total and still reproducible run to run:

      1. ``rank``          — the existing §7b host match, including a rank
         restored by :func:`_geo_rescues_host_miss`.
      2. authoritative TLD — for an institution, ``.edu``/``.gov``/``.org``
         outranks ``.com``. Until now the TLD set CONFIDENCE and never ORDER,
         so an athletics ``.com`` competed on equal footing with the
         university's ``.edu``. Institutions only: for a company ``.com`` is
         the normal home and promoting ``.org`` above it would be wrong.
      3. city corroborated — which campus of a multi-campus institution.
      4. host depth        — the root host over one of its subdomains
         (:func:`_host_depth`).
      5. SERP position     — the provider's own ranking, previously discarded
         outright. utexas.edu was the #1 result and lost to the #2.
      6. ``_candidate_key`` — unchanged, and still what makes ties reproducible.
    """
    authoritative = (
        record_type == "research_institution" and _tld(sr.url) in _OFFICIAL_TLDS
    )
    return (
        -rank,
        0 if authoritative else 1,
        0 if _geo_corroborated(city, sr) else 1,
        _host_depth(sr.url),
        position,
        _candidate_key(sr),
    )


def _evaluate_candidates(
    name1: str,
    results: list[SearchResult],
    record_type: str | None,
    *,
    country: str | None,
    city: str | None,
) -> tuple[list[SearchResult], dict[int, int], SearchResult | None]:
    """``(valid, rank_by_id, best)`` — the whole selection decision, computed
    once.

    :func:`select_website_from_serp` and :func:`_assemble_path_b_trace` both
    need this and each used to compute it separately, the trace's copy carrying
    a comment promising it mirrored the real one "in the SAME short-circuit
    order". That is a promise only shared code can actually keep.

    ``best`` is ``None`` when the winner is still rank 0 — no candidate has a
    distinctive name token (or the acronym) in its HOST and none was rescued by
    locality evidence, so the overlap is only a word in a title. Too weak to
    trust ('scup.org' for 'Bayfront Research'); the caller defers to Path C.
    """
    valid = [
        sr for sr in results
        if sr.url and _URL_RE.match(sr.url)
        and not _is_blacklisted(sr.url)
        and _name_overlap(name1, sr)
        and not _wrong_country(sr.url, country)
    ]
    if not valid:
        return [], {}, None

    position = {id(sr): i for i, sr in enumerate(results)}
    ranks: dict[int, int] = {}
    for sr in valid:
        if not _has_host_match(name1, sr.url, record_type):
            ranks[id(sr)] = (
                2 if _geo_rescues_host_miss(name1, sr, city, record_type) else 0
            )
        else:
            ranks[id(sr)] = 1 if _domain_introduces_foreign_brand(name1, sr.url) else 2

    best = min(valid, key=lambda sr: _sort_key(
        sr,
        ranks[id(sr)],
        position.get(id(sr), len(results)),
        record_type=record_type,
        city=city,
    ))
    return valid, ranks, (best if ranks[id(best)] != 0 else None)


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
    city: str | None = None,
) -> dict:
    """Build the per-candidate Path B trace record.

    Purely read-only: it re-evaluates the same pure guards (``_URL_RE``,
    ``_is_blacklisted``, ``_name_overlap``, ``_rank``) that
    ``select_website_from_serp`` used, in the SAME short-circuit order, to
    attribute each candidate's ``rejected_by`` to the FIRST guard that fired.
    It never mutates state or changes what was resolved.
    """
    # The SAME evaluation select_website_from_serp ran, not a copy of it, so
    # the per-candidate `chosen`/`rank` fields cannot drift from the real
    # decision.
    _valid, ranks, chosen_sr = _evaluate_candidates(
        name1, results, record_type, country=country, city=city,
    )

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
                entry["geo_corroborated"] = _geo_corroborated(city, sr)
                entry["geo_rescued"] = bool(
                    rank == 2 and not _has_host_match(name1, sr.url, record_type)
                )
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
        "record_city": city,
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
    city: str | None = None,
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

    *city* is the record's own city, and it decides between campuses. It ranks
    candidates (:func:`_sort_key`) and, for an institution, can restore a
    candidate the host test rejected (:func:`_geo_rescues_host_miss`). Passing
    ``None`` disables both and leaves ranking exactly as it was.

    Candidates rank 0/1/2 on WHERE the name matched (§7b):
      * 2 = distinctive/acronym host match with no foreign brand word (clean),
        or a rank-0 institution candidate restored by locality evidence
      * 1 = host match but the label adds a foreign brand (sub-brand)
      * 0 = the name only overlaps the title, not the host → rejected
    """
    _valid, ranks, best = _evaluate_candidates(
        name1, results, record_type, country=country, city=city,
    )
    if best is None:
        return WebsiteResolution()

    best_rank = ranks[id(best)]
    if record_type == "research_institution":
        # §7c: an authoritative TLD grants HIGH only with a clean host match,
        # and only when the SERP title says the institution's name.
        #
        # The TLD condition alone is not enough. `comptroller.texas.gov` is a
        # clean rank-2 match for "University of Texas" — `extract_domain`
        # reduces the host to `texas.gov`, whose entire label IS one of the
        # name's own words, so `_host_label_covered_by_name` accepts it — and
        # `.gov` then granted it HIGH, i.e. written with no review flag. The
        # Texas state comptroller was the resolver's confident answer for a
        # university the moment the record's city entered the query.
        #
        # An institution states its name in its own page title. A state
        # directory listing it does not ("Texas Colleges and Universities"),
        # and neither does a partial-label coincidence. Where the title is
        # silent the URL is still written — only the clean/flagged call
        # changes.
        # ...or states its acronym. fit.edu's real SERP title is "Florida
        # Tech: www.fit.edu" — the institution's own homepage, which never
        # spells "Florida Institute of Technology" out. `_acronym_in_host` has
        # already matched the host to the initials in full, which identifies
        # the institution at least as strongly as the phrase would; requiring
        # the phrase on top of it would flag every acronym-domain university in
        # the batch. `texas.gov` does not benefit: the initials of "University
        # of Texas" are "UT", not "texas".
        high = (
            best_rank == 2
            and _tld(best.url) in _OFFICIAL_TLDS
            and (
                _name_phrase_in_title(name1, best.title)
                or _acronym_in_host(name1, best.url)
            )
        )
    else:
        high = best_rank == 2
    if not _has_host_match(name1, best.url, record_type):
        # Rank 2 reached on locality evidence rather than on the host itself.
        # That is a weaker claim than the test it stands in for — utep.edu and
        # utsystem.edu clear it on the same SERP — so the URL is written and
        # the record is flagged, never written clean.
        high = False
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
        # Region and city, not country alone. "University of Texas" does not
        # name one university — it names a system of a dozen — and the bare
        # query returns whichever campus Google ranks first (Austin) together
        # with its athletics and merchandise sites. The record's city is the
        # only field that says WHICH campus this row is, and dropping it here
        # is why utep.edu never entered the candidate set at all for a
        # Sun Bowl Dr / El Paso row.
        #
        # The country stays on the end rather than being displaced by the city:
        # a campus town is not unique across countries (Cambridge), and it is
        # the country that keeps the ccTLD gate and the query agreeing.
        parts = [p.strip() for p in (city, state, country) if p and p.strip()]
        return f"{base} {' '.join(parts)}" if parts else base
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
            city=city,
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
                country=gate_country, city=city,
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
                    attempt=attempt, country=gate_country, city=city,
                )))
            return WebsiteResolution()
        chosen = select_website_from_serp(
            name1, results, record_type, country=gate_country, city=city,
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
                city=city,
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
