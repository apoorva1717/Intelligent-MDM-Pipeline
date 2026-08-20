"""Department-domain candidate matching, incl. abbreviated subdomains
(chem.ufl.edu for "Department of Chemistry")."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.orchestrator import _seg_matches_needle, _score_dept_candidate


@pytest.mark.parametrize("seg, needle, expected", [
    ("chem", "chemistry", True),    # abbreviation
    ("phys", "physics", True),
    ("math", "mathematics", True),
    ("csail", "cs", True),          # acronym is a substring of the host
    ("bio", "chemistry", False),    # unrelated
    ("sci", "computer", False),
    ("", "chemistry", False),
])
def test_seg_matches_needle(seg, needle, expected):
    assert _seg_matches_needle(seg, needle) is expected


def test_abbreviated_subdomain_scores_positive():
    # chem.ufl.edu for "Department of Chemistry" must score > 0 (was 0 before).
    score = _score_dept_candidate(
        host="chem.ufl.edu", base="ufl.edu", path="/",
        title="Department of Chemistry", tokens={"chemistry"}, acronym=None,
    )
    assert score >= 3


def test_unrelated_subdomain_scores_zero():
    score = _score_dept_candidate(
        host="bio.ufl.edu", base="ufl.edu", path="/",
        title="Department of Biology", tokens={"chemistry"}, acronym=None,
    )
    assert score == 0


# ---------------------------------------------------------------------------
# §5b / §5c path helpers (generic path segments, canonicality penalty)
# ---------------------------------------------------------------------------

from enrichment.orchestrator import (  # noqa: E402
    _path_is_generic,
    _path_canonicality_penalty,
    _score_dept_candidate,
)


class TestPathGenericAndCanonicality:
    def test_generic_path_flagged(self):
        assert _path_is_generic("news-events/events/2008/10/foo") is True
        assert _path_is_generic("news/2026/story") is True
        assert _path_is_generic("departments/chemistry") is False

    def test_canonicality_penalty_orders_landing_over_deep(self):
        landing = _path_canonicality_penalty("departments/chemistry")
        deep = _path_canonicality_penalty(
            "arts-sciences/departments/chemistry/undergrad")
        dated = _path_canonicality_penalty("chemistry/news/2020/event")
        assert landing < deep       # shallow landing page preferred
        assert dated > landing      # dated content penalised

    def test_scorer_no_bonus_for_generic_path(self):
        # A needle in a news/events path earns no path bonus; the same needle in
        # a real department path does. Title held constant to isolate the path.
        generic = _score_dept_candidate(
            "chem.ufl.edu", "ufl.edu", "news-events/chemistry-lecture",
            "", {"chemistry"}, None)
        real = _score_dept_candidate(
            "chem.ufl.edu", "ufl.edu", "chemistry", "", {"chemistry"}, None)
        assert generic == 3          # host match only, no path bonus
        assert real == 4             # host + path bonus


# ---------------------------------------------------------------------------
# §5e/§5f probe base resolution (subdomain-aware + redirect-resolved)
# ---------------------------------------------------------------------------

import pytest as _pytest  # noqa: E402

from config import Settings  # noqa: E402
from enrichment.orchestrator import Orchestrator  # noqa: E402
from utils.cache import BatchCache  # noqa: E402
from tests.mocks.ror_mock import MockRORClient  # noqa: E402
from tests.mocks.lei_mock import MockLEIClient  # noqa: E402
from tests.mocks.page_mock import MockPageFetcher  # noqa: E402


class _RedirectPF(MockPageFetcher):
    def __init__(self, mapping):
        super().__init__()
        self._m = mapping
        self.calls = 0

    async def resolve_final_url(self, url, timeout=5):
        self.calls += 1
        return self._m.get(url, url)


class _NoSearch:
    async def search(self, q, num_results=5):
        return []


def _orch(pf):
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": MockRORClient(st), "lei": MockLEIClient(st),
        "search": _NoSearch(), "page_fetcher": pf,
        "llm": type("L", (), {"extract_json": staticmethod(lambda *a, **k: {}),
                              "aclose": staticmethod(lambda: None)})(),
    })


class TestProbeBaseResolution:
    @_pytest.mark.asyncio
    async def test_redirect_resolves_new_registrable(self):
        # §5f: ROR's dur.ac.uk redirects to durham.ac.uk.
        pf = _RedirectPF({"https://dur.ac.uk": "https://www.durham.ac.uk/"})
        o = _orch(pf)
        base = await o._resolve_probe_base(
            {"website_url": "https://dur.ac.uk", "record_id": "D"},
            "dur.ac.uk", BatchCache())
        assert base == "durham.ac.uk"

    @_pytest.mark.asyncio
    async def test_subdomain_institution_uses_full_host(self):
        # §5e: gc.cuny.edu is a subdomain of cuny.edu → keep the full host.
        pf = _RedirectPF({})
        o = _orch(pf)
        base = await o._resolve_probe_base(
            {"website_url": "https://gc.cuny.edu", "record_id": "C"},
            "cuny.edu", BatchCache())
        assert base == "gc.cuny.edu"

    @_pytest.mark.asyncio
    async def test_www_web_prefix_stripped_to_registrable(self):
        pf = _RedirectPF({})
        o = _orch(pf)
        base = await o._resolve_probe_base(
            {"website_url": "https://web.mit.edu", "record_id": "M"},
            "mit.edu", BatchCache())
        assert base == "mit.edu"

    @_pytest.mark.asyncio
    async def test_result_cached_per_batch(self):
        pf = _RedirectPF({})
        o = _orch(pf)
        cache = BatchCache()
        r = {"website_url": "https://gc.cuny.edu", "record_id": "C"}
        await o._resolve_probe_base(r, "cuny.edu", cache)
        await o._resolve_probe_base(r, "cuny.edu", cache)
        assert pf.calls == 1  # second call served from the per-batch cache


# ---------------------------------------------------------------------------
# Newsroom / administrative subdomains are never a department home
# ---------------------------------------------------------------------------

from enrichment.orchestrator import _host_prefix_is_generic  # noqa: E402
from search.base import SearchResult  # noqa: E402
from search.page_fetcher import PageContent  # noqa: E402


class TestHostPrefixIsGeneric:
    def test_newsroom_subdomains_rejected(self):
        assert _host_prefix_is_generic("news.mit.edu", "mit.edu") is True
        assert _host_prefix_is_generic("events.stanford.edu", "stanford.edu") is True
        assert _host_prefix_is_generic("newsroom.ucla.edu", "ucla.edu") is True

    def test_department_subdomains_kept(self):
        assert _host_prefix_is_generic("chem.ufl.edu", "ufl.edu") is False
        assert _host_prefix_is_generic("eecs.mit.edu", "mit.edu") is False
        assert _host_prefix_is_generic("chemistry.gc.cuny.edu", "gc.cuny.edu") is False

    def test_base_optional_and_registrable_domain_left_alone(self):
        # No base: a 3+-label host is judged on its first label…
        assert _host_prefix_is_generic("news.hopkinsmedicine.org") is True
        # …but a bare registrable domain has no subdomain to judge.
        assert _host_prefix_is_generic("press.org") is False
        assert _host_prefix_is_generic("mit.edu", "mit.edu") is False


class _SerpStub:
    """Returns one SERP hit: an MIT News story about the department."""

    def __init__(self, results):
        self._results = results

    async def search(self, q, num_results=5):
        return list(self._results)


class _StoryPF(MockPageFetcher):
    """Every fetched URL verifies as "Chemistry" — so only the host guard,
    not the content check, can keep a newsroom story out."""

    def __init__(self, pages):
        super().__init__()
        self._pages = pages
        self.fetched: list[str] = []

    async def resolve_final_url(self, url, timeout=5):
        return url

    async def fetch_outgoing_links(self, url, base_domain):
        return []

    async def fetch_page_content(self, url):
        self.fetched.append(url)
        title = self._pages.get(url)
        if title is None:
            return None
        return PageContent(url=url, url_path="", page_title=title, h1="",
                           breadcrumb="", body_text=title)


def _probe_result():
    return {
        "record_id": "R1",
        "routing_type": "research_institution",
        "domain": "mit.edu",
        "website_url": "https://mit.edu",
        "name1_enriched": "Massachusetts Institute of Technology",
        "name2_enriched": "Department of Chemistry",
        "department_domain": None,
    }


class TestNewsroomHostNeverWins:
    @_pytest.mark.asyncio
    async def test_stage_2b_skips_news_story_url(self):
        # The SERP's only on-domain hit is a news story whose path carries the
        # dept token. Accepting it would ship https://news.mit.edu once
        # canonicalise_host drops the path.
        story = "https://news.mit.edu/2026/chemistry-breakthrough"
        pf = _StoryPF({story: "MIT News | Department of Chemistry breakthrough"})
        st = Settings()
        o = Orchestrator(st, mock_clients={
            "ror": MockRORClient(st), "lei": MockLEIClient(st),
            "search": _SerpStub([SearchResult(
                title="Department of Chemistry breakthrough",
                url=story, snippet="")]),
            "page_fetcher": pf,
            "llm": type("L", (), {
                "extract_json": staticmethod(lambda *a, **k: {}),
                "aclose": staticmethod(lambda: None)})(),
        })
        result = _probe_result()
        await o._probe_department_url("R1", result, BatchCache())
        assert result["department_domain"] is None

    @_pytest.mark.asyncio
    async def test_real_department_subdomain_still_wins(self):
        # Same shape, but the hit is the department's own subdomain.
        url = "https://chemistry.mit.edu/about"
        pf = _StoryPF({
            "https://chemistry.mit.edu/": "MIT Department of Chemistry",
            url: "MIT Department of Chemistry",
        })
        st = Settings()
        o = Orchestrator(st, mock_clients={
            "ror": MockRORClient(st), "lei": MockLEIClient(st),
            "search": _SerpStub([SearchResult(
                title="MIT Department of Chemistry", url=url, snippet="")]),
            "page_fetcher": pf,
            "llm": type("L", (), {
                "extract_json": staticmethod(lambda *a, **k: {}),
                "aclose": staticmethod(lambda: None)})(),
        })
        result = _probe_result()
        await o._probe_department_url("R1", result, BatchCache())
        assert result["department_domain"] == "chemistry.mit.edu"

    @_pytest.mark.asyncio
    async def test_stage_0_never_get_probes_a_generic_subdomain(self):
        # name2 "News and Media Relations" would otherwise construct
        # news.mit.edu / media.mit.edu as stage-0 candidates.
        pf = _StoryPF({})
        st = Settings()
        o = Orchestrator(st, mock_clients={
            "ror": MockRORClient(st), "lei": MockLEIClient(st),
            "search": _SerpStub([]), "page_fetcher": pf,
            "llm": type("L", (), {
                "extract_json": staticmethod(lambda *a, **k: {}),
                "aclose": staticmethod(lambda: None)})(),
        })
        result = _probe_result()
        result["name2_enriched"] = "News and Media Relations"
        await o._probe_department_url("R1", result, BatchCache())
        assert result["department_domain"] is None
        assert not any(
            h in u for u in pf.fetched for h in ("news.mit.edu", "media.mit.edu")
        )
