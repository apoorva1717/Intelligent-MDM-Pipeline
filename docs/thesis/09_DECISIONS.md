Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 09

# Pass 09 — Decision log

## 0. Method and scope

This file records design decisions that are evidenced in this repository: each carries the
commit whose diff or message establishes it, the date of that commit, and the file:line at
this commit where the decision is in force. A decision whose *rejected alternative* is not
evidenced by a diff or a commit message is marked `⚠ ALTERNATIVE NOT EVIDENCED` and its
"alternative rejected" column states what the code excludes, not what the project tried.

Commands run to produce this file:

```
$ git rev-list --count HEAD
154

$ git log --reverse --date=short --pretty=format:"%h %ad %s" | head -1
f77080b 2026-04-09 Initial Enrichment Code

$ git log -1 --date=short --pretty=format:"%h %ad %s"
86d173b 2026-09-07 sql: reformat merge procs; adf: parameterised pipelines
```

The history mined is 154 commits, 2026-04-09 to 2026-09-07, on branch `feature/llm-fixes`.

**Tree state at generation.** `git status --porcelain` is not empty:

```
 M docs/thesis/00_INVENTORY.md
 M docs/thesis/01_TRACEABILITY.md
 M docs/thesis/02_ARCHITECTURE.md
 M docs/thesis/03_ALGORITHMS.md
 M docs/thesis/03b_EXEMPLARS.md
 M docs/thesis/04_PARAMETERS.md
 M docs/thesis/05_DATA_MODEL.md
 M docs/thesis/06_EXTERNAL_DEPS.md
```

Every modified path is an output of Passes 00–06 of this same regeneration. No source file,
SQL file, ADF export or fixture is modified, so the code read for this pass is the code at
`86d173b`. `docs/thesis/06b_CROSSCUTTING.md`, `07_EVALUATION.md` and `08_GAPS.md` still carry
the header `Commit: 515cc7c…` and have not been regenerated at this commit.

**Register note.** Rule 7 forbids historical statements about the system. A decision log
cannot be written without them, because a rejected alternative is by definition a past state.
Historical content in this file is confined to the *Alternative rejected* and *Evidence*
columns and to quoted commit text; every *Code at this commit* statement is present tense.

---

## 1. Master table

| # | Decision | Alternative rejected | Evidence commit | Date |
|---|----------|----------------------|-----------------|------|
| D-01 | Enrichment and entity resolution are two separate passes with separate endpoints | One pass that cleans and merges together | `13a1274`, `c4834a5` | 2026-06-17, 2026-08-20 |
| D-02 | Issues are measured twice — once on the raw record, once on the enriched record — into two columns | A single post-enrichment audit | `2bb9d23`, `5a1fd70` | 2026-06-04, 2026-09-07 |
| D-03 | The service is stateless JSON-in / JSON-out; the orchestrator owns file ↔ JSON and persistence | Service-side file I/O and an approval store | `efe1379` | 2026-07-22 |
| D-04 | Entity and group code are pipeline and procedure parameters; the group code is a `code`-prefix `LIKE` predicate | A schema and table hardcoded per run | `5a1fd70`, `86d173b` | 2026-09-07 |
| D-05 | `/enrich` is called on 30-row pages through a sequential `ForEach`, retry 0 | One call per row; one call per whole group | `86d173b` | 2026-09-07 |
| D-06 | Tiered escalation: deterministic methods run before probabilistic ones | Going to the model first | `847f92e` | 2026-05-14 |
| D-07 | Flagging over inventing: low confidence returns the input and raises a flag | Emitting a best guess | `635d5ba`, `847f92e` | 2026-04-11, 2026-05-14 |
| D-08 | The flag is rebuilt once from the record's final state and is field-scoped | Each tier appending its own reason as it runs | `5e423c2` | 2026-08-20 |
| D-09 | Advisory codes emit prose without queueing a reviewer; an unverified domain ships rather than being blanked | Every code raising `flag_for_review` | `9da5ae8` | 2026-08-27 |
| D-10 | Every Name 1 / Name 2 candidate passes one write gate, whatever lane produced it | Registry and LLM names reaching the field by separate routes | `20b3050` | 2026-09-01 |
| D-11 | Identity change is a three-valued verdict (SAME / UNDECIDABLE / DIFFERENT); only DIFFERENT refuses | A boolean `canonical_preserves_identity` | `20b3050` | 2026-09-01 |
| D-12 | Provenance is per field, enforced by a write API, over six scoped fields | A record-level `tier_used` / `source` / `confidence` triple | `59d3e4d` | 2026-08-20 |
| D-13 | An origin may change only when the value changes | A lane re-attributing a value it did not change | `e31b53b` | 2026-09-02 |
| D-14 | One domain-resolution chokepoint with an ownership guard; the `Domain` column carries the bare domain | Five direct write sites and no ownership check | `4645b33` | 2026-08-20 |
| D-15 | One classification authority for `record_type` | Last-writer-wins across tiers | `6038372` | 2026-08-20 |
| D-16 | Batch consensus is field propagation, never a merge; rows in = rows out | Letting Phase 2 adjudicate intra-batch divergence | `c4834a5` | 2026-08-20 |
| D-17 | Wikidata is a lookup key and a witness, never a source of a name | Copying a Wikidata label into `name1_enriched` | `55b9e33` | 2026-08-25 |
| D-18 | A verified Tier 1 match writes the registry's official name unconditionally | Applying the LLM-path identity guard to a registry's own name | `cbd2698` | 2026-08-20 |
| D-19 | Lookup caches key on the normalised name plus country; Tier 1 is re-run once after canonicalisation | One cache entry per input spelling; a single Tier 1 attempt | `0f884f1` | 2026-08-20 |
| D-20 | Search terms derive only from enriched values | Falling back to pre-enrichment input | `308f357` | 2026-08-20 |
| D-21 | An LLM adjudicates the name decision in entity resolution | A trained matcher / embeddings ⚠ | `13a1274`, README | 2026-06-17 |
| D-22 | The LLM sees distinct signatures, never raw rows | Sending every row pair to the model | `13a1274` | 2026-06-17 |
| D-23 | Blocking is computed in the service from delivery points; a caller-supplied `block_id` still wins | Relying on the upstream address gate's raw string hash alone | `8868908` | 2026-09-04 |
| D-24 | v2 clustering ships behind three independent default-false flags | Replacing v1 in place | `8868908` | 2026-09-04 |
| D-25 | `Link ID` is a third outcome beside `Cluster ID`; an ROR/LEI conflict routes to review rather than splitting the entity | Reporting a family as either a merge or as unique | `60e0b51` | 2026-09-05 |
| D-26 | Residue nomination is candidacy only; it never merges | Letting a similarity score merge a pair directly | `929492b` | 2026-07-23 |
| D-27 | An ambiguous verdict routes the block to `manual_review` | Choosing a safe default | `13a1274` | 2026-06-17 |
| D-28 | Adjudicator calls are recorded and replayed from a content-addressed cache; replay refuses on a miss | Fresh model calls on every run | `ec5efab` | 2026-09-05 |
| D-29 | `temperature=0.0` is sent only where the deployment accepts it alongside `reasoning_effort`; `seed` is not added | Sending both unconditionally | `3527585` | 2026-08-18 |
| D-30 | The prompt version is a data column, and the v2 prompt carries its own version | One version string across prompt revisions | `8868908` | 2026-09-04 |
| D-31 | Election is deterministic arithmetic over an editable weights table, separate from adjudication | Electing inside the LLM adjudication pass | `611c348` | 2026-07-11 |
| D-32 | Scoring is permissive: an unrecognised value scores 0 and never fails the batch; a duplicated `row_id` is the one hard error | Rejecting dirty rows | `611c348` | 2026-07-11 |
| D-33 | A merge below the confidence threshold is demoted to `manual_review`, and approval is a separate endpoint | Merging on the adjudicator's verdict alone | `efe1379` | 2026-07-22 |
| D-34 | Count points are awarded only to the cluster's most recent last-used year | Awarding count points on any row | `c18921d` | 2026-07-23 |
| D-35 | Dates are coerced to years only on the two `*_last_used` fields | Extracting `.year` from any date anywhere | `f5c8d8d` | 2026-09-05 |
| D-36 | Grain consolidation is a column append: rows in = rows out | Filtering the extract to one row per customer | `6cf4639` | 2026-09-05 |
| D-37 | ZFIS is out of scope for dedup | Re-checking the upstream gate | `611c348` | 2026-07-11 |
| D-38 | The merge procedures raise with `THROW` | `RAISERROR` ⚠ | `5a1fd70` | 2026-09-07 |
| D-39 | Every merge procedure is `WHEN MATCHED` only — no `INSERT` branch | Upserting unknown customers | `5a1fd70` | 2026-09-07 |
| D-40 | An empty enrichment value never blanks a populated target column | A straight column assignment | `ecac51a`, `5a1fd70` | 2026-08-19, 2026-09-07 |
| D-41 | Dynamic SQL splices only the schema name; every other value is an `sp_executesql` parameter | Concatenating the group code into the statement | `5a1fd70` | 2026-09-07 |
| D-42 | Behaviour changes ship additively: a default-false switch plus a test that the switched-off output is byte-identical | Changing behaviour in place | `8868908` | 2026-09-04 |
| D-43 | A before/after comparison reads `raised` and `remedy` per code, not the code's group | Segmenting by `REDUCIBLE_GROUPS` / `PERSISTENT_GROUP` | `e7e60cd` | 2026-09-07 |
| D-44 | A retired issue code is withdrawn in place, keeping its identifier and a reason | Deleting the entry or reusing the number | `e7e60cd`, `3ed371b` | 2026-09-07 |

---

## 2. Pipeline shape and orchestration

### D-01 — Enrichment and entity resolution are two passes

**Alternative rejected.** A single pass that cleans a record and decides its duplicates
together.

**Evidence.** `13a1274` (2026-06-17, "Deduplication endpoint") adds `dedup/adjudicator.py` as
a second endpoint rather than extending `/enrich`. `c4834a5` (2026-08-20) states the boundary
in its message: "It is field propagation, never a merge — the batch out is the same length and
order as the batch in, and Phase 2 remains the only place entities are merged."

**Code at this commit.** `README.md:2506` states the reason a record-local pass cannot make the
decision: "Phase 1 (`/enrich`) cleans each record in isolation. It cannot tell whether two
*different* records are actually the same customer — that is a cross-record decision."
`README.md:2508` bounds Phase 2: "It does **not** do address validation, embeddings,
golden-record election, or file I/O." The two entry points are `api/routes.py:106`
(`/enrich`) and `api/routes.py:1330` (`/api/dedup/cluster-block`).

**What the decision does not settle.** The ordering constraint is stated in prose, not enforced
in code: nothing in `/api/dedup/cluster-block` checks that its input has been enriched.

### D-02 — Issues are measured on both sides of enrichment

**Alternative rejected.** One audit, run after enrichment.

**Evidence.** `2bb9d23` (2026-06-04, "Add Remaining Issues sheet to XLSX comparison output").
`5a1fd70` (2026-09-07) adds the two-column guard to the merge procedure.

**Code at this commit.** `sql/usp_merge_legacy_issues.sql:14–17` admits exactly two targets:

```sql
    IF @target_column NOT IN (N'Issues Before', N'Issues')
    BEGIN
        THROW 50000, N'target_column must be Issues Before or Issues.', 1;
    END;
```

`api/routes.py:916` exposes `/issues/compare`. `enrichment/issue_detection.py` carries a
`raised` column per code recording whether it is raised on the raw record, the enriched record,
or both (`e7e60cd`, D-43).

**⚠ NOT WIRED.** `adf/issues_pipeline.json` passes only `payload` to the procedure and never
`@target_column`, so the exported pipeline cannot select which of the two columns it writes.
See D-04.

### D-03 — The service is stateless; the orchestrator owns files and persistence

**Alternative rejected.** A service that reads and writes files and keeps an approval store.

**Evidence.** `efe1379` (2026-07-22) adds `/api/dedup/approve` as a stateless echo endpoint.

**Code at this commit.** `api/routes.py:1491–1496`: "Stateless: the caller submits the scored
rows, the decision is applied to the named cluster_id … and the updated rows are echoed back.
Persistence is intentionally out of scope — a durable approval store is a future step."
`dedup/scoring.py:617` states the same for the underlying function: "Never mutates the inputs."
`README.md:2508`: "The orchestrator (ADF/DATAshaper) handles file ↔ JSON conversion; this
endpoint is strictly JSON in / JSON out."

**Qualification.** File-upload routes exist beside the JSON routes — `api/routes.py:700`
(`/enrich/file`), `:1026` (`/api/preprocess/consolidate/file`), `:1360` (`/api/dedup/file`),
`:1518` (`/api/dedup/score/file`). None is the URL any exported pipeline calls: the four Web
activities in `adf/*.json` target `/enrich`, `/issues/json`, `/api/dedup/cluster-block` and
`/api/dedup/score`.

### D-04 — Entity and group code are parameters; the group code is a `code`-prefix predicate

**Alternative rejected.** A fixed schema and table per deployment. `ecac51a` (2026-08-19)
carries the earlier shape verbatim: `MERGE dp_legacy.test_77.Legacy AS tgt`, with no entity
parameter and no group-code predicate.

**Evidence.** `5a1fd70` and `86d173b` (both 2026-09-07, "sql: reformat merge procs; adf:
parameterised pipelines").

**Code at this commit.** All four procedures take `@chrEntity SYSNAME, @chrGroupCode
NVARCHAR(50)` (`sql/usp_merge_legacy_enriched.sql:2–3`, `usp_merge_legacy_issues.sql:2–3`,
`usp_merge_validation_clusters.sql:2–3`, `usp_merge_validation_scores.sql:2–3`) and build the
scoping pattern the same way (`usp_merge_legacy_enriched.sql:26–28`):

```sql
    -- Group code is scoped by the code prefix: <groupcode>_<source key>
    DECLARE @pat NVARCHAR(60) = LTRIM(RTRIM(@chrGroupCode)) + N'\_%';
    DECLARE @esc NCHAR(1) = N'\';
```

All four ADF pipelines declare the same two parameters and carry the predicate in their Lookup:

| Pipeline | Parameters | Group-code predicate in Lookup |
|----------|-----------|-------------------------------|
| `adf/enrichment_pipeline.json` | `chrEntity`, `chrGroupCode` | yes |
| `adf/issues_pipeline.json` | `chrEntity`, `chrGroupCode` | yes |
| `adf/deduplication_pipeline.json` | `chrEntity`, `chrGroupCode` | yes |
| `adf/scoring_pipeline.json` | `chrEntity`, `chrGroupCode` | yes |

The `issues_pipeline` Lookup query, quoted from `adf/issues_pipeline.json`:

```sql
SELECT * FROM dp_legacy.[@{pipeline().parameters.chrEntity}].Legacy
WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\' ORDER BY [code]
```

**⚠ NOT WIRED — the decision is half-implemented.** Every one of the four stored-procedure
activities passes `payload` and nothing else:

```
$ python3 -c "import json,glob; ..."   # storedProcedureParameters keys per pipeline
adf/deduplication_pipeline.json -> Mapping.usp_MergeValidationClusters params: ['payload']
adf/enrichment_pipeline.json    -> Mapping.usp_merge_legacy_enriched   params: ['payload']
adf/issues_pipeline.json        -> Mapping.usp_MergeLegacyIssues       params: ['payload']
adf/scoring_pipeline.json       -> Mapping.usp_MergeValidationScores   params: ['payload']
```

`@chrEntity` and `@chrGroupCode` have no defaults in any procedure, so a call carrying only
`payload` cannot satisfy the signature.

**⚠ NAME MISMATCH.** `adf/enrichment_pipeline.json` names the procedure
`Mapping.usp_merge_legacy_enriched`; `sql/usp_merge_legacy_enriched.sql:1` defines
`[Mapping].[usp_MergeLegacyEnriched]`. The other three pipelines name the PascalCase forms that
`sql/` defines.

### D-05 — 30-row pages, sequential, retry 0

**Alternative rejected.** One Web call per row, and one Web call for a whole group.

**Evidence.** `86d173b` (2026-09-07).

**Code at this commit.** `adf/enrichment_pipeline.json` computes page offsets in `Lookup2`:

```sql
SELECT (n.rn - 1) AS offset
FROM (
    SELECT ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS rn
    FROM dp_legacy.[@{pipeline().parameters.chrEntity}].Legacy
    WHERE [code] LIKE '@{pipeline().parameters.chrGroupCode}\_%' ESCAPE '\'
) n
WHERE (n.rn - 1) % 30 = 0
```

and `ForEach1` runs with `isSequential: true`, its inner Lookup fetching
`OFFSET @{item().offset} ROWS FETCH NEXT 30 ROWS ONLY`. Its Web activity carries
`timeout: 0.12:00:00`, `retry: 0`, `retryIntervalInSeconds: 30`. The other three pipelines have
no `ForEach`: `issues`, `deduplication` and `scoring` each run one Lookup, one Web call and one
merge for the whole group, with the same timeout and retry policy.

**Consequence recorded rather than resolved.** `retry: 0` means a transient failure on one page
fails the pipeline; the merge procedures are `WHEN MATCHED` updates (D-39) and are therefore
safe to re-run, so the constraint is a choice about pipeline behaviour, not about the SQL.

---

## 3. Enrichment

### D-06 — Deterministic before probabilistic

**Alternative rejected.** Sending the record to the model first and using deterministic rules
to check the answer.

**Evidence.** `847f92e` (2026-05-14) introduces the design-principles list into `README.md`.

**Code at this commit.** `README.md:93`: "start with the cheapest, most reliable method and
escalate only when cheaper methods fail." The ladder is `README.md:97–103`: preprocessing
(regex, zero cost) → Tier 1 ROR → Tier 1 GLEIF → Tier 2A contact → Tier 2 canonical → Tier 2B
department → Tier 3 pure LLM. `README.md:108`: "**Deterministic before probabilistic.**
Regex-based preprocessing runs before any API or LLM call." `README.md:109` constrains the
model's input as well: "LLM prompts extract from structured page elements (URL path, title, H1,
breadcrumb) rather than interpreting free-form body text."

### D-07 — Flagging over inventing

**Alternative rejected.** Emitting the model's best guess as an enriched value.

**Evidence.** `635d5ba` (2026-04-11) is the earliest commit in which `llm/prompts.py` contains
the string `Do not invent`; `llm/prompts.py` exists at the initial commit `f77080b`
(2026-04-09) without it. `847f92e` (2026-05-14) is the earliest commit in which `README.md`
contains `Never fabricate`.

**Code at this commit.** `README.md:107`: "**Never fabricate data.** If confidence is low,
return the original values and flag for human review." The rule is repeated as an instruction
in every prompt that could invent: `llm/prompts.py:143` ("Do not invent, reformat, abbreviate,
or expand anything."), `:191` ("return null. Do not invent."), `:224`, `:275` ("Do not invent
companies, and never return a company other than…"), `:291` ("Never guess or hallucinate
URLs."), `:480` ("No fabrication of institutions or invented people."), `:507` ("Never guess
from the name alone."). `enrichment/tier3_llm.py:1` states the tier's status: "Tier 3: LLM
inference — last resort, always flagged for review"; the code the tier's writes attract is
`UNVERIFIED_INFERENCE = "unverified-inference"` (`enrichment/flags.py:151`).

**Enforcement beyond the prompt.** The instruction is not the only guard.
`enrichment/tier3_llm.py:33–36` rejects a Tier 3 name suggestion that is address content —
a postal code, a house-number-plus-street-type pattern, or high token overlap with the
record's own street — and `enrichment/name_gate.py` (D-10) recomputes every check from the
record rather than trusting the producing lane.

### D-08 — One flag, rebuilt from the final state, field-scoped

**Alternative rejected.** Each tier appending its own reason as it executes.

**Evidence.** `5e423c2` (2026-08-20, "Fix 8: flag model redesign"): "The review flag answered
'which tier ran?' because each tier appended its own reason as it executed. 47 of 50 demo
records were flagged, so it could not be used to decide what to look at." The same message
records the effect: "Demo batch flag rate 47/50 -> 21/50; 13 reason strings -> 6 codes in use;
9 of 21 flagged records are single-field."

**Code at this commit.** `enrichment/flags.py:9–35` states the three rules the module
enforces: "**Rebuilt, never appended.** :func:`compute_flags` is called once, from
``finalise``, after every name, domain and contact field has settled. Tiers record *evidence*
(the ``_ev_*`` keys below); they never write a flag"; "**Field-scoped.** ``flagged_fields``
names the output fields the flag concerns"; "**Absence of data is not a defect.**" The one
later pass that can invalidate a computed flag has a withdraw-only escape:
`enrichment/flags.py:21–22` — ":func:`retract` is its only recourse, and it can only ever
withdraw — never raise, never re-judge."

### D-09 — Advisory codes do not queue a reviewer; an unverified domain ships

**Alternative rejected.** Any emitted code raising `flag_for_review`; blanking a domain the
ownership guard declined.

**Evidence.** `9da5ae8` (2026-08-27, "Ship unverified domains and split advisory codes out of
the review queue"): "Checked against the batch most of them are the right site — the guard's
failure is usually an absence of corroboration, not evidence of a wrong answer — and blanking
the column made a reviewer rediscover a value the pipeline already had." And: "a contradicted
registry address is usually an entity address against an operating site, and a per-row queue
entry for either spends the reviewer's attention on the ordinary case."

**Code at this commit.** `ADVISORY_CODES` is declared at `enrichment/flags.py:249`;
`flag_for_review` is derived rather than "codes are non-empty" at `enrichment/flags.py:896–907`.
`enrichment/flags.py:39–42`: "A row can therefore ship with ``flag_for_review`` false and a
populated" reason. A related suppression is `0c057bc` (2026-08-28), which withholds specific
G6 and G7 codes from the `/issues` audit column while leaving them detected elsewhere; that
suppression set is deleted by `e7e60cd` (D-43).

### D-10 — One write gate for every name candidate

**Alternative rejected.** A registry name and an LLM name reaching the output field by
different routes with different checks.

**Evidence.** `20b3050` (2026-09-01, "Route every name candidate through one write gate"):
"a rejection on either route vanished silently — the record shipped its input value and the
reviewer was told the canonical form 'could not be established', naming neither the candidate
nor the reason."

**Code at this commit.** `enrichment/name_gate.py:12–14`: "Every candidate for a name field now
passes :func:`evaluate`, whatever produced it. The checks are recomputed here, from the record,
rather than trusted from whatever the producing lane decided." The checks are enumerated at
`:16–23`: the `different` identity verdict, a country contradiction, address-shaped names,
empty answers, and a candidate carrying `entity-superseded` evidence. `:25–28`: "A refusal is
never silent: :class:`GateDecision` carries the candidate and the reason … 'nothing is
discarded' is a property of the gate, not a promise each caller has to keep."

### D-11 — Three identity verdicts, not a boolean

**Alternative rejected.** The boolean `canonical_preserves_identity`.

**Evidence.** `20b3050` (2026-09-01): "`classify_name_change` replaces the boolean
`canonical_preserves_identity` with three verdicts — SAME, UNDECIDABLE, DIFFERENT. The boolean
collapsed UNDECIDABLE into rejection, discarding correct answers for abbreviated records
exactly as it discarded hallucinations. Only DIFFERENT refuses a write."

**Code at this commit.** `utils/name_identity.py` holds `classify_name_change`;
`enrichment/name_gate.py:16–18` names it as "the hallucination wall" for Name 1 and
`enrichment.tier2_canonical.subject_preserved` as its counterpart for the department slots.

**Related.** `cbd2698` (2026-08-20, D-18) removes that guard from the ROR path entirely, on the
ground that it is "right for the LLM canonicalisation paths … It is wrong between a verified
registry match and its own name."

### D-12 — Per-field provenance, enforced, over six scoped fields

**Alternative rejected.** A record-level `tier_used` / `source` / `confidence` triple.

**Evidence.** `59d3e4d` (2026-08-20, "Implement per-field provenance tracking and enhance
evidence handling").

**Code at this commit.** `enrichment/provenance.py:5–7` states the principle: "Every value the
system writes must be attributable after the fact to the source that produced it and the
confidence under which it was produced. A written value whose origin cannot be reconstructed is
not admissible." `:9–15` states why the record-level triple is insufficient: it "cannot
represent a field that was written twice — which is exactly what Fix 2's Tier 1 retry does to
``name1``." Scope is six fields (`SCOPED_FIELDS`), and the exclusion is deliberate
(`:20–24`): "``contact``, ``care_of`` and ``email`` are deliberately excluded" because they
carry personal data. Enforcement is structural (`:28–33`): "the six scoped keys cannot be
assigned. ``record["domain"] = x`` raises :class:`UnattributedWriteError`; the only way a value
reaches one of them is :meth:`EnrichedRecord.write`, which requires a structured
:class:`Evidence` argument."

**Bounded.** `enrichment/provenance.py:35–37`: "This is stateless … nothing here writes to a
database, and nothing here writes to Application Insights — App Insights stays operational
monitoring."

### D-13 — An origin may change only when the value changes

**Alternative rejected.** A lane declaring itself the producer of a value it passed through
unchanged.

**Evidence.** `e31b53b` (2026-09-02, "An origin may change only when the value changes"):
"Gate 2 destroyed provenance. The passthrough declared producer='input', so `_origin_for` moved
`_slot_origin` from `preprocess:street` to `input` and `relocated-unverified` stopped firing on
seven records — five of them dropping out of review entirely — with their Name 2 byte-identical
on both sides. The doubt was never answered; the fact it rested on was destroyed."

**Code at this commit.** The invariant is enforced at the `_write` funnel, and the comparison
that decides "changed" is bounded in the same commit message: "The fold is whitespace and case
only, never `normalize_key`, which folds legal forms."

### D-14 — One domain chokepoint with an ownership guard

**Alternative rejected.** Five direct write sites, and no ownership check at all.

**Evidence.** `4645b33` (2026-08-20, "Fix 1: single domain resolution chokepoint with an
ownership guard"): "the domain path had no ownership check at all, so an unrelated company's
website could be attached to a customer record (delta.com for 'Delta Analytical') and read as
successful enrichment."

**Code at this commit.** `utils/domain_resolver.py` is the single decision site; the acceptance
rule is registry provenance, name similarity at `DOMAIN_NAME_MATCH_THRESHOLD`, a non-generic
email domain on the record, or an on-domain SERP title naming the organisation — otherwise
`domain = None` and the record carries `domain-unverified`. The threshold value and its
`config.py` line are Pass 04's; the commit records it as "82, tuned on the demo batch."
The export change is deliberately schema-neutral: "Same column names, same order — only the
value changes, so the DATAshaper / ADF schema is untouched." D-09 later ships the declined
candidate rather than blanking the column.

### D-15 — One classification authority for `record_type`

**Alternative rejected.** Last-writer-wins across tiers.

**Evidence.** `6038372` (2026-08-20, "Fix 3: single classification authority for record_type"):
"`record_type` was written by whichever tier ran last … So MIT came out `company` because it
holds an LEI, a hospital came out `company` because it took the company branch, and `unknown`
sat on 21 of 50 demo records without anything having decided so." The message also states why
the field is not merely cosmetic: "The field was never purely an output: it gates which tiers
run."

**Code at this commit.** `enrichment/classifier.py`. A Tier 1 retry hit whose registry type
contradicts the record logs `tier1_retry_type_conflict` and leaves the value alone
(`0f884f1`).

### D-16 — Batch consensus propagates fields; it never merges

**Alternative rejected.** Leaving intra-batch divergence for Phase 2 to adjudicate.

**Evidence.** `c4834a5` (2026-08-20, "Fix 6: batch consensus pass"): "it is cheaper than having
Phase 2 adjudicate the divergence later." The boundary is explicit: "It is field propagation,
never a merge — the batch out is the same length and order as the batch in, and Phase 2 remains
the only place entities are merged."

**Code at this commit.** `enrichment/batch_consensus.py`, run from `enrich_batch` after every
record is finalised and before serialisation. Three sub-decisions are recorded in the same
message and are load-bearing:

| Sub-decision | Statement |
|---|---|
| Shared address key | "The address key is `derive_block_id`, reused verbatim from `dedup/signatures.py` so Phase 1 and Phase 2 cannot disagree about what the same address means." |
| Legal form kept separate | "Stripping the form from the key would group 'Delta Analytical Inc' with 'Delta Analytical LLC', which is exactly Phase 2's judgement to make." |
| Keys never leave the pass | "Both key halves are dictionary keys and nothing else: never output, never sent to an API, never in a prompt, never in a scoring path." |

A group holding no registry identity "never chooses between competing values — it fills gaps
only where the group is already unanimous"; a group holding conflicting registry identities
"propagates nothing." An inheriting record keeps its `tier_used`, because "setting it to 1
would inflate the Tier 1 count and corrupt the tier distribution used in evaluation."

### D-17 — Wikidata is a lookup key and a witness, never an authority

**Alternative rejected.** Copying the Wikidata label onto the record as a name.

**Evidence.** `55b9e33` (2026-08-25) adds `enrichment/wikidata.py`.

**Code at this commit.** `enrichment/wikidata.py:1` — "Wikidata crosswalk lane — a pointer and
a witness, never an authority." The two outcomes are separated at `:11–20` and `:21–27`. On the
crosswalk path the lane "does **not** copy Wikidata's label onto the record. It follows the
pointer: it re-queries ROR / GLEIF *by that identifier* … with the registry's provenance
(``ror`` / ``gleif``), all of the registry's own guards applied unchanged, and nothing in the
output naming Wikidata at all." On the witness path "it may never write ``name1_enriched``.
The most it writes is ``operating_name``."

The reason is stated at `:29–36`: "A crowd-edited label is not a customer master's source of
truth for a legal name, and treating it as one would put an unreviewed edit into SAP. Treating
it as a *lookup key* is safe in a way that treating it as a *value* is not: a wrong pointer
resolves to a registry record that then fails the registry's own name and country guards, and
the record misses."

Two further decisions live in the same lane. `:37–41` — "**No LLM is involved anywhere in this
lane.** … Where ambiguity survives the gauntlet the answer is *no match* — never an LLM
tiebreak, because a tiebreak is exactly the judgement a crowd-sourced source has not earned."
`:48–50` — the SPARQL endpoint "is deliberately never used: it is rate-limited, frequently
unavailable, and a query language is a much larger surface than this lane needs." A
disambiguation page is a no-match, not a list to choose from (`:51–53`), and a missing country
is a no-match (`:57–59`).

**⚠ MEASUREMENT.** `wikidata_lane_report.md:99` records that on the 100-row batch it was
measured against, "Nothing was crosswalked, so nothing tested the pointer path in production."
That report is dated at its own commit and is not regenerated at this commit.

### D-18 — A verified Tier 1 match writes the registry's official name

**Alternative rejected.** Applying the LLM-path identity guard between a verified registry
match and the registry's own name.

**Evidence.** `cbd2698` (2026-08-20, "Fix 4: a verified Tier 1 match writes the official
name"): "Row 27 shipped ror.org/03zzw1w08 next to a Name 1 of 'Mayo Clinic FLA'. … The write
was then attempted and suppressed by a second gate: `canonical_preserves_identity()` … That
guard is right for the LLM canonicalisation paths … It is wrong between a verified registry
match and its own name, and is removed from the ROR path only."

**Code at this commit.** Both registries and both passes write through one helper,
`_write_registry_name()`, which marks the field registry-owned; `finalise()` runs the global
abbreviation map over Name 1–4 on every non-registry path and skips a registry-owned name,
"ROR and GLEIF are the authority on their own spelling."

**Guard that makes it safe.** From the same message: "the name1 match is scored directly
against Name 1 and the local rescore requires every distinctive/identifier token of Name 1 to
be covered, so a parent that drops the child's distinguishing tokens cannot reach the
threshold. Local child matching writes Name 2-4 only, never Name 1."

### D-19 — Normalised cache keys, and one Tier 1 re-lookup after canonicalisation

**Alternative rejected.** One cache entry per input spelling; a single Tier 1 attempt taken
before the pipeline knows the organisation's real name.

**Evidence.** `0f884f1` (2026-08-20, "Fix 2: canonical cache keys and Tier 1 re-lookup after
canonicalisation"): "Identical entities produced different output depending on how the input
happened to be spelled."

**Code at this commit.** ROR, LEI and SERP lookups key on `normalize_key(query)` plus country,
"reusing dedup/signatures.py rather than writing a second normaliser." The key is confined:
"The key is a dictionary key and nothing else: the unnormalised string is what reaches the API
and every scoring path, pinned by a test." Country is part of the key "so two same-named orgs
in different countries cannot share an entry." One exception is carved out: "The SERP key keeps
the quoting distinction. `normalize_key` strips quote characters, which would have made an
exact-phrase query and its unquoted retry collide."

`_retry_tier1_after_canonicalisation` runs once per record, at the top of
`_finalise_and_return`, "every guard intact (a retry that fails the country guard is a miss),
nothing written on a miss."

**Limit recorded in the same commit.** "the retry can only fire when a tier actually writes a
changed `name1_enriched`."

### D-20 — Search terms derive only from enriched values

**Alternative rejected.** Falling back to the pre-enrichment input.

**Evidence.** `308f357` (2026-08-20, "Refactor search term derivation to utilize only enriched
values"): search terms are "derived exclusively from enriched values, eliminating reliance on
pre-enrichment input … preventing stale or incorrect information from being used", and are
computed in `finalise` "after all enrichment processes are complete, guaranteeing that only
settled values are considered."

**Code at this commit.** `enrichment/search_terms.py`.

---

## 4. Entity resolution

### D-21 — An LLM adjudicates the name decision

**Alternative rejected.** ⚠ ALTERNATIVE NOT EVIDENCED. No commit in the 154 introduces and then
removes a trained matcher, an embedding index, or a similarity-threshold-only merge. What the
repository evidences is an exclusion, not a reversal.

**Evidence.** `13a1274` (2026-06-17) introduces the adjudicator with an LLM at its centre.
`README.md:2508` excludes the alternative by name: Phase 2 "does **not** do address validation,
embeddings, golden-record election, or file I/O."

**Code at this commit.** The model's role is narrow and its input is pre-constrained.
`dedup/prompts.py:30`: "Every record you receive already shares the same physical address
(country, postal code, street). Address matching is done. Your only job is to decide, from the
names, which records refer to the SAME real-world customer entity." Deterministic components
around it do not use a model at all: `dedup/scoring.py:6–7` ("No LLM, no network — ever"),
`dedup/candidates.py:14–16` ("Everything here is deterministic and pure (no LLM, no network)"),
`dedup/address.py:35` ("Everything here is pure and deterministic: no LLM, no network, no
I/O"), `enrichment/wikidata.py:37` ("No LLM is involved anywhere in this lane").

### D-22 — The LLM sees signatures, not rows

**Alternative rejected.** Presenting the model with raw row pairs.

**Evidence.** `13a1274` (2026-06-17) introduces `dedup/signatures.py` as STEP A.

**Code at this commit.** `dedup/signatures.py:3–5`: "A *signature* is a distinct
``(norm_name1, norm_department)`` key within a block. 100 byte-identical rows collapse to one
signature; the LLM only ever works on distinct signatures, never on raw rows. This is the
blow-up guard." Two sub-decisions follow. The department half reads the whole name block below
Name 1, not Name 2 alone (`:7–10`): "two rows at one address whose units differ only in Name 3
are two departments, and collapsing them would merge records that name different things."
And the key stays internal (`:12–13`): "The normalized key is internal only — it never reaches
the LLM, which always sees the original (un-normalized) names." The normalisation is
deliberately shallow (`:28–30`): "We deliberately do NOT strip legal forms (GmbH, AG, Inc.) or
expand abbreviations here — that is the LLM's job."

### D-23 — Blocking is computed in the service, from delivery points

**Alternative rejected.** Taking the upstream address gate's raw `country|postal|street|house`
hash as the block and merging nothing across it.

**Evidence.** `8868908` (2026-09-04) adds `dedup/address.py` and the v2 block builder.

**Code at this commit.** `dedup/address.py:3–10` states the failure the decision addresses:
"v1 blocks on ``sha1(country | postal_code | street | house_no)`` with each part only case- and
punctuation-folded … Two records at one door therefore fall into different blocks whenever the
address was typed differently … and nothing downstream can merge across blocks. That is the
single largest source of missed duplicates in the stress batch."

The key becomes the delivery point (`:12–16`), with two secondary rules: `K2`
(`country | city | house`) recovers a zip typo, checked pairwise by `address_compatible` which
"refus[es] a pair whose zips differ by more than one edit" (`:19–24`); and the fallback block
(`country | zip5`) is quarantined (`:26–34`): "A row with no usable house number names no
delivery point. It does NOT join a house-bearing block — an address we cannot verify is not
evidence that two records share a door … House-less rows block only with each other, and every
cluster they form is routed to manual review. Their relationship to the verified blocks is a
Link ID … never a Cluster ID."

The upstream gate keeps precedence where it has spoken — `dedup/signatures.py:201–203`:
"A caller-supplied ``block_id`` still wins, exactly as in v1: an upstream address gate that has
already decided the blocks is better evidence than anything re-derived from the columns here."

Order-independence is a property of the construction (`dedup/signatures.py:196–199`):
"Union-find is iterative and driven off sorted keys, so the components — and the block ids
derived from them — do not depend on the order the rows arrived in."

### D-24 — v2 ships behind three default-false flags

**Alternative rejected.** Replacing v1's blocking, Name-2 handling and conflict routing in
place.

**Evidence.** `8868908` (2026-09-04).

**Code at this commit.** `dedup/flags.py:3–11` declares "Three independent switches, all
default-false, each gating one change": `DEDUP_V2_BLOCKING`, `DEDUP_V2_NAME2`,
`DEDUP_V2_ID_CONFLICT`. The contract is stated at `:13–15`: "They must work independently and
together, and with all three off the output must be byte-identical to v1 — which is what
tests/test_dedup_v2_flags_off.py asserts against a recorded run." That test file is present:

```
$ ls tests | grep -i v2_flags
test_dedup_v2_flags_off.py
```

Two supporting choices: a typo fails safe (`dedup/flags.py:27–29` — "Anything else — including
'0', 'no', 'off', the empty string and an unset variable — means off"), and the flags are read
from the environment on every call rather than captured at import (`:17–20`), so a process that
imported the module before a test set the variable is not stuck with the import-time value.
The output contract is gated too: `v2_any()` (`:55–62`) governs the `Link ID` column, "so the
workbook has exactly v1's columns" with every flag off. The dispatch site is
`dedup/signatures.py:264` (`return _v2_blocks(rows) if v2_blocking() else _v1_blocks(rows)`)
and `:284–292` for the Name-2 path.

### D-25 — `Link ID` is a third outcome; an ID conflict routes to review

**Alternative rejected.** Reporting a family as either a merge or as unique; exploding an
ROR/LEI conflict into singletons.

**Evidence.** `60e0b51` (2026-09-05, "A link is not a merge, and an id conflict is not a reason
to lose the pair"): "Two records can be one organisation and two records. The output had no way
to say so: a Cluster ID, or nothing. So a company and its research institute, a parent and its
subsidiary, a university and the LLC that runs its warehouse were each reported either as
duplicates — overstating — or as unique, which loses the finding entirely."

**Code at this commit.** `dedup/cluster_key.py:12–13` declares the two prefixes
(`CLUSTER_ID_PREFIX = "c_"`, `LINK_ID_PREFIX = "l_"`) and `:30–33` states why they differ: "a
reader glancing at a cell must be able to tell a 'these are the same record' id from a 'these
are the same organisation' id without consulting a legend." Both are `sha256` over the sorted
member `row_ids`, truncated to 12 hex characters, which makes them stable across runs
(`:20–21`): "Same membership -> same id across runs, machines, and input orderings."

Independence from the merge is deliberate (`60e0b51`): "it is computed independently of the
merge, because deriving it from the clustering would make it say nothing the cluster does not
already say — the pairs worth linking are exactly the ones that did NOT merge." Families span
blocks: "An institution family is not a property of one delivery point." On the conflict:
"either the records are the same and the split is wrong, or they are different and the ids did
their job, and in both cases what a steward needs is the pair with both ids named."

The same commit records three order-dependencies closed to make the ids reproducible, including
that "the cross-block union-find was keyed on signature_id, which restarts at s1 in every
block, so the whole file came out carrying one Link ID."

### D-26 — Residue nomination is candidacy only

**Alternative rejected.** Letting a similarity score merge a pair, or leaving the residue
un-adjudicated.

**Evidence.** `929492b` (2026-07-23, "Implement residue candidate nomination and adjudication
process"); documented in `8d07acb` (2026-08-03).

**Code at this commit.** `dedup/candidates.py:3–7` names the gap: "What they never compare are
pairs the deterministic Name-2 asymmetry rule keeps apart … Those pairs bypass the LLM entirely
and default to ``unique`` with no reasoning." The boundary is at `:9–12`: "This module
NOMINATES such residue pairs for LLM adjudication when there is a same-entity signal …
Nomination is candidacy ONLY: it never merges. The LLM verdict and the two-level identity rule
still decide." Determinism is required for a stable call sequence (`:14–16`): "the same units in
any order yield the same candidate list, so the LLM call sequence is stable."

Legal suffixes are held as data and only ever used for candidate similarity
(`dedup/candidates.py:26–29`): "Stripped only for candidate-similarity computation — never from
the canonical signature itself."

### D-27 — Ambiguity routes to review; nothing is guessed

**Alternative rejected.** Choosing a default verdict when the model is unclear.

**Evidence.** `13a1274` (2026-06-17).

**Code at this commit.** `dedup/adjudicator.py:197`: "into singletons and flag each for human
review; we never guess a safe" [default]. `dedup/adjudicator.py:354`: "verdict is ambiguous,
route the whole block to manual_review, never guess". `60e0b51` narrows what the routing means:
"manual_review now means a contradiction or an admitted uncertainty, never 'we did not look'."

### D-28 — Adjudicator calls are recorded and replayed; replay refuses on a miss

**Alternative rejected.** A fresh model call on every run.

**Evidence.** `ec5efab` (2026-09-05, "Record and replay adjudicator calls, so a real-model
number can be checked"): "every run made fresh model calls … Every v2 number so far came from a
test double, and the one measurement that matters — what the deployed model actually does with
the new prompt — was unrepeatable. A figure in a report that nobody, including its author, can
reproduce next week is not evidence."

**Code at this commit.** `dedup/cache.py` is "a content-addressed store keyed on everything
that decides the answer: deployment, both prompts, the token budget, and the sampling
parameters actually in force." It is "Off unless DEDUP_FIXTURE_CACHE_DIR is set." The refusal
is the point: "Replay mode REFUSES on a miss rather than quietly calling the model, because a
miss means the prompt changed and the new answers would otherwise be reported under the old
run's name." Prompts are stored beside each answer: "a cache entry that is only a hash is one
nobody can review." The committed cache is recorded as "38 calls over the 200-row stress
batch."

### D-29 — `temperature=0.0` where the deployment allows it; `seed` deliberately not added

**Alternative rejected.** Sending `temperature` and `reasoning_effort` together
unconditionally.

**Evidence.** `3527585` (2026-08-18, "3.2 Send temperature=0.0 on dedup calls where the
deployment allows it"). The commit pastes the measurement rather than assuming it:

```
temperature=0.0 alone                  -> 200
reasoning_effort=low alone             -> 200
temperature=0.0 + reasoning_effort=low -> 400 Unsupported value:
    'temperature' does not support 0.0 with this model. Only the default
    (1) value is supported.
```

**Code at this commit.** `dedup/llm.py:9–16`: "``temperature=0.0`` is sent when
``reasoning_effort`` is not in play, matching the Phase 1 enrichment path so both phases make
the same reproducibility claim wherever the deployment permits it. On a reasoning deployment the
two are mutually exclusive … so on such a deployment reasoning_effort wins and temperature is
not sent."

**The claim the decision does not support.** From the same commit: "On the default config
(reasoning_effort=low) temperature is therefore not sent and dedup output is not
bit-reproducible — the honest position." And where temperature does apply, "10 live runs of a
borderline block against a matched control show it narrows but does not remove variability:
distinct confidence vectors 8 -> 3, distinct routing vectors 2 -> 1, distinct reasoning strings
28 -> 25." `seed` "IS accepted alongside reasoning_effort on this deployment and is the stronger
control; deliberately not added here."

### D-30 — The prompt version is a data column, and v2 carries its own

**Alternative rejected.** One version string carried across prompt revisions.

**Evidence.** `8868908` (2026-09-04) introduces the v2 prompt builders and the second version
constant.

**Code at this commit.** `dedup/prompts.py:14` — `PROMPT_VERSION = "p2-dedup-v3"`;
`dedup/prompts.py:22` — `PROMPT_VERSION_V2 = "p2-dedup-v8"`. The reason is at `:16–21`: "A
different prompt is a different experiment, so it gets its own version … Any output produced
under one of these is not comparable with output produced under the other, and the version
column is how a reader tells which they are holding." Selection is at `dedup/prompts.py:122`
(`return PROMPT_VERSION_V2 if v2_name2() else PROMPT_VERSION`), and `:12–13` states the bump
rule: "Bumped whenever the prompt wording changes in a way that could shift decisions. Logged
per LLM call and emitted in every result row."

---

## 5. Scoring, election and grain

### D-31 — Election is deterministic arithmetic, separate from adjudication

**Alternative rejected.** Electing a golden record inside the LLM adjudication pass.

**Evidence.** `611c348` (2026-07-11, "Phase 2 thesis") adds `dedup/scoring.py`.

**Code at this commit.** `dedup/scoring.py:3–7`: "Separate from the LLM adjudicator on purpose:
clustering and election have different inputs, cadences, and cost profiles. Election is pure
arithmetic over an editable weights table (``dedup/weights.json``), so it can be re-run on
retuned weights without paying for LLM adjudication again. No LLM, no network — ever."
`WEIGHTS_PATH` is `dedup/weights.json` (`dedup/scoring.py`, module constants). Weight values
and the file's last-change commit belong to Pass 04.

### D-32 — Scoring is permissive; one hard error

**Alternative rejected.** Rejecting rows the extract cannot supply cleanly.

**Evidence.** `611c348` (2026-07-11).

**Code at this commit.** `dedup/scoring.py:9–13`: "The real CRM extract is ~half empty and
dirty. Scoring is therefore permissive: a missing or unrecognised value scores 0 (with a warning
when the value was present but unrecognised) and NEVER raises or fails the batch. The one hard
error is a duplicated row_id in a single request — that means a broken upstream join, and
scoring it would double-elect." The coercion helpers carry the same rule
(`dedup/scoring.py:695`): "Coercion helpers — permissive, never guess, never raise."
`dedup/consolidate.py:22–24` applies the identical rule at the consolidation stage: "a blank
Customer is counted as an error and the row is passed through untouched with both columns
empty — never a raised exception, never a dropped row."

**Cost of the decision, evidenced.** `f5c8d8d` (2026-09-05) records what permissiveness hides
when a warning is `exclude=True`: "the coercion warning is exclude=True, so it reached neither
the workbook, the JSON body nor the summary." See D-35.

### D-33 — Confidence demotion, and approval as a separate step

**Alternative rejected.** Merging on the adjudicator's verdict alone.

**Evidence.** `efe1379` (2026-07-22): "Updated the scoring logic to demote merges to
`manual_review` if the confidence is below the threshold, ensuring human verification for
uncertain cases", with `CONFIDENCE_MERGE_THRESHOLD` "defaulting to 0.95", and a new
`/api/dedup/approve` endpoint "to record human approval decisions on proposed clusters."

**Code at this commit.** `api/routes.py:1487` (`/api/dedup/approve`) and
`dedup/scoring.py:605–617` (`apply_approval`). The downstream contract is stated at
`api/routes.py:1495–1496`: "Phase 3 consumes ONLY rows with approval_status='approved' or
election_status='unique'." Scope of the four-eyes control is entity resolution only: no
approval gate exists on any enrichment write path — `/enrich` (`api/routes.py:106`) writes back
through `usp_MergeLegacyEnriched` with no approval column in the payload
(`sql/usp_merge_legacy_enriched.sql:46–79`).

**⚠ NOT PERSISTED.** `api/routes.py:1494–1495`: "Persistence is intentionally out of scope — a
durable approval store is a future step."

### D-34 — Count points only for the cluster's most recent year

**Alternative rejected.** Awarding count points on any row regardless of recency.

**Evidence.** `c18921d` (2026-07-23, "Refactor scoring logic to align with Bernd's year-priority
rule"): "Updated scoring functions to ensure count points are awarded only when the row's
last-used year is the most recent in its cluster, preventing older records from outscoring more
recent ones." Fields were renamed to carry the constraint in their names —
`order_count` → `orders_in_last_used_year`, `partner_order_count` →
`partner_orders_in_last_used_year`. `994fb3b` (2026-07-23) removes a false recency-suppression
warning raised by the same rule.

**Code at this commit.** `dedup/scoring.py`, the year-maxima rule; exact formula and weights
are Pass 04's and Pass 14's.

### D-35 — Dates coerce to years only where a year is meant

**Alternative rejected.** Extracting `.year` from any date value anywhere.

**Evidence.** `f5c8d8d` (2026-09-05, "Dates are not years: four sales-order rules scored 0 on
every record"): "`_coerce_int` now takes an explicit allow_date, passed True only for the two
`*_last_used` fields. Extracting .year from any date anywhere is smaller code and the wrong
behaviour: the count columns are plain integers, so a date in Equipment_Total_Count is a broken
join, and reading it as ~2026 would hit the >15 band and award 30 points. Trading a silent zero
for a silent thirty is not a fix."

**Code at this commit.** `dedup/scoring.py`, `_coerce_int(..., allow_date=...)`.

**Measurement in the same commit.** On `US_Qlic report data_2026-07-30.xlsx` (22,224 rows,
20,025 distinct customers): the file route refused the workbook outright because the report
spells the column `Customer No.`, and bypassing that, "15,369 of 22,224 rows raised
ValidationError. Only 6,855 were scoreable at all."

### D-36 — Grain consolidation is a column append

**Alternative rejected.** Filtering the extract down to one row per customer.

**Evidence.** `6cf4639` (2026-09-05, "Implement customer grain consolidation endpoints for SAP
extracts").

**Code at this commit.** `dedup/consolidate.py:1–6` states the grain: "The SAP customer extract
arrives at ROW grain: one row per customer per company-code / sales-area assignment, so a single
customer occupies 1 to 68 rows. Every master-data field is byte-identical across a customer's
rows; only the sales-area fields vary." `:12–15` states the failure of the alternative: it
"keeps ONE of a customer's company codes and, when the surviving row happened to be a sales-area
row with a blank company code, keeps none at all." The decision is at `:17–20`: "This stage is a
COLUMN-APPEND, not a collapse: rows in == rows out, in the same order, with two strings computed
per customer and written onto every row of that customer. Collapsing the extract to one row per
customer is a separate, downstream concern and deliberately not done here."

Two supporting choices: the header matcher is imported rather than re-spelled
(`dedup/consolidate.py:35–38`) so "the writer here and the reader downstream … must never
disagree about which spelling of a header is 'Company Code'"; and the delimiter is one constant
(`dedup/consolidate.py:45` — `CONSOLIDATED_DELIMITER = ","`) with the reader accepting both
forms (`:42–44`): "``split_consolidated`` in dedup/scoring.py reads both ',' and ';' so
reversing this decision means changing this line and nothing else."

**Endpoints.** `api/routes.py:960` (`/api/preprocess/consolidate`) and `:1026`
(`/api/preprocess/consolidate/file`) are both present. Neither is called by any exported ADF
pipeline.

### D-37 — ZFIS is out of scope for dedup

**Alternative rejected.** Re-checking the upstream gate inside scoring.

**Evidence.** `611c348` (2026-07-11).

**Code at this commit.** `dedup/scoring.py:15–16`: "ZFIS is deliberately absent: it is a
separate upstream gate that runs before enrichment; those records never reach dedup."

---

## 6. Merge-back and SQL

### D-38 — `THROW`, not `RAISERROR`

**Alternative rejected.** ⚠ ALTERNATIVE NOT EVIDENCED. No commit in the 154 contains
`RAISERROR` in any `sql/` file; the string appears in the repository only in
`docs/thesis-doc-prompt-v2.md:153` and in `docs/thesis/02_ARCHITECTURE.md:415`. The decision is
readable from the code as a uniform choice, not from a diff that replaced one form with the
other.

**Evidence.** `5a1fd70` (2026-09-07) introduces all guards, in `THROW` form, in one commit.

**Code at this commit.**

```
$ grep -rc "RAISERROR" sql/*.sql
sql/usp_merge_legacy_enriched.sql:0
sql/usp_merge_validation_scores.sql:0
sql/usp_merge_legacy_issues.sql:0
sql/usp_merge_validation_clusters.sql:0

$ grep -c "THROW" sql/*.sql
sql/usp_merge_legacy_enriched.sql:3
sql/usp_merge_legacy_issues.sql:4
sql/usp_merge_validation_scores.sql:3
sql/usp_merge_validation_clusters.sql:3
```

The error numbers are a stable vocabulary across all four procedures: `50000` target column
(`usp_merge_legacy_issues.sql:16`, that procedure only), `50001` entity schema missing
(`usp_merge_legacy_enriched.sql:16`, `usp_merge_legacy_issues.sql:25`,
`usp_merge_validation_clusters.sql:20`, `usp_merge_validation_scores.sql:20`), `50002` group
code missing (`:24`, `:33`, `:28`, `:28`), `50003` group code matches no row (`:36`, `:45`,
`:40`, `:40`).

**Consequence.** `THROW` re-raises with severity 16 and does not permit the message-formatting
arguments `RAISERROR` accepts; the procedures build their message text into `@msg NVARCHAR(400)`
first (`usp_merge_legacy_enriched.sql:8`, `:15`, `:35`).

### D-39 — Update-only merge; no `INSERT` branch

**Alternative rejected.** Upserting a customer the target table does not hold.

**Evidence.** `5a1fd70` (2026-09-07); the pre-parameterisation form at `ecac51a` is likewise
`WHEN MATCHED` only.

**Code at this commit.**

```
$ grep -c "WHEN NOT MATCHED" sql/*.sql
sql/usp_merge_legacy_enriched.sql:0
sql/usp_merge_validation_scores.sql:0
sql/usp_merge_validation_clusters.sql:0
sql/usp_merge_legacy_issues.sql:0
```

Each merge is keyed on the customer identifier **and** the group-code prefix — so a payload row
outside the requested group matches nothing and writes nothing:

| Procedure | Line | Match |
|---|---|---|
| `usp_merge_legacy_enriched.sql` | 86 | `ON tgt.Customer = src.Customer AND tgt.[code] LIKE @pat ESCAPE @esc` |
| `usp_merge_legacy_issues.sql` | 66 | `ON tgt.[Customer] = src.[record_id] AND tgt.[code] LIKE @pat ESCAPE @esc` |
| `usp_merge_validation_clusters.sql` | 63 | `ON tgt.Customer = src.row_id AND tgt.[code] LIKE @pat ESCAPE @esc` |
| `usp_merge_validation_scores.sql` | 78 | `ON tgt.Customer = src.Customer AND tgt.[code] LIKE @pat ESCAPE @esc` |

This makes every procedure idempotent: re-running the same payload writes the same values.

### D-40 — An empty enrichment value never blanks a populated column

**Alternative rejected.** A straight `tgt.[col] = src.[col]` assignment for every column.

**Evidence.** `ecac51a` (2026-08-19) already carries the pattern; `5a1fd70` (2026-09-07)
preserves it through the rewrite, substituting `SPACE(0)` for the quoted empty string so the
dynamic text contains no nested quotes (`sql/usp_merge_legacy_enriched.sql:81–84`).

**Code at this commit.** `sql/usp_merge_legacy_enriched.sql:86` writes 26 text columns as

```sql
tgt.[Name 1] = COALESCE(NULLIF(LTRIM(RTRIM(src.[Name 1])), SPACE(0)), tgt.[Name 1])
```

The rule is not uniform, and the exception is the decision's other half. Five columns on the
same line are assigned unconditionally — `Record Type`, `ROR ID`, `LEI ID`, `Flag for Review`,
`Flag Reason` — because each is a *verdict* the run computed, and a blank verdict is a fact:
a record that no longer carries a registry identifier must lose the one it held, and a cleared
flag must clear. `usp_merge_validation_clusters.sql:63` and `usp_merge_validation_scores.sql:78`
assign every column unconditionally for the same reason: a cluster and a score are whole-run
outputs, not field-level enrichments.

### D-41 — Dynamic SQL splices the schema name only

**Alternative rejected.** Concatenating the group code, or any payload value, into the
statement text.

**Evidence.** `5a1fd70` (2026-09-07).

**Code at this commit.** `sql/usp_merge_legacy_enriched.sql:81–83`: "Dynamic SQL: only the
schema name is spliced in. SPACE(0) is used instead of a quoted empty string so the dynamic
text contains no nested quotes." The schema name passes through `QUOTENAME` before splicing
(`:29` — `DECLARE @tgt NVARCHAR(300) = N'dp_legacy.' + QUOTENAME(@chrEntity) + N'.Legacy'`), and
its existence is checked first (`:13`). The pattern and escape character are passed as
parameters, never concatenated (`:87`):

```sql
    EXEC sp_executesql @sql, N'@pat NVARCHAR(60), @esc NCHAR(1)', @pat = @pat, @esc = @esc;
```

The payload itself never enters dynamic text at all: it is shredded by static `OPENJSON` into
`#src` first (`:42–46`).

**One residual splice.** `usp_merge_legacy_issues.sql:66` splices
`QUOTENAME(@target_column)` into the `SET` clause. It is guarded by the allow-list at `:14–17`
(D-02), so the value can only be one of two literals.

**⚠ UNRESOLVED IN SOURCE.** `usp_merge_validation_clusters.sql:8` and
`usp_merge_validation_scores.sql:8` both carry `DECLARE @db SYSNAME = N'dp_validation';
-- <<< confirm`. The validation database name is a hardcoded literal with an open confirmation
marker in the committed source.

---

## 7. Process discipline

### D-42 — Behaviour changes ship additively

**Alternative rejected.** Changing behaviour in place and relying on review to catch
regressions.

**Evidence.** `8868908` (2026-09-04) is the clearest instance; the discipline recurs across the
history.

**Code at this commit.** The pattern has three parts, each observable:

| Part | Instance |
|---|---|
| Default-false switch | `dedup/flags.py:29`, `:31–33` — three flags, `_TRUTHY` membership, unset means off |
| Byte-identical fallback test | `tests/test_dedup_v2_flags_off.py` (D-24) |
| A single-line reversal point | `dedup/consolidate.py:42–44` — "reversing this decision means changing this line and nothing else" |

Other reversal points named in the history: `DOMAIN_OWNERSHIP_GUARD_ENABLED` (default true,
`4645b33`); `DEPT_SPLIT_CANONICALISES` (`e31b53b` — "`DEPT_SPLIT_CANONICALISES=false` reverses
it"); `DEDUP_FIXTURE_CACHE_DIR` (`ec5efab` — "Off unless … is set"); `CACHE_FROZEN`
(`config.py`, Pass 06's citation).

Dead code is left in place with its test rather than deleted where a decision is still open —
`963125a` (2026-09-07): "`provenance_is_low` is now unused in production and is left in place
with its test, pending a decision."

### D-43 — A comparison reads `raised` and `remedy`, not the group

**Alternative rejected.** Segmenting a before/after comparison by `REDUCIBLE_GROUPS` and
`PERSISTENT_GROUP`.

**Evidence.** `e7e60cd` (2026-09-07, "Issue catalogue redesign, steps A-C"): "Two columns on
IssueDefinition say what a before/after run should do with a code: `raised` (raw / enriched /
both) and `remedy` (rule / enrichment / steward). The group says what KIND of defect a code is,
and the two questions had come apart — G2 holds G2-VAL-007, which a rule fixes, beside
G2-VAL-001, which only a steward can. REDUCIBLE_GROUPS and PERSISTENT_GROUP are deleted;
segment() reads the two new columns and nothing else."

**Code at this commit.** `enrichment/issue_detection.py`, `IssueDefinition.raised` and
`.remedy`; `segment()`. Group membership survives as a statement about the kind of defect, not
as a reduction predicate.

### D-44 — A retired code is withdrawn in place

**Alternative rejected.** Deleting the entry, or reusing the number.

**Evidence.** `e7e60cd` and `5ab6241` (2026-09-07) withdraw and re-home codes;
`3ed371b` (2026-09-07) withdraws `G1-NAME-001`. `e7e60cd` states the renumber rule and its
exception: "old G7 -> G6 (Enriched — Confirm), old G8 -> G7 (Left Unchanged — Verify).
G7-VERIFY-001 keeps its identifier and stays withdrawn, so today's G7 is not that code."

**Code at this commit.**

```
$ python3 -c "import sys,collections; sys.path.insert(0,'.'); from enrichment.issue_detection import ISSUE_CATALOGUE; c=collections.Counter(d.status for d in ISSUE_CATALOGUE.values()); print('declared:',len(ISSUE_CATALOGUE)); print(dict(c))"
declared: 43
{'live': 33, 'withdrawn': 10}
```

`enrichment/issue_detection.py:219` declares the status vocabulary
(`Status = Literal["live", "withdrawn", "ndd", "unlisted"]`) and `:56–61` lists the ten
withdrawn codes with the date each was withdrawn: "All are declared here for the audit trail;
each carries its ``reason``." The invariant that makes the audit trail meaningful is at `:54–55`:
"nothing withdrawn is reachable and nothing live is unreachable."

`5ab6241` records the replacement rule when a code is dissolved: "The four flags that pointed at
the dissolved G6-RESOLVE-001 now name the code for the defect each actually describes … 'Which
defect' is what a reviewer needed from the code; a shared 'could not resolve' bucket never said
it."

---

## 8. Decisions whose rejected alternative is not evidenced in this repository

Rule 4 forbids asserting what the repository does not show. Two entries in the master table
rest on an exclusion in the code rather than on a diff:

| # | Decision | What the repository shows | What it does not show |
|---|----------|---------------------------|-----------------------|
| D-21 | LLM adjudication rather than a trained matcher | An LLM adjudicator from `13a1274` onward; `README.md:2508` excluding embeddings by name; every deterministic module stating that it uses no model | No commit introducing, evaluating, or removing a trained matcher, an embedding index, or a threshold-only merge. No recorded comparison of the two approaches. |
| D-38 | `THROW` rather than `RAISERROR` | `THROW` in all four procedures, `RAISERROR` in none | No commit in which a `sql/` file contained `RAISERROR`. The uniformity is evidence of a convention; the rejection is not separately recorded. |

⚠ MEASUREMENT REQUIRED for D-21: a comparison of the LLM adjudicator against a deterministic
baseline on the stress set would be produced by a harness under `tools/`; none exists at this
commit (`tools/` holds `build_dedup_v2_fixture.py`, `dedup_v2_real_model_run.py`,
`provenance_invariance.py`, `run_diff.py`, `shuffle_evidence.py`).

---

## 9. Decisions carried in commit messages that name a person

Where a rule is attributed to a named stakeholder in the history, the attribution is recorded
here and nowhere else in this pass:

| Decision | Attribution | Commit | Date |
|---|---|---|---|
| Count points only for the cluster's most recent last-used year (D-34) | "Bernd's year-priority rule" | `c18921d` | 2026-07-23 |

No other commit in the 154 attributes a rule to a named person.

---

Pass 09 complete: 44 decisions recorded with evidence commits and file:line citations, of which
two (D-21 LLM vs trained matcher, D-38 `THROW` vs `RAISERROR`) rest on an exclusion in the code
rather than a diff and are marked `⚠ ALTERNATIVE NOT EVIDENCED`, and three further `⚠` findings
are raised for Pass 08 (ADF stored-proc activities pass only `payload` so `@chrEntity` /
`@chrGroupCode` / `@target_column` are never supplied; `adf/enrichment_pipeline.json` names
`Mapping.usp_merge_legacy_enriched` against the defined `[Mapping].[usp_MergeLegacyEnriched]`;
`@db SYSNAME = N'dp_validation'` carries an unresolved `-- <<< confirm` marker in both
validation procedures).
