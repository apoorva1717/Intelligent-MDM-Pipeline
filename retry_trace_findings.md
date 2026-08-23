# Fix 1 — Stage 5 retry trace: findings

**Question.** In the chemspeed 100-row US SMB batch, 24 records received an
LLM-canonicalised Name 1 and **zero** of them gained a `ror_id` or `lei_id` from
`Orchestrator._retry_tier1_after_canonicalisation`. Is that a wiring defect, a
guard misfire, or the mechanism working correctly against a registry that has
nothing to offer?

**Answer.** Working correctly. **Bucket 1 (wiring) is empty** — the retry was
reached on 100 of 100 records. The zero-hit result is fully explained by
bucket 2 (the "corrected" name is the queried name modulo punctuation) and
bucket 4 (the registry genuinely missed). **No code change was made.**

---

## What was built

`RETRY_TRACE` (config, default `false`) — a diagnostic-only flag following the
existing `WEBSITE_TRACE` pattern. When on, `Orchestrator._emit_retry_trace`
writes one JSON line per **finalised** record to the `enrichment.trace.retry`
logger. Behaviour with the flag off is byte-identical: the only unconditional
addition is a transient `_retry_trace` dict on the working record, popped
before pydantic validation.

Counters per record, exactly as specified:

| Field | Meaning |
|---|---|
| `retry_eligible` | judged from the **shipped** record: a tier (not the input passthrough) authored `name1_enriched`, and no `ror_id`/`lei_id` is present |
| `retry_skipped_reason` | `not_called_on_this_path` / `already_has_id` / `normalize_key_equal` / `already_attempted` / `other:tier1_never_ran` / `other:no_name1` |
| `retry_fired` | the retry passed every gate and queried a registry |
| `retry_registry_queried` | `["ror"]`, `["ror","gleif"]`, … in call order |
| `retry_guard_rejected` | every guard rejection the registry client reported during the retry — guard name, candidate, score, threshold |
| `retry_hit` | `"ROR"` \| `"gleif"` \| `null` |

`retry_eligible` is deliberately derived at the emission site rather than
inside the retry, so a record the retry *never reached* is still counted as
eligible — otherwise the wiring defect the trace exists to find would be
invisible to its own counter.

Supporting tooling: `scripts/run_batch.py` (runs an XLSX batch through the real
orchestrator offline, exactly as `POST /enrich/file` does) and
`scripts/retry_trace_report.py` (classifies a trace into the four buckets).

## The run

```
python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
    --out logs/runs/A_baseline_traced.xlsx --json logs/runs/A_baseline_traced.json \
    --retry-trace --trace-out logs/runs/A_retry_trace.jsonl --concurrency 5
```

100 records, live ROR / GLEIF / SerpAPI / Azure OpenAI, 313.6 s.

The supplied evidence workbook `chemspeed_us_100_enriched.xlsx` was **not**
overwritten; this run's output is `logs/runs/A_baseline_traced.xlsx`. The two
agree closely — the pipeline is LLM-driven and therefore not bit-reproducible:

| | supplied baseline | traced run A |
|---|---|---|
| Name 1 kept from input (`input:1:rule`) | 42 | 41 |
| Name 1 authored by an LLM | 24 | 25 |
| `low-confidence-unchanged` | 37 | 36 |
| `domain-unverified` | 20 | 19 |
| `ror_id` / `lei_id` | 14 / 24 | 14 / 24 |
| Stage 5 retry hits | 0 | 2 |

Rows are joined between input and output by position; Name 1 is unique across
all 100 input rows, so the trace's `name1_original` is an unambiguous key too.

## Counter totals (run A, 100 records)

```
records traced            : 100
retry reached (called)    : 100      <- bucket 1 is empty
retry_eligible            : 25
retry_fired               : 9
retry_hit                 : 2        (both GLEIF)

retry_skipped_reason (all 100 records):
  normalize_key_equal        59
  already_has_id             31
  (not skipped — fired)       9
  other:tier1_never_ran       1      (the person-unresolved row: Name 1 held a
                                      person, Tier 1 was never queried)
  not_called_on_this_path     0

retry_registry_queried:  ror 9, gleif 9
guard rejections recorded during retries: 77
```

`tier1_retry_attempts=9`, `tier1_retry_hits_ror=0`, `tier1_retry_hits_lei=2` on
the batch summary — the retry mechanism's own telemetry, and it agrees.

## Bucket classification of the LLM-changed population

25 rows carry an LLM-authored Name 1 after the run, plus 2 rows the retry
itself converted to `gleif:1:exact` — 27 rows in the population Stage 5 exists
to serve.

| Bucket | Count | Reading |
|---|---|---|
| **1 — retry never invoked (wiring defect)** | **0** | — |
| **2 — skipped by `normalize_key` equality** | **18** | correct by design |
| **3 — fired, guard rejected a correct candidate** | **0** | no guard misfired |
| **4 — fired, registry genuinely missed** | **7** | coverage limit, not a defect |
| (0 — fired and hit) | 2 | the mechanism working |

### Bucket 2 in detail — the dominant explanation

18 of the 25 eligible rows never reached a registry because the LLM's
"correction" and the string Tier 1 was already queried with are the same name
under `normalize_key` (lowercase, trim, collapse whitespace, strip punctuation,
fold accents). Both of the names named in the brief land here:

* `Allnex USA Inc` → `Allnex USA Inc.` — one full stop.
* `Ascend Performance Materials` → `Ascend Performance Materials` — identical.

Tier 1 had already queried exactly those strings on the first pass and missed.
Re-querying would have cost an API call to receive the same answer, which is
precisely what the `normalize_key` gate exists to prevent. Other members:
`AgraQuest Inc.`→`AgraQuest, Inc.`, `Aprecia Pharmaceuticals LLC`→`Aprecia
Pharmaceuticals, LLC`, `Ameriqual Foods`→`AmeriQual Foods` (case only),
`ALLCHEMY INC`→`Allchemy, Inc.`.

This also holds for the **supplied** baseline, checked offline with no API
calls: of its 24 LLM-authored rows, **16** are `normalize_key`-equal to the
input Name 1 and **8** differ materially. So the supplied run would have fired
8 retries and hit 0 — consistent with the reported zero, and consistent with
run A's shape.

### Bucket 4 in detail — the retry fired and the registry had nothing

7 rows fired, queried ROR then GLEIF (all 7 are company-branch names, so the
research-institution gate never blocked GLEIF), and both registries missed:

`ABGENT`→`Abgent, Inc.` · `ACTEGA NORTH AMERICA`→`Actega North America, Inc.` ·
`Admix`→`Admix, Inc.` · `Alfieri - McBee Corp.`→`Alfieri-McBee Corporation` ·
`Alsym Energy`→`Alsym Energy, Inc.` · `Applied catalyst`→`Applied Catalysts` ·
`AquaPhoenix Scientific`→`AquaPhoenix Scientific, Inc.`

These are private US SMBs. An LEI is required for entities that trade
reportable financial instruments; a 40-person specialty-chemicals firm has no
reason to hold one. The brief's expectation that "Allnex USA Inc." and "Ascend
Performance Materials" plausibly hold LEIs is not contradicted by this batch —
neither of them ever reached GLEIF a second time, because neither was actually
re-spelled (bucket 2). Whether GLEIF holds them under the *first-pass* spelling
is a Tier 1 coverage question, not a Stage 5 question.

### Bucket 3 in detail — no guard misfired

77 guard rejections were recorded across the 9 fired retries. Every one is a
correct refusal:

* **ROR distinctive-token guard** (9 rejections, one per fired retry). Best
  candidate scored 0.38–0.70 against the 0.80 threshold: `Eden Medical` for
  "Admix, Inc.", `Zap Energy` for "Alsym Energy, Inc.", `CC` for
  "Alfieri-McBee Corporation", `ANS` for "Applied Catalysts". None is the
  right entity; the guard is doing exactly its job.
* **GLEIF name-verification guard** (68 rejections). The highest score in the
  entire set is `Air Products and Chemicals Master Trust` at **80.0** against
  the 88 threshold, for query "Air Products and Chemicals, Inc." — a pension
  trust is a genuinely different legal entity from the operating company, and
  that same record then matched the correct entity and took an LEI. Every
  other GLEIF rejection scores 21–71.

**No threshold change is proposed.** Lowering the GLEIF threshold to admit 80
would admit the Master Trust — a wrong-entity LEI on an operating-company
record, which is the exact failure the guard was introduced to stop.

### Side observation, outside the fix's scope

GLEIF's fulltext `legalName` search returns the *same ten unrelated candidates*
for almost every query — `Pruvations Inc 401K Inc`, `GIIG, Inc. TOOT, Inc.`,
`Income Inc.`, `LEI24, INC.`, `AUTHENTIC8, INC.`, `VARRD, Inc.`, `KOBKLEIN,
INC.`, `TRUPEER INC`, `BACKBLAZE, INC.`, `Vaxxinity, Inc.` — which strongly
suggests it is matching on the legal-suffix token ("Inc") rather than on the
distinctive part of the name. The name-verification guard catches all of them,
so nothing wrong ships; but 68 of 77 rejections in this batch are the same ten
names, and a query that stripped the legal suffix before searching would
plausibly surface real candidates instead. Recorded as an **open item** against
`enrichment/tier1_lei.py` — not touched here, because it is a Tier 1 query
change, not a Stage 5 change.

## Verdict and change made

**Bucket 1 is empty, bucket 3 is empty. No repair was made to the retry path.**
The instrumentation is the whole of Fix 1's code delta:

| File | Change |
|---|---|
| `config.py` | `RETRY_TRACE` flag + `Settings.retry_trace` (default off) |
| `enrichment/orchestrator.py` | `_retry_trace*` helpers, trace writes inside `_retry_tier1_after_canonicalisation`, `_emit_retry_trace` called from `_finalise_and_return` |
| `scripts/run_batch.py` | offline batch runner (new) |
| `scripts/retry_trace_report.py` | bucket classifier (new) |
| `tests/test_retry_trace.py` | 11 tests pinning the classification and the no-op-when-off contract |

Regression tests cover: the trace is emitted for every finalised record; each
skip reason is produced by its own condition; a fired retry records the
registries it queried and the guards that rejected; `normalize_key`-equal names
do **not** buy an API call; and with `RETRY_TRACE=false` no line is emitted and
`_retry_trace` never reaches the response model.

---

> **\reviewnote summary.** Instrumenting Stage 5 on the 100-row chemspeed batch
> shows the retry is reached on 100/100 records, so the zero-hit result is not
> a wiring defect. Of 25 records whose Name 1 an LLM authored, 18 were skipped
> because the "corrected" name equals the already-queried name under
> `normalize_key` — punctuation and casing only, including both names cited as
> plausible LEI holders — and 7 fired, queried ROR and GLEIF, and were missed by
> both; 2 further records were rescued by the retry and took an LEI. No guard
> misfired: the highest-scoring rejected candidate in the whole run is a pension
> trust at 80.0 against an 88 threshold, correctly refused. The correct outcome
> was therefore to add the diagnostic and change nothing else, and the residual
> finding is that Stage 5's yield is bounded by GLEIF's coverage of private US
> SMBs rather than by the pipeline.
