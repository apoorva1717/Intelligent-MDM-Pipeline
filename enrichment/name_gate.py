"""One write gate for every Name 1 / Name 2 candidate (§2).

Before this module a registry name and an LLM name reached the output field by
different routes, with different checks, and a rejection on either route
vanished — the record shipped its input and the reviewer was told the canonical
form "could not be established", which named neither the candidate nor the
reason. Two consequences the corpus shows directly: a UK-registered Dow entity
shipped ``gleif:verified`` onto a Midland, Michigan record because the registry
route never asked the country question the LLM route asks, and a correct LLM
answer was discarded silently because the identity guard was binary.

Every candidate for a name field now passes :func:`evaluate`, whatever
produced it. The checks are recomputed here, from the record, rather than
trusted from whatever the producing lane decided:

* the ``different`` verdict — the hallucination wall (:mod:`utils.name_identity`
  for Name 1, :func:`enrichment.tier2_canonical.subject_preserved` for the
  department slots, whose vocabulary is the one that fits a unit name);
* **country** — a candidate whose registry states a country the record
  contradicts is refused, and a record whose own country is missing or not a
  valid ISO code gets no fuzzy registry accept at all;
* address-shaped names, empty answers, and a candidate carrying
  ``entity-superseded`` evidence, which may never overwrite a name.

A refusal is never silent: :class:`GateDecision` carries the candidate and the
reason, and the caller renders both into the flag detail. That is the whole
point of routing both lanes through one place — "nothing is discarded" is a
property of the gate, not a promise each caller has to keep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from enrichment.locality import split_site_suffix
from enrichment.tier2_canonical import subject_preserved
from enrichment.tier3_llm import _is_address_like_name
from utils.name_identity import (
    DIFFERENT,
    SAME,
    UNDECIDABLE,
    classify_name_change,
)

logger = logging.getLogger(__name__)

#: Why a candidate was refused. Recorded on the decision and counted per batch.
REASON_EMPTY = "empty"
REASON_ADDRESS_LIKE = "address_like"
REASON_DIFFERENT_ENTITY = "different_entity"
REASON_COUNTRY_CONFLICT = "country_conflict"
REASON_NO_COUNTRY_FUZZY = "no_country_fuzzy_registry"
REASON_SUPERSEDED = "entity_superseded"

@dataclass
class GateDecision:
    """What the gate decided about one candidate, and why."""

    #: True when the candidate may be written into the field.
    allow: bool
    #: ``same`` / ``undecidable`` / ``different`` — recorded in provenance and
    #: read by the flag layer to decide selectivity.
    verdict: str = SAME
    #: Set only on a refusal; one of the ``REASON_*`` constants.
    reason: str | None = None
    #: The refused candidate, verbatim, for the flag detail. Never dropped.
    suggestion: str | None = None

    @property
    def flagged(self) -> bool:
        """True when a written value still owes the reviewer a look."""
        return self.allow and self.verdict == UNDECIDABLE


def _valid_iso_country(country: str | None) -> bool:
    """True when the record states a country as a usable ISO code.

    A blank or free-text country is not a country the registry answer can be
    checked against, and §2 makes that the condition for refusing a fuzzy
    registry accept: without it the match rests on the name alone, which is
    exactly how a same-named entity in another jurisdiction is adopted.
    """
    from enrichment.locality import normalise_country

    code = normalise_country(country)
    return bool(code) and len(code) == 2 and code.isalpha()


def _registry_country_conflict(
    result: dict[str, Any], registry: str | None,
) -> tuple[bool, str | None]:
    """``(conflict, detail)`` for a registry candidate's stated country.

    Reads the locality verdict the registry client already computed and left
    on the result. Only a ``country``-scope contradiction blocks: a city or
    region difference is a plant against a head office, which
    ``registry-location-mismatch`` reports as the advisory it is.
    """
    from enrichment.locality import CONTRADICTED

    keys = (
        ("_src_locality_gleif", "GLEIF"),
        ("_src_locality_ror", "ROR"),
    )
    for key, name in keys:
        if registry and name.lower() != str(registry).lower():
            continue
        info = result.get(key)
        if not isinstance(info, dict):
            continue
        if info.get("verdict") == CONTRADICTED and info.get("scope") == "country":
            return True, info.get("detail") or f"{name} states another country"
    return False, None


def _verdict_for(
    field: str,
    incumbent: str | None,
    candidate: str,
    context: tuple[str | None, ...] = (),
    *,
    city: str | None = None,
    region: str | None = None,
) -> str:
    """The identity verdict for *candidate* in *field*.

    Name 1 asks the company question (:func:`classify_name_change`); a
    department slot asks the subject question, because the company comparator
    rejects every legitimate unit rewrite — its addable vocabulary carries
    "University" and "Institute", not "Department". ``subject_preserved`` is
    already the pipeline's answer for that slot and is reused rather than
    re-derived, so Tier 2 and the gate can never disagree.

    A trailing site qualifier on the incumbent — "Merck Research Laboratories
    - Rahway, NJ" — is taken off before either question is asked. It names the
    site, not the entity, and leaving it on makes DROPPING it look like a
    subject swap: "rahway" and "nj" are tokens the canonical form does not
    keep, so the gate refused "Merck Research Laboratories" as a different
    unit and the record shipped the raw value with "the canonical form could
    not be established". Preprocessing takes the same suffix off the FIELD;
    this takes it off the RECORD's value, which is what the gate compares
    against (`_slot_input_value` reads the raw SAP text).
    """
    if not incumbent or not incumbent.strip():
        return SAME
    site = split_site_suffix(incumbent, city=city, region=region)
    if site:
        incumbent = site[0]
    if field == "name1":
        return classify_name_change(incumbent, candidate, context)
    if not subject_preserved(incumbent, candidate):
        return DIFFERENT
    # The unit survived. Whether every token of it did is what separates a
    # clean rewrite from one a reviewer should see, and the company
    # comparator answers that part without being allowed to veto.
    return (
        SAME if classify_name_change(incumbent, candidate) == SAME
        else UNDECIDABLE
    )


def evaluate(
    result: dict[str, Any],
    field: str,
    candidate: str | None,
    *,
    incumbent: str | None = None,
    street: str | None = None,
    country: str | None = None,
    registry: str | None = None,
    from_registry: bool = False,
    exact_name_match: bool = False,
    check_identity: bool = True,
    settings: Any = None,
) -> GateDecision:
    """Decide whether *candidate* may be written into *field*.

    ``from_registry`` marks a registry-supplied name, which is what makes the
    country checks apply; ``exact_name_match`` says the record stated the
    registry's name verbatim, which is what survives a missing record country.

    ``check_identity`` is False where the entity was identified by something
    other than its name — a direct registry match on the record's own text, or
    a Wikidata pointer followed to a ROR id. The identity verdict compares two
    NAMES, and where the identification did not rest on the name it can only
    second-guess a settled match: ROR's "University of Florida" reached from a
    QID is not disproved by the record saying something else. It stays True on
    every path where a MODEL proposed the text — including a model-proposed
    name used as a registry query, which is the route by which "Thermal
    Scientific Inc" acquired ROR's "Thermo Fisher Scientific".
    """
    authoritative = bool(
        getattr(settings, "llm_fallback_authoritative", True),
    )

    if not (candidate and str(candidate).strip()):
        return GateDecision(False, SAME, REASON_EMPTY, None)
    value = str(candidate).strip()

    # An organisation that no longer exists is a business decision, not a data
    # correction — the flag hands the successor over and the name stands.
    if result.get("_ev_entity_superseded") and field == "name1":
        return GateDecision(False, SAME, REASON_SUPERSEDED, value)

    if _is_address_like_name(value, street):
        return GateDecision(False, SAME, REASON_ADDRESS_LIKE, value)

    if from_registry:
        conflict, detail = _registry_country_conflict(result, registry)
        if conflict:
            logger.info({
                "record_id": result.get("record_id"),
                "step": "name_gate_country_conflict",
                "field": field,
                "candidate": value,
                "registry": registry,
                "detail": detail,
            })
            return GateDecision(
                False, SAME, REASON_COUNTRY_CONFLICT, value,
            )
        # No usable country on the record: a fuzzy registry match has nothing
        # to check its name against, so only a verbatim name match is accepted.
        if not _valid_iso_country(country) and not exact_name_match:
            logger.info({
                "record_id": result.get("record_id"),
                "step": "name_gate_no_country_fuzzy_refused",
                "field": field,
                "candidate": value,
                "registry": registry,
                "record_country": country,
            })
            return GateDecision(
                False, SAME, REASON_NO_COUNTRY_FUZZY, value,
            )

    # The record's own geography, so a registry naming the same entity by its
    # region is not read as naming a different one. See `classify_name_change`.
    context = tuple(
        v for v in (
            result.get("city"), result.get("region"), result.get("state"),
        ) if v
    )
    verdict = _verdict_for(
        field, incumbent, value, context,
        city=result.get("city"),
        region=result.get("region") or result.get("state"),
    )

    if not check_identity:
        return GateDecision(True, verdict if verdict != DIFFERENT else UNDECIDABLE,
                            None, None)

    if verdict == DIFFERENT and from_registry and not context:
        # A registry match with no geography on the record to compare against.
        # The identity verdict cannot tell a place-name from a brand here —
        # ROR's "Mayo Clinic in Florida" against "Mayo Clinic FLA" reads as an
        # invented word without the record's state to recognise it — and a
        # registry has already identified the entity. Deferred rather than
        # refused; the country check above is the registry-side question that
        # does not need this context.
        logger.info({
            "record_id": result.get("record_id"),
            "step": "name_gate_registry_verdict_deferred_no_geography",
            "field": field,
            "input_value": incumbent,
            "candidate": value,
        })
        return GateDecision(True, UNDECIDABLE, None, None)

    if verdict == DIFFERENT:
        logger.info({
            "record_id": result.get("record_id"),
            "step": "name_gate_different_entity",
            "field": field,
            "input_value": incumbent,
            "candidate": value,
        })
        return GateDecision(False, DIFFERENT, REASON_DIFFERENT_ENTITY, value)

    if verdict == UNDECIDABLE and not authoritative:
        # Legacy policy: only a proven-same rewrite was ever written.
        return GateDecision(False, UNDECIDABLE, REASON_DIFFERENT_ENTITY, value)

    if verdict == UNDECIDABLE and not getattr(
        settings, "undecidable_writes", True,
    ):
        return GateDecision(False, UNDECIDABLE, REASON_DIFFERENT_ENTITY, value)

    return GateDecision(True, verdict, None, None)
