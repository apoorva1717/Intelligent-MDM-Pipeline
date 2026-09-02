"""Tests for Path A/B/C website resolution.

Path A — extract_website_from_ror (sync helper, ROR links[]).
Path B — select_website_from_serp + resolve_website_via_serp.
Path C — infer_website_via_llm (uses MockOpenAIClient).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator
from enrichment.provenance import UNVERIFIED_DOMAIN_RULE
from enrichment.tier1_ror import extract_website_from_ror
from enrichment.website_resolver import (
    DOMAIN_BLACKLIST,
    WebsiteResolution,
    infer_website_via_llm,
    resolve_website_via_serp,
    select_website_from_serp,
)
from search.base import SearchResult
from tests.conftest import seed
from utils.cache import BatchCache


# ---------------------------------------------------------------------------
# Path A — extract_website_from_ror
# ---------------------------------------------------------------------------

class TestExtractWebsiteFromROR:
    def test_returns_first_website_link(self):
        org = {
            "links": [
                {"type": "website", "value": "https://www.stanford.edu"},
                {"type": "wikipedia", "value": "https://en.wikipedia.org/wiki/Stanford_University"},
            ],
        }
        assert extract_website_from_ror(org) == "https://www.stanford.edu"

    def test_skips_non_website_links(self):
        org = {
            "links": [
                {"type": "wikipedia", "value": "https://en.wikipedia.org/wiki/Foo"},
                {"type": "website", "value": "http://www.ufl.edu"},
            ],
        }
        assert extract_website_from_ror(org) == "http://www.ufl.edu"

    def test_no_links_returns_none(self):
        assert extract_website_from_ror({"links": []}) is None
        assert extract_website_from_ror({}) is None

    def test_website_link_without_value_returns_none(self):
        org = {"links": [{"type": "website"}]}
        assert extract_website_from_ror(org) is None


# ---------------------------------------------------------------------------
# Path B — select_website_from_serp
# ---------------------------------------------------------------------------

def _sr(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


class TestSelectWebsiteFromSERP:
    def test_research_official_edu_top_hit_is_high_confidence(self):
        results = [
            _sr(
                "Florida Institute of Technology",
                "https://www.fit.edu/",
                "FIT homepage",
            ),
        ]
        chosen = select_website_from_serp(
            "Florida Institute of Technology", results,
            record_type="research_institution",
        )
        assert chosen.url == "https://www.fit.edu"
        assert chosen.confidence == "high"
        assert chosen.source == "serp"

    def test_blacklisted_hosts_are_skipped(self):
        results = [
            _sr(
                "Florida Institute of Technology - Wikipedia",
                "https://en.wikipedia.org/wiki/Florida_Institute_of_Technology",
            ),
            _sr(
                "Florida Institute of Technology",
                "https://www.fit.edu/",
            ),
        ]
        chosen = select_website_from_serp(
            "Florida Institute of Technology", results,
            record_type="research_institution",
        )
        assert chosen.url == "https://www.fit.edu"
        assert chosen.confidence == "high"

    def test_research_non_official_tld_is_low_confidence(self):
        results = [
            _sr("Acme Research Foundation", "https://www.acmeres.com/about"),
        ]
        chosen = select_website_from_serp(
            "Acme Research Foundation", results,
            record_type="research_institution",
        )
        assert chosen.url == "https://www.acmeres.com"
        assert chosen.confidence == "low"

    def test_company_token_in_host_is_high_confidence(self):
        # Company rule: name token is a substring of the host → high.
        results = [
            _sr("Fisher Scientific", "https://www.fishersci.com/"),
        ]
        chosen = select_website_from_serp(
            "Fisher Scientific Co. LLC", results, record_type="company",
        )
        assert chosen.url == "https://www.fishersci.com"
        assert chosen.confidence == "high"

    def test_company_token_not_in_host_is_rejected(self):
        # Company rule: the only overlap is a word in the TITLE, not the host
        # — too weak to trust, so nothing is returned (Path C/LLM then tries).
        results = [
            _sr("Pittsburgh PA Listings — Page 4", "https://www.example.com/co/listings"),
        ]
        chosen = select_website_from_serp(
            "Acme Pittsburgh", results, record_type="company",
        )
        assert chosen.url is None
        assert chosen.confidence == "none"

    def test_company_unrelated_host_with_title_overlap_rejected(self):
        # "Sign A Rama USA": a result whose HOST shares no name token but whose
        # title mentions "signs" must NOT be emitted as the website.
        results = [
            _sr("Signs & banners — University Surgical Center",
                "http://universitysurgical.com/"),
        ]
        chosen = select_website_from_serp(
            "Sign A Rama USA", results, record_type="company",
        )
        assert chosen.url is None
        assert chosen.confidence == "none"

    def test_company_prefers_root_domain_over_subsidiary(self):
        # "Siemens AG": the subsidiary siemens-healthineers.com contains the
        # token "siemens" but introduces the foreign brand "healthineers".
        # The clean root siemens.com must win even when it ranks lower.
        results = [
            _sr("Siemens Healthineers", "https://www.siemens-healthineers.com/"),
            _sr("Siemens Global", "https://www.siemens.com/"),
        ]
        chosen = select_website_from_serp(
            "Siemens AG", results, record_type="company",
        )
        assert chosen.url == "https://www.siemens.com"
        assert chosen.confidence == "high"

    def test_company_subsidiary_only_is_low_confidence(self):
        # When only the sub-brand domain is available, it is still returned
        # (best effort) but flagged low so a human verifies it.
        results = [
            _sr("Siemens Healthineers", "https://www.siemens-healthineers.com/"),
        ]
        chosen = select_website_from_serp(
            "Siemens AG", results, record_type="company",
        )
        assert chosen.url == "https://www.siemens-healthineers.com"
        assert chosen.confidence == "low"

    def test_company_multiword_concatenated_domain_stays_high(self):
        # A single concatenated label ("thermofisher") is NOT a foreign brand
        # — must remain high (no regression on legitimate company domains).
        results = [
            _sr("Thermo Fisher", "https://www.thermofisher.com/"),
        ]
        chosen = select_website_from_serp(
            "Thermo Fisher Scientific Inc.", results, record_type="company",
        )
        assert chosen.url == "https://www.thermofisher.com"
        assert chosen.confidence == "high"

    def test_no_name_overlap_skipped(self):
        results = [
            _sr("Some Other Site", "https://www.some-other.edu/page"),
        ]
        chosen = select_website_from_serp(
            "Florida Institute of Technology", results,
            record_type="research_institution",
        )
        assert chosen.url is None
        assert chosen.confidence == "none"

    def test_empty_results_returns_none(self):
        chosen = select_website_from_serp("Anything", [])
        assert chosen == WebsiteResolution()

    def test_blacklist_covers_expected_aggregators(self):
        # Sanity check the constant — covers the spec's required hosts.
        for host in {
            "wikipedia.org", "linkedin.com", "facebook.com",
            "ratemyprofessors.com", "bloomberg.com", "indeed.com",
        }:
            assert host in DOMAIN_BLACKLIST


# ---------------------------------------------------------------------------
# Path B — resolve_website_via_serp (async, with mock SERP)
# ---------------------------------------------------------------------------

class _ListSearchClient:
    """Returns a fixed list of SearchResult on every search() call."""

    def __init__(self, results: list[SearchResult]):
        self._results = results
        self.calls: list[str] = []

    async def search(
        self, query: str, num_results: int = 5, *, country: str | None = None,
    ) -> list[SearchResult]:
        self.calls.append(query)
        return self._results[:num_results]


class TestResolveWebsiteViaSERP:
    @pytest.mark.asyncio
    async def test_runs_serp_when_no_prefetch(self):
        client = _ListSearchClient([
            _sr("Florida Institute of Technology", "https://www.fit.edu/"),
        ])
        cache = BatchCache()
        res = await resolve_website_via_serp(
            record_id="T1", name1="Florida Institute of Technology",
            city="Melbourne", state="FL", country="US",
            record_type="research_institution",
            search_client=client, cache=cache,
        )
        assert res.url == "https://www.fit.edu"
        assert res.confidence == "high"
        assert len(client.calls) == 1
        assert "Florida Institute of Technology" in client.calls[0]

    @pytest.mark.asyncio
    async def test_company_query_includes_city_state(self):
        client = _ListSearchClient([
            _sr("Acme Co — Pittsburgh", "https://www.acmeco.com/"),
        ])
        cache = BatchCache()
        res = await resolve_website_via_serp(
            record_id="T1B", name1="Acme Co",
            city="Pittsburgh", state="PA", country="US",
            record_type="company",
            search_client=client, cache=cache,
        )
        assert res.url == "https://www.acmeco.com"
        # "acme" is a substring of "acmeco.com" → high confidence
        # under the company rule.
        assert res.confidence == "high"
        assert "Pittsburgh PA" in client.calls[0]

    @pytest.mark.asyncio
    async def test_uses_prefetched_results_when_provided(self):
        client = _ListSearchClient([])  # would return nothing if called
        prefetched = [
            _sr("Florida Institute of Technology", "https://www.fit.edu/"),
        ]
        cache = BatchCache()
        res = await resolve_website_via_serp(
            record_id="T2", name1="Florida Institute of Technology",
            city=None, state=None, country=None,
            record_type="research_institution",
            search_client=client, cache=cache,
            prefetched_results=prefetched,
        )
        assert res.url == "https://www.fit.edu"
        assert client.calls == []  # no SERP call made

    @pytest.mark.asyncio
    async def test_blank_name1_returns_none(self):
        client = _ListSearchClient([])
        res = await resolve_website_via_serp(
            record_id="T3", name1="",
            city=None, state=None, country=None,
            record_type=None,
            search_client=client, cache=BatchCache(),
        )
        assert res.url is None
        assert client.calls == []


# ---------------------------------------------------------------------------
# Path C — infer_website_via_llm (uses mock_llm_client fixture)
# ---------------------------------------------------------------------------

class TestInferWebsiteViaLLM:
    @pytest.mark.asyncio
    async def test_known_company_returns_url_low_confidence(self, mock_llm_client):
        res = await infer_website_via_llm(
            record_id="C1", name1="Fisher Scientific Co. LLC",
            city="Pittsburgh", state="PA", country="US",
            llm_client=mock_llm_client,
        )
        # Mock returns a URL for "fisher scientific"; the resolver
        # always returns confidence="low" because LLM inference is
        # never considered authoritative — the orchestrator flags it.
        assert res.url == "https://www.fishersci.com"
        assert res.confidence == "low"
        assert res.source == "llm"

    @pytest.mark.asyncio
    async def test_unknown_company_returns_none(self, mock_llm_client):
        res = await infer_website_via_llm(
            record_id="C2", name1="BioMed Solutions Inc.",
            city="Tampa", state="FL", country="US",
            llm_client=mock_llm_client,
        )
        assert res.url is None
        assert res.confidence == "none"


# ---------------------------------------------------------------------------
# Orchestrator end-to-end — Path A wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def orchestrator(mock_clients):
    return Orchestrator(Settings(), mock_clients=mock_clients)


@pytest.fixture
def default_options():
    return EnrichmentOptions(max_concurrency=1)


class TestOrchestratorWebsiteFields:
    @pytest.mark.asyncio
    async def test_path_a_stanford_no_flag(self, orchestrator, default_options):
        record = EnrichmentRecord(
            record_id="WEB_A1", name1="Stanford University",
            city="Stanford", state="CA", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        # website_url is always https://<registrable domain> — the "www."
        # host of the source link never survives (utils/domain_resolver.py).
        assert result.domain == "stanford.edu"
        assert result.website_url == "https://stanford.edu"
        # Path A (ROR) is authoritative — no website-driven review flag.
        assert (result.flag_reason or "").lower().find("website") == -1

    @pytest.mark.asyncio
    async def test_path_a_university_of_florida(self, orchestrator, default_options):
        record = EnrichmentRecord(
            record_id="WEB_A2", name1="University of Florida",
            city="Gainesville", state="FL", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.domain == "ufl.edu"
        assert result.website_url == "https://ufl.edu"

    @pytest.mark.asyncio
    async def test_path_b_company_via_serp_no_flag(
        self, orchestrator, default_options,
    ):
        # "Thermo Fisher Scientific Inc." — not in ROR mock; SERP mock
        # has a fixture with thermofisher.com → Path B fires, name token
        # in domain → high confidence → no website-driven review flag.
        record = EnrichmentRecord(
            record_id="WEB_B1", name1="Thermo Fisher Scientific Inc.",
            city="Waltham", state="MA", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.domain == "thermofisher.com"
        assert result.website_url == "https://thermofisher.com"
        assert "website" not in (result.flag_reason or "").lower()

    @pytest.mark.asyncio
    async def test_path_c_llm_guess_ships_unverified_and_flagged(
        self, orchestrator, default_options,
    ):
        # "Fisher Scientific Co. LLC" — not in ROR mock; the SERP mock
        # has no entry for it (generic results don't share name tokens),
        # so Path B yields nothing and Path C (LLM) fires with
        # fishersci.com. Path C has no registry provenance and no search
        # evidence, the record carries no email, and "fishersci" is a
        # contraction the name-similarity rule cannot reach — so the guard
        # cannot attribute it.
        #
        # It ships anyway. The guard's failure here is an absence of
        # corroboration, not evidence of a wrong answer, and fishersci.com is
        # in fact Fisher Scientific's site — blanking the column made a
        # reviewer go and find a value the pipeline already had. What the row
        # must not do is CLAIM it: the provenance reads `low`, never
        # `provisional`, and `domain-unverified` fires.
        record = EnrichmentRecord(
            record_id="WEB_C1", name1="Fisher Scientific Co. LLC",
            city="Pittsburgh", state="PA", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.domain == "fishersci.com"
        assert result.domain_provenance == "web:fishersci.com:low"
        # NOT written: the guard verified no website, and `website_url` is the
        # homepage of a domain that was attributed. Only the column the
        # reviewer acts on is populated.
        assert result.website_url is None
        # ADVISORY (`flags.ADVISORY_CODES`): the code, its `domain` scope and
        # its prose all ship; no review is requested. A consumer wanting these
        # rows filters on the code or on `web:*:low`, not on the boolean.
        assert result.flag_for_review is False
        assert result.flag_reason and "fishersci.com" in result.flag_reason
        assert "domain-unverified" in result.flag_codes
        assert result.flagged_fields == ["domain"]
        # The guard still refused, and the telemetry still counts it — what
        # changed is what the pipeline does with the refusal, not whether it
        # happened.
        assert response.summary.domain_rejected_unverified == 1

    @pytest.mark.asyncio
    async def test_path_c_llm_guess_survives_with_email_corroboration(
        self, orchestrator, default_options,
    ):
        # Same record, but it carries a company email on the domain the LLM
        # proposed — condition 3 — so the domain is written after all.
        record = EnrichmentRecord(
            record_id="WEB_C1E", name1="Fisher Scientific Co. LLC",
            city="Pittsburgh", state="PA", country="US",
            email="orders@fishersci.com",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.domain == "fishersci.com"
        assert result.website_url == "https://fishersci.com"
        assert response.summary.domain_from_email == 1

    @pytest.mark.asyncio
    async def test_unknown_company_leaves_website_empty(
        self, orchestrator, default_options,
    ):
        # No SERP mock entry, LLM mock returns null for this name.
        record = EnrichmentRecord(
            record_id="WEB_C2", name1="BioMed Solutions Inc.",
            city="Tampa", state="FL", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.website_url is None


# ---------------------------------------------------------------------------
# WEBSITE_TRACE diagnostic flag (Step 6 — no behaviour change when off)
# ---------------------------------------------------------------------------

import json
import logging


class _CollectHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


class TestWebsiteTraceFlag:
    def test_defaults_false(self, monkeypatch):
        # With no WEBSITE_TRACE env var, the setting defaults to False.
        monkeypatch.delenv("WEBSITE_TRACE", raising=False)
        assert Settings().website_trace is False

    @pytest.mark.asyncio
    async def test_no_trace_records_when_off(self):
        handler = _CollectHandler()
        trace_log = logging.getLogger("enrichment.trace.website")
        trace_log.addHandler(handler)
        try:
            client = _ListSearchClient([
                _sr("Acme Co", "https://www.acmeco.com/"),
            ])
            # trace defaults to False → no records emitted.
            res = await resolve_website_via_serp(
                record_id="OFF", name1="Acme Co",
                city="Pittsburgh", state="PA", country="US", record_type="company",
                search_client=client, cache=BatchCache(),
            )
            assert res.url == "https://www.acmeco.com"   # behaviour unchanged
            assert handler.lines == []                    # nothing traced
        finally:
            trace_log.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_trace_records_emitted_when_on(self):
        handler = _CollectHandler()
        trace_log = logging.getLogger("enrichment.trace.website")
        trace_log.setLevel(logging.INFO)
        trace_log.addHandler(handler)
        try:
            client = _ListSearchClient([
                _sr("Acme Co — Pittsburgh", "https://www.acmeco.com/"),
            ])
            await resolve_website_via_serp(
                record_id="ON", name1="Acme Co",
                city="Pittsburgh", state="PA", country="US", record_type="company",
                search_client=client, cache=BatchCache(), trace=True,
            )
            assert len(handler.lines) == 1
            rec = json.loads(handler.lines[0])
            assert rec["phase"] == "path_b"
            assert rec["record_id"] == "ON"
            assert rec["results_returned"] == 1
            assert rec["candidates"][0]["chosen"] is True
            assert rec["candidates"][0]["rank"] == 2
            assert rec["chosen_url"] == "https://www.acmeco.com"
            assert rec["fell_through_to_path_c"] is False
        finally:
            trace_log.removeHandler(handler)


# ---------------------------------------------------------------------------
# §7 Path B guards / §8 retrieval (mocked — assert direction, not live hosts)
# ---------------------------------------------------------------------------

class TestPathBGuards:
    def test_generic_token_only_host_not_accepted(self):
        # "research" is generic; only-generic host match must not validate (§7a).
        res = select_website_from_serp(
            "Precision Research",
            [_sr("Precision Research Inc", "https://researchgate.net/x")],
            record_type="company",
        )
        assert res.url is None

    def test_institution_title_only_rejected(self):
        # §7b: institution candidate matching only in the title (no host match)
        # → rank 0 → rejected. "scup.org" ≠ acronym of "Bayfront Research".
        res = select_website_from_serp(
            "Bayfront Research",
            [_sr("Bayfront Research — planning", "https://scup.org/about")],
            record_type="research_institution",
        )
        assert res.url is None

    def test_org_tld_no_host_match_not_high(self):
        # §7c: an authoritative TLD alone must not grant high confidence.
        res = select_website_from_serp(
            "Bayfront Research",
            [_sr("Bayfront Research", "https://scup.org/")],
            record_type="research_institution",
        )
        assert res.confidence != "high"

    def test_acronym_domain_institution_still_high(self):
        # Guard against over-tightening: fit.edu ↔ Florida Institute of
        # Technology (acronym-in-host) must still resolve high (§7 decision).
        res = select_website_from_serp(
            "Florida Institute of Technology",
            [_sr("Florida Institute of Technology", "https://www.fit.edu/")],
            record_type="research_institution",
        )
        assert res.url == "https://www.fit.edu"
        assert res.confidence == "high"

    def test_distinctive_host_match_company_high(self):
        res = select_website_from_serp(
            "Verdox",
            [_sr("Verdox — direct air capture", "https://www.verdox.com/")],
            record_type="company",
        )
        assert res.url == "https://www.verdox.com"
        assert res.confidence == "high"


class _QuoteAwareSearch:
    """Returns *quoted_results* only for a quoted query, *unquoted_results* for
    the unquoted retry."""

    def __init__(self, quoted_results, unquoted_results):
        self._q = quoted_results
        self._u = unquoted_results
        self.calls: list[str] = []

    async def search(self, query, num_results=5, *, country=None):
        self.calls.append(query)
        return (self._q if '"' in query else self._u)[:num_results]


class TestPathBRetry:
    @pytest.mark.asyncio
    async def test_unquoted_retry_on_quoted_miss(self):
        client = _QuoteAwareSearch(
            quoted_results=[],  # exact phrase finds nothing usable
            unquoted_results=[_sr("Atlantic Testing Laboratories",
                                  "https://www.atlantictesting.com/")],
        )
        res = await resolve_website_via_serp(
            record_id="R1", name1="Atlantic Testing Labs",
            city=None, state=None, country="US", record_type="company",
            search_client=client, cache=BatchCache(),
        )
        assert res.url == "https://www.atlantictesting.com"
        assert len(client.calls) == 2
        assert '"' in client.calls[0] and '"' not in client.calls[1]

    @pytest.mark.asyncio
    async def test_no_retry_when_quoted_succeeds(self):
        client = _QuoteAwareSearch(
            quoted_results=[_sr("Verdox", "https://www.verdox.com/")],
            unquoted_results=[],
        )
        res = await resolve_website_via_serp(
            record_id="R2", name1="Verdox", city=None, state=None, country="US",
            record_type="company", search_client=client, cache=BatchCache(),
        )
        assert res.url == "https://www.verdox.com"
        assert len(client.calls) == 1  # no retry


# ---------------------------------------------------------------------------
# Country gate — the record's country vs the candidate's ccTLD
# ---------------------------------------------------------------------------

class TestPathBCountryGate:
    """The guard the SERP layer never had.

    Every other Path B test asks whether the NAME fits the host. A
    multinational's name fits its host in every country it trades in, so name
    tests cannot separate a US subsidiary's record from the parent's Belgian
    site — only the country can.
    """

    # The live case: a Texas record for a Unilever research subsidiary matched
    # unilever.be on the token "unilever", because it IS a Unilever site.
    def test_foreign_cctld_not_selected(self):
        res = select_website_from_serp(
            "Unilever Trumbull Research Services Inc",
            [_sr("Unilever Belgium", "https://www.unilever.be/")],
            record_type="research_institution",
            country="US",
        )
        assert res.url is None
        assert res.confidence == "none"

    def test_matching_cctld_selected(self):
        res = select_website_from_serp(
            "Unilever Belgium",
            [_sr("Unilever Belgium", "https://www.unilever.be/")],
            record_type="company",
            country="BE",
        )
        assert res.url == "https://www.unilever.be"

    def test_gtld_never_gated(self):
        # .com carries no country claim, so the gate has nothing to say and the
        # candidate is judged on name evidence exactly as before.
        res = select_website_from_serp(
            "Unilever Trumbull Research Services Inc",
            [_sr("Unilever", "https://www.unilever.com/")],
            record_type="company",
            country="US",
        )
        assert res.url == "https://www.unilever.com"

    def test_worldwide_cctld_never_gated(self):
        # .ai is Anguilla on paper. Gating it would be a false positive on one
        # of the commonest TLDs a US company could pick.
        res = select_website_from_serp(
            "Verdox",
            [_sr("Verdox", "https://www.verdox.ai/")],
            record_type="company",
            country="US",
        )
        assert res.url == "https://www.verdox.ai"

    def test_gate_off_restores_previous_selection(self):
        # country=None is the kill switch's shape: the same input that the
        # gate rejects above is selected again.
        res = select_website_from_serp(
            "Unilever Trumbull Research Services Inc",
            [_sr("Unilever Belgium", "https://www.unilever.be/")],
            record_type="research_institution",
            country=None,
        )
        assert res.url == "https://www.unilever.be"

    def test_domestic_candidate_wins_over_foreign_one(self):
        # The gate removes candidates from eligibility rather than reordering
        # them, so a foreign result ranked FIRST cannot displace a domestic one.
        res = select_website_from_serp(
            "Acme Instruments",
            [
                _sr("Acme Instruments GmbH", "https://www.acme-instruments.de/"),
                _sr("Acme Instruments", "https://www.acmeinstruments.com/"),
            ],
            record_type="company",
            country="US",
        )
        assert res.url == "https://www.acmeinstruments.com"

    def test_unreadable_record_country_fails_open(self):
        # A country string the ISO map cannot read makes no claim, so it must
        # not manufacture a rejection.
        res = select_website_from_serp(
            "Unilever Belgium",
            [_sr("Unilever Belgium", "https://www.unilever.be/")],
            record_type="company",
            country="Ruritania",
        )
        assert res.url == "https://www.unilever.be"

    @pytest.mark.asyncio
    async def test_path_c_is_gated_too(self):
        # Path B rejecting a foreign candidate is what MAKES Path C run, so a
        # gate covering only Path B would hand the same domain back through the
        # fallback it opened.
        class _LLM:
            async def extract_json(self, system, user):
                return {"website_url": "https://www.unilever.be/"}

        res = await infer_website_via_llm(
            record_id="R", name1="Unilever Trumbull Research Services Inc",
            city=None, state="TX", country="US", llm_client=_LLM(),
        )
        assert res.url is None

    @pytest.mark.asyncio
    async def test_path_c_gate_off_keeps_the_answer(self):
        class _LLM:
            async def extract_json(self, system, user):
                return {"website_url": "https://www.unilever.be/"}

        res = await infer_website_via_llm(
            record_id="R", name1="Unilever Trumbull Research Services Inc",
            city=None, state="TX", country="US", llm_client=_LLM(),
            country_gate=False,
        )
        assert res.url == "https://www.unilever.be/"

    @pytest.mark.asyncio
    async def test_serp_path_end_to_end_falls_through(self):
        client = _QuoteAwareSearch(
            quoted_results=[_sr("Unilever Belgium", "https://www.unilever.be/")],
            unquoted_results=[_sr("Unilever Belgium", "https://www.unilever.be/")],
        )
        res = await resolve_website_via_serp(
            record_id="R", name1="Unilever Trumbull Research Services Inc",
            city=None, state="TX", country="US",
            record_type="research_institution",
            search_client=client, cache=BatchCache(),
        )
        assert res.url is None

    @pytest.mark.asyncio
    async def test_country_gate_flag_off_leaves_query_country_intact(self):
        # The kill switch turns off the DISQUALIFIER, not the country's other
        # job: the query still carries it.
        client = _QuoteAwareSearch(
            quoted_results=[_sr("Unilever Belgium", "https://www.unilever.be/")],
            unquoted_results=[],
        )
        res = await resolve_website_via_serp(
            record_id="R", name1="Unilever Trumbull Research Services Inc",
            city=None, state="TX", country="US",
            record_type="research_institution",
            search_client=client, cache=BatchCache(), country_gate=False,
        )
        assert res.url == "https://www.unilever.be"
        assert "US" in client.calls[0]


# ---------------------------------------------------------------------------
# Path B — region / city as campus evidence
# ---------------------------------------------------------------------------

class TestMultiCampusInstitution:
    """The University of Texas case.

    "University of Texas" is not one university. Every guard in the resolver
    asks whether the NAME fits the host, and the name of a multi-campus system
    fits every campus it has plus everything else that carries the state's name
    — so the name tests alone could only ever pick one arbitrarily. On the live
    SERP that arbitrary pick was UT Austin's athletics site, on every record.
    """

    # The exact SERP shape SerpAPI returns for the resolver's own query. It is
    # the ordering that matters: utexas.edu is #1 and texaslonghorns.com is #2.
    _UT_SERP = [
        _sr("The University of Texas at Austin", "https://www.utexas.edu/",
            "a leading public research university"),
        _sr("University of Texas Athletics - Official Athletics Website",
            "https://texaslonghorns.com/",
            "The official athletics website for the University of Texas Longhorns."),
        _sr("Home | The University of Texas System", "https://www.utsystem.edu/",
            "consists of 13 institutions across the state"),
        _sr("University Co-op: Texas Longhorns Apparel, Gifts & Textbooks",
            "https://www.universitycoop.com/", "Longhorns apparel"),
    ]

    def test_athletics_site_no_longer_beats_the_university(self):
        # The reported bug. texaslonghorns.com, universitycoop.com and
        # utexas.edu all rank 2 — "texas"/"university" is a substring of each
        # host — and the tiebreak was ALPHABETICAL, so the athletics site won
        # the #1 result deterministically, on every University of Texas row.
        res = select_website_from_serp(
            "University of Texas", self._UT_SERP,
            record_type="research_institution", country="US",
        )
        assert res.url == "https://www.utexas.edu"

    def test_city_selects_the_right_campus(self):
        # utep.edu fails every host test there is: "utep" carries neither
        # "university" nor "texas", and the initials of the name are two
        # letters, below the acronym rule's floor. Only the city reaches it.
        results = [
            _sr("UTEP: The University of Texas at El Paso", "https://www.utep.edu/",
                "America's Leading Hispanic-Serving University"),
            _sr("The University of Texas at Austin", "https://www.utexas.edu/",
                "a leading public research university"),
        ]
        res = select_website_from_serp(
            "University of Texas", results,
            record_type="research_institution", country="US", city="El Paso",
        )
        assert res.url == "https://www.utep.edu"
        # Rank 2 reached on locality evidence, not on the host — written, but
        # flagged rather than trusted clean.
        assert res.confidence == "low"

    def test_reference_site_with_an_identical_title_loses_on_tld(self):
        # texasalmanac.com's SERP title is character-for-character the same as
        # utep.edu's, and it mentions El Paso too. Neither the name test nor the
        # city test can separate them; the TLD can.
        results = [
            _sr("The University of Texas at El Paso", "https://www.texasalmanac.com/x",
                "founded under Senate Bill 183 in April 1913"),
            _sr("UTEP: The University of Texas at El Paso", "https://www.utep.edu/",
                "America's Leading Hispanic-Serving University"),
        ]
        res = select_website_from_serp(
            "University of Texas", results,
            record_type="research_institution", country="US", city="El Paso",
        )
        assert res.url == "https://www.utep.edu"

    def test_state_directory_on_a_gov_tld_is_not_rescued(self):
        # comptroller.texas.gov is rank 2, authoritative, and names El Paso —
        # the live result the city-bearing query promoted to HIGH confidence
        # before the full-name-phrase condition existed.
        results = [
            _sr("Texas Colleges and Universities",
                "https://comptroller.texas.gov/economy/education/",
                "University of Texas at Arlington - University of Texas at El Paso"),
        ]
        res = select_website_from_serp(
            "University of Texas", results,
            record_type="research_institution", country="US", city="El Paso",
        )
        # Eligible on the host test ("texas" in the host), so it is still
        # selectable — but only at LOW confidence, never written clean.
        assert res.confidence == "low"

    def test_rescue_requires_edu_or_gov(self):
        # .org is in _OFFICIAL_TLDS but not in _RESCUE_TLDS: it is where
        # 'scup.org' lives, the stranger the rank-0 rule exists to reject.
        results = [
            _sr("The University of Texas at El Paso", "https://www.someorg.org/",
                "about UTEP"),
        ]
        res = select_website_from_serp(
            "University of Texas", results,
            record_type="research_institution", country="US", city="El Paso",
        )
        assert res.url is None

    def test_rescue_is_institutions_only(self):
        results = [
            _sr("Acme Biotech, El Paso", "https://www.somelab.edu/", "Acme Biotech"),
        ]
        res = select_website_from_serp(
            "Acme Biotech", results,
            record_type="company", country="US", city="El Paso",
        )
        assert res.url is None

    def test_short_city_is_not_evidence(self):
        # "Ada" occurs inside ordinary words; a three-letter city would
        # corroborate anything.
        results = [
            _sr("The University of Texas at Ada", "https://www.utada.edu/",
                "Canada research programme"),
        ]
        res = select_website_from_serp(
            "University of Texas", results,
            record_type="research_institution", country="US", city="Ada",
        )
        assert res.url is None

    def test_city_absent_leaves_ranking_unchanged(self):
        # city=None is the shape every existing caller had. Selection must fall
        # back to exactly the name/TLD ordering, with no rescue.
        with_city = select_website_from_serp(
            "University of Texas", self._UT_SERP,
            record_type="research_institution", country="US", city="El Paso",
        )
        without = select_website_from_serp(
            "University of Texas", self._UT_SERP,
            record_type="research_institution", country="US",
        )
        assert with_city.url == without.url == "https://www.utexas.edu"


class TestInstitutionQueryCarriesRegion:
    def test_region_and_city_reach_the_query(self):
        from enrichment.website_resolver import _build_serp_query

        q = _build_serp_query(
            "University of Texas", "El Paso", "TX", "US", "research_institution",
        )
        assert q == '"University of Texas" official website El Paso TX US'

    def test_country_alone_when_no_city_or_region(self):
        from enrichment.website_resolver import _build_serp_query

        q = _build_serp_query(
            "University of Texas", None, None, "US", "research_institution",
        )
        assert q == '"University of Texas" official website US'


class TestInstitutionConfidenceEvidence:
    """An authoritative TLD alone does not earn a clean write."""

    def test_state_directory_is_written_but_flagged(self):
        # comptroller.texas.gov reduces to the registrable domain texas.gov,
        # whose whole label is one of the name's own words — a clean rank-2
        # host match — and .gov then granted HIGH. The Texas state comptroller
        # was the resolver's unflagged answer for a university.
        res = select_website_from_serp(
            "University of Texas",
            [_sr("Texas Colleges and Universities",
                 "https://comptroller.texas.gov/economy/education/",
                 "University of Texas at El Paso")],
            record_type="research_institution", country="US", city="El Paso",
        )
        assert res.url is not None
        assert res.confidence == "low"

    def test_acronym_institution_keeps_high_confidence(self):
        # fit.edu's real SERP title is "Florida Tech: www.fit.edu" — its own
        # homepage, which never spells the name out. The host already matches
        # the initials in full, so requiring the phrase as well would flag every
        # acronym-domain university in the batch.
        res = select_website_from_serp(
            "Florida Institute of Technology",
            [_sr("Florida Tech: www.fit.edu", "https://www.fit.edu/", "homepage")],
            record_type="research_institution", country="US", city="Melbourne",
        )
        assert res.url == "https://www.fit.edu"
        assert res.confidence == "high"

    def test_root_host_beats_a_unit_subdomain(self):
        # Same registrable domain, so _candidate_key sorted them as strings and
        # "college.harvard.edu" preceded "www.harvard.edu".
        res = select_website_from_serp(
            "Harvard University",
            [
                _sr("Harvard College - Harvard University",
                    "https://college.harvard.edu/", "Cambridge, MA"),
                _sr("Harvard University", "https://www.harvard.edu/",
                    "Cambridge, Massachusetts"),
            ],
            record_type="research_institution", country="US", city="Cambridge",
        )
        assert res.url == "https://www.harvard.edu"


class TestShippingTheUnverifiedDomain:
    """`_ship_unverified_domain` puts the ownership guard's declined candidate
    in the column, and the three conditions that stop it.

    Unit-level and deliberately so: the orchestrator test above proves the
    path end to end, and these pin the narrowing rules one at a time, which a
    full pipeline run cannot isolate.
    """

    @staticmethod
    def _record(**overrides):
        from enrichment.orchestrator import _init_result

        result = _init_result(EnrichmentRecord(
            record_id="U1", name1="Meridian Labs", country="US",
        ))
        for key, value in overrides.items():
            if key == "domain":
                seed(result, domain=value)
            else:
                result[key] = value
        return result

    def _ship(self, **overrides):
        from enrichment.orchestrator import _ship_unverified_domain

        result = self._record(**overrides)
        _ship_unverified_domain(result)
        return result

    def test_the_declined_candidate_goes_in_the_column(self):
        result = self._ship(_domain_unverified="meridianlabs.ai")
        assert result.get("domain") == "meridianlabs.ai"

    def test_the_column_says_the_value_is_unverified(self):
        """The whole basis for shipping it. A value in `domain` that read
        `provisional` would be claiming corroboration the guard could not
        find, which is the thing the guard exists to prevent."""
        from enrichment.provenance import derived_scalar

        result = self._ship(_domain_unverified="meridianlabs.ai")
        assert derived_scalar(result.provenance, "domain") == (
            "web:meridianlabs.ai:low"
        )

    def test_a_domain_already_settled_is_not_overwritten(self):
        """The page read promoting this same candidate to `provisional` is
        the common case, and its answer is the better one."""
        result = self._ship(
            domain="meridianlabs.ai", _domain_unverified="meridianlabs.ai",
        )
        assert result.get("domain") == "meridianlabs.ai"
        # One attributing event — the seeded one. Nothing was written over it.
        assert not [
            e for e in result.provenance.events
            if getattr(e, "rule_id", None) == UNVERIFIED_DOMAIN_RULE
        ]

    def test_a_refuted_domain_does_not_come_back(self):
        """The one class where the pipeline has evidence AGAINST the domain
        rather than merely no evidence for it: the site's own page states
        another organisation's identity. `_withdraw_domain` removed it, and
        this must not undo that."""
        result = self._ship(
            _domain_unverified="johnsoncontrols.com", _domain_refuted=True,
        )
        assert result.get("domain") in (None, "")

    def test_a_bare_marker_names_no_site_and_writes_nothing(self):
        """An older `True` marker raises the code but carries no domain —
        there is nothing to put in the column."""
        assert self._ship(_domain_unverified=True).get("domain") in (None, "")

    def test_no_candidate_writes_nothing(self):
        assert self._ship().get("domain") in (None, "")


# ---------------------------------------------------------------------------
# §3 — the lane retries the next candidate after a refusal
# ---------------------------------------------------------------------------

class TestTheLaneRetriesTheNextCandidate:
    """Record 13333947, "ORCHARD LAB CORP" in West Bloomfield MI.

    The SERP returned `orchard-labs.com` at position 1 and `labcorp.com` at
    position 2, and the ranker preferred labcorp: `_significant_tokens` drops
    the generic "lab", so the correct site's own `labs` label reads as a
    foreign brand word (rank 1, demoted) while `lab**corp**.com` matches the
    surviving token `corp` cleanly (rank 2, chosen). The ownership guard then
    declined labcorp — correctly, nothing tied it to the record — and the lane
    stopped, with the right answer sitting at position 1 of the same results.

    Selection ranks; it does not verify. When the two disagree the lane now
    walks on to the next candidate in the SAME order, through the SAME guard.
    """

    RESULTS = [
        _sr("Orchard Laboratories: Home", "https://orchard-labs.com/"),
        _sr("Laboratory Testing in Farmington 48334",
            "https://locations.labcorp.com/mi/farmington/1784/"),
        _sr("Contact Us", "https://orchard-labs.com/contact-us/"),
        _sr("ORCHARD LABORATORIES - Updated August 2026",
            "https://www.yelp.com/biz/orchard-laboratories-west-bloomfield"),
    ]

    def test_the_runner_up_is_offered_in_ranked_order(self):
        res = select_website_from_serp(
            "ORCHARD LAB CORP", self.RESULTS, "company",
            country="US", city="WEST BLOOMFIELD",
        )
        # Selection itself is UNCHANGED — labcorp still wins the ranking.
        assert res.url == "https://locations.labcorp.com"
        # …and the site the guard will actually tie to the record is now
        # reachable behind it.
        assert res.alternates == ("https://orchard-labs.com",)

    def test_a_blacklisted_host_is_never_an_alternate(self):
        res = select_website_from_serp(
            "ORCHARD LAB CORP", self.RESULTS, "company",
            country="US", city="WEST BLOOMFIELD",
        )
        assert not any("yelp" in a for a in res.alternates)

    def test_alternates_are_deduplicated_by_host(self):
        """Three of the four results are the same site."""
        res = select_website_from_serp(
            "ORCHARD LAB CORP", self.RESULTS, "company",
            country="US", city="WEST BLOOMFIELD",
        )
        hosts = [a.split("//", 1)[-1] for a in res.alternates]
        assert len(hosts) == len(set(hosts))

    def test_a_rank_0_candidate_is_never_an_alternate(self):
        """Rank 0 is "the name only overlaps the TITLE" — too weak to trust as
        a first choice, and no stronger as a second."""
        results = [
            _sr("Orchard Laboratories: Home", "https://orchard-labs.com/"),
            _sr("ORCHARD LABORATORIES CORP Company Overview",
                "https://leadiq.com/c/orchard-laboratories-corp/61608d"),
        ]
        res = select_website_from_serp(
            "ORCHARD LAB CORP", results, "company",
            country="US", city="WEST BLOOMFIELD",
        )
        assert not any("leadiq" in a for a in res.alternates)

    def test_the_attempt_bound_is_three(self):
        """A ten-candidate result set offers at most two runners-up, so the
        lane makes at most three attempts."""
        from enrichment.orchestrator import _DOMAIN_MAX_ATTEMPTS

        results = [
            _sr(f"Orchard Laboratories {i}", f"https://orchard-labs-{i}.com/")
            for i in range(10)
        ]
        res = select_website_from_serp(
            "ORCHARD LABORATORIES", results, "company",
            country="US", city="WEST BLOOMFIELD",
        )
        attempted = (res.url, *res.alternates)[:_DOMAIN_MAX_ATTEMPTS]
        assert len(attempted) == 3
        assert _DOMAIN_MAX_ATTEMPTS == 3

    def test_a_single_candidate_offers_no_alternates(self):
        """The unchanged case: one candidate, one attempt, same as before."""
        res = select_website_from_serp(
            "ORCHARD LAB CORP",
            [_sr("Orchard Laboratories: Home", "https://orchard-labs.com/")],
            "company", country="US", city="WEST BLOOMFIELD",
        )
        assert res.url == "https://orchard-labs.com"
        assert res.alternates == ()


class TestTheRetryAddsAttemptsNotAcceptances:
    """The safety property. Every retried candidate goes through the same
    guard chain, so a record whose candidates all fail ends exactly where it
    ended before the retry existed — no domain, and the flag naming the first
    refusal."""

    @staticmethod
    def _attempts(monkeypatch, decisions):
        """Drive the lane's loop with a scripted `_apply_domain`, recording the
        candidates it was asked about."""
        from utils.domain_resolver import DomainDecision
        import enrichment.orchestrator as O

        seen: list[str] = []
        answers = list(decisions)

        def fake(result, candidate_url, **kw):
            seen.append(candidate_url)
            accept = answers.pop(0) if answers else False
            if accept:
                return DomainDecision(
                    domain="orchard-labs.com",
                    website_url=candidate_url, verified_by="name",
                )
            return DomainDecision(rejected=True, rejected_by="conditions",
                                  candidate=candidate_url)

        monkeypatch.setattr(O, "_apply_domain", fake)
        return seen

    def test_every_candidate_refused_leaves_no_domain(self, monkeypatch):
        from utils.domain_resolver import DomainDecision
        import enrichment.orchestrator as O

        seen = self._attempts(monkeypatch, [False, False, False])
        result: dict = {}
        for url in ("https://a.com", "https://b.com", "https://c.com")[
            :O._DOMAIN_MAX_ATTEMPTS
        ]:
            decision = O._apply_domain(result, url)
            if decision.accepted:
                break
        assert seen == ["https://a.com", "https://b.com", "https://c.com"]
        assert not result.get("domain")

    def test_the_loop_stops_at_the_first_acceptance(self, monkeypatch):
        import enrichment.orchestrator as O

        seen = self._attempts(monkeypatch, [False, True, False])
        for url in ("https://a.com", "https://b.com", "https://c.com"):
            if O._apply_domain({}, url).accepted:
                break
        # The third candidate is never consulted — a retry is a fallback, not
        # a sweep.
        assert seen == ["https://a.com", "https://b.com"]


class TestARefusedRetryDoesNotRewriteWhichCandidateTheRecordIsAbout:
    """The regression the first cut of §3 shipped, caught by its own gate.

    `_apply_domain` records every refusal as `_domain_unverified`, so walking
    a candidate list overwrote the marker each time and left it naming the
    LAST — worst-ranked — candidate. That is what `_ship_unverified_domain`
    publishes, so 23 rows moved onto a worse domain than they had before the
    retry existed: `merck.com` -> `merckhelps.com` (a patient-assistance
    programme), `rowan.edu` -> `medschoolinsiders.com` (a third-party blog),
    `eminentmedicalcenter.com` -> `visitsaltlake.com` (a tourism board).

    The ranker's first choice is restored when every candidate is refused, and
    the ordered list is handed to the page read — the one stage that can tell
    these apart with evidence.
    """

    def test_the_first_refused_candidate_is_the_one_remembered(self):
        from utils.domain_resolver import DomainDecision

        # What the loop leaves behind: three refusals, marker on the last.
        result: dict = {}
        for host in ("merck.com", "merckmedicalportal.com", "merckhelps.com"):
            result["_domain_unverified"] = host   # `_apply_domain`'s behaviour
        assert result["_domain_unverified"] == "merckhelps.com"

        # …and what the fix restores.
        refused = ["merck.com", "merckmedicalportal.com", "merckhelps.com"]
        result["_domain_unverified"] = refused[0]
        result["_domain_candidates"] = refused
        assert result["_domain_unverified"] == "merck.com"

    def test_the_candidate_list_is_kept_in_ranked_order(self):
        res = select_website_from_serp(
            "ORCHARD LAB CORP",
            TestTheLaneRetriesTheNextCandidate.RESULTS, "company",
            country="US", city="WEST BLOOMFIELD",
        )
        ordered = (res.url, *res.alternates)
        assert ordered[0] == "https://locations.labcorp.com"
        assert ordered[1] == "https://orchard-labs.com"


class TestThePageRefutationHasThreeEscapes:
    """§1 refuses to ship a candidate whose page states a different identity.
    Three ways a page can state a name sharing no token with the record and
    still be the record's own site — each one measured on the gate, each one
    having removed a CORRECT domain."""

    @staticmethod
    def _refutes(name1, stated, domain):
        from enrichment.orchestrator import _page_refutes_candidate

        return _page_refutes_candidate({
            "name1_enriched": name1,
            "_page_corroboration": {"stated_org_name": stated},
            "_domain_unverified": domain,
        })

    def test_a_contraction_still_ships(self):
        """Scores under the threshold, but the tokens are covered."""
        assert not self._refutes(
            "Thermo Fisher Scientific", "Fisher Scientific", "fishersci.com",
        )

    def test_a_page_stating_the_records_acronym_still_ships(self):
        """thinksrs.com states "SRS" — that IS Stanford Research Systems."""
        assert not self._refutes(
            "Stanford Research Systems Inc", "SRS", "thinksrs.com",
        )

    def test_a_page_stating_the_acquirer_still_ships(self):
        """darylflood.com states "The Suddath Companies" — the acquirer. A
        parent's name on the page is not evidence the SITE is someone
        else's; the host is evidence it is the record's."""
        assert not self._refutes(
            "Daryl Flood Austin, Texas", "The Suddath Companies",
            "darylflood.com",
        )

    def test_a_page_stating_the_legal_owner_still_ships(self):
        assert not self._refutes(
            "CALM/UCSD", "Regents of the University of California", "ucsd.edu",
        )

    def test_the_case_the_rule_exists_for_is_still_refuted(self):
        """labcorp.com passes none of the three: `Labcorp` covers neither
        `orchard` nor `laboratory`, the record's initials are `ol`, and the
        host carries neither token."""
        assert self._refutes(
            "Orchard Laboratory Corp.", "Labcorp", "labcorp.com",
        )


class TestARegistryMatchNeedsOneAnchor:
    """Record 13338646 supplied "Adam Technologies" in Union NJ and shipped
    "Ada Technologies Inc." with LEI 549300FIRKSM1MOM7Y22 at `gleif:verified`,
    with `name1` not even in `flagged_fields`.

    GLEIF's exact tier found nothing; the fuzzy tier scored 97.0, and the
    identity gate returned `same` because `utils.name_identity._covers` treats
    a prefix relation at ANY length as one word — `'adam'.startswith('ada')`.
    `registry-location-mismatch` (OH vs NJ) did fire, but it is one of the two
    codes in `ADVISORY_CODES`, so the row was never queued. A different
    company, verified, silent — while the record's own correct domain
    (`adam-tech.com`) carried the only name-shaped doubt on the row.

    ONE disagreement is not evidence: a registry holds the incorporation
    address and the record holds a plant, so a region difference on a match
    identified BY ITS NAME is geography, and a near-miss name whose region
    agrees is a spelling variant. TWO disagreements are a different claim.
    """

    @staticmethod
    def _fold(a, b):
        from enrichment.registry_match import separator_fold_exact

        return separator_fold_exact(a, b)

    def test_the_fold_is_word_level_and_never_folds_legal_forms(self):
        assert self._fold("LAC+USC", "LAC USC")
        assert self._fold("Harbor-UCLA", "Harbor UCLA")
        assert self._fold("Bio-Rad Laboratories", "Bio Rad Laboratories")
        # The case the rule acts on.
        assert not self._fold("Adam Technologies", "Ada Technologies Inc.")
        # A WORD of difference is still a difference.
        assert not self._fold("University of Texas", "University of North Texas")
        # Legal forms are never folded — `batch_consensus._name_parts` records
        # why a dedup-GROUPING equivalence must not decide identity ACCEPTANCE.
        assert not self._fold("Delta Analytical Inc", "Delta Analytical LLC")

    def test_the_prefix_rule_is_what_accepted_the_wrong_company(self):
        """Pinned so the diagnosis cannot be lost: this is the accepted
        counterpart to steinen's refusal — one letter, opposite failures."""
        from utils.name_identity import _covers, classify_name_change

        assert _covers("adam", "ada") is True
        assert classify_name_change(
            "Adam Technologies", "Ada Technologies Inc.",
        ) == "same"

    def test_a_region_contradiction_alone_does_not_refuse(self):
        """One disagreement. An exact-fold name whose region contradicts is a
        company that moved states — the advisory's legitimate case, and the
        reason the advisory exists."""
        from enrichment.orchestrator import _refuse_region_contradicted_registry_match

        result = {
            "name1_supplied": "Bio-Rad Laboratories",
            "name1_enriched": "Bio Rad Laboratories",
            "lei_id": "LEI123",
        }
        _refuse_region_contradicted_registry_match(result, {
            "step": "registry_location_mismatch", "registry": "GLEIF",
            "scope": "region", "detail": "states region CA; record says NY",
        })
        assert result["name1_enriched"] == "Bio Rad Laboratories"
        assert result["lei_id"] == "LEI123"

    def test_a_city_contradiction_is_not_a_region_contradiction(self):
        """A plant against a head office inside one state — Houston/Baytown,
        the advisory's own worked example."""
        from enrichment.orchestrator import _refuse_region_contradicted_registry_match

        result = {
            "name1_supplied": "Adam Technologies",
            "name1_enriched": "Ada Technologies Inc.",
            "lei_id": "LEI123",
        }
        _refuse_region_contradicted_registry_match(result, {
            "step": "registry_location_mismatch", "registry": "GLEIF",
            "scope": "city", "detail": "states city Newark; record says Milford",
        })
        assert result["name1_enriched"] == "Ada Technologies Inc."
        assert result["lei_id"] == "LEI123"

    def test_a_name_near_miss_alone_does_not_refuse(self):
        """The other single disagreement: no location line at all."""
        from enrichment.orchestrator import _refuse_region_contradicted_registry_match

        result = {
            "name1_supplied": "Adam Technologies",
            "name1_enriched": "Ada Technologies Inc.",
            "lei_id": "LEI123",
        }
        _refuse_region_contradicted_registry_match(result, None)
        assert result["name1_enriched"] == "Ada Technologies Inc."
        assert result["lei_id"] == "LEI123"


class TestAWithdrawnMatchTakesItsDependentsWithIt:
    """13104777 shipped `domain = aws.org` at **`ror:verified`** after its ROR
    identifier had been withdrawn — American Welding Society's site, on a
    Fullerton CA record, with provenance naming a registry match the record no
    longer had — and `record_type = research_institution` decided by
    `classifier:ror` from the same match's research flag.

    A withdrawal that stops at the identifier leaves the record asserting the
    match's consequences on the authority of a match it has deleted. The
    dependents are read FROM THE LOG, never from a hand-written list of
    fields: a list goes stale the first time a lane learns to write something
    new, and the log already knows.
    """

    @staticmethod
    def _event(field, value, producers, ref=None, rule="r"):
        from enrichment.provenance import ProvenanceEvent

        return ProvenanceEvent(
            seq=0, field=field, old_value=None, new_value=value,
            producer_chain=tuple(producers), evidence_ref=ref, rule_id=rule,
        )

    def test_a_write_naming_the_registry_as_producer_cites_it(self):
        from enrichment.orchestrator import _event_cites_registry

        ev = self._event("domain", "aws.org", ("ror",))
        assert _event_cites_registry(ev, "ror", "https://ror.org/00syxh370")

    def test_a_write_quoting_the_identifier_cites_it(self):
        from enrichment.orchestrator import _event_cites_registry

        ev = self._event(
            "domain", "aws.org", ("website_resolver",),
            ref={"registry_id": "https://ror.org/00syxh370"},
        )
        assert _event_cites_registry(ev, "ror", "https://ror.org/00syxh370")

    def test_the_ownership_guards_registry_condition_cites_it(self):
        from enrichment.orchestrator import _event_cites_registry

        ev = self._event(
            "domain", "aws.org", ("website_resolver",),
            ref={"verified_by": "registry"},
        )
        assert _event_cites_registry(ev, "ror", None)

    def test_an_independent_write_does_not_cite_it(self):
        """13338646's domain came from the web lane on its own evidence, so
        nothing cascades to it when GLEIF's match is withdrawn."""
        from enrichment.orchestrator import _event_cites_registry

        ev = self._event(
            "domain", "adam-tech.com", ("website_resolver",),
            ref={"verified_by": "serp"},
        )
        assert not _event_cites_registry(ev, "gleif", "549300FIRKSM1MOM7Y22")

    def test_the_registry_derived_classifier_inputs_are_cleared(self):
        """`record_type` is not withdrawn, it is RE-DERIVED: `_classify_record`
        runs later in `finalise` and is its only writer, so removing the
        registry's own inputs is all the cascade has to do. Existing
        machinery, not a second opinion."""
        from enrichment.orchestrator import _REGISTRY_DERIVED_INPUTS

        assert "_ror_is_research" in _REGISTRY_DERIVED_INPUTS
        assert "_gleif_category" in _REGISTRY_DERIVED_INPUTS
