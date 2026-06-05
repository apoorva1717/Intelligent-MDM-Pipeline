"""Tests for BatchCache and the shared in-memory SERP cache."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from search.base import SearchResult
from utils.cache import BatchCache, SerpCache


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
