Generated: 2026-08-21 · Commit: d4fc46938514c9a7d249979c4aa9b4ae4cf3e564 · Branch: main · Tree: clean (no uncommitted tracked modifications, no untracked non-ignored files)

# Pass 11 — Delta against the pass-document baseline

Baseline: `515cc7c1a84f55f817d63b4f3f094ce47d57f7fd` (recorded in every `docs/thesis/` header as
branch `diag/website-trace`). Ancestry verified:

```
$ git merge-base --is-ancestor 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd HEAD ; echo $?
0
$ git rev-parse HEAD
d4fc46938514c9a7d249979c4aa9b4ae4cf3e564
$ git rev-parse --abbrev-ref HEAD
main
$ git status --porcelain --untracked-files=all
(empty)
```

The baseline is an ancestor of `HEAD`, so the comparison is a linear `515cc7c..HEAD` range of **22
commits touching 120 files**. `git branch --contains 515cc7c` reports only `main` and
`origin/main`: the branch `diag/website-trace` named in the pass-document headers no longer exists
as a ref, but the commit it named is now on `main`, so the baseline resolves and the comparison is
well-defined. The working tree is clean, so **the working tree and `HEAD` are the same state** and
the "committed / working-tree-only" column in §1 reads *committed* for every row.

**Method.** Every statement below was read out of the working tree at the commit in the header.
Commit messages were used only to locate changes, never to describe them; where a message and the
code disagree the disagreement is recorded as a finding (§2, D-2). Every figure quoted is either a
line of current code or the verbatim output of a script named with its command. No value in this
document was recomputed from memory or carried over from another pass document.

---

## §1 · Change inventory

120 files. Grouped by area. Every row is *committed* (the tree is clean).

### 1.1 · API handlers

| Path | Type | What changed |
|---|---|---|
| `api/models.py` | modified | `EnrichmentRecord.name_5` added (`:107-110`); `EnrichmentResult.website_url` demoted to internal and `domain` promoted to the serialised "Domain" field (`:361`, `:518`); flag model replaced by `flag_codes` / `flagged_fields` / `flag_scopes` / `flag_details` (`:422-442`); six `*_provenance` derived scalars plus `provenance`, `provenance_rejected`, `provenance_rejected_omitted` added (`:457-476`); the six scoped fields write-locked via `__setattr__` with `write()` as the sole write path (`:562-597`); `EnrichmentSummary` gains 15 counters (`:622-654`). |
| `api/output_columns.py` | modified | `RESPONSE_COLUMNS` grows 56 → 65 entries; `website_url` removed, `domain` (`:45`), `name5_enriched` (`:38`), `flag_codes` / `flagged_fields` (`:87-88`) and six `*_provenance` columns (`:104-109`) added. |
| `api/routes.py` | modified | `/issues` and `/issues/compare` now pass a `flag_for_review` argument into `detect_issues` (`:723-728`, `:466-471`); `_build_comparison_xlsx` segments the report into Reduced (G1–G5) / Expected to persist (G6) / Verification (G7) and computes the headline percentage over G1–G5 alone (`:494-500`, `:539-541`); `_cell` added to flatten list-valued response fields for XLSX (`:328-338`); dedup header aliases gain `name3`–`name5` and `leiid` / `lei` (`:817-836`); unrecognised dedup headers now logged at WARNING (`:873-879`). **No endpoint was added, removed, or changed in its HTTP signature.** |

### 1.2 · Orchestrator

| Path | Type | What changed |
|---|---|---|
| `enrichment/orchestrator.py` | modified (+2176/−…) | `_init_result` now returns a write-locked `EnrichedRecord` rather than a plain dict; `_flag_website_review` **deleted**; new `_retry_tier1_after_canonicalisation` (`:2376`), `_return_canonical_short_circuit`, `_classify_record` (`:1105`), `normalise_output_fields` (`:547`), `_apply_domain`, `_write_registry_name`, `_write`, `_log_registry_rejections`, `_record_gleif_evidence`, `_host_prefix_is_generic`, `_raise_unattributed_flag`, `_scoped_scalars`. `finalise` (`:632`) now calls `compute_flags` (`:953`) as the single flag authority; `enrich_batch` calls `apply_batch_consensus` after the batch completes (`:1523`); domain writes route through `write_domain` (`:1209`). |

### 1.3 · New enrichment modules

| Path | Type | What changed |
|---|---|---|
| `enrichment/provenance.py` | added (963 lines) | Per-field provenance log over six `SCOPED_FIELDS` = `('name1_enriched','name2_enriched','domain','record_type','ror_id','lei_id')` (`:53-62`); `Evidence`, `ProvenanceEvent`, `RejectedCandidate`, `ProvenanceLog`; five confidence scales (`ROR_LOCAL`, `FUZZY_RATIO`, `LLM_SELF_REPORTED`, `DETERMINISTIC`, `REGISTRY_EXACT`, `INHERITED`) so bands are namespaced by scale; five guard names and `MAX_REJECTIONS_PER_FIELD = 3` (`:353-365`). |
| `enrichment/flags.py` | added (520 lines) | 11 flag codes (`ALL_CODES`, `:81-95`), rebuilt once from final state by `compute_flags` (`:375`), rendered by `render` (`:280`), withdrawn only by `retract` (`:326`). |
| `enrichment/batch_consensus.py` | added (545 lines) | Post-batch field propagation within (address block, canonical name, legal form) groups; `PROPAGATED_FIELDS` (`:77`), `NEVER_PROPAGATED` (`:92`), `CONSENSUS_SOURCE = "batch_consensus"` (`:113`); `apply_batch_consensus` (`:463`). Never merges records and never changes `tier_used`. |
| `enrichment/elf_codes.py` | added (183 lines) | Static ISO 20275 ELF code sets `NON_COMMERCIAL_ELF` (`:43`) and `COMMERCIAL_ELF` (`:59`), generated at development time from the GLEIF registry. No runtime lookup. |
| `enrichment/classifier.py` | modified (stub → 192-line module) | Was a docstring-only stub declaring classification REMOVED. Now the single `record_type` authority: `classify(TypeEvidence) -> (record_type, record_type_source)` (`:174-190`) over ranked evidence ROR → GLEIF → keyword → unknown. |

### 1.4 · Tiers and resolution

| Path | Type | What changed |
|---|---|---|
| `enrichment/tier1_ror.py` | modified | Cache keys normalised; `ror_normalised_hits()` telemetry added; `_INSTITUTION_ACRONYM_RE` tightened; `_has_case_contrast` and `_guard_identifier_tokens` added; `_US_POSTAL_CODES` / `_TWO_LETTER_STATE_RE` / `_WORDLIKE_POSTAL_CODES` added; `_DISTINCTIVE_TOKEN_MIN_LEN = 4`, `_CONNECTOR_WORDS` introduced. |
| `enrichment/tier1_lei.py` | modified | Cache keys normalised; `lei_normalised_hits()` telemetry added. |
| `enrichment/tier2a_contact.py` | modified | `flag_for_review`/`flag_reason` fields **removed** from `Tier2AResult`, replaced by `low_conf_unchanged: set[str]`; `source_title` added; `extra_departments` parameter added and the whole department block passed to the affiliation prompt; `_apply_mode_b` gains `llm_confidence` and splits the sub-80 band on it. |
| `enrichment/tier2b_dept.py` | modified | `flag_for_review` / `flag_reason` **removed** from `Tier2BResult` (`:37-40` region); the module no longer raises a flag, only sets `confidence`. Still has **no production call site**. |
| `enrichment/tier3_llm.py` | modified | `flag_for_review` / `flag_reason` **removed** from `Tier3Result`; `name4_suggestion` / `name5_suggestion` added; `SUGGESTION_ATTRS` derived from `NAME_SLOTS`. |
| `enrichment/website_resolver.py` | modified | `WebsiteResolution.title` added as read-only evidence for the domain ownership guard; SERP cache calls now pass `country`. |
| `enrichment/confidence.py` | modified | `should_flag_for_review` **deleted** (52 lines removed). `determine_enrichment_status` retained unchanged and still has no caller. |

### 1.5 · Preprocessing, address, search terms

| Path | Type | What changed |
|---|---|---|
| `enrichment/preprocess.py` | modified | Slot-generalised: `_strip_name3_junk` → `_strip_dept_slot_junk`, `_extract_co_attn_from_name2` → `_extract_co_attn_from_slot` plus `_extract_co_attn_from_names` and `_set_care_of`; `STREET_SLOTS` added; `find_suspicious_plain_names` signature changed to varargs. |
| `enrichment/address_processing.py` | modified | `_MAIL_STOP_RE` / `_MAIL_STOP_MARKER_RE` added; `_STREET_TYPE_SUFFIX_RE` and `_has_street_type` added (German street types); `_write_name` added. |
| `enrichment/search_terms.py` | modified | `_UNIT_KEYWORDS` and `_strip_unit_keywords` added; derivation restricted to enriched values. |
| `enrichment/overflow_check.py` | modified | `_CONFIDENCE_RANK` and `_norm` added. |

### 1.6 · Issue detection

| Path | Type | What changed |
|---|---|---|
| `enrichment/issue_detection.py` | modified (+833) | Catalogue restructured from a code→name map into `ISSUE_CATALOGUE: dict[str, IssueDefinition]` carrying `group`, `name`, `field`, `mandatory`, `origin`, `status`, `reason` (`:160-183`). `status ∈ live/withdrawn/ndd/unlisted` introduced. One code added (`G7-VERIFY-001`, `:278`). `G2-CONTACT-008` and `G2-CONTACT-009` moved to `status="withdrawn"`. `EMITTED_CODES` (`:282-284`), `QUALITY_GROUPS` (`:287`), `REDUCIBLE_GROUPS` (`:293`), `PERSISTENT_GROUP` / `VERIFICATION_GROUP` (`:296-297`) added. `detect_issues` gains a `flag_for_review` parameter; `flag_for_review_is_set` added (`:1002`). |

### 1.7 · Utilities

| Path | Type | What changed |
|---|---|---|
| `utils/domain_resolver.py` | added (491 lines) | Single write path for `domain` / `website_url`. `canonicalise_domain` (`:99`), `canonicalise_host` (`:121`), `website_url_for` (`:145`), `resolve_domain` (`:311`), `write_domain` (`:393`). Ownership guard accepts on registry provenance, name similarity, email evidence or on-domain SERP evidence; otherwise leaves `domain` empty and flags `domain-unverified` (`:53-54`). |
| `utils/name_slots.py` | added (94 lines) | `NAME_SLOT_COUNT = 5` (`:35`) and the derived slot vocabularies `NAME_SLOTS`, `RECORD_NAME_FIELDS`, `ENRICHED_NAME_FIELDS`, `DEPT_SLOTS`, `ADJACENT_NAME_PAIRS`. |
| `utils/cache.py` | modified | Keys normalised via `dedup.signatures.normalize_key` and extended with country: `lookup_key` (`:41`), `serp_key` (`:63`); `legacy_lookup_key` / `legacy_serp_key` kept for telemetry only. **`BatchCache.get_ror` / `set_ror` deleted** as dead code; `stats` no longer reports `ror_entries`. |
| `utils/text_utils.py` | modified | `normalise_case` added with `_CANONICAL_TOKEN_FORMS`, `_ROMAN_NUMERALS`, `_LOWERCASE_PARTICLES`, `_APOSTROPHES` and helpers. `smart_title_case` retained and still used at 6 sites. |

### 1.8 · Dedup

| Path | Type | What changed |
|---|---|---|
| `dedup/llm.py` | modified | `TEMPERATURE = 0.0` class constant added; sent when `reasoning_effort` is not active, with a runtime fallback that drops it on a 400 (`_is_unsupported_temperature`). `_is_unsupported_reasoning_effort` refactored onto a shared `_is_unsupported_param`. |
| `dedup/models.py` | modified | `DedupRow.name3` / `name4` / `name5` added (`:39-44`). |
| `dedup/signatures.py` | modified | `department_text(row)` added so the signature key reads the whole name block. |
| `dedup/adjudicator.py`, `dedup/scoring.py` | **unchanged** | Not in the diff. |

### 1.9 · LLM prompts

| Path | Type | What changed |
|---|---|---|
| `llm/prompts.py` | modified | Prompt versioning added: `_digest`, `prompt_version`, and four version constants (`OVERFLOW_CHECK_`, `LAB_PARENT_`, `COMPANY_CANONICAL_`, `PERSON_AFFILIATION_PROMPT_VERSION`), consumed by the orchestrator (`enrichment/orchestrator.py:90-93`). Tier 3 prompt extended to Name 4 / Name 5. |

### 1.10 · ADF artefacts and SQL

| Path | Type | What changed |
|---|---|---|
| `sql/usp_merge_legacy_enriched.sql` | added | Verbatim export of the enrichment merge-back procedure. Binds 32 payload fields including `Name 1`–`Name 4`, `Domain`, `Department Domain`, `Record Type`, `ROR ID`, `LEI ID`, `Flag for Review`, `Flag Reason`. **Does not bind `Name 5`, `Flag Codes`, `Flagged Fields`, or any `*_provenance` column.** |
| `sql/usp_merge_validation_clusters.sql` | added | Verbatim export; binds `row_id`, `block_id`, `cluster_id` (NVARCHAR — comment records it was formerly INT), `routing`, `signature_id`, `confidence`, `reasoning`. |
| `sql/usp_merge_validation_scores.sql` | added | Verbatim export; binds `score_final`, the 11 `score_*` criterion columns, the three count columns, and the golden/approval/version columns. |
| No ADF pipeline JSON was added or changed. | — | — |

### 1.11 · Config

| Path | Type | What changed |
|---|---|---|
| `config.py` | modified | Two new settings: `domain_name_match_threshold` (default `82`, `:213-215`) and `domain_ownership_guard_enabled` (default `True`, `:219-222`); both added to `OPTIONAL_VARS_WITH_DEFAULTS` (`:92-93`), taking it from 21 to 23 entries. |
| `.env.example` | modified | Documents `DOMAIN_NAME_MATCH_THRESHOLD=82` and `DOMAIN_OWNERSHIP_GUARD_ENABLED=true`. |
| `.gitignore` | modified | Ignores `docs/thesis/exemplar_id_map.local.md`. |

### 1.12 · Scripts

| Path | Type | What changed |
|---|---|---|
| `scripts/issue_catalogue_census.py` | added (279 lines) | Derives every Issue-Catalogue figure from the source; defines *declared / live / unlisted / emitted / withdrawn / ndd / observed / fixture* once (`:16-33`). |
| `scripts/ch02_measure.py` | added (710 lines) | Produces the Chapter 2 figures. |
| `scripts/ror_repro.py` | added (523 lines) | ROR reproduction harness over the recorded fixtures. |

### 1.13 · Tests

19 test files added, 24 modified, plus `tests/conftest.py` (+73) and `tests/mocks/lei_mock.py` (+24).
Added: `test_batch_consensus.py`, `test_cache_normalisation.py`, `test_domain_resolver.py`,
`test_flags.py`, `test_issue_catalogue_coverage.py`, `test_mail_stop_variants.py`,
`test_name_slot_parity.py`, `test_output_casing.py`, `test_provenance.py`,
`test_record_type_authority.py`, `test_registry_name_authority.py`, `test_ror_allcaps_guard.py`,
`test_ror_short_distinctive_token.py`. Fixtures added: `tests/fixtures/issue_catalogue_coverage.json`
(242 lines) and 12 `tests/fixtures/ror_repro/*.json` recordings.

### 1.14 · Docs and data

| Path | Type | What changed |
|---|---|---|
| `docs/thesis/*` (17 files) + `docs/thesis/figures/*` (9 files) | added | The pass documents themselves, committed in `3f5a28d` after being generated at `515cc7c`. Not a source change. |
| `docs/thesis-doc-prompt.md` | added | The pass specification. |
| `docs/thesis/desktop.ini` | added | Windows folder-metadata artefact (4 lines). Not content. |
| `README.md` | modified (+950/−…) | Rewritten across the changed subsystems. |
| `PresentationTestData.xlsx`, `PresentationTestData_enriched_checked_v1.xlsx` | modified | Binary. Both shrank (143252→128040 and 130637→116238 bytes). Contents not diffable here — see §6, U-1. |

---

## §2 · Behaviour deltas vs documentation deltas

### (a) BEHAVIOUR — the system now does something different

**B-1 · The "Domain" output column carries a bare registrable domain, not a URL.**
Old: the column was bound to `website_url`, which held the full URL — `"website_url": "Domain"`
(`api/output_columns.py` at baseline), and `domain` was `exclude=True`.
New: the column is bound to `domain`, which holds the registrable domain (`mit.edu`), and
`website_url` is the internal `https://<domain>` homepage (`api/output_columns.py:45`;
`api/models.py:361`, `:518`; `utils/domain_resolver.py:99-148`).

**B-2 · A web-derived domain is now rejected unless ownership is corroborated.**
Old: no ownership guard existed on the domain path; the selected URL's domain was written.
New: `resolve_domain` accepts only on registry provenance, `token_sort_ratio(Name 1, domain label)
≥ DOMAIN_NAME_MATCH_THRESHOLD` (default 82), corroborating email evidence, or on-domain SERP
evidence; otherwise `domain` is left empty and the record is flagged `domain-unverified`
(`utils/domain_resolver.py:311-383`, `:53-54`; `config.py:213-222`).

**B-3 · The review flag is a multi-code, field-scoped structure rebuilt once from final state.**
Old: each tier set `flag_for_review` and appended to a single `flag_reason` string as it ran, and
`_flag_website_review` appended with `"; "` (`docs/thesis/05_DATA_MODEL.md:621`).
New: tiers raise no flags at all (the fields are deleted from `Tier2AResult`, `Tier2BResult`,
`Tier3Result`); `compute_flags` runs once from `finalise` (`enrichment/orchestrator.py:953`) and
produces `flag_codes`, `flagged_fields` and a rendered `flag_reason` over 11 codes
(`enrichment/flags.py:81-95`, `:375`). `_flag_website_review` no longer exists.

**B-4 · `record_type` has a single classification authority.**
Old: `record_type` was written by whichever tier ran last — ROR org types, then LEI, then company
canonicalisation, each overwriting the previous, and `enrichment/classifier.py` was a dead stub.
New: `classify()` decides once at the end of `finalise` from ranked evidence (ROR → GLEIF entity
metadata → keyword → unknown), reports `record_type_source`, and withholds a commercial verdict
when the name reads as a research institution (`enrichment/classifier.py:174-190`, `:139-161`;
`enrichment/orchestrator.py:1105-1121`). A separate internal `routing_type` carries the provisional
value used for tier gating, and `routing_type_mismatch` records a disagreement without correcting it
(`api/models.py:537-547`).

**B-5 · The name block is five slots wide.**
Old: four name slots (`Name 1`–`Name 4`).
New: `NAME_SLOT_COUNT = 5` (`utils/name_slots.py:35`); `EnrichmentRecord.name_5`
(`api/models.py:107-110`), `name5_enriched` column (`api/output_columns.py:38`), Tier 3 emits
`name5_suggestion`, `DedupRow` carries `name3`–`name5` (`dedup/models.py:39-44`).

**B-6 · Per-field provenance is recorded and the six scoped fields are write-locked.**
Old: a single record-level `tier_used` / `source` / `confidence` triple, all `exclude=True`.
New: every write to one of six scoped fields must carry an `Evidence` argument or raise
`UnattributedWriteError` / `MissingEvidenceError` (`api/models.py:562-580`;
`enrichment/provenance.py:53-62`, `:215-231`); the event log and six derived `*_provenance` scalars
ship in the response, the scalars also as file columns (`api/output_columns.py:104-109`).

**B-7 · A batch consensus pass runs after every batch.**
Old: no such pass; each record was enriched in isolation and the batch could carry divergent
identities for one organisation.
New: `apply_batch_consensus` runs over the finalised results (`enrichment/orchestrator.py:1523`),
propagating organisation-level fields within (address block, canonical name, legal form) groups and
setting `source="batch_consensus"` without changing `tier_used` (`enrichment/batch_consensus.py:77-113`,
`:463`).

**B-8 · Tier 1 is re-attempted after canonicalisation.**
Old: one Tier 1 pass per record.
New: `_retry_tier1_after_canonicalisation` (`enrichment/orchestrator.py:2376`) performs one
permitted re-lookup when a later tier produced a name Tier 1 never saw; outcomes counted as
`tier1_retry_attempts` / `tier1_retry_hits_ror` / `tier1_retry_hits_lei` (`api/models.py:622-624`).

**B-9 · Cache keys are normalised and country-scoped.**
Old: keys were `query.strip().lower()` for every namespace.
New: `lookup_key` / `serp_key` normalise via `dedup.signatures.normalize_key` (lowercase, trim,
collapse whitespace, strip punctuation, fold accents) and append the upper-cased country; the SERP
key additionally carries a quoted-phrase bit so an exact-phrase query and its unquoted retry stay
distinct (`utils/cache.py:41-77`). The unnormalised string is still what reaches the API
(`utils/cache.py:16-30`).

**B-10 · Tier 2A verification mode is reachable, and its sub-80 band is split.**
Old: the Tier 2A gate required Name 2 to be blank, so verification mode could not be entered.
New: the gate runs "in either mode: population when Name 2 is blank, verification when it is
populated" (`enrichment/orchestrator.py:3642-3646`), with `canonical_short_circuit` deferred so
Tier 2A can still run (`:3636-3640`). Below the fuzzy threshold, `_apply_mode_b` now splits on the
extraction confidence: only a high-confidence extraction overwrites; a medium-confidence
disagreement leaves the record's value untouched (`enrichment/tier2a_contact.py`, `_apply_mode_b`
docstring and body).

**B-11 · Tier 2A sees the whole department block.**
Old: only Name 2 and Name 3 reached the affiliation prompt.
New: `run_tier2a` takes `extra_departments` and `_extract_affiliation` appends a prompt line per
populated lower slot (`enrichment/tier2a_contact.py`, `run_tier2a` signature and `_extract_affiliation`
body; call site `enrichment/orchestrator.py:3672`).

**B-12 · `/issues` and `/issues/compare` emit `G7-VERIFY-001` and segment the reduction metric.**
Old: 37 declared codes, no verification code, one undifferentiated "Total issues before/after"
pair, and the reduction percentage computed over every code.
New: `G7-VERIFY-001` is declared and emitted from the record's `Flag for Review` cell
(`enrichment/issue_detection.py:278`, `:1002-1015`; `api/routes.py:168-180`), and the comparison
workbook reports three blocks with the percentage computed over `REDUCIBLE_GROUPS` = G1–G5 alone
(`enrichment/issue_detection.py:293`; `api/routes.py:494-500`, `:539-541`).

**B-13 · The dedup file route binds the `LEI ID` column.**
Old: `_DEDUP_HEADER_ALIASES` had no `leiid` key, so the column `/enrich/file` writes was dropped.
New: `"leiid": "lei_id"` and `"lei": "lei_id"` are present (`api/routes.py:831-836`), and every
unrecognised header is now named once at WARNING (`api/routes.py:873-879`).

**B-14 · Dedup calls send `temperature=0.0` where the deployment allows it.**
Old: `reasoning_effort` only; the docstring said "`reasoning_effort` instead of temperature".
New: `TEMPERATURE = 0.0` is sent whenever `reasoning_effort` is not active, with a runtime fallback
that drops it on a rejecting deployment (`dedup/llm.py`, `TEMPERATURE` constant, `_is_unsupported_temperature`,
and the `elif self._use_temperature` branch in the request builder).

**B-15 · The dedup signature key reads the whole name block.**
Old: `name1` / `name2` only. New: `department_text(row)` folds Name 2–Name 5 (`dedup/signatures.py`).

**B-16 · German street-type recognition and mail-stop extraction added.**
`_STREET_TYPE_SUFFIX_RE` / `_has_street_type` and `_MAIL_STOP_RE` / `_MAIL_STOP_MARKER_RE` added to
`enrichment/address_processing.py`, changing which strings are classified as street content and which
sub-location markers are extracted.

**B-17 · Search-term derivation uses only enriched values and strips unit keywords.**
`_UNIT_KEYWORDS` / `_strip_unit_keywords` added to `enrichment/search_terms.py`; derivation restricted
to enriched fields (commit `308f357`, verified in the current module body).

**B-18 · Prompts are versioned.**
Four `*_PROMPT_VERSION` constants derived by digest (`llm/prompts.py`, `prompt_version`) are consumed
by the orchestrator (`enrichment/orchestrator.py:90-93`), so a prompt change is now visible in
telemetry. No Phase 1 prompt version existed at baseline.

### (b) DOCUMENTATION DRIFT FIXED — no change in effect

**D-1 · `should_flag_for_review` deleted.** 52 lines removed from `enrichment/confidence.py`. It had
no caller at baseline (`docs/thesis/03_ALGORITHMS.md:1608`), so deleting it changes no behaviour; it
removes the code/spec contradiction the README's Flag Rules table described.

**D-2 · `BatchCache.get_ror` / `set_ror` deleted.** Dead at baseline
(`docs/thesis/03_ALGORITHMS.md:2485`). Removal is behaviour-neutral. The `stats` property changed
shape as a consequence (`ror_entries` gone) — but `stats` itself still has no caller, so this too is
unobservable.

**D-3 · The issue-catalogue docstring counts are now derived and test-asserted.** The baseline
docstring said "36-code Issue Catalogue" and "34 of the 36 catalogue codes are emitted" while the
code declared 37 — recorded as G-9. The counts are now generated by
`scripts/issue_catalogue_census.py` and pinned by
`tests/test_issue_detection.py::test_docstring_counts_match_the_catalogue`
(`enrichment/issue_detection.py:52-56`).

**D-4 · `enrichment/classifier.py` no longer preserves removed logic in its docstring.** G-60 was
about the stub carrying the deleted rules as prose. The file is now a live module. *(The
classification behaviour change itself is B-4; only the docstring-preservation aspect is drift.)*

**D-5 · The comparison workbook no longer mislabels the catalogue value.** At baseline
`for code, name in ISSUE_CATALOGUE.items()` bound `name` to what is now an `IssueDefinition`; the
current code binds `entry` and writes `entry.name` (`api/routes.py:597-604`, `:620-624`). At
baseline the catalogue values were bare strings, so this is a rename that keeps the same output —
not a behaviour change.

**D-6 · A commit message overstates its change.** `4bc0882` reads "3.4 Remove the unreachable issue
code G2-CONTACT-008". The code does not remove it: `G2-CONTACT-008` is still declared, with
`status="withdrawn"` and a reason recording that it was struck through in Catalogue v2
(`enrichment/issue_detection.py:216-223`). The declaration is deliberately retained "for the audit
trail" (`enrichment/issue_detection.py:41-42`). **The code wins: the code is withdrawn, not
removed**, and `G2-CONTACT-009` was withdrawn in the same change without the message mentioning it
at all. This is why §3 records G-46 as *closed by withdrawal* rather than *closed by deletion*, and
why the declared count went **up** by one rather than down.

### (c) NO-OP

- `docs/thesis/desktop.ini` — Windows folder metadata, 4 lines, no content.
- `.gitignore` — adds one ignore path for a local-only file.
- `docs/thesis/*.md`, `docs/thesis/figures/*.mmd`, `docs/thesis/Datashaper-Tutorial-Part[1-3].txt`,
  `docs/thesis/CONTEXT-EXTERNAL.md`, `docs/thesis-doc-prompt.md` — the pass documents themselves,
  committed after generation. Not system changes.
- `tests/fixtures/ror_repro/*.json` (12 files) — recorded HTTP responses; data, no assertions.
- `enrichment/tier3_llm.py`'s `SUGGESTION_ATTRS` — replaces a hardcoded three-tuple with a derived
  five-tuple; the widening to five slots is B-5, the derivation itself is a refactor.

---

## §3 · Invalidated claims, per pass document

### 3.0 · The issue catalogue, recounted

Required by the brief, stated with method so it is reproducible.

**Counting method** (definitions from `scripts/issue_catalogue_census.py:16-33`):

- **declared** = `len(ISSUE_CATALOGUE)` — every code the module knows of, including withdrawn and
  not-deterministically-detectable ones (`enrichment/issue_detection.py:186-279`).
- **emission sites / deterministically emitted** = `len(EMITTED_CODES)` = entries whose `status` is
  `"live"` or `"unlisted"` (`enrichment/issue_detection.py:282-284`).
- **observable** = codes with a *positive* case in `tests/fixtures/issue_catalogue_coverage.json`,
  asserted by `tests/test_issue_catalogue_coverage.py`. A code with a passing positive case is one
  some input actually makes fire.

**Command and verbatim output** (`./.venv/Scripts/python.exe scripts/issue_catalogue_census.py`):

```
  declared                     38
  live                         34
  unlisted (not in v2)         1
  deterministically emitted    35   (live + unlisted)
  withdrawn                    2
  not det. detectable          1
  fixture-covered              35 of 35
  live quality codes (G1-G6)   33
  origin of those              {'API': 20, 'DS': 11, 'BOTH': 2}
  entries per group            {'G1': 12, 'G2': 7, 'G3': 7, 'G4': 5, 'G5': 2, 'G6': 4, 'G7': 1}
```

**The three numbers: declared 38 · emission sites 35 · observable 35.**

Baseline figures were **37 declared, 35 with emission sites, ≤34 observable, 31 observed** on the
demo dataset (`docs/thesis/ch02_SOURCE.md:1206`). The changes: `G7-VERIFY-001` added (+1 declared,
+1 emitted); `G2-CONTACT-008` and `G2-CONTACT-009` moved from live to withdrawn (−2 emitted). Net
35 emitted, unchanged in total but different in membership. The "≤34 observable" ceiling — which
existed because `G2-CONTACT-008`'s site was unreachable — is gone: the unreachable site is no longer
an emission site at all, and all 35 emitted codes now carry a positive fixture case.

The **observed** figure is a property of a dataset, not of the rule set, and is *not* restated here:
both workbooks changed in this range (§6, U-1), so any observed count would need a fresh run against
the current data. `ch02_SOURCE.md`'s observed figures are therefore listed as unverified below rather
than replaced.

---

### 3.1 · `00_INVENTORY.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:336` | "Suite run (this commit): **`3 failed, 1019 passed, 12 warnings in 28.44s`**" | `5 failed, 1773 passed, 14 warnings in 41.37s` | §5 below |
| `:341-343` | The three named failures | Still the three named failures, plus two new ones | §5 below |
| `:79` | "\| `enrichment/classifier.py` \| 13 \| Docstring-only stub; classification logic REMOVED and moved to ROR org types (see §4). \|" | 192 lines; the single `record_type` authority, called from `_classify_record` | `enrichment/classifier.py:174-190`; `enrichment/orchestrator.py:1105-1121` |
| `:314-317` | "**`enrichment/classifier.py`** (13 lines) — a docstring-only stub stating classification was REMOVED … No module imports it anywhere in the repository" | Imported by the orchestrator | `enrichment/orchestrator.py:1105-1121` |
| `:415` | Unreferenced-code list includes "`enrichment/confidence.py`, … `enrichment/classifier.py` (stub)" | `classifier.py` removed from the list; `confidence.py` remains but only for `determine_enrichment_status` — `should_flag_for_review` no longer exists | `enrichment/confidence.py:25`; `should_flag_for_review` absent |
| §1 file table | Does not list `enrichment/provenance.py`, `enrichment/flags.py`, `enrichment/batch_consensus.py`, `enrichment/elf_codes.py`, `utils/domain_resolver.py`, `utils/name_slots.py`, `scripts/ch02_measure.py`, `scripts/issue_catalogue_census.py`, `scripts/ror_repro.py`, `sql/*.sql` | Add 9 modules and 3 SQL exports | §1.3, §1.7, §1.10, §1.12 |
| §3.1 call graph | Does not contain `_retry_tier1_after_canonicalisation`, `_classify_record`, `apply_batch_consensus`, `write_domain`, `compute_flags`, `normalise_output_fields` | Add all six | `enrichment/orchestrator.py:2376`, `:1105`, `:1523`, `:1209`, `:953`, `:547` |
| §3 anchors | `enrich_records (api/routes.py:88)`, `dedup_cluster_block (api/routes.py:802)`, `dedup_score (api/routes.py:896)` | `:96`, `:952`, `:1046` | `api/routes.py:96`, `:952`, `:1046` |
| §3 anchors | `enrich_batch (orchestrator.py:783)`, `_enrich_single (…:1698)`, `_finalise_and_return (…:1550)`, `_maybe_resolve_website_bc (…:858)`, `_probe_department_url (…:963)`, `_resolve_person_affiliation (…:1413/1433)`, `_run_lei_lookup (…:1624)` | `:1469`, `:2765`, `:2597`, `:1577`, `:1705`, `:2169`, `:2676` | `enrichment/orchestrator.py` at those lines |
| §5 test inventory | 19 test files absent | Add the files listed in §1.13 | §1.13 |

**Endpoint count: unchanged.** 13 routes at baseline, 13 now (`grep -cE "^@router\.(get|post)" api/routes.py`
returns 13 at both revisions, and the baseline's route list is identical) — `/health`, `/enrich`,
`/enrich/file`, `/issues`, `/issues/compare`, `/api/dedup/cluster-block`, `/api/dedup/file`,
`/api/dedup/score`, `/api/dedup/approve`, `/api/dedup/score/file`, `/diag/llm`, `/diag/dedup-llm`,
`/tiers` (`api/routes.py:82,95,635,697,750,951,981,1045,1095,1126,1183,1215,1254`). None added,
removed, or changed in HTTP signature. `detect_issues`'s *Python* signature gained a `flag_for_review`
keyword (`api/routes.py:723-728`) — an internal call-path change, not an endpoint change.

### 3.2 · `01_TRACEABILITY.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:12` | "Suite status for all cited test files is from the single Pass-0 run (`3 failed, 1019 passed`)" | `5 failed, 1773 passed` | §5 |
| `:128` | "\| G2-CONTACT-008 \| No Contact and No Department \| `issue_detection.py:367` \| implemented \|" | Status → **superseded**; declared `status="withdrawn"`, never emitted | `enrichment/issue_detection.py:216-223` |
| `:129` | "\| G2-CONTACT-009 \| Department Missing And Enrichable from Contact \| `issue_detection.py:369` \| implemented \|" | Status → **superseded**; declared `status="withdrawn"`, never emitted | `enrichment/issue_detection.py:224-233` |
| `:253-254` | "**`README.md` cites `enrichment/classifier.py` for classification** (Record Classification Logic), but that module is a REMOVED stub (`classifier.py:1-12`)" | The discrepancy is closed from the code side: the module now implements classification | `enrichment/classifier.py:174-190` |
| `:75` (UC 3) | Cites `_run_lei_lookup orchestrator.py:1624`; `run_company_canonical orchestrator.py:2164` | `:2676`; `run_company_canonical` call site moved | `enrichment/orchestrator.py:2676` |
| new rows | No requirement covers the domain ownership guard, per-field provenance, the flag model, batch consensus, the Tier 1 retry, or the five-slot name block | Six new `X-` requirements needed | `utils/domain_resolver.py:311`; `enrichment/provenance.py:53`; `enrichment/flags.py:375`; `enrichment/batch_consensus.py:463`; `enrichment/orchestrator.py:2376`; `utils/name_slots.py:35` |

**Requirement status changes: G2-CONTACT-008 and G2-CONTACT-009, both `implemented` → `superseded`.**
No other requirement ID in the table changes status. G-1's subject (Tier 2A verification mode) is a
gap entry, not a requirement row.

### 3.3 · `02_ARCHITECTURE.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:506-508` | "notes that the public \"Domain\" output column is `website_url` and the bare `domain` is internal" | Inverted: the column is `domain`; `website_url` is internal | `api/output_columns.py:45`; `api/models.py:361`, `:518` |
| §5 (artefacts) | Lists the merge-back procedures as external, unexported | Three procedures are now verbatim repository artefacts | `sql/usp_merge_legacy_enriched.sql`, `sql/usp_merge_validation_clusters.sql`, `sql/usp_merge_validation_scores.sql` |
| §3 (components) | No component for post-batch convergence | `apply_batch_consensus` is a batch-scoped stage between per-record enrichment and the response | `enrichment/orchestrator.py:1523` |

The ADF pipeline topology, the SQL MI table progression and the DATAshaper configuration are
**unchanged**: no ADF JSON was added or modified in this range.

### 3.4 · `03_ALGORITHMS.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:1581-1608` | The whole "Flag-for-review / enrichment-status assignment (`determine_enrichment_status`, `should_flag_for_review` …)" section, incl. "`should_flag_for_review(confidence, tier_used, tier2_mode: str \| None, name2_match_result, source) -> tuple[bool, str \| None]` (enrichment/confidence.py:40-46)" | `should_flag_for_review` no longer exists. Replace with `enrichment.flags.compute_flags`, called once from `finalise` | `enrichment/flags.py:375`; `enrichment/orchestrator.py:953`; `should_flag_for_review` absent from `enrichment/confidence.py` |
| `:1608` | "neither function is imported or called anywhere" | Half-true now: `determine_enrichment_status` still has no caller; the other function is gone | `enrichment/confidence.py:25` |
| `:1431` | "⚠ Stale test: tests/test_orchestrator.py:358-370 (`test_web_search_determines_record_type`) … No web-search-based `record_type` derivation exists in the current orchestrator" | Still fails, but the reason changed: `record_type` is now decided by `classify()` from ROR/GLEIF/keyword evidence, and web search is still not among them | `enrichment/classifier.py:174-190`; §5 |
| `:1502`, `:3288`, `:3497` | "`_finalise_and_return` (1550-1573) … derives `domain` from `website_url` when missing (1566-1569)" | Direction inverted: `website_url` is derived from `domain` by `website_url_for` | `utils/domain_resolver.py:145-148`; `api/models.py:518` |
| `:2068`, `:2085`, `:3295-3312`, `:3325` | Path A writes `website_url` directly from ROR `links[]` | ROR's link is now a *candidate* into `write_domain`, which canonicalises it to the registrable domain and rebuilds the homepage | `enrichment/orchestrator.py:1209`; `utils/domain_resolver.py:99-148`, `:393` |
| `:3417` | "the orchestrator writes `website_url` and calls `_flag_website_review(result, \"Website inferred by LLM — verify\")` (enrichment/orchestrator.py:916-921), which sets `flag_for_review=True` and appends to any existing `flag_reason` (enrichment/orchestrator.py:619-628)" | `_flag_website_review` no longer exists; no tier appends a flag reason | `_flag_website_review` absent from `enrichment/orchestrator.py`; `enrichment/flags.py:375` |
| `:3477` | "establishes the precedence ROR domain → domain-from-`website_url` → domain-from-`source_url`" | Precedence is now expressed as ownership conditions in `resolve_domain`: registry / name / email / serp | `utils/domain_resolver.py:311-383` |
| `:2485` | "`utils/cache.py` `BatchCache` declares a per-batch ROR store (`get_ror`/`set_ror`, utils/cache.py:75-81), but no production code calls these methods" | Deleted | `utils/cache.py:130-137` (comment recording the deletion) |
| `:7193` | "results are cached in a **module-level dict** `_ror_cache` keyed `(name_lower, country_code)`" | Key is now `(normalize_key(name), COUNTRY)` | `utils/cache.py:41-48`; `enrichment/tier1_ror.py` (`ror_normalised_hits`) |
| `:4993`, `:5018`, `:5042-5047`, `:5077`, `:5283` | Every statement that `G2-CONTACT-008` "has an add-site that is provably unreachable" and that a caller "counting codes the detector can emit over-counts by one" | Both `G2-CONTACT-008` and `G2-CONTACT-009` are `status="withdrawn"` and excluded from `EMITTED_CODES` by construction; the over-count is gone | `enrichment/issue_detection.py:216-233`, `:282-284` |
| `:5019` | "\| G2-CONTACT-009 \| … \| Yes (issue_detection.py:369) \|" | Withdrawn, never emitted | `enrichment/issue_detection.py:224-233` |
| `:5286` | "the non-VAL missing-data rules (G2-NAME-009/012, G2-CONTACT-009) ignore `present_fields`" | `G2-CONTACT-009` is withdrawn; the claim now covers G2-NAME-009/012 only | `enrichment/issue_detection.py:224-233` |
| `:5437` | "the module docstring's \"36-code\"/\"34 emitted\" figures are stale (§1.2); G2-CONTACT-008's emission site is unreachable (§1.3)" | Both flags cleared; counts are derived and test-asserted | `enrichment/issue_detection.py:52-56` |
| Part H | No specification for `G7-VERIFY-001` | New rule: fires from the record's `Flag for Review` cell; `None` (no column) and `False` both suppress it but mean different things | `enrichment/issue_detection.py:278`, `:1002-1015`; `api/routes.py:168-180` |
| new sections | No procedures documented for the domain ownership guard, provenance/admissibility, the flag model, batch consensus, the Tier 1 retry, or `classify()` | Six new procedure specifications required | `utils/domain_resolver.py:311`; `enrichment/provenance.py`; `enrichment/flags.py`; `enrichment/batch_consensus.py`; `enrichment/orchestrator.py:2376`; `enrichment/classifier.py:174` |
| Tier 2A section | Documents Tier 2A as population-only with a single sub-threshold outcome | Verification mode reachable; sub-80 band split on extraction confidence; whole department block passed to the prompt | `enrichment/orchestrator.py:3636-3646`, `:3672`; `enrichment/tier2a_contact.py` `_apply_mode_b` |

### 3.5 · `03b_EXEMPLARS.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:80` | "raises **32 of the 37 declared codes**. The five it never raises:" | 38 declared; the emitted set changed membership (−2 withdrawn, +1 G7). The dataset-observed figure must be re-derived — both workbooks changed in this range | `enrichment/issue_detection.py:186-279`; §6 U-1 |
| `:86` | "\| `G2-CONTACT-008` \| No Contact and No Department \| Has an emission site but it is unreachable (`enrichment/issue_detection.py:364-367`…) \|" | Withdrawn; no emission site | `enrichment/issue_detection.py:216-223` |
| `:252`, `:256`, `:511` | "Codes raised: **`G1-ADDR-001`, `G2-NAME-012`, `G2-CONTACT-009`.**" | `G2-CONTACT-009` can no longer be raised; the exemplar's code list must be re-derived | `enrichment/issue_detection.py:224-233` |
| `:259` | "`G2-CONTACT-008` concretely: that code requires the same gate with the contact **absent**…" | The unreachability demonstration is moot — the code is withdrawn | `enrichment/issue_detection.py:216-223` |
| `:298` | "Note that `G2-CONTACT-009` does **not** fire on the raw record despite the blank Name 2" | True but for a new reason: it cannot fire on any record | `enrichment/issue_detection.py:224-233` |

Every exemplar's raw field values and post-pipeline values are drawn from the two workbooks, both of
which changed (§6, U-1). **The whole document requires re-derivation**, not only the rows above.

### 3.6 · `04_PARAMETERS.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:603` | "40 environment variables enumerated, of which 2 are secrets and 2 are required" | **42** environment variables; secrets and required counts unchanged at 2 and 2 (`REQUIRED_VARS == ['AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT']`) | `config.py:92-93` adds two entries, taking `OPTIONAL_VARS_WITH_DEFAULTS` from 21 to 23; `config.py` `REQUIRED_VARS` |
| §1 (new rows) | No row for `DOMAIN_NAME_MATCH_THRESHOLD` | Default `82`, float, `rapidfuzz token_sort_ratio` cut-off for attributing a web-derived domain | `config.py:92`, `:213-215`; `.env.example` |
| §1 (new rows) | No row for `DOMAIN_OWNERSHIP_GUARD_ENABLED` | Default `true`, bool, kill switch; when off, candidates are still canonicalised and only the ownership conditions are skipped | `config.py:93`, `:219-222`; `.env.example` |
| §1 (new rows) | No row for the dedup sampling temperature | `DedupLLM.TEMPERATURE = 0.0`, **not configurable by design** — "an env knob would let it drift silently between runs" | `dedup/llm.py`, `TEMPERATURE` constant |
| §1 (new rows) | No row for `MAX_REJECTIONS_PER_FIELD` | `3` — cap on stored guard rejections per field per record | `enrichment/provenance.py:365` |
| §1 (new rows) | No row for `NAME_SLOT_COUNT` | `5` | `utils/name_slots.py:35` |
| §1 (new rows) | No row for `_DISTINCTIVE_TOKEN_MIN_LEN` | `4` (ROR distinctive-token guard) | `enrichment/tier1_ror.py` |
| §1 (new rows) | No row for `_SIGNIFICANT_TOKEN_LEN` | `4` (domain-guard significant tokens) | `utils/domain_resolver.py:80` |
| §5 | "7 parameters defined but not consumed" | `OPTIONAL_VARS_WITH_DEFAULTS` is still never read (`config.py:83` is its only occurrence), so G-39 stands; the count needs re-derivation against the two new settings, both of which **are** consumed | `utils/domain_resolver.py:299-309` |
| §2 conflicts | 7 conflicts recorded | Unchanged in this range: `MAX_PAGE_CONTENT_CHARS` (still `"3000"` in `OPTIONAL_VARS_WITH_DEFAULTS`, `config.py:93` region) and `DEPT_PROBE_CROSS_DOMAIN` were not touched | `config.py` diff contains only the two additions |

**Model deployment names, generation parameters, timeouts, page sizes and batch sizes are otherwise
unchanged**: `config.py`'s diff in this range consists solely of the two `DOMAIN_*` additions
(2 lines in `OPTIONAL_VARS_WITH_DEFAULTS`, 21 lines of `Settings` field + comment). `GLEIF_TIMEOUT_SECONDS=15`,
`LEI_NAME_MATCH_THRESHOLD=88`, `LEI_MAX_RETRIES=2`, `FUZZY_MATCH_THRESHOLD=80`,
`MAX_PAGE_CONTENT_CHARS=3000`, `DEFAULT_MAX_CONCURRENCY=5` all read as before (`.env.example`
context lines in the diff). No ADF activity policy changed.

### 3.7 · `05_DATA_MODEL.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:424` | "the 50 `RESPONSE_COLUMNS` headers in order" | **65**. Note this figure was already wrong at the baseline, where the count was **56** — the delta is 56 → 65, and the document's 50 matched neither | `api/output_columns.py` (`len(RESPONSE_COLUMNS) == 65`); baseline module evaluated at `515cc7c` gives 56 |
| §1.4 field table | `website_url` → `Domain` row | Replace with `domain` → `Domain`; move `website_url` to the excluded-fields list | `api/output_columns.py:45`; `api/models.py:361`, `:518` |
| §1.4 field table | 10 columns absent | Add `name5_enriched`→`Name 5`, `flag_codes`→`Flag Codes`, `flagged_fields`→`Flagged Fields`, and the six `*_provenance` columns | `api/output_columns.py:38`, `:87-88`, `:104-109` |
| `:174` | "\| `flag_reason` \| `Flag Reason` \| `Optional[str]` \| yes \| `None` \| `:395`; `:83` \|" | Line refs now `api/models.py:441`; `api/output_columns.py:89`. The field is now a *rendering* of `flag_codes`, not an independently-set string | `api/models.py:428-441`; `enrichment/flags.py:280` |
| `:621` | "\| `Flag Reason` (`flag_reason`) \| same sites as `Flag for Review`; `_flag_website_review` appends with `\"; \"` rather than overwriting (`orchestrator.py:619-628`) \|" | Single producer: `compute_flags`, once, from `finalise`. `_flag_website_review` deleted | `enrichment/orchestrator.py:953`; `enrichment/flags.py:375` |
| §1.4 excluded list | Does not carry `domain_verified_by`, `domain_rejected`, `tier1_retry_attempted`, `tier1_retry_hit`, `record_type_source`, `routing_type`, `routing_type_mismatch`, `flag_scopes`, `flag_details` | Add nine internal fields | `api/models.py:436-442`, `:518-547` |
| §2 summary contract | `EnrichmentSummary` missing 15 counters | Add `tier1_retry_attempts`, `tier1_retry_hits_ror`, `tier1_retry_hits_lei`, `routing_type_mismatch_count`, `cache_hits_after_normalisation`, `domain_from_registry`, `domain_from_email`, `domain_from_serp`, `domain_rejected_unverified`, `consensus_groups`, `consensus_records_updated`, `consensus_conflicts`, `consensus_fields_propagated`, `consensus_flags_retracted` | `api/models.py:622-654` |
| `:640`, `:1140` | "`enrichment/confidence.py` (`determine_enrichment_status`, `should_flag_for_review`) has **no** [caller]" | Only `determine_enrichment_status` remains | `enrichment/confidence.py:25` |
| §2.5 dedup contract | `DedupRow` carries `name1`/`name2` | Add `name3`, `name4`, `name5` | `dedup/models.py:39-44` |
| `:489-491` | "⚠ Whether Legacy actually carries target columns named `Domain`, `Department Domain`, `Flag for Review`, `Flag Reason`, `Error`, `Record Type`" | **Now evidenced** for most of them: the merge procedure binds them explicitly. It binds `Name 1`–`Name 4` but **not** `Name 5`, and none of `Flag Codes`, `Flagged Fields`, or the six `*_provenance` columns | `sql/usp_merge_legacy_enriched.sql` |
| §1 (new) | No schema recorded for the three stored procedures | Three verbatim exports now available as ground truth | `sql/*.sql` |

**A new contract gap.** `RESPONSE_COLUMNS` emits 9 columns that `usp_merge_legacy_enriched` does not
bind: `Name 5`, `Flag Codes`, `Flagged Fields` and the six `*_provenance` columns. The service writes
them; the merge-back drops them. This is a **new G-series gap** (see §3.9).

### 3.8 · `06_EXTERNAL_DEPS.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:128` | "⚠ `BatchCache.get_ror` / `set_ror` (`utils/cache.py:75-81`) exist but have **no callers**" | Deleted | `utils/cache.py:130-137` |
| Caching section | ROR/LEI/SERP caches keyed on `strip().lower()` | Keyed on `normalize_key(name)` plus upper-cased country; SERP additionally on a quoted-phrase bit. The value sent to each API is still the unnormalised string | `utils/cache.py:41-77`, `:16-30` |
| SERP section | `cache.get_serp(query)` / `set_serp(query, results)` | Both now take `country` | `enrichment/website_resolver.py:492`, `:513` |
| Dedup LLM section | "`reasoning_effort` instead of temperature (reasoning models may ignore temperature)" | `temperature=0.0` is sent when `reasoning_effort` is not in play, with a runtime fallback on rejection | `dedup/llm.py` module docstring and `_is_unsupported_temperature` |
| GLEIF section | Response fields read: name, country | Also `entity.category`, `entity.legalForm.id`, `entity.legalForm.other`, consumed by `classify()` | `enrichment/classifier.py:100-135` |

**No external service was added or removed.** `enrichment/elf_codes.py` is explicitly a static table,
not a service: "Nothing looks a code up at runtime: this is a lookup table over fields the pipeline
already fetches, not a new service dependency" (`enrichment/elf_codes.py:7-10`). The set of network
dependencies remains ROR, GLEIF, SerpAPI/DuckDuckGo, page fetch, Azure OpenAI.

### 3.9 · `06b_CROSSCUTTING.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:103` | "3 failed, 1019 passed, 12 warnings in 29.97s" | `5 failed, 1773 passed, 14 warnings in 41.37s` | §5 |
| `:107-108` | The three named failures | Three still fail; two new ones | §5 |
| `:224` | "\| Test gate \| none; suite is manual and currently red (3 failed / 1019 passed) \|" | red (5 failed / 1773 passed) | §5 |
| `:1236` | "The test suite is red at `HEAD` — 3 failed, 1019 passed — and no gate consumes the result" | 5 failed, 1773 passed; still no gate | §5 |
| `:562` | "(`ror_entries` / `serp_entries`) and is never called by any logging site" | `stats` now returns `serp_entries` and `serp_normalised_hits`; still no caller | `utils/cache.py:194-200` |
| Observability | No prompt-version telemetry for Phase 1 | Four `*_PROMPT_VERSION` constants now emitted | `llm/prompts.py`; `enrichment/orchestrator.py:90-93` |
| Security / personal data | — | `SCOPED_FIELDS` deliberately excludes `contact`, `care_of` and `email` so the provenance store carries no personal data | `enrichment/provenance.py:20-23` |

### 3.10 · `07_EVALUATION.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:293-294` | "The run recorded at this commit is `3 failed, 1019 passed, 12 warnings in 28.44s`, with the three failures named in `00_INVENTORY.md:336-343`" | `5 failed, 1773 passed, 14 warnings in 41.37s`; five failures | §5 |
| §1 harness inventory | Does not list `scripts/ch02_measure.py`, `scripts/issue_catalogue_census.py`, `scripts/ror_repro.py` | Three metric-producing scripts to add. `issue_catalogue_census.py` is the reproduction command for every catalogue figure | `scripts/issue_catalogue_census.py:1-33` |
| §2 metrics | No metric over the domain guard, batch consensus, the Tier 1 retry, or cache normalisation | 15 new summary counters are now emitted per batch and are directly usable as metrics | `api/models.py:622-654` |
| §7 results table | Row set predates the new counters | Add rows for the above | `api/models.py:622-654` |
| `:279` (M-8) | `scripts/test_local.py` compares `flag_for_review` among six fields | Still valid, but `flag_codes` / `flagged_fields` now carry the discriminating information | `api/models.py:422-427` |

### 3.11 · `08_GAPS.md` — by gap ID

**Closed.**

| ID | Was | Now | Evidence |
|---|---|---|---|
| G-1 | Tier 2A verification mode cannot be entered from any input | Reachable: "verification when it is populated" | `enrichment/orchestrator.py:3642-3646`, `:3636-3640` |
| G-6 | README cites `classifier.py` as the classification module, but it is a REMOVED stub | The module implements classification | `enrichment/classifier.py:174-190` |
| G-9 | Issue-detection docstring states stale catalogue counts | Derived and pinned by a test | `enrichment/issue_detection.py:52-56` |
| G-36 | `BatchCache`'s ROR store and `stats` have no callers | ROR store deleted (*`stats` still has no caller — see partial below*) | `utils/cache.py:130-137` |
| G-46 | `G2-CONTACT-008` has an emission site no input can reach | Withdrawn; no emission site. **Closed by withdrawal, not deletion** — see §2 D-6 | `enrichment/issue_detection.py:216-223` |
| G-48 | The dedup file route drops the `LEI ID` column for want of a header alias | `"leiid"` and `"lei"` aliases added | `api/routes.py:831-836` |
| G-60 | `classifier.py` preserves removed classification logic inside its docstring | Docstring replaced by a live specification | `enrichment/classifier.py:1-46` |

**Partially closed.**

| ID | What closed | What remains | Evidence |
|---|---|---|---|
| G-24 / G-35 | `should_flag_for_review` deleted, so half of `enrichment/confidence.py`'s dead surface is gone | `determine_enrichment_status` is still defined and still uncalled | `enrichment/confidence.py:25`; only occurrences are the definition and its own docstring |
| G-36 | ROR store deleted | `stats` still has no calling site | `utils/cache.py:194-200` |
| G-45 | "Two catalogue codes are declared and never emitted" — the *count* is now 3 (two withdrawn + one ndd) and each carries a machine-readable `status` and a required `reason` | The situation is now modelled rather than incidental, but codes are still declared and never emitted | `enrichment/issue_detection.py:180-183`, `:216-233`, `:200-212` |
| G-5 | "Enrichment cannot correct an incorrect existing Name 2" — Tier 2A Mode B can now overwrite below the fuzzy threshold on a high-confidence extraction | A medium-confidence disagreement still leaves the record untouched by design | `enrichment/tier2a_contact.py` `_apply_mode_b` |

**Newly opened.**

| ID | Statement | Evidence |
|---|---|---|
| G-76 (new) | `usp_merge_legacy_enriched` binds none of the nine columns added in this range — `Name 5`, `Flag Codes`, `Flagged Fields`, and the six `*_provenance` columns. The service emits them and the merge-back drops them | `sql/usp_merge_legacy_enriched.sql`; `api/output_columns.py:38`, `:87-88`, `:104-109` |
| G-77 (new) | `enrichment/tier2b_dept.py` had its flag fields removed and its confidence semantics rewritten, but `run_tier2b` **still has no production call site** — a repository search finds only the definition and tests. The module was edited without being wired | `enrichment/tier2b_dept.py:48`; no non-test importer |
| G-78 (new) | Two cross-scale comparisons are documented in-code as known defects and deliberately left in place: `_apply_mode_b` takes `max(llm_score, our_score)` across an LLM self-report and a RapidFuzz ratio; `EnrichmentResult.confidence` projects four incommensurable scales onto one label | `enrichment/tier2a_contact.py` `_apply_mode_b` comment; `api/models.py:501-516` |
| G-79 (new) | `routing_type_mismatch` records records whose tiers were gated on a type the evidence later contradicted. They are surfaced and **not re-run** | `api/models.py:541-547`; `enrichment/classifier.py:12-20` |
| G-80 (new) | Two tests that passed at the baseline now fail — one asserting G2-NAME-012 slot parity, one asserting the segmented compare metric | §5 |

**Unchanged (verified still open):** G-2 (`run_tier2b` has no call site — reconfirmed above),
G-37 (`unit_domain_or_path` has no application caller — only `enrichment/search_terms.py:268` and
tests), G-38 (`prefetched_results` branch — only `enrichment/website_resolver.py:455-487` and tests),
G-39 (`OPTIONAL_VARS_WITH_DEFAULTS` never read — `config.py:83` is its only occurrence).
G-3, G-4, G-7, G-8, G-10 … G-23, G-25 … G-34, G-40 … G-44, G-47, G-49 … G-59, G-61 … G-75 were not
re-verified individually in this pass and are not asserted either way (§6, U-4).

### 3.12 · `09_DECISIONS.md` — by decision ID

| ID | Status | Superseded text | Replacement | Evidence |
|---|---|---|---|---|
| D-36 | **Superseded** | "Merge the \"Domain\" and \"Website URL\" output columns into one column carrying the URL" (`:121`) | The merged column now carries the bare registrable domain, not the URL. A new decision entry is needed for the reversal and its reason (a deep ROR link or a sub-site host could ship in the column) | `api/output_columns.py:39-45`; `utils/domain_resolver.py:10-18` |
| D-5 | **Citation broken, decision intact** | "enforced at six separate sites … `enrichment/confidence.py:33,51-55`; `enrichment/orchestrator.py:390,1888,2417,2571`" (`:90`) | `confidence.py:51-55` was inside `should_flag_for_review`, now deleted; all four orchestrator line refs have moved. The principle itself is *strengthened*: flags are now derived from what the record holds | `enrichment/flags.py:1-40`; `should_flag_for_review` absent |
| D-1 | **Partially reversed** | "Restrict Tier 2A to populating a blank Name 2, and unwire Tier 2B, disabling both Name-2 correction paths" (`:86`) | The Tier 2A half is reversed — verification mode is reachable. The Tier 2B half stands: `run_tier2b` still has no call site | `enrichment/orchestrator.py:3642-3646`; `enrichment/tier2b_dept.py:48` |
| D-21 | **Superseded** | "Declare two catalogue codes that the deterministic detector never emits" (`:106`), citing `issue_detection.py:18-24,88,112` | Three codes are now non-emitting, each with a typed `status` and a mandatory `reason`; the cited lines no longer hold that content | `enrichment/issue_detection.py:180-183`, `:200-233` |
| D-40 | **Extended** | "Pin the dedup client to a newer API version and drop `reasoning_effort` on rejection rather than failing" (`:125`) | The same drop-on-rejection pattern now also covers `temperature` | `dedup/llm.py` `_is_unsupported_temperature` |
| D-35 | **Weakened** | "Align the `/enrich` JSON response with the file column schema and drop `domain` from it" (`:120`) | `domain` is back in the response, and the response now carries `provenance`, `provenance_rejected` and `provenance_rejected_omitted`, which the file schema deliberately does **not** — the two schemas are no longer identical | `api/models.py:361`, `:469-476`; `api/output_columns.py:96-103` |
| D-33 | **Weakened** | "Slim `EnrichmentResult`: drop `*_original` / `*_changed`, exclude internals from the response" (`:118`) | The model grew by ~20 fields, 10 of them serialised | `api/models.py:417-476` |
| D-12 | **Superseded in part** | "Website precedence ROR → SERP → LLM, with SERP/LLM skipped once `website_url` is set" (`:97`) | Precedence still holds for candidate *selection*, but every candidate now passes the ownership guard before it is written | `utils/domain_resolver.py:311-383` |
| new | — | No decision records the domain ownership guard, the flag redesign, per-field provenance, batch consensus, the Tier 1 retry, the single classification authority, or the five-slot block | Seven new entries. Each has an unusually rich in-code rationale, quotable directly | `utils/domain_resolver.py:19-31`; `enrichment/flags.py:1-40`; `enrichment/provenance.py:1-35`; `enrichment/batch_consensus.py:1-31`; `enrichment/classifier.py:1-46`; `utils/name_slots.py:1-27` |

D-2, D-3, D-4, D-6, D-7, D-8, D-9, D-10, D-11, D-13, D-14, D-15, D-16, D-17, D-18, D-19, D-20,
D-22 … D-32, D-34, D-37, D-38, D-39, D-41 were not contradicted by anything read in this pass.

### 3.13 · `00_OPEN_ITEMS.md`

| § / line | Superseded text | Replacement | Evidence |
|---|---|---|---|
| `:532` (item 129) | "`determine_enrichment_status` / `should_flag_for_review` are documented as pipeline behaviour and have no caller … either wire them … or delete `enrichment/confidence.py`" | **Half-resolved by the second option**: `should_flag_for_review` was deleted and its role taken by `enrichment/flags.py`. `determine_enrichment_status` remains, uncalled | `enrichment/confidence.py:25`; `enrichment/flags.py:375` |
| `:544` (item 141) | "Decide and act: delete the ROR store from `BatchCache`, or route `tier1_ror` through it" | **Resolved by the first option** — deleted. The second half of the item (log `BatchCache.stats` at `orchestrator.py:838`) is **not** done | `utils/cache.py:130-137`, `:194-200` |
| `:49`, `:54`, `:65` | The `should_flag_for_review` dead-code register entries | Resolved | `should_flag_for_review` absent |
| `:473` (item 99) | "Author a per-field labelled answer key … minimally `name1_enriched`, `name2_enriched`, `website_url`, `record_type`" | Field name is now `domain`, not `website_url` | `api/output_columns.py:45` |

The register's remaining items were not individually re-verified (§6, U-4).

### 3.14 · Documents unaffected

None of the thirteen pass documents is unaffected — every one carries at least the baseline commit
header, which no longer describes the tree. Beyond the header:

- **`01_TRACEABILITY.md`, `06_EXTERNAL_DEPS.md`, `07_EVALUATION.md`** are affected in a bounded way
  (the rows named in §3.2, §3.8, §3.10) and are otherwise sound.
- **`02_ARCHITECTURE.md`** is affected in three places only; its external-system content — ADF
  pipelines, the SQL MI progression, DATAshaper — is **entirely unaffected**, because no ADF artefact
  changed in this range.
- **`09_DECISIONS.md`**'s historical entries (what was decided, when, by which commit) remain
  accurate as history; only the eight entries in §3.12 need a superseding note.

`CONTEXT-EXTERNAL.md` and `ch02_SOURCE.md` are not in the thirteen named documents. For the record:
`CONTEXT-EXTERNAL.md` is unaffected except that its `[AUTHOR]`-marked description of the three stored
procedures can now be upgraded to `[EXPORT]` against `sql/*.sql`. `ch02_SOURCE.md` is affected
throughout §3/§5/§6 (every figure derives from `scripts/ch02_measure.py` over the two workbooks, both
of which changed) and at `:167`, `:223-224`, `:288`, `:494-500`, `:579-580`, `:729`, `:1101`,
`:1206` for the catalogue counts.

---

## §4 · Figures requiring regeneration

The eight `.mmd` files were **not** edited, per the brief.

| Figure | Verdict | Specific changes |
|---|---|---|
| **fig-01** `system-components` | **needs edit (minor)** | Node and edge topology unchanged — no ADF or DS artefact changed. Two annotations: edges `e8` and `e18` cite `usp_merge_legacy_enriched` / `usp_merge_validation_clusters`, which now have verbatim in-repo artefacts (`sql/*.sql`) and can drop any "not exported" hedging. A third procedure, `usp_merge_validation_scores`, now exists in the repo and has no edge; note that its existence is **not** evidence it is wired in ADF, so edge `e20`'s "⚠ not wired in ADF" annotation stands. |
| **fig-02** `er-data-model` | **needs redraw** | `ENRICH_RESULT`: replace `string website_url "Domain"` with `string domain "Domain"`; add `name5_enriched`, `flag_codes`, `flagged_fields`, and the six `*_provenance` attributes. `ISSUE_CODE`: relabel PK comment `"36-code catalogue, 34 emitted"` → `"38 declared, 35 emitted"`. `LEGACY`: relabel `"50 RESPONSE_COLUMNS written back - NOT EVIDENCED"` → `"65 RESPONSE_COLUMNS emitted; 31 target columns bound by usp_merge_legacy_enriched"` (and the "NOT EVIDENCED" hedge can go — `sql/usp_merge_legacy_enriched.sql` is now the evidence; 31 is the count of distinct `tgt.[…]` assignments in its `UPDATE SET` clause). `DEDUP_ROW`: add `name3`, `name4`, `name5`. Consider a new `PROVENANCE_EVENT` entity related `ENRICH_RESULT ||--o{ PROVENANCE_EVENT`. |
| **fig-03** `enrichment-run-sequence` | **unaffected** | The ADF ↔ MI ↔ API sequence and the failure branch are unchanged; the diagram does not decompose the API participant, so batch consensus (which runs inside it) needs no new message. |
| **fig-04** `enrich-call-graph` | **needs redraw** | Every `orchestrator.py` line anchor has moved (see §3.1). Node relabels: `enrich_records (api/routes.py:88)`→`:96`; `enrich_batch :783`→`:1469`; `_process_with_semaphore :799`→`:1486`; `_enrich_single :1698`→`:2765`; `_finalise_and_return :1550`→`:2597`; `_maybe_resolve_website_bc :858`→`:1577`; `_probe_department_url :963`→`:1705`; `_resolve_person_affiliation :1413/1433`→`:2169`; `_run_lei_lookup :2058/2152/2198`→`:2676`; `_run_address_stage`→`:2625`; `finalise :600`→`:632`. New nodes and edges: `_retry_tier1_after_canonicalisation (:2376)` reached from the canonical/Tier-3 path with ROR/GLEIF external edges; `_classify_record (:1105)` → `classify (classifier.py:174)` under `finalise`; `write_domain (:1209)` → `resolve_domain (domain_resolver.py:311)` on the Path A/B/C join; `compute_flags (:953)` under `finalise`; `normalise_output_fields (:547)`; and a batch-level `apply_batch_consensus (:1523)` hanging off `enrich_batch` **after** the per-record fan-in, not inside `_enrich_single`. |
| **fig-05** `deduplication-run-sequence` | **unaffected** | `dedup/adjudicator.py` is unchanged; the ADF sequence is unchanged. |
| **fig-06** `dedup-clustering-call-graph` | **needs edit (one node)** | Only the entry node: `dedup_cluster_block (api/routes.py:802)` → `(api/routes.py:952)`. Every `dedup/adjudicator.py` and `dedup/signatures.py` anchor is still correct — those files' line numbers did not shift (`_enforce_name2_split:136`, `_enforce_identity_split:185`, `_mode_a:270`, `_mode_b:400`, `_adjudicate_residue:556`, `_emit_rows:721`, `_process_block:831`, `cluster_blocks:933`, all verified). |
| **fig-07** `golden-record-election-sequence` | **unaffected** | `dedup/scoring.py` and the `/score` + `/approve` route bodies are unchanged. |
| **fig-08** `scoring-call-graph` | **needs edit (one node)** | Only the entry node: `dedup_score (api/routes.py:896)` → `(api/routes.py:1046)`. Every `dedup/scoring.py` anchor is still correct (`detect_issues:454`, `coerce_weights:626`, `score_row:813`, `_tiebreak_key:939`, `_cluster_year_maxima:982`, `_cluster_merge_confidence:1020`, `elect_golden_records:1033`, `build_summary:1208`, all verified). |

---

## §5 · Test evidence

The suite could not be run with the interpreter first on `PATH`
(`…/Python313/python.exe: No module named pytest`). It runs in the project virtualenv at
`.venv/`. Command and actual final lines:

```
$ ./.venv/Scripts/python.exe -m pytest -q
...
FAILED tests/test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing
FAILED tests/test_orchestrator.py::TestOrchestrator::test_tier1_full_resolution
FAILED tests/test_orchestrator.py::TestOrchestrator::test_web_search_fallback_for_name1
FAILED tests/test_orchestrator.py::TestOrchestrator::test_web_search_determines_record_type
FAILED tests/test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric
5 failed, 1773 passed, 14 warnings in 41.37s
```

**Summary: 5 failed, 1773 passed, 14 warnings in 41.37s.** Baseline was
`3 failed, 1019 passed, 12 warnings in 28.44s` (`docs/thesis/00_INVENTORY.md:336`) — 754 more
passing tests.

**The three previously failing tests all still fail.** All three are the `tests/test_orchestrator.py`
cases named at `docs/thesis/00_INVENTORY.md:341-343`: `test_tier1_full_resolution`,
`test_web_search_fallback_for_name1`, `test_web_search_determines_record_type`.

**Two new failures.**

1. `tests/test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing`
   — `tests/test_name_slot_parity.py:180`. Failing assertion:

   ```
   assert "G2-NAME-012" not in detect_issues(rec)
   E   AssertionError: assert 'G2-NAME-012' not in ['G1-NAME-004', 'G2-VAL-002', 'G2-VAL-004',
       'G2-VAL-007', 'G2-VAL-008', 'G2-VAL-003', ...]
   ```

   The test's own docstring states the intent: "G2-NAME-012 read Name 2 alone, so a department in
   Name 3 with a blank Name 2 was reported as missing. It is not missing." Input is
   `Name 1="Stanford University"`, `Name 2=""`, `Name 3="Department of Genetics"`. The slot-parity
   fix was written and the test added, but `G2-NAME-012` still reads Name 2 alone. **This is a
   genuine unfixed defect, not a stale test**: the rule contradicts the five-slot generalisation
   applied elsewhere in the same change (§2 B-5).

2. `tests/test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric`
   — `tests/test_routes.py:530`. Failing assertion:

   ```
   # The reduction block sees only the G1 defect: 1 before, 0 after, 100%.
   assert summary["Reduced: issues before"] == 1
   E   assert 2 == 1
   ```

   The segmented reduction block counts 2 reducible issues before, where the test expects 1. Either
   the fixture record raises a second G1–G5 code the test did not anticipate, or `segment()` is
   placing a code in "Reduced" that belongs in G6/G7. Not resolved from the code alone (§6, U-3).

Both new failures are in files added in this range, so they are new assertions failing on first
introduction rather than regressions of previously-green tests.

**Warnings** rose 12 → 14; all 14 are the same `httpx` `verify=<str>` `DeprecationWarning`
(`tests/test_flags.py` ×2, `tests/test_routes.py` ×9, `tests/test_scoring.py` ×3).

---

## §6 · Unresolved

**U-1 · The two workbooks changed and their contents are not diffable here.**
`PresentationTestData.xlsx` (143252 → 128040 bytes) and
`PresentationTestData_enriched_checked_v1.xlsx` (130637 → 116238 bytes) both changed; both shrank.
Every dataset-derived figure in `03b_EXEMPLARS.md` and `ch02_SOURCE.md` §3/§5/§6 — the observed code
counts, the per-code before/after table, the exemplar records themselves — rests on them. I could not
determine whether the change is a re-export, a row-set change, a column change, or an anonymisation
pass. *Evidence that would settle it:* `python scripts/ch02_measure.py` and
`python scripts/issue_catalogue_census.py PresentationTestData.xlsx PresentationTestData_enriched_checked_v1.xlsx`
run against both revisions of the workbooks, with the sheet names, row counts and header rows of each
compared. Until then no observed-count claim in either document should be treated as either confirmed
or refuted.

**U-2 · Whether `G7-VERIFY-001`'s introduction changes the reduction percentage the thesis reports.**
G7 is excluded from `REDUCIBLE_GROUPS` by construction (`enrichment/issue_detection.py:293`), and G6
is excluded too — but G6 was *included* in the baseline's single undifferentiated total
(`api/routes.py` at baseline computed `pct` over `total_before`). So the reduction percentage is
computed over a strictly smaller denominator than at baseline, and the direction of the change
depends on how many G6 codes the dataset carries. *Evidence that would settle it:*
`POST /issues/compare` run on both workbooks, comparing "Reduction %" against the baseline route's
single-total figure on the same input.

**U-3 · Why `test_issues_compare_segments_g6_and_g7_out_of_the_metric` counts 2 rather than 1.**
The assertion is at `tests/test_routes.py:530` and the fixture is constructed in the same test. I did
not read the fixture body closely enough to say whether the extra code is a legitimate second G1–G5
issue the test author did not anticipate, or a mis-segmentation in `segment()`
(`api/routes.py:494-500`). This determines whether `03_ALGORITHMS.md` Part H needs a corrected rule
or the test needs a corrected expectation. *Evidence that would settle it:* re-run the test with
`detect_issues` on the fixture record printed, and check each returned code's
`ISSUE_CATALOGUE[code].group` against `REDUCIBLE_GROUPS`.

**U-4 · The gap and open-item registers were verified selectively, not exhaustively.**
I re-verified G-1, G-2, G-5, G-6, G-9, G-24, G-35, G-36, G-37, G-38, G-39, G-45, G-46, G-48, G-60
against current code, and open items 99, 129 and 141. The remaining ~60 gap entries and the rest of
the open-item register were not individually re-checked; §3.11 does not assert their status either
way. *Evidence that would settle it:* one pass per remaining gap ID re-running the specific search or
citation its entry names.

**U-5 · Whether the nine unbound columns in `usp_merge_legacy_enriched` are an oversight or a
decision.** The procedure is a verbatim export of a deployed object, so it may simply predate the
columns. Nothing in the repository states an intent either way. *Evidence that would settle it:* the
deployment date of the procedure in the Managed Instance against the commit dates of `b8ad102`
(five name slots), `5e423c2` (flag model) and `59d3e4d` (provenance) — or a statement from whoever
owns the procedure.

**U-6 · Whether `run_tier2b`'s rewrite in this range was intended to precede wiring it.**
`enrichment/tier2b_dept.py` had its flag fields deleted and its confidence semantics rewritten with a
substantive new comment, yet it still has no production caller (G-2, reconfirmed). The edit is
consistent both with "prepared for wiring" and with "swept along by the batch flag removal". *Evidence
that would settle it:* whether any branch or open work item wires `run_tier2b`, or a statement of
intent from the author.

**U-7 · The five-slot generalisation is incomplete and I could not determine its intended extent.**
`G2-NAME-012` still reads Name 2 alone (§5, failure 1), while `G1-NAME-004`, `G4-NAME-015` and the
preprocessing rules were generalised via `utils/name_slots.py`. Whether every remaining `Name 2`
literal in `enrichment/issue_detection.py` is a deliberate exception or an unfinished sweep is not
determinable from the code. *Evidence that would settle it:* an enumeration of every `name2`/`Name 2`
literal in `enrichment/issue_detection.py` classified against `DEPT_SLOTS`, plus the intended scope of
each affected catalogue rule in Catalogue v2.

---

Pass 11 complete. 120 files changed across 22 commits; 18 behaviour deltas, 6 documentation-drift
fixes, 5 no-op groups (§2); 13 pass documents all affected, none unaffected (§3); issue catalogue
recounted as 38 declared / 35 emission sites / 35 observable with the counting method and command
stated (§3.0); 7 gaps closed, 4 partially closed, 5 newly opened (§3.11); 8 decisions superseded,
weakened or reversed (§3.12); 2 figures need redraw, 3 need edits, 3 unaffected (§4); suite is
`5 failed, 1773 passed` with the 3 baseline failures persisting and 2 new ones named (§5); 7 items
unresolved (§6). No file outside `docs/thesis/11_DELTA.md` was modified. Stop.
