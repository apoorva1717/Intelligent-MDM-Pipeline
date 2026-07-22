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
from collections import Counter
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    model_config = ConfigDict(populate_by_name=True)

    row_id: str = Field(..., description="SAP Customer / BP number, join key.")
    cluster_id: Optional[str] = Field(
        default=None,
        description="Stable cluster key from the adjudicator. None => no cluster.",
    )
    confidence: Optional[float] = Field(
        default=None,
        description=(
            "Adjudication merge confidence from clustering (0-1). Below the "
            "configured threshold the merge is demoted to manual_review. None "
            "(a deterministic identical-collapse, or a unique row) never gates."
        ),
    )
    routing: Optional[str] = Field(
        default=None,
        description=(
            "Incoming clustering routing: cluster | unique | manual_review. A "
            "manual_review from clustering is INHERITED by election and can "
            "never be upgraded to proposed — uncertainty only ever propagates."
        ),
    )
    reasoning: Optional[str] = Field(
        default=None,
        description=(
            "Adjudication reasoning from clustering. Surfaced in the issue list "
            "to flag a verdict_contradiction (reasoning that disowns a merge)."
        ),
    )
    last_order_year: Scalar = None
    order_count: Scalar = None
    partner_last_order_year: Scalar = None
    partner_order_count: Scalar = None
    equipment_count: Scalar = None
    sleeping_band: Optional[str] = None  # expected "No" | "3-4" | ">5"
    customer_status: Optional[str] = None  # expected "active" | "blocked"
    account_group: Optional[str] = None
    company_code_consolidated: Optional[str] = None  # ";"-delimited
    sales_org_consolidated: Optional[str] = None  # ";"-delimited
    salesforce_ids: List[Optional[str]] = Field(
        default_factory=list,
        description="8 slots: biosystems, AXS, 3..8. Only non-empty ids count.",
    )

    @field_validator(
        "cluster_id",
        "routing",
        "reasoning",
        "sleeping_band",
        "customer_status",
        "account_group",
        "company_code_consolidated",
        "sales_org_consolidated",
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

    @field_validator("salesforce_ids", mode="before")
    @classmethod
    def _stringify_ids(cls, v):
        if v is None:
            return []
        return [None if item is None else str(item) for item in v]

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

    row_id: str
    cluster_id: Optional[str] = None
    score: int
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
    score_breakdown: Dict[str, int]
    warnings: List[str] = Field(default_factory=list)


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
)

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

    Row-level: verdict_contradiction. Cluster-level (keyed on the proposed
    winner): low_confidence_merge, all_blocked_cluster, tiebreak_decided,
    empty_scoring_payload. Deterministic and offline.
    """
    threshold = _resolve_confidence_threshold(confidence_threshold)
    by_id = {r.row_id: r for r in rows}
    issues: List[DedupIssue] = []

    # Row-level — a persisted reasoning that argues against its own merge.
    for row in rows:
        if _reasoning_is_contradiction(row.reasoning):
            issues.append(DedupIssue(
                row_id=row.row_id, cluster_id=row.cluster_id,
                issue_type="verdict_contradiction",
                detail=row.reasoning.strip()[:200],  # type: ignore[union-attr]
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
    """Non-empty parts of a ";"-delimited cell ("1003;1017;" -> 2 parts)."""
    if value is None:
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def derived_counts(row: ScoringRow) -> Tuple[int, int, int]:
    """(company_code_count, sales_org_count, salesforce_instance_count).

    Always derived from the consolidated fields / id slots, never read from
    the file — single source of truth.
    """
    company_codes = len(split_consolidated(row.company_code_consolidated))
    sales_orgs = len(split_consolidated(row.sales_org_consolidated))
    sf_instances = sum(
        1 for sid in row.salesforce_ids if sid is not None and str(sid).strip()
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

def score_row(row: ScoringRow, weights: dict) -> Tuple[Dict[str, int], List[str]]:
    """Per-criterion points + warnings for one row.

    The breakdown always carries every criterion key (0 where nothing
    matched) so the audit trail and the file writeback are column-stable.
    """
    warnings: List[str] = []
    breakdown: Dict[str, int] = {}

    last_year = _coerce_int(row.last_order_year, "last_order_year", warnings)
    order_count = _coerce_int(row.order_count, "order_count", warnings)
    partner_year = _coerce_int(
        row.partner_last_order_year, "partner_last_order_year", warnings
    )
    partner_count = _coerce_int(row.partner_order_count, "partner_order_count", warnings)
    equipment = _coerce_int(row.equipment_count, "equipment_count", warnings)
    company_codes, sales_orgs, sf_instances = derived_counts(row)

    breakdown["sales_order_last_used"] = _match_numeric_band(
        last_year, weights["sales_order_last_used"]
    )
    breakdown["sales_order_count"] = _match_numeric_band(
        order_count, weights["sales_order_count"]
    )
    breakdown["sales_order_partner_last_used"] = _match_numeric_band(
        partner_year, weights["sales_order_partner_last_used"]
    )
    # UNCONFIRMED: partner count tiers mirror sales order count. CONFIRM w/ Bernd.
    breakdown["sales_order_partner_count"] = _match_numeric_band(
        partner_count, weights["sales_order_partner_count"]
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

    def __init__(self, row: ScoringRow, weights: dict):
        self.row = row
        self.breakdown, self.warnings = score_row(row, weights)
        self.total = sum(self.breakdown.values())
        silent: List[str] = []  # coercion warnings already captured by score_row
        self.last_year = _coerce_int(row.last_order_year, "last_order_year", silent)
        self.equipment = _coerce_int(row.equipment_count, "equipment_count", silent)
        self.company_codes = derived_counts(row)[0]


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

    scored = [_Scored(row, weights) for row in rows]

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
    if election_status == "unique":
        return ScoringResultRow(
            row_id=row_id, cluster_id=s.row.cluster_id, score=s.total,
            is_golden_record=True, golden_record_id=row_id,
            proposed_golden_id=None, election_status="unique",
            approval_status=None, scored_with_weights_version=wv,
            score_breakdown=s.breakdown, warnings=s.warnings,
        )
    is_winner = row_id == winner_id
    return ScoringResultRow(
        row_id=row_id, cluster_id=s.row.cluster_id, score=s.total,
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
