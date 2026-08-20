"""Pydantic v2 request/response models for the enrichment API."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    Field,
)

from api.output_columns import RESPONSE_COLUMNS


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class EnrichmentRecord(BaseModel):
    """A single customer master data record to enrich.

    The canonical request body mirrors the SAP customer-master export
    columns one-to-one. Each field accepts the exact spreadsheet header
    as its JSON key (e.g. ``"Name 1"``, ``"Country/Region Key"``) via a
    Pydantic alias. For backwards compatibility the previous snake-case
    keys (``name1``, ``zip``, ``record_id`` …) are still accepted as
    secondary aliases.

    The enrichment pipeline reads a handful of normalised attributes
    (``name1``, ``state``, ``zip`` …) — those are exposed as read-only
    properties that map onto the SAP fields, so the orchestrator does
    not need to know about the SAP column naming.
    """

    # Accept the field's own (snake_case) name in addition to the
    # declared validation aliases.
    model_config = ConfigDict(populate_by_name=True)

    # ── Identity & administrative ────────────────────────────────────
    customer: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Customer", "customer", "record_id"),
        description="Customer number (primary key). Used as record_id.",
    )
    ecc_customer_number: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ECC Customer Number", "ecc_customer_number"),
    )
    central_deletion_flag: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Central Deletion Flag", "central_deletion_flag"),
    )
    comments: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Comments", "comments"),
    )
    account_group: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Account group", "account_group"),
    )
    company_code: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Company Code", "company_code"),
    )
    sales_organization: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Sales Organization", "sales_organization"),
    )
    distribution_channel: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Distribution Channel", "distribution_channel"),
    )
    division: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Division", "division"),
    )

    # ── Name block ───────────────────────────────────────────────────
    name_1: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Name 1", "name_1", "name1"),
    )
    name_2: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Name 2", "name_2", "name2"),
    )
    name_3: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Name 3", "name_3", "name3"),
    )
    name_4: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Name 4", "name_4", "name4"),
    )

    # ── Address block ────────────────────────────────────────────────
    street_1: Optional[str] = Field(
        default=None,
        # ``street`` was the legacy single-street key.
        validation_alias=AliasChoices("Street 1", "street_1", "street1", "street"),
    )
    house_number: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("House Number", "house_number"),
    )
    street_2: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Street 2", "street_2", "street2"),
    )
    street_3: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Street 3", "street_3", "street3"),
    )
    street_4: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Street 4", "street_4", "street4"),
    )
    street_5: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Street 5", "street_5", "street5"),
    )
    po_box: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("PO Box", "po_box"),
    )
    country_region_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Country/Region Key", "country_region_key", "country"),
    )
    postal_code: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Postal Code", "postal_code", "zip"),
    )
    city: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("City", "city"),
    )
    region: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Region", "region", "state"),
    )

    # ── Other SAP master-data columns (carried through, not enriched) ─
    language_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Language Key", "language_key"),
    )
    reconciliation_acct: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Reconciliation acct", "reconciliation_acct"),
    )
    tax_jurisdiction: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Tax Jurisdiction", "tax_jurisdiction"),
    )
    central_delivery_block: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Central delivery block", "central_delivery_block"),
    )
    delivery_priority: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Delivery Priority", "delivery_priority"),
    )
    shipping_conditions: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Shipping Conditions", "shipping_conditions"),
    )
    delivering_plant: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Delivering Plant", "delivering_plant"),
    )
    created_on: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Created On", "created_on"),
    )
    created_by: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Created By", "created_by"),
    )
    vat_registration_no: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("VAT Registration No.", "vat_registration_no"),
    )
    search_term_1: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Search Term 1", "search_term_1"),
    )
    search_term_2: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Search Term 2", "search_term_2"),
    )
    terms_of_payment_contact: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Terms of Payment Contact", "terms_of_payment_contact"),
    )

    # ── Auxiliary enrichment inputs (no SAP column) ──────────────────
    # The SAP export has no dedicated contact-person / email / c-o
    # column, but the enrichment pipeline (Tier 2A contact lookup, c/o
    # handling) consumes them when available. They are accepted as
    # optional auxiliary inputs so that functionality keeps working;
    # when absent, the same signals are recovered from the Name fields
    # during preprocessing.
    care_of: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("care_of", "Care Of", "c/o"),
    )
    contact: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("contact", "Contact"),
    )
    email: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("email", "Email"),
    )

    # No field is mandatory: every column is optional, including the
    # customer identifier. When absent, ``record_id`` falls back to an
    # empty string (the record is still processed; correlation just lacks
    # an id).

    # ── Compatibility accessors used by the enrichment pipeline ──────
    # The orchestrator/preprocess/address code reads these normalised
    # names; they map onto the SAP columns above.

    @property
    def record_id(self) -> str:
        return (self.customer or self.ecc_customer_number or "").strip()

    @property
    def name1(self) -> Optional[str]:
        return self.name_1

    @property
    def name2(self) -> Optional[str]:
        return self.name_2

    @property
    def name3(self) -> Optional[str]:
        return self.name_3

    @property
    def name4(self) -> Optional[str]:
        return self.name_4

    @property
    def street(self) -> Optional[str]:
        # Legacy single-street accessor → SAP "Street 1".
        return self.street_1

    @property
    def street1(self) -> Optional[str]:
        return self.street_1

    @property
    def street2(self) -> Optional[str]:
        return self.street_2

    @property
    def street3(self) -> Optional[str]:
        return self.street_3

    @property
    def street4(self) -> Optional[str]:
        return self.street_4

    @property
    def street5(self) -> Optional[str]:
        return self.street_5

    @property
    def state(self) -> Optional[str]:
        return self.region

    @property
    def zip(self) -> Optional[str]:
        return self.postal_code

    @property
    def country(self) -> Optional[str]:
        return self.country_region_key


class EnrichmentOptions(BaseModel):
    """Per-request processing options."""
    max_concurrency: int = Field(default=5, ge=1, le=20)
    serp_provider: Literal["serpapi", "duckduckgo"] = "serpapi"
    skip_tier: Optional[int] = Field(default=None, description="Skip a specific tier (for testing)")


class EnrichmentRequest(BaseModel):
    """Top-level POST /enrich request body."""
    records: List[EnrichmentRecord] = Field(..., min_length=1)
    options: EnrichmentOptions = Field(default_factory=EnrichmentOptions)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class EnrichmentResult(BaseModel):
    """Enrichment outcome for one record.

    Serialises with the exact /enrich/file column headers as JSON keys
    (via ``RESPONSE_COLUMNS``), so the response body and the file output
    share one schema — same columns, same names. Internal code keeps
    using the snake_case field names; only the wire format is aliased.
    """
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            serialization_alias=lambda field_name: RESPONSE_COLUMNS.get(
                field_name, field_name
            ),
        ),
    )

    # Serialised fields are declared in RESPONSE_COLUMNS order so the JSON
    # response carries the columns in the same order as the file output.

    # ── Identity & administrative (carried through verbatim) ─────────
    record_id: str
    ecc_customer_number: Optional[str] = None
    central_deletion_flag: Optional[str] = None
    comments: Optional[str] = None
    account_group: Optional[str] = None
    company_code: Optional[str] = None
    sales_organization: Optional[str] = None
    distribution_channel: Optional[str] = None
    division: Optional[str] = None

    # ── Name block (enriched) + web enrichment ───────────────────────
    name1_enriched: Optional[str] = None
    name2_enriched: Optional[str] = None
    name3_enriched: Optional[str] = None
    name4_enriched: Optional[str] = None
    # Serialised as the "Domain" column: the registrable domain ('mit.edu'),
    # written only through utils.domain_resolver.resolve_domain. Null when the
    # candidate could not be verified as belonging to this organisation (the
    # record is then flagged `domain-unverified`).
    domain: Optional[str] = None
    # Unit-scoped host with TLD (e.g. 'cs.mit.edu') when the source URL
    # points to a real subdomain of the institution domain. Null when
    # name2 is absent or the source URL is the bare institution domain.
    department_domain: Optional[str] = None

    # ── Contact block (enriched) ─────────────────────────────────────
    # c/o (extracted from prefixed Name 2 or passed through)
    care_of_enriched: Optional[str] = None
    # Contact / email (extracted or passed through)
    contact_enriched: Optional[str] = None
    email_enriched: Optional[str] = None

    # ── Address Stage 1 — cleaned streets + extracted sub-locations ──
    street_cleaned: Optional[str] = None
    # House number passed through verbatim from the input record.
    house_number: Optional[str] = None
    street_2_cleaned: Optional[str] = None
    street_3_cleaned: Optional[str] = None
    street_4_cleaned: Optional[str] = None
    street_5_cleaned: Optional[str] = None
    po_box_extracted: Optional[str] = None
    suite: Optional[str] = None
    building: Optional[str] = None
    floor: Optional[str] = None
    room: Optional[str] = None
    unit: Optional[str] = None
    mail_stop: Optional[str] = None
    unloading_point: Optional[str] = None
    mail_code: Optional[str] = None

    # ── Geography & remaining SAP master-data (carried through) ───────
    country_region_key: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    language_key: Optional[str] = None
    reconciliation_acct: Optional[str] = None
    tax_jurisdiction: Optional[str] = None
    central_delivery_block: Optional[str] = None
    delivery_priority: Optional[str] = None
    shipping_conditions: Optional[str] = None
    delivering_plant: Optional[str] = None
    created_on: Optional[str] = None
    created_by: Optional[str] = None
    vat_registration_no: Optional[str] = None
    # Compact search handles for downstream re-querying.
    # search_term_1 mirrors name1 (acronym preferred, domain fallback);
    # search_term_2 mirrors name2 with the same shape — null when name2 is absent.
    search_term_1: Optional[str] = None
    search_term_2: Optional[str] = None
    terms_of_payment: Optional[str] = None

    # ── Review metadata (enrichment output) ──────────────────────────
    flag_for_review: bool = False
    flag_reason: Optional[str] = None
    error: Optional[str] = None
    record_type: Literal["research_institution", "company", "unknown"] = "unknown"
    # Registry identifiers — both surface in the JSON (not excluded) so the
    # dedup phase can converge records on a shared identifier: ror_id for
    # institutions, lei_id for companies (e.g. "Pfizer AG" / "Pfizer").
    ror_id: Optional[str] = None
    lei_id: Optional[str] = None

    # ── Internal-only fields (excluded from serialisation) ───────────
    # NOTE: the fields marked ``exclude=True`` below are still populated and
    # used internally (tier logic, batch summary counts, tests) — they are
    # just omitted from the serialised API response to keep the output lean.
    tier_used: Literal[1, 2, 3] = Field(default=1, exclude=True)
    tier2_mode: Optional[Literal["2A_population", "2A_verification", "2B"]] = Field(default=None, exclude=True)
    confidence: Literal["high", "medium", "low", "none"] = Field(default="none", exclude=True)
    source: Literal[
        "ROR", "ROR+child", "contact_lookup_found",
        "contact_lookup_corrected", "dept_search", "LLM",
        "llm_canonical", "SERP+LLM", "pattern_match",
        "web_search", "passthrough", "gleif", "none",
    ] = Field(default="none", exclude=True)
    source_url: Optional[str] = Field(default=None, exclude=True)
    # The organisation homepage, always ``https://<domain>`` — never a deep
    # path or a sub-site host. Derived from ``domain`` by
    # utils.domain_resolver, and excluded from the response body: the "Domain"
    # column carries the bare ``domain``, keeping the JSON response identical
    # to the file output schema.
    website_url: Optional[str] = Field(default=None, exclude=True)
    # Which ownership condition accepted ``domain`` ("registry" | "email" |
    # "name" | "serp" | "unguarded"), and whether a candidate was rejected
    # outright. Batch-summary telemetry only.
    domain_verified_by: Optional[str] = Field(default=None, exclude=True)
    domain_rejected: bool = Field(default=False, exclude=True)
    # Tier 1 re-lookup after canonicalisation (orchestrator.
    # _retry_tier1_after_canonicalisation). `tier1_retry_attempted` records
    # that the one permitted retry has been spent; `tier1_retry_hit` names the
    # registry that answered on the retry ("ROR" | "gleif"), which is what
    # separates a retry hit from a first-pass Tier 1 hit in the evaluation.
    # Both excluded from the response body: the exported column set is
    # unchanged.
    tier1_retry_attempted: bool = Field(default=False, exclude=True)
    tier1_retry_hit: Optional[str] = Field(default=None, exclude=True)
    # Which evidence source decided `record_type` (enrichment/classifier.py):
    # "ror" | "gleif" | "keyword" | "unresolved". A record_type of "unknown"
    # always reports "unresolved". Excluded from the response body — the
    # exported column set is unchanged.
    record_type_source: Literal["ror", "gleif", "keyword", "unresolved"] = Field(
        default="unresolved", exclude=True,
    )
    # Provisional type used for branch selection and tier gating during the run.
    # Internal only; `record_type` is the decided value. `routing_type_mismatch`
    # marks a record whose tiers were gated on a type the evidence later
    # contradicted — it was routed down the wrong branch and is NOT re-run.
    routing_type: Literal["research_institution", "company", "unknown"] = Field(
        default="unknown", exclude=True,
    )
    routing_type_mismatch: bool = Field(default=False, exclude=True)
    contact_used: bool = Field(default=False, exclude=True)
    name2_match_result: Literal["exact", "partial", "no_match", "not_applicable", "unknown"] = Field(default="not_applicable", exclude=True)
    # Which use cases (0-9) fired for this record
    use_cases_triggered: List[int] = Field(default_factory=list, exclude=True)
    enrichment_status: Literal["enriched", "verified", "unresolved", "failed"] = Field(default="failed", exclude=True)
    duration_ms: int = Field(default=0, exclude=True)


class EnrichmentSummary(BaseModel):
    """Aggregate statistics for the batch."""
    total: int = 0
    enriched: int = 0
    verified: int = 0
    unresolved: int = 0
    failed: int = 0
    research_institution_count: int = 0
    company_count: int = 0
    tier1_resolved: int = 0
    # Tier 1 LEI (GLEIF) telemetry — the company registry step.
    tier1_lei_count: int = 0
    lei_attempts: int = 0
    lei_hits_exact: int = 0
    lei_hits_fuzzy: int = 0
    lei_misses: int = 0
    lei_errors: int = 0
    # Tier 1 re-lookup after canonicalisation: how often a later tier produced
    # a name Tier 1 had never seen, and how often looking that name up
    # recovered a registry identity the first pass missed.
    tier1_retry_attempts: int = 0
    tier1_retry_hits_ror: int = 0
    tier1_retry_hits_lei: int = 0
    # Records whose provisional routing_type disagreed with the record_type the
    # evidence finally supported — i.e. tiers were gated on the wrong type.
    # Surfaced, not corrected: re-running those records is a separate decision.
    routing_type_mismatch_count: int = 0
    # Lookups served under the normalised cache key that the previous
    # lowercase-only key would have missed — ROR + LEI + SERP combined.
    cache_hits_after_normalisation: int = 0
    # Domain ownership guard telemetry (utils/domain_resolver.py). The three
    # `domain_from_*` counters partition the records that kept a domain by the
    # evidence that carried it; `domain_from_serp` covers every web-derived
    # domain (name similarity or on-domain search evidence).
    domain_from_registry: int = 0
    domain_from_email: int = 0
    domain_from_serp: int = 0
    domain_rejected_unverified: int = 0
    tier2a_population_count: int = 0
    tier2a_verification_count: int = 0
    tier2b_count: int = 0
    tier3_count: int = 0
    contact_lookup_attempted: int = 0
    contact_lookup_success: int = 0
    processing_time_ms: int = 0


class EnrichmentResponse(BaseModel):
    """Top-level POST /enrich response body."""
    results: List[EnrichmentResult]
    summary: EnrichmentSummary


class HealthResponse(BaseModel):
    """GET /health response."""
    status: str = "healthy"
    version: str = "1.0.0"
    env: str = "production"
    mock_mode: bool = False
    tiers_available: List[int] = Field(default_factory=lambda: [1, 2, 3])


class TierConfigResponse(BaseModel):
    """GET /tiers response with current thresholds."""
    # FIX(Bug 1): single ROR threshold for all record types
    ror_confidence_threshold: float
    fuzzy_match_threshold: int
    max_page_content_chars: int
    page_fetch_timeout_seconds: int
    default_max_concurrency: int
    serp_provider: str
    mock_mode: bool
