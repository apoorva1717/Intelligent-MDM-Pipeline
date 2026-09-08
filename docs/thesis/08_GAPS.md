Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 08

# Pass 08 — Gaps

Every `⚠` and every discrepancy raised in Passes 00, 01, 02, 03, 03b, 04, 05, 06, 06b and
07, collected into one register, renumbered into a single canonical sequence, deduplicated
across passes, and graded. Each entry carries both sides: what the code does, and what the
artefact that contradicts it says. Entries that state an absence rather than a contradiction
carry the absence and the artefact that names the thing absent.

This pass adds no new investigation of the system beyond what is needed to reconcile two
passes that measured the same quantity differently (§8.10, D-4), to establish whether a
Notion export exists in the repository (§8.9), and to re-verify at first hand the claims
Pass 06b contributes to the high band (§A.6). It reads no file outside the repository and
writes nothing but this document.

## 8.0 Scope, sources and severity

### 8.0.1 Source documents

| Pass | File | Gap section | Items | Local numbering |
|---|---|---|---|---|
| 00 | `docs/thesis/00_INVENTORY.md` | §0.9 | 14 | `⚠-1` … `⚠-14` |
| 01 | `docs/thesis/01_TRACEABILITY.md` | §1.5 | 11 | `⚠-13` … `⚠-23` |
| 02 | `docs/thesis/02_ARCHITECTURE.md` | §2.7 | 11 | `⚠-24` … `⚠-34` |
| 03 | `docs/thesis/03_ALGORITHMS.md` | §3.17 | 14 | `03-1` … `03-14` |
| 03b | `docs/thesis/03b_EXEMPLARS.md` | §3b.9 | 11 | `3b-1` … `3b-11` |
| 04 | `docs/thesis/04_PARAMETERS.md` | §9 | 10 | `9.1` … `9.10` |
| 05 | `docs/thesis/05_DATA_MODEL.md` | §5 gap table | 11 | `⚠-35` … `⚠-45` |
| 06 | `docs/thesis/06_EXTERNAL_DEPS.md` | §9 | 9 | `1` … `9` |
| 06b | `docs/thesis/06b_CROSSCUTTING.md` | §6b.7 | 10 | `G-93` … `G-102` |
| 07 | `docs/thesis/07_EVALUATION.md` | §8 | 14 | `07-1` … `07-14` |
| | | **total raw** | **115** | |

Pass 06b **is** a source. `docs/thesis/06b_CROSSCUTTING.md` was regenerated at `86d173b`
after the first writing of this file and now carries the same header as every other pass
(§A.4). It states its items in a graded table at §6b.7 rather than with the `⚠` glyph —
`grep -c '⚠' docs/thesis/06b_CROSSCUTTING.md` returns `0` at this commit, against `41` for
the superseded version at `HEAD` (§A.6) — and numbers them `G-93` … `G-102`, continuing this
file's canonical sequence rather than opening a local one. Those numbers are adopted
unchanged: Pass 06b already cites them, and renumbering would break those citations.

### 8.0.2 Deduplication

Nine findings are raised by more than one pass. They are merged into a single entry carrying
every source citation. 115 raw entries − 13 duplicate rows = **102 canonical entries**,
`G-01` … `G-102`.

| Merged entry | Sources |
|---|---|
| G-03 | Pass 02 `⚠-26`, Pass 00 `⚠-14` |
| G-10 | Pass 03b `3b-2`, Pass 05 `⚠-37`, Pass 07 `07-1` |
| G-13 | Pass 07 `07-4`, Pass 07 `07-5`, Pass 00 `⚠-8` |
| G-18 | Pass 00 `⚠-6`, Pass 01 `⚠-16`, Pass 03 `03-5` |
| G-19 | Pass 00 `⚠-7`, Pass 05 `⚠-42` |
| G-21 | Pass 00 `⚠-12`, Pass 00 `⚠-13`, Pass 05 `⚠-44` |
| G-39 | Pass 03 `03-9`, Pass 04 `9.8` |
| G-48 | Pass 04 `9.1`, Pass 06 `1` |
| G-72 | Pass 02 `⚠-32`, Pass 04 `9.10` |

Pass 06b raises no duplicate of its own. It re-evidences fifteen entries already in the
register from the cross-cutting side — logging, determinism, merge idempotency, provenance —
and states that it does not re-raise them
(`docs/thesis/06b_CROSSCUTTING.md:830–832`). They are listed here so that a reader of an
entry knows a second pass carries evidence for it.

| Entry | Re-evidenced at, in `docs/thesis/06b_CROSSCUTTING.md` |
|---|---|
| G-04, G-30, G-32 | `:604` — the three values dropped at the write-back boundary |
| G-12 | `:59`, `:481` — the tier route recoverable only from an untracked artefact |
| G-13 | `:224`, `:274` — the cache is the determinism mechanism and is not in the repository |
| G-18 | `:163` — `summary.tier2b_count` structurally zero |
| G-23 | `:809` — the two issue vocabularies |
| G-29 | `:522` — the approval endpoint no pipeline calls |
| G-36 | `:812` — confidence compared only within Phase 1 |
| G-55 | `:222` — `CACHE_FROZEN` off by default and Phase 1 only |
| G-57 | `:169` — Phase 1 token usage discarded |
| G-71 | `:248` — the reproducibility gate has no test |
| G-72 | `:530` — `retry: 0`, and the enrichment merge inside the `ForEach` |
| G-79 | `:172` — the `DEDUP_V2_*` flag state in no output column |
| G-86 | `:509` — the join on an empty customer number |

### 8.0.3 Severity

Passes 00, 01, 02, 03b, 05, 06b and 07 grade their own items; Passes 03, 04 and 06 do not.
The grade below is the source grade where one exists, and this pass's grade where none does.
Two grades are worth flagging and say so in their own row: G-02 (Pass 03 assigns none; graded high here) and G-33 (Pass 02 assigns low; graded medium here).

| Grade | Meaning |
|---|---|
| **high** | A claim the thesis would make is unsupportable from this repository, or a value is lost on a production path with no signal that it was lost. |
| **medium** | A stated behaviour and the code disagree, or a measurement is available on one path and not the other. |
| **low** | A local inconsistency that changes no shipped value at this commit. |

### 8.0.4 Tree state

    $ git status --porcelain
     M docs/thesis/00_INVENTORY.md
     M docs/thesis/01_TRACEABILITY.md
     M docs/thesis/02_ARCHITECTURE.md
     M docs/thesis/03_ALGORITHMS.md
     M docs/thesis/03b_EXEMPLARS.md
     M docs/thesis/04_PARAMETERS.md
     M docs/thesis/05_DATA_MODEL.md
     M docs/thesis/06_EXTERNAL_DEPS.md
     M docs/thesis/06b_CROSSCUTTING.md
     M docs/thesis/07_EVALUATION.md
     M docs/thesis/08_GAPS.md
     M docs/thesis/09_DECISIONS.md

The tree is not clean. Every modified path is a `docs/thesis/*.md` output of this same
documentation run; no source file, fixture, SQL file or ADF export is modified. The commit
`86d173b8a4d715a619b0a2656986c145da7fa81e` that every citation addresses is therefore intact,
and the pass proceeds on that basis. This is recorded as D-1 in §8.10.

---

## 8.1 The numbering collision, and the map from source IDs

Three passes share one `⚠-n` sequence and it does not join up. Pass 01 opens its gap section
with "Numbering continues from Pass 00 (`⚠-1` … `⚠-12`)" (`docs/thesis/01_TRACEABILITY.md:189`)
and starts at `⚠-13`; Pass 00 at this commit raises fourteen, ending at `⚠-14`
(`docs/thesis/00_INVENTORY.md:1063`). `⚠-13` and `⚠-14` therefore each name two different
findings. Both Pass 01 (`:229`) and Pass 02 (`:519–522`) record the collision and instruct
Pass 08 to renumber. Pass 05 (`:997`) does the same.

The canonical sequence below replaces every source ID. Where a thesis chapter cites a gap, it
cites `G-nn`.

| Source ID | Canonical | Source ID | Canonical | Source ID | Canonical |
|---|---|---|---|---|---|
| 00 `⚠-1` | G-16 | 02 `⚠-24` | G-27 | 04 `9.1` | G-48 |
| 00 `⚠-2` | G-64 | 02 `⚠-25` | G-28 | 04 `9.2` | G-80 |
| 00 `⚠-3` | G-01 | 02 `⚠-26` | G-03 | 04 `9.3` | G-49 |
| 00 `⚠-4` | G-17 | 02 `⚠-27` | G-29 | 04 `9.4` | G-81 |
| 00 `⚠-5` | G-65 | 02 `⚠-28` | G-30 | 04 `9.5` | G-50 |
| 00 `⚠-6` | G-18 | 02 `⚠-29` | G-31 | 04 `9.6` | G-82 |
| 00 `⚠-7` | G-19 | 02 `⚠-30` | G-04 | 04 `9.7` | G-83 |
| 00 `⚠-8` | G-13 | 02 `⚠-31` | G-32 | 04 `9.8` | G-39 |
| 00 `⚠-9` | G-66 | 02 `⚠-32` | G-72 | 04 `9.9` | G-84 |
| 00 `⚠-10` | G-67 | 02 `⚠-33` | G-73 | 04 `9.10` | G-72 |
| 00 `⚠-11` | G-20 | 02 `⚠-34` | G-33 | 05 `⚠-35` | G-85 |
| 00 `⚠-12` | G-21 | 03 `03-1` | G-34 | 05 `⚠-36` | G-07 |
| 00 `⚠-13` | G-21 | 03 `03-2` | G-74 | 05 `⚠-37` | G-10 |
| 00 `⚠-14` | G-03 | 03 `03-3` | G-75 | 05 `⚠-38` | G-51 |
| 01 `⚠-13` | G-22 | 03 `03-4` | G-35 | 05 `⚠-39` | G-52 |
| 01 `⚠-14` | G-09 | 03 `03-5` | G-18 | 05 `⚠-40` | G-06 |
| 01 `⚠-15` | G-23 | 03 `03-6` | G-36 | 05 `⚠-41` | G-05 |
| 01 `⚠-16` | G-18 | 03 `03-7` | G-37 | 05 `⚠-42` | G-19 |
| 01 `⚠-17` | G-68 | 03 `03-8` | G-38 | 05 `⚠-43` | G-08 |
| 01 `⚠-18` | G-24 | 03 `03-9` | G-39 | 05 `⚠-44` | G-21 |
| 01 `⚠-19` | G-25 | 03 `03-10` | G-76 | 05 `⚠-45` | G-86 |
| 01 `⚠-20` | G-69 | 03 `03-11` | G-77 | 06 `1` | G-48 |
| 01 `⚠-21` | G-70 | 03 `03-12` | G-40 | 06 `2` | G-53 |
| 01 `⚠-22` | G-71 | 03 `03-13` | G-02 | 06 `3` | G-54 |
| 01 `⚠-23` | G-26 | 03 `03-14` | G-41 | 06 `4` | G-87 |
| 03b `3b-1` | G-12 | 03b `3b-7` | G-45 | 06 `5` | G-55 |
| 03b `3b-2` | G-10 | 03b `3b-8` | G-46 | 06 `6` | G-56 |
| 03b `3b-3` | G-11 | 03b `3b-9` | G-78 | 06 `7` | G-88 |
| 03b `3b-4` | G-42 | 03b `3b-10` | G-79 | 06 `8` | G-57 |
| 03b `3b-5` | G-43 | 03b `3b-11` | G-47 | 06 `9` | G-58 |
| 03b `3b-6` | G-44 | 07 `07-1` | G-10 | 07 `07-8` | G-61 |
| 07 `07-2` | G-14 | 07 `07-5` | G-13 | 07 `07-9` | G-62 |
| 07 `07-3` | G-15 | 07 `07-6` | G-59 | 07 `07-10` | G-63 |
| 07 `07-4` | G-13 | 07 `07-7` | G-60 | 07 `07-11` | G-89 |
| 07 `07-12` | G-90 | 07 `07-13` | G-91 | 07 `07-14` | G-92 |

Pass 06b needs no map row. It numbers its ten items `G-93` … `G-102` in this sequence at
source (`docs/thesis/06b_CROSSCUTTING.md:828–832`) and is the only pass that cites register
entries by canonical id.

---

## 8.2 Register — high (G-01 … G-15, G-93, G-97)

| # | Statement | Side A — the code at `86d173b` | Side B — the artefact that contradicts it, or the absence | Source |
|---|---|---|---|---|
| **G-01** | The gate that `tests/KNOWN_FAILURES.md` says asserts the failing set does not exist, and the recorded set no longer matches the observed set. | `pytest -q` at this commit: 12 failed, 3857 passed, 12 skipped, 1 xfailed (`docs/thesis/00_INVENTORY.md` §0.7.1). No `.py` file references `KNOWN_FAILURES` — `grep -rn 'KNOWN_FAILURES' --include='*.py' .` returns nothing (§A.1). | `tests/KNOWN_FAILURES.md:3–5`, `:7`: "A gate asserts the failing set is exactly this manifest … 8 failed, 3311 passed, 7 skipped". `eval/out/RUNS.md:370`: "now pinned in `tests/KNOWN_FAILURES.md`, which every future gate asserts against as a SET". | 00 `⚠-3` |
| **G-02** | The constrained-reader claim — that the model cannot return a value absent from its context — is enforced in code at exactly one of thirteen Phase 1 LLM call sites. | `enrichment/grounded_resolver.py:289–302` is the one containment check. Three further sites hold structurally, by closed-vocabulary schema; three are asserted in prompt text only; six prompts explicitly direct inference beyond the supplied context. The write gate that replaced containment is identity: `enrichment/name_gate.py:171–313`, where only a `DIFFERENT` verdict refuses. | `docs/thesis/03_ALGORITHMS.md` §3.16.4. The claim as stated cannot be made of the pipeline as a whole; Pass 12 must carry it as identity-gated, not containment-gated. **Graded high by this pass** (Pass 03 does not grade): it is the load-bearing claim of the thesis' central argument. | 03 `03-13` |
| **G-03** | No ADF path can produce a before/after issue pair, so the reduction metric the evaluation rests on has no orchestrated source. | `usp_MergeLegacyIssues` declares `@target_column SYSNAME = N'Issues'` (`sql/usp_merge_legacy_issues.sql:5`) and admits `N'Issues Before'` or `N'Issues'` (`:14`); `adf/issues_pipeline.json:90–99` passes `payload` only, so the parameter always takes its default and a second run overwrites the first. `POST /issues/compare` (`api/routes.py:917`) is the only before/after mechanism and is a two-file multipart endpoint no pipeline calls. | `docs/thesis/CONTEXT-EXTERNAL.md:431–432`: "`/issues` may also be run standalone against the raw file … ⚠ Whether that path is in ADF or manual is unconfirmed." Pass 02 answers: manual. No exported pipeline ever writes `Issues Before`, the column a before/after reduction would read. | 02 `⚠-26`, 00 `⚠-14` |
| **G-04** | `Flag Codes` is never written back, and three issue codes can be raised from nothing else. | `sql/usp_merge_legacy_enriched.sql:47–78` lists 32 `OPENJSON` paths and `Flag Codes` is not among them — `grep -c 'Flag Codes' sql/usp_merge_legacy_enriched.sql` returns `0` (§A.2). `FLAG_CODE_ISSUES` (`enrichment/issue_detection.py:1515–1559`) is the only route to `G3-NAME-006`, `G6-CONFIRM-001` and `G7-UNCHANGED-001`. | `api/output_columns.py` declares `Flag Codes` as a response column and `api/routes.py:784` passes it to `detect_issues` when the request carries it. An ADF-driven post-enrichment issues run reads Legacy (`adf/issues_pipeline.json:21`), which never received the column, so those three codes cannot fire on that path. They do fire on the file path, where the enriched workbook still carries the column. | 02 `⚠-30` |
| **G-05** | All four stored-procedure activities pass one parameter; all four procedures require three. | `usp_MergeLegacyEnriched`, `usp_MergeValidationClusters` and `usp_MergeValidationScores` declare `@chrEntity SYSNAME`, `@chrGroupCode NVARCHAR(50)` and `@payload NVARCHAR(MAX)`, none with a default (`sql/usp_merge_legacy_enriched.sql:2–4`, `sql/usp_merge_validation_clusters.sql:2–4`, `sql/usp_merge_validation_scores.sql:2–4`); `usp_MergeLegacyIssues` adds `@target_column` with a default (`sql/usp_merge_legacy_issues.sql:2–5`). | Every `SqlServerStoredProcedure` activity supplies `storedProcedureParameters` of exactly `{payload}` (`adf/deduplication_pipeline.json:91–99`, `adf/enrichment_pipeline.json:138–146`, `adf/issues_pipeline.json:91–99`, `adf/scoring_pipeline.json:91–99`), verified by enumeration in §A.2. The entity and group code the Lookups are parameterised on never reach the merge. ⚠ UNVERIFIED whether the deployed factory differs from the exported JSON. | 05 `⚠-41` |
| **G-06** | The ADF-orchestrated clustering runs on a strictly smaller feature set than the file-orchestrated one. | `DedupRow` declares 21 fields (`dedup/models.py:18–71`); the signature key reads the whole name block below Name 1 — `DEPT_SLOTS = NAME_SLOTS[1:]` = `name2`…`name5` (`utils/name_slots.py:53`), joined by `department_text` (`dedup/signatures.py:65–77`) and normalised into `norm_name2` (`:321–323`). | `adf/deduplication_pipeline.json:21` projects 11 columns. `name3`, `name4`, `name5`, `enriched_name`, `operating_name`, `suggested_name`, `record_type`, `ror_id_provenance`, `lei_id_provenance` and `building` are never supplied. On the ADF path a record whose unit sits in Name 3 is a record with no department at all — the far side of the deterministic Name 2 asymmetry rule (`dedup/signatures.py:142–156`). | 05 `⚠-40` |
| **G-07** | A populated SAP column is silently dropped by the file route. | `EnrichmentRecord` binds only `Terms of Payment Contact` (`api/models.py:208–211`, alias at `:210`); the result field is fed from it (`enrichment/orchestrator.py:741`). `Terms of Payment` is a declared output column (`api/output_columns.py:94`) and is therefore excluded from the passthrough set (`api/routes.py:355–356`). | `data/eval/S2_pre.xlsx` carries `Terms of Payment = NT30`; `data/eval/S2_post.xlsx` carries the column empty. The value is neither bound nor passed through, and nothing reports the loss. | 05 `⚠-36` |
| **G-08** | No DDL for any persisted table exists in the repository. | A `grep -rln` for `CREATE TABLE` or `ALTER TABLE` over `*.sql`, `*.py` and `*.md` hits only the two documentation files that quote the pattern; no `.sql` file in the tree contains either statement. Command and verbatim output in §A.2. | Column existence for Import, Legacy, Validation and the load file is inferred from ADF projections and merge targets only. Type, length, nullability and every key constraint are `⚠ NOT EVIDENCED` throughout `docs/thesis/05_DATA_MODEL.md` §3, and the Import table has no evidenced column at all (`:150`). | 05 `⚠-43` |
| **G-09** | The number `13` names two different behaviours and both write it into the same `use_cases_triggered` array, so a consumer cannot tell them apart. | Lab → parent department: `enrichment/lab_resolver.py:48`, recorded `enrichment/orchestrator.py:8759–8760`, `:8768–8769`. Department-slot residual junk cleanup and person-in-slot routing: `enrichment/preprocess.py:2551` (section), `res.note(13, …)` at `:2562`, `:2575`, `:2579`, `:2584`. | `README.md:1350` declares UC 13 as "Lab → Parent Department Resolution" only. The preprocessing meaning is undeclared anywhere. Any per-use-case count drawn from `use_cases_triggered` conflates the two. | 01 `⚠-14` |
| **G-10** | `Issues` is not one column. Three of the ten stratum workbooks carry the header twice, the position is unstable across the set, and the two occurrences in `S1_pre` are two different runs. | `POST /issues` echoes the sheet "unchanged with a single appended `Issues` column" (`api/routes.py:845`), built as `ws.append([*headers, "Issues"])` (`api/routes.py:445`) — the appended column is always the last. `_parse_xlsx` keys each row by header (`api/routes.py:268–277`), so on re-upload the rightmost non-empty duplicate wins; `_passthrough_headers` deduplicates by normalised header (`api/routes.py:360–369`). No validation rejects or reports the collision. | `S1_pre.xlsx` carries `Issues` at 0-based columns 2 and 7 and **neither is the last column**, so neither can be the column this endpoint appended to this layout; the two disagree on 70 of 100 rows (§A.3). `S2_pre.xlsx` (6, 41) and `S1_post.xlsx` (7, 82) carry exact copies. `S1_post.xlsx` also carries `Flag Codes` twice and `S1_pre.xlsx` a `None` header. No file states which occurrence is authoritative. | 03b `3b-2`, 05 `⚠-37`, 07 `07-1` |
| **G-11** | Nine of the ten stratum workbooks carry at least one code with `status="withdrawn"` at this commit, so every one of them predates the current catalogue. | `G1-NAME-001` is `status="withdrawn"` with `reason="withdrawn 2026-09-07; …"` (`enrichment/issue_detection.py:270–274`); `G4-ADDR-008` is `status="withdrawn"` (`:353–361`). Neither is emittable at `86d173b`. | `G1-NAME-001` appears in nine of the ten workbooks and `G4-ADDR-008` in `S1_pre` (`docs/thesis/03b_EXEMPLARS.md` §3b.8.2). A pre/post count taken from these files is not measured against the catalogue at this commit. | 03b `3b-3` |
| **G-12** | The tier route a record took is recoverable only from `logs/`, which is gitignored, so no committed artefact reproduces the tier trace the exemplars cite. | Provenance columns record a **source**, not a tier, by decision (`enrichment/confidence.py:12–16`). | `git ls-files logs/` returns nothing; `.gitignore:21` excludes the directory. Every tier trace in `docs/thesis/03b_EXEMPLARS.md` is drawn from `logs/enrichment_api.log.5`, an untracked file (`:165`, `:402`, `:629`, `:810`). ⚠ MEASUREMENT REQUIRED — a committed tier trace (§8.6). | 03b `3b-1` |
| **G-13** | A frozen replay is not reproducible from the repository: the default cache directory covers two of six namespaces, and the four that matter are gitignored and absent. | `utils/cache.py:457–465` — a frozen run against the repository's own default `EVIDENCE_CACHE_DIR` replays only page reads and Wikidata. `.gitignore:37–40` ignores `tests/fixtures/serp/`, `tests/fixtures/registry/`, `tests/fixtures/fetch/`, `tests/fixtures/llm/`; none is tracked (`ls` on all four returns "No such file or directory", `docs/thesis/07_EVALUATION.md` §A.9). | `.gitignore:33–36`: "the runs behind determinism_findings.md were measured against exactly that". The determinism result the thesis would cite cannot be reproduced by anyone who has only this repository. | 07 `07-4`, 07 `07-5`, 00 `⚠-8` |
| **G-14** | 100 of the 183 rows of `stress_200_scored.xlsx` carry seeded dummy scoring inputs. | The seven QLIC-derived scoring columns on those rows are synthetic (`docs/thesis/07_EVALUATION.md` §3.3). | The workbook's own `Dummy_Fill` sheet records the seeding. Any election-agreement or weight-sensitivity figure computed over the full 183 rows is computed over a majority of fabricated inputs; Pass 18 must restrict to the 83 real rows and say so. | 07 `07-2` |
| **G-15** | The repository's own clustering evaluator cannot read the repository's own stress workbooks. | `eval/dedup_eval.py:3–5` reads `expected_cluster` / `expected_routing`. | No file in `data/eval/` carries either column (`docs/thesis/07_EVALUATION.md` §3.3). The clustering accuracy figures Pass 18 is asked for have no ready harness; the ground-truth vocabulary that does exist is `gt_expected_action` in `dedup_STRESS_200_v1-verified.xlsx`, which the evaluator does not read. | 07 `07-3` |
| **G-93** | The logging formatter renders none of the structured fields the middleware attaches, and the module docstring calls the middleware structured JSON. | `api/middleware.py:87–91` sets the format to `"%(asctime)s %(levelname)s %(name)s [%(funcName)s] %(message)s"` — re-read at first hand here (§A.6). Pass 06b demonstrates the consequence by executing the repository's own `configure_logging`: `logger.info("request_complete", extra={"request_id": …, "status": 200, "duration_ms": 42})` emits `… request_complete` and nothing else (`docs/thesis/06b_CROSSCUTTING.md` §A.3). | `api/middleware.py:1`: "FastAPI middleware for structured JSON logging, request timing, and error handling." All ten idiom-(a) call sites lose their payload, including the three that carry the request correlation id (`api/middleware.py:30–34`, `:41–48`, `:62–69`) and the three that carry the Phase 2 token counts (`dedup/adjudicator.py:1301`, `:1402`, `:1534`). The records that do survive are a Python `dict` repr, not JSON. | 06b `G-93` |
| **G-97** | Re-running the scoring pipeline discards a steward decision recorded in `Validation.[approval_status]`. | `sql/usp_merge_validation_scores.sql:78` assigns `tgt.[approval_status] = src.approval_status` with no guard; the column is parsed from the payload at `:60`, re-read here (§A.6). `elect_golden_records` sets that field to `"proposed"` for every member of a `proposed`/`manual_review` cluster (`dedup/scoring.py:1322`) and `None` for a `unique` row (`:1308`). | The only source of `"approved"`/`"rejected"` is `apply_approval` (`dedup/scoring.py:628`) behind `POST /api/dedup/approve`, which is stateless and which no ADF pipeline calls (G-29). The column the four-eyes control depends on is therefore writable by an unattended re-run, and the twenty other columns in the same statement are legitimately overwritten by a rescore, so the remedy cannot be to skip the statement. | 06b `G-97` |

---

## 8.3 Register — medium (G-16 … G-63, G-94 … G-96, G-98, G-99, G-102)

| # | Statement | Side A — the code at `86d173b` | Side B — the artefact that contradicts it, or the absence | Source |
|---|---|---|---|---|
| **G-16** | The Function App is deployed with anonymous HTTP auth while a route docstring claims key/function auth. | `function_app.py:12`: `func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)` | `api/routes.py:1337–1339`: "Auth is inherited from the Azure Function App (same key/function-auth pattern as the other endpoints)". | 00 `⚠-1` |
| **G-17** | Five `tests/test_dedup.py` failures are outside the recorded manifest and have no recorded explanation anywhere in the repository. | Assertions at `tests/test_dedup.py:204`, `:240`, `:528`, `:682`, `:999` (`docs/thesis/00_INVENTORY.md` §0.7.2 rows 1–5). | `tests/KNOWN_FAILURES.md:41`: "None is a flake — each is a stable assertion failure at every commit tested" — a statement made about a manifest that does not include these five. See G-01. | 00 `⚠-4` |
| **G-18** | Tier 2B ships as a complete module with tests and a registered prompt, has no production call site, and its telemetry counter is structurally unreachable. | `grep -rn 'run_tier2b' --include='*.py' .` returns `enrichment/tier2b_dept.py:48` (the definition) and five lines in `tests/test_tier2b.py` and nothing else (§A.2). `tier2_mode` is written once, `enrichment/orchestrator.py:3854`, from `Tier2AResult.mode` ∈ {`"2A_population"`, `"2A_verification"`} (`enrichment/tier2a_contact.py:90`). `summary.tier2b_count` (`api/models.py:850`) increments only when `r.tier2_mode == "2B"` (`enrichment/orchestrator.py:9304–9305`), so it is always zero. Prompt constants remain registered (`llm/prompts.py:654–657`). | `enrichment/orchestrator.py:8764–8766` names Tier 2B as a live downstream option: "the record falls through to tier 2 canonical / 2A / 2B / 3, any of which may settle Name 2". Tier 2B is not among them at this commit. | 00 `⚠-6`, 01 `⚠-16`, 03 `03-5` |
| **G-19** | One ADF activity names a procedure no `sql/` file creates. | `sql/usp_merge_legacy_enriched.sql:1` creates `[Mapping].[usp_MergeLegacyEnriched]`. | `adf/enrichment_pipeline.json:136` sets `"storedProcedureName": "Mapping.usp_merge_legacy_enriched"`. The two identifiers differ by more than letter case, so a case-insensitive collation does not reconcile them. The other three pipelines name their procedures exactly as created — enumerated in §A.2. | 00 `⚠-7`, 05 `⚠-42` |
| **G-20** | The repository is not pinned to a Python version and the only interpreter present is 3.9.6. | `python3 -V` → `Python 3.9.6`; `which python` → not found. | `requirements.txt` (14 LOC) declares no `python_requires`; there is no `pyproject.toml`, `setup.py`, `.python-version` or `runtime.txt` in `git ls-files`. The deployed Function App's runtime version is not stated anywhere in the repository. | 00 `⚠-11` |
| **G-21** | The Validation database cannot be resolved from this repository, and the procedures' own marker saying so is unresolved. | Write side: `QUOTENAME(@db) + N'.' + QUOTENAME(@chrEntity) + N'.Validation'` with `DECLARE @db SYSNAME = N'dp_validation';  -- <<< confirm` (`sql/usp_merge_validation_clusters.sql:8`, `:33`; `sql/usp_merge_validation_scores.sql:8`, `:33`). | Read side: `FROM [@{pipeline().parameters.chrEntity}].Validation` with **no** database prefix, resolved by the linked service's default (`adf/deduplication_pipeline.json:20`, `adf/scoring_pipeline.json:20`). The linked service is not exported (G-33), so the default cannot be read here and nothing confirms the two agree. The two `dp_legacy` pipelines have no such gap: both qualify the database in the query (`adf/enrichment_pipeline.json:20`, `:67`; `adf/issues_pipeline.json:20`) and in the procedure (`sql/usp_merge_legacy_enriched.sql:29`, `sql/usp_merge_legacy_issues.sql:38`). | 00 `⚠-12`, 00 `⚠-13`, 05 `⚠-44` |
| **G-22** | The `FR-1 … FR-36` functional-requirement numbering does not exist in the repository. | `grep -rn 'FR-[0-9]'` over the tree returns hits only inside `docs/`. | `docs/thesis-doc-prompt-v2.md:66`: "IDs from the repo's own numbering (UC, FR-1…FR-36, issue codes)". The requirement set the thesis would cite as `FR-n` has no repository source; Pass 01 traces against the `U-n` numbering it derived instead. | 01 `⚠-13` |
| **G-23** | Two disjoint issue vocabularies exist and neither references the other. | `ISSUE_CATALOGUE` (`enrichment/issue_detection.py:260–423`, 43 declared codes) drives `/issues`; `detect_issues` in `dedup/scoring.py:485` emits `DedupIssue` (`dedup/scoring.py:458`) from `/api/dedup/score`. | No code maps a scoring issue onto a catalogue code, and no catalogue code is declared for a scoring defect. A thesis-level "issue count" is not well defined across the two; Pass 18 must report them in separate tables and never sum them. | 01 `⚠-15` |
| **G-24** | The `detect_issues` docstring describes group G6 under its pre-renumbering meaning. | The one live G6 code is `G6-CONFIRM-001`, origin `API` (`enrichment/issue_detection.py:402–405`). The renumbering is recorded at `enrichment/issue_detection.py:259`: "Renumbered 2026-09-06: old G6 withdrawn, old G7->G6, old G8->G7". | `enrichment/issue_detection.py:1693–1695`: "the before/after reduction narrative is defined over the whole G1-G6 set — of which G6 is entirely DS-origin". No live G6 code is DS-origin. | 01 `⚠-18` |
| **G-25** | Two codes carry a prefix that contradicts their declared group, so a per-group census gives different answers depending on which field it reads. | `_d("G2-VAL-003", "G6", …)` (`enrichment/issue_detection.py:380`); `_d("G2-VAL-006", "G6", …)` (`:385`). `issue_group` (`:455`) returns the declared field. | Declared-field census: G6 1/4. Prefix census: G6 1/2, G2 7/11. Both codes are withdrawn, so no live count moves, but Pass 18's per-group reduction table must state which field it counts on. | 01 `⚠-19` |
| **G-26** | The two artefacts that make the pipeline run in production are traced to no requirement and covered by no test. | `adf/*.json` (4 files); `sql/*.sql` (4 files). No test module executes SQL or validates an ADF export. | No requirement statement, README section or code comment enumerates the pipelines that must exist or the procedures they must call. Correctness of the whole write-back path is unverifiable from this repository alone. | 01 `⚠-23` |
| **G-27** | `docs/thesis/CONTEXT-EXTERNAL.md` quotes ADF JSON that the exported files contradict on three points. | `adf/deduplication_pipeline.json:21` is parameterised on `chrEntity` and carries the group-code predicate; `:89` names `Mapping.usp_MergeValidationClusters`; `adf/enrichment_pipeline.json:21`, `:68` batch in 30s. | `docs/thesis/CONTEXT-EXTERNAL.md:226` quotes `FROM test_77.Validation` — hard-coded entity, no predicate; `:281` quotes `dbo.usp_merge_validation_clusters`; `:188` states "50-row offsets". The exported files supersede the quotation; the external document has not been re-observed since 2026-08-16 (`:15`). | 02 `⚠-24` |
| **G-28** | Three pipelines carry a `lastPublishTime` and the issues pipeline does not. | `adf/deduplication_pipeline.json:115`, `adf/enrichment_pipeline.json:165`, `adf/scoring_pipeline.json:115`. | `adf/issues_pipeline.json` has no such key. On repository evidence the issues pipeline is authored but unpublished, so the `/issues` leg of the production workflow (`docs/thesis/CONTEXT-EXTERNAL.md:424`) is exported but not demonstrably live. ⚠ UNVERIFIED against the factory. | 02 `⚠-25` |
| **G-29** | The steward approval step has no orchestrated write-back path. | `POST /api/dedup/approve` (`api/routes.py:1488`) is stateless — "Persistence is intentionally out of scope" (`:1494–1495`) — and no `adf/*.json` names the route. | `usp_MergeValidationScores` writes `approval_status` (`sql/usp_merge_validation_scores.sql:59`), so the target column exists. The approval observed in DS Studio (`docs/thesis/CONTEXT-EXTERNAL.md:395–398`) is a DATAshaper action, not a call to this endpoint. The two approval mechanisms are unconnected. | 02 `⚠-27` |
| **G-30** | Scoring diagnostics are computed and discarded at the write-back boundary. | `/api/dedup/score` returns `issues` — `DedupIssue` values from `dedup/scoring.py:485`, attached at `api/routes.py:1475`. | `usp_MergeValidationScores` parses `$.rows` only (`sql/usp_merge_validation_scores.sql:49`); no path reads `$.issues` and no column receives them. On the ADF path the cluster-quality diagnostics Pass 18 is asked to report exist only in the HTTP response. | 02 `⚠-28` |
| **G-31** | Three identity columns are overwritten unconditionally by the enrichment merge, so a run that fails to resolve clears a value an earlier run established. | `sql/usp_merge_legacy_enriched.sql:86`: `tgt.[Record Type] = LTRIM(RTRIM(src.[Record Type]))`, `tgt.[ROR ID] = LTRIM(RTRIM(src.[ROR ID]))`, `tgt.[LEI ID] = LTRIM(RTRIM(src.[LEI ID]))`. | The other 26 value columns in the same statement use `COALESCE(NULLIF(…), tgt.[col])` and preserve the incumbent on a blank. The asymmetry is stated in no comment; the only comment on the statement concerns `SPACE(0)` (`:83–84`). | 02 `⚠-29` |
| **G-32** | `link_id` has no write-back path. | `DedupResultRow.link_id` is produced for every row (`dedup/adjudicator.py:1518–1525`) and serialised by the response model (`dedup/models.py:74` block, `:101`). | `usp_MergeValidationClusters` parses seven fields (`sql/usp_merge_validation_clusters.sql:51–57`) and `link_id` is not one; the `MERGE` writes six columns (`:59`) and none is a link. The "same organisation, not the same record" outcome the field exists to express is dropped at the boundary. | 02 `⚠-31` |
| **G-33** | The referenced ADF datasets, linked services and integration runtime are not exported, so the database each pipeline actually reads cannot be resolved from this repository. | `AzureSqlMITable1` (`adf/enrichment_pipeline.json:27`), `AzureSqlMITable3` (`adf/deduplication_pipeline.json:27`), `ls_sqlmi_legacy` (`adf/issues_pipeline.json:101`), `ls_sqlmi_validation` (`adf/deduplication_pipeline.json:101`), `AutoResolveIntegrationRuntime` (`:60`). | None is tracked. **Graded medium by this pass** (Pass 02 grades low): it is the sole reason G-21 cannot be resolved from repository evidence. | 02 `⚠-34` |
| **G-34** | The SAP name-column width is not stated anywhere in this repository. | `NAME_FIELD_WIDTH = 40` (`enrichment/name_repack.py:37–50`) rests on a comment asserting SAP `ADRC-NAME1` is `CHAR(40)` plus a corpus observation. | No DDIC export, column definition or DS schema in the repository states the width. Every UC 0 overflow decision and the `G4-NAME-015` predicate depend on it. | 03 `03-1` |
| **G-35** | Stage 2b (person affiliation) is not exercised by any row in the evaluation corpus. | `enrichment/orchestrator.py:8043–8052` is the gate; coverage is by unit test only (`tests/test_person_affiliation.py`, `tests/test_person_affiliation_guard.py`). | No row in `data/eval/S1..S5_pre.xlsx` has a Name 1 that is only a person's name (`docs/thesis/03_ALGORITHMS.md:932`). Pass 18 can report no stratum figure for this stage. | 03 `03-4` |
| **G-36** | Tier 2A Mode B compares two numbers on different scales and thresholds the winner with a single number. | `enrichment/tier2a_contact.py:476–490` compares the model's self-reported `name2_match_score` against a RapidFuzz `token_sort_ratio`. | The code records this as a known defect left in place (`:476–490` comment). `effective_score` is therefore not a single well-defined quantity, and the threshold on it is not interpretable as one confidence level. | 03 `03-6` |
| **G-37** | `search_term_1` rule 2 carries no second name check, so a record can ship a search handle naming a different organisation. | `strip_tld(domain)` with no name check (`enrichment/search_terms.py:980–994`); the absence of the check is documented as deliberate. | `data/eval/S1_post.xlsx` row `13162559` — Whitehead Institute, `ror.org/04vqm6w82` — ships `Search Term 1 = "MIT"` from its ROR-supplied `Domain = "mit.edu"`. | 03 `03-7` |
| **G-38** | `_cap_to_two_terms` can produce a handle that identifies no organisation. | `enrichment/search_terms.py:709–762`, `:763–781`. `"United States National Nuclear Security Administration"` normalises to `"UNITED STATES"`, verified by executing `_normalise_term` on that string (`docs/thesis/03_ALGORITHMS.md:1364`). | `data/eval/S3_post.xlsx` row `13216632` ships that value as `Search Term 1`. | 03 `03-8` |
| **G-39** | `dedup/weights.json` marks three values UNCONFIRMED in its own `_comment`, pending confirmation with Bernd. | `dedup/weights.json:2` — the `combined_presence_bonus` value, the `sales_order_partner_count` tiers, and `account_group` key `DRIT` (the transcript said `DRID`). Two matching comments sit in the scorer: `dedup/scoring.py:986`, `:1019`. | The weights are in force at this commit and every election in `data/eval/stress_200_scored.xlsx` was computed with them. No artefact in the repository resolves the three. | 03 `03-9`, 04 `9.8` |
| **G-40** | Two prompt constants live outside `llm/prompts.py` and are the only production prompt pair with no registered `prompt_version`. | `PERSON_CLASSIFIER_SYSTEM_PROMPT` / `_USER_PROMPT_TEMPLATE` at `enrichment/preprocess.py:3314–3330`. | `llm/prompts.py:1–4`: "All LLM prompt strings as module-level constants. Centralised here so they can be versioned, reviewed, and tested independently"; the registry is at `:646–691`. A write influenced by the person classifier is not reproducible from the recorded provenance. | 03 `03-12` |
| **G-41** | A record can ship a divergent `Name 1 Provenance` within one batch-consensus group when the value itself converges. | `Name 1 Provenance` is not in `PROPAGATED_FIELDS` (`enrichment/batch_consensus.py:80–93`). | `data/eval/S2_post.xlsx` rows `13342215` and `13342226` share one address, one name and one `gleif:verified` LEI, and ship `gleif:verified` and `llm:provisional` respectively. A provenance-based count over a consensus group is not stable. | 03 `03-14` |
| **G-42** | No clustering or scoring output exists for any S2 or S4 `Customer`, so two of five strata have no Phase 2 exemplar or Phase 2 measurement. | `data/eval/` holds no clustering or scoring export keyed to `S{n}_post.xlsx`; the Phase 2 workbooks are the stress set, whose `Customer` overlap with S2 and S4 is **0** rows each (`docs/thesis/03b_EXEMPLARS.md` §3b.0.5). | Pass 18 asks for clustering and election figures per stratum. For S2 and S4 the answer is `⚠ NOT MEASURED` unless a run is executed (§8.6). | 03b `3b-4` |
| **G-43** | 36 rows ship with the whole name block below Name 1 empty after arriving with Name 2 populated, and no catalogue code tests the condition. | Counted per stratum at `docs/thesis/03b_EXEMPLARS.md` §3b.8.6: S1–S5 wholly absent 5+12+9 and the rest; 36 rows across the five strata. `13341941` is among them. | Some are correct clearings (`13342215` held a Name 2 identical to Name 1; `13348125` held address content). No code in `ISSUE_CATALOGUE` distinguishes a correct clearing from a loss, so the count is the population a defect count would be drawn from and not a defect count. | 03b `3b-5` |
| **G-44** | `expected_issue_codes` and the emitted `Issues` disagree on every exemplar examined. | S1: `G4-ADDR-025` expected, `G4-ADDR-008` emitted, `G1-ADDR-003` unexpected. S3: `G5-NAME-001` expected and not emittable. S5: `G2-NAME-009` and `G5-NAME-001` expected, neither emitted on the raw side. (`docs/thesis/03b_EXEMPLARS.md` §3b.2.2, §3b.4.2, §3b.6.2, §3b.8.4.) | The two are separate vocabularies and neither derives from the other. `expected_issue_codes` is a design expectation written into the workbook; the `Issues` column is detector output. Pass 18 must not treat the former as ground truth for the latter. | 03b `3b-6` |
| **G-45** | `G1-ADDR-001` persists from raw to enriched on rows whose declared remedy is `"rule"`. | The code's `remedy` is `"rule"` (`enrichment/issue_detection.py:265`). | On `13342215` and `13342545` the house number stays inside `Street 1` and `House Number` stays empty in both `S2_pre.xlsx` and `S2_post.xlsx` (`docs/thesis/03b_EXEMPLARS.md` §3b.3.5, §3b.6.5). A code declared as programme-remediable is not remediated by the programme. | 03b `3b-7` |
| **G-46** | Enrichment introduces catalogue codes by writing a registry name, so a before/after count is not a count of the same predicate over the same text. | `13334231` (S4): raw `JERSEY SHORE UNIV MED CTR` → `looks_like_university_or_research_institute` `False`; enriched `Jersey Shore University Medical Center` → `True` with Name 2 empty → `G2-NAME-012` appears post-only. `13348125` (S2): raw `Veracyte` → `_is_non_canonical_name` `False`; enriched `Veracyte, Inc.` → `True` → `G5-NAME-001` appears post-only. | `data/eval/S4_post.xlsx`, `data/eval/S2_post.xlsx`. The reduction metric will show these as new issues caused by enrichment; they are the detector reading a longer, more correct string. | 03b `3b-8` |
| **G-47** | A stress-set cluster contradicts its own ground truth. | Cluster `c_4b36bea42391` groups three rows (`docs/thesis/03b_EXEMPLARS.md` §3b.4.6). | `gt_expected_action` on all three rows is `LINK - same organisation, different site: do not collapse`. The house-less demotion routes the cluster to review rather than asserting it, so the run does not emit a wrong merge — but the ground truth and the emitted grouping disagree. | 03b `3b-11` |
| **G-48** | `MAX_PAGE_CONTENT_CHARS` has two stated defaults and the documented one is inert. | `config.py:368` reads `int(os.getenv("MAX_PAGE_CONTENT_CHARS", "1500"))` — the dataclass field that is actually read. `OPTIONAL_VARS_WITH_DEFAULTS` is consumed by no code path: `grep -rn 'OPTIONAL_VARS_WITH_DEFAULTS'` matches only its definition at `config.py:100`. | `config.py:121` declares `"MAX_PAGE_CONTENT_CHARS": "3000"` and `.env.example:105` states `MAX_PAGE_CONTENT_CHARS=3000`. The effective default is `1500`; the local untracked `.env` sets `3000`, so a run on the author's machine uses 3000 characters and a clean checkout uses 1500. | 04 `9.1`, 06 `1` |
| **G-49** | `dept_split_canonicalises` — comment and default disagree. | The field defaults to `True` (`config.py:290–294`). | The comment block states "Phase 5 — origin-based Tier 2 eligibility. OFF by default." and "Off by default" (`config.py:257`, `:268`), and records that a flip to `True` "was gated and REVERTED". Code wins: the lane is on at `86d173b`. | 04 `9.3` |
| **G-50** | Three thresholds are read from the environment outside the `Settings` snapshot, and one is read at import time. | `ROR_CONFIDENCE_THRESHOLD` (`enrichment/tier1_ror.py:936`), `ROR_API_BASE` (`enrichment/tier1_ror.py:934`, `:1654`, `enrichment/liveness.py:263`) and `LEI_NAME_MATCH_THRESHOLD` (`enrichment/orchestrator.py:1776`, `:3100`) use `os.getenv` directly. | The `enrichment/orchestrator.py:1776` read happens at import, so a variable set after the module is imported does not reach `_LEI_NAME_THRESHOLD`. `/config` reports the `Settings` snapshot and therefore cannot report the value in force for these three. | 04 `9.5` |
| **G-51** | `Signature ID` is persisted as if it were a key and is not one. | `signature_id` is `s1`, `s2`, … assigned **per block** and restarting in every block (`dedup/signatures.py:302–306`, `:379`). | `sql/usp_merge_validation_clusters.sql:55`, `:63` writes it into `Validation.[Signature ID]` with no block component. Two unrelated rows in different blocks both carry `s1`, and nothing downstream can tell them apart. | 05 `⚠-38` |
| **G-52** | `scored_with_reference_year` has no write-back column, so half the drift check is unavailable on the ADF path. | `ScoringResultRow.scored_with_reference_year` serialises (`dedup/scoring.py:331`) and exists so a proposal and a later approval can be checked for ladder drift the way `scored_with_weights_version` checks for weights drift (`:326–330`). | `sql/usp_merge_validation_scores.sql:51–72` parses 22 paths and this is not among them; `:78` writes 21 columns and none is a reference year. Observable in the file path only (`data/eval/stress_200_scored.xlsx`). | 05 `⚠-39` |
| **G-53** | TLS verification is inconsistent across page-fetch entry points. | `search/page_fetcher.py:333`, `:340`, `:356`, `:409` pin `verify=certifi.where()`; `:229–241` and `:436–440` pass no `verify` and use the `requests` default. | The comment at `:234–240` records the reason and the measurement, so the split is deliberate — but two fetches of the same host can differ in trust-store behaviour behind a TLS-inspecting VPN, and the evidence one of them produces is not reproducible on a machine without that VPN. | 06 `2` |
| **G-54** | A 429 is retried by exactly one client. | `enrichment/wikidata.py:741` treats `429` as transient. | `enrichment/tier1_lei.py:551–552` retries on `status >= 500` only; ROR, SerpAPI, DuckDuckGo and page fetch have no retry at all. A rate-limited registry call fails open and the record proceeds without the authority's answer. | 06 `3` |
| **G-55** | `CACHE_FROZEN` does not cover Phase 2. | `utils/cache.py:446–452` describes one switch across every namespace. | `dedup/llm.py` participates in none of them and has a separate, separately-switched store (`dedup/cache.py:16–32`). A frozen Phase 1 run and a Phase 2 run are not frozen by the same control; see G-13 and G-92. | 06 `5` |
| **G-56** | The SerpAPI key travels in the URL. | `search/serpapi_client.py:65` places `api_key` in the query parameters. | Query strings are recorded by intermediaries and by the cache key machinery; no code redacts it before logging. | 06 `6` |
| **G-57** | Phase 1 discards `response.usage`, so Phase 1 token cost cannot be computed from any repository artefact. | `llm/openai_client.py:345` returns only the message content. | `dedup/llm.py:244–245` captures token counts for Phase 2. The per-record cost figure Pass 18 is asked for is therefore available for Phase 2 and not for Phase 1 until this is captured; `docs/thesis/06_EXTERNAL_DEPS.md:521` records that no code change is needed beyond capturing the field. | 06 `8` |
| **G-58** | The address-validation service is undocumented: it exists in the DS workflow and nowhere in the repository. | No code, no ADF export, no endpoint (`docs/thesis/06_EXTERNAL_DEPS.md` §2.9). | `docs/thesis/CONTEXT-EXTERNAL.md:423` names the step and an 80% auto-write-back threshold; open item at `:442`. The service's identity, and whether the comparison is `>` or `≥`, are `⚠ UNVERIFIED`. | 06 `9` |
| **G-59** | `test-all-100-original.xlsx` and its enriched twin are column-misaligned on 24 of 99 rows. | `docs/thesis/07_EVALUATION.md` §3.4, measured in that pass's §A.4. | The header row and the data rows disagree in width on those rows, so any header-keyed read of them is reading a shifted value. The pair is unusable for a per-field comparison without repair. | 07 `07-6` |
| **G-60** | The ten stratum workbooks record no generating commit, run id or settings. | No provenance sheet, header cell or sidecar file in `data/eval/S{n}_{pre,post}.xlsx` (`docs/thesis/07_EVALUATION.md` §3.1). | `git log` places all ten at `eb924e6` (`docs/thesis/07_EVALUATION.md` §A.8), which is when they were committed, not when they were produced. The cache state, flag state and catalogue version behind them are `⚠ MEASUREMENT REQUIRED` (§8.6), and G-11 shows they predate this commit's catalogue. | 07 `07-7` |
| **G-61** | A fixture builder points at a path removed two commits ago. | `tools/build_dedup_v2_fixture.py:46`: `WORKBOOK = _ROOT / "docs" / "thesis" / "dedup_STRESS_200_v1_enriched_dedup.xlsx"`. | The workbook now lives at `data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx`, moved in `ea6f9d9`. The builder cannot run as committed. | 07 `07-8` |
| **G-62** | `eval/out/RUNS.md` states three strata's inputs are unavailable; `data/eval/` now holds all five. | `data/eval/` holds `S1`…`S5` pre and post (`docs/thesis/07_EVALUATION.md` §3.1, §A.8). | `eval/out/RUNS.md` records S2, S3 and S5 inputs as unavailable (`docs/thesis/07_EVALUATION.md` §6.1). The recorded run history is scoped to a corpus smaller than the one now committed, so its aggregate figures are not the figures Pass 18 will produce. | 07 `07-9` |
| **G-63** | The five `pre` workbooks are not column-identical, so an index-based reader breaks on S1 and S2. | `S3`–`S5` hold `Division` at 0-based column 6 and the appended `Issues` at 41; `S2` holds `Issues` at 6 in place of `Division`; `S1` holds an unnamed empty header at 1, `Issues` at 2 and `Issues` at 7 (`docs/thesis/07_EVALUATION.md` §3.2, verified §A.3). | Any harness reading these by column index rather than by header reads the wrong column on at least one stratum. Pass 18's script must key by header. | 07 `07-10` |
| **G-94** | The HTTP request id and the per-record telemetry cannot be joined. | `request_id` is generated and stored at `api/middleware.py:22`, `:27` and returned as `X-Request-ID` (`:58`). | None of the 86 idiom-(b) telemetry records carries it (`docs/thesis/06b_CROSSCUTTING.md` §A.2); they key on `record_id`. The one place it is logged is an idiom-(a) record, so it is dropped at the formatter (G-93). A batch cannot be traced from an HTTP call to the records it processed. | 06b `G-94` |
| **G-95** | `tools/run_diff.py` states a column count that is two short. | `len(api.output_columns.RESPONSE_COLUMNS)` is **69** at this commit and `len(enrichment.orchestrator.PROVENANCE_COLUMNS)` is **7**, both re-read here (§A.6). | `tools/run_diff.py:204`: "Seven of the sixty-seven output columns are provenance". The number is stale; the ratio the argument rests on is unaffected. | 06b `G-95` |
| **G-96** | A dropped `seed` degrades reproducibility silently and permanently, and leaves no trace in any artefact. | `llm/openai_client.py:324–344`: on a deployment that rejects `seed`, `_SEED_SUPPORTED` is set `False` at `:336` "for the life of the process", a `WARNING` is logged, and every later call omits it (`:448`). `dedup/llm.py:66`, `:265–270` carries the same latch for `seed` and `temperature`. | The warning's own text: "Byte-identical re-runs are no longer guaranteed by the service." The latch is not in `EnrichmentSummary` (`api/models.py:697–865`), not in `RESPONSE_COLUMNS`, and not in any provenance event, so a run that lost its seed is indistinguishable from one that kept it — including to `tools/run_diff.py`, whose whole purpose is to decide whether two runs agree. | 06b `G-96` |
| **G-98** | The merge procedures are idempotent but not convergent, and silently drop payload rows that do not join. | No `WHEN NOT MATCHED BY TARGET` and no `WHEN NOT MATCHED BY SOURCE` in any of the four — `grep -c 'NOT MATCHED' sql/*.sql` returns `0` for each (§A.6); the `MERGE` is `UPDATE`-only in all four (`sql/usp_merge_legacy_enriched.sql:86`, `sql/usp_merge_legacy_issues.sql:66`, `sql/usp_merge_validation_clusters.sql:63`, `sql/usp_merge_validation_scores.sql:78`). | A payload row whose customer number falls outside the group-code slice is discarded with no error and no count, and a target row the payload does not mention keeps whatever a previous run wrote. Neither outcome reaches the caller: the procedures return no row count and the ADF activity asserts nothing on its output. | 06b `G-98` |
| **G-99** | The whole provenance apparatus is dropped at the write-back boundary. | Six write-locked fields, an event log, a guard-rejection log and seven validated columns are produced for every record (`enrichment/provenance.py:70–77`, `api/models.py:550–557`, `enrichment/orchestrator.py:2051–2054`, `:3278–3280`). | `grep -ci provenance` over each of the four merge procedures returns `0` (§A.6). `api/models.py:546–549` anticipates this — "ADF decides what, if anything, to store" — and the answer at this commit is nothing. On the ADF path a value in Legacy carries no attribution at all, the condition `enrichment/provenance.py:5–8` declares inadmissible. | 06b `G-99` |
| **G-102** | Phase 1 forbids thresholding a model's self-report and Phase 2 thresholds one, and nothing maps the two onto each other. | Phase 1: hard rule 1 bars `llm` from `verified` in both `compute_confidence` (`enrichment/confidence.py:236–238`) and `validate` (`:300–310`); `comparable(a, b)` returns true only for equal scales (`enrichment/provenance.py:188–197`); `llm_self_reported` "must never be read as a measurement" (`:135–138`). | Phase 2: the adjudicator's self-reported merge confidence is coerced to `[0, 1]` (`dedup/adjudicator.py:124–136`), reduced per cluster to the member minimum (`dedup/scoring.py:1138–1148`) and thresholded at `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` (`dedup/scoring.py:50`) to gate a merge. Both are defensible in their own terms; the shipped word `confidence` names two incommensurable quantities across the two phases, and no code maps one onto the other — the shape of G-23. | 06b `G-102` |

---

## 8.4 Register — low (G-64 … G-92, G-100, G-101)

| # | Statement | Side A — the code at `86d173b` | Side B — the artefact that contradicts it, or the absence | Source |
|---|---|---|---|---|
| **G-64** | `_audit_rows` is documented as the one detection path, and `/issues/compare` bypasses it. | `api/routes.py:475–490` repeats `_rows_to_records`, `_present_fields`, `detect_issues`, `_flag_for_review` and `_flag_codes` inline inside `_audit_upload`. | `api/routes.py:767`: "The one detection path behind both ``/issues`` and ``/issues/json``" — the docstring names only those two routes, so the claim is narrow, but the duplication is a second detection path in fact, and it is the path the before/after comparison runs on. | 00 `⚠-2` |
| **G-65** | `_process_block`'s return annotation is a 2-tuple and the function returns a 3-tuple. | `dedup/adjudicator.py:1326`: `-> tuple[List[DedupResultRow], BlockStats]`; `:1417`: `return out, stats, entities`; unpacked as three at `:1496`. | The annotation is wrong, not the code. | 00 `⚠-5` |
| **G-66** | Nine public functions are defined and referenced nowhere, including by tests. | `docs/thesis/00_INVENTORY.md` §0.6 scan 2. | — (listed, not deleted, per the pass rule.) | 00 `⚠-9` |
| **G-67** | `scripts/cache_state.py` is neither imported nor executable as a script. | `scripts/cache_state.py:1` is a docstring; there is no `__main__` block. | No module imports it. | 00 `⚠-10` |
| **G-68** | The `detect_issues` docstring states the number of codes the flag route drives and it is wrong. | `FLAG_CODE_ISSUES` maps 13 tokens onto 7 distinct codes (`enrichment/issue_detection.py:1515–1559`). | `enrichment/issue_detection.py:1680–1681`: "*flag_codes* carries the enriched record's ``Flag Codes`` and drives six codes through :data:`FLAG_CODE_ISSUES`". | 01 `⚠-17` |
| **G-69** | The README use-case table is four use cases out of date. | The code declares and emits UC 14, 15, 16 and 17 (`enrichment/preprocess.py:2588`, `:1133`, `:1112`, `:2448`). | `README.md:1336–1350` lists UC 0 and UC 2–13 only. | 01 `⚠-20` |
| **G-70** | The UC 4 gate carries a condition the README does not state; the UC 5 gate is wider than the README trigger. | `can_do_contact_lookup` requires `not multi_contact` in addition to research institution, contact present and domain known (`enrichment/orchestrator.py:8778–8783`). `can_canonical` admits `routing_type ∈ {research_institution, company}` with any resolved `name1_enriched` (`:8790–8793`), so a GLEIF-resolved company qualifies. | `README.md:1341` states the UC 4 trigger as "Contact present, ROR hit, domain known" — a record with two contacts is silently excluded. `README.md:1342` states the UC 5 trigger as "Name2 present, ROR hit, no child match". | 01 `⚠-21` |
| **G-71** | The reproducibility gate has no test. | `tools/run_diff.py:82`, `:142`, `:196`, `:231`. | No test module names `tools/run_diff.py` as its subject (`docs/thesis/00_INVENTORY.md` §0.7.4). Pass 18 depends on this script for its determinism result. | 01 `⚠-22` |
| **G-72** | Every ADF activity has `retry: 0`, so a single transient HTTP failure fails the run. | `retry` is `0` and `retryIntervalInSeconds` is `30` on all thirteen policy-carrying activities across the four pipelines (`ForEach1` carries none), e.g. `adf/deduplication_pipeline.json:11–12`. | The enrichment pipeline merges **inside** its `ForEach` (`adf/enrichment_pipeline.json:136`), so a failure part-way leaves earlier pages committed and later pages not — a partially written group code with no compensating action. | 02 `⚠-32`, 04 `9.10` |
| **G-73** | The two enrichment Lookups order by different keys. | `Lookup2` counts rows with `ROW_NUMBER() OVER (ORDER BY (SELECT NULL))` (`adf/enrichment_pipeline.json:21`); `Lookup1` pages with `ORDER BY [code]` (`:68`). | The offsets are a fixed arithmetic series and only the page read's ordering assigns rows to pages, so the cover stays disjoint. Recorded because the two orderings read as if they were meant to agree. | 02 `⚠-33` |
| **G-74** | A UC 0 merge is invisible in the shipped name columns when the merged value re-cuts to the same pieces. | `chunk_name("National Technology & Engineering Solutions of Sandia LLC")` returns exactly the source block (`enrichment/name_repack.py:107–135`). | `data/eval/S3_post.xlsx` row `13212777` ships the two fragments unchanged; the merge is visible only in `Operating Name`, `Domain` and `Search Term 1`. A per-column diff of pre against post scores this record as untouched. | 03 `03-2` |
| **G-75** | UC 15 Case B (`_has_legal_suffix`) does not fire on the corpus's `C/O <company>` rows. | `enrichment/preprocess.py:1392–1403`, `:1295`, `:1563–1565`. `"Parallon Business Solutions"` carries no legal form, so the value routes through Case E instead. | `data/eval/S4_pre.xlsx` row `13343538`. The routing outcome is the same; the recorded classification differs, so a per-case count of UC 15 is wrong. | 03 `03-3` |
| **G-76** | `_tiebreak_key`'s ordering is marked UNCONFIRMED in its own docstring. | `dedup/scoring.py:1048–1059`. | The ordering is in force for every election in `data/eval/stress_200_scored.xlsx`. Nothing in the repository confirms it. | 03 `03-10` |
| **G-77** | `missing_building_inconsistency` is a declared `ISSUE_TYPES` member that no branch emits. | `dedup/scoring.py:430–433`, `:437`. | Documented as reserved for a Phase 1 building differentiator. A consumer enumerating the tuple will never see it, so the `DedupIssue` vocabulary's declared size overstates its emitted size by one. | 03 `03-11` |
| **G-78** | `S4_post.xlsx` carries an extra column the other four post files do not. | 85 columns against 83–84 elsewhere; the extra column is `Name`, holding the input Name 1 (`docs/thesis/03b_EXEMPLARS.md` §3b.5.4, §3b.8.1). | The post files are not one schema. | 03b `3b-9` |
| **G-79** | The v2 flag state of the run that produced the stress workbooks is not recorded in any output column. | At least one v2 flag was set, inferred from the populated `Link ID` column (`dedup/flags.py:55–63`; `data/eval/stress_200_scored.xlsx`). | Which of the three flags were set is recorded nowhere. The v2 behaviours Pass 03b attributes to the run are consistent with the output but not asserted by it. | 03b `3b-10` |
| **G-80** | `DEFAULT_MAX_CONCURRENCY` is reported and never applied. | `settings.default_max_concurrency` (`config.py:592–593`) is read at exactly one site, the `/config` diagnostics response (`api/routes.py:1656`, model field `api/models.py:890`). | The concurrency the pipeline uses comes from `EnrichmentOptions.max_concurrency`, whose default is the literal `5` at `api/models.py:306` and `api/routes.py:703`. Raising the variable changes the diagnostics output and nothing else. | 04 `9.2` |
| **G-81** | `UNDECIDABLE_WRITES` parsing is substring-based. | `(os.getenv("UNDECIDABLE_WRITES") or "").replace("on", "true").replace("off", "false")` (`config.py:251–252`). | The substring `on` is rewritten anywhere in the value, not only as a whole token, before `_bool` sees it. | 04 `9.4` |
| **G-82** | GLEIF defaults are stated in two places. | `enrichment/tier1_lei.py:567–570` and `:803–806` repeat `base_url`, `timeout`, `max_retries` and `threshold` as function-signature defaults. | The `LEIClient` passes the `Settings` values (`enrichment/tier1_lei.py:919–924`). Both sets agree at this commit, so nothing is wrong today and the duplication is the exposure. | 04 `9.6` |
| **G-83** | Eighteen environment variables are read by code and absent from `.env.example`. | `AZURE_OPENAI_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `LLM_SSL_VERIFY`, `LLM_HTTP_CONNECT_TIMEOUT`, `LLM_HTTP_TIMEOUT`, `LLM_FALLBACK_AUTHORITATIVE`, `UNDECIDABLE_WRITES`, `DEPT_SPLIT_CANONICALISES`, `ACCEPTED_DOMAIN_CITY_WITHDRAWAL_ENABLED`, `NAME_FIELD_WIDTH`, `LOG_FILE`, `WEBSITE_TRACE`, `RETRY_TRACE`, `DEDUP_V2_BLOCKING`, `DEDUP_V2_NAME2`, `DEDUP_V2_ID_CONFLICT`, `DEDUP_FIXTURE_CACHE_DIR`, `DEDUP_FIXTURE_CACHE_MODE` (`docs/thesis/04_PARAMETERS.md` §9.7). | A reader configuring from `.env.example` alone cannot reach them, including the three `DEDUP_V2_*` flags whose state G-79 shows is unrecoverable from output. | 04 `9.7` |
| **G-84** | The Function App timeout is not in the repository. | `host.json` sets no `functionTimeout` (`host.json:1–20`). | ⚠ MEASUREMENT REQUIRED — the value in force is an Azure Function App application setting; `az functionapp config appsettings list` or the portal would supply it. The per-batch Web activity timeouts in ADF are set (`docs/thesis/02_ARCHITECTURE.md` §2.2) and the function-side ceiling is not. | 04 `9.9` |
| **G-85** | The enrichment merge's source path and target column disagree on a name. | `sql/usp_merge_legacy_enriched.sql:72` reads `$."Unloading Point"` into `#src.[Unloading]`; `:86` writes `tgt.[Unloading]`. | `api/output_columns.py:75` declares the response column `Unloading Point`. Every other merged pair shares one name, so the Legacy column name is not derivable from the file schema. | 05 `⚠-35` |
| **G-86** | A record with no identifier cannot receive its issues, and nothing reports the drop. | `IssueDetectionResult.record_id` is `(customer or ecc_customer_number or "").strip()` (`api/models.py:242–244`, model field `:923–925`). | `sql/usp_merge_legacy_issues.sql:66` joins that value to `tgt.[Customer]`; an empty string matches nothing and the record's codes are dropped without an error. The file route excludes such rows explicitly and logs them (`api/routes.py:459–461`); the JSON route returns them with an empty id. | 05 `⚠-45` |
| **G-87** | ROR and GLEIF send no `User-Agent`. | `enrichment/tier1_ror.py:1083–1085` and `enrichment/tier1_lei.py:626–629` construct clients with no `headers` beyond GLEIF's `Accept`. | `enrichment/wikidata.py:695–697` records that an anonymous bulk caller is the one most likely to be rate-limited, and sets one. The two authorities are the clients least able to absorb a 429 (G-54). | 06 `4` |
| **G-88** | `certifi` is an undeclared direct dependency. | Imported at three sites (`docs/thesis/06_EXTERNAL_DEPS.md` §8). | Absent from `requirements.txt`. It is present transitively today; a resolver that drops the transitive edge breaks the pinned-trust-store fetches of G-53. | 06 `7` |
| **G-89** | No internationalisation evaluation set exists at this commit. | `docs/thesis/07_EVALUATION.md` §7. | All sixteen workbooks in `data/eval/` are US-only. `SERP_COUNTRY_LOCALISATION_ENABLED` (`config.py:355–359`) sets the SERP country from the record's country, and no committed dataset exercises it with a non-US value. | 07 `07-11` |
| **G-90** | The workbooks name the country column `Country/Region Key`; the documentation prompt asks for `Country`. | `docs/thesis/07_EVALUATION.md` Appendix A — every `data/eval/` workbook uses `Country/Region Key`. | `docs/thesis-doc-prompt-v2.md:157`: "country distribution (count `Country` column)". Pass 18's script must read the repository's header, not the prompt's. | 07 `07-12` |
| **G-91** | One harness script modifies a repository file. | `scripts/issue_catalogue_census.py --write-oracle` writes into `PresentationTestData.xlsx` (`scripts/issue_catalogue_census.py:250`, `:274–275`). | It is the only script in the harness that does. Pass 18's own script must be read-only; running the census with that flag would dirty the tree the documentation set is pinned to. | 07 `07-13` |
| **G-92** | The stress scoring run went to the network and is not reproducible from the repository. | `stress_200_scored.xlsx::Run` records `fixture_cache off`; its 39 LLM calls were live. | `docs/thesis/07_EVALUATION.md` §3.3. Combined with G-55, no Phase 2 result in `data/eval/` can be replayed. | 07 `07-14` |
| **G-100** | The origin invariant governs a field that never ships, so its effect is unauditable from any output. | `_slot_origin` is maintained at one funnel (`enrichment/orchestrator.py:1711`, invariant at `:1731–1732`, guard at `:1750–1753`) and popped before serialisation (`:3287`). | No response field, workbook column or table carries it. The seven records the invariant's own comment names as having lost `relocated-unverified` (`:1743–1747`) could be identified only by instrumenting a run, not by reading an output. Same shape as G-12. | 06b `G-100` |
| **G-101** | One telemetry record writes a person's name and cannot be joined to a record. | `enrichment/person_affiliation.py:180` logs `{"step", "contact", "query", "institution", "department", "confidence"}`. | It is the only one of the 86 idiom-(b) records with no `record_id` (`docs/thesis/06b_CROSSCUTTING.md` §A.2), and the only one that puts a contact name in a log line. `enrichment/provenance.py:19–24` states the opposite policy for the provenance store: `contact`, `care_of` and `email` are out of scope precisely so the store carries no personal data. | 06b `G-101` |

---

## 8.5 Not exported, not present

Absences that no register entry above is a contradiction about: the thing is named somewhere
and no artefact for it exists in this repository. None may be described from memory.

| Thing | Named at | State | Register entry |
|---|---|---|---|
| `Entity_BasicFlow` — the pipeline that would express the run order | `docs/thesis-doc-prompt-v2.md:78`, `:270` | ⚠ NOT EXPORTED. `grep -rn 'Entity_BasicFlow' .` returns hits only in `docs/`. No `ExecutePipeline` activity exists anywhere in `adf/`, so no run order is expressed in the repository at all | G-26 |
| Address validation / auto write-back above 80% confidence (DS workflow step 6) | `docs/thesis/CONTEXT-EXTERNAL.md:423`, open item `:442` | ⚠ NOT EXPORTED and ⚠ NOT PRESENT in code | G-58 |
| Consolidation pipeline (`POST /api/preprocess/consolidate/file`, first step of the production sequence) | `README.md:3441` | ⚠ NOT EXPORTED. The endpoint exists; no ADF JSON calls it | — |
| DS process invocations (`LegacyMapping`, `MigrateData`, `ProcessValidation`) as ADF stored-procedure activities | `docs/thesis/CONTEXT-EXTERNAL.md:349–352` | ⚠ NOT EXPORTED | — |
| ADF datasets `AzureSqlMITable1`, `AzureSqlMITable3`; linked services `ls_sqlmi_legacy`, `ls_sqlmi_validation`; `AutoResolveIntegrationRuntime` | the four exported pipelines | ⚠ NOT EXPORTED | G-33, G-21 |
| DDL for Import, Legacy, Validation, load file | — | ⚠ NOT PRESENT. No `.sql` file in the tree creates or alters a table (§A.2) | G-08 |
| Network approvals, firewall rules, private endpoints, managed identity | — | ⚠ UNVERIFIED. No file records any of these; no networking JSON is tracked | — |
| The four evidence-cache namespaces `serp`, `registry`, `fetch`, `llm` | `.gitignore:37–40` | ⚠ NOT PRESENT | G-13 |
| Internationalisation evaluation set | — | ⚠ NOT PRESENT | G-89 |
| Custom Application Insights telemetry | `host.json:3–10` enables platform App Insights logging (`"isEnabled": true`, `"excludedTypes": "Request"`) | ⚠ NOT PRESENT. No application code emits a custom metric or event: a case-insensitive search for `applicationinsights`, `opencensus`, `azure.monitor`, `opentelemetry` and `instrumentation_key` hits `host.json:4` and nothing else (§A.6). `enrichment/provenance.py:36–38` states this as policy — "App Insights stays operational monitoring" | G-93, G-94 |
| A Notion export of the Issue Catalogue | referenced by URL only | ⚠ NOT PRESENT (§8.9) | — |
| The SAP load that consumes the load file (edge `e21`) | `docs/thesis/CONTEXT-EXTERNAL.md:343` | ⚠ UNVERIFIED — the load file is named as the terminus; the load itself is not evidenced here | — |

---

## 8.6 Measurement required

Numbers the thesis needs that exist only in a live run or an external console. Each names the
command or console path that would produce it. None is asserted anywhere in this documentation
set.

| Quantity | Source that would supply it | Register entry |
|---|---|---|
| Azure Function App `functionTimeout` in force | `az functionapp config appsettings list`, or the portal's Application Settings | G-84 |
| Azure OpenAI endpoint value | Azure portal, or the Function App's Application Settings | — |
| Azure OpenAI TPM/RPM quota for `MDM-Apoorva-gpt-5.4` | Azure AI Foundry deployment blade | — |
| SerpAPI rate limit and unit price | the SerpAPI account dashboard for the key in `SERPAPI_KEY` | — |
| Azure OpenAI price per 1K tokens, Phase 1 | Azure pricing for the deployment at `AZURE_OPENAI_ENDPOINT` | G-57 |
| Azure OpenAI price per 1K tokens, Phase 2 | Azure pricing for `AOAI_DEPLOYMENT_DEDUP`; per-call token counts are already captured (`dedup/llm.py:244–245`) | — |
| ROR, GLEIF, Wikidata, DuckDuckGo rate ceilings | the published terms of use for each; free APIs, so the constraint is a rate, not a price | G-54 |
| Egress cost for arbitrary-host page fetches | Azure Cost Management → the `mdm-pipeline-api` Function App → Bandwidth meter, over a run of known `N` | — |
| Phase 1 calls per record per tier per stratum | one instrumented run; the batch summary already carries the counters (`docs/thesis/06_EXTERNAL_DEPS.md:521`) | G-57 |
| A committed tier trace per exemplar record | one run with `provenance` events persisted to a tracked path rather than `logs/` | G-12 |
| Phase 2 clustering and election for S2 and S4 | one `/api/dedup/cluster-block` + `/api/dedup/score` run over `S2_post.xlsx` and `S4_post.xlsx` | G-42 |
| The settings, cache state and catalogue version behind the ten stratum workbooks | not recoverable; a re-run under recorded settings is the only route | G-60, G-11 |
| The deployed factory's parameter bindings, if they differ from `adf/*.json` | the ADF authoring canvas for the four pipelines | G-05 |
| Whether the deployed Function App's auth level matches `function_app.py` | the Function App's configuration blade | G-16 |
| Whether the deployed Azure OpenAI deployment accepts `seed`, and whether any run has dropped it | one instrumented run against `AZURE_OPENAI_ENDPOINT`; the latch at `llm/openai_client.py:336` reaches no output column, summary field or provenance event, so no committed artefact can answer it | G-96 |

---

## 8.7 README and docstring against code

The register entries where the contradicting artefact is the repository's own prose. In every
one the code is the ground truth and the prose is the defect, per the pass rule.

| Register entry | Prose artefact | Says |
|---|---|---|
| G-04 | `enrichment/issue_detection.py` flag-code table, `api/output_columns.py` | `Flag Codes` is a first-class response column; the merge never writes it |
| G-09 | `README.md:1350` | UC 13 is "Lab → Parent Department Resolution" only |
| G-16 | `api/routes.py:1337–1339` | "same key/function-auth pattern as the other endpoints" |
| G-18 | `enrichment/orchestrator.py:8764–8766` | "tier 2 canonical / 2A / 2B / 3, any of which may settle Name 2" |
| G-24 | `enrichment/issue_detection.py:1693–1695` | "the whole G1-G6 set — of which G6 is entirely DS-origin" |
| G-31 | `sql/usp_merge_legacy_enriched.sql:83–84` | the only comment on the statement concerns `SPACE(0)`; the overwrite asymmetry is unremarked |
| G-36 | `enrichment/tier2a_contact.py:476–490` | the comment records the scale mismatch as a defect left in place |
| G-40 | `llm/prompts.py:1–4` | "All LLM prompt strings as module-level constants. Centralised here" |
| G-48 | `config.py:121`, `.env.example:105` | `MAX_PAGE_CONTENT_CHARS=3000` |
| G-49 | `config.py:257`, `:268` | "Off by default" against a `True` default |
| G-64 | `api/routes.py:767` | "The one detection path" |
| G-65 | `dedup/adjudicator.py:1326` | a 2-tuple return annotation on a 3-tuple return |
| G-68 | `enrichment/issue_detection.py:1680–1681` | "drives six codes"; the table maps 13 tokens onto 7 |
| G-69 | `README.md:1336–1350` | UC 0 and UC 2–13 only |
| G-70 | `README.md:1341`, `:1342` | UC 4 and UC 5 triggers narrower and wider respectively than the gates |
| G-76 | `dedup/scoring.py:1048–1059` | the tie-break docstring marks its own ordering UNCONFIRMED |
| G-77 | `dedup/scoring.py:430–433` | `missing_building_inconsistency` declared and reserved |
| G-01, G-17 | `tests/KNOWN_FAILURES.md:3–5`, `:7`, `:41`; `eval/out/RUNS.md:370` | a gate that does not exist, and a manifest that does not match |
| G-27 | `docs/thesis/CONTEXT-EXTERNAL.md:188`, `:226`, `:281` | ADF JSON superseded by the exported files |
| G-62 | `eval/out/RUNS.md` | S2/S3/S5 inputs unavailable |
| G-90 | `docs/thesis-doc-prompt-v2.md:157` | "count `Country` column"; the workbooks say `Country/Region Key` |
| G-22 | `docs/thesis-doc-prompt-v2.md:66` | "FR-1…FR-36" |
| G-93 | `api/middleware.py:1` | "structured JSON logging" over a plain-text `logging.Formatter` |
| G-95 | `tools/run_diff.py:204` | "Seven of the sixty-seven output columns are provenance"; `RESPONSE_COLUMNS` is 69 |
| G-99 | `api/models.py:546–549` | "ADF decides what, if anything, to store"; at this commit it stores none of it |
| G-101 | `enrichment/provenance.py:19–24` | `contact`, `care_of` and `email` out of scope so the store carries no personal data — against a telemetry line that logs a contact name |

---

## 8.8 Load-bearing for Pass 18

The subset that constrains what Pass 18 can compute, and what it must say when it cannot.

| Register entry | Constraint on Pass 18 |
|---|---|
| G-10 | Must key every workbook read by header, must state which `Issues` occurrence it read for `S1_pre`, and must compare code **sets**, not strings. |
| G-11 | Every pre/post count is measured against a catalogue older than `86d173b`; the withdrawn codes present in the files must be reported, not silently dropped. |
| G-23 | `ISSUE_CATALOGUE` counts and `DedupIssue` counts go in separate tables and are never summed. |
| G-25 | The per-group table must state whether it counts on the declared `group` field or the code prefix. |
| G-14 | Scoring figures must be restricted to the 83 non-seeded rows of `stress_200_scored.xlsx`. |
| G-15 | No `expected_cluster` / `expected_routing` column exists; `gt_expected_action` in `dedup_STRESS_200_v1-verified.xlsx` is the only ground truth, and `eval/dedup_eval.py` does not read it. |
| G-42 | Clustering and election for S2 and S4 are `⚠ NOT MEASURED`. |
| G-44 | `expected_issue_codes` is not ground truth for the emitted `Issues`. |
| G-46 | Post-only codes are partly the detector reading a longer correct string, not new defects. |
| G-13, G-92, G-55 | No frozen replay is reproducible from the repository alone; the determinism result must state which caches were present. |
| G-57 | Phase 1 cost is `⚠ MEASUREMENT REQUIRED`; Phase 2 token counts are available. |
| G-59, G-63 | Index-based reads break; the `t100` pair needs repair before any per-field comparison. |
| G-90 | The country column is `Country/Region Key`. |
| G-91 | Pass 18's script must be read-only and must not invoke `--write-oracle`. |
| G-71 | `tools/run_diff.py` has no test; its result is reported as observed, not as validated. |
| G-96 | The determinism result must state whether the run kept its `seed`. No artefact records the latch, so on any run that is not instrumented the answer is `⚠ UNVERIFIED`. |
| G-102 | Merge-confidence figures are Phase 2 quantities and must be labelled as such. They are not comparable with any Phase 1 confidence and must not be pooled with one. |

---

## 8.9 Notion against code

**No Notion export exists in this repository at `86d173b`.** `git ls-files | grep -ci notion`
returns `0` (§A.4). Notion is referenced by URL only, in `docs/14_SCORING_DOSSIER.md:21`,
`:1247` and `eval/out/RUNS.md:58`. A Notion-vs-code reconciliation of the Issue Catalogue
cannot be performed from repository evidence, and Notion remains the author's authority for
group membership.

What the repository does hold is three places where the code records a divergence from the
Notion catalogue in its own comments. These are the whole of the Notion-vs-code evidence
available here.

| # | Code side | Notion side, as the code reports it | State at `86d173b` |
|---|---|---|---|
| N-1 | `G4-NAME-015` is declared "Name Overflow Beyond the Name Block" (`enrichment/issue_detection.py:352`) | "v2 names this 'Name Overflow Beyond Name 4'. The name block is five slots wide as of the five-name-slot change, so the slot-agnostic wording is kept here and the divergence is reported for a Notion correction" (`enrichment/issue_detection.py:348–351`) | **Open.** The divergence stands; the correction is on the Notion side and cannot be verified here. |
| N-2 | `G2-VAL-004` fires on any record with a present-and-blank `Region`, with no country condition; pinned by `tests/test_issue_detection.py:509–520` across seven country values | The removed predicate `lambda r: _is_us(r)` was justified by "Catalogue v2 gates Region Missing on US records only" — a sentence that "appears nowhere except the comment that asserted it and the measurement script that copied it — no catalogue extract, no Notion row, no README table states it" (`enrichment/issue_detection.py:479–482`) | **Resolved in code.** The predicate is gone. Recorded because it is the one documented case of a Notion claim that had no Notion source, and because its effect was total: every blank-`Region` record in the demo corpus is German, so a mandatory DS-origin rule sat permanently dark (`:491–495`). |
| N-3 | `G3-ADDR-012` "Duplicate Street Across Fields" is declared (`enrichment/issue_detection.py:334–337`) and emitted (`:1325`) | It "held this status [unlisted] while its absence from the Catalogue v2 G3 table was open; it was resolved live on 2026-09-06" (`enrichment/issue_detection.py:51–52`) | **Resolved.** The module's own census now reads "**0 unlisted**" (`:51`). `docs/15_ISSUES_DOSSIER.md:382`, `:392`, `:396`, which reports it as open and states "I cannot enumerate 'Notion rows with no code counterpart'", predates this commit (§8.10, D-5). |

`enrichment/issue_detection.py:56–60` records the module's own withdrawal history — ten
withdrawn codes, of which `G2-CONTACT-008` and `G2-CONTACT-009` are "struck through in
Catalogue v2" and eight were withdrawn locally on 2026-09-06 and 2026-09-07. Whether the eight
local withdrawals are reflected in Notion is ⚠ UNVERIFIED from this repository.

---

## 8.10 Gaps in the documentation set itself

Not gaps in the system. Recorded because rule 6 requires them and because each changes how a
later pass must read an earlier one.

| # | Statement | Evidence | Effect |
|---|---|---|---|
| **D-1** | The tree is not clean, so rule 1 is not satisfied as written. | `git status --porcelain` (§8.0.4) lists ten modified paths, every one a `docs/thesis/*.md` output of this same run. No source, fixture, SQL or ADF file is modified. | The commit every citation addresses is intact. This pass proceeds; the MANIFEST must record that the set was written against a tree dirty only in its own outputs. |
| **D-2** | Pass 06b was regenerated after the first writing of this file, so this register is the second writing. | `docs/thesis/06b_CROSSCUTTING.md` now heads `Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 06b` (§A.4). The superseded version at `HEAD` heads `2026-08-17 · 515cc7c1… · diag/website-trace` and carries 41 `⚠` lines against the regenerated file's 0 (§A.6). | The first writing covered Passes 00–07 minus 06b and said so; this one covers all ten. Ten register entries (`G-93` … `G-102`), fifteen re-evidenced entries (§8.0.2) and one documentation-set gap (D-7) enter here that the first writing did not carry. The determinism, fail-open-`except` and origin-invariant material Pass 12 and Pass 18 depend on is now in the register. |
| **D-3** | Pass 01 was re-headed, not re-derived. | `docs/thesis/01_TRACEABILITY.md:229`: the body was generated at `ea6f9d92168d3de949d369ed54a58b5a745a59b7`. `git diff --stat` between that commit and `86d173b` touches no Python source — four `adf/*.json` (one line each), four `sql/*.sql` (whitespace-only) and two `docs/thesis/*.md`. | Its citations address the same bytes at both commits, so they hold. Its `⚠` numbering does not — see §8.1. |
| **D-4** | Two passes state different disagreement counts for the same pair of columns in `S1_pre.xlsx`. Both are correct; they partition differently. | Pass 03b: "Agreement: **30 of 100 rows**" (`docs/thesis/03b_EXEMPLARS.md` §3b.0.4). Pass 07: "identical 28, different 56, only col2 13, only col7 1, both empty 2" (`docs/thesis/07_EVALUATION.md:194`, §3.2). Re-measured here (§A.3): identical 28, different 56, only col2 13, only col7 1, both empty 2 — so agreement is 28 + 2 = 30 and disagreement is 56 + 13 + 1 = 70. | Pass 07's "56" counts rows where both columns are non-empty and differ; Pass 03b's "70" counts every row where the two are not the same value, including the 14 one-sided rows. G-10 states the disagreement as 70 of 100 and names the partition. Pass 18 must do the same. |
| **D-5** | The three dossiers under `docs/` are at an older commit than the pass set and at least one of their findings is stale. | `docs/15_ISSUES_DOSSIER.md:382`, `:392`, `:396` reports `G3-ADDR-012` as emitted-but-absent-from-v2 and unresolvable; `enrichment/issue_detection.py:51–52` records it resolved on 2026-09-06 and the census as "0 unlisted". | Passes 13, 14 and 15 regenerate these at `86d173b`. Until then the dossiers are not evidence, and no entry in this register is drawn from them. |
| **D-6** | Passes 03, 04 and 06 grade no severity. | Their gap sections carry no severity column. | 29 of the 102 canonical entries carry a grade assigned by this pass rather than by their source. One entry departs from a grade its source did assign: G-33 (02 `⚠-34`, low → medium). G-02 is graded high here although Pass 03 assigns none; both say so in their own row. |
| **D-7** | Pass 01 contradicts itself about the merge procedures, and the false half is given as the reason a citation cannot be made. | `docs/thesis/01_TRACEABILITY.md:185`, row U-31: "Each file is one physical line, so no statement inside them is separately citable (Pass 00 ⚠-8)". The same document at `:229` records that the `86d173b` diff includes "four `sql/*.sql` (whitespace-only reformatting)", and `wc -l sql/*.sql` returns 89, 69, 66 and 81 (§A.6); commit `86d173b`'s own message is "sql: reformat merge procs; adf: parameterised pipelines". Raised by Pass 06b (`docs/thesis/06b_CROSSCUTTING.md:847–854`). | Every SQL citation in Passes 02, 05, 06b and in this register addresses a line in the reformatted files, and they hold. Pass 01 needs the sentence removed. Its `(Pass 00 ⚠-8)` attribution is to the pre-`86d173b` Pass 00 numbering, the collision D-3 and §8.1 record. |

---

## 8.11 Counts

| Measure | Value |
|---|---|
| Raw entries in the gap sections of Passes 00–07 and 06b | 115 |
| Duplicate rows merged | 13 |
| **Canonical entries** | **102** (`G-01` … `G-102`) |
| high | 17 |
| medium | 54 |
| low | 31 |
| Entries with no source grade (Passes 03, 04, 06), graded here | 29 |
| Entries where this pass departs from a source grade | 1 (G-33, low → medium) |
| Entries re-evidenced by Pass 06b without being re-raised | 15 (§8.0.2) |
| Entries whose contradicting artefact is the repository's own prose | 27, in 26 rows (§8.7) |
| Entries load-bearing for Pass 18 | 20, in 17 rows (§8.8) |
| Notion-vs-code items, of which open | 3, of which 1 open (§8.9) |
| Documentation-set gaps | 7 (`D-1` … `D-7`) |
| Passes covered | 00, 01, 02, 03, 03b, 04, 05, 06, 06b, 07 |
| Passes not covered | none |

Distribution by originating pass, after deduplication (an entry with several sources is
counted once, against its highest-severity source):

| Pass | high | medium | low | total |
|---|---|---|---|---|
| 00 | 1 | 6 | 4 | 11 |
| 01 | 1 | 5 | 4 | 10 |
| 02 | 2 | 7 | 2 | 11 |
| 03 | 1 | 8 | 4 | 13 |
| 03b | 3 | 6 | 2 | 11 |
| 04 | 0 | 3 | 5 | 8 |
| 05 | 4 | 2 | 2 | 8 |
| 06 | 0 | 6 | 2 | 8 |
| 06b | 2 | 6 | 2 | 10 |
| 07 | 3 | 5 | 4 | 12 |
| **total** | **17** | **54** | **31** | **102** |

---

## Appendix A — commands and verbatim output

Every command run by this pass. All are read-only over local files.

### A.1 Commit, branch, date, and the `KNOWN_FAILURES` gate

    $ git rev-parse HEAD
    86d173b8a4d715a619b0a2656986c145da7fa81e

    $ git rev-parse --abbrev-ref HEAD
    feature/llm-fixes

    $ date -I
    2026-09-07

    $ grep -rn 'KNOWN_FAILURES' --include='*.py' .
    (no output)

### A.2 The claims re-verified in `sql/` and `adf/`

    $ grep -c 'Flag Codes' sql/usp_merge_legacy_enriched.sql
    0

    $ python3 -c "... walk activities, print stored procedure name and parameter keys ..."
    adf/enrichment_pipeline.json (inside ForEach) | proc: Mapping.usp_merge_legacy_enriched | params: ['payload']
    adf/issues_pipeline.json | proc: Mapping.usp_MergeLegacyIssues | params: ['payload']
    adf/deduplication_pipeline.json | proc: Mapping.usp_MergeValidationClusters | params: ['payload']
    adf/scoring_pipeline.json | proc: Mapping.usp_MergeValidationScores | params: ['payload']

The enumeration walks `properties.activities` and recurses into `ForEach` activities, printing
every `SqlServerStoredProcedure`. It confirms G-19 (one of four names a procedure no `sql/`
file creates) and G-05 (all four pass exactly one of the three required parameters).

    $ sed -n '1,20p' sql/usp_merge_legacy_issues.sql | tr ',' '\n' | grep -n 'target_column'
    8:    @target_column SYSNAME = N'Issues'
    17:    IF @target_column NOT IN (N'Issues Before'
    21: N'target_column must be Issues Before or Issues.'

    $ grep -rn 'run_tier2b' --include='*.py' .
    tests/test_tier2b.py:13:from enrichment.tier2b_dept import run_tier2b
    tests/test_tier2b.py:36:        result = await run_tier2b(
    tests/test_tier2b.py:61:        result = await run_tier2b(
    tests/test_tier2b.py:81:        result = await run_tier2b(
    tests/test_tier2b.py:100:        result = await run_tier2b(
    enrichment/tier2b_dept.py:48:async def run_tier2b(

    $ grep -n 'Terms of Payment' api/models.py api/output_columns.py
    api/output_columns.py:94:    "terms_of_payment": "Terms of Payment",
    api/models.py:210:        validation_alias=AliasChoices("Terms of Payment Contact", "terms_of_payment_contact"),

    $ grep -rln "CREATE TABLE\|ALTER TABLE" --include="*.sql" --include="*.py" --include="*.md" .
    docs/thesis/08_GAPS.md
    docs/thesis/05_DATA_MODEL.md

The only two hits are this file and `05_DATA_MODEL.md`, which quote the search pattern itself.
No `.sql`, `.py` or other `.md` file in the tree contains either statement, so G-08 stands: no
DDL for any persisted table exists in the repository.

### A.3 `S1_pre.xlsx` — reconciling the two disagreement counts (D-4, G-10, G-63)

    $ python3 - <<'PY'
    import openpyxl
    wb = openpyxl.load_workbook('data/eval/S1_pre.xlsx', read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    idx = [i for i, h in enumerate(hdr) if h == 'Issues']
    print('Issues header at 0-based columns:', idx, '| data rows:', len(rows) - 1)
    a, b = idx
    ident = diff = onlya = onlyb = bothempty = 0
    for r in rows[1:]:
        x = (r[a] or '').strip(); y = (r[b] or '').strip()
        if not x and not y: bothempty += 1
        elif x and not y:   onlya += 1
        elif y and not x:   onlyb += 1
        elif x == y:        ident += 1
        else:               diff += 1
    print(f'identical {ident}, different {diff}, only col{a} {onlya}, only col{b} {onlyb}, both empty {bothempty}')
    print('agree (identical + both empty) =', ident + bothempty)
    print('disagree (different + one-sided) =', diff + onlya + onlyb)
    PY
    Issues header at 0-based columns: [2, 7] | data rows: 100
    identical 28, different 56, only col2 13, only col7 1, both empty 2
    agree (identical + both empty) = 30
    disagree (different + one-sided) = 70

### A.4 Documentation-set state, and the search for a Notion export

    $ for f in docs/thesis/0*.md; do printf "%-36s %s\n" "$f" "$(head -1 "$f" | sed 's/Generated: //')"; done
    docs/thesis/00_INVENTORY.md          2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 00
    docs/thesis/00_OPEN_ITEMS.md         2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace
    docs/thesis/01_TRACEABILITY.md       2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 01
    docs/thesis/02_ARCHITECTURE.md       2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 02
    docs/thesis/03_ALGORITHMS.md         2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 03
    docs/thesis/03b_EXEMPLARS.md         2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 03b
    docs/thesis/04_PARAMETERS.md         2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 04
    docs/thesis/05_DATA_MODEL.md         2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 05
    docs/thesis/06_EXTERNAL_DEPS.md      2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 06
    docs/thesis/06b_CROSSCUTTING.md      2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 06b
    docs/thesis/07_EVALUATION.md         2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 07
    docs/thesis/08_GAPS.md               2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 08
    docs/thesis/09_DECISIONS.md          2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 09

The `08_GAPS.md` line above is the header written by the first writing of this file, which
this one overwrites. `00_OPEN_ITEMS.md` is not a pass output of this run and is not a source.

    $ git ls-files | grep -ci notion
    0

    $ grep -rn -i 'notion' --include='*.py' .
    tests/test_issue_detection.py:514:    Notion row or README table — while 03_ALGORITHMS.md documents the rule as
    enrichment/issue_detection.py:351:    # here and the divergence is reported for a Notion correction.
    enrichment/issue_detection.py:482:# copied it — no catalogue extract, no Notion row, no README table states it.
    enrichment/batch_consensus.py:263:    # exactly as the enriched side is and no third notion of "same name"
    enrichment/orchestrator.py:412:    "wixsite.com", "squarespace.com", "webflow.io", "notion.site",
    enrichment/dept_block.py:191:    One notion of "fuller", shared with :func:`same_unit`'s truncation arm:

The last three matches are the English word and a domain suffix, not the product. The first
three are N-1 and N-2 in §8.9.

    $ grep -n 'G3-ADDR-012' enrichment/issue_detection.py
    51:* **0 unlisted** — ``G3-ADDR-012`` held this status while its absence from the
    335:        "G3-ADDR-012", "G3", "Duplicate Street Across Fields", "Street", False, "API",
    1311:    # G3-ADDR-012 — the same street address appears in more than one slot.
    1325:        found.add("G3-ADDR-012")

### A.5 Gap-section extents in the source passes

    $ for f in 00_INVENTORY 01_TRACEABILITY 02_ARCHITECTURE 03_ALGORITHMS 03b_EXEMPLARS \
        04_PARAMETERS 05_DATA_MODEL 06_EXTERNAL_DEPS 06b_CROSSCUTTING 07_EVALUATION; do \
        printf "%-20s %4d lines, %3d lines carrying ⚠\n" "$f" \
          "$(wc -l < docs/thesis/$f.md)" "$(grep -c '⚠' docs/thesis/$f.md)"; done
    00_INVENTORY         1077 lines,  31 lines carrying ⚠
    01_TRACEABILITY       229 lines,  22 lines carrying ⚠
    02_ARCHITECTURE       556 lines,  34 lines carrying ⚠
    03_ALGORITHMS        2963 lines,  13 lines carrying ⚠
    03b_EXEMPLARS        1228 lines,  21 lines carrying ⚠
    04_PARAMETERS         768 lines,   6 lines carrying ⚠
    05_DATA_MODEL        1031 lines,  31 lines carrying ⚠
    06_EXTERNAL_DEPS      615 lines,  17 lines carrying ⚠
    06b_CROSSCUTTING     1152 lines,   0 lines carrying ⚠
    07_EVALUATION         832 lines,  11 lines carrying ⚠

The `⚠` line count is not the item count: a pass states some items without the glyph inside its
own gap table, and repeats others in the body. The item counts in §8.0.1 are read from each
pass's gap section, not from this grep. Pass 06b is the clearest case — 0 lines carry the glyph
and its gap section states ten graded items in a table.

### A.6 The Pass 06b claims re-verified here

The ten entries Pass 06b contributes are read into this register on its evidence. The two it
grades high, and the file-level facts the rest turn on, are re-read at first hand.

    $ sed -n '1p;87,91p' api/middleware.py
    """FastAPI middleware for structured JSON logging, request timing, and error handling."""
        fmt = (
            "%(asctime)s %(levelname)s %(name)s "
            "[%(funcName)s] %(message)s"
        )
        formatter = logging.Formatter(fmt)

The first line is the module docstring; the five that follow are the formatter it describes.
The output above is indented four spaces as a code block, over the file's own indentation.

    $ grep -n 'approval_status' sql/usp_merge_validation_scores.sql | cut -c1-118
    60:        approval_status NVARCHAR(30) N'$.approval_status',
    78:    DECLARE @sql NVARCHAR(MAX) = N'      MERGE ' + @tgt + N' AS tgt      USING #src AS src         ON tgt.Customer

    $ grep -c 'NOT MATCHED' sql/*.sql
    sql/usp_merge_legacy_enriched.sql:0
    sql/usp_merge_legacy_issues.sql:0
    sql/usp_merge_validation_clusters.sql:0
    sql/usp_merge_validation_scores.sql:0

    $ for f in sql/*.sql; do printf "%-40s %s\n" "$f" "$(grep -ci provenance "$f")"; done
    sql/usp_merge_legacy_enriched.sql        0
    sql/usp_merge_legacy_issues.sql          0
    sql/usp_merge_validation_clusters.sql    0
    sql/usp_merge_validation_scores.sql      0

    $ wc -l sql/*.sql
          89 sql/usp_merge_legacy_enriched.sql
          69 sql/usp_merge_legacy_issues.sql
          66 sql/usp_merge_validation_clusters.sql
          81 sql/usp_merge_validation_scores.sql
         305 total

    $ python3 -c "import api.output_columns as o; print(len(o.RESPONSE_COLUMNS))"
    69

    $ python3 -c "from enrichment.orchestrator import PROVENANCE_COLUMNS as p; print(len(p))"
    7

    $ grep -n 'Seven of the sixty' tools/run_diff.py
    204:    migration. Seven of the sixty-seven output columns are provenance, and

    $ grep -n '_SEED_SUPPORTED' llm/openai_client.py
    114:_SEED_SUPPORTED: bool = True
    119:    return _SEED_SUPPORTED
    124:    global _SEED_SUPPORTED
    125:    _SEED_SUPPORTED = True
    303:    global _SEED_SUPPORTED
    327:                **_params(_SEED_SUPPORTED)
    330:            if not (_SEED_SUPPORTED and _is_unsupported_seed(exc)):
    336:            _SEED_SUPPORTED = False
    448:                seed=LLM_SEED if _SEED_SUPPORTED else None,

    $ grep -rn -i 'applicationinsights\|opencensus\|azure\.monitor\|opentelemetry\|instrumentation_key' \
        --include='*.py' --include='*.json' --include='*.txt' .
    host.json:4:    "applicationInsights": {

    $ sed -n '185p' docs/thesis/01_TRACEABILITY.md | tr '|' '\n' | grep -n 'physical line'
    6: All four declare schema `[Mapping]`; the ADF activities that call them name `dbo` (Pass 00 ⚠-7). Two carry an unresolved `-- <<< confirm` marker on their `@db` declaration. Each file is one physical line, so no statement inside them is separately citable (Pass 00 ⚠-8).

    $ grep -c '⚠' docs/thesis/06b_CROSSCUTTING.md
    0

    $ git show HEAD:docs/thesis/06b_CROSSCUTTING.md | grep -c '⚠'
    41

The last pair is the whole of D-2: the superseded Pass 06b raised 41 `⚠` lines this register
could not import, and the regenerated one raises ten graded entries that it can.

---

Pass 08 complete: 115 raw entries from the gap sections of Passes 00, 01, 02, 03, 03b, 04, 05,
06, 06b and 07 collected, 13 duplicate rows merged, and the colliding `⚠-13` / `⚠-14` numbering
replaced by one canonical sequence `G-01` … `G-102` with a full source map — 17 high, 54 medium,
31 low, each carrying both sides; plus 15 measurement-required quantities, 12 not-exported or
not-present artefacts, 27 prose-against-code contradictions, 3 Notion-against-code items of
which 1 is open, and 7 gaps in the documentation set itself. Pass 06b is now regenerated at
`86d173b` and folded in: it contributes `G-93` … `G-102`, re-evidences fifteen existing entries,
and supplies D-7. No pass in the set is now outside this register.
