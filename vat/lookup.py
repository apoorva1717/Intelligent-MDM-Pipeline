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


class LookupEngine(Protocol):
    mode: str

    async def lookup(self, inp: LookupInput) -> FoundResult: ...

    def stats(self) -> dict[str, int]: ...

    async def aclose(self) -> None: ...


def in_scope(fmt: VatFormat) -> bool:
    """A row the lookup is for: no VAT value, Country/Region Key DE."""
    return fmt.status == MISSING and fmt.country_key in SCOPE_COUNTRIES


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def run_lookups(
    engine: LookupEngine,
    inputs: list[LookupInput | None],
) -> list[FoundResult | None]:
    """One lookup per Customer (per row when a row has no Customer), in input
    order, one at a time; the answer is fanned out to every row of it.
    ``None`` in, ``None`` out (row out of scope)."""
    by_key: dict[Any, FoundResult] = {}
    out: list[FoundResult | None] = []
    for index, inp in enumerate(inputs):
        if inp is None:
            out.append(None)
            continue
        key = ("customer", inp.customer) if inp.customer else ("row", index)
        if key not in by_key:
            by_key[key] = await engine.lookup(inp)
        out.append(by_key[key])
    return out
