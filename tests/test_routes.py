"""Tests for API routes using httpx AsyncClient."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["ENV"] = "local"
os.environ["MOCK_EXTERNAL_CALLS"] = "true"
# FIX(Bug 6): use direct OpenAI env vars, not Azure
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-mocks")

from api.app import app


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest_asyncio.fixture
async def client(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestRoutes:
    """Test API endpoints."""

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert data["mock_mode"] is True

    @pytest.mark.asyncio
    async def test_tiers(self, client):
        resp = await client.get("/tiers")
        assert resp.status_code == 200
        data = resp.json()
        # FIX(Bug 1): single threshold now
        assert "ror_confidence_threshold" in data
        assert data["mock_mode"] is True

    @pytest.mark.asyncio
    async def test_enrich_single_record(self, client):
        payload = {
            "records": [
                {
                    "record_id": "ROUTE_001",
                    "name1": "Massachusetts Institute of Technology",
                    "name2": "Department of Chemistry",
                }
            ],
            "options": {"max_concurrency": 1},
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["record_id"] == "ROUTE_001"
        assert data["summary"]["total"] == 1

    @pytest.mark.asyncio
    async def test_enrich_validation_error(self, client):
        """Empty records list should return 422."""
        payload = {"records": [], "options": {}}
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_enrich_missing_record_id(self, client):
        """Missing required field record_id → 422."""
        payload = {
            "records": [{"name1": "MIT"}],
            "options": {},
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_enrich_batch(self, client):
        """Batch of 3 records returns 3 results."""
        payload = {
            "records": [
                {"record_id": "R1", "name1": "MIT", "name2": "Department of Physics"},
                {"record_id": "R2", "name1": "Pfizer Inc", "name2": "R&D"},
                {"record_id": "R3", "name1": "UCLA"},
            ],
            "options": {"max_concurrency": 2},
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 3
        assert data["summary"]["total"] == 3

    @pytest.mark.asyncio
    async def test_enrich_always_200(self, client):
        """Valid request always returns 200, errors in result.error."""
        payload = {
            "records": [
                {"record_id": "ERR_001", "name1": None},
            ],
        }
        resp = await client.post("/enrich", json=payload)
        assert resp.status_code == 200
