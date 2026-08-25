"""Fix 3: the page-read corroborator.

The rule the whole module exists to keep is stated once and tested from three
angles: **a page is a witness, never an author.** A fetched page may clear a
doubt, may withdraw a domain the pipeline was wrong to keep, and may supply an
``operating_name`` — and it may never change ``name1_enriched``. The last test
in ``TestNameOneIsNeverTouched`` is the one that matters: a batch through the
corroborator must come out with byte-identical Name 1 values.

The second rule is that not-looking is not evidence. A 403, a bot challenge, a
parked domain and a page that simply does not say who runs it are four ways of
learning nothing, and none of them may be scored as either verdict.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import Orchestrator, _init_result
from enrichment.page_corroborator import (
    CONTRADICTED,
    CORROBORATED,
    FETCH_UNAVAILABLE,
    NAME_MISMATCH,
    NO_IDENTITY,
    PARKED,
    PageStatement,
    compare,
    compare_location,
    fetch_pages,
    operating_name_provenance,
)
from enrichment.provenance import GUARD_PAGE_IDENTITY, deterministic_evidence
from search.page_fetcher import PageContent, PageFetchResult
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.serp_mock import MockSearchClient
from utils.cache import PageCache

THRESHOLD = 88.0


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _Fetcher:
    """Serves canned responses per URL path; records every URL it was asked
    for, so the imprint-probe order can be asserted."""

    def __init__(self, pages: dict[str, PageFetchResult]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    async def fetch_page_result(self, url, timeout=None):
        self.requested.append(url)
        return self.pages.get(url, PageFetchResult(url=url, status=404))


def _page(url: str, title: str = "", h1: str = "", text: str = "") -> PageFetchResult:
    return PageFetchResult(
        url=url, status=200,
        content=PageContent(
            url=url, url_path="", page_title=title, h1=h1,
            breadcrumb="", body_text=text,
        ),
    )


class _Reader:
    """An LLM double that returns one canned page statement."""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = 0

    async def extract_json(self, system, user, **kw):
        self.calls += 1
        return self.payload


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

class TestFetch:
    @pytest.mark.asyncio
    async def test_root_then_the_first_imprint_path_that_answers(self):
        """`/impressum` and `/legal` 404; `/about` answers, and nothing after
        it is requested."""
        fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", "Acme", text="x" * 200),
            "https://acme.com/about": _page(
                "https://acme.com/about", text="Acme Laboratories Inc, Irvine CA",
            ),
            "https://acme.com/contact": _page("https://acme.com/contact", text="c"),
        })
        payload = await fetch_pages("acme.com", fetcher, PageCache())

        assert payload["paths"] == ["/", "/about"]
        assert "Acme Laboratories Inc" in payload["text"]
        assert "https://acme.com/contact" not in fetcher.requested

    @pytest.mark.asyncio
    async def test_an_unreachable_root_stops_there(self):
        fetcher = _Fetcher({})
        payload = await fetch_pages("nowhere.example", fetcher, PageCache())
        assert payload["paths"] == []
        assert fetcher.requested == ["https://nowhere.example/"]

    @pytest.mark.asyncio
    async def test_one_fetch_per_domain_across_the_batch(self):
        """The cache is keyed on the domain, so a second record naming the same
        organisation costs nothing."""
        cache = PageCache()
        fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="x" * 200),
        })
        await fetch_pages("acme.com", fetcher, cache)
        before = len(fetcher.requested)
        await fetch_pages("acme.com", fetcher, cache)
        assert len(fetcher.requested) == before

    @pytest.mark.asyncio
    async def test_a_disk_fixture_reproduces_the_read_in_a_new_process(self, tmp_path):
        fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="Acme Inc" * 40),
        })
        await fetch_pages("acme.com", fetcher, PageCache(tmp_path))

        # A fresh cache, and a fetcher that would answer differently.
        cold = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="SOMETHING ELSE"),
        })
        payload = await fetch_pages("acme.com", cold, PageCache(tmp_path))
        assert "Acme Inc" in payload["text"]
        assert payload["from_fixture"] is True
        assert cold.requested == []

    @pytest.mark.asyncio
    async def test_replay_only_reports_a_miss_instead_of_fetching(self, tmp_path):
        fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="x" * 200),
        })
        payload = await fetch_pages(
            "acme.com", fetcher, PageCache(tmp_path, replay_only=True),
        )
        assert payload["error"] == "replay_only_miss"
        assert fetcher.requested == []


# ---------------------------------------------------------------------------
# Learning nothing
# ---------------------------------------------------------------------------

class TestNotLookingIsNotEvidence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 429, 451, 500, 404])
    async def test_a_refused_or_broken_fetch_is_fetch_unavailable(self, status):
        orch, result, record = _record_with_candidate("acme.com")
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": PageFetchResult(url="https://acme.com/", status=status),
        })
        await orch._corroborate_domain(record, result)
        assert result["_page_corroboration"]["outcome"] == FETCH_UNAVAILABLE
        assert result["domain"] == "acme.com"          # untouched
        assert result.get("operating_name") is None

    @pytest.mark.asyncio
    async def test_a_bot_challenge_that_answers_200_is_still_unavailable(self):
        orch, result, record = _record_with_candidate("acme.com")
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": _page(
                "https://acme.com/", title="Just a moment...",
                text="Checking your browser before accessing acme.com. " * 8,
            ),
        })
        await orch._corroborate_domain(record, result)
        assert result["_page_corroboration"]["outcome"] == FETCH_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_a_parked_domain_is_not_a_name_mismatch(self):
        """A for-sale placeholder names nobody. Scoring it as "the page names
        someone else" would withdraw a domain on no evidence at all."""
        orch, result, record = _record_with_candidate("acme.com")
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": _page(
                "https://acme.com/", title="acme.com",
                text="This domain is for sale. Buy this domain. " * 8,
            ),
        })
        await orch._corroborate_domain(record, result)
        assert result["_page_corroboration"]["outcome"] == PARKED
        assert result["domain"] == "acme.com"

    @pytest.mark.asyncio
    async def test_a_page_that_states_no_identity_is_not_evidence(self):
        orch, result, record = _record_with_candidate("acme.com")
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="Welcome. " * 40),
        })
        orch._llm_client = _Reader({"stated_org_name": None, "legal_form_present": False})
        await orch._corroborate_domain(record, result)
        assert result["_page_corroboration"]["outcome"] == NO_IDENTITY
        assert result["domain"] == "acme.com"


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class TestLocation:
    def test_absence_of_an_address_is_neutral_not_a_rejection(self):
        state, detail, scope = compare_location(
            PageStatement(stated_org_name="Acme Inc"),
            city="Irvine", region="CA", country="US", postal_code="92618",
        )
        assert state == "neutral"
        assert detail is None
        assert scope is None

    def test_a_different_stated_city_is_a_contradiction(self):
        state, detail, scope = compare_location(
            PageStatement(stated_org_name="X", stated_city="Milwaukee"),
            city="Irvine", region="CA", country="US", postal_code=None,
        )
        assert state == "contradicted"
        assert "Milwaukee" in detail
        assert scope == "city"

    def test_a_different_state_contradicts_at_region_scope(self):
        """Scope is what separates a plant-and-head-office difference from a
        genuinely different organisation — only region and country justify a
        withdrawal."""
        state, _, scope = compare_location(
            PageStatement(
                stated_org_name="X", stated_city="Milwaukee", stated_region="WI",
            ),
            city="Irvine", region="CA", country="US", postal_code=None,
        )
        assert (state, scope) == ("contradicted", "region")

    def test_a_state_abbreviation_matches_its_full_name(self):
        """`San Francisco, California` on a page and `San Francisco, CA` on the
        record are the same place. Measured: without this the chemspeed batch
        reported a false contradiction on Anresco Laboratories."""
        state, _, _ = compare_location(
            PageStatement(
                stated_org_name="Anresco Laboratories",
                stated_city="San Francisco", stated_region="California",
            ),
            city="San Francisco", region="CA", country="US", postal_code=None,
        )
        assert state == "consistent"

    def test_matching_postal_code_is_consistent_even_with_no_city(self):
        state, _, scope = compare_location(
            PageStatement(stated_org_name="X", stated_postal_code="92618-1234"),
            city=None, region=None, country="US", postal_code="92618",
        )
        assert (state, scope) == ("consistent", "postal")

    def test_a_matching_country_alone_is_too_coarse_to_corroborate(self):
        """Every candidate in a US batch states "United States" somewhere."""
        state, _, _ = compare_location(
            PageStatement(stated_org_name="X", stated_country="US"),
            city="Irvine", region="CA", country="US", postal_code=None,
        )
        assert state == "neutral"


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

class TestCompare:
    def test_legal_form_differences_do_not_break_the_name_match(self):
        """`_name_match_score` — GLEIF's own scorer — takes the max of the raw
        ratio and the legal-form-stripped one, so this is the existing
        machinery, not a new tolerance."""
        outcome, score, *_ = compare(
            PageStatement(stated_org_name="Aixelo, Inc."),
            name1="Aixelo", threshold=THRESHOLD,
        )
        assert outcome == CORROBORATED
        assert score >= THRESHOLD

    def test_a_different_company_is_a_name_mismatch(self):
        outcome, score, *_ = compare(
            PageStatement(stated_org_name="Johnson Controls International plc"),
            name1="AB Controls, Inc.", threshold=THRESHOLD,
        )
        assert outcome == NAME_MISMATCH
        assert score < THRESHOLD

    def test_right_name_wrong_place_is_contradicted_not_mismatched(self):
        outcome, _, location, *_ = compare(
            PageStatement(stated_org_name="Acme Inc", stated_city="Boston"),
            name1="Acme Inc", city="Irvine", region="CA", threshold=THRESHOLD,
        )
        assert outcome == CONTRADICTED
        assert location == "contradicted"

    def test_location_is_computed_even_when_the_name_does_not_match(self):
        """The withdrawal rule needs both answers, so the name check must not
        short-circuit the location one."""
        outcome, _, location, _, scope = compare(
            PageStatement(
                stated_org_name="Apollo Olive Oil",
                stated_region="Northern California",
            ),
            name1="Apollo Organic Synthesis", city="Valley Cottage",
            region="NY", threshold=THRESHOLD,
        )
        assert outcome == NAME_MISMATCH
        assert (location, scope) == ("contradicted", "region")


# ---------------------------------------------------------------------------
# Consequences on the record
# ---------------------------------------------------------------------------

def _orch(**over) -> Orchestrator:
    settings = Settings()
    object.__setattr__(settings, "page_fixture_dir", "")
    clients = {
        "ror": MockRORClient(settings), "lei": MockLEIClient(settings),
        "search": MockSearchClient(), "llm": MockOpenAIClient(),
    }
    clients.update(over)
    return Orchestrator(settings, mock_clients=clients)


def _record_with_candidate(
    domain: str, *, accepted: bool = True, name1: str = "Acme Inc",
):
    orch = _orch()
    record = EnrichmentRecord(
        record_id="R1", name1=name1, country="US", city="Irvine", state="CA",
    )
    result = _init_result(record)
    result.write(
        "name1_enriched", name1,
        deterministic_evidence("test", producer="input", tier=1),
    )
    if accepted:
        result.write(
            "domain", domain,
            deterministic_evidence("test", producer="website_resolver"),
        )
        result["domain_verified_by"] = "name"
    else:
        result["_domain_unverified"] = domain
        result["domain_rejected"] = True
    return orch, result, record


class TestConsequences:
    @pytest.mark.asyncio
    async def test_a_corroborated_unverified_domain_loses_its_flag(self):
        orch, result, record = _record_with_candidate("acme.com", accepted=False)
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="Acme " * 60),
        })
        orch._llm_client = _Reader({
            "stated_org_name": "Acme, Inc.", "stated_city": "Irvine",
            "stated_region": "CA", "legal_form_present": True,
        })
        await orch._corroborate_domain(record, result)

        assert result["_page_corroboration"]["outcome"] == CORROBORATED
        assert "_domain_unverified" not in result
        assert result["domain_rejected"] is False
        assert result["operating_name"] == "Acme, Inc."
        assert result["operating_name_provenance"] == "web:acme.com:provisional"

    @pytest.mark.asyncio
    async def test_an_accepted_wrong_entity_domain_is_withdrawn(self):
        """The AB Controls case: the page names a different organisation AND
        places it in another state, so the accepted domain reverts to empty
        and is flagged."""
        orch, result, record = _record_with_candidate(
            "johnsoncontrols.com", name1="AB Controls, Inc.",
        )
        orch._page_fetcher = _Fetcher({
            "https://johnsoncontrols.com/": _page(
                "https://johnsoncontrols.com/", text="Johnson Controls " * 40,
            ),
        })
        orch._llm_client = _Reader({
            "stated_org_name": "Johnson Controls International plc",
            "stated_city": "Milwaukee", "stated_region": "WI",
            "legal_form_present": True,
        })
        await orch._corroborate_domain(record, result)

        assert result["_page_corroboration"]["outcome"] == NAME_MISMATCH
        assert result["domain"] is None
        assert result["website_url"] is None
        assert result["domain_rejected"] is True
        assert result["_domain_unverified"] == "johnsoncontrols.com"
        assert "Johnson Controls" in result["_domain_page_note"]
        assert result.get("operating_name") is None

        rejection = [
            r for r in result.provenance.rejections
            if r.guard == GUARD_PAGE_IDENTITY
        ]
        assert rejection and rejection[0].candidate == "johnsoncontrols.com"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name1", "stated", "city", "region"),
        [
            # Every one of these withdrew a CORRECT domain when the rule was
            # "name score below threshold". They are brand-vs-legal-name
            # variants, and `token_sort_ratio` is length-sensitive by design.
            ("AquaPhoenix Scientific, Inc.", "AquaPhoenix", "Hanover", "Pennsylvania"),
            ("Analytical Sales", "Analytical Sales and Services, Inc.",
             "Flanders", "New Jersey"),
            ("Applied Catalysts", "Applied Catalysts + Technologies, LLC (AC+T)",
             None, None),
            # Same state, different city: a plant and a head office.
            ("Armor Industrial", "Armor Industrial Fabricators, Inc",
             "Houston", "TX"),
        ],
    )
    async def test_a_name_difference_alone_never_withdraws(
        self, name1, stated, city, region,
    ):
        orch, result, record = _record_with_candidate("acme.com", name1=name1)
        # The record's own place, matching the batch these rows came from.
        record = EnrichmentRecord(
            record_id="R1", name1=name1, country="US",
            city=city or "Hanover", state=region or "PA",
        )
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="x " * 200),
        })
        orch._llm_client = _Reader({
            "stated_org_name": stated, "stated_city": city,
            "stated_region": region, "legal_form_present": True,
        })
        await orch._corroborate_domain(record, result)

        assert result["_page_corroboration"]["outcome"] == NAME_MISMATCH
        # Reported, not acted on.
        assert result["domain"] == "acme.com"
        assert orch._page_counts["withdrawn"] == 0
        assert orch._page_counts["mismatch_not_withdrawn"] == 1
        assert stated in result["_domain_page_note"]

    @pytest.mark.asyncio
    async def test_the_withdrawal_note_reaches_the_flag_reason(self):
        from enrichment.flags import DOMAIN_UNVERIFIED, compute_flags

        result = {
            "record_id": "R1", "flag_codes": [],
            "_domain_unverified": "johnsoncontrols.com",
            "_domain_page_note": "its page states 'Johnson Controls' in Milwaukee",
        }
        compute_flags(result)
        assert DOMAIN_UNVERIFIED in result["flag_codes"]
        # Appended, not overwritten: the original wording survives in full.
        assert "confirm johnsoncontrols.com before using it" in result["flag_reason"]
        assert result["flag_reason"].endswith(
            "— its page states 'Johnson Controls' in Milwaukee"
        )

    def test_a_corroborated_page_is_not_no_match(self):
        """`no-match` says "no source could identify this organisation". A page
        read that returned the organisation's own stated identity is such a
        source, whatever else the record does or does not hold."""
        from enrichment.flags import NO_MATCH, compute_flags

        result = {
            "record_id": "R1", "flag_codes": [],
            "operating_name": "American Art Clay Company, Inc.",
            "operating_name_provenance": "web:amaco.com:extracted:2026-08-22",
        }
        compute_flags(result)
        assert NO_MATCH not in result["flag_codes"]

    @pytest.mark.asyncio
    async def test_a_registry_resolved_record_is_never_re_read(self):
        orch, result, record = _record_with_candidate("mit.edu")
        result.write(
            "ror_id", "https://ror.org/042nb2s44",
            deterministic_evidence("test", producer="ror", tier=1),
        )
        orch._page_fetcher = _Fetcher({})
        await orch._corroborate_domain(record, result)
        assert "_page_corroboration" not in result
        assert orch._page_counts["attempted"] == 0

    @pytest.mark.asyncio
    async def test_the_step_does_nothing_when_disabled(self):
        orch, result, record = _record_with_candidate("acme.com")
        object.__setattr__(orch._settings, "page_corroboration_enabled", False)
        orch._page_fetcher = _Fetcher({})
        await orch._corroborate_domain(record, result)
        assert "_page_corroboration" not in result


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

class TestNameOneIsNeverTouched:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "statement",
        [
            {"stated_org_name": "Acme, Inc.", "stated_city": "Irvine",
             "legal_form_present": True},
            {"stated_org_name": "Johnson Controls International plc",
             "stated_city": "Milwaukee", "legal_form_present": True},
            {"stated_org_name": "Acme Inc", "stated_city": "Boston",
             "legal_form_present": True},
            {"stated_org_name": None, "legal_form_present": False},
        ],
    )
    async def test_no_outcome_writes_name1(self, statement):
        orch, result, record = _record_with_candidate("acme.com")
        before = result["name1_enriched"]
        orch._page_fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="Acme " * 60),
        })
        orch._llm_client = _Reader(statement)
        await orch._corroborate_domain(record, result)
        assert result["name1_enriched"] == before

    @pytest.mark.asyncio
    async def test_a_batch_through_the_corroborator_keeps_name1_byte_identical(self):
        """The acceptance criterion, stated as a test: with the retry feed off,
        every Name 1 in a batch is byte-identical with the corroborator on and
        with it off."""
        records = [
            EnrichmentRecord(record_id="R1", name1="MIT", country="US"),
            EnrichmentRecord(
                record_id="R2", name1="Acme Widgets", country="US", city="Irvine",
            ),
            EnrichmentRecord(
                record_id="R3", name1="Belharra Therapeutics, Inc.", country="US",
            ),
        ]
        options = EnrichmentOptions(max_concurrency=1)

        off = _orch()
        object.__setattr__(off._settings, "page_corroboration_enabled", False)
        baseline = await off.enrich_batch(records, options)

        on = _orch()
        object.__setattr__(on._settings, "page_extract_feeds_retry", False)
        on._page_fetcher = _Fetcher({
            "https://acme.com/": _page("https://acme.com/", text="Acme " * 60),
        })
        after = await on.enrich_batch(records, options)

        assert [r.name1_enriched for r in after.results] == [
            r.name1_enriched for r in baseline.results
        ]


class TestProvenanceString:
    def test_the_shape_is_web_domain_provisional(self):
        """Provenance Scheme B: `source:confidence`, and the source of a name
        read off a page is the page's domain.

        `extracted` was a METHOD in a slot that a reader takes for a
        confidence, and the date decayed. `provisional` is the substantive
        claim, and it is deliberately not `verified`: the page and the domain
        it was served from are ONE evidence system (hard rule 4), so a site
        naming itself corroborates nothing independent.
        """
        assert operating_name_provenance("acme.com") == (
            "web:acme.com:provisional"
        )

    def test_it_is_in_the_grammar(self):
        """This column is written directly rather than derived from a
        provenance event, so it is the one that could drift out of the scheme
        without anything noticing. The finalisation assertion covers it — see
        `enrichment.orchestrator.PROVENANCE_COLUMNS` — and so does this."""
        from enrichment.confidence import validate

        validate(operating_name_provenance("acme.com"))
