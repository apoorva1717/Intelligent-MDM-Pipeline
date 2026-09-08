Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 10b

# Pass 10b — Thesis figures

Six authored figures, one per file in this directory. They are **not** extractions: no block here
appears in any pass document, and none is byte-identical to one. `../INDEX.md` holds the twenty
extracted figures, which are the auditor's view — every node a call site, every edge evidenced in
place. These six are the reader's view.

Each figure is held to the same limits: **at most 9 nodes, at most 12 edges, at most one level of
subgraph nesting, no `file:line` in any node label, and no citation in the drawing.** Node labels
name a step a reader would recognise, not a function. Where the real process has more steps than
the budget allows, the extra steps are collapsed into a named stage and the caption says what the
stage contains. The detail is not lost — it is in the pass document named on each figure's
`detail in` line.

Traceability lives in this file. Every claim each drawing makes is listed below against the pass
section that establishes it, so a reader who doubts an arrow can find the evidence without the
drawing having to carry it.

| # | File | Chapter | Nodes | Edges |
|---|------|---------|-------|-------|
| T1 | `fig-T1-end-to-end.mmd` | Introduction | 9 | 8 |
| T2 | `fig-T2-tier-ladder.mmd` | Phase 1 — Enrichment | 8 | 9 |
| T3 | `fig-T3-improvement-loop.mmd` | Phase 1 — Enrichment | 9 | 10 |
| T4 | `fig-T4-deduplication.mmd` | Phase 2 — Deduplication | 9 | 9 |
| T5 | `fig-T5-taxonomy.mmd` | Problem Description | 9 + 1 container | 8 |
| T6 | `fig-T6-components.mmd` | System Architecture | 9 + 1 container | 9 |

---

## T1 — End-to-end pipeline

**Caption.** A customer record travels from the SAP extract through the three DATAshaper table
layers, is enriched and audited on the Legacy table and clustered and scored on the Validation
table, and leaves as a golden record.

**Detail in.** `02_ARCHITECTURE.md` §2.1a for the stage order and the table layers; §2.3.1–§2.3.5
for what happens inside each of the four service stages.

**Collapsed.** *Import staging* is the DS Import (bronze) layer and the preprocessing that
precedes it, which is not in this repository. *Enrichment* is the whole of T2. *Deduplication* is
blocking through adjudication, the left half of T4. *Election and approval* is election, demotion,
manual review and steward sign-off, the right half of T4. The return leg — Validation to load file
to SAP — is omitted: the figure ends at the golden record, as the chapter's framing does.

| Claim the drawing makes | Source |
|---|---|
| SAP extract feeds an Import layer, which feeds Legacy, which feeds Validation | `02_ARCHITECTURE.md` §2.1a, edges e01, e02, e10 |
| Enrichment reads and writes the Legacy table | §2.1c, edges e03 and e06 |
| Issue detection runs on the Legacy table, after enrichment | §2.1c, edges e07 and e09; the ordering of e03/e06 before e07/e09 |
| Deduplication and scoring read and write the Validation table | §2.1c, edges e11, e13, e14, e16 |
| A golden record is the terminus of the scoring and approval path | `14_SCORING_DOSSIER.md` §1 and §8 |

⚠ The final hop from the load file into SAP is `⚠ UNVERIFIED` in `02_ARCHITECTURE.md` §2.1c
(edge e21). The figure does not draw it, so it makes no claim about it.

---

## T2 — Enrichment tier ladder

**Caption.** One record escalates through the ladder only as far as it must — deterministic
preprocessing first, then free registry lookups, then paid search and page evidence, then the
model alone — and every lane exits through a gate that may refuse to write.

**Detail in.** `03_ALGORITHMS.md` §3.1 for the executed stage order; `12_RATIONALE.md` §2 for the
cost of each stage and why the order is cheapest-first; `12_RATIONALE.md` §3 for the gate.

**Collapsed.** *Registry lookup* is Tier 1 ROR, Tier 1 GLEIF/LEI and the Wikidata crosswalk.
*Grounded search* is Tier 2 canonicalisation, the Tier 2A contact lookup and the grounded
resolver; their costs differ inside the stage — Tier 2 is LLM-only with no search call, Tier 2A
adds one search call, the grounded resolver one to two searches and up to three page fetches.
*Model inference* is Tier 3. *Name gate* stands for the whole abstention mechanism, which is four
distinct shapes in three modules, not one switch. Omitted entirely: the UC 0 name-overflow check
that precedes preprocessing, the person-affiliation short-circuit, the AP short-circuit, and the
finalisation stage — website resolution, the address stage, search terms and flags — that follows
the gate.

| Claim the drawing makes | Source |
|---|---|
| The order is deterministic → registry → search → model, and it is cheapest-first by design | `12_RATIONALE.md` §2, the ordered cost table; `README.md:93` and `:108` as quoted there |
| Preprocessing costs no network call | `12_RATIONALE.md` §2, order 1 — "Deterministic; no network" |
| Registry lookups are free APIs | §2, orders 3–5; `06_EXTERNAL_DEPS.md` §1, services 1–3, auth "none" |
| Search and fetch are reached only on a registry miss | `03_ALGORITHMS.md` §3.1, the miss branches out of Tier 1 |
| The model-only lane is reached only when evidence fails | `12_RATIONALE.md` §2, order 10b and "Does not solve" item 3 — Tier 3 is the grounded lane's degraded mode |
| A refusal keeps the record's own value rather than writing one | `12_RATIONALE.md` §3, shape 1 and the "Does not solve" paragraph |
| Refusal is a decision with a closed vocabulary, not a failure | §3 — six refusal reasons, and a three-valued identity decision in which only `different` refuses |

⚠ The ladder as drawn shows escalation but not spend: `12_RATIONALE.md` §2 records that no counter
caps what one record can pay, so a single record may traverse every stage.

---

## T3 — Issue detection and the improvement loop

**Caption.** Every issue is detected, given a code and a group, and routed by its remedy class;
the enriched batch is then re-audited and the difference between the two audits is the measure
that drives the next revision.

**Detail in.** `15_ISSUES_DOSSIER.md` §15.6 for the three comparison segments and how a code is
assigned to one; §15.5 for the reduction set the measure is computed over; §15.1 and §15.3 for the
catalogue and the 33 live codes.

**Collapsed.** *Detect issues* and *Assign code and group* are one pass over the batch in the
implementation, split here because the two are different claims. *Automated remediation* is two
remedy classes, `rule` and `enrichment`, which the comparison treats identically. *Measure
reduction* is the before/after join and its segmentation. The three endpoints that carry this —
file-in/file-out, JSON, and compare — are one path in the drawing.

| Claim the drawing makes | Source |
|---|---|
| A detected issue carries both a code and a group | `15_ISSUES_DOSSIER.md` §15.1; group is a declared attribute, never the code prefix (`17_TAXONOMY_MAP.md` §17.0, class C) |
| Routing is by remedy class, not by group | `15_ISSUES_DOSSIER.md` §15.6, and decision D-43 recorded in `09_DECISIONS.md` |
| The remedy classes are rule, enrichment and steward | §15.6, the `segment()` predicate |
| Only the rule and enrichment codes enter the reduction measure | §15.5 — 18 codes at this commit — and §15.6, "Reduced" |
| Steward-remedy codes are expected to survive into the re-audit | §15.6, "Expected to persist" — 12 codes |
| The loop closes: the measure informs the next revision | `07_EVALUATION.md` §6.1 — successive run sets at three commits, each scored under the same reduction rules; the compare endpoint is what produces the measure (`15_ISSUES_DOSSIER.md` §15.6) |

⚠ Three codes are raised by enrichment's own output and can never fire on the raw batch; they form
a third comparison segment, *Verification*, and are excluded from the reduction measure
(`15_ISSUES_DOSSIER.md` §15.6 and §15.7). The drawing does not show them — its `Remedy class`
branch has two arms, not three. ⚠ The reduction set holds **18** codes at this commit, not the 19
some earlier text quotes (§15.5). ⚠ The recorded run sets that exercise this loop are all at
commits preceding this one, and `RUNS.md` is stale with respect to the current `data/eval/`
(`07_EVALUATION.md` §6.1).

---

## T4 — Deduplication

**Caption.** Records are blocked by delivery point, collapsed onto signatures, adjudicated by the
model where a block holds more than one, split back apart by deterministic guards, and the
surviving cluster elects a golden record that a steward approves.

**Detail in.** `13_CLUSTERING_DOSSIER.md` §1.2 for the pipeline, §1.3 for the block key, §1.5 for
mode selection and §1.7 for the guards; `14_SCORING_DOSSIER.md` §1 for election, §5 for demotion
and §8 for approval.

**Collapsed.** *LLM adjudication* is the two modes — a partition per bucket for blocks of 2 to 12
signatures, incremental assignment above 12 — plus the residue nomination pass.
*Deterministic split guards* is four guards in a fixed order: the address split, identity split
and conflict routing, the reasoning-disowns-membership check, and institution conflict flagging.
*Golden-record election* is eleven scoring criteria, the cluster year maxima, the recency gate and
the tie-break. The request-level `Link ID` pass that runs after emission is not drawn.

| Claim the drawing makes | Source |
|---|---|
| Blocking is by delivery point, and precedes everything | `13_CLUSTERING_DOSSIER.md` §1.2 node A, §1.3 |
| Rows collapse onto signatures before any model call | §1.2 nodes A→B; §1.4 |
| The model is called only when a block yields two or more signatures | §1.2 node C — a block of one signature is emitted as a single entity with no LLM call |
| Deterministic guards run after adjudication and may split what it merged | §1.2 nodes H–K; §1.7, "the deterministic guards, in order" |
| Election is per cluster, and clusters of one do not elect | `14_SCORING_DOSSIER.md` §1, steps 6 and 9 |
| A cluster can be demoted to manual review instead of electing | §5 — four independent demotion conditions |
| Approval is what promotes a proposal to a golden record | §8 — `approved` sets `golden_record_id` from `proposed_golden_id` |
| A manual-review row reaches a golden record only through approval | §8, "the only way a `manual_review` row acquires a non-blank `golden_record_id`" |

⚠ *Steward approval* as drawn is the process step, not one endpoint. On the observed path the
steward writes a Leading Code in the DATAshaper deduplication view; `POST /api/dedup/approve`
exists, is stateless, persists nothing, and no pipeline or procedure calls it
(`02_ARCHITECTURE.md` §2.3.5, `14_SCORING_DOSSIER.md` §8). There is no durable approval store.

---

## T5 — Taxonomy graph

**Caption.** Two roots — standardisation and enrichment, entity resolution — sit over the six
data-quality dimensions, which sit over the seven issue groups; both roots reach every dimension,
so the dimension layer does not tell a reader which phase owns a defect.

**Detail in.** `17_TAXONOMY_MAP.md` §17.2.1 for the twelve root-to-dimension edges, §17.2.2 for
the dimension-to-group edges, §17.3 for the graph drawn with all seven groups, and §17.2.3–§17.2.4
for the codes beneath them.

**Collapsed.** *Seven issue groups* is one node standing for seven: G1 Data in Wrong Field
(10 codes), G2 Missing Required Data (7), G3 Duplicate or Conflicting Data (9), G4 Invalid Format
or Length (3), G5 Non-Standard Naming (2), G6 Enriched — Confirm (1), G7 Left Unchanged — Verify
(1). Two facts are lost in that collapse and are stated here instead: **G3 has two dimension
parents**, uniqueness (6 codes) and consistency (3), and **completeness has two children**, the
quality group G2 and the verification group G7. Individual codes are never drawn, at any
magnification. The two roots reach the dimension band as one arrow each rather than as twelve
crossing arrows — the count on each arrow carries what the twelve edges would have shown.

| Claim the drawing makes | Source |
|---|---|
| There are exactly two roots, and they are the two phases as shipped | `17_TAXONOMY_MAP.md` §17.1 |
| The six dimensions are placement, completeness, uniqueness, consistency, validity, accuracy | §17.2.1 |
| Every one of the six dimensions sits under both roots — all twelve edges exist | §17.2.1, "All twelve edges exist" |
| R1 reaches 33 codes; R2 reaches 27 | §17.2.1, the per-dimension counts: 10+8+6+3+3+3 and 9+6+3+3+3+3 |
| Groups sit below dimensions, and codes below groups | §17.0, edge classes B and C |
| The dimension layer does not partition the roots | §17.2.1, stated in bold; §17.0, "the dimension layer does not separate the two phases" |

⚠ The R1 count includes `reads-only` edges — a code Phase 1 can see but not repair still attaches
to R1. The remediable subset is smaller: 9, 5, 6, 3, 1, 3 across the six dimensions (§17.2.1,
parenthesised counts). ⚠ Eleven of the edges beneath this drawing are judgement, not derivation
(§17.4).

---

## T6 — Component view

**Caption.** DATAshaper holds the data and the steward's views, Azure Data Factory drives the
four pipelines, one service does the enrichment and deduplication work, and the external services
sit behind that service alone.

**Detail in.** `02_ARCHITECTURE.md` §2.1a and §2.1b for the two planes and §2.1c for the evidence
on every edge; `06_EXTERNAL_DEPS.md` §1 for the service inventory and §2 for each one.

**Collapsed.** *Registry APIs* is ROR v2, GLEIF v1 and the Wikidata Action API. *Web search and
pages* is SerpAPI or DuckDuckGo, plus arbitrary third-party page fetches. *Language model* is
Azure OpenAI, used by both phases under two deployments. *Azure Data Factory* is four exported
pipelines — enrichment, issues, deduplication, scoring — and *read and merge back* stands for both
the Lookup that reads a table and the merge procedure that writes it back. The four merge
procedures are not drawn separately.

| Claim the drawing makes | Source |
|---|---|
| ADF invokes the service; the service is one Function App | `02_ARCHITECTURE.md` §2.1b; `06_EXTERNAL_DEPS.md` §1, closing paragraph |
| No ADF pipeline calls an external service directly | `06_EXTERNAL_DEPS.md` §1 — every outbound URL in the four pipelines addresses the Function App |
| Each pipeline reads a DATAshaper table and merges its result back | `02_ARCHITECTURE.md` §2.1b, the four merge procedures; §2.1c, edges e06, e09, e13, e16 |
| The external calls are registry, search and page, and model | `06_EXTERNAL_DEPS.md` §1, services 1–8 |
| The steward works in the DATAshaper views and writes back there | `02_ARCHITECTURE.md` §2.1c, edges e17, e18, e19 |
| The service emits telemetry | §2.1c, edge e22 |
| SAP is both the source and the destination | §2.1c, edges e01 and e20/e21 |

⚠ The steward's write back into the Validation table is `[OBSERVED]`, not code: no pipeline and no
repository code performs it (`02_ARCHITECTURE.md` §2.1c, edge e19). ⚠ The address-validation
service named in the DATAshaper workflow is `⚠ NOT PRESENT` in this repository
(`06_EXTERNAL_DEPS.md` §1, service 9, and §2.9); the drawing does not show it.

---

## Chapters without an authored figure

State of the Art, Requirements Analysis, Cross-Cutting Concerns, Discussion and Conclusion take
no figure from this pass. Discussion is covered by the coupling exemplar, figure 20 of
`../INDEX.md`, which is an extraction and carries its citations in the drawing because its
audience is the auditor. Requirements Analysis and Cross-Cutting Concerns remain tables in
`01_TRACEABILITY.md` and `06b_CROSSCUTTING.md`.

---

## Legibility at typesetting

Rendered with `@mermaid-js/mermaid-cli@11` at default theme and font size, the six figures have
these aspect ratios:

| # | Rendered | Ratio | Placement |
|---|---|---|---|
| T1 | 1893 × 70 | 27 : 1 | ⚠ see below |
| T2 | 477 × 915 | 1 : 1.9 | half-page portrait |
| T3 | 515 × 917 | 1 : 1.8 | half-page portrait |
| T4 | 293 × 950 | 1 : 3.2 | tall column; sits beside body text |
| T5 | 1309 × 376 | 3.5 : 1 | full text width |
| T6 | 1138 × 530 | 2.1 : 1 | full text width |

⚠ **T1 cannot be set at body-text size in one row.** Nine stages in a single row with no branches
is the specification for that figure, and it produces a 27 : 1 banner: placed at a 150 mm text
width, the node labels come out near 3.5 pt. Three ways out, in order of preference — typeset it
landscape on its own page; re-render it with an enlarged font and accept a taller strip; or, in
final typesetting only, break the row after *Validation table* into two stacked rows, which
preserves the stage sequence and the absence of branches. The `.mmd` in this directory is the
one-row form as specified; any break belongs in the typesetting, not in the source.

T4 at 1 : 3.2 is the reverse case and is comfortable: it is narrow enough to sit in a column
beside its own discussion.
