# Known failures

The tests that fail on a clean tree. **A gate asserts the failing set is exactly this
manifest** — not a count, the set. A run with seven of these failing and one other test
failing is a regression, and a count alone cannot say so.

    8 failed, 3311 passed, 7 skipped

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
| `test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric` | single |

## The three clusters

* **Tier 2A gate narrowed, pre-history — 3 tests.** All three assert
  `tier2_mode == "2A_verification"` and get `None`. `run_tier2a` still exists and is still
  called, so this is the contact-lookup gate having narrowed rather than the lane having
  been withdrawn. One investigation, not three.
* **Mock-path classification drift — 2 tests.** Both assert `record_type == "company"` and
  get `"unknown"`: the classifier no longer settles a company on the mock search path.
* **Singles — 3 tests.** `test_tier1_full_resolution` (`confidence` `medium` where `high`
  is asserted); `test_department_in_a_lower_slot_is_not_reported_missing` (`G2-NAME-012`
  is now raised for a department in a lower slot); and the issues-compare route
  (`issues before` 2 where 1 is asserted).

None is a flake — each is a stable assertion failure at every commit tested.
