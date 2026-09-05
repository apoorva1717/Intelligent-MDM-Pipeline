"""Deterministic scoring + golden-record election (Phase 2 "Pass 3").

Separate from the LLM adjudicator on purpose: clustering and election have
different inputs, cadences, and cost profiles. Election is pure arithmetic
over an editable weights table (``dedup/weights.json``), so it can be re-run
on retuned weights without paying for LLM adjudication again. No LLM, no
network — ever.

The real CRM extract is ~half empty and dirty. Scoring is therefore
permissive: a missing or unrecognised value scores 0 (with a warning when the
value was present but unrecognised) and NEVER raises or fails the batch. The
one hard error is a duplicated row_id in a single request — that means a
broken upstream join, and scoring it would double-elect.

ZFIS is deliberately absent: it is a separate upstream gate that runs before
enrichment; those records never reach dedup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from dedup.cluster_key import CLUSTER_ID_PREFIX, cluster_hash

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).parent / "weights.json"

# A merge whose adjudication confidence is below this is demoted to
# manual_review at election time. Overridable via env / config
# (CONFIDENCE_MERGE_THRESHOLD); gating here never re-runs the LLM.
DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95

# Raw cell/JSON value for numeric-ish fields. Typed permissively so one dirty
# value in a real extract can never 422 the whole request; coercion (and the
# warning for unrecognised values) happens in the scorer, not the model.
Scalar = Union[int, float, str, None]

# score_breakdown key -> flat output column header (the exact name the file
# endpoint writes). Single source of truth shared by the JSON model (which
# flattens score_breakdown into these columns) and dedup.scoring_xlsx (which
# writes them). Keep in sync with nothing else — both consumers import THIS.
SCORE_BREAKDOWN_COLUMNS: Dict[str, str] = {
    "sales_order_last_used": "score_SalesOrderLastUsed",
    "sales_order_count": "score_SalesOrderCount",
    "sales_order_partner_last_used": "score_SalesOrderPartnerLastUsed",
    "sales_order_partner_count": "score_SalesOrderPartnerCount",
    "equipment_count": "score_EquipmentCount",
    "sleeping_customer": "score_SleepingCustomer",
    "customer_status": "score_CustomerStatus",
    "account_group": "score_AccountGroup",
    "company_code_count": "score_CompanyCodeCount",
    "combined_presence_bonus": "score_CombinedPresence",
    "salesforce_instance_count": "score_SalesforceInstances",
}

# The 8 flat Salesforce id fields, in slot order (sf1 = Biosystems, sf2 = AXS,
# sf3..sf8). Each is its own scalar column — no list/object on the wire.
SF_FIELDS: Tuple[str, ...] = ("sf1", "sf2", "sf3", "sf4", "sf5", "sf6", "sf7", "sf8")


class DuplicateRowIdError(ValueError):
    """A row_id appeared more than once in one request (broken upstream join)."""

    def __init__(self, row_ids: List[str]):
        self.row_ids = row_ids
        super().__init__(f"Duplicate row_id(s): {', '.join(row_ids)}")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ScoringRow(BaseModel):
    """One SAP customer / business partner to score.

    Every scoring field is optional and defaults to None. ``sleeping_band``
    and ``customer_status`` are deliberately plain ``str`` (NOT Literal): one
    stray value in a real extract must not 422 the request — the scorer
    normalises them and maps unknowns to 0 points plus a warning.
    """

    # populate_by_name lets every field be supplied EITHER by its exact file
    # column header (the alias, e.g. "Customer") OR by its snake_case name
    # ("row_id"), so existing JSON callers keep working while the wire format
    # matches /api/dedup/score/file exactly.
    model_config = ConfigDict(populate_by_name=True)

    row_id: str = Field(
        ..., alias="Customer", description="SAP Customer / BP number, join key."
    )
    cluster_id: Optional[str] = Field(
        default=None, alias="Cluster ID",
        description="Stable cluster key from the adjudicator. None => no cluster.",
    )
    confidence: Optional[float] = Field(
        default=None, alias="Confidence",
        description=(
            "Adjudication merge confidence from clustering (0-1). Below the "
            "configured threshold the merge is demoted to manual_review. None "
            "(a deterministic identical-collapse, or a unique row) never gates."
        ),
    )
    routing: Optional[str] = Field(
        default=None, alias="Routing",
        description=(
            "Incoming clustering routing: cluster | unique | manual_review. A "
            "manual_review from clustering is INHERITED by election and can "
            "never be upgraded to proposed — uncertainty only ever propagates."
        ),
    )
    reasoning: Optional[str] = Field(
        default=None, alias="Reasoning",
        description=(
            "Adjudication reasoning from clustering. Surfaced in the issue list "
            "to flag a verdict_contradiction (reasoning that disowns a merge)."
        ),
    )
    last_order_year: Scalar = Field(default=None, alias="Sales_Order_Last_Used")
    # G1 (Bernd's year-priority rule): this is the number of sales orders WITHIN
    # the record's last-used year, NOT a lifetime total. It only differentiates
    # records that share the most-recent year in a cluster (see score_row /
    # elect_golden_records). The legacy name ``order_count`` is still accepted on
    # input (deprecated — remove once callers migrate).
    orders_in_last_used_year: Scalar = Field(
        default=None,
        validation_alias=AliasChoices(
            "Sales_Order_Total_Count", "orders_in_last_used_year", "order_count"
        ),
        serialization_alias="Sales_Order_Total_Count",
    )
    partner_last_order_year: Scalar = Field(
        default=None, alias="Sales_Order_Partner_Last_Used"
    )
    # G1: within-year partner order count (mirror of orders_in_last_used_year).
    # Legacy name ``partner_order_count`` accepted on input (deprecated).
    partner_orders_in_last_used_year: Scalar = Field(
        default=None,
        validation_alias=AliasChoices(
            "Sales_Order_Partner_Total_Count",
            "partner_orders_in_last_used_year",
            "partner_order_count",
        ),
        serialization_alias="Sales_Order_Partner_Total_Count",
    )
    equipment_count: Scalar = Field(default=None, alias="Equipment_Total_Count")
    # expected "No" | "3-4" | ">5"
    sleeping_band: Optional[str] = Field(default=None, alias="SleepingCustomer")
    # expected "active" | "blocked"
    customer_status: Optional[str] = Field(default=None, alias="CustomerStatus")
    account_group: Optional[str] = Field(default=None, alias="Account group")
    company_code_consolidated: Optional[str] = Field(
        default=None, alias="Company_Code_Consolidated"  # "," or ";"-delimited
    )
    sales_org_consolidated: Optional[str] = Field(
        default=None, alias="Sales_Org_Consolidated"  # "," or ";"-delimited
    )
    # Salesforce ids as 8 flat scalar columns (no list/object on the wire).
    # Only non-empty ids count toward Salesforce_Instance_Count.
    sf1: Optional[str] = Field(default=None, description="Salesforce id slot 1 (Biosystems).")
    sf2: Optional[str] = Field(default=None, description="Salesforce id slot 2 (AXS).")
    sf3: Optional[str] = None
    sf4: Optional[str] = None
    sf5: Optional[str] = None
    sf6: Optional[str] = None
    sf7: Optional[str] = None
    sf8: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _unpack_salesforce_ids(cls, data):
        """Backward-compat: accept a legacy ``salesforce_ids`` list (still used by
        the file endpoint and older callers) and spread it across sf1..sf8. The
        canonical shape is the flat sf1..sf8 columns; explicit sf* keys win."""
        if (
            isinstance(data, dict)
            and "salesforce_ids" in data
            and not any(f in data for f in SF_FIELDS)
        ):
            ids = data.get("salesforce_ids") or []
            data = dict(data)
            for i, field in enumerate(SF_FIELDS):
                data[field] = ids[i] if i < len(ids) else None
        return data

    @field_validator(
        "cluster_id",
        "routing",
        "reasoning",
        "sleeping_band",
        "customer_status",
        "account_group",
        "company_code_consolidated",
        "sales_org_consolidated",
        "sf1", "sf2", "sf3", "sf4", "sf5", "sf6", "sf7", "sf8",
        mode="before",
    )
    @classmethod
    def _stringify(cls, v):
        """Excel cells arrive as native types (a lone company code as int,
        a sleeping band as whatever); stringify instead of rejecting."""
        if v is None or isinstance(v, str):
            return v
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _floatify_confidence(cls, v):
        """A blank/dirty Confidence cell must never 422 the request — coerce to
        float or fall back to None (which never gates)."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class ScoringRequest(BaseModel):
    """Top-level POST /api/dedup/score request body.

    No min_length: an empty rows list is a valid request and returns an
    empty result with a zeroed summary.
    """

    rows: List[ScoringRow] = Field(default_factory=list)
    weights: Optional[dict] = Field(
        default=None,
        description=(
            "Optional weights override (same structure as dedup/weights.json: "
            "criterion -> {band label: points}). Applied wholesale when valid; "
            "a malformed override is ignored wholesale with a warning in "
            "summary.warnings — identical semantics to the /api/dedup/score/file "
            "Weights sheet. Omit to use dedup/weights.json."
        ),
    )


class ScoringResultRow(BaseModel):
    """Score + election outcome for one input row.

    Table invariant: a unique row and an APPROVED winner are golden=true and
    self-reference; a duplicate is golden=false and points at its survivor.
    A manual_review row leaves is_golden_record/golden_record_id EMPTY — its
    computed winner lives in ``proposed_golden_id`` so nobody acting on
    is_golden_record alone can touch an unreviewed row.

    PHASE 3 CONTRACT: consume ONLY rows with ``approval_status == "approved"``
    or ``election_status == "unique"``. Everything else is a proposal awaiting
    human sign-off (see POST /api/dedup/approve).
    """

    # Every serialized key is the EXACT file column header (see the aliases),
    # so the JSON /api/dedup/score output matches /api/dedup/score/file column
    # for column. populate_by_name keeps snake_case construction/validation
    # working for internal callers and round-tripped /approve payloads.
    model_config = ConfigDict(populate_by_name=True)

    row_id: str = Field(alias="Customer")
    cluster_id: Optional[str] = Field(default=None, alias="Cluster ID")
    score: int = Field(alias="score_final")
    # Raw derived counts (from the consolidated fields / SF id slots), mirroring
    # the Company_Code_Count / Sales_Org_Count / Salesforce_Instance_Count
    # columns the file endpoint writes. NOTE: these are the RAW counts, distinct
    # from the *points* in the score_* columns. Source: ``derived_counts``.
    company_code_count: int = Field(default=0, alias="Company_Code_Count")
    sales_org_count: int = Field(default=0, alias="Sales_Org_Count")
    salesforce_instance_count: int = Field(default=0, alias="Salesforce_Instance_Count")
    is_golden_record: bool
    golden_record_id: Optional[str] = None
    # The computed winner for this row's cluster — always present for a cluster
    # member (proposed or manual_review), None for a unique row. On a
    # manual_review row this is the ONLY place the proposal survives.
    proposed_golden_id: Optional[str] = None
    election_status: Literal["proposed", "manual_review", "unique"]
    # Human approval lifecycle. The pipeline only ever writes "proposed" (or
    # null for unique — nothing to approve); approved/rejected are set later by
    # POST /api/dedup/approve.
    approval_status: Optional[Literal["proposed", "approved", "rejected"]] = None
    # 12-hex fingerprint of the weights the row was scored with — detects score
    # drift if weights were retuned between a proposal and its approval.
    scored_with_weights_version: Optional[str] = None
    # Internal only: the per-criterion points. Flattened into the score_*
    # columns below for output (so excluded here); still read by the file
    # writeback and internal callers. Optional so a /score output (which has the
    # flat score_* columns, not this dict) round-trips back into /approve.
    score_breakdown: Dict[str, int] = Field(default_factory=dict, exclude=True)
    # Not a file column — kept internal for summary.rows_with_warnings only.
    warnings: List[str] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _fold_score_columns(cls, data):
        """Reassemble score_breakdown from the flat score_* columns on input, so
        a /score output round-trips losslessly back into /approve."""
        if isinstance(data, dict) and "score_breakdown" not in data:
            present = {
                key: data[col]
                for key, col in SCORE_BREAKDOWN_COLUMNS.items()
                if col in data
            }
            if present:
                data = dict(data)
                data["score_breakdown"] = {k: int(v) for k, v in present.items()}
        return data

    # Flattened per-criterion points — one computed field per file score column,
    # read from score_breakdown so the serialized output is exactly the file's
    # score_* columns. Serialized under the alias (FastAPI dumps by_alias).
    @computed_field(alias="score_SalesOrderLastUsed")
    @property
    def _pts_sales_order_last_used(self) -> int:
        return int(self.score_breakdown.get("sales_order_last_used", 0))

    @computed_field(alias="score_SalesOrderCount")
    @property
    def _pts_sales_order_count(self) -> int:
        return int(self.score_breakdown.get("sales_order_count", 0))

    @computed_field(alias="score_SalesOrderPartnerLastUsed")
    @property
    def _pts_sales_order_partner_last_used(self) -> int:
        return int(self.score_breakdown.get("sales_order_partner_last_used", 0))

    @computed_field(alias="score_SalesOrderPartnerCount")
    @property
    def _pts_sales_order_partner_count(self) -> int:
        return int(self.score_breakdown.get("sales_order_partner_count", 0))

    @computed_field(alias="score_EquipmentCount")
    @property
    def _pts_equipment_count(self) -> int:
        return int(self.score_breakdown.get("equipment_count", 0))

    @computed_field(alias="score_SleepingCustomer")
    @property
    def _pts_sleeping_customer(self) -> int:
        return int(self.score_breakdown.get("sleeping_customer", 0))

    @computed_field(alias="score_CustomerStatus")
    @property
    def _pts_customer_status(self) -> int:
        return int(self.score_breakdown.get("customer_status", 0))

    @computed_field(alias="score_AccountGroup")
    @property
    def _pts_account_group(self) -> int:
        return int(self.score_breakdown.get("account_group", 0))

    @computed_field(alias="score_CompanyCodeCount")
    @property
    def _pts_company_code_count(self) -> int:
        return int(self.score_breakdown.get("company_code_count", 0))

    @computed_field(alias="score_CombinedPresence")
    @property
    def _pts_combined_presence_bonus(self) -> int:
        return int(self.score_breakdown.get("combined_presence_bonus", 0))

    @computed_field(alias="score_SalesforceInstances")
    @property
    def _pts_salesforce_instance_count(self) -> int:
        return int(self.score_breakdown.get("salesforce_instance_count", 0))


class ScoringSummary(BaseModel):
    """Aggregate statistics for the whole request."""

    rows_in: int = 0
    clusters: int = 0
    rows_elected: int = 0
    rows_duplicates: int = 0
    rows_unique: int = 0
    rows_manual_review: int = 0
    all_blocked_clusters: int = 0
    rows_with_warnings: int = 0
    errors: int = 0
    warnings: List[str] = Field(default_factory=list)


# Recognised issue types (thesis "potential inconsistency" list). Cluster-level
# types key on the proposed winner's row_id. missing_building_inconsistency is
# reserved for the upstream building differentiator (Phase 1); it is a declared
# type here but not emitted from election (no building signal at this stage).
ISSUE_TYPES = (
    "low_confidence_merge",
    "verdict_contradiction",
    "missing_building_inconsistency",
    "all_blocked_cluster",
    "tiebreak_decided",
    "empty_scoring_payload",
    "count_suppressed_by_recency",
    "candidate_cap_exceeded",
)

# Marker written by the adjudicator's residue pass when a block exceeds the
# per-block candidate cap and is routed to manual_review.
_CANDIDATE_CAP_MARKER = "candidate_cap_exceeded"

# Marker written by score_row when the G1 recency gate zeroes a count component.
_COUNT_SUPPRESSED_MARKER = "count suppressed (G1)"

_CONTRADICTION_MARKERS = (
    "should not be merged", "should not merge", "must not be merged",
    "not be merged", "do not merge", "should be split", "must be split",
)


class DedupIssue(BaseModel):
    """One 'potential inconsistency' for the reviewer feedback loop."""

    row_id: Optional[str] = None
    cluster_id: Optional[str] = None
    issue_type: str
    detail: str


class ScoringResponse(BaseModel):
    """Top-level POST /api/dedup/score response body."""

    rows: List[ScoringResultRow]
    summary: ScoringSummary
    issues: List[DedupIssue] = Field(default_factory=list)


def _reasoning_is_contradiction(text: Optional[str]) -> bool:
    """A reasoning string that disowns a merge (an explicit non-merge assertion,
    or the deterministic identity-split marker) — the Q1 verdict-contradiction
    signal, surfaced downstream from the persisted Reasoning column."""
    if not text:
        return False
    low = text.casefold()
    return low.startswith("split:") or any(m in low for m in _CONTRADICTION_MARKERS)


def detect_issues(
    rows: List[ScoringRow],
    results: List[ScoringResultRow],
    *,
    confidence_threshold: Optional[float] = None,
) -> List[DedupIssue]:
    """Derive the potential-inconsistency list from scored rows + results.

    Row-level: verdict_contradiction, count_suppressed_by_recency. Cluster-level
    (keyed on the proposed winner): low_confidence_merge, all_blocked_cluster,
    tiebreak_decided, empty_scoring_payload. Deterministic and offline.
    """
    threshold = _resolve_confidence_threshold(confidence_threshold)
    by_id = {r.row_id: r for r in rows}
    issues: List[DedupIssue] = []

    # Row-level — a persisted reasoning that argues against its own merge, or a
    # block routed to manual_review because it blew the candidate cap.
    seen_cap_clusters: set = set()
    for row in rows:
        if _reasoning_is_contradiction(row.reasoning):
            issues.append(DedupIssue(
                row_id=row.row_id, cluster_id=row.cluster_id,
                issue_type="verdict_contradiction",
                detail=row.reasoning.strip()[:200],  # type: ignore[union-attr]
            ))
        if row.reasoning and _CANDIDATE_CAP_MARKER in row.reasoning:
            # One issue per capped block (keyed by cluster_id, or row when none).
            key = row.cluster_id or row.row_id
            if key not in seen_cap_clusters:
                seen_cap_clusters.add(key)
                issues.append(DedupIssue(
                    row_id=row.row_id, cluster_id=row.cluster_id,
                    issue_type="candidate_cap_exceeded",
                    detail=row.reasoning.strip()[:200],
                ))

    # Row-level — G1 recency gate zeroed a count component (Bernd's rule firing).
    for res in results:
        for warning in res.warnings:
            if _COUNT_SUPPRESSED_MARKER in warning:
                issues.append(DedupIssue(
                    row_id=res.row_id, cluster_id=res.cluster_id,
                    issue_type="count_suppressed_by_recency",
                    detail=warning,
                ))

    clusters: Dict[str, List[ScoringResultRow]] = {}
    for res in results:
        if res.cluster_id is not None:
            clusters.setdefault(res.cluster_id, []).append(res)

    for cid, members in clusters.items():
        winner = next(
            (m.proposed_golden_id or m.golden_record_id for m in members), None
        )
        inputs = [by_id[m.row_id] for m in members if m.row_id in by_id]

        confs = [im.confidence for im in inputs if im.confidence is not None]
        if confs and min(confs) < threshold:
            issues.append(DedupIssue(
                row_id=winner, cluster_id=cid, issue_type="low_confidence_merge",
                detail=f"min merge confidence {min(confs):.2f} < {threshold:.2f}",
            ))
        if inputs and all(_normalized_status(im) == "blocked" for im in inputs):
            issues.append(DedupIssue(
                row_id=winner, cluster_id=cid, issue_type="all_blocked_cluster",
                detail="every member is blocked",
            ))
        scores = [m.score for m in members]
        top = max(scores)
        if len(scores) >= 2 and scores.count(top) >= 2:
            issues.append(DedupIssue(
                row_id=winner, cluster_id=cid, issue_type="tiebreak_decided",
                detail=f"top score {top} shared by {scores.count(top)} members",
            ))
        if members and all(m.score == 0 for m in members):
            issues.append(DedupIssue(
                row_id=winner, cluster_id=cid, issue_type="empty_scoring_payload",
                detail="all members scored 0 — winner decided by tie-break only",
            ))
    return issues


# ---------------------------------------------------------------------------
# Approval lifecycle (human sign-off on a proposed/manual_review cluster)
# ---------------------------------------------------------------------------

class ClusterNotFoundError(ValueError):
    """The cluster_id to approve/reject is absent from the submitted rows."""

    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
        super().__init__(f"cluster_id not found in payload: {cluster_id!r}")


class ApprovalRequest(BaseModel):
    """POST /api/dedup/approve body: a human's decision on one cluster.

    Stateless — the caller submits the scored rows, the decision is applied to
    the named cluster, and the updated rows are echoed back. Persistence is out
    of scope (a durable approval store is a future step).
    """

    cluster_id: str
    decision: Literal["approved", "rejected"]
    approver: str = Field(..., min_length=1, description="Who approved/rejected.")
    rows: List[ScoringResultRow] = Field(..., min_length=1)


class ApprovalResponse(BaseModel):
    """POST /api/dedup/approve response: the decision and the echoed rows."""

    cluster_id: str
    decision: Literal["approved", "rejected"]
    approver: str
    updated_row_ids: List[str]
    rows: List[ScoringResultRow]


def apply_approval(
    rows: List[ScoringResultRow],
    cluster_id: str,
    decision: str,
) -> Tuple[List[ScoringResultRow], List[str]]:
    """Apply an approve/reject decision to every row in ``cluster_id``.

    Returns (all rows with the decision applied, updated row_ids). On
    "approved" the proposed winner is promoted into the golden fields so Phase 3
    can act uniformly (a manual_review row's golden was left blank until now).
    On "rejected" the golden fields are left as-is. Raises ClusterNotFoundError
    when no row carries the cluster_id. Never mutates the inputs.
    """
    if not any(r.cluster_id == cluster_id for r in rows):
        raise ClusterNotFoundError(cluster_id)

    out: List[ScoringResultRow] = []
    updated: List[str] = []
    for r in rows:
        if r.cluster_id != cluster_id:
            out.append(r)
            continue
        r2 = r.model_copy()
        r2.approval_status = decision  # type: ignore[assignment]
        if decision == "approved" and r2.proposed_golden_id is not None:
            r2.is_golden_record = r2.row_id == r2.proposed_golden_id
            r2.golden_record_id = r2.proposed_golden_id
        out.append(r2)
        updated.append(r2.row_id)
    return out, updated


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

def weights_version(weights: dict) -> str:
    """Stable 12-hex fingerprint of the weights table (sha256 of the canonical
    JSON). Written onto every scored row so a proposal and its later approval
    can be checked for score drift when weights were retuned in between."""
    canonical = json.dumps(weights, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_weights(path: Union[str, Path, None] = None) -> dict:
    """Load the scoring weights table (criterion -> {band label: points})."""
    with open(path or WEIGHTS_PATH, encoding="utf-8") as f:
        weights = json.load(f)
    # Metadata keys (e.g. "_comment") are not criteria.
    return {k: v for k, v in weights.items() if not k.startswith("_")}


def coerce_weights(
    candidate: dict, expected: dict, *, source: str = "Weights"
) -> Tuple[Optional[dict], Optional[str]]:
    """Validate a candidate weights mapping against ``expected``'s structure.

    The single source of truth for the weights-override rule shared by BOTH
    the JSON ``/api/dedup/score`` body and the ``/api/dedup/score/file`` Weights
    sheet: all-or-nothing. Every (criterion, band) pair in ``expected``
    (dedup/weights.json) must be present with a numeric Points value, else the
    WHOLE candidate is rejected — a half-applied retune is worse than none.
    Returns ``(weights, None)`` on success or ``(None, reason)`` on rejection;
    ``source`` names the candidate in the reason string.
    """
    parsed: dict = {}
    for criterion, bands in expected.items():
        crit_in = candidate.get(criterion)
        crit_map = crit_in if isinstance(crit_in, dict) else {}
        parsed[criterion] = {}
        for band in bands:
            if band not in crit_map:
                return None, (
                    f"{source} ignored: missing (criterion, band) pair "
                    f"({criterion!r}, {band!r}); using dedup/weights.json"
                )
            points = crit_map[band]
            if isinstance(points, bool) or not isinstance(points, (int, float)):
                try:
                    points = float(str(points).strip())
                except (TypeError, ValueError):
                    return None, (
                        f"{source} ignored: non-numeric Points for "
                        f"({criterion!r}, {band!r}); using dedup/weights.json"
                    )
            parsed[criterion][band] = int(points)
    return parsed, None


# ---------------------------------------------------------------------------
# Coercion helpers — permissive, never guess, never raise
# ---------------------------------------------------------------------------

def _coerce_int(value: Scalar, field_name: str, warnings: List[str]) -> Optional[int]:
    """Coerce a raw cell/JSON value to int.

    Excel numerics arrive as floats (2026.0). Blank or non-numeric values
    become None (0 points); an actually-present unparseable value also gets
    a warning. Never raises.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; a bool here is dirt
        warnings.append(f"{field_name} {value!r} not numeric -> 0")
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        warnings.append(f"{field_name} {text!r} not numeric -> 0")
        return None


def _clean_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def split_consolidated(value: Optional[str]) -> List[str]:
    """Non-empty parts of a ","- or ";"-delimited cell ("1003;1017;" -> 2).

    BOTH delimiters are accepted. The preprocess stage that produces these
    cells joins on ``dedup.consolidate.CONSOLIDATED_DELIMITER`` (","), while
    every historic extract uses ";" — a splitter that knew only one of them
    would count a whole comma-joined list as a single value and silently
    flatten score_CompanyCodeCount and score_CombinedPresence across the run.
    Widening is purely additive: a semicolon-delimited cell keeps its count.
    """
    if value is None:
        return []
    return [part.strip() for part in re.split(r"[,;]", str(value)) if part.strip()]


def derived_counts(row: ScoringRow) -> Tuple[int, int, int]:
    """(company_code_count, sales_org_count, salesforce_instance_count).

    Always derived from the consolidated fields / id slots, never read from
    the file — single source of truth.
    """
    company_codes = len(split_consolidated(row.company_code_consolidated))
    sales_orgs = len(split_consolidated(row.sales_org_consolidated))
    sf_instances = sum(
        1
        for field in SF_FIELDS
        if (sid := getattr(row, field)) is not None and str(sid).strip()
    )
    return company_codes, sales_orgs, sf_instances


# ---------------------------------------------------------------------------
# Band matching — generic so weights.json stays a pure data retune
# ---------------------------------------------------------------------------

def _match_numeric_band(value: Optional[int], bands: Dict[str, int]) -> int:
    """Points for a numeric value against band labels.

    Labels: "a-b" inclusive range, ">n" strictly greater, "n+" greater-or-
    equal, bare number exact (covers the year tiers). No match (including
    None) scores 0 — the table's explicit "else" band.
    """
    if value is None:
        return 0
    for label, points in bands.items():
        label = label.strip()
        try:
            if label.startswith(">"):
                if value > int(label[1:]):
                    return int(points)
            elif label.endswith("+"):
                if value >= int(label[:-1]):
                    return int(points)
            elif "-" in label.lstrip("-"):
                low, high = label.rsplit("-", 1)
                if int(low) <= value <= int(high):
                    return int(points)
            elif value == int(label):
                return int(points)
        except ValueError:
            logger.warning("Unparseable weights band label %r — skipped", label)
    return 0


def _match_label_band(
    value: Optional[str],
    bands: Dict[str, int],
    field_name: str,
    warnings: List[str],
    *,
    warn_unknown: bool,
) -> int:
    """Points for an enum-ish value (case-insensitive, "X/Y" = either literal).

    None (absent) is 0 with no warning — absence is not activity, and half
    the extract is empty. A present-but-unrecognised value is 0 with a
    warning when ``warn_unknown`` (fields with an explicit anything-else band,
    e.g. account_group, stay silent).
    """
    if value is None:
        return 0
    needle = value.strip().casefold()
    if not needle:
        return 0
    for label, points in bands.items():
        alternatives = [alt.strip().casefold() for alt in label.split("/")]
        if needle in alternatives:
            return int(points)
    if warn_unknown:
        warnings.append(f"{field_name} {value.strip()!r} unrecognized -> 0")
    return 0


def _single_band_value(bands: Dict[str, int]) -> int:
    """The points of a single-band criterion (bonus / per-instance)."""
    return int(next(iter(bands.values()), 0))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _award_count(row_year: Optional[int], cluster_max_year: Optional[int]) -> bool:
    """G1 recency-dominance gate for a sales-order count component.

    Bernd's rule: the count is always "in relation to the year" — it only
    differentiates records sharing the most-recent year, and "does not define
    what is the golden record, it just adds something".

    - A row with no year (None) never receives count points.
    - cluster_max_year is None means context-free scoring (a singleton / unique
      row is trivially its own max) → award (given the row has a year).
    - In a cluster, award only when the row's year equals the cluster maximum.
      (If every member's year is None the maximum is None, but the first guard
      already denies all of them — nobody receives count points.)
    """
    if row_year is None:
        return False
    if cluster_max_year is None:
        return True
    return row_year == cluster_max_year


def score_row(
    row: ScoringRow,
    weights: dict,
    cluster_max_year: Optional[int] = None,
    cluster_max_partner_year: Optional[int] = None,
) -> Tuple[Dict[str, int], List[str]]:
    """Per-criterion points + warnings for one row.

    The breakdown always carries every criterion key (0 where nothing matched)
    so the audit trail and the file writeback are column-stable.

    G1: the two sales-order COUNT criteria are cluster-context-dependent. Count
    points are awarded only when the row's last-used year is the most recent in
    its cluster (see ``_award_count``); otherwise the count component scores 0
    and a suppression warning is emitted. Pass ``cluster_max_year`` /
    ``cluster_max_partner_year`` = None for context-free scoring (singletons /
    unique rows, where the row is trivially its own maximum). Every other
    criterion is unconditional.
    """
    warnings: List[str] = []
    breakdown: Dict[str, int] = {}

    last_year = _coerce_int(row.last_order_year, "last_order_year", warnings)
    order_count = _coerce_int(
        row.orders_in_last_used_year, "orders_in_last_used_year", warnings
    )
    partner_year = _coerce_int(
        row.partner_last_order_year, "partner_last_order_year", warnings
    )
    partner_count = _coerce_int(
        row.partner_orders_in_last_used_year,
        "partner_orders_in_last_used_year", warnings,
    )
    equipment = _coerce_int(row.equipment_count, "equipment_count", warnings)
    company_codes, sales_orgs, sf_instances = derived_counts(row)

    breakdown["sales_order_last_used"] = _match_numeric_band(
        last_year, weights["sales_order_last_used"]
    )
    # G1: count only "adds something" when this row owns the cluster's most
    # recent year — otherwise an older, higher-volume record could out-score a
    # more recent one, which Bernd said must never happen.
    count_pts = _match_numeric_band(order_count, weights["sales_order_count"])
    if _award_count(last_year, cluster_max_year):
        breakdown["sales_order_count"] = count_pts
    else:
        breakdown["sales_order_count"] = 0
        # Only flag GENUINE recency losses: points that would have scored (>0),
        # suppressed because a strictly more recent record exists in the cluster
        # (cluster_max_year not None). A context-free suppression — a lone
        # year-None row with no cluster competitor — is not evidence of "volume
        # losing to recency" and would only be noise.
        if count_pts > 0 and cluster_max_year is not None:
            warnings.append(
                f"order count suppressed (G1): last-used year {last_year} is not "
                f"the cluster's most recent ({cluster_max_year})"
            )
    breakdown["sales_order_partner_last_used"] = _match_numeric_band(
        partner_year, weights["sales_order_partner_last_used"]
    )
    # UNCONFIRMED: partner count tiers mirror sales order count. CONFIRM w/ Bernd.
    # G1: mirror the recency-dominance gate on the partner pair.
    partner_count_pts = _match_numeric_band(
        partner_count, weights["sales_order_partner_count"]
    )
    if _award_count(partner_year, cluster_max_partner_year):
        breakdown["sales_order_partner_count"] = partner_count_pts
    else:
        breakdown["sales_order_partner_count"] = 0
        # Same genuine-loss guard as the sales pair: skip context-free (no
        # recency competitor) suppressions.
        if partner_count_pts > 0 and cluster_max_partner_year is not None:
            warnings.append(
                f"partner order count suppressed (G1): partner last-used year "
                f"{partner_year} is not the cluster's most recent "
                f"({cluster_max_partner_year})"
            )
    breakdown["equipment_count"] = _match_numeric_band(
        equipment, weights["equipment_count"]
    )
    breakdown["sleeping_customer"] = _match_label_band(
        _clean_str(row.sleeping_band), weights["sleeping_customer"],
        "sleeping_band", warnings, warn_unknown=True,
    )
    # "blocked" scores 0 but stays ELIGIBLE to win — a differentiator, not an
    # eligibility exclusion. Absent status is never defaulted to "active".
    breakdown["customer_status"] = _match_label_band(
        _clean_str(row.customer_status), weights["customer_status"],
        "customer_status", warnings, warn_unknown=True,
    )
    # account_group has an explicit anything-else=0 band (DBRU/Dios parked),
    # so unknown values score 0 silently.
    breakdown["account_group"] = _match_label_band(
        _clean_str(row.account_group), weights["account_group"],
        "account_group", warnings, warn_unknown=False,
    )
    breakdown["company_code_count"] = _match_numeric_band(
        company_codes, weights["company_code_count"]
    )
    # UNCONFIRMED bonus value; sales org has no standalone tier.
    breakdown["combined_presence_bonus"] = (
        _single_band_value(weights["combined_presence_bonus"])
        if company_codes > 0 and sales_orgs > 0
        else 0
    )
    breakdown["salesforce_instance_count"] = (
        sf_instances * _single_band_value(weights["salesforce_instance_count"])
    )

    return breakdown, warnings


# ---------------------------------------------------------------------------
# Election
# ---------------------------------------------------------------------------

def _normalized_status(row: ScoringRow) -> Optional[str]:
    status = _clean_str(row.customer_status)
    return status.casefold() if status else None


def _norm_routing(value: Optional[str]) -> Optional[str]:
    """Incoming clustering routing, normalised for comparison."""
    return value.strip().casefold() if value and value.strip() else None


def _tiebreak_key(scored: "_Scored", numeric_ids: bool):
    """Sort key: best candidate first, deterministic and order-independent.

    UNCONFIRMED ordering (confirm with Bernd): total score, most recent
    last_order_year, equipment_count, company_code_count, then LOWEST row_id
    — compared numerically when every row_id in the cluster parses as an
    integer, else lexically. row_id is the final uniqueness guarantee, so
    the winner is invariant under input shuffling.
    """
    row_key = int(scored.row.row_id) if numeric_ids else scored.row.row_id
    return (
        -scored.total,
        -(scored.last_year if scored.last_year is not None else -1),
        -(scored.equipment if scored.equipment is not None else -1),
        -scored.company_codes,
        row_key,
    )


class _Scored:
    """Internal per-row working record (score + tie-break inputs)."""

    __slots__ = ("row", "breakdown", "warnings", "total", "last_year",
                 "equipment", "company_codes")

    def __init__(
        self,
        row: ScoringRow,
        weights: dict,
        cluster_max_year: Optional[int] = None,
        cluster_max_partner_year: Optional[int] = None,
    ):
        self.row = row
        self.breakdown, self.warnings = score_row(
            row, weights, cluster_max_year, cluster_max_partner_year
        )
        self.total = sum(self.breakdown.values())
        silent: List[str] = []  # coercion warnings already captured by score_row
        self.last_year = _coerce_int(row.last_order_year, "last_order_year", silent)
        self.equipment = _coerce_int(row.equipment_count, "equipment_count", silent)
        self.company_codes = derived_counts(row)[0]


def _cluster_year_maxima(
    members: List[ScoringRow],
) -> Tuple[Optional[int], Optional[int]]:
    """(max last_order_year, max partner_last_order_year) over a cluster's rows,
    ignoring None. Both None when no member carries the respective year."""
    silent: List[str] = []
    years = [
        y for y in (
            _coerce_int(m.last_order_year, "last_order_year", silent)
            for m in members
        ) if y is not None
    ]
    partner_years = [
        y for y in (
            _coerce_int(m.partner_last_order_year, "partner_last_order_year", silent)
            for m in members
        ) if y is not None
    ]
    return (max(years) if years else None,
            max(partner_years) if partner_years else None)


def _resolve_confidence_threshold(explicit: Optional[float]) -> float:
    """Election confidence threshold: explicit arg > env > default."""
    if explicit is not None:
        return explicit
    raw = os.getenv("CONFIDENCE_MERGE_THRESHOLD")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "Invalid CONFIDENCE_MERGE_THRESHOLD %r; using %.2f",
                raw, DEFAULT_CONFIDENCE_MERGE_THRESHOLD,
            )
    return DEFAULT_CONFIDENCE_MERGE_THRESHOLD


def _cluster_merge_confidence(members: List["_Scored"]) -> Optional[float]:
    """The cluster's merge confidence = the LOWEST non-None member confidence.

    Conservative on purpose: if any member joined below threshold the whole
    merge is gated. All-None (a deterministic identical-collapse, no LLM merge)
    returns None and never gates.
    """
    confs = [
        m.row.confidence for m in members if m.row.confidence is not None
    ]
    return min(confs) if confs else None


def elect_golden_records(
    rows: List[ScoringRow],
    weights: Optional[dict] = None,
    *,
    confidence_threshold: Optional[float] = None,
) -> List[ScoringResultRow]:
    """Score every row and elect one golden record per cluster.

    Semantics (the output is Phase 3's mapping table):
    - UNIQUE rows — cluster_id None, or a cluster that degrades to a single
      member — are golden, self-reference, status "unique". Phase 3 Case A
      matches Salesforce records against golden rows; a unique SAP customer
      is a valid match target.
    - Real clusters (>=2 members) elect the highest-scoring member; losers
      point at the winner. Every election is a PROPOSAL, never auto-committed.
    - An all-blocked cluster still elects, but status "manual_review" so a
      human confirms before anything is blocked.

    Raises DuplicateRowIdError when a row_id repeats (broken upstream join).
    Results are returned in input order.
    """
    if weights is None:
        weights = load_weights()
    threshold = _resolve_confidence_threshold(confidence_threshold)
    wv = weights_version(weights)

    duplicates = sorted(
        rid for rid, n in Counter(r.row_id for r in rows).items() if n > 1
    )
    if duplicates:
        raise DuplicateRowIdError(duplicates)

    # G1: count criteria are cluster-context-dependent, so compute each real
    # cluster's most-recent year BEFORE scoring, then score every row against
    # its cluster's maxima. Single-member clusters (which degrade to unique) and
    # uncluster rows are scored context-free (their own year is the max).
    rows_by_cluster: Dict[str, List[ScoringRow]] = {}
    for row in rows:
        if row.cluster_id is not None:
            rows_by_cluster.setdefault(row.cluster_id, []).append(row)
    cluster_maxima: Dict[str, Tuple[Optional[int], Optional[int]]] = {
        cid: _cluster_year_maxima(members)
        for cid, members in rows_by_cluster.items()
        if len(members) >= 2
    }

    scored = [
        _Scored(row, weights, *cluster_maxima.get(row.cluster_id, (None, None)))
        for row in rows
    ]

    clusters: Dict[str, List[_Scored]] = {}
    for s in scored:
        if s.row.cluster_id is not None:
            clusters.setdefault(s.row.cluster_id, []).append(s)

    # A content-hash cluster_id whose present members don't reproduce the hash
    # is a PARTIAL cluster: some members were submitted in a different score
    # call (e.g. split across blocks). Warn — never fail — and elect within
    # what's seen. (Test/JSON ids like "C1" aren't hashes and never trip this.)
    partial_clusters: set[str] = set()
    for cid, members in clusters.items():
        if cid.startswith(CLUSTER_ID_PREFIX) and cluster_hash(
            m.row.row_id for m in members
        ) != cid:
            partial_clusters.add(cid)

    winner_by_cluster: Dict[str, str] = {}
    manual_review_clusters: set[str] = set()
    for cluster_id, members in clusters.items():
        if len(members) < 2:
            continue  # single-member cluster degrades to unique below
        numeric_ids = all(_parses_as_int(m.row.row_id) for m in members)
        winner = min(members, key=lambda m: _tiebreak_key(m, numeric_ids))
        winner_by_cluster[cluster_id] = winner.row.row_id
        # A cluster is demoted to manual_review when, in precedence order:
        #   1. clustering already routed a member to manual_review (INHERITED —
        #      election can never upgrade upstream uncertainty),
        #   2. every member is blocked (a human confirms before a block), or
        #   3. the merge confidence is below threshold.
        # Any one is sufficient; election never leaves such a cluster proposed.
        inherited_mr = any(
            _norm_routing(m.row.routing) == "manual_review" for m in members
        )
        all_blocked = all(_normalized_status(m.row) == "blocked" for m in members)
        merge_conf = _cluster_merge_confidence(members)
        low_confidence = merge_conf is not None and merge_conf < threshold
        # A zero-signal election (every member scored 0) has no basis to pick a
        # winner beyond the tie-break — it must not look confident.
        zero_signal = all(m.total == 0 for m in members)
        if inherited_mr or all_blocked or low_confidence or zero_signal:
            manual_review_clusters.add(cluster_id)

    results: List[ScoringResultRow] = []
    for s in scored:
        cluster_id = s.row.cluster_id
        winner_id = winner_by_cluster.get(cluster_id) if cluster_id else None
        if winner_id is None:
            # No cluster, or a degraded single-member cluster. Normally unique —
            # but a row clustering flagged manual_review stays manual_review
            # (election never upgrades uncertainty into a confident unique).
            lone_status = (
                "manual_review"
                if _norm_routing(s.row.routing) == "manual_review"
                else "unique"
            )
            # A lone row is its own proposed winner.
            result = _build_result(s, lone_status, s.row.row_id, wv)
        else:
            status = (
                "manual_review" if cluster_id in manual_review_clusters else "proposed"
            )
            result = _build_result(s, status, winner_id, wv)
        if cluster_id in partial_clusters:
            result.warnings = [
                *result.warnings,
                f"partial_cluster: submitted rows are a subset of {cluster_id}",
            ]
        results.append(result)
    return results


def _build_result(
    s: "_Scored", election_status: str, winner_id: str, wv: Optional[str] = None
) -> ScoringResultRow:
    """Assemble one result row with the golden + approval lifecycle fields.

    - unique: golden=true, self-reference, nothing to approve (approval None).
    - proposed / manual_review: is_golden_record/golden_record_id carry the
      COMPUTED proposal (winner=true, self-reference; loser=false, points at the
      winner), proposed_golden_id echoes the winner, approval_status="proposed".

    The manual_review golden BLANKING the spec calls for is applied at the file
    writeback (dedup.scoring_xlsx), not here — the JSON/model keeps the proposal
    so summary accounting and Phase-3 promotion-on-approval have it. Phase 3
    still filters on approval_status/election_status, never is_golden alone.
    """
    row_id = s.row.row_id
    company_code_count, sales_org_count, salesforce_instance_count = derived_counts(
        s.row
    )
    if election_status == "unique":
        return ScoringResultRow(
            row_id=row_id, cluster_id=s.row.cluster_id, score=s.total,
            company_code_count=company_code_count,
            sales_org_count=sales_org_count,
            salesforce_instance_count=salesforce_instance_count,
            is_golden_record=True, golden_record_id=row_id,
            proposed_golden_id=None, election_status="unique",
            approval_status=None, scored_with_weights_version=wv,
            score_breakdown=s.breakdown, warnings=s.warnings,
        )
    is_winner = row_id == winner_id
    return ScoringResultRow(
        row_id=row_id, cluster_id=s.row.cluster_id, score=s.total,
        company_code_count=company_code_count,
        sales_org_count=sales_org_count,
        salesforce_instance_count=salesforce_instance_count,
        is_golden_record=is_winner,
        golden_record_id=row_id if is_winner else winner_id,
        proposed_golden_id=winner_id,
        election_status=election_status,  # "proposed" | "manual_review"
        approval_status="proposed", scored_with_weights_version=wv,
        score_breakdown=s.breakdown, warnings=s.warnings,
    )


def _parses_as_int(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


def build_summary(
    results: List[ScoringResultRow],
    *,
    errors: int = 0,
    warnings: Optional[List[str]] = None,
) -> ScoringSummary:
    """Aggregate a result list into the response summary."""
    summary = ScoringSummary(
        rows_in=len(results) + errors,
        errors=errors,
        warnings=list(warnings or []),
    )
    cluster_ids: set[str] = set()
    manual_review_ids: set[str] = set()
    for r in results:
        if r.warnings:
            summary.rows_with_warnings += 1
        if r.election_status == "unique":
            summary.rows_unique += 1
            continue
        if r.election_status == "manual_review":
            summary.rows_manual_review += 1
        # A non-unique row is either part of a real cluster, or a lone row that
        # clustering flagged manual_review (cluster_id None). Both still elect a
        # record (self for the lone row); only real clusters count toward
        # cluster / manual_review-cluster tallies.
        if r.is_golden_record:
            summary.rows_elected += 1
        else:
            summary.rows_duplicates += 1
        if r.cluster_id is not None:
            cluster_ids.add(r.cluster_id)
            if r.election_status == "manual_review":
                manual_review_ids.add(r.cluster_id)
    summary.clusters = len(cluster_ids)
    summary.all_blocked_clusters = len(manual_review_ids)
    return summary
