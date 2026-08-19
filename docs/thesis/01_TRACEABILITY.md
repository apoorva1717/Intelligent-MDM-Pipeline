Generated: 2026-08-16 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 1 — Requirements Traceability

This document maps every requirement the repository defines to its implementation and its
evidence, and — per the amendment to this pass — assigns a new `X-` requirement ID to every
behaviour present in code but absent from any pre-existing requirement list, documenting each
to the same standard. Nothing implemented is treated as out of scope.

`Test` cites a test file whose subject is the requirement; a requirement with no such test is
marked `none`. `Status ∈ implemented / partial / not implemented / superseded`. Suite status
for all cited test files is from the single Pass-0 run (`3 failed, 1019 passed`); the three
failures are confined to `tests/test_orchestrator.py`.

---

## Amendment 2026-08-17 — reachability corrections from Pass 3

Pass 3 established that two Name-2 procedures are present in the source but cannot execute in
the running pipeline. A requirement whose implementation cannot be reached is not `implemented`,
however complete the module behind it. The following rows changed; every other row is unaltered.

| Row | Was | Now | Reason |
|---|---|---|---|
| UC 4 | implemented | **partial** | The "discover" half (Mode A, populating a blank Name 2) runs; the "verify" half (Mode B) is unreachable by construction — the orchestrator gate admits only records whose Name 2 is blank (`enrichment/orchestrator.py:2451-2457`) while the mode selector requires it populated (`enrichment/tier2a_contact.py:80`) |
| X-31 | *(new row)* | **not implemented** | Tier 2B department search exists as a complete module but has no call site and no import in the orchestrator (`enrichment/orchestrator.py:37-59`); back-filled per the Table 2 note below |

Table 2's closing note already anticipates back-filling from Pass 3 ("Additional X-items may
surface there and should be back-filled here"), which is the basis for adding X-31 rather than
leaving the behaviour untraced.

**Checked and deliberately not changed.** UC 5 (normalise Name 2 to official wording) remains
`implemented`: it is served by `run_tier2_canonical` at a call site independent of Tier 2A
(`enrichment/orchestrator.py:2384`), which requires a *populated* field to act on
(`:2367-2374`) and is reached for `record_type ∈ {research_institution, company}` with a
resolved Name 1 (`:2362-2365`). The distinction that matters for the thesis: an existing Name 2
**is** normalised to official wording by an LLM working from the name alone, but it is **not**
verified or corrected against retrieved web evidence. X-11 and X-13 mention "verify" in the
sense of the department-domain probe (`_verify_candidate_url`) and are unrelated to Tier 2A
verification; both are unchanged. The G-series, EP-series and DD-series tables are unaffected.

The history of how both procedures became unreachable is `09_DECISIONS.md` (D-1); the
consequences are listed in `08_GAPS.md`.

---

## Requirement-ID sources

Four ID systems already exist in the repository:

1. **Use-case numbers (UC).** The README "Use Case Reference Table" (`README.md:655-670`)
   enumerates UC 0 and UC 2–13. The code additionally defines **UC 14–17** as section headers
   and `res.note()` tags in `enrichment/preprocess.py` (`preprocess.py:612,633,1560,1704`),
   which the README table does **not** list. **UC 1 is not defined anywhere** (the sequence
   skips it in both README and code). These extra/absent numbers are recorded as discrepancies
   in §Discrepancies and carried in Table 1a with status notes, not relabelled — they are real
   use-case numbers the code uses.
2. **Issue-catalogue rule codes (G-series).** `enrichment/issue_detection.py:77-117` defines
   `_ISSUE_CATALOGUE`, a dict of ~35 codes in five groups: **G1** (cross-field / address / name
   placement), **G2** (validation / name / contact completeness), **G3** (duplicate / conflict),
   **G4** (overflow / format), **G5** (canonicalisation form).
3. **API endpoint contracts.** 13 HTTP routes in `api/routes.py` plus the Azure Function
   catch-all (`function_app.py:14`). Enumerated in Pass 0 §2.
4. **Phase-2 dedup contract.** The identity model and rules documented in `README.md`
   (Phase 2 — Deduplication Adjudicator) and encoded in `dedup/`.

---

## Table 1a — Use cases (UC)

| ID | Requirement (one line) | Implemented in | Test | Status |
|----|------------------------|----------------|------|--------|
| UC 0 | Detect Name1+Name2 being one split organisation name (LLM), flag if so | `enrichment/overflow_check.py`; invoked `orchestrator.py:1724`; tag set `orchestrator.py:1750` | none (no dedicated `run_overflow_check` test) | implemented |
| UC 2 | Resolve institution Name 1 to official ROR name on a ROR match | `tier1_ror.py:848 (call)`; write path `orchestrator.py:1955-2015`; tag `orchestrator.py:1693` | `test_tier1.py`, `test_tier1_ror_country.py`, `test_ror_name_verbatim.py` | implemented |
| UC 3 | Canonicalise a company name: GLEIF/LEI registry first, LLM geographic fallback | `_run_lei_lookup orchestrator.py:1624`; `run_company_canonical orchestrator.py:2164`; tag `orchestrator.py:1695` | `test_tier1_lei.py`, `test_classifier.py` | implemented |
| UC 4 | Discover/verify Name 2 from the contact's faculty page (scope-filtered) | `run_tier2a orchestrator.py:2468`; `enrichment/tier2a_contact.py`; tag `orchestrator.py:2531` | `test_tier2a_population.py`, `test_tier2a_verification.py` | **partial** — discovery (Mode A) reachable; verification (Mode B) unreachable by construction (see Amendment 2026-08-17) |
| UC 5 | Normalise Name 2 department to official wording (LLM) | `run_tier2_canonical orchestrator.py:2384,2508`; `enrichment/tier2_canonical.py`; tag `orchestrator.py:2411` | `test_tier2_canonical_downgrade.py` | implemented |
| UC 6 | Recognise accounts-payable / admin desks and flag for special handling | `preprocess.py:1476-1482`; admin classifier `text_utils.py:990 is_admin_unit`; tag `orchestrator.py:2270` | `test_search_terms_fixes.py` (is_admin_unit), `test_preprocess_co_attn.py` | implemented |
| UC 7 | Move a person name out of the name fields into `contact` | `preprocess.py:1584` (extraction), `preprocess.py:1420` (Attn Pattern A) | `test_person_in_name1.py`, `test_person_in_name1_flag.py`, `test_person_org_in_street.py` | implemented |
| UC 8 | Copy an email from a name/address field to `email` (non-destructive) | `preprocess.py:1485-` | `test_preprocess_co_attn.py` | implemented |
| UC 9 | Extract a street address embedded in a name field to a street slot | `preprocess.py:1520-` | `test_org_in_street.py`, `test_street_in_name.py`, `test_address_cleanup.py` | implemented |
| UC 10 | Detect and clear opaque codes / meaningless identifiers in name fields | `preprocess.py:305` (detect), `preprocess.py:1575` (clear) | `test_leading_code_strip.py` | implemented |
| UC 11 | Normalise a "Doing Business As" variant to canonical "DBA" | `preprocess.py:1547-1557` | none (rewrite itself untested; the ST2 DBA guard is tested in `test_search_terms_fixes.py`) | partial — the normalisation rewrite has no dedicated test |
| UC 12 | Silently clear an identical duplicate name field | `preprocess.py:1750-` | `test_canonical_dedup.py`, `test_preprocess_co_attn.py` | implemented |
| UC 13 | Resolve a granular lab's parent academic department (parent→Name2, lab→Name3) | `enrichment/lab_resolver.py`; `run_lab_resolver orchestrator.py:2298`; tag `orchestrator.py:2341,2355` | `test_lab_resolver.py` | implemented |
| UC 14 | Consolidate name slots (pack Name 2–4 leftward; promote Name 2→Name 1 after person extraction) | `preprocess.py:1704-` | `test_person_org_in_street.py` (promotion), `test_preprocess_co_attn.py` | implemented — ⚠ not in README UC table |
| UC 15 | c/o + ATTN five-case extraction from Name 2 (person/company/dept/email/title) | `preprocess.py:633,1247`; `_extract_co_attn_from_name2` | `test_uc15_co_attn.py`, `test_preprocess_co_attn.py` | implemented — ⚠ not in README UC table |
| UC 16 | Split an institution + embedded department in Name 1; route org/dept in a street to the Name block | `preprocess.py:1634` (Name-1 split); street routers `preprocess.py:1288-1389` | `test_street_org_split.py`, `test_street_scope_routing.py`, `test_pipe_splitter_inversion.py`, `test_named_building.py` | implemented — ⚠ not in README UC table |
| UC 17 | Normalise long-form legal suffixes (e.g. "Aktiengesellschaft"→"AG", "Incorporated"→"Inc") | `preprocess.py:1560-` | `test_legal_suffix_normalisation.py` | implemented — ⚠ not in README UC table |

⚠ **UC 13 dual use.** The number 13 tags two distinct behaviours: lab→parent resolution
(README + `orchestrator.py`) and "Name 3 residual junk cleanup" (`preprocess.py:1664`). The row
above documents the README-authoritative meaning; the preprocess use is recorded in
§Discrepancies. ⚠ **UC 1** is undefined in both README and code.

---

## Table 1b — Issue-catalogue rules (G-series)

Emitted deterministically by `enrichment/issue_detection.py`; catalogue at
`issue_detection.py:77-117`. All emitted codes are exercised by `tests/test_issue_detection.py`
(status `implemented`) unless noted. `G2-VAL-*` are emitted by the required-field loop
(`issue_detection.py:130-136`).

| ID | Description | Emitted at | Status |
|----|-------------|-----------|--------|
| G1-CROSS-001 | Address Content in Name Field | `issue_detection.py:228` | implemented |
| G1-CROSS-002 | Org Name in Address Field | `issue_detection.py:242` | implemented |
| G1-CROSS-003 | Contact Information in Wrong Field | `issue_detection.py:256,261` | implemented |
| G1-ADDR-001 | House Number Embedded in Street | `issue_detection.py:269` | implemented |
| G1-ADDR-003 | Sub-location Embedded in Street | `issue_detection.py:275` | implemented |
| G1-ADDR-004 | PO Box Embedded in Street | `issue_detection.py:281` | implemented |
| G1-ADDR-006 | Mail Code in Street Field | `issue_detection.py:287` | implemented |
| G1-ADDR-009 | Unclassified Residual in Address | catalogue `issue_detection.py:88` — marked "LLM-only — never emitted" | not implemented (deterministic) |
| G1-ADDR-011 | Department Label in Street Field | `issue_detection.py:293` | implemented |
| G1-NAME-001 | Name Overflow Across Fields | `issue_detection.py:305` | implemented |
| G1-NAME-004 | Name 2 Empty With Name 3 Populated | `issue_detection.py:309` | implemented |
| G1-NAME-013 | SAP Internal Code in Name Field | `issue_detection.py:314` | implemented |
| G2-VAL-001 | Name 1 Missing | `issue_detection.py:130` | implemented |
| G2-VAL-002 | Postal Code Missing | `issue_detection.py:131` | implemented |
| G2-VAL-003 | Tax Jurisdiction Missing | `issue_detection.py:132` | implemented |
| G2-VAL-004 | Region Missing | `issue_detection.py:133` | implemented |
| G2-VAL-006 | Language Missing | `issue_detection.py:134` | implemented |
| G2-VAL-007 | Search Term 1 Missing | `issue_detection.py:135` | implemented |
| G2-VAL-008 | Country Missing | `issue_detection.py:136` | implemented |
| G2-NAME-009 | Lab Without Department | `issue_detection.py:351` | implemented |
| G2-NAME-012 | Research Institution Missing Department | `issue_detection.py:343,366` | implemented |
| G2-CONTACT-008 | No Contact and No Department | `issue_detection.py:367` | implemented |
| G2-CONTACT-009 | Department Missing And Enrichable from Contact | `issue_detection.py:369` | implemented |
| G3-NAME-003 | DBA Pattern in Name Field | `issue_detection.py:383` | implemented |
| G3-NAME-005 | Duplicate Name Across Fields | `issue_detection.py:390` | implemented |
| G3-ADDR-005 | Multiple PO Boxes on Record | `issue_detection.py:401` | implemented |
| G3-ADDR-012 | Duplicate Street Across Fields | `issue_detection.py:417` | implemented |
| G3-ADDR-013 | Two Distinct Street Addresses on Record | `issue_detection.py:424` | implemented |
| G3-ADDR-014 | PO Box and Street Both Present | `issue_detection.py:428` | implemented |
| G3-CONTACT-007 | Multiple Contacts on Record | `issue_detection.py:432` | implemented |
| G4-NAME-015 | Name Overflow Beyond Name 4 | `issue_detection.py:443` | implemented |
| G4-ADDR-008 | Bare Sub-location Marker Without Value | `issue_detection.py:448` | implemented |
| G4-ADDR-025 | Sub-location Overflow Beyond Street 5 | catalogue `issue_detection.py:112` — marked "LLM-only — never emitted" | not implemented (deterministic) |
| G4-ADDR-026 | Postal Code Format Invalid | `issue_detection.py:456` | implemented |
| G4-ADDR-027 | Country Code Not ISO 2-letter | `issue_detection.py:463` | implemented |
| G5-NAME-001 | Organisation Name Not in Official Form | `issue_detection.py:475` | implemented |
| G5-NAME-002 | Unit Name Not in Official Form | `issue_detection.py:480` | implemented |

Two catalogue codes (`G1-ADDR-009`, `G4-ADDR-025`) are declared but the source comment states
they are LLM-only and never emitted by the deterministic detector — `not implemented` in the
shipped (deterministic) `/issues` path.

---

## Table 1c — API endpoint contracts

Handlers and models per Pass 0 §2. `tests/test_routes.py` exercises route contracts.

| ID | Requirement (one line) | Implemented in | Test | Status |
|----|------------------------|----------------|------|--------|
| EP-health | Liveness probe | `api/routes.py:75` | `test_routes.py` | implemented |
| EP-enrich | Enrich a batch of records (JSON) | `api/routes.py:88` | `test_routes.py`, `test_orchestrator.py` | implemented |
| EP-enrich-file | Enrich an uploaded XLSX, return XLSX | `api/routes.py:518` | `test_routes.py` | implemented |
| EP-issues | Deterministic issue audit of an XLSX | `api/routes.py:580` | `test_routes.py`, `test_issue_detection.py` | implemented |
| EP-issues-compare | Before/after issue comparison of two XLSX | `api/routes.py:628` | `test_routes.py` | implemented |
| EP-dedup-cluster | Cluster one address-gated block (LLM adjudication) | `api/routes.py:802` → `cluster_blocks dedup/adjudicator.py:933` | `test_dedup.py`, `test_routes.py` | implemented |
| EP-dedup-file | Cluster an uploaded XLSX of candidate rows | `api/routes.py:832` | `test_routes.py` | implemented |
| EP-dedup-score | Deterministic scoring + golden-record election (JSON) | `api/routes.py:896` → `elect_golden_records dedup/scoring.py:1033` | `test_scoring.py`, `test_routes.py` | implemented |
| EP-dedup-score-file | Scoring + election over an XLSX | `api/routes.py:977` → `dedup/scoring_xlsx.py` | `test_scoring.py`, `test_routes.py` | implemented |
| EP-dedup-approve | Record a human approve/reject on a cluster | `api/routes.py:946` → `apply_approval dedup/scoring.py:574` | `test_scoring.py`, `test_routes.py` | implemented |
| EP-diag-llm | Probe the enrichment LLM | `api/routes.py:1034` | none | implemented |
| EP-diag-dedup-llm | Probe the dedup LLM | `api/routes.py:1066` | none | implemented |
| EP-tiers | Report tier configuration | `api/routes.py:1105` | `test_routes.py` | implemented |
| EP-azure | Serve all routes as an Azure Function (catch-all ASGI) | `function_app.py:14-19` | none | implemented |

---

## Table 1d — Phase-2 dedup rules

Documented in `README.md` (Phase 2 section) and encoded in `dedup/`.

| ID | Requirement (one line) | Implemented in | Test | Status |
|----|------------------------|----------------|------|--------|
| DD-sig | STEP A: collapse rows into distinct `(norm_name1, norm_name2)` signatures with a block id | `dedup/signatures.py:build_signatures, derive_block_id` | `test_dedup.py` | implemented |
| DD-modeA | Mode A: partition a same-`has_name2` bucket into entities (LLM) | `dedup/adjudicator.py:270 _mode_a` | `test_dedup.py` | implemented |
| DD-modeB | Mode B: assign signatures to existing entities (LLM) | `dedup/adjudicator.py:400 _mode_b` | `test_dedup.py` | implemented |
| DD-identity | Two-level identity rule + identity/Name2 split enforcement | `dedup/adjudicator.py:136 _enforce_name2_split`, `:185 _enforce_identity_split` | `test_dedup.py`, `test_canonical_identity.py` | implemented |
| DD-residue | Residue candidate nomination (id/name/token convergence) + pairwise adjudication | `dedup/candidates.py`; `dedup/adjudicator.py:556 _adjudicate_residue` | `test_candidates.py`, `test_dedup.py` | implemented |
| DD-cap | `MAX_CANDIDATES_PER_BLOCK` cap → route block to `manual_review` | `dedup/adjudicator.py:903 _resolve_candidate_config` | `test_dedup.py` | implemented |
| DD-elect | Golden-record election (scoring + tie-break + confidence demotion) | `dedup/scoring.py:1033 elect_golden_records` | `test_scoring.py` | implemented |
| DD-score | Deterministic per-row scoring against `weights.json` bands | `dedup/scoring.py:813 score_row`; `dedup/weights.json` | `test_scoring.py` | implemented |
| DD-approve | Human approval overrides a proposed cluster | `dedup/scoring.py:574 apply_approval` | `test_scoring.py` | implemented |
| DD-issues | Dedup-side issue detection (contradictions, etc.) | `dedup/scoring.py:454 detect_issues` | `test_scoring.py` | implemented |
| DD-eval | Evaluation harness computing dedup metrics | `eval/dedup_eval.py` | `test_dedup_eval.py` | implemented |

---

## Table 2 — X-series requirements (behaviour in code, not in any prior requirement list)

Per the amendment, each entry below is a first-class requirement with a new `X-` ID and is
documented to the same standard as Table 1. These are predominantly the enrichment
subsystems developed after the original UC/issue-catalogue lists were written.

| ID | Requirement (one line) | Implemented in | Test | Status |
|----|------------------------|----------------|------|--------|
| X-1 | Derive Search Term 1: ROR-acronym → `strip_tld(domain)` → usable-Name-1 handle → None | `search_terms.py:479 _derive_search_term_1` | `test_search_terms_fixes.py`, `test_search_terms.py` | implemented |
| X-2 | Derive Search Term 2: ADMIN → subdomain acronym → Name-2 filled-to-32 → dept-domain host → None | `search_terms.py:505 _derive_search_term_2`, `:442 _subdomain_acronym`, `:413 _fill_to_width` | `test_search_terms_fixes.py`, `test_search_terms.py` | implemented |
| X-3 | Terminal normalisation of both search terms (upper/trim/collapse/≤32 on word boundary) | `search_terms.py:403 _normalise_term` | `test_search_terms_fixes.py::TestTerminalNormalisation` | implemented |
| X-4 | ST2 field-content guards: block DBA Name 2 and institution-in-Name-2 (field swap) from handles | `search_terms.py:431 _name2_is_unit_phrase`, guard in `_derive_search_term_2` | `test_search_terms_fixes.py::test_field_swap_flags_and_nulls, ::test_dba_name2_nulled` | implemented |
| X-5 | Website Path A: adopt ROR `links[]` website / domain on a ROR match | `orchestrator.py:1955-2015` (ROR write); `tier1_ror.py extract_website_from_ror` | `test_website_resolver.py::TestExtractWebsiteFromROR` | implemented |
| X-6 | Website Path B (SERP): distinctive/acronym-in-host 0/1/2 ranking, rank-0 reject, TLD-needs-host-match | `website_resolver.py:350 select_website_from_serp`, `:150 _has_host_match`, `:139 _acronym_in_host` | `test_website_resolver.py::TestPathBGuards, ::TestSelectWebsiteFromSERP` | implemented |
| X-7 | Website Path B retrieval: `num_results=10` + one unquoted retry on a first-pass miss | `website_resolver.py:440 resolve_website_via_serp`, `:406 _build_serp_query` | `test_website_resolver.py::TestPathBRetry` | implemented |
| X-8 | Website Path C (LLM) fallback when Path B finds nothing | `website_resolver.py:550 infer_website_via_llm` | `test_website_resolver.py::TestInferWebsiteViaLLM` | implemented |
| X-9 | `WEBSITE_TRACE` read-only per-candidate JSON diagnostic + driver script | `website_resolver.py:247 _assemble_path_b_trace`; `config.py website_trace`; `scripts/trace_website.py` | `test_website_resolver.py::TestWebsiteTraceFlag` | implemented |
| X-10 | Registrable `domain` derivation (ROR → website-derived → source_url-derived) | `orchestrator.py` (`extract_domain` of website; source_url fallback in `finalise`) | `test_domain_from_website.py` | implemented |
| X-11 | Department-domain probe: subdomain construction, homepage scrape, site SERP, on-domain path, cross-domain SERP | `orchestrator.py:963 _probe_department_url`; scorer `:167 _score_dept_candidate`; verify `:1345 _verify_candidate_url` | `test_dept_domain_probe.py` | implemented |
| X-12 | Dept-probe generic-path blocklist (§5b) + path canonicality scoring (§5c) | `orchestrator.py:_path_is_generic, _path_canonicality_penalty`; applied at `:2b stage` | `test_dept_domain_probe.py::TestPathGenericAndCanonicality` | implemented |
| X-13 | Dept-probe morphological verification (`physics`↔`physical`) keeping `science.mit.edu` rejected | `orchestrator.py:1397 _needle_hit` (within `_verify_candidate_url`) | none (asserted only via `_seg_matches_needle` in `test_dept_domain_probe.py`) | partial — the ≥5-char common-prefix rule has no direct verify-level test |
| X-14 | Dept-probe base: subdomain-aware + redirect-resolved, cached per batch | `orchestrator.py:923 _resolve_probe_base`; `page_fetcher.py resolve_final_url`; `cache.py get/set_resolved_host` | `test_dept_domain_probe.py::TestProbeBaseResolution` | implemented |
| X-15 | Admin-desk suppression of the dept probe (no fetch/SERP) | `orchestrator.py` (probe precondition using `is_admin_unit`) | `test_search_terms_fixes.py` (is_admin_unit); probe path via `test_dept_domain_probe.py` | implemented |
| X-16 | Person-only Name 1 → Stage 2b affiliation: propose org, ROR-confirm in-country, else flag | `enrichment/person_affiliation.py:run_person_affiliation`; `orchestrator.py:1413 _resolve_person_affiliation` | `test_person_affiliation.py`, `test_person_affiliation_guard.py`, `test_person_in_name1_flag.py` | implemented (a prior web-only variant was reverted — see 09_DECISIONS) |
| X-17 | ROR acronym currency selection (current initials over historical acronym) | `tier1_ror.py:490-501`; `text_utils.py:927 name_initials, :944 acronym_matches_name` | `test_search_terms_fixes.py::TestRorAcronymCurrency` | implemented |
| X-18 | Standardise a Name 1 kept over ROR's divergent official form (`clean_passthrough_org_name`) | `orchestrator.py:2018` | `test_ror_name_verbatim.py::test_stuttgart_univ_standardised_on_drop, ::test_allcaps_input_titlecased_on_drop` | implemented |
| X-19 | Street "scope-table" reduction: building/floor/room/suite/mail/care-of/campus routing to own fields; Street 1 to one line | `enrichment/address_processing.py:process_address` | `test_street_scope_table.py`, `test_address_cleanup.py`, `test_street_qualifier_split.py` | implemented |
| X-20 | Street pipe/comma/semicolon splitters routing org/dept segments to the Name block, cleaning the source | `preprocess.py:1288-1389` (routers), splitter helpers | `test_pipe_splitter_inversion.py`, `test_street_org_split.py`, `test_street_scope_routing.py`, `test_person_org_in_street.py` | implemented |
| X-21 | Name 1 acronym/full-form dedupe (`MIT Massachusetts…`, dash forms `MRC - …`) | `preprocess.py` (`_strip_redundant_acronym`, dash-acronym helpers) | `test_acronym_dedupe.py` | implemented |
| X-22 | `smart_title_case`: ALL-CAPS → title case, preserving acronyms / `Mc` surnames / hyphen segments | `text_utils.py smart_title_case` | `test_smart_title_case.py` | implemented |
| X-23 | ROR US state-abbreviation expansion for the ROR query only (`Fla`→`Florida`) | `tier1_ror.py` (`_US_STATE_ABBREVS`, `_expand_state_abbrevs`) | `test_ror_state_abbrev.py` | implemented |
| X-24 | ROR country guard (reject a same-name org in the wrong country) | `tier1_ror.py` (`_country_ok`, country-filtered `call`) | `test_tier1_ror_country.py` | implemented |
| X-25 | ROR identity guard (`canonical_preserves_identity`) — keep the fuller input over a token-dropping ROR name | `orchestrator.py:2007`; `text_utils.py canonical_preserves_identity` | `test_ror_name_verbatim.py`, `test_canonical_identity.py` | implemented |
| X-26 | Named-building extraction from name/street to the Building field | `preprocess.py:712 _named_building`; `address_processing.py:_named_building_value` | `test_named_building.py`, `test_street_scope_table.py` | implemented |
| X-27 | Request logging + rotating file logging middleware | `api/middleware.py:RequestLoggingMiddleware, configure_logging` | none | implemented |
| X-28 | TLS/CA sanitisation for corporate VPN (override bogus `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`) | `config.py:27-67 _sanitize_ssl_env` | none | implemented |
| X-29 | Process-level SERP cache shared across batches | `utils/cache.py:SerpCache` | `test_cache.py` | implemented |
| X-30 | `/api/dedup/score` ↔ `/score/file` identical column contract | `dedup/scoring_xlsx.py`; `dedup/scoring.py` | `test_scoring.py` | implemented |
| X-31 | Tier 2B department search from the institution's website (SERP + on-domain ranking + LLM extraction from structured page elements), for records where the contact-based path does not apply | `enrichment/tier2b_dept.py:50 run_tier2b` | `test_tier2b.py` (direct calls only) | **not implemented** — module complete but never invoked: no call site and no import in `enrichment/orchestrator.py:37-59`; wired in `f77080b`, unwired in `635d5ba` (see `09_DECISIONS.md` D-1) |

⚠ UNVERIFIED — Table 2 was compiled from the subsystems visible in Passes 0–1 and prior
change history; a guaranteed-complete enumeration of every un-catalogued behaviour would
require the full algorithm walk of Pass 3. Additional X-items may surface there and should be
back-filled here.

---

## Discrepancies (code ↔ requirement list)

Recorded here and to be carried into `08_GAPS.md`:

1. **UC 14–17 absent from the README use-case table.** Defined and tagged in
   `enrichment/preprocess.py` (`:612,633,1560,1704`) but not listed in `README.md:655-670`.
2. **UC 13 tags two behaviours.** README/`orchestrator.py` = lab→parent resolution;
   `preprocess.py:1664` comment = "Name 3 residual junk cleanup".
3. **UC 1 undefined.** The use-case sequence skips 1 in both README and code.
4. **Two catalogue issue codes are never emitted deterministically** — `G1-ADDR-009`
   (`issue_detection.py:88`) and `G4-ADDR-025` (`issue_detection.py:112`), both annotated
   "LLM-only — never emitted".
5. **`README.md` cites `enrichment/classifier.py` for classification** (Record Classification
   Logic), but that module is a REMOVED stub (`classifier.py:1-12`); classification is derived
   from ROR org types in `tier1_ror.py`/`orchestrator.py` (also recorded in Pass 0 §4).
6. **Tier 2A verification mode is unreachable by construction** (added 2026-08-17). The gate at
   `enrichment/orchestrator.py:2451-2457` requires Name 2 blank; the mode selector at
   `enrichment/tier2a_contact.py:80` requires it populated. `tests/test_tier2a_verification.py`
   exercises the mode by direct call, so the suite passes while no pipeline path reaches it — a
   test-coverage signal that does not imply reachability.
7. **`enrichment/tier2b_dept.py:1-11` describes a role the module cannot fill** (added
   2026-08-17). Its docstring states it is used "when Tier 2A is not applicable … or when name2
   is already filled and needs normalization", but the module has no call site. Both cases it
   names are consequently unserved by any web-evidence path.
8. **Enrichment cannot correct an incorrect existing Name 2** (added 2026-08-17). Both paths
   that would act on a populated Name 2 against retrieved evidence — Tier 2A Mode B and Tier 2B
   — are unreachable, so `enrichment_status="verified"`
   (`enrichment/tier2a_contact.py:459`) and `source="contact_lookup_corrected"` (`:479`) can
   never appear in output despite being declared values.

Stop.
