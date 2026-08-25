"""Fix 2 — the three states an unchanged Name 1 can be in.

A record whose Name 1 leaves the pipeline exactly as it arrived used to have
one outcome, and it was reached two different ways. Whether that record was
flagged `low-confidence-unchanged` depended on whether Tier 3 happened to run:
`_apply_tier3` marks every name slot it declined to rewrite, and the ROR-miss
research passthrough marks Name 1 — but the company branch's
"canonicalisation failed, keep the input" path marks nothing. On the chemspeed
batch that produced 37 flags and 5 silent passes drawn from the same evidence
class, one of which (`3M Corporate`) had *less* evidence than several of the
flagged rows.

The doubt an unchanged Name 1 carries is not one thing, so one outcome cannot
express it:

``unchanged-verified``
    Something independent of the record says this name belongs to this
    organisation: a domain the ownership guard tied to Name 1, a page read that
    states the organisation's identity (Fix 3), or a Wikidata item that passed
    the crosswalk lane's full type / country / name / headquarters gauntlet
    without carrying a registry pointer. Provenance ``input:1:verified``. Not
    flagged — a reviewer asked to "confirm the value is correct" has nothing to
    do that the pipeline has not already done.

``unchanged-confirmed``
    The company-canonical model, asked what the organisation is called and
    never shown the record's own answer as a candidate, returned that answer.
    Provenance ``input:1:confirmed``. Not flagged. This is deliberately NOT a
    yes/no "is this canonical?" question: asking a model to agree about an
    entity it may know nothing about measures agreement bias, not the name.
    Only a generated proposal counts, and only when it reproduces the input
    under ``normalize_key`` — the same equality Stage 5 uses to decide that a
    "correction" is punctuation and not a new name.

``unchanged-unresolved``
    Nothing came back, or what came back was refused by the identity guard and
    names something materially different. Provenance ``input:1:rule``,
    unchanged. Flagged `low-confidence-unchanged` with the existing reason
    text, byte for byte.

The decision is taken **once**, in ``finalise``, from the record's settled
state — the same rule as `enrichment.flags`, and for the same reason: which
tier ran is not the question. That is also what makes the treatment consistent,
because the answer no longer depends on which branch reached the passthrough.

Scope is Name 1. The department slots keep their existing per-field
`low-confidence-unchanged` behaviour: the states here are about an
organisation's identity, and the evidence that settles it — a registry, an
owned domain, a page that names the company — has no counterpart for "is this
the right spelling of the Department of Chemistry".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dedup.signatures import normalize_key
from enrichment.provenance import (
    INPUT_CORROBORATED,
    INPUT_SELF_CONSISTENT,
    Evidence,
)

logger = logging.getLogger(__name__)

UNCHANGED_VERIFIED = "unchanged-verified"
UNCHANGED_CONFIRMED = "unchanged-confirmed"
UNCHANGED_UNRESOLVED = "unchanged-unresolved"

#: The producer that authors a Name 1 the pipeline kept from the record.
INPUT_PRODUCER = "input"

#: Ownership conditions (``utils.domain_resolver.resolve_domain``) that tie the
#: accepted domain to **Name 1** specifically, and therefore corroborate the
#: name rather than merely the record:
#:
#: * ``name``     — ``token_sort_ratio`` between Name 1 and the registrable
#:                  stem cleared ``DOMAIN_NAME_MATCH_THRESHOLD``. This *is* the
#:                  "registrable stem shares a distinctive token with Name 1"
#:                  test, evaluated by the existing guard; there is deliberately
#:                  no second, weaker token matcher here.
#: * ``serp``     — every significant Name-1 token appears in the title or H1 of
#:                  a search result **on that domain** (``has_on_domain_evidence``).
#: * ``registry`` — ROR/GLEIF supplied the site for a record that passed that
#:                  registry's own guard.
#:
#: ``email`` is excluded on purpose. A non-generic address on the record says
#: which organisation the record belongs to; it says nothing about whether the
#: Name 1 *string* is that organisation's name, which is the question here.
#: ``unguarded`` is excluded because the guard was switched off, so nothing was
#: checked at all.
NAME_TYING_OWNERSHIP_CONDITIONS: frozenset[str] = frozenset(
    {"name", "serp", "registry"},
)


@dataclass(frozen=True)
class UnchangedOutcome:
    """One record's unchanged-Name-1 state and what settled it."""

    state: str
    #: Short machine-readable evidence label — ``domain:name``, ``domain:serp``,
    #: ``page:<domain>``, ``canonical_proposal``, or ``none``.
    evidence: str
    #: What a reviewer would open to check it: the domain, the page URL, or the
    #: proposal string. ``None`` for ``unchanged-unresolved``.
    evidence_ref: Any = None

    @property
    def flagged(self) -> bool:
        return self.state == UNCHANGED_UNRESOLVED


def name1_was_kept(result: Any) -> bool:
    """True when the shipped Name 1 is the record's own value.

    Read from the provenance log's attributing producer rather than by
    comparing strings: casing, abbreviation expansion and legal-suffix collapse
    all run after the tiers and are transforms, so a string comparison would
    call several retained names "changed" and several rewritten ones
    "unchanged".
    """
    log = getattr(result, "provenance", None)
    if log is None or not hasattr(log, "attributing_event"):
        return False
    event = log.attributing_event("name1_enriched")
    return bool(event) and event.producer_chain[-1] == INPUT_PRODUCER


def resolve(result: Any) -> UnchangedOutcome | None:
    """Which of the three states this record's unchanged Name 1 is in.

    ``None`` in two cases, and they are different:

    * Name 1 was **rewritten** — a tier authored the shipped value, so none of
      the three states describe it.
    * The record **never reached Tier 1** — a UC 0 overflow or an opaque-code
      short-circuit returns from Stage 0, before anything was asked about the
      organisation's identity. All three states are verdicts on an attempt to
      establish the name, and "unresolved" in particular reads "nothing came
      back", which would be a false account of a question never put. Those
      records carry their own structural code (`overflow`, `opaque-code`) and
      that is the actionable one. ``_tier1_query_name`` — the string Tier 1 was
      actually queried with — is the marker, the same one Stage 5 keys off.

    Precedence: corroborating **evidence** outranks a second opinion. A record
    whose domain the ownership guard tied to Name 1 is verified even if the
    canonicaliser also agreed, because the domain is a thing a reviewer can
    open and the agreement is not.
    """
    if not name1_was_kept(result):
        return None
    if not (result.get("_tier1_query_name") or "").strip():
        return None

    name1 = (result.get("name1_enriched") or "").strip()
    if not name1:
        return None

    # ── unchanged-verified ────────────────────────────────────────────────
    # Fix 3's page read, first: it is the strongest of the three, because it
    # is the organisation's own site stating its own identity.
    page = result.get("_page_corroboration")
    if page and page.get("corroborated"):
        return UnchangedOutcome(
            UNCHANGED_VERIFIED, f"page:{page.get('domain')}",
            page.get("source_url") or page.get("domain"),
        )

    verified_by = result.get("domain_verified_by")
    if result.get("domain") and verified_by in NAME_TYING_OWNERSHIP_CONDITIONS:
        return UnchangedOutcome(
            UNCHANGED_VERIFIED, f"domain:{verified_by}", result["domain"],
        )

    # The Wikidata crosswalk lane matched an item — through the type, country,
    # name and headquarters gauntlet — that carried no registry pointer. That
    # is a single independent source saying this name belongs to an
    # organisation of this kind in this place, which is exactly what
    # `unchanged-verified` asserts and no more. It is placed BELOW the domain
    # check on purpose: an owned domain and a page read are things a reviewer
    # can open and see for themselves, and a wiki item is weaker than either,
    # so it settles the state only where neither of them did.
    #
    # It can never do more than this. Nothing in the witness path writes
    # `name1_enriched` — the value here is the record's own, and this event
    # only records why it was allowed to stand unflagged.
    wikidata = result.get("_wikidata_corroboration")
    if wikidata and wikidata.get("corroborated"):
        qid = wikidata.get("qid")
        return UnchangedOutcome(
            UNCHANGED_VERIFIED, f"wikidata:{qid}",
            f"https://www.wikidata.org/wiki/{qid}" if qid else None,
        )

    # ── unchanged-confirmed ───────────────────────────────────────────────
    proposal = (result.get("_canonical_proposal") or "").strip()
    if proposal and normalize_key(proposal) == normalize_key(name1):
        return UnchangedOutcome(
            UNCHANGED_CONFIRMED, "canonical_proposal", proposal,
        )

    return UnchangedOutcome(UNCHANGED_UNRESOLVED, "none", None)


def enrichment_status_for(outcome: UnchangedOutcome, current: str | None) -> str:
    """The ``enrichment_status`` a settled unchanged state implies.

    The status column is what DATAshaper maps to an issue severity: ``enriched``
    is no issue, ``verified`` is "Info — confirmed correct", ``unresolved`` is
    "Warning — manual review", ``failed`` is "Error — investigate". A record
    Fix 2 declines to flag must not simultaneously ask a data steward to review
    it, so verified and confirmed map to ``verified``, which is exactly what
    that value has always meant and is the first time anything but Tier 2A
    Mode B has produced it.

    ``enriched`` is never downgraded: a record whose Name 1 was retained can
    still have had Name 2 or the domain genuinely enriched, and that is the
    stronger statement. ``unresolved`` for the unresolved state is unchanged.
    """
    if outcome.state == UNCHANGED_UNRESOLVED:
        return current or "unresolved"
    if current == "enriched":
        return current
    return "verified"


def evidence_for(outcome: UnchangedOutcome, name1: str) -> Evidence | None:
    """The provenance event that records a corroborated / confirmed state.

    Appended as a ``write`` of the value the record already holds, which is the
    honest shape: the input value stands, and this event is what a reviewer
    reads to see *why* it was allowed to stand without a flag. Without it the
    derived scalar could only say ``input:1:rule``, and the three states would
    exist in the flag column and nowhere else.

    ``None`` for ``unchanged-unresolved`` — nothing happened, and an event
    saying so would be noise.
    """
    if outcome.state == UNCHANGED_VERIFIED:
        return Evidence(
            producer_chain=(INPUT_PRODUCER,),
            tier=1,
            confidence_scale=INPUT_CORROBORATED,
            confidence_value=1.0,
            evidence_ref={
                "corroborated_by": outcome.evidence,
                "evidence_ref": outcome.evidence_ref,
                "value": name1,
            },
            rule_id="fix2:unchanged-verified",
        )
    if outcome.state == UNCHANGED_CONFIRMED:
        return Evidence(
            producer_chain=(INPUT_PRODUCER,),
            tier=1,
            confidence_scale=INPUT_SELF_CONSISTENT,
            confidence_value=1.0,
            evidence_ref={
                "proposal": outcome.evidence_ref,
                "value": name1,
                "matched_under": "normalize_key",
            },
            rule_id="fix2:unchanged-confirmed",
        )
    return None
