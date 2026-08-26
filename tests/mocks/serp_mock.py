"""Mock SERP client for testing — returns curated search results.

Covers: faculty page found, directory found, no results, irrelevant results,
and name1-resolution queries (Tier 1B web search fallback).
"""

from __future__ import annotations

import logging

from search.base import SearchClient, SearchResult

logger = logging.getLogger(__name__)

# Curated results keyed by domain or institution name fragments
_MOCK_RESULTS: dict[str, list[dict[str, str]]] = {
    "mit.edu": [
        {
            "title": "Dr. Jane Smith | MIT Department of Chemistry",
            "url": "https://chemistry.mit.edu/profile/jane-smith/",
            "snippet": "Dr. Jane Smith is a Professor in the Department of Chemistry at MIT, specializing in NMR spectroscopy.",
        },
        {
            "title": "Faculty Directory | MIT",
            "url": "https://web.mit.edu/directory/faculty/",
            "snippet": "Complete faculty directory for Massachusetts Institute of Technology.",
        },
    ],
    "ucla.edu": [
        {
            "title": "Dr. John Doe | UCLA Chemistry",
            "url": "https://www.chemistry.ucla.edu/faculty/john-doe",
            "snippet": "Professor John Doe, Department of Chemistry and Biochemistry, UCLA. Research: organic synthesis.",
        },
    ],
    "stanford.edu": [
        {
            "title": "Dr. Alice Johnson | Stanford Chemistry",
            "url": "https://chemistry.stanford.edu/people/alice-johnson",
            "snippet": "Alice Johnson, PhD. Assistant Professor, Department of Chemistry. Stanford University.",
        },
        {
            "title": "Stanford School of Medicine — About Us",
            "url": "https://med.stanford.edu/about",
            "snippet": "Stanford School of Medicine is a world-renowned institution for medical research and education.",
        },
    ],
    "jhu.edu": [
        {
            "title": "Dr. Robert Chen | Johns Hopkins School of Medicine",
            "url": "https://www.hopkinsmedicine.org/profiles/details/robert-chen",
            "snippet": "Dr. Robert Chen, Department of Radiology, Johns Hopkins School of Medicine.",
        },
    ],
    "pfizer.com": [
        {
            "title": "Pfizer Analytical Sciences Division",
            "url": "https://www.pfizer.com/science/research/analytical-sciences",
            "snippet": "Pfizer's Analytical Sciences division provides support for drug development.",
        },
    ],
    "ufl.edu": [
        {
            "title": "NMR Facility — AMRIS | University of Florida",
            "url": "https://amris.ufl.edu/",
            "snippet": "Advanced Magnetic Resonance Imaging and Spectroscopy (AMRIS) NMR Facility at University of Florida, Biomolecular NMR Lab.",
        },
        {
            "title": "Department of Chemistry — University of Florida",
            "url": "https://chem.ufl.edu/",
            "snippet": "The Department of Chemistry at UF is home to the Biomolecular NMR Lab and NMR Facility.",
        },
    ],
}

# Name1 resolution results — for companies/orgs not in ROR
_NAME1_RESULTS: dict[str, list[dict[str, str]]] = {
    "comet therapeutics": [
        {
            "title": "Comet Therapeutics — Precision Medicine for Rare Diseases",
            "url": "https://www.comettherapeutics.com/",
            "snippet": "Comet Therapeutics is developing novel precision medicines for patients with rare diseases.",
        },
    ],
    "research lab inc": [
        {
            "title": "Research Lab Inc — Analytical Testing Services",
            "url": "https://www.researchlabinc.com/",
            "snippet": "Research Lab Inc provides analytical testing and quality control services.",
        },
    ],
    "thermo fisher scientific": [
        {
            "title": "Thermo Fisher Scientific — Official Site",
            "url": "https://www.thermofisher.com/",
            "snippet": "Thermo Fisher Scientific is the world leader in serving science.",
        },
    ],
}

# Generic fallback for any institution query
_GENERIC_RESULTS = [
    {
        "title": "Faculty Profile Page",
        "url": "https://www.example-university.edu/people/faculty/profile",
        "snippet": "Department of Sciences. Professor and researcher in analytical chemistry.",
    },
]


class MockSearchClient(SearchClient):
    """Mock search client returning deterministic results based on query content."""

    async def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        country: str | None = None,
    ) -> list[SearchResult]:
        """Return mock results based on domain, institution name, or org name.

        *country* is accepted to satisfy the `SearchClient` contract and
        ignored: the fixtures are keyed on query content, so honouring it would
        mean inventing per-country result sets the tests do not assert on.
        """
        query_lower = query.lower()
        results: list[dict[str, str]] = []

        # Try domain-based matching first
        for domain, domain_results in _MOCK_RESULTS.items():
            if domain in query_lower:
                results = domain_results
                break

        # Try institution-name matching (e.g. "University of Florida" → ufl.edu results)
        if not results:
            _INST_DOMAIN_MAP = {
                "university of florida": "ufl.edu",
                "univ of florida": "ufl.edu",
                "stanford": "stanford.edu",
                "mit": "mit.edu",
                "ucla": "ucla.edu",
                "johns hopkins": "jhu.edu",
                "pfizer": "pfizer.com",
            }
            for inst_name, domain_key in _INST_DOMAIN_MAP.items():
                if inst_name in query_lower and domain_key in _MOCK_RESULTS:
                    results = _MOCK_RESULTS[domain_key]
                    break

        # Try name1-resolution matching (for Tier 1B web search fallback)
        if not results:
            for name_key, name_results in _NAME1_RESULTS.items():
                if name_key in query_lower:
                    results = name_results
                    break

        # Fall back to generic results if nothing matched
        if not results:
            if "nonexistent" in query_lower or "zzz_no_match" in query_lower:
                logger.info("[MOCK] SERP: no results for '%s'", query[:80])
                return []
            results = _GENERIC_RESULTS

        logger.info("[MOCK] SERP: %d results for '%s'", len(results), query[:80])
        return [
            SearchResult(
                title=r["title"],
                url=r["url"],
                snippet=r["snippet"],
            )
            for r in results[:num_results]
        ]
