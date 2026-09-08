Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 10

# Pass 10 — Figures

Every Mermaid block in Passes 00–09 is extracted into one `.mmd` file in this directory. Each
file carries a two-line `%%` provenance header naming the figure and the source document with the
line range of the fenced block; everything below the header is byte-identical to the block in the
source document, so a figure and its source diff cleanly.

Twenty blocks exist, across five pass documents: `00_INVENTORY.md` (7), `02_ARCHITECTURE.md` (7),
`03_ALGORITHMS.md` (1), `03b_EXEMPLARS.md` (1), `05_DATA_MODEL.md` (4). Passes 01, 04, 06, 06b,
07, 08 and 09 produced no Mermaid block.

The eight `.mmd` files previously in this directory were generated at commit `515cc7c` on branch
`diag/website-trace` and are superseded in full; they were removed rather than updated, because
neither the numbering nor the source line ranges survive.

## 1 · Figure index

Numbering follows the order in which the figures would appear in a thesis using the chapter
structure Introduction · Problem Description · State of the Art · Requirements Analysis · System
Architecture · Phase 1 · Phase 2 · Cross-Cutting Concerns · Discussion · Conclusion. Within a
chapter the order is: pipeline-level view (ADF and the table progression), then data model, then
call-level view, then algorithm-level view.

| # | File | Chapter | Caption | Source document |
|---|------|---------|---------|-----------------|
| 1 | `fig-01-data-plane.mmd` | System Architecture | Data plane of the pipeline: the SAP customer master feeding the DS Import (bronze) → `dp_legacy` Legacy (silver) → `dp_validation` Validation (gold) → load-file progression, with the four service reads (`/enrich`, `/issues`, cluster-block, score) shown as out-and-back edge pairs against the table they read and write, and the steward's DS issues and dedup views as the only human entry point. Edge labels `eNN` are shared with figure 2; the edge evidence table is `02_ARCHITECTURE.md` §2.1.3. | `02_ARCHITECTURE.md` §2.1a (L34–52) |
| 2 | `fig-02-processing-plane.mmd` | System Architecture | Processing plane over the same `eNN` edges: the four exported ADF pipelines invoking the `mdm-pipeline-api` Function App, the Function App's outbound calls to AI Foundry (gpt-5.4), ROR, GLEIF, Wikidata, SerpAPI/DuckDuckGo and page fetch, its telemetry edge to Application Insights, and the four merge-back procedures each pipeline calls after its Web activity. | `02_ARCHITECTURE.md` §2.1b (L56–73) |
| 3 | `fig-03-er-overview.mmd` | System Architecture | Entity-relationship overview, entities and relationships only (attributes are in `05_DATA_MODEL.md` §3 and §5): the group code scoping the three table grains, the Import–Legacy–Validation one-to-one mappings, the conditional publication of a Validation row to the load file, and the branch into enrichment (result, provenance event, registry identity) and entity resolution (address block, signature, cluster, link, score row, golden record). | `05_DATA_MODEL.md` §8 (L821–840) |
| 4 | `fig-04-enrichment-run-sequence.mmd` | Phase 1 — Enrichment | Enrichment run: `Lookup2` computes offsets in steps of 30, a sequential `ForEach1` pages the Legacy table with `OFFSET n FETCH NEXT 30`, posts each page to `POST /enrich`, and calls `Mapping.usp_merge_legacy_enriched` with the response payload. The merge sits inside the loop, so a failure at page *k* leaves pages 1…*k*−1 written (`02_ARCHITECTURE.md` §2.3.1; `adf/enrichment_pipeline.json:21`, `:46–49`, `:105`, `:136`). | `02_ARCHITECTURE.md` §2.3.1 (L224–239) |
| 5 | `fig-05-issues-run-sequence.mmd` | Phase 1 — Enrichment | Issues run: one unbatched Lookup over every row of the group code, one `POST /issues/json` call, and a merge through `Mapping.usp_MergeLegacyIssues`. The closing note records that `@target_column` is never passed, so the baseline and post-enrichment runs of this same pipeline write the same `Issues` column and cannot coexist in the table (`02_ARCHITECTURE.md` §2.3.2; `adf/issues_pipeline.json:21`, `:90–99`; `sql/usp_merge_legacy_issues.sql:5`, `:14`). | `02_ARCHITECTURE.md` §2.3.2 (L254–264) |
| 6 | `fig-06-er-ingestion.mmd` | Phase 1 — Enrichment | Ingestion detail: the Legacy row keyed on `Customer` with its `code` prefix and the two issue columns, the `EnrichmentResult` returned one-per-record by `POST /enrich`, the provenance event emitted per scoped write with its confidence scale and value, and the 31-column write-back through `usp_MergeLegacyEnriched`. | `05_DATA_MODEL.md` §9.1 (L872–900) |
| 7 | `fig-07-enrich-batch-spine.mmd` | Phase 1 — Enrichment | Call graph of the `POST /enrich` batch spine: handler → orchestrator acquisition → `enrich_batch`, the three per-batch cache and counter resets, the per-record fan-out to `_enrich_single`, then the batch-level `normalise_output_fields`, `apply_batch_consensus` and `_build_summary`. The legend under `00_INVENTORY.md` §0.5.1 carries the `file:line` for each node. | `00_INVENTORY.md` §0.5.1 (L451–464) |
| 8 | `fig-08-enrich-tier-ladder.mmd` | Phase 1 — Enrichment | Call graph of the tier ladder inside `_enrich_single`: overflow check and preprocessing, the person-affiliation branch, Tier 1 ROR and GLEIF with the Wikidata crosswalk, the lab and company-canonical resolvers, Tier 2 and Tier 2A, the grounded resolver, Tier 3 and its application, and the exit into `_finalise_and_return`. Sequence is not implied by the edges; the executed order is figure 11. | `00_INVENTORY.md` §0.5.2 (L488–506) |
| 9 | `fig-09-enrich-finalisation.mmd` | Phase 1 — Enrichment | Call graph of `_finalise_and_return`: the four name and Tier 1 retry paths, the website B/C and domain resolution chain with its two corroboration steps, the department-URL probe and liveness check, the address stage, and `finalise` with flag computation and search-term derivation, closing on the retry trace. Exceeds the node budget — see §3. | `00_INVENTORY.md` §0.5.3 (L541–561) |
| 10 | `fig-10-issues-call-graph.mmd` | Phase 1 — Enrichment | Call graph of the three issue endpoints: `/issues` (XLSX in, XLSX out), `/issues/json`, and `/issues/compare`, converging on the shared `_audit_rows` path and the single detection function `detect_issues`. The header-alias normalisation chain (`_present_fields` → `_input_alias_to_field` → `_norm_header`) is the point at which input column naming is reconciled. | `00_INVENTORY.md` §0.5.4 (L587–607) |
| 11 | `fig-11-phase1-stage-order.mmd` | Phase 1 — Enrichment | Phase 1 stage order as executed, from UC 0 overflow check through preprocessing, the person-only decision and its short-circuit, the Tier 1 → Tier 2 → Tier 2A → grounded → Tier 3 ladder with its match/miss branches, and finalisation into website resolution, the address stage, search terms and flags. This is the ordering figure; figures 8 and 9 are its call-level counterparts. | `03_ALGORITHMS.md` §3.1 (L50–71) |
| 12 | `fig-12-dedup-cluster-run-sequence.mmd` | Phase 2 — Deduplication | Deduplication cluster run: an eleven-column Lookup projection scoped to the group code, one `POST /api/dedup/cluster-block` call, block construction and per-block signatures inside the service, Mode A or Mode B adjudication against AI Foundry, the deterministic split guards and `Link ID` assignment, and the merge through `Mapping.usp_MergeValidationClusters` (`adf/deduplication_pipeline.json:21`; `dedup/adjudicator.py:1518–1523`). | `02_ARCHITECTURE.md` §2.3.3 (L291–305) |
| 13 | `fig-13-scoring-run-sequence.mmd` | Phase 2 — Deduplication | Scoring run: a twenty-four-column Lookup projection scoped to the group code, one `POST /api/dedup/score` call, weight loading and per-row scoring, cluster year-maxima, tie-break and election inside the service, and the merge through `Mapping.usp_MergeValidationScores`. The response also carries `issues`, which the procedure does not read (`02_ARCHITECTURE.md` §2.3.4; `sql/usp_merge_validation_scores.sql:49`). | `02_ARCHITECTURE.md` §2.3.4 (L317–328) |
| 14 | `fig-14-steward-approval-sequence.mmd` | Phase 2 — Deduplication | Steward approval: the steward inspects a cluster and its `Reason` in the DS deduplication view, selects a Leading Code, and the view writes it to the Validation table. The two notes record that `POST /api/dedup/approve` exists and is stateless, and that no ADF pipeline and no procedure calls it — the endpoint is not on the observed approval path (`02_ARCHITECTURE.md` §2.3.5; `api/routes.py:1488`, `:1494–1495`). | `02_ARCHITECTURE.md` §2.3.5 (L342–353) |
| 15 | `fig-15-er-clustering.mmd` | Phase 2 — Deduplication | Clustering detail: the `DedupRow` request grain with its block id and registry ids, its collapse onto a signature keyed by `(norm_name1, norm_name2)`, the result row carrying `cluster_id`, `link_id`, `routing` and `confidence`, and the six-column write-back through `usp_MergeValidationClusters`. | `05_DATA_MODEL.md` §9.2 (L916–941) |
| 16 | `fig-16-er-scoring.mmd` | Phase 2 — Deduplication | Scoring detail: the scoring request row including the two consolidated columns, the four-component score breakdown, the result row carrying `score`, the golden and proposed-golden ids, `election_status` and `approval_status`, the flattening of the breakdown into eleven `score_` columns, and the 21-column write-back through `usp_MergeValidationScores`. | `05_DATA_MODEL.md` §9.3 (L954–982) |
| 17 | `fig-17-cluster-block-call-graph.mmd` | Phase 2 — Deduplication | Call graph of `POST /api/dedup/cluster-block`: candidate configuration and block construction, then per block address parsing, signature building, Mode A and Mode B, residue adjudication, the address and identity split guards, the reasoning-disowns-membership check, institution links and row emission, closing on the cross-block `_merge_link_maps`. | `00_INVENTORY.md` §0.5.5 (L641–659) |
| 18 | `fig-18-score-approve-call-graph.mmd` | Phase 2 — Deduplication | Call graph of `POST /api/dedup/score` and `POST /api/dedup/approve`: weight coercion and loading, election with threshold and weights-version resolution, cluster year-maxima, per-row scoring, tie-break key, cluster merge confidence and result construction, then summary and `DedupIssue` detection; `dedup_approve` → `apply_approval` is the disjoint second component. This path makes no LLM call (`api/routes.py:1444`). | `00_INVENTORY.md` §0.5.6 (L689–704) |
| 19 | `fig-19-consolidate-call-graph.mmd` | Phase 2 — Deduplication | Call graph of `POST /api/preprocess/consolidate`: `consolidate_rows` driving row lookup, per-row `observe` on the customer key, `resolve` through `consolidate_values`, the read and write accessors, the three warning producers (missing column, blank customer, batch boundary), and the summary. This is the row-grain → customer-grain consolidation that supplies the two `_consolidated` columns in figure 16 (`dedup/consolidate.py`). | `00_INVENTORY.md` §0.5.7 (L731–745) |
| 20 | `fig-20-coupling-exemplar.mmd` | Discussion | Coupling exemplar carrying the central argument: records `13130303` and `13351065` land in two different v1 blocks and are never compared; after street-type standardisation and the enrichment of `NSU` to `Nova Southeastern University` with ROR `042bbge36` they share one block, the deterministic name gate still refuses the Name 2 rewrite as `different_entity`, Mode A adjudication clusters them at confidence `0.94`, and election proposes `13351065` as golden. Longest label exceeds the width budget — see §3. | `03b_EXEMPLARS.md` §3b.7.5 (L861–876) |

## 2 · Chapters without figures

Introduction, Problem Description, State of the Art, Requirements Analysis, Cross-Cutting
Concerns and Conclusion have no figure from Passes 00–09. This states what the passes produced,
not what the thesis needs: Requirements Analysis and Cross-Cutting Concerns are documented as
tables in `01_TRACEABILITY.md` and `06b_CROSSCUTTING.md` with no diagrammatic counterpart, and a
figure for either chapter would have to be authored rather than extracted. Discussion holds one
figure (20).

## 3 · Legibility at 75 mm width

A half-page figure in a typical thesis layout is roughly 75 mm wide. Two properties decide
legibility at that width: the node count, which sets how far the figure must scale down, and the
longest node label, which sets the minimum column width before Mermaid wraps.

Criteria applied — node count is flowchart nodes, sequence participants, or ER entities:

- **legible** — ≤ 14 nodes, longest label ≤ 45 characters, no ER attribute blocks.
- **borderline** — 15–18 nodes, or longest label 46–60 characters, or an ER diagram with
  attribute blocks (each attribute row consumes vertical space at full label width).
- **too dense** — more than 18 nodes, or longest label longer than 60 characters.

| # | Verdict | Elements | Longest label |
|---|---------|----------|---------------|
| 1 | legible | 11 nodes, 16 edges | 36 |
| 2 | borderline | 16 nodes, 15 edges | 34 |
| 3 | borderline | 15 entities, 17 relationships | 31 |
| 4 | legible | 4 participants, 7 messages, 1 loop | 41 |
| 5 | borderline | 3 participants, 4 messages, 1 note | 46 |
| 6 | borderline | 3 entities, 3 relationships, 17 attribute rows | 35 |
| 7 | legible | 12 nodes, 11 edges | 26 |
| 8 | borderline | 17 nodes, 16 edges | 27 |
| 9 | ⚠ **too dense** | 19 nodes, 18 edges | 35 |
| 10 | borderline | 16 nodes, 18 edges | 22 |
| 11 | borderline | 17 nodes, 19 edges, 1 decision node | 38 |
| 12 | legible | 4 participants, 8 messages | 44 |
| 13 | borderline | 3 participants, 6 messages | 49 |
| 14 | borderline | 4 participants, 3 messages, 2 notes | 47 |
| 15 | borderline | 4 entities, 3 relationships, 14 attribute rows | 38 |
| 16 | borderline | 4 entities, 3 relationships, 17 attribute rows | 37 |
| 17 | borderline | 17 nodes, 16 edges | 29 |
| 18 | borderline | 15 nodes, 13 edges | 29 |
| 19 | legible | 13 nodes, 12 edges | 23 |
| 20 | ⚠ **too dense** | 11 nodes, 11 edges | 62 |

Two figures fail the budget. The `.mmd` bodies are byte-identical to their sources by rule, so
neither remediation is applied here; each is applied either when the figure is redrawn for the
thesis or by editing the source pass document and re-running this pass.

### Figure 9 — split into two

`_finalise_and_return` has nineteen children, one over the budget. The split follows the
boundary the function's own ordering implies (`00_INVENTORY.md` §0.5.3 legend):

- **9a · Name and web resolution.** `_finalise_and_return` with `_grounded_fallthrough`,
  `_dept_fallthrough`, `_name_post_checks`, `_retry_tier1_after_canonicalisation`,
  `_site_qualifier_retry`, `_retain_wikidata_website`, `_maybe_resolve_website_bc`,
  `_apply_domain`, `_corroborate_domain_from_wikidata`, `_corroborate_domain`,
  `_probe_department_url`, `_check_liveness` — 13 nodes.
- **9b · Address, flags and trace.** `_finalise_and_return` with `_run_address_stage` →
  `process_address`, `finalise` → `compute_flags` and `derive_search_terms`, and
  `_emit_retry_trace` — 7 nodes.

`_finalise_and_return` is retained as the root of both halves so the two figures register
against each other.

### Figure 20 — move the values into the caption

The node count is well inside the budget; three labels carry record values rather than stage
names, and the longest is 62 characters. Shorten the three to `13130303 raw`, `13351065 raw` and
`enrichment: NSU → Nova Southeastern University`, and carry the raw field values and the ROR id
in the caption instead — they are already tabulated at `03b_EXEMPLARS.md` §3b.7.1–§3b.7.4, so
nothing is lost. Longest label then falls to 45 characters and the verdict becomes legible.

## 4 · Verification

Byte-identity of every extracted body against its source block was checked by re-reading each
`.mmd`, discarding the two `%%` header lines and the trailing newline, and comparing the
remainder to the lines between the fences at the recorded line range. All twenty compared equal.
The line ranges in the `%%` headers and in the index table are the fence lines themselves
(opening ```` ```mermaid ```` and closing ```` ``` ````) in the source document at this commit.

Pass 10 complete: 20 Mermaid blocks from 5 pass documents extracted to `docs/thesis/figures/*.mmd` with byte-identical bodies, indexed and assessed; 16 legible or borderline at 75 mm, 2 over budget (figures 9 and 20) with splits proposed, and the superseded 8-figure set from commit `515cc7c` removed.
