# 11 — Where do records actually die?

Type: task
Status: measured — see ## Findings
Blocked by: —

## Question

Convert "results are poor" into a per-gate loss table. For a real batch
(`docs/thesis/chemspeed_us_100.xlsx`), count how many records are rejected at each gate:

**ROR path** (`enrichment/tier1_ror.py`)
1. ROR affiliation returned no `chosen` candidate at all
2. `chosen` present but ROR's own score < `ROR_CONFIDENCE_THRESHOLD` (0.8)
3. **`chosen` accepted by ROR but rejected by the local rapidfuzz rescore** — and for each, record
   ROR's score *and* the local score side by side. This is the gate under suspicion.
4. Rejected by the country guard (`_country_ok`)
5. Query-endpoint fallback: best score below threshold

**GLEIF path** (`enrichment/tier1_lei.py`) — the equivalent funnel through `fuzzycompletions`,
the name-match threshold (88), and the status/country filters.

**Shared guards** (`enrichment/registry_match.py`)
6. Refused by `REGISTRY_AMBIGUITY_MARGIN` (near-tie -> no match)
7. Refused by the collision-prone short-name rule

## Why this is first

It is hours, not days: `_note_rejection` and the provenance rejection log already record most of
this — the work is aggregating and rendering it, not building instrumentation. And it either
confirms the gate-composition hypothesis (13) or kills it, which decides whether an expensive
agent lane is even aimed at the right target.

## Deliverable

A table of counts per gate, plus — for gate 3 specifically — the paired (ROR score, local score)
for every rejected record, since the hypothesis is that high-ROR/low-local is the abbreviation
population being wrongly discarded.

## Findings (2026-08-29) — measured, live registries

Full write-up: [`research/11-rejection-funnel.md`](../research/11-rejection-funnel.md).
Corpora: `chemspeed_us_100.xlsx` through the **full pipeline, live** (`scripts/run_batch.py`,
no `MOCK_EXTERNAL_CALLS`), plus the 200 labelled `docs/results/*_enriched.xlsx` records through a
direct Tier-1 harness that was first validated to reproduce the full run's ROR gate table and
outcome exactly.

### The ticket's premise was half right

`_note_rejection` covers the guards, but **not** the funnel's first two steps (ROR's "no `chosen`"
and "`chosen` below threshold" share one branch and emit only a `logger.info`), and gate 3 records
the local score *without* ROR's — the exact pairing this ticket asks for. The provenance log also
caps at `MAX_REJECTIONS_PER_FIELD = 3`. Counting-only instrumentation was therefore added:
`enrichment/funnel_probe.py` (new, off unless `FUNNEL_PROBE` is set) plus 12 `event(...)` call
sites in `tier1_ror.py` and 7 in `tier1_lei.py`. `registry_match.py` untouched — gates 6 and 7
were already fully recorded. No decision reads a probe value. Suite unchanged at the documented
baseline (5 failed / 2815 passed / 5 skipped).

### Per-gate loss, chemspeed 100 (100 ROR lookups, 1:1 with records)

| # | gate | reached it | **ended here** |
|---|---|---|---|
| 1 | affiliation returned no `chosen` | 98 | 0 (falls through) |
| 2 | `chosen` but ROR score < 0.8 | **0** | 0 |
| 3 | `chosen` refused by the local rescore | **1** | 0 (falls through) |
| 4 | affiliation country guard | **0** | 0 |
| — | affiliation accepted | 1 | 1 hit |
| — | ROR API HTTP 500 | 1 | **1** |
| — | query endpoint: 0 items | 9 | **9** |
| 6 | `REGISTRY_AMBIGUITY_MARGIN` near-tie | 4 | **4** |
| 5 | query endpoint below threshold | 75 | **75** |
| 7 | collision-prone short name | **0** | 0 |
| — | query endpoint accepted | 10 | 10 hits |

GLEIF, 95 lookups: exact verified **15**; exact unverified 80 → `fuzzycompletions` empty **53**,
resolved-but-unverified **25**, fuzzy verified **2**. Candidate-level: 125 `gleif_name_verification`
refusals, 83 `gleif_country`, 5 short-name, **0** ambiguity.

Joint: ROR 11 · GLEIF 17 · **either 24 / 100**. 49 of the 76 losses got a below-threshold ROR
candidate *and* zero GLEIF typeahead completions.

### Gate 3 — the paired scores (all 300 records, 9 distinct queries)

Every ROR score is **1.000**; every local score is **0.21–0.47**.

| query | ROR | local | ROR chose | right to reject? |
|---|---|---|---|---|
| `Apollo Organic Synthesis` | 1.000 | 0.348 | Flying Dutchmen | **yes** |
| `Kimberly-Clark Corp` | 1.000 | 0.421 | Clark Art Institute | **yes** |
| `Intelligent Epitaxy Technology Inc` | 1.000 | 0.467 | IntelliEPI (United States) | **no** |
| `Vamc Miami Visn 8` | 1.000 | 0.213 | VA Sunshine Healthcare Network | parent network |
| `Vamc Redding Visn 21` | 1.000 | 0.267 | VA Sierra Pacific Network | parent network |
| `Vamc Temple Visn 17` | 1.000 | 0.286 | VA Heart of Texas Health Care Network | parent network |
| `Vamc Martinez Visn 21` | 1.000 | 0.304 | VA Sierra Pacific Network | parent network |
| `VA MC West la Visn 22` | 1.000 | 0.281 | VA Desert Pacific Healthcare Network | parent network |
| `Vamc West la Visn 22` | 1.000 | 0.286 | VA Desert Pacific Healthcare Network | parent network |

**Not one abbreviation case.** 2 are ROR false positives the gate correctly stopped, 6 are a
parent-vs-child policy question, 1 is a genuine wrong rejection — in 300 records.

### Verdict on ticket 13: the ROR limb is KILLED

Gate 3 fires on 3% of records and is right about most of them; a perfect redesign moves chemspeed
from 24% to at most 25% registry identity. The country guard rejected **0** in 300 records and the
short-name rule rejected **0**. Ticket 13's own question 5 ("what does the change cost in
precision?") now has an answer: it costs precision and buys ~1 record in 300. **Close the ROR limb.**

What survives is **question 4, the GLEIF path**: the 88 name threshold refuses 309 candidates
across the two corpora, and the 78–88 band contains real losses (`Dow Chemical` → `THE DOW CHEMICAL
COMPANY` at 85.71, `Expeditors International of` → `EXPEDITORS INTERNATIONAL OF WASHINGTON, INC.`
at 83.08) sitting next to a correct rejection at 87.50 (`ABB Inc` → `Abby Inc.`). The discriminator
is containment vs substitution, which `token_sort_ratio` cannot express. If 13 is kept, re-scope it
to that.

### The bigger finding for the map

On chemspeed the records die because **the organisations are not in the registries**, not because
the gates are strict: the 75 below-threshold ROR candidates have a median local score of 0.636 and
are visibly *different companies*, and `_score_org` step 1 would return 1.0 if ROR held them under
any name or acronym. Corpus D — large corporates and government labs — clears the *same* gates at
86/200 ROR. **~24-25% is a coverage ceiling on this corpus, not a gate failure**, which bounds what
any registry-verified lane (agentic or not) can recover here.

Two silent losses also surfaced: ROR returns **HTTP 500** on names containing `/`
(`20/15 Visioneers`, `Slac/su_mcculsimes`, `County of Sacramento PH/Laboratory Svsc` — 3 in 300),
indistinguishable downstream from a genuine miss; and `1910 Genetics` reaches ROR as `Genetics`
because preprocessing strips the leading numeral, which then trips the ambiguity guard.
