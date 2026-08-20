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
import logging
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
from enrichment.overflow_check import run_overflow_check
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
from enrichment.tier1_lei import LEIClient, clear_lei_cache, lei_normalised_hits
from enrichment.tier1_ror import RORClient, clear_ror_cache, ror_normalised_hits
from enrichment.tier2_canonical import run_tier2_canonical
from enrichment.tier2a_contact import Tier2AResult, run_tier2a
from enrichment.tier3_llm import Tier3Result, run_tier3
from enrichment.website_resolver import (
    infer_website_via_llm,
    resolve_website_via_serp,
)
from llm.openai_client import OpenAIClient, install_httpx_aclose_noise_filter
from search.base import SearchClient
from search.duckduckgo_client import DuckDuckGoClient
from search.page_fetcher import PageFetcher
from search.serpapi_client import SerpAPIClient
from utils.cache import BatchCache, SerpCache
from utils.domain_resolver import (
    DomainDecision,
    DomainEvidence,
    canonicalise_host,
    resolve_domain,
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
)

logger = logging.getLogger(__name__)


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

def _init_result(record: EnrichmentRecord) -> dict[str, Any]:
    """Create a blank result dict with originals populated."""
    # Map the legacy single 'street' input into street1 if street1 is blank.
    street1_original = record.street1 or record.street
    return {
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
        "name1_original": record.name1,
        "name2_original": record.name2,
        "name3_original": record.name3,
        "name4_original": record.name4,
        "name1_enriched": None,
        "name2_enriched": None,
        "name3_enriched": None,
        "name4_enriched": None,
        "name1_changed": False,
        "name2_changed": False,
        "name3_changed": False,
        "name4_changed": False,
        "search_term_1": None,
        "search_term_2": None,
        # Original SAP Search Term 1 — used only as a last-resort fallback
        # so the derived search_term_1 is never empty. Stripped in finalise().
        "_search_term_1_original": record.search_term_1,
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
    }


def _write_registry_name(
    result: dict[str, Any],
    field: str,
    value: str | None,
    registry: str,
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
    result[f"{field}_enriched"] = value.strip()
    result.setdefault("_registry_name_fields", set()).add(field)
    logger.info({
        "record_id": result.get("record_id"),
        "step": "registry_name_write",
        "field": field,
        "registry": registry,
        "value": value.strip(),
    })


# ── Output normalisation — one function, every exit path ──────────────────────

# Name fields. A short upper-case token defaults to an acronym here ("HCA",
# "UCI"), which is the long-standing behaviour of `smart_title_case`.
_CASE_NAME_FIELDS = (
    "name1_enriched", "name2_enriched", "name3_enriched", "name4_enriched",
)
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
            result[field] = normalise_case(str(val), mode="name")
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


def finalise(result: dict[str, Any], start: float) -> dict[str, Any]:
    """Apply empty-string guards and compute changed flags.

    FIX(Bug 5): enriched name fields must NEVER be empty string "".
    They must be a non-empty string or None.

    FIX(Bug 8): changed flags are True only when enriched is not None
    AND enriched differs from original.
    """
    for field in ("name1_enriched", "name2_enriched", "name3_enriched",
                  "name4_enriched"):
        val = result.get(field)
        if val is not None and not str(val).strip():
            result[field] = None

    # Item 6c: a Name 2 that was blank in the input and was populated ONLY by
    # Tier 3 (LLM inference) is a guess. Require high confidence, otherwise
    # return null rather than emit a fabricated department (e.g. "St. Louis
    # Site" invented from nothing).
    if (
        result.get("tier_used") == 3
        and result.get("_name2_from_tier3")
        and result.get("name2_enriched")
        and not (result.get("name2_original") and str(result.get("name2_original")).strip())
        and str(result.get("confidence") or "").lower() != "high"
    ):
        logger.info(
            "[%s] Tier 3 name2 guess dropped (input blank, confidence=%s): %r",
            result.get("record_id"), result.get("confidence"),
            result.get("name2_enriched"),
        )
        # No flag: the input Name 2 was blank and the output Name 2 is
        # blank. Nothing was dropped and nothing is uncertain — the record
        # simply has no department, which is not a defect.
        result["name2_enriched"] = None

    # Normalise Name 1 when it was passed through uncanonicalised (a ROR miss
    # left the raw source value — often ALL-CAPS and abbreviated, e.g. "LARGO
    # MEDICAL CTR", "UNIVERSTIY OF FLORIDA"). Title-case + expand abbreviations
    # so passthrough rows are consistent with ROR-matched ones. ROR / LLM
    # canonical names are never ALL-CAPS, so for those we only run the (no-op
    # on mixed-case) title-case as a safety net and never touch their wording.
    name1_val = result.get("name1_enriched")
    if name1_val:
        if result.get("source") == "passthrough":
            result["name1_enriched"] = clean_passthrough_org_name(name1_val)
        else:
            result["name1_enriched"] = smart_title_case(name1_val) or name1_val

    # Expand organisational abbreviations in the OUTPUT name fields. Before
    # Fix 4 `expand_abbreviations` only ever reached an output name via
    # `clean_passthrough_org_name` (name1, and only when source ==
    # "passthrough") and via `canonicalise_unit_name` (name2-4, and only when
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
    for field in ("name1", "name2", "name3", "name4"):
        if field in registry_named:
            continue
        val = result.get(f"{field}_enriched")
        if val:
            result[f"{field}_enriched"] = expand_abbreviations(val) or val

    # Guarantee the short legal form on the final output regardless of source
    # (input passthrough, ROR, GLEIF, or LLM): "… Aktiengesellschaft" → "… AG",
    # "… Incorporated" → "… Inc". Preprocess (UC 17) already does this on the
    # input; this backstops any long form a downstream tier introduces.
    for field in ("name1_enriched", "name2_enriched", "name3_enriched",
                  "name4_enriched"):
        val = result.get(field)
        if val:
            result[field] = collapse_legal_suffix(val)

    # Canonicalise academic unit names on name2/name3 only. name1
    # (the institution) is never a "Department of X", so we leave
    # it alone. This collapses "Chemistry Department",
    # "Dept of Chemistry", "Department of Chemistry" all to
    # "Department of Chemistry".
    # UC 5 scope: never canonicalise granular units (lab/group/
    # centre/facility) — leave them verbatim.
    for field in ("name2_enriched", "name3_enriched", "name4_enriched"):
        val = result.get(field)
        if val and not is_granular_unit(val):
            canonical = canonicalise_unit_name(val)
            if canonical and canonical != val:
                result[field] = canonical

    # A named building lifted out of a name field (see preprocess) fills the
    # Building output only when the address stage did not already extract one
    # from the street fields.
    pp_building = result.pop("_pp_building", None)
    if pp_building and not result.get("building"):
        result["building"] = pp_building

    preprocess_cleared = result.get("_preprocess_cleared") or set()

    # Passthrough: if no tier enriched name2/name3 but the record had
    # one, retain the original value — UNLESS preprocessing deliberately
    # cleared the field (e.g. extracted an email, address, contact
    # name). Enrichment must never silently drop user-supplied fields
    # but must also respect preprocessing's decision to empty a field.
    for field in ("name2", "name3", "name4"):
        if result.get(f"{field}_enriched") is None and field not in preprocess_cleared:
            orig = result.get(f"{field}_original")
            if orig and str(orig).strip():
                result[f"{field}_enriched"] = str(orig).strip()

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
    # be sitting in a name field: a tier wrote one into name2/name3 AFTER
    # preprocessing's UC 9 ran, or the passthrough above restored an
    # address-bearing original. The address stage handles the common case,
    # but it runs before this passthrough — so re-check the FINAL name
    # values here and pull any embedded street address into the first empty
    # street output slot. name1 (the institution) is never touched. Only
    # rewrite a name field when every fragment finds a slot, so a record
    # with all street slots full never silently drops part of an address.
    _street_out = ("street_cleaned", "street_2_cleaned", "street_3_cleaned",
                   "street_4_cleaned", "street_5_cleaned")
    for _nf in ("name2_enriched", "name3_enriched", "name4_enriched"):
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
                result[_nf] = None
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
            result[_nf] = _cleaned or None

    # UC 11 safety net: if preprocessing rewrote a DBA variant in a name
    # field, the preprocessed value IS the canonical form. Restore it
    # over anything a downstream tier (company_canonical, tier2_canonical,
    # tier3) wrote — those LLMs treat DBA as noise and strip it, but the
    # marker is user intent (legal name vs. trading name).
    dba_values = result.get("_dba_values") or {}
    for base, preprocessed in dba_values.items():
        if preprocessed and result.get(f"{base}_enriched") != preprocessed:
            result[f"{base}_enriched"] = preprocessed

    # Post-tier dedup of the department slots. Preprocess already deduped, but
    # the tiers (especially the Tier 3 LLM) can set name2/name3/name4 to the
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
    for f in ("name2_enriched", "name3_enriched", "name4_enriched"):
        val = result.get(f)
        n = _name_norm(val)
        if not n:
            continue
        if any(n == kn or fuzz.ratio(n, kn) >= 92 for kn in kept_norms):
            continue
        kept_vals.append(val)
        kept_norms.append(n)
    for i, f in enumerate(("name2_enriched", "name3_enriched", "name4_enriched")):
        result[f] = kept_vals[i] if i < len(kept_vals) else None

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

    for f in ("name1", "name2", "name3", "name4", "care_of", "contact",
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

    # THE flag decision, taken once, here. Every name, contact and domain
    # field above has settled and the `*_changed` flags are computed, so the
    # codes describe the state the record ended in rather than the tiers that
    # ran to get there. Ordering against the rest of finalise: after the
    # domain fallback (which is the last thing that can raise
    # `_domain_unverified`) and before `_registry_name_fields` is stripped
    # below, which is what tells compute_flags that a name is registry-owned
    # and so not an unverified inference.
    compute_flags(result)

    # Compact search handles for downstream consumers. Runs after all
    # name / domain fields are settled so the derivation sees final
    # values. department_domain is written directly by the probe in
    # the orchestrator (see _probe_department_url) — it doesn't share
    # source_url with the tiers.
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

    result["duration_ms"] = int((time.monotonic() - start) * 1000)
    # Strip transient non-schema keys before pydantic validation.
    result.pop("_preprocess_cleared", None)
    result.pop("_dba_values", None)
    result.pop("_pp_name1", None)
    result.pop("_ror_acronym", None)
    result.pop("_search_term_1_original", None)
    result.pop("_name1_was_person", None)
    result.pop("_website_raw", None)
    result.pop("_source_title", None)
    result.pop("_source_h1", None)
    result.pop("_tier1_query_name", None)
    result.pop("_tier1_country_code", None)
    result.pop("_registry_name_fields", None)
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
    result["record_type"] = record_type
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


def _apply_domain(
    result: dict[str, Any],
    candidate_url: str | None,
    *,
    registry: str | None = None,
    serp_title: str | None = None,
    serp_h1: str | None = None,
    serp_url: str | None = None,
    settings: Settings | None = None,
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
    )
    decision = resolve_domain(
        candidate_url,
        evidence,
        threshold=settings.domain_name_match_threshold if settings else None,
        guard_enabled=(
            settings.domain_ownership_guard_enabled if settings else None
        ),
    )

    if decision.domain:
        result["domain"] = decision.domain
        result["website_url"] = decision.website_url
        result["_website_raw"] = candidate_url
        result["domain_verified_by"] = decision.verified_by
        # A later, verified candidate clears an earlier rejection.
        result["domain_rejected"] = False
        result.pop("_domain_unverified", None)
    elif decision.rejected:
        result["domain_rejected"] = True
        result["_domain_unverified"] = True
        logger.info(
            "[%s] domain rejected as unverified: candidate=%s name1=%r",
            result.get("record_id"), decision.candidate,
            (evidence.name1 or "")[:60],
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

def _apply_tier2a(result: dict, tier2a: Tier2AResult, mode: str) -> None:
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
        result["name2_enriched"] = tier2a.name2_enriched.strip()
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


def _apply_tier3(result: dict, tier3: Tier3Result) -> None:
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
                result["name1_enriched"] = suggestion
                written.add("name1")
            else:
                logger.warning(
                    "[%s] Tier 3: REJECTED name1 '%s' → '%s' "
                    "(different entity — identity not preserved)",
                    result.get("record_id"), original_name1, suggestion,
                )
        if tier3.name2_suggestion and tier3.name2_suggestion.strip():
            result["name2_enriched"] = tier3.name2_suggestion.strip()
            result["_name2_from_tier3"] = True
            written.add("name2")
        if tier3.name3_suggestion and tier3.name3_suggestion.strip():
            result["name3_enriched"] = tier3.name3_suggestion.strip()
            written.add("name3")

    # Tier 3 is the last resort. A field it was asked about and declined to
    # write leaves the pipeline holding exactly what the record supplied, with
    # nothing having confirmed it — 8f's other half. A field an earlier tier
    # already rewrote is excluded: that value was settled before Tier 3 ran.
    unchanged: set[str] = result.setdefault("_ev_low_conf_unchanged", set())
    for field in ("name1", "name2", "name3"):
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

        if mock_clients:
            self._ror_client: RORClient = mock_clients.get("ror", RORClient(settings))
            self._lei_client: LEIClient = mock_clients.get("lei", LEIClient(settings))
            self._search_client: SearchClient = mock_clients.get(
                "search", self._build_search_client(settings))
            self._page_fetcher: PageFetcher = mock_clients.get("page_fetcher", PageFetcher(
                timeout=settings.page_fetch_timeout_seconds,
                max_chars=settings.max_page_content_chars,
            ))
            self._llm_client: OpenAIClient = mock_clients.get("llm", OpenAIClient(settings))
        else:
            self._ror_client = RORClient(settings)
            self._lei_client = LEIClient(settings)
            self._search_client = self._build_search_client(settings)
            self._page_fetcher = PageFetcher(
                timeout=settings.page_fetch_timeout_seconds,
                max_chars=settings.max_page_content_chars,
            )
            self._llm_client = OpenAIClient(settings)

        # Per-batch GLEIF/LEI telemetry counters (reset in enrich_batch).
        self._lei_counts: dict[str, int] = self._new_lei_counts()
        # Per-batch Tier 1 re-lookup telemetry (reset in enrich_batch).
        self._tier1_retry_counts: dict[str, int] = self._new_tier1_retry_counts()

        # In-memory SERP cache shared by every batch this orchestrator
        # processes, so repeated/overlapping queries reuse a prior result
        # instead of re-hitting the search API for the life of the process.
        self._serp_cache = SerpCache()

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
        self._lei_counts = self._new_lei_counts()  # reset per-batch telemetry
        self._tier1_retry_counts = self._new_tier1_retry_counts()
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
            # only: no record is merged, dropped or deduplicated here, and no
            # flag or `tier_used` is touched. See enrichment/batch_consensus.py.
            consensus = apply_batch_consensus(final_results)

            batch_ms = int((time.perf_counter() - batch_start) * 1000)
            summary = self._build_summary(final_results, batch_ms)
            summary.consensus_groups = consensus.groups
            summary.consensus_records_updated = consensus.records_updated
            summary.consensus_conflicts = consensus.conflicts
            summary.consensus_fields_propagated = dict(consensus.fields_propagated)
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

        hosts_to_probe = [f"{c}.{base}" for c in candidates]
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
        scored.sort(reverse=True)
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
        cached = cache.get_serp(query, probe_country)
        if cached is not None:
            serp_results = cached
        else:
            try:
                serp_results = await self._search_client.search(
                    query, num_results=5,
                )
            except Exception as exc:
                logger.info(
                    "[%s] dept domain probe: SERP failed: %s",
                    record_id, exc,
                )
                serp_results = []
            else:
                cache.set_serp(query, serp_results, probe_country)

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
        scored.sort(reverse=True)
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
        path_candidates: list[tuple[int, int, str]] = []
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
            path = (parsed.path or "").strip("/")
            if not path:
                continue  # bare domain handled by the host scan above
            if _path_is_generic(path):
                continue  # §5b: news/events/archive path — not a dept home
            hay = re.sub(r"[/\-_]+", " ", path).lower() + " " + (sr.title or "").lower()
            if not any(n in hay for n in needles):
                continue
            # Sort key: lowest canonicality penalty first, then SERP order.
            path_candidates.append((_path_canonicality_penalty(path), idx, sr.url))
        path_candidates.sort()
        for _penalty, _idx, cand_url in path_candidates:
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
        cached = cache.get_serp(query2)
        if cached is not None:
            serp_results2 = cached
        else:
            try:
                serp_results2 = await self._search_client.search(
                    query2, num_results=5,
                )
            except Exception as exc:
                logger.info(
                    "[%s] dept domain probe: no-site SERP failed: %s",
                    record_id, exc,
                )
                serp_results2 = []
            else:
                cache.set_serp(query2, serp_results2)

        for sr in serp_results2:
            host = _host_of(sr.url)
            if not host:
                continue
            if _is_third_party_host(host):
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

        if confirmed and confirmed.get("matched"):
            official = (confirmed.get("official_name") or affil.institution).strip()
            result["name1_enriched"] = official
            result["ror_id"] = confirmed.get("ror_id")
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
                result["name2_enriched"] = department

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
            for f in ("name2", "name3", "name4")
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
                {"name2", "name3"},
            )
        return await self._finalise_and_return(result, start, record, cache)

    async def _retry_tier1_after_canonicalisation(
        self,
        record: EnrichmentRecord,
        result: dict[str, Any],
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
        """
        if result.get("tier1_retry_attempted"):
            return
        # Already carries a registry identity — nothing to recover.
        if result.get("ror_id") or result.get("lei_id"):
            return
        original = (result.get("_tier1_query_name") or "").strip()
        if not original:
            # Tier 1 never ran for this record (skipped tier / person path);
            # there is no "originally queried with" to compare against.
            return
        canonical = (result.get("name1_enriched") or "").strip()
        if not canonical:
            return
        # Same normalisation the cache uses: a pure punctuation/case/accent
        # difference is not a corrected name and must not buy an API call.
        if normalize_key(canonical) == normalize_key(original):
            return

        result["tier1_retry_attempted"] = True
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
        try:
            ror_res = await self._ror_client.call(
                canonical,
                country_code=country_code,
                country=record.country,
                city=record.city,
                state=record.state,
            )
        except Exception as exc:  # noqa: BLE001 — a retry must never fail a record
            logger.warning(
                "[%s] Tier 1 retry ROR raised (non-fatal): %s",
                record.record_id, exc,
            )
            ror_res = {"matched": False}

        if ror_res.get("matched"):
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
            )
            result["ror_id"] = ror_res["ror_id"]
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
            return

        if not self._settings.lei_lookup_enabled:
            return
        self._lei_counts["attempts"] += 1
        try:
            lei_res = await self._lei_client.call(
                canonical, country_code=country_code,
            )
        except Exception as exc:  # noqa: BLE001 — GLEIF must never fail a record
            self._lei_counts["errors"] += 1
            logger.warning(
                "[%s] Tier 1 retry LEI raised (non-fatal): %s",
                record.record_id, exc,
            )
            return

        if lei_res.get("error"):
            self._lei_counts["errors"] += 1
            return
        if not lei_res.get("matched"):
            self._lei_counts["misses"] += 1
            return

        if lei_res.get("strategy") == "exact":
            self._lei_counts["hits_exact"] += 1
        else:
            self._lei_counts["hits_fuzzy"] += 1

        self._tier1_retry_counts["hits_lei"] += 1
        _write_registry_name(
            result, "name1", lei_res.get("legal_name"), registry="GLEIF",
        )
        result["lei_id"] = lei_res.get("lei_id")
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
        await self._maybe_resolve_website_bc(record, result, cache)
        # Defensive: every path that writes a website now writes the matching
        # domain through _apply_domain, so the two can no longer diverge. Kept
        # so a website arriving by some other route still reaches the guard
        # rather than leaving the department probe without a base domain.
        if not result.get("domain") and result.get("website_url"):
            _apply_domain(
                result, result["website_url"], settings=self._settings,
            )
        await self._probe_department_url(record.record_id, result, cache)
        await self._run_address_stage(result, record)
        result = finalise(result, start)
        return EnrichmentResult(**result)

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
            lei_res = await self._lei_client.call(name, country_code=country_code)
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
        )
        result["lei_id"] = lei_res.get("lei_id")
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

        try:
            # ── UC 0: Name 1 overflow check ──────────────────────────
            # Single LLM call. If Name1 + Name2 read as ONE continuous
            # organisation name, flag the record and return immediately
            # — no other tier runs, no auto-correction. Flagged records
            # go to manual review.
            # Skip when name1 and name2 are identical (case/whitespace-
            # normalized): two equal strings are duplicates, not an
            # overflow split. UC 12 dedup in preprocess handles them.
            n1_norm = re.sub(r"\s+", " ", (record.name1 or "").strip()).lower()
            n2_norm = re.sub(r"\s+", " ", (record.name2 or "").strip()).lower()
            if (
                record.name1 and record.name1.strip()
                and record.name2 and record.name2.strip()
                and n1_norm != n2_norm
            ):
                overflow = await run_overflow_check(
                    record_id=record.record_id,
                    name1=record.name1,
                    name2=record.name2,
                    llm_client=self._llm_client,
                )
                if overflow.is_overflow:
                    logger.info({
                        "record_id": record.record_id,
                        "step": "uc0_overflow_flagged",
                        "confidence": overflow.confidence,
                        "reasoning": overflow.reasoning,
                    })
                    # Pass through originals untouched. Flag only.
                    result["name1_enriched"] = record.name1.strip()
                    result["name2_enriched"] = record.name2.strip()
                    result["routing_type"] = "unknown"
                    result["tier_used"] = 1
                    result["source"] = "pattern_match"
                    result["confidence"] = overflow.confidence
                    result["enrichment_status"] = "unresolved"
                    result["_ev_overflow"] = True
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
                record.name1, record.name2, record.name3, record.name4,
            )
            person_verdicts = await llm_classify_plain_names_async(
                self._llm_client, suspicious,
            ) if suspicious else {}

            pre = preprocess_record(
                name1=record.name1,
                name2=record.name2,
                name3=record.name3,
                name4=record.name4,
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
            for base, pre_val, orig in (
                ("name1", pre.name1, record.name1),
                ("name2", pre.name2, record.name2),
                ("name3", pre.name3, record.name3),
                ("name4", pre.name4, record.name4),
            ):
                orig_stripped = (orig or "").strip()
                pre_stripped = (pre_val or "").strip()
                if orig_stripped and not pre_stripped:
                    preprocess_cleared.add(base)
                elif orig_stripped and pre_stripped and orig_stripped != pre_stripped:
                    # Preprocessing changed (but didn't clear) the value
                    # — write it as the enriched value now so finalise()
                    # doesn't overwrite it with the original. Example:
                    # "Accounts Payable Dept" → "Accounts Payable".
                    result[f"{base}_enriched"] = pre_stripped
                elif not orig_stripped and pre_stripped:
                    # Preprocessing populated a previously empty slot
                    # (UC 14 name3 → name2 shift). Record the new value
                    # so downstream tiers and finalise() see it.
                    result[f"{base}_enriched"] = pre_stripped
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
            # UC 7 person signal (Name 1 held only a person). Read by
            # derive_search_terms so an unresolved person row emits no ST1
            # (its original SAP name is a person, not an institution).
            result["_name1_was_person"] = bool(
                getattr(pre, "name1_was_person", False)
            )

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
            result["_has_dept_signal"] = bool(
                (pp_name2 and pp_name2.strip())
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
                )

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
                    )

                    # Carry the ROR acronym (when present) for the
                    # search_term_1 derivation in finalise().
                    ror_acronym = ror_parent.get("acronym")
                    if ror_acronym and ror_acronym.strip():
                        result["_ror_acronym"] = ror_acronym.strip()

                    result["ror_id"] = ror_parent["ror_id"]
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

                    # ── Child match for name2 and name3 ──────────────
                    children = ror_parent.get("children", [])
                    for field_key, field_val in [("name2", pp_name2), ("name3", pp_name3), ("name4", pp_name4)]:
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
                        result["name1_enriched"] = name1_cleaned
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["tier_used"] = 1
                        result["routing_type"] = "research_institution"
                        result["enrichment_status"] = "unresolved"
                        result.setdefault(
                            "_ev_low_conf_unchanged", set(),
                        ).add("name1")
                        institution_domain = None
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
                        if lei_matched:
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

                    if company_res and company_res.success and company_res.name1_enriched:
                        result["name1_enriched"] = company_res.name1_enriched
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
                        result["name1_enriched"] = name1_cleaned
                        result["source"] = "passthrough"
                        result["confidence"] = "low"
                        result["tier_used"] = 1
                        result["routing_type"] = "unknown"
                    # else: research-institution passthrough already set above

                    institution_domain = None

            name2_already_filled = bool(pp_name2 and pp_name2.strip())
            has_dept_signal = result["_has_dept_signal"]
            multi_contact = result["_multi_contact"]

            # ── AP short-circuit: handled entirely by preprocessing ──
            # Check both name2 and name3 for AP normalisation. If any
            # AP field is present, return immediately.
            any_ap = any(
                (getattr(pre, f) or "").strip().lower() == "accounts payable"
                for f in ("name1", "name2", "name3")
            )
            if any_ap:
                for f, pp_val in [("name2", pp_name2), ("name3", pp_name3)]:
                    if pp_val and pp_val.strip():
                        result[f"{f}_enriched"] = pp_val.strip()
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
                    name3_already_set = bool(pp_name3 and pp_name3.strip())
                    result["name2_enriched"] = lab_res.parent_department
                    if not name3_already_set:
                        # Promote the original lab name to Name3.
                        result["name3_enriched"] = pp_name2.strip()
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
                        # The lab name could not be demoted, so Name 2 / Name
                        # 3 no longer describe a clean parent/child split.
                        result["_ev_name3_not_demoted"] = True
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
            # Runs the same logic for BOTH name2 and name3: child match
            # first (already done above), then Tier 2 canonical LLM,
            # then UC 5 scope filter. Zero SerpAPI calls.
            # Uses a shared helper to avoid duplication.
            can_canonical = (
                result["routing_type"] in ("research_institution", "company")
                and result.get("name1_enriched")
            )
            any_canonical_ran = False
            for field_key, pp_val in [("name2", pp_name2), ("name3", pp_name3), ("name4", pp_name4)]:
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
                    result[f"{field_key}_enriched"] = pp_val
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
                        result[f"{field_key}_enriched"] = pp_val
                    else:
                        result[f"{field_key}_enriched"] = canonical.name2_enriched
                        if 5 not in result["use_cases_triggered"]:
                            result["use_cases_triggered"].append(5)
                else:
                    # LLM not confident — passthrough original
                    result[f"{field_key}_enriched"] = pp_val

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
                        _apply_tier2a(result, tier2a_result, tier2a_result.mode)
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
                contact=pp_contact,
                street=pp_street1 or record.street,
                city=record.city,
                state=record.state,
                zip_code=record.zip,
                country=record.country,
                llm_client=self._llm_client,
            )

            _apply_tier3(result, tier3_result)

            # Last resort: if nothing enriched name1, pass through the preprocessed original
            if not result.get("name1_enriched") and not is_blank(pp_name1):
                result["name1_enriched"] = (pp_name1 or "").strip()

            # Rule: if the input had no name2 and no contact, there is no
            # signal from which to infer a department — name2_enriched
            # must remain empty regardless of what any tier produced.
            if not has_dept_signal:
                result["name2_enriched"] = None

            # Rule: if preprocessing stripped name2 down to empty
            # (because it was actually a contact / email / address /
            # AP reference), do not let Tier 3 fabricate a replacement.
            if (
                record.name2 and record.name2.strip()
                and not (pp_name2 and pp_name2.strip())
            ):
                logger.info({
                    "record_id": record.record_id,
                    "step": "name2_cleared_by_preprocess",
                    "original": record.name2,
                })
                result["name2_enriched"] = None

            # Rule: name2_enriched should never echo name1_enriched.
            if (
                result.get("name2_enriched")
                and result.get("name1_enriched")
                and result["name2_enriched"].strip().lower()
                    == result["name1_enriched"].strip().lower()
            ):
                logger.info({
                    "record_id": record.record_id,
                    "step": "name2_equals_name1_dropped",
                    "value": result["name2_enriched"],
                })
                result["name2_enriched"] = None

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
            elif r.domain_verified_by == "email":
                summary.domain_from_email += 1
            elif r.domain_verified_by is not None:
                summary.domain_from_serp += 1
            if r.domain_rejected:
                summary.domain_rejected_unverified += 1

        return summary
