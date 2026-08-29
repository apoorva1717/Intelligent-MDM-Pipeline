# 28 — Stage 0 overflow misses a name ending in a dangling connector

Type: task
Status: open
Blocked by: —
Source: measured on the S2 sample, 2026-08-29

## The finding

Six of 100 S2 records carry a Name 1 that ends mid-phrase, with the rest of the name in Name 2:

```
'Exxonmobil Research &'        + 'Engineering Co Clinton Twp'
'Exxonmobil Research &'        + 'Engineering Co'
'Exxonmobil Research &'        + 'Engineering Co., Inc.'
'ExxonMobil Technology and'    + 'Engineering Company'
'Expeditors International of'  + 'Washington, Inc.'
```

A Name 1 ending in `&`, `and`, `of`, `the` or a hyphen is a **near-certain** overflow signal: no
organisation's name ends in a coordinating conjunction or a preposition. This is exactly UC 0's job
(`overflow_check`, Stage 0), and it is not firing on this shape.

## The cost is paid three times over

**1. The name is wrong.** `Exxonmobil Research &` is not an organisation.

**2. The domain resolves somewhere else.** Compare, from the same run:

| Name 1 | domain | provenance |
|---|---|---|
| `ExxonMobil Research & Engineering` *(whole)* | exxonmobil.com | **`verified+domain`** |
| `Exxonmobil Research &` *(truncated)* | exxonmobil.com | `web:...:low` |
| `Exxonmobil Research &` *(truncated)* | **nlrb.gov** | `web:...:provisional` |

The truncated form searches as a fragment and lands on a regulator's docket — ticket 27.

**3. It manufactures a Name 2 review flag.** The second half of the organisation's name sits in the
Name 2 slot, so the pipeline reports *"the department could not be canonicalised"* about it.
**8 of the 34 noise flags in ticket 26 are this defect.**

One fix, three symptoms.

## Questions

1. **Why does `overflow_check` not fire?** Read it before changing it — UC 0 exists and handles
   other split shapes. Establish whether the trailing-connector case was considered and rejected, or
   simply never seen.
2. **What is the exact predicate?** A trailing coordinating conjunction (`&`, `and`, `or`),
   preposition (`of`, `for`, `at`, `in`) or article (`the`), plus a non-empty Name 2. Decide whether
   a trailing hyphen or comma belongs — `Merck & Co.,` is a different shape.
3. **What is the join rule?** `'Exxonmobil Research &' + 'Engineering Co Clinton Twp'` must not
   become a name with a township in it. The join has to stop at the organisation, and Name 2 may
   carry a city (`Clinton Twp`) that belongs in the address. Stage 1 preprocessing already separates
   locality fragments; reuse it rather than writing a second rule.
4. **What happens to Name 2 after a join?** It is now empty, or holds the address fragment. Empty is
   correct and must not itself raise "Name 2 missing" (see the name-slot parity tests).
5. **Does this interact with batch consensus?** Six records here are variants of two organisations;
   a repaired name should let Stage 6 group them, which it cannot do today.

## Measurement required

Before/after on the 200: names repaired, domains changed (in both directions), Name 2 flags removed,
and — the one to watch — any record where the join produces a **worse** name than the truncation.

## Evidence

`logs/compare/s2_now.json`; `logs/compare/enriched_samples_200.xlsx`.
Detection probe: Name 1 matching `(&|\band\b|\bof\b|\bthe\b|-)\s*$` with a non-empty Name 2.
