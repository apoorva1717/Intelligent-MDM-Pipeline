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
    build_mode_a_user_prompt,
    build_mode_a_user_prompt_v2,
    build_mode_b_user_prompt,
    build_mode_b_user_prompt_v2,
    prompt_version,
    system_prompt,
)
from dedup.candidates import CandidateUnit, generate_candidate_pairs, pair_evidence
from dedup.flags import v2_any, v2_blocking, v2_id_conflict, v2_name2
from dedup.cluster_key import cluster_hash, link_hash
from dedup.signatures import Signature, build_blocks, build_signatures

logger = logging.getLogger(__name__)

DEFAULT_SIG_PARTITION_THRESHOLD = 12
DEFAULT_DEDUP_MAX_CONCURRENCY = 5
DEFAULT_NAME_CANDIDATE_THRESHOLD = 0.85
DEFAULT_TOKEN_CANDIDATE_THRESHOLD = 0.6
DEFAULT_MAX_CANDIDATES_PER_BLOCK = 50


@dataclass
class _CandidateConfig:
    """Resolved residue-nomination knobs (settings > env > default)."""

    name_threshold: float = DEFAULT_NAME_CANDIDATE_THRESHOLD
    token_threshold: float = DEFAULT_TOKEN_CANDIDATE_THRESHOLD
    max_candidates: int = DEFAULT_MAX_CANDIDATES_PER_BLOCK


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
    # True once this entity's membership has been decided by the LLM (a Mode A/B
    # verdict or a residue-pass adjudication). A deterministic bucket-size-1 /
    # seed entity that never reached the LLM stays False — that is the ONLY case
    # allowed to emit an empty Reasoning field.
    adjudicated: bool = False

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
    # Residue candidate-nomination telemetry.
    candidates_generated: int = 0
    candidates_by_rule: Counter = field(default_factory=Counter)
    rejected_with_reasoning: int = 0
    candidate_cap_exceeded: bool = False


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


def _next_entity_index(entities: List[Entity]) -> int:
    """Smallest ``e<N>`` index free above every existing entity id."""
    highest = 0
    for ent in entities:
        suffix = ent.entity_id[1:] if ent.entity_id.startswith("e") else ""
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def _distinct_nonempty(values: Any) -> set[str]:
    return {v for v in values if v}


def _enforce_identity_split(
    entities: List[Entity], next_index: int
) -> tuple[List[Entity], int, bool]:
    """Deterministic guard: an entity may never hold two DIFFERENT non-empty
    ROR ids, nor two different non-empty LEI ids.

    A different non-empty hard identifier means a different institution / legal
    entity — a strong split signal (ROR/LEI is only ever a split signal here,
    never a merge trigger). When an LLM merge violates this, split the entity
    into singletons and flag each for human review; we never guess a safe
    regrouping (the safe outcome is manual_review). Returns the (expanded)
    entity list, the next free id index, and whether any split fired.

    Under ``DEDUP_V2_ID_CONFLICT`` the entity is KEPT and routed to review
    instead (see :func:`_route_identity_conflict`). Exploding it into
    singletons destroys the finding: two records at one door carrying two
    different ROR ids is a Phase 1 resolution problem, and the pair is the
    evidence for it. Split apart they become two unremarkable unique rows, and
    a steward has nothing to look at.
    """
    if v2_id_conflict():
        return _route_identity_conflict(entities), next_index, False

    result: List[Entity] = []
    fired = False
    for ent in entities:
        rors = _distinct_nonempty(s.ror_id for s in ent.signatures)
        leis = _distinct_nonempty(s.lei_id for s in ent.signatures)
        if len(ent.signatures) < 2 or (len(rors) < 2 and len(leis) < 2):
            result.append(ent)
            continue
        fired = True
        kind = "ROR" if len(rors) >= 2 else "LEI"
        ids = sorted(rors if len(rors) >= 2 else leis)
        logger.warning(
            "Dedup: entity %s merged conflicting %s ids %s; splitting to "
            "manual_review", ent.entity_id, kind, ids,
        )
        reason = (
            f"Split: different non-empty {kind} ids ({', '.join(ids)}) "
            f"indicate different entities; routed to manual review."
        )
        for i, sig in enumerate(ent.signatures):
            sig.uncertain = True
            sig.merge_reasoning = reason
            sig.merge_confidence = ent.confidence
            if i == 0:
                entity_id = ent.entity_id
            else:
                entity_id = f"e{next_index}"
                next_index += 1
            result.append(Entity(
                entity_id=entity_id,
                signatures=[sig],
                institution=ent.institution,
                department=ent.department,
                confidence=ent.confidence,
                reasoning=reason,
            ))
    return result, next_index, fired


def _ordered_distinct(values: Any) -> List[str]:
    """Distinct non-empty values in first-appearance order.

    NOT ``_distinct_nonempty``, which returns a set: iterating a set of strings
    is hash-ordered, so a reason string built from one names its two ids in an
    order that can change between interpreters. The v1 split path sorts its set
    before rendering and is safe; anything new has to preserve order itself.
    """
    seen: dict = {}
    for value in values:
        if value:
            seen.setdefault(value, None)
    return list(seen)


def _signature_order(sig: Signature) -> tuple:
    """Sort key putting a block's signatures back in build order (s1, s2, …).

    An entity's ``signatures`` list is in the order the MODEL happened to write
    the ids, which is not a property of the data. Anything user-visible derived
    from it — the two ids named in an id-conflict reason — has to be re-ordered
    off the signature ids, or the same conflict reads differently between runs.
    """
    raw = (sig.signature_id or "").lstrip("s")
    return (0, int(raw)) if raw.isdigit() else (1, sig.signature_id or "")


def _short_id(value: str) -> str:
    """``https://ror.org/04v7hvq31`` → ``04v7hvq31``; an LEI unchanged."""
    return str(value).rstrip("/").rsplit("/", 1)[-1]


def _inferred_from_short_name(sig: Signature, kind: str) -> Optional[str]:
    """A note when this signature's id was guessed from a name too thin to guess from.

    A one- or two-token Name 1 — "Scripps", "Takeda" — is a brand, not an
    organisation: it names a family with several members and a resolver picking
    one of them is choosing, not resolving. When Phase 1's provenance says the
    id was not registry-verified AND the name it came from is that short, the
    conflict has a likely cause and the reviewer should be told it rather than
    left to rediscover it.

    Silent when the provenance column says verified, and silent when it says
    nothing at all — an absent provenance is not evidence of inference.
    """
    provenance = sig.ror_provenance if kind == "ROR" else sig.lei_provenance
    if not provenance or ":verified" in provenance:
        return None
    tokens = (sig.institution or sig.name1 or "").split()
    if not tokens or len(tokens) > 2:
        return None
    identifier = sig.ror_id if kind == "ROR" else sig.lei_id
    return (
        f"{kind} {_short_id(identifier)} was inferred "
        f"({provenance}) from a {len(tokens)}-token name "
        f"({' '.join(tokens)!r})"
    )


def _route_identity_conflict(entities: List[Entity]) -> List[Entity]:
    """Keep the entity, route it to review, and say what the conflict is.

    v1 split such an entity into singletons. That is the one outcome that
    cannot be right: either the records are the same and the split is wrong, or
    they are different and the ids are doing their job — and in both cases the
    thing a steward needs is the PAIR, with both ids named. The cluster id
    therefore stands, the routing says "confirm this", and the reasoning names
    the two ids in the order the signatures appear.
    """
    for ent in entities:
        if len(ent.signatures) < 2:
            continue
        ordered = sorted(ent.signatures, key=_signature_order)
        for kind, ids in (
            ("ROR", _ordered_distinct(s.ror_id for s in ordered)),
            ("LEI", _ordered_distinct(s.lei_id for s in ordered)),
        ):
            if len(ids) < 2:
                continue
            logger.warning(
                "Dedup: entity %s holds conflicting %s ids %s; routing to "
                "manual_review with the cluster intact", ent.entity_id, kind, ids,
            )
            reason = (
                f"id conflict: {kind} "
                + " vs ".join(_short_id(value) for value in ids)
            )
            notes = [
                note for note in (
                    _inferred_from_short_name(sig, kind) for sig in ordered
                ) if note
            ]
            if notes:
                reason = f"{reason} — {'; '.join(dict.fromkeys(notes))}"
            for sig in ent.signatures:
                sig.uncertain = True
                sig.merge_reasoning = reason
            ent.reasoning = reason
            break
    return entities


# Explicit non-merge assertions. Read ONLY to demote toward manual_review —
# never to merge — so a coarse phrase match is the safe direction (spec: if a
# verdict is ambiguous, route the whole block to manual_review, never guess
# toward merging).
_NONMERGE_MARKERS = (
    "should not be merged",
    "should not merge",
    "must not be merged",
    "not be merged",
    "do not merge",
    "should be split",
    "must be split",
)


def _enforce_address_split(
    entities: List[Entity], addresses: dict, next_index: int
) -> tuple[List[Entity], int]:
    """Deterministic guard: an entity may not span incompatible delivery points.

    The city key (``country|city|house``) deliberately unions two blocks whose
    zips disagree, so that one transposed digit does not split a door. That
    widening has to be paid for somewhere: without this guard it would also let
    the model merge two genuinely different doors that happen to share a house
    number in one city. Here the pair is separated again and both sides routed
    to review — never silently kept together, and never silently dropped.

    Splits off the members incompatible with the entity's FIRST signature, so
    the outcome does not depend on which member the LLM happened to name first.
    """
    from dedup.address import address_compatible

    def rows_of(sig: Signature) -> List[Any]:
        return [addresses[r] for r in sig.row_ids if r in addresses]

    result: List[Entity] = []
    for ent in entities:
        if len(ent.signatures) < 2:
            result.append(ent)
            continue
        anchor = rows_of(ent.signatures[0])
        kept: List[Signature] = [ent.signatures[0]]
        split: List[Signature] = []
        for sig in ent.signatures[1:]:
            others = rows_of(sig)
            incompatible = bool(anchor) and bool(others) and all(
                address_compatible(a, b) == "incompatible"
                for a in anchor
                for b in others
            )
            (split if incompatible else kept).append(sig)
        if not split:
            result.append(ent)
            continue
        logger.warning(
            "Dedup: entity %s spans incompatible delivery points; splitting "
            "%d signature(s) to manual_review", ent.entity_id, len(split),
        )
        ent.signatures = kept
        result.append(ent)
        for sig in split:
            sig.uncertain = True
            sig.merge_reasoning = (
                "Split: the delivery points are incompatible (different house "
                "number, or postcodes more than one edit apart); routed to "
                "manual review."
            )
            result.append(Entity(
                entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
            ))
            next_index += 1
    return result, next_index


def _reasoning_disowns_membership(entities: List[Entity]) -> bool:
    """True when a MERGED entity (>=2 signatures) carries reasoning that
    explicitly asserts a non-merge — a self-contradicting verdict. The INVARIANT
    (asserted at the block seam): a record's stored reasoning may never assert
    non-merge of a signature it belongs to."""
    for ent in entities:
        if len(ent.signatures) < 2 or not ent.reasoning:
            continue
        text = ent.reasoning.casefold()
        if any(marker in text for marker in _NONMERGE_MARKERS):
            return True
    return False


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
        anchor = signatures[0] if signatures else None
        payload = [_signature_payload(s, anchor) for s in bucket]
        if v2_name2():
            evidence = [
                (left.signature_id, right.signature_id, pair_evidence(left, right))
                for index, left in enumerate(bucket)
                for right in bucket[index + 1:]
            ]
            user_prompt = build_mode_a_user_prompt_v2(payload, evidence)
        else:
            user_prompt = build_mode_a_user_prompt(payload)

        async with semaphore:
            call = await llm.adjudicate(system_prompt(), user_prompt)
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
                entities.append(Entity(
                    entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
                ))
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
            confidence = _confidence_to_float(ent_obj.get("confidence"))
            reasoning = (ent_obj.get("reasoning") or None)
            # The partition rationale is this group's merge rationale — attach
            # it to every member so each output row carries the reasoning for
            # its own membership.
            for member in members:
                member.merge_reasoning = reasoning
                member.merge_confidence = confidence
            entities.append(Entity(
                entity_id=f"e{next_index}",
                signatures=members,
                institution=(ent_obj.get("institution") or None),
                department=(ent_obj.get("department") or None),
                confidence=confidence,
                reasoning=reasoning,
                adjudicated=True,
            ))
            next_index += 1

        # v2 gives an uncertain verdict somewhere to put its reason. Without
        # it the instruction "list both signatures and give the reason" has no
        # slot to write into, and a reviewer opening the workbook on a
        # link-for-review row finds an empty Reasoning cell — which by the
        # emission contract means "never nominated", the opposite of the truth.
        uncertain_reasons = parsed.get("uncertain_reasons") or {}
        if not isinstance(uncertain_reasons, dict):
            uncertain_reasons = {}
        _record_institution_relation(by_id, parsed.get("institution_relation"))
        for sid in parsed.get("uncertain_signature_ids", []) or []:
            sig = by_id.get(sid)
            if sig is None or sid in assigned:
                continue
            assigned.add(sid)
            decision_counts["uncertain"] += 1
            sig.uncertain = True
            reason = uncertain_reasons.get(sid)
            if isinstance(reason, str) and reason.strip():
                sig.merge_reasoning = reason.strip()
            entities.append(Entity(
                entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
            ))
            next_index += 1

        # Any signature the LLM dropped from its partition: treat as uncertain
        # so it surfaces for review rather than vanishing.
        for sig in bucket:
            if sig.signature_id not in assigned:
                decision_counts["missing"] += 1
                sig.uncertain = True
                entities.append(Entity(
                    entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
                ))
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

        anchor = signatures[0]
        candidate = _signature_payload(sig, anchor)
        canonical_payload = [
            {
                "entity_id": e.entity_id,
                "institution": e.institution or e.signatures[0].name1,
                "department": e.department or e.signatures[0].name2,
                **{k: v for k, v in _signature_payload(e.signatures[0], anchor).items()
                   if k != "signature_id"},
                "ror_id": next((s.ror_id for s in e.signatures if s.ror_id), "none"),
                "lei_id": next((s.lei_id for s in e.signatures if s.lei_id), "none"),
            }
            for e in compatible
        ]
        if v2_name2():
            evidence = [
                (sig.signature_id, e.entity_id, pair_evidence(sig, e.signatures[0]))
                for e in compatible
            ]
            user_prompt = build_mode_b_user_prompt_v2(
                candidate, canonical_payload, evidence
            )
        else:
            user_prompt = build_mode_b_user_prompt(candidate, canonical_payload)

        async with semaphore:
            call = await llm.adjudicate(system_prompt(), user_prompt, max_tokens=1000)
        stats.llm_calls += 1
        _record_call_stats(stats, call)

        parsed = parse_json_object(call.raw) if call.error is None else None
        if parsed is not None:
            _record_institution_relation({sig.signature_id: sig},
                                         {sig.signature_id: parsed.get("institution_relation")})
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
                # Stamp the decision on the joining signature so its output row
                # carries the rationale for ITS OWN membership — never a blob
                # overwritten across the whole entity as members accrue.
                sig.merge_reasoning = reasoning
                sig.merge_confidence = confidence
                target.signatures.append(sig)
                # Entity-level confidence/reasoning is the latest merge, kept as
                # a fallback for the seed signature only.
                target.confidence = confidence
                target.reasoning = reasoning
                target.adjudicated = True
                _log_llm_call("B", stats, call, {"match": 1})
                continue
            # Claimed a match to an unknown/incompatible id → treat as new.
            logger.warning(
                "Dedup Mode B: match to unknown entity_id %r for signature %s; "
                "treating as new", parsed.get("matched_entity_id"), sig.signature_id,
            )
            sig.merge_reasoning = reasoning
            canonicals.append(Entity(
                entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
                reasoning=reasoning,
            ))
            next_index += 1
            _log_llm_call("B", stats, call, {"new": 1})
        elif decision == "new":
            # Adjudicated as a distinct entity — record the reason so the row is
            # never left with an empty (never-nominated) Reasoning field.
            sig.merge_reasoning = reasoning
            canonicals.append(Entity(
                entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
                reasoning=reasoning,
            ))
            next_index += 1
            _log_llm_call("B", stats, call, {"new": 1})
        else:
            # "uncertain" or anything unrecognised → own entity, flagged.
            sig.uncertain = True
            sig.merge_reasoning = reasoning
            sig.merge_confidence = confidence
            ent = Entity(
                entity_id=f"e{next_index}",
                signatures=[sig],
                confidence=confidence,
                reasoning=reasoning,
                adjudicated=True,
            )
            canonicals.append(ent)
            next_index += 1
            _log_llm_call("B", stats, call, {"uncertain": 1})

    return canonicals


# ---------------------------------------------------------------------------
# Residue candidate pass — nominate + adjudicate the pairs bucketing skipped
# ---------------------------------------------------------------------------

def _entity_unit(index: int, ent: Entity) -> CandidateUnit:
    return CandidateUnit(
        index=index,
        name=ent.signatures[0].name1 if ent.signatures else "",
        ror_id=next((s.ror_id for s in ent.signatures if s.ror_id), None),
        lei_id=next((s.lei_id for s in ent.signatures if s.lei_id), None),
        has_name2=ent.has_name2,
        adjudicated=ent.adjudicated,
        row_ids=tuple(ent.row_ids),
        aliases=tuple(
            alias for sig in ent.signatures for alias in sig.aliases
        ),
        operating_name=next(
            (s.operating_name for s in ent.signatures if s.operating_name), None),
        suggested_name=next(
            (s.suggested_name for s in ent.signatures if s.suggested_name), None),
    )


def _address_gate(addresses: dict) -> Any:
    """A residue eligibility gate over parsed delivery points, or None.

    ``None`` when the flag is off, so ``generate_candidate_pairs`` takes the v1
    path untouched rather than one that merely happens to answer the same.
    """
    from dedup.address import any_compatible

    def gate(x: CandidateUnit, y: CandidateUnit) -> bool:
        return any_compatible(
            [addresses[r] for r in x.row_ids if r in addresses],
            [addresses[r] for r in y.row_ids if r in addresses],
        )

    return gate


def _signature_payload(sig: Signature, anchor: Optional[Signature] = None) -> dict:
    """What one record looks like to the model.

    v1's five fields, or v2's twelve. The extra seven are not decoration: five
    of them are columns the file route used to drop before adjudication ever
    saw them (C.4), ``aliases`` carries the trading name or opaque code the
    slot classifier lifted out of the department, and ``street_match`` reports
    the one part of the address that blocking did NOT already settle.

    ``street_match`` is stated relative to *anchor* — the block's first
    signature — so every record in one prompt is described against the same
    reference rather than against whichever neighbour came before it.
    """
    if not v2_name2():
        return {
            "signature_id": sig.signature_id,
            "name1": sig.name1,
            "name2": sig.name2,
            "ror_id": sig.ror_id or "none",
            "lei_id": sig.lei_id or "none",
        }

    label = "unknown"
    if anchor is not None and sig.address is not None and anchor.address is not None:
        from dedup.address import street_match

        label = street_match(sig.address, anchor.address)

    return {
        "signature_id": sig.signature_id,
        "institution": sig.institution or sig.name1,
        "department": sig.department,
        "aliases": list(sig.aliases),
        "operating_name": sig.operating_name or "none",
        "suggested_name": sig.suggested_name or "none",
        "record_type": sig.record_type or "unknown",
        "ror_id": sig.ror_id or "none",
        "lei_id": sig.lei_id or "none",
        "street_match": label,
        "hints": list(sig.hints),
    }


def _entity_prompt_fields(ent: Entity, anchor: Optional[Signature] = None) -> dict:
    sig = ent.signatures[0]
    payload = _signature_payload(sig, anchor)
    payload["ror_id"] = next((s.ror_id for s in ent.signatures if s.ror_id), "none")
    payload["lei_id"] = next((s.lei_id for s in ent.signatures if s.lei_id), "none")
    return payload


async def _adjudicate_residue(
    block_id: str,
    entities: List[Entity],
    llm: DedupLLM,
    semaphore: asyncio.Semaphore,
    stats: BlockStats,
    cfg: _CandidateConfig,
    address_gate: Any = None,
    extra_rules: bool = False,
) -> List[Entity]:
    """Nominate residue pairs (ID / name / token) the bucketed pass never
    compared, adjudicate each via a pairwise LLM call, and apply the verdicts.

    Nomination never merges — the LLM decides. Every nominated pair records
    reasoning on BOTH sides, including rejects. On exceeding the candidate cap
    the whole block is routed to manual_review (deterministic priority already
    put id-convergence pairs first). Fully deterministic ordering.
    """
    if len(entities) < 2:
        return entities

    units = [_entity_unit(i, e) for i, e in enumerate(entities)]
    candidates = generate_candidate_pairs(
        units,
        name_threshold=cfg.name_threshold,
        token_threshold=cfg.token_threshold,
        address_gate=address_gate,
        extra_rules=extra_rules,
    )
    stats.candidates_generated += len(candidates)
    for c in candidates:
        stats.candidates_by_rule[c.rule] += 1

    if len(candidates) > cfg.max_candidates:
        stats.candidate_cap_exceeded = True
        logger.warning(
            "Dedup: block %s generated %d candidate pairs > cap %d; routing the "
            "whole block to manual_review", block_id, len(candidates), cfg.max_candidates,
        )
        marker = (
            f"candidate_cap_exceeded: {len(candidates)} candidate pairs exceed the "
            f"per-block cap of {cfg.max_candidates}; block routed to manual review"
        )
        for ent in entities:
            ent.adjudicated = True
            for sig in ent.signatures:
                sig.uncertain = True
                if sig.merge_reasoning is None:
                    sig.merge_reasoning = marker
        return entities

    # Union-find over entity indices; lowest index stays root for a stable id.
    parent = list(range(len(entities)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            lo, hi = (rx, ry) if rx < ry else (ry, rx)
            parent[hi] = lo

    nominated: set[int] = set()
    distinct_note: dict[int, str] = {}

    for c in candidates:
        nominated.add(c.a)
        nominated.add(c.b)
        if find(c.a) == find(c.b):
            continue  # already merged transitively — don't re-ask
        canon_ent, cand_ent = entities[c.a], entities[c.b]
        anchor = entities[0].signatures[0] if entities[0].signatures else None
        canonical = {
            "entity_id": canon_ent.entity_id,
            "institution": canon_ent.institution or canon_ent.signatures[0].name1,
            "department": canon_ent.department or canon_ent.signatures[0].name2,
            **{k: v for k, v in _entity_prompt_fields(canon_ent, anchor).items()
               if k != "signature_id"},
        }
        if v2_name2():
            user_prompt = build_mode_b_user_prompt_v2(
                _entity_prompt_fields(cand_ent, anchor), [canonical],
                [(
                    cand_ent.signatures[0].signature_id,
                    canon_ent.entity_id,
                    pair_evidence(cand_ent.signatures[0], canon_ent.signatures[0]),
                )],
            )
        else:
            user_prompt = build_mode_b_user_prompt(
                _entity_prompt_fields(cand_ent, anchor), [canonical]
            )
        async with semaphore:
            call = await llm.adjudicate(system_prompt(), user_prompt, max_tokens=1000)
        stats.llm_calls += 1
        _record_call_stats(stats, call)

        canon_name = canon_ent.signatures[0].name1
        cand_name = cand_ent.signatures[0].name1
        # Which rule put this pair in front of the model. Recorded in the
        # Reasoning so a reviewer can tell an id convergence from a guess at an
        # acronym — the two deserve very different amounts of trust.
        rule = f" [{c.rule}]" if v2_name2() else ""
        canon_ent.adjudicated = True
        cand_ent.adjudicated = True

        parsed = parse_json_object(call.raw) if call.error is None else None
        if parsed is None:
            # Ambiguous / unusable verdict → route both sides to manual_review.
            stats.errors += 1
            for e in (canon_ent, cand_ent):
                for s in e.signatures:
                    s.uncertain = True
            _log_llm_call("R", stats, call, {"error": 1})
            continue

        decision = str(parsed.get("decision", "")).strip().lower()
        confidence = _confidence_to_float(parsed.get("confidence"))
        reasoning = parsed.get("reasoning") or None
        tail = f" ({reasoning})" if reasoning else ""

        if decision == "match":
            note = f"adjudicated vs {canon_name}{rule}: merged{tail}"
            for s in cand_ent.signatures:
                s.merge_reasoning = note
                s.merge_confidence = confidence
            union(c.a, c.b)
            _log_llm_call("R", stats, call, {"match": 1})
        elif decision in ("new", "distinct"):
            distinct_note[c.b] = f"adjudicated vs {canon_name}{rule}: distinct{tail}"
            distinct_note[c.a] = f"adjudicated vs {cand_name}{rule}: distinct{tail}"
            stats.rejected_with_reasoning += 1
            _log_llm_call("R", stats, call, {"distinct": 1})
        else:
            # uncertain / unrecognised → manual_review, reasoning recorded.
            for e in (canon_ent, cand_ent):
                for s in e.signatures:
                    s.uncertain = True
                    if s.merge_reasoning is None:
                        s.merge_reasoning = reasoning
            _log_llm_call("R", stats, call, {"uncertain": 1})

    # Rebuild entities from the union-find groups (deterministic root order).
    groups: dict[int, List[int]] = {}
    for i in range(len(entities)):
        groups.setdefault(find(i), []).append(i)

    rebuilt: List[Entity] = []
    for root in sorted(groups):
        idxs = groups[root]
        if len(idxs) == 1:
            ent = entities[idxs[0]]
            note = distinct_note.get(idxs[0])
            # A nominated-but-unmerged reject records its distinct rationale so
            # its Reasoning is never empty.
            if note is not None:
                for s in ent.signatures:
                    if s.merge_reasoning is None:
                        s.merge_reasoning = note
                if ent.reasoning is None:
                    ent.reasoning = note
            rebuilt.append(ent)
            continue
        members = [entities[i] for i in idxs]
        rebuilt.append(Entity(
            entity_id=entities[root].entity_id,
            signatures=[s for e in members for s in e.signatures],
            institution=entities[root].institution,
            department=entities[root].department,
            confidence=next((e.confidence for e in members if e.confidence is not None), None),
            reasoning=next((e.reasoning for e in members if e.reasoning), None),
            adjudicated=True,
        ))
    return rebuilt


# ---------------------------------------------------------------------------
# STEP C — emit clusters, fan out to rows
# ---------------------------------------------------------------------------

#: Why a cluster built out of address-less rows is not asserted outright.
UNVERIFIED_DELIVERY_POINT = "unverified delivery point"


_RELATIONS = ("same", "different", "uncertain")


def _record_institution_relation(by_id: dict, reported: Any) -> None:
    """Store the model's institution verdict on each signature it named.

    Tolerant by construction: an absent or malformed value leaves the
    signature's relation ``None``, which the linker reads as "never asked"
    rather than as "different". A field the model forgot must not become a
    silent assertion that two organisations are unrelated.
    """
    if isinstance(reported, str):
        reported = {sid: reported for sid in by_id}
    if not isinstance(reported, dict):
        return
    for sid, value in reported.items():
        sig = by_id.get(sid)
        if sig is None or not isinstance(value, str):
            continue
        value = value.strip().lower()
        if value in _RELATIONS:
            sig.institution_relation = value


def _institution_links(
    entities: List[Entity], cross_block: bool = False
) -> tuple[dict, List[tuple]]:
    """Group signatures into institution families, and find conflicts.

    The Link ID answers "same ORGANISATION?" and the Cluster ID answers "same
    RECORD?", and the two are computed independently on purpose. Deriving the
    link from the merge outcome would make it say nothing the Cluster ID does
    not already say — and the cases worth linking are exactly the ones that did
    NOT merge: a company and its research institute, a parent and its
    subsidiary, a university and the LLC that runs its warehouse.

    A pair is one family when any of these holds:

    * they share a ROR or LEI — a registry says so;
    * a deterministic ``evidence`` line fires AND the model called the
      institution the "same" on either side;
    * a deterministic line fires AND the model was "uncertain" on either side.

    A CONFLICT is the fourth case: deterministic evidence (or a shared registry
    id) says one organisation and the model says "different". That is a
    disagreement between two sources that both have standing, and it is the one
    thing a steward must see — so it links AND routes to review. A flag with no
    connection is useless to whoever opens the workbook: two rows marked for
    review and nothing saying they are about each other.

    ``cross_block`` restricts the pair test to the registry arm. An institution
    family is not a property of one delivery point — HGST at Great Oaks Pkwy
    and HGST at Yerba Buena Rd are one organisation, and a Link ID that stopped
    at the block boundary would say so twice and connect neither. The model,
    however, only ever compares within a block, so across blocks the shared ROR
    or LEI is the only evidence there is; the within-block links then join
    those families transitively.

    Returns ``({row_id: link_id}, [(signature, counterpart), …])``.
    """
    signatures = [sig for ent in entities for sig in ent.signatures]
    if len(signatures) < 2:
        return {}, []

    # Keyed on POSITION, never on signature_id: those restart at "s1" in every
    # block (dedup/signatures.py), so across blocks they collide and every
    # block's first signature is unioned with every other block's — which
    # produced one Link ID for the entire file.
    parent = list(range(len(signatures)))

    def find(key: int) -> int:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    conflicts: List[tuple] = []
    for index, left in enumerate(signatures):
        for offset, right in enumerate(signatures[index + 1:], start=index + 1):
            shared_id = _ids_converge_pair(left, right)
            if cross_block:
                if shared_id:
                    union(index, offset)
                continue
            evidence = bool(pair_evidence(left, right))
            if not shared_id and not evidence:
                continue
            relations = {left.institution_relation, right.institution_relation}
            # Every case that reached here links: agreement, uncertainty, and
            # disagreement alike. What the disagreement changes is the routing,
            # not whether the pair is connected.
            union(index, offset)
            if "different" in relations:
                # Evidence or a registry id against the model's own verdict.
                for sig in (left, right):
                    if sig.institution_relation == "different":
                        conflicts.append((sig, left if sig is right else right))

    groups: dict = {}
    for position, sig in enumerate(signatures):
        groups.setdefault(find(position), []).append(sig)

    links: dict = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        row_ids = [rid for sig in members for rid in sig.row_ids]
        link_id = link_hash(row_ids)
        for rid in row_ids:
            links[rid] = link_id
    return links, conflicts


def _merge_link_maps(local: dict, across: dict) -> dict:
    """Join the within-block families with the across-block ones.

    Two rows are one family if either map says so, so the two partitions are
    unioned rather than one overriding the other: HGST's two "HGST Inc" rows
    reach each other only through their local links to a "Hitachi Global
    Storage Technologies" row, and those two reach each other only through a
    shared ROR.
    """
    parent: dict = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for mapping in (local, across):
        groups: dict = {}
        for row_id, link_id in mapping.items():
            groups.setdefault(link_id, []).append(row_id)
        for members in groups.values():
            for other in members[1:]:
                union(members[0], other)

    families: dict = {}
    for row_id in parent:
        families.setdefault(find(row_id), []).append(row_id)

    merged: dict = {}
    for members in families.values():
        if len(members) < 2:
            continue
        link_id = link_hash(members)
        for row_id in members:
            merged[row_id] = link_id
    return merged


def _ids_converge_pair(left: Signature, right: Signature) -> bool:
    return bool(
        (left.lei_id and left.lei_id == right.lei_id)
        or (left.ror_id and left.ror_id == right.ror_id)
    )


def _flag_institution_conflicts(conflicts: List[tuple]) -> None:
    """Route a model-vs-evidence disagreement to a human, with the reason.

    Not a merge and not a split: the entity structure is left exactly as the
    model decided it. Only the routing changes, because the disagreement is the
    finding — "the registry says these are one organisation and the model says
    they are two" is precisely a steward's question, and resolving it either
    way in code would be inventing an answer neither source gave.
    """
    for sig, _counterpart in conflicts:
        sig.uncertain = True




def _emit_rows(
    block_id: str,
    entities: List[Entity],
    model: str,
    model_version: str,
    stats: BlockStats,
    unverified_block: bool = False,
    link_ids: Optional[dict] = None,
) -> List[DedupResultRow]:
    """Build one output row per input row.

    Each cluster's id is a content hash of its member row_ids (see
    ``cluster_hash``) — already globally unique and stable, so no post-hoc
    renumbering is needed.

    ``unverified_block`` (v2) marks a block whose rows named no usable house
    number. Such rows may still be duplicates of each other, and the cluster
    id says so, but nothing established the delivery point they share — so the
    cluster is routed to review rather than asserted. A singleton is left
    alone: there is no claim in it to qualify.
    """
    out: List[DedupResultRow] = []

    for ent in entities:
        row_ids = ent.row_ids
        clustered = len(row_ids) >= 2
        if clustered:
            cluster_id: Optional[str] = cluster_hash(row_ids)
            stats.clusters += 1
        else:
            cluster_id = None

        for sig in ent.signatures:
            for rid in sig.row_ids:
                demoted = False
                if sig.uncertain:
                    routing = "manual_review"
                    stats.rows_manual_review += 1
                elif cluster_id is not None and unverified_block:
                    routing = "manual_review"
                    demoted = True
                    stats.rows_manual_review += 1
                elif cluster_id is not None:
                    routing = "cluster"
                    stats.rows_clustered += 1
                else:
                    routing = "unique"
                    stats.rows_unique += 1

                # REASONING is an ADJUDICATION signal: surface it for any entity
                # the LLM decided (merged, rejected, or uncertain) so a rejected
                # candidate still records why. An empty Reasoning therefore means
                # exactly "never nominated" (a deterministic collapse / lone
                # bucket that never reached the LLM).
                if ent.adjudicated or sig.uncertain:
                    reasoning = (
                        sig.merge_reasoning
                        if sig.merge_reasoning is not None
                        else ent.reasoning
                    )
                else:
                    reasoning = None
                # CONFIDENCE is a MERGE signal: surface it only for a genuine
                # merge (>=2 signatures) or an uncertain row — never for a pure
                # identical-collapse or a distinct verdict, where a spurious
                # confidence would wrongly trip the election confidence gate.
                if ent.llm_merged or sig.uncertain:
                    confidence = (
                        sig.merge_confidence
                        if sig.merge_confidence is not None
                        else ent.confidence
                    )
                else:
                    confidence = None

                if demoted:
                    reasoning = (
                        f"{UNVERIFIED_DELIVERY_POINT}: {reasoning}"
                        if reasoning
                        else UNVERIFIED_DELIVERY_POINT
                    )

                out.append(DedupResultRow(
                    row_id=rid,
                    block_id=block_id,
                    cluster_id=cluster_id,
                    link_id=(link_ids or {}).get(rid),
                    routing=routing,
                    llm_flag=ent.llm_merged,
                    signature_id=sig.signature_id,
                    confidence=confidence,
                    reasoning=reasoning,
                    model=model,
                    model_version=model_version,
                    prompt_version=prompt_version(),
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
            "prompt_version": prompt_version(),
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
    cfg: _CandidateConfig,
    unverified_block: bool = False,
) -> tuple[List[DedupResultRow], BlockStats]:
    stats = BlockStats(block_id=block_id, rows_in=len(rows))

    # Parsed once per block and passed down: the residue gate and the address
    # guard must read the same delivery points, and re-parsing in each would
    # be two places for them to drift apart.
    if v2_blocking():
        from dedup.address import parse_address
        addresses = {row.row_id: parse_address(row) for row in rows}
        address_gate = _address_gate(addresses)
    else:
        addresses, address_gate = {}, None

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

    # Residue widening: nominate + adjudicate the pairs the bucketed pass never
    # compared (cross-Name2-boundary, lone-bucket). Runs BEFORE the identity
    # guard so a bad name/token merge across conflicting ROR/LEI is still split.
    entities = await _adjudicate_residue(
        block_id, entities, llm, semaphore, stats, cfg, address_gate,
        # The acronym and cross-slot rules are for blocks the bucketed pass
        # cannot cover in one call — the same size test that selects Mode B.
        extra_rules=v2_name2() and n > threshold,
    )

    # 0) A merge across incompatible delivery points is split back apart. Runs
    #    before the identity guard for the same reason the residue pass does:
    #    the later guards should see the address-corrected grouping.
    if v2_blocking():
        entities, _ = _enforce_address_split(
            entities, addresses, _next_entity_index(entities)
        )

    # Deterministic verdict guards, applied uniformly to both modes' output.
    # 1) A merge across different non-empty ROR/LEI ids is split to
    #    manual_review (a hard identifier conflict is a strong split signal).
    entities, _, _ = _enforce_identity_split(entities, _next_entity_index(entities))
    # 2) INVARIANT: a merged entity's reasoning may never assert non-merge of a
    #    member. If it does, the verdict is self-contradictory — route the whole
    #    block to manual_review rather than guess toward merging.
    if _reasoning_disowns_membership(entities):
        logger.warning(
            "Dedup: reasoning contradicts membership in block %s; routing the "
            "whole block to manual_review", block_id,
        )
        for ent in entities:
            for sig in ent.signatures:
                sig.uncertain = True

    # Conflicts are block-local — they are a disagreement about a comparison
    # the model actually made, and it only compares within a block. Linking is
    # deliberately NOT block-local and happens once, at the request level.
    if v2_any():
        _, conflicts = _institution_links(entities)
        _flag_institution_conflicts(conflicts)

    out = _emit_rows(
        block_id, entities, llm.model, stats.model_version or llm.model, stats,
        unverified_block=unverified_block,
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
            "candidates_generated": stats.candidates_generated,
            "candidates_by_rule": dict(stats.candidates_by_rule),
            "rejected_with_reasoning": stats.rejected_with_reasoning,
            "candidate_cap_exceeded": stats.candidate_cap_exceeded,
        },
    )
    return out, stats, entities


def _resolve_candidate_config(settings: Any) -> _CandidateConfig:
    """Residue knobs: settings attrs > env vars > module defaults."""
    def pick(attr: str, env: str, cast, default):
        if settings is not None and getattr(settings, attr, None) is not None:
            return getattr(settings, attr)
        raw = os.getenv(env)
        if raw:
            try:
                return cast(raw)
            except ValueError:
                logger.warning("Invalid %s=%r; using default", env, raw)
        return default

    return _CandidateConfig(
        name_threshold=pick(
            "name_candidate_threshold", "NAME_CANDIDATE_THRESHOLD",
            float, DEFAULT_NAME_CANDIDATE_THRESHOLD),
        token_threshold=pick(
            "token_candidate_threshold", "TOKEN_CANDIDATE_THRESHOLD",
            float, DEFAULT_TOKEN_CANDIDATE_THRESHOLD),
        max_candidates=pick(
            "max_candidates_per_block", "MAX_CANDIDATES_PER_BLOCK",
            int, DEFAULT_MAX_CANDIDATES_PER_BLOCK),
    )


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
    cfg = _resolve_candidate_config(settings)

    blocks = build_blocks(rows)

    block_outputs = await asyncio.gather(
        *[
            _process_block(
                block.block_id, block.rows, llm, threshold, semaphore, cfg,
                unverified_block=block.unverified,
            )
            for block in blocks.values()
        ]
    )

    all_rows: List[DedupResultRow] = []
    all_entities: List[Entity] = []
    summary = DedupSummary(blocks=len(blocks), rows_in=len(rows))
    total_prompt_tokens = 0
    total_completion_tokens = 0
    candidates_generated = 0
    candidates_by_rule: Counter = Counter()
    rejected_with_reasoning = 0
    candidate_cap_exceeded_blocks = 0
    # cluster_id is a content hash of member row_ids (see cluster_hash) —
    # already globally unique and order-independent, so no cross-block
    # renumbering is required.
    for out_rows, stats, block_entities in block_outputs:
        all_rows.extend(out_rows)
        all_entities.extend(block_entities)
        summary.distinct_signatures += stats.distinct_signatures
        summary.clusters += stats.clusters
        summary.rows_clustered += stats.rows_clustered
        summary.rows_unique += stats.rows_unique
        summary.rows_manual_review += stats.rows_manual_review
        summary.llm_calls += stats.llm_calls
        summary.errors += stats.errors
        total_prompt_tokens += stats.prompt_tokens
        total_completion_tokens += stats.completion_tokens
        candidates_generated += stats.candidates_generated
        candidates_by_rule.update(stats.candidates_by_rule)
        rejected_with_reasoning += stats.rejected_with_reasoning
        candidate_cap_exceeded_blocks += int(stats.candidate_cap_exceeded)

    if v2_any():
        # One Link ID per institution family across the whole request. Within
        # each block the model's verdicts have already been applied; here the
        # families those produced are joined wherever a registry id spans two
        # blocks, so an organisation at three addresses carries one id.
        block_links, _ = _institution_links(all_entities, cross_block=True)
        local_links: dict = {}
        for out_rows, _stats, block_entities in block_outputs:
            links, _ = _institution_links(block_entities)
            local_links.update(links)
        merged = _merge_link_maps(local_links, block_links)
        for row in all_rows:
            row.link_id = merged.get(row.row_id)

    summary.candidates_generated = candidates_generated
    summary.rejected_with_reasoning = rejected_with_reasoning
    summary.candidate_cap_exceeded_blocks = candidate_cap_exceeded_blocks

    total_latency_ms = int((time.perf_counter() - request_start) * 1000)
    logger.info(
        "dedup_request",
        extra={
            "summary": summary.model_dump(),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_latency_ms": total_latency_ms,
            "prompt_version": prompt_version(),
            # Residue candidate telemetry (measures this change's volume effect).
            "candidates_generated": candidates_generated,
            "candidates_by_rule": dict(candidates_by_rule),
            "rejected_candidates_with_reasoning": rejected_with_reasoning,
            "candidate_cap_exceeded_blocks": candidate_cap_exceeded_blocks,
        },
    )

    return DedupResponse(rows=all_rows, summary=summary)
