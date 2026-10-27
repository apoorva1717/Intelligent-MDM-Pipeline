"""Mode B — LLM_ONLY missing-VAT lookup.

========================================================================
EXPERIMENT ONLY — NOT FOR PRODUCTION USE.
Results are NOT to be written to SAP, to MDM, or to any steward queue.
========================================================================

This mode exists for one reason: to **measure how unreliable** it is to ask a
language model for a company's VAT ID from memory. It asks the model — with no
search, no fetched page and no tools — for the German USt-IdNr. of a company
given only its SAP name and address, then runs whatever comes back through the
offline format check and VIES.

It is deliberately never "verified":

* the status is always ``LLM_GUESS:<format_status>:<vies_status>``
  (``LLM_GUESS:FORMAT_OK:VALID`` included — a number that passes the checksum
  and that VIES calls VALID can still belong to a different company, and for a
  German number VIES cannot tell: it discloses no name and ignores trader
  fields);
* every row carries the flag ``EXPERIMENT_LLM_ONLY``;
* there is no evidence: ``VAT_Found_Source_URL`` is empty and
  ``VAT_Found_Evidence`` holds only the model's self-reported confidence.

Mode A (``vat/lookup_grounded.py``) is the production-shaped method; this mode
is its control group. ``tools/vat_lookup_compare.py`` runs both against a file
with known VAT IDs.
"""

from __future__ import annotations

from typing import Any

from llm.openai_client import LLMUnavailableFrozen
from vat.llm_cache import VatLLM
from vat.lookup import LLM_ONLY, LOOKUP_INCOMPLETE, FoundResult, LookupInput, today
from vat.normalize import FORMAT_OK, assess_format
from vat.prompts import LLM_ONLY_SYSTEM_PROMPT, LLM_ONLY_USER_PROMPT_TEMPLATE
from vat.validate import NOT_CHECKED
from vat.vies_client import VIESClient

#: Every row this mode produces carries this flag.
EXPERIMENT_FLAG = "EXPERIMENT_LLM_ONLY"
#: Status prefix; the status is never FOUND_VERIFIED.
LLM_GUESS = "LLM_GUESS"
CONFIDENCES = ("high", "medium", "low")


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text.lower() not in ("null", "none", "unknown") else None


class LlmOnlyLookup:
    """Mode B. EXPERIMENT ONLY (see module docstring)."""

    mode = LLM_ONLY

    def __init__(self, settings: Any, vies: VIESClient, *, llm: VatLLM | None = None) -> None:
        self._vies = vies
        self._llm = llm or VatLLM(vies.evidence_cache.namespace("vat_llm"))
        self._llm.write_failures = vies.write_failures
        self._llm_asks = 0
        self._vies_checks = 0

    def stats(self) -> dict[str, int]:
        return {
            "llm_asks": self._llm_asks,
            "llm_network_calls": self._llm.network_calls,
            "llm_cache_hits": self._llm.cache_hits,
            "vies_checks": self._vies_checks,
            "cache_write_failures": self._vies.write_failures.count,
        }

    async def aclose(self) -> None:
        await self._llm.aclose()

    @staticmethod
    def prompt(inp: LookupInput) -> str:
        names = list(inp.names) + [None] * (4 - len(inp.names))
        street = " ".join(p for p in (inp.street, inp.house_number) if p)
        return LLM_ONLY_USER_PROMPT_TEMPLATE.format(
            name_1=names[0] or "", name_2=names[1] or "", name_3=names[2] or "",
            name_4=names[3] or "", street=street, postal_code=inp.postal_code or "",
            city=inp.city or "", country=inp.country or "",
        )

    async def lookup(self, inp: LookupInput) -> FoundResult:
        self._llm_asks += 1
        try:
            # Reasoning models (gpt-6-sol) count their reasoning against this
            # cap: at 200 they spent all of it thinking and returned "".
            raw = await self._llm.extract_json(
                LLM_ONLY_SYSTEM_PROMPT, self.prompt(inp), max_tokens=4000,
            )
        except LLMUnavailableFrozen:
            return FoundResult(LLM_ONLY, LOOKUP_INCOMPLETE,
                               flags=(EXPERIMENT_FLAG, "FROZEN_CACHE_MISS"))
        except (RuntimeError, ValueError):
            return FoundResult(LLM_ONLY, LOOKUP_INCOMPLETE,
                               flags=(EXPERIMENT_FLAG, "LLM_UNAVAILABLE"))

        raw = raw if isinstance(raw, dict) else {}
        guess = _clean(raw.get("vat_id"))
        confidence = (_clean(raw.get("confidence")) or "").lower() or None
        if confidence not in CONFIDENCES:
            confidence = f"unstated:{confidence}" if confidence else None

        fmt = assess_format(guess, inp.country)
        flags = [EXPERIMENT_FLAG]
        if not guess:
            flags.append("LLM_NO_ANSWER")
        if fmt.format_error:
            flags.append(f"FORMAT_ERROR:{fmt.format_error}")
        flags += [f for f in fmt.flags]

        vies_status = NOT_CHECKED
        if fmt.status == FORMAT_OK and self._vies.enabled:
            self._vies_checks += 1
            vies = await self._vies.check(fmt)
            vies_status = vies.status
            if vies.error_code:
                flags.append(f"VIES:{vies.error_code}")

        return FoundResult(
            LLM_ONLY,
            f"{LLM_GUESS}:{fmt.status}:{vies_status}",
            value=fmt.normalized or guess,
            source_url=None,
            attributed_to=None,
            evidence=confidence,
            date=self._llm.last_fetched_at or today(),
            flags=tuple(flags),
        )
