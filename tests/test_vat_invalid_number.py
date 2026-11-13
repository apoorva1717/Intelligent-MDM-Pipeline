"""A found number that cannot be the record's VAT ID is never proposed
(2026-11-10): the 22 kinds seen in the 7,358 DE e-invoicing rows."""

from __future__ import annotations

import pytest

from tests.mocks import vies_mock
from tests.mocks.vat_lookup_mock import page
from tests.mocks.vies_mock import ViesStub
from tests.test_vat_agent import IMPRESSUM, Agent, _engine, _Hybrid, _rows, _settings, agent_answer
from tests.test_vat_hybrid import found
from utils.cache import EvidenceCache
from vat.lookup import FOUND_PENDING, NOT_FOUND, FoundResult, reject_invalid_number
from vat.lookup_agent import AGENT_TRIED, agent_eligible
from vat.validate import validate_rows
from vat.vies_client import VIESClient


class _Engine:
    mode = "llm_web"

    def __init__(self, value):
        self.value = value

    async def lookup(self, inp):
        return FoundResult("llm_web", FOUND_PENDING, value=self.value, source_url="https://example.de/impressum",
                           attributed_to="Firma GmbH", evidence=f"USt-IdNr.: {self.value}")

    def stats(self):
        return {}

    async def aclose(self):
        pass


def _vies(tmp_path):
    return VIESClient(_settings(), cache=EvidenceCache(tmp_path),
                      transport=ViesStub(vies_mock.echo_valid_undisclosed).transport)


ROW = {"Customer": "1", "Country/Region Key": "DE", "Name": "Firma GmbH", "VAT Registration No.": None,
       "Street": "Musterstr.", "House Number": "1", "Postal Code": "10115", "City": "BERLIN"}

# One of each kind found in the run, as printed on the source page.
INVALID = [
    ("DE123456789", "wrong check digit"),            # template / sample page
    ("DE 137 919 139", "wrong check digit"),         # typo on the own page
    ("DE1294204004", "wrong length"),                # ten digits
    ("NL003085879B01", "another country's number"),  # the Dutch parent's
    ("FR93602036444", "another country's number"),
    ("DK1404973", "wrong length"),                   # Danish prefix, and too short for DK
    ("GB709301360", "not an EU VAT ID"),
]


class TestRejectInvalidNumber:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("value,why", INVALID)
    async def test_never_proposed(self, tmp_path, value, why):
        [row], summary = await validate_rows([ROW], _vies(tmp_path), lookup=_Engine(value), output_columns="review")
        assert row["VAT_Proposed"] is None and row["VAT_Decision"] == "Not found"
        assert row["Not_Found_Reason"] == "INVALID_VAT_NUMBER"
        assert f"({value.replace(' ', '')}: {why})" in row["VAT_Why"]
        assert row["VAT_Found_Source_URL"] == "https://example.de/impressum"   # the page stays for the reviewer
        assert "_rejected" not in row
        assert summary["lookup"]["kpis"]["decision"] == {"NOT_FOUND": 1}

    @pytest.mark.asyncio
    async def test_a_valid_number_is_untouched(self, tmp_path):
        [row], _ = await validate_rows([ROW], _vies(tmp_path), lookup=_Engine("DE 811 193 231"),
                                       output_columns="review")
        assert row["VAT_Proposed"] == "DE811193231" and row["Not_Found_Reason"] is None

    def test_the_agent_pass_looks_again(self):
        rejected = reject_invalid_number(FoundResult("llm_web", FOUND_PENDING, value="DE123456789"), "DE")
        assert agent_eligible(rejected)

    @pytest.mark.asyncio
    async def test_an_invalid_number_from_the_agent_does_not_replace_the_lookup(self, tmp_path):
        bad = "DE123456789"
        model = Agent(agent_answer(vat=bad, quote=f"USt-IdNr. {bad}"),
                      reads=[found(vat=bad, quote=f"USt-IdNr. {bad}")])
        body = f"Impressum Robert Bosch GmbH Robert-Bosch-Platz 1 70839 Gerlingen USt-IdNr. {bad}"
        agent = _engine(tmp_path, model, {IMPRESSUM: page(IMPRESSUM, body)})
        out, _ = await validate_rows(_rows()[:1], _vies(tmp_path / "v"), lookup=_Hybrid(), agent=agent)
        assert out[0]["VAT_Found_Value"] is None
        assert out[0]["VAT_Found_Status"] == "NOT_FOUND:NO_NUMBER_ON_OWN_SITE"
        assert AGENT_TRIED in out[0]["VAT_Found_Flags"]

    def test_another_eu_country_is_fine_for_its_own_records(self):
        found = FoundResult("llm_web", FOUND_PENDING, value="NL003085879B01")
        assert reject_invalid_number(found, "NL") is found

    def test_not_a_candidate_is_untouched(self):
        nothing = FoundResult("llm_web", NOT_FOUND, value=None)
        assert reject_invalid_number(nothing, "DE") is nothing
