"""Fix 7 — the identifier-token guard keys on case CONTRAST, not upper case.

Row 35 of the demo batch ("BRIGHAM & WOMENS HOSP", Customer 40000014) is in
ROR as `ror.org/04b6nzv94` and ROR's own affiliation scorer returned it at
1.0, yet Tier 1 missed on the first pass. Cause: `_extract_identifier_tokens`
treated every short all-caps token as a distinguishing acronym. SAP master
data is stored entirely in upper case, so in "BRIGHAM & WOMENS HOSP" the
guard read "HOSP" as an acronym and required it to appear literally in the
candidate. "HOSP" is not in "Brigham and Women's Hospital", so the
exact/subset/substring shortcuts were blocked and the fuzzy branch — raw
0.8163, over the 0.8 threshold — was capped to 0.7 and rejected. The record
only recovered via the Fix 2 retry, at the cost of an LLM round trip.

Controls A–D isolate the variable: A and C normalise to the *same* string
('brigham & womens hosp') and differ only in casing, so punctuation and the
unexpanded abbreviation are both excluded as causes.

Everything here runs offline against the ROR responses recorded in
`tests/fixtures/ror_repro/` (refresh with `python3 scripts/ror_repro.py
--record`). No test in this file touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from enrichment import tier1_ror  # noqa: E402
from enrichment.tier1_ror import (  # noqa: E402
    _compute_name_score,
    _extract_identifier_tokens,
    _guard_identifier_tokens,
    _score_org,
    call_ror,
    clear_ror_cache,
)
from ror_repro import diagnose_score, fixture_handler, load_fixtures  # noqa: E402

BWH = "https://ror.org/04b6nzv94"
FSU = "https://ror.org/05g3dte14"
KENT_STATE = "https://ror.org/04xkxnk94"

BWH_NAMES = [
    {"value": "BWH", "types": ["acronym"]},
    {"value": "Brigham and Women's Hospital", "types": ["ror_display", "label"]},
    {"value": "The Brigham", "types": ["alias"]},
]


@pytest.fixture(autouse=True)
def _clear():
    clear_ror_cache()
    yield
    clear_ror_cache()


@pytest.fixture
def offline_ror(monkeypatch):
    """Point the ROR client at the recorded fixtures — no network."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(
            transport=httpx.MockTransport(fixture_handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(tier1_ror.httpx, "AsyncClient", factory)


# ───────────────────────── the all-caps case (the fix) ──────────────────────


def test_allcaps_query_reaches_the_scoring_shortcuts() -> None:
    """An entirely upper-case query must not have its casing read as acronyms.

    This is the regression: with the guard keyed on upper case alone, 'hosp'
    was demanded of the candidate and the score was capped at 0.7.
    """
    assert _guard_identifier_tokens("BRIGHAM & WOMENS HOSP") == set()
    score = _compute_name_score("BRIGHAM & WOMENS HOSP", BWH_NAMES, {"boston"})
    assert score >= 0.8, f"all-caps row 35 should match BWH, got {score}"


def test_casing_is_the_only_difference_between_a_miss_and_a_hit() -> None:
    """Controls A and C — same normalised string, only the casing differs.

    Proves the cause is casing, not the apostrophe in "Women's" and not the
    unexpanded "HOSP": both spellings normalise identically.
    """
    from enrichment.tier1_ror import _normalise_for_tokens

    upper, mixed = "BRIGHAM & WOMENS HOSP", "Brigham & Womens Hosp"
    assert _normalise_for_tokens(upper) == _normalise_for_tokens(mixed)
    assert _compute_name_score(upper, BWH_NAMES, {"boston"}) == pytest.approx(
        _compute_name_score(mixed, BWH_NAMES, {"boston"})
    )


@pytest.mark.asyncio
async def test_row35_hits_ror_on_the_first_pass(offline_ror) -> None:
    """End-to-end through call_ror, exactly as the orchestrator invokes it."""
    res = await call_ror(
        "BRIGHAM & WOMENS HOSP",
        country_code="US", country="US", city="BOSTON", state="MA",
    )
    assert res["matched"] is True
    assert res["ror_id"] == BWH
    assert res["official_name"] == "Brigham and Women's Hospital"
    # First pass = the plain affiliation strategy, no acronym-expanded retry.
    assert res["strategy"] == "affiliation"


@pytest.mark.asyncio
async def test_row35_is_classified_research_institution(offline_ror) -> None:
    res = await call_ror(
        "BRIGHAM & WOMENS HOSP",
        country_code="US", country="US", city="BOSTON", state="MA",
    )
    assert res["is_research_institution"] is True
    assert "healthcare" in res["org_types"]


@pytest.mark.asyncio
async def test_row35_yields_a_trusted_institution_domain(offline_ror) -> None:
    """Tier 2B needs this domain to build its `site:` department query."""
    res = await call_ror(
        "BRIGHAM & WOMENS HOSP",
        country_code="US", country="US", city="BOSTON", state="MA",
    )
    assert res["domain"] == "brighamandwomens.org"


# ───────────────── the contrast case: the guard still fires ─────────────────


def test_mixed_case_query_still_yields_identifier_tokens() -> None:
    """Case contrast is the signal the guard was designed for."""
    assert _guard_identifier_tokens("HFT Stuttgart") == {"hft"}


@pytest.mark.parametrize(
    "org_name",
    ["Stuttgart Observatory", "Marienhospital Stuttgart", "Klinikum Stuttgart"],
)
def test_hft_stuttgart_still_blocked_from_same_city_orgs(org_name: str) -> None:
    """'HFT Stuttgart' must not subset-match another Stuttgart org.

    The whole point of the guard: 'HFT' is 3 chars so the subset shortcut
    ignores it, leaving only the shared city token. Keeping the guard live
    for mixed-case queries is what stops that.
    """
    org = {"names": [{"value": org_name, "types": ["ror_display", "label"]}]}
    score = _score_org("HFT Stuttgart", org)
    assert score < 0.8, f"HFT Stuttgart must not match {org_name}, got {score}"


@pytest.mark.asyncio
async def test_hft_stuttgart_still_resolves_to_the_right_org(offline_ror) -> None:
    """Row 9 keeps resolving — and to the Hochschule, not a same-city org."""
    res = await call_ror(
        "HFT Stuttgart", country_code="DE", country="DE", city="STUTTGART",
    )
    assert res["matched"] is True
    assert "stuttgart" in (res["domain"] or "")
    assert "hft" in (res["domain"] or "")


def test_emsl_still_capped_against_asl() -> None:
    """The original mixed-case acronym false match stays blocked."""
    asl = {
        "names": [
            {"value": "ASL Analytical", "types": ["ror_display", "label"]},
            {"value": "ASL Analytical, Inc.", "types": ["alias"]},
        ],
    }
    assert _score_org("EMSL Analytical, Inc.", asl) < 0.8


# ──────────── the other guards are untouched by the case-contrast change ────


@pytest.mark.asyncio
async def test_country_guard_still_rejects_wrong_country_for_allcaps(
    monkeypatch,
) -> None:
    """BASF, README's country-guard case, in the ALL-CAPS spelling.

    The case-contrast change disables one guard on all-caps queries; this
    pins that the country guard is not the one, in exactly the input shape
    that now takes the new path.
    """
    def _org(ror_id: str, name: str, cc: str) -> dict:
        return {
            "id": ror_id,
            "names": [{"value": name, "types": ["ror_display"]}],
            "types": ["company"],
            "locations": [{"geonames_details": {
                "country_code": cc, "country_name": cc,
            }}],
            "relationships": [],
            "links": [],
        }

    us_basf = "https://ror.org/002yzpx87"

    def handler(request: httpx.Request) -> httpx.Response:
        if "affiliation" in request.url.params:
            return httpx.Response(200, json={"items": [
                {"chosen": True, "score": 1.0,
                 "organization": _org(us_basf, "BASF", "US")},
            ]})
        return httpx.Response(200, json={"items": [_org(us_basf, "BASF", "US")]})

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(
            transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(tier1_ror.httpx, "AsyncClient", factory)

    res = await call_ror(
        "BASF SE", country_code="DE", country="Germany", city="LUDWIGSHAFEN",
    )
    assert res["matched"] is False
    assert res.get("ror_id") is None


def test_distinctive_token_guard_still_caps_coastal_anser() -> None:
    """The non-acronym twin of the guard is unaffected."""
    anser = {
        "names": [
            {"value": "Analytical Services", "types": ["ror_display", "label"]},
            {"value": "ANSER", "types": ["acronym"]},
        ],
    }
    assert _score_org("Coastal Analytical Services", anser) < 0.8


def test_allcaps_query_still_capped_by_the_distinctive_token_guard() -> None:
    """Turning the acronym guard off on all-caps must not open the floodgates.

    'CARDINAL INSTRUMENTS' has no case contrast, so the identifier guard no
    longer fires — but 'cardinal' is still an uncovered distinctive token, so
    the match is capped just as a mixed-case query would be.
    """
    other = {
        "names": [{"value": "Horizon Instruments", "types": ["ror_display", "label"]}],
    }
    assert _score_org("CARDINAL INSTRUMENTS", other) < 0.8


# ───────────────────── Fla State Univ / Kent State ──────────────────────────


def test_fla_state_univ_still_not_matched_to_kent_state() -> None:
    kent = {"names": [{"value": "Kent State University", "types": ["ror_display", "label"]}]}
    assert _score_org("Florida State Univ", kent) < 0.8
    assert _score_org("FL STATE UNIV.", kent) < 0.8


@pytest.mark.asyncio
async def test_fla_state_univ_still_resolves_to_florida_state(offline_ror) -> None:
    res = await call_ror(
        "FL STATE UNIV.",
        country_code="US", country="US", city="TALLAHASSEE", state="FL",
    )
    assert res["matched"] is True
    assert res["ror_id"] == FSU
    assert res["ror_id"] != KENT_STATE


# ─────────────────── the initialism rescue is not collateral ────────────────


def test_initialism_extraction_is_not_gated_on_case_contrast() -> None:
    """`_extract_identifier_tokens` keeps its old behaviour.

    Only the *guard* wrapper is case-contrast gated. The initialism fallback
    can only raise a score, never cap one, so an all-caps "JAH VA HOSPITAL"
    must still be able to reach "James A. Haley Veterans' Hospital".
    """
    assert _extract_identifier_tokens("BRIGHAM & WOMENS HOSP") == {"hosp"}

    jah = [{
        "value": "James A. Haley Veterans' Hospital",
        "types": ["ror_display", "label"],
    }]
    assert _compute_name_score("JAH VA HOSPITAL", jah, set()) == 1.0


# ──────────────────────── the harness itself is honest ──────────────────────


def test_fixtures_are_present_and_offline() -> None:
    fixtures = load_fixtures()
    assert fixtures, "no recorded ROR fixtures — run scripts/ror_repro.py --record"
    assert all("params" in f and "response" in f for f in fixtures)


def test_diagnosis_agrees_with_the_production_scorer() -> None:
    """The repro script explains the real scorer, and cannot drift from it.

    Every candidate in every recorded fixture is diagnosed and cross-checked
    against `_compute_name_score`; a divergence means the harness is lying.
    """
    checked = 0
    for fx in load_fixtures():
        for item in fx["response"].get("items", []):
            org = item.get("organization", item)
            for query in ("BRIGHAM & WOMENS HOSP", "Brigham & Womens Hosp",
                          "HFT Stuttgart", "Florida STATE University"):
                d = diagnose_score(query, org.get("names") or [], {"boston"})
                assert d["agrees"], (
                    f"diagnosis {d['score']} != production {d['actual_score']} "
                    f"for {query!r} vs {org.get('id')}"
                )
                checked += 1
    assert checked > 100, f"only {checked} candidate diagnoses checked"
