Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 01

# Pass 01 — Requirements traceability

Tree state: `git status --porcelain` produces no output. `docs/thesis/00_INVENTORY.md`, the
Pass 00 output of this documentation run, is written but the pass is read against
`ea6f9d92168d3de949d369ed54a58b5a745a59b7` in every case. No tracked source, test, SQL or
configuration file differs from that commit.

No Python source file changed between the previous documentation run's commit (`eb924e6`)
and this one — `git diff --stat eb924e6..HEAD` lists four new `adf/*.json`, four `sql/*.sql`,
four moved workbooks and three `docs/` files, and nothing else. Every code citation in this
pass therefore addresses the same bytes it did at the previous run; the additions to the
traced set are the ADF and SQL artefacts, which no requirement covers (§1.4, U-30 and U-31).

## 1.1 Requirement-ID systems present in the repository

Four ID systems carry requirement-like identity at this commit. Each is named, not invented.

| system | where declared | range at this commit |
|---|---|---|
| Use cases (`UC n`) | `README.md:1332` ("Use Case Reference Table"), table at `README.md:1336–1350`, plus section headers and `ResultState.note(uc, reason)` tags in `enrichment/preprocess.py:135–138` | README declares UC 0 and UC 2–13. Code additionally emits UC 14, 15, 16, 17. UC 1 is declared nowhere. |
| Issue Catalogue codes (`Gn-…`) | `ISSUE_CATALOGUE`, `enrichment/issue_detection.py:260–423` | 43 declared: 33 `status="live"`, 10 `status="withdrawn"`. Groups G1–G7. |
| Fix identifiers (`Fix n`, `Fix A–D`) | Comment and docstring markers throughout `enrichment/` | `Fix 1`–`Fix 10`, `Fix A`–`Fix D`. Change-tracking labels, not requirement statements: no file enumerates them, and none carries acceptance criteria. Not traced as requirements here. |
| Review items (`Item n`) | Test docstrings only (e.g. `tests/test_smart_title_case.py:1`, `tests/test_pipe_splitter_inversion.py:1`) | `Item 1`–`Item 9`. Labels for a review list that is not in the repository. Not traced as requirements here. |

**`FR-1 … FR-36` do not exist in this repository.** `grep -rn 'FR-[0-9]'` over the whole
tree returns hits only inside `docs/` — `docs/thesis-doc-prompt-v2.md:66` (the pass
specification itself) and the superseded `docs/thesis/01_TRACEABILITY.md`, which quotes it.
No source file, test, README section, SQL file or configuration declares an `FR-` identifier.
⚠ The functional-requirement numbering the pass specification anticipates is not a repository
artefact; §1.5 (⚠-13) records this.

`EP-`, `DD-` and `X-` identifiers likewise return no hits outside `docs/thesis/`. They appear
only in the superseded `docs/thesis/01_TRACEABILITY.md`, which assigned them itself. This
pass does not carry them forward.

## 1.2 Table 1a — Use cases

`Implemented in` cites the code that performs the behaviour and, where the run records the
use case, the site that appends the number to `use_cases_triggered`
(`enrichment/orchestrator.py:810` initialises the list). `Test` cites a test file whose
subject is the behaviour; `none` where no test file has it as its subject.

| ID | requirement (README wording, verbatim where quoted) | implemented in | test | status |
|---|---|---|---|---|
| UC 0 | "Name1 Overflow Detection … Both Name1 + Name2 non-blank → LLM checks if it's one split name; flags if yes" (`README.md:1338`) | `run_overflow_check_block` `enrichment/overflow_check.py:154`, called `enrichment/orchestrator.py:7770`; repair by `enrichment/name_repack.py`; prompt `llm/prompts.py:7`; recorded `enrichment/orchestrator.py:7818–7819` | `tests/test_name_repack.py`, `tests/test_output_casing.py` | implemented |
| UC 1 | — no requirement text exists. The number is skipped by `README.md:1338–1350` and never passed to `note()` or appended to `use_cases_triggered`. | none | none | not implemented |
| UC 2 | "Institution ROR Resolution … ROR match found → Enriches Name1 with official ROR name" (`README.md:1339`) | `RORClient.call` `enrichment/tier1_ror.py:1769`, called `enrichment/orchestrator.py:8103`; recorded `enrichment/orchestrator.py:6017–6018`, `:7713–7714`, `:8276–8277`, `:8567–8568` | `tests/test_tier1.py`, `tests/test_registry_name_authority.py` (neither names "UC 2") | implemented |
| UC 3 | "Company Name Canonicalization … GLEIF/LEI registry lookup first (official legal name + `lei_id`); LLM canonicalization with geographic context as the fallback when LEI misses" (`README.md:1340`) | `Orchestrator._run_lei_lookup` `enrichment/orchestrator.py:7621` over `enrichment/tier1_lei.py`; LLM fallback `run_company_canonical` `enrichment/company_canonical.py:56`, called `enrichment/orchestrator.py:8436`; recorded `enrichment/orchestrator.py:6095–6096`, `:7715–7716`, `:8278–8279`, `:8569–8570` | `tests/test_tier1_lei.py`, `tests/test_canonical_identity.py` (neither names "UC 3") | implemented |
| UC 4 | "Contact Lookup with Scope Filter … Contact present, ROR hit, domain known → Discovers/verifies Name2 from contact's faculty page" (`README.md:1341`) | `run_tier2a` `enrichment/tier2a_contact.py:70`, mode chosen at `:90` (`"2A_population"` when Name 2 blank, `"2A_verification"` otherwise); gate `can_do_contact_lookup` `enrichment/orchestrator.py:8778–8783`; called `enrichment/orchestrator.py:9041`; recorded `:9108–9109` | `tests/test_tier2a_population.py`, `tests/test_tier2a_verification.py` | implemented |
| UC 5 | "Department Canonicalization … Name2 present, ROR hit, no child match → LLM normalizes department name to official wording" (`README.md:1342`) | `run_tier2_canonical` `enrichment/tier2_canonical.py:179`, called `enrichment/orchestrator.py:8864` and `:9083`; gate `can_canonical` `enrichment/orchestrator.py:8790–8793`; scope filter `utils/text_utils.py:1016–1018`; recorded `enrichment/orchestrator.py:8968–8969` | `tests/test_tier2_canonical_downgrade.py`, `tests/test_tier2_canonical_medium.py` | implemented |
| UC 6 | "Accounts Payable Recognition … AP pattern detected → Flags as accounts payable for special handling" (`README.md:1343`) | `enrichment/preprocess.py:181` (section), `res.note(6, …)` `enrichment/preprocess.py:2250`, applied `:2216`; orchestrator write path `enrichment/orchestrator.py:8620–8639`, evidence tag `"uc6:accounts-payable-normalised"` `:8630` | `tests/test_ap_desk_split.py` | implemented |
| UC 7 | "Contact Person Extraction … Person name in name fields → Moves person name to `contact` field" (`README.md:1344`) | `enrichment/preprocess.py:1605` (section), `res.note(7, …)` `:2208`, `:2212`, `:2510`, `:2514`; Pattern A loop `:2160–2173`, Pattern B2 LLM classifier `:1830` | `tests/test_person_in_name1.py`, `tests/test_person_org_in_street.py` | implemented |
| UC 8 | "Email Copy (Non-Destructive) … Email address in name or address fields → Copies email to `email` field; source preserved" (`README.md:1345`) | `enrichment/preprocess.py:315` (section), `res.note(8, …)` `:2282`, `:2299`; applied `:2253`; accumulator `ResultState.add_email` `enrichment/preprocess.py:140` | `tests/test_multiple_emails.py` | implemented |
| UC 9 | "Address Extraction … Street address in name fields → Moves address to `street1`/`street2`/`street3` fields" (`README.md:1346`) | `enrichment/preprocess.py:371` (section), `res.note(9, …)` `:1961`, `:1979`, `:1983`, `:2362`, `:2366`, `:2370`; applied `:2302`, `:2347` | `tests/test_street_org_split.py`, `tests/test_address_in_name_slot.py` | implemented |
| UC 10 | "Opaque Code Detection … Internal code/ID in name fields → Flags as non-name data" (`README.md:1347`) | `enrichment/preprocess.py:692` (section), `res.note(10, …)` `:1864`, `:2468`; applied `:2463` | `tests/test_leading_code_strip.py` | implemented |
| UC 11 | "DBA Normalization … 'Doing Business As' variant in name fields → Rewrites variant to canonical 'DBA'" (`README.md:1348`) | `enrichment/preprocess.py:620` (section), `_normalise_dba` referenced `:2126–2132`, `res.note(11, …)` `:2445`; applied `:2435` | `tests/test_dept_block.py` (DBA marker behaviour, `tests/test_dept_block.py:738–739`) | implemented |
| UC 12 | "Duplicate Name Clearing … Name1==Name2 or Name2==Name3 (case/whitespace insensitive) → Silently clears the duplicate field" (`README.md:1349`) | `enrichment/preprocess.py:2635` (section), `res.note(12, …)` `:1876`, `:1935`, `:2403`, `:2407`, `:2432`; bracketed-span rule `:2374–2377` | `tests/test_canonical_dedup.py`, `tests/test_strip_parentheticals.py` | implemented |
| UC 13 | "Lab → Parent Department Resolution … Name2 is a granular unit (lab/group/centre/core/facility) at a research institution with a known domain → SERP + page fetch + LLM extracts the parent academic department; parent → Name2, lab → Name3 (when Name3 empty)" (`README.md:1350`) | `run_lab_resolver` `enrichment/lab_resolver.py:48`, called `enrichment/orchestrator.py:8667`; prompt `llm/prompts.py:151`; recorded `enrichment/orchestrator.py:8759–8760`, `:8768–8769` | `tests/test_lab_resolver.py` | implemented — but the number is shared, see ⚠-14 |
| UC 14 | Name-slot consolidation: the name block is packed leftward after extraction so a slot emptied by UC 7/8/9 is not left as a hole. Declared in code only (`enrichment/preprocess.py:2588–2596`); absent from the README table. | `enrichment/preprocess.py:2588` (section), `res.note(14, …)` `:2612`, `:2630` | `tests/test_preprocess_populated_slot.py` | implemented |
| UC 15 | c/o and ATTN extraction from the department slots, as a five-case classifier. Declared in code only (`enrichment/preprocess.py:1133`); absent from the README table. | `enrichment/preprocess.py:1133` (section), `res.note(15, …)` `:1465`, `:1488`, `:1497`, `:1504`, `:1511`, `:1544`, `:1559`, `:1565`; applied `:1938–1939` | `tests/test_uc15_co_attn.py`, `tests/test_preprocess_co_attn.py` | implemented |
| UC 16 | Institution + embedded department split in Name 1. Declared in code only (`enrichment/preprocess.py:1112`); absent from the README table. | `enrichment/preprocess.py:1112` (section), `res.note(16, …)` `:2024`, `:2027`, `:2047`, `:2050`, `:2072`, `:2080`, `:2100`, `:2112`, `:2156`, `:2541`, `:3095`, `:3104`, `:3108`; applied `:2522` | `tests/test_preprocess_co_attn.py:328` | implemented |
| UC 17 | Long-form legal-suffix normalisation. Declared in code only (`enrichment/preprocess.py:2448`); absent from the README table. | `enrichment/preprocess.py:2448` (section), `res.note(17, …)` `:2460`; output-side mirror `utils/text_utils.py:1297` | `tests/test_legal_suffix_normalisation.py` | implemented |

Use-case numbers the running pipeline can actually record, taken from every write site:
0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17. `res.note()` in
`enrichment/preprocess.py` is passed 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17
(`grep -o 'note(\s*[0-9]\+' enrichment/preprocess.py`); the orchestrator appends 0, 2, 3, 4,
5 and 13 directly. UC 1 is written by nothing.

## 1.3 Table 1b — Issue Catalogue codes

`status="live"` maps to `implemented`; `status="withdrawn"` maps to `superseded`, with the
declared reason quoted. Every declared code is named by at least one test. Detection sites
are the raise points; a code marked "flag route" is raised from the enriched record's
`Flag Codes` column through `FLAG_CODE_ISSUES` (`enrichment/issue_detection.py:1515–1559`)
rather than from record content.

| ID | requirement (catalogue `name`, verbatim) | implemented in | test | status |
|---|---|---|---|---|
| `G1-CROSS-001` | Address Content in Name Field | `_detect_wrong_field` `enrichment/issue_detection.py:1056` | `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G1-CROSS-002` | Org Name in Address Field | `_detect_wrong_field` `enrichment/issue_detection.py:1070` | `tests/test_issue_detection.py` | implemented |
| `G1-CROSS-003` | Contact Information in Wrong Field | `_detect_wrong_field` `enrichment/issue_detection.py:1077`, `:1082` | `tests/test_issue_detection.py` | implemented |
| `G1-ADDR-001` | House Number Embedded in Street | `_detect_wrong_field` `enrichment/issue_detection.py:1090` | `tests/test_issue_detection.py` | implemented |
| `G1-ADDR-003` | Sub-location Embedded in Street | `_detect_wrong_field` `enrichment/issue_detection.py:1099` | `tests/test_issue_detection.py` | implemented |
| `G1-ADDR-004` | PO Box Embedded in Street | `_detect_wrong_field` `enrichment/issue_detection.py:1105` | `tests/test_issue_detection.py` | implemented |
| `G1-ADDR-006` | Mail Code in Street Field | `_detect_wrong_field` `enrichment/issue_detection.py:1128`, `:1134` | `tests/test_issue_detection.py` | implemented |
| `G1-ADDR-011` | Department Label in Street Field | `_detect_wrong_field` `enrichment/issue_detection.py:1140` | `tests/test_issue_detection.py` | implemented |
| `G1-NAME-001` | Name Overflow Across Fields | none — declaration only, `enrichment/issue_detection.py:270–274` | `tests/test_issue_catalogue_coverage.py`, `tests/test_issue_detection.py`, `tests/test_name_slot_parity.py` | superseded — withdrawn 2026-09-07; the deterministic heuristic was a proxy for a rule that is LLM-only |
| `G1-NAME-004` | Empty field in between populated name fields | `_detect_wrong_field` `enrichment/issue_detection.py:1157` | `tests/test_issue_detection.py`, `tests/test_name_slot_parity.py` | implemented |
| `G1-NAME-013` | SAP Internal Code in Name Field | `_detect_wrong_field` `enrichment/issue_detection.py:1163`; flag route `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1527` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G1-ADDR-009` | Unclassified Residual in Address | none — declaration only, `enrichment/issue_detection.py:283–287` | `tests/test_issue_detection.py` | superseded — ndd, never emitted; residual classifier not called by /issues |
| `G2-VAL-001` | Name 1 Missing | `_REQUIRED_FIELD_CODES` `enrichment/issue_detection.py:497`, consumed `_detect_missing` `:1189` | `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G2-VAL-002` | Postal Code Missing | `_REQUIRED_FIELD_CODES` `enrichment/issue_detection.py:498`, consumed `_detect_missing` `:1189` | `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G2-VAL-004` | Region Missing | `_REQUIRED_FIELD_CODES` `enrichment/issue_detection.py:499`, consumed `_detect_missing` `:1189` | `tests/test_issue_detection.py` | implemented |
| `G2-VAL-007` | Search Term 1 Missing | `_REQUIRED_FIELD_CODES` `enrichment/issue_detection.py:500`, consumed `_detect_missing` `:1189` | `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G2-VAL-008` | Country Missing | `_REQUIRED_FIELD_CODES` `enrichment/issue_detection.py:501`, consumed `_detect_missing` `:1189` | `tests/test_issue_detection.py` | implemented |
| `G2-NAME-009` | Lab Without Department | `_detect_missing` `enrichment/issue_detection.py:1266` | `tests/test_issue_detection.py` | implemented |
| `G2-NAME-012` | Research Institution Missing Department (Name 2 blank or holds only an administrative desk) | `_detect_missing` `enrichment/issue_detection.py:1254` | `tests/test_issue_detection.py`, `tests/test_name_slot_parity.py`, `tests/test_routes.py` | implemented |
| `G2-CONTACT-008` | No Contact and No Department | none — declaration only, `enrichment/issue_detection.py:305–312` | `tests/test_issue_detection.py` | superseded — Struck through in Catalogue v2. Its gate was identical to G2-NAME-012's, so it could never carry information the latter had not already reported. |
| `G2-CONTACT-009` | Department Missing And Enrichable from Contact | none — declaration only, `enrichment/issue_detection.py:313–322` | `tests/test_issue_detection.py` | superseded — Struck through in Catalogue v2. Withdrawing it removed the contact-based (Tier 2A) department recovery path, which is why G2-NAME-012 now sits in G6 — no automated route to a department remains. |
| `G3-NAME-003` | DBA Pattern in Name Field | `_detect_duplicate` `enrichment/issue_detection.py:1289` | `tests/test_issue_detection.py` | implemented |
| `G3-NAME-005` | Duplicate Name Across Fields | `_detect_duplicate` `enrichment/issue_detection.py:1297` | `tests/test_issue_detection.py`, `tests/test_name_slot_parity.py` | implemented |
| `G3-NAME-006` | Site Qualifier in Name Conflicts With Address | flag route only — `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1530` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py` | implemented |
| `G3-ADDR-005` | Multiple PO Boxes on Record | `_detect_duplicate` `enrichment/issue_detection.py:1309` | `tests/test_issue_detection.py` | implemented |
| `G3-ADDR-012` | Duplicate Street Across Fields | `_detect_duplicate` `enrichment/issue_detection.py:1325` | `tests/test_issue_detection.py`, `tests/test_street_fragment_dedup.py` | implemented |
| `G3-ADDR-013` | Two Distinct Street Addresses on Record | `_detect_duplicate` `enrichment/issue_detection.py:1351` | `tests/test_issue_catalogue_coverage.py`, `tests/test_issue_detection.py` | implemented |
| `G3-ADDR-014` | PO Box and Street Both Present | `_detect_duplicate` `enrichment/issue_detection.py:1355` | `tests/test_issue_detection.py` | implemented |
| `G3-CONTACT-007` | Multiple Contacts on Record | `_detect_duplicate` `enrichment/issue_detection.py:1359`; flag route `:1528` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py` | implemented |
| `G3-CONTACT-010` | Multiple Email Addresses on Record | `_detect_duplicate` `enrichment/issue_detection.py:1379`; flag route `:1529` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G4-NAME-015` | Name Overflow Beyond the Name Block | `_detect_format` `enrichment/issue_detection.py:1390`; flag route `:1554` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py` | implemented |
| `G4-ADDR-008` | Bare Sub-location Marker Without Value | none — declaration only, `enrichment/issue_detection.py:353–361` | `tests/test_issue_detection.py`, `tests/test_mail_stop_variants.py` | superseded — a bare marker cannot be separated from a named building ('810 R L Smith Bldg') without a building-name source; false positives on campus addresses outweigh the true hits |
| `G4-ADDR-025` | Sub-location Overflow Beyond Street 5 | none — declaration only, `enrichment/issue_detection.py:362–366` | `tests/test_issue_detection.py` | superseded — >4 sub-locations, 0/500 observed; `overflow` covers spill |
| `G4-ADDR-026` | Postal Code Format Invalid | `_detect_format` `enrichment/issue_detection.py:1397` | `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G4-ADDR-027` | Country Code Not ISO 2-letter | `_detect_format` `enrichment/issue_detection.py:1404` | `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G5-NAME-001` | Organisation Name Not in Official Form | `_detect_naming` `enrichment/issue_detection.py:1437` | `tests/test_issue_catalogue_coverage.py`, `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G5-NAME-002` | Unit Name Not in Official Form | `_detect_naming` `enrichment/issue_detection.py:1446` | `tests/test_issue_catalogue_coverage.py`, `tests/test_issue_detection.py`, `tests/test_name_slot_parity.py` | implemented |
| `G2-VAL-003` | Tax Jurisdiction Missing | none — declaration only, `enrichment/issue_detection.py:380–384` (declared group `"G6"`, not `G2`) | `tests/test_issue_detection.py`, `tests/test_routes.py` | superseded — SAP-derived field, 65% blank, not master-data scope |
| `G2-VAL-006` | Language Missing | none — declaration only, `enrichment/issue_detection.py:385–389` (declared group `"G6"`, not `G2`) | `tests/test_issue_detection.py`, `tests/test_routes.py` | superseded — 99% populated, no defect class |
| `G6-RESOLVE-001` | Enrichment Could Not Resolve the Record | none — declaration only, `enrichment/issue_detection.py:390–395` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py`, `tests/test_routes.py` | superseded — group G6 (not resolvable) dissolved; members re-homed in Step D |
| `G6-CONFIRM-001` | Enriched Value Requires Confirmation | flag route only — `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1535–1539` | `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |
| `G7-VERIFY-001` | Enriched Record Requires Verification | none — declaration only, `enrichment/issue_detection.py:410–415` | `tests/test_issue_detection.py`, `tests/test_routes.py` | superseded — routing now carried by group membership (already decided 2026-09-02) |
| `G7-UNCHANGED-001` | Enrichment Left the Value Unestablished | flag route only — `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1556–1558` | `tests/test_determinism.py`, `tests/test_flag_issue_alignment.py`, `tests/test_issue_detection.py`, `tests/test_routes.py` | implemented |

Counts, from `ISSUE_CATALOGUE` at `enrichment/issue_detection.py:260–423`: 43 declared,
33 live (implemented), 10 withdrawn (superseded). `EMITTED_CODES`
(`enrichment/issue_detection.py:426–428`) selects codes whose status is `"live"` or
`"unlisted"`; no code carries `"unlisted"` at this commit, so it resolves to exactly the 33
live codes.

Group membership is the `group` field of the declaration, **not** the code's prefix. The two
disagree on two codes — `G2-VAL-003` and `G2-VAL-006` are declared with group `"G6"`
(`enrichment/issue_detection.py:380`, `:385`) — so a per-group census computed from code
prefixes returns different numbers from one computed from the declared field. See ⚠-19.

Per group, live / declared, counted on the declared `group` field:
G1 10/12 · G2 7/9 · G3 9/9 · G4 3/5 · G5 2/2 · G6 1/4 · G7 1/2.
Counted on the code prefix instead: G1 10/12 · G2 7/11 · G3 9/9 · G4 3/5 · G5 2/2 ·
G6 1/2 · G7 1/2.

Three live codes have no content detector and can be raised only from `Flag Codes`
(`G3-NAME-006`, `G6-CONFIRM-001`, `G7-UNCHANGED-001`); `FLAG_CODE_ISSUES` maps 13 flag
tokens onto 7 distinct codes (`G1-NAME-013`, `G3-CONTACT-007`, `G3-CONTACT-010`,
`G3-NAME-006`, `G4-NAME-015`, `G6-CONFIRM-001`, `G7-UNCHANGED-001`).

## 1.4 Table 2 — Behaviour in code with no requirement

Substantive behaviour that no `UC` number and no Issue-Catalogue code states. The `U-n`
labels are **assigned by this document** for cross-referencing within the thesis; they are
not repository identifiers and appear in no source file. Ordered by subsystem.

| ID | behaviour | implemented in | test | note |
|---|---|---|---|---|
| U-1 | Deduplication: signature construction, delivery-point blocking, Mode A / Mode B adjudication, residue widening, identity and address split guards, `Link ID` assignment | `dedup/adjudicator.py:1450` (`cluster_blocks`), `dedup/signatures.py:260`, `:302`, `dedup/address.py:160`, `dedup/candidates.py:216`, `dedup/name_slots.py:345` | `tests/test_dedup.py` (5 failing, Pass 00 §0.7.2), `tests/test_dedup_v2*.py`, `tests/test_candidates.py` | Whole Phase 2 "Pass 2". No UC covers it. |
| U-2 | Golden-record scoring and election: weighted per-row score, cluster year maxima, tie-break, `election_status`, merge confidence | `dedup/scoring.py:903` (`score_row`), `:1151` (`elect_golden_records`), `:1048`, `:1097`, `:1138`; weights `dedup/weights.json` | `tests/test_scoring.py` (210/210 pass) | Phase 2 "Pass 3". Raises its own issues via `dedup/scoring.py:485`, a second issue vocabulary distinct from `ISSUE_CATALOGUE` — see ⚠-15. |
| U-3 | Steward approve/reject on a proposed cluster, with promotion of the winner into the golden fields | `dedup/scoring.py:605` (`apply_approval`), route `api/routes.py:1488` | `tests/test_scoring.py` | Stateless by design (`api/routes.py:1494–1495`): no durable approval store. |
| U-4 | Row-grain → customer-grain consolidation of company codes and sales orgs | `dedup/consolidate.py:355` (`consolidate_rows`), `:219`, `:238`; route `api/routes.py:974` | `tests/test_preprocess_consolidate.py` (51/51 pass) | Named in the pass specification as an endpoint to check for, but stated by no requirement. |
| U-5 | Before/after issue-reduction reporting across two workbooks | `api/routes.py:917` (`compare_file_issues`), `:456`, `:503` | `tests/test_routes.py` | The reduction metric the evaluation depends on; no requirement defines it. |
| U-6 | Website resolution Paths A/B/C, including SERP selection and LLM inference of an official site | `enrichment/website_resolver.py:721`, `:875`, `:1007`; orchestrator `enrichment/orchestrator.py:4443` | `tests/test_website_resolver.py` (93/93 pass) | |
| U-7 | Single write path for `domain` / `website_url` plus the domain-ownership guard | `utils/domain_resolver.py:99`, `:121`, `:145`; `_apply_domain` `enrichment/orchestrator.py:3550` | `tests/test_domain_resolver.py` (102/102 pass) | Name-match threshold is a tuned constant; Pass 04 records the value. |
| U-8 | Wikidata crosswalk lane — pointer and witness, never an authority | `enrichment/wikidata.py:420`, `:462`, `:494`; `Orchestrator._wikidata_crosswalk` `enrichment/orchestrator.py:6107`, `_crosswalk_to_ror` `:6233`, `_crosswalk_to_gleif` `:6325` | `tests/test_wikidata.py` (55/55 pass) | Role enforcement is Pass 06's subject. |
| U-9 | Liveness / going-concern check on the named organisation | `enrichment/liveness.py:167`, `:202`, `:223`; `Orchestrator._check_liveness` `enrichment/orchestrator.py:6559` | `tests/test_liveness.py` (38/38 pass) | Produces the `entity-superseded` flag; deliberately not mapped to a catalogue code (`enrichment/issue_detection.py:1508–1514`). |
| U-10 | Batch consensus — one identity per organisation per address, applied after every record is finalised | `enrichment/batch_consensus.py:611` (`apply_batch_consensus`), called `enrichment/orchestrator.py:4351` | `tests/test_batch_consensus.py` (69/69 pass) | Cross-record behaviour; every UC is per-record. |
| U-11 | Per-field provenance and admissibility (Scheme B: `source:confidence[+witness]`) | `enrichment/provenance.py:436`, `enrichment/confidence.py:187`, `:260`, `:274` | `tests/test_provenance.py`, `tests/test_provenance_scheme_b.py` | |
| U-12 | Search-handle derivation (`search_term_1`, `search_term_2`) | `enrichment/search_terms.py:1110` (`derive_search_terms`), called `enrichment/orchestrator.py:3215` | `tests/test_search_terms.py`, `tests/test_search_terms_fixes.py` | `G2-VAL-007` requires Search Term 1 to be present but states nothing about how it is derived. |
| U-13 | Address Stage 1 — clean, extract, route, cross-check, classify, normalise, after the tiers have run | `enrichment/address_processing.py:989` (`process_address`), `:1317`; `Orchestrator._run_address_stage` `enrichment/orchestrator.py:7570` | `tests/test_address_cleanup.py`, `tests/test_street_scope_table.py` | UC 9 covers only extraction out of *name* fields during preprocessing. |
| U-14 | The universal grounded lane — SERP + one LLM read + registry re-verification | `enrichment/grounded_resolver.py:329`, `:526`; called `enrichment/orchestrator.py:9141`; `Orchestrator._grounded_fallthrough` `:6984` | `tests/test_grounded_resolver.py` (27/27 pass) | |
| U-15 | Page corroboration — read the candidate website and test whether it names this record | `enrichment/page_corroborator.py:244`, `:347`, `:427`; `Orchestrator._corroborate_domain` `enrichment/orchestrator.py:7126` | `tests/test_page_corroborator.py` (48/48 pass) | |
| U-16 | One write gate for every Name 1 / Name 2 candidate | `enrichment/name_gate.py:171` (`evaluate`), `enrichment/orchestrator.py:1049` (`_write_registry_name`) | `tests/test_llm_name_authoritative.py`, `tests/test_name_identity_verdicts.py` | |
| U-17 | Three-verdict identity comparison for a proposed canonical name | `utils/name_identity.py:324`, `:441`, `:490` | `tests/test_name_identity_verdicts.py` (32/32 pass) | |
| U-18 | The three states an unchanged Name 1 can be in | `enrichment/unchanged_state.py:206` (`resolve`), `:306`, `:328` | `tests/test_unchanged_state.py` (22/22 pass) | |
| U-19 | Review flags computed once from the record's final state | `enrichment/flags.py:1059` (`compute_flags`), called `enrichment/orchestrator.py:3173` | `tests/test_flags.py` (253/253 pass), `tests/test_flag_issue_alignment.py` | The flag vocabulary is the input to `FLAG_CODE_ISSUES`; four flag tokens are deliberately not mapped to any code (`enrichment/issue_detection.py:1499–1514`). |
| U-20 | Record classification — the single authority for `record_type`, and the provisional `routing_type` that gates the tiers | `enrichment/classifier.py:172` (`classify`), `enrichment/elf_codes.py`; `_classify_record` `enrichment/orchestrator.py:3458` | `tests/test_classifier.py`, `tests/test_record_type_authority.py` (41/41 pass) | Documented at `README.md:1356–1400`, but as mechanism, not as a numbered requirement. |
| U-21 | The department block (Name 2..5) as one value with one authority | `enrichment/dept_block.py:207` (`classify`), `:246` (`normalise`) | `tests/test_dept_block.py` (130/132 pass, 2 skip) | |
| U-22 | Cross-source consistency gate — no record ships two contradictory identities | `enrichment/consistency.py:119` (`apply_cross_source_gate`), counters `:206`, `:325`, reset `:330` | `tests/test_determinism.py`, `tests/test_calibration.py` | |
| U-23 | One locality comparator shared by the page read and the registries | `enrichment/locality.py:79`, `:112`, `:147` | `tests/test_address_in_name_slot.py`, `tests/test_calibration.py` | |
| U-24 | Registry candidate acceptance — which candidate wins and whether any does | `enrichment/registry_match.py:90`, `:99`, `:129`, `:138` | `tests/test_registry_name_authority.py` (119/119 pass), `tests/test_ror_short_distinctive_token.py` | |
| U-25 | Person-affiliation lookup (Stage 2b) — a person-only Name 1 gets its institution from the record's own evidence | `enrichment/person_affiliation.py:104` (`run_person_affiliation`); `Orchestrator._resolve_person_affiliation` `enrichment/orchestrator.py:5163`, called `:8058` | `tests/test_person_affiliation.py`, `tests/test_person_affiliation_guard.py` | UC 7 moves a person to `contact`; recovering the institution is a separate behaviour. |
| U-26 | The evidence cache: namespaced record/replay store keyed on the request, with a frozen mode | `utils/cache.py:131`, `:150`, `:556`; namespaces described `.gitignore:28–40` | `tests/test_cache.py`, `tests/test_cache_normalisation.py` | `CACHE_FROZEN` semantics are Pass 06's subject. |
| U-27 | Reproducibility gate — diff two enrichment runs of the same batch | `tools/run_diff.py:82`, `:142`, `:196`, `:231` | none — no test file has `tools/run_diff.py` as its subject | Pass 18 must run it to report determinism. See ⚠-22. |
| U-28 | Department/division search via SERP + LLM extraction ("Tier 2B") | `enrichment/tier2b_dept.py:48` (`run_tier2b`) | `tests/test_tier2b.py` (4/4 pass) | **Present but unwired.** `run_tier2b` is imported only by `tests/test_tier2b.py:13`; the orchestrator never imports or calls it. `tier2_mode` is written only at `enrichment/orchestrator.py:3854` from `Tier2AResult.mode`, which is `"2A_population"` or `"2A_verification"` (`enrichment/tier2a_contact.py:90`) and never `"2B"`, so the counter `summary.tier2b_count` (`api/models.py:850`, incremented `enrichment/orchestrator.py:9305`) can never be non-zero. See ⚠-16. |
| U-29 | Diagnostic and configuration endpoints (`/health`, `/tiers`, `/diag/llm`, `/diag/dedup-llm`) | `api/routes.py:94`, `:1647`, `:1576`, `:1608` | `tests/test_routes.py` | `/diag/llm` and `/diag/dedup-llm` each make a live model call and return the raw error string in the response body (`api/routes.py:1579–1580`; `:1609–1613`). |
| U-30 | ADF orchestration of four of the sixteen routes: a Lookup that selects the batch, a Web activity that posts it, and a stored-procedure activity that merges the answer back — parameterised on `chrEntity` and `chrGroupCode` | `adf/enrichment_pipeline.json:1` (`/enrich`, inside a `ForEach`), `adf/issues_pipeline.json:1` (`/issues/json`), `adf/deduplication_pipeline.json:1` (`/api/dedup/cluster-block`), `adf/scoring_pipeline.json:1` (`/api/dedup/score`) | none — no test reads `adf/` | New at this commit (added in `5a1fd70`). No requirement, README section or code comment states which pipelines must exist, in what order, or with which predicate. Pass 02 reads the definitions in full. |
| U-31 | Merge-back stored procedures: four `CREATE PROCEDURE` bodies taking `@chrEntity SYSNAME`, `@chrGroupCode NVARCHAR(50)` and `@payload NVARCHAR(MAX)` — `usp_MergeLegacyIssues` additionally `@target_column SYSNAME = N'Issues'` | `sql/usp_merge_legacy_enriched.sql:1`, `sql/usp_merge_legacy_issues.sql:1`, `sql/usp_merge_validation_clusters.sql:1`, `sql/usp_merge_validation_scores.sql:1` | none — no test executes SQL | All four declare schema `[Mapping]`; the ADF activities that call them name `dbo` (Pass 00 ⚠-7). Two carry an unresolved `-- <<< confirm` marker on their `@db` declaration. Each file is one physical line, so no statement inside them is separately citable (Pass 00 ⚠-8). |

## 1.5 Discrepancies raised in this pass

Numbering continues from Pass 00 (⚠-1 … ⚠-12). All are carried to `08_GAPS.md`.

| id | severity | statement | code side | other side |
|---|---|---|---|---|
| ⚠-13 | medium | The `FR-1 … FR-36` functional-requirement numbering does not exist in the repository. | `grep -rn 'FR-[0-9]'` over the tree returns hits only inside `docs/` | `docs/thesis-doc-prompt-v2.md:66`: "IDs from the repo's own numbering (UC, FR-1…FR-36, issue codes)". The requirement set the thesis would cite as `FR-n` has no repository source; either it lives outside the repo or the numbering is notional. |
| ⚠-14 | high | The number `13` names two different behaviours and both write it into the same `use_cases_triggered` array, so a consumer cannot tell them apart. | Lab → parent department: `enrichment/lab_resolver.py:48`, recorded `enrichment/orchestrator.py:8759–8760`, `:8768–8769`. Department-slot residual junk cleanup and person-in-slot routing: `enrichment/preprocess.py:2551` (section), `res.note(13, …)` `:2562`, `:2575`, `:2579`, `:2584` | `README.md:1350` declares UC 13 as "Lab → Parent Department Resolution" only. The preprocessing meaning is undeclared. |
| ⚠-15 | medium | Two disjoint issue vocabularies exist and neither references the other. | `ISSUE_CATALOGUE` (`enrichment/issue_detection.py:260–423`, 43 codes) drives `/issues`; `detect_issues` in `dedup/scoring.py:485` emits `DedupIssue` (`dedup/scoring.py:458`) from `/api/dedup/score` | No code maps a scoring issue onto a catalogue code, and no catalogue code is declared for a scoring defect. A thesis-level "issue count" is not well defined across the two. |
| ⚠-16 | medium | Tier 2B exists as a complete module with tests but has no production call site; its telemetry counter is unreachable. | `run_tier2b` `enrichment/tier2b_dept.py:48` is imported only by `tests/test_tier2b.py:13`. `tier2_mode` is written once, `enrichment/orchestrator.py:3854`, from `Tier2AResult.mode` ∈ {`"2A_population"`, `"2A_verification"`} (`enrichment/tier2a_contact.py:90`). `summary.tier2b_count` (`api/models.py:850`) increments only when `r.tier2_mode == "2B"` (`enrichment/orchestrator.py:9304–9305`) | `enrichment/orchestrator.py:8764–8766` names Tier 2B as a downstream option: "the record falls through to tier 2 canonical / 2A / 2B / 3, any of which may settle Name 2". Tier 2B is not among them at this commit. |
| ⚠-17 | low | The `detect_issues` docstring states the number of codes the flag route drives and it is wrong. | `FLAG_CODE_ISSUES` maps 13 tokens onto 7 distinct codes (`enrichment/issue_detection.py:1515–1559`) | `enrichment/issue_detection.py:1680–1681`: "*flag_codes* carries the enriched record's ``Flag Codes`` and drives six codes through :data:`FLAG_CODE_ISSUES`". |
| ⚠-18 | medium | The `detect_issues` docstring describes group G6 under its pre-renumbering meaning. | The one live G6 code is `G6-CONFIRM-001`, origin `API` (`enrichment/issue_detection.py:402–405`). The renumbering is recorded at `enrichment/issue_detection.py:259`: "Renumbered 2026-09-06: old G6 withdrawn, old G7->G6, old G8->G7" | `enrichment/issue_detection.py:1693–1695`: "the before/after reduction narrative is defined over the whole G1-G6 set — of which G6 is entirely DS-origin". No live G6 code is DS-origin. |
| ⚠-19 | medium | Two codes carry a prefix that contradicts their declared group, so a per-group census gives different answers depending on which field it reads. | `_d("G2-VAL-003", "G6", …)` `enrichment/issue_detection.py:380`; `_d("G2-VAL-006", "G6", …)` `enrichment/issue_detection.py:385`. Declared-field census: G6 1/4. Prefix census: G6 1/2, G2 7/11 | Both codes are withdrawn, so no live count moves; but Pass 18's per-group reduction table must state which field it counts on. `issue_group` (`enrichment/issue_detection.py:455`) returns the declared field, so any consumer of that helper gets the G6 reading. |
| ⚠-20 | low | The README use-case table is four use cases out of date. | The code declares and emits UC 14, 15, 16, 17 (`enrichment/preprocess.py:2588`, `:1133`, `:1112`, `:2448`) | `README.md:1336–1350` lists UC 0 and UC 2–13 only. |
| ⚠-21 | low | The UC 4 gate carries a condition the README does not state; the UC 5 gate is wider than the README trigger. | `can_do_contact_lookup` requires `not multi_contact` in addition to research institution, contact present and domain known (`enrichment/orchestrator.py:8778–8783`). `can_canonical` admits `routing_type ∈ {research_institution, company}` with any resolved `name1_enriched` (`enrichment/orchestrator.py:8790–8793`) — a GLEIF-resolved company qualifies | `README.md:1341` states the UC 4 trigger as "Contact present, ROR hit, domain known"; a record with two contacts is silently excluded. `README.md:1342` states the UC 5 trigger as "Name2 present, ROR hit, no child match". |
| ⚠-22 | low | The reproducibility gate has no test. | `tools/run_diff.py:82`, `:142`, `:196`, `:231` | No test module names `tools/run_diff.py` as its subject (Pass 00 §0.7.4). Pass 18 depends on this script for its determinism result. |
| ⚠-23 | medium | The two artefacts that make the pipeline run in production — the ADF pipelines and the merge procedures — are traced to no requirement and covered by no test. | `adf/*.json` (4 files, U-30); `sql/*.sql` (4 files, U-31) | No requirement statement, README section or code comment enumerates the pipelines that must exist or the procedures they must call. Correctness of the write-back path is unverifiable from this repository alone. |

## 1.6 Coverage summary

| requirement set | total | implemented | partial | not implemented | superseded |
|---|---|---|---|---|---|
| Use cases (UC 0–17) | 18 | 17 | 0 | 1 (UC 1 — no requirement text exists) | 0 |
| Issue Catalogue codes | 43 | 33 | 0 | 0 | 10 |
| **Total traced** | **61** | **50** | **0** | **1** | **10** |

Behaviour with no requirement: 31 entries (U-1 … U-31), of which one (U-28, Tier 2B) is
present in source but unreachable in the running pipeline, and two (U-30, U-31) are
deployment artefacts with no test coverage of any kind.

---

**Pass 01 summary.** Traced 61 repository-declared requirements — 18 use cases (17
implemented, UC 1 declared nowhere) and 43 Issue-Catalogue codes (33 live/implemented, 10
withdrawn/superseded, every one named by at least one test) — recorded 31 substantive
behaviours that no requirement states, two of them new at this commit (the four ADF pipelines
and the four merge procedures), and raised eleven discrepancies (⚠-13 … ⚠-23): that
`FR-1…FR-36` exists nowhere in the repository, that UC 13 names two different behaviours
writing to the same array, that two withdrawn codes carry a prefix contradicting their
declared group so a per-group census is ambiguous, and that Tier 2B ships complete and tested
but unwired.

**Carried forward.** The body of this pass was generated at `ea6f9d92168d3de949d369ed54a58b5a745a59b7` and is re-headed, not re-derived, at `86d173b8a4d715a619b0a2656986c145da7fa81e`; `git diff --stat` between the two touches no Python source — four `adf/*.json` (one line each), four `sql/*.sql` (whitespace-only reformatting) and two `docs/thesis/*.md` — so every citation above addresses the same bytes at both commits. ⚠ The ⚠ numbering above no longer joins up: §1.5 continues from "Pass 00 (⚠-1 … ⚠-12)", and Pass 00 at this commit raises fourteen, so ⚠-13 and ⚠-14 name one thing in `00_INVENTORY.md` and another here. Pass 08 must renumber, or this pass must be re-run.
