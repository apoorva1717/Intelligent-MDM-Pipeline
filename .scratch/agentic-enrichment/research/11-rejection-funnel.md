# 11 — The rejection funnel, measured

Date: 2026-08-29. Every number below came out of executed code, not out of reading it.

## How it was produced

Two corpora, two harnesses, cross-validated against each other.

| | corpus | route | registry calls |
|---|---|---|---|
| **A** | `docs/thesis/chemspeed_us_100.xlsx` (100) | full pipeline, `scripts/run_batch.py`, **live** ROR/GLEIF/SERP/LLM | ror 297, gleif 263 (run 1, cold) |
| **B** | same 100 | same, re-run on the warm evidence cache | ror 5, gleif 0 |
| **C** | same 100 | `drive_tier1.py` — preprocess → `call_ror`/`call_lei` only | cached |
| **D** | `docs/results/demo_S2_large_corporate_100_v1 (1)_enriched.xlsx` + `..._S3_government_labs_100_v1 (1)_enriched.xlsx` (200 labelled) | `drive_tier1.py` | live |

`MOCK_EXTERNAL_CALLS` was **not** set anywhere — these are real ROR and GLEIF answers.

**Harness C was validated against the full pipeline before being trusted on corpus D**: on
the same 100 records it reproduces the full run's ROR gate table cell-for-cell and its outcome
exactly (`ror_id` 11, `lei_id` 17). So the direct-drive numbers on the 200 labelled records are
comparable to pipeline numbers.

Scripts (durable): `.scratch/agentic-enrichment/scripts/drive_tier1.py`,
`.scratch/agentic-enrichment/scripts/aggregate_funnel.py`.
Raw event logs: `.scratch/agentic-enrichment/tmp/funnel_100b.jsonl` (A/B),
`funnel_s2s3.jsonl` (D).

### The ticket's premise about instrumentation was half right

`_note_rejection` and `ProvenanceLog.reject` do cover the *guards* — country, ambiguity,
short-name, GLEIF name verification. They do **not** cover three things the funnel needs:

1. **ROR gate 1 vs gate 2 are indistinguishable.** "no `chosen` candidate" and "`chosen` scored
   below `ROR_CONFIDENCE_THRESHOLD`" share one branch in `tier1_ror.py` and emit only a
   `logger.info`; neither produces a rejection record.
2. **Gate 3 records the local score without ROR's.** `_note_rejection(_guard, org, local_score, …)`
   drops `ch["score"]` — precisely the pairing this ticket asks for.
3. **The provenance log caps at `MAX_REJECTIONS_PER_FIELD = 3` per field per record**, so it
   cannot be used for candidate-level counting (GLEIF alone produced 115 name-guard rejections
   across 95 lookups on corpus A).

So counting-only instrumentation was added — see "What was added" at the end.

---

## The per-gate loss table

### ROR — corpus A/B (100 records, 100 lookups, 1:1)

| # | gate | lookups reaching it | **died here** |
|---|---|---|---|
| — | lookup entered | 100 | — |
| **1** | affiliation returned **no `chosen`** candidate | 98 | 0 ¹ |
| **2** | `chosen` present, **ROR score < 0.8** | 0 | 0 |
| **3** | `chosen` accepted by ROR, **rejected by the local rapidfuzz rescore** | 1 | 0 ¹ |
| **4** | affiliation **country guard** `_country_ok` | 0 | 0 |
| — | affiliation short-name guard | 0 | 0 |
| — | affiliation **accepted** | 1 | **1 hit** |
| — | query endpoint: ROR API error (HTTP 500) | 1 | **1** |
| — | query endpoint: 0 items after both strategies | 9 | **9** |
| **6** | query endpoint: `REGISTRY_AMBIGUITY_MARGIN` near-tie | 4 | **4** |
| **5** | query endpoint: **best score below threshold** | 75 | **75** |
| **7** | query endpoint: collision-prone short name | 0 | 0 |
| — | query endpoint **accepted** | 10 | **10 hits** |

¹ gates 1 and 3 are not terminal — a lookup that fails them falls through to the query endpoint
and dies (or survives) there. Column 3 counts where the lookup *ended*. 1 + 9 + 4 + 75 + 10 + 1 = 100.

**ROR identity: 11/100.**

Which local guard capped the score at gate 5 (and gate 3):

| cap | count |
|---|---|
| `distinctive_token` | 58 |
| `identifier_token` | 7 |
| both | 7 |
| uncapped (plain ratio short of 0.8) | 4 |

### ROR — corpus D (200 labelled records)

| gate | lookups reaching it | died here |
|---|---|---|
| affiliation no `chosen` | 110 | 0 |
| `chosen` below ROR threshold | 0 | 0 |
| **local rescore reject** | **10** | 0 |
| country guard (affiliation) | 0 | 0 |
| affiliation accepted | 35 | **35 hits** |
| query: 0 items | 1 | 1 |
| query: ambiguity near-tie | 5 | 5 |
| query: below threshold | 87 | 87 |
| query: short-name guard | 0 | 0 |
| query accepted | 25 | **25 hits** |
| ROR API error | 3 | 3 |

**ROR identity: 86/200** (a further note: 1 lookup had wrong-country candidates dropped, 0 died of it).

### GLEIF — corpus A/B (95 lookups; 5 records never reached GLEIF)

| gate | lookups | died here |
|---|---|---|
| exact `filter[entity.legalName]` + ACTIVE + country → **verified** | 15 | **15 hits** |
| exact → no verified candidate | 80 | 0 (falls through to fuzzy) |
| `fuzzycompletions` returned **nothing** | 53 | **53** |
| fuzzy candidates resolved, none verified | 25 | **25** |
| fuzzy → verified | 2 | **2 hits** |

**GLEIF identity: 17/95.**

Candidate-level guard rejections inside those lookups:

| phase | guard | candidates refused |
|---|---|---|
| exact | `gleif_name_verification` (score < 88) | 115 |
| exact | `short_name_uncorroborated` | 3 |
| exact | `gleif_country` | 0 |
| fuzzy | `gleif_country` | 83 |
| fuzzy | `gleif_name_verification` | 10 |
| fuzzy | `short_name_uncorroborated` | 2 |
| either | `registry_ambiguity` | 0 |

### GLEIF — corpus D (158 lookups)

| gate | lookups | died here |
|---|---|---|
| exact → verified | 16 | **16 hits** |
| exact → no verified candidate | 102 | 0 |
| `fuzzycompletions` returned nothing | 77 | **77** |
| fuzzy, none verified | 15 | **15** |
| fuzzy → verified | 10 | **10 hits** |

Guards: exact `gleif_name_verification` 160, exact short-name 1; fuzzy country 33,
fuzzy name 24, fuzzy short-name 1; ambiguity 0.

### Joint outcome, corpus A/B (100 records)

ROR 11 · GLEIF 17 · **either 24** · neither 76. The 76 losses:

| ROR ended at | GLEIF ended at | records |
|---|---|---|
| query below threshold | fuzzy: no completions | 49 |
| query below threshold | fuzzy: none verified | 11 |
| query: 0 items | fuzzy: none verified | 6 |
| ambiguity near-tie | fuzzy: none verified | 4 |
| query below threshold | (GLEIF not called) | 3 |
| query: 0 items | fuzzy: no completions | 2 |
| ROR HTTP 500 | fuzzy: no completions | 1 |

---

## Gate 3 — every (ROR score, local score) pair

All 11 events across all 300 records. Nine distinct queries; the duplicates are two records with
the same normalised name racing past each other's memory cache.

| corpus | query | ROR score | local score | cap | ROR chose | correct to reject? |
|---|---|---|---|---|---|---|
| A | `Apollo Organic Synthesis` | **1.000** | 0.348 | distinctive_token | Flying Dutchmen | **yes** — unrelated org |
| D | `Kimberly-Clark Corp` | **1.000** | 0.421 | distinctive_token | Clark Art Institute | **yes** — unrelated org |
| D | `Intelligent Epitaxy Technology Inc` | **1.000** | 0.467 | distinctive_token | IntelliEPI (United States) | **no** — same company, brand name |
| D | `Vamc Miami Visn 8` ×2 | **1.000** | 0.213 | distinctive_token | VA Sunshine Healthcare Network | debatable — parent network, not this VAMC |
| D | `Vamc Redding Visn 21` ×2 | **1.000** | 0.267 | distinctive_token | VA Sierra Pacific Network | debatable — parent network |
| D | `Vamc Temple Visn 17` | **1.000** | 0.286 | distinctive_token | VA Heart of Texas Health Care Network | debatable — parent network |
| D | `Vamc Martinez Visn 21` | **1.000** | 0.304 | distinctive_token | VA Sierra Pacific Network | debatable — parent network |
| D | `VA MC West la Visn 22` | **1.000** | 0.281 | distinctive+identifier | VA Desert Pacific Healthcare Network | debatable — parent network |
| D | `Vamc West la Visn 22` | **1.000** | 0.286 | distinctive_token | VA Desert Pacific Healthcare Network | debatable — parent network |

Every ROR score is exactly **1.000** and every local score is **0.21–0.47**. So the
high-ROR/low-local population the hypothesis predicted *does exist* — it is just not what the
hypothesis said it was.

* **2 of 9 are unambiguous ROR false positives** — a shared generic token (`Clark`, an
  `Apollo`/`Dutchmen` coincidence) that ROR's affiliation scorer saturates on. These are the
  `EMSL Analytical` → `ASL Analytical` case the gate was built for, caught in the wild.
* **6 of 9 are VA medical centers where ROR offered the parent VISN network.** Accepting them
  would attach the regional network's identity to a specific medical center. That is a
  parent-vs-child policy question, not a fuzzy-matching one, and it is the same question
  `tier1_ror`'s own parent-match path already reasons about.
* **1 of 9 is a genuine wrong rejection** — `Intelligent Epitaxy Technology Inc` → `IntelliEPI`.
  One record in 300.

**Not a single case is an abbreviation the expansion machinery failed to reach.** The refutation
already recorded in ticket 13 (`rescore_names` carries `expand_abbreviations` +
`_expand_state_abbrevs`, and `_score_org` step 1 exact-matches ROR's acronym variants) holds
under measurement: the abbreviation population never reaches gate 3, because it passes.

---

## What actually kills records

### 1. Absence from the registry, not strictness (corpus A, ~60–70 of the 76 losses)

The 75 records that died at ROR gate 5 have a median top-candidate local score of **0.636**, and
the candidates are visibly *different organisations*:

```
0.700 ACTEGA NORTH AMERICA           -> AO North America
0.700 Adaptive Surface Technologies  -> Innovative Surface Technologies
0.700 Alliance Rubber Company        -> Coda Alliance
0.700 Alora Pharmaceuticals          -> NovoBiotic Pharmaceuticals
0.700 Alsym Energy                   -> CMS Energy
0.700 Amylin Pharmaceuticals, Inc    -> Zentalis Pharmaceuticals
0.700 American Coatings Association  -> American Nurses Association
0.696 1st Source Research, Inc       -> Western Research Company, Inc.
```

`_score_org` step 1 is an exact match against *any* ROR name variant. If ROR held these
organisations under any name or acronym, the score would be 1.0, not 0.70. It doesn't.
The same 49 of them get **zero `fuzzycompletions` from GLEIF**. Corpus A is a list of small
private US chemical and laboratory suppliers: ROR indexes research organisations, GLEIF indexes
entities that hold an LEI, and most of these are neither. **24% registry identity on this batch is
a coverage ceiling, not a gate failure.** Corpus D, whose names are large corporates and
government labs, reaches 86/200 ROR and 26/158 GLEIF through the *same* gates.

### 2. ROR's affiliation endpoint is nearly inert on commercial names — and loosening it would hurt

98/100 lookups on corpus A got no `chosen` candidate at all (110/200 on corpus D). Directly
against the live API:

```
"Pfizer Inc, New York, NY, United States"  -> items=10, chosen=0, top: WRA Environmental Consultants (0.80)
"Sekisui Xenotech, Kansas City, US"        -> items=10, chosen=0, top: City UC (0.86)
"Massachusetts Institute of Technology, …" -> chosen=1: Instituto Tecnológico de Massachusetts (1.00)
```

Measured (`tmp/aff_context_probe.py`, 100 records × 2 affiliation forms): dropping the
city/state/country context from the affiliation string raises `chosen ≥ 0.8` from **2 to 7** —
but 6 of those 7 are wrong (`ACTEGA NORTH AMERICA` → `AO North America` 0.97, `AFB International`
→ `CAB International` 0.97, `Alliance Rubber Company` → `Alliance` 1.00). Only `Arkema` → `Arkema`
is a real gain. The location context is *suppressing false positives*, and the local rescore would
have to catch the rest. This is not a cheap win.

### 3. The one gate with a measured wrong-rejection population is **GLEIF's name threshold (88)**

Not the ROR rescore. Name-guard rejections scoring 78–88 (i.e. just under the bar):

| corpus | query | score | GLEIF candidate | verdict |
|---|---|---|---|---|
| D | `Dow Chemical` | 85.71 | THE DOW CHEMICAL COMPANY | **wrong rejection** |
| D | `Expeditors International of` | 83.08 | EXPEDITORS INTERNATIONAL OF WASHINGTON, INC. | **wrong rejection** |
| D | `Charles River Laboratories, Inc` | 78.79 | CHARLES RIVER LABORATORIES INTERNATIONAL, INC. | likely wrong rejection |
| A | `Ascend Performance Materials` | 83.58 | Ascend Performance Materials Operations LLC | likely wrong rejection |
| D | `ExxonMobil` | 83.33 | EXXONMOBIL OIL CORPORATION | subsidiary — debatable |
| D | `Varian Medical Systems, Inc` | 84.62 | VARIAN MEDICAL SYSTEMS PACIFIC, INC. | subsidiary — debatable |
| D | `McKesson Medical-Surgical Inc` | 80.56 | MCKESSON MEDICAL-SURGICAL TOP HOLDINGS INC. | holding co — debatable |
| D | `Bayer Pharmaceuticals` | 79.25 | Bayer Healthcare Pharmaceuticals Inc. | debatable |
| **A** | **`ABB Inc`** | **87.50** | **Abby Inc.** | **correct rejection — 0.5 below the bar** |
| D | `Bristol-Myers Squibb Company` | 81.16 | Bristol-Myers Squibb Company Master Trust | correct rejection |
| A | `Apex LLC` | 84.21 | Apex TH LLC / Apex SW LLC / Apex 41 LLC | correct rejection |
| D | `Department of Veterans Affairs` | 78.95 | State of Oregon Department of Veterans Affairs | correct rejection |

8 near-misses on corpus A, 16 on corpus D. Roughly a third look recoverable. But `ABB Inc` vs
`Abby Inc.` at 87.50 shows the number is load-bearing: a threshold move alone trades one error
for another. The discriminating structure is *containment* (`X` ⊂ `THE X COMPANY`,
`X` ⊂ `X INTERNATIONAL INC`) versus *substitution* (`ABB` → `Abby`), which
`token_sort_ratio` cannot express. That is a real gate-composition question — on the GLEIF path.

### 4. Two silent losses worth their own line

* **ROR HTTP 500 on `20/15 Visioneers`** (and on `Slac/su_mcculsimes`, `County of Sacramento
  PH/Laboratory Svsc` in corpus D). The `/` in the name breaks ROR's query parser; the record
  falls out of Tier 1 with `matched: False`, indistinguishable from a genuine miss. 3 records in
  300. Reproduced live, twice.
* **`1910 Genetics` reaches ROR as `Genetics`.** Preprocessing strips the leading numeral, and the
  bare token then triggers the ambiguity guard (`Baylor Genetics` vs `Myriad Genetics`, both 1.00)
  and 10 GLEIF name-guard rejections. A preprocessing defect, charged to the registry gates.

---

## Verdict on ticket 13

**The gate-composition hypothesis is dead, on the ROR path.**

Ticket 13 exists on the claim that the local rapidfuzz rescore discards the abbreviation cases
ROR gets right, and its own recorded refutation asked for exactly this measurement: "either find a
*measured* population it wrongly rejects (ticket 11, gate 3 …), or close this ticket as out of scope."

The measurement:

* Gate 3 fires on **1 of 100** records in corpus A and **10 of 200** in corpus D — **9 distinct
  queries in 300 records, 3%**.
* Of those 9, **2 are ROR false positives the gate correctly stopped**, 6 are a parent-network
  substitution (a policy question, not a scoring one), and **1 is a genuine wrong rejection**.
* **Zero are the abbreviation signature.** Not one MIT-shaped case reached the gate.
* Redesigning gate 3 perfectly — recovering every single case it refuses, including the two it is
  right to refuse — would move corpus A from 24% to at most 25% registry identity.

Meanwhile the gates it shares a ticket with are quieter still: the **ROR country guard rejected
0 candidates in 300 records**, the **short-name rule rejected 0**, and the **ambiguity margin
refused 4 + 5** (and one of those 9, `Genetics`, is a preprocessing artefact). Question 5 of
ticket 13 — "what does the change cost in precision?" — now has an answer: the change costs
precision and buys ~1 record in 300.

**What survives of 13.** Question 4 ("same questions for the GLEIF path — does `fuzzycompletions`
get second-guessed the same way?") is the only limb with measured evidence behind it, and the
answer is yes: GLEIF's 88 threshold refuses 309 candidates across the two corpora (125 on A, 184 on D), of which a
minority are legal-suffix or parent-name containments of the query that look recoverable. If 13 is
kept, it should be **re-scoped to the GLEIF name threshold and the containment-vs-substitution
distinction**, and the ROR limb closed.

**What this says about the map.** The destination is "find out where enrichment actually loses
records". On the chemspeed corpus it loses them because **ROR and GLEIF do not contain these
organisations** — 49 of the 76 losses got a below-threshold ROR candidate *and* zero GLEIF
typeahead completions. No gate redesign, and no agent lane, recovers an organisation that is not
in the registry. The registry-verifier premise underneath the agent lane (map decision 3: "the
agent chooses queries; deterministic checks own verification") has a ceiling on this corpus that
the funnel now measures: **at most ~25% of these records can ever be registry-verified**, however
good the retrieval gets. Whatever recovers the remaining 76 has to come from web evidence, not
from a registry — which is a different lane with a different verification story.

## What was added (counting-only instrumentation)

New file `enrichment/funnel_probe.py` (~60 lines): `ENABLED` is read once at import from
`FUNNEL_PROBE` (**absent → off**), `event(**fields)` appends one JSON line to `FUNNEL_PROBE_OUT`,
`next_call_id()` correlates the events of one lookup. It reads and writes nothing but that file.

Call sites, all of the form `funnel_probe.event(...)` on a line of their own:

* `enrichment/tier1_ror.py` — 12 sites: lookup entry; affiliation no-`chosen` / below-ROR-threshold
  (split), local-rescore reject **carrying `ch["score"]` beside `local_score`**, country reject,
  short-name reject, accept; query 0-items, country-drop note, ambiguity reject, below-threshold,
  short-name reject, accept; frozen miss; error.
* `enrichment/tier1_lei.py` — 7 sites: lookup entry; exact phase outcome (+ that phase's slice of
  `guard_rejections`); fuzzy no-completions; fuzzy outcome (+ its slice); frozen miss; 2 error
  paths. Plus one keyword-only `probe_call: int = 0` parameter on the private `_fuzzy_lookup`.
* `enrichment/registry_match.py` — **untouched.** Gates 6 and 7 were already fully recorded through
  `_note_rejection` / the GLEIF `rejections` list.

No decision reads a probe value; no record, flag, provenance entry or scoped field is touched.
Full suite after the change: **5 failed, 2815 passed, 5 skipped** — the documented baseline,
same five failures.
