"""Identity guard: canonicalisation must not swap in a different company."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from utils.text_utils import canonical_preserves_identity


@pytest.mark.parametrize("original, canonical", [
    ("Iso Group Inc", "ISO Group, Inc."),          # reformatting / casing
    ("Iso Group Inc", "Iso Group, Incorporated"),  # suffix expansion
    ("Pfizer Inc", "Pfizer, Inc."),
    ("Apple", "Apple Inc."),                        # add legal suffix
    ("IBM", "International Business Machines"),      # acronym expansion
    ("The ABC Co", "ABC Company"),
    ("Liberty Health Sciences", "Liberty Health Sciences, Inc."),  # legit suffix
    ("Univ of Florida Foundation", "University of Florida Foundation"),  # abbrev
    ("UF", "University of Florida"),                # acronym with 'of' infix
    ("Mass Inst Tech", "Massachusetts Institute of Technology"),  # per-word abbrev
    ("Harvard", "Harvard University"),             # add institution-type word
    ("Mayo", "Mayo Clinic"),
])
def test_preserves_identity_accepts_reformatting(original, canonical):
    assert canonical_preserves_identity(original, canonical) is True


@pytest.mark.parametrize("original, canonical", [
    ("Iso Group Inc", "CoStar Group"),             # reported bug #1
    ("Liberty Health Sciences", "Liberty Science Center"),  # reported bug #2 (shares "Liberty")
    ("USDA Agricultural Research Service", "Agricultural Research Service"),  # bug #3 (parent dropped)
    ("Precision Instruments Co.", "World Precision Instruments"),  # bug #4 (brand word prepended)
    ("Global NMR Solutions", "Global Solutions for Infectious Diseases"),  # bug #5 (dropped "NMR", added words)
    ("Ibero-American Research Foundation", "American Hearing Research Foundation"),  # bug #6 (dropped "Ibero", added "Hearing")
    ("NASA Jet Propulsion Laboratory", "Jet Propulsion Laboratory"),
    ("Acme Widgets LLC", "Globex Corporation"),
    ("International Paper", "International Business Machines"),  # diff company
])
def test_preserves_identity_rejects_different_company(original, canonical):
    assert canonical_preserves_identity(original, canonical) is False


def test_blank_inputs_are_permissive():
    # Nothing to compare → don't block.
    assert canonical_preserves_identity(None, "Anything") is True
    assert canonical_preserves_identity("Anything", None) is True


def test_company_canonical_rejects_different_entity():
    """run_company_canonical must drop a high-confidence but different name."""
    import asyncio

    class _FakeLLM:
        async def extract_json(self, system, user):
            return {"official_name": "CoStar Group", "confidence": "high"}

    from enrichment.company_canonical import run_company_canonical
    res = asyncio.run(run_company_canonical(
        record_id="R1", name1="Iso Group Inc",
        city=None, state=None, country="US", llm_client=_FakeLLM(),
    ))
    assert res.success is False
    assert res.name1_enriched is None


def test_company_canonical_accepts_same_entity():
    import asyncio

    class _FakeLLM:
        async def extract_json(self, system, user):
            return {"official_name": "ISO Group, Inc.", "confidence": "high"}

    from enrichment.company_canonical import run_company_canonical
    res = asyncio.run(run_company_canonical(
        record_id="R1", name1="Iso Group Inc",
        city=None, state=None, country="US", llm_client=_FakeLLM(),
    ))
    assert res.success is True
    assert res.name1_enriched == "ISO Group, Inc."
