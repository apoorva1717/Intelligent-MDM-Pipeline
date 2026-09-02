"""Batch consensus pass — one identity per organisation per address (Fix 6).

Every record is enriched in isolation, so two rows naming the same organisation
at the same address can leave the batch carrying different identities: one
resolved against a registry and the other did not. Fix 2's `Tier 1 re-lookup
after canonicalisation` recovers most of these at source; this pass is the
safety net for whatever the retry does not catch, and it is cheaper to prevent
the divergence here than to have Phase 2 adjudicate it later.

**Two tiers of consensus.** A group whose members carry exactly one registry
identity propagates all six organisation-level fields from a registry-resolved
donor — the registry is the authority. A group carrying *no* registry identity
falls back to a strictly weaker rule: it never chooses between competing values,
it only fills gaps where the group is already unanimous. `name1_enriched` is
the single exception, because its competing values are surface variants of a
name every member already holds.

**What this is not.** It never merges, drops or deduplicates records — Phase 2
remains the only place entities are merged. It is field propagation within one
batch: the record count in equals the record count out. It never changes
`tier_used`.

**Flags.** It raises none, and a record that inherits keeps every flag it
earned on its own — with one exception, which is not a judgement about the
record but bookkeeping about this pass's own write. A flag is a statement about
a field's value; replacing the value can make the statement false. Three demo
rows spelling one company three ways converge on one Name 1, and the row whose
flag read "left exactly as supplied" was then not left as supplied — two
identical names, one flagged. So a propagated `name1_enriched` withdraws
exactly the codes it falsified (`_RETRACTED_BY_NAME1`) and nothing else.

**Ordering.** It runs after every record has been finalised and before
serialisation (`Orchestrator.enrich_batch`). Propagated values therefore come
from an already-finalised record and are copied verbatim — never re-run through
Fix 4's abbreviation expansion or Fix 5's casing, which would corrupt a
registry-owned name.

**The grouping key is internal.** Both halves of it — the address block id and
the canonicalised name — are dictionary keys and nothing else, under the same
contract as Fix 2's cache key: never written to output, never sent to any API,
never placed in an LLM prompt, and never fed to `_compute_name_score()` or any
other scoring path.
"""

from __future__ import annotations

import logging
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from rapidfuzz import fuzz

from dedup.candidates import strip_legal_suffix
from dedup.models import DedupRow
from dedup.signatures import derive_block_id, normalize_key
from enrichment.flags import (
    LOW_CONFIDENCE_UNCHANGED,
    NO_MATCH,
    UNVERIFIED_INFERENCE,
    retract as retract_flags,
)
from enrichment.provenance import (
    SCOPED_FIELDS,
    derived_scalar,
    inherited_evidence,
    log_from_dicts,
)
from enrichment.tier1_ror import _normalise_for_tokens
from utils.name_slots import DEPT_ENRICHED_FIELDS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.models import EnrichmentResult

logger = logging.getLogger(__name__)


# Organisation-level fields: true of the legal entity, so they are the same for
# every row that names that entity at that address.
PROPAGATED_FIELDS: tuple[str, ...] = (
    "ror_id",
    "lei_id",
    "name1_enriched",
    "domain",
    "website_url",
    "record_type",
)

# Department-level or record-level, and therefore NEVER propagated. Listed as
# data so the exclusion is readable and testable rather than implied by the
# absence of a field from PROPAGATED_FIELDS. Rows 12-14 of the demo batch are
# Stanford at one address with three different departments: they must share
# Stanford's ROR id and domain and keep their own department name and
# department domain.
NEVER_PROPAGATED: tuple[str, ...] = (
    *DEPT_ENRICHED_FIELDS,
    "department_domain",
    "contact_enriched",
    "care_of_enriched",
    "email_enriched",
    "search_term_2",
    "street_cleaned",
    "house_number",
    "street_2_cleaned",
    "street_3_cleaned",
    "street_4_cleaned",
    "street_5_cleaned",
    "postal_code",
    "city",
    "country_region_key",
    "region",
)

# The `source` value written on a record that inherited at least one field.
# `tier_used` is deliberately left alone — see the module docstring.
CONSENSUS_SOURCE = "batch_consensus"

# Flag codes a propagated `name1_enriched` falsifies, by consensus mode. This
# pass still raises no flag and still judges no record; it withdraws the codes
# its OWN write made untrue, which is the same rule as leaving every other flag
# alone. Anything not listed here survives propagation untouched.
#
# `low-confidence-unchanged` goes in both modes: it means "left exactly as
# supplied", and the record was not left as supplied once a value replaced it.
# Three demo rows spelling one company three ways ended with one identical
# Name 1 and one of them still carrying that reason.
#
# The other two go only under `registry`, because only there does something
# arrive that answers them. `no-match` says no source identified the
# organisation and a registry identity now has; `unverified-inference` says the
# value rests on no external evidence and it now rests on the donor's registry
# match, recorded with the donor's id. Under `name_form` neither is answered —
# electing the batch's modal spelling introduces no evidence — so both stand.
_RETRACTED_BY_NAME1: dict[str, tuple[str, ...]] = {
    # `low-confidence-unchanged` is NOT here any more: it was retired with the
    # provenance migration, and the state it named is now read off the record's
    # own provenance. `member.write` below regenerates `name1_provenance` from
    # the inheriting event, so the derived flag is withdrawn by re-derivation
    # rather than by naming a code — see enrichment.flags.retract.
    "registry": (NO_MATCH, UNVERIFIED_INFERENCE),
    "name_form": (),
}

# A record_type of "unknown" asserts nothing (Fix 3), so it is treated as
# absent here rather than as a value that could conflict.
_UNKNOWN_TYPE = "unknown"


@dataclass
class ConsensusTelemetry:
    """Batch-level counters for the pass."""

    groups: int = 0
    records_updated: int = 0
    conflicts: int = 0
    fields_propagated: dict[str, int] = field(default_factory=dict)
    # Flag codes withdrawn because this pass replaced the value they described
    # — see _RETRACTED_BY_NAME1. Counts codes, not records: one record can
    # shed several.
    flags_retracted: int = 0


@dataclass
class _Group:
    """Members of one (address block, canonical name, legal form) group."""

    block_id: str
    base_name: str
    legal_form: str
    members: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Grouping key — internal only
# ---------------------------------------------------------------------------

def _address_block_id(result: "EnrichmentResult") -> Optional[str]:
    """The record's address block, derived by the SAME function Phase 2 uses.

    `dedup.signatures.derive_block_id` is reused rather than reimplemented, so
    a batch and its later dedup pass can never disagree about what "the same
    address" means. Returns None when the record carries no address signal at
    all — a blank address would otherwise hash every such record into one block
    and let a bare name match propagate an identity across unrelated rows.
    """
    street = result.street_cleaned
    postal = result.postal_code
    if not (street and str(street).strip()) and not (postal and str(postal).strip()):
        return None
    row = DedupRow(
        row_id=str(result.record_id or ""),
        street=street,
        house_no=result.house_number,
        postal_code=postal,
        city=result.city,
        country=result.country_region_key,
    )
    return derive_block_id(row)


def _name_parts(name: Optional[str]) -> tuple[str, str]:
    """Split a Name 1 into ``(base, legal_form)`` for grouping.

    Three existing normalisers are composed, each for the one thing it does,
    rather than a fourth being written:

    1. `dedup.signatures.normalize_key` — lowercase, trim, collapse whitespace,
       strip punctuation, fold accents. It deliberately does not touch legal
       forms, which is why steps 2 and 3 exist.
    2. `tier1_ror._normalise_for_tokens` — canonicalises US legal-entity
       suffixes to their abbreviated form (Incorporated→inc, Corporation→corp,
       Company→co, Limited→ltd), so "Acme Co" and "Acme Company" carry the
       *same* legal form rather than two spellings of it.
    3. `dedup.candidates.strip_legal_suffix` — removes the trailing legal-form
       token run, using the `LEGAL_SUFFIXES` table that already exists there.

    The legal form is returned SEPARATELY, never folded into the base. Folding
    it in would group "Delta Analytical Inc" with "Delta Analytical LLC" at a
    shared address — potentially distinct legal entities, and exactly the
    judgement Phase 2 exists to make.
    """
    if not name:
        return ("", "")
    canon = _normalise_for_tokens(normalize_key(str(name)))
    if not canon:
        return ("", "")
    base = strip_legal_suffix(canon)
    if not base or base == canon:
        # Nothing stripped, or the name IS a legal form (strip_legal_suffix
        # keeps the full name in that case rather than returning nothing).
        return (canon, "")
    return (base, canon[len(base):].strip())


def _legal_forms_compatible(a: str, b: str) -> bool:
    """Two legal forms are compatible when identical, or absent on one side."""
    return not a or not b or a == b


def _build_groups(results: list["EnrichmentResult"]) -> list[_Group]:
    """Group by address block first, then by name + legal form.

    Within one address block two rows group together when their names are equal
    after legal-form canonicalisation AND their legal forms are compatible.
    "Their names" means either the name a row SHIPS or the name it was
    SUPPLIED — see the two-key note below.
    Compatibility is not transitive — an absent form is compatible with every
    form — so when a block holds two or more DIFFERENT legal forms under one
    base name, each form gets its own group and every absent-form row is left
    on its own. Assigning it to one of the competing forms would be a guess,
    and guessing which legal entity a row belongs to is Phase 2's call.
    """
    # Two keys per record, not one: the name it SHIPS and the name it was
    # SUPPLIED. Keying on the enriched name alone asks the batch to agree
    # after the disagreement has already happened — two S3 rows at one address
    # whose inputs differ only in case ("VAMC REDDING VISN 21" /
    # "VAMC Redding Visn 21") shipped "Redding VA Clinic" and "Veterans
    # Affairs Medical Center Redding", and those two enriched names do not
    # group, so neither row could correct the other. The supplied names are
    # the same string; on that key they are obviously one organisation.
    #
    # Both keys go through `_name_parts`, so the supplied side is normalised
    # exactly as the enriched side is and no third notion of "same name"
    # enters the pass.
    by_block: "OrderedDict[str, list[tuple[int, frozenset, str, str]]]" = (
        OrderedDict()
    )
    for index, result in enumerate(results):
        block_id = _address_block_id(result)
        if not block_id:
            continue
        base, legal_form = _name_parts(result.name1_enriched)
        supplied_base, _supplied_form = _name_parts(_supplied_name(result))
        keys = frozenset(k for k in (base, supplied_base) if k)
        if not keys:
            continue
        by_block.setdefault(block_id, []).append(
            (index, keys, legal_form, base or supplied_base),
        )

    # A record joins a bucket when EITHER of its keys matches EITHER key of a
    # member already there, so one record can bridge two buckets that no
    # single key would have joined. The bridged buckets are unioned rather
    # than the record being assigned to one of them — picking a side would be
    # the guess this pass exists to avoid.
    buckets: "OrderedDict[tuple[str, str], list[tuple[int, str]]]" = OrderedDict()
    for block_id, entries in by_block.items():
        merged: list[dict] = []
        for index, keys, legal_form, base in entries:
            touching = [b for b in merged if b["keys"] & keys]
            if not touching:
                merged.append({
                    "keys": set(keys), "base": base,
                    "members": [(index, legal_form)],
                })
                continue
            head = touching[0]
            for other in touching[1:]:
                head["keys"] |= other["keys"]
                head["members"].extend(other["members"])
                merged.remove(other)
            head["keys"] |= keys
            head["members"].append((index, legal_form))
        for bucket in merged:
            # Batch order inside a bucket, which a union may have disturbed.
            # The donor ranking below tie-breaks on it, so it decides which
            # record answers for the group and must not depend on merge order.
            buckets[(block_id, bucket["base"])] = sorted(bucket["members"])

    groups: list[_Group] = []
    for (block_id, base), items in buckets.items():
        forms = {form for _, form in items if form}
        if len(forms) <= 1:
            # One legal form (or none) in play: every form present is
            # compatible with every other, so the whole bucket is one group.
            groups.append(_Group(block_id, base, next(iter(forms), ""),
                                 [index for index, _ in items]))
            continue
        by_form: dict[str, list[int]] = defaultdict(list)
        for index, form in items:
            # An absent form is keyed by its own row index, which no canonical
            # form can collide with, so it lands in a singleton group and
            # inherits nothing.
            by_form[form or f"\x00{index}"].append(index)
        for form, members in by_form.items():
            groups.append(_Group(block_id, base,
                                 "" if form.startswith("\x00") else form,
                                 members))
    return groups


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------

def _sole(values: list[Any]) -> Optional[Any]:
    """The single distinct non-null value in *values*, else None."""
    distinct = {v for v in values if v is not None and str(v).strip()}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _donor_rank(index: int, result: "EnrichmentResult") -> tuple[int, int, int]:
    """Order registry-carrying members: most ids, then earliest tier, then
    batch order. Deterministic, so the same batch always elects the same donor."""
    id_count = (1 if result.ror_id else 0) + (1 if result.lei_id else 0)
    return (-id_count, int(result.tier_used or 3), index)


def _supplied_name(member: "EnrichmentResult") -> str:
    """The Name 1 *member* arrived with, or "".

    `name1_supplied` is carried on the result from the top of `_enrich_single`,
    before any lane has written the column. The provenance log is the fallback
    and not the source: `original_value` is truthful only where the input
    reached the log as a passthrough event, and on a record whose Name 1 was
    written into an EMPTY slot by a tier it never does — the first event is
    that tier's own write, with `old_value=None`, so the log reports that the
    record supplied nothing. Two VA rows at one address, supplied the same name
    in different case, could not see each other for exactly that reason.
    """
    carried = getattr(member, "name1_supplied", None)
    if carried and str(carried).strip():
        return str(carried)
    log = log_from_dicts(
        member.provenance, member.provenance_rejected,
        member.provenance_rejected_omitted,
    )
    original = log.original_value("name1_enriched")
    return str(original) if original and str(original).strip() else ""


def _supplied_names(members: list["EnrichmentResult"]) -> list[str]:
    """Every Name 1 the group's records actually arrived with.

    Read back out of each record's provenance log, which is where the input
    passthrough is recorded — a finalised `EnrichmentResult` carries no
    `name1_original`. A member whose log has no input event contributes
    nothing; a group where none does yields an empty list, and the election
    falls through to the tie-breaks below it.
    """
    return [name for name in (_supplied_name(m) for m in members) if name]


def _input_affinity(form: str, supplied: list[str]) -> int:
    """How well *form* represents the names the group was supplied with.

    Summed similarity against every original, not just the one belonging to
    the record that produced *form*: the question is which surface form best
    stands for what the batch actually said, and a form is answering for the
    whole group once it is elected. Scored on the same
    `token_sort_ratio` the rest of the pipeline matches names with, and
    rounded so the sort key is integral and cannot turn on a float.
    """
    return round(sum(
        fuzz.token_sort_ratio(form.lower(), original.lower())
        for original in supplied
    ))


def _consensus_name_form(
    members: list["EnrichmentResult"],
    indices: list[int],
) -> Optional[str]:
    """The group's modal Name 1 surface form, for NAME-FORM consensus.

    Used only when a group holds no registry identity at all. Every member
    already carries the same organisation name — that is what put them in the
    group — and they differ only in how the legal form is spelled, punctuated
    or cased. Electing one of those surface forms asserts no new fact about the
    organisation; it picks a representation among spellings the batch already
    contains.

    The modal form wins, because the batch's own majority spelling is evidence
    and "which legal form is more correct" is not a judgement this pass is
    entitled to make.

    **Ties break on closeness to the supplied names.** A tie means the batch
    has no majority spelling, so the rule that decides it should still be one
    about the data rather than about row order — and the demo batch showed what
    happens when it is not. The Coastal trio spells one company three ways, no
    two records enrich to the same form, and the old tie-break (earliest tier,
    then batch order) elected `Coastal Diagnostics Inc.` — a string none of the
    three records was supplied with, taken from the first row purely because it
    was first. Preferring the form closest to what the customer actually typed
    elects `Coastal Diagnostics, Inc` instead, which one of them typed
    verbatim. Tier and batch order remain below it, so the election is still
    total and deterministic when a group has no originals to compare against.

    Returns None when the group already agrees.
    """
    forms: dict[str, tuple[int, int, int]] = {}
    for index, member in zip(indices, members):
        name = member.name1_enriched
        if not name:
            continue
        count, tier, first = forms.get(name, (0, 99, index))
        forms[name] = (count + 1, min(tier, int(member.tier_used or 3)),
                       min(first, index))
    if len(forms) < 2:
        return None
    supplied = _supplied_names(members)
    return min(
        forms.items(),
        key=lambda kv: (
            -kv[1][0],                            # modal count first
            -_input_affinity(kv[0], supplied),    # then closest to the input
            kv[1][1], kv[1][2],                   # then tier, then batch order
        ),
    )[0]


def _consensus_values(
    members: list["EnrichmentResult"],
    indices: list[int],
) -> Optional[tuple[dict[str, Any], str]]:
    """The values this group agrees on plus the mode that produced them, or
    None when the group holds conflicting registry identities.

    Two modes, and the weaker one is deliberately much weaker:

    ``"registry"``
        The group holds exactly one registry identity. That identity is the
        authority, so all six organisation-level fields propagate.

    ``"name_form"``
        The group holds no registry identity. `ror_id` and `lei_id` are absent
        by definition, and the remaining fields propagate under a strictly
        weaker rule: **this mode never chooses between competing values, it
        only fills gaps where the group is unanimous.** `name1_enriched` is the
        one exception, and only because its competing values are surface
        variants of a name every member already holds — picking one asserts
        nothing new. `domain`, `website_url` and `record_type` move only when
        the group holds exactly ONE distinct non-null value, so a member that
        resolved nothing inherits it and a group that disagrees keeps its
        disagreement. Note what that does and does not bypass: the surviving
        domain already satisfied the [ownership guard](#2b) on a record whose
        Name 1 is equal to the receiving record's after canonicalisation, so
        the guard's name-similarity condition would reach the same verdict here
        — the value is not attributed on weaker evidence than the guard needs,
        it is attributed on the same evidence.
    """
    rors = {m.ror_id for m in members if m.ror_id}
    leis = {m.lei_id for m in members if m.lei_id}
    if len(rors) > 1 or len(leis) > 1:
        return None

    registry_backed = bool(rors or leis)
    donor: Optional["EnrichmentResult"] = None
    if registry_backed:
        donors = [(i, m) for i, m in zip(indices, members) if m.ror_id or m.lei_id]
        donor = min(donors, key=lambda pair: _donor_rank(*pair))[1]

    values: dict[str, Any] = {
        "ror_id": next(iter(rors), None),
        "lei_id": next(iter(leis), None),
    }

    # Name 1. With a registry behind the group the donor's name wins outright:
    # after Fix 4 a verified Tier 1 match writes the registry's official name,
    # which is authoritative for the whole group. Without one, the group's modal
    # surface form wins — see _consensus_name_form.
    if donor is not None:
        values["name1_enriched"] = (
            donor.name1_enriched or _sole([m.name1_enriched for m in members])
        )
    else:
        values["name1_enriched"] = _consensus_name_form(members, indices)

    # `domain` and `website_url` are two views of one decision, so they are
    # taken from ONE record and never mixed. The donor holds them when its
    # registry hit carried a domain; otherwise the group's single distinct
    # domain, if it has exactly one, is used. A null donor value never erases a
    # domain another member resolved, and a group holding two domains
    # propagates neither.
    domain_source: Optional["EnrichmentResult"] = None
    if donor is not None and donor.domain:
        domain_source = donor
    else:
        sole_domain = _sole([m.domain for m in members])
        if sole_domain:
            domain_source = next(m for m in members if m.domain == sole_domain)
    values["domain"] = domain_source.domain if domain_source else None
    values["website_url"] = domain_source.website_url if domain_source else None

    # "unknown" asserts nothing, so it is treated as absent on both sides.
    donor_type = (
        donor.record_type
        if donor is not None and donor.record_type != _UNKNOWN_TYPE
        else None
    )
    values["record_type"] = donor_type or _sole(
        [m.record_type for m in members if m.record_type != _UNKNOWN_TYPE]
    )
    return values, ("registry" if registry_backed else "name_form")


def _snapshot(
    members: list["EnrichmentResult"],
) -> dict[int, dict[str, Any]]:
    """The members' propagated-field values BEFORE this pass writes any.

    Donor resolution has to run against this, not against live state: members
    are updated inside the loop, so a member that has itself just inherited the
    value would otherwise be named as its donor and the trail would stop one
    hop short of the record that actually resolved the organisation.
    """
    return {
        id(m): {f: getattr(m, f, None) for f in PROPAGATED_FIELDS}
        for m in members
    }


def _donor_for(
    members: list["EnrichmentResult"],
    before: dict[int, dict[str, Any]],
    receiver: "EnrichmentResult",
    field: str,
    value: Any,
) -> Optional["EnrichmentResult"]:
    """The record this ONE field's value is being copied from.

    Per field, not per group: a group can hold its Name 1 in one record and its
    LEI in another, and naming the wrong one is as unhelpful as naming none.
    The receiving record is excluded — a record cannot be its own donor, and an
    inheritance that pointed back at itself would be exactly the "inherited ROR
    ID indistinguishable from a first-hand one" the fix exists to prevent.
    """
    for member in members:
        if member is receiver:
            continue
        if before.get(id(member), {}).get(field) == value:
            return member
    return None


def _donor_scale(
    donor: Optional["EnrichmentResult"], field: str,
) -> Optional[str]:
    """The confidence scale the DONOR's own write was on.

    An inherited value is only as good as the evidence behind it, and that
    evidence is on the donor's event, not on this one — so the scale travels
    with the inheritance instead of the receiving record having to guess.
    """
    if donor is None:
        return None
    event = log_from_dicts(donor.provenance).attributing_event(field)
    return event.confidence_scale if event else None


def _donor_derived(
    donor: Optional["EnrichmentResult"], field: str,
) -> Optional[str]:
    """The donor's DERIVED ``source:confidence`` for *field*.

    What the donor actually ships, which is not the same question as what kind
    of write put it there. `_donor_scale` reads the attributing event, and on a
    passthrough that event says `deterministic` — retaining a value is a
    deterministic act — while the field derives `input:low`, because nothing
    vouched for the value that was retained. Inheriting the first number let a
    copy read as better evidenced than its original.

    The same `derived_scalar` the flags read, so "the donor is flagged low"
    and "the inheritance is low" cannot disagree.
    """
    if donor is None:
        return None
    return derived_scalar(log_from_dicts(donor.provenance), field, donor)


def apply_batch_consensus(
    results: list["EnrichmentResult"],
) -> ConsensusTelemetry:
    """Converge organisation-level fields across a finalised batch, in place.

    Returns the batch telemetry. The list is never reordered and never changes
    length — this is field propagation, not deduplication.
    """
    telemetry = ConsensusTelemetry()
    groups = [g for g in _build_groups(results) if len(g.members) >= 2]
    telemetry.groups = len(groups)

    for group in groups:
        members = [results[i] for i in group.members]
        outcome = _consensus_values(members, group.members)
        if outcome is None:
            telemetry.conflicts += 1
            logger.info(
                "%s",
                {
                    "step": "batch_consensus_conflict",
                    "block_id": group.block_id,
                    "members": [m.record_id for m in members],
                    "ror_ids": sorted({m.ror_id for m in members if m.ror_id}),
                    "lei_ids": sorted({m.lei_id for m in members if m.lei_id}),
                },
            )
            continue
        values, mode = outcome

        before = _snapshot(members)
        for member in members:
            changed: list[str] = []
            for name in PROPAGATED_FIELDS:
                new = values.get(name)
                if new is None or getattr(member, name) == new:
                    continue
                # Copied verbatim from an already-finalised record: no casing,
                # no abbreviation expansion, no re-normalisation of any kind.
                #
                # The DONOR record id travels with the value. An inherited ROR
                # ID must not be indistinguishable from a first-hand one: the
                # receiving record never looked this organisation up, and a
                # reviewer asking "where did this come from" has to be able to
                # land on the record that did.
                if name in SCOPED_FIELDS:
                    field_donor = _donor_for(members, before, member, name, new)
                    member.write(
                        name, new,
                        inherited_evidence(
                            field_donor.record_id if field_donor else "",
                            mode=mode,
                            donor_scale=_donor_scale(field_donor, name),
                            donor_derived=_donor_derived(field_donor, name),
                        ),
                    )
                else:
                    setattr(member, name, new)
                changed.append(name)
            if not changed:
                continue
            member.source = CONSENSUS_SOURCE
            if "name1_enriched" in changed:
                telemetry.flags_retracted += len(
                    retract_flags(member, _RETRACTED_BY_NAME1[mode], "name1"),
                )
            telemetry.records_updated += 1
            for name in changed:
                telemetry.fields_propagated[name] = (
                    telemetry.fields_propagated.get(name, 0) + 1
                )
            logger.info(
                "%s",
                {
                    "step": "batch_consensus_inherit",
                    "record_id": member.record_id,
                    "block_id": group.block_id,
                    "mode": mode,
                    "fields": changed,
                    "tier_used": member.tier_used,
                },
            )

    return telemetry
