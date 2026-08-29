"""Pydantic v2 request/response models for the enrichment API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    AliasChoices,
    AliasGenerator,
    BaseModel,
    ConfigDict,
    Field,
)

from api.output_columns import RESPONSE_COLUMNS
from enrichment.provenance import (
    DERIVED_SCALAR_FIELDS,
    SCOPED_FIELDS,
    Evidence,
    MissingEvidenceError,
    UnattributedWriteError,
    derived_scalar,
    log_from_dicts,
)


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
    name_5: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("Name 5", "name_5", "name5"),
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
    def name5(self) -> Optional[str]:
        return self.name_5

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
    name5_enriched: Optional[str] = None
    # Fix 3 — the organisation name the candidate website states about itself,
    # read from the page by a constrained reader. NEVER written to
    # `name1_enriched`: a page is a witness, not an authority on what the
    # customer master should call this customer, and a brand-vs-legal-entity
    # difference is normal rather than an error to correct. Null unless a page
    # read returned an identity.
    operating_name: Optional[str] = None
    # `web:{domain}:provisional` — Provenance Scheme B, the same grammar the
    # scoped fields use (enrichment/confidence.py). Never `verified`: the page
    # and the domain that served it are one evidence system, so a site naming
    # itself corroborates nothing independent. The fetch date used to be in
    # this string and is now on the cache entry and the
    # `operating_name_extracted` trace line — a decaying token in an exported
    # field is read as part of the claim.
    operating_name_provenance: Optional[str] = None
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
    # The three flag fields are always consistent, and are rebuilt together
    # once per record from its final state — see enrichment/flags.py.
    # `flag_for_review` is DERIVED, not "`flag_codes` is non-empty": a core
    # field at `low` confidence raises it with no code attached, and a code in
    # `enrichment.flags.ADVISORY_CODES` (`registry-location-mismatch`,
    # `domain-unverified`) ships its prose without raising it. So a row can carry a populated
    # `flag_reason` with this false — the finding is stated, no review is
    # requested. A consumer building the review queue must read this field and
    # not test `flag_codes` for emptiness.
    flag_for_review: bool = False
    # Machine-readable triage codes from enrichment.flags.ALL_CODES. A record
    # can carry several; the single concatenated `flag_reason` string it
    # replaced did not scale past one condition.
    flag_codes: List[str] = Field(default_factory=list)
    # The output fields this record's flags concern (name1, name2, name3,
    # domain, contact, email, address). A record with a verified ROR ID and an
    # uncertain department lists `name2` alone, which is what tells a reviewer
    # a one-field check from a full record review.
    flagged_fields: List[str] = Field(default_factory=list)
    # Human-readable prose rendering of `flag_codes`, field scope included so
    # the scope survives for a consumer that reads only this column.
    flag_reason: Optional[str] = None
    # Which fields each code concerns, keyed by code — the scope map the three
    # columns above are rendered from. Internal: `flagged_fields` is the union
    # of these and is what ships. It exists because the batch consensus pass
    # runs after `compute_flags` and must be able to withdraw one code from one
    # field without re-deriving the rest (see enrichment.flags.retract).
    flag_scopes: Dict[str, List[str]] = Field(default_factory=dict, exclude=True)
    # The specific value a code names in its prose, keyed by code — the
    # rejected domain for `domain-unverified`. Internal for the same reason
    # `flag_scopes` is: it is already rendered into `flag_reason`, and is kept
    # only so a later withdrawal can re-render the codes it keeps with the
    # wording they were raised with.
    flag_details: Dict[str, str] = Field(default_factory=dict, exclude=True)
    # One clause appended to a code's prose, keyed by code (Fix 3) — the page
    # read's finding that a candidate site belongs to a company in a different
    # city. Internal for the same reason `flag_details` is: it is already
    # rendered into `flag_reason`, and is kept so a later withdrawal re-renders
    # the codes it keeps with the wording they were raised with.
    flag_notes: Dict[str, str] = Field(default_factory=dict, exclude=True)
    # Core fields (Name 1, Name 2) whose derived provenance confidence is
    # `low` — the derived half of `flag_for_review`, which no longer follows
    # from `flag_codes` alone. Internal for the same reason `flag_scopes` is:
    # it is already rendered into `flag_reason` and into `flagged_fields`, and
    # is kept so the batch-consensus pass can tell whether its write changed
    # the derivation (see enrichment.flags.retract). The confidence itself
    # ships, in `name1_provenance` / `name2_provenance`.
    flag_low_confidence: List[str] = Field(default_factory=list, exclude=True)
    error: Optional[str] = None
    record_type: Literal[
        "research_institution", "company", "government", "unknown",
    ] = "unknown"
    # Registry identifiers — both surface in the JSON (not excluded) so the
    # dedup phase can converge records on a shared identifier: ror_id for
    # institutions, lei_id for companies (e.g. "Pfizer AG" / "Pfizer").
    ror_id: Optional[str] = None
    lei_id: Optional[str] = None
    # The registry identifier of a Name 2 that turned out to be its OWN
    # registered entity — "Ames Research Center" under NASA, resolving to its
    # own ROR record rather than to NASA's. Written only by the grounded
    # resolver's re-verification step, and only when the id differs from
    # Name 1's, so it can never be a second copy of `ror_id`.
    #
    # Excluded from the response body on purpose. `ror_id` / `lei_id` are
    # exported because they identify THE ORGANISATION the record is about, and
    # the dedup phase converges records on them; a unit's own identifier
    # answers a different question and would converge the wrong rows. It is
    # kept because it is real evidence about how the match was made, and the
    # trace is where that belongs.
    name2_registry_id: Optional[str] = Field(default=None, exclude=True)

    # ── Per-field provenance (Fix 10) ────────────────────────────────
    # Six derived scalars, one per Phase 1 scoped field, in Provenance
    # Scheme B — `source:confidence[+witness]`, e.g. `ror:verified`,
    # `input:verified+web`, `llm:provisional`, `web:acme.com:provisional`.
    # The grammar and the confidence table are in enrichment/confidence.py.
    # They are REGENERATED from `provenance` and never maintained separately,
    # so the column and the log cannot drift. Null when the field is null:
    # there is no value to attribute.
    name1_provenance: Optional[str] = None
    name2_provenance: Optional[str] = None
    domain_provenance: Optional[str] = None
    record_type_provenance: Optional[str] = None
    ror_id_provenance: Optional[str] = None
    lei_id_provenance: Optional[str] = None

    # The events themselves — one per write, nested, unbounded length, and
    # NOT a file column (the XLSX writer emits `RESPONSE_COLUMNS` only, which
    # is where the six scalars above live and this does not). It is part of
    # the API response, not telemetry: Application Insights stays operational
    # monitoring, and ADF decides what, if anything, to store.
    provenance: List[Dict[str, Any]] = Field(default_factory=list)
    # Candidates a GUARD refused — the ROR country guard, the distinctive- and
    # identifier-token guards, Fix 1's domain ownership guard, GLEIF's name
    # verification. Only guard rejections: the pipeline had a confident answer
    # and deliberately declined it, which is the case worth defending later.
    # Capped per field per record; the count of any beyond the cap is below.
    provenance_rejected: List[Dict[str, Any]] = Field(default_factory=list)
    provenance_rejected_omitted: Dict[str, int] = Field(default_factory=dict)

    # ── Internal-only fields (excluded from serialisation) ───────────
    # NOTE: the fields marked ``exclude=True`` below are still populated and
    # used internally (tier logic, batch summary counts, tests) — they are
    # just omitted from the serialised API response to keep the output lean.
    tier_used: Literal[1, 2, 3] = Field(default=1, exclude=True)
    tier2_mode: Optional[Literal["2A_population", "2A_verification", "2B"]] = Field(default=None, exclude=True)
    # A COARSE PROJECTION, not a measurement (Fix 10, Step 3).
    #
    # One label fed by numbers that are not commensurable: a ROR local rescore
    # (`ror_local`, 0-1), GLEIF's name-verification ratio (`fuzzy_ratio`,
    # 0-100), a model's assertion about its own output (`llm_self_reported`,
    # which is not a probability of anything) and a passthrough rule that sets
    # "low" because nothing was found. "high" from a registry match and "high"
    # from an LLM mean different things, and no consumer of this field can tell
    # them apart.
    #
    # Kept for backward compatibility. The measurement lives per field, on the
    # provenance events: `confidence_scale` + `confidence_value`, projected for
    # reading into the six `*_provenance` columns, where the band is namespaced
    # by scale (`self_high`, never a bare `high`) precisely so the two cannot
    # be confused again.
    confidence: Literal["high", "medium", "low", "none"] = Field(default="none", exclude=True)
    source: Literal[
        "ROR", "ROR+child", "contact_lookup_found",
        "contact_lookup_corrected", "dept_search", "LLM",
        "llm_canonical", "SERP+LLM", "pattern_match",
        "web_search", "passthrough", "gleif", "none",
        # Written by the batch consensus pass (enrichment/batch_consensus.py)
        # on a record that inherited an organisation-level field from another
        # record in the same batch. `tier_used` is deliberately NOT changed to
        # 1 — inflating the Tier 1 count would corrupt the tier-distribution
        # figures used in evaluation.
        "batch_consensus",
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
    # The page-fed entry point (Fix 3, PAGE_EXTRACT_FEEDS_RETRY) spends its own
    # once-per-record budget, so a spent canonical retry cannot starve it and
    # vice versa.
    tier1_page_retry_attempted: bool = Field(default=False, exclude=True)
    tier1_retry_hit: Optional[str] = Field(default=None, exclude=True)
    # Fix 2 — which of the three unchanged-Name-1 states this record is in, or
    # None when a tier rewrote Name 1 and none of them apply. Excluded from the
    # response body: the state already ships, in `name1_provenance`
    # (`input:verified+web` / `input:provisional+llm` / `input:low`). This
    # field is the same fact in a form the batch summary and the evaluation
    # scripts can count without parsing a scalar.
    unchanged_name1_state: Optional[
        Literal["unchanged-verified", "unchanged-confirmed", "unchanged-unresolved"]
    ] = Field(default=None, exclude=True)
    # Which evidence source decided `record_type` (enrichment/classifier.py):
    # "ror" | "gleif" | "keyword" | "unresolved". A record_type of "unknown"
    # always reports "unresolved". Excluded from the response body — the
    # exported column set is unchanged.
    record_type_source: Literal[
        "ror", "gleif", "legal_form", "keyword", "unresolved",
    ] = Field(
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

    # ── The write lock, carried past finalisation (Fix 10, Step 1) ───
    # ``EnrichedRecord`` locks the six scoped fields for the life of the
    # pipeline; this locks them for the life of the *result*. It matters
    # because the batch consensus pass (Fix 6) writes ROR IDs, domains and
    # names onto already-finalised records, and an inherited registry
    # identifier must never be indistinguishable from a first-hand one.

    def __setattr__(self, name: str, value: object) -> None:
        if name in SCOPED_FIELDS:
            raise UnattributedWriteError(
                f"{name!r} is write-locked on EnrichmentResult: use "
                f"result.write({name!r}, value, evidence).",
            )
        super().__setattr__(name, value)

    def write(self, field: str, value: object, evidence: Evidence) -> None:
        """The one write path for a scoped field on a finalised result.

        Appends the event to ``provenance`` and regenerates that field's
        derived scalar from the log, so the two can never disagree.
        """
        if not isinstance(evidence, Evidence):
            raise MissingEvidenceError(
                f"write to {field!r} requires an Evidence argument — a value "
                "whose origin cannot be reconstructed is not admissible",
            )
        log = log_from_dicts(
            self.provenance, self.provenance_rejected,
            self.provenance_rejected_omitted,
        )
        if field in SCOPED_FIELDS:
            log.record(field, getattr(self, field, None), value, evidence)
        self._force(field, value)
        self._force("provenance", log.as_dicts())
        column = DERIVED_SCALAR_FIELDS.get(field)
        if column:
            self._force(
                column,
                derived_scalar(log, field, self)
                if value not in (None, "") else None,
            )

    def _force(self, name: str, value: object) -> None:
        """Set a field past the lock. Only ``write`` may call this."""
        object.__setattr__(self, name, value)
        self.__pydantic_fields_set__.add(name)


class EnrichmentSummary(BaseModel):
    """Aggregate statistics for the batch."""
    total: int = 0
    enriched: int = 0
    verified: int = 0
    unresolved: int = 0
    failed: int = 0
    research_institution_count: int = 0
    company_count: int = 0
    #: Added with the `government` record type. Without it a public body
    #: counted in neither of the two above and the summary under-reported.
    government_count: int = 0
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
    # Fix 2 — the three states a record whose Name 1 was kept from the input
    # can be in. They partition that population exactly: their sum is the
    # number of records that shipped the value they arrived with, and only the
    # last of the three is flagged.
    unchanged_verified: int = 0
    unchanged_confirmed: int = 0
    unchanged_unresolved: int = 0
    # Fix 3 — the page-read corroborator. The six outcome counters partition
    # `page_reads_attempted`; the last two count what the verdicts did.
    page_reads_attempted: int = 0
    page_corroborated: int = 0
    page_contradicted: int = 0
    page_name_mismatch: int = 0
    page_fetch_unavailable: int = 0
    page_no_identity: int = 0
    page_parked: int = 0
    page_domains_withdrawn: int = 0
    page_flags_cleared: int = 0
    # A page named a different organisation but did not place it in another
    # state or country, so the accepted domain was reported and kept rather
    # than withdrawn. Almost always a brand-vs-legal-name variant.
    page_mismatch_not_withdrawn: int = 0
    #: Domains the page read ACCEPTED — the candidate had reached no other
    #: ownership condition and the site itself named the record's
    #: organisation. Distinct from `page_flags_cleared`, which counts the
    #: flag withdrawals: a record whose domain the guard had already accepted
    #: clears its flag without this counter moving.
    page_domains_accepted: int = 0
    # Wikidata crosswalk lane (enrichment/wikidata.py). `matched`, `no_match`,
    # `ambiguous` and `unavailable` partition `queried` — every invocation ends
    # in exactly one. `type_rejected` / `country_rejected` are diagnostics that
    # deliberately overlap with `no_match`: they count records where a gauntlet
    # step refused at least one candidate.
    wikidata_queried: int = 0
    wikidata_matched: int = 0
    wikidata_no_match: int = 0
    wikidata_ambiguous: int = 0
    wikidata_unavailable: int = 0
    wikidata_type_rejected: int = 0
    wikidata_country_rejected: int = 0
    # What the pointer bought. `crosswalk_ror` + `crosswalk_lei` count pointers
    # followed; `crosswalk_registry_hit` counts the ones the registry's own
    # guards then confirmed — the gap between them is pointers the registry
    # refused, which is the lane working as designed.
    wikidata_crosswalk_ror: int = 0
    wikidata_crosswalk_lei: int = 0
    wikidata_crosswalk_registry_hit: int = 0
    wikidata_superseded_flagged: int = 0
    # Liveness lane (enrichment/liveness.py). `checked` is the denominator;
    # the three `*_flagged` counters count FINDINGS and may overlap, so their
    # sum is >= `flagged`, which counts RECORDS. `ror_queried` is the lane's
    # entire network cost.
    liveness_checked: int = 0
    liveness_flagged: int = 0
    liveness_ror_queried: int = 0
    liveness_ror_flagged: int = 0
    liveness_gleif_flagged: int = 0
    liveness_redirect_flagged: int = 0
    # A match with no registry pointer: `operating_name` at most, never Name 1.
    wikidata_witness_only: int = 0
    wikidata_domain_corroborated: int = 0
    # P856 disagreed with the record's candidate domain. Counted and acted on
    # in no way — Wikidata may be stale, and a wiki field is not grounds to
    # withdraw a domain the ownership guard accepted.
    wikidata_domain_disagree: int = 0
    # The lane's corroboration-only pass on records the registries already
    # resolved: how often it ran, and how often it returned a `P856` to
    # compare against the candidate domain. Separate from `wikidata_queried` /
    # `wikidata_matched`, which measure the crosswalk lane on the disjoint
    # population of records that hold no registry identifier.
    wikidata_corroboration_queried: int = 0
    wikidata_corroboration_matched: int = 0
    # Records whose provisional routing_type disagreed with the record_type the
    # evidence finally supported — i.e. tiers were gated on the wrong type.
    # Surfaced, not corrected: re-running those records is a separate decision.
    routing_type_mismatch_count: int = 0
    # Lookups served under the normalised cache key that the previous
    # lowercase-only key would have missed — ROR + LEI + SERP combined.
    cache_hits_after_normalisation: int = 0
    # ── Fix B — the shared evidence cache (utils/cache.py) ────────────────
    # `evidence_cache_frozen` reports whether this run was an evaluation
    # freeze. `evidence_network_calls` counts the answers this run had to go
    # and get: every cache miss that reached a source. It is the number the
    # reproducibility gate asserts is ZERO on a warm second run — a re-run that
    # calls out is a re-run whose evidence is not the evidence it is comparing
    # against. `evidence_frozen_misses` counts what a frozen run went without.
    evidence_cache_frozen: bool = False
    evidence_network_calls: int = 0
    # Which sources those calls went to, so a non-zero count on a warm run
    # names the lane to look at rather than sending someone through the trace.
    evidence_network_calls_by_namespace: dict[str, int] = Field(
        default_factory=dict,
    )
    evidence_frozen_misses: int = 0
    evidence_cache_hits: int = 0
    # Fix D(2) — registry matches whose registered locality contradicted the
    # record but whose NAME match was exact, so the row carries no
    # `registry-location-mismatch`. Kept as a batch number rather than a
    # per-row field because it answers a question about the batch: how often
    # is the register's address not the operating address? A rise here without
    # a rise in the flag is the normal case getting more common, not a
    # regression. See `enrichment/consistency.py`.
    registry_location_unconfirmed: int = 0
    # Records where ROR and GLEIF, queried independently, named one
    # organisation. The other outcome of the same comparison that raises
    # `source-conflict`, and a batch number for the same reason: it answers
    # "how often do the two registers corroborate each other?", which no
    # single row can answer. Raises no flag — an agreement is a finding, not
    # a triage signal. See `enrichment/consistency.py`.
    registry_agreement: int = 0
    # Domain ownership guard telemetry (utils/domain_resolver.py). The three
    # `domain_from_*` counters partition the records that kept a domain by the
    # evidence that carried it; `domain_from_serp` covers every web-derived
    # domain (name similarity or on-domain search evidence).
    domain_from_registry: int = 0
    # A candidate an INDEPENDENT system's stated official website confirmed —
    # a ROR `links[]` entry or a Wikidata `P856` claim naming the same
    # registrable domain the web path found. Separate from `domain_from_
    # registry`, which is a domain the registry SUPPLIED: this one is a domain
    # two systems agree on, and it is the only web-derived domain that reaches
    # `verified` in the provenance column.
    domain_from_witness: int = 0
    domain_from_email: int = 0
    domain_from_serp: int = 0
    # Accepted because the page served BY the candidate stated this
    # organisation's own name (the page-identity ownership condition).
    domain_from_page: int = 0
    domain_rejected_unverified: int = 0
    tier2a_population_count: int = 0
    tier2a_verification_count: int = 0
    tier2b_count: int = 0
    tier3_count: int = 0
    contact_lookup_attempted: int = 0
    contact_lookup_success: int = 0
    # Batch consensus pass (enrichment/batch_consensus.py): groups are
    # (address block, canonical name, legal form) sets holding 2+ records;
    # `consensus_conflicts` counts the groups that held two or more
    # conflicting registry identities and therefore propagated nothing.
    consensus_groups: int = 0
    consensus_records_updated: int = 0
    consensus_conflicts: int = 0
    consensus_fields_propagated: Dict[str, int] = Field(default_factory=dict)
    # Flag codes the pass withdrew because it replaced the value they
    # described. Counts codes, not records.
    consensus_flags_retracted: int = 0
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
