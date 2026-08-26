"""Is the organisation this record names still a going concern?

The pipeline's other lanes all answer **"which entity is this?"**. This one
answers **"does that entity still exist?"**, and the two questions are
independent: a dissolved company is often *easier* to identify than a live
one, because a well-known acquisition leaves a well-curated paper trail.

That independence is the reason this lane exists at all. Supersession
detection used to live inside the Wikidata crosswalk
(:mod:`enrichment.wikidata`), which runs **only** when ROR and GLEIF have both
missed — so the check was gated on the pipeline having failed to identify the
record. Celgene Corporation is the case that exposed it: GLEIF resolves the
name to ``4SIHMF0MOSTTL8CD0X64`` at a perfect 100.0, the registry hit
suppresses the crosswalk, and a company Bristol-Myers Squibb absorbed in 2019
leaves the pipeline enriched, high-confidence and unflagged. This lane
therefore runs from :meth:`Orchestrator._finalise_and_return` — after identity
is settled, on every record, whatever resolved it.

Nothing here rewrites a name. The lane's entire output is the
``entity-superseded`` evidence key that :func:`enrichment.flags.compute_flags`
already reads; which legal entity a customer record should point at after an
acquisition is a business decision, and the flag hands the reviewer what was
found and stops.

The three sources
----------------

Each was chosen by measuring its **flag rate** against the whole registry, not
by how plausible it sounds. A signal that fires on a third of the book is not a
signal, whatever it means in the spec.

===============================  ============  ==========  ===============
Signal                           Population    Flag rate   Used
===============================  ============  ==========  ===============
ROR ``status=inactive``          1,683         1.2%        **yes**
GLEIF ``entity.status=INACTIVE`` 249,972       7.3%        **yes**
GLEIF ``registration=RETIRED``   250,388       7.3%        **yes**
Cross-organisation redirect      --            low         **yes**
GLEIF ``registration=LAPSED``    1,193,113     **35.0%**   no
GLEIF ``registration=MERGED``    0             0%          (empty)
ROR ``status=withdrawn``         1,417         1.0%        no
===============================  ============  ==========  ===============

Counts taken from the live registries on 2026-08-26; ``page[size]=1`` against
``api.gleif.org/api/v1/lei-records`` and ``number_of_results`` from
``api.ror.org/v2/organizations``.

**LAPSED is excluded and that is the important exclusion.** It is tempting —
Celgene *is* LAPSED, and an acquirer abandoning a subsidiary's renewal is a
real pattern. But 35% of every LEI on file is LAPSED, and ``ACTIVE + LAPSED``
(Celgene's exact state) accounts for 1,193,112 of those 1,193,113 records. It
does not mean the entity died; it means nobody paid the renewal fee. Raising
``entity-superseded`` on it would flag a third of every record that resolves to
an LEI, which is indistinguishable from not having a flag.

**MERGED is accepted but empty.** It is the status the LEI-CDF spec defines for
exactly this case and GLEIF holds *zero* records in it. Kept in the set because
it costs nothing and is unambiguous if GLEIF ever populates it; it is not a
signal today.

**ANNULLED and ROR ``withdrawn`` are both excluded, for one reason.** Neither
says an organisation ceased to exist — they say the *registry record* was
retracted (issued in error, or deduplicated). ROR's withdrawn entries make this
plain: their ``successor`` relationships point at labels character-identical to
their own name, because they are merged duplicates, not acquisitions. Reporting
those as "no longer exists" would describe registry housekeeping as a corporate
event.

Cost
----

One ROR query per distinct (name, country) in the batch, memoised for the
batch's lifetime; the GLEIF read is free (the fields are already in the
response the record matched on); the redirect check reuses the resolution
:meth:`Orchestrator._resolve_probe_base` already performs, sharing its
``BatchCache`` entry, so a record that runs both pays for one.

Failure is closed, as it is in every other lane: a timeout, a non-200 or a
malformed body raises nothing and flags nothing. A liveness failure must never
fail a record, and — because this lane can only *add* a flag — a failure can
only cost a flag that would have been raised, never corrupt a value.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from enrichment.tier1_lei import _name_match_score, _normalise_legal_name
from enrichment.tier1_ror import _score_org, _strip_ror_country_suffix
from llm.openai_client import resolve_tls_verify
from utils.cache import RegistryUnavailableFrozen, cached_registry_get
from utils.domain_resolver import canonicalise_domain
from utils.text_utils import strip_parentheticals

logger = logging.getLogger(__name__)


# ── What each registry calls death ────────────────────────────────────────────

#: ``entity.status``. GLEIF's statement about the ORGANISATION, as distinct
#: from the statement about its registration below.
GLEIF_DEAD_ENTITY_STATUS: frozenset[str] = frozenset({"INACTIVE"})

#: ``registration.status``. ``LAPSED`` is deliberately absent — see the module
#: docstring; it is 35% of the register. ``ANNULLED`` is absent because it
#: retracts a record, not an organisation.
GLEIF_DEAD_REGISTRATION_STATUS: frozenset[str] = frozenset({"RETIRED", "MERGED"})

#: ROR ``status``. ``withdrawn`` is deliberately absent — it is ROR's
#: deduplication state, not a statement about the organisation.
ROR_DEAD_STATUS: frozenset[str] = frozenset({"inactive"})


# ── Sources, in the order their findings are rendered ─────────────────────────

SOURCE_ROR = "ror"
SOURCE_GLEIF = "gleif"
SOURCE_REDIRECT = "redirect"

#: Render order. A registry's own statement outranks an inference drawn from an
#: HTTP redirect, so the reviewer reads the strongest evidence first. Fixed
#: rather than discovery-ordered: two findings must render identically whatever
#: order the checks happened to complete in.
_SOURCE_ORDER: tuple[str, ...] = (SOURCE_ROR, SOURCE_GLEIF, SOURCE_REDIRECT)


@dataclass(frozen=True)
class LivenessFinding:
    """One source's statement that the organisation is gone.

    *detail* is a clause, not a sentence: it is rendered into
    ``_DETAILED_REASONS[ENTITY_SUPERSEDED]`` — "names an organisation that no
    longer exists as a separate entity (**{detail}**)" — so it must read as a
    fragment and must name the evidence rather than the lane.
    """

    source: str
    detail: str


def render_detail(findings: list[LivenessFinding]) -> str | None:
    """The reason clause for ``entity-superseded``, or ``None``.

    Every finding is rendered, not just the first: two sources independently
    saying an organisation is gone is materially more actionable than one, and
    the reviewer should not have to re-run the pipeline to discover the second.
    Ordered by :data:`_SOURCE_ORDER` and deduplicated, so the string is a
    function of the findings and not of their arrival order.
    """
    if not findings:
        return None
    seen: list[str] = []
    for source in _SOURCE_ORDER:
        for finding in findings:
            if finding.source == source and finding.detail not in seen:
                seen.append(finding.detail)
    return "; ".join(seen) or None


# ── GLEIF: free, because the fields are already in hand ───────────────────────

def gleif_verdict(
    entity_status: str | None, registration_status: str | None,
) -> LivenessFinding | None:
    """Read the status pair GLEIF returned with the record that matched.

    No call: :func:`enrichment.tier1_lei._record_fields` parses both values out
    of the response the record was already verified against, so this is a
    dictionary read on a lookup that has happened.
    """
    entity = (entity_status or "").strip().upper()
    registration = (registration_status or "").strip().upper()
    if entity in GLEIF_DEAD_ENTITY_STATUS:
        return LivenessFinding(
            SOURCE_GLEIF, f"GLEIF records this entity as {entity}",
        )
    if registration in GLEIF_DEAD_REGISTRATION_STATUS:
        return LivenessFinding(
            SOURCE_GLEIF,
            f"the GLEIF registration for this entity is {registration}",
        )
    return None


# ── ROR: one query, because search hides exactly what we are looking for ──────

def _ror_display_name(org: dict[str, Any]) -> str | None:
    """The ``ror_display`` label, stripped the way the main lane strips it."""
    for entry in org.get("names") or []:
        if "ror_display" in (entry.get("types") or []):
            value = (entry.get("value") or "").strip()
            if value:
                return strip_parentheticals(_strip_ror_country_suffix(value))
    return None


def ror_verdict(
    org: dict[str, Any] | None, *, score: float, threshold: float,
) -> LivenessFinding | None:
    """Judge the best-scoring ROR candidate, active or not.

    Takes the winner of a scored field rather than the first inactive hit:
    a name that matches a live organisation better than a dead one has not
    identified the dead one, and flagging on any inactive match at all would
    report every company whose name resembles some defunct org.
    """
    if not org or score < threshold:
        return None
    status = (org.get("status") or "").strip().lower()
    if status not in ROR_DEAD_STATUS:
        return None
    label = _ror_display_name(org) or "this organisation"
    return LivenessFinding(
        SOURCE_ROR, f"ROR records {label!r} as {status}",
    )


async def probe_ror_status(
    name: str,
    *,
    country_code: str | None = None,
    threshold: float,
    timeout: float = 15.0,
    base_url: str | None = None,
) -> tuple[LivenessFinding | None, dict[str, Any] | None, float]:
    """Ask ROR about *name* **including the records its search hides**.

    ROR omits non-active organisations from its default index, which is why
    the main lane cannot see them: ``?query=Celgene`` returns nothing, and
    ``affiliation=Celgene Corporation, Summit, NJ`` returns ten unrelated
    companies with none ``chosen``. The record exists and holds the answer —
    ``0527yg379``, ``status: inactive`` — but only ``all_status=`` puts it in
    the result set.

    The whole field is scored, active candidates included, and the best one is
    judged: see :func:`ror_verdict`. Scoring is
    :func:`enrichment.tier1_ror._score_org` at ``ROR_CONFIDENCE_THRESHOLD`` —
    the main lane's scorer at the main lane's threshold, with its
    distinctive-token and identifier guards. No new metric and no new number.

    The query is scored in **two forms**, raw and with legal-form suffixes
    dropped, and the better is taken. ROR's scorer has no legal-suffix rule of
    its own — its token-subset test requires every query token of four
    characters or more to appear in the candidate — so ``"Celgene Corp"``
    against ROR's ``"Celgene (United States)"`` scores 0.457 and misses the
    threshold on the strength of the word "Corp". Stripped, it is 1.0. This is
    the same max-of-both-forms rule
    :func:`enrichment.tier1_lei._name_match_score` already applies for GLEIF,
    for the same reason and using that module's own normaliser; it is not a new
    relaxation, and the ROR guards still run on both forms.

    Returns ``(finding, winning_org, score)``. Never raises.
    """
    query = (name or "").strip()
    if not query:
        return None, None, 0.0
    if base_url is None:
        base_url = os.getenv("ROR_API_BASE", "https://api.ror.org/v2/organizations")

    params: dict[str, str] = {"query": query, "all_status": ""}
    if country_code:
        params["filter"] = (
            f"locations.geonames_details.country_code:{country_code}"
        )

    try:
        async with httpx.AsyncClient(
            timeout=timeout, verify=resolve_tls_verify(),
        ) as client:

            async def _fetch() -> dict[str, Any]:
                resp = await client.get(base_url, params=params)
                resp.raise_for_status()
                return resp.json()

            data = await cached_registry_get("ror", base_url, params, _fetch)
    except RegistryUnavailableFrozen:
        logger.info("liveness: frozen cache has no ROR status probe for %r", query[:60])
        return None, None, 0.0
    except Exception as exc:  # noqa: BLE001 — the lane must never fail a record
        logger.info("liveness: ROR status probe failed for %r (%s)", query[:60], exc)
        return None, None, 0.0

    items = (data or {}).get("items") or []
    if not items:
        return None, None, 0.0

    # Deterministic winner. Ties break on the ROR ID, for the same reason the
    # GLEIF fuzzy candidates are truncated by LEI rather than by arrival order
    # (`tier1_lei` Fix C(1)): ROR does not promise a stable order across calls,
    # and two candidates on the same score must not swap between runs.
    stripped = _normalise_legal_name(query)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for org in items:
        try:
            score = float(_score_org(query, org))
            if stripped and stripped != query.lower():
                score = max(score, float(_score_org(stripped, org)))
        except Exception:  # noqa: BLE001 — a malformed candidate is not fatal
            continue
        current_id = str((best or {}).get("id") or "")
        if score > best_score or (
            score == best_score and best is not None
            and str(org.get("id") or "") < current_id
        ):
            best, best_score = org, score

    return (
        ror_verdict(best, score=best_score, threshold=threshold),
        best,
        best_score,
    )


# ── Redirect: the only signal that needs no registry at all ───────────────────

def _domain_stem(domain: str | None) -> str:
    """The registrable domain's first label — ``bms.com`` → ``bms``."""
    return (domain or "").split(".")[0].strip().lower()


def redirect_verdict(
    name: str | None,
    start_domain: str | None,
    final_domain: str | None,
    *,
    threshold: float,
) -> LivenessFinding | None:
    """Judge a website that redirects off its own registrable domain.

    ``celgene.com`` serves ``https://www.bms.com/``. No registry is involved
    and none is needed: the organisation's own website now belongs to somebody
    else, which is what an acquisition looks like from the outside. This is the
    only signal in the lane that fires on a company in neither ROR nor GLEIF —
    and, for Celgene, one of only two that fire at all.

    A cross-domain redirect has a second, innocent reading, which is why the
    name check is here rather than the flag being raised on the redirect alone:
    an organisation that simply *moved* also redirects, and ROR's stale
    ``dur.ac.uk`` → live ``durham.ac.uk`` is the case
    :meth:`Orchestrator._resolve_probe_base` was written for. The two are
    separated by whether the landing domain still names the same organisation::

        celgene.com             → bms.com          stem score   0.0   flag
        mellanox.com            → nvidia.com                   14.8   flag
        horizontherapeutics.com → amgen.com                    16.7   flag
        alexionpharma.com       → alexion.com                  70.0   keep
        dur.ac.uk               → durham.ac.uk                 66.7   keep

    Measured 2026-08-26. The band between 16.7 and 66.7 is empty, and
    ``LIVENESS_REDIRECT_NAME_THRESHOLD`` defaults to 60 — inside that gap, far
    from either wall. It is a new threshold, which this pipeline avoids on
    principle, because no existing one fits: ``LEI_NAME_MATCH_THRESHOLD`` (88)
    would call Alexion and Durham different organisations, and reporting a
    university that renamed its domain as dissolved is precisely the false
    positive this check has to not make.

    Returns ``None`` — never a finding — when either domain is missing or the
    two are the same registrable domain. Absence of a redirect is not evidence
    of anything: an acquirer routinely keeps the acquired brand's site up
    (``monsanto.com`` still serves Monsanto years after Bayer), so this check
    has low recall by construction and can never support a completeness claim.
    """
    start = canonicalise_domain(start_domain)
    final = canonicalise_domain(final_domain)
    if not start or not final or start == final:
        return None

    start_stem, final_stem = _domain_stem(start), _domain_stem(final)
    if not final_stem:
        return None
    # Best of the two readings of "does the landing domain still name us" — the
    # record's own name against it, and the departing domain against it. The
    # first catches `horizontherapeutics.com → amgen.com`; the second catches a
    # record whose name1 is an abbreviation its domain spells out.
    score = max(
        _name_match_score(name or "", final_stem),
        _name_match_score(start_stem, final_stem),
    )
    if score >= threshold:
        return None
    return LivenessFinding(
        SOURCE_REDIRECT,
        f"{start} redirects to {final}, which names a different organisation",
    )
