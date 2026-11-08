"""The reviewer's columns (vat/review_columns.py, 2026-11-08): plain codes a
reviewer can filter, one sentence why, and the output layout
(VAT_OUTPUT_COLUMNS). Company data: obviously fake (Muster), or public
Impressum data from the fixtures."""

from __future__ import annotations

import re

import pytest
from openpyxl import Workbook

from tests.test_vat_agent import _Hybrid, _rows
from tests.test_vat_log_columns import _vies
from vat.costs import COST_COLUMNS
from vat.lookup import FoundResult
from vat.review_columns import (
    AROUND_THE_NUMBER, REVIEW_COLUMNS, apply_vies, found_address, issue_flags, review_columns, town, why,
)
from vat.validate import validate_rows
from vat.vies_retry import apply_to_workbook

URL = "https://www.muster.example/impressum"


def _found(**kw):
    base = dict(value="DE811128135", source_url=URL, source_type="COMPANY_IMPRESSUM", attributed_to="Muster GmbH",
                evidence="USt-IdNr.: DE 811 128 135", name_match="EXACT", address_match="EXACT",
                validation_status="VALID", published_address="Musterweg 1, 12345 Musterstadt", match_score=100)
    return FoundResult("llm_web", kw.pop("status", "FOUND_VERIFIED"), **{**base, **kw})


def _row(r, name="Muster GmbH", city="MUSTERSTADT"):
    return review_columns(r, name, "12345", city)


class TestTheChecks:
    @pytest.mark.parametrize("match, issues, expected", [
        ("EXACT", (), "SAME_NAME"),
        ("PARTIAL", ("NAME_CONTAINED",), "SAP_NAME_PLUS_MORE"),
        ("PARTIAL", ("NAME_PARTIAL",), "SIMILAR_NAME"),
        ("MISMATCH", ("NAME_PARTIAL",), "DIFFERENT_NAME"),
    ])
    def test_name_check(self, match, issues, expected):
        assert _row(_found(name_match=match, issues=issues))["Name_Check"] == expected

    @pytest.mark.parametrize("level, expected", [
        ("EXACT", "SAME_ADDRESS"), ("SAME_TOWN", "OTHER_STREET_SAME_TOWN"), ("DIFFERENT_TOWN", "OTHER_TOWN"),
        ("NO_ADDRESS", "NO_ADDRESS_ON_PAGE"),
    ])
    def test_address_check(self, level, expected):
        assert _row(_found(address_match=level))["Address_Check"] == expected

    def test_a_clean_auto_accept_has_no_flags(self):
        row = _row(_found())
        assert row["VAT_Decision"] == "Auto-accept" and row["Issue_Flags"] is None and row["VIES"] == "Valid"
        assert row["VAT_Why"] == "Number from the organisation's own website; name matches, address matches."

    def test_the_proposed_number_spaced_differently_is_not_another_number(self):
        r = _found(flags=("OTHER_VAT_IDS_ON_PAGE:DE 811 128 135",))
        assert "SEVERAL_NUMBERS_ON_PAGE" not in issue_flags(r)
        assert "SEVERAL_NUMBERS_ON_PAGE" in issue_flags(_found(flags=("OTHER_VAT_IDS_ON_PAGE:DE129274235",)))

    @pytest.mark.parametrize("kw, flag", [
        (dict(issues=("PARENT_ENTITY_CANDIDATE",)), "MAYBE_PARENT_COMPANY"),
        (dict(issues=("SUCCESSOR_CANDIDATE",)), "MAYBE_RENAMED_OR_SUCCESSOR"),
        (dict(issues=("LEGAL_FORM_DIFFERS:gmbh/ag",)), "LEGAL_FORM_DIFFERS"),
        (dict(issues=("DEPARTMENT",)), "DEPARTMENT_OR_UNIT"),
        (dict(issues=("BRANCH",)), "SITE_OR_PLANT"),
        (dict(issues=("REGISTER_MISMATCH",)), "REGISTER_NO_DIFFERS"),
        (dict(issues=("SISTER_ENTITY_ON_PAGE",)), "SISTER_COMPANY_ON_PAGE"),
        (dict(source_type="AGGREGATOR"), "FROM_DIRECTORY"),
        (dict(flags=("CONFLICTING_SOURCES:DE129274235",)), "OTHER_SOURCE_OTHER_NUMBER"),
        (dict(flags=("AMBIGUOUS_ON_PAGE",)), "SEVERAL_NUMBERS_ON_PAGE"),
        (dict(evidence=None), "NUMBER_NOT_QUOTED"),
        (dict(flags=("GENERIC_NAME",)), "NAME_SHARED_BY_OTHERS"),
    ])
    def test_issue_flags(self, kw, flag):
        assert flag in issue_flags(_found(**kw))

    def test_an_archive_copy_is_not_a_directory(self):
        flags = issue_flags(_found(source_type="WEB_ARCHIVE", flags=("ARCHIVE_SOURCE",)))
        assert "FROM_OLD_ARCHIVE" in flags and "FROM_DIRECTORY" not in flags

    @pytest.mark.parametrize("status, reason", [
        ("NOT_FOUND:NO_OWN_SITE", "NO_WEBSITE_FOUND"), ("NOT_FOUND:NO_NUMBER_ON_OWN_SITE", "WEBSITE_HAS_NO_VAT_ID"),
        ("NOT_FOUND:OWN_SITE_SCRIPT_ONLY", "WEBSITE_NOT_READABLE"), ("NOT_FOUND:OWN_SITE_BLOCKED", "WEBSITE_BLOCKED_US"),
        ("NOT_FOUND:PARENT_ONLY", "ONLY_PARENT_SITE_FOUND"), ("NOT_FOUND_NO_VAT_EXPECTED:STATED", "PAGE_SAYS_NO_VAT_ID"),
        ("NOT_FOUND_NO_VAT_EXPECTED:INFERRED", "PUBLIC_BODY_NO_VAT"), ("LOOKUP_INCOMPLETE:HTTP_403", "WEBSITE_BLOCKED_US"),
        ("LOOKUP_INCOMPLETE", "SEARCH_INCOMPLETE"), ("NOT_FOUND", "NO_NUMBER_FOUND"),
    ])
    def test_every_not_found_has_a_reason(self, status, reason):
        row = _row(FoundResult("llm_web", status))
        assert row["Not_Found_Reason"] == reason and row["VAT_Decision"] == "Not found"
        assert row["VAT_Why"] and row["Name_Check"] is None and row["Issue_Flags"] is None

    @pytest.mark.parametrize("status, flags, reason, words", [
        ("LOOKUP_INCOMPLETE", ("LLM_WEB_SEARCH", "LLM_UNAVAILABLE"), "MODEL_SERVICE_FAILED", "model service"),
        ("LOOKUP_INCOMPLETE", ("LLM_WEB_SEARCH", "MALFORMED_OUTPUT"), "MODEL_ANSWER_UNREADABLE", "could not be read"),
        ("LOOKUP_INCOMPLETE:CONTENT_FILTER", ("CONTENT_FILTER",), "CONTENT_FILTER_STOPPED", "content filter"),
        ("LOOKUP_INCOMPLETE", ("FROZEN_CACHE_MISS",), "NOT_IN_REPLAY", "replays"),
        ("LOOKUP_INCOMPLETE", ("LLM_WEB_SEARCH", "BUDGET_EXCEEDED"), "COST_CAP_REACHED", "cost cap"),
        # the lookup's own failure, not the agent pass's budget stop after it
        ("LOOKUP_INCOMPLETE", ("LLM_UNAVAILABLE", "AGENT_SKIPPED_BUDGET", "BUDGET_EXCEEDED"),
         "MODEL_SERVICE_FAILED", "model service"),
    ])
    def test_why_a_search_could_not_finish(self, status, flags, reason, words):
        row = _row(FoundResult("llm_web", status, flags=flags))
        assert row["Not_Found_Reason"] == reason and words in row["VAT_Why"]
        assert "budget ran out" not in row["VAT_Why"]

    def test_a_row_not_looked_up_is_blank(self):
        assert review_columns(None) == dict.fromkeys(REVIEW_COLUMNS)


class TestFoundAddressAndEvidence:
    def test_the_readers_copy_first(self):
        assert found_address(_found(), "12345", "MUSTERSTADT") == "Musterweg 1, 12345 Musterstadt"

    def test_else_the_block_the_check_matched(self):
        text = ("Impressum Muster GmbH Musterweg 1 12345 Musterstadt Telefon: +49 1234 5678 … "
                "Andere AG Weg 2 54321 Anderstadt")
        r = _found(published_address=None, address_text=text)
        assert found_address(r, "12345", "MUSTERSTADT") == "Muster GmbH Musterweg 1 12345 Musterstadt"

    def test_a_number_without_a_quote_shows_the_text_around_it(self):
        r = _found(evidence=None, usage={"evidence_snippet": "Muster GmbH USt-IdNr. DE 811 128 135"})
        assert _row(r)["VAT_Found_Evidence"] == AROUND_THE_NUMBER + "Muster GmbH USt-IdNr. DE 811 128 135"


class TestWhy:
    @pytest.mark.parametrize("kw", [
        dict(issues=("PARENT_ENTITY_CANDIDATE",), name_match="MISMATCH", attributed_to="Muster Holding GmbH"),
        dict(source_type="AGGREGATOR", name_match="PARTIAL", issues=("NAME_PARTIAL",)),
        dict(address_match="DIFFERENT_TOWN"), dict(address_match="NO_ADDRESS"),
        dict(flags=("AMBIGUOUS_ON_PAGE",), evidence=None), dict(issues=("LEGAL_FORM_DIFFERS:gmbh/ag",)),
        dict(issues=("BRANCH",), address_match="DIFFERENT_TOWN"), dict(validation_status="INVALID"),
        dict(attributed_to=None, name_match=None), dict(issues=("DEPARTMENT",)),
    ])
    def test_plain_words_only(self, kw):
        text = _row(_found(**kw))["VAT_Why"]
        assert text and not re.search(r"[A-Z]{3,}_[A-Z]", text) and "None" not in text and "''" not in text
        assert text.count(".") <= 4  # two sentences at most (abbreviations aside)

    def test_the_parent_case(self):
        text = _row(_found(issues=("PARENT_ENTITY_CANDIDATE",), name_match="MISMATCH",
                           attributed_to="Muster Holding GmbH"), name="Muster Keramik GmbH")["VAT_Why"]
        assert text.startswith("The page belongs to 'Muster Holding GmbH', possibly the parent")

    def test_the_sap_town_as_written(self):
        assert town("FRANKFURT AM MAIN") == "Frankfurt am Main" and town("Bad Kreuznach") == "Bad Kreuznach"
        assert "in Frankfurt am Main" in _row(FoundResult("llm_web", "NOT_FOUND:NO_OWN_SITE"),
                                              city="FRANKFURT AM MAIN")["VAT_Why"]


class TestViesLater:
    def test_valid_keeps_the_decision_and_updates_why(self):
        row = _row(_found(validation_status="PENDING", status="FOUND_UNVERIFIED:VIES_PENDING"))
        assert row["VIES"] == "Pending" and row["VAT_Why"].endswith("Goes to SAP once VIES confirms it.")
        assert apply_vies(row, {"DE811128135": ("VALID", "2026-11-08")}, "Muster GmbH")
        assert (row["VIES"], row["VAT_Decision"]) == ("Valid", "Auto-accept")
        assert not row["VAT_Why"].endswith("once VIES confirms it.")

    def test_invalid_sends_it_to_review(self):
        row = _row(_found(validation_status="PENDING", status="FOUND_UNVERIFIED:VIES_PENDING"))
        assert apply_vies(row, {"DE811128135": ("INVALID", "2026-11-08")})
        assert (row["VIES"], row["VAT_Decision"], row["VAT_Found_Confidence"]) == ("Invalid", "Review",
                                                                                   "LOW_CONFIDENCE")
        assert row["VAT_Why"].startswith("VIES says this number is not valid.")

    def test_on_a_written_workbook(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        row = _row(_found(validation_status="PENDING", status="FOUND_UNVERIFIED:VIES_PENDING"))
        ws.append(["Customer", "Name", "City", *REVIEW_COLUMNS])
        ws.append(["1", "Muster GmbH", "MUSTERSTADT", *[row[c] for c in REVIEW_COLUMNS]])
        assert apply_to_workbook(wb, {"DE811128135": ("INVALID", "2026-11-08")}) == 1
        headers = [c.value for c in ws[1]]
        assert ws.cell(row=2, column=headers.index("VIES") + 1).value == "Invalid"


class TestTheLayout:
    @pytest.mark.asyncio
    async def test_review_writes_the_input_and_the_reviewers_columns(self, tmp_path):
        rows = _rows()
        out, _ = await validate_rows(rows, _vies(tmp_path), lookup=_Hybrid(), output_columns="review")
        added = [k for k in out[0] if k not in rows[0]]
        assert added == [c for c in REVIEW_COLUMNS + list(COST_COLUMNS.values()) if c in added]
        assert set(REVIEW_COLUMNS) <= set(out[0]) and "VAT_Found_Value" not in out[0]
        assert all(k in out[0] and out[0][k] == v for k, v in rows[0].items())  # every input field as it was

    @pytest.mark.asyncio
    async def test_full_is_the_old_layout(self, tmp_path):
        out, _ = await validate_rows(_rows(), _vies(tmp_path), lookup=_Hybrid(), output_columns="full")
        assert "VAT_Found_Value" in out[0] and "VAT_Proposed" not in out[0] and "VAT_Why" not in out[0]
