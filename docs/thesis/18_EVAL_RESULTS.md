Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 18

# Pass 18 — Evaluation results

Every number in this document is produced by one read-only script,
`tools/eval_report.py`, over the workbooks in `data/eval/` and the recorded run artefacts under
`logs/`. The invocation and its complete output are Appendix A; each table below is a
transcription of a block of that output and carries the output line range it came from. No
number is stated here that Appendix A does not contain.

**Tree state.** `git status --porcelain` lists the pass documents of this documentation set and
the one file this pass adds, `tools/eval_report.py`. No source file, dataset, fixture or log is
modified. `eval_report.py` writes nothing: it opens workbooks read-only and prints.

**Environment.** Python 3.9.6, `openpyxl` 3.1.5, `pydantic` 2.12.5. No network call and no LLM
call is made by any measurement in this pass.

---

## 18.1 Method, and the one methodological choice this pass makes

### 18.1.1 File pairing

`data/eval/S{n}_pre.xlsx` is the pre-enrichment issues export for stratum *n*,
`data/eval/S{n}_post.xlsx` the post-enrichment export. All ten files resolve; no stratum is
missing either side, so nothing is skipped (Appendix A §1).

| stratum | pre file | rows | cols | post file | rows | cols |
|---|---|---|---|---|---|---|
| S1 | `data/eval/S1_pre.xlsx` | 100 | 42 | `data/eval/S1_post.xlsx` | 100 | 83 |
| S2 | `data/eval/S2_pre.xlsx` | 100 | 42 | `data/eval/S2_post.xlsx` | 100 | 84 |
| S3 | `data/eval/S3_pre.xlsx` | 100 | 42 | `data/eval/S3_post.xlsx` | 100 | 84 |
| S4 | `data/eval/S4_pre.xlsx` | 100 | 42 | `data/eval/S4_post.xlsx` | 100 | 85 |
| S5 | `data/eval/S5_pre.xlsx` | 100 | 42 | `data/eval/S5_post.xlsx` | 100 | 84 |

Every workbook parses to 100 rows and 100 validated `EnrichmentRecord`s; no row is dropped for
want of a record id. The clustering and scoring workbooks are separate inputs and are not paired
with anything: they are treated in §18.6.

### 18.1.2 The issue counts are recomputed, not read out of the workbooks

Each stratum workbook carries an `Issues` column written by some earlier run of `POST /issues`.
None of the ten records the commit it was produced at (Pass 07 §3.1), and the catalogue has moved
since: `G1-NAME-001` was withdrawn on 2026-09-07 (`enrichment/issue_detection.py:270–274`), one
commit before this one, and cannot be emitted at `86d173b`.

The figures in §18.3 are therefore produced by re-running the shipped detector over the shipped
cells, on the same path `POST /issues` uses — `_parse_xlsx` (`api/routes.py:227–290`) then
`_audit_rows` (`api/routes.py:762–788`), which calls `detect_issues` with the file's
`present_fields`, `Flag for Review` and `Flag Codes` exactly as the endpoint does. The shipped
column is then compared against that recomputation, per code, in §18.2, and the shipped `post`
workbooks are compared against the pipeline's own recorded output for the same records in §18.9.

### 18.1.3 Vocabulary

`ISSUE_CATALOGUE` holds 43 declared entries, 33 live and emittable, 10 withdrawn
(`enrichment/issue_detection.py:425–428`). `QUALITY_GROUPS` is `("G1","G2","G3","G4","G5")`
(`:432`); `VERIFICATION_GROUPS` is `("G6","G7")` (`:446`) and is "never counted in any reduction
figure" (`:441–445`).

The reduction metric is the catalogue vocabulary only. `DedupIssue` codes from
`/api/dedup/score` are cluster-quality diagnostics; they appear in §18.6 and are never summed
with catalogue codes.

**The reduction set holds 18 codes, not 19.** It is `segment(code) == "Reduced"` — the rule
`POST /issues/compare` applies (`api/routes.py:540–561`): `raised == "enriched"` →
*Verification*, else `remedy == "steward"` → *Expected to persist*, else *Reduced*. Reproduced in
`tools/eval_report.py:segment`, it selects exactly the 18 codes `tests/test_issue_detection.py`
asserts at `:218`. §15.5 records why the specification's "19-code reduction set" names a
superseded state.

| group | codes in the reduction set |
|---|---|
| G1 (9) | `G1-CROSS-001`, `G1-CROSS-002`, `G1-CROSS-003`, `G1-ADDR-001`, `G1-ADDR-003`, `G1-ADDR-004`, `G1-ADDR-006`, `G1-ADDR-011`, `G1-NAME-004` |
| G2 (3) | `G2-VAL-007`, `G2-VAL-008`, `G2-NAME-009` |
| G3 (3) | `G3-NAME-003`, `G3-NAME-005`, `G3-ADDR-012` |
| G4 (1) | `G4-ADDR-027` |
| G5 (2) | `G5-NAME-001`, `G5-NAME-002` |

The seven live mandatory (DATAshaper *Error*) codes and the segment each falls in:

| code | name | segment |
|---|---|---|
| `G2-VAL-001` | Name 1 Missing | Expected to persist |
| `G2-VAL-002` | Postal Code Missing | Expected to persist |
| `G2-VAL-004` | Region Missing | Expected to persist |
| `G2-VAL-007` | Search Term 1 Missing | Reduced |
| `G2-VAL-008` | Country Missing | Reduced |
| `G4-NAME-015` | Name Overflow Beyond the Name Block | Expected to persist |
| `G4-ADDR-027` | Country Code Not ISO 2-letter | Reduced |

Because the reduction set is *defined* as the "Reduced" segment, the two lines carry identical
totals in every table below. Both are printed so that a reader checking one against the
specification's wording finds the other beside it.

---

## 18.2 The shipped `Issues` columns against the detector at this commit

Compared as code **sets**, per column, per workbook (Appendix A §4).

| workbook | `Issues` col (0-based) | position | rows agreeing | shipped only | detector only |
|---|---|---|---|---|---|
| S1_pre | 2 | earlier | 29/100 | `G5-NAME-002` 45, `G4-ADDR-008` 17, `G1-NAME-001` 15, `G5-NAME-001` 5, `G1-ADDR-006` 1 | `G2-NAME-012` 9, `G3-ADDR-013` 2, `G1-NAME-004` 1, `G1-CROSS-001` 1, `G1-CROSS-003` 1 |
| S1_pre | 7 | last | 74/100 | `G1-NAME-001` 26 | — |
| S1_post | 7 | earlier | 83/100 | `G1-NAME-001` 17 | — |
| S1_post | 82 | last | 83/100 | `G1-NAME-001` 17 | — |
| S2_pre | 6 | earlier | 95/100 | `G1-NAME-001` 5 | — |
| S2_pre | 41 | last | 95/100 | `G1-NAME-001` 5 | — |
| S2_post | 83 | last | 97/100 | `G1-NAME-001` 3 | — |
| S3_pre | 41 | last | 95/100 | `G1-NAME-001` 5 | — |
| S3_post | 83 | last | 99/100 | `G1-NAME-001` 1 | — |
| S4_pre | 41 | last | 95/100 | `G1-NAME-001` 5 | — |
| S4_post | 84 | last | 100/100 | — | — |
| S5_pre | 41 | last | 97/100 | `G1-NAME-001` 3 | — |
| S5_post | 83 | last | 98/100 | `G1-NAME-001` 2 | — |

Two results follow.

**The exports and the detector agree on everything except one withdrawn code.** Across all ten
workbooks and every last-position column, the only code the shipped column carries that the
detector does not emit is `G1-NAME-001`, and there is no code the detector emits that the shipped
column lacks. The exports were produced against a catalogue that still held `G1-NAME-001`; on
every other code, at every position, the recomputation reproduces them cell for cell.

**The S1 ambiguity Pass 07 §3.2 raised is resolved.** `S1_pre.xlsx` carries two columns named
`Issues`. Column 7 is the detector's output — it agrees on 74 of 100 rows and its only divergence
is the 26 rows carrying `G1-NAME-001`. Column 2 is not: it agrees on 29 rows and diverges in both
directions, carrying `G4-ADDR-008` (withdrawn) and `G5-NAME-002` on rows where no rule fires,
while missing `G2-NAME-012` on nine rows where one does. Column 2 is an annotator's column, not a
detector's. This pass reads column 7 for S1_pre and the last column everywhere else.

⚠ A workbook that carries two columns with the same header cannot be read through
`_parse_xlsx`'s row dictionaries: those are keyed by header and keep the **last non-empty** cell
for a repeated header (`api/routes.py:272–278`), so the dict view of `S1_pre`'s `Issues` is a
cell-by-cell blend of columns 2 and 7. No detector reads the `Issues` column, so detection is
unaffected; anything that reads it — including a naïve version of this pass — must go to the
sheet by column index. `tools/eval_report.py:Audit._shipped` does.

---

## 18.3 Issues per stratum, pre versus post

Recomputed at `86d173b`. "Reduction" is `(pre − post) / pre`; a negative reduction is an
increase. `n/a` means the pre count is zero and no ratio exists (Appendix A §3).

### 18.3.1 S1 — academic_research

Records carrying at least one issue: pre 71, post 67.

| code | grp | segment | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|
| `G1-ADDR-001` | G1 | Reduced | 17 | 17 | 0 | 0.0% |
| `G1-ADDR-003` | G1 | Reduced | 28 | 0 | −28 | 100.0% |
| `G1-ADDR-004` | G1 | Reduced | 5 | 0 | −5 | 100.0% |
| `G1-ADDR-006` | G1 | Reduced | 5 | 0 | −5 | 100.0% |
| `G1-CROSS-001` | G1 | Reduced | 3 | 0 | −3 | 100.0% |
| `G1-CROSS-002` | G1 | Reduced | 6 | 2 | −4 | 66.7% |
| `G1-CROSS-003` | G1 | Reduced | 9 | 0 | −9 | 100.0% |
| `G1-NAME-004` | G1 | Reduced | 3 | 0 | −3 | 100.0% |
| `G2-NAME-009` | G2 | Reduced | 1 | 3 | +2 | −200.0% |
| `G2-NAME-012` | G2 | Expected to persist | 23 | 35 | +12 | −52.2% |
| `G2-VAL-002` | G2 | Expected to persist | 1 | 1 | 0 | 0.0% |
| `G3-ADDR-013` | G3 | Expected to persist | 2 | 1 | −1 | 50.0% |
| `G3-NAME-003` | G3 | Reduced | 1 | 1 | 0 | 0.0% |
| `G3-NAME-005` | G3 | Reduced | 2 | 0 | −2 | 100.0% |
| `G5-NAME-001` | G5 | Reduced | 13 | 1 | −12 | 92.3% |
| `G5-NAME-002` | G5 | Reduced | 7 | 1 | −6 | 85.7% |
| `G6-CONFIRM-001` | G6 | Verification | 0 | 24 | +24 | n/a |
| `G7-UNCHANGED-001` | G7 | Verification | 0 | 13 | +13 | n/a |

| group | pre | post | Δ | reduction | | set | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 76 | 19 | −57 | 75.0% | | Reduced | 100 | 25 | −75 | **75.0%** |
| G2 | 25 | 39 | +14 | −56.0% | | Expected to persist | 26 | 37 | +11 | −42.3% |
| G3 | 5 | 2 | −3 | 60.0% | | Verification | 0 | 37 | +37 | n/a |
| G5 | 20 | 2 | −18 | 90.0% | | reduction set (18) | 100 | 25 | −75 | **75.0%** |
| G6 | 0 | 24 | +24 | n/a | | mandatory (7) | 1 | 1 | 0 | 0.0% |
| G7 | 0 | 13 | +13 | n/a | | all live codes | 126 | 99 | −27 | 21.4% |

### 18.3.2 S2 — large_corporate

Records carrying at least one issue: pre 94, post 85.

| code | grp | segment | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|
| `G1-ADDR-001` | G1 | Reduced | 35 | 35 | 0 | 0.0% |
| `G1-ADDR-003` | G1 | Reduced | 28 | 2 | −26 | 92.9% |
| `G1-ADDR-004` | G1 | Reduced | 10 | 0 | −10 | 100.0% |
| `G1-ADDR-006` | G1 | Reduced | 4 | 0 | −4 | 100.0% |
| `G1-CROSS-001` | G1 | Reduced | 1 | 0 | −1 | 100.0% |
| `G1-CROSS-002` | G1 | Reduced | 2 | 0 | −2 | 100.0% |
| `G1-CROSS-003` | G1 | Reduced | 29 | 1 | −28 | 96.6% |
| `G1-NAME-004` | G1 | Reduced | 3 | 0 | −3 | 100.0% |
| `G1-NAME-013` | G1 | Expected to persist | 2 | 0 | −2 | 100.0% |
| `G2-NAME-009` | G2 | Reduced | 9 | 7 | −2 | 22.2% |
| `G2-NAME-012` | G2 | Expected to persist | 0 | 3 | +3 | n/a |
| `G2-VAL-002` | G2 | Expected to persist | 3 | 3 | 0 | 0.0% |
| `G3-ADDR-005` | G3 | Expected to persist | 2 | 0 | −2 | 100.0% |
| `G3-ADDR-013` | G3 | Expected to persist | 3 | 2 | −1 | 33.3% |
| `G3-ADDR-014` | G3 | Expected to persist | 2 | 2 | 0 | 0.0% |
| `G3-CONTACT-010` | G3 | Expected to persist | 2 | 2 | 0 | 0.0% |
| `G3-NAME-003` | G3 | Reduced | 3 | 3 | 0 | 0.0% |
| `G3-NAME-005` | G3 | Reduced | 5 | 0 | −5 | 100.0% |
| `G3-NAME-006` | G3 | Verification | 0 | 1 | +1 | n/a |
| `G5-NAME-001` | G5 | Reduced | 48 | 46 | −2 | 4.2% |
| `G5-NAME-002` | G5 | Reduced | 18 | 5 | −13 | 72.2% |
| `G6-CONFIRM-001` | G6 | Verification | 0 | 40 | +40 | n/a |
| `G7-UNCHANGED-001` | G7 | Verification | 0 | 11 | +11 | n/a |

| group | pre | post | Δ | reduction | | set | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 114 | 38 | −76 | 66.7% | | Reduced | 195 | 99 | −96 | **49.2%** |
| G2 | 12 | 13 | +1 | −8.3% | | Expected to persist | 14 | 12 | −2 | 14.3% |
| G3 | 17 | 10 | −7 | 41.2% | | Verification | 0 | 52 | +52 | n/a |
| G5 | 66 | 51 | −15 | 22.7% | | reduction set (18) | 195 | 99 | −96 | **49.2%** |
| G6 | 0 | 40 | +40 | n/a | | mandatory (7) | 3 | 3 | 0 | 0.0% |
| G7 | 0 | 11 | +11 | n/a | | all live codes | 209 | 163 | −46 | 22.0% |

### 18.3.3 S3 — government_labs

Records carrying at least one issue: pre 94, post 74.

| code | grp | segment | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|
| `G1-ADDR-001` | G1 | Reduced | 21 | 21 | 0 | 0.0% |
| `G1-ADDR-003` | G1 | Reduced | 39 | 0 | −39 | 100.0% |
| `G1-ADDR-004` | G1 | Reduced | 3 | 0 | −3 | 100.0% |
| `G1-ADDR-006` | G1 | Reduced | 15 | 0 | −15 | 100.0% |
| `G1-CROSS-001` | G1 | Reduced | 1 | 0 | −1 | 100.0% |
| `G1-CROSS-002` | G1 | Reduced | 1 | 0 | −1 | 100.0% |
| `G1-CROSS-003` | G1 | Reduced | 11 | 0 | −11 | 100.0% |
| `G1-NAME-004` | G1 | Reduced | 3 | 0 | −3 | 100.0% |
| `G2-NAME-009` | G2 | Reduced | 23 | 26 | +3 | −13.0% |
| `G2-NAME-012` | G2 | Expected to persist | 0 | 3 | +3 | n/a |
| `G3-NAME-003` | G3 | Reduced | 1 | 2 | +1 | −100.0% |
| `G3-NAME-005` | G3 | Reduced | 2 | 0 | −2 | 100.0% |
| `G3-NAME-006` | G3 | Verification | 0 | 1 | +1 | n/a |
| `G5-NAME-001` | G5 | Reduced | 41 | 4 | −37 | 90.2% |
| `G5-NAME-002` | G5 | Reduced | 25 | 6 | −19 | 76.0% |
| `G6-CONFIRM-001` | G6 | Verification | 0 | 46 | +46 | n/a |
| `G7-UNCHANGED-001` | G7 | Verification | 0 | 25 | +25 | n/a |

| group | pre | post | Δ | reduction | | set | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 94 | 21 | −73 | 77.7% | | Reduced | 186 | 59 | −127 | **68.3%** |
| G2 | 23 | 29 | +6 | −26.1% | | Expected to persist | 0 | 3 | +3 | n/a |
| G3 | 3 | 3 | 0 | 0.0% | | Verification | 0 | 72 | +72 | n/a |
| G5 | 66 | 10 | −56 | 84.8% | | reduction set (18) | 186 | 59 | −127 | **68.3%** |
| G6 | 0 | 46 | +46 | n/a | | mandatory (7) | 0 | 0 | 0 | n/a |
| G7 | 0 | 25 | +25 | n/a | | all live codes | 186 | 134 | −52 | 28.0% |

### 18.3.4 S4 — hospital_health

Records carrying at least one issue: pre 94, post 95.

| code | grp | segment | mand | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|
| `G1-ADDR-001` | G1 | Reduced | | 65 | 66 | +1 | −1.5% |
| `G1-ADDR-003` | G1 | Reduced | | 20 | 0 | −20 | 100.0% |
| `G1-ADDR-004` | G1 | Reduced | | 5 | 0 | −5 | 100.0% |
| `G1-ADDR-006` | G1 | Reduced | | 3 | 0 | −3 | 100.0% |
| `G1-ADDR-011` | G1 | Reduced | | 3 | 0 | −3 | 100.0% |
| `G1-CROSS-001` | G1 | Reduced | | 2 | 0 | −2 | 100.0% |
| `G1-CROSS-002` | G1 | Reduced | | 5 | 0 | −5 | 100.0% |
| `G1-CROSS-003` | G1 | Reduced | | 19 | 1 | −18 | 94.7% |
| `G1-NAME-004` | G1 | Reduced | | 3 | 0 | −3 | 100.0% |
| `G1-NAME-013` | G1 | Expected to persist | | 1 | 0 | −1 | 100.0% |
| `G2-NAME-009` | G2 | Reduced | | 18 | 14 | −4 | 22.2% |
| `G2-NAME-012` | G2 | Expected to persist | | 3 | 21 | +18 | −600.0% |
| `G2-VAL-001` | G2 | Expected to persist | **yes** | 0 | 35 | +35 | n/a |
| `G2-VAL-002` | G2 | Expected to persist | **yes** | 1 | 1 | 0 | 0.0% |
| `G2-VAL-007` | G2 | Reduced | **yes** | 0 | 35 | +35 | n/a |
| `G3-NAME-003` | G3 | Reduced | | 3 | 3 | 0 | 0.0% |
| `G3-NAME-005` | G3 | Reduced | | 12 | 0 | −12 | 100.0% |
| `G5-NAME-001` | G5 | Reduced | | 55 | 2 | −53 | 96.4% |
| `G5-NAME-002` | G5 | Reduced | | 22 | 12 | −10 | 45.5% |
| `G6-CONFIRM-001` | G6 | Verification | | 0 | 53 | +53 | n/a |
| `G7-UNCHANGED-001` | G7 | Verification | | 0 | 36 | +36 | n/a |

| group | pre | post | Δ | reduction | | set | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 126 | 67 | −59 | 46.8% | | Reduced | 235 | 133 | −102 | **43.4%** |
| G2 | 22 | 106 | +84 | −381.8% | | Expected to persist | 5 | 57 | +52 | −1040.0% |
| G3 | 15 | 3 | −12 | 80.0% | | Verification | 0 | 89 | +89 | n/a |
| G5 | 77 | 14 | −63 | 81.8% | | reduction set (18) | 235 | 133 | −102 | **43.4%** |
| G6 | 0 | 53 | +53 | n/a | | mandatory (7) | 1 | 71 | +70 | −7000.0% |
| G7 | 0 | 36 | +36 | n/a | | all live codes | 240 | 279 | +39 | −16.2% |

S4 is the one stratum whose total issue count rises. §18.4.3 and §18.3.6 account for it: 35 of
its 100 post records carry no `Name 1`.

### 18.3.5 S5 — smb_residual

Records carrying at least one issue: pre 95, post 94.

| code | grp | segment | mand | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|
| `G1-ADDR-001` | G1 | Reduced | | 65 | 65 | 0 | 0.0% |
| `G1-ADDR-003` | G1 | Reduced | | 24 | 0 | −24 | 100.0% |
| `G1-ADDR-004` | G1 | Reduced | | 7 | 0 | −7 | 100.0% |
| `G1-ADDR-006` | G1 | Reduced | | 3 | 0 | −3 | 100.0% |
| `G1-ADDR-011` | G1 | Reduced | | 1 | 1 | 0 | 0.0% |
| `G1-CROSS-001` | G1 | Reduced | | 3 | 0 | −3 | 100.0% |
| `G1-CROSS-002` | G1 | Reduced | | 5 | 2 | −3 | 60.0% |
| `G1-CROSS-003` | G1 | Reduced | | 19 | 0 | −19 | 100.0% |
| `G1-NAME-004` | G1 | Reduced | | 4 | 0 | −4 | 100.0% |
| `G1-NAME-013` | G1 | Expected to persist | | 4 | 0 | −4 | 100.0% |
| `G2-NAME-009` | G2 | Reduced | | 2 | 3 | +1 | −50.0% |
| `G2-NAME-012` | G2 | Expected to persist | | 0 | 4 | +4 | n/a |
| `G2-VAL-001` | G2 | Expected to persist | **yes** | 0 | 2 | +2 | n/a |
| `G2-VAL-002` | G2 | Expected to persist | **yes** | 3 | 3 | 0 | 0.0% |
| `G2-VAL-007` | G2 | Reduced | **yes** | 0 | 2 | +2 | n/a |
| `G3-ADDR-005` | G3 | Expected to persist | | 1 | 0 | −1 | 100.0% |
| `G3-ADDR-013` | G3 | Expected to persist | | 2 | 1 | −1 | 50.0% |
| `G3-ADDR-014` | G3 | Expected to persist | | 4 | 3 | −1 | 25.0% |
| `G3-CONTACT-010` | G3 | Expected to persist | | 1 | 1 | 0 | 0.0% |
| `G3-NAME-003` | G3 | Reduced | | 8 | 9 | +1 | −12.5% |
| `G3-NAME-005` | G3 | Reduced | | 3 | 0 | −3 | 100.0% |
| `G5-NAME-001` | G5 | Reduced | | 43 | 40 | −3 | 7.0% |
| `G5-NAME-002` | G5 | Reduced | | 8 | 6 | −2 | 25.0% |
| `G6-CONFIRM-001` | G6 | Verification | | 0 | 39 | +39 | n/a |
| `G7-UNCHANGED-001` | G7 | Verification | | 0 | 28 | +28 | n/a |

| group | pre | post | Δ | reduction | | set | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 135 | 68 | −67 | 49.6% | | Reduced | 195 | 128 | −67 | **34.4%** |
| G2 | 5 | 14 | +9 | −180.0% | | Expected to persist | 15 | 14 | −1 | 6.7% |
| G3 | 19 | 14 | −5 | 26.3% | | Verification | 0 | 67 | +67 | n/a |
| G5 | 51 | 46 | −5 | 9.8% | | reduction set (18) | 195 | 128 | −67 | **34.4%** |
| G6 | 0 | 39 | +39 | n/a | | mandatory (7) | 3 | 7 | +4 | −133.3% |
| G7 | 0 | 28 | +28 | n/a | | all live codes | 210 | 209 | −1 | 0.5% |

### 18.3.6 Column gating — why a mandatory count grows without a regression

The five `G2-VAL-*` rules fire only when the column exists in the file **and** is blank
(`enrichment/issue_detection.py:1189–1215`). A code whose column is absent from the pre file has
a pre count of zero by construction, not by cleanliness.

| code | column | S1 pre/post | S2 | S3 | S4 | S5 |
|---|---|---|---|---|---|---|
| `G2-VAL-001` | Name 1 | y/y | y/y | y/y | y/y | y/y |
| `G2-VAL-002` | Postal Code | y/y | y/y | y/y | y/y | y/y |
| `G2-VAL-004` | Region | y/y | y/y | y/y | y/y | y/y |
| `G2-VAL-007` | Search Term 1 | **n**/y | **n**/y | **n**/y | **n**/y | **n**/y |
| `G2-VAL-008` | Country/Region Key | y/y | y/y | y/y | y/y | y/y |

No `pre` workbook carries a `Search Term 1` column. The combined mandatory rise of +74 therefore
splits in two: 37 instances of `G2-VAL-007` are an artefact of the pre files not carrying the
column the rule judges, and 37 instances of `G2-VAL-001` are a real loss of `Name 1` on 37
records (35 in S4, 2 in S5 — §18.4.3). **The mandatory-code line is not comparable pre-to-post on
this dataset**; only its `G2-VAL-001`, `-002`, `-004`, `-008` and `G4-*` members are.

### 18.3.7 S1–S5 combined

| code | grp | segment | pre | post | Δ | reduction |
|---|---|---|---|---|---|---|
| `G1-ADDR-001` | G1 | Reduced | 203 | 204 | +1 | −0.5% |
| `G1-ADDR-003` | G1 | Reduced | 139 | 2 | −137 | 98.6% |
| `G1-ADDR-004` | G1 | Reduced | 30 | 0 | −30 | 100.0% |
| `G1-ADDR-006` | G1 | Reduced | 30 | 0 | −30 | 100.0% |
| `G1-ADDR-011` | G1 | Reduced | 4 | 1 | −3 | 75.0% |
| `G1-CROSS-001` | G1 | Reduced | 10 | 0 | −10 | 100.0% |
| `G1-CROSS-002` | G1 | Reduced | 19 | 4 | −15 | 78.9% |
| `G1-CROSS-003` | G1 | Reduced | 87 | 2 | −85 | 97.7% |
| `G1-NAME-004` | G1 | Reduced | 16 | 0 | −16 | 100.0% |
| `G1-NAME-013` | G1 | Expected to persist | 7 | 0 | −7 | 100.0% |
| `G2-NAME-009` | G2 | Reduced | 53 | 53 | 0 | 0.0% |
| `G2-NAME-012` | G2 | Expected to persist | 26 | 66 | +40 | −153.8% |
| `G2-VAL-001` | G2 | Expected to persist | 0 | 37 | +37 | n/a |
| `G2-VAL-002` | G2 | Expected to persist | 8 | 8 | 0 | 0.0% |
| `G2-VAL-007` | G2 | Reduced | 0 | 37 | +37 | n/a |
| `G3-ADDR-005` | G3 | Expected to persist | 3 | 0 | −3 | 100.0% |
| `G3-ADDR-013` | G3 | Expected to persist | 7 | 4 | −3 | 42.9% |
| `G3-ADDR-014` | G3 | Expected to persist | 6 | 5 | −1 | 16.7% |
| `G3-CONTACT-010` | G3 | Expected to persist | 3 | 3 | 0 | 0.0% |
| `G3-NAME-003` | G3 | Reduced | 16 | 18 | +2 | −12.5% |
| `G3-NAME-005` | G3 | Reduced | 24 | 0 | −24 | 100.0% |
| `G3-NAME-006` | G3 | Verification | 0 | 2 | +2 | n/a |
| `G5-NAME-001` | G5 | Reduced | 200 | 93 | −107 | 53.5% |
| `G5-NAME-002` | G5 | Reduced | 80 | 30 | −50 | 62.5% |
| `G6-CONFIRM-001` | G6 | Verification | 0 | 202 | +202 | n/a |
| `G7-UNCHANGED-001` | G7 | Verification | 0 | 113 | +113 | n/a |

| set | pre | post | Δ | reduction |
|---|---|---|---|---|
| Reduced | 911 | 444 | −467 | **51.3%** |
| Expected to persist | 60 | 123 | +63 | −105.0% |
| Verification | 0 | 317 | +317 | n/a |
| reduction set (18 codes) | 911 | 444 | −467 | **51.3%** |
| mandatory (7 codes) | 8 | 82 | +74 | −925.0% |
| all live codes | 971 | 884 | −87 | 9.0% |

**Headline.** Over 500 records in five strata the reduction-set count falls from 911 to 444, a
51.3% reduction. Per stratum: S1 75.0%, S3 68.3%, S2 49.2%, S4 43.4%, S5 34.4%.

Three codes carry the result and three resist it.

| behaviour | codes | combined |
|---|---|---|
| eliminated outright | `G1-ADDR-004`, `G1-ADDR-006`, `G1-CROSS-001`, `G1-NAME-004`, `G3-NAME-005` | 110 → 0 |
| near-eliminated | `G1-ADDR-003` 98.6%, `G1-CROSS-003` 97.7%, `G1-CROSS-002` 78.9% | 245 → 8 |
| halved | `G5-NAME-002` 62.5%, `G5-NAME-001` 53.5% | 280 → 123 |
| **unmoved** | `G1-ADDR-001` (House Number Embedded in Street) | 203 → 204 |
| **unmoved** | `G2-NAME-009` (Lab Without Department) | 53 → 53 |
| **increases** | `G2-NAME-012` (Research Institution Missing Department) | 26 → 66 |

⚠ `G1-ADDR-001` is in the reduction set with `remedy = "rule"` (§15.3) and is the largest single
code in the corpus, yet its count is 203 before and 204 after: the deterministic rule that is
supposed to fix it does not change the count on any of the five strata (S1 17→17, S2 35→35,
S3 21→21, S4 65→66, S5 65→65). It alone accounts for 204 of the 444 residual reduction-set
instances — 46%. Excluding it, the reduction-set count falls from 708 to 240, a 66.1% reduction.

⚠ `G2-NAME-012` is *Expected to persist* (`remedy = "steward"`), and its count rises by 40. Read
with the `Name 2` fill rates of §18.4, the pipeline empties `Name 2` on more records than it
populates, and each emptied `Name 2` on a research institution raises this code.

---

## 18.4 Completeness KPI

Filled cells over expected cells (the row count), on the name and address block. No accuracy KPI
is computed: no reference of correct values exists for these strata.

**Field set.** `EnrichmentRecord`'s own Name block (`api/models.py:90–110`; the slot tuple is
`RECORD_NAME_FIELDS`, `utils/name_slots.py:43–46`) and Address block (`api/models.py:112–157`),
in declaration order — 16 fields. Measurement is on the validated record, so a column reaches its
field through any accepted alias (`_input_alias_to_field`, `api/routes.py:144–159`).

⚠ The repository's own name/address column map, `COLUMN` in `scripts/ch02_measure.py:110–132`,
is **not** usable at this commit: `scripts/ch02_measure.py` fails at import —
`ImportError: cannot import name '_SUBLOCATION_SLOTS' from 'enrichment.issue_detection'`
(`scripts/ch02_measure.py:64–76`, the name at `:76`). The model's own blocks are used instead. See §18.8.

### 18.4.1 Per stratum, per field

Two of the 16 fields — `Name 5` and `Street 5` — have no column in any `pre` workbook, so the
headline is taken over the 14 carried by both sides.

| field | S1 pre/post | S2 pre/post | S3 pre/post | S4 pre/post | S5 pre/post |
|---|---|---|---|---|---|
| Name 1 | 100/100 | 100/100 | 100/100 | **100/65** | 100/98 |
| Name 2 | 83/78 | 67/50 | 69/68 | 52/39 | 41/27 |
| Name 3 | 43/33 | 23/9 | 26/24 | 9/6 | 4/4 |
| Name 4 | 6/8 | 4/1 | 5/7 | 2/0 | 5/0 |
| Name 5 | 0/0 | 0/0 | 0/1 | 0/0 | 0/0 |
| Street 1 | 97/89 | 86/79 | 90/84 | 96/93 | 95/94 |
| House Number | 64/64 | 36/36 | 58/58 | 17/17 | 19/19 |
| Street 2 | 59/17 | 36/14 | 36/5 | 44/8 | 39/15 |
| Street 3 | 12/0 | 4/0 | 5/0 | 4/0 | 6/0 |
| Street 4 | 1/0 | 1/0 | 2/0 | 0/0 | 0/0 |
| Street 5 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| PO Box | 5/6 | 17/8 | 19/3 | 4/5 | 10/6 |
| Country/Region Key | 100/100 | 100/100 | 100/100 | 100/100 | 100/100 |
| Postal Code | 99/99 | 97/97 | 100/100 | 99/99 | 97/97 |
| City | 100/100 | 100/100 | 100/100 | 100/100 | 100/100 |
| Region | 100/100 | 100/100 | 100/100 | 100/100 | 100/100 |

Values are "filled / 100". `Name 5` and `Street 5` have no pre-side column in any stratum, so
their pre figure is a structural zero; the one populated post cell is S3's.

| stratum | pre | post | Δ |
|---|---|---|---|
| S1 | 869/1400 = 62.1% | 794/1400 = 56.7% | −75 cells, −5.4 pp |
| S2 | 771/1400 = 55.1% | 694/1400 = 49.6% | −77 cells, −5.5 pp |
| S3 | 810/1400 = 57.9% | 749/1400 = 53.5% | −61 cells, −4.4 pp |
| S4 | 727/1400 = 51.9% | 632/1400 = 45.1% | −95 cells, −6.8 pp |
| S5 | 716/1400 = 51.1% | 660/1400 = 47.1% | −56 cells, −4.0 pp |

**Completeness measured on the input schema falls in every stratum.** That is the correct reading
of this KPI and it is not, on its own, a defect: the pipeline's output schema is wider than its
input schema, and most of the loss is content relocated into columns the pre files do not have.

### 18.4.2 Where the content goes — the columns the export adds

Filled cells out of 100, on columns with no pre-side counterpart (Appendix A §5, last table).

| stratum | Suite | Building | Floor | Room | Unit | Mail Stop | Unloading Point | Mail Code | Care Of | Contact | Email | Operating Name | Suggested Name | Domain | Dept Domain | Record Type | ROR ID | LEI ID | Search Term 1 | Search Term 2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | 6 | 21 | 6 | 11 | 0 | 1 | 3 | 20 | 1 | 3 | 7 | 6 | 5 | 99 | 27 | 100 | 86 | 0 | 100 | 69 |
| S2 | 13 | 5 | 0 | 4 | 2 | 2 | 6 | 4 | 8 | 5 | 17 | 22 | 6 | 97 | 1 | 100 | 30 | 34 | 100 | 39 |
| S3 | 5 | 20 | 2 | 4 | 0 | 13 | 6 | 6 | 8 | 4 | 3 | 8 | 15 | 90 | 7 | 100 | 58 | 4 | 100 | 63 |
| S4 | 12 | 2 | 0 | 4 | 1 | 0 | 8 | 6 | 12 | 3 | 2 | 3 | 6 | 35 | 0 | 100 | 11 | 3 | 65 | 25 |
| S5 | 18 | 4 | 0 | 2 | 1 | 0 | 4 | 7 | 7 | 5 | 9 | 21 | 14 | 74 | 0 | 100 | 17 | 7 | 98 | 20 |

`Record Type` is written on every record of every stratum. `Domain` is written on 90–99 of 100 in
S1–S3 and on 35 and 74 in S4 and S5. `Search Term 1` — a mandatory SAP field the pre files do not
carry — is written on 100, 100, 100, 65 and 98.

### 18.4.3 Values lost — filled before, blank after

Joined on `Customer`, all 100 records join in every stratum (Appendix A §5, "values lost").

| stratum | Name 1 | Name 2 | Name 3 | Name 4 | Street 1 | Street 2 | Street 3 | PO Box |
|---|---|---|---|---|---|---|---|---|
| S1 | 0 | 9 | 16 | 1 | 8 | 45 | 12 | 5 |
| S2 | 0 | 19 | 16 | 3 | 8 | 23 | 4 | 17 |
| S3 | 0 | 11 | 6 | 0 | 6 | 32 | 5 | 19 |
| S4 | **35** | 18 | 4 | 2 | 4 | 36 | 4 | 4 |
| S5 | 2 | 20 | 2 | 5 | 2 | 25 | 6 | 10 |

`House Number`, `Country/Region Key`, `Postal Code`, `City` and `Region` lose nothing in any
stratum; `Street 4` loses 1, 1 and 2 cells in S1, S2 and S3 and nothing in S4 and S5. For the Street block the loss is relocation: §18.4.2's `Suite`, `Building`,
`Floor`, `Room`, `Mail Stop`, `Unloading Point` and `Mail Code` are the destination.

⚠ **`Name 1` has no such destination.** S4's 35 records and S5's 2 lose the one field the SAP
load requires. Inspection of the 35 S4 records shows `Error` blank, `Record Type` = `unknown`,
`Flag Codes` = `no-match` and `Flag Reason` = *"Name 1: no source could identify this
organisation — resolve the name manually"*; the original value survives only in a column headed
`Name` (0-based 69), which is **not** in the output schema — `RESPONSE_COLUMNS`
(`api/output_columns.py:22`) has 69 entries and `Name` is not one of them. S4_post is the only
one of the five post workbooks carrying that column. S1_post, conversely, is the only one missing
four schema columns (`Company Code`, `Sales Organization`, `Distribution Channel`, `Division`).

⚠ Neither shape is what `_build_output_xlsx` writes, so **neither S1_post nor S4_post is a
verbatim `POST /enrich/file` output**. Pass 07 §3.1 records that no workbook states its
generating run; this is the observable consequence. Every §18.3 and §18.4 figure for those two
strata is a figure about the shipped artefact, not necessarily about the pipeline at this commit.

---

## 18.5 Improvement index

Per category, the post-run count divided by the mean count across categories. An index above 1.0
is the next-iteration trigger. The specification does not say which partition "category" names,
so both readings are given (Appendix A §6).

### 18.5.1 By issue group, within a stratum

| stratum | G1 | G2 | G3 | G4 | G5 | G6 | G7 | mean | above 1.0 |
|---|---|---|---|---|---|---|---|---|---|
| S1 | **1.34** | **2.76** | 0.14 | 0.00 | 0.14 | **1.70** | 0.92 | 14.14 | G1, G2, G6 |
| S2 | **1.63** | 0.56 | 0.43 | 0.00 | **2.19** | **1.72** | 0.47 | 23.29 | G1, G5, G6 |
| S3 | **1.10** | **1.51** | 0.16 | 0.00 | 0.52 | **2.40** | **1.31** | 19.14 | G1, G2, G6, G7 |
| S4 | **1.68** | **2.66** | 0.08 | 0.00 | 0.35 | **1.33** | 0.90 | 39.86 | G1, G2, G6 |
| S5 | **2.28** | 0.47 | 0.47 | 0.00 | **1.54** | **1.31** | 0.94 | 29.86 | G1, G5, G6 |

G1 is above 1.0 in all five strata and G6 in all five. G4 is 0.00 in all five: no live G4 code
fires on any post workbook. The next iteration is pointed at G1 (which, per §18.3.7, is
`G1-ADDR-001` almost alone) and at the volume of G6 confirmations the pipeline asks a steward
for.

### 18.5.2 By stratum, the stratum being the record category

| stratum | category | post count | index | |
|---|---|---|---|---|
| S1 | academic_research | 99 | 0.56 | |
| S2 | large_corporate | 163 | 0.92 | |
| S3 | government_labs | 134 | 0.76 | |
| S4 | hospital_health | 279 | **1.58** | above |
| S5 | smb_residual | 209 | **1.18** | above |

Mean 176.80. `hospital_health` and `smb_residual` are the two categories above 1.0 — the same two
that carry the `Name 1` losses of §18.4.3.

---

## 18.6 Clustering and golden-record election

### 18.6.1 The ground-truth adapter

`eval/dedup_eval.py` reads `expected_cluster` and `expected_routing` (`eval/dedup_eval.py:45–46`);
no `data/eval/` workbook carries either header. The mapping below is applied once, in
`tools/eval_report.py:_ACTION_TO_ROUTING`, and the rows are then handed to that module's own
`pairwise_metrics`, `business_risk_metrics` and `election_metrics`, so the numbers are the
repository's scorer's.

| `gt_expected_action` | `expected_routing` |
|---|---|
| `MERGE` | `cluster` |
| `REVIEW` | `manual_review` |
| `LINK` | `unique` |
| `DO NOT MERGE` (both forms) | `unique` |
| `UNIQUE` | `unique` |

`expected_cluster` = `gt_dup_group`. The mapping follows the `Method` sheet's own scoring rule:
"for every pair of records, the truth is MERGE if they share a `gt_dup_group`; LINK-not-collapse
if they share `gt_entity_id` but not `gt_dup_group`; NO-MATCH otherwise". Only `MERGE` forms
ground-truth pairs, so `LINK` and either `DO NOT MERGE` can appear only as a false positive.

⚠ One consequence to read carefully: `business_risk_metrics`'s `wrongful_block_candidates`
counts rows whose `expected_routing` is `unique` and whose `is_golden_record` is `False`
(`eval/dedup_eval.py:192–195`). Under this mapping `LINK` rows land in `unique`, so a `LINK`
record correctly connected but not elected counts there.

### 18.6.2 The stress set at the 200-row grain

`data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx` — clustering output, no scoring columns.
Ground truth is on the same sheet; 200 of 200 rows carry it.

| | |
|---|---|
| routing produced | `unique` 160, `cluster` 35, `manual_review` 5 |
| expected routing | `cluster` 103, `unique` 95, `manual_review` 2 |
| ground-truth MERGE groups of size ≥ 2 | 45 |
| clusters produced | 17 |

| metric | value |
|---|---|
| true positives | 21 |
| false positives | **0** |
| false negatives | 54 |
| precision | **1.0000** |
| recall | **0.2800** |
| F1 | 0.4375 |
| ground-truth pairs | 75 |
| predicted pairs | 21 |

Over-merge pairs: **none**, in any class — no `LINK` pair, no trap-group pair, no unrelated pair.
Under-merge pairs: 54, including `13057338+13341783` (Takeda U.S.A.), `13341685+13342488` (UCLA
MRL building), `13368532+13369241` (UT Dallas), `13185655+13350355` (UT Southwestern) and
`13033017+13116126` (North Texas at Dallas). This run is maximally conservative: everything it
merges is correct and it merges just over a quarter of what it should.

### 18.6.3 The stress set at the 183-row grain, with election

`data/eval/stress_200_scored.xlsx`, scored against `dedup_STRESS_200_v1-verified.xlsx` (Data
sheet). 183 of 183 rows carry ground truth.

| | |
|---|---|
| routing produced | `unique` 88, `cluster` 78, `manual_review` 17 |
| expected routing | `cluster` 95, `unique` 86, `manual_review` 2 |
| ground-truth MERGE groups of size ≥ 2 | 41 |
| clusters produced | 42 |

| metric | value |
|---|---|
| true positives | 50 |
| false positives | 9 |
| false negatives | 21 |
| precision | **0.8475** |
| recall | **0.7042** |
| F1 | **0.7692** |
| ground-truth pairs | 71 |
| predicted pairs | 59 |

**Over-merges: 9 pairs, all of one kind** — `link (same entity, different site)`: two records of
the same organisation at different delivery points collapsed into one cluster. Examples:
`13132835+13225308`, `13345790+13345937`, `13115460+13129200`, `13144897+13223387`,
`13348301+13364371`, `13113215+13128534`. **No pair crosses a trap group** and no pair joins two
unrelated organisations. The nine trap families of the `Traps` sheet — `T-CONTRACOSTA`, `T-HP`,
`T-LEE`, `T-LICKING`, `T-ORLANDO`, `T-STANFORD`, `T-TAKEDA`, `T-UCLA`, `T-UTDALLAS` — are held
apart in both runs. Every false positive is a site-granularity error, not an identity error.

**Under-merges: 21 pairs**, including `13057338+13341783`, `13341685+13342488`,
`13056457+13134277`, `13334236+13335826`, `13334046+13336374`, `13033988+13135468`.

Election:

| metric | value |
|---|---|
| clusters | 42 |
| elections | 33 |
| `manual_review` rows | 25 |
| clusters decided by tie-break | 3 — `c_2759839874b4`, `c_7b0f5107ff65`, `c_cb3fed562da7` |

| business risk | count | row ids |
|---|---|---|
| `wrongful_block_candidates` | 4 | 13128534, 13129200, 13144897, 13225308 |
| `competing_goldens` | 15 | 13033121, 13056457, 13057338, 13127964, 13138597, 13147440, 13162837, 13225238, … |
| `uncertainty_upgrades` | 0 | — |

The four `wrongful_block_candidates` are the same records that appear in the nine `LINK`
over-merges: they are a consequence of the mapping in §18.6.1, not four independent failures.

**Scoring election agreement.** ⚠ MEASUREMENT REQUIRED. No artefact in this repository names the
record that *should* win an election in any cluster: the stress workbooks' ground truth is
`gt_dup_group`, `gt_entity_id`, `gt_trap_group`, `gt_expected_action`, `gt_match_signal` and
`gt_why` — a clustering reference, not an election reference. Producing an agreement figure needs
a per-cluster expected-golden column. What can be said is what the election did: 42 clusters, 33
elected, 25 rows routed to `manual_review`, 3 clusters decided by the tie-break rather than the
score, all 183 rows scored under weights version `3147cac47910` and reference year 2026.

⚠ The election is over partly synthetic evidence. `stress_200_scored.xlsx`'s own `Dummy_Fill`
sheet records that 83 of the 183 customers carry real QLIC scoring values and 100 carry
deterministic dummies seeded `sha256("bbio-dummy-v1:" + Customer)`. Clustering does not read those
columns and is unaffected; every election and business-risk figure above is affected.

### 18.6.4 Two runs, two answers

| | 200-row grain | 183-row grain |
|---|---|---|
| source | `dedup_STRESS_200_v1_enriched_dedup.xlsx` | `stress_200_scored.xlsx` |
| rows | 200 | 183 |
| clusters produced | 17 | 42 |
| precision | 1.0000 | 0.8475 |
| recall | 0.2800 | 0.7042 |
| F1 | 0.4375 | 0.7692 |
| over-merges | 0 | 9 (all `LINK`) |
| settings recorded | none | `Run` sheet: `p2-dedup-v8`, `MDM-Apoorva-gpt-5.4`, all three `DEDUP_V2_*` flags true, `fixture_cache off`, 111 blocks, 39 LLM calls |

⚠ These are two different runs over overlapping but unequal populations (the 183 are a strict
subset of the 200 — Pass 07 §3.3) and only one records its settings. The difference between them
is not attributable from repository evidence: it is consistent with a configuration change, a
model change, or the 17-record population difference, and nothing recorded distinguishes those.
Neither run states the commit it ran at.

### 18.6.5 The S5 test set

⚠ MEASUREMENT REQUIRED. `S5_post.xlsx` carries the clustering **annotation** — `cluster_id` on 23
of 100 records forming 11 clusters (ten pairs and one triple) and `cluster_role` distributed as
`same entity, same site` 14, `same entity, different sites` 5, `same street, different building`
2, `same street, one building value missing` 2 — but **no clustering output column**: no
`Cluster ID`, no `Routing`, no `election_status`. Expected-versus-found, over-merges,
under-merges, `manual_review` and `unique` counts cannot be computed for this stratum. Producing
them needs `POST /api/dedup/cluster` over `S5_post.xlsx`. The same holds for S1–S4, which carry
the identical annotation block.

⚠ A trap for anyone attempting it with the existing harness: `eval/dedup_eval.py` maps the header
`cluster_id` onto its **output** field (`eval/dedup_eval.py:47`), so pointing it at a stratum
workbook reads the annotation as the run's answer and scores the ground truth against itself.

---

## 18.7 Cost

### 18.7.1 What the repository records for an enrichment run

`logs/runs/determinism_S1_327ee53.json` is the only per-run counter set in the repository that
names a stratum; ⚠ the file name is the only evidence that it is S1 — the artefact carries no
input path. It is a `tools/run_diff.py --json` report and its `summary_run1` is the run summary
`scripts/run_batch.py` writes.

| counter | value | per record |
|---|---|---|
| `total` | 100 | |
| `tier1_resolved` | 17 | 0.17 |
| `lei_attempts` | 3 | 0.03 |
| `tier2a_population_count` | 1 | 0.01 |
| `tier2a_verification_count` | 0 | 0.00 |
| `tier2b_count` | 0 | 0.00 |
| `tier3_count` | 19 | 0.19 |
| `page_reads_attempted` | 13 | 0.13 |
| `wikidata_queried` | 16 | 0.16 |
| `liveness_ror_queried` | 57 | 0.57 |
| `domain_from_serp` | 2 | 0.02 |
| `contact_lookup_attempted` | 1 | 0.01 |
| `evidence_network_calls` | 0 | 0.00 |
| `evidence_cache_hits` | 1577 | 15.77 |
| `evidence_cache_frozen` | True | |
| `processing_time_ms` | 442 | 4.42 |

⚠ `evidence_network_calls = 0` and `evidence_cache_frozen = true`: this run replayed a recorded
cache, and `evidence_network_calls_by_namespace` is `{}`. The table therefore measures the work
the pipeline *asked for* — how many records reached each tier, how many page reads and Wikidata
and ROR liveness queries it wanted — not calls billed on that run. On a cold run each of those
counters is a call.

Per record per tier for this stratum: Tier 1 resolves 0.17, Tier 2A populates 0.01 and verifies
0.00, Tier 2B 0.00, Tier 3 0.19. ⚠ MEASUREMENT REQUIRED for S2–S5: no run summary exists for any
other stratum.

### 18.7.2 What the repository records for a dedup run

`data/eval/stress_200_scored.xlsx :: Run`:

| setting | value |
|---|---|
| `prompt_version` | `p2-dedup-v8` |
| `model` / `model_version` | `MDM-Apoorva-gpt-5.4` |
| `DEDUP_V2_BLOCKING` / `_NAME2` / `_ID_CONFLICT` | `true` / `true` / `true` |
| `fixture_cache` | `off` |
| `rows_in` | 183 |
| `blocks` | 111 |
| `llm_calls` | **39** |
| `rows_clustered` / `rows_unique` / `rows_manual_review` | 78 / 88 / 17 |

0.213 LLM calls per record: 39 calls over 183 rows, and 0.35 calls per block — blocking produced
111 candidate blocks and 39 of them reached the model. `fixture_cache off` means those 39 calls
went to the network and are not reproducible from anything in the repository.

### 18.7.3 Recorded evidence caches

Unique keys per namespace under `logs/cache/`. A key is a distinct question, not a call: a
repeated question is one key. ⚠ No cache directory records the batch it was recorded on, so none
is attributable to a stratum.

| cache dir | fetch | llm | page_reads | registry | serp | wikidata |
|---|---|---|---|---|---|---|
| `cutu_suzu_1` | 680 | 337 | 15 | 259 | 142 | 39 |
| `cutu_suzu_2` | 1594 | 1163 | 129 | 1944 | 642 | 390 |
| `cutu_suzu_3` | 707 | 339 | 15 | 256 | 147 | 39 |
| `cutu_suzu_4` | 695 | 338 | 15 | 256 | 145 | 39 |
| `cutu_suzu_5` | 400 | 292 | 56 | 602 | 171 | 119 |
| `cutu_suzu_6` | 604 | 383 | 36 | 678 | 218 | 162 |
| `cutu_suzu_7` | 420 | 313 | 14 | 396 | 151 | 101 |
| `grounded` | 424 | 282 | 54 | 695 | 181 | 145 |
| `log_A` | 78 | 115 | 23 | 401 | 61 | 51 |
| `log_B` | 74 | 116 | 23 | 401 | 62 | 96 |
| `log_C` | 237 | 243 | 40 | 576 | 139 | 157 |
| `log_Z` | 78 | 115 | 23 | 402 | 61 | 51 |

### 18.7.4 Cost in currency

⚠ MEASUREMENT REQUIRED. No price, rate card or token-price constant exists anywhere in this
repository. `config.py` carries the SerpAPI key and the fallback policy (`config.py:195–202`,
`:218`) and no rate. A currency figure needs three inputs from outside the repository: the
SerpAPI plan's per-search price, the Azure OpenAI per-1K-token price for the deployment named on
the `Run` sheet (`MDM-Apoorva-gpt-5.4`), and a token count per call — which no artefact records
either. An Application Insights cost export would supply all three.

---

## 18.8 Determinism and run-to-run stability

`tools/run_diff.py` compares two `scripts/run_batch.py --json` artefacts
(`tools/run_diff.py:327–328`) and deliberately refuses the enriched XLSX (`:16–18`). Two kinds of
comparison are available in this repository and they answer different questions.

### 18.8.1 The determinism gate proper — two runs at one commit

Three stored `run_diff --json` reports exist, one per commit, each over two frozen runs of the
S1 batch. The two *run* artefacts behind each are not in the repository, only `run_diff`'s report
of them, so those diffs cannot be re-executed; `tools/eval_report.py` re-renders each report
through `tools.run_diff.render`, so the text below is `run_diff`'s own, not a paraphrase.

| artefact | rows run 1 | rows run 2 | compared | rows differing | cell differences | verdict | run 2 network calls |
|---|---|---|---|---|---|---|---|
| `logs/runs/determinism_S1.json` | 100 | 100 | 100 | 0 | 0 | PASS | 0 |
| `logs/runs/determinism_S1_327ee53.json` | 100 | 100 | 100 | 0 | 0 | PASS | 0 |
| `logs/runs/determinism_S1_f57782f.json` | 100 | 100 | 100 | 0 | 0 | PASS | 0 |

Verbatim, for `determinism_S1_327ee53.json` (all three are identical in every printed field):

```
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 0
```

The gate passes: two runs of the 100-record S1 batch are identical across every column of
`api.output_columns.RESPONSE_COLUMNS`, with no row present in only one run, and both runs read
the same frozen evidence (`run 2 network calls: 0`).

⚠ **The evidence is 53 commits old.** `327ee53` and `f57782f` are both dated 2026-09-02;
`git rev-list --count 327ee53..86d173b` is 53. `logs/runs/determinism_S1.json` names no commit at
all — `eval/out/RUNS.md` attributes it to `d3a3cfc`. The determinism gate has not been run at
`86d173b`. Re-running it needs two `scripts/run_batch.py --json` runs of the S1 batch against a
frozen cache (`--frozen --cache-dir`), then `python tools/run_diff.py run1.json run2.json`. The
caches that make a frozen replay possible are gitignored (Pass 07 §4.3, warning 07-5), so this
cannot be done from a fresh clone.

### 18.8.2 Run-to-run stability across commits — `run_diff` executed at this commit

`eval/out/` holds tracked `scripts/run_batch.py --json` artefacts of frozen runs of the S1, S4,
S5 and t100 batches, keyed by generating commit: bare files at `d3a3cfc`, `eval/out/f57782f/`,
`eval/out/327ee53/` (Pass 07 §6.1). The record ids of the S1, S4 and S5 artefacts are **exactly
the 100 `Customer` values** of `S1_pre.xlsx`, `S4_pre.xlsx` and `S5_pre.xlsx` respectively — 100
of 100 in each case (Appendix A §10). `run_diff` therefore runs, here, at this commit, on real
batches; the four diffs below are executed, not recalled.

Each pair is the same batch at two **different** commits, so a difference is the effect of a code
change, not evidence of non-determinism. What it measures is how far the shipped output moved
between two commits.

| pair | rows compared | rows differing | cell differences | verdict |
|---|---|---|---|---|
| S1 `d3a3cfc` → `f57782f` | 100 | **0** | **0** | PASS |
| S1 `f57782f` → `327ee53` | 100 | 18 | 39 | FAIL |
| S4 `d3a3cfc` → `f57782f` | 100 | 4 | 30 | FAIL |
| S4 `f57782f` → `327ee53` | 100 | 40 | 122 | FAIL |

`run 2 network calls: 0` on all four: every run read frozen evidence, so no difference is
attributable to a changed external answer.

The columns that move, S1 `f57782f` → `327ee53` (39 cell differences over 18 rows):
`Search Term 1` 7, `Suggested Name` 5, `Suggestion Source` 5, `Name 2` 3, `Search Term 2` 3,
`Street 1` 2, `Flag Codes` 2, `Flagged Fields` 2, `Flag Reason` 2, then `Street 2`,
`Flag for Review`, `Care Of`, `Suite`, `Name 1 Provenance`, `Operating Name`,
`Operating Name Provenance`, `Domain Provenance` at 1 each.

S4 `f57782f` → `327ee53` (122 cell differences over 40 rows): `Search Term 1` 22,
`Flag Reason` 13, `Flagged Fields` 12, `Name 1 Provenance` 11, `Suggested Name` 11,
`Suggestion Source` 11, `Flag Codes` 10, `Flag for Review` 6, `Name 2 Provenance` 5,
`Name 2` 3, then eleven columns at 2 or 1 — including `Name 1`, `Record Type`, `ROR ID`.

Two readings follow. `Search Term 1`, the review-metadata block (`Flag *`) and the
suggestion/provenance columns carry most of the movement, which is what a change to flagging and
naming rules looks like. But `Name 1`, `Record Type`, `ROR ID` and `Domain` also move on S4 —
identity fields, on which a change is a different decision about which organisation the record
is. ⚠ Neither `RUNS.md` nor any commit message in the repository states which of the 53
intervening commits produced which of these movements; attributing them is not possible from
repository evidence.

---

## 18.9 Cross-check: the shipped `post` workbooks against the recorded runs

`eval/out/327ee53/` gives a **second post-enrichment state** for S1, S4 and S5 — one that names
the commit it was produced at, which no `post` workbook does. `tools/eval_report.py` rebuilds each
recorded result through the shipped output schema and the shipped cell adapter — `RESPONSE_COLUMNS`
(`api/output_columns.py:22`) and `_cell` (`api/routes.py:372–382`) — so the row handed to the
detector is the row `_build_output_xlsx` would have written, and then audits it exactly as it
audits a workbook (Appendix A §10). A difference below is a difference between two artefacts made
at two commits; it is not by itself a regression at either.

| code | S1 run / S1_post | S4 run / S4_post | S5 run / S5_post |
|---|---|---|---|
| `G1-ADDR-001` | 17 / 17 | 66 / 66 | 65 / 65 |
| `G1-ADDR-011` | — | — | 1 / 1 |
| `G1-CROSS-002` | 1 / 2 | — | 1 / 2 |
| `G1-CROSS-003` | — | 0 / 1 | — |
| `G2-NAME-009` | 3 / 3 | 12 / 14 | 3 / 3 |
| `G2-NAME-012` | 33 / 35 | 11 / **21** | 4 / 4 |
| `G2-VAL-001` | — | **0 / 35** | 2 / 2 |
| `G2-VAL-002` | 1 / 1 | 1 / 1 | 3 / 3 |
| `G2-VAL-007` | — | **0 / 35** | 2 / 2 |
| `G3-ADDR-013` | 1 / 1 | — | 1 / 1 |
| `G3-ADDR-014` | — | — | 3 / 3 |
| `G3-CONTACT-010` | — | — | 1 / 1 |
| `G3-NAME-003` | 1 / 1 | 4 / 3 | 9 / 9 |
| `G5-NAME-001` | 1 / 1 | 5 / 2 | 40 / 40 |
| `G5-NAME-002` | 2 / 1 | 4 / **12** | 4 / 6 |
| `G6-CONFIRM-001` | 37 / 24 | 38 / 53 | 46 / 39 |
| `G7-UNCHANGED-001` | 0 / 13 | 0 / 36 | 3 / 28 |
| **total** | 97 / 99 | **141 / 279** | 188 / 209 |

And the completeness field set:

| field | S1 run / S1_post | S4 run / S4_post | S5 run / S5_post |
|---|---|---|---|
| Name 1 | 100 / 100 | **100 / 65** | 98 / 98 |
| Name 2 | 80 / 78 | 46 / 39 | 28 / 27 |
| Name 3 | 28 / 33 | 9 / 6 | 4 / 4 |
| Name 4 | 7 / 8 | — | 1 / 0 |
| Street 1 | 90 / 89 | 93 / 93 | 94 / 94 |
| House Number | 64 / 64 | 17 / 17 | 19 / 19 |
| Street 2 | 16 / 17 | 8 / 8 | 14 / 15 |
| PO Box | 6 / 6 | 5 / 5 | 6 / 6 |
| Country / Postal / City / Region | identical | identical | identical |

Three results.

**S1 and S5 reconcile.** Every quality code agrees to within 2 instances and every completeness
figure to within 5 cells. The shipped `S1_post.xlsx` and `S5_post.xlsx` are consistent with what
the pipeline recorded itself producing on the same 100 records.

**S4 does not.** The recorded run at `327ee53` fills `Name 1` on all 100 records and raises
`G2-VAL-001` zero times; `S4_post.xlsx` fills it on 65 and raises `G2-VAL-001` and `G2-VAL-007`
35 times each. Its total issue count is 279 against the recorded run's 141. ⚠ Combined with the
non-schema `Name` column carrying the original value on exactly those 35 records (§18.4.3),
`S4_post.xlsx` is best read as an artefact of a different process from the one `eval/out/` records.
The S4 figures in §18.3.4, §18.4 and §18.5 describe that artefact; they are not corroborated as a
description of the pipeline.

**`G7-UNCHANGED-001` is not comparable across the two states, and this is a code change, not a
data difference.** G7 is raised only from the `Flag Codes` column, by the tokens
`low-confidence-unchanged`, `no-match` and `person-unresolved`
(`enrichment/issue_detection.py:1555–1558`). In the `327ee53` artefacts those tokens are nearly
absent — S1 raises none, S4 none, S5 three (`no-match` 1, `person-unresolved` 2, matching its
G7 count of 3); the vocabulary those runs actually emitted is `unverified-inference`,
`domain-unverified`, `relocated-unverified`, `registry-location-mismatch`, `entity-superseded`,
`dept-via-lab`. The `post` workbooks raise G7 13, 36 and 28 times. The flag vocabulary changed
between `327ee53` and whatever produced the exports; the G6/G7 lines of §18.3 measure the
exports' vocabulary, not `327ee53`'s.

---

## 18.10 Defects this pass found

| # | what | evidence |
|---|---|---|
| 18-1 | `scripts/ch02_measure.py` does not import at this commit: `cannot import name '_SUBLOCATION_SLOTS' from 'enrichment.issue_detection'` (`scripts/ch02_measure.py:64–76`). Pass 07 §1.1 lists it as a live harness script. | §18.4 |
| 18-2 | `S4_post.xlsx` has no `Name 1` on 35 of 100 records; the value survives only in a non-schema column headed `Name`. `G2-VAL-001` (mandatory) fires on all 35. | §18.4.3, §18.9 |
| 18-3 | `S1_post.xlsx` is missing four `RESPONSE_COLUMNS` columns (`Company Code`, `Sales Organization`, `Distribution Channel`, `Division`); `S4_post.xlsx` carries one extra. Neither is a verbatim `POST /enrich/file` output. | §18.4.3 |
| 18-4 | `G1-ADDR-001` — the largest single code in the corpus, `remedy = "rule"`, in the reduction set — moves 203 → 204 across 500 records. | §18.3.7 |
| 18-5 | `G2-NAME-012` rises 26 → 66; the pipeline empties `Name 2` on more research-institution records than it fills. | §18.3.7, §18.4.1 |
| 18-6 | No `pre` workbook carries `Search Term 1`, so `G2-VAL-007` cannot fire pre and the mandatory-code line is not comparable pre-to-post. | §18.3.6 |
| 18-7 | `_parse_xlsx` merges columns sharing a header, keeping the last non-empty cell (`api/routes.py:272–278`); `S1_pre.xlsx` has two `Issues` columns that disagree on 56 rows, so its dict view is a blend of both. | §18.2 |
| 18-8 | `eval/dedup_eval.py` writes `eval_report.json` into the working directory by default (`eval/dedup_eval.py:296`, written at `:302`) — a scorer with a side effect. | §18.6.1 |
| 18-9 | `eval/dedup_eval.py` reads the header `cluster_id` as its output field, so it silently scores a stratum workbook's annotation against itself. | §18.6.5 |
| 18-10 | The two dedup runs in `data/eval/` disagree sharply (precision 1.00 / recall 0.28 against 0.85 / 0.70) and only one records its settings; neither records a commit. | §18.6.4 |
| 18-11 | `S4_post.xlsx` is not corroborated by the recorded run of the same 100 records at `327ee53`: that run fills `Name 1` on 100 and raises `G2-VAL-001` zero times; the workbook fills 65 and raises it 35 times. Total issue count 141 against 279. | §18.9 |
| 18-12 | `eval/out/RUNS.md` contradicts itself on S5: its heading says "S1, S4 and **S5** were run and scored", its run table says S5 is "not on this machine", and `eval/out/327ee53/` holds `S5_results.json`. | §18.8.2, Pass 08 G-62 |
| 18-13 | Between `f57782f` and `327ee53`, on frozen evidence and the same S4 batch, 40 of 100 rows change, including the identity fields `Name 1`, `Record Type`, `ROR ID` and `Domain`. No commit message or record attributes the change. | §18.8.2 |
| 18-14 | `G7-UNCHANGED-001` counts are not comparable between the `327ee53` artefacts and the `post` workbooks: the flag-code vocabulary the runs emit changed, and G7 is raised only from `Flag Codes`. | §18.9 |

---

## Appendix A — invocation and full output

```
$ git rev-parse HEAD
86d173b8a4d715a619b0a2656986c145da7fa81e
$ git rev-parse --abbrev-ref HEAD
feature/llm-fixes
$ python3 -c "import sys, openpyxl, pydantic; print(sys.version.split()[0], openpyxl.__version__, pydantic.VERSION)"
3.9.6 3.1.5 2.12.5
$ python3 tools/eval_report.py
```

```

==============================================================================
1 — FILE PAIRING AND ROW COUNTS
==============================================================================
  stratum  pre file               rows  cols  records  post file               rows  cols  records
  -------  ---------------------  ----  ----  -------  ----------------------  ----  ----  -------
  S1       data/eval/S1_pre.xlsx  100   42    100      data/eval/S1_post.xlsx  100   83    100    
  S2       data/eval/S2_pre.xlsx  100   42    100      data/eval/S2_post.xlsx  100   84    100    
  S3       data/eval/S3_pre.xlsx  100   42    100      data/eval/S3_post.xlsx  100   84    100    
  S4       data/eval/S4_pre.xlsx  100   42    100      data/eval/S4_post.xlsx  100   85    100    
  S5       data/eval/S5_pre.xlsx  100   42    100      data/eval/S5_post.xlsx  100   84    100    

  Every stratum resolves to both files; no stratum is skipped.

==============================================================================
2 — VOCABULARY
==============================================================================
  ISSUE_CATALOGUE declared : 43
  live / emittable         : 33  {'live': 33, 'withdrawn': 10}
  quality groups           : ['G1', 'G2', 'G3', 'G4', 'G5']
  verification groups      : ['G6', 'G7']  (never counted in a reduction figure)

  Reduction set (segment == 'Reduced'): 18 codes
    G1  (9)  G1-CROSS-001, G1-CROSS-002, G1-CROSS-003, G1-ADDR-001, G1-ADDR-003, G1-ADDR-004, G1-ADDR-006, G1-ADDR-011, G1-NAME-004
    G2  (3)  G2-VAL-007, G2-VAL-008, G2-NAME-009
    G3  (3)  G3-NAME-003, G3-NAME-005, G3-ADDR-012
    G4  (1)  G4-ADDR-027
    G5  (2)  G5-NAME-001, G5-NAME-002

  Mandatory (DATAshaper Error) live codes: 7
    G2-VAL-001  Name 1 Missing  [Expected to persist]
    G2-VAL-002  Postal Code Missing  [Expected to persist]
    G2-VAL-004  Region Missing  [Expected to persist]
    G2-VAL-007  Search Term 1 Missing  [Reduced]
    G2-VAL-008  Country Missing  [Reduced]
    G4-NAME-015  Name Overflow Beyond the Name Block  [Expected to persist]
    G4-ADDR-027  Country Code Not ISO 2-letter  [Reduced]

  DedupIssue codes from /api/dedup/score are cluster-quality
  diagnostics and are reported in section 7 only. They are never
  summed with catalogue codes.

==============================================================================
3 — ISSUES PER STRATUM, PRE VS POST (recomputed at this commit)
==============================================================================

--- S1 ------------------------------------------------------------------------
  rows: pre 100  post 100
  records carrying >=1 issue: pre 71  post 67

  code              grp  segment              mand  pre  post  delta  reduction
  ----------------  ---  -------------------  ----  ---  ----  -----  ---------
  G1-ADDR-001       G1   Reduced                    17   17    0      0.0%     
  G1-ADDR-003       G1   Reduced                    28   0     -28    100.0%   
  G1-ADDR-004       G1   Reduced                    5    0     -5     100.0%   
  G1-ADDR-006       G1   Reduced                    5    0     -5     100.0%   
  G1-CROSS-001      G1   Reduced                    3    0     -3     100.0%   
  G1-CROSS-002      G1   Reduced                    6    2     -4     66.7%    
  G1-CROSS-003      G1   Reduced                    9    0     -9     100.0%   
  G1-NAME-004       G1   Reduced                    3    0     -3     100.0%   
  G2-NAME-009       G2   Reduced                    1    3     2      -200.0%  
  G2-NAME-012       G2   Expected to persist        23   35    12     -52.2%   
  G2-VAL-002        G2   Expected to persist  yes   1    1     0      0.0%     
  G3-ADDR-013       G3   Expected to persist        2    1     -1     50.0%    
  G3-NAME-003       G3   Reduced                    1    1     0      0.0%     
  G3-NAME-005       G3   Reduced                    2    0     -2     100.0%   
  G5-NAME-001       G5   Reduced                    13   1     -12    92.3%    
  G5-NAME-002       G5   Reduced                    7    1     -6     85.7%    
  G6-CONFIRM-001    G6   Verification               0    24    24     n/a      
  G7-UNCHANGED-001  G7   Verification               0    13    13     n/a      

  group  pre  post  delta  reduction
  -----  ---  ----  -----  ---------
  G1     76   19    -57    75.0%    
  G2     25   39    14     -56.0%   
  G3     5    2     -3     60.0%    
  G5     20   2     -18    90.0%    
  G6     0    24    24     n/a      
  G7     0    13    13     n/a      

  set                       pre  post  delta  reduction
  ------------------------  ---  ----  -----  ---------
  Reduced                   100  25    -75    75.0%    
  Expected to persist       26   37    11     -42.3%   
  Verification              0    37    37     n/a      
  reduction set (18 codes)  100  25    -75    75.0%    
  mandatory (7 codes)       1    1     0      0.0%     
  all live codes            126  99    -27    21.4%    

--- S2 ------------------------------------------------------------------------
  rows: pre 100  post 100
  records carrying >=1 issue: pre 94  post 85

  code              grp  segment              mand  pre  post  delta  reduction
  ----------------  ---  -------------------  ----  ---  ----  -----  ---------
  G1-ADDR-001       G1   Reduced                    35   35    0      0.0%     
  G1-ADDR-003       G1   Reduced                    28   2     -26    92.9%    
  G1-ADDR-004       G1   Reduced                    10   0     -10    100.0%   
  G1-ADDR-006       G1   Reduced                    4    0     -4     100.0%   
  G1-CROSS-001      G1   Reduced                    1    0     -1     100.0%   
  G1-CROSS-002      G1   Reduced                    2    0     -2     100.0%   
  G1-CROSS-003      G1   Reduced                    29   1     -28    96.6%    
  G1-NAME-004       G1   Reduced                    3    0     -3     100.0%   
  G1-NAME-013       G1   Expected to persist        2    0     -2     100.0%   
  G2-NAME-009       G2   Reduced                    9    7     -2     22.2%    
  G2-NAME-012       G2   Expected to persist        0    3     3      n/a      
  G2-VAL-002        G2   Expected to persist  yes   3    3     0      0.0%     
  G3-ADDR-005       G3   Expected to persist        2    0     -2     100.0%   
  G3-ADDR-013       G3   Expected to persist        3    2     -1     33.3%    
  G3-ADDR-014       G3   Expected to persist        2    2     0      0.0%     
  G3-CONTACT-010    G3   Expected to persist        2    2     0      0.0%     
  G3-NAME-003       G3   Reduced                    3    3     0      0.0%     
  G3-NAME-005       G3   Reduced                    5    0     -5     100.0%   
  G3-NAME-006       G3   Verification               0    1     1      n/a      
  G5-NAME-001       G5   Reduced                    48   46    -2     4.2%     
  G5-NAME-002       G5   Reduced                    18   5     -13    72.2%    
  G6-CONFIRM-001    G6   Verification               0    40    40     n/a      
  G7-UNCHANGED-001  G7   Verification               0    11    11     n/a      

  group  pre  post  delta  reduction
  -----  ---  ----  -----  ---------
  G1     114  38    -76    66.7%    
  G2     12   13    1      -8.3%    
  G3     17   10    -7     41.2%    
  G5     66   51    -15    22.7%    
  G6     0    40    40     n/a      
  G7     0    11    11     n/a      

  set                       pre  post  delta  reduction
  ------------------------  ---  ----  -----  ---------
  Reduced                   195  99    -96    49.2%    
  Expected to persist       14   12    -2     14.3%    
  Verification              0    52    52     n/a      
  reduction set (18 codes)  195  99    -96    49.2%    
  mandatory (7 codes)       3    3     0      0.0%     
  all live codes            209  163   -46    22.0%    

--- S3 ------------------------------------------------------------------------
  rows: pre 100  post 100
  records carrying >=1 issue: pre 94  post 74

  code              grp  segment              mand  pre  post  delta  reduction
  ----------------  ---  -------------------  ----  ---  ----  -----  ---------
  G1-ADDR-001       G1   Reduced                    21   21    0      0.0%     
  G1-ADDR-003       G1   Reduced                    39   0     -39    100.0%   
  G1-ADDR-004       G1   Reduced                    3    0     -3     100.0%   
  G1-ADDR-006       G1   Reduced                    15   0     -15    100.0%   
  G1-CROSS-001      G1   Reduced                    1    0     -1     100.0%   
  G1-CROSS-002      G1   Reduced                    1    0     -1     100.0%   
  G1-CROSS-003      G1   Reduced                    11   0     -11    100.0%   
  G1-NAME-004       G1   Reduced                    3    0     -3     100.0%   
  G2-NAME-009       G2   Reduced                    23   26    3      -13.0%   
  G2-NAME-012       G2   Expected to persist        0    3     3      n/a      
  G3-NAME-003       G3   Reduced                    1    2     1      -100.0%  
  G3-NAME-005       G3   Reduced                    2    0     -2     100.0%   
  G3-NAME-006       G3   Verification               0    1     1      n/a      
  G5-NAME-001       G5   Reduced                    41   4     -37    90.2%    
  G5-NAME-002       G5   Reduced                    25   6     -19    76.0%    
  G6-CONFIRM-001    G6   Verification               0    46    46     n/a      
  G7-UNCHANGED-001  G7   Verification               0    25    25     n/a      

  group  pre  post  delta  reduction
  -----  ---  ----  -----  ---------
  G1     94   21    -73    77.7%    
  G2     23   29    6      -26.1%   
  G3     3    3     0      0.0%     
  G5     66   10    -56    84.8%    
  G6     0    46    46     n/a      
  G7     0    25    25     n/a      

  set                       pre  post  delta  reduction
  ------------------------  ---  ----  -----  ---------
  Reduced                   186  59    -127   68.3%    
  Expected to persist       0    3     3      n/a      
  Verification              0    72    72     n/a      
  reduction set (18 codes)  186  59    -127   68.3%    
  mandatory (7 codes)       0    0     0      n/a      
  all live codes            186  134   -52    28.0%    

--- S4 ------------------------------------------------------------------------
  rows: pre 100  post 100
  records carrying >=1 issue: pre 94  post 95

  code              grp  segment              mand  pre  post  delta  reduction
  ----------------  ---  -------------------  ----  ---  ----  -----  ---------
  G1-ADDR-001       G1   Reduced                    65   66    1      -1.5%    
  G1-ADDR-003       G1   Reduced                    20   0     -20    100.0%   
  G1-ADDR-004       G1   Reduced                    5    0     -5     100.0%   
  G1-ADDR-006       G1   Reduced                    3    0     -3     100.0%   
  G1-ADDR-011       G1   Reduced                    3    0     -3     100.0%   
  G1-CROSS-001      G1   Reduced                    2    0     -2     100.0%   
  G1-CROSS-002      G1   Reduced                    5    0     -5     100.0%   
  G1-CROSS-003      G1   Reduced                    19   1     -18    94.7%    
  G1-NAME-004       G1   Reduced                    3    0     -3     100.0%   
  G1-NAME-013       G1   Expected to persist        1    0     -1     100.0%   
  G2-NAME-009       G2   Reduced                    18   14    -4     22.2%    
  G2-NAME-012       G2   Expected to persist        3    21    18     -600.0%  
  G2-VAL-001        G2   Expected to persist  yes   0    35    35     n/a      
  G2-VAL-002        G2   Expected to persist  yes   1    1     0      0.0%     
  G2-VAL-007        G2   Reduced              yes   0    35    35     n/a      
  G3-NAME-003       G3   Reduced                    3    3     0      0.0%     
  G3-NAME-005       G3   Reduced                    12   0     -12    100.0%   
  G5-NAME-001       G5   Reduced                    55   2     -53    96.4%    
  G5-NAME-002       G5   Reduced                    22   12    -10    45.5%    
  G6-CONFIRM-001    G6   Verification               0    53    53     n/a      
  G7-UNCHANGED-001  G7   Verification               0    36    36     n/a      

  group  pre  post  delta  reduction
  -----  ---  ----  -----  ---------
  G1     126  67    -59    46.8%    
  G2     22   106   84     -381.8%  
  G3     15   3     -12    80.0%    
  G5     77   14    -63    81.8%    
  G6     0    53    53     n/a      
  G7     0    36    36     n/a      

  set                       pre  post  delta  reduction
  ------------------------  ---  ----  -----  ---------
  Reduced                   235  133   -102   43.4%    
  Expected to persist       5    57    52     -1040.0% 
  Verification              0    89    89     n/a      
  reduction set (18 codes)  235  133   -102   43.4%    
  mandatory (7 codes)       1    71    70     -7000.0% 
  all live codes            240  279   39     -16.2%   

--- S5 ------------------------------------------------------------------------
  rows: pre 100  post 100
  records carrying >=1 issue: pre 95  post 94

  code              grp  segment              mand  pre  post  delta  reduction
  ----------------  ---  -------------------  ----  ---  ----  -----  ---------
  G1-ADDR-001       G1   Reduced                    65   65    0      0.0%     
  G1-ADDR-003       G1   Reduced                    24   0     -24    100.0%   
  G1-ADDR-004       G1   Reduced                    7    0     -7     100.0%   
  G1-ADDR-006       G1   Reduced                    3    0     -3     100.0%   
  G1-ADDR-011       G1   Reduced                    1    1     0      0.0%     
  G1-CROSS-001      G1   Reduced                    3    0     -3     100.0%   
  G1-CROSS-002      G1   Reduced                    5    2     -3     60.0%    
  G1-CROSS-003      G1   Reduced                    19   0     -19    100.0%   
  G1-NAME-004       G1   Reduced                    4    0     -4     100.0%   
  G1-NAME-013       G1   Expected to persist        4    0     -4     100.0%   
  G2-NAME-009       G2   Reduced                    2    3     1      -50.0%   
  G2-NAME-012       G2   Expected to persist        0    4     4      n/a      
  G2-VAL-001        G2   Expected to persist  yes   0    2     2      n/a      
  G2-VAL-002        G2   Expected to persist  yes   3    3     0      0.0%     
  G2-VAL-007        G2   Reduced              yes   0    2     2      n/a      
  G3-ADDR-005       G3   Expected to persist        1    0     -1     100.0%   
  G3-ADDR-013       G3   Expected to persist        2    1     -1     50.0%    
  G3-ADDR-014       G3   Expected to persist        4    3     -1     25.0%    
  G3-CONTACT-010    G3   Expected to persist        1    1     0      0.0%     
  G3-NAME-003       G3   Reduced                    8    9     1      -12.5%   
  G3-NAME-005       G3   Reduced                    3    0     -3     100.0%   
  G5-NAME-001       G5   Reduced                    43   40    -3     7.0%     
  G5-NAME-002       G5   Reduced                    8    6     -2     25.0%    
  G6-CONFIRM-001    G6   Verification               0    39    39     n/a      
  G7-UNCHANGED-001  G7   Verification               0    28    28     n/a      

  group  pre  post  delta  reduction
  -----  ---  ----  -----  ---------
  G1     135  68    -67    49.6%    
  G2     5    14    9      -180.0%  
  G3     19   14    -5     26.3%    
  G5     51   46    -5     9.8%     
  G6     0    39    39     n/a      
  G7     0    28    28     n/a      

  set                       pre  post  delta  reduction
  ------------------------  ---  ----  -----  ---------
  Reduced                   195  128   -67    34.4%    
  Expected to persist       15   14    -1     6.7%     
  Verification              0    67    67     n/a      
  reduction set (18 codes)  195  128   -67    34.4%    
  mandatory (7 codes)       3    7     4      -133.3%  
  all live codes            210  209   -1     0.5%     

--- column gating on the required-field rules --------------------------------
  The five G2-VAL-* rules fire only when the column EXISTS in the
  file and is blank (enrichment/issue_detection.py:1189-1215).
  A code whose column is absent from the pre file cannot fire there,
  so its pre count is 0 by construction, not by cleanliness.

  code        column              S1 pre/post  S2 pre/post  S3 pre/post  S4 pre/post  S5 pre/post
  ----------  ------------------  -----------  -----------  -----------  -----------  -----------
  G2-VAL-001  Name 1              y/y          y/y          y/y          y/y          y/y        
  G2-VAL-002  Postal Code         y/y          y/y          y/y          y/y          y/y        
  G2-VAL-004  Region              y/y          y/y          y/y          y/y          y/y        
  G2-VAL-007  Search Term 1       n/y          n/y          n/y          n/y          n/y        
  G2-VAL-008  Country/Region Key  y/y          y/y          y/y          y/y          y/y        

--- S1-S5 combined -----------------------------------------------------------

  code              grp  segment              pre  post  delta  reduction
  ----------------  ---  -------------------  ---  ----  -----  ---------
  G1-ADDR-001       G1   Reduced              203  204   1      -0.5%    
  G1-ADDR-003       G1   Reduced              139  2     -137   98.6%    
  G1-ADDR-004       G1   Reduced              30   0     -30    100.0%   
  G1-ADDR-006       G1   Reduced              30   0     -30    100.0%   
  G1-ADDR-011       G1   Reduced              4    1     -3     75.0%    
  G1-CROSS-001      G1   Reduced              10   0     -10    100.0%   
  G1-CROSS-002      G1   Reduced              19   4     -15    78.9%    
  G1-CROSS-003      G1   Reduced              87   2     -85    97.7%    
  G1-NAME-004       G1   Reduced              16   0     -16    100.0%   
  G1-NAME-013       G1   Expected to persist  7    0     -7     100.0%   
  G2-NAME-009       G2   Reduced              53   53    0      0.0%     
  G2-NAME-012       G2   Expected to persist  26   66    40     -153.8%  
  G2-VAL-001        G2   Expected to persist  0    37    37     n/a      
  G2-VAL-002        G2   Expected to persist  8    8     0      0.0%     
  G2-VAL-007        G2   Reduced              0    37    37     n/a      
  G3-ADDR-005       G3   Expected to persist  3    0     -3     100.0%   
  G3-ADDR-013       G3   Expected to persist  7    4     -3     42.9%    
  G3-ADDR-014       G3   Expected to persist  6    5     -1     16.7%    
  G3-CONTACT-010    G3   Expected to persist  3    3     0      0.0%     
  G3-NAME-003       G3   Reduced              16   18    2      -12.5%   
  G3-NAME-005       G3   Reduced              24   0     -24    100.0%   
  G3-NAME-006       G3   Verification         0    2     2      n/a      
  G5-NAME-001       G5   Reduced              200  93    -107   53.5%    
  G5-NAME-002       G5   Reduced              80   30    -50    62.5%    
  G6-CONFIRM-001    G6   Verification         0    202   202    n/a      
  G7-UNCHANGED-001  G7   Verification         0    113   113    n/a      

  set                  pre  post  delta  reduction
  -------------------  ---  ----  -----  ---------
  Reduced              911  444   -467   51.3%    
  Expected to persist  60   123   63     -105.0%  
  Verification         0    317   317    n/a      
  reduction set (18)   911  444   -467   51.3%    
  mandatory (7)        8    82    74     -925.0%  
  all live codes       971  884   -87    9.0%     

==============================================================================
4 — SHIPPED `Issues` COLUMN VS THE DETECTOR AT THIS COMMIT
==============================================================================
  Every `Issues` column in every workbook, compared as code SETS
  against the detector re-run over the same cells at this commit.
  `POST /issues` appends its column LAST (api/routes.py:445), so an
  earlier column of that name is input-side, not output.

  workbook  col (0-based)  position  rows agreeing  shipped only                                                                                   detector only                                                                               
  --------  -------------  --------  -------------  ---------------------------------------------------------------------------------------------  --------------------------------------------------------------------------------------------
  S1_pre    2              earlier   29/100         {'G5-NAME-002': 45, 'G4-ADDR-008': 17, 'G1-NAME-001': 15, 'G5-NAME-001': 5, 'G1-ADDR-006': 1}  {'G2-NAME-012': 9, 'G3-ADDR-013': 2, 'G1-NAME-004': 1, 'G1-CROSS-001': 1, 'G1-CROSS-003': 1}
  S1_pre    7              last      74/100         {'G1-NAME-001': 26}                                                                            -                                                                                           
  S1_post   7              earlier   83/100         {'G1-NAME-001': 17}                                                                            -                                                                                           
  S1_post   82             last      83/100         {'G1-NAME-001': 17}                                                                            -                                                                                           
  S2_pre    6              earlier   95/100         {'G1-NAME-001': 5}                                                                             -                                                                                           
  S2_pre    41             last      95/100         {'G1-NAME-001': 5}                                                                             -                                                                                           
  S2_post   83             last      97/100         {'G1-NAME-001': 3}                                                                             -                                                                                           
  S3_pre    41             last      95/100         {'G1-NAME-001': 5}                                                                             -                                                                                           
  S3_post   83             last      99/100         {'G1-NAME-001': 1}                                                                             -                                                                                           
  S4_pre    41             last      95/100         {'G1-NAME-001': 5}                                                                             -                                                                                           
  S4_post   84             last      100/100        -                                                                                              -                                                                                           
  S5_pre    41             last      97/100         {'G1-NAME-001': 3}                                                                             -                                                                                           
  S5_post   83             last      98/100         {'G1-NAME-001': 2}                                                                             -                                                                                           

==============================================================================
5 — COMPLETENESS KPI (filled / expected, name + address block)
==============================================================================
  Field set: EnrichmentRecord's Name block (api/models.py:90-110,
  utils/name_slots.py:43-46) and Address block (api/models.py:112-157).
  'expected' is the row count. 'col' says whether the workbook
  carries a column that maps onto the field at all: a field with no
  column can only read as 0 filled, which is a different fact from
  an empty cell.

--- S1 ------------------------------------------------------------------------
  SAP column          field               col  pre filled  col  post filled  delta
  ------------------  ------------------  ---  ----------  ---  -----------  -----
  Name 1              name_1              y    100/100     y    100/100      0    
  Name 2              name_2              y    83/100      y    78/100       -5   
  Name 3              name_3              y    43/100      y    33/100       -10  
  Name 4              name_4              y    6/100       y    8/100        2    
  Name 5              name_5              n    0/100       y    0/100        0    
  Street 1            street_1            y    97/100      y    89/100       -8   
  House Number        house_number        y    64/100      y    64/100       0    
  Street 2            street_2            y    59/100      y    17/100       -42  
  Street 3            street_3            y    12/100      y    0/100        -12  
  Street 4            street_4            y    1/100       y    0/100        -1   
  Street 5            street_5            n    0/100       y    0/100        0    
  PO Box              po_box              y    5/100       y    6/100        1    
  Country/Region Key  country_region_key  y    100/100     y    100/100      0    
  Postal Code         postal_code         y    99/100      y    99/100       0    
  City                city                y    100/100     y    100/100      0    
  Region              region              y    100/100     y    100/100      0    

  headline over the 14 fields carried by BOTH files:
    pre  869/1400 = 62.1%
    post 794/1400 = 56.7%
    delta -75 filled cells (-5.4 pp)
    fields with no pre-side column: Name 5, Street 5

--- S2 ------------------------------------------------------------------------
  SAP column          field               col  pre filled  col  post filled  delta
  ------------------  ------------------  ---  ----------  ---  -----------  -----
  Name 1              name_1              y    100/100     y    100/100      0    
  Name 2              name_2              y    67/100      y    50/100       -17  
  Name 3              name_3              y    23/100      y    9/100        -14  
  Name 4              name_4              y    4/100       y    1/100        -3   
  Name 5              name_5              n    0/100       y    0/100        0    
  Street 1            street_1            y    86/100      y    79/100       -7   
  House Number        house_number        y    36/100      y    36/100       0    
  Street 2            street_2            y    36/100      y    14/100       -22  
  Street 3            street_3            y    4/100       y    0/100        -4   
  Street 4            street_4            y    1/100       y    0/100        -1   
  Street 5            street_5            n    0/100       y    0/100        0    
  PO Box              po_box              y    17/100      y    8/100        -9   
  Country/Region Key  country_region_key  y    100/100     y    100/100      0    
  Postal Code         postal_code         y    97/100      y    97/100       0    
  City                city                y    100/100     y    100/100      0    
  Region              region              y    100/100     y    100/100      0    

  headline over the 14 fields carried by BOTH files:
    pre  771/1400 = 55.1%
    post 694/1400 = 49.6%
    delta -77 filled cells (-5.5 pp)
    fields with no pre-side column: Name 5, Street 5

--- S3 ------------------------------------------------------------------------
  SAP column          field               col  pre filled  col  post filled  delta
  ------------------  ------------------  ---  ----------  ---  -----------  -----
  Name 1              name_1              y    100/100     y    100/100      0    
  Name 2              name_2              y    69/100      y    68/100       -1   
  Name 3              name_3              y    26/100      y    24/100       -2   
  Name 4              name_4              y    5/100       y    7/100        2    
  Name 5              name_5              n    0/100       y    1/100        1    
  Street 1            street_1            y    90/100      y    84/100       -6   
  House Number        house_number        y    58/100      y    58/100       0    
  Street 2            street_2            y    36/100      y    5/100        -31  
  Street 3            street_3            y    5/100       y    0/100        -5   
  Street 4            street_4            y    2/100       y    0/100        -2   
  Street 5            street_5            n    0/100       y    0/100        0    
  PO Box              po_box              y    19/100      y    3/100        -16  
  Country/Region Key  country_region_key  y    100/100     y    100/100      0    
  Postal Code         postal_code         y    100/100     y    100/100      0    
  City                city                y    100/100     y    100/100      0    
  Region              region              y    100/100     y    100/100      0    

  headline over the 14 fields carried by BOTH files:
    pre  810/1400 = 57.9%
    post 749/1400 = 53.5%
    delta -61 filled cells (-4.4 pp)
    fields with no pre-side column: Name 5, Street 5

--- S4 ------------------------------------------------------------------------
  SAP column          field               col  pre filled  col  post filled  delta
  ------------------  ------------------  ---  ----------  ---  -----------  -----
  Name 1              name_1              y    100/100     y    65/100       -35  
  Name 2              name_2              y    52/100      y    39/100       -13  
  Name 3              name_3              y    9/100       y    6/100        -3   
  Name 4              name_4              y    2/100       y    0/100        -2   
  Name 5              name_5              n    0/100       y    0/100        0    
  Street 1            street_1            y    96/100      y    93/100       -3   
  House Number        house_number        y    17/100      y    17/100       0    
  Street 2            street_2            y    44/100      y    8/100        -36  
  Street 3            street_3            y    4/100       y    0/100        -4   
  Street 4            street_4            y    0/100       y    0/100        0    
  Street 5            street_5            n    0/100       y    0/100        0    
  PO Box              po_box              y    4/100       y    5/100        1    
  Country/Region Key  country_region_key  y    100/100     y    100/100      0    
  Postal Code         postal_code         y    99/100      y    99/100       0    
  City                city                y    100/100     y    100/100      0    
  Region              region              y    100/100     y    100/100      0    

  headline over the 14 fields carried by BOTH files:
    pre  727/1400 = 51.9%
    post 632/1400 = 45.1%
    delta -95 filled cells (-6.8 pp)
    fields with no pre-side column: Name 5, Street 5

--- S5 ------------------------------------------------------------------------
  SAP column          field               col  pre filled  col  post filled  delta
  ------------------  ------------------  ---  ----------  ---  -----------  -----
  Name 1              name_1              y    100/100     y    98/100       -2   
  Name 2              name_2              y    41/100      y    27/100       -14  
  Name 3              name_3              y    4/100       y    4/100        0    
  Name 4              name_4              y    5/100       y    0/100        -5   
  Name 5              name_5              n    0/100       y    0/100        0    
  Street 1            street_1            y    95/100      y    94/100       -1   
  House Number        house_number        y    19/100      y    19/100       0    
  Street 2            street_2            y    39/100      y    15/100       -24  
  Street 3            street_3            y    6/100       y    0/100        -6   
  Street 4            street_4            y    0/100       y    0/100        0    
  Street 5            street_5            n    0/100       y    0/100        0    
  PO Box              po_box              y    10/100      y    6/100        -4   
  Country/Region Key  country_region_key  y    100/100     y    100/100      0    
  Postal Code         postal_code         y    97/100      y    97/100       0    
  City                city                y    100/100     y    100/100      0    
  Region              region              y    100/100     y    100/100      0    

  headline over the 14 fields carried by BOTH files:
    pre  716/1400 = 51.1%
    post 660/1400 = 47.1%
    delta -56 filled cells (-4.0 pp)
    fields with no pre-side column: Name 5, Street 5

--- values lost: filled in pre, blank in post, joined on Customer ------------
  stratum  joined  Name 1  Name 2  Name 3  Name 4  Name 5  Street 1  House Number  Street 2  Street 3  Street 4  Street 5  PO Box  Country/Region Key  Postal Code  City  Region
  -------  ------  ------  ------  ------  ------  ------  --------  ------------  --------  --------  --------  --------  ------  ------------------  -----------  ----  ------
  S1       100     0       9       16      1       0       8         0             45        12        1         0         5       0                   0            0     0     
  S2       100     0       19      16      3       0       8         0             23        4         1         0         17      0                   0            0     0     
  S3       100     0       11      6       0       0       6         0             32        5         2         0         19      0                   0            0     0     
  S4       100     35      18      4       2       0       4         0             36        4         0         0         4       0                   0            0     0     
  S5       100     2       20      2       5       0       2         0             25        6         0         0         10      0                   0            0     0     
  A count here is a value the pre file carried and the post file
  does not. For the Street block most of it is relocation into the
  sub-location columns below; for Name 1 there is no such
  destination in the schema.

--- columns the enriched export adds (no pre-side counterpart) ---------------
  stratum  Suite  Building  Floor  Room  Unit  Mail Stop  Unloading Point  Mail Code  Care Of  Contact  Email  Operating Name  Suggested Name  Domain  Department Domain  Record Type  ROR ID  LEI ID  Search Term 1  Search Term 2
  -------  -----  --------  -----  ----  ----  ---------  ---------------  ---------  -------  -------  -----  --------------  --------------  ------  -----------------  -----------  ------  ------  -------------  -------------
  S1       6      21        6      11    0     1          3                20         1        3        7      6               5               99      27                 100          86      0       100            69           
  S2       13     5         0      4     2     2          6                4          8        5        17     22              6               97      1                  100          30      34      100            39           
  S3       5      20        2      4     0     13         6                6          8        4        3      8               15              90      7                  100          58      4       100            63           
  S4       12     2         0      4     1     0          8                6          12       3        2      3               6               35      0                  100          11      3       65             25           
  S5       18     4         0      2     1     0          4                7          7        5        9      21              14              74      0                  100          17      7       98             20           

==============================================================================
6 — IMPROVEMENT INDEX (post-run count / mean count across categories)
==============================================================================
  'category' is read two ways because the specification does not
  fix one; both are given. An index above 1.0 is the next-iteration
  trigger.

--- S1: by issue group -------------------------------------------------------
  group  post count  index       
  -----  ----------  -----  -----
  G1     19          1.34   ABOVE
  G2     39          2.76   ABOVE
  G3     2           0.14        
  G4     0           0.00        
  G5     2           0.14        
  G6     24          1.70   ABOVE
  G7     13          0.92        
  mean across 7 groups = 14.14   above 1.0: G1, G2, G6

--- S2: by issue group -------------------------------------------------------
  group  post count  index       
  -----  ----------  -----  -----
  G1     38          1.63   ABOVE
  G2     13          0.56        
  G3     10          0.43        
  G4     0           0.00        
  G5     51          2.19   ABOVE
  G6     40          1.72   ABOVE
  G7     11          0.47        
  mean across 7 groups = 23.29   above 1.0: G1, G5, G6

--- S3: by issue group -------------------------------------------------------
  group  post count  index       
  -----  ----------  -----  -----
  G1     21          1.10   ABOVE
  G2     29          1.51   ABOVE
  G3     3           0.16        
  G4     0           0.00        
  G5     10          0.52        
  G6     46          2.40   ABOVE
  G7     25          1.31   ABOVE
  mean across 7 groups = 19.14   above 1.0: G1, G2, G6, G7

--- S4: by issue group -------------------------------------------------------
  group  post count  index       
  -----  ----------  -----  -----
  G1     67          1.68   ABOVE
  G2     106         2.66   ABOVE
  G3     3           0.08        
  G4     0           0.00        
  G5     14          0.35        
  G6     53          1.33   ABOVE
  G7     36          0.90        
  mean across 7 groups = 39.86   above 1.0: G1, G2, G6

--- S5: by issue group -------------------------------------------------------
  group  post count  index       
  -----  ----------  -----  -----
  G1     68          2.28   ABOVE
  G2     14          0.47        
  G3     14          0.47        
  G4     0           0.00        
  G5     46          1.54   ABOVE
  G6     39          1.31   ABOVE
  G7     28          0.94        
  mean across 7 groups = 29.86   above 1.0: G1, G5, G6

--- across strata (the stratum IS the record category) -----------------------
  stratum  category           post count  index       
  -------  -----------------  ----------  -----  -----
  S1       academic_research  99          0.56        
  S2       large_corporate    163         0.92        
  S3       government_labs    134         0.76        
  S4       hospital_health    279         1.58   ABOVE
  S5       smb_residual       209         1.18   ABOVE
  mean across 5 strata = 176.80   above 1.0: S4, S5

==============================================================================
7 — CLUSTERING AND ELECTION
==============================================================================
  Ground-truth mapping onto eval/dedup_eval.py's vocabulary
  (the Method sheet's 'How to score' rule, stated once):
    gt_expected_action MERGE         -> expected_routing cluster
    gt_expected_action REVIEW        -> expected_routing manual_review
    gt_expected_action LINK          -> expected_routing unique
    gt_expected_action DO NOT MERGE  -> expected_routing unique
    gt_expected_action UNIQUE        -> expected_routing unique
    expected_cluster = gt_dup_group
  Only MERGE forms ground-truth pairs, so LINK and the two DO NOT
  MERGE actions can only ever appear as a false positive.

--- stress set, 200-row grain (clustering run, no scoring) --------------------
  scored workbook : data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx
  ground truth    : data/eval/dedup_STRESS_200_v1_enriched_dedup.xlsx [Sheet]
  rows scored     : 200   ground truth matched: 200
  routing         : {'unique': 160, 'cluster': 35, 'manual_review': 5}
  expected routing: {'cluster': 103, 'unique': 95, 'manual_review': 2}

  metric              value 
  ------------------  ------
  true_positives      21    
  false_positives     0     
  false_negatives     54    
  precision           1.0   
  recall              0.28  
  f1                  0.4375
  ground_truth_pairs  75    
  predicted_pairs     21    

  ground-truth MERGE groups of size >= 2 : 45
  clusters the run produced              : 17
  under-merge pairs (truth MERGE, not clustered): 54
    e.g. 13057338+13341783, 13341685+13342488, 13368532+13369241, 13185655+13350355, 13033017+13116126, 13044882+13044976, 13011572+13088325, 13210816+13337284, 13130623+13141440, 13337285+13349043, 13056457+13134277, 13056457+13213881
  over-merge pairs (clustered, truth not MERGE):
    none

  No election columns in this workbook — election and
  business-risk metrics are not computable from it.

--- stress set, 183-row grain (clustering + scoring + election) ---------------
  scored workbook : data/eval/stress_200_scored.xlsx
  ground truth    : data/eval/dedup_STRESS_200_v1-verified.xlsx [Data]
  rows scored     : 183   ground truth matched: 183
  routing         : {'unique': 88, 'cluster': 78, 'manual_review': 17}
  expected routing: {'cluster': 95, 'unique': 86, 'manual_review': 2}

  metric              value 
  ------------------  ------
  true_positives      50    
  false_positives     9     
  false_negatives     21    
  precision           0.8475
  recall              0.7042
  f1                  0.7692
  ground_truth_pairs  71    
  predicted_pairs     59    

  ground-truth MERGE groups of size >= 2 : 41
  clusters the run produced              : 42
  under-merge pairs (truth MERGE, not clustered): 21
    e.g. 13057338+13341783, 13341685+13342488, 13056457+13134277, 13056457+13213881, 13334236+13335826, 13334236+13344636, 13334046+13336374, 13335012+13336374, 13033988+13135468, 13033988+13138597, 13033988+13353599, 13033988+13364185
  over-merge pairs (clustered, truth not MERGE):
    link (same entity, different site): 9   e.g. 13132835+13225308, 13345790+13345937, 13115460+13129200, 13144897+13223387, 13348301+13364371, 13113215+13128534

  election metric            value
  -------------------------  -----
  clusters                   42   
  elections                  33   
  manual_review_rows         25   
  tiebreak_decided_clusters  3    
    tie-break cluster ids: ['c_2759839874b4', 'c_7b0f5107ff65', 'c_cb3fed562da7']

  business risk              count  row ids                                                                       
  -------------------------  -----  ------------------------------------------------------------------------------
  wrongful_block_candidates  4      13128534, 13129200, 13144897, 13225308                                        
  competing_goldens          15     13033121, 13056457, 13057338, 13127964, 13138597, 13147440, 13162837, 13225238
  uncertainty_upgrades       0      -                                                                             

--- S5 test set --------------------------------------------------------------
  annotation columns present : cluster_id, cluster_role
  annotated clusters         : 11 over 23 records, sizes {3: 1, 2: 10}
  cluster_role distribution  : {'blank': 77, 'same entity, same site': 14, 'same entity, different sites': 5, 'same street, different building': 2, 'same street, one building value missing': 2}
  clustering OUTPUT columns  : NONE
  ** MEASUREMENT REQUIRED ** — no run output exists for S5, so
  expected-vs-found, over-merges, under-merges, manual_review and
  unique counts cannot be computed for this stratum. Producing them
  needs POST /api/dedup/cluster over S5_post.xlsx.
  Note also that eval/dedup_eval.py maps the header `cluster_id`
  onto its OUTPUT field (eval/dedup_eval.py:47), so pointing it at
  a stratum workbook would read the annotation as the run's answer.

==============================================================================
8 — COST
==============================================================================
  Recorded run summary — logs/runs/determinism_S1_327ee53.json
  (the only per-run counter set in the repository that names a
   stratum; the name is the only evidence it is S1)

  counter                    value  per record
  -------------------------  -----  ----------
  total                      100              
  tier1_resolved             17     0.17      
  lei_attempts               3      0.03      
  tier2a_population_count    1      0.01      
  tier2a_verification_count  0      0.00      
  tier2b_count               0      0.00      
  tier3_count                19     0.19      
  page_reads_attempted       13     0.13      
  wikidata_queried           16     0.16      
  liveness_ror_queried       57     0.57      
  domain_from_serp           2      0.02      
  contact_lookup_attempted   1      0.01      
  evidence_network_calls     0      0.00      
  evidence_cache_hits        1577   15.77     
  evidence_cache_frozen      True             
  processing_time_ms         442    4.42      

  evidence_network_calls = 0 and evidence_cache_frozen = true:
  this run replayed a recorded cache, so it measures the WORK
  the pipeline asked for, not calls billed on that run.
  network calls by namespace: {}

  Dedup LLM calls — data/eval/stress_200_scored.xlsx :: Run sheet
  setting               value              
  --------------------  -------------------
  prompt_version        p2-dedup-v8        
  model                 MDM-Apoorva-gpt-5.4
  model_version         MDM-Apoorva-gpt-5.4
  DEDUP_V2_BLOCKING     true               
  DEDUP_V2_NAME2        true               
  DEDUP_V2_ID_CONFLICT  true               
  dedup_v2_active       true               
  fixture_cache         off                
  rows_in               183                
  blocks                111                
  llm_calls             39                 
  rows_clustered        78                 
  rows_unique           88                 
  rows_manual_review    17                 
  LLM calls per record: 0.213

  Recorded evidence caches (unique keys per namespace, NOT calls —
  a repeated question is one key). None is attributable to a
  stratum: no cache directory records the batch it was recorded on.
  cache dir    fetch  llm   page_reads  registry  serp  wikidata
  -----------  -----  ----  ----------  --------  ----  --------
  cutu_suzu_1  680    337   15          259       142   39      
  cutu_suzu_2  1594   1163  129         1944      642   390     
  cutu_suzu_3  707    339   15          256       147   39      
  cutu_suzu_4  695    338   15          256       145   39      
  cutu_suzu_5  400    292   56          602       171   119     
  cutu_suzu_6  604    383   36          678       218   162     
  cutu_suzu_7  420    313   14          396       151   101     
  grounded     424    282   54          695       181   145     
  log_A        78     115   23          401       61    51      
  log_B        74     116   23          401       62    96      
  log_C        237    243   40          576       139   157     
  log_Z        78     115   23          402       61    51      

  ** MEASUREMENT REQUIRED ** — cost in currency. No price, rate
  card or token-price constant exists anywhere in this repository
  (grep for price/pricing/USD/cost_per over *.py, *.json, *.md,
  *.yaml returns no rate). Supplying it needs the SerpAPI plan
  rate and the Azure OpenAI deployment's per-1K-token price for
  the deployment named on the Run sheet, or an App Insights cost
  export. Neither is in the repository.
  ** MEASUREMENT REQUIRED ** — calls per record per tier per
  stratum for S2-S5: no run summary exists for those strata.

==============================================================================
9 — DETERMINISM AND RUN-TO-RUN STABILITY
==============================================================================
  9a — tools/run_diff.py RUN HERE, at this commit, over the tracked
  run artefacts in eval/out/. Each pair is the SAME batch at two
  DIFFERENT commits, so a difference is a code change, not
  non-determinism: this measures how far the output moved between
  commits, not whether one commit repeats itself.

--- S1  d3a3cfc -> f57782f ----------------------------------------------------
$ python tools/run_diff.py eval/out/S1_results.json eval/out/f57782f/S1_results.json --quiet
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 0
note: S1_results.json — 50 row(s) share a (name, city) key; disambiguated by customer number.
note: S1_results.json — 50 row(s) share a (name, city) key; disambiguated by customer number.
[exit 0]

--- S1  f57782f -> 327ee53 ----------------------------------------------------
$ python tools/run_diff.py eval/out/f57782f/S1_results.json eval/out/327ee53/S1_results.json --quiet
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 18
cell differences   : 39

PER-COLUMN COUNTS
  Search Term 1              7
  Suggested Name             5
  Suggestion Source          5
  Name 2                     3
  Search Term 2              3
  Street 1                   2
  Flag Codes                 2
  Flagged Fields             2
  Flag Reason                2
  Street 2                   1
  Flag for Review            1
  Care Of                    1
  Suite                      1
  Name 1 Provenance          1
  Operating Name             1
  Operating Name Provenance  1
  Domain Provenance          1

FAIL — 18 row(s) differ.

run 2 network calls: 0
note: S1_results.json — 50 row(s) share a (name, city) key; disambiguated by customer number.
note: S1_results.json — 50 row(s) share a (name, city) key; disambiguated by customer number.
[exit 1]

--- S4  d3a3cfc -> f57782f ----------------------------------------------------
$ python tools/run_diff.py eval/out/S4_results.json eval/out/f57782f/S4_results.json --quiet
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 4
cell differences   : 30

PER-COLUMN COUNTS
  Operating Name             3
  Operating Name Provenance  3
  Name 1                     3
  ROR ID                     3
  Name 1 Provenance          3
  Domain Provenance          3
  Record Type Provenance     3
  ROR ID Provenance          3
  Domain                     1
  Search Term 1              1
  Flag for Review            1
  Flag Codes                 1
  Flagged Fields             1
  Flag Reason                1

FAIL — 4 row(s) differ.

run 2 network calls: 0
note: S4_results.json — 25 row(s) share a (name, city) key; disambiguated by customer number.
note: S4_results.json — 25 row(s) share a (name, city) key; disambiguated by customer number.
[exit 1]

--- S4  f57782f -> 327ee53 ----------------------------------------------------
$ python tools/run_diff.py eval/out/f57782f/S4_results.json eval/out/327ee53/S4_results.json --quiet
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 40
cell differences   : 122

PER-COLUMN COUNTS
  Search Term 1              22
  Flag Reason                13
  Flagged Fields             12
  Name 1 Provenance          11
  Suggested Name             11
  Suggestion Source          11
  Flag Codes                 10
  Flag for Review            6
  Name 2 Provenance          5
  Name 2                     3
  Street 1                   2
  Street 2                   2
  Search Term 2              2
  Domain                     2
  Domain Provenance          2
  Unloading Point            1
  Name 1                     1
  Operating Name             1
  Operating Name Provenance  1
  Record Type                1
  ROR ID                     1
  Record Type Provenance     1
  ROR ID Provenance          1

FAIL — 40 row(s) differ.

run 2 network calls: 0
note: S4_results.json — 25 row(s) share a (name, city) key; disambiguated by customer number.
note: S4_results.json — 25 row(s) share a (name, city) key; disambiguated by customer number.
[exit 1]

  9b — the determinism gate proper: TWO RUNS AT ONE COMMIT. The two
  run artefacts of each such pair are not in the repository, only
  run_diff's stored --json report of them. Each is re-rendered
  verbatim below through tools.run_diff.render().

--- logs/runs/determinism_S1.json --------------------
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 0

--- logs/runs/determinism_S1_327ee53.json --------------------
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 0

--- logs/runs/determinism_S1_f57782f.json --------------------
========================================================================
RUN DIFF
========================================================================
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 0

==============================================================================
10 — CROSS-CHECK AGAINST THE RECORDED RUNS IN eval/out/
==============================================================================
  eval/out/327ee53/ holds the scripts/run_batch.py --json artefact
  of a frozen run of S1, S4 and S5 at commit 327ee53
  (eval/out/RUNS.md). Its record ids are the same 100 per stratum as
  the data/eval workbooks. Auditing that artefact gives a SECOND
  post-enrichment state for those three strata — one that names the
  commit it was produced at, which no post workbook does.
  A difference below is a difference between two artefacts produced
  at two commits; it is not by itself a regression at either.

--- S1 ------------------------------------------------------------------------
  eval/out/327ee53/S1_results.json
  records 100   ids shared with S1_post.xlsx: 100
  code              grp  recorded run @327ee53  S1_post.xlsx  delta
  ----------------  ---  ---------------------  ------------  -----
  G1-ADDR-001       G1   17                     17            0    
  G1-CROSS-002      G1   1                      2             1    
  G2-NAME-009       G2   3                      3             0    
  G2-NAME-012       G2   33                     35            2    
  G2-VAL-002        G2   1                      1             0    
  G3-ADDR-013       G3   1                      1             0    
  G3-NAME-003       G3   1                      1             0    
  G5-NAME-001       G5   1                      1             0    
  G5-NAME-002       G5   2                      1             -1   
  G6-CONFIRM-001    G6   37                     24            -13  
  G7-UNCHANGED-001  G7   0                      13            13   
  totals: recorded run 97   S1_post.xlsx 99

  field               filled, recorded run  filled, S1_post.xlsx  delta
  ------------------  --------------------  --------------------  -----
  Name 1              100                   100                   0    
  Name 2              80                    78                    -2   
  Name 3              28                    33                    5    
  Name 4              7                     8                     1    
  Street 1            90                    89                    -1   
  House Number        64                    64                    0    
  Street 2            16                    17                    1    
  PO Box              6                     6                     0    
  Country/Region Key  100                   100                   0    
  Postal Code         99                    99                    0    
  City                100                   100                   0    
  Region              100                   100                   0    

--- S4 ------------------------------------------------------------------------
  eval/out/327ee53/S4_results.json
  records 100   ids shared with S4_post.xlsx: 100
  code              grp  recorded run @327ee53  S4_post.xlsx  delta
  ----------------  ---  ---------------------  ------------  -----
  G1-ADDR-001       G1   66                     66            0    
  G1-CROSS-003      G1   0                      1             1    
  G2-NAME-009       G2   12                     14            2    
  G2-NAME-012       G2   11                     21            10   
  G2-VAL-001        G2   0                      35            35   
  G2-VAL-002        G2   1                      1             0    
  G2-VAL-007        G2   0                      35            35   
  G3-NAME-003       G3   4                      3             -1   
  G5-NAME-001       G5   5                      2             -3   
  G5-NAME-002       G5   4                      12            8    
  G6-CONFIRM-001    G6   38                     53            15   
  G7-UNCHANGED-001  G7   0                      36            36   
  totals: recorded run 141   S4_post.xlsx 279

  field               filled, recorded run  filled, S4_post.xlsx  delta
  ------------------  --------------------  --------------------  -----
  Name 1              100                   65                    -35  
  Name 2              46                    39                    -7   
  Name 3              9                     6                     -3   
  Street 1            93                    93                    0    
  House Number        17                    17                    0    
  Street 2            8                     8                     0    
  PO Box              5                     5                     0    
  Country/Region Key  100                   100                   0    
  Postal Code         99                    99                    0    
  City                100                   100                   0    
  Region              100                   100                   0    

--- S5 ------------------------------------------------------------------------
  eval/out/327ee53/S5_results.json
  records 100   ids shared with S5_post.xlsx: 100
  code              grp  recorded run @327ee53  S5_post.xlsx  delta
  ----------------  ---  ---------------------  ------------  -----
  G1-ADDR-001       G1   65                     65            0    
  G1-ADDR-011       G1   1                      1             0    
  G1-CROSS-002      G1   1                      2             1    
  G2-NAME-009       G2   3                      3             0    
  G2-NAME-012       G2   4                      4             0    
  G2-VAL-001        G2   2                      2             0    
  G2-VAL-002        G2   3                      3             0    
  G2-VAL-007        G2   2                      2             0    
  G3-ADDR-013       G3   1                      1             0    
  G3-ADDR-014       G3   3                      3             0    
  G3-CONTACT-010    G3   1                      1             0    
  G3-NAME-003       G3   9                      9             0    
  G5-NAME-001       G5   40                     40            0    
  G5-NAME-002       G5   4                      6             2    
  G6-CONFIRM-001    G6   46                     39            -7   
  G7-UNCHANGED-001  G7   3                      28            25   
  totals: recorded run 188   S5_post.xlsx 209

  field               filled, recorded run  filled, S5_post.xlsx  delta
  ------------------  --------------------  --------------------  -----
  Name 1              98                    98                    0    
  Name 2              28                    27                    -1   
  Name 3              4                     4                     0    
  Name 4              1                     0                     -1   
  Street 1            94                    94                    0    
  House Number        19                    19                    0    
  Street 2            14                    15                    1    
  PO Box              6                     6                     0    
  Country/Region Key  100                   100                   0    
  Postal Code         97                    97                    0    
  City                100                   100                   0    
  Region              100                   100                   0    
```
