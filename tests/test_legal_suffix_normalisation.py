"""UC 17 — Long-form legal-suffix normalisation.

A company name must resolve on the same enrichment path regardless of
which legal form the source system recorded. Production bug: two rows at
one address, "Carl Zeiss AG" and "Carl Zeiss Aktiengesellschaft",
diverged — the AG form enriched cleanly while the long form missed ROR,
fed the raw suffix into the SERP query, and resolved to an unrelated
site (canada.ca). Collapsing the long form to "AG" during preprocessing
keeps both on one path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import _normalise_legal_suffix, preprocess_record


def _run(name1):
    return preprocess_record(
        name1=name1,
        name2=None,
        name3=None,
        contact=None,
        email=None,
        street1=None,
        street2=None,
        street3=None,
        llm_person_verdicts={},
    )


class TestNormaliseHelper:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Carl Zeiss Aktiengesellschaft", "Carl Zeiss AG"),
            ("Acme Incorporated", "Acme Inc"),
            ("Globex Corporation", "Globex Corp"),
            ("Initech Limited Liability Company", "Initech LLC"),
            ("Initech Limited Liability Partnership", "Initech LLP"),
            ("Beispiel Gesellschaft mit beschränkter Haftung", "Beispiel GmbH"),
            ("Beispiel Gesellschaft mit beschrankter Haftung", "Beispiel GmbH"),
        ],
    )
    def test_longform_collapses(self, raw, expected):
        out, changed = _normalise_legal_suffix(raw)
        assert out == expected
        assert changed is True

    @pytest.mark.parametrize(
        "raw",
        [
            "Carl Zeiss AG",          # already short — unchanged
            "Acme Inc",
            "Limited Brands",         # ambiguous bare word — left alone
            "The Walt Disney Company",
            "University of Stuttgart",
        ],
    )
    def test_unambiguous_and_short_forms_unchanged(self, raw):
        out, changed = _normalise_legal_suffix(raw)
        assert out == raw
        assert changed is False

    def test_none_and_empty(self):
        assert _normalise_legal_suffix(None) == ("", False)
        assert _normalise_legal_suffix("") == ("", False)


class TestPreprocessIntegration:
    def test_aktiengesellschaft_becomes_ag(self):
        res = _run("Carl Zeiss Aktiengesellschaft")
        assert res.name1 == "Carl Zeiss AG"
        assert 17 in res.use_cases

    def test_ag_sibling_unchanged_so_both_converge(self):
        long_form = _run("Carl Zeiss Aktiengesellschaft").name1
        short_form = _run("Carl Zeiss AG").name1
        assert long_form == short_form == "Carl Zeiss AG"

    def test_no_use_case_when_already_short(self):
        res = _run("Carl Zeiss AG")
        assert 17 not in res.use_cases
