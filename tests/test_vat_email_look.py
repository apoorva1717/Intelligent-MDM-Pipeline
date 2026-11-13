"""Step AM (vat/email_domain.py, vat/email_look.py): which email domain is
the customer's own, and route B alone on it. The model and the web are
faked; company data is the fixtures' public Impressum data or obviously fake."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from search.page_fetcher import PageFetchResult
from tests.test_vat_hybrid import BOSCH, BODY, IMPRESSUM, QUOTE, Model, _settings, found, read, run
from utils.cache import EvidenceCache
from vat.email_domain import own_domain, shared_domains, why_not
from vat.email_look import EmailLook, row_outcome
from vat.llm_cache import VatLLM
from vat.lookup import AUTO_ACCEPT
from vat.peppol import PeppolDirectory
from vat.step_al import CANDIDATE_OUTCOME, NOTHING_PASSES, NOT_RECHECKED
from vat.vies_client import VIESClient


class TestOwnDomain:
    def test_mailbox_providers_are_not_the_customer(self):
        for e in ("a@gmail.com", "a@t-online.de", "a@outlook.com", "a@online.de", "a@arcor.de", "a@hotmail.de"):
            assert own_domain([e]) is None and why_not([e]) == "MAIL_PROVIDER"

    def test_our_own_company_is_not_the_customer(self):
        assert own_domain(["max.muster@bruker.com"]) is None
        assert why_not(["max.muster@bruker.com"]) == "OWN_COMPANY"

    def test_umbrella_domains(self):
        assert why_not(["poststelle@bund.de"]) == "UMBRELLA"
        assert own_domain(["a@bundeswehr.org", "b@sachsen.de"]) is None

    def test_the_first_own_domain_wins(self):
        assert own_domain(["max@bruker.com", "einkauf@firma-xyz.de"]) == "firma-xyz.de"
        assert own_domain(["info@firma-xyz.de", "einkauf@andere.de"]) == "firma-xyz.de"
        assert why_not(["max@bruker.com", "einkauf@firma-xyz.de"]) == "USED"
        assert own_domain([None, None]) is None and why_not([None, ""]) == "NO_EMAIL"

    def test_a_domain_of_several_entities_is_shared(self):
        records = [("praxis a", ["a@portal.example"]), ("praxis b", ["b@portal.example"]),
                   ("praxis c", ["c@portal.example"]), ("firma", ["x@firma.example"]),
                   ("firma", ["y@firma.example"]), ("firma", ["z@firma.example"])]
        shared = shared_domains(records)
        assert shared == {"portal.example"}  # one entity on three rows is not shared
        assert own_domain(["a@portal.example"], shared) is None
        assert why_not(["a@portal.example"], shared) == "SHARED"
        assert own_domain(["x@firma.example"], shared) == "firma.example"


def _engine(tmp_path, model, pages):
    cache = EvidenceCache(tmp_path)
    s = _settings()
    vies = VIESClient(s, cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
    return EmailLook(s, vies, llm=VatLLM(cache.namespace("vat_llm"), search=model), fetcher=FakeWeb(pages),
                     peppol=PeppolDirectory(cache, enabled=False))


EMAIL_INP = replace(BOSCH, website=None, email="info@bosch.example")


class TestEmailLook:
    @pytest.mark.asyncio
    async def test_the_legal_notice_naming_the_entity_gives_the_number(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(EMAIL_INP)
        assert r.value == "DE811128135" and r.decision == AUTO_ACCEPT
        assert r.usage["al"]["route"] == "B" and r.usage["al"]["outcome"] == CANDIDATE_OUTCOME
        assert not [c for c in model.calls if c[0] == "run"]  # never a search

    @pytest.mark.asyncio
    async def test_another_company_notice_finds_nothing_and_never_searches(self, tmp_path):
        body = "Impressum Fremd Holding AG Musterweg 1 12345 Musterstadt " + QUOTE
        model = Model(run=[run("q", [])], read=[read(found(name="Fremd Holding AG"))])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(EMAIL_INP)
        assert r.value is None and r.usage["al"]["outcome"] == NOTHING_PASSES
        assert r.usage["al"]["routes"] == ["B"]
        assert r.usage["al"]["attempts"][-1]["gate"] == "NAME_NOT_ON_PAGE"
        assert not [c for c in model.calls if c[0] in ("run", "read")]

    @pytest.mark.asyncio
    async def test_a_domain_that_never_answers_is_not_rechecked(self, tmp_path):
        root = "https://www.bosch.example"
        refused = {u: PageFetchResult(url=u, error="SSLError") for u in (root + "/impressum", root + "/imprint",
                                                                          root + "/")}
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, refused).lookup(EMAIL_INP)
        assert r.value is None and r.usage["al"]["outcome"] == NOT_RECHECKED
        assert r.usage["al"]["failures"] == ["UNREACHABLE_SSLError"]

    @pytest.mark.asyncio
    async def test_404_everywhere_is_no_legal_notice(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {}).lookup(EMAIL_INP)
        assert r.usage["al"]["outcome"] == NOTHING_PASSES
        assert r.usage["al"]["attempts"][-1]["gate"] == "NO_LEGAL_NOTICE"

    @pytest.mark.asyncio
    async def test_no_domain_no_route(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {}).lookup(replace(EMAIL_INP, email="x@gmail.com"))
        assert r.value is None and r.usage["al"]["routes"] == []
        assert not model.calls

    def test_row_outcome_keeps_the_old_row_unless_a_number_passed(self):
        old = {"VAT_Decision": "Not found", "Not_Found_Reason": "NO_WEBSITE_FOUND", "VAT_Why": "old"}
        cols = row_outcome(None, old, "firma.example", "Firma", "12345", "Musterstadt")
        assert cols["VAT_Decision"] == "Not found" and cols["Not_Found_Reason"] == "NO_WEBSITE_FOUND"
        assert cols["EM_Outcome"] == "not re-checked" and cols["EM_Domain"] == "firma.example"

    @pytest.mark.asyncio
    async def test_row_outcome_of_a_new_number(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(EMAIL_INP)
        cols = row_outcome(r, {"VAT_Decision": "Not found"}, "bosch.example", BOSCH.name_1, BOSCH.postal_code,
                           BOSCH.city)
        assert cols["EM_Outcome"] == "new number" and cols["VAT_Proposed"] == "DE811128135"
        assert cols["Not_Found_Reason"] is None
        assert NOT_RECHECKED not in cols.values()
