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
import time  # noqa: E402
from enrichment.orchestrator import finalise  # noqa: E402
from tests.conftest import make_record  # noqa: E402


class _RedirectPF(MockPageFetcher):
    def __init__(self, mapping):
        super().__init__()
        self._m = mapping
        self.calls = 0

    async def resolve_final_url(self, url, timeout=5):
        self.calls += 1
        return self._m.get(url, url)


class _NoSearch:
    async def search(self, q, num_results=5, *, country=None):
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

    async def search(self, q, num_results=5, *, country=None):
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


# ---------------------------------------------------------------------------
# A department domain that reduces to the organisation's own domain
# ---------------------------------------------------------------------------

class TestNeverDuplicatesTheOrgDomain:
    """`department_domain` must never ship the Domain column verbatim.

    Stage 2b stores the FULL URL of a path-hosted department page, and
    `finalise` then strips the path (`canonicalise_host`). When that page sits
    on the institution's own registrable domain — "boston.gov/departments/
    city-clerk", "nasa.gov/ames/space-biosciences" — the stripped value is the
    institution domain again: it names the unit not at all, and its mere
    presence corroborates Name 2 out of `unverified-inference`.
    """

    @_pytest.mark.asyncio
    async def test_stage_2b_skips_a_page_on_the_base_domain(self):
        url = "https://mit.edu/departments/chemistry"
        pf = _StoryPF({url: "MIT Department of Chemistry"})
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
        assert result["department_domain"] is None

    @_pytest.mark.asyncio
    async def test_stage_2b_skips_a_path_on_a_host_that_names_no_unit(self):
        # §5i. clas.mit.edu/chemistry stays distinct from mit.edu once the
        # path is stripped — but what is left names the College of Liberal
        # Arts, not the Department of Chemistry. Distinct from the Domain
        # column is not the same as naming the unit.
        url = "https://clas.mit.edu/chemistry"
        pf = _StoryPF({
            url: "MIT Department of Chemistry",
            "https://clas.mit.edu/": "MIT College of Liberal Arts",
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
        assert result["department_domain"] is None
        # …and no verification fetch was spent on it.
        assert url not in pf.fetched

    @_pytest.mark.parametrize("stored", [
        "https://nasa.gov/ames/space-biosciences",
        "https://www.nasa.gov/",
        "nasa.gov",
    ])
    def test_finalise_drops_a_value_that_canonicalises_to_the_domain(self, stored):
        # Emit-point guard: whatever route wrote it, a value whose host is the
        # organisation domain is dropped before flags and search terms read it.
        out = finalise(make_record(
            record_id="R1",
            routing_type="research_institution",
            record_type="research_institution",
            domain="nasa.gov",
            name1_enriched="NASA Ames Research Center",
            name2_enriched="Space Biosciences Research",
            department_domain=stored,
        ), time.monotonic())
        assert out["department_domain"] is None
        # …and search_term_2 no longer falls back to the institution handle:
        # with the duplicate in place the department column read "NASA", the
        # same value search_term_1 already carries.
        assert out["search_term_1"] == "NASA"
        assert out["search_term_2"] != "NASA"

    def test_finalise_keeps_a_real_department_subdomain(self):
        out = finalise(make_record(
            record_id="R2",
            routing_type="research_institution",
            record_type="research_institution",
            domain="mit.edu",
            name1_enriched="Massachusetts Institute of Technology",
            name2_enriched="Department of Chemistry",
            department_domain="chemistry.mit.edu",
        ), time.monotonic())
        assert out["department_domain"] == "https://chemistry.mit.edu"


# ---------------------------------------------------------------------------
# §5i — the host that ships must name the unit
# ---------------------------------------------------------------------------

from enrichment.orchestrator import (  # noqa: E402
    _dept_needles,
    _host_names_the_unit,
    _is_third_party_host,
)


class TestHostNamesTheUnit:
    """`department_domain` ships as a bare host, so the host is the whole of
    what the column says. Stages 2b and 3 accept a candidate on evidence that
    does NOT survive into the column — the unit named in a URL *path*, or on
    the page body — which is how an institution's cross-cutting services, a
    parent brand and a third-party directory all came to be shipped as
    department domains. Each observed value below is a real row from the
    university / hospital eval batches.
    """

    @pytest.mark.parametrize("host, name2", [
        # Cross-cutting services: one page per department, hosted by the
        # service. The page verifies; the host names the service.
        ("digitalcommons.pvamu.edu", "Department of Chemistry and Physics"),
        ("digitalcommons.usf.edu",
         "Department of Chemical & Biomedical Engineering"),
        ("catalog.smu.edu", "Department of Biological Sciences"),
        ("libguides.csun.edu", "Department of Chemistry and Biochemistry"),
        ("facultyhonors.umich.edu", "Institute of Howard Hughes Medicine"),
        # An umbrella school / college: a real unit, but not this one.
        ("pll.harvard.edu", "Department of Chemistry & Biochemistry"),
        ("sph.umich.edu", "Institute of Life Sciences"),
        ("artsci.uc.edu", "Department of Chemistry"),
        ("science-math.wright.edu", "Department of Chemistry"),
        # A parent brand's homepage — reached through the §5f redirect
        # (brighamandwomens.org → massgeneralbrigham.org), so it is not the
        # record's own Domain and §5h never saw it.
        ("massgeneralbrigham.org",
         "Department of Rheumatology, Immunology and Allergy"),
        ("idfellowship.massgeneralbrigham.org", "Global Health"),
        ("research.massgeneralbrigham.org",
         "Massachusetts Eye and Ear Infirmary"),
        # A third-party directory that outranks the institution itself.
        ("bigfuture.collegeboard.org", "Department of Physics"),
    ])
    def test_host_that_names_no_unit_is_refused(self, host, name2):
        assert _host_names_the_unit(
            host, "example.edu", _dept_needles(name2),
        ) is False

    @pytest.mark.parametrize("host, name2", [
        ("engineering.ucdavis.edu", "College of Engineering"),
        ("npb.ucdavis.edu",
         "Department of Neurobiology, Physiology and Behavior"),
        ("nanoengineering.ucsd.edu", "Department of NanoEngineering"),
        ("mse.mtu.edu", "Department of Materials Science and Engineering"),
        ("chemical.uml.edu", "Department of Chemical Engineering"),
        ("transportation.tamu.edu", "Institute of Texas A&M Transportation"),
        ("medicine.osu.edu", "College of Medicine"),
        ("artsandsciences.osu.edu", "College of Arts and Sciences"),
        ("me.mit.edu", "Department of Mechanical Engineering"),
        ("phoenix.ucdavis.edu", "Phoenix Cluster"),
        # Two labels below the registrable domain — either may name the unit.
        ("heb.fas.harvard.edu", "Department of Human Evolutionary Biology"),
        # §5e: the institution is itself a subdomain.
        ("chemistry.gc.cuny.edu", "Department of Chemistry"),
        # The acronym keeps its ampersand ("S&M"); the host does not.
        ("csm.rowan.edu", "College of Science & Mathematics"),
        # A compound label is matched by its parts too.
        ("rad-onc.medschool.umich.edu", "Department of Radiation Oncology"),
    ])
    def test_host_that_names_the_unit_is_kept(self, host, name2):
        assert _host_names_the_unit(
            host, "example.edu", _dept_needles(name2),
        ) is True

    def test_a_units_own_registrable_domain_names_it(self):
        # A unit with a domain of its own names itself in the registrable
        # label — but only when that domain is not the organisation's.
        needles = _dept_needles("Harvard Business School")
        assert _host_names_the_unit("hbs.edu", "harvard.edu", needles) is True
        assert _host_names_the_unit("hbs.edu", "hbs.edu", needles) is False

    def test_no_name2_means_nothing_to_match(self):
        assert _dept_needles(None) == set()
        assert _host_names_the_unit("chem.mit.edu", "mit.edu", set()) is False


class TestThirdPartyPlatformHosts:
    """A unit's account on a site builder carries the unit's name in the
    SUBDOMAIN, so §5i alone would accept it — the registrable domain is what
    disqualifies it. Observed: nationwidechildrenshospital.tumblr.com shipped
    as the department domain for the Research Institute at Nationwide
    Children's Hospital."""

    @pytest.mark.parametrize("host", [
        "nationwidechildrenshospital.tumblr.com",
        "chemistry.wordpress.com",
        "physicsdept.blogspot.com",
        "bigfuture.collegeboard.org",
        "www.usnews.com",
    ])
    def test_platform_and_directory_hosts_refused(self, host):
        assert _is_third_party_host(host) is True

    @pytest.mark.parametrize("host", [
        "chem.ufl.edu", "eecs.mit.edu", "hopkinsmedicine.org",
    ])
    def test_institution_hosts_untouched(self, host):
        assert _is_third_party_host(host) is False


class TestFinaliseRefusesAHostThatNamesNoUnit:
    """The emit-point gate — whatever route wrote the value."""

    @pytest.mark.parametrize("stored", [
        "https://digitalcommons.mit.edu/chemistry_collection",
        "catalog.mit.edu",
        "https://libguides.mit.edu/chemistry",
        "https://chemistry.tumblr.com",
        "https://clas.mit.edu/chemistry",
        "https://facultyhonors.mit.edu/2026/chemistry-prize",
    ])
    def test_dropped_before_flags_and_search_terms_read_it(self, stored):
        name2 = "Department of Chemistry"
        out = finalise(make_record(
            record_id="R1",
            routing_type="research_institution",
            record_type="research_institution",
            domain="mit.edu",
            name1_enriched="Massachusetts Institute of Technology",
            name2_enriched=name2,
            department_domain=stored,
        ), time.monotonic())
        assert out["department_domain"] is None
        # The value is not inert: a non-null department_domain corroborates
        # Name 2 out of `unverified-inference` and feeds search_term_2.
        assert out["search_term_2"] == "CHEMISTRY"

    def test_a_real_department_subdomain_still_ships(self):
        out = finalise(make_record(
            record_id="R2",
            routing_type="research_institution",
            record_type="research_institution",
            domain="usf.edu",
            name1_enriched="University of South Florida",
            name2_enriched="Department of Chemical & Biomedical Engineering",
            department_domain="https://cbe.usf.edu/undergraduate",
        ), time.monotonic())
        assert out["department_domain"] == "https://cbe.usf.edu"


class TestAdminWordThatAlsoNamesAUnit:
    """§5g is a hard veto and §5i is per-record. A word that is a service host
    at one institution and a real unit at another must be judged by §5i — so
    it is deliberately absent from `_GENERIC_HOST_PREFIXES`."""

    @pytest.mark.parametrize("host, name2, domain", [
        ("finance.wharton.upenn.edu", "Department of Finance", "upenn.edu"),
        ("athletics.osu.edu", "Department of Athletics", "osu.edu"),
        ("housing.boston.gov", "Department of Housing", "boston.gov"),
        ("honors.psu.edu", "Schreyer Honors College", "psu.edu"),
        ("extension.psu.edu", "Cooperative Extension", "psu.edu"),
    ])
    def test_the_unit_it_names_still_claims_it(self, host, name2, domain):
        from enrichment.orchestrator import _dept_host_is_admissible
        assert _dept_host_is_admissible(
            host, domain, domain, _dept_needles(name2),
        ) is True

    @pytest.mark.parametrize("host", [
        "finance.smu.edu", "athletics.smu.edu", "catalog.smu.edu",
    ])
    def test_every_other_record_is_refused(self, host):
        from enrichment.orchestrator import _dept_host_is_admissible
        assert _dept_host_is_admissible(
            host, "smu.edu", "smu.edu",
            _dept_needles("Department of Biological Sciences"),
        ) is False
