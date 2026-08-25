"""Mock ROR client for testing and local development without API access.

Updated to match the new RORClient.call(name, country_code) interface
that uses the ?query= parameter with country filtering.
"""

from __future__ import annotations

import logging
from typing import Any

from rapidfuzz import fuzz

from config import Settings
from enrichment.tier1_ror import ROR_RESEARCH_TYPES, RORClient

logger = logging.getLogger(__name__)

# Curated mock data keyed by lowercased name/alias substrings.
_MOCK_DATA: dict[str, dict[str, Any]] = {
    "massachusetts institute of technology": {
        "score": 0.97,
        "ror_id": "https://ror.org/042nb2s44",
        "official_name": "Massachusetts Institute of Technology",
        "domain": "mit.edu",
        "website": "https://web.mit.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["Massachusetts Institute of Technology", "MIT"],
        "children": [
            {"name": "MIT Lincoln Laboratory", "id": "https://ror.org/fakelincoln"},
            {"name": "MIT Media Lab", "id": "https://ror.org/fakemedia"},
            {"name": "Department of Chemistry", "id": "https://ror.org/fakechem"},
            {"name": "Department of Physics", "id": "https://ror.org/fakephys"},
            {"name": "Department of Electrical Engineering and Computer Science", "id": "https://ror.org/fakeeecs"},
            {"name": "Koch Institute for Integrative Cancer Research", "id": "https://ror.org/fakekoch"},
            {"name": "Department of Nuclear Science and Engineering", "id": "https://ror.org/fakenuc"},
        ],
    },
    "mit": {
        "score": 0.92,
        "ror_id": "https://ror.org/042nb2s44",
        "official_name": "Massachusetts Institute of Technology",
        "domain": "mit.edu",
        "website": "https://web.mit.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["Massachusetts Institute of Technology", "MIT"],
        "children": [
            {"name": "MIT Lincoln Laboratory", "id": "https://ror.org/fakelincoln"},
            {"name": "MIT Media Lab", "id": "https://ror.org/fakemedia"},
            {"name": "Department of Chemistry", "id": "https://ror.org/fakechem"},
            {"name": "Department of Physics", "id": "https://ror.org/fakephys"},
        ],
    },
    "ucla": {
        "score": 0.95,
        "ror_id": "https://ror.org/046rm7j60",
        "official_name": "University of California, Los Angeles",
        "domain": "ucla.edu",
        "website": "https://www.ucla.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["University of California, Los Angeles", "UCLA", "UC Los Angeles"],
        "children": [
            {"name": "Department of Chemistry and Biochemistry", "id": "https://ror.org/fakeuclachem"},
            {"name": "Department of Physics and Astronomy", "id": "https://ror.org/fakeuclaphys"},
            {"name": "David Geffen School of Medicine", "id": "https://ror.org/fakegeffen"},
            {"name": "Henry Samueli School of Engineering", "id": "https://ror.org/fakesamueli"},
        ],
    },
    "university of california los angeles": {
        "score": 0.99,
        "ror_id": "https://ror.org/046rm7j60",
        "official_name": "University of California, Los Angeles",
        "domain": "ucla.edu",
        "website": "https://www.ucla.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["University of California, Los Angeles", "UCLA", "UC Los Angeles"],
        "children": [
            {"name": "Department of Chemistry and Biochemistry", "id": "https://ror.org/fakeuclachem"},
            {"name": "Department of Physics and Astronomy", "id": "https://ror.org/fakeuclaphys"},
            {"name": "David Geffen School of Medicine", "id": "https://ror.org/fakegeffen"},
        ],
    },
    "stanford university": {
        "score": 0.98,
        "ror_id": "https://ror.org/00f54p054",
        "official_name": "Stanford University",
        "domain": "stanford.edu",
        "website": "https://www.stanford.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["Stanford University", "Leland Stanford Junior University"],
        "children": [
            {"name": "Department of Chemistry", "id": "https://ror.org/fakestanchem"},
            {"name": "School of Medicine", "id": "https://ror.org/fakestanmed"},
            {"name": "Department of Applied Physics", "id": "https://ror.org/fakestanphys"},
            {"name": "Stanford Synchrotron Radiation Lightsource", "id": "https://ror.org/fakessrl"},
        ],
    },
    "johns hopkins university": {
        "score": 0.96,
        "ror_id": "https://ror.org/00za53h95",
        "official_name": "Johns Hopkins University",
        "domain": "jhu.edu",
        "website": "https://www.jhu.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["Johns Hopkins University", "JHU"],
        "children": [
            {"name": "Department of Chemistry", "id": "https://ror.org/fakejhuchem"},
            {"name": "School of Medicine", "id": "https://ror.org/fakejhumed"},
            {"name": "Bloomberg School of Public Health", "id": "https://ror.org/fakejhubloom"},
        ],
    },
    "harvard university": {
        "score": 0.98,
        "ror_id": "https://ror.org/03vek6s52",
        "official_name": "Harvard University",
        "domain": "harvard.edu",
        "website": "https://www.harvard.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["Harvard University"],
        "children": [
            {"name": "Department of Chemistry", "id": "https://ror.org/fakeharvchem"},
            {"name": "Harvard Medical School", "id": "https://ror.org/fakeharvmed"},
        ],
    },
    # Both the abbreviated and full forms resolve to the SAME ROR id, so the
    # abbreviated input adopts ROR's fuller official name.
    "uni stuttgart": {
        "score": 0.99,
        "ror_id": "https://ror.org/04vnq7t77",
        "official_name": "University of Stuttgart",
        "domain": "uni-stuttgart.de",
        "website": "https://www.uni-stuttgart.de",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "Germany",
        "names": ["University of Stuttgart", "Universität Stuttgart", "Uni Stuttgart"],
        "children": [],
    },
    "university of stuttgart": {
        "score": 0.99,
        "ror_id": "https://ror.org/04vnq7t77",
        "official_name": "University of Stuttgart",
        "domain": "uni-stuttgart.de",
        "website": "https://www.uni-stuttgart.de",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "Germany",
        "names": ["University of Stuttgart", "Universität Stuttgart", "Uni Stuttgart"],
        "children": [],
    },
    "pfizer": {
        "score": 0.95,
        "ror_id": "https://ror.org/01xdqrp08",
        "official_name": "Pfizer (United States)",
        "domain": "pfizer.com",
        "website": "https://www.pfizer.com",
        "org_types": ["company"],
        "is_research_institution": False,
        "country": "United States",
        "names": ["Pfizer (United States)", "Pfizer Inc."],
        "children": [],
    },
    "novartis": {
        "score": 0.93,
        "ror_id": "https://ror.org/02f9zrr09",
        "official_name": "Novartis AG",
        "domain": "novartis.com",
        "website": "https://www.novartis.com",
        "org_types": ["company"],
        "is_research_institution": False,
        "country": "Switzerland",
        "names": ["Novartis AG", "Novartis"],
        "children": [],
    },
    "university of florida": {
        "score": 0.99,
        "ror_id": "https://ror.org/02y3ad647",
        "official_name": "University of Florida",
        "domain": "ufl.edu",
        "website": "https://www.ufl.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["University of Florida", "UF", "Univ of Florida"],
        "children": [
            {"name": "Advanced Magnetic Resonance Imaging and Spectroscopy Facility", "id": "https://ror.org/fakeuflnmr"},
            {"name": "Department of Chemistry", "id": "https://ror.org/fakeuflchem"},
            {"name": "McKnight Brain Institute", "id": "https://ror.org/fakeuflbrain"},
            {"name": "College of Medicine", "id": "https://ror.org/fakeuflmed"},
        ],
    },
    "univ of florida": {
        "score": 0.97,
        "ror_id": "https://ror.org/02y3ad647",
        "official_name": "University of Florida",
        "domain": "ufl.edu",
        "website": "https://www.ufl.edu",
        "org_types": ["education"],
        "is_research_institution": True,
        "country": "United States",
        "names": ["University of Florida", "UF", "Univ of Florida"],
        "children": [
            {"name": "Advanced Magnetic Resonance Imaging and Spectroscopy Facility", "id": "https://ror.org/fakeuflnmr"},
            {"name": "Department of Chemistry", "id": "https://ror.org/fakeuflchem"},
            {"name": "McKnight Brain Institute", "id": "https://ror.org/fakeuflbrain"},
            {"name": "College of Medicine", "id": "https://ror.org/fakeuflmed"},
        ],
    },
}


class MockRORClient(RORClient):
    """Mock ROR client that returns deterministic results from curated data.

    Matches the new call(name, country_code) interface.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def call(
        self,
        name: str,
        country_code: str | None = None,
        country: str | None = None,
        city: str | None = None,
        state: str | None = None,
        *,
        # Fix C(3) record context. Accepted and unused: a mock stands in for
        # the registry, not for the client-side guards that read it.
        record_domain: str | None = None,
    ) -> dict[str, Any]:
        """Return mock ROR data based on name matching.

        Tries exact key match first, then substring/containment match,
        then fuzzy match against all mock names.  Extra location params
        are accepted for interface compatibility but not used in matching.
        """
        name_lower = name.strip().lower()

        # Exact key match
        mock_data = _MOCK_DATA.get(name_lower)

        # Substring / containment match
        if mock_data is None:
            for key, data in _MOCK_DATA.items():
                if key in name_lower or name_lower in key:
                    mock_data = data
                    break

        # Fuzzy match against all mock names
        if mock_data is None:
            best_score = 0.0
            for _key, data in _MOCK_DATA.items():
                for alias in data.get("names", []):
                    ratio = fuzz.token_sort_ratio(name_lower, alias.lower())
                    if ratio > best_score:
                        best_score = ratio
                        if ratio >= 70:
                            mock_data = data

        if mock_data is None:
            logger.info("[MOCK] ROR: no match for '%s'", name[:80])
            return {"matched": False, "score": 0.0}

        threshold = float(self._settings.ror_confidence_threshold)
        score = mock_data["score"]

        if score < threshold:
            logger.info(
                "[MOCK] ROR: score %.2f below threshold %.2f for '%s'",
                score, threshold, name[:60],
            )
            return {"matched": False, "score": score}

        result = {
            "matched": True,
            "score": score,
            "ror_id": mock_data["ror_id"],
            "official_name": mock_data["official_name"],
            "org_types": mock_data["org_types"],
            "is_research_institution": mock_data["is_research_institution"],
            "domain": mock_data["domain"],
            "website": mock_data.get("website"),
            "children": mock_data.get("children", []),
            "country": mock_data.get("country"),
            "query_used": name,
            "country_filter": country_code,
        }

        logger.info(
            "[MOCK] ROR: '%s' → matched=%s, score=%.2f, name='%s'",
            name[:40], result["matched"], score, result["official_name"],
        )
        return result

    async def call_by_id(
        self,
        ror_id: str,
        country_code: str | None = None,
        *,
        country: str | None = None,
        city: str | None = None,
        state: str | None = None,
        record_domain: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a known ROR ID against the curated data.

        The by-identifier path the [Wikidata crosswalk
        lane](`enrichment.wikidata`) follows. Matched on the bare id so both
        ``https://ror.org/02y3ad647`` and ``02y3ad647`` resolve, and never
        scored — an identifier names one record, so there is no candidate set
        to rank. Unknown ids are a clean miss, which is what a stale pointer
        must produce.
        """
        bare = (ror_id or "").strip().rstrip("/").rsplit("/", 1)[-1].lower()
        if not bare:
            return {"matched": False, "score": 0.0, "guard_rejections": []}
        for data in _MOCK_DATA.values():
            if data["ror_id"].rsplit("/", 1)[-1].lower() != bare:
                continue
            logger.info("[MOCK] ROR by-id: %s → '%s'", bare, data["official_name"])
            return {
                "matched": True,
                "score": 1.0,
                "strategy": "by_id",
                "guard_rejections": [],
                "ror_id": data["ror_id"],
                "official_name": data["official_name"],
                "org_types": data["org_types"],
                "is_research_institution": data["is_research_institution"],
                "domain": data["domain"],
                "website": data.get("website"),
                "children": data.get("children", []),
                "country": data.get("country"),
                "query_used": bare,
                "country_filter": country_code,
            }
        logger.info("[MOCK] ROR by-id: %s not found", bare)
        return {"matched": False, "score": 0.0, "guard_rejections": []}
