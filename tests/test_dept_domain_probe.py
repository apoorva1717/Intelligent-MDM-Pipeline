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
