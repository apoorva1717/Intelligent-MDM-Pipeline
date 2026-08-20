"""The distinctive-token guard counts 4-letter tokens, not just 5+.

Customer 40000015 ("Acme Biotech", Tampa FL) came back from the pipeline as
**"AUM BioTech"** — a real but unrelated company in Philadelphia PA — with
`ror.org/0106fnq84` and `aumbiotech.com` attached and no review flag.

The cause was a length floor, not a scoring bug. Step 4 of
`_compute_name_score` capped a fuzzy hit at 0.7 whenever a *distinctive*
query token had no counterpart in the candidate, but "distinctive" required
5+ characters. In "Acme Biotech" the only 5+ token is `biotech`, which the
candidate shares, so `q_distinctive` held nothing uncovered, no cap fired,
and the raw `token_sort_ratio` of **0.8696** cleared the 0.8 threshold.
`acme` — the token that says *which* company — never entered the comparison.
The exemption covered every org whose distinguishing word is four letters:
Acme, Duke, Yale, Mayo, Ohio, Iowa.

Nothing downstream could catch it. A verified Tier 1 match writes the
registry's name unconditionally (`_write_registry_name`), and a
registry-supplied domain bypasses the ownership guard — which had been tuned
to 82 specifically so that "Acme Biotech" vs `aumbiotech.com` (81.8) is
rejected on the *web* path. Tier 1 handed it the same domain through the
front door.

`_DISTINCTIVE_TOKEN_MIN_LEN = 4` closes it, matching the `>= 4` the
token-subset rule and `_extract_location_tokens` already use.

The end-to-end test replays the ROR responses recorded in
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
    _COMMON_DOMAIN_WORDS,
    _CONNECTOR_WORDS,
    _DISTINCTIVE_TOKEN_MIN_LEN,
    _compute_name_score,
    call_ror,
    clear_ror_cache,
)
from utils.text_utils import expand_abbreviations  # noqa: E402

from ror_repro import fixture_handler  # noqa: E402

AUM = "https://ror.org/0106fnq84"

# ROR's entry for the wrong company, as `_extract_org_fields` sees it.
AUM_NAMES = [{"value": "AUM BioTech", "types": ["ror_display", "label"]}]
TAMPA = {"tampa"}


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


# ────────────────────────────── the scorer ──────────────────────────────────


def test_four_letter_distinctive_token_caps_the_score() -> None:
    """The regression itself: "Acme Biotech" must not match "AUM BioTech"."""
    caps: set[str] = set()
    score = _compute_name_score("Acme Biotech", AUM_NAMES, TAMPA, caps)
    assert score == pytest.approx(0.7), (
        f"'acme' is uncovered by 'aum biotech'; expected the 0.7 cap, got {score}"
    )
    assert caps == {"distinctive_token"}


def test_the_raw_fuzzy_ratio_would_have_passed() -> None:
    """Without the cap the pair clears the threshold — so the guard, and only
    the guard, is what rejects it. Guards the fix against being 'fixed' by
    nudging `ror_confidence_threshold` instead."""
    from rapidfuzz import fuzz

    raw = fuzz.token_sort_ratio("acme biotech", "aum biotech") / 100.0
    assert raw == pytest.approx(0.8696, abs=1e-4)
    assert raw > 0.8


def test_the_floor_is_four() -> None:
    assert _DISTINCTIVE_TOKEN_MIN_LEN == 4


@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        # Every one of these shares a generic word and differs only in the
        # four-letter token that names the organisation.
        ("Acme Biotech", "AUM BioTech"),
        ("Duke University", "Drew University"),
        ("Yale Research Institute", "Kale Research Institute"),
        ("Mayo Health System", "Mays Health System"),
    ],
)
def test_four_letter_swaps_are_all_rejected(query: str, candidate: str) -> None:
    names = [{"value": candidate, "types": ["ror_display", "label"]}]
    assert _compute_name_score(query, names, set()) <= 0.7


@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        # A four-letter token that IS covered must still match — the guard
        # rejects substitution, not abbreviation. "Duke Univ" is scored by the
        # caller in its expanded form too (`_score_org` runs on both the raw
        # and the abbreviation-expanded query and takes the max), which is why
        # the abbreviation is safe here.
        ("Duke Univ", "Duke University"),
        ("Yale School of Medicine", "Yale School of Medicine"),
        ("Ohio State Univ", "Ohio State University"),
        ("Mayo Clinic", "Mayo Clinic"),
    ],
)
def test_covered_four_letter_tokens_still_match(query: str, candidate: str) -> None:
    names = [{"value": candidate, "types": ["ror_display", "label"]}]
    caps: set[str] = set()
    score = max(
        _compute_name_score(query, names, set(), caps),
        _compute_name_score(expand_abbreviations(query) or query, names, set(), caps),
    )
    assert score >= 0.8
    assert "distinctive_token" not in caps


def test_generic_four_letter_tokens_are_not_distinctive() -> None:
    """Dropping the floor to 4 pulls legal forms and non-prefix abbreviations
    into scope; they are excluded by name so they cannot cap a true match."""
    for word in ("gmbh", "kgaa", "sarl", "labs", "intl"):
        assert word in _COMMON_DOMAIN_WORDS

    # "labs" ↛ "laboratories" is not a prefix, so without the exclusion this
    # pair would cap at 0.7.
    names = [{"value": "Acme Laboratories", "types": ["ror_display", "label"]}]
    caps: set[str] = set()
    _compute_name_score("Acme Labs", names, set(), caps)
    assert "distinctive_token" not in caps


def test_prefix_abbreviations_need_no_exclusion() -> None:
    """`_fuzzy_token_covers` already bridges these, so they must NOT be listed
    as generic — "tech" in "Acme Tech" still identifies the org."""
    for word in ("univ", "inst", "hosp", "dept", "tech"):
        assert word not in _COMMON_DOMAIN_WORDS


# ─────────────────────────────── end to end ─────────────────────────────────


@pytest.mark.asyncio
async def test_acme_biotech_misses_ror_entirely(offline_ror) -> None:
    """Through `call_ror`, exactly as the orchestrator invokes it for
    Customer 40000015. Both strategies must come back empty — a miss here is
    the correct answer, and leaves Name 1 as supplied and flagged rather than
    silently replaced."""
    res = await call_ror(
        "Acme Biotech",
        country_code="US", country="US", city="TAMPA", state="FL",
    )
    assert res["matched"] is False
    assert res.get("ror_id") is None
    assert res["score"] <= 0.7

    # And the rejection is attributable: the reviewer can see which candidate
    # was refused and by which guard.
    rejections = res.get("guard_rejections") or []
    assert any(
        r.get("guard") == "distinctive_token" for r in rejections
    ), f"expected a distinctive_token rejection, got {rejections}"


@pytest.mark.asyncio
async def test_aum_biotech_is_never_the_answer(offline_ror) -> None:
    """The named defect, stated as its own assertion so a future relaxation of
    the guard fails on the symptom and not only on the mechanism."""
    res = await call_ror(
        "Acme Biotech",
        country_code="US", country="US", city="TAMPA", state="FL",
    )
    assert res.get("official_name") != "AUM BioTech"
    assert res.get("ror_id") != AUM
    assert res.get("domain") != "aumbiotech.com"


# ───────────── the regression the floor drop caused, and its fix ────────────

HFT = "https://ror.org/039gdg280"

# ROR's entry for row 9, umlaut and all.
HFT_NAMES = [
    {"value": "Hochschule für Technik Stuttgart", "types": ["ror_display", "label"]},
    {"value": "Stuttgart University of Applied Sciences", "types": ["alias"]},
]


def test_transliterated_connector_does_not_cap() -> None:
    """Dropping the floor to 4 put German "fuer" in scope and broke row 9.

    SAP stores names in ASCII, so the record arrives as "Hochschule fuer
    Technik Stuttgart" while ROR holds "Hochschule für Technik Stuttgart".
    The pair fuzzes to 0.95 and ROR's own affiliation scorer returns it at
    0.97, but `_fuzzy_token_covers` will not bridge "fuer" ↔ "für" — it
    requires both tokens at ≥4 characters and "für" is three. `fuer` is a
    connector, so `_CONNECTOR_WORDS` keeps it out of the distinctive set.
    """
    caps: set[str] = set()
    score = _compute_name_score(
        "Hochschule fuer Technik Stuttgart", HFT_NAMES, {"stuttgart"}, caps,
    )
    assert score >= 0.8, f"row 9 must still resolve to HFT Stuttgart, got {score}"
    assert caps == set()


def test_connectors_are_excluded_at_any_length() -> None:
    """A connector never says WHICH organisation, so length is irrelevant —
    this is a separate exclusion from `_COMMON_DOMAIN_WORDS`, which holds
    words that *are* about the org but describe its type."""
    for word in ("fuer", "für", "und", "della", "voor", "pour"):
        assert word in _CONNECTOR_WORDS


def test_umlaut_transliteration_is_mostly_not_covered() -> None:
    """Pins the current, PRE-EXISTING limit — not something this fix changed.

    `_fuzzy_token_covers` bridges a transliterated umlaut only when the token
    is long enough for the two spellings to fuzz above 85. That is the
    exception, not the rule: only "universitaet" ↔ "universität" (86.96)
    clears it. "koeln" ↔ "köln" (66.67), "muenchen" ↔ "münchen" (80.00) and
    "strasse" ↔ "straße" (76.92) do not, and all three are ≥5 characters —
    so they were already capping matches under the old floor, independently
    of `_DISTINCTIVE_TOKEN_MIN_LEN`. Folding ü→ue / ö→oe / ä→ae / ß→ss in
    `_normalise_for_tokens` would close the class; it is deliberately not
    done here, because no record in the demo batch exercises it and the
    change would move every German match at once.

    "fuer" ↔ "für" is the same class but three characters, below the
    `_fuzzy_token_covers` floor entirely — hence `_CONNECTOR_WORDS`.
    """
    from enrichment.tier1_ror import _fuzzy_token_covers

    assert _fuzzy_token_covers("universitaet", "universität")
    assert not _fuzzy_token_covers("koeln", "köln")
    assert not _fuzzy_token_covers("muenchen", "münchen")
    assert not _fuzzy_token_covers("strasse", "straße")
    assert not _fuzzy_token_covers("fuer", "für")


@pytest.mark.asyncio
async def test_row9_still_hits_ror(offline_ror) -> None:
    """End-to-end: the fix for 40000015 must not cost row 9 its match."""
    res = await call_ror(
        "Hochschule fuer Technik Stuttgart",
        country_code="DE", country="DE", city="STUTTGART", state=None,
    )
    assert res["matched"] is True
    assert res["ror_id"] == HFT
    # ROR's `official_name` for this org is its English form; the German
    # ror_display is what the *scorer* matched on. This is the exact value
    # the record carried before the fix.
    assert res["official_name"] == "Stuttgart Technical University of Applied Sciences"
