Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 07

# Pass 07 — Evaluation harness

What exists in the repository to evaluate the pipeline with: the harness scripts, the gates in
the test suite, the datasets, the fixture stores, and the recorded artefacts of earlier runs.

**No result is computed here.** Row counts, column layouts, country distributions and dataset
provenance are properties of the artefacts and are reported; issue counts, reduction rates,
precision, recall, cluster agreement and cost belong to Pass 18 and are not stated in this
document. Where an earlier run's numbers are already recorded in the repository they are
identified as such, with the commit they were produced at, and are not repeated as current.

**Tree state.** `git status --porcelain` at the time of writing lists only the pass documents of
this documentation set (`docs/thesis/00`–`06`). No source file, dataset or fixture is modified;
every measurement below is taken against the working tree at
`86d173b8a4d715a619b0a2656986c145da7fa81e`.

**Method.** Workbook figures come from one read-only script,
`eval_inventory.py` (scratch, not committed — Pass 18 commits `tools/eval_report.py`), which
opens each workbook with `openpyxl` and reports, per sheet, the number of data rows (rows in
which at least one cell is non-blank; fully blank separator rows are excluded), the header list,
duplicate headers, and the value distribution of the country column. Its invocation and verbatim
output are in Appendix A. `pandas` is not installed in this environment; `openpyxl 3.1.5` is.

---

## 1 — Harness scripts

### 1.1 Scorers

| script | LOC | scores what | input | output | network |
|---|---|---|---|---|---|
| `eval/dedup_eval.py` | 308 | Phase 2 clustering + golden-record election against fixture ground truth | a *scored* workbook that still carries `expected_cluster` / `expected_routing` (`eval/dedup_eval.py:3-5`) | printed report + `eval_report.json` (`eval/dedup_eval.py:296-303`) | none — "pure arithmetic over the sheet" (`eval/dedup_eval.py:5-6`) |
| `eval/name_eval.py` | 354 | the name block (Name 1–4) of an enriched workbook | enriched workbook + `testall100_SOLVED_REFERENCE_v1.xlsx` (default, `eval/name_eval.py:319`), optional `--baseline` (`:322-325`) | printed report, `--out` JSON (`:326`) | none |
| `scripts/issue_catalogue_census.py` | 280 | the Issue-Catalogue vocabulary itself, and optionally a per-file census | `ISSUE_CATALOGUE` / `EMITTED_CODES` from `enrichment/issue_detection.py` (`:41-47`), plus any XLSX paths given as arguments (`:250`) | printed census; `--write-oracle` **rewrites the oracle sheets of `PresentationTestData.xlsx`** (`:250`, `:274-275`) | none |
| `scripts/ch02_measure.py` | 708 | Chapter 2 frequency measurements (issue frequency, duplicate prevalence, registry coverage) | `PresentationTestData.xlsx` and `PresentationTestData_enriched_checked_v1.xlsx` (`:106-107`) | printed report | none — "Read-only: no file in the repository is modified and no external call is made" (`:4`) |

`eval/dedup_eval.py` reports pairwise TP/FP/FN, precision, recall, F1 against the fixture, plus
three named business-risk counts — `wrongful_block_candidates`, `competing_goldens`,
`uncertainty_upgrades` — each with the offending row ids (`eval/dedup_eval.py:22-25`), and the
election counts including how many clusters were decided by the tie-break rather than the score
(`:27-29`).

`eval/name_eval.py` embeds no expectation of its own: the per-column match rules (`exact`,
`exact_ci`, `skip`) and the 68 per-cell overrides are read from the `Match Rules` and
`Cell Notes` sheets of the reference workbook at run time (`eval/name_eval.py:13-30`). The
reference "is not ground truth and does not claim to be… authored by one reviewer"
(`eval/name_eval.py:8-10`).

### 1.2 Reproducibility gates

| script | LOC | asserts | input | exit codes |
|---|---|---|---|---|
| `tools/run_diff.py` | 401 | two runs of one batch are identical across every column in `api.output_columns.RESPONSE_COLUMNS` (`:34-41`) | two `scripts/run_batch.py --json` artefacts (`:327-328`); the enriched XLSX is deliberately refused (`:16-18`) | `0` PASS, `1` rows differ (`:397`), `2` incomparable provenance grammars (`:341-366`) |
| `tools/provenance_invariance.py` | 248 | enrichment **values** are invariant while provenance columns change — the migration gate (`:1-6`) | two run `--json` artefacts (`:229-230`) | `0` / `1` on `report["passed"]` (`:244`) |
| `tools/shuffle_evidence.py` | 115 | the pipeline decides rather than replaying candidate order: reverses every candidate list in a recorded evidence cache (`:1-5`) | source cache dir, destination cache dir (`:76-79`) | `2` on wrong argument count |
| `scripts/cache_state.py` | 28 | two gate lines are comparable: entry count + sha256 over the sorted key list, keys only (`:1-8`) | a cache root, default `logs/cache` (`:12`) | — |

`run_diff.py` joins on `(name1_original, city)` and states why not on Search Term 1 — a
pipeline-written column would fail to line up exactly the records the two runs disagree about
(`tools/run_diff.py:19-32`). `duration_ms` is outside `RESPONSE_COLUMNS` and so is not compared
(`:43-44`). After the diff it prints `run 2 network calls`, taken from the second run's summary,
because "a second run that went to the network is not comparing the same evidence the first one
saw" (`:377-382`).

### 1.3 Run drivers and fixture builders

| script | LOC | role |
|---|---|---|
| `scripts/run_batch.py` | 200 | runs an XLSX batch through the orchestrator offline, on the same `_parse_xlsx` / `_rows_to_records` / `_build_output_xlsx` path as `POST /enrich/file` (`:3-7`) |
| `tools/build_dedup_v2_fixture.py` | 182 | freezes the dedup stress workbook into `tests/fixtures/dedup_v2_stress_200.json` (`:46-48`); `--check` regenerates and diffs without writing (`:5-6`) |
| `tools/dedup_v2_real_model_run.py` | 317 | runs the frozen 200-row dedup fixture against the deployed model and scores it against the oracle double; `--record` calls the deployment and writes `tests/fixtures/dedup_v2_llm_cache/` (`:35-36`, `:175-176`) |
| `scripts/wikidata_warm_fixtures.py` | 94 | records the Wikidata fixtures for a workbook serially, so the measured run is a replay (`:1-7`) |
| `scripts/wikidata_lane_report.py` | 225 | A/B report of the Wikidata lane from two `run_batch.py` runs (`WIKIDATA_ENABLED` off vs on) (`:1-6`); default input `docs/thesis/chemspeed_us_100.xlsx` (`:123`) |
| `scripts/fix_reports.py` | 264 | per-row tables and before/after delta for Fixes 2 and 3, from two run JSON dumps plus the page trace (`:1-6`) |
| `scripts/retry_trace_report.py` | 132 | classifies a `RETRY_TRACE` run into the four Stage 5 buckets from the `enrichment.trace.retry` JSON lines (`:1-6`) |

`scripts/run_batch.py` is the only entry point that exposes the evaluation controls on the
command line: `--frozen` sets `CACHE_FROZEN=true` (`:76-79`, applied at `:96-97`), `--cache-dir`
overrides `EVIDENCE_CACHE_DIR` (`:80-81`, applied at `:98-99`), `--no-wikidata` sets the A/B
baseline (`:74-75`), `--json` writes the artefact `run_diff.py` consumes (`:65`), and
`--retry-trace` / `--website-trace` / `--wikidata-trace` enable the diagnostic loggers
(`:68-73`).

### 1.4 Single-record diagnostics

`scripts/ror_repro.py` (523) reproduces one record's Tier 1 ROR lookup and prints why each
candidate scored what it scored (`:1-7`); `scripts/trace_website.py` (202) exercises only Path B
/ Path C website resolution (`:1-6`); `scripts/debug_ucsf.py` (269) runs one named record with
verbose logging; `scripts/test_local.py` (202) exercises the JSON fixtures against a running API
in `--mock` or `--live` mode (`:1-7`); `scripts/verify_fixes.py` (232) is a post-fix
verification script that requires a server on `main.py` (`:1-7`).

---

## 2 — Gates inside the test suite

| test module | LOC | what it gates |
|---|---|---|
| `tests/test_determinism.py` | 1951 | Fixes A–D: sampling parameters pinned, cache keys free of run/batch/date ids, cross-source consistency (`:1-9`) |
| `tests/test_dedup_eval.py` | 105 | the metrics of `eval/dedup_eval.py`, against an in-memory scored workbook with known ground truth (`:1-4`) |
| `tests/test_issue_catalogue_coverage.py` | 182 | every emittable issue code has a positive and a near-miss case in `tests/fixtures/issue_catalogue_coverage.json`; adding a code without one fails the suite (`:1-7`) |
| `tests/test_dedup_v2.py` | 369 | the dedup v2 expectations over the 200-row fixture, with all three flags on (`:1-5`) |
| `tests/dedup_v2_support.py` | 965 | not a test module: fixture loader, expectation tables, and the three LLM doubles the v2 tests share (`:1-6`) |
| `tests/test_calibration.py` | 1028 | the calibration fixes, each named record a gate rather than a special case (`:1-6`) |

`pytest.ini` sets `asyncio_mode = strict` and `testpaths = tests`. `tests/conftest.py` forces the
suite offline by default: `MOCK_EXTERNAL_CALLS=true` (`:20`), `WIKIDATA_FIXTURE_REPLAY_ONLY=true`
so no test can reach the live Wikidata API (`:21-27`), and every evidence-cache namespace rooted
in a throwaway directory so one test's recorded answer cannot be served to another and the suite
cannot write into `tests/fixtures/page_reads/` (`:31-40`).

`tests/KNOWN_FAILURES.md` pins the failing set — not a count, the set — at
`8 failed, 3311 passed, 7 skipped` (`tests/KNOWN_FAILURES.md:7`), established across nine
commits, with a per-test manifest and a three-cluster explanation (`:17-27`). The suite state at
this commit is Pass 00's measurement and is not restated here.

---

## 3 — Datasets in `data/eval/`

Sixteen workbooks, all tracked. Every one of them is US-only: the country column
(`Country/Region Key` — see §3.4) holds `US` in every populated data row of every sheet that
carries it.

### 3.1 Stratum pairs S1–S5

`S{n}_pre.xlsx` is the pre-enrichment issues export for stratum *n*; `S{n}_post.xlsx` is the
post-enrichment export (the pairing rule of Pass 18).

| file | rows | columns | country | `eval_set` | `category` | `record_type_hint` | last-touching commit |
|---|---|---|---|---|---|---|---|
| `S1_pre.xlsx` | 100 | 42 | US 100 | S1 100 | academic_research 100 | research_institution 99, company 1 | `eb924e6` 2026-09-07 |
| `S1_post.xlsx` | 100 | 83 | US 100 | S1 100 | academic_research 100 | research_institution 99, company 1 | `eb924e6` |
| `S2_pre.xlsx` | 100 | 42 | US 100 | S2 100 | large_corporate 100 | company 99, government 1 | `eb924e6` |
| `S2_post.xlsx` | 100 | 84 | US 100 | S2 100 | large_corporate 100 | company 99, government 1 | `eb924e6` |
| `S3_pre.xlsx` | 100 | 42 | US 100 | S3 100 | government_labs 100 | government 80, company 20 | `eb924e6` |
| `S3_post.xlsx` | 100 | 84 | US 100 | S3 100 | government_labs 100 | government 80, company 20 | `eb924e6` |
| `S4_pre.xlsx` | 100 | 42 | US 100 | S4 100 | hospital_health 100 | hospital_health 93, government 4, company 3 | `eb924e6` |
| `S4_post.xlsx` | 100 | 85 | US 100 | S4 100 | hospital_health 100 | hospital_health 93, government 4, company 3 | `eb924e6` |
| `S5_pre.xlsx` | 100 | 42 | US 100 | S5 100 | smb_residual 100 | company 87, individual 11, government 2 | `eb924e6` |
| `S5_post.xlsx` | 100 | 84 | US 100 | S5 100 | smb_residual 100 | company 87, individual 11, government 2 | `eb924e6` |

Each file is a single sheet named `Sheet`. `src_state` (the source SAP state export) per stratum:

| stratum | `src_state` distribution |
|---|---|
| S1 | CA 31, TX 28, NJ 10, MI 10, MA 9, FL 9, OH 3 |
| S2 | TX 26, CA 21, OH 14, NJ 12, MA 11, FL 9, MI 7 |
| S3 | CA 51, FL 15, TX 13, MI 7, MA 5, OH 5, NJ 4 |
| S4 | TX 36, CA 25, FL 18, OH 8, MA 6, NJ 4, MI 3 |
| S5 | CA 33, TX 24, FL 13, MA 11, NJ 9, OH 6, MI 4 |

`pre` and `post` carry the identical annotation block — `eval_set`, `category`, `src_state`,
`record_type_hint`, `cluster_id`, `cluster_role`, `demo_highlight`, `issue_count`,
`issue_groups`, `expected_issue_codes`, `what_the_pipeline_does`, `expected_use_cases`,
`defect_evidence` — so a stratum's annotation is available on both sides of the pair.
`expected_issue_codes` is the annotator's expectation (semicolon-separated codes); the `Issues`
column is what the detector emitted.

**No script in the repository generates these ten workbooks**, and none of them records a
generating commit, run id or settings block: there is no `Run` sheet and no provenance column.
Their only recorded provenance is the git commit that added them (`eb924e6`, 2026-09-07).
⚠ MEASUREMENT REQUIRED — the settings under which the `post` exports were produced (cache state,
`CACHE_FROZEN`, `WIKIDATA_ENABLED`, deployment) are not recoverable from the files; Pass 18 must
either re-run the strata or state that it is scoring exports of unknown provenance.

### 3.2 Where the `Issues` column sits, and the S1 ambiguity

`POST /issues` echoes the uploaded sheet "unchanged with a single appended `Issues` column"
(`api/routes.py:845`), built as `ws.append([*headers, "Issues"])` (`api/routes.py:445`) — the
appended column is therefore always the **last** one.

| file | rows | `Issues` at column(s) (0-based) | non-empty per column |
|---|---|---|---|
| `S1_pre.xlsx` | 100 | 2, 7 | 97 / 85 |
| `S2_pre.xlsx` | 100 | 6, 41 | 95 / 95 |
| `S3_pre.xlsx` | 100 | 41 | 95 |
| `S4_pre.xlsx` | 100 | 41 | 94 |
| `S5_pre.xlsx` | 100 | 41 | 97 |
| `S1_post.xlsx` | 100 | 7, 82 | 79 / 79 |
| `S2_post.xlsx` | 100 | 83 | 88 |
| `S3_post.xlsx` | 100 | 83 | 74 |
| `S4_post.xlsx` | 100 | 84 | 95 |
| `S5_post.xlsx` | 100 | 83 | 95 |

Where a workbook holds two columns named `Issues`, the two were compared cell by cell:

    data/eval/S1_pre.xlsx:  cols 2/7  — identical 28, different 56, only col2 13, only col7 1, both empty 2
    data/eval/S2_pre.xlsx:  cols 6/41 — identical 95, different 0, only col6 0, only col41 0, both empty 5
    data/eval/S1_post.xlsx: cols 7/82 — identical 79, different 0, only col7 0, only col82 0, both empty 21

⚠ **`S1_pre.xlsx` carries two `Issues` columns that disagree on 56 of its 100 rows**, and neither
is the last column, so neither can be the column `POST /issues` appended to *this* layout. Some
of the 56 differ only in code order (`13334811`: `G1-ADDR-001; G1-ADDR-003; G5-NAME-001;
G2-NAME-012` against `G1-ADDR-001; G1-ADDR-003; G2-NAME-012; G5-NAME-001`) and some differ in
membership (`13047582`: `G4-ADDR-008` against `G2-NAME-012`). The stratum-1 pre-enrichment
baseline is therefore ambiguous as shipped. Pass 18 must compare code **sets**, not strings, and
must state which column it read. The duplicate columns in `S2_pre.xlsx` and `S1_post.xlsx` are
exact copies and carry no such ambiguity.

The five `pre` workbooks are also not column-identical to one another: `S3`–`S5` hold `Division`
at column 6 and the appended `Issues` at 41; `S2` holds `Issues` at 6 in place of `Division`;
`S1` holds an unnamed empty header at column 1, `Issues` at 2, and `Issues` at 7. Any harness
that reads these by column index rather than by header will read the wrong column on at least
one stratum.

### 3.3 Stress sets

| file | sheet | rows | columns | distinct `Customer` | last-touching commit |
|---|---|---|---|---|---|
| `stress_200_pre.xlsx` | Data | 183 | 38 | 183 | `eb924e6` |
| | Clusters | 85 | 11 | — | |
| | Traps | 9 | 6 | — | |
| | Method | 7 | 2 | — | |
| `dedup_STRESS_200_v1-verified.xlsx` | Data | 183 | 34 | 183 | `ea6f9d9` |
| | Clusters / Traps / Method | 85 / 9 / 7 | 11 / 6 / 2 | — | |
| `dedup_STRESS_200_v1_enriched_dedup.xlsx` | Sheet | 200 | 87 | 200 | `ea6f9d9` |
| | Dedup Debug | 200 | 4 | — | |
| | Clusters / Traps / Method | 85 / 9 / 7 | 11 / 6 / 2 | — | |
| `stress_200_scored.xlsx` | Sheet | 183 | 117 | 183 | `eb924e6` |
| | Dedup Debug | 183 | 5 | — | |
| | Run | 14 | 2 | — | |
| | Clusters / Traps / Method | 85 / 9 / 7 | 11 / 6 / 2 | — | |
| | Reconciliation | 64 | 2 | — | |
| | QLIC_Merge | 26 | 3 | — | |
| | Dummy_Fill | 27 | 3 | — | |
| | Issues | 20 | 4 | — | |

The design is a "200-record stress test"; the shipped customer-grain files carry **183**. The
183 customers of `-verified`, `stress_200_pre` and `stress_200_scored` are identical sets and a
strict subset of the 200 in `_enriched_dedup` — 17 customers are in the enriched workbook only.

`category` distribution is the same in every one of them at the 183-row grain — academic_research
80, large_corporate 40, hospital_health 23, smb_residual 21, government_labs 19 — and at the
200-row grain: academic_research 84, large_corporate 43, hospital_health 26, smb_residual 24,
government_labs 23. The `Method` sheet states the intended mix as the 200-row figures
(`stress_200_pre.xlsx::Method`, row "The mix").

**Ground truth vocabulary.** The stress workbooks carry `gt_entity_id`, `gt_dup_group`,
`gt_expected_action`, `gt_trap_group`, `gt_difficulty`, `gt_match_signal`, `gt_why`, and a
per-entity `Clusters` sheet. The `Method` sheet defines three levels: `gt_dup_group` = collapse
to one golden record; a shared `gt_entity_id` across different dup groups = same organisation at
different sites, "the algorithm should link them but NOT collapse them"; `gt_trap_group` =
look-alike families across different organisations, where "any merge across a trap group is a
false positive". `gt_expected_action` takes `MERGE`, `LINK`, `DO NOT MERGE (building)`,
`DO NOT MERGE (different organisation)`, `REVIEW`, `UNIQUE`. `gt_difficulty` is computed from the
minimum pairwise rapidfuzz similarity of the raw Name 1 values inside the entity group —
hard < 60, medium 60–84, easy ≥ 85.

The `Method` sheet's own honesty notes state that the file "was authored by one reviewer; treat
`gt_*` as a reference, not an oracle", that unverifiable same-entity claims were labelled `LINK`
or `REVIEW` rather than `MERGE` so the `MERGE` labels are conservative, and that a handful of
labels rest on institutional knowledge recorded per case in `gt_why`.

The nine trap families are named on the `Traps` sheet: `T-CONTRACOSTA`, `T-HP`, `T-LEE`,
`T-LICKING`, `T-ORLANDO`, `T-STANFORD`, `T-TAKEDA`, `T-UCLA`, `T-UTDALLAS`, each with entity
count, record count, difficulty, membership and the discrimination it tests.

⚠ **`eval/dedup_eval.py` cannot score these workbooks as they stand.** It reads
`expected_cluster` and `expected_routing` (`eval/dedup_eval.py:3-5`, `:45-46`); no sheet in any
`data/eval/` workbook carries either header. Pass 18 needs an explicit mapping from
`gt_dup_group` / `gt_expected_action` onto that vocabulary, or a different scorer.

**How `stress_200_scored.xlsx` was built** — from its own `Reconciliation`, `QLIC_Merge` and
`Dummy_Fill` sheets:

| fact | value |
|---|---|
| Source fixture | `dedup_STRESS_200_v1-verified.xlsx` (Data sheet) |
| Raw grain source | 7 unfiltered SAP state exports: `CA_EXPORT.xlsx`, `TX_EXPORT.xlsx`, `MA_EXPORT.xlsx`, `FL_EXPORT.xlsx`, `NJ_EXPORT.xlsx`, `MI_EXPORT.xlsx`, `OH_EXPORT.xlsx` |
| Customers in source fixture | 183 (183 matched to the raw exports, 0 unmatched) |
| Rows emitted at SAP grain | 839 |
| Duplicate `gt_expected_action` column | dropped (col 34); kept col 7 — the two disagreed on 8 rows |
| Scoring reference | `US_Qlic_report_data_2026-07-30.xlsx`, 22,224 rows / 20,025 customers |
| Join key | `Customer`, trimmed, leading zeros stripped; 0 within-customer conflicts |
| Customers matched in QLIC | 83 — scoring columns populated |
| Customers not in QLIC | 100 — **filled with deterministic dummies**, seed `sha256("bbio-dummy-v1:" + Customer)` |

⚠ **100 of the 183 rows carry synthetic scoring inputs.** The seven QLIC-derived scoring columns
(`Sales_Order_Last_Used`, `Sales_Order_Total_Count`, `Sales_Order_Partner_Last_Used`,
`Sales_Order_Partner_Total_Count`, `Equipment_Total_Count`, `SleepingCustomer`,
`CustomerStatus`, plus `sf1`–`sf8`) are real for 83 customers and seeded dummies for 100. Any
election result computed from this workbook is an election over partly synthetic evidence and
must be reported as such. Clustering, which does not read those columns, is unaffected.

The `Run` sheet records the settings of the run that produced the scored workbook — the only
dataset in `data/eval/` that records its own generating configuration:

    prompt_version      p2-dedup-v8
    model               MDM-Apoorva-gpt-5.4
    model_version       MDM-Apoorva-gpt-5.4
    DEDUP_V2_BLOCKING   true
    DEDUP_V2_NAME2      true
    DEDUP_V2_ID_CONFLICT true
    dedup_v2_active     true
    fixture_cache       off
    rows_in             183
    blocks              111
    llm_calls           39
    rows_clustered      78
    rows_unique         88
    rows_manual_review  17

No commit is recorded on that sheet. `fixture_cache off` means the run went to the network for
its LLM calls; the 39 calls are therefore not reproducible from anything in the repository.

### 3.4 The `t100` mixed workbook, and a column-alignment defect

| file | sheet | rows | header columns | populated columns | last-touching commit |
|---|---|---|---|---|---|
| `test-all-100-original.xlsx` | Sheet1 | 99 | 50 (41 named, 9 unnamed) | 41 on 75 rows, 50 on 24 rows | `ea6f9d9` |
| `test-all-100-original_enriched (4).xlsx` | Sheet | 99 | 81 (all named) | 72 on 75 rows, 81 on 24 rows | `ea6f9d9` |

⚠ **`test-all-100-original.xlsx` is column-misaligned on 24 of its 99 rows.** Its header row
names 41 columns and leaves the last nine unnamed. Seventy-five rows populate 41 columns and
align with the header. Twenty-four rows populate 50 and are shifted right by nine positions from
column 24 (`Terms of Payment`) onward, so for those rows the header `Created On` sits over a city
(`PRINCETON`), `Created By` over a postal code (`08544`), `eval_set` over a region (`NJ`),
`category` over a country (`US`), and the real annotation block lands in the nine unnamed
columns. The 24 shifted rows are the S1 (academic_research) rows. The enriched twin inherits the
same split — 75 rows at 72 columns, 24 at 81 — so the defect is in the input, not the export.
Reading either workbook by header name yields wrong values for a quarter of its rows.

Both files also hold 99 rows, not 100.

---

## 4 — Datasets and fixture stores in `tests/fixtures/`

### 4.1 Record fixtures (JSON)

| file | records | shape | country values |
|---|---|---|---|
| `acronym_name1.json` | 1 | `{options, records}`; record keys `city, contact, country, name1, name2, name3, record_id, state, street, zip` | US |
| `company_with_name2.json` | 1 | as above | US |
| `fully_blank_name2_no_contact.json` | 1 | as above | US |
| `research_missing_name2_with_contact.json` | 1 | as above | US |
| `research_no_contact_name2_present.json` | 1 | as above | US |
| `research_wrong_name2_with_contact.json` | 1 | as above | US |
| `mixed_batch_10_records.json` | 10 | `{options: {max_concurrency: 3}, records}` | US 9, **CH 1** |
| `expected_outcomes.json` | — | expectations keyed by the six single-record fixture names | — |
| `issue_catalogue_coverage.json` | — | `{_comment, baseline, cases}` — the positive/near-miss cases the coverage gate reads (`tests/test_issue_catalogue_coverage.py:4-7`) | — |
| `dedup_v2_stress_200.json` | 200 | `{source, input_columns (78), order (200), rows (200), v1}` | — |
| `dedup_v2_real_model_report.json` | — | `{cache, differences, llm_calls, model, oracle, real, routing}` — output of `tools/dedup_v2_real_model_run.py` | — |

`mixed_batch_10_records.json` is the only fixture in the repository holding a non-US record:
`BSP_2000006`, "Novartis", Basel, `CH`.

`dedup_v2_stress_200.json` records its own source: workbook
`docs/thesis/dedup_STRESS_200_v1_enriched_dedup.xlsx`, sheet `Sheet`, sha256
`2921c26d…0fee46`, `generated_by tools/build_dedup_v2_fixture.py`, with `rows` = the
adjudicator's input and `v1` = the recorded v1 output of one `/api/dedup/file` run over exactly
those rows.

⚠ **The fixture's source path no longer exists.** The workbook was renamed
`docs/thesis/… → data/eval/…` in `ea6f9d9` (git records it as `R100`), and
`tools/build_dedup_v2_fixture.py:46` still points at the old path. The sha256 recorded in the
fixture matches `data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx` byte for byte, so the
fixture itself is intact and the tests that read it are unaffected — but `--check` and any
regeneration fail at this commit until the constant is updated.

### 4.2 Recorded evidence (the fixture cache in the repository)

`EVIDENCE_CACHE_DIR` defaults to `tests/fixtures` (`config.py:144`, `config.py:389-390`), and
`EvidenceCache.LAYOUT` maps seven namespaces onto six subdirectories — `page → page_reads`,
`wikidata → wikidata`, `serp → serp`, `fetch → fetch`, `llm → llm`, `ror`/`gleif` → `registry`
(`utils/cache.py:457-465`).

    $ git ls-files tests/fixtures | sed 's|tests/fixtures/||' | cut -d/ -f1 | sort | uniq -c | sort -rn
     611 wikidata
     166 page_reads
      37 dedup_v2_llm_cache
      12 ror_repro
       1 research_wrong_name2_with_contact.json
       1 research_no_contact_name2_present.json
       1 research_missing_name2_with_contact.json
       1 mixed_batch_10_records.json
       1 issue_catalogue_coverage.json
       1 fully_blank_name2_no_contact.json
       1 expected_outcomes.json
       1 dedup_v2_stress_200.json
       1 dedup_v2_real_model_report.json
       1 company_with_name2.json
       1 acronym_name1.json
       1 .gitkeep

| namespace | directory | files tracked |
|---|---|---|
| `page` | `tests/fixtures/page_reads/` | 166 |
| `wikidata` | `tests/fixtures/wikidata/` | 611 (360 `wikidata_search_*`, 251 `wikidata_entity_*`) |
| `serp` | `tests/fixtures/serp/` | **absent** |
| `fetch` | `tests/fixtures/fetch/` | **absent** |
| `llm` | `tests/fixtures/llm/` | **absent** |
| `ror`, `gleif` | `tests/fixtures/registry/` | **absent** |

Two further directories are not cache namespaces: `tests/fixtures/dedup_v2_llm_cache/` (37 files,
the adjudicator responses recorded by `tools/dedup_v2_real_model_run.py --record`,
`tools/dedup_v2_real_model_run.py:35`) and `tests/fixtures/ror_repro/` (12
`affiliation_*.json`, the recordings `scripts/ror_repro.py` replays).

⚠ **A frozen run against the repository's own default cache directory replays only page reads
and Wikidata.** Four of the six namespaces have no recorded entries at this commit, so under
`CACHE_FROZEN=true` every SERP query, every ROR/GLEIF lookup, every raw HTTP fetch and every LLM
call is a miss, recorded as `evidence-unavailable-frozen` (`utils/cache.py:110`,
`utils/cache.py:370-381`) and shipped without that evidence. `CACHE_FROZEN` "applies to every
namespace at once — freezing three of five sources would not freeze the run"
(`config.py:391-401`), which is exactly why the partial store cannot stand in for a frozen
evaluation.

### 4.3 The evidence caches that a frozen run actually needs

The six-namespace caches on this machine live under `logs/cache/`, which is **gitignored**
(`.gitignore:21`, `logs/`) and therefore not part of the repository:

    $ for ns in fetch llm page_reads registry serp wikidata; do printf "%-11s %6d\n" "$ns" \
        "$(find logs/cache -mindepth 2 -maxdepth 2 -type d -name "$ns" -exec ls -1 {} \; | wc -l)"; done
    fetch         5991
    llm           4036
    page_reads     443
    registry      6866
    serp          2120
    wikidata      1389

    $ find logs/cache -name '*.json' | wc -l
       20845

Twelve session directories (`cutu_suzu_1`…`cutu_suzu_7`, `grounded`, `log_A`, `log_B`, `log_C`,
`log_Z`), each with the full six-namespace layout. ⚠ **Every frozen replay the thesis relies on
depends on an artefact that is not in the repository and is not reproducible from it.** A reader
who clones this repository can run the pipeline live; they cannot reproduce a `--frozen` run, and
Pass 18's determinism and cost figures must state which cache they were taken against.
`scripts/cache_state.py` exists to make two gate lines comparable by recording entry count and a
sha256 over the sorted key list (`scripts/cache_state.py:1-12`), and is the mechanism by which a
cache can at least be *identified* in a report.

---

## 5 — Reference workbooks outside `data/eval/`

Read by the harness scripts by default, tracked at the repository root:

| file | sheet | rows | columns | country distribution |
|---|---|---|---|---|
| `testall100_SOLVED_REFERENCE_v1.xlsx` | Reference | 198 | 69 | US 198 |
| | Match Rules | 67 | 3 | — |
| | Cell Notes | 87 | 3 | — |
| | Method | 6 | 2 | — |
| `testall100_CLEAN_INPUT.xlsx` | Sheet1 | 99 | 45 | US 99 |
| `PresentationTestData.xlsx` | TestData_500 | 500 | 53 | US 416, DE 39, GB 34, USA 5, blank 5, "United States" 1 |
| | Oracle_Summary / Issue_Counts / Group_Totals / Dedup_Scoring_Oracle | 19 / 37 / 9 / 24 | 2 / 5 / 3 / 7 | — |
| `PresentationTestData_enriched_checked_v1.xlsx` | Sheet | 500 | 77 | US 416, DE 39, GB 34, USA 5, blank 5, "United States" 1 |
| `PresentationTestData_subset.xlsx` | TestData_500 | 23 | 53 | US 18, DE 4, GB 1 |

The `Reference` sheet holds 198 rows because it carries an `INPUT` row and an `EXPECTED` row per
record, paired on `Customer` (`eval/name_eval.py:5-6`) — 99 records.

`PresentationTestData.xlsx` is the only multi-country dataset in the repository. Its country
column is not normalised: `US`, `USA` and `United States` all appear, and five rows are blank.
`PresentationTestData_enriched_checked_v1.xlsx` carries six duplicate headers (`Comments`,
`Name 1`, `Name 2`, `Street 1`, `House Number`, `Street 2`).

---

## 6 — Recorded evaluation artefacts

### 6.1 `eval/out/` — 17 tracked files

| directory | commit | contents |
|---|---|---|
| `eval/out/327ee53/` | `327ee53` — **current** per `RUNS.md` | `S1`, `S4`, `S5`, `t100` enriched workbooks + `*_results.json` |
| `eval/out/f57782f/` | `f57782f` — superseded | `S1`, `S4` enriched + results |
| `eval/out/` (bare) | `d3a3cfc` — superseded | `S1`, `S4` enriched + results |
| `eval/out/RUNS.md` | — | the run record for all three |

`RUNS.md` records, per run set: the commit, that the working tree was clean at run time, the
evidence cache's entry count and key hash (`d3a3cfc` 5365 / `d801665da8fc`; `f57782f` 5397 /
`8d9caf4f8625`; `327ee53` 7134 / `0d01989dd9c7`), the mode (`--frozen`, 0 network calls), the
per-stratum row and frozen-miss counts, the `pytest -q` split, and the scoring rules used
(G7 excluded from the reduction metric; precision/recall over the annotation vocabulary only;
G6 rows kept and marked *persists by design*).

Three facts about it matter for Pass 18:

1. **It is a three-stratum evaluation, not five.** Its own heading says so: S1, S4 and S5 were
   run and scored; "S2 and S3 raw inputs are not on this machine".
2. **Its inputs were not in the repository.** S1/S4/S5 were run from
   `~/Downloads/demo_S{n}_…_100_v1 (1).xlsx`. ⚠ This contradicts the current `data/eval/`, which
   holds pre/post pairs for all five strata (§3.1) — the workbooks arrived after `RUNS.md` was
   written, and `RUNS.md` is stale with respect to them.
3. **Every number in it is at `d3a3cfc`, `f57782f` or `327ee53`**, all of which precede this
   commit. None of it is restated as current here.

`RUNS.md` also carries its own correction: both `pytest -q` lines originally recorded five
failures where the tree gives eight, with the totals unchanged — the origin of the
`tests/KNOWN_FAILURES.md` manifest.

### 6.2 `logs/runs/` — determinism artefacts

`determinism_S1.json`, `determinism_S1_f57782f.json`, `determinism_S1_327ee53.json`: the
`run_diff.py` reports for two frozen runs of S1 at each commit. Untracked (`.gitignore:21`).
`RUNS.md` reports all three as PASS with 0 rows differing and 0 network calls on run 2.

### 6.3 Other untracked run material

`logs/` and `handoff/runs/logs/` hold paired before/after workbooks and their JSON artefacts
(`base20`/`after20b`, `s2_before2`/`s2_after2`, `grounded/baseline`, `grounded/after`,
`grounded/after2`, `grounded/frozen`) plus `trace.jsonl` files. All are gitignored and none is
a dataset the thesis cites; they are the working material of the fix reports
(`scripts/fix_reports.py`).

---

## 7 — Internationalisation set

⚠ **NOT PRESENT.** No internationalisation evaluation set exists at this commit.

    $ grep -rn "I18N\|i18n" --include="*.py" --include="*.md" --include="*.json" . | grep -v "^./.git"
    docs/thesis-doc-prompt-v2.md:138:What exists to evaluate: scripts, fixtures, stress sets, I18N set. For every dataset in

The only match is the instruction asking for one. Every workbook in `data/eval/` is US-only
(§3). The nearest thing in the repository is `PresentationTestData.xlsx`, whose 500 rows include
39 `DE` and 34 `GB` records alongside 416 `US` (§5), and
`tests/fixtures/mixed_batch_10_records.json`, which contains one Swiss record (§4.1). Neither is
a designed I18N evaluation set, neither is stratified, and neither carries the
`expected_issue_codes` annotation the reduction metric needs.

The pipeline's country handling is nonetheless exercised: `utils.text_utils.country_to_iso_code`
normalises the country before it reaches a search provider or a cache key
(`utils/cache.py:751`), the ROR country guard and
`utils.domain_resolver.country_conflict` are country-conditional, and SerpAPI receives the
country as `gl` when `SERP_COUNTRY_LOCALISATION_ENABLED` is on (`config.py:355-359`). ⚠ None of
that is measured by any dataset in the repository.

---

## 8 — Warnings raised by this pass

| # | severity | warning | evidence |
|---|---|---|---|
| 07-1 | high | `S1_pre.xlsx` holds two `Issues` columns disagreeing on 56 of 100 rows; neither is the appended column | §3.2 |
| 07-2 | high | 100 of the 183 rows of `stress_200_scored.xlsx` carry seeded dummy scoring inputs | §3.3, `Dummy_Fill` sheet |
| 07-3 | high | `eval/dedup_eval.py` cannot read the stress workbooks: no `expected_cluster` / `expected_routing` column exists in `data/eval/` | §3.3, `eval/dedup_eval.py:3-5` |
| 07-4 | high | A frozen run against the default `EVIDENCE_CACHE_DIR` replays only 2 of 6 namespaces | §4.2, `utils/cache.py:457-465` |
| 07-5 | high | The caches that make a frozen replay possible are gitignored and not reproducible from the repository | §4.3, `.gitignore:21` |
| 07-6 | medium | `test-all-100-original.xlsx` and its enriched twin are column-misaligned on 24 of 99 rows | §3.4 |
| 07-7 | medium | The ten stratum workbooks record no generating commit, run id or settings | §3.1 |
| 07-8 | medium | `tools/build_dedup_v2_fixture.py:46` points at a path removed in `ea6f9d9` | §4.1 |
| 07-9 | medium | `eval/out/RUNS.md` states S2/S3/S5 inputs are unavailable; `data/eval/` now holds all five strata | §6.1 |
| 07-10 | medium | The five `pre` workbooks are not column-identical; index-based readers break on S1 and S2 | §3.2 |
| 07-11 | low | No I18N evaluation set exists | §7 |
| 07-12 | low | The workbooks name the country column `Country/Region Key`; the documentation prompt asks for `Country` | Appendix A |
| 07-13 | low | `scripts/issue_catalogue_census.py --write-oracle` writes into `PresentationTestData.xlsx` — the only harness script that modifies a repository file | `scripts/issue_catalogue_census.py:250`, `:274-275` |
| 07-14 | low | `stress_200_scored.xlsx::Run` records `fixture_cache off`: its 39 LLM calls went to the network and are not reproducible from the repository | §3.3 |

---

## Appendix A — commands and verbatim output

### A.1 Tree state and commit

    $ git status --porcelain
     M docs/thesis/00_INVENTORY.md
     M docs/thesis/01_TRACEABILITY.md
     M docs/thesis/02_ARCHITECTURE.md
     M docs/thesis/03_ALGORITHMS.md
     M docs/thesis/03b_EXEMPLARS.md
     M docs/thesis/04_PARAMETERS.md
     M docs/thesis/05_DATA_MODEL.md
     M docs/thesis/06_EXTERNAL_DEPS.md

    $ git rev-parse HEAD && git rev-parse --abbrev-ref HEAD && date -I
    86d173b8a4d715a619b0a2656986c145da7fa81e
    feature/llm-fixes
    2026-09-07

    $ python3 -c "import openpyxl; print('openpyxl', openpyxl.__version__)"
    openpyxl 3.1.5

### A.2 The inventory script

`eval_inventory.py`, run from the repository root. It opens each workbook read-only, and per
sheet reports data rows (fully blank rows excluded), header count, duplicate headers, the
country column and its distribution, and the distribution of the four annotation columns. It
looks for a header named `Country` first and falls back to `Country/Region Key`; **no workbook in
this repository has a column named `Country`** — the SAP field name `Country/Region Key` is used
throughout, which is warning 07-12.

    $ python3 eval_inventory.py data/eval/S1_pre.xlsx data/eval/S1_post.xlsx …
    == data/eval/S1_pre.xlsx
       sheet 'Sheet': 100 data rows, 42 columns, duplicate headers: ['Issues']
       country column 'Country/Region Key': US=100
       eval_set: S1=100
       category: academic_research=100
       src_state: CA=31, TX=28, NJ=10, MI=10, MA=9, FL=9, OH=3
       record_type_hint: research_institution=99, company=1
    == data/eval/S1_post.xlsx
       sheet 'Sheet': 100 data rows, 83 columns, duplicate headers: ['Flag Codes', 'Flag Reason', 'Issues']
       country column 'Country/Region Key': US=100
       eval_set: S1=100
       category: academic_research=100
       src_state: CA=31, TX=28, NJ=10, MI=10, MA=9, FL=9, OH=3
       record_type_hint: research_institution=99, company=1
    == data/eval/S2_pre.xlsx
       sheet 'Sheet': 100 data rows, 42 columns, duplicate headers: ['Issues']
       country column 'Country/Region Key': US=100
       eval_set: S2=100
       category: large_corporate=100
       src_state: TX=26, CA=21, OH=14, NJ=12, MA=11, FL=9, MI=7
       record_type_hint: company=99, government=1
    == data/eval/S2_post.xlsx
       sheet 'Sheet': 100 data rows, 84 columns
       country column 'Country/Region Key': US=100
       eval_set: S2=100
       category: large_corporate=100
       src_state: TX=26, CA=21, OH=14, NJ=12, MA=11, FL=9, MI=7
       record_type_hint: company=99, government=1
    == data/eval/S3_pre.xlsx
       sheet 'Sheet': 100 data rows, 42 columns
       country column 'Country/Region Key': US=100
       eval_set: S3=100
       category: government_labs=100
       src_state: CA=51, FL=15, TX=13, MI=7, MA=5, OH=5, NJ=4
       record_type_hint: government=80, company=20
    == data/eval/S3_post.xlsx
       sheet 'Sheet': 100 data rows, 84 columns
       country column 'Country/Region Key': US=100
       eval_set: S3=100
       category: government_labs=100
       src_state: CA=51, FL=15, TX=13, MI=7, MA=5, OH=5, NJ=4
       record_type_hint: government=80, company=20
    == data/eval/S4_pre.xlsx
       sheet 'Sheet': 100 data rows, 42 columns
       country column 'Country/Region Key': US=100
       eval_set: S4=100
       category: hospital_health=100
       src_state: TX=36, CA=25, FL=18, OH=8, MA=6, NJ=4, MI=3
       record_type_hint: hospital_health=93, government=4, company=3
    == data/eval/S4_post.xlsx
       sheet 'Sheet': 100 data rows, 85 columns
       country column 'Country/Region Key': US=100
       eval_set: S4=100
       category: hospital_health=100
       src_state: TX=36, CA=25, FL=18, OH=8, MA=6, NJ=4, MI=3
       record_type_hint: hospital_health=93, government=4, company=3
    == data/eval/S5_pre.xlsx
       sheet 'Sheet': 100 data rows, 42 columns
       country column 'Country/Region Key': US=100
       eval_set: S5=100
       category: smb_residual=100
       src_state: CA=33, TX=24, FL=13, MA=11, NJ=9, OH=6, MI=4
       record_type_hint: company=87, individual=11, government=2
    == data/eval/S5_post.xlsx
       sheet 'Sheet': 100 data rows, 84 columns
       country column 'Country/Region Key': US=100
       eval_set: S5=100
       category: smb_residual=100
       src_state: CA=33, TX=24, FL=13, MA=11, NJ=9, OH=6, MI=4
       record_type_hint: company=87, individual=11, government=2

### A.3 Stress sets and t100

    $ python3 eval_inventory.py data/eval/stress_200_pre.xlsx data/eval/stress_200_scored.xlsx \
        data/eval/dedup_STRESS_200_v1-verified.xlsx data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx \
        data/eval/test-all-100-original.xlsx "data/eval/test-all-100-original_enriched (4).xlsx"
    == data/eval/stress_200_pre.xlsx
       sheet 'Data': 183 data rows, 38 columns, duplicate headers: ['gt_expected_action']
       country column 'Country/Region Key': US=183
       category: academic_research=80, large_corporate=40, hospital_health=23, smb_residual=21, government_labs=19
       src_state: CA=81, TX=41, MA=27, FL=11, OH=11, MI=8, NJ=4
       sheet 'Clusters': 85 data rows, 11 columns
       category: academic_research=30, large_corporate=18, government_labs=14, hospital_health=13, smb_residual=10
       sheet 'Traps': 9 data rows, 6 columns
       sheet 'Method': 7 data rows, 2 columns
    == data/eval/stress_200_scored.xlsx
       sheet 'Sheet': 183 data rows, 117 columns
       country column 'Country/Region Key': US=183
       category: academic_research=80, large_corporate=40, hospital_health=23, smb_residual=21, government_labs=19
       sheet 'Dedup Debug': 183 data rows, 5 columns
       sheet 'Run': 14 data rows, 2 columns
       sheet 'Clusters': 85 data rows, 11 columns
       sheet 'Traps': 9 data rows, 6 columns
       sheet 'Method': 7 data rows, 2 columns
       sheet 'Reconciliation': 64 data rows, 2 columns
       sheet 'QLIC_Merge': 26 data rows, 3 columns
       sheet 'Dummy_Fill': 27 data rows, 3 columns
       sheet 'Issues': 20 data rows, 4 columns
    == data/eval/dedup_STRESS_200_v1-verified.xlsx
       sheet 'Data': 183 data rows, 34 columns, duplicate headers: ['gt_expected_action']
       country column 'Country/Region Key': US=183
       category: academic_research=80, large_corporate=40, hospital_health=23, smb_residual=21, government_labs=19
       src_state: CA=81, TX=41, MA=27, FL=11, OH=11, MI=8, NJ=4
       sheet 'Clusters': 85 data rows, 11 columns
       sheet 'Traps': 9 data rows, 6 columns
       sheet 'Method': 7 data rows, 2 columns
    == data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx
       sheet 'Sheet': 200 data rows, 87 columns, duplicate headers: ['Reasoning', 'Cluster ID', 'Central delivery block']
       country column 'Country/Region Key': US=200
       category: academic_research=84, large_corporate=43, hospital_health=26, smb_residual=24, government_labs=23
       src_state: CA=87, TX=43, MA=32, FL=11, OH=11, MI=8, NJ=8
       sheet 'Dedup Debug': 200 data rows, 4 columns
       sheet 'Clusters': 85 data rows, 11 columns
       sheet 'Traps': 9 data rows, 6 columns
       sheet 'Method': 7 data rows, 2 columns
    == data/eval/test-all-100-original.xlsx
       sheet 'Sheet1': 99 data rows, 50 columns
       country column 'Country/Region Key': US=99
    == data/eval/test-all-100-original_enriched (4).xlsx
       sheet 'Sheet': 99 data rows, 81 columns

*(The `eval_set` / `category` / `src_state` lines for the two `test-all-100-original` files are
omitted here because the workbooks are column-misaligned on 24 rows — §3.4 — so the values under
those headers are not the values those headers name.)*

### A.4 Row width, showing the misalignment

    $ python3 widths.py data/eval/test-all-100-original.xlsx Sheet1
    header cells: len=50 non-empty=41
    last-populated-column counts: {41: 75, 50: 24}

    $ python3 widths.py "data/eval/test-all-100-original_enriched (4).xlsx" Sheet
    header cells: len=81 non-empty=81
    last-populated-column counts: {72: 75, 81: 24}

### A.5 The `Issues` columns

    $ python3 issues_cols.py data/eval/S?_pre.xlsx data/eval/S?_post.xlsx
    data/eval/S1_pre.xlsx: 100 rows, 'Issues' at columns [2, 7], non-empty per column {2: 97, 7: 85}
    data/eval/S2_pre.xlsx: 100 rows, 'Issues' at columns [6, 41], non-empty per column {6: 95, 41: 95}
    data/eval/S3_pre.xlsx: 100 rows, 'Issues' at columns [41], non-empty per column {41: 95}
    data/eval/S4_pre.xlsx: 100 rows, 'Issues' at columns [41], non-empty per column {41: 94}
    data/eval/S5_pre.xlsx: 100 rows, 'Issues' at columns [41], non-empty per column {41: 97}
    data/eval/S1_post.xlsx: 100 rows, 'Issues' at columns [7, 82], non-empty per column {7: 79, 82: 79}
    data/eval/S2_post.xlsx: 100 rows, 'Issues' at columns [83], non-empty per column {83: 88}
    data/eval/S3_post.xlsx: 100 rows, 'Issues' at columns [83], non-empty per column {83: 74}
    data/eval/S4_post.xlsx: 100 rows, 'Issues' at columns [84], non-empty per column {84: 95}
    data/eval/S5_post.xlsx: 100 rows, 'Issues' at columns [83], non-empty per column {83: 95}

    $ python3 issues_agree.py data/eval/S1_pre.xlsx data/eval/S2_pre.xlsx data/eval/S1_post.xlsx
    data/eval/S1_pre.xlsx: cols 2/7 — identical 28, different 56, only col2 13, only col7 1, both empty 2
    data/eval/S2_pre.xlsx: cols 6/41 — identical 95, different 0, only col6 0, only col41 0, both empty 5
    data/eval/S1_post.xlsx: cols 7/82 — identical 79, different 0, only col7 0, only col82 0, both empty 21

Four of the 56 disagreeing S1 rows, verbatim:

    13361199 | col2: G2-NAME-012 || col7: G1-NAME-004; G2-NAME-012
    13147626 | col2: G1-ADDR-003; G5-NAME-002 || col7: G1-ADDR-003
    13047582 | col2: G4-ADDR-008 || col7: G2-NAME-012
    13334811 | col2: G1-ADDR-001; G1-ADDR-003; G5-NAME-001; G2-NAME-012 || col7: G1-ADDR-001; G1-ADDR-003; G2-NAME-012; G5-NAME-001

### A.6 Grain of the stress workbooks

    $ python3 grain.py "data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx::Sheet" \
        "data/eval/dedup_STRESS_200_v1-verified.xlsx::Data" \
        "data/eval/stress_200_scored.xlsx::Sheet" "data/eval/stress_200_pre.xlsx::Data"
    data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx::Sheet: 200 rows, 200 distinct Customer values
    data/eval/dedup_STRESS_200_v1-verified.xlsx::Data: 183 rows, 183 distinct Customer values
    data/eval/stress_200_scored.xlsx::Sheet: 183 rows, 183 distinct Customer values
    data/eval/stress_200_pre.xlsx::Data: 183 rows, 183 distinct Customer values

    enriched_dedup 200 | verified 183 | scored 183
    verified subset of enriched_dedup: True | missing: 0
    scored == verified: True
    in enriched_dedup only: 17

### A.7 Fixture source integrity

    $ shasum -a 256 data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx
    2921c26d35513066949916ecaf9431e1833b46b57fda98ef21832323680fee46  data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx

matching `tests/fixtures/dedup_v2_stress_200.json`'s recorded `source.sha256`.

    $ git log --oneline --diff-filter=ADR --name-status -- "*dedup_STRESS_200_v1_enriched_dedup.xlsx"
    ea6f9d9 Add new evaluation workbooks for stress testing
    R100	docs/thesis/dedup_STRESS_200_v1_enriched_dedup.xlsx	data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx
    8868908 Enhance deduplication logic with v2 features and address handling
    A	docs/thesis/dedup_STRESS_200_v1_enriched_dedup.xlsx

    $ grep -n "WORKBOOK = " tools/build_dedup_v2_fixture.py
    46:WORKBOOK = _ROOT / "docs" / "thesis" / "dedup_STRESS_200_v1_enriched_dedup.xlsx"

### A.8 Last-touching commit per dataset

    $ for f in data/eval/*.xlsx; do printf "%-52s %s\n" "$f" \
        "$(git log -1 --format='%h %ad %s' --date=short -- "$f")"; done
    data/eval/S1_post.xlsx                               eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S1_pre.xlsx                                eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S2_post.xlsx                               eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S2_pre.xlsx                                eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S3_post.xlsx                               eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S3_pre.xlsx                                eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S4_post.xlsx                               eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S4_pre.xlsx                                eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S5_post.xlsx                               eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/S5_pre.xlsx                                eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/dedup_STRESS_200_v1-verified.xlsx          ea6f9d9 2026-09-07 Add new evaluation workbooks for stress testing
    data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx    ea6f9d9 2026-09-07 Add new evaluation workbooks for stress testing
    data/eval/stress_200_pre.xlsx                        eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/stress_200_scored.xlsx                     eb924e6 2026-09-07 Add evaluation workbooks and the v2 thesis documentation prompt
    data/eval/test-all-100-original.xlsx                 ea6f9d9 2026-09-07 Add new evaluation workbooks for stress testing
    data/eval/test-all-100-original_enriched (4).xlsx    ea6f9d9 2026-09-07 Add new evaluation workbooks for stress testing

### A.9 Fixture directories

    $ for d in page_reads wikidata ror_repro dedup_v2_llm_cache; do \
        echo "tests/fixtures/$d: $(ls tests/fixtures/$d | wc -l) files"; done
    tests/fixtures/page_reads: 166 files
    tests/fixtures/wikidata: 611 files
    tests/fixtures/ror_repro: 12 files
    tests/fixtures/dedup_v2_llm_cache: 37 files

    $ ls tests/fixtures/wikidata | cut -d_ -f1-2 | sort | uniq -c | sort -rn
     360 wikidata_search
     251 wikidata_entity

    $ ls -d tests/fixtures/serp tests/fixtures/registry tests/fixtures/fetch tests/fixtures/llm
    ls: tests/fixtures/fetch: No such file or directory
    ls: tests/fixtures/llm: No such file or directory
    ls: tests/fixtures/registry: No such file or directory
    ls: tests/fixtures/serp: No such file or directory

---

Pass 07 complete: the harness is 20 scripts plus five suite gate modules; `data/eval/` holds sixteen
US-only workbooks (five 100-row stratum pairs, four stress workbooks at 183/200-row grain, one
99-row mixed pair) and `tests/fixtures/` holds eleven JSON fixtures, 777 recorded evidence files
covering two of the six cache namespaces, and 49 files in two stores that are not
namespaces; fourteen warnings are raised, of which the
load-bearing ones for Pass 18 are the ambiguous S1 baseline, the 100 synthetic scoring rows in
the stress set, the missing `expected_cluster` vocabulary, and the fact that no frozen replay is
reproducible from the repository alone.
