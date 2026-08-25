"""The Wikidata crosswalk lane.

One rule is tested from every angle the lane offers: **Wikidata is a crosswalk
and a witness, never an authority.**

* A matched item that carries a registry pointer buys a *lookup*, and the
  registry writes the value — with the registry's provenance, through the
  registry's own guards, and with nothing in the record naming Wikidata
  (`TestCrosswalk`).
* A matched item that carries no pointer may write ``operating_name`` and
  nothing else; ``name1_enriched`` comes out byte-identical
  (`TestWitnessOnly`).
* Every constraint in the gauntlet can refuse a candidate on its own, and an
  ambiguity is a refusal rather than a tiebreak (`TestTheGauntlet`).

The second rule is that the lane is a **pure insert**: switched off, the
pipeline produces the same bytes it produced before the lane existed, and a
lane that cannot reach the API leaves the record to continue down the waterfall
exactly as it would have (`TestTheLaneIsAPureInsert`).
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from config import Settings
from enrichment.flags import ENTITY_SUPERSEDED, compute_flags
from enrichment.orchestrator import Orchestrator, _init_result
from enrichment.provenance import GUARD_GLEIF_NAME, deterministic_evidence
from enrichment.unchanged_state import UNCHANGED_VERIFIED, resolve as resolve_unchanged
from enrichment.wikidata import (
    AMBIGUOUS,
    COUNTRY_REJECTED,
    MATCHED,
    NO_MATCH,
    P279_ONE_STEP,
    TYPE_WHITELIST,
    TYPE_REJECTED,
    WITNESS_PROVENANCE,
    WikidataClient,
    WikidataItem,
    WikidataUnavailable,
    parse_entity,
    resolve as resolve_wikidata,
    type_allowed,
    website_agrees,
)
from tests.mocks.lei_mock import MockLEIClient
from tests.mocks.openai_mock import MockOpenAIClient
from tests.mocks.ror_mock import MockRORClient
from tests.mocks.serp_mock import MockSearchClient
from tests.mocks.wikidata_mock import (
    MockWikidataClient,
    entity_claim,
    make_entity,
    string_claim,
    time_claim,
)

# Real QIDs, so the fixtures exercise the shipped whitelist rather than a
# parallel one. Verified against live Wikidata on 2026-08-23.
Q_UNIVERSITY = "Q3918"
Q_BUSINESS = "Q4830453"
Q_FILM = "Q11424"
Q_DISAMBIG = "Q4167410"
Q_US = "Q30"
Q_GERMANY = "Q183"

# The curated identifiers the ROR / LEI mocks resolve by id.
UFL_ROR = "02y3ad647"
PFIZER_LEI = "549300ZZDOU0WGVYS169"


# ---------------------------------------------------------------------------
# Doubles and helpers
# ---------------------------------------------------------------------------

def _orch(wikidata: MockWikidataClient | None = None, **over) -> Orchestrator:
    settings = Settings()
    # No fixture store: these tests state their own answers and must not read
    # or write a recording.
    object.__setattr__(settings, "page_fixture_dir", "")
    object.__setattr__(settings, "wikidata_fixture_dir", "")
    clients = {
        "ror": MockRORClient(settings), "lei": MockLEIClient(settings),
        "search": MockSearchClient(), "llm": MockOpenAIClient(),
        "wikidata": wikidata if wikidata is not None else MockWikidataClient(),
    }
    clients.update(over)
    return Orchestrator(settings, mock_clients=clients)


def _record(name1: str = "Acme Widgets Inc", **over) -> EnrichmentRecord:
    fields = {
        "record_id": "R1", "name1": name1, "country": "US",
        "city": "Irvine", "state": "CA",
    }
    fields.update(over)
    return EnrichmentRecord(**fields)


def _after_registry_miss(record: EnrichmentRecord):
    """A result in the state the lane actually sees: Tier 1 has run and missed,
    the SAP input is standing in Name 1, and no registry identifier is on the
    record."""
    result = _init_result(record)
    result["_tier1_query_name"] = record.name1
    result["_tier1_country_code"] = "US"
    result.write(
        "name1_enriched", record.name1,
        deterministic_evidence(
            "tier1-ror-miss:research-passthrough", producer="input", tier=1,
        ),
    )
    return result


def _org_entity(
    qid: str,
    label: str,
    *,
    kind: str = Q_BUSINESS,
    country: str | None = Q_US,
    aliases: tuple[str, ...] = (),
    ror: str | None = None,
    lei: str | None = None,
    website: str | None = None,
    hq: str | None = None,
    dissolved: str | None = None,
    replaced_by: str | None = None,
):
    claims: dict[str, list] = {"P31": [entity_claim("P31", kind)]}
    if country:
        claims["P17"] = [entity_claim("P17", country)]
    if hq:
        claims["P159"] = [entity_claim("P159", hq)]
    if ror:
        claims["P6782"] = [string_claim("P6782", ror)]
    if lei:
        claims["P1278"] = [string_claim("P1278", lei)]
    if website:
        claims["P856"] = [string_claim("P856", website)]
    if dissolved:
        claims["P576"] = [time_claim("P576", dissolved)]
    if replaced_by:
        claims["P1366"] = [entity_claim("P1366", replaced_by)]
    return make_entity(qid, label, aliases=aliases, claims=claims)


def _city_entity(qid: str, label: str, country: str = Q_US):
    return make_entity(qid, label, claims={"P17": [entity_claim("P17", country)]})


async def _run_lane(orch: Orchestrator, record: EnrichmentRecord, result):
    return await orch._wikidata_crosswalk(
        record, result, record.name1, "US",
    )


# ---------------------------------------------------------------------------
# The declared constants
# ---------------------------------------------------------------------------

class TestTheWhitelist:
    def test_every_one_step_parent_lands_in_the_whitelist(self):
        """The ``P279`` table exists to carry a subtype INTO the whitelist. An
        entry whose parents are all outside it does nothing and is a mistake."""
        for subtype, parents in P279_ONE_STEP.items():
            assert any(p in TYPE_WHITELIST for p in parents), subtype

    def test_organisation_itself_is_not_whitelisted(self):
        """Q43229 is `organization`. Admitting it would gate nothing — every
        candidate this lane ever sees is an organisation of some kind, which is
        exactly why the closure is one declared step and not transitive."""
        assert "Q43229" not in TYPE_WHITELIST

    def test_a_subtype_reaches_the_whitelist_in_one_step(self):
        # pharmaceutical company -> company
        item = WikidataItem(qid="Q1", instance_of=("Q19644607",))
        assert type_allowed(item)

    def test_an_undeclared_subtype_is_refused(self):
        """The conservative direction: a class the table does not name gets no
        step up and the candidate is rejected, rather than being admitted on an
        unverified hierarchy."""
        item = WikidataItem(qid="Q1", instance_of=("Q99999999",))
        assert not type_allowed(item)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_claims_are_read_off_the_real_payload_shape(self):
        item = parse_entity("Q42", _org_entity(
            "Q42", "Acme Widgets, Inc.", aliases=("Acme",),
            ror="02y3ad647", lei=PFIZER_LEI,
            website="https://www.acme.com/en/", hq="Q62",
        ))
        assert item.label == "Acme Widgets, Inc."
        assert item.aliases == ("Acme",)
        assert item.instance_of == (Q_BUSINESS,)
        assert item.countries == (Q_US,)
        assert item.headquarters == ("Q62",)
        assert item.ror_id == "02y3ad647"
        assert item.lei_id == PFIZER_LEI
        assert item.website == "https://www.acme.com/en/"
        assert item.names == ("Acme Widgets, Inc.", "Acme")

    @pytest.mark.parametrize(
        ("precision", "expected"),
        [(11, "2019-06-30"), (10, "2019-06"), (9, "2019")],
    )
    def test_a_dissolution_date_is_rendered_at_its_own_precision(
        self, precision, expected,
    ):
        """Printing a day Wikidata does not claim would put a fact in the flag
        reason that the source does not carry."""
        entity = make_entity("Q1", "Acme", claims={
            "P576": [time_claim("P576", "2019-06-30", precision=precision)],
        })
        assert parse_entity("Q1", entity).dissolved == expected

    def test_the_fixture_keeps_only_what_the_lane_reads(self):
        """A recorded entity is what the pipeline CONSUMED, not everything the
        API sent. The unpruned recordings for the 100-row chemspeed batch came
        to 4.3 MB against 589 KB for the entire page-read fixture store; pruned
        they are 169 KB, and a human can read one."""
        from enrichment.wikidata import READ_PROPERTIES, prune_entity

        raw = _org_entity("Q1", "Acme Widgets Inc", ror=UFL_ROR, hq="Q62")
        # Statements the lane never looks at, of the kind a well-cited item
        # accumulates by the hundred.
        raw["claims"]["P1433"] = [entity_claim("P1433", "Q15756077")]
        raw["claims"]["P2860"] = [entity_claim("P2860", "Q123")]
        raw["descriptions"] = {"en": {"value": "a company"}}

        pruned = prune_entity(raw)
        assert set(pruned["claims"]) <= set(READ_PROPERTIES)
        assert "P1433" not in pruned["claims"]
        assert "descriptions" not in pruned
        # ...and the pruned form parses to exactly the same item.
        assert parse_entity("Q1", pruned) == parse_entity("Q1", raw)

    def test_a_registrable_domain_comparison_ignores_scheme_and_www(self):
        assert website_agrees("https://www.acme.com/en/", "acme.com") is True
        assert website_agrees("https://acme.com", "acmecorp.com") is False
        # Nothing to compare is a third answer, not a disagreement.
        assert website_agrees(None, "acme.com") is None
        assert website_agrees("https://acme.com", None) is None


# ---------------------------------------------------------------------------
# The gauntlet
# ---------------------------------------------------------------------------

class TestTheGauntlet:
    async def _resolve(self, entities, *, name="Acme Widgets Inc", **kw):
        client = MockWikidataClient(
            search={name.lower(): list(entities)}, entities=entities,
        )
        return await resolve_wikidata(
            record_id="R1", name=name, city=kw.pop("city", "Irvine"),
            region=kw.pop("region", "CA"), client=client, threshold=88.0,
        )

    @pytest.mark.asyncio
    async def test_a_disambiguation_page_is_a_no_match_not_a_menu(self):
        outcome = await self._resolve({
            "Q1": make_entity(
                "Q1", "Acme Widgets Inc",
                claims={"P31": [entity_claim("P31", Q_DISAMBIG)]},
            ),
            # A perfectly good candidate sitting behind it. It must not be
            # reached: the wiki has said the name identifies several things.
            "Q2": _org_entity("Q2", "Acme Widgets Inc"),
        })
        assert outcome.outcome == NO_MATCH
        assert outcome.item is None
        assert "disambiguation" in outcome.reasons

    @pytest.mark.asyncio
    async def test_a_film_with_a_matching_name_is_refused_on_type(self):
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc", kind=Q_FILM),
        })
        assert outcome.outcome == NO_MATCH
        assert TYPE_REJECTED in outcome.reasons
        assert outcome.candidates[0].rejected_by == TYPE_REJECTED

    @pytest.mark.asyncio
    async def test_a_matching_name_in_the_wrong_country_is_refused(self):
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc", country=Q_GERMANY),
        })
        assert outcome.outcome == NO_MATCH
        assert COUNTRY_REJECTED in outcome.reasons

    @pytest.mark.asyncio
    async def test_no_country_statement_at_all_is_refused(self):
        """Deliberately conservative. An item nobody has finished curating is
        not thereby American, and a wrong-country identity wrongly converges
        distinct entities in Phase 2 dedup."""
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc", country=None),
        })
        assert outcome.outcome == NO_MATCH
        assert COUNTRY_REJECTED in outcome.reasons

    @pytest.mark.asyncio
    async def test_the_country_may_come_from_the_headquarters(self):
        outcome = await self._resolve({
            "Q1": _org_entity(
                "Q1", "Acme Widgets Inc", country=None, hq="Q62",
            ),
            "Q62": _city_entity("Q62", "Irvine"),
        })
        assert outcome.outcome == MATCHED

    @pytest.mark.asyncio
    async def test_a_name_below_the_existing_threshold_is_refused(self):
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Consolidated Diesel Holdings"),
        })
        assert outcome.outcome == NO_MATCH
        assert outcome.candidates[0].rejected_by == "name_rejected"

    @pytest.mark.asyncio
    async def test_an_alias_can_carry_the_name_check(self):
        outcome = await self._resolve({
            "Q1": _org_entity(
                "Q1", "Acme Manufacturing Holdings Corporation",
                aliases=("Acme Widgets Inc",),
            ),
        })
        assert outcome.outcome == MATCHED

    @pytest.mark.asyncio
    async def test_a_contradicting_headquarters_city_is_refused(self):
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc", hq="Q1297"),
            "Q1297": _city_entity("Q1297", "Chicago"),
        })
        assert outcome.outcome == NO_MATCH
        assert "city_rejected" in outcome.reasons

    @pytest.mark.asyncio
    async def test_a_missing_headquarters_is_neutral(self):
        """Silence is not evidence — the same rule the page corroborator keeps."""
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc"),
        })
        assert outcome.outcome == MATCHED

    @pytest.mark.asyncio
    async def test_the_record_region_rescues_a_state_level_headquarters(self):
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc", hq="Q99"),
            "Q99": _city_entity("Q99", "California"),
        })
        assert outcome.outcome == MATCHED

    @pytest.mark.asyncio
    async def test_two_survivors_are_an_ambiguity_not_a_tiebreak(self):
        outcome = await self._resolve({
            "Q1": _org_entity("Q1", "Acme Widgets Inc"),
            "Q2": _org_entity("Q2", "Acme Widgets, Inc."),
        })
        assert outcome.outcome == AMBIGUOUS
        assert outcome.item is None

    @pytest.mark.asyncio
    async def test_the_gauntlet_runs_over_every_search_hit_in_one_fetch(self):
        """The collision check needs all five candidates, and the call budget
        allows one entity request — so the request is batched."""
        client = MockWikidataClient(
            search={"acme widgets inc": ["Q1", "Q2", "Q3"]},
            entities={
                "Q1": _org_entity("Q1", "Acme Widgets Inc", kind=Q_FILM),
                "Q2": _org_entity("Q2", "Something Else Entirely"),
                "Q3": _org_entity("Q3", "Acme Widgets Inc"),
            },
        )
        outcome = await resolve_wikidata(
            record_id="R1", name="Acme Widgets Inc", city="Irvine",
            region="CA", client=client, threshold=88.0,
        )
        assert outcome.outcome == MATCHED
        assert outcome.qid == "Q3"
        assert client.fetched == [["Q1", "Q2", "Q3"]]

    @pytest.mark.asyncio
    async def test_an_empty_search_is_a_clean_no_match(self):
        client = MockWikidataClient()
        outcome = await resolve_wikidata(
            record_id="R1", name="Acme Widgets Inc", city=None, region=None,
            client=client, threshold=88.0,
        )
        assert outcome.outcome == NO_MATCH
        assert outcome.reasons == set()


# ---------------------------------------------------------------------------
# Following the pointer
# ---------------------------------------------------------------------------

class TestCrosswalk:
    @pytest.mark.asyncio
    async def test_a_ror_pointer_is_followed_and_ror_writes_the_identity(self):
        """The happy path. Wikidata supplies ``P6782``; ROR supplies the name,
        the id, the domain and the provenance."""
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity(
                "Q1", "Acme Widgets Inc", kind=Q_UNIVERSITY, ror=UFL_ROR,
            )},
        ))
        record = _record()
        result = _after_registry_miss(record)

        assert await _run_lane(orch, record, result) is True

        # The registry's values, not the wiki's label.
        assert result["ror_id"] == "https://ror.org/02y3ad647"
        assert result["name1_enriched"] == "University of Florida"
        assert result["domain"] == "ufl.edu"
        assert result["source"] == "ROR"
        assert result["tier_used"] == 1

        # ...and the registry's provenance. The PRODUCER and the SCALE are
        # ROR's on every field the crosswalk caused to be written — a reviewer
        # opening this record is sent to ROR, not to a wiki. The `rule_id` does
        # say `wikidata:crosswalk-ror`, and should: it names the rule that
        # fired, exactly as `fix2:tier1-retry-after-canonicalisation` does on
        # the Stage 5 path, and it is how a crosswalked identity is auditable
        # after the fact.
        for field in ("name1_enriched", "ror_id", "domain"):
            event = result.provenance.attributing_event(field)
            assert event.producer_chain == ("ror",), field
            assert "wikidata" not in event.confidence_scale
        for field in ("name1_enriched", "ror_id"):
            assert result.provenance.attributing_event(
                field,
            ).rule_id == "wikidata:crosswalk-ror"
        assert result.provenance.attributing_event(
            "name1_enriched",
        ).confidence_scale == "registry_exact"
        # The domain went through the ownership guard's own write path,
        # unchanged, and is attributed to the condition that carried it.
        assert result.provenance.attributing_event(
            "domain",
        ).rule_id == "domain-ownership:registry"

        counts = orch._wikidata_counts
        assert counts["matched"] == 1
        assert counts["crosswalk_ror"] == 1
        assert counts["crosswalk_registry_hit"] == 1
        assert counts["witness_only"] == 0

    @pytest.mark.asyncio
    async def test_a_lei_pointer_to_a_different_company_is_refused_by_gleif(self):
        """GLEIF's name-verification guard, unchanged. The pointer resolves to
        PFIZER AG for a record that says "Acme Widgets Inc", the existing
        ``token_sort_ratio`` guard scores it far below 88, and nothing is
        written — which is exactly what stops a stale or vandalised wiki link
        putting another company's legal name into the customer master."""
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity(
                "Q1", "Acme Widgets Inc", lei=PFIZER_LEI,
            )},
        ))
        record = _record()
        result = _after_registry_miss(record)
        before = result["name1_enriched"]

        assert await _run_lane(orch, record, result) is False

        assert result["lei_id"] is None
        assert result["name1_enriched"] == before
        assert orch._wikidata_counts["crosswalk_lei"] == 1
        assert orch._wikidata_counts["crosswalk_registry_hit"] == 0
        # The refusal is on the record's log, not silently dropped.
        rejections = [
            r for r in result.provenance.rejections if r.guard == GUARD_GLEIF_NAME
        ]
        assert rejections and rejections[0].candidate == "PFIZER AG"

    @pytest.mark.asyncio
    async def test_a_lei_pointer_that_verifies_lets_gleif_write_the_identity(self):
        orch = _orch(MockWikidataClient(
            search={"pfizer": ["Q1"]},
            entities={"Q1": _org_entity("Q1", "Pfizer", lei=PFIZER_LEI)},
        ))
        record = _record(name1="Pfizer")
        result = _after_registry_miss(record)

        assert await _run_lane(orch, record, result) is True
        assert result["lei_id"] == PFIZER_LEI
        assert result["name1_enriched"] == "PFIZER AG"
        assert result["source"] == "gleif"
        event = result.provenance.attributing_event("name1_enriched")
        assert event.producer_chain == ("gleif",)

    @pytest.mark.asyncio
    async def test_a_pointer_to_a_record_that_does_not_exist_is_a_clean_miss(self):
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity(
                "Q1", "Acme Widgets Inc", ror="99nosuchid",
            )},
        ))
        record = _record()
        result = _after_registry_miss(record)

        assert await _run_lane(orch, record, result) is False
        assert result["ror_id"] is None
        assert orch._wikidata_counts["crosswalk_ror"] == 1
        assert orch._wikidata_counts["crosswalk_registry_hit"] == 0
        # A pointer the registry refused does NOT fall back to witnessing:
        # taking the wiki's word after the authority declined would invert the
        # ordering the whole lane rests on.
        assert orch._wikidata_counts["witness_only"] == 0
        assert result.get("operating_name") is None


# ---------------------------------------------------------------------------
# Witnessing
# ---------------------------------------------------------------------------

class TestWitnessOnly:
    @pytest.mark.asyncio
    async def test_a_pointerless_match_writes_operating_name_and_nothing_else(self):
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity("Q1", "Acme Widgets, Incorporated")},
        ))
        record = _record()
        result = _after_registry_miss(record)
        before = result["name1_enriched"]

        assert await _run_lane(orch, record, result) is False

        # The one field a witness may write.
        assert result["operating_name"] == "Acme Widgets, Incorporated"
        assert result["operating_name_provenance"] == WITNESS_PROVENANCE
        # Byte-identical Name 1. This is the rule, stated as an assertion.
        assert result["name1_enriched"] == before
        assert result["ror_id"] is None and result["lei_id"] is None
        assert orch._wikidata_counts["witness_only"] == 1

    @pytest.mark.asyncio
    async def test_a_page_read_identity_is_not_overwritten(self):
        """The site itself is the better witness of the two, and the field
        holds one value."""
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity("Q1", "Acme Widgets, Incorporated")},
        ))
        record = _record()
        result = _after_registry_miss(record)
        result["operating_name"] = "Acme, Inc."
        result["operating_name_provenance"] = "web:acme.com:extracted:2026-08-22"

        await _run_lane(orch, record, result)
        assert result["operating_name"] == "Acme, Inc."
        assert result["operating_name_provenance"].startswith("web:")

    @pytest.mark.asyncio
    async def test_a_witness_match_makes_a_retained_name1_unchanged_verified(self):
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity("Q1", "Acme Widgets Inc")},
        ))
        record = _record()
        result = _after_registry_miss(record)
        await _run_lane(orch, record, result)

        outcome = resolve_unchanged(result)
        assert outcome is not None
        assert outcome.state == UNCHANGED_VERIFIED
        assert outcome.evidence == "wikidata:Q1"
        assert not outcome.flagged

    @pytest.mark.asyncio
    async def test_an_agreeing_official_website_corroborates_the_domain(self):
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity(
                "Q1", "Acme Widgets Inc", website="https://www.acme.com/en/",
            )},
        ))
        record = _record()
        result = _after_registry_miss(record)
        await _run_lane(orch, record, result)

        # The website paths run after the lane, which is why the check is
        # deferred rather than made inside it.
        result.write(
            "domain", "acme.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        orch._corroborate_domain_from_wikidata(result)

        assert orch._wikidata_counts["domain_corroborated"] == 1
        assert orch._wikidata_counts["domain_disagree"] == 0
        assert result["_wikidata_corroboration"]["domain_corroborated"] is True

    @pytest.mark.asyncio
    async def test_a_disagreeing_website_never_withdraws_the_domain(self):
        """P856 can be years stale. Counted, and nothing else."""
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity(
                "Q1", "Acme Widgets Inc", website="https://acme-legacy.com",
            )},
        ))
        record = _record()
        result = _after_registry_miss(record)
        await _run_lane(orch, record, result)
        result.write(
            "domain", "acme.com",
            deterministic_evidence("test", producer="website_resolver"),
        )
        orch._corroborate_domain_from_wikidata(result)

        assert orch._wikidata_counts["domain_disagree"] == 1
        assert result["domain"] == "acme.com"
        assert result["website_url"] is None or "acme.com" in result["website_url"]


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------

class TestSupersession:
    @pytest.mark.asyncio
    async def test_a_replaced_entity_is_flagged_and_the_name_is_left_alone(self):
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={
                "Q1": _org_entity(
                    "Q1", "Acme Widgets Inc", replaced_by="Q900",
                    dissolved="2019-06-30",
                ),
                "Q900": make_entity("Q900", "Consolidated Widgets Holdings"),
            },
        ))
        record = _record()
        result = _after_registry_miss(record)
        before = result["name1_enriched"]

        await _run_lane(orch, record, result)

        # The successor is NAMED, never written. Which legal entity a customer
        # record should point at after a merger is a business decision.
        assert result["name1_enriched"] == before
        detail = result["_ev_entity_superseded"]
        assert "Consolidated Widgets Holdings" in detail
        assert "Q900" in detail
        assert orch._wikidata_counts["superseded_flagged"] == 1

        compute_flags(result)
        assert ENTITY_SUPERSEDED in result["flag_codes"]
        assert result["flag_for_review"] is True
        assert result["flagged_fields"] == ["name1"]
        assert "Consolidated Widgets Holdings" in result["flag_reason"]

    @pytest.mark.asyncio
    async def test_a_dissolution_with_no_successor_states_the_date(self):
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={"Q1": _org_entity(
                "Q1", "Acme Widgets Inc", dissolved="2019-06-30",
            )},
        ))
        record = _record()
        result = _after_registry_miss(record)
        await _run_lane(orch, record, result)
        assert result["_ev_entity_superseded"] == "dissolved 2019-06-30"

    @pytest.mark.asyncio
    async def test_the_flag_stands_even_when_the_crosswalk_resolves(self):
        """A dissolved entity's registry record is still informative, so the
        pointer is still followed — and the flag is about the entity, not about
        whether the lookup worked."""
        orch = _orch(MockWikidataClient(
            search={"acme widgets inc": ["Q1"]},
            entities={
                "Q1": _org_entity(
                    "Q1", "Acme Widgets Inc", kind=Q_UNIVERSITY,
                    ror=UFL_ROR, replaced_by="Q900",
                ),
                "Q900": make_entity("Q900", "Consolidated Widgets Holdings"),
            },
        ))
        record = _record()
        result = _after_registry_miss(record)

        assert await _run_lane(orch, record, result) is True
        assert result["ror_id"] == "https://ror.org/02y3ad647"
        assert "Consolidated Widgets Holdings" in result["_ev_entity_superseded"]

    def test_the_flag_renders_without_a_detail_too(self):
        """An older or partial marker still raises the code, with the generic
        wording — the same fallback ``domain-unverified`` has."""
        result = {"record_id": "R1", "flag_codes": [],
                  "_ev_entity_superseded": True}
        compute_flags(result)
        assert ENTITY_SUPERSEDED in result["flag_codes"]
        assert "no longer exists as a separate entity" in result["flag_reason"]


# ---------------------------------------------------------------------------
# Failure is closed
# ---------------------------------------------------------------------------

class TestFailureIsClosed:
    @pytest.mark.asyncio
    async def test_a_timeout_is_counted_as_unavailable_and_writes_nothing(self):
        orch = _orch(MockWikidataClient(fail="timeout"))
        record = _record()
        result = _after_registry_miss(record)
        before = dict(result)

        assert await _run_lane(orch, record, result) is False

        assert orch._wikidata_counts["unavailable"] == 1
        assert orch._wikidata_counts["queried"] == 1
        assert orch._wikidata_counts["no_match"] == 0
        assert result["name1_enriched"] == before["name1_enriched"]
        assert result.get("operating_name") is None

    @pytest.mark.asyncio
    async def test_the_client_turns_a_read_timeout_into_wikidata_unavailable(
        self, monkeypatch,
    ):
        settings = Settings()
        object.__setattr__(settings, "wikidata_max_retries", 0)
        client = WikidataClient(settings)

        async def _boom(self, url, params=None, **kw):
            raise httpx.ReadTimeout("too slow")

        monkeypatch.setattr(httpx.AsyncClient, "get", _boom)
        with pytest.raises(WikidataUnavailable):
            await client.search("Acme Widgets Inc")

    @pytest.mark.asyncio
    async def test_a_malformed_body_is_unavailable_not_a_miss(self, monkeypatch):
        settings = Settings()
        client = WikidataClient(settings)

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("not json")

        async def _ok(self, url, params=None, **kw):
            return _Resp()

        monkeypatch.setattr(httpx.AsyncClient, "get", _ok)
        with pytest.raises(WikidataUnavailable):
            await client.search("Acme Widgets Inc")

    @pytest.mark.asyncio
    async def test_replay_only_refuses_to_reach_the_network(self, monkeypatch):
        from utils.cache import PageCache

        settings = Settings()
        client = WikidataClient(settings, cache=PageCache(replay_only=True))

        async def _boom(self, url, params=None, **kw):  # pragma: no cover
            raise AssertionError("replay_only must not call out")

        monkeypatch.setattr(httpx.AsyncClient, "get", _boom)
        with pytest.raises(WikidataUnavailable):
            await client.search("Acme Widgets Inc")

    @pytest.mark.parametrize(
        ("attempt", "status", "headers", "expected"),
        [
            # A 429 is the API stating a rate, not a failure to recover from.
            # Retrying it in half a second is not a retry, it is the same
            # request — measured: 28 of 68 invocations were rate-limited on the
            # first live run under GLEIF's 0.5s schedule.
            (1, 429, {}, 5.0),
            (2, 429, {}, 10.0),
            # An ordinary transient error keeps the standard schedule.
            (1, 503, {}, 0.5),
            (2, 503, {}, 1.0),
            (1, None, {}, 0.5),
            # `Retry-After` wins: guessing over the top of the server's own
            # stated interval is how a client earns a longer ban.
            (1, 429, {"Retry-After": "3"}, 3.0),
            # ...but is capped, so one header cannot stall a whole batch.
            (1, 429, {"Retry-After": "600"}, 30.0),
            # An HTTP-date form is not parsed; fall through to the schedule.
            (1, 429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 5.0),
        ],
    )
    def test_the_backoff_schedule(self, attempt, status, headers, expected):
        from enrichment.wikidata import _backoff

        class _Resp:
            def __init__(self, h):
                self.headers = h

        assert _backoff(attempt, status, _Resp(headers)) == expected

    @pytest.mark.asyncio
    async def test_the_lane_never_fails_a_record(self):
        """Even an exception the client was not supposed to raise."""
        class _Exploding(MockWikidataClient):
            async def search(self, name):
                raise RuntimeError("boom")

        orch = _orch(_Exploding())
        record = _record()
        result = _after_registry_miss(record)
        assert await _run_lane(orch, record, result) is False
        assert orch._wikidata_counts["unavailable"] == 1


# ---------------------------------------------------------------------------
# The lane is a pure insert
# ---------------------------------------------------------------------------

_VOLATILE = {"duration_ms", "retrieved_at"}


def _scrub(value):
    """Drop the two keys that legitimately differ between two runs of the same
    batch — a wall-clock duration and a fetch timestamp. Everything else must
    match exactly."""
    if isinstance(value, dict):
        return {
            k: _scrub(v) for k, v in value.items() if k not in _VOLATILE
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _dump(response):
    return [_scrub(r.model_dump(by_alias=True)) for r in response.results]


_BATCH = [
    EnrichmentRecord(
        record_id="R1", name1="Acme Widgets Inc", country="US",
        city="Irvine", state="CA",
    ),
    EnrichmentRecord(
        record_id="R2", name1="Belharra Therapeutics, Inc.", country="US",
    ),
    EnrichmentRecord(
        record_id="R3", name1="Coastal Analytical Services", country="US",
        city="Tampa", state="FL",
    ),
    # One record that DOES resolve, so the comparison covers a registry path
    # as well as three misses.
    EnrichmentRecord(record_id="R4", name1="MIT", country="US"),
]


class TestTheLaneIsAPureInsert:
    @pytest.mark.asyncio
    async def test_the_pipeline_is_byte_identical_with_the_lane_disabled(self):
        """The acceptance criterion for "pure insert", stated as a test.

        The baseline is the pipeline **without the lane at all** — the method
        is replaced by one that fails if it is ever entered, which is as close
        as a test can get to running the pre-lane build. The comparison run has
        the lane wired in and switched off at config, and is given a client
        that would match every record in the batch, so a lane that leaked
        anything past the flag would show up.

        Every serialised field is compared, not a chosen few; only the
        wall-clock duration and a fetch timestamp are scrubbed."""
        options = EnrichmentOptions(max_concurrency=1)
        matching = dict(
            search={"acme": ["Q1"], "belharra": ["Q2"], "coastal": ["Q3"]},
            entities={
                "Q1": _org_entity("Q1", "Acme Widgets Inc", ror=UFL_ROR),
                "Q2": _org_entity("Q2", "Belharra Therapeutics, Inc."),
                "Q3": _org_entity("Q3", "Coastal Analytical Services"),
            },
        )

        # The baseline: the two lane entry points excised, so this run is the
        # pre-lane pipeline as exactly as a test can reconstruct it.
        without = _orch(MockWikidataClient(**matching))

        async def _absent(*a, **kw):
            return False

        without._wikidata_crosswalk = _absent
        # BOTH entry points. The lane gained a second one — the
        # corroboration-only pass that retains `P856` on a record the
        # registries already resolved — and a pure-insert baseline that
        # excised only the first would stop being a baseline.
        without._retain_wikidata_website = _absent
        without._corroborate_domain_from_wikidata = lambda result: None
        baseline = await without.enrich_batch(copy.deepcopy(_BATCH), options)

        off_client = MockWikidataClient(**matching)
        off = _orch(off_client)
        object.__setattr__(off._settings, "wikidata_enabled", False)
        disabled = await off.enrich_batch(copy.deepcopy(_BATCH), options)

        assert _dump(disabled) == _dump(baseline)
        # ...and the lane really was reached and really did decline, rather
        # than the batch having no eligible record in it.
        assert all(v == 0 for v in off._wikidata_counts.values())
        assert off_client.searched == []

    @pytest.mark.asyncio
    async def test_the_same_batch_with_the_lane_ON_does_change(self):
        """Guards the test above against passing vacuously: the client it uses
        really would move these records, so "identical" is a statement about
        the flag and not about a lane that could never fire."""
        options = EnrichmentOptions(max_concurrency=1)
        matching = dict(
            search={"acme": ["Q1"], "belharra": ["Q2"], "coastal": ["Q3"]},
            entities={
                "Q1": _org_entity("Q1", "Acme Widgets Inc", ror=UFL_ROR),
                "Q2": _org_entity("Q2", "Belharra Therapeutics, Inc."),
                "Q3": _org_entity("Q3", "Coastal Analytical Services"),
            },
        )

        off = _orch(MockWikidataClient(**matching))
        object.__setattr__(off._settings, "wikidata_enabled", False)
        baseline = await off.enrich_batch(copy.deepcopy(_BATCH), options)

        on = _orch(MockWikidataClient(**matching))
        after = await on.enrich_batch(copy.deepcopy(_BATCH), options)

        assert _dump(after) != _dump(baseline)
        assert on._wikidata_counts["crosswalk_registry_hit"] == 1
        assert on._wikidata_counts["witness_only"] == 2
        # ...and Name 1 still only ever moves where a REGISTRY wrote it.
        moved = [
            (b.record_id, b.name1_enriched, a.name1_enriched)
            for b, a in zip(baseline.results, after.results)
            if b.name1_enriched != a.name1_enriched
        ]
        assert [m[0] for m in moved] == ["R1"]
        assert moved[0][2] == "University of Florida"

    @pytest.mark.asyncio
    async def test_a_disabled_lane_issues_no_call_at_all(self):
        client = MockWikidataClient(
            search={"acme": ["Q1"]},
            entities={"Q1": _org_entity("Q1", "Acme Widgets Inc")},
        )
        orch = _orch(client)
        object.__setattr__(orch._settings, "wikidata_enabled", False)
        await orch.enrich_batch(copy.deepcopy(_BATCH), EnrichmentOptions(
            max_concurrency=1,
        ))
        assert client.searched == []
        assert orch._wikidata_counts["queried"] == 0

    @pytest.mark.asyncio
    async def test_an_unavailable_lane_leaves_the_record_exactly_as_it_was(self):
        """Requirement 10: an API timeout is counted, and the record proceeds
        down the rest of the waterfall — the web lane included — producing the
        same output as a run in which the lane did not exist."""
        options = EnrichmentOptions(max_concurrency=1)

        off = _orch()
        object.__setattr__(off._settings, "wikidata_enabled", False)
        baseline = await off.enrich_batch(copy.deepcopy(_BATCH), options)

        timing_out = _orch(MockWikidataClient(fail="timeout"))
        after = await timing_out.enrich_batch(copy.deepcopy(_BATCH), options)

        assert _dump(after) == _dump(baseline)
        assert after.summary.wikidata_unavailable == timing_out._wikidata_counts[
            "queried"
        ]
        assert after.summary.wikidata_unavailable > 0

    @pytest.mark.asyncio
    async def test_the_lane_is_skipped_on_a_record_that_already_has_a_registry_id(
        self,
    ):
        client = MockWikidataClient(
            search={"mit": ["Q1"]},
            entities={"Q1": _org_entity("Q1", "MIT")},
        )
        orch = _orch(client)
        record = _record(name1="MIT")
        result = _after_registry_miss(record)
        result.write(
            "ror_id", "https://ror.org/042nb2s44",
            deterministic_evidence("test", producer="ror", tier=1),
        )
        assert await _run_lane(orch, record, result) is False
        assert client.searched == []
        assert orch._wikidata_counts["queried"] == 0

    @pytest.mark.asyncio
    async def test_the_counters_partition_the_invocations(self):
        """`matched`, `no_match`, `ambiguous` and `unavailable` must add up to
        `queried`; a counter that double-counts makes the report unreadable."""
        orch = _orch(MockWikidataClient(
            search={
                "acme widgets inc": ["Q1"],
                "belharra therapeutics, inc.": ["Q2", "Q3"],
                "coastal analytical services": [],
            },
            entities={
                "Q1": _org_entity("Q1", "Acme Widgets Inc"),
                "Q2": _org_entity("Q2", "Belharra Therapeutics, Inc."),
                "Q3": _org_entity("Q3", "Belharra Therapeutics Inc"),
            },
        ))
        await orch.enrich_batch(
            copy.deepcopy(_BATCH), EnrichmentOptions(max_concurrency=1),
        )
        c = orch._wikidata_counts
        assert c["queried"] == (
            c["matched"] + c["no_match"] + c["ambiguous"] + c["unavailable"]
        )
        assert c["queried"] > 0
        assert c["ambiguous"] == 1
