"""The universal grounded lane — SERP + one LLM read + registry re-verification.

Every record the registries and the short-circuit lanes could not settle used
to end at Tier 3, which asks a model what it *remembers* about the name in the
record. That is the one kind of answer this pipeline cannot audit: there is no
page to open, no identifier to check, and a confident recollection is
indistinguishable from a correct one.

This lane replaces the question. It looks the record up, fetches what the
search returned, hands the model the record's own fields *and that evidence*,
and requires the answer to point at the evidence item it came from
(``evidence_index``). Then it does the thing that makes the answer worth
having: it takes the model's canonical names back to ROR / GLEIF and asks the
registry. A model that has correctly read "Ames Research Center" off a NASA
page is a model that has just handed the pipeline a string ROR resolves — and
the record leaves with a registry identifier rather than with an inference.

Three outcomes, in descending order of what a reviewer can do with them:

* **registry hit** — the registry authored the final name and supplied the id.
  Terminal, unflagged, and provenance says ``ror``/``gleif``. The model's only
  contribution was the query; that is recorded as an EARLIER event on the same
  field rather than as a ``+llm`` witness, because a witness-carrying
  ``verified`` is exactly what ``enrichment.confidence`` hard rule 1 forbids a
  model to produce. The log shows both writes; the column shows the registry.
* **no registry hit, evidence-backed** — the value is adopted, sourced to the
  page the model pointed at, and flagged. A reviewer has a URL.
* **no registry hit, no evidence** — the value is adopted, sourced to the
  model, flagged, and the record stays ``unresolved``. This is Tier 3's old
  outcome, reached only when nothing else was available, and now labelled as
  such rather than being the default.

Nothing here replaces an existing lane. Tier 1, the lab resolver, Tier 2/2A
and the canonical short-circuit all still run first and still short-circuit on
success; this lane sees only what they left. When the search returns nothing —
or every fetch fails — it degrades to ``run_tier3`` unchanged, so a record can
never be worse off for the lane having been tried.

The deterministic guards are the pipeline's existing ones, imported rather
than restated: :func:`canonical_preserves_identity` for Name 1,
:func:`department_preserves_identity` for Name 2 (the same comparator over
abbreviation-expanded forms, because a department string is abbreviated far
more often than an organisation name), Tier 3's address-like-name guard for
every name, and the locality comparator for country. A guard drops the field it refused and nothing else — the other
field's proposal, and the record's own original value, are untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from typing import Any, Sequence

from config import Settings
# The same equality Fix 2's `unchanged-confirmed` uses to decide that a
# model's "correction" is punctuation and not a new name. Imported, not
# reimplemented — the two decisions have to agree or a proposal is a
# confirmation to one of them and a rewrite to the other.
from dedup.signatures import normalize_key
from enrichment.locality import normalise_country
from enrichment.search_terms import clean_name2_phrase
# The Tier 3 address-like-name guard. Imported, never reimplemented: a NAME
# slot holding street content is one rule, and two copies of it would drift.
from enrichment.tier3_llm import _is_address_like_name
from llm.openai_client import OpenAIClient
from llm.prompts import (
    GROUNDED_RESOLVER_SYSTEM_PROMPT,
    GROUNDED_RESOLVER_USER_PROMPT_TEMPLATE,
)
from search.base import SearchClient, SearchResult
from search.page_fetcher import PageContent, PageFetcher
from utils.cache import BatchCache, cached_serp
from utils.text_utils import (
    canonical_preserves_identity,
    department_preserves_identity,
    looks_like_research_institution,
)

logger = logging.getLogger(__name__)

#: How many SERP results are fetched. Three is the lab resolver's budget and
#: the same reasoning applies: past the third organic result the pages stop
#: being about the organisation and start being about the words in its name.
MAX_FETCHES = 3

#: How many organic results are requested. Wider than `MAX_FETCHES` because a
#: snippet is evidence too — an unfetchable result still tells the model what
#: the search index says the page is about.
NUM_RESULTS = 5

#: The `name2_kind` values that name something to write into a department slot.
WRITEABLE_KINDS: frozenset[str] = frozenset({"department", "sub_entity"})

#: The values that mean Name 2 holds nothing worth keeping. Both clear the
#: slot: an alias of Name 1 states nothing new, and noise never stated
#: anything. `person` is deliberately absent — a person's name in a department
#: slot is preprocessing's problem, and this lane leaves it exactly as it
#: found it rather than guessing which slot it belongs in.
CLEARING_KINDS: frozenset[str] = frozenset({"alias_of_name1", "noise"})

#: The `origin` vocabulary this lane reports, in descending authority. These
#: are the lane's own words for what backed a value; the orchestrator maps
#: them onto the result's closed `source` vocabulary
#: (`ROR` / `gleif` / `SERP+LLM` / `LLM`).
ORIGIN_ROR = "ror"
ORIGIN_LEI = "lei"
ORIGIN_SERP = "serp"
ORIGIN_LLM = "llm"


@dataclass
class EvidenceItem:
    """One numbered thing the model is allowed to read.

    A SERP result and the page behind it are ONE item, not two. The model is
    asked which item supports a claim so the caller can attach a URL to it,
    and a title that came from the search index and an H1 that came from the
    page it points at name the same URL — splitting them would make
    ``evidence_index`` ambiguous about exactly the thing it exists to resolve.
    """

    index: int
    url: str
    serp_title: str = ""
    serp_snippet: str = ""
    page: PageContent | None = None

    @property
    def fetched(self) -> bool:
        return self.page is not None and not self.page.is_empty()

    def render(self) -> str:
        lines = [f"[{self.index}] {self.url}"]
        if self.serp_title:
            lines.append(f"    search result title: {self.serp_title}")
        if self.serp_snippet:
            lines.append(f"    search result snippet: {self.serp_snippet}")
        if self.page is not None:
            # URL path / title / H1 / breadcrumb only. The body text is
            # deliberately not offered: it is where marketing copy, unrelated
            # news items and other organisations' names live, and a model
            # given it starts sourcing claims to prose rather than to the
            # page's own structural statement of what it is.
            for label, value in (
                ("page url path", self.page.url_path),
                ("page title tag", self.page.page_title),
                ("page h1", self.page.h1),
                ("page breadcrumb", self.page.breadcrumb),
            ):
                if value and str(value).strip():
                    lines.append(f"    {label}: {str(value).strip()}")
        return "\n".join(lines)


@dataclass
class GroundedProposal:
    """One field's outcome: what to write, and what backs it."""

    field: str
    #: What to WRITE. On a registry hit this is the registry's official name;
    #: on every other path it is `proposed`.
    value: str
    #: What the MODEL said, always. Kept apart from `value` because the
    #: registry path writes twice — the proposal, then the official name over
    #: it — and an audit trail that recorded the registry's name as the
    #: model's claim would show a model that never made one.
    proposed: str
    #: `ror` | `lei` | `serp` | `llm` — see the ORIGIN_* constants.
    origin: str
    #: The model's own confidence for this field, verbatim. Kept even on a
    #: registry hit, where it is no longer the confidence of the VALUE (the
    #: registry authored that) but is still the confidence of the query that
    #: found it — which is what the provenance event records.
    self_reported: str
    registry: str | None = None
    registry_id: str | None = None
    #: Every name the registry publishes for the matched entity, so the
    #: caller's `_write_registry_name` can keep the record's own spelling
    #: when the registry publishes it as a variant.
    variants: Sequence[str] | None = None
    #: The registry's stated website, for `_apply_domain`.
    website: str | None = None
    #: The raw registry response, for `record_registry_identity`.
    registry_response: dict[str, Any] | None = None
    evidence_index: int | None = None
    source_url: str | None = None

    @property
    def from_registry(self) -> bool:
        return self.origin in (ORIGIN_ROR, ORIGIN_LEI)


@dataclass
class GroundedResult:
    """What the lane concluded about one record."""

    #: The lane executed an LLM call. False when it degraded before that.
    ran: bool = False
    #: The caller must fall back to `run_tier3`. Set when the search returned
    #: nothing, when every fetch failed, or when the LLM call itself failed.
    degraded: bool = False
    #: Why it degraded — for the log, and for the tests to assert on.
    degraded_reason: str | None = None

    name1: GroundedProposal | None = None
    name2: GroundedProposal | None = None

    name2_kind: str | None = None
    #: `alias_of_name1` / `noise` — the caller writes Name 2 as None.
    clear_name2: bool = False

    #: Fields whose proposal a deterministic guard refused, with the reason.
    #: The record keeps its original value for these; nothing else changes.
    dropped: dict[str, str] = dc_field(default_factory=dict)

    #: field → the model's proposal, for fields where the proposal REPRODUCED
    #: the value the record already held and no registry improved on it.
    #:
    #: Not a write, and that is the entire point. Writing the same string back
    #: costs the record its `input:verified+web` attribution and buys nothing:
    #: the page corroboration that produced it is keyed on Name 1 having been
    #: KEPT, and a write — even an identical one — makes the model the author.
    #: Measured on the 100-row chemspeed batch: 14 rows whose Name 1 a page
    #: read had already corroborated shipped as `llm:provisional`, five of them
    #: newly flagged, for a value that had not changed by a character.
    #:
    #: What it IS, is Fix 2's `unchanged-confirmed` situation exactly — a model
    #: asked what the organisation is called, never shown the record's answer,
    #: returning that answer. The caller hands it to `_canonical_proposal`, the
    #: field that decision already reads.
    confirmed: dict[str, str] = dc_field(default_factory=dict)

    query: str = ""
    evidence: list[EvidenceItem] = dc_field(default_factory=list)
    reasoning: str = ""

    @property
    def wrote_anything(self) -> bool:
        return bool(self.name1 or self.name2 or self.clear_name2)

    @property
    def settled_anything(self) -> bool:
        """The lane established something — a write, a cleared slot, or a
        confirmation. A confirmation is not a write, but it is not a failure
        either, and the caller must not describe the record as a passthrough
        that found nothing when the model independently reproduced its name."""
        return self.wrote_anything or bool(self.confirmed)


def _clean(value: Any) -> str | None:
    """A model's string field, or None. ``"null"`` is None: JSON mode returns
    the string often enough that treating it as a name would be a bug waiting
    for its first record."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "na", "unknown"}:
        return None
    return text


def _band(value: Any) -> str:
    band = str(value or "").strip().lower()
    return band if band in {"high", "medium", "low"} else "low"


def _index(value: Any, ceiling: int) -> int | None:
    """A validated ``evidence_index``. Out of range is None, not an error: an
    index the model invented points at nothing, which is the same situation as
    it having pointed at nothing."""
    if value is None or isinstance(value, bool):
        return None
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < ceiling else None


def build_query(
    name1: str | None,
    name2: str | None,
    city: str | None,
    state: str | None,
) -> str:
    """The one SERP query, built from the record's own identifying material.

    Name 1 quoted (it is the subject), the department's *core* subject added
    unquoted via ``clean_name2_phrase`` — the same reduction the search-term
    derivation uses, so "Department of Chemistry" contributes "Chemistry"
    rather than three structural words the index will match everywhere — and
    the city/state as disambiguating context, which is what separates the
    University of Melbourne in Australia from the one in Florida.
    """
    parts: list[str] = []
    subject = (name1 or "").strip()
    if subject:
        parts.append(f'"{subject}"')
    unit = clean_name2_phrase(name2)
    if unit:
        parts.append(unit)
    for place in (city, state):
        if place and place.strip():
            parts.append(place.strip())
    return " ".join(parts)


async def _gather_evidence(
    record_id: str,
    query: str,
    search_client: SearchClient,
    page_fetcher: PageFetcher,
    cache: BatchCache,
    country_code: str | None,
) -> tuple[list[EvidenceItem], str | None]:
    """``(evidence, degraded_reason)``. One SERP call, up to three fetches."""
    results: list[SearchResult] = await cached_serp(
        cache, search_client, query,
        num_results=NUM_RESULTS, country=country_code,
    )
    if not results:
        return [], "serp_empty"

    items: list[EvidenceItem] = []
    for position, hit in enumerate(results[:MAX_FETCHES]):
        item = EvidenceItem(
            index=position,
            url=hit.url,
            serp_title=(hit.title or "").strip(),
            serp_snippet=(hit.snippet or "").strip(),
        )
        try:
            item.page = await page_fetcher.fetch_page_content(hit.url)
        except Exception as exc:  # noqa: BLE001 — a fetch must not fail a record
            logger.info(
                "[%s] Grounded resolver: fetch raised for %s: %s",
                record_id, hit.url[:80], exc,
            )
            item.page = None
        items.append(item)

    if not any(item.fetched for item in items):
        # The search found the words but nothing could be read. Snippets alone
        # are the search index's summary, not the organisation's own statement
        # of what it is called, and this lane exists to do better than that —
        # so it hands the record back rather than dressing a snippet up as
        # page evidence.
        return items, "all_fetches_failed"

    return items, None


async def _re_verify(
    record_id: str,
    field: str,
    name: str,
    *,
    routing_type: str,
    country_code: str | None,
    country: str | None,
    city: str | None,
    state: str | None,
    domain: str | None,
    ror_client: Any,
    lei_client: Any,
    exclude_ids: set[str],
) -> tuple[str | None, dict[str, Any] | None]:
    """Take one canonical name back to the registry it belongs to.

    ``(registry, response)`` on an accepted match, ``(None, None)`` otherwise.
    The registry clients apply their own country guards, acceptance thresholds
    and caches — this function chooses WHICH registry to ask and enforces the
    two conditions that are specific to re-verification: the country the
    registry states must agree with the record's, and a Name 2 match must be
    its own entity rather than Name 1's.

    A registry raising is a miss, never a failure: this is a bonus lookup on a
    record that had already run out of registry answers.
    """
    # Which registry, and in what order. A DECIDED routing type asks exactly
    # one — the branch it was routed down is the branch its evidence supports,
    # and asking the other would spend a call to contradict the classifier.
    #
    # An `unknown` type asks both, best guess first. That population is the
    # reason: it is disproportionately the records that reach this lane at
    # all, the classifier could not settle them, and the name the lane has
    # just read off a page is often the first string either registry could
    # have matched. "NASA" is the worked example — nothing about the acronym
    # or its expansion trips `looks_like_research_institution`, and the
    # expansion is a clean ROR hit.
    if routing_type == "research_institution":
        order = ("ROR",)
    elif routing_type == "company":
        order = ("GLEIF",)
    elif looks_like_research_institution(name):
        order = ("ROR", "GLEIF")
    else:
        order = ("GLEIF", "ROR")

    for registry in order:
        try:
            if registry == "ROR":
                res = await ror_client.call(
                    name,
                    country_code=country_code,
                    country=country,
                    city=city,
                    state=state,
                    record_domain=domain,
                )
            else:
                res = await lei_client.call(
                    name,
                    country_code=country_code,
                    city=city,
                    state=state,
                    record_domain=domain,
                )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[%s] Grounded resolver: %s raised for %s=%r (non-fatal): %s",
                record_id, registry, field, name, exc,
            )
            continue

        if not res.get("matched"):
            continue
        identifier = (
            res.get("ror_id") if registry == "ROR" else res.get("lei_id")
        )
        if identifier:
            break
    else:
        return None, None

    if not res.get("matched") or not identifier:
        return None, None

    # Country consistency. The registry clients guard on the code they were
    # given; this asks the same question of what the registry actually stated,
    # which is the half a blank `country_code` leaves unasked.
    stated = normalise_country(res.get("country"))
    ours = normalise_country(country)
    if stated and ours and stated != ours:
        logger.info({
            "record_id": record_id,
            "step": "grounded_registry_country_mismatch",
            "field": field,
            "registry": registry,
            "registry_country": res.get("country"),
            "record_country": country,
        })
        return None, None

    # Name 2 must be its OWN entity. A registry that answers Name 2's query
    # with Name 1's identifier has confirmed nothing about the unit — it has
    # matched the institution again, which the record already knows.
    if field != "name1" and str(identifier) in exclude_ids:
        logger.info({
            "record_id": record_id,
            "step": "grounded_registry_same_entity_as_name1",
            "field": field,
            "registry": registry,
            "identifier": identifier,
        })
        return None, None

    return registry, res


async def run_grounded_resolver(
    record_id: str,
    *,
    name1: str | None,
    name2: str | None,
    street: str | None,
    city: str | None,
    state: str | None,
    country: str | None,
    country_code: str | None,
    routing_type: str,
    domain: str | None,
    name1_registry_ids: Sequence[str] = (),
    search_client: SearchClient,
    page_fetcher: PageFetcher,
    llm_client: OpenAIClient,
    ror_client: Any,
    lei_client: Any,
    cache: BatchCache,
    settings: Settings,
) -> GroundedResult:
    """Resolve one record against the open web, then against the registries.

    ``name1_registry_ids`` are the identifiers Name 1 already holds (its
    ``ror_id`` / ``lei_id``, plus anything this run has just attached). A
    Name 2 match against one of them is refused — see :func:`_re_verify`.
    """
    result = GroundedResult()

    if not (name1 and name1.strip()) and not (name2 and name2.strip()):
        # Nothing to search for. Not a degradation — Tier 3 would have nothing
        # to infer from either — but the caller's fallback is still the right
        # place for a record with no names, so it is reported as one.
        result.degraded = True
        result.degraded_reason = "no_names"
        return result

    result.query = build_query(name1, name2, city, state)
    evidence, reason = await _gather_evidence(
        record_id, result.query, search_client, page_fetcher, cache,
        country_code,
    )
    result.evidence = evidence
    if reason:
        logger.info({
            "record_id": record_id,
            "step": "grounded_degraded",
            "reason": reason,
            "query": result.query,
        })
        result.degraded = True
        result.degraded_reason = reason
        return result

    user_prompt = GROUNDED_RESOLVER_USER_PROMPT_TEMPLATE.format(
        name1=name1 or "not recorded",
        name2=name2 or "not recorded",
        city=city or "not recorded",
        state=state or "not recorded",
        country=country or "not recorded",
        evidence="\n".join(item.render() for item in evidence),
    )
    try:
        extraction = await llm_client.extract_json(
            GROUNDED_RESOLVER_SYSTEM_PROMPT, user_prompt, temperature=0.0,
        )
    except Exception:
        logger.exception(
            "[%s] Grounded resolver: LLM call failed", record_id,
        )
        result.degraded = True
        result.degraded_reason = "llm_failed"
        return result

    result.ran = True
    result.reasoning = str(extraction.get("reasoning") or "")

    per_field = extraction.get("per_field_confidence")
    per_field = per_field if isinstance(per_field, dict) else {}
    indices = extraction.get("evidence_index")
    indices = indices if isinstance(indices, dict) else {}

    kind = _clean(extraction.get("name2_kind"))
    kind = kind.lower() if kind else None
    result.name2_kind = kind

    proposals: dict[str, str] = {}
    n1 = _clean(extraction.get("name1_canonical"))
    if n1:
        proposals["name1"] = n1

    if kind in CLEARING_KINDS:
        # Name 2 states nothing about a unit. Clearing it IS the outcome, and
        # no canonical name is written even if the model offered one.
        result.clear_name2 = True
    elif kind == "person":
        # Left exactly as found — see CLEARING_KINDS.
        pass
    else:
        n2 = _clean(extraction.get("name2_canonical"))
        # An unclassified Name 2 is treated as a department. The kind is the
        # model's classification of a value the record already holds, and
        # declining to classify is not a reason to throw away a canonical
        # form it did supply.
        if n2 and (kind in WRITEABLE_KINDS or kind is None):
            proposals["name2"] = n2

    # ── Deterministic guards, before anything is looked up or written ─────
    # Applied here rather than after re-verification on purpose: a proposal
    # the identity guard refuses must not be used as a REGISTRY QUERY either.
    # Sending "Liberty Science Center" to ROR on behalf of a record that said
    # "Liberty Health Sciences" is how a wrong entity acquires a real
    # identifier, which is the one outcome worse than not resolving.
    originals = {"name1": name1, "name2": name2}
    for field in list(proposals):
        value = proposals[field]
        if _is_address_like_name(value, street):
            result.dropped[field] = "address_like"
            del proposals[field]
            continue
        # Both name slots are identity-checked. The guard was `name1`-only,
        # so a Name 2 proposal reached the write path -- and the ROR
        # re-verification -- with nothing asking whether it still denoted the
        # same unit. Two values shipped that way: `Forensic Science Div` ->
        # `Forensic Services Laboratory` (the unit TYPE changed) and
        # `Baytown Refinery Laboratory` -> `Baytown Refinery` (the unit word
        # dropped, so the value named the site instead of the lab). The
        # comment above applies to Name 2 exactly as written: a proposal the
        # guard refuses must not become a registry query either.
        #
        # Name 2 uses the abbreviation-expanding variant. The raw comparator
        # reads `Div` -> `Division` as a distinctive-token mismatch and would
        # refuse the lane's registry-verified answers along with its wrong
        # ones.
        if field == "name1":
            refused = not canonical_preserves_identity(
                originals.get("name1"), value,
            )
        else:
            # Name 1 is passed as context: a department names a unit OF
            # something, so a proposal that spells out the parent organisation
            # has stated what the Name 2 slot left implicit, not changed which
            # unit it means. Without it the guard refuses `Weapons Div` ->
            # `Naval Air Warfare Center Weapons Division`, whose four "new"
            # words are Name 1 sitting in the same record — and which carries
            # a real ROR identifier.
            refused = not department_preserves_identity(
                originals.get(field), value, parent_name=name1,
            )
        if field in originals and refused:
            result.dropped[field] = "identity_not_preserved"
            del proposals[field]
    for field, why in result.dropped.items():
        logger.info({
            "record_id": record_id,
            "step": "grounded_guard_dropped",
            "field": field,
            "reason": why,
            "proposed": _clean(extraction.get(f"{field}_canonical")),
        })

    # ── Registry re-verification ──────────────────────────────────────────
    exclude_ids = {str(i) for i in name1_registry_ids if i}
    for field in ("name1", "name2"):
        value = proposals.get(field)
        if not value:
            continue
        idx = _index(indices.get(field), len(evidence))
        item = evidence[idx] if idx is not None else None
        self_reported = _band(per_field.get(field))

        registry, res = await _re_verify(
            record_id, field, value,
            routing_type=routing_type,
            country_code=country_code,
            country=country,
            city=city,
            state=state,
            domain=domain,
            ror_client=ror_client,
            lei_client=lei_client,
            exclude_ids=exclude_ids,
        )

        if registry and res:
            official = _clean(res.get("official_name") or res.get("legal_name"))
            identifier = str(
                res.get("ror_id") if registry == "ROR" else res.get("lei_id"),
            )
            # The registry's own name still passes the address guard. It never
            # fires on a real registry name; it costs nothing, and it is the
            # difference between "we trust the registry" and "we do not check
            # what we write into a NAME column".
            if official and not _is_address_like_name(official, street):
                proposal = GroundedProposal(
                    field=field,
                    value=official,
                    proposed=value,
                    origin=ORIGIN_ROR if registry == "ROR" else ORIGIN_LEI,
                    self_reported=self_reported,
                    registry=registry,
                    registry_id=identifier,
                    variants=res.get("name_variants"),
                    website=res.get("website"),
                    registry_response=res,
                    evidence_index=idx,
                    source_url=item.url if item else None,
                )
                setattr(result, field, proposal)
                if field == "name1":
                    # Name 2's own-entity check compares against whatever
                    # Name 1 ended up holding, including an id attached a
                    # moment ago by this same lane.
                    exclude_ids.add(identifier)
                logger.info({
                    "record_id": record_id,
                    "step": "grounded_registry_hit",
                    "field": field,
                    "registry": registry,
                    "queried": value,
                    "official_name": official,
                    "identifier": identifier,
                })
                continue

        # No registry hit, and the proposal reproduces what the record already
        # said. That is a confirmation, not a rewrite — see `confirmed`.
        incumbent = originals.get(field)
        if incumbent and normalize_key(incumbent) == normalize_key(value):
            result.confirmed[field] = value
            logger.info({
                "record_id": record_id,
                "step": "grounded_confirmed_input",
                "field": field,
                "value": value,
            })
            continue

        # No registry hit — adopt the model's canonical name, and say plainly
        # what it rests on. `evidence_index` is the whole distinction: a claim
        # that names a page is a claim a reviewer can check, and a claim that
        # names nothing is the model's recollection under a different name.
        setattr(result, field, GroundedProposal(
            field=field,
            value=value,
            proposed=value,
            origin=ORIGIN_SERP if item is not None else ORIGIN_LLM,
            self_reported=self_reported,
            evidence_index=idx,
            source_url=item.url if item else None,
        ))
        logger.info({
            "record_id": record_id,
            "step": "grounded_adopted",
            "field": field,
            "value": value,
            "origin": ORIGIN_SERP if item is not None else ORIGIN_LLM,
            "evidence_index": idx,
            "confidence": self_reported,
        })

    logger.info({
        "record_id": record_id,
        "step": "grounded_result",
        "query": result.query,
        "evidence": len(evidence),
        "name2_kind": kind,
        "name1": result.name1.value if result.name1 else None,
        "name1_origin": result.name1.origin if result.name1 else None,
        "name2": result.name2.value if result.name2 else None,
        "name2_origin": result.name2.origin if result.name2 else None,
        "clear_name2": result.clear_name2,
        "confirmed": sorted(result.confirmed),
        "dropped": result.dropped,
    })
    return result
