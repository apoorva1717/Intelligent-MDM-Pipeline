"""Mock Wikidata client for the crosswalk lane.

Mirrors :class:`tests.mocks.lei_mock.MockLEIClient`: it satisfies the
:class:`enrichment.wikidata.WikidataClient` interface — ``search(name)`` and
``entities(qids)`` — from curated data, and never touches the network.

Two curated stores rather than one, because the lane makes two distinct calls
and the tests need to make either of them fail on its own: ``_SEARCH`` maps a
lowercased name substring to the QIDs ``wbsearchentities`` would return, and
``_ENTITIES`` holds raw ``wbgetentities`` entity payloads. The payload shape is
the real one — ``labels``/``aliases``/``claims`` with ``mainsnak.datavalue`` —
so :func:`enrichment.wikidata.parse_entity` is exercised rather than bypassed.
"""

from __future__ import annotations

import logging
from typing import Any

from enrichment.wikidata import WikidataClient, WikidataUnavailable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Payload builders — the real wbgetentities shape, spelled once
# ---------------------------------------------------------------------------

def entity_claim(prop: str, qid: str) -> dict[str, Any]:
    """A ``wikibase-entityid`` statement (``P31``, ``P17``, ``P159``, ``P1366``)."""
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {
                "value": {"entity-type": "item", "id": qid},
                "type": "wikibase-entityid",
            },
        },
    }


def string_claim(prop: str, value: str) -> dict[str, Any]:
    """A string statement (``P6782``, ``P1278``, ``P856``)."""
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {"value": value, "type": "string"},
        },
    }


def time_claim(prop: str, stamp: str, precision: int = 11) -> dict[str, Any]:
    """A time statement (``P576``). *stamp* is ``YYYY-MM-DD``."""
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": prop,
            "datavalue": {
                "value": {"time": f"+{stamp}T00:00:00Z", "precision": precision},
                "type": "time",
            },
        },
    }


def make_entity(
    qid: str,
    label: str | None = None,
    *,
    aliases: tuple[str, ...] = (),
    claims: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """One ``wbgetentities`` entity, in the API's own shape."""
    payload: dict[str, Any] = {"id": qid, "claims": claims or {}}
    if label is not None:
        payload["labels"] = {"en": {"language": "en", "value": label}}
    if aliases:
        payload["aliases"] = {
            "en": [{"language": "en", "value": a} for a in aliases]
        }
    return payload


class MockWikidataClient(WikidataClient):
    """Deterministic Wikidata client. Zero network, curated answers.

    Constructed empty by default — every search misses, which is what the
    orchestrator test suite wants from a lane it is not exercising. Tests that
    need a match pass ``search=`` and ``entities=`` maps, or a ``fail`` string
    to make every call report the lane unavailable.
    """

    def __init__(
        self,
        settings: Any = None,
        *,
        search: dict[str, list[str]] | None = None,
        entities: dict[str, dict[str, Any]] | None = None,
        fail: str | None = None,
    ) -> None:
        # Deliberately NOT calling super().__init__: this double owns no
        # transport, no cache and no base URL, and inheriting them would leave
        # a live client one forgotten override away.
        self._search = {k.lower(): v for k, v in (search or {}).items()}
        self._entities = dict(entities or {})
        self._fail = fail
        self.calls = 0
        self.operations = 0
        self.searched: list[str] = []
        self.fetched: list[list[str]] = []

    async def search(self, name: str) -> list[str]:
        if self._fail:
            raise WikidataUnavailable(self._fail)
        query = (name or "").strip().lower()
        self.searched.append(query)
        if not query:
            return []
        hits = self._search.get(query)
        if hits is None:
            for key, value in self._search.items():
                if key in query or query in key:
                    hits = value
                    break
        logger.info("[MOCK] wikidata search '%s' → %s", name[:60], hits)
        return list(hits or [])

    async def entities(self, qids: list[str]) -> dict[str, dict[str, Any]]:
        if self._fail:
            raise WikidataUnavailable(self._fail)
        wanted = [q for q in dict.fromkeys(qids) if q]
        self.fetched.append(wanted)
        return {q: self._entities[q] for q in wanted if q in self._entities}
