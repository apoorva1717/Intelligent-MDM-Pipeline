"""Pydantic v2 request/response models for the dedup adjudicator endpoint.

Contract is JSON in / JSON out. The orchestrator (ADF/DATAshaper) handles
file<->JSON; this endpoint never touches files.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class DedupRow(BaseModel):
    """A single address-gated candidate row.

    Every row in a request already shares the same physical address as the
    other rows in its block (the address gates ran upstream). The adjudicator
    only decides, from the names, which rows refer to the same entity.
    """

    # Accept snake_case aliases too, so the caller can use either casing.
    model_config = ConfigDict(populate_by_name=True)

    row_id: str = Field(..., description="Caller's stable key, echoed back verbatim.")
    block_id: Optional[str] = Field(
        default=None,
        description=(
            "Address block. When null, derived from the normalized "
            "(country, postal_code, street, house_no)."
        ),
    )
    name1: Optional[str] = Field(default=None, description="Institution / company.")
    name2: Optional[str] = Field(default=None, description="Department / sub-unit (may be empty).")
    # The rest of the SAP name block. A record's unit can sit in any of these
    # — Name 3 as readily as Name 2 — so the signature key reads all of them.
    # Defaulted last so existing positional/keyword construction is unchanged.
    name3: Optional[str] = Field(default=None, description="Further sub-unit (may be empty).")
    name4: Optional[str] = Field(default=None, description="Further sub-unit (may be empty).")
    name5: Optional[str] = Field(default=None, description="Further sub-unit (may be empty).")
    street: Optional[str] = None
    house_no: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    ror_id: Optional[str] = Field(default=None, description="ROR id from Phase 1, if resolved (institution hint).")
    lei_id: Optional[str] = Field(default=None, description="GLEIF LEI from Phase 1, if resolved (company legal-entity hint).")
    enriched_name: Optional[str] = Field(default=None, description="Phase 1 official name, if resolved.")
    # Phase 1 columns the file route used to drop on the floor (v2, C.4). All
    # optional with a None default, so every existing construction — the JSON
    # route, the tests, the ADF payload — is unchanged by their arrival.
    operating_name: Optional[str] = Field(
        default=None, description="Trading / operating name from Phase 1, if any.")
    suggested_name: Optional[str] = Field(
        default=None, description="Phase 1's suggested canonical name, if any.")
    record_type: Optional[str] = Field(
        default=None, description="research_institution | company | unknown.")
    ror_id_provenance: Optional[str] = Field(
        default=None, description="How the ROR id was reached (ror:verified, llm:provisional, …).")
    lei_id_provenance: Optional[str] = Field(
        default=None, description="How the LEI was reached.")
    # Building is a HINT and nothing else: it is shown to the model and never
    # reaches blocking or the signature key. Two records in one building are
    # not thereby one entity, and two in different buildings at one street
    # address are not thereby two — the delivery point is the address.
    building: Optional[str] = Field(
        default=None, description="Building, passed to the model as a hint only.")


class DedupRequest(BaseModel):
    """Top-level POST /api/dedup/cluster-block request body.

    May carry rows from one or more address blocks; each block is processed
    independently.
    """

    rows: List[DedupRow] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DedupResultRow(BaseModel):
    """Cluster assignment for one input row."""

    row_id: str
    block_id: str
    # Stable content-hash cluster id ("c_" + 12 hex of sha256 over the sorted
    # member row_ids); null for rows that are not in a duplicate cluster. Same
    # membership => same id across runs; a membership change => a new id.
    cluster_id: Optional[str] = None
    routing: Literal["cluster", "unique", "manual_review"]
    llm_flag: bool
    signature_id: str
    # Merge confidence: set only for a genuine LLM merge (>=2 distinct
    # signatures) or an uncertain row; null for a unique row AND for a pure
    # identical-signature collapse (deterministic, no merge decision).
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    model: str
    model_version: str
    prompt_version: str


class DedupSummary(BaseModel):
    """Aggregate statistics for the whole request."""

    blocks: int = 0
    rows_in: int = 0
    distinct_signatures: int = 0
    clusters: int = 0
    rows_clustered: int = 0
    rows_unique: int = 0
    rows_manual_review: int = 0
    llm_calls: int = 0
    errors: int = 0
    # Residue candidate-nomination telemetry.
    candidates_generated: int = 0
    rejected_with_reasoning: int = 0
    candidate_cap_exceeded_blocks: int = 0


class DedupResponse(BaseModel):
    """Top-level POST /api/dedup/cluster-block response body."""

    rows: List[DedupResultRow]
    summary: DedupSummary
