"""The research log columns and VAT_Found_Entity_Status (vat/lookup_log.py):
read from what the lookup gathered, no call of their own. Company data:
public Impressum data from the fixtures, or obviously fake."""

from __future__ import annotations

import pytest

from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import page
from tests.mocks.vies_mock import ViesStub
from tests.test_vat_agent import BODY, Agent, _engine, _Hybrid, _rows, agent_answer
from tests.test_vat_hybrid import IMPRESSUM, _settings
from utils.cache import EvidenceCache
from vat.lookup import NO_VAT_INFERRED, FoundResult
from vat.lookup_log import LOG_COLUMNS, entity_status, evidence_snippet, log_columns, name_vs_sap
from vat.validate import validate_rows
from vat.vies_client import VIESClient
from vat.web_pages import number_holder, number_snippet


def _vies(tmp_path):
    return VIESClient(_settings(), cache=EvidenceCache(tmp_path / "v"),
                      transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)


def _codes(status):
    return [line.split(":")[0] for line in (status or "").split("\n") if line]


class TestEntityStatus:
    def test_nothing_learned_is_blank(self):
        assert entity_status(FoundResult("llm_web", "NOT_FOUND:NO_OWN_SITE", usage={})) is None
        assert entity_status(None) is None

    def test_a_fetched_page_says_merged(self):
        r = FoundResult("llm_web", "NOT_FOUND:ENTITY_DEFUNCT", usage={"pages": {
            "https://alt.example/impressum": {"defunct": "Die Alt GmbH wurde auf die Neu GmbH verschmolzen."}}})
        status = entity_status(r)
        assert status.startswith("MERGED: ") and "https://alt.example/impressum" in status

    def test_the_sap_name_says_insolvent(self):
        r = FoundResult("llm_web", "NOT_FOUND:NO_OWN_SITE", usage={})
        assert _codes(entity_status(r, names=("Muster AG in Insolvenz",))) == ["INSOLVENT"]

    def test_parent_operator_and_sub_unit(self):
        r = FoundResult("llm_web", "FOUND_UNVERIFIED:VIES_PENDING", "DE811128135", attributed_to="Muster Holding AG",
                        issues=("PARENT_ENTITY_CANDIDATE",), usage={
                            "name_class": "DEPARTMENT", "entity": "Muster Holding AG", "found_via": "operator",
                            "found_kind": "parent",
                            "operator": {"name": "Muster Holding AG", "url": "https://holding.example/impressum",
                                         "read": True}})
        assert _codes(entity_status(r)) == ["VAT_MAY_BE_PARENTS", "VAT_FROM_OPERATOR", "SUB_UNIT_OF"]

    def test_the_agent_relation(self):
        r = FoundResult("llm_web", "FOUND_UNVERIFIED:VIES_PENDING", "DE811128135",
                        usage={"agent_relation": "parent", "agent_notes": "Unit of the university."})
        assert entity_status(r) == "VAT_FROM_PARENT (agent): Unit of the university."

    def test_siblings(self):
        found = FoundResult("llm_web", "FOUND_VERIFIED", "DE811128135", usage={})
        assert _codes(entity_status(found, [("2", "DE811128135"), ("3", "DE129274235")])) == [
            "SAME_AS_SIBLING", "SIBLING_HAS_OTHER_VAT"]
        none = FoundResult("llm_web", "NOT_FOUND:NO_OWN_SITE", usage={})
        assert entity_status(none, [("2", "DE811128135")]).startswith("SIBLING_HAS_VAT: 2=DE811128135")

    def test_no_vat_inferred_and_deleted(self):
        r = FoundResult("llm_web", NO_VAT_INFERRED, usage={})
        assert _codes(entity_status(r, deleted=True)) == ["PUBLIC_BODY_NO_VAT_INFERRED", "DELETED_IN_SAP"]

    def test_ranking_hints_are_labelled_unverified(self):
        r = FoundResult("llm_web", "NOT_FOUND:NO_OWN_SITE", usage={"ranked_1": [
            {"url": "https://group.example/firmen/alt", "type": "own_site", "reason": "Likely parent company page."},
            {"url": "https://kanzlei.example/presse", "type": "other", "reason": "Insolvency notice for Alt AG."},
            {"url": "https://alt.example/", "type": "own_site", "reason": "Official homepage."}]})
        assert entity_status(r).split("\n") == [
            "PARENT_HINT (search result, unverified): group.example — Likely parent company page.",
            "DEFUNCT_HINT (search result, unverified): kanzlei.example — Insolvency notice for Alt AG."]


class TestNumberHolder:
    """The company a page names before a number no model attributed."""

    GROUP = ("Impressum\nMuster Holding GmbH\nMusterweg 1, 12345 Musterstadt\n"
             "Vertreten durch: Max Muster\nRegistergericht: Amtsgericht Musterstadt HRB 1\n"
             "Umsatzsteuer-ID: DE811128135")

    def test_the_parents_name(self):
        assert number_holder(self.GROUP, "DE811128135", ("Muster Ceramics GmbH",)) == "Muster Holding GmbH"

    def test_a_general_partner_is_skipped(self):
        text = ("Impressum Muster Logistik GmbH & Co. KG\n"
                "Persönlich haftende Gesellschafterin (Komplementärin): Verwaltung Muster GmbH\n"
                "USt-IdNr.: DE 811 128 135")
        assert number_holder(text, "DE811128135", ("Muster Logistik GmbH",)) == "Muster Logistik GmbH & Co. KG"

    def test_lower_case_joiners_and_an_association(self):
        text = "Impressum\nMUSTER Gesellschaft für Technik und Forschung e.V.\nUSt.-Id-Nr.: DE811128135"
        assert number_holder(text, "DE811128135", ("MUSTER Institut",)) == \
            "MUSTER Gesellschaft für Technik und Forschung e.V."

    def test_only_since_the_previous_number(self):
        text = "Alpha Muster GmbH USt-ID DE129274235\nBeta Muster AG USt-ID DE811128135"
        assert number_holder(text, "DE811128135", ("Beta Muster AG",)) == "Beta Muster AG"

    def test_no_name_no_number(self):
        assert number_holder("USt-ID DE811128135", "DE811128135") is None
        assert number_holder("Muster GmbH", "DE811128135") is None


class TestNameVsSap:
    def test_the_comparison(self):
        r = FoundResult("llm_web", "FOUND_UNVERIFIED", "DE811128135", attributed_to="Muster Technologies Group AG",
                        name_match="PARTIAL", usage={})
        names = ("Muster Technologies GmbH", None, None, None)
        assert name_vs_sap(r, names) == ("PARTIAL; SAP: Muster Technologies GmbH; found: Muster Technologies "
                                         "Group AG; legal form gmbh → ag; only found: group")
        assert entity_status(r, names=names) == ("VAT_MAY_BE_PARENTS: the page gives the number to Muster "
                                                 "Technologies Group AG (a holding / group name), not the record's name")

    def test_another_company_holds_the_number(self):
        r = FoundResult("llm_web", "FOUND_UNVERIFIED", "DE811128135", attributed_to="Muster GmbH",
                        name_match="PARTIAL", usage={})
        assert entity_status(r, names=("Muster Zwei GmbH",)) == \
            "OTHER_ENTITY_HOLDS_VAT: the page gives the number to Muster GmbH, not the record's name"

    def test_a_spelling_difference_is_no_other_entity(self):
        r = FoundResult("llm_web", "FOUND_UNVERIFIED", "DE811128135", attributed_to="Muster Chemicals Services GmbH",
                        name_match="PARTIAL", usage={})
        assert entity_status(r, names=("Muster Chemical Services GmbH",)) is None

    def test_the_evidence_snippet(self):
        text = "x" * 400 + " Muster GmbH Musterweg 1 USt-IdNr.: DE 811 128 135 Kontakt" + "y" * 200
        assert number_snippet(text, "DE811128135").startswith("…") and "DE 811 128 135 Kontakt" in \
            number_snippet(text, "DE811128135")
        assert number_snippet(text, "DE129274235") is None
        r = FoundResult("llm_web", "FOUND_UNVERIFIED", "DE811128135", source_url="https://muster.example/impressum",
                        evidence=None, usage={"evidence_snippet": "Muster GmbH USt-IdNr.: DE 811 128 135"})
        assert evidence_snippet(r) == "Muster GmbH USt-IdNr.: DE 811 128 135 (https://muster.example/impressum)"
        other = FoundResult("llm_web", "FOUND_UNVERIFIED", "DE129274235", evidence="USt-ID DE129274235",
                            usage={"hybrid_usage": {"evidence_snippet": "Muster GmbH DE 811 128 135"}})
        assert evidence_snippet(other) == "USt-ID DE129274235"   # the hybrid's snippet is another number

    def test_no_found_name(self):
        assert name_vs_sap(FoundResult("llm_web", "NOT_FOUND:NO_OWN_SITE", usage={}), ("Muster GmbH",)) is None


class TestLogColumns:
    def test_a_row_not_looked_up_is_blank(self):
        assert log_columns(None) == dict.fromkeys(LOG_COLUMNS)

    def test_the_research_log(self):
        r = FoundResult("llm_web", "NOT_FOUND:NO_NUMBER_ON_OWN_SITE", usage={
            "name_class": "OTHER", "search_name": "Muster GmbH", "query_1_run": '"Muster GmbH" Musterstadt',
            "query_2_run": "Muster GmbH Impressum USt-IdNr", "picked_1": [["https://muster.example", "own"]],
            "ranking_1": "model",
            "hosts": {"muster.example": {"kind": "own", "answered": True, "fetched": ["https://muster.example"]}},
            "pages": {"https://muster.example/impressum": {"status": 200, "via": "follow"}},
            "peppol_queries": 1, "peppol_entries": 0, "searches": 2, "fetches": 2, "follows": 1})
        cols = log_columns(r)
        assert cols["VAT_Log_Search_Name"] == "Muster GmbH"
        assert cols["VAT_Log_Queries"].split("\n") == ['1: "Muster GmbH" Musterstadt',
                                                        "2: Muster GmbH Impressum USt-IdNr"]
        assert cols["VAT_Log_Ranking"] == "1 (model): https://muster.example [own]"
        assert cols["VAT_Log_Hosts"] == "muster.example [own, answered]"
        assert cols["VAT_Log_Pages"] == "https://muster.example/impressum [200; via follow]"
        assert cols["VAT_Log_Peppol"] == "entries 0, no match"
        assert cols["VAT_Log_No_Number_Class"].startswith("E: ")
        assert cols["VAT_Log_Counts"] == "searches 2, fetches 2, follows 1"


class TestInTheOutput:
    @pytest.mark.asyncio
    async def test_off_by_default(self, tmp_path):
        out, _ = await validate_rows(_rows(), _vies(tmp_path), lookup=_Hybrid())
        assert not set(LOG_COLUMNS) & set(out[0])

    @pytest.mark.asyncio
    async def test_on_every_row_with_the_agent(self, tmp_path):
        agent = _engine(tmp_path, Agent(agent_answer()), {IMPRESSUM: page(IMPRESSUM, BODY)})
        out, _ = await validate_rows(_rows(), _vies(tmp_path), lookup=_Hybrid(), agent=agent, log_columns=True)
        bosch, sibling, police, _found = out
        assert bosch["VAT_Found_Value"] == "DE811128135"
        assert "SAME_AS_SIBLING: customer 2" in bosch["VAT_Found_Entity_Status"]
        assert '"relation": "same_entity"' in bosch["VAT_Log_Agent"]
        assert bosch["VAT_Log_Hosts"] == "bosch.example [own, answered]"       # the hybrid lookup's hosts kept
        assert sibling["VAT_Found_Entity_Status"] is None                       # not looked up: blank
        assert police["VAT_Found_Entity_Status"].startswith("PUBLIC_BODY_NO_VAT_INFERRED")
