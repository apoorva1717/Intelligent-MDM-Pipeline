# 19 — Query formulation vs registry absence, measured

Date: 2026-08-29. Every number below came out of executed code against the live
`api.ror.org`, `api.gleif.org` and `wikidata.org` APIs. No production code was
changed by this ticket.

**Verdict up front.** The hypothesis is **refuted on ticket 11's own corpus and
substantially refuted on the labelled corpus.** Over the 175 records that
currently leave with no registry identity, the best combination of *every*
query formulation tested recovers **12 records (7%)** as the organisation the
record actually names. Under the looser criterion ticket 15 used — a returned
organisation's registered domain equalling the record's resolved domain, with no
check that it is the same entity — the union is **52 (30%)**, but **40 of those
52 are the parent, a sibling site, or an unrelated organisation that shares the
same corporate domain.** On corpus A (`chemspeed_us_100`), where the coverage
ceiling was claimed, the union recovers **1 record of 74**.

**Ticket 11's coverage-ceiling claim survives.** It is, if anything,
under-stated: on corpus A, **0 of the 52 lost records that have a resolved
domain have any ROR organisation registered on that domain**. Query formulation
is not the top defect.

---

## How it was produced

| | corpus | source of the "lost" set | records | lost |
|---|---|---|---|---|
| **A** | `docs/thesis/chemspeed_us_100.xlsx` | fresh full-pipeline run, live ROR/GLEIF/SERP/LLM (`tmp/q19/runA.json`) | 100 | **74** |
| **S2** | `docs/results/demo_S2_large_corporate_100_v1 (1)_enriched.xlsx` | the file's own `ROR ID` / `LEI ID` columns | 100 | **51** |
| **S3** | `docs/results/demo_S3_government_labs_100_v1 (1)_enriched.xlsx` | same | 100 | **50** |

*Lost* = empty `ror_id` **and** empty `lei_id`. Total **175**.

Corpus A had to be re-run: ticket 11's artefact (`tmp/run100b.json`) was produced
while `SERPAPI_KEY` was shadowed by a placeholder, so `domain_from_serp` was 0
and **no lost record carried a resolved domain** — the domain-equality test was
inapplicable to it. With SerpAPI working the same 100 records now resolve 79
domains (55 on lost records), and registry identity moves **24/100 → 26/100**
(ROR 11, LEI 19). *Fixing the search lane moved registry coverage by two
records.* The comparison to ticket 11 therefore holds.

Scripts (durable): `.scratch/agentic-enrichment/scripts/q19_population.py`,
`q19_strategies.py`, `q19_wikidata.py`, `q19_report.py`, `q19_parent_test.py`,
`q19_strict_union.py`, `q19_supplement.py`. Raw artefacts under
`tmp/q19/` (`population.json`, `strategy_results.json`, `wikidata_results.json`,
`FINAL_*.txt`). 119 new ROR/GLEIF queries were cached under
`tests/fixtures/registry/` (gitignored); Wikidata wrote 122 new fixtures.

### The control, stated exactly

The control is the **direct-drive control** — ticket 11's harness C/D:
`preprocess_record` → `strip_address_fragments` → the real `call_ror` /
`call_lei`, with the record's own city/state/country, Stage 0 (overflow
recombination) **not** run, exactly as `drive_tier1.py` documents. Every
strategy changes **only the query string** and keeps `call_ror` / `call_lei`,
their gates and their thresholds untouched.

The control is not identical to the full pipeline: on this population it returns
9 matches the pipeline did not keep, 8 of them domain-wrong (they are the
Stage-0 overflow records — `'National Technology and'`, `'Novartis Institute
for'` — where ROR happily answers a truncated name with a different
organisation at score 1.000). Every delta below is therefore measured
**record-by-record against the control**, not as a raw recovery count.

### Adjudication

Two verdicts per hit, both computed.

**Lenient — ticket 15's domain-equality test, as briefed.** A ROR hit is
*correct* only when the returned organisation's own registered domain
(`domains[]` / `links[]`, reduced to the registrable domain) equals the record's
resolved registrable domain. Different → *wrong*. GLEIF publishes no website, so
its stand-in is one-directional brand containment: the brand label of the
record's domain must appear inside the returned legal name. (The reverse
direction was tried and dropped: it accepted `ALLCHEMY INC` / `allchemy.net` →
`ALLCHEM, LLC` at GLEIF fuzzy 93.3, a different company.)

**Strict — is it the entity the record names, or a broader one?** The lenient
test *plus* a token-direction test on `registry_match.distinctive_tokens`, with
ROR's bracketed keyspace qualifier stripped and `US` / `U.S.` / `United States`
collapsed: the names must be token-equal, or the record's tokens must be a
strict subset of ROR's (a truncated SAP name). A parent, a sibling site or an
unrelated organisation registered on the same corporate domain fails.

**Why the second test is not optional.** Domain equality is *circular* for the
domain-first strategy: that strategy retrieves the organisation whose registered
domain equals the record's, so domain equality holds by construction. It is also
blind to the dominant failure mode here — a medical center, refinery, division
or national lab shares its parent's domain, so the parent is what comes back.

**Records with no resolved domain cannot be adjudicated at all.** 31 of the 175
(A 22, S2 2, S3 7) have no domain, or only an aggregator domain (`linkedin.com`).
They are counted in **neither** column. Only 2 of them have any strategy that
returns something: `Intelligent Epitaxy Technology Inc` → `IntelliEPI` (GLEIF,
after suffix strip — the one genuine wrong-rejection ticket 11 already found)
and `ALZA Corp` → `Alza` (Wikidata → LEI).

---

## The per-strategy table

Over all 175 lost records. "fires" = the strategy produces a registry match at
all; the three verdict columns partition it. "mixed" = ROR and GLEIF disagree,
so both a right and a wrong identifier would be written.

| strategy | queries differing from control | fires | correct | **WRONG** | mixed | ROR hits | GLEIF hits |
|---|---:|---:|---:|---:|---:|---:|---:|
| `control` | 0 | 9 | 1 | **8** | 0 | 9 | 0 |
| `raw` (Name 1 verbatim) | 22 | 9 | 1 | **8** | 0 | 9 | 0 |
| `expand_query` | **0** | 9 | 1 | **8** | 0 | 9 | 0 |
| `nosuffix` | 47 | 15 | 4 | **9** | 1 | 12 | 4 |
| `noloc` | 15 | 9 | 1 | **8** | 0 | 9 | 0 |
| `name1_name2` | 57 | 1 | 1 | 0 | 0 | 1 | 0 |
| `slashfix` | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| `wikidata` crosswalk | 175 | 1 | 0 | 0 | 0 | 0 | 1 |
| `domain_first` | 147 | 50 | 50 | 0 | 0 | 50 | 0 |

### Delta against the control, per record

| strategy | new-correct | **new-WRONG** | fixes a control FP | net |
|---|---:|---:|---:|---:|
| `domain_first` | **49** | 0 | 7 | **+49** |
| `nosuffix` | 3 | **2** | 0 | +1 |
| `name1_name2` | 1 | 0 | 0 | +1 |
| `wikidata` | 0 | 0 | 0 | 0 |
| `slashfix` | 0 | 0 | 0 | 0 |
| `raw` | 0 | 0 | 0 | 0 |
| `noloc` | 0 | 0 | 0 | 0 |
| `expand_query` | 0 | 0 | 0 | 0 |

`domain_first`'s 0 in the wrong column is the circularity, not a quality
signal — see below.

### The same table under the strict adjudication

| strategy | lenient (domain equality only) | own entity **or its parent** | own entity **only** |
|---|---:|---:|---:|
| `domain_first` | **49** | 22 | **8** |
| `nosuffix` | 3 | 3 | 3 |
| `name1_name2` | 1 | 1 | 1 |
| everything else | 0 | 0 | 0 |

---

## The union — the decisive number

| reading | records recovered | % of the 175 lost |
|---|---:|---:|
| **lenient** (ticket 15's criterion) | **52** | 30% |
| **own entity or its parent company** (a policy call, not a retrieval one) | **26** | 15% |
| **strict** (the record's own entity) | **12** | **7%** |

Greedy cumulative build under the lenient reading: `domain_first` (+49), then
`nosuffix` (+3, at the cost of 2 new false positives), then nothing else has a
positive marginal net. **Two strategies exhaust the space; the other six
contribute zero.**

By corpus:

| corpus | lost | lenient | own+parent | **own entity only** |
|---|---:|---:|---:|---:|
| **A** (chemspeed, ticket 11's corpus) | 74 | 1 | 1 | **1** |
| S2 (large corporate) | 51 | 20 | 17 | **4** |
| S3 (government labs) | 50 | 31 | 8 | **7** |

The twelve records the strict union recovers, in full:

```
A   'Advanced Energy Materials LLC'        advancedenergymat.com  nosuffix     -> Advanced Energy Materials
S2  'Amazon'                               amazon.com             domain_first -> Amazon (United States)
S2  'US Department of Defense'             defense.gov            domain_first -> United States Department of Defense
S2  'Sabic Innovative Plastics Inc'        sabic.com              nosuffix     -> SABIC INNOVATIVE PLASTICS US LLC
S2  'Microsemi Corp'                       microsemi.com          nosuffix     -> Microsemi
S3  'US Department of Veterans Affairs' x5 va.gov                 domain_first -> United States Department of Veterans Affairs
S3  'Naval Air Warfare Center'             navy.mil               name1_name2  -> Naval Air Warfare Center Weapons Division
S3  'US Air Force Institute of Technology' afit.edu               domain_first -> U.S. Air Force Institute of Technology
```

Five of the twelve are one duplicated record (`US Department of Veterans
Affairs` ×5). **The distinct organisations recovered across 300 records number
eight.**

---

## Why the lenient number is 52 and the strict number is 12

`domain_first` returns 50 domain-equal ROR organisations. Re-adjudicated on
evidence the retrieval did not use:

| token direction of ROR's name vs the record's | n |
|---|---:|
| **equal** (same entity) | **8** |
| `ror_broader` — ROR names the parent company | 14 |
| `overlapping` | 13 |
| `disjoint` — no shared distinctive token at all | 15 |

And the independent name check: **the ROR organisation's name agrees with the
record's name under the pipeline's own comparator (`names_agree` at 88) in 3 of
50 cases.** In the other 47 the domain is the *only* evidence.

The `disjoint` bucket, in full, is what the criterion is actually counting:

```
Vamc Miami Visn 8 / Vamc Redding Visn 21 / Vamc Temple Visn 17 /
Vamc Martinez Visn 21 / Vamc West la Visn 22 / VA MC West la Visn 22 /
Vamc Iron Mountain Visn12 / VA Medical Center x2 / JAH VA Hospital
     va.gov     -> United States Department of Veterans Affairs
SLAC National Accelerator      stanford.edu -> Stanford University
Naval Air Warfare Center       navy.mil     -> United States Navy
US Department of Energy        energy.gov   -> Naval Nuclear Laboratory
```

Twelve VA medical centres in different states all resolve to `va.gov` and would
all be given the ROR id of the federal department. `SLAC National Accelerator`
would be given Stanford's — even though SLAC has its own ROR record. This is the
same parent-vs-child substitution ticket 11 recorded at ROR gate 3 for the VISN
networks, arriving by a different route.

The `ror_broader` bucket (14) is the corporate-parent case — `ExxonMobil
Refinery` → `ExxonMobil`, `McKesson Medical-Surgical Inc` → `McKesson`,
`Novartis Institute for` → `Novartis`, `Shell Global Solutions Us Inc` → `Shell`.
The pipeline's own comparator *accepts* these pairings by containment, so they
would be written. Whether a site should carry its parent's `ror_id` is a policy
question — but it is not evidence that "the organisation is in the registry".

### And the domains themselves are mostly unverified

Provenance of the resolved domain that `domain_first` keys on, over its 50
recoveries: **`:low` 31, `:provisional` 17, `:verified` 2.** A `:low` domain is
one `resolve_domain`'s ownership guard declined to attribute. Four of the fifty
also cross a border — `Shell Global Solutions Us Inc` (US) → `Shell
(Netherlands)`, `Novartis Institute for` (US) → `Novartis (Switzerland)` ×2,
`Bayer Pharmaceuticals` (US) → `Bayer (Germany)`.

So the strategy that produces the entire lenient headline runs
`unverified domain → registry identity`, in the opposite direction to
`utils/domain_resolver.py`'s ownership guard, which exists precisely to stop a
`:low` domain from authorising anything.

---

## Corpus A — the ceiling, tested directly

Ticket 11's ceiling claim was about `chemspeed_us_100`. The direct test:

```
lost                                            74
   with a resolved, non-aggregator domain       52
   a ROR organisation registers that domain      0     <-- zero
recovered correctly by ANY strategy              1
```

**Zero.** Not one of the 52 lost chemspeed records with a resolved domain has an
ROR organisation registered on that domain. The single recovery,
`Advanced Energy Materials LLC` → `Advanced Energy Materials`, comes from
stripping the legal suffix.

Corpus A is small private US chemical and laboratory suppliers. ROR indexes
research organisations; GLEIF indexes LEI holders. Ticket 11's conclusion — that
these organisations are *not in the registries* — is confirmed by a method that
does not depend on ROR's name retrieval at all.

---

## Strategy-by-strategy findings

### 1. `raw` — Name 1 verbatim. 22 records differ. **0 new correct.**
Includes `1910 Genetics` (preprocessing strips the leading numeral, leaving
`Genetics`) and `3M (Detroit)`. Sending the raw string recovers nothing and
changes no verdict.

### 2. `expand_query` — `expand_abbreviations` on the query. **0 records differ.**
The structural gap is real and was verified by reading `enrichment/tier1_ror.py`:
`expand_abbreviations` is computed at lines 1226/1230/1252/1331 and flows only
into `rescore_names` and `_item_score` — it scores candidates ROR already
returned. The query strings are `_expand_state_abbrevs(name)` (line 940, the
affiliation string; line 1266, the query endpoint) and
`_expand_institution_acronyms(name)` (line 1249). The comment at line 1325
("Expand abbreviations in the query first") describes an intent the code does
not implement — `expanded_query` is computed at 1331, after the fetch at 1279.

**But the addressable population is zero.** Applying `expand_abbreviations` to
the query string it would change **2 names in 300**, both `Jet Propulsion Lab` →
`Jet Propulsion Laboratory`, and **both of those records already carry a
registry identity**. Across the 175 lost records the map fires on none. The
`Mass Inst of Tech` signature does not occur in either corpus.

This closes the question the coordinator raised: the retrieval path *is* blind
to the general abbreviation map, and on these two corpora that blindness costs
**zero records**. The fix is cheap and correct as hygiene; it is not a recovery
lever, and no measurement here supports prioritising it.

### 3. `nosuffix` — legal-form suffix stripped. 47 differ. **+3 correct, +2 wrong.**
The only name-side strategy with any yield. The wins:
`Advanced Energy Materials LLC` → ROR `Advanced Energy Materials`;
`Microsemi Corp` → ROR `Microsemi`; `Sabic Innovative Plastics Inc` → GLEIF exact
`SABIC INNOVATIVE PLASTICS US LLC`. The costs: `ACT Solutions Corp` → GLEIF fuzzy
`AXT SOLUTIONS INC.` (92.3) and `ALLCHEMY INC` → GLEIF fuzzy `ALLCHEM, LLC`
(93.3) — both different companies, both admitted because dropping the suffix
removes the discriminating token. **3 right, 2 wrong is not a shippable ratio.**

### 4. `noloc` — location tokens removed. 15 differ. **0 new correct, 0 new wrong.**
Reconfirms ticket 11's affiliation-context result from the other side: removing
location tokens from the *name* changes nothing on this population.

### 5. `name1_name2` — Stage-0 recombination. 57 differ. **+1 correct, 0 wrong.**
`Naval Air Warfare Center` + `Weapons Div` → ROR `Naval Air Warfare Center
Weapons Division`. Clean, no false positive, and it is the *right* fix for the
overflow records — but only one of the 57 reaches a registry. The other
overflow-truncated names (`National Technology and` + `Engineering Solutions of
Sandia`, `Novartis Institute for` + `BioMedical Research Inc`) still miss.

### 6. `slashfix` — `/` replaced. 3 differ. **0 recoveries.**
The ROR HTTP 500 on `/` reproduced again this session (`20/15 Visioneers`).
Replacing the slash makes the query legal and ROR still returns nothing usable.
The bug is real; fixing it recovers nothing on these corpora.

### 7. `wikidata` crosswalk. 175 queried live. **1 pointer, 0 recoveries.**
`no_match` 169, `matched` 4, `ambiguous` 2. Exactly one crosswalk pointer:
`ALZA Corp` → `Alza` → LEI `5493000D0616BBOFAM23`. That record has no resolved
domain, so it lands in the unadjudicable bucket and is counted in neither column.

*Environment note:* the first two Wikidata passes returned HTTP 403 on every
live call — Wikimedia enforces its robot policy on the User-Agent and the
lane's product-name-only string is refused. That was found independently here
and fixed in `enrichment/wikidata.py` by the coordinator mid-run; the numbers
above are from a re-run **with the production fix in place and no probe-local
override**, and reproduce the probe-local run exactly.

### 8. `domain_first` — ROR `query.advanced=domains:"<resolved domain>"`.
The only high-yield strategy, and the one the adjudication cannot vouch for.
Firing rate by corpus, over lost records that have a domain:

```
A    0 / 52   ( 0%)
S2  18 / 49   (37%)
S3  32 / 43   (74%)
```

50 hits, all domain-equal by construction; 8 are the record's own organisation.

### 9. GLEIF exact vs `fuzzycompletions`.
Across every strategy, GLEIF produced 4 hits on lost records — **1 exact, 3
fuzzy**. Two of the three fuzzy hits are the false positives named above
(`AXT SOLUTIONS INC.`, `ALLCHEM, LLC`); the exact hit (`SABIC INNOVATIVE
PLASTICS US LLC`) is right. This is consistent with ticket 11's finding that the
GLEIF name threshold is the one gate with a real wrong-rejection population, and
adds that the fuzzy phase is also where the wrong-*acceptances* come from.

---

## Two incidental defects found while measuring

* **GLEIF HTTP 400 on a name ending in a comma.** `filter[entity.legalName]=National
  Technology and Engineering Solutions of Sandia,` returns
  `{"status":"400","title":"Invalid Query Parameter","detail":"Value must not be
  empty."}`. Reproduced repeatedly this session. Same class as the ROR HTTP 500
  on `/`: a punctuation character in a SAP name turns a lookup into an API error
  that is indistinguishable from a miss.
* **ROR HTTP 500 on `/` reconfirmed** — `20/15 Visioneers`, live, this session.

---

## What this says about the map

The destination is "find out where enrichment actually loses records". This
ticket tested the strongest remaining alternative to "the registries do not
contain these organisations", and the alternative loses:

* On corpus A, **zero** of the lost records with a resolved domain have an ROR
  organisation on that domain. The ceiling is not an artefact of retrieval.
* On corpus D, the organisation on the record's domain usually **is** in ROR —
  but 42 of 50 times it is the *parent*, and the record names a site, a medical
  center or a division. That is an **entity-granularity** problem, not a query
  problem: ROR's unit of registration is coarser than SAP's customer record.
* Across the whole 175, every name-side reformulation combined moves **4
  records**, and two of the four came at the price of two new false positives.

Ticket 15's ">=47 of the 77 `unknown` records are in ROR" should be read as
"≥47 have an organisation in ROR on their domain" — which is true, and which is
mostly the parent. Under the entity test the figure is 8 distinct organisations
across 300 records.

The parent-vs-child question is now the second measured population pointing at
the same thing (ticket 11's ROR gate 3 was the first: 6 of 9 rejections were VA
VISN networks offered for specific medical centres). That is a policy decision
about what `ror_id` on a customer record is *for* — and it is a better-defined,
higher-yield question than any query rewrite this ticket measured.
