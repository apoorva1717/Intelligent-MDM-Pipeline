# Known failures

The tests that fail on a clean tree. **A gate asserts the failing set is exactly this
manifest** — not a count, the set. A run with seven of these failing and one other test
failing is a regression, and a count alone cannot say so.

    12 failed, 3906 passed, 12 skipped, 2 xfailed

Established by running the full suite at nine commits — `d3a3cfc`, `f57782f`, `2125ad2`,
`327ee53`, `e31b53b`, `f292bfa`, `e396722`, `a17a2e0`, `96dd528`. **The same eight fail at
every one of them**, including `327ee53` itself, so none was introduced by any commit in
the range the evaluation artefacts span and none is a rebase artifact of it. They predate
every artefact on this machine. Presence or absence of `.env` does not change the set.

## The manifest

| test | cluster |
|---|---|
| `test_orchestrator.py::TestOrchestrator::test_tier1_to_tier2a_verification` | Tier 2A gate |
| `test_orchestrator.py::TestTier2AVerificationMergeLayer::test_low_score_medium_confidence_keeps_record_value` | Tier 2A gate |
| `test_orchestrator.py::TestTier2AVerificationMergeLayer::test_low_score_high_confidence_overwrites_record_value` | Tier 2A gate |
| `test_orchestrator.py::TestOrchestrator::test_web_search_fallback_for_name1` | mock-path classification |
| `test_orchestrator.py::TestOrchestrator::test_web_search_determines_record_type` | mock-path classification |
| `test_orchestrator.py::TestOrchestrator::test_tier1_full_resolution` | single |
| `test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing` | single |
| `test_dedup.py::test_conflicting_ror_not_merged_verdict_guard` | conflicting-registry merge guard |
| `test_dedup.py::test_conflicting_lei_not_merged_verdict_guard` | conflicting-registry merge guard |
| `test_dedup.py::test_no_signal_pair_not_nominated_reason_empty_ok` | dedup single |
| `test_dedup.py::test_mode_b_canonical_assignment_produces_correct_clusters` | dedup single |
| `test_dedup.py::test_route_cluster_block_identical_rows` | dedup single |

## The five clusters

* **Tier 2A gate narrowed, pre-history — 3 tests.** All three assert
  `tier2_mode == "2A_verification"` and get `None`. `run_tier2a` still exists and is still
  called, so this is the contact-lookup gate having narrowed rather than the lane having
  been withdrawn. One investigation, not three.
* **Mock-path classification drift — 2 tests.** Both assert `record_type == "company"` and
  get `"unknown"`: the classifier no longer settles a company on the mock search path.
* **Orchestrator / parity singles — 2 tests.** `test_tier1_full_resolution` (`confidence`
  `medium` where `high` is asserted); and
  `test_department_in_a_lower_slot_is_not_reported_missing` (`G2-NAME-012` is now raised
  for a department in a lower slot). The issues-compare route was the third of these and
  has since started passing — see the re-pin note below.

* **Conflicting-registry merge guard — 2 tests.** `test_conflicting_ror_…` and
  `test_conflicting_lei_…` both fail at `assert not co_clustered`: two rows with
  conflicting ROR / LEI ids ARE being co-merged into one cluster, where the guard should
  hold them apart and demote both to `manual_review`. One investigation, not two — and the
  one with the sharpest consequence here, since it is a wrong-entity merge.
* **Dedup singles — 3 tests.** `test_no_signal_pair_not_nominated_reason_empty_ok`
  (`reasoning` carries `'mode-a distinct s1'` where `None` is asserted — a no-signal pair
  is being given a rationale); `test_mode_b_canonical_assignment_produces_correct_clusters`
  (`ScriptedLLM.calls == 1`, `>= 2` asserted — mode B is making fewer LLM calls than the
  canonical-assignment path expects); `test_route_cluster_block_identical_rows` (not every
  row of an identical-row block comes back `routing == "cluster"`).

None is a flake — each is a stable assertion failure at every commit tested.

## Re-pin, 2026-09-08 (`8ba0c75`)

The manifest above replaces the original eight. Two independent drifts had accumulated
since it was written, both verified against a clean `git worktree` at `7af631c` before any
of this branch's changes were applied:

* **Added 5** — the `test_dedup.py` cluster above. Not present in the original nine-commit
  survey; they entered the tree afterwards.
* **Removed 1** — `test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric`
  now **passes**. It is no longer a known failure and its presence here would mask a
  future regression in the opposite direction.

The count line moved 8 → 12 while the suite itself grew (3311 → 3878 passing), so the
change is two real drifts, not a re-count of the same set.

## Re-pin, 2026-09-08 (named building keeps trailing identifier)

**The failing set is unchanged — still exactly the 12 above.** What moved is the xfail
count, 1 → 2, and this section records the second one so it is a pinned known-open item
rather than an unexplained marker.

| test | why it is open |
|---|---|
| `test_address_cleanup.py::TestNamedBuildingDetector::test_street_1_keeps_the_value_and_sets_no_building` | Street 1 half of the named-building trailing-identifier defect |

The Street 2-5 half is fixed (`_split_building_remainder` now runs a recognised building
segment to the end of the slot when no separator or room word follows). The Street 1 half
is not, and deliberately: `allow_rest=False` returns from `_named_building_value` before
the split path is reached, so `_SUITE_PATTERNS`' marker-first `Bldg <id>` entry still
takes the identifier and rewrites Street 1. Fixing it means suppressing that entry on the
primary line, which changes Street 1 output — the first STOP condition of this change's
gate. It gets its own prompt and its own gate, which will need a deliberate exception for
"Street 1 restored to the input verbatim on rows the marker-first entry currently splits".

`strict=True`, so if the Street 1 behaviour ever changes the suite fails on the XPASS
instead of going quietly green.

**On the count line.** It reads 3906, not the 3878 recorded at the `8ba0c75` re-pin. The
clean-tree control for this change measured **3893** — so 15 of the 28 were added by
`6140e17` and `f3f5dd7` after that re-pin, and 13 are this change's new fixtures. The count
line is documentation; the manifest table is the gate.
