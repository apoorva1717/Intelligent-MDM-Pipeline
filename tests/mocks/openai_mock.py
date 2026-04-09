"""Mock Azure OpenAI client for testing — returns deterministic JSON responses.

Covers: high confidence extraction, medium confidence, person not found,
malformed JSON handling.

Uses curated lookup tables rather than heuristic text parsing, so outputs
are clean and realistic — matching what gpt-4o would return.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Curated extraction results keyed by (contact_fragment, institution_fragment).
# Simulates what gpt-4o would extract from each mock page.
_KNOWN_AFFILIATIONS: dict[str, dict[str, Any]] = {
    "jane smith|mit": {
        "person_found": True,
        "official_dept": "Department of Chemistry",
        "official_group": "NMR Spectroscopy Group",
        "title": "Professor of Chemistry",
        "confidence": "high",
    },
    "jane smith|massachusetts institute of technology": {
        "person_found": True,
        "official_dept": "Department of Chemistry",
        "official_group": "NMR Spectroscopy Group",
        "title": "Professor of Chemistry",
        "confidence": "high",
    },
    "john doe|ucla": {
        "person_found": True,
        "official_dept": "Department of Chemistry and Biochemistry",
        "official_group": "Organic Chemistry Research Group",
        "title": "Professor",
        "confidence": "high",
    },
    "john doe|university of california": {
        "person_found": True,
        "official_dept": "Department of Chemistry and Biochemistry",
        "official_group": "Organic Chemistry Research Group",
        "title": "Professor",
        "confidence": "high",
    },
    "alice johnson|stanford": {
        "person_found": True,
        "official_dept": "Department of Chemistry",
        "official_group": "Analytical Chemistry Group",
        "title": "Assistant Professor",
        "confidence": "high",
    },
    "robert chen|johns hopkins": {
        "person_found": True,
        "official_dept": "Department of Radiology",
        "official_group": "MRI Research Group",
        "title": "Professor of Radiology",
        "confidence": "high",
    },
}

# Curated department results for Tier 2B, keyed by institution fragment
_KNOWN_DEPARTMENTS: dict[str, dict[str, Any]] = {
    "mit": {"official_name": "Department of Chemistry", "confidence": "medium"},
    "massachusetts institute of technology": {"official_name": "Department of Chemistry", "confidence": "medium"},
    "massachusetts institute": {"official_name": "Department of Chemistry", "confidence": "medium"},
    "ucla": {"official_name": "Department of Chemistry and Biochemistry", "confidence": "medium"},
    "university of california": {"official_name": "Department of Chemistry and Biochemistry", "confidence": "medium"},
    "university of california los angeles": {"official_name": "Department of Chemistry and Biochemistry", "confidence": "medium"},
    "stanford": {"official_name": "Department of Chemistry", "confidence": "medium"},
    "harvard": {"official_name": "Department of Chemistry and Chemical Biology", "confidence": "medium"},
    "johns hopkins": {"official_name": "Department of Radiology", "confidence": "medium"},
    "pfizer": {"official_name": "Analytical Sciences Division", "confidence": "medium"},
    "novartis": {"official_name": "Research and Development", "confidence": "medium"},
}


class MockOpenAIClient:
    """Mock that returns curated, clean JSON responses matching real LLM output.

    Uses lookup tables keyed by contact/institution names rather than
    heuristic text parsing, producing realistic department and group names.
    """

    async def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Generate a mock LLM response based on prompt analysis."""
        prompt_lower = user_prompt.lower()

        if "extract affiliation for:" in prompt_lower:
            return self._mock_tier2a(user_prompt, prompt_lower)
        elif "extract the official department" in prompt_lower:
            return self._mock_tier2b(user_prompt, prompt_lower)
        elif "infer official org and dept" in prompt_lower:
            return self._mock_tier3(user_prompt, prompt_lower)

        logger.warning("[MOCK] OpenAI: unrecognised prompt structure, returning generic response")
        return {"confidence": "low", "reasoning": "Mock fallback — unrecognised prompt"}

    def _mock_tier2a(self, user_prompt: str, prompt_lower: str) -> dict[str, Any]:
        """Mock Tier 2A contact affiliation extraction."""
        contact = self._extract_field(user_prompt, "Extract affiliation for:")
        institution = self._extract_field(user_prompt, "Institution:")
        existing_name2 = self._extract_field(user_prompt, "Existing Name 2:")

        # Look up curated affiliation by contact + institution
        affiliation = self._find_affiliation(contact, institution)

        if affiliation is None:
            return {
                "person_found": False,
                "official_dept": None,
                "official_group": None,
                "title": None,
                "name2_match": "unknown",
                "name2_match_score": 0,
                "confidence": "low",
                "reasoning": "Person not found on page",
            }

        dept = affiliation["official_dept"]
        group = affiliation["official_group"]

        # Determine match against existing Name 2
        match_result, match_score = self._compute_name2_match(existing_name2, dept)

        return {
            "person_found": True,
            "official_dept": dept,
            "official_group": group,
            "title": affiliation.get("title"),
            "name2_match": match_result,
            "name2_match_score": match_score,
            "confidence": affiliation["confidence"],
            "reasoning": f"Found {contact} affiliated with {dept}",
        }

    def _mock_tier2b(self, user_prompt: str, prompt_lower: str) -> dict[str, Any]:
        """Mock Tier 2B department extraction."""
        name1 = self._extract_field(user_prompt, "Organisation:")
        existing_name2 = self._extract_field(user_prompt, "Existing Name 2:")

        dept_info = self._find_department(name1)

        if dept_info is None:
            return {
                "official_name": None,
                "name2_match": "unknown",
                "name2_match_score": 0,
                "confidence": "low",
                "reasoning": "No department found on page",
            }

        match_result, match_score = self._compute_name2_match(
            existing_name2, dept_info["official_name"],
        )

        return {
            "official_name": dept_info["official_name"],
            "name2_match": match_result,
            "name2_match_score": match_score,
            "confidence": dept_info["confidence"],
            "reasoning": f"Found department: {dept_info['official_name']}",
        }

    def _mock_tier3(self, user_prompt: str, prompt_lower: str) -> dict[str, Any]:
        """Mock Tier 3 LLM inference."""
        name1 = self._extract_field(user_prompt, "Name 1:")

        if name1 and name1 != "not recorded" and len(name1) > 3:
            return {
                "name1_suggestion": name1,
                "name2_suggestion": "General Research Division",
                "name3_suggestion": None,
                "confidence": "medium",
                "reasoning": "Inferred from available address and name information",
                "requires_verification": True,
            }

        return {
            "name1_suggestion": None,
            "name2_suggestion": None,
            "name3_suggestion": None,
            "confidence": "low",
            "reasoning": "Insufficient information for inference",
            "requires_verification": True,
        }

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _extract_field(prompt: str, label: str) -> str:
        """Extract the value after a label line in the prompt."""
        for line in prompt.split("\n"):
            if line.strip().startswith(label):
                return line.split(label, 1)[-1].strip()
        return ""

    @staticmethod
    def _find_affiliation(contact: str, institution: str) -> dict[str, Any] | None:
        """Look up a curated affiliation by contact + institution name fragments."""
        contact_lower = (
            contact.lower()
            .replace("dr.", "").replace("prof.", "")
            .strip()
        )
        inst_lower = institution.lower()

        for key, data in _KNOWN_AFFILIATIONS.items():
            name_part, inst_part = key.split("|")
            if name_part in contact_lower and inst_part in inst_lower:
                return data

        return None

    @staticmethod
    def _find_department(name1: str) -> dict[str, Any] | None:
        """Look up a curated department by institution name fragment."""
        name1_lower = name1.lower()
        for key, data in _KNOWN_DEPARTMENTS.items():
            if key in name1_lower or name1_lower in key:
                return data
        return None

    @staticmethod
    def _compute_name2_match(
        existing_name2: str, official_dept: str,
    ) -> tuple[str, int]:
        """Compare existing Name 2 against the official department name."""
        if not existing_name2 or existing_name2 == "not recorded":
            return "unknown", 0

        existing_lower = existing_name2.lower().strip()
        official_lower = official_dept.lower().strip()

        if existing_lower == official_lower:
            return "exact", 100

        # Check if one contains the other (near-exact)
        if existing_lower in official_lower or official_lower in existing_lower:
            return "exact", 95

        # Check for shared key words (partial match)
        existing_words = set(existing_lower.split()) - {"of", "the", "and", "for", "in", "dept"}
        official_words = set(official_lower.split()) - {"of", "the", "and", "for", "in", "department"}
        overlap = existing_words & official_words

        if overlap and len(overlap) >= len(existing_words) * 0.5:
            return "partial", 70

        return "no_match", 20