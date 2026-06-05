"""Tests for strip_address_fragments — removing address noise from name fields."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.text_utils import strip_address_fragments


# ── Trailing location-suffix stripping (no street/zip present) ───────────────

def test_trailing_city_suffix_stripped_when_city_matches():
    # "HCA Florida University Hospital, Davie" with City "Davie" → drop ", Davie".
    assert (
        strip_address_fragments("HCA Florida University Hospital, Davie", city="Davie")
        == "HCA Florida University Hospital"
    )


def test_trailing_city_and_state_suffix_peeled():
    assert (
        strip_address_fragments(
            "HCA Florida University Hospital, Davie, FL",
            city="Davie", state="FL",
        )
        == "HCA Florida University Hospital"
    )


def test_trailing_city_not_stripped_when_city_unknown():
    # Without a City field we can't know "Davie" is a location — leave it.
    assert (
        strip_address_fragments("HCA Florida University Hospital, Davie", city=None)
        == "HCA Florida University Hospital, Davie"
    )


# ── Safety: interior words and word-bounded state codes are untouched ────────

def test_interior_state_word_not_stripped():
    # "Florida" is part of the org name, not a trailing suffix.
    assert (
        strip_address_fragments("HCA Florida University Hospital", state="Florida")
        == "HCA Florida University Hospital"
    )


def test_state_code_word_bounded():
    assert (
        strip_address_fragments("University of Florida", state="FL")
        == "University of Florida"
    )


def test_no_address_fields_is_noop():
    assert (
        strip_address_fragments("Massachusetts Institute of Technology")
        == "Massachusetts Institute of Technology"
    )
