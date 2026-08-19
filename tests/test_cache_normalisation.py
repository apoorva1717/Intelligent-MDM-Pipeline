"""Normalised cache keys (Step 1) and the Tier 1 re-lookup (Step 2).

Two defects, one shape: identical entities produced different output depending
on how the input happened to be spelled.

* **Cache keys** — lowercasing collapses "MIT" and "mit" but not
  ``Coastal Diagnostics, Inc.`` against ``Coastal Diagnostics Inc``, so one
  organisation was looked up under several keys inside a single batch, got
  several outcomes, and the batch emitted contradictory records for it. Keys
  now run through :func:`dedup.signatures.normalize_key` plus the country.
* **Tier 1 re-lookup** — ROR missed on the input spelling, a later tier worked
  out the real name, and nothing ever looked *that* name up.

The HTTP-path tests use ``httpx.MockTransport`` and count real requests,
because the module-level ``_ror_cache`` / ``_lei_cache`` are the caches ROR and
LEI lookups actually consult — a client mock that overrides ``call()`` would
bypass exactly the thing under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord
from config import Settings
from enrichment import tier1_lei, tier1_ror
from enrichment.orchestrator import Orchestrator
from enrichment.tier1_lei import call_lei, clear_lei_cache, lei_normalised_hits
from enrichment.tier1_ror import call_ror, clear_ror_cache, ror_normalised_hits
from utils.cache import BatchCache, lookup_key, serp_key

STUTTGART = "https://ror.org/04vnq7t77"


@pytest.fixture(autouse=True)
def _clear():
    clear_ror_cache()
    clear_lei_cache()
    yield
    clear_ror_cache()
    clear_lei_cache()


def _patch_ror(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(tier1_ror.httpx, "AsyncClient", factory)


def _patch_lei(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(tier1_lei.httpx, "AsyncClient", factory)


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------

class TestKeyConstruction:
    @pytest.mark.parametrize("a,b", [
        ("Coastal Diagnostics Inc", "Coastal Diagnostics, Inc."),
        ("Lockheed Martin Corp", "LOCKHEED MARTIN CORP."),
        ("MIT", "mit"),
        ("Universität Stuttgart", "Universitat  Stuttgart"),
        ("St. Jude Children's Research Hospital",
         "St Jude Children's  Research Hospital"),
    ])
    def test_spelling_variants_share_one_key(self, a, b):
        assert lookup_key(a, "US") == lookup_key(b, "US")

    def test_country_is_part_of_the_key(self):
        """Two genuinely distinct orgs sharing a name in different countries
        must not share a cache entry."""
        assert lookup_key("Sartorius", "DE") != lookup_key("Sartorius", "US")
        assert lookup_key("Sartorius", "de") == lookup_key("Sartorius", "DE")

    def test_legal_forms_are_not_stripped(self):
        """normalize_key is deliberately conservative — it does not remove
        legal forms or expand abbreviations, so genuinely different names stay
        apart."""
        assert lookup_key("Bruker GmbH", "DE") != lookup_key("Bruker AG", "DE")
        assert lookup_key("Uni Stuttgart", "DE") != lookup_key(
            "University of Stuttgart", "DE")

    def test_quoted_and_unquoted_serp_queries_stay_distinct(self):
        """website_resolver §8 issues an unquoted retry when the exact-phrase
        query finds nothing. normalize_key strips the quotes, so without the
        extra key component the retry would be served the very phrase results
        it exists to get away from."""
        quoted = '"Vanguard Sciences" official website'
        unquoted = 'Vanguard Sciences official website'
        assert serp_key(quoted, "US") != serp_key(unquoted, "US")

    def test_serp_key_still_collapses_punctuation(self):
        assert serp_key('"Coastal Diagnostics, Inc." official website', "US") == \
               serp_key('"Coastal Diagnostics Inc" official website', "US")


# ---------------------------------------------------------------------------
# The API payload is never the cache key
# ---------------------------------------------------------------------------

class TestUnnormalisedQueryReachesTheAPI:
    """Condition 2 of the cache-key contract, pinned.

    The key decides *whether* a call is made; it is never the payload. If the
    normalised form ever became the payload, punctuation stripping would enter
    the ROR/LEI scoring path through the back door — which is exactly what the
    ground rules forbid.
    """

    @pytest.mark.asyncio
    async def test_ror_receives_the_original_string(self, monkeypatch):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            seen.append(params.get("affiliation") or params.get("query") or "")
            return httpx.Response(200, json={"items": []})

        _patch_ror(monkeypatch, handler)
        raw = "Coastal Diagnostics, Inc."
        await call_ror(raw, country_code="US")

        assert seen, "ROR was never called"
        # The record's punctuation survives into the request…
        assert any(raw in s for s in seen)
        # …and the normalised key never appears as a payload.
        normalised = lookup_key(raw, "US")[0]
        assert all(s != normalised for s in seen)

    @pytest.mark.asyncio
    async def test_lei_receives_the_original_string(self, monkeypatch):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"data": []})

        _patch_lei(monkeypatch, handler)
        raw = "Lockheed Martin Corp."
        await call_lei(raw, country_code="US")

        assert seen, "GLEIF was never called"
        joined = " ".join(seen)
        # httpx form-encodes spaces as '+'; the period is what matters — it is
        # exactly what the cache key strips.
        assert "Lockheed+Martin+Corp." in joined
        assert lookup_key(raw, "US")[0] not in joined


# ---------------------------------------------------------------------------
# One entity, one API call
# ---------------------------------------------------------------------------

class TestOneEntityOneCall:
    @pytest.mark.asyncio
    async def test_punctuation_variants_cost_one_ror_call(self, monkeypatch):
        """'Coastal Diagnostics Inc' and 'Coastal Diagnostics, Inc.' are one
        lookup."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"items": []})

        _patch_ror(monkeypatch, handler)
        before = calls["n"]
        await call_ror("Coastal Diagnostics Inc", country_code="US")
        after_first = calls["n"]
        await call_ror("Coastal Diagnostics, Inc.", country_code="US")

        assert after_first > before, "first lookup must reach the API"
        assert calls["n"] == after_first, "second lookup must be served from cache"
        assert ror_normalised_hits() == 1

    @pytest.mark.asyncio
    async def test_punctuation_variants_cost_one_lei_call(self, monkeypatch):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"data": []})

        _patch_lei(monkeypatch, handler)
        await call_lei("Lockheed Martin Corp", country_code="US")
        after_first = calls["n"]
        await call_lei("LOCKHEED MARTIN CORP.", country_code="US")

        assert after_first > 0
        assert calls["n"] == after_first
        assert lei_normalised_hits() == 1

    @pytest.mark.asyncio
    async def test_accent_variants_cost_one_ror_call(self, monkeypatch):
        """Accent folding: 'Universität Stuttgart' and 'Universitat Stuttgart'
        are one lookup."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"items": []})

        _patch_ror(monkeypatch, handler)
        await call_ror("Universität Stuttgart", country_code="DE")
        after_first = calls["n"]
        await call_ror("Universitat Stuttgart", country_code="DE")
        assert calls["n"] == after_first

    @pytest.mark.asyncio
    async def test_the_three_stuttgart_forms_and_what_actually_collapses(
        self, monkeypatch,
    ):
        """The Stuttgart trio does NOT collapse to a single ROR call, and this
        test pins that rather than pretending otherwise.

        ``normalize_key`` folds case, punctuation and accents. It does not
        expand abbreviations or resolve synonyms — deliberately, and the fix
        brief asks for exactly that conservatism. So
        ``universitat stuttgart`` / ``university of stuttgart`` /
        ``uni stuttgart`` remain three distinct keys and cost three calls.

        What removes the divergence is Step 2: the accented and abbreviated
        forms adopt ROR's official name and the retry resolves *that*, so all
        three rows converge on one ``ror_id``. See
        ``TestTier1Retry.test_variant_spellings_converge_on_one_ror_id``.
        """
        queried: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            queried.append(params.get("affiliation") or params.get("query") or "")
            return httpx.Response(200, json={"items": []})

        _patch_ror(monkeypatch, handler)
        for name in ("Universität Stuttgart", "University of Stuttgart",
                     "Uni Stuttgart"):
            await call_ror(name, country_code="DE")

        distinct_keys = {
            lookup_key(n, "DE")
            for n in ("Universität Stuttgart", "University of Stuttgart",
                      "Uni Stuttgart")
        }
        assert len(distinct_keys) == 3
        # A same-spelling repeat is free, which is the part the cache can fix.
        before = len(queried)
        await call_ror("UNIVERSITÄT STUTTGART", country_code="DE")
        assert len(queried) == before

    @pytest.mark.asyncio
    async def test_different_countries_do_not_share_an_entry(self, monkeypatch):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"items": []})

        _patch_ror(monkeypatch, handler)
        await call_ror("Sartorius", country_code="DE")
        after_de = calls["n"]
        await call_ror("Sartorius", country_code="US")
        assert calls["n"] > after_de


class TestSerpCacheNormalisation:
    def test_punctuation_variants_share_one_serp_entry(self):
        cache = BatchCache()
        cache.set_serp('"Coastal Diagnostics, Inc." official website',
                       ["r1"], "US")
        assert cache.get_serp('"Coastal Diagnostics Inc" official website',
                              "US") == ["r1"]
        assert cache.normalised_hits == 1

    def test_unquoted_retry_is_not_served_the_quoted_results(self):
        cache = BatchCache()
        cache.set_serp('"Vanguard Sciences" official website', ["quoted"], "US")
        assert cache.get_serp("Vanguard Sciences official website", "US") is None

    def test_country_separates_serp_entries(self):
        cache = BatchCache()
        cache.set_serp("Sartorius official website", ["de"], "DE")
        assert cache.get_serp("Sartorius official website", "US") is None

    def test_identical_query_is_not_counted_as_a_normalisation_win(self):
        """The counter must report only what the OLD key would have missed."""
        cache = BatchCache()
        cache.set_serp("Bruker official website", ["r"], "US")
        assert cache.get_serp("Bruker official website", "US") == ["r"]
        assert cache.normalised_hits == 0


# ---------------------------------------------------------------------------
# Step 2 — Tier 1 re-lookup after canonicalisation
# ---------------------------------------------------------------------------

def _orchestrator(mock_clients) -> Orchestrator:
    return Orchestrator(Settings(), mock_clients=mock_clients)


class _StubROR:
    """Counts calls and answers only for the canonical name."""

    def __init__(self, answers: dict, *, country_guard_fails: bool = False):
        self.answers = answers
        self.calls: list[str] = []
        self.country_guard_fails = country_guard_fails

    async def call(self, name, country_code=None, country=None, city=None,
                   state=None):
        self.calls.append(name)
        if self.country_guard_fails:
            # What the real client returns when the only candidate is in the
            # wrong country: a clean miss, no ror_id.
            return {"matched": False, "score": 0.0}
        return self.answers.get(name, {"matched": False, "score": 0.0})


class _StubLEI:
    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[str] = []

    async def call(self, name, country_code=None):
        self.calls.append(name)
        return self.answers.get(name, {"matched": False, "strategy": "fuzzy",
                                       "score": 0.0})


@pytest.mark.asyncio
class TestTier1Retry:
    async def _run_retry(self, orch, result, record=None):
        record = record or EnrichmentRecord(
            record_id="R1", name1="MASSACHUSETTS INSITUTE OF TECHNOLOGY",
            country="US",
        )
        await orch._retry_tier1_after_canonicalisation(record, result)
        return result

    def _base_result(self, original: str, canonical: str) -> dict:
        return {
            "record_id": "R1",
            "_tier1_query_name": original,
            "_tier1_country_code": "US",
            "name1_enriched": canonical,
            "use_cases_triggered": [],
            "record_type": "company",
        }

    async def test_canonical_name_recovers_the_ror_id(self):
        """Row 24: ROR misses the typo'd input, Tier 3 produces the real name,
        and nothing used to look that name up."""
        ror = _StubROR({
            "Massachusetts Institute of Technology": {
                "matched": True,
                "ror_id": "https://ror.org/042nb2s44",
                "is_research_institution": True,
                "website": "https://web.mit.edu",
            },
        })
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result(
            "MASSACHUSETTS INSITUTE OF TECHNOLOGY",
            "Massachusetts Institute of Technology",
        )
        await self._run_retry(orch, result)

        assert result["ror_id"] == "https://ror.org/042nb2s44"
        assert result["tier_used"] == 1
        assert result["source"] == "ROR"
        assert result["tier1_retry_hit"] == "ROR"
        assert orch._tier1_retry_counts["attempts"] == 1
        assert orch._tier1_retry_counts["hits_ror"] == 1

    async def test_retry_hit_restores_a_verified_domain(self):
        """Interaction with Fix 1: registry provenance satisfies ownership
        condition 1, so a record that lost its domain to the guard gets one
        back — and the earlier rejection is cleared before finalise would
        raise `domain-unverified`."""
        ror = _StubROR({
            "Massachusetts Institute of Technology": {
                "matched": True,
                "ror_id": "https://ror.org/042nb2s44",
                "is_research_institution": True,
                "website": "https://web.mit.edu/research/index.html",
            },
        })
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result(
            "MASSACHUSETTS INSITUTE OF TECHNOLOGY",
            "Massachusetts Institute of Technology",
        )
        result["domain_rejected"] = True
        result["_domain_unverified"] = True
        await self._run_retry(orch, result)

        assert result["domain"] == "mit.edu"          # deep link canonicalised
        assert result["website_url"] == "https://mit.edu"
        assert result["domain_verified_by"] == "registry"
        assert result["domain_rejected"] is False
        assert "_domain_unverified" not in result

    async def test_a_record_cannot_retry_twice(self):
        ror = _StubROR({})
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result("Acme Labs", "Acme Laboratories Inc")

        await self._run_retry(orch, result)
        assert result["tier1_retry_attempted"] is True
        first = len(ror.calls)

        # Another tier rewrites the name again — still no second retry.
        result["name1_enriched"] = "Acme Laboratories Incorporated"
        await self._run_retry(orch, result)

        assert len(ror.calls) == first
        assert orch._tier1_retry_counts["attempts"] == 1

    async def test_retry_failing_the_country_guard_is_a_miss(self):
        """The retry runs the full normal path — no guard is relaxed for it.
        A candidate the country guard drops leaves the record untouched."""
        ror = _StubROR({}, country_guard_fails=True)
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result("BASF Corp", "BASF SE")
        result["source"] = "llm_canonical"
        await self._run_retry(orch, result)

        assert ror.calls == ["BASF SE"], "the retry must actually be attempted"
        assert result.get("ror_id") is None
        assert result.get("tier1_retry_hit") is None
        assert result["source"] == "llm_canonical", "a miss changes nothing"
        assert orch._tier1_retry_counts["attempts"] == 1
        assert orch._tier1_retry_counts["hits_ror"] == 0

    async def test_no_retry_when_the_name_did_not_change(self):
        """A pure punctuation/case difference is not a corrected name and must
        not buy an API call."""
        ror = _StubROR({})
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result("Coastal Diagnostics Inc",
                                   "Coastal Diagnostics, Inc.")
        await self._run_retry(orch, result)

        assert ror.calls == []
        assert orch._tier1_retry_counts["attempts"] == 0

    async def test_no_retry_when_the_record_already_has_a_registry_id(self):
        ror = _StubROR({})
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result("MIT", "Massachusetts Institute of Technology")
        result["ror_id"] = "https://ror.org/042nb2s44"
        await self._run_retry(orch, result)
        assert ror.calls == []

    async def test_no_retry_when_tier1_never_ran(self):
        ror = _StubROR({})
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = {
            "record_id": "R1",
            "name1_enriched": "Massachusetts Institute of Technology",
            "use_cases_triggered": [],
        }
        await self._run_retry(orch, result)
        assert ror.calls == []

    async def test_company_branch_falls_through_to_lei(self):
        ror = _StubROR({})
        lei = _StubLEI({
            "Lockheed Martin Corporation": {
                "matched": True,
                "lei_id": "DPRBOZP0K5RM2YE8UU08",
                "strategy": "exact",
                "legal_name": "Lockheed Martin Corporation",
            },
        })
        orch = _orchestrator({"ror": ror, "lei": lei})
        result = self._base_result("Lockheed Martin Corp",
                                   "Lockheed Martin Corporation")
        await self._run_retry(orch, result)

        assert result["lei_id"] == "DPRBOZP0K5RM2YE8UU08"
        assert result["source"] == "gleif"
        assert result["tier1_retry_hit"] == "gleif"
        assert orch._tier1_retry_counts["hits_lei"] == 1

    async def test_research_name_never_reaches_gleif(self):
        """Same branch rule as the first pass: a research-institution name is
        not sent to a company registry."""
        ror = _StubROR({})
        lei = _StubLEI({})
        orch = _orchestrator({"ror": ror, "lei": lei})
        result = self._base_result("Uni Stuttgart", "University of Stuttgart")
        await self._run_retry(orch, result)

        assert ror.calls == ["University of Stuttgart"]
        assert lei.calls == []

    async def test_record_type_is_left_alone_on_a_contradiction(self):
        """record_type reassignment is out of scope: the registry's view is
        logged, the existing value stands."""
        ror = _StubROR({
            "Bayfront Research Institute": {
                "matched": True,
                "ror_id": "https://ror.org/fakebayfront",
                "is_research_institution": True,
                "website": "https://bayfront.org",
            },
        })
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})
        result = self._base_result("Bayfront Research",
                                   "Bayfront Research Institute")
        result["record_type"] = "company"
        await self._run_retry(orch, result)

        assert result["ror_id"] == "https://ror.org/fakebayfront"
        assert result["record_type"] == "company", "left uncorrected for Fix 3"
        conflict = result["_tier1_retry_type_conflict"]
        assert conflict["registry_type"] == "research_institution"
        assert conflict["record_type"] == "company"

    async def test_variant_spellings_converge_on_one_ror_id(self):
        """Rows 6/7/8: three spellings of one university, one identity.

        Each row's own tier resolves its name to ROR's official form; the
        retry then looks that form up, and the cache means only the first of
        them costs an API call.
        """
        ror = _StubROR({
            "University of Stuttgart": {
                "matched": True,
                "ror_id": STUTTGART,
                "is_research_institution": True,
                "website": "http://www.uni-stuttgart.de/home/index.en.html",
            },
        })
        orch = _orchestrator({"ror": ror, "lei": _StubLEI({})})

        ids = []
        for original in ("Universität Stuttgart", "Uni Stuttgart",
                         "Univ Stuttgart"):
            result = self._base_result(original, "University of Stuttgart")
            result["record_type"] = "research_institution"
            await self._run_retry(orch, result)
            ids.append(result.get("ror_id"))
            # Fix 1: registry provenance restores the domain, canonicalised.
            assert result["domain"] == "uni-stuttgart.de"

        assert ids == [STUTTGART, STUTTGART, STUTTGART]
        assert orch._tier1_retry_counts["hits_ror"] == 3
