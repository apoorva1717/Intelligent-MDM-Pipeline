# Provenance Scheme B — migration report

**Scheme:** `source:confidence[+witness]`, replacing `producer:tier:method`
**Batch:** `docs/thesis/chemspeed_us_100.xlsx` (100 records), against the frozen evidence cache
**Artefacts:** `logs/provmig/` — `pre1.json` (Scheme A), `post1.json` / `post2.json` (Scheme B),
`invariance.json`, `gate_pre.json`, `gate_post.json`
**Canonical documentation:** README § "The provenance grammar — Scheme B"; parameter reference
`docs/thesis/04_PARAMETERS.md` §1.20; machine-readable source `enrichment/confidence.py`

---

## 1 · Precondition

The migration was gated on the determinism work being in place and the double-run reproducibility
test passing. Re-measured before any code was touched, not taken from the earlier report:

```bash
python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx --out pre1.xlsx --json pre1.json --frozen
python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx --out pre2.xlsx --json pre2.json --frozen
python tools/run_diff.py logs/provmig/pre1.json logs/provmig/pre2.json
```

> rows compared 100 · **rows differing 0** · cell differences 0 · **run 2 network calls 0**

The same gate re-run after the migration gives the same result, so the migration did not cost the
property it was gated on.

**One caveat on "merged".** The determinism fixes are present in the working tree but are **not
committed** — `HEAD` is `75cfcad`, and `git status` shows 38 modified files and the determinism
report itself untracked. The substantive precondition (the gate passes) holds; the literal one
(the work is merged) does not, and this migration is stacked on top of that uncommitted work.

**One correction of record.** The brief and the determinism report both call this a 101-row batch.
The file holds **100** records and every count in this document is out of 100.

---

## 2 · The mapping actually applied

Every distinct Scheme A string the batch produced, with the row count that carried it. Domains
are collapsed to `{domain}` and extraction dates to `{date}`; the emitted strings carry the real
values. **300 provenance cells migrated across 100 records.**

| Column | Scheme A | Scheme B | Rows |
|---|---|---|---|
| `Name 1 Provenance` | `input:1:verified` | `input:verified+web` | 25 |
| `Name 1 Provenance` | `input:1:rule` | `input:low` | 20 |
| `Name 1 Provenance` | `gleif:1:exact` | `gleif:verified` | 18 |
| `Name 1 Provenance` | `llm_tier3:3:self_medium` | `llm:provisional` | 14 |
| `Name 1 Provenance` | `llm_company_canonical:2:self_high` | `llm:provisional` | 8 |
| `Name 1 Provenance` | `input:1:confirmed` | `input:provisional+llm` | 7 |
| `Name 1 Provenance` | `ror:1:exact` | `ror:verified` | 7 |
| `Name 1 Provenance` | `llm_tier3:3:self_high` | `llm:provisional` | 1 |
| `Domain Provenance` | `ror:1:exact` | `ror:verified` | 11 |
| `Domain Provenance` | `website_resolver:3:rule` | `web:{domain}:provisional` | 11 |
| `Domain Provenance` | `website_resolver:1:rule` | `web:{domain}:provisional` | 7 |
| `Domain Provenance` | `website_resolver:3:exact` | `web:{domain}:provisional` | 7 |
| `Domain Provenance` | `website_resolver:3:high` | `web:{domain}:provisional` | 7 |
| `Domain Provenance` | `website_resolver:2:rule` | `web:{domain}:provisional` | 4 |
| `Domain Provenance` | `website_resolver:3:medium` | `web:{domain}:provisional` | 4 |
| `Domain Provenance` | `website_resolver:1:medium` | `web:{domain}:provisional` | 3 |
| `Record Type Provenance` | `classifier:-:rule` | `input:low` | 72 |
| `Record Type Provenance` | `classifier:-:rule` | `gleif:verified` | 14 |
| `Record Type Provenance` | `classifier:-:rule` | `ror:verified` | 11 |
| `Record Type Provenance` | `classifier:-:rule` | `input:provisional` | 3 |
| `ROR ID Provenance` | `ror:1:exact` | `ror:verified` | 11 |
| `LEI ID Provenance` | `gleif:1:exact` | `gleif:verified` | 18 |
| `Operating Name Provenance` | `web:{domain}:extracted:{date}` | `web:{domain}:provisional` | 15 |
| `Operating Name Provenance` | `wikidata:2:crosswalk` | `wikidata:provisional` | 2 |

`Name 2 Provenance` is null on all 100 records of this batch — no record reached a Tier 2A/2B
department write — so the column migrated no cells. Its mapping is exercised by fixture instead
(`llm:provisional`).

### Three rows of that table are worth reading twice

**`website_resolver:*:*` → one string.** Seven distinct Scheme A strings across three tiers and
four bands collapse to `web:{domain}:provisional`. The brief anticipated eight variants; this
batch produced seven (`website_resolver:2:medium` is reachable but unhit). The collapse is the
substantive content of hard rule 4, not a simplification: `website_resolver:3:exact` read as the
strongest domain attribution the pipeline could make, and what it actually recorded was a
`token_sort_ratio` of 100 between the record's own Name 1 and the candidate host — one source
comparing the input to itself. `:serp` is worse: the "evidence" is the candidate site's own page,
which is the same evidence system as the domain it is being asked to vouch for.

**`classifier:-:rule` → four different strings.** One Scheme A string hid four distinct evidence
situations. A record type ROR settled and a record type nothing settled shipped byte-identical
provenance. The classifier already recorded `decided_by` on the event; the migration promotes it
to the column. 72 of 100 records had **no source at all** for their record type, which was
invisible before.

**`input:1:verified` → `input:verified+web`.** The old string asserted `verified` and left the
witness unrecorded, which is exactly the shape hard rule 2 now forbids for a non-registry value.
The witness is read from what `unchanged_state` already recorded (`domain:name`, `domain:serp`,
`page:{domain}`, `wikidata:{qid}`) and rendered as `+web` / `+wikidata` / `+registry`.

---

## 3 · Strings not in the supplied state table

Two, both surfaced rather than silently mapped.

### 3.1 · `wikidata:2:crosswalk` — mapped, on your confirmation

**Column:** `Operating Name Provenance`. **Rows:** 2.
**Applied mapping:** `wikidata:provisional`.

Not in the brief's state table. It is not an invented mapping either: `enrichment/wikidata.py`
already carried a `TODO(provenance-migration)` naming `wikidata:provisional` as this constant's
target, written when the token was introduced as a deliberate placeholder ("the `2` is the slot's
filler, not an assertion that Tier 2 ran"). You confirmed applying it. `provisional` and never
`verified` is forced by the path itself: the witness write happens precisely when the crosswalk
found **no** registry pointer to follow, so there is no second evidence system agreeing.

### 3.2 · `batch_consensus:-:inherited` — mapped, **needs your confirmation**

**Columns:** any scoped field. **Rows in this batch: 0** (`consensus_fields_propagated: {}`).
**Applied mapping:** `ror:provisional` for `ror_id`, `gleif:provisional` for `lei_id`,
`input:provisional` for everything else.

Not in the state table, and unlike §3.1 there is no in-repo decision to fall back on. Scheme B's
`source` vocabulary has no `batch_consensus` and its `confidence` vocabulary has no `inherited`,
so the old string cannot survive in any form: the grammar names *who said it* and *how much
weight it carries*, and "a sibling record in this batch" is neither.

The reasoning behind what was applied: a registry authored the identifier, so the source is that
registry — but **this** record never looked it up, so it is `provisional` here and `verified` only
on the donor. The donor's record id stays on the provenance event, which is what a reviewer
opens. Pinned by `tests/test_provenance.py::TestEarlierFixes::test_fix6_inheritance_names_the_donor_record`.

**This is the one mapping in the migration resting on my judgement rather than on your table or
the repo's.** It affected zero rows of the reference batch, so nothing measured here depends on
it — but a batch with duplicate customers will hit it.

---

## 4 · Behaviour invariance — the core gate

```bash
python tools/provenance_invariance.py logs/provmig/pre1.json logs/provmig/post1.json
```

| Measure | Result |
|---|---|
| Rows compared | 100 |
| Value columns compared | 56 (every `RESPONSE_COLUMNS` entry that is not provenance, flag or event log) |
| **Value differences** | **0** |
| Provenance cells migrated | 300 |
| **Rows changing `Flag for Review`** | **0** |
| **Rows changing `Flag Reason`** | **0** — byte-identical |
| Rows changing `Flagged Fields` | 0 |
| Double-run reproducibility, post-migration | 0 differing rows, 0 network calls on run 2 |

Names, domains, identifiers, record types, operating names, addresses, search terms, statuses and
tiers are byte-identical across all 100 records. No resolution decision, guard, threshold or
acceptance behaviour moved.

### Rows whose flag status changed: none

The brief expected rows previously flagged only by `low-confidence-unchanged` mechanics to remain
flagged via `input:low`. Measured, the correspondence is exact rather than approximate:

- rows where the derived low fires and the code did not: **0**
- rows where the code fired and the derived low does not: **0**
- flagged rows before **55**, after **55**

The three guards the old rule needed — skip if registry-named, skip if already
`unverified-inference`, skip if the field is empty — are *subsumed* rather than reimplemented,
which is the evidence that this is the same decision and not a lookalike. A field a registry wrote
is `ror:verified`; a field Tier 3 wrote is `llm:provisional`; neither can be `low`, so neither
needs excluding.

### One regression the gate caught, and the fix

Retiring the code silently broke `no-match`. Its rule is "raise only when no other code fired",
and `low-confidence-unchanged` used to be one of those codes. With the code gone, `codes` was
empty on rows that still had a real doubt, and `no-match` took its place — **4 → 15 rows**,
promoting eleven records from *"confirm this value is correct"* to *"no source could identify
this organisation"*. Those are not interchangeable statements: the pipeline had established that
the record's own value stands. The guard moved with the code (`flags.py`, `not codes and not
low_confidence and _nothing_was_enriched`). This is measured, not hypothesised — it is why the
invariance run is worth doing on a real batch rather than on fixtures.

---

## 5 · Flag taxonomy

`Flag for Review` is now **derived** and no longer equals `bool(flag_codes)`:

```
flagged := any(core field confidence == "low")  OR  any code present
core fields := Name 1, Name 2
```

**Core fields are Name 1 and Name 2 only, per your instruction** — this deviates from the brief,
which named Name 1, Domain and Record Type. The measured consequence of the brief's list was the
reason for asking: `record_type` is `input:low` on 72 of 100 records, and including it would have
taken the batch from **55 flagged rows to 96**, moving 41 currently-unflagged records into the
review queue. `Domain` and `Record Type` still carry and export their confidence; they do not
raise the flag.

**`low-confidence-unchanged` is retired.** It is out of `ALL_CODES`, cannot appear in
`flag_codes`, and `flags.render` **raises** if a caller still passes it rather than silently
discarding a real doubt. Its reason text survives, rendered in the position its slot in
`_CODE_ORDER` still holds — which is why `Flag Reason` is byte-identical on all 100 rows.

**One deviation you should know about.** The brief says the code "is now exactly `input:low` on
Name 1". On this batch that is true. Structurally it is not: `_ev_low_conf_unchanged` is also set
for the **department slots**, and `Name 3`–`Name 5` are outside Fix 10's Phase 1 provenance scope,
so they carry no confidence and a purely provenance-derived rule would have dropped their doubt
silently. This batch would not have caught it — all twenty of its low-confidence rows are Name 1.
So the derived low is the **union** of what provenance says for the fields provenance covers and
what the marker says for the fields it does not yet reach. When `Name 3`–`Name 5` enter provenance
scope that half deletes itself and nothing else changes.

`no-match` behaviour is **unchanged**, per the brief's instruction for the undecided case. The
determinism report records its fate as still open ("Its fate is yours", §7) and there is no
later commit deciding it.

Batch consensus withdraws the derived low by **re-derivation**: the propagated write goes through
`EnrichmentResult.write`, which regenerates the field's provenance, so a record that was
`input:low` because its own value stood stops being `input:low` once a donor's value replaces it.
`low-confidence-unchanged` is therefore gone from `_RETRACTED_BY_NAME1` in both modes.

---

## 6 · Compatibility surfaces — checked, not assumed

### 6.1 · Everything that parses provenance

| Site | Was | Now |
|---|---|---|
| `scripts/fix_reports.py::_state` | `scalar.rsplit(":", 1)[-1]` against a band→state map | `confidence.parse()`, keyed on the `(confidence, witness)` pair. An old-grammar artefact now returns "cannot say" rather than a wrong answer |
| `enrichment/orchestrator.py::_emit_retry_trace` | `(name1_provenance).split(":")[0]` | `confidence.parse()`. The naive split happened to give the same answer here — which is exactly why it was worth removing while still harmless |
| `tools/run_diff.py` | compared provenance columns like any other | detects the scheme and **refuses** a cross-scheme comparison (exit 2), pointing at `tools/provenance_invariance.py` |
| `enrichment/wikidata.py::WITNESS_PROVENANCE` | literal `"wikidata:2:crosswalk"` | `render(SOURCE_WIKIDATA, PROVISIONAL)` — composed and validated, not spelled |
| `enrichment/page_corroborator.py::operating_name_provenance` | `web:{domain}:extracted:{date}` | `web:{domain}:provisional`; the `when` parameter is gone from the signature |
| README, `docs/thesis/04_PARAMETERS.md`, `docs/thesis/12_RATIONALE.md` | quoted Scheme A throughout | updated; changelog entries annotated rather than rewritten |

`web:{domain}:provisional` contains two colons. Every consumer in the repository now goes through
`confidence.parse()`; the naive `split(":")` puts the domain in the confidence slot.

### 6.1a · The extraction date reached a trace, verified rather than assumed

The date left `Operating Name Provenance` on the condition that it survives elsewhere. It does,
in two places: on the immutable cache entry it was always read from (`PageCache.fetched_at`), and
on a new `operating_name_extracted` trace line.

That second one was wrong on the first attempt and the check caught it. The line was emitted on
the orchestrator's module logger, and a batch run attaches its capture handler **by name** to the
`enrichment.trace.*` loggers only (`scripts/run_batch.py:113-124`) — so it reached no trace file
and the date would have been deleted from the export path into nothing. Moved to
`enrichment.trace.page`, and verified on the artefact rather than in principle:

```
$ grep -c operating_name_extracted logs/provmig/post1.jsonl
15
{"step": "operating_name_extracted", "domain": "20visioneers15.com",
 "fetched_at": "2026-08-22", "stated_org_name": "20/15 Visioneers",
 "provenance": "web:20visioneers15.com:provisional"}
```

15 trace lines for the 15 rows that lost a date from the column. The match mode and tier the six
scoped columns dropped needed no such move — they were already on the shipped provenance event
(`rule_id: "tier1-lei:fuzzy"`, `confidence_scale`, `confidence_value`, `tier`), which is strictly
more than the `:1:exact` slot ever carried.

### 6.2 · DATAshaper writeback — for Bert

**No stored procedure in this repository reads a provenance column.** Checked, not assumed:

- `sql/usp_merge_legacy_enriched.sql` — its `OPENJSON … WITH` block enumerates 33 columns
  explicitly. `Name 1`, `Domain`, `Record Type`, `ROR ID`, `LEI ID`, `Flag for Review`,
  `Flag Reason` are in it; **no `* Provenance` column is**, nor `Operating Name`.
- `sql/usp_merge_validation_clusters.sql` — Phase 2 clustering only.
- `sql/usp_merge_validation_scores.sql` — Phase 2 scoring only.

Nothing pattern-matches provenance content, so nothing in the merge path can break on the new
grammar. **Two columns need Bert's confirmation anyway, and neither is a provenance column:**

| Column | Type in the proc | Why it needs confirming |
|---|---|---|
| `Flag for Review` | `BIT` | Its **derivation** changed. On this batch the value is identical on all 100 rows, so no writeback changes — but the contract a consumer might rely on (`Flag for Review` ⇔ `Flag Codes` non-empty) no longer holds. Anything downstream that reconstructs the boolean from the codes, rather than reading it, will now under-flag |
| `Flag Reason` | `NVARCHAR(500)` | Byte-identical on this batch, so no new truncation risk from the migration. Flagged only because it is the column the retired code's prose now reaches by a different route |

Open from the earlier Fix 10 work and **still open**: whether DATAshaper's column-typed validation
model accepts the widened column set at all. The migration does not change the column count.

---

## 7 · Tests

`tests/test_provenance_scheme_b.py` — **70 tests**, in four groups:

1. `compute_confidence`, every row of the table and both hard rules, plus an exhaustive sweep of
   the situation flag space asserting no reachable combination renders an invalid string.
2. The grammar: every shape the pipeline emits parses; every Scheme A shape is rejected; the
   two-colon `web:` trap is pinned explicitly.
3. Fourteen per-state fixtures asserting exact output — registry hit, fuzzy registry hit,
   crosswalked hit, input+web verified, input+llm provisional, input low, llm provisional, domain
   provisional by own-page corroboration (asserted **not** `verified`), domain provisional by name
   similarity, domain verified by email witness, domain from a registry-stated website, record
   type from GLEIF, record type unset, record type from a keyword, operating name, Wikidata
   witness.
4. Behaviour invariance across the 100-record batch, plus the retired code's disappearance from
   the vocabulary and the survival of its prose.

**Full suite: `2083 passed, 5 failed`.** All 5 failures pre-date this work and are the same five
the determinism report records as pre-existing, verified there by stashing to `HEAD`:

| Test | Status |
|---|---|
| `test_orchestrator.py::test_tier1_full_resolution` | pre-existing (`assert 'medium' == 'high'`) |
| `test_orchestrator.py::test_web_search_fallback_for_name1` | pre-existing |
| `test_orchestrator.py::test_web_search_determines_record_type` | pre-existing |
| `test_name_slot_parity.py::test_department_in_a_lower_slot_is_not_reported_missing` | pre-existing |
| `test_routes.py::test_issues_compare_segments_g6_and_g7_out_of_the_metric` | pre-existing |

38 existing tests were edited, every one because it pinned a representation the migration
replaced. One change is worth naming because it is a change of *meaning*, not of spelling:
`tests/conftest.py::fixture_evidence` recorded a bare deterministic write, which under Scheme B
renders `input:low` — so every record built by `make_record` would have arrived at `compute_flags`
with its Name 1 in doubt, and a dozen tests about overflow, contacts and department scope would
have started reporting `name1` in `flagged_fields`. It now records `input_corroborated`, because
that is what a fixture value means: the field is settled and the test is about something else.

---

## 8 · One paragraph, thesis-ready

The pipeline's exported provenance was `producer:tier:method` — `ror:1:exact`,
`llm_tier3:3:self_medium`, `website_resolver:3:rule`, `web:acme.com:extracted:2026-08-22` — and
every one of its three slots exported a mechanism where a reader needs a claim: the tier is the
route a value took rather than a warrant for it, so one registry match read as two depending on
which lane reached it; the method token was not comparable across producers, since `exact` means
"an identifier was returned" for a registry and "at least 99.5 on a fuzzy ratio" for a string
comparison; `self_high` leaked a model's assessment of its own output into a slot that sorts and
therefore reads as authority; and the extraction date decayed inside a field consumers treat as
part of the claim. Scheme B replaces all of it with `source:confidence[+witness]` over a closed
three-value confidence vocabulary computed by a single function from a four-row table, so that
the column means the same thing in every row of it and no lane can assign its own confidence ad
hoc. Four rules are enforced rather than documented: a model can never produce or contribute to
`verified`, because a confident unverifiable claim is the more dangerous case and not the safer
one; a witness-less `verified` is a registry's alone, which makes the rule checkable from the
string without knowing which lane wrote it; rejected evidence never appears in provenance,
enforced by testing contradiction *above* the registry row so that a refused registry hit cannot
report itself as verified; and "independent" means a different evidence system, so a page fetched
from the domain it corroborates counts once — which collapsed seven spellings of an accepted
domain, including the one that read `exact`, into a single `provisional`. The migration's value
lies in what it revealed rather than in what it renamed: one Scheme A string, `classifier:-:rule`,
covered four distinct evidence situations, and promoting the classifier's already-recorded
`decided_by` into the column showed that **72 of 100 records had no source at all** for their
record type, a fact the previous representation was structurally incapable of expressing. The
migration is a representation change and is held to that by a gate rather than by intent: run the
100-record batch against the same frozen evidence before and after, and every one of the 56
non-provenance columns is byte-identical, 300 provenance cells move, no row changes its flag
status, and `Flag Reason` is byte-identical on all 100 rows. That gate paid for itself
immediately — retiring the `low-confidence-unchanged` code silently broke the unrelated
`no-match` rule, whose "only when nothing else fired" guard had been counting on it, promoting
eleven records from *"confirm this value is correct"* to *"no source could identify this
organisation"* before the diff caught it.
