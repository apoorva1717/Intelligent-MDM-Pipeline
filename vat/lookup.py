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

**Decision model.** Every result carries its issue codes
(``VAT_Found_Issues``, :func:`issues_of`), an operational decision
(``AUTO_ACCEPT`` / ``MANUAL_REVIEW`` / ``NOT_FOUND`` / ``CONFLICT``,
:func:`decide`) and an evidence-level confidence (:func:`confidence_of`).
The decision is a function of the issues: a candidate with no blocking issue
(:data:`vat.entity_match.BLOCKING_ISSUES`) is ``AUTO_ACCEPT``, one with any is
``MANUAL_REVIEW``, ``CONFLICTING_SOURCES`` is ``CONFLICT``. The weighted match
score is reported but decides nothing. ``AUTO_ACCEPT`` means *eligible* for a
controlled update: nothing here writes to SAP, and :func:`sap_update_eligible`
also needs VIES VALID. A MANUAL_REVIEW candidate carries a review tier
(:func:`review_tier`: HIGH / MEDIUM / LOW) as its confidence.

**VIES is not in the lookup path.** A candidate is not sent to VIES while it
is looked up: it leaves the lookup as ``VAT_Found_Validation_Status =
PENDING`` with ``VIES_PENDING`` (:func:`vies_pending`), is queued, and the
queue (``vat/vies_retry.py``) verifies it in batches at the end of the run.

**One lookup per legal entity** (:func:`entity_key`: Name 1 + legal form +
City), not per record; the result is copied to every record of the entity.
Each record keeps its own address match; the decision is the entity's.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from vat.entity_match import (
    ACCEPTED_ADDRESS,
    ADDRESS_DIFFERENT_TOWN,
    ADDRESS_EXACT,
    ADDRESS_ISSUE,
    ADDRESS_MISMATCH,
    ADDRESS_NOT_EXACT,
    ADDRESS_NOT_ON_PAGE,
    CLASS_C,
    CONFLICTING_SOURCES as ISSUE_CONFLICTING_SOURCES,
    DEPARTMENT,
    EVIDENCE_NOT_VERIFIED,
    EXTRACTED_BY_PATTERN,
    INPUT_INCOMPLETE,
    ISSUE_NAME_PARTIAL,
    ISSUE_ORDER,
    LEGAL_FORM_DIFFERS,
    NAME_CONTAINED,
    NAME_EXACT,
    NAME_MISMATCH,
    NAME_PARTIAL,
    OTHER_VAT_IDS_ON_PAGE,
    PARENT_ENTITY,
    PARENT_ENTITY_CANDIDATE,
    SECONDARY_SOURCE,
    SOURCE_CLASS,
    SUCCESSOR_CANDIDATE,
    TRUSTED_CLASSES,
    VIES_INVALID,
    VIES_PENDING,
    VIES_UNAVAILABLE,
    address_match as _address_match,
    fold,
    has_department,
    is_blocking,
    issue_code,
    parse_name,
    sort_issues,
)
from vat.normalize import (
    FORMAT_OK,
    MISSING,
    NON_EU_PREFIX,
    PREFIX_COUNTRY_MISMATCH,
    VatFormat,
    assess_format,
)

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
    # Who published the number (A / B / C) and every issue the evidence raises.
    "VAT_Found_Source_Class",
    "VAT_Found_Issues",
]

# ── VAT_Found_Status ─────────────────────────────────────────────────────────
#: Every gate passed and VIES said VALID.
FOUND_VERIFIED = "FOUND_VERIFIED"
#: An ID was read but a gate failed (reason in VAT_Found_Flags), or VIES did
#: not say VALID (status ``FOUND_UNVERIFIED:VIES_<status>``).
FOUND_UNVERIFIED = "FOUND_UNVERIFIED"
#: The lookup ran completely and no page offered a VAT ID.
NOT_FOUND = "NOT_FOUND"
#: No VAT ID, and none is expected (decision NOT_FOUND). STATED: a page says
#: the entity has none (not liable to VAT, Kleinunternehmer); the quote is the
#: evidence. INFERRED: a school / authority, and no page found states one —
#: an assumption, not evidence, so it is still counted as not found.
NOT_FOUND_NO_VAT_EXPECTED = "NOT_FOUND_NO_VAT_EXPECTED"
NO_VAT_STATED = f"{NOT_FOUND_NO_VAT_EXPECTED}:STATED"
NO_VAT_INFERRED = f"{NOT_FOUND_NO_VAT_EXPECTED}:INFERRED"
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
#: MANUAL_REVIEW, weakest tier (Step AF-2). Replaces NOT_VERIFIED for review rows.
CONF_LOW = "LOW_CONFIDENCE"
CONF_NOT_VERIFIED = "NOT_VERIFIED"
CONF_NOT_FOUND = "NOT_FOUND"
CONF_CONFLICT = "CONFLICT"
CONFIDENCES = (CONF_VERIFIED, CONF_HIGH, CONF_MEDIUM, CONF_LOW, CONF_NOT_FOUND,
               CONF_CONFLICT)
#: The review tiers, strongest first.
REVIEW_TIERS = (CONF_HIGH, CONF_MEDIUM, CONF_LOW)

# ── Flags the decision reads (set by the engines) ───────────────────────────
#: Prefix: several candidates in the winning tier with different numbers.
AMBIGUOUS_SAME_NAME = "AMBIGUOUS_SAME_NAME"
#: Prefix: another candidate whose page names this exact entity, at an
#: address that is not someone else's, states a different number.
CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
#: LLM_WEB: source_url is not one of the URLs the search visited. Recorded,
#: not blocking: our own fetch of the page is the visit the rules ask for.
SOURCE_NOT_VISITED = "SOURCE_NOT_VISITED"

# ── VAT_Found_Validation_Status ──────────────────────────────────────────────
#: VIES did not answer (MS_UNAVAILABLE, a timeout, a service error) or was
#: switched off. Not blocking; the number is queued for re-verification
#: (``vat/vies_retry.py``).
VALIDATION_UNAVAILABLE = "UNAVAILABLE"
#: Queued for VIES, no answer yet (:func:`vies_pending`). Never written to SAP.
VALIDATION_PENDING = "PENDING"
#: Gates a–f passed, VIES not asked yet.
FOUND_PENDING = f"{FOUND_UNVERIFIED}:VIES_{VALIDATION_PENDING}"
#: VIES statuses that mean "no answer", not "no".
VIES_NO_ANSWER = frozenset({"SERVICE_UNAVAILABLE", "ERROR", VALIDATION_UNAVAILABLE})
#: VIES's own answers; anything else on a candidate becomes PENDING.
VIES_ANSWERED = frozenset({"VALID", "INVALID"})

#: Country/Region Keys in scope.
SCOPE_COUNTRIES = frozenset({"DE"})

#: Customers looked up at the same time. The time per customer is almost all
#: waiting on SerpAPI, web pages and the LLM, so this divides the wall time.
#: VIES is unaffected: its client keeps one concurrency limit and one pacing
#: interval across every in-flight lookup. 20 since the Azure calls are paced
#: to the quota (vat/rate_limit.py, 2026-11-08): this caps open connections
#: and page downloads; the limiter sets the real pace.
LOOKUP_CONCURRENCY = int(os.getenv("VAT_LOOKUP_CONCURRENCY", "20"))


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
    #: SAP's Central Deletion Flag is set: an archive snapshot may be a source.
    deleted: bool = False

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
    #: The engine's issue codes (name, legal form, sister entity, other IDs,
    #: register, evidence, department); :func:`issues_of` adds the address,
    #: source, VIES and conflict ones.
    issues: tuple[str, ...] = ()
    #: The page text an address match reads (``entity_match.address_text``),
    #: so every record of the entity can be matched against it. Not a column.
    address_text: str | None = None
    #: The entity this result was looked up for (:func:`entity_key`). Not a column.
    entity: str | None = None
    #: A record's copy of its entity's result keeps the entity's decision.
    entity_decision: str | None = None
    entity_confidence: str | None = None
    #: llm_web: the entity's API usage, tool calls, page fetches and cost
    #: (summary / run reports only; not a column).
    usage: dict[str, Any] | None = None

    @property
    def source_class(self) -> str | None:
        return SOURCE_CLASS.get(self.source_type or "")

    @property
    def all_issues(self) -> tuple[str, ...]:
        return issues_of(self)

    @property
    def decision(self) -> str | None:
        return self.entity_decision or decide(self)

    @property
    def confidence(self) -> str | None:
        return self.entity_confidence or confidence_of(self)

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
            "VAT_Found_Source_Class": self.source_class,
            "VAT_Found_Issues": "; ".join(self.all_issues) or None,
        }


def _is_candidate(r: FoundResult) -> bool:
    return bool(r.value) and r.status not in (NOT_RUN, NOT_FOUND, NO_VAT_STATED, NO_VAT_INFERRED,
                                              LOOKUP_INCOMPLETE)


#: Codes that keep a contained name from counting as the entity's own:
#: other numbers on the page, a number our code picked without a quote.
_NOT_WITH_CONTAINED = frozenset({OTHER_VAT_IDS_ON_PAGE, EXTRACTED_BY_PATTERN})


def name_contained_ok(r: FoundResult) -> bool:
    """2026-11-07: SAP's name stands whole inside the page's
    (NAME_CONTAINED: "LMU Klinikum" in "LMU Klinikum München, AöR") and the
    page is the organisation's own (source class A), at its address or in its
    town, with no other number on it and a number the reader quoted. Then the
    name counts as matched for AUTO_ACCEPT. On the AD 50 / FINAL 50 reviews,
    12 of the 13 such records with a SAP number had SAP's own number."""
    codes = {issue_code(i) for i in r.issues}
    return (NAME_CONTAINED in codes and r.source_class == "A" and r.address_match in ACCEPTED_ADDRESS
            and not codes & _NOT_WITH_CONTAINED)


def issues_of(r: FoundResult) -> tuple[str, ...]:
    """Every issue code for a result, in :data:`vat.entity_match.ISSUE_ORDER`.
    Empty without a candidate, and for a clean match.

    AUTO_ACCEPT needs ALL of: source class A or B, name EXACT (a branch is
    fine) or contained (:func:`name_contained_ok`), address EXACT or
    SAME_TOWN, VIES VALID or unavailable, the number read on a page our own
    code fetched, and no register mismatch, conflicting source or sister
    entity. Each unmet condition is a blocking issue here, so :func:`decide`
    only has to look at the issues."""
    if not _is_candidate(r):
        return ()
    contained = name_contained_ok(r)
    # NAME_CONTAINED outside its conditions is what it was before: NAME_PARTIAL.
    issues = [i if i != NAME_CONTAINED or contained else ISSUE_NAME_PARTIAL for i in r.issues]
    if r.address_match in ADDRESS_ISSUE:
        issues.append(ADDRESS_ISSUE[r.address_match])
    if r.source_class == CLASS_C:
        issues.append(SECONDARY_SOURCE)
    if r.validation_status == "INVALID" or r.status.endswith(":VIES_INVALID"):
        issues.append(VIES_INVALID)
    elif r.validation_status == VALIDATION_PENDING:
        issues.append(VIES_PENDING)
    elif r.validation_status in VIES_NO_ANSWER:
        issues.append(VIES_UNAVAILABLE)
    if any(f.startswith((AMBIGUOUS_SAME_NAME, CONFLICTING_SOURCES)) for f in r.flags):
        issues.append(ISSUE_CONFLICTING_SOURCES)
    # A GENERIC name or an initials match is weaker evidence: AUTO only with
    # the address EXACT (SAME_TOWN → review, HIGH).
    if ({"GENERIC_NAME", "NAME_ABBREVIATED"} & set(r.flags)) and r.address_match != ADDRESS_EXACT:
        issues.append(ADDRESS_NOT_EXACT)
    if not r.source_url:
        issues.append(EVIDENCE_NOT_VERIFIED)
    unmet = ((r.name_match != NAME_EXACT and not contained) or r.address_match not in ACCEPTED_ADDRESS
             or r.source_class not in TRUSTED_CLASSES)
    if unmet and not any(is_blocking(i) for i in issues):
        # A condition no specific code covers (no name or address was read).
        issues.append(EVIDENCE_NOT_VERIFIED)
    return sort_issues(issues)


def decide(r: FoundResult) -> str | None:
    """The operational decision. None when the lookup did not run.
    ``CONFLICT`` on CONFLICTING_SOURCES; ``MANUAL_REVIEW`` on any other
    blocking issue; ``AUTO_ACCEPT`` otherwise (see :func:`issues_of`)."""
    if r.status == NOT_RUN:
        return None
    if not _is_candidate(r):
        return DECISION_NOT_FOUND
    issues = issues_of(r)
    if any(issue_code(i) == ISSUE_CONFLICTING_SOURCES for i in issues):
        return CONFLICT
    if any(is_blocking(i) for i in issues):
        return MANUAL_REVIEW
    return AUTO_ACCEPT


def confidence_of(r: FoundResult) -> str | None:
    """The evidence level, alongside the decision: VERIFIED for AUTO_ACCEPT,
    the review tier (:func:`review_tier`) for MANUAL_REVIEW."""
    d = decide(r)
    if d is None:
        return None
    if d == DECISION_NOT_FOUND:
        return CONF_NOT_FOUND
    if d == CONFLICT:
        return CONF_CONFLICT
    if d == AUTO_ACCEPT:
        return CONF_VERIFIED
    return review_tier(r.name_match, r.address_match, r.source_class, issues_of(r), r.evidence)


#: Step AF-2: each of these makes a review candidate MEDIUM.
MEDIUM_ISSUES = frozenset({
    SECONDARY_SOURCE, LEGAL_FORM_DIFFERS, PARENT_ENTITY_CANDIDATE, SUCCESSOR_CANDIDATE,
    ADDRESS_NOT_ON_PAGE, VIES_UNAVAILABLE, VIES_PENDING,
})
#: NAME_PARTIAL together with one of these is LOW.
_LOW_WITH_PARTIAL = frozenset({ADDRESS_MISMATCH, SECONDARY_SOURCE, OTHER_VAT_IDS_ON_PAGE})


def review_tier(name_match: str | None, address_match: str | None, source_class: str | None,
                issues: Any, evidence: str | None = None) -> str:
    """A MANUAL_REVIEW candidate's tier (Step AF-2), from the columns a row
    carries, so the VIES queue can recompute it on a written row:

    * LOW — NAME_PARTIAL with ADDRESS_MISMATCH, SECONDARY_SOURCE or
      OTHER_VAT_IDS_ON_PAGE; EXTRACTED_BY_PATTERN without a quote;
      VIES_INVALID; and a number not checked on a page we read
      (EVIDENCE_NOT_VERIFIED);
    * MEDIUM — any of :data:`MEDIUM_ISSUES`;
    * HIGH — name EXACT (an ABBREVIATED or BRANCH match is EXACT with a
      flag), address EXACT or SAME_TOWN, source class A or B;
    * anything else (a partial name alone, another town alone): MEDIUM.

    The order keeps the tier monotone: another issue never raises it."""
    codes = {issue_code(i) for i in issues or ()}
    if ((ISSUE_NAME_PARTIAL in codes and codes & _LOW_WITH_PARTIAL)
            or (EXTRACTED_BY_PATTERN in codes and not (evidence or "").strip())
            or VIES_INVALID in codes or EVIDENCE_NOT_VERIFIED in codes):
        return CONF_LOW
    if codes & MEDIUM_ISSUES:
        return CONF_MEDIUM
    if (name_match == NAME_EXACT and address_match in ACCEPTED_ADDRESS
            and source_class in TRUSTED_CLASSES):
        return CONF_HIGH
    return CONF_MEDIUM


def vies_pending(r: FoundResult) -> FoundResult:
    """A candidate leaves the lookup without a VIES call: one with no VIES
    answer (never asked, or no answer) becomes ``PENDING`` with
    ``VIES_PENDING``, for the queue (``vat/vies_retry.py``). This covers a
    candidate whose name was reconciled after a gate stopped it before VIES
    (Göttingen, Konstanz, UK Tübingen). A CONFLICT is not queued."""
    if not _is_candidate(r) or r.validation_status in VIES_ANSWERED or decide(r) == CONFLICT:
        return r
    status = FOUND_PENDING if r.status.startswith(f"{FOUND_UNVERIFIED}:VIES_") else r.status
    return replace(r, status=status, validation_status=VALIDATION_PENDING, validation_date=None,
                   entity_decision=None, entity_confidence=None)


#: A number found on a page that cannot be the record's VAT ID: python-stdnum
#: rejects its syntax or check digit, or it carries another country's prefix
#: (a foreign parent's number). Status ``NOT_FOUND:INVALID_NUMBER``; the flag
#: ``INVALID_NUMBER:<number>:<why>`` keeps it for the reviewer.
INVALID_NUMBER = "INVALID_NUMBER"


def reject_invalid_number(r: FoundResult, country: str | None) -> FoundResult:
    """*r* unchanged unless its candidate fails the format check for the
    record's *country*; then NOT_FOUND, so the agent pass looks again
    (2026-11-10: 22 such proposals in the 7,358 DE rows, among them the
    template number DE123456789, page typos and NL / FR / GB parents' IDs).
    Applied by ``vat.validate.validate_rows`` and the agent pass, not by
    :func:`run_lookups`: ``tools/vat_lookup_compare.py`` measures what an
    engine proposes, malformed numbers included."""
    if not _is_candidate(r):
        return r
    fmt = assess_format(r.value, country)
    if fmt.status == FORMAT_OK and PREFIX_COUNTRY_MISMATCH not in fmt.flags:
        return r
    why = fmt.format_error or (PREFIX_COUNTRY_MISMATCH if PREFIX_COUNTRY_MISMATCH in fmt.flags else NON_EU_PREFIX)
    return replace(r, status=f"{NOT_FOUND}:{INVALID_NUMBER}", value=None, validation_status=None,
                   validation_date=None, entity_decision=None, entity_confidence=None,
                   flags=(*r.flags, f"{INVALID_NUMBER}:{fmt.normalized or r.value}:{why}"))


#: Step AL (2026-11-11): the only number found is another company's — a
#: page in another town that names the entity only partly or not at all.
#: Status ``NOT_FOUND:ONLY_OTHER_COMPANY_FOUND``; the number, the page's name
#: and the source stay in the usage (``withdrawn``) for the reviewer.
ONLY_OTHER_COMPANY_FOUND = "ONLY_OTHER_COMPANY_FOUND"
WITHDRAWN = "WITHDRAWN"


def other_name_other_town(r: FoundResult) -> bool:
    """A candidate whose page is in another town and names another or a
    similar company (Name_Check SIMILAR_NAME / DIFFERENT_NAME). Same name in
    another town is not: a head office, reviewed as before. Nor is a page
    naming exactly the record's name cut after its legal form ("Arrow Central
    Europe GmbH" for "Arrow Central Europe GmbH Büro Hamb"): an office or site
    of that legal entity (2026-11-12)."""
    if not _is_candidate(r) or r.address_match != ADDRESS_DIFFERENT_TOWN:
        return False
    if r.name_match not in (NAME_PARTIAL, NAME_MISMATCH):
        return False
    if any(issue_code(i) == NAME_CONTAINED for i in r.issues):
        return False
    return not _same_entity_office(r)


def _same_entity_office(r: FoundResult) -> bool:
    """The page's name is the record's entity name up to its legal form."""
    from vat.name_prep import name_up_to_form

    def key(s: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", fold(s or ""))

    found = key(r.attributed_to)
    usage = r.usage or {}
    names = [usage.get("entity"), *(usage.get("acceptable_names") or [])]
    return bool(found) and any(key(name_up_to_form(n)) == found for n in names if name_up_to_form(n))


def withdraw(r: FoundResult, reason: str = ONLY_OTHER_COMPANY_FOUND) -> FoundResult:
    """*r* as NOT_FOUND:<reason>, its number kept in ``usage["withdrawn"]``
    (number, the page's name, source URL, whether it looked like a parent)."""
    fmt = assess_format(r.value, r.value[:2] if r.value else None)
    usage = dict(r.usage or {}, withdrawn={
        "number": fmt.normalized or r.value, "found_name": r.attributed_to, "source_url": r.source_url,
        "found_address": r.published_address, "address_text": r.address_text,
        "parent": any(issue_code(i) in (PARENT_ENTITY_CANDIDATE, PARENT_ENTITY) for i in r.issues)})
    return replace(r, status=f"{NOT_FOUND}:{reason}", value=None, validation_status=None, validation_date=None,
                   entity_decision=None, entity_confidence=None,
                   flags=(*r.flags, f"{WITHDRAWN}:{fmt.normalized or r.value}"), usage=usage)


def reject_other_name_other_town(r: FoundResult) -> FoundResult:
    """VAT_REJECT_OTHER_NAME_OTHER_TOWN: :func:`withdraw` a candidate that is
    :func:`other_name_other_town`; *r* unchanged otherwise. On the first DE
    run (7,358 rows) these were 689 Review / Conflict rows, mostly another
    company's number (Step AL)."""
    return withdraw(r) if other_name_other_town(r) else r


def sap_update_eligible(decision: str | None, validation_status: str | None) -> bool:
    """May a controlled update write this number to SAP? Only an
    AUTO_ACCEPT that VIES has said VALID: never while PENDING."""
    return decision == AUTO_ACCEPT and validation_status == "VALID"


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


def default_scope(settings: Any) -> str:
    """The lookup scope when none is given: ``all`` with VAT_LOOKUP_ALL_ROWS
    (every row, VAT ID or not), else ``missing`` (rows with no VAT ID)."""
    return SCOPE_ALL if getattr(settings, "vat_lookup_all_rows", False) else SCOPE_MISSING


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
    """The reporting measures, per entity researched (an entity's result is
    copied to its records; it counts once). Rates are fractions of the
    entities researched; NOT_RUN is not researched. ``issues`` counts each
    issue code per decision segment."""
    from collections import Counter

    unique = list({r.entity or id(r): r for r in results if r is not None}.values())
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
        # AUTO_ACCEPT and VIES VALID: what a controlled update may write.
        "sap_update_eligible": sum(
            1 for r in researched if sap_update_eligible(r.decision, r.validation_status)),
        "vies_pending": sum(1 for r in researched if r.validation_status == VALIDATION_PENDING),
        "manual_review_rate": rate(decisions[MANUAL_REVIEW]),
        "not_found_rate": rate(decisions[DECISION_NOT_FOUND]),
        "conflicts": decisions[CONFLICT],
        "no_vat_stated": sum(1 for r in researched if r.status == NO_VAT_STATED),
        "no_vat_inferred": sum(1 for r in researched if r.status == NO_VAT_INFERRED),
        # Step AH: why the NOT_FOUND ones are (``NOT_FOUND:<reason>``).
        "not_found_reasons": dict(Counter(
            r.status.split(":", 1)[1] for r in researched if r.status.startswith(f"{NOT_FOUND}:"))),
        "avg_seconds_per_customer": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "decision": {d: decisions[d] for d in DECISIONS if decisions[d]},
        "confidence": dict(Counter(r.confidence for r in researched if r.confidence)),
        "conflicts_by_source_type": dict(Counter(
            r.source_type or "(none)" for r in researched if r.decision == CONFLICT)),
        "issues": {
            d: dict(sorted(Counter(
                issue_code(i) for r in researched if r.decision == d for i in r.all_issues
            ).items(), key=lambda kv: ISSUE_ORDER.index(kv[0])))
            for d in DECISIONS if decisions[d]
        },
    }


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def entity_key(inp: LookupInput) -> str | None:
    """The legal entity a record belongs to: normalised Name 1, legal form
    (Name 1's, else Name 1–4's) and City. None without a Name 1."""
    if not inp.name_1:
        return None
    from vat.name_prep import prepare  # the entity Name 1–4 describe (Step AD)

    plan = prepare(inp.names)
    name = parse_name(plan.entity, sap=not plan.complete)
    return "|".join((" ".join(name.core), plan.form or name.form or "", fold(inp.city)))


def for_record(entity: FoundResult, inp: LookupInput) -> FoundResult:
    """The entity's result as written to one of its records: the record's
    own address match against the same page, DEPARTMENT when its Name 2–4
    hold a department, INPUT_INCOMPLETE for what it lacks. The decision and
    confidence stay the entity's."""
    flags, issues, address = entity.flags, entity.issues, entity.address_match
    missing = inp.missing_mandatory()
    if missing:
        flags = (*flags, f"{INPUT_INCOMPLETE}:{','.join(missing)}")
    if _is_candidate(entity) and any(has_department(n) for n in inp.names[1:]):
        issues = (*issues, DEPARTMENT)
    if entity.address_text and entity.address_match in ACCEPTED_ADDRESS:
        own, _ = _address_match(entity.address_text, inp.street, inp.house_number,
                                inp.postal_code, inp.city)
        # Only between the two accepted levels: the decision is the entity's.
        if own in ACCEPTED_ADDRESS:
            address = own
    return replace(entity, flags=flags, issues=issues, address_match=address,
                   entity_decision=entity.decision, entity_confidence=entity.confidence)


async def run_lookups(
    engine: LookupEngine,
    inputs: list[LookupInput | None],
    concurrency: int = LOOKUP_CONCURRENCY,
) -> list[FoundResult | None]:
    """One lookup per legal entity (:func:`entity_key`; per row when a row
    has no Name 1), up to *concurrency* at a time; the answer is copied to
    every record of the entity (:func:`for_record`), in input order. ``None``
    in, ``None`` out (row out of scope).

    Parallelism changes only the wall time: each lookup is independent, every
    external answer is cached under a key of its own request, and the output
    order is the input order."""
    keys: list[Any] = []
    first: dict[Any, LookupInput] = {}
    for index, inp in enumerate(inputs):
        if inp is None:
            keys.append(None)
            continue
        entity = entity_key(inp)
        key = ("entity", entity) if entity else ("row", index)
        keys.append(key)
        first.setdefault(key, inp)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    version = getattr(engine, "agent_version", None)

    async def one(key: Any, inp: LookupInput) -> FoundResult:
        async with semaphore:
            started = time.perf_counter()
            result = vies_pending(await engine.lookup(inp))
            return replace(result, agent_version=result.agent_version or version,
                           entity=repr(key), seconds=round(time.perf_counter() - started, 3))

    results = await asyncio.gather(*(one(k, inp) for k, inp in first.items()))
    by_key = dict(zip(first, results))
    return [(_not_shared(by_key[k]) if _aggregator_only(by_key[k]) and inp is not first[k]
             else for_record(by_key[k], inp)) if k is not None else None
            for k, inp in zip(keys, inputs)]


#: Flags: the number was read on a directory entry (unternehmensverzeichnis.org,
#: ``vat/lookup_web.py``); a sibling record of the entity did not get it.
AGGREGATOR_FALLBACK = "AGGREGATOR_FALLBACK"
AGGREGATOR_NOT_SHARED = "AGGREGATOR_NOT_SHARED"


def _aggregator_only(result: FoundResult) -> bool:
    return bool(result.value) and AGGREGATOR_FALLBACK in result.flags


def _not_shared(result: FoundResult) -> FoundResult:
    """A directory's candidate stays with the record it was looked up for;
    the entity's other records get NOT_FOUND (Step AF rule 17)."""
    return FoundResult(result.mode, NOT_FOUND, flags=(AGGREGATOR_NOT_SHARED,), agent_version=result.agent_version,
                       entity=result.entity)
