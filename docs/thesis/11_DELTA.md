Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 11

# Pass 11 — Delta against the pass-document baseline

## 11.0 · Baseline, ranges and tree state

The baseline is the commit recorded in the header of the previous `11_DELTA.md`:
`d4fc46938514c9a7d249979c4aa9b4ae4cf3e564`, branch `main`, generated 2026-08-21. It is an
ancestor of `HEAD`, so the comparison is a linear range.

```
$ git merge-base --is-ancestor d4fc46938514c9a7d249979c4aa9b4ae4cf3e564 HEAD ; echo "ancestor_exit=$?"
ancestor_exit=0
$ git rev-list --count d4fc469..HEAD
81
$ git rev-parse HEAD
86d173b8a4d715a619b0a2656986c145da7fa81e
$ git rev-parse --abbrev-ref HEAD
feature/llm-fixes
$ date -I
2026-09-07
```

**Two ranges, and why both appear below.** The pass documents that this delta supersedes do not
carry the baseline commit in their own headers. Every one of `00_INVENTORY.md` through
`09_DECISIONS.md` at `d4fc469` carries
`Generated: 2026-08-16` or `2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd ·
Branch: diag/website-trace`; only `11_DELTA.md` itself was regenerated at `d4fc469`.

```
$ for f in 00_INVENTORY 01_TRACEABILITY 04_PARAMETERS 08_GAPS 09_DECISIONS; do \
    git show d4fc469:docs/thesis/$f.md | head -1; done
Generated: 2026-08-16 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace
Generated: 2026-08-16 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace
Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace
Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace
Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace
```

| Range | Commits | Use |
|---|---:|---|
| `d4fc469..HEAD` | 81 | `git diff --stat` in §11.1, per the pass specification |
| `515cc7c..HEAD` | 103 | the span over which the superseded statements in §11.2 were true |

Every citation in the "replacement" and "evidence" columns of §11.2 is to the working tree at
`86d173b`, so the doc-vintage difference affects only which commits carry a change, never
whether a replacement statement holds.

**Tree state.** `git status --porcelain` is non-empty. Every entry is inside `docs/thesis/` and
is the output of the Pass 00–10 regeneration this pass belongs to: 14 modified `.md` files, 8
deleted `.mmd` figures, 20 untracked `.mmd` figures. No tracked file outside `docs/thesis/` is
modified.

```
$ git status --porcelain | grep -v '^ M docs/thesis/\|^?? docs/thesis/\|^ D docs/thesis/' ; echo "exit=$?"
exit=1
```

---

## 11.1 · `git diff --stat d4fc469..HEAD`

```
$ git diff --shortstat d4fc469..HEAD
 1047 files changed, 280119 insertions(+), 4246 deletions(-)
```

The full per-file stat is 1,047 rows. It is aggregated here by top-level path; the invocation
that produces the table is given so the aggregation can be reproduced.

```
$ git diff --numstat d4fc469..HEAD | awk '{n=split($3,p,"/"); a=(n==1?"(repo root)":(p[1]=="docs"||p[1]=="data"||p[1]=="tests")&&n>2?p[1]"/"p[2]"/":p[1]"/"); f[a]++; if($1=="-"){b[a]++}else{i[a]+=$1;d[a]+=$2}} END{printf "%-22s %6s %10s %9s %6s\n","path","files","insert","delete","binary"; for(a in f) printf "%-22s %6d %10d %9d %6d\n",a,f[a],i[a],d[a],b[a]}' | (read -r h; echo "$h"; sort -k2 -rn)
path                    files     insert    delete binary
tests/fixtures/           817      41809        43      0
tests/                     70      21443       408      0
enrichment/                31      19160      2658      0
eval/                      18     172517         0      8
dedup/                     16       3157       151      0
data/eval/                 16          0         0     16
docs/thesis/               14       4694       689      4
scripts/                    8        961        19      0
docs/                       6       5728         0      1
tools/                      5       1263         0      0
tests/mocks/                5        377         4      0
utils/                      4       2261        59      0
sql/                        4        305         3      0
search/                     4        364        50      0
adf/                        4        521         0      0
api/                        3        700        43      0
llm/                        2        429        36      0
(repo root)                20       4430        83      6
```

**Modules and integration artefacts added over the pass-document vintage.** No source module is
deleted in the same range.

```
$ git diff --name-status 515cc7c..HEAD -- 'api/*' 'enrichment/*' 'dedup/*' 'utils/*' 'llm/*' 'search/*' 'sql/*' 'adf/*' 'config.py' | grep -E '^[AD]'
A	adf/deduplication_pipeline.json
A	adf/enrichment_pipeline.json
A	adf/issues_pipeline.json
A	adf/scoring_pipeline.json
A	dedup/address.py
A	dedup/cache.py
A	dedup/consolidate.py
A	dedup/consolidate_xlsx.py
A	dedup/flags.py
A	dedup/name_slots.py
A	enrichment/batch_consensus.py
A	enrichment/consistency.py
A	enrichment/dept_block.py
A	enrichment/elf_codes.py
A	enrichment/flags.py
A	enrichment/grounded_resolver.py
A	enrichment/liveness.py
A	enrichment/locality.py
A	enrichment/name_gate.py
A	enrichment/name_repack.py
A	enrichment/page_corroborator.py
A	enrichment/provenance.py
A	enrichment/registry_match.py
A	enrichment/unchanged_state.py
A	enrichment/wikidata.py
A	sql/usp_merge_legacy_enriched.sql
A	sql/usp_merge_legacy_issues.sql
A	sql/usp_merge_validation_clusters.sql
A	sql/usp_merge_validation_scores.sql
A	utils/domain_resolver.py
A	utils/name_identity.py
A	utils/name_slots.py
```

---

## 11.2 · Superseded statements, by pass document

Quotations are from the pass documents as they stand at `d4fc469`; line references in the
"superseded statement" column are to those files. Line references in the other two columns are
to the working tree at `86d173b`.

### 11.2.1 · `00_INVENTORY.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 00-a | `:52` — "`api/routes.py` \| 1118 \| All HTTP route handlers and their XLSX parse/build helpers (**13 routes**; see §2)." | `api/routes.py` is 1,659 lines and registers **16** routes. | `wc -l api/routes.py` → `1659`; `grep -c '^@router\.' api/routes.py` → `16`. The three additional paths are `POST /issues/json` (`api/routes.py:883`), `POST /api/preprocess/consolidate` (`:960–961`) and `POST /api/preprocess/consolidate/file` (`:1026`). |
| 00-b | `:336` — "Suite run (this commit): **`3 failed, 1019 passed, 12 warnings in 28.44s`**" | `12 failed, 3857 passed, 12 skipped, 1 xfailed, 1 warning in 18.05s`. | §11.3.4 (verbatim `pytest -q` tail). The observed failing set is also recorded in `08_GAPS.md` G-01. |
| 00-c | `§3.1` call graph — the per-record chain runs `run_overflow_check` → `preprocess_record` → ROR → LEI → `run_lab_resolver` → `run_tier2_canonical` → `run_tier2a` → `run_tier3` → `_finalise_and_return`, with `_enrich_single` at `orchestrator.py:1698`. | `_enrich_single` is at `enrichment/orchestrator.py:7719` and runs a twelve-stage ladder. Three stages have no counterpart in the superseded graph: the Wikidata crosswalk (`:8342`, `:8411`), the accounts-payable short-circuit (`:8615`) and the grounded resolver (`:9125`, call at `:9141`). `run_tier3` is reached in degraded mode only (`:9174`). | `docs/thesis/12_RATIONALE.md:105–118` (stage table); `enrichment/orchestrator.py:168–177` (Wikidata import); `enrichment/grounded_resolver.py:9–36`. |
| 00-d | The file table lists no module under `enrichment/` other than the twenty named at `:61–79`. | Twenty-four Python modules are added — fifteen under `enrichment/`, six under `dedup/`, three under `utils/` — together with four SQL merge procedures and four ADF pipeline exports; none is deleted. | The `git diff --name-status 515cc7c..HEAD` output in §11.1. |

### 11.2.2 · `00_OPEN_ITEMS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| OI-a | `:31` — "X-1 · `09_DECISIONS.md` cites `enrichment/confidence.py` as a live enforcement layer; `08_GAPS.md` proves it dead" | `enrichment/confidence.py` is 329 lines and defines Provenance Scheme B — `source:confidence[+witness]` — and is imported by five production modules. | `wc -l enrichment/confidence.py` → `329`; docstring `enrichment/confidence.py:1–2`; vocabulary at `:53–103`; importers `enrichment/orchestrator.py:82`, `enrichment/flags.py:71`, `enrichment/issue_detection.py:153`, `enrichment/search_terms.py:50`, `enrichment/page_corroborator.py:68`. |

### 11.2.3 · `01_TRACEABILITY.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 01-a | `:100–101` — "Emitted deterministically by `enrichment/issue_detection.py`; catalogue at `issue_detection.py:77-117`." | The catalogue is `ISSUE_CATALOGUE` at `enrichment/issue_detection.py:260–423`, and `EMITTED_CODES` derives from it at `:426–428`. | `grep -n '^ISSUE_CATALOGUE\|^EMITTED_CODES' enrichment/issue_detection.py` → `260`, `426`. |
| 01-b | `:117` — "\| G1-NAME-004 \| **Name 2 Empty With Name 3 Populated** \| `issue_detection.py:309` \| implemented \|" | The code's name is "Empty field in between populated name fields"; its emission site is `enrichment/issue_detection.py:1157`. | `enrichment/issue_detection.py:279–281`, `:1157`. |
| 01-c | Table 1b — `G1-ADDR-009` "not implemented (deterministic)" and `G1-NAME-001` "implemented". | Both carry `status="withdrawn"` and neither is in `EMITTED_CODES`. | `enrichment/issue_detection.py:284–287` (`reason="ndd, never emitted; residual classifier not called by /issues"`), `:271–274` (`reason="withdrawn 2026-09-07; the deterministic heuristic was a proxy for a rule that is LLM-only"`). |
| 01-d | Table 1c — API endpoint contracts enumerate the thirteen routes of `00_INVENTORY.md:155–166`. | Sixteen routes exist; `/issues/json`, `/api/preprocess/consolidate` and `/api/preprocess/consolidate/file` have no row in Table 1c. | Item 00-a above. |

### 11.2.4 · `02_ARCHITECTURE.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 02-a | `:119–120` — "**e4** — ADF `Lookup1` reads `SELECT * FROM test_77.Legacy ORDER BY Customer OFFSET @offset ROWS FETCH NEXT 50 ROWS ONLY` via `SqlMISource`" | The page read is `SELECT * FROM dp_legacy.[@{pipeline().parameters.chrEntity}].Legacy WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\' ORDER BY [code] OFFSET @{item().offset} ROWS FETCH NEXT 30 ROWS ONLY`. Entity and group code are pipeline parameters; the page is 30 rows; the order is `[code]`. | `adf/enrichment_pipeline.json:68`; parameters at `:160`; offset generator at `:21` (`WHERE (n.rn - 1) % 30 = 0`). |
| 02-b | `:121–122` — "**e5** — ADF `Web1` POSTs JSON `{"records": <50 legacy rows>}`" | The body carries one 30-row page. | `adf/enrichment_pipeline.json:100` (`"method": "POST"`), `:105` (`"url": "https://mdm-pipeline-api.azurewebsites.net/enrich"`), `:110` (body), `:50` (`"isSequential": true`). |
| 02-c | `:133–137` — "**e9–e12** — Address validation (step 6) and the `/issues` call (step 7) have **no exported ADF pipeline** … ⚠ The only `/issues` endpoint in the service consumes a multipart XLSX upload (`detect_file_issues`, `api/routes.py:580-581`), **not JSON**, so how an ADF Web activity invokes it is unverified" | An `/issues` pipeline is in the repository and it targets a JSON endpoint that exists. Address validation still has no pipeline. | `adf/issues_pipeline.json:58` (`"url": "https://mdm-pipeline-api.azurewebsites.net/issues/json"`), `:21` (Lookup), `:89` (`"storedProcedureName": "Mapping.usp_MergeLegacyIssues"`); handler `detect_json_issues` at `api/routes.py:883–884`. |
| 02-d | `:60`, `:75–76` — the ADF artefacts for the dedup, scoring and issues steps are "not exported". | Four pipeline JSONs are in the repository. `issues_pipeline.json` carries no `lastPublishTime`; the other three do. | `ls adf/` → four files; `grep -n lastPublishTime adf/*.json` in §11.3.3. |

### 11.2.5 · `03_ALGORITHMS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 03-a | `:36–38` — "The sweep identified **102 decision procedures** across the eleven subsystems below, plus **16 distinct LLM call sites**." | Fourteen LLM call sites, thirteen of them in Phase 1. | `docs/thesis/03_ALGORITHMS.md:2961–2963`. |
| 03-b | `:51` — "Every deterministic issue-detection rule in the catalogue \| Part H §1.1 (**all 37 codes**)" | The catalogue declares 43 entries, of which 33 are emittable. | §11.3.1. |
| 03-c | Part H documents `detect_issues(record, present_fields)` and five rule groups. | The signature takes three further keyword arguments and a sixth detector runs. | `enrichment/issue_detection.py:1654–1661` (`flag_for_review`, `flag_codes`, `origins`), `:1623` (`_detect_enrichment_flags`), dispatch at `:1700–1706`. |

### 11.2.6 · `03b_EXEMPLARS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 03b-a | `:79–80` — "Across all repository data … the detector raises **32 of the 37 declared codes**. The five it never raises:" (`G1-ADDR-009`, `G4-ADDR-025`, `G2-CONTACT-008`, `G1-NAME-001`, `G3-ADDR-013`) | 43 declared, 33 emittable. Four of the five named codes are withdrawn and cannot be raised at all; the fifth, `G3-ADDR-013`, is observable in `data/eval/`. | §11.3.1 census; `enrichment/issue_detection.py:284`, `:363`, `:306`, `:271` (withdrawn); `G3-ADDR-013` emission site `:1351`. |
| 03b-b | §5–§8 — the exemplar corpus is `REC-01` … `REC-13`, drawn from `PresentationTestData.xlsx`, its subset, and JSON fixtures (`:16–34`). | The exemplar corpus is the five strata pairs in `data/eval/`, one worked record per stratum across five stages. | `docs/thesis/03b_EXEMPLARS.md:110–124` (strata), `:125` onward (S1–S5). |
| 03b-c | `:96–98` — "One code is exercised **only** by the enriched workbook … `G3-ADDR-012` (Duplicate Street Across Fields)." | `G3-ADDR-012` is emittable and appears in no `Issues` column of any `data/eval/` workbook. | §11.3.1, "emitted, never observed" list. |

### 11.2.7 · `04_PARAMETERS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 04-a | `:182` — "\| Issue catalogue size \| **36 codes, G1–G5** \| dict \| `enrichment/issue_detection.py:75-118` … Two codes (`G1-ADDR-009`, `G4-ADDR-025`) are marked "LLM-only — never emitted"" | 43 declared entries over seven groups, 33 emittable, 0 declared-and-unemittable. Both named codes carry `status="withdrawn"`. | §11.3.1; `enrichment/issue_detection.py:432` (`QUALITY_GROUPS`), `:446` (`VERIFICATION_GROUPS`), `:284`, `:363`. |
| 04-b | `:455–458`, `:462–465` — the `sales_order_last_used` and `sales_order_partner_last_used` ladders are keyed `2026`→20, `2025`→15, `2024`→10, `2023`→5; `:538–539` — "the DS bands are relative to the current date, the repository's are **absolute calendar years**". | Both ladders are keyed `0`→20, `1`→15, `2`→10, `3`→5 and are banded on the offset from the election's reference year. | `dedup/weights.json:3–7`, `:14–18` and its `_comment` at `:2`; `_year_offset` at `dedup/scoring.py:763–770`; lookups at `:957–958`, `:978–979`; reference year `current_year = datetime.date.today().year` at `:1181`, carried on each result as `scored_with_reference_year` (`:331`, `:1309`, `:1323`). |

### 11.2.8 · `05_DATA_MODEL.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 05-a | `:653–655` — "The catalogue holds **36 codes** (`issue_detection.py:75-118`), of which `G1-ADDR-009` and `G4-ADDR-025` are declared but never emitted" | 43 declared, 33 emittable; the two named codes are withdrawn. | §11.3.1; `enrichment/issue_detection.py:260–428`. |
| 05-b | `:800` — "`ISSUE_CODE { string code PK "36-code catalogue, 34 emitted" }`" | "43-code catalogue, 33 emitted". | §11.3.1. |
| 05-c | `:650` — the `Issues` cell is produced from "the five rule groups `_detect_wrong_field`, `_detect_missing`, `_detect_duplicate`, `_detect_format`, `_detect_naming`" | Six detectors run; the sixth, `_detect_enrichment_flags`, maps `Flag Codes` tokens onto catalogue codes. | `enrichment/issue_detection.py:1700–1705`; `FLAG_CODE_ISSUES` at `:1515–1559`. |

### 11.2.9 · `06_EXTERNAL_DEPS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 06-a | `:35–43` — the service inventory has seven numbered rows: ROR, GLEIF/LEI, SerpAPI, DuckDuckGo, Azure OpenAI Phase 1, Azure OpenAI Phase 2, third-party web hosts. | An eighth service is called: the Wikidata Action API, over `httpx`, authenticated by User-Agent only. | `enrichment/wikidata.py:725` (client), `:697` (`BrukerMDM-EnrichmentAPI/1.0 (Wikidata crosswalk lane)`); wiring `enrichment/orchestrator.py:168–177`; documented at `docs/thesis/06_EXTERNAL_DEPS.md:41`, `:119–126`. |

### 11.2.10 · `06b_CROSSCUTTING.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 06b-a | `:680–697` — the Phase-1 call-volume table enumerates fifteen stages, the last evidence-bearing one being `Tier 3` at `orchestrator.py:2543`. | Four lanes that issue network calls have no row in that table: the Wikidata crosswalk, the grounded resolver, the liveness lane and the page corroborator. | `enrichment/wikidata.py:725`; `enrichment/grounded_resolver.py:9–36`; `enrichment/liveness.py:272` (`httpx.AsyncClient`); `enrichment/page_corroborator.py:63` (`from search.page_fetcher import PageFetcher`). |

### 11.2.11 · `07_EVALUATION.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| 07-a | `:293–294` — "The run recorded at this commit is `3 failed, 1019 passed, 12 warnings in 28.44s`, with the three failures named in `00_INVENTORY.md:336-343`." | `12 failed, 3857 passed, 12 skipped, 1 xfailed, 1 warning in 18.05s`. | §11.3.4. |
| 07-b | `§4` — the datasets are `PresentationTestData.xlsx`, its enriched twin, the subset, and the JSON fixtures. | `data/eval/` holds sixteen workbooks: five `S{n}_pre`/`S{n}_post` stratum pairs, two stress inputs, a scored stress export, a verified stress workbook, and a 100-row pair. | `ls data/eval/`; `docs/thesis/03b_EXEMPLARS.md:17–30`. |
| 07-c | `:422`, `:442` — "No `Block ID` column exists in the repository datasets", and no ground-truth cluster labelling is "present in any workbook in the repository". | No `Block ID` column exists. A ground-truth column does: `gt_expected_action` in the stress inputs, alongside `Clusters`, `Traps` and `Method` sheets. | Header row of `data/eval/stress_200_pre.xlsx` sheet `Data` and of `data/eval/dedup_STRESS_200_v1-verified.xlsx`; sheet names `['Data', 'Clusters', 'Traps', 'Method']`. |

### 11.2.12 · `08_GAPS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| G-9 | `:170–178` — "the module docstring states 'Coverage: 34 of the 36 catalogue codes are emitted' … The catalogue declares **37** codes … Both docstring figures are stale against the current source." | The docstring states 43 declared / 33 live / 33 emitted / 10 withdrawn / 0 not deterministically detectable, and a test asserts the figures against the catalogue. | `enrichment/issue_detection.py:46–61`, `:85–89` ("These figures are asserted against the source by `tests/test_issue_detection.py::test_docstring_counts_match_the_catalogue`"); §11.3.1. |
| G-28 | `:454–461` — "The `/issues` ADF pipeline is not exported and `/issues` has no JSON variant … the step as designed has no callable JSON contract." | Both halves are closed: `adf/issues_pipeline.json` exists and `POST /issues/json` exists. | `adf/issues_pipeline.json:58`; `api/routes.py:883–884`. |
| G-29 | `:463–470` — "No stored procedure writing the Issues column back to Legacy is evidenced." | `Mapping.usp_MergeLegacyIssues` is in the repository, takes `@target_column SYSNAME = N'Issues'`, and is named by the pipeline. | `sql/usp_merge_legacy_issues.sql:1–5`, guard at `:14–17`; call site `adf/issues_pipeline.json:89`. |
| G-30 | `:471–478` — "Neither exported ADF pipeline invokes `/api/dedup/score` or `/api/dedup/approve`." | Half closed. `adf/scoring_pipeline.json` invokes `/api/dedup/score`. No pipeline invokes `/api/dedup/approve`. | `adf/scoring_pipeline.json:58`; `grep -rn approve adf/` returns nothing. |
| G-31 | `:480–490` — "No group-code predicate exists on any of the three ADF Lookup activities … the enrichment Lookups read `test_77.Legacy` unfiltered and the deduplication Lookup reads `test_77.Validation` unfiltered." | All four pipelines carry the predicate `WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\'`, and the entity is a parameter rather than the literal `test_77`. | §11.3.2. |
| G-45 | `:612–622` — "Two catalogue codes are declared and never emitted … `G1-ADDR-009` … and `G4-ADDR-025` … are both annotated `# LLM-only — never emitted`." | Both carry `status="withdrawn"`. Emittable and live coincide exactly: 33 and 33, with `EMITTED_CODES` derived from status rather than annotated by hand. | `enrichment/issue_detection.py:284–287`, `:363–366`, `:426–428`; §11.3.1. |
| G-46 | `:623` — "`G2-CONTACT-008` has an emission site that no input can reach." | `G2-CONTACT-008` carries `status="withdrawn"`; so does `G2-CONTACT-009`. | `enrichment/issue_detection.py:306–313`, `:314–321`. |
| G-80 | `:1234–1247` — "the detector raises 32 of the 37 declared codes … The remaining two are genuine data gaps": `G1-NAME-001` and `G3-ADDR-013`. | `G1-NAME-001` is withdrawn. `G3-ADDR-013` is emittable and observable in `data/eval/`. Seven emittable codes have no observation in any `data/eval/` `Issues` column. | `enrichment/issue_detection.py:271–274`; §11.3.1. |

### 11.2.13 · `09_DECISIONS.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| D-21 | `:786–797` — "Two catalogue codes declared but never emitted … 'Coverage: 34 of the 36 catalogue codes are emitted. Two are intentionally never emitted because they genuinely require the pipeline's LLM residual classifier'" | The two codes are withdrawn rather than declared-and-unemitted, each carrying a `reason` on its catalogue entry. | `enrichment/issue_detection.py:284–287`, `:363–366`; docstring `:54–61`. |
| D-29 | `:1150–1155` — "`dedup/weights.json` instead scores **absolute years**: `"2026": 20, "2025": 15, "2024": 10, "2023": 5`. … ⚠ The change from months-since-now to absolute years is not recorded anywhere in this repository" | The ladders are relative: they band the offset from the election's reference year, which is resolved once per election. | Item 04-b above; `dedup/weights.json:2` (`_comment`: "The two *_last_used ladders are banded on the OFFSET from the election's reference year (0 = this year, 1 = last year, …), not on absolute years"); commit `f5c8d8d` "Dates are not years: four sales-order rules scored 0 on every record". |

### 11.2.14 · `figures/INDEX.md`

| # | Superseded statement | Replacement | Evidence |
|---|---|---|---|
| F-a | The index lists eight figures, `fig-01-system-components.mmd` … `fig-08-scoring-call-graph.mmd`. | Twenty figures, `fig-01-data-plane.mmd` … `fig-20-coupling-exemplar.mmd`. | `ls docs/thesis/figures/*.mmd \| wc -l` → `20`; `docs/thesis/figures/INDEX.md`. |

---

## 11.3 · Facts block

### 11.3.1 · Issue-catalogue census

**Catalogue, from the source.** The repository carries the derivation as a script.

```
$ python3 scripts/issue_catalogue_census.py
====================================================================
Issue Catalogue census — derived from enrichment/issue_detection.py
====================================================================
  declared                     43
  live                         33
  unlisted (not in v2)         0
  deterministically emitted    33   (live + unlisted)
  withdrawn                    10
  not det. detectable          0
  fixture-covered              33 of 33
  live quality codes (G1-G6)   31
  origin of those              {'API': 16, 'DS': 8, 'BOTH': 7}
  entries per group            {'G1': 12, 'G2': 9, 'G3': 9, 'G4': 5, 'G5': 2, 'G6': 4, 'G7': 2}
```

The ten withdrawn entries, each with the `reason` on its catalogue entry
(`enrichment/issue_detection.py`):

| Code | Line | `reason` |
|---|---|---|
| `G1-NAME-001` | `:271–274` | withdrawn 2026-09-07; the deterministic heuristic was a proxy for a rule that is LLM-only |
| `G1-ADDR-009` | `:284–287` | ndd, never emitted; residual classifier not called by /issues |
| `G2-CONTACT-008` | `:306–313` | struck through in Catalogue v2 |
| `G2-CONTACT-009` | `:314–321` | struck through in Catalogue v2 |
| `G4-ADDR-008` | `:354–361` | withdrawn 2026-09-06 |
| `G4-ADDR-025` | `:363–366` | >4 sub-locations, 0/500 observed; `overflow` covers spill |
| `G2-VAL-003` | `:381–384` | SAP-derived field, 65% blank, not master-data scope |
| `G2-VAL-006` | `:386–389` | 99% populated, no defect class |
| `G6-RESOLVE-001` | `:391–394` | group G6 (not resolvable) dissolved; members re-homed in Step D |
| `G7-VERIFY-001` | `:411–414` | routing now carried by group membership (already decided 2026-09-02) |

**Per group, four populations.** `observed` counts distinct codes appearing in any column headed
`Issues` in `data/eval/*.xlsx`; `oracle` counts the same for `expected_issue_codes`. Three of the
ten workbooks carry the header `Issues` twice and the two occurrences disagree, so every
occurrence is read (`docs/thesis/03b_EXEMPLARS.md:63–90`). `docs-only` counts codes named in
`docs/**.md` or `README.md` that `ISSUE_CATALOGUE` does not declare.

```
$ cat > /tmp/census_by_group.py <<'PY'
import collections, glob, re, subprocess, sys
sys.path.insert(0, ".")
from openpyxl import load_workbook
from enrichment.issue_detection import EMITTED_CODES, ISSUE_CATALOGUE

CODE = re.compile(r"G[1-8]-[A-Z]+-\d+")
GROUPS = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")

def from_workbooks(header):
    seen = set()
    for path in sorted(glob.glob("data/eval/*.xlsx")):
        book = load_workbook(path, read_only=True, data_only=True)
        for name in book.sheetnames:
            rows = book[name].iter_rows(values_only=True)
            try:
                head = next(rows)
            except StopIteration:
                continue
            cols = [i for i, h in enumerate(head) if h and str(h).strip() == header]
            for row in rows:
                for i in cols:
                    if i < len(row) and row[i]:
                        seen.update(CODE.findall(str(row[i])))
        book.close()
    return seen

docs_files = [f for f in subprocess.run(
    ["git", "ls-files", "docs", "README.md"], capture_output=True, text=True
).stdout.split() if f.endswith((".md", ".txt"))]
docs_files = sorted(set(docs_files) | set(glob.glob("docs/thesis/*.md")) | set(glob.glob("docs/*.md")))
in_docs = set()
for f in docs_files:
    in_docs.update(CODE.findall(open(f, encoding="utf-8", errors="ignore").read()))

declared, emitted = set(ISSUE_CATALOGUE), set(EMITTED_CODES)
observed, oracle = from_workbooks("Issues"), from_workbooks("expected_issue_codes")
docs_only = in_docs - declared
group = lambda c: ISSUE_CATALOGUE[c].group if c in ISSUE_CATALOGUE else c.split("-")[0]
count = lambda s, g: sum(1 for c in s if group(c) == g)

print(f"{'group':<6}{'declared':>9}{'emitted':>9}{'observed':>10}{'oracle':>8}{'docs-only':>11}")
for g in GROUPS:
    print(f"{g:<6}{count(declared,g):>9}{count(emitted,g):>9}{count(observed & emitted,g):>10}"
          f"{count(oracle & emitted,g):>8}{count(docs_only,g):>11}")
print(f"{'total':<6}{len(declared):>9}{len(emitted):>9}{len(observed & emitted):>10}"
      f"{len(oracle & emitted):>8}{len(docs_only):>11}")
print()
print("emitted, never observed in any data/eval `Issues` column:")
print("   ", sorted(emitted - observed))
print("observed but NOT emittable at this commit (withdrawn):")
print("   ", sorted(observed - emitted))
print("in `expected_issue_codes` but NOT emittable at this commit (withdrawn):")
print("   ", sorted(oracle - emitted))
print("referenced in docs, absent from ISSUE_CATALOGUE:")
print("   ", sorted(docs_only))
PY
$ python3 /tmp/census_by_group.py
group  declared  emitted  observed  oracle  docs-only
G1           12       10        10      10          0
G2            9        7         5       3          0
G3            9        9         7       6          0
G4            5        3         0       1          0
G5            2        2         2       2          0
G6            4        1         1       0          0
G7            2        1         1       0          1
G8            0        0         0       0          1
total        43       33        26      22          2

emitted, never observed in any data/eval `Issues` column:
    ['G2-VAL-004', 'G2-VAL-008', 'G3-ADDR-012', 'G3-CONTACT-007', 'G4-ADDR-026', 'G4-ADDR-027', 'G4-NAME-015']
observed but NOT emittable at this commit (withdrawn):
    ['G1-NAME-001', 'G4-ADDR-008']
in `expected_issue_codes` but NOT emittable at this commit (withdrawn):
    ['G1-ADDR-009', 'G1-NAME-001', 'G4-ADDR-008', 'G4-ADDR-025']
referenced in docs, absent from ISSUE_CATALOGUE:
    ['G7-CONFIRM-001', 'G8-VERIFY-001']
```

Three readings of that output bear on the thesis:

1. **Every `data/eval/` workbook predates the current catalogue.** `G1-NAME-001` and
   `G4-ADDR-008` appear in `Issues` columns and neither is emittable at `86d173b`
   (`enrichment/issue_detection.py:271–274`, `:354–361`). The oracle column carries two further
   withdrawn codes.
2. **Seven emittable codes have no observation** in any `data/eval/` `Issues` column. Four of
   the seven are DS-origin required-field or format rules (`G2-VAL-004`, `G2-VAL-008`,
   `G4-ADDR-026`, `G4-ADDR-027`).
3. **Two codes named in the documentation do not exist in the code.** `G7-CONFIRM-001` and
   `G8-VERIFY-001` are the pre-2026-09-06 identifiers of today's `G6-CONFIRM-001`
   (`enrichment/issue_detection.py:403`) and `G7-UNCHANGED-001` (`:420`). Both appear in
   `README.md:2306–2307` and throughout `docs/15_ISSUES_DOSSIER.md` (`:332–333`, `:1008`,
   `:1471`). `G8` is not a group at this commit: `QUALITY_GROUPS` is `("G1","G2","G3","G4","G5")`
   (`:432`) and `VERIFICATION_GROUPS` is `("G6","G7")` (`:446`). The renumbering is recorded at
   `enrichment/issue_detection.py:36–40`: "They were G7 and G8 before 2026-09-06, when the old G6
   was dissolved and the two shifted down; the withdrawn `G7-VERIFY-001` keeps its old identifier
   and is not the same code as today's G7."

### 11.3.2 · Group-code predicate in each ADF Lookup

Present in all four pipelines.

```
$ grep -n "chrGroupCode" adf/*.json | cut -c1-120
adf/scoring_pipeline.json:21:                            "value": "SELECT\n    Customer
adf/scoring_pipeline.json:110:            "chrGroupCode": {
adf/issues_pipeline.json:21:                            "value": "SELECT * FROM dp_legacy.[@{p
adf/issues_pipeline.json:110:            "chrGroupCode": {
adf/deduplication_pipeline.json:21:                            "value": "SELECT\n    Customer
adf/deduplication_pipeline.json:110:            "chrGroupCode": {
adf/enrichment_pipeline.json:21:                            "value": "SELECT (n.rn - 1) AS off
adf/enrichment_pipeline.json:68:                            "value": "SELECT * FROM dp_legacy.
adf/enrichment_pipeline.json:160:            "chrGroupCode": {
```

| Pipeline | Lookup activity | Predicate present | SQL as it stands in the JSON |
|---|---|---|---|
| `adf/enrichment_pipeline.json` | `Lookup2` (`:6–7`), offsets | yes (`:21`) | `SELECT (n.rn - 1) AS offset FROM ( SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS rn FROM dp_legacy.[@{pipeline().parameters.chrEntity}].Legacy WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\' ) n WHERE (n.rn - 1) % 30 = 0` |
| `adf/enrichment_pipeline.json` | `Lookup1` (`:53–54`), page read inside `ForEach1` | yes (`:68`) | `SELECT * FROM dp_legacy.[@{pipeline().parameters.chrEntity}].Legacy WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\' ORDER BY [code] OFFSET @{item().offset} ROWS FETCH NEXT 30 ROWS ONLY` |
| `adf/issues_pipeline.json` | `Lookup1` (`:6–7`) | yes (`:21`) | `SELECT * FROM dp_legacy.[@{pipeline().parameters.chrEntity}].Legacy WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\' ORDER BY [code]` |
| `adf/deduplication_pipeline.json` | `Lookup1` (`:6–7`) | yes (`:21`) | eleven-column projection, then `FROM [@{pipeline().parameters.chrEntity}].Validation WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\'` |
| `adf/scoring_pipeline.json` | `Lookup1` (`:6–7`) | yes (`:21`) | twenty-four-column projection, then `FROM [@{pipeline().parameters.chrEntity}].Validation WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\'` |

Both parameters are declared on every pipeline (`adf/enrichment_pipeline.json:160`,
`adf/issues_pipeline.json:110`, `adf/deduplication_pipeline.json:110`,
`adf/scoring_pipeline.json:110`), and all four merge procedures take them
(`sql/usp_merge_legacy_enriched.sql:1–4`, `sql/usp_merge_legacy_issues.sql:1–5`,
`sql/usp_merge_validation_clusters.sql:1–4`, `sql/usp_merge_validation_scores.sql:1–4`).

### 11.3.3 · Baseline `/issues` run: ADF or manual

**Manual.** Three facts, each from a file.

```
$ grep -n "lastPublishTime" adf/*.json
adf/deduplication_pipeline.json:115:        "lastPublishTime": "2026-07-29T12:09:37Z"
adf/scoring_pipeline.json:115:        "lastPublishTime": "2026-07-31T18:27:48Z"
adf/enrichment_pipeline.json:165:        "lastPublishTime": "2026-09-06T16:49:32Z"

$ grep -rn "issues/compare" adf/ ; echo "exit=$?"
exit=1
```

1. `adf/issues_pipeline.json` carries no `lastPublishTime`; the other three do. It is authored,
   not published.
2. `usp_MergeLegacyIssues` admits two target columns and defaults to one of them —
   `@target_column SYSNAME = N'Issues'` (`sql/usp_merge_legacy_issues.sql:5`), guarded to
   `N'Issues Before'` or `N'Issues'` (`:14–17`) — but the pipeline passes only `payload`
   (`adf/issues_pipeline.json:90–99`), so every ADF run writes `Issues` and a second run
   overwrites the first. A baseline and a post-enrichment count cannot coexist in the table on
   that path.
3. The before/after mechanism the repository implements is `POST /issues/compare`
   (`api/routes.py:917`), a two-file multipart endpoint (`:918–919`) that audits both workbooks
   (`:932–933`) and returns a delta report (`:943`). No ADF pipeline calls it.

The author's own note is consistent: the baseline path "may also be run standalone against the
raw file" and "⚠ Whether that path is in ADF or manual is unconfirmed"
(`docs/thesis/CONTEXT-EXTERNAL.md:431–432`). The code decides it: manual. Recorded at
`docs/thesis/02_ARCHITECTURE.md:266–287`.

### 11.3.4 · `pytest -q`

```
$ python3 -m pytest -q ; echo "exit=$?"
[…]
=========================== short test summary info ============================
FAILED tests/test_dedup.py::test_conflicting_ror_not_merged_verdict_guard - a...
FAILED tests/test_dedup.py::test_conflicting_lei_not_merged_verdict_guard - a...
FAILED tests/test_dedup.py::test_no_signal_pair_not_nominated_reason_empty_ok
FAILED tests/test_dedup.py::test_mode_b_canonical_assignment_produces_correct_clusters
FAILED tests/test_dedup.py::test_route_cluster_block_identical_rows - assert ...
FAILED tests/test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing
FAILED tests/test_orchestrator.py::TestOrchestrator::test_tier1_full_resolution
FAILED tests/test_orchestrator.py::TestOrchestrator::test_tier1_to_tier2a_verification
FAILED tests/test_orchestrator.py::TestOrchestrator::test_web_search_fallback_for_name1
FAILED tests/test_orchestrator.py::TestOrchestrator::test_web_search_determines_record_type
FAILED tests/test_orchestrator.py::TestTier2AVerificationMergeLayer::test_low_score_medium_confidence_keeps_record_value
FAILED tests/test_orchestrator.py::TestTier2AVerificationMergeLayer::test_low_score_high_confidence_overwrites_record_value
12 failed, 3857 passed, 12 skipped, 1 xfailed, 1 warning in 18.05s
exit=1
```

`tests/KNOWN_FAILURES.md:7` records a different set — `8 failed, 3311 passed, 7 skipped` — and
`:3–5` asserts a gate over it; no `.py` file references `KNOWN_FAILURES`. Recorded as G-01 in
`docs/thesis/08_GAPS.md:178`.

### 11.3.5 · `weights.json` values and last-change commit

```
$ git log -1 --format='%H %ad %s' --date=iso -- dedup/weights.json
f5c8d8df9db1a6dc3d93f912781849c49383af72 2026-09-05 21:41:19 +0200 Dates are not years: four sales-order rules scored 0 on every record

$ git log --format='%h %ad %s' --date=short -- dedup/weights.json
f5c8d8d 2026-09-05 Dates are not years: four sales-order rules scored 0 on every record
611c348 2026-07-11 Phase 2 thesis
```

| Key | Bands, verbatim | Line |
|---|---|---|
| `sales_order_last_used` | `"0": 20, "1": 15, "2": 10, "3": 5` | `dedup/weights.json:3–8` |
| `sales_order_count` | `"1-5": 5, "6-10": 15, ">10": 25` | `:9–13` |
| `sales_order_partner_last_used` | `"0": 20, "1": 15, "2": 10, "3": 5` | `:14–19` |
| `sales_order_partner_count` | `"1-5": 5, "6-10": 15, ">10": 25` | `:20–24` |
| `equipment_count` | `"1-3": 5, "4-8": 12, "9-15": 20, ">15": 30` | `:25–30` |
| `sleeping_customer` | `"No": 15, "Yes": 0` | `:31–34` |
| `customer_status` | `"active": 10, "blocked": 0` | `:35–38` |
| `account_group` | `"DRIT": 20, "0002/SHIP2": 15, "0003": 10, "0004": 10, "0005/LIEF/MLIEF": 5` | `:39–45` |
| `company_code_count` | `"1": 5, "2-4": 15, "5+": 25` | `:46–50` |
| `combined_presence_bonus` | `"company code AND sales org": 10` | `:51–53` |
| `salesforce_instance_count` | `"per instance": 10` | `:54–56` |

The band grammar and the three unconfirmed values are stated in the file's own `_comment`
(`dedup/weights.json:2`): band labels `'a-b'` inclusive, `'>n'` strictly greater, `'n+'`
greater-or-equal, bare number exact, `'X/Y'` either literal case-insensitively; values matching
no band score 0; the two `*_last_used` ladders band the offset from the election's reference
year; the three count ladders start at 1 because the source report encodes "none" as NULL;
`combined_presence_bonus`, the `sales_order_partner_count` tiers and `account_group` `DRIT`
are marked `UNCONFIRMED (verify with Bernd)`.

### 11.3.6 · `split_consolidated` delimiter handling

**Both.** One regular expression accepts `,` and `;` alike.

```
$ sed -n '780,792p' dedup/scoring.py
def split_consolidated(value: Optional[str]) -> List[str]:
    """Non-empty parts of a ","- or ";"-delimited cell ("1003;1017;" -> 2).

    BOTH delimiters are accepted. The preprocess stage that produces these
    cells joins on ``dedup.consolidate.CONSOLIDATED_DELIMITER`` (","), while
    every historic extract uses ";" — a splitter that knew only one of them
    would count a whole comma-joined list as a single value and silently
    flatten score_CompanyCodeCount and score_CombinedPresence across the run.
    Widening is purely additive: a semicolon-delimited cell keeps its count.
    """
    if value is None:
        return []
    return [part.strip() for part in re.split(r"[,;]", str(value)) if part.strip()]
```

The writer joins on `,` — `CONSOLIDATED_DELIMITER = ","` (`dedup/consolidate.py:45`), used at
`:118`. Both readings are asserted:
`tests/test_preprocess_consolidate.py:277–279` (`"1140;1207"` and `"1140,1207"` both yield 2),
`:281–285` (whitespace and trailing empties dropped, `None` and `""` yield `[]`), and
`:287–289` (the writer's output round-trips through the reader). Consumers:
`derived_counts` (`dedup/scoring.py:801–802`) derives `company_code_count` and
`sales_org_count` from it, never from the file.

### 11.3.7 · `/api/preprocess/consolidate`

**Present**, with a file twin.

```
$ grep -n "preprocess/consolidate" api/routes.py
961:    "/api/preprocess/consolidate",
1004:    POST /api/preprocess/consolidate/file, which always sees the whole file.
1026:@router.post("/api/preprocess/consolidate/file")
1034:    """Same consolidation as /api/preprocess/consolidate, XLSX in / XLSX out.
```

| Route | Handler | Transport | Batching invariant |
|---|---|---|---|
| `POST /api/preprocess/consolidate` | `preprocess_consolidate` (`api/routes.py:974`) | JSON in, JSON out (`ConsolidateRequest` → `ConsolidateResponse`) | cannot be verified inside one request; emits a heuristic warning naming the customers at the first and last row positions (`:996–1005`) |
| `POST /api/preprocess/consolidate/file` | `preprocess_consolidate_file` (`api/routes.py:1027`) | XLSX in, XLSX out | always safe — the whole workbook is processed (`:1040–1042`) |

Both append the two customer-level columns `Company_Code_Consolidated` and
`Sales_Org_Consolidated` onto every row of each customer, reading only `Customer`,
`Company Code` and `Sales Organization` (`:989–995`). Rows in equals rows out, same order
(`:993–994`). A blank `Customer` is counted in `summary.errors` and the row is returned
unchanged with both columns empty (`:1006–1008`). No ADF pipeline calls either route.

---

**Summary.** Pass 11 compares `86d173b` against baseline `d4fc469` (81 commits, 1,047 files,
+280,119/−4,246) and against the `515cc7c` vintage of the pass documents it supersedes (103
commits, 32 modules and integration artefacts added, none deleted); it records 40 superseded
statements across the thirteen prior pass documents and the figure index, and answers the seven
required facts: 43 codes declared and 33 emittable over groups G1–G7 with 26 observable in
`data/eval/` and 2 named only in documentation, the group-code predicate present in all four ADF
Lookups, the baseline `/issues` run manual, `12 failed, 3857 passed, 12 skipped, 1 xfailed`,
`weights.json` last changed at `f5c8d8d` with both `*_last_used` ladders banded on a year
offset, `split_consolidated` accepting comma and semicolon alike, and
`/api/preprocess/consolidate` present with a file twin.
