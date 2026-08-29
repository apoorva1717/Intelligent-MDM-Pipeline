# 02 — Labelling rules: what "correct" means, per field

Decided 2026-08-29. Ticket: `issues/02-eval-metrics-and-labelling-rules.md`.
Consumer: ticket 03 (`eval/enrich_eval.py`). This document is meant to be implementable
without a follow-up question.

**Stance.** Where the pipeline already encodes an answer, the eval adopts it verbatim —
same function, same threshold, same vocabulary — and this note says so. The eval measures
the system against **its own contract plus the business's intent**, not against a
definition invented here. Every claim about pipeline behaviour below was checked by
reading the code and, where behaviour was load-bearing, by running a probe against
`.venv\Scripts\python.exe`. Probe results are quoted inline and marked **[probed]**.

---

## Q1 — The metrics

### Decision

**Three primary metrics, derived from one outcome partition, plus a named-risk count block.**

The partition is the primitive: every record lands in **exactly one** bucket per scored
field, the buckets sum to N, and the three metrics are ratios over subsets of it. This is
what stops the metrics being separately computed and quietly inconsistent, and it mirrors
the house style already set by `eval/dedup_eval.py` (pairwise metrics **plus** named
business-risk counts with the offending row ids).

**The partition, per scored field:**

| Bucket | Condition | Counts as |
|---|---|---|
| `hit` | pipeline wrote a value **and** it matches gold | pass |
| `error` | pipeline wrote a value **and** it does not match gold | **defect** |
| `correct-decline` | pipeline wrote nothing, gold says nothing was resolvable, **and the decline is legible** (Q3c) | pass |
| `unmarked-decline` | pipeline wrote nothing, gold says nothing was resolvable, **no flag and no derived-low** | **defect** |
| `false-decline` | pipeline wrote nothing, gold says a value existed | **defect (miss)** |
| `excluded` | gold label is `unestablished` (Q3) | not scored; reported as a count |

**The three metrics:**

1. **Coverage** = `(records with a registry-authored identity) / N`.
   A *volume* metric, not a correctness metric. Defined on the identifier columns:
   `ROR ID` or `LEI ID` non-empty. **Report the tighter variant alongside it** —
   `Name 1 Provenance` parses to source `ror` / `gleif` / `wikidata` — because the two
   are not the same population. **[probed]** on the 200-record baseline workbooks:
   S2 has 49 records with an id but only 41 with a registry-authored `Name 1`
   (8 carry an id while `Name 1` stayed `input:*`); S3 has 50 vs 47. The reverse never
   occurs (0 records in either file). So `id ⊋ registry-authored name1`, always.
2. **Precision on what was written** = `hit / (hit + error)`, per field.
   Declines are **not** in this denominator. The safety metric.
3. **Recall on the resolvable population** = `hit / (hit + error + false-decline)`, per field.
   Its complement, `false-decline / resolvable`, is the **false-abstention rate**.

**Named-risk counts** (integers with row ids, target zero, never averaged into a rate):
`silent_error` (Q5), `wrong_identifier_written` (Q4), `unmarked_decline`,
`record_type_hint_not_producible` (baseline §1 / ticket 15).

**The number ticket 03 needs for the cost model** — what fraction of records enter the
agent lane under decision 6 — is not a metric, it is a count over the same run:
`Tier 1 miss OR weak match OR refused ambiguity`. It is reported in the same JSON.

### Why

Coverage and precision alone are jointly gameable in exactly the direction this effort is
pointed. Suppressing writes raises precision and lowers coverage; without a third number
there is nothing to say whether a coverage drop was *honest* (no registry entry exists) or
a *loss* (a correct answer was discarded). Those are the same coverage number and
completely different products — and map.md's redraw says the known defect is the second
one: "MIT does not fail for want of reasoning; it fails because a correct answer was
discarded." A two-metric eval scores that defect as a coverage shortfall and cannot name
it. Recall against the resolvable population names it.

This is also the 09 research note's own conclusion, arrived at from the model side:
FACTS Grounding needed a disqualification filter because factuality metrics "can be
circumvented by ... shorter responses that evade conveying comprehensive information"
(1–5pp, and it reordered the leaderboard) — from which the note draws the **[inferred]**
rule: *"your abstention rate and your grounding rate must be scored separately, or the
lane will learn to abstain in order to look grounded."*

### What most nearly argued the other way

Recall requires the labeller to establish a **negative** — "this organisation is in no
registry" — which is the most expensive label in the set and the only one that cannot be
fully proven. Absence of a search hit is not absence of an entry.

**Kept anyway, with a mitigation that costs nothing:** the gold label for registry
membership is **three-state**, not boolean — `yes` / `no` / `unestablished`. Only `yes`
and `no` enter the recall denominator; `unestablished` rows are `excluded` and their count
is printed at the top of the report. A recall figure over 71 confidently-labelled records
with 29 named exclusions is honest; a recall figure over 100 with 29 guesses is not.

---

## Q2 — When is `name1_enriched` correct?

### Decision

**Set-membership under the pipeline's own name comparator.** Not exact match, not a
normalisation invented here.

```python
from enrichment.registry_match import names_agree

NAME_THRESHOLD = 88.0          # config.LEI_NAME_MATCH_THRESHOLD, the value consistency.py uses

def name1_correct(written: str, gold_accepted_names: list[str]) -> bool:
    return any(names_agree(written, a, NAME_THRESHOLD) for a in gold_accepted_names)
```

`gold_accepted_names` is a **closed, enumerated set per gold entity**: the GLEIF legal
name, the ROR display name, any registry alias/acronym the labeller confirms, and the
record's own input string **iff** the labeller judges it already canonical. It is anchored
to a single entity identified by its gold registry id.

**Correctness does NOT depend on `record_type`.**

**One coupling rule, and it is not optional:** where gold carries a registry id and the
pipeline wrote one, `name1` scores `hit` only if the written id is the gold id. A right
name attached to the wrong entity is scored as an **identifier error**, not a name pass.

### Why

`consistency.py` already answers "do these two strings name one organisation", and it is
the *only* place in the codebase that answers it across ROR and GLEIF. Its module
docstring states the reason directly: "the two registries do not answer the same question
about a name. GLEIF returns the FORMAL LEGAL name and ROR returns the BRAND." An eval that
scored `Bruker Corporation` against a gold of `Bruker` as an error would contradict the
gate that ships in production, and would report as defects the records the pipeline
handled correctly.

**[probed]** — `names_agree` at threshold 88, both halves (ratio, then containment):

| a | b | ratio | containment | agree |
|---|---|---|---|---|
| `Bruker Corporation` | `Bruker` | 100.0 | True | **True** |
| `CORTEVA AGRISCIENCE LLC` | `Corteva` | 53.8 | True | **True** |
| `Stryker Orthopaedics` | `Stryker` | 51.9 | True | **True** |
| `Owens Corning Sales LLC` | `Corning` | 53.8 | False | **False** |
| `Thermo Fisher Scientific` | `Fisher Scientific` | 82.9 | False | **False** |
| `Massachusetts Institute of Technology` | `MIT` | 10.0 | False | **False** |
| `BIC Corporation` | `Centene Corporation` | 76.5 | False | **False** |

The MIT row is not a bug in the comparator — an acronym is a different surface form, and
it belongs in `gold_accepted_names` as a member, which is precisely what set-membership is
for. That is the whole reason the rule is set-membership rather than a single canonical
gold string.

On `record_type`: the accepted-name set is per-**entity**, and which surface forms exist is
already settled by which registries hold the entity (a company in GLEIF has a legal name;
an institution in ROR has a display name; an org in both has both). Making name correctness
depend on `record_type` would make the eval inherit a known-broken input — the baseline
measured `record_type` exact-match at **43% (S2) and 0% (S3)**, and baseline §1 shows the
value `government` is *not producible* at all (`classifier.py:27-28` maps ROR's
*government* org type onto `research_institution`). `record_type` is scored as its own
field and gates nothing.

### What most nearly argued the other way

Adopting `names_agree` wholesale scores a **subsidiary as agreeing with its parent**.
**[probed]**:

- `names_agree("Bruker BioSpin Corporation", "Bruker Corporation", 88.0)` → **True**
- `names_agree("Bruker Daltonics Inc", "Bruker", 88.0)` → **True**

The containment half is direction-agnostic and keeps only the longer name's leading token,
so any `<Brand> <Division>` string agrees with `<Brand>`. In a master-data system where
`Bruker BioSpin` and `Bruker Daltonics` are legitimately distinct customers, that is a
wrong-entity write scored as correct.

This is why the rule is **not** "written agrees with the gold string" but "written agrees
with a *member of an enumerated set anchored to one gold entity*", plus the id-coupling
rule. `Bruker BioSpin Corporation` scores `hit` only if the labeller put it in that
record's set — which they only do if the gold entity is Bruker BioSpin. The burden moves
to the label, which is where it belongs, and the comparator stays the pipeline's own.

---

## Q3 — What is correct when the organisation is in no registry?

### Decision — **a legible decline is a SUCCESS. It is never a free one.**

Three properties, and all three are required:

**(a) `correct-decline` is a pass** in the outcome partition, reported as its own integer.

**(b) It cannot inflate anything.** It is excluded from the **precision** denominator
(nothing was written, so there is nothing to be right about) and excluded from the
**recall** denominator (nothing was resolvable). Declining is therefore worth exactly zero
metric points — it costs nothing and earns nothing.

**(c) The decline must be legible, or it is a defect.** A record counts as
`correct-decline` only if the field is empty or its provenance parses to source `input`,
**and** the record carries the marker that says so:

```python
from enrichment.confidence import parse, LOW

def decline_is_legible(row) -> bool:
    # any ONE of these is sufficient
    return (
        "no-match" in row.flag_codes
        or "unverified-inference" in row.flag_codes      # a value was written, unverified
        or row.flag_for_review is True                   # derived low on a core field
        or parse(row.name1_provenance)[1] == LOW         # input:low — the derived-low state
    )
```

A silent empty field with no flag and no low confidence is `unmarked-decline` — **a
defect**, counted by name with its row ids. This is the "refused" vs "refused for the right
reason" split, made mechanical.

### Why

The business consequence is asymmetric, and the pipeline already encodes that asymmetry as
a hard constraint rather than a preference. `provenance.py` write-locks the six scoped
fields and drops any value whose origin cannot be reconstructed. `confidence.py` hard rule 2
forbids a witness-less `verified` outside a registry. `flags.py` rule 3 states plainly:
*"Absence of data is not a defect."* An eval that scored an honest decline as a miss would
be scoring the system against a definition the system explicitly rejects — and, worse,
would push any future agent toward exactly the failure the write-lock exists to prevent:
a `verified` value that nothing authored.

The 09 note supplies the number on the other side of that trade. GPT-5 System Card,
SimpleQA **[measured]**: `gpt-5-thinking-mini` gives up **2 points of accuracy for a
49-point reduction in wrong answers** against `o4-mini`. That is the trade this pipeline is
built around, and the eval must not price it backwards.

And there is a concrete downstream reason it must be a pass rather than a miss: this is a
**customer master-data** system whose output goes to a human review queue and then into
SAP. A blank flagged field costs a reviewer's minute. A confidently-written wrong entity
costs a wrong merge in Phase 2 (Q4) and a wrong record in the system of record.

### What most nearly argued the other way — and it argued hard

**Rewarding refusal is gameable, and this pipeline is already over-refusing.**

A bare refusal *rate* is a metric that has been measured being gamed. RefusalBench
**[measured]**: GPT-4o reaches a high detection F1 by **refusing 60%+ of answerable
questions** while scoring only 54.1% category accuracy — it "can identify but not
understand informational flaws." FACTS Grounding needed a disqualification filter for the
structurally identical reason.

Worse, the local case is not hypothetical. map.md's redraw says the ROR path re-scores
ROR's own affiliation match with a whole-string rapidfuzz ratio in a purely conjunctive
gate with no rescue path, "which rejects exactly the abbreviation cases ROR gets right."
A naive rule of "declining is a success" would score that defect — the single most
actionable finding on the board — as a **win**.

**That is the whole reason for property (b).** A decline is free *only* when gold says
nothing was resolvable. Every decline on a record gold says was resolvable is
`false-decline`, is counted against recall, and is printed with its row id.
**Refusing is not rewarded; refusing correctly is not punished.** Those are different
rules and the partition is what keeps them apart. Property (c) closes the remaining hole:
refusing *invisibly* is a defect even when the refusal was right, because a reviewer who
is not told cannot act.

---

## Q4 — How are `ror_id` / `lei_id` scored?

### Decision

**A wrong identifier is strictly worse than no identifier, and is scored as a named risk
against a target of zero — never netted into a single accuracy rate.**

- Written and matches gold → `hit`.
- Written and does not match gold → `error`, **and** incremented into
  `wrong_identifier_written` with the row id, the written id and the gold id. Reported as
  an integer against target 0, alongside the precision rate.
- Not written, gold has one → `false-decline` (counts against recall).
- Not written, gold has none → `correct-decline` (Q3). **Never an error.**

Additionally sub-count `wrong_identifier_written` into its two failure modes, because they
have different downstream costs:
`convergent` (the same wrong id appears on ≥2 records that are different gold entities) and
`divergent` (a record's id is wrong but no other record shares it).

### Why — and the ticket's claim needs correcting

The ticket states "Phase 2 says a wrong one causes a wrong merge." **Verified in code; the
claim is directionally right but imprecise, and the imprecision matters.** A wrong id does
**not** deterministically merge anything, and the two error directions are guarded
*asymmetrically*:

- **`dedup/candidates.py:123-145`** — `_ids_converge` fires on a shared non-empty `lei_id`
  or `ror_id` and returns `Candidate(..., rule="id", score=1.0)`. `sort_key` ranks `id`
  at **0**, ahead of name similarity (1) and token overlap (2). It only *nominates*; the
  docstring is explicit: "a merge is never implied — this only picks the LLM candidate."
- **`dedup/prompts.py:33-34`** — the adjudicator is then **told**: *"A shared LEI (Legal
  Entity Identifier) means the records are the same legal entity"* and *"a strong
  same-INSTITUTION signal."* So the shared id is presented to the decider as
  near-dispositive.
- **`dedup/adjudicator.py:199-230`** — the only hard identifier guard fires on
  **divergent** ids inside one entity (`len(rors) >= 2` or `len(leis) >= 2`), splitting the
  entity into singletons and routing every row to `manual_review`. A **shared wrong** id
  gives `len == 1` and is **invisible to it**.
- **`dedup/scoring.py` + `dedup/weights.json`** — golden-record election reads neither
  `ror_id` nor `lei_id`. The blast radius is clustering only.

So the two modes are:

| Mode | Mechanism | Cost | Guarded? |
|---|---|---|---|
| **Convergent** wrong id (two different orgs share one wrong id) | rank-0 nomination + "same legal entity" told to the adjudicator | a **silent wrong merge** — two customers become one record | **No.** Nothing downstream can see it |
| **Divergent** wrong id (right entity, wrong id) | adjudicator split guard fires | a wrongly-split entity → `manual_review` | Yes. Visible, recoverable, costs reviewer time |
| **Absent** id | falls through to name/token similarity | dedup performs as it would without Phase 1 | n/a — no new error introduced |

The convergent mode is the one a plausible-but-wrong registry match actually produces
(two similar orgs both matched onto the same well-known entity — precisely the "BIC Corp →
Centene" shape `consistency.py` was written for), and it is the one with no guard. That
asymmetry is the reason the metric is a named count and not a rate: a rate lets one
convergent wrong id be averaged away by ninety-nine right ones.

### What most nearly argued the other way

A wrong id is also the Phase 1 output most likely to be *caught*: the divergent case
self-reports into `manual_review`, and golden-record election ignores ids entirely. One
could argue the true cost is bounded and a single precision rate suffices. **Rejected**,
because the bound only holds for the divergent case; the convergent case is unbounded,
silent, and appears in no downstream artefact.

### Normalisation (needed to compare at all)

**[probed]** the `ROR ID` column ships the **full URL**: `https://ror.org/032hx4q18`.
`LEI ID` ships a bare 20-character uppercase identifier: `MZK1AT00SJV4XB7WNL71`.
Comparison is on the normalised forms in the table below, not on the raw column.

---

## Q5 — Does the eval score `flag_codes` / `flagged_fields`?

### Decision

**Yes — but as a *conditioning variable on the value outcome*, never as a field with its
own precision and recall.** No per-code scoring, no comparison of `flag_codes` against an
expected code list.

Two things are computed:

1. **`silent_error`** — the primary safety number. A field outcome of `error` where the
   record did **not** tell anyone:
   ```python
   silent = (outcome == "error") and not row.flag_for_review \
            and field_base not in row.flagged_fields
   ```
   Target **zero**. Reported as an integer with row ids, exactly as `dedup_eval` reports
   `wrongful_block_candidates`.
2. **`unmarked_decline`** — Q3(c). A decline with no flag and no derived-low.

An `error` that *is* flagged is still an `error` — it still lowers precision. It is simply
not a `silent_error`. Wrong-and-flagged and wrong-and-silent are different outcomes and the
report distinguishes them; they are not averaged.

### Why

`flag_for_review` is **derived**, not `bool(flag_codes)` — in both directions. README, *The
derived review flag*: a core field at `low` confidence raises it with **no code attached**,
and an `ADVISORY_CODES` code (`domain-unverified`, `registry-location-mismatch`) emits its
prose **without** raising it. **[probed]** `render({'domain-unverified': ['domain']})` →
`flag_for_review=False`, `flag_reason` populated. So "a row with `flag_for_review` false and
a populated `flag_reason` is a valid, expected state — and after these two, the common one."

Scoring codes individually would therefore re-litigate a taxonomy the pipeline deliberately
*reduced*: `low-confidence-unchanged` is retired precisely because it duplicated a fact the
provenance column already states, and `flags.render` **raises** if a caller passes it. And
it would penalise correct records for carrying advisory codes — `domain-unverified` is the
top code in the baseline (34 S2 / 31 S3), by design, on records that are mostly right.

The only question the business actually needs answered is: **when the pipeline is wrong,
does a human find out?** That is `flagged_fields` ∩ wrong fields, and nothing else.

### One gap this surfaced — verified, and worth the lead's attention

**`flagged_fields` structurally cannot name an identifier.**
`flags._FIELD_ORDER` is `('name1','name2','name3','name4','name5','domain','contact','email','address')`,
and `_sorted_fields` **silently drops** unknown names. **[probed]**:

```
_sorted_fields({'ror_id','lei_id','record_type','name1'})           -> ['name1']
render({'source-conflict': ['ror_id','name1']})['flagged_fields']   -> ['name1']
```

So a wrong `ror_id` / `lei_id` / `record_type` can never appear in `flagged_fields`. The
`silent_error` test for the identifier fields must therefore fall back to
`flag_for_review` alone (plus `name1` scope as a proxy) — **the scorer must special-case
this, or it will report every identifier error as silent.** Whether that is a flag-model
gap worth closing is not this ticket's call; it is noted for 11/13.

### What most nearly argued the other way

`expected_issue_codes` already exists as eval metadata on the S2/S3 workbooks, and
`/issues` + `/issues/compare` are built endpoints that do per-code scoring. The machinery
is there and free. **Rejected for this eval** because those score *issue detection on the
input* (use-case coverage) — a different question from *identity correctness on the
output*. Mixing them would let a strong issue-detection score mask a weak identity score,
which is the exact failure mode this ticket exists to prevent. That report stays separate
and keeps running.

---

## The per-field comparison table

Scored fields, the exact column, the exact normalisation, and what counts as a match.
`row` is one output row keyed by the values of `api/output_columns.RESPONSE_COLUMNS`.

| # | Field | Output column | Gold column | Normalisation (both sides) | Match rule | In headline? |
|---|---|---|---|---|---|---|
| 1 | `name1_enriched` | `Name 1` | `gold_accepted_names` (`\|`-separated set) | `str.strip()`, collapse internal whitespace. **No case-folding, no legal-form stripping** — the comparator does both internally | `any(names_agree(written, a, 88.0) for a in gold_set)` using `enrichment.registry_match.names_agree`. **AND** if gold has an id and the row wrote one, the ids must match (Q2 coupling rule) | **Yes** |
| 2 | `ror_id` | `ROR ID` | `gold_ror_id` | `re.sub(r'^https?://(www\.)?ror\.org/', '', v.strip()).strip('/').lower()` → bare `032hx4q18` | exact string equality | **Yes** |
| 3 | `lei_id` | `LEI ID` | `gold_lei_id` | `v.strip().upper()` → 20-char code | exact string equality | **Yes** |
| 4 | `domain` | `Domain` | `gold_domain` | `utils.domain_resolver.canonicalise_domain(v)` on **both** sides (`https://www.MIT.edu/home` → `mit.edu`; `investors.lockheedmartin.com` → `lockheedmartin.com`) **[probed]** | exact string equality after canonicalisation | No — report only |
| 5 | `record_type` | `Record Type` | `gold_record_type` | `v.strip().casefold()`; gold restricted to the **producible** vocabulary `{research_institution, company, unknown}` (`classifier.RESEARCH/COMPANY/UNKNOWN`) | exact equality. A gold hint outside the vocabulary (e.g. `government`) is `excluded` and counted in `record_type_hint_not_producible` — see baseline §1, ticket 15 | No — report only |
| 6 | `name2_enriched` | `Name 2` | `gold_name2` | `v.strip().casefold()`, collapse whitespace, strip trailing `.,;:` | exact equality after normalisation; gold may be the literal `n/a` meaning "this record has no department", which makes an empty write a `correct-decline` | No — report only |

**Out of scope, deliberately:** `Name 3`–`Name 5` (README: *"Name 3–5 are not in Phase 1
provenance scope at all"* — they carry no confidence, so there is nothing to condition an
outcome on), and every carried-through SAP column.

**Read but never scored** (they bucket the record; they are not fields with outcomes):

| Column | Read with | Used for |
|---|---|---|
| `Name 1 Provenance`, `ROR ID Provenance`, `LEI ID Provenance`, `Domain Provenance`, `Record Type Provenance` | `enrichment.confidence.parse` — **never** `split(":")`; `web:acme.com:provisional` has two colons | coverage variant, Q3(c) legibility, entry-gate count |
| `Flag for Review` | as-is (bool) | `silent_error`, Q3(c) |
| `Flagged Fields` | split on `,`/`;`, strip | `silent_error` (with the identifier special-case above) |
| `Flag Codes` | split on `,`/`;`, strip; vocabulary is `flags.ALL_CODES` | Q3(c) legibility (`no-match`, `unverified-inference`) |

---

## The gold-label schema (ticket 03's fixture)

One row per input record, joined to the run artefact on `(name1_original, city)` — the same
key `tools/run_diff.py` uses.

| Gold column | Type | Meaning |
|---|---|---|
| `record_key` | str | `(name1_original, city)` join key |
| `gold_in_registry` | `yes` \| `no` \| `unestablished` | **three-state, deliberately.** Only `yes`/`no` enter the recall denominator |
| `gold_ror_id` | str \| empty | bare ROR id, no URL |
| `gold_lei_id` | str \| empty | 20-char LEI |
| `gold_accepted_names` | `\|`-separated | the closed surface-form set for the gold **entity**: GLEIF legal name, ROR display name, confirmed aliases/acronyms, and the input string iff already canonical |
| `gold_domain` | str \| empty | registrable domain |
| `gold_record_type` | str \| empty | producible vocabulary only |
| `gold_name2` | str \| `n/a` \| empty | `n/a` = this record has no department |
| `label_note` | str | free text; **required** whenever `gold_in_registry` is `no` — what was searched, so the negative is auditable |

`gold_in_registry = no` is the label that makes Q3 work and is the one that cannot be
proven. `label_note` is what makes it reviewable rather than merely asserted. Labelling is
HITL per ticket 03; the agent may propose, a human confirms.

## Report shape

Mirror `eval/dedup_eval.py`: JSON to `--out`, with (1) the excluded/`unestablished` counts
printed first, (2) the per-field partition table, (3) the three metrics per field,
(4) the named-risk counts **each with its offending row ids**, and (5) the entry-gate
count for the cost model. A metric with no row ids behind it is not auditable and is not
enough.
