"""Wikidata crosswalk lane — a pointer and a witness, never an authority.

The lane sits between the [GLEIF miss](`enrichment.tier1_lei`) and the
web-evidence step: a record that ROR missed, that GLEIF missed (or that never
reached GLEIF, on the research branch), and that therefore carries no registry
identifier at all. For those records Wikidata is asked one question — *is there
a curated item for this organisation, and does it point at a registry?*

Two outcomes, and the difference between them is the whole design:

**Crosswalk.** The matched item carries a ROR ID (``P6782``) or an LEI
(``P1278``). The lane does **not** copy Wikidata's label onto the record.
It follows the pointer: it re-queries ROR / GLEIF *by that identifier* through
the existing Tier-1 clients, and the registry's own response writes the name
and the identifier exactly as a direct Tier-1 hit would — with the registry's
provenance (``ror`` / ``gleif``), all of the registry's own guards applied
unchanged, and nothing in the output naming Wikidata at all. Wikidata bought
the lookup key and is recorded as having done so in trace and metrics; it did
not supply a value.

**Witness.** The item has no registry pointer. Then Wikidata is *one source*
and is treated as one: it may corroborate a Name 1 the pipeline kept (feeding
:mod:`enrichment.unchanged_state`'s ``unchanged-verified``), and its official
website (``P856``) may corroborate a candidate domain — but it may never write
``name1_enriched``. The most it writes is ``operating_name``, the field
:mod:`enrichment.page_corroborator` already uses for "what another source
calls this organisation".

Wikidata is an open wiki. A crowd-edited label is not a customer master's
source of truth for a legal name, and treating it as one would put an
unreviewed edit into SAP. Treating it as a *lookup key* is safe in a way that
treating it as a *value* is not: a wrong pointer resolves to a registry record
that then fails the registry's own name and country guards, and the record
misses. That asymmetry is why the crosswalk is allowed to write a name and the
witness is not.

**No LLM is involved anywhere in this lane.** Matching is deterministic:
search, then a fixed gauntlet of constraints, then the same RapidFuzz
comparison GLEIF's verification guard uses, at the same threshold. Where
ambiguity survives the gauntlet the answer is *no match* — never an LLM
tiebreak, because a tiebreak is exactly the judgement a crowd-sourced source
has not earned.

The gauntlet, in order, every step mandatory
--------------------------------------------

1. ``wbsearchentities`` on the normalised record name (labels + aliases, ``en``,
   limit 5). The SPARQL endpoint is deliberately never used: it is rate-limited,
   frequently unavailable, and a query language is a much larger surface than
   this lane needs.
2. **Disambiguation pages are a no-match** (``P31 = Q4167410``), never a list to
   pick from. "Apollo" naming eleven organisations is the registry telling us it
   cannot identify one.
3. **Type whitelist** — the item's ``P31``, or one ``P279`` step up from it, must
   land in :data:`TYPE_WHITELIST`. No transitive closure: two steps up from
   almost anything is ``organization``, and everything is an organisation.
4. **Country** — ``P17``, or ``P159`` resolved to its country, must be the
   United States (``Q30``). Missing both is a **no-match**, deliberately: see
   :data:`COUNTRY_REJECTED`.
5. **Name check** — best label/alias against the record name through
   :func:`enrichment.tier1_lei._name_match_score` at
   ``LEI_NAME_MATCH_THRESHOLD``. No new scorer and no new threshold.
6. **Identity cross-check** — an item whose ``P159`` resolves to a city that
   contradicts the record's city/region is a no-match. A missing ``P159`` is
   neutral, exactly as a page that states no address is neutral.

More than one candidate surviving all six is a **collision**, and a collision
is a no-match counted separately (:data:`AMBIGUOUS`).

Call budget
-----------

Two API calls per record: one ``wbsearchentities``, and one
``wbgetentities`` that fetches the claims of **every** search hit in a single
batched request — which is what makes the collision check affordable. A third,
*conditional and batch-shared* call resolves referenced items (a ``P159``
headquarters to its country and label, a ``P1366`` successor to its label);
it fires only for an item that carries one of those properties, is batched
into a single request per record, and is cached by QID across the whole batch,
so it costs nothing after the first record that names a given city. See
``docs/thesis/04_PARAMETERS.md`` §1.18 for why this third call exists rather
than the gauntlet silently dropping steps 4 and 6.

Every call is fixture-cached through :class:`utils.cache.PageCache` — the same
store the page reads use — keyed on the query string for a search and on the
QID for an entity fetch, so a thesis re-run reproduces its decisions instead of
re-litigating them against whatever Wikidata says today.

Failure is closed
-----------------

A timeout, a non-200, or a malformed body is :data:`UNAVAILABLE` — a no-match
that is counted apart from a real miss. A lane failure never fails a record.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from dedup.signatures import normalize_key
from enrichment.confidence import (
    PROVISIONAL,
    SOURCE_WIKIDATA,
    render as render_provenance,
)
from enrichment.locality import normalise_region
from enrichment.tier1_lei import _name_match_score
from llm.openai_client import resolve_tls_verify
from utils.cache import PageCache, note_network_call
from utils.domain_resolver import canonicalise_domain

logger = logging.getLogger(__name__)

#: One JSON line per lane invocation, on its own logger — the same shape as
#: ``enrichment.trace.retry`` and ``enrichment.trace.page``. Gated by
#: ``WIKIDATA_TRACE``; with the flag off nothing is emitted and the lane
#: behaves identically.
trace_logger = logging.getLogger("enrichment.trace.wikidata")


# ── Properties ────────────────────────────────────────────────────────────────

P_INSTANCE_OF = "P31"        # instance of
P_SUBCLASS_OF = "P279"       # subclass of
P_COUNTRY = "P17"            # country
P_HEADQUARTERS = "P159"      # headquarters location
P_WEBSITE = "P856"           # official website
P_ROR = "P6782"              # ROR ID
P_LEI = "P1278"              # Legal Entity Identifier
P_DISSOLVED = "P576"         # dissolved, abolished or demolished date
P_REPLACED_BY = "P1366"      # replaced by

#: Wikimedia disambiguation page. An item of this type names several
#: organisations and identifies none, so it is a no-match rather than a menu.
Q_DISAMBIGUATION = "Q4167410"

#: United States. The only country this lane accepts — see :data:`COUNTRY_REJECTED`.
Q_UNITED_STATES = "Q30"

#: The type gate. An item whose ``P31`` (or one ``P279`` step up from it) is not
#: in here is not the kind of thing a customer master record can be about.
#: Every QID is named, and every one was checked against live Wikidata on
#: 2026-08-23; the labels below are the labels Wikidata returns.
#:
#: The list is deliberately explicit rather than derived from a ``P279``
#: closure. Two steps up from "pharmaceutical company" is "organization", and
#: two steps up from a film production company is also "organization" — a
#: closure admits everything and gates nothing.
TYPE_WHITELIST: frozenset[str] = frozenset({
    # ── business / company ────────────────────────────────────────────────
    "Q4830453",    # business
    "Q783794",     # company
    "Q6881511",    # enterprise
    # ── university / college ──────────────────────────────────────────────
    "Q3918",       # university
    "Q189004",     # college
    "Q875538",     # public university
    "Q902104",     # private university
    "Q1336920",    # community college
    "Q1371037",    # institute of technology
    # ── research institute ────────────────────────────────────────────────
    "Q31855",      # research institute
    "Q7315155",    # research center
    "Q483242",     # laboratory
    # ── national laboratory ───────────────────────────────────────────────
    "Q2624320",    # United States national laboratory
    # ── hospital / health system ──────────────────────────────────────────
    "Q16917",      # hospital
    "Q11000047",   # health system
    "Q4287745",    # medical organization
    "Q1774898",    # clinic
    # ── government agency ─────────────────────────────────────────────────
    "Q327333",     # government agency
    "Q2659904",    # government organization
    "Q20857065",   # United States federal agency
    # ── nonprofit organisation ────────────────────────────────────────────
    "Q163740",     # nonprofit organization
    "Q708676",     # charitable organization
})

#: The one ``P279`` step, resolved from a **declared table** rather than from a
#: live query. The alternative is a third API call per unrecognised class, and
#: the two-call budget in this lane's docstring is a hard constraint — a class
#: hierarchy walk is exactly the unbounded fan-out that budget exists to
#: prevent. Every pair below was read off live Wikidata on 2026-08-23.
#:
#: A subtype that is not in this table therefore does **not** get its step up
#: and is rejected. That is the conservative direction: the lane under-matches
#: rather than admitting an unverified class, and the rejection is visible as
#: ``wikidata_type_rejected`` rather than as a silent pass.
P279_ONE_STEP: dict[str, tuple[str, ...]] = {
    "Q19644607": ("Q783794", "Q4830453"),    # pharmaceutical company
    "Q90298876": ("Q783794", "Q4830453"),    # biotechnology company
    "Q7603893": ("Q875538",),                # state public university
    "Q1336920": ("Q189004",),                # community college
    "Q11000047": ("Q4287745",),              # health system
    "Q1371037": ("Q3918",),                  # institute of technology
    "Q2624320": ("Q31855", "Q483242"),       # United States national laboratory
    "Q483242": ("Q31855",),                  # laboratory
    "Q708676": ("Q163740",),                 # charitable organization
    "Q20857065": ("Q327333",),               # United States federal agency
    "Q875538": ("Q3918",),                   # public university
    "Q902104": ("Q3918",),                   # private university
}


# ── Outcomes ──────────────────────────────────────────────────────────────────
#
# The four below partition every lane invocation. The two rejection reasons
# further down are diagnostics and deliberately overlap with NO_MATCH: a record
# whose only candidate was a film increments both `type_rejected` and
# `no_match`, because both statements are true and the second is the one that
# describes what the pipeline did.

MATCHED = "matched"
NO_MATCH = "no_match"
AMBIGUOUS = "ambiguous"          # more than one candidate survived the gauntlet
UNAVAILABLE = "unavailable"      # timeout / non-200 / malformed JSON

#: Rejection reasons, recorded per candidate and counted per record.
DISAMBIGUATION_REJECTED = "disambiguation"
TYPE_REJECTED = "type_rejected"
COUNTRY_REJECTED = "country_rejected"
NAME_REJECTED = "name_rejected"
CITY_REJECTED = "city_rejected"


@dataclass(frozen=True)
class WikidataItem:
    """The claims this lane reads off one Wikidata item.

    Everything else the API returns is discarded at parse time — a narrow
    struct is what keeps the gauntlet readable and stops a later change from
    quietly starting to depend on a property nobody declared.
    """

    qid: str
    label: str | None = None
    aliases: tuple[str, ...] = ()
    instance_of: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    headquarters: tuple[str, ...] = ()
    website: str | None = None
    ror_id: str | None = None
    lei_id: str | None = None
    dissolved: str | None = None
    replaced_by: str | None = None

    @property
    def is_disambiguation(self) -> bool:
        return Q_DISAMBIGUATION in self.instance_of

    @property
    def names(self) -> tuple[str, ...]:
        """Label first, then aliases — the strings the name check scores."""
        return tuple(n for n in (self.label, *self.aliases) if n and n.strip())

    @property
    def superseded(self) -> bool:
        return bool(self.dissolved or self.replaced_by)

    @property
    def referenced_qids(self) -> tuple[str, ...]:
        """Items whose own claims/labels the gauntlet needs (steps 4 and 6)."""
        refs = list(self.headquarters)
        if self.replaced_by:
            refs.append(self.replaced_by)
        return tuple(dict.fromkeys(refs))


@dataclass
class Candidate:
    """One search hit, and what the gauntlet made of it."""

    qid: str
    item: WikidataItem | None = None
    name_score: float = 0.0
    rejected_by: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "label": self.item.label if self.item else None,
            "name_score": round(self.name_score, 1),
            "rejected_by": self.rejected_by,
            "detail": self.detail,
        }


@dataclass
class WikidataOutcome:
    """One record's lane result, and the evidence behind it."""

    outcome: str
    query: str
    item: WikidataItem | None = None
    name_score: float | None = None
    #: Label of the ``P1366`` successor, when the matched item names one.
    successor_label: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    #: Rejection reasons seen across the candidate set, for the counters.
    reasons: set[str] = field(default_factory=set)
    error: str | None = None
    #: Logical API operations this record cost — the budget number.
    calls: int = 0
    #: HTTP requests issued, retries included — the rate-limiter's number.
    http_requests: int = 0
    from_fixture: bool = True

    @property
    def matched(self) -> bool:
        return self.outcome == MATCHED

    @property
    def qid(self) -> str | None:
        return self.item.qid if self.item else None

    def supersession_detail(self) -> str | None:
        """The reason clause for the ``entity-superseded`` flag, or ``None``.

        Names the successor when ``P1366`` is present — that is the thing a
        reviewer needs in order to make the business decision this lane
        deliberately does not make — and falls back to the dissolution date
        when only ``P576`` is.
        """
        if self.item is None or not self.item.superseded:
            return None
        if self.item.replaced_by:
            name = self.successor_label or "an unnamed successor"
            return f"replaced by {name} ({self.item.replaced_by})"
        return f"dissolved {self.item.dissolved}"

    def as_dict(self) -> dict[str, Any]:
        item = self.item
        return {
            "outcome": self.outcome,
            "query": self.query,
            "qid": self.qid,
            "label": item.label if item else None,
            "name_score": self.name_score,
            "ror_id": item.ror_id if item else None,
            "lei_id": item.lei_id if item else None,
            "website": item.website if item else None,
            "superseded": bool(item and item.superseded),
            "supersession": self.supersession_detail(),
            "reasons": sorted(self.reasons),
            "candidates": [c.as_dict() for c in self.candidates],
            "api_calls": self.calls,
            "http_requests": self.http_requests,
            "from_fixture": self.from_fixture,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _claim_ids(entity: dict[str, Any], prop: str) -> tuple[str, ...]:
    """Every ``wikibase-entityid`` value of *prop*, in statement order."""
    out: list[str] = []
    for claim in entity.get("claims", {}).get(prop, []) or ():
        snak = claim.get("mainsnak") or {}
        value = (snak.get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            out.append(str(value["id"]))
    return tuple(dict.fromkeys(out))


def _claim_strings(entity: dict[str, Any], prop: str) -> tuple[str, ...]:
    out: list[str] = []
    for claim in entity.get("claims", {}).get(prop, []) or ():
        snak = claim.get("mainsnak") or {}
        value = (snak.get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return tuple(dict.fromkeys(out))


def _claim_date(entity: dict[str, Any], prop: str) -> str | None:
    """The first time-valued claim of *prop*, rendered at its own precision.

    Wikidata stores ``+2019-06-30T00:00:00Z`` with a precision code; printing
    the full string would assert a day the source does not claim. Precision 9
    is a year, 10 a month, 11 a day.
    """
    for claim in entity.get("claims", {}).get(prop, []) or ():
        snak = claim.get("mainsnak") or {}
        value = (snak.get("datavalue") or {}).get("value")
        if not isinstance(value, dict):
            continue
        raw = str(value.get("time") or "").lstrip("+")
        if not raw:
            continue
        stamp = raw.split("T")[0]
        precision = value.get("precision")
        if precision == 9:
            return stamp[:4]
        if precision == 10:
            return stamp[:7]
        return stamp
    return None


#: Every property this module reads. Nothing else is parsed, and — see
#: :func:`prune_entity` — nothing else is recorded.
READ_PROPERTIES: tuple[str, ...] = (
    P_INSTANCE_OF, P_SUBCLASS_OF, P_COUNTRY, P_HEADQUARTERS,
    P_WEBSITE, P_ROR, P_LEI, P_DISSOLVED, P_REPLACED_BY,
)


def prune_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """The slice of a ``wbgetentities`` entity this lane actually consumes.

    Wikidata entities are large — a single well-cited item carries every
    statement, qualifier, reference and hash it has ever accumulated, and the
    100-row chemspeed batch recorded **4.6 MB** of them against 589 KB for the
    entire page-read fixture store. Almost none of it is read here.

    So the fixture records the labels, the English aliases and the nine
    properties in :data:`READ_PROPERTIES`, and nothing else. That is the same
    choice the page fixtures already make in storing extracted text rather than
    raw HTML: a fixture is a record of *what the pipeline consumed*, which is
    the thing a re-run has to reproduce, and it is worth being able to read one.

    The cost is stated rather than hidden: **adding a property to this lane
    means the existing fixtures do not carry it**, and the recordings have to be
    refreshed (`scripts/wikidata_warm_fixtures.py`) before a replay run means
    anything. Parsing is unaffected either way — :func:`parse_entity` ignores
    extras, so an unpruned recording still reads correctly.
    """
    claims = entity.get("claims") or {}
    return {
        "id": entity.get("id"),
        "labels": {"en": (entity.get("labels") or {}).get("en")}
        if (entity.get("labels") or {}).get("en") else {},
        "aliases": {"en": (entity.get("aliases") or {}).get("en", [])}
        if (entity.get("aliases") or {}).get("en") else {},
        "claims": {
            prop: [
                {"mainsnak": {
                    "snaktype": (c.get("mainsnak") or {}).get("snaktype"),
                    "property": prop,
                    "datavalue": (c.get("mainsnak") or {}).get("datavalue"),
                }}
                for c in claims.get(prop, []) or ()
            ]
            for prop in READ_PROPERTIES
            if claims.get(prop)
        },
    }


def parse_entity(qid: str, entity: dict[str, Any]) -> WikidataItem:
    """Read the claims this lane cares about off one ``wbgetentities`` entity."""
    labels = entity.get("labels") or {}
    label = ((labels.get("en") or {}).get("value") or "").strip() or None
    aliases = tuple(
        (a.get("value") or "").strip()
        for a in (entity.get("aliases") or {}).get("en", []) or ()
        if (a.get("value") or "").strip()
    )
    ror = _claim_strings(entity, P_ROR)
    lei = _claim_strings(entity, P_LEI)
    site = _claim_strings(entity, P_WEBSITE)
    successors = _claim_ids(entity, P_REPLACED_BY)
    return WikidataItem(
        qid=qid,
        label=label,
        aliases=aliases,
        instance_of=_claim_ids(entity, P_INSTANCE_OF),
        countries=_claim_ids(entity, P_COUNTRY),
        headquarters=_claim_ids(entity, P_HEADQUARTERS),
        website=site[0] if site else None,
        ror_id=ror[0] if ror else None,
        lei_id=lei[0] if lei else None,
        dissolved=_claim_date(entity, P_DISSOLVED),
        replaced_by=successors[0] if successors else None,
    )


# ---------------------------------------------------------------------------
# The gauntlet's individual steps
# ---------------------------------------------------------------------------

def type_allowed(item: WikidataItem) -> bool:
    """Step 3 — ``P31``, or one declared ``P279`` step up from it, is whitelisted."""
    for cls in item.instance_of:
        if cls in TYPE_WHITELIST:
            return True
        if any(parent in TYPE_WHITELIST for parent in P279_ONE_STEP.get(cls, ())):
            return True
    return False


def country_verdict(
    item: WikidataItem, hq_countries: dict[str, tuple[str, ...]],
) -> bool:
    """Step 4 — the item is in the United States.

    ``P17`` first; failing that, the country of a ``P159`` headquarters.
    *hq_countries* maps a headquarters QID to the ``P17`` values of that item,
    which is what the reference call resolved.

    **Missing both is a rejection, and that is a deliberately conservative
    choice.** The batch this lane serves is a US customer master, so an item
    with no country statement is not thereby "probably American" — it is an
    item nobody has finished curating, and admitting it would mean matching a
    US record against a same-named organisation anywhere on earth. The same
    reasoning ROR's country guard already applies: a wrong-country identity is
    worse than none, because it wrongly converges distinct entities in Phase 2.
    """
    if Q_UNITED_STATES in item.countries:
        return True
    for hq in item.headquarters:
        if Q_UNITED_STATES in hq_countries.get(hq, ()):
            return True
    return False


def _norm_place(value: str | None) -> str:
    """Case/whitespace fold for a place name, matching the corroborator's."""
    return " ".join((value or "").split()).lower()


def city_verdict(
    item: WikidataItem,
    hq_labels: dict[str, str],
    *,
    city: str | None,
    region: str | None,
) -> tuple[bool, str | None]:
    """Step 6 — ``(consistent, detail)``. A missing ``P159`` is neutral.

    Deliberately city-level, which is stricter than the page corroborator's
    region-level withdrawal rule. The two are answering different questions: the
    corroborator is deciding whether to *destroy* a domain it already published,
    where a plant and a head office in one state must not count as a
    disagreement, whereas this is deciding whether to *admit* a crowd-sourced
    identity in the first place. Refusing to admit one is cheap; the record
    simply proceeds to the web lane exactly as it would have.

    The record's region rescues a headquarters stated at state granularity
    ("California" against a record in ``CA``), through the same
    ``US_REGION_CODES`` normalisation the corroborator reuses — now imported
    from ``enrichment.locality``, the leaf module that holds it, rather than
    lazily out of ``page_corroborator`` to dodge an import cycle.
    """
    if not item.headquarters:
        return True, None
    want_city, want_region = _norm_place(city), normalise_region(region)
    if not want_city and not want_region:
        return True, None

    stated = [hq_labels[q] for q in item.headquarters if hq_labels.get(q)]
    if not stated:
        # The reference call could not name the place. Silence is not evidence.
        return True, None

    for place in stated:
        norm_place = _norm_place(place)
        if want_city and (
            norm_place == want_city
            or norm_place in want_city
            or want_city in norm_place
        ):
            return True, None
        if want_region and normalise_region(place) == want_region:
            return True, None
    return False, (
        f"headquarters {stated[0]!r} contradicts record city "
        f"{city or '?'} / region {region or '?'}"
    )


def best_name_score(item: WikidataItem, name: str) -> float:
    """Step 5 — best label/alias against *name*, on the existing scorer.

    :func:`enrichment.tier1_lei._name_match_score` verbatim: ``token_sort_ratio``
    taken as the max of the raw score and the legal-form-stripped score. No new
    similarity machinery and no new normalisation function — this is the same
    supplied-name-vs-official-name comparison GLEIF's guard and the page reader
    both make, so it is the same code and the same threshold.
    """
    return max((_name_match_score(name, n) for n in item.names), default=0.0)


def website_agrees(website: str | None, domain: str | None) -> bool | None:
    """``True`` / ``False`` / ``None`` (nothing to compare).

    Registrable-domain equality through
    :func:`utils.domain_resolver.canonicalise_domain` — the same registrable-stem
    reduction the ownership guard uses, so ``https://www.acme.com/en/`` and
    ``acme.com`` are one domain here exactly as they are there, and no second
    stem-extraction rule is written.

    ``False`` is deliberately NOT grounds to withdraw an accepted domain. A
    ``P856`` statement can be years old and companies change domains; the page
    corroborator already established that a single disagreement is not enough
    to destroy a published value, and a wiki field is weaker evidence than the
    site itself. Disagreement is counted (``wikidata_domain_disagree``) and
    nothing else happens.
    """
    if not website or not domain:
        return None
    stated = canonicalise_domain(website)
    have = canonicalise_domain(domain)
    if not stated or not have:
        return None
    return stated == have


#: Provenance token for a value the witness path wrote.
#:
#: Provenance Scheme B, ``source:confidence`` — see :mod:`enrichment.confidence`.
#: The source is ``wikidata`` because a Wikidata item is what stated this
#: label, and the confidence is ``provisional`` because that item is the
#: single uncontradicted source for it. It can never be ``verified``: this is
#: the *witness* path, taken precisely when the crosswalk found no registry
#: pointer to follow, so there is no second evidence system agreeing and no
#: registry authoring the value. A crowd-edited label is a pointer, never an
#: authority — which the old ``wikidata:2:crosswalk`` said only by implication,
#: through a ``2`` that asserted a tier the lane never ran and a ``crosswalk``
#: band that was a placeholder occupying the method slot.
#:
#: This string was NOT in the migration's state table; the mapping applied is
#: the one this module already recorded as its target, and it is listed in
#: ``provenance_migration_report.md`` for confirmation.
WITNESS_PROVENANCE = render_provenance(SOURCE_WIKIDATA, PROVISIONAL)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

class WikidataUnavailable(RuntimeError):
    """A call failed in a way that must be scored as "we could not look"."""


#: Base backoff for a rate-limited retry, seconds. Deliberately an order of
#: magnitude longer than the 0.5s the GLEIF client uses, and the difference is
#: measured rather than guessed: the first live 100-row run at concurrency 3
#: took `HTTPStatusError:429` on 28 of 68 invocations under GLEIF's schedule
#: (0.5s, 1.0s), because Wikidata rate-limits anonymous callers far harder than
#: GLEIF does. A 429 is the API stating a rate, not a failure to recover from,
#: so retrying it in half a second is not a retry — it is the same request.
_RATE_LIMIT_BACKOFF_SECONDS = 5.0

#: Cap on a server-supplied ``Retry-After``. Honouring an arbitrarily long one
#: would let a single header stall a whole batch; past this the lane gives up
#: and reports `wikidata_unavailable`, which costs the record nothing.
_MAX_RETRY_AFTER_SECONDS = 30.0


def _backoff(attempt: int, status: int | None, response: Any) -> float:
    """Seconds to wait before retry *attempt*.

    ``Retry-After`` wins when the server sends one — it is the API stating how
    long it wants to be left alone, and guessing over the top of that is how a
    client earns a longer ban. Otherwise: the standard exponential schedule for
    a transient error, and the much slower one above for a 429.
    """
    header = None
    try:
        header = (getattr(response, "headers", None) or {}).get("Retry-After")
    except Exception:  # noqa: BLE001 — a malformed header is just absent
        header = None
    if header:
        try:
            return min(float(str(header).strip()), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass  # an HTTP-date form; fall through to the schedule below
    base = _RATE_LIMIT_BACKOFF_SECONDS if status == 429 else 0.5
    return base * (2 ** (attempt - 1))


class WikidataClient:
    """``wbsearchentities`` + ``wbgetentities``, fixture-cached, fail-closed.

    Mirrors :class:`enrichment.tier1_lei.LEIClient`'s shape so it can be
    injected and mocked the same way. The two public methods are the only
    network surface this lane has; both raise :class:`WikidataUnavailable` on
    anything that is not a well-formed 200, and the caller turns that into a
    counted no-match.
    """

    #: Wikidata asks every client to identify itself; an anonymous bulk caller
    #: is the one most likely to be rate-limited.
    USER_AGENT = "BrukerMDM-EnrichmentAPI/1.0 (Wikidata crosswalk lane)"

    def __init__(self, settings: Any, cache: PageCache | None = None) -> None:
        self._base_url = settings.wikidata_api_base
        self._timeout = settings.wikidata_timeout_seconds
        self._max_retries = settings.wikidata_max_retries
        self._search_limit = settings.wikidata_search_limit
        self._cache = cache if cache is not None else PageCache()
        #: HTTP requests actually issued, **retries included** — the load this
        #: client puts on the API. A fixture hit costs none.
        self.calls = 0
        #: Logical API operations — one per search or entity fetch that was not
        #: served from a fixture, whatever it took to complete. This is the
        #: number the two-call-per-record budget is stated in; `calls` is the
        #: number a rate limiter sees, and conflating them made the first live
        #: run look as though it were spending five calls on one record when it
        #: was spending two operations and three retries.
        self.operations = 0

    # -- transport ----------------------------------------------------------

    async def _get(self, params: dict[str, str]) -> dict[str, Any]:
        """One GET, retrying transient failures, raising on anything else."""
        attempt = 0
        self.operations += 1
        # verify=resolve_tls_verify() for the same reason ROR and GLEIF use it:
        # a TLS-inspecting corporate VPN presents its own root CA, and pinning
        # certifi makes every call fail the handshake.
        async with httpx.AsyncClient(
            timeout=self._timeout,
            verify=resolve_tls_verify(),
            headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"},
        ) as client:
            while True:
                try:
                    self.calls += 1
                    note_network_call("wikidata")
                    resp = await client.get(self._base_url, params=params)
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    # 429 is Wikidata asking us to slow down, and is transient.
                    transient = status is None or status >= 500 or status == 429
                    attempt += 1
                    if not transient or attempt > self._max_retries:
                        raise WikidataUnavailable(
                            f"{type(exc).__name__}:{status}",
                        ) from exc
                    await asyncio.sleep(_backoff(attempt, status, response))
                except (ValueError, json.JSONDecodeError) as exc:
                    # Malformed body. Not retried: a wiki that answered 200 with
                    # something that is not JSON will do so again.
                    raise WikidataUnavailable("malformed_json") from exc

    async def _cached(self, key: str, params: dict[str, str]) -> dict[str, Any]:
        """Fixture-cached GET. *key* is the cache key AND the fixture filename."""
        cached = self._cache.get(key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get("__unavailable__"):
                raise WikidataUnavailable(str(cached.get("error") or "recorded"))
            return cached
        if self._cache.replay_only:
            # An offline re-analysis must never silently reach the network. A
            # missing fixture is reported as "we could not look".
            raise WikidataUnavailable("replay_only_miss")
        payload = await self._get(params)
        if not isinstance(payload, dict):
            raise WikidataUnavailable("malformed_json")
        self._cache.set(key, payload)
        return payload

    # -- the two calls ------------------------------------------------------

    async def search(self, name: str) -> list[str]:
        """Call 1 — candidate QIDs for *name*, best first.

        ``wbsearchentities`` searches labels **and** aliases, which is why the
        lane needs no alias expansion of its own. The cache key is the
        normalised query, through the same :func:`dedup.signatures.normalize_key`
        every other cache namespace in this codebase uses; the **unnormalised**
        name is what is sent, exactly as in ``utils.cache``'s contract.
        """
        query = (name or "").strip()
        if not query:
            return []
        payload = await self._cached(
            f"search:{normalize_key(query)}",
            {
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": str(self._search_limit),
                "format": "json",
            },
        )
        hits = payload.get("search")
        if not isinstance(hits, list):
            raise WikidataUnavailable("malformed_search")
        return [
            str(h["id"]) for h in hits
            if isinstance(h, dict) and h.get("id")
        ][: self._search_limit]

    async def entities(self, qids: list[str]) -> dict[str, dict[str, Any]]:
        """Call 2 (and the conditional reference call) — claims for *qids*.

        Batched into ONE request. Fetching all five search hits together is
        what makes the collision check affordable inside the two-call budget:
        the gauntlet has to be run over every candidate to know whether more
        than one survives, and one request does that.

        Each QID is fixture-cached separately, so a batch that names the same
        organisation (or the same headquarters city) twice fetches it once and
        a partially-recorded set only fetches what is missing.
        """
        wanted = [q for q in dict.fromkeys(qids) if q]
        if not wanted:
            return {}

        out: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for qid in wanted:
            cached = self._cache.get(f"entity:{qid}")
            if cached is None:
                missing.append(qid)
            elif isinstance(cached, dict) and cached.get("__unavailable__"):
                raise WikidataUnavailable(str(cached.get("error") or "recorded"))
            else:
                out[qid] = cached

        if missing:
            if self._cache.replay_only:
                raise WikidataUnavailable("replay_only_miss")
            payload = await self._get({
                "action": "wbgetentities",
                # The API caps `ids` at 50; the lane never asks for more than
                # the search limit plus a handful of referenced items.
                "ids": "|".join(missing[:50]),
                "props": "labels|aliases|claims",
                "languages": "en",
                "format": "json",
            })
            entities = payload.get("entities")
            if not isinstance(entities, dict):
                raise WikidataUnavailable("malformed_entities")
            for qid in missing:
                entity = entities.get(qid)
                if not isinstance(entity, dict) or entity.get("missing") is not None:
                    continue
                # Recorded pruned — see `prune_entity`. The in-memory value is
                # the pruned one too, so a fixture hit and a live fetch feed
                # the gauntlet identical input and cannot diverge.
                entity = prune_entity(entity)
                self._cache.set(f"entity:{qid}", entity)
                out[qid] = entity
        return out


# ---------------------------------------------------------------------------
# The whole step
# ---------------------------------------------------------------------------

async def resolve(
    *,
    record_id: str,
    name: str,
    city: str | None,
    region: str | None,
    client: WikidataClient,
    threshold: float,
    trace: bool = False,
) -> WikidataOutcome:
    """Run the full gauntlet for one record. Never raises.

    Returns :data:`MATCHED` with exactly one item, or one of the three
    no-match outcomes. Nothing here writes to a record: the orchestrator reads
    the outcome and decides what, if anything, it licenses.
    """
    query = (name or "").strip()
    started_ops = getattr(client, "operations", 0)
    started_http = getattr(client, "calls", 0)
    outcome = WikidataOutcome(outcome=NO_MATCH, query=query)

    def _finish(result: WikidataOutcome) -> WikidataOutcome:
        result.calls = getattr(client, "operations", 0) - started_ops
        result.http_requests = getattr(client, "calls", 0) - started_http
        result.from_fixture = result.http_requests == 0
        line = {"record_id": record_id, "step": "wikidata", **result.as_dict()}
        logger.info(line)
        if trace:
            trace_logger.info(json.dumps(line, default=str))
        return result

    if not query:
        return _finish(outcome)

    try:
        qids = await client.search(query)
    except WikidataUnavailable as exc:
        outcome.outcome, outcome.error = UNAVAILABLE, str(exc)
        return _finish(outcome)

    if not qids:
        return _finish(outcome)

    try:
        raw = await client.entities(qids)
    except WikidataUnavailable as exc:
        outcome.outcome, outcome.error = UNAVAILABLE, str(exc)
        return _finish(outcome)

    candidates = [Candidate(qid=q) for q in qids]
    outcome.candidates = candidates

    # ── Steps 2, 3 and 5: everything that needs no referenced item ─────────
    for cand in candidates:
        entity = raw.get(cand.qid)
        if entity is None:
            cand.rejected_by, cand.detail = NO_MATCH, "entity not returned"
            continue
        item = parse_entity(cand.qid, entity)
        cand.item = item

        if item.is_disambiguation:
            # Step 2. A disambiguation page is a no-match for the WHOLE record,
            # not merely for this candidate: the wiki is saying the name
            # identifies several organisations, and picking one from the list
            # is precisely the judgement this lane refuses to make.
            cand.rejected_by = DISAMBIGUATION_REJECTED
            outcome.reasons.add(DISAMBIGUATION_REJECTED)
            outcome.outcome = NO_MATCH
            return _finish(outcome)

        if not type_allowed(item):
            cand.rejected_by = TYPE_REJECTED
            cand.detail = f"P31={list(item.instance_of)}"
            outcome.reasons.add(TYPE_REJECTED)
            continue

        cand.name_score = best_name_score(item, query)
        if cand.name_score < threshold:
            cand.rejected_by = NAME_REJECTED
            cand.detail = f"{cand.name_score:.1f} < {threshold:.1f}"
            outcome.reasons.add(NAME_REJECTED)
            continue

    survivors = [c for c in candidates if c.item and not c.rejected_by]
    if not survivors:
        return _finish(outcome)

    # ── The conditional reference call ─────────────────────────────────────
    # Only the survivors' headquarters and successors, batched into one
    # request, and only when at least one survivor carries such a property.
    refs: list[str] = []
    for cand in survivors:
        assert cand.item is not None
        refs.extend(cand.item.referenced_qids)
    hq_countries: dict[str, tuple[str, ...]] = {}
    hq_labels: dict[str, str] = {}
    if refs:
        try:
            ref_raw = await client.entities(refs)
        except WikidataUnavailable as exc:
            outcome.outcome, outcome.error = UNAVAILABLE, str(exc)
            return _finish(outcome)
        for qid, entity in ref_raw.items():
            ref_item = parse_entity(qid, entity)
            hq_countries[qid] = ref_item.countries
            if ref_item.label:
                hq_labels[qid] = ref_item.label

    # ── Steps 4 and 6 ──────────────────────────────────────────────────────
    for cand in survivors:
        item = cand.item
        assert item is not None
        if not country_verdict(item, hq_countries):
            cand.rejected_by = COUNTRY_REJECTED
            cand.detail = (
                f"P17={list(item.countries)} P159={list(item.headquarters)}"
            )
            outcome.reasons.add(COUNTRY_REJECTED)
            continue
        consistent, detail = city_verdict(
            item, hq_labels, city=city, region=region,
        )
        if not consistent:
            cand.rejected_by = CITY_REJECTED
            cand.detail = detail
            outcome.reasons.add(CITY_REJECTED)

    final = [c for c in survivors if not c.rejected_by]
    if not final:
        return _finish(outcome)
    if len(final) > 1:
        # A collision. Two curated items both pass every constraint, so the
        # constraints have not identified one organisation — and choosing
        # between them on the higher fuzzy score would be exactly the tiebreak
        # this lane does not do.
        outcome.outcome = AMBIGUOUS
        return _finish(outcome)

    winner = final[0]
    outcome.outcome = MATCHED
    outcome.item = winner.item
    outcome.name_score = winner.name_score
    assert winner.item is not None
    if winner.item.replaced_by:
        outcome.successor_label = hq_labels.get(winner.item.replaced_by)
    return _finish(outcome)
