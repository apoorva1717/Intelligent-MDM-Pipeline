"""Fix 4: a verified registry match owns the output name.

Two defects, one rule.

*Defect A* — a record could hold ``ror.org/03zzw1w08`` and still display
"Mayo Clinic FLA". The identifier was attached, the match had passed ROR's
country and distinctive-token guards, and the name write was then suppressed
by a *second* gate (``canonical_preserves_identity``) that read the
abbreviation "FLA" as a distinctive token ROR had dropped. The Fix 2 retry
path had the same defect in a blunter form: it wrote ``ror_id`` and the domain
and never wrote the name at all.

*Defect B* — abbreviations survived into output names. ``expand_abbreviations``
only ever reached an output field via ``clean_passthrough_org_name`` (name1,
and only when ``source == "passthrough"``), so "Cardinal Research GRP" shipped
verbatim from every other path.

The rule these tests pin:

* a verified match writes the registry's official name, with no second
  threshold — direct match, child match, first pass or retry, ROR or GLEIF;
* a name that came from a registry is never abbreviation-expanded afterwards;
* every other output name field IS expanded, using the GLOBAL map only;
* the ROR-local ``_INSTITUTION_ACRONYMS`` / ``_US_STATE_ABBREVS`` maps stay
  ROR-local — they improve resolution and never reach an output name.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.orchestrator import (
    Orchestrator,
    _init_result,
    _write_registry_name,
    finalise,
)
from tests.conftest import seed
from enrichment.tier1_ror import (
    _INSTITUTION_ACRONYMS,
    _US_POSTAL_CODES,
    _US_STATE_ABBREVS,
    _expand_institution_acronyms,
    _expand_state_abbrevs,
)
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.page_mock import MockPageFetcher
from utils.text_utils import _ABBREV_MAP, expand_abbreviations


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _NoSearch:
    async def search(self, q, num_results=5):
        return []


class _EmptyLLM:
    async def extract_json(self, s, u, **k):
        return {}

    async def aclose(self):
        pass


class _StubROR:
    """ROR client returning one canned org for a named query.

    ``matches`` maps a lowercased query string to the org payload. Anything
    else is a miss, so a test states exactly which query resolves — that is
    what makes "resolved on the first attempt" distinguishable from "resolved
    on the Fix 2 retry".
    """

    def __init__(self, matches: dict[str, dict[str, Any]]) -> None:
        self.matches = matches
        self.queries: list[str] = []

    async def call(self, name, country_code=None, country=None,
                   city=None, state=None, **_ctx) -> dict[str, Any]:
        self.queries.append(name)
        hit = self.matches.get(name.strip().lower())
        if hit is None:
            return {"matched": False, "score": 0.0}
        return {"matched": True, "score": 1.0, **hit}


def _ror_org(**kw: Any) -> dict[str, Any]:
    org = {
        "ror_id": "https://ror.org/03zzw1w08",
        "official_name": "Mayo Clinic in Florida",
        "org_types": ["healthcare"],
        "is_research_institution": True,
        "domain": "mayoclinic.org",
        "website": "https://www.mayoclinic.org/patient-visitor-guide/florida",
        "children": [],
        "country": "United States",
    }
    org.update(kw)
    return org


def _orch(ror_client, lei_client=None):
    st = Settings()
    return Orchestrator(st, mock_clients={
        "ror": ror_client,
        "lei": lei_client if lei_client is not None else MockLEIClient(st),
        "search": _NoSearch(),
        "page_fetcher": MockPageFetcher(),
        "llm": _EmptyLLM(),
    })


async def _run(orch, **record_kw):
    rec = EnrichmentRecord(record_id="t", country="US", **record_kw)
    resp = await orch.enrich_batch([rec], EnrichmentOptions(max_concurrency=1))
    return resp.results[0]


# ---------------------------------------------------------------------------
# Defect A — the verified match must write the official name
# ---------------------------------------------------------------------------

class TestVerifiedMatchWritesOfficialName:
    @pytest.mark.asyncio
    async def test_row_27_mayo_clinic_fla(self):
        """The exact row-27 failure: ror.org/03zzw1w08 next to "Mayo Clinic
        FLA". "FLA" reads as a distinctive token that ROR's "Mayo Clinic in
        Florida" drops, which is what the removed gate keyed on."""
        ror = _StubROR({"mayo clinic fla": _ror_org()})
        r = await _run(_orch(ror), name1="Mayo Clinic FLA",
                       city="Jacksonville", state="Florida")
        assert r.ror_id == "https://ror.org/03zzw1w08"
        assert r.name1_enriched == "Mayo Clinic in Florida"

    def test_name1_changed_follows_the_existing_rule(self):
        """The write sets the flag through finalise's ordinary rule — True
        only when enriched is not None AND differs from the original."""
        for original, official, expected in [
            ("Mayo Clinic FLA", "Mayo Clinic in Florida", True),
            ("Mayo Clinic in Florida", "Mayo Clinic in Florida", False),
        ]:
            result = _init_result(EnrichmentRecord(
                record_id="t", name1=original, country="US",
            ))
            seed(result, name1_enriched=official)
            result["_registry_name_fields"] = {"name1"}
            out = finalise(result, time.monotonic())
            assert out["name1_changed"] is expected

    @pytest.mark.asyncio
    async def test_no_secondary_threshold_above_the_match_threshold(self):
        """A match that only just cleared the ROR threshold writes its name on
        exactly the same terms as a perfect one."""
        ror = _StubROR({"mayo clinic fla": _ror_org()})
        ror_low = _StubROR({"mayo clinic fla": _ror_org()})

        async def _barely(name, **kw):
            res = await _StubROR.call(ror_low, name, **kw)
            if res.get("matched"):
                res["score"] = 0.80
            return res

        ror_low.call = _barely  # type: ignore[method-assign]
        high = await _run(_orch(ror), name1="Mayo Clinic FLA")
        low = await _run(_orch(ror_low), name1="Mayo Clinic FLA")
        assert high.name1_enriched == low.name1_enriched == "Mayo Clinic in Florida"

    @pytest.mark.asyncio
    async def test_official_name_wins_over_a_fuller_input(self):
        """ROR's name for the matched entity ships even when the SAP input
        carried an extra parent qualifier. The match is verified; the registry
        is the authority on the entity's name."""
        ror = _StubROR({"usda agricultural research service": _ror_org(
            ror_id="https://ror.org/02d2m2044",
            official_name="Agricultural Research Service",
            domain="ars.usda.gov", website="https://www.ars.usda.gov",
        )})
        r = await _run(_orch(ror), name1="USDA Agricultural Research Service",
                       city="Beltsville", state="Maryland")
        assert r.ror_id == "https://ror.org/02d2m2044"
        assert r.name1_enriched == "Agricultural Research Service"

    @pytest.mark.asyncio
    async def test_ror_id_never_ships_beside_a_different_name(self):
        """The batch-wide invariant, over every shape that attaches a ror_id
        on the name1 path."""
        cases = [
            ("Mayo Clinic FLA", _ror_org()),
            ("FL State Univ", _ror_org(
                ror_id="https://ror.org/05g3dte14",
                official_name="Florida State University",
                domain="fsu.edu", website="https://www.fsu.edu")),
            ("GA Tech", _ror_org(
                ror_id="https://ror.org/01zkghx44",
                official_name="Georgia Institute of Technology",
                domain="gatech.edu", website="https://www.gatech.edu")),
            ("Universitat Stuttgart", _ror_org(
                ror_id="https://ror.org/04vnq7t77",
                official_name="University of Stuttgart",
                domain="uni-stuttgart.de", website="https://www.uni-stuttgart.de")),
        ]
        for name1, org in cases:
            r = await _run(_orch(_StubROR({name1.lower(): org})), name1=name1)
            assert r.ror_id == org["ror_id"], name1
            assert r.name1_enriched == org["official_name"], name1

    @pytest.mark.asyncio
    async def test_retry_path_writes_the_official_name(self):
        """Defect A's second form. ROR misses the SAP spelling, a later tier
        produces the corrected name, and the Fix 2 retry resolves it — the
        retry must attach the name as well as the identifier."""
        ror = _StubROR({"massachusetts institute of technology": _ror_org(
            ror_id="https://ror.org/042nb2s44",
            official_name="Massachusetts Institute of Technology",
            domain="mit.edu", website="https://web.mit.edu",
        )})
        result = _init_result(EnrichmentRecord(
            record_id="t", name1="MASSACHUSETTS INSITUTE OF TECHNOLOGY",
            country="US",
        ))
        # State the pipeline reaches after a ROR miss + LLM canonicalisation.
        result["_tier1_query_name"] = "MASSACHUSETTS INSITUTE OF TECHNOLOGY"
        result["_tier1_country_code"] = "US"
        seed(result, name1_enriched="Massachusetts Institute of Technology")
        orch = _orch(ror)
        await orch._retry_tier1_after_canonicalisation(
            EnrichmentRecord(record_id="t", country="US"), result,
        )
        assert result["ror_id"] == "https://ror.org/042nb2s44"
        assert result["name1_enriched"] == "Massachusetts Institute of Technology"
        assert "name1" in result["_registry_name_fields"]


class TestRegistryNamesOnEveryWritePath:
    """The rule is one rule: every path that takes a name from a registry
    marks it, so every such name is protected from later re-processing."""

    @pytest.mark.asyncio
    async def test_local_child_match_writes_rors_child_name(self):
        """A child name comes straight off the ROR record, so it is
        registry-owned like name1 — the abbreviation pass leaves its spelling
        alone. (UC 5's `canonicalise_unit_name` still rewrites a name2-4 that
        is a "<Unit> of X" construction, registry-sourced or not; that rule
        predates this fix and is unchanged. "Grp" is a granular unit, which UC
        5 leaves verbatim, so this probe isolates the expansion pass.)"""
        ror = _StubROR({"massachusetts institute of technology": _ror_org(
            ror_id="https://ror.org/042nb2s44",
            official_name="Massachusetts Institute of Technology",
            domain="mit.edu", website="https://web.mit.edu",
            children=[{"name": "Kavli Nanoscience Grp",
                       "id": "https://ror.org/fakekavli"}],
        )})
        r = await _run(
            _orch(ror), name1="Massachusetts Institute of Technology",
            name2="Kavli Nanoscience Grp", city="Cambridge",
        )
        assert r.name2_enriched == "Kavli Nanoscience Grp"

    def test_gleif_legal_name_is_registry_owned(self):
        """GLEIF's token_sort_ratio guard is the LEI counterpart of ROR's
        guards — a verified legal name is written and then left alone."""
        result = _init_result(EnrichmentRecord(record_id="t", country="US"))
        _write_registry_name(result, "name1", "Sterling Svcs Ltd", "GLEIF")
        assert result["name1_enriched"] == "Sterling Svcs Ltd"
        out = finalise(result, time.monotonic())
        assert out["name1_enriched"] == "Sterling Svcs Ltd"

    def test_a_blank_registry_name_writes_nothing(self):
        """A registry that returns no name must not blank the field, and must
        not claim ownership of it."""
        result = _init_result(EnrichmentRecord(record_id="t", country="US"))
        seed(result, name1_enriched="Cardinal Research GRP")
        _write_registry_name(result, "name1", "   ", "ROR")
        assert result["name1_enriched"] == "Cardinal Research GRP"
        assert not result.get("_registry_name_fields")
        out = finalise(result, time.monotonic())
        assert out["name1_enriched"] == "Cardinal Research Group"



# ---------------------------------------------------------------------------
# Defect B — abbreviations in output names
# ---------------------------------------------------------------------------

class TestOutputNameExpansion:
    @pytest.mark.parametrize("original,expected", [
        # Rows 40 and 46: organisations with no registry entry. No registry
        # will ever supply these names, so expansion is the only mechanism.
        ("Cardinal Research GRP", "Cardinal Research Group"),
        ("Coastal Analytical Svcs", "Coastal Analytical Services"),
    ])
    def test_unregistered_org_names_are_expanded(self, original, expected):
        result = _init_result(EnrichmentRecord(
            record_id="t", name1=original, country="US",
        ))
        seed(result, name1_enriched=original)
        out = finalise(result, time.monotonic())
        assert out["name1_enriched"] == expected

    @pytest.mark.parametrize("abbrev", ["Univ", "Dept", "Grp", "Svcs", "Inst"])
    def test_global_map_covers_the_required_abbreviations(self, abbrev):
        expanded = expand_abbreviations(f"Example {abbrev}")
        assert expanded != f"Example {abbrev}"
        assert abbrev not in expanded.split()

    @pytest.mark.parametrize("field", ["name1", "name2", "name3", "name4"])
    def test_expansion_covers_every_output_name_field(self, field):
        result = _init_result(EnrichmentRecord(record_id="t", country="US"))
        seed(result, **{f"{field}_enriched": "Coastal Analytical Svcs"})
        out = finalise(result, time.monotonic())
        # name2-4 are packed leftward at the end of finalise, so assert the
        # expanded value survives somewhere rather than pinning the slot.
        surviving = [out[f"name{i}_enriched"] for i in (1, 2, 3, 4)]
        assert "Coastal Analytical Services" in surviving
        assert "Coastal Analytical Svcs" not in surviving

    def test_a_registry_name_is_not_expanded(self):
        """A registry name is authoritative on its own spelling. Nothing in
        the global map may re-open it — not even a token that looks like an
        abbreviation."""
        result = _init_result(EnrichmentRecord(record_id="t", country="US"))
        seed(result, name1_enriched="Inst Pasteur")
        result["source"] = "ROR"
        result["_registry_name_fields"] = {"name1"}
        out = finalise(result, time.monotonic())
        assert out["name1_enriched"] == "Inst Pasteur"

    def test_the_same_value_IS_expanded_when_it_is_not_registry_sourced(self):
        """Control for the test above — the marker is what makes the
        difference, not the string."""
        result = _init_result(EnrichmentRecord(record_id="t", country="US"))
        seed(result, name1_enriched="Inst Pasteur")
        out = finalise(result, time.monotonic())
        assert out["name1_enriched"] == "Institute Pasteur"

    @pytest.mark.asyncio
    async def test_ror_name_survives_finalisation_unaltered(self):
        """End-to-end: the registry marker travels from the write to
        finalisation, so an official name containing an expandable token
        ships verbatim."""
        ror = _StubROR({"inst pasteur": _ror_org(
            ror_id="https://ror.org/0495fxg12",
            official_name="Inst Pasteur",
            domain="pasteur.fr", website="https://www.pasteur.fr",
        )})
        r = await _run(_orch(ror), name1="Inst Pasteur")
        assert r.name1_enriched == "Inst Pasteur"


# ---------------------------------------------------------------------------
# Separation of the two layers
# ---------------------------------------------------------------------------

class TestRORLocalMapsStayRORLocal:
    @pytest.mark.parametrize("key", sorted(_INSTITUTION_ACRONYMS))
    def test_institution_acronyms_never_expand_an_output_name(self, key):
        """The ROR-local EXPANSION must never appear in an output name. (The
        global map may still touch a token inside the key on its own terms —
        "Tech" → "Technology" is a global rule, not the acronym map.)"""
        probe = f"{key.upper()} Example"
        assert _INSTITUTION_ACRONYMS[key] not in (expand_abbreviations(probe) or "")

    @pytest.mark.parametrize("key", sorted(_US_STATE_ABBREVS))
    def test_state_abbrevs_never_expand_an_output_name(self, key):
        probe = f"{key.title()} State Univ"
        # Only the GLOBAL "Univ" rule may fire — never the state token.
        assert expand_abbreviations(probe) == f"{key.title()} State University"

    @pytest.mark.parametrize("key", sorted(_US_POSTAL_CODES))
    def test_postal_codes_never_expand_an_output_name(self, key):
        probe = f"{key.upper()} State Univ"
        assert expand_abbreviations(probe) == f"{key.upper()} State University"

    def test_ror_local_expansions_are_absent_from_the_global_map(self):
        """Structural: the two layers must not be merged by accident."""
        global_targets = set(_ABBREV_MAP.values())
        for local in (_INSTITUTION_ACRONYMS, _US_STATE_ABBREVS, _US_POSTAL_CODES):
            assert not (set(local.values()) & global_targets)

    @pytest.mark.asyncio
    async def test_hft_stuttgart_outputs_rors_name_not_the_local_expansion(self):
        """The acronym map got the record to resolve; it must not appear in
        the output. What ships is ROR's official name."""
        ror = _StubROR({"hft stuttgart": _ror_org(
            ror_id="https://ror.org/00hnhm792",
            official_name="Hochschule für Technik Stuttgart",
            domain="hft-stuttgart.de", website="https://www.hft-stuttgart.de",
            country="Germany",
        )})
        r = await _run(_orch(ror), name1="HFT Stuttgart", city="Stuttgart")
        assert r.name1_enriched == "Hochschule für Technik Stuttgart"


class TestBoundedTwoLetterPostalExpansion:
    @pytest.mark.parametrize("query,expected", [
        ("FL State Univ", "Florida State Univ"),
        ("FL State University", "Florida State University"),
        ("IN State Univ", "Indiana State Univ"),
        ("OR State Univ", "Oregon State Univ"),
        ("TX Tech", "Texas Tech"),
        ("VA Tech", "Virginia Tech"),
        ("NJ Institute of Technology", "New Jersey Institute of Technology"),
    ])
    def test_fires_inside_the_bounded_contexts(self, query, expected):
        assert _expand_state_abbrevs(query) == expected

    @pytest.mark.parametrize("query", [
        # The README collision the general exclusion exists for.
        "IN Laboratories",
        "OR Diagnostics",
        "DE Instruments",
        "ME Analytical Services",
        "CA Biosciences",
        "FL Holdings",
        # Bare code, no following context at all.
        "OR",
        # Right words, wrong order — the code must immediately precede them.
        "State Univ of IN",
    ])
    def test_does_not_fire_outside_them(self, query):
        assert _expand_state_abbrevs(query) == query

    @pytest.mark.parametrize("query", ["Hi Tech Solutions", "In Tech Partners"])
    def test_wordlike_codes_are_held_back_from_the_tech_contexts(self, query):
        """"HI"/"IN" are real postal codes AND ordinary words. "Hi Tech" is a
        company name, so the Tech contexts exclude them — while "Hi State
        University", which names nothing, stays permitted."""
        assert _expand_state_abbrevs(query) == query

    def test_ga_tech_is_left_to_the_acronym_map(self):
        """The bounded pattern would produce "Georgia Tech", which ROR
        resolves to Georgia Tech Foundation (ror.org/00adhzq59) — a different
        legal entity from the university (ror.org/01zkghx44). The phrase is
        owned by _INSTITUTION_ACRONYMS, which expands it to the exact official
        name on the affiliation retry."""
        assert _expand_state_abbrevs("GA Tech") == "GA Tech"
        assert _expand_institution_acronyms("GA Tech") == (
            "Georgia Institute of Technology"
        )

    def test_fla_resolves_row_27s_query(self):
        """"FLA" is already a key of the three-to-five letter map, so row 27's
        ROR query carried the distinctive geographic token all along — the
        failure was on the write, not the lookup."""
        assert _expand_state_abbrevs("Mayo Clinic FLA") == "Mayo Clinic Florida"
