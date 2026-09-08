# Evaluation runs — PARTIAL

> **Current runs: `327ee53`, in `eval/out/327ee53/`.** Both `eval/out/f57782f/` and the
> bare `d3a3cfc` artefacts in this directory are **SUPERSEDED** and kept in place for
> comparison. Every number below the "Runs at 327ee53" heading is the current one.

**This is a three-stratum evaluation plus a mixed workbook, not a five-stratum one.** S1,
S4 and **S5** were run and scored; `testall100_CLEAN_INPUT` was run and has a before/after
table but **carries no `expected_issue_codes` column**, so it has no precision or recall
and none is implied. **S2 and S3 raw inputs are not on this machine** — the only S3 files
are enriched exports, which carry no `*_Original` columns, so the raw input cannot be
reconstructed from them and re-running S3 would feed the pipeline its own output. Nothing
here is relabelled or extrapolated to stand for the missing strata.

## Provenance

| | |
|---|---|
| commit | `d3a3cfc2287a76b305fe571fb3e0b84b0445e64b` |
| working tree | clean at run time (`git status --porcelain` empty) |
| evidence cache |entries: 5365   keys-sha256[:12]: d801665da8fc |
| cache path | `<scratch>/cache` (session-local; not `logs/cache`) |
| mode | `--frozen` against the warm cache — 0 network calls on every run |
| `pytest -q` | **8 failed / 3073 passed / 7 skipped** — see the correction below |

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

---

# Runs at 327ee53 — current

Same scoring rules and catalogue exclusions as above (G7 out of the after-totals and out of
precision/recall; precision/recall over the annotation vocabulary only; G6 kept and marked
*persists by design*).

| | |
|---|---|
| commit | `327ee53` — *An origin may change only when the value changes* |
| working tree | clean at run time (`git status --porcelain` empty) |
| evidence cache | entries 7134, keys-sha256[:12] `0d01989dd9c7` |
| mode | `--frozen`, 0 network calls on every run |
| `pytest -q` | **8 failed / 3247 passed / 7 skipped** — see the correction below |
| supersedes | `f57782f` and `d3a3cfc`, both kept in place |

> **The cache is not the one `f57782f` ran against** (5397 entries, `8d9caf4f8625` → 7134
> entries, `0d01989dd9c7`). It grew across a session of gating, and warming fills lanes that
> previously degraded under `--frozen`. Every delta below therefore mixes two causes — code
> change and cache growth — and is reported as an observation, not as an attribution to the
> code. The `327ee53` numbers themselves are clean: one commit, one cache, one run.

| stratum | input | output | rows | frozen misses | annotated |
|---|---|---|---|---|---|
| S1 academic research | `~/Downloads/demo_S1_academic_research_100_v1 (1).xlsx` | `eval/out/327ee53/S1_enriched.xlsx` | 100 | 0 | 100 |
| S4 hospital health | `~/Downloads/demo_S4_hospital_health_100_v1 (1).xlsx` | `eval/out/327ee53/S4_enriched.xlsx` | 100 | 3 | 99 |
| **S5 SMB residual** | `~/Downloads/demo_S5_smb_residual_100_v1 (1).xlsx` | `eval/out/327ee53/S5_enriched.xlsx` | 100 | 5 | 99 |
| t100 mixed | `testall100_CLEAN_INPUT.xlsx` | `eval/out/327ee53/t100_enriched.xlsx` | 99 | 1 | **0** |
| S2 | — | — | — | — | not on this machine |
| S3 government labs | — | — | — | — | not on this machine (enriched exports only, raw not reconstructable) |

## §1 Determinism at 327ee53

`logs/runs/determinism_S1_327ee53.json` — two frozen runs of S1 at this SHA against this
cache:

    rows compared    : 100
    rows differing   : 0
    cell differences : 0
    frozen misses    : 0 on both runs
    PASS

Join key `(Name 1, City, Customer)`. The `d3a3cfc` and `f57782f` artefacts
(`determinism_S1.json`, `determinism_S1_f57782f.json`) are kept and superseded.

## §3 S5 — scored for the first time

| | raw | corrected |
|---|---|---|
| precision | 0.339 | **0.836** |
| recall | 0.648 | **0.648** |
| TP / FP / FN | 199 / 388 / 108 | 199 / 39 / 108 |

Outside annotation scope (not scored): `G2-VAL-003` 100, `G2-VAL-006` 100 (both G6),
`G2-VAL-007` 100. Verification dispositions, reported separately: `G7-VERIFY-001` **49**.

### S5 before/after (G1–G6, G7 excluded)

| | before | after | delta |
|---|---|---|---|
| **TOTAL (G1–G6)** | 538 | 337 | **−201** |
| of which G6 | 200 | 205 | +5 *persists by design* |
| **G1–G5 only** | 338 | **132** | **−206** |

Largest reductions: `G2-VAL-007` 100→2, `G1-ADDR-003` 24→0, `G1-CROSS-003` 19→0,
`G4-ADDR-008` 17→0, `G1-ADDR-004` 7→0. `G2-VAL-001` 0→2 and `G2-NAME-012` 0→3 are G6
increases (*persists by design*; the latter is the improved-detectability case documented
above). `G5-NAME-001` 47→40 is the weakest reduction of any stratum — S5 is the residual
SMB stratum and carries the most person-shaped and unresolvable names.

**S5 is the hardest stratum on recall (0.648) and the best on precision (0.836).**
`G1-ADDR-009` (11 expected, 0 raised) and `G1-ADDR-011` (6 missed) are the whole of the
recall gap, the same two codes named as the largest pure recall gaps on S1 and S4.

## §3 S1 and S4 at 327ee53

| | S1 raw | S1 corrected | S4 raw | S4 corrected |
|---|---|---|---|---|
| precision | 0.262 | **0.728** | 0.339 | **0.805** |
| recall | 0.566 | **0.566** | 0.744 | **0.744** |
| FP | 400 | 53 | 402 | 50 |
| G7 dispositions | 46 | — | 49 | — |

| before/after | S1 | S4 |
|---|---|---|
| TOTAL (G1–G6) | 496 → **248** (−248) | 559 → **303** (−256) |
| of which G6 | 214 → 220 (+6) | 203 → 210 (+7) |
| G1–G5 only | 282 → **28** (−254) | 356 → **93** (−263) |

**S1 is identical to `f57782f` in every measure** — precision, recall, every code count.
**S4's after-total moves 301 → 303**: `G2-NAME-009` 12 (was 11) and `G3-NAME-003` 4
(was 3). Both are consequences of department slots that now survive rather than being
emptied, which is what the `327ee53` commit fixed — more records ship a Name 2, so more
records are eligible for codes that ask about it.

## t100 — before/after only

No `expected_issue_codes` column, so **no precision or recall is computed or implied**.

| | before | after | delta |
|---|---|---|---|
| TOTAL (G1–G6) | 505 | 279 | −226 |
| of which G6 | 210 | 208 | −2 |
| G1–G5 only | 295 | **71** | **−224** |

## Flag rates per stratum

| stratum | rows | flagged | rate | `relocated-unverified` | `Suggested Name` populated |
|---|---|---|---|---|---|
| S1 | 100 | 46 | 46.0% | **1** | 5 |
| S4 | 100 | 49 | 49.0% | **9** | 11 |
| S5 | 100 | 49 | 49.0% | **6** | 11 |
| t100 | 99 | 45 | 45.5% | **4** | 5 |

`relocated-unverified` — the code added this session for a value the pipeline MOVED into a
name slot that nothing vouches for — fires on **20 rows across 399**, and is the flag whose
survival the `327ee53` origin invariant protects: before that fix, seven records lost it
while their data stayed byte-identical.

`Suggested Name` / `Suggestion Source` are populated on **32 rows across 399** — a refused
identity proposal handed to the steward rather than discarded. Neither column is read by
anything downstream; both are steward-facing only. **Still pending Bernd's answer on
whether DATAshaper passes unmapped columns through.**

Code mix at 327ee53:

    S1    unverified-inference 31, domain-unverified 7, dept-via-lab 2, entity-superseded 2,
          relocated-unverified 1, registry-location-mismatch 1
    S4    domain-unverified 22, entity-superseded 13, unverified-inference 12,
          relocated-unverified 9, registry-location-mismatch 3, dept-via-lab 1
    S5    domain-unverified 30, unverified-inference 15, relocated-unverified 6,
          registry-location-mismatch 5, entity-superseded 3, person-unresolved 2,
          dept-via-lab 1, no-match 1, email-conflict 1
    t100  domain-unverified 21, unverified-inference 16, entity-superseded 15,
          relocated-unverified 4, dept-via-lab 2

## Provenance mix, and the delta against f57782f

| measure | S1 f57782f | S1 327ee53 | S4 f57782f | S4 327ee53 | S5 (new) | t100 (new) |
|---|---|---|---|---|---|---|
| Name 1 changed | 42 | 42 | 75 | **76** | 76 | 53 |
| registry-verified | 84 | 84 | 48 | **49** | 23 | 61 |
| llm-provisional | 6 | 6 | 21 | 21 | 29 | 12 |
| input-retained | 10 | 10 | 31 | **30** | 46 | 26 |
| Flag for Review | 45 | **46** | 45 | **49** | 49 | 45 |
| `domain-unverified` | 8 | **7** | 23 | **22** | 30 | 21 |
| `entity-superseded` | 2 | 2 | 13 | 13 | 3 | 15 |

S4 gains one registry match and four flagged rows; S1 gains one flagged row and loses one
`domain-unverified`. Per the cache caveat above, these are **not attributed to the code** —
the warmer cache resolves lanes that previously degraded.

**S5's shape is the finding worth stating.** It is the only stratum where input-retained
(46) exceeds registry-verified (23): the SMB residual stratum is dominated by organisations
that are in no registry, which is what the stratum was constructed to contain. Its 0.836
precision and 0.648 recall should be read against that, not against S1's 84-of-100 registry
resolution.


---

# Correction — the `pytest -q` lines said five, the tree says eight

Both `pytest -q` figures above originally read "5 failed / … (the five pre-existing
failures)". Re-running the full suite at those exact SHAs gives **eight**, and the same
eight at both:

| | recorded | actual |
|---|---|---|
| `d3a3cfc` | 5 failed / 3076 passed | **8 failed / 3073 passed** |
| `327ee53` | 5 failed / 3250 passed | **8 failed / 3247 passed** |

**The totals were consistent; the split was not.** 5 + 3076 = 3081 = 8 + 3073, and
5 + 3250 = 3255 = 8 + 3247. Same test set, same run, three tests recorded as passing that
were already failing — so nothing about the sizes of those runs is in doubt, only the
pass/fail division within them.

The eight are the same eight at `f57782f`, `2125ad2`, `e31b53b`, `f292bfa`, `e396722`,
`a17a2e0` and `96dd528` as well: no commit in the range introduced any of them. They are
now pinned in `tests/KNOWN_FAILURES.md`, which every future gate asserts against as a SET
rather than as a count.

## Withdrawn codes still in `expected_issue_codes` — the denominator is stale

Every precision/recall figure computed from these fixtures is scored against an
annotation vocabulary that still expects **158 codes the catalogue has withdrawn**.
A withdrawn code has no emission site (`enrichment/issue_detection.py` declares each
with `status="withdrawn"` and a `reason`), so it can never be raised: every occurrence
below is an unreachable expectation that depresses recall by construction.

| stratum | expected codes | withdrawn | rows affected | breakdown |
|---|---|---|---|---|
| S1 | 251 | 66 | 52 | `G1-NAME-001` 34, `G1-ADDR-009` 26, `G4-ADDR-008` 3, `G4-ADDR-025` 3 |
| S2 | 238 | 38 | 32 | `G1-NAME-001` 15, `G4-ADDR-008` 12, `G1-ADDR-009` 11 |
| S3 | 206 | 9 | 9 | `G1-NAME-001` 6, `G1-ADDR-009` 2, `G4-ADDR-025` 1 |
| S4 | 277 | 10 | 9 | `G4-ADDR-008` 5, `G1-NAME-001` 3, `G1-ADDR-009` 2 |
| S5 | 307 | 35 | 35 | `G4-ADDR-008` 18, `G1-ADDR-009` 11, `G1-NAME-001` 6 |
| `dedup_STRESS_200_v1` | — | — | — | no `expected_issue_codes` column (Rule 1) |

Totals: `G1-NAME-001` 64, `G1-ADDR-009` 52, `G4-ADDR-008` 38, `G4-ADDR-025` 4.

`G1-ADDR-009` was withdrawn on 2026-09-06 (ndd, never emitted); `G1-NAME-001` on
2026-09-07; `G4-ADDR-008` and `G4-ADDR-025` on 2026-09-06.

**No read-time filter exists.** No `.py` in the repository reads
`expected_issue_codes` — this file is maintained by hand — so nothing currently
subtracts these from the denominator. Anyone quoting a precision or recall number from
a stratum above must subtract its withdrawn count first, or the figure understates
recall by up to 26% of the vocabulary (S1).

One cell has been corrected at source: row `13332345` (S1) expected
`G1-ADDR-009;G2-NAME-012` and now expects `G1-ADDR-003;G2-NAME-012`, which is what the
detector raises for `Equad A302`. The remaining 26 `G1-ADDR-009` rows in S1 are
deliberately untouched — remapping each withdrawn code to its replacement is a
per-code judgement, not a per-row edit, and some have no deterministic replacement,
which is why they were withdrawn.

## Follow-ups opened by the PO Box carry-through

The change that carries a dedicated-column PO Box to the enrichment output was
deliberately kept to the output layer. Four things were found while doing it and
consciously not fixed there; each is its own branch.

1. **PO Box output shape normalisation.** The column now mixes two shapes by
   provenance: `_extract_po_box` returns the whole match (`"PO Box 2000"`) while the
   dedicated column carries a bare id (`"750162"`). Both are faithful to their source
   and neither is normalised. Decide one output shape and apply it to both.
2. **G3-ADDR-005 equality at both emission sites.** The code means "more than one PO
   Box on the record" and is raised on *presence*, not on disagreement — a street PO
   Box alongside an identical column value still flags. Both sites do this:
   `enrichment/address_processing.py` (address stage) and
   `enrichment/issue_detection.py` (detector, `po_box_count >= 2`). If the code should
   mean "conflicting PO Boxes", both have to change together.
3. **No code for non-PO-Box content in the PO Box column.** Four of the 62 rows carry
   something that is not a PO Box at all — `M/S 643` (S2 13145811), `CODE 71740`
   (S3 13212527), `MC 151 NC` (S5 13158418), `V38` (`dedup_STRESS_200_v1` 13345937).
   They are carried through verbatim, because preserving the input is the rule and
   classifying misfiled content is a different job. No catalogue code currently
   describes "the PO Box column holds a mail stop / mail code".
4. **`department_domain` is not reproducible under `CACHE_FROZEN`.** Row 13134277
   (`university of southern california|los angeles`, `dedup_STRESS_200_v1`) returns
   `department_domain` as `https://cancer.usc.edu` on some runs and blank on others —
   **at the same commit, the same frozen cache, the same input, zero network calls**.
   Four consecutive runs at `5479130` gave value / blank / value / blank. This is not
   an artefact of any code change; it means the reproducibility gate has a live false
   positive on this column, and any A/B touching it must run a same-code control
   before attributing a delta. Every other column was stable across those runs.
