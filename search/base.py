"""Abstract search interface for SERP providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class SearchUnavailable(RuntimeError):
    """The search could not be executed — a transport failure, not an answer.

    A provider that swallows a reset connection and returns ``[]`` is telling
    its caller "there are no results for this query", which is a claim about
    the world. Fix B made that claim durable: `utils.cache.cached_serp`
    RECORDS what a search returned, so one dropped TLS connection would have
    been cached as "this organisation has no web presence" for every later
    run. The two outcomes are now distinguishable, and only a real answer is
    recorded.

    Callers already treat a failed search as "no candidates"; `cached_serp`
    preserves that by returning ``[]`` after declining to record.
    """


@dataclass
class SearchResult:
    """One organic search result."""
    title: str
    url: str
    snippet: str


class SearchClient(ABC):
    """Abstract base for web search providers."""

    @abstractmethod
    async def search(self, query: str, num_results: int = 5) -> list[SearchResult]:
        """Execute a web search and return organic results."""
        ...
