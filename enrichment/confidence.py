"""Provenance Scheme B — ``source:confidence[+witness]``, and the one
function that decides the confidence.

The scheme this replaces was ``producer:tier:method``: ``ror:1:exact``,
``llm_tier3:3:self_medium``, ``website_resolver:3:rule``,
``web:acme.com:extracted:2026-08-22``. Four things were wrong with it as an
*exported* representation, and all four are the same mistake — it exported the
mechanism instead of the claim.

1. **The tier is a route, not a warrant.** ``:1:`` says which lane answered,
   which tells a reviewer nothing about whether to trust the value. The same
   registry match reached through Tier 1 and through Fix 2's Tier 1 retry is
   the same claim, and it read as two.
2. **The method token was not comparable across producers.** ``exact`` on a
   registry means "an identifier was returned"; ``exact`` on a fuzzy ratio
   means "99.5 or better"; ``self_high`` means a model asserted something
   about its own output. Three tokens in one slot, on three scales, and a
   consumer sorting the column got nonsense.
3. **``self_*`` leaked a model's self-assessment into an authority claim.** A
   confident unverifiable assertion is the more dangerous case, not the safer
   one, and ``self_high`` read as the safer one.
4. **The date in ``extracted:{date}`` decayed.** It made the column
   irreproducible for eleven rows of a hundred before Fix B pinned it to the
   fetch date, and it belongs in the evidence cache and the trace, which is
   where a reviewer can act on it.

What replaces it answers exactly one question — *how much weight may I put on
this value, and who else says so* — in two tokens and an optional third::

    provenance := source ":" confidence ( "+" witness )?
    source     := "input" | "ror" | "gleif" | "wikidata" | "web:" domain | "llm"
    confidence := "verified" | "provisional" | "low"
    witness    := "web" | "wikidata" | "llm" | "registry" | "domain" | "dba"

Everything the old scheme carried and this one does not — the tier, the match
mode, the fuzzy score, the model's self-report, the extraction date — is still
recorded, on the provenance *event* and in the trace. Nothing was deleted from
the audit trail; it was removed from the shipped column, where it was being
read as a warrant it never was.

This module is deliberately free of pipeline imports. It knows about evidence
situations, not about lanes, which is what lets every lane share it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# ── The vocabulary ────────────────────────────────────────────────────────────

VERIFIED = "verified"
PROVISIONAL = "provisional"
LOW = "low"

CONFIDENCES: tuple[str, ...] = (VERIFIED, PROVISIONAL, LOW)

#: Sources that are registries in the sense the confidence table means: they
#: *authored* the value, and the identifier they returned is the evidence.
SOURCE_INPUT = "input"
SOURCE_ROR = "ror"
SOURCE_GLEIF = "gleif"
SOURCE_WIKIDATA = "wikidata"
SOURCE_LLM = "llm"
#: ``web:`` is a prefix, not a token: the domain that follows it is the
#: evidence, and a bare ``web`` would name no page a reviewer could open.
SOURCE_WEB_PREFIX = "web:"

#: The sources that may carry a witness-less ``verified`` (hard rule 2).
#: ``wikidata`` is on this list only in its crosswalked-registry role — see
#: :func:`compute_confidence`, which never returns a witness-less ``verified``
#: for a wikidata source that did not route to a registry.
REGISTRY_SOURCES: frozenset[str] = frozenset(
    {SOURCE_ROR, SOURCE_GLEIF, SOURCE_WIKIDATA},
)

WITNESS_WEB = "web"
WITNESS_WIKIDATA = "wikidata"
WITNESS_LLM = "llm"
WITNESS_REGISTRY = "registry"
#: The record's own email domain. Named ``domain`` rather than ``email``
#: because what corroborates is the domain part; the local part is personal
#: data and never reaches provenance.
WITNESS_DOMAIN = "domain"
#: The record's own "doing business as" line. First-party, and the one witness
#: that is not external: a DBA marker is the customer stating, in the master
#: data itself, which of its names is the trading name. That is a deliberate
#: assertion rather than a value the pipeline inferred, which is what
#: separates `input:verified+dba` from `input:low` — the marker is the
#: warrant. It corroborates only the name it marks, never a name elsewhere on
#: the record.
WITNESS_DBA = "dba"

WITNESSES: tuple[str, ...] = (
    WITNESS_WEB, WITNESS_WIKIDATA, WITNESS_LLM,
    WITNESS_REGISTRY, WITNESS_DOMAIN, WITNESS_DBA,
)

#: A witness that can never carry a value to ``verified`` (hard rule 1). Kept
#: as a set rather than an ``is llm`` check so the rule reads as a policy and
#: an added model-ish witness has to be classified deliberately.
NON_CORROBORATING_WITNESSES: frozenset[str] = frozenset({WITNESS_LLM})

#: A domain label, lowercase, at least one dot. Deliberately permissive about
#: length and TLD — this validates a *shape*, and a real domain that fails a
#: strict pattern would be a silent provenance loss.
_DOMAIN = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"

#: The grammar, as one anchored expression. This is the single definition;
#: the README, the parameter docs and the finalisation assertion all quote it
#: rather than restating it.
PROVENANCE_PATTERN = (
    rf"^(?P<source>input|ror|gleif|wikidata|llm|web:{_DOMAIN})"
    rf":(?P<confidence>verified|provisional|low)"
    rf"(?:\+(?P<witness>web|wikidata|llm|registry|domain|dba))?$"
)

PROVENANCE_RE = re.compile(PROVENANCE_PATTERN)


class ProvenanceGrammarError(ValueError):
    """An emitted provenance string is not in the grammar, or breaks a hard
    rule. Raised rather than logged: a provenance column that does not parse
    is worse than an empty one, because it reads as an attribution.
    """


# ── The evidence situation ────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceSituation:
    """What is known about how one value came to be, in the terms the
    confidence table is written in.

    This is the *input* to the one confidence decision, and it deliberately
    contains no lane names, no tiers and no scores. Translating a lane's
    evidence into these terms is the adapter's job
    (:func:`enrichment.provenance.situation_for`); deciding what the terms
    mean is this module's, and no component may do it ad hoc.

    ``registry_authored``
        A ROR or GLEIF *response* produced this value — the registry returned
        it, it was not scored into place.
    ``via_wikidata_crosswalk``
        The registry hit was reached by crosswalking through a Wikidata item
        rather than by querying the registry with the record's own name.
    ``has_source``
        Some source produced this value at all. ``False`` is the "no source"
        row of the table: the input was kept because nothing came back.
    ``witness``
        An *independent* second evidence system that agrees. Independent means
        a different system, not a second read of the same one: a page fetched
        from the domain it corroborates is one source, not two, so that case
        passes ``None`` here.
    ``contradicted``
        Two sources named different things, or a guard refused the value.
    ``ambiguous``
        A near-tie, a no-match, or a name too short to identify an entity.
    ``llm_involved``
        A model is the source, or the only thing agreeing. Hard rule 1: this
        can never produce or contribute to ``verified``.
    ``canonical_proposal_equals_input``
        The one case the table lets ``+llm`` be recorded: asked what the
        organisation is called *without being shown the record's answer*, the
        model returned the string the record already held.
    """

    registry_authored: bool = False
    via_wikidata_crosswalk: bool = False
    has_source: bool = False
    witness: str | None = None
    contradicted: bool = False
    ambiguous: bool = False
    llm_involved: bool = False
    canonical_proposal_equals_input: bool = False

    def __post_init__(self) -> None:
        if self.witness is not None and self.witness not in WITNESSES:
            raise ProvenanceGrammarError(
                f"unknown witness {self.witness!r}; expected one of {WITNESSES}",
            )


# ── The one confidence decision ───────────────────────────────────────────────

def compute_confidence(evidence: EvidenceSituation) -> tuple[str, str | None]:
    """``(confidence, witness | None)`` for one evidence situation.

    THE confidence authority. Every lane's provenance passes through here and
    nothing else assigns a confidence, which is what makes the column mean the
    same thing in every row of it. The table, in order of precedence:

    ==========================================  ============  ==================
    Evidence situation                          Confidence    Witness
    ==========================================  ============  ==================
    Value authored by a registry (ROR/GLEIF)    ``verified``  ``+wikidata`` iff
                                                              crosswalked, else
                                                              none
    Non-registry value + independent second     ``verified``  required
    source agrees
    Single uncontradicted source                ``provisional`` ``+llm`` only for
                                                              canonical-proposal
                                                              -equals-input
    No source, contradicted, ambiguity/         ``low``       never
    no-match, short-name guard
    ==========================================  ============  ==================

    Two hard rules are enforced here rather than left to callers:

    1. ``llm`` as source or witness can never produce or contribute to
       ``verified``. An ``llm`` witness does not satisfy the second row — it
       falls through to ``provisional``. This is the rule the old scheme's
       ``self_high`` band quietly broke.
    2. A witness-less ``verified`` is only ever returned for a registry-
       authored value. Every other ``verified`` this function returns names
       its witness, so :func:`validate` can reject a witness-less one as
       invalid output without needing to know which lane wrote it.

    Contradiction and ambiguity are checked FIRST, before the registry row.
    A registry hit that a consistency check refused is not a verified value
    that happens to be flagged — it is a value the pipeline decided against,
    and hard rule 3 (rejected evidence never appears in provenance) means the
    column must not say ``verified`` about it.
    """
    if evidence.contradicted or evidence.ambiguous:
        return LOW, None

    if evidence.registry_authored:
        # Hard rule 1 still binds: a registry response is not a model's
        # output, so `llm_involved` on a registry-authored value means the
        # model only *proposed the query*. The registry authored the answer.
        witness = WITNESS_WIKIDATA if evidence.via_wikidata_crosswalk else None
        return VERIFIED, witness

    witness = evidence.witness
    if witness is not None and witness not in NON_CORROBORATING_WITNESSES:
        return VERIFIED, witness

    if evidence.has_source:
        if evidence.canonical_proposal_equals_input:
            return PROVISIONAL, WITNESS_LLM
        return PROVISIONAL, None

    return LOW, None


# ── Rendering and parsing ─────────────────────────────────────────────────────

def web_source(domain: str) -> str:
    """``web:{domain}`` — the source token for a value read off a page.

    Lowercased because the grammar's domain production is lowercase and a
    host name is case-insensitive; a ``web:ACME.com`` would fail validation
    for a difference that means nothing.
    """
    return f"{SOURCE_WEB_PREFIX}{(domain or '').strip().lower()}"


def render(source: str, confidence: str, witness: str | None = None) -> str:
    """Compose and validate one provenance string.

    Validation happens on the way out rather than at the finalisation
    assertion alone, so an invalid combination fails at the site that built
    it, where the stack trace still names the lane.
    """
    text = f"{source}:{confidence}"
    if witness:
        text = f"{text}+{witness}"
    validate(text)
    return text


def parse(provenance: str) -> tuple[str, str, str | None]:
    """``(source, confidence, witness | None)``.

    Use this rather than ``split(":")``: ``web:acme.com:provisional`` contains
    two colons, and the naive split puts the domain in the confidence slot.
    Every consumer that reads the column is expected to come through here.
    """
    match = PROVENANCE_RE.match(provenance or "")
    if not match:
        raise ProvenanceGrammarError(
            f"{provenance!r} is not a provenance string in the scheme "
            f"`source:confidence[+witness]`",
        )
    return match["source"], match["confidence"], match["witness"]


def validate(provenance: str) -> None:
    """Raise unless *provenance* matches the grammar AND hard rules 1–2.

    Hard rule 3 (rejected evidence never appears) is not checkable from the
    string alone — it is a property of what the pipeline chose to record, and
    it is enforced at the adapter and asserted by the per-state fixtures.
    """
    source, confidence, witness = parse(provenance)

    # Hard rule 1 — llm can never produce or contribute to `verified`.
    if confidence == VERIFIED:
        if source == SOURCE_LLM:
            raise ProvenanceGrammarError(
                f"{provenance!r}: `llm` as source can never be `verified` "
                "(hard rule 1)",
            )
        if witness in NON_CORROBORATING_WITNESSES:
            raise ProvenanceGrammarError(
                f"{provenance!r}: `+{witness}` can never carry a value to "
                "`verified` (hard rule 1)",
            )

    # Hard rule 2 — a witness-less `verified` is legal only for a registry.
    if confidence == VERIFIED and witness is None:
        if source not in REGISTRY_SOURCES:
            raise ProvenanceGrammarError(
                f"{provenance!r}: `verified` without a witness is legal only "
                f"for {sorted(REGISTRY_SOURCES)} (hard rule 2)",
            )


def validate_all(values: Iterable[str | None]) -> None:
    """Validate every non-empty string in *values*. The finalisation
    assertion's one call — an empty column is a field with no value to
    attribute and is always legal.
    """
    for value in values:
        if value in (None, ""):
            continue
        validate(str(value))
