"""VAT_Name_Match (vat/name_match.py): VIES name vs Name + Name 2–4.

The VIES-side spellings are the ones VIES returned live for the VAT test
export on 2026-10-18 ("SA SANOFI WINTHROP INDUSTRIE", "AMAZON EU SARL" …).
"""

from __future__ import annotations

import pytest

from vat.name_match import (
    EXACT,
    EXACT_WITH_NAME_2_4,
    FUZZY,
    MATCH,
    MISMATCH,
    NO_INPUT_NAME,
    NO_MATCH,
    NOT_AVAILABLE,
    NOT_DISCLOSED,
    PARTIAL,
    TOKEN_SUBSET,
    VIES_NOT_VALID,
    match_name,
    name_tokens,
)


def _m(vies_name, name, *extra, status="VALID"):
    return match_name(status, vies_name, name, list(extra))


class TestMatch:
    @pytest.mark.parametrize("vies, ours", [
        ("GOOGLE IRELAND LIMITED", "Google Ireland Limited"),
        ("SA SANOFI WINTHROP INDUSTRIE", "Sanofi Winthrop Industrie"),  # leading form
        ("AMAZON EU SARL", "Amazon EU S.a r.l."),                        # dotted form
        ("PAYPAL (EUROPE) S.A R.L. ET CIE, S.C.A.", "PayPal (Europe) S.a r.l. et Cie, S.C.A."),
        ("Red Bull GmbH", "RED BULL"),                                   # case, form omitted
        ("UNIVERSITAET STUTTGART", "Universität Stuttgart"),             # umlaut
        ("IRELAND GOOGLE LIMITED", "Google Ireland Ltd."),               # word order
    ])
    def test_exact_after_normalisation(self, vies, ours):
        r = _m(vies, ours)
        assert (r.status, r.rule, r.flag) == (MATCH, EXACT, "NAME_MATCH:EXACT")

    def test_match_needs_name_2(self):
        r = _m("MICROSOFT IRELAND OPERATIONS LIMITED", "Microsoft Ireland", "Operations Limited")
        assert (r.status, r.rule) == (MATCH, EXACT_WITH_NAME_2_4)


class TestPartial:
    def test_token_subset(self):
        r = _m("SIEMENS AG", "Siemens AG Werk Erlangen")
        assert (r.status, r.rule) == (PARTIAL, TOKEN_SUBSET)

    def test_fuzzy(self):
        r = _m("MICROSOFT IRELAND OPERATIONS LIMITED", "Microsoft Ireland Operation Ltd")
        assert (r.status, r.rule) == (PARTIAL, FUZZY)
        assert r.score >= 88


class TestMismatch:
    @pytest.mark.parametrize("vies, ours", [
        ("TOTALLY DIFFERENT LTD", "Acme GmbH"),
        ("BMW AG", "Bayerische Motoren Werke AG"),  # an acronym is not expanded
    ])
    def test_no_match(self, vies, ours):
        r = _m(vies, ours)
        assert (r.status, r.rule) == (MISMATCH, NO_MATCH)


class TestNotAvailable:
    @pytest.mark.parametrize("status", ["INVALID", "SERVICE_UNAVAILABLE", "ERROR", "NOT_CHECKED"])
    def test_status_not_valid(self, status):
        r = _m("GOOGLE IRELAND LIMITED", "Google Ireland Limited", status=status)
        assert (r.status, r.rule) == (NOT_AVAILABLE, VIES_NOT_VALID)

    @pytest.mark.parametrize("vies", [None, "", "---"])
    def test_de_es_name_not_disclosed(self, vies):
        assert (_m(vies, "Siemens AG").status, _m(vies, "Siemens AG").rule) == (
            NOT_AVAILABLE, NOT_DISCLOSED,
        )

    def test_no_input_name(self):
        r = _m("GOOGLE IRELAND LIMITED", None, None, "  ")
        assert (r.status, r.rule) == (NOT_AVAILABLE, NO_INPUT_NAME)


class TestTokens:
    def test_legal_forms_dropped_wherever_they_stand(self):
        assert name_tokens("SA SANOFI WINTHROP INDUSTRIE") == ["sanofi", "winthrop", "industrie"]
        assert name_tokens("Henkel AG & Co. KGaA") == ["henkel"]

    def test_a_name_that_is_only_a_legal_form_keeps_it(self):
        assert name_tokens("GmbH") == ["gmbh"]

    def test_deterministic(self):
        assert _m("SIEMENS AG", "Siemens AG Werk Erlangen") == _m("SIEMENS AG", "Siemens AG Werk Erlangen")
