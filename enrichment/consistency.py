"""Fix D(1) — no record ships two contradictory identities.

The motivating record: "BIC Corp", Milford CT. It left the pipeline carrying
GLEIF's correct legal name and LEI *and* a ROR id and domain belonging to a
different company — Centene on one run, Balchem on the next — with no flag, and
with Search Term 1 rewritten from the wrong ROR entity. Every individual guard
had passed. Nothing had ever compared the two registries' answers to each
other.

That is what this module does, once, at the end of ``finalise``:

**Collect what each source said this organisation is called.** ROR's official
name, GLEIF's legal name, and the identity a page read extracted into
``operating_name``. Each is recorded by its writer as a transient
``_src_*`` key.

**Compare them pairwise** with the machinery already in the codebase —
:func:`enrichment.tier1_lei._name_match_score` (``token_sort_ratio``, max of
raw and legal-form-stripped) at ``LEI_NAME_MATCH_THRESHOLD``. No new scorer, no
new threshold: this is the same supplied-name-vs-official-name question GLEIF's
guard, the page reader and the Wikidata lane all ask.

**On disagreement, keep one and null the other.** Priority is fixed:

* a registry (GLEIF or ROR) outranks the web, always — a page is a witness and
  a registry is a register;
* between GLEIF and ROR, keep whichever agrees better with the **record's own**
  supplied Name 1. Not with the enriched name: by this point the enriched name
  IS one of the two claimants, so scoring against it would hand the tie to
  whichever source happened to write last. On the BIC record the input says
  "BIC Corp", GLEIF says "BIC CORPORATION" and ROR says "Centene Corporation" —
  a comparison that is not close.

**Flag it.** ``source-conflict``, naming both entities, scoped to the fields
the losing source had written. The flag is the point: a record that silently
dropped one of two identities tells a reviewer nothing, and this one was wrong
often enough to matter.

Fix D(2) lives here too, as a much smaller rule: a registry match whose
registered locality contradicts the record's city/state keeps its match and
may gain ``registry-location-mismatch``. Flag, do not discard — a company
moving within one country is common and is not evidence that the match is
wrong.

*May* gain, because the trigger is a conjunction and the address is only half
of it. A contradiction is only a doubt about WHICH organisation this is when
the name did not settle that question on its own; where the record states the
registry's name verbatim, a disagreeing address is the register's address
being an incorporation address, which is what register addresses are. That
case is counted in the trace and left unflagged. See
:func:`apply_registry_location_check`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from enrichment.flags import REGISTRY_LOCATION_MISMATCH, SOURCE_CONFLICT
from enrichment.registry_match import (
    LOCATION_ACTION_FLAG,
    LOCATION_ACTION_TRACE,
    location_check_action,
    names_agree,
)
from enrichment.tier1_lei import _name_match_score

logger = logging.getLogger(__name__)

#: One JSON line per record the gate acted on, on its own logger — the same
#: shape as ``enrichment.trace.page`` and friends.
trace_logger = logging.getLogger("enrichment.trace.consistency")

#: Transient keys the writers leave behind, read here and dropped in
#: ``finalise``. One per source that can name an organisation.
SOURCE_KEYS: tuple[str, ...] = (
    "_src_name_ror",
    "_src_name_gleif",
    "_src_name_web",
    "_src_locality_ror",
    "_src_locality_gleif",
    "_src_stated_websites",
)

#: Source → the output fields that source authored, nulled when it loses.
#: ``operating_name`` is web's; the registries own their identifier, and ROR
#: additionally owns the domain and the search acronym it supplied.
_ROR_FIELDS: tuple[str, ...] = ("ror_id",)
_GLEIF_FIELDS: tuple[str, ...] = ("lei_id",)
_WEB_FIELDS: tuple[str, ...] = ("operating_name", "operating_name_provenance")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _agrees(a: str, b: str, threshold: float) -> bool:
    """True when two source-side names name the same organisation.

    :func:`enrichment.registry_match.names_agree` — the ratio this gate has
    always used, OR normalised token-set containment. The containment half is
    Fix 3: the two registries do not answer the same question about a name.
    GLEIF returns the FORMAL LEGAL name and ROR returns the BRAND, and
    "CORTEVA AGRISCIENCE LLC" against "Corteva" scores 53.8 on a
    length-sensitive ratio — a same-entity agreement that this gate was
    reading as a contradiction, and acting on by deleting the ROR id, the ROR
    domain and the ROR acronym from a record whose two sources agreed.

    Absence is still not conflict: with one side empty there is nothing to
    disagree about, the same rule the locality comparator applies to a missing
    address.
    """
    if not a or not b:
        return True
    return names_agree(a, b, threshold)


def apply_cross_source_gate(
    result: Any, threshold: float,
) -> dict[str, Any] | None:
    """Reconcile the identities on *result*. Returns the action taken, or None.

    Called from ``finalise`` before ``compute_flags`` — the flag has to be able
    to see what this did, and the search-term derivation has to run after it,
    on the fields that survived.

    Writes ``_ev_source_conflict`` (the reason detail) and
    ``_ev_source_conflict_fields`` (its scope) for ``compute_flags`` to render.
    Never raises.
    """
    ror_name = _clean(result.get("_src_name_ror"))
    gleif_name = _clean(result.get("_src_name_gleif"))
    web_name = _clean(result.get("_src_name_web"))
    record_name = _clean(result.get("name1_original"))

    present = [n for n in (ror_name, gleif_name, web_name) if n]
    if len(present) < 2:
        return None

    registry_conflict = (
        ror_name and gleif_name
        and not _agrees(ror_name, gleif_name, threshold)
    )

    if ror_name and gleif_name and not registry_conflict:
        # Fix 3 — the OTHER outcome of the same comparison, and the one this
        # gate had no name for. Two registers, queried independently, naming
        # one organisation is a double-witness confirmation: both identifiers
        # stay on the record and the name keeps the provenance its own
        # registry earned (`gleif:verified` — a registry-authored value is
        # already `verified`, so there is no new state to invent and none is
        # invented here). What was missing was the RECORD of it, because
        # "the gate did nothing" and "the gate found the two sources agreed"
        # left identical evidence behind and only one of them is a finding.
        _record_agreement(result, ror_name, gleif_name, record_name)

    if registry_conflict:
        # Between two registries, the one whose name is closer to what the
        # customer master actually says. Ties go to GLEIF: on the company
        # branch it is the register of legal entities, and ROR is a research
        # registry that happens to also carry companies.
        ror_fit = _name_match_score(record_name, ror_name) if record_name else 0.0
        gleif_fit = (
            _name_match_score(record_name, gleif_name) if record_name else 0.0
        )
        if ror_fit > gleif_fit:
            return _resolve(
                result, keep="ROR", kept_name=ror_name,
                dropped="GLEIF", dropped_name=gleif_name,
                dropped_fields=_GLEIF_FIELDS,
                kept_fit=ror_fit, dropped_fit=gleif_fit,
                record_name=record_name,
            )
        return _resolve(
            result, keep="GLEIF", kept_name=gleif_name,
            dropped="ROR", dropped_name=ror_name,
            dropped_fields=_ROR_FIELDS + _ror_web_fields(result),
            kept_fit=gleif_fit, dropped_fit=ror_fit,
            record_name=record_name,
        )

    # A registry against the web. The registry wins by rank, not by score.
    registry_name = gleif_name or ror_name
    registry_label = "GLEIF" if gleif_name else "ROR"
    if registry_name and web_name and not _agrees(
        registry_name, web_name, threshold,
    ):
        return _resolve(
            result, keep=registry_label, kept_name=registry_name,
            dropped="web", dropped_name=web_name,
            dropped_fields=_WEB_FIELDS,
            kept_fit=None, dropped_fit=None,
            record_name=record_name,
        )
    return None


#: Records where two registries independently named one organisation. A batch
#: counter for the same reason `_location_unconfirmed` is one: it answers a
#: question about the population ("how often do the two registers corroborate
#: each other?"), which no single row can answer.
_registry_agreements = 0


def registry_agreement_count() -> int:
    """Records where ROR and GLEIF independently named one organisation."""
    return _registry_agreements


def _record_agreement(
    result: Any, ror_name: str, gleif_name: str, record_name: str,
) -> None:
    """Note a two-registry agreement. Changes no field; writes one trace line."""
    global _registry_agreements
    _registry_agreements += 1
    result["_ev_registry_agreement"] = (
        f"GLEIF identifies it as {gleif_name!r} and ROR as {ror_name!r}"
    )
    line = {
        "record_id": result.get("record_id"),
        "step": "source_agreement",
        "gleif_entity": gleif_name,
        "ror_entity": ror_name,
        "record_name": record_name,
        "score": _name_match_score(ror_name, gleif_name),
    }
    logger.info(line)
    trace_logger.info(json.dumps(line, default=str))


def _ror_web_fields(result: Any) -> tuple[str, ...]:
    """The non-identifier fields ROR authored on this record.

    A ROR match supplies its ``links[]`` website as the domain and its acronym
    as the search handle. When ROR loses the consistency check those are that
    entity's too — centene.com on a BIC record is exactly as wrong as Centene's
    ROR id, and leaving it behind is how the losing entity keeps writing
    Search Term 1 (Fix D3).
    """
    fields: list[str] = []
    if (result.get("domain_verified_by") or "") == "registry":
        fields.extend(("domain", "website_url"))
    if result.get("_ror_acronym"):
        fields.append("_ror_acronym")
    return tuple(fields)


def _resolve(
    result: Any,
    *,
    keep: str,
    kept_name: str,
    dropped: str,
    dropped_name: str,
    dropped_fields: tuple[str, ...],
    kept_fit: float | None,
    dropped_fit: float | None,
    record_name: str,
) -> dict[str, Any]:
    """Null the losing source's fields and leave the evidence for the flag."""
    from enrichment.provenance import SCOPED_FIELDS, deterministic_evidence

    nulled: list[str] = []
    for field in dropped_fields:
        if not result.get(field):
            continue
        nulled.append(field)
        if field in SCOPED_FIELDS:
            result.write(
                field, None,
                deterministic_evidence(
                    "fixD:cross-source-conflict",
                    producer="consistency_gate",
                    evidence_ref={
                        "dropped_source": dropped,
                        "dropped_entity": dropped_name,
                        "kept_source": keep,
                        "kept_entity": kept_name,
                        "dropped_value": result.get(field),
                    },
                ),
            )
        else:
            result[field] = None

    detail = (
        f"{keep} identifies it as {kept_name!r} and {dropped} as "
        f"{dropped_name!r}; the {dropped} fields were removed"
    )
    result["_ev_source_conflict"] = detail
    # Scope: Name 1 always (the identity itself is what disagreed), plus the
    # domain when that is one of the fields withdrawn.
    scope = ["name1"]
    if "domain" in nulled:
        scope.append("domain")
    result["_ev_source_conflict_fields"] = scope

    action = {
        "record_id": result.get("record_id"),
        "step": "source_conflict",
        "kept": keep,
        "kept_entity": kept_name,
        "kept_fit": kept_fit,
        "dropped": dropped,
        "dropped_entity": dropped_name,
        "dropped_fit": dropped_fit,
        "record_name": record_name,
        "nulled_fields": nulled,
    }
    logger.info(action)
    trace_logger.info(json.dumps(action, default=str))
    return action


#: Records whose registry match contradicted the record's location but whose
#: name match was exact — the match stands, unflagged, and this counts them.
#: A module counter rather than a per-record field because it answers a
#: question about the BATCH ("how often is the register's address not the
#: operating address?"), which is not a question any one row can answer. The
#: trace line carries the per-record detail.
_location_unconfirmed = 0


def registry_location_unconfirmed_count() -> int:
    """Exact-tier matches whose registered locality contradicted the record."""
    return _location_unconfirmed


def reset_consistency_counters() -> None:
    """Zero the batch counters (per batch / between tests)."""
    global _location_unconfirmed, _registry_agreements
    _location_unconfirmed = 0
    _registry_agreements = 0


def apply_registry_location_check(result: Any) -> dict[str, Any] | None:
    """Fix D(2) — flag a registry match whose locality contradicts the record.

    The comparison itself already happened, inside the registry client, against
    every address the registry publishes for the entity, with the comparator
    the page read uses; the verdict travelled here on ``_src_locality_*``.

    What is decided here is what a *contradiction* is worth, and that depends
    entirely on how the entity was identified in the first place:

    **Exact-tier name match** — the record states the registry's name verbatim
    (see :func:`enrichment.registry_match.name_match_tier`). The entity has
    been identified by its name, and the address disagreeing is then a fact
    about the organisation's geography: the register holds the incorporation or
    head-office address and the record holds a plant. Arkema Inc. is registered
    in King of Prussia PA and the chemspeed record names its North Carolina
    site; both are true, neither is a doubt about which company this is.
    ``registry_location_unconfirmed`` in the trace, no flag on the row — a
    flag that fires on the normal case teaches reviewers to clear it unread.

    **Anything weaker** — a fuzzy match, a collision-prone short name, or a
    crosswalk that followed a pointer instead of a name. There the address is
    the second opinion on an identification that had no anchor, and its
    disagreeing is exactly the doubt ``registry-location-mismatch`` exists to
    raise. Flag, and keep the match: a same-country relocation is still the
    more likely explanation, and this is an advisory, not a rejection.

    (A contradiction on a short name that had NO corroborating signal never
    reaches here at all: Fix C(3) refused that match outright in the client.)
    """
    global _location_unconfirmed

    # Observations the comparator declined to act on — a city difference
    # inside an agreeing region. They are the evidence for the granularity
    # rule, so they are traced whether or not anything else happened on the
    # record; a rule that silently forgives leaves nothing to audit.
    quiet: list[dict[str, Any]] = []

    for key, registry in (
        ("_src_locality_gleif", "GLEIF"),
        ("_src_locality_ror", "ROR"),
    ):
        info = result.get(key)
        if not isinstance(info, dict):
            continue
        action = location_check_action(info.get("verdict"), info.get("tier"))
        if action not in (LOCATION_ACTION_TRACE, LOCATION_ACTION_FLAG):
            for note in info.get("notes") or []:
                quiet.append({"registry": registry, "note": note})
            continue
        detail = info.get("detail") or "the registry places it elsewhere"
        tier = info.get("tier")
        line = {
            "record_id": result.get("record_id"),
            "registry": registry,
            "detail": detail,
            "scope": info.get("scope"),
            "name_match_tier": tier,
            "notes": info.get("notes") or [],
        }
        # THE trigger, for every lane. `location_check_action` is the one
        # implementation; this loop no longer knows the rule, only how to act
        # on its answer. See `enrichment.registry_match`.
        if action == LOCATION_ACTION_TRACE:
            _location_unconfirmed += 1
            line["step"] = "registry_location_unconfirmed"
            logger.info(line)
            trace_logger.info(json.dumps(line, default=str))
            return line
        result["_ev_registry_location_mismatch"] = f"{registry} {detail}"
        line["step"] = "registry_location_mismatch"
        logger.info(line)
        trace_logger.info(json.dumps(line, default=str))
        return line

    if quiet:
        line = {
            "record_id": result.get("record_id"),
            "step": "registry_location_note",
            "notes": quiet,
        }
        logger.info(line)
        trace_logger.info(json.dumps(line, default=str))
        return line
    return None


def record_registry_identity(
    result: Any,
    registry: str,
    res: dict[str, Any],
    *,
    name: str | None,
) -> None:
    """Note what *registry* said this organisation is called, and where.

    Called at every point a registry match is accepted onto a record, so the
    gate at the end of ``finalise`` has both sides to compare. Purely a record
    of what was claimed — it decides nothing.
    """
    key = "gleif" if registry.lower() in ("gleif", "lei") else "ror"
    if name and str(name).strip():
        result[f"_src_name_{key}"] = str(name).strip()
    # The official website the registry STATES for this entity — ROR's
    # `links[]`, already parsed into `website` by the client. Retained rather
    # than consumed at the point the domain is first proposed, because the
    # domain the web paths find is not proposed until finalisation and this
    # claim has to still be on the record when it is: it is the evidence
    # behind `web:{domain}:verified+registry`. GLEIF publishes no website
    # field, so this is a no-op on that lane.
    stated = str(res.get("website") or "").strip()
    if stated:
        websites = list(result.get("_src_stated_websites") or ())
        entry = ("registry", stated)
        if entry not in websites:
            websites.append(entry)
        result["_src_stated_websites"] = websites
    result[f"_src_locality_{key}"] = {
        "verdict": res.get("location_verdict"),
        "detail": res.get("location_detail"),
        "scope": res.get("location_scope"),
        # The granularity notes the comparator did NOT act on (a city
        # difference inside an agreeing region) — trace only, by design.
        "notes": res.get("location_notes") or [],
        # How strongly the name identified this entity. The whole trigger
        # turns on it; see `apply_registry_location_check`.
        "tier": res.get("name_match_tier"),
    }
