Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 19

# Pass 19 — Implementation state

What is present and wired at this commit, from repository evidence only. A component is
**present** when a tracked file implements it, **wired** when a tracked artefact calls it, and
**deployed** only where a file records the deployment. No claim here rests on the running Azure
tenant: nothing in this repository observes it.

Working-tree note (Rule 1): `git status --porcelain` returns 52 entries. Fifty-one are files
under `docs/thesis/` produced by this documentation run. The fifty-second is untracked
`tools/eval_report.py`, the read-only Pass 18 calculator (`tools/eval_report.py:1–5`); `tools/` is
excluded from the deployment package (`.funcignore:33`) and `pytest.ini:3` collects only `tests/`,
so it enters neither the shipped artefact nor the run in §19.6. **No source file, SQL file, ADF
export, fixture or workbook is modified**, so §19.6 is a run of the code at this commit.

---

## 19.1 Function App

`function_app.py:12` constructs `func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)` and
`:15–19` registers a single catch-all route `{*route}` that hands every request to the shared
FastAPI app through `AsgiMiddleware`. `host.json:13` sets `"routePrefix": ""`, so routes are
served at their FastAPI paths with no injected `/api` segment. One function fronts every
endpoint, so **auth level is uniform across all sixteen routes**; no per-route auth exists in
application code. `api/app.py:29` mounts the single router; `main.py:8` runs the same app under
uvicorn for local use.

Sixteen routes are defined. The "ADF caller" column names the tracked artefact that calls the
route; `—` means no artefact in this repository calls it.

| Route | Defined at | ADF caller |
|---|---|---|
| `GET /health` | `api/routes.py:93` | — |
| `POST /enrich` | `api/routes.py:106` | `adf/enrichment_pipeline.json:105` |
| `POST /enrich/file` | `api/routes.py:700` | — |
| `POST /issues` | `api/routes.py:837` | — |
| `POST /issues/json` | `api/routes.py:883` | `adf/issues_pipeline.json:58` |
| `POST /issues/compare` | `api/routes.py:916` | — |
| `POST /api/preprocess/consolidate` | `api/routes.py:960` | — |
| `POST /api/preprocess/consolidate/file` | `api/routes.py:1026` | — |
| `POST /api/dedup/cluster-block` | `api/routes.py:1330` | `adf/deduplication_pipeline.json:58` |
| `POST /api/dedup/file` | `api/routes.py:1360` | — |
| `POST /api/dedup/score` | `api/routes.py:1437` | `adf/scoring_pipeline.json:58` |
| `POST /api/dedup/approve` | `api/routes.py:1487` | — |
| `POST /api/dedup/score/file` | `api/routes.py:1518` | — |
| `GET /diag/llm` | `api/routes.py:1575` | — |
| `GET /diag/dedup-llm` | `api/routes.py:1607` | — |
| `GET /tiers` | `api/routes.py:1646` | — |

Four of the sixteen are wired. The preprocess pair is the case worth naming: `README.md:3441`
places `POST /api/preprocess/consolidate/file` **first** in the production sequence, run once over
the complete extract before any batching, and no exported pipeline calls it (`08_GAPS.md` §8.5).
The steward approval route `/api/dedup/approve` is likewise unwired; `CONTEXT-EXTERNAL.md:394–400`
records approval as a DS Studio action (`Leading Code` selector, `Apply Leading Code`), so the
repository does not evidence which of the two performs it.

**Deployment.** The host name `mdm-pipeline-api.azurewebsites.net` appears only inside the four
ADF `url` values (`adf/enrichment_pipeline.json:105`, `adf/issues_pipeline.json:58`,
`adf/deduplication_pipeline.json:58`, `adf/scoring_pipeline.json:58`) and in
`CONTEXT-EXTERNAL.md:405–408`, which is marked `[AUTHOR]`, not an export. `.funcignore:1–60`
defines the deployment package contents; no CI workflow, deployment script, `local.settings.json`
or ARM/Bicep template is tracked (`ls -a` shows no `.github`). **⚠ UNVERIFIED — that the Function
App is deployed, on which hosting plan, and with what HTTP timeout ceiling.** `host.json:3–10`
enables platform Application Insights; no application code emits custom telemetry
(`08_GAPS.md` §8.5, G-93/G-94).

---

## 19.2 ADF pipelines

Four pipelines are exported. All four are parameterised on `chrEntity` and `chrGroupCode`
(`adf/enrichment_pipeline.json:156–163`; `:106–113` in each of the other three) and carry the
group-code predicate `WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\'` in
their Lookup query.

| Pipeline | File | `lastPublishTime` | Shape | Endpoint | Merge procedure |
|---|---|---|---|---|---|
| Enrichment Pipeline | `adf/enrichment_pipeline.json` | `2026-09-06T16:49:32Z` (`:165`) | `Lookup2` offset driver → `ForEach1` → `Lookup1` / `Web1` / `Merge Back`, 30-row pages (`:21`, `:68`) | `/enrich` (`:105`) | `Mapping.usp_merge_legacy_enriched` (`:136`) |
| Issues Pipeline | `adf/issues_pipeline.json` | none recorded | `Lookup1` → `Web1` → `Merge Back`, unbatched (`:21`) | `/issues/json` (`:58`) | `Mapping.usp_MergeLegacyIssues` (`:89`) |
| Deduplication Pipeline | `adf/deduplication_pipeline.json` | `2026-07-29T12:09:37Z` (`:115`) | `Lookup1` → `Web1` → `Merge Back`, unbatched (`:21`) | `/api/dedup/cluster-block` (`:58`) | `Mapping.usp_MergeValidationClusters` (`:89`) |
| Scoring Pipeline | `adf/scoring_pipeline.json` | `2026-07-31T18:27:48Z` (`:115`) | `Lookup1` → `Web1` → `Merge Back`, unbatched (`:21`) | `/api/dedup/score` (`:58`) | `Mapping.usp_MergeValidationScores` (`:89`) |

`Entity_BasicFlow` is **⚠ NOT EXPORTED**, and no run order is expressed anywhere in the
repository: `grep -rn "ExecutePipeline" adf/` returns `0` lines, so no pipeline invokes another.
The order in which the four run is stated only in prose (`README.md:3436–3455`,
`CONTEXT-EXTERNAL.md:412–429`). Register entry G-26 (`08_GAPS.md` §8.5).

Also **not exported**, each with a consumer in the four files above: datasets `AzureSqlMITable1`,
`AzureSqlMITable3`; linked services `ls_sqlmi_legacy`, `ls_sqlmi_validation`; the
`AutoResolveIntegrationRuntime` (G-33). Because the linked services are absent, the database the
two `Validation` pipelines read cannot be resolved here — their queries carry no database prefix
(`adf/deduplication_pipeline.json:21`, `adf/scoring_pipeline.json:21`) — G-21.

Two further workflow steps have no ADF artefact at all: address validation with auto write-back
above 80 % confidence (`CONTEXT-EXTERNAL.md:423`, G-58) and the preprocess/ZFI-exclusion script
(`CONTEXT-EXTERNAL.md:418`, `:445`).

---

## 19.3 Merge procedures

All four are present as `CREATE PROCEDURE` scripts under `sql/`, in schema `[Mapping]`:
`usp_MergeLegacyEnriched` (`sql/usp_merge_legacy_enriched.sql:1`, 89 lines),
`usp_MergeLegacyIssues` (`sql/usp_merge_legacy_issues.sql:1`, 69 lines),
`usp_MergeValidationClusters` (`sql/usp_merge_validation_clusters.sql:1`, 66 lines),
`usp_MergeValidationScores` (`sql/usp_merge_validation_scores.sql:1`, 81 lines). They are the only
`.sql` files tracked; no file creates or alters a table (G-08).

Three defects at the ADF ↔ SQL boundary are carried from Pass 08 and are stated here as
implementation state, not re-derived:

- **G-19** — the enrichment activity names `Mapping.usp_merge_legacy_enriched`
  (`adf/enrichment_pipeline.json:136`) while the script creates `[Mapping].[usp_MergeLegacyEnriched]`
  (`sql/usp_merge_legacy_enriched.sql:1`). The identifiers differ by more than letter case. The
  other three match exactly.
- **G-05** — all four activities pass `storedProcedureParameters` of exactly `{payload}`; all four
  procedures declare `@chrEntity` and `@chrGroupCode` without defaults. The entity and group code
  the Lookups are parameterised on never reach the merge.
- **G-21** — `DECLARE @db SYSNAME = N'dp_validation';  -- <<< confirm`
  (`sql/usp_merge_validation_clusters.sql:8`, `sql/usp_merge_validation_scores.sql:8`) is an
  unresolved marker left in the deployed text of both Validation procedures.

**⚠ UNVERIFIED — whether any of the four is installed on the Managed Instance, and whether the
installed body matches the tracked file.** No connection string, deployment script or migration
log is tracked.

---

## 19.4 DATAshaper entity, group codes and views

Every fact in this section is `[OBSERVED]` from DS Studio on 2026-08-16 and recorded in
`docs/thesis/CONTEXT-EXTERNAL.md`; none is a file artefact, and DATAshaper offers no export
(`CONTEXT-EXTERNAL.md:337–339`).

| Item | State | Cite |
|---|---|---|
| Entity | `test_77`, realised as the SQL schema name | `CONTEXT-EXTERNAL.md:21–27` |
| Group codes | `TEST2`–`TEST8`, `TEST10`; runs observed under `TEST8` | `:21–23` |
| Record code grammar | group code as prefix — `TEST7_41000009`, `TEST10_42000001` | `:34–35` |
| Table progression | Import → Legacy → Validation → load file | `:341–345` |
| DS processes | `LegacyMapping`, `MigrateData`, `ProcessValidation` | `:30–33`, `:347–352` |
| Issues view | per-code counts with drill-down to field | `:362–380` |
| Deduplication view | `Cluster`, `Code`, `Reason`, `Cluster_ID`, `Block ID`, `Signature`; steward `Apply Leading Code` | `:387–402` |

`test_77` is corroborated inside the repository only as a write-back target named in prose —
`dp_legacy.test_77.Legacy` (`README.md:3473`, `corroborator_report.md:176`). The exported
pipelines never hard-code it; they take it as `chrEntity`.

---

## 19.5 End-to-end run

**⚠ UNVERIFIED — no full end-to-end DATAshaper run is evidenced by any file in this repository.**
No run log, ADF run id, pipeline-run export or DS output artefact is tracked. The nearest evidence
is three observations, and none covers the whole sequence:

- DS process tasks `LegacyMapping`, `MigrateData`, `ProcessValidation` completed with result `1/1`
  on 12/08/2026 (`CONTEXT-EXTERNAL.md:30–33`) — the DS half, not the API calls.
- The DS issues view rendered per-code counts (`G2-VAL-007` 40, `G1-ADDR-001` 3/8, …)
  (`CONTEXT-EXTERNAL.md:366–375`), and the deduplication view rendered `c_`-prefixed `Cluster_ID`
  values (`:390–393`). Both are outputs of this pipeline reaching DS at some time before
  2026-08-16, at an unrecorded commit.
- `logs/enrichment_api.log` and its five rotations are **local** uvicorn/pytest logs written on
  this machine (most recent entry `2026-09-07 19:43`), not production traffic.

The evaluation is likewise local and file-based, and is not evidence of a wired run: Pass 18
computes every figure by re-running the shipped detector over the workbooks in `data/eval/`
(`docs/thesis/18_EVAL_RESULTS.md` §18.1.2), and the recorded enrichment runs it cross-checks
against ran `--frozen` with `0` network calls (`eval/out/RUNS.md`, commit `d3a3cfc`). No number
in the documentation set was produced by a call that passed through ADF, the merge procedures or
DATAshaper.

---

## 19.6 Test suite

`pytest.ini:3` sets `testpaths = tests`. Run at this commit, with no source file modified (see the working-tree note):

    $ python3 -m pytest -q
    FAILED tests/test_dedup.py::test_conflicting_ror_not_merged_verdict_guard
    FAILED tests/test_dedup.py::test_conflicting_lei_not_merged_verdict_guard
    FAILED tests/test_dedup.py::test_no_signal_pair_not_nominated_reason_empty_ok
    FAILED tests/test_dedup.py::test_mode_b_canonical_assignment_produces_correct_clusters
    FAILED tests/test_dedup.py::test_route_cluster_block_identical_rows
    FAILED tests/test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing
    FAILED tests/test_orchestrator.py::TestOrchestrator::test_tier1_full_resolution
    FAILED tests/test_orchestrator.py::TestOrchestrator::test_tier1_to_tier2a_verification
    FAILED tests/test_orchestrator.py::TestOrchestrator::test_web_search_fallback_for_name1
    FAILED tests/test_orchestrator.py::TestOrchestrator::test_web_search_determines_record_type
    FAILED tests/test_orchestrator.py::TestTier2AVerificationMergeLayer::test_low_score_medium_confidence_keeps_record_value
    FAILED tests/test_orchestrator.py::TestTier2AVerificationMergeLayer::test_low_score_high_confidence_overwrites_record_value
    12 failed, 3857 passed, 12 skipped, 1 xfailed, 1 warning in 17.15s

This reproduces the run recorded in Pass 00 §0.7.1 exactly. The failing **set** is 7 of the 8
manifest entries in `tests/KNOWN_FAILURES.md:19–26` — `test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric`
now passes — plus 5 `tests/test_dedup.py` failures the manifest does not list. The manifest itself
records `8 failed, 3311 passed, 7 skipped` (`tests/KNOWN_FAILURES.md:7`) and asserts at `:3–5` that
"a gate asserts the failing set is exactly this manifest"; **no such gate exists** —
`grep -rn 'KNOWN_FAILURES' --include='*.py' .` returns nothing, and no CI configuration is tracked.
Register entries G-01 and G-17.

One harness script outside `tests/` does not import at this commit, so it is present but not
runnable — `python3 -c "import scripts.ch02_measure"` raises
`ImportError: cannot import name '_SUBLOCATION_SLOTS' from 'enrichment.issue_detection'`
(`scripts/ch02_measure.py:64`). Pass 07 §1.1 lists it as a live harness script; independently
found by Pass 18 (`docs/thesis/18_EVAL_RESULTS.md` §18.10 defect 18-1). Nothing in the shipped
package imports it (`.funcignore:32` excludes `scripts/`).

---

## 19.7 Open items carrying a named owner

Only two of the six names in scope appear as an owner in any tracked file.

| Item | Owner named | Cite |
|---|---|---|
| `Operating Name` / `Operating Name Provenance` write-back — the two nullable additive columns exist in the API response and file schema; the Legacy DDL, the `OPENJSON … WITH` list, the DS mapping and the ADF allow-list are not done | Bernd / Bert | `README.md:3466`, `:3468–3476` |
| The `unresolved` → `verified` severity move on corroborated-unchanged records, which a DS rule may be keyed on — to confirm before rollout | Bernd / Bert | `README.md:3491` |
| Two merge-procedure columns needing confirmation ahead of the provenance-grammar change; no procedure reads a provenance column today | Bert | `provenance_migration_report.md:268–281` |
| Golden-record weights marked unconfirmed — `combined_presence_bonus` value, `sales_order_partner_count` tiers, `account_group` `DRIT` vs `DRID` | Bernd | `dedup/weights.json:2` |
| Scoring code marked unconfirmed — partner-count tiers, the sales-org bonus, the tie-break ordering | Bernd | `dedup/scoring.py:982`, `:1021`, `:1051` |
| ZFI records excluded from processing on instruction; the rationale is recorded in no artefact | Bernd Schnurrer | `CONTEXT-EXTERNAL.md:434–435` |

**Daniel, Jean-Yves, Michel and Salvador are named as owner in no tracked file.** A
case-insensitive search over `*.py`, `*.md`, `*.json`, `*.sql` and `*.txt` returns, for those four
names, only `tests/test_output_casing.py:134` (`"JEAN-YVES DUPONT"` as a title-casing fixture) and
substring hits inside `Albert` / `Adelbert` / `Robert` in test data. ⚠ UNVERIFIED whether items are
assigned to them outside the repository; DS validation rules can carry an assigned responsible
person (`CONTEXT-EXTERNAL.md:354–360`) and that assignment is not exportable.

---

## 19.8 Out of scope

**Salesforce enrichment and prospect matching are out of scope** and are not implemented here.
Salesforce enters the repository at one point only: eight flat identifier slots `sf1`–`sf8`
(`SF_ID_Biosystems`, `SF_ID_AXS`, `SF_ID_3`–`SF_ID_8`) read as **input** to golden-record scoring,
counted for non-blank presence and multiplied by a weight
(`dedup/scoring_xlsx.py:66–69`, `dedup/scoring.py:794–808`, `:1027–1029`;
`adf/scoring_pipeline.json:21` projects them from `Validation`). No code reads or writes a
Salesforce system, and the word "prospect" occurs in no tracked source file.

---

## 19.9 Items raised by this pass

| # | Item |
|---|---|
| ⚠-1 | Twelve of the sixteen routes have no caller in any tracked artefact, and two of them are load-bearing in the documented workflow: preprocess-consolidate is stated as the mandatory first step (`README.md:3441`) and `/api/dedup/approve` is the steward sign-off. Either ADF calls them through an unexported pipeline, or the workflow's first and last steps run by hand. §19.1. |
| ⚠-2 | The Issues Pipeline carries no `lastPublishTime` while the other three do. Whether it has been published to the factory at all cannot be read from the export. §19.2. |
| ⚠-3 | Nothing in this repository is evidence that the Function App, the four pipelines or the four procedures are deployed. Every deployment claim in the set rests on `CONTEXT-EXTERNAL.md` `[AUTHOR]` prose or on a URL string inside an export. §19.1, §19.3. |
| ⚠-4 | The three DS observations in §19.5 were taken on 2026-08-16, before the current enrichment pipeline was published (2026-09-06) and before this commit. They cannot be cited as the state of a run of this code. |
| ⚠-5 | `test_77` and the group codes `TEST2`–`TEST10` are observation-only. No tracked artefact fixes the entity a run uses; the pipelines take it as a parameter and the parameters have no `defaultValue`. §19.2, §19.4. |

---

**Pass 19 summary.** `docs/thesis/19_IMPLEMENTATION_STATE.md` written: sixteen Function App routes
with the four that ADF calls, the four exported pipelines with publish times and the absent
`Entity_BasicFlow`, the four merge procedures present as scripts with three unresolved boundary
defects, the observation-only DATAshaper entity `test_77` / group codes, no evidenced end-to-end
run, a reproduced `12 failed / 3857 passed / 12 skipped / 1 xfailed` against a manifest and a gate
that do not match it plus one harness script that no longer imports, six open items owned by Bernd
and Bert with none owned by the other four names, and Salesforce confined to eight scoring input
slots.
