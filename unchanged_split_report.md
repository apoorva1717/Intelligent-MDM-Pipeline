# Fix 2 — splitting the unchanged-Name-1 outcome into three states

**Observed.** 42 records in the chemspeed baseline kept the input Name 1. 37 were
flagged `low-confidence-unchanged`; 5 were not. The five were not better
evidenced — one of them (`3M Corporate`) had *less* evidence than several
flagged rows. They escaped because the flag depended on **which branch reached
the passthrough**: `_apply_tier3` marked every name slot it declined to rewrite
and the ROR-miss research path marked Name 1, while the company branch's
"canonicalisation failed, keep the input" path marked nothing at all.

**Change.** The decision moved out of the branches and into `finalise`, where it
is taken once from the settled record (`enrichment/unchanged_state.py`), and the
single outcome became three.

| State | Condition | Provenance | Flagged |
|---|---|---|---|
| `unchanged-verified` | corroborated by evidence independent of the record | `input:1:verified` | no |
| `unchanged-confirmed` | the company-canonical model's generated proposal reproduces the input under `normalize_key` | `input:1:confirmed` | no |
| `unchanged-unresolved` | nothing came back, or what came back names something materially different | `input:1:rule` | **yes** — existing code, existing reason text |

---

## Results (run F, 100 records, live APIs)

```
python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx \
    --out logs/runs/F_final.xlsx --json logs/runs/F_final.json \
    --retry-trace --trace-out logs/runs/F_trace.jsonl --concurrency 5
```

**48 records kept the input Name 1** (the baseline's 42; the population moves
run to run because the pipeline is LLM-driven — see *Reproducibility* below).

| State | Count | Flagged |
|---|---|---|
| `unchanged-verified` | **24** | 0 |
| `unchanged-confirmed` | **6** | 0 |
| `unchanged-unresolved` | **18** | 18 |

**Flag count drops materially.** `low-confidence-unchanged` **36 → 18** on the
batch; records carrying any flag **54 → 34**. Half the flags in this category
were asking a reviewer to confirm a value the pipeline had already corroborated.

**What corroborated the 24 verified rows:**

| Evidence | Count |
|---|---|
| Page read (Fix 3) — the organisation's own site states this identity | 12 |
| `domain:name` — ownership guard tied the domain to Name 1 by name similarity | 9 |
| `domain:serp` — every significant Name-1 token appears in the title/H1 of a result *on that domain* | 3 |

The full per-row table (name, shipped value, state, provenance, evidence,
flagged) is `logs/runs/unchanged_rows.md`, 48 rows.

### The regression anchors

The four rows that were silently unflagged in the baseline had to keep their
behaviour and gain only a label:

| Row | Baseline | Run F | Verdict |
|---|---|---|---|
| `Aixelo Inc.` | `input:1:rule`, unflagged, `aixelo.com` | `input:1:verified`, unflagged, `aixelo.com` | ✅ label only |
| `Aroma Creations Inc.` | `input:1:rule`, unflagged, `aromacreations.com` | `input:1:verified`, unflagged, `aromacreations.com` | ✅ label only |
| `Ascension Publishing Inc.` | `input:1:rule`, unflagged, `ascension-publishing.com` | `input:1:verified`, unflagged, `ascension-publishing.com` | ✅ label only |
| `Advanced Composites Inc.` | `input:1:rule`, unflagged, `advancedcomposites.com` | `llm_tier3:3:self_medium`, **unflagged**, `advancedcomposites.com` | ⚠️ behaviour unchanged; outside the population |

The fourth needs stating plainly: in run F, Tier 3 authored its Name 1 (writing
`Advanced Composites Inc.` where the baseline kept `Advanced Composites Inc`),
so the record is not in the retained-Name-1 population at all and none of the
three states describe it. **Its behaviour is unchanged** — same domain, still
unflagged — but the label is `llm_tier3`, not `unchanged-verified`. That is
run-to-run LLM variance, not a Fix 2 outcome: with the same provenance as the
baseline it lands in `unchanged-verified` on the domain evidence, which is what
the other three demonstrate.

### The fifth row: `3M Corporate`

`3M Corporate` was the baseline's fifth silent pass. It has no accepted domain
(its candidate was rejected by the ownership guard), and the company-canonical
model proposed `3M Company`, which the identity guard refused as a different
entity. That is `unchanged-unresolved`, and it is **now flagged**
`low-confidence-unchanged` alongside the `domain-unverified` it already carried.
This is the consistency fix working: the row was escaping the flag by accident,
not by merit.

---

## Two defects the run exposed, and what was done

**1. `enrichment_status` left at its `failed` default.** The first run of Fix 2
returned 12 records with `enrichment_status = "failed"` against a baseline of 1.
The new `unchanged-confirmed` short-circuit returned before any tier set the
field, so `_init_result`'s default stood — and `failed` maps to DATAshaper's
"Error — requires investigation". Fixed by `unchanged_state.enrichment_status_for`,
which maps verified and confirmed onto `verified` ("Info — confirmed correct")
and never downgrades `enriched`. Run F reports **0 failed**, and 30 `verified` —
the first path other than Tier 2A Mode B ever to produce that status.

**2. A Stage 0 short-circuit was being classified.** A UC 0 overflow returns
before Tier 1 is queried, so "nothing came back" would be a false account of a
question never put — and the record already carries `overflow`, which is the
actionable code. `resolve()` now returns `None` when `_tier1_query_name` is
absent. Caught by an existing test (`test_uc0_overflow_early_return_is_normalised`),
which is why it is worth having.

---

## Decisions taken, with the evidence

**Registry near-match is NOT implemented as a corroboration source.** The brief
lists it as one of three. Across the 41 retained-Name-1 rows in the traced
baseline, exactly **one** guard-rejected registry candidate scored high enough
to qualify:

> `Advanced Composites Inc` ← `TORAY ADVANCED COMPOSITES USA INC.`, GLEIF name
> verification, **80.7** against the 88 threshold.

That is a different legal entity — Toray's US subsidiary, not the customer. A
sub-threshold registry candidate is by construction a name that *looks* similar
and was refused for it; promoting those refusals to corroboration would launder
exactly the decisions the guard exists to make. Implemented: the two evidence
classes that hold up. **Open item** — if a later batch shows registry
near-matches that are genuinely the same entity, the condition to add is
"rejected by the country guard while matching the name above threshold", which
is the only non-identity refusal ROR makes.

**`email` does not corroborate.** A non-generic address on the record says which
organisation the *record* belongs to. It says nothing about whether the Name 1
string is that organisation's name, which is the question. Excluded, though it
is enough to keep the domain.

**A confirmed record ships its own string, not the model's.** When the proposal
is `normalize_key`-equal, Name 1 is written from the input. Adopting the model's
comma would change the value with no claim behind it, and punctuation belongs to
the DATAshaper validation mapping. Visible in the delta as four rows —
`AgraQuest, Inc.` → `AgraQuest Inc`, `Allnex USA Inc.` → `Allnex USA Inc`,
`Aprecia Pharmaceuticals, LLC` → `Aprecia Pharmaceuticals LLC`,
`Amylin Pharmaceuticals, Inc.` → `Amylin Pharmaceuticals, Inc` — each of which
is the record's own spelling replacing the model's repunctuation.

**Scope is Name 1.** The department slots keep their existing per-field
`low-confidence-unchanged` rule. The three states turn on evidence that an
organisation's identity is right — a registry, an owned domain, a page that
names the company — and none of that has a counterpart for "is this the right
spelling of the Department of Chemistry".

---

## Open item: `no-match` on an LLM-authored, materially unchanged Name 1

Run F ships one `no-match` that the baseline did not: `Acrotein ChemBio Inc`.
Tier 3 "wrote" Name 1 with a value identical to the input, so the record is
outside Fix 2's population (its provenance is `llm_tier3`), and `name1_changed`
is false, so `unverified-inference` does not fire either — leaving `no-match`.

This is **pre-existing** behaviour: `_apply_tier3` has always skipped a slot it
wrote (`if field in written: continue`), so such a row was never marked
`low-confidence-unchanged` before Fix 2 either. It surfaces here only because
the LLM happened to route this record through Tier 3 in this run. It is real,
and it is the same inconsistency Fix 2 removes, one corner further out: the
population should arguably be "the shipped Name 1 equals the queried Name 1
under `normalize_key`", regardless of which producer's write landed last.

Not changed, because the authorised population is rows whose Name 1 provenance
is `input`, and extending it would fold Tier 3's agreement — an evidence-free
producer — into a state that means "corroborated". **Listed here rather than
silently defaulted.** The record is still flagged, so nothing escapes review.

---

## Reproducibility

The pipeline is LLM-driven and not bit-reproducible; the retained-Name-1
population is 42 in the supplied baseline, 41 in the traced baseline (run A) and
48 in run F. The movement is dominated by Fix 2's own interception — six rows
whose company-canonical proposal equalled the input are now attributed to the
input instead of to the model, which moves them *into* the population — plus
ordinary run-to-run variance in which tier answers first. Every count above is
from run F; nothing is projected.

Tests: `tests/test_unchanged_state.py`, 17 cases — each state from its own
condition, the `normalize_key`-equality path, the byte-identical reason text,
the provenance projection, and the three ways the states must not change
anything (Name 1's value, a rewritten name, the department slots).

---

> **\reviewnote summary.** Splitting the single unchanged-Name-1 outcome into
> `unchanged-verified` / `unchanged-confirmed` / `unchanged-unresolved` and
> deciding it once from the settled record — rather than per branch as each tier
> ran — halves the `low-confidence-unchanged` flag count on the 100-row
> chemspeed batch (36 → 18) and cuts flagged records from 54 to 34, while
> leaving the previously-unflagged rows unflagged and adding a flag to the one
> record that had been escaping it without evidence. Of 48 records that kept
> their input Name 1, 24 are corroborated (12 by a page read, 9 by a
> name-matched domain, 3 by on-domain search evidence), 6 are confirmed by a
> canonicalisation proposal that reproduced the input, and 18 remain unresolved
> and flagged with byte-identical reason text. The corroboration reuses the
> existing domain ownership guard rather than a second token matcher, and the
> one registry near-match in the batch that would have qualified — Toray
> Advanced Composites at 80.7 for "Advanced Composites Inc" — is precisely why
> registry near-match was left unimplemented and recorded as an open item.
