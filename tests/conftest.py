"""Pytest fixtures: mock client injection, JSON fixture loaders, settings overrides."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Ensure the project root is on sys.path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ENV", "local")
os.environ.setdefault("MOCK_EXTERNAL_CALLS", "true")
# FIX(Bug 6): use direct OpenAI env vars, not Azure
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from config import Settings
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.page_mock import MockPageFetcher
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.serp_mock import MockSearchClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
def mock_clients(
    mock_ror_client: MockRORClient,
    mock_lei_client: MockLEIClient,
    mock_search_client: MockSearchClient,
    mock_page_fetcher: MockPageFetcher,
    mock_llm_client: MockOpenAIClient,
) -> dict:
    """Bundle all mock clients for Orchestrator injection."""
    return {
        "ror": mock_ror_client,
        "lei": mock_lei_client,
        "search": mock_search_client,
        "page_fetcher": mock_page_fetcher,
        "llm": mock_llm_client,
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
