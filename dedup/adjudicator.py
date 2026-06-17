"""Per-block dedup algorithm (STEP B/C) and the request-level entry point.

STEP A (signature collapsing) lives in ``dedup.signatures``. This module groups
signatures into entities — Mode A (one partition call) for small blocks, Mode B
(incremental canonical assignment) for large ones — enforces the deterministic
Name 2 asymmetry rule, emits clusters, and fans the decisions back out to rows.

Telemetry is emitted as structured logs; Azure Functions ships them to the
``mdm-pipeline-insights`` Application Insights instance (see host.json).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, List, Optional

from dedup.llm import DedupLLM, parse_json_object
from dedup.models import DedupResponse, DedupResultRow, DedupRow, DedupSummary
from dedup.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_mode_a_user_prompt,
    build_mode_b_user_prompt,
)
from dedup.signatures import Signature, build_signatures, group_rows_by_block

logger = logging.getLogger(__name__)

DEFAULT_SIG_PARTITION_THRESHOLD = 12
DEFAULT_DEDUP_MAX_CONCURRENCY = 5


# ---------------------------------------------------------------------------
# Internal working types
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """A specific (institution, department) — the unit duplicates map to."""

    entity_id: str
    signatures: List[Signature] = field(default_factory=list)
    institution: Optional[str] = None
    department: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None

    @property
    def has_name2(self) -> bool:
        # All signatures in an entity share the same has_name2 (enforced).
        return any(s.has_name2 for s in self.signatures)

    @property
    def row_ids(self) -> List[str]:
        """Union of row_ids across the entity's signatures (order-preserving)."""
        seen: set[str] = set()
        ordered: List[str] = []
        for sig in self.signatures:
            for rid in sig.row_ids:
                if rid not in seen:
                    seen.add(rid)
                    ordered.append(rid)
        return ordered

    @property
    def llm_merged(self) -> bool:
        """True when membership came from an LLM merge across distinct
        signatures (≥2 signatures); False for a pure identical-collapse."""
        return len(self.signatures) >= 2


@dataclass
class BlockStats:
    """Per-block telemetry accumulator."""

    block_id: str = ""
    rows_in: int = 0
    distinct_signatures: int = 0
    mode: str = "-"
    llm_calls: int = 0
    clusters: int = 0
    rows_clustered: int = 0
    rows_unique: int = 0
    rows_manual_review: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model_version: str = ""


def _confidence_to_float(value: Any) -> Optional[float]:
    """Coerce an LLM confidence to a float in [0, 1], or None."""
    if value is None:
        return None
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return None
    if conf < 0.0:
        return 0.0
    if conf > 1.0:
        return 1.0
    return conf


def _enforce_name2_split(entities: List[Entity], next_index: int) -> tuple[List[Entity], int]:
    """Deterministic safety net for the Name 2 asymmetry rule.

    A signature with an empty Name 2 can NEVER share an entity with a
    populated-Name 2 signature. If an LLM-returned entity mixes the two, split
    it so the empty-Name 2 signatures form their own (institution-level)
    entity. Returns the (possibly expanded) entity list and the next free id
    index.
    """
    result: List[Entity] = []
    for ent in entities:
        populated = [s for s in ent.signatures if s.has_name2]
        empty = [s for s in ent.signatures if not s.has_name2]
        if populated and empty:
            logger.warning(
                "Dedup: LLM merged empty- and populated-Name2 signatures in "
                "entity %s; splitting deterministically", ent.entity_id,
            )
            ent.signatures = populated
            result.append(ent)
            split = Entity(
                entity_id=f"e{next_index}",
                signatures=empty,
                institution=ent.institution,
                department="",
                confidence=ent.confidence,
                reasoning="Split from a mixed-Name2 group (deterministic rule).",
            )
            next_index += 1
            result.append(split)
        else:
            result.append(ent)
    return result, next_index


# ---------------------------------------------------------------------------
# Mode A — one partition call per Name 2 bucket
# ---------------------------------------------------------------------------

async def _mode_a(
    signatures: List[Signature],
    llm: DedupLLM,
    semaphore: asyncio.Semaphore,
    stats: BlockStats,
) -> List[Entity]:
    """Partition signatures into entities with a partition call.

    Signatures are first split by ``has_name2`` so the empty-vs-populated
    decision is never sent to the LLM (it is deterministic). Each bucket of
    ≥2 signatures gets one partition call; singleton buckets become entities
    directly with no call.
    """
    entities: List[Entity] = []
    next_index = 1

    buckets = {
        True: [s for s in signatures if s.has_name2],
        False: [s for s in signatures if not s.has_name2],
    }

    for has_name2, bucket in buckets.items():
        if not bucket:
            continue
        if len(bucket) == 1:
            sig = bucket[0]
            entities.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            continue

        by_id = {s.signature_id: s for s in bucket}
        payload = [
            {
                "signature_id": s.signature_id,
                "name1": s.name1,
                "name2": s.name2,
                "ror_id": s.ror_id or "none",
            }
            for s in bucket
        ]
        user_prompt = build_mode_a_user_prompt(payload)

        async with semaphore:
            call = await llm.adjudicate(SYSTEM_PROMPT, user_prompt)
        stats.llm_calls += 1
        _record_call_stats(stats, call)

        parsed = parse_json_object(call.raw) if call.error is None else None
        if parsed is None:
            # Bad / unparseable response — mark every signature in the bucket
            # uncertain and keep going. Never fail the block.
            stats.errors += 1
            logger.error(
                "Dedup Mode A: unusable LLM response for block=%s bucket_size=%d "
                "signatures=%s error=%s",
                stats.block_id, len(bucket), list(by_id.keys()), call.error,
            )
            for sig in bucket:
                sig.uncertain = True
                entities.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
                next_index += 1
            continue

        assigned: set[str] = set()
        decision_counts: Counter = Counter()

        for ent_obj in parsed.get("entities", []) or []:
            sig_ids = [sid for sid in (ent_obj.get("signature_ids") or []) if sid in by_id]
            members = [by_id[sid] for sid in sig_ids if sid not in assigned]
            if not members:
                continue
            assigned.update(s.signature_id for s in members)
            decision_counts["entity"] += 1
            entities.append(Entity(
                entity_id=f"e{next_index}",
                signatures=members,
                institution=(ent_obj.get("institution") or None),
                department=(ent_obj.get("department") or None),
                confidence=_confidence_to_float(ent_obj.get("confidence")),
                reasoning=(ent_obj.get("reasoning") or None),
            ))
            next_index += 1

        for sid in parsed.get("uncertain_signature_ids", []) or []:
            sig = by_id.get(sid)
            if sig is None or sid in assigned:
                continue
            assigned.add(sid)
            decision_counts["uncertain"] += 1
            sig.uncertain = True
            entities.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1

        # Any signature the LLM dropped from its partition: treat as uncertain
        # so it surfaces for review rather than vanishing.
        for sig in bucket:
            if sig.signature_id not in assigned:
                decision_counts["missing"] += 1
                sig.uncertain = True
                entities.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
                next_index += 1

        _log_llm_call("A", stats, call, dict(decision_counts))

    # Safety net (the bucketing above already prevents mixed entities, but
    # honour the spec's "enforce after the LLM returns" guarantee).
    entities, _ = _enforce_name2_split(entities, next_index)
    return entities


# ---------------------------------------------------------------------------
# Mode B — incremental canonical assignment
# ---------------------------------------------------------------------------

async def _mode_b(
    signatures: List[Signature],
    llm: DedupLLM,
    semaphore: asyncio.Semaphore,
    stats: BlockStats,
) -> List[Entity]:
    """Assign signatures to canonical entities one at a time.

    Keeps calls O(signatures) with each prompt bounded. The empty-vs-populated
    Name 2 constraint is enforced by only ever presenting canonicals whose
    ``has_name2`` matches the candidate; an incompatible candidate starts a new
    entity with no LLM call.
    """
    canonicals: List[Entity] = []
    next_index = 1

    for sig in signatures:
        if not canonicals:
            canonicals.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            continue

        compatible = [e for e in canonicals if e.has_name2 == sig.has_name2]
        if not compatible:
            # Deterministically a new entity — never compared across the
            # Name 2 boundary.
            canonicals.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            continue

        candidate = {
            "signature_id": sig.signature_id,
            "name1": sig.name1,
            "name2": sig.name2,
            "ror_id": sig.ror_id or "none",
        }
        canonical_payload = [
            {
                "entity_id": e.entity_id,
                "institution": e.institution or e.signatures[0].name1,
                "department": e.department or e.signatures[0].name2,
                "name1": e.signatures[0].name1,
                "name2": e.signatures[0].name2,
                "ror_id": next((s.ror_id for s in e.signatures if s.ror_id), "none"),
            }
            for e in compatible
        ]
        user_prompt = build_mode_b_user_prompt(candidate, canonical_payload)

        async with semaphore:
            call = await llm.adjudicate(SYSTEM_PROMPT, user_prompt, max_tokens=1000)
        stats.llm_calls += 1
        _record_call_stats(stats, call)

        parsed = parse_json_object(call.raw) if call.error is None else None
        if parsed is None:
            stats.errors += 1
            logger.error(
                "Dedup Mode B: unusable LLM response for block=%s signature=%s error=%s",
                stats.block_id, sig.signature_id, call.error,
            )
            sig.uncertain = True
            canonicals.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            continue

        decision = str(parsed.get("decision", "")).strip().lower()
        confidence = _confidence_to_float(parsed.get("confidence"))
        reasoning = parsed.get("reasoning") or None
        compatible_by_id = {e.entity_id: e for e in compatible}

        if decision == "match":
            target = compatible_by_id.get(parsed.get("matched_entity_id"))
            if target is not None:
                target.signatures.append(sig)
                # Carry the merge's confidence/reasoning onto the entity.
                target.confidence = confidence
                target.reasoning = reasoning
                _log_llm_call("B", stats, call, {"match": 1})
                continue
            # Claimed a match to an unknown/incompatible id → treat as new.
            logger.warning(
                "Dedup Mode B: match to unknown entity_id %r for signature %s; "
                "treating as new", parsed.get("matched_entity_id"), sig.signature_id,
            )
            canonicals.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            _log_llm_call("B", stats, call, {"new": 1})
        elif decision == "new":
            canonicals.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            _log_llm_call("B", stats, call, {"new": 1})
        else:
            # "uncertain" or anything unrecognised → own entity, flagged.
            sig.uncertain = True
            ent = Entity(
                entity_id=f"e{next_index}",
                signatures=[sig],
                confidence=confidence,
                reasoning=reasoning,
            )
            canonicals.append(ent)
            next_index += 1
            _log_llm_call("B", stats, call, {"uncertain": 1})

    return canonicals


# ---------------------------------------------------------------------------
# STEP C — emit clusters, fan out to rows
# ---------------------------------------------------------------------------

def _emit_rows(
    block_id: str,
    entities: List[Entity],
    model: str,
    model_version: str,
    stats: BlockStats,
) -> List[DedupResultRow]:
    """Build one output row per input row, with a block-local cluster number.

    The block-local number is remapped to a global sequential id in
    ``cluster_blocks`` once every block has finished, so the final cluster_id
    is a simple 1, 2, 3 … unique across the whole response.
    """
    out: List[DedupResultRow] = []
    cluster_n = 0

    for ent in entities:
        row_ids = ent.row_ids
        clustered = len(row_ids) >= 2
        if clustered:
            cluster_n += 1
            cluster_id: Optional[int] = cluster_n
            stats.clusters += 1
        else:
            cluster_id = None

        for sig in ent.signatures:
            for rid in sig.row_ids:
                if sig.uncertain:
                    routing = "manual_review"
                    stats.rows_manual_review += 1
                elif cluster_id is not None:
                    routing = "cluster"
                    stats.rows_clustered += 1
                else:
                    routing = "unique"
                    stats.rows_unique += 1

                # Confidence/reasoning surface for clustered or uncertain rows;
                # a plain unique row carries neither.
                if cluster_id is not None or sig.uncertain:
                    confidence = ent.confidence
                    reasoning = ent.reasoning
                else:
                    confidence = None
                    reasoning = None

                out.append(DedupResultRow(
                    row_id=rid,
                    block_id=block_id,
                    cluster_id=cluster_id,
                    routing=routing,
                    llm_flag=ent.llm_merged,
                    signature_id=sig.signature_id,
                    confidence=confidence,
                    reasoning=reasoning,
                    model=model,
                    model_version=model_version,
                    prompt_version=PROMPT_VERSION,
                ))
    return out


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

def _record_call_stats(stats: BlockStats, call: Any) -> None:
    stats.prompt_tokens += call.prompt_tokens
    stats.completion_tokens += call.completion_tokens
    stats.latency_ms += call.latency_ms
    if call.model_version:
        stats.model_version = call.model_version


def _log_llm_call(mode: str, stats: BlockStats, call: Any, decisions: dict) -> None:
    logger.info(
        "dedup_llm_call",
        extra={
            "block_id": stats.block_id,
            "mode": mode,
            "latency_ms": call.latency_ms,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "decisions": decisions,
            "model_version": call.model_version,
            "prompt_version": PROMPT_VERSION,
        },
    )


# ---------------------------------------------------------------------------
# Per-block driver
# ---------------------------------------------------------------------------

async def _process_block(
    block_id: str,
    rows: List[DedupRow],
    llm: DedupLLM,
    threshold: int,
    semaphore: asyncio.Semaphore,
) -> tuple[List[DedupResultRow], BlockStats]:
    stats = BlockStats(block_id=block_id, rows_in=len(rows))

    signatures = build_signatures(rows)
    stats.distinct_signatures = len(signatures)
    n = len(signatures)

    if n <= 1:
        # Single signature (or empty) — no LLM. Identical rows still cluster.
        stats.mode = "A"
        entities = [Entity(entity_id="e1", signatures=signatures)] if signatures else []
    elif n <= threshold:
        stats.mode = "A"
        entities = await _mode_a(signatures, llm, semaphore, stats)
    else:
        stats.mode = "B"
        entities = await _mode_b(signatures, llm, semaphore, stats)

    out = _emit_rows(
        block_id, entities, llm.model, stats.model_version or llm.model, stats,
    )

    logger.info(
        "dedup_block",
        extra={
            "block_id": block_id,
            "rows_in": stats.rows_in,
            "distinct_signatures": stats.distinct_signatures,
            "mode": stats.mode,
            "llm_calls": stats.llm_calls,
            "clusters": stats.clusters,
            "rows_manual_review": stats.rows_manual_review,
            "errors": stats.errors,
        },
    )
    return out, stats


# ---------------------------------------------------------------------------
# Request-level entry point
# ---------------------------------------------------------------------------

async def cluster_blocks(
    rows: List[DedupRow],
    llm: DedupLLM,
    *,
    settings: Any = None,
    threshold: Optional[int] = None,
    concurrency: Optional[int] = None,
) -> DedupResponse:
    """Cluster every address block in ``rows`` and return assignments.

    Blocks are processed independently and concurrently; a shared semaphore
    bounds the number of in-flight LLM calls across all blocks.
    """
    request_start = time.perf_counter()

    if threshold is None:
        threshold = int(os.getenv("SIG_PARTITION_THRESHOLD", str(DEFAULT_SIG_PARTITION_THRESHOLD)))
    if concurrency is None:
        concurrency = int(os.getenv("DEDUP_MAX_CONCURRENCY", str(DEFAULT_DEDUP_MAX_CONCURRENCY)))
    semaphore = asyncio.Semaphore(max(1, concurrency))

    blocks = group_rows_by_block(rows)

    block_outputs = await asyncio.gather(
        *[
            _process_block(block_id, block_rows, llm, threshold, semaphore)
            for block_id, block_rows in blocks.items()
        ]
    )

    all_rows: List[DedupResultRow] = []
    summary = DedupSummary(blocks=len(blocks), rows_in=len(rows))
    total_prompt_tokens = 0
    total_completion_tokens = 0
    # Remap each block's local cluster numbers onto a single global sequence
    # (1, 2, 3 …). Done after gather, in deterministic block order, so the
    # ids are stable regardless of concurrent block completion order.
    global_cluster_n = 0

    for out_rows, stats in block_outputs:
        local_to_global: dict[int, int] = {}
        for row in out_rows:
            if row.cluster_id is not None:
                if row.cluster_id not in local_to_global:
                    global_cluster_n += 1
                    local_to_global[row.cluster_id] = global_cluster_n
                row.cluster_id = local_to_global[row.cluster_id]
        all_rows.extend(out_rows)
        summary.distinct_signatures += stats.distinct_signatures
        summary.clusters += stats.clusters
        summary.rows_clustered += stats.rows_clustered
        summary.rows_unique += stats.rows_unique
        summary.rows_manual_review += stats.rows_manual_review
        summary.llm_calls += stats.llm_calls
        summary.errors += stats.errors
        total_prompt_tokens += stats.prompt_tokens
        total_completion_tokens += stats.completion_tokens

    total_latency_ms = int((time.perf_counter() - request_start) * 1000)
    logger.info(
        "dedup_request",
        extra={
            "summary": summary.model_dump(),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_latency_ms": total_latency_ms,
            "prompt_version": PROMPT_VERSION,
        },
    )

    return DedupResponse(rows=all_rows, summary=summary)
