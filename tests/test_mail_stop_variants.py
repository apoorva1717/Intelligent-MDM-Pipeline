"""Mail-stop markers in a street field.

"MS" is the common abbreviation for Mail Stop and appears with every
separator SAP exports produce: a space ("MS 42"), a hyphen ("MS-4"), a
colon ("Mail Stop: NE-L3"), a hash ("MS#RD45"), the slashed form
("M/S 456"), and the one-word spelling ("Mailstop 456"). Only the
space-separated form used to be recognised; the rest were left in the
cleaned street value.
"""

import pytest

from enrichment.address_processing import process_address


async def _addr(street, street_2=None):
    return await process_address(
        record_id="t", name1="Acme Inc", name2=None, name3=None,
        street=street, street_2=street_2, street_3=None,
        city="Gainesville", state="FL", zip_code="32601", country="US",
        po_box=None, care_of_enriched=None, llm_client=None,
    )


@pytest.mark.parametrize("street,expected", [
    ("123 Main St MS 456", "456"),
    ("123 Main St MS-456", "456"),
    ("123 Main St MS: 456", "456"),
    ("123 Main St MS#456", "456"),
    ("123 Main St MS. 456", "456"),
    ("123 Main St M/S 456", "456"),
    ("123 Main St Mailstop 456", "456"),
    ("123 Main St Mail Stop 456", "456"),
    ("500 TECH DR STE 210 MS-4", "4"),
    ("1 Cyclotron Rd, MS 50A-4119", "50A-4119"),
])
@pytest.mark.asyncio
async def test_marker_variants_route_to_mail_stop(street, expected):
    r = await _addr(street)
    assert r.mail_stop == expected
    assert "MS" not in (r.street_cleaned or "").upper().split()


@pytest.mark.asyncio
async def test_hyphenated_mail_stop_is_not_split_into_a_mail_code():
    """In a secondary slot the bare mail-code scan runs first. It must not
    claim "RD45" out of "MS-RD45" and strand the marker."""
    r = await _addr("123 Main St", "Bldg 7 Suite 200 MS-RD45 Receiving Dept")
    assert r.mail_stop == "RD45"
    assert r.mail_code is None
    assert r.building == "7"
    assert r.suite == "200"


@pytest.mark.asyncio
async def test_state_abbreviation_is_not_read_as_a_mail_stop():
    """A full address in street 1 is split before sub-location extraction,
    so the "MS" in "Jackson, MS 39201" stays a state."""
    r = await _addr("500 Tech Dr, Jackson, MS 39201")
    assert r.mail_stop is None
    assert r.street_cleaned == "500 Tech Dr"


@pytest.mark.asyncio
async def test_a_value_less_marker_is_still_only_flagged():
    r = await _addr("123 Main St", "MS")
    assert r.mail_stop is None
    assert "G4-ADDR-008" in r.address_issues
