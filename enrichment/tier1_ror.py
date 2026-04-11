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

from utils.text_utils import expand_abbreviations, extract_domain

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

    Strategy:
    1. Exact match against any variant → 1.0.
    2. Token-subset match: every significant (≥4-char) query token
       appears as a whole word in a variant → 1.0.  Allows "Stanford"
       to match "Stanford University".
    3. Length-guarded substring match (shorter side ≥60% of longer).
    4. Fuzz ratios (token_sort_ratio always; partial_ratio only when
       length-guarded, so tiny acronyms like "UNI" can't produce a
       false 1.0 against long unrelated names).

    Short acronym variants (≤4 chars) are excluded from fuzz scoring:
    they only contribute via exact-match in step 1.  This prevents
    ROR's acronym variants (e.g. "UNI" for University of Northern
    Iowa) from matching any query that happens to contain those
    letters.
    """
    query_lower = query.strip().lower()
    if not query_lower:
        return 0.0

    all_values = [n["value"].lower() for n in org_names if n.get("value")]

    # Step 1: exact match against any variant (acronyms allowed here)
    for val in all_values:
        if query_lower == val:
            return 1.0

    query_tokens = set(query_lower.split())
    significant_query_tokens = {t for t in query_tokens if len(t) >= 4}

    # Scoring excludes short acronym-like variants to avoid false positives
    scoring_values = [v for v in all_values if len(v) >= 5]
    if not scoring_values:
        return 0.0

    def _length_ok(a: str, b: str) -> bool:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        return longer > 0 and shorter / longer >= 0.6

    best = 0.0
    for val in scoring_values:
        val_tokens = set(val.split())

        # Step 2: all significant query tokens appear as whole words
        if significant_query_tokens and significant_query_tokens.issubset(val_tokens):
            return 1.0

        # Step 3: length-guarded substring
        if _length_ok(query_lower, val) and (query_lower in val or val in query_lower):
            return 1.0

        # Step 4a: token_sort_ratio (always safe — symmetric)
        token_ratio = fuzz.token_sort_ratio(query_lower, val) / 100.0
        if token_ratio > best:
            best = token_ratio

        # Step 4b: partial_ratio only when lengths are comparable
        if _length_ok(query_lower, val):
            partial = fuzz.partial_ratio(query_lower, val) / 100.0
            if partial > best:
                best = partial

    return best


def _score_org(query: str, org: dict[str, Any]) -> float:
    """Wrapper: extract org names and compute match score."""
    return _compute_name_score(query, org.get("names", []))


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

            # Score ALL items and pick the best. Expand abbreviations
            # in the query first so 'Stanford Uni' exact-matches
            # 'Stanford University' rather than tying with every other
            # variant that contains 'Stanford'. Prefer exact display
            # name match over mere token-subset matches.
            expanded_query = expand_abbreviations(name) or name
            exp_lower = expanded_query.strip().lower()

            def _rank_key(item: dict) -> tuple:
                s = _score_org(expanded_query, item)
                s2 = _score_org(name, item)
                score = max(s, s2)

                # Inspect all name variants for an EXACT match against
                # the expanded query — that's the strongest signal and
                # beats any token-subset score.
                exact_match = 0
                display_name = ""
                for ne in item.get("names", []):
                    val = (ne.get("value") or "").strip().lower()
                    if val == exp_lower:
                        exact_match = 1
                    if "ror_display" in ne.get("types", []):
                        display_name = ne.get("value") or ""
                if not display_name and item.get("names"):
                    display_name = item["names"][0].get("value", "")

                # Tiebreaker: prefer display name with closest token
                # count to the expanded query.
                token_diff = abs(
                    len(display_name.split()) - len(expanded_query.split())
                )

                # (exact_match desc, score desc, token_diff asc)
                return (exact_match, score, -token_diff)

            ranked = sorted(q_data["items"][:10], key=_rank_key, reverse=True)
            best_org = ranked[0] if ranked else None
            best_score = 0.0
            if best_org:
                best_score = max(
                    _score_org(expanded_query, best_org),
                    _score_org(name, best_org),
                )

            if best_org is None:
                return _no_match()

            org = best_org
            fields = _extract_org_fields(org)
            score = best_score

            if score < threshold:
                logger.info(
                    "ROR query: best score %.2f below threshold %.2f for '%s' (best: '%s')",
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
