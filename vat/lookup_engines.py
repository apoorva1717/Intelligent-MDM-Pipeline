"""``lookup_missing`` → a lookup engine. One place, used by the endpoints, the
runner and ``tools/vat_lookup_compare.py``.

``none`` (the default everywhere) returns no engine, and the output is exactly
what it was before the lookups existed. ``llm_web`` is the model searching
the web (``vat/lookup_web.py``). ``llm_only`` is the Mode B memory-recall
**experiment** (``vat/lookup_llm_only.py``): never a production path; the
endpoints map it to ``llm_web`` (``api/vat_routes.py``).
"""

from __future__ import annotations

from typing import Any

from vat.lookup import GROUNDED, LLM_ONLY, LLM_WEB, MODES, NONE, LookupEngine
from vat.lookup_grounded import GroundedLookup
from vat.lookup_llm_only import LlmOnlyLookup
from vat.lookup_web import WebSearchLookup
from vat.vies_client import VIESClient


def make_lookup(mode: str, settings: Any, vies: VIESClient) -> LookupEngine | None:
    mode = (mode or NONE).strip().lower()
    if mode == NONE:
        return None
    if mode == GROUNDED:
        return GroundedLookup(settings, vies)
    if mode == LLM_ONLY:
        return LlmOnlyLookup(settings, vies)
    if mode == LLM_WEB:
        return WebSearchLookup(settings, vies)
    raise ValueError(f"lookup_missing must be one of {', '.join(MODES)}, not {mode!r}")
