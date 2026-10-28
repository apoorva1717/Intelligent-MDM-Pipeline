"""The missing-VAT lookup against the design paper "Designing an Agent to
Identify German VAT Identification Numbers" (2026-10-27).

One class per paper section the code implements: §2 inputs, §4 source
hierarchy, §5 entity resolution, §6 extraction, §8 decision model, §10 audit
trail and KPIs. The doubles are the grounded tests' (tests/mocks/); company
data is the fixture's public Impressum data or obviously fake.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeReader, FakeSearch, FakeWeb, page, reading
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.entity_match import (
    ADDRESS_EXACT,
    ADDRESS_NONE,
    ADDRESS_NOT_STATED,
    ADDRESS_PARTIAL,
    ADDRESS_STRONG,
    NAME_BRANCH,
    NAME_EXACT,
    NAME_MISMATCH,
    NAME_PARTIAL,
    SOURCE_COMPANY_IMPRESSUM,
    SOURCE_COMPANY_WEBSITE,
    SOURCE_OFFICIAL_REGISTER,
    SOURCE_SECONDARY,
    VAT_PATTERN,
    address_match,
    company_domain,
    legal_form,
    legal_forms_differ,
    match_score,
    register_match,
    source_type,
    street_on_page,
    vat_candidates,
)
from vat.llm_cache import VatLLM
from vat.lookup import (
    AUTO_ACCEPT,
    CONF_HIGH,
    CONF_MEDIUM,
    CONF_NOT_FOUND,
    CONF_NOT_VERIFIED,
    CONF_VERIFIED,
    CONFLICT,
    DECISION_NOT_FOUND,
    FOUND_UNVERIFIED,
    FOUND_VERIFIED,
    LOOKUP_COLUMNS,
    LOOKUP_INCOMPLETE,
    MANUAL_REVIEW,
    NOT_FOUND,
    NOT_RUN,
    FoundResult,
    LookupInput,
    lookup_kpis,
    run_lookups,
)
from vat.lookup_grounded import ATTRIBUTION_PARTIAL, GroundedLookup, is_directory_host
from vat.lookup_web import WebSearchLookup
from vat.validate import validate_rows
from vat.vies_client import NOT_CHECKED, VIESClient

BOSCH = LookupInput(
    customer="99000003", names=("Robert Bosch GmbH", None, None, None),
    street="Robert-Bosch-Platz", house_number="1", postal_code="70839",
    city="Gerlingen-Schillerhoehe", country="DE",
)
SITE = "https://www.bosch.example"
IMPRESSUM = f"{SITE}/impressum"
QUOTE = "Umsatzsteuer-Identifikationsnummer: DE 811 128 135"
BODY = (
    "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen-Schillerhöhe "
    "Registergericht: Amtsgericht Stuttgart, HRB 14000 " + QUOTE
)
ADDRESS = "Robert-Bosch-Platz 1, 70839 Gerlingen-Schillerhöhe"


def _settings():
    return replace(get_settings(), vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                   vies_requester_vat="", cache_frozen=False)


def _grounded(tmp_path, *, search=None, web=None, reader=None, vies_reply=None):
    cache = EvidenceCache(tmp_path)
    vies = VIESClient(_settings(), cache=cache,
                      transport=ViesStub(vies_reply or vies_mock.echo_valid_undisclosed).transport)
    reader = reader if reader is not None else FakeReader(
        {IMPRESSUM: reading("DE811128135", "Robert Bosch GmbH", QUOTE, address=ADDRESS)})
    return GroundedLookup(
        _settings(), vies,
        search_client=search if search is not None else FakeSearch({"Bosch": [IMPRESSUM]}),
        fetcher=web if web is not None else FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY)}),
        llm=VatLLM(cache.namespace("vat_llm"), complete=reader),
    )


def _one(tmp_path, url, body, answer, key="Bosch"):
    return _grounded(tmp_path, search=FakeSearch({key: [url]}), web=FakeWeb({url: page(url, body)}),
                     reader=FakeReader({url: answer}))


# ── §2 inputs ────────────────────────────────────────────────────────────────

class TestInputs:
    def test_mandatory_inputs(self):
        assert BOSCH.missing_mandatory() == ()
        bare = LookupInput("1", ("X GmbH", None, None, None), None, None, None, "Bonn", "DE")
        assert bare.missing_mandatory() == ("street", "postal_code")

    @pytest.mark.asyncio
    async def test_incomplete_input_is_flagged(self, tmp_path):
        bare = replace(BOSCH, street=None, house_number=None)
        [r] = await run_lookups(_grounded(tmp_path), [bare])
        assert "INPUT_INCOMPLETE:street" in r.flags

    @pytest.mark.asyncio
    async def test_optional_columns_reach_the_lookup(self, tmp_path):
        seen = []

        class Spy:
            mode, agent_version = "grounded", "spy/1"

            async def lookup(self, inp):
                seen.append(inp)
                return FoundResult("grounded", NOT_FOUND)

            def stats(self):
                return {}

        row = {"Customer": "1", "Country/Region Key": "DE", "VAT Registration No.": None,
               "Name": "Robert Bosch GmbH", "Website": "https://www.bosch.de",
               "Email": "einkauf@bosch.de", "Commercial Register Number": "HRB 14000"}
        out, _ = await validate_rows([row], VIESClient(_settings(), cache=EvidenceCache(tmp_path)),
                                     lookup=Spy())
        assert (seen[0].website, seen[0].email, seen[0].register_number) == (
            "https://www.bosch.de", "einkauf@bosch.de", "HRB 14000")
        assert out[0]["VAT_Found_Agent_Version"] == "spy/1"

    def test_web_prompt_carries_domain_and_register(self):
        inp = replace(BOSCH, website="www.bosch.de", register_number="HRB 14000")
        prompt = WebSearchLookup.prompt(inp)
        assert "bosch.de" in prompt and "HRB 14000" in prompt

    @pytest.mark.parametrize("website, email, domain", [
        ("https://www.bosch.de/de", None, "bosch.de"),
        (None, "a.b@de.bosch.com", "bosch.com"),
        (None, "someone@gmail.com", None),  # a mailbox provider is not the company
        (None, None, None),
    ])
    def test_company_domain(self, website, email, domain):
        assert company_domain(website, email) == domain


# ── §4 source hierarchy ──────────────────────────────────────────────────────

class TestSourceHierarchy:
    def test_official_registers_are_not_directories(self):
        url = "https://www.unternehmensregister.de/ureg/result.html"
        assert not is_directory_host(url)
        assert source_type(url, directory=False, impressum=False, own_domain=None) == \
            SOURCE_OFFICIAL_REGISTER

    @pytest.mark.parametrize("kw, expected", [
        (dict(directory=True, impressum=True, own_domain=None), SOURCE_SECONDARY),
        (dict(directory=False, impressum=True, own_domain=None), SOURCE_COMPANY_IMPRESSUM),
        (dict(directory=False, impressum=False, own_domain=True), SOURCE_COMPANY_WEBSITE),
        (dict(directory=False, impressum=False, own_domain=None), SOURCE_SECONDARY),
    ])
    def test_tiers(self, kw, expected):
        assert source_type("https://x.example/p", **kw) == expected

    @pytest.mark.asyncio
    async def test_secondary_source_is_never_auto_accepted(self, tmp_path):
        url = "https://www.northdata.de/Robert+Bosch+GmbH"
        r = await _one(tmp_path, url, BODY, reading("DE811128135", "Robert Bosch GmbH", QUOTE)
                       ).lookup(BOSCH)
        assert (r.source_type, r.decision) == (SOURCE_SECONDARY, MANUAL_REVIEW)


# ── §5 entity resolution ─────────────────────────────────────────────────────

class TestLegalForm:
    @pytest.mark.parametrize("name, form", [
        ("Siemens Aktiengesellschaft", "ag"),
        ("Siemens AG", "ag"),
        ("Henkel AG & Co. KGaA", "ag kgaa"),
        ("Kappler GmbH & Co.KG", "gmbh kg"),
        ("Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V.", "ev"),
        ("Gesellschaft für Informatik mbH", "gmbh"),
        ("Universität Stuttgart", None),
    ])
    def test_canonical_form(self, name, form):
        assert legal_form(name) == form

    def test_only_a_difference_counts(self):
        assert legal_forms_differ("Example AG", "Example GmbH") == ("gmbh", "ag")
        assert legal_forms_differ("Example GmbH", "Example GmbH & Co. KG") == ("gmbh kg", "gmbh")
        assert legal_forms_differ("Example", "Example GmbH") is None
        assert legal_forms_differ("Siemens AG", "Siemens Aktiengesellschaft") is None

    @pytest.mark.asyncio
    async def test_same_name_other_legal_form_is_partial(self, tmp_path):
        # The paper's governing principle: same brand, another legal entity.
        quote = "USt-IdNr. DE811128135"
        body = "Impressum Robert Bosch AG Robert-Bosch-Platz 1 70839 Gerlingen " + quote
        r = await _one(tmp_path, IMPRESSUM, body, reading("DE811128135", "Robert Bosch AG", quote)
                       ).lookup(BOSCH)
        assert r.flags[0] == ATTRIBUTION_PARTIAL
        assert "LEGAL_FORM_DIFFERS:gmbh/ag" in r.flags
        assert (r.name_match, r.decision) == (NAME_PARTIAL, MANUAL_REVIEW)


class TestAddress:
    @pytest.mark.parametrize("page_text, ok", [
        ("Robert-Bosch-Platz 1 70839 Gerlingen", True),
        ("Robert Bosch Platz 1, 70839 Gerlingen", True),
        ("Robert-Bosch-Platz 12 70839 Gerlingen", False),
    ])
    def test_street_and_house_number(self, page_text, ok):
        assert street_on_page(page_text, "Robert-Bosch-Platz", "1") is ok

    @pytest.mark.parametrize("sap, page_text", [
        ("Hauptstr. 5", "Hauptstraße 5"),
        ("Haupt-Straße 5", "Hauptstr. 5"),
        ("Carl-Wery-Str. 34", "Carl-Wery-Straße 34"),
    ])
    def test_street_abbreviations(self, sap, page_text):
        assert street_on_page(page_text, sap, None)

    @pytest.mark.parametrize("page_text, level", [
        ("Robert-Bosch-Platz 1 70839 Gerlingen-Schillerhöhe", ADDRESS_EXACT),
        ("Postfach 10 60 50 70839 Gerlingen-Schillerhöhe", ADDRESS_STRONG),
        ("Gerlingen-Schillerhöhe", ADDRESS_PARTIAL),
        ("Carl-Wery-Str. 34 81739 München", ADDRESS_NONE),
        ("Impressum", ADDRESS_NOT_STATED),
    ])
    def test_levels(self, page_text, level):
        got, _ = address_match(page_text, "Robert-Bosch-Platz", "1", "70839",
                               "Gerlingen-Schillerhoehe")
        assert got == level


class TestScoreAndRegister:
    def test_register(self):
        assert register_match("Amtsgericht Stuttgart, HRB 14000", "HRB 14000") is True
        assert register_match("Amtsgericht Stuttgart HRB14000", "hrb 014000") is True
        assert register_match("Amtsgericht München HRB 999", "HRB 14000") is False
        assert register_match("no register entry", "HRB 14000") is None
        assert register_match("HRB 14000", None) is None

    def test_weights(self):
        full = {"street": True, "postal_code": True, "city": True, "domain": True, "register": True}
        assert match_score(NAME_EXACT, full) == 100
        assert match_score(NAME_MISMATCH, full) == 70
        # Elements the record lacks are left out, not counted against it.
        assert match_score(NAME_EXACT, {"street": True, "postal_code": True, "city": True,
                                        "domain": None, "register": None}) == 100
        assert match_score(NAME_BRANCH, {"street": False, "postal_code": True, "city": True,
                                         "domain": None, "register": None}) == 50

    @pytest.mark.asyncio
    async def test_register_mismatch_blocks_auto_accept(self, tmp_path):
        inp = replace(BOSCH, register_number="HRB 99999")
        r = await _grounded(tmp_path).lookup(inp)
        assert "REGISTER_MISMATCH" in r.flags and r.decision == MANUAL_REVIEW


# ── §6 extraction ────────────────────────────────────────────────────────────

class TestExtraction:
    def test_the_papers_pattern(self):
        assert VAT_PATTERN.findall("USt-IdNr. DE 811128135, DE811128135") == [
            "DE 811128135", "DE811128135"]

    def test_candidates_normalised_and_grouped(self):
        text = "USt-IdNr.: DE 1477 94 196 · DE 811.128.135 · DE811128135"
        assert vat_candidates(text) == ["DE147794196", "DE811128135"]

    def test_w_idnr_is_not_a_candidate(self):
        assert vat_candidates("W-IdNr. DE811128135-00001") == []

    @pytest.mark.asyncio
    async def test_unreported_ids_on_the_page_are_listed(self, tmp_path):
        body = BODY + " Bosch Sicherheitssysteme GmbH USt-IdNr. DE 147 799 625"
        r = await _grounded(tmp_path, web=FakeWeb({IMPRESSUM: page(IMPRESSUM, body)})).lookup(BOSCH)
        assert "OTHER_VAT_IDS_ON_PAGE:DE147799625" in r.flags


# ── §8 decision model ────────────────────────────────────────────────────────

class TestDecision:
    @pytest.mark.asyncio
    async def test_auto_accept(self, tmp_path):
        r = await _grounded(tmp_path).lookup(BOSCH)
        assert (r.status, r.tier) == (FOUND_VERIFIED, "PROPOSED")
        assert (r.decision, r.confidence) == (AUTO_ACCEPT, CONF_VERIFIED)
        assert (r.name_match, r.address_match, r.source_type) == (
            NAME_EXACT, ADDRESS_EXACT, SOURCE_COMPANY_IMPRESSUM)

    @pytest.mark.asyncio
    async def test_branch_is_reviewed_not_accepted(self, tmp_path):
        werk = replace(BOSCH, names=("Robert Bosch GmbH Werk Gerlingen", None, None, None))
        r = await _grounded(tmp_path).lookup(werk)
        assert r.name_match == NAME_BRANCH
        assert (r.decision, r.confidence) == (MANUAL_REVIEW, CONF_HIGH)

    @pytest.mark.asyncio
    async def test_postcode_only_is_not_a_strong_address(self, tmp_path):
        body = "Impressum Robert Bosch GmbH Postfach 30 02 20 70442 Stuttgart " \
               "Sitz 70839 " + QUOTE
        r = await _grounded(tmp_path, web=FakeWeb({IMPRESSUM: page(IMPRESSUM, body)})).lookup(BOSCH)
        assert r.address_match == ADDRESS_PARTIAL and r.decision == MANUAL_REVIEW

    @pytest.mark.asyncio
    async def test_vies_unavailable_is_reviewed(self, tmp_path):
        r = await _grounded(tmp_path, vies_reply=vies_mock.error("MS_UNAVAILABLE")).lookup(BOSCH)
        assert r.validation_status == "SERVICE_UNAVAILABLE"
        assert r.decision == MANUAL_REVIEW

    @pytest.mark.asyncio
    async def test_two_exact_sources_two_numbers_is_conflict(self, tmp_path):
        a, b = IMPRESSUM, "https://www.bosch-zwei.example/impressum"
        qa, qb = QUOTE, "USt-IdNr. DE129274235"
        web = FakeWeb({a: page(a, "Impressum Robert Bosch GmbH " + qa),
                       b: page(b, "Impressum Robert Bosch GmbH 70839 Gerlingen " + qb)})
        reader = FakeReader({a: reading("DE811128135", "Robert Bosch GmbH", qa),
                             b: reading("DE129274235", "Robert Bosch GmbH", qb)})
        r = await _grounded(tmp_path, search=FakeSearch({"Bosch": [a, b]}), web=web,
                            reader=reader).lookup(BOSCH)
        assert any(f.startswith("CONFLICTING_SOURCES:") for f in r.flags)
        assert r.decision == CONFLICT and r.confidence == CONFLICT

    @pytest.mark.parametrize("result, decision, confidence", [
        (FoundResult("grounded", NOT_RUN), None, None),
        (FoundResult("grounded", NOT_FOUND), DECISION_NOT_FOUND, CONF_NOT_FOUND),
        (FoundResult("grounded", LOOKUP_INCOMPLETE), DECISION_NOT_FOUND, CONF_NOT_FOUND),
        (FoundResult("grounded", FOUND_UNVERIFIED, "DE811128135", tier="LIKELY"),
         MANUAL_REVIEW, CONF_MEDIUM),
        (FoundResult("grounded", "FOUND_UNVERIFIED:VIES_INVALID", "DE811128135", tier="REVIEW"),
         MANUAL_REVIEW, CONF_NOT_VERIFIED),
        (FoundResult("llm_only", FOUND_UNVERIFIED, "DE811128135"), MANUAL_REVIEW, CONF_NOT_VERIFIED),
    ])
    def test_rules(self, result, decision, confidence):
        assert (result.decision, result.confidence) == (decision, confidence)


# ── §10 audit trail and KPIs ─────────────────────────────────────────────────

class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_evidence_columns(self, tmp_path):
        engine = _grounded(tmp_path)
        [r] = await run_lookups(engine, [BOSCH])
        cols = r.columns()
        assert set(cols) == set(LOOKUP_COLUMNS)
        assert cols["VAT_Found_Page_Title"] == "Impressum"
        assert cols["VAT_Found_Published_Address"] == ADDRESS
        assert cols["VAT_Found_Validation_Status"] == "VALID"
        assert cols["VAT_Found_Validation_Date"]
        assert cols["VAT_Found_Match_Score"] == 100
        assert cols["VAT_Found_Agent_Version"].startswith("grounded|vat_impressum_read/v2:")

    @pytest.mark.asyncio
    async def test_an_address_not_on_the_page_is_dropped(self, tmp_path):
        reader = FakeReader({IMPRESSUM: reading("DE811128135", "Robert Bosch GmbH", QUOTE,
                                                address="Musterweg 9, 12345 Berlin")})
        r = await _grounded(tmp_path, reader=reader).lookup(BOSCH)
        assert r.published_address is None

    @pytest.mark.asyncio
    async def test_a_failed_gate_was_never_validated(self, tmp_path):
        reader = FakeReader({IMPRESSUM: reading("DE811128135", "Robert Bosch GmbH", "not on page")})
        r = await _grounded(tmp_path, reader=reader).lookup(BOSCH)
        assert (r.validation_status, r.validation_date) == (NOT_CHECKED, None)

    @pytest.mark.asyncio
    async def test_web_search_result_carries_the_same_evidence(self, tmp_path):
        cache = EvidenceCache(tmp_path)
        vies = VIESClient(_settings(), cache=cache,
                          transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)

        async def search(system, user, effort):
            return json.dumps({"vat_id": "DE811128135", "source_url": IMPRESSUM,
                               "evidence_quote": QUOTE, "attributed_to": "Robert Bosch GmbH",
                               "published_address": ADDRESS}), [IMPRESSUM]

        engine = WebSearchLookup(_settings(), vies, llm=VatLLM(cache.namespace("vat_llm"), search=search),
                                 fetcher=FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY)}))
        r = await engine.lookup(BOSCH)
        assert (r.decision, r.published_address, r.source_type) == (
            AUTO_ACCEPT, ADDRESS, SOURCE_COMPANY_IMPRESSUM)
        assert engine.agent_version.startswith("llm_web|vat_llm_web/v2:")

    def test_kpis_count_each_customer_once(self):
        ok = FoundResult("grounded", FOUND_VERIFIED, "DE811128135", tier="PROPOSED",
                         name_match=NAME_EXACT, address_match=ADDRESS_EXACT,
                         source_type=SOURCE_COMPANY_IMPRESSUM, seconds=2.0)
        none = FoundResult("grounded", NOT_FOUND, seconds=4.0)
        k = lookup_kpis([ok, ok, none, None, FoundResult("grounded", NOT_RUN)])
        assert k["researched_customers"] == 2
        assert (k["discovery_rate"], k["verification_rate"]) == (0.5, 0.5)
        assert (k["auto_accept_rate"], k["not_found_rate"], k["manual_review_rate"]) == (0.5, 0.5, 0.0)
        assert k["avg_seconds_per_customer"] == 3.0
        assert k["decision"] == {AUTO_ACCEPT: 1, DECISION_NOT_FOUND: 1}
