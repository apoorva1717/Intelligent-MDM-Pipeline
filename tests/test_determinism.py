"""Determinism and cross-source consistency — Fixes A, B, C and D.

Two runs of the identical 101-row chemspeed batch, on the identical codebase,
produced seven substantively different records. These tests pin the four
properties that made that possible, one class per fix:

* **A** — every LLM call that gates a decision is pinned to ``temperature=0``,
  ``top_p=1`` and a fixed ``seed``, and a component run twice against the same
  fixture produces byte-identical output *including its confidence
  self-report*, which is what flipped on three rows.
* **B** — cache keys are pure functions of the request, entries are immutable
  and carry the date their evidence was gathered, and ``CACHE_FROZEN`` turns a
  miss into a recorded unavailability instead of a network call.
* **C** — candidate selection is a total order that does not depend on the
  order an API answered in; a near-tie is a no-match; a name too short to
  identify an entity needs a second signal.
* **D** — no record ships two contradictory identities, a registry match whose
  locality contradicts the record is flagged rather than discarded, and
  Search Term 1 comes from the identity that survived.

The named cases from the two diffed runs (BIC Corp, BHS) appear here as
fixtures, spelled as they appeared in the batch.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment import tier1_lei, tier1_ror
from enrichment.company_canonical import run_company_canonical
from enrichment.flags import REGISTRY_LOCATION_MISMATCH, SOURCE_CONFLICT
from enrichment.orchestrator import Orchestrator
from enrichment.page_corroborator import operating_name_provenance
from enrichment.registry_match import (
    REGISTRY_AMBIGUITY_MARGIN,
    is_collision_prone,
)
from enrichment.tier1_lei import call_lei, clear_lei_cache
from enrichment.tier1_ror import call_ror, clear_ror_cache
from enrichment.tier3_llm import run_tier3
from llm import openai_client
from llm.openai_client import (
    LLM_SEED,
    LLM_TEMPERATURE,
    LLM_TOP_P,
    call_openai,
    reset_seed_support,
    seed_supported,
)
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.page_mock import MockPageFetcher
from utils.cache import (
    BatchCache,
    DiskCache,
    EvidenceCache,
    SerpCache,
    cached_serp,
    lookup_key,
    http_disk_key,
    llm_disk_key,
    serp_disk_key,
    serp_key,
    set_active_evidence_cache,
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _NoSearch:
    async def search(self, q, num_results=5, *, country=None):
        return []


class _EmptyLLM:
    async def extract_json(self, s, u, **k):
        return {}

    async def aclose(self):
        pass


class _RecordingClient:
    """Stands in for ``AsyncAzureOpenAI`` and keeps every request's kwargs."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on

        class _Completions:
            @staticmethod
            async def create(**kwargs):
                self.calls.append(kwargs)
                if self.fail_on and self.fail_on in kwargs:
                    raise RuntimeError(
                        f"Unrecognized request argument supplied: {self.fail_on}"
                    )
                return type("R", (), {"choices": [
                    type("C", (), {"message": type("M", (), {
                        "content": '{"ok": true}'})()})()
                ]})()

        self.chat = type("Chat", (), {"completions": _Completions()})()

    async def close(self):
        pass


def _orch(**clients) -> Orchestrator:
    st = Settings()
    base: dict[str, Any] = {
        "ror": clients.pop("ror", None),
        "lei": clients.pop("lei", None) or MockLEIClient(st),
        "search": clients.pop("search", None) or _NoSearch(),
        "page_fetcher": clients.pop("page_fetcher", None) or MockPageFetcher(),
        "llm": clients.pop("llm", None) or _EmptyLLM(),
    }
    base.update(clients)
    return Orchestrator(st, mock_clients={k: v for k, v in base.items() if v})


async def _run(orch: Orchestrator, **record_kw):
    rec = EnrichmentRecord(record_id="t", country="US", **record_kw)
    resp = await orch.enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


def _patch_registry(monkeypatch, module, handler) -> None:
    """Point *module*'s httpx client at a MockTransport running *handler*."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        kwargs.pop("headers", None)
        return real(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


@pytest.fixture(autouse=True)
def _clear_registry_caches():
    clear_ror_cache()
    clear_lei_cache()
    yield
    clear_ror_cache()
    clear_lei_cache()


# ---------------------------------------------------------------------------
# Fix A — deterministic LLM calls
# ---------------------------------------------------------------------------

class TestEveryLLMCallIsPinned:
    """Every call that gates a decision carries the same three parameters.

    `temperature=0` alone is not determinism: it makes the sampler pick the
    arg-max, but a tie between two equally-likely tokens is still broken by the
    service. `seed` is what asks for a reproducible sampling path and `top_p=1`
    removes nucleus truncation as a second source of drift.
    """

    @pytest.mark.asyncio
    async def test_temperature_top_p_and_seed_are_all_sent(self):
        client = _RecordingClient()
        await call_openai("sys", "user", client=client)
        sent = client.calls[0]
        assert sent["temperature"] == LLM_TEMPERATURE == 0.0
        assert sent["top_p"] == LLM_TOP_P == 1.0
        assert sent["seed"] == LLM_SEED

    @pytest.mark.asyncio
    async def test_the_seed_is_fixed_not_derived_from_anything(self):
        """The same seed on every call, whatever the prompt. A seed derived
        from the record, the clock or a counter would be no seed at all."""
        client = _RecordingClient()
        await call_openai("sys", "record A", client=client)
        await call_openai("sys", "record B", client=client)
        assert {c["seed"] for c in client.calls} == {LLM_SEED}

    @pytest.mark.asyncio
    async def test_a_rejected_seed_is_caught_once_and_never_re_sent(self):
        """The prompt's rule: catch it once, log it, proceed at temperature 0.
        A per-call probe would pay a guaranteed 400 on every record."""
        reset_seed_support()
        try:
            client = _RecordingClient(fail_on="seed")
            await call_openai("sys", "first", client=client)
            # First call: attempted with the seed, retried without it.
            assert "seed" in client.calls[0]
            assert "seed" not in client.calls[1]
            assert client.calls[1]["temperature"] == 0.0
            assert seed_supported() is False

            await call_openai("sys", "second", client=client)
            assert "seed" not in client.calls[2]
            assert len(client.calls) == 3  # one call, not a probe plus a retry
        finally:
            reset_seed_support()

    @pytest.mark.asyncio
    async def test_a_real_failure_is_not_swallowed_as_a_seed_problem(self):
        client = _RecordingClient(fail_on="messages")  # always present
        with pytest.raises(RuntimeError):
            await call_openai("sys", "user", client=client)


class TestComponentsAreByteIdenticalAcrossRuns:
    """Fix A's acceptance criterion: each LLM-calling component, run twice
    against the same fixture, produces byte-identical output."""

    @staticmethod
    def _twice(coro_factory):
        async def _go():
            return await coro_factory(), await coro_factory()

        return asyncio.get_event_loop().run_until_complete(_go())

    @pytest.mark.asyncio
    async def test_tier3_including_its_confidence_self_report(self):
        """The three rows that flipped between `self_high` and `self_medium`
        across the two runs flipped HERE — the confidence field is part of the
        output, not metadata about it, because `finalise` drops a Tier 3
        department guess unless the confidence is high."""
        llm = MockOpenAIClient()

        async def _call():
            return await run_tier3(
                record_id="t", name1="Chemspeed Technologies", name2=None,
                name3=None, contact=None, street="123 Main St", city="Boston",
                state="MA", zip_code="02108", country="US", llm_client=llm,
            )

        first, second = await _call(), await _call()
        assert first.confidence == second.confidence
        assert json.dumps(first.__dict__, sort_keys=True) == json.dumps(
            second.__dict__, sort_keys=True,
        )

    @pytest.mark.asyncio
    async def test_company_canonical(self):
        llm = MockOpenAIClient()

        async def _call():
            return await run_company_canonical(
                record_id="t", name1="Pfizer Inc", llm_client=llm,
                city="New York", state="NY", country="US",
            )

        first, second = await _call(), await _call()
        assert json.dumps(first.__dict__, sort_keys=True) == json.dumps(
            second.__dict__, sort_keys=True,
        )

    @pytest.mark.asyncio
    async def test_the_whole_pipeline_for_one_record(self):
        """Two orchestrators, same fixtures, byte-identical output rows."""
        async def _once():
            result = await _run(
                _orch(llm=MockOpenAIClient()),
                name1="Chemspeed Technologies Inc",
                city="Boston", state="MA", zip="02108",
            )
            payload = result.model_dump(by_alias=True)
            # `duration_ms` measures wall clock and is deliberately excluded:
            # it is the one output that SHOULD differ between runs, and the
            # reproducibility gate excludes it for the same reason.
            payload.pop("duration_ms", None)
            payload.pop("provenance", None)
            payload.pop("provenance_rejected", None)
            return json.dumps(payload, sort_keys=True, default=str)

        assert await _once() == await _once()


class TestPromptsCarryNothingNondeterministic:
    @pytest.mark.asyncio
    async def test_injected_snippets_are_sorted_by_a_stable_key(self):
        """Fix A(3). The person-affiliation lane injects SERP snippets into its
        prompt. Two runs that retrieve the same five results in a different
        order must build the SAME prompt, or temperature 0 buys nothing."""
        from enrichment.person_affiliation import run_person_affiliation
        from search.base import SearchResult

        hits = [
            SearchResult(title="C", url="https://c.example/p", snippet="c"),
            SearchResult(title="A", url="https://a.example/p", snippet="a"),
            SearchResult(title="B", url="https://b.example/p", snippet="b"),
        ]

        class _Search:
            def __init__(self, results):
                self.results = results

            async def search(self, q, num_results=5, *, country=None):
                return list(self.results)

        prompts: list[str] = []

        class _Capture:
            async def extract_json(self, system, user, **kw):
                prompts.append(user)
                return {"institution": None, "confidence": "low"}

        for order in (hits, list(reversed(hits)), [hits[1], hits[2], hits[0]]):
            await run_person_affiliation(
                contact="Jane Smith", city="Boston", region="MA",
                country="US", email=None, search_client=_Search(order),
                llm_client=_Capture(), settings=Settings(),
            )

        assert len(set(prompts)) == 1, "the injected evidence order leaked"

    def test_no_prompt_template_carries_a_timestamp_or_run_id(self):
        """Structural. A template that interpolated the clock or a run id
        would make every call unique and every cache useless."""
        import llm.prompts as prompts

        banned = ("{now", "{today", "{timestamp", "{run_id", "{record_id",
                  "{uuid", "{date}")
        for attr in dir(prompts):
            if not (attr.endswith("_PROMPT") or attr.endswith("_TEMPLATE")):
                continue
            text = getattr(prompts, attr)
            if not isinstance(text, str):
                continue
            for token in banned:
                assert token not in text, f"{attr} interpolates {token}"


# ---------------------------------------------------------------------------
# Fix B — the shared, persistent, freezable evidence cache
# ---------------------------------------------------------------------------

class TestCacheKeysArePureFunctionsOfTheRequest:
    def test_a_key_is_the_same_on_every_call(self):
        for _ in range(3):
            assert lookup_key("Coastal Diagnostics, Inc.", "US") == (
                "coastal diagnostics inc", "US",
            )
            assert serp_key(
                '"Acme" official website', "US", provider="serpapi",
            ) == ("acme official website", True, "US", "serpapi")

    def test_a_key_is_built_only_from_the_request(self):
        """The property that makes a second run HIT rather than miss.

        Asserted the only way that means anything: build each key twice with
        the SAME request and different ambient state, and require the two to
        be equal. A key that reached for the clock, a run id or a record id
        would differ.
        """
        from utils.cache import current_record_id

        def build() -> list[str]:
            return [
                serp_disk_key(
                    '"Acme Labs" official website', "US", provider="serpapi",
                ),
                http_disk_key(
                    "https://api.ror.org/v2/organizations",
                    {"query": "Univ of Florida", "filter": "cc:US"},
                ),
                llm_disk_key(
                    deployment="gpt-5.4", api_version="2024-08-01-preview",
                    temperature=0.0, top_p=1.0, seed=LLM_SEED,
                    max_tokens=1024, system_prompt="sys", user_prompt="usr",
                ),
                DiskCache._key("Acme.com."),
            ]

        token = current_record_id.set("record-A")
        first = build()
        current_record_id.reset(token)
        token = current_record_id.set("record-B")
        second = build()
        current_record_id.reset(token)

        assert first == second
        # And nothing that looks like a year got in on either pass.
        assert not any(
            str(y) in k for k in first for y in range(2020, 2036)
        )

    def test_the_llm_key_changes_when_anything_that_could_change_the_answer_does(self):
        """The other half: an entry must never be served to a request that is
        not identical. Editing a prompt template invalidates every entry that
        used it, which is correct — the recorded answer answered a different
        question."""
        base = dict(
            deployment="gpt-5.4", api_version="2024-08-01-preview",
            temperature=0.0, top_p=1.0, seed=LLM_SEED, max_tokens=1024,
            system_prompt="sys", user_prompt="usr",
        )
        keys = {llm_disk_key(**base)}
        for field, value in (
            ("deployment", "gpt-4o"), ("api_version", "2025-04-01-preview"),
            ("temperature", 0.7), ("top_p", 0.9), ("seed", None),
            ("max_tokens", 512), ("system_prompt", "sys "),
            ("user_prompt", "usr "),
        ):
            keys.add(llm_disk_key(**{**base, field: value}))
        assert len(keys) == 9, "two different requests share a key"

    def test_the_quoted_and_unquoted_forms_stay_distinct(self):
        """Website resolver §8's retry exists to escape the phrase results;
        a key that collapsed the quoting would serve it those very results."""
        assert serp_disk_key(
            '"Acme" official website', provider="serpapi",
        ) != serp_disk_key("Acme official website", provider="serpapi")


class TestEntriesAreImmutableAndDated(object):
    def test_a_recorded_entry_is_never_rewritten(self, tmp_path):
        cache = DiskCache(tmp_path, prefix="page")
        cache.set("acme.com", {"text": "first"})
        first_date = cache.fetched_at("acme.com")

        # A later run reads the same page and gets something else. The
        # recording does not move.
        second = DiskCache(tmp_path, prefix="page")
        second.set("acme.com", {"text": "second"})
        assert second.get("acme.com") == {"text": "first"}
        assert second.fetched_at("acme.com") == first_date

    def test_the_extraction_date_comes_from_the_entry_not_the_run(self, tmp_path):
        """Fix B(4), and what the provenance migration did to it.

        Eleven rows of the two diffed runs differed in nothing but the
        operating-name provenance string, because it was stamped with
        `date.today()` whether or not the page had actually been re-read.
        Fix B pinned the stamp to the cache entry. The migration then removed
        the date from the exported column altogether — a token that decays is
        read as part of the claim — so the column is now reproducible by
        construction rather than by care.

        Both halves are still worth pinning. The entry is still what holds the
        fetch date, because that is what the `operating_name_extracted` trace
        line quotes; and the string no longer varies with it at all.
        """
        path = tmp_path / "page_acme.com.json"
        path.write_text(json.dumps({
            "domain": "acme.com",
            "fetched_at": "2026-01-15",
            "payload": {"text": "x"},
        }), encoding="utf-8")

        cache = DiskCache(tmp_path, prefix="page")
        # The date still comes off the entry — the trace needs it.
        assert cache.fetched_at("acme.com") == "2026-01-15"
        # And the exported column no longer carries it, so no run date and no
        # entry date can move it.
        assert operating_name_provenance("acme.com") == (
            "web:acme.com:provisional"
        )

    def test_a_legacy_entry_without_a_date_falls_back_to_the_file_date(
        self, tmp_path,
    ):
        """Entries recorded before Fix B carry no `fetched_at`. The file's own
        modification date is still the day the fetch happened, and is still the
        same on the next run — which is what the provenance string needs."""
        import os
        from datetime import date

        path = tmp_path / "page_old.example.json"
        path.write_text(json.dumps({
            "domain": "old.example", "payload": {"text": "x"},
        }), encoding="utf-8")
        stamp = 1_700_000_000  # 2023-11-14 UTC
        os.utime(path, (stamp, stamp))

        cache = DiskCache(tmp_path, prefix="page")
        assert cache.fetched_at("old.example") == date.fromtimestamp(
            stamp,
        ).isoformat()


class TestTheCacheIsSharedAndSurvivesTheProcess:
    def test_serp_results_survive_a_new_cache_object(self, tmp_path):
        from search.base import SearchResult

        store = DiskCache(tmp_path, prefix="serp", namespace="serp")
        first = SerpCache(disk=store)
        first.set(
            "acme labs", [SearchResult("T", "https://acme.example", "s")], "US",
            provider="serpapi",
        )

        # A second run: new in-memory cache, same directory.
        second = SerpCache(disk=DiskCache(tmp_path, prefix="serp", namespace="serp"))
        hit = second.get("acme labs", "US", provider="serpapi")
        assert hit is not None
        assert hit[0].url == "https://acme.example"

    def test_every_namespace_lives_under_one_root(self, tmp_path):
        cache = EvidenceCache(tmp_path)
        for name in ("page", "wikidata", "serp", "ror", "gleif", "fetch"):
            store = cache.namespace(name)
            assert store._dir is not None
            assert tmp_path in store._dir.parents or store._dir == tmp_path

    def test_an_explicitly_empty_directory_means_memory_only(self, tmp_path):
        """Not the same answer as "no directory given". A lane a caller asked
        to keep off disk must stay off it rather than inherit the root."""
        cache = EvidenceCache(tmp_path)
        assert cache.namespace("page", directory="")._dir is None


class TestTheCacheHoldsEvidenceNotConclusions:
    @pytest.mark.asyncio
    async def test_a_registry_response_is_recorded_and_re_decided(
        self, monkeypatch, tmp_path,
    ):
        """A frozen cache must fix the EVIDENCE, not the code's conclusion.

        Freezing `dedup/weights.json` fixes the inputs so a change to the
        logic can be measured against them; a cache that stored the finished
        match dict would fix the logic instead, and a change to the selection
        rules would have no effect on a warm run. So the registry's raw body
        is what is recorded, and every guard is re-applied on every run.
        """
        # Counted per REQUEST, not in total: raising the threshold makes the
        # exact path find nothing verified, so the client goes on to the fuzzy
        # endpoint — a different request, legitimately issued. What must not
        # happen is the recorded one being fetched again.
        calls: Counter[str] = Counter()
        records = [_lei_record("LEI0000000000000001", "NORTH STAR LABS INC")]

        def handler(request):
            calls[request.url.path] += 1
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        cache = EvidenceCache(tmp_path)
        set_active_evidence_cache(cache)
        try:
            first = await call_lei("North Star Labs Inc", country_code="US")
            assert first["matched"] is True
            exact_calls = calls["/api/v1/lei-records"]
            assert exact_calls == 1

            # A second process: fresh memory caches, same directory. No call.
            clear_lei_cache()
            second = await call_lei("North Star Labs Inc", country_code="US")
            assert calls["/api/v1/lei-records"] == exact_calls
            assert second["lei_id"] == first["lei_id"]

            # Now the GUARD changes. The evidence is frozen; the decision is
            # not, and the same recorded body must now be refused.
            clear_lei_cache()
            third = await call_lei(
                "North Star Labs Inc", country_code="US", threshold=101.0,
            )
            assert calls["/api/v1/lei-records"] == exact_calls, (
                "re-deciding must not re-fetch the recorded response"
            )
            assert third["matched"] is False
        finally:
            set_active_evidence_cache(None)


class TestCacheFrozenIsAnEvaluationControl:
    @pytest.mark.asyncio
    async def test_a_frozen_miss_makes_no_call_and_is_recorded(self, tmp_path, caplog):
        store = DiskCache(tmp_path, prefix="serp", namespace="serp",
                          replay_only=True)
        batch = BatchCache(shared_serp=SerpCache(disk=store))

        class _Boom:
            async def search(self, q, num_results=5, *, country=None):
                raise AssertionError("a frozen run must not reach the network")

        with caplog.at_level("INFO"):
            results = await cached_serp(batch, _Boom(), "acme labs")

        assert results == []
        assert store.frozen_misses == 1
        assert any(
            isinstance(r.msg, dict) and r.msg.get("step") ==
            "evidence-unavailable-frozen"
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_a_frozen_hit_is_served_normally(self, tmp_path):
        from search.base import SearchResult

        class _Boom:
            provider_id = "boom"

            async def search(self, q, num_results=5, *, country=None):
                raise AssertionError("should have been served from the cache")

        warm = DiskCache(tmp_path, prefix="serp", namespace="serp")
        SerpCache(disk=warm).set(
            "acme labs", [SearchResult("T", "u", "s")], provider="boom",
        )

        frozen = DiskCache(tmp_path, prefix="serp", namespace="serp",
                           replay_only=True)
        batch = BatchCache(shared_serp=SerpCache(disk=frozen))

        assert len(await cached_serp(batch, _Boom(), "acme labs")) == 1
        assert frozen.frozen_misses == 0

    def test_freezing_is_all_or_nothing(self, tmp_path):
        """Freezing three of five sources would not freeze the run."""
        cache = EvidenceCache(tmp_path, frozen=True)
        for name in ("page", "wikidata", "serp", "ror", "gleif", "fetch"):
            assert cache.namespace(name).replay_only is True


# ---------------------------------------------------------------------------
# Fix C — deterministic candidate selection
# ---------------------------------------------------------------------------

def _ror_item(ror_id: str, name: str, *, city: str | None = None,
              region: str | None = None,
              country_code: str = "US", country: str = "United States") -> dict:
    geo: dict[str, Any] = {
        "country_code": country_code, "country_name": country,
    }
    if city:
        geo["name"] = city
    if region:
        geo["country_subdivision_name"] = region
    return {
        "id": ror_id,
        "names": [{"value": name, "types": ["ror_display"]}],
        "types": ["company"],
        "locations": [{"geonames_details": geo}],
        "relationships": [],
        "links": [],
    }


def _lei_record(lei: str, name: str, *, country: str = "US",
                city: str | None = None, region: str | None = None,
                hq_city: str | None = None, hq_region: str | None = None,
                hq_country: str | None = None,
                status: str = "ACTIVE") -> dict:
    """A GLEIF lei-record. *hq_* fills ``headquartersAddress``.

    GLEIF publishes two addresses and the comparator reads both, so a builder
    that could only express one could not state the case that matters — an
    entity incorporated in one state and operating from another.
    """
    address: dict[str, Any] = {"country": country}
    if city:
        address["city"] = city
    if region:
        address["region"] = region
    entity: dict[str, Any] = {
        "legalName": {"name": name},
        "status": status,
        "legalAddress": address,
    }
    if hq_city or hq_region or hq_country:
        hq: dict[str, Any] = {"country": hq_country or country}
        if hq_city:
            hq["city"] = hq_city
        if hq_region:
            hq["region"] = hq_region
        entity["headquartersAddress"] = hq
    return {"id": lei, "attributes": {"entity": entity}}


class TestSelectionDoesNotDependOnResponseOrder:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
    async def test_ror_picks_the_same_org_from_a_shuffled_list(
        self, monkeypatch, seed,
    ):
        """The chemspeed defect, reproduced: ROR returns the same candidates in
        a different order and the pipeline picks a different one."""
        items = [
            _ror_item("https://ror.org/00000001", "Advanced Composites Group",
                      city="Salt Lake City"),
            _ror_item("https://ror.org/00000002", "Advanced Composites Inc",
                      city="Salt Lake City"),
            _ror_item("https://ror.org/00000003", "Advanced Composites",
                      city="Salt Lake City"),
        ]
        shuffled = list(items)
        random.Random(seed).shuffle(shuffled)

        def handler(request):
            if "affiliation" in request.url.params:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": shuffled})

        _patch_registry(monkeypatch, tier1_ror, handler)
        clear_ror_cache()
        res = await call_ror(
            "Advanced Composites Inc", country_code="US",
            country="United States", city="Salt Lake City",
        )
        # Whatever the order, the same answer — and it is an answer, not a
        # refusal, because the exact display-name match separates it from the
        # other two by more than the ambiguity margin.
        assert res["matched"] is True
        assert res["ror_id"] == "https://ror.org/00000002"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
    async def test_gleif_picks_the_same_entity_from_a_shuffled_list(
        self, monkeypatch, seed,
    ):
        records = [
            _lei_record("LEI0000000000000001", "APPLIED CATALYSTS LLC"),
            _lei_record("LEI0000000000000002", "APPLIED CATALYSTS AND TECHNOLOGIES LLC"),
            _lei_record("LEI0000000000000003", "APPLIED CATALYST HOLDINGS LLC"),
        ]
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": shuffled})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await call_lei("Applied Catalysts LLC", country_code="US")
        assert res["matched"] is True
        assert res["lei_id"] == "LEI0000000000000001"

    @pytest.mark.asyncio
    async def test_the_tiebreak_is_the_canonical_id_ascending(self, monkeypatch):
        """Two identical names. Nothing about the organisations distinguishes
        them for the scorer, so the LEI does — deterministically, and the same
        way whatever order they arrive in."""
        records = [
            _lei_record("LEI000000000000000B", "TWIN LABS INC"),
            _lei_record("LEI000000000000000A", "TWIN LABS INC"),
        ]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await call_lei("Twin Labs Inc", country_code="US")
        # Identical names score identically, so the ambiguity rule refuses
        # both — which is the stronger guarantee. The point of the tiebreak is
        # that the REFUSAL is reached the same way every time.
        assert res["matched"] is False
        assert res.get("refused_by") == "ambiguous"


class TestTruncationIsNotAHidingPlace:
    """A cap on how many candidates are examined is still a selection.

    `completions[:5]` and `items[:10]` both let the API's arrival order decide
    which candidates were even looked at — invisible to a double-run diff
    against one frozen cache, and found only by reversing the recorded order
    (`tools/shuffle_evidence.py`). It changed one record of the chemspeed
    batch.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("order", ["forward", "reversed"])
    async def test_gleif_resolves_the_same_five_completions_either_way(
        self, monkeypatch, order,
    ):
        # Eight completions: more than the resolve budget, so the truncation
        # has to choose — and its choice must not be the API's ordering.
        leis = [f"LEI00000000000000{i:04d}" for i in range(8)]
        completions = [
            {"relationships": {"lei-records": {"data": {"id": lei}}}}
            for lei in leis
        ]
        if order == "reversed":
            completions = list(reversed(completions))
        resolved: list[str] = []

        def handler(request):
            path = request.url.path
            if path.endswith("/fuzzycompletions"):
                return httpx.Response(200, json={"data": completions})
            if path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": []})  # exact: miss
            lei = path.rsplit("/", 1)[-1]
            resolved.append(lei)
            return httpx.Response(200, json={
                "data": _lei_record(lei, "SOMETHING ELSE ENTIRELY LLC"),
            })

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        await call_lei("Northstar Instruments Inc", country_code="US")
        assert resolved == sorted(leis)[:5]


class TestANearTieIsANoMatch:
    def test_the_margin_is_one_documented_constant(self):
        assert REGISTRY_AMBIGUITY_MARGIN == 2.0

    @pytest.mark.asyncio
    async def test_gleif_refuses_two_candidates_within_the_margin(
        self, monkeypatch,
    ):
        records = [
            _lei_record("LEI0000000000000001", "NORTH STAR LABS INC"),
            _lei_record("LEI0000000000000002", "NORTH STAR LABS LLC"),
        ]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await call_lei("North Star Labs Inc", country_code="US")
        assert res["matched"] is False
        assert res["refused_by"] == "ambiguous"
        assert any(
            r["guard"] == "registry_ambiguity"
            for r in res.get("guard_rejections") or ()
        )

    @pytest.mark.asyncio
    async def test_a_clear_winner_is_still_accepted(self, monkeypatch):
        """The margin refuses ties, not matches. Loosening nothing means
        accepting nothing new — and refusing nothing that was distinguished."""
        records = [
            _lei_record("LEI0000000000000001", "NORTH STAR LABS INC"),
            _lei_record("LEI0000000000000002", "SOUTHERN CROSS CHEMICALS PLC"),
        ]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await call_lei("North Star Labs Inc", country_code="US")
        assert res["matched"] is True
        assert res["lei_id"] == "LEI0000000000000001"


class TestTheShortNameGuard:
    @pytest.mark.parametrize("name,expected", [
        ("BHS", True),
        ("BIC Corp", True),
        ("3M", True),
        ("NABCO", True),
        ("LARGO MEDICAL CTR", False),
        ("Pfizer Inc", False),
        ("Massachusetts Institute of Technology", False),
    ])
    def test_which_names_are_collision_prone(self, name, expected):
        assert is_collision_prone(name) is expected

    @pytest.mark.asyncio
    async def test_bhs_resolves_to_neither_expansion(self, monkeypatch):
        """The named BHS case. Two plausible expansions, both in the wrong
        city, and the batch matched a different one on each run. It must
        resolve to NEITHER — not to the first, and not to the higher score."""
        items = [
            _ror_item("https://ror.org/0bhs00001", "Berkshire Health Systems",
                      city="Pittsfield"),
            _ror_item("https://ror.org/0bhs00002", "Behavioral Health Systems",
                      city="Birmingham"),
        ]

        def handler(request):
            if "affiliation" in request.url.params:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": items})

        _patch_registry(monkeypatch, tier1_ror, handler)
        clear_ror_cache()
        res = await call_ror(
            "BHS", country_code="US", country="United States",
            city="Nampa", state="ID",
        )
        assert res["matched"] is False
        assert res.get("ror_id") is None

    @pytest.mark.asyncio
    async def test_a_short_name_IS_accepted_when_the_city_agrees(
        self, monkeypatch,
    ):
        """The guard asks for a second signal; it does not ban short names.
        With the locality agreeing, the match stands."""
        items = [_ror_item("https://ror.org/0bhs00001", "BHS", city="Nampa")]

        def handler(request):
            if "affiliation" in request.url.params:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": items})

        _patch_registry(monkeypatch, tier1_ror, handler)
        clear_ror_cache()
        res = await call_ror(
            "BHS", country_code="US", country="United States",
            city="Nampa", state="ID",
        )
        assert res["matched"] is True
        assert res["corroborated_by"] == "location"

    @pytest.mark.asyncio
    async def test_a_short_name_is_accepted_when_the_domain_agrees(
        self, monkeypatch,
    ):
        items = [{
            **_ror_item("https://ror.org/0bic00001", "BIC"),
            "links": [{"type": "website", "value": "https://www.bic.com/"}],
        }]

        def handler(request):
            if "affiliation" in request.url.params:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": items})

        _patch_registry(monkeypatch, tier1_ror, handler)
        clear_ror_cache()
        res = await call_ror(
            "BIC", country_code="US", country="United States",
            record_domain="bic.com",
        )
        assert res["matched"] is True
        assert res["corroborated_by"] == "domain"


# ---------------------------------------------------------------------------
# Fix D — cross-source consistency
# ---------------------------------------------------------------------------

class _StubROR:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload

    async def call(self, name, country_code=None, country=None, city=None,
                   state=None, **_ctx):
        if self.payload is None:
            return {"matched": False, "score": 0.0}
        return {"matched": True, "score": 1.0, **self.payload}


class _StubLEI:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload

    async def call(self, name, country_code=None, **_ctx):
        if self.payload is None:
            return {"matched": False, "strategy": "fuzzy", "score": 0.0}
        return {"matched": True, "strategy": "exact", "confidence": "high",
                "score": 96.0, **self.payload}

    async def call_by_id(self, lei, query_name, country_code=None, **_ctx):
        return {"matched": False, "strategy": "by_id", "score": 0.0}


#: The BIC Corp row, as it appeared in the two diffed runs: GLEIF returned the
#: right company and ROR returned a different one (Centene on run 1, Balchem on
#: run 2). Both were written; nothing compared them.
_BIC_ROR = {
    "ror_id": "https://ror.org/0centene1",
    "official_name": "Centene Corporation",
    "org_types": ["company"],
    "is_research_institution": False,
    "domain": "centene.com",
    "website": "https://www.centene.com",
    "acronym": None,
    "children": [],
    "country": "United States",
    "location_verdict": "neutral",
}
_BIC_GLEIF = {
    "lei_id": "LEI00BIC0000000000001",
    "legal_name": "BIC CORPORATION",
    "country": "US",
    "status": "ACTIVE",
    "location_verdict": "consistent",
}


class TestATITradingRejectsTheWrongOwnerDomain:
    """The named ATI Trading case, as a fixture.

    Run 1 of the two diffed runs read `american-trading.com`, extracted a
    location contradiction, and rejected the candidate. Run 2 did not extract
    it, accepted the wrong-owner domain unflagged, and the corroborator then
    promoted Name 1 to `input:1:verified` on the strength of that domain.
    Nothing about the record differed — only what the page reader returned.

    The row itself is NOT in `docs/thesis/chemspeed_us_100.xlsx` (that workbook
    runs "1st Source Research" … "ATC Automation"), so it cannot be asserted on
    the batch. It is asserted here instead, spelled as it appeared.
    """

    @staticmethod
    def _fixture():
        from enrichment.orchestrator import _init_result
        from enrichment.provenance import deterministic_evidence
        from tests.test_page_corroborator import _Fetcher, _Reader, _page

        settings = Settings()
        object.__setattr__(settings, "page_fixture_dir", "")
        orch = Orchestrator(settings, mock_clients={
            "ror": _StubROR(None), "lei": _StubLEI(None),
            "search": _NoSearch(), "llm": _EmptyLLM(),
        })
        record = EnrichmentRecord(
            record_id="ATI", name1="ATI Trading", country="US",
            city="Elizabeth", state="NJ",
        )
        result = _init_result(record)
        result.write(
            "name1_enriched", "ATI Trading",
            deterministic_evidence("test", producer="input", tier=1),
        )
        result.write(
            "domain", "american-trading.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        result["domain_verified_by"] = "name"
        orch._page_fetcher = _Fetcher({
            "https://american-trading.com/": _page(
                "https://american-trading.com/",
                text="American Trading International " * 30,
            ),
        })
        orch._llm_client = _Reader({
            "stated_org_name": "American Trading International, Inc.",
            "stated_city": "Torrance", "stated_region": "CA",
            "legal_form_present": True,
        })
        return orch, result, record

    @pytest.mark.asyncio
    async def test_the_wrong_owner_domain_is_withdrawn_with_its_reason(self):
        orch, result, record = self._fixture()
        await orch._corroborate_domain(record, result)

        assert result["domain"] is None
        assert result["domain_rejected"] is True
        assert result["_domain_unverified"] == "american-trading.com"
        note = result["_domain_page_note"]
        assert "American Trading International, Inc." in note
        assert "Torrance" in note
        # The location contradiction, named — that is what separates this from
        # a brand-vs-legal-name difference, which never withdraws a domain.
        assert "different state or country" in note

    @pytest.mark.asyncio
    async def test_the_reason_survives_into_the_shipped_flag(self):
        import time

        from enrichment.orchestrator import finalise

        orch, result, record = self._fixture()
        await orch._corroborate_domain(record, result)
        finalise(result, time.monotonic())

        assert "domain-unverified" in result["flag_codes"]
        reason = result["flag_reason"]
        assert "american-trading.com" in reason
        assert "American Trading International, Inc." in reason

    @pytest.mark.asyncio
    async def test_a_withdrawn_domain_never_verifies_name_1(self):
        """Run 2's second failure: having accepted the wrong domain, the
        corroborator promoted Name 1 to `input:1:verified` on its strength.
        A domain the page read refuted must corroborate nothing."""
        import time

        from enrichment.orchestrator import finalise

        orch, result, record = self._fixture()
        await orch._corroborate_domain(record, result)
        finalise(result, time.monotonic())

        assert not (result.get("name1_provenance") or "").endswith(":verified")


class TestNoRecordShipsTwoIdentities:
    @pytest.mark.asyncio
    async def test_bic_keeps_gleif_nulls_ror_and_flags_the_conflict(self):
        """Fix D's acceptance criterion, on the named record."""
        result = await _run(
            _orch(ror=_StubROR(_BIC_ROR), lei=_StubLEI(_BIC_GLEIF)),
            name1="BIC Corp", city="Milford", state="CT",
        )
        assert result.lei_id == "LEI00BIC0000000000001"
        # GLEIF's "BIC CORPORATION", after UC 17's legal-suffix collapse and
        # output casing — the record's own identity, not Centene's.
        assert result.name1_enriched == "BIC Corp"
        assert result.ror_id is None
        assert result.domain is None
        assert SOURCE_CONFLICT in result.flag_codes
        reason = result.flag_reason or ""
        assert "BIC CORPORATION" in reason and "Centene Corporation" in reason

    @pytest.mark.asyncio
    async def test_the_bic_search_term_comes_from_the_surviving_identity(self):
        """Fix D(3). Search Term 1 was rewritten from the ROR entity that lost
        the consistency check — BALCHEM on one run, from a company the record
        has nothing to do with."""
        result = await _run(
            _orch(ror=_StubROR(_BIC_ROR), lei=_StubLEI(_BIC_GLEIF)),
            name1="BIC Corp", city="Milford", state="CT",
        )
        assert result.search_term_1 is not None
        assert "CENTENE" not in result.search_term_1
        assert result.search_term_1.startswith("BIC")

    @pytest.mark.asyncio
    async def test_two_agreeing_sources_are_left_alone(self):
        """The gate acts on disagreement only. A record whose registries agree
        keeps both identifiers and carries no flag from this rule."""
        ror = dict(_BIC_ROR, official_name="BIC Corporation",
                   domain="bic.com", website="https://bic.com")
        result = await _run(
            _orch(ror=_StubROR(ror), lei=_StubLEI(_BIC_GLEIF)),
            name1="BIC Corp", city="Milford", state="CT",
        )
        assert result.ror_id == "https://ror.org/0centene1"
        assert result.lei_id == "LEI00BIC0000000000001"
        assert SOURCE_CONFLICT not in result.flag_codes


class TestTheLocalityComparatorDoesNotInventDisagreements:
    """Silence is neutral, and so is the same place written two ways."""

    @pytest.mark.parametrize("stated,record", [
        ("United States", "US"),
        ("USA", "United States"),
        ("us", "United States"),
    ])
    def test_two_spellings_of_one_country_are_not_a_contradiction(
        self, stated, record,
    ):
        """Measured: six ROR matches were reported as contradicting their own
        record, because ROR states `country_name` ("United States") and the
        SAP record carries the ISO code ("US")."""
        from enrichment.locality import NEUTRAL, compare_locality

        verdict, _, _ = compare_locality(
            stated_country=stated, country=record,
        )
        assert verdict == NEUTRAL

    def test_a_different_country_still_contradicts(self):
        from enrichment.locality import CONTRADICTED, compare_locality

        verdict, _, scope = compare_locality(
            stated_country="Germany", country="US",
        )
        assert (verdict, scope) == (CONTRADICTED, "country")

    def test_a_us_state_code_is_expanded_in_the_reason(self):
        """"DE" on a US batch is Delaware far more often than Germany, and a
        reviewer should not have to work that out from the flag."""
        from enrichment.locality import compare_locality

        _, detail, _ = compare_locality(stated_region="DE", region="NJ")
        assert "DE (Delaware)" in detail and "NJ (New Jersey)" in detail


class TestTheRegistryLocationCheck:
    @pytest.mark.asyncio
    async def test_a_contradicting_locality_is_flagged_not_discarded(self):
        """Same-country relocations are common, so the match stands and the
        reviewer is told where the registry thinks the organisation is."""
        lei = dict(
            _BIC_GLEIF,
            location_verdict="contradicted",
            location_detail="states city Newark; record says Milford",
            location_scope="city",
        )
        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(lei)),
            name1="Bicorporate Products Inc", city="Milford", state="CT",
        )
        assert result.lei_id == "LEI00BIC0000000000001"
        assert REGISTRY_LOCATION_MISMATCH in result.flag_codes
        assert "Newark" in (result.flag_reason or "")
        assert "Milford" in (result.flag_reason or "")

    @pytest.mark.asyncio
    async def test_a_neutral_locality_raises_nothing(self):
        """Absence is not a signal — the rule the whole comparator rests on."""
        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(_BIC_GLEIF)),
            name1="Bicorporate Products Inc", city="Milford", state="CT",
        )
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes


class TestTheFlagVocabularyGrewByExactlyTwo:
    def test_only_the_two_authorised_codes_were_added(self):
        from enrichment.flags import ALL_CODES

        assert set(ALL_CODES) == {
            "no-match", "dept-via-lab",
            "person-unresolved", "overflow", "opaque-code",
            "domain-unverified", "email-conflict", "name3-not-demoted",
            "multiple-contacts", "unverified-inference", "entity-superseded",
            # The two this work authorised, and nothing else.
            "source-conflict", "registry-location-mismatch",
            # `low-confidence-unchanged` was RETIRED by the provenance
            # migration — it said exactly what `input:low` on the field says,
            # and the flag is now derived from that. Its prose survives on the
            # derived flag; the token cannot be emitted again. See
            # tests/test_provenance_scheme_b.py::TestTheRetiredCode.
        }


class TestTheRegistryLocationTriggerIsAConjunction:
    """`registry-location-mismatch` needs a contradicted address AND a weak name.

    The flag was firing on a contradicted address alone, and on the 101-row
    chemspeed batch that made it the second most common code on the sheet — 19
    rows, of which eleven read "GLEIF states region DE (Delaware)". Delaware is
    where a US company is incorporated, not where it is; the flag was reporting
    the American corporate-registration system rather than a doubt about any
    record. A code that fires on the normal case is a code reviewers learn to
    clear unread, which costs the rows that were worth reading.

    Two changes, both narrowing:

    * the comparison runs against EVERY address the registry publishes, and
      agreement with any one of them is agreement (AdvanSix: legal address
      Wilmington DE, headquarters Parsippany NJ, record NJ);
    * a contradiction is raised as a flag only when the NAME match was below
      exact tier. Where the record states the registry's name verbatim the
      entity is identified, and a disagreeing address is a fact about its
      geography — counted in the trace, not flagged on the row.
    """

    # ── (a) two addresses, one of them agreeing ──────────────────────────
    def test_gleif_extracts_both_the_legal_and_the_headquarters_address(self):
        from enrichment.tier1_lei import _record_fields

        fields = _record_fields(_lei_record(
            "LEI0000000000000001", "ADVANSIX INC.",
            city="Wilmington", region="US-DE",
            hq_city="Parsippany", hq_region="US-NJ",
        ))
        assert [
            (a["kind"], a["city"], a["region"]) for a in fields["addresses"]
        ] == [
            ("legal", "Wilmington", "DE"),
            ("headquarters", "Parsippany", "NJ"),
        ]
        # The flat keys still mean the LEGAL address — the country guard reads
        # `country` and must keep filtering on what GLEIF filters on.
        assert (fields["city"], fields["region"]) == ("Wilmington", "DE")

    def test_one_identical_address_is_not_counted_twice(self):
        """Most entities state the same place twice. Two copies of one
        statement are not two pieces of evidence."""
        from enrichment.tier1_lei import _record_fields

        fields = _record_fields(_lei_record(
            "LEI0000000000000001", "ARKEMA INC.",
            city="King of Prussia", region="US-PA",
            hq_city="King of Prussia", hq_region="US-PA",
        ))
        assert len(fields["addresses"]) == 1

    def test_agreement_with_either_address_is_agreement(self):
        """DE on the legal address, TX on the headquarters, TX on the record.

        The legal address naming Delaware is not evidence against Texas: the
        two addresses are not competing claims about one place, they are two
        true statements about one entity.
        """
        from enrichment.locality import CONSISTENT, compare_registry_addresses

        verdict, detail, scope, notes = compare_registry_addresses(
            [
                {"kind": "legal", "city": "Wilmington", "region": "DE",
                 "country": "US"},
                {"kind": "headquarters", "city": "Houston", "region": "TX",
                 "country": "US"},
            ],
            city="Houston", region="TX", country="US",
        )
        assert verdict == CONSISTENT
        assert scope in ("city", "region")
        assert notes == []
        assert detail

    @pytest.mark.asyncio
    async def test_a_de_legal_tx_hq_entity_is_not_flagged_on_a_tx_record(
        self, monkeypatch,
    ):
        """(a), end to end: the row ships the LEI and no address advisory."""
        records = [_lei_record(
            "LEI0000000000000001", "GULF COAST POLYMERS INCORPORATED",
            city="Wilmington", region="US-DE",
            hq_city="Houston", hq_region="US-TX",
        )]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Gulf Coast Polymers Incorporated", country_code="US",
            city="Houston", state="TX",
        )
        assert res["matched"] is True
        assert res["location_verdict"] == "consistent"

        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(res)),
            name1="Gulf Coast Polymers Incorporated",
            city="Houston", state="TX",
        )
        assert result.lei_id == "LEI0000000000000001"
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes

    # ── region as a selection input ──────────────────────────────────────
    @pytest.mark.asyncio
    async def test_region_picks_between_two_candidates_the_name_cannot(
        self, monkeypatch,
    ):
        """Two entities, one name, two states — the record's region decides.

        Name verification is a GATE: both of these clear it, and neither is
        more "exact" than the other. What tells them apart is that the
        registry puts one of them where the record is. Asking the region only
        AFTER a winner was picked meant the coin-flip happened first and the
        region was left to complain about the result.
        """
        records = [
            _lei_record("LEI000000000000000A", "CARGILL INCORPORATED",
                        city="Minneapolis", region="US-MN"),
            _lei_record("LEI000000000000000B", "CARGILL INCORPORATED",
                        city="Dayton", region="US-OH"),
        ]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Cargill Incorporated", country_code="US",
            city="Dayton", state="OH",
        )
        assert res["matched"] is True
        assert res["lei_id"] == "LEI000000000000000B"
        assert res["location_verdict"] == "consistent"

    @pytest.mark.asyncio
    async def test_the_headquarters_region_can_be_what_agrees(
        self, monkeypatch,
    ):
        """Both addresses count in selection, exactly as in the verdict.

        The candidate that wins is incorporated in Delaware like everything
        else and OPERATES from the record's state; the loser is wholly in
        another one. Reading the legal address alone would rank them equal
        and fall through to the LEI tiebreak.
        """
        records = [
            _lei_record("LEI000000000000000A", "ACME POLYMERS INCORPORATED",
                        city="Albany", region="US-NY"),
            _lei_record("LEI000000000000000Z", "ACME POLYMERS INCORPORATED",
                        city="Wilmington", region="US-DE",
                        hq_city="Houston", hq_region="US-TX"),
        ]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Acme Polymers Incorporated", country_code="US",
            city="Houston", state="TX",
        )
        # Z sorts last on the LEI tiebreak and wins anyway, on its headquarters.
        assert res["lei_id"] == "LEI000000000000000Z"

    @pytest.mark.asyncio
    async def test_a_record_without_a_region_ranks_exactly_as_before(
        self, monkeypatch,
    ):
        """Silence is not agreement. A record stating no region must leave the
        order it had, or this becomes a behaviour change on every row that
        happens to have an empty Region cell."""
        records = [
            _lei_record("LEI000000000000000A", "ACME POLYMERS INCORPORATED",
                        city="Albany", region="US-NY"),
            _lei_record("LEI000000000000000B", "ACME POLYMERS INCORPORATED",
                        city="Houston", region="US-TX"),
        ]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Acme Polymers Incorporated", country_code="US",
            city=None, state=None,
        )
        # Two identical names at one score, and no region to tell them apart:
        # the ambiguity rule refuses both, exactly as it did before region
        # entered selection. Region RESOLVES a near-tie; it never creates one,
        # and it never rescues a tie it cannot speak to.
        assert res["matched"] is False

    @pytest.mark.asyncio
    async def test_the_country_guard_accepts_a_headquarters_in_country(
        self, monkeypatch,
    ):
        """The guard reads both addresses for the same reason the verdict
        does. An entity incorporated abroad and headquartered in the record's
        country IS in that country."""
        records = [_lei_record(
            "LEI000000000000000C", "NORDIC POLYMERS INCORPORATED",
            country="NL", city="Amsterdam",
            hq_country="US", hq_city="Houston", hq_region="US-TX",
        )]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Nordic Polymers Incorporated", country_code="US",
            city="Houston", state="TX",
        )
        assert res["matched"] is True
        assert res["lei_id"] == "LEI000000000000000C"

    # ── region normalisation ─────────────────────────────────────────────
    def test_an_iso_subdivision_code_is_the_same_region_as_the_bare_code(self):
        """"US-TX" is how GLEIF writes the region an SAP record calls "TX"."""
        from enrichment.locality import normalise_region

        assert normalise_region("US-TX") == normalise_region("TX")
        assert normalise_region("US-TX") == normalise_region("Texas")
        assert normalise_region("US-DE") == normalise_region("Delaware")

    def test_the_iso_strip_lives_in_the_comparator_not_one_parse_site(self):
        """The prefix arrives from more than one direction.

        GLEIF's parser is not the only way a region reaches the comparator —
        ROR falls back to ``country_subdivision_code`` and a record's own
        state field is whatever its source system wrote. A strip applied at
        one parse site normalises one lane and leaves the others comparing
        "us-tx" against "texas", so it is applied inside `normalise_region`
        and both directions are pinned here.
        """
        from enrichment.locality import CONSISTENT, compare_registry_addresses

        # Registry prefixed, record bare.
        verdict, _, _, _ = compare_registry_addresses(
            [{"kind": "registered", "city": "Houston", "region": "US-TX",
              "country": "US"}],
            city="Houston", region="TX", country="US",
        )
        assert verdict == CONSISTENT

        # Record prefixed, registry bare.
        verdict, _, _, _ = compare_registry_addresses(
            [{"kind": "registered", "city": "Houston", "region": "TX",
              "country": "US"}],
            city="Houston", region="US-TX", country="US",
        )
        assert verdict == CONSISTENT

    def test_a_hyphenated_region_name_is_not_a_prefixed_code(self):
        """Stripping at the last hyphen would invent regions.

        "Nord-Pas-de-Calais" is a region's NAME, not a country and a
        subdivision, and cutting it to "Calais" would compare it equal to a
        city. Only the ISO shape — two letters, a hyphen, a short tail — is
        treated as a prefix.
        """
        from enrichment.locality import strip_subdivision_prefix

        assert strip_subdivision_prefix("Nord-Pas-de-Calais") == "Nord-Pas-de-Calais"
        assert strip_subdivision_prefix("Provence-Alpes-Côte d'Azur") == (
            "Provence-Alpes-Côte d'Azur"
        )
        assert strip_subdivision_prefix("US-TX") == "TX"
        assert strip_subdivision_prefix("GB-ENG") == "ENG"

    def test_the_reason_prose_quotes_the_stripped_region(self):
        """A reason reading "states region US-TX" invites a reviewer to wonder
        whether the prefix IS the mismatch."""
        from enrichment.locality import region_label

        assert region_label("US-DE") == "DE (Delaware)"
        assert region_label("US-TX") == "TX (Texas)"

    # ── the reported LEI, verbatim ───────────────────────────────────────
    #: HUNTSMAN INTERNATIONAL LLC as GLEIF publishes it: incorporated in
    #: Wilmington DE, operating from Houston TX. The address pair that the
    #: check used to read half of, kept here under its real identifier so the
    #: regression is findable by the LEI a reviewer would quote.
    _HUNTSMAN_LEI = "3YTEJFW18LGIUQ2N5J61"

    @classmethod
    def _huntsman(cls):
        return _lei_record(
            cls._HUNTSMAN_LEI, "HUNTSMAN INTERNATIONAL LLC",
            city="WILMINGTON", region="US-DE",
            hq_city="Houston", hq_region="US-TX",
        )

    @classmethod
    def _huntsman_handler(cls, monkeypatch):
        records = [cls._huntsman()]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()

    @pytest.mark.asyncio
    async def test_the_reported_lei_corroborates_a_texas_record(
        self, monkeypatch,
    ):
        """The reported defect: a TX record on this LEI flagged
        "GLEIF states region DE".

        GLEIF states BOTH — DE where the entity is incorporated and TX where
        it is. The record agreeing with either one is agreement, and the ISO
        subdivision code the registry writes ("US-TX") is the same region as
        the bare code the record carries ("TX").
        """
        self._huntsman_handler(monkeypatch)
        res = await tier1_lei.call_lei(
            "Huntsman International LLC", country_code="US",
            city="Houston", state="TX",
        )
        assert res["matched"] is True
        assert res["location_verdict"] == "consistent"

        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(res)),
            name1="Huntsman International LLC", city="Houston", state="TX",
        )
        assert result.lei_id == self._HUNTSMAN_LEI
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes

    @pytest.mark.asyncio
    async def test_the_reported_lei_on_an_ohio_record_is_traced_not_flagged(
        self, monkeypatch,
    ):
        """The same entity against a record in a third state. Neither
        registered address is Ohio, so the locality IS contradicted — but the
        record states the registry's name verbatim, so which company this is
        was never the question. Trace, no flag."""
        from enrichment.consistency import (
            registry_location_unconfirmed_count,
            reset_consistency_counters,
        )

        self._huntsman_handler(monkeypatch)
        res = await tier1_lei.call_lei(
            "Huntsman International LLC", country_code="US",
            city="Toledo", state="OH",
        )
        assert res["matched"] is True
        assert res["location_verdict"] == "contradicted"
        assert res["name_match_tier"] == "exact"

        reset_consistency_counters()
        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(res)),
            name1="Huntsman International LLC", city="Toledo", state="OH",
        )
        assert result.lei_id == self._HUNTSMAN_LEI
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes
        assert registry_location_unconfirmed_count() == 1

    def test_a_city_difference_inside_an_agreeing_region_is_a_note(self):
        """Altria: SUFFOLK on the legal address, RICHMOND on the
        headquarters, both Virginia, and the record says Richmond VA. Two
        cities in one state are a plant and a head office."""
        from enrichment.locality import CONSISTENT, compare_registry_addresses

        verdict, _, scope, notes = compare_registry_addresses(
            [{"kind": "legal", "city": "SUFFOLK", "region": "VA",
              "country": "US"}],
            city="Richmond", region="VA", country="US",
        )
        assert (verdict, scope) == (CONSISTENT, "region")
        assert notes and "SUFFOLK" in notes[0]

    def test_two_cities_in_two_states_still_contradict(self):
        """The granularity rule forgives a city, never a region."""
        from enrichment.locality import CONTRADICTED, compare_registry_addresses

        verdict, _, scope, _ = compare_registry_addresses(
            [{"kind": "legal", "city": "Wilmington", "region": "DE",
              "country": "US"}],
            city="Richmond", region="VA", country="US",
        )
        assert (verdict, scope) == (CONTRADICTED, "region")

    # ── (b) exact name, every address contradicting ──────────────────────
    @pytest.mark.asyncio
    async def test_an_exact_name_match_that_contradicts_both_is_unflagged(
        self, monkeypatch,
    ):
        """(b). A multi-state company: the record names the site, the
        registry names the incorporation and the head office, and neither is
        the site. The name is stated verbatim, so which company this is was
        never in question — the row carries no advisory and the batch counter
        carries the observation."""
        from enrichment.consistency import (
            registry_location_unconfirmed_count,
            reset_consistency_counters,
        )

        records = [_lei_record(
            "LEI0000000000000002", "ARKEMA INC.",
            city="Wilmington", region="US-DE",
            hq_city="King of Prussia", hq_region="US-PA",
        )]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Arkema Inc.", country_code="US",
            city="Charlotte", state="NC",
        )
        assert res["matched"] is True
        assert res["location_verdict"] == "contradicted"
        assert res["name_match_tier"] == "exact"
        # Both places named, so the trace says which two the record failed.
        assert "DE" in res["location_detail"] and "PA" in res["location_detail"]

        reset_consistency_counters()
        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(res)),
            name1="Arkema Inc.", city="Charlotte", state="NC",
        )
        assert result.lei_id == "LEI0000000000000002"
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes
        assert registry_location_unconfirmed_count() == 1

    def test_a_legal_form_the_record_omits_is_still_exact(self):
        """"Arkema" against "ARKEMA INC." is one name written twice. The
        legal form is the register's suffix, not a distinguishing token."""
        from enrichment.registry_match import EXACT_TIER, name_match_tier

        assert name_match_tier(["Arkema"], ["ARKEMA INC."]) == EXACT_TIER

    def test_an_abbreviated_legal_form_is_still_exact(self):
        """"Huntsman Corp" against "HUNTSMAN CORPORATION" is one name.

        The rule already forgave a legal form a record OMITS, so "Huntsman"
        alone scored exact against "HUNTSMAN CORPORATION" while "Huntsman
        Corp" scored fuzzy — stating the suffix in the form an SAP operator
        types it ranked BELOW not stating it at all. On record 13017466
        (Longview TX) that wrong tier was the whole reason the row wore
        `registry-location-mismatch`.
        """
        from enrichment.registry_match import EXACT_TIER, name_match_tier

        assert name_match_tier(
            ["Huntsman Corp"], ["HUNTSMAN CORPORATION"],
        ) == EXACT_TIER
        assert name_match_tier(["Widget Co"], ["WIDGET COMPANY"]) == EXACT_TIER
        assert name_match_tier(
            ["Vestas Inc"], ["VESTAS INCORPORATED"],
        ) == EXACT_TIER

    def test_the_abbreviation_fold_never_crosses_two_forms(self):
        """Corp and Inc both name corporations and are still two forms.

        The fold is abbreviation-to-expansion of ONE word. Where the register
        draws a line between two forms, so does the tier.
        """
        from enrichment.registry_match import FUZZY_TIER, name_match_tier

        assert name_match_tier(["Smith Inc"], ["Smith Corp"]) == FUZZY_TIER
        assert name_match_tier(["Nordic Oy"], ["NORDIC OYJ"]) == FUZZY_TIER

    @pytest.mark.asyncio
    async def test_record_13017466_keeps_its_lei_without_the_advisory(
        self, monkeypatch,
    ):
        """The reported row, end to end.

        HUNTSMAN CORPORATION states Wilmington DE on BOTH addresses, so the
        locality genuinely is contradicted against a record in Longview TX —
        this is not a case the two-address rule rescues. What was wrong was
        the tier: the record names the entity verbatim, so the contradiction
        is geography and belongs in the trace, not on the row.
        """
        from enrichment.consistency import (
            registry_location_unconfirmed_count,
            reset_consistency_counters,
        )

        records = [_lei_record(
            "5299000V56320A7RIQ67", "HUNTSMAN CORPORATION",
            city="WILMINGTON", region="US-DE",
            hq_city="WILMINGTON", hq_region="US-DE",
        )]

        def handler(request):
            if request.url.path.endswith("/lei-records"):
                return httpx.Response(200, json={"data": records})
            return httpx.Response(200, json={"data": []})

        _patch_registry(monkeypatch, tier1_lei, handler)
        clear_lei_cache()
        res = await tier1_lei.call_lei(
            "Huntsman Corp", country_code="US",
            city="Longview", state="TX",
        )
        assert res["matched"] is True
        # Both blocks name one place, so the set holds one address.
        assert res["location_verdict"] == "contradicted"
        assert res["name_match_tier"] == "exact"

        reset_consistency_counters()
        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(res)),
            name1="Huntsman Corp", city="Longview", state="TX",
        )
        assert result.lei_id == "5299000V56320A7RIQ67"
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes
        assert registry_location_unconfirmed_count() == 1

    def test_two_different_legal_forms_are_not_exact(self):
        """A register is the authority that says Smith Inc and Smith LLC are
        two entities, and it is not overruled here."""
        from enrichment.registry_match import FUZZY_TIER, name_match_tier

        assert name_match_tier(["Smith Inc"], ["Smith LLC"]) == FUZZY_TIER

    def test_a_crosswalk_is_never_exact_tier(self):
        """The Wikidata lane follows a pointer, not a name. A stale pointer is
        how a record acquires another organisation's registry entry, so the
        address check must stay armed on that lane."""
        from enrichment.registry_match import CROSSWALK_TIER, name_match_tier

        assert name_match_tier(
            ["Aurora University"], ["Aurora University"], crosswalk=True,
        ) == CROSSWALK_TIER

    def test_a_collision_prone_name_is_never_exact_tier(self):
        """"BHS" equals "BHS" verbatim and identifies nothing — the premise
        Fix C(3) already rests on."""
        from enrichment.registry_match import SHORT_NAME_TIER, name_match_tier

        assert name_match_tier(["BHS"], ["BHS"]) == SHORT_NAME_TIER

    # ── (c) the weak match that must keep firing ─────────────────────────
    @staticmethod
    def _aurora_ror(monkeypatch, official_name: str):
        """ROR answering with an Aurora, ILLINOIS organisation.

        The record is in Aurora, COLORADO. Two cities share the name, which is
        exactly why a name match here needs its address checked: the string
        that identified the organisation also appears in the record's own
        city, so it carries less than it looks like it does.
        """
        items = [_ror_item(
            "https://ror.org/0aurora01", official_name,
            city="Aurora", region="Illinois",
        )]

        def handler(request):
            # Affiliation returns nothing, so selection runs on the query
            # endpoint — the path the local rescore and the tier both use.
            if "affiliation" in request.url.params:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": items})

        _patch_registry(monkeypatch, tier1_ror, handler)
        clear_ror_cache()

    @pytest.mark.asyncio
    async def test_the_aurora_university_fuzzy_cross_state_match_is_flagged(
        self, monkeypatch,
    ):
        """(c). ROR returns "Aurora University Foundation" in Illinois for a
        record that says Aurora, Colorado. The name is not what the record
        states and the address is not where the record is: both halves of the
        conjunction, and this is the case the flag exists for."""
        self._aurora_ror(monkeypatch, "Aurora University Foundation")
        res = await tier1_ror.call_ror(
            "Aurora University", country_code="US",
            country="United States", city="Aurora", state="CO",
        )
        assert res["matched"] is True
        assert res["location_verdict"] == "contradicted"
        assert res["name_match_tier"] == "fuzzy"

        result = await _run(
            _orch(ror=_StubROR(res), lei=_StubLEI(None)),
            name1="Aurora University", city="Aurora", state="CO",
        )
        assert REGISTRY_LOCATION_MISMATCH in result.flag_codes
        assert "Illinois" in (result.flag_reason or "")

    @pytest.mark.asyncio
    async def test_the_same_contradiction_under_the_exact_name_is_not_flagged(
        self, monkeypatch,
    ):
        """The twin of the case above with ONE thing changed: ROR returns the
        name the record states. Same registry, same city, same contradicted
        address — and no flag, because the address was never the thing that
        identified the organisation.
        """
        from enrichment.consistency import (
            registry_location_unconfirmed_count,
            reset_consistency_counters,
        )

        self._aurora_ror(monkeypatch, "Aurora University")
        res = await tier1_ror.call_ror(
            "Aurora University", country_code="US",
            country="United States", city="Aurora", state="CO",
        )
        assert res["location_verdict"] == "contradicted"
        assert res["name_match_tier"] == "exact"

        reset_consistency_counters()
        result = await _run(
            _orch(ror=_StubROR(res), lei=_StubLEI(None)),
            name1="Aurora University", city="Aurora", state="CO",
        )
        assert REGISTRY_LOCATION_MISMATCH not in result.flag_codes
        assert registry_location_unconfirmed_count() == 1

    @pytest.mark.asyncio
    async def test_a_missing_tier_is_not_treated_as_exact(self):
        """A result that carries no tier at all — an older cached shape, a
        lane that has not been taught to record one — must fall to the
        flagging side. Silence about the strength of a match is not a claim
        that the match was strong."""
        result = await _run(
            _orch(ror=_StubROR(None), lei=_StubLEI(dict(
                _BIC_GLEIF,
                location_verdict="contradicted",
                location_detail="states region NY (New York); record says CT",
                location_scope="region",
            ))),
            name1="Bicorporate Products Inc", city="Milford", state="CT",
        )
        assert REGISTRY_LOCATION_MISMATCH in result.flag_codes
