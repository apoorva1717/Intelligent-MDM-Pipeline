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


@pytest.mark.parametrize("raw, expected", [
    ("SAP Aktiengesellschaft", "SAP AG"),
    ("Carl Zeiss AKTIENGESELLSCHAFT", "Carl Zeiss AG"),
    ("Acme Incorporated", "Acme Inc"),
    ("Globex Corporation", "Globex Corp"),
    ("Muster Gesellschaft mit beschränkter Haftung", "Muster GmbH"),
    ("SAP SE", "SAP SE"),            # not a long form — unchanged
    ("SAP AG", "SAP AG"),            # already short — unchanged
    ("The Walt Disney Company", "The Walt Disney Company"),  # bare word kept
])
def test_collapse_legal_suffix(raw, expected):
    from utils.text_utils import collapse_legal_suffix
    assert collapse_legal_suffix(raw) == expected


def test_long_form_suffix_preserves_identity_against_short():
    # The identity guard must treat the long and short legal forms as the
    # SAME entity, so "Aktiengesellschaft" is not read as a distinctive word.
    assert canonical_preserves_identity("SAP Aktiengesellschaft", "SAP AG") is True
    assert canonical_preserves_identity("SAP AG", "SAP Aktiengesellschaft") is True


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
    # The proposal is surfaced (proposed_name), but the orchestrator's
    # spelling-variant gate — not run_company_canonical — is what blocks an
    # entity swap from ever being re-verified/accepted.
    from utils.text_utils import canonical_is_spelling_variant
    assert canonical_is_spelling_variant("Iso Group Inc", res.proposed_name) is False


@pytest.mark.parametrize("original, canonical", [
    ("Bayr AG", "Bayer AG"),                       # dropped letter
    ("Siemns AG", "Siemens AG"),
    ("Microsft Corp", "Microsoft Corporation"),    # typo + suffix expansion
    ("Volkswagon AG", "Volkswagen AG"),            # common misspelling
])
def test_spelling_variant_accepts_typos(original, canonical):
    from utils.text_utils import canonical_is_spelling_variant
    assert canonical_is_spelling_variant(original, canonical) is True


@pytest.mark.parametrize("original, canonical", [
    ("Iso Group Inc", "CoStar Group"),   # entity swap
    ("Apple", "Google"),                 # unrelated
    ("Bayer AG", "Baker AG"),            # different word, not a typo
    ("Bayer AG", "Bayer AG"),            # identical — no correction to make
    ("Pfizer", "Pfizer Inc"),           # pure legal-suffix add (identity path)
])
def test_spelling_variant_rejects_non_typos(original, canonical):
    from utils.text_utils import canonical_is_spelling_variant
    assert canonical_is_spelling_variant(original, canonical) is False


def test_company_canonical_surfaces_typo_proposal_for_reverify():
    """A high-confidence spelling correction the identity guard blocks must be
    exposed via proposed_name so the orchestrator can re-verify it (GLEIF)."""
    import asyncio

    class _FakeLLM:
        async def extract_json(self, system, user):
            return {"official_name": "Bayer AG", "confidence": "high"}

    from enrichment.company_canonical import run_company_canonical
    from utils.text_utils import canonical_is_spelling_variant
    res = asyncio.run(run_company_canonical(
        record_id="R2", name1="Bayr AG",
        city="Leverkusen", state=None, country="DE", llm_client=_FakeLLM(),
    ))
    assert res.success is False            # identity guard still blocks it
    assert res.name1_enriched is None
    assert res.proposed_name == "Bayer AG"  # …but the proposal is surfaced
    assert canonical_is_spelling_variant("Bayr AG", res.proposed_name) is True


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
