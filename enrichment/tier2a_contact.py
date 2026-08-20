"""Tier 2A: Contact person lookup on institution website.

Supports two modes:
  MODE A (population) — name2 is null, find the department from contact's page
  MODE B (verification) — name2 exists, verify/correct it against the page
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from config import Settings
from llm.openai_client import OpenAIClient
from llm.prompts import TIER2A_SYSTEM_PROMPT, TIER2A_USER_PROMPT_TEMPLATE
from utils.name_slots import DEPT_SLOTS, slot_label
from search.base import SearchClient, SearchResult
from search.page_fetcher import PageContent, PageFetcher
from utils.cache import BatchCache
from utils.text_utils import is_blank, score_search_result

logger = logging.getLogger(__name__)


_NULLISH_STRINGS = {"null", "none", "n/a", "na", "nil", "undefined", "not recorded"}


def _clean_llm_string(value: object) -> str | None:
    """Return a cleaned LLM string field or None.

    Guards against the common LLM failure of returning the literal
    string 'null' / 'none' / 'n/a' instead of JSON null.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _NULLISH_STRINGS:
        return None
    return text


@dataclass
class Tier2AResult:
    """Outcome of a Tier 2A contact lookup."""
    success: bool = False
    name2_enriched: str | None = None
    name3_enriched: str | None = None
    title: str | None = None
    mode: str | None = None  # "2A_population" or "2A_verification"
    name2_match: str = "not_applicable"  # exact|partial|no_match|unknown
    name2_match_score: float = 0.0
    confidence: str = "none"  # high|medium|low
    source_url: str | None = None
    # Title of the SERP result ``source_url`` came from. Read-only evidence for
    # the domain ownership guard (utils/domain_resolver.py) when the domain has
    # to be derived from this page; it never influences the lookup itself.
    source_title: str | None = None
    # Evidence, not a flag. Output fields Tier 2A tried to settle and left
    # exactly as the record supplied them, because the page disagreed and the
    # extraction was not confident enough to overwrite. `enrichment.flags`
    # turns this into `low-confidence-unchanged`; every other Tier 2A outcome
    # writes a value backed by `source_url` and needs no flag.
    low_conf_unchanged: set[str] = field(default_factory=set)
    enrichment_status: str = "failed"
    source: str = "none"


async def run_tier2a(
    record_id: str,
    contact: str,
    institution: str,
    domain: str | None,
    name2: str | None,
    name3: str | None,
    search_client: SearchClient,
    page_fetcher: PageFetcher,
    llm_client: OpenAIClient,
    cache: BatchCache,
    settings: Settings,
    extra_departments: tuple[str | None, ...] = (),
) -> Tier2AResult:
    """Execute Tier 2A contact person lookup.

    Decision points:
    - name2 is blank → Mode A (population): search for contact, extract dept
    - name2 exists → Mode B (verification): verify/correct name2 via contact page
    """
    mode = "2A_population" if is_blank(name2) else "2A_verification"
    logger.info("[%s] Tier 2A starting in %s mode", record_id, mode)

    result = Tier2AResult(mode=mode)

    # Step 1 — Build SERP queries
    queries = _build_queries(contact, institution, domain)

    # Step 2 — Search and filter for people pages
    candidates = await _search_and_rank(queries, search_client, cache, contact=contact)
    if not candidates:
        logger.info("[%s] Tier 2A: no candidate pages found", record_id)
        return result

    # Step 2b — Defensive name verification on the SERP result itself.
    # Google will return near-miss profiles ('Sarah Knox' for
    # 'Sarah Chen') when the domain has no exact match. Reject any
    # candidate whose URL / title / snippet does not contain BOTH
    # the first name and the surname of the contact. This is purely
    # deterministic and costs nothing (no page fetch, no LLM call).
    verified = _filter_candidates_by_name(candidates, contact)
    logger.info(
        "[%s] Tier 2A: name-verified %d of %d candidates",
        record_id, len(verified), len(candidates),
    )
    if not verified:
        return result

    # Step 3 — Fetch pages and extract with LLM (try top 3)
    for candidate in verified[:3]:
        page_content = await page_fetcher.fetch_page_content(candidate.url)
        if page_content is None or page_content.is_empty():
            logger.info("[%s] Tier 2A: page fetch failed for %s", record_id, candidate.url[:80])
            continue

        # Build a merged page text blob that prepends the authoritative
        # elements (url host / url path / title / h1 / breadcrumb /
        # body). Profile pages like "Sarah Chen | Anesthesia" often
        # name the department ONLY in the <title>. Department subdomains
        # like "ortho.ufl.edu" or "anesthesia.ucsf.edu" carry the
        # department identity in the URL itself — feed the full URL
        # host to the LLM so it can use it when the body doesn't
        # explicitly name the department.
        from urllib.parse import urlparse as _up
        parsed_url = _up(candidate.url)
        page_blob = (
            f"URL host: {parsed_url.netloc}\n"
            f"URL path: {page_content.url_path}\n"
            f"Title: {page_content.page_title}\n"
            f"H1: {page_content.h1}\n"
            f"Breadcrumb: {page_content.breadcrumb}\n"
            f"Body: {page_content.body_text}"
        )

        # Step 4 — LLM extraction
        try:
            extraction = await _extract_affiliation(
                llm_client, contact, institution,
                (name2, name3, *extra_departments),
                page_blob,
            )
        except Exception:
            logger.exception("[%s] Tier 2A: LLM extraction failed", record_id)
            continue

        # Step 5 — Apply extraction results
        if not extraction.get("person_found", False):
            logger.info("[%s] Tier 2A: person not found on page %s", record_id, candidate.url[:80])
            continue

        llm_confidence = extraction.get("confidence", "low")
        if llm_confidence == "low":
            logger.info("[%s] Tier 2A: LLM confidence too low", record_id)
            continue

        # Person found with acceptable confidence — apply Mode A or B logic
        result.source_url = candidate.url
        result.source_title = candidate.title
        result.confidence = llm_confidence
        official_dept = _clean_llm_string(extraction.get("official_dept"))
        official_group = _clean_llm_string(extraction.get("official_group"))
        result.title = _clean_llm_string(extraction.get("title"))

        if mode == "2A_population":
            # MODE A: populate name2 from discovered department
            result = _apply_mode_a(result, official_dept, official_group, llm_confidence)
        else:
            # MODE B: verify/correct existing name2
            result = _apply_mode_b(
                result, name2, official_dept, official_group,
                extraction, settings.fuzzy_match_threshold,
                llm_confidence,
            )

        if result.success:
            logger.info(
                "[%s] Tier 2A success: name2='%s', name3='%s', match=%s",
                record_id,
                result.name2_enriched,
                result.name3_enriched,
                result.name2_match,
            )
            return result

    logger.info("[%s] Tier 2A: all candidates exhausted, falling through", record_id)
    return result


_HONORIFICS = {
    "dr", "dr.", "prof", "prof.", "professor",
    "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "mx", "mx.",
}
_NAME_SUFFIXES = {"md", "md.", "phd", "phd.", "msc", "msc.",
                  "jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def _extract_first_and_last(contact: str) -> tuple[str, str] | None:
    """Return (first_name, surname) from a contact string, or None.

    Both honorifics and academic suffixes are stripped. The first
    remaining token is the given name; the last is the surname.
    """
    clean = _clean_contact_name(contact)
    if not clean:
        return None
    parts = clean.split()
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def _name_appears_in_text(first: str, last: str, text: str) -> bool:
    """True if *text* contains both *first* and *last* as whole words.

    The check is whitespace-insensitive enough to handle URL slugs
    (sarah.chen, sarah-chen, sarah_chen, sarahchen) because the text
    is normalised: ``.``, ``-``, ``_``, ``/`` are turned into spaces
    before matching. Matching is case-insensitive.
    """
    if not first or not last or not text:
        return False
    import re
    normalised = re.sub(r"[._\-/]+", " ", text.lower())
    first_l = first.lower()
    last_l = last.lower()
    has_first = bool(re.search(rf"\b{re.escape(first_l)}\b", normalised))
    has_last = bool(re.search(rf"\b{re.escape(last_l)}\b", normalised))
    # Also accept the concatenated slug form 'sarahchen' / 'chensarah'
    if not (has_first and has_last):
        if (first_l + last_l) in normalised.replace(" ", ""):
            return True
        if (last_l + first_l) in normalised.replace(" ", ""):
            return True
        return False
    return True


def _filter_candidates_by_name(
    candidates: list[SearchResult],
    contact: str,
) -> list[SearchResult]:
    """Drop SERP candidates whose url/title/snippet do not contain
    BOTH the contact's first name and surname as whole words.

    This catches the common failure where Google returns near-miss
    profiles (e.g. 'Sarah Knox' for a 'Sarah Chen' query) on domains
    that have no exact match for the queried name.
    """
    names = _extract_first_and_last(contact)
    if not names:
        # Single-token name — cannot verify, fall back to trusting SERP.
        return candidates
    first, last = names
    out: list[SearchResult] = []
    for sr in candidates:
        haystack = " ".join([sr.url or "", sr.title or "", sr.snippet or ""])
        if _name_appears_in_text(first, last, haystack):
            out.append(sr)
    return out


def _clean_contact_name(contact: str) -> str:
    """Strip honorifics and academic suffixes from a contact name.

    'Prof. Sarah Chen'   → 'Sarah Chen'
    'Dr Robert Kim, MD'  → 'Robert Kim'
    'Jane Smith PhD'     → 'Jane Smith'
    """
    if not contact:
        return ""
    # Normalise commas to spaces so 'Robert Kim, MD' tokenises cleanly
    raw = contact.replace(",", " ")
    tokens = [t for t in raw.split() if t]

    # Drop leading honorifics
    while tokens and tokens[0].lower() in _HONORIFICS:
        tokens.pop(0)

    # Drop trailing academic suffixes
    while tokens and tokens[-1].lower() in _NAME_SUFFIXES:
        tokens.pop()

    return " ".join(tokens)


def _build_queries(contact: str, institution: str, domain: str | None) -> list[str]:
    """Build a single SERP query for contact lookup.

    Contact honorifics are stripped so the quoted name matches the
    page text verbatim. We run exactly ONE query — the on-domain
    quoted-name search — and rely on the top results. This is the
    lowest-cost way to find an institutional profile page. If it
    returns nothing, the contact path is unrecoverable without more
    calls, so we fall through rather than fan out.
    """
    clean = _clean_contact_name(contact) or contact
    if domain:
        return [f'"{clean}" site:{domain}']
    # No domain (Tier1 didn't find one) — single off-domain query
    return [f'"{clean}" "{institution}"']


async def _search_and_rank(
    queries: list[str],
    search_client: SearchClient,
    cache: BatchCache,
    contact: str | None = None,
) -> list[SearchResult]:
    """Execute SERP queries and rank results by people-page signals.

    Ranking priorities (highest wins):
      1. URL path slug contains BOTH the contact's first name and
         surname — this is an exact identity match on a profile page
         (e.g. ``ortho.ufl.edu/darlene-bailey`` for contact
         "Darlene Bailey"). Beats every generic people-page signal.
      2. Snippet starts with the contact's full name — suggests a
         profile/bio page rather than a list of names.
      3. Generic people-page URL signals ("people", "faculty",
         "profile", "staff", "directory", "team", ...).

    This ordering prevents roster pages (which share a "team" URL
    signal) from outranking the actual profile page.
    """
    all_results: list[SearchResult] = []
    seen_urls: set[str] = set()

    for query in queries:
        cached = cache.get_serp(query)
        if cached is not None:
            results = cached
        else:
            results = await search_client.search(query, num_results=5)
            cache.set_serp(query, results)

        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                all_results.append(r)

    names = _extract_first_and_last(contact) if contact else None

    def _rank(sr: SearchResult) -> int:
        score = score_search_result(sr.url, sr.snippet)
        if names:
            first, last = names
            # Exact name in URL path (the identity slug). This must
            # beat all other signals so we fetch the real profile
            # before any list/roster pages.
            import re as _re
            path_norm = _re.sub(
                r"[._\-/]+", " ", (sr.url or "").lower()
            )
            if (
                _re.search(rf"\b{_re.escape(first.lower())}\b", path_norm)
                and _re.search(rf"\b{_re.escape(last.lower())}\b", path_norm)
            ):
                score += 100
            # Title starts with the full name — likely a profile page.
            title_lower = (sr.title or "").strip().lower()
            if title_lower.startswith(f"{first} {last}".lower()):
                score += 20
        return score

    scored = [(_rank(r), r) for r in all_results]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:3]]


async def _extract_affiliation(
    llm_client: OpenAIClient,
    contact: str,
    institution: str,
    departments: tuple[str | None, ...],
    page_text: str,
) -> dict:
    """Use OpenAI to extract person affiliation from page text.

    *departments* is the record's whole department block in slot order, so
    the model sees every unit the record already carries — a lab in Name 4
    is as much context for "which department is this person in" as one in
    Name 2, and withholding it invites a contradictory answer. Name 2 and
    Name 3 keep their own prompt lines (the verification verdict is scored
    against Name 2 by name); the slots below them are appended as extra
    lines, and contribute nothing when the record has none.
    """
    extra = "".join(
        f"Existing {slot_label(slot)}: {value.strip()}\n"
        for slot, value in zip(DEPT_SLOTS[2:], departments[2:])
        if value and value.strip()
    )
    def _shown(value: str | None) -> str:
        return value.strip() if value and value.strip() else "not recorded"

    user_prompt = TIER2A_USER_PROMPT_TEMPLATE.format(
        contact=contact,
        institution=institution,
        name2=_shown(departments[0] if departments else None),
        name3=_shown(departments[1] if len(departments) > 1 else None),
        existing_departments=extra,
        page_text=page_text,
    )
    return await llm_client.extract_json(TIER2A_SYSTEM_PROMPT, user_prompt)


def _apply_mode_a(
    result: Tier2AResult,
    official_dept: str | None,
    official_group: str | None,
    llm_confidence: str,
) -> Tier2AResult:
    """Mode A: name2 was null — populate from discovered department.

    Success: name2_enriched = official_dept, name3_enriched = official_group (bonus)
    """
    if not official_dept or not official_dept.strip():
        return result  # No department found, stay unsuccessful

    result.success = True
    # FIX(Bug 5): never assign empty string to enriched fields
    result.name2_enriched = official_dept.strip() if official_dept and official_dept.strip() else None
    result.name3_enriched = official_group.strip() if official_group and official_group.strip() else None
    result.source = "contact_lookup_found"
    result.name2_match = "not_applicable"  # No pre-existing name2 to compare

    # Either way the department was read off the contact's own page on the
    # institution's domain, and `source_url` records which page. That is
    # auditable evidence, so no review flag is raised at either confidence.
    result.enrichment_status = "enriched"

    return result


def _apply_mode_b(
    result: Tier2AResult,
    existing_name2: str | None,
    official_dept: str | None,
    official_group: str | None,
    extraction: dict,
    fuzzy_threshold: int,
    llm_confidence: str,
) -> Tier2AResult:
    """Mode B: name2 exists — verify or correct via contact page.

    Uses rapidfuzz to compare existing name2 against official_dept from the page.
    ≥ 95 exact: normalise to official format
    80–94 partial: normalise to official format
    < 80: the page disagrees substantially with the record. That has two
        explanations — the record is wrong, or the wrong page was found —
        and this is precisely where the evidence is weakest. Split on the
        extraction confidence (guaranteed "high" or "medium" by the caller's
        entry precondition): only a high-confidence extraction is trusted
        enough to overwrite. A medium-confidence disagreement is reported
        and left for a human, with the record's own value untouched.
    """
    if not official_dept or not official_dept.strip():
        return result

    result.success = True

    # Use LLM-provided match info as primary signal
    llm_match = extraction.get("name2_match", "unknown")
    llm_score = extraction.get("name2_match_score", 0)

    # Also run our own fuzzy match for consistency
    if existing_name2:
        our_score = fuzz.token_sort_ratio(existing_name2, official_dept)
    else:
        our_score = 0.0

    # Prefer the higher score between LLM and our own fuzzy matching.
    #
    # CROSS-SCALE COMPARISON (Fix 10, Step 3). These two numbers are not on the
    # same scale: `llm_score` is the model's own assertion about its own match
    # and `our_score` is a RapidFuzz token_sort_ratio. Taking the max ranks one
    # against the other and then thresholds the winner with a single number
    # (`fuzzy_threshold`, and again at 95), so an LLM saying "92" outranks a
    # measured 88 on no common footing. Left as-is deliberately — Fix 10
    # records what happens, it does not change what happens — and reported so
    # the decision to fix it can be taken on its own terms. The provenance
    # event this write produces carries `llm_self_reported`, which is the scale
    # of whichever number actually won.
    effective_score = max(float(llm_score), our_score)

    if effective_score >= fuzzy_threshold:
        # FIX(Bug 5): guard against empty string
        result.name2_enriched = official_dept.strip() if official_dept and official_dept.strip() else None
        result.name2_match_score = effective_score

        if effective_score >= 95:
            # Near-exact match
            result.name2_match = "exact"
            result.enrichment_status = "verified"
            result.source = "contact_lookup_found"
        else:
            # Partial match — normalise to the page's wording.
            result.name2_match = "partial"
            result.enrichment_status = "enriched"
            result.source = "contact_lookup_found"
    else:
        # No match. Who is wrong — the record, or the page we picked?
        result.name2_match = "no_match"
        result.name2_match_score = effective_score
        if llm_confidence == "high":
            # The extraction is solid, so treat the record as wrong and
            # replace it with the page version.
            # FIX(Bug 5): guard against empty string
            result.name2_enriched = official_dept.strip() if official_dept and official_dept.strip() else None
            result.enrichment_status = "enriched"
            result.source = "contact_lookup_corrected"
        else:
            # Medium confidence on a substantial disagreement is not
            # enough to overwrite a value the customer supplied. Leave
            # name2_enriched unset — the merge layer's non-blank filter
            # then keeps whatever the record already had — and surface
            # the disagreement for manual review instead.
            result.enrichment_status = "unresolved"
            result.low_conf_unchanged.add("name2")
            result.source = "contact_lookup_found"

    # FIX(Bug 5): guard against empty string for name3
    result.name3_enriched = official_group.strip() if official_group and official_group.strip() else None
    return result
