Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 12

# Pass 12 — Design rationale

Scope and standing
------------------

This document records why the pipeline is built the way it is. Seven design choices are
stated, each under a fixed four-part shape:

| Part | What it states |
|---|---|
| **Problem** | The failure the choice exists to prevent. |
| **Mechanism** | What the code does, cited. |
| **Guard** | The line that makes the mechanism hold when a caller does not co-operate. |
| **Does not solve** | The residue the choice leaves, stated rather than implied. |

**This file outranks `03_ALGORITHMS.md` on interpretation.** Where Pass 03 describes a
procedure and this document describes what the procedure is for, this document governs; where
the two disagree about a procedural fact, the citation wins and the disagreement belongs in
`08_GAPS.md`.

Pass order: Passes 00–11 are generated at this same commit and this document is written
against them. Where a fact is already established and cited by an earlier pass, it is
cross-referenced by its identifier (`08_GAPS.md` G-numbers, `02_ARCHITECTURE.md` ⚠-numbers)
rather than re-derived, and the figure that draws it is named.

Tree state at generation: `git status --porcelain` is non-empty. Every entry is a
`docs/thesis/` output of this same regeneration run — fifteen modified files (the pass
documents 00–09, 11 and 12, plus the figure index) and twenty regenerated `.mmd` files
replacing the previous eight. No source, SQL, ADF or configuration file is modified, so every
citation below reads the tree as committed at `86d173b`.

---

## §1 · The constrained reader

**Problem.** The terminal enrichment lane used to ask a model what it remembered about a name.
`enrichment/grounded_resolver.py:4–8`: "That is the one kind of answer this pipeline cannot
audit: there is no page to open, no identifier to check, and a confident recollection is
indistinguishable from a correct one." The concrete failure is recorded at `:657–663`: two rows
sharing an address and differing only in the case of their input produced "Redding VA Clinic",
which the fetched evidence states, and "Veterans Affairs Medical Center Redding", which it does
not — "the model assembled it from 'Veterans Affairs' and the place name, and it named an
institution that does not exist."

**Mechanism.** The model is given a numbered evidence set and is asked to read, not to recall.

| Step | Code | Constant / rule |
|---|---|---|
| Gather | `enrichment/grounded_resolver.py:361` (`_gather_evidence`) | `MAX_FETCHES = 3` (`:83`), `NUM_RESULTS = 5` (`:88`) |
| Render | `:131` (`EvidenceItem.render`) | URL path, title tag, H1, breadcrumb only |
| Ask | `:596` via `llm/prompts.py:552–556`, `:558–608` | "EVIDENCE (numbered; the ONLY material you may use)" (`llm/prompts.py:565`) |
| Point | `:316` (`_index`), prompt rule 2 (`llm/prompts.py:585–587`) | `evidence_index` per field; out of range → `None` |
| Re-verify | `:408` (`_re_verify`) | The model's canonical name is sent back to ROR / GLEIF |

Page body text is withheld by construction. `enrichment/grounded_resolver.py:138–143`: "The
body text is deliberately not offered: it is where marketing copy, unrelated news items and
other organisations' names live, and a model given it starts sourcing claims to prose rather
than to the page's own structural statement of what it is." The same principle is stated as a
design principle in `README.md:109`.

The lane's three outcomes are ordered by what a reviewer can act on
(`enrichment/grounded_resolver.py:18–31`): a registry hit, where "the registry authored the
final name and supplied the id" and provenance says `ror`/`gleif`; an evidence-backed value,
where "a reviewer has a URL"; and a model-sourced value, "flagged, and the record stays
`unresolved`".

**Guard.** The instruction is not trusted; containment is recomputed.
`enrichment/grounded_resolver.py:283–298` (`_appears_in`) requires the proposal to appear
verbatim in the rendered evidence after casefolding, punctuation folding and whitespace
collapse (`:266–281`), and the docstring states why a ratio is refused: "'Veterans Affairs
Medical Center Redding' scores well against evidence holding 'Veterans Affairs' and 'Redding VA
Clinic' while naming an institution that appears in neither." A failing proposal is dropped
with reason `not_in_evidence` and kept as a suggestion (`:665–669`).

The guards run **before** the registry lookup, not after. `:641–645`: "a proposal the identity
guard refuses must not be used as a REGISTRY QUERY either. Sending 'Liberty Science Center' to
ROR on behalf of a record that said 'Liberty Health Sciences' is how a wrong entity acquires a
real identifier, which is the one outcome worse than not resolving."

A model may never author a `verified` value, whatever it read. `enrichment/confidence.py:103`
puts `llm` in `NON_CORROBORATING_WITNESSES`, and `:209–214` states hard rule 1: "``llm`` as
source or witness can never produce or contribute to ``verified``." On the registry path the
model's contribution is recorded as an earlier event on the same field rather than as a
witness (`enrichment/grounded_resolver.py:21–25`).

**Does not solve.** The query is still the model's and the lane's:
`enrichment/grounded_resolver.py:329` (`build_query`) composes it from the record, and the
evidence set is whatever the search engine returned within three fetches and five results —
containment proves the answer was copied from that set, not that the set contains the right
organisation. When the search returns nothing, every fetch fails, or the LLM call fails, the
record degrades to `run_tier3` (`enrichment/orchestrator.py:9167–9174`), which is recall with
no page behind it: `enrichment/tier3_llm.py:1` — "Tier 3: LLM inference — last resort, always
flagged for review." The constrained reader therefore bounds what the pipeline *writes without
a flag*; it does not remove unevidenced values from the output.

---

## §2 · Tier ladder ordering and cost

**Problem.** A model asked first, and checked afterwards, pays an LLM call on every record and
makes deterministic evidence a reviewer of the model rather than the source of the answer.
`README.md:93`: the ladder exists to "start with the cheapest, most reliable method and
escalate only when cheaper methods fail." `README.md:108`: "**Deterministic before
probabilistic.** Regex-based preprocessing runs before any API or LLM call."

**Mechanism.** One function runs the ladder for one record —
`enrichment/orchestrator.py:7719` (`_enrich_single`) — and each stage may terminate it. The
executed order is figure 11 (`fig-11-phase1-stage-order.mmd`); the call-level counterpart, in
which the edges do not imply sequence, is figure 8 (`fig-08-enrich-tier-ladder.mmd`).

| Order | Stage | Site | Cost at this commit |
|---|---|---|---|
| 0 | Name-overflow check (UC 0) | `enrichment/orchestrator.py:7747` | 1 LLM call per adjacent name pair |
| 1 | Preprocess (UC 6/7/8/9) | `:7821` | Deterministic; no network |
| 2 | Person-only Name 1 affiliation | `:8040` | SERP + LLM, gated on a person-shaped Name 1 |
| 3 | Tier 1 ROR | `:8068` | Free registry API, cached by `(name, country)` |
| 4 | Tier 1 GLEIF/LEI | `:8262`, `:8402` | Free registry API; completion budget `[:5]` (`enrichment/tier1_lei.py:735–736`) |
| 5 | Wikidata crosswalk | `:8342`, `:8411` | Two API calls per record (`enrichment/wikidata.py:70–83`) |
| 6 | AP short-circuit | `:8615` | Returns immediately; preprocessing already settled the record |
| 7 | UC 13 lab → parent department | `:8642` | SERP + fetch + LLM |
| 8 | Tier 2 canonical (UC 5) | `:8785`, call at `:8864` | LLM only — "Zero SerpAPI calls" (`:8788`) |
| 9 | Tier 2A contact lookup | `:9022`, call at `:9041` | "1 SerpAPI call, gated" (`:9022`) |
| 10 | Grounded resolver | `:9125`, call at `:9141` | 1–2 SERP calls, ≤3 fetches, 1 LLM call, then registry re-verification |
| 10b | Tier 3 (degraded mode only) | `:9174` | 1 LLM call, no evidence |

Escalation is conditional, and the conditions are computed before the lane runs. Tier 2A's
gate is `can_do_contact_lookup` (`:8778–8783`): a research institution, exactly one contact, and
a known institution domain. It is deliberately computed early — `:8771–8777`: "This must be
known BEFORE the canonical short-circuit below, because that short-circuit returns for every
record with a populated Name 2 — which is exactly the population Tier 2A verification mode
needs to see."

Cost is accounted rather than assumed. Every real request calls
`utils/cache.py:563–571` (`note_network_call`), and the batch summary reports
`evidence_network_calls` against `evidence_cache_hits` (`:527–534`). Within a batch the cache
is shared, so "two records naming the same organisation cost one fetch"
(`utils/cache.py:235`; see also `enrichment/orchestrator.py:4163`).

**Guard.** Ordering is enforced by the call order of one function plus early returns, not by a
scheduler: `enrichment/orchestrator.py:8640` (`return await self._finalise_and_return(...)`)
ends the AP branch, and `:5663` (`_return_canonical_short_circuit`) ends the canonical branch.
Both readers of "did canonicalisation establish anything" call the same predicate,
`_canonical_short_circuit_enriched` (`:890–905`), so "a gate that decided 'this would be a
passthrough' by any other test could disagree with the branch that then labels it."

**Does not solve.** Three facts about the ladder as shipped:

1. **Tier 2B is not in it** (`08_GAPS.md` G-18). `enrichment/tier2b_dept.py:48` defines
   `run_tier2b` and the only importer in the repository is `tests/test_tier2b.py:13`.
   `tier2_mode` is written at exactly one site — `enrichment/orchestrator.py:3854`, from
   `Tier2AResult.mode` ∈ {`"2A_population"`, `"2A_verification"`} (`enrichment/tier2a_contact.py:90`)
   — so the counter at `:9304–9305` is structurally zero. Two documents still name Tier 2B as
   live: `README.md:102`, and the pipeline's own comment at
   `enrichment/orchestrator.py:8765` — "the record falls through to tier 2 canonical / 2A / 2B
   / 3, any of which may settle Name 2".
2. **Tier 3 is no longer a tier.** It is the grounded lane's degraded mode
   (`enrichment/orchestrator.py:9125–9135`), reached only when the search or the fetches or the
   LLM call fail.
3. **The ladder does not cap per-record spend.** A record can pay UC 0, an affiliation lookup,
   two registry lanes, Wikidata, Tier 2 canonical, Tier 2A and the grounded lane in one pass;
   no counter stops it, and `note_network_call` observes cost rather than bounding it.

---

## §3 · Abstention

**Problem.** A pipeline that must fill a column will invent the value.
`README.md:107`: "**Never fabricate data.** If confidence is low, return the original values
and flag for human review." The opposite failure is equally recorded: a flag raised on
everything stops being a signal. `enrichment/flags.py:4–8` — the flag used to answer "which
tier ran?", "That produced a flag on 47 of 50 demo records and a reason text that named a code
path rather than a doubt, so the flag stopped working as a triage signal."

**Mechanism.** Abstention has four distinct shapes, and they are different columns:

| Shape | Meaning | Site |
|---|---|---|
| Refuse the write, keep the candidate | The value is not written; the string travels as a suggestion | `enrichment/name_gate.py:171` (`evaluate`), `enrichment/orchestrator.py:3175` |
| Write, and mark the doubt | The value ships with a code scoped to the field | `enrichment/flags.py:1059` (`compute_flags`), `:797` (`render`) |
| Write, and say nothing is asked | Advisory code: prose, no reviewer | `enrichment/flags.py:249–252` (`ADVISORY_CODES`) |
| Decline to change | The input is retained and the record is declared confirmed | `enrichment/unchanged_state.py:306–325` |

The refusal vocabulary is closed (`enrichment/name_gate.py:51–56`): `empty`, `address_like`,
`different_entity`, `country_conflict`, `no_country_fuzzy_registry`, `entity_superseded`. The
identity decision is three-valued, not boolean — `same` / `undecidable` / `different`
(`:62–68`) — and only `different` refuses; `undecidable` writes and flags
(`GateDecision.flagged`, `:72–75`).

`flag_for_review` is derived, not "codes are non-empty" (`enrichment/flags.py:36–43`): "A row
can therefore ship with `flag_for_review` false and a populated `flag_reason`: something is
worth saying about the record, and nothing is being asked of anyone." The status column
carries the same distinction: `enriched` / `verified` / `unresolved` / `failed`
(`api/models.py:646`), and `enrichment/unchanged_state.py:311–316` states the constraint that
ties them together — "A record Fix 2 declines to flag must not simultaneously ask a data
steward to review it."

Absence of data is explicitly not a defect (`enrichment/flags.py:31–35`): "A research
institution with no department and no contact is not flagged: there is nothing for a reviewer
to do."

**Guard.** Three guards make abstention hold against a lane that would rather write.

* **The confidence table is one function.** `enrichment/confidence.py:187–224`. Contradiction
  and ambiguity are tested first, before the registry row: "A registry hit that a consistency
  check refused is not a verified value that happens to be flagged — it is a value the
  pipeline decided against."
* **The gate recomputes.** `enrichment/name_gate.py:11–15`: "The checks are recomputed here,
  from the record, rather than trusted from whatever the producing lane decided."
* **A refusal cannot vanish.** `enrichment/name_gate.py:24–28`: the decision "carries the
  candidate and the reason, and the caller renders both into the flag detail … 'nothing is
  discarded' is a property of the gate, not a promise each caller has to keep." What qualifies
  as a *suggestion* is narrowed further (`enrichment/orchestrator.py:1264–1292`): only a name
  that survived an identity comparison and lost to a policy, never a dissimilarity, shape or
  evidence-absence refusal.

**Does not solve.** Abstention returns the record's own value, which may itself be wrong: the
last-resort write at `enrichment/orchestrator.py:9194–9201` retains the preprocessed input
under rule `last-resort:preprocessed-input-retained`. Advisory codes ship a value nobody will
look at — `enrichment/orchestrator.py:1922–1957` (`_ship_unverified_domain`) writes a domain
the ownership guard declined, at `web:{domain}:low`, on the recorded ground that "most of those
candidates are the right site; the guard's failure is usually an absence of corroboration
rather than evidence of a wrong answer". And nothing in the repository records what a reviewer
decided: the flag is an output column, not a workflow state.

---

## §4 · Grain policy

**Problem.** The two phases do not share a grain, and every conflation of the two has a
measured cost. `dedup/consolidate.py:2–6`: "The SAP customer extract arrives at ROW grain: one
row per customer per company-code / sales-area assignment, so a single customer occupies 1 to
68 rows. Every master-data field is byte-identical across a customer's rows; only the
sales-area fields vary."

**Mechanism.** Four grains, each with one owner:

| Grain | Unit | Owner | Citation |
|---|---|---|---|
| Row | One SAP extract row | `/enrich`, stateless | `api/routes.py:106`; `enrichment/provenance.py:34–37` |
| Customer | One `Customer` number | Consolidation stage | `dedup/consolidate.py:355` (`consolidate_rows`) |
| Signature | One `(norm_name1, norm_department)` within a block | `dedup/signatures.py` | `dedup/signatures.py:3–5` |
| Cluster | A set of member row ids | `dedup/cluster_key.py:17` (`cluster_hash`) | `dedup/scoring.py:1151` |

Consolidation is a column append, not a collapse. `dedup/consolidate.py:17–20`: "rows in ==
rows out, in the same order, with two strings computed per customer and written onto every row
of that customer. Collapsing the extract to one row per customer is a separate, downstream
concern and deliberately not done here." The alternative is recorded with its failure mode
(`:12–15`): filtering to one arbitrary row per customer "keeps ONE of a customer's company
codes and, when the surviving row happened to be a sales-area row with a blank company code,
keeps none at all."

The counts derived from the consolidated columns are never read from the file
(`dedup/scoring.py:795–800`): "Always derived from the consolidated fields / id slots, never
read from the file — single source of truth." The delimiter is one constant
(`dedup/consolidate.py:45`, `CONSOLIDATED_DELIMITER = ","`) and the reader accepts both forms
(`dedup/scoring.py:780–792`), because "every historic extract uses ';' — a splitter that knew
only one of them would count a whole comma-joined list as a single value."

The LLM never sees the row grain. `dedup/signatures.py:3–5`: "100 byte-identical rows collapse
to one signature; the LLM only ever works on distinct signatures, never on raw rows. This is
the blow-up guard." The department half of the key reads the whole name block below Name 1
(`:7–10`), because "two rows at one address whose units differ only in Name 3 are two
departments, and collapsing them would merge records that name different things."

Enrichment holds the row grain with one deliberate exception: batch consensus propagates fields
across records that name one organisation at one address. It is explicitly not a merge —
`enrichment/batch_consensus.py:18–21`: "It never merges, drops or deduplicates records — Phase
2 remains the only place entities are merged. It is field propagation within one batch: the
record count in equals the record count out."

**Guard.** Header spellings cannot drift between the writer and the reader:
`dedup/consolidate.py:35–38` imports the scorer's tolerant matcher rather than re-spelling it,
so the two "must never disagree about which spelling of a header is 'Company Code'". Cluster
identity is a hash of the sorted member row ids (`dedup/cluster_key.py:17–20`), so "same
membership → same id across runs, machines, and input orderings". A repeated `row_id` in one
scoring request is the one hard error (`dedup/scoring.py:12–14`): "that means a broken upstream
join, and scoring it would double-elect."

**Does not solve.** Nothing in the repository collapses the extract to one row per customer;
that stage is named as downstream and does not exist here. The consolidation endpoints —
`api/routes.py:960` (`/api/preprocess/consolidate`, drawn as figure 19) and `:1026` (`.../file`)
— are present but are not called by any exported ADF pipeline; the four pipelines call
`/enrich`, `/issues/json`, `/api/dedup/cluster-block` and `/api/dedup/score` and nothing else
(`11_DELTA.md` §11.3.7). A pipeline that skips consolidation therefore scores
`Company_Code_Count` and `Sales_Org_Count` from columns nothing wrote.

The customer grain is also not verifiable inside the JSON route. Consolidation is correct only
when one request carries all of a customer's rows, and a request cannot check that it does:
`api/routes.py:999–1004` emits a heuristic warning naming the customers at the first and last
row positions, because a customer split across two requests consolidates twice, each time over
a part. The file route has no such limit — "always safe — the whole workbook is processed"
(`:1040–1042`).

---

## §5 · The origin invariant

**Problem.** A lane that declares itself the producer of a value it passed through unchanged
destroys the record of where the value came from. `enrichment/orchestrator.py:1735–1748` states
the measurement: "seven records lost `relocated-unverified` this way, five of them dropping out
of review entirely, with their Name 2 byte-identical on both sides (13333471, 13335858,
13140896, 13333600, 13335245, 13335676, 13340639). The doubt was not answered — the fact it was
derived from was destroyed."

**Mechanism.** One funnel writes fields, and the origin is recorded there.
`enrichment/orchestrator.py:1711` (`_write`) — chosen for completeness rather than convenience
(`:1720–1725`): "The department block has 27 write sites … and a hand-maintained list of them
is a list that goes stale. Recording the origin HERE makes completeness structural."

The invariant itself is `:1731–1733` — "an origin may change only when the VALUE changes" —
enforced at `:1750`:

```
if origin is not None and not _same_value_folded(value, incumbent):
```

`_origin_for` (`:1235–1246`) returns `None` for a transform, because "casing, abbreviation
expansion and the packing rules reshape or relocate a value, they do not produce one, and the
origin follows the VALUE."

**Guard.** The equality that decides "changed" is deliberately narrow.
`enrichment/orchestrator.py:1756–1770` (`_same_value_folded`) folds whitespace runs and case
only, and states the exclusion: "deliberately NOT `normalize_key`, which folds legal forms and
would read 'Delta Analytical Inc' and 'Delta Analytical LLC' as one value … Two values that
differ by a period or a comma ARE different values here, and rightly re-attribute."

Underneath it, attribution is structural rather than conventional.
`enrichment/provenance.py:27–33`: "the six scoped keys cannot be assigned.
``record["domain"] = x`` raises :class:`UnattributedWriteError`; the only way a value reaches
one of them is :meth:`EnrichedRecord.write`, which requires a structured :class:`Evidence`
argument" (`:1064–1110`). The scope is `SCOPED_FIELDS` (`:70–77`): `name1_enriched`,
`name2_enriched`, `domain`, `record_type`, `ror_id`, `lei_id`.

**Does not solve.** The invariant is enforced for the department block only — `_slot_origin` is
maintained when `field in DEPT_ENRICHED_FIELDS` (`enrichment/orchestrator.py:1727`, `:1729`) —
and provenance covers six fields. `contact`, `care_of` and `email` are outside it by design
(`enrichment/provenance.py:20–24`) because they carry personal data, so no origin is
reconstructible for them. The provenance log is per batch and is not persisted
(`:34–37`): "The log lives on the record for the life of one batch and ships in the JSON
response; nothing here writes to a database."

---

## §6 · Four-eyes scope

**Problem.** A merge is irreversible in the master data and an enrichment write is not. The two
therefore get different controls, and the boundary is stated rather than assumed.

**Mechanism.** Every election is a proposal (`dedup/scoring.py:1159–1167`): unique rows
self-reference with status `unique`; a real cluster elects its highest-scoring member and
"Every election is a PROPOSAL, never auto-committed"; an all-blocked cluster elects with status
`manual_review` "so a human confirms before anything is blocked". A low-confidence adjudication
is demoted to `manual_review` at `CONFIDENCE_MERGE_THRESHOLD`, default `0.95`
(`dedup/scoring.py:50`, `config.py:128`, `:600`).

Approval is a separate call: `api/routes.py:1487` (`/api/dedup/approve`) →
`dedup/scoring.py:605–634` (`apply_approval`), which sets `approval_status` and, on
"approved", promotes the proposed winner into the golden fields. The downstream contract is
`api/routes.py:1495–1496`: "Phase 3 consumes ONLY rows with approval_status='approved' or
election_status='unique'."

Enrichment has no such gate, and the write-back targets differ accordingly:

| Path | Endpoint | Merge proc | Target |
|---|---|---|---|
| Enrichment | `/enrich` (`api/routes.py:106`) | `Mapping.usp_merge_legacy_enriched` (`adf/enrichment_pipeline.json:136`) | `dp_legacy.<entity>.Legacy` (`sql/usp_merge_legacy_enriched.sql:29`) |
| Issues | `/issues/json` (`api/routes.py:883`) | `Mapping.usp_MergeLegacyIssues` | Legacy |
| Clustering | `/api/dedup/cluster-block` (`api/routes.py:1330`) | `Mapping.usp_MergeValidationClusters` | `dp_validation.<entity>.Validation` (`sql/usp_merge_validation_clusters.sql:8`, `:33`) |
| Scoring | `/api/dedup/score` (`api/routes.py:1437`) | `Mapping.usp_MergeValidationScores` | `dp_validation.<entity>.Validation` |

Entity-resolution output lands in a validation database and carries `approval_status` as a
column (`sql/usp_merge_validation_scores.sql:60`, `:78`); enrichment output lands in the legacy
master table directly.

**Guard.** The enrichment merge is update-only and never blanks a populated column:
`sql/usp_merge_legacy_enriched.sql:86` composes `WHEN MATCHED THEN UPDATE SET` with
`COALESCE(NULLIF(LTRIM(RTRIM(src.[…])), SPACE(0)), tgt.[…])` for every free-text field, so an
empty enrichment value leaves the incumbent in place. There is no `INSERT` branch, and every
proc is scoped by two guards before it writes — the entity schema must exist
(`:11–17`) and the group code must match rows under the `<groupcode>_%` `code` prefix
(`:19–36`).

**Does not solve.** Three limits, all of them in the code:

1. **Approval is not persisted.** `api/routes.py:1494–1495`: "Persistence is intentionally out
   of scope — a durable approval store is a future step." The endpoint is stateless: the caller
   submits the rows and gets them back.
2. **Approval is not enforced in SQL.** `usp_MergeValidationScores` writes `approval_status` as
   data; its `MERGE` carries no predicate on it (`sql/usp_merge_validation_scores.sql:78`). The
   four-eyes control is a contract on a consumer, not a constraint on the store.
3. **That consumer does not exist here.** No Phase 3 promotion code is in the repository, and
   no exported ADF pipeline calls `/api/dedup/approve` — the four pipelines' web activities
   name `/enrich`, `/issues/json`, `/api/dedup/cluster-block` and `/api/dedup/score`.
4. **The approval that actually happens does not run through this code**
   (`02_ARCHITECTURE.md` ⚠-27, drawn as figure 14). The observed step is a DATAshaper action:
   the steward opens a cluster in the DS deduplication view, reads the adjudicator's `Reason`,
   selects a `Leading Code` and applies it, and the view writes to the Validation table
   ([OBSERVED] `docs/thesis/CONTEXT-EXTERNAL.md:395–398` — "This is the human approval step:
   the system proposes, a steward confirms"). The endpoint, `apply_approval`, `approval_status`
   and the `manual_review` demotion are a parallel mechanism, unconnected to it.

So the four-eyes principle holds in the deployment — entity resolution proposes and a human
confirms before anything is applied — while the specific machinery this repository implements
for it is not what enforces it.

---

## §7 · Determinism

**Problem.** A measurement nobody can reproduce is not evidence. Two failures are recorded in
the code. `utils/cache.py:7–11`: "Two runs of the identical 101-row chemspeed batch on the
identical codebase produced 7 substantively different records. Eleven of the differing rows
differed only in an extraction date." And `llm/openai_client.py:94–99`: "`temperature=0` alone
is NOT determinism. It makes the sampler pick the arg-max token, but a tie between two
equally-likely tokens is still broken by the server, and MoE routing/batching makes the logits
themselves vary slightly between requests. Two runs of the identical chemspeed batch flipped
three records' `confidence` self-reports between `self_high` and `self_medium` on exactly that."

**Mechanism.** Determinism is pursued in three places, with different strength:

| Layer | Control | Citation |
|---|---|---|
| Sampling (Phase 1) | `temperature=0.0`, `top_p=1.0`, `seed=42` on every call | `llm/openai_client.py:102`, `:103`, `:108`, `:308–322` |
| Sampling (Phase 2) | `temperature=0.0` only when `reasoning_effort` is not in play; no `seed` | `dedup/llm.py:9–16`, `:138`, `:236–237` |
| Evidence | Every external answer recorded on disk under a key that is a pure function of the request | `utils/cache.py:1–21`, `:188` (`llm_disk_key`) |
| Replay | Frozen enrichment cache; record/replay adjudicator cache | `config.py:145` (`CACHE_FROZEN`), `utils/cache.py:110`; `dedup/cache.py:17–27` |
| Arithmetic | Election is pure arithmetic over a versioned weights table | `dedup/scoring.py:1–7`, `:641–646` |

The cache key rule is the load-bearing one. `utils/cache.py:40–45`: "Every key is built here,
from the request and nothing else. **No key contains a run id, a batch id, a date or a record
id** — that is the property that makes a second run hit rather than miss, and
`tests/test_determinism.py` asserts it structurally rather than by inspection." Keys normalise
through `dedup.signatures.normalize_key`, reused rather than reimplemented (`:57–61`).

The model's answers are cached on the same terms as a page read
(`llm/openai_client.py:425–433`): "An LLM answer is evidence the pipeline reads, exactly as a
page read or a SERP result is — and the service does not guarantee reproducibility even at
temperature 0 with a fixed seed … Measured: with the seed accepted and sent, two warm runs of
the chemspeed batch still differed on 10 of 100 rows, every one of them an LLM decision."

`CACHE_FROZEN` turns a miss into a recorded error rather than a network call
(`utils/cache.py:15–21`), traced as `evidence-unavailable-frozen` (`:110`), which is what lets
a thesis measurement "state exactly which records were short of evidence instead of silently
re-gathering it against a web that has moved on". Phase 2's equivalent is
`DEDUP_FIXTURE_CACHE_DIR` with two modes (`dedup/cache.py:19–27`): `record` serves a hit and
writes on a miss; `replay` "Serve[s] a hit; on a miss REFUSE, loudly … a miss means the prompt
changed, and silently calling the model would quietly re-measure something else while reporting
it under the old run's name." Errored calls are never cached (`:29–31`): "A 429 or a socket
timeout is a fact about the afternoon, not about the question."

Around the model, the surrounding computation is deterministic by construction: nomination is
"deterministic and pure (no LLM, no network): the same units in any order yield the same
candidate list, so the LLM call sequence is stable" (`dedup/candidates.py:14–15`); cluster ids
are membership hashes (`dedup/cluster_key.py:17–20`); scoring has "No LLM, no network — ever"
(`dedup/scoring.py:6–7`); and the weights table is fingerprinted onto every scored row so "a
proposal and its later approval can be checked for score drift when weights were retuned in
between" (`dedup/scoring.py:641–646`).

**Guard.** The sampling parameters are module constants, not environment variables.
`llm/openai_client.py:89–92`: "They are module constants rather than env knobs on purpose: a
reproducibility control that can be changed per environment is not a control." A deployment
that rejects `seed` disables it once, process-wide, and says so
(`llm/openai_client.py:110–114`, `:324–345`) rather than failing every record or silently
continuing. The reference year for both `*_last_used` ladders is resolved once per election
(`dedup/scoring.py:1176–1180`), "never at import … and never per row (a batch straddling
midnight on 31 December would band its first and last rows differently)."

**Does not solve.** The pipeline does not claim bit-reproducibility, and the code says where the
claim stops.

Command and verbatim output:

```
$ git log --format=%B -n 1 3527585
```
```
Measured against the live deployment (MDM-Apoorva-gpt-5.4, model
gpt-5.4-2026-03-05) rather than assumed. temperature and reasoning_effort turn
out to be mutually exclusive there:

    temperature=0.0 alone                  -> 200
    reasoning_effort=low alone             -> 200
    temperature=0.0 + reasoning_effort=low -> 400 Unsupported value:
        'temperature' does not support 0.0 with this model. Only the default
        (1) value is supported.

So temperature is sent only when reasoning_effort is not in play. […] On the default
config (reasoning_effort=low) temperature is therefore not sent and dedup
output is not bit-reproducible — the honest position […]

Where temperature does apply (reasoning_effort empty), 10 live runs of a
borderline block against a matched control show it narrows but does not remove
variability: distinct confidence vectors 8 -> 3, distinct routing vectors
2 -> 1, distinct reasoning strings 28 -> 25.

seed IS accepted alongside reasoning_effort on this deployment and is the
stronger control; deliberately not added here.
```

Five residues follow.

1. **Phase 2's default configuration carries no sampling control at all.** With
   `DEDUP_REASONING_EFFORT="low"` (`dedup/llm.py:148`) neither `temperature` nor `seed` is
   sent, so adjudication is reproducible only through the record/replay cache.
2. **Phase 1 sends all three controls and is still not byte-identical.** The recorded figure is
   10 of 100 rows (`llm/openai_client.py:430–433`), which is why the evidence cache, not the
   sampler, is the reproducibility mechanism.
3. **A frozen run is reproducible without being equivalent.** A frozen miss lets the record
   proceed with less evidence than a live run had (`utils/cache.py:15–19`), so a frozen re-run
   reproduces *the recording*, not the original network conditions.
4. **The recording is not in this repository** (`08_GAPS.md` G-13, G-55). Six namespaces are
   declared (`utils/cache.py:457–465`) and four of them are gitignored — `tests/fixtures/serp/`,
   `tests/fixtures/registry/`, `tests/fixtures/fetch/`, `tests/fixtures/llm/`
   (`.gitignore:37–40`) — and absent from the tree, so a frozen run against the default
   `EVIDENCE_CACHE_DIR` replays page reads and Wikidata only. `.gitignore:33–36` states what
   that costs: "the runs behind determinism_findings.md were measured against exactly that,
   and `llm/` in particular is what makes a re-run reproduce the model's answers rather than
   re-ask for them." Phase 2 is not covered by `CACHE_FROZEN` in any case; it has a separate
   store under a separate switch (`dedup/cache.py:17–27`).
5. **A run that lost its `seed` cannot be told from one that kept it** (`08_GAPS.md` G-96).
   The latch is process-wide and one-shot — `_SEED_SUPPORTED = False` at
   `llm/openai_client.py:336`, read at `:448` — and reaches no output column, no
   `EnrichmentSummary` field and no provenance event. Only the log carries it, in the warning's
   own words: "Byte-identical re-runs are no longer guaranteed by the service."

Command and verbatim output for residue 4:

```
$ for d in page_reads wikidata serp registry fetch llm; do printf "%-12s " "$d"; \
    [ -d "tests/fixtures/$d" ] && echo "present" || echo "ABSENT"; done
```
```
page_reads   present
wikidata     present
serp         ABSENT
registry     ABSENT
fetch        ABSENT
llm          ABSENT
```

---

## Summary

Seven design choices, stated as problem / mechanism / guard / residue: the constrained reader
(the model reads numbered evidence and its answer is checked for verbatim containment before it
may even be used as a registry query); the tier ladder (deterministic and free sources first,
each escalation conditional, with Tier 2B defined but unwired and Tier 3 demoted to a degraded
mode); abstention (four distinct shapes, a closed refusal vocabulary, a three-valued identity
verdict, and no silent discard); grain policy (four grains with one owner each, and
consolidation as a column append rather than a collapse); the origin invariant (an origin may
change only when the value changes, enforced at the one write funnel); four-eyes scope (entity
resolution proposes into a validation database and waits for a steward action that the observed
deployment performs in DATAshaper, not through this repository's approval endpoint, while
enrichment writes back to the legacy master directly); and
determinism (sampling controls, a request-keyed evidence cache, and replay modes, with the
non-reproducibility that remains stated in the code rather than papered over).

Two of the seven describe a principle the deployment honours through machinery this repository
does not contain: the four-eyes control is exercised in the DATAshaper view rather than through
`/api/dedup/approve`, and the determinism control depends on recordings that are gitignored and
absent. Both are stated in their sections above rather than left to inference.
