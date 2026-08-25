"""Pytest fixtures: mock client injection, JSON fixture loaders, settings overrides."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

# Ensure the project root is on sys.path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
# The Wikidata crosswalk lane is on by default in production, and nineteen test
# modules build an Orchestrator with a partial `mock_clients` dict — so without
# this the lane would reach the live API from any test whose record misses ROR
# and GLEIF. Replay-only is the mode the setting exists for: an unrecorded
# query is reported as `wikidata_unavailable` and no network call is made.
# A test that wants the lane to match injects a `wikidata` client of its own.
os.environ.setdefault("WIKIDATA_FIXTURE_REPLAY_ONLY", "true")
# FIX(Bug 6): use direct OpenAI env vars, not Azure
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

# Fix B — the evidence cache is persistent by design, which is exactly what a
# unit-test process must not have: one test's recorded GLEIF answer would be
# served to the next test that happens to look up the same name, and the
# assertion under test would pass or fail depending on execution order.
#
# So every namespace is rooted in a throwaway directory for the duration of a
# test run. That also stops the suite writing into `tests/fixtures/page_reads/`,
# which it always did — harmlessly while entries were overwritable, and not
# harmlessly once Fix B made them immutable: the first test to record
# `acme.com` would have pinned that payload for every later test. No test reads
# a committed page/Wikidata recording; they all inject a double.
# `_reset_evidence_cache` below then clears the process-global store between
# tests, so nothing survives even in memory.
_TEST_CACHE_ROOT = Path(tempfile.gettempdir()) / "enrichment_api_test_cache"
os.environ.setdefault("EVIDENCE_CACHE_DIR", str(_TEST_CACHE_ROOT))
os.environ.setdefault("PAGE_FIXTURE_DIR", str(_TEST_CACHE_ROOT / "page_reads"))
os.environ.setdefault("WIKIDATA_FIXTURE_DIR", str(_TEST_CACHE_ROOT / "wikidata"))

from config import Settings
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.serp_mock import MockSearchClient
from tests.mocks.wikidata_mock import MockWikidataClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_evidence_cache():
    """Give every test a fresh, empty evidence cache.

    The cache is process-global by design (the module-level ROR / GLEIF
    clients reach it that way), and persistent by design. Both are wrong
    inside one pytest process: without this, the first test to build an
    Orchestrator leaves its store installed, and every later test that looks
    up a name the earlier one recorded is served that recording instead of
    calling its own stub. It is the test-suite counterpart of the per-batch
    `clear_ror_cache()`.
    """
    from utils.cache import set_active_evidence_cache

    set_active_evidence_cache(None)
    yield
    set_active_evidence_cache(None)


@pytest.fixture
def test_settings() -> Settings:
    """Settings with test-appropriate defaults."""
    return Settings()


@pytest.fixture
def mock_ror_client(test_settings: Settings) -> MockRORClient:
    return MockRORClient(test_settings)


@pytest.fixture
def mock_lei_client(test_settings: Settings) -> MockLEIClient:
    return MockLEIClient(test_settings)


@pytest.fixture
def mock_search_client() -> MockSearchClient:
    return MockSearchClient()


@pytest.fixture
def mock_page_fetcher() -> MockPageFetcher:
    return MockPageFetcher()


@pytest.fixture
def mock_llm_client() -> MockOpenAIClient:
    return MockOpenAIClient()


@pytest.fixture
def mock_wikidata_client(test_settings: Settings) -> MockWikidataClient:
    """A crosswalk lane that matches nothing — the default for tests that are
    not exercising it. A test that wants a match builds its own with curated
    ``search=`` / ``entities=`` maps."""
    return MockWikidataClient(test_settings)


@pytest.fixture
def mock_clients(
    mock_ror_client: MockRORClient,
    mock_lei_client: MockLEIClient,
    mock_search_client: MockSearchClient,
    mock_page_fetcher: MockPageFetcher,
    mock_llm_client: MockOpenAIClient,
    mock_wikidata_client: MockWikidataClient,
) -> dict:
    """Bundle all mock clients for Orchestrator injection."""
    return {
        "ror": mock_ror_client,
        "lei": mock_lei_client,
        "search": mock_search_client,
        "page_fetcher": mock_page_fetcher,
        "llm": mock_llm_client,
        "wikidata": mock_wikidata_client,
    }


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file by name (without .json extension)."""
    path = FIXTURES_DIR / f"{name}.json"
    with open(path) as f:
        return json.load(f)


def load_expected_outcomes() -> dict:
    """Load the expected outcomes file."""
    return load_fixture("expected_outcomes")


@pytest.fixture
def fixture_loader():
    """Provide fixture loading capability to tests."""
    return load_fixture


@pytest.fixture
def expected_outcomes():
    """Load expected outcomes for assertion."""
    return load_expected_outcomes()


# ---------------------------------------------------------------------------
# Fix 10 — the test-fixture write path
# ---------------------------------------------------------------------------
#
# The six Phase 1 scoped fields are write-locked on `EnrichedRecord`: nothing
# reaches them without evidence, tests included. A test that puts a record into
# a stated post-tier state is standing in for a producer, so it says so — the
# evidence names the fixture as the producer rather than laundering an
# unattributed value through a back door that production code could also use.

from enrichment.provenance import (  # noqa: E402
    INPUT_CORROBORATED,
    SCOPED_FIELDS,
    EnrichedRecord,
    Evidence,
    deterministic_evidence,
)


def fixture_evidence(rule_id: str = "test-fixture"):
    """Evidence for a value a test wrote directly, and is not testing.

    Recorded as `input_corroborated` — Provenance Scheme B renders it
    `input:verified+web` — because that is what a fixture value MEANS: the
    field is settled and the test is about something else. A test that is
    about the field's confidence writes its own evidence and says so.

    The obvious alternative, a bare `deterministic_evidence`, is wrong now
    that the review flag is derived from provenance. It renders `input:low` —
    "the input value stood and nothing corroborated it", which is exactly the
    doubt `low-confidence-unchanged` used to name — so every record built by
    `make_record` would arrive at `compute_flags` with its Name 1 in doubt,
    and a dozen tests about overflow, contacts and department scope would
    start reporting `name1` in `flagged_fields`. That would be the fixture
    asserting something it does not mean.
    """
    return Evidence(
        producer_chain=("test_fixture",),
        tier=1,
        confidence_scale=INPUT_CORROBORATED,
        confidence_value=1.0,
        evidence_ref={"corroborated_by": "domain:test", "rule_id": rule_id},
        rule_id=rule_id,
    )


def tier3_evidence():
    """Evidence for a value a test is standing in for Tier 3 to have written.

    Fix 10 derives the `unverified-inference` flag from the provenance log, so
    a fixture that says "Tier 3 wrote this" has to write it AS Tier 3 — which
    is the point: the flag now follows from the record's write history rather
    than from a marker a tier remembered to set.
    """
    from llm.prompts import TIER3_PROMPT_VERSION
    from enrichment.provenance import llm_evidence

    return llm_evidence(
        ("llm_tier3",), tier=3, prompt_version=TIER3_PROMPT_VERSION,
        deployment="test-deployment", self_reported="high",
    )


def seed(record, evidence=None, **values):
    """Set fields on a result record, routing scoped fields through ``write``.

    The plain-dict equivalent of ``result.update(**values)`` for tests. Non-
    scoped keys are set directly; scoped keys go through the one write path and
    so appear in the record's provenance log, which is what lets the seeded
    record survive the admissibility gate exactly as a real one does.
    """
    if not isinstance(record, EnrichedRecord):
        record.update(values)
        return record
    for key, value in values.items():
        if key in SCOPED_FIELDS:
            record.write(key, value, evidence or fixture_evidence())
        else:
            record[key] = value
    return record


def make_record(**fields):
    """An ``EnrichedRecord`` in a stated post-tier state, for finalise() tests.

    Splits the scoped fields out of *fields* and writes them through the one
    write path, so a hand-built fixture record carries the same provenance a
    real one does and reaches the admissibility gate on the same terms.
    """
    scoped = {k: v for k, v in fields.items() if k in SCOPED_FIELDS}
    base = {k: v for k, v in fields.items() if k not in SCOPED_FIELDS}
    base.update({k: None for k in SCOPED_FIELDS if k != "record_type"})
    base.setdefault("record_type", "unknown")
    record = EnrichedRecord.initialise(base)
    return seed(record, **scoped)
