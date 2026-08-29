"""Abstract search interface for SERP providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


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

    #: Stable identity of the provider, and part of the cache key for every
    #: search it answers (`utils.cache.serp_key`). Without it a SerpAPI query
    #: and a DuckDuckGo query for the same string collide on one entry, and one
    #: provider's silence replays as the other's answer — which is exactly what
    #: happened: 251 empty DuckDuckGo results, recorded while SERPAPI_KEY was
    #: shadowed by a duplicate in `.env`, were served to later runs as "this
    #: organisation has no web presence", with no network call and no warning.
    #: Same bug class as :class:`SearchUnavailable` one level up: there,
    #: *provider failed* must not become *no results*; here, *a different
    #: provider answered* must not become *this provider found nothing*.
    #:
    #: Subclasses MUST set it. It is baked into durable cache keys, so changing
    #: an existing value silently orphans every entry that provider wrote.
    provider_id: ClassVar[str] = ""

    @abstractmethod
    async def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        country: str | None = None,
    ) -> list[SearchResult]:
        """Execute a web search and return organic results.

        *country* is the record's country as an ISO 3166-1 alpha-2 code,
        already normalised by `utils.cache.cached_serp` — providers never see a
        raw "United States" / "USA" / blank. It asks the provider to rank
        results FOR that country; a provider with no such control ignores it.

        It is a ranking hint, not a filter, and no caller may treat it as one:
        the country a returned domain actually belongs to is decided
        downstream by `utils.domain_resolver.country_conflict`, which does not
        depend on any provider honouring this.
        """
        ...


def provider_id_of(client: Any) -> str:
    """The cache-key identity of *client*.

    Prefers the declared :attr:`SearchClient.provider_id`. A client that does
    not declare one — a test double, a lane that was handed something
    duck-typed — is derived from its class name rather than defaulted to a
    shared constant: two undeclared providers must not collide with each other
    either, and a silent shared default is the bug this exists to prevent.

    Pure function of the client's type, so it cannot make a cache key depend on
    anything but the request.
    """
    declared = getattr(client, "provider_id", "") or ""
    if isinstance(declared, str) and declared.strip():
        return declared.strip().lower()
    name = type(client).__name__
    stripped = name[:-6] if name.endswith("Client") and len(name) > 6 else name
    return "".join(c for c in stripped.lower() if c.isalnum()) or "unknown"
