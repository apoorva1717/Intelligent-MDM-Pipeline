"""Tests for Tier 1 LEI / GLEIF company lookup.

Three layers, mirroring the ROR tests:

* Unit tests on the verification guard (``_best_verified_candidate`` /
  ``_name_match_score``) — pure, no HTTP.
* HTTP-path tests for ``call_lei`` using ``httpx.MockTransport`` (mock
  GLEIF) — exact match, fuzzy fallback, wrong-entity rejection, error.
* End-to-end orchestrator tests with ``MockLEIClient`` — integration into
  the company branch, ROR-institution protection, and the feature flag.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment import tier1_lei
from enrichment.orchestrator import Orchestrator
from enrichment.tier1_lei import (
    LEIClient,
    _best_verified_candidate,
    _name_match_score,
    call_lei,
    clear_lei_cache,
)

# Correct GLEIF LEI for the Swiss Pfizer entity (used across tests).
PFIZER_AG_LEI = "549300ZZDOU0WGVYS169"


def _rec(lei: str, name: str, country: str, status: str = "ACTIVE") -> dict:
    """Build a minimal GLEIF lei-record in JSON:API shape."""
    return {
        "id": lei,
        "type": "lei-records",
        "attributes": {
            "entity": {
                "legalName": {"name": name},
                "status": status,
                "legalAddress": {"country": country},
            }
        },
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_lei_cache()
    yield
    clear_lei_cache()


def _patch_gleif(monkeypatch, handler) -> None:
    """Route every httpx.AsyncClient in tier1_lei through a MockTransport."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(
            transport=httpx.MockTransport(handler),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(tier1_lei.httpx, "AsyncClient", factory)


# ── Verification guard (unit) ─────────────────────────────────────────────────

class TestVerificationGuard:
    def test_name_match_is_case_folded(self) -> None:
        """GLEIF returns names UPPERCASE; the guard must score case-insensitively."""
        assert _name_match_score("Pfizer AG", "PFIZER AG") == 100.0

    def test_best_candidate_picks_verified_over_wrong(self) -> None:
        """The correct entity wins over a statistically-close wrong one."""
        records = [
            _rec("WRONGLEI000000000001",
                 "Personalvorsorgestiftung der Pfizer AG in Liquidation", "CH"),
            _rec(PFIZER_AG_LEI, "PFIZER AG", "CH"),
        ]
        fields, score = _best_verified_candidate("Pfizer AG", records, 88.0)
        assert fields is not None
        assert fields["lei_id"] == PFIZER_AG_LEI
        assert score == 100.0

    def test_below_threshold_rejected(self) -> None:
        """A close-but-wrong entity alone yields no match (no fabrication).

        This is the case a naive token_set_ratio would WRONGLY accept (the
        input is a subset of the candidate → set ratio 100); length-sensitive
        token_sort keeps it well below threshold.
        """
        records = [
            _rec("WRONGLEI000000000001",
                 "Personalvorsorgestiftung der Pfizer AG in Liquidation", "CH"),
        ]
        fields, score = _best_verified_candidate("Pfizer AG", records, 88.0)
        assert fields is None
        assert score < 88.0

    def test_bare_name_matches_legal_form(self) -> None:
        """An input lacking the legal form verifies against the official name.

        'Novartis' / 'Pfizer' (no suffix) must clear the threshold against
        'NOVARTIS AG' / 'PFIZER INC.' via the legal-form strip.
        """
        assert _name_match_score("Novartis", "NOVARTIS AG") >= 88.0
        assert _name_match_score("Pfizer", "PFIZER INC.") >= 88.0

    def test_subsidiary_not_collapsed(self) -> None:
        """A descriptive token (not a legal form) still distinguishes entities.

        Bare 'Pfizer' must NOT match the distinct 'PFIZER PRODUCTS INC.' —
        'products' is kept, so the score stays below threshold.
        """
        assert _name_match_score("Pfizer", "PFIZER PRODUCTS INC.") < 88.0

    def test_active_preferred_over_inactive(self) -> None:
        """When two candidates both verify, the ACTIVE one is preferred."""
        records = [
            _rec("INACTIVE000000000001", "PFIZER AG", "CH", status="INACTIVE"),
            _rec(PFIZER_AG_LEI, "PFIZER AG", "CH", status="ACTIVE"),
        ]
        fields, _ = _best_verified_candidate("Pfizer AG", records, 88.0)
        assert fields is not None
        assert fields["lei_id"] == PFIZER_AG_LEI

    def test_wrong_country_rejected(self) -> None:
        """A same-name entity in a different country is rejected by the guard."""
        records = [
            _rec("USWRONG0000000000001", "PFIZER AG", "US"),
        ]
        fields, _ = _best_verified_candidate(
            "Pfizer AG", records, 88.0, country_code="CH",
        )
        assert fields is None

    def test_right_country_selected_over_wrong_country(self) -> None:
        """With both countries present, only the requested-country entity wins."""
        records = [
            _rec("USWRONG0000000000001", "PFIZER AG", "US"),
            _rec(PFIZER_AG_LEI, "PFIZER AG", "CH"),
        ]
        fields, _ = _best_verified_candidate(
            "Pfizer AG", records, 88.0, country_code="CH",
        )
        assert fields is not None
        assert fields["lei_id"] == PFIZER_AG_LEI
        assert fields["country"] == "CH"

    def test_no_country_filter_keeps_all(self) -> None:
        """When no country is given, the guard does not filter on country."""
        records = [_rec("USONLY00000000000001", "PFIZER AG", "US")]
        fields, _ = _best_verified_candidate("Pfizer AG", records, 88.0)
        assert fields is not None
        assert fields["lei_id"] == "USONLY00000000000001"

    def test_country_match_is_case_insensitive(self) -> None:
        """Requested country compares case-insensitively against GLEIF's value."""
        records = [_rec(PFIZER_AG_LEI, "PFIZER AG", "CH")]
        fields, _ = _best_verified_candidate(
            "Pfizer AG", records, 88.0, country_code="ch",
        )
        assert fields is not None
        assert fields["lei_id"] == PFIZER_AG_LEI


# ── call_lei HTTP paths (mock GLEIF) ──────────────────────────────────────────

class TestCallLei:
    @pytest.mark.asyncio
    async def test_exact_match(self, monkeypatch) -> None:
        """Exact legalName filter returns a verified ACTIVE entity → high."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": [
                    _rec(PFIZER_AG_LEI, "PFIZER AG", "CH"),
                ]})
            return httpx.Response(200, json={"data": []})

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is True
        assert res["strategy"] == "exact"
        assert res["confidence"] == "high"
        assert res["lei_id"] == PFIZER_AG_LEI
        assert res["legal_name"] == "PFIZER AG"

    @pytest.mark.asyncio
    async def test_wrong_entity_rejected(self, monkeypatch) -> None:
        """A statistically-close but wrong entity is rejected → miss, no LEI."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": [
                    _rec("WRONGLEI000000000001",
                         "Personalvorsorgestiftung der Pfizer AG in Liquidation",
                         "CH"),
                ]})
            # fuzzycompletions also empty
            return httpx.Response(200, json={"data": []})

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is False
        assert "lei_id" not in res or res.get("lei_id") is None

    @pytest.mark.asyncio
    async def test_fuzzy_fallback(self, monkeypatch) -> None:
        """Exact returns nothing; fuzzycompletions resolves a verified record."""
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/fuzzycompletions"):
                return httpx.Response(200, json={"data": [{
                    "type": "fuzzycompletions",
                    "attributes": {"value": "PFIZER AG"},
                    "relationships": {
                        "lei-records": {"data": {"type": "lei-records", "id": PFIZER_AG_LEI}}
                    },
                }]})
            if path.endswith(f"/lei-records/{PFIZER_AG_LEI}"):
                return httpx.Response(200, json={"data": _rec(PFIZER_AG_LEI, "PFIZER AG", "CH")})
            if path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": []})  # exact miss
            return httpx.Response(404, json={"data": []})

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is True
        assert res["strategy"] == "fuzzy"
        assert res["confidence"] == "medium"
        assert res["lei_id"] == PFIZER_AG_LEI

    @pytest.mark.asyncio
    async def test_fuzzy_wrong_country_rejected(self, monkeypatch) -> None:
        """The reported bug: fuzzycompletions resolves a same-name entity in a
        DIFFERENT country. With a country filter, it must be rejected (miss),
        not returned."""
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/fuzzycompletions"):
                return httpx.Response(200, json={"data": [{
                    "type": "fuzzycompletions",
                    "attributes": {"value": "PFIZER AG"},
                    "relationships": {
                        "lei-records": {"data": {"type": "lei-records", "id": "USWRONG0000000000001"}}
                    },
                }]})
            if path.endswith("/lei-records/USWRONG0000000000001"):
                # Same name, WRONG country (US, not the requested CH).
                return httpx.Response(200, json={"data": _rec("USWRONG0000000000001", "PFIZER AG", "US")})
            if path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": []})  # exact miss
            return httpx.Response(404, json={"data": []})

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is False
        assert res.get("lei_id") is None

    @pytest.mark.asyncio
    async def test_exact_wrong_country_rejected(self, monkeypatch) -> None:
        """Even if GLEIF's server-side country filter slips and returns a
        wrong-country record, the post-filter guard rejects it."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": [
                    _rec("USWRONG0000000000001", "PFIZER AG", "US"),
                ]})
            return httpx.Response(200, json={"data": []})  # fuzzy empty

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is False

    @pytest.mark.asyncio
    async def test_miss_returns_unmatched(self, monkeypatch) -> None:
        """No records and no completions → clean miss (not an error)."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Nonexistent Tiny Co", country_code="US", max_retries=0)
        assert res["matched"] is False
        assert not res.get("error")

    @pytest.mark.asyncio
    async def test_server_error_returns_error_dict(self, monkeypatch) -> None:
        """A 500 never raises out of call_lei — it returns an error dict."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is False
        assert res["error"] is True

    @pytest.mark.asyncio
    async def test_timeout_returns_error_dict(self, monkeypatch) -> None:
        """A transport timeout is swallowed into an error dict."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        _patch_gleif(monkeypatch, handler)
        res = await call_lei("Pfizer AG", country_code="CH", max_retries=0)
        assert res["matched"] is False
        assert res["error"] is True


# ── Orchestrator integration (MockLEIClient) ──────────────────────────────────

@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def orchestrator(settings, mock_clients):
    return Orchestrator(settings, mock_clients=mock_clients)


@pytest.fixture
def default_options():
    return EnrichmentOptions(max_concurrency=1)


class TestLeiOrchestration:
    @pytest.mark.asyncio
    async def test_pfizer_ag_verified_match(self, orchestrator, default_options):
        """'Pfizer AG' → GLEIF match: official legalName, lei_id, company, high."""
        record = EnrichmentRecord(
            record_id="LEI_001", name1="Pfizer AG",
            city="Zurich", country="CH",
        )
        result = (await orchestrator.enrich_batch([record], default_options)).results[0]
        assert result.lei_id == PFIZER_AG_LEI
        assert result.record_type == "company"
        assert result.confidence == "high"
        assert "pfizer" in (result.name1_enriched or "").lower()

    @pytest.mark.asyncio
    async def test_pfizer_no_legal_form_same_lei(self, orchestrator, default_options):
        """'Pfizer' (no legal form) resolves to the SAME lei_id as 'Pfizer AG'."""
        record = EnrichmentRecord(
            record_id="LEI_002", name1="Pfizer",
            city="New York", country="US",
        )
        result = (await orchestrator.enrich_batch([record], default_options)).results[0]
        assert result.lei_id == PFIZER_AG_LEI

    @pytest.mark.asyncio
    async def test_unknown_company_falls_through_no_fabrication(
        self, orchestrator, default_options,
    ):
        """Unknown tiny company → LEI miss → LLM path, lei_id None, originals kept."""
        record = EnrichmentRecord(
            record_id="LEI_003", name1="Acme Tiny Widgets Ltd",
            city="Springfield", country="US",
        )
        result = (await orchestrator.enrich_batch([record], default_options)).results[0]
        assert result.lei_id is None
        # name1 preserved (no fabricated legal name)
        assert "acme" in (result.name1_enriched or "").lower()

    @pytest.mark.asyncio
    async def test_wrong_entity_rejected_passthrough(
        self, orchestrator, default_options, mock_lei_client,
    ):
        """GLEIF verification fails (close-but-wrong) → passthrough, lei_id None."""
        record = EnrichmentRecord(
            record_id="LEI_004", name1="CloseBrand",
            city="Anywhere", country="US",
        )
        result = (await orchestrator.enrich_batch([record], default_options)).results[0]
        assert result.lei_id is None
        assert mock_lei_client.last_name == "CloseBrand"

    @pytest.mark.asyncio
    async def test_gleif_error_record_still_succeeds(
        self, orchestrator, default_options,
    ):
        """A GLEIF error never fails the record; lei_errors is incremented."""
        record = EnrichmentRecord(
            record_id="LEI_005", name1="ErrorCo",
            city="Anywhere", country="US",
        )
        response = await orchestrator.enrich_batch([record], default_options)
        result = response.results[0]
        assert result.lei_id is None
        assert result.error is None  # no crash
        assert response.summary.lei_errors == 1

    @pytest.mark.asyncio
    async def test_research_institution_not_touched(
        self, orchestrator, default_options, mock_lei_client,
    ):
        """A ROR-matched research institution → LEI is NOT called, ROR wins."""
        record = EnrichmentRecord(
            record_id="LEI_006", name1="Stanford University",
            city="Stanford", state="CA", country="US",
        )
        result = (await orchestrator.enrich_batch([record], default_options)).results[0]
        assert result.record_type == "research_institution"
        assert result.lei_id is None
        assert result.name1_enriched == "Stanford University"
        assert mock_lei_client.call_count == 0

    @pytest.mark.asyncio
    async def test_feature_flag_disabled_regression(
        self, monkeypatch, mock_clients, default_options, mock_lei_client,
    ):
        """LEI_LOOKUP_ENABLED=false → behaviour identical to today (no LEI)."""
        monkeypatch.setenv("LEI_LOOKUP_ENABLED", "false")
        disabled = Settings()
        assert disabled.lei_lookup_enabled is False
        orch = Orchestrator(disabled, mock_clients=mock_clients)
        record = EnrichmentRecord(
            record_id="LEI_007", name1="Pfizer AG",
            city="Zurich", country="CH",
        )
        result = (await orch.enrich_batch([record], default_options)).results[0]
        assert result.lei_id is None
        assert mock_lei_client.call_count == 0

    @pytest.mark.asyncio
    async def test_summary_counts_exact_hit(self, orchestrator, default_options):
        """Telemetry: an exact GLEIF hit increments attempts + hits_exact."""
        record = EnrichmentRecord(
            record_id="LEI_008", name1="Pfizer AG", country="CH",
        )
        summary = (await orchestrator.enrich_batch([record], default_options)).summary
        assert summary.lei_attempts == 1
        assert summary.lei_hits_exact == 1
        assert summary.tier1_lei_count == 1
