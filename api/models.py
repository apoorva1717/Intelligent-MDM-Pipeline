"""Pydantic v2 request/response models for the enrichment API."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class EnrichmentRecord(BaseModel):
    """A single customer master data record to enrich."""
    record_id: str = Field(..., description="DATAshaper Code: GroupCode_CustomerNumber")
    name1: Optional[str] = None
    name2: Optional[str] = None
    name3: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None
    # Legacy single-street field kept for backwards compatibility. New
    # callers should populate street1/2/3 directly. If ``street`` is
    # provided and ``street1`` is not, the orchestrator treats it as
    # street1.
    street: Optional[str] = None
    street1: Optional[str] = None
    street2: Optional[str] = None
    street3: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


class EnrichmentOptions(BaseModel):
    """Per-request processing options."""
    max_concurrency: int = Field(default=5, ge=1, le=20)
    serp_provider: Literal["serpapi", "duckduckgo"] = "serpapi"
    skip_tier: Optional[int] = Field(default=None, description="Skip a specific tier (for testing)")
    dry_run: bool = False


class EnrichmentRequest(BaseModel):
    """Top-level POST /enrich request body."""
    records: List[EnrichmentRecord] = Field(..., min_length=1)
    options: EnrichmentOptions = Field(default_factory=EnrichmentOptions)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class EnrichmentResult(BaseModel):
    """Enrichment outcome for one record."""
    record_id: str

    # Name fields
    name1_original: Optional[str] = None
    name2_original: Optional[str] = None
    name3_original: Optional[str] = None
    name1_enriched: Optional[str] = None
    name2_enriched: Optional[str] = None
    name3_enriched: Optional[str] = None
    name1_changed: bool = False
    name2_changed: bool = False
    name3_changed: bool = False

    # Contact / email (extracted or passed through)
    contact_original: Optional[str] = None
    contact_enriched: Optional[str] = None
    contact_changed: bool = False
    email_original: Optional[str] = None
    email_enriched: Optional[str] = None
    email_changed: bool = False

    # Street fields (extracted or passed through)
    street1_original: Optional[str] = None
    street1_enriched: Optional[str] = None
    street1_changed: bool = False
    street2_original: Optional[str] = None
    street2_enriched: Optional[str] = None
    street2_changed: bool = False
    street3_original: Optional[str] = None
    street3_enriched: Optional[str] = None
    street3_changed: bool = False

    # Classification & provenance
    record_type: Literal["research_institution", "company", "unknown"] = "unknown"
    tier_used: Literal[1, 2, 3] = 1
    tier2_mode: Optional[Literal["2A_population", "2A_verification", "2B"]] = None
    confidence: Literal["high", "medium", "low", "none"] = "none"
    source: Literal[
        "ROR", "ROR+child", "contact_lookup_found",
        "contact_lookup_corrected", "dept_search", "LLM",
        "llm_canonical", "SERP+LLM", "pattern_match",
        "web_search", "passthrough", "none",
    ] = "none"
    ror_id: Optional[str] = None
    source_url: Optional[str] = None
    domain: Optional[str] = None
    contact_used: bool = False
    name2_match_result: Literal["exact", "partial", "no_match", "not_applicable", "unknown"] = "not_applicable"

    # Which use cases (0-9) fired for this record
    use_cases_triggered: List[int] = Field(default_factory=list)

    flag_for_review: bool = False
    flag_reason: Optional[str] = None
    enrichment_status: Literal["enriched", "verified", "unresolved", "failed"] = "failed"
    duration_ms: int = 0
    error: Optional[str] = None


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
