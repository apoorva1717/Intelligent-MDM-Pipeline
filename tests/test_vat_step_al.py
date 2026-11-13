"""Step AL (vat/step_al.py): the second look at the "other name in another
town" rows, and the permanent rule that keeps the group from forming again.
The model and the web are faked. Company data: public Impressum data from
the fixtures, or obviously fake."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import FakeWeb, page
from tests.mocks.vies_mock import ViesStub
from tests.test_vat_hybrid import BOSCH, BODY, IMPRESSUM, QUOTE, Model, _settings, found, read, run
from utils.cache import EvidenceCache
from vat.llm_cache import VatLLM
from vat.lookup import (
    AUTO_ACCEPT, MANUAL_REVIEW, FoundResult, reject_other_name_other_town, withdraw,
)
from vat.lookup_web import exact_name_query
from vat.name_prep import prepare
from vat.peppol import PeppolDirectory
from vat.review_columns import review_columns
from vat.step_al import (
    CANDIDATE_OUTCOME, DIRECTORY_NOT_AT_SAP_ADDRESS, INVALID_NUMBER, LEGAL_FORM_CONTRADICTS, NAME_NOT_EXACT,
    NOT_QUOTED, NOT_RECHECKED, NOTHING_PASSES, OTHER_COMPANY_BLOCK, SEVERAL_NUMBERS, SOURCE_NOT_TRUSTED,
    SecondLook, address_query, check_input, gate, pick_test_rows, row_outcome, stratum_of, withdrawn_result,
)
from vat.vies_client import VIESClient

PLAN = prepare(BOSCH.names)


def cand(**kw) -> FoundResult:
    """A candidate as the hybrid reader leaves it: own Impressum, exact name."""
    base = dict(mode="llm_web", status="FOUND_VERIFIED", value="DE811128135", source_url=IMPRESSUM,
                attributed_to="Robert Bosch GmbH", evidence=QUOTE, source_type="COMPANY_IMPRESSUM",
                name_match="EXACT", address_match="EXACT", validation_status="PENDING")
    return FoundResult(**{**base, **kw})


def _engine(tmp_path, model, pages):
    cache = EvidenceCache(tmp_path)
    s = _settings()
    vies = VIESClient(s, cache=cache, transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)
    return SecondLook(s, vies, llm=VatLLM(cache.namespace("vat_llm"), search=model), fetcher=FakeWeb(pages),
                      peppol=PeppolDirectory(cache, enabled=False))


# ── 1. The input check ─────────────────────────────────────────────────────

def _group(n_review=301, n_conflict=388):
    rows = [{"Customer": str(i), "Source sheet": "BP w email", "VAT_Decision": "Review",
             "Name_Check": "SIMILAR_NAME", "Address_Check": "OTHER_TOWN"} for i in range(n_review)]
    rows += [{"Customer": str(10000 + i), "Source sheet": "BP wo email", "VAT_Decision": "Conflict",
              "Name_Check": "DIFFERENT_NAME", "Address_Check": "OTHER_TOWN"} for i in range(n_conflict)]
    return rows


class TestInputCheck:
    def test_the_689_rows_pass(self):
        assert check_input(_group()) == []

    def test_another_count_or_value_is_reported(self):
        assert any("688 rows" in p for p in check_input(_group(n_review=300)))
        rows = _group()
        rows[0]["Address_Check"] = "OTHER_STREET_SAME_TOWN"
        rows[1]["Name_Check"] = "SAME_NAME"
        problems = check_input(rows)
        assert any("Address_Check" in p for p in problems) and any("Name_Check" in p for p in problems)

    def test_a_duplicate_customer_is_reported(self):
        rows = _group()
        rows[1]["Customer"] = rows[0]["Customer"]
        assert any("more than once" in p for p in check_input(rows))


# ── 0. The email columns ───────────────────────────────────────────────────

class TestEmailColumns:
    def test_both_sap_headers_are_read(self):
        from vat.validate import _LOOKUP_ROLE_FIELDS, resolve_columns

        cols = resolve_columns(["Name", "Clrk's internet add.", "E-Mail Address"], _LOOKUP_ROLE_FIELDS)
        assert cols["email"] == "E-Mail Address" and cols["email_2"] == "Clrk's internet add."

    def test_the_company_address_wins_over_a_mailbox(self):
        from vat.validate import _email

        assert _email("x@gmail.com", "einkauf@bosch.example") == "einkauf@bosch.example"
        assert _email("x@gmail.com", None) == "x@gmail.com" and _email(None, None) is None

    def test_the_lookup_input_carries_it(self):
        from vat.normalize import assess_format
        from vat.validate import _LOOKUP_ROLE_FIELDS, _lookup_input, resolve_columns

        row = {"Name": "Robert Bosch GmbH", "City": "GERLINGEN", "E-Mail Address": None,
               "Clrk's internet add.": "rechnung@bosch.example", "VAT Registration No.": None}
        inp = _lookup_input(row, resolve_columns(row), resolve_columns(row, _LOOKUP_ROLE_FIELDS), "1",
                            assess_format(None, "DE"))
        assert inp.email == "rechnung@bosch.example"


# ── 2. The routes' queries ─────────────────────────────────────────────────

class TestQueries:
    def test_route_a_is_the_quoted_legal_name_without_the_town(self):
        assert exact_name_query(PLAN) == '"Robert Bosch GmbH" Impressum USt-IdNr'
        assert "Gerlingen" not in exact_name_query(PLAN).title()

    def test_route_a_needs_a_legal_form(self):
        assert exact_name_query(prepare(("Feuerwehr Essen", None, None, None))) is None

    def test_route_c_is_name_street_postcode_city_unquoted(self):
        assert address_query(PLAN, BOSCH) == "Robert Bosch GmbH Robert-Bosch-Platz 1 70839 GERLINGEN"

    @pytest.mark.asyncio
    async def test_routes_run_as_built_in_order(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {}).lookup(BOSCH)
        queries = [u for kind, u in model.calls if kind == "run"]
        assert '"Robert Bosch GmbH" Impressum USt-IdNr' in queries[0]
        assert "Robert-Bosch-Platz 1 70839 GERLINGEN" in queries[1] and len(queries) == 2
        assert r.usage["al"]["routes"] == ["A", "C"] and r.usage["al"]["outcome"] == NOTHING_PASSES


# ── 3. The gate, one test per condition ────────────────────────────────────

class TestGate:
    def test_a_clean_candidate_passes(self):
        assert gate(cand(), PLAN, BODY) == (cand(), None)

    def test_name_not_exact(self):
        assert gate(cand(name_match="PARTIAL", attributed_to="Bosch Rexroth AG"), PLAN, BODY)[1] == NAME_NOT_EXACT

    def test_a_contained_name_passes(self):
        r = cand(name_match="PARTIAL", issues=("NAME_CONTAINED",))
        assert gate(r, PLAN, BODY)[1] is None

    def test_a_contradicting_legal_form(self):
        r = cand(name_match="PARTIAL", issues=("LEGAL_FORM_DIFFERS:gmbh/ag",))
        assert gate(r, PLAN, "Impressum Robert Bosch AG " + QUOTE)[1] == LEGAL_FORM_CONTRADICTS

    def test_a_stated_conversion_is_review_not_rejection(self):
        r = cand(name_match="PARTIAL", issues=("LEGAL_FORM_DIFFERS:gmbh/ag",))
        passed, why = gate(r, PLAN, "Impressum Robert Bosch AG (vormals Robert Bosch GmbH) " + QUOTE)
        assert why is None and "SUCCESSOR_CANDIDATE" in passed.issues and passed.decision == MANUAL_REVIEW
        assert "MAYBE_RENAMED_OR_SUCCESSOR" in review_columns(passed)["Issue_Flags"]

    def test_a_number_without_a_quote(self):
        assert gate(cand(issues=("EXTRACTED_BY_PATTERN",)), PLAN, BODY)[1] == NOT_QUOTED
        assert gate(cand(evidence=None), PLAN, BODY)[1] == NOT_QUOTED

    def test_another_companys_block(self):
        assert gate(cand(issues=("PARENT_ENTITY_CANDIDATE",)), PLAN, BODY)[1] == OTHER_COMPANY_BLOCK

    def test_several_numbers_the_reader_could_not_attribute(self):
        r = cand(flags=("AMBIGUOUS_SAME_NAME:DE811128135,DE129274235",))
        assert gate(r, PLAN, BODY)[1] == SEVERAL_NUMBERS

    def test_a_directory_only_at_saps_street_and_postcode(self):
        d = cand(source_type="AGGREGATOR", address_match="SAME_TOWN")
        assert d.source_class == "C" and gate(d, PLAN, BODY)[1] == DIRECTORY_NOT_AT_SAP_ADDRESS
        passed, why = gate(replace(d, address_match="EXACT"), PLAN, BODY)
        assert why is None and passed.decision == MANUAL_REVIEW
        assert "FROM_DIRECTORY" in review_columns(passed)["Issue_Flags"]

    def test_an_untrusted_source(self):
        assert gate(cand(source_type=None), PLAN, BODY)[1] == SOURCE_NOT_TRUSTED

    def test_an_invalid_number(self):
        assert gate(cand(value="DE811128136"), PLAN, BODY)[1] == INVALID_NUMBER

    def test_another_town_is_review_not_rejection(self):
        passed, why = gate(cand(address_match="DIFFERENT_TOWN"), PLAN, BODY)
        assert why is None and passed.decision == MANUAL_REVIEW

    def test_saps_town_can_be_auto(self):
        assert gate(cand(address_match="SAME_TOWN"), PLAN, BODY)[0].decision == AUTO_ACCEPT


class TestBlockAttribution:
    """A group's legal notice with three companies, each with its number."""

    NOTICE = ("Impressum\n"
              "Bosch Holding Beispiel AG, Musterweg 1, 12345 Musterstadt, USt-IdNr. DE129274235\n"
              + "Weitere Gesellschaften der Gruppe, jeweils mit eigenem Sitz und eigener Registrierung. " * 8 + "\n"
              "Robert Bosch GmbH, Robert-Bosch-Platz 1, 70839 Gerlingen, USt-IdNr. DE811128135\n"
              + "Weitere Gesellschaften der Gruppe, jeweils mit eigenem Sitz und eigener Registrierung. " * 8 + "\n"
              "Bosch Beispiel Service GmbH, Musterweg 2, 12345 Musterstadt, USt-IdNr. DE811569869\n")

    def test_the_number_in_the_entitys_block_passes(self):
        assert gate(cand(), PLAN, self.NOTICE)[1] is None

    def test_a_number_from_another_companys_block_does_not(self):
        assert gate(cand(value="DE129274235"), PLAN, self.NOTICE)[1] == OTHER_COMPANY_BLOCK
        assert gate(cand(value="DE811569869"), PLAN, self.NOTICE)[1] == OTHER_COMPANY_BLOCK


# ── Route B: the customer's email domain ───────────────────────────────────

EMAIL_INP = replace(BOSCH, email="einkauf@bosch.example")


class TestRouteB:
    @pytest.mark.asyncio
    async def test_its_legal_notice_names_the_entity(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(EMAIL_INP)
        assert r.value == "DE811128135" and r.decision == AUTO_ACCEPT
        assert r.usage["al"]["route"] == "B" and r.usage["al"]["outcome"] == CANDIDATE_OUTCOME
        assert not [c for c in model.calls if c[0] == "run"]  # no search needed

    @pytest.mark.asyncio
    async def test_a_notice_naming_another_company_is_not_own(self, tmp_path):
        body = "Impressum Fremd Holding AG Musterweg 1 12345 Musterstadt " + QUOTE
        model = Model(run=[run("q", [])], read=[read(found(name="Fremd Holding AG"))])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)}).lookup(EMAIL_INP)
        assert r.value is None and r.usage["al"]["outcome"] == NOTHING_PASSES
        assert r.usage["hosts"]["bosch.example"]["rejected"] == "NAME_NOT_ON_PAGE"
        assert not [c for c in model.calls if c[0] == "read"]  # never read

    @pytest.mark.asyncio
    async def test_imprint_after_impressum(self, tmp_path):
        imprint = "https://www.bosch.example/imprint"
        model = Model(run=[run("q", [])], read=[read(found(url=imprint))])
        r = await _engine(tmp_path, model, {imprint: page(imprint, BODY)}).lookup(EMAIL_INP)
        assert r.value == "DE811128135" and r.usage["al"]["route"] == "B"

    @pytest.mark.asyncio
    async def test_a_mailbox_domain_is_no_route(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {}).lookup(replace(BOSCH, email="x@gmail.com"))
        assert r.usage["al"]["routes"] == ["A", "C"]


# ── 4. Outcomes ────────────────────────────────────────────────────────────

OLD = {"VAT_Proposed": "DE129274235", "VAT_Decision": "Conflict", "Found_Name": "Fremd Holding AG",
       "Found_Address": "Musterweg 1, 12345 Musterstadt", "VAT_Found_Source_URL": "https://www.fremd.example/impressum",
       "Issue_Flags": "MAYBE_PARENT_COMPANY", "Name_Check": "DIFFERENT_NAME", "Address_Check": "OTHER_TOWN",
       "VAT_Why": "old why"}


class TestOutcomes:
    @pytest.mark.asyncio
    async def test_a_passing_candidate_replaces_the_old_number(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(EMAIL_INP)
        c = row_outcome(r, OLD, "Robert Bosch GmbH", "70839", "GERLINGEN")
        assert c["AL_Outcome"] == "new number" and c["VAT_Proposed"] == "DE811128135"
        assert c["VAT_Decision"] == "Auto-accept" and c["VAT_Withdrawn_Number"] is None

    @pytest.mark.asyncio
    async def test_the_same_number_is_confirmed(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)}).lookup(EMAIL_INP)
        c = row_outcome(r, dict(OLD, VAT_Proposed="DE 811 128 135"), "Robert Bosch GmbH", "70839", "GERLINGEN")
        assert c["AL_Outcome"] == "old number confirmed"

    @pytest.mark.asyncio
    async def test_nothing_passes_withdraws_the_old_number(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {}).lookup(BOSCH)
        c = row_outcome(r, OLD, "Robert Bosch GmbH", "70839", "GERLINGEN")
        assert c["AL_Outcome"] == "withdrawn" and c["VAT_Proposed"] is None and c["VAT_Decision"] == "Not found"
        assert c["Not_Found_Reason"] == "ONLY_OTHER_COMPANY_FOUND"
        assert (c["VAT_Withdrawn_Number"], c["VAT_Withdrawn_Found_Name"], c["VAT_Withdrawn_Source_URL"]) == (
            "DE129274235", "Fremd Holding AG", "https://www.fremd.example/impressum")
        assert c["VAT_Why"] == ("The only number found belongs to the parent or group company 'Fremd Holding AG' "
                                "in Musterstadt; no page gives a number to 'Robert Bosch GmbH'.")

    def test_without_the_parent_flag_the_why_names_the_company(self):
        c = row_outcome(FoundResult("llm_web", "NOT_FOUND", usage={"al": {"outcome": NOTHING_PASSES}}),
                        dict(OLD, Issue_Flags=None), "Robert Bosch GmbH", "70839", "GERLINGEN")
        assert c["VAT_Why"].startswith("The only number found belongs to 'Fremd Holding AG' in Musterstadt;")

    @pytest.mark.asyncio
    async def test_a_technical_failure_keeps_the_old_row(self, tmp_path):
        model = Model(run=[RuntimeError("Azure 500")])
        r = await _engine(tmp_path, model, {}).lookup(BOSCH)
        assert r.usage["al"]["outcome"] == NOT_RECHECKED and r.status.startswith("LOOKUP_INCOMPLETE")
        c = row_outcome(r, OLD, "Robert Bosch GmbH", "70839", "GERLINGEN")
        assert c["AL_Outcome"] == "not re-checked" and c["VAT_Proposed"] == "DE129274235"
        assert c["VAT_Decision"] == "Conflict" and c["VAT_Why"] == "old why"
        assert c["AL_Not_Rechecked_Reason"] == "LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_an_own_host_that_refuses_us_keeps_the_old_row(self, tmp_path):
        model = Model(run=[run("q", [])], read=[read(found())])
        r = await _engine(tmp_path, model, {IMPRESSUM: 403}).lookup(EMAIL_INP)
        assert r.usage["al"]["outcome"] == NOT_RECHECKED
        assert row_outcome(r, OLD, "Robert Bosch GmbH", "70839", "GERLINGEN")["VAT_Proposed"] == "DE129274235"

    def test_the_test_sample_is_40_rows_of_distinct_entities(self):
        rows = []
        for i in range(200):
            rows.append({"Customer": str(i), "Issue_Flags": "MAYBE_PARENT_COMPANY" if i % 3 == 0 else None,
                         "Check_Email_Domain": ("Matches source site" if i % 17 == 0 else
                                                "Differs from source site" if i % 13 == 0 else "No email")})
        forms = [i % 2 == 0 for i in range(200)]
        keys = [f"e{i // 2}" for i in range(200)]
        idx = pick_test_rows(rows, keys, forms)
        assert len(idx) == 40 and len({keys[i] for i in idx}) == 40 and idx == pick_test_rows(rows, keys, forms)
        strata = [stratum_of(rows[i], forms[i]) for i in idx]
        assert strata.count("email_match") == 2 and strata.count("email_other") == 3


# ── 6. The permanent rule (VAT_REJECT_OTHER_NAME_OTHER_TOWN) ────────────────

class TestPermanentRule:
    def test_other_name_other_town_is_withdrawn(self):
        r = cand(name_match="PARTIAL", attributed_to="Bosch Beispiel Service GmbH", address_match="DIFFERENT_TOWN",
                 published_address="Musterweg 2, 12345 Musterstadt")
        w = reject_other_name_other_town(r)
        assert w.value is None and w.status == "NOT_FOUND:ONLY_OTHER_COMPANY_FOUND"
        cols = review_columns(w, "Robert Bosch GmbH", "70839", "GERLINGEN")
        assert cols["VAT_Decision"] == "Not found" and cols["Not_Found_Reason"] == "ONLY_OTHER_COMPANY_FOUND"
        assert cols["VAT_Withdrawn_Number"] == "DE811128135"
        assert cols["VAT_Withdrawn_Found_Name"] == "Bosch Beispiel Service GmbH"
        assert "in Musterstadt" in cols["VAT_Why"]

    def test_a_different_name_too(self):
        r = cand(name_match="MISMATCH", attributed_to="Fremd Holding AG", address_match="DIFFERENT_TOWN")
        assert reject_other_name_other_town(r).value is None

    def test_same_name_in_another_town_stays_review(self):
        r = cand(address_match="DIFFERENT_TOWN")
        assert reject_other_name_other_town(r) is r and r.decision == MANUAL_REVIEW

    def test_a_contained_name_in_another_town_stays(self):
        r = cand(name_match="PARTIAL", issues=("NAME_CONTAINED",), address_match="DIFFERENT_TOWN")
        assert reject_other_name_other_town(r) is r

    def test_other_name_in_saps_town_stays(self):
        r = cand(name_match="PARTIAL", address_match="SAME_TOWN")
        assert reject_other_name_other_town(r) is r

    def test_an_office_of_the_same_legal_entity_stays(self):
        # "Arrow Central Europe GmbH Büro Hamb" (SAP) and the head office's page.
        r = cand(name_match="PARTIAL", attributed_to="Arrow Central Europe GmbH", address_match="DIFFERENT_TOWN",
                 usage={"entity": "Arrow Central Europe GmbH Büro Hamb",
                        "acceptable_names": ["Arrow Central Europe GmbH Büro Hamb"]})
        assert reject_other_name_other_town(r) is r

    def test_another_company_than_the_cut_name_is_still_withdrawn(self):
        r = cand(name_match="PARTIAL", attributed_to="Arrow Holding GmbH", address_match="DIFFERENT_TOWN",
                 usage={"entity": "Arrow Central Europe GmbH Büro Hamb"})
        assert reject_other_name_other_town(r).value is None
        # Nothing after the form: no cut, the rule as before.
        r = cand(name_match="PARTIAL", attributed_to="Carl Zeiss AG", address_match="DIFFERENT_TOWN",
                 usage={"entity": "Carl Zeiss MicroImaging GmbH"})
        assert reject_other_name_other_town(r).value is None

    def test_name_up_to_form(self):
        from vat.name_prep import name_up_to_form
        assert name_up_to_form("Arrow Central Europe GmbH Büro Hamb") == "Arrow Central Europe GmbH"
        assert name_up_to_form("Märklen GmbH & Co.KG Niederlassung") == "Märklen GmbH & Co.KG"
        assert name_up_to_form("Arrow Central Europe GmbH") is None
        assert name_up_to_form("IBH Ing.-Büro und Handels vertretun") is None
        assert name_up_to_form(None) is None

    def test_the_withdrawn_number_keeps_the_parent_hint(self):
        r = cand(name_match="MISMATCH", attributed_to="Fremd Holding AG", address_match="DIFFERENT_TOWN",
                 issues=("PARENT_ENTITY_CANDIDATE",))
        assert withdraw(r).usage["withdrawn"]["parent"] is True

    @pytest.mark.asyncio
    async def test_validate_rows_applies_it_and_can_be_switched_off(self):
        from vat.validate import validate_rows

        class One:
            mode, agent_version = "llm_web", "test"

            async def lookup(self, inp):
                return cand(name_match="PARTIAL", attributed_to="Bosch Beispiel Service GmbH",
                            address_match="DIFFERENT_TOWN")

            def stats(self):
                return {}

            async def aclose(self):
                pass

        class NoVies:
            enabled, frozen = False, True
            write_failures = type("W", (), {"count": 0})()

            async def check_many(self, formats):
                return {}

        rows = [{"Customer": "1", "Name": "Robert Bosch GmbH", "City": "GERLINGEN", "Postal Code": "70839",
                 "Country/Region Key": "DE", "VAT Registration No.": None}]
        out, _ = await validate_rows(rows, NoVies(), lookup=One(), output_columns="review", reject_other_town=True)
        assert out[0]["VAT_Decision"] == "Not found" and out[0]["VAT_Withdrawn_Number"] == "DE811128135"
        out, _ = await validate_rows(rows, NoVies(), lookup=One(), output_columns="review", reject_other_town=False)
        assert out[0]["VAT_Decision"] == "Review" and out[0]["VAT_Withdrawn_Number"] is None


# ── The withdrawn row from an old row ──────────────────────────────────────

def test_withdrawn_result_carries_the_old_values():
    w = withdrawn_result(OLD)
    assert w.status == "NOT_FOUND:ONLY_OTHER_COMPANY_FOUND" and w.usage["withdrawn"]["number"] == "DE129274235"
    assert json.dumps(w.usage["withdrawn"])  # plain data for the usage JSON


# ── VAT_EXACT_NAME_SEARCH in the normal hybrid lookup (off by default) ──────

class TestExactNameSearchSetting:
    @pytest.mark.asyncio
    async def test_off_the_model_runs_search_2_as_before(self, tmp_path):
        from tests.test_vat_hybrid import _engine as hybrid

        model = Model(run=[run("q1", [])])
        await hybrid(tmp_path, model, {}).lookup(BOSCH)
        assert [k for k, _ in model.calls].count("second") == 1 and [k for k, _ in model.calls].count("run") == 1

    @pytest.mark.asyncio
    async def test_on_the_exact_legal_name_replaces_search_2(self, tmp_path):
        from tests.test_vat_hybrid import _engine as hybrid

        model = Model(run=[run("q1", []), run("q2", [IMPRESSUM])], read=[read(found())])
        r = await hybrid(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, BODY)},
                         vat_exact_name_search=True).lookup(BOSCH)
        runs = [u for k, u in model.calls if k == "run"]
        assert len(runs) == 2 and '"Robert Bosch GmbH" Impressum USt-IdNr' in runs[1]
        assert "second" not in [k for k, _ in model.calls]
        assert r.value == "DE811128135" and "FOUND_BY_SEARCH:exact" in r.flags
