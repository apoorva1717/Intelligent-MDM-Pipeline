"""Person-affiliation lookup (Stage 2b).

When Name 1 held ONLY a person's name (moved to Contact by UC 7, leaving Name 1
empty), the normal tiers have no organisation to enrich. This module runs a
single grounded web lookup on the person to *propose* their institution +
department so the record is not left with just a contact and no organisation.

It only proposes a candidate from the SERP snippets — it never fabricates.
Reliability is enforced by the CALLER (the orchestrator), which:

  * confirms the proposed institution against ROR in the record's country
    (this rejects wrong-country matches, e.g. an Irish university for a GB
    address),
  * uses ROR's official name + domain — never a guessed one, and
  * always short-circuits person records so Tier 3 cannot fabricate/overwrite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Settings
from llm.openai_client import OpenAIClient
from llm.prompts import (
    PERSON_AFFILIATION_SYSTEM_PROMPT,
    PERSON_AFFILIATION_USER_PROMPT_TEMPLATE,
)
from search.base import SearchClient
from utils.cache import BatchCache, cached_serp

logger = logging.getLogger(__name__)


# Free/consumer email domains carry no organisation signal — do not query on them.
_FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "qq.com",
    "163.com", "126.com", "yandex.com", "mail.com", "zoho.com",
}


@dataclass
class PersonAffiliation:
    """A proposed affiliation. institution is None when nothing could be found."""
    institution: str | None = None
    department: str | None = None
    confidence: str = "low"  # "high" | "medium" | "low"
    source_url: str | None = None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or v.lower() in {"null", "none", "n/a", "unknown", "not provided"}:
        return None
    return v


def _email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    dom = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return dom or None


def _location(city: str | None, region: str | None, country: str | None) -> str:
    return ", ".join(p.strip() for p in (city, region, country) if p and p.strip())


def _build_queries(
    contact: str,
    city: str | None,
    region: str | None,
    country: str | None,
    email: str | None,
) -> list[str]:
    """Ordered, de-duplicated SERP queries — most specific first."""
    loc = _location(city, region, country)
    queries: list[str] = []
    dom = _email_domain(email)
    if dom and dom not in _FREEMAIL:
        # A corporate/edu email domain is the strongest disambiguator.
        queries.append(f'"{contact}" {dom}')
    if loc:
        queries.append(f'"{contact}" {loc}')
    queries.append(f'"{contact}" university OR institute OR company OR hospital')
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


async def run_person_affiliation(
    *,
    contact: str,
    city: str | None,
    region: str | None,
    country: str | None,
    email: str | None,
    search_client: SearchClient,
    llm_client: OpenAIClient,
    settings: Settings,
    cache: BatchCache | None = None,
) -> PersonAffiliation:
    """Propose the contact's institution + department from web snippets.

    Returns an empty ``PersonAffiliation`` (institution=None) when no search
    result ties the person to an organisation, or on any search/LLM error.
    Never raises.
    """
    if not contact or not contact.strip():
        return PersonAffiliation()

    queries = _build_queries(contact, city, region, country, email)
    results = []
    used_query = None
    for q in queries:
        try:
            # Fix B — through the shared cache like every other lane. This one
            # called the search client directly, so its results were never
            # recorded and a second run of a batch re-issued every query.
            hits = await cached_serp(cache, search_client, q, num_results=5)
        except Exception:
            logger.exception("person_affiliation: SERP failed for %r", q)
            continue
        if hits:
            results = hits
            used_query = q
            break

    if not results:
        logger.info("person_affiliation: no search results for %r", contact)
        return PersonAffiliation()

    # Fix A(3) — the snippet list is evidence injected into a prompt, so it is
    # ordered by a stable key of its own (the URL) rather than by whatever
    # order the search API happened to return. Two runs that retrieve the same
    # five results in a different order previously built two different prompts
    # and could get two different answers; now they build one prompt.
    results = sorted(results[:5], key=lambda r: ((r.url or ""), (r.title or "")))
    blob = "\n\n".join(
        f"[{i + 1}] {r.title}\nURL: {r.url}\n{r.snippet}"
        for i, r in enumerate(results)
    )
    user_prompt = PERSON_AFFILIATION_USER_PROMPT_TEMPLATE.format(
        contact=contact,
        location=_location(city, region, country) or "not provided",
        results=blob,
    )

    try:
        data = await llm_client.extract_json(
            PERSON_AFFILIATION_SYSTEM_PROMPT, user_prompt,
        )
    except Exception:
        logger.exception("person_affiliation: LLM extraction failed for %r", contact)
        return PersonAffiliation()

    institution = _clean(data.get("institution"))
    department = _clean(data.get("department"))
    confidence = (data.get("confidence") or "low")
    confidence = confidence.strip().lower() if isinstance(confidence, str) else "low"
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    if not institution:
        return PersonAffiliation(confidence=confidence)

    logger.info({
        "step": "person_affiliation",
        "contact": contact,
        "query": used_query,
        "institution": institution,
        "department": department,
        "confidence": confidence,
    })
    return PersonAffiliation(
        institution=institution,
        department=department,
        confidence=confidence,
        source_url=results[0].url if results else None,
    )
