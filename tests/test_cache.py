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
    assert cache.get_serp("q", provider="serpapi") is None
    cache.set_serp("q", _results(), provider="serpapi")
    assert cache.get_serp("q", provider="serpapi")[0].url == "https://x.edu"
    # Key is case/space-insensitive.
    assert cache.get_serp("  Q ", provider="serpapi") is not None


def test_shared_cache_reused_across_batches():
    shared = SerpCache()

    # First batch populates both the batch and the shared cache.
    batch1 = BatchCache(shared_serp=shared)
    batch1.set_serp("q", _results(), provider="serpapi")
    assert shared.size == 1

    # A brand-new batch (empty per-batch cache) still gets the hit from the
    # shared process-level cache — no API call needed.
    batch2 = BatchCache(shared_serp=shared)
    hit = batch2.get_serp("q", provider="serpapi")
    assert hit is not None
    assert isinstance(hit[0], SearchResult)
    assert hit[0].url == "https://x.edu"


def test_no_shared_cache_means_per_batch_only():
    # Without a shared cache, a fresh batch does not see another batch's hits.
    batch1 = BatchCache()
    batch1.set_serp("q", _results(), provider="serpapi")
    batch2 = BatchCache()
    assert batch2.get_serp("q", provider="serpapi") is None


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


# ---------------------------------------------------------------------------
# The provider is part of the key (ticket 20)
#
# The incident: `SERPAPI_KEY` was shadowed by a duplicate placeholder in
# `.env`, every search silently fell back to DuckDuckGo, and 251 empty results
# were recorded under a key with no provider component. Later runs — with the
# key fixed and SerpAPI live — were served those empties as "this organisation
# has no web presence": no network call, no warning, byte-identical to a real
# negative. Same class as `SearchUnavailable` one level up.
# ---------------------------------------------------------------------------

class _StubClient:
    """A search client that records whether it was actually asked."""

    def __init__(self, provider_id: str, results):
        self.provider_id = provider_id
        self._results = results
        self.calls = 0

    async def search(self, query, num_results=5, *, country=None):
        self.calls += 1
        return list(self._results)


class TestProviderIsPartOfTheSerpKey:
    def test_two_providers_do_not_share_one_entry(self):
        cache = BatchCache()
        cache.set_serp("acme labs", ["ddg"], "US", provider="duckduckgo")
        assert cache.get_serp("acme labs", "US", provider="serpapi") is None
        assert cache.get_serp("acme labs", "US", provider="duckduckgo") == ["ddg"]

    def test_disk_keys_differ_by_provider(self):
        from utils.cache import serp_disk_key

        assert serp_disk_key("acme labs", "US", provider="serpapi") != \
            serp_disk_key("acme labs", "US", provider="duckduckgo")

    def test_the_key_shape_is_versioned(self):
        """A key-shape change must invalidate cleanly rather than silently
        reuse entries written under the old shape — those are not
        distinguishable from correct ones by inspection."""
        from utils.cache import SERP_KEY_VERSION, serp_disk_key

        key = serp_disk_key("acme labs", "US", provider="serpapi")
        assert key.startswith(f"{SERP_KEY_VERSION}:")
        assert not key.startswith("serp:"), "v1 shape, which omitted the provider"

    @pytest.mark.asyncio
    async def test_a_fallback_providers_empty_is_not_replayed_to_the_primary(self):
        """The incident, end to end: DuckDuckGo's silence is recorded, then
        SerpAPI asks the same question and must still reach the network."""
        shared = SerpCache()

        ddg = _StubClient("duckduckgo", [])
        assert await cached_serp(BatchCache(shared_serp=shared), ddg,
                                 "acme labs", country="US") == []
        assert ddg.calls == 1

        serpapi = _StubClient("serpapi", _results())
        hit = await cached_serp(BatchCache(shared_serp=shared), serpapi,
                                "acme labs", country="US")
        assert serpapi.calls == 1, "served DuckDuckGo's empty result"
        assert hit[0].url == "https://x.edu"

    @pytest.mark.asyncio
    async def test_the_same_provider_is_still_served_from_cache(self):
        """The fix must not cost the warm-second-run property that
        `tools/run_diff.py` depends on (evidence_network_calls == 0)."""
        shared = SerpCache()
        client = _StubClient("serpapi", _results())

        await cached_serp(BatchCache(shared_serp=shared), client, "acme labs",
                          country="US")
        await cached_serp(BatchCache(shared_serp=shared), client, "acme labs",
                          country="US")
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_an_undeclared_client_does_not_collide_with_another(self):
        """Two doubles that declare no `provider_id` must still be distinct;
        a shared default is the bug this exists to prevent."""
        shared = SerpCache()

        class AlphaClient(_StubClient):
            pass

        class BetaClient(_StubClient):
            pass

        alpha = AlphaClient("", [])
        beta = BetaClient("", _results())
        await cached_serp(BatchCache(shared_serp=shared), alpha, "q")
        await cached_serp(BatchCache(shared_serp=shared), beta, "q")
        assert beta.calls == 1, "served the other undeclared provider's result"
