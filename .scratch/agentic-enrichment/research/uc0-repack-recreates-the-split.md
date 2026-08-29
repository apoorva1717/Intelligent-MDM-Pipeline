# Why Name 1 / Name 2 look broken in `enriched_samples_200.xlsx`

Measured 2026-08-29 on `logs/compare/enriched_samples_200.xlsx` (S2+S3, 200 rows),
against the warm LLM/SERP evidence caches from the run that produced it.

## The headline

**UC 0 is not failing. It detects the split, merges it, enriches the whole name
correctly — and then `_repack_merged_name_block` cuts the settled name back into
the exact split it was repaired from.** The output is byte-identical to the SAP
input, so every probe that reads the output workbook concludes UC 0 never fired.

Tickets 26 and 28 were both written from that reading. Their counts are right;
their causal claims are wrong.

## The proof, in four steps

**1. Stage 0 runs unconditionally.** `orchestrator.py:5206` — no entry gate.

**2. The LLM said yes, at high confidence.** 145 UC-0 verdicts are in the warm
LLM cache; **22 are `is_overflow=true`, every one of them `high`**, including the
records ticket 28 lists as misses:

```
[high] 'Exxonmobil Research &'        + 'Engineering Co'
[high] 'Exxonmobil Research &'        + 'Engineering Co Clinton Twp'
[high] 'ExxonMobil Technology and'    + 'Engineering Company'
[high] 'Expeditors International of'  + 'Washington, Inc.'
[high] 'Novartis Institute for'       + 'BioMedical Research Inc'
[high] 'National Technology &'        + 'Engineering Solutions of Sandia'
```

Prompt rule 5 ("a lower field opening with a connector") is indeed anchored on the
wrong end, but it does not matter — rule 1 and rule 4 carry these on their own.

**3. The merged name reached the search layer.** The SERP cache keys are queries
over the *whole* name, never the fragment:

```
'exxonmobil research engineering co annandale nj'
'novartis institute for biomedical research inc official website cambridge ma us'
'national technology and engineering solutions of sandia llc official website livermore ca'
'expeditors international of washington inc official website peabody ma'
```

**4. `chunk_name` reproduces the original split byte-for-byte.** `NAME_FIELD_WIDTH`
is 32; every one of these names is 36-60 chars, and the greedy word-boundary cut
lands on the same boundary SAP's writer did:

```
'Exxonmobil Research & Engineering Co'          -> 'Exxonmobil Research &'      + 'Engineering Co'
'ExxonMobil Technology and Engineering Company' -> 'ExxonMobil Technology and'  + 'Engineering Company'
'Expeditors International of Washington, Inc.'  -> 'Expeditors International of'+ 'Washington, Inc.'
'Novartis Institute for BioMedical Research Inc'-> 'Novartis Institute for'     + 'BioMedical Research Inc'
```

**14 of the 15 dangling-connector rows in the workbook are this.** The one genuine
UC-0 negative is `'DOH - Bureau of'` + `'Bureau of Public Health Laboratories'`,
where the repeated `Bureau of` makes `false/high` a defensible read.

## The defect this actually is

Not detection. Two things, both downstream:

### A. `chunk_name` has no opinion about where it cuts

It retreats to the last word boundary that fits and stops there. A cut that strands
a trailing `&`, `and`, `of`, `for` is the one cut it must never take, and it is the
cut it takes here. `'Exxonmobil Research' + '& Engineering Co'` is the same width
and reads correctly. Raising `NAME_FIELD_WIDTH` 32 -> 35 does **not** fix this:
`'Exxonmobil Research & Engineering Co'` is 36 characters.

### B. The flags describe the pre-repack block; the workbook shows the post-repack one

`finalise` order: `compute_flags` (1764) -> `_classify_record` (1792) ->
`_repack_merged_name_block` (1797). The repack re-derives `*_changed` and
`_registry_name_fields`, but **the flags, `flagged_fields` and `flag_reason` are
left standing** against a slot layout that no longer exists.

Simulated end to end on a real Sandia row:

```
1. SAP input       name1='National Technology &'  name2='Engineering Solutions of Sandia'  name3='LLC'
2. after UC0 merge  name1='National Technology & Engineering Solutions of Sandia'  name2='LLC'
   compute_flags() sees name2 = 'LLC'      <-- what it flags
3. after repack     name1='National Technology &'  name2='Engineering Solutions of Sandia'  name3='LLC'
   workbook shows   name2 = 'Engineering Solutions of Sandia'
```

The reviewer reads *"Name 2: left exactly as supplied — the canonical form could not
be established"* next to `Engineering Solutions of Sandia`. The flag was raised about
`LLC`. **Six of ticket 26's eight "Name 1 overflow" rows are this**, and the ticket
mis-classified them precisely because it read a post-repack value against a
pre-repack flag.

### C. Provenance follows the value onto a fragment

`'Exxonmobil Research &'` ships `input:verified+web`; `'Novartis Institute for'` and
two Sandia rows likewise. The repack is deliberately a transform that carries
attribution (`orchestrator.py:1160-1164`), which is coherent for a clean cut and
indefensible for this one: nothing verified `'Novartis Institute for'`. The grammar
assertion cannot catch it — the string parses fine.

## What ticket 28 gets wrong beyond detection

Its "cost is paid three times over" attributes the bad domains to the truncation.
The search ran on the whole name (step 3 above), so **the domain failures are not
caused by the split**. `nlrb.gov` on ExxonMobil is ticket 27, standing on its own.
The `verified+domain` vs `low` comparison it draws is between two *different*
strings — `'ExxonMobil Research & Engineering'` (33 chars, never merged, so never
repacked) and `'Exxonmobil Research & Engineering Co Clinton Twp Facility'`.

## Numbers

| | n |
|---|---:|
| rows in the workbook | 200 |
| UC-0 `true/high` verdicts in cache | 22 |
| rows whose block is exactly `chunk_name(join(block))` | 23 |
| dangling-connector Name 1 rows | 15 |
| ...caused by the repack | **14** |
| ...a genuine UC-0 negative | 1 |
| Name 2 canonicalisation flags | 57 |
| ...on a dangling-connector row | 8 |
| ...where the flag names a different string than the slot shows | 6 |

## What to decide

1. **Should `chunk_name` refuse a cut after a connector?** Cheap, deterministic,
   local, no re-run needed to reason about. Retreat one more word.
2. **Should the repack run before `compute_flags` instead of after?** The docstring
   argues it must run after everything that reads a name as a name. That argument
   holds for the classifier and search terms; it does not obviously hold for flags,
   which describe slots. Moving it is the fix for B, and it is not a small move.
3. **Should a repacked fragment keep `verified`?** If a cut piece is not the value a
   source verified, the attribution is false on its face.
4. **Should the block be repacked at all** when the whole name fits the real 35-char
   SAP column? Only 32 is targeted, and the 3-char reserve is what splits several of
   these.

## Reproduce

`.scratch/.../research/` probes ran against `tests/fixtures/llm/` and
`tests/fixtures/serp/` (warm) plus `logs/compare/enriched_samples_200.xlsx`.
`chunk_name` claims are pure — `from enrichment.name_repack import chunk_name`.
