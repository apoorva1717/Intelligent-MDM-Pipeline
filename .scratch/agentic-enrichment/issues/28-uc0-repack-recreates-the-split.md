# 28 — The UC-0 repack re-creates the split it just repaired

Type: task
Status: **parts A and B fixed; part C open (a decision)**
Blocked by: —
Source: measured on the S2 sample, 2026-08-29
Amended: 2026-08-29 — **the original premise was wrong.** See "Correction" below.
Evidence: `.scratch/agentic-enrichment/research/uc0-repack-recreates-the-split.md`

## Correction — what this ticket said, and why it was wrong

This ticket was filed as *"Stage 0 overflow misses a name ending in a dangling
connector"*, on the strength of a regex probe over the **output** workbook: Name 1
matching a trailing connector with a non-empty Name 2.

**That probe cannot distinguish "UC 0 never fired" from "UC 0 fired, merged,
enriched correctly, and was then repacked back into the same two columns."** It is
the second one. The counts in the original ticket are right; every causal claim
in it was wrong.

Answering the ticket's own question 1 — *why does `overflow_check` not fire?* — it
does fire:

* Stage 0 runs unconditionally (`orchestrator.py:5206`), no entry gate.
* The warm LLM cache from the run holds 145 UC-0 verdicts. **22 are
  `is_overflow=true`, every one at `high`** — including all five pairs this ticket
  listed as misses.
* The SERP cache proves the merged name reached the tiers:
  `exxonmobil research engineering co annandale nj`,
  `novartis institute for biomedical research inc official website cambridge ma us`.

Prompt rule 5 *is* anchored on the wrong end — it describes a **lower** field
*opening* with a connector, not an **upper** field *ending* with one — but rules 1
and 4 carry these cases without it. Worth tidying; not the defect.

**14 of the 15 dangling-connector rows are the repack.** The one genuine UC-0
negative is `DOH - Bureau of` + `Bureau of Public Health Laboratories`, where
the repeated `Bureau of` makes `false/high` defensible.

## The actual defect, part A — `chunk_name` has no opinion about where it cuts

`name_repack.chunk_name` retreats to the last word boundary that fits and stops.
With `NAME_FIELD_WIDTH = 32` that lands on precisely the boundary SAP's own writer
used, so the output is byte-identical to the input:

```
Exxonmobil Research & Engineering Co           -> 'Exxonmobil Research &'       + 'Engineering Co'
ExxonMobil Technology and Engineering Company  -> 'ExxonMobil Technology and'   + 'Engineering Company'
Expeditors International of Washington, Inc.   -> 'Expeditors International of' + 'Washington, Inc.'
Novartis Institute for BioMedical Research Inc -> 'Novartis Institute for'      + 'BioMedical Research Inc'
National Technology & Engineering Solutions of Sandia
                                               -> 'National Technology &'       + 'Engineering Solutions of Sandia'
```

A cut that strands a trailing coordinating conjunction, preposition or article is
the one cut the function must never take. `Exxonmobil Research` + `& Engineering Co`
is the same width and reads correctly.

**Widening the column does not fix this.** `NAME_FIELD_WIDTH` is 32 against a real
35-char SAP column; `Exxonmobil Research & Engineering Co` is 36. The cut *point*
is the bug, not the cut *width*.

## The actual defect, part B — the flags describe a slot layout that no longer exists

`finalise` order: `compute_flags` (`orchestrator.py:1764`) → `derive_search_terms`
(1772) → `_classify_record` (1792) → `_repack_merged_name_block` (1797).

The repack re-derives `*_changed` and remaps `_registry_name_fields` through its
`origin` map — but **`flag_codes`, `flagged_fields` and `flag_reason` are left
standing** against the pre-repack slots. Simulated end to end on a real Sandia row:

```
1. SAP input        name1='National Technology &'  name2='Engineering Solutions of Sandia'  name3='LLC'
2. after UC0 merge  name1='National Technology & Engineering Solutions of Sandia'  name2='LLC'
                    compute_flags() sees name2 = 'LLC'   <-- the string it flags
3. after repack     name1='National Technology &'  name2='Engineering Solutions of Sandia'  name3='LLC'
                    workbook shows name2 = 'Engineering Solutions of Sandia'
```

The reviewer reads *"Name 2: left exactly as supplied — the canonical form could not
be established"* against `Engineering Solutions of Sandia`. **The flag was raised
about `LLC`.** Six rows in the 200 are in this state.

`repack_name_block` already returns `origin: {dest_index -> source_index}` and the
caller already uses it to carry registry ownership across the move. The flag slot
references are the same kind of state and are not carried.

## The actual defect, part C — attribution follows a fragment

`Exxonmobil Research &` ships `input:verified+web`. So do `Novartis Institute for`,
`Novartis Institutes for` and two Sandia rows. The repack is deliberately a
*transform* that carries attribution (`orchestrator.py:1160-1164`) — coherent for a
clean cut, indefensible for a cut through the middle of a phrase: nothing verified
`Novartis Institute for`. The Scheme-B grammar assertion cannot catch it because
the string parses.

Decide whether a piece that is not the value a source verified may keep `verified`.

## What is NOT claimed any more

The original ticket's *"the cost is paid three times over"* attributed the bad
domains to the truncation. **The search ran on the whole name**, so the split does
not cause them. `nlrb.gov` on ExxonMobil is ticket 27, standing on its own. The
`verified+domain` vs `low` comparison the ticket drew is between two different
strings: `ExxonMobil Research & Engineering` (33 chars, Name 2 = `Accounts Payable`,
correctly **not** merged and therefore never repacked) versus
`Exxonmobil Research & Engineering Co Clinton Twp Facility`.

## Questions

1. **Part A** — the exact predicate for a forbidden cut point. A trailing `&`, `and`,
   `or`, `of`, `for`, `at`, `in`, `the`, `de`, `und`, `von`. Does a trailing comma or
   hyphen belong? Retreating one word must not produce an empty chunk.
2. **Part B** — carry the flag slot references through `origin`, or move the repack
   before `compute_flags`? Moving it breaks the docstring's contract (the classifier
   and search-term derivation both run *after* flags and must see whole names), so
   carrying is the smaller change and matches the module's own precedent.
   `flag_reason` is a rendered string containing the slot label, so carrying means
   re-rendering, not just relabelling a field list.
3. **Part C** — may a repacked fragment keep `verified`?
4. Does a repaired cut let Stage 6 batch consensus group the six Sandia variants and
   the four ExxonMobil ones, which it cannot do today?

## What was done

**Part A — `name_repack.chunk_name`.** A new `CUT_STOPWORDS` set and a second
retreat: a piece that would end on a connector gives that word to the next piece.
A run of connectors retreats whole (`of the` both move). The last piece is never
retreated — nothing follows it, and a name that genuinely ends on a connector does.
The retreat is declined when carrying the word forward would push the next piece
past the width, because a mid-word cut is worse than a connector at a column edge.

The tidier cut costs a slot often enough to matter, and a piece with no slot is
*lost*, so `repack_name_block` now cuts twice: connector-aware first, and the dense
cut instead when the tidy one does not fit the block. Content beats aesthetics.
`chunk_name(..., avoid_connector_endings=False)` exposes the dense cut directly.

**Part B — `flags.relabel_name_slots`.** The flag columns now follow the value
across the repack, through the same `origin` map registry ownership already used.
It lives in `flags.py` beside `retract` and re-renders through `render`, so that
stays the only thing that builds the flag columns — this is a **relabelling**, not
a re-judgement, and does not touch rule 1. A name cut across three columns scopes
to all three; a source whose content was dropped leaves the scope, and a code whose
scope empties is dropped with it; a record-level code keeps its empty scope; fields
outside the name block are untouched.

## Measurement

Before/after over the 200-record sample, and the suite:

| | before | after |
|---|---:|---:|
| blocks with a connector-ending piece | **40** | **0** |
| blocks losing content off the end of the block | 0 | **0** |
| blocks whose cut changed | — | 40 |
| ...taking one more slot | — | 7 |

The Sandia case end to end: `National Technology &` / `Engineering Solutions of
Sandia` / `LLC` now repacks to `National Technology` / `& Engineering Solutions` /
`of Sandia` / `LLC`, and the flag that reads *"left exactly as supplied"* moved from
`Name 2` to `Name 4` — the slot that actually holds `LLC`, the string it was raised
about.

Suite: **5 failed, 2905 passed, 5 skipped**, against a clean-tree baseline of
**5 failed, 2888 passed, 5 skipped** — the same five documented pre-existing
failures, +17 new tests
(`TestTheCutNeverStrandsAConnector`, `TestTheFlagFollowsTheValue`).

Two existing expectations were updated rather than worked around, both encoding the
old cut point: `test_the_merged_name_is_what_the_registry_is_asked` now expects
`Massachusetts Institute` / `of Technology` (it asserted `Massachusetts Institute of`,
which is the defect), and the dropped-piece test now exercises the density fallback.

## Still open

**Part C** — a repacked fragment keeps the attribution of the whole:
`Exxonmobil Research &` still ships `input:verified+web`. Deliberate behaviour
(`orchestrator.py:1160-1164`), untouched here because it is a decision about what
`verified` means, not a defect to fix silently. With part A landed the fragments are
at least well-formed phrases, which narrows but does not close it.

**A re-run is still needed** to confirm on real output. Everything above is measured
against the *recorded* names in `enriched_samples_200.xlsx` and the pure functions;
no batch has been re-run.

## Evidence

`logs/compare/enriched_samples_200.xlsx`; warm `tests/fixtures/llm/` and
`tests/fixtures/serp/`. `chunk_name` claims are pure and reproduce with
`from enrichment.name_repack import chunk_name`.
