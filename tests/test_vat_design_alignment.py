"""The missing-VAT lookup's matching and decision rules.

One class per rule area: inputs, address match (four values), name and legal
form, decision rules and VIES, entity-level results, issue codes, source
classes, extraction, audit trail and KPIs. Fixtures: the Bosch Impressum
(public data) and the fictional "Müller Maschinenbau GmbH, Hauptstraße 5,
70839 Gerlingen". The doubles are the grounded tests' (tests/mocks/).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeReader, FakeSearch, FakeWeb, page, reading
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.entity_match import (
    ADDRESS_DIFFERENT_TOWN,
    ADDRESS_EXACT,
    ADDRESS_NO_ADDRESS,
    ADDRESS_SAME_TOWN,
    BLOCKING_ISSUES,
    ISSUE_ORDER,
    NAME_EXACT,
    NAME_MISMATCH,
    NAME_PARTIAL,
    SOURCE_AGGREGATOR,
    SOURCE_COMPANY_IMPRESSUM,
    SOURCE_COMPANY_WEBSITE,
    SOURCE_OFFICIAL_FILING,
    SOURCE_PEPPOL_DIRECTORY,
    SOURCE_PROCUREMENT_PROFILE,
    SOURCE_WEB_ARCHIVE,
    VAT_PATTERN,
    address_match,
    company_domain,
    is_blocking,
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
    CONF_LOW,
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
    entity_key,
    lookup_kpis,
    run_lookups,
)
from vat.lookup_grounded import ATTRIBUTION_PARTIAL, GroundedLookup, is_directory_host
from vat.lookup_web import WebSearchLookup
from vat.peppol import PeppolDirectory
from vat.validate import validate_rows
from vat.vies_client import NOT_CHECKED, VIESClient
from vat.vies_retry import ViesRetryQueue, apply_answers, enqueue, next_attempt

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

# ── The fictional Müller Maschinenbau GmbH ──────────────────────────────────
MUELLER = LookupInput(
    customer="1", names=("Müller Maschinenbau GmbH", None, None, None),
    street="Hauptstraße", house_number="5", postal_code="70839", city="Gerlingen", country="DE",
)
M_URL = "https://www.mueller-maschinenbau.example/impressum"
M_VAT = "DE811128135"            # a number with a valid check digit
M_OTHER = "DE129273398"
M_QUOTE = f"USt-IdNr.: {M_VAT}"
M_ADDRESS = "Hauptstraße 5, 70839 Gerlingen"


def _settings():
    return replace(get_settings(), vat_web_query_mode="model", vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                   vies_requester_vat="", cache_frozen=False,
                   # The inline VIES check these tests were written for (Step AF-2: the queue).
                   vat_vies_in_lookup=True)


def _vies(cache, reply=None):
    return VIESClient(_settings(), cache=cache,
                      transport=ViesStub(reply or vies_mock.echo_valid_undisclosed).transport)


def _grounded(tmp_path, *, search=None, web=None, reader=None, vies_reply=None):
    cache = EvidenceCache(tmp_path)
    reader = reader if reader is not None else FakeReader(
        {IMPRESSUM: reading("DE811128135", "Robert Bosch GmbH", QUOTE, address=ADDRESS)})
    return GroundedLookup(
        _settings(), _vies(cache, vies_reply),
        search_client=search if search is not None else FakeSearch({"Bosch": [IMPRESSUM]}),
        fetcher=web if web is not None else FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY)}),
        llm=VatLLM(cache.namespace("vat_llm"), complete=reader),
    )


def _one(tmp_path, url, body, answer, key="Bosch", vies_reply=None):
    return _grounded(tmp_path, search=FakeSearch({key: [url]}), web=FakeWeb({url: page(url, body)}),
                     reader=FakeReader({url: answer}), vies_reply=vies_reply)


async def _mueller(tmp_path, *, name="Müller Maschinenbau GmbH", address=M_ADDRESS, extra="",
                   inp=MUELLER, url=M_URL, vies_reply=None, others=None):
    """The Müller Impressum stating *name* at *address*, read through the
    grounded engine against *inp*."""
    body = f"Impressum {name} {address} {M_QUOTE} {extra}"
    answer = reading(M_VAT, name, M_QUOTE, others=others)
    key = inp.names[0].split()[0]
    return await _one(tmp_path, url, body, answer, key=key, vies_reply=vies_reply).lookup(inp)


def _web(tmp_path, answer, pages, *, peppol=None, vies_reply=None, visited=None):
    cache = EvidenceCache(tmp_path)

    async def search(system, user, effort):
        return json.dumps(answer), visited if visited is not None else [answer.get("source_url")]

    return WebSearchLookup(
        _settings(), _vies(cache, vies_reply), llm=VatLLM(cache.namespace("vat_llm"), search=search),
        fetcher=FakeWeb(pages), peppol=peppol or PeppolDirectory(cache, enabled=False),
    )


def _issues(r: FoundResult) -> list[str]:
    return list(r.all_issues)


# ── Inputs ───────────────────────────────────────────────────────────────────

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
               "Email": "einkauf@bosch.de", "Commercial Register Number": "HRB 14000",
               "Central Deletion Flag": "X"}
        out, _ = await validate_rows([row], VIESClient(_settings(), cache=EvidenceCache(tmp_path)),
                                     lookup=Spy())
        assert (seen[0].website, seen[0].email, seen[0].register_number, seen[0].deleted) == (
            "https://www.bosch.de", "einkauf@bosch.de", "HRB 14000", True)
        assert out[0]["VAT_Found_Agent_Version"] == "spy/1"

    def test_web_prompt_carries_domain_register_and_status(self):
        inp = replace(BOSCH, website="www.bosch.de", register_number="HRB 14000")
        prompt = WebSearchLookup.prompt(inp)
        assert "bosch.de" in prompt and "HRB 14000" in prompt and "Record status: active" in prompt
        assert "deleted" in WebSearchLookup.prompt(replace(inp, deleted=True)).split("Record status:")[1]

    @pytest.mark.parametrize("website, email, domain", [
        ("https://www.bosch.de/de", None, "bosch.de"),
        (None, "a.b@de.bosch.com", "bosch.com"),
        (None, "someone@gmail.com", None),  # a mailbox provider is not the company
        (None, None, None),
    ])
    def test_company_domain(self, website, email, domain):
        assert company_domain(website, email) == domain


# ── 1. Address match ─────────────────────────────────────────────────────────

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
        ("Hauptstrasse 5", "Haupt-Straße 5"),
        ("Carl-Wery-Str. 34", "Carl-Wery-Straße 34"),
    ])
    def test_street_folding(self, sap, page_text):
        assert street_on_page(page_text, sap, None)

    @pytest.mark.parametrize("page_text, level", [
        ("Hauptstraße 5, 70839 Gerlingen", ADDRESS_EXACT),
        ("Hauptstr. 5, 70839 Gerlingen", ADDRESS_EXACT),
        ("Hauptstraße 12, 70839 Gerlingen", ADDRESS_SAME_TOWN),
        ("Industriestraße 20, 70839 Gerlingen", ADDRESS_SAME_TOWN),
        ("Hauptstraße 5, 70442 Gerlingen", ADDRESS_SAME_TOWN),
        ("Postfach 30 02 20, Gerlingen", ADDRESS_SAME_TOWN),
        ("Königstraße 1, 70173 Stuttgart", ADDRESS_DIFFERENT_TOWN),
        ("Impressum Müller Maschinenbau GmbH", ADDRESS_NO_ADDRESS),
    ])
    def test_four_values(self, page_text, level):
        got, _ = address_match(page_text, "Hauptstraße", "5", "70839", "Gerlingen")
        assert got == level

    def test_sap_without_street_is_same_town(self):
        got, _ = address_match("Hauptstraße 5, 70839 Gerlingen", None, None, "70839", "Gerlingen")
        assert got == ADDRESS_SAME_TOWN

    def test_court_town_is_not_the_address(self):
        # "Amtsgericht Gerlingen" is the registry court, not where the company is.
        got, _ = address_match("Amtsgericht Gerlingen HRB 1 · Königstraße 1, 70173 Stuttgart",
                               "Hauptstraße", "5", "70839", "Gerlingen")
        assert got == ADDRESS_DIFFERENT_TOWN

    @pytest.mark.asyncio
    @pytest.mark.parametrize("address, level, decision, confidence, issues", [
        ("Hauptstraße 12, 70839 Gerlingen", ADDRESS_SAME_TOWN, AUTO_ACCEPT, CONF_VERIFIED,
         ["ADDRESS_DIFFERS"]),
        ("Industriestraße 20, 70839 Gerlingen", ADDRESS_SAME_TOWN, AUTO_ACCEPT, CONF_VERIFIED,
         ["ADDRESS_DIFFERS"]),
        ("Hauptstraße 5, 70442 Gerlingen", ADDRESS_SAME_TOWN, AUTO_ACCEPT, CONF_VERIFIED,
         ["ADDRESS_DIFFERS"]),
        ("Postfach 30 02 20, Gerlingen", ADDRESS_SAME_TOWN, AUTO_ACCEPT, CONF_VERIFIED,
         ["ADDRESS_DIFFERS"]),
        ("", ADDRESS_NO_ADDRESS, MANUAL_REVIEW, CONF_MEDIUM, ["ADDRESS_NOT_ON_PAGE"]),
        ("Königstraße 1, 70173 Stuttgart", ADDRESS_DIFFERENT_TOWN, MANUAL_REVIEW,
         CONF_MEDIUM, ["ADDRESS_MISMATCH"]),
    ])
    async def test_decisions(self, tmp_path, address, level, decision, confidence, issues):
        r = await _mueller(tmp_path, address=address)
        assert (r.address_match, r.decision, r.confidence, _issues(r)) == (
            level, decision, confidence, issues)

    @pytest.mark.asyncio
    async def test_exact_address_is_a_clean_match(self, tmp_path):
        r = await _mueller(tmp_path)
        assert (r.address_match, r.decision, r.confidence, _issues(r)) == (
            ADDRESS_EXACT, AUTO_ACCEPT, CONF_VERIFIED, [])

    @pytest.mark.asyncio
    async def test_sap_without_street_auto_accepts(self, tmp_path):
        r = await _mueller(tmp_path, inp=replace(MUELLER, street=None, house_number=None))
        assert (r.address_match, r.decision) == (ADDRESS_SAME_TOWN, AUTO_ACCEPT)


# ── 2. Name and legal form ───────────────────────────────────────────────────

class TestLegalForm:
    @pytest.mark.parametrize("name, form", [
        ("Siemens Aktiengesellschaft", "ag"),
        ("Siemens AG", "ag"),
        ("Henkel AG & Co. KGaA", "ag & co kgaa"),
        ("Kappler GmbH & Co.KG", "gmbh & co kg"),
        ("Kappler GmbH + Co. KG", "gmbh & co kg"),
        ("Kappler GmbH & Co KG", "gmbh & co kg"),
        ("Example SE & Co. KGaA", "se & co kgaa"),
        ("Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e. V.", "ev"),
        ("Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V.", "ev"),
        ("Gesellschaft für Informatik mbH", "gmbh"),
        ("Example gemeinnützige GmbH", "ggmbh"),
        ("Example gGmbH", "ggmbh"),
        ("Example Gesellschaft mit beschränkter Haftung", "gmbh"),
        ("Universität Stuttgart", None),
    ])
    def test_canonical_form(self, name, form):
        assert legal_form(name) == form

    @pytest.mark.parametrize("sap, form", [
        ("Donaldson Filtration Deutschland Gm", "gmbh"),
        ("Donaldson Filtration Deutschland Gmb", "gmbh"),
        ("Müller Maschinenbau Verwaltung GmbH & Co", "gmbh & co kg"),
        ("Müller Maschinenbau Verwaltungs GmbH & C", "gmbh & co kg"),
        ("Müller Maschinenbau Werkzeuge Deutschl A", None),   # ambiguous: no form
    ])
    def test_sap_35_character_cut(self, sap, form):
        assert legal_form(sap, sap=True) == form

    def test_only_a_difference_counts(self):
        assert legal_forms_differ("Example AG", "Example GmbH") == ("gmbh", "ag")
        assert legal_forms_differ("Example GmbH", "Example GmbH & Co. KG") == ("gmbh & co kg", "gmbh")
        assert legal_forms_differ("Example", "Example GmbH") is None
        assert legal_forms_differ("Siemens AG", "Siemens Aktiengesellschaft") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("page_name, decision, issues", [
        ("Müller Maschinenbau AG", MANUAL_REVIEW, ["LEGAL_FORM_DIFFERS:gmbh/ag"]),
        ("Müller Maschinenbau GmbH & Co. KG", MANUAL_REVIEW,
         ["LEGAL_FORM_DIFFERS:gmbh/gmbh & co kg"]),
        ("Müller Maschinenbau Gesellschaft mit beschränkter Haftung", AUTO_ACCEPT, []),
        ("Müller Werkzeugbau GmbH", MANUAL_REVIEW, ["NAME_PARTIAL"]),
    ])
    async def test_page_name(self, tmp_path, page_name, decision, issues):
        r = await _mueller(tmp_path, name=page_name)
        assert (r.decision, _issues(r)) == (decision, issues)

    @pytest.mark.asyncio
    async def test_other_legal_form_is_partial(self, tmp_path):
        r = await _mueller(tmp_path, name="Müller Maschinenbau AG")
        assert r.flags[0] == ATTRIBUTION_PARTIAL and r.name_match == NAME_PARTIAL

    @pytest.mark.asyncio
    async def test_form_missing_in_sap(self, tmp_path):
        inp = replace(MUELLER, names=("Müller Maschinenbau", None, None, None))
        r = await _mueller(tmp_path, inp=inp)
        assert (r.name_match, r.decision, _issues(r)) == (
            NAME_EXACT, AUTO_ACCEPT, ["LEGAL_FORM_MISSING_IN_SAP"])

    @pytest.mark.asyncio
    async def test_form_missing_in_sap_with_a_sister_on_the_page(self, tmp_path):
        inp = replace(MUELLER, names=("Müller Maschinenbau", None, None, None))
        sister = f"Müller Maschinenbau GmbH & Co. KG USt-IdNr. {M_OTHER}"
        r = await _mueller(tmp_path, inp=inp, extra=sister)
        assert (r.decision, _issues(r)) == (MANUAL_REVIEW, [
            "LEGAL_FORM_MISSING_IN_SAP", "SISTER_ENTITY_ON_PAGE", "OTHER_VAT_IDS_ON_PAGE"])

    @pytest.mark.asyncio
    async def test_form_missing_in_sap_general_partner_of_a_kg(self, tmp_path):
        inp = replace(MUELLER, names=("Müller Maschinenbau", None, None, None))
        extra = "Komplementärin der Müller Holding GmbH & Co. KG"
        r = await _mueller(tmp_path, inp=inp, extra=extra)
        assert "SISTER_ENTITY_ON_PAGE" in _issues(r) and r.decision == MANUAL_REVIEW

    @pytest.mark.asyncio
    async def test_truncated_sap_name(self, tmp_path):
        inp = replace(MUELLER, names=("Donaldson Filtration Deutschland Gm", None, None, None))
        r = await _mueller(tmp_path, inp=inp, name="Donaldson Filtration Deutschland GmbH")
        assert (r.name_match, r.decision, _issues(r)) == (NAME_EXACT, AUTO_ACCEPT, [])

    @pytest.mark.asyncio
    async def test_branch_is_the_entity(self, tmp_path):
        inp = replace(MUELLER, names=("Müller Maschinenbau GmbH Werk Hamburg", None, None, None))
        r = await _mueller(tmp_path, inp=inp)
        assert (r.name_match, r.decision, _issues(r)) == (NAME_EXACT, AUTO_ACCEPT, ["BRANCH"])

    @pytest.mark.asyncio
    async def test_department_in_name_2_is_not_a_branch(self, tmp_path):
        inp = replace(MUELLER, names=("Müller Maschinenbau GmbH", "Institut für Werkstoffkunde",
                                      None, None))
        [r] = await run_lookups(_one(tmp_path, M_URL, f"Impressum Müller Maschinenbau GmbH "
                                     f"{M_ADDRESS} {M_QUOTE}",
                                     reading(M_VAT, "Müller Maschinenbau GmbH", M_QUOTE),
                                     key="Müller"), [inp])
        assert (r.name_match, r.decision, _issues(r)) == (NAME_EXACT, AUTO_ACCEPT, ["DEPARTMENT"])


# ── 3. Decision rules and VIES ───────────────────────────────────────────────

class TestDecision:
    @pytest.mark.asyncio
    async def test_auto_accept(self, tmp_path):
        r = await _grounded(tmp_path).lookup(BOSCH)
        assert (r.status, r.tier) == (FOUND_VERIFIED, "PROPOSED")
        assert (r.decision, r.confidence, _issues(r)) == (AUTO_ACCEPT, CONF_VERIFIED, [])
        assert (r.name_match, r.address_match, r.source_type, r.source_class) == (
            NAME_EXACT, ADDRESS_EXACT, SOURCE_COMPANY_IMPRESSUM, "A")

    @pytest.mark.asyncio
    async def test_vies_valid(self, tmp_path):
        r = await _mueller(tmp_path)
        assert (r.validation_status, r.decision, _issues(r)) == ("VALID", AUTO_ACCEPT, [])

    @pytest.mark.asyncio
    async def test_vies_unavailable_does_not_block_and_is_queued(self, tmp_path):
        r = await _mueller(tmp_path, vies_reply=vies_mock.error("MS_UNAVAILABLE"))
        assert (r.validation_status, r.decision, _issues(r)) == (
            "UNAVAILABLE", AUTO_ACCEPT, ["VIES_UNAVAILABLE"])
        queue = ViesRetryQueue(tmp_path / "queue.json")
        assert enqueue(queue, [r]) == 1 and queue.entries[M_VAT].state == "PENDING"

    @pytest.mark.asyncio
    async def test_vies_invalid_at_first_attempt(self, tmp_path):
        r = await _mueller(tmp_path, vies_reply=lambda b: vies_mock.invalid(
            b["countryCode"], b["vatNumber"]))
        assert (r.decision, r.confidence, _issues(r)) == (
            MANUAL_REVIEW, CONF_LOW, ["VIES_INVALID"])

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
        assert "CONFLICTING_SOURCES" in _issues(r)
        assert r.decision == CONFLICT and r.confidence == CONFLICT

    @pytest.mark.asyncio
    async def test_register_mismatch_blocks_auto_accept(self, tmp_path):
        r = await _grounded(tmp_path).lookup(replace(BOSCH, register_number="HRB 99999"))
        assert "REGISTER_MISMATCH" in _issues(r) and r.decision == MANUAL_REVIEW

    @pytest.mark.parametrize("result, decision, confidence", [
        (FoundResult("grounded", NOT_RUN), None, None),
        (FoundResult("grounded", NOT_FOUND), DECISION_NOT_FOUND, CONF_NOT_FOUND),
        (FoundResult("grounded", LOOKUP_INCOMPLETE), DECISION_NOT_FOUND, CONF_NOT_FOUND),
        (FoundResult("llm_only", FOUND_UNVERIFIED, "DE811128135"), MANUAL_REVIEW, CONF_LOW),
    ])
    def test_rules(self, result, decision, confidence):
        assert (result.decision, result.confidence) == (decision, confidence)


class TestViesRetry:
    def test_schedule_hourly_then_daily_then_over(self):
        t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
        assert next_attempt(t0, t0 + timedelta(hours=3)) == t0 + timedelta(hours=4)
        assert next_attempt(t0, t0 + timedelta(hours=25)) == t0 + timedelta(hours=49)
        assert next_attempt(t0, t0 + timedelta(days=7, hours=23)) is None

    def test_queue_persists_and_expires(self, tmp_path):
        t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
        queue = ViesRetryQueue(tmp_path / "q.json")
        queue.add(M_VAT, t0)
        queue.save()
        queue = ViesRetryQueue(tmp_path / "q.json")
        # Due at once (Step AF-2: the queue is the first VIES call).
        assert queue.due(t0 - timedelta(seconds=1)) == [] and queue.due(t0) == [M_VAT]
        queue.record(M_VAT, "SERVICE_UNAVAILABLE", t0 + timedelta(days=8))
        assert queue.entries[M_VAT].state == "EXPIRED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply, status, decision, issues", [
        (vies_mock.echo_valid_undisclosed, "VALID", AUTO_ACCEPT, None),
        (lambda b: vies_mock.invalid(b["countryCode"], b["vatNumber"]), "INVALID", MANUAL_REVIEW,
         "VIES_INVALID"),
    ])
    async def test_retry_answer_is_written_back(self, tmp_path, reply, status, decision, issues):
        r = await _mueller(tmp_path / "a", vies_reply=vies_mock.error("MS_UNAVAILABLE"))
        row = r.columns()
        assert row["VAT_Found_Issues"] == "VIES_UNAVAILABLE"
        t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
        queue = ViesRetryQueue(None)
        enqueue(queue, [r], t0)
        asked = await queue.process(_vies(EvidenceCache(tmp_path / "b"), reply),
                                    t0 + timedelta(hours=1))
        assert asked == {M_VAT: status}
        assert apply_answers([row], queue.answers()) == 1
        assert (row["VAT_Found_Validation_Status"], row["VAT_Found_Decision"],
                row["VAT_Found_Issues"]) == (status, decision, issues)
        assert row["VAT_Found_Validation_Date"]


# ── 4. Entity-level results ──────────────────────────────────────────────────

class TestEntityLevel:
    def test_key(self):
        a = replace(MUELLER, names=("Müller Maschinenbau GmbH", "Vertrieb", None, None))
        b = replace(MUELLER, customer="2", street="Industriestraße", house_number="20",
                    names=("Mueller Maschinenbau GmbH", "Institut für Werkstoffkunde", None, None))
        assert entity_key(a) == entity_key(b) == "mueller maschinenbau|gmbh|gerlingen"
        assert entity_key(replace(MUELLER, city="Stuttgart")) != entity_key(MUELLER)
        assert entity_key(replace(MUELLER, names=(None, None, None, None))) is None

    @pytest.mark.asyncio
    async def test_one_lookup_both_rows_filled(self, tmp_path):
        engine = _one(tmp_path, M_URL, f"Impressum Müller Maschinenbau GmbH {M_ADDRESS} {M_QUOTE}",
                      reading(M_VAT, "Müller Maschinenbau GmbH", M_QUOTE), key="Müller")
        first = MUELLER
        second = replace(MUELLER, customer="2", street="Industriestraße", house_number="20",
                         names=("Müller Maschinenbau GmbH", "Institut für Werkstoffkunde",
                                None, None))
        a, b = await run_lookups(engine, [first, second])
        assert engine.stats()["llm_reads"] == 1
        assert a.value == b.value == M_VAT and a.decision == b.decision == AUTO_ACCEPT
        assert (a.address_match, b.address_match) == (ADDRESS_EXACT, ADDRESS_SAME_TOWN)
        assert _issues(a) == [] and _issues(b) == ["ADDRESS_DIFFERS", "DEPARTMENT"]
        assert lookup_kpis([a, b])["researched_customers"] == 1


# ── 5. Issue codes ───────────────────────────────────────────────────────────

_CASES = [
    dict(), dict(address="Hauptstraße 12, 70839 Gerlingen"), dict(address=""),
    dict(address="Königstraße 1, 70173 Stuttgart"), dict(name="Müller Maschinenbau AG"),
    dict(name="Müller Maschinenbau GmbH & Co. KG"), dict(name="Müller Werkzeugbau GmbH"),
    dict(url="https://www.northdata.de/Mueller+Maschinenbau"),
    dict(vies_reply=vies_mock.error("MS_UNAVAILABLE")),
    dict(vies_reply=lambda b: vies_mock.invalid(b["countryCode"], b["vatNumber"])),
    dict(inp=replace(MUELLER, names=("Müller Maschinenbau", None, None, None)),
         extra=f"Müller Maschinenbau GmbH & Co. KG USt-IdNr. {M_OTHER}"),
]


class TestIssueCodes:
    def test_blocking_codes_are_listed_codes(self):
        assert BLOCKING_ISSUES <= set(ISSUE_ORDER)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", _CASES)
    async def test_invariant(self, tmp_path, case):
        r = await _mueller(tmp_path, **case)
        blocking = [i for i in _issues(r) if is_blocking(i)]
        if r.decision in (MANUAL_REVIEW, CONFLICT):
            assert blocking, (r.decision, _issues(r))
        if r.decision == AUTO_ACCEPT:
            assert not blocking, _issues(r)
        assert r.columns()["VAT_Found_Issues"] == ("; ".join(_issues(r)) or None)

    @pytest.mark.asyncio
    async def test_sap_vat_outdated(self, tmp_path):
        engine = _one(tmp_path, M_URL, f"Impressum Müller Maschinenbau GmbH {M_ADDRESS} {M_QUOTE}",
                      reading(M_VAT, "Müller Maschinenbau GmbH", M_QUOTE), key="Müller")
        row = {"Customer": "1", "Country/Region Key": "DE", "VAT Registration No.": M_OTHER,
               "Name": "Müller Maschinenbau GmbH", "Street": "Hauptstraße", "House Number": "5",
               "Postal Code": "70839", "City": "Gerlingen"}
        out, summary = await validate_rows([row], _vies(EvidenceCache(tmp_path / "v")),
                                           lookup=engine, scope="all")
        assert (out[0]["VAT_Found_Decision"], out[0]["VAT_Found_Issues"]) == (
            AUTO_ACCEPT, "SAP_VAT_OUTDATED")
        assert summary["lookup"]["kpis"]["issues"] == {AUTO_ACCEPT: {"SAP_VAT_OUTDATED": 1}}


# ── 6. Source classes ────────────────────────────────────────────────────────

class TestSourceClasses:
    @pytest.mark.parametrize("url, kw, expected", [
        ("https://www.bundesanzeiger.de/pub/x", {}, SOURCE_OFFICIAL_FILING),
        ("https://www.unternehmensregister.de/ureg/x", {}, SOURCE_OFFICIAL_FILING),
        ("https://www.handelsregister.de/rp_web/x", {}, SOURCE_AGGREGATOR),
        ("https://www.dtvp.de/Center/company/x", {}, SOURCE_PROCUREMENT_PROFILE),
        ("https://www.evergabe-online.de/x", {}, SOURCE_PROCUREMENT_PROFILE),
        ("https://directory.peppol.eu/public/x", {}, SOURCE_PEPPOL_DIRECTORY),
        ("https://web.archive.org/web/20210305/https://x.example/impressum", {}, SOURCE_WEB_ARCHIVE),
        ("https://www.northdata.de/x", dict(impressum=True), SOURCE_AGGREGATOR),
        ("https://x.example/impressum", dict(impressum=True), SOURCE_COMPANY_IMPRESSUM),
        ("https://x.example/agb.pdf", dict(own_domain=True), SOURCE_COMPANY_WEBSITE),
        ("https://www.uni-stuttgart.de/einkauf", dict(core=("universitaet", "stuttgart")),
         SOURCE_COMPANY_WEBSITE),
        ("https://www.kit.edu/einkauf", dict(core=("karlsruher", "institut", "fuer", "technologie")),
         SOURCE_COMPANY_WEBSITE),
        ("https://some-blog.example/post", dict(core=("universitaet", "stuttgart")), SOURCE_AGGREGATOR),
    ])
    def test_types(self, url, kw, expected):
        kwargs = dict(impressum=False, own_domain=None)
        kwargs.update(kw)
        assert source_type(url, **kwargs) == expected

    def test_directory_hosts(self):
        assert is_directory_host("https://www.northdata.de/x")
        assert is_directory_host("https://www.wer-zu-wem.de/firma/x")
        assert not is_directory_host("https://www.unternehmensregister.de/x")

    @pytest.mark.asyncio
    async def test_aggregator_is_secondary(self, tmp_path):
        r = await _mueller(tmp_path, url="https://www.northdata.de/Mueller+Maschinenbau")
        assert (r.source_class, r.decision, r.confidence, _issues(r)) == (
            "C", MANUAL_REVIEW, CONF_MEDIUM, ["SECONDARY_SOURCE"])

    @pytest.mark.asyncio
    async def test_university_procurement_page_is_class_a(self, tmp_path):
        uni = LookupInput("2", ("Universität Stuttgart", None, None, None), "Keplerstraße", "7",
                          "70174", "Stuttgart", "DE")
        url = "https://www.uni-stuttgart.de/universitaet/einkauf/"
        quote = "Umsatzsteuer-Identifikationsnummer: DE147794196"
        body = f"Einkauf und Beschaffung Universität Stuttgart Keplerstraße 7 70174 Stuttgart {quote}"
        engine = _web(tmp_path, {"vat_id": "DE147794196", "source_url": url, "evidence_quote": quote,
                                 "attributed_to": "Universität Stuttgart"},
                      {url: page(url, body, title="Einkauf")})
        r = await engine.lookup(uni)
        assert (r.source_type, r.source_class, r.decision) == (SOURCE_COMPANY_WEBSITE, "A", AUTO_ACCEPT)

    @pytest.mark.asyncio
    async def test_peppol_directory_hit_is_class_a(self, tmp_path):
        def peppol_api(request: httpx.Request) -> httpx.Response:
            assert request.url.params["name"] == "Müller Maschinenbau GmbH"
            return httpx.Response(200, json={"matches": [
                {"participantID": {"scheme": "iso6523-actorid-upis", "value": f"9930:{M_VAT.lower()}"},
                 "entities": [{"name": [{"name": "Müller Maschinenbau GmbH (1000)"}],
                               "countryCode": "DE", "geoInfo": "Hauptstraße 5; 70839; Gerlingen"}]},
                {"participantID": {"scheme": "iso6523-actorid-upis", "value": "0204:991-12345-67"},
                 "entities": [{"name": [{"name": "Müller Maschinenbau GmbH"}], "countryCode": "DE"}]},
            ]})

        cache = EvidenceCache(tmp_path / "p")
        directory = PeppolDirectory(cache, transport=httpx.MockTransport(peppol_api))
        engine = _web(tmp_path, {"vat_id": None}, {}, peppol=directory)
        r = await engine.lookup(MUELLER)
        assert (r.value, r.source_type, r.source_class, r.decision, _issues(r)) == (
            M_VAT, SOURCE_PEPPOL_DIRECTORY, "A", AUTO_ACCEPT, [])
        assert "PEPPOL_PARTICIPANT:9930:de811128135" in r.flags

    @pytest.mark.asyncio
    async def test_archive_snapshot_on_an_active_record_is_not_used(self, tmp_path):
        url = "https://web.archive.org/web/20210305120000/https://www.mueller.example/impressum"
        engine = _web(tmp_path, {"vat_id": M_VAT, "source_url": url, "evidence_quote": M_QUOTE,
                                 "attributed_to": "Müller Maschinenbau GmbH"},
                      {url: page(url, f"Impressum Müller Maschinenbau GmbH {M_ADDRESS} {M_QUOTE}")})
        r = await engine.lookup(MUELLER)
        assert (r.status, r.value, r.decision) == (NOT_FOUND, None, DECISION_NOT_FOUND)
        assert "ARCHIVE_NOT_USED" in r.flags

    @pytest.mark.asyncio
    async def test_archive_snapshot_on_a_deleted_record(self, tmp_path):
        url = "https://web.archive.org/web/20210305120000/https://www.mueller.example/impressum"
        engine = _web(tmp_path, {"vat_id": M_VAT, "source_url": url, "evidence_quote": M_QUOTE,
                                 "attributed_to": "Müller Maschinenbau GmbH"},
                      {url: page(url, f"Impressum Müller Maschinenbau GmbH {M_ADDRESS} {M_QUOTE}")})
        r = await engine.lookup(replace(MUELLER, deleted=True))
        assert (r.source_type, r.decision, r.confidence, r.date) == (
            SOURCE_WEB_ARCHIVE, MANUAL_REVIEW, CONF_MEDIUM, "2021-03-05")
        assert _issues(r) == ["SECONDARY_SOURCE"]

    @pytest.mark.asyncio
    async def test_marketplace_is_rejected(self, tmp_path):
        url = "https://www.amazon.de/sp?seller=x"
        engine = _web(tmp_path, {"vat_id": M_VAT, "source_url": url, "evidence_quote": M_QUOTE,
                                 "attributed_to": "Müller Maschinenbau GmbH"},
                      {url: page(url, f"Müller Maschinenbau GmbH {M_ADDRESS} {M_QUOTE}")})
        r = await engine.lookup(MUELLER)
        assert r.status == NOT_FOUND and "REJECTED_SOURCE" in r.flags


# ── Extraction ───────────────────────────────────────────────────────────────

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
        assert "OTHER_VAT_IDS_ON_PAGE" in _issues(r) and r.decision == AUTO_ACCEPT


# ── Audit trail and KPIs ─────────────────────────────────────────────────────

class TestAuditTrail:
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
        assert match_score(NAME_EXACT, {"street": True, "postal_code": True, "city": True,
                                        "domain": None, "register": None}) == 100

    @pytest.mark.asyncio
    async def test_evidence_columns(self, tmp_path):
        engine = _grounded(tmp_path)
        [r] = await run_lookups(engine, [BOSCH])
        cols = r.columns()
        assert list(cols) == LOOKUP_COLUMNS
        assert LOOKUP_COLUMNS[-2:] == ["VAT_Found_Source_Class", "VAT_Found_Issues"]
        assert cols["VAT_Found_Page_Title"] == "Impressum"
        assert cols["VAT_Found_Published_Address"] == ADDRESS
        assert cols["VAT_Found_Validation_Status"] == "VALID"
        assert cols["VAT_Found_Validation_Date"]
        assert cols["VAT_Found_Match_Score"] == 100
        assert (cols["VAT_Found_Source_Class"], cols["VAT_Found_Issues"]) == ("A", None)
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
        assert "EVIDENCE_NOT_VERIFIED" in _issues(r) and r.decision == MANUAL_REVIEW

    @pytest.mark.asyncio
    async def test_web_search_result_carries_the_same_evidence(self, tmp_path):
        engine = _web(tmp_path, {"vat_id": "DE811128135", "source_url": IMPRESSUM,
                                 "evidence_quote": QUOTE, "attributed_to": "Robert Bosch GmbH",
                                 "published_address": ADDRESS}, {IMPRESSUM: page(IMPRESSUM, BODY)})
        r = await engine.lookup(BOSCH)
        assert (r.decision, r.published_address, r.source_type) == (
            AUTO_ACCEPT, ADDRESS, SOURCE_COMPANY_IMPRESSUM)
        assert engine.agent_version.startswith("llm_web|vat_llm_web/v4:")

    @pytest.mark.asyncio
    async def test_unvisited_source_is_recorded_not_blocking(self, tmp_path):
        engine = _web(tmp_path, {"vat_id": "DE811128135", "source_url": IMPRESSUM,
                                 "evidence_quote": QUOTE, "attributed_to": "Robert Bosch GmbH"},
                      {IMPRESSUM: page(IMPRESSUM, BODY)}, visited=[])
        r = await engine.lookup(BOSCH)
        assert "SOURCE_NOT_VISITED" in r.flags and r.decision == AUTO_ACCEPT

    def test_kpis_count_each_entity_once(self):
        ok = FoundResult("grounded", FOUND_VERIFIED, "DE811128135", source_url=IMPRESSUM,
                         name_match=NAME_EXACT, address_match=ADDRESS_EXACT,
                         source_type=SOURCE_COMPANY_IMPRESSUM, validation_status="VALID",
                         seconds=2.0, entity="e1")
        none = FoundResult("grounded", NOT_FOUND, seconds=4.0, entity="e2")
        k = lookup_kpis([ok, replace(ok), none, None, FoundResult("grounded", NOT_RUN)])
        assert k["researched_customers"] == 2
        assert (k["discovery_rate"], k["verification_rate"]) == (0.5, 0.5)
        assert (k["auto_accept_rate"], k["not_found_rate"], k["manual_review_rate"]) == (0.5, 0.5, 0.0)
        assert k["avg_seconds_per_customer"] == 3.0
        assert k["decision"] == {AUTO_ACCEPT: 1, DECISION_NOT_FOUND: 1}
        assert k["issues"] == {AUTO_ACCEPT: {}, DECISION_NOT_FOUND: {}}
