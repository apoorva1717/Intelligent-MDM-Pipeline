"""Every output column that is also an input must be readable back as one.

`/enrich/file` emits `api.output_columns.RESPONSE_COLUMNS`. Feed that workbook
back in — which people do, and which the dedup phase does by construction — and
any column whose header is not an accepted input alias arrives empty. The value
is not corrected or flagged; it is silently dropped.

That is not hypothetical. Two columns were in this state, and the golden set
found them by losing 54 cells across 57 of its 99 records:

* `Terms of Payment` — the output header, while the only input aliases were
  `Terms of Payment Contact` / `terms_of_payment_contact`.
* `PO Box` — read, but used only to decide whether a PO Box found in a *street*
  was a second one and therefore a conflict. `po_box_extracted` was written from
  the street and nowhere else, so a record arriving with its PO Box already in
  the right column shipped without it.

So the inventory below is pinned. A column moving between the two lists is
either a fix or a regression, and both should be deliberate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.output_columns import RESPONSE_COLUMNS  # noqa: E402
from api.routes import _input_alias_to_field, _norm_header, _rows_to_records  # noqa: E402

#: Output columns that are legitimately produced by enrichment and have no
#: input to round-trip. Re-reading one would let a previous run's answer stand
#: in for evidence, which is the opposite of what the provenance rules want.
ENRICHMENT_PRODUCED = {
    "Operating Name", "Operating Name Provenance",
    "Domain", "Department Domain",
    "Flag for Review", "Flag Codes", "Flagged Fields", "Flag Reason",
    "Error", "Record Type", "ROR ID", "LEI ID",
    "Name 1 Provenance", "Name 2 Provenance", "Domain Provenance",
    "Record Type Provenance", "ROR ID Provenance", "LEI ID Provenance",
}

#: SAP address columns the pipeline WRITES but cannot READ. A clean SAP export
#: carries these — the corpus behind the golden set has `Building`,
#: `Room`, `Suite`, `Floor`, `Unit`, `Unloading Point` and `Mail Code`
#: populated on most rows — and every one of them would be dropped on the way
#: in, exactly as `PO Box` was.
#:
#: Not fixed here, and deliberately not: each needs the same decision `PO Box`
#: needed (what happens when the input column AND the street both name a value)
#: and none is exercised by the golden set, whose reference INPUT rows leave all
#: eight blank. Recorded so the gap is a known quantity rather than a surprise.
#: See `golden_eval_report.md`.
LATENT_INPUT_GAPS = {
    "Suite", "Building", "Floor", "Room", "Unit",
    "Mail Stop", "Unloading Point", "Mail Code",
}


def _unreadable() -> set[str]:
    aliases = _input_alias_to_field()
    return {
        header for header in RESPONSE_COLUMNS.values()
        if _norm_header(header) not in aliases
    }


class TestTheOutputSchemaRoundTrips:
    def test_the_inventory_of_unreadable_columns_is_exactly_what_we_expect(self):
        assert _unreadable() == ENRICHMENT_PRODUCED | LATENT_INPUT_GAPS

    def test_no_column_is_in_both_lists(self):
        assert not (ENRICHMENT_PRODUCED & LATENT_INPUT_GAPS)

    def test_every_other_output_column_is_readable_as_an_input(self):
        readable = set(RESPONSE_COLUMNS.values()) - _unreadable()
        # The passthrough spine: identity, address, and the SAP codes.
        for header in ("Customer", "Name 1", "Name 2", "City", "Postal Code",
                       "Region", "Country/Region Key", "PO Box",
                       "Terms of Payment", "House Number", "Care Of",
                       "Contact", "Email"):
            assert header in readable, header


class TestTheTwoColumnsTheGoldenSetFound:
    """Regression tests for the specific losses, by their output header."""

    @staticmethod
    def _record(**columns):
        return _rows_to_records([{
            "Customer": "X", "Name 1": "Acme", "Country/Region Key": "US",
            **columns,
        }])[0]

    def test_terms_of_payment_is_read_under_the_header_it_is_written_as(self):
        assert self._record(**{
            "Terms of Payment": "NT30",
        }).terms_of_payment_contact == "NT30"

    def test_the_older_contact_spelling_still_works(self):
        assert self._record(**{
            "Terms of Payment Contact": "NT30",
        }).terms_of_payment_contact == "NT30"

    def test_po_box_is_read(self):
        assert self._record(**{"PO Box": "750314"}).po_box == "750314"


async def _address(street=None, po_box=None):
    from enrichment.address_processing import process_address
    return await process_address(
        record_id="x",
        name1="Southern Methodist University", name2=None, name3=None,
        street=street, street_2=None, street_3=None,
        city="Dallas", state="TX", zip_code="75275", country="US",
        po_box=po_box, care_of_enriched=None, llm_client=None,
    )


class TestAnInputPoBoxSurvivesTheAddressStage:
    """`po_box_extracted` used to be written only from a street field."""

    @pytest.mark.asyncio
    async def test_a_po_box_already_in_its_own_column_is_carried_through(self):
        result = await _address(street="3215 DANIEL AVE", po_box="750314")
        assert result.po_box_extracted == "750314"

    @pytest.mark.asyncio
    async def test_a_po_box_in_a_street_is_still_extracted_when_the_column_is_empty(self):
        # Extracted with its label, as it always was — this test pins that the
        # street path is untouched, not that its output form changed.
        result = await _address(street="PO BOX 4755")
        assert result.po_box_extracted == "PO BOX 4755"
        assert result.street_cleaned is None

    @pytest.mark.asyncio
    async def test_the_record_column_wins_a_conflict_and_the_conflict_is_raised(self):
        # Two different PO Boxes. The record's own column is the one to keep —
        # the street is the value in doubt — and the disagreement is reported
        # rather than silently resolved.
        result = await _address(street="PO BOX 999", po_box="750314")
        assert result.po_box_extracted == "750314"
        assert "G3-ADDR-005" in (result.address_issues or [])
