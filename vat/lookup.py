"""Missing-VAT lookups: what is shared by both modes.

Scope: rows whose ``VAT Registration No.`` is empty (``VAT_Format_Status =
MISSING``) and whose ``Country/Region Key`` is ``DE``. One lookup per Customer,
fanned out to every row of it, like the VIES check.

**Proposals only.** A lookup fills the ``VAT_Found_*`` columns and nothing
else; ``VAT Registration No.`` is never written, by either mode.

Two modes, built to be compared on the same file:

* ``grounded`` (``vat/lookup_grounded.py``) — search, fetch the Impressum, an
  LLM reads the fetched text, deterministic gates, VIES.
* ``llm_only`` (``vat/lookup_llm_only.py``) — an **experiment** that asks the
  model from memory, to measure how unreliable that is. Never a production
  path, and not reachable from the VAT endpoints.
* ``llm_web`` (``vat/lookup_web.py``) — the model searches the web with high
  reasoning effort, the way ChatGPT / Claude do, and reports the page it read
  the number on; the code fetches that page itself and holds the answer to
  the grounded mode's gates. Never from memory. The endpoints run it for
  ``llm_only`` too.

**Decision model** (design paper §8). Every result carries an operational
decision (``AUTO_ACCEPT`` / ``MANUAL_REVIEW`` / ``NOT_FOUND`` / ``CONFLICT``,
:func:`decide`) and an evidence-level confidence (:func:`confidence_of`).
Both are rules over the evidence the lookup recorded; the weighted match
score is reported next to them but decides nothing. ``AUTO_ACCEPT`` means
*eligible* for a controlled update: nothing here writes to SAP.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from vat.entity_match import (
    INPUT_INCOMPLETE,
    NAME_EXACT,
    PRIMARY_SOURCES,
    REGISTER_MISMATCH,
    STRONG_ADDRESS,
)
from vat.normalize import MISSING, VatFormat

# ── Modes ────────────────────────────────────────────────────────────────────
NONE = "none"
GROUNDED = "grounded"
LLM_ONLY = "llm_only"
#: The model searches the web (``vat/lookup_web.py``); never from memory.
LLM_WEB = "llm_web"
MODES = (NONE, GROUNDED, LLM_ONLY, LLM_WEB)

#: Labels in VAT_Lookup_Mode.
MODE_LABELS = {GROUNDED: "GROUNDED", LLM_ONLY: "LLM_ONLY", LLM_WEB: "LLM_WEB"}

#: Appended after the VAT_* columns, only when a lookup mode is on.
LOOKUP_COLUMNS: list[str] = [
    "VAT_Lookup_Mode",
    "VAT_Found_Value",
    "VAT_Found_Status",
    "VAT_Found_Source_URL",
    "VAT_Found_Attributed_To",
    "VAT_Found_Evidence",
    "VAT_Found_Date",
    "VAT_Found_Flags",
    "VAT_Found_Tier",
    # Design paper §8 / §10: decision, evidence and audit trail.
    "VAT_Found_Decision",
    "VAT_Found_Confidence",
    "VAT_Found_Source_Type",
    "VAT_Found_Page_Title",
    "VAT_Found_Published_Address",
    "VAT_Found_Name_Match",
    "VAT_Found_Address_Match",
    "VAT_Found_Match_Score",
    "VAT_Found_Validation_Status",
    "VAT_Found_Validation_Date",
    "VAT_Found_Agent_Version",
]

# ── VAT_Found_Status ─────────────────────────────────────────────────────────
#: Every gate passed and VIES said VALID.
FOUND_VERIFIED = "FOUND_VERIFIED"
#: An ID was read but a gate failed (reason in VAT_Found_Flags), or VIES did
#: not say VALID (status ``FOUND_UNVERIFIED:VIES_<status>``).
FOUND_UNVERIFIED = "FOUND_UNVERIFIED"
#: The lookup ran completely and no page offered a VAT ID.
NOT_FOUND = "NOT_FOUND"
#: The lookup could not look everywhere it would have (LLM failure, a frozen
#: cache miss) and found nothing — not evidence that there is nothing.
LOOKUP_INCOMPLETE = "LOOKUP_INCOMPLETE"
#: The lookup was not run for this row (e.g. no SERPAPI_KEY for Mode A).
NOT_RUN = "NOT_RUN"

# ── VAT_Found_Tier / VAT_Grounded_Tier (grounded only) ──────────────────────
#: Name and address both tie the ID to this customer.
TIER_PROPOSED = "PROPOSED"
#: One of the two ties is weaker (a partial name with a matching address, or
#: a matching name on a page that states no address).
TIER_LIKELY = "LIKELY"
#: A candidate was read, but nothing ties it to this customer well enough.
TIER_REVIEW = "REVIEW"
TIERS = (TIER_PROPOSED, TIER_LIKELY, TIER_REVIEW)

# ── VAT_Found_Decision (design paper §8) ────────────────────────────────────
#: Primary source, exact legal-entity match, exact or strong address match,
#: VIES VALID. Eligible for a controlled update (never made here).
AUTO_ACCEPT = "AUTO_ACCEPT"
#: A candidate, but name, address, source quality or legal relationship is
#: incomplete: a data steward reviews the evidence.
MANUAL_REVIEW = "MANUAL_REVIEW"
#: No reliable candidate: the VAT ID stays blank, the research record is kept.
DECISION_NOT_FOUND = "NOT_FOUND"
#: Trusted sources show different numbers for the entity: block and escalate.
CONFLICT = "CONFLICT"
DECISIONS = (AUTO_ACCEPT, MANUAL_REVIEW, DECISION_NOT_FOUND, CONFLICT)

# ── VAT_Found_Confidence (evidence level, design paper §8) ──────────────────
CONF_VERIFIED = "VERIFIED"
CONF_HIGH = "HIGH_CONFIDENCE"
CONF_MEDIUM = "MEDIUM_CONFIDENCE"
CONF_NOT_VERIFIED = "NOT_VERIFIED"
CONF_NOT_FOUND = "NOT_FOUND"
CONF_CONFLICT = "CONFLICT"
CONFIDENCES = (CONF_VERIFIED, CONF_HIGH, CONF_MEDIUM, CONF_NOT_VERIFIED, CONF_NOT_FOUND,
               CONF_CONFLICT)

# ── Flags the decision reads (set by the engines) ───────────────────────────
#: Prefix: several candidates in the winning tier with different numbers.
AMBIGUOUS_SAME_NAME = "AMBIGUOUS_SAME_NAME"
#: Prefix: another candidate whose page names this exact entity, at an
#: address that is not someone else's, states a different number.
CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
#: LLM_WEB: source_url is not one of the URLs the search visited.
SOURCE_NOT_VISITED = "SOURCE_NOT_VISITED"

#: Country/Region Keys in scope.
SCOPE_COUNTRIES = frozenset({"DE"})

#: Customers looked up at the same time. The time per customer is almost all
#: waiting on SerpAPI, web pages and the LLM, so this divides the wall time.
#: VIES is unaffected: its client keeps one concurrency limit and one pacing
#: interval across every in-flight lookup.
LOOKUP_CONCURRENCY = 5


@dataclass(frozen=True)
class LookupInput:
    """What a lookup may read about one customer."""

    customer: str | None
    names: tuple[str | None, ...]       # Name, Name 2, Name 3, Name 4
    street: str | None
    house_number: str | None
    postal_code: str | None
    city: str | None
    country: str | None
    #: Optional inputs (design paper §2): supporting evidence when present.
    website: str | None = None
    email: str | None = None
    register_number: str | None = None

    def missing_mandatory(self) -> tuple[str, ...]:
        """The design paper's mandatory inputs (§2) this record lacks."""
        return tuple(k for k, v in (
            ("name", self.name_1), ("street", self.street), ("postal_code", self.postal_code),
            ("city", self.city), ("country", self.country),
        ) if not v)

    @property
    def name_1(self) -> str | None:
        return self.names[0] if self.names else None


@dataclass(frozen=True)
class FoundResult:
    mode: str
    status: str
    value: str | None = None
    source_url: str | None = None
    attributed_to: str | None = None
    evidence: str | None = None
    date: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)
    #: Grounded only: TIER_PROPOSED / _LIKELY / _REVIEW; None without a candidate.
    tier: str | None = None
    # ── Evidence for the decision and the audit trail (design paper §10) ──
    source_type: str | None = None
    page_title: str | None = None
    #: The address the page states, as the reader extracted it. Kept only
    #: when it is on the page; the address match itself runs on the page text.
    published_address: str | None = None
    name_match: str | None = None
    address_match: str | None = None
    match_score: int | None = None
    #: VIES's answer for the candidate and when it was given. NOT_CHECKED
    #: when the candidate was never sent (a gate failed, or VIES is off).
    validation_status: str | None = None
    validation_date: str | None = None
    agent_version: str | None = None
    #: Wall time of the lookup in seconds (summary only; not a column).
    seconds: float | None = None

    @property
    def decision(self) -> str | None:
        return decide(self)

    @property
    def confidence(self) -> str | None:
        return confidence_of(self)

    def columns(self) -> dict[str, Any]:
        return {
            "VAT_Lookup_Mode": MODE_LABELS.get(self.mode, self.mode),
            "VAT_Found_Value": self.value,
            "VAT_Found_Status": self.status,
            "VAT_Found_Source_URL": self.source_url,
            "VAT_Found_Attributed_To": self.attributed_to,
            "VAT_Found_Evidence": self.evidence,
            "VAT_Found_Date": self.date,
            "VAT_Found_Flags": "; ".join(self.flags) or None,
            "VAT_Found_Tier": self.tier,
            "VAT_Found_Decision": self.decision,
            "VAT_Found_Confidence": self.confidence,
            "VAT_Found_Source_Type": self.source_type,
            "VAT_Found_Page_Title": self.page_title,
            "VAT_Found_Published_Address": self.published_address,
            "VAT_Found_Name_Match": self.name_match,
            "VAT_Found_Address_Match": self.address_match,
            "VAT_Found_Match_Score": self.match_score,
            "VAT_Found_Validation_Status": self.validation_status,
            "VAT_Found_Validation_Date": self.validation_date,
            "VAT_Found_Agent_Version": self.agent_version,
        }


def decide(r: FoundResult) -> str | None:
    """The design paper's operational decision (§8). None when the lookup
    did not run.

    ``AUTO_ACCEPT`` needs every one of: VIES VALID (``FOUND_VERIFIED``), tier
    ``PROPOSED``, a page naming the *exact* legal entity (not a branch, not a
    partial name, not another legal form), an exact or strong address match,
    a primary source (§4 tiers 1–2), a page the search really visited, and no
    register entry on the page that contradicts the record's."""
    if r.status == NOT_RUN:
        return None
    if not r.value or r.status in (NOT_FOUND, LOOKUP_INCOMPLETE):
        return DECISION_NOT_FOUND
    if any(f.startswith((AMBIGUOUS_SAME_NAME, CONFLICTING_SOURCES)) for f in r.flags):
        return CONFLICT
    if (r.status == FOUND_VERIFIED and r.tier == TIER_PROPOSED
            and r.name_match == NAME_EXACT and r.address_match in STRONG_ADDRESS
            and r.source_type in PRIMARY_SOURCES
            and not {SOURCE_NOT_VISITED, REGISTER_MISMATCH} & set(r.flags)):
        return AUTO_ACCEPT
    return MANUAL_REVIEW


def confidence_of(r: FoundResult) -> str | None:
    """The evidence-level classification (§8), alongside the decision."""
    d = decide(r)
    if d is None:
        return None
    if d == DECISION_NOT_FOUND:
        return CONF_NOT_FOUND
    if d == CONFLICT:
        return CONF_CONFLICT
    if d == AUTO_ACCEPT:
        return CONF_VERIFIED
    if r.status.endswith(":VIES_INVALID"):
        return CONF_NOT_VERIFIED
    if r.tier == TIER_PROPOSED:
        return CONF_HIGH
    if r.tier == TIER_LIKELY:
        return CONF_MEDIUM
    return CONF_NOT_VERIFIED


#: VAT_Found_* for a row outside the lookup's scope: every column blank.
BLANK_COLUMNS: dict[str, Any] = dict.fromkeys(LOOKUP_COLUMNS)

# ── Side-by-side comparison (LLM-only experiment + grounded) ─────────────────
#: The grounded lookup's result, when it runs NEXT TO the LLM-only experiment
#: so both can be compared row by row in one file. Appended after
#: LOOKUP_COLUMNS (which then hold the LLM-only result).
GROUNDED_COMPARE_COLUMNS: list[str] = [
    c.replace("VAT_Found_", "VAT_Grounded_") for c in LOOKUP_COLUMNS if c.startswith("VAT_Found_")
]
AGREEMENT_COLUMN = "VAT_LLM_vs_Grounded"
COMPARE_COLUMNS: list[str] = GROUNDED_COMPARE_COLUMNS + [AGREEMENT_COLUMN]

#: VAT_LLM_vs_Grounded values.
SAME = "SAME"                    # both proposed the same VAT ID
DIFFERENT = "DIFFERENT"          # both proposed one, and they differ
LLM_ONLY_VALUE = "LLM_ONLY"      # only the LLM-only experiment proposed one
GROUNDED_ONLY = "GROUNDED_ONLY"  # only the grounded lookup proposed one
NEITHER = "NEITHER"              # neither proposed one


def grounded_compare_columns(result: "FoundResult | None") -> dict[str, Any]:
    if result is None:
        return dict.fromkeys(GROUNDED_COMPARE_COLUMNS)
    return {c.replace("VAT_Found_", "VAT_Grounded_"): v
            for c, v in result.columns().items() if c.startswith("VAT_Found_")}


def _canon(value: str | None, country: str | None) -> str | None:
    import re

    from vat.normalize import assess_format

    if not value:
        return None
    return assess_format(value, country).normalized or re.sub(r"[^0-9A-Z]", "", value.upper()) or None


def agreement(llm_value: str | None, grounded_value: str | None, country: str | None) -> str:
    """Do the two modes propose the same VAT ID (normalised; spacing, case
    and a missing prefix ignored)?"""
    a, b = _canon(llm_value, country), _canon(grounded_value, country)
    if a and b:
        return SAME if a == b else DIFFERENT
    if a:
        return LLM_ONLY_VALUE
    if b:
        return GROUNDED_ONLY
    return NEITHER


class LookupEngine(Protocol):
    mode: str
    #: VAT_Found_Agent_Version: mode, prompt version and model.
    agent_version: str

    async def lookup(self, inp: LookupInput) -> FoundResult: ...

    def stats(self) -> dict[str, int]: ...

    async def aclose(self) -> None: ...


#: Which rows a lookup runs on.
#: ``missing`` — VAT blank (production use: propose IDs where SAP has none);
#: ``all`` — every row of a scope country, VAT ID or not (evaluation: the
#: proposal is compared with the SAP value — which the lookup never sees).
SCOPE_MISSING = "missing"
SCOPE_ALL = "all"
SCOPES = (SCOPE_MISSING, SCOPE_ALL)


def in_scope(fmt: VatFormat, scope: str = SCOPE_MISSING) -> bool:
    """A row the lookup is for: Country/Region Key DE, and — unless *scope* is
    ``all`` — no VAT value."""
    if fmt.country_key not in SCOPE_COUNTRIES:
        return False
    return scope == SCOPE_ALL or fmt.status == MISSING


# ── Proposal vs the SAP value (scope "all") ─────────────────────────────────
FOUND_VS_SAP = "VAT_Found_vs_SAP"
GROUNDED_VS_SAP = "VAT_Grounded_vs_SAP"
MATCHES_SAP = "MATCHES_SAP"            # the proposal is the SAP VAT ID
DIFFERS_FROM_SAP = "DIFFERS_FROM_SAP"  # a proposal, and it is not the SAP VAT ID
NO_PROPOSAL = "NO_PROPOSAL"            # SAP has a VAT ID, the lookup proposed none
SAP_BLANK = "SAP_BLANK"                # SAP has no VAT ID to compare with


def vs_sap(proposal: str | None, sap_normalized: str | None, country: str | None) -> str:
    """The proposal against the SAP value (both normalised)."""
    if not sap_normalized:
        return SAP_BLANK
    p = _canon(proposal, country)
    if not p:
        return NO_PROPOSAL
    return MATCHES_SAP if p == sap_normalized else DIFFERS_FROM_SAP


def lookup_kpis(results: list[FoundResult | None]) -> dict[str, Any]:
    """The design paper's reporting measures (§10), per customer researched
    (a customer's result is fanned out to its rows; it counts once). Rates
    are fractions of the customers researched; NOT_RUN is not researched."""
    from collections import Counter

    unique = list({id(r): r for r in results if r is not None}.values())
    researched = [r for r in unique if r.status != NOT_RUN]
    n = len(researched)
    decisions = Counter(r.decision for r in researched)
    seconds = [r.seconds for r in researched if r.seconds is not None]

    def rate(k: int) -> float | None:
        return round(k / n, 3) if n else None

    return {
        "researched_customers": n,
        "discovery_rate": rate(sum(1 for r in researched if r.value)),
        "verification_rate": rate(sum(1 for r in researched if r.status == FOUND_VERIFIED)),
        "auto_accept_rate": rate(decisions[AUTO_ACCEPT]),
        "manual_review_rate": rate(decisions[MANUAL_REVIEW]),
        "not_found_rate": rate(decisions[DECISION_NOT_FOUND]),
        "conflicts": decisions[CONFLICT],
        "avg_seconds_per_customer": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "decision": {d: decisions[d] for d in DECISIONS if decisions[d]},
        "confidence": dict(Counter(r.confidence for r in researched if r.confidence)),
        "conflicts_by_source_type": dict(Counter(
            r.source_type or "(none)" for r in researched if r.decision == CONFLICT)),
    }


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def run_lookups(
    engine: LookupEngine,
    inputs: list[LookupInput | None],
    concurrency: int = LOOKUP_CONCURRENCY,
) -> list[FoundResult | None]:
    """One lookup per Customer (per row when a row has no Customer), up to
    *concurrency* customers at a time; the answer is fanned out to every row
    of it, in input order. ``None`` in, ``None`` out (row out of scope).

    Parallelism changes only the wall time: each customer's lookup is
    independent, every external answer is cached under a key of its own
    request, and the output order is the input order."""
    keys: list[Any] = []
    first: dict[Any, LookupInput] = {}
    for index, inp in enumerate(inputs):
        if inp is None:
            keys.append(None)
            continue
        key = ("customer", inp.customer) if inp.customer else ("row", index)
        keys.append(key)
        first.setdefault(key, inp)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    version = getattr(engine, "agent_version", None)

    async def one(inp: LookupInput) -> FoundResult:
        async with semaphore:
            started = time.perf_counter()
            result = await engine.lookup(inp)
            flags = result.flags
            missing = inp.missing_mandatory()
            if missing:
                flags = (*flags, f"{INPUT_INCOMPLETE}:{','.join(missing)}")
            return replace(result, flags=flags, agent_version=result.agent_version or version,
                           seconds=round(time.perf_counter() - started, 3))

    results = await asyncio.gather(*(one(inp) for inp in first.values()))
    by_key = dict(zip(first, results))
    return [by_key[k] if k is not None else None for k in keys]
