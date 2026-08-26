"""A parent organisation's acronym is expanded, never dropped.

SAP master data routinely writes an organisation and one of its units into a
single Name 1 field, with the organisation abbreviated: "USDA - Kerrville MLRA
Office", "NASA Ames Research Center". Two separate rules used to delete the
organisation from those rows.

*The dash rule.* ``preprocess._strip_redundant_acronym`` resolves "an
abbreviation and its expansion, written twice" down to the expansion. Its dash
branch fired on any spaced dash with a short token on one side, whether or not
the token was an initialism of the other side — so "USDA - Kerrville MLRA
Office" was read as a name written twice and the USDA was deleted. The flag it
raised ("acronym-ambiguous") named the uncertainty and dropped the value
anyway.

*The casing rule.* ``smart_title_case`` keeps a token upper-case only when it
is short, vowel-less, or on a hand-maintained allowlist. "MLRA" is none of the
three, so the same row also shipped "Mlra".

Together they turned "USDA - KERRVILLE MLRA OFFICE" into "Kerrville Mlra
Office": the owning department gone, the programme acronym mangled.

The rule these tests pin: an acronym that names a known parent organisation is
expanded into Name 1 — the organisation slot — and the unit beside it moves
into the department block. Neither half is discarded, and an acronym that
cannot be read as an English word keeps its casing without anyone adding it to
a list by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import (
    _parent_org_split,
    _strip_redundant_acronym,
    _syllabic_dash_abbrev,
    preprocess_record,
)
from utils.text_utils import PARENT_ORG_ACRONYMS, smart_title_case


def _pp(name1, **kw):
    fields = dict(
        name2=None, name3=None, name4=None, name5=None,
        contact=None, email=None, street1=None, street2=None, street3=None,
    )
    fields.update(kw)
    return preprocess_record(name1=name1, **fields)


class TestParentSplit:
    @pytest.mark.parametrize("value, parent, unit", [
        # The two rows that prompted this.
        ("USDA - KERRVILLE MLRA OFFICE",
         "United States Department of Agriculture", "KERRVILLE MLRA OFFICE"),
        ("NASA AMES RESEARCH CENTER",
         "National Aeronautics and Space Administration",
         "AMES RESEARCH CENTER"),
        # Mixed case, adjacent and dashed, and a dotted acronym.
        ("NASA Jet Propulsion Laboratory",
         "National Aeronautics and Space Administration",
         "Jet Propulsion Laboratory"),
        ("USDA Agricultural Research Service",
         "United States Department of Agriculture",
         "Agricultural Research Service"),
        ("NOAA - National Marine Fisheries Service",
         "National Oceanic and Atmospheric Administration",
         "National Marine Fisheries Service"),
        ("U.S.D.A. Forest Service",
         "United States Department of Agriculture", "Forest Service"),
    ])
    def test_parent_and_unit_are_separated(self, value, parent, unit):
        assert _parent_org_split(value) == (parent, unit)

    @pytest.mark.parametrize("value", [
        "NASA",                      # the organisation alone — no unit
        "USDA",
        "University of Florida",     # no acronym at all
        "UC Berkeley",               # acronym, but not a known parent
        "3M Company",
        "Dana-Farber Cancer Institute",
        "EMSL Analytical, Inc.",     # deliberately not in the map
        "CERN",
    ])
    def test_leaves_everything_else_alone(self, value):
        assert _parent_org_split(value) is None

    @pytest.mark.parametrize("value", [
        # The acronym and the phrase beside it are ONE entity written twice.
        # That is the dedupe's job, not a parent split — otherwise the
        # expansion would be duplicated across Name 1 and Name 2.
        "FDA - Food & Drug Administration",
        "FDA Food and Drug Administration",
        "USDA - United States Department of Agriculture",
        "NIST - National Institute of Standards and Technology",
    ])
    def test_same_entity_written_twice_is_not_a_parent_split(self, value):
        assert _parent_org_split(value) is None


class TestPreprocessRoutesParentAndUnit:
    def test_usda_field_office(self):
        r = _pp("USDA - KERRVILLE MLRA OFFICE")
        assert r.name1 == "United States Department of Agriculture"
        assert r.name2 == "KERRVILLE MLRA OFFICE"

    def test_nasa_centre(self):
        r = _pp("NASA AMES RESEARCH CENTER")
        assert r.name1 == "National Aeronautics and Space Administration"
        assert r.name2 == "AMES RESEARCH CENTER"

    def test_existing_department_shifts_down_it_is_not_overwritten(self):
        # Name 2 already held a department. The unit split out of Name 1 owns
        # the slot above it — it is the larger of the two units — and the
        # department moves down rather than being replaced.
        r = _pp("NASA Ames Research Center", name2="Procurement Office")
        assert r.name1 == "National Aeronautics and Space Administration"
        assert r.name2 == "Ames Research Center"
        assert r.name3 == "Procurement Office"

    def test_full_block_reports_what_it_pushed_out(self):
        # Five slots, all occupied, and a sixth value to place. Something has
        # to go; the flag says which, so the loss is visible in the export
        # rather than silent.
        r = _pp(
            "NASA Ames Research Center",
            name2="Dept A", name3="Dept B", name4="Dept C", name5="Dept D",
        )
        assert r.name1 == "National Aeronautics and Space Administration"
        assert (r.name2, r.name3, r.name4, r.name5) == (
            "Ames Research Center", "Dept A", "Dept B", "Dept C",
        )
        assert any("name-block-overflow" in f and "Dept D" in f
                   for f in r.flags)

    def test_no_ambiguity_flag_is_raised(self):
        # The old path flagged "acronym-ambiguous" and dropped the acronym.
        # There is nothing ambiguous here: the acronym is a known organisation
        # and both halves ship.
        r = _pp("USDA - KERRVILLE MLRA OFFICE")
        assert not any("acronym-ambiguous" in f for f in r.flags)


class TestDedupeStillResolvesOneEntityWrittenTwice:
    """The parent split must not weaken the rule it runs ahead of."""

    @pytest.mark.parametrize("value, expected", [
        ("FDA - Food & Drug Administration", "Food and Drug Administration"),
        ("MIT Massachusetts Institute of Technology",
         "Massachusetts Institute of Technology"),
        ("Njit - New Jersey Institute of Technology",
         "New Jersey Institute of Technology"),
    ])
    def test_full_form_still_wins(self, value, expected):
        assert _strip_redundant_acronym(value).lower() == expected.lower()

    def test_syllabic_abbreviation_still_flags(self):
        # Not a known parent, not a verified initialism: unchanged behaviour.
        value = "Tuhh - Hamburg University of Technology"
        assert _strip_redundant_acronym(value) == "Hamburg University of Technology"
        assert _syllabic_dash_abbrev(value) is not None

    def test_parent_split_is_not_flagged_as_a_syllabic_abbreviation(self):
        assert _syllabic_dash_abbrev("USDA - Kerrville MLRA Office") is None


class TestAcronymCasingSurvivesAnAllCapsField:
    @pytest.mark.parametrize("value, expected", [
        # The programme acronym that started this: 4 letters, carries a vowel,
        # on no allowlist — and unsayable, which is what now keeps it.
        ("KERRVILLE MLRA OFFICE", "Kerrville MLRA Office"),
        ("NRLF STORAGE FACILITY", "NRLF Storage Facility"),
        # Unchanged: the allowlist and the length/vowel rules still do their
        # own work, and ordinary words are still cased.
        ("NASA AMES RESEARCH CENTER", "NASA Ames Research Center"),
        ("MRI DEPARTMENT", "MRI Department"),
        ("SOUTH BAY HOSPITAL", "South Bay Hospital"),
        ("LAKELAND REGIONAL HEALTH", "Lakeland Regional Health"),
        ("SECRETARY OF STATE", "Secretary of State"),
    ])
    def test_casing(self, value, expected):
        assert smart_title_case(value) == expected

    @pytest.mark.parametrize("value, expected", [
        # A borrowed proper noun opens with a two-consonant run, which the
        # rule deliberately does not touch — three or more is where it starts.
        ("DVORAK LABORATORIES", "Dvorak Laboratories"),
        ("SVEN NILSSON AB", "Sven Nilsson AB"),
        ("KLEIN INSTRUMENTS", "Klein Instruments"),
        ("STRAUB DESIGN", "Straub Design"),
        ("SCHNEIDER ELECTRIC", "Schneider Electric"),
        # Three-consonant onsets that ARE names. German "SCH" and Gaelic "MC"
        # are carved out by prefix; the rest are out of the length band.
        ("SCHMIDT INSTRUMENTS", "Schmidt Instruments"),
        ("MCKAY LABS", "McKay Labs"),
        ("MCDONALD RESEARCH", "McDonald Research"),
    ])
    def test_words_with_consonant_clusters_are_still_cased(self, value, expected):
        assert smart_title_case(value) == expected

    @pytest.mark.parametrize("value, mode, expected", [
        # The token-level path, which is what actually cases the department
        # slots. It carries its own copy of the heuristics, so the rule has to
        # be in both places or a value is cased differently depending on which
        # path reached it.
        ("KERRVILLE MLRA OFFICE", "name", "Kerrville MLRA Office"),
        ("KERRVILLE MLRA OFFICE", "text", "Kerrville MLRA Office"),
        ("500 TECH DR MS-4", "text", "500 Tech Dr MS-4"),
        ("SOUTH BAY HOSPITAL", "name", "South Bay Hospital"),
        ("DVORAK LABORATORIES", "name", "Dvorak Laboratories"),
    ])
    def test_token_level_casing_agrees(self, value, mode, expected):
        from utils.text_utils import normalise_case
        assert normalise_case(value, mode=mode) == expected

    def test_mixed_case_input_is_untouched(self):
        # Casing already carries information — nothing to decide.
        assert smart_title_case("Kerrville MLRA Office") == "Kerrville MLRA Office"


def test_every_parent_acronym_keeps_its_casing():
    """The two lists are folded together in text_utils; this pins that they
    cannot drift apart — an acronym the pipeline expands must also be an
    acronym the pipeline refuses to title-case."""
    for acronym in PARENT_ORG_ACRONYMS:
        assert smart_title_case(f"{acronym} FIELD OFFICE").startswith(acronym)
