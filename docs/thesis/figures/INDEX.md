Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 10 — Figures

Every Mermaid diagram produced in Passes 0–9 is extracted verbatim into one `.mmd` file in this
directory. Each file carries a two-line `%%` provenance header naming the figure and the source
document and line range; the diagram body below that header is byte-identical to the fenced block
in the source document, so a figure and its source can be diffed directly.

Eight diagrams exist across three pass documents: `00_INVENTORY.md` (3), `02_ARCHITECTURE.md` (4),
`05_DATA_MODEL.md` (1). Passes 1, 3, 3b, 4, 6, 6b, 7, 8, and 9 produced no Mermaid diagrams.

## 1 · Figure index

Numbering follows the order in which the figures would appear in a thesis using the chapter
structure Introduction · Problem Description · State of the Art · Requirements Analysis · System
Architecture · Phase 1 · Phase 2 · Cross-Cutting Concerns · Discussion · Conclusion. Within each
chapter, the external (pipeline-level) view precedes the internal (call-level) view of the same
flow.

| # | File | Chapter | Caption | Source document |
|---|------|---------|---------|-----------------|
| 1 | `fig-01-system-components.mmd` | System Architecture | Component diagram of the master-data pipeline, showing the SAP extract feeding the DATAshaper Import → Legacy → Validation → load-file progression in the Tillit tenant, the two Azure Data Factory pipelines that invoke the `mdm-pipeline-api` Function App in the Bruker Azure spoke, and the external services (ROR, GLEIF/LEI, SerpAPI/DuckDuckGo, page fetch, Azure OpenAI) the service calls; each edge is labelled `eNN` with its protocol and payload, and dashed edges mark components whose ADF artefact was not exported. | `02_ARCHITECTURE.md` §2 (L62–108) |
| 2 | `fig-02-er-data-model.mmd` | System Architecture | Entity-relationship model spanning the three DATAshaper tables (Import, Legacy, Validation), the load file, and the service's request/response payloads for enrichment, clustering, scoring, and approval, with `PK`/`FK` markers shown only where the evidence supports a constraint and unmarked attributes denoting join participants without an evidenced key. | `05_DATA_MODEL.md` §3 (L730–878) |
| 3 | `fig-03-enrichment-run-sequence.mmd` | Phase 1 — Enrichment | Runtime sequence of one enrichment run: the ADF pipeline pages the Legacy table in 50-row offsets through a sequential `ForEach`, posts each page to `POST /enrich`, and merges the response back via `usp_merge_legacy_enriched`; the closing notes record the failure branch, in which `retry: 0` stops the loop at the failing batch while earlier batches remain committed, so a rerun re-enriches them and repeats their LLM and SERP spend. | `02_ARCHITECTURE.md` §4.1 (L197–216) |
| 4 | `fig-04-enrich-call-graph.mmd` | Phase 1 — Enrichment | Call graph of `POST /enrich` from the FastAPI handler down to the external-call boundary, showing the person-affiliation short-circuit, the Tier 1 (ROR, GLEIF) → Tier 2/2A → Tier 3 resolution ladder with its early returns, and the `_finalise_and_return` stage that resolves website, department domain, address, and search terms; edge labels name the external service each call reaches. | `00_INVENTORY.md` §3.1 (L205–243) |
| 5 | `fig-05-deduplication-run-sequence.mmd` | Phase 2 — Deduplication | Runtime sequence of one deduplication run: as exported, a single unbatched Lookup over the whole `test_77.Validation` table feeds one `POST /api/dedup/cluster-block` call whose Mode A, Mode B, and residue adjudication reach the LLM, with the result merged back via `usp_merge_validation_clusters`; the notes record the near-term change to iterate distinct `block_id` values through a `ForEach` and the per-block commit behaviour that follows. | `02_ARCHITECTURE.md` §4.2 (L235–249) |
| 6 | `fig-06-dedup-clustering-call-graph.mmd` | Phase 2 — Deduplication | Call graph of `POST /api/dedup/cluster-block`, showing signature construction (Step A) followed by per-block processing that branches on the `has_name2` bucket into Mode A partitioning or Mode B assignment, then residue adjudication, the deterministic identity and Name 2 split enforcement, and row emission; the three adjudication paths are the only edges that reach the deduplication LLM. | `00_INVENTORY.md` §3.2 (L250–265) |
| 7 | `fig-07-golden-record-election-sequence.mmd` | Phase 2 — Deduplication | Runtime sequence of golden-record election and steward approval (production workflow step 12): `POST /api/dedup/score` returns one proposal per cluster with `election_status ∈ {proposed, manual_review, unique}`, the steward inspects the DATAshaper deduplication view, and the "Apply Leading Code" action posts to `POST /api/dedup/approve`, which promotes the proposed winner into the golden fields; the closing note records that both endpoints are stateless and persist nothing. | `02_ARCHITECTURE.md` §4.3 (L262–275) |
| 8 | `fig-08-scoring-call-graph.mmd` | Phase 2 — Deduplication | Call graph of `POST /api/dedup/score`, showing weight coercion, per-cluster election via year-maxima computation, per-row scoring, tie-breaking, and merge-confidence aggregation, followed by summary construction and issue detection; the path makes no external calls and is deterministic over the request rows. | `00_INVENTORY.md` §3.3 (L271–281) |

## 2 · Chapters without figures

Introduction, Problem Description, State of the Art, Requirements Analysis, Cross-Cutting
Concerns, Discussion, and Conclusion have no figure from Passes 0–9. This is a statement of what
the passes produced, not a recommendation: Requirements Analysis and Cross-Cutting Concerns in
particular are documented as tables in `01_TRACEABILITY.md` and `06b_CROSSCUTTING.md` with no
diagrammatic counterpart, and any figure for those chapters would have to be authored rather than
extracted.

## 3 · Legibility at half-page width

Assessed by element count and label length. A half-page figure in a typical thesis layout is
roughly 75 mm wide; node labels in these diagrams carry fully qualified `file:line` citations,
which dominate node width and force either a small type size or heavy wrapping.

| # | Verdict | Elements |
|---|---------|----------|
| 1 | ⚠ **too dense** | 16 nodes, 2 subgraphs, 23 labelled edges |
| 2 | ⚠ **too dense** | 15 entities, 18 relationships, ~90 attribute rows |
| 3 | legible | 4 participants, 7 messages, 4 notes |
| 4 | ⚠ **too dense** | 26 nodes, 33 edges, one decision node |
| 5 | legible | 4 participants, 6 messages, 2 notes |
| 6 | borderline | 13 nodes, 14 edges |
| 7 | legible | 3 participants, 6 messages, 2 notes |
| 8 | legible | 9 nodes, 8 edges |

### Figure 1 — split into two

Split along the plane boundary the diagram already implies.

- **1a · Data plane.** `SAP → PRE → DSIMP → LEG → VAL → LOAD`, plus the two DATAshaper steward
  views (`DSISSUE`, `DSDEDUP`). Edges e1–e3, e13, e14, e19, e22. This is the table progression and
  reads as the spine of the chapter.
- **1b · Processing plane.** The four ADF pipelines, the Function App, AI Foundry, the external
  APIs, and Application Insights, with `LEG` and `VAL` retained as the two anchor nodes so the
  reader can register the figures against each other. Edges e4–e12, e15–e18, e20, e21, e23.

Keep the `eNN` labels identical in both halves so the single edge-evidence list in
`02_ARCHITECTURE.md` §2 continues to serve both figures without renumbering.

### Figure 2 — split into one overview plus three detail figures

Attribute blocks, not entities, are what make this unrenderable: 15 entities are tractable, ~90
attribute rows are not.

- **2 · Overview.** All 15 entities and all 18 relationships with relationship labels retained and
  **all attribute blocks removed**. This fits half-page width and is the figure the body text
  should reference.
- **2a · Ingestion and enrichment detail.** `IMPORT`, `LEGACY`, `VALIDATION`, `LOADFILE`,
  `ENRICH_RESULT`, `ISSUE_CODE` with attributes.
- **2b · Clustering detail.** `DEDUP_ROW`, `BLOCK`, `SIGNATURE`, `CLUSTER`, `DEDUP_RESULT` with
  attributes.
- **2c · Scoring and approval detail.** `SCORING_ROW`, `SCORING_RESULT`, `WEIGHTS`,
  `DEDUP_ISSUE`, `APPROVAL` with attributes.

The three detail figures are appendix material; only the overview belongs in the body. Where a
detail figure is not warranted, the attribute lists are already tabulated per column in
`05_DATA_MODEL.md` §1–2 and the figure can cite those tables instead.

### Figure 4 — split into two, cutting at `_finalise_and_return`

The call graph has two phases that share only one node, which makes the cut clean.

- **4a · Tier resolution.** `A → B → C → D`, the overflow check, `preprocess_record`, the
  person-only decision node, and the resolution ladder (`_resolve_person_affiliation`, ROR, GLEIF,
  `run_company_canonical`, `run_lab_resolver`, `run_tier2_canonical`, `run_tier2a`, `run_tier3`),
  terminating in `FIN` drawn as a single collapsed node. 18 nodes.
- **4b · Finalisation.** `FIN` expanded: `_maybe_resolve_website_bc` (Paths B and C),
  `_probe_department_url` with `_resolve_probe_base`/`resolve_final_url`, `_run_address_stage`, and
  `derive_search_terms`. 10 nodes.

A further reduction applies to both halves and to Figure 6: move the `file:line` citations out of
the node labels into a numbered legend beneath the figure. Node labels then carry only the function
name, which roughly halves node width, and the citations remain in the figure caption apparatus
rather than being lost. Applying that legend treatment to Figure 6 alone is sufficient — it does
not need splitting.
