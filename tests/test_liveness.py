"""The liveness lane.

One rule, tested from every angle the lane offers: **"which entity is this?"
and "does that entity still exist?" are independent questions, and the second
must not be gated on the first having failed.**

That was the defect. Supersession detection lived inside the Wikidata
crosswalk, which runs only when ROR *and* GLEIF have both missed — so the
pipeline asked whether an organisation was gone exclusively of records it could
not identify, while an acquisition is a property of entities that identify
*easily*. `TestCelgene` is the case that exposed it, wired end to end.

The second rule is that the lane is a **pure addition**: it can raise a flag
and do nothing else. No name, no identifier, no domain, no status — so a lane
that is switched off, that raises, or that runs against a frozen cache costs a
flag and never a value (`TestTheLaneOnlyEverAddsAFlag`).

The third is that which registry states count as death was **measured, not read
off the spec** (`TestWhatCountsAsDeath`). Every exclusion in there is load-
bearing: LAPSED is 35% of GLEIF, and ROR's `withdrawn` is deduplication.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.flags import ENTITY_SUPERSEDED, compute_flags
from enrichment.liveness import (
    SOURCE_GLEIF,
    SOURCE_REDIRECT,
    SOURCE_ROR,
    LivenessFinding,
    gleif_verdict,
    probe_ror_status,
    redirect_verdict,
    render_detail,
    ror_verdict,
)
from enrichment.tier1_lei import _record_fields


def _ror_org(qid: str, name: str, status: str) -> dict:
    return {
        "id": f"https://ror.org/{qid}",
        "status": status,
        "names": [{"value": name, "types": ["ror_display"]}],
    }


class TestWhatCountsAsDeath:
    """The measured exclusions. Each one has a population behind it."""

    def test_gleif_inactive_entity_is_death(self):
        finding = gleif_verdict("INACTIVE", "ISSUED")
        assert finding is not None and finding.source == SOURCE_GLEIF
        assert "INACTIVE" in finding.detail

    def test_gleif_retired_registration_is_death(self):
        finding = gleif_verdict("ACTIVE", "RETIRED")
        assert finding is not None and "RETIRED" in finding.detail

    def test_lapsed_is_not_death(self):
        """35% of every LEI on file is LAPSED — 1,193,113 of 3,412,502.

        It is the single most tempting false signal in the lane, because
        Celgene *is* LAPSED and an acquirer abandoning a subsidiary's renewal
        really is a pattern. It is still not evidence: `ACTIVE + LAPSED` covers
        1,193,112 of those records, so raising on it would flag a third of
        every record that resolves to an LEI.
        """
        assert gleif_verdict("ACTIVE", "LAPSED") is None

    def test_annulled_is_not_death(self):
        """ANNULLED retracts a RECORD, not an organisation — issued in error."""
        assert gleif_verdict("ACTIVE", "ANNULLED") is None

    def test_a_live_entity_is_not_flagged(self):
        assert gleif_verdict("ACTIVE", "ISSUED") is None
        assert gleif_verdict(None, None) is None

    def test_status_is_read_case_and_space_insensitively(self):
        assert gleif_verdict(" inactive ", None) is not None

    def test_ror_inactive_is_death(self):
        org = _ror_org("0527yg379", "Celgene (United States)", "inactive")
        finding = ror_verdict(org, score=1.0, threshold=0.8)
        assert finding is not None and finding.source == SOURCE_ROR
        assert "Celgene" in finding.detail

    def test_ror_withdrawn_is_not_death(self):
        """ROR's deduplication state, not a corporate event.

        Withdrawn records carry `successor` relationships whose labels are
        character-identical to their own name, because they are merged
        duplicates. Reporting them as "no longer exists" would describe
        registry housekeeping as an acquisition.
        """
        org = _ror_org("03k1he386", "The Journal of Student Science", "withdrawn")
        assert ror_verdict(org, score=1.0, threshold=0.8) is None

    def test_ror_active_is_not_death(self):
        org = _ror_org("00gtmwv55", "Bristol-Myers Squibb", "active")
        assert ror_verdict(org, score=1.0, threshold=0.8) is None

    def test_a_below_threshold_name_match_is_not_judged_at_all(self):
        """A name that did not identify the dead organisation cannot report it."""
        org = _ror_org("0527yg379", "Celgene (United States)", "inactive")
        assert ror_verdict(org, score=0.4, threshold=0.8) is None


class TestTheRedirectReading:
    """A cross-domain redirect has two readings and the lane must pick one."""

    THRESHOLD = 60.0

    @pytest.mark.parametrize(
        "name, start, final",
        [
            ("Celgene Corp", "celgene.com", "https://www.bms.com/"),
            ("Mellanox Technologies", "mellanox.com", "https://www.nvidia.com/"),
            ("Horizon Therapeutics", "horizontherapeutics.com", "https://www.amgen.com/"),
        ],
    )
    def test_a_landing_domain_naming_someone_else_is_flagged(self, name, start, final):
        finding = redirect_verdict(name, start, final, threshold=self.THRESHOLD)
        assert finding is not None and finding.source == SOURCE_REDIRECT
        assert start in finding.detail

    @pytest.mark.parametrize(
        "name, start, final",
        [
            # An organisation that MOVED. ROR's stale dur.ac.uk → live
            # durham.ac.uk is the case `_resolve_probe_base` was written for,
            # and reporting a university that renamed its domain as dissolved
            # is precisely the false positive this check must not make.
            ("Durham University", "dur.ac.uk", "https://durham.ac.uk/"),
            # A subsidiary the acquirer kept as a brand.
            ("Alexion Pharmaceuticals", "alexionpharma.com", "https://alexion.com/"),
        ],
    )
    def test_a_move_is_not_a_supersession(self, name, start, final):
        assert redirect_verdict(name, start, final, threshold=self.THRESHOLD) is None

    def test_no_redirect_is_not_evidence_of_anything(self):
        """monsanto.com still serves Monsanto years after Bayer.

        Absence of a redirect must never be read as absence of an acquisition:
        the check has low recall by construction and cannot support a
        completeness claim.
        """
        assert redirect_verdict(
            "Monsanto", "monsanto.com", "https://monsanto.com/",
            threshold=self.THRESHOLD,
        ) is None

    def test_scheme_and_www_do_not_make_a_redirect(self):
        assert redirect_verdict(
            "Sigma-Aldrich", "sigmaaldrich.com", "https://www.sigmaaldrich.com/x?q=1",
            threshold=self.THRESHOLD,
        ) is None

    def test_a_missing_side_is_silent(self):
        assert redirect_verdict("Celgene", None, "https://bms.com", threshold=60) is None
        assert redirect_verdict("Celgene", "celgene.com", None, threshold=60) is None


class TestTheReasonClause:
    """What the reviewer is handed."""

    def test_every_source_is_rendered_not_just_the_first(self):
        detail = render_detail([
            LivenessFinding(SOURCE_REDIRECT, "celgene.com redirects to bms.com"),
            LivenessFinding(SOURCE_ROR, "ROR records 'Celgene' as inactive"),
        ])
        assert "ROR" in detail and "redirects" in detail

    def test_order_is_fixed_not_discovery_ordered(self):
        """Two findings must render identically whichever completed first."""
        a = LivenessFinding(SOURCE_ROR, "ror-said")
        b = LivenessFinding(SOURCE_REDIRECT, "redirect-said")
        assert render_detail([a, b]) == render_detail([b, a])
        assert render_detail([a, b]).startswith("ror-said")

    def test_duplicates_collapse(self):
        a = LivenessFinding(SOURCE_ROR, "same")
        assert render_detail([a, a]) == "same"

    def test_nothing_found_is_none(self):
        assert render_detail([]) is None

    def test_the_clause_reaches_the_flag(self):
        """The lane's whole output is an evidence key `compute_flags` reads."""
        result = {
            "name1_enriched": "Celgene Corp",
            "_ev_entity_superseded": "ROR records 'Celgene (United States)' as inactive",
        }
        compute_flags(result)
        assert ENTITY_SUPERSEDED in result["flag_codes"]
        assert "inactive" in result["flag_reason"]


class TestGleifCarriesItsOwnStatus:
    def test_both_statuses_are_parsed_off_the_real_payload_shape(self):
        """Celgene's actual GLEIF record, and the disagreement that matters."""
        fields = _record_fields({
            "id": "4SIHMF0MOSTTL8CD0X64",
            "attributes": {
                "entity": {
                    "legalName": {"name": "CELGENE CORPORATION"},
                    "status": "ACTIVE",
                    "legalAddress": {"country": "US", "city": "Summit"},
                },
                "registration": {"status": "LAPSED"},
            },
        })
        assert fields["status"] == "ACTIVE"
        assert fields["registration_status"] == "LAPSED"

    def test_a_missing_registration_block_is_not_an_error(self):
        fields = _record_fields({
            "id": "X",
            "attributes": {"entity": {"legalName": {"name": "N"}, "status": "ACTIVE"}},
        })
        assert fields["registration_status"] is None


@pytest.mark.asyncio
class TestTheRorProbe:
    """ROR's search hides exactly what the probe is looking for."""

    async def test_the_best_match_decides_even_when_a_dead_one_is_present(
        self, monkeypatch,
    ):
        """A name matching a LIVE org better than a dead one has not
        identified the dead one. Flagging on any inactive hit at all would
        report every company whose name resembles some defunct organisation.
        """
        items = [
            _ror_org("dead00001", "Allievex Corporation", "inactive"),
            _ror_org("live00001", "Balchem Corporation", "active"),
        ]
        monkeypatch.setattr(
            "enrichment.liveness.cached_registry_get",
            _fake_registry({"items": items}),
        )
        finding, org, score = await probe_ror_status(
            "Balchem Corporation", country_code="US", threshold=0.8,
        )
        assert finding is None
        assert org["id"].endswith("live00001")

    async def test_an_inactive_winner_is_flagged(self, monkeypatch):
        items = [
            _ror_org("0527yg379", "Celgene (United States)", "inactive"),
            _ror_org("live00001", "Balchem Corporation", "active"),
        ]
        monkeypatch.setattr(
            "enrichment.liveness.cached_registry_get",
            _fake_registry({"items": items}),
        )
        finding, org, _score = await probe_ror_status(
            "Celgene Corporation", country_code="US", threshold=0.8,
        )
        assert finding is not None and finding.source == SOURCE_ROR
        assert org["id"].endswith("0527yg379")

    async def test_the_probe_asks_for_the_records_search_hides(self, monkeypatch):
        """`all_status=` is the entire reason this lane can see Celgene.

        Without it `?query=Celgene` returns zero rows and the organisation
        that holds the answer is invisible to every ROR path the pipeline has.
        """
        seen: dict = {}

        async def _capture(registry, url, params, fetch):
            seen.update(params or {})
            return {"items": []}

        monkeypatch.setattr("enrichment.liveness.cached_registry_get", _capture)
        await probe_ror_status("Celgene Corp", country_code="US", threshold=0.8)
        assert "all_status" in seen
        assert "US" in seen.get("filter", "")

    async def test_a_tie_breaks_on_the_ror_id_not_arrival_order(self, monkeypatch):
        """Same reasoning as `tier1_lei` Fix C(1): ROR promises no order, and
        two candidates on one score must not swap between runs."""
        pair = [
            _ror_org("bbbbbbbbb", "Acme Corporation", "active"),
            _ror_org("aaaaaaaaa", "Acme Corporation", "active"),
        ]
        winners = []
        for items in (pair, list(reversed(pair))):
            monkeypatch.setattr(
                "enrichment.liveness.cached_registry_get",
                _fake_registry({"items": items}),
            )
            _f, org, _s = await probe_ror_status(
                "Acme Corporation", country_code="US", threshold=0.8,
            )
            winners.append(org["id"])
        assert winners[0] == winners[1] == "https://ror.org/aaaaaaaaa"

    async def test_the_lane_never_raises(self, monkeypatch):
        async def _boom(registry, url, params, fetch):
            raise RuntimeError("ROR is down")

        monkeypatch.setattr("enrichment.liveness.cached_registry_get", _boom)
        finding, org, score = await probe_ror_status(
            "Celgene Corp", country_code="US", threshold=0.8,
        )
        assert (finding, org, score) == (None, None, 0.0)

    async def test_an_empty_name_costs_no_call(self, monkeypatch):
        async def _never(registry, url, params, fetch):
            raise AssertionError("probe called for an empty name")

        monkeypatch.setattr("enrichment.liveness.cached_registry_get", _never)
        assert await probe_ror_status("  ", threshold=0.8) == (None, None, 0.0)


def _fake_registry(body: dict):
    async def _get(registry, url, params, fetch):
        return body
    return _get


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

import copy  # noqa: E402

from api.models import EnrichmentOptions, EnrichmentRecord  # noqa: E402
from config import Settings  # noqa: E402
from enrichment.orchestrator import Orchestrator  # noqa: E402
from tests.mocks.lei_mock import MockLEIClient  # noqa: E402
from tests.mocks.openai_mock import MockOpenAIClient  # noqa: E402
from tests.mocks.ror_mock import MockRORClient  # noqa: E402
from tests.mocks.serp_mock import MockSearchClient  # noqa: E402

_VOLATILE = {"processing_time_ms", "fetched_at"}


def _orch(**over) -> Orchestrator:
    settings = Settings()
    # No fixture store: these tests state their own answers.
    object.__setattr__(settings, "page_fixture_dir", "")
    object.__setattr__(settings, "wikidata_fixture_dir", "")
    object.__setattr__(settings, "wikidata_enabled", False)
    clients = {
        "ror": MockRORClient(settings), "lei": MockLEIClient(settings),
        "search": MockSearchClient(), "llm": MockOpenAIClient(),
    }
    orch = Orchestrator(settings, mock_clients=clients)
    for key, value in over.items():
        object.__setattr__(orch._settings, key, value)
    return orch


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _dump(response):
    return [_scrub(r.model_dump(by_alias=True)) for r in response.results]


_BATCH = [
    EnrichmentRecord(
        record_id="R1", name1="Celgene Corp", country="US",
        city="Summit", state="NJ",
    ),
    EnrichmentRecord(record_id="R2", name1="MIT", country="US"),
]


def _no_ror_probe(*_a, **_kw):
    async def _go(*a, **kw):
        return None, None, 0.0
    return _go


@pytest.mark.asyncio
class TestCelgene:
    """The record that exposed the defect, wired end to end.

    Bristol-Myers Squibb absorbed Celgene Corporation in 2019. Before this
    lane the record left the pipeline enriched, high-confidence and unflagged,
    and every stage was behaving correctly when it did: GLEIF resolves the
    name to `4SIHMF0MOSTTL8CD0X64` at a perfect 100.0, and the registry hit is
    precisely what suppresses the Wikidata crosswalk that held the only
    supersession check. Nothing asked whether the entity was still alive.
    """

    async def test_ror_alone_flags_it(self, monkeypatch):
        monkeypatch.setattr(
            "enrichment.orchestrator.liveness_probe_ror_status",
            _fake_probe(LivenessFinding(
                SOURCE_ROR, "ROR records 'Celgene (United States)' as inactive",
            )),
        )
        orch = _orch(liveness_redirect_check_enabled=False)
        out = await orch.enrich_batch(
            [copy.deepcopy(_BATCH[0])], EnrichmentOptions(max_concurrency=1),
        )
        record = out.results[0]
        assert ENTITY_SUPERSEDED in record.flag_codes
        assert "inactive" in record.flag_reason

    async def test_the_lane_reports_but_does_not_rewrite(self, monkeypatch):
        """Which entity the record should point at after an acquisition is a
        business decision that depends on contracts this service cannot see.
        The flag hands the reviewer what was found and stops."""
        monkeypatch.setattr(
            "enrichment.orchestrator.liveness_probe_ror_status",
            _fake_probe(LivenessFinding(SOURCE_ROR, "ROR records it as inactive")),
        )
        orch = _orch(liveness_redirect_check_enabled=False)
        out = await orch.enrich_batch(
            [copy.deepcopy(_BATCH[0])], EnrichmentOptions(max_concurrency=1),
        )
        record = out.results[0]
        assert "bristol" not in (record.name1_enriched or "").lower()
        assert "bms" not in (record.domain or "").lower()

    async def test_two_sources_are_both_reported(self, monkeypatch):
        """A ROR status and a redirect are independent statements, and the
        reviewer should not have to re-run the pipeline to see the second."""
        result = {
            "name1_enriched": "Celgene Corp",
            "_ev_entity_superseded": (
                "ROR records 'Celgene (United States)' as inactive; "
                "celgene.com redirects to bms.com, which names a different "
                "organisation"
            ),
        }
        compute_flags(result)
        assert ENTITY_SUPERSEDED in result["flag_codes"]
        assert "ROR" in result["flag_reason"] and "bms.com" in result["flag_reason"]


@pytest.mark.asyncio
class TestTheLaneOnlyEverAddsAFlag:
    """The acceptance criterion for "pure addition", stated as tests."""

    async def test_disabled_is_byte_identical_to_the_lane_not_existing(
        self, monkeypatch,
    ):
        """Baseline: the entry point excised, which is as close as a test can
        get to the pre-lane build. The comparison run has the lane wired in,
        switched off at config, and given a probe that would flag every record
        in the batch — so anything leaking past the flag would show up."""
        monkeypatch.setattr(
            "enrichment.orchestrator.liveness_probe_ror_status",
            _fake_probe(LivenessFinding(SOURCE_ROR, "everything is dead")),
        )
        options = EnrichmentOptions(max_concurrency=1)

        without = _orch()

        async def _absent(*a, **kw):
            return None

        without._check_liveness = _absent
        baseline = await without.enrich_batch(copy.deepcopy(_BATCH), options)

        off = _orch(liveness_enabled=False)
        disabled = await off.enrich_batch(copy.deepcopy(_BATCH), options)

        assert _dump(disabled) == _dump(baseline)

    async def test_a_flagging_run_differs_from_the_baseline_in_the_flag_alone(
        self, monkeypatch,
    ):
        """The other half of the claim: the lane is inert everywhere EXCEPT
        `flag_codes` / `flag_reason`. A pure-addition test that only proved
        "off == absent" would pass for a lane that was silently broken."""
        monkeypatch.setattr(
            "enrichment.orchestrator.liveness_probe_ror_status",
            _fake_probe(LivenessFinding(SOURCE_ROR, "ROR records it as inactive")),
        )
        options = EnrichmentOptions(max_concurrency=1)

        off = _orch(liveness_enabled=False)
        baseline = _dump(await off.enrich_batch(copy.deepcopy(_BATCH), options))

        on = _orch(liveness_redirect_check_enabled=False)
        flagged = _dump(await on.enrich_batch(copy.deepcopy(_BATCH), options))

        differing = {
            key
            for base, live in zip(baseline, flagged)
            for key in base
            if base[key] != live[key]
        }
        # The serialised (aliased) names. All four are downstream of raising a
        # flag — the codes, the rendered reason, the review boolean and the
        # field scope — and nothing else in the record may move.
        assert differing <= {
            "Flag Codes", "Flag Reason", "Flag for Review", "Flagged Fields",
        }, differing
        assert any(ENTITY_SUPERSEDED in (r["Flag Codes"] or "") for r in flagged)

    async def test_a_probe_that_raises_costs_a_flag_and_nothing_else(
        self, monkeypatch,
    ):
        async def _boom(*a, **kw):
            raise RuntimeError("ROR is down")

        monkeypatch.setattr(
            "enrichment.orchestrator.liveness_probe_ror_status", _boom,
        )
        options = EnrichmentOptions(max_concurrency=1)
        off = _orch(liveness_enabled=False)
        baseline = _dump(await off.enrich_batch(copy.deepcopy(_BATCH), options))

        # `_enrich_single` catches everything and turns it into an error
        # record, so a lane that let this escape would cost the record its
        # whole enrichment to save a flag. The batch must complete, and come
        # out identical to the lane having been switched off.
        on = _orch(liveness_redirect_check_enabled=False)
        flagged = _dump(await on.enrich_batch(copy.deepcopy(_BATCH), options))
        assert flagged == baseline

    async def test_the_probe_is_asked_once_per_distinct_name_in_a_batch(
        self, monkeypatch,
    ):
        calls: list[str] = []

        async def _count(name, **kw):
            calls.append(name)
            return None, None, 0.0

        monkeypatch.setattr(
            "enrichment.orchestrator.liveness_probe_ror_status", _count,
        )
        batch = [
            EnrichmentRecord(record_id="A", name1="Celgene Corp", country="US"),
            EnrichmentRecord(record_id="B", name1="Celgene Corp", country="US"),
        ]
        orch = _orch(liveness_redirect_check_enabled=False)
        await orch.enrich_batch(batch, EnrichmentOptions(max_concurrency=1))
        assert len(calls) == 1, calls


def _fake_probe(finding):
    async def _go(name, **kw):
        return finding, None, 1.0
    return _go
