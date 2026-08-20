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

from enrichment.preprocess import _is_opaque_code
from enrichment.provenance import SCOPED_FIELDS, FIELD_LABELS as PROV_FIELD_LABELS, weak_fields
from utils.name_slots import DEPT_SLOTS, NAME_SLOT_LABELS, NAME_SLOTS

logger = logging.getLogger(__name__)


# ── Flag codes ────────────────────────────────────────────────────────────────
#
# The machine-readable vocabulary. `flag_codes` holds a subset of these; a
# record can carry several. Each has exactly one detection rule below and one
# prose template in `_REASONS`.

NO_MATCH = "no-match"
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

ALL_CODES: tuple[str, ...] = (
    NO_MATCH,
    LOW_CONFIDENCE_UNCHANGED,
    DEPT_VIA_LAB,
    PERSON_UNRESOLVED,
    OVERFLOW,
    OPAQUE_CODE,
    DOMAIN_UNVERIFIED,
    EMAIL_CONFLICT,
    NAME3_NOT_DEMOTED,
    MULTIPLE_CONTACTS,
    UNVERIFIED_INFERENCE,
)

# Emission order — most structural first, so the leading clause of a
# multi-code reason is the one that most changes what a reviewer does.
_CODE_ORDER: tuple[str, ...] = (
    OVERFLOW,
    OPAQUE_CODE,
    PERSON_UNRESOLVED,
    NO_MATCH,
    UNVERIFIED_INFERENCE,
    LOW_CONFIDENCE_UNCHANGED,
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
    if (
        result.get("domain")
        or result.get("department_domain")
        or result.get("source_url")
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


def render(
    scopes: dict[str, Iterable[str]],
    details: dict[str, str] | None = None,
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
    """
    ordered = [c for c in _CODE_ORDER if c in scopes]
    scoped = {c: _sorted_fields(set(scopes[c] or ())) for c in ordered}
    kept = {
        c: str(v) for c, v in (details or {}).items()
        if c in scoped and c in _DETAILED_REASONS and v
    }

    reasons: list[str] = []
    flagged: set[str] = set()
    for code in ordered:
        fields = scoped[code]
        flagged.update(fields)
        prose = (
            _DETAILED_REASONS[code].format(detail=kept[code])
            if code in kept
            else _REASONS[code]
        )
        reasons.append(f"{_label(fields)}: {prose}" if fields else prose)

    return {
        "flag_codes": ordered,
        "flagged_fields": _sorted_fields(flagged),
        "flag_for_review": bool(ordered),
        "flag_reason": "; ".join(reasons) if reasons else None,
        "flag_scopes": scoped,
        "flag_details": kept,
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

    if not withdrawn:
        return ()
    details = dict(getattr(result, "flag_details", None) or {})
    for key, value in render(scopes, details).items():
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

    # Canonicalisation was attempted, came back below threshold, and the input
    # value was left in place. Per field, because that is how the doubt is
    # actually shaped. Deliberately NOT conditioned on `*_changed`: the value
    # may still have been title-cased or had an abbreviation expanded on the
    # way out, and neither settles the question the low confidence raised.
    # What does settle it is a later authority writing the field — the
    # registry, or Tier 3, whose own code then describes it.
    for field in sorted(evidence.get("_ev_low_conf_unchanged") or ()):
        if field in registry_named or field in inferred:
            continue
        if not result.get(f"{field}_enriched"):
            # An empty input field that stayed empty. Nothing to review.
            continue
        raise_flag(LOW_CONFIDENCE_UNCHANGED, field)

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

    # ── Total miss ────────────────────────────────────────────────────────
    # Only when there is nothing more specific to say. `no-match` means "the
    # pipeline has nothing to offer"; if any other code fired, that code is
    # the actionable one and this would only add noise.
    if not codes and _nothing_was_enriched(result):
        raise_flag(NO_MATCH, "name1")

    result.update(render({c: scopes.get(c, set()) for c in codes}, details))
    ordered = result["flag_codes"]

    if ordered:
        logger.info({
            "record_id": result.get("record_id"),
            "step": "flags_computed",
            "flag_codes": ordered,
            "flagged_fields": result["flagged_fields"],
        })
