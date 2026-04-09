"""Record classification — REMOVED (Bug 1 fix).

Classification is now derived from the ROR API response org types,
not from keyword matching on name1. See enrichment/tier1_ror.py
and enrichment/orchestrator.py for the new approach.

ROR_RESEARCH_TYPES = {"education", "healthcare", "government",
                      "facility", "nonprofit", "archive", "other"}

- ROR matched AND org type in ROR_RESEARCH_TYPES → research_institution
- ROR matched AND org type not in ROR_RESEARCH_TYPES → company
- ROR did not match → unknown
"""
