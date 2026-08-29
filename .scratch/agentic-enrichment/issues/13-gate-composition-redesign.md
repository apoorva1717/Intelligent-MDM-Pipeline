# 13 — Should the registry gates be able to rescue, not only reject?

Type: grilling
Status: open — ROR limb killed by 11; re-scoped to GLEIF
Blocked by: 11

## Question

The ROR path is three conjunctive gates — ROR's own score, a local whole-string rapidfuzz rescore,
and a country check — and **every one can only subtract**. There is no path by which local evidence
rescues a candidate. Decide whether that composition is right, and if not, what replaces it.

1. **Is a whole-string fuzzy ratio the right discriminator for the local rescore?** Its stated
   purpose is catching "ASL Analytical" matched to "EMSL Analytical" — shared *generic* token,
   different *distinctive* token. But the same check rejects "MIT" -> "Massachusetts Institute of
   Technology", where ROR is right and the ratio is near zero. The discriminating signal is
   *which* token agrees, not how much of the string does. `registry_match.is_collision_prone`
   already reasons in these terms.
2. **Should ROR-high + local-low + location-agrees survive?** That is the abbreviation signature.
   Today it cannot, structurally.
3. **Should two different scales share one threshold?** ROR's affiliation score and a rapidfuzz
   ratio measure different things; both being `0.8` is a coincidence of naming, not a calibration.
4. **Same questions for the GLEIF path** — does `fuzzycompletions` get second-guessed the same way?
5. **What does the change cost in precision?** Every loosening risks the wrong-entity acceptances
   Fix C and Fix D were built to stop. The answer must say how the change is measured, not just
   what it is.

## Answer must include

The **measured** effect of the redesign on the eval (03) and on the funnel (11) — ticket 12 is
blocked on knowing whether this alone recovers the losses.

## Prior analysis — the motivating hypothesis was TESTED AND LARGELY REFUTED (2026-08-29)

This ticket was created on the claim that the local rapidfuzz rescore discards the abbreviation
cases ROR's affiliation matcher gets right ("MIT" scoring near zero locally). **That claim is
wrong.** Tested directly against the real `_score_org`, mirroring the production `rescore_names`
construction (`tier1_ror.py:925` + `:1177-1180` + the `max()` at `:1112`):

| query | org | score | verdict |
|---|---|---|---|
| `MIT` | MIT | 1.000 | PASS |
| `Mass Inst of Tech` | MIT | 1.000 | PASS |
| `Massachusetts Inst of Tech` | MIT | 1.000 | PASS |
| `Univ of Florida` | U. Florida | 1.000 | PASS |
| `U of Florida` | U. Florida | 1.000 | PASS |
| `Brigham & Womens Hosp` | BWH | 0.816 | PASS |
| `EMSL Analytical, Inc.` | ASL Analytical | 0.700 | **REJECT (correct)** |

Two mechanisms make the gate work, both of which the original analysis missed:

1. **`rescore_names` already carries expansions** — `expand_abbreviations(name)` and
   `_expand_state_abbrevs(name)` — and the gate takes the `max()` across all of them. So an
   abbreviation is scored in its expanded form.
2. **`_score_org` step 1 is an exact match against *any* variant, acronyms included**, and ROR v2
   records carry acronyms in `names`. "MIT" matches MIT's acronym variant outright.

Meanwhile the documented false positive it exists to stop (`EMSL` vs `ASL Analytical`) is correctly
rejected at 0.700.

**Consequence for this ticket.** The "gates can only subtract, never rescue" observation is still
structurally true, but the harm it was supposed to cause is not in evidence. Do not redesign this
gate on the original argument. Either find a *measured* population it wrongly rejects (ticket 11,
gate 3 — the paired ROR-score / local-score dump is exactly the right instrument), or close this
ticket as out of scope.

**Wider lesson, which is why ticket 11 outranks everything:** reasoning about this code path by
reading it produced a confidently wrong answer twice in one session (first the hypothesis itself,
then a probe that fed the org's own name back in as a rescore variant and returned a vacuous
1.000). Measure; do not reason.


## Re-scope (ticket 11, measured live over 300 lookups, 2026-08-29)

**The ROR limb of this ticket is dead.** Gate 3 (the local rapidfuzz rescore, the gate this ticket
was written to indict) fires on **3% of records** and is right about most of them: of 9 rejections,
2 are ROR false positives it correctly stopped (the `EMSL`/`ASL` pattern, caught in the wild), 6 are
ROR offering the **parent VISN network** instead of the specific VA medical center, and 1 is a
genuine wrong rejection (`Intelligent Epitaxy Technology Inc` -> `IntelliEPI`). The country guard
rejected **0** in 300 lookups; the collision-prone short-name rule rejected **0**.

A perfect gate-3 redesign moves chemspeed from 24% to at most 25%. **Question 5 now has an answer:
the change costs precision and buys ~1 record in 300.**

Notable: every one of the 9 gate-3 rejections has ROR score **exactly 1.000** against a local score
of **0.21-0.47**. The high-ROR/low-local population the ticket predicted **does exist** — it is just
not the abbreviation population. It is ROR's affiliation scorer being confidently wrong, which is
the thing the local rescore was added to catch.

**What survives is question 4 — the GLEIF name threshold.** The 88 cut refuses **309 candidates**
across the two corpora, and the 78-88 band holds real losses sitting immediately beside a correct
rejection:

| record | GLEIF candidate | score | correct? |
|---|---|---|---|
| `Dow Chemical` | `THE DOW CHEMICAL COMPANY` | 85.71 | **loss** |
| `Expeditors International of` | `EXPEDITORS INTERNATIONAL OF WASHINGTON, INC.` | 83.08 | **loss** |
| `ABB Inc` | `Abby Inc.` | 87.50 | correct reject |

The discriminator is **containment vs substitution**, which `token_sort_ratio` cannot express.
Lowering the threshold admits `Abby Inc.`; a containment-aware comparator admits the first two and
still refuses the third. If this ticket is kept, it is this and only this.
