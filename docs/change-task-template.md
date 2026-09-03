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
* **Slash-joined acronyms are lowercased by `smart_title_case`.** `CALM/UCSD` ->
  `Calm/ucsd`, `UCLA/USC` -> `Ucla/usc`, `LABORATORY/STE 150` -> `Laboratory/ste 150`.
  Each half is an acronym the caser gets right ALONE (`CALM` -> `CALM`, `UCSD` -> `UCSD`);
  joined, the shape test sees one token that is not uniformly an acronym, title-cases it,
  and everything after the `/` goes lowercase. `/` is already in the exactness fold set
  for registry matching (`_is_exact_by_words`) — the caser does not split on it. Row ids:
  13333689, 13337503.
* **Two records for one entity ship two different Name 1 values.** 13333689 (`CALM/UCSD`,
  group DRIT) ships `Calm/ucsd` at `input:low`; 13337503 (`Calm/UCSD`, group 0002) ships
  `Calm` at `llm:provisional` — the LLM dropped the `/UCSD` half, and consensus did not
  unify the pair. Same street lines, same postal code, same domain (`ucsd.edu`). A steward
  sees one site under two names, neither of them the supplied one.
* **A relocated fragment ships its dangling separator.** 13337503's Street 2
  `Laboratory/Ste 150` splits, `Ste 150` returns to the address, and Name 2 ships as
  `Laboratory/`. Correctly flagged `relocated-unverified`, but the trailing separator is
  the split's own residue, not the record's text. The sibling row 13333689 carries the
  same content in Street 1 and ships Name 2 empty — so the street router treats the two
  lines differently as well. Row ids: 13333689, 13337503.
* **The derived-low clause reads "left exactly as supplied" on an INHERITOR.** Now that an
  inheritance renders at the donor's effective scale, 13337503 raises the same Name 1 clause
  as its donor — but its value was not left as supplied: it was `Calm/UCSD` and it ships
  `CALM/UCSD`, elected from a sibling. The doubt is right and the prose is not; the clause
  predates inheritance carrying a low. Needs a second rendering for the inherited case
  ("copied from a sibling record that was left exactly as supplied"). Row id: 13337503.
* **`_strip_residue` does not trim "/".** It trims `" ,;|-"`, so a street line that KEEPS
  its slot can still ship a dangling slash; only fragments routed into a name slot are
  trimmed (`_trim_fragment`). Unifying the two trim sets would move street columns
  corpus-wide and needs its own gate.
* **Two Name 1 values ship a trailing comma** — 13011411 `"ExxonMobil Research & Engineering
  Co.,"` and 13332323 `"Expeditors International of Washington,"`. Not split residue: these
  are SAP 40-character truncations, and the comma is the record's own text marking where the
  legal form was cut off (13332323's Name 2 is the surviving `"Inc."`). Left alone
  deliberately — trimming it would erase the only signal that the value is truncated.
* **`House Number` is an SAP input column, not a derived one.** The pipeline never splits a
  house number out of a street line — 64/100 S1 rows carry one because the export supplied
  it. A record submitted without the column ships `House Number = None` however cleanly its
  street parses, which reads as a parsing failure and is not one. Deriving it would be a new
  behaviour with its own gate. Worked example: 13337073, shipping `Street 1 = "307 Boatner
  Rd"` and `House Number = None`.
* **The correct domain demoted for containing its own industry's word.** `orchard-labs.com`
  — the right site for 13333947, "ORCHARD LAB CORP" — was ranked BELOW `labcorp.com` by
  `_domain_introduces_foreign_brand`. `_significant_tokens("ORCHARD LAB CORP")` drops the
  generic "lab" and returns `{orchard, corp}`, so the host's own `labs` label matches no
  surviving token and reads as a foreign brand word (rank 1, demoted), while `lab**corp**.com`
  matches `corp` cleanly (rank 2, chosen). The ranker handed the ownership guard the wrong
  candidate; the guard declined it correctly; the lane then stopped with the right answer at
  position 1 of the same SERP.
  §3's retry makes the mis-ranking SELF-CORRECTING — the lane walks on to the runner-up and
  the guard ties it — so the demotion no longer costs the record its domain. The ranking
  itself is untouched and still wrong: a generic word dropped for scoring is not thereby a
  foreign word in a host, and the two uses of `_significant_tokens` want different sets.
  Fixing the ranker is its own gate. Row id: 13333947.
* **Accepted-domain withdrawal (§A, parked behind
  `ACCEPTED_DOMAIN_CITY_WITHDRAWAL_ENABLED`, default off).** The escape set cannot
  currently distinguish `heartoftexasdpc` (wrong org, shares "heart") from
  `fisher` / `ucsd` / `darylflood` (right org, partial cover); every candidate narrowing
  breaks a protected case. Needs a distinctiveness-aware cover test designed with the full
  worked-example table: **13333920, thinksrs, darylflood, ucsd, fisher, steinen**.

  The rule is BUILT and tested, only the trigger is disabled: `_name_disagreement_stands`
  (shared by the refused-candidate and accepted-domain paths), the city-pair condition, and
  `_revoke_domain_witness` (+ `DOMAIN_WITNESS_REVOKED_RULE`), which re-derives Name 1 to
  `input:low` while leaving the original `fix2:unchanged-verified` event in the log — a
  witness withdrawal, not a class decrease.

  This pass's report, for whoever picks it up:

  | record | page states | score | why the disagreement does not stand |
  |---|---|---|---|
  | 13333920 `Heart of TX CHC` | "Heart of Texas Direct Primary Care" (Waco; record McGregor) | 57.1 | token cover on `heart` AND host contains `heart` — **wrong org, escapes anyway** |
  | 13237446 `Stanford Research Systems Inc` | "SRS" | 21.4 | acronym escape — right org |
  | 13345215 `Daryl Flood` | "The Suddath Companies" (acquirer) | 35.6 | host contains `daryl`/`flood` — right org |
  | 13333689 `CALM/UCSD` | "Regents of the University of California" (legal owner) | 25.0 | host contains `ucsd` — right org |
  | Fisher-class | "Fisher Scientific" | 82.9 | token cover on `fisher`/`scientific` — right org |
  | 13338029 `William Steiner Mfg` | "STEINEN" | 33.3 | **no escape fires** — near-miss `steiner`/`steinen`, domain probably right, currently refused by §1 |

  The two ends of the table are the problem: `heart` escapes and should not; `steinen`
  does not escape and should. Both are token-similarity questions the current cover test
  (substring containment, any single token) cannot answer.

  Landed separately and NOT parked: the witness-consistency rule on the
  `fix2:unchanged-verified` domain rung — it may not take that rung when the page
  corroborator recorded `name_mismatch` for that same domain. That removes 13333920's false
  `input:verified+web` without touching the domain column or needing any escape logic.
* **`DEPT_SPLIT_CANONICALISES` — flipped to `True`, gated, REVERTED.** The lane's
  protections all held: 51 admin-desk slots across the four workbooks were untouched
  (`has_no_canonical_form` guards it), and no slot was rewritten to a different unit — the
  stop trigger the task named never fired. It was reverted on the allow-list instead, which
  three of the eleven changed rows fell outside:

  | row | before | after | why it is outside |
  |---|---|---|---|
  | 13335676 (S4, t100) | Name 2 `Davie Medical Center` | **empty**, ST2 `DAVIE MEDICAL` -> empty | a deletion, not a canonicalisation — the refused answer cleared the slot instead of falling back |
  | 13333471 (S5) | `Suggested Name` "JFK Medical Center" / `Suggestion Source` "llm, refused: different_entity" | **empty** | the column that exists to make identity refusals steward-visible, silently cleared |
  | 13034224 (t100) | Name 2 `GD` | `Division of Geologic` | right unit (USGS's Geologic Division), form nobody writes |

  The two clean wins, for whoever re-lands it: 13348118 `Moores Cancer Center` ->
  `UCSD Moores Cancer Center`, and 13336873 `W.A. Foote Memorial Hospital` ->
  `W. A. Foote Memorial Hospital` with `unverified-inference` clearing.

  **The blocker is the refusal path, not the canonicaliser.** When the identity guard
  refuses the canonical form for a slot made eligible by this flag, the slot must fall back
  to the value it had; today it ships empty, and the record loses both the name and its
  search term. Fix that first, then re-gate.

  Also measured: the verification record 13336633 ("SAC FISH & WILDLIFE SERV" relocated
  from Street 2) is NOT canonicalised with the flag on — it ships unchanged at `input:low`
  with `relocated-unverified`. So the flag does not reach the street-relocated government
  case the task expected it to.
* **`DEPT_SPLIT_CANONICALISES` — re-land attempt, gated, REVERTED again.** The §1 fixes
  landed and are KEPT in the tree (they are correct independently of the flag); only the
  default went back to `False`.

  What the re-land fixed, measured on the second gate (20 rows, **0 allow-list violations,
  0 slots cleared, 0 admin-desk slots changed** of 51):

  | row | first gate | re-land |
  |---|---|---|
  | 13335676 Davie | Name 2 emptied, ST2 lost | **retained** — Tier 2 proposed the PARENT ("HCA Florida University Hospital"); refused at the lane, value kept |
  | 13336501 | Name 2 empty | **'Columbia Mainland Medical Center' retained** — same destruction, also fixed |
  | 13034224 USGS | `GD` -> `Division of Geologic` | **`Geologic Division`** — Tier 2 had proposed the correct form all along; UC 5's reorder was rewriting a tier's answer |
  | 13208652 | `Division of Cincinnati Procurement Operations` | **`Cincinnati Procurement Operations Division`** — same reorder bug, second worked example |

  Plus the wins that were always there: `Moores Cancer Center` -> `UCSD Moores Cancer
  Center`, `W.A. Foote` -> `W. A. Foote`, `Institute of Memory Impairments` -> `Institute
  FOR Memory Impairments` (x2), `Institute of Regenerative Medicine` -> `Institute FOR
  Regenerative Medicine` (x2), `Emerson Climate Technologies, Inc` -> `... Inc.`.

  **Why it was reverted: one suggestion lost.** 13333471 shipped `Suggested Name`
  "JFK Medical Center" / `Suggestion Source` "llm, refused: different_entity" in the
  baseline and nothing in the candidate. The suggestion was not cleared by the lane — the
  ROW STOPPED BEING FLAGGED (`relocated-unverified` dropped, `Flag for Review` True ->
  False), and the suggestion renderer runs only `if result.get("flag_for_review")`, so the
  column was never populated.

  The cause is BATCH-LEVEL, not the lane acting on this record: re-run in isolation under
  both flag states the record is byte-identical (origin `preprocess:street`,
  `relocated-unverified` raised, Name 2 `JFK Medical Center`). Something about the group —
  13336501 in the same corpus gains a Name 2 under the flag, which changes what batch
  consensus sees — retracts the relocated doubt. That is worth understanding before a third
  attempt: a doubt being dropped without being ANSWERED is the same failure class as the
  cleared slot, one level up.

  **Next attempt should start there**, not at the canonicalisation lane, which is now
  behaving.
* **Phase 5 shipped on the third gate — and the rule that made it safe.** The two failed
  attempts above were both the same defect wearing different clothes: a lane that CHANGED
  NOTHING nevertheless re-attributed what it touched. Gate 1 destroyed values (the refused
  canonical cleared the slot); gate 2 destroyed the record of where a value came from (the
  passthrough declared `producer="input"`, so `_slot_origin` went from `preprocess:street`
  to `input` and `relocated-unverified` stopped firing on seven records, five of them
  dropping out of review entirely — with their Name 2 byte-identical on both sides).

  The fix is one invariant at the write funnel: **an origin may change only when the value
  changes.** `_origin_for` answers "who performed this write", and for a write that changes
  nothing that is a different question from "where did this value come from". A transform
  already expressed this ("the origin follows the VALUE"); `_write` now guarantees it for
  any writer, so a lane cannot silently re-attribute a value however it declares itself.
  The fold is whitespace and case only — deliberately not `normalize_key`, which folds
  legal forms.

  Third gate: 18 rows, 0 allow-list violations, 0 slots cleared, 0 admin-desk slots
  changed (of 51), **0 doubts dropped without a value change**, 10 canonicalisations
  landing. Spend +17 Tier 2 calls (t100 +5, S1 +2, S4 +8, S5 +2).

* **The canonical short-circuit costs one suggestion — accepted, and reversible.**
  13333471 shipped `Suggested Name` "JFK Medical Center" with the flag OFF and nothing with
  it ON. Nothing is destroyed: the record keeps its value, origin, `relocated-unverified`
  and review state. The flag makes the canonicalisation lane run, and
  `canonical_short_circuit` then finishes the record ("If canonical ran on any field, the
  record is finished HERE") — so Tier 3, the lane that proposed "JFK Medical Center" and
  had it refused, never runs. Verified directly: flag off -> `tier3_ran=True`; flag on ->
  `tier2_canonical_ran=True, tier3_ran=False`.

  The trade is one refused-proposal suggestion for the canonicalisation of that record's
  block, it is visible per-record, and `DEPT_SPLIT_CANONICALISES=false` reverses it.
  Narrowing the short-circuit so a PASSED-THROUGH slot does not skip Tier 3 would recover
  both, but the short-circuit is load-bearing for spend and that is its own gate. Row id:
  13333471.
* **adam/ada — the accepted counterpart to steinen, and the table now brackets the rule
  from both sides.** Same edit distance, opposite failures, both from a one-letter
  difference crossing a company boundary:

  | | record | other name | test | outcome |
  |---|---|---|---|---|
  | **steinen** | `William Steiner Manufacturing` | page states `STEINEN` | `_is_exact` / verbatim cover | one letter **REFUSED a right domain** |
  | **adam/ada** | `Adam Technologies` (Union NJ) | GLEIF `Ada Technologies Inc.` (Lyndhurst OH) | `_covers`, prefix-at-any-length | one letter **ACCEPTED a wrong LEI** |

  `utils/name_identity.py:206` treats a prefix relation at ANY length as one word, so
  `'adam'.startswith('ada')` made `classify_name_change` return `same`; GLEIF's fuzzy tier
  scored 97.0; `registry-location-mismatch` (OH vs NJ) fired but is one of the two codes in
  `ADVISORY_CODES`, so the row was never queued. A different company, `gleif:verified`,
  with `name1` not even in `flagged_fields` — while the record's own correct domain
  (`adam-tech.com`) carried the only name-shaped doubt on the row.

  A distinctiveness-aware cover test has to explain BOTH: `steinen`/`steiner` should meet
  and `ada`/`adam` should not, and neither string similarity nor prefix length separates
  them on its own. The other rows of the table are 13333920 (`heart`), thinksrs,
  darylflood, ucsd, fisher.

* **Registry two-disagreement refusal — landed and gated.** A GLEIF/ROR match that is
  neither separator-fold exact against the record's supplied name NOR registered in the
  record's region no longer writes the name or the identifier: the input stands, the match
  becomes a `Suggested Name`, and the derived low flags `name1`. ONE disagreement is
  untouched — an exact-name match whose region differs is a company that moved states (the
  advisory's legitimate case), and a near-miss name whose region agrees is a spelling
  variant. A CITY difference is not a region difference (Houston/Baytown).

  Gate: 8 rows, 0 allow-list violations, **every removal verified against live ROR/GLEIF
  and every one a different organisation in a different state** — Rutgers NJ vs SUNY
  Albany NY; Largo FL vs Hilo HI; ValleyCare CA vs Valley Medical Center Renton WA; North
  Austin TX vs North Canyon Gooding ID; CMC Steel TX vs Cincinnati Museum Center OH; WS
  Tyler MI vs Tyler Junior College TX; AWS-CQC CA vs American Welding Society Miami FL;
  Adam NJ vs Ada Lyndhurst OH. Zero same-company relocations, so the exactness arm does
  not need loosening.

  The gate also caught a defect in the rule's first cut: it reverted the NAME on three rows
  where the registry had never written it (`llm:provisional` — "North Austin Medical
  Center", "W.S. Tyler", "AWS"), throwing away a good expansion to punish a bad
  identifier. The rule refuses an ACCEPTANCE, so the name revert is now scoped to
  registry-written names; the identifier still goes either way, because it is the same
  wrong match.

---

## A late refusal must re-derive every downstream read of the state it reverses

Three instances now, all the same shape and all found the same way — by gating and reading
every changed row:

1. **`name1_changed`** is computed at `finalise`'s line 2792; the registry refusal runs at
   2872. Left stale it still reported the registry's rewrite, and `_still_as_supplied` —
   the filter that decides whether the derived low may speak — read it and stayed silent.
   The name was restored and silently UNFLAGGED.
2. **The `fix2:unchanged-verified` domain rung** upgraded Name 1 to `input:verified+web` on
   a domain whose page read had already recorded `name_mismatch`. The witness disagreed and
   the upgrade stood anyway.
3. **The registry cascade**: withdrawing an identifier left `domain` at `ror:verified` and
   `record_type` at `classifier:ror` — the match's consequences asserted on the authority
   of a match the record had deleted.

The general rule: **a refusal that lands late must re-derive every downstream read of the
state it reverses.** In practice that means asking, for each one: what was computed from
this before I changed it, and does it run again after me? Where it does not, re-derive it
explicitly; where it does, clear its INPUTS and let the existing machinery answer — never
write the answer yourself.

The cascade reads the log rather than a field list, for the same reason the origin
invariant lives at the funnel: a hand-written list of dependents goes stale the first time
a lane learns to write something new, and the log already knows.

* **The refused registry name is not retained when the gate refuses it before the write.**
  §2 asks that a withdrawn match always populate `Suggested Name`. It does on 6 of the 8
  gated rows. On **13334925** (ROR: North Canyon Medical Center) and **13338211** (ROR:
  Tyler Junior College) it cannot: the name gate refused the registry's name BEFORE any
  write, so there is no registry `name1` event, no rejection recorded, and by `finalise`
  the registry response is gone — the string exists nowhere on the record. Closing it means
  carrying the matched name forward from Tier 1 as a transient (`_registry_matched_name`),
  which is new plumbing through the registry lanes and wants its own gate. Row ids:
  13334925, 13338211.

* **Sibling-aware anchor — Moores and Texas Heart Institute bracket the design.** A
  registry match into a department slot now needs an anchor: the record's `ror_id` among
  the matched unit's published parents, or — with no identifier to hang it on —
  separator-fold exactness plus a location the registry does not contradict. Two rows sit
  either side of what the parent graph can prove:

  | row | matched unit | its parents | record | truth |
  |---|---|---|---|---|
  | **13348118** | Moores Cancer Center `01qkmtm61` | UC San Diego Health System `01kbfgm16` | UC San Diego `0168r3w48` | **true child, unprovable** |
  | **13146053** | Texas Heart Institute `00r4vsg44` | *none* (`related` → Texas Medical Center) | Baylor College of Medicine `02pttbw34` | **false child, undisprovable** |

  ROR publishes no edge between `01kbfgm16` and `0168r3w48` — the health system record
  carries no parent at all, and UCSD lists its medical centre only as `related` — so
  Moores dead-ends upward exactly as THI does. Both are fold-exact, both have agreeing
  locations, both refuse. **Only real-world facts separate them, so no mechanical
  relaxation that saves Moores excludes Texas Heart Institute.** Moores is the accepted
  cost: it loses `ror:verified` and its unit identifier and ships `input:low`, with the
  value itself unchanged.

* **13364434 — the lab sits above the department.** The absorb fix restores
  "Department of Biological Sciences", which the stale-incumbent drop had deleted, but it
  lands in Name 3 under Name 2's "Greenberg Laboratory" rather than above it. Unit
  ordering territory (`dept_block`'s rank step orders divisions above departments and does
  not rank a lab against one); out of scope for the anchor/absorb package. Row id:
  13364434.

* **13213468 — "Department of Philosophy" is the record's own words, and the absorb was
  deleting them.** Reported first as an LLM invention that the absorb fix had let escape
  unflagged. It is not: the record supplies `Dept of  Philosophy` in **Name 4**, and the
  shipped value is that string expanded — verdict `same`, correctly unflagged. The
  misreading came from the absorb's own log line, whose `supplied` field names the input
  of the slot being TESTED (Name 3's "Social Sciences") while `dropped` names the value
  being deleted, which had been packed up from Name 4. Two different slots' words in one
  event, and the log reads as though they were one. The row is a restore, in the same
  class as 13364434, not an invention.

  What this cost: a "doubt follows the value" change was built on the misreading — Tier 2
  dept writes recording a verdict, verdicts travelling with values through
  `dept_block.normalise`, and a missing-verdict fallback in the flag layer. It was
  reverted. Worth noting for whoever revisits it: it moved **49 rows** on the four
  workbooks and drove S1's frozen misses from 3 to 82, because refusing and re-attributing
  dept slots changes the downstream query path enough to leave the warm cache. **It is not
  gateable against this cache** and needs its own warming run. Row id: 13213468.
