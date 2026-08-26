"""Tests for BatchCache and the shared in-memory SERP cache."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from search.base import SearchResult
from utils.cache import BatchCache, SerpCache, cached_serp


def _results():
    return [SearchResult(title="T", url="https://x.edu", snippet="S")]


def test_batch_cache_serp_roundtrip_in_memory():
    cache = BatchCache()
    assert cache.get_serp("q") is None
    cache.set_serp("q", _results())
    assert cache.get_serp("q")[0].url == "https://x.edu"
    # Key is case/space-insensitive.
    assert cache.get_serp("  Q ") is not None


def test_shared_cache_reused_across_batches():
    shared = SerpCache()

    # First batch populates both the batch and the shared cache.
    batch1 = BatchCache(shared_serp=shared)
    batch1.set_serp("q", _results())
    assert shared.size == 1

    # A brand-new batch (empty per-batch cache) still gets the hit from the
    # shared process-level cache — no API call needed.
    batch2 = BatchCache(shared_serp=shared)
    hit = batch2.get_serp("q")
    assert hit is not None
    assert isinstance(hit[0], SearchResult)
    assert hit[0].url == "https://x.edu"


def test_no_shared_cache_means_per_batch_only():
    # Without a shared cache, a fresh batch does not see another batch's hits.
    batch1 = BatchCache()
    batch1.set_serp("q", _results())
    batch2 = BatchCache()
    assert batch2.get_serp("q") is None


# ---------------------------------------------------------------------------
# The record's country reaches the PROVIDER, not just the cache key
# ---------------------------------------------------------------------------

class _RecordingSearch:
    """Captures the kwargs `cached_serp` hands the provider."""

    def __init__(self):
        self.calls: list[dict] = []

    async def search(self, query, num_results=5, *, country=None):
        self.calls.append(
            {"query": query, "num_results": num_results, "country": country},
        )
        return [SearchResult(title="t", url="https://example.com", snippet="")]


class TestSerpCountryReachesTheProvider:
    """`country` used to key the cache without ever entering the request, so
    two records in different countries were filed under different keys for a
    search that had been issued identically. The key promised a distinction the
    request never made."""

    @pytest.mark.asyncio
    async def test_country_is_passed_through(self):
        client = _RecordingSearch()
        await cached_serp(None, client, "acme official website", country="US")
        assert client.calls[0]["country"] == "US"

    @pytest.mark.asyncio
    async def test_country_is_normalised_before_the_provider_sees_it(self):
        # Providers never receive a raw "United States" / "USA" / "UK" — the
        # normalisation happens once here rather than in each client.
        client = _RecordingSearch()
        await cached_serp(None, client, "q", country="United States")
        await cached_serp(None, client, "q2", country="UK")
        assert [c["country"] for c in client.calls] == ["US", "GB"]

    @pytest.mark.asyncio
    async def test_unreadable_country_arrives_as_none_not_as_a_guess(self):
        client = _RecordingSearch()
        await cached_serp(None, client, "q", country="Ruritania")
        assert client.calls[0]["country"] is None

    @pytest.mark.asyncio
    async def test_no_country_lane_is_unaffected(self):
        client = _RecordingSearch()
        await cached_serp(None, client, "q")
        assert client.calls[0]["country"] is None


class TestSerpAPIGeoParam:
    """The `gl` parameter is what makes the country do anything at Google."""

    def _params(self, country):
        captured = {}

        class _FakeGoogleSearch:
            def __init__(self, params):
                captured.update(params)

            def get_dict(self):
                return {"organic_results": []}

        import search.serpapi_client as mod

        real = mod.GoogleSearch
        mod.GoogleSearch = _FakeGoogleSearch
        try:
            mod.SerpAPIClient("k")._sync_search("q", 5, country)
        finally:
            mod.GoogleSearch = real
        return captured

    def test_gl_is_set_from_the_country(self):
        assert self._params("US")["gl"] == "us"

    def test_gl_is_absent_without_a_country(self):
        assert "gl" not in self._params(None)

    def test_malformed_code_is_not_forwarded(self):
        # `cached_serp` normalises, so anything else arriving here is not a
        # country code and must not be passed off as one.
        assert "gl" not in self._params("United States")

    def test_hl_is_never_guessed(self):
        # A country does not determine a language — Belgium has three official
        # ones — so narrowing on it would invent a fact the record never stated.
        assert "hl" not in self._params("BE")
