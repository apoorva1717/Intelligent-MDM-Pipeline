# 02 — What does "correct" mean, per field?

Type: grilling
Status: decided
Blocked by: —

## Question

Before anything can be labelled, the labelling rules have to exist. Decide:

1. **What are the metrics?** The working proposal is **coverage** (share of records leaving with a
   registry-authored identity) and **precision on what was written** (of the values written, how
   many are right). Are those the two that matter, and is there a third?
2. **When is `name1_enriched` correct?** GLEIF returns a formal legal name, ROR returns a brand.
   "Bruker Corporation" vs "Bruker" — is one correct and the other wrong, are both correct, or
   does it depend on `record_type`? `consistency.py` already treats these as agreeing; the eval
   should not contradict the pipeline's own definition.
3. **What is the correct answer when the organisation is genuinely not in any registry?**
   Is an empty, flagged field a *success* (correctly declined) or a *miss*? This decides whether
   the agent is rewarded for refusing — which decides how it behaves.
4. **How are `ror_id` / `lei_id` scored?** Wrong identifier vs no identifier: are they equally bad,
   or is a wrong one worse? (Phase 2 says a wrong one causes a wrong merge.)
5. **Does the eval score `flag_codes` / `flagged_fields`?** A record that is wrong *and flagged*
   is a different outcome from one that is wrong and silent.

## Why this is a decision, not work

Get this wrong and the eval measures the wrong thing, and every later claim rests on it.

## Decision

Decided 2026-08-29. Full rules, the per-field comparison table and the gold-label schema:
[`research/02-labelling-rules.md`](../research/02-labelling-rules.md). Ticket 03 is unblocked.

**1. Metrics — three, derived from one outcome partition, plus named-risk counts.**
Every record lands in exactly one bucket per field (`hit` / `error` / `correct-decline` /
`unmarked-decline` / `false-decline` / `excluded`), the buckets sum to N, and the metrics are
ratios over subsets of it. (i) **Coverage** = share leaving with a registry-authored identity
(measured on `ROR ID` / `LEI ID`; report the tighter `Name 1 Provenance` variant too — measured,
they differ: S2 49 vs 41, S3 50 vs 47, and the id set is always the superset). (ii) **Precision
on what was written** — declines excluded from the denominator. (iii) **Recall on the resolvable
population**, whose complement is the **false-abstention rate**. The third is not optional:
without it a coverage drop cannot be told apart from a discarded correct answer, which is
map.md's headline defect (MIT). Named-risk counts, integers with row ids against target zero,
in `dedup_eval`'s house style. *Nearly argued otherwise:* a negative label ("in no registry")
cannot be proven — mitigated by a three-state `gold_in_registry`, with `unestablished` excluded
from the denominator and its count printed.

**2. `name1_enriched` — set-membership under `enrichment.registry_match.names_agree` at 88.**
Adopted verbatim from `consistency.py`; the eval must not contradict the gate that ships.
Gold carries an enumerated `gold_accepted_names` set per **entity** (GLEIF legal name, ROR
display name, confirmed aliases/acronyms). **Independent of `record_type`** — the surface forms
follow from registry membership, and coupling to a field measured at 43%/0% accuracy would make
the eval inherit a known-broken input. One coupling rule: where gold has an id and the row wrote
one, the ids must match — a right name on the wrong entity is an id error, not a name pass.
*Nearly argued otherwise, and measured:* `names_agree("Bruker BioSpin Corporation", "Bruker
Corporation")` → **True**, `names_agree("Bruker Daltonics Inc", "Bruker")` → **True**. The
containment half scores a subsidiary as agreeing with its parent — which is exactly why the rule
is membership of an **enumerated, entity-anchored** set rather than open-ended agreement.

**3. A legible decline is a SUCCESS — and never a free one.** (a) `correct-decline` is a pass.
(b) It is excluded from **both** the precision and the recall denominators, so it is worth
exactly zero metric points: it costs nothing and earns nothing, and cannot inflate any number.
(c) It only counts if the decline is **legible** — `no-match`, or `unverified-inference`, or
`flag_for_review`, or `name1_provenance` parsing to `low`. A silent empty field is
`unmarked-decline`, a **defect**. Rationale: the pipeline already encodes this asymmetry as a
hard constraint (`SCOPED_FIELDS` write-lock, hard rule 2, `flags.py` rule 3 "absence of data is
not a defect"); scoring an honest decline as a miss would score the system against a definition
it explicitly rejects and would push a future agent toward the one failure the write-lock exists
to prevent. *Nearly argued otherwise:* rewarding refusal is gameable (RefusalBench: GPT-4o
refuses 60%+ of answerable questions; FACTS Grounding needed a disqualification filter), and
**this pipeline is already over-refusing** — the ROR whole-string rescore discards correct
answers. Property (b) is the answer: refusing is not rewarded, refusing *correctly* is not
punished, and every decline on a resolvable record is `false-decline` by name and row id.

**4. Identifiers — a wrong one is strictly worse than none; scored as a named count against
target zero, not netted into a rate.** *The ticket's claim needed correcting.* A wrong id does
**not** deterministically merge, and the two directions are guarded asymmetrically:
`candidates.py:123-145` `_ids_converge` only *nominates* (rank 0, ahead of name and token), but
`prompts.py:33-34` then **tells** the adjudicator a shared LEI "means the records are the same
legal entity"; `adjudicator.py:199-230`'s split guard fires only on **divergent** ids
(`len >= 2`) and is blind to a **shared wrong** id; `scoring.py`/`weights.json` ignore ids
entirely, so the blast radius is clustering only. So: convergent wrong id → silent wrong merge,
**no guard**; divergent wrong id → wrong split to `manual_review`, visible and recoverable;
absent id → dedup falls back to name/token, i.e. no new error. Sub-count the two modes.
*Nearly argued otherwise:* the divergent case self-reports and election ignores ids, so the cost
looks bounded — but that bound does not hold for the convergent case, which is exactly what a
plausible-but-wrong match produces and is invisible everywhere downstream.

**5. Flags are scored as a conditioning variable on the value outcome, not as a field.** No
per-code precision, no expected-code list. Two numbers: **`silent_error`** (`error` AND not
`flag_for_review` AND the field not in `flagged_fields`) against target zero with row ids, and
`unmarked_decline`. Rationale: `flag_for_review` is derived in both directions (an advisory code
ships prose without queueing; a core `low` queues with no code), so per-code scoring would
re-litigate a taxonomy the pipeline deliberately reduced and would penalise correct records for
carrying `domain-unverified` — the top code in the baseline, by design. *Nearly argued
otherwise:* `expected_issue_codes` and `/issues/compare` already exist — but they score issue
detection on the **input**, and mixing that with identity correctness on the **output** would let
one mask the other.

**Gap surfaced, verified, for 11/13:** `flags._FIELD_ORDER` has no identifier entry and
`_sorted_fields` silently drops unknown names, so **`flagged_fields` can never name `ror_id`,
`lei_id` or `record_type`** — probed: `_sorted_fields({'ror_id','lei_id','record_type','name1'})`
→ `['name1']`. The scorer must special-case the identifier fields or it will report every
identifier error as silent.
