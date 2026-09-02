# Evaluation runs — PARTIAL

> **Current runs: `f57782f`, in `eval/out/f57782f/`.** The `d3a3cfc` artefacts in this
> directory (`S1_enriched.xlsx`, `S4_enriched.xlsx`, `S{n}_results.json`) are **SUPERSEDED**
> and kept in place for comparison. Every §3 number below the "Runs at f57782f" heading is
> the current one; the tables above it are the `d3a3cfc` baseline they are a delta against.

**This is a two-stratum evaluation, not a five-stratum one.** S2, S3 and S5 raw inputs are
not on this machine; only S1 and S4 were run. Nothing here is relabelled or extrapolated to
stand for the missing strata. The remaining strata follow as files.

## Provenance

| | |
|---|---|
| commit | `d3a3cfc2287a76b305fe571fb3e0b84b0445e64b` |
| working tree | clean at run time (`git status --porcelain` empty) |
| evidence cache |entries: 5365   keys-sha256[:12]: d801665da8fc |
| cache path | `<scratch>/cache` (session-local; not `logs/cache`) |
| mode | `--frozen` against the warm cache — 0 network calls on every run |
| `pytest -q` | 5 failed / 3076 passed / 7 skipped (the five pre-existing failures) |

## Runs

| stratum | input | output | rows | frozen misses | network calls |
|---|---|---|---|---|---|
| S1 academic research | `~/Downloads/demo_S1_academic_research_100_v1 (1).xlsx` | `eval/out/S1_enriched.xlsx` | 100 | 0 | 0 |
| S4 hospital health | `~/Downloads/demo_S4_hospital_health_100_v1 (1).xlsx` | `eval/out/S4_enriched.xlsx` | 100 | 6 | 0 |
| S2 | — | — | — | — | not on this machine |
| S3 government labs | — | — | — | — | not on this machine (enriched outputs only) |
| S5 | — | — | — | — | not on this machine |

Full `EnrichmentResult` JSON alongside each workbook as `S{n}_results.json`.

S4's 6 frozen misses are stable across runs and are the same 6 recorded throughout the
gating in this session; they are evidence the cache has never held, not run-to-run variance.

## §1 Determinism

`logs/runs/determinism_S1.json` — two runs of S1 at this SHA against this cache:

    rows compared    : 100
    rows differing   : 0
    cell differences : 0
    run 2 network calls: 0
    PASS

Join key `(name1_original, city)`; 50 rows shared a key and were disambiguated by customer
number identically in both runs. Not yet produced for S4 (§1 specifies S1).

---

# §3 Scoring rules — Issue Catalogue v2

Scored against **Issue Catalogue v2** (Notion `355109a5c46181498a76ee02e7c7c220`, last
edited 2026-08-20). Both a RAW and a CORRECTED table are emitted per stratum in
`S{n}_results.json` under `scores`; the report cites the corrected ones.

## Group membership is not the code prefix

Four G2-prefixed codes belong to group **G6**: `G2-VAL-001`, `G2-VAL-003`, `G2-VAL-006`,
`G2-NAME-012`. The catalogue is explicit that membership follows the remediation path, not
the code string:

> Group membership is decided by the **absence of a remediation path in the
> implementation**, not by what happened to remain flagged in any particular run. A code
> stays in G6 until a resolution path is built for it, at which point it returns to its
> defect-kind group.

`G2-VAL-007` (Search Term 1) stays in G2 and is reduced 100 -> 0 on both strata:

> `G2-VAL-007` (Search Term 1) is derived by `derive_search_terms` on every record

## Rule 1 — G7 is excluded from the metric and from precision/recall

> The distinction that matters for the metric: G1–G6 codes are raised by **defects in the
> source record**; a G7 code is raised by **successful enrichment**. It fires precisely when
> the pipeline worked [...] Counting these in the post-pipeline issue total would inflate
> that total in proportion to how well enrichment performed, which is the opposite of what
> the before/after delta is meant to show. **G7 is therefore reported separately and never
> enters the reduction metric.**

`G7-VERIFY-001` is out of the after-totals and out of precision/recall entirely, and is
reported on its own line as **verification dispositions**.

## Rule 2 — precision/recall scored only over the annotation vocabulary

The vocabulary is the union of `expected_issue_codes` across the stratum. A code the
detector raises that no annotator ever used is not evidence of a false positive; it is
outside what the annotation can speak to. Those codes are reported with counts and their
catalogue group in **outside annotation scope**, and are not scored.

The vocabulary per stratum is recorded in `S{n}_results.json` as
`scores.annotation_vocabulary`.

## Rule 3 — G6 rows persist by design

G6 rows are kept in the before/after table and marked *persists by design*:

> These codes are detected, reported, and routed to a data steward; they are expected to
> persist from the raw record to the post-pipeline record, and their persistence is correct
> behaviour rather than a pipeline failure.

`G2-NAME-012` (Research Institution Missing Department, group G6) **increases** on both
strata — S1 14 -> 20, S4 3 -> 9. Footnoted as improved detectability: the code fires on
`record_type = research AND Name 2 blank`, and enrichment resolves `record_type` on records
that arrived unclassified, so more records become eligible to be detected. The catalogue
records that its remediation path was withdrawn:

> The contact-based recovery path (Tier 2A) was withdrawn with `G2-CONTACT-008` /
> `G2-CONTACT-009`, so no automated route to a department remains

## Effect of the corrections

| | S1 raw | S1 corrected | S4 raw | S4 corrected |
|---|---|---|---|---|
| precision | 0.262 | **0.728** | 0.342 | **0.805** |
| recall | 0.566 | **0.566** | 0.744 | **0.744** |
| FP | 399 | 53 | 397 | 50 |
| after-total | 293 (incl. G7) | 248 (G1–G6) | 345 (incl. G7) | 301 (G1–G6) |

Recall is unchanged by both rules: every expected code is in the vocabulary by construction,
and no annotation expected G7.

---

# Runs at f57782f — current

Same cache, same scoring rules, same catalogue exclusions (G7 out of the after-totals and
out of precision/recall; precision/recall over the annotation vocabulary only; G6 kept and
marked *persists by design*).

| | |
|---|---|
| commit | `f57782f` — *ROR chosen flag is a fast path; overriding the registry's hedge requires exact name evidence* |
| working tree | clean at run time |
| evidence cache | entries 5397, keys-sha256[:12] `8d9caf4f8625` |
| mode | `--frozen`, 0 network calls |
| supersedes | `d3a3cfc` (`eval/out/S{n}_enriched.xlsx`, kept in place) |

| stratum | output | rows | frozen misses |
|---|---|---|---|
| S1 academic research | `eval/out/f57782f/S1_enriched.xlsx` | 100 | 0 |
| S4 hospital health | `eval/out/f57782f/S4_enriched.xlsx` | 100 | **3** (was 6) |

S4's frozen misses fall 6 -> 3: three records now resolve at Tier 1 and never reach the web
lane, so they no longer ask for evidence the cache does not hold.

## §1 Determinism at f57782f

`logs/runs/determinism_S1_f57782f.json` — 100 rows compared, **0 differing**, 0 cell
differences, 0 network calls on run 2. PASS. The `d3a3cfc` artefact
(`logs/runs/determinism_S1.json`) is kept and superseded.

## §3 deltas against the d3a3cfc tables

**S1 is unchanged in every measure** — no S1 record was affected by the Tier 1 change.

| measure | S4 d3a3cfc | S4 f57782f | delta |
|---|---|---|---|
| Name 1 changed | 74 | 75 | **+1** |
| registry-verified | 45 | **48** | **+3** |
| llm-provisional | 23 | **21** | **−2** |
| input-retained | 32 | 31 | **−1** |
| Flag for Review true | 44 | 45 | **+1** |
| `domain-unverified` | 24 | 23 | −1 |
| `entity-superseded` | 12 | 13 | +1 |

The three registry gains, all verified against live ROR:

    13334354  -> 04xzj3x20  LAC+USC Medical Center       llm:provisional -> ror:verified
    13344455  -> 04xzj3x20  LAC+USC Medical Center       llm:provisional -> ror:verified
    13343608  -> 05h4zj272  Harbor–UCLA Medical Center   input:provisional+llm -> ror:verified

`13343608` accounts for the whole flag movement: the ROR match supplies `harbor-ucla.org`,
which redirects to `lacounty.gov`, so `domain-unverified` clears and `entity-superseded`
fires. The record is both more resolved and more flagged.

Precision, recall and the before/after issue totals are **identical to d3a3cfc** on both
strata — S1 0.728/0.566 and S4 0.805/0.744, G1-G6 496->248 and 559->301. The Tier 1 change
moves provenance and identifiers, not issue-code detection: none of the three records was
raising or clearing a G1-G6 code either side.

Tier 1 hit rate across the two strata: **+3 of 200 rows (+1.5%)**; +3 of 299 (+1.0%) across
all three workbooks including `testall100_CLEAN_INPUT`.
