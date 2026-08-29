# 19 — Are the organisations absent from the registries, or are we asking with the wrong name?

Type: grilling
Status: answered — hypothesis refuted; promotes ticket 23
Blocked by: —

## The hypothesis (user's, 2026-08-29)

The organisations **are** in ROR/GLEIF. The pipeline fails to find them because it queries with the
wrong string, not because the registry lacks them.

## Why this is live, and why ticket 11 did not settle it

Ticket 11 concluded "~24-25% is a coverage ceiling on this corpus". The supporting argument was:
75/100 records got a below-threshold ROR candidate that was a visibly different company, and since
`_score_org` step 1 exact-matches any ROR name variant, a held organisation would have scored 1.0.

**That argument assumes ROR's retrieval surfaced the right candidate.** If the query is malformed,
ROR returns wrong candidates, they score low, and the record is recorded as "not in the registry"
when the truth is "not retrieved". **Retrieval failure and genuine absence are indistinguishable in
that measurement.** The ceiling is an upper bound on what *one query formulation* reaches, not on
what the registries hold.

Ticket 11 tested exactly one variant — dropping location context — which made things worse (6 of 7
new hits wrong). One variant is not the space.

**Direct counter-evidence already exists.** Ticket 15 finding C searched ROR by name and counted a
hit only where a returned org's *registered domain* equalled the record's resolved domain:
**>=47 of the 77 `unknown` records are in ROR**, all 77 having empty `ror_id` *and* empty `lei_id`.

Two more known query-side losses (ticket 18): `1910 Genetics` reaches ROR as `Genetics` because
preprocessing strips the leading numeral; ROR returns HTTP 500 on names containing `/`.

## The question

For records that currently leave with no registry identity, **what fraction are reachable in
ROR/GLEIF under some other query formulation** — and which formulation?

Candidate strategies to measure separately and cumulatively:

1. Raw `Name1` verbatim (no preprocessing)
2. Preprocessed name as the pipeline sends it today (the control)
3. `expand_abbreviations` / `_expand_state_abbrevs` variants
4. Legal-suffix stripped (`Inc`, `LLC`, `GmbH`, …) and, separately, suffix retained
5. Location tokens removed / retained (ticket 11 measured this one — reconfirm, do not assume)
6. **Domain-first**: resolved domain -> registry org whose registered link matches. This is the
   strategy that produced the >=47 figure and is the strongest available signal.
7. Wikidata crosswalk -> `ror_id` / `lei_id` pointer
8. GLEIF exact legal-name lookup vs `fuzzycompletions`
9. `Name1` + `Name2` recombined (for records where Stage 0 split an overflowed name)

## What would settle it

A table: per strategy, how many currently-lost records become resolvable, **and how many of those
are correct** — recovery that costs precision is not recovery. Adjudicate with the domain-equality
test (ticket 15's method), not by eye.

Then the decisive number: **the union.** If the best combination recovers a large share, the
"coverage ceiling" framing is wrong and query formulation is the top defect in the pipeline. If it
recovers little, the ceiling stands and ticket 12's target shrinks accordingly.

## Note

This ticket can invalidate the headline finding of ticket 11 and the framing in `map.md`. That is
the point. Record whichever way it lands.

## Findings

Full write-up: [`research/19-query-formulation.md`](../research/19-query-formulation.md).
Every number below came out of executed code against the live ROR, GLEIF and
Wikidata APIs. No production code was changed by this ticket.

**Population.** 175 records leaving with empty `ror_id` *and* empty `lei_id`:
74 of 100 on `chemspeed_us_100` (re-run live, because ticket 11's artefact was
produced while SerpAPI was dead and therefore carried **no resolved domain on
any lost record**), 51 of 100 on S2, 50 of 100 on S3. With SerpAPI working,
corpus A registry identity moves 24/100 -> **26/100** — fixing the search lane
buys two records.

**Adjudication.** Ticket 15's domain-equality test, plus a second, independent
test the briefed method lacks: is the returned organisation *the entity the
record names*, or a broader one? (Token-direction on
`registry_match.distinctive_tokens`, ROR's bracketed qualifier stripped,
`US`/`United States` collapsed.) The second test is not optional, because domain
equality is **circular for the domain-first strategy** — that strategy retrieves
the org whose registered domain equals the record's. 31 records have no
resolved domain and are counted in **neither** column.

### Per strategy, delta against the direct-drive control

| strategy | queries differing | new-correct | **new-WRONG** |
|---|---:|---:|---:|
| `domain_first` (ROR `query.advanced=domains:"…"`) | 147 | **49** | 0 ¹ |
| `nosuffix` (legal suffix stripped) | 47 | 3 | **2** |
| `name1_name2` (Stage-0 recombination) | 57 | 1 | 0 |
| `wikidata` crosswalk | 175 | 0 | 0 |
| `slashfix` | 3 | 0 | 0 |
| `raw` (Name 1 verbatim) | 22 | 0 | 0 |
| `noloc` (location tokens removed) | 15 | 0 | 0 |
| `expand_abbreviations` on the query | **0** | 0 | 0 |

¹ zero by construction — see the entity test below.

### The union

| reading | recovered | % of 175 |
|---|---:|---:|
| lenient — domain equality only (ticket 15's criterion) | **52** | 30% |
| own entity **or its parent company** (a policy call) | **26** | 15% |
| strict — **the record's own entity** | **12** | **7%** |

By corpus (lost / lenient / strict): **A 74 / 1 / 1**; S2 51 / 20 / 4;
S3 50 / 31 / 7. Five of the twelve strict recoveries are one duplicated record;
**eight distinct organisations across 300 records.**

Greedy build: `domain_first` (+49), `nosuffix` (+3 at the cost of 2 FPs), then
**nothing else has a positive marginal net**. Six of eight strategies contribute
exactly zero.

### Why 52 collapses to 12

Of `domain_first`'s 50 domain-equal ROR hits: **8 are the same entity**, 14 are
the corporate parent (`ExxonMobil Refinery` -> `ExxonMobil`), 13 overlap, and 15
share **no distinctive token at all** — twelve VA medical centres in different
states all resolving to `va.gov` -> `United States Department of Veterans
Affairs`, `SLAC National Accelerator` -> `Stanford University` (SLAC has its own
ROR record), `US Department of Energy` -> `Naval Nuclear Laboratory`. The ROR
name agrees with the record's name under the pipeline's own comparator
(`names_agree` @88) in **3 of 50**. And the domains it keys on are
**`:low` 31 / `:provisional` 17 / `:verified` 2** — the strategy runs
`unverified domain -> registry identity`, against the direction of
`domain_resolver`'s ownership guard.

### Corpus A — the ceiling, tested without relying on ROR's name retrieval

```
lost .......................................... 74
   with a resolved, non-aggregator domain ..... 52
   a ROR organisation registers that domain ...  0
recovered correctly by ANY strategy ...........  1   (Advanced Energy Materials LLC)
```

### The `expand_abbreviations` lead, settled

The structural gap is real: `expand_abbreviations` reaches only `rescore_names`
/ `_item_score`, never the query (`tier1_ror.py` 1226/1230/1252/1331 vs the
query strings at 940/1249/1266); the comment at line 1325 describes an intent
the code does not implement. **Addressable population: 2 names in 300, both
`Jet Propulsion Lab`, both already carrying a registry identity — 0 of the 175
lost records.** Worth fixing as hygiene; it is not a recovery lever.

### Incidental

* **GLEIF HTTP 400** on a legal-name filter ending in a comma
  (`…Solutions of Sandia,`) — same class as the ROR HTTP 500 on `/`
  (reconfirmed live this session): punctuation turns a lookup into an API error
  indistinguishable from a miss.
* The Wikidata lane was returning **HTTP 403 on every live call** (Wikimedia
  enforces its robot policy on the User-Agent). Found here independently, fixed
  in production mid-run by the coordinator; the numbers above are from a re-run
  with the shipped fix and no probe-local override.

## Decision

**The hypothesis is refuted. Ticket 11's coverage-ceiling claim survives.**

On `chemspeed_us_100` — the corpus the ceiling was claimed for — the best
combination of every formulation tested recovers **1 record of 74**, and **0 of
the 52 lost records that have a resolved domain have any ROR organisation on
that domain**. That is measured without depending on ROR's name retrieval at
all, so it is not the "retrieval failure is indistinguishable from absence"
confound the ticket was opened on. The ceiling is real and this ticket
strengthens it.

**Query formulation is not the top defect.** Every name-side reformulation
combined — raw, abbreviation-expanded, suffix-stripped, location-stripped,
slash-repaired, Name1+Name2 recombined, Wikidata-crosswalked — moves **4 of 175
records**, and two of the four cost two new false positives. The single
highest-yield strategy, `domain_first`, is not a query formulation at all: it is
a different retrieval key, its apparent precision of 1.000 is an artefact of the
adjudication criterion, and 42 of its 50 hits name a different legal entity from
the record.

**Ticket 15's finding C should be restated.** ">=47 of the 77 `unknown` records
are in ROR" is true as ">=47 have *an organisation* in ROR registered on their
domain". Under the entity test that is **8 distinct organisations across 300
records**. The `>=47` figure is a lower bound on *domain presence*, not on
*entity presence*, and it should not be carried forward as evidence that the
Tier 1 gate is rejecting matches that exist.

**What this promotes instead.** Two populations now point at the same question
from different directions: ticket 11's ROR gate 3 (6 of 9 rejections were VA
VISN networks offered for specific medical centres) and this ticket's 42
parent/sibling substitutions. **What is `ror_id` on a SAP customer record for —
the legal entity, the site, or the parent?** ROR's unit of registration is
coarser than the customer record, and no retrieval change fixes that. That is a
better-defined and higher-yield question than any query rewrite measured here.

**Cheap, defensible changes this measurement does support**, none of them a
recovery lever:

1. Send `expand_abbreviations` output as the ROR query, or delete the misleading
   comment at `tier1_ror.py:1325`. Zero measured effect on these corpora.
2. Strip a trailing comma before the GLEIF legal-name filter, and a `/` before
   the ROR query. Both currently produce API errors indistinguishable from
   misses; neither recovers a record here, but both hide real failures.
3. Recombine Stage-0-split names before Tier 1 (`name1_name2`): +1 correct,
   0 wrong, on the population where it applies.

**Not supported:** stripping the legal suffix as a retry (3 right / 2 wrong),
and domain-first as a registry-identity source while the domains it would key on
are 96% `:low` or `:provisional`.
