"""llm_web "hybrid" mode (Steps AA / AB; vat/web_query.py, vat/web_pages.py,
vat/lookup_web.py).

Search 1 is name + city, built by code; only the entity's own hosts (and a
parent's for a department) are fetched, one page per host, links followed;
a host whose page names another entity or town is rejected; search 2 (the
model's query) only when search 1 showed no own host; each call one search
and no page opens; the reader reads the sections; Peppol for every entity;
the grounded engine once for what is still NOT_FOUND (when on);
NOT_FOUND_NO_VAT_EXPECTED. The model is a script keyed by call type. Company
data: the fixture's public Impressum data or obviously fake.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat import lookup_grounded
from vat.llm_cache import VatLLM
from vat.lookup import (
    AUTO_ACCEPT,
    CONFLICT,
    LOOKUP_INCOMPLETE,
    NO_VAT_INFERRED,
    NO_VAT_STATED,
    NOT_FOUND,
    FoundResult,
    LookupInput,
)
from vat.lookup_web import (
    PDF_SOURCE,
    PUBLIC_BODY,
    QUERY_NOT_RUN_AS_GIVEN,
    SEARCH_LIMIT_REACHED,
    WebSearchLookup,
)
from vat.peppol import PeppolDirectory, parse_matches
from vat.prompts import (
    LLM_WEB_RANK_SYSTEM_PROMPT,
    LLM_WEB_READ_SYSTEM_PROMPT,
    LLM_WEB_RUN_SYSTEM_PROMPT,
    LLM_WEB_SECOND_SYSTEM_PROMPT,
    NAME2_SYSTEM_PROMPT,
)
from vat.vies_client import VIESClient
from vat.web_pages import OWN, PARENT, POINTER, VERIFY, follow_targets, host_check, select_pages
from vat.web_query import (
    first_query,
    is_public_body,
    no_vat_statement,
    page_sections,
    query_name,
    search_city,
    search_name,
)

BOSCH = LookupInput(
    customer="99000003", names=("Robert Bosch GmbH", None, None, None),
    street="Robert-Bosch-Platz", house_number="1", postal_code="70839",
    city="GERLINGEN", country="DE",
)
HOME = "https://www.bosch.example/"
IMPRESSUM = "https://www.bosch.example/impressum"
QUOTE = "Umsatzsteuer-Identifikationsnummer: DE 811 128 135"
BODY = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen " + QUOTE
USAGE = {"input_tokens": 3000, "cached_tokens": 0, "output_tokens": 200, "tool_calls": 1,
         "search_calls": 1, "other_tool_calls": 0}
READ_USAGE = dict(USAGE, tool_calls=0, search_calls=0)
NO_SEARCH_2 = (json.dumps({"query": "x", "name_used": "y", "why_this_query": "z", "domain_hint": None,
                           "results": []}), [], USAGE)
HELPER_USAGE = {"input_tokens": 900, "cached_tokens": 0, "output_tokens": 40, "reasoning_tokens": 0,
                "tool_calls": 0, "search_calls": 0, "other_tool_calls": 0}
NO_RANKING = (json.dumps({"ranked": []}), [], HELPER_USAGE)


def found(vat="DE811128135", url=IMPRESSUM, quote=QUOTE, name="Robert Bosch GmbH", no_vat=None):
    return {"vat_id": vat, "source_url": url, "evidence_quote": quote, "attributed_to": name,
            "published_address": None, "other_vat_ids_on_page": [], "no_vat_statement": no_vat}


NOTHING = found(None, None, None, None)


def linked(url, body, links):
    p = page(url, body, title="Home")
    p.content.links = [list(x) for x in links]
    return p


class Model:
    """search(system, user, effort) by call type: run (search 1 and the
    aggregator search), second (search 2), read, rank, name2. Each entry is a
    list of replies used in order."""

    def __init__(self, run=(), second=(), read=(), rank=(), name2=()):
        second = list(second) or [NO_SEARCH_2]
        self.replies = {LLM_WEB_RUN_SYSTEM_PROMPT: list(run), LLM_WEB_SECOND_SYSTEM_PROMPT: second,
                        LLM_WEB_READ_SYSTEM_PROMPT: list(read), LLM_WEB_RANK_SYSTEM_PROMPT: list(rank) or [NO_RANKING],
                        NAME2_SYSTEM_PROMPT: list(name2)}
        self.calls: list[tuple[str, str]] = []
        self.efforts: list[tuple[str, str]] = []

    async def __call__(self, system, user, effort):
        kind = {LLM_WEB_RUN_SYSTEM_PROMPT: "run", LLM_WEB_SECOND_SYSTEM_PROMPT: "second",
                LLM_WEB_READ_SYSTEM_PROMPT: "read", LLM_WEB_RANK_SYSTEM_PROMPT: "rank",
                NAME2_SYSTEM_PROMPT: "name2"}[system]
        self.calls.append((kind, user))
        self.efforts.append((kind, effort))
        replies = self.replies[system]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(query, visited, usage=USAGE, results=None):
    return (json.dumps({"query_run": query, "results": results or []}), list(visited),
            dict(usage, queries=[query]))


def read(answer):
    return json.dumps(answer), [], READ_USAGE


def _settings(**kw):
    # The Step AF helpers (ranking call, Name-2 classifier, aggregator search)
    # are off unless a test turns them on: the scripts below list the core calls.
    kw = {"vat_web_ranking": "heuristic", "vat_name2_classifier": False, "vat_aggregator_fallback": False, **kw}
    return replace(get_settings(), vat_web_query_mode="hybrid", vies_backoff_seconds=0.0,
                   vies_min_interval_ms=0, vies_requester_vat="", cache_frozen=False,
                   vat_web_retry_backoff_seconds=0.0, **kw)


def _engine(tmp_path, model, pages, *, grounded=None, peppol=None, web=None, **settings):
    cache = EvidenceCache(tmp_path)
    s = _settings(**settings)
    vies = VIESClient(s, cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
    return WebSearchLookup(s, vies, llm=VatLLM(cache.namespace("vat_llm"), search=model),
                           fetcher=web or FakeWeb(pages),
                           peppol=peppol or PeppolDirectory(cache, enabled=False), grounded=grounded)


class TestQuery:
    def test_search_1_is_name_and_city(self):
        assert first_query("Robert Bosch GmbH", "GERLINGEN") == '"Robert Bosch GmbH" Gerlingen'

    @pytest.mark.parametrize("name, expected", [
        ("Robert Bosch GmbH Werk Feuerbach", "Robert Bosch GmbH"),
        ("Müller GmbH c/o Meier", "Müller GmbH"),
        ("Muster AG z. Hd. Einkauf", "Muster AG"),
        ("Meier u. Söhne GmbH", "Meier & Söhne GmbH"),
        ("Meier + Söhne GmbH", "Meier & Söhne GmbH"),
        ("R+V Allgemeine Versicherung AG", "R+V Allgemeine Versicherung AG"),
        ("Musterfirma GmbH Abteilung Einkauf", "Musterfirma GmbH"),
        ("Karlsruher Institut für Technologie", "Karlsruher Institut für Technologie"),
        ("IBQ - Institut für Baustoff-", "IBQ - Institut"),
        ("Keysight Technologies Deutschland Gm", "Keysight Technologies Deutschland"),
    ])
    def test_name_for_search(self, name, expected):
        assert search_name(name) == expected

    @pytest.mark.parametrize("name_1, name_2, expected", [
        ("Keysight Technologies GmbH", "Deutschland GmbH & Co KG",
         '"Keysight Technologies Deutschland GmbH & Co KG" Böblingen'),
        ("BAK Boysen Abgaskomponenten GmbH", "& Co. KG", '"BAK Boysen Abgaskomponenten GmbH & Co. KG" Böblingen'),
        ("IBQ - Institut für Baustoff-", "Qualitätssicherung GmbH",
         '"IBQ - Institut für Baustoff-Qualitätssicherung GmbH" Böblingen'),
        ("Kliniken des Landkreises Lörrach", "GmbH", '"Kliniken des Landkreises Lörrach GmbH" Böblingen'),
        ("KHP Medizinisches Fachzentrum GmbH", "& Co KG pflege-partner",
         '"KHP Medizinisches Fachzentrum GmbH & Co KG" Böblingen'),
    ])
    def test_name_2_continues_the_name(self, name_1, name_2, expected):
        assert first_query(name_1, "BOEBLINGEN", name_2) == expected
        assert query_name(name_1, name_2)[2] is True

    @pytest.mark.parametrize("name_2", ["Inst für Technische Chemie und", "FB Chemie AG Physikalische Chemie",
                                        "Elkalub Hochleistungs-Schmierstoffe", ">> 1256685",
                                        "Medicinal Chemistry, GG/RC", "Abt. Chemie/Reag./Diagnostik"])
    def test_department_brand_or_note_is_not_appended(self, name_2):
        # Karlsruher Institut für Technologie is exactly 35 characters and complete.
        assert query_name("Karlsruher Institut für Technologie", name_2)[2] is False
        assert query_name("Abbott GmbH & Co. KG", name_2)[2] is False

    def test_a_name_at_the_length_limit_stays_unquoted(self):
        q = first_query("ZeWiS - Zentrum für Wissenschaftlic", "OBERNBURG", "Services")
        assert q == "ZeWiS - Zentrum für Wissenschaftlic Services Obernburg"  # rule 10, unquoted

    def test_quoted_only_when_complete_and_two_words(self):
        assert first_query("Steinbeis-Transferzentrum", "MANNHEIM") == "Steinbeis-Transferzentrum Mannheim"
        assert not first_query("Keysight Technologies Deutschland Gm", "BOEBLINGEN").startswith('"')

    @pytest.mark.parametrize("city, expected", [
        ("MUNSTER", "Münster"), ("HATTERSHEIMAMMAIN", "Hattersheim"),
        ("FRANKFURT AM MAIN", "Frankfurt am Main"), ("BIETIGHEIM-BISSINGEN", "Bietigheim-Bissingen"),
        ("Tübingen", "Tübingen"),
    ])
    def test_city_folded_never_quoted(self, city, expected):
        assert search_city(city) == expected
        assert f'"{expected}"' not in first_query("Robert Bosch GmbH", city)

    @pytest.mark.parametrize("name, expected", [
        ("Grundschule am Park", True), ("Landratsamt Calw", True), ("Finanzamt Stuttgart", True),
        ("Hochschule Karlsruhe", False), ("Universitätsklinikum Tübingen", False),
        ("Robert Bosch GmbH", False),
    ])
    def test_public_body(self, name, expected):
        assert is_public_body(name) is expected


class TestSections:
    def test_number_far_into_a_long_page_is_kept(self):
        text = "Menü " * 5000 + "Impressum Robert Bosch GmbH " + QUOTE + " Footer " * 3000
        cut = page_sections(text, window=1500, limit=12000)
        assert QUOTE in cut and cut.startswith("Menü") and len(cut) <= 12100

    def test_short_page_unchanged(self):
        assert page_sections(BODY) == BODY

    @pytest.mark.parametrize("text", [
        "Wir verfügen über keine Umsatzsteuer-Identifikationsnummer.",
        "Der Verein ist nicht umsatzsteuerpflichtig.",
        "Als Kleinunternehmer im Sinne von § 19 Abs. 1 UStG wird keine Umsatzsteuer berechnet.",
    ])
    def test_no_vat_statement(self, text):
        assert no_vat_statement("Impressum Muster e.V. " + text)

    def test_ordinary_impressum_has_none(self):
        assert no_vat_statement(BODY) is None

    def test_an_article_about_kleinunternehmer_is_not_a_statement(self):
        # steuer-erklaerer.de, fetched for Kliniken des Landkreises Lörrach.
        assert no_vat_statement("Checkliste § 14 UStG, inkl. Kleinunternehmer-Regeln und der "
                                "E-Rechnungs-Pflicht ab 2025. Kleinunternehmer-Spezial") is None


class TestSelection:
    def test_own_hosts_only_one_page_each_best_path(self):
        results = [{"url": "https://www.northdata.de/Robert-Bosch"}, {"url": HOME},
                   {"url": "https://www.bosch.example/en/imprint"}, {"url": IMPRESSUM},
                   {"url": "https://www.other.example/impressum"}]
        picked, skipped = select_pages(results, BOSCH)
        # Another host's Impressum comes last, to verify (own only if it names the entity).
        assert picked == [(IMPRESSUM, OWN), ("https://www.other.example/impressum", VERIFY)]
        why = dict(skipped)
        assert why["https://www.northdata.de/Robert-Bosch"] == "directory: pointer only"
        assert why[HOME] == why["https://www.bosch.example/en/imprint"] == "one page per host"

    def test_directory_points_to_the_website(self):
        results = [{"url": "https://www.northdata.de/Muster", "website": "https://www.xyz-werke.example/"}]
        picked, _ = select_pages(results, BOSCH)
        assert picked == [("https://www.xyz-werke.example/", POINTER)]

    def test_title_with_the_full_name_makes_an_impressum_own(self):
        results = [{"url": "https://www.rb-online.example/impressum", "title": "Impressum | Robert Bosch GmbH"}]
        assert select_pages(results, BOSCH)[0] == [("https://www.rb-online.example/impressum", OWN)]

    def test_a_directory_page_titled_with_the_name_is_not_own(self):
        results = [{"url": "https://www.findglocal.example/DE/Gerlingen/123/Robert-Bosch",
                    "title": "Robert Bosch GmbH, Gerlingen - Firmenauskunft"}]
        assert select_pages(results, BOSCH)[0] == []

    @pytest.mark.parametrize("url", ["https://r-biopharm.mitarbeiterangebote.de/meta/imprint",
                                     "https://de.industryarena.com/bosch/impressum", "https://studi.info/impressum",
                                     "https://steuer-erklaerer.de/bosch"])
    def test_blocklist_never_fetched(self, url):
        picked, skipped = select_pages([{"url": url}], BOSCH)
        assert picked == [] and skipped[0][1] in ("blocklist (class C)", "not the entity's own host")

    def test_generic_words_do_not_make_a_host_own(self):
        lg = replace(BOSCH, names=("LG International (Deutschland) GmbH", None, None, None))
        assert select_pages([{"url": "https://www.international-deutschland.example/impressum"}], lg)[0] == \
            [("https://www.international-deutschland.example/impressum", VERIFY)]
        assert select_pages([{"url": "https://www.international-deutschland.example/"}], lg)[0] == []

    def test_acronym_domain_and_parent_host(self):
        kit = replace(BOSCH, names=("Karlsruher Institut für Technologie", None, None, None))
        assert select_pages([{"url": "https://www.kit.edu/impressum.php"}], kit)[0] == \
            [("https://www.kit.edu/impressum.php", OWN)]
        # A parent's host counts only when Name 1–4 name the parent (Step AD).
        zewis = replace(BOSCH, names=("ZeWiS - Zentrum für Wissenschaftlic", "Services", None, None))
        picked, _ = select_pages([{"url": "https://www.th-ab.de/impressum"}], zewis)
        assert picked == [("https://www.th-ab.de/impressum", VERIFY)]
        mri = replace(BOSCH, names=("Technische Universität München", "Klinikum rechts der Isar", None, None))
        assert select_pages([{"url": "https://www.mri.tum.de/impressum"}], mri)[0] == \
            [("https://www.mri.tum.de/impressum", OWN)]
        hs = replace(BOSCH, names=("Hochschule des Landes BaWü", "Staatliche Akademie der bildenden", None, None))
        assert select_pages([{"url": "https://www.dhbw-stuttgart.de/impressum"}], hs)[0] == \
            [("https://www.dhbw-stuttgart.de/impressum", VERIFY)]  # no DHBW from type + city

    def test_follow_targets(self):
        links = [["Produkte", "/produkte"], ["Imprint", "/en/imprint"], ["Impressum", "/impressum"],
                 ["AGB", "/agb"], ["Partner", "https://elsewhere.example/impressum"]]
        targets = follow_targets(HOME, links, [], {("bosch.example", "/")})
        assert targets[:2] == [IMPRESSUM, "https://www.bosch.example/agb"]  # one twin, same host only

    @pytest.mark.parametrize("text, url, kind, reason, named", [
        # Another town never rejects: the address issue sends it to review.
        ("Impressum Robert Bosch GmbH Weseler Str. 1 47169 Duisburg", IMPRESSUM, OWN, None, "EXACT"),
        ("Impressum Robert Bosch AG 70839 Gerlingen", IMPRESSUM, OWN, None, "EXACT"),
        # A legal page that does not name the entity rejects the host (Step AD 3.2).
        ("Impressum Bauer KG Hauptstr. 1 12345 Musterstadt", IMPRESSUM, OWN, "NAME_NOT_ON_PAGE", "ABSENT"),
        ("Impressum Bauer KG 70839 Gerlingen", IMPRESSUM, OWN, "NAME_NOT_ON_PAGE", "ABSENT"),
        # A homepage is not name-checked: follow its Impressum link.
        ("Willkommen bei Elkalub Hochleistungs-Schmierstoffe 12345 Musterstadt", HOME, OWN, None, "NOT_CHECKED"),
        # A host to verify must name the entity on its Impressum.
        ("Impressum Pharma Test AG 63512 Hainburg", "https://www.pharma-test.example/impressum", VERIFY,
         "NAME_NOT_ON_PAGE", "ABSENT"),
        ("Robert Bosch GmbH Datenschutz", "https://www.bosch.example/agb.pdf", OWN, None, "EXACT"),
    ])
    def test_host_check(self, text, url, kind, reason, named):
        assert host_check(text, url, BOSCH, kind) == (reason, named)

    def test_initials_are_the_name(self):
        hp = replace(BOSCH, names=("Hewlett-Packard Deutschland GmbH", None, None, None),
                     postal_code="71034", city="BOEBLINGEN")
        text = "Impressum HP Deutschland GmbH Schickardstr. 32 71034 Böblingen USt-IdNr DE815547421"
        assert host_check(text, "https://www.hp.example/de-de/impressum.html", hp, OWN) == (None, "ABBREVIATED")

    def test_the_model_s_name_used_is_the_name(self):
        text = "Impressum Hochschulen Fresenius gGmbH Limburger Str. 2 65510 Idstein"
        eff = replace(BOSCH, names=("Europa Fachhochschule Fresenius", "GmbH", None, None),
                      postal_code="99999", city="ANDERSWO")
        assert host_check(text, IMPRESSUM, eff, OWN)[0] == "NAME_NOT_ON_PAGE"
        assert host_check(text, IMPRESSUM, eff, OWN, ("Hochschulen Fresenius",)) == (None, "NAME_USED")

    def test_host_accepted(self):
        assert host_check(BODY, IMPRESSUM, BOSCH, OWN) == (None, "EXACT")

    def test_another_hosts_impressum_is_a_host_to_verify(self):
        jm = replace(BOSCH, names=("J&M Analytik AG", None, None, None))
        picked, _ = select_pages([{"url": "https://www.pharma-test.example/en/jm/imprint/"},
                                  {"url": "https://www.pharma-test.example/produkte"}], jm)
        assert picked == [("https://www.pharma-test.example/en/jm/imprint/", VERIFY)]


class TestPeppol:
    def test_leitweg_id_with_a_vat_id(self):
        body = {"matches": [
            {"participantID": {"value": "0204:08222000-de143845342-36"},
             "entities": [{"name": [{"name": "Universität Mannheim"}], "countryCode": "DE"}]},
            {"participantID": {"value": "0204:05911-35001-20"},
             "entities": [{"name": [{"name": "Klinikum X GmbH"}], "countryCode": "DE"}]}]}
        entries = parse_matches(body)
        assert [(e.vat_id, e.participant) for e in entries] == [("DE143845342", "0204:08222000-de143845342-36")]


class TestFlow:
    @pytest.mark.asyncio
    async def test_found_by_search_1(self, tmp_path):
        q1 = first_query("Robert Bosch GmbH", "GERLINGEN")
        model = Model(run=[run(q1, [IMPRESSUM])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(BOSCH)
        assert (r.value, r.decision) == ("DE811128135", AUTO_ACCEPT)
        assert "FOUND_BY_SEARCH:1" in r.flags and "ENGINE:llm_web" in r.flags
        assert [k for k, _ in model.calls] == ["run", "read"]  # no search 2
        assert q1 in model.calls[0][1]
        assert (r.usage["searches"], r.usage["fetches"], r.usage["follows"], r.usage["found_by"]) == (1, 1, 0, 1)
        assert r.usage["found_via"] == "rank" and r.usage["reasoning_effort"] == "medium"
        assert r.usage["peppol_queries"] == 1 and r.usage["peppol_hit"] is False

    @pytest.mark.asyncio
    async def test_link_followed_from_the_homepage(self, tmp_path):
        model = Model(run=[run("q", [HOME])], read=[read(found())])
        pages = {HOME: linked(HOME, "Robert Bosch GmbH Willkommen", [["Impressum", "/impressum"]]),
                 IMPRESSUM: page(IMPRESSUM, BODY)}
        r = await _engine(tmp_path, model, pages).lookup(BOSCH)
        assert r.value == "DE811128135"
        assert (r.usage["fetches"], r.usage["follows"], r.usage["found_via"]) == (1, 1, "follow")
        assert r.usage["hosts"]["bosch.example"]["fetched"] == [HOME, IMPRESSUM]

    @pytest.mark.asyncio
    async def test_follows_capped_at_two(self, tmp_path):
        links = [["Impressum", "/impressum"], ["Steuern", "/steuern"], ["AGB", "/agb"]]
        pages = {HOME: linked(HOME, "Robert Bosch GmbH", links),
                 IMPRESSUM: linked(IMPRESSUM, "Impressum Robert Bosch GmbH", links),
                 "https://www.bosch.example/steuern": page("https://www.bosch.example/steuern", "Robert Bosch GmbH")}
        model = Model(run=[run("q", [HOME])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, pages).lookup(BOSCH)
        assert r.status == NOT_FOUND and r.usage["follows"] == 2
        # Its pages are near-empty (rendered by script): the host did not
        # answer, so search 2 runs; nothing VAT-like: no reader call.
        assert [k for k, _ in model.calls] == ["run", "second"] and r.usage["needs_js"] >= 1

    @pytest.mark.asyncio
    async def test_fetches_capped_at_four_hosts(self, tmp_path):
        # Step AF budget: 4 fetches per entity (the many-own-hosts budget of
        # Step AF-2 switched off here: tests/test_vat_step_af2.py).
        urls = [f"https://www.bosch{i}.example/impressum" for i in range(6)]
        model = Model(run=[run("q1", urls)], read=[read(NOTHING)])
        web = FakeWeb({u: page(u, "Impressum Robert Bosch GmbH 70839 Gerlingen") for u in urls})
        r = await _engine(tmp_path, model, {}, web=web, vat_web_max_follows=0,
                          vat_web_many_own_hosts=99).lookup(BOSCH)
        assert r.status == NOT_FOUND and r.usage["fetches"] == 4 and len(web.fetched) == 4
        assert [n["url"] for n in r.usage["not_fetched"]] == urls[4:]

    @pytest.mark.asyncio
    async def test_rejected_host_is_no_source(self, tmp_path):
        braun = replace(BOSCH, names=("Braun GmbH", None, None, None), city="KRONBERG IM TAUNUS",
                        postal_code="61476", street=None, house_number=None)
        other = "https://www.braun.example/impressum"
        model = Model(run=[run("q", [other])], second=[NO_SEARCH_2], read=[read(found(url=other))])
        pages = {other: page(other, "Impressum Müller Elektro AG Hauptstr. 1 12345 Musterstadt " + QUOTE)}
        r = await _engine(tmp_path, model, pages).lookup(braun)
        assert r.usage["hosts"]["braun.example"]["rejected"] == "NAME_NOT_ON_PAGE"
        # A rejected host did not answer: search 2 runs.
        assert r.status == NOT_FOUND and [k for k, _ in model.calls] == ["run", "second"]

    @pytest.mark.asyncio
    async def test_head_office_in_another_town_is_read_and_reviewed(self, tmp_path):
        # Grillo-Werke: SAP Frankfurt, the Impressum Duisburg.
        hq = page(IMPRESSUM, "Impressum Robert Bosch GmbH Weseler Str. 1 47169 Duisburg " + QUOTE)
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: hq}).lookup(BOSCH)
        assert r.value == "DE811128135" and r.decision == "MANUAL_REVIEW"
        assert r.usage["hosts"]["bosch.example"]["rejected"] is None

    @pytest.mark.asyncio
    async def test_search_2_only_when_no_own_host_was_seen(self, tmp_path):
        model = Model(run=[run("q", ["https://www.northdata.de/x"])], second=[NO_SEARCH_2], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {}).lookup(BOSCH)
        assert [k for k, _ in model.calls] == ["run", "second"] and r.usage["own_host_seen_1"] is False

    @pytest.mark.asyncio
    async def test_impressum_probed_when_the_one_found_is_empty(self, tmp_path):
        # TIB Chemicals: /de/de/impressum renders by script; /impressum is tried.
        empty = "https://www.bosch.example/de/de/impressum"
        agb = "https://www.bosch.example/agb"
        pages = {HOME: linked(HOME, "Robert Bosch GmbH", [["Impressum", "/de/de/impressum"], ["AGB", "/agb"]]),
                 empty: page(empty, "Impressum Produkte Karriere Kontakt"), agb: page(agb, "AGB Robert Bosch GmbH"),
                 IMPRESSUM: page(IMPRESSUM, BODY)}
        model = Model(run=[run("q", [HOME])], read=[read(found())])
        r = await _engine(tmp_path, model, pages).lookup(BOSCH)
        assert r.value == "DE811128135" and r.usage["pages"][empty]["empty"] is True
        assert r.usage["source_page"] == IMPRESSUM  # reached after the empty one
        # With every follow used, the probe still runs.
        model = Model(run=[run("q", [HOME])], read=[read(found())])
        r = await _engine(tmp_path / "b", model, pages, vat_web_max_follows=1).lookup(BOSCH)
        assert r.value == "DE811128135" and r.usage["probes"] == 1 and r.usage["found_via"] == "probe"

    @pytest.mark.asyncio
    async def test_verified_host_becomes_own(self, tmp_path):
        jm = replace(BOSCH, names=("J&M Analytik AG", None, None, None), postal_code="73457", city="ESSINGEN")
        url = "https://www.pharma-test.example/en/jm/imprint/"
        body = "Imprint J&M Analytik AG Willy-Messerschmitt-Str. 8 73457 Essingen USt-IdNr. DE 811 128 135"
        model = Model(run=[run("q", [url])], read=[read(found(url=url, name="J&M Analytik AG"))])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(jm)
        assert r.value == "DE811128135" and r.usage["hosts"]["pharma-test.example"]["kind"] == OWN

    @pytest.mark.asyncio
    async def test_blocked_host_browser_then_archive_then_incomplete(self, tmp_path, monkeypatch):
        calls = []

        async def browser(url, flags):
            calls.append(url)
            return None

        async def wayback(url):
            return None

        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        engine = _engine(tmp_path, model, {IMPRESSUM: 403})
        monkeypatch.setattr(engine._pages, "fetch_browser", browser)
        monkeypatch.setattr(engine, "_wayback", wayback)
        r = await engine.lookup(BOSCH)
        assert calls == [IMPRESSUM] and r.status == f"{LOOKUP_INCOMPLETE}:HTTP_403"
        assert r.decision == "NOT_FOUND" and r.usage["blocked"][0]["code"] == 403

    @pytest.mark.asyncio
    async def test_blocked_host_read_from_the_archive(self, tmp_path, monkeypatch):
        snap = "https://web.archive.org/web/20250101000000/https://www.bosch.example/impressum"

        async def browser(url, flags):
            return None

        async def wayback(url):
            return snap

        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found(url=snap))])
        engine = _engine(tmp_path, model, {IMPRESSUM: 403, snap: page(snap, BODY)})
        monkeypatch.setattr(engine._pages, "fetch_browser", browser)
        monkeypatch.setattr(engine, "_wayback", wayback)
        r = await engine.lookup(BOSCH)
        assert r.value == "DE811128135" and "ARCHIVE_SOURCE" in r.flags and r.decision == "MANUAL_REVIEW"

    @pytest.mark.asyncio
    async def test_content_filter_is_incomplete_never_not_found(self, tmp_path):
        stopped = ("", [], dict(USAGE, incomplete_reason="content_filter", api_status="incomplete"))
        model = Model(run=[stopped])
        r = await _engine(tmp_path, model, {}).lookup(BOSCH)
        assert r.status == f"{LOOKUP_INCOMPLETE}:CONTENT_FILTER" and len(model.calls) == 2  # one retry

    @pytest.mark.asyncio
    async def test_group_page_takes_the_block_that_names_the_entity(self, tmp_path):
        # A chain's Impressum: two companies, two numbers; the reader picked the first.
        body = ("Impressum Diakonie Holding gGmbH Hauptstr. 1 57548 Kirchen USt-IdNr. DE 129 274 202 "
                "Diakonie Klinikum Kirchen GmbH Hauptstr. 1 57548 Kirchen USt-IdNr. DE 811 128 135")
        dk = replace(BOSCH, names=("Diakonie Klinikum Kirchen GmbH", "Einkauf", None, None),
                     postal_code="57548", city="KIRCHEN", street="Hauptstr.", house_number="1")
        url = "https://www.diakonie-kirchen.example/impressum"
        model = Model(run=[run("q", [url])], read=[read(found("DE129274202", url, None, None))])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(dk)
        assert r.value == "DE811128135" and "BLOCK_SELECTED" in r.flags
        assert "OTHER_VAT_IDS_ON_PAGE" in " ".join(r.all_issues) or r.decision != "AUTO_ACCEPT"

    @pytest.mark.asyncio
    async def test_several_numbers_on_the_page_go_to_a_person(self, tmp_path):
        # Two companies, two numbers; the record's name only after both:
        # no block names it, so a person picks (never the first number).
        body = ("Hochschulen Fresenius gemeinnützige Trägergesellschaft mbH 65510 Idstein "
                "Umsatzsteuer-ID: DE196697659 Hochschulen Fresenius GmbH 65510 Idstein "
                "Umsatzsteuer-ID: DE299793075 " + "Kontakt Anfahrt Karriere " * 30 +
                "Impressum der Europa Fachhochschule Fresenius GmbH")
        eff = replace(BOSCH, names=("Europa Fachhochschule Fresenius", "GmbH", None, None),
                      postal_code="65510", city="IDSTEIN")
        url = "https://www.hs-fresenius.example/impressum/"
        answer = dict(NOTHING, candidates=[
            {"vat_id": "DE196697659", "source_url": url,
             "attributed_to": "Hochschulen Fresenius gemeinnützige Trägergesellschaft mbH"},
            {"vat_id": "DE299793075", "source_url": url, "attributed_to": "Hochschulen Fresenius GmbH"}])
        model = Model(run=[run("q", [url])], read=[read(answer)])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(eff)
        assert r.value in ("DE196697659", "DE299793075") and r.decision == CONFLICT
        assert "AMBIGUOUS_SAME_NAME:DE196697659,DE299793075" in r.flags

    @pytest.mark.asyncio
    async def test_search_2_with_name_used_and_zero_results_rule(self, tmp_path):
        model = Model(run=[run("q1", [])], second=[(json.dumps(
            {"query": "Robert Bosch Impressum USt-IdNr", "name_used": "Robert Bosch",
             "why_this_query": "the legal name", "results": []}), [IMPRESSUM],
            dict(USAGE, queries=["Robert Bosch Impressum USt-IdNr"]))], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(BOSCH)
        assert r.value == "DE811128135" and "FOUND_BY_SEARCH:2" in r.flags
        assert (r.usage["name_used"], r.usage["why_this_query"]) == ("Robert Bosch", "the legal name")
        assert r.usage["searches"] == 2 and SEARCH_LIMIT_REACHED in r.flags
        assert QUERY_NOT_RUN_AS_GIVEN in r.flags  # the script ran "q1", not the built query
        assert "leave out the quotes and the legal form" in model.calls[1][1]

    @pytest.mark.asyncio
    async def test_each_search_call_has_its_own_schema(self, tmp_path):
        sent = []

        async def search(system, user, effort, limits):
            sent.append((system, tuple(limits.schema["required"]), limits.max_searches + limits.max_opens))
            if system == LLM_WEB_RUN_SYSTEM_PROMPT:
                return json.dumps({"query_run": "q", "results": []}), [], USAGE
            if system == LLM_WEB_SECOND_SYSTEM_PROMPT:
                return json.dumps({"query": "x", "name_used": "Bosch", "why_this_query": "z",
                                   "domain_hint": None, "results": []}), [], USAGE
            return json.dumps(NOTHING), [], READ_USAGE

        llm = VatLLM(EvidenceCache(tmp_path).namespace("vat_llm"))
        llm._default_search = search  # the real call path: limits passed through
        engine = _engine(tmp_path, None, {})
        engine._llm = llm
        r = await engine.lookup(BOSCH)
        assert [s[1] for s in sent] == [("query_run", "results"),
                                        ("query", "name_used", "why_this_query", "domain_hint", "results")]
        assert all(s[2] == 1 for s in sent)
        assert r.usage["name_used"] == "Bosch"

    @pytest.mark.asyncio
    async def test_reader_sees_sections_and_pattern_hits(self, tmp_path):
        body = "Menü " * 5000 + BODY + " Footer " * 3000
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(BOSCH)
        prompt = model.calls[1][1]
        assert "VAT-ID-shaped strings on the whole page: DE811128135" in prompt
        assert QUOTE in prompt and len(prompt) < 20000 and r.value == "DE811128135"

    @pytest.mark.asyncio
    async def test_number_not_on_the_page_is_dropped(self, tmp_path):
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found(vat="DE129274202"))])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(BOSCH)
        assert r.status == NOT_FOUND and r.value is None
        assert r.usage["dropped"][0]["vat_id"] == "DE129274202"

    @pytest.mark.asyncio
    async def test_pdf_on_the_own_host_is_read(self, tmp_path, monkeypatch):
        pdf = "https://www.bosch.example/einkaufsbedingungen.pdf"
        monkeypatch.setattr(lookup_grounded, "fetch_pdf", lambda url, timeout=15.0: page(pdf, BODY))
        model = Model(run=[run("q", [pdf])], read=[read(found(url=pdf))])
        r = await _engine(tmp_path, model, {}).lookup(BOSCH)
        assert r.value == "DE811128135" and PDF_SOURCE in r.flags
        assert r.usage["pdf_fetches"] == 1 and r.usage["pdf_used"] is True


class TestNoVatExpected:
    @pytest.mark.asyncio
    async def test_a_third_party_page_is_not_the_entitys_statement(self, tmp_path):
        verein = replace(BOSCH, names=("Musterverein e.V.", None, None, None))
        url = "https://www.steuer-ratgeber.example/impressum"
        model = Model(run=[run("q", [url])], second=[NO_SEARCH_2], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {url: page(url, "Wir sind Kleinunternehmer.")}).lookup(verein)
        assert r.status == NOT_FOUND
        assert r.usage["hosts"]["steuer-ratgeber.example"]["rejected"] in ("NAME_NOT_ON_PAGE", "NOT_VERIFIED")

    @pytest.mark.asyncio
    async def test_page_says_no_vat_id(self, tmp_path):
        verein = replace(BOSCH, names=("Musterverein e.V.", None, None, None))
        url = "https://www.musterverein.example/impressum"
        text = "Impressum Musterverein e.V. Der Verein ist nicht umsatzsteuerpflichtig."
        model = Model(run=[run("q", [url])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {url: page(url, text)}).lookup(verein)
        assert (r.status, r.decision, r.source_url) == (NO_VAT_STATED, "NOT_FOUND", url)
        assert "nicht umsatzsteuerpflichtig" in r.evidence

    @pytest.mark.asyncio
    async def test_school_whose_impressum_has_no_number(self, tmp_path):
        school = replace(BOSCH, names=("Grundschule am Park", None, None, None), postal_code="70839")
        url = "https://www.grundschule-am-park.example/impressum"
        body = "Impressum Grundschule am Park Schulstr. 1 70839 Gerlingen Schulleitung: Frau Muster"
        model = Model(run=[run("q", [url])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(school)
        assert r.status == NO_VAT_INFERRED and PUBLIC_BODY in r.flags and r.evidence is None

    @pytest.mark.asyncio
    async def test_school_with_nothing_read_is_not_found(self, tmp_path):
        school = replace(BOSCH, names=("Grundschule am Park", None, None, None))
        r = await _engine(tmp_path, Model(run=[run("q", [])], read=[read(NOTHING)]), {}).lookup(school)
        assert r.status == NOT_FOUND  # INFERRED needs its own Impressum read


class TestGroundedFallback:
    @pytest.mark.asyncio
    async def test_runs_once_with_search_1_for_not_found(self, tmp_path):
        class Grounded:
            calls = []

            def stats(self):
                n = len(self.calls)
                return {"serp_queries": n, "serp_network_calls": n, "llm_reads": 2 * n,
                        "llm_network_calls": 2 * n, "page_attempts": 3 * n}

            async def lookup(self, inp, *, query=None, max_fetches=None):
                self.calls.append((query, max_fetches))
                return FoundResult("grounded", "FOUND_VERIFIED", value="DE811128135", flags=("X",))

        g = Grounded()
        model = Model(run=[run("q", [])], second=[NO_SEARCH_2], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {}, grounded=g).lookup(BOSCH)
        assert g.calls == [(first_query("Robert Bosch GmbH", "GERLINGEN"), 4)]
        assert r.value == "DE811128135" and "ENGINE:grounded" in r.flags
        assert r.usage["engine"] == "grounded"
        assert (r.usage["grounded_serp_queries"], r.usage["grounded_llm_reads"]) == (1, 2)

    @pytest.mark.asyncio
    async def test_not_run_when_found(self, tmp_path):
        class Grounded:
            async def lookup(self, inp, **kw):
                raise AssertionError("must not run")

        model = Model(run=[run("q", [IMPRESSUM])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}, grounded=Grounded()).lookup(BOSCH)
        assert r.value == "DE811128135"


class TestStepAD:
    """Step AD part 3: which page counts."""

    @pytest.mark.parametrize("names, text, reason", [
        # BAK Boysen "GmbH / & Co. KG" vs a page "GmbH & Co. KG" → pass.
        (("BAK Boysen Abgaskomponenten GmbH", "& Co. KG"),
         "Impressum BAK Boysen Abgaskomponenten GmbH & Co. KG 72227 Simmersfeld", None),
        # "Kappler GmbH & Co.KG" vs a page "Kappler GmbH" in another town → reject (form).
        (("Kappler GmbH & Co.KG",), "Impressum Kappler GmbH Hauptstr. 3 75217 Birkenfeld",
         None),  # Step AF: a candidate with LEGAL_FORM_DIFFERS, not a rejection
        # Grillo-Werke AG, its Impressum in another town → accepted.
        (("Grillo-Werke AG",), "Impressum Grillo-Werke AG Weseler Str. 1 47169 Duisburg", None),
        # A cut "GmbH & Co" never differs from a GmbH family form.
        (("NFG Nehlsen Flugzeug-Galvanik Gmbh&",), "Impressum NFG Nehlsen Flugzeug-Galvanik GmbH & Co. KG", None),
    ])
    def test_host_check_cases(self, names, text, reason):
        record = replace(BOSCH, names=(*names, *([None] * (4 - len(names)))))
        assert host_check(text, IMPRESSUM, record, OWN)[0] == reason

    def test_sub_entity_fails_on_the_parents_impressum(self):
        mri = replace(BOSCH, names=("Technische Universität München", "Klinikum rechts der Isar", None, None))
        assert host_check("Impressum Technische Universität München Arcisstr. 21 80333 München",
                          "https://www.tum.example/impressum", mri, OWN)[0] == "NAME_NOT_ON_PAGE"
        assert host_check("Impressum Klinikum rechts der Isar der TU München AöR Ismaninger Str. 22",
                          "https://www.mri.tum.example/impressum", mri, OWN)[0] is None

    @pytest.mark.asyncio
    async def test_bmw_branch_by_initials_needs_the_address_exact(self, tmp_path):
        bmw = replace(BOSCH, names=("BMW Group Werk Landshut", None, None, None), street="Ohmstraße",
                      house_number="2", postal_code="84030", city="LANDSHUT")
        url = "https://www.bmw.example/impressum"
        quote = "Umsatzsteuer-Identifikationsnummer: DE 811 128 135"
        same_town = page(url, "Impressum Bayerische Motoren Werke Aktiengesellschaft 84030 Landshut " + quote)
        model = Model(run=[run("q", [url])], read=[read(found(url=url, quote=quote,
                                                             name="Bayerische Motoren Werke Aktiengesellschaft"))])
        r = await _engine(tmp_path, model, {url: same_town}, peppol=None).lookup(bmw)
        assert r.value == "DE811128135" and "NAME_ABBREVIATED" in r.flags and "BRANCH" in r.all_issues
        assert r.decision == "MANUAL_REVIEW" and "ADDRESS_NOT_EXACT" in r.all_issues
        exact = page(url, "Impressum Bayerische Motoren Werke Aktiengesellschaft Ohmstraße 2 84030 Landshut " + quote)
        model = Model(run=[run("q", [url])], read=[read(found(url=url, quote=quote,
                                                             name="Bayerische Motoren Werke Aktiengesellschaft"))])
        r = await _engine(tmp_path / "x", model, {url: exact}).lookup(bmw)
        assert "ADDRESS_NOT_EXACT" not in r.all_issues and r.name_match == "EXACT"

    @pytest.mark.asyncio
    async def test_reader_fallback_by_pattern(self, tmp_path):
        model = Model(run=[run("q", [IMPRESSUM])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(BOSCH)
        assert r.value == "DE811128135" and "EXTRACTED_BY_PATTERN" in r.all_issues

    @pytest.mark.asyncio
    async def test_umbrella_parent_takes_its_number(self, tmp_path):
        fhg = replace(BOSCH, names=("Fraunhofer-Institut für Silicatforschung ISC", None, None, None),
                      street="Neunerplatz", house_number="2", postal_code="97082", city="WUERZBURG")
        url = "https://www.isc.fraunhofer.example/impressum"
        body = ("Impressum Fraunhofer-Institut für Silicatforschung ISC Neunerplatz 2 97082 Würzburg. "
                "Das Institut ist eine rechtlich nicht selbständige Einrichtung der Fraunhofer-Gesellschaft "
                "zur Förderung der angewandten Forschung e.V. " + QUOTE)
        model = Model(run=[run("q", [url])], read=[read(found(
            url=url, name="Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V."))])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(fhg)
        assert r.value == "DE811128135" and "PARENT_ENTITY" in r.all_issues and r.name_match == "EXACT"

    @pytest.mark.asyncio
    async def test_other_parent_is_a_candidate_for_review(self, tmp_path):
        eni = replace(BOSCH, names=("European Neuroscience Institute", "Göttingen", None, None),
                      postal_code="37077", city="GOETTINGEN")
        url = "https://www.eni-goettingen.example/impressum"
        body = ("Impressum European Neuroscience Institute Göttingen 37077 Göttingen, eine Einrichtung der "
                "Universitätsmedizin Göttingen " + QUOTE)
        model = Model(run=[run("q", [url])], read=[read(found(url=url, name="Universitätsmedizin Göttingen"))])
        r = await _engine(tmp_path, model, {url: page(url, body)}).lookup(eni)
        assert "PARENT_ENTITY_CANDIDATE" in r.all_issues and r.decision != "AUTO_ACCEPT"

    @pytest.mark.asyncio
    async def test_generic_name_needs_the_address_exact(self, tmp_path):
        lang = replace(BOSCH, names=("Lang GmbH", None, None, None))
        url = "https://www.lang-gerlingen.example/impressum"
        body = "Impressum Lang GmbH 70839 Gerlingen " + QUOTE
        model = Model(run=[run("q", [url])], read=[read(found(url=url, name="Lang GmbH"))])
        web = FakeWeb({url: page(url, body)})
        r = await _engine(tmp_path, model, {}, web=web).lookup(lang)
        # The token rule is off for a GENERIC name: lang-gerlingen.example is
        # not own by its domain, only as a verified Impressum.
        assert r.value == "DE811128135" and "GENERIC_NAME" in r.flags
        assert r.decision == "MANUAL_REVIEW" and "ADDRESS_NOT_EXACT" in r.all_issues
        assert '"Lang GmbH"' not in r.usage["query_1"]  # never quoted

    @pytest.mark.asyncio
    async def test_search_2_after_a_dead_own_host(self, tmp_path):
        # J&M: j-m.de does not answer, so it does not count as found.
        jm = replace(BOSCH, names=("J&M Analytik AG", None, None, None))
        model = Model(run=[run("q", ["https://www.jm-analytik.example/"])], read=[read(NOTHING)])
        r = await _engine(tmp_path, model, {"https://www.jm-analytik.example/": "timeout"}).lookup(jm)
        assert [k for k, _ in model.calls][:2] == ["run", "second"]

    def test_public_body_query(self):
        assert first_query("Bundeskriminalamt Berlin", "BERLIN").endswith(" USt-IdNr")
        assert first_query("GDF GmbH", "ROSBACH V.D.H.", "Gesellschaft für dentale") == "GDF GmbH Rosbach"
