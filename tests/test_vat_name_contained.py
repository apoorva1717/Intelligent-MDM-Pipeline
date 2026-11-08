"""NAME_CONTAINED (2026-11-07): SAP's name stands whole, in order, inside the
page's company name. On the organisation's own site, at its address or in
its town, it counts as matched for AUTO_ACCEPT; anywhere else it is
NAME_PARTIAL as before. Company data: public names from the reviewed test
files, or obviously fake (Muster)."""

from __future__ import annotations

import pytest

from vat.entity_match import NAME_CONTAINED, name_contained, name_match, parse_name
from vat.lookup import AUTO_ACCEPT, MANUAL_REVIEW, FoundResult


def _contained(sap: str, page: str) -> bool:
    return name_contained(parse_name(sap, sap=True).core, parse_name(page).core)


class TestTheDefinition:
    @pytest.mark.parametrize("sap, page", [
        ("Universität Bielefeld", "Universität Bielefeld, Centrum für Biotechnologie"),
        ("Staatliches Bauamt Erlangen", "Staatliches Bauamt Erlangen-Nürnberg"),
        ("Staatliches Bauamt Erlangen Nürnberg", "Staatliches Bauamt Erlangen-Nürnberg"),   # hyphen / space
        ("Sächsisches Textilforschungsinst eV", "Sächsisches Textilforschungsinstitut e.V. (STFI)"),  # cut at 35
        ("LMU Klinikum", "LMU Klinikum München, Anstalt des öffentlichen Rechts"),
        ("TCS Terminal Chemical Services GmbH", "TCS TERMINAL CHEMICALS SERVICES GmbH & Co. KG"),  # plural s
        ("Muster Straßenbau", "Muster Strassenbau und Tiefbau"),                              # ß / ss
        ("Müller Forschung", "Mueller Forschung Süd"),                                       # umlaut / ue
    ])
    def test_contained(self, sap, page):
        assert _contained(sap, page)

    @pytest.mark.parametrize("sap, page", [
        ("CPT Zwei GmbH", "CPT GmbH"),                                                       # a SAP word is missing
        ("Kranich-Apotheke Oliver Pelzer eK", "Kranich-Apotheke in Eppendorf"),
        ("Hahn-Schickard-Gesellschaft für angewandte Forschung", "Hahn-Schickard"),
        ("Refratechnik Ceramics GmbH", "Refratechnik Holding GmbH"),
        ("Vitesco Technologies GmbH", "Vitesco Technologies Group AG"),                      # a group's name
        ("Muster Logistik GmbH", "Muster Logistik Verwaltungs GmbH"),                        # the general partner
        ("Bielefeld Universität", "Universität Bielefeld"),                                  # order matters
        ("Muster Te", "Muster Technik"),                                                     # a short cut word is no prefix
    ])
    def test_not_contained(self, sap, page):
        assert not _contained(sap, page)

    def test_the_matcher_writes_it_instead_of_name_partial(self):
        v = name_match("LMU Klinikum München, Anstalt des öffentlichen Rechts", ("LMU Klinikum", None, None, None))
        assert v.issues == (NAME_CONTAINED,) and v.match == "PARTIAL"
        assert name_match("CPT GmbH", ("CPT Zwei GmbH", None, None, None)).issues == ("NAME_PARTIAL",)

    def test_another_legal_form_still_differs(self):
        v = name_match("Muster Forschung AG", ("Muster Forschung GmbH", None, None, None))
        assert any(i.startswith("LEGAL_FORM_DIFFERS") for i in v.issues)


def _result(status="FOUND_UNVERIFIED:VIES_PENDING", **kw):
    base = dict(value="DE811128135", source_url="https://www.muster.example/impressum", source_type="COMPANY_IMPRESSUM",
                name_match="PARTIAL", address_match="SAME_TOWN", validation_status="PENDING",
                issues=(NAME_CONTAINED,), attributed_to="Muster Klinikum München")
    return FoundResult("llm_web", status, **{**base, **kw})


class TestTheGate:
    def test_own_site_in_the_right_town_is_auto_accept(self):
        r = _result()
        assert r.decision == AUTO_ACCEPT and NAME_CONTAINED in r.all_issues
        assert _result(address_match="EXACT").decision == AUTO_ACCEPT

    @pytest.mark.parametrize("change", [
        dict(source_type="DIRECTORY"),                                    # not the organisation's own site (class C)
        dict(address_match="DIFFERENT_TOWN"),
        dict(address_match="NO_ADDRESS"),
        dict(issues=(NAME_CONTAINED, "OTHER_VAT_IDS_ON_PAGE:DE129274235")),
        dict(issues=(NAME_CONTAINED, "EXTRACTED_BY_PATTERN")),
    ])
    def test_anywhere_else_it_is_name_partial(self, change):
        r = _result(**change)
        assert r.decision == MANUAL_REVIEW
        assert "NAME_PARTIAL" in r.all_issues and NAME_CONTAINED not in r.all_issues

    @pytest.mark.parametrize("issue", ["PARENT_ENTITY_CANDIDATE", "SUCCESSOR_CANDIDATE", "LEGAL_FORM_DIFFERS:gmbh/ag",
                                       "SISTER_ENTITY_ON_PAGE"])
    def test_other_blocking_issues_still_block(self, issue):
        assert _result(issues=(NAME_CONTAINED, issue)).decision == MANUAL_REVIEW

    def test_vies_still_decides_the_sap_update(self):
        from vat.lookup import sap_update_eligible

        r = _result()
        assert not sap_update_eligible(r.decision, r.validation_status)
        assert sap_update_eligible(_result(validation_status="VALID", status="FOUND_VERIFIED").decision, "VALID")
