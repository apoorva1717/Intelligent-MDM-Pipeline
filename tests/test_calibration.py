"""The three calibration fixes: witnesses, trigger parity, normalised names.

Every named record below is a GATE, not a special case. Each one is here to
prove that a rule stated in general terms produces the right answer on the row
that motivated it — and the negative gates (Owens Corning, the Kellogg plant,
the two weak-tier matches, the genuine conflict) are here to prove the same
rules did not loosen a guard on the way past.

The three fixes:

**Fix 1 — an independent witness can verify a candidate domain.** A candidate
the ownership guard could tie to nothing is accepted when a system that never
consulted the web path states the same official website (ROR's ``links[]``,
Wikidata's ``P856``) — provenance ``web:{domain}:verified+registry`` /
``+wikidata`` — or, at ``provisional``, when the page served BY the candidate
states the record's own organisation. Name comparison there gains normalised
token-set containment, so a site that trades under the brand while the record
carries brand-plus-division reads as agreement rather than as a different
company.

**Fix 2 — one location-check trigger, for every registry lane.** GLEIF, ROR
and the Wikidata crosswalk classify the strength of their name match with one
function and act on a contradicted address with one function.

**Fix 3 — the cross-source gate compares normalised entities.** GLEIF returns
the formal legal name and ROR returns the brand; "CORTEVA AGRISCIENCE LLC" and
"Corteva" are one organisation agreeing, not two contradicting.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.consistency import (
    apply_cross_source_gate,
    apply_registry_location_check,
    registry_agreement_count,
    registry_location_unconfirmed_count,
    reset_consistency_counters,
)
from enrichment.flags import REGISTRY_LOCATION_MISMATCH, SOURCE_CONFLICT
from enrichment.locality import compare_registry_addresses
from enrichment.registry_match import (
    CROSSWALK_TIER,
    EXACT_TIER,
    FUZZY_TIER,
    LOCATION_ACTION_FLAG,
    LOCATION_ACTION_NONE,
    LOCATION_ACTION_TRACE,
    distinctive_tokens,
    location_check_action,
    name_match_tier,
    names_agree,
    names_agree_by_containment,
)
from utils.domain_resolver import (
    DomainEvidence,
    resolve_domain,
    stated_website_witness,
)

#: The GLEIF name-verification threshold, which is also the page reader's and
#: the cross-source gate's. Named once here so a test that means "the ratio
#: could not have carried this" says so.
_THRESHOLD = 88.0


# ===========================================================================
# Fix 1a — an independent witness verifies a candidate domain
# ===========================================================================

class TestWitnessDomainEquality:
    """A stated official website that IS the candidate is a second source."""

    def test_a_wikidata_official_website_verifies_the_candidate(self):
        """GATE 1 — Johnson & Johnson, candidate `jnj.com`, Wikidata `P856`
        `https://www.jnj.com`.

        Name similarity cannot reach `jnj` from "Johnson & Johnson" and never
        will: the label is an acronym and the comparator is forbidden from
        segmenting concatenated labels. The claim that settles it was already
        fetched — it is on the Wikidata item — and it only ever needed
        carrying as far as the guard.
        """
        decision = resolve_domain(
            "https://www.jnj.com",
            DomainEvidence(
                name1="Johnson & Johnson",
                stated_websites=(("wikidata", "https://www.jnj.com"),),
            ),
            threshold=82, guard_enabled=True,
        )
        assert decision.domain == "jnj.com"
        assert decision.verified_by == "witness_wikidata"
        assert decision.witness == "wikidata"
        assert decision.rejected is False

    def test_a_ror_links_website_verifies_the_candidate(self):
        """The same rule with the other witness. ROR states
        `http://www.thermofisher.com` for the entity the record resolved to;
        the web path found `thermofisher.com`.
        """
        decision = resolve_domain(
            "https://thermofisher.com/order",
            DomainEvidence(
                name1="Fisher Scientific Co. LLC",
                stated_websites=(("registry", "http://www.thermofisher.com"),),
            ),
            threshold=82, guard_enabled=True,
        )
        assert decision.verified_by == "witness_registry"
        assert decision.witness == "registry"

    def test_the_comparison_is_on_the_registrable_stem(self):
        """A deep link, a `www.`, a trailing slash and a scheme are four ways
        of writing one website, and the witness check runs on the value the
        `domain` field would carry."""
        evidence = DomainEvidence(
            stated_websites=(
                ("registry", "http://www.example.co.uk/en/about/index.html"),
            ),
        )
        assert stated_website_witness(evidence, "example.co.uk") == "registry"

    def test_a_stated_website_on_another_domain_is_not_a_witness(self):
        """The guard is not loosened by having a claim to consult: a claim
        naming a DIFFERENT website says nothing about this candidate, and the
        candidate falls through to the conditions it always had."""
        decision = resolve_domain(
            "https://acme-labs.example",
            DomainEvidence(
                name1="Delta Analytical",
                stated_websites=(("wikidata", "https://delta.com"),),
            ),
            threshold=82, guard_enabled=True,
        )
        assert decision.domain is None
        assert decision.rejected is True

    def test_no_witness_and_no_other_condition_is_still_a_rejection(self):
        decision = resolve_domain(
            "https://www.jnj.com",
            DomainEvidence(name1="Johnson & Johnson"),
            threshold=82, guard_enabled=True,
        )
        assert decision.domain is None and decision.rejected is True

    def test_registry_provenance_still_outranks_a_witness(self):
        """Precedence is unchanged above the new condition: a candidate the
        registry SUPPLIED is attributed to the registry, not to itself as its
        own witness."""
        decision = resolve_domain(
            "https://thermofisher.com",
            DomainEvidence(
                registry="ROR",
                stated_websites=(("registry", "https://thermofisher.com"),),
            ),
            threshold=82, guard_enabled=True,
        )
        assert decision.verified_by == "registry"


class TestWitnessProvenance:
    """The witness reaches the provenance column, and hard rule 1 still binds."""

    @pytest.mark.parametrize(
        ("witness", "expected"),
        [
            ("wikidata", "web:jnj.com:verified+wikidata"),
            ("registry", "web:jnj.com:verified+registry"),
        ],
    )
    def test_the_designed_provenance_states_are_produced(self, witness, expected):
        """Both strings existed in the provenance spec and no code path
        produced either of them. These are the two."""
        from enrichment.confidence import (
            EvidenceSituation,
            compute_confidence,
            render,
            web_source,
        )
        from enrichment.provenance import _DOMAIN_WITNESSES

        token = _DOMAIN_WITNESSES[f"witness_{witness}"]
        confidence, resolved = compute_confidence(
            EvidenceSituation(has_source=True, witness=token),
        )
        assert render(web_source("jnj.com"), confidence, resolved) == expected

    def test_a_page_read_is_never_a_witness_for_its_own_domain(self):
        """Hard rule 4, unchanged by the page-identity ownership condition: a
        page fetched from the domain it vouches for is ONE source. The
        condition accepts at `provisional` and can never reach `verified`."""
        from enrichment.provenance import _DOMAIN_WITNESSES

        assert "page" not in _DOMAIN_WITNESSES
        assert "serp" not in _DOMAIN_WITNESSES
        assert "name" not in _DOMAIN_WITNESSES


class TestTheCorroborationOnlyPass:
    """The lane's second entry point: retain `P856`, write nothing.

    The crosswalk lane declines a record that already holds a registry
    identifier, and is right to — a register outranks a wiki. What the record
    still has to gain is the item's stated official website, and this pass
    exists to retain exactly that and nothing else.
    """

    @staticmethod
    def _orch(**counts):
        from config import Settings
        from enrichment.orchestrator import Orchestrator

        return Orchestrator(Settings())

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("result", "why"),
        [
            ({"ror_id": None, "lei_id": None},
             "no registry identity — the crosswalk lane's own population"),
            ({"lei_id": "L1", "_wikidata_qid": "Q1"},
             "the lane already ran on this record"),
            ({"lei_id": "L1", "domain_verified_by": "registry"},
             "the register already supplied the domain"),
        ],
    )
    async def test_the_pass_declines_where_it_can_buy_nothing(self, result, why):
        """Each guard is a call NOT made. The pass is the one place this
        calibration adds a request, so what it declines is the measurement
        that matters."""
        from api.models import EnrichmentRecord

        orch = self._orch()
        called: list[str] = []

        async def _spy(**kw):
            called.append(kw.get("name", ""))
            raise AssertionError("the pass should not have queried")

        orch_module = __import__(
            "enrichment.orchestrator", fromlist=["resolve_wikidata"],
        )
        original = orch_module.resolve_wikidata
        orch_module.resolve_wikidata = _spy
        try:
            record = EnrichmentRecord(record_id="t", name1="Acme Labs", country="US")
            await orch._retain_wikidata_website(
                record, {"name1_enriched": "Acme Labs", **result},
            )
        finally:
            orch_module.resolve_wikidata = original
        assert called == [], why

    @pytest.mark.asyncio
    async def test_the_flag_turns_the_whole_pass_off(self):
        from api.models import EnrichmentRecord

        orch = self._orch()
        object.__setattr__(
            orch._settings, "wikidata_domain_corroboration", False,
        )
        orch_module = __import__(
            "enrichment.orchestrator", fromlist=["resolve_wikidata"],
        )
        original = orch_module.resolve_wikidata

        async def _spy(**kw):
            raise AssertionError("the flag did not gate the call")

        orch_module.resolve_wikidata = _spy
        try:
            await orch._retain_wikidata_website(
                EnrichmentRecord(record_id="t", name1="Acme", country="US"),
                {"lei_id": "L1", "name1_enriched": "Acme"},
            )
        finally:
            orch_module.resolve_wikidata = original

    @pytest.mark.asyncio
    async def test_a_matched_item_leaves_only_the_website_behind(self):
        """The whole contract of the pass, as one assertion: the record it
        touched differs from the record it was handed in exactly one key."""
        from api.models import EnrichmentRecord
        from enrichment.wikidata import MATCHED, WikidataItem, WikidataOutcome

        orch = self._orch()
        orch_module = __import__(
            "enrichment.orchestrator", fromlist=["resolve_wikidata"],
        )
        original = orch_module.resolve_wikidata

        async def _matched(**kw):
            return WikidataOutcome(
                outcome=MATCHED, query=kw.get("name", ""),
                item=WikidataItem(
                    qid="Q333718", label="Johnson & Johnson",
                    website="https://www.jnj.com",
                    lei_id="549300G0CFPGEF6X2043",
                ),
                name_score=100.0,
            )

        before = {"lei_id": "549300G0CFPGEF6X2043", "name1_enriched": "Johnson & Johnson"}
        after = dict(before)
        orch_module.resolve_wikidata = _matched
        try:
            await orch._retain_wikidata_website(
                EnrichmentRecord(
                    record_id="13017857", name1="Johnson & Johnson", country="US",
                ),
                after,
            )
        finally:
            orch_module.resolve_wikidata = original

        assert set(after) - set(before) == {"_wikidata_website"}
        assert after["_wikidata_website"] == "https://www.jnj.com"
        # Not a name, not an operating_name, not a ror_id. A wiki label is not
        # a customer master's source of truth and this pass does not make it
        # one.
        assert after["name1_enriched"] == "Johnson & Johnson"
        assert "operating_name" not in after
        assert "ror_id" not in after


# ===========================================================================
# Fix 1b — containment-aware name comparison
# ===========================================================================

class TestContainmentComparator:
    """One function, used by the page reader and the cross-source gate."""

    @pytest.mark.parametrize(
        ("record", "other"),
        [
            # GATE 2 — the page states the brand, the record the division.
            ("Stryker Orthopaedics Corp", "Stryker"),
            # GATE 3 — the same shape with a hyphenated head.
            ("Kla-Tencor Corp", "KLA"),
            # GATE 9 — GLEIF's legal name against ROR's brand.
            ("CORTEVA AGRISCIENCE LLC", "Corteva"),
            ("THE CHEMOURS COMPANY", "Chemours"),
            ("ABBOTT LABORATORIES", "Abbott"),
        ],
    )
    def test_brand_and_brand_plus_division_agree(self, record, other):
        assert names_agree_by_containment(record, other) is True

    @pytest.mark.parametrize(
        ("record", "other"),
        [
            ("Stryker Orthopaedics Corp", "Stryker"),
            ("CORTEVA AGRISCIENCE LLC", "Corteva"),
        ],
    )
    def test_the_rule_is_direction_agnostic(self, record, other):
        """Which side carries the division is an accident of which source
        answered, and the rule must not depend on it."""
        assert names_agree_by_containment(other, record) is True
        assert names_agree_by_containment(record, other) is True

    @pytest.mark.parametrize(
        ("record", "other", "why"),
        [
            # GATE 4 — the distinctive HEAD token is missing from the subset.
            ("Owens Corning Sales LLC", "Corning Incorporated",
             "Corning Incorporated is a different company"),
            ("Owens Corning Sales LLC", "Corning",
             "the bare brand, same reason"),
            # The same shape one level up: a subsidiary's name is contained in
            # its parent's and they are still two entities.
            ("Fisher Scientific Co. LLC", "Thermo Fisher Scientific",
             "the head token 'thermo' is absent"),
            # GATE 5 — nothing distinctive is shared at all.
            ("KELLOGG CO BATTLE CREEK MI PLANT", "Clara's Restaurant Group",
             "an unrelated local business"),
            ("Kellogg North America", "Kellogg Community College",
             "neither token set contains the other"),
            ("BIC CORPORATION", "Centene Corporation", "two companies"),
        ],
    )
    def test_a_name_that_drops_the_head_token_does_not_agree(
        self, record, other, why,
    ):
        assert names_agree_by_containment(record, other) is False, why

    def test_a_name_made_only_of_generic_words_agrees_with_nothing(self):
        """"The Company Group" contains no identity. "Contained in
        everything" must not read as "agrees with everything"."""
        assert distinctive_tokens("The Company Group Ltd") == []
        assert names_agree_by_containment("The Company Group Ltd", "Acme") is False

    def test_containment_does_not_replace_the_ratio(self):
        """The ratio is still the primary test and is untouched: a pair the
        ratio accepts is accepted whether or not either contains the other."""
        assert names_agree("ADVANSIX INC.", "AdvanSix Inc", _THRESHOLD) is True

    def test_containment_is_what_carries_the_gate_pairs(self):
        """And these are pairs the ratio alone REFUSES — the fix is the
        containment rule, not a lowered threshold."""
        from enrichment.tier1_lei import _name_match_score

        for record, other in (
            ("Stryker Orthopaedics Corp", "Stryker"),
            ("CORTEVA AGRISCIENCE LLC", "Corteva"),
        ):
            assert _name_match_score(record, other) < _THRESHOLD
            assert names_agree(record, other, _THRESHOLD) is True

    def test_containment_never_makes_a_match_exact_tier(self):
        """The tier classifier asks a stricter question — is this the same
        string typed twice — and containment must not answer it. A brand
        against a brand-plus-division is a FUZZY match whose address check
        stays armed."""
        assert name_match_tier(
            ["Stryker Orthopaedics Corp"], ["Stryker"],
        ) == FUZZY_TIER


class TestContainmentInThePageReader:
    """The comparator, in the place Fix 1b names."""

    @staticmethod
    def _statement(name: str, **kw) -> Any:
        from enrichment.page_corroborator import PageStatement

        return PageStatement(stated_org_name=name, **kw)

    def test_a_page_stating_the_brand_corroborates_the_record(self):
        """GATE 2 — stryker.com states "Stryker" and no address."""
        from enrichment.page_corroborator import CORROBORATED, compare

        outcome, score, location, _, _ = compare(
            self._statement("Stryker"),
            name1="Stryker Orthopaedics", threshold=_THRESHOLD,
            city="MAHWAH", region="NJ", country="US",
        )
        assert outcome == CORROBORATED
        assert score < _THRESHOLD          # the ratio did not carry it
        assert location == "neutral"       # and neither did the address

    def test_a_page_naming_a_different_organisation_is_still_a_mismatch(self):
        """GATE 5 — battlecreekmich.com states "Clara's Restaurant Group"."""
        from enrichment.page_corroborator import NAME_MISMATCH, compare

        outcome, _, _, _, _ = compare(
            self._statement("Clara's Restaurant Group"),
            name1="KELLOGG CO BATTLE CREEK MI PLANT", threshold=_THRESHOLD,
            city="BATTLE CREEK", region="MI", country="US",
        )
        assert outcome == NAME_MISMATCH

    def test_a_location_contradiction_still_blocks_at_region_scope(self):
        """Containment fixes the NAME comparison and nothing else. A page that
        names the organisation but places it in another STATE is still
        `contradicted`, and the domain decision reads that."""
        from enrichment.page_corroborator import (
            CONTRADICTED,
            compare,
            page_identifies_record,
        )
        from enrichment.page_corroborator import Corroboration

        outcome, score, location, detail, scope = compare(
            self._statement("Stryker", stated_region="Michigan"),
            name1="Stryker Orthopaedics", threshold=_THRESHOLD,
            city="MAHWAH", region="NJ", country="US",
        )
        assert (outcome, location, scope) == (CONTRADICTED, "contradicted", "region")
        assert page_identifies_record(Corroboration(
            outcome=outcome, domain="stryker.com", name_consistent=True,
            location=location, location_scope=scope,
        )) is False

    def test_a_city_difference_inside_an_agreeing_region_does_not_block(self):
        """GATE 3 — KLA states Milpitas, California; the record says Santa
        Clara, CA. A plant and a head office in one state are one company,
        which is the granularity rule the withdrawal side already applies."""
        from enrichment.page_corroborator import (
            CONTRADICTED,
            Corroboration,
            compare,
            page_identifies_record,
        )

        outcome, _, location, _, scope = compare(
            self._statement(
                "KLA", stated_city="Milpitas", stated_region="California",
            ),
            name1="KLA-Tencor Corporation", threshold=_THRESHOLD,
            city="SANTA CLARA", region="CA", country="US",
        )
        assert (outcome, scope) == (CONTRADICTED, "city")
        assert page_identifies_record(Corroboration(
            outcome=outcome, domain="kla.com", name_consistent=True,
            location=location, location_scope=scope,
        )) is True


# ===========================================================================
# Fix 1c — a fetch we were refused is not evidence
# ===========================================================================

class TestFetchBlockedIsNotEvidence:
    """Confirmed, not changed: a block counts for nothing, in either
    direction, at the accept site as well as at the withdraw site."""

    @pytest.mark.parametrize("outcome", ["fetch_unavailable", "parked", "no_identity"])
    def test_a_blocked_fetch_can_never_accept_a_domain(self, outcome):
        """GATE 4, as it actually occurs: corning.com answers 403 on the
        Owens Corning record. "We could not look" must not read as "the site
        agrees"."""
        from enrichment.page_corroborator import Corroboration, page_identifies_record

        assert page_identifies_record(Corroboration(
            outcome=outcome, domain="corning.com",
        )) is False

    def test_a_blocked_fetch_can_never_withdraw_a_domain(self):
        from enrichment.page_corroborator import Corroboration, location_decides

        assert location_decides(Corroboration(
            outcome="fetch_unavailable", domain="corning.com",
        )) is False

    def test_a_challenge_page_is_recorded_as_unavailable_not_as_content(self):
        """The server answered 200, so the status code alone does not reveal
        that we were refused."""
        from enrichment.page_corroborator import _looks_challenged

        assert _looks_challenged("Just a moment...", "") is True
        assert _looks_challenged("", "Verify you are human") is True
        assert _looks_challenged("Stryker", "We are Stryker.") is False


# ===========================================================================
# Fix 2 — one trigger, every registry lane
# ===========================================================================

class TestTheSharedTrigger:
    """`location_check_action` is the rule; no lane carries its own copy."""

    @pytest.mark.parametrize(
        ("verdict", "tier", "expected"),
        [
            ("contradicted", EXACT_TIER, LOCATION_ACTION_TRACE),
            ("contradicted", FUZZY_TIER, LOCATION_ACTION_FLAG),
            ("contradicted", CROSSWALK_TIER, LOCATION_ACTION_FLAG),
            ("contradicted", "short_name", LOCATION_ACTION_FLAG),
            # Silence about the strength of a match is not a claim that it
            # was strong.
            ("contradicted", None, LOCATION_ACTION_FLAG),
            ("consistent", FUZZY_TIER, LOCATION_ACTION_NONE),
            ("neutral", EXACT_TIER, LOCATION_ACTION_NONE),
            (None, None, LOCATION_ACTION_NONE),
        ],
    )
    def test_the_rule_is_a_pure_function_of_verdict_and_tier(
        self, verdict, tier, expected,
    ):
        assert location_check_action(verdict, tier) == expected

    @pytest.mark.parametrize("registry", ["GLEIF", "ROR"])
    def test_every_lane_reaches_the_same_answer(self, registry):
        """The same (verdict, tier) pair on either lane's `_src_locality_*`
        key produces the same action — which is what "one function" means
        operationally."""
        for tier, flagged in ((EXACT_TIER, False), (FUZZY_TIER, True)):
            reset_consistency_counters()
            result: dict[str, Any] = {"record_id": "t"}
            key = "_src_locality_gleif" if registry == "GLEIF" else "_src_locality_ror"
            result[key] = {
                "verdict": "contradicted",
                "detail": "states region MA; record says FL",
                "scope": "region", "notes": [], "tier": tier,
            }
            line = apply_registry_location_check(result)
            assert line is not None
            if flagged:
                assert line["step"] == "registry_location_mismatch"
                assert result.get("_ev_registry_location_mismatch")
            else:
                assert line["step"] == "registry_location_unconfirmed"
                assert result.get("_ev_registry_location_mismatch") is None
                assert registry_location_unconfirmed_count() == 1

    def test_the_crosswalk_lane_is_checked_like_the_others(self):
        """A crosswalk followed a pointer, not a name — it can never be exact
        tier, so a contradicted address on that lane always flags. It is the
        one route that picks an organisation without reading its name, so it
        is the last route whose address should go unchecked."""
        reset_consistency_counters()
        result: dict[str, Any] = {
            "record_id": "t",
            "_src_locality_ror": {
                "verdict": "contradicted", "detail": "states region IL",
                "scope": "region", "notes": [], "tier": CROSSWALK_TIER,
            },
        }
        assert apply_registry_location_check(result)["step"] == (
            "registry_location_mismatch"
        )


class TestTheAddressSetEveryLaneCompares:
    """Each lane hands the comparator everything its registry publishes."""

    def test_ror_publishes_a_list_and_all_of_it_is_compared(self):
        """ROR's `locations[]` is a list. Truncating it to `locations[0]` is
        how a record naming a real site of the organisation gets reported as
        contradicting "the" registered address — the same defect GLEIF's two
        address blocks were given to the comparator to fix."""
        from enrichment.tier1_ror import _extract_org_fields

        fields = _extract_org_fields({
            "id": "https://ror.org/0test0001",
            "names": [{"types": ["ror_display"], "value": "Acme Research"}],
            "locations": [
                {"geonames_details": {
                    "name": "Waltham", "country_subdivision_name": "Massachusetts",
                    "country_name": "United States",
                }},
                {"geonames_details": {
                    "name": "Jacksonville", "country_subdivision_name": "Florida",
                    "country_name": "United States",
                }},
            ],
        })
        assert len(fields["addresses"]) == 2
        # The flat keys still name the PRIMARY location — they are output
        # fields, and only the comparison reads the set.
        assert (fields["city"], fields["region"]) == ("Waltham", "Massachusetts")

        verdict, _, _, _ = compare_registry_addresses(
            fields["addresses"], city="JACKSONVILLE", region="FL",
            country="United States",
        )
        assert verdict == "consistent"

    def test_a_single_location_behaves_exactly_as_before(self):
        from enrichment.tier1_ror import _extract_org_fields

        fields = _extract_org_fields({
            "id": "https://ror.org/03x1ewr52",
            "names": [{
                "types": ["ror_display"],
                "value": "Thermo Fisher Scientific (United States)",
            }],
            "locations": [{"geonames_details": {
                "name": "Waltham", "country_subdivision_name": "Massachusetts",
                "country_name": "United States",
            }}],
        })
        assert len(fields["addresses"]) == 1
        verdict, _, scope, _ = compare_registry_addresses(
            fields["addresses"], city="JACKSONVILLE", region="FL",
            country="United States",
        )
        assert (verdict, scope) == ("contradicted", "region")

    def test_duplicate_locations_are_not_double_counted(self):
        from enrichment.tier1_ror import _extract_org_fields

        geo = {"geonames_details": {
            "name": "Waltham", "country_subdivision_name": "Massachusetts",
            "country_name": "United States",
        }}
        fields = _extract_org_fields({
            "id": "https://ror.org/0test0002",
            "names": [{"types": ["ror_display"], "value": "Acme"}],
            "locations": [geo, dict(geo)],
        })
        assert len(fields["addresses"]) == 1


class TestTierClassificationParity:
    """Both lanes rank the record against every name their registry
    publishes, not against the display name alone."""

    def test_ror_counts_every_name_variant(self):
        """A record stating an alias verbatim has named the organisation as
        surely as one stating the display name."""
        assert name_match_tier(
            ["Aurora University"],
            ["Aurora College", "Aurora University"],
        ) == EXACT_TIER

    def test_gleif_counts_its_other_names(self):
        from enrichment.tier1_lei import _record_fields

        fields = _record_fields({
            "id": "LEI00TEST0000000001",
            "attributes": {"entity": {
                "legalName": {"name": "ACME HOLDINGS INTERNATIONAL LLC"},
                "otherNames": [{"name": "Acme Labs", "type": "TRADING_OR_OPERATING_NAME"}],
                "legalAddress": {"city": "WILMINGTON", "region": "US-DE", "country": "US"},
                "status": "ACTIVE",
            }},
        })
        assert fields["entity_names"] == [
            "ACME HOLDINGS INTERNATIONAL LLC", "Acme Labs",
        ]
        assert name_match_tier(["Acme Labs"], fields["entity_names"]) == EXACT_TIER

    def test_the_thermo_fisher_gate(self):
        """GATE 6 — a record naming "Thermo Fisher Scientific" at its Florida
        site against the real cached ROR entity (Waltham, MA). The name
        identified the organisation; the address disagreeing is a fact about
        the organisation's geography, not a doubt about which one it is.
        Trace, no flag.
        """
        reset_consistency_counters()
        tier = name_match_tier(
            ["Thermo Fisher Scientific"], ["Thermo Fisher Scientific"],
        )
        verdict, detail, scope, notes = compare_registry_addresses(
            [{"kind": "registered", "city": "Waltham",
              "region": "Massachusetts", "country": "United States"}],
            city="JACKSONVILLE", region="FL", country="United States",
        )
        assert tier == EXACT_TIER and verdict == "contradicted"

        result: dict[str, Any] = {
            "record_id": "13017576",
            "_src_locality_ror": {
                "verdict": verdict, "detail": detail, "scope": scope,
                "notes": notes, "tier": tier,
            },
        }
        line = apply_registry_location_check(result)
        assert line["step"] == "registry_location_unconfirmed"
        assert result.get("_ev_registry_location_mismatch") is None
        assert registry_location_unconfirmed_count() == 1

    @pytest.mark.parametrize(
        ("record_id", "query", "official", "record_region", "registry_region"),
        [
            # GATE 7 — ROR answered "Cargill Foundation" for "Cargill
            # Incorporated", Minnesota against an Ohio record.
            ("13056499", "Cargill Incorporated", "Cargill Foundation",
             "OH", "Minnesota"),
            # GATE 8 — GLEIF answered "Jansen LLC" for "Janssen", Oregon
            # against a New Jersey record.
            ("13018096", "Janssen", "Jansen LLC", "NJ", "OR"),
        ],
    )
    def test_the_weak_tier_catches_still_fire(
        self, record_id, query, official, record_region, registry_region,
    ):
        """The two genuine catches, and the half of this calibration that is
        not allowed to change.

        Note what the trigger does and does not read. Whether the two names
        AGREE is a different question from whether the record stated the
        registry's name VERBATIM, and only the second one suppresses the
        advisory. "Cargill Incorporated" against "Cargill Foundation" is a
        containment agreement and "Janssen" against "Jansen LLC" clears the
        ratio — and neither is the record naming the registry's entity, so
        both stay weak-tier and both keep their flag.
        """
        tier = name_match_tier([query], [official])
        assert tier == FUZZY_TIER

        verdict, detail, scope, notes = compare_registry_addresses(
            [{"kind": "registered", "city": "Somewhere",
              "region": registry_region, "country": "United States"}],
            city="Elsewhere", region=record_region, country="United States",
        )
        assert verdict == "contradicted"

        reset_consistency_counters()
        result: dict[str, Any] = {
            "record_id": record_id,
            "_src_locality_ror": {
                "verdict": verdict, "detail": detail, "scope": scope,
                "notes": notes, "tier": tier,
            },
        }
        assert apply_registry_location_check(result)["step"] == (
            "registry_location_mismatch"
        )
        assert registry_location_unconfirmed_count() == 0


# ===========================================================================
# Fix 3 — the cross-source gate compares normalised entities
# ===========================================================================

class TestCrossSourceNormalisedComparison:

    @staticmethod
    def _result(**kw):
        """A working record, scoped fields and all — `_resolve` nulls a losing
        source's identifier through the attributed write path, so a bare dict
        would not exercise what the gate actually does."""
        from enrichment.provenance import EnrichedRecord

        base: dict[str, Any] = {
            "record_id": "t", "name1_original": "", "domain": None,
            "ror_id": None, "lei_id": None,
        }
        base.update(kw)
        return EnrichedRecord(base)

    def test_the_corteva_gate(self):
        """GATE 9 — GLEIF "CORTEVA AGRISCIENCE LLC", ROR "Corteva". One
        organisation, two registers, two conventions for writing its name.
        No conflict, both identifiers kept, and the agreement recorded."""
        reset_consistency_counters()
        result = self._result(
            name1_original="Corteva Agriscience",
            _src_name_gleif="CORTEVA AGRISCIENCE LLC",
            _src_name_ror="Corteva",
            ror_id="https://ror.org/02pm1jf23",
            lei_id="LTQMAOOXIYXCDZKIWJ44",
        )
        assert apply_cross_source_gate(result, _THRESHOLD) is None
        assert result["ror_id"] == "https://ror.org/02pm1jf23"
        assert result["lei_id"] == "LTQMAOOXIYXCDZKIWJ44"
        assert registry_agreement_count() == 1
        assert "CORTEVA AGRISCIENCE LLC" in result["_ev_registry_agreement"]

    @pytest.mark.parametrize(
        ("gleif", "ror"),
        [
            ("THE CHEMOURS COMPANY", "Chemours"),
            ("ABBOTT LABORATORIES", "Abbott"),
        ],
    )
    def test_the_same_shape_on_the_other_gate_rows(self, gleif, ror):
        reset_consistency_counters()
        result = self._result(
            name1_original=gleif.title(),
            _src_name_gleif=gleif, _src_name_ror=ror,
            ror_id="https://ror.org/0test0001", lei_id="LEI00TEST0000000001",
        )
        assert apply_cross_source_gate(result, _THRESHOLD) is None
        assert result["ror_id"] and result["lei_id"]

    def test_a_genuine_conflict_is_unchanged(self):
        """GATE 10 — BIC Corp: GLEIF returned the right company and ROR
        returned Centene. Normalisation changes nothing about that pair, and
        the handling is byte-for-byte what it was."""
        reset_consistency_counters()
        result = self._result(
            name1_original="BIC Corp",
            _src_name_gleif="BIC CORPORATION",
            _src_name_ror="Centene Corporation",
            ror_id="https://ror.org/0centene1",
            lei_id="LEI00BIC0000000000001",
            domain="centene.com", domain_verified_by="registry",
            website_url="https://centene.com",
        )
        action = apply_cross_source_gate(result, _THRESHOLD)
        assert action is not None
        assert action["kept"] == "GLEIF" and action["dropped"] == "ROR"
        assert result["ror_id"] is None
        assert result["lei_id"] == "LEI00BIC0000000000001"
        assert "ror_id" in action["nulled_fields"]
        assert registry_agreement_count() == 0
        assert result["_ev_source_conflict"]

    def test_an_absent_source_is_not_an_agreement_either(self):
        """One source is not two. Absence has never been conflict and it must
        not become confirmation."""
        reset_consistency_counters()
        result = self._result(
            name1_original="Acme Labs",
            _src_name_gleif="ACME LABORATORIES LLC",
            lei_id="LEI00TEST0000000001",
        )
        assert apply_cross_source_gate(result, _THRESHOLD) is None
        assert registry_agreement_count() == 0

    def test_the_flag_codes_are_unchanged_by_an_agreement(self):
        """An agreement is a finding, not a triage signal: it raises nothing
        and it reaches the reviewer through the trace."""
        from enrichment.flags import compute_flags

        result = self._result(
            name1_enriched="Corteva Agriscience LLC",
            _ev_registry_agreement="GLEIF … and ROR …",
            flag_codes=[],
        )
        compute_flags(result)
        assert SOURCE_CONFLICT not in result["flag_codes"]
        assert REGISTRY_LOCATION_MISMATCH not in result["flag_codes"]
        # And it is dropped before the record is validated, so an agreement
        # never reaches the exported row at all.
        assert "_ev_registry_agreement" not in result


# ===========================================================================
# The genericity requirement, as a test
# ===========================================================================

class TestNoNamedRecordReachesNonTestCode:
    """Every rule above is a rule of a comparator. None of them may be a
    lookup keyed on a company, a domain, or anything in the evidence files —
    the same rules run unchanged against five evaluation strata, and one that
    only helps large corporates is mis-scoped.

    What is checked, and why it is checked this way. A gate name may appear in
    a DOCSTRING or a COMMENT: the rules in these modules are derived from
    measured cases, and a rule whose derivation is unrecoverable is worse than
    one that names the row it was measured on — the modules already document
    themselves that way throughout. A gate name may never appear anywhere the
    code can COMPUTE with it: not as an identifier, not in a literal the
    program evaluates, not in a comparison. So the check walks the AST rather
    than grepping, and every string constant that is not a docstring is an
    offence.
    """

    #: Every organisation and domain named in the gates.
    _GATE_NAMES: tuple[str, ...] = (
        "johnson", "jnj", "stryker", "kla", "tencor", "corteva", "chemours",
        "abbott", "thermo", "fisher", "cargill", "jansen", "janssen",
        "kellogg", "owens", "corning", "battlecreek", "agriscience",
    )

    #: The non-test modules this calibration changed.
    _CHANGED: tuple[str, ...] = (
        "enrichment/registry_match.py",
        "enrichment/page_corroborator.py",
        "enrichment/consistency.py",
        "enrichment/locality.py",
        "enrichment/tier1_ror.py",
        "enrichment/tier1_lei.py",
        "enrichment/provenance.py",
        "enrichment/flags.py",
        "enrichment/orchestrator.py",
        "utils/domain_resolver.py",
        "api/models.py",
    )

    @staticmethod
    def _docstring_nodes(tree) -> set:
        """The `Constant` nodes that are module / class / function
        docstrings — the only string constants exempt from the check."""
        import ast

        exempt = set()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef,
                       ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            body = getattr(node, "body", None) or []
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                exempt.add(id(body[0].value))
        return exempt

    #: A gate name at the START of a word. Plain substring matching reports
    #: "kla" inside "Oklahoma" — the US region map is full of place names —
    #: and a rule that cannot tell a state from a company is not a check.
    #: Anchoring on the left only, so a domain label that runs the name into
    #: something else ("battlecreekmich.com", "thermofisher.com") is still
    #: caught.
    @classmethod
    def _hits(cls, haystack: str) -> list[str]:
        import re

        return [
            name for name in cls._GATE_NAMES
            if re.search(rf"(?<![a-z0-9]){re.escape(name)}", haystack)
        ]

    def test_no_gate_name_is_computable_in_the_changed_modules(self):
        import ast

        root = Path(__file__).resolve().parent.parent
        offences: list[str] = []
        for relative in self._CHANGED:
            source = (root / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            exempt = self._docstring_nodes(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in exempt:
                        continue
                    haystack = node.value.lower()
                elif isinstance(node, ast.Name):
                    haystack = node.id.lower()
                elif isinstance(node, ast.Attribute):
                    haystack = node.attr.lower()
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)):
                    haystack = node.name.lower()
                else:
                    continue
                for name in self._hits(haystack):
                    offences.append(
                        f"{relative}:{getattr(node, 'lineno', '?')}: "
                        f"{name!r} in {haystack[:60]!r}"
                    )
        assert not offences, (
            "a gate name reached executable code: " + "; ".join(offences)
        )

    def test_the_gate_names_would_be_found_if_they_were_there(self):
        """The check above passes trivially if the walk is broken. This one
        fails it on purpose, against a module that DOES name a gate in a
        computable position — this test file itself."""
        import ast

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        exempt = self._docstring_nodes(tree)
        found = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in exempt
            and self._hits(node.value.lower())
        ]
        assert found, "the AST walk found no gate names in the gate tests"
