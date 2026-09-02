# Change Task Template — A/B gate protocol

The template these pipeline change tasks are written against. Kept in the repo for the
same reason `docs/thesis-doc-prompt.md` is: the protocol is what makes two runs
comparable, and a protocol that lives only in a prompt drifts between tasks.

---

## The gate

Every change is gated by an A/B: the same batches, the same evidence cache, the code
being the only difference. A gate states an **allow-list** — the shape of change the task
expects — and anything outside it stops the task with a report rather than a fix.

    Gate: A/B. Allow-list: <the expected shape>. Print every such row.
    Anything else: stop.

### Cache state — frozen or warm

**A gate for a change that alters queries or prompts runs WARM on both sides.**

`run_batch.py --frozen` sets `CACHE_FROZEN=true`: a SERP, page fetch or LLM completion the
cache does not already hold is recorded as `evidence-unavailable-frozen` instead of being
issued. That is what makes a frozen run deterministic, and it is exactly wrong for a change
that alters what is asked:

* a new or edited prompt has a different cache key, so it misses **by construction**;
* a lane that misses degrades — `grounded_degraded`, `LLMUnavailableFrozen` — and the A/B
  then measures the degradation, not the change.

A gate for such a change is run warm on both sides: populate the cache with a non-frozen
run first, then run both sides frozen against that same populated cache, or run both sides
non-frozen. Either way the two sides must see the *same* cache contents — warming between
the two runs invalidates the comparison, because warming also fills pre-existing gaps that
the baseline was previously degrading on.

Changes that do **not** alter queries or prompts (a casing rule, a flag rule, a write gate
downstream of the model) gate frozen, which is cheaper and stricter.

"Alters queries" is wider than it looks. Anything that changes a NAME before the tiers run
changes the ROR/GLEIF/SERP queries built from it, so it is a warm-gate change even though it
touches no prompt and no query builder — preprocessing's dept-block normalisation (Phase 4)
was mis-classified as frozen on exactly this reasoning and its miss counts went 1/0/6 -> 4/3/18.
The miss counts are the check: if they move, the change reached a query, whatever it looked
like in the diff.

**Every gate report states which it was, and against which cache.** One line, at the top of
the result, with all four fields:

    Gate: frozen | misses 1 / 0 / 9 (identical both sides)
          cache <path> — entries 5215, keys-sha256[:12] 3bde5eb38090
    Gate: warm   | 0 network calls both sides
          cache <path> — entries 6104, keys-sha256[:12] a91c77d02e41

Required fields, all four:

1. **frozen or warm**;
2. **frozen-miss counts per workbook**, which must be identical on both sides — a difference
   means one side saw evidence the other did not, and the diff is measuring that;
3. **cache entry count**;
4. **hash of the sorted key list** (`sha256` of the newline-joined relative paths, first 12
   hex chars).

The count and hash are what let a later reader tell whether two gate lines are comparable at
all. A gate taken against a different cache is a different experiment, whatever the code did.
The hash covers KEYS only, never contents: a re-recorded answer for an existing key does not
change what the run could replay. Both are read with `cache_state.py` against the evidence
cache directory actually in use (`EVIDENCE_CACHE_DIR`), which is not necessarily
`logs/cache`.

A report that does not carry all four is not a gate result.

### Baseline hygiene

* The baseline is captured once, from unmodified code, and never edited.
* If the cache changes between baseline and candidate — for any reason, including warming
  for this task — the **baseline is re-taken** against the new cache before comparing.
  Prove it: a re-run of the baseline code must reproduce the baseline byte-for-byte, and
  the frozen-miss counts must match on both sides.
* A revert is verified the same way: re-running the reverted code must reproduce the last
  gated state exactly.

---

## The report

Per section, in this order:

1. **Gate line** — frozen or warm, with miss counts.
2. **A/B result** — identical, or the allow-listed rows, or stopped.
3. **Row count per file** — the number a reviewer will actually see.
4. **Every changed row printed**, with whatever the allow-list says to print alongside it
   (the refused proposal, the donor, the verdict, the evidence excerpt).
5. **`pytest -q`** — the baseline failure count, and confirmation that it is the same set.
6. **The diff.**

---

## Rules that apply to every task

* Additive where the task says additive. Named files only.
* `pytest -q` after every section; the baseline failures must be the same five, and the
  same five — a different set of five is a stop.
* A live end-to-end check (`/enrich` over the motivating records) is not a substitute for a
  gate, and a gate is not a substitute for it. The gate says what moved across the batch;
  the live check says whether the record is right. Assertions in a live check must be
  strong enough to fail: "not equal to the wrong answer" passes when nothing was written
  at all.
* `/enrich` returns SAP column names (`Name 1`, `Flag Codes`), not model field names, and
  does not expose `source`, `confidence` or `tier_used`. Assert on the provenance event
  instead.

---

## Post-thesis list

Findings recorded during gated changes that were deliberately not fixed, because the fix is
larger than the task that surfaced it.

* **Monotone provenance at the `_write` funnel** — a write may not lower a field's provenance
  class (registry > web-corroborated > llm > input) except via an evidenced `different`
  verdict. Would have converted all three item-3 hard-rule failures into no-ops. Prerequisite
  for re-landing the grounded refusal fall-through, VISN drop, hint, and the unquote fallback
  (`snap3`).

  The three gate reports, by cache hash:

  | gate | cache | result |
  |---|---|---|
  | item 3, full (VISN drop unquoted + hint + eligibility) | `313b2dad9c94`, entries 5294 | warm, misses 3/0/6 — **not identical to baseline 1/0/6**, so the comparison was invalid on its own terms. 61 rows changed, 56 touching value columns. `13343269` and `13336642` lost `ROR ID` outright; `13336744`/`13336752` changed ROR entity. |
  | item 3a, split (VISN drop inside the quoted subject) | `313b2dad9c94`, entries 5294 | warm, misses 1/0/6 identical. 6 rows. `13336744`/`13336752` lost `ROR ID` and every `ror:verified` provenance. Isolated to the VISN drop ALONE: making the query return results routed the record away from Tier 3, whose answer the registry retry had been matching. |
  | item 3a re-land + `grounded_refused` fall-through | `896ad3925a8e`, entries 5335 | warm, misses 1/0/6 identical. 5 rows, 2 violations. `13336752` byte-identical — the fall-through worked. `13336744` lost `ROR ID`: the lane wrote a ROR-verified value for the wrong entity and `name_gate` refused it in `_apply_grounded`, AFTER the fall-through rule had already decided the lane had answered. |
  | `grounded_refused` rule alone | `896ad3925a8e`, entries 5335 | frozen, misses 1/0/6 identical. 8 rows, 1 violation: `13083855` `input:verified+web` -> `llm:provisional`. Not safe even without the query changes. |

  Every one of these is the same shape: a lane that previously produced nothing, or was never
  reached, starts producing something, and what it produces displaces a better-attributed
  value. A monotonicity rule at the funnel makes that impossible to express.

* **The annotations never use `G2-VAL-003`, `G2-VAL-006` or `G2-VAL-007`**, though all three
  fire on all 100 rows of both strata. Under Catalogue v2 scoring they sit outside the
  annotation vocabulary and are not counted as false positives; the gap between the
  annotation and the detector is still real and unclosed. `G2-VAL-003` and `G2-VAL-006` are
  G6 (persist by design); `G2-VAL-007` is G2 and is cleared 100 -> 0. Row ids: all 100 in
  each stratum.
* **`G1-ADDR-009` (27 expected, 0 raised on S1) and `G1-ADDR-011` (12 missed on S4)** are the
  largest pure recall gaps; `G3-ADDR-013`, `G3-ADDR-014`, `G4-ADDR-025`, `G4-NAME-015` are
  expected but never raised on either stratum. Recall is unaffected by the v2 corrections —
  0.566 on S1 and 0.744 on S4 both before and after — so these are the whole of the gap.
* **`G2-NAME-012` increases under enrichment** (S1 14 -> 20, S4 3 -> 9). Recorded as improved
  detectability rather than regression: the code needs `record_type = research`, and
  enrichment resolves `record_type` on records that arrived unclassified. Not verified
  record-by-record.
* **`G1-ADDR-001` rises on S4 under enrichment** (65 -> 66) and does not fall on S1 (17 -> 17)
  — the only G1 code enrichment does not reduce.
* **Output schema grows by two columns** — `Suggested Name` and `Suggestion Source`,
  appended after `Operating Name Provenance`. **Pending Bert's answer on whether DATAshaper
  passes unmapped columns through.** If it does not, the two need either a DATAshaper
  mapping or a feature flag; a flag has NOT been added, on the reasoning that adding one
  before the answer builds a switch nobody may need. Nothing downstream reads either column
  (grep-verified against `dedup/`, `batch_consensus.py`, `search_terms.py`,
  `issue_detection.py`, `flags.py`), so the risk is purely whether the columns survive the
  hand-off, not what they would do if they did.
* **Registry currency** — ROR display for `04xzj3x20` predates the 2023 rename to Los Angeles
  General Medical Center; the pipeline correctly ships the registry's current label.
  Operational remedy: ROR curation request. Worked example for the "as current as its
  registries" bound.
* **No-chosen override is exact-only by design** — worked example 13348274, UTMB Galveston
  vs University of North Texas; loosening it re-admits silently-wrong `ror:verified`, the
  costliest failure class. The exactness is word-level: separators `{+, /, –, —, -}` and
  whitespace runs fold, periods and apostrophes stay significant. It is deliberately NOT
  `normalize_key`, which folds legal forms — `batch_consensus._name_parts` already records
  why a dedup-GROUPING equivalence must not decide identity ACCEPTANCE ("Delta Analytical
  Inc" vs "LLC" at one address).
* **Two confirmation markers for one question** — a grounded confirmation of Name 1 is
  recorded as `_canonical_proposal` (+ `_ev_grounded_confirmed_name1`), and of Name 2-5 as
  `_ev_input_confirmed`, with different consumers: the first feeds `unchanged_state`'s
  ladder, the second feeds the flag rules directly. Unifying them is not small — the two
  answer the same question for different fields and reach different decisions — so the split
  is recorded rather than forced.
* **Two token-cover implementations for one question** — `subject_preserved` keeps the 4-char
  floor (`vet` ↛ `veterans`) that `name_identity._covers` replaced; unify on `_covers`. Own
  gate; shipped values will move. Worked example: 13336736, tier 3's correct
  `"Olin E. Teague Veterans' Medical Center"` refused on the floor.
* **Two allowlists for one question (casing).** `_case_segment` asks `_SHORT_ORG_WORDS`
  whether a short ALL-CAPS token is a word; `_case_core` asks `_FORCE_TITLE_SHORT` for a
  <=3-letter token and `_SHORT_ORG_WORDS` for everything longer. The shape half of the
  question is now shared (`_shape_says_acronym`), the wordlist half is not — so a word added
  to one set may still shout from the other path. "GAS" had to be added to
  `_FORCE_TITLE_SHORT` specifically, having been in `_SHORT_ORG_WORDS` all along.
* **Input as event zero in the provenance log.** `original_value("name1_enriched")` is
  truthful only where the input reached the log as a passthrough event; where a tier wrote
  into an empty slot it reports that nothing was supplied. Worked around by carrying
  `name1_supplied` on the result. The fix is to record the input as the first event on every
  scoped field, which moves provenance on every record and needs its own A/B.
* **The dept block ships holes.** `dept-slot-echoes-name1:dropped` and
  `name-post-check:slot-names-nothing` clear a slot AFTER the only packing pass, so a record
  they fire on ships Name 2 empty with Name 3 populated. Closed by `dept_block.normalise`;
  the ordering that causes it is untouched.
