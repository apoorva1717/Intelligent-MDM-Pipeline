"""Step AH: the number pattern with group separators, the operator / Träger
follow, the on-site document search, the NOT_FOUND reasons. Company data:
public Impressum data from the fixtures, or obviously fake."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.mocks.vat_lookup_mock import page
from tests.test_vat_hybrid import BOSCH, IMPRESSUM, NOTHING, Model, _engine, found, linked, read, run
from vat import lookup_grounded
from vat.entity_match import vat_candidates
from vat.lookup import AUTO_ACCEPT, CONF_MEDIUM, MANUAL_REVIEW, NOT_FOUND, FoundResult, lookup_kpis
from vat.lookup_web import (
    ENTITY_DEFUNCT, NO_NUMBER_ON_OWN_SITE, NO_OWN_SITE, OWN_SITE_BLOCKED, OWN_SITE_SCRIPT_ONLY, PARENT_ONLY,
    not_found_reason, with_not_found_reason,
)
from vat.normalize import assess_format
from vat.web_pages import defunct_statement, operator_host_matches, operator_link

UKM = ("Universitätsklinikum Münster Albert-Schweitzer-Campus 1 48149 Münster Umsatzsteuer "
       "Umsatzsteuer-Identifikationsnummer gemäß §27 a Umsatzsteuergesetz: DE 212/275/725 Aufsichtsbehörde")


class TestPattern:
    """Rule 1."""

    @pytest.mark.parametrize("text, found_", [
        (UKM, ["DE212275725"]),
        ("USt-IdNr.: DE212/275/725", ["DE212275725"]),
        ("USt-IdNr.: DE-212275725", ["DE212275725"]),
        ("USt-IdNr.: DE 212-275-725", ["DE212275725"]),
        ("USt-IdNr.: DE 811.128.135", ["DE811128135"]),
        ("W-IdNr. DE123456789-00001", []),       # a Wirtschafts-IdNr., not a VAT ID
        ("Tel. DE 0251 83-55555", []),            # eleven digits: no
        ("Steuernummer 212/275/725", []),         # no DE prefix
    ])
    def test_candidates(self, text, found_):
        assert vat_candidates(text) == found_

    @pytest.mark.parametrize("raw", ["DE 212/275/725", "DE212/275/725", "DE-212275725", "DE 212 275 725"])
    def test_normalised_before_the_check_digit(self, raw):
        f = assess_format(raw, "DE")
        assert f.normalized == "DE212275725" and f.status == "FORMAT_OK"

    @pytest.mark.asyncio
    async def test_a_slashed_number_on_an_impressum_is_found(self, tmp_path):
        body = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen USt-IdNr.: DE 811/128/135"
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found(quote="USt-IdNr.: DE 811/128/135"))])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.decision == AUTO_ACCEPT


ENI_IMPRINT = "https://eni-g.de/imprint"
ENI_TEXT = ("Imprint European Neuroscience Institute Göttingen ENI-G, a Joint Initiative of the University "
            "Medical Center Göttingen (UMG) and the Max Planck Institute for Multidisciplinary Sciences. It is an "
            "organizational unit within the UMG. Click here for the imprint of UMG. Contact address European "
            "Neuroscience Institute Göttingen Grisebachstr. 5 37077 Göttingen Germany")
ENI_LINKS = [["↗ UMG", "https://www.umg.eu"], ["↗ University Medical Center Göttingen (UMG)", "https://umg.eu/"],
             ["↗ Click here for the imprint of UMG.", "https://www.umg.eu/impressum/"],
             ["↗ Max Planck Institute for Multidisciplinary Sciences", "https://www.mpinat.mpg.de"]]
UMG_IMPRESSUM = "https://www.umg.eu/impressum/"
UMG_TEXT = ("Impressum Universitätsmedizin Göttingen Georg-August-Universität Stiftung Öffentlichen Rechts "
            "Robert-Koch-Straße 40 37075 Göttingen Umsatzsteuer-Identifikationsnummer: DE 286 005 408")
ENI = replace(BOSCH, customer="13028353", names=("European Neuroscience Institute", None, None, None),
              street="Grisebachstr.", house_number="5", postal_code="37077", city="GÖTTINGEN")


class TestOperatorFollow:
    """Rule 2."""

    def test_eni_names_umg_and_links_its_imprint(self):
        assert operator_link(ENI_TEXT, ENI_LINKS, ENI_IMPRINT) == (
            "University Medical Center Göttingen (UMG)", UMG_IMPRESSUM)

    def test_fta_mineral_is_a_company_of_gapco(self):
        text = ("Impressum FTA Mineral GmbH Braunfelser Str. 8 35638 Leun. Die FTA Mineral GmbH ist ein "
                "Unternehmen der GAPCO GmbH. Registergericht Wetzlar HRB 9999")
        links = [["Impressum", "https://www.fta-mineral.example/impressum"], ["GAPCO", "https://www.gapco.example/"]]
        assert operator_link(text, links, "https://www.fta-mineral.example/impressum") == (
            "GAPCO GmbH", "https://www.gapco.example/impressum")

    @pytest.mark.parametrize("text", [
        "Impressum Muster Klinik gGmbH. Träger: Diakonie Musterland e.V., Musterweg 1",
        "Betreiber: Diakonie Musterland e.V., Musterweg 1",
        "Das Institut ist eine gemeinsame Einrichtung der Diakonie Musterland und der Stadt",
    ])
    def test_the_german_phrases(self, text):
        links = [["Diakonie Musterland", "https://www.diakonie-musterland.example/"]]
        assert operator_link(text, links, "https://www.klinik.example/impressum") == (
            operator_link(text, links, "https://www.klinik.example/impressum")[0],
            "https://www.diakonie-musterland.example/impressum")

    def test_no_phrase_no_link(self):
        assert operator_link("Impressum Robert Bosch GmbH, Gerlingen", ENI_LINKS, IMPRESSUM) is None
        # the phrase, but no link to that organisation
        assert operator_link("Träger: Diakonie Musterland e.V.", [["Kontakt", "/kontakt"]], IMPRESSUM) is None

    def test_own_host_rules_on_the_operator(self):
        name = "University Medical Center Göttingen (UMG)"
        assert operator_host_matches(UMG_IMPRESSUM, name, UMG_TEXT)       # the acronym is the domain
        assert not operator_host_matches("https://www.example-agentur.example/impressum", name, "Impressum Agentur")

    @pytest.mark.asyncio
    async def test_eni_to_umg_is_a_parent_candidate_medium(self, tmp_path):
        pages = {ENI_IMPRINT: linked(ENI_IMPRINT, ENI_TEXT, ENI_LINKS), UMG_IMPRESSUM: page(UMG_IMPRESSUM, UMG_TEXT)}
        umg = found("DE286005408", UMG_IMPRESSUM, "Umsatzsteuer-Identifikationsnummer: DE 286 005 408",
                    "Universitätsmedizin Göttingen")
        model = Model(run=[run("q", [ENI_IMPRINT])], read=[read(umg)])
        r = await _engine(tmp_path, model, pages, vat_web_operator_follow=True).lookup(ENI)
        assert r.value == "DE286005408" and r.source_url == UMG_IMPRESSUM
        assert "PARENT_ENTITY_CANDIDATE" in r.all_issues
        assert r.decision == MANUAL_REVIEW and r.confidence == CONF_MEDIUM
        assert r.usage["operator"]["read"] is True and r.usage["fetches"] == 2

    @pytest.mark.asyncio
    async def test_off_by_setting(self, tmp_path):
        pages = {ENI_IMPRINT: linked(ENI_IMPRINT, ENI_TEXT, ENI_LINKS), UMG_IMPRESSUM: page(UMG_IMPRESSUM, UMG_TEXT)}
        model = Model(run=[run("q", [ENI_IMPRINT])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, pages, vat_web_operator_follow=False).lookup(ENI)
        assert r.value is None and "operator" not in r.usage


CALW_HOST = "https://www.kreis-calw.example"
CALW_IMPRESSUM = f"{CALW_HOST}/impressum"
CALW_PDF = f"{CALW_HOST}/media/custom/2442_5492_1.PDF"
CALW = replace(BOSCH, customer="13053434", names=("Kreiskliniken Calw gGmbH", None, None, None),
               street="Eduard-Conz-Straße", house_number="6", postal_code="75365", city="CALW")


class TestSiteSearch:
    """Rule 3."""

    def _pages(self):
        impressum = page(CALW_IMPRESSUM, "Impressum Kreiskliniken Calw gGmbH Eduard-Conz-Straße 6 75365 Calw "
                                         "Geschäftsführer Max Muster Registergericht Stuttgart HRB 1234")
        pdf = page(CALW_PDF, "Kreiskliniken Calw gGmbH Eduard-Conz-Straße 6 75365 Calw Rechnungsanschrift "
                             "USt-IdNr.: DE 248351721", title="Schreiben")
        return {CALW_IMPRESSUM: impressum, CALW_PDF: pdf}

    @pytest.mark.asyncio
    async def test_calw_number_in_a_document_on_its_own_host(self, tmp_path, monkeypatch):
        pdf = self._pages()[CALW_PDF]
        monkeypatch.setattr(lookup_grounded, "fetch_pdf", lambda url, timeout=15.0: pdf)
        model = Model(run=[run("q1", [CALW_IMPRESSUM]), run("site", [CALW_PDF])],
                      read=[read(found("DE248351721", CALW_PDF, "USt-IdNr.: DE 248351721",
                                       "Kreiskliniken Calw gGmbH"))])
        r = await _engine(tmp_path, model, self._pages(), vat_web_site_search=True).lookup(CALW)
        site_query = [u for k, u in model.calls if k == "run"][1]
        assert "site:kreis-calw.example Kreiskliniken Calw gGmbH USt-IdNr" in site_query
        assert r.value == "DE248351721" and r.source_url == CALW_PDF and r.source_class == "A"
        assert r.decision == AUTO_ACCEPT
        assert "second" not in [k for k, _ in model.calls]          # it is search 2
        assert r.usage["site_search"]["found"] == "DE248351721"

    @pytest.mark.asyncio
    async def test_only_hits_on_that_host(self, tmp_path):
        other = "https://www.steuerberater.example/ust-idnr.pdf"
        model = Model(run=[run("q1", [CALW_IMPRESSUM]), run("site", [other])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, self._pages(), vat_web_site_search=True).lookup(CALW)
        assert r.value is None and r.usage["site_search"]["on_host"] == 0
        assert r.status == f"{NOT_FOUND}:{NO_NUMBER_ON_OWN_SITE}"


class TestNotFoundReasons:
    """Rule 4."""

    def _usage(self, **h):
        host = {"kind": "own", "fetched": ["https://x.example/impressum"], "rejected": None, "answered": False, **h}
        return {"hosts": {"x.example": host}, "pages": {"https://x.example/impressum": {}}}

    def test_each_reason(self):
        assert not_found_reason(self._usage(answered=True))[0] == NO_NUMBER_ON_OWN_SITE
        assert not_found_reason(self._usage(blocked=403))[0] == OWN_SITE_BLOCKED
        u = self._usage()
        u["pages"]["https://x.example/impressum"] = {"needs_js": True}
        assert not_found_reason(u)[0] == OWN_SITE_SCRIPT_ONLY
        assert not_found_reason({"hosts": {"uni.example": {"kind": "parent", "answered": True, "fetched": []}},
                                 "pages": {}})[0] == PARENT_ONLY
        assert not_found_reason({})[0] == NO_OWN_SITE
        u = self._usage(answered=True)
        u["pages"]["https://x.example/impressum"] = {"defunct": "Die Muster AG ist in Insolvenz."}
        assert not_found_reason(u) == (ENTITY_DEFUNCT, "Die Muster AG ist in Insolvenz.", "https://x.example/impressum")

    def test_defunct_needs_the_entity_nearby(self):
        assert defunct_statement("Die Veritas AG befindet sich in Insolvenz.", ("Veritas AG",))
        assert defunct_statement("Wir beraten Unternehmen in Insolvenz.", ("Veritas AG",)) is None
        assert defunct_statement("Die Muster GmbH wurde auf die Neu GmbH verschmolzen.", ("Muster GmbH",))

    def test_status_flag_and_kpis(self):
        r = with_not_found_reason(FoundResult("llm_web", NOT_FOUND, usage=self._usage(answered=True)))
        assert r.status == "NOT_FOUND:NO_NUMBER_ON_OWN_SITE" and "NOT_FOUND_REASON:NO_NUMBER_ON_OWN_SITE" in r.flags
        assert r.decision == "NOT_FOUND"
        kpis = lookup_kpis([replace(r, entity="a")])
        assert kpis["not_found_reasons"] == {NO_NUMBER_ON_OWN_SITE: 1} and kpis["not_found_rate"] == 1.0

    def test_other_statuses_are_left(self):
        r = FoundResult("llm_web", "LOOKUP_INCOMPLETE:HTTP_403")
        assert with_not_found_reason(r) is r

    @pytest.mark.asyncio
    async def test_a_lookup_reports_its_reason(self, tmp_path):
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        body = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen Registergericht Stuttgart HRB 14000"
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(BOSCH)
        assert r.status == f"{NOT_FOUND}:{NO_NUMBER_ON_OWN_SITE}"


class TestLiveRunFixes:
    """What the AH re-run showed."""

    @pytest.mark.asyncio
    async def test_the_operator_fetch_is_on_top_of_a_spent_budget(self, tmp_path):
        # ENI-G on the AH run: four fetches on its own pages, none left.
        pages = {ENI_IMPRINT: linked(ENI_IMPRINT, ENI_TEXT, ENI_LINKS), UMG_IMPRESSUM: page(UMG_IMPRESSUM, UMG_TEXT)}
        umg = found("DE286005408", UMG_IMPRESSUM, "Umsatzsteuer-Identifikationsnummer: DE 286 005 408",
                    "Universitätsmedizin Göttingen")
        model = Model(run=[run("q", [ENI_IMPRINT])], read=[read(umg)])
        r = await _engine(tmp_path, model, pages, vat_web_operator_follow=True, vat_web_max_fetches=1).lookup(ENI)
        assert r.value == "DE286005408" and r.usage["fetches"] == 2 and r.usage["operator_follows"] == 1

    @pytest.mark.asyncio
    async def test_a_town_in_the_name_must_be_in_the_directory_entry(self, tmp_path):
        from tests.test_vat_step_af import entry
        other = "https://www.unternehmensverzeichnis.org/kreiskliniken-reutlingen-ggmbh-reutlingen-2500000000002"
        results = [{"url": other, "title": "Kreiskliniken Reutlingen gGmbH | unternehmensverzeichnis.org"}]
        model = Model(run=[run("q", []), run("agg", [other], results=results)], read=[read(NOTHING)])
        from tests.mocks.vat_lookup_mock import FakeWeb
        web = FakeWeb({other: entry(other, "Kreiskliniken Reutlingen gGmbH", "DE229860840")})
        r = await _engine(tmp_path, model, {}, web=web, vat_aggregator_fallback=True).lookup(CALW)
        assert r.value is None and other not in web.fetched

    @pytest.mark.asyncio
    async def test_site_hits_have_their_own_fetches(self, tmp_path, monkeypatch):
        # Kreiskliniken Calw on the AH run: its own pages spent the budget first.
        t = TestSiteSearch()
        pdf = t._pages()[CALW_PDF]
        monkeypatch.setattr(lookup_grounded, "fetch_pdf", lambda url, timeout=15.0: pdf)
        model = Model(run=[run("q1", [CALW_IMPRESSUM]), run("site", [f"{CALW_HOST}/seite", CALW_PDF])],
                      read=[read(found("DE248351721", CALW_PDF, "USt-IdNr.: DE 248351721",
                                       "Kreiskliniken Calw gGmbH"))])
        r = await _engine(tmp_path, model, t._pages(), vat_web_site_search=True, vat_web_max_fetches=1).lookup(CALW)
        assert r.value == "DE248351721" and r.usage["pages"][CALW_PDF]["round"] == "site"
