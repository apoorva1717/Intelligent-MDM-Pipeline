"""Step AF: the hybrid method's rules (budget, own-host recognition, ranking,
name preparation, domain hint, extraction, the legal-notice block, legal
form, the A–F classes, the aggregator fallback). Company data: public
Impressum data from the fixtures, or obviously fake."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from search.page_fetcher import PageFetchResult
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.test_vat_hybrid import (
    BOSCH, HELPER_USAGE, IMPRESSUM, NOTHING, NO_SEARCH_2, QUOTE, USAGE, Model, _engine, found, linked, read, run,
)
from vat import lookup_grounded, name2_classifier
from vat.lookup import AUTO_ACCEPT, CONF_LOW, CONFLICT, LOOKUP_INCOMPLETE, NOT_FOUND, LookupInput, run_lookups
from vat.lookup_web import ALL_OWN_HOSTS_BLOCKED, DOMAIN_HINT
from vat.name_prep import CONTINUATION, CONTINUATION_TRUNCATED, DEPARTMENT, OTHER, prepare
from vat.no_number_class import classify
from vat.web_pages import (
    CANDIDATE, INSTITUTION, OWN, PARENT_CANDIDATE, host_check, host_kind, institutional_kind, name_acronyms,
    select_pages,
)

HOME = "https://www.bosch.example/"


def rec(names, city="GERLINGEN", **kw):
    names = (*names, *([None] * (4 - len(names))))
    return replace(BOSCH, names=names, city=city, **kw)


def rank(*items):
    return json.dumps({"ranked": [dict(zip(("url", "type", "belongs_to_entity", "reason"), i)) for i in items]}), \
        [], HELPER_USAGE


def second(hint=None, visited=()):
    return (json.dumps({"query": "x", "name_used": "y", "why_this_query": "z", "domain_hint": hint,
                        "results": []}), list(visited), USAGE)


@pytest.fixture
def no_browser(monkeypatch):
    """A refused page is refused as a browser too; no archive."""
    monkeypatch.setattr(lookup_grounded, "fetch_as_browser", lambda url, fetcher, timeout=15.0: PageFetchResult(
        url=url, status=403))


class TestOwnHosts:
    """Rules 4–7."""

    @pytest.mark.parametrize("names, city, url, kind", [
        (("FAU Erlangen-Nürnberg", "NAT Max Schaldach Stiftungs"), "ERLANGEN", "https://www.fau.de/impressum", OWN),
        (("Universitätsklinikum Münster", "GB Finanzen"), "MÜNSTER", "https://www.ukm.de/impressum", OWN),
        (("Universitätsmedizin Göttingen", "Stiftung öffentlichen Rechts"), "GÖTTINGEN", "https://www.umg.eu/", OWN),
        (("NMI Naturwissenschaftliches und", "und Medizinisches Institut an der"), "REUTLINGEN",
         "https://www.nmi.de/impressum", OWN),
        (("Karlsruher Institut für Technologie",), "KARLSRUHE", "https://www.kit.edu/impressum.php", OWN),
        (("ACL GmbH",), "ROTTENBURG", "https://www.acl-online.de/impressum", OWN),  # GENERIC, caps acronym
        (("BMW Group Werk Landshut",), "LANDSHUT", "https://www.einvoicing.bmwgroup.com/de/impressum.html", OWN),
        # Rule 5: a prefix with ≥ 6 letters in common.
        (("Bundespolizeiabteilung Blumberg",), "AHRENSFELDE", "https://www.bundespolizei.de/impressum", OWN),
        # Rule 6: the city never identifies the host ...
        (("FAU Erlangen-Nürnberg",), "ERLANGEN", "https://www.erlangen.de/impressum", None),
        (("Universität Heidelberg", "Dez Finanzen"), "HEIDELBERG", "https://www.heidelberg.de/impressum", None),
        (("Hochschule Karlsruhe",), "KARLSRUHE", "https://www.ph-karlsruhe.de/impressum", None),
        (("Hochschule Karlsruhe",), "KARLSRUHE", "https://www.karlshochschule.de/", None),
        # ... except beside the record's own institution type, or for a town.
        (("Universität Heidelberg", "Dez Finanzen"), "HEIDELBERG", "https://www.uni-heidelberg.de/impressum", OWN),
        (("Stadt Erlangen",), "ERLANGEN", "https://www.erlangen.de/impressum", OWN),
    ])
    def test_host_kind(self, names, city, url, kind):
        assert host_kind(url, rec(names, city), plan=prepare(names)) == kind

    def test_acronyms(self):
        assert {"fau"} <= name_acronyms("FAU Erlangen-Nürnberg")
        assert "ukm" in name_acronyms("Universitätsklinikum Münster")
        assert "umg" in name_acronyms("Universitätsmedizin Göttingen Stiftung öffentlichen Rechts")
        assert "kit" in name_acronyms("Karlsruher Institut für Technologie")

    @pytest.mark.parametrize("names, url, kind", [
        (("Europäische Kommission", "Gemeinsame Forschungsstelle"), "https://joint-research-centre.ec.europa.eu/x",
         INSTITUTION),
        (("Bundesamt für Strahlenschutz",), "https://www.bfs.bund.de/impressum", INSTITUTION),
        (("Landeskriminalamt Rheinland-Pfalz",), "https://lka.polizei.rlp.de/impressum", INSTITUTION),
        (("Max-Planck-Institut für Kohlenforschung",), "https://www.kofo.mpg.de/impressum", INSTITUTION),
        (("Robert Bosch GmbH",), "https://ec.europa.eu/x", None),  # a company: not its parent domain
    ])
    def test_institutional_parent_domains(self, names, url, kind):
        assert institutional_kind(url, prepare(names)) == kind


class TestRanking:
    def test_heuristic_order(self):
        results = [{"url": "https://jobs.bosch.example/"}, {"url": "https://www.bosch.example/produkte"},
                   {"url": "https://www.bosch-group.example/impressum"}]
        picked, _ = select_pages(results, BOSCH, plan=prepare(BOSCH.names))
        # An Impressum path first, the job portal last.
        assert [u for u, _ in picked] == ["https://www.bosch-group.example/impressum",
                                          "https://www.bosch.example/produkte", "https://jobs.bosch.example/"]

    @pytest.mark.asyncio
    async def test_model_vouches_for_a_host_the_name_check_confirms(self, tmp_path):
        # fau.de-style: no token match; the model says it belongs; its Impressum names the entity.
        site = "https://www.campus-site.example/"
        imp = "https://www.campus-site.example/impressum"
        model = Model(run=[run("q", [site])], read=[read(found(url=imp))],
                      rank=[rank((site, "own_site", "yes", "the organisation's own site"))])
        pages = {site: linked(site, "Willkommen", [["Impressum", "/impressum"]]), imp: page(imp, BODY_BOSCH)}
        r = await _engine(tmp_path, model, pages, vat_web_ranking="model").lookup(BOSCH)
        assert r.value == "DE811128135" and r.usage["ranking_1"] == "model"
        assert r.usage["ranked_1"][0]["used"] == CANDIDATE and r.usage["hosts"]["campus-site.example"]["kind"] == OWN
        rank_call = next(c for c in r.usage["calls"] if c["call"] == "rank_1")
        assert (rank_call["call_kind"], rank_call["deployment"]) == ("ranking", "MDM-Apoorva-gpt-5.4-mini")
        assert ("rank", "none") in model.efforts  # reasoning "none" on the helper model

    @pytest.mark.asyncio
    async def test_model_judgement_never_replaces_the_name_check(self, tmp_path):
        site = "https://www.other-company.example/impressum"
        model = Model(run=[run("q", [site])], read=[read(found(url=site))],
                      rank=[rank((site, "impressum", "yes", "looks right"))])
        pages = {site: page(site, "Impressum Müller Elektro AG 12345 Musterstadt " + QUOTE)}
        r = await _engine(tmp_path, model, pages, vat_web_ranking="model").lookup(BOSCH)
        assert r.value is None and r.usage["hosts"]["other-company.example"]["rejected"] == "NAME_NOT_ON_PAGE"

    @pytest.mark.asyncio
    async def test_a_directory_pointer_is_only_its_website(self, tmp_path):
        directory = "https://www.bionity.example/companies/bosch"
        site = "https://www.bosch.example/impressum"
        model = Model(run=[run("q", [directory], results=[{"url": directory, "title": "Robert Bosch GmbH",
                                                             "website": site}])], read=[read(found(url=site))],
                      rank=[rank((directory, "directory_pointer", "likely", "names the website"))])
        web = FakeWeb({site: page(site, BODY_BOSCH)})
        r = await _engine(tmp_path, model, {}, web=web, vat_web_ranking="model").lookup(BOSCH)
        assert r.value == "DE811128135" and directory not in web.fetched

    @pytest.mark.asyncio
    async def test_a_vouched_host_needs_every_word_of_a_generic_name(self, tmp_path):
        # Hoffmann Engineering Services GmbH, Hoffmann Group's Impressum.
        hes = rec(("Hoffmann Engineering Services GmbH",), city="MUENCHEN")
        url = "https://www.tools-group.example/imprint"
        body = "Imprint Hoffmann GmbH Qualitätswerkzeuge Haberlandstr. 55 81241 München " + QUOTE
        model = Model(run=[run("q", [url])], read=[read(found(url=url, name="Hoffmann GmbH Qualitätswerkzeuge"))],
                      rank=[rank((url, "impressum", "likely", "Hoffmann Group imprint"))])
        r = await _engine(tmp_path, model, {url: page(url, body)}, vat_web_ranking="model").lookup(hes)
        assert r.value is None and r.usage["hosts"]["tools-group.example"]["rejected"] in (
            "NOT_VERIFIED", "NAME_NOT_ON_PAGE")

    @pytest.mark.asyncio
    async def test_failed_ranking_call_falls_back_to_rule_8(self, tmp_path):
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found())], rank=[RuntimeError("down")])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY_BOSCH)},
                          vat_web_ranking="model").lookup(BOSCH)
        assert r.value == "DE811128135" and "RANKING_FALLBACK" in r.usage["ranking_1"]


BODY_BOSCH = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen " + QUOTE


class TestBudget:
    """Rules 1, 2 and 9."""

    @pytest.mark.asyncio
    async def test_probe_is_one_fetch_impressum_then_imprint(self, tmp_path):
        imprint = "https://www.bosch.example/imprint"
        model = Model(run=[run("q", [HOME])], read=[read(found(url=imprint))])
        pages = {HOME: page(HOME, "Robert Bosch GmbH Startseite Produkte Karriere " * 20, title="Home"),
                 imprint: page(imprint, BODY_BOSCH)}
        r = await _engine(tmp_path, model, pages, vat_web_max_follows=0).lookup(BOSCH)
        assert r.value == "DE811128135" and r.usage["probes"] == 1 and r.usage["fetches"] == 2

    @pytest.mark.asyncio
    async def test_probe_after_a_stale_link_404(self, tmp_path):
        # Kappler: the result's /kontakt link is gone, the host is up.
        stale = "https://www.bosch.example/kontakt?lang=en"
        model = Model(run=[run("q", [stale])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY_BOSCH)}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.usage["probes"] == 1

    def test_a_directory_website_without_a_scheme(self):
        results = [{"url": "https://web2.cylex.de/firma/x", "website": "www.bosch.example"}]
        picked, _ = select_pages(results, BOSCH, plan=prepare(BOSCH.names))
        assert picked == [("https://www.bosch.example", "pointer")]

    @pytest.mark.asyncio
    async def test_no_probe_on_a_pointer_host(self, tmp_path):
        directory = "https://www.northdata.de/Robert-Bosch"
        site = "https://www.bosch-pointer.example/"
        model = Model(run=[run("q", [directory], results=[{"url": directory, "title": "Robert Bosch GmbH",
                                                             "website": site}])], read=[read(NOTHING)])
        web = FakeWeb({site: page(site, "Robert Bosch GmbH Startseite " * 30, title="Home")})
        await _engine(tmp_path, model, {}, web=web, vat_web_max_follows=0).lookup(BOSCH)
        assert "https://www.bosch-pointer.example/impressum" not in web.fetched

    @pytest.mark.asyncio
    async def test_blocked_own_host_hands_the_budget_to_the_next(self, tmp_path, no_browser):
        blocked = "https://www.bosch.example/impressum"
        other = "https://www.bosch.de/impressum"
        model = Model(run=[run("q", [blocked, other])], read=[read(found(url=other))])
        r = await _engine(tmp_path, model, {blocked: 403, other: page(other, BODY_BOSCH)}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.usage["hosts"]["bosch.example"]["blocked"] == 403

    @pytest.mark.asyncio
    async def test_incomplete_only_when_every_own_host_refused(self, tmp_path, no_browser):
        urls = ["https://www.bosch.example/impressum", "https://www.bosch.de/impressum"]
        model = Model(run=[run("q", urls)], read=[read(NOTHING)])
        engine = _engine(tmp_path, model, {u: 403 for u in urls})

        async def no_archive(url):
            return None

        engine._wayback = no_archive
        r = await engine.lookup(BOSCH)
        assert r.status == f"{LOOKUP_INCOMPLETE}:HTTP_403" and ALL_OWN_HOSTS_BLOCKED in r.flags
        assert r.usage["own_hosts_all_blocked"] is True

    @pytest.mark.asyncio
    async def test_a_kontakt_page_does_not_stop_search_2(self, tmp_path):
        kontakt = "https://www.bosch.example/kontakt"
        model = Model(run=[run("q", [kontakt])], read=[read(NOTHING)])
        pages = {kontakt: page(kontakt, "Kontakt Robert Bosch GmbH 70839 Gerlingen Telefon 0711 400 40990")}
        r = await _engine(tmp_path, model, pages, vat_web_max_follows=0).lookup(BOSCH)
        assert r.usage["own_host_answered_1"] is False and [k for k, _ in model.calls][:2] == ["run", "second"]


class TestNamePrep:
    """Rule 10."""

    @pytest.mark.parametrize("names, cls, entity", [
        (("Chemisches und Veterinaer", "Untersuchungsamt Sigmaringen"), CONTINUATION_TRUNCATED,
         "Chemisches und Veterinaer Untersuchungsamt Sigmaringen"),
        (("NMI Naturwissenschaftliches und", "und Medizinisches Institut an"), CONTINUATION_TRUNCATED,
         "NMI Naturwissenschaftliches und Medizinisches Institut an"),
        (("Pfizer Manufacturing Frankfurt GmbH", "& Co KG"), CONTINUATION, "Pfizer Manufacturing Frankfurt GmbH & Co KG"),
        (("Eberhard Karls Universität Tübingen", "Proteom Centrum Tübingen"), DEPARTMENT,
         "Eberhard Karls Universität Tübingen"),
        (("Albert-Ludwigs-Universität Freiburg", "Dez 4 Bau und Infrastruktur"), DEPARTMENT,
         "Albert-Ludwigs-Universität Freiburg"),
        (("K+S Minerals & Agriculture GmbH", "Werk Bernburg"), OTHER, "K+S Minerals & Agriculture GmbH"),
        (("Chemisches und Veterinärunter-", "suchungsamt Freiburg"), CONTINUATION_TRUNCATED,
         "Chemisches und Veterinäruntersuchungsamt Freiburg"),
        (("WIKA Alexander Wiegand SE & Co. KG", "Wika Retoure für Wareneingang WIKA"), DEPARTMENT,
         "WIKA Alexander Wiegand SE & Co. KG"),
    ])
    def test_classes(self, names, cls, entity):
        plan = prepare(names)
        assert (plan.cls, plan.entity) == (cls, entity)
        if cls == CONTINUATION_TRUNCATED:
            assert plan.acceptable == (entity, names[0])

    def test_site_in_name_2_is_a_branch(self):
        assert "BRANCH" in prepare(("K+S Minerals & Agriculture GmbH", "Werk Bernburg")).issues

    def test_a_join_at_the_length_limit_is_never_quoted(self):
        plan = prepare(("Polizeiinspektion Zentrale Dienste", "Sachsen-Anhalt"))
        assert plan.cls == CONTINUATION_TRUNCATED and plan.complete is False


class TestName2Classifier:
    """Rule 10b."""

    PLAN = prepare(("Polizeiinspektion Zentrale Dienste", "Sachsen-Anhalt"))

    def test_continuation_with_confidence_sets_the_search_name(self):
        new = name2_classifier.apply(self.PLAN, {"class": "CONTINUATION", "confidence": 0.9, "reason": "",
                                                 "legal_name": "Polizeiinspektion Zentrale Dienste Sachsen-Anhalt"})
        assert new.search_name == "Polizeiinspektion Zentrale Dienste Sachsen-Anhalt"
        assert new.acceptable == ("Polizeiinspektion Zentrale Dienste Sachsen-Anhalt",
                                  "Polizeiinspektion Zentrale Dienste")

    def test_low_confidence_is_other_and_other_classes_keep_the_rules(self):
        low = name2_classifier.apply(self.PLAN, {"class": "CONTINUATION", "confidence": 0.5, "reason": "",
                                                 "legal_name": "x"})
        assert (low.cls, low.search_name) == (OTHER, "Polizeiinspektion Zentrale Dienste")
        plain = prepare(("Radisson Blu Park Hotel & Conference Ct.", "Z.Hd. Frau Bittlingmayer"))
        assert name2_classifier.apply(plain, {"class": "DEPARTMENT", "confidence": 0.99, "reason": "",
                                              "legal_name": None}) is plain

    def test_a_confident_department_undoes_the_rule_join(self):
        dept = name2_classifier.apply(self.PLAN, {"class": "DEPARTMENT", "confidence": 0.96, "reason": "",
                                                  "legal_name": None})
        assert (dept.cls, dept.search_name, dept.acceptable) == (
            DEPARTMENT, "Polizeiinspektion Zentrale Dienste", ("Polizeiinspektion Zentrale Dienste",))

    def test_a_legal_name_with_words_not_in_the_record_is_refused(self):
        plan = prepare(("Radisson Blu Park Hotel & Conference Ct.", "Z.Hd. Frau Bittlingmayer"))
        out = name2_classifier.apply(plan, {"class": "CONTINUATION", "confidence": 0.94, "reason": "",
                                            "legal_name": "Radisson Blu Park Hotel & Conference Ct. RADEBEUL"})
        assert out.search_name == "Radisson Blu Park Hotel & Conference Ct."

    def test_eligibility(self):
        assert name2_classifier.eligible(self.PLAN, self.PLAN.names)
        assert not name2_classifier.eligible(prepare(("Robert Bosch GmbH", "Einkauf")), ("Robert Bosch GmbH", "Einkauf"))

    @pytest.mark.asyncio
    async def test_engine_uses_the_legal_name_and_logs_the_call(self, tmp_path):
        pi = rec(("Polizeiinspektion Zentrale Dienste", "Sachsen-Anhalt"), city="MAGDEBURG")
        answer = (json.dumps({"class": "CONTINUATION", "legal_name": "Polizeiinspektion Zentrale Dienste Sachsen-Anhalt",
                              "confidence": 0.9, "reason": "state in the name"}), [], HELPER_USAGE)
        model = Model(run=[run("q", [])], read=[read(NOTHING)], name2=[answer])
        r = await _engine(tmp_path, model, {}, vat_name2_classifier=True).lookup(pi)
        assert "Polizeiinspektion Zentrale Dienste Sachsen-Anhalt" in r.usage["query_1"]
        call = next(c for c in r.usage["calls"] if c["call"] == "name2")
        assert call["call_kind"] == "name2_classifier" and ("name2", "none") in model.efforts


class TestDomainHint:
    """Rule 11."""

    @pytest.mark.asyncio
    async def test_hint_counts_only_when_its_impressum_names_the_entity(self, tmp_path):
        hint = "https://www.rb-hint.example/impressum"
        model = Model(run=[run("q", [])], second=[second("rb-hint.example")], read=[read(found(url=hint))])
        r = await _engine(tmp_path, model, {hint: page(hint, BODY_BOSCH)}).lookup(BOSCH)
        assert r.value == "DE811128135" and DOMAIN_HINT in r.flags and r.usage["domain_hint_passed"] is True

    @pytest.mark.asyncio
    async def test_a_wrong_hint_is_not_used(self, tmp_path):
        hint = "https://www.wrong-hint.example/impressum"
        model = Model(run=[run("q", [])], second=[second("wrong-hint.example")], read=[read(found(url=hint))])
        pages = {hint: page(hint, "Impressum Müller Elektro AG 12345 Musterstadt " + QUOTE)}
        r = await _engine(tmp_path, model, pages).lookup(BOSCH)
        assert r.value is None and r.usage["domain_hint_passed"] is False


class TestExtraction:
    """Rule 12."""

    @pytest.mark.asyncio
    async def test_one_number_on_the_page_is_extracted(self, tmp_path):
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY_BOSCH)}).lookup(BOSCH)
        assert r.value == "DE811128135" and "EXTRACTED_BY_PATTERN" in r.all_issues

    @pytest.mark.asyncio
    async def test_two_numbers_are_candidates_never_picked(self, tmp_path):
        body = BODY_BOSCH + " Bosch Rexroth AG Umsatzsteuer-ID: DE 811 164 473"
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(BOSCH)
        assert r.decision == CONFLICT and "EXTRACTED_BY_PATTERN" not in r.all_issues

    @pytest.mark.asyncio
    async def test_the_page_names_whose_number_it_is(self, tmp_path):
        """2026-11-06: no model named the company; the notice's own name is
        compared (here the AG, not SAP's GmbH: LEGAL_FORM_DIFFERS)."""
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        body = BODY_BOSCH.replace("Robert Bosch GmbH", "Robert Bosch AG")
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(BOSCH)
        assert r.attributed_to == "Robert Bosch AG"
        assert "LEGAL_FORM_DIFFERS:gmbh/ag" in r.all_issues
        assert r.usage["evidence_snippet"].startswith("Impressum Robert Bosch AG Robert-Bosch-Platz 1")
        assert "DE811128135" in r.usage["evidence_snippet"].replace(" ", "")

    @pytest.mark.asyncio
    async def test_a_candidate_from_two_numbers_has_its_companys_name(self, tmp_path):
        body = BODY_BOSCH + " Bosch Rexroth AG Umsatzsteuer-ID: DE 811 164 473"
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(BOSCH)
        assert (r.value, r.attributed_to) == ("DE811128135", "Robert Bosch GmbH")


class TestLegalNotice:
    """Rules 13 and 14, and the legal-form principle."""

    KIRCHEN = rec(("Diakonie Klinikum Kirchen GmbH", "Einkauf"), city="KIRCHEN", postal_code="57548",
                  street=None, house_number=None)
    URL = "https://www.diakonie-klinikum-kirchen.example/impressum"
    TEXT = ("Impressum - Diakonie Klinikum Kirchen Start Über uns Aktuelles " + "Menü Kontakt Spenden " * 40
            + "Impressum Diakonie in Südwestfalen gGmbH Wichernstraße 40 57074 Siegen Geschäftsführer: Dr. Josef "
              "Rosenbauer Registergericht: Amtsgericht Siegen Registernummer: HRB 3432 "
              "Umsatzsteuer-Identifikationsnummer: DE 811 128 135")

    def test_the_notice_names_another_company(self):
        assert host_check(self.TEXT, self.URL, self.KIRCHEN, OWN, plan=prepare(self.KIRCHEN.names)) == \
            (None, PARENT_CANDIDATE)

    @pytest.mark.asyncio
    async def test_a_number_there_is_the_parents_never_auto(self, tmp_path):
        model = Model(run=[run("q", [self.URL])], read=[read(found(
            url=self.URL, quote="Umsatzsteuer-Identifikationsnummer: DE 811 128 135",
            name="Diakonie in Südwestfalen gGmbH"))])
        r = await _engine(tmp_path, model, {self.URL: page(self.URL, self.TEXT)}).lookup(self.KIRCHEN)
        assert r.value == "DE811128135" and "PARENT_ENTITY_CANDIDATE" in r.all_issues
        assert r.decision != AUTO_ACCEPT

    @pytest.mark.asyncio
    async def test_group_page_kreuznacher_diakonie(self, tmp_path):
        # The foundation writes its form first; the page also names the gGmbHs.
        skd = rec(("Stiftung kreuznacher diakonie",), city="BAD KREUZNACH", postal_code="55543",
                  street="Ringstraße", house_number="58")
        url = "https://www.kreuznacherdiakonie.example/service/impressum"
        body = ("Impressum Stiftung kreuznacher diakonie Kirchliche Stiftung des öffentlichen Rechts vertreten "
                "durch den Vorstand Ringstraße 58 55543 Bad Kreuznach Umsatzsteuer-Identifikationsnummer gemäß "
                "§ 27a Umsatzsteuergesetz: DE 811 128 135 Medizinisches Versorgungszentrum Stiftung kreuznacher "
                "diakonie gGmbH Ringstraße 58 55543 Bad Kreuznach")
        model = Model(run=[run("q", [url])], read=[read(found(
            url=url, quote="Umsatzsteuergesetz: DE 811 128 135", name="Stiftung kreuznacher diakonie"))])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(skd)
        assert r.value == "DE811128135" and not any(i.startswith("LEGAL_FORM_DIFFERS") for i in r.all_issues)

    @pytest.mark.asyncio
    async def test_another_legal_form_is_a_candidate_for_review(self, tmp_path):
        tk = rec(("ThyssenKrupp Industrial Solutions", "AG Business Unit Process"), city="DORTMUND")
        url = "https://www.thyssenkrupp-industrial-solutions.example/impressum"
        body = "Impressum ThyssenKrupp Industrial Solutions GmbH 44141 Dortmund " + QUOTE
        model = Model(run=[run("q", [url])], read=[read(found(url=url, name="ThyssenKrupp Industrial Solutions GmbH"))])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(tk)
        assert r.value == "DE811128135" and r.decision == "MANUAL_REVIEW"
        assert any(i.startswith("LEGAL_FORM_DIFFERS") for i in r.all_issues)

    def test_sap_without_a_form_never_fails_on_the_form(self):
        plan = prepare(("Robert Bosch",))
        assert host_check(BODY_BOSCH, IMPRESSUM, rec(("Robert Bosch",)), OWN, plan=plan)[0] is None


class TestNoNumberClass:
    """Rule 15."""

    def test_a_read_own_impressum_is_never_b(self):
        usage = {"hosts": {"x.example": {"answered": True, "rejected": None}},
                 "not_fetched": [{"url": "https://www.stadt.example/", "why": "fetch limit"}], "pages": {}}
        assert classify(usage, "DE455596231")[0] == "E"

    @pytest.mark.parametrize("usage, cls", [
        ({"hosts": {}, "pages": {}}, "A"),
        ({"hosts": {"x": {"answered": False}}, "pages": {}, "not_fetched": [{"url": "u"}]}, "B"),
        ({"hosts": {"x": {"answered": True}}, "pages": {"u": {"hits": ["DE111111111"]}}}, "C"),
        ({"hosts": {"x": {"answered": True}}, "pages": {"u": {"hits": ["DE111111111"],
                                                             "hits_in_sections": ["DE111111111"]}}}, "D"),
        ({"hosts": {"x": {"answered": True}}, "pages": {"u": {"hits": ["DE111111111"], "rejected": "R"}}}, "F"),
    ])
    def test_classes(self, usage, cls):
        assert classify(usage, "DE111111111")[0] == cls


def entry(url, title, number, extra=""):
    return page(url, f"{title} Industriestr. 1 74912 Kirchardt Umsatzsteuer-ID-Nr.: {number} "
                     f"Handelsregister-Nr.: HRA 100953 {extra}", title=f"{title} | unternehmensverzeichnis.org")


class TestAggregator:
    """Rule 17."""

    KACO = rec(("KACO GmbH & Co KG Dichtungswerke",), city="KIRCHARDT", postal_code="74912",
               street="Industriestr.", house_number="1")
    E = "https://www.unternehmensverzeichnis.org/kaco-gmbh--co-kg-kirchardt-2500504978433"

    def _model(self, results):
        agg = run("agg", [r["url"] for r in results], results=results)
        return Model(run=[run("q", []), agg], read=[read(NOTHING)])

    @pytest.mark.asyncio
    async def test_kaco_entry_is_a_review_candidate(self, tmp_path):
        model = self._model([{"url": self.E, "title": "KACO GmbH + Co. KG | unternehmensverzeichnis.org"}])
        r = await _engine(tmp_path, model, {self.E: entry(self.E, "KACO GmbH + Co. KG", "DE145801743")},
                          vat_aggregator_fallback=True).lookup(self.KACO)
        assert r.value == "DE145801743" and r.decision == "MANUAL_REVIEW" and r.confidence == CONF_LOW  # NAME_PARTIAL + SECONDARY_SOURCE
        assert "SECONDARY_SOURCE" in r.all_issues and "AGGREGATOR_FALLBACK" in r.flags

    @pytest.mark.asyncio
    async def test_other_companies_entries_are_never_scanned(self, tmp_path):
        other = "https://www.unternehmensverzeichnis.org/aytex-textil-vertriebs-gmbh-aachen-2500184536107"
        aix = rec(("AixMinerals",), city="AACHEN")
        model = self._model([{"url": other, "title": "Aytex Textil-Vertriebs GmbH | unternehmensverzeichnis.org"}])
        web = FakeWeb({other: entry(other, "Aytex Textil-Vertriebs GmbH", "DE123598151")})
        r = await _engine(tmp_path, model, {}, web=web, vat_aggregator_fallback=True).lookup(aix)
        assert r.value is None and other not in web.fetched

    @pytest.mark.asyncio
    async def test_a_partial_match_needs_the_records_first_word(self, tmp_path):
        # Ceramaret Meissen GmbH ↔ "Duravit Sanitärporzellan Meißen GmbH": another company.
        other = "https://www.unternehmensverzeichnis.org/duravit-sanitaerporzellan-meissen-gmbh-meissen-2500000000001"
        cer = rec(("Ceramaret Meissen GmbH",), city="MEISSEN")
        model = self._model([{"url": other, "title": "Duravit Sanitärporzellan Meissen GmbH"}])
        web = FakeWeb({other: entry(other, "Duravit Sanitärporzellan Meissen GmbH", "DE811378376")})
        r = await _engine(tmp_path, model, {}, web=web, vat_aggregator_fallback=True).lookup(cer)
        assert r.value is None and other not in web.fetched

    @pytest.mark.asyncio
    async def test_a_partial_name_is_a_candidate_with_name_partial(self, tmp_path):
        url = "https://www.unternehmensverzeichnis.org/keysight-technologies-deutschland-gmbh-boeblingen-2501100519426"
        ks = rec(("Keysight Technologies GmbH",), city="BOEBLINGEN")
        model = self._model([{"url": url, "title": "Keysight Technologies Deutschland GmbH"}])
        r = await _engine(tmp_path, model, {url: entry(url, "Keysight Technologies Deutschland GmbH", "DE266727866")},
                          vat_aggregator_fallback=True).lookup(ks)
        assert r.value == "DE266727866" and r.decision == "MANUAL_REVIEW"
        assert "NAME_PARTIAL" in r.all_issues or r.name_match == "PARTIAL"

    @pytest.mark.asyncio
    async def test_a_successor_under_a_new_name(self, tmp_path):
        url = "https://www.unternehmensverzeichnis.org/lx-international-deutschland-gmbh-frankfurt-am-main-2500811087866"
        lg = rec(("LG International (Deutschland) GmbH",), city="FRANKFURT AM MAIN")
        model = self._model([{"url": url, "title": "LX International (Deutschland) GmbH - Frankfurt am Main 60528"}])
        page_ = entry(url, "LX International (Deutschland) GmbH", "DE114183218",
                      "Ehemalige Firmennamen: LG International (Deutschland) GmbH Ehemalige Standorte: Lyoner Str. 15")
        r = await _engine(tmp_path, model, {url: page_}, vat_aggregator_fallback=True).lookup(lg)
        assert r.value == "DE114183218" and r.decision != AUTO_ACCEPT
        assert {"NAME_PARTIAL", "SUCCESSOR_CANDIDATE"} <= set(r.all_issues)

    @pytest.mark.asyncio
    async def test_never_shared_to_the_entitys_other_records(self, tmp_path):
        model = self._model([{"url": self.E, "title": "KACO GmbH + Co. KG"}])
        engine = _engine(tmp_path, model, {self.E: entry(self.E, "KACO GmbH + Co. KG", "DE145801743")},
                         vat_aggregator_fallback=True)
        sibling = replace(self.KACO, customer="99000099")
        first, second_ = await run_lookups(engine, [self.KACO, sibling])
        assert first.value == "DE145801743" and second_.value is None
        assert "AGGREGATOR_NOT_SHARED" in second_.flags and second_.status == NOT_FOUND
