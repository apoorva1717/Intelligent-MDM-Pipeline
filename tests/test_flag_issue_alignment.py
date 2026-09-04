"""The join between the pipeline's flag vocabulary and the Issue Catalogue.

Two vocabularies, one record. ``flag_codes`` says what the pipeline is unsure
of and which column it concerns; the catalogue's ``G6-RESOLVE-001`` /
``G7-CONFIRM-001`` / ``G8-VERIFY-001`` say what a DATAshaper reviewer is being
asked to DO about it — supply a value nothing can resolve, confirm a value the
pipeline wrote, establish one it could not. ``FLAG_CODE_ISSUES`` is the join,
and it is only as good as the tokens the pipeline actually emits: a doubt that
ships as prose and not as a code cannot be mapped by anything.

Two of them did exactly that, and this file pins both ends of the fix.

* ``low-confidence-unchanged`` is DERIVED from the record's provenance rather
  than raised by a tier — that is the provenance migration and it stands — but
  the token was withdrawn along with the tier that used to raise it, leaving
  the largest single population the catalogue describes (a record shipped
  exactly as supplied) reachable only by reading a confidence column. It is a
  code again and still cannot be raised.
* ``dept-via-contact`` was reported under ``dept-via-lab`` with "the contact's
  own affiliation" in the reason text. The sentence was right and the
  vocabulary was wrong: nothing counting codes could tell a parent inferred
  from a lab's page from a unit inferred from an individual.

The tests run the whole way through — ``finalise`` renders the flags, the
record is projected onto the shipped output columns, and ``/issues``' own
audit path reads them back — because the two halves are wired together by the
column names and a unit test of either half alone would not notice a break in
the wiring.

Everything here is additive: no test asserts that a flag stopped being raised,
and the reason prose is asserted character-for-character where it moved
between codes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.models import EnrichmentRecord, EnrichmentResult
from api.output_columns import RESPONSE_COLUMNS
from api.routes import _audit_rows
from enrichment import flags
from enrichment.batch_consensus import apply_batch_consensus
from enrichment.issue_detection import FLAG_CODE_ISSUES, UNMAPPED_FLAG_CODES
from enrichment.orchestrator import _init_result, finalise
from enrichment.provenance import deterministic_evidence, registry_evidence
from tests.conftest import seed


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _issues(record: Any) -> list[str]:
    """The Issue-Catalogue codes an audit of *record*'s output row produces.

    The record is projected onto ``RESPONSE_COLUMNS`` — the shipped schema,
    the same one ``/enrich/file`` writes — and handed to ``_audit_rows``, the
    single detection path behind ``/issues`` and ``/issues/json``. So this is
    the real journey a flag code makes to reach a reviewer: through a column
    header, out of the pipeline and back into the audit. Nothing is passed
    from one half to the other in memory.
    """
    read = (
        record.get if callable(getattr(record, "get", None))
        else (lambda field, default=None: getattr(record, field, default))
    )
    headers = list(RESPONSE_COLUMNS.values())
    row = {}
    for field, header in RESPONSE_COLUMNS.items():
        value = read(field)
        if value is None or value == "":
            continue
        row[header] = value
    _, per_row = _audit_rows(headers, [row])
    return per_row[0]


def _input_retained(name1: str, **fields: Any) -> dict:
    """A record whose Name 1 no source could improve on, finalised.

    The write is the real passthrough event, so the record derives
    ``input:low`` the way a live one does and the flag follows from the
    provenance rather than from anything this fixture states.
    """
    result = _init_result(EnrichmentRecord(
        record_id="R1", country="US", name1=name1,
        **{k: v for k, v in fields.items() if not k.startswith("_")},
    ))
    seed(
        result,
        deterministic_evidence(
            "tier2:company-canonical-failed-passthrough",
            producer="input", tier=1,
        ),
        name1_enriched=name1,
    )
    result["_tier1_query_name"] = name1
    for key, value in fields.items():
        if key.startswith("_"):
            result[key] = value
    return finalise(result, time.monotonic())


# ---------------------------------------------------------------------------
# G8 — the derived low
# ---------------------------------------------------------------------------

class TestTheDerivedLowReachesTheCatalogue:
    """`low-confidence-unchanged` -> `G8-VERIFY-001`, through `Flag Codes`.

    The audit could already reach these rows by reading `input:low` off the
    provenance columns, and still does — that is what an export taken while
    the token was withdrawn carries. The point of the token is that a consumer
    reading the vocabulary, which is what the catalogue is defined over, no
    longer has to know about a second column to see the largest population the
    catalogue describes.
    """

    def test_an_input_retained_name1_carries_the_code_and_g8(self):
        out = _input_retained("Apollo Organic Synthesis")

        assert out["name1_provenance"] == "input:low"
        assert flags.LOW_CONFIDENCE_UNCHANGED in out["flag_codes"]
        assert out["flag_low_confidence"] == ["name1"]
        assert out["flag_for_review"] is True
        assert "G8-VERIFY-001" in _issues(out)

    def test_the_clause_a_reviewer_reads_is_unchanged(self):
        """The prose was always rendered — the code is what was missing — so
        the sentence must be byte-identical to the one the derived flag
        produced before it had a token again."""
        out = _input_retained("Apollo Organic Synthesis")
        assert out["flag_reason"] == (
            "Name 1: left exactly as supplied — the canonical form could not "
            "be established with enough confidence to rewrite it; confirm the "
            "value is correct"
        )

    def test_batch_consensus_withdraws_both(self):
        """The pass replaces `name1_enriched`, which falsifies the statement
        the code makes about it, so the code goes and `G8-VERIFY-001` goes
        with it. The withdrawal is `flags.retract`'s and needed no change: it
        re-derives the low from the record's regenerated provenance, and the
        code is rendered from the low."""
        # The demo batch's Coastal trio: three rows at one address spelling
        # one company two ways, so the group converges on the modal spelling
        # and the odd one out is rewritten.
        tampa = {
            "street_cleaned": "500 Bayshore Blvd", "postal_code": "33602",
            "city": "Tampa", "country_region_key": "US",
        }
        rows = [
            EnrichmentResult(
                record_id="r15", name1_enriched="Coastal Diagnostics, Inc.",
                **tampa,
            ),
            EnrichmentResult(
                record_id="r16", name1_enriched="Coastal Diagnostics",
                **tampa, **flags.render({}, low_confidence=["name1"]),
            ),
            EnrichmentResult(
                record_id="r17", name1_enriched="Coastal Diagnostics, Inc.",
                **tampa,
            ),
        ]
        assert flags.LOW_CONFIDENCE_UNCHANGED in rows[1].flag_codes
        assert "G8-VERIFY-001" in _issues(rows[1])

        apply_batch_consensus(rows)

        assert rows[1].name1_enriched == "Coastal Diagnostics, Inc."
        assert rows[1].flag_codes == []
        assert rows[1].flag_low_confidence == []
        assert rows[1].flag_for_review is False
        assert "G8-VERIFY-001" not in _issues(rows[1])

    @staticmethod
    def _registry_name_with_department(name2: str) -> dict:
        """Name 1 settled by GLEIF, Name 2 supplied and left alone — so the
        department slot is the only thing on the record that could carry a
        doubt."""
        result = _init_result(EnrichmentRecord(
            record_id="R1", country="US", name1="Chemspeed Technologies Inc",
            name2=name2,
        ))
        seed(
            result, registry_evidence("gleif", "529900ESWZRHXOW27Z37"),
            name1_enriched="Chemspeed Technologies Inc",
            lei_id="529900ESWZRHXOW27Z37",
        )
        seed(
            result, deterministic_evidence("passthrough"),
            name2_enriched=name2,
        )
        return finalise(result, time.monotonic())

    def test_an_admin_desk_left_as_supplied_carries_neither(self):
        """"Accounts Payable" has no canonical form to establish, so the
        derived low declines it — and the code and the issue decline with it.
        The exemption is `name2_needs_no_verification`, which is the same
        predicate the search-term derivation and the department-domain probe
        use, and this is the test that the four still agree.

        The provenance is unchanged and still says `input:low`: the column
        states what happened, and only the review request is withheld."""
        out = self._registry_name_with_department("Accounts Payable")

        assert out["name2_enriched"] == "Accounts Payable"
        assert out["name2_provenance"] == "input:low"
        assert flags.LOW_CONFIDENCE_UNCHANGED not in out["flag_codes"]
        assert out["flag_low_confidence"] == []
        assert "G8-VERIFY-001" not in _issues(out)

    def test_the_same_slot_holding_a_real_unit_does_carry_both(self):
        """The control. Identical fixture, identical `input:low` provenance —
        a department that HAS an institutional spelling is asked about, which
        is what makes the desk's exemption an exemption and not a hole."""
        out = self._registry_name_with_department("Department of Chemistry")

        assert out["name2_provenance"] == "input:low"
        assert flags.LOW_CONFIDENCE_UNCHANGED in out["flag_codes"]
        assert out["flag_low_confidence"] == ["name2"]
        assert "G8-VERIFY-001" in _issues(out)


# ---------------------------------------------------------------------------
# G7 — a value the pipeline wrote
# ---------------------------------------------------------------------------

class TestTheContactDerivedDepartment:
    """`dept-via-contact` -> `G7-CONFIRM-001`, saying the same sentence.

    Tier 2A's `2A_population` reads the department off the affiliation of the
    person in the Contact column, on a record that stated no department of its
    own. It was reported as `dept-via-lab` carrying "the contact's own
    affiliation" as the detail — right in prose, wrong in the vocabulary.
    """

    @staticmethod
    def _via_contact():
        result = _init_result(EnrichmentRecord(
            record_id="R1", country="US", name1="Stanford University",
            contact="Dr Paul Gaurav Nalam",
        ))
        seed(
            result, registry_evidence("ror", "https://ror.org/00f54p054"),
            name1_enriched="Stanford University",
            ror_id="https://ror.org/00f54p054",
        )
        seed(
            result, deterministic_evidence("tier2a"),
            name2_enriched="Department of Neurosurgery",
        )
        seed(result, _ev_dept_via_person=True, contact_used=True)
        return finalise(result, time.monotonic())

    def test_it_has_its_own_code_and_maps_to_g7(self):
        out = self._via_contact()

        assert flags.DEPT_VIA_CONTACT in out["flag_codes"]
        assert flags.DEPT_VIA_LAB not in out["flag_codes"]
        assert out["flagged_fields"] == ["name2"]
        assert "G7-CONFIRM-001" in _issues(out)

    def test_the_reason_is_the_string_it_always_was(self):
        """Character-for-character what `_DETAILED_REASONS[DEPT_VIA_LAB]`
        rendered for this path with `detail="the contact's own affiliation"`.
        Record 13104799 of the S1 stratum shipped this exact sentence."""
        assert self._via_contact()["flag_reason"] == (
            "Name 2: the department was inferred from the contact's own "
            "affiliation, not read from a stated department — confirm it is "
            "the right unit for this record"
        )

    def test_the_lab_path_keeps_its_own_prose(self):
        """The other half of the split: `dept-via-lab` is now lab-only and
        says what it always said about a parent inferred from a lab's page."""
        assert flags.render({flags.DEPT_VIA_LAB: {"name2", "name3"}})[
            "flag_reason"
        ] == (
            "Name 2 and Name 3: parent department was inferred from the lab's "
            "own page, not read from a stated department — confirm the "
            "department is the right parent for this lab"
        )


class TestTheTwoCodesThatWereMappedToNothing:
    """`relocated-unverified` and `name-states-another-site` are both live
    codes on the demo strata, and both fell through the join.

    Neither is a new finding and neither changes what the record ships; what
    changes is that a reviewer working the Issues column now sees them, in the
    queue whose question they answer.
    """

    def test_a_relocated_slot_asks_for_confirmation(self):
        """Preprocessing MOVED the value into the slot it ships in. That is a
        write nothing vouched for, which is what `G7-CONFIRM-001` is: confirm
        a value the pipeline wrote."""
        result = _init_result(EnrichmentRecord(
            record_id="R1", country="US", name1="Harbor-UCLA Medical Center",
            street2="Supply Chain Oper. Warehouse",
        ))
        seed(
            result, registry_evidence("ror", "https://ror.org/05h4zj272"),
            name1_enriched="Harbor-UCLA Medical Center",
            ror_id="https://ror.org/05h4zj272",
        )
        seed(
            result, deterministic_evidence("passthrough"),
            name2_enriched="Supply Chain Oper. Warehouse",
        )
        seed(result, _slot_origin={"name2": "preprocess:street"})
        out = finalise(result, time.monotonic())

        assert flags.RELOCATED_UNVERIFIED in out["flag_codes"]
        assert "G7-CONFIRM-001" in _issues(out)

    def test_a_record_that_states_two_places_asks_a_human(self):
        """Record 13348125: "Veracyte, Inc. - South San Francisco, CA" on a
        row addressed in Pine Brook, NJ. No automated path can say which
        location the record is FOR — one is a site someone typed into a name
        column, the other is where the mail goes — which is exactly the
        `G6-RESOLVE-001` case."""
        result = _init_result(EnrichmentRecord(
            record_id="13348125", country="US", name1="Veracyte Inc",
            city="Pine Brook", region="NJ",
        ))
        # The real row carried an LEI: the entity is settled, and the only
        # question left is which of the two places the record is for.
        seed(
            result, registry_evidence("gleif", "529900ESWZRHXOW27Z37"),
            name1_enriched="Veracyte, Inc.",
            lei_id="529900ESWZRHXOW27Z37",
        )
        seed(result, _ev_name_site_conflict=(
            "Name 2 states South San Francisco, CA; "
            "record says Pine Brook, NJ"
        ))
        out = finalise(result, time.monotonic())

        assert flags.NAME_STATES_ANOTHER_SITE in out["flag_codes"]
        assert out["flagged_fields"] == ["address"]
        assert "G6-RESOLVE-001" in _issues(out)


# ---------------------------------------------------------------------------
# The join itself
# ---------------------------------------------------------------------------

class TestEveryFlagCodeIsAccountedFor:
    """A code is either mapped to a catalogue code or declared unmapped.

    The failure this closes is silence: `relocated-unverified` and
    `name-states-another-site` were added to the flag vocabulary and simply
    never reached the join, and nothing said so — a flag that maps to nothing
    and a flag that maps to nothing *deliberately* looked identical. They do
    not any more.
    """

    def test_the_vocabulary_is_partitioned(self):
        mapped = set(FLAG_CODE_ISSUES)
        assert set(flags.ALL_CODES) <= mapped | UNMAPPED_FLAG_CODES
        assert not mapped & UNMAPPED_FLAG_CODES

    def test_the_unmapped_five_are_the_declared_five(self):
        """Named individually so that dropping one out of the queue is an
        edit to this list and not a side effect. The reasons are on
        `FLAG_CODE_ISSUES`: two are already reported from record content,
        one is advisory in the pipeline, and two ask a business question no
        catalogue code carries the meaning for."""
        assert UNMAPPED_FLAG_CODES == {
            flags.OVERFLOW,
            flags.NAME3_NOT_DEMOTED,
            flags.REGISTRY_LOCATION_MISMATCH,
            flags.ENTITY_SUPERSEDED,
            flags.SOURCE_CONFLICT,
        }

    def test_the_three_catalogue_codes_are_the_whole_image(self):
        assert set(FLAG_CODE_ISSUES.values()) == {
            "G6-RESOLVE-001", "G7-CONFIRM-001", "G8-VERIFY-001",
        }
