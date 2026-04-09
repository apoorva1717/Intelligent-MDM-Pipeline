"""Tier 1: ROR v2 hybrid lookup — affiliation-first, query-fallback.

Primary strategy: use the ``?affiliation=`` parameter which handles
abbreviations (e.g. "Univ of Florida" → "University of Florida"),
misspellings, and real-world name variants.  It returns scored results
with a ``chosen`` flag.

Fallback: if the affiliation endpoint yields no confident match, retry
with the ``?query=`` parameter plus an optional country filter.  The
query endpoint uses Elasticsearch full-text search and works well for
exact/full names combined with geographic filtering.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from rapidfuzz import fuzz

from utils.text_utils import extract_domain

logger = logging.getLogger(__name__)

# Classification derived from ROR org types, not keyword matching.
ROR_RESEARCH_TYPES = {
    "education", "healthcare", "government",
    "facility", "nonprofit", "archive", "other",
}

# Module-level cache keyed by (name_lower, country_code).
_ror_cache: dict[tuple[str, str | None], dict[str, Any]] = {}


def clear_ror_cache() -> None:
    """Reset the module-level ROR cache (useful between test runs)."""
    _ror_cache.clear()


def _compute_name_score(query: str, org_names: list[dict[str, Any]]) -> float:
    """Score how well *query* matches any of the organisation's name variants.

    Returns a float 0.0–1.0.  An exact or substring match against any
    variant (including acronyms) yields 1.0; otherwise the best of
    ``token_sort_ratio`` and ``partial_ratio`` is returned — the latter
    handles abbreviations like "Univ" matching "University".
    """
    query_lower = query.strip().lower()
    all_values = [n["value"].lower() for n in org_names]

    for val in all_values:
        if query_lower == val or query_lower in val or val in query_lower:
            return 1.0

    best = 0.0
    for val in all_values:
        ratio = max(
            fuzz.token_sort_ratio(query_lower, val),
            fuzz.partial_ratio(query_lower, val),
        ) / 100.0
        if ratio > best:
            best = ratio
    return best


def _extract_org_fields(org: dict[str, Any]) -> dict[str, Any]:
    """Extract common fields (name, website, types, children, country) from an org dict."""
    org_names = org.get("names", [])

    display_name = None
    for name_entry in org_names:
        if "ror_display" in name_entry.get("types", []):
            display_name = name_entry["value"]
            break
    if not display_name and org_names:
        display_name = org_names[0]["value"]

    website = next(
        (link["value"] for link in org.get("links", [])
         if link.get("type") == "website"),
        None,
    )
    domain = extract_domain(website) if website else None

    org_types = [t.lower() for t in org.get("types", [])]
    is_research = any(t in ROR_RESEARCH_TYPES for t in org_types)

    children = [
        {"name": r["label"], "id": r["id"]}
        for r in org.get("relationships", [])
        if r.get("type", "").lower() == "child"
    ]

    country = None
    if org.get("locations"):
        country = (
            org["locations"][0]
            .get("geonames_details", {})
            .get("country_name")
        )

    return {
        "ror_id": org["id"],
        "official_name": display_name,
        "org_types": org_types,
        "is_research_institution": is_research,
        "domain": domain,
        "website": website,
        "children": children,
        "country": country,
        "org_names": org_names,
    }


async def call_ror(
    name: str,
    country_code: str | None = None,
    country: str | None = None,
    city: str | None = None,
    state: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Hybrid ROR lookup: affiliation-first with query-fallback.

    1. Try ``?affiliation=name, city, state, country`` — the affiliation
       endpoint handles abbreviations, misspellings, and uses geographic
       context to disambiguate (e.g. "University of Melbourne, Australia"
       vs. "University of Melbourne, FL").
    2. If no confident match, try ``?query=name`` with a country filter,
       scoring locally via ``_compute_name_score``.

    Parameters
    ----------
    name : str
        Organisation name (e.g. "Univ of Florida").
    country_code : str | None
        ISO alpha-2 code for the query-endpoint country filter.
    country : str | None
        Raw country text from the record (included in the affiliation string).
    city : str | None
        City from the record (included in the affiliation string).
    state : str | None
        State/province from the record (included in the affiliation string).
    """
    cache_key = (name.lower().strip(), country_code)
    if cache_key in _ror_cache:
        return _ror_cache[cache_key]

    if base_url is None:
        base_url = os.getenv("ROR_API_BASE", "https://api.ror.org/v2/organizations")

    threshold = float(os.getenv("ROR_CONFIDENCE_THRESHOLD", "0.8"))

    # Build a rich affiliation string: "name, city, state, country"
    aff_parts = [name]
    for part in (city, state, country):
        if part and part.strip():
            aff_parts.append(part.strip())
    affiliation_string = ", ".join(aff_parts)

    def _no_match(score: float = 0.0) -> dict[str, Any]:
        r: dict[str, Any] = {"matched": False, "score": score}
        _ror_cache[cache_key] = r
        return r

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ── Strategy A: affiliation endpoint with location context ──
            logger.info(
                "ROR affiliation request: '%s'", affiliation_string[:120],
            )
            resp_aff = await client.get(
                base_url, params={"affiliation": affiliation_string},
            )
            resp_aff.raise_for_status()
            aff_data = resp_aff.json()

            chosen = next(
                (it for it in aff_data.get("items", []) if it.get("chosen") is True),
                None,
            )

            if chosen and chosen.get("score", 0.0) >= threshold:
                org = chosen["organization"]
                fields = _extract_org_fields(org)
                score = chosen["score"]

                result: dict[str, Any] = {
                    "matched": True,
                    "score": score,
                    **{k: v for k, v in fields.items() if k != "org_names"},
                    "query_used": name,
                    "affiliation_used": affiliation_string,
                    "country_filter": country_code,
                    "strategy": "affiliation",
                }
                _ror_cache[cache_key] = result
                logger.info(
                    "ROR affiliation matched '%s' → '%s' (score=%.2f)",
                    affiliation_string[:80], fields["official_name"], score,
                )
                return result

            logger.info(
                "ROR affiliation no confident match for '%s' (chosen=%s, score=%.2f), trying query",
                affiliation_string[:80],
                chosen is not None,
                chosen["score"] if chosen else 0.0,
            )

            # ── Strategy B: query endpoint with country filter ─────────
            query_params: dict[str, str] = {"query": name}
            if country_code:
                query_params["filter"] = (
                    f"locations.geonames_details.country_code:{country_code}"
                )

            resp_q = await client.get(base_url, params=query_params)
            resp_q.raise_for_status()
            q_data = resp_q.json()

            # Retry without country filter if empty
            if not q_data.get("items") and country_code:
                logger.info(
                    "ROR query returned 0 items for '%s' with country=%s, retrying without filter",
                    name[:60], country_code,
                )
                resp_q = await client.get(base_url, params={"query": name})
                resp_q.raise_for_status()
                q_data = resp_q.json()

            if not q_data.get("items"):
                logger.info("ROR: 0 items for '%s' across both strategies", name[:80])
                return _no_match()

            org = q_data["items"][0]
            fields = _extract_org_fields(org)
            score = _compute_name_score(name, fields["org_names"])

            if score < threshold:
                logger.info(
                    "ROR query: score %.2f below threshold %.2f for '%s' (top: '%s')",
                    score, threshold, name[:60],
                    fields["official_name"] or "?",
                )
                return _no_match(score)

            result = {
                "matched": True,
                "score": score,
                **{k: v for k, v in fields.items() if k != "org_names"},
                "query_used": name,
                "country_filter": country_code,
                "strategy": "query",
            }
            _ror_cache[cache_key] = result
            logger.info(
                "ROR query matched '%s' → '%s' (score=%.2f)",
                name[:60], fields["official_name"], score,
            )
            return result

    except httpx.HTTPStatusError as exc:
        logger.error(
            "ROR API HTTP %d for '%s': %s",
            exc.response.status_code, name[:80], exc.response.text[:200],
        )
        return _no_match()
    except Exception:
        logger.exception("ROR API call failed for '%s'", name[:80])
        return _no_match()


class RORClient:
    """Thin wrapper around call_ror() for dependency injection and mocking."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._base_url = settings.ror_api_base

    async def call(
        self,
        name: str,
        country_code: str | None = None,
        country: str | None = None,
        city: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Look up an organisation name via ROR with location context."""
        return await call_ror(
            name,
            country_code=country_code,
            country=country,
            city=city,
            state=state,
            base_url=self._base_url,
        )
