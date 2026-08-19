Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 7 — Evaluation harness

This document specifies every component of the repository that produces a metric, the exact
definition of each metric **as computed in code**, the commands that reproduce it end to end,
the datasets those commands read, and the threats to validity that are visible from the source.
It does not report any measured value: §7 is the results table with empty value cells, per the
pass specification.

## 0 · Method and evidence rules

Every claim carries a citation `path/file.py:LINE` or `path/file.py:LINE-LINE`, taken from the
cited body. Metric definitions are transcribed from the arithmetic in the source, not from the
docstring that describes it; where the two disagree the code is recorded and the discrepancy is
stated. Row counts in §4 were read from the files themselves with `openpyxl` at this commit; no
count is quoted from a docstring, a README, or another document. Constants are verbatim.

Two facts established in earlier passes are load-bearing here and are cross-referenced rather
than restated: the three issue paths do not all agree, and only `/issues` and `/issues/compare`
emit catalogue codes at all (`03_ALGORITHMS.md` Part H §2.3); and no external call in the system
is seeded, cached across runs, or replayable (`03_ALGORITHMS.md` Part K §§B.1–B.4).

No source, test, configuration, or data file was modified in producing this document. The one
command executed against repository data (§3.4) wrote its output outside the repository tree;
`git status --porcelain` after the run showed no new or modified tracked file.

---

## 1 · Inventory of metric-producing components

The sweep covered every module, script, test, and notebook in the repository. There are **no
notebooks** (`find . -name "*.ipynb"` outside `.venv/` returns nothing) and **no committed
results file** — no CSV, no `eval_report.json`, no coverage report. `.env` is git-ignored
(`.gitignore:9`) and so are `htmlcov/`, `.coverage`, `*.log` and `logs/` (`.gitignore:17-21`),
so no measurement output is under version control.

| ID | Component | Kind | Emits | Source |
|----|-----------|------|-------|--------|
| M-1 | `_build_comparison_xlsx` (`POST /issues/compare`) | HTTP route helper | Before/after issue-reduction report: 11 headline figures + per-code Before/After/Delta + per-record + remaining-issues sheets | `api/routes.py:417-515` |
| M-2 | `detect_file_issues` (`POST /issues`) | HTTP route | Per-row issue codes; one log-only aggregate ("N records, M with issues") | `api/routes.py:580-625`, log `:608-613` |
| M-3 | `eval/dedup_eval.py` | Offline CLI harness | Pairwise TP/FP/FN/precision/recall/F1, three business-risk counts with row ids, four election counts; JSON report | `eval/dedup_eval.py:160-256` |
| M-4 | `Orchestrator._build_summary` (`POST /enrich`, `/enrich/file`) | Pipeline aggregate | `EnrichmentSummary` — 22 batch counters incl. GLEIF/LEI telemetry and wall-clock | `enrichment/orchestrator.py:2611-2650`; model `api/models.py:430-453`; LEI counters `:826-836` |
| M-5 | `cluster_blocks` (`POST /api/dedup/cluster-block`, `/api/dedup/file`) | Pipeline aggregate | `DedupSummary` — 12 counters; plus log-only token and latency telemetry | `dedup/adjudicator.py:964-1011`; model `dedup/models.py:85-100` |
| M-6 | `build_summary` + `detect_issues` (`POST /api/dedup/score`, `/api/dedup/score/file`) | Pipeline aggregate | `ScoringSummary` — 9 counters + warning list; `Issues` sheet of typed inconsistencies | `dedup/scoring.py:1208-1244`, `:384-396`, `:454`; sheet `dedup/scoring_xlsx.py:305-315` |
| M-7 | `pytest` suite | Test harness | Pass/fail counts over 53 test files | `pytest.ini:1-3`; inventory `00_INVENTORY.md:334-407` |
| M-8 | `scripts/test_local.py` | Fixture harness | `pass_count` / `fail_count` over six JSON fixtures against `expected_outcomes.json` | `scripts/test_local.py:178-198` |
| M-9 | `scripts/verify_fixes.py` | Ad-hoc harness | `passed` / `failed` over six hand-written steps | `scripts/verify_fixes.py:29-228` |
| M-10 | `scripts/trace_website.py` | Diagnostic | Per-record, per-candidate counters (`num_results`, `results_returned`, rejection reason); no aggregate | `scripts/trace_website.py:112-149` |

`tests/test_dedup_eval.py` does not produce a metric over system output; it asserts M-3's
arithmetic against a workbook it builds in memory (`tests/test_dedup_eval.py:25-39`). It is
documented in §2.3.7 because it is the only executable specification of the metric definitions.

**Metrics that no component computes.** There is no precision, recall, accuracy, or error-rate
measurement anywhere for Phase 1 enrichment — not for record-type classification, ROR/GLEIF
match acceptance, website or department-domain resolution, address decomposition, or search-term
derivation. M-4 counts *how many records took which path*, never *how many took the right one*.
The only correctness-bearing metrics in the repository are M-1 (issue reduction, deterministic
rules) and M-3 (dedup clustering, against fixture columns). ⚠ MEASUREMENT REQUIRED for any
Phase-1 accuracy claim: it would require a labelled answer key per enriched field, which does
not exist in the repository (the `Oracle_*` sheets of §4.1 hold aggregate expectations and a
cluster-level key, not per-field labels).

---

## 2 · Metric definitions exactly as computed

### 2.1 M-1 — Issue-reduction report (`POST /issues/compare`)

Inputs are two `{record_id: [codes]}` maps produced by `_audit_upload` over the two uploads
(`api/routes.py:644-645`). `_audit_upload` excludes any row whose `record.record_id` is blank
and keeps only the first occurrence of a duplicated id via `setdefault`
(`api/routes.py:399-406`); `record_id` is `(customer or ecc_customer_number or "").strip()`
(`api/models.py:229-231`). Codes come from the deterministic detector only
(`api/routes.py:406`); the enrichment pipeline's own `address_issues` never reach any output
(`03_ALGORITHMS.md` Part H §2.3).

Population partition (`api/routes.py:433-435`):

- `matched_ids = [rid for rid in before_map if rid in after_map]` — insertion order of the
  before-map.
- `only_before`, `only_after` — ids in exactly one map.

**Every headline figure is computed over `matched_ids` only.** Records present in one file only
contribute to nothing except their own count (and, for enriched-only records, Sheet 3).

| Metric (sheet label, verbatim) | Numerator / value | Denominator | Excluded | Source |
|---|---|---|---|---|
| `Records matched (joined by id)` | `len(matched_ids)` | — | blank-id rows; duplicate-id repeats | `api/routes.py:433, 473` |
| `Records only in original` | `len(only_before)` | — | as above | `:434, 474` |
| `Records only in enriched` | `len(only_after)` | — | as above | `:435, 475` |
| `Total issues before` | `Σ len(before_map[rid])` over `matched_ids` — **occurrence count over the returned list**, not distinct codes | — | unmatched records entirely | `:450, 477` |
| `Total issues after` | `Σ len(after_map[rid])` over `matched_ids` | — | unmatched records entirely | `:451, 478` |
| `Issues resolved` | `Σ len(resolved)`, where `resolved = [c for c in ISSUE_CATALOGUE if c in bset - aset]` per matched record | — | codes present in both; unmatched records | `:447, 452, 479` |
| `Issues introduced` | `Σ len(introduced)`, `introduced = [c for c in ISSUE_CATALOGUE if c in aset - bset]` | — | as above | `:448, 453, 480` |
| `Net reduction` | `total_before - total_after` | — | — | `:465, 481` |
| `Reduction %` | `round(net / total_before * 100, 1)`; **`0.0` when `total_before == 0`**, including when issues were introduced | `total_before` | — | `:466, 482` |
| Per-code `Before` | `code_before[code]` from `Counter.update(bset)` — **per-record set membership**, so a code counts at most once per record | — | codes with `before == after == 0` are not emitted as rows | `:445, 454, 485-491` |
| Per-code `After` | `code_after[code]` from `Counter.update(aset)` | — | as above | `:455, 486` |
| Per-code `Delta` | `after_count - before_count` (negative = improvement) | — | — | `:490` |
| Sheet 3 row count | one row per `(code, rid)` for every `rid` in **`after_map`** still carrying `code`, catalogue order then sorted id | — | nothing — covers matched **and** enriched-only records | `:506-511` |

Two aggregations of the same quantity coexist: the headline totals are per-occurrence over the
returned lists (`:450-451`), the per-code table is per-record set membership (`:454-455`). They
coincide only because `detect_issues` set-projects its result before returning
(`enrichment/issue_detection.py:504-510`).

`ISSUE_CATALOGUE` has 37 entries; 2 are declared but never emitted and 1 more is emitted only
from an unreachable branch, so at most 34 distinct codes can appear in either column
(`03_ALGORITHMS.md` §1.1, Part H §§1.1–1.3).

### 2.2 M-2 — Issue census (`POST /issues`)

The route emits no aggregate to the client. The workbook is the input sheet plus one `Issues`
column holding `"; ".join(codes)` (`api/routes.py:366-370`). One aggregate is logged and
discarded: `len(records)` and `sum(1 for issues in issues_per_row if issues)` — records carrying
at least one code (`api/routes.py:608-613`). Unlike M-1, this path includes rows with no record
id (`03_ALGORITHMS.md` Part H §2.2), so the two paths' record populations differ on the same
file.

### 2.3 M-3 — Dedup evaluation harness (`eval/dedup_eval.py`)

#### 2.3.1 Row admission and normalisation (the shared denominator)

`load_scored_rows` (`eval/dedup_eval.py:97-142`):

1. Selects the **first worksheet whose header row contains a cell normalising to `customer` or
   `rowid`** (`:104-110`); raises `ValueError` if none. `_norm` keeps alphanumerics of the
   lowercased header (`:61-62`).
2. Maps headers through `_FIELD_BY_HEADER` (`:43-56`); the **first** column matching a field
   wins (`:115-117`). Recognised: `customer`/`rowid`→`row_id`, `expectedcluster`,
   `expectedrouting`, `clusterid`, `routing`, `isgoldenrecord`, `electionstatus`, `scorefinal`,
   `goldenrecordid`, `proposedgoldenid`, `scoredwithweightsversion`. **Any header not in this
   table is silently ignored, and any absent field is silently `None`** — a workbook missing the
   ground-truth columns loads without error (§3.4).
3. Skips fully blank rows (`:121-122`) and **rows with a blank `row_id`** (`:126-127`).
4. Casefolds `expected_routing`, `routing`, `election_status`; empty string becomes `None`
   (`:130-137`). `is_golden_record` is `True`/`False` only for a native bool or the literals
   `"true"/"1"/"false"/"0"`, otherwise `None` (`:72-84`). `score_final` is `None` when the cell
   does not parse as a float (`:87-94`).

`rows_evaluated = len(rows)` after these exclusions (`:249`).

#### 2.3.2 Pair construction

`_pairs_by_key(rows, key)` groups rows by the string value of `key`, **skipping rows whose value
is `None`**, and emits every unordered pair of `row_id`s within a group, sorted
(`eval/dedup_eval.py:145-157`). Blank keys therefore form no pairs.

#### 2.3.3 Pairwise metrics — the asymmetric populations

`pairwise_metrics` (`eval/dedup_eval.py:160-187`):

- **Ground-truth pairs**: `_pairs_by_key(truth_rows, "expected_cluster")` where
  `truth_rows = [r for r in rows if r["expected_routing"] in {"cluster", "manual_review"}]`
  (`:162-165`, `_CLUSTER_ROUTINGS` `:58`). Rows whose expected routing is `unique`, blank, or
  any unrecognised value are **excluded from the ground truth**.
- **Predicted pairs**: `_pairs_by_key(rows, "cluster_id")` over **all** loaded rows, with no
  routing filter (`:166`).

The two sides are therefore drawn from different populations: a predicted pair may join a row
that the ground truth never considered.

| Metric | Definition in code | Source |
|---|---|---|
| `true_positives` | `len(gt & pred)` | `:168` |
| `false_positives` | `len(pred - gt)` | `:169` |
| `false_negatives` | `len(gt - pred)` | `:170` |
| `precision` | `tp / (tp + fp)` if `tp + fp` else **`0.0`** | `:171` |
| `recall` | `tp / (tp + fn)` if `tp + fn` else **`0.0`** | `:172` |
| `f1` | `2·p·r / (p + r)` if `p + r` else `0.0` — computed from the **unrounded** p and r | `:173-177` |
| `ground_truth_pairs` | `len(gt)` | `:185` |
| `predicted_pairs` | `len(pred)` | `:186` |
| rounding | `round(·, 4)` applied to p, r, F1 only | `:182-184` |

The zero-guards are silent: an evaluation with no ground-truth column reports precision, recall
and F1 of `0.0` rather than failing (demonstrated in §3.4).

#### 2.3.4 Business-risk counts

Each is a list of `row_id`s, reported as `{"count": len(ids), "row_ids": sorted(ids)}`
(`eval/dedup_eval.py:206-213`).

| Metric | Predicate, verbatim | Source |
|---|---|---|
| `wrongful_block_candidates` | `expected_routing == "unique" and is_golden_record is False` | `:192-195` |
| `competing_goldens` | `expected_routing == "cluster" and routing == "unique"` | `:196-199` |
| `uncertainty_upgrades` | `expected_routing == "manual_review" and election_status == "proposed"` | `:200-204` |

`is_golden_record is False` is an identity test against `False`: a blank cell loads as `None`
(`:72-84`) and can never satisfy it. `dedup/scoring_xlsx.py` writes `None` into
`is_golden_record` for every `manual_review` row by design (`dedup/scoring_xlsx.py:294-298`), so
**no manual-review row can ever be counted as a wrongful block candidate**, whatever its
expected routing. `competing_goldens` uses `routing` (the clustering verdict column), not
`election_status`; `uncertainty_upgrades` uses `election_status`.

#### 2.3.5 Election counts

`election_metrics` (`eval/dedup_eval.py:216-240`) groups rows by non-`None` `cluster_id`
(`:218-221`).

| Metric | Definition in code | Source |
|---|---|---|
| `clusters` | `len(clusters)` — distinct non-blank `cluster_id` values | `:233` |
| `elections` | `sum(1 for r in rows if r["is_golden_record"] is True and r["cluster_id"])` — **counts rows, not clusters**; excludes blanked manual-review winners | `:223` |
| `manual_review_rows` | `sum(1 for r in rows if r["election_status"] == "manual_review")` | `:224` |
| `tiebreak_decided_clusters` | clusters where `len(scores) >= 2 and scores.count(max(scores)) >= 2`, over members with non-`None` `score_final` — i.e. the top score is shared, so the tie-break decided the winner | `:226-230, 236-239` |

#### 2.3.6 Report envelope

`evaluate` returns `source` (the path as passed), `rows_evaluated`, `weights_versions` =
`sorted({r["weights_version"] for r in rows if r["weights_version"]})` — more than one entry
means the workbook mixes rows scored under different weight tables (`:243-256`) — and the three
metric blocks. `main` prints the console table and writes the JSON to `--out`, default
`eval_report.json` **in the current working directory** (`:289-304`). `--out` is read as the
argument following the flag with no validation (`:297-298`).

#### 2.3.7 The executable specification of these definitions

`tests/test_dedup_eval.py` builds a 7-row workbook with eight headers (`:25-39`) and asserts
every metric exactly: `ground_truth_pairs == 4`, `predicted_pairs == 3`, `TP == 2`, `FP == 1`,
`FN == 2`, `precision == round(2/3, 4)`, `recall == 0.5` (`:61-73`); the three business-risk
entries with their row ids (`:76-83`); `clusters == 3`, `elections == 3`,
`manual_review_rows == 0`, one tie-break-decided cluster (`:86-94`); and the report key set
(`:97-105`). The fixture's own commentary names the intended failure each row encodes — FN plus
competing golden, wrongful block, bad-merge winner, upgrade plus tie (`:34-38`).

### 2.4 M-4 — Enrichment batch summary

`_build_summary` (`enrichment/orchestrator.py:2611-2650`) is a single pass over the result list.

- `total = len(results)`; `processing_time_ms` = wall clock from `time.perf_counter()` at batch
  start to summary construction (`:2614-2617`, measured `:789, 825`).
- Status counters partition every record exactly once: `enriched`, `verified`, `unresolved`,
  and `failed` as the `else` branch — so any status string other than the three named lands in
  `failed` (`:2618-2626`).
- `research_institution_count` / `company_count` count `record_type`; records of any other type
  (including unset) are counted in neither (`:2628-2631`).
- Tier counters key on `tier_used` and, for tier 2, on `tier2_mode`
  (`"2A_population"`, `"2A_verification"`, `"2B"`) (`:2633-2643`). Two of those three can never
  increment: Tier 2A Mode B is unreachable by construction and `run_tier2b` has no call site
  (`03_ALGORITHMS.md` §1.2).
- `contact_lookup_attempted` increments on `r.contact_used`; `contact_lookup_success` on
  `contact_used` **and** a status in `("enriched", "verified")` — success is attributed at
  record level, not to the contact lookup itself (`:2645-2648`).
- GLEIF/LEI counters are not derived from results; they are per-batch instrumentation copied in
  afterwards: `lei_attempts`, `lei_hits_exact`, `lei_hits_fuzzy`, `lei_misses`, `lei_errors`,
  and `tier1_lei_count = hits_exact + hits_fuzzy` (`:826-836`, incremented
  `:1648, 1652, 1672, 1675, 1679, 1681`, reset per batch `:754, 764`).

None of these is a correctness measure. `08_GAPS.md:92` records the consequence for a reader:
the summary cannot distinguish "no record needed the tier" from "the tier never ran".

### 2.5 M-5 — Dedup adjudication summary

`cluster_blocks` initialises `DedupSummary(blocks=len(blocks), rows_in=len(rows))` and sums the
per-block stats (`dedup/adjudicator.py:965-989`): `distinct_signatures`, `clusters`,
`rows_clustered`, `rows_unique`, `rows_manual_review`, `llm_calls`, `errors`. Three residue
telemetry fields are assigned after the loop: `candidates_generated`, `rejected_with_reasoning`,
`candidate_cap_exceeded_blocks` = the count of blocks whose boolean cap flag was set
(`:986-993`). Field list at `dedup/models.py:85-100`.

Four further quantities are **logged only** and appear in no response: `total_prompt_tokens`,
`total_completion_tokens`, their sum, `total_latency_ms`, plus `candidates_by_rule` as a dict
(`:984-987, 996-1011`). Any cost or token analysis must be recovered from the log stream, which
is git-ignored (`.gitignore:19-21`).

### 2.6 M-6 — Scoring and election summary

`build_summary` (`dedup/scoring.py:1208-1244`) over the elected result rows:

- `rows_in = len(results) + errors`, where `errors` is passed in by the caller — the count of
  rows skipped for a blank `Customer` cell in the file path (`dedup/scoring_xlsx.py:243-246`),
  and `0` on the JSON path (`api/routes.py:933`).
- `rows_with_warnings` — rows with a non-empty `warnings` list (`:1223-1224`).
- `rows_unique` — `election_status == "unique"`; these rows `continue`, so they are counted in
  no other bucket (`:1225-1227`).
- `rows_manual_review` — `election_status == "manual_review"` (`:1228-1229`).
- `rows_elected` / `rows_duplicates` — partition of the non-unique rows by `is_golden_record`;
  a lone row flagged `manual_review` with `cluster_id is None` still counts as elected
  (`:1230-1237`).
- `clusters` = distinct non-`None` `cluster_id`; `all_blocked_clusters` = distinct cluster ids
  all of whose counted rows carried `election_status == "manual_review"` (`:1238-1243`).

The companion `Issues` sheet lists one row per detected inconsistency, typed from the eight
`ISSUE_TYPES` (`dedup/scoring.py:403-412`, emitted `:454`, written
`dedup/scoring_xlsx.py:305-315`). The sheet is rebuilt from scratch on every run (`:308-309`).

### 2.7 M-7 to M-9 — Harness pass/fail counts

- **M-7** `pytest` reports the standard summary line over `testpaths = tests`
  (`pytest.ini:1-3`). The run recorded at this commit is `3 failed, 1019 passed, 12 warnings in
  28.44s`, with the three failures named in `00_INVENTORY.md:336-343`.
- **M-8** `scripts/test_local.py` compares six fields per fixture — `tier_used`, `tier2_mode`,
  `contact_used`, `flag_for_review`, `enrichment_status`, and conditionally `name2_changed` —
  against `tests/fixtures/expected_outcomes.json`, prints
  `"Results: {pass_count} passed, {fail_count} failed out of {len(fixtures)}"` and exits 1 on
  any failure (`scripts/test_local.py:110-126, 181-198`). A fixture with **no** entry in
  `expected_outcomes.json` is counted as a **pass** ("No expected outcome defined — skipped
  validation", `:104-105`).
- **M-9** `scripts/verify_fixes.py` increments `passed`/`failed` over six independent steps
  (LLM connectivity, a live ROR query, the affiliation-string builder, a type-set assertion,
  the empty-string guard, one end-to-end `/enrich` call) and prints
  `"══ Results: {passed} passed, {failed} failed ══"` (`:29-228`). Steps 1, 2 and 6 require live
  network and a running server; the script exits 0 regardless of the counts.

---

## 3 · Reproduction

### 3.0 Prerequisites common to every command

Python dependencies: `pip install -r requirements.txt -r requirements-dev.txt`
(`requirements.txt:1-14`, `requirements-dev.txt:1-5`). The commands below use the repository's
own interpreter path on the documented platform; substitute as appropriate.

Required environment variables (`config.py:78-81`) — `validate_env` warns and continues rather
than raising, so the service starts without them and fails at the first LLM call
(`config.py:122-135`):

```
AZURE_OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT
```

Optional variables and their defaults are the verbatim `OPTIONAL_VARS_WITH_DEFAULTS` table
(`config.py:83-119`, reproduced in `03_ALGORITHMS.md` Part K §B.1). The ones that change a
measurement are:

| Variable | Default | Effect on a measurement |
|---|---|---|
| `MOCK_EXTERNAL_CALLS` | `false` | `true` swaps in `tests/mocks/*` for every external client (`api/routes.py:64-72, 673-677`; `enrichment/orchestrator.py:730-743`) — the run then measures the mocks |
| `SERPAPI_KEY` | unset | unset selects DuckDuckGo instead of SerpAPI, changing the result set (`config.py:137-145`; `enrichment/orchestrator.py:770-779`) |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.4` | changes the model behind all 16 Phase-1 call sites |
| `ROR_CONFIDENCE_THRESHOLD` | `0.8` | Tier 1 acceptance |
| `LEI_LOOKUP_ENABLED` | `true` | disables the GLEIF branch |
| `CONFIDENCE_MERGE_THRESHOLD` | `0.95` | how many clusters are demoted to `manual_review` at election |
| `NAME_CANDIDATE_THRESHOLD` / `TOKEN_CANDIDATE_THRESHOLD` / `MAX_CANDIDATES_PER_BLOCK` | `0.85` / `0.6` / `50` | residue nomination volume |
| `DEDUP_REASONING_EFFORT` / `DEDUP_MAX_RETRIES` | `low` / `3` | dedup LLM behaviour (`dedup/llm.py:122-123`) |
| `SIG_PARTITION_THRESHOLD` / `DEDUP_MAX_CONCURRENCY` | module defaults | block partitioning and concurrency (`dedup/adjudicator.py:948-951`) |

`.env` is loaded by the scripts that call `load_dotenv()` (`scripts/verify_fixes.py:24-26`) and
by `scripts/debug_ucsf.py:22-29`; the API itself reads `os.environ` directly
(`config.py:150-213`).
`.env` is git-ignored (`.gitignore:9`), so no key material is in the repository.

Start the service (both file endpoints and the JSON endpoints are on the same app):

```powershell
cd C:\Users\apoorva.ajay\Downloads\ApoorvaThesis\ApoorvaThesis\enrichment_api
.venv\Scripts\python.exe main.py      # uvicorn api.app:app on 0.0.0.0:8000 (main.py:6-8)
```

### 3.1 M-1 — Issue-reduction report

Multipart field names are `original` and `enriched` and are not interchangeable
(`api/routes.py:630-631`); both must be `.xlsx`/`.xlsm` and non-empty (`api/routes.py:384-393`).

```powershell
curl.exe -X POST http://localhost:8000/issues/compare `
  -F "original=@PresentationTestData.xlsx" `
  -F "enriched=@PresentationTestData_enriched_checked_v1.xlsx" `
  -o issue_reduction_report.xlsx
```

Output: a three-sheet workbook, `Summary` / `Per Record` / `Remaining Issues`
(`api/routes.py:468-511`), returned as `issue_reduction_report.xlsx`
(`api/routes.py:655-664`). No environment variable is required: the path performs no LLM,
network, or orchestrator call (`03_ALGORITHMS.md` Part H, non-determinism notes).

**The `enriched` argument above is the pre-existing checked workbook, not a fresh pipeline
output.** To produce the enriched side from the pipeline instead:

```powershell
curl.exe -X POST "http://localhost:8000/enrich/file?max_concurrency=5" `
  -F "file=@PresentationTestData.xlsx" -o enriched.xlsx
```

(`api/routes.py:518-521`; the query parameter is bounded `ge=1, le=20`.) This path requires the
two Azure variables and performs live ROR, GLEIF, SERP, page-fetch and LLM calls unless
`MOCK_EXTERNAL_CALLS=true`. §5 states why the two enriched candidates are not interchangeable.

### 3.2 M-2 — Per-row issue census

```powershell
curl.exe -X POST http://localhost:8000/issues -F "file=@PresentationTestData.xlsx" `
  -o PresentationTestData_issues.xlsx
```

(`api/routes.py:580-625`; output name is `<stem>_issues.xlsx`, `:617-624`.) The aggregate
"N records, M with issues" appears only in the log (`:608-613`).

### 3.3 M-3 — Dedup evaluation, full chain

The harness scores a workbook that already carries the clustering and election columns. The
production chain is `/enrich/file` → `/api/dedup/file` → `/api/dedup/score/file`
(`api/routes.py:263-266`), and each stage preserves the other sheets so the last stage still
sees `Weights` (`api/routes.py:260-282`; `dedup/scoring_xlsx.py:1-7`).

```powershell
# 1 — enrichment (live external calls unless MOCK_EXTERNAL_CALLS=true)
curl.exe -X POST "http://localhost:8000/enrich/file?max_concurrency=5" `
  -F "file=@PresentationTestData.xlsx" -o step1_enriched.xlsx

# 2 — clustering (dedup LLM; block ids derived when no Block ID column exists)
curl.exe -X POST http://localhost:8000/api/dedup/file `
  -F "file=@step1_enriched.xlsx" -o step2_dedup.xlsx

# 3 — scoring + golden-record election (deterministic, no LLM)
curl.exe -X POST http://localhost:8000/api/dedup/score/file `
  -F "file=@step2_dedup.xlsx" -o step3_scored.xlsx

# 4 — metrics
.venv\Scripts\python.exe -m eval.dedup_eval step3_scored.xlsx --out eval_report.json
```

Column requirements read from the code:

- Step 2 accepts SAP headers or snake_case names via `_DEDUP_HEADER_ALIASES`
  (`api/routes.py:688-707`); `row_id` is mandatory, everything else optional
  (`dedup/models.py:29-46`). No `Block ID` column exists in the repository datasets (§4), so
  `block_id` is derived from the normalised `(country, postal_code, street, house_no)`
  (`dedup/models.py:30-36`; `dedup/signatures.py:95-99`). It appends `Cluster ID`, `Routing`,
  `LLM Flag`, `Confidence`, `Reasoning` and a `Dedup Debug` sheet (`api/routes.py:744-746`).
- Step 3 requires a sheet with a `Customer` header (`dedup/scoring_xlsx.py:114-120`) and reads
  the input columns of `INPUT_HEADERS` (`:35-53`) plus the eight `SF_ID_*` slots (`:56-59`). It
  prefers the `Routing` + `Cluster ID` pair and falls back to
  `expected_routing` + `expected_cluster`, never mixing the two (`:147-158`). A `Weights` sheet
  overrides `dedup/weights.json` wholesale or is rejected wholesale (`:199-213`).
- Step 4 reads `Customer`, `expected_cluster`, `expected_routing`, `Cluster ID`, `Routing`,
  `is_golden_record`, `election_status`, `score_final`, and
  `scored_with_weights_version` (`eval/dedup_eval.py:43-56`).

`--out` defaults to `eval_report.json` **relative to the working directory**
(`eval/dedup_eval.py:296-298`); with no argument at all the module prints its docstring and
exits 2 (`:291-293`).

### 3.4 Executed check: the chain cannot be closed with repository data

The harness's two ground-truth columns, `expected_cluster` and `expected_routing`, are **not
present in any workbook in the repository** (§4.1, §4.2 — verified against the header rows).
Running the harness against the dataset therefore does not fail; it loads all rows and reports
zeros. Executed at this commit, writing outside the repository tree:

```powershell
.venv\Scripts\python.exe -m eval.dedup_eval PresentationTestData.xlsx --out <scratch>\eval_report.json
```

exits 0, reports `rows evaluated: 500`, and returns `precision 0.00 recall 0.00 F1 0.00`,
`TP 0 FP 0 FN 0 (GT pairs 0, predicted 0)`, all three business-risk counts `0`, and all four
election counts `0`. This is the zero-guard behaviour of `:171-177`, not a measurement of the
system. **Reproducing any non-trivial value of M-3 requires first authoring the
`expected_cluster` / `expected_routing` columns**, which is a labelling task, not a command; the
cluster-level expectations that do exist are in the `Dedup_Scoring_Oracle` sheet in a different
shape (§4.1) and would have to be projected onto per-row columns. ⚠ MEASUREMENT REQUIRED.

### 3.5 M-4 to M-6 — Pipeline summaries

The summaries are fields of the JSON responses; the file endpoints return only workbooks, so
the summary must be taken from the JSON route or from the log line.

```powershell
# M-4: EnrichmentSummary in the response body
curl.exe -X POST http://localhost:8000/enrich -H "Content-Type: application/json" `
  -d "@tests/fixtures/mixed_batch_10_records.json"

# M-5: DedupSummary in the response body (JSON in / JSON out; never reads files)
curl.exe -X POST http://localhost:8000/api/dedup/cluster-block -H "Content-Type: application/json" -d "@block.json"

# M-6: ScoringSummary in the response body
curl.exe -X POST http://localhost:8000/api/dedup/score -H "Content-Type: application/json" -d "@scoring.json"
```

`api/routes.py:88-107, 802-830, 896-943`. No JSON request body for `/api/dedup/cluster-block`
or `/api/dedup/score` exists in the repository — `tests/fixtures/` holds only `/enrich` payloads
(§4.3) — so `block.json` and `scoring.json` above must be authored from `DedupRequest`
(`dedup/models.py:49-56`) and `ScoringRequest`. M-5's token and latency telemetry is reachable
only through the `dedup_request` log record (`dedup/adjudicator.py:996-1011`).

### 3.6 M-7 to M-9 — Harnesses

```powershell
.venv\Scripts\python.exe -m pytest -q                       # M-7, no env vars needed
.venv\Scripts\python.exe -m pytest --cov=. --cov-report=term-missing   # coverage (pytest-cov>=5.0.0)
.venv\Scripts\python.exe scripts\test_local.py --mock       # M-8, offline; --live for real APIs
.venv\Scripts\python.exe scripts\verify_fixes.py            # M-9, needs main.py running + network
.venv\Scripts\python.exe scripts\trace_website.py           # M-10, forces WEBSITE_TRACE=true
```

`scripts/test_local.py` starts its own server on port 8765 in a background thread and defaults
to mock mode (`:25-26, 41-55, 162-167`). `scripts/verify_fixes.py` sets `ENV=local`, calls
`load_dotenv()`, and expects a server on port 8000 (`:23-26, 176-179`).
`scripts/trace_website.py` sets `WEBSITE_TRACE=true` before `Settings` is constructed, uses a
fresh unshared `BatchCache` per record so no cached SERP result can mask a live one, and writes
`logs/website_trace.json` (`:32, 97, 164-173`) — a git-ignored path (`.gitignore:19-21`). Its
default record set is six hard-coded names, three "failing" and three controls (`:46-56`).

---

## 4 · Datasets

Row counts below were read from the files at this commit. "Data rows" excludes the header row;
"non-blank" counts rows with at least one non-empty cell.

### 4.1 `PresentationTestData.xlsx` — the pre-enrichment dataset

Repository root, committed, 143,252 bytes. Five sheets:

| Sheet | Rows incl. header | Non-blank | Data rows | Columns | Content |
|---|---:|---:|---:|---:|---|
| `TestData_500` | 501 | 501 | **500** | 53 | SAP master-data records; the active sheet, hence the one `_parse_xlsx` reads (`api/routes.py:184`) |
| `Oracle_Summary` | 20 | 20 | 19 | 2 | `Metric` / `Value` answer key |
| `Issue_Counts` | 39 | 39 | 38 | 5 | `Group` / `Group Name` / `Code` / `Rule` / `Count` — 36 code rows |
| `Group_Totals` | 8 | 7 | 6 | 3 | issue instances per G-group |
| `Dedup_Scoring_Oracle` | 25 | 25 | 24 | 7 | `Cluster` / `Size` / `Members` / `Expected Winner (Customer)` / `Winner Name` / `Winner Score (illustrative)` / `Why` — 12 merge clusters and 10 must-not-merge guardrails |

Header row of `TestData_500`: `Customer`, `ECC Customer Number`, `Central Deletion Flag`,
`Comments`, `Account group`, `Company Code`, `Sales Organization`, `Distribution Channel`,
`Division`, `Name 1`…`Name 4`, `Contact`, `Street 1`, `House Number`, `Street 2`…`Street 5`,
`PO Box`, `Country/Region Key`, `Postal Code`, `City`, `Region`, `Language Key`,
`Reconciliation acct`, `Tax Jurisdiction`, `Central delivery block`, `Delivery Priority`,
`Shipping Conditions`, `Delivering Plant`, `Created On`, `Created By`, `VAT Registration No.`,
`Search Term 1`, `Search Term 2`, `Terms of Payment`, `Sales_Order_Last_Used`,
`Sales_Order_Total_Count`, `Sales_Order_Partner_Last_Used`,
`Sales_Order_Partner_Total_Count`, `Equipment_Total_Count`, `SleepingCustomer`,
`CustomerStatus`, `SF_ID_Biosystems`, `SF_ID_AXS`, `SF_ID_3`…`SF_ID_8`.

Properties that bear on the metrics, read from the file:

- **No `expected_cluster`, `expected_routing`, `Block ID`, `Cluster ID`, `Routing`, or any
  `score_*` column exists.** The only header containing "block" is `Central delivery block`.
- The `Comments` column is **empty in all 500 rows**, so no per-row cluster or routing tag is
  carried there either.
- All 500 `Customer` values are distinct. Their cell types are **mixed `int` and `str`**; the
  parser coerces every cell with `str(cell).strip()` (`api/routes.py:211`), and no value is
  float-typed, so no `"1001.0"`-style rendering arises for this file.

**Sample selection.** The `Oracle_Summary` sheet states the construction intent verbatim:
`"Scoring values calibrated to the US Qlic report distribution. Oracle columns = ground-truth
answer key."` It declares the composition of the 500 rows — clean vs issue-bearing records,
records "using REAL source data", records with a department, with a dept-domain e-mail, ALL-CAPS
names, dedup clusters, Salesforce links, the routing split, and the country split. No sampling
frame, extraction query, date, or source system is recorded anywhere in the repository. Pass 3b
reached the same conclusion independently and marked it
⚠ UNVERIFIED — whether any row derives from a production SAP extract, noting that many rows
share filler values (`MAIN ST`, house number `100`, tax jurisdiction `1200000000`) consistent
with a demonstration dataset (`03b_EXEMPLARS.md:26-33`).

⚠ The `Issue_Counts` sheet is titled "all 36 codes" and `Oracle_Summary` claims
`Distinct issue codes covered: 36/36`. The catalogue in code declares **37** codes, of which at
most 34 can ever be observed in detector output (`03_ALGORITHMS.md` §1.1). The oracle's
expectation is therefore not satisfiable by the implemented detector, independent of the data.
This is recorded as a code↔data discrepancy for `08_GAPS.md`.

### 4.2 `PresentationTestData_enriched_checked_v1.xlsx` — the post-enrichment side

Repository root, committed, 130,637 bytes. Sheet `Sheet` (active): 501 rows incl. header,
**500 data rows**, **77 columns**. The four `Oracle_*` sheets are carried over unchanged with
identical row counts to §4.1.

- All 500 `Customer` values are distinct, all of cell type `str`, and the set is **identical to
  the 500 ids of `TestData_500`** when both are compared as stripped strings. The join key is
  therefore total for this pair.
- **Six header names occur twice**: `Comments` (columns 3 and 9), `Name 1` (10 and 12),
  `Name 2` (11 and 13), `Street 1` (21 and 24), `House Number` (22 and 25), `Street 2`
  (23 and 26) — 0-based indices. The earlier column of each pair holds the original value and
  the later the enriched value (`03b_EXEMPLARS.md:63-73`). §5 gives the consequence.
- The column set is a **superset** of the original's, not a different set: computed through the
  repository's own `_present_fields` (`api/routes.py:144-158`), the original file maps to 37
  model fields and this file to 39; the two added fields are `care_of` and `email`; **no field
  present in the original is absent here**.

⚠ The filename records a manual checking step (`_checked_v1`) and no run manifest, log, commit
message, or configuration snapshot in the repository states which code version, model
deployment, or settings produced it. It is also **not a shape `_build_output_xlsx` can emit**:
`RESPONSE_COLUMNS` is a dict with unique values and `_passthrough_headers` de-duplicates against
it (`api/output_columns.py:22-89`; `api/routes.py:285-303, 323-333`), so a pipeline-produced
workbook never repeats a header. The duplicated columns were added outside the pipeline.

### 4.3 Other repository data

| Path | Count read from file | Role |
|---|---|---|
| `PresentationTestData_subset.xlsx`, sheet `TestData_500` | 24 rows incl. header → **23 data rows**, 53 columns (same header row as §4.1) | subset of the 500; no selection rule recorded |
| `tests/fixtures/mixed_batch_10_records.json` | `records`: **10** | `/enrich` batch payload |
| `tests/fixtures/acronym_name1.json` | `records`: **1** | single-record `/enrich` payload |
| `tests/fixtures/company_with_name2.json` | `records`: **1** | as above |
| `tests/fixtures/fully_blank_name2_no_contact.json` | `records`: **1** | as above |
| `tests/fixtures/research_missing_name2_with_contact.json` | `records`: **1** | as above |
| `tests/fixtures/research_no_contact_name2_present.json` | `records`: **1** | as above |
| `tests/fixtures/research_wrong_name2_with_contact.json` | `records`: **1** | as above |
| `tests/fixtures/expected_outcomes.json` | **6** top-level keys | M-8's answer key; keyed by fixture filename stem (`scripts/test_local.py:101-105`) |

`expected_outcomes.json` has entries for six fixtures; `FIXTURE_FILES` also lists six
(`scripts/test_local.py:31-38`). `mixed_batch_10_records.json` is in neither list and is used
only by tests and by exemplar documentation.

No dataset is split into tuning and evaluation partitions anywhere in the repository; there is
one dataset, used whole.

---

## 5 · Before and after: how the two sides are produced, and whether they are symmetric

### 5.1 The mechanism

Both sides run **the same function on the same code path**: `_audit_upload(original)` and
`_audit_upload(enriched)` (`api/routes.py:644-645`), each performing extension guard → empty
guard → `_parse_xlsx` → `_rows_to_records` → `_present_fields` → `detect_issues(record, present)`
per row (`api/routes.py:384-406`). There is no separate "before" detector and no separate
"after" detector, and the enrichment pipeline contributes no codes to either side: `/enrich`
never calls `detect_issues`, and the internal `address_issues` list it does compute is silently
discarded when the result dict is materialised as `EnrichmentResult`
(`03_ALGORITHMS.md` Part H §2.3, the Pass 3 amendment (d) finding). **A before/after comparison
is only meaningful when the enriched side is re-audited as a file by the same deterministic
detector**, which is what `/issues/compare` does.

At the level of the code, therefore, normalisation is identical on both sides. The asymmetries
are not in the code path; they are in what the two *files* present to it.

### 5.2 Asymmetry 1 — column-presence gating (does not fire for this file pair)

`detect_issues` gates the seven `G2-VAL-*` required-field rules on `present_fields`
(`enrichment/issue_detection.py:129-137, 330-334`), so a column absent from a file is never
judged missing in that file. The route docstring states this is deliberate — "the before/after
counts stay apples-to-apples" (`api/routes.py:640-642`) — but Pass 3 recorded that it makes
"Issues resolved" conflate a filled value with a dropped column
(`03_ALGORITHMS.md` `_build_comparison_xlsx` §7).

For the specific pair in §4.1–4.2 this hazard does **not** materialise in the dangerous
direction: computed with the repository's own `_present_fields`, the enriched file's field set
is a strict superset of the original's (37 → 39 fields; added: `care_of`, `email`; removed:
none). All seven gated fields — `name_1`, `postal_code`, `tax_jurisdiction`, `region`,
`language_key`, `search_term_1`, `country_region_key` — are present on both sides, so every
`G2-VAL-*` rule is evaluated on both sides. The two added columns are not gated by any rule:
`care_of` and `email` are read unconditionally where used (`enrichment/issue_detection.py:365`).

This conclusion is specific to this file pair. Any other enriched export that drops a column
re-opens the hazard, and the report has no field that would reveal it.

### 5.3 Asymmetry 2 — duplicated headers on the enriched side (does fire)

`_parse_xlsx` builds each row as a dict keyed by the **header string**, assigning only non-empty
values (`api/routes.py:207-213`). With a header that occurs twice, the later column overwrites
the earlier — **unless the later cell is empty, in which case the earlier value survives**. The
original workbook has no duplicated header; the enriched workbook has six (§4.2). Counted from
the enriched file:

| Duplicated header | Enriched column non-blank | Rows where the enriched column is blank and the original is not — the original value is what reaches the detector |
|---|---:|---:|
| `Name 1` (cols 10 / 12) | 470 | **25** |
| `Name 2` (cols 11 / 13) | 165 | **12** |
| `Street 1` (cols 21 / 24) | 486 | **14** |
| `Street 2` (cols 23 / 26) | 15 | **26** |
| `House Number` (cols 22 / 25) | 221 | 0 |
| `Comments` (cols 3 / 9) | 21 | 0 |

For those rows the "after" side is audited against pre-enrichment field values. The effect is
one-directional — it can only make the after side look more like the before side, understating
both resolution and introduction on the affected fields — and it is invisible in the report,
which has no column that distinguishes an enriched value from an inherited one.

Note that `_rows_to_records`' documented "first non-empty value wins" rule (`api/routes.py:241-246`)
is **not** what governs here: that rule applies to distinct header strings normalising to the
same field. For byte-identical duplicate headers the dict overwrite in `_parse_xlsx` has already
chosen the value, and it chooses the last non-empty one. The two rules select opposite ends.

### 5.4 Asymmetry 3 — join population

`_audit_upload` drops rows with a blank `record_id`, counting and logging them
(`api/routes.py:399-413`), and keeps only the first row of a duplicated id
(`setdefault`, `:406`). `_build_comparison_xlsx` then computes every headline figure over
`matched_ids` alone (`:433, 442`). For the pair in §4 this costs nothing — all 500 ids are
non-blank, distinct, and identical across the two files (§4.2) — but the report's
"Records only in …" lines are the sole indication if it ever does.

### 5.5 Statement

**The two sides are symmetric in code and asymmetric in data.** Identical parsing, identical
record construction, identical column-presence computation, and one identical detector call are
applied to both uploads; §5.2 and §5.4 are inert for the committed file pair; §5.3 is not — for
between 12 and 26 of 500 rows per field, depending on the field, the "after" audit reads a
pre-enrichment value because the enriched column is blank and its duplicated twin is not. A
before/after comparison run on this pair is therefore not a clean measurement of the pipeline,
and the discrepancy is a property of the checked workbook's layout, not of the pipeline. Two
remedies are visible from the code, neither applied: produce the enriched side with
`/enrich/file` (which cannot emit duplicate headers, §4.2), or strip the six original columns
from the checked workbook before uploading it.

---

## 6 · Threats to validity visible from the code

### 6.1 Leakage between tuning and evaluation data

- **There is one dataset and no split.** No train/test, dev/eval, or holdout partition exists
  anywhere in the repository (§4). Every threshold in `04_PARAMETERS.md` and every weight in
  `dedup/weights.json` is applied to the same 500 rows any metric would be computed on.
- **The dataset is calibrated to the scoring model it would be used to evaluate.**
  `Oracle_Summary` states `"Scoring values calibrated to the US Qlic report distribution"` and
  supplies the expected routing split and cluster inventory (§4.1). A dataset constructed so
  that the scoring criteria discriminate on it cannot also serve as an independent test of those
  criteria.
- **The weight values are unevidenced outside the file itself.** `04_PARAMETERS.md:442-445`
  records that the transcripts establish only `customer_status: active = 10`; every other band
  value is evidenced by `dedup/weights.json` alone, and the file's own header comment flags
  `combined_presence_bonus`, the `sales_order_partner_count` tiers and `account_group DRIT` as
  `UNCONFIRMED` (`dedup/weights.json:2`). Whether those numbers were fitted to this dataset is
  not recorded either way — ⚠ UNVERIFIED, and unfalsifiable from the repository.
- **Construct circularity in M-1.** The detector defines both what counts as an issue and what
  the pipeline is built to fix; the same `ISSUE_CATALOGUE` supplies the before column, the after
  column, and the target. M-1 measures conformance to a rule set, not data quality. The rules
  are explicitly precision-first heuristics standing in for semantic checks
  (`enrichment/issue_detection.py:26-28`), so a code that clears may reflect a heuristic no
  longer matching rather than a defect repaired — Pass 3b's REC-15 is the worked case
  (`03b_EXEMPLARS.md:461-468`).
- **M-8's answer key is per-fixture and hand-written** (`tests/fixtures/expected_outcomes.json`,
  §4.3), and a fixture absent from it scores as a pass (`scripts/test_local.py:104-105`).

### 6.2 Metrics computed on filtered subsets

- M-3 ground-truth pairs are drawn only from rows whose `expected_routing` is `cluster` or
  `manual_review`, while predicted pairs are drawn from **all** rows
  (`eval/dedup_eval.py:162-166`). The denominators of precision and recall are populations of
  different sizes.
- M-3 rows with a blank `expected_cluster` or a blank `cluster_id` form no pairs at all
  (`:150-151`), so they silently leave both sides of the comparison.
- M-3 rows with a blank `row_id` are dropped before anything is computed (`:126-127`).
- M-3's `wrongful_block_candidates` cannot include a `manual_review` row, because the scoring
  writer blanks `is_golden_record` for exactly those rows and `is False` rejects `None`
  (`eval/dedup_eval.py:194`; `dedup/scoring_xlsx.py:294-298`). `elections` excludes them for the
  same reason (`eval/dedup_eval.py:223`).
- M-1 computes every headline figure over matched ids only, excluding rows with no identifier
  and duplicate-id repeats (§5.4). `Reduction %` returns `0.0` when `total_before` is zero even
  if issues were introduced (`api/routes.py:466`).
- M-1's per-code counts are per-record set memberships while the headline totals are
  per-occurrence (`api/routes.py:450-455`); the two aggregations agree only because the detector
  set-projects (`enrichment/issue_detection.py:504-510`).
- M-4's `contact_lookup_success` is attributed from record-level status, not from the contact
  lookup's own outcome (`enrichment/orchestrator.py:2645-2648`), and two of its three tier-2
  counters can never increment (`03_ALGORITHMS.md` §1.2).
- M-2 and M-1 count different row populations on the same file (§2.2).

### 6.3 Non-deterministic components without seeding or cached responses

The full per-procedure inventory is `03_ALGORITHMS.md` Part K §B.3. The points that bear on
reproducing a number:

- **No seed exists anywhere**: no `seed` parameter appears in any LLM request construction
  (`llm/openai_client.py:198-207`; `dedup/llm.py:174-184`).
- Phase 1 sets `temperature=0.0` on every call (`llm/openai_client.py:205`) and the kwarg is not
  forwarded, so it cannot be overridden (`:272-275`). **Phase 2 dedup passes no temperature at
  all** and runs a reasoning deployment with `DEDUP_REASONING_EFFORT` defaulting to `low`
  (`dedup/llm.py:122-123, 174-184`). Greedy decoding is not bit-reproducibility.
- **LLM responses are never cached** (`03_ALGORITHMS.md` §B.2). SERP results are cached per
  batch and per process (`utils/cache.py:14, 48-105`), which stabilises repeats *within* a run
  and does nothing *across* runs; the person-affiliation stage bypasses the cache entirely
  (`enrichment/person_affiliation.py:122-131`); page fetches are never cached
  (`search/page_fetcher.py:85-93`).
- **No capture-and-replay layer exists.** A repository-wide search for `vcr`, `cassette`,
  `replay`, `record_mode`, `betamax` finds nothing; the only substitute is the hand-curated
  mocks in `tests/mocks/` (`03_ALGORITHMS.md` §B.4). `MOCK_EXTERNAL_CALLS=true` makes a run
  reproducible but converts the measurement into a measurement of the mocks' curated tables
  (`tests/mocks/openai_mock.py:91-125`; `tests/mocks/serp_mock.py:15-16`).
- **Model drift.** `AZURE_OPENAI_DEPLOYMENT` defaults to `gpt-5.4` (`config.py:84`); tiers whose
  answer is drawn from parametric memory — company canonical, Tier 2 canonical, website Path C,
  Tier 3 — change with the deployment, and the deployment version is not recorded in any output
  (`03_ALGORITHMS.md` §B.3).
- **Order dependence.** Dedup Mode B accretes entities in input order, so canonical sets differ
  with row order (`dedup/adjudicator.py:416-449`). Blocks are processed concurrently
  (`:957-962`), and `cluster_id` is a content hash over sorted member ids, which stabilises the
  id given the same membership but not the membership itself (`dedup/models.py:68-71`).

### 6.4 Registry data that changes over time

- **ROR**: `https://api.ror.org/v2/organizations`, 15 s timeout, acceptance at
  `ROR_CONFIDENCE_THRESHOLD = 0.8`, results cached only per batch in a module-level dict cleared
  at batch start (`config.py:85-86`; `enrichment/tier1_ror.py:35-41, 566-568`;
  `enrichment/orchestrator.py:793`). Organisation records are added and renamed upstream, and
  the server-side affiliation scoring that feeds the threshold is not versioned in any response
  the code stores.
- **GLEIF/LEI**: `https://api.gleif.org/api/v1`, 15 s timeout, ≤2 transient retries, fuzzy gate
  `token_sort_ratio >= 88` over a **fulltext** `legalName` filter (`config.py:88-91`;
  `enrichment/tier1_lei.py:79-86, 194-207, 232-240`). Both the registry contents and the
  filter's behaviour are outside the repository's control.
- Neither client records a registry snapshot date, ETag, or version alongside the `ror_id` /
  `lei_id` it writes, so a re-run months later cannot be distinguished from a code change. A
  metric computed against live registries is valid only for the day it ran.

### 6.5 Search-ranking volatility

- Provider selection is runtime-dependent: SerpAPI when `SERPAPI_KEY` is set, DuckDuckGo
  otherwise, with the code itself noting the fallback returns lower-quality results
  (`config.py:137-145`; `enrichment/orchestrator.py:770-779`). Two runs on two machines can use
  two different search engines with no marker in the output.
- Neither client sets a timeout or retry, and **both swallow every exception into an empty
  result list** (`search/serpapi_client.py:27-56`; `search/duckduckgo_client.py:19-42`), so a
  transient search failure is indistinguishable from "nothing found" in any downstream count.
- Selection logic is deterministic given a fixed result set — Path B takes the first maximum in
  SERP order and the probe sorts by `(penalty, SERP index)` (`03_ALGORITHMS.md` Part E,
  non-determinism notes) — which means the *entire* variance of website and department-domain
  resolution enters through the ranking, and none of it is recorded. `scripts/trace_website.py`
  exists precisely to make one such result set observable (`:1-18`), and writes to a git-ignored
  path.
- Page content behind the chosen URL drifts independently; fetches have a 10 s timeout, no
  retry, and truncate the body at `MAX_PAGE_CONTENT_CHARS` — whose effective default is **1500**,
  not the `3000` declared in the never-consumed `OPTIONAL_VARS_WITH_DEFAULTS` table
  (`config.py:93, 208-210`; `search/page_fetcher.py:246-249`; discrepancy recorded at
  `03_ALGORITHMS.md` §B.1).

### 6.6 Threats specific to the artefacts, not to the algorithms

- **The enriched workbook's provenance is unrecorded and its layout corrupts the after side for
  part of the population** (§4.2, §5.3).
- **The dataset's provenance is unrecorded** and its filler-value pattern is consistent with a
  demonstration set (`03b_EXEMPLARS.md:26-33`); external validity beyond it is not supported by
  anything in the repository.
- **The oracle expects a code count the detector cannot produce** (36/36 against a 37-code
  catalogue with at most 34 observable, §4.1).
- **The test suite is not green at this commit**: 3 failures in `tests/test_orchestrator.py`
  concerning mock-LLM `record_type` and website-fallback expectations
  (`00_INVENTORY.md:336-343`). Any Phase-1 measurement runs against code whose own expectations
  are known to be unmet in those three cases.
- **No coverage figure is committed** and `htmlcov/`, `.coverage` are git-ignored
  (`.gitignore:17-18`); the coverage claim in `00_INVENTORY.md:421-425` remains
  ⚠ MEASUREMENT REQUIRED.
- **Cost is not measured.** Token counts exist only in a log record
  (`dedup/adjudicator.py:1000-1002`) and only for Phase 2; Phase 1 records none. Any cost figure
  in the thesis is ⚠ MEASUREMENT REQUIRED.
- **Latency figures are single-run wall clock** (`processing_time_ms`,
  `enrichment/orchestrator.py:789, 825`; `total_latency_ms`, `dedup/adjudicator.py:995`) with no
  repetition, warm-up, or percentile, and include network time to three external services.

---

## 7 · Results

Not populated, per the pass specification. Every metric the repository can produce is listed
with its source; value cells are empty.

### 7.1 M-1 — Issue reduction (`/issues/compare`, `api/routes.py:417-515`)

| Metric | Value |
|---|---|
| Records matched (joined by id) | |
| Records only in original | |
| Records only in enriched | |
| Total issues before | |
| Total issues after | |
| Issues resolved | |
| Issues introduced | |
| Net reduction | |
| Reduction % | |
| Per-code Before / After / Delta (one row per catalogue code with a non-zero count) | |
| Remaining-issue rows (code × customer) | |

### 7.2 M-3 — Dedup evaluation (`eval/dedup_eval.py`)

| Metric | Value |
|---|---|
| rows_evaluated | |
| weights_versions | |
| pairwise.ground_truth_pairs | |
| pairwise.predicted_pairs | |
| pairwise.true_positives | |
| pairwise.false_positives | |
| pairwise.false_negatives | |
| pairwise.precision | |
| pairwise.recall | |
| pairwise.f1 | |
| business_risk.wrongful_block_candidates.count | |
| business_risk.competing_goldens.count | |
| business_risk.uncertainty_upgrades.count | |
| election.clusters | |
| election.elections | |
| election.manual_review_rows | |
| election.tiebreak_decided_clusters.count | |

### 7.3 M-4 — Enrichment batch summary (`api/models.py:430-453`)

| Metric | Value |
|---|---|
| total | |
| enriched | |
| verified | |
| unresolved | |
| failed | |
| research_institution_count | |
| company_count | |
| tier1_resolved | |
| tier1_lei_count | |
| lei_attempts | |
| lei_hits_exact | |
| lei_hits_fuzzy | |
| lei_misses | |
| lei_errors | |
| tier2a_population_count | |
| tier2a_verification_count *(unreachable — `03_ALGORITHMS.md` §1.2)* | |
| tier2b_count *(unreachable — as above)* | |
| tier3_count | |
| contact_lookup_attempted | |
| contact_lookup_success | |
| processing_time_ms | |

### 7.4 M-5 — Dedup adjudication summary (`dedup/models.py:85-100`)

| Metric | Value |
|---|---|
| blocks | |
| rows_in | |
| distinct_signatures | |
| clusters | |
| rows_clustered | |
| rows_unique | |
| rows_manual_review | |
| llm_calls | |
| errors | |
| candidates_generated | |
| rejected_with_reasoning | |
| candidate_cap_exceeded_blocks | |
| total_prompt_tokens *(log only)* | |
| total_completion_tokens *(log only)* | |
| total_latency_ms *(log only)* | |

### 7.5 M-6 — Scoring and election summary (`dedup/scoring.py:384-396`)

| Metric | Value |
|---|---|
| rows_in | |
| clusters | |
| rows_elected | |
| rows_duplicates | |
| rows_unique | |
| rows_manual_review | |
| all_blocked_clusters | |
| rows_with_warnings | |
| errors | |
| Issues sheet rows, by `ISSUE_TYPES` (`dedup/scoring.py:403-412`) | |

### 7.6 M-2, M-7 to M-9 — Census and harness counts

| Metric | Value |
|---|---|
| M-2 records audited | |
| M-2 records with ≥1 issue | |
| M-7 pytest passed / failed | |
| M-7 line coverage (`pytest --cov`) | |
| M-8 fixtures passed / failed of 6 | |
| M-9 verification steps passed / failed of 6 | |

---

Pass 7 complete. 10 metric-producing components documented, 3 workbooks and 8 JSON fixtures
inventoried with counts read from the files, before/after symmetry assessed at three levels with
one asymmetry confirmed to fire, and 6 classes of threat to validity recorded. Results not
populated. Stop.
