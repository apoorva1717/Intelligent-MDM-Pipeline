"""Provenance Scheme B — ``source:confidence[+witness]``.

The migration these tests pin is a REPRESENTATION change and nothing else:

    Same inputs, same enrichment values. Only the provenance strings and the
    flag derivation may differ.

Four things are tested, in the order they can fail:

1. :func:`compute_confidence` — every row of the confidence table and both
   hard rules, as unit tests on the one function that decides confidence.
2. The grammar validator — every emitted string parses AND satisfies hard
   rules 1–2, and an invalid one raises rather than shipping.
3. One fixture per state, asserting the exact string. These are the migration's
   state table, executable.
4. Behaviour invariance across the 100-row chemspeed batch, against the frozen
   evidence cache: every enrichment value byte-identical, only provenance and
   flag columns moved.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.confidence import (
    LOW,
    PROVENANCE_RE,
    PROVISIONAL,
    VERIFIED,
    WITNESS_DOMAIN,
    WITNESS_LLM,
    WITNESS_REGISTRY,
    WITNESS_WEB,
    WITNESS_WIKIDATA,
    EvidenceSituation,
    ProvenanceGrammarError,
    compute_confidence,
    parse,
    render,
    validate,
    validate_all,
    web_source,
)
from enrichment.page_corroborator import operating_name_provenance
from enrichment.provenance import (
    INPUT_CORROBORATED,
    INPUT_SELF_CONSISTENT,
    Evidence,
    EnrichedRecord,
    derived_scalar,
    deterministic_evidence,
    llm_evidence,
    registry_evidence,
)
from enrichment.wikidata import WITNESS_PROVENANCE
from llm.prompts import TIER3_PROMPT_VERSION


# ═════════════════════════════════════════════════════════════════════════════
# 1 · compute_confidence — the confidence table, row by row
# ═════════════════════════════════════════════════════════════════════════════

class TestTheConfidenceTable:
    """One test per row of the table, and the table is the whole policy.

    The point of routing every lane through one function is that these four
    rows are the complete set of answers the system can give. A fifth answer
    appearing anywhere is a bug in a caller, not a new case.
    """

    def test_registry_authored_is_verified_without_a_witness(self):
        """Row 1. A registry returned the value; nothing else need agree.

        This is the ONLY situation that produces a witness-less `verified`,
        which is what makes hard rule 2 checkable from the string alone.
        """
        assert compute_confidence(
            EvidenceSituation(registry_authored=True, has_source=True),
        ) == (VERIFIED, None)

    def test_a_crosswalked_registry_hit_names_wikidata_as_the_route(self):
        """Row 1, second column. ROR still authored the value — the crosswalk
        is how it was reached, not who said it. Recorded because a hit found
        by following a Wikidata pointer rests on a crowd-edited claim that the
        organisation and the registry entry are the same thing, and a reviewer
        auditing a wrong entity needs to see that step."""
        assert compute_confidence(
            EvidenceSituation(
                registry_authored=True,
                has_source=True,
                via_wikidata_crosswalk=True,
            ),
        ) == (VERIFIED, WITNESS_WIKIDATA)

    def test_an_independent_second_source_verifies_a_non_registry_value(self):
        """Row 2. The witness is REQUIRED, and is returned so it can be named
        in the string — an unattributed `verified` on a non-registry value is
        exactly the claim the old scheme let through."""
        assert compute_confidence(
            EvidenceSituation(has_source=True, witness=WITNESS_WEB),
        ) == (VERIFIED, WITNESS_WEB)

    def test_a_single_uncontradicted_source_is_provisional(self):
        """Row 3. One source, nothing against it, nothing else for it."""
        assert compute_confidence(
            EvidenceSituation(has_source=True),
        ) == (PROVISIONAL, None)

    def test_the_canonical_proposal_case_is_the_only_plus_llm(self):
        """Row 3, second column. The model was asked what the organisation is
        called WITHOUT being shown the record's answer, and returned the
        string the record already held. That is agreement from a second
        source — but from a model, so it can never reach `verified`, and
        `provisional+llm` is the strongest thing that can be said about it."""
        assert compute_confidence(
            EvidenceSituation(
                has_source=True,
                llm_involved=True,
                canonical_proposal_equals_input=True,
            ),
        ) == (PROVISIONAL, WITNESS_LLM)

    def test_no_source_is_low(self):
        """Row 4. The input value stood because nothing came back."""
        assert compute_confidence(EvidenceSituation()) == (LOW, None)

    def test_contradicted_evidence_is_low_even_with_a_registry(self):
        """Row 4 outranks row 1, and has to.

        A registry hit a consistency check refused is not a verified value
        that happens to carry a flag — it is a value the pipeline decided
        against. Hard rule 3 says rejected evidence never appears in
        provenance, and `verified` about a refused match would be the loudest
        possible way to break it.
        """
        assert compute_confidence(
            EvidenceSituation(
                registry_authored=True, has_source=True, contradicted=True,
            ),
        ) == (LOW, None)

    def test_ambiguity_is_low(self):
        """Row 4. A near-tie, a no-match, and the short-name guard all arrive
        here: where the evidence does not distinguish two candidates, the
        honest output is that nothing was established."""
        assert compute_confidence(
            EvidenceSituation(has_source=True, ambiguous=True),
        ) == (LOW, None)


class TestTheTwoHardRules:
    """Enforced in the function, not left to callers to remember."""

    def test_hard_rule_1_an_llm_witness_never_reaches_verified(self):
        """An `llm` witness does not satisfy row 2 — it falls through to
        `provisional`. This is the rule the old scheme's `self_high` band
        quietly broke: a model's assertion about its own output was rendered
        into a band that sorted above `medium` and read as corroboration."""
        confidence, witness = compute_confidence(
            EvidenceSituation(
                has_source=True, witness=WITNESS_LLM, llm_involved=True,
            ),
        )
        assert confidence == PROVISIONAL
        # And the witness is DROPPED, not carried down to `provisional+llm`.
        # The table allows `+llm` in exactly one situation — a canonical
        # proposal that reproduced the input — and a model agreeing with a
        # value it was shown is not that situation. Recording it as a witness
        # anywhere else would make the one meaningful `+llm` unreadable.
        assert witness is None

    def test_hard_rule_1_llm_as_a_source_can_never_be_verified(self):
        with pytest.raises(ProvenanceGrammarError, match="hard rule 1"):
            validate("llm:verified+web")

    def test_hard_rule_1_holds_for_every_witness_spelling(self):
        with pytest.raises(ProvenanceGrammarError, match="hard rule 1"):
            validate("input:verified+llm")

    @pytest.mark.parametrize("source", ["ror", "gleif", "wikidata"])
    def test_hard_rule_2_a_registry_may_be_verified_witness_less(self, source):
        validate(f"{source}:verified")

    @pytest.mark.parametrize(
        "provenance", ["input:verified", "web:acme.com:verified"],
    )
    def test_hard_rule_2_nothing_else_may_be(self, provenance):
        with pytest.raises(ProvenanceGrammarError, match="hard rule 2"):
            validate(provenance)

    def test_compute_confidence_never_emits_an_invalid_pair(self):
        """The two rules, checked against the function rather than against a
        string: every combination of situation flags this function can be
        handed must render to something `validate` accepts.

        Exhaustive over the flag space (2^7 with one enum), because the point
        of a single confidence authority is that it has no unreachable
        corners for a caller to fall into.
        """
        import itertools

        flags = (
            "registry_authored", "via_wikidata_crosswalk", "has_source",
            "contradicted", "ambiguous", "llm_involved",
            "canonical_proposal_equals_input",
        )
        witnesses = (None, *(w for w in (
            WITNESS_WEB, WITNESS_WIKIDATA, WITNESS_LLM,
            WITNESS_REGISTRY, WITNESS_DOMAIN,
        )))
        for combo in itertools.product((False, True), repeat=len(flags)):
            for witness in witnesses:
                situation = EvidenceSituation(
                    witness=witness, **dict(zip(flags, combo)),
                )
                confidence, out_witness = compute_confidence(situation)
                # A registry source is the only witness-less `verified`, so
                # render against the source that situation implies.
                source = "ror" if situation.registry_authored else "input"
                if not situation.registry_authored and out_witness is None:
                    source = "input"
                validate(render(source, confidence, out_witness))


# ═════════════════════════════════════════════════════════════════════════════
# 2 · The grammar
# ═════════════════════════════════════════════════════════════════════════════

class TestTheGrammar:

    @pytest.mark.parametrize("provenance", [
        "input:verified+web",
        "input:provisional+llm",
        "input:low",
        "ror:verified",
        "ror:verified+wikidata",
        "gleif:verified",
        "wikidata:provisional",
        "llm:provisional",
        "web:acme.com:provisional",
        "web:sub.acme.co.uk:verified+domain",
        "web:20visioneers15.com:provisional",
    ])
    def test_every_shape_the_pipeline_emits_parses(self, provenance):
        assert PROVENANCE_RE.match(provenance)
        validate(provenance)

    @pytest.mark.parametrize("provenance", [
        "ror:1:exact",                          # scheme A
        "llm_tier3:3:self_medium",              # scheme A
        "website_resolver:3:rule",              # scheme A
        "web:acme.com:extracted:2026-08-22",    # scheme A
        "wikidata:2:crosswalk",                 # scheme A
        "input:high",                           # not a confidence
        "serp:provisional",                     # not a source
        "input:provisional+serp",               # not a witness
        "web::provisional",                     # no domain
        "input",                                # no confidence
        "",
    ])
    def test_nothing_else_does(self, provenance):
        with pytest.raises(ProvenanceGrammarError):
            validate(provenance)

    def test_a_web_source_is_split_from_the_right_not_the_left(self):
        """`web:{domain}:provisional` carries two colons. The naive
        `split(":")` puts the domain in the confidence slot, which is why the
        grammar ships a parser and every consumer is expected to use it."""
        source, confidence, witness = parse("web:acme.com:provisional")
        assert (source, confidence, witness) == (
            "web:acme.com", "provisional", None,
        )
        assert "acme.com".split(":")[0] != confidence  # the trap, named

    def test_validate_all_ignores_empty_columns(self):
        """A field with no value has nothing to attribute, and a null
        provenance column is the correct output for it — not a violation."""
        validate_all([None, "", "ror:verified"])

    def test_validate_all_raises_on_the_first_invalid_string(self):
        with pytest.raises(ProvenanceGrammarError):
            validate_all(["ror:verified", "llm_tier3:3:self_high"])

    def test_a_domain_is_lowercased_into_the_source(self):
        assert web_source("ACME.com") == "web:acme.com"
        validate(render(web_source("ACME.com"), PROVISIONAL))


# ═════════════════════════════════════════════════════════════════════════════
# 3 · One fixture per state — the migration's state table, executable
# ═════════════════════════════════════════════════════════════════════════════

def _record(**fields) -> EnrichedRecord:
    """A bare record with the scoped keys initialised, for a single write."""
    base = {
        "name1_enriched": None, "name2_enriched": None, "domain": None,
        "ror_id": None, "lei_id": None, "record_type": "unknown",
    }
    base.update(fields)
    return EnrichedRecord(base)


class TestOneFixturePerState:
    """Each test writes ONE value the way the lane that owns it writes it, and
    asserts the exact string. These are the rows of the migration's state
    table; if a mapping changes, exactly one of these fails and names itself.
    """

    def test_registry_hit(self):
        """`ror:1:exact` (and every fuzzy variant) → `ror:verified`.

        The match MODE is gone from the column. It is not gone from the
        record: the event still carries the scale, the score and the rule id.
        """
        record = _record()
        record.write(
            "ror_id", "https://ror.org/042nb2s44",
            registry_evidence("ror", "https://ror.org/042nb2s44", tier=1),
        )
        assert derived_scalar(record.provenance, "ror_id") == "ror:verified"

    def test_a_fuzzy_registry_match_is_the_same_string(self):
        """The whole claim of collapsing the method token: `exact` and
        `fuzzy` were two spellings of "a registry answered", and the
        difference between them was never a difference in confidence."""
        record = _record()
        record.write(
            "lei_id", "LEI0000000000000001",
            registry_evidence(
                "gleif", "LEI0000000000000001", tier=1, score=91.0,
                rule_id="tier1-lei:fuzzy",
            ),
        )
        assert derived_scalar(record.provenance, "lei_id") == "gleif:verified"

    def test_crosswalked_registry_hit(self):
        """A registry hit reached by following a Wikidata pointer. ROR still
        authored the value, so the source is `ror`; `+wikidata` records the
        route, which is the step a reviewer auditing a wrong entity needs."""
        record = _record()
        record.write(
            "ror_id", "https://ror.org/042nb2s44",
            registry_evidence(
                "ror", "https://ror.org/042nb2s44", tier=1,
                rule_id="wikidata:crosswalk-ror",
            ),
        )
        assert derived_scalar(record.provenance, "ror_id") == (
            "ror:verified+wikidata"
        )

    def test_input_verified_by_an_independent_web_witness(self):
        """Fix 2's `unchanged-verified`: the record's own Name 1 stood, and a
        source that is not the record agreed. `input:1:verified` said the
        first half and left the second unattributed."""
        record = _record()
        record.write(
            "name1_enriched", "Aesir Technologies",
            Evidence(
                producer_chain=("input",), tier=1,
                confidence_scale=INPUT_CORROBORATED, confidence_value=1.0,
                evidence_ref={"corroborated_by": "page:aesirtec.com"},
                rule_id="fix2:unchanged-verified",
            ),
        )
        assert derived_scalar(record.provenance, "name1_enriched") == (
            "input:verified+web"
        )

    def test_input_provisional_when_a_proposal_reproduced_it(self):
        """Fix 2's `unchanged-confirmed`. The one `+llm` the table allows, and
        it is `provisional`: a model reproducing a string is agreement from a
        model, and hard rule 1 says that never reaches `verified`."""
        record = _record()
        record.write(
            "name1_enriched", "Admix",
            Evidence(
                producer_chain=("input",), tier=1,
                confidence_scale=INPUT_SELF_CONSISTENT, confidence_value=1.0,
                evidence_ref={"proposal": "Admix", "matched_under": "normalize_key"},
                rule_id="fix2:unchanged-confirmed",
            ),
        )
        assert derived_scalar(record.provenance, "name1_enriched") == (
            "input:provisional+llm"
        )

    def test_input_low_when_nothing_came_back(self):
        """`input:1:rule` → `input:low`. This string IS the retired
        `low-confidence-unchanged` flag code — see
        :class:`TestTheRetiredCodeIsExactlyThisString`."""
        record = _record()
        record.write(
            "name1_enriched", "Aixelo",
            deterministic_evidence(
                "tier2:company-canonical-failed-passthrough",
                producer="input", tier=1,
            ),
        )
        assert derived_scalar(record.provenance, "name1_enriched") == "input:low"

    def test_llm_provisional(self):
        """Every `llm_*` producer and every `self_*` band collapse to one
        string. `self_high` is not preserved and must not be: a confident
        unverifiable claim is the more dangerous case, not the safer one."""
        record = _record()
        record.write(
            "name1_enriched", "Agilent Technologies",
            llm_evidence(
                ("llm_tier3",), tier=3, prompt_version=TIER3_PROMPT_VERSION,
                deployment="test-deployment", self_reported="high",
            ),
        )
        assert derived_scalar(record.provenance, "name1_enriched") == (
            "llm:provisional"
        )

    def test_a_domain_corroborated_by_its_own_page_is_NOT_verified(self):
        """Hard rule 4, and the test the rule exists for.

        A page fetched from `acme.com` that names Acme corroborates nothing
        independent: the site and the page are one evidence system, and
        treating the second read as a second source is how a pipeline talks
        itself into confidence it has not got. `provisional`, and the
        assertion is written as an inequality as well so it fails loudly if
        the collapse ever drifts upward.
        """
        record = _record()
        record.write(
            "domain", "acme.com",
            Evidence(
                producer_chain=("website_resolver",), tier=3,
                confidence_scale="deterministic", confidence_value=1.0,
                evidence_ref={
                    "verified_by": "serp",
                    "source_url": "https://acme.com",
                },
                rule_id="domain-ownership:serp",
            ),
        )
        scalar = derived_scalar(record.provenance, "domain")
        assert scalar == "web:acme.com:provisional"
        assert parse(scalar)[1] != VERIFIED

    def test_a_name_similarity_accepted_domain_is_also_not_verified(self):
        """The other seven `website_resolver:*:*` variants land in the same
        place. `website_resolver:3:exact` read as the strongest domain
        attribution the pipeline could make; it was a string comparison
        against the record's own Name 1, which is one source."""
        record = _record()
        record.write(
            "domain", "aropha.com",
            Evidence(
                producer_chain=("website_resolver",), tier=3,
                confidence_scale="fuzzy_ratio", confidence_value=100.0,
                evidence_ref={"verified_by": "name"},
                rule_id="domain-ownership:name",
            ),
        )
        assert derived_scalar(record.provenance, "domain") == (
            "web:aropha.com:provisional"
        )

    def test_a_domain_verified_by_an_independent_witness(self):
        """The record's own email domain is a different evidence system from
        the candidate site, so it is the second source rule 4 requires."""
        record = _record()
        record.write(
            "domain", "meridianlabs.com",
            Evidence(
                producer_chain=("record_email",), tier=3,
                confidence_scale="deterministic", confidence_value=1.0,
                evidence_ref={
                    "verified_by": "email",
                    "email_domain": "meridianlabs.com",
                    "source_url": None,
                },
                rule_id="domain-ownership:email",
            ),
        )
        assert derived_scalar(record.provenance, "domain") == (
            "web:meridianlabs.com:verified+domain"
        )

    def test_a_registry_stated_website_is_the_registrys_claim(self):
        """The other independent witness for a domain. The producer is the
        registry itself, so this is row 1 of the table rather than row 2 —
        which is why it carries no `+registry`."""
        record = _record()
        record.write(
            "domain", "mit.edu",
            registry_evidence("ror", "https://ror.org/042nb2s44", tier=1),
        )
        assert derived_scalar(record.provenance, "domain") == "ror:verified"

    def test_record_type_from_gleif(self):
        """`classifier:-:rule` named the mechanism and hid the evidence: a
        record type GLEIF settled and one nothing settled shipped the same
        string. The source is now what actually decided it."""
        record = _record()
        record.write(
            "record_type", "company",
            deterministic_evidence(
                "classifier:gleif", producer="classifier",
                evidence_ref={"decided_by": "gleif"},
            ),
        )
        assert derived_scalar(record.provenance, "record_type") == (
            "gleif:verified"
        )

    def test_record_type_unset_is_input_low(self):
        """Nothing set it. `classifier:-:rule` claimed a rule had fired, which
        was true and useless — the rule that fired was the fallthrough."""
        record = _record()
        record.write(
            "record_type", "unknown",
            deterministic_evidence(
                "classifier:unresolved", producer="classifier",
                evidence_ref={"decided_by": "unresolved"},
            ),
        )
        assert derived_scalar(record.provenance, "record_type") == "input:low"

    def test_record_type_from_a_keyword_is_the_inputs_own_claim(self):
        """The name read as a research institution. One source — the record —
        and nothing contradicting it."""
        record = _record()
        record.write(
            "record_type", "research_institution",
            deterministic_evidence(
                "classifier:keyword", producer="classifier",
                evidence_ref={"decided_by": "keyword"},
            ),
        )
        assert derived_scalar(record.provenance, "record_type") == (
            "input:provisional"
        )

    def test_operating_name_from_a_page_read(self):
        """`web:{domain}:extracted:{date}` → `web:{domain}:provisional`.

        `extracted` was a method, and the date decayed — eleven rows of two
        diffed runs once differed in nothing else. The date is on the cache
        entry and on the `operating_name_extracted` trace line.
        """
        assert operating_name_provenance("amaco.com") == (
            "web:amaco.com:provisional"
        )
        validate(operating_name_provenance("amaco.com"))

    def test_the_wikidata_witness_path(self):
        """A Wikidata label with no registry pointer to follow. It can never
        be `verified`: this path is taken precisely when the crosswalk found
        no registry, so there is no second system agreeing."""
        assert WITNESS_PROVENANCE == "wikidata:provisional"
        validate(WITNESS_PROVENANCE)


# ═════════════════════════════════════════════════════════════════════════════
# 4 · Behaviour invariance — the migration's core gate
# ═════════════════════════════════════════════════════════════════════════════

_ARTEFACTS = Path(__file__).resolve().parent.parent / "logs" / "provmig"
_BEFORE = _ARTEFACTS / "pre1.json"
_AFTER = _ARTEFACTS / "post1.json"


@pytest.mark.skipif(
    not (_BEFORE.exists() and _AFTER.exists()),
    reason=(
        "needs the two batch artefacts: run scripts/run_batch.py --frozen "
        "once on each side of the migration into logs/provmig/"
    ),
)
class TestBehaviourInvariance:
    """The claim the whole migration rests on, measured on 100 real records.

    Skipped rather than faked when the artefacts are absent. A gate that
    passes because it had nothing to measure is worse than no gate, and this
    one costs two batch runs against the frozen evidence cache to produce.
    """

    @staticmethod
    def _report() -> dict:
        result = subprocess.run(
            [
                sys.executable, "tools/provenance_invariance.py",
                str(_BEFORE), str(_AFTER), "--json",
                str(_ARTEFACTS / "invariance.json"),
            ],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(
            (_ARTEFACTS / "invariance.json").read_text(encoding="utf-8"),
        )

    def test_no_enrichment_value_moved(self):
        """56 non-provenance, non-flag columns over 100 records. Any
        difference here means something DECIDED differently, and this is not
        the representation migration it claims to be."""
        report = self._report()
        assert report["value_differences"] == [], (
            report["value_differences"][:10]
        )
        assert report["rows"] == 100
        assert report["value_columns_compared"] == 56

    def test_every_provenance_column_actually_migrated(self):
        """The other half of the claim: a migration that changed nothing
        would also pass the test above."""
        report = self._report()
        moved = {
            column: sum(
                e["count"] for e in entries if e["before"] != e["after"]
            )
            for column, entries in report["provenance_mapping"].items()
        }
        assert moved["name1_provenance"] == 100
        assert moved["record_type_provenance"] == 100
        assert moved["ror_id_provenance"] == 11
        assert moved["lei_id_provenance"] == 18

    def test_no_row_changed_its_flag_status(self):
        """The derivation reproduces the flag set exactly. `input:low` on
        Name 1 selects precisely the rows `low-confidence-unchanged` selected,
        which is the evidence that retiring the code lost nothing."""
        report = self._report()
        assert report["flag_status_changes"] == []

    def test_the_reviewers_prose_is_unchanged(self):
        """`flag_reason` is byte-identical on all 100 rows. The code is gone
        from `flag_codes`; the sentence a human reads is not."""
        report = self._report()
        assert report["flag_reason_changes"] == 0

    def test_every_shipped_provenance_string_is_in_the_grammar(self):
        """The finalisation assertion, verified against a real batch rather
        than against constructed records."""
        from enrichment.orchestrator import PROVENANCE_COLUMNS

        rows = json.loads(_AFTER.read_text(encoding="utf-8"))["results"]
        assert len(rows) == 100
        for row in rows:
            validate_all(row.get(column) for column in PROVENANCE_COLUMNS)


class TestTheRetiredCode:
    """`low-confidence-unchanged` is gone from the vocabulary and its meaning
    is not."""

    def test_it_can_never_appear_in_flag_codes_again(self):
        from enrichment.flags import ALL_CODES, LOW_CONFIDENCE_UNCHANGED

        assert LOW_CONFIDENCE_UNCHANGED not in ALL_CODES

    def test_its_prose_survives_for_the_derived_flag(self):
        """"Review UX is unchanged" is a testable claim, not a hope: the
        clause a reviewer reads is still rendered, from the same template, in
        the same position in a multi-part reason."""
        from enrichment.flags import (
            LOW_CONFIDENCE_UNCHANGED, _CODE_ORDER, _REASONS, render,
        )

        assert LOW_CONFIDENCE_UNCHANGED in _CODE_ORDER
        rendered = render({}, low_confidence=["name1"])
        assert rendered["flag_for_review"] is True
        assert rendered["flag_codes"] == []
        assert _REASONS[LOW_CONFIDENCE_UNCHANGED] in rendered["flag_reason"]
        assert rendered["flagged_fields"] == ["name1"]

    def test_the_flag_no_longer_follows_from_the_codes_alone(self):
        """The authorised contract change, stated as a test so a consumer
        reading `flag_for_review == bool(flag_codes)` fails here rather than
        in DATAshaper."""
        from enrichment.flags import render

        assert render({}, low_confidence=[])["flag_for_review"] is False
        assert render({}, low_confidence=["name1"])["flag_for_review"] is True

    def test_a_low_core_field_is_read_off_the_provenance(self):
        from enrichment.flags import low_confidence_core_fields

        record = _record()
        record.write(
            "name1_enriched", "Aixelo",
            deterministic_evidence("passthrough", producer="input", tier=1),
        )
        assert low_confidence_core_fields(record) == ["name1"]

    def test_a_registry_written_name_is_never_low(self):
        """One of the three guards the old rule needed, subsumed rather than
        reimplemented — which is the evidence that the derivation is the same
        decision and not a lookalike."""
        from enrichment.flags import low_confidence_core_fields

        record = _record()
        record.write(
            "name1_enriched", "Massachusetts Institute of Technology",
            registry_evidence("ror", "https://ror.org/042nb2s44", tier=1),
        )
        assert low_confidence_core_fields(record) == []

    def test_domain_and_record_type_do_not_derive_the_flag(self):
        """Core fields are Name 1 and Name 2 only. A record with no settled
        type is not a record with a wrong name in it, and routing those into
        the review queue would take this batch from 55 flagged rows to 96 —
        restoring the "flag on 47 of 50" failure Fix 8 exists to have fixed.
        """
        from enrichment.flags import low_confidence_core_fields

        record = _record()
        record.write(
            "record_type", "unknown",
            deterministic_evidence(
                "classifier:unresolved", producer="classifier",
                evidence_ref={"decided_by": "unresolved"},
            ),
        )
        assert derived_scalar(record.provenance, "record_type") == "input:low"
        assert low_confidence_core_fields(record) == []
