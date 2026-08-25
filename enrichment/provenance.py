"""Fix 10 — per-field provenance and admissibility.

One principle, made mechanical:

    Every value the system writes must be attributable after the fact to the
    source that produced it and the confidence under which it was produced. A
    written value whose origin cannot be reconstructed is not admissible.

Before this module the response carried a *record-level* ``tier_used`` /
``source`` / ``confidence`` triple. That collapses a record whose Name 1 came
from ROR, whose Name 2 came from a SERP→fetch→LLM chain and whose department
domain came from Tier 2B into a single label, and it cannot represent a field
that was written twice — which is exactly what Fix 2's Tier 1 retry does to
``name1`` (Tier 3 writes it, ROR overwrites it). A final-state map cannot show
that an LLM wrote first; a log can.

Phase 1 scope
-------------
Six fields — :data:`SCOPED_FIELDS`. They are the fields where a wrong value
causes a wrong merge in Phase 2, and they carry no personal data, which keeps
the provenance store clear of a data-protection question. ``contact``,
``care_of`` and ``email`` are deliberately excluded for that reason. The write
API below is general; extending the scope is a one-line change to
:data:`SCOPED_FIELDS` plus the input-value map in :data:`INPUT_VALUE_KEYS`.

Enforcement
-----------
Recording provenance is easy to add and easy to bypass, so the point is
:class:`EnrichedRecord`: the six scoped keys cannot be assigned. ``record[
"domain"] = x`` raises :class:`UnattributedWriteError`; the only way a value
reaches one of them is :meth:`EnrichedRecord.write`, which requires a
structured :class:`Evidence` argument. :class:`api.models.EnrichmentResult`
carries the same guard for the post-finalisation stage (batch consensus).

This is stateless. The log lives on the record for the life of one batch and
ships in the JSON response; nothing here writes to a database, and nothing here
writes to Application Insights — App Insights stays operational monitoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from enrichment.confidence import (
    EvidenceSituation,
    SOURCE_GLEIF,
    SOURCE_INPUT,
    SOURCE_LLM,
    SOURCE_ROR,
    WITNESS_DOMAIN,
    WITNESS_REGISTRY,
    WITNESS_WEB,
    WITNESS_WIKIDATA,
    compute_confidence,
    render as confidence_render,
    validate_all as validate_provenance_strings,
    web_source as confidence_web_source,
)

logger = logging.getLogger(__name__)


# ── Scope ─────────────────────────────────────────────────────────────────────

#: The six Phase 1 fields. Keys as they appear on the result record.
SCOPED_FIELDS: tuple[str, ...] = (
    "name1_enriched",
    "name2_enriched",
    "domain",
    "record_type",
    "ror_id",
    "lei_id",
)

#: Short name used in the event's ``field`` and in the derived scalar column,
#: so an event reads ``{"field": "name1"}`` rather than ``name1_enriched``.
FIELD_LABELS: dict[str, str] = {
    "name1_enriched": "name1",
    "name2_enriched": "name2",
    "domain": "domain",
    "record_type": "record_type",
    "ror_id": "ror_id",
    "lei_id": "lei_id",
}

#: Where the admissibility gate finds the *input* value to revert a scoped
#: field to. ``None`` means "the field has no input counterpart" and the
#: revert target is :data:`INPUT_VALUE_DEFAULTS`.
INPUT_VALUE_KEYS: dict[str, str | None] = {
    "name1_enriched": "name1_original",
    "name2_enriched": "name2_original",
    "domain": None,
    "record_type": None,
    "ror_id": None,
    "lei_id": None,
}

INPUT_VALUE_DEFAULTS: dict[str, Any] = {
    "domain": None,
    "record_type": "unknown",
    "ror_id": None,
    "lei_id": None,
}

#: Derived-scalar column, one per scoped field.
DERIVED_SCALAR_FIELDS: dict[str, str] = {
    "name1_enriched": "name1_provenance",
    "name2_enriched": "name2_provenance",
    "domain": "domain_provenance",
    "record_type": "record_type_provenance",
    "ror_id": "ror_id_provenance",
    "lei_id": "lei_id_provenance",
}


# ── Confidence scales (Step 3) ────────────────────────────────────────────────
#
# The pipeline's single `confidence` float was fed by numbers that are not
# commensurable. 0.85 from a ROR rescore, 0.85 from a RapidFuzz ratio and 0.85
# from a model's assertion about its own output mean three different things, and
# thresholding them with one number is not sound. Every event therefore carries
# the scale its value is on, and nothing compares two values without first
# comparing their scales — see `comparable`.

#: ROR's local rescore: ``token_sort_ratio`` against the ROR record's name
#: variants, already normalised to 0.0–1.0 by ``tier1_ror``.
ROR_LOCAL = "ror_local"
#: RapidFuzz string similarity, 0–100. Tier 2A/2B name matching, local child
#: matching, GLEIF's name-verification guard, the domain ownership guard.
FUZZY_RATIO = "fuzzy_ratio"
#: A model's assertion about its own output. Not a probability of anything;
#: the float is a rendering of the model's own "high"/"medium"/"low" label
#: through :data:`_SELF_REPORT_VALUES` and must never be read as a measurement.
LLM_SELF_REPORTED = "llm_self_reported"
#: A rule fired. The value is 1.0 by construction and means "this rule matched",
#: not "this value is 100% likely" — passthrough, preprocessing, output casing,
#: the classifier's ranked evidence.
DETERMINISTIC = "deterministic"
#: A registry answered with an identifier it owns. Exact by definition: ROR's
#: ``ror_id`` and GLEIF's ``lei_id`` are not scored, they are returned.
REGISTRY_EXACT = "registry_exact"
#: Copied from another record in the same batch (Fix 6). The value is the
#: donor's own confidence and is only as good as the donor's scale, which the
#: evidence_ref names alongside the donor record id.
INHERITED = "inherited"
#: The input value was kept AND independently corroborated — an
#: ownership-guard-passing domain that ties a site to this Name 1, or a page
#: read that states this organisation's identity (Fix 3). The value is 1.0 by
#: construction: the corroborating check either held or it did not, and the
#: check's own score lives on the event that recorded it (the domain write, the
#: page-read event). Band: ``verified``.
INPUT_CORROBORATED = "input_corroborated"
#: The input value was kept AND an independently generated canonicalisation
#: proposal reproduced it under ``normalize_key`` — the model, asked what the
#: organisation is called without being shown a candidate answer, returned the
#: string the record already held. That is agreement from a second source, not
#: corroboration by evidence, so it is a scale of its own rather than a band of
#: :data:`INPUT_CORROBORATED`. Band: ``confirmed``.
INPUT_SELF_CONSISTENT = "input_self_consistent"
#: No confidence attaches to this write.
NO_SCALE = "none"

CONFIDENCE_SCALES: tuple[str, ...] = (
    ROR_LOCAL, FUZZY_RATIO, LLM_SELF_REPORTED,
    DETERMINISTIC, REGISTRY_EXACT, INHERITED,
    INPUT_CORROBORATED, INPUT_SELF_CONSISTENT, NO_SCALE,
)

#: The self-reported label → float rendering. Documented, fixed, and NOT a
#: calibration: it exists so the event carries a sortable number alongside the
#: label, which is preserved verbatim in ``evidence_ref["self_reported"]``.
_SELF_REPORT_VALUES: dict[str, float] = {
    "high": 0.9, "medium": 0.7, "low": 0.4, "none": 0.0,
}


def self_reported_value(label: str | None) -> float | None:
    """Float rendering of a model's own confidence label."""
    if not label:
        return None
    return _SELF_REPORT_VALUES.get(str(label).strip().lower())


def comparable(a: str | None, b: str | None) -> bool:
    """True when two confidence values may be compared at all.

    The whole of Step 3 in one function: values are comparable only when they
    are on the same scale. A caller that needs to rank across scales has to
    rank the KIND of evidence, not the floats — which is what
    :func:`weak_fields` does, and it does it on the producer rather than on
    any number.
    """
    return bool(a) and a == b


# ── Confidence bands, per scale ───────────────────────────────────────────────
#
# NO LONGER ON THE EXPORT PATH. The band used to be the third component of the
# derived scalar; Provenance Scheme B replaced it, because a slot holding
# "self_high" for one producer and "exact" for another is not a confidence, it
# is three vocabularies sharing a column. What a reader needs is in
# `enrichment.confidence`; what an auditor needs — the scale, the raw value and
# the rule id — is on every event and in the trace, which is strictly more than
# the band ever carried.
#
# Retained as a human-readable rendering of one (scale, value) pair for
# diagnostics and for the tests that pin the banding thresholds. Nothing in the
# pipeline calls it, and nothing should call it to decide anything.

def confidence_band(scale: str | None, value: float | None) -> str:
    """The scale-namespaced band for one (scale, value) pair.

    Diagnostic only — see the note above. This is not what any provenance
    column contains.
    """
    if scale == REGISTRY_EXACT:
        return "exact"
    if scale == ROR_LOCAL:
        if value is None:
            return "unscored"
        if value >= 0.999:
            return "exact"
        if value >= 0.90:
            return "high"
        if value >= 0.75:
            return "medium"
        return "low"
    if scale == FUZZY_RATIO:
        if value is None:
            return "unscored"
        if value >= 99.5:
            return "exact"
        if value >= 90:
            return "high"
        if value >= 75:
            return "medium"
        return "low"
    if scale == LLM_SELF_REPORTED:
        if value is None:
            return "self_unstated"
        if value >= 0.85:
            return "self_high"
        if value >= 0.6:
            return "self_medium"
        return "self_low"
    if scale == DETERMINISTIC:
        return "rule"
    if scale == INHERITED:
        return "inherited"
    # Fix 2's three unchanged states. `rule` (DETERMINISTIC) remains the third:
    # the input was kept and nothing came back, which is what `input:1:rule`
    # already meant and still means.
    if scale == INPUT_CORROBORATED:
        return "verified"
    if scale == INPUT_SELF_CONSISTENT:
        return "confirmed"
    return "none"


# ── Errors ────────────────────────────────────────────────────────────────────

class UnattributedWriteError(RuntimeError):
    """A scoped field was assigned directly, bypassing :meth:`write`.

    Raised by :meth:`EnrichedRecord.__setitem__` and by
    ``EnrichmentResult.__setattr__``. There is no way to write a scoped field
    that does not carry evidence — which is what makes the principle a property
    of the code rather than a convention.
    """


class MissingEvidenceError(ValueError):
    """:meth:`write` was called without usable evidence."""


# ── The evidence model (Step 2) ───────────────────────────────────────────────

@dataclass(frozen=True)
class Evidence:
    """What produced one value, and under what confidence.

    ``producer_chain``
        The ordered list of tools that produced this ONE value —
        ``("serp", "fetch", "llm_tier2b")`` for a Tier 2B department. A chain
        is not a list of competing sources; it is one value produced by several
        tools in sequence. Competing sources are separate events on separate
        ``seq`` numbers.
    ``evidence_ref``
        The thing a reviewer opens to check the claim: a ``ror_id`` / ``lei_id``
        string, a ``{"source_url", "retrieved_at"}`` mapping for a web-derived
        value, a ``{"deployment", "prompt_version", "temperature"}`` mapping for
        an LLM write, or a ``{"donor_record_id", …}`` mapping for a batch
        consensus inheritance.
    ``kind``
        ``"write"`` — a producer decided this value.
        ``"transform"`` — a deterministic rule reshaped a value already
        present (output casing, abbreviation expansion, legal-suffix collapse).
        A transform never becomes the attribution in the derived scalar: it did
        not produce the value, it restyled it.
        ``"revert"`` — the admissibility gate put the input value back.
    """

    producer_chain: tuple[str, ...]
    tier: int | None = None
    confidence_scale: str = NO_SCALE
    confidence_value: float | None = None
    evidence_ref: Any = None
    rule_id: str | None = None
    kind: str = "write"

    def __post_init__(self) -> None:
        if not self.producer_chain:
            raise MissingEvidenceError(
                "Evidence.producer_chain must name at least one producer",
            )
        if self.confidence_scale not in CONFIDENCE_SCALES:
            raise MissingEvidenceError(
                f"unknown confidence_scale {self.confidence_scale!r}; "
                f"expected one of {CONFIDENCE_SCALES}",
            )
        if self.kind not in ("write", "transform", "revert"):
            raise MissingEvidenceError(f"unknown evidence kind {self.kind!r}")

    @property
    def producer(self) -> str:
        """The tool that produced the value — the last link of the chain."""
        return self.producer_chain[-1]


@dataclass
class ProvenanceEvent:
    """One write to one scoped field."""

    seq: int
    field: str
    old_value: Any
    new_value: Any
    producer_chain: tuple[str, ...]
    evidence_ref: Any = None
    confidence_scale: str = NO_SCALE
    confidence_value: float | None = None
    rule_id: str | None = None
    tier: int | None = None
    kind: str = "write"

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "field": self.field,
            "kind": self.kind,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "producer_chain": list(self.producer_chain),
            "evidence_ref": self.evidence_ref,
            "confidence_scale": self.confidence_scale,
            "confidence_value": self.confidence_value,
            "rule_id": self.rule_id,
            "tier": self.tier,
        }


@dataclass
class RejectedCandidate:
    """A candidate a GUARD refused (Step 4).

    Only guard rejections are logged. The full candidate list from every lookup
    multiplies volume for little value; a guard rejection is the case worth
    being able to defend afterwards, because the pipeline had a confident
    answer and deliberately refused it.
    """

    seq: int
    field: str
    candidate: Any
    guard: str
    reason: str | None = None
    producer_chain: tuple[str, ...] = ()
    evidence_ref: Any = None
    confidence_scale: str = NO_SCALE
    confidence_value: float | None = None
    tier: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "field": self.field,
            "candidate": self.candidate,
            "guard": self.guard,
            "reason": self.reason,
            "producer_chain": list(self.producer_chain),
            "evidence_ref": self.evidence_ref,
            "confidence_scale": self.confidence_scale,
            "confidence_value": self.confidence_value,
            "tier": self.tier,
        }


#: The named guards. A rejection logged under any other name is a bug — these
#: six are the decision-relevant refusals (Step 4).
GUARD_ROR_COUNTRY = "ror_country"
GUARD_DISTINCTIVE_TOKEN = "distinctive_token"
GUARD_IDENTIFIER_TOKEN = "identifier_token"
GUARD_DOMAIN_OWNERSHIP = "domain_ownership"
GUARD_GLEIF_NAME = "gleif_name_verification"
#: Fix 3 — the candidate site's own page names a different organisation. The
#: only guard that refuses a domain on evidence read from the domain itself,
#: and the only one that can refuse a domain the ownership guard already
#: accepted (johnsoncontrols.com for "AB Controls, Inc.").
GUARD_PAGE_IDENTITY = "page_identity"

GUARDS: tuple[str, ...] = (
    GUARD_ROR_COUNTRY, GUARD_DISTINCTIVE_TOKEN, GUARD_IDENTIFIER_TOKEN,
    GUARD_DOMAIN_OWNERSHIP, GUARD_GLEIF_NAME, GUARD_PAGE_IDENTITY,
)

#: Rejections retained per field per record. Beyond it only the count is kept.
MAX_REJECTIONS_PER_FIELD = 3


# ── Evidence constructors ─────────────────────────────────────────────────────
#
# Convenience only: every one of these returns an ordinary `Evidence`, and
# `EnrichedRecord.write` is the single write path regardless of which is used.

#: Which scale each registry's match score is on. ROR rescores locally against
#: the record's name variants and returns 0.0-1.0; GLEIF's name-verification
#: guard is a RapidFuzz ratio on 0-100. Two numbers, two scales, never
#: comparable — which is the whole of Step 3 in one dict.
REGISTRY_SCALES: dict[str, str] = {"ror": ROR_LOCAL, "gleif": FUZZY_RATIO}


def registry_evidence(
    registry: str,
    identifier: str | None,
    *,
    tier: int = 1,
    score: float | None = None,
    scale: str | None = None,
    rule_id: str | None = None,
    kind: str = "write",
) -> Evidence:
    """A value taken from ROR or GLEIF. ``identifier`` is the evidence_ref —
    the registry id a reviewer can open.

    With a ``score`` the event carries the registry's own match score on that
    registry's own scale. Without one the write is ``registry_exact``: an
    identifier is not scored, it is returned.
    """
    if scale is None:
        scale = (
            REGISTRY_SCALES.get(registry, FUZZY_RATIO)
            if score is not None else REGISTRY_EXACT
        )
    return Evidence(
        producer_chain=(registry,),
        tier=tier,
        confidence_scale=scale,
        confidence_value=1.0 if scale == REGISTRY_EXACT else score,
        evidence_ref=identifier,
        rule_id=rule_id,
        kind=kind,
    )


def llm_evidence(
    producer_chain: Sequence[str],
    *,
    tier: int,
    prompt_version: str,
    deployment: str,
    temperature: float = 0.0,
    self_reported: str | None = None,
    source_url: str | None = None,
    rule_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Evidence:
    """A value a model produced.

    ``deployment``, ``prompt_version`` and ``temperature`` are recorded because
    a value produced by a model deployment is not reproducible without them,
    and deployments are not permanent. The prompt TEXT is never recorded — only
    its version identifier.
    """
    ref: dict[str, Any] = {
        "deployment": deployment,
        "prompt_version": prompt_version,
        "temperature": temperature,
    }
    if self_reported:
        ref["self_reported"] = self_reported
    if source_url:
        ref["source_url"] = source_url
        ref["retrieved_at"] = _now()
    if extra:
        ref.update(dict(extra))
    return Evidence(
        producer_chain=tuple(producer_chain),
        tier=tier,
        confidence_scale=LLM_SELF_REPORTED,
        confidence_value=self_reported_value(self_reported),
        evidence_ref=ref,
        rule_id=rule_id,
    )


def web_evidence(
    producer_chain: Sequence[str],
    source_url: str | None,
    *,
    tier: int | None = None,
    scale: str = FUZZY_RATIO,
    score: float | None = None,
    rule_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Evidence:
    """A value read off a fetched page."""
    ref: dict[str, Any] = {"source_url": source_url, "retrieved_at": _now()}
    if extra:
        ref.update(dict(extra))
    return Evidence(
        producer_chain=tuple(producer_chain),
        tier=tier,
        confidence_scale=scale,
        confidence_value=score,
        evidence_ref=ref,
        rule_id=rule_id,
    )


def deterministic_evidence(
    rule_id: str,
    *,
    producer: str = "pipeline",
    tier: int | None = None,
    evidence_ref: Any = None,
    kind: str = "write",
) -> Evidence:
    """A rule fired. ``rule_id`` names the use case or guard."""
    return Evidence(
        producer_chain=(producer,),
        tier=tier,
        confidence_scale=DETERMINISTIC,
        confidence_value=1.0,
        evidence_ref=evidence_ref,
        rule_id=rule_id,
        kind=kind,
    )


def inherited_evidence(
    donor_record_id: str,
    *,
    mode: str,
    donor_scale: str | None = None,
    donor_value: float | None = None,
    rule_id: str | None = None,
) -> Evidence:
    """Fix 6 batch consensus. The DONOR record id is the evidence_ref, so an
    inherited registry identifier is never indistinguishable from a first-hand
    one."""
    return Evidence(
        producer_chain=("batch_consensus",),
        tier=None,
        confidence_scale=INHERITED,
        confidence_value=donor_value,
        evidence_ref={
            "donor_record_id": donor_record_id,
            "mode": mode,
            "donor_confidence_scale": donor_scale,
        },
        rule_id=rule_id or f"batch_consensus:{mode}",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── The log ───────────────────────────────────────────────────────────────────

@dataclass
class ProvenanceLog:
    """Every write to a scoped field on one record, in order.

    ``seq`` is monotonic per record across all fields, so the interleaving of
    writes is reconstructable and not just the per-field order.
    """

    events: list[ProvenanceEvent] = dc_field(default_factory=list)
    rejections: list[RejectedCandidate] = dc_field(default_factory=list)
    #: field → number of guard rejections dropped after the per-field cap.
    rejections_omitted: dict[str, int] = dc_field(default_factory=dict)
    _seq: int = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record(
        self, field: str, old_value: Any, new_value: Any, evidence: Evidence,
    ) -> ProvenanceEvent:
        event = ProvenanceEvent(
            seq=self._next_seq(),
            field=FIELD_LABELS.get(field, field),
            old_value=old_value,
            new_value=new_value,
            producer_chain=tuple(evidence.producer_chain),
            evidence_ref=evidence.evidence_ref,
            confidence_scale=evidence.confidence_scale,
            confidence_value=evidence.confidence_value,
            rule_id=evidence.rule_id,
            tier=evidence.tier,
            kind=evidence.kind,
        )
        self.events.append(event)
        return event

    def reject(
        self,
        field: str,
        candidate: Any,
        guard: str,
        *,
        reason: str | None = None,
        evidence: Evidence | None = None,
    ) -> RejectedCandidate | None:
        """Log a candidate a guard refused, up to the per-field cap.

        Returns ``None`` when the cap has been reached — the count is kept in
        :attr:`rejections_omitted` so the volume is never silently truncated.
        """
        label = FIELD_LABELS.get(field, field)
        kept = sum(1 for r in self.rejections if r.field == label)
        if kept >= MAX_REJECTIONS_PER_FIELD:
            self.rejections_omitted[label] = (
                self.rejections_omitted.get(label, 0) + 1
            )
            return None
        rejection = RejectedCandidate(
            seq=self._next_seq(),
            field=label,
            candidate=candidate,
            guard=guard,
            reason=reason,
            producer_chain=tuple(evidence.producer_chain) if evidence else (),
            evidence_ref=evidence.evidence_ref if evidence else None,
            confidence_scale=(
                evidence.confidence_scale if evidence else NO_SCALE
            ),
            confidence_value=evidence.confidence_value if evidence else None,
            tier=evidence.tier if evidence else None,
        )
        self.rejections.append(rejection)
        return rejection

    # ── Projections ──────────────────────────────────────────────────────
    def events_for(self, field: str) -> list[ProvenanceEvent]:
        label = FIELD_LABELS.get(field, field)
        return [e for e in self.events if e.field == label]

    def attributing_event(self, field: str) -> ProvenanceEvent | None:
        """The last event that PRODUCED the field's value.

        Transforms are skipped: output casing did not decide that Name 1 is
        "Massachusetts Institute of Technology", ROR did. A field whose only
        events are transforms has no attribution and fails the admissibility
        gate, which is the correct answer — something reshaped a value nothing
        is on record as having produced.
        """
        for event in reversed(self.events_for(field)):
            if event.kind in ("write", "revert"):
                return event
        return None

    def has_event(self, field: str) -> bool:
        return bool(self.events_for(field))

    def as_dicts(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.events]

    def rejections_as_dicts(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.rejections]


# ── Derived scalars: Provenance Scheme B ──────────────────────────────────────
#
# The scalar is `source:confidence[+witness]` — see `enrichment.confidence`
# for the grammar and the confidence table. What lives HERE is only the
# adapter: the translation from what a lane recorded (a producer chain, a
# confidence scale, a rule id, an evidence ref) into the terms the confidence
# table is written in. The decision itself is `compute_confidence`, and this
# module does not take it.
#
# Why an adapter rather than each lane stating its own confidence: a lane
# knows what it saw, not what that is worth relative to what every other lane
# saw. Ranking evidence is a whole-pipeline judgement, so it is made in one
# place, from evidence the lanes record without interpreting.

#: Producers whose write IS a registry response.
_REGISTRY_PRODUCERS: frozenset[str] = frozenset({"ror", "gleif"})

#: Producers that read a page. The value's source is the domain that served
#: it, which is why these do not share the `input` fallback below.
_WEB_PRODUCERS: frozenset[str] = frozenset(
    {"website_resolver", "page_read", "domain_resolver"},
)

#: The record's own email domain — a witness under rule 4, not a page read.
_EMAIL_PRODUCER = "record_email"

#: Rule-id prefix that marks a registry hit reached by crosswalking through a
#: Wikidata item rather than by querying the registry directly. The registry
#: still authored the value (see `_crosswalk_to_ror`), so the source stays
#: `ror`/`gleif`; the crosswalk is what `+wikidata` records.
_CROSSWALK_RULE_PREFIX = "wikidata:crosswalk"

#: `unchanged-verified` records WHAT corroborated the retained name as a
#: `kind:detail` string. This maps the kind to the witness token.
_CORROBORATION_WITNESSES: dict[str, str] = {
    "page": WITNESS_WEB,
    "domain": WITNESS_WEB,
    "wikidata": WITNESS_WIKIDATA,
    "registry": WITNESS_REGISTRY,
    "email": WITNESS_DOMAIN,
}

#: `domain_ownership` conditions that constitute an INDEPENDENT witness for
#: the domain (hard rule 4). `name`, `serp` and `page` are deliberately
#: absent: a string comparison against the record's own Name 1, and a page
#: fetched from the very domain it is being asked to corroborate, are each ONE
#: source — `page` most explicitly of all, which is why the page-identity
#: ownership condition accepts a domain at `provisional` and never carries it
#: to `verified`.
#:
#: The two `witness_*` conditions are the opposite case and the reason this
#: map exists: a registry's `links[]` or a Wikidata `P856` claim stating the
#: same website the web path found is a SECOND system agreeing, reached
#: without consulting the first.
_DOMAIN_WITNESSES: dict[str, str] = {
    "email": WITNESS_DOMAIN,
    "registry": WITNESS_REGISTRY,
    "witness_registry": WITNESS_REGISTRY,
    "witness_wikidata": WITNESS_WIKIDATA,
}


def _ref(event: ProvenanceEvent) -> dict[str, Any]:
    return event.evidence_ref if isinstance(event.evidence_ref, dict) else {}


def _host_of(url: str | None) -> str:
    """The bare host of *url*, or ``""`` — no network, no validation."""
    if not url:
        return ""
    text = str(url).strip()
    text = text.split("://", 1)[-1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    text = text.split(":", 1)[0]
    return text.lower().lstrip(".")


def _web_domain_for(event: ProvenanceEvent) -> str:
    """The domain that goes in ``web:{domain}`` for a web-produced value.

    For the ``domain`` field the written value IS the domain. For anything
    else a page produced, the domain is the host of the page that was read.
    """
    if event.field == "domain" and event.new_value:
        return str(event.new_value).strip().lower()
    ref = _ref(event)
    for key in ("email_domain", "source_url", "withdrawn"):
        host = _host_of(ref.get(key))
        if host:
            return host
    return str(event.new_value or "").strip().lower()


def situation_for(
    event: ProvenanceEvent, record: Any = None,
) -> tuple[str, EvidenceSituation]:
    """``(source, EvidenceSituation)`` for one attributing event.

    The whole old→new mapping, in one readable function, and the only place a
    producer name is turned into a source token. *record* is optional and is
    consulted for exactly one thing: whether Wikidata's ``P856`` independently
    agreed with the domain the ownership guard accepted, which is a fact about
    the record rather than about the write that produced it.
    """
    producer = event.producer_chain[-1] if event.producer_chain else "input"
    scale = event.confidence_scale
    rule_id = event.rule_id or ""
    ref = _ref(event)

    # ── A registry authored it ────────────────────────────────────────────
    # `ror:1:exact`, `gleif:1:exact` and every fuzzy variant collapse here:
    # the match MODE is not a confidence, and it stays on the event and in the
    # trace rather than in the column.
    if producer in _REGISTRY_PRODUCERS:
        return producer, EvidenceSituation(
            registry_authored=True,
            has_source=True,
            via_wikidata_crosswalk=rule_id.startswith(_CROSSWALK_RULE_PREFIX),
            llm_involved=any(
                link.startswith("llm") for link in event.producer_chain
            ),
        )

    # ── The classifier ────────────────────────────────────────────────────
    # `classifier:-:rule` said only that a rule fired, never WHICH evidence
    # the rule read — so a record type ROR settled and one nothing settled
    # shipped the same string. The classifier records `decided_by`; that is
    # the source, and an unresolved type has no source at all.
    if producer == "classifier":
        decided = str(ref.get("decided_by") or "unresolved")
        if decided in _REGISTRY_PRODUCERS:
            return decided, EvidenceSituation(
                registry_authored=True, has_source=True,
            )
        if decided == "unresolved":
            return SOURCE_INPUT, EvidenceSituation(has_source=False)
        # `keyword` — the record's own Name 1 read as a research institution.
        # One source, uncontradicted: the input.
        return SOURCE_INPUT, EvidenceSituation(has_source=True)

    # ── A model ───────────────────────────────────────────────────────────
    # Every `llm_*` producer and every `self_*` band collapse to one string.
    # The model's self-report survives on the event; it was never a
    # measurement and it is not an authority claim, which is precisely what
    # `self_high` in an exported column was being read as.
    if producer.startswith("llm") or scale == LLM_SELF_REPORTED:
        return SOURCE_LLM, EvidenceSituation(
            has_source=True, llm_involved=True,
        )

    # ── The record's own email domain ─────────────────────────────────────
    if producer == _EMAIL_PRODUCER:
        return confidence_web_source(_web_domain_for(event)), EvidenceSituation(
            has_source=True, witness=WITNESS_DOMAIN,
        )

    # ── A page / a resolved website ───────────────────────────────────────
    # All eight `website_resolver:*:*` variants collapse to one provisional
    # string, and that is the substantive claim of hard rule 4: the tier and
    # the ownership condition told a reader which check fired, never that a
    # second, independent source agreed. Only a registry-stated website, a
    # Wikidata P856 agreement, or the record's own email domain does that.
    if producer in _WEB_PRODUCERS:
        witness = _DOMAIN_WITNESSES.get(str(ref.get("verified_by") or ""))
        if witness is None and event.field == "domain" and record is not None:
            corroboration = (
                record.get("_wikidata_corroboration")
                if hasattr(record, "get") else None
            )
            if (
                isinstance(corroboration, dict)
                and corroboration.get("domain_corroborated")
            ):
                witness = WITNESS_WIKIDATA
        return confidence_web_source(_web_domain_for(event)), EvidenceSituation(
            has_source=True, witness=witness,
        )

    # ── Batch consensus ───────────────────────────────────────────────────
    # NOT in the migration's state table — see `provenance_migration_report.md`.
    # An identifier a sibling record matched is authored by that registry, but
    # THIS record never looked it up, so it is never `verified` here. The
    # donor record id stays on the event, which is what a reviewer opens.
    if scale == INHERITED:
        source = {
            "ror_id": SOURCE_ROR, "lei_id": SOURCE_GLEIF,
        }.get(event.field, SOURCE_INPUT)
        return source, EvidenceSituation(has_source=True)

    # ── The input value stood ─────────────────────────────────────────────
    # Fix 2's three unchanged states, which are the three rows of the
    # confidence table that concern a value nobody rewrote.
    if scale == INPUT_CORROBORATED:
        kind = str(ref.get("corroborated_by") or "").split(":", 1)[0]
        return SOURCE_INPUT, EvidenceSituation(
            has_source=True,
            witness=_CORROBORATION_WITNESSES.get(kind, WITNESS_WEB),
        )
    if scale == INPUT_SELF_CONSISTENT:
        return SOURCE_INPUT, EvidenceSituation(
            has_source=True,
            llm_involved=True,
            canonical_proposal_equals_input=True,
        )

    # Everything else is a deterministic rule reshaping or retaining the
    # record's own value with nothing corroborating it — the old
    # `input:1:rule`. No source agreed; that is the table's last row.
    return SOURCE_INPUT, EvidenceSituation(has_source=False)


def derived_scalar(
    log: ProvenanceLog, field: str, record: Any = None,
) -> str | None:
    """``source:confidence[+witness]`` for one scoped field.

    Regenerated from the events every time — never maintained separately, so
    the scalar and the log cannot drift apart. ``None`` when the field has no
    attributing event, which is exactly the condition the admissibility gate
    acts on.
    """
    event = log.attributing_event(field)
    if event is None:
        return None
    source, situation = situation_for(event, record)
    band, witness = compute_confidence(situation)
    return confidence_render(source, band, witness)


def derived_scalars(
    log: ProvenanceLog, record: Any = None,
) -> dict[str, str | None]:
    """The six derived scalar columns, regenerated from *log*."""
    return {
        column: derived_scalar(log, field, record)
        for field, column in DERIVED_SCALAR_FIELDS.items()
    }


# ── Weakness (Fix 8 interaction) ──────────────────────────────────────────────

#: Producers whose writes rest on nothing but a model's training data. A value
#: from one of these is an unverified inference no matter how confident the
#: model said it was — a confident unverifiable claim is the more dangerous
#: case, not the safer one.
EVIDENCE_FREE_PRODUCERS: frozenset[str] = frozenset({"llm_tier3"})

#: Producers whose write makes a field registry-owned.
REGISTRY_PRODUCERS: frozenset[str] = frozenset({"ror", "gleif"})


def weak_fields(log: ProvenanceLog) -> set[str]:
    """Scoped field labels whose current value rests on no external evidence.

    Derived from the log rather than from a separately maintained evidence set:
    the question "is this value weak" is a question about who wrote it last,
    which is what the log records. A field an authority overwrote is not weak
    even if an LLM wrote it first — the earlier event is still in the log, and
    that is the point of keeping a log rather than a final-state map.
    """
    weak: set[str] = set()
    for field in SCOPED_FIELDS:
        event = log.attributing_event(field)
        if event is None:
            continue
        if event.producer_chain[-1] in EVIDENCE_FREE_PRODUCERS:
            weak.add(event.field)
    return weak


def registry_owned_fields(log: ProvenanceLog) -> set[str]:
    """Scoped field labels whose current value came from a registry."""
    owned: set[str] = set()
    for field in SCOPED_FIELDS:
        event = log.attributing_event(field)
        if event is not None and event.producer_chain[-1] in REGISTRY_PRODUCERS:
            owned.add(event.field)
    return owned


# ── The record ────────────────────────────────────────────────────────────────

class EnrichedRecord(dict):
    """The pipeline's working record, with the six scoped fields write-locked.

    A ``dict`` subclass so that the ~18k lines of reading code
    (``result.get("name1_enriched")``) are untouched, while every *write* to a
    scoped key must state its evidence::

        record.write("domain", "mit.edu", registry_evidence("ror", ror_id))
        record["domain"] = "mit.edu"          # UnattributedWriteError

    Non-scoped keys behave exactly like an ordinary dict. Extending the scope
    is a change to :data:`SCOPED_FIELDS` and nothing else.
    """

    __slots__ = ("provenance", "_unlocked")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Seed through dict.__init__ so _init_result's blank scoped keys are
        # initialisation rather than writes: a field that is None has nothing
        # to attribute, and the gate only ever asks about non-null values.
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "provenance", ProvenanceLog())
        object.__setattr__(self, "_unlocked", False)

    # ── The one write path ───────────────────────────────────────────────
    def write(self, field: str, value: Any, evidence: Evidence | None) -> None:
        """Write *value* to *field*, recording *evidence*.

        The only way a scoped field is ever populated. ``evidence`` is
        required and structured; a bare string or ``None`` raises
        :class:`MissingEvidenceError`.
        """
        if evidence is None:
            raise MissingEvidenceError(
                f"write to {field!r} requires an Evidence argument — a value "
                "whose origin cannot be reconstructed is not admissible",
            )
        if not isinstance(evidence, Evidence):
            raise MissingEvidenceError(
                f"write to {field!r} needs an Evidence, got "
                f"{type(evidence).__name__}",
            )
        old = self.get(field)
        if field in SCOPED_FIELDS:
            self.provenance.record(field, old, value, evidence)
        self._set(field, value)

    def transform(
        self,
        field: str,
        value: Any,
        *,
        rule_id: str,
        producer: str = "finalise",
    ) -> None:
        """Reshape a value already present — casing, abbreviation expansion,
        legal-suffix collapse. Recorded, but never the attribution: a transform
        restyles a value, it does not produce one."""
        if value == self.get(field):
            return
        self.write(
            field, value,
            deterministic_evidence(
                rule_id, producer=producer, kind="transform",
            ),
        )

    def revert(self, field: str, value: Any, *, rule_id: str) -> None:
        """Put the input value back — the admissibility gate's write."""
        self.write(
            field, value,
            deterministic_evidence(
                rule_id, producer="admissibility_gate", kind="revert",
            ),
        )

    def reject(
        self,
        field: str,
        candidate: Any,
        guard: str,
        *,
        reason: str | None = None,
        evidence: Evidence | None = None,
    ) -> None:
        """Log a candidate a guard refused. Capped per field — see
        :meth:`ProvenanceLog.reject`."""
        self.provenance.reject(
            field, candidate, guard, reason=reason, evidence=evidence,
        )

    # ── Enforcement ──────────────────────────────────────────────────────
    def _set(self, key: str, value: Any) -> None:
        dict.__setitem__(self, key, value)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in SCOPED_FIELDS and not self._unlocked:
            raise UnattributedWriteError(
                f"{key!r} is write-locked: use record.write({key!r}, value, "
                "evidence). A written value whose origin cannot be "
                "reconstructed is not admissible.",
            )
        dict.__setitem__(self, key, value)

    def setdefault(self, key: str, default: Any = None) -> Any:  # noqa: D102
        # Refused whether or not the key happens to be present: `setdefault`
        # states an intent to write, and a reader of the call site cannot tell
        # which branch it will take. Reads use `.get`.
        if key in SCOPED_FIELDS:
            raise UnattributedWriteError(
                f"{key!r} is write-locked: use record.write(), or .get() to "
                "read it",
            )
        return dict.setdefault(self, key, default)

    def update(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        incoming = dict(*args, **kwargs)
        blocked = [k for k in incoming if k in SCOPED_FIELDS]
        if blocked and not self._unlocked:
            raise UnattributedWriteError(
                f"{sorted(blocked)} are write-locked: use record.write()",
            )
        dict.update(self, incoming)

    def pop(self, key: str, *default: Any) -> Any:  # noqa: D102
        if key in SCOPED_FIELDS:
            raise UnattributedWriteError(
                f"{key!r} is write-locked and must not be removed",
            )
        return dict.pop(self, key, *default)

    # ── Construction ─────────────────────────────────────────────────────
    @classmethod
    def initialise(cls, base: Mapping[str, Any]) -> "EnrichedRecord":
        """Build a record from a blank template.

        Seeding is not writing: the scoped keys arrive as ``None`` (or, for
        ``record_type``, ``"unknown"``), which asserts nothing and so has
        nothing to attribute. Any non-null scoped value in *base* would be an
        unattributed write and is refused.
        """
        unattributed = [
            k for k in SCOPED_FIELDS
            if base.get(k) not in (None, INPUT_VALUE_DEFAULTS.get(k))
        ]
        if unattributed:
            raise UnattributedWriteError(
                f"cannot initialise with pre-populated scoped fields "
                f"{sorted(unattributed)} — write them with record.write()",
            )
        return cls(base)


# ── The admissibility gate (Step 5) ───────────────────────────────────────────

#: Flag code raised on a field the gate reverted.
UNATTRIBUTED_CODE = "unattributed-value"
UNATTRIBUTED_REASON = (
    "was written with no record of what produced it, so it could not be "
    "attributed — the input value has been restored; re-run the record"
)


def enforce_admissibility(record: "EnrichedRecord") -> list[str]:
    """Every non-null scoped field must carry at least one provenance event.

    A field that does not is inadmissible: its value is reverted to the input
    value and the field is flagged. The record is NOT failed — shipping the
    original input is strictly better than failing the batch, and strictly
    better than shipping an unattributable value.

    Returns the field labels that were reverted.
    """
    reverted: list[str] = []
    for field in SCOPED_FIELDS:
        value = record.get(field)
        if value in (None, "", INPUT_VALUE_DEFAULTS.get(field)):
            continue
        if record.provenance.has_event(field):
            continue
        input_key = INPUT_VALUE_KEYS.get(field)
        fallback = (
            record.get(input_key) if input_key else INPUT_VALUE_DEFAULTS.get(field)
        )
        if isinstance(fallback, str) and not fallback.strip():
            fallback = None
        logger.warning({
            "record_id": record.get("record_id"),
            "step": "inadmissible_value_reverted",
            "field": field,
            "dropped": value,
            "restored": fallback,
        })
        record.revert(field, fallback, rule_id="admissibility-gate")
        reverted.append(FIELD_LABELS.get(field, field))
    return reverted


def assert_admissible(record: "EnrichedRecord") -> None:
    """The gate as a hard assertion, for tests.

    Same condition as :func:`enforce_admissibility`; in production the value is
    reverted and flagged, in tests it fails loudly.
    """
    missing = [
        field for field in SCOPED_FIELDS
        if record.get(field) not in (None, "", INPUT_VALUE_DEFAULTS.get(field))
        and not record.provenance.has_event(field)
    ]
    if missing:
        raise AssertionError(
            f"record {record.get('record_id')!r} holds scoped values with no "
            f"provenance event: {missing}",
        )


# ── Shared helpers for the response layer ─────────────────────────────────────

def log_from_dicts(
    events: Iterable[Mapping[str, Any]],
    rejections: Iterable[Mapping[str, Any]] = (),
    omitted: Mapping[str, int] | None = None,
) -> ProvenanceLog:
    """Rebuild a :class:`ProvenanceLog` from its serialised form.

    Used by the response layer, which carries the events as plain dicts, so the
    derived scalars can be regenerated from exactly what ships.
    """
    log = ProvenanceLog()
    for raw in events:
        log.events.append(ProvenanceEvent(
            seq=int(raw.get("seq", 0)),
            field=str(raw.get("field")),
            old_value=raw.get("old_value"),
            new_value=raw.get("new_value"),
            producer_chain=tuple(raw.get("producer_chain") or ()),
            evidence_ref=raw.get("evidence_ref"),
            confidence_scale=str(raw.get("confidence_scale") or NO_SCALE),
            confidence_value=raw.get("confidence_value"),
            rule_id=raw.get("rule_id"),
            tier=raw.get("tier"),
            kind=str(raw.get("kind") or "write"),
        ))
    for raw in rejections:
        log.rejections.append(RejectedCandidate(
            seq=int(raw.get("seq", 0)),
            field=str(raw.get("field")),
            candidate=raw.get("candidate"),
            guard=str(raw.get("guard")),
            reason=raw.get("reason"),
            producer_chain=tuple(raw.get("producer_chain") or ()),
            evidence_ref=raw.get("evidence_ref"),
            confidence_scale=str(raw.get("confidence_scale") or NO_SCALE),
            confidence_value=raw.get("confidence_value"),
            tier=raw.get("tier"),
        ))
    log._seq = max(
        [e.seq for e in log.events] + [r.seq for r in log.rejections] + [0],
    )
    if omitted:
        log.rejections_omitted.update(dict(omitted))
    return log
