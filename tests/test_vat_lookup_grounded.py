"""Mode A — grounded missing-VAT lookup (vat/lookup_grounded.py).

SerpAPI, the web and the LLM are doubles (tests/mocks/vat_lookup_mock.py);
VIES is the scripted stub. Each test gets its own evidence cache under
tmp_path. Company data: the fixture's public Impressum IDs, or obviously fake.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from config import get_settings
from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeReader, FakeSearch, FakeWeb, page, reading
from tests.mocks.vies_mock import ViesStub
from utils.cache import EvidenceCache
from vat.llm_cache import VatLLM
from vat.lookup import (
    FOUND_UNVERIFIED,
    FOUND_VERIFIED,
    LOOKUP_COLUMNS,
    LOOKUP_INCOMPLETE,
    NOT_FOUND,
    NOT_RUN,
    LookupInput,
)
from vat.lookup_grounded import (
    ATTRIBUTION_MATCH,
    ATTRIBUTION_MISMATCH,
    ATTRIBUTION_PARTIAL,
    ATTRIBUTION_QUALIFIER,
    DIRECTORY_DOMAIN,
    FORMAT_ERROR,
    ID_NOT_IN_PAGE,
    MAX_FETCHES,
    NO_IMPRESSUM_PAGE,
    NO_SERPAPI_KEY,
    NO_VAT_ID_ON_PAGE,
    QUOTE_NOT_IN_PAGE,
    READER_TEXT_LIMIT,
    GroundedLookup,
    is_directory_host,
    reader_text,
)
from vat.validate import VAT_COLUMNS, validate_rows
from vat.vies_client import VIESClient

BOSCH = LookupInput(
    customer="99000003", names=("Robert Bosch GmbH", None, None, None),
    street="Robert-Bosch-Platz", house_number="1", postal_code="70839",
    city="Gerlingen-Schillerhoehe", country="DE",
)
SITE = "https://www.bosch.example"
IMPRESSUM = f"{SITE}/impressum"
BODY = (
    "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen-Schillerhöhe "
    "Registergericht: Amtsgericht Stuttgart, HRB 14000 "
    "Umsatzsteuer-Identifikationsnummer: DE 811 128 135 "
    "Steuernummer: 99/123/45678"
)
QUOTE = "Umsatzsteuer-Identifikationsnummer: DE 811 128 135"


def _settings(**kw):
    return replace(get_settings(), vies_backoff_seconds=0.0, vies_min_interval_ms=0,
                   vies_requester_vat="", cache_frozen=False, **kw)


def _engine(tmp_path, *, search=None, web=None, reader=None, vies_reply=None, frozen=False):
    cache = EvidenceCache(tmp_path, frozen=frozen)
    vies_stub = ViesStub(vies_reply or vies_mock.echo_valid_undisclosed)
    vies = VIESClient(_settings(), cache=cache, transport=vies_stub.transport)
    reader = reader if reader is not None else FakeReader()
    engine = GroundedLookup(
        _settings(), vies,
        search_client=search if search is not None else FakeSearch({"Bosch": [IMPRESSUM]}),
        fetcher=web if web is not None else FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY)}),
        llm=VatLLM(cache.namespace("vat_llm"), complete=reader),
    )
    return engine, vies_stub, reader


def _reader(answer):
    return FakeReader({IMPRESSUM: answer})


class TestFound:
    @pytest.mark.asyncio
    async def test_verified_with_spaced_digits(self, tmp_path):
        engine, vies, _ = _engine(tmp_path, reader=_reader(
            reading("DE 811 128 135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert r.status == FOUND_VERIFIED
        assert (r.value, r.source_url, r.attributed_to, r.evidence) == (
            "DE811128135", IMPRESSUM, "Robert Bosch GmbH", QUOTE)
        assert r.date and ATTRIBUTION_MATCH in r.flags
        assert vies.requests == [{"countryCode": "DE", "vatNumber": "811128135"}]

    @pytest.mark.asyncio
    async def test_two_ids_on_one_page(self, tmp_path):
        body = BODY + " Bosch Sicherheitssysteme GmbH USt-IdNr. DE 147 799 625"
        web = FakeWeb({IMPRESSUM: page(IMPRESSUM, body)})
        engine, _, _ = _engine(tmp_path, web=web, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE, others=["DE 147 799 625"])))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value) == (FOUND_VERIFIED, "DE811128135")
        assert "OTHER_VAT_IDS_ON_PAGE:DE 147 799 625" in r.flags

    @pytest.mark.asyncio
    async def test_fallback_path_on_the_same_host(self, tmp_path):
        web = FakeWeb({
            SITE + "/": page(SITE + "/", "Willkommen bei Bosch", title="Bosch Home"),
            IMPRESSUM: page(IMPRESSUM, BODY),
        })
        search = FakeSearch({"Bosch": [SITE + "/"]})
        engine, _, _ = _engine(tmp_path, search=search, web=web, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.source_url) == (FOUND_VERIFIED, IMPRESSUM)
        assert web.fetched == [SITE + "/", IMPRESSUM]


class TestGates:
    @pytest.mark.asyncio
    async def test_a_quote_not_in_page(self, tmp_path):
        engine, vies, _ = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", "USt-IdNr. DE811128135")))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags[0]) == (FOUND_UNVERIFIED, QUOTE_NOT_IN_PAGE)
        assert vies.requests == []

    @pytest.mark.asyncio
    async def test_b_llm_completes_digits_that_are_not_on_the_page(self, tmp_path):
        truncated = "Umsatzsteuer-Identifikationsnummer: DE 811 128 13"
        web = FakeWeb({IMPRESSUM: page(IMPRESSUM, truncated + " Robert Bosch GmbH")})
        engine, vies, _ = _engine(tmp_path, web=web, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", truncated)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags[0]) == (FOUND_UNVERIFIED, ID_NOT_IN_PAGE)
        assert vies.requests == []

    @pytest.mark.asyncio
    async def test_c_format_error(self, tmp_path):
        bad = "Umsatzsteuer-Identifikationsnummer: DE 811 128 136"
        web = FakeWeb({IMPRESSUM: page(IMPRESSUM, "Robert Bosch GmbH " + bad)})
        engine, vies, _ = _engine(tmp_path, web=web, reader=_reader(
            reading("DE811128136", "Robert Bosch GmbH", bad)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags[:2]) == (FOUND_UNVERIFIED, (FORMAT_ERROR, "FORMAT_ERROR:InvalidChecksum"))
        assert vies.requests == []

    @pytest.mark.asyncio
    async def test_d_parent_company_impressum_bosch_rexroth(self, tmp_path):
        # SAP "Bosch Rexroth AG" vs page "Robert Bosch GmbH": they share {bosch}
        # and SAP's extra "rexroth" is no site qualifier -> a different company.
        rexroth = replace(BOSCH, customer="X1", names=("Bosch Rexroth AG", None, None, None))
        engine, vies, _ = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(rexroth)
        assert (r.status, r.flags[0]) == (FOUND_UNVERIFIED, ATTRIBUTION_PARTIAL)
        assert vies.requests == []

    @pytest.mark.asyncio
    async def test_d_subsidiary_with_extra_words_bosch_hausgeraete(self, tmp_path):
        # The live 2026-10-19 smoke case: a real, VIES-valid number of another
        # Bosch company. Must not be verified.
        engine, vies, _ = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch Hausger\u00e4te GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags[0]) == (FOUND_UNVERIFIED, ATTRIBUTION_PARTIAL)
        assert vies.requests == []

    @pytest.mark.asyncio
    async def test_d_site_qualifier_is_verified_with_flag(self, tmp_path):
        werk = replace(BOSCH, customer="X2", names=("Siemens AG Werk Erlangen", None, None, None))
        siemens_quote = "USt-IdNr.: DE 129 274 202"
        url = "https://www.siemens.example/impressum"
        engine, _, _ = _engine(
            tmp_path, search=FakeSearch({"Siemens": [url]}),
            web=FakeWeb({url: page(url, "Siemens AG Werner-von-Siemens-Str. 1 " + siemens_quote)}),
            reader=FakeReader({url: reading("DE129274202", "Siemens AG", siemens_quote)}))
        r = await engine.lookup(werk)
        assert (r.status, r.value) == (FOUND_VERIFIED, "DE129274202")
        assert ATTRIBUTION_QUALIFIER in r.flags

    @pytest.mark.asyncio
    async def test_d_qualifier_in_name_2_still_matches_on_name_alone(self, tmp_path):
        werk = replace(BOSCH, customer="X3", names=("Robert Bosch GmbH", "Werk Bamberg", None, None))
        engine, _, _ = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(werk)
        assert r.status == FOUND_VERIFIED and ATTRIBUTION_MATCH in r.flags

    @pytest.mark.asyncio
    async def test_d_no_attribution_fails_too(self, tmp_path):
        engine, _, _ = _engine(tmp_path, reader=_reader(reading("DE811128135", None, QUOTE)))
        r = await engine.lookup(BOSCH)
        assert r.flags[0] == ATTRIBUTION_MISMATCH

    @pytest.mark.asyncio
    async def test_e_directory_domain(self, tmp_path):
        url = "https://www.northdata.de/Robert+Bosch+GmbH,Gerlingen/impressum"
        web = FakeWeb({url: page(url, BODY)})
        engine, vies, _ = _engine(
            tmp_path, search=FakeSearch({"Bosch": [url]}), web=web,
            reader=FakeReader({url: reading("DE811128135", "Robert Bosch GmbH", QUOTE)}))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags[0]) == (FOUND_UNVERIFIED, DIRECTORY_DOMAIN)
        assert vies.requests == []

    @pytest.mark.parametrize("url", [
        "https://www.northdata.com/x", "https://firmeneintrag.creditreform.de/x",
        "https://www.dasoertliche.de/x", "https://www.11880.com/x", "https://www.cylex.de/x",
        "https://www.firmenabc.de/x", "https://www.meinestadt.de/x",
        "https://www.werliefertwas.de/x", "https://www.firmeneintrag.de/x",
        "https://www.branchenbuch-deutschland.de/x",
    ])
    def test_directory_list_matches(self, url):
        assert is_directory_host(url)

    @pytest.mark.parametrize("url", ["https://www.bosch.com/impressum", "https://www.basf.com/x"])
    def test_company_sites_are_not_directories(self, url):
        assert not is_directory_host(url)


class TestKeepLooking:
    @pytest.mark.asyncio
    async def test_first_page_partial_second_page_match_takes_the_second(self, tmp_path):
        bsh = "https://www.bosch-home.example/impressum"
        bsh_quote = "Umsatzsteuer-Identifikationsnummer DE129274235"
        web = FakeWeb({
            bsh: page(bsh, "Robert Bosch Hausger\u00e4te GmbH M\u00fcnchen " + bsh_quote),
            IMPRESSUM: page(IMPRESSUM, BODY),
        })
        reader = FakeReader({
            bsh: reading("DE129274235", "Robert Bosch Hausger\u00e4te GmbH", bsh_quote),
            IMPRESSUM: reading("DE811128135", "Robert Bosch GmbH", QUOTE),
        })
        engine, _, _ = _engine(tmp_path, search=FakeSearch({"Bosch": [bsh, IMPRESSUM]}),
                               web=web, reader=reader)
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value, r.source_url) == (FOUND_VERIFIED, "DE811128135", IMPRESSUM)
        cands = [f for f in r.flags if f.startswith("CANDIDATE:")]
        assert cands == [
            "CANDIDATE:DE129274235|Robert Bosch Hausger\u00e4te GmbH|www.bosch-home.example"
            "|FOUND_UNVERIFIED:ATTRIBUTION_PARTIAL",
            "CANDIDATE:DE811128135|Robert Bosch GmbH|www.bosch.example|FOUND_VERIFIED",
        ]

    @pytest.mark.asyncio
    async def test_stops_at_a_verified_match(self, tmp_path):
        other = "https://www.other.example/impressum"
        reader = FakeReader({IMPRESSUM: reading("DE811128135", "Robert Bosch GmbH", QUOTE)})
        engine, _, _ = _engine(
            tmp_path, search=FakeSearch({"Bosch": [IMPRESSUM, other]}),
            web=FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY), other: page(other, BODY)}),
            reader=reader)
        r = await engine.lookup(BOSCH)
        assert r.status == FOUND_VERIFIED
        assert len(reader.prompts) == 1  # the second page is never read

    @pytest.mark.asyncio
    async def test_directory_hosts_get_no_fallback_probes(self, tmp_path):
        url = "https://www.dasoertliche.de/Themen/Bosch"
        web = FakeWeb({url: page(url, "Bosch Filialen", title="Das Oertliche")})
        engine, _, _ = _engine(tmp_path, search=FakeSearch({"Bosch": [url]}), web=web)
        r = await engine.lookup(BOSCH)
        assert web.fetched == [url]
        assert (r.status, r.flags) == (NOT_FOUND, (NO_IMPRESSUM_PAGE,))


class TestCacheWriteGuard:
    @pytest.mark.asyncio
    async def test_unwritten_entries_are_counted(self, tmp_path, monkeypatch):
        from utils import cache as cache_mod
        monkeypatch.setattr(cache_mod.DiskCache, "set", lambda self, key, payload: None)
        engine, _, _ = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert r.status == FOUND_VERIFIED
        failures = engine._vies.write_failures
        assert failures.count == 5  # 2 SERP + 1 page + 1 LLM + 1 VIES
        assert set(failures.by_namespace) == {"vat_serp", "vat_pages", "vat_llm", "vies"}
        assert engine.stats()["cache_write_failures"] == 5

    @pytest.mark.asyncio
    async def test_summary_reports_them(self, tmp_path, monkeypatch):
        from utils import cache as cache_mod
        monkeypatch.setattr(cache_mod.DiskCache, "set", lambda self, key, payload: None)
        engine, _, _ = _engine(tmp_path)
        rows = [{"Customer": "1", "Country/Region Key": "DE", "VAT Registration No.": "DE136695976"}]
        _, summary = await validate_rows(rows, engine._vies)
        assert summary["cache_write_failures"] == 1

    @pytest.mark.asyncio
    async def test_normal_writes_count_nothing(self, tmp_path):
        engine, _, _ = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        await engine.lookup(BOSCH)
        assert engine._vies.write_failures.count == 0


class TestViesOutcome:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply, status", [
        (vies_mock.invalid("DE", "811128135"), f"{FOUND_UNVERIFIED}:VIES_INVALID"),
        (vies_mock.error("MS_UNAVAILABLE"), f"{FOUND_UNVERIFIED}:VIES_SERVICE_UNAVAILABLE"),
    ])
    async def test_only_vies_valid_is_verified(self, tmp_path, reply, status):
        engine, _, _ = _engine(tmp_path, vies_reply=reply, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert r.status == status


class TestNothingFound:
    @pytest.mark.asyncio
    async def test_no_impressum_found(self, tmp_path):
        home = SITE + "/"
        web = FakeWeb({home: page(home, "Willkommen", title="Home")})
        engine, _, reader = _engine(tmp_path, search=FakeSearch({"Bosch": [home]}), web=web)
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags) == (NOT_FOUND, (NO_IMPRESSUM_PAGE,))
        assert web.fetched == [home] + [SITE + p for p in
                                        ("/impressum", "/imprint", "/legal-notice", "/de/impressum")]
        assert reader.prompts == []

    @pytest.mark.asyncio
    async def test_at_most_six_fetches(self, tmp_path):
        hosts = [f"https://site{i}.example/" for i in range(3)]
        engine, _, _ = _engine(tmp_path, search=FakeSearch({"Bosch": hosts}),
                               web=FakeWeb({h: page(h, "x", title="Home") for h in hosts}))
        r = await engine.lookup(BOSCH)
        assert r.status == NOT_FOUND
        assert engine.stats()["page_attempts"] == MAX_FETCHES

    @pytest.mark.asyncio
    async def test_impressum_without_vat_id(self, tmp_path):
        engine, _, _ = _engine(tmp_path, reader=_reader(reading(None)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags) == (NOT_FOUND, (NO_VAT_ID_ON_PAGE,))

    @pytest.mark.asyncio
    async def test_llm_failure_is_incomplete_not_not_found(self, tmp_path):
        engine, _, _ = _engine(tmp_path, reader=_reader(RuntimeError("OpenAI call failed")))
        r = await engine.lookup(BOSCH)
        assert r.status == LOOKUP_INCOMPLETE

    @pytest.mark.asyncio
    async def test_no_serpapi_key_is_not_run(self, tmp_path):
        cache = EvidenceCache(tmp_path)
        vies = VIESClient(_settings(), cache=cache, transport=ViesStub().transport)
        engine = GroundedLookup(_settings(serpapi_key=""), vies)
        r = await engine.lookup(BOSCH)
        assert (r.status, r.flags) == (NOT_RUN, (NO_SERPAPI_KEY,))


class TestCache:
    @pytest.mark.asyncio
    async def test_second_run_and_frozen_run_replay_everything(self, tmp_path):
        answer = reading("DE811128135", "Robert Bosch GmbH", QUOTE)
        first, _, _ = _engine(tmp_path, reader=_reader(answer))
        r1 = await first.lookup(BOSCH)

        search, web, reader = FakeSearch(), FakeWeb(), FakeReader()
        frozen, vies, _ = _engine(tmp_path, search=search, web=web, reader=reader, frozen=True)
        r2 = await frozen.lookup(BOSCH)
        assert (search.queries, web.fetched, reader.prompts, vies.requests) == ([], [], [], [])
        assert (r2.status, r2.value, r2.evidence) == (r1.status, r1.value, r1.evidence)
        s = frozen.stats()
        assert (s["serp_network_calls"], s["pages_fetched"], s["llm_network_calls"]) == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_frozen_with_nothing_recorded_is_incomplete(self, tmp_path):
        search = FakeSearch({"Bosch": [IMPRESSUM]})
        engine, _, _ = _engine(tmp_path, search=search, frozen=True)
        r = await engine.lookup(BOSCH)
        assert r.status == LOOKUP_INCOMPLETE
        assert search.queries == []

    @pytest.mark.asyncio
    async def test_only_definitive_fetches_are_recorded(self, tmp_path):
        urls = {f"{SITE}/a": 404, f"{SITE}/b": 503, f"{SITE}/c": "timeout"}
        engine, _, _ = _engine(tmp_path, web=FakeWeb(urls))
        flags: set[str] = set()
        for url in urls:
            await engine._fetch(url, flags)
        recorded = sorted(p.name for p in (tmp_path / "vat_pages").glob("*.json"))
        assert len(recorded) == 1 and "_a" in recorded[0]


class TestReaderText:
    def test_long_page_keeps_the_vat_passage(self):
        text = "Impressum ACME GmbH " + "lorem ipsum " * 3000 + QUOTE + " " + "dolor " * 3000
        cut = reader_text(text)
        assert len(cut) <= READER_TEXT_LIMIT + 20
        assert QUOTE in cut and cut.startswith("Impressum ACME GmbH")

    def test_short_page_is_unchanged(self):
        assert reader_text(BODY) == BODY


class TestInValidateRows:
    @pytest.mark.asyncio
    async def test_only_missing_de_rows_are_looked_up_once_per_customer(self, tmp_path):
        engine, _, reader = _engine(tmp_path, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        base = {"Name": "Robert Bosch GmbH", "City": "Gerlingen-Schillerhoehe",
                "Postal Code": "70839", "Street": "Robert-Bosch-Platz"}
        rows = [
            {"Customer": "99000003", "Country/Region Key": "DE", "VAT Registration No.": None, **base},
            {"Customer": "99000003", "Country/Region Key": "DE", "VAT Registration No.": "", **base},
            {"Customer": "2", "Country/Region Key": "DE", "VAT Registration No.": "DE136695976", **base},
            {"Customer": "3", "Country/Region Key": "FR", "VAT Registration No.": None, **base},
        ]
        out, summary = await validate_rows(rows, engine._vies, lookup=engine)
        assert list(out[0])[-len(LOOKUP_COLUMNS):] == LOOKUP_COLUMNS
        assert [r["VAT_Found_Status"] for r in out] == [FOUND_VERIFIED, FOUND_VERIFIED, None, None]
        assert out[0]["VAT_Lookup_Mode"] == "GROUNDED"
        assert len(reader.prompts) == 1                      # one lookup for the customer
        assert all(r["VAT Registration No."] == rows[i]["VAT Registration No."]
                   for i, r in enumerate(out))               # never written
        assert summary["lookup"]["rows_in_scope"] == 2

    @pytest.mark.asyncio
    async def test_without_a_lookup_the_output_is_unchanged(self, tmp_path):
        engine, _, _ = _engine(tmp_path)
        rows = [{"Customer": "1", "Country/Region Key": "DE", "VAT Registration No.": None}]
        out, summary = await validate_rows(rows, engine._vies)
        assert list(out[0]) == list(rows[0]) + VAT_COLUMNS
        assert "lookup" not in summary



# ---------------------------------------------------------------------------
# Fix 1 — a blocked / timed-out page is LOOKUP_INCOMPLETE, never NOT_FOUND
# ---------------------------------------------------------------------------

class TestUnavailablePages:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer, reason", [(403, "403"), (503, "503"), ("timeout", "ReadTimeout")])
    async def test_blocked_page_is_incomplete_with_the_reason(self, tmp_path, answer, reason):
        engine, _, reader = _engine(tmp_path, web=FakeWeb({IMPRESSUM: answer}))
        r = await engine.lookup(BOSCH)
        assert r.status == LOOKUP_INCOMPLETE
        assert f"PAGE_UNAVAILABLE:{reason}" in r.flags
        assert reader.prompts == []

    @pytest.mark.asyncio
    async def test_frozen_replay_reproduces_it_exactly(self, tmp_path):
        live, _, _ = _engine(tmp_path, web=FakeWeb({IMPRESSUM: 403}))
        r1 = await live.lookup(BOSCH)
        web = FakeWeb()
        frozen, _, _ = _engine(tmp_path, search=FakeSearch(), web=web, frozen=True)
        r2 = await frozen.lookup(BOSCH)
        assert web.fetched == []
        assert (r2.status, r2.flags) == (r1.status, r1.flags)
        assert "FROZEN_CACHE_MISS" not in r2.flags

    @pytest.mark.asyncio
    async def test_a_live_rerun_asks_the_site_again(self, tmp_path):
        first, _, _ = _engine(tmp_path, web=FakeWeb({IMPRESSUM: 403}))
        await first.lookup(BOSCH)
        web = FakeWeb({IMPRESSUM: page(IMPRESSUM, BODY)})
        again, _, _ = _engine(tmp_path, web=web, reader=_reader(
            reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await again.lookup(BOSCH)
        assert web.fetched == [IMPRESSUM]          # not served the 403
        assert r.status == FOUND_VERIFIED

    @pytest.mark.asyncio
    async def test_unavailable_answers_never_enter_vat_pages(self, tmp_path):
        engine, _, _ = _engine(tmp_path, web=FakeWeb({IMPRESSUM: 403}))
        await engine.lookup(BOSCH)
        assert engine._pages.get(IMPRESSUM) is None          # the 403 is not in vat_pages
        assert engine._unavailable.get(IMPRESSUM) == {"url": IMPRESSUM, "reason": "403"}
        assert len(list((tmp_path / "vat_pages_unavailable").glob("*.json"))) == 1

    @pytest.mark.asyncio
    async def test_a_found_result_still_lists_blocked_pages(self, tmp_path):
        blocked = "https://www.blocked.example/impressum"
        engine, _, _ = _engine(
            tmp_path, search=FakeSearch({"Bosch": [blocked, IMPRESSUM]}),
            web=FakeWeb({blocked: 403, IMPRESSUM: page(IMPRESSUM, BODY)}),
            reader=_reader(reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert r.status == FOUND_VERIFIED and "PAGE_UNAVAILABLE:403" in r.flags


# ---------------------------------------------------------------------------
# Fix 2 — result URLs alternate between the two queries
# ---------------------------------------------------------------------------

class _TwoQueries(FakeSearch):
    def __init__(self, impressum, imprint):
        super().__init__()
        self.lists = {"Impressum": impressum, "Imprint": imprint}

    async def search(self, query, num_results=5, *, country=None):
        self.queries.append(query)
        from search.base import SearchResult
        urls = self.lists["Impressum" if query.endswith("Impressum") else "Imprint"]
        return [SearchResult(title=u, url=u, snippet="") for u in urls]


class TestAlternatingResults:
    @pytest.mark.asyncio
    async def test_a_noisy_query_cannot_crowd_out_the_other(self, tmp_path):
        noise = ["https://de.wikipedia.org/wiki/Berlin", "https://www.berlin.de/",
                 "https://www.visitberlin.de/de"]
        good = [IMPRESSUM, "https://corporate.bosch.example/impressum"]
        engine, _, _ = _engine(tmp_path, search=_TwoQueries(noise, good))
        assert await engine._search_urls(BOSCH) == [noise[0], IMPRESSUM, noise[1]]

    @pytest.mark.asyncio
    async def test_duplicates_across_queries_count_once(self, tmp_path):
        engine, _, _ = _engine(tmp_path, search=_TwoQueries([IMPRESSUM, "https://a.example/"],
                                                            [IMPRESSUM, "https://b.example/"]))
        assert await engine._search_urls(BOSCH) == [IMPRESSUM, "https://a.example/", "https://b.example/"]

    @pytest.mark.asyncio
    async def test_the_good_page_is_reached_within_the_fetch_cap(self, tmp_path):
        noise = ["https://de.wikipedia.org/wiki/Berlin", "https://www.berlin.de/"]
        web = FakeWeb({noise[0]: page(noise[0], "Berlin", title="Berlin – Wikipedia"),
                       IMPRESSUM: page(IMPRESSUM, BODY)})
        engine, _, _ = _engine(tmp_path, search=_TwoQueries(noise, [IMPRESSUM]), web=web,
                               reader=_reader(reading("DE811128135", "Robert Bosch GmbH", QUOTE)))
        r = await engine.lookup(BOSCH)
        assert (r.status, r.value) == (FOUND_VERIFIED, "DE811128135")
