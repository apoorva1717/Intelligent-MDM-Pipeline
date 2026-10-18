"""VAT normaliser + offline format check (vat/normalize.py).

Valid numbers are python-stdnum's own documented examples; invalid ones differ
from them by one check digit. No real personal data.
"""

from __future__ import annotations

import pytest

from vat.normalize import (
    FORMAT_ERROR,
    FORMAT_OK,
    MISSING,
    NO_PREFIX_NO_COUNTRY,
    NON_EU_PREFIX,
    NOT_EU,
    PREFIX_COUNTRY_MISMATCH,
    PREFIX_FROM_COUNTRY_KEY,
    assess_format,
    check_format,
    normalize_vat,
)


class TestRequiredCases:
    def test_valid_de(self):
        r = assess_format("DE136695976", "DE")
        assert (r.status, r.normalized, r.prefix, r.flags) == (
            FORMAT_OK, "DE136695976", "DE", (),
        )
        assert r.vies_eligible

    def test_de_bad_checksum(self):
        r = assess_format("DE136695977", "DE")
        assert r.status == FORMAT_ERROR
        assert r.format_error == "InvalidChecksum"
        assert not r.vies_eligible

    def test_spaces_dots_and_dashes_are_removed(self):
        r = assess_format(" de 136.695-976 ", "DE")
        assert (r.status, r.normalized) == (FORMAT_OK, "DE136695976")
        assert r.raw == " de 136.695-976 "  # the raw value is kept verbatim

    def test_missing_prefix_taken_from_country_key(self):
        r = assess_format("136695976", "DE")
        assert (r.status, r.normalized) == (FORMAT_OK, "DE136695976")
        assert r.flags == (PREFIX_FROM_COUNTRY_KEY,)

    def test_gr_prefix_becomes_el(self):
        r = assess_format("GR094014201", "GR")
        assert (r.status, r.normalized, r.prefix) == (FORMAT_OK, "EL094014201", "EL")
        assert PREFIX_COUNTRY_MISMATCH not in r.flags

    def test_us_row_is_not_eu(self):
        r = assess_format("12-3456789", "US")
        assert r.status == NOT_EU
        assert r.prefix is None
        assert not r.vies_eligible

    @pytest.mark.parametrize("raw", [None, "", "   ", " . - "])
    def test_empty_value_is_missing(self, raw):
        r = assess_format(raw, "DE")
        assert (r.status, r.normalized) == (MISSING, None)

    @pytest.mark.parametrize("key", ["US", "CH", "GB"])
    def test_empty_value_outside_vies_is_not_eu(self, key):
        # Test_Notes row 31: a US customer with no VAT ID → NOT_EU, not MISSING.
        assert assess_format(None, key).status == NOT_EU

    def test_empty_value_without_country_key_is_missing(self):
        assert assess_format(None, None).status == MISSING

    def test_prefix_country_mismatch_is_flagged_not_fixed(self):
        r = assess_format("DE136695976", "FR")
        assert (r.status, r.normalized) == (FORMAT_OK, "DE136695976")
        assert r.flags == (PREFIX_COUNTRY_MISMATCH,)


class TestGreeceAndNorthernIreland:
    def test_bare_number_with_gr_key_gets_el(self):
        r = assess_format("094014201", "GR")
        assert (r.status, r.normalized) == (FORMAT_OK, "EL094014201")

    def test_el_prefix_with_gr_key_is_not_a_mismatch(self):
        assert assess_format("EL094014201", "GR").flags == ()

    def test_xi_with_gb_key_is_checked_and_not_a_mismatch(self):
        r = assess_format("XI 980 7806 84", "GB")
        assert (r.status, r.normalized, r.flags) == (FORMAT_OK, "XI980780684", ())

    def test_bare_number_with_gb_key_is_not_guessed_as_xi(self):
        r = assess_format("980780684", "GB")
        assert r.status == NOT_EU

    def test_bare_number_with_explicit_xi_key_gets_xi(self):
        assert assess_format("980780684", "XI").normalized == "XI980780684"


class TestNonEuPrefix:
    def test_swiss_uid_with_de_key_is_kept_and_flagged(self):
        r = assess_format("CHE-123.456.789", "DE")
        assert (r.status, r.normalized, r.prefix, r.flags) == (
            FORMAT_ERROR, "CHE123456789", None, (NON_EU_PREFIX,),
        )
        assert not r.vies_eligible

    @pytest.mark.parametrize("raw", ["GB980780684", "NO123456789MVA"])
    def test_other_non_eu_prefixes_with_eu_key(self, raw):
        r = assess_format(raw, "FR")
        assert (r.status, r.normalized, r.flags) == (FORMAT_ERROR, raw, (NON_EU_PREFIX,))

    def test_non_eu_prefix_with_non_eu_key_is_not_eu(self):
        r = assess_format("CHE-123.456.789", "CH")
        assert (r.status, r.flags) == (NOT_EU, ())

    def test_unprefixed_spanish_number_starting_with_a_letter(self):
        # The rule is "no leading letters at all", so a bare Spanish CIF
        # (A28015865) is not given ES. Pinned so a change to it is deliberate.
        r = assess_format("A28015865", "ES")
        assert (r.status, r.flags) == (FORMAT_ERROR, (NON_EU_PREFIX,))
        assert assess_format("ESA28015865", "ES").status == FORMAT_OK


class TestEdgeCases:
    def test_austrian_u_is_kept(self):
        assert assess_format("ATU13585627", "AT").status == FORMAT_OK

    def test_austrian_u_is_never_added(self):
        r = assess_format("13585627", "AT")
        assert (r.normalized, r.status) == ("AT13585627", FORMAT_ERROR)

    def test_no_prefix_no_country(self):
        r = assess_format("136695976", None)
        assert r.status == NOT_EU
        assert r.flags == (NO_PREFIX_NO_COUNTRY,)

    def test_country_key_is_trimmed_and_upper_cased(self):
        assert assess_format("136695976", " de ").normalized == "DE136695976"

    def test_normalize_alone_leaves_eu_status_open(self):
        assert normalize_vat("DE136695976", "DE").status is None

    def test_check_format_reports_stdnum_reason(self):
        assert check_format("DE136695976") == (FORMAT_OK, None)
        assert check_format("DE12345") == (FORMAT_ERROR, "InvalidLength")
