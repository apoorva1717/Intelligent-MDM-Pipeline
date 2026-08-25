"""Review flags — computed once, from the record's final state (Fix 8).

The flag answers one question for a human reviewer: *is there something here
for me to do, and to which field?* Before Fix 8 it answered a different one —
*which tier ran?* — because each tier appended its own reason as it executed.
That produced a flag on 47 of 50 demo records and a reason text that named a
code path rather than a doubt, so the flag stopped working as a triage signal.

Three rules follow from that, and this module exists to enforce them:

1. **Rebuilt, never appended.** :func:`compute_flags` is called once, from
   ``finalise``, after every name, domain and contact field has settled. Tiers
   record *evidence* (the ``_ev_*`` keys below); they never write a flag. A
   record that reached Tier 3 and was then rescued by Fix 2's Tier 1 retry
   ends with a registry identifier and no Tier 3 reason, because the reason is
   derived from what the record *holds*, not from what ran.

   One pass runs later than "every field has settled" allows for: batch
   consensus converges a whole finalised batch and can replace
   ``name1_enriched``, leaving a flag that describes a value no longer on the
   record. :func:`retract` is its only recourse, and it can only ever
   withdraw — never raise, never re-judge.

2. **Field-scoped.** ``flagged_fields`` names the output fields the flag
   concerns. A Stanford record with a verified ROR ID and an uncertain
   department scopes to ``name2`` alone, so a reviewer can tell a one-field
   check from a full record review.

3. **Absence of data is not a defect.** A research institution with no
   department and no contact is not flagged: there is nothing for a reviewer
   to do. Neither is any deterministic normalisation, nor an evidence-backed
   result whose evidence is auditable (a Tier 2B stated department carries a
   ``source_url``; a verified Tier 1 match carries a registry identifier).

The three output fields are a contract with DATAshaper and are always
consistent: ``flag_for_review`` is true **iff** ``flag_codes`` is non-empty,
and ``flag_reason`` is the prose rendering of the same codes. The scope is
encoded in the reason text as well as in ``flagged_fields``, so a consumer
that reads only the two pre-Fix-8 columns still learns which field is in
doubt. :func:`render` builds all three, and is the only thing that does.

``flag_scopes`` and ``flag_details`` are two further fields and are internal
(``exclude=True``): the code -> fields map the other three are rendered from,
and the code -> named-value map that puts the rejected domain into the prose.
Both are kept so a later pass can withdraw one code from one field without
re-deriving the rest, or re-wording what it keeps. ``flagged_fields`` is the
union of the scopes and is what ships.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from enrichment.confidence import LOW, parse as parse_provenance
from enrichment.preprocess import _is_opaque_code
from enrichment.provenance import (
    SCOPED_FIELDS,
    FIELD_LABELS as PROV_FIELD_LABELS,
    derived_scalar,
    log_from_dicts,
    weak_fields,
)
from enrichment.unchanged_state import UNCHANGED_CONFIRMED, UNCHANGED_VERIFIED
from utils.name_slots import DEPT_SLOTS, NAME_SLOT_LABELS, NAME_SLOTS
from utils.text_utils import is_admin_unit

logger = logging.getLogger(__name__)


# ── Flag codes ────────────────────────────────────────────────────────────────
#
# The machine-readable vocabulary. `flag_codes` holds a subset of these; a
# record can carry several. Each has exactly one detection rule below and one
# prose template in `_REASONS`.

NO_MATCH = "no-match"
#: RETIRED by the provenance migration, and kept for exactly two jobs: its
#: prose (`_REASONS`) is still what the derived flag says, and its slot in
#: `_CODE_ORDER` is still where that prose appears in a multi-part reason. It
#: is NOT in `ALL_CODES` and can never appear in `flag_codes` again.
#:
#: It said "left exactly as supplied — the canonical form could not be
#: established with enough confidence to rewrite it". That is the definition of
#: `input:low` on the field, which the record now carries in its provenance, so
#: the code was a second recording of a fact the column already stated — and
#: the two could disagree, because one was raised by a tier remembering to
#: leave a marker and the other is derived from the write history.
LOW_CONFIDENCE_UNCHANGED = "low-confidence-unchanged"
DEPT_VIA_LAB = "dept-via-lab"
PERSON_UNRESOLVED = "person-unresolved"
OVERFLOW = "overflow"
OPAQUE_CODE = "opaque-code"
DOMAIN_UNVERIFIED = "domain-unverified"
EMAIL_CONFLICT = "email-conflict"
NAME3_NOT_DEMOTED = "name3-not-demoted"
MULTIPLE_CONTACTS = "multiple-contacts"
UNVERIFIED_INFERENCE = "unverified-inference"
#: The organisation the record names has been dissolved (Wikidata ``P576``) or
#: replaced by another entity (``P1366``). The pipeline deliberately does NOT
#: rewrite the name to the successor: which legal entity a customer record
#: should point at after a merger is a business decision, not a data-quality
#: correction, and it depends on contracts and open orders this service cannot
#: see. The flag hands the reviewer the successor's name and QID and stops.
ENTITY_SUPERSEDED = "entity-superseded"
#: Fix D(1). Two sources named this organisation and they named different
#: organisations — a GLEIF legal name against a ROR official name, or a
#: registry against what the candidate website states. The lower-priority
#: source's fields have been removed; the reason names both entities so the
#: reviewer can see which identity was dropped and why, rather than finding a
#: silently blank column. Raised only when the pipeline ACTED, so it is never
#: a report of a disagreement that made no difference.
SOURCE_CONFLICT = "source-conflict"
#: Fix D(2). A ROR or GLEIF match whose registered locality contradicts the
#: record's city/state. The match is KEPT — a company relocating within one
#: country is ordinary, and a moved address is not evidence that the registry
#: identified the wrong entity — but the two places are named so a reviewer
#: checking the address knows the registry disagrees with it. (A locality
#: contradiction on a name too short to identify an entity on its own is a
#: different case entirely and never gets this far: Fix C(3) refuses that
#: match in the registry client.)
REGISTRY_LOCATION_MISMATCH = "registry-location-mismatch"

#: Every code that can appear in `flag_codes`. `LOW_CONFIDENCE_UNCHANGED` is
#: deliberately absent — see its definition above.
ALL_CODES: tuple[str, ...] = (
    NO_MATCH,
    DEPT_VIA_LAB,
    PERSON_UNRESOLVED,
    OVERFLOW,
    OPAQUE_CODE,
    DOMAIN_UNVERIFIED,
    EMAIL_CONFLICT,
    NAME3_NOT_DEMOTED,
    MULTIPLE_CONTACTS,
    UNVERIFIED_INFERENCE,
    ENTITY_SUPERSEDED,
    SOURCE_CONFLICT,
    REGISTRY_LOCATION_MISMATCH,
)

# Emission order — most structural first, so the leading clause of a
# multi-code reason is the one that most changes what a reviewer does.
_CODE_ORDER: tuple[str, ...] = (
    OVERFLOW,
    OPAQUE_CODE,
    PERSON_UNRESOLVED,
    # Before `no-match`: "this organisation no longer exists" is a bigger
    # change to what a reviewer does than "we could not identify it".
    ENTITY_SUPERSEDED,
    # Before `no-match` and before the inference codes: "two sources named two
    # different organisations" changes what a reviewer does more than any
    # doubt about a single value does — it says the record's identity itself
    # is contested.
    SOURCE_CONFLICT,
    NO_MATCH,
    UNVERIFIED_INFERENCE,
    LOW_CONFIDENCE_UNCHANGED,
    REGISTRY_LOCATION_MISMATCH,
    DEPT_VIA_LAB,
    NAME3_NOT_DEMOTED,
    MULTIPLE_CONTACTS,
    EMAIL_CONFLICT,
    DOMAIN_UNVERIFIED,
)

# Reason prose. Each states what is uncertain and what the reviewer should do —
# never which tier ran. Field scope is prefixed at render time.
_REASONS: dict[str, str] = {
    NO_MATCH: (
        "no source could identify this organisation — resolve the name "
        "manually"
    ),
    LOW_CONFIDENCE_UNCHANGED: (
        "left exactly as supplied — the canonical form could not be "
        "established with enough confidence to rewrite it; confirm the value "
        "is correct"
    ),
    DEPT_VIA_LAB: (
        "parent department was inferred from the lab's own page, not read "
        "from a stated department — confirm the department is the right "
        "parent for this lab"
    ),
    PERSON_UNRESOLVED: (
        "holds a person, and the organisation they belong to could not be "
        "resolved — identify the organisation manually"
    ),
    OVERFLOW: (
        "one value appears to be split across several SAP fields — confirm "
        "the field split before the record is used"
    ),
    OPAQUE_CODE: (
        "holds an internal code rather than an organisation name — supply "
        "the organisation name"
    ),
    DOMAIN_UNVERIFIED: (
        "a candidate website was found but nothing tied it to this "
        "organisation — confirm the website before using it"
    ),
    EMAIL_CONFLICT: (
        "an email found in the record differs from the one already on file "
        "— confirm which address is correct"
    ),
    NAME3_NOT_DEMOTED: (
        "the parent department was written to Name 2 but every slot below it "
        "was already populated, so the lab name could not be moved down — "
        "confirm the department split across these fields"
    ),
    MULTIPLE_CONTACTS: (
        "names more than one person, so the department could not be "
        "confirmed against a contact's page — split the contacts into one "
        "person per record"
    ),
    UNVERIFIED_INFERENCE: (
        "inferred without external evidence — confirm against an "
        "authoritative source"
    ),
    ENTITY_SUPERSEDED: (
        "names an organisation that no longer exists as a separate entity — "
        "decide which entity this record should point to"
    ),
    SOURCE_CONFLICT: (
        "two sources identified this as different organisations — the "
        "lower-priority source's fields were removed; confirm which identity "
        "is correct"
    ),
    REGISTRY_LOCATION_MISMATCH: (
        "the registry record matched to this organisation is registered at a "
        "different address — confirm the address, or that the organisation "
        "has moved"
    ),
}

# Prose variants that name the specific value in doubt. A reviewer told to
# "confirm the website" has to go and find which website was rejected — the
# pipeline knew, discarded it (that is the point of the guard), and left the
# reviewer to rediscover it. Where the raising site can supply the value, this
# template is used instead of `_REASONS` and the value is rendered into
# `{detail}`. Codes without an entry here, or with no detail supplied, fall
# back to `_REASONS` unchanged.
_DETAILED_REASONS: dict[str, str] = {
    DOMAIN_UNVERIFIED: (
        "a candidate website ({detail}) was found but nothing tied it to "
        "this organisation — confirm {detail} before using it"
    ),
    # The successor's name and QID, or the dissolution date. Without it the
    # reviewer is told an entity is gone and left to find out what replaced it
    # — which the pipeline already knows and has deliberately declined to act
    # on. Naming it is the whole value of the flag.
    ENTITY_SUPERSEDED: (
        "names an organisation that no longer exists as a separate entity "
        "({detail}) — decide which entity this record should point to"
    ),
    # Both entities by name. A reviewer told only "two sources disagreed" has
    # to go and find out which two and about what — which the pipeline knew
    # and has already acted on.
    SOURCE_CONFLICT: (
        "two sources identified this as different organisations — {detail}; "
        "confirm which identity is correct"
    ),
    REGISTRY_LOCATION_MISMATCH: (
        "the registry record matched to this organisation is registered at a "
        "different address ({detail}) — confirm the address, or that the "
        "organisation has moved"
    ),
}

# The `flagged_fields` vocabulary, and how each renders in the reason prose.
FIELD_LABELS: dict[str, str] = {
    **NAME_SLOT_LABELS,
    "domain": "Domain",
    "contact": "Contact",
    "email": "Email",
    "address": "Address",
}

_FIELD_ORDER: tuple[str, ...] = (
    *NAME_SLOTS, "domain", "contact", "email", "address",
)

# Transient evidence keys written by the tiers and consumed here. Popped in
# `compute_flags` so they never reach pydantic validation.
_EVIDENCE_KEYS: tuple[str, ...] = (
    "_ev_overflow",
    "_ev_person_unresolved",
    "_ev_dept_via_lab",
    "_ev_name3_not_demoted",
    "_ev_demoted_to",
    "_ev_low_conf_unchanged",
    "_ev_tier3_wrote",
    "_ev_email_conflict",
    "_has_dept_signal",
    "_multi_contact",
    "_domain_unverified",
    "_domain_page_note",
    "_ev_entity_superseded",
    # Fix D — left by `enrichment.consistency`, which runs just before this.
    "_ev_source_conflict",
    "_ev_source_conflict_fields",
    "_ev_registry_location_mismatch",
    # Fix 3 — the OPPOSITE finding: two registries named one organisation.
    # Popped here with the rest so it cannot reach the response model; it
    # raises no flag (an agreement is not a triage signal), and the finding
    # itself lives on the `source_agreement` trace line.
    "_ev_registry_agreement",
)


def _sorted_fields(fields: set[str]) -> list[str]:
    """Field bases in output-column order, unknown names dropped."""
    return [f for f in _FIELD_ORDER if f in fields]


def _label(fields: list[str]) -> str:
    """Render a field list as the reason's leading scope clause."""
    labels = [FIELD_LABELS[f] for f in fields]
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _nothing_was_enriched(result: dict[str, Any]) -> bool:
    """True when the pipeline added nothing at all to this record.

    "Nothing" is judged from the record's final state: no registry identifier,
    no domain or evidence URL, and no output field whose content differs from
    the input. A field that was empty on input and stayed empty is not a
    change, and a difference that is only letter case is not one either — the
    ``*_changed`` flags already encode both rules (see ``finalise``).
    """
    if result.get("enrichment_status") in ("enriched", "verified"):
        return False
    if result.get("ror_id") or result.get("lei_id"):
        return False
    # Fix 2: a Name 1 the pipeline kept AND corroborated (or that an
    # independent canonicalisation reproduced) is not "no source could identify
    # this organisation". The pipeline established something about the record —
    # that its own value stands, with evidence a reviewer can open — and the
    # unchanged state records what. Without this, dropping the
    # `low-confidence-unchanged` code from a verified row would let `no-match`
    # take its place, which is a worse claim than the one just withdrawn.
    if result.get("unchanged_name1_state") in (
        UNCHANGED_CONFIRMED, UNCHANGED_VERIFIED,
    ):
        return False
    if (
        result.get("domain")
        or result.get("department_domain")
        or result.get("source_url")
        # Fix 3: a page read that returned this organisation's stated identity
        # is a source that identified it — which is the exact claim `no-match`
        # denies. Measured: `American Art Clay Company` had amaco.com read,
        # corroborated and written to `operating_name`, and still shipped "no
        # source could identify this organisation".
        or result.get("operating_name")
    ):
        return False
    return not any(
        result.get(f"{f}_changed")
        for f in (*NAME_SLOTS, "contact", "email")
    )


#: Name slots inside Fix 10's Phase 1 provenance scope. For these the
#: "did an evidence-free producer write this" question is answered by the
#: record's provenance log; for the slots below them there is no log yet, so
#: the tier's own `_ev_tier3_wrote` marker still answers it.
_PROVENANCE_SCOPED_SLOTS: frozenset[str] = frozenset(
    PROV_FIELD_LABELS[f] for f in SCOPED_FIELDS
    if PROV_FIELD_LABELS[f] in NAME_SLOTS
)


def _evidence_free_fields(
    result: dict[str, Any], evidence: dict[str, Any],
) -> set[str]:
    """Fields whose value rests on nothing but a model's training data.

    Fix 10 makes this DERIVED for the fields in Phase 1 provenance scope:
    "who wrote this last" is a question about the record's write history, and
    the provenance log is that history — so it answers rather than a flag that
    a tier remembered to set. It answers better, too: a field Tier 3 wrote and
    a registry then overwrote is no longer Tier 3's claim, and the log shows
    that directly instead of needing a second `_registry_name_fields` check.

    Name 3 and below are outside Phase 1 scope, so their marker still comes
    from the tier. Extending the scope removes the second branch.
    """
    log = getattr(result, "provenance", None)
    marked = set(evidence.get("_ev_tier3_wrote") or ())
    if log is None:
        return marked
    derived = weak_fields(log)
    out_of_scope = {f for f in marked if f not in _PROVENANCE_SCOPED_SLOTS}
    return derived | out_of_scope


#: The fields whose confidence derives the flag.
#:
#: Name 1 and Name 2 only. ``domain`` and ``record_type`` carry provenance
#: like every other scoped field, and their confidence is exported — but they
#: do not raise the review flag. The reason is what the flag is FOR: it asks a
#: human to check a name. A record whose type could not be settled, or which
#: has no website, is not a record with a wrong name in it, and routing those
#: into the review queue would have moved this batch from 55 flagged rows to
#: 96 — restoring exactly the "flag on 47 of 50 records" failure Fix 8 exists
#: to have fixed.
CORE_PROVENANCE_FIELDS: tuple[str, ...] = ("name1_enriched", "name2_enriched")


def low_confidence_core_fields(result: Any) -> list[str]:
    """Core field labels whose derived provenance confidence is ``low``.

    The retirement of ``low-confidence-unchanged`` in one function. That code
    was raised by a tier leaving an ``_ev_low_conf_unchanged`` marker behind;
    it is now READ OFF the provenance the record already carries, because
    "the canonical form could not be established" and ``input:low`` are the
    same statement and there is no longer any reason to record it twice.

    The three guards the old rule needed are subsumed rather than reimplemented
    — which is the evidence that the derivation is the same decision, not a
    lookalike. A field a registry wrote is ``ror:verified``, not ``input:low``;
    a field Tier 3 wrote is ``llm:provisional``; a field batch consensus
    replaced re-derives the moment it is written. None of them can be ``low``,
    so none of them needs excluding.

    Derived from the log rather than from the ``*_provenance`` columns because
    ``compute_flags`` runs before those columns are projected — and because a
    flag that read a column it also helps produce could drift from it.
    """
    log = getattr(result, "provenance", None)
    if log is None:
        return []
    if isinstance(log, list):
        log = log_from_dicts(log)

    def _value(field: str) -> Any:
        if hasattr(result, "get"):
            return result.get(field)
        return getattr(result, field, None)

    low: list[str] = []
    for field in CORE_PROVENANCE_FIELDS:
        # An empty field has no value to doubt. Rule 3 of this module:
        # absence of data is not a defect.
        if not _value(field):
            continue
        scalar = derived_scalar(log, field, result)
        if not scalar:
            continue
        if parse_provenance(scalar)[1] == LOW:
            low.append(PROV_FIELD_LABELS[field])
    return low


def render(
    scopes: dict[str, Iterable[str]],
    details: dict[str, str] | None = None,
    notes: dict[str, str] | None = None,
    low_confidence: Iterable[str] = (),
) -> dict[str, Any]:
    """Render a code -> field-scope map into the five output fields.

    The single place the flag columns are built, so a pass that withdraws a
    code later (:func:`retract`) cannot render them differently from the pass
    that raised it. A code mapped to an empty scope is record-level and its
    prose carries no leading scope clause.

    *details* names the specific value a code is about — the rejected domain
    for ``domain-unverified``. It is kept out of the scope map and returned as
    its own field because it is not scope: it survives so :func:`retract` can
    re-render the codes it keeps with the same prose they were raised with,
    rather than silently dropping back to the generic wording.

    *notes* is Fix 3's addition: one further clause **appended** to a code's
    prose, for a finding that sharpens an existing doubt without being a new
    one. A page read that says the candidate site belongs to a company in
    another city does not change what the reviewer is being asked — confirm
    this website — it tells them what to expect when they look. Appending
    keeps the code, the scope and the existing wording untouched, which is the
    point: the flag vocabulary does not grow every time the pipeline learns
    something. A code with no note renders byte-identically to before.
    """
    if LOW_CONFIDENCE_UNCHANGED in scopes:
        # Raised rather than dropped. The code is retired, so a caller that
        # still passes it is a caller that has not been migrated — and
        # silently discarding its scope would lose a real doubt about a real
        # field, which is the one outcome worse than failing here.
        raise ValueError(
            f"{LOW_CONFIDENCE_UNCHANGED!r} is retired and cannot be raised as "
            "a code; pass its fields as `low_confidence=` instead, or let "
            "`low_confidence_core_fields` derive them from the provenance",
        )
    ordered = [c for c in _CODE_ORDER if c in scopes]
    scoped = {c: _sorted_fields(set(scopes[c] or ())) for c in ordered}
    kept = {
        c: str(v) for c, v in (details or {}).items()
        if c in scoped and c in _DETAILED_REASONS and v
    }
    kept_notes = {
        c: str(v) for c, v in (notes or {}).items() if c in scoped and v
    }
    low = _sorted_fields(set(low_confidence or ()))

    reasons: list[str] = []
    flagged: set[str] = set()
    # `_CODE_ORDER` still holds `LOW_CONFIDENCE_UNCHANGED`, and it is never in
    # `scopes` any more — the slot is walked so the derived clause appears at
    # the position the retired code occupied. A reviewer reading a multi-part
    # reason sees the same clause in the same place as before the migration;
    # only `flag_codes` lost the token.
    for code in _CODE_ORDER:
        if code == LOW_CONFIDENCE_UNCHANGED:
            if low:
                flagged.update(low)
                reasons.append(
                    f"{_label(low)}: {_REASONS[LOW_CONFIDENCE_UNCHANGED]}",
                )
            continue
        if code not in scoped:
            continue
        fields = scoped[code]
        flagged.update(fields)
        prose = (
            _DETAILED_REASONS[code].format(detail=kept[code])
            if code in kept
            else _REASONS[code]
        )
        if code in kept_notes:
            prose = f"{prose} — {kept_notes[code]}"
        reasons.append(f"{_label(fields)}: {prose}" if fields else prose)

    return {
        "flag_codes": ordered,
        "flagged_fields": _sorted_fields(flagged),
        # DERIVED, not "true iff there is a code". A core field at `low`
        # raises the flag with no code attached, which is the whole of the
        # authorised taxonomy change: `low-confidence-unchanged` was a code
        # that existed only to say what the confidence already says.
        "flag_for_review": bool(ordered) or bool(low),
        "flag_reason": "; ".join(reasons) if reasons else None,
        "flag_scopes": scoped,
        "flag_details": kept,
        "flag_notes": kept_notes,
        "flag_low_confidence": low,
    }


def retract(result: Any, codes: Iterable[str], field: str) -> tuple[str, ...]:
    """Withdraw *codes* from *field*'s scope on an already-flagged result.

    Rule 1 of this module — rebuilt, never appended — assumes every input to
    the decision has settled by the time ``finalise`` runs. Batch consensus
    breaks that assumption for one field: it runs after the whole batch is
    finalised and replaces `name1_enriched` on a record whose flag already
    described the value it replaced. `low-confidence-unchanged` then reads
    "left exactly as supplied" on a record that was not left as supplied.

    So the batch pass withdraws exactly the codes its own write falsified, and
    nothing else. Withdrawal is per field: a code scoped to two fields keeps
    the other one and is dropped only when its scope empties. A record-level
    code (empty scope) is never reached, because no field is in its scope.

    Takes and returns *codes* rather than recomputing, because the evidence
    ``compute_flags`` consumed is gone by now — the caller states what its
    write invalidated and this renders the consequence. Returns the codes
    actually withdrawn, empty when nothing changed.
    """
    scopes: dict[str, Iterable[str]] = {
        code: set(fields or ())
        for code, fields in (getattr(result, "flag_scopes", None) or {}).items()
    }
    withdrawn: list[str] = []
    for code in codes:
        fields = scopes.get(code)
        if fields is None or field not in fields:
            continue
        withdrawn.append(code)
        fields.discard(field)
        if not fields:
            del scopes[code]

    # The derived half of the flag is RE-DERIVED rather than withdrawn. Batch
    # consensus reaches here having just written `name1_enriched` through
    # `EnrichmentResult.write`, which regenerated that field's provenance from
    # the new event — so a record that was `input:low` because its own value
    # stood is no longer `input:low` once a donor's value replaced it, and the
    # flag drops out on its own. That is why `low-confidence-unchanged` is no
    # longer in `_RETRACTED_BY_NAME1`: there is no code left to withdraw, and
    # the state it described withdraws itself.
    # Strictly a WITHDRAWAL, exactly like the codes above: if *field* was in
    # the derived low and its freshly re-derived provenance no longer says
    # `low`, it drops out. Nothing else in the set is re-judged — a Name 2
    # doubt raised from the department marker is not this pass's business,
    # and re-deriving the whole set here would silently discard it, because
    # the marker was consumed by `compute_flags` and is gone.
    low_before = list(getattr(result, "flag_low_confidence", None) or ())
    low_now = list(low_before)
    if field in low_now and field not in low_confidence_core_fields(result):
        low_now.remove(field)
        # Reported under the retired code's name. It is not being emitted —
        # `flag_codes` cannot contain it and `ALL_CODES` does not list it —
        # but the STATEMENT withdrawn is the one that code used to make, its
        # prose is what was rendered, and a telemetry line or a log entry
        # saying anything else would be describing a different withdrawal.
        withdrawn.append(LOW_CONFIDENCE_UNCHANGED)
    if not withdrawn and low_now == low_before:
        return ()
    details = dict(getattr(result, "flag_details", None) or {})
    notes = dict(getattr(result, "flag_notes", None) or {})
    for key, value in render(scopes, details, notes, low_now).items():
        setattr(result, key, value)
    logger.info({
        "record_id": getattr(result, "record_id", None),
        "step": "flags_retracted",
        "field": field,
        "retracted": withdrawn,
        "flag_codes": getattr(result, "flag_codes", None),
    })
    return tuple(withdrawn)


def compute_flags(result: dict[str, Any]) -> None:
    """Rebuild ``flag_codes``, ``flagged_fields``, ``flag_for_review`` and
    ``flag_reason`` from *result*'s final state, and drop the evidence keys.

    THE single flag authority. Called once, from ``finalise``. Every tier's
    job is to leave evidence behind; the decision about what that evidence
    means is taken here and nowhere else.
    """
    evidence = {k: result.pop(k, None) for k in _EVIDENCE_KEYS}

    # field base -> set of codes concerning it. A code with no entry here is
    # record-level (it concerns the record as a whole, not one column).
    scopes: dict[str, set[str]] = {}
    codes: set[str] = set()
    # code -> the specific value the code is about, when the raising site knows
    # it (see `_DETAILED_REASONS`).
    details: dict[str, str] = {}
    # code -> one clause appended to that code's prose (Fix 3).
    notes: dict[str, str] = {}

    def raise_flag(code: str, *fields: str) -> None:
        codes.add(code)
        for field in fields:
            scopes.setdefault(code, set()).add(field)

    # ── Structural input problems ─────────────────────────────────────────
    # UC 0 (one Name overflowing into the Name below it, at any slot
    # boundary) and preprocessing's slots-full signals are the same defect
    # seen from two ends: content that the SAP field split placed wrongly,
    # or could not place at all.
    overflow_fields = evidence.get("_ev_overflow")
    if overflow_fields:
        # UC 0 reports the exact slots whose contents ran together; the
        # preprocess `slots-full` signal has no pair to name, so it scopes
        # to the whole name block. A bare True from either side falls back
        # to the block as well.
        if isinstance(overflow_fields, (list, tuple, set)):
            scoped = [f for f in NAME_SLOTS if f in set(overflow_fields)]
        else:
            scoped = []
        raise_flag(OVERFLOW, *(scoped or NAME_SLOTS))

    # UC 10. Preprocessing clears an opaque code out of Name 2..N, but never
    # out of Name 1 — a Name 1 that is only a code leaves the pipeline with
    # no name at all, and a reviewer has to supply one.
    name1 = result.get("name1_enriched")
    if name1 and _is_opaque_code(str(name1)):
        raise_flag(OPAQUE_CODE, "name1")

    if evidence.get("_ev_person_unresolved"):
        raise_flag(PERSON_UNRESOLVED, "name1")

    # The Wikidata crosswalk lane found the matched item dissolved (`P576`) or
    # replaced (`P1366`). Scoped to Name 1 because that is the field whose
    # value the finding is about: the string is not wrong, the entity behind it
    # is gone. Raised whatever else the lane did — a dissolved entity's LEI
    # record is still informative and is still followed, and the flag stands
    # regardless of whether that crosswalk resolved.
    superseded = evidence.get("_ev_entity_superseded")
    if superseded:
        raise_flag(ENTITY_SUPERSEDED, "name1")
        if isinstance(superseded, str):
            details[ENTITY_SUPERSEDED] = superseded

    # ── Fix D — cross-source consistency ──────────────────────────────────
    # The gate has already acted: the losing source's fields are gone. This
    # renders the consequence. Raised only when something WAS removed, so the
    # code always describes a change the reviewer can see in the record.
    conflict = evidence.get("_ev_source_conflict")
    if conflict:
        raise_flag(
            SOURCE_CONFLICT,
            *(evidence.get("_ev_source_conflict_fields") or ("name1",)),
        )
        details[SOURCE_CONFLICT] = str(conflict)

    # The registry match STANDS; only its address is in doubt, so the flag is
    # scoped to the address and not to the name or the identifier.
    location_mismatch = evidence.get("_ev_registry_location_mismatch")
    if location_mismatch:
        raise_flag(REGISTRY_LOCATION_MISMATCH, "address")
        details[REGISTRY_LOCATION_MISMATCH] = str(location_mismatch)

    # ── Field-level uncertainty ───────────────────────────────────────────
    # A value Tier 3 wrote rests on the LLM's training data and nothing else.
    # Flagged regardless of the model's confidence: a confident unverifiable
    # claim is the more dangerous case, not the safer one. A field a later
    # authority overwrote — Fix 2's Tier 1 retry writing the registry's
    # official name — is no longer Tier 3's claim and is not flagged.
    registry_named = result.get("_registry_name_fields") or set()
    # A department the probe independently located on the organisation's own
    # web presence is no longer an evidence-free claim: `department_domain` is
    # a column a reviewer can open, exactly as `source_url` is for a stated
    # department. It answers *does this unit exist here* — which is precisely
    # the question `unverified-inference` asks — and so clears that code, and
    # only that code. It does NOT clear `low-confidence-unchanged`: a matching
    # host says the unit is real, not that the record spells it the way the
    # institution does, and those are different doubts.
    corroborated: set[str] = set()
    if result.get("department_domain"):
        corroborated.add("name2")

    # An administrative desk in Name 2 — "Accounts Payable", "Procurement
    # Services", "Central Purchasing" — is not a claim about the organisation
    # that anything could verify. There is no registry entry, no web presence
    # and no page for the accounts-payable desk of a chemicals company: the
    # phrase names WHERE IN the customer an invoice goes, not a unit whose
    # existence is in question. `search_term_2` is "ADMIN" for exactly these
    # rows, and the department-domain probe already skips them before it
    # spends a fetch (`orchestrator` §5a); flagging afterwards asks a reviewer
    # to confirm what the pipeline itself declined to look for.
    #
    # Unlike `department_domain` above, this clears BOTH name2 doubts, not
    # only `unverified-inference`. `department_domain` answers "does this unit
    # exist here" and leaves "is it spelled the way the institution spells it"
    # open; an admin desk has no institutional spelling to be wrong about.
    admin_name2 = is_admin_unit(
        (result.get("name2_enriched") or "").strip()
        or (result.get("name2_original") or "").strip()
    )
    if admin_name2:
        corroborated.add("name2")

    inferred: set[str] = set()
    for field in sorted(_evidence_free_fields(result, evidence)):
        if field in registry_named or field in corroborated:
            continue
        # Tier 3's write only stands if it survived finalisation and actually
        # differs from the input. A difference that is only casing or an
        # expanded abbreviation is not a claim about the entity — the
        # `*_changed` rules encode exactly that — so the record reads as
        # unchanged instead.
        if not result.get(f"{field}_changed"):
            continue
        inferred.add(field)
        raise_flag(UNVERIFIED_INFERENCE, field)

    # `low-confidence-unchanged` was raised here as a CODE. Retired: the state
    # it named is `input:low` on the field, the provenance says so, and
    # `render` attaches this code's own prose to the derived flag — so the
    # reviewer's sentence is unchanged and only the machine-readable token is
    # gone.
    #
    # The marker is still READ, for one reason. `_ev_low_conf_unchanged` is
    # set for the DEPARTMENT slots as well as for Name 1 (see
    # `_mark_unchanged_departments` — "there is no corroborating evidence
    # class for a unit name, so there is nothing for a three-state split to
    # read"), and Name 3..5 are not in Fix 10's Phase 1 provenance scope at
    # all. They have no attributing event, so they can carry no confidence,
    # so a purely provenance-derived rule would drop their doubt silently.
    # The 100-row chemspeed batch would not have caught it: every one of its
    # twenty low-confidence rows is Name 1.
    #
    # So the derived low is the UNION of the two — what the provenance says
    # for the fields provenance covers, and what the marker says for the
    # fields it does not yet reach. When Name 3..5 enter provenance scope,
    # this half deletes itself and nothing else changes.
    marker_low: set[str] = set()
    for field in sorted(evidence.get("_ev_low_conf_unchanged") or ()):
        if field in registry_named or field in inferred:
            continue
        # The admin-desk rule above, applied to the other half of the same
        # doubt. See `admin_name2`.
        if admin_name2 and field == "name2":
            continue
        if not result.get(f"{field}_enriched"):
            # An empty input field that stayed empty. Nothing to review.
            continue
        marker_low.add(field)

    # ── UC 13 ─────────────────────────────────────────────────────────────
    # The lab name normally lands in Name 3, but a full Name 3 sends it
    # further down the block — scope the flag to wherever it actually went.
    demoted_to = evidence.get("_ev_demoted_to") or "name3"
    if evidence.get("_ev_dept_via_lab"):
        raise_flag(DEPT_VIA_LAB, "name2", demoted_to)
    if evidence.get("_ev_name3_not_demoted"):
        raise_flag(NAME3_NOT_DEMOTED, *DEPT_SLOTS)

    # ── Contact / email / domain ──────────────────────────────────────────
    # Multiple people in one Contact field is only a problem because it stops
    # Tier 2A resolving the department against a named person's page. When
    # Tier 2A ran anyway (contact_used), the department is settled and there
    # is nothing outstanding.
    if evidence.get("_multi_contact") and not result.get("contact_used"):
        raise_flag(MULTIPLE_CONTACTS, "contact", "name2")

    if evidence.get("_ev_email_conflict"):
        raise_flag(EMAIL_CONFLICT, "email")

    # Fix 1's ownership guard rejected the candidate, so nothing was written.
    # The evidence carries the rejected domain itself when the guard knew it,
    # and the reason names it — the reviewer's job is to confirm *that* site,
    # and nothing else on the record records which one it was. An older
    # bare-True marker still raises the code, with the generic wording.
    rejected_domain = evidence.get("_domain_unverified")
    if rejected_domain:
        raise_flag(DOMAIN_UNVERIFIED, "domain")
        if isinstance(rejected_domain, str):
            details[DOMAIN_UNVERIFIED] = rejected_domain
        # Fix 3 — the page read went and looked. Appended to the existing
        # reason rather than replacing it or raising a code of its own: the
        # reviewer's task is the same ("confirm this website"), and this tells
        # them what the page actually said, which is the part they would
        # otherwise have to discover for themselves.
        page_note = evidence.get("_domain_page_note")
        if page_note:
            notes[DOMAIN_UNVERIFIED] = str(page_note)

    # ── Total miss ────────────────────────────────────────────────────────
    # Only when there is nothing more specific to say. `no-match` means "the
    # pipeline has nothing to offer"; if any other code fired, that code is
    # the actionable one and this would only add noise.
    #
    # The derived low counts as something more specific, and has to. Before
    # the provenance migration this was guarded by `low-confidence-unchanged`
    # being IN `codes`; retiring that code without moving the guard with it
    # silently promoted eleven rows of the chemspeed batch from "confirm this
    # value is correct" to "no source could identify this organisation" —
    # measured, not hypothesised. The two statements are not interchangeable:
    # the pipeline established that the record's own value stands, and
    # `no-match` denies that anything was established at all.
    low_confidence = _sorted_fields(
        set(low_confidence_core_fields(result)) | marker_low,
    )
    if not codes and not low_confidence and _nothing_was_enriched(result):
        raise_flag(NO_MATCH, "name1")

    result.update(
        render(
            {c: scopes.get(c, set()) for c in codes},
            details, notes, low_confidence,
        ),
    )
    ordered = result["flag_codes"]

    if ordered or low_confidence:
        logger.info({
            "record_id": result.get("record_id"),
            "step": "flags_computed",
            "flag_codes": ordered,
            "flagged_fields": result["flagged_fields"],
            "low_confidence_fields": low_confidence,
        })
