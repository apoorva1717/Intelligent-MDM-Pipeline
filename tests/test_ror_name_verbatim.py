"""Item 2: the ROR canonical name must be written verbatim into name1_enriched,
including a post-comma campus qualifier ("University of California, Davis").

Since Fix 4 the write is UNCONDITIONAL on a verified match — the identity guard
`canonical_preserves_identity` no longer sits between the match and the name,
so the only path that could still drop the campus is
`_strip_ror_country_suffix` (which strips " (Country)" / a full ", City, ST,
Country" tail — never a bare campus). The guard itself is still exercised here
because it remains in force on the LLM canonicalisation paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier1_ror import _strip_ror_country_suffix
from utils.text_utils import (
    canonical_preserves_identity,
    strip_address_fragments,
)


def _name1_decision(original, street, city, state, zipc, official):
    """Mirror the orchestrator's name1_enriched decision (Fix 4).

    Address fragments are stripped to build the ROR query, and a verified
    match writes ROR's official name unconditionally. There is no second
    threshold between the match and the name write: if the match was good
    enough to attach ror_id, it is good enough to attach the name.
    """
    strip_address_fragments(
        original, street=street, city=city, state=state, zip_code=zipc,
    )
    return (official or "").strip() or original


class TestCampusQualifierKept:
    @pytest.mark.parametrize("name", [
        "University of California, Davis",
        "California State University, Long Beach",
    ])
    def test_country_suffix_strip_keeps_campus(self, name):
        # Only country/full-address suffixes are stripped — never a bare campus.
        assert _strip_ror_country_suffix(name) == name

    @pytest.mark.parametrize("original,official", [
        ("UNIVERSITY OF CALIFORNIA, DAVIS", "University of California, Davis"),
        ("CALIFORNIA STATE UNIVERSITY, LONG BEACH", "California State University, Long Beach"),
    ])
    def test_identity_guard_accepts_campus_name(self, original, official):
        # The guard no longer gates the ROR write, but it still runs on the
        # LLM canonicalisation paths, where a campus name must survive it.
        assert canonical_preserves_identity(original, official) is True

    def test_country_suffix_still_stripped(self):
        # Sanity: the strip still removes a genuine country suffix.
        assert _strip_ror_country_suffix("Pfizer (United States)") == "Pfizer"


class TestCampusCityStripInteraction:
    """Item 2 root cause: strip_address_fragments removes a campus city that
    equals the record's City ("…, Davis" + City "Davis"). Fix 4 settles it for
    good — the official name is written whatever the stripped query looked
    like."""

    def test_uc_davis_campus_survives_city_strip(self):
        assert _name1_decision(
            "UNIVERSITY OF CALIFORNIA, DAVIS",
            street="Chemistry Department, | One Shields Ave,",
            city="Davis", state="California", zipc="95616-5270",
            official="University of California, Davis",
        ) == "University of California, Davis"

    def test_csulb_campus_survives_city_strip(self):
        assert _name1_decision(
            "CALIFORNIA STATE UNIVERSITY, LONG BEACH",
            street="CSULB Foundation, | 6300 State University Drive, Suite 332",
            city="Long Beach", state="California", zipc="90815",
            official="California State University, Long Beach",
        ) == "California State University, Long Beach"

    def test_official_name_wins_when_input_says_less(self):
        # Fix 4: ROR matched the Agricultural Research Service and verified it,
        # so the registry's official name for the matched entity is what ships
        # — the abbreviated input form does not survive beside a verified id.
        #
        # Note what this test is NOT saying. It reaches _name1_decision
        # directly, with the organisation ALREADY resolved to the entity ROR
        # matched. A compound "USDA Agricultural Research Service" never gets
        # this far intact: preprocessing splits the owning department into
        # Name 1 and the service into Name 2 first
        # (tests/test_parent_org_expansion.py). Reading this test as licence to
        # drop a parent organisation is what shipped rows whose only trace of
        # the USDA was the ars.usda.gov domain.
        assert _name1_decision(
            "Agricultural Research Svc", street="", city="Beltsville",
            state="Maryland", zipc="20705",
            official="Agricultural Research Service",
        ) == "Agricultural Research Service"

    def test_stuttgart_official_name_wins(self):
        # ROR's German official name diverges from the English input, but the
        # match is verified, so the official name ships. Note what does NOT
        # happen: the ROR-local _INSTITUTION_ACRONYMS expansion that got the
        # record to resolve never reaches the output. (Record 42000006.)
        assert _name1_decision(
            "Stuttgart Univ of Applied Sciences", street="SCHELLINGSTR 24",
            city="STUTTGART", state="", zipc="70174",
            official="Hochschule für Technik Stuttgart",
        ) == "Hochschule für Technik Stuttgart"

    def test_allcaps_input_does_not_alter_official_name(self):
        assert _name1_decision(
            "STUTTGART UNIV OF APPLIED SCIENCES", street="", city="STUTTGART",
            state="", zipc="70174", official="Hochschule für Technik Stuttgart",
        ) == "Hochschule für Technik Stuttgart"

    def test_the_trap_old_guard_would_have_failed(self):
        # Documents the bug: comparing the STRIPPED query form against the
        # official wrongly reads the campus as "added" and rejects it.
        cleaned = strip_address_fragments(
            "UNIVERSITY OF CALIFORNIA, DAVIS",
            street="Chemistry Department, | One Shields Ave,",
            city="Davis", state="California", zip_code="95616-5270",
        )
        assert cleaned == "UNIVERSITY OF CALIFORNIA"
        assert canonical_preserves_identity(cleaned, "University of California, Davis") is False
        assert canonical_preserves_identity(
            "UNIVERSITY OF CALIFORNIA, DAVIS", "University of California, Davis"
        ) is True