# Calibration findings — domain witnesses, ROR trigger parity, cross-source name normalisation

Final calibration before evaluation code freeze. Three fixes, each expressed as a rule of an
existing comparator or evidence path. No threshold moved. No flag code was added or removed —
the vocabulary stays at 14.

---

## Thesis-ready summary

Three defects shared one cause: a comparison was being made with less evidence than the pipeline
already held. The domain-ownership guard refused candidates it could tie to nothing while a
registry record and a Wikidata item on the same record both stated the organisation's official
website, and while the page corroborator had already fetched and read the candidate's own site;
the location-check trigger that distinguishes "the register holds an incorporation address" from
"this may be the wrong organisation" was stated once but implemented per-lane, so ROR compared one
of the locations it publishes, GLEIF classified the name against one of the names it publishes,
and the Wikidata crosswalk — the only route that identifies an organisation without reading its
name — compared its address and told nobody; and the cross-source gate compared GLEIF's *formal
legal name* against ROR's *brand* with a length-sensitive ratio, so a same-entity agreement scored
in the fifties and was handled as a contradiction, deleting a correct `ror_id`, the domain it
supplied and the acronym `search_term_1` was derived from. The fixes are, correspondingly, three
rules: an independent system's stated official website whose registrable domain *is* the candidate
verifies it (`web:{domain}:verified+registry` / `+wikidata` — two provenance states the grammar
had defined and no code path produced), and the candidate's own page accepts it at `provisional`;
two names agree when one's distinctive token set is a subset of the other's *and* retains the
longer name's leading token, which admits brand-versus-brand-plus-division while still refusing a
different company whose name is a suffix of the record's; and one function decides the location
trigger for every registry lane, fed by every address and every name variant each registry
publishes. On the 99-row S2 evaluation stratum, both runs warm-cache with zero network calls, flag
instances fell from 46 to 35 and records carrying a domain rose from 65 to 77, while
`name1_enriched` was byte-identical on all 99 rows — the fixes add evidence to decisions the
pipeline was already taking, and change no value it had authored.

---

## 1 · What changed, where

### Fix 1 — independent witnesses can verify a candidate domain

| Where | Change |
|---|---|
| `enrichment/registry_match.py` | **New:** `distinctive_tokens`, `names_agree_by_containment`, `names_agree`. The one answer to "do these two strings name one organisation", used by the page reader and the cross-source gate. |
| `utils/domain_resolver.py` | `DomainEvidence` gains `stated_websites` and `page_identity`; `DomainDecision` gains `witness`. **New:** `stated_website_witness`. `resolve_domain` gains conditions **2** (witness) and **6** (page identity); precedence is registry → witness → name → email → serp → page. |
| `enrichment/provenance.py` | `_DOMAIN_WITNESSES` gains `witness_registry` → `+registry` and `witness_wikidata` → `+wikidata`. `page`, `serp` and `name` stay absent — hard rule 4. |
| `enrichment/page_corroborator.py` | `compare` uses `names_agree`. **New:** `ACTIONABLE_LOCATION_SCOPES`, `location_decides`, `page_identifies_record` — one granularity floor, read by both the accept and the withdraw site. |
| `enrichment/consistency.py` | `record_registry_identity` also retains the registry's stated website (`_src_stated_websites`). |
| `enrichment/orchestrator.py` | **New:** `_domain_witnesses` (gathers the claims), `_retain_wikidata_website` (the corroboration-only pass). `_apply_domain` gains `page_identity`. `_corroborate_domain` accepts a corroborated candidate through the single write path. `_corroborate_domain_from_wikidata` reads `_wikidata_website` instead of popping it. |
| `config.py` | **New:** `WIKIDATA_DOMAIN_CORROBORATION` (default on). |
| `api/models.py` | **New:** `domain_from_witness`, `domain_from_page`, `page_domains_accepted`, `wikidata_corroboration_queried`, `wikidata_corroboration_matched`. |

### Fix 2 — location-check trigger parity for every registry lane

| Where | Change |
|---|---|
| `enrichment/registry_match.py` | **New:** `location_check_action(verdict, tier)` and the three `LOCATION_ACTION_*` constants. The rule, once. |
| `enrichment/consistency.py` | `apply_registry_location_check` calls it instead of deciding inline. |
| `enrichment/tier1_ror.py` | `_extract_org_fields` builds an `addresses` list from **every** `locations[]` entry (de-duplicated); `_locality` and `call_ror_by_id` compare that set. The flat `city`/`region`/`country` keys still name the primary location. |
| `enrichment/tier1_lei.py` | `_record_fields` exposes `entity_names` (`legalName` + `otherNames` + `transliteratedOtherNames`); the tier is classified against all of them. |
| `enrichment/orchestrator.py` | `_crosswalk_to_ror` and `_crosswalk_to_gleif` call `record_registry_identity`, so the crosswalk lane's locality verdict reaches the gate. |

### Fix 3 — cross-source conflict compares normalised entities

| Where | Change |
|---|---|
| `enrichment/consistency.py` | `_agrees` uses `names_agree`. **New:** `_record_agreement`, `registry_agreement_count`, `_ev_registry_agreement`, a `source_agreement` trace line. `reset_consistency_counters` zeroes the new counter. |
| `enrichment/flags.py` | `_ev_registry_agreement` added to `_EVIDENCE_KEYS` so it is dropped before validation. It raises nothing. |
| `api/models.py` / `enrichment/orchestrator.py` | **New:** `registry_agreement` batch counter. |

### Documentation

`README.md` — ownership guard §2b (six conditions) and new §2c (where the witness claims come
from); Stage 5b (outcome table, the shared granularity floor, a new "Containment" subsection);
Stage 2c (new "The corroboration-only pass"); Fix D (containment in the gate, the agreement
outcome, the shared trigger, ROR's full address set, GLEIF's full name set); env table; module
reference for `registry_match`, `page_corroborator`, `consistency`, `domain_resolver`; TOC; test
commands; changelog. `docs/thesis/04_PARAMETERS.md` — new §1.6a (the six ownership conditions as a
table), three new rows in §1.17 (containment, the leading-token condition, the acceptance floor),
`WIKIDATA_DOMAIN_CORROBORATION` in §1.18.1, four new/rewritten rows in §1.19 (the shared trigger
and its two inputs, the crosswalk recording, the cross-source comparison).

---

## 2 · Per-file flag deltas

### S2 evaluation stratum — `eval-largecompanies.xlsx`, 99 records

Both runs warm-cache, `evidence_network_calls: 0`, 651 / 727 cache hits. The **before** run was
executed from a git worktree at the exact pre-calibration working tree (stash commit `8827814`),
so the only variable between the two is this calibration.

| Flag code | Before | After | Δ |
|---|---:|---:|---:|
| `domain-unverified` | 30 | 22 | **−8** |
| `registry-location-mismatch` | 6 | 7 | **+1** |
| `source-conflict` | 4 | 0 | **−4** |
| `unverified-inference` | 5 | 5 | 0 |
| `entity-superseded` | 1 | 1 | 0 |
| **Total flag instances** | **46** | **35** | **−11** |
| Records carrying ≥1 flag | 41 | 31 | −10 |

| Field-level | Before | After | Δ |
|---|---:|---:|---:|
| Records with a `domain` | 65 | 77 | +12 |
| Records with a `ror_id` | 49 | 53 | +4 |
| Records with a `lei_id` | 51 | 51 | 0 |
| Records with an `operating_name` | 1 | 9 | +8 |
| `name1_enriched` identical | — | **99 / 99** | — |
| `name2_enriched` identical | — | **99 / 99** | — |
| `record_type` identical | — | **99 / 99** | — |

| Batch counter | Before | After |
|---|---:|---:|
| `domain_from_witness` | — | 1 |
| `domain_from_page` | — | 7 |
| `page_domains_accepted` | — | 7 |
| `page_corroborated` | 0 | 7 |
| `page_contradicted` | 0 | 2 |
| `page_name_mismatch` | 13 | 4 |
| `page_domains_withdrawn` | 0 | 0 |
| `registry_location_unconfirmed` | 37 | 37 |
| `registry_agreement` | — | 29 |
| `wikidata_corroboration_queried` | — | 22 |
| `wikidata_corroboration_matched` | — | 3 |
| `unchanged_verified` | 3 | 7 |
| `verified` (enrichment status) | 6 | 9 |
| `unresolved` | 23 | 20 |

**`tools/run_diff.py`: 15 rows differ of 99.** The columns that ever differ are exactly: Domain,
Domain Provenance, ROR ID, ROR ID Provenance, Name 1 Provenance, Operating Name, Operating Name
Provenance, Search Term 1, and the four flag columns. **Name 1, Name 2, Record Type, LEI ID,
Department Domain, Search Term 2, Enrichment Status and every address column are byte-identical
on all 99 rows.** "A page is a witness, never an author" holds across the whole file.

The 15th row (14 by field, 15 by `run_diff`) is Roche Sequencing Solutions, which changed only its
flag *reason* — see §4.

### 20-row iteration subset — `logs/s2_subset_20.xlsx`

Drawn from the S2 file and carrying every gate record. Used for all iteration; full-batch runs
were held to the end.

| Flag code | Before | After | Δ |
|---|---:|---:|---:|
| `domain-unverified` | 7 | 4 | −3 |
| `source-conflict` | 4 | 0 | −4 |
| `registry-location-mismatch` | 4 | 4 | 0 |
| `unverified-inference` | 2 | 2 | 0 |
| **Total** | **17** | **10** | **−7** |

The four `registry-location-mismatch` rows the subset carries — two Cargill and one Janssen
(gates 7 and 8), plus the Thermo Fisher row whose match is genuinely fuzzy — are unchanged, which
is what the subset was chosen to hold.

### Chemspeed 101-row batch — NOT RUN

**Stated plainly: the population-genericity check on the SMB stratum was not performed.** The
Chemspeed evidence cache is not in this repository. A frozen probe of the first 20 rows against
`tests/fixtures` (`CACHE_FROZEN=true`, no network) returned **144 cache misses against 39 hits** —
only the page-read and Wikidata fixtures are warm; the ROR, GLEIF, SerpAPI and LLM namespaces are
empty for that population. A "warm-cache" Chemspeed run as the brief specifies is therefore not
possible from this checkout, and a live one would be 101 records of paid SerpAPI and Azure OpenAI
calls on top of two runs needed for a before/after. Asked to choose, the author elected to skip it
and have the omission recorded here rather than spend the run.

**What that leaves unmeasured**, precisely: whether these rules cause flag or value changes on
small and mid-size businesses, which is the population where the containment rule has the least
head-room (an SMB name is often one distinctive token, so containment reduces to equality) and
where the witness path has the least to offer (few SMBs hold a ROR record or a Wikidata item).
The unit gates cover the *rules*; what is missing is the *distribution*. The Chemspeed cache is
also what the [reproducibility gate](README.md#the-reproducibility-gate) and
`tools/shuffle_evidence.py` are pinned against, so re-warming it is worth doing for more than this
report.

---

## 3 · Gates

All ten pass. Six are live rows of the S2 run; four are fixtures built from real cached evidence
in `tests/test_calibration.py` (67 tests).

| # | Gate | Result | Where |
|---|---|---|---|
| 1 | Johnson & Johnson + `jnj.com` + Wikidata `P856` | **domain accepted**, `web:jnj.com:verified+wikidata`, no `domain-unverified` | live, row 13017857 |
| 2 | Stryker Orthopaedics + page stating "Stryker" (location neutral) | name-consistent via containment → **accepted**, `web:stryker.com:provisional` | live, row 13032663 |
| 3 | KLA-Tencor Corp + page stating "KLA" in Milpitas | name-consistent; the city difference is inside an agreeing region → **accepted**, `web:kla.com:provisional` | live, row 13017654 |
| 4 | Owens Corning Sales LLC + `corning.com` | **still rejected**, `domain-unverified` — `corning.com` answers 403 (never evidence), and containment refuses `{corning} ⊂ {owens, corning, sales}` on the missing head token | live, row 13011066 + `TestContainmentComparator` |
| 5 | Kellogg plant + `battlecreekmich.com` (page states "Clara's Restaurant Group") | **still rejected**, `domain-unverified` — no shared distinctive token | live, row 13017665 + `TestContainmentInThePageReader` |
| 6 | Thermo Fisher Scientific, FL site, exact-tier ROR match, ROR address MA | **no flag**, trace `registry_location_unconfirmed` | fixture from the cached ROR record for `ror.org/03x1ewr52` (Waltham MA) |
| 7 | Cargill-Foundation-class (weak-tier ROR, cross-state) | **still flagged** `registry-location-mismatch` | live, rows 13056499 / 13057335 + fixture |
| 8 | Jansen-LLC-class (weak GLEIF, cross-state) | **still flagged** `registry-location-mismatch` | live, row 13018096 + fixture |
| 9 | Corteva: GLEIF "CORTEVA AGRISCIENCE LLC" + ROR "Corteva" | **no `source-conflict`**, both ids retained, name from GLEIF (`gleif:verified`) | live, rows 13034678 / 13036202 |
| 10 | Genuine conflict (GLEIF "BIC CORPORATION" vs ROR "Centene Corporation") | **still `source-conflict`**, ROR nulled, handling byte-for-byte unchanged | fixture, `TestCrossSourceNormalisedComparison` |

Gate 6 is a fixture rather than a live row because the S2 file's Thermo Fisher record
(13017576) states "Fisher Scientific Co. LLC" — genuinely *not* the registry's name, correctly
classified `fuzzy`, and it keeps its flag. The gate's exact-tier condition is reproduced against
the same cached ROR response with the record stating the registry's name.

---

## 4 · The rows that changed, and why

14 of 99 rows changed a field; 15 changed something `run_diff` compares.

**Twelve domains newly filled.** Four of them — Chemours, Corteva ×2, Abbott — are a consequence
of Fix 3 rather than of a new ownership condition: the ROR identifier was no longer deleted, so
ROR's own website was written with registry provenance (`ror:verified`). The other **eight are the
new conditions**, one by witness and seven by page identity:

| Row | Organisation | Domain | Accepted by | Provenance |
|---|---|---|---|---|
| 13017857 | Johnson & Johnson | `jnj.com` | witness (Wikidata `P856`) | `web:jnj.com:verified+wikidata` |
| 13017654 | Kla-Tencor Corp | `kla.com` | page identity | `web:kla.com:provisional` |
| 13032663 | Stryker Orthopaedics Corp | `stryker.com` | page identity | `web:stryker.com:provisional` |
| 13035908 | Novartis Inst for Biomedical Research | `novartis.com` | page identity | `web:novartis.com:provisional` |
| 13038540 | Novartis Pharmaceuticals Corp | `novartis.com` | page identity | `web:novartis.com:provisional` |
| 13046143 | Lg Chem Michigan Inc | `lgchem.com` | page identity | `web:lgchem.com:provisional` |
| 13047768 | Sanofi Vaccines US Inc | `sanofi.com` | page identity | `web:sanofi.com:provisional` |
| 13047922 | Halliburton Technology Partners LLC | `halliburton.com` | page identity | `web:halliburton.com:provisional` |

Every one is a brand-versus-brand-plus-division pair that the ratio scores below threshold and
containment resolves. Four of them also carry `name1_provenance` `input:low` →
`input:verified+web`: the page independently corroborated the retained Name 1, which is what
[`unchanged-verified`](README.md#the-three-unchanged-name-1-states) was built to record
(`unchanged_verified` 3 → 7).

**Four `source-conflict` flags withdrawn**, ROR's identifier and domain retained:

| Row | GLEIF says | ROR says | Kept |
|---|---|---|---|
| 13033343 | THE CHEMOURS COMPANY | Chemours | both ids, name `gleif:verified`, `chemours.com` |
| 13034678 | CORTEVA AGRISCIENCE LLC | Corteva | both ids, `corteva.us` |
| 13036202 | CORTEVA AGRISCIENCE LLC | Corteva | both ids, `corteva.us` |
| 13057088 | ABBOTT LABORATORIES | Abbott | both ids, `abbott.com` |

Each of these four also regained the ROR-supplied domain (`ror:verified`), which is where four of
the twelve newly-filled `domain` values come from. `search_term_1` on these four moves from the
full legal name to the registry acronym
(`THE CHEMOURS` → `CHEMOURS`) — a direct consequence of the ROR acronym no longer being deleted
along with the identifier, and the intended behaviour of the Search Term chain.

**One flag newly raised** — 13036140, Nestlé Health Science. The record is in Boca Raton FL; it was
resolved through the Wikidata crosswalk to a ROR entity registered in New Jersey. Before, the
crosswalk lane never recorded its locality verdict, so the check never ran on that lane at all;
now it does, `CROSSWALK_TIER` is below exact by construction (a pointer is not a name), and the
contradiction flags. This is the only new flag on the file and it is the Fix 2 gap closing.

**One flag reason rewritten** — 13037131, Roche Sequencing Solutions (Pleasanton, US) against
`roche.com`, whose page states Roche in Basel. Before: the ratio failed, so the outcome was
`name_mismatch` and the note read "its page states 'Roche' in Basel". After: containment makes the
name consistent, so the outcome is `contradicted` at **country** scope, `location_decides` is
true, and the domain is **not** accepted — `domain-unverified` stands, with the reason now naming
the contradiction. This is the Fix 1b invariant demonstrated live: containment fixes the name
comparison and a location contradiction still blocks.

**One `operating_name` gained with no other change** — 13037231, Google Quantum AI, whose already-
accepted domain's page names Google. The field was always this module's output; the containment
rule is what let the page's statement count.

---

## 5 · Genericity check

The requirement: no fix may be a special case for a named company, a hardcoded name list, a domain
allowlist, or logic keyed to anything in the evidence files.

**Grep over the added lines of the non-test, non-markdown diff** (852 lines), for every gate name
and domain — `johnson`, `jnj`, `stryker`, `kla-`, `tencor`, `corteva`, `chemours`, `abbott`,
`thermo`, `cargill`, `jansen`, `janssen`, `kellogg`, `owens`, `corning`, `battlecreek`,
`agriscience`:

```
16 hits. All 16 are in a docstring or a `#` comment. 0 are in executable code.
```

The hits are the derivations: the pair that motivated the leading-token condition, the pair that
motivated containment, the pair that motivated the legal-form alias fold. The modules already
document themselves this way throughout, and a rule whose derivation is unrecoverable is worse
than one that names the row it was measured on.

**The authoritative check is `tests/test_calibration.py::TestNoNamedRecordReachesNonTestCode`,**
which does not grep. It walks the AST of all eleven changed non-test modules and fails on a gate
name appearing in *any* string constant that is not a module/class/function docstring, or in any
identifier, attribute or definition name. Comments are absent from the AST and docstrings are
exempt by construction, so the test states the rule exactly: **a gate name may explain a rule; it
may never be a value the program computes with.** A companion test fails the walk on purpose
against a module that *does* name gates in computable positions (the gate test file itself), so
the check cannot pass by being broken.

Matching is anchored at word start rather than by substring, because `kla` is inside `Oklahoma`
and the US region map is full of place names.

Two further structural properties hold and are asserted:

* **The Wikidata lane is still a pure insert.** `WIKIDATA_ENABLED=false` leaves the pipeline
  byte-identical to a build without the lane. The pure-insert baseline in `test_wikidata.py` was
  extended to excise *both* entry points, since the lane gained one.
* **No new provenance state was invented.** `web:{domain}:verified+wikidata` and
  `+registry` were already in the Scheme B grammar with no producer; `page` is deliberately
  absent from `_DOMAIN_WITNESSES`, so the page-identity condition can never reach `verified`.

---

## 6 · Verification

**Unit tests — `tests/test_calibration.py`, 67 tests**, fixtures only, no network:

* witness-domain equality — both witnesses, the registrable-stem comparison, a claim naming a
  different domain (negative), no-claim-at-all (negative), and registry provenance still
  outranking a witness;
* the two designed provenance states, produced end-to-end through `compute_confidence`;
* the containment comparator — five agreeing pairs, both directions, six negatives including the
  leading-token case and a name made only of generic words, plus proofs that the ratio still
  carries what it always carried and that containment never reaches exact tier;
* containment inside the page reader, with the location contradiction still blocking at region
  scope and not blocking at city scope;
* fetch-blocked is not evidence — at the accept site and the withdraw site;
* the shared trigger as a pure function over eight (verdict, tier) pairs, then across all three
  lanes, including the crosswalk;
* the address set each lane compares (multi-location ROR, single-location ROR, de-duplication)
  and the tier inputs each lane classifies against;
* the corroboration-only pass — three declines, the flag, and the one-key contract;
* the normalised cross-source comparison — agreement (both ids kept, agreement counted),
  the same shape on two more rows, genuine disagreement unchanged, absence is not agreement,
  and an agreement raising no flag.

**Full suite: 2172 passed, 5 failed, 5 skipped.** The five failures are the documented
pre-existing ones on `main`, verified unchanged against a clean checkout at the start of this
work: `test_name_slot_parity.py::test_department_in_a_lower_slot_is_not_reported_missing`,
`test_orchestrator.py::{test_tier1_full_resolution, test_web_search_fallback_for_name1,
test_web_search_determines_record_type}`, `test_routes.py::test_issues_compare_segments_g6_and_g7_out_of_the_metric`.
Baseline before this work: 2105 passed, 5 failed. **No test regressed.**

Two existing tests were updated, both because the behaviour they pinned improved:

* `test_page_corroborator.py::test_a_name_difference_alone_never_withdraws` — its four
  parametrised pairs (AquaPhoenix, Analytical Sales, Applied Catalysts, Armor Industrial) are all
  brand-versus-legal-name variants and now read as `corroborated` rather than `name_mismatch`. The
  invariant it was written for — the domain is not withdrawn — is unchanged and still asserted,
  and the test now also asserts the name score is still below threshold, i.e. that containment and
  not a loosened threshold carried them.
* `test_wikidata.py::TestTheLaneIsAPureInsert` — the pure-insert baseline now excises both lane
  entry points.

**Runs.** All iteration on the 20-row subset. The S2 file was run once per side at the end. An
earlier S2 baseline was discarded: `git stash` had reverted the previous session's uncommitted
work along with this calibration's, so the diff conflated two changes. The baseline was re-taken
from a git worktree at the exact inherited tree (stash commit `8827814`), warm-cache and
zero-network, and the after side re-run warm so both sides read identical evidence.

---

## 7 · Open items

1. **The Chemspeed / SMB stratum is unmeasured** (§2). Re-warming that evidence cache would also
   restore the reproducibility gate and the evidence-shuffle experiment.
2. **`WIKIDATA_DOMAIN_CORROBORATION` costs one search plus one entity fetch** on a registry-
   resolved record whose domain did not come from a registry — 22 calls over 99 records here,
   yielding 3 claims and 1 verified domain. That ratio is a large-corporate ratio; on a stratum
   with poor Wikidata coverage the yield will be lower and the cost the same. It is a flag so it
   can be A/B'd out per stratum.
3. **`wikidata_domain_disagree` fired twice** on this file — `P856` naming a website other than
   the candidate. Counted and acted on in no way, per the existing rule that a wiki field is not
   grounds to withdraw a domain the guard accepted. Worth a look if the count grows.
4. **Containment agrees on parent/subsidiary pairs that share a head token** — "Cargill
   Incorporated" and "Cargill Foundation" agree by containment, as "Corteva" and "Corteva
   Agriscience LLC" do, and no string comparison can separate the two situations. This does not
   affect the location trigger, which reads the stricter verbatim test, and gates 7 and 8 confirm
   those flags still fire. It does mean the cross-source gate will keep both identifiers on such a
   pair rather than choosing.
