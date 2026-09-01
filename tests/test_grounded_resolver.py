"""The universal grounded lane — `enrichment/grounded_resolver.py`.

Seven cases, and between them they pin the three things the lane changes about
a record that nothing else could settle: WHAT it writes, WHAT backs it, and
whether a reviewer is asked to look.

The LLM is stubbed per test rather than curated into `openai_mock`, because
every case here is defined by a DIFFERENT model answer over the same evidence
— a registry-resolvable name, an unresolvable one, one with no evidence index,
one that swaps the entity, one that says Name 2 is an alias. A shared mock
would have to key on the record to tell them apart, which is the test's own
setup hiding inside a fixture.

The SERP / page / ROR fixtures ARE shared, and additive: `nasa.gov`,
`kelvinbridge.com`, and two new ROR records (NASA and Ames, deliberately two
records with two identifiers — that is the whole subject of case 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentOptions, EnrichmentRecord
from enrichment.grounded_resolver import (
    ORIGIN_LLM,
    ORIGIN_ROR,
    ORIGIN_SERP,
    build_query,
    run_grounded_resolver,
)
from enrichment.orchestrator import Orchestrator
from tests.mocks.openai_mock import MockOpenAIClient

# The one string that identifies the grounded lane's user prompt. Asserting on
# it is how a test says "the lane ran" without reaching into the orchestrator.
GROUNDED_MARKER = "EVIDENCE (numbered"
TIER3_MARKER = "infer official org and dept"


class StubLLM(MockOpenAIClient):
    """The curated mock, with the grounded answer supplied per test.

    Everything the record passes through on its way to the lane — the lab
    resolver, Tier 2 canonicalisation, company canonicalisation — is answered
    with a decline, so the record actually REACHES the lane instead of being
    settled earlier by a mock that happened to know the name. Those declines
    are the situation the lane exists for.
    """

    def __init__(self, grounded: dict | None = None) -> None:
        self.grounded = grounded or {}
        #: Every user prompt this client was handed, in order.
        self.prompts: list[str] = []

    async def extract_json(self, system, user, **kwargs):
        self.prompts.append(user)
        lowered = user.lower()
        if GROUNDED_MARKER in user:
            return dict(self.grounded)
        if "research unit (a lab" in lowered:            # lab resolver
            return {"parent_department": None, "confidence": "low", "reasoning": ""}
        if "user-supplied department text:" in lowered:  # tier 2 canonical
            return {"official_name": None, "confidence": "low", "reasoning": ""}
        if "user-supplied company name:" in lowered:     # company canonical
            return {"official_name": None, "confidence": "low", "reasoning": ""}
        return await super().extract_json(system, user, **kwargs)

    def saw(self, marker: str) -> bool:
        return any(marker in p or marker in p.lower() for p in self.prompts)


def _orchestrator(test_settings, mock_clients, llm: StubLLM) -> Orchestrator:
    clients = dict(mock_clients)
    clients["llm"] = llm
    return Orchestrator(test_settings, mock_clients=clients)


async def _run(orchestrator: Orchestrator, record: EnrichmentRecord):
    response = await orchestrator.enrich_batch(
        [record], EnrichmentOptions(max_concurrency=1),
    )
    return response.results[0]


class TestQueryConstruction:
    """The SERP query, from the record's own identifying material.

    Name 1 is no longer quoted. A quoted phrase demands the index hold that
    exact string, and a SAP name field routinely holds a lookup key rather
    than a name: `"VAMC REDDING VISN 21" REDDING CA` returned zero results for
    a VA clinic that is one ordinary search away, so the lane reported
    `serp_empty` and degraded — on a record it could have resolved.
    """

    def test_name1_is_unquoted_and_name2_reduced_to_its_subject(self):
        assert build_query(
            "Stanford University", "Department of Chemistry", "Stanford", "CA",
        ) == "Stanford University Chemistry Stanford CA"

    def test_omits_what_the_record_does_not_state(self):
        assert build_query("Acme GmbH", None, None, None) == "Acme GmbH"

    def test_a_visn_number_is_dropped(self):
        # The VA's internal network id. It names a region of the health
        # system, never the site the record is about, and no page repeats it.
        assert build_query("VAMC REDDING VISN 21", None, "REDDING", "CA") == (
            "VAMC REDDING REDDING CA"
        )
        assert build_query("VAMC Redding Visn 21", None, "Redding", "CA") == (
            "VAMC Redding Redding CA"
        )

    def test_abbreviations_are_expanded(self):
        assert build_query("Univ of Texas", None, None, None) == (
            "University of Texas"
        )

    def test_a_name_that_is_only_a_visn_number_keeps_its_raw_value(self):
        # The fallback: a query built on nothing is worse than one built on
        # shorthand.
        assert build_query("VISN 21", None, None, None) == "VISN 21"


class TestGroundedResolver:
    """The seven cases."""

    # ── 1 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_name2_reverifies_to_its_own_registry_id(
        self, test_settings, mock_clients,
    ):
        """NASA / AMES RESEARCH CENTER.

        The record arrives with an acronym Tier 1 cannot resolve and a unit
        that is its OWN registered entity. The lane reads both off nasa.gov,
        takes both back to ROR, and both resolve — to DIFFERENT identifiers.
        That difference is what makes the Name 2 answer worth anything: a
        registry that answered with NASA's own id would have confirmed nothing
        about the centre.

        Registry-authored on both sides, so the record is not flagged. This is
        the outcome that did not previously exist: before, the record's only
        route past here was a model saying what it remembered.
        """
        llm = StubLLM({
            "name1_canonical": "National Aeronautics and Space Administration",
            "name2_canonical": "Ames Research Center",
            "name2_kind": "sub_entity",
            "per_field_confidence": {"name1": "high", "name2": "high"},
            "evidence_index": {"name1": 1, "name2": 0},
            "reasoning": "Both names are stated on nasa.gov.",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GR1", name1="NASA", name2="AMES RESEARCH CENTER",
                city="Moffett Field", state="CA", country="US",
            ),
        )

        assert llm.saw(GROUNDED_MARKER)
        assert result.name2_enriched == "Ames Research Center"
        assert result.name2_registry_id == "https://ror.org/02acart68"
        # Its own entity, not the institution's.
        assert result.name2_registry_id != result.ror_id
        assert result.name1_enriched == (
            "National Aeronautics and Space Administration"
        )
        assert result.ror_id == "https://ror.org/027ka1x80"
        # A registry authored both names, so neither is a claim to review.
        assert result.name1_provenance == "ror:verified"
        assert result.name2_provenance == "ror:verified"
        assert result.flag_for_review is False
        assert result.source == "ROR"

    # ── 2 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_registry_miss_with_on_domain_evidence_is_sourced_to_the_page(
        self, test_settings, mock_clients,
    ):
        """A real company with a real site and no registry entry.

        GLEIF does not have Kelvin Bridge Instruments, so the lane adopts what
        the page said anyway — and says so: `SERP+LLM`, a `source_url` a
        reviewer can open, and the flag raised. The value is written; the
        difference from case 1 is entirely in what the record claims about it.
        """
        llm = StubLLM({
            "name1_canonical": "Kelvin Bridge Instruments Ltd",
            "name2_canonical": "Calibration Services",
            "name2_kind": "department",
            "per_field_confidence": {"name1": "high", "name2": "medium"},
            "evidence_index": {"name1": 0, "name2": 0},
            "reasoning": "Stated in the About page title.",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GR2", name1="Kelvin Bridge Instruments",
                name2="Calibration Services",
                city="Glasgow", country="GB",
            ),
        )

        assert result.name1_enriched == "Kelvin Bridge Instruments Ltd"
        assert result.source == "SERP+LLM"
        assert result.enrichment_status == "enriched"
        # No registry authored it, so the reviewer is asked — and given a URL.
        assert result.flag_for_review is True
        assert result.ror_id is None and result.lei_id is None

    # ── 3 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_llm_only_answer_is_labelled_llm_and_left_unresolved(
        self, test_settings, mock_clients,
    ):
        """The model answered but pointed at no evidence item.

        `evidence_index: null` is the model saying it did not get this from
        the pages it was shown — which makes the answer Tier 3's kind of
        answer, and the record says so: `LLM`, `unresolved`, flagged. The value
        is still written; a reviewer with a candidate is better off than a
        reviewer with a blank field, and `unresolved` is what stops the
        candidate being read as a finding.
        """
        llm = StubLLM({
            "name1_canonical": "Kelvin Bridge Instruments Ltd",
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "low"},
            "evidence_index": {"name1": None, "name2": None},
            "reasoning": "Not stated on any supplied page.",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GR3", name1="Kelvin Bridge Instruments",
                city="Glasgow", country="GB",
            ),
        )

        assert result.name1_enriched == "Kelvin Bridge Instruments Ltd"
        assert result.source == "LLM"
        assert result.confidence == "low"
        assert result.enrichment_status == "unresolved"
        assert result.flag_for_review is True
        assert result.name1_provenance == "llm:provisional"

    # ── 4 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_identity_guard_drops_only_the_field_it_refused(
        self, test_settings, mock_clients,
    ):
        """The model answered with a DIFFERENT company.

        "Kelvin Bridge Instruments" → "Wheatstone Metrology Group" shares no
        distinctive token, so `canonical_preserves_identity` refuses it. The
        refusal is scoped: Name 1 keeps what the record supplied, and Name 2's
        own proposal — which the guard has no objection to — still lands.

        Checked at the lane rather than through the orchestrator because the
        thing under test is what the lane PROPOSES: a dropped proposal leaves
        no trace downstream, which is the point of dropping it.
        """
        from tests.mocks.serp_mock import MockSearchClient
        from tests.mocks.page_mock import MockPageFetcher
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Wheatstone Metrology Group",
            "name2_canonical": "Calibration Services Department",
            "name2_kind": "department",
            "per_field_confidence": {"name1": "high", "name2": "high"},
            "evidence_index": {"name1": 0, "name2": 0},
            "reasoning": "",
        })
        stub = _EvidenceStating(
            "Wheatstone Metrology Group", "Calibration Services Department",
        )
        grounded = await run_grounded_resolver(
            "GR4",
            name1="Kelvin Bridge Instruments",
            name2="Calibration Services",
            street=None, city="Glasgow", state=None,
            country="GB", country_code="GB",
            routing_type="company", domain=None,
            search_client=stub,
            page_fetcher=stub,
            llm_client=llm,
            ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(),
            settings=test_settings,
        )

        assert grounded.ran is True
        assert grounded.dropped == {"name1": "identity_not_preserved"}
        assert grounded.name1 is None          # original stands
        assert grounded.name2 is not None      # the other field is untouched
        assert grounded.name2.value == "Calibration Services Department"

    # ── 5 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_alias_of_name1_clears_name2(
        self, test_settings, mock_clients,
    ):
        """Name 2 restated the institution rather than naming a unit.

        "NASA" in Name 2 alongside "NASA" in Name 1 is not a department. The
        lane empties the slot rather than canonicalising it into a second copy
        of the organisation's name, which is what a slot naming nothing new
        deserves.
        """
        llm = StubLLM({
            "name1_canonical": "National Aeronautics and Space Administration",
            "name2_canonical": None,
            "name2_kind": "alias_of_name1",
            "per_field_confidence": {"name1": "high", "name2": "high"},
            "evidence_index": {"name1": 1, "name2": None},
            "reasoning": "Name 2 is the agency's own acronym.",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GR5", name1="NASA", name2="NASA",
                city="Moffett Field", state="CA", country="US",
            ),
        )

        assert result.name2_enriched is None

    # ── 6 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_empty_serp_degrades_to_tier3_unchanged(
        self, test_settings, mock_clients,
    ):
        """No search results — the lane hands the record back.

        There is no evidence to ground anything in, so grounding is not on
        offer, and the lane must not spend an LLM call pretending otherwise.
        `run_tier3` runs exactly as it did before this lane existed: the record
        is never worse off for the lane having been tried.
        """
        llm = StubLLM({"name1_canonical": "Should Never Be Reached"})
        record = EnrichmentRecord(
            record_id="GR6", name1="Nonexistent Holdings GmbH",
            name2="Zentrale Beschaffung", city="Berlin", country="DE",
        )
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm), record,
        )

        assert not llm.saw(GROUNDED_MARKER), "no LLM call without evidence"
        assert llm.saw(TIER3_MARKER), "Tier 3 must still run"
        assert result.name1_enriched != "Should Never Be Reached"

    @pytest.mark.asyncio
    async def test_all_fetches_failing_also_degrades(
        self, test_settings, mock_clients,
    ):
        """SERP answered, every page read failed.

        Snippets alone are the search index's summary of a page, not the
        organisation's own statement of what it is called — and this lane
        exists to do better than that. So it degrades rather than dressing a
        snippet up as page evidence.
        """
        from tests.mocks.serp_mock import MockSearchClient
        from search.base import SearchResult
        from tests.mocks.page_mock import MockPageFetcher
        from utils.cache import BatchCache

        class NoPages(MockSearchClient):
            async def search(self, query, num_results=5, *, country=None):
                # "timeout" in the URL is `page_mock`'s own failure fixture.
                return [SearchResult(
                    title="Kelvin Bridge Instruments",
                    url="https://www.kelvinbridge.com/timeout/",
                    snippet="Precision metrology in Glasgow.",
                )]

        llm = StubLLM({"name1_canonical": "Should Never Be Reached"})
        grounded = await run_grounded_resolver(
            "GR6B",
            name1="Kelvin Bridge Instruments", name2=None,
            street=None, city="Glasgow", state=None,
            country="GB", country_code="GB",
            routing_type="company", domain=None,
            search_client=NoPages(),
            page_fetcher=MockPageFetcher(),
            llm_client=llm,
            ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(),
            settings=test_settings,
        )

        assert grounded.degraded is True
        assert grounded.degraded_reason == "all_fetches_failed"
        assert grounded.ran is False
        assert llm.prompts == []

    # ── 7 ────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_step16_passthrough_now_reaches_the_resolver(
        self, test_settings, mock_clients,
    ):
        """Gate change 1, in the one shape that distinguishes it.

        Tier 2 canonicalisation RAN on a populated Name 2 and declined. Before,
        that returned `passthrough` / `low` / `unresolved` — the record left
        holding exactly what it arrived with, having spent an LLM call to get
        there. Now the same record continues to the grounded lane.

        The assertion that matters is the prompt: `llm.saw(GROUNDED_MARKER)` is
        false on the old gate and true on the new one, and nothing else about
        the record has to change for that to be the whole difference.
        """
        llm = StubLLM({
            "name1_canonical": None,
            "name2_canonical": "Materials Testing Laboratory",
            "name2_kind": "department",
            "per_field_confidence": {"name1": "low", "name2": "medium"},
            "evidence_index": {"name1": None, "name2": 0},
            "reasoning": "",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GR7", name1="Stanford University",
                name2="Materials Testing",
                city="Stanford", state="CA", country="US",
            ),
        )

        assert llm.saw("user-supplied department text:"), (
            "the test only means anything if Tier 2 canonical actually ran"
        )
        assert llm.saw(GROUNDED_MARKER)
        assert result.source != "passthrough"


class TestConfirmationRatherThanRewrite:
    """A proposal that reproduces the record's own value is not a write.

    The regression this exists for was measured, not imagined: on the 100-row
    chemspeed batch the first cut of this lane rewrote 14 records' Name 1 with
    a string identical to the one already there, and each of those rewrites
    cost the record its `input:verified+web` attribution — a page read had
    corroborated the name — replacing it with `llm:provisional`. Five of them
    picked up an `unverified-inference` flag for a value that had not changed
    by a character.
    """

    @pytest.mark.asyncio
    async def test_an_unchanged_proposal_is_recorded_not_written(
        self, test_settings, mock_clients,
    ):
        from tests.mocks.serp_mock import MockSearchClient
        from tests.mocks.page_mock import MockPageFetcher
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Kelvin Bridge Instruments",   # == the input
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "GRE",
            name1="Kelvin Bridge Instruments", name2=None,
            street=None, city="Glasgow", state=None,
            country="GB", country_code="GB",
            routing_type="company", domain=None,
            search_client=MockSearchClient(), page_fetcher=MockPageFetcher(),
            llm_client=llm, ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )

        assert grounded.name1 is None, "an identical value is not a rewrite"
        assert grounded.confirmed == {
            "name1": "Kelvin Bridge Instruments",
        }
        assert grounded.settled_anything is True

    @pytest.mark.asyncio
    async def test_the_record_keeps_its_input_attribution_and_its_flag_state(
        self, test_settings, mock_clients,
    ):
        """End to end: nothing written, nothing relabelled, nothing flagged.

        The model agreeing with the record is evidence, so it is handed to
        `_canonical_proposal` — the field Fix 2's `unchanged-confirmed`
        already reads — rather than thrown away. What must NOT happen is a
        write, and `name1_provenance` starting with `input:` is how the test
        says the record still owns its own name.
        """
        llm = StubLLM({
            "name1_canonical": "Kelvin Bridge Instruments",
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GRF", name1="Kelvin Bridge Instruments",
                city="Glasgow", country="GB",
            ),
        )

        assert result.name1_enriched == "Kelvin Bridge Instruments"
        assert (result.name1_provenance or "").startswith("input:")
        assert "unverified-inference" not in (result.flag_codes or [])

    @pytest.mark.asyncio
    async def test_a_registry_hit_still_wins_over_an_unchanged_proposal(
        self, test_settings, mock_clients,
    ):
        """Confirmation does not block the registry step.

        The suppression is of the WRITE, not of the lookup — a proposal that
        reproduces the input is still taken to the registry, and a hit is
        still adopted. `ror:verified` outranks `input:verified+web`, so this
        is the one case where writing over a retained name is an upgrade.
        """
        from tests.mocks.serp_mock import MockSearchClient
        from tests.mocks.page_mock import MockPageFetcher
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Ames Research Center",   # == the input
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "GRG",
            name1="Ames Research Center", name2=None,
            street=None, city="Moffett Field", state="CA",
            country="US", country_code="US",
            routing_type="research_institution", domain=None,
            search_client=_EvidenceStating("Ames Research Center"),
            page_fetcher=_EvidenceStating("Ames Research Center"),
            llm_client=llm, ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )

        assert grounded.confirmed == {}
        assert grounded.name1 is not None
        assert grounded.name1.origin == ORIGIN_ROR
        assert grounded.name1.registry_id == "https://ror.org/02acart68"


class TestProvenanceTrail:
    """What the log says about how a grounded value came to be."""

    @pytest.mark.asyncio
    async def test_a_registry_hit_records_that_a_model_proposed_the_query(
        self, test_settings, mock_clients,
    ):
        """Two events on the field, in order: the model, then the registry.

        The spec for this lane asked for a ``+llm`` witness on the registry
        outcome. The grammar forbids it — ``enrichment.confidence`` hard rule 1
        is explicit that ``llm`` can neither produce nor contribute to
        ``verified``, and ``validate`` raises on ``ror:verified+llm``. So the
        model's contribution is recorded the way Fix 2's Tier 1 retry already
        records the same situation: as an EARLIER write on the same field.

        `attributing_event` takes the last write, so the exported column reads
        ``ror:verified`` and the value is unflagged; the log still shows that
        an LLM wrote first, with the string it proposed and the evidence item
        it read it from. Nothing is lost except a token the grammar rejects.
        """
        llm = StubLLM({
            "name1_canonical": "National Aeronautics and Space Administration",
            "name2_canonical": "Ames Research Center",
            "name2_kind": "sub_entity",
            "per_field_confidence": {"name1": "high", "name2": "high"},
            "evidence_index": {"name1": 1, "name2": 0},
            "reasoning": "",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GRP", name1="NASA", name2="AMES RESEARCH CENTER",
                city="Moffett Field", state="CA", country="US",
            ),
        )

        events = [
            e for e in (result.provenance or []) if e["field"] == "name2"
        ]
        producers = [tuple(e["producer_chain"]) for e in events]
        assert ("serp", "fetch", "llm_grounded") in producers
        # The registry write comes after it, so it is the attributing one.
        assert producers[-1] == ("ror",)
        assert result.name2_provenance == "ror:verified"

    @pytest.mark.asyncio
    async def test_an_ungrounded_value_reads_as_the_model_s_own_claim(
        self, test_settings, mock_clients,
    ):
        """No registry, no evidence index — `llm:provisional`, and flagged.

        `llm_grounded` is in `EVIDENCE_FREE_PRODUCERS` for the same reason
        `llm_tier3` is. A value resting on a model's reading and nothing a
        reviewer can re-open is a claim to review, whichever prompt produced
        it.
        """
        from enrichment.provenance import EVIDENCE_FREE_PRODUCERS

        assert "llm_grounded" in EVIDENCE_FREE_PRODUCERS

        llm = StubLLM({
            "name1_canonical": "Kelvin Bridge Instruments Ltd",
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "medium"},
            "evidence_index": {"name1": None},
            "reasoning": "",
        })
        result = await _run(
            _orchestrator(test_settings, mock_clients, llm),
            EnrichmentRecord(
                record_id="GRQ", name1="Kelvin Bridge Instruments",
                city="Glasgow", country="GB",
            ),
        )

        assert result.name1_provenance == "llm:provisional"
        assert "unverified-inference" in (result.flag_codes or [])
        assert result.flag_for_review is True


class TestGuardsAtTheLane:
    """The deterministic guards, checked where they act."""

    @pytest.fixture
    def clients(self, mock_clients):
        from tests.mocks.serp_mock import MockSearchClient
        from tests.mocks.page_mock import MockPageFetcher
        return {
            "search": MockSearchClient(),
            "page": MockPageFetcher(),
            "ror": mock_clients["ror"],
            "lei": mock_clients["lei"],
        }

    @pytest.mark.asyncio
    async def test_address_content_is_never_written_into_a_name(
        self, test_settings, clients,
    ):
        """Tier 3's address-like-name guard, on this lane's proposals.

        Imported from `tier3_llm`, not restated — a NAME slot holding street
        content is one rule, and the second copy is the one that drifts.
        """
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "1200 Industrial Park Road",
            "name2_canonical": "Calibration Services Department",
            "name2_kind": "department",
            "per_field_confidence": {"name1": "high", "name2": "high"},
            "evidence_index": {"name1": 0, "name2": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "GRA",
            name1="Kelvin Bridge Instruments", name2="Calibration Services",
            street="1200 Industrial Park Road", city="Glasgow", state=None,
            country="GB", country_code="GB",
            routing_type="company", domain=None,
            search_client=_EvidenceStating("Calibration Services Department"),
            page_fetcher=_EvidenceStating("Calibration Services Department"),
            llm_client=llm, ror_client=clients["ror"],
            lei_client=clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )

        assert grounded.dropped == {"name1": "address_like"}
        assert grounded.name1 is None
        assert grounded.name2 is not None

    @pytest.mark.asyncio
    async def test_a_name2_matching_name1s_own_id_is_refused(
        self, test_settings, clients,
    ):
        """The Name 2 own-entity condition.

        ROR answers "NASA" with the agency's record whichever slot asked. A
        registry hit that hands back Name 1's identifier has confirmed nothing
        about the unit, so it is not a Name 2 answer — and the field falls
        through to the evidence-backed path instead of acquiring an
        identifier that names the wrong thing.
        """
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "National Aeronautics and Space Administration",
            "name2_canonical": "National Aeronautics and Space Administration",
            "name2_kind": "sub_entity",
            "per_field_confidence": {"name1": "high", "name2": "high"},
            "evidence_index": {"name1": 1, "name2": 1},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "GRB",
            name1="NASA", name2="NASA HQ",
            street=None, city="Moffett Field", state="CA",
            country="US", country_code="US",
            routing_type="research_institution", domain=None,
            search_client=clients["search"], page_fetcher=clients["page"],
            llm_client=llm, ror_client=clients["ror"],
            lei_client=clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )

        assert grounded.name1 is not None
        assert grounded.name1.origin == ORIGIN_ROR
        assert grounded.name2 is not None
        # Same string, same registry, same id — refused as a Name 2 answer.
        assert grounded.name2.origin == ORIGIN_SERP
        assert grounded.name2.registry_id is None

    @pytest.mark.asyncio
    async def test_a_registry_in_another_country_is_refused(
        self, test_settings, clients,
    ):
        """Country consistency, on what the registry actually STATED.

        The registry clients guard on the code they were handed; this asks the
        same question of the answer that came back, which is the half a blank
        `country_code` leaves unasked. A US ROR record cannot settle a German
        record's name.
        """
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Ames Research Center",
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "GRC",
            name1="Ames Research Center", name2=None,
            street=None, city="Moffett Field", state=None,
            country="Germany", country_code=None,
            routing_type="research_institution", domain=None,
            search_client=_EvidenceStating("Ames Research Center"),
            page_fetcher=_EvidenceStating("Ames Research Center"),
            llm_client=llm, ror_client=clients["ror"],
            lei_client=clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )

        # No registry proposal: ROR matched, and the country comparison threw
        # the match away. What is left is the model reproducing the record's
        # own name, which is a confirmation and not an identifier.
        assert grounded.name1 is None
        assert grounded.confirmed.get("name1") == "Ames Research Center"

    @pytest.mark.asyncio
    async def test_an_out_of_range_evidence_index_is_not_evidence(
        self, test_settings, clients,
    ):
        """An index the model invented points at nothing.

        Treated as no index at all rather than as an error: the situation is
        "this claim names no evidence item", which is exactly what the `llm`
        origin is for. Accepting it would attach a `source_url` to a claim the
        page does not make.
        """
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Kelvin Bridge Instruments Ltd",
            "name2_canonical": None,
            "name2_kind": None,
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 99},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "GRD",
            name1="Kelvin Bridge Instruments", name2=None,
            street=None, city="Glasgow", state=None,
            country="GB", country_code="GB",
            routing_type="company", domain=None,
            search_client=clients["search"], page_fetcher=clients["page"],
            llm_client=llm, ror_client=clients["ror"],
            lei_client=clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )

        assert grounded.name1.origin == ORIGIN_LLM
        assert grounded.name1.evidence_index is None
        assert grounded.name1.source_url is None


# ── The proposal must appear in the evidence ────────────────────────────────


class _EvidenceStating:
    """Search + page stubs whose evidence states exactly *phrases*.

    The shared SERP mock answers an unmatched query with a generic default
    ("Faculty Profile Page", "Example University"), which was harmless while
    nothing compared a proposal against the evidence it was supposedly read
    from. It is not harmless now: a test that curates a model answer and takes
    the default evidence is a test whose proposal appears nowhere in its own
    evidence, and the containment guard drops it before the guard the test is
    actually about ever runs. These stubs put the proposal in the evidence so
    each test still exercises its own subject.
    """

    def __init__(self, *phrases: str) -> None:
        self._phrases = list(phrases)

    async def search(self, query, num_results=5, *, country=None):
        from search.base import SearchResult
        return [SearchResult(
            title=" | ".join(self._phrases),
            url="https://www.example.org/about/",
            snippet=". ".join(self._phrases) + ".",
        )]

    async def fetch_page_content(self, url, **kwargs):
        from search.page_fetcher import PageContent
        return PageContent(
            url=url, url_path="/about/",
            page_title=" | ".join(self._phrases),
            h1=self._phrases[0] if self._phrases else "",
            breadcrumb="", body_text=". ".join(self._phrases) + ".",
        )

    async def aclose(self):
        pass


class _ReddingSearch:
    """Evidence that states `Redding VA Clinic` and `Veterans Affairs`, and
    does not state `Veterans Affairs Medical Center Redding`."""

    async def search(self, query, num_results=5, *, country=None):
        from search.base import SearchResult
        return [
            SearchResult(
                title="Redding VA Clinic | VA Northern California Health Care",
                url="https://www.va.gov/northern-california-health-care/locations/redding-va-clinic/",
                snippet=(
                    "The Redding VA Clinic offers primary care to Veterans in "
                    "Shasta County. Part of the Department of Veterans Affairs."
                ),
            ),
        ]


class _ReddingPages:
    """A page whose readable slices say the same thing the snippet does.

    A fetchable page is required — the lane declines to answer on snippets
    alone (`all_fetches_failed`) — so the evidence is a real page that names
    the clinic and the department, and no institution called "Veterans Affairs
    Medical Center Redding".
    """

    async def fetch_page_content(self, url, **kwargs):
        from search.page_fetcher import PageContent
        return PageContent(
            url=url,
            url_path="/northern-california-health-care/locations/redding-va-clinic/",
            page_title="Redding VA Clinic | VA Northern California Health Care",
            h1="Redding VA Clinic",
            breadcrumb="Home > Locations > Redding VA Clinic",
            body_text="Part of the Department of Veterans Affairs.",
        )

    async def aclose(self):
        pass


class TestAProposalMustAppearInTheEvidence:
    """The lane's contract, enforced rather than trusted.

    S3 rows 13336690 and 13336733 share an address and differ only in the case
    of their input. One got `Redding VA Clinic`, which the evidence states;
    the other got `Veterans Affairs Medical Center Redding`, which it does not
    — the model assembled it from "Veterans Affairs" and the place name. Both
    were written. The evidence is handed to the model precisely so it can copy
    a name out of it, and nothing checked that it had.
    """

    HAYSTACK = (
        "[0] https://www.va.gov/northern-california-health-care/locations/"
        "redding-va-clinic/\n"
        "    search result title: Redding VA Clinic | VA Northern California\n"
        "    search result snippet: The Redding VA Clinic offers primary care "
        "to Veterans. Part of the Department of Veterans Affairs."
    )

    @staticmethod
    def _in(value, haystack):
        from enrichment.grounded_resolver import (
            _appears_in, _flatten_for_containment,
        )
        return _appears_in(value, _flatten_for_containment(haystack))

    def test_a_composed_name_is_not_in_the_evidence(self):
        assert self._in(
            "Veterans Affairs Medical Center Redding", self.HAYSTACK,
        ) is False

    def test_the_name_the_evidence_states_is_kept(self):
        assert self._in("Redding VA Clinic", self.HAYSTACK) is True

    @pytest.mark.parametrize("value", [
        "REDDING VA CLINIC",            # case
        "redding va clinic",            # case
        "Redding V.A. Clinic",          # punctuation
        "Redding  VA   Clinic",         # whitespace
    ])
    def test_case_and_punctuation_differences_are_still_verbatim(self, value):
        # The model may recase, repunctuate or rewrap what it copied. It may
        # not add a word.
        assert self._in(value, self.HAYSTACK) is True

    @pytest.mark.asyncio
    async def test_the_lane_drops_it_and_keeps_it_as_a_suggestion(
        self, test_settings, mock_clients,
    ):
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Veterans Affairs Medical Center Redding",
            "name2_kind": "none",
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "13336733",
            name1="VAMC Redding Visn 21",
            name2=None,
            street=None, city="Redding", state="CA",
            country="US", country_code="US",
            routing_type="government", domain=None,
            search_client=_ReddingSearch(),
            page_fetcher=_ReddingPages(),
            llm_client=llm,
            ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(),
            settings=test_settings,
        )
        assert grounded.ran is True
        assert grounded.dropped.get("name1") == "not_in_evidence"
        assert grounded.name1 is None
        # Kept, so the flag can carry what was refused.
        assert grounded.suggestions["name1"] == (
            "Veterans Affairs Medical Center Redding"
        )

    @pytest.mark.asyncio
    async def test_the_evidence_backed_name_survives(
        self, test_settings, mock_clients,
    ):
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Redding VA Clinic",
            "name2_kind": "none",
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "13336690",
            name1="VAMC REDDING VISN 21",
            name2=None,
            street=None, city="Redding", state="CA",
            country="US", country_code="US",
            routing_type="government", domain=None,
            search_client=_ReddingSearch(),
            page_fetcher=_ReddingPages(),
            llm_client=llm,
            ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(),
            settings=test_settings,
        )
        assert grounded.ran is True
        assert "name1" not in grounded.dropped
        assert grounded.name1 is not None
        assert grounded.name1.value == "Redding VA Clinic"



class TestTheHintIsASecondQuery:
    """A name an earlier lane could not confirm is asked about, not adopted.

    Company canonicalisation recalls a name; it reads nothing. When the gate
    calls that `undecidable`, the record stays eligible for the §1d
    fall-through and the proposal travels here as a hint — issued as its own
    search alongside the record's own words, so the containment guard can
    decide between them on evidence.
    """

    @pytest.mark.asyncio
    async def test_both_queries_are_issued_and_the_evidence_pooled(
        self, test_settings, mock_clients,
    ):
        from utils.cache import BatchCache

        queries: list[str] = []

        class _Recording(_EvidenceStating):
            async def search(self, query, num_results=5, *, country=None):
                queries.append(query)
                return await super().search(query, num_results, country=country)

        stub = _Recording("Redding VA Clinic")
        llm = StubLLM({
            "name1_canonical": "Redding VA Clinic",
            "name2_kind": "none",
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        grounded = await run_grounded_resolver(
            "13336733",
            name1="VAMC Redding Visn 21", name2=None,
            street=None, city="Redding", state="CA",
            country="US", country_code="US",
            routing_type="government", domain=None,
            hint="Veterans Affairs Medical Center Redding",
            search_client=stub, page_fetcher=stub,
            llm_client=llm, ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )
        assert len(queries) == 2
        # The record's own words first, so `evidence_index` still points at
        # the record's own material by default.
        assert queries[0] == "VAMC Redding Redding CA"
        assert queries[1] == "Veterans Affairs Medical Center Redding Redding CA"
        assert grounded.name1 is not None
        assert grounded.name1.value == "Redding VA Clinic"

    @pytest.mark.asyncio
    async def test_no_hint_means_one_query(self, test_settings, mock_clients):
        from utils.cache import BatchCache

        queries: list[str] = []

        class _Recording(_EvidenceStating):
            async def search(self, query, num_results=5, *, country=None):
                queries.append(query)
                return await super().search(query, num_results, country=country)

        stub = _Recording("Redding VA Clinic")
        llm = StubLLM({
            "name1_canonical": "Redding VA Clinic",
            "name2_kind": "none",
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        await run_grounded_resolver(
            "no-hint",
            name1="VAMC Redding Visn 21", name2=None,
            street=None, city="Redding", state="CA",
            country="US", country_code="US",
            routing_type="government", domain=None,
            search_client=stub, page_fetcher=stub,
            llm_client=llm, ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )
        assert len(queries) == 1

    @pytest.mark.asyncio
    async def test_a_hint_the_evidence_does_not_support_still_cannot_survive(
        self, test_settings, mock_clients,
    ):
        # The guard the second query does not weaken: pooling more evidence
        # gives the model more to read, never permission to answer from the
        # hint itself.
        from utils.cache import BatchCache

        llm = StubLLM({
            "name1_canonical": "Veterans Affairs Medical Center Redding",
            "name2_kind": "none",
            "per_field_confidence": {"name1": "high"},
            "evidence_index": {"name1": 0},
            "reasoning": "",
        })
        stub = _EvidenceStating("Redding VA Clinic")
        grounded = await run_grounded_resolver(
            "13336733",
            name1="VAMC Redding Visn 21", name2=None,
            street=None, city="Redding", state="CA",
            country="US", country_code="US",
            routing_type="government", domain=None,
            hint="Veterans Affairs Medical Center Redding",
            search_client=stub, page_fetcher=stub,
            llm_client=llm, ror_client=mock_clients["ror"],
            lei_client=mock_clients["lei"],
            cache=BatchCache(), settings=test_settings,
        )
        assert grounded.dropped.get("name1") == "not_in_evidence"
        assert grounded.name1 is None
