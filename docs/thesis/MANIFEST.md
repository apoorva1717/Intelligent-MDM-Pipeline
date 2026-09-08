Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: MANIFEST

# MANIFEST — the documentation set at `86d173b`

Every file under `docs/thesis/`, including `figures/` and `figures/thesis/`. Line counts are
`wc -l`. The "Pass" column reads the `Pass:` field of each file's own header line; `—` means the
file carries no such field and is therefore not a product of this documentation run.

**Headers are not uniform, and are not made uniform here.** The generation clock rolled over
mid-run: passes 00–12 carry `2026-09-07`, passes 13–19 carry `2026-09-08`. Both dates are
recorded below as written. One file departs from that split and is called out in §M.4.

---

## M.1 Pass files

All 49 files in this section carry `Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e` and
`Branch: feature/llm-fixes`, or are `.mmd` figures governed by an index file that does (§M.3).

| Pass | File | Lines | Generated |
|---|---|---|---|
| 00 | `00_INVENTORY.md` | 1077 | 2026-09-07 |
| 01 | `01_TRACEABILITY.md` | 229 | 2026-09-07 |
| 02 | `02_ARCHITECTURE.md` | 556 | 2026-09-07 |
| 03 | `03_ALGORITHMS.md` | 2963 | 2026-09-07 |
| 03b | `03b_EXEMPLARS.md` | 1228 | 2026-09-07 |
| 04 | `04_PARAMETERS.md` | 768 | 2026-09-07 |
| 05 | `05_DATA_MODEL.md` | 1031 | 2026-09-07 |
| 06 | `06_EXTERNAL_DEPS.md` | 615 | 2026-09-07 |
| 06b | `06b_CROSSCUTTING.md` | 1152 | 2026-09-07 |
| 07 | `07_EVALUATION.md` | 832 | 2026-09-07 |
| 08 | `08_GAPS.md` | 720 | 2026-09-07 |
| 09 | `09_DECISIONS.md` | 1136 | 2026-09-07 |
| 10 | `figures/INDEX.md` | 131 | 2026-09-07 |
| 10 | `figures/fig-01-data-plane.mmd` | 19 | — (see §M.3) |
| 10 | `figures/fig-02-processing-plane.mmd` | 18 | — |
| 10 | `figures/fig-03-er-overview.mmd` | 20 | — |
| 10 | `figures/fig-04-enrichment-run-sequence.mmd` | 16 | — |
| 10 | `figures/fig-05-issues-run-sequence.mmd` | 11 | — |
| 10 | `figures/fig-06-er-ingestion.mmd` | 29 | — |
| 10 | `figures/fig-07-enrich-batch-spine.mmd` | 14 | — |
| 10 | `figures/fig-08-enrich-tier-ladder.mmd` | 19 | — |
| 10 | `figures/fig-09-enrich-finalisation.mmd` | 21 | — |
| 10 | `figures/fig-10-issues-call-graph.mmd` | 21 | — |
| 10 | `figures/fig-11-phase1-stage-order.mmd` | 22 | — |
| 10 | `figures/fig-12-dedup-cluster-run-sequence.mmd` | 15 | — |
| 10 | `figures/fig-13-scoring-run-sequence.mmd` | 12 | — |
| 10 | `figures/fig-14-steward-approval-sequence.mmd` | 12 | — |
| 10 | `figures/fig-15-er-clustering.mmd` | 26 | — |
| 10 | `figures/fig-16-er-scoring.mmd` | 29 | — |
| 10 | `figures/fig-17-cluster-block-call-graph.mmd` | 19 | — |
| 10 | `figures/fig-18-score-approve-call-graph.mmd` | 16 | — |
| 10 | `figures/fig-19-consolidate-call-graph.mmd` | 15 | — |
| 10 | `figures/fig-20-coupling-exemplar.mmd` | 16 | — |
| 10b | `figures/thesis/THESIS_INDEX.md` | 265 | 2026-09-08 |
| 10b | `figures/thesis/fig-T1-end-to-end.mmd` | 10 | — |
| 10b | `figures/thesis/fig-T2-tier-ladder.mmd` | 11 | — |
| 10b | `figures/thesis/fig-T3-improvement-loop.mmd` | 12 | — |
| 10b | `figures/thesis/fig-T4-deduplication.mmd` | 11 | — |
| 10b | `figures/thesis/fig-T5-taxonomy.mmd` | 20 | — |
| 10b | `figures/thesis/fig-T6-components.mmd` | 16 | — |
| 11 | `11_DELTA.md` | 581 | 2026-09-07 |
| 12 | `12_RATIONALE.md` | 564 | 2026-09-07 |
| 13 | `13_CLUSTERING_DOSSIER.md` | 2005 | 2026-09-08 |
| 14 | `14_SCORING_DOSSIER.md` | 776 | 2026-09-08 |
| 15 | `15_ISSUES_DOSSIER.md` | 547 | 2026-09-08 |
| 16 | `16_RULESETS.md` | 1064 | 2026-09-08 |
| 17 | `17_TAXONOMY_MAP.md` | 710 | 2026-09-08 |
| 18 | `18_EVAL_RESULTS.md` | 2070 | 2026-09-08 |
| 19 | `19_IMPLEMENTATION_STATE.md` | 265 | 2026-09-08 |

**Totals.** 49 files, 21,735 lines: 23 prose and index files at 21,285 lines, 20 Pass 10 figures
at 370 lines, 6 Pass 10b figures at 80 lines. There is no Pass 10a; the figure set is `10` and
`10b`. No pass numbered `20` or higher exists, and there is no gap in `00`–`19` other than the
absence of a Pass `10a` and the pre-existing absence of a Pass `10` prose file (Pass 10 writes
figures and an index, not a chapter).

---

## M.2 Files under `docs/thesis/` that are NOT part of the set

Present in the directory, carrying no `Pass:` field, and not written by any pass of this run.
They are listed because the directory contains them, not because the set includes them.

| File | Lines | Header commit | State |
|---|---|---|---|
| `00_OPEN_ITEMS.md` | 733 | `515cc7c1a84f55f817d63b4f3f094ce47d57f7fd` | ⚠ **different commit** — see §M.4 |
| `ch02_SOURCE.md` | 1229 | `515cc7c1a84f55f817d63b4f3f094ce47d57f7fd` | ⚠ **different commit** — see §M.4 |
| `CONTEXT-EXTERNAL.md` | 448 | none — first line is `# External System Context` | Input to the run, not output. Marks its own provenance per section (`[EXPORT]` / `[OBSERVED]` / `[AUTHOR]`) and records its observations as taken 2026-08-16 (`:15`) |
| `Datashaper-Tutorial-Part1.txt` | 2096 | none | Vendor tutorial transcript; input material |
| `Datashaper-Tutorial-Part2.txt` | 2054 | none | Vendor tutorial transcript; input material |
| `Datashaper-Tutorial-Part3.txt` | 1013 | none | Vendor tutorial transcript; input material |
| `chemspeed_us_100.xlsx` | n/a (4,240,093 bytes) | n/a | Evaluation workbook |
| `chemspeed_us_100_enriched.xlsx` | n/a (33,450 bytes) | n/a | Evaluation workbook |
| `desktop.ini` | n/a (198 bytes) | n/a | Windows folder-settings file; not content |
| `~$chemspeed_us_100.xlsx` | n/a (165 bytes) | n/a | Excel lock file left by an open workbook; not content |
| `~$dedup_STRESS_200_v1-verified.xlsx` | n/a (165 bytes) | n/a | Excel lock file; its workbook is not in this directory |

---

## M.3 Header verification

Checked by reading the first line of all 55 `.md` and `.mmd` files under `docs/thesis/`.

| Group | Files | Result |
|---|---|---|
| Pass prose and index files (00–19, 10, 10b) | 23 | **All 23 carry `Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e` and `Branch: feature/llm-fixes`.** No file in this group carries a different commit |
| `.mmd` figures (Pass 10 and 10b) | 26 | **No header line, by construction.** A `Generated:` line inside a `.mmd` file is not a comment and would break the diagram. Their commit is asserted by the index that lists them — `figures/INDEX.md` and `figures/thesis/THESIS_INDEX.md`, both at `86d173b` |
| Non-set files carrying a header | 2 | Both at `515cc7c1…` — §M.4 |
| Non-set files carrying no header | 4 | `CONTEXT-EXTERNAL.md` and the three tutorial transcripts |

Headers are **not** uniform across the directory and are not made so: the two dates are a true
record of when each pass ran, and the two non-set files are older artefacts that this run did not
write and must not restamp.

---

## M.4 Files whose commit differs, and the one date exception

**Two files carry a commit other than `86d173b`.** Both predate this documentation run by three
weeks and neither is a pass output:

| File | Header |
|---|---|
| `00_OPEN_ITEMS.md` | `Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace` |
| `ch02_SOURCE.md` | `Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace` |

Neither carries a `Pass:` field, and both were written on a different branch. `00_OPEN_ITEMS.md`
is nonetheless cited by the current set — passes 02, 06b, 07 and 08 reference its numbered items —
so it is live input at a stale commit, not dead weight. Its item numbering has not been
re-verified against `86d173b` by any pass in this run.

**One date exception to the 00–12 / 13–19 split.** `figures/thesis/THESIS_INDEX.md` carries
`Pass: 10b` but `Generated: 2026-09-08`, not the `2026-09-07` its pass number would imply under
the split. The number records which pass produced it; the date records when that pass ran. Pass
10b ran after the clock rolled over, so the two disagree. Every other file in `00`–`12`, the 20
Pass 10 figures and `figures/INDEX.md` included, carries `2026-09-07`; every file in `13`–`19`
carries `2026-09-08`.

---

## M.5 Written outside `docs/thesis/`

Pass 18 also wrote **`tools/eval_report.py`** — 1070 lines, untracked at this commit. It is the
read-only calculator behind every figure in `18_EVAL_RESULTS.md`: it writes nothing to the
repository, makes no network call and invokes no LLM (`tools/eval_report.py:1–5`). It is excluded
from the Function App deployment package (`.funcignore:33` excludes `tools/`) and lies outside
`pytest.ini`'s `testpaths = tests`, so it enters neither the shipped artefact nor the test run
recorded in `19_IMPLEMENTATION_STATE.md` §19.6.

It is the only file this documentation run wrote outside `docs/thesis/`. No source file, SQL file,
ADF export, fixture or workbook was modified by any pass.

---

Documentation set complete at 86d173b.
