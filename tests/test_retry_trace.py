"""Fix 1: the Stage 5 (Tier 1 re-lookup) diagnostic trace.

The trace exists to answer one question — *why did the retry not recover a
registry identity for this record* — and it is only worth having if its answer
can be trusted. Two properties are therefore pinned here:

1. **Each skip reason is produced by its own condition, and only by it.** A
   trace that reported `normalize_key_equal` where the retry was in fact never
   reached would hide the exact defect it was built to find.
2. **Tracing changes nothing.** With ``RETRY_TRACE`` off no line is emitted;
   with it on or off, the transient ``_retry_trace`` slot never survives into
   the response model, and the retry fires under exactly the same conditions.

The classification these tests pin is what
``retry_trace_findings.md`` reports against the chemspeed batch.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import (
    RETRY_SKIP_ALREADY_ATTEMPTED,
    RETRY_SKIP_ALREADY_HAS_ID,
    RETRY_SKIP_NORMALIZE_KEY_EQUAL,
    RETRY_SKIP_NOT_CALLED,
    RETRY_SKIP_NO_TIER1_QUERY,
    Orchestrator,
    _init_result,
)
from enrichment.provenance import registry_evidence
from tests.conftest import tier3_evidence
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.serp_mock import MockSearchClient


def _orch(trace: bool = True, **over) -> Orchestrator:
    settings = Settings()
    object.__setattr__(settings, "retry_trace", trace)
    clients = {
        "ror": MockRORClient(settings), "lei": MockLEIClient(settings),
        "search": MockSearchClient(), "page_fetcher": MockPageFetcher(),
        "llm": MockOpenAIClient(),
    }
    clients.update(over)
    return Orchestrator(settings, mock_clients=clients)


def _record(**kw):
    return _init_result(EnrichmentRecord(record_id="R1", country="US", **kw))


@pytest.fixture
def trace_lines(caplog):
    """Capture the JSON lines the trace logger emits, parsed."""
    logger = logging.getLogger("enrichment.trace.retry")
    logger.setLevel(logging.INFO)
    logger.propagate = True
    with caplog.at_level(logging.INFO, logger="enrichment.trace.retry"):
        yield caplog


def _emitted(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "enrichment.trace.retry"
    ]


# ---------------------------------------------------------------------------
# Each skip reason comes from its own condition
# ---------------------------------------------------------------------------

class TestSkipReasons:
    @pytest.mark.asyncio
    async def test_already_has_id_when_tier1_resolved_first_pass(self):
        orch = _orch()
        result = _record(name1="Massachusetts Institute of Technology")
        result.write(
            "ror_id", "https://ror.org/042nb2s44",
            registry_evidence("ror", "https://ror.org/042nb2s44"),
        )
        await orch._retry_tier1_after_canonicalisation(
            EnrichmentRecord(record_id="R1", country="US"), result,
        )
        assert result["_retry_trace"]["skipped_reason"] == RETRY_SKIP_ALREADY_HAS_ID
        assert result["_retry_trace"]["fired"] is False

    @pytest.mark.asyncio
    async def test_normalize_key_equal_when_only_punctuation_changed(self):
        """The chemspeed bucket-2 case: "Allnex USA Inc" -> "Allnex USA Inc.".
        Tier 1 was already queried with that name; re-querying must not buy an
        API call."""
        orch = _orch()
        record = EnrichmentRecord(record_id="R1", country="US")
        result = _record(name1="Allnex USA Inc")
        result["_tier1_query_name"] = "Allnex USA Inc"
        result.write("name1_enriched", "Allnex USA Inc.", tier3_evidence())

        await orch._retry_tier1_after_canonicalisation(record, result)

        assert result["_retry_trace"]["skipped_reason"] == (
            RETRY_SKIP_NORMALIZE_KEY_EQUAL
        )
        assert result["_retry_trace"]["fired"] is False
        assert result["_retry_trace"]["registries_queried"] == []
        assert orch._tier1_retry_counts["attempts"] == 0

    @pytest.mark.asyncio
    async def test_tier1_never_ran_is_not_reported_as_normalize_key_equal(self):
        """The person path never queries Tier 1, so there is no "queried with"
        to compare against — and that is a different answer from "the names
        matched"."""
        orch = _orch()
        result = _record(name1="Dr Jane Roe")
        result.write("name1_enriched", "Some Institute", tier3_evidence())
        await orch._retry_tier1_after_canonicalisation(
            EnrichmentRecord(record_id="R1", country="US"), result,
        )
        assert result["_retry_trace"]["skipped_reason"] == RETRY_SKIP_NO_TIER1_QUERY

    @pytest.mark.asyncio
    async def test_already_attempted_on_a_second_call(self):
        orch = _orch()
        record = EnrichmentRecord(record_id="R1", country="US")
        result = _record(name1="MASSACHUSETTS INSITUTE OF TECHNOLOGY")
        result["_tier1_query_name"] = "MASSACHUSETTS INSITUTE OF TECHNOLOGY"
        result.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            tier3_evidence(),
        )
        await orch._retry_tier1_after_canonicalisation(record, result)
        assert result["_retry_trace"]["fired"] is True
        # Second call: the one permitted retry is spent.
        await orch._retry_tier1_after_canonicalisation(record, result)
        assert result["_retry_trace"]["skipped_reason"] == (
            RETRY_SKIP_ALREADY_ATTEMPTED
        )


# ---------------------------------------------------------------------------
# A fired retry records what it did
# ---------------------------------------------------------------------------

class TestFiredRetry:
    @pytest.mark.asyncio
    async def test_hit_records_registry_and_query(self):
        orch = _orch()
        record = EnrichmentRecord(record_id="R1", country="US")
        result = _record(name1="MASSACHUSETTS INSITUTE OF TECHNOLOGY")
        result["_tier1_query_name"] = "MASSACHUSETTS INSITUTE OF TECHNOLOGY"
        result["_tier1_country_code"] = "US"
        result.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            tier3_evidence(),
        )

        await orch._retry_tier1_after_canonicalisation(record, result)

        trace = result["_retry_trace"]
        assert trace["fired"] is True
        assert trace["hit"] == "ROR"
        assert trace["registries_queried"] == ["ror"]
        assert trace["query_original"] == "MASSACHUSETTS INSITUTE OF TECHNOLOGY"
        assert trace["query_canonical"] == "Massachusetts Institute of Technology"
        assert result["ror_id"] == "https://ror.org/042nb2s44"

    @pytest.mark.asyncio
    async def test_company_miss_queries_ror_then_gleif(self):
        """A company-branch name that ROR misses reaches GLEIF, and the trace
        names both — which is how bucket 4 (registry coverage) is told apart
        from a branch that never asked."""
        orch = _orch()
        record = EnrichmentRecord(record_id="R1", country="US")
        result = _record(name1="Nowhere Widgets")
        result["_tier1_query_name"] = "Nowhere Widgets"
        result.write("name1_enriched", "Nowhere Widgets, Inc.", tier3_evidence())

        await orch._retry_tier1_after_canonicalisation(record, result)

        trace = result["_retry_trace"]
        assert trace["fired"] is True
        assert trace["registries_queried"] == ["ror", "gleif"]
        assert trace["hit"] is None
        assert trace["gleif_outcome"] == "miss"

    @pytest.mark.asyncio
    async def test_research_name_never_reaches_gleif_and_says_so(self):
        """Branch rules are unchanged by the trace: a research-institution name
        is never sent to a company registry. The trace records the reason
        rather than leaving an empty `registries_queried` to be misread as a
        GLEIF outage."""
        orch = _orch()
        record = EnrichmentRecord(record_id="R1", country="US")
        result = _record(name1="Nowhere State Univ")
        result["_tier1_query_name"] = "Nowhere State Univ"
        result.write(
            "name1_enriched", "Nowhere State University of Nothing",
            tier3_evidence(),
        )

        await orch._retry_tier1_after_canonicalisation(record, result)

        trace = result["_retry_trace"]
        assert trace["registries_queried"] == ["ror"]
        assert trace["gleif_skipped"] == "looks_like_research_institution"


# ---------------------------------------------------------------------------
# The trace is inert
# ---------------------------------------------------------------------------

class TestTracingChangesNothing:
    @pytest.mark.asyncio
    async def test_no_line_emitted_when_flag_is_off(self, trace_lines):
        orch = _orch(trace=False)
        results = await orch.enrich_batch(
            [EnrichmentRecord(record_id="R1", name1="MIT", country="US")],
            EnrichmentOptions(max_concurrency=1),
        )
        assert results.results
        assert _emitted(trace_lines) == []

    @pytest.mark.asyncio
    async def test_one_line_per_finalised_record_when_on(self, trace_lines):
        orch = _orch(trace=True)
        await orch.enrich_batch(
            [
                EnrichmentRecord(record_id="R1", name1="MIT", country="US"),
                EnrichmentRecord(record_id="R2", name1="UCLA", country="US"),
            ],
            EnrichmentOptions(max_concurrency=1),
        )
        emitted = _emitted(trace_lines)
        assert len(emitted) == 2
        assert {t["record_id"] for t in emitted} == {"R1", "R2"}
        # `called` is what separates bucket 1 from every other outcome.
        assert all(t["called"] for t in emitted)
        assert all(t["skipped_reason"] != RETRY_SKIP_NOT_CALLED for t in emitted)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("trace", [True, False])
    async def test_transient_slot_never_reaches_the_response(self, trace):
        """`_retry_trace` is not a schema field. It is popped on every path,
        tracing on or off, so pydantic never sees it."""
        orch = _orch(trace=trace)
        response = await orch.enrich_batch(
            [EnrichmentRecord(record_id="R1", name1="MIT", country="US")],
            EnrichmentOptions(max_concurrency=1),
        )
        result = response.results[0]
        assert not hasattr(result, "_retry_trace")
        assert "_retry_trace" not in result.model_dump()
