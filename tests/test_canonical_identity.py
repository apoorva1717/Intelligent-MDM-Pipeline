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


# ---------------------------------------------------------------------------
# The Name 2 (department) guard — ticket 24
#
# `grounded_resolver`'s identity guard was `name1`-only, so every Name 2
# proposal reached the write path AND the ROR re-verification with nothing
# asking whether it still denoted the same unit. Two values shipped that way.
#
# Every case below is a real proposal from the ticket-14 live run
# (.scratch/agentic-enrichment/tmp/live21_result.json), not an invented one.
# ---------------------------------------------------------------------------

from utils.text_utils import department_preserves_identity


class TestDepartmentIdentityGuardRefuses:
    """A changed or dropped unit type is what the guard is for."""

    def test_the_unit_type_may_not_change(self):
        """S3_16, shipped: a division became a laboratory."""
        assert not department_preserves_identity(
            "Forensic Science Div", "Forensic Services Laboratory",
            parent_name="Texas Department of Public Safety",
        )

    def test_the_unit_word_may_not_be_dropped(self):
        """S2_02, shipped: without "Laboratory" the value names the site, not
        the lab."""
        assert not department_preserves_identity(
            "Baytown Refinery Laboratory", "Baytown Refinery",
            parent_name="Exxonmobil",
        )

    def test_a_mangled_value_is_refused(self):
        """S3_11, shipped with a null provenance."""
        assert not department_preserves_identity(
            "Center for Medical", "For Medical", parent_name="Walter Reed",
        )

    def test_the_parent_name_cannot_rescue_a_dropped_token(self):
        """`parent_name` only ever makes words ADDABLE. It must not turn a
        dropped unit word into an accepted one."""
        assert not department_preserves_identity(
            "Baytown Refinery Laboratory", "Baytown Refinery",
            parent_name="Baytown Refinery Laboratory",
        )


class TestDepartmentIdentityGuardAccepts:
    """The guard must not cost the lane its correct answers."""

    def test_an_abbreviation_expansion_is_not_an_identity_change(self):
        """S3_13/14. The raw comparator reads `Lab` -> `Laboratory` as a
        distinctive-token mismatch, which is why this guard expands first."""
        assert department_preserves_identity(
            "Orange County Water Lab", "Orange County Water Laboratory",
        )

    def test_a_registry_verified_expansion_survives(self):
        """S3_15 — `ror.org/03cap2a49`, a real identifier for the right
        entity. The four "new" words ARE Name 1, sitting in the same record;
        a guard that refused this would discard a correct resolution."""
        assert department_preserves_identity(
            "Weapons Div", "Naval Air Warfare Center Weapons Division",
            parent_name="Naval Air Warfare Center",
        )

    def test_without_the_parent_that_same_value_is_refused(self):
        """The parent name is doing the work, not a loosened comparator."""
        assert not department_preserves_identity(
            "Weapons Div", "Naval Air Warfare Center Weapons Division",
        )

    def test_mgmt_expands(self):
        """S3_17. `Mgmt` was absent from `_ABBREV_MAP`, so a correct expansion
        read as a token swap."""
        assert department_preserves_identity(
            "Department of Supply Chain Mgmt",
            "Department of Supply Chain Management",
        )

    def test_a_unit_word_may_be_ADDED(self):
        """The asymmetry is deliberate: gaining a unit type states what the
        slot left implicit, losing one changes what the value names."""
        assert department_preserves_identity(
            "Calibration Services", "Calibration Services Department",
        )


class TestTheName1GuardIsUnchanged:
    """The widened addable vocabulary is scoped to Name 2 and must not leak
    into the Name 1 guard.

    The abbreviation expansion no longer is: it moved into the comparator and
    now applies to both slots, which is what `TestTheGuardExpandsBothSides`
    below covers. What must not leak is the *unit vocabulary* — `Division` is
    addable for a department and never for an organisation.
    """

    @pytest.mark.parametrize("original, canonical", [
        ("Kelvin Bridge Instruments", "Wheatstone Metrology Group"),
        ("Liberty Health Sciences", "Liberty Science Center"),
        ("Iso Group Inc", "CoStar Group"),
    ])
    def test_name1_still_refuses_a_replacement(self, original, canonical):
        assert not canonical_preserves_identity(original, canonical)

    def test_name1_does_not_gain_the_unit_vocabulary(self):
        """`Division` is addable for a department and must not become addable
        for an organisation name."""
        assert not canonical_preserves_identity(
            "Acme Instruments", "Acme Instruments Weapons Division",
        )

    def test_extra_addable_defaults_to_nothing(self):
        assert canonical_preserves_identity(
            "Acme", "Acme Institute",
        ) is canonical_preserves_identity(
            "Acme", "Acme Institute", extra_addable=None,
        )


class TestTheGuardExpandsBothSides:
    """`_token_covers` disables its prefix rule under four characters, so
    `Lab` and `Laboratories` read as different words — and `Lab` is the
    commonest abbreviation in this data. Both sides are expanded first.

    Measured on the golden set: the guard refused two proposals that were
    exactly the name the reference asks for, and the record fell back to the
    abbreviation it arrived with.
    """

    @pytest.mark.parametrize("original, canonical", [
        # Both measured. The first shipped as "Bio-Rad Laboratory".
        ("Bio-Rad Lab Inc", "Bio-Rad Laboratories, Inc."),
        ("Orange County Public Health Lab",
         "Orange County Public Health Laboratory"),
        # The same shape, the other way round, and per-word.
        ("Acme Laboratories", "Acme Lab"),
        ("Natl Inst of Standards", "National Institute of Standards"),
    ])
    def test_the_same_name_spelled_out_is_the_same_name(self, original, canonical):
        assert canonical_preserves_identity(original, canonical) is True

    @pytest.mark.parametrize("original, canonical", [
        # Every rejection the golden set records, all still refused: a site
        # swapped for its parent, a swapped word, a dropped site.
        ("Valero Refinery", "Valero Energy Corporation"),
        ("Huntsman Advanced Chemicals", "Huntsman Advanced Materials"),
        ("Zoetis Ref Lab Cincinnati", "Zoetis Reference Laboratories"),
        ("3M Corporate", "3M Company"),
        # And the replacements the guard was written for.
        ("Iso Group Inc", "CoStar Group"),
        ("Liberty Health Sciences", "Liberty Science Center"),
    ])
    def test_expanding_refuses_everything_it_refused_before(
        self, original, canonical,
    ):
        assert canonical_preserves_identity(original, canonical) is False

    def test_a_resolution_no_string_rule_could_justify_is_still_refused(self):
        """Correct, and rightly needs corroboration rather than a looser
        comparator: nothing in the original spells the canonical."""
        assert not canonical_preserves_identity(
            "VA MC West LA Visn 22",
            "VA Greater Los Angeles Healthcare System",
        )


class TestThePluralFold:
    """The prefix relation already handles the `-s` plural ("sciences" starts
    with "science"). It cannot handle `-ies`, where the stem changes — and
    that is the plural this data is full of."""

    @pytest.mark.parametrize("original, canonical", [
        ("Acme Laboratory", "Acme Laboratories"),
        ("Acme Industry", "Acme Industries"),
        ("Acme Technology Group", "Acme Technologies Group"),
    ])
    def test_an_ies_plural_is_the_same_word(self, original, canonical):
        assert canonical_preserves_identity(original, canonical) is True

    @pytest.mark.parametrize("original, canonical", [
        # Not each other, and a looser stemmer would fold them.
        ("Acme Series Group", "Acme Serial Group"),
        ("Bayer Group", "Baker Group"),
        ("Acme Studies Group", "Acme Student Group"),
    ])
    def test_it_does_not_fold_words_that_are_not_each_other(
        self, original, canonical,
    ):
        assert canonical_preserves_identity(original, canonical) is False

    def test_an_ies_word_that_is_not_a_plural_folds_to_a_non_word(self):
        """`series` and `species` are not plurals of `sery` and `specy`, and
        the fold has no way to know. It is harmless: the fold can only ever
        make two tokens match, and nothing in the data spells the non-word it
        produces. Pinned so the cost is visible rather than assumed away."""
        assert canonical_preserves_identity(
            "Acme Series Group", "Acme Sery Group",
        ) is True
