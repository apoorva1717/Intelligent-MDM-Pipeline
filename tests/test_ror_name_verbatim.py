"""Item 2: the ROR canonical name must be written verbatim into name1_enriched,
including a post-comma campus qualifier ("University of California, Davis").

The two code paths that could drop the campus are exercised without hitting the
network: (1) `_strip_ror_country_suffix` (only strips " (Country)" / a full
", City, ST, Country" tail — never a bare campus), and (2) the identity guard
`canonical_preserves_identity`, which decides whether ROR's official name is
written or the input is kept.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.tier1_ror import _strip_ror_country_suffix
from utils.text_utils import (
    canonical_preserves_identity,
    clean_passthrough_org_name,
    expand_abbreviations,
    strip_address_fragments,
)


def _name1_decision(original, street, city, state, zipc, official):
    """Mirror the orchestrator's name1_enriched decision: strip address
    fragments for the ROR query, then run the identity guard against the
    ORIGINAL name (item 2 fix) — not the stripped query form."""
    cleaned = strip_address_fragments(
        original, street=street, city=city, state=state, zip_code=zipc,
    ) or original
    candidates = {
        original,
        expand_abbreviations(original) or original,
        cleaned,
        expand_abbreviations(cleaned) or cleaned,
    }
    if any(canonical_preserves_identity(c, official) for c in candidates if c):
        return official
    # Drop path: keep the input, but STANDARDISE it (expand abbreviations,
    # title-case ALL-CAPS) exactly as the orchestrator now does.
    kept = original or cleaned
    return clean_passthrough_org_name(kept) or kept


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
        # Guard True → the orchestrator writes ROR's official (full) name,
        # not a comma-truncated fallback.
        assert canonical_preserves_identity(original, official) is True

    def test_country_suffix_still_stripped(self):
        # Sanity: the strip still removes a genuine country suffix.
        assert _strip_ror_country_suffix("Pfizer (United States)") == "Pfizer"


class TestCampusCityStripInteraction:
    """Item 2 root cause: strip_address_fragments removes a campus city that
    equals the record's City ("…, Davis" + City "Davis"), so the guard must be
    run against the ORIGINAL name, or the campus is lost."""

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

    def test_usda_guard_still_keeps_original_on_drop(self):
        # ROR dropping "USDA" is a genuine identity drop → keep the original,
        # never a fragment.
        assert _name1_decision(
            "USDA Agricultural Research Service", street="", city="Beltsville",
            state="Maryland", zipc="20705",
            official="Agricultural Research Service",
        ) == "USDA Agricultural Research Service"

    def test_stuttgart_univ_standardised_on_drop(self):
        # ROR's German official "Hochschule für Technik Stuttgart" diverges from
        # the input, so the input is kept — but it must still be standardised:
        # "Univ" → "University". (Record 42000006.)
        assert _name1_decision(
            "Stuttgart Univ of Applied Sciences", street="SCHELLINGSTR 24",
            city="STUTTGART", state="", zipc="70174",
            official="Hochschule für Technik Stuttgart",
        ) == "Stuttgart University of Applied Sciences"

    def test_allcaps_input_titlecased_on_drop(self):
        assert _name1_decision(
            "STUTTGART UNIV OF APPLIED SCIENCES", street="", city="STUTTGART",
            state="", zipc="70174", official="Hochschule für Technik Stuttgart",
        ) == "Stuttgart University of Applied Sciences"

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