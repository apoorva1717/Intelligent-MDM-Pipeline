"""UC 13 — Lab / research group / centre → parent department resolution.

When the input ``Name2`` names a granular unit (lab, research group,
centre, core, or facility) it is too low in the hierarchy for MDM
purposes. The institution's own website almost always documents the
PARENT academic department for such units in URL breadcrumbs, the
page title, or the breadcrumb navigation.

This module performs:
    1. SERP search for the lab name on the institution's domain.
    2. Page fetch of the top on-domain candidates.
    3. LLM extraction of the parent department from authoritative
       structured page elements (URL path / title / H1 / breadcrumb).

If a parent department is found, the orchestrator promotes it into
``Name2`` and shifts the original lab name into ``Name3`` (when
``Name3`` is empty). On failure, the existing pipeline behaviour is
preserved (granular names pass through as-is and may be flagged).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Settings
from llm.openai_client import OpenAIClient
from llm.prompts import (
    LAB_PARENT_SYSTEM_PROMPT,
    LAB_PARENT_USER_PROMPT_TEMPLATE,
)
from search.base import SearchClient, SearchResult
from search.page_fetcher import PageFetcher
from utils.cache import BatchCache

logger = logging.getLogger(__name__)


@dataclass
class LabResolverResult:
    success: bool = False
    parent_department: str | None = None
    confidence: str = "none"
    source_url: str | None = None
    reasoning: str = ""


async def run_lab_resolver(
    record_id: str,
    institution: str,
    lab_name: str,
    domain: str | None,
    search_client: SearchClient,
    page_fetcher: PageFetcher,
    llm_client: OpenAIClient,
    cache: BatchCache,
    settings: Settings,
) -> LabResolverResult:
    """Resolve *lab_name* to its parent academic department on
    *institution*'s website."""
    result = LabResolverResult()

    if not lab_name or not institution:
        return result

    # Bias the query toward department/parent-unit pages by adding the
    # word "department" — these are exactly the pages that link to the
    # lab from above and tend to surface the parent name in
    # breadcrumb/title.
    if domain:
        # On-domain query — institution's own wording is best.
        query = f'"{lab_name}" department site:{domain}'
    else:
        # Off-domain fallback — used when ROR didn't supply a website
        # and Path B/C hasn't resolved one yet. Lower precision, but
        # still useful when the institution name is distinctive.
        query = f'"{institution}" "{lab_name}" department'

    cached = cache.get_serp(query)
    if cached is not None:
        results: list[SearchResult] = cached
    else:
        results = await search_client.search(query, num_results=5)
        cache.set_serp(query, results)

    if domain:
        candidates = [r for r in results if domain in r.url.lower()]
    else:
        # No known domain — accept whatever SERP returned, ranked by
        # original order. The LLM extraction step is the final gate.
        candidates = list(results)

    if not candidates:
        logger.info(
            "[%s] Lab resolver: no usable results for query=%r",
            record_id, query,
        )
        return result

    extractions: list[dict] = []
    for candidate in candidates[:3]:
        page_content = await page_fetcher.fetch_page_content(candidate.url)
        if page_content is None or page_content.is_empty():
            continue

        try:
            user_prompt = LAB_PARENT_USER_PROMPT_TEMPLATE.format(
                name1=institution,
                lab_name=lab_name,
                url_path=page_content.url_path or "(none)",
                page_title=page_content.page_title or "(none)",
                h1=page_content.h1 or "(none)",
                breadcrumb=page_content.breadcrumb or "(none)",
            )
            extraction = await llm_client.extract_json(
                LAB_PARENT_SYSTEM_PROMPT, user_prompt,
            )
        except Exception as exc:
            logger.info(
                "[%s] Lab resolver: LLM extraction failed for %s: %s",
                record_id, candidate.url[:80], exc,
            )
            continue

        parent = extraction.get("parent_department")
        if not parent or not str(parent).strip():
            continue
        cleaned = str(parent).strip()
        if cleaned.lower() in {"null", "none", "n/a", "na"}:
            continue

        extractions.append({
            "parent_department": cleaned,
            "confidence": (extraction.get("confidence") or "low").lower(),
            "reasoning": extraction.get("reasoning", ""),
            "url": candidate.url,
        })

    if not extractions:
        logger.info(
            "[%s] Lab resolver: no parent department extracted for %r",
            record_id, lab_name,
        )
        return result

    # Prefer high > medium > low; ties broken by longer parent name
    # (more specific) and then alphabetical for stability.
    confidence_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    extractions.sort(
        key=lambda e: (
            confidence_rank.get(e["confidence"], 0),
            len(e["parent_department"]),
            e["parent_department"].lower(),
        ),
        reverse=True,
    )
    best = extractions[0]

    result.success = True
    result.parent_department = best["parent_department"]
    result.confidence = best["confidence"]
    result.source_url = best["url"]
    result.reasoning = best["reasoning"]

    logger.info(
        "[%s] Lab resolver: %r → parent %r (confidence=%s, url=%s)",
        record_id, lab_name, result.parent_department,
        result.confidence, result.source_url[:80],
    )
    return result
