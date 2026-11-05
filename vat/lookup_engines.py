"""``lookup_missing`` → a lookup engine. One place, used by the endpoints, the
runner and ``tools/vat_lookup_compare.py``.

``llm_web`` (the default on the endpoints and the runner) is the model
searching the web (``vat/lookup_web.py``). ``none`` returns no engine, and
the output is exactly what it was before the lookups existed. ``llm_only`` is
the Mode B memory-recall **experiment** (``vat/lookup_llm_only.py``): never a
production path; the endpoints map it to ``llm_web`` (``api/vat_routes.py``).

``grounded`` (SerpAPI + page read) is off unless ``VAT_GROUNDED_FALLBACK`` is
set: asking for it raises :class:`GroundedDisabled`. With the flag set it can
be asked for, and ``llm_web`` falls back to it for a customer whose web search
could not run (:class:`WithGroundedFallback`) and, in the "hybrid" query mode,
once with search 1 for a customer still NOT_FOUND (``vat/lookup_web.py``).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from vat.lookup import (
    GROUNDED, LLM_ONLY, LLM_WEB, LOOKUP_INCOMPLETE, MODES, NONE, FoundResult, LookupEngine, LookupInput,
)
from vat.lookup_grounded import LLM_UNAVAILABLE, GroundedLookup
from vat.lookup_llm_only import LlmOnlyLookup
from vat.lookup_web import WebSearchLookup
from vat.vies_client import VIESClient

#: The llm_web search could not run for this customer; the grounded lookup answered.
GROUNDED_FALLBACK = "GROUNDED_FALLBACK"


class GroundedDisabled(ValueError):
    """The grounded lookup was asked for while VAT_GROUNDED_FALLBACK is off."""


def grounded_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "vat_grounded_fallback", False))


class WithGroundedFallback:
    """``llm_web`` first; the grounded lookup only when the model's web search
    could not run at all (``LOOKUP_INCOMPLETE`` / ``LLM_UNAVAILABLE``). A
    ``NOT_FOUND`` or a dropped answer is final: this covers outages, it is not
    a second opinion. The grounded reader uses the same LLM provider (a plain
    call, no web search tool), so a full provider outage stays incomplete."""

    def __init__(self, primary: LookupEngine, fallback: LookupEngine) -> None:
        self._primary = primary
        self._fallback = fallback
        self.mode = primary.mode
        self._fallbacks = 0

    @property
    def agent_version(self) -> str:
        return self._primary.agent_version

    def __getattr__(self, name: str) -> Any:
        # The primary's run records (usage_log, meter) pass through.
        if name in ("usage_log", "meter"):
            return getattr(self._primary, name)
        raise AttributeError(name)

    async def lookup(self, inp: LookupInput) -> FoundResult:
        result = await self._primary.lookup(inp)
        if result.status != LOOKUP_INCOMPLETE or LLM_UNAVAILABLE not in result.flags:
            return result
        self._fallbacks += 1
        backup = await self._fallback.lookup(inp)
        return replace(backup, flags=(*backup.flags, GROUNDED_FALLBACK, LLM_UNAVAILABLE),
                       agent_version=self._fallback.agent_version)

    def stats(self) -> dict[str, int]:
        out = dict(self._primary.stats())
        out["grounded_fallbacks"] = self._fallbacks
        out.update({f"fallback_{k}": v for k, v in self._fallback.stats().items()})
        return out

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()


def make_lookup(mode: str, settings: Any, vies: VIESClient) -> LookupEngine | None:
    mode = (mode or NONE).strip().lower()
    if mode == NONE:
        return None
    if mode == GROUNDED:
        if not grounded_enabled(settings):
            raise GroundedDisabled(
                "The grounded lookup (SerpAPI) is off; set VAT_GROUNDED_FALLBACK=true to run it.")
        return GroundedLookup(settings, vies)
    if mode == LLM_ONLY:
        return LlmOnlyLookup(settings, vies)
    if mode == LLM_WEB:
        if grounded_enabled(settings):
            # The same engine for both: "hybrid" runs it once with search 1
            # for an entity still NOT_FOUND; the wrapper when the search failed.
            grounded = GroundedLookup(settings, vies)
            return WithGroundedFallback(WebSearchLookup(settings, vies, grounded=grounded), grounded)
        return WebSearchLookup(settings, vies)
    raise ValueError(f"lookup_missing must be one of {', '.join(MODES)}, not {mode!r}")
