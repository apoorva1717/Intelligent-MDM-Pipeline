# Wikidata crosswalk lane — measured on the 100-row chemspeed US SMB batch

**Batch:** `docs/thesis/chemspeed_us_100.xlsx`, 100 rows · **Date:** 2026-08-23 ·
**Lane:** [Stage 2c](README.md#stage-2c-wikidata-crosswalk-lane), `enrichment/wikidata.py`

**Headline: the lane matched 3 records out of 68 it queried, followed 0 registry pointers, and
changed the shipped output of 2 rows out of 100.** That is a hit rate of **4.4 %** of eligible
records and **3 %** of the batch, and it is a real finding rather than a defect: 56 of the 68
queried names return **no Wikidata item at all**. The gauntlet was not tuned to raise this number
and no constraint was relaxed after seeing it.

---

## 1 · How the runs were made

Three full pipeline runs over the same workbook, so the lane's effect could be separated from the
LLM tiers' run-to-run variance:

| Run | Command | Purpose |
|---|---|---|
| **A** (baseline) | `run_batch.py … --no-wikidata` | `WIKIDATA_ENABLED=false` |
| **B** (baseline, repeat) | `run_batch.py … --no-wikidata` | The **noise floor** — same config as A |
| **C** (lane on) | `run_batch.py … --wikidata-trace` | `WIKIDATA_ENABLED=true` |

Before run C, `scripts/wikidata_warm_fixtures.py` recorded every row's Wikidata responses serially
(one request every two seconds): **100 queries warmed, 110 live HTTP requests, zero 429s**. Run C
therefore served **68 of 68** lane invocations from fixtures and issued **zero** live calls, so
the measurement is a replay and not a race against a rate limiter.

Diff tool: `scripts/wikidata_lane_report.py`, which compares every shipped field
(`name1_enriched` … `record_type_provenance`) row by row, paired by batch position — the chemspeed
workbook has no populated `record_id`, so a keyed join would collapse all 100 rows into one.

---

## 2 · Every counter, with real numbers

Run C. All fourteen are zero in runs A and B, which is the config gate working.

| Counter | Value | Reading |
|---|---:|---|
| `wikidata_queried` | **68** | Records that reached the lane eligible. The other 32 already held a ROR or LEI identity from Tier 1 and were skipped by the lane's own precondition. |
| `wikidata_matched` | **3** | Survived all six constraints, uniquely. |
| `wikidata_no_match` | **65** | |
| `wikidata_ambiguous` | **0** | No collision occurred on this batch. |
| `wikidata_unavailable` | **0** | Nothing failed. (Run C replayed fixtures; the *first*, unwarmed run took 28 — see §6.) |
| `wikidata_type_rejected` | **7** | Records where at least one candidate failed the type whitelist. |
| `wikidata_country_rejected` | **0** | Not one candidate reached the country gate and failed it. |
| `wikidata_crosswalk_ror` | **0** | **No matched item carried a `P6782`.** |
| `wikidata_crosswalk_lei` | **0** | **No matched item carried a `P1278`.** |
| `wikidata_crosswalk_registry_hit` | **0** | Nothing to follow, so nothing followed. |
| `wikidata_superseded_flagged` | **0** | No matched item carried `P576` or `P1366`. |
| `wikidata_witness_only` | **3** | Every match was pointerless. |
| `wikidata_domain_corroborated` | **1** | |
| `wikidata_domain_disagree` | **0** | |

`matched + no_match + ambiguous + unavailable = 3 + 65 + 0 + 0 = 68 = queried`. The partition holds.

**Call budget, measured.** Every one of the 68 invocations cost **0 live operations** in run C
(fixture replay). During warming, 100 queries cost **110 HTTP requests** — an average of 1.1 per
query, because 56 queries stop after the search returns nothing and never make the second call.
The conditional third call fired on the three surviving-candidate records whose item carries
`P159` — `Advanced Solutions Life Sciences` (Q43668 = Louisville), `Ares Materials` (Q1439 =
Texas) and `Aprecia Pharmaceuticals` (Q885619 = Blue Ash). Two of those needed it for the country
gate as well: neither Q139902601 nor Q139970047 states `P17` at all, so `P159` → its country is
the only route to constraint 4. No record exceeded three operations.

---

## 3 · The three matched records

| Row | Input Name 1 | QID | Wikidata label | Name score | Pointer | What the lane did | What changed vs run A |
|---|---|---|---|---:|---|---|---|
| 12 | `ABGENT` | [Q4667305](https://www.wikidata.org/wiki/Q4667305) | Abgent | 100.0 | none | **Witness.** Wrote `operating_name`. `P856` = `abgent.com` agreed with the record's candidate domain → `wikidata_domain_corroborated` | `operating_name`: `null` → `Abgent`; `operating_name_provenance`: `null` → `wikidata:2:crosswalk` |
| 28 | `Advanced Solutions Life Sciences` | [Q139902601](https://www.wikidata.org/wiki/Q139902601) | ADVANCED SOLUTIONS LIFE SCIENCES, LLC | 100.0 | none | **Witness — and wrote nothing.** A page read had already established `operating_name` from `advancedsolutions.com`, and the site is the better witness of the two | *(nothing)* |
| 86 | `Ares Materials` | [Q118301114](https://www.wikidata.org/wiki/Q118301114) | Ares Materials | 100.0 | none | **Witness.** Wrote `operating_name` | `operating_name`: `null` → `Ares Materials`; `operating_name_provenance`: `null` → `wikidata:2:crosswalk` |

Two of the three needed the conditional third call to pass at all, and both of the constraints it
serves fired in production:

- **Q139902601** (row 28) states **no `P17`**. Its `P159` is Louisville (Q43668), whose own `P17`
  is `Q30` — so constraint 4 passed only through the headquarters route. The record's city is
  Louisville, so constraint 6 passed on an exact city match.
- **Q118301114** (row 86) has `P159` = **Texas** (Q1439), a state rather than a city, against a
  record in Plano, **TX**. It passed on the region rescue — `_norm_region("Texas")` and
  `_norm_region("TX")` compare equal through the `_US_POSTAL_CODES` map the page corroborator
  already reuses. Without that rescue this match would have been refused on a false city
  contradiction, which is the failure the rescue was written for.

**`name1_enriched` is byte-identical on all three**, which is the rule the lane exists to keep.
Row 12's Name 1 stayed `Abgent, Inc.` from the LLM canonicaliser and row 86's stayed the record's
own `Ares Materials`; the wiki's label went to `operating_name` in both cases, which is what
`operating_name` is for.

Row 28 is the most informative of the three. The lane matched, and correctly did nothing: the
"do not overwrite a page-read identity" rule is not a hypothetical, it fired in production on the
first batch.

**Nothing was crosswalked, so nothing tested the pointer path in production.** The crosswalk is
covered by unit tests (a `P6782` → ROR hit writing registry provenance; a `P1278` → GLEIF record
whose legal name the existing 88-threshold guard refuses) and by nothing on this batch. Reported
as an untested-in-production path, not as a working one.

---

## 4 · Where the other 65 records were lost

This is the substance of the low hit rate, and it is almost entirely upstream of the gauntlet.

| Stage | Records | Share of the 68 |
|---|---:|---:|
| **`wbsearchentities` returned nothing at all** | **56** | **82 %** |
| Reached the gauntlet with ≥ 1 candidate | 12 | 18 % |
| → all candidates rejected | 9 | |
| → exactly one survivor (matched) | 3 | |

Wikidata simply does not have items for private US small and mid-size chemistry and life-science
businesses. That is the finding. No threshold in this lane can change it, and the 56 are not a
tuning opportunity — they are an absence of source data.

Of the 27 candidates the 12 remaining records did produce:

| Rejected by | Candidates | What they actually were |
|---|---:|---|
| `type_rejected` | 21 | 4 scholarly articles, 3 US patents, 3 bare `organization` (Q43229), 2 `type of technology`, 2 `energy company`, 1 each: computer network, retracted paper, software, clinical trial, branch of computer science, type of intelligence, class of being, gas-fired power station |
| `name_rejected` | 2 | |
| `city_rejected` | 1 | |
| survived | 3 | |

The type gate is doing what it was built for: an SMB name search on Wikidata returns papers,
patents and concepts far more often than it returns the company.

### Four rejections worth reading individually

- **`Ascend Performance Materials`** produced four candidates and lost all four — two to
  `energy company` (Q1341478), one to bare `organization`, one on name score 83.6 / 88. Q1341478
  is a *legitimate* company subtype, and it is not in the declared `P279_ONE_STEP` table, so it
  got no step up. **This is the measured cost of resolving the `P279` step from a table instead of
  a live query, and it was left in place** — adding the QID after seeing this row is exactly the
  tuning the brief rules out. It is recorded as an open item in §7 instead.
- **`American Coatings Association`** and **`3BC`** were refused on bare `organization`
  (Q43229). Q43229 is deliberately outside the whitelist: it is the class every candidate this
  lane ever sees belongs to, so admitting it would gate nothing. Cost: two records.
- **`Anresco Laboratories`** matched an item (Q124545355) with **no English label and no English
  alias**, so the name check scored 0.0. A consequence of the `language=en` restriction, and the
  right outcome — an item with no name in the language being compared cannot be verified against
  the record.
- **`Aprecia Pharmaceuticals LLC`** matched `APRECIA PHARMACEUTICALS LLC` at **100.0** and was
  then refused by constraint 6: the item's `P159` is *Blue Ash* (Ohio); the record says *East
  Windsor, NJ*. This is the single most debatable rejection in the batch. Aprecia has had sites in
  both places, so one of the two is stale and the lane cannot tell which. Refusing costs nothing —
  the record proceeded to the web lane exactly as it would have — while accepting would have
  attached a crowd-sourced identity to a record whose location contradicts it. Left as is.

---

## 5 · What changed in the shipped output, and what did not

**7 of 100 rows differ between run A and run C. Only 2 of them are the lane.**

That needs the noise floor to state honestly, which is why run B exists:

| Comparison | Rows that moved |
|---|---:|
| A vs B (**same config**, LLM variance only) | **7** |
| A vs C | 7 |
| B vs C | 8 |

The LLM tiers below the lane are not deterministic on this batch — `company_canonical` and Tier 3
produce different answers run to run — and the size of that noise is the same as the size of the
A-vs-C diff. Row 49 (`Aldrich APL`) resolves to **Allen Public Library** in run A and **Arlington
Public Library** in run B, with a different `ror_id` and domain each time; row 2 (`1910 Genetics`)
lands on Baylor Genetics in A and Myriad Genetics in B. Neither record ever reached the lane.

The lane's own contribution is isolable because its writes are uniquely identifiable:

| Row | Change | Attributable to the lane? |
|---|---|---|
| 12 | `operating_name` + `wikidata:2:crosswalk` provenance | **Yes** |
| 86 | `operating_name` + `wikidata:2:crosswalk` provenance | **Yes** |
| 16, 26, 49 | `name1_provenance` flips, legal-suffix punctuation, a different library | No — these move between A and B too |
| 30, 68 | `low-confidence-unchanged` appears; `Amylin Pharmaceuticals, Inc` → `, LLC` | No — the trace shows the lane returned `no_match` with **zero candidates and zero API calls** on both, so it wrote nothing to either record. The two baselines happen to agree here, which with n=2 is coincidence rather than evidence |

Every summary-level delta between A and C (`enriched` 38→39, `tier1_resolved` 44→43,
`unchanged_verified` 23→24, `verified` 31→30, `tier3_count`, `page_*`) is inside the A-vs-B noise
band and none of it is claimed for the lane.

**Latency is not reported.** The three runs took 592 s, 268 s and 486 s; with the lane replaying
from fixtures and contributing no network calls, that spread is LLM and SERP latency and says
nothing about the lane.

### The unchanged-verified feed did not fire

The witness path can make a retained Name 1 `unchanged-verified`, and on this batch it never got
the chance: all three matched records were already settled by something stronger — row 12's Name 1
was rewritten by the canonicaliser (so no unchanged state applies), rows 28 and 86 were already
`input:1:verified` from a page read and an ownership-guard-passing domain respectively. The code
path is unit-tested and unexercised in production. Reported, not claimed.

---

## 6 · Rate limiting — the one thing that did have to be fixed

The **first** live run of the lane, at concurrency 3 with the GLEIF client's retry schedule
(0.5 s, 1.0 s, 2 retries), took `HTTPStatusError:429` on **28 of 68** invocations. Wikidata
rate-limits anonymous callers far harder than GLEIF does.

Nothing broke: all 28 failed closed to `wikidata_unavailable`, no record was harmed, and the
batch completed. But 28 of 68 records getting no answer is not a measurement, so three changes
were made **to the transport only** — no constraint, threshold or matching rule was touched:

1. A 429 now backs off from **5 s**, doubling. A 429 is the API stating a rate, not a failure to
   recover from; retrying it in half a second is not a retry, it is the same request.
2. A server-supplied `Retry-After` wins outright, capped at 30 s so one header cannot stall a
   batch.
3. The lane now separates **operations** (one per search or entity fetch — the budget number)
   from **HTTP requests** (retries included — what a rate limiter sees). Conflating them made the
   first run look as though it were spending five calls on one record when it was spending two
   operations and three retries.

Plus `scripts/wikidata_warm_fixtures.py`, which records a workbook's fixtures serially at one
request every two seconds. 100 queries, 110 requests, **zero 429s**.

**Fixture size.** The unpruned recordings came to **4.3 MB** for 79 entities, against 589 KB for
the entire page-read fixture store — Wikidata entities carry every statement, qualifier, reference
and hash an item has accumulated. Fixtures now record only the nine properties the lane reads plus
English labels and aliases (`prune_entity`): **169 KB**, a 25× reduction, and a file a human can
read. The cost is stated in the code: adding a property means the recordings must be refreshed.

---

## 7 · Open items — recorded, not acted on

1. **`Q1341478` (energy company) is not in `P279_ONE_STEP`,** and cost `Ascend Performance
   Materials` two candidates. Adding it is a one-line change and is deliberately **not** made
   here: adding a QID because a specific row failed is tuning the gauntlet to the batch. If it is
   added, it should be as part of a considered pass over company subtypes, verified against live
   Wikidata like the existing twelve, and the batch re-measured afterwards.
2. **Row 12: `P856` agrees with a domain the ownership guard rejected, and the flag stands.**
   `abgent.com` was refused by the guard (`domain-unverified`), and Wikidata's official-website
   statement for the matched item is exactly `abgent.com`. The lane counts the agreement and does
   nothing else, because accepting the domain would be a fifth ownership condition — the same
   boundary the page corroborator declined to cross, and for the same reason. Whether a `P856`
   agreement *plus* a page read should together clear `domain-unverified` is a question for the
   ownership guard, not for this lane.
3. **The registry-pointer crosswalk has never run in production.** 0 of 3 matched items carried
   `P6782` or `P1278`. A batch containing universities, hospitals or LEI-holding public companies
   would exercise it; a batch of private US SMBs does not. The chemspeed batch is the wrong
   population to evaluate the crosswalk half of this lane on, and a second batch is the way to
   evaluate it — not a relaxed gauntlet on this one.
4. **`entity-superseded` has never fired in production**, for the same reason.
5. **The LLM tiers' run-to-run variance (7 rows / 100) is larger than this feature's effect.** It
   is out of scope here, but any future A/B on this pipeline needs a repeated baseline to be
   readable at all — which is the main methodological lesson of this report.

---

## 8 · Verdict

The lane is correct, cheap and inert where it should be inert:

- **Byte-identical when disabled** — asserted by test against a build with both entry points
  excised, and confirmed on the batch: all fourteen counters are zero in runs A and B.
- **Never wrote a name.** 3 matches, 0 writes to `name1_enriched`, 2 writes to `operating_name`.
- **Never failed a record.** 28 rate-limited invocations in the first run, 0 records harmed.
- **Two API operations per record**, one of them skipped entirely on the 82 % of queries that
  return nothing.

And its yield on this population is **3 records in 100, all of them witnesses**. That is worth
having — `operating_name` on two rows that had none, and one corroborated domain — and it is not
worth pretending is more than it is. The value the lane was built for, following a registry
pointer to a verified identity, is untested on this batch because the batch contains no
organisation Wikidata knows an identifier for.
