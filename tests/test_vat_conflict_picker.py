"""The Conflict picker (vat/conflict_picker.py): the code checks on the model's answer."""

import pytest

from vat import conflict_picker as cp

#: An invented group page: the parent first, then two subsidiaries.
PAGE = ("Impressum Musterwerk Holding AG Hauptstraße 1 70173 Stuttgart USt-IdNr.: DE111111111 "
        "Tochtergesellschaften: Musterwerk Antriebe GmbH Werkstraße 5 89073 Ulm USt-IdNr.: DE 222 222 222 "
        "Musterwerk Service GmbH Ringweg 2 76131 Karlsruhe USt-IdNr.: DE333333333")
NAMES = ("Musterwerk Antriebe GmbH",)


def answer(number, quote, confidence=0.9):
    return cp.verdict_of({"number": number, "quote": quote, "confidence": confidence, "reason": "x"})


def test_numbers_on_page_in_order_with_context():
    numbers = cp.numbers_on_page(PAGE)
    assert [v for v, _ in numbers] == ["DE111111111", "DE222222222", "DE333333333"]
    assert "Musterwerk Antriebe GmbH" in numbers[1][1]


def test_block_before_stops_at_the_previous_number():
    block = cp.block_before(PAGE, "DE222222222")
    assert "Musterwerk Antriebe GmbH" in block and "Holding" not in block


@pytest.mark.parametrize("verdict, result", [
    (answer("DE222222222", "Musterwerk Antriebe GmbH Werkstraße 5 89073 Ulm USt-IdNr.: DE 222 222 222"), cp.PICKED),
    (answer("DE 222 222 222", "Antriebe GmbH Werkstraße 5 89073 Ulm USt-IdNr.: DE 222 222 222"), cp.PICKED),
    # The parent's number: the text before it names the holding, not the customer.
    (answer("DE111111111", "Musterwerk Holding AG Hauptstraße 1 70173 Stuttgart USt-IdNr.: DE111111111"), cp.UNSURE),
    (answer("DE999999999", "Musterwerk Antriebe GmbH DE999999999"), cp.UNSURE),          # not on the page
    (answer("DE222222222", "Musterwerk Antriebe GmbH, USt-IdNr. DE222222222"), cp.UNSURE),  # invented quote
    (answer("DE222222222", "Musterwerk Antriebe GmbH Werkstraße 5"), cp.UNSURE),         # quote lacks number
    (answer("DE222222222", "Musterwerk Antriebe GmbH Werkstraße 5 89073 Ulm USt-IdNr.: DE 222 222 222", 0.6),
     cp.UNSURE),
    (answer(None, None, 0.9), cp.NONE),
    (answer(None, None, 0.5), cp.UNSURE),
    (None, cp.UNSURE),
])
def test_outcome(verdict, result):
    assert cp.outcome(verdict, PAGE, cp.numbers_on_page(PAGE), NAMES)[0] == result


@pytest.mark.parametrize("quote, on_page", [
    ("Musterwerk Antriebe GmbH … USt-IdNr.: DE 222 222 222", True),
    ("Musterwerk Antriebe GmbH ... USt-IdNr.: DE 222 222 222", True),
    ("USt-IdNr.: DE 222 222 222 … Musterwerk Antriebe GmbH", False),   # pieces out of order
    ("Musterwerk Antriebe GmbH … USt-IdNr.: DE 444 444 444", False),   # a piece not on the page
])
def test_quote_on_page_allows_left_out_text(quote, on_page):
    assert cp.quote_on_page(quote, PAGE) is on_page


def test_a_name_not_on_the_page_is_never_picked():
    verdict = answer("DE333333333", "Musterwerk Service GmbH Ringweg 2 76131 Karlsruhe USt-IdNr.: DE333333333")
    assert cp.outcome(verdict, PAGE, cp.numbers_on_page(PAGE), ("Beispiel Logistik GmbH",))[0] == cp.UNSURE


def test_prompt_lists_every_number_and_the_record():
    record = cp.record_text(("Musterwerk Antriebe", "GmbH", None, None), "Werkstraße 5", "89073", "ULM")
    _, user = cp.prompt(record, "https://example.test/impressum", cp.numbers_on_page(PAGE))
    assert record == "Musterwerk Antriebe | GmbH | Werkstraße 5 | 89073 ULM"
    assert "1. DE111111111" in user and "3. DE333333333" in user and record in user


def test_verdict_of_rejects_a_bad_answer():
    assert cp.verdict_of({"number": "DE1", "quote": None, "confidence": "high", "reason": ""}) is None
    assert cp.verdict_of("DE222222222") is None
