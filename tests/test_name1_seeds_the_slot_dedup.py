"""A department slot must not restate the organisation.

`finalise` already collapses two department slots that hold the same unit,
packing the survivors leftward. It compared those slots only to each other, so
a slot that restated **Name 1** survived — and one does not have to be copied
there to get there. Measured on the golden set (13345790):

    Name 1 = "Palo Alto Veterans Institute for Researc"   (SAP, truncated)
    Name 2 = "PAVIR"                                       (its acronym)

Preprocess leaves the acronym alone; a tier then resolves it, and the record
ships the organisation's name in both slots. The reference leaves Name 2 empty.

The fix seeds the dedup with the enriched Name 1. Name 1 is only ever the
seed — it is never a candidate for removal, so a record can never be left
nameless by this rule.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from enrichment.orchestrator import _init_result, finalise
from tests.conftest import fixture_evidence, seed


def _finalised(**overrides):
    result = _init_result(EnrichmentRecord(record_id="D1", country="US",
                                           name1="Acme Labs"))
    seed(result, fixture_evidence(), **overrides)
    return finalise(result, time.monotonic())


class TestASlotThatRestatesTheOrganisationIsDropped:
    def test_the_measured_pavir_case(self):
        r = _finalised(
            name1_enriched="Palo Alto Veterans Institute for Research",
            name2_enriched="Palo Alto Veterans Institute for Research",
        )
        assert r["name1_enriched"] == "Palo Alto Veterans Institute for Research"
        assert not r["name2_enriched"]

    def test_the_slots_below_pack_leftward(self):
        r = _finalised(
            name1_enriched="Princeton University",
            name2_enriched="Princeton University",
            name3_enriched="Department of Chemistry",
        )
        assert r["name1_enriched"] == "Princeton University"
        assert r["name2_enriched"] == "Department of Chemistry"
        assert not r["name3_enriched"]

    def test_a_surface_variant_counts_as_the_same_name(self):
        r = _finalised(
            name1_enriched="Princeton University",
            name2_enriched="princeton  university",
        )
        assert not r["name2_enriched"]


class TestItOnlyEverRemoves:
    def test_a_real_department_is_kept(self):
        r = _finalised(
            name1_enriched="Princeton University",
            name2_enriched="Department of Chemistry",
        )
        assert r["name1_enriched"] == "Princeton University"
        assert r["name2_enriched"] == "Department of Chemistry"

    def test_a_unit_that_merely_shares_words_with_the_org_is_kept(self):
        # The slot survives. What it holds is the unit canonicaliser's
        # business, not this rule's -- it rewrites the value here, and that is
        # pinned where that rule lives.
        r = _finalised(
            name1_enriched="Princeton University",
            name2_enriched="Princeton Neuroscience Institute",
        )
        assert r["name2_enriched"]
        assert r["name2_enriched"] != r["name1_enriched"]

    def test_name1_is_the_seed_and_is_never_itself_removed(self):
        """Name 1 is not a candidate: whatever else the block holds, the
        record keeps its organisation."""
        r = _finalised(
            name1_enriched="Princeton University",
            name2_enriched="Princeton University",
            name3_enriched="Princeton University",
        )
        assert r["name1_enriched"] == "Princeton University"
        assert not r["name2_enriched"]
        assert not r["name3_enriched"]

    def test_an_empty_name1_does_not_seed_anything(self):
        r = _finalised(name2_enriched="Department of Chemistry")
        assert r["name2_enriched"] == "Department of Chemistry"
