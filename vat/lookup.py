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
  path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from vat.normalize import MISSING, VatFormat

# ── Modes ────────────────────────────────────────────────────────────────────
NONE = "none"
GROUNDED = "grounded"
LLM_ONLY = "llm_only"
MODES = (NONE, GROUNDED, LLM_ONLY)

#: Mode-A/B labels in VAT_Lookup_Mode.
MODE_LABELS = {GROUNDED: "GROUNDED", LLM_ONLY: "LLM_ONLY"}

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
        }


#: VAT_Found_* for a row outside the lookup's scope: every column blank.
BLANK_COLUMNS: dict[str, Any] = dict.fromkeys(LOOKUP_COLUMNS)

# ── Side-by-side comparison (LLM-only experiment + grounded) ─────────────────
#: The grounded lookup's result, when it runs NEXT TO the LLM-only experiment
#: so both can be compared row by row in one file. Appended after
#: LOOKUP_COLUMNS (which then hold the LLM-only result).
GROUNDED_COMPARE_COLUMNS: list[str] = [
    "VAT_Grounded_Value",
    "VAT_Grounded_Status",
    "VAT_Grounded_Source_URL",
    "VAT_Grounded_Attributed_To",
    "VAT_Grounded_Evidence",
    "VAT_Grounded_Date",
    "VAT_Grounded_Flags",
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
    return {
        "VAT_Grounded_Value": result.value,
        "VAT_Grounded_Status": result.status,
        "VAT_Grounded_Source_URL": result.source_url,
        "VAT_Grounded_Attributed_To": result.attributed_to,
        "VAT_Grounded_Evidence": result.evidence,
        "VAT_Grounded_Date": result.date,
        "VAT_Grounded_Flags": "; ".join(result.flags) or None,
    }


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

    async def one(inp: LookupInput) -> FoundResult:
        async with semaphore:
            return await engine.lookup(inp)

    results = await asyncio.gather(*(one(inp) for inp in first.values()))
    by_key = dict(zip(first, results))
    return [by_key[k] if k is not None else None for k in keys]
