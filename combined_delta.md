# Combined before/after delta — chemspeed 100-row US SMB batch

Three columns, because two of them matter for different reasons.

* **Supplied** — `docs/thesis/chemspeed_us_100_enriched.xlsx`, the evidence
  workbook the brief was written against. Not overwritten by any run here.
* **Before** — run A, the same code as *Supplied* plus Fix 1's diagnostic flag,
  re-run so the before/after comparison is between two runs of *my* pipeline
  rather than across a code change and a run boundary at once.
* **After** — run F, all three fixes.

All three are live runs against ROR, GLEIF, SerpAPI and Azure OpenAI. The
pipeline is LLM-driven and **not bit-reproducible**: *Supplied* and *Before* are
the same code and differ slightly, and that difference is the noise floor for
every row below. Page reads *are* reproducible — they are served from the
recorded fixture store (`tests/fixtures/page_reads/`, 47 of 47 reads in run F
came from it).

```
run A: python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
         --out logs/runs/A_baseline_traced.xlsx --json logs/runs/A_baseline_traced.json \
         --retry-trace --trace-out logs/runs/A_retry_trace.jsonl --concurrency 5
run F: python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
         --out logs/runs/F_final.xlsx --json logs/runs/F_final.json \
         --retry-trace --trace-out logs/runs/F_trace.jsonl --concurrency 5
```

---

## Registry identifiers

| | Supplied | Before (A) | After (F) |
|---|---:|---:|---:|
| `ror_id` present | 14 | 14 | 15 |
| `lei_id` present | 24 | 24 | 24 |
| **either** | — | 33 | **34** |
| Stage 5 retry attempts | — | 9 | 9 |
| Stage 5 retry hits (ROR / GLEIF) | 0 / 0 | 0 / 2 | 0 / 2 |

Unchanged, as expected: **Fix 1 made no behavioural change** (see
`retry_trace_findings.md` — bucket 1 was empty). The ±1 movement is run-to-run
variance in which name the canonicaliser proposes.

## Flags

| Flag code | Supplied | Before (A) | After (F) |
|---|---:|---:|---:|
| `low-confidence-unchanged` | 37 | 36 | **18** |
| `domain-unverified` | 20 | 19 | **16** |
| `unverified-inference` | 3 | 3 | 3 |
| `person-unresolved` | 1 | 1 | 0 |
| `no-match` | 0 | 0 | 1 |
| **records carrying any flag** | **55** | **54** | **34** |

**Records needing review fall from 54 to 34 — a 37% reduction** — and every
withdrawal is evidence-backed rather than a loosened threshold. The single
`no-match` is a pre-existing path newly triggered by LLM routing variance and is
analysed in `unchanged_split_report.md`; the record is still flagged.

## Domains

| | Supplied | Before (A) | After (F) |
|---|---:|---:|---:|
| accepted (`Domain` populated) | 58 | 57 | 60 |
| unverified (flagged, candidate discarded) | 20 | 19 | 16 |
| **withdrawn by page read** | — | 0 | **0** |
| `operating_name` written | 0 | 0 | **16** |

Zero withdrawals is the correct result, not an inactive feature: the one record
in the batch meeting both withdrawal conditions had its candidate *already*
rejected by the ownership guard, so there was nothing to take back. See
`corroborator_report.md`.

## The three unchanged-Name-1 states

| | Before (A) | After (F) |
|---|---:|---:|
| `unchanged-verified` | — | **24** |
| `unchanged-confirmed` | — | **6** |
| `unchanged-unresolved` | — | **18** |
| *(retained-Name-1 population)* | 41 | 48 |

Corroboration behind the 24 verified: 12 page reads, 9 name-matched domains,
3 on-domain search matches.

## Page reads

| | After (F) |
|---|---:|
| attempted | 47 |
| `corroborated` | 16 |
| `no_identity` | 19 |
| `name_mismatch` | 6 |
| `contradicted` | 3 |
| `fetch_unavailable` | 3 |
| `parked` | 0 |
| domains withdrawn | 0 |
| `domain-unverified` flags cleared | 1 |

## Enrichment status (DATAshaper severity input)

| | Before (A) | After (F) |
|---|---:|---:|
| `enriched` — no issue | 47 | 39 |
| `verified` — Info, confirmed correct | 0 | **30** |
| `unresolved` — Warning, manual review | 52 | 31 |
| `failed` — Error, investigate | 1 | **0** |

The 30 `verified` are Fix 2's corroborated and confirmed records: the pipeline
declines to flag them, so it must not simultaneously ask a steward to review
them. `failed` reaching 0 is a defect fix — a Fix 2 short-circuit initially left
12 records at the `failed` default, which would have shipped 12 spurious "Error"
severities.

## Name 1 stability

11 of 100 Name 1 values differ between run A and run F. None is caused by the
page corroborator, which is asserted not to write `name1_enriched`
(`test_a_batch_through_the_corroborator_keeps_name1_byte_identical`). The
breakdown:

* **5 are Fix 2 working as specified** — a canonicalisation proposal that equals
  the input under `normalize_key` no longer replaces the record's punctuation
  with the model's: `AgraQuest, Inc.` → `AgraQuest Inc`, `Allnex USA Inc.` →
  `Allnex USA Inc`, `Aprecia Pharmaceuticals, LLC` → `Aprecia Pharmaceuticals
  LLC`, `Amylin Pharmaceuticals, Inc.` → `Amylin Pharmaceuticals, Inc`,
  `Adaptive Surface Technologies, Inc.` → `Adaptive Surface Technologies, Inc`.
* **6 are run-to-run LLM variance**, unrelated to any fix: `Acrotein Chembio
  Inc.` ↔ `Acrotein ChemBio Inc`, `Advanced Composites Inc` ↔ `Advanced
  Composites Inc.`, `AmeriQual Foods` ↔ `AmeriQual Foods, LLC`, `AquaPhoenix
  Scientific, Inc.` ↔ `AquaPhoenix Scientific`, and two Tier 3 guesses on
  opaque inputs (`Aldrich APL`, `ATC Automation`) that are wrong in **both**
  runs and differently wrong in each — a pre-existing Tier 3 problem, outside
  the scope of these three fixes and worth its own investigation.

---

## Test suite

```
python -m pytest -q
1893 passed, 5 failed
```

The 5 failures are **pre-existing** and were verified against a clean `git
stash` of this work at `HEAD`:

* `test_name_slot_parity.py::test_department_in_a_lower_slot_is_not_reported_missing`
* `test_orchestrator.py::test_tier1_full_resolution`
* `test_orchestrator.py::test_web_search_fallback_for_name1`
* `test_orchestrator.py::test_web_search_determines_record_type`
* `test_routes.py::test_issues_compare_segments_g6_and_g7_out_of_the_metric`

The brief mentions two known pre-existing failures; there are five. None was
introduced here and none was fixed here — they are outside the scope of these
three fixes.

New tests, all passing: `test_retry_trace.py` (11), `test_unchanged_state.py`
(17), `test_page_corroborator.py` (40). One existing test was updated for an
intentional signature change (`test_flags.py::test_render_of_nothing_is_the_unflagged_record`
now expects the `flag_notes` key alongside `flag_details`).

During development the suite caught two real defects in this work before they
reached a batch run — a Stage 0 short-circuit being wrongly classified by Fix 2,
and the `flag_notes` addition changing `render()`'s contract.
