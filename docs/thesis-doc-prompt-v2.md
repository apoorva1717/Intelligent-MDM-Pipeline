# Thesis documentation regeneration — v2 (full pass set)

You are producing code-derived ground truth for a Master's thesis on the MDM pipeline in this
repository. Every file you write will be used verbatim as the factual basis for thesis chapters.
The thesis author cannot see this repo while writing; whatever you do not state, does not exist.

## Rules (apply to every pass)

1. **One commit, clean tree.** Run `git status --porcelain`. If non-empty, stop and say so.
   Record `git rev-parse HEAD`, branch, and `date -I`. Every file starts with the identical
   header line:
   `Generated: YYYY-MM-DD · Commit: <full sha> · Branch: <branch> · Pass: <NN>`
2. **Cite everything.** Every factual claim carries `path/to/file.py:LINE` or `:L1–L2`.
   A claim without a citation is a defect. For SQL, ADF JSON, YAML and config, cite the same way.
3. **Code is ground truth.** Where code contradicts `README.md`, docstrings, Notion exports,
   comments or older `docs/thesis/*.md`, the code wins; record the discrepancy in `08_GAPS.md`
   with both sides quoted.
4. **No invented numbers.** No counts, rates, costs, runtimes unless you read them out of a file
   in this repo or produced them by a command whose invocation and verbatim output you paste.
   If a number is needed but absent, write `⚠ MEASUREMENT REQUIRED` and name the script or query
   that would produce it.
5. **Constants verbatim.** Thresholds, weights, temperatures, timeouts, regexes copied exactly.
6. **Mark unknowns loudly.** `⚠ UNVERIFIED —` rather than smoothing. Silence about an
   unimplemented or un-wired component is a defect.
7. **Register.** Present tense, third person, no "we", no evaluative adjectives. Methods-section
   prose. State current fact only — never "was changed from", "previously", "now fixed".
8. **Do not modify source code.** Documentation only. Do not run anything that writes to Azure,
   DATAshaper or SQL. Local tests and local scripts over local files only.
9. **Output location.** `docs/thesis/` — one file per pass. Overwrite the existing files; the
   old set is superseded in full. Do not start a later pass early. End every pass with a one-line
   summary and stop for review.
10. **Tables over prose** wherever the content is a rule, a parameter, a mapping or a count.

## Inputs you should find and use

- Service code (FastAPI app, enrichment, `dedup/`, `issues/`, config), `tests/`, fixtures.
- `adf/*.json` — every ADF pipeline that has been exported. List which pipelines are referenced
  in code, README or SQL but have **no** JSON in the repo; do not describe those from memory.
- `sql/` or wherever the four merge procs live
  (`usp_MergeLegacyEnriched`, `usp_MergeLegacyIssues`, `usp_MergeValidationClusters`,
  `usp_MergeValidationScores`).
- `weights.json`.
- `data/eval/` — the stratum pairs and the clustering/scoring workbooks. The authoritative
  pairing rule is in Pass 18; use it, and say which file is missing if one is.
- Existing `docs/thesis/11_DELTA.md` — read its header for the commit it was generated at;
  that is the baseline for the new delta (Pass 11). Do not otherwise trust old pass docs.

---

## Pass 00 — Inventory and call graph → `00_INVENTORY.md`

- File table: path | LOC | one-line purpose | last-touched commit date. List excluded dirs and why.
- Entry points: every HTTP route, Azure Function binding, CLI. Method, path, request model,
  response model, handler `file:line`. Expected at minimum: `/enrich`, `/issues`,
  `/api/dedup/cluster-block`, `/api/dedup/score`, `/api/dedup/approve`,
  `/api/preprocess/consolidate` (state whether present).
- Call graph per entry point as Mermaid `flowchart TD`, function names only in node labels,
  `file:line` in a numbered legend under the diagram.
- Dead/unreferenced code. List, do not delete.
- Test inventory: file | covers | module | result. Run `pytest -q`; paste the tail verbatim and
  the list of failing test names.

## Pass 01 — Requirements traceability → `01_TRACEABILITY.md`

Table: ID | requirement | implemented in | test | status ∈ implemented/partial/not implemented/
superseded. IDs from the repo's own numbering (UC, FR-1…FR-36, issue codes). For `partial`,
state the missing sub-behaviour. Second table: behaviour in code with no requirement.

## Pass 02 — Architecture → `02_ARCHITECTURE.md`

- Component diagram (Mermaid) split into **2a data plane** (SAP → DS Import → Legacy →
  Validation → load file, steward views) and **2b processing plane** (ADF pipelines, Function App,
  AI Foundry, external APIs, App Insights). Identical `eNN` edge labels in both halves; one edge
  evidence table.
- ADF orchestration, **from JSON only**: for each exported pipeline — parameters (`Entity`,
  `Groupcode`), Lookup query, ForEach batching, Web activity URL/timeout/retry, stored-proc
  activity, and whether the group-code predicate is present in the Lookup (quote the SQL).
  For `Entity_BasicFlow`: which Execute Pipeline activities exist and in what order.
  Pipelines without JSON: one line each, `⚠ NOT EXPORTED`.
- Sequence diagrams for: enrichment run, issues (baseline and post-enrichment) run, dedup
  cluster run, scoring run, steward approval. State for the **baseline `/issues` run** whether it
  is orchestrated by ADF or executed manually.
- Merge-back procs: for each, target table, key match, columns written, group-code guard
  (quote it), dynamic-SQL pattern.
- Security posture as of this commit: auth on the Function App, key handling, TLS setting,
  network approvals — facts only.

## Pass 03 — Algorithms → `03_ALGORITHMS.md`

Pseudocode for each stage with a worked example from a real fixture (cite fixture path):
Name1 overflow, preprocessing UC 6–12, care-of decomposition, Tier 1 ROR, Tier 1 GLEIF, person
affiliation, Tier 2/2A canonicalisation, Tier 3 LLM inference, finalisation (website, department
URL probe, address stage, search terms), batch consensus, flag emission; clustering (signature,
blocking, Mode A / Mode B, Name 2 asymmetry, residue adjudication, `Link ID`), scoring (election,
year-maxima, tie-break, merge confidence, `election_status`), consolidation endpoint if present.
For every LLM call: prompt file path, temperature, max tokens, JSON schema, and what evidence
is placed in context. Assert or refute, per call, that the model cannot return a value that is
not present in its context (the "constrained reader" claim) — cite the guard.

## Pass 03b — Exemplars → `03b_EXEMPLARS.md`

One end-to-end trace per stratum (S1–S5) from a real record in `data/eval/`: raw → issues →
enrichment tiers hit → enriched → issues remaining → cluster → score. Plus **one coupling
exemplar**: a pair of records that fail to cluster on raw values and cluster correctly only after
standardisation/enrichment, showing the exact field values before and after and the signature
each produces. This exemplar will carry the thesis' central argument; pick it carefully and cite
every value.

## Pass 04 — Parameters → `04_PARAMETERS.md`

Every tunable: name | value (verbatim) | file:line | effect | who sets it
(env / config / `dedup/weights.json`).
Full contents of `dedup/weights.json` reproduced, with the commit at which it was last
changed (`git log -1 --format=%h -- dedup/weights.json`). The file is at
`dedup/weights.json`, not at the repository root.

## Pass 05 — Data model → `05_DATA_MODEL.md`

DS Import/Legacy/Validation columns, load file, every request/response model. ER overview
(entities + relationships, **no attribute blocks**) plus three detail figures (ingestion,
clustering, scoring). Note the `code` column prefix convention (`<groupcode>_<sourcekey>`).

## Pass 06 — External dependencies → `06_EXTERNAL_DEPS.md`

ROR, GLEIF, Wikidata, SerpAPI/DuckDuckGo, page fetch, Azure OpenAI deployment
(`MDM-Apoorva-gpt-5.4`): endpoint, auth, rate limits as configured, timeouts, retry,
cache behaviour (`CACHE_FROZEN`), role (authority / witness / crosswalk) — cite the code that
enforces the role.

## Pass 06b — Cross-cutting → `06b_CROSSCUTTING.md`

Logging/telemetry fields, determinism (`tools/run_diff.py`), error handling (list every
fail-open `except` with file:line), idempotency of merge procs, provenance/origin fields and the
origin invariant, confidence fields and how they are computed.

## Pass 07 — Evaluation harness → `07_EVALUATION.md`

What exists to evaluate: scripts, fixtures, stress sets, I18N set. For every dataset in
`data/eval/` and `tests/fixtures/`: row count, country distribution (count `Country` column),
stratum, generating commit if recorded. **Do not compute results here** — Pass 18 does.

## Pass 08 — Gaps → `08_GAPS.md`

Every `⚠` raised in Passes 00–07 collected, numbered, with severity and the evidence on both
sides. Include README/docstring vs code discrepancies and Notion-vs-code discrepancies where a
Notion export exists in the repo.

## Pass 09 — Decision log → `09_DECISIONS.md`

Mined from `git log` and commit diffs: decision | alternative rejected | evidence commit | date.
Include at minimum: two-stage split, LLM vs trained matcher, flagging over inventing,
Wikidata as crosswalk only, blocking in service not DS, v1→v2 clustering, grain policy,
`THROW` vs `RAISERROR`, additive-only change discipline.

## Pass 10 — Figures → `docs/thesis/figures/*.mmd` + `INDEX.md`

Extract every Mermaid block from Passes 00–09 into one `.mmd` each, two-line `%%` provenance
header, byte-identical body. `INDEX.md`: number | file | chapter | caption | source. Legibility
verdict per figure at 75 mm width; none may exceed ~18 nodes.

## Pass 11 — Delta → `11_DELTA.md`

Against the commit in the old `11_DELTA.md` header. `git diff --stat`, then per old pass document:
superseded statement (quoted) → replacement → evidence. Then a **facts block** answering, with
command and verbatim output:
- Issue catalogue census: codes declared in code / emitted deterministically / observable in
  `data/eval/` outputs / referenced in docs but absent in code. Per group G1–G7.
- Group-code predicate present in each ADF Lookup: yes/no per pipeline, SQL quoted.
- Baseline `/issues` run: ADF or manual.
- `pytest -q` tail.
- `weights.json` values and last-change commit.
- `split_consolidated` delimiter handling (comma / semicolon / both).
- `/api/preprocess/consolidate` present or not.

## Pass 12 — Rationale → `12_RATIONALE.md`

Per design choice: the problem it solves, the mechanism, the cited guard, what it does **not**
solve. Sections: constrained reader, tier ladder ordering and cost, abstention, grain policy,
origin invariant, four-eyes scope (entity resolution only; enrichment writes back directly),
determinism. This file outranks `03_ALGORITHMS.md` on interpretation.

## Pass 13 — Clustering dossier → `13_CLUSTERING_DOSSIER.md`

Regenerate at this commit: v2 algorithm, known failure classes with exemplar rows from the
stress set, `Link ID` semantics, Name 2 token classes (child-unit / ancestor / continuation).

## Pass 14 — Scoring dossier → `14_SCORING_DOSSIER.md`

Election procedure, weights, tie-break, `election_status` semantics, the continuation-split
failure class, cross-cluster name inconsistency, with exemplar rows from
`dedup_STRESS_200_v1_*_scored*.xlsx`.

## Pass 15 — Issues dossier → `15_ISSUES_DOSSIER.md`

Per issue code: code | group | name | raised (raw/enriched/both) | remedy (rule/enrichment/steward)
| mandatory? | in 19-code reduction set? | detection function `file:line`. Counts per group.
If the repo contains a Notion export, reconcile; otherwise state that Notion is the author's
authority for group membership and list only what the code emits.

Document `ISSUE_CATALOGUE` and `DedupIssue` as two distinct vocabularies by design, with
their separate consumers and emission sites.

## Pass 16 — Rulesets → `16_RULESETS.md`  *(new)*

Four tables, one per ruleset, each row citing `file:line`. Rules are stated as a reader-verifiable
predicate, not a description ("`suite|ste\.` token present in `Street` → I-xx", not "detects
suite in street").

- **16.1 Standardisation and enrichment rules.** Preprocessing rules UC 0–12, admin-desk /
  unit-word rules, care-of, registry acceptance criteria per tier (what makes a ROR/GLEIF hit
  accepted), corroboration rule for Tier 2, Tier 3 flag rule, website/department-URL acceptance,
  address-stage rules, origin invariant, batch-consensus rule.
- **16.2 Issue detection rules.** Every issue code: number | name | predicate | field(s) |
  remedy class (programme rule / enrichment / four-eyes steward) | DQ dimension
  (completeness / consistency / validity / accuracy / uniqueness / placement — pick the one the
  predicate actually tests; mark `judgement` where the assignment is not mechanical).
- **16.3 Deduplication rules.** Signature construction, blocking key, strip lists (logistics/admin/
  alias markers), department-token block rule, Name 2 asymmetry, Mode A/B selection, residue
  nomination, identity hard rules, `Link ID` rule, LLM adjudication acceptance rule.
- **16.4 Scoring and election rules.** Each weight with value, per-row score formula, year-maxima
  rule, tie-break order, `election_status` thresholds, merge-confidence formula, issue codes
  raised by scoring.

## Pass 17 — Taxonomy map → `17_TAXONOMY_MAP.md`  *(new)*

Build the graph the thesis will draw. Two roots: **Standardisation & Enrichment** and
**Entity Resolution**. Under them, DQ dimensions; under those, issue groups G1–G7; under those,
codes. An issue may attach to both roots — record that as two edges, not a duplicate node.
Output: (a) an edge table `parent | child | basis (file:line or "judgement")`; (b) a Mermaid graph
of roots → dimensions → groups only (codes stay in the table); (c) the taxonomy **applied** to
each representative exemplar from Pass 03b — which codes fire on each, and under which root(s).
Flag every edge that is not mechanically derivable from the detection predicate.

## Pass 18 — Evaluation results → `18_EVAL_RESULTS.md`  *(new)*

Compute from `data/eval/` with a script you write under `tools/eval_report.py` (read-only over
the files; commit nothing). Paste the invocation and full output.

**File pairing.**
- `data/eval/S{n}_pre.xlsx` is the pre-enrichment issues export for stratum n;
  `S{n}_post.xlsx` is post-enrichment.
- Print the resolved pair and row counts per stratum before computing. Stop if a stratum is
  missing either file.
- Clustering/scoring workbooks are separate inputs, not a PRE/POST pair.

**Vocabulary.** The reduction metric is the `ISSUE_CATALOGUE` vocabulary only. `DedupIssue`
codes from `/api/dedup/score` are cluster-quality diagnostics, reported in a separate table
and never summed with catalogue codes.

Per stratum S1–S5:
- Rows; issues per code and per group, pre vs post; reduction absolute and relative;
  the 19-code reduction set and the 7 mandatory codes reported separately.
- **Completeness KPI**: filled/expected per field, pre vs post, over the address/name field set.
  Use the field list defined in the repo if one exists (cite it); otherwise use the columns
  present in both input and output and list them. No accuracy KPI.
- **Improvement index**: per issue category, count ÷ mean count across categories, post-run;
  list categories above 1.0. This is the "next iteration" trigger.
- Clustering: on the S5 test set and the stress set — expected groups vs found, over-merges,
  under-merges, `manual_review` count, `unique` count; scoring election agreement where a
  reference exists.
- Cost: from whatever the repo records (SerpAPI call counters, token logs, App Insights export,
  config prices). Calls per record per tier per stratum. Where price is not in the repo,
  `⚠ MEASUREMENT REQUIRED` with the source that would supply it.
- Determinism: `tools/run_diff.py` result on one stratum, verbatim.
Every number in this file must be reproducible from the pasted command.

## Pass 19 — Implementation state → `19_IMPLEMENTATION_STATE.md`  *(new)*

Two pages maximum. What is deployed and wired at this commit, from repo evidence: Function App
endpoints live; ADF pipelines exported / parameterised / wired into `Entity_BasicFlow`; merge
procs present; DS entity/group used; whether a full end-to-end DS run has been executed (cite a
log or output file, or `⚠ UNVERIFIED`); test suite state; known open items with owner
(Bernd / Bert / Daniel / Jean-Yves / Michel / Salvador) where a code comment or issue names one.
Explicitly out of scope: Salesforce enrichment/prospect matching.

## MANIFEST → `MANIFEST.md`

List every file written, its pass, its line count, and the shared commit hash. Confirm all
headers are identical. Final line: `Documentation set complete at <sha>.`
