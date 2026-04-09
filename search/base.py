"""Abstract search interface for SERP providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


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
