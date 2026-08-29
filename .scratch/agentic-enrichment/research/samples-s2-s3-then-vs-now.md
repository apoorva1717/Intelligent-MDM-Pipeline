# Your S2 / S3 sample workbooks — then vs now, like for like

**then** = the values shipped in `docs/results/demo_S{2,3}_*_enriched.xlsx`
**now** = the same 200 records through `enrichment-spike` (`efc4db3`), 2026-08-29
Ground truth = the `record_type_hint` column those workbooks carry.
`logs/compare/s2_now.xlsx`, `s3_now.xlsx` (+ `.json`). 566s and 563s respectively.

## S2 — large corporate, 100 records

| metric | then | now | |
|---|---:|---:|---|
| **`record_type` exact vs hint** | **43** | **65** | **+22** |
| of which decided but **wrong** | 18 | 16 | −2 |
| `record_type` decided at all | 61 | 81 | +20 |
| registry identity (ROR or LEI) | 49 | 50 | +1 |
| domain | 98 | 96 | −2 |
| name 2 populated | 57 | 57 | 0 |
| flagged for review | 52 | 47 | −5 |
| flag codes | 53 | 51 | −2 |

**+22 is ticket 17 landing exactly as projected (+21).** And the two records that moved out of
"decided but wrong" are the corporate R&D arms ticket 15 finding B named.

## S3 — government labs, 100 records

| metric | then | now | |
|---|---:|---:|---|
| `record_type` exact vs hint | 0 | 7 | +7 |
| of which decided but **wrong** | 62 | 61 | −1 |
| registry identity | 50 | 50 | 0 |
| domain | 93 | 89 | −4 |
| **name 2 `ror:verified`** | **2** | **11** | **+9** |
| name 2 `llm:provisional` | 21 | 7 | −14 |
| flag codes | 48 | **28** | **−20** |
| `unverified-inference` | 10 | 3 | −7 |
| `registry-location-mismatch` | 5 | 0 | −5 |

**Name 2 is the real S3 story.** Registry-verified departments went 2 → 11 while unverified LLM
guesses fell 21 → 7. That is the grounded lane on healthy search evidence, with ticket 24's guard
refusing the drifted proposals. Not a volume change — a **provenance** change: the same number of
Name 2 values, far better sourced.

## The domain "regression" is 7 records and mostly correct

| record | lost | old provenance |
|---|---|---|
| Vamc Miami Visn 8 | va.gov | `web:va.gov:low` |
| Vamc Temple Visn 17 | va.gov | `web:va.gov:low` |
| Vamc Iron Mountain Visn12 | va.gov | `web:va.gov:low` |
| Vamc Martinez Visn 21 | va.gov | `web:va.gov:low` |
| Vamc West la Visn 22 | va.gov | `web:va.gov:low` |
| CVG Ferrominera Orinocco CA | ferrominera.com | `web:ferrominera.com:low` |
| **Spansion LLC** | **infineon.com** | **`web:infineon.com:verified+domain`** |

Five of the seven are `va.gov` assigned to five *different* VA medical centres in five different
states — the same parent-vs-child confusion as **ticket 23**, and all were `:low`. Dropping them is
defensible. `Spansion LLC` losing a **`verified+domain`** value is the one that is not, and is worth
its own look (Spansion was acquired by Cypress, then Infineon, so the old value was a successor
domain).

One gained: `DOH Bureau of Public Health Labs` -> `floridahealth.gov`.

---

# Where else to improve — in order of size, all evidenced

## 1. `government` is not a producible `record_type` — worth ~73 of 100 on S3

S3 scores **7/100** exact. 61 records are "decided but wrong", and nearly every one says
`research_institution` where the label says `government`. `classifier.py:27-28` folds ROR's
*government* org type into `research_institution`; the value cannot be emitted.

**This is the single largest improvement available anywhere on the board, and it is a product
decision, not a code one** — ticket 15 finding A, now the live half of ticket 23. Nothing I can
measure decides it.

## 2. A silently wrong domain: `nlrb.gov` on ExxonMobil

```
name1='Exxonmobil Research &'  name2='Engineering Co'
   domain='nlrb.gov'  prov='web:nlrb.gov:provisional'  flags=[]   <-- no flag at all
```

Two records. The National Labor Relations Board's domain, at **`provisional`** confidence — not
`low` — so it does not trip `domain-unverified`, and one of the two ships with **no flag of any
kind**. Almost certainly a SERP hit on an NLRB case *involving* ExxonMobil.

This is the worst class of error the pipeline can make: wrong, and silent. It is not covered by any
open ticket. **New ticket needed.**

## 3. Stage 0 overflow misses a dangling connector — 6 of 100 on S2

```
'Exxonmobil Research &'      + 'Engineering Co Clinton Twp'
'Exxonmobil Research &'      + 'Engineering Co., Inc.'
'ExxonMobil Technology and'  + 'Engineering Company'
'Expeditors International of'+ 'Washington, Inc.'
```

Name 1 ends in `&`, `and`, `of` — a name split mid-phrase across two SAP slots, which is exactly
UC 0's job. The proof that repairing it pays twice: the one record where the name IS whole,
`ExxonMobil Research & Engineering`, resolves its domain at **`verified+domain`**, while its
truncated siblings sit at `low` or land on `nlrb.gov`.

A trailing coordinating conjunction or preposition is a near-certain overflow signal and is cheap to
detect. **New ticket needed.**

## 4. The keyword heuristic still misfires on corporate R&D arms

`ExxonMobil Research & Engineering` -> `research_institution`. Ticket 17 did not fix this: there is
no legal suffix on the name, so `legal_form` never fires and `_from_keyword` still answers. Ticket 15
finding B looked at this and declined to write a narrower predicate, because the harmful and helpful
names are the same shape. Worth revisiting now that a second, higher-precision source exists — the
question is whether "Research" *following* a known company name should be treated differently.

## 5. Name 2 is still 43% untouched input on S3

`input:low` = 43, `(empty)` = 39. The grounded lane now resolves 11 to `ror:verified`, but ticket
14's finding stands: 74% of unresolved Name 2 values are admin desks, phrases naming nothing, or
Name-1 overflow (see 3 above). **Ticket 25** (`site:` term) is the next lever, and 19 of the 21
addressable records have a domain to scope by.

## 6. Fifty S2 records still carry no registry identity and no verified domain

Half the corporate set. Ticket 19 established this is registry *absence*, not retrieval failure, for
the chemspeed population — but that was measured before the search environment was repaired, and it
is worth re-testing now that SERP actually answers.
