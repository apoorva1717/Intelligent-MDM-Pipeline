"""Name-2 slot classification — ``DEDUP_V2_NAME2`` (change C).

What the text below Name 1 IS, and what follows from getting it right: the
signature key, the ``has_name2`` boundary that the deterministic asymmetry rule
turns on, the six columns the file route used to drop, the prompt, and the two
extra candidate rules for large blocks.

Every classification case here is a real row from the stress batch, cited by
Customer number, because the rules were written against these shapes and a
synthetic example would not tell you whether they hold on the data.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from dedup.candidates import CandidateUnit, generate_candidate_pairs  # noqa: E402
from dedup.models import DedupRow  # noqa: E402
from dedup.name_slots import classify_slots  # noqa: E402
from dedup.prompts import PROMPT_VERSION, PROMPT_VERSION_V2  # noqa: E402
from dedup.signatures import build_signatures  # noqa: E402
from tests.dedup_v2_support import (  # noqa: E402
    V2_FLAGS,
    SpecOracleLLM,
    fixture_dedup_rows,
    load_fixture,
    run_clustering,
)


@pytest.fixture
def flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in V2_FLAGS:
        monkeypatch.setenv(flag, "true")


@pytest.fixture
def name2_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only C. The flags must work one at a time as well as together."""
    monkeypatch.setenv("DEDUP_V2_BLOCKING", "false")
    monkeypatch.setenv("DEDUP_V2_NAME2", "true")
    monkeypatch.setenv("DEDUP_V2_ID_CONFLICT", "false")


@pytest.fixture(scope="module")
def fixture() -> dict:
    return load_fixture()


# ---------------------------------------------------------------------------
# classify_slots
# ---------------------------------------------------------------------------

#: (label, Name 1, Name 2, other Name 1s in the block, kind, institution, department)
SLOT_CASES = [
    ("13044976 a delivery desk is not a department",
     "University of Texas", "Central Receiving", [],
     "logistics", "University of Texas", ""),
    ("13161437 accounts payable is not a department",
     "University of California, San Francisco", "Accounts Payable", [],
     "logistics", "University of California, San Francisco", ""),
    ("13216611 a trading name is not a department",
     "Lee Memorial Health System", "DBA Lee Health", [],
     "alias", "Lee Memorial Health System", ""),
    ("13226604 a parent company is not a department",
     "Global Equipment Services Inc", "A Kimball Electronics Company", [],
     "alias", "Global Equipment Services Inc", ""),
    ("13135468 Name 1's own tail is not a department",
     "EMD Serono Research & Development", "Institute, Inc", [],
     "overflow", "EMD Serono Research & Development Institute, Inc", ""),
    ("13345937 a truncated Name 1 continues into Name 2",
     "Palo Alto Veterans Institute for", "Research",
     ["Palo Alto Veterans Institute for Research"],
     "overflow", "Palo Alto Veterans Institute for Research", ""),
    ("13130623 an opaque Name 1 means Name 2 is the institution",
     "GHW23", "Case Western Reserve University",
     ["Case Western Reserve University"],
     "institution", "Case Western Reserve University", ""),
    ("13342545 a person is not a department",
     "UCSF", "Emanuela Zacco - LCA Core", [],
     "contact", "UCSF", ""),
    ("13367825 a real department stays a department",
     "Stanford University", "Fairchild Science", [],
     "department", "Stanford University", "Fairchild Science"),
    ("13036862 the whole block below Name 1 is the department",
     "National Aeronautics and Space Administration",
     "Intelligent Systems Division", [],
     "department", "National Aeronautics and Space Administration",
     "Intelligent Systems Division"),
]


@pytest.mark.parametrize(
    ("label", "name1", "name2", "others", "kind", "institution", "department"),
    SLOT_CASES, ids=[case[0] for case in SLOT_CASES],
)
def test_classify_slots(label, name1, name2, others, kind, institution, department) -> None:
    result = classify_slots(name1, name2, block_name1s=[name1, *others])
    assert (result.kind, result.institution, result.department) == (
        kind, institution, department,
    )


def test_a_two_word_department_is_not_read_as_a_person() -> None:
    """The trap this batch is built around, stated as its own assertion.

    "Fairchild Science" has the shape of a first and last name. Reading it as a
    contact empties the department, and the Stanford record then merges into
    the two bare "Stanford University" rows at the same door — the single
    highest-cost misclassification available here, because it destroys a real
    distinction rather than merely failing to find one.
    """
    result = classify_slots("Stanford University", "Fairchild Science")
    assert result.kind == "department"
    assert result.department == "Fairchild Science"


def test_a_unit_of_the_records_own_institution_is_not_a_person() -> None:
    """"Army Contracting Command - Detroit Arsenal" has the person-plus-site
    shape and is neither. It shares "Army" with its own Name 1, which is what
    a unit does and a contact does not."""
    result = classify_slots(
        "United States Army", "Army Contracting Command - Detroit Arsenal"
    )
    assert result.kind == "department"


def test_logistics_text_is_dropped_from_a_lower_slot_too() -> None:
    """A desk is not a unit wherever it was typed (13047774: Name 3)."""
    result = classify_slots(
        "The University of Texas Southwestern", "Medical Center", "Central Receiving"
    )
    assert result.department == "Medical Center"


def test_an_alias_is_kept_and_reported_not_discarded() -> None:
    """The department is emptied; the trading name is not thrown away.

    It is the other spelling of this institution, and the cross-slot candidate
    rule is built on exactly this field.
    """
    result = classify_slots("Lee Memorial Health System", "DBA Lee Health")
    assert result.aliases == ["DBA Lee Health"]


def test_an_opaque_name1_is_kept_as_an_alias() -> None:
    result = classify_slots(
        "KMB3 LLC", "Case Western Reserve University",
        block_name1s=["KMB3 LLC", "Case Western Reserve University"],
    )
    assert result.aliases == ["KMB3 LLC"]


def test_an_opaque_name1_needs_a_matching_name1_in_the_block() -> None:
    """Otherwise every short upper-case Name 1 promotes whatever is under it.

    "UCSF" is the same shape as "GHW23"; what separates them is that no other
    record in UCSF's block spells "Emanuela Zacco - LCA Core" as an institution.
    """
    result = classify_slots("UCSF", "Emanuela Zacco - LCA Core", block_name1s=["UCSF"])
    assert result.kind != "institution"
    assert result.institution == "UCSF"


# ---------------------------------------------------------------------------
# The signature key and has_name2
# ---------------------------------------------------------------------------

def _rows(*pairs) -> list[DedupRow]:
    return [
        DedupRow(row_id=str(i), name1=n1, name2=n2, country="US",
                 postal_code="12345", street="Main St", house_no="1")
        for i, (n1, n2) in enumerate(pairs, start=1)
    ]


def test_a_desk_no_longer_splits_a_record_from_its_own_institution(name2_only) -> None:
    """UTSA, the shortest statement of what C is for.

    Two records at 7703 Floyd Curl Dr: one bare "University of Texas", one with
    "Central Receiving" below it. v1 read the second as a departmental record,
    which the asymmetry rule then forbade from ever sharing an entity with the
    first — the rule working exactly as designed, on a false premise.
    """
    signatures = build_signatures(
        _rows(("University of Texas", None), ("University of Texas", "Central Receiving"))
    )
    assert len(signatures) == 1
    assert signatures[0].has_name2 is False


def test_a_real_department_still_splits(name2_only) -> None:
    signatures = build_signatures(
        _rows(("Stanford University", None), ("Stanford University", "Fairchild Science"))
    )
    assert len(signatures) == 2
    assert sorted(s.has_name2 for s in signatures) == [False, True]


def test_overflow_rebuilds_one_institution_from_two_cells(name2_only) -> None:
    signatures = build_signatures(
        _rows(("Palo Alto Veterans Institute for", "Research"),
              ("Palo Alto Veterans Institute for Research", None))
    )
    assert len(signatures) == 1, "the two spellings are one signature"
    assert signatures[0].institution == "Palo Alto Veterans Institute for Research"


def test_the_signature_carries_the_bound_phase_one_columns(name2_only) -> None:
    """C.4's six columns reach a signature, not just a DedupRow."""
    rows = [DedupRow(
        row_id="1", name1="The Assay Depot Inc", country="US", postal_code="92075",
        street="Lomas Santa Fe Dr", house_no="505",
        operating_name="scientist com", suggested_name="The Assay Depot, Inc.",
        record_type="unknown", ror_id_provenance="ror:verified",
        building="Suite 110",
    )]
    sig = build_signatures(rows)[0]
    assert sig.operating_name == "scientist com"
    assert sig.suggested_name == "The Assay Depot, Inc."
    assert sig.record_type == "unknown"
    assert sig.ror_provenance == "ror:verified"


def test_building_reaches_neither_the_key_nor_the_block(flags_on) -> None:
    """A building is not an entity, and two buildings are not two doors.

    The change request binds Building as a hint only. This is that promise as a
    test: the same address with different Building values is one block and one
    signature.
    """
    from dedup.signatures import build_blocks

    rows = [
        DedupRow(row_id="1", name1="Acme Labs", country="US", postal_code="12345",
                 street="Main St", house_no="1", building="A"),
        DedupRow(row_id="2", name1="Acme Labs", country="US", postal_code="12345",
                 street="Main St", house_no="1", building="B"),
    ]
    assert len(build_blocks(rows)) == 1
    assert len(build_signatures(rows)) == 1


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

def test_the_prompt_version_moves_with_the_flag(flags_on, name2_only) -> None:
    from dedup.prompts import prompt_version

    assert prompt_version() == PROMPT_VERSION_V2


def test_the_v1_prompt_version_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    from dedup.prompts import prompt_version

    for flag in V2_FLAGS:
        monkeypatch.setenv(flag, "false")
    assert prompt_version() == PROMPT_VERSION


def test_the_model_is_shown_the_new_fields(flags_on, fixture) -> None:
    """Including the five columns the file route used to drop before this."""
    llm = SpecOracleLLM(fixture)
    asyncio.run(run_clustering(fixture_dedup_rows(fixture), llm))
    prompts = "\n".join(llm.prompts)
    for field in (
        "institution", "department", "aliases", "operating_name",
        "suggested_name", "record_type", "ror_id", "lei_id", "street_match",
    ):
        assert f'"{field}"' in prompts, f"{field} never reached the model"


def test_the_model_is_never_told_a_blocked_pair_is_incompatible(flags_on, fixture) -> None:
    """Two records only reach one prompt after their delivery point matched.

    "incompatible" would be describing a question that was already answered,
    in a word that invites the model to re-answer it the other way.
    """
    llm = SpecOracleLLM(fixture)
    asyncio.run(run_clustering(fixture_dedup_rows(fixture), llm))
    assert "incompatible" not in "\n".join(llm.prompts)


def test_the_department_rule_is_stated_in_both_directions(flags_on) -> None:
    """v3 said when records are the SAME and left the asymmetric case silent.

    One record with a department and one without, at one door, is the single
    most common shape in this batch; the prompt has to answer it outright.
    """
    from dedup.prompts import system_prompt

    text = system_prompt()
    assert "SAME ENTITY when" in text and "DIFFERENT ENTITY when" in text
    assert "one has a department and the other has none" in text


# ---------------------------------------------------------------------------
# C.5 — the extra candidate rules
# ---------------------------------------------------------------------------

def _unit(index: int, name: str, **kw) -> CandidateUnit:
    # ``adjudicated=False``: a lone signature the bucketed pass never put to the
    # model. That is what residue nomination is FOR, and a pair where both
    # sides were already adjudicated in the same bucket is skipped before any
    # rule is consulted.
    return CandidateUnit(
        index=index, name=name, ror_id=None, lei_id=None,
        has_name2=False, adjudicated=False, **kw,
    )


def _rules(units, extra_rules: bool) -> dict[tuple[int, int], str]:
    pairs = generate_candidate_pairs(
        units, name_threshold=0.85, token_threshold=0.6, extra_rules=extra_rules,
    )
    return {(c.a, c.b): c.rule for c in pairs}


def test_acronym_nominates_a_short_name_against_its_initials() -> None:
    units = [_unit(0, "Global Equipment Services Inc"), _unit(1, "GES Inc")]
    assert _rules(units, extra_rules=True) == {(0, 1): "acronym"}
    assert _rules(units, extra_rules=False) == {}


def test_acronym_drops_the_connector_words() -> None:
    """"University of Texas" is UT to everyone who writes it down."""
    units = [_unit(0, "University of Texas"), _unit(1, "UT")]
    assert _rules(units, extra_rules=True) == {(0, 1): "acronym"}


def test_acronym_does_not_fire_on_a_long_name() -> None:
    """Only the short side may BE the acronym, or initials match noise."""
    units = [
        _unit(0, "Global Equipment Services Inc"),
        _unit(1, "General Electric Systems Corporation"),
    ]
    assert _rules(units, extra_rules=True) == {}


def test_cross_slot_nominates_through_an_alias() -> None:
    units = [
        _unit(0, "Assay Depot Inc", aliases=("Scientist.com",)),
        _unit(1, "Scientist com"),
    ]
    assert _rules(units, extra_rules=True) == {(0, 1): "cross_slot"}


def test_cross_slot_nominates_through_a_suggested_name() -> None:
    units = [
        _unit(0, "Takeda Pharmaceuticals",
              suggested_name="TAKEDA PHARMACEUTICALS U.S.A., INC."),
        _unit(1, "Takeda Pharmaceutical U.S.A., Inc."),
    ]
    assert (0, 1) in _rules(units, extra_rules=True)


def test_id_convergence_still_outranks_the_new_rules() -> None:
    units = [
        CandidateUnit(index=0, name="Global Equipment Services Inc", ror_id="r1",
                      lei_id=None, has_name2=False, adjudicated=False),
        CandidateUnit(index=1, name="GES Inc", ror_id="r1", lei_id=None,
                      has_name2=False, adjudicated=False),
    ]
    assert _rules(units, extra_rules=True) == {(0, 1): "id"}


def test_the_extra_rules_stay_off_for_a_small_block(flags_on, fixture) -> None:
    """They are for blocks Mode A cannot cover in one call.

    Every block in this fixture is small, so nothing here should nominate on an
    acronym or an alias — the pairs those rules would find were all put to the
    model already, in one partition call, and asking again costs a call per
    pair for a second opinion on a settled question.
    """
    llm = SpecOracleLLM(fixture)
    results, _summary = asyncio.run(
        run_clustering(fixture_dedup_rows(fixture), llm)
    )
    reasons = " ".join(r.reasoning or "" for r in results.values())
    assert "[acronym]" not in reasons and "[cross_slot]" not in reasons


def test_the_reasoning_names_the_rule_that_nominated_the_pair(flags_on, fixture) -> None:
    """An id convergence and a guess at an acronym deserve different trust."""
    llm = SpecOracleLLM(fixture)
    results, _summary = asyncio.run(
        run_clustering(fixture_dedup_rows(fixture), llm)
    )
    reasons = [r.reasoning or "" for r in results.values()]
    assert any("[id]" in reason for reason in reasons)
