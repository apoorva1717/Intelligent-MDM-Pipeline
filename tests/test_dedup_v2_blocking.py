"""Delivery-point blocking — ``DEDUP_V2_BLOCKING`` (change B).

Three layers, tested separately because they fail separately:

1. :func:`dedup.address.parse_address` — a pure function over one row's
   address columns. Pinned on the exact street strings the change request
   names, so the parse is a stated contract rather than whatever the
   implementation happens to do.
2. :func:`dedup.address.address_compatible` and the block keys — can these two
   rows be at one door, and which blocks does a row join.
3. The whole clustering pass over the 200-row fixture: which groups blocking
   makes comparable, which it keeps apart, and that it stays deterministic.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from dedup.address import (  # noqa: E402
    ParsedAddress,
    address_compatible,
    block_keys,
    parse_address,
    street_match,
    streets_compatible,
)
from dedup.models import DedupRow  # noqa: E402
from dedup.signatures import build_blocks  # noqa: E402
from tests.dedup_v2_support import (  # noqa: E402
    V2_FLAGS,
    SpecOracleLLM,
    fixture_dedup_rows,
    fixture_row_dicts,
    load_fixture,
    run_clustering,
)


def _row(street: str = "", house_no: str = "", postal: str = "12345",
         city: str = "Town", country: str = "US", row_id: str = "r") -> DedupRow:
    return DedupRow(
        row_id=row_id, street=street or None, house_no=house_no or None,
        postal_code=postal or None, city=city or None, country=country or None,
    )


@pytest.fixture
def flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in V2_FLAGS:
        monkeypatch.setenv(flag, "true")


@pytest.fixture
def blocking_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only B. Proves the flag stands on its own, not just alongside C and D."""
    monkeypatch.setenv("DEDUP_V2_BLOCKING", "true")
    monkeypatch.setenv("DEDUP_V2_NAME2", "false")
    monkeypatch.setenv("DEDUP_V2_ID_CONFLICT", "false")


# ---------------------------------------------------------------------------
# 1. parse_address
# ---------------------------------------------------------------------------

#: (Street 1, expected house, expected street_core, expected house_hint).
STREET_CASES = [
    ("35 Landsdowne St",         "35",    "landsdowne street",        ""),
    ("N. Stemmons Freeway",      "",      "north stemmons freeway",   ""),
    ("4500 S. Lancaster Rd 113", "4500",  "south lancaster road",     ""),
    ("1 Hoag Dr Truck 2",        "1",     "hoag drive",               ""),
    ("7777 Forest Ln",           "7777",  "forest lane",              ""),
    ("Middlesex TPK",            "",      "middlesex turnpike",       ""),
    ("1855 Folsom St.",          "1855",  "folsom street",            ""),
    ("FM 521 Rd",                "",      "fm 521 road",              ""),
    ("47-111 Monroe St",         "47111", "monroe street",            ""),
    ("2550 SR Mary Columbia Dr", "2550",  "sr mary columbia drive",   ""),
    ("38",                       "",      "38",                       "38"),
    ("Technology",               "",      "technology",               ""),
]


@pytest.mark.parametrize(
    ("street", "house", "core", "hint"), STREET_CASES,
    ids=[case[0] for case in STREET_CASES],
)
def test_parse_address_street_line(street, house, core, hint) -> None:
    parsed = parse_address(_row(street=street))
    assert (parsed.house, parsed.street_core, parsed.house_hint) == (house, core, hint)


def test_a_bare_number_is_not_a_house_number() -> None:
    """"38" in the street line names no door, and must not act like one.

    The row it comes from (PAVIR, 94304) sits in a zip that also holds a real
    house 38's worth of neighbours; treating the fragment as a house number
    would block it against them on the strength of a number that survived some
    upstream truncation. It is kept as a hint and the row is house-less.
    """
    parsed = parse_address(_row(street="38"))
    assert parsed.house == ""
    assert parsed.house_less is True
    assert parsed.house_hint == "38"


def test_a_number_with_a_street_beside_it_is_a_house_number() -> None:
    parsed = parse_address(_row(street="3801 Miranda Ave"))
    assert (parsed.house, parsed.street_core, parsed.house_less) == (
        "3801", "miranda avenue", False,
    )


def test_the_house_number_column_wins_over_the_street_line() -> None:
    parsed = parse_address(_row(street="Middlesex Turnpike", house_no="45A"))
    assert (parsed.house, parsed.street_core) == ("45a", "middlesex turnpike")


def test_a_directional_never_ends_the_street_core() -> None:
    """Otherwise "Charles E Young Dr So" would stop at "E" and lose the street.

    The two UCLA addresses differ only in house number (675 vs 695), so the
    street core has to survive intact for the pair to be comparable at all.
    """
    assert parse_address(_row(street="675 Charles E Young Dr So")).street_core == (
        "charles east young drive"
    )
    assert parse_address(_row(street="695 Charles E Young Dr S")).street_core == (
        "charles east young drive"
    )


def test_zip5_truncates_a_us_zip_plus_four() -> None:
    assert parse_address(_row(postal="44106-2623")).zip5 == "44106"
    assert parse_address(_row(postal="07065-4646")).zip5 == "07065"


def test_a_non_us_postcode_keeps_its_whole_normalised_value() -> None:
    """Only the US has a 5+4; truncating anything else folds real places together."""
    parsed = parse_address(_row(postal="SW1A 1AA", country="GB"))
    assert parsed.zip5 == "sw1a 1aa"


# ---------------------------------------------------------------------------
# 2. Compatibility and block keys
# ---------------------------------------------------------------------------

def test_streets_compatible_accepts_a_missing_leading_word() -> None:
    """Jaro-Winkler reads this pair at 0.72 because it is prefix-weighted.

    They are the same street; the token rule is what says so.
    """
    assert streets_compatible("east qume drive", "qume drive") is True


def test_streets_compatible_accepts_an_abbreviated_saint_name() -> None:
    assert streets_compatible("sr mary columbia drive", "sister mary columba") is True


def test_streets_compatible_rejects_a_disagreeing_number() -> None:
    """A number inside a street core is part of the street's NAME."""
    assert streets_compatible("11 mile road", "13 mile road") is False


def test_streets_compatible_accepts_a_dropped_street_type() -> None:
    assert streets_compatible("fm 521 road", "fm 521") is True


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (_row(street="Manning Rd", house_no="40"), _row(street="40 Manning Rd"), "exact"),
        (_row(street="Folsom St", house_no="1855", postal="94103"),
         _row(street="1855 Folsom St.", postal="94143"), "fuzzy"),
        (_row(street="Manning Rd", house_no="40"),
         _row(street="Fortune Dr", house_no="15"), "incompatible"),
        (_row(street="Technology", postal="90003"),
         _row(street="", postal="90030"), "partial"),
        (_row(street="Qume Dr", house_no="2372", postal="95131"),
         _row(street="Monroe St", house_no="2372", postal="92201"), "incompatible"),
    ],
    ids=["same door", "zip typo", "different house", "no house either side", "different zip and street"],
)
def test_address_compatible(left, right, expected) -> None:
    assert address_compatible(parse_address(left), parse_address(right)) == expected


def test_a_zip_transposition_is_one_edit_not_two() -> None:
    """90003/90030 is one keystroke. Damerau, not plain Levenshtein."""
    a = parse_address(_row(street="Technology", house_no="1", postal="90003"))
    b = parse_address(_row(street="Technology", house_no="1", postal="90030"))
    assert address_compatible(a, b) != "incompatible"


def test_street_match_never_says_incompatible() -> None:
    """The model is told about the STREET, not about the delivery point.

    Two rows only reach one prompt after their zip and house already matched,
    so a failed string comparison of the street is not evidence that they are
    at different addresses — and a label that implied it would invite the model
    to reject on a question that was already settled.
    """
    a = parse_address(_row(street="Adelbert Rd", house_no="2109"))
    b = parse_address(_row(street="Circle Dr", house_no="2109"))
    assert street_match(a, b) == "differs"
    assert street_match(a, a) == "exact"
    assert street_match(a, parse_address(_row(house_no="2109"))) == "unknown"
    assert set(ParsedAddress.__dataclass_fields__) >= {"house", "street_core"}


def test_a_house_bearing_row_emits_the_zip_and_city_keys() -> None:
    keys = block_keys(parse_address(_row(street="Folsom St", house_no="1855",
                                         postal="94103", city="San Francisco")))
    assert keys == ["z:US|94103|1855", "c:US|san francisco|1855"]


def test_a_house_less_row_emits_only_its_fallback_key() -> None:
    """No wildcard attach: a row with no door joins no door's block.

    This is the whole of the "may LINK, never MERGE" rule for address-less
    rows — it is enforced by the key, before any similarity is computed, so
    nothing downstream can undo it.
    """
    assert block_keys(parse_address(_row(street="Mark Ave", postal="94035"))) == [
        "f:US|94035"
    ]
    assert block_keys(parse_address(_row(postal="94035"))) == ["f:US|94035"]


# ---------------------------------------------------------------------------
# 3. Over the fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixture() -> dict:
    return load_fixture()


def _blocks_of(rows) -> dict[str, str]:
    return {
        row.row_id: block_id
        for block_id, block in build_blocks(rows).items()
        for row in block.rows
    }


#: Pairs the delivery point must put in ONE block. Each is a spelling
#: difference v1 blocked on: house number in the street line, a dropped street
#: type, a zip typo, a misspelt street, a zip+4.
SAME_BLOCK = [
    ("Bruker: house column vs house in the street line", "13238351", "IC1280"),
    ("Merck Rahway: house column vs house in the street line", "13189884", "13334413"),
    ("UCSF: 94103 vs 94143, joined on city + house", "13161437", "13342545"),
    ("Covia: FM 521 Rd vs FM 521", "13113215", "13128534"),
    ("UTRGV: W University Dr vs W Universtiy, 78539 vs 78539-2999", "13144897", "13223387"),
    ("Medical City: Forrest Ln vs Forest Ln", "13145693", "13344098"),
    ("GES: E Qume Dr vs 2372 Qume Dr", "13223469", "13234427"),
    ("St Elizabeth: SR Mary Columbia Dr vs Sister Mary Columba", "13336040", "13336285"),
    ("JFK: 47-111 Monroe St", "13334236", "13344636"),
    ("CWRU: 44106-2623 vs 44106", "13130623", "13141440"),
]


@pytest.mark.parametrize(("label", "left", "right"), SAME_BLOCK, ids=[c[0] for c in SAME_BLOCK])
def test_one_delivery_point_is_one_block(label, left, right, blocking_only, fixture) -> None:
    blocks = _blocks_of(fixture_dedup_rows(fixture))
    assert blocks[left] == blocks[right], (
        f"{label}: {left} in {blocks[left]}, {right} in {blocks[right]}"
    )


#: Pairs the delivery point must keep in DIFFERENT blocks — a different house
#: number on the same street, or a house-less row against a real door.
DIFFERENT_BLOCK = [
    ("UCLA 675 vs 695", "13342488", "13349159"),
    ("Bruker 40 Manning vs 15 Fortune", "13238351", "13016575"),
    ("UCSF 1855 Folsom vs 1550 4th", "13161437", "13334454"),
    ("UTRGV 1201 W University vs 1407 E Freddy Gonzalez", "13144897", "13044748"),
    ("Labcorp 655 Fairfield vs 800 Technology", "13348403", "13346510"),
    ("Assay Depot 505 Lomas Santa Fe vs 125 N Acacia", "13035402", "13346804"),
    ("NASA 239 Mark Ave vs the address-less ISD rows", "13036862", "13120409"),
    ("PAVIR 3801 Miranda vs the address-less rows", "13345935", "13345937"),
    ("UTSA 7703 Floyd Curl vs the address-less THSU row", "13044882", "13046339"),
]


@pytest.mark.parametrize(
    ("label", "left", "right"), DIFFERENT_BLOCK, ids=[c[0] for c in DIFFERENT_BLOCK]
)
def test_different_delivery_points_are_different_blocks(
    label, left, right, blocking_only, fixture
) -> None:
    blocks = _blocks_of(fixture_dedup_rows(fixture))
    assert blocks[left] != blocks[right], f"{label}: both landed in {blocks[left]}"


def test_house_less_rows_never_share_a_block_with_a_house(blocking_only, fixture) -> None:
    """The invariant behind every "may LINK, never MERGE" expectation."""
    rows = fixture_dedup_rows(fixture)
    for block in build_blocks(rows).values():
        house_less = {r.row_id for r in block.rows if parse_address(r).house_less}
        housed = {r.row_id for r in block.rows} - house_less
        assert not (house_less and housed), (
            f"block {block.block_id} mixes house-less {sorted(house_less)} with "
            f"housed {sorted(housed)}"
        )
        assert block.unverified == bool(house_less), (
            f"block {block.block_id}: unverified={block.unverified} but "
            f"{len(house_less)} of {len(block.rows)} rows are house-less"
        )


@pytest.mark.asyncio
async def test_a_cluster_of_house_less_rows_routes_to_review(flags_on, fixture) -> None:
    """PAVIR's two address-less rows are one entity — and a reviewer's call.

    They share a cluster id, because the names say they are the same
    organisation. They route to manual_review, because nothing said they share
    a door. Both halves matter: dropping the id would lose the finding, and
    routing it "cluster" would assert something no address supports.
    """
    results, _summary = await run_clustering(
        fixture_dedup_rows(fixture), SpecOracleLLM(fixture)
    )
    left, right = results["13345790"], results["13345937"]
    assert left.cluster_id is not None and left.cluster_id == right.cluster_id
    assert left.routing == right.routing == "manual_review"
    assert "unverified delivery point" in (left.reasoning or "")


@pytest.mark.asyncio
async def test_a_lone_house_less_row_is_still_just_unique(flags_on, fixture) -> None:
    """The demotion is about an asserted cluster, not about the row.

    A singleton makes no claim, so there is nothing for a reviewer to check.
    """
    results, _summary = await run_clustering(
        fixture_dedup_rows(fixture), SpecOracleLLM(fixture)
    )
    assert results["13046339"].routing == "unique"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def _assignments(results) -> dict[str, tuple]:
    return {
        row_id: (r.cluster_id, r.routing, r.llm_flag, r.reasoning)
        for row_id, r in results.items()
    }


def test_shuffled_input_gives_identical_output_and_call_count(flags_on, fixture) -> None:
    """Blocking unions keys, not rows, and every block is sorted by Customer.

    So the input's row order can reach neither the block membership, nor the
    signature ids, nor the order the model is asked things in. If it could, two
    exports of one batch that differed only in sort order would produce
    different cluster ids for the same records.
    """
    rows = fixture_dedup_rows(fixture)
    shuffled = list(rows)
    random.Random(20260904).shuffle(shuffled)
    assert [r.row_id for r in shuffled] != [r.row_id for r in rows]

    ordered_llm, shuffled_llm = SpecOracleLLM(fixture), SpecOracleLLM(fixture)
    ordered, ordered_summary = asyncio.run(run_clustering(rows, ordered_llm))
    reshuffled, shuffled_summary = asyncio.run(run_clustering(shuffled, shuffled_llm))

    assert _assignments(ordered) == _assignments(reshuffled)
    assert ordered_summary.llm_calls == shuffled_summary.llm_calls
    assert ordered_llm.calls == shuffled_llm.calls


def test_two_runs_produce_a_byte_identical_workbook(flags_on, fixture) -> None:
    """The end of the chain: the file a reviewer actually receives.

    Equal assignments are not the same claim as an equal file — the sheet
    assembly, the debug sheet and the column order all sit between them.
    """
    from api.routes import _build_dedup_xlsx, _rows_to_dedup_rows
    from config import Settings
    from dedup.adjudicator import cluster_blocks

    row_dicts = fixture_row_dicts(fixture)
    headers = fixture["input_columns"]

    def build() -> bytes:
        rows = _rows_to_dedup_rows(row_dicts)
        response = asyncio.run(
            cluster_blocks(rows, SpecOracleLLM(fixture), settings=Settings())
        )
        return _build_dedup_xlsx(headers, row_dicts, rows, response)

    assert build() == build()
