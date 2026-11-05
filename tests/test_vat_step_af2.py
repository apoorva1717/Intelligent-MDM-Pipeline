"""Step AF-2: VIES out of the lookup path (the queue), the review tiers, the
fetch budget for entities with many own hosts, the ranking prompt's
preference for legal-notice pages. Company data: public Impressum data from
the fixtures, or obviously fake."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import Workbook

from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from tests.test_vat_hybrid import BOSCH, IMPRESSUM, NOTHING, Model, _engine, _settings, found, read, run
from utils.cache import EvidenceCache
from vat.lookup import (
    AUTO_ACCEPT,
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    CONF_VERIFIED,
    CONFLICT,
    FOUND_PENDING,
    FOUND_UNVERIFIED,
    FOUND_VERIFIED,
    MANUAL_REVIEW,
    VALIDATION_PENDING,
    FoundResult,
    lookup_kpis,
    review_tier,
    run_lookups,
    sap_update_eligible,
    vies_pending,
)
from vat.llm_cache import VatLLM
from vat.lookup_web import WebSearchLookup
from vat.peppol import PeppolDirectory
from vat.prompts import LLM_WEB_RANK_USER_PROMPT_TEMPLATE
from vat.vies_client import VIESClient
from vat.vies_retry import ViesRetryQueue, apply_answers, apply_to_workbook, drain, enqueue

VAT = "DE811128135"
BODY = "Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen Umsatzsteuer-Identifikationsnummer: DE 811 128 135"


def _counted(tmp_path, model, pages, stub=None, **settings):
    """The hybrid engine with a VIES stub the test can count."""
    cache = EvidenceCache(tmp_path)
    s = _settings(**settings)
    stub = stub or ViesStub(vies_mock.echo_valid_undisclosed)
    vies = VIESClient(s, cache=cache, transport=stub.transport)
    engine = WebSearchLookup(s, vies, llm=VatLLM(cache.namespace("vat_llm"), search=model),
                             fetcher=FakeWeb(pages), peppol=PeppolDirectory(cache, enabled=False))
    return engine, vies, stub


def _model():
    return Model(run=[run("q", [IMPRESSUM])], read=[read(found())])


class TestViesOutOfTheLookup:
    """Item 1."""

    @pytest.mark.asyncio
    async def test_an_auto_candidate_leaves_the_lookup_pending_without_a_vies_call(self, tmp_path):
        engine, _, stub = _counted(tmp_path, _model(), {IMPRESSUM: page(IMPRESSUM, BODY)})
        r = await engine.lookup(BOSCH)
        assert stub.requests == []
        assert (r.value, r.status, r.validation_status) == (VAT, FOUND_PENDING, VALIDATION_PENDING)
        assert r.decision == AUTO_ACCEPT and "VIES_PENDING" in r.all_issues
        assert not sap_update_eligible(r.decision, r.validation_status)

    @pytest.mark.asyncio
    async def test_inline_vies_on_request(self, tmp_path):
        engine, _, stub = _counted(tmp_path, _model(), {IMPRESSUM: page(IMPRESSUM, BODY)},
                                   vat_vies_in_lookup=True)
        r = await engine.lookup(BOSCH)
        assert len(stub.requests) == 1 and (r.status, r.validation_status) == (FOUND_VERIFIED, "VALID")
        assert sap_update_eligible(r.decision, r.validation_status)

    def test_a_reconciled_candidate_with_no_vies_status_is_queued(self):
        # Göttingen: a gate stopped it before VIES (NOT_CHECKED), the name
        # reconcile made it AUTO.
        r = FoundResult("llm_web", FOUND_UNVERIFIED, VAT, source_url=IMPRESSUM, name_match="EXACT",
                        address_match="EXACT", source_type="COMPANY_IMPRESSUM", validation_status="NOT_CHECKED")
        assert r.decision == AUTO_ACCEPT
        p = vies_pending(r)
        assert (p.status, p.validation_status, p.decision) == (FOUND_UNVERIFIED, VALIDATION_PENDING, AUTO_ACCEPT)
        assert "VIES_PENDING" in p.all_issues

    def test_vies_answers_and_conflicts_are_left_alone(self):
        valid = FoundResult("llm_web", FOUND_VERIFIED, VAT, source_url=IMPRESSUM, validation_status="VALID")
        assert vies_pending(valid) is valid
        conflict = FoundResult("llm_web", FOUND_UNVERIFIED, VAT, source_url=IMPRESSUM,
                               flags=("CONFLICTING_SOURCES:DE1,DE2",), validation_status="NOT_CHECKED")
        assert conflict.decision == CONFLICT and vies_pending(conflict) is conflict

    @pytest.mark.asyncio
    async def test_run_lookups_marks_every_record_pending(self, tmp_path):
        engine, _, _ = _counted(tmp_path, _model(), {IMPRESSUM: page(IMPRESSUM, BODY)})
        a, b = await run_lookups(engine, [BOSCH, BOSCH])
        assert a.validation_status == b.validation_status == VALIDATION_PENDING
        kpis = lookup_kpis([a, b])
        assert kpis["vies_pending"] == 1 and kpis["sap_update_eligible"] == 0


class TestQueue:
    """Item 1: the queue verifies PENDING rows in batches."""

    T0 = datetime(2026, 11, 5, tzinfo=timezone.utc)

    def _row(self, decision=AUTO_ACCEPT, confidence=CONF_VERIFIED, issues="VIES_PENDING"):
        return {"VAT_Found_Value": VAT, "VAT_Found_Status": FOUND_PENDING, "VAT_Found_Decision": decision,
                "VAT_Found_Confidence": confidence, "VAT_Found_Validation_Status": VALIDATION_PENDING,
                "VAT_Found_Issues": issues, "VAT_Found_Name_Match": "EXACT", "VAT_Found_Address_Match": "EXACT",
                "VAT_Found_Source_Class": "A", "VAT_Found_Evidence": "USt-IdNr. DE811128135"}

    async def _answers(self, tmp_path, reply):
        cache = EvidenceCache(tmp_path)
        stub = ViesStub(reply)
        client = VIESClient(_settings(), cache=cache, transport=stub.transport)
        queue = ViesRetryQueue(None)
        queue.add(VAT, self.T0)
        asked = await queue.process(client, self.T0)
        return queue, asked, stub

    def test_pending_results_are_queued_and_due_at_once(self):
        r = vies_pending(FoundResult("llm_web", FOUND_UNVERIFIED + ":VIES_NOT_CHECKED", VAT, source_url=IMPRESSUM))
        queue = ViesRetryQueue(None)
        assert enqueue(queue, [r], self.T0) == 1 and queue.due(self.T0) == [VAT]

    @pytest.mark.asyncio
    async def test_valid_clears_the_issue(self, tmp_path):
        queue, asked, stub = await self._answers(tmp_path, vies_mock.echo_valid_undisclosed)
        assert asked == {VAT: "VALID"} and len(stub.requests) == 1
        row = self._row()
        assert apply_answers([row], queue.answers()) == 1
        assert (row["VAT_Found_Status"], row["VAT_Found_Validation_Status"], row["VAT_Found_Issues"]) == (
            FOUND_VERIFIED, "VALID", None)
        assert row["VAT_Found_Validation_Date"]
        assert sap_update_eligible(row["VAT_Found_Decision"], row["VAT_Found_Validation_Status"])

    @pytest.mark.asyncio
    async def test_valid_recomputes_a_review_rows_tier(self, tmp_path):
        queue, _, _ = await self._answers(tmp_path, vies_mock.echo_valid_undisclosed)
        row = self._row(MANUAL_REVIEW, CONF_MEDIUM, "BRANCH; VIES_PENDING")
        apply_answers([row], queue.answers())
        assert (row["VAT_Found_Confidence"], row["VAT_Found_Issues"]) == (CONF_HIGH, "BRANCH")

    @pytest.mark.asyncio
    async def test_invalid_is_review_low(self, tmp_path):
        queue, _, _ = await self._answers(
            tmp_path, lambda b: vies_mock.invalid(b["countryCode"], b["vatNumber"]))
        row = self._row()
        apply_answers([row], queue.answers())
        assert (row["VAT_Found_Decision"], row["VAT_Found_Confidence"], row["VAT_Found_Issues"]) == (
            MANUAL_REVIEW, CONF_LOW, "VIES_INVALID")

    @pytest.mark.asyncio
    async def test_unavailable_stays_pending_and_is_retried(self, tmp_path):
        queue, asked, _ = await self._answers(tmp_path, vies_mock.error("MS_UNAVAILABLE"))
        assert asked[VAT] != "VALID" and queue.answers() == {}
        row = self._row()
        assert apply_answers([row], queue.answers()) == 0 and row["VAT_Found_Validation_Status"] == VALIDATION_PENDING
        assert queue.due(self.T0 + timedelta(hours=1)) == [VAT]

    @pytest.mark.asyncio
    async def test_one_batch_for_many_numbers(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        client = VIESClient(_settings(), cache=EvidenceCache(tmp_path), transport=stub.transport)
        queue = ViesRetryQueue(None)
        for v in (VAT, "DE129274235", "DE143589180"):
            queue.add(v, self.T0)
        assert set((await queue.process(client, self.T0)).values()) == {"VALID"} and len(stub.requests) == 3

    @pytest.mark.asyncio
    async def test_a_frozen_run_never_drains(self, tmp_path):
        stub = ViesStub(vies_mock.echo_valid_undisclosed)
        client = VIESClient(_settings(), cache=EvidenceCache(tmp_path, frozen=True), transport=stub.transport)
        queue = ViesRetryQueue(None)
        queue.add(VAT, self.T0)
        _, asked = await drain(client, queue)
        assert asked == {} and stub.requests == []

    def test_answers_are_written_into_a_workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        row = self._row()
        ws.append(list(row))
        ws.append(list(row.values()))
        assert apply_to_workbook(wb, {VAT: ("VALID", "2026-11-05")}) == 1
        values = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
        assert values["VAT_Found_Validation_Status"] == "VALID" and values["VAT_Found_Issues"] is None


class TestReviewTiers:
    """Item 2."""

    @pytest.mark.parametrize("name, address, cls, issues, evidence, tier", [
        ("EXACT", "EXACT", "A", [], "q", CONF_HIGH),
        ("EXACT", "SAME_TOWN", "B", ["ADDRESS_DIFFERS", "BRANCH"], "q", CONF_HIGH),
        ("EXACT", "EXACT", "A", ["ADDRESS_NOT_EXACT"], "q", CONF_HIGH),  # ABBREVIATED, SAME_TOWN only
        ("EXACT", "EXACT", "C", ["SECONDARY_SOURCE"], "q", CONF_MEDIUM),
        ("EXACT", "EXACT", "A", ["LEGAL_FORM_DIFFERS:gmbh/ag"], "q", CONF_MEDIUM),
        ("EXACT", "EXACT", "A", ["PARENT_ENTITY_CANDIDATE"], "q", CONF_MEDIUM),
        ("PARTIAL", "EXACT", "C", ["NAME_PARTIAL", "SUCCESSOR_CANDIDATE", "SECONDARY_SOURCE"], "q", CONF_LOW),  # LG
        ("PARTIAL", "EXACT", "A", ["NAME_PARTIAL", "SUCCESSOR_CANDIDATE"], "q", CONF_MEDIUM),
        ("EXACT", "NO_ADDRESS", "A", ["ADDRESS_NOT_ON_PAGE"], "q", CONF_MEDIUM),
        ("EXACT", "EXACT", "A", ["VIES_PENDING"], "q", CONF_MEDIUM),
        ("EXACT", "EXACT", "A", ["VIES_UNAVAILABLE"], "q", CONF_MEDIUM),
        ("PARTIAL", "DIFFERENT_TOWN", "A", ["ADDRESS_MISMATCH", "NAME_PARTIAL"], "q", CONF_LOW),
        ("PARTIAL", "EXACT", "C", ["NAME_PARTIAL", "SECONDARY_SOURCE"], "q", CONF_LOW),
        ("PARTIAL", "EXACT", "A", ["NAME_PARTIAL", "OTHER_VAT_IDS_ON_PAGE"], "q", CONF_LOW),
        ("EXACT", "EXACT", "A", ["EXTRACTED_BY_PATTERN"], None, CONF_LOW),
        ("EXACT", "EXACT", "A", ["EXTRACTED_BY_PATTERN"], "USt-IdNr. DE811128135", CONF_HIGH),
        ("EXACT", "EXACT", "A", ["VIES_INVALID"], "q", CONF_LOW),
        # An unchecked number is LOW; neither list (another town alone, a
        # partial name alone) is MEDIUM, never above a row with more issues.
        ("EXACT", "EXACT", None, ["EVIDENCE_NOT_VERIFIED"], "q", CONF_LOW),
        ("EXACT", "EXACT", "C", ["SECONDARY_SOURCE", "EVIDENCE_NOT_VERIFIED"], "q", CONF_LOW),
        ("EXACT", "DIFFERENT_TOWN", "A", ["ADDRESS_MISMATCH"], "q", CONF_MEDIUM),
        ("EXACT", "DIFFERENT_TOWN", "A", ["ADDRESS_MISMATCH", "LEGAL_FORM_DIFFERS:ag/gmbh"], "q", CONF_MEDIUM),
        ("PARTIAL", "EXACT", "A", ["NAME_PARTIAL"], "q", CONF_MEDIUM),
    ])
    def test_tier(self, name, address, cls, issues, evidence, tier):
        assert review_tier(name, address, cls, issues, evidence) == tier

    def test_a_review_result_carries_its_tier(self):
        r = FoundResult("llm_web", FOUND_VERIFIED, VAT, source_url=IMPRESSUM, name_match="PARTIAL",
                        address_match="EXACT", source_type="COMPANY_IMPRESSUM", validation_status="VALID",
                        issues=("NAME_PARTIAL", "OTHER_VAT_IDS_ON_PAGE"))
        assert (r.decision, r.confidence) == (MANUAL_REVIEW, CONF_LOW)


class TestFetchBudget:
    """Item 3."""

    URLS = [f"https://www.bosch{i}.example/impressum" for i in range(8)]

    async def _lookup(self, tmp_path, n_hosts):
        urls = self.URLS[:n_hosts]
        model = Model(run=[run("q1", urls)], read=[read(NOTHING)])
        web = FakeWeb({u: page(u, "Impressum Robert Bosch GmbH 70839 Gerlingen") for u in urls})
        return await _engine(tmp_path, model, {}, web=web, vat_web_max_follows=0).lookup(BOSCH)

    @pytest.mark.asyncio
    async def test_three_or_more_own_hosts_get_six_fetches(self, tmp_path):
        r = await self._lookup(tmp_path, 8)
        assert (r.usage["own_hosts_1"], r.usage["max_fetches"], r.usage["fetches"]) == (8, 6, 6)
        assert [n["url"] for n in r.usage["not_fetched"]] == self.URLS[6:]

    @pytest.mark.asyncio
    async def test_fewer_own_hosts_keep_four(self, tmp_path):
        r = await self._lookup(tmp_path, 2)
        assert (r.usage["own_hosts_1"], r.usage["max_fetches"]) == (2, 4)

    @pytest.mark.asyncio
    async def test_follows_and_probe_unchanged(self, tmp_path):
        r = await self._lookup(tmp_path, 3)
        assert r.usage["max_fetches"] == 6 and r.usage["follows"] == 0 and r.usage.get("probes", 0) <= 1

    def test_ranking_prompt_prefers_legal_notice_pages(self):
        text = LLM_WEB_RANK_USER_PROMPT_TEMPLATE
        assert all(w in text for w in ("legal,", "impressum,", "imprint", "legal-notice"))
        assert "before every other result" in text
