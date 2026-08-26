"""Orchestrator: tier escalation, record_type derivation, and result assembly.

This is the main entry point for enrichment logic.  It instantiates all
clients (or mock clients when MOCK_EXTERNAL_CALLS=true), manages the
per-batch cache, and coordinates asyncio concurrency.

ROR lookup uses a hybrid strategy: ``?affiliation=`` first (handles
abbreviations like "Univ of Florida"), then ``?query=`` with a country
filter as fallback.  Child matching is done locally against the parent's
relationships list using rapidfuzz, saving a second API call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from rapidfuzz import fuzz

from api.models import (
    EnrichmentOptions,
    EnrichmentRecord,
    EnrichmentResponse,
    EnrichmentResult,
    EnrichmentSummary,
)
from config import Settings
from enrichment.address_processing import (
    _normalise_street_value,
    merge_into_result as merge_address_into_result,
    process_address,
)
from enrichment.batch_consensus import apply_batch_consensus
from enrichment.company_canonical import run_company_canonical
from enrichment.lab_resolver import run_lab_resolver
from enrichment.overflow_check import run_overflow_check_block
from enrichment.person_affiliation import run_person_affiliation
from enrichment.search_terms import (
    clean_name2_phrase,
    derive_acronym,
    derive_search_terms,
    extract_dept_core,
)
from enrichment.preprocess import (
    _extract_addresses,
    _location_fragment,
    find_suspicious_plain_names,
    has_multiple_contacts,
    llm_classify_plain_names_async,
    preprocess_record,
)
from dedup.signatures import normalize_key
from enrichment.classifier import TypeEvidence, classify
from enrichment.flags import compute_flags
from enrichment.confidence import (
    SOURCE_INPUT,
    ProvenanceGrammarError,
    parse as parse_provenance,
)
from enrichment.provenance import (
    DERIVED_SCALAR_FIELDS,
    EnrichedRecord,
    Evidence,
    FUZZY_RATIO,
    GUARD_GLEIF_NAME,
    GUARD_PAGE_IDENTITY,
    LLM_SELF_REPORTED,
    ROR_LOCAL,
    ProvenanceLog,
    UNATTRIBUTED_CODE,
    UNATTRIBUTED_REASON,
    deterministic_evidence,
    derived_scalar,
    validate_provenance_strings,
    enforce_admissibility,
    inherited_evidence,
    llm_evidence,
    registry_evidence,
    self_reported_value,
    web_evidence,
)
from enrichment.consistency import (
    SOURCE_KEYS,
    apply_cross_source_gate,
    apply_registry_location_check,
    record_registry_identity,
    registry_agreement_count,
    registry_location_unconfirmed_count,
    reset_consistency_counters,
)
from enrichment.tier1_lei import LEIClient, clear_lei_cache, lei_normalised_hits
from enrichment.tier1_ror import RORClient, clear_ror_cache, ror_normalised_hits
from enrichment.page_corroborator import (
    CONTRADICTED,
    CORROBORATED,
    FETCH_UNAVAILABLE,
    NAME_MISMATCH,
    NO_IDENTITY,
    PARKED,
    Corroboration,
    corroborate,
    location_decides,
    operating_name_provenance,
    page_identifies_record,
)
from enrichment.wikidata import (
    AMBIGUOUS as WIKIDATA_AMBIGUOUS,
    COUNTRY_REJECTED as WIKIDATA_COUNTRY_REJECTED,
    TYPE_REJECTED as WIKIDATA_TYPE_REJECTED,
    UNAVAILABLE as WIKIDATA_UNAVAILABLE,
    WITNESS_PROVENANCE as WIKIDATA_WITNESS_PROVENANCE,
    WikidataClient,
    WikidataOutcome,
    resolve as resolve_wikidata,
    website_agrees as wikidata_website_agrees,
)
from enrichment.tier2_canonical import run_tier2_canonical
from enrichment.tier2a_contact import Tier2AResult, run_tier2a
from enrichment.tier3_llm import Tier3Result, run_tier3
from enrichment.unchanged_state import (
    UNCHANGED_CONFIRMED,
    UNCHANGED_UNRESOLVED,
    UNCHANGED_VERIFIED,
    enrichment_status_for as unchanged_status,
    evidence_for as unchanged_evidence,
    resolve as resolve_unchanged_name1,
)
from enrichment.website_resolver import (
    infer_website_via_llm,
    resolve_website_via_serp,
)
from llm.openai_client import (
    LLM_SEED,
    LLM_TEMPERATURE,
    OpenAIClient,
    install_httpx_aclose_noise_filter,
    seed_supported,
)
from llm.prompts import (
    COMPANY_CANONICAL_PROMPT_VERSION,
    LAB_PARENT_PROMPT_VERSION,
    PERSON_AFFILIATION_PROMPT_VERSION,
    TIER2A_PROMPT_VERSION,
    TIER2B_PROMPT_VERSION,
    TIER2_CANONICAL_PROMPT_VERSION,
    TIER3_PROMPT_VERSION,
)
from search.base import SearchClient
from search.duckduckgo_client import DuckDuckGoClient
from search.page_fetcher import PageFetcher
from search.serpapi_client import SerpAPIClient
from utils.cache import (
    BatchCache,
    SerpCache,
    build_evidence_cache,
    cached_serp,
    current_record_id,
)
from utils.name_slots import (
    ADJACENT_NAME_PAIRS,
    DEPT_ENRICHED_FIELDS,
    DEPT_SLOTS,
    ENRICHED_NAME_FIELDS,
    NAME_SLOTS,
)
from utils.domain_resolver import (
    DomainDecision,
    DomainEvidence,
    canonicalise_host,
    resolve_domain,
    write_domain,
)
from utils.text_utils import (
    canonical_is_spelling_variant,
    canonical_preserves_identity,
    canonicalise_unit_name,
    clean_passthrough_org_name,
    collapse_legal_suffix,
    country_to_iso_code,
    expand_abbreviations,
    extract_domain,
    is_admin_unit,
    is_blank,
    is_granular_unit,
    looks_like_research_institution,
    normalise_case,
    smart_title_case,
    strip_address_fragments,
    strip_parentheticals,
)

logger = logging.getLogger(__name__)

# Diagnostic-only per-record trace of Stage 5, the Tier 1 re-lookup after
# canonicalisation (see config.RETRY_TRACE). One JSON line per finalised
# record, emitted ONLY when the setting is on; with tracing off this logger
# never fires and nothing about the retry changes. Mirrors the
# `enrichment.trace.website` pattern.
retry_trace_logger = logging.getLogger("enrichment.trace.retry")
#: The page lane's trace, shared with `enrichment.page_corroborator` so a
#: reader sees one stream per lane rather than one per module.
page_trace_logger = logging.getLogger("enrichment.trace.page")

#: The mutually exclusive reasons Stage 5 did not query a registry for a
#: record. ``not_called_on_this_path`` is the one that can only mean a wiring
#: defect: the retry was never reached at all, so no decision was taken.
RETRY_SKIP_NOT_CALLED = "not_called_on_this_path"
RETRY_SKIP_ALREADY_ATTEMPTED = "already_attempted"
RETRY_SKIP_ALREADY_HAS_ID = "already_has_id"
RETRY_SKIP_NO_TIER1_QUERY = "other:tier1_never_ran"
RETRY_SKIP_NO_CANONICAL = "other:no_name1"
RETRY_SKIP_NORMALIZE_KEY_EQUAL = "normalize_key_equal"


def _retry_trace_new(record_id: str | None) -> dict[str, Any]:
    """A fresh Stage 5 trace slot for one record.

    Always allocated (it is a handful of keys on a dict that already exists)
    so the emission site can distinguish "the retry ran and decided to skip"
    from "the retry was never reached" — which is precisely the wiring defect
    the trace exists to detect, and which a flag-gated allocation could not
    tell apart from tracing being off.
    """
    return {
        "record_id": record_id,
        "called": False,
        "skipped_reason": None,
        "query_original": None,
        "query_canonical": None,
        "fired": False,
        "registries_queried": [],
        "guard_rejections": [],
        "hit": None,
    }


def _retry_trace(result: Any, record_id: str | None = None) -> dict[str, Any]:
    """The record's Stage 5 trace slot, created on first use."""
    slot = result.get("_retry_trace")
    if slot is None:
        slot = _retry_trace_new(record_id or result.get("record_id"))
        result["_retry_trace"] = slot
    return slot


def _retry_trace_guards(
    trace: dict[str, Any], registry: str, res: dict[str, Any] | None,
) -> None:
    """Copy a registry client's guard rejections into the trace.

    Reads the same ``guard_rejections`` payload ``_log_registry_rejections``
    writes to the provenance log — the trace is a second projection of it, not
    a second source, so a guard named here is the guard that actually ran.
    """
    if not res or not isinstance(res, dict):
        return
    for raw in res.get("guard_rejections") or ():
        trace["guard_rejections"].append({
            "registry": registry,
            "guard": raw.get("guard"),
            "candidate": raw.get("candidate_name"),
            "score": raw.get("score"),
            "threshold": raw.get("threshold"),
            "detail": raw.get("detail"),
        })


# ── Department-domain probe helpers ───────────────────────────────────────────

# Generic words that aren't unit-distinguishing (every department has
# them; matching on them produces false positives).
_DEPT_GENERIC_TOKENS = {
    "department", "dept", "school", "institute", "center", "centre",
    "division", "faculty", "office", "group", "lab", "laboratory",
    "of", "for", "the", "and", "in", "on", "at", "to", "a", "an", "&",
    "research", "studies", "programme", "program",
}

# Subdomains that are administrative/cross-cutting, never a department
# home — pre-empt the SERP probe from latching onto them.
_GENERIC_HOST_PREFIXES = {
    "professorships", "inside", "calendar", "news", "alumni", "admin",
    "hr", "store", "shop", "give", "donate", "support", "events",
    "directory", "library", "libraries", "career", "careers", "jobs",
    "search", "secure", "my", "mail", "email", "wiki", "intranet",
    "media", "press",
    # Newsroom / magazine / marketing hosts an institution publishes its
    # stories on. A story about a department is not that department's home.
    "newsroom", "blog", "blogs", "stories", "story", "magazine", "today",
    "video", "videos", "podcast", "photos", "gallery",
    "giving", "apply", "admissions", "visit", "map", "maps", "about",
    "portal", "login", "auth", "sso", "help", "status", "sitemap",
    "archive", "archives",
}

# Registrable domains that are third-party platforms — never represent
# a department's web home. Used to filter no-site SERP results.
_THIRD_PARTY_DOMAINS = {
    "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com",
    "x.com", "youtube.com", "instagram.com", "reddit.com",
    "researchgate.net", "scholar.google.com", "google.com",
    "amazon.com", "indeed.com", "glassdoor.com", "pubmed.gov",
    "ncbi.nlm.nih.gov", "nih.gov", "doi.org", "academia.edu",
    "github.com", "github.io", "medium.com", "substack.com",
}


def _is_third_party_host(host: str) -> bool:
    """True if *host*'s registrable domain is a known third-party
    platform (Wikipedia, LinkedIn, etc.). Such hosts should never be
    accepted as a department_domain regardless of content match."""
    parts = host.split(".")
    if len(parts) >= 2:
        last_two = ".".join(parts[-2:])
        if last_two in _THIRD_PARTY_DOMAINS:
            return True
    return False

def _host_prefix_is_generic(host: str, base: str | None = None) -> bool:
    """True when *host* is an administrative / newsroom subdomain rather
    than a department home (``news.mit.edu``, ``events.stanford.edu``).

    :func:`_score_dept_candidate` already caps such hosts at 0, but the
    stage-0 GET probe, the stage-2b path-page scan and the cross-domain
    SERP fallback accept candidates without scoring them — a story URL
    like ``news.mit.edu/2026/chemistry-…`` would otherwise verify (the
    page does discuss the department) and, once the path is dropped by
    ``canonicalise_host``, ship as ``https://news.mit.edu``. This is the
    one guard all four acceptance paths share.

    Only a *subdomain* label is judged: with *base* known the base is
    stripped first, otherwise a host is treated as a subdomain only when
    it carries more than two labels, so a registrable domain that happens
    to be named ``press.org`` is left alone.
    """
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    base = (base or "").strip().lower().rstrip(".")
    if base and host.endswith("." + base):
        prefix = host[: -len("." + base)]
    elif host.count(".") >= 2:
        prefix = host.split(".", 1)[0]
    else:
        return False                    # bare registrable domain — no prefix
    first_seg = prefix.split(".", 1)[0]
    return first_seg in _GENERIC_HOST_PREFIXES


# §5b: path segments that are non-department content (news, events, archived
# stories, calendars). A candidate whose path contains one of these is not a
# department landing page. Mirrors _GENERIC_HOST_PREFIXES but for the path.
_GENERIC_PATH_SEGMENTS = {
    "news", "news-events", "events", "event", "story", "stories",
    "article", "articles", "blog", "calendar", "archive", "colloquium",
    "seminar", "admin", "hr", "library", "libraries", "careers", "career",
    "directory", "media", "press",
}
# §5c: sub-pages of a department (penalised, not rejected — the landing page is
# preferred over "…/chemistry/undergrad/").
_SUBPAGE_PATH_SEGMENTS = {
    "undergrad", "undergraduate", "graduate", "grad", "people", "faculty",
    "staff", "contact", "admissions", "apply", "courses", "alumni", "giving",
}
_YEAR_SEG_RE = re.compile(r"^\d{4}$")


def _path_is_generic(path: str) -> bool:
    """True when any path segment is non-department content (§5b)."""
    return any(
        seg.lower() in _GENERIC_PATH_SEGMENTS
        for seg in (path or "").split("/") if seg
    )


def _path_canonicality_penalty(path: str) -> int:
    """§5c: a penalty (≥0) for deep / dated / sub-page paths, so a department
    landing page outranks an archived or sub-section page at the same host."""
    segs = [s for s in (path or "").split("/") if s]
    penalty = max(0, len(segs) - 1)              # prefer shallower
    if any(_YEAR_SEG_RE.match(s) for s in segs):  # dated / archive content
        penalty += 5
    if any(s.lower() in _SUBPAGE_PATH_SEGMENTS for s in segs):
        penalty += 3
    return penalty


_TOKEN_RE = re.compile(r"[A-Za-z]{3,}")


def _significant_dept_tokens(text: str) -> set[str]:
    """Pull unit-distinguishing tokens from *text* (cleaned name2).

    Lowercased alpha words ≥3 chars, minus generic descriptors. The
    result is what we expect to see in a real department URL.
    """
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if t.lower() not in _DEPT_GENERIC_TOKENS
    }


def _seg_matches_needle(seg: str, needle: str) -> bool:
    """Match a host segment against a dept token/acronym, allowing the
    common abbreviation case where the subdomain is a prefix of the token
    ("chem" ← "chemistry", "phys" ← "physics") or vice versa.
    """
    seg = (seg or "").lower()
    needle = (needle or "").lower()
    if not seg or not needle:
        return False
    if needle in seg:               # substring (e.g. "cs" in "csail")
        return True
    # Shared leading prefix of ≥3 chars — abbreviation either direction.
    return (
        min(len(seg), len(needle)) >= 3
        and (needle.startswith(seg) or seg.startswith(needle))
    )


def _score_dept_candidate(
    host: str,
    base: str,
    path: str,
    title: str,
    tokens: set[str],
    acronym: str | None,
) -> int:
    """Score a candidate URL host/path/title against the dept tokens
    and acronym.

    Strict rule: the host prefix MUST contain a significant token or
    the acronym (substring match) for any positive score. Path / title
    matches alone aren't enough — that's how parent hosts like
    ``fas.harvard.edu`` (FAS) or ``krieger.jhu.edu`` (umbrella school)
    were sneaking in.

    +3 host_prefix substring-contains a token/acronym
    +1 path substring-contains a token/acronym
    +1 title substring-contains a token/acronym
    0  generic admin host (blocklist) — capped regardless of signals
    0  host prefix doesn't contain anything → not a department host
    """
    needles: set[str] = set(tokens)
    if acronym and len(acronym) >= 2:
        needles.add(acronym.lower())
    if not needles:
        return 0

    host_prefix = host
    if host.endswith("." + base):
        host_prefix = host[: -len("." + base)]
    first_seg = host_prefix.split(".", 1)[0]
    if first_seg in _GENERIC_HOST_PREFIXES:
        return 0

    # The first host segment must match a token/acronym — allowing the
    # abbreviation case ("chem" ← "chemistry") so departments that use a
    # shortened subdomain are not rejected.
    if not any(_seg_matches_needle(first_seg, n) for n in needles):
        return 0

    score = 3
    path_lower = (path or "").lower()
    title_lower = (title or "").lower()
    # §5b/§5c: only reward a path match on a real department path — a generic
    # (news/events) path earns no bonus and a deep/dated path is penalised.
    if any(n in path_lower for n in needles) and not _path_is_generic(path):
        score += 1
        score -= min(2, _path_canonicality_penalty(path))
    if any(n in title_lower for n in needles):
        score += 1
    return score


# ── Result helpers ────────────────────────────────────────────────────────────

def _init_result(record: EnrichmentRecord) -> EnrichedRecord:
    """Create a blank result record with originals populated.

    The six Phase 1 scoped fields (``name1_enriched``, ``name2_enriched``,
    ``domain``, ``record_type``, ``ror_id``, ``lei_id``) are seeded empty and
    are write-locked from here on: the only way any of them takes a value is
    ``result.write(field, value, evidence)``. Seeding is not writing — an empty
    field asserts nothing and so has nothing to attribute.
    """
    # Map the legacy single 'street' input into street1 if street1 is blank.
    street1_original = record.street1 or record.street
    return EnrichedRecord.initialise({
        "record_id": record.record_id,
        # SAP master-data columns carried through verbatim (not enriched).
        "ecc_customer_number": record.ecc_customer_number,
        "central_deletion_flag": record.central_deletion_flag,
        "comments": record.comments,
        "account_group": record.account_group,
        "company_code": record.company_code,
        "sales_organization": record.sales_organization,
        "distribution_channel": record.distribution_channel,
        "division": record.division,
        "country_region_key": record.country_region_key,
        "postal_code": record.postal_code,
        "city": record.city,
        "region": record.region,
        "language_key": record.language_key,
        "reconciliation_acct": record.reconciliation_acct,
        "tax_jurisdiction": record.tax_jurisdiction,
        "central_delivery_block": record.central_delivery_block,
        "delivery_priority": record.delivery_priority,
        "shipping_conditions": record.shipping_conditions,
        "delivering_plant": record.delivering_plant,
        "created_on": record.created_on,
        "created_by": record.created_by,
        "vat_registration_no": record.vat_registration_no,
        "terms_of_payment": record.terms_of_payment_contact,
        **{f"{slot}_original": getattr(record, slot, None) for slot in NAME_SLOTS},
        **{f"{slot}_enriched": None for slot in NAME_SLOTS},
        **{f"{slot}_changed": False for slot in NAME_SLOTS},
        # Both derived in finalise(), from enriched values only. The input's
        # own Search Term 1/2 are deliberately not carried here: they are
        # pre-enrichment customer text and must not survive into the output
        # of a record whose names we just corrected.
        "search_term_1": None,
        "search_term_2": None,
        "department_domain": None,
        # Internal carrier — populated when ROR returns an acronym variant.
        # Stripped out in finalise() so it doesn't leak into the response.
        "_ror_acronym": None,
        "care_of_original": record.care_of,
        "care_of_enriched": None,
        "care_of_changed": False,
        "contact_original": record.contact,
        "contact_enriched": None,
        "contact_changed": False,
        "email_original": record.email,
        "email_enriched": None,
        "email_changed": False,
        "street1_original": street1_original,
        "street1_changed": False,
        # Passed through verbatim — enrichment never alters the house number.
        "house_number": record.house_number,
        "street2_original": record.street2,
        "street2_changed": False,
        "street3_original": record.street3,
        "street3_changed": False,
        "street4_original": record.street4,
        "street4_changed": False,
        "street5_original": record.street5,
        "street5_changed": False,
        # Address Stage 1 outputs — populated by _run_address_stage.
        "street_cleaned": None,
        "street_2_cleaned": None,
        "street_3_cleaned": None,
        "street_4_cleaned": None,
        "street_5_cleaned": None,
        "suite": None,
        "building": None,
        "floor": None,
        "room": None,
        "unit": None,
        "mail_stop": None,
        "po_box_extracted": None,
        "unloading_point": None,
        "mail_code": None,
        "unclear_address_info": None,
        "address_issues": [],
        "record_type": "unknown",
        # Provisional type. Drives branch selection and tier gating during the
        # run; internal only, never serialised. The final `record_type` is
        # decided once in finalise() by enrichment.classifier — see the module
        # docstring there for why the two cannot be the same value.
        "routing_type": "unknown",
        "tier_used": 1,
        "tier2_mode": None,
        "confidence": "none",
        "source": "none",
        "ror_id": None,
        "lei_id": None,
        "source_url": None,
        "domain": None,
        "website_url": None,
        "contact_used": False,
        "name2_match_result": "not_applicable",
        "use_cases_triggered": [],
        # Written once, by enrichment.flags.compute_flags, at the end of
        # finalise. No tier touches them; tiers leave `_ev_*` evidence behind
        # and finalisation decides what it means.
        "flag_for_review": False,
        "flag_codes": [],
        "flagged_fields": [],
        "flag_reason": None,
        "enrichment_status": "failed",
        "duration_ms": 0,
        "error": None,
    })


def _record_domain_hint(result: dict[str, Any], record: Any = None) -> str | None:
    """The domain the RECORD itself already carries, for Fix C(3).

    One of the two signals allowed to corroborate a collision-prone registry
    name match. Two places it can come from, in order of how much they claim:
    a domain the pipeline has already accepted through the ownership guard,
    and the host of an email address the record supplied. An email host is
    weak evidence of a website but strong evidence of *which organisation this
    record is about*, which is the only question being asked here.
    """
    accepted = (result.get("domain") or "").strip()
    if accepted:
        return accepted
    email = (
        result.get("email_enriched")
        or result.get("email_original")
        or getattr(record, "email", None)
        or ""
    )
    email = str(email).strip()
    if "@" not in email:
        return None
    host = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    return host or None


def _write_registry_name(
    result: dict[str, Any],
    field: str,
    value: str | None,
    registry: str,
    *,
    identifier: str | None = None,
    score: float | None = None,
    scale: str | None = None,
    rule_id: str | None = None,
) -> None:
    """Write a registry's official name into an output name field and record
    that the field is registry-owned.

    A verified match — one that has already passed ROR's country and
    distinctive-token guards, or GLEIF's ``token_sort_ratio`` guard — is
    authoritative for the name as well as for the identifier. There is no
    second threshold: if the match was good enough to attach ``ror_id`` /
    ``lei_id``, it is good enough to attach the name. Holding a verified
    registry identifier while displaying the abbreviated SAP input ("Mayo
    Clinic FLA" against ror.org/03zzw1w08) is never correct.

    ``_registry_name_fields`` is transient (dropped in :func:`finalise`) and
    tells the abbreviation-expansion pass there to leave this value alone —
    a registry name is the authority on its own spelling and must not be
    re-processed.
    """
    if not (value and value.strip()):
        return
    # ``identifier`` is the evidence_ref — the registry id a reviewer opens to
    # check the name. A registry-supplied name is exact by definition: the
    # scored comparison happened upstream, at the match, and its score is
    # recorded on the identifier's own event rather than re-asserted here.
    _write(
        result, f"{field}_enriched", value.strip(),
        registry_evidence(
            registry.lower(), identifier,
            score=score, scale=scale,
            rule_id=rule_id or f"registry-name:{registry.lower()}",
        ),
    )
    result.setdefault("_registry_name_fields", set()).add(field)
    logger.info({
        "record_id": result.get("record_id"),
        "step": "registry_name_write",
        "field": field,
        "registry": registry,
        "value": value.strip(),
    })


def _write(result: Any, field: str, value: Any, evidence: Evidence) -> None:
    """Write one field, attributing it when the field is in Phase 1 scope.

    A single funnel so the tier code reads the same whichever field it is
    writing. Scoped fields go through ``EnrichedRecord.write`` and raise
    without evidence; the name slots below Name 2 are out of Phase 1 scope and
    are written directly, which is the one line that changes when the scope is
    extended.
    """
    result.write(field, value, evidence)


# ── Output normalisation — one function, every exit path ──────────────────────

# Name fields. A short upper-case token defaults to an acronym here ("HCA",
# "UCI"), which is the long-standing behaviour of `smart_title_case`.
_CASE_NAME_FIELDS = ENRICHED_NAME_FIELDS
# Address / person fields. A short upper-case token defaults to a WORD here
# ("WAY", "OAK", "DR"), because that is what it almost always is in a street,
# a city or a person's name.
_CASE_TEXT_FIELDS = (
    "care_of_enriched", "contact_enriched",
    "street_cleaned", "street_2_cleaned", "street_3_cleaned",
    "street_4_cleaned", "street_5_cleaned",
    "city", "po_box_extracted",
)


def normalise_output_fields(result: dict[str, Any]) -> dict[str, Any]:
    """Apply output casing to every field that carries free text.

    THE single normalisation entry point. Called at the end of :func:`finalise`
    — which every orchestrator return path funnels through, the UC 0 overflow
    early return included — and directly on the batch-level fail-safe path in
    :meth:`Orchestrator.enrich_batch`, which builds a result without running
    the pipeline and so never reaches ``finalise``.

    Casing only. No flag, reason, tier decision or field routing is touched,
    and no character is added or removed — see
    :func:`utils.text_utils.normalise_case`.

    A registry-owned name is skipped, exactly as the abbreviation-expansion
    pass above skips it: ROR and GLEIF are the authority on their own spelling,
    and title-casing "Massachusetts Institute of Technology" would yield
    "Massachusetts Institute Of Technology". Code fields (Country/Region Key,
    Region, Language Key, Postal Code, Account group, Customer) are never
    touched — they are codes, and their case is meaningful.
    """
    registry_named = result.get("_registry_name_fields") or set()
    for field in _CASE_NAME_FIELDS:
        if field[: -len("_enriched")] in registry_named:
            continue
        val = result.get(field)
        if val:
            _cased = normalise_case(str(val), mode="name")
            if isinstance(result, EnrichedRecord):
                result.transform(field, _cased, rule_id="rule7:output-casing")
            else:
                result[field] = _cased
    for field in _CASE_TEXT_FIELDS:
        val = result.get(field)
        if val:
            result[field] = normalise_case(str(val), mode="text")
    # An email address is case-insensitive by RFC and lower case by convention;
    # "ORDERS@MERIDIANLABS.COM" is never the right output form.
    email = result.get("email_enriched")
    if email:
        result["email_enriched"] = str(email).lower()
    return result


#: Every column that carries a Provenance Scheme B string. The six derived
#: scalars plus `operating_name_provenance`, which is written directly by the
#: page corroborator and the Wikidata witness path rather than derived from a
#: provenance event — and which is in the grammar's scope exactly like the
#: rest of them.
PROVENANCE_COLUMNS: tuple[str, ...] = (
    *DERIVED_SCALAR_FIELDS.values(),
    "operating_name_provenance",
)


def _scoped_scalars(result: Any) -> dict[str, str | None]:
    """The six derived scalar columns for *result*, regenerated from its log.

    Provenance Scheme B — ``source:confidence[+witness]``: ``ror:verified``,
    ``input:verified+web``, ``llm:provisional``, ``web:acme.com:provisional``.
    A null field carries a null scalar: there is no value to attribute. The
    grammar and the one confidence decision are in
    :mod:`enrichment.confidence`; the adapter that maps a recorded event onto
    it is :func:`enrichment.provenance.situation_for`.
    """
    log = result.provenance
    scalars: dict[str, str | None] = {}
    for field, column in DERIVED_SCALAR_FIELDS.items():
        value = result.get(field)
        scalars[column] = (
            derived_scalar(log, field, result)
            if value not in (None, "")
            else None
        )
    return scalars


def _raise_unattributed_flag(result: Any, fields: list[str]) -> None:
    """Add the gate's flag to a record whose flags were already computed.

    ``compute_flags`` is the single flag authority and runs earlier in
    ``finalise`` — before the classifier and the output casing, both of which
    write scoped fields. The gate can only run after those, so rather than move
    the flag decision it appends this one code, which is the only code that can
    be raised after ``compute_flags`` has run.
    """
    codes = list(result.get("flag_codes") or [])
    if UNATTRIBUTED_CODE not in codes:
        codes.append(UNATTRIBUTED_CODE)
    flagged = sorted(set(result.get("flagged_fields") or []) | set(fields))
    result["flag_codes"] = codes
    result["flagged_fields"] = flagged
    result["flag_for_review"] = True
    prose = f"{', '.join(fields)}: {UNATTRIBUTED_REASON}"
    existing = result.get("flag_reason")
    result["flag_reason"] = f"{existing}; {prose}" if existing else prose


def finalise(result: dict[str, Any], start: float) -> dict[str, Any]:
    """Apply empty-string guards and compute changed flags.

    FIX(Bug 5): enriched name fields must NEVER be empty string "".
    They must be a non-empty string or None.

    FIX(Bug 8): changed flags are True only when enriched is not None
    AND enriched differs from original.
    """
    for field in ENRICHED_NAME_FIELDS:
        val = result.get(field)
        if val is not None and not str(val).strip():
            result.transform(field, None, rule_id="bug5:empty-string-guard")

    # Item 6c: a department slot that was blank in the input and was
    # populated ONLY by Tier 3 (LLM inference) is a guess. Require high
    # confidence, otherwise return null rather than emit a fabricated unit
    # (e.g. "St. Louis Site" invented from nothing). Applies to every slot
    # below Name 1 — a fabricated Name 4 is no more defensible than a
    # fabricated Name 2.
    if result.get("tier_used") == 3 and str(
        result.get("confidence") or ""
    ).lower() != "high":
        for _slot in DEPT_SLOTS:
            _orig = result.get(f"{_slot}_original")
            if (
                result.get(f"_{_slot}_from_tier3")
                and result.get(f"{_slot}_enriched")
                and not (_orig and str(_orig).strip())
            ):
                logger.info(
                    "[%s] Tier 3 %s guess dropped (input blank, confidence=%s): %r",
                    result.get("record_id"), _slot, result.get("confidence"),
                    result.get(f"{_slot}_enriched"),
                )
                # No flag: the input slot was blank and the output slot is
                # blank. Nothing was dropped and nothing is uncertain — the
                # record simply has no unit there, which is not a defect.
                # Recorded as a write, not a transform: dropping the value is
                # a decision about the field, and the log is what shows a
                # reviewer that Tier 3 offered one and the rule refused it.
                _write(
                    result, f"{_slot}_enriched", None,
                    deterministic_evidence(
                        "item6c:tier3-guess-dropped",
                        producer="finalise", tier=3,
                        evidence_ref={
                            "dropped": result.get(f"{_slot}_enriched"),
                            "confidence": result.get("confidence"),
                        },
                    ),
                )

    # Normalise Name 1 when it was passed through uncanonicalised (a ROR miss
    # left the raw source value — often ALL-CAPS and abbreviated, e.g. "LARGO
    # MEDICAL CTR", "UNIVERSTIY OF FLORIDA"). Title-case + expand abbreviations
    # so passthrough rows are consistent with ROR-matched ones. ROR / LLM
    # canonical names are never ALL-CAPS, so for those we only run the (no-op
    # on mixed-case) title-case as a safety net and never touch their wording.
    name1_val = result.get("name1_enriched")
    if name1_val:
        if result.get("source") == "passthrough":
            result.transform(
                "name1_enriched", clean_passthrough_org_name(name1_val),
                rule_id="rule7:passthrough-org-name-cleanup",
            )
        else:
            result.transform(
                "name1_enriched", smart_title_case(name1_val) or name1_val,
                rule_id="rule7:smart-title-case",
            )

    # Expand organisational abbreviations in the OUTPUT name fields. Before
    # Fix 4 `expand_abbreviations` only ever reached an output name via
    # `clean_passthrough_org_name` (name1, and only when source ==
    # "passthrough") and via `canonicalise_unit_name` (name2..N, and only when
    # the value is a "<Unit> of X" construction), so "FL State Univ" and
    # "Cardinal Research GRP" shipped verbatim from every other path.
    #
    # This is the GLOBAL map only. The ROR-local `_INSTITUTION_ACRONYMS` /
    # `_US_STATE_ABBREVS` maps stay where they are — they exist to improve ROR
    # resolution and must never touch an output name or a search term.
    #
    # A name written from a registry is skipped: ROR and GLEIF are the
    # authority on their own spelling, and re-processing a verified official
    # name could only corrupt it.
    registry_named = result.get("_registry_name_fields") or set()
    for field in NAME_SLOTS:
        if field in registry_named:
            continue
        val = result.get(f"{field}_enriched")
        if val:
            result.transform(
                f"{field}_enriched", expand_abbreviations(val) or val,
                rule_id="fix4:expand-abbreviations",
            )

    # Guarantee the short legal form on the final output regardless of source
    # (input passthrough, ROR, GLEIF, or LLM): "… Aktiengesellschaft" → "… AG",
    # "… Incorporated" → "… Inc". Preprocess (UC 17) already does this on the
    # input; this backstops any long form a downstream tier introduces.
    for field in ENRICHED_NAME_FIELDS:
        val = result.get(field)
        if val:
            result.transform(
                field, collapse_legal_suffix(val),
                rule_id="uc17:collapse-legal-suffix",
            )

    # Canonicalise academic unit names on the department slots only. name1
    # (the institution) is never a "Department of X", so we leave
    # it alone. This collapses "Chemistry Department",
    # "Dept of Chemistry", "Department of Chemistry" all to
    # "Department of Chemistry".
    # UC 5 scope: never canonicalise granular units (lab/group/
    # centre/facility) — leave them verbatim.
    for field in DEPT_ENRICHED_FIELDS:
        val = result.get(field)
        if val and not is_granular_unit(val):
            canonical = canonicalise_unit_name(val)
            if canonical and canonical != val:
                result.transform(
                    field, canonical, rule_id="uc5:canonicalise-unit-name",
                )

    # A named building lifted out of a name field (see preprocess) fills the
    # Building output only when the address stage did not already extract one
    # from the street fields.
    pp_building = result.pop("_pp_building", None)
    if pp_building and not result.get("building"):
        result["building"] = pp_building

    preprocess_cleared = result.get("_preprocess_cleared") or set()

    # Passthrough: if no tier enriched a department slot but the record had
    # one, retain the original value — UNLESS preprocessing deliberately
    # cleared the field (e.g. extracted an email, address, contact
    # name). Enrichment must never silently drop user-supplied fields
    # but must also respect preprocessing's decision to empty a field.
    for field in DEPT_SLOTS:
        if result.get(f"{field}_enriched") is None and field not in preprocess_cleared:
            orig = result.get(f"{field}_original")
            if orig and str(orig).strip():
                # The input value IS the producer here. Attributing it to the
                # record itself is what separates "nothing found it, so the
                # supplied value stands" from "a tier confirmed it".
                _write(
                    result, f"{field}_enriched", str(orig).strip(),
                    deterministic_evidence(
                        "passthrough:input-retained",
                        producer="input",
                        evidence_ref={"input_field": f"{field}_original"},
                    ),
                )

    # Passthrough for care_of / contact / email / street fields: any
    # field that preprocessing / tiers did not touch retains its
    # original value in the enriched slot. This means "enriched"
    # always reflects the final state after the pipeline runs, not
    # only what changed.
    for base in ("care_of", "contact", "email", "street1", "street2",
                 "street3", "street4", "street5"):
        if result.get(f"{base}_enriched") is None:
            orig = result.get(f"{base}_original")
            if orig and str(orig).strip():
                result[f"{base}_enriched"] = str(orig).strip()

    # Address-in-name safety net. By this point a street address can still
    # be sitting in a name field: a tier wrote one into a department slot AFTER
    # preprocessing's UC 9 ran, or the passthrough above restored an
    # address-bearing original. The address stage handles the common case,
    # but it runs before this passthrough — so re-check the FINAL name
    # values here and pull any embedded street address into the first empty
    # street output slot. name1 (the institution) is never touched. Only
    # rewrite a name field when every fragment finds a slot, so a record
    # with all street slots full never silently drops part of an address.
    _street_out = ("street_cleaned", "street_2_cleaned", "street_3_cleaned",
                   "street_4_cleaned", "street_5_cleaned")
    for _nf in DEPT_ENRICHED_FIELDS:
        _nval = result.get(_nf)
        if not (_nval and str(_nval).strip()):
            continue
        # A value that is purely address sub-locations ("Wing C", "Annex D
        # Pod 2", "Floor 3 Room 12") is moved verbatim as one unit — these
        # are not street addresses, so _extract_addresses misses them. Mirror
        # preprocessing: location fragment first, then embedded addresses.
        _frag = _location_fragment(str(_nval))
        if _frag:
            _slot = next((s for s in _street_out if not result.get(s)), None)
            if _slot is not None:
                result[_slot] = _normalise_street_value(_frag) or _frag
                _write(
                    result, _nf, None,
                    deterministic_evidence(
                        "uc9:location-fragment-moved-to-street",
                        producer="finalise",
                        evidence_ref={"moved": _frag, "into": _slot},
                    ),
                )
            continue
        _addrs, _cleaned = _extract_addresses(str(_nval))
        if not _addrs:
            continue
        _placed_all = True
        for _addr in _addrs:
            _slot = next((s for s in _street_out if not result.get(s)), None)
            if _slot is None:
                _placed_all = False
                break
            result[_slot] = _normalise_street_value(_addr) or _addr
        if _placed_all:
            _write(
                result, _nf, _cleaned or None,
                deterministic_evidence(
                    "uc9:address-in-name-extracted",
                    producer="finalise",
                    evidence_ref={"addresses": list(_addrs)},
                ),
            )

    # UC 11 safety net: if preprocessing rewrote a DBA variant in a name
    # field, the preprocessed value IS the canonical form. Restore it
    # over anything a downstream tier (company_canonical, tier2_canonical,
    # tier3) wrote — those LLMs treat DBA as noise and strip it, but the
    # marker is user intent (legal name vs. trading name).
    dba_values = result.get("_dba_values") or {}
    for base, preprocessed in dba_values.items():
        if preprocessed and result.get(f"{base}_enriched") != preprocessed:
            _write(
                result, f"{base}_enriched", preprocessed,
                deterministic_evidence(
                    "uc11:dba-marker-is-user-intent",
                    producer="preprocess",
                    evidence_ref={"overrode": result.get(f"{base}_enriched")},
                ),
            )

    # Bracketed spans dropped from every OUTPUT name field — the same rule
    # preprocessing (UC 12) applies to the input, backstopping every value a
    # tier introduced after that ran. This is where "3M (Detroit)" and
    # "3M Corporate (Saint Paul)" are caught: ROR and GLEIF append a
    # disambiguating city or country to distinguish same-named records, and
    # that suffix is an artefact of THEIR keyspace, not part of the
    # organisation's name.
    #
    # A registry-owned name is NOT skipped here, unlike the abbreviation and
    # casing passes. Those defer to ROR/GLEIF on spelling; this rule removes a
    # span that is not spelling at all, and the registries are exactly the
    # source that adds it.
    for field in ENRICHED_NAME_FIELDS:
        val = result.get(field)
        if not val:
            continue
        stripped = strip_parentheticals(str(val))
        if stripped != val:
            result.transform(
                field, stripped, rule_id="uc12:strip-parentheticals",
            )

    # Post-tier dedup of the department slots. Preprocess already deduped, but
    # the tiers (especially the Tier 3 LLM) can set two department slots to the
    # same unit — or a near-duplicate/typo — AFTER that ran. Collapse
    # equivalent enriched name slots here (canonical + fuzzy match) and pack
    # the survivors leftward so a duplicate never reaches the output.
    def _name_norm(v: Any) -> str:
        if not v or not str(v).strip():
            return ""
        canon = canonicalise_unit_name(str(v)) or str(v)
        return re.sub(r"\s+", " ", canon.strip()).lower()

    kept_vals: list[Any] = []
    kept_norms: list[str] = []
    for f in DEPT_ENRICHED_FIELDS:
        val = result.get(f)
        n = _name_norm(val)
        if not n:
            continue
        if any(n == kn or fuzz.ratio(n, kn) >= 92 for kn in kept_norms):
            continue
        kept_vals.append(val)
        kept_norms.append(n)
    for i, f in enumerate(DEPT_ENRICHED_FIELDS):
        _packed = kept_vals[i] if i < len(kept_vals) else None
        if _packed == result.get(f):
            continue
        # A transform, not a write: packing decides WHICH SLOT a value sits
        # in, never what the value is. Attribution stays with whatever
        # produced it, which is what keeps a Tier 3 department flagged after
        # the slots are repacked around it.
        _write(
            result, f, _packed,
            deterministic_evidence(
                "dept-slot-dedup-and-pack",
                producer="finalise",
                evidence_ref={"replaced": result.get(f)},
                kind="transform",
            ),
        )

    # Compute all changed flags.
    #
    # Rule 5 + Fix 5 option (b): a difference that is ONLY letter case is not a
    # modification. The flag answers "did enrichment change this field's
    # content", and output casing (Rule 7) is applied to every free-text field
    # on every record — counting it here would set the flag on nearly every row
    # and destroy the flag's meaning. `Mayo Clinic FLA` -> `Mayo Clinic in
    # Florida` is still True; `GAINESVILLE MEDICAL` -> `Gainesville Medical`
    # is not. Nothing else about the rule changes: the value must still be
    # non-None and still differ from the original.
    def _changed(field: str) -> bool:
        enr = result.get(f"{field}_enriched")
        orig = result.get(f"{field}_original")
        if not enr or enr == orig:
            return False
        return str(enr).casefold() != str(orig or "").casefold()

    for f in (*NAME_SLOTS, "care_of", "contact",
              "email", "street1", "street2", "street3", "street4", "street5"):
        result[f"{f}_changed"] = _changed(f)

    # Domain fallback: if ROR didn't supply a domain but a successful
    # tier produced a source_url, offer its host as a candidate. Tier 2A's
    # URLs are on-domain by construction (the contact's faculty page);
    # Tier 2B's may not be — an investor-relations or admissions sub-site is
    # not the organisation's domain — so the candidate goes through the same
    # ownership guard as every other write path.
    if not result.get("domain") and result.get("source_url"):
        _apply_domain(
            result,
            result["source_url"],
            serp_title=result.get("_source_title"),
            serp_h1=result.get("_source_h1"),
            serp_url=result.get("source_url"),
        )

    # Fix 2 — which of the three unchanged states Name 1 is in, decided once,
    # from the settled record. Must run AFTER the domain fallback above (the
    # accepted domain is one of the two corroborating evidence classes) and
    # BEFORE compute_flags, which is the thing it feeds.
    _resolve_unchanged_name1(result)

    # ── Fix D — the cross-source consistency gate ─────────────────────────
    # Before the flag decision, because the flag has to describe what this
    # did; before the search-term derivation, because Fix D(3) requires the
    # terms to come from the identity that SURVIVED rather than from a
    # registry entity that lost. After everything that can write an identity,
    # including the Stage 5 retry and the page read.
    #
    # No record ships two contradictory identities. See
    # enrichment/consistency.py.
    # The same threshold GLEIF's own name-verification guard uses, read the
    # way `tier1_ror` reads ROR_CONFIDENCE_THRESHOLD — `finalise` is a module
    # function with no Settings in hand, and a NEW threshold for this
    # comparison is exactly what the fix forbids.
    apply_cross_source_gate(
        result, float(os.getenv("LEI_NAME_MATCH_THRESHOLD", "88")),
    )
    apply_registry_location_check(result)

    # THE flag decision, taken once, here. Every name, contact and domain
    # field above has settled and the `*_changed` flags are computed, so the
    # codes describe the state the record ended in rather than the tiers that
    # ran to get there. Ordering against the rest of finalise: after the
    # domain fallback (which is the last thing that can raise
    # `_domain_unverified`) and before `_registry_name_fields` is stripped
    # below, which is what tells compute_flags that a name is registry-owned
    # and so not an unverified inference.
    compute_flags(result)

    # Compact search handles for downstream consumers. Runs here, near the end
    # of finalise, so the derivation sees ONLY settled post-enrichment values:
    # every name slot has been canonicalised, deduped and passed through, the
    # domain fallback above has run, and the department probe has written
    # department_domain (see _probe_department_url — it doesn't share
    # source_url with the tiers). Nothing pre-enrichment feeds either term.
    result["search_term_1"], result["search_term_2"] = derive_search_terms(result)

    # Emit department_domain as a full URL rather than a bare host. Done AFTER
    # derive_search_terms, which needs the host form.
    #
    # The host is canonicalised first (path, query, fragment and trailing slash
    # dropped): a stage-2b path winner would otherwise ship a deep link such as
    # medschool.umich.edu/departments/radiation-oncology. Subdomains are NOT
    # collapsed — a department domain legitimately IS a subdomain
    # (chemistry.stanford.edu, be.mit.edu), and collapsing would destroy the
    # Tier 2B output.
    dept_dom = (result.get("department_domain") or "").strip()
    if dept_dom:
        dept_host = canonicalise_host(dept_dom)
        result["department_domain"] = (
            f"https://{dept_host}" if dept_host else None
        )

    # Single classification authority — every tier before this point wrote
    # `routing_type`, never `record_type`.
    _classify_record(result)

    # Output casing, last. It runs AFTER the changed flags, the classifier and
    # the search-term derivation, so those three see exactly the values they
    # saw before this rule existed: casing decides nothing and flags nothing.
    # It runs BEFORE `_registry_name_fields` is stripped below, because that is
    # what tells it which names came from a registry and must not be re-cased.
    normalise_output_fields(result)

    # ── The admissibility gate (Fix 10, Step 5) ──────────────────────────
    # Last, because it has to see every write. Every non-null scoped field
    # must carry at least one provenance event; one that does not is
    # inadmissible and its value is reverted to the input value and flagged.
    # The record is NOT failed — shipping the original input is strictly
    # better than failing the batch, and strictly better than shipping a value
    # nothing is on record as having produced.
    reverted = enforce_admissibility(result)
    if reverted:
        _raise_unattributed_flag(result, reverted)

    # Two projections of one log. The events array is the record; the six
    # derived scalars are regenerated from it here and never maintained
    # separately, so they cannot drift from what the events say.
    result["provenance"] = result.provenance.as_dicts()
    result["provenance_rejected"] = result.provenance.rejections_as_dicts()
    result["provenance_rejected_omitted"] = dict(
        result.provenance.rejections_omitted,
    )
    result.update(_scoped_scalars(result))

    # ── The grammar assertion (Provenance Scheme B) ──────────────────────
    # Every provenance string this record ships must parse, and must satisfy
    # hard rules 1-2 (`llm` never reaches `verified`; a witness-less
    # `verified` is a registry's alone). An invalid string is raised, not
    # logged: a provenance column that does not parse is worse than an empty
    # one, because a consumer reads it as an attribution. `operating_name`
    # is in scope even though it is not one of the six write-locked fields —
    # the grammar is a property of the COLUMN, not of the write path.
    validate_provenance_strings(
        result.get(column) for column in PROVENANCE_COLUMNS
    )

    result["duration_ms"] = int((time.monotonic() - start) * 1000)
    # Strip transient non-schema keys before pydantic validation.
    result.pop("_preprocess_cleared", None)
    result.pop("_dba_values", None)
    result.pop("_pp_name1", None)
    # One per department slot — set by _apply_tier3, read by finalise().
    for _slot in DEPT_SLOTS:
        result.pop(f"_{_slot}_from_tier3", None)
    result.pop("_ror_acronym", None)
    result.pop("_website_raw", None)
    result.pop("_source_title", None)
    result.pop("_source_h1", None)
    result.pop("_tier1_query_name", None)
    result.pop("_tier1_country_code", None)
    # Fix 2 / Fix 3 evidence, consumed by `_resolve_unchanged_name1` above.
    result.pop("_canonical_proposal", None)
    result.pop("_page_corroboration", None)
    # Wikidata lane evidence: the corroboration marker `_resolve_unchanged_name1`
    # read above, the matched QID, and the P856 the deferred domain check
    # already consumed (popped here too, for the paths that never reached it).
    result.pop("_wikidata_corroboration", None)
    result.pop("_wikidata_qid", None)
    result.pop("_wikidata_website", None)
    result.pop("_registry_name_fields", None)
    # Fix D — the per-source identity claims the gate above consumed.
    for _src_key in SOURCE_KEYS:
        result.pop(_src_key, None)
    # Registry-vs-record_type disagreement surfaced by a retry hit. Logged at
    # the point of conflict and deliberately left unreconciled — record_type
    # assignment is out of scope here.
    result.pop("_tier1_retry_type_conflict", None)
    result.pop("_ror_is_research", None)
    result.pop("_gleif_category", None)
    result.pop("_gleif_sub_category", None)
    result.pop("_gleif_legal_form_id", None)
    result.pop("_gleif_legal_form_other", None)
    return result


#: Registry guard name → the field the refused candidate would have written.
#: A refused ROR/GLEIF candidate would have supplied the identifier, the name
#: and the domain together, so the rejection is filed against the identity that
#: was declined rather than duplicated across all three.
_GUARD_FIELDS: dict[str, str] = {
    "ror_country": "ror_id",
    "distinctive_token": "ror_id",
    "identifier_token": "ror_id",
    "local_rescore": "ror_id",
    "gleif_country": "lei_id",
    "gleif_name_verification": "lei_id",
}


def _resolve_unchanged_name1(result: Any) -> None:
    """Decide Fix 2's unchanged-Name-1 state and make it visible.

    Two effects, both of them derived from one decision taken in
    :mod:`enrichment.unchanged_state`:

    * **Provenance.** A verified or confirmed record gets one further event on
      ``name1_enriched``, recording the value it already holds together with
      what allowed it to stand — so the derived scalar reads
      ``input:verified+web`` / ``input:provisional+llm`` instead of
      ``input:low``.
    * **Flagging.** ``_ev_low_conf_unchanged`` is set for ``name1`` iff the
      state is *unresolved*. This is the consistency fix: before it, whether an
      unchanged Name 1 was flagged depended on whether Tier 3 happened to run,
      which is why the chemspeed batch had five records with the same evidence
      class as flagged rows passing silently.

    Records whose Name 1 a tier rewrote are untouched — the three states do not
    describe them, and the per-slot marker Tier 3 leaves on Name 2..5 is not
    read here.
    """
    outcome = resolve_unchanged_name1(result)
    if outcome is None:
        return

    unchanged: set[str] = result.setdefault("_ev_low_conf_unchanged", set())
    if outcome.flagged:
        unchanged.add("name1")
    else:
        unchanged.discard("name1")
        evidence = unchanged_evidence(outcome, result["name1_enriched"])
        if evidence is not None:
            _write(result, "name1_enriched", result["name1_enriched"], evidence)

    # The status column is DATAshaper's severity input. A record Fix 2 declines
    # to flag must not still be asking for a manual review — or, as the first
    # run of this fix showed, reporting a process error because a new
    # short-circuit returned before any tier set the field.
    result["enrichment_status"] = unchanged_status(
        outcome, result.get("enrichment_status"),
    )
    result["unchanged_name1_state"] = outcome.state
    logger.info({
        "record_id": result.get("record_id"),
        "step": "unchanged_name1_state",
        "state": outcome.state,
        "evidence": outcome.evidence,
        "evidence_ref": outcome.evidence_ref,
        "name1": result.get("name1_enriched"),
    })


def _log_registry_rejections(
    result: Any,
    registry: str,
    res: dict[str, Any] | None,
) -> None:
    """Copy a registry client's guard rejections onto the record's log.

    Only guards — the ROR country guard, the distinctive- and identifier-token
    guards and GLEIF's name verification. Not the full candidate list from
    every lookup: that multiplies volume for little value, whereas a guard
    rejection is the pipeline refusing an answer it was confident about.
    Capped per field per record inside :meth:`ProvenanceLog.reject`, which
    keeps the count of anything beyond the cap.
    """
    if not res or not isinstance(res, dict):
        return
    for raw in res.get("guard_rejections") or ():
        guard = str(raw.get("guard"))
        field = _GUARD_FIELDS.get(guard, "ror_id")
        score = raw.get("score")
        result.reject(
            field, raw.get("candidate_name"), guard,
            reason=raw.get("detail"),
            evidence=Evidence(
                producer_chain=(registry,),
                tier=1,
                confidence_scale=(
                    ROR_LOCAL if registry == "ror" else FUZZY_RATIO
                ),
                confidence_value=score,
                evidence_ref={
                    "candidate_id": raw.get("candidate_id"),
                    "query": raw.get("query"),
                    "threshold": raw.get("threshold"),
                },
                rule_id=f"{registry}-guard:{guard}",
            ),
        )


def _record_gleif_evidence(result: dict[str, Any], lei_res: dict[str, Any]) -> None:
    """Carry GLEIF's entity metadata onto the record for classification.

    Transient (`_`-prefixed, dropped in finalise): the fields exist to let
    :func:`enrichment.classifier.classify` judge commercial status, and are not
    part of the response.
    """
    result["_gleif_category"] = lei_res.get("category")
    result["_gleif_sub_category"] = lei_res.get("sub_category")
    result["_gleif_legal_form_id"] = lei_res.get("legal_form_id")
    result["_gleif_legal_form_other"] = lei_res.get("legal_form_other")


def _classify_record(result: dict[str, Any]) -> None:
    """Decide ``record_type`` once, from ranked evidence. The ONLY place the
    field is written.

    Runs at the end of ``finalise`` so it sees every tier's evidence, including
    a name a later tier corrected and a registry id the Tier 1 re-lookup
    recovered. Everything before this point steers on ``routing_type``.
    """
    evidence = TypeEvidence(
        name1=_domain_evidence_name1(result),
        ror_is_research=result.get("_ror_is_research"),
        lei_id=result.get("lei_id"),
        gleif_category=result.get("_gleif_category"),
        gleif_legal_form_id=result.get("_gleif_legal_form_id"),
        gleif_legal_form_other=result.get("_gleif_legal_form_other"),
    )
    record_type, source = classify(evidence)
    # `record_type` is decided from RANKED evidence, not from a score: a ROR
    # research flag outranks a GLEIF category, which outranks a keyword. The
    # rule that fired is the attribution, and `record_type_source` names it.
    _write(
        result, "record_type", record_type,
        deterministic_evidence(
            f"classifier:{source}",
            producer="classifier",
            evidence_ref={
                "decided_by": source,
                "ror_is_research": result.get("_ror_is_research"),
                "lei_id": result.get("lei_id"),
                "gleif_category": result.get("_gleif_category"),
                "name1": evidence.name1,
            },
        ),
    )
    result["record_type_source"] = source
    routing = result.get("routing_type")
    # The record ran down a branch chosen from `routing`; where that disagrees
    # with the answer, tiers were gated on the wrong type (routed as a company,
    # so Tier 2B never ran, then finally classified research_institution). The
    # record is NOT re-run — the count just makes the size of it visible.
    result["routing_type_mismatch"] = bool(
        routing and routing != "unknown"
        and record_type != "unknown"
        and routing != record_type
    )
    if result["routing_type_mismatch"]:
        logger.info({
            "record_id": result.get("record_id"),
            "step": "routing_type_mismatch",
            "routed_as": routing,
            "classified_as": record_type,
            "source": source,
        })


def _domain_evidence_name1(result: dict[str, Any]) -> str | None:
    """The organisation name a candidate domain is claimed to belong to."""
    for key in ("name1_enriched", "_pp_name1", "name1_original"):
        val = (result.get(key) or "").strip()
        if val:
            return val
    return None


def _domain_witnesses(result: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Every official website an INDEPENDENT system states for this record's
    organisation, as ``(witness, url)`` pairs.

    Two sources, both of them evidence the pipeline has already fetched while
    resolving the identity — no call is made here and none is added anywhere:

    * ``registry`` — the ``links[]`` website of the ROR record the match
      landed on, retained by
      :func:`enrichment.consistency.record_registry_identity` at every point a
      registry match is accepted;
    * ``wikidata`` — the ``P856`` official-website claim of the item the
      crosswalk lane matched, stashed by the lane.

    The claims are ordered registry-first so that when both agree the stronger
    witness is the one provenance names.
    """
    witnesses: list[tuple[str, str]] = []
    for witness, url in result.get("_src_stated_websites") or ():
        entry = (str(witness), str(url))
        if entry not in witnesses:
            witnesses.append(entry)
    stated = str(result.get("_wikidata_website") or "").strip()
    if stated and ("wikidata", stated) not in witnesses:
        witnesses.append(("wikidata", stated))
    return tuple(witnesses)


def _apply_domain(
    result: dict[str, Any],
    candidate_url: str | None,
    *,
    registry: str | None = None,
    serp_title: str | None = None,
    serp_h1: str | None = None,
    serp_url: str | None = None,
    settings: Settings | None = None,
    producer_chain: tuple[str, ...] = ("website_resolver",),
    tier: int | None = None,
    page_identity: bool = False,
) -> DomainDecision:
    """Route one candidate URL through the single ``domain`` write path.

    The ONLY place ``result["domain"]`` / ``result["website_url"]`` are set.
    ``registry`` is ``"ROR"``/``"GLEIF"`` when the candidate came out of a
    registry record that already passed that registry's own guard.

    First non-empty wins, as documented: once a domain is accepted a later
    (lower-precedence) candidate never overwrites it. The raw candidate URL is
    kept in the transient ``_website_raw`` key because the department probe
    needs the real host — ``asrc.gc.cuny.edu`` must not become ``cuny.edu``
    before ``site:`` restriction (§5e) — while the emitted ``website_url`` is
    always the canonical ``https://<domain>``.
    """
    if result.get("domain"):
        return DomainDecision(
            domain=result["domain"],
            website_url=result.get("website_url"),
            verified_by=result.get("domain_verified_by"),
        )

    evidence = DomainEvidence(
        name1=_domain_evidence_name1(result),
        email=result.get("email_enriched") or result.get("email_original"),
        registry=registry,
        serp_title=serp_title,
        serp_h1=serp_h1,
        serp_url=serp_url or candidate_url,
        stated_websites=_domain_witnesses(result),
        page_identity=page_identity,
        # The record's own country, for the guard's country disqualifier. Read
        # from the result rather than taken as a parameter: every caller of
        # this function already has the result and none of them had a reason
        # to know the gate exists.
        country=result.get("country_region_key"),
    )
    decision = write_domain(
        result,
        candidate_url,
        evidence,
        producer_chain=(
            (registry.lower(),) if registry else producer_chain
        ),
        registry_identifier=result.get("ror_id") or result.get("lei_id"),
        threshold=settings.domain_name_match_threshold if settings else None,
        guard_enabled=(
            settings.domain_ownership_guard_enabled if settings else None
        ),
        country_gate_enabled=(
            settings.domain_country_gate_enabled if settings else None
        ),
        # The tier that was running when the candidate was decided. Falls back
        # to the record's current tier rather than being left unstated: a
        # domain accepted during Tier 1 and one accepted during the Tier 2B
        # fallback rest on different amounts of work.
        tier=tier if tier is not None else result.get("tier_used"),
    )
    if decision.rejected and not decision.domain:
        logger.info(
            "[%s] domain rejected as unverified: candidate=%s name1=%r "
            "rejected_by=%s record_country=%s",
            result.get("record_id"), decision.candidate,
            (evidence.name1 or "")[:60], decision.rejected_by,
            evidence.country,
        )
    return decision


# ── Child matching helper ─────────────────────────────────────────────────────

_CHILD_MATCH_THRESHOLD = 70  # rapidfuzz token_sort_ratio minimum


def _match_child_locally(
    name2: str,
    children: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match *name2* against the parent org's children list using rapidfuzz.

    Returns the best-matching child dict (with added ``score`` key) if the
    score meets the threshold, otherwise None.  This avoids a second ROR API
    call for child matching.
    """
    if not children or not name2.strip():
        return None

    name2_lower = name2.strip().lower()
    best: dict[str, Any] | None = None
    best_score = 0.0

    for child in children:
        child_name = child.get("name", "")
        ratio = fuzz.token_sort_ratio(name2_lower, child_name.lower())
        if ratio > best_score:
            best_score = ratio
            best = child

    if best and best_score >= _CHILD_MATCH_THRESHOLD:
        return {**best, "score": best_score}
    return None


# ── Tier result application helpers ───────────────────────────────────────────

def _apply_tier2a(
    result: dict, tier2a: Tier2AResult, mode: str, deployment: str = "unknown",
) -> None:
    """Transfer Tier 2A outcome into the result dict."""
    result["tier_used"] = 2
    result["tier2_mode"] = tier2a.mode
    result["contact_used"] = True
    result["source"] = tier2a.source
    result["source_url"] = tier2a.source_url
    # Transient evidence for the domain ownership guard, should the domain end
    # up being derived from this page in finalise().
    result["_source_title"] = tier2a.source_title
    result["confidence"] = tier2a.confidence
    result["enrichment_status"] = tier2a.enrichment_status
    result["name2_match_result"] = tier2a.name2_match
    if tier2a.low_conf_unchanged:
        result.setdefault("_ev_low_conf_unchanged", set()).update(
            tier2a.low_conf_unchanged,
        )

    if tier2a.name2_enriched and tier2a.name2_enriched.strip():
        # One value, produced by three tools in sequence: the search that found
        # the contact's page, the fetch that retrieved it, and the model that
        # read the department off its structured elements.
        _write(
            result, "name2_enriched", tier2a.name2_enriched.strip(),
            llm_evidence(
                ("serp", "fetch", "llm_tier2a"),
                tier=2,
                prompt_version=TIER2A_PROMPT_VERSION,
                deployment=deployment,
                self_reported=tier2a.confidence,
                source_url=tier2a.source_url,
                rule_id=f"tier2a:{tier2a.mode}",
                extra={"name2_match": tier2a.name2_match},
            ),
        )
    # Only populate name3 if the input record originally had one.
    # Tier 2A opportunistically extracts groups/programs from the
    # contact's page — but we should not introduce a name3 the user
    # didn't ask for.
    if (
        tier2a.name3_enriched and tier2a.name3_enriched.strip()
        and result.get("name3_original")
        and str(result["name3_original"]).strip()
    ):
        result["name3_enriched"] = tier2a.name3_enriched.strip()


def _apply_tier3(
    result: dict, tier3: Tier3Result, deployment: str = "unknown",
) -> None:
    """Transfer Tier 3 outcome into the result dict."""
    result["tier_used"] = 3
    result["source"] = "LLM"
    result["confidence"] = tier3.confidence
    result["enrichment_status"] = tier3.enrichment_status

    # Which fields Tier 3 actually wrote. Tier 3 has no external evidence —
    # it relies entirely on the LLM's training data — so anything it writes
    # is an unverified inference regardless of the model's stated confidence.
    # Finalisation decides that; recording the fields is all that happens here.
    # A low-confidence run writes nothing, so this stays empty and the record
    # is described as unchanged instead.
    written: set[str] = result.setdefault("_ev_tier3_wrote", set())

    if tier3.success:
        if tier3.name1_suggestion and tier3.name1_suggestion.strip():
            suggestion = tier3.name1_suggestion.strip()
            # Identity guard: never let the LLM swap name1 for a different
            # entity (e.g. "Iso Group Inc" → "CoStar Group"). Accept only a
            # reformatting / acronym expansion of the original.
            original_name1 = result.get("name1_original")
            if canonical_preserves_identity(original_name1, suggestion):
                _write(
                    result, "name1_enriched", suggestion,
                    llm_evidence(
                        ("llm_tier3",),
                        tier=3,
                        prompt_version=TIER3_PROMPT_VERSION,
                        deployment=deployment,
                        self_reported=tier3.confidence,
                        rule_id="tier3:name1_suggestion",
                    ),
                )
                written.add("name1")
            else:
                logger.warning(
                    "[%s] Tier 3: REJECTED name1 '%s' → '%s' "
                    "(different entity — identity not preserved)",
                    result.get("record_id"), original_name1, suggestion,
                )
        # Every department slot takes its suggestion the same way. Name 2
        # additionally records that Tier 3 authored it, because finalisation
        # drops a Tier-3-invented department when the input slot was blank
        # and the model was not certain — the same doubt applies to the
        # slots below it, so they are marked too.
        for slot in DEPT_SLOTS:
            suggestion = getattr(tier3, f"{slot}_suggestion", None)
            if suggestion and suggestion.strip():
                _write(
                    result, f"{slot}_enriched", suggestion.strip(),
                    llm_evidence(
                        ("llm_tier3",),
                        tier=3,
                        prompt_version=TIER3_PROMPT_VERSION,
                        deployment=deployment,
                        self_reported=tier3.confidence,
                        rule_id=f"tier3:{slot}_suggestion",
                    ),
                )
                result[f"_{slot}_from_tier3"] = True
                written.add(slot)

    # Tier 3 is the last resort. A field it was asked about and declined to
    # write leaves the pipeline holding exactly what the record supplied, with
    # nothing having confirmed it — 8f's other half. A field an earlier tier
    # already rewrote is excluded: that value was settled before Tier 3 ran.
    #
    # Name 1 is NOT marked here since Fix 2. "Tier 3 declined to rewrite it" is
    # one of several ways to arrive at a retained Name 1, and marking only the
    # ones Tier 3 saw is exactly what made two records with the same evidence
    # get different treatment. The decision moved to finalise, where it is
    # taken once from the settled record — see enrichment/unchanged_state.py.
    # The department slots keep this marker: there is no corroborating evidence
    # class for a unit name, so there is nothing for a three-state split to
    # read.
    unchanged: set[str] = result.setdefault("_ev_low_conf_unchanged", set())
    for field in DEPT_SLOTS:
        if field in written:
            continue
        enriched = result.get(f"{field}_enriched")
        original = result.get(f"{field}_original")
        if enriched and (
            str(enriched).strip().casefold()
            != str(original or "").strip().casefold()
        ):
            continue
        unchanged.add(field)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:
    """Coordinates the multi-tier enrichment pipeline for a batch of records."""

    def __init__(self, settings: Settings, mock_clients: dict[str, Any] | None = None) -> None:
        self._settings = settings
        self._mock_clients = mock_clients

        # Fix B — ONE evidence cache for this process: SERP, page reads,
        # Wikidata, ROR and GLEIF, all under `EVIDENCE_CACHE_DIR` and all
        # governed by one `CACHE_FROZEN` switch. Registered process-wide so the
        # module-level registry clients (`tier1_ror`, `tier1_lei`) reach the
        # disk layer without eight new parameters. Built first because the
        # Wikidata client takes its namespace.
        self._evidence_cache = build_evidence_cache(settings)

        # Wikidata crosswalk lane. Its own namespace, under its own filename
        # prefix, so a search/entity recording sits beside the page reads
        # rather than inside them.
        self._wikidata_cache = self._evidence_cache.namespace(
            "wikidata",
            directory=settings.wikidata_fixture_dir,
            replay_only=settings.wikidata_fixture_replay_only,
        )

        if mock_clients:
            self._ror_client: RORClient = mock_clients.get("ror", RORClient(settings))
            self._lei_client: LEIClient = mock_clients.get("lei", LEIClient(settings))
            self._wikidata_client: WikidataClient = mock_clients.get(
                "wikidata", WikidataClient(settings, cache=self._wikidata_cache),
            )
            self._search_client: SearchClient = mock_clients.get(
                "search", self._build_search_client(settings))
            self._page_fetcher: PageFetcher = mock_clients.get("page_fetcher", PageFetcher(
                timeout=settings.page_fetch_timeout_seconds,
                max_chars=settings.max_page_content_chars,
                store=self._evidence_cache.namespace("fetch"),
            ))
            self._llm_client: OpenAIClient = mock_clients.get("llm", OpenAIClient(settings))
        else:
            self._ror_client = RORClient(settings)
            self._lei_client = LEIClient(settings)
            self._wikidata_client = WikidataClient(
                settings, cache=self._wikidata_cache,
            )
            self._search_client = self._build_search_client(settings)
            self._page_fetcher = PageFetcher(
                timeout=settings.page_fetch_timeout_seconds,
                max_chars=settings.max_page_content_chars,
                store=self._evidence_cache.namespace("fetch"),
            )
            self._llm_client = OpenAIClient(settings)

        # Per-batch GLEIF/LEI telemetry counters (reset in enrich_batch).
        self._lei_counts: dict[str, int] = self._new_lei_counts()
        # Per-batch Tier 1 re-lookup telemetry (reset in enrich_batch).
        self._tier1_retry_counts: dict[str, int] = self._new_tier1_retry_counts()
        # Per-batch page-read telemetry (reset in enrich_batch).
        self._page_counts: dict[str, int] = self._new_page_counts()
        # Per-batch Wikidata crosswalk telemetry (reset in enrich_batch).
        self._wikidata_counts: dict[str, int] = self._new_wikidata_counts()

        # Page reads, keyed on domain and recorded to disk. Process-level like
        # the SERP cache — two records naming the same organisation cost one
        # fetch — and additionally fixture-backed, because a page read is a
        # claim about what a site said on a day and a thesis re-run has to
        # reproduce it rather than re-ask the live web.
        self._page_cache = self._evidence_cache.namespace(
            "page",
            directory=settings.page_fixture_dir,
            replay_only=settings.page_fixture_replay_only,
        )

        # SERP cache shared by every batch this orchestrator processes — and,
        # since Fix B, by every RUN, because it now has a disk layer behind the
        # in-memory one. Without that, a second run of a batch re-issued every
        # search it had already paid for and could be handed a different
        # result set for the identical query.
        self._serp_cache = SerpCache(
            disk=self._evidence_cache.namespace("serp"),
        )

    @staticmethod
    def _new_lei_counts() -> dict[str, int]:
        """Fresh per-batch GLEIF/LEI telemetry counters."""
        return {
            "attempts": 0, "hits_exact": 0, "hits_fuzzy": 0,
            "misses": 0, "errors": 0,
        }

    @staticmethod
    def _new_tier1_retry_counts() -> dict[str, int]:
        """Fresh per-batch Tier 1 re-lookup counters."""
        return {"attempts": 0, "hits_ror": 0, "hits_lei": 0}

    @staticmethod
    def _new_page_counts() -> dict[str, int]:
        """Fresh per-batch page-read counters. The outcome keys partition the
        attempts; `flag_cleared` and `withdrawn` count the two consequences."""
        return {
            "attempted": 0, "flag_cleared": 0, "withdrawn": 0,
            "mismatch_not_withdrawn": 0, "domain_accepted": 0,
            CORROBORATED: 0, CONTRADICTED: 0, NAME_MISMATCH: 0,
            FETCH_UNAVAILABLE: 0, NO_IDENTITY: 0, PARKED: 0,
        }

    @staticmethod
    def _new_wikidata_counts() -> dict[str, int]:
        """Fresh per-batch Wikidata crosswalk counters.

        Four of them — ``matched``, ``no_match``, ``ambiguous``,
        ``unavailable`` — **partition** ``queried``: every lane invocation ends
        in exactly one. ``type_rejected`` and ``country_rejected`` are
        deliberately NOT part of that partition: they count records where at
        least one *candidate* was refused by that gauntlet step, so a record
        whose only candidate was a film increments both ``type_rejected`` and
        ``no_match``. Both statements are true, and the second is the one that
        describes what the pipeline did with the record.

        The rest count consequences: what the crosswalk followed, what it
        recovered, and what the witness path did.
        """
        return {
            "queried": 0, "matched": 0, "no_match": 0, "ambiguous": 0,
            "unavailable": 0, "type_rejected": 0, "country_rejected": 0,
            "crosswalk_ror": 0, "crosswalk_lei": 0,
            "crosswalk_registry_hit": 0, "superseded_flagged": 0,
            "witness_only": 0, "domain_corroborated": 0,
            "domain_disagree": 0,
            # The corroboration-only pass on registry-resolved records:
            # how often it ran, and how often it came back with a website
            # claim to compare. Kept apart from `queried`/`matched`, which
            # measure the crosswalk lane, so neither number is diluted by
            # the other's population.
            "corroboration_queried": 0, "corroboration_matched": 0,
        }

    @staticmethod
    def _build_search_client(settings: Settings) -> SearchClient:
        """Select SERP provider based on configuration."""
        key = (settings.serpapi_key or "").strip()
        if key:
            logger.info("Using SerpAPI search provider")
            return SerpAPIClient(key)
        logger.warning(
            "SERPAPI_KEY not set — using DuckDuckGo (lower quality results). "
            "Set SERPAPI_KEY in .env for reliable department search."
        )
        return DuckDuckGoClient()

    async def enrich_batch(
        self,
        records: list[EnrichmentRecord],
        options: EnrichmentOptions,
    ) -> EnrichmentResponse:
        """Process a batch of records with concurrency control."""
        batch_start = time.perf_counter()
        # Filter the openai+httpx+py3.13 aclose() AttributeError noise
        # for the duration of this batch. Idempotent.
        install_httpx_aclose_noise_filter()
        clear_ror_cache()  # fresh cache per batch to avoid stale failures
        clear_lei_cache()  # likewise for the GLEIF/LEI cache
        reset_consistency_counters()  # and the Fix D(2) batch counter
        self._lei_counts = self._new_lei_counts()  # reset per-batch telemetry
        self._tier1_retry_counts = self._new_tier1_retry_counts()
        self._page_counts = self._new_page_counts()
        self._wikidata_counts = self._new_wikidata_counts()
        self._evidence_cache.network_calls = 0
        self._evidence_cache.network_calls_by_namespace.clear()
        cache = BatchCache(shared_serp=self._serp_cache)
        semaphore = asyncio.Semaphore(options.max_concurrency)

        async def _process_with_semaphore(record: EnrichmentRecord) -> EnrichmentResult:
            async with semaphore:
                return await self._enrich_single(record, options, cache)

        try:
            results = await asyncio.gather(
                *[_process_with_semaphore(r) for r in records],
                return_exceptions=True,
            )

            final_results: list[EnrichmentResult] = []
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(
                        "Unhandled exception for record %s: %s",
                        records[i].record_id, str(res),
                    )
                    # Carry every original column through even on failure so
                    # the result workbook still round-trips the input row.
                    failed = _init_result(records[i])
                    failed["enrichment_status"] = "failed"
                    failed["error"] = str(res)
                    # This path never reaches finalise() — the record died
                    # before its return. Normalise it here so a fail-safe row
                    # is cased like every other row in the workbook.
                    normalise_output_fields(failed)
                    final_results.append(EnrichmentResult(**failed))
                else:
                    final_results.append(res)

            # Fix 6 — batch consensus. Runs AFTER every record has been
            # finalised and BEFORE serialisation (and before the summary, so
            # the counts describe what actually ships). Field propagation
            # only: no record is merged, dropped or deduplicated here, and
            # `tier_used` is untouched. It raises no flag; it withdraws only
            # the codes its own write to `name1_enriched` falsified. See
            # enrichment/batch_consensus.py.
            consensus = apply_batch_consensus(final_results)

            batch_ms = int((time.perf_counter() - batch_start) * 1000)
            summary = self._build_summary(final_results, batch_ms)
            summary.consensus_groups = consensus.groups
            summary.consensus_records_updated = consensus.records_updated
            summary.consensus_conflicts = consensus.conflicts
            summary.consensus_fields_propagated = dict(consensus.fields_propagated)
            summary.consensus_flags_retracted = consensus.flags_retracted
            # Fold in the GLEIF/LEI per-batch telemetry. tier1_lei_count is
            # the number of records resolved by the LEI step (exact + fuzzy).
            summary.lei_attempts = self._lei_counts["attempts"]
            summary.lei_hits_exact = self._lei_counts["hits_exact"]
            summary.lei_hits_fuzzy = self._lei_counts["hits_fuzzy"]
            summary.lei_misses = self._lei_counts["misses"]
            summary.lei_errors = self._lei_counts["errors"]
            summary.tier1_lei_count = (
                self._lei_counts["hits_exact"] + self._lei_counts["hits_fuzzy"]
            )
            # Tier 1 re-lookup after canonicalisation.
            summary.tier1_retry_attempts = self._tier1_retry_counts["attempts"]
            summary.tier1_retry_hits_ror = self._tier1_retry_counts["hits_ror"]
            summary.tier1_retry_hits_lei = self._tier1_retry_counts["hits_lei"]
            # Fix 3 — page-read corroborator.
            summary.page_reads_attempted = self._page_counts["attempted"]
            summary.page_corroborated = self._page_counts[CORROBORATED]
            summary.page_contradicted = self._page_counts[CONTRADICTED]
            summary.page_name_mismatch = self._page_counts[NAME_MISMATCH]
            summary.page_fetch_unavailable = self._page_counts[FETCH_UNAVAILABLE]
            summary.page_no_identity = self._page_counts[NO_IDENTITY]
            summary.page_parked = self._page_counts[PARKED]
            summary.page_domains_withdrawn = self._page_counts["withdrawn"]
            summary.page_flags_cleared = self._page_counts["flag_cleared"]
            summary.page_mismatch_not_withdrawn = (
                self._page_counts["mismatch_not_withdrawn"]
            )
            summary.page_domains_accepted = self._page_counts["domain_accepted"]
            # Wikidata crosswalk lane. Maintained unconditionally, like the
            # page-read counters: WIKIDATA_TRACE gates the per-record JSON
            # line, not the aggregates, so a measurement run cannot end up
            # with silently-zero numbers because a flag was forgotten.
            for _key, _value in self._wikidata_counts.items():
                setattr(summary, f"wikidata_{_key}", _value)
            # Lookups the normalised cache key served that the old lowercased
            # key would have missed — i.e. API calls Step 1 saved outright.
            summary.routing_type_mismatch_count = sum(
                1 for r in final_results if r.routing_type_mismatch
            )
            summary.cache_hits_after_normalisation = (
                ror_normalised_hits()
                + lei_normalised_hits()
                + cache.normalised_hits
            )
            # Fix B — what this run had to go and get, and what a frozen run
            # went without. `evidence_network_calls == 0` on a warm second run
            # is the reproducibility gate's precondition: a run that called out
            # is not comparing the same evidence the first run saw.
            summary.evidence_cache_frozen = self._evidence_cache.frozen
            summary.evidence_network_calls = self._evidence_cache.network_calls
            summary.evidence_frozen_misses = self._evidence_cache.frozen_misses
            summary.evidence_cache_hits = self._evidence_cache.hits
            summary.registry_location_unconfirmed = (
                registry_location_unconfirmed_count()
            )
            summary.registry_agreement = registry_agreement_count()
            summary.evidence_network_calls_by_namespace = dict(
                sorted(self._evidence_cache.network_calls_by_namespace.items())
            )

            logger.info(
                "Batch complete: %d records, %d enriched, %d failed in %dms",
                summary.total, summary.enriched, summary.failed, batch_ms,
            )

            return EnrichmentResponse(results=final_results, summary=summary)
        finally:
            # Release the cached AsyncOpenAI HTTP client cleanly so we
            # don't leave Python's GC to call aclose() at an arbitrary
            # later time — that's where the
            # `AsyncHttpxClientWrapper has no attribute '_transport'`
            # noise comes from on Python 3.13. Mocks expose aclose
            # only when present.
            aclose = getattr(self._llm_client, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception as exc:  # noqa: BLE001
                    logger.info("LLM client aclose failed (non-fatal): %s", exc)

    async def _maybe_resolve_website_bc(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        cache: BatchCache,
    ) -> None:
        """Run Path B (SERP) for any record type, then Path C (LLM)
        as a fallback. Idempotent — safe to call from any return path.

        Path A (ROR's links[].website) is written inline in the Tier 1
        block as soon as ROR matches; this helper handles the
        post-ROR fallback for both research institutions and
        companies. Confidence semantics:

        * Path B high   → ``website_url``, no website-specific flag.
        * Path B low    → ``website_url`` + flag for review.
        * Path C        → ``website_url`` + flag for review.

        Both paths hand their candidate to ``_apply_domain`` rather than
        writing the field: a SERP or LLM URL is a guess about which
        organisation owns a site, and the ownership guard is what decides
        whether it may be attributed. When the guard rejects it nothing is
        written, so the "verify this website" flag — which is about a website
        we wrote — does not fire either; the record carries
        ``domain-unverified`` instead.
        """
        # Idempotence: a candidate already resolved — or already rejected by
        # the guard — must not trigger a second round of lookups.
        if result.get("website_url") or result.get("domain_rejected"):
            return
        pp_name1 = result.get("_pp_name1")
        if not pp_name1 or not pp_name1.strip():
            return

        rec_type = result.get("routing_type")
        # Path B runs for any record type; the resolver builds the
        # appropriate query shape and applies TLD-based confidence.
        serp_res = await resolve_website_via_serp(
            record_id=record.record_id,
            name1=pp_name1,
            city=record.city,
            state=record.state,
            country=record.country,
            record_type=rec_type,
            search_client=self._search_client,
            cache=cache,
            trace=self._settings.website_trace,
            country_gate=self._settings.domain_country_gate_enabled,
        )
        if serp_res.url:
            decision = _apply_domain(
                result,
                serp_res.url,
                serp_title=serp_res.title,
                serp_url=serp_res.url,
                settings=self._settings,
            )
            # No provenance flag. "Resolved by SERP with low confidence"
            # described where the URL came from, not whether it belongs to
            # this organisation — which is the question `_apply_domain`'s
            # ownership guard already answers, and answers with evidence. A
            # candidate it cannot tie to the record is not written at all and
            # raises `domain-unverified` instead.
            return

        # Path C: LLM fallback when SERP returned nothing usable.
        llm_res = await infer_website_via_llm(
            record_id=record.record_id,
            name1=pp_name1,
            city=record.city,
            state=record.state,
            country=record.country,
            llm_client=self._llm_client,
            trace=self._settings.website_trace,
            country_gate=self._settings.domain_country_gate_enabled,
        )
        if llm_res.url:
            # Path C has no search evidence to offer — an LLM-inferred URL
            # stands or falls on name similarity / the record's own email.
            # Same rule as Path B: an accepted candidate cleared the
            # ownership guard, so its provenance is not itself a doubt.
            _apply_domain(result, llm_res.url, settings=self._settings)

    async def _resolve_probe_base(
        self, result: dict[str, Any], registrable: str, cache: BatchCache,
    ) -> str:
        """§5e/§5f: the base host the department probe keys off.

        Follows the institution website's redirect chain once (ROR's stale
        ``dur.ac.uk`` → live ``durham.ac.uk``) and, when the institution host is
        itself a subdomain (``gc.cuny.edu``), uses the FULL host so
        ``site:gc.cuny.edu`` does not leak other CUNY campuses. Cached per batch
        (one resolution per institution). Falls back to the registrable domain.
        """
        # The RAW candidate URL, not the canonical https://<domain>: §5e needs
        # the real host (asrc.gc.cuny.edu) so site: restriction doesn't leak
        # other campuses, and §5f needs the original link to follow its
        # redirect chain.
        website = (
            (result.get("_website_raw") or result.get("website_url") or "").strip()
            or f"https://{registrable}/"
        )
        cached = cache.get_resolved_host(website)
        if cached is not None:
            return cached

        base = registrable
        try:
            final = await self._page_fetcher.resolve_final_url(website)
        except Exception:
            final = None
        if final:
            host = (urlparse(final).hostname or "").lower()
            for pref in ("www.", "web."):
                if host.startswith(pref):
                    host = host[len(pref):]
            if host:
                final_registrable = extract_domain(final) or host
                if final_registrable and final_registrable != registrable:
                    base = final_registrable   # §5f: redirect landed on a new domain
                else:
                    base = host                # §5e: subdomain (or == registrable)
        cache.set_resolved_host(website, base)
        if base != registrable:
            logger.info(
                "[%s] dept probe base resolved: %s → %s",
                result.get("record_id"), registrable, base,
            )
        return base

    async def _probe_department_url(
        self,
        record_id: str,
        result: dict[str, Any],
        cache: BatchCache,
    ) -> None:
        """Find the unit's web host and write it to
        ``result["department_domain"]``.

        Scores every candidate host against significant tokens drawn
        from the cleaned name2 phrase, so the probe doesn't blindly
        accept the first non-base host (which produced wrong answers
        like ``professorships.jhu.edu`` for Radiology or
        ``inside.fpm.wisc.edu`` for the Candelario Lab).

        Strategy:

        1. **Homepage scrape** — fetch ``https://<domain>/`` /
           ``website_url``; score each outgoing link.
        2. **SERP fallback** — ``<cleaned_name2> site:<domain>``; score
           each result.

        Scoring per candidate (host_prefix = host with the institution
        base stripped):

        * +3 if any significant token appears in *host_prefix*
        * +1 if any significant token appears in the URL path
        * +1 if any significant token appears in the link text / title
        * Generic admin hosts (``professorships``, ``inside``, ``news``,
          …) are forcibly scored 0 so they can never win.

        The highest-scoring candidate (>0) wins. Ties go to the first
        encountered. No usable candidate → ``department_domain``
        stays null.

        Gates: research_institution + name2 present + institution
        domain known + name2 not granular. Granular units (labs,
        groups, centres) are skipped — they're too fine-grained for a
        domain probe; a lab's web home requires lab_resolver, not a
        SERP guess.
        """
        if result.get("routing_type") != "research_institution":
            return
        if result.get("department_domain"):
            return
        base = (result.get("domain") or "").strip().lower()
        if not base:
            return
        name2 = (
            (result.get("name2_enriched") or "").strip()
            or (result.get("name2_original") or "").strip()
        )
        if not name2:
            return
        # §5a: an administrative desk (accounts payable, finance, …) has no
        # department web home and its search_term_2 is "ADMIN" regardless — skip
        # BEFORE any page fetch or SERP call.
        if is_admin_unit(name2):
            logger.info(
                "[%s] dept domain probe: skipped (admin unit name2=%r)",
                record_id, name2,
            )
            return
        # An address or pure location fragment that a tier dropped into name2
        # ("104 Rhines Hall", "Annex D Pod 2") is NOT a department. The
        # name→street cleanup (address stage / finalise) moves it out, but
        # that runs after this probe — so guard here too. Skipping spends no
        # SERP call and avoids resolving a wrong "department" for a building.
        _name2_addrs, _name2_rem = _extract_addresses(name2)
        if (_name2_addrs and not _name2_rem.strip()) or _location_fragment(name2):
            logger.info(
                "[%s] dept domain probe: skipped (name2 is an address/"
                "location fragment %r)",
                record_id, name2,
            )
            return
        if is_granular_unit(name2):
            logger.info(
                "[%s] dept domain probe: skipped (granular unit name2=%r)",
                record_id, name2,
            )
            return

        # Strip donor-name prefix ("Russell H. Morgan Department of
        # Radiology and Radiological Science" → "Radiology and
        # Radiological Science"). Without this, donor names contribute
        # noise tokens that match unrelated faculty pages.
        core = extract_dept_core(name2) or name2
        cleaned = clean_name2_phrase(core) or core
        tokens = _significant_dept_tokens(cleaned)
        acronym = derive_acronym(cleaned)
        if not tokens and not acronym:
            logger.info(
                "[%s] dept domain probe: no significant tokens/acronym "
                "in %r (core=%r)",
                record_id, cleaned, core,
            )
            return

        # §5e/§5f: resolve the base the probe keys off — follow the institution
        # website's redirect once (dur.ac.uk → durham.ac.uk) and use the FULL
        # host when the institution is itself a subdomain (gc.cuny.edu). `base`
        # was the registrable domain up to here; every stage below now keys off
        # the resolved value.
        base = await self._resolve_probe_base(result, base, cache)

        def _host_of(url: str) -> str | None:
            try:
                host = (urlparse(url).hostname or "").lower()
            except Exception:
                return None
            if not host:
                return None
            if host.startswith("www."):
                host = host[4:]
            if host == base:
                return None
            return host

        # ── 0) GET-probe + verify candidate subdomains ────────────────
        # Construct candidate subdomains from the acronym and the
        # longest tokens, fetch each, and verify the page actually
        # describes this department (title/h1 must mention the dept
        # phrase or two of its significant tokens). This is what
        # rejects ``science.mit.edu`` for "Computer Science" — that
        # host resolves fine, but its title is "MIT School of Science"
        # and doesn't reference "Computer Science" specifically.
        candidates: list[str] = []
        if acronym and 2 <= len(acronym) <= 6:
            candidates.append(acronym.lower())
        sorted_tokens = sorted(
            (t for t in tokens if len(t) >= 4), key=len, reverse=True,
        )
        for tok in sorted_tokens[:2]:
            tok_l = tok.lower()
            if tok_l not in candidates:
                candidates.append(tok_l)
            # Departments often use an abbreviated subdomain
            # ("chem" ← "chemistry", "phys" ← "physics", "math" ←
            # "mathematics"). Probe short prefixes of the token too.
            for plen in (4, 3):
                if len(tok_l) > plen:
                    pref = tok_l[:plen]
                    if pref not in candidates:
                        candidates.append(pref)

        hosts_to_probe = [
            f"{c}.{base}" for c in candidates
            if not _host_prefix_is_generic(f"{c}.{base}", base)
        ]
        if hosts_to_probe:
            verified = await asyncio.gather(
                *(self._verify_candidate_host(h, cleaned, tokens, acronym)
                  for h in hosts_to_probe),
                return_exceptions=True,
            )
            for host, ok in zip(hosts_to_probe, verified):
                if ok is True:
                    result["department_domain"] = host
                    logger.info(
                        "[%s] dept domain via verified probe: %s",
                        record_id, host,
                    )
                    return

        # ── 1) Homepage scrape ────────────────────────────────────────
        # Raw candidate first — the real institution host links to its
        # departments; the canonical https://<domain> may not.
        homepage = (
            result.get("_website_raw")
            or result.get("website_url")
            or f"https://{base}/"
        )
        try:
            links = await self._page_fetcher.fetch_outgoing_links(
                homepage, base,
            )
        except Exception as exc:
            logger.info(
                "[%s] dept domain probe: homepage fetch failed (%s): %s",
                record_id, homepage[:80], exc,
            )
            links = []

        # Rank homepage links by score (high to low) and verify in
        # order — top scorer wins only if its page is actually about
        # this department. Without this, false positives like
        # ``aas.princeton.edu`` for "Astrophysical Sciences" (host
        # ``aas`` substring-contains acronym ``AS``) sneak through
        # even when the page is African American Studies.
        scored: list[tuple[int, str]] = []
        seen_hosts: set[str] = set()
        for text, link_url in links:
            host = _host_of(link_url)
            if not host or host in seen_hosts:
                continue
            try:
                path = urlparse(link_url).path or ""
            except Exception:
                path = ""
            score = _score_dept_candidate(
                host, base, path, text, tokens, acronym,
            )
            if score > 0:
                scored.append((score, host))
                seen_hosts.add(host)
        # Fix C(1) — (score DESC, canonical id ASC). `sort(reverse=True)` on
        # the raw tuple ordered hosts DESCENDING within a score tier, which is
        # deterministic but arbitrary; the rule is ascending canonical id.
        scored.sort(key=lambda t: (-t[0], t[1]))
        for score, host in scored[:5]:
            if await self._verify_candidate_host(
                host, cleaned, tokens, acronym,
            ):
                result["department_domain"] = host
                logger.info(
                    "[%s] dept domain via homepage: %s (score=%d, verified)",
                    record_id, host, score,
                )
                return

        # ── 2) SERP fallback (site-restricted) ────────────────────────
        # Strict scoring picks dept subdomains of the institution
        # (e.g. ``eecs.mit.edu`` for CS via the ``cs`` acronym
        # substring in ``eecs``). Top scorer must verify before win.
        query = f"{cleaned} site:{base}"
        probe_country = result.get("country_region_key") or result.get("country")
        try:
            serp_results = await cached_serp(
                cache, self._search_client, query,
                num_results=5, country=probe_country,
            )
        except Exception as exc:
            logger.info(
                "[%s] dept domain probe: SERP failed: %s",
                record_id, exc,
            )
            serp_results = []

        scored = []
        seen_hosts = set()
        for sr in serp_results:
            host = _host_of(sr.url)
            if not host or host in seen_hosts:
                continue
            try:
                path = urlparse(sr.url).path or ""
            except Exception:
                path = ""
            score = _score_dept_candidate(
                host, base, path, sr.title or "", tokens, acronym,
            )
            if score > 0:
                scored.append((score, host))
                seen_hosts.add(host)
        # Fix C(1) — same total order as the homepage scan above.
        scored.sort(key=lambda t: (-t[0], t[1]))
        for score, host in scored[:5]:
            if await self._verify_candidate_host(
                host, cleaned, tokens, acronym,
            ):
                result["department_domain"] = host
                logger.info(
                    "[%s] dept domain via SERP: %s (score=%d, verified) "
                    "for name2=%r",
                    record_id, host, score, name2,
                )
                return

        # ── 2b) Path-based official page (no new SERP call) ───────────
        # Some institutions host the department at a PATH on the main or a
        # faculty domain ("clas.ufl.edu/chemistry") rather than a dedicated
        # subdomain. The subdomain scan above skips those (host == base).
        # Reuse the site:-restricted results already fetched: accept the
        # FULL URL — including path — of the first on-domain result whose
        # path/title carries the dept tokens AND whose page verifies.
        needles: set[str] = set(tokens)
        if acronym and len(acronym) >= 2:
            needles.add(acronym.lower())
        # §5b/§5c: collect the on-domain path candidates, DROP any whose path is
        # non-department content (news/events/archive), and rank by canonicality
        # (a shallow department landing page beats a deep dated sub-page) before
        # verifying — so an archived event URL no longer ties a landing page.
        path_candidates: list[tuple[int, str, int]] = []
        for idx, sr in enumerate(serp_results):
            try:
                parsed = urlparse(sr.url)
            except Exception:
                continue
            host = (parsed.hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if not (host == base or host.endswith("." + base)):
                continue
            if _host_prefix_is_generic(host, base):
                continue  # newsroom/admin host — the path can't redeem it
            path = (parsed.path or "").strip("/")
            if not path:
                continue  # bare domain handled by the host scan above
            if _path_is_generic(path):
                continue  # §5b: news/events/archive path — not a dept home
            hay = re.sub(r"[/\-_]+", " ", path).lower() + " " + (sr.title or "").lower()
            if not any(n in hay for n in needles):
                continue
            # Fix C(1) — lowest canonicality penalty first, then the URL as
            # the canonical id. The SERP index used to be the tiebreak, which
            # made the winner depend on the order the search API returned
            # equally-canonical paths in.
            path_candidates.append((_path_canonicality_penalty(path), sr.url, idx))
        path_candidates.sort(key=lambda t: (t[0], t[1]))
        for _penalty, cand_url, _idx in path_candidates:
            if await self._verify_candidate_url(cand_url, cleaned, tokens, acronym):
                result["department_domain"] = cand_url
                logger.info(
                    "[%s] dept domain via on-domain path page: %s for name2=%r",
                    record_id, cand_url, name2,
                )
                return

        # ── 3) SERP fallback (no site:) — cross-domain departments ────
        # Some institutions host their dept on a brand domain
        # (hopkinsmedicine.org for JHU medical departments). The
        # site:-filtered query above can't see those. Run an unrestricted
        # SERP and verify each candidate by fetching the page.
        # This is a SECOND SERP call per record, so it is opt-in: when
        # DEPT_PROBE_CROSS_DOMAIN is off (default) the probe stops here at
        # one SERP call and leaves department_domain null for the
        # cross-domain case rather than spending another query.
        if not self._settings.dept_probe_cross_domain:
            logger.info(
                "[%s] dept domain probe: cross-domain fallback disabled — "
                "stopping at one SERP call",
                record_id,
            )
            return
        name1 = (
            result.get("name1_enriched") or result.get("name1_original") or ""
        ).strip()
        if name1:
            query2 = f"{cleaned} {name1}"
        else:
            query2 = cleaned
        try:
            serp_results2 = await cached_serp(
                cache, self._search_client, query2, num_results=5,
            )
        except Exception as exc:
            logger.info(
                "[%s] dept domain probe: no-site SERP failed: %s",
                record_id, exc,
            )
            serp_results2 = []

        for sr in serp_results2:
            host = _host_of(sr.url)
            if not host:
                continue
            if _is_third_party_host(host):
                continue
            if _host_prefix_is_generic(host):
                continue
            # Verify the page actually describes this dept before
            # accepting a cross-domain host.
            if await self._verify_candidate_host(
                host, cleaned, tokens, acronym,
            ):
                result["department_domain"] = host
                logger.info(
                    "[%s] dept domain via cross-domain SERP: %s "
                    "for name2=%r",
                    record_id, host, name2,
                )
                return

        logger.info(
            "[%s] dept domain probe: no host matched for name2=%r "
            "core=%r tokens=%s acronym=%r",
            record_id, name2, core, sorted(tokens), acronym,
        )

    async def _verify_candidate_host(
        self,
        host: str,
        cleaned_phrase: str,
        tokens: set[str],
        acronym: str | None,
    ) -> bool:
        """Verify ``https://<host>/`` describes the department."""
        return await self._verify_candidate_url(
            f"https://{host}/", cleaned_phrase, tokens, acronym,
        )

    async def _verify_candidate_url(
        self,
        url: str,
        cleaned_phrase: str,
        tokens: set[str],
        acronym: str | None,
    ) -> bool:
        """Fetch *url* and verify the page actually describes the department.

        A page passes verification when EITHER:
        * the full *cleaned_phrase* appears in title/h1/breadcrumb, OR
        * at least 2 significant needles (tokens + acronym) appear there
          (or 1 needle, when only one is available).

        This rejects pages that resolve but don't describe the dept
        (e.g. ``science.mit.edu`` is the School of Science, not the
        Computer Science department).
        """
        try:
            page = await self._page_fetcher.fetch_page_content(url)
        except Exception:
            return False
        if page is None or page.is_empty():
            return False

        text = " ".join([
            (page.page_title or ""),
            (page.h1 or ""),
            (page.breadcrumb or ""),
        ]).lower()
        if not text.strip():
            return False

        if cleaned_phrase and cleaned_phrase.strip():
            phrase = cleaned_phrase.strip().lower()
            if len(phrase) >= 4 and phrase in text:
                return True

        needles: set[str] = set(tokens)
        if acronym and len(acronym) >= 2:
            needles.add(acronym.lower())
        if not needles:
            return False
        # §5d: accept morphological variants, not just the literal token, so
        # physics.nist.gov ("Physical Measurement Laboratory") verifies for a
        # "Physics" needle. A text word matches a needle when _seg_matches_needle
        # holds (substring / one a full prefix of the other — "chem" ← "chemistry")
        # OR they share a ≥5-char leading prefix ("physic"al ← "physic"s). The
        # needle-count thresholds are unchanged (≥2, or ≥1 for a single needle),
        # so science.mit.edu still fails a Computer Science query.
        text_words = re.findall(r"[a-z]+", text)

        def _needle_hit(n: str) -> bool:
            for w in text_words:
                if _seg_matches_needle(w, n):
                    return True
                i = 0
                while i < len(w) and i < len(n) and w[i] == n[i]:
                    i += 1
                if i >= 5:
                    return True
            return False

        matches = sum(1 for n in needles if _needle_hit(n))
        if len(needles) == 1:
            return matches >= 1
        return matches >= 2

    async def _resolve_person_affiliation(
        self,
        result: dict[str, Any],
        record: EnrichmentRecord,
        contact: str,
        pp_name2: str | None,
        start: float,
        cache: BatchCache,
    ) -> EnrichmentResult:
        """Stage 2b: find a person-only contact's institution + department.

        Runs a grounded web lookup, CONFIRMS the proposed institution against
        ROR in the record's country, and — only on confirmation — writes
        Name 1 (ROR's official name), the registry id/domain/website, and the
        department (via Tier 2A on the confirmed domain, falling back to the
        web-proposed department). Everything is flagged for review. On any
        failure the contact is kept, Name 1 is left empty, and the record is
        flagged for a manual lookup. ALWAYS short-circuits — Tier 3 never runs
        for these records, so it can neither fabricate nor overwrite.
        """
        affil = await run_person_affiliation(
            contact=contact,
            city=record.city,
            region=record.state,
            country=record.country,
            email=result.get("email_enriched") or record.email,
            search_client=self._search_client,
            llm_client=self._llm_client,
            settings=self._settings,
            cache=cache,
        )

        confirmed = None
        if affil.institution and affil.confidence in ("high", "medium"):
            country_code = country_to_iso_code(record.country)
            try:
                confirmed = await self._ror_client.call(
                    affil.institution,
                    country_code=country_code,
                    country=record.country,
                    city=record.city,
                    state=record.state,
                )
            except Exception:
                logger.exception(
                    "[%s] person_affiliation: ROR confirm failed", record.record_id,
                )
                confirmed = None
        _log_registry_rejections(result, "ror", confirmed)

        if confirmed and confirmed.get("matched"):
            official = (confirmed.get("official_name") or affil.institution).strip()
            # The chain is what makes this defensible: a search on the person,
            # a page fetch, a model that named their institution, and ROR
            # confirming that institution in the record's country. Four tools,
            # one value — not four competing sources.
            _affil_chain = ("serp", "fetch", "llm_person_affiliation", "ror")
            _write(
                result, "name1_enriched", official,
                Evidence(
                    producer_chain=_affil_chain,
                    tier=1,
                    confidence_scale=ROR_LOCAL,
                    confidence_value=confirmed.get("score"),
                    evidence_ref={
                        "ror_id": confirmed.get("ror_id"),
                        "proposed_by_llm": affil.institution,
                        "llm_confidence": affil.confidence,
                        "prompt_version": PERSON_AFFILIATION_PROMPT_VERSION,
                        "deployment": self._settings.openai_model,
                        # The constant, not a literal — a provenance record
                        # that can disagree with the request it describes is
                        # worse than no record.
                        "temperature": LLM_TEMPERATURE,
                        "seed": LLM_SEED if seed_supported() else None,
                    },
                    rule_id="person-affiliation:ror-confirmed",
                ),
            )
            _write(
                result, "ror_id", confirmed.get("ror_id"),
                registry_evidence(
                    "ror", confirmed.get("ror_id"),
                    rule_id="person-affiliation:ror-confirmed",
                ),
            )
            result["tier_used"] = 1
            # Name/id/domain are ROR's; the affiliation origin (web lookup on the
            # contact) is recorded in flag_reason. Confidence is capped at medium
            # because the person→org link came from the web, not the registry.
            result["source"] = "ROR"
            result["confidence"] = "medium"
            result["_ror_is_research"] = bool(confirmed.get("is_research_institution"))
            result["routing_type"] = (
                "research_institution"
                if confirmed.get("is_research_institution")
                else "company"
            )
            # ROR-confirmed in the record's country — registry provenance, so
            # the ownership guard passes on condition 1.
            domain = _apply_domain(
                result,
                confirmed.get("website"),
                registry="ROR",
                settings=self._settings,
            ).domain
            if confirmed.get("acronym") and confirmed["acronym"].strip():
                result["_ror_acronym"] = confirmed["acronym"].strip()

            # Department: prefer a Tier 2A lookup on the CONFIRMED domain (the
            # contact searched on the institution's own site), fall back to the
            # web-proposed department.
            department = affil.department
            if is_blank(pp_name2) and domain:
                try:
                    t2a = await run_tier2a(
                        record.record_id,
                        contact=contact,
                        institution=official,
                        domain=domain,
                        name2=None,
                        name3=None,
                        search_client=self._search_client,
                        page_fetcher=self._page_fetcher,
                        llm_client=self._llm_client,
                        cache=cache,
                        settings=self._settings,
                    )
                    if t2a.name2_enriched:
                        department = t2a.name2_enriched
                except Exception:
                    logger.exception(
                        "[%s] person_affiliation: Tier 2A dept lookup failed",
                        record.record_id,
                    )
            if department and is_blank(pp_name2):
                _write(
                    result, "name2_enriched", department,
                    llm_evidence(
                        ("serp", "fetch", "llm_tier2a"),
                        tier=2,
                        prompt_version=TIER2A_PROMPT_VERSION,
                        deployment=self._settings.openai_model,
                        self_reported=affil.confidence,
                        rule_id="person-affiliation:department",
                    ),
                )

            # No flag. The affiliation was confirmed against ROR through the
            # same country and distinctive-token guards as any other Tier 1
            # match, and the record leaves with the registry's official name,
            # its id and its domain — evidence a reviewer can audit.
            result["_pp_name1"] = official
            logger.info({
                "record_id": record.record_id,
                "step": "person_affiliation_confirmed",
                "contact": contact,
                "institution": official,
                "department": result.get("name2_enriched"),
            })
        else:
            result["_ev_person_unresolved"] = True
            result["_pp_name1"] = None
            logger.info({
                "record_id": record.record_id,
                "step": "person_affiliation_unresolved",
                "contact": contact,
                "proposed": affil.institution,
                "confidence": affil.confidence,
            })

        return await self._finalise_and_return(result, start, record, cache)

    async def _return_canonical_short_circuit(
        self,
        result: dict[str, Any],
        start: float,
        record: EnrichmentRecord,
        cache: BatchCache,
    ) -> EnrichmentResult:
        """Close out a record at the Tier 2 canonical stage.

        Reached when LLM canonicalisation ran on a record whose Name 2 was
        already populated. Stopping here is what keeps Tier 3 from
        overwriting a canonical unit name with a fabricated one. Called
        from two places: directly, when Tier 2A contact verification is
        not available for the record, and again after Tier 2A has run
        without producing a usable result.
        """
        result["tier_used"] = 2
        has_enriched = any(
            result.get(f"{f}_enriched") and result.get(f"{f}_enriched") != getattr(record, f)
            for f in DEPT_SLOTS
        )
        if has_enriched:
            result["source"] = "llm_canonical"
            result["confidence"] = "high"
            result["enrichment_status"] = "enriched"
            # No flag — "Tier 2 Canonical high confidence → No flag" is the
            # documented rule, and canonicalisation is a normalisation of a
            # value the record already carried, not a new claim about it.
        else:
            result["source"] = "passthrough"
            result["confidence"] = "low"
            result["enrichment_status"] = "unresolved"
            # Attempted, below threshold, input left in place — per field, so
            # finalisation can scope the flag to whichever slot is populated.
            result.setdefault("_ev_low_conf_unchanged", set()).update(
                DEPT_SLOTS,
            )
        return await self._finalise_and_return(result, start, record, cache)

    async def _retry_tier1_after_canonicalisation(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        candidate: str | None = None,
    ) -> None:
        """Re-run Tier 1 once, with the canonical name a later tier produced.

        The pipeline could already work out the right name and then throw the
        chance away: ROR misses on "MASSACHUSETTS INSITUTE OF TECHNOLOGY",
        company_canonical / Tier 3 / 2A / 2B produce "Massachusetts Institute
        of Technology", and nothing ever looks *that* string up — so the record
        ends with the correct official name and no registry ID, even though the
        corrected string resolves in ROR on the first try.
        (``person_affiliation`` already re-enters Tier 1 this way; the company
        canonicalisation and Tier 3 paths were terminal.)

        Runs at most once per record (``tier1_retry_attempted``), through the
        full normal path — the ROR country guard, the distinctive-token guard
        and the GLEIF name-verification guard all apply unchanged, and a retry
        that fails one of them is simply a miss. On a miss nothing is written:
        the record keeps whatever the earlier tier produced.

        ``record_type`` is deliberately NOT reassigned. Where the registry's
        view contradicts the value already on the record the contradiction is
        left standing and logged (``tier1_retry_type_conflict``) — reconciling
        it belongs to the record_type fix, not here.

        *candidate* (Fix 3, ``PAGE_EXTRACT_FEEDS_RETRY``, off by default) is a
        name from somewhere other than ``name1_enriched`` — the legal name a
        page read extracted. It is queried in place of the canonical name and
        spends its own once-per-record budget, so the two entry points cannot
        starve each other. Everything else is identical: same guards, same
        branch rules, same writes.
        """
        trace = _retry_trace(result, record.record_id)
        trace["called"] = True
        # The page-fed entry point has its own budget. Both are once per
        # record, and neither can consume the other's.
        attempt_key = (
            "tier1_page_retry_attempted" if candidate
            else "tier1_retry_attempted"
        )

        if result.get(attempt_key):
            trace["skipped_reason"] = RETRY_SKIP_ALREADY_ATTEMPTED
            return
        # Already carries a registry identity — nothing to recover.
        if result.get("ror_id") or result.get("lei_id"):
            trace["skipped_reason"] = RETRY_SKIP_ALREADY_HAS_ID
            return
        original = (result.get("_tier1_query_name") or "").strip()
        trace["query_original"] = original or None
        if not original:
            # Tier 1 never ran for this record (skipped tier / person path);
            # there is no "originally queried with" to compare against.
            trace["skipped_reason"] = RETRY_SKIP_NO_TIER1_QUERY
            return
        canonical = (candidate or result.get("name1_enriched") or "").strip()
        trace["query_canonical"] = canonical or None
        if not canonical:
            trace["skipped_reason"] = RETRY_SKIP_NO_CANONICAL
            return
        # Same normalisation the cache uses: a pure punctuation/case/accent
        # difference is not a corrected name and must not buy an API call.
        if normalize_key(canonical) == normalize_key(original):
            trace["skipped_reason"] = RETRY_SKIP_NORMALIZE_KEY_EQUAL
            return

        trace["fired"] = True
        trace["candidate_source"] = "page_read" if candidate else "canonical"
        result[attempt_key] = True
        self._tier1_retry_counts["attempts"] += 1
        country_code = result.get("_tier1_country_code")

        logger.info({
            "record_id": record.record_id,
            "step": "tier1_retry",
            "original_query": original,
            "canonical_query": canonical,
            "country_filter": country_code,
        })

        def _note_type_conflict(registry: str, registry_type: str) -> None:
            """Log where the registry disagrees with the branch the record was
            actually routed down. Since Fix 3 the disagreement is no longer
            left standing in the output — ``enrichment.classifier`` ranks this
            registry evidence above everything else in ``finalise`` — but the
            record still *ran* down the wrong branch, so the mismatch is worth
            surfacing. The batch summary counts it as
            ``routing_type_mismatch``."""
            current = result.get("routing_type")
            if current and current != registry_type:
                result["_tier1_retry_type_conflict"] = {
                    "registry": registry,
                    "registry_type": registry_type,
                    "routing_type": current,
                }
                logger.info({
                    "record_id": record.record_id,
                    "step": "tier1_retry_type_conflict",
                    "registry": registry,
                    "registry_says": registry_type,
                    "routed_as": current,
                })

        # ── ROR first, exactly as the first pass does ────────────────────
        trace["registries_queried"].append("ror")
        try:
            ror_res = await self._ror_client.call(
                canonical,
                country_code=country_code,
                country=record.country,
                city=record.city,
                state=record.state,
                record_domain=_record_domain_hint(result, record),
            )
        except Exception as exc:  # noqa: BLE001 — a retry must never fail a record
            logger.warning(
                "[%s] Tier 1 retry ROR raised (non-fatal): %s",
                record.record_id, exc,
            )
            ror_res = {"matched": False}
        _log_registry_rejections(result, "ror", ror_res)
        _retry_trace_guards(trace, "ror", ror_res)

        if ror_res.get("matched"):
            trace["hit"] = "ROR"
            self._tier1_retry_counts["hits_ror"] += 1
            # The retry attaches the identifier, so it must attach the name
            # too. Before Fix 4 this path wrote ror_id and the domain but left
            # name1_enriched as whatever the earlier tier produced, which is
            # the same defect as the suppressed first-pass write: a record
            # holding a ror_id while displaying a name that is not that ROR
            # record's official name. The retry runs through the identical
            # guards as the first pass, so a hit here is equally verified.
            _write_registry_name(
                result, "name1", ror_res.get("official_name"), registry="ROR",
                identifier=ror_res["ror_id"],
                rule_id="fix2:tier1-retry-after-canonicalisation",
            )
            record_registry_identity(
                result, "ROR", ror_res, name=ror_res.get("official_name"),
            )
            # The SECOND event on name1 for a record the retry rescues. The
            # first came from whichever tier produced the corrected name, and
            # the final value alone would not show that an LLM wrote first.
            _write(
                result, "ror_id", ror_res["ror_id"],
                registry_evidence(
                    "ror", ror_res["ror_id"],
                    rule_id="fix2:tier1-retry-after-canonicalisation",
                ),
            )
            result["tier_used"] = 1
            result["source"] = "ROR"
            result["confidence"] = "high"
            result["enrichment_status"] = "enriched"
            result["tier1_retry_hit"] = "ROR"
            result["_ror_is_research"] = bool(
                ror_res.get("is_research_institution")
            )
            _note_type_conflict(
                "ROR",
                "research_institution"
                if ror_res.get("is_research_institution")
                else "company",
            )
            # Registry provenance: the match passed ROR's country guard, so
            # the domain guard accepts on condition 1. This is what lets a
            # record that lost its domain to the ownership guard regain a
            # verified one — it runs before finalise raises the flag.
            _apply_domain(
                result,
                ror_res.get("website"),
                registry="ROR",
                settings=self._settings,
            )
            for uc in (2, 3):
                if uc not in result["use_cases_triggered"]:
                    result["use_cases_triggered"].append(uc)
            logger.info({
                "record_id": record.record_id,
                "step": "tier1_retry_hit",
                "registry": "ROR",
                "ror_id": result["ror_id"],
                "query": canonical,
            })
            return

        # ── LEI on the company branch, same rule as the first pass ───────
        # A name that looks like a research institution never reaches GLEIF.
        if looks_like_research_institution(canonical):
            trace["gleif_skipped"] = "looks_like_research_institution"
            return

        if not self._settings.lei_lookup_enabled:
            trace["gleif_skipped"] = "lei_lookup_disabled"
            return
        trace["registries_queried"].append("gleif")
        self._lei_counts["attempts"] += 1
        try:
            lei_res = await self._lei_client.call(
                canonical, country_code=country_code,
                city=record.city, state=record.state,
                record_domain=_record_domain_hint(result, record),
            )
        except Exception as exc:  # noqa: BLE001 — GLEIF must never fail a record
            self._lei_counts["errors"] += 1
            trace["gleif_outcome"] = f"exception:{type(exc).__name__}"
            logger.warning(
                "[%s] Tier 1 retry LEI raised (non-fatal): %s",
                record.record_id, exc,
            )
            return
        _log_registry_rejections(result, "gleif", lei_res)
        _retry_trace_guards(trace, "gleif", lei_res)

        if lei_res.get("error"):
            self._lei_counts["errors"] += 1
            trace["gleif_outcome"] = "error"
            return
        if not lei_res.get("matched"):
            self._lei_counts["misses"] += 1
            trace["gleif_outcome"] = "miss"
            return

        if lei_res.get("strategy") == "exact":
            self._lei_counts["hits_exact"] += 1
        else:
            self._lei_counts["hits_fuzzy"] += 1

        trace["hit"] = "gleif"
        self._tier1_retry_counts["hits_lei"] += 1
        _write_registry_name(
            result, "name1", lei_res.get("legal_name"), registry="GLEIF",
            identifier=lei_res.get("lei_id"),
            rule_id="fix2:tier1-retry-after-canonicalisation",
        )
        record_registry_identity(
            result, "GLEIF", lei_res, name=lei_res.get("legal_name"),
        )
        _write(
            result, "lei_id", lei_res.get("lei_id"),
            registry_evidence(
                "gleif", lei_res.get("lei_id"),
                rule_id="fix2:tier1-retry-after-canonicalisation",
            ),
        )
        _record_gleif_evidence(result, lei_res)
        result["tier_used"] = 1
        result["source"] = "gleif"
        result["confidence"] = lei_res.get("confidence", "high")
        result["enrichment_status"] = "enriched"
        result["tier1_retry_hit"] = "gleif"
        _note_type_conflict("GLEIF", "company")
        for uc in (2, 3):
            if uc not in result["use_cases_triggered"]:
                result["use_cases_triggered"].append(uc)
        logger.info({
            "record_id": record.record_id,
            "step": "tier1_retry_hit",
            "registry": "GLEIF",
            "lei_id": result["lei_id"],
            "query": canonical,
        })

    # ── Wikidata crosswalk lane ──────────────────────────────────────────

    async def _wikidata_crosswalk(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        name: str,
        country_code: str | None,
    ) -> bool:
        """Ask Wikidata for a pointer, and follow it if there is one.

        Runs on a record ROR missed, GLEIF missed (or never reached, on the
        research branch) and that therefore holds no registry identifier —
        between the registry miss and the web-evidence step. See
        :mod:`enrichment.wikidata` for the gauntlet and for why Wikidata is
        never the authority for a written value.

        Returns ``True`` **only** when a registry pointer resolved and the
        registry wrote the identity, so the caller can skip the LLM
        canonicalisation exactly as it does after a direct GLEIF hit. Every
        other outcome — witness, no match, ambiguity, unavailable — returns
        ``False`` and leaves the record to continue down the waterfall
        unchanged.

        Nothing on any path raises: a lane failure must never fail a record.
        """
        if not self._settings.wikidata_enabled:
            return False
        # The lane's precondition, re-checked here rather than trusted from the
        # call site: a registry identity is stronger than anything a wiki can
        # offer, and a record that has one has nothing to gain.
        if result.get("ror_id") or result.get("lei_id"):
            return False
        if not (name and name.strip()):
            return False

        counts = self._wikidata_counts
        counts["queried"] += 1
        try:
            outcome = await resolve_wikidata(
                record_id=record.record_id,
                name=name,
                city=record.city,
                region=record.state,
                client=self._wikidata_client,
                # No new threshold. This is the same supplied-name-vs-official-
                # name comparison GLEIF's verification guard makes, so it is
                # that guard's scorer at that guard's threshold.
                threshold=self._settings.lei_name_match_threshold,
                trace=self._settings.wikidata_trace,
            )
        except Exception as exc:  # noqa: BLE001 — the lane must never fail a record
            counts["unavailable"] += 1
            logger.warning(
                "[%s] Wikidata lane raised (non-fatal): %s",
                record.record_id, exc,
            )
            return False

        # Diagnostics first, so a rejected candidate is counted whatever the
        # record's final outcome turns out to be.
        if WIKIDATA_TYPE_REJECTED in outcome.reasons:
            counts["type_rejected"] += 1
        if WIKIDATA_COUNTRY_REJECTED in outcome.reasons:
            counts["country_rejected"] += 1

        if outcome.outcome == WIKIDATA_UNAVAILABLE:
            counts["unavailable"] += 1
            return False
        if outcome.outcome == WIKIDATA_AMBIGUOUS:
            counts["ambiguous"] += 1
            return False
        if not outcome.matched or outcome.item is None:
            counts["no_match"] += 1
            return False

        counts["matched"] += 1
        item = outcome.item
        result["_wikidata_qid"] = item.qid

        # ── Supersession, before anything else and independent of it ──────
        # A dissolved entity's registry record is still informative, so the
        # crosswalk below still runs; the flag stands whatever it finds.
        detail = outcome.supersession_detail()
        if detail:
            result["_ev_entity_superseded"] = detail
            counts["superseded_flagged"] += 1
            logger.info({
                "record_id": record.record_id,
                "step": "wikidata_entity_superseded",
                "qid": item.qid,
                "detail": detail,
                "name1": result.get("name1_enriched") or name,
            })

        # P856, stashed for the deferred domain check: at this point in the
        # waterfall the website paths have not run, so there is no candidate
        # domain to compare against yet. See
        # `_corroborate_domain_from_wikidata`.
        if item.website:
            result["_wikidata_website"] = item.website

        # ── Follow the pointer ────────────────────────────────────────────
        if item.ror_id:
            counts["crosswalk_ror"] += 1
            if await self._crosswalk_to_ror(record, result, item, country_code):
                counts["crosswalk_registry_hit"] += 1
                return True
        if item.lei_id:
            counts["crosswalk_lei"] += 1
            if await self._crosswalk_to_gleif(
                record, result, item, name, country_code,
            ):
                counts["crosswalk_registry_hit"] += 1
                return True

        # ── No pointer: a single witness, and treated as one ──────────────
        # An item that DID carry a pointer the registry then refused is not
        # promoted to a witness. The refusal is the registry's verdict on this
        # identity, and taking the wiki's word after the authority declined it
        # would invert the whole ordering this lane is built on.
        if item.ror_id or item.lei_id:
            return False

        counts["witness_only"] += 1
        self._write_wikidata_witness(result, outcome)
        return False

    async def _crosswalk_to_ror(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        item: Any,
        country_code: str | None,
    ) -> bool:
        """Re-query ROR by the item's ``P6782`` and let ROR write the identity.

        Every write below is the first-pass Tier 1 ROR block's, verbatim: the
        official name, the ``ror_id``, the tier/source/confidence triple, the
        research flag, and the registry-provenance domain. The provenance is
        ``ror``, not ``wikidata`` — because ROR is what produced these values.
        The crosswalk is recorded in the trace and the counters and nowhere in
        the record, which is the point.
        """
        try:
            ror_res = await self._ror_client.call_by_id(
                item.ror_id, country_code=country_code,
                # Fix D(2) — the crosswalk lane compares its registered
                # locality like every other lane. It is the one route that
                # picks an organisation without reading its name, so it is
                # the last route that should skip the address check.
                country=record.country, city=record.city, state=record.state,
            )
        except Exception as exc:  # noqa: BLE001 — never fail a record
            logger.warning(
                "[%s] Wikidata→ROR crosswalk raised (non-fatal): %s",
                record.record_id, exc,
            )
            return False
        _log_registry_rejections(result, "ror", ror_res)
        if not ror_res.get("matched"):
            logger.info({
                "record_id": record.record_id,
                "step": "wikidata_crosswalk_ror_miss",
                "qid": item.qid,
                "ror_id": item.ror_id,
            })
            return False

        _write_registry_name(
            result, "name1", ror_res.get("official_name"), registry="ROR",
            identifier=ror_res["ror_id"],
            rule_id="wikidata:crosswalk-ror",
        )
        # Fix D — what ROR says this organisation is called, where it is
        # registered, and which website it states. The crosswalk lane was the
        # one registry route that never recorded any of it, so the ONE route
        # that picks an organisation without reading its name was also the one
        # whose address was never checked against the record's. The client had
        # already done the comparison; nothing was listening.
        record_registry_identity(
            result, "ROR", ror_res, name=ror_res.get("official_name"),
        )
        _write(
            result, "ror_id", ror_res["ror_id"],
            registry_evidence(
                "ror", ror_res["ror_id"], score=ror_res.get("score"),
                rule_id="wikidata:crosswalk-ror",
            ),
        )
        acronym = (ror_res.get("acronym") or "").strip()
        if acronym:
            result["_ror_acronym"] = acronym
        result["tier_used"] = 1
        result["source"] = "ROR"
        result["confidence"] = "high"
        result["enrichment_status"] = "enriched"
        result["_ror_is_research"] = bool(ror_res.get("is_research_institution"))
        result["routing_type"] = (
            "research_institution"
            if ror_res.get("is_research_institution")
            else "company"
        )
        _apply_domain(
            result, ror_res.get("website"), registry="ROR",
            settings=self._settings,
        )
        for uc in (2, 3):
            if uc not in result["use_cases_triggered"]:
                result["use_cases_triggered"].append(uc)
        logger.info({
            "record_id": record.record_id,
            "step": "wikidata_crosswalk_hit",
            "registry": "ROR",
            "qid": item.qid,
            "ror_id": result["ror_id"],
            "official_name": result.get("name1_enriched"),
        })
        return True

    async def _crosswalk_to_gleif(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        item: Any,
        name: str,
        country_code: str | None,
    ) -> bool:
        """Re-query GLEIF by the item's ``P1278`` and let GLEIF write the identity.

        The record's own Name 1 is what GLEIF's name-verification guard scores
        the returned ``legalName`` against — unchanged, at
        ``LEI_NAME_MATCH_THRESHOLD``. A pointer to a record whose legal name
        does not verify is refused exactly as a searched-for candidate would
        be, which is what keeps a stale or vandalised wiki link from writing
        another company's name into the customer master.

        The GLEIF *name-lookup* counters (`lei_attempts` and friends) are
        deliberately not touched: they measure the Tier 1 search step, and
        folding a by-identifier fetch into `lei_hits_fuzzy` would misreport it.
        This lane's own counters carry it.
        """
        try:
            lei_res = await self._lei_client.call_by_id(
                item.lei_id, name, country_code=country_code,
                city=record.city, state=record.state,
            )
        except Exception as exc:  # noqa: BLE001 — never fail a record
            logger.warning(
                "[%s] Wikidata→GLEIF crosswalk raised (non-fatal): %s",
                record.record_id, exc,
            )
            return False
        _log_registry_rejections(result, "gleif", lei_res)
        if lei_res.get("error") or not lei_res.get("matched"):
            logger.info({
                "record_id": record.record_id,
                "step": "wikidata_crosswalk_lei_miss",
                "qid": item.qid,
                "lei_id": item.lei_id,
                "score": lei_res.get("score"),
                "error": bool(lei_res.get("error")),
            })
            return False

        _write_registry_name(
            result, "name1", lei_res.get("legal_name"), registry="GLEIF",
            identifier=lei_res.get("lei_id"),
            rule_id="wikidata:crosswalk-lei",
        )
        # Fix D — as on the ROR crosswalk above, and for the same reason.
        record_registry_identity(
            result, "GLEIF", lei_res, name=lei_res.get("legal_name"),
        )
        _write(
            result, "lei_id", lei_res.get("lei_id"),
            registry_evidence(
                "gleif", lei_res.get("lei_id"),
                rule_id="wikidata:crosswalk-lei",
            ),
        )
        _record_gleif_evidence(result, lei_res)
        result["tier_used"] = 1
        result["source"] = "gleif"
        result["confidence"] = lei_res.get("confidence", "high")
        result["enrichment_status"] = "enriched"
        for uc in (2, 3):
            if uc not in result["use_cases_triggered"]:
                result["use_cases_triggered"].append(uc)
        logger.info({
            "record_id": record.record_id,
            "step": "wikidata_crosswalk_hit",
            "registry": "GLEIF",
            "qid": item.qid,
            "lei_id": result["lei_id"],
            "legal_name": result.get("name1_enriched"),
        })
        return True

    @staticmethod
    def _write_wikidata_witness(
        result: dict[str, Any], outcome: WikidataOutcome,
    ) -> None:
        """The most a registry-pointerless match is allowed to do.

        ``operating_name`` — the field :mod:`enrichment.page_corroborator`
        already uses for "what another source calls this organisation" — and a
        marker that lets :mod:`enrichment.unchanged_state` count this as the
        one independent source ``unchanged-verified`` requires.

        ``name1_enriched`` is not written, and cannot be: a crowd-edited label
        is not a customer master's source of truth for a legal name. An
        ``operating_name`` a page read already established is not overwritten
        either — the site itself is the better witness of the two, and the
        field holds one value.
        """
        item = outcome.item
        if item is None:
            return
        if item.label and not (result.get("operating_name") or "").strip():
            result["operating_name"] = item.label
            result["operating_name_provenance"] = WIKIDATA_WITNESS_PROVENANCE
        result["_wikidata_corroboration"] = {
            "qid": item.qid,
            "label": item.label,
            "name_score": outcome.name_score,
            "website": item.website,
            "corroborated": True,
            "domain_corroborated": False,
        }

    async def _retain_wikidata_website(
        self, record: EnrichmentRecord, result: dict[str, Any],
    ) -> None:
        """The lane's corroboration-only pass, for a record that ALREADY holds
        a registry identity.

        The crosswalk lane returns early on such a record and is right to: a
        register outranks a wiki and a resolved record has no pointer to
        gain. What it does have to gain is the item's ``P856`` — an
        independent statement of the organisation's official website, and the
        only evidence that can verify a candidate domain the name comparator
        structurally cannot reach. An acronym host, a contraction of the name,
        a brand domain: the ownership guard documents all three as unreachable
        by name similarity, and every one of them is settled by a second
        source naming the same website.

        What this method may do is exactly one thing: stash the claim. It
        follows no pointer, writes no name, proposes no ``operating_name``,
        and touches no field. The gauntlet is the lane's own, unchanged, so a
        film, a foreign namesake or a low-scoring label is refused here for
        the same reasons it is refused there — and a refused item leaves
        nothing behind, so the guard is exactly as strict as it was.

        Runs before the website paths propose a candidate, because that is
        when the claim has to be on the record; the comparison itself is
        :meth:`_corroborate_domain_from_wikidata`, deferred to after them.

        Skipped when the domain the record carries already came FROM a
        registry: a website the register itself supplied has nothing to gain
        from a second statement of the same fact, and the call would buy
        nothing.
        """
        settings = self._settings
        if not (settings.wikidata_enabled and settings.wikidata_domain_corroboration):
            return
        if not (result.get("ror_id") or result.get("lei_id")):
            return          # the crosswalk lane's own population
        if result.get("_wikidata_qid") or result.get("_wikidata_website"):
            return          # the lane already ran on this record
        if (result.get("domain_verified_by") or "") == "registry":
            return
        name = (result.get("name1_enriched") or record.name1 or "").strip()
        if not name:
            return

        counts = self._wikidata_counts
        counts["corroboration_queried"] += 1
        try:
            outcome = await resolve_wikidata(
                record_id=record.record_id,
                name=name,
                city=record.city,
                region=record.state,
                client=self._wikidata_client,
                threshold=settings.lei_name_match_threshold,
                trace=settings.wikidata_trace,
            )
        except Exception as exc:  # noqa: BLE001 — never fail a record
            counts["unavailable"] += 1
            logger.warning(
                "[%s] Wikidata corroboration raised (non-fatal): %s",
                record.record_id, exc,
            )
            return

        item = outcome.item
        if not outcome.matched or item is None or not item.website:
            return
        counts["corroboration_matched"] += 1
        result["_wikidata_website"] = item.website
        logger.info({
            "record_id": record.record_id,
            "step": "wikidata_website_retained",
            "qid": item.qid,
            "website": item.website,
            "resolved_by": "ROR" if result.get("ror_id") else "GLEIF",
        })

    def _corroborate_domain_from_wikidata(self, result: dict[str, Any]) -> None:
        """Compare the item's ``P856`` with the record's candidate domain.

        Deferred to :meth:`_finalise_and_return` rather than run inside the
        lane, because when the lane runs the website paths have not proposed a
        candidate yet — there is nothing to compare against. The item's
        official website is stashed on the record at match time and settled
        here, after ``_maybe_resolve_website_bc`` and before the page read.

        Agreement counts as one corroborating source for the domain and feeds
        the same ``unchanged-verified`` marker the name match feeds.
        Disagreement is counted and **nothing else**: ``P856`` can be years
        stale, and a wiki field is not grounds to withdraw a domain the
        ownership guard accepted.
        """
        # Read, not popped: the same claim is the witness evidence the domain
        # ownership guard reads (`_domain_witnesses`), and this method runs
        # after the guard has already judged the candidate. `finalise` drops
        # the key.
        stated = result.get("_wikidata_website")
        if not stated:
            return
        accepted = (result.get("domain") or "").strip()
        rejected = result.get("_domain_unverified")
        candidate = accepted or (rejected if isinstance(rejected, str) else "")
        if not candidate:
            return
        agrees = wikidata_website_agrees(stated, candidate)
        if agrees is None:
            return
        corroboration = result.get("_wikidata_corroboration")
        if agrees:
            self._wikidata_counts["domain_corroborated"] += 1
            if isinstance(corroboration, dict):
                corroboration["domain_corroborated"] = True
        else:
            self._wikidata_counts["domain_disagree"] += 1
        logger.info({
            "record_id": result.get("record_id"),
            "step": "wikidata_domain_check",
            "stated": stated,
            "candidate": candidate,
            "agrees": agrees,
        })

    async def _finalise_and_return(
        self,
        result: dict[str, Any],
        start: float,
        record: EnrichmentRecord,
        cache: BatchCache,
    ) -> EnrichmentResult:
        """Resolve website (B/C if needed), probe for unit URL, run
        address Stage 1, finalise, and return."""
        # Tier 1 retry FIRST: a registry hit here supplies both the id and a
        # domain with registry provenance, which must be in place before the
        # website paths propose a candidate and before finalise decides whether
        # to raise `domain-unverified`.
        await self._retry_tier1_after_canonicalisation(record, result)
        # The Wikidata website claim for a record the registries resolved —
        # retained here, BEFORE a candidate domain exists, because that is
        # when the lane can still be asked. See `_retain_wikidata_website`.
        await self._retain_wikidata_website(record, result)
        await self._maybe_resolve_website_bc(record, result, cache)
        # Defensive: every path that writes a website now writes the matching
        # domain through _apply_domain, so the two can no longer diverge. Kept
        # so a website arriving by some other route still reaches the guard
        # rather than leaving the department probe without a base domain.
        if not result.get("domain") and result.get("website_url"):
            _apply_domain(
                result, result["website_url"], settings=self._settings,
            )
        # Wikidata's P856, settled now that there IS a candidate domain to
        # compare it against. Counts only, plus the corroboration marker —
        # never a withdrawal. No-ops on every record the lane did not match.
        self._corroborate_domain_from_wikidata(result)
        # Fix 3 — read the candidate site. After the website paths have
        # proposed (and the ownership guard has judged) a candidate, so there
        # IS one to read; before the department probe, so a domain the page
        # read withdraws is not then mined for department URLs.
        await self._corroborate_domain(record, result)
        await self._probe_department_url(record.record_id, result, cache)
        await self._run_address_stage(result, record)
        result = finalise(result, start)
        self._emit_retry_trace(record, result)
        return EnrichmentResult(**result)

    async def _corroborate_domain(
        self, record: EnrichmentRecord, result: dict[str, Any],
    ) -> None:
        """Fix 3 — open the candidate website and see whether it names this
        organisation.

        Runs for a record that HAS a candidate — accepted, or discarded by the
        ownership guard and remembered in ``_domain_unverified`` — and is not
        already registry-resolved: a ROR/GLEIF identity is stronger evidence
        than a page, and re-reading the site could only weaken it.

        What the verdict is allowed to do, and what it is not:

        * ``corroborated`` — the ``domain-unverified`` flag is withdrawn (the
          reviewer's question has been answered) and the extracted identity is
          written to ``operating_name``. The ``domain`` FIELD is deliberately
          not written for a candidate the ownership guard refused: accepting on
          a page read would be a fifth ownership condition, and the guard is
          out of this fix's scope. See corroborator_report.md — it is the one
          change this fix recommends and does not make.
        * ``name_mismatch`` — the page names someone else. An accepted domain
          is **withdrawn** (this is johnsoncontrols.com for "AB Controls,
          Inc."); an already-unverified one keeps its flag. Either way the
          reason gains a clause naming what the page said.
        * ``contradicted`` — the page names this organisation but places it
          elsewhere. Noted, never acted on: the name matched, so the site is
          probably right and it is the record's address that is in doubt, which
          is not what ``domain-unverified`` asks a reviewer to check.
        * ``fetch_unavailable`` / ``parked`` / ``no_identity`` — nothing
          changes. We could not look, so we learned nothing, and "learned
          nothing" must never read as either verdict.

        Never writes ``name1_enriched``.
        """
        if not self._settings.page_corroboration_enabled:
            return
        if result.get("ror_id") or result.get("lei_id"):
            return

        accepted = (result.get("domain") or "").strip()
        rejected = result.get("_domain_unverified")
        candidate = accepted or (rejected if isinstance(rejected, str) else "")
        if not candidate:
            return
        name1 = _domain_evidence_name1(result)
        if not name1:
            return

        self._page_counts["attempted"] += 1
        corroboration = await corroborate(
            record_id=record.record_id,
            domain=candidate,
            name1=name1,
            city=record.city,
            region=record.state,
            country=record.country,
            postal_code=record.postal_code,
            fetcher=self._page_fetcher,
            cache=self._page_cache,
            llm_client=self._llm_client,
            threshold=self._settings.page_name_match_threshold,
            timeout=self._settings.page_read_timeout_seconds,
        )
        self._page_counts[corroboration.outcome] = (
            self._page_counts.get(corroboration.outcome, 0) + 1
        )
        result["_page_corroboration"] = corroboration.as_dict()
        statement = corroboration.statement
        stated = statement.stated_org_name if statement else None

        if page_identifies_record(corroboration):
            # The reviewer's question — "does this site belong to this
            # organisation?" — has been answered by the site itself, so the
            # answer is written to the field rather than only to the flag.
            #
            # This is the fifth ownership condition, and closing the open item
            # `corroborator_report.md` recorded. The three earlier conditions
            # ask whether some OTHER evidence ties the candidate to the record;
            # this one asks the site. Refusing to consult it while flagging the
            # row "verify this domain" asked a reviewer to do by hand the one
            # check the pipeline had already run and then discarded. It accepts
            # at `provisional` and never at `verified` — a page fetched from
            # the domain it vouches for is one source, not two.
            #
            # A candidate the guard ALREADY accepted is untouched:
            # `_apply_domain` returns the standing decision while `domain` is
            # set, so this can only ever fill an empty field.
            if not (result.get("domain") or "").strip():
                decision = _apply_domain(
                    result,
                    corroboration.source_url or f"https://{candidate}",
                    settings=self._settings,
                    producer_chain=("page_read",),
                    page_identity=True,
                )
                if decision.domain:
                    self._page_counts["domain_accepted"] += 1
            if result.pop("_domain_unverified", None) is not None:
                result["domain_rejected"] = False
                self._page_counts["flag_cleared"] += 1
            # Fix D — the web's claim about this organisation's identity,
            # for the cross-source gate. `operating_name` itself is the
            # witness field; this is the same value under the name the gate
            # looks for.
            result["_src_name_web"] = stated
            result["operating_name"] = stated
            result["operating_name_provenance"] = operating_name_provenance(
                candidate,
            )
            # The extraction date is no longer in the provenance column (it
            # was a decaying token in a field read as a claim). It is not
            # lost: it is on the cache entry the string was always taken
            # from, and on this trace line.
            #
            # Emitted on `enrichment.trace.page` and NOT on this module's
            # logger, which is the difference between a trace and a hope: a
            # batch run attaches its capture handler to the `enrichment.trace.*`
            # loggers by name (`scripts/run_batch.py`), so a line on the module
            # logger reaches no trace file and the date would have been
            # deleted from the export path into nothing. Measured — the first
            # version of this line was on `logger` and produced zero rows in
            # the batch trace.
            page_trace_logger.info(json.dumps({
                "record_id": result.get("record_id"),
                "step": "operating_name_extracted",
                "domain": candidate,
                "fetched_at": corroboration.fetched_at,
                "stated_org_name": stated,
                "provenance": result["operating_name_provenance"],
            }, default=str))
            await self._maybe_feed_retry_from_page(record, result, corroboration)
            return

        if corroboration.outcome == NAME_MISMATCH:
            # not name-consistent — the accept above did not fire.
            note = f"its page states {stated!r}"
            if statement and statement.stated_city:
                note += f" in {statement.stated_city}"
            # Withdrawal needs TWO independent disagreements, and the
            # geographic one at region or country granularity.
            #
            # Measured, not assumed. On the chemspeed batch a name score below
            # the threshold on its own withdrew four correct domains: the page
            # simply carried the fuller legal name ("AquaPhoenix" for
            # "AquaPhoenix Scientific, Inc.", "Analytical Sales and Services,
            # Inc." for "Analytical Sales"). `token_sort_ratio` is
            # length-sensitive by design — that is what makes it safe as
            # GLEIF's guard — and a brand-vs-legal-name variant is exactly the
            # shape it scores low. Requiring the page to ALSO place the
            # organisation in a different state or country leaves the one true
            # positive in that batch standing (Apollo Organic Synthesis in NY
            # against a page for Apollo Olive Oil in Northern California) and
            # drops all four false ones. City alone is not enough: a plant and
            # a head office in one state (Houston / Baytown, TX) are one
            # company.
            elsewhere = location_decides(corroboration)
            if accepted and elsewhere:
                self._withdraw_domain(result, accepted, corroboration, name1)
                self._page_counts["withdrawn"] += 1
                note += "; the page places it in a different state or country"
            elif accepted:
                # Reported, not acted on. The reviewer gets the page's own
                # words and decides; the pipeline does not destroy a domain on
                # a name difference alone.
                self._page_counts["mismatch_not_withdrawn"] += 1
            result["_domain_page_note"] = note
            return

        if corroboration.outcome == CONTRADICTED and rejected:
            detail = corroboration.location_detail or "the page places it elsewhere"
            result["_domain_page_note"] = (
                f"its page names this organisation but {detail}"
            )

    async def _maybe_feed_retry_from_page(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        corroboration: "Corroboration",
    ) -> None:
        """Offer a page-extracted legal name to Stage 5 as a lookup candidate.

        Config-gated on ``PAGE_EXTRACT_FEEDS_RETRY`` and **off by default**.
        Fix 1's trace showed Stage 5's yield on this population is bounded by
        GLEIF's coverage of private US SMBs rather than by the supply of
        candidate names, so this buys API calls before it buys identifiers —
        it is built, measured and left off rather than assumed useful.

        Two conditions beyond the flag, both from the page statement itself:
        the name must carry an explicit legal form (a brand without one is not
        a registry key), and it must differ materially from Name 1 under
        ``normalize_key`` — the same equality Stage 5 applies to its own
        candidate, since a punctuation variant cannot resolve where the
        original did not.

        Every retry guard applies unchanged: ROR's country and
        distinctive-token guards, GLEIF's name verification, the
        research-institution branch rule. A candidate a guard refuses is a
        clean miss.
        """
        if not self._settings.page_extract_feeds_retry:
            return
        statement = corroboration.statement
        if statement is None or not statement.legal_form_present:
            return
        extracted = (statement.stated_org_name or "").strip()
        current = (result.get("name1_enriched") or "").strip()
        if not extracted or normalize_key(extracted) == normalize_key(current):
            return
        if result.get("ror_id") or result.get("lei_id"):
            return

        logger.info({
            "record_id": record.record_id,
            "step": "page_extract_feeds_retry",
            "candidate": extracted,
            "name1": current,
            "source_url": corroboration.source_url,
        })
        await self._retry_tier1_after_canonicalisation(
            record, result, candidate=extracted,
        )

    @staticmethod
    def _withdraw_domain(
        result: dict[str, Any],
        domain: str,
        corroboration: "Corroboration",
        name1: str,
    ) -> None:
        """Take back a domain the ownership guard accepted and the page read
        then refuted.

        The only place in the pipeline that removes an accepted ``domain``.
        Recorded as a guard rejection rather than as a silent blank: the
        pipeline had an answer, published it, and then found evidence against
        it, which is the case most worth being able to defend afterwards.
        """
        _write(
            result, "domain", None,
            Evidence(
                producer_chain=("page_read",),
                confidence_scale=FUZZY_RATIO,
                confidence_value=corroboration.name_score,
                evidence_ref={
                    "withdrawn": domain,
                    "source_url": corroboration.source_url,
                    "stated_org_name": (
                        corroboration.statement.stated_org_name
                        if corroboration.statement else None
                    ),
                },
                rule_id="fix3:page-read-withdraws-domain",
            ),
        )
        result["website_url"] = None
        result["_website_raw"] = None
        result["domain_verified_by"] = None
        result["domain_rejected"] = True
        # The withdrawn domain, so `domain-unverified`'s reason still names the
        # site a reviewer has to look at.
        result["_domain_unverified"] = domain
        result.reject(
            "domain", domain, GUARD_PAGE_IDENTITY,
            reason=(
                "the site's own page states a different organisation's "
                "identity"
            ),
            evidence=Evidence(
                producer_chain=("page_read",),
                confidence_scale=FUZZY_RATIO,
                confidence_value=corroboration.name_score,
                evidence_ref={
                    "claimed_for": name1,
                    "source_url": corroboration.source_url,
                    "stated_org_name": (
                        corroboration.statement.stated_org_name
                        if corroboration.statement else None
                    ),
                },
                rule_id="page-identity-guard",
            ),
        )
        logger.info({
            "record_id": result.get("record_id"),
            "step": "domain_withdrawn_by_page_read",
            "domain": domain,
            "claimed_for": name1,
            "stated_org_name": (
                corroboration.statement.stated_org_name
                if corroboration.statement else None
            ),
        })

    def _emit_retry_trace(
        self, record: EnrichmentRecord, result: dict[str, Any],
    ) -> None:
        """Emit one Stage 5 trace line, then drop the transient slot.

        Runs after ``finalise`` so eligibility is judged from the state the
        record actually ships in — the name slots have settled, the registry
        identifiers are final, and the derived provenance scalar names whoever
        authored ``name1_enriched`` last. The slot is popped unconditionally,
        tracing on or off, so it never reaches pydantic validation.
        """
        trace = result.pop("_retry_trace", None)
        if not self._settings.retry_trace:
            return
        if trace is None:
            trace = _retry_trace_new(record.record_id)
        # A tier — not the input passthrough — authored the final Name 1, and
        # the record still has no registry identity. That is exactly the
        # population Stage 5 exists for, judged from the shipped record rather
        # than from anything the retry itself recorded, so a record the retry
        # never reached is still counted as eligible.
        # Through the grammar's own parser, never `split(":")`: a
        # `web:{domain}:provisional` scalar carries two colons and the naive
        # split puts the domain in the confidence slot. Here it happens to
        # give the same answer, which is exactly why the naive form is worth
        # removing while it is still harmless.
        try:
            source, _, _ = parse_provenance(result.get("name1_provenance") or "")
        except ProvenanceGrammarError:
            source = ""
        has_id = bool(result.get("ror_id") or result.get("lei_id"))
        trace["eligible"] = (
            bool(source) and source != SOURCE_INPUT and not has_id
        )
        if not trace["called"]:
            trace["skipped_reason"] = RETRY_SKIP_NOT_CALLED
        trace.update({
            "name1_original": record.name1,
            "name1_final": result.get("name1_enriched"),
            "name1_provenance": result.get("name1_provenance"),
            "ror_id": result.get("ror_id"),
            "lei_id": result.get("lei_id"),
            "flag_codes": list(result.get("flag_codes") or ()),
            "country": record.country,
        })
        retry_trace_logger.info(json.dumps(trace, default=str))

    async def _run_address_stage(
        self,
        result: dict[str, Any],
        record: EnrichmentRecord,
    ) -> None:
        """Clean, extract, route, and normalise address fields. Runs on
        every return path so unresolved/failed records also get the
        deterministic address cleanup. Address-stage exceptions are
        swallowed — the name enrichment result must still surface."""
        def _pick(base: str) -> str | None:
            return result.get(f"{base}_enriched") or result.get(f"{base}_original")

        # Streets: preprocessing owns them (it may have emptied a slot). Use the
        # recorded post-preprocess values when present, so a cleared slot stays
        # cleared instead of falling back to the raw original.
        pp_streets = result.get("_pp_streets")

        def _street(base: str) -> str | None:
            if pp_streets is not None and base in pp_streets:
                return pp_streets[base]
            return _pick(base)

        try:
            addr = await process_address(
                record_id=record.record_id,
                name1=_pick("name1"),
                name2=_pick("name2"),
                name3=_pick("name3"),
                name4=_pick("name4"),
                name5=_pick("name5"),
                street=_street("street1"),
                street_2=_street("street2"),
                street_3=_street("street3"),
                street_4=_street("street4"),
                street_5=_street("street5"),
                city=record.city,
                state=record.state,
                zip_code=record.zip,
                country=record.country,
                po_box=record.po_box,
                care_of_enriched=_pick("care_of"),
                llm_client=self._llm_client,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Address Stage 1 failed for %s: %s",
                record.record_id, exc,
            )
            return
        merge_address_into_result(result, addr)

    async def _run_lei_lookup(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
        name: str,
        country_code: str | None,
    ) -> bool:
        """Tier 1 LEI: resolve official legal name + LEI from GLEIF for a
        company record (the company counterpart to ROR).

        On a verified match, writes ``name1_enriched`` / ``lei_id`` /
        classification / provenance and returns True. On miss /
        below-threshold / API error, leaves the result untouched and
        returns False so the caller falls through to the existing LLM
        company-canonical path. A GLEIF failure NEVER fails the record.

        ``domain`` is intentionally left as-is: GLEIF has no website field,
        and on a ROR company match ROR's domain must be preserved.
        """
        if not self._settings.lei_lookup_enabled:
            return False
        if not name or not name.strip():
            return False

        self._lei_counts["attempts"] += 1
        try:
            lei_res = await self._lei_client.call(
                name, country_code=country_code,
                city=record.city, state=record.state,
                record_domain=_record_domain_hint(result, record),
            )
        except Exception as exc:  # noqa: BLE001 — GLEIF must never fail a record
            self._lei_counts["errors"] += 1
            logger.warning(
                "[%s] Tier 1 LEI lookup raised (non-fatal): %s",
                record.record_id, exc,
            )
            return False

        logger.info({
            "record_id": record.record_id,
            "step": "tier1_lei",
            "query": name,
            "country_filter": country_code,
            "matched": lei_res.get("matched"),
            "strategy": lei_res.get("strategy"),
            "lei_id": lei_res.get("lei_id"),
            "legal_name": lei_res.get("legal_name"),
            "score": lei_res.get("score"),
        })

        _log_registry_rejections(result, "gleif", lei_res)

        if lei_res.get("error"):
            self._lei_counts["errors"] += 1
            return False
        if not lei_res.get("matched"):
            self._lei_counts["misses"] += 1
            return False

        if lei_res.get("strategy") == "exact":
            self._lei_counts["hits_exact"] += 1
        else:
            self._lei_counts["hits_fuzzy"] += 1

        # The GLEIF token_sort_ratio guard has already verified this match,
        # so the legal name is written unconditionally — same rule as ROR.
        _write_registry_name(
            result, "name1", lei_res.get("legal_name"), registry="GLEIF",
            identifier=lei_res.get("lei_id"),
            rule_id="tier1-lei:name-verified",
        )
        # Fix D — GLEIF's claim about this organisation's identity and
        # registered address, for the cross-source gate in finalise.
        record_registry_identity(
            result, "GLEIF", lei_res, name=lei_res.get("legal_name"),
        )
        _write(
            result, "lei_id", lei_res.get("lei_id"),
            registry_evidence(
                "gleif", lei_res.get("lei_id"),
                rule_id=f"tier1-lei:{lei_res.get('strategy')}",
            ),
        )
        # An LEI proves legal registration, not commercial status — universities,
        # hospitals and foundations hold LEIs too. The classification evidence is
        # recorded; enrichment.classifier decides, in finalise().
        _record_gleif_evidence(result, lei_res)
        result["tier_used"] = 1
        result["source"] = "gleif"
        result["confidence"] = lei_res.get("confidence", "high")
        result["enrichment_status"] = "enriched"
        if 2 not in result["use_cases_triggered"]:
            result["use_cases_triggered"].append(2)
        if 3 not in result["use_cases_triggered"]:
            result["use_cases_triggered"].append(3)
        return True

    async def _enrich_single(
        self,
        record: EnrichmentRecord,
        options: EnrichmentOptions,
        cache: BatchCache,
    ) -> EnrichmentResult:
        """Run the full tier-escalation pipeline for one record."""
        result = _init_result(record)
        start = time.monotonic()
        # Fix B — so a frozen-cache miss five frames down can name the record
        # it left short of evidence. asyncio gives each concurrently-enriched
        # record its own value; the token is not reset because the task ends
        # with this call.
        current_record_id.set(record.record_id)

        try:
            # ── UC 0: name overflow check ────────────────────────────
            # One LLM call per adjacent name pair. If any upper Name +
            # the Name below it read as ONE continuous organisation
            # name, flag the record and return immediately — no other
            # tier runs, no auto-correction. Flagged records go to
            # manual review. Pairs whose two values are identical
            # (case/whitespace-normalised) are skipped inside the check:
            # two equal strings are duplicates, not an overflow split,
            # and UC 12 dedup in preprocess handles them.
            block_names = {slot: getattr(record, slot, None) for slot in NAME_SLOTS}
            overflow = await run_overflow_check_block(
                record_id=record.record_id,
                names=block_names,
                llm_client=self._llm_client,
            )
            if overflow.is_overflow:
                logger.info({
                    "record_id": record.record_id,
                    "step": "uc0_overflow_flagged",
                    "fields": overflow.fields,
                    "confidence": overflow.confidence,
                    "reasoning": overflow.reasoning,
                })
                # Pass through originals untouched. Flag only.
                for slot in NAME_SLOTS:
                    value = block_names.get(slot)
                    if value and value.strip():
                        _write(
                            result, f"{slot}_enriched", value.strip(),
                            deterministic_evidence(
                                "uc0:overflow-passthrough",
                                producer="input", tier=1,
                                evidence_ref={
                                    "overflow_fields": list(
                                        overflow.fields or (),
                                    ),
                                },
                            ),
                        )
                result["routing_type"] = "unknown"
                result["tier_used"] = 1
                result["source"] = "pattern_match"
                result["confidence"] = overflow.confidence
                result["enrichment_status"] = "unresolved"
                result["_ev_overflow"] = overflow.fields
                result["use_cases_triggered"] = [0]
                return await self._finalise_and_return(result, start, record, cache)

            # ── PREPROCESS (UC 6, 7, 8, 9): deterministic cleanup ─────
            # Pulls emails, addresses, contact-person names, and AP
            # references out of name fields and moves them to the
            # correct slots. LLM is only called for plain-name
            # candidates (UC 7 Pattern B2) when the pattern is
            # suspicious — no title, no org signals, 2-3 capitalised
            # words.
            suspicious = find_suspicious_plain_names(
                *(getattr(record, slot, None) for slot in NAME_SLOTS),
            )
            person_verdicts = await llm_classify_plain_names_async(
                self._llm_client, suspicious,
            ) if suspicious else {}

            pre = preprocess_record(
                name1=record.name1,
                name2=record.name2,
                name3=record.name3,
                name4=record.name4,
                name5=record.name5,
                contact=record.contact,
                email=record.email,
                street1=result["street1_original"],
                street2=record.street2,
                street3=record.street3,
                street4=record.street4,
                street5=record.street5,
                house_number=record.house_number,
                llm_person_verdicts=person_verdicts,
            )

            if pre.use_cases:
                logger.info({
                    "record_id": record.record_id,
                    "step": "preprocess",
                    "use_cases": pre.use_cases,
                    "flags": pre.flags,
                })
            for uc in pre.use_cases:
                if uc not in result["use_cases_triggered"]:
                    result["use_cases_triggered"].append(uc)

            # Track which name fields were touched by preprocessing.
            # "cleared" = field was non-empty and is now None/empty.
            # "changed" = field value is different (includes cleared).
            # Both types prevent finalise() from restoring the original.
            preprocess_cleared: set[str] = set()
            for base in NAME_SLOTS:
                pre_val = getattr(pre, base, None)
                orig = getattr(record, base, None)
                orig_stripped = (orig or "").strip()
                pre_stripped = (pre_val or "").strip()
                if orig_stripped and not pre_stripped:
                    preprocess_cleared.add(base)
                elif orig_stripped and pre_stripped and orig_stripped != pre_stripped:
                    # Preprocessing changed (but didn't clear) the value
                    # — write it as the enriched value now so finalise()
                    # doesn't overwrite it with the original. Example:
                    # "Accounts Payable Dept" → "Accounts Payable".
                    _write(
                        result, f"{base}_enriched", pre_stripped,
                        deterministic_evidence(
                            "preprocess:value-rewritten",
                            producer="preprocess",
                            evidence_ref={"input": orig_stripped},
                        ),
                    )
                elif not orig_stripped and pre_stripped:
                    # Preprocessing populated a previously empty slot
                    # (UC 14 name3 → name2 shift). Record the new value
                    # so downstream tiers and finalise() see it.
                    _write(
                        result, f"{base}_enriched", pre_stripped,
                        deterministic_evidence(
                            "preprocess:slot-populated",
                            producer="preprocess",
                            evidence_ref={"input": orig_stripped or None},
                        ),
                    )
            result["_preprocess_cleared"] = preprocess_cleared
            # Names where UC 11 rewrote a DBA variant. The preprocessed
            # value IS the canonical form — record it so finalise() can
            # restore it if any downstream tier (company_canonical,
            # tier2_canonical, tier3) overwrites with an LLM "cleanup"
            # that strips the DBA marker.
            result["_dba_values"] = {
                f: getattr(pre, f)
                for f in pre.dba_fields
                if getattr(pre, f)
            }
            # If preprocessing populated any care_of/contact/email/
            # street/name field, record the enriched value now. (Final
            # passthrough in finalise() will retain originals for
            # untouched fields.)
            if pre.care_of is not None and pre.care_of != result["care_of_original"]:
                result["care_of_enriched"] = pre.care_of
            if pre.contact is not None and pre.contact != result["contact_original"]:
                result["contact_enriched"] = pre.contact
            if pre.email is not None and pre.email != result["email_original"]:
                result["email_enriched"] = pre.email
            for slot in ("street1", "street2", "street3", "street4", "street5"):
                v = getattr(pre, slot)
                if v is not None and v != result[f"{slot}_original"]:
                    result[f"{slot}_enriched"] = v
            # Preprocessing is the source of truth for the street slots (it may
            # have EMPTIED a slot by routing its content to a Name/Building
            # field). Record the post-preprocess street values — including the
            # cleared Nones — so the address stage uses them instead of falling
            # back to the raw original street ("Finance/Procurement" routed to a
            # Name must not reappear in Street 1).
            result["_pp_streets"] = {
                slot: getattr(pre, slot)
                for slot in ("street1", "street2", "street3", "street4", "street5")
            }
            # A named building pulled out of a name field. Stashed as a
            # transient and applied in finalise() AFTER the address stage, so
            # a building extracted from the street fields takes precedence and
            # this only fills the Building slot when it is otherwise empty.
            if pre.building:
                result["_pp_building"] = pre.building

            # From here on, the tiers work with the PREPROCESSED names.
            pp_name1 = pre.name1
            pp_name2 = pre.name2
            pp_name3 = pre.name3
            pp_name4 = pre.name4
            pp_name5 = pre.name5
            pp_contact = pre.contact
            pp_street1 = pre.street1

            # ── Person-only Name 1: discover the contact's affiliation ───
            # Name 1 held only a person's name (now moved to Contact), leaving
            # no organisation for the tiers to enrich. Fetching the contact's
            # institution + department is a core deliverable, so we run a
            # grounded web lookup (Stage 2b) — but guard it hard against the
            # ways it went wrong before:
            #   * confirm the web-proposed institution against ROR IN THE
            #     RECORD'S COUNTRY (rejects wrong-country matches), and
            #   * take the official name / id / domain from ROR — never a
            #     website-resolver guess, and
            #   * ALWAYS short-circuit here (whether or not we found an org) so
            #     Tier 3 can never fabricate an institution or overwrite the
            #     confirmed one.
            if (
                is_blank(pp_name1)
                and pp_contact and pp_contact.strip()
                and getattr(pre, "name1_was_person", False)
            ):
                return await self._resolve_person_affiliation(
                    result, record, pp_contact, pp_name2, start, cache,
                )

            # Stash for the post-Tier-1 website resolver. Read by
            # _maybe_resolve_website_bc at every return path.
            result["_pp_name1"] = pp_name1

            # Stash the dept-lookup precondition signals BEFORE Tier 1
            # so that finalise() — invoked by every return path
            # including Tier 1's short-circuit — can flag a research
            # institution with no actionable signal (no dept + no
            # contact, or multiple contacts).
            # Any populated department slot counts, not only Name 2.
            # Preprocessing packs the block leftward so a department normally
            # lands in Name 2, but a slot the packing could not reach (or a
            # value a tier writes later) is just as much a signal.
            result["_has_dept_signal"] = bool(
                any(
                    (getattr(pre, slot, None) or "").strip()
                    for slot in DEPT_SLOTS
                )
                or (pp_contact and pp_contact.strip())
            )
            result["_multi_contact"] = has_multiple_contacts(pp_contact)

            # Preprocessing signals that survive into the review model.
            # `slots-full` is UC 0's defect seen from the other end — content
            # the SAP field split could not place — so it reads as overflow.
            if any("slots-full" in f for f in pre.flags):
                result["_ev_overflow"] = True
            if any("email-conflict" == f for f in pre.flags):
                result["_ev_email_conflict"] = True

            institution_domain: str | None = None

            # ── TIER 1: ROR ──────────────────────────────────────────────

            if not is_blank(pp_name1):
                # Resolve country to ISO alpha-2 for the ROR query filter
                country_code = country_to_iso_code(record.country)

                # Clean any residual address fragments that leaked into
                # name1 using the record's own structured address fields
                # (preprocessing may have already pulled some out; this
                # is a second defensive pass that uses pp_street1 in
                # case preprocessing moved content there).
                name1_cleaned = strip_address_fragments(
                    pp_name1,
                    street=pp_street1 or record.street,
                    city=record.city,
                    state=record.state,
                    zip_code=record.zip,
                ) or (pp_name1 or "").strip()

                if name1_cleaned != (pp_name1 or "").strip():
                    logger.info({
                        "record_id": record.record_id,
                        "step": "tier1_name1_cleaned",
                        "original": pp_name1,
                        "cleaned": name1_cleaned,
                    })

                # The name Tier 1 was actually queried with. A later tier may
                # replace name1_enriched with the organisation's real name
                # (company_canonical, Tier 3, 2A, 2B); the retry in
                # _finalise_and_return compares against this to decide whether
                # Tier 1 has ever seen the corrected name.
                result["_tier1_query_name"] = name1_cleaned
                result["_tier1_country_code"] = country_code

                ror_parent = await self._ror_client.call(
                    name1_cleaned,
                    country_code=country_code,
                    country=record.country,
                    city=record.city,
                    state=record.state,
                    record_domain=_record_domain_hint(result, record),
                )

                _log_registry_rejections(result, "ror", ror_parent)

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier1_ror_parent",
                    "query": name1_cleaned,
                    "country": record.country,
                    "country_filter": country_code,
                    "matched": ror_parent["matched"],
                    "score": ror_parent.get("score"),
                    "official_name": ror_parent.get("official_name"),
                    "is_research": ror_parent.get("is_research_institution"),
                    "domain": ror_parent.get("domain"),
                })

                if ror_parent["matched"]:
                    # Write name1 enrichment IMMEDIATELY so later tier
                    # failures don't lose it.
                    #
                    # Unconditional on a verified match. The match already
                    # passed ROR's country guard and the distinctive-token /
                    # identifier-token guards in tier1_ror, and README's
                    # pipeline walkthrough says plainly: "Write official ROR
                    # name to name1_enriched". There is deliberately no second
                    # threshold here.
                    #
                    # This replaces a `canonical_preserves_identity` gate that
                    # compared the SAP input against ROR's official name and
                    # kept the input whenever a distinctive token appeared to
                    # be dropped. It suppressed exactly the writes that matter:
                    # "Mayo Clinic FLA" vs "Mayo Clinic in Florida" reads as a
                    # dropped "fla" token, so the record shipped a verified
                    # ror.org/03zzw1w08 next to the abbreviated input form.
                    # The guard is still the right tool for the LLM
                    # canonicalisation paths (company_canonical, the Tier 3
                    # suggestion path) — an LLM can substitute a different
                    # company outright — but a registry match is verified, not
                    # suggested.
                    #
                    # Parent substitution is not a risk on this path: the match
                    # is scored directly against Name 1, and the local rescore
                    # requires every distinctive/identifier token of Name 1 to
                    # be covered before a candidate can reach the threshold, so
                    # a parent that drops the child's distinguishing tokens
                    # cannot match. Local child matching writes name2/3/4 only,
                    # never name1.
                    _write_registry_name(
                        result, "name1",
                        ror_parent.get("official_name"),
                        registry="ROR",
                        identifier=ror_parent["ror_id"],
                        rule_id="tier1-ror:parent-match",
                    )
                    # Fix D — what ROR says this organisation is called, and
                    # where it is registered. Recorded, not acted on; the gate
                    # at the end of finalise compares it with GLEIF's answer.
                    record_registry_identity(
                        result, "ROR", ror_parent,
                        name=ror_parent.get("official_name"),
                    )

                    # Carry the ROR acronym (when present) for the
                    # search_term_1 derivation in finalise().
                    ror_acronym = ror_parent.get("acronym")
                    if ror_acronym and ror_acronym.strip():
                        result["_ror_acronym"] = ror_acronym.strip()

                    _write(
                        result, "ror_id", ror_parent["ror_id"],
                        registry_evidence(
                            "ror", ror_parent["ror_id"],
                            score=ror_parent.get("score"),
                            rule_id="tier1-ror:parent-match",
                        ),
                    )
                    result["tier_used"] = 1
                    result["source"] = "ROR"
                    result["confidence"] = "high"

                    result["_ror_is_research"] = bool(
                        ror_parent.get("is_research_institution")
                    )
                    result["routing_type"] = (
                        "research_institution"
                        if ror_parent.get("is_research_institution")
                        else "company"
                    )
                    # Path A: ROR's links[] website is authoritative — the
                    # match already passed ROR's country guard, so the
                    # ownership guard passes on registry provenance. It is
                    # still canonicalised: ROR often stores a deep link
                    # (http://www.uni-stuttgart.de/home/index.en.html).
                    institution_domain = _apply_domain(
                        result,
                        ror_parent.get("website"),
                        registry="ROR",
                        settings=self._settings,
                    ).domain

                    # ── Tier 1 LEI: company counterpart to ROR ───────
                    # ROR classified this record as a company. Resolve
                    # the official legal name + LEI from GLEIF and let it
                    # win for name1 (LEI overwrites name1 on a company
                    # match). ROR's domain/website are preserved. A
                    # research institution never reaches this branch, so
                    # ROR's institution result is never touched.
                    if result["routing_type"] == "company":
                        await self._run_lei_lookup(
                            record, result, name1_cleaned, country_code,
                        )

                    # Mark Tier 1 success and UC 2/3 triggered.
                    result["enrichment_status"] = "enriched"
                    if 2 not in result["use_cases_triggered"]:
                        result["use_cases_triggered"].append(2)
                    if 3 not in result["use_cases_triggered"]:
                        result["use_cases_triggered"].append(3)

                    # If there's no name2 and no contact, Tier 1 is
                    # the final answer — return immediately rather
                    # than letting Tier 3 LLM overwrite the canonical
                    # ROR name with a fabricated variant.
                    if not (pp_name2 and pp_name2.strip()) and not (
                        pp_contact and pp_contact.strip()
                    ):
                        return await self._finalise_and_return(
                            result, start, record, cache,
                        )

                    # ── Child match across every department slot ─────
                    children = ror_parent.get("children", [])
                    for field_key, field_val in zip(
                        DEPT_SLOTS, (pp_name2, pp_name3, pp_name4, pp_name5),
                    ):
                        if not (field_val and field_val.strip()):
                            continue
                        val_for_match = (
                            expand_abbreviations(field_val.strip())
                            or field_val.strip()
                        )
                        child_match = _match_child_locally(
                            val_for_match, children,
                        )
                        logger.info({
                            "record_id": record.record_id,
                            "step": f"tier1_child_local_match_{field_key}",
                            field_key: field_val,
                            "num_children": len(children),
                            "best_child": child_match.get("name") if child_match else None,
                            "best_score": child_match.get("score") if child_match else 0,
                        })
                        if child_match:
                            # A matched child name comes straight from the ROR
                            # record, so it is registry-owned like name1.
                            _write_registry_name(
                                result, field_key,
                                child_match.get("name"),
                                registry="ROR",
                            )

                else:
                    # ── ROR miss ─────────────────────────────────────
                    # If the name LOOKS like a research institution
                    # (University / College / Hospital / Medical School
                    # / School of X, etc.) we do NOT call the company
                    # canonical LLM, which would return a legal entity
                    # name like 'President and Fellows of Harvard
                    # College' for 'Harvard Medical School'. Pass
                    # through the original and flag for review instead.
                    looks_research = looks_like_research_institution(name1_cleaned)

                    logger.info({
                        "record_id": record.record_id,
                        "step": "tier1_ror_miss",
                        "name1_cleaned": name1_cleaned,
                        "looks_research": looks_research,
                    })

                    if looks_research:
                        # ── Wikidata crosswalk lane ──────────────────
                        # ROR missed and GLEIF never runs on the research
                        # branch, so this record carries no registry
                        # identifier: the lane's precondition holds. It
                        # returns True only when a registry pointer
                        # resolved and ROR/GLEIF wrote the identity, in
                        # which case the passthrough below must not
                        # overwrite it. Config-gated — with
                        # WIKIDATA_ENABLED off this is a no-op and the
                        # branch behaves exactly as it did before.
                        wd_registry_hit = await self._wikidata_crosswalk(
                            record, result, name1_cleaned, country_code,
                        )
                        if wd_registry_hit:
                            # The registry authored the name and the id, and
                            # `_apply_domain` attached its website. Keep the
                            # branch's own type verdict where the registry did
                            # not supply one (the GLEIF path, mirroring
                            # `_run_lei_lookup`, deliberately does not).
                            if result.get("routing_type") in (None, "unknown"):
                                result["routing_type"] = "research_institution"
                        else:
                            # Nothing identified this organisation, so the SAP
                            # input stands. The input IS the producer —
                            # recording that is what separates a retained value
                            # from a confirmed one.
                            _write(
                                result, "name1_enriched", name1_cleaned,
                                deterministic_evidence(
                                    "tier1-ror-miss:research-passthrough",
                                    producer="input", tier=1,
                                    evidence_ref={
                                        "queried": name1_cleaned,
                                        "registry": "ROR",
                                        "matched": False,
                                    },
                                ),
                            )
                            result["source"] = "passthrough"
                            result["confidence"] = "low"
                            result["tier_used"] = 1
                            result["routing_type"] = "research_institution"
                            result["enrichment_status"] = "unresolved"
                            # No `_ev_low_conf_unchanged` marker here since
                            # Fix 2. Whether a retained Name 1 is flagged is
                            # decided once, in finalise, from the evidence the
                            # finished record holds — see
                            # enrichment/unchanged_state.py. Marking it at the
                            # branch is what made the outcome depend on which
                            # branch reached the passthrough.
                        # Short-circuit if no name2/contact to process.
                        if not (pp_name2 and pp_name2.strip()) and not (
                            pp_contact and pp_contact.strip()
                        ):
                            return await self._finalise_and_return(
                                result, start, record, cache,
                            )
                        # Otherwise fall through to Tier 2/3 for name2.
                        company_res = None
                    else:
                        # ── Tier 1 LEI (deterministic) BEFORE the LLM ──
                        # The company branch's deterministic step: resolve
                        # the official legal name + LEI from GLEIF. On a
                        # verified match this becomes the canonical name1;
                        # only on a miss/error do we fall back to the LLM
                        # company-canonical path (unchanged).
                        lei_matched = await self._run_lei_lookup(
                            record, result, name1_cleaned, country_code,
                        )
                        # ── Wikidata crosswalk lane ──────────────────
                        # ROR missed AND GLEIF missed, so the record holds
                        # no registry identifier. This is the position the
                        # lane occupies: after the registry miss, before
                        # the web-evidence step and before the LLM. A
                        # registry pointer that resolves here is handled
                        # exactly as `lei_matched` is — the LLM
                        # canonicalisation is skipped, because a verified
                        # registry name must not be re-litigated by a
                        # model. Config-gated; a no-op when off.
                        wd_registry_hit = (
                            False if lei_matched
                            else await self._wikidata_crosswalk(
                                record, result, name1_cleaned, country_code,
                            )
                        )
                        if lei_matched or wd_registry_hit:
                            company_res = None
                            # If no name2, Tier 1 LEI is the final answer —
                            # return now so Tier 3 can't overwrite it.
                            if not (pp_name2 and pp_name2.strip()):
                                return await self._finalise_and_return(
                                    result, start, record, cache,
                                )
                        else:
                            company_res = await run_company_canonical(
                                record_id=record.record_id,
                                name1=name1_cleaned,
                                city=record.city,
                                state=record.state,
                                country=record.country,
                                llm_client=self._llm_client,
                                # Full street address: lets the LLM recognise a
                                # well-known HQ and correct a garbled name the
                                # name alone can't resolve. The spelling-variant
                                # gate below still blocks an address-driven swap
                                # to a different co-tenant company.
                                street=pp_street1,
                                postal_code=record.postal_code,
                            )
                            # Fix 2: the model's independent answer, whatever
                            # the gates below then do with it. Read in finalise
                            # to decide `unchanged-confirmed` — the model was
                            # asked what the organisation is called and was
                            # never shown the record's answer as a candidate,
                            # so a proposal that reproduces it is evidence
                            # rather than assent. Transient; popped in finalise.
                            result["_canonical_proposal"] = (
                                company_res.returned_name
                            )
                            # ── Registry re-verify of a typo'd company name ──
                            # GLEIF's name search is not typo-tolerant, so a
                            # misspelling ("Bayr AG") misses on the raw name
                            # above. The company-canonical LLM proposes the
                            # correction ("Bayer AG") but the identity guard
                            # blocks it (a typo is not a prefix/acronym of the
                            # original). When that proposal is a genuine
                            # spelling VARIANT (not an entity swap), re-query
                            # GLEIF on it: a confirmed ACTIVE entity in the
                            # right country proves the correction real and
                            # attaches the LEI. The spelling-variant gate is
                            # what keeps this from laundering a hallucination.
                            if (
                                not company_res.success
                                and company_res.proposed_name
                                and canonical_is_spelling_variant(
                                    name1_cleaned, company_res.proposed_name,
                                )
                            ):
                                confirmed = await self._run_lei_lookup(
                                    record, result,
                                    company_res.proposed_name, country_code,
                                )
                                if confirmed:
                                    logger.info({
                                        "record_id": record.record_id,
                                        "step": "tier1_lei_typo_recovered",
                                        "input_name": name1_cleaned,
                                        "llm_proposed": company_res.proposed_name,
                                        "legal_name": result.get("name1_enriched"),
                                        "lei_id": result.get("lei_id"),
                                    })
                                    company_res = None
                                    if not (pp_name2 and pp_name2.strip()):
                                        return await self._finalise_and_return(
                                            result, start, record, cache,
                                        )

                    # Fix 2 — the model agreed with the record. Its answer and
                    # the record's differ only by punctuation or case under
                    # `normalize_key`, so there is no corrected name to write:
                    # rewriting would ship the model's comma instead of the
                    # record's, which is a change to the value with no claim
                    # behind it. The record's own string stands and finalise
                    # labels it `input:provisional+llm`. The same equality Stage 5
                    # uses to decide a "correction" is not one.
                    canonical_confirms_input = bool(
                        company_res
                        and company_res.success
                        and company_res.name1_enriched
                        and normalize_key(company_res.name1_enriched)
                        == normalize_key(name1_cleaned)
                    )
                    if canonical_confirms_input:
                        _write(
                            result, "name1_enriched", name1_cleaned,
                            deterministic_evidence(
                                "tier2:company-canonical-confirms-input",
                                producer="input", tier=1,
                                evidence_ref={
                                    "queried": name1_cleaned,
                                    "proposal": company_res.name1_enriched,
                                },
                            ),
                        )
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["tier_used"] = 1
                        result["routing_type"] = "company"
                        # Provisional: finalise settles it once the unchanged
                        # state is known (`unchanged_state.enrichment_status_for`),
                        # and Tier 3 overrides it if this record falls through.
                        # Set here so no path can return with the "failed"
                        # default standing, which would ship a DATAshaper
                        # "Error — investigate" severity on a confirmed record.
                        result["enrichment_status"] = "unresolved"
                        logger.info({
                            "record_id": record.record_id,
                            "step": "company_canonical_confirms_input",
                            "input": name1_cleaned,
                            "proposal": company_res.name1_enriched,
                        })
                        if not (pp_name2 and pp_name2.strip()):
                            return await self._finalise_and_return(
                                result, start, record, cache,
                            )
                    elif company_res and company_res.success and company_res.name1_enriched:
                        _write(
                            result, "name1_enriched",
                            company_res.name1_enriched,
                            llm_evidence(
                                ("llm_company_canonical",),
                                tier=2,
                                prompt_version=COMPANY_CANONICAL_PROMPT_VERSION,
                                deployment=self._settings.openai_model,
                                self_reported=company_res.confidence,
                                rule_id="tier2:company-canonical",
                                extra={"input_name": name1_cleaned},
                            ),
                        )
                        result["source"] = "llm_canonical"
                        result["confidence"] = "high"
                        # Routing only — company canonicalisation no longer
                        # decides the output type.
                        result["routing_type"] = "company"
                        result["tier_used"] = 2
                        if 2 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(2)
                        if 3 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(3)
                        result["enrichment_status"] = "enriched"
                        # No flag — see the note in
                        # _return_canonical_short_circuit.

                        # If no name2, we're done — return immediately
                        # so Tier 3 doesn't overwrite the canonical name.
                        if not (pp_name2 and pp_name2.strip()):
                            return await self._finalise_and_return(
                                result, start, record, cache,
                            )
                    elif company_res is not None:
                        # Company canonical was attempted but failed.
                        _write(
                            result, "name1_enriched", name1_cleaned,
                            deterministic_evidence(
                                "tier2:company-canonical-failed-passthrough",
                                producer="input", tier=1,
                                evidence_ref={"queried": name1_cleaned},
                            ),
                        )
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["tier_used"] = 1
                        result["routing_type"] = "unknown"
                    # else: research-institution passthrough already set above

                    # The ROR-miss default. A registry step INSIDE this block
                    # may nonetheless have attached a domain — the Wikidata
                    # crosswalk following a `P6782` pointer writes ROR's
                    # website through `_apply_domain` — and discarding it here
                    # would leave the department paths without the base they
                    # need on exactly the records the crosswalk just resolved.
                    # `None` when nothing wrote one, which is every path that
                    # existed before the crosswalk: the LEI step never writes a
                    # domain (GLEIF has no website field) and the website
                    # resolver has not run yet.
                    institution_domain = result.get("domain") or None

            name2_already_filled = any(
                (getattr(pre, slot, None) or "").strip() for slot in DEPT_SLOTS
            )
            has_dept_signal = result["_has_dept_signal"]
            multi_contact = result["_multi_contact"]

            # ── AP short-circuit: handled entirely by preprocessing ──
            # Check every name slot for AP normalisation. If any AP field
            # is present, return immediately.
            any_ap = any(
                (getattr(pre, f) or "").strip().lower() == "accounts payable"
                for f in NAME_SLOTS
            )
            if any_ap:
                for f, pp_val in zip(
                    DEPT_SLOTS, (pp_name2, pp_name3, pp_name4, pp_name5),
                ):
                    if pp_val and pp_val.strip():
                        _write(
                            result, f"{f}_enriched", pp_val.strip(),
                            deterministic_evidence(
                                "uc6:accounts-payable-normalised",
                                producer="preprocess", tier=2,
                            ),
                        )
                result["tier_used"] = 2
                result["source"] = "pattern_match"
                result["confidence"] = "high"
                result["enrichment_status"] = "enriched"
                if 6 not in result["use_cases_triggered"]:
                    result["use_cases_triggered"].append(6)
                return await self._finalise_and_return(result, start, record, cache)

            # ── UC 13 / Rule A-15: Lab/group/centre → parent dept ─────
            # When the input Name2 names a granular research unit (lab,
            # research group, centre, core, facility, unit, program),
            # look up the parent academic department on the
            # institution's site. If found: the parent department
            # becomes Name2 and the original lab name shifts to Name3
            # (when Name3 is empty).
            #
            # Skip when ROR child match already resolved Name2 to a
            # non-granular (department-level) name — Tier 1's answer
            # is authoritative and we must not overwrite it.
            ror_child_enriched_name2 = result.get("name2_enriched")
            ror_child_resolved = bool(
                ror_child_enriched_name2
                and ror_child_enriched_name2.strip().lower()
                    != (pp_name2 or "").strip().lower()
                and not is_granular_unit(ror_child_enriched_name2)
            )
            can_lab_resolve = (
                result["routing_type"] == "research_institution"
                and bool(pp_name2 and pp_name2.strip())
                and is_granular_unit(pp_name2)
                and not ror_child_resolved
            )
            if can_lab_resolve:
                lab_res = await run_lab_resolver(
                    record_id=record.record_id,
                    institution=result["name1_enriched"] or pp_name1 or "",
                    lab_name=pp_name2,  # type: ignore[arg-type]
                    domain=institution_domain,
                    search_client=self._search_client,
                    page_fetcher=self._page_fetcher,
                    llm_client=self._llm_client,
                    cache=cache,
                    settings=self._settings,
                )
                logger.info({
                    "record_id": record.record_id,
                    "step": "uc13_lab_resolver_result",
                    "lab": pp_name2,
                    "parent": lab_res.parent_department,
                    "confidence": lab_res.confidence,
                    "url": lab_res.source_url,
                })
                if lab_res.success and lab_res.parent_department:
                    # Demote the original lab name into the first free slot
                    # below Name 2. Name 3 is the natural landing spot, but
                    # when it is occupied the slots below it will do just as
                    # well — the parent/child order is preserved either way.
                    # Only a name block with no free slot at all loses the
                    # lab name, and that is what the flag reports.
                    pp_dept_values = {
                        slot: getattr(pre, slot, None) for slot in DEPT_SLOTS
                    }
                    demote_target = next(
                        (
                            slot for slot in DEPT_SLOTS[1:]
                            if not (pp_dept_values.get(slot) or "").strip()
                        ),
                        None,
                    )
                    name3_already_set = demote_target is None
                    _write(
                        result, "name2_enriched", lab_res.parent_department,
                        llm_evidence(
                            ("serp", "fetch", "llm_lab_parent"),
                            tier=2,
                            prompt_version=LAB_PARENT_PROMPT_VERSION,
                            deployment=self._settings.openai_model,
                            self_reported=lab_res.confidence,
                            source_url=lab_res.source_url,
                            rule_id="uc13:parent-department-from-lab-page",
                            extra={"lab": pp_name2},
                        ),
                    )
                    if demote_target is not None:
                        result[f"{demote_target}_enriched"] = pp_name2.strip()
                    result["tier_used"] = 2
                    result["source"] = "dept_search"
                    result["source_url"] = lab_res.source_url
                    result["confidence"] = lab_res.confidence
                    result["enrichment_status"] = "enriched"
                    # The parent department was INFERRED from the lab's own
                    # page rather than read from a stated department, so it
                    # is a claim a reviewer has to check — unlike a stated
                    # department read off an on-domain page, which carries a
                    # source_url and is not flagged.
                    result["_ev_dept_via_lab"] = True
                    if name3_already_set:
                        # Every slot below Name 2 is occupied, so the lab name
                        # could not be demoted anywhere and the name block no
                        # longer describes a clean parent/child split.
                        result["_ev_name3_not_demoted"] = True
                    elif demote_target != "name3":
                        # Demoted, but past Name 3 — record where it landed so
                        # the flag scope points a reviewer at the right column.
                        result["_ev_demoted_to"] = demote_target
                    if 13 not in result["use_cases_triggered"]:
                        result["use_cases_triggered"].append(13)
                    return await self._finalise_and_return(result, start, record, cache)
                # Granular Name2 detected but no parent could be resolved
                # (no candidates, or LLM said null). No flag is raised here:
                # the record falls through to tier 2 canonical / 2A / 2B / 3,
                # any of which may settle Name 2, and finalisation flags
                # whatever state it actually ends in.
                if 13 not in result["use_cases_triggered"]:
                    result["use_cases_triggered"].append(13)

            # ── Contact-lookup eligibility (computed early) ───────────
            # This must be known BEFORE the canonical short-circuit
            # below, because that short-circuit returns for every record
            # with a populated Name 2 — which is exactly the population
            # Tier 2A verification mode needs to see. Computed once here
            # and consumed both by the short-circuit and by the Tier 2A
            # gate further down.
            can_do_contact_lookup = (
                result["routing_type"] == "research_institution"
                and bool(pp_contact and pp_contact.strip())
                and not multi_contact
                and bool(institution_domain)
            )

            # ── TIER 2 (canonical — UC 5): LLM canonicalization ──────
            # Runs the same logic for EVERY department slot: child match
            # first (already done above), then Tier 2 canonical LLM,
            # then UC 5 scope filter. Zero SerpAPI calls.
            # Uses a shared helper to avoid duplication.
            can_canonical = (
                result["routing_type"] in ("research_institution", "company")
                and result.get("name1_enriched")
            )
            any_canonical_ran = False
            for field_key, pp_val in zip(
                DEPT_SLOTS, (pp_name2, pp_name3, pp_name4, pp_name5),
            ):
                if not (pp_val and pp_val.strip()):
                    continue
                # Already resolved by child match above?
                if result.get(f"{field_key}_enriched"):
                    continue
                if not can_canonical:
                    continue

                # UC 11: a name containing a DBA marker is the user's
                # declared canonical form — skip LLM canonicalisation,
                # which would strip the marker. finalise() also restores
                # the preprocessed value as a safety net.
                if field_key in (result.get("_dba_values") or {}):
                    _write(
                        result, f"{field_key}_enriched", pp_val,
                        deterministic_evidence(
                            "uc11:dba-marker-skips-canonicalisation",
                            producer="preprocess", tier=2,
                        ),
                    )
                    continue

                canonical = await run_tier2_canonical(
                    record_id=record.record_id,
                    institution=result["name1_enriched"],
                    name2=pp_val,  # type: ignore[arg-type]
                    llm_client=self._llm_client,
                )
                logger.info({
                    "record_id": record.record_id,
                    "step": f"tier2_canonical_result_{field_key}",
                    "found": canonical.success,
                    "confidence": canonical.confidence,
                    "enriched": canonical.name2_enriched,
                })
                any_canonical_ran = True

                if canonical.success and canonical.name2_enriched:
                    # UC 5 scope filter: reject granular units.
                    if is_granular_unit(canonical.name2_enriched):
                        logger.info({
                            "record_id": record.record_id,
                            "step": f"tier2_canonical_rejected_scope_{field_key}",
                            "rejected": canonical.name2_enriched,
                        })
                        _write(
                            result, f"{field_key}_enriched", pp_val,
                            deterministic_evidence(
                                "uc5:granular-unit-rejected-passthrough",
                                producer="input", tier=2,
                                evidence_ref={
                                    "rejected": canonical.name2_enriched,
                                },
                            ),
                        )
                    else:
                        _write(
                            result, f"{field_key}_enriched",
                            canonical.name2_enriched,
                            llm_evidence(
                                ("llm_canonical",),
                                tier=2,
                                prompt_version=TIER2_CANONICAL_PROMPT_VERSION,
                                deployment=self._settings.openai_model,
                                self_reported=canonical.confidence,
                                rule_id="uc5:tier2-canonical",
                                extra={"input_value": pp_val},
                            ),
                        )
                        if 5 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(5)
                else:
                    # LLM not confident — passthrough original
                    _write(
                        result, f"{field_key}_enriched", pp_val,
                        deterministic_evidence(
                            "tier2-canonical:below-threshold-passthrough",
                            producer="input", tier=2,
                        ),
                    )

            # If canonical ran on any field, the record is finished HERE
            # unless Tier 2A can still verify Name 2 against the contact's
            # own page. Deferring is safe: every path out of the Tier 2A
            # block below either returns with a 2A result or falls back to
            # this same short-circuit, so a record that would have stopped
            # here never reaches Tier 3.
            canonical_short_circuit = any_canonical_ran and name2_already_filled
            if canonical_short_circuit and not can_do_contact_lookup:
                return await self._return_canonical_short_circuit(
                    result, start, record, cache,
                )

            # ── TIER 2A (contact lookup): 1 SerpAPI call, gated ────────
            # Runs for a research institution with a single contact and an
            # official domain, in either mode: population when Name 2 is
            # blank, verification when it is populated (the contact's page
            # is the authority on which unit they actually sit in).
            # A single quoted-name on-domain query. SERP candidates are
            # deterministically filtered so only results whose
            # URL/title/snippet contain BOTH the first name and the
            # surname of the contact reach the page fetch / LLM step.
            # That blocks near-miss matches like 'Sarah Knox' for a
            # 'Sarah Chen' query.
            logger.info({
                "record_id": record.record_id,
                "step": "tier_contact_decision",
                "attempting": can_do_contact_lookup,
                "name2_already_filled": name2_already_filled,
            })

            if can_do_contact_lookup:
                tier2a_result = await run_tier2a(
                    record_id=record.record_id,
                    contact=pp_contact,  # type: ignore[arg-type]
                    institution=result["name1_enriched"] or pp_name1 or "",
                    domain=institution_domain,
                    name2=pp_name2,
                    name3=pp_name3,
                    search_client=self._search_client,
                    page_fetcher=self._page_fetcher,
                    llm_client=self._llm_client,
                    cache=cache,
                    settings=self._settings,
                    extra_departments=(pp_name4, pp_name5),
                )

                logger.info({
                    "record_id": record.record_id,
                    "step": "tier_contact_result",
                    "found": tier2a_result.success,
                    "mode": tier2a_result.mode,
                    "confidence": tier2a_result.confidence,
                    "name2_enriched": tier2a_result.name2_enriched,
                })

                if tier2a_result.success:
                    # UC 4 scope: reject lab/group/centre/facility
                    # canonicalisations. If the raw Tier 2A answer is
                    # itself granular, skip the upgrade and the result.
                    if (
                        tier2a_result.name2_enriched
                        and is_granular_unit(tier2a_result.name2_enriched)
                    ):
                        logger.info({
                            "record_id": record.record_id,
                            "step": "tier_contact_rejected_scope",
                            "reason": "lab/group/centre/facility out of scope",
                            "rejected": tier2a_result.name2_enriched,
                        })
                    else:
                        # Canonicalise bare-subject answers like
                        # "Anesthesia" → full institutional name.
                        if tier2a_result.name2_enriched:
                            canon = await run_tier2_canonical(
                                record_id=record.record_id,
                                institution=result["name1_enriched"] or pp_name1 or "",
                                name2=tier2a_result.name2_enriched,
                                llm_client=self._llm_client,
                            )
                            if canon.success and canon.name2_enriched:
                                if is_granular_unit(canon.name2_enriched):
                                    logger.info({
                                        "record_id": record.record_id,
                                        "step": "tier_contact_canonical_rejected_scope",
                                        "rejected": canon.name2_enriched,
                                    })
                                else:
                                    logger.info({
                                        "record_id": record.record_id,
                                        "step": "tier_contact_canonicalised",
                                        "before": tier2a_result.name2_enriched,
                                        "after": canon.name2_enriched,
                                    })
                                    tier2a_result.name2_enriched = canon.name2_enriched
                        _apply_tier2a(
                            result, tier2a_result, tier2a_result.mode,
                            self._settings.openai_model,
                        )
                        if 4 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(4)
                        return await self._finalise_and_return(
                            result, start, record, cache,
                        )

            # Tier 2A was eligible but produced nothing usable (no
            # candidate page, person not found, or a granular answer
            # rejected by the UC 4 scope filter). A record that would
            # have stopped at the canonical short-circuit stops here
            # instead — it must not fall through to Tier 3, which would
            # overwrite the canonical Name 2 with a fabricated one.
            if canonical_short_circuit:
                return await self._return_canonical_short_circuit(
                    result, start, record, cache,
                )

            # ── TIER 3: LLM INFERENCE ────────────────────────────────────

            logger.info({
                "record_id": record.record_id,
                "step": "tier3_start",
            })

            tier3_result: Tier3Result = await run_tier3(
                record_id=record.record_id,
                name1=pp_name1,
                name2=pp_name2,
                name3=pp_name3,
                name4=pp_name4,
                name5=pp_name5,
                contact=pp_contact,
                street=pp_street1 or record.street,
                city=record.city,
                state=record.state,
                zip_code=record.zip,
                country=record.country,
                llm_client=self._llm_client,
            )

            _apply_tier3(result, tier3_result, self._settings.openai_model)

            # Last resort: if nothing enriched name1, pass through the preprocessed original
            if not result.get("name1_enriched") and not is_blank(pp_name1):
                _write(
                    result, "name1_enriched", (pp_name1 or "").strip(),
                    deterministic_evidence(
                        "last-resort:preprocessed-input-retained",
                        producer="preprocess", tier=3,
                    ),
                )

            # Rule: if the input had no department and no contact, there is
            # no signal from which to infer one — EVERY department slot must
            # remain empty regardless of what any tier produced. The rule is
            # about the record carrying no department signal at all, so it
            # cannot stop at Name 2.
            if not has_dept_signal:
                for _slot in DEPT_SLOTS:
                    if result.get(f"{_slot}_enriched") is None:
                        continue
                    _write(
                        result, f"{_slot}_enriched", None,
                        deterministic_evidence(
                            "no-dept-signal:slot-cleared",
                            producer="pipeline", tier=3,
                            evidence_ref={
                                "dropped": result.get(f"{_slot}_enriched"),
                            },
                        ),
                    )

            # Rule: if preprocessing stripped a department slot down to empty
            # (because it was actually a contact / email / address /
            # AP reference), do not let Tier 3 fabricate a replacement.
            pp_dept_values = {
                "name2": pp_name2, "name3": pp_name3,
                "name4": pp_name4, "name5": pp_name5,
            }
            for _slot in DEPT_SLOTS:
                _original = getattr(record, _slot, None)
                _preprocessed = pp_dept_values.get(_slot)
                if (
                    _original and _original.strip()
                    and not (_preprocessed and _preprocessed.strip())
                ):
                    logger.info({
                        "record_id": record.record_id,
                        "step": f"{_slot}_cleared_by_preprocess",
                        "original": _original,
                    })
                    if result.get(f"{_slot}_enriched") is not None:
                        _write(
                            result, f"{_slot}_enriched", None,
                            deterministic_evidence(
                                "preprocess-cleared:no-tier3-replacement",
                                producer="preprocess", tier=3,
                                evidence_ref={
                                    "input": _original,
                                    "dropped": result.get(
                                        f"{_slot}_enriched",
                                    ),
                                },
                            ),
                        )

            # Rule: no department slot should echo name1_enriched. A unit
            # value equal to the institution names nothing new, at whichever
            # slot it landed.
            _n1 = result.get("name1_enriched")
            if _n1:
                for _slot in DEPT_SLOTS:
                    _val = result.get(f"{_slot}_enriched")
                    if (
                        _val
                        and _val.strip().lower() == _n1.strip().lower()
                    ):
                        logger.info({
                            "record_id": record.record_id,
                            "step": f"{_slot}_equals_name1_dropped",
                            "value": _val,
                        })
                        _write(
                            result, f"{_slot}_enriched", None,
                            deterministic_evidence(
                                "dept-slot-echoes-name1:dropped",
                                producer="pipeline", tier=3,
                                evidence_ref={"dropped": _val},
                            ),
                        )

            return await self._finalise_and_return(result, start, record, cache)

        except Exception as exc:
            logger.error({
                "record_id": record.record_id,
                "step": "orchestrator_error",
                "error": str(exc),
            })
            result["enrichment_status"] = "failed"
            result["error"] = str(exc)
            return await self._finalise_and_return(
                result, start, record, cache,
            )

    @staticmethod
    def _build_summary(results: list[EnrichmentResult], batch_ms: int) -> EnrichmentSummary:
        """Aggregate individual results into batch-level summary statistics."""
        summary = EnrichmentSummary(
            total=len(results),
            processing_time_ms=batch_ms,
        )
        for r in results:
            if r.enrichment_status == "enriched":
                summary.enriched += 1
            elif r.enrichment_status == "verified":
                summary.verified += 1
            elif r.enrichment_status == "unresolved":
                summary.unresolved += 1
            else:
                summary.failed += 1

            if r.record_type == "research_institution":
                summary.research_institution_count += 1
            elif r.record_type == "company":
                summary.company_count += 1

            if r.tier_used == 1:
                summary.tier1_resolved += 1
            elif r.tier_used == 2:
                if r.tier2_mode == "2A_population":
                    summary.tier2a_population_count += 1
                elif r.tier2_mode == "2A_verification":
                    summary.tier2a_verification_count += 1
                elif r.tier2_mode == "2B":
                    summary.tier2b_count += 1
            elif r.tier_used == 3:
                summary.tier3_count += 1

            if r.contact_used:
                summary.contact_lookup_attempted += 1
                if r.enrichment_status in ("enriched", "verified"):
                    summary.contact_lookup_success += 1

            # Domain ownership guard telemetry. "serp" covers every
            # web-derived domain — name similarity and on-domain evidence
            # alike — so the three counters partition the kept domains.
            if r.domain_verified_by == "registry":
                summary.domain_from_registry += 1
            elif (r.domain_verified_by or "").startswith("witness_"):
                summary.domain_from_witness += 1
            elif r.domain_verified_by == "email":
                summary.domain_from_email += 1
            elif r.domain_verified_by == "page":
                summary.domain_from_page += 1
            elif r.domain_verified_by is not None:
                summary.domain_from_serp += 1
            if r.domain_rejected:
                summary.domain_rejected_unverified += 1

            # Fix 2 — the three unchanged-Name-1 states.
            if r.unchanged_name1_state == UNCHANGED_VERIFIED:
                summary.unchanged_verified += 1
            elif r.unchanged_name1_state == UNCHANGED_CONFIRMED:
                summary.unchanged_confirmed += 1
            elif r.unchanged_name1_state == UNCHANGED_UNRESOLVED:
                summary.unchanged_unresolved += 1

        return summary
