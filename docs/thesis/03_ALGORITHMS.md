Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 3 — Algorithms and Decision Procedures

This document specifies every decision procedure in the system. It is the primary source for
the implementation chapters and is written to be exhaustive rather than readable. Architecture,
component boundaries, and deployment are not restated here; they are in
`docs/thesis/02_ARCHITECTURE.md`. The procedure list was derived from `docs/thesis/00_INVENTORY.md`
(file table, entry points, call graphs) and cross-checked against
`docs/thesis/01_TRACEABILITY.md` (UC, G-series, EP, DD and X-series requirement IDs).

## 0 · Method and evidence rules

Every behavioural claim carries a citation of the form `path/file.py:LINE` or
`path/file.py:LINE-LINE`, taken by reading the cited function body rather than inferring from a
name. Constants — thresholds, weights, regexes, prompt text, temperatures — are reproduced
verbatim from their definition sites and are never rounded or reformatted. Worked examples are
drawn only from repository fixtures and test cases, cited by file and line; where no fixture
exercises a procedure the section carries `⚠ NO FIXTURE COVERAGE` and names the input that
would be required. Claims that could not be established from the source carry a
`⚠ UNVERIFIED —` prefix. No source, test, or configuration file was modified in producing this
document.

Each procedure is documented in a fixed seven-part structure: purpose; inputs and outputs with
types; numbered language-agnostic pseudocode matching the real control flow, including early
returns, guard clauses, and exception paths, with the source range cited above each block;
every constant the procedure reads; complexity in real loop bounds; a worked example with
intermediate values; and failure modes.

Complexity is stated in the loop bounds the code actually imposes — records per batch, streets
per record, SERP results ranked, pages fetched per record, rows per block, candidates per
signature — rather than in asymptotic notation detached from those bounds.

## 1 · Procedures found, and coverage of this document

The sweep identified **102 decision procedures** across the eleven subsystems below, plus
**16 distinct LLM call sites**. All 102 are documented here to the seven-part structure, and
all 16 LLM calls are documented with verbatim prompts in Part K. The coverage requested by the
pass specification maps onto the parts as follows.

| Required procedure | Documented in |
|---|---|
| Record type classification (company / institution / unknown) | Part B — Record-type classification |
| Tier escalation: entry and abandonment conditions per tier | Part B — Tier escalation (`_enrich_single`) |
| Tier 1 registry lookup and match acceptance — ROR | Part C §§1–12 |
| Tier 1 registry lookup and match acceptance — GLEIF/LEI | Part C §§18–21 |
| Tier 2 search-result selection and structured page extraction | Part D §§1–2, 7–8 |
| Tier 3 LLM inference and confidence assignment | Part D §6; confidence in Part B |
| Website resolution and domain / department-domain selection | Part E (Paths A/B/C, domain, dept probe) |
| Search term construction | Part F (ST1, ST2, terminal normalisation) |
| Every deterministic issue-detection rule in the catalogue | Part H §1.1 (all 37 codes) |
| Address-gate blocking and block key construction | Part I §2 — with the scope caveat in §1.2 below |
| Exact-signature collapse (dedup Step A) and signature construction | Part I §1 |
| LLM adjudication of candidate clusters (dedup Step B) | Part I §§4–8, 10 |
| Golden-record scoring and leading-code election, including tie-breaks | Part J §§2–3 |
| Normalisation, canonicalisation, fuzzy matching used by the above | Parts A §§15–18, C §§3–8, 14–17, F, G §§8–10, I §1 |

Procedure counts by part:

| Part | Subsystem | Procedures | Source module(s) |
|---|---|---:|---|
| A | Deterministic preprocessing (UC 6–17) | 17 | `enrichment/preprocess.py`, `utils/text_utils.py` |
| B | Orchestration, tier escalation, confidence | 6 | `enrichment/orchestrator.py`, `confidence.py`, `overflow_check.py` |
| C | Tier 1 registries (ROR, GLEIF/LEI) | 22 | `enrichment/tier1_ror.py`, `tier1_lei.py`, `company_canonical.py` |
| D | Tier 2 / Tier 3 and page extraction | 8 | `enrichment/tier2a_contact.py`, `tier2b_dept.py`, `tier2_canonical.py`, `lab_resolver.py`, `person_affiliation.py`, `tier3_llm.py`, `search/` |
| E | Website, domain, department-domain | 6 | `enrichment/website_resolver.py`, `orchestrator.py` |
| F | Search-term construction | 13 | `enrichment/search_terms.py` |
| G | Late address stage | 10 | `enrichment/address_processing.py` |
| H | Issue detection and the three issue paths | 3 + catalogue | `enrichment/issue_detection.py`, `api/routes.py` |
| I | Dedup Step A and Step B adjudication | 10 | `dedup/signatures.py`, `candidates.py`, `adjudicator.py`, `llm.py`, `prompts.py` |
| J | Golden-record scoring and election | 7 | `dedup/scoring.py`, `scoring_xlsx.py`, `weights.json` |
| K | LLM prompt appendix and non-determinism | 16 calls | `llm/prompts.py`, `openai_client.py`, `dedup/prompts.py`, `dedup/llm.py` |

### 1.1 Headline catalogue figures

The issue catalogue declares **37 codes**; **35** have an emission site; **2** are declared but
never emitted. Because one further code is emitted only through an unreachable branch, **at
most 34 distinct codes can be observed** in detector output. These figures were counted from
the source and re-verified by executing the module; the derivation, the names of the
never-emitted codes, and the unreachability proof are in Part H §§1.1–1.3. The module docstring
states "36-code" and "34 of the 36" (`enrichment/issue_detection.py:4,18`); both figures are
stale against the current source and must not be used.

### 1.2 Coverage limits stated plainly

- **The address gate itself is not implemented in this repository.** `[Block ID]` is precomputed
  by the DATAshaper address gate and read from the request (`docs/thesis/02_ARCHITECTURE.md:142-144`).
  This document specifies the block-key procedures the service does own — `derive_block_id`, the
  supplied-id precedence rule, and `cluster_hash` — in Part I §2. The gate's own blocking
  predicate is external and is therefore ⚠ NOT DOCUMENTABLE FROM THIS REPOSITORY; obtaining it
  requires the DATAshaper rule configuration, which is SaaS-side and not a repository artefact
  (`docs/thesis/02_ARCHITECTURE.md:331-333`).
- **Two documented procedures have no reachable production call site, for different reasons.**
  Tier 2A Mode B verification (Part D §1) is unreachable **by construction**: the orchestrator
  gate admits only records whose `pp_name2` is blank (`enrichment/orchestrator.py:2451-2457`)
  while the mode selector requires it to be populated
  (`enrichment/tier2a_contact.py:80`), so the qualifying input set is empty rather than merely
  unobserved. `run_tier2b` (Part D §2) has no gate at all — no call site and no import exist
  (`enrichment/orchestrator.py:37-59`); it is invoked only from `tests/test_tier2b.py`. In
  consequence the summary counters for `"2A_verification"` and `"2B"` cannot increment
  (`enrichment/orchestrator.py:2636-2641`), and enrichment fills blank `Name 2` values only,
  with no ability to correct an incorrect existing one. Both procedures are documented as
  implemented code with their reachability stated; the history is in `09_DECISIONS.md` (D-1).
- **Procedures lacking fixture coverage are marked in place** with `⚠ NO FIXTURE COVERAGE`
  rather than illustrated with constructed inputs. No hypothetical example appears in this
  document.

## 2 · Non-determinism, in summary

Deterministic subsystems, verified by import inspection and cited in each part's closing notes:
preprocessing (Part A), search-term construction (Part F), the late address stage except its
optional LLM residual step (Part G), deterministic issue detection and both `/issues` paths
(Part H), and golden-record scoring, election and approval (Part J).

Non-deterministic subsystems: every tier that calls an LLM or a search API (Parts B, C, D, E, I).
The enrichment LLM transport sets `temperature=0.0` explicitly
(`llm/openai_client.py:205`); the dedup transport passes **no** temperature and **no** seed
(`dedup/llm.py:174-184`). No seed is set on any call. SERP result sets and their ranking vary
between invocations, and the ROR and GLEIF registries change over time. Caching is in-memory
only and never persisted: per-batch registry caches cleared at batch start, and a SERP cache
with per-batch and process-level scopes. No capture-and-replay layer exists for live external
calls; the test suite substitutes hand-curated mocks. The full inventory — per procedure,
naming the external call, the source of variance, the cache scope, and whether the procedure is
reproducible — is Part K §B.3, and the exact configuration constants are Part K §B.1.

## 3 · Reading conventions

Part headings are `#`; procedure headings are `###`; the seven fixed subsections of each
procedure are `####`. Two parts additionally group their procedures under intermediate `##`
sections: Part H (three sections — catalogue, shared normalisation, formatting contract) and
Part K (two sections — prompt appendix, non-determinism inventory).

Line references are to the working tree at the commit in the header above; if that commit does
not match `HEAD`, treat this document as stale. Bracketed numbers inside pseudocode blocks, for
example `[504]` or `[264-269]`, are line numbers in the file named in the block's source
citation. Inside Part K, section labels of the form `A.9` or `B.3` refer to that part's own
numbered subsections, not to Parts A and B of this document.

---


# Part A — Deterministic preprocessing (UC 6–17)

All procedures below live in `enrichment/preprocess.py` (2,332 lines) unless a `utils/text_utils.py` path is given. Preprocessing is documented as it executes: a single synchronous function, `preprocess_record`, applies an ordered sequence of pattern-based stages to the mutable name/street/contact subset of a record (`enrichment/preprocess.py:1-24`). The module's docstring states the design constraint: it "Runs BEFORE any network/LLM call and is entirely pattern-based. … No SerpAPI, no ROR, no LLM on the hot path" (`enrichment/preprocess.py:1-9`).

---

### Top-level preprocessing orchestration (`preprocess_record` — enrichment/preprocess.py)

#### 1 Purpose

`preprocess_record` executes every deterministic cleanup stage in a fixed order over the record's four name slots, five street slots, and the contact/care_of/email fields, returning a `PreprocessResult` dataclass that mirrors the mutable subset of an `EnrichmentRecord` plus bookkeeping (`use_cases`, `flags`, `dba_fields`, `trigger_dept_lookup`, `name1_was_person`, `building`) (`enrichment/preprocess.py:50-87`, `enrichment/preprocess.py:1170-1198`). Because the function is synchronous by design, the LLM person/organisation classifier cannot be called from within it; the orchestrator runs an async pre-pass (`find_suspicious_plain_names` → `llm_classify_plain_names_async`) and passes the verdicts in via the `llm_person_verdicts` argument, keyed by lowercased candidate text (`enrichment/preprocess.py:1185-1191`, `enrichment/preprocess.py:2215-2332`).

#### 2 Inputs and outputs

Inputs (`enrichment/preprocess.py:1170-1184`): `name1..name4`, `contact`, `email`, `street1..street5`, `house_number`, `llm_person_verdicts: dict[str, str] | None` — all `str | None` except the verdict map.

Output: a `PreprocessResult` with fields `name1..name4`, `care_of`, `contact`, `email`, `street1..street5`, `building`, `use_cases: list[int]`, `flags: list[str]`, `dba_fields: set[str]`, `trigger_dept_lookup: bool`, `name1_was_person: bool` (`enrichment/preprocess.py:50-83`). The helper `PreprocessResult.note(uc, reason)` appends the use-case number (once) and a free-text flag (`enrichment/preprocess.py:84-87`).

#### 3 Pseudocode

The stage order is load-bearing; each numbered stage below cites its source range. Stages consume the state produced by all earlier stages.

(`enrichment/preprocess.py:1192-1198`)
1. Initialise `res` from the inputs; default `llm_person_verdicts` to `{}`.

(`enrichment/preprocess.py:1200-1211`) — leading opaque-code strip (part of UC 10)
2. For each slot in (`name1`,`name2`,`name3`,`name4`): apply `_strip_leading_opaque_code`; on change, write back (empty → `None`) and note UC 10. Comment states this runs FIRST so that a following `c/o` clause becomes a prefix UC 15 can route (`enrichment/preprocess.py:1201-1205`).

(`enrichment/preprocess.py:1213-1224`) — repeated-phrase collapse (noted under UC 12)
3. For each of the 9 name+street slots: apply `_collapse_repeated_phrase`; on change, write back and note UC 12.

(`enrichment/preprocess.py:1226-1244`) — Name 1 acronym/full-form dedupe (noted under UC 12)
4. If `name1` non-blank: `deduped = _strip_redundant_acronym(name1)`. If changed: assign; if `_syllabic_dash_abbrev(original)` returns a reason, append flag `"acronym-ambiguous: …"`; else note UC 12.

(`enrichment/preprocess.py:1246-1252`) — UC 15
5. `name2_handled_by_co_attn = _extract_co_attn_from_name2(res, llm_person_verdicts)` — the 5-case c/o+ATTN classifier (documented separately). Runs before the legacy UC 7 Pattern A loop and UC 6 so those see the routed state; touches only Name 2, care_of, contact, email (`enrichment/preprocess.py:1247-1251`).

(`enrichment/preprocess.py:1254-1271`) — named building (noted under UC 9)
6. For each slot in (`name2`,`name3`,`name4`,`street1`..`street5`), in order: if `_named_building(slot)` matches, set `res.building`, clear the slot, note UC 9, and **break** (only the first match moves).

(`enrichment/preprocess.py:1273-1290`) — location fragments (noted under UC 9)
7. For each slot in (`name2`,`name3`,`name4`): if `_location_fragment(slot)` matches, move the whole value verbatim to `_first_empty_street_slot(res)` and clear the name slot; if no street slot is empty, append flag `"street-slots-full"` and note UC 9. No break — every matching name slot is processed.

(`enrichment/preprocess.py:1292-1306`) — pipe splitter (UC 16)
8. For each street slot: `_split_pipe_street(value)`; if it fires, keep the rejoined non-org remainder in the slot and route org parts via `_route_org_parts_to_names`.

(`enrichment/preprocess.py:1308-1334`) — comma splitter (UC 16)
9. For each street slot: `_split_comma_street(value)`; if it fires, keep address segments in the slot, route name segments via `_route_org_parts_to_names`, and place each "other" fragment (e.g. a campus name) into the next empty street slot (flagging `"street-slots-full"` when none).

(`enrichment/preprocess.py:1336-1357`) — semicolon splitter (UC 16)
10. Same routing as step 9 using `_split_semicolon_street` (which additionally accepts bare institution acronyms as name content).

(`enrichment/preprocess.py:1359-1389`) — organisation-in-street router (UC 16)
11. For each street slot: if `_street_is_org_name(value)`: if Name 1 non-blank and not a unit construction, move the org to `_first_empty_name_slot` (skip slot silently when none); else promote the org to Name 1, first pushing any department currently in Name 1 down to the first empty name slot. Clear the street slot and **break** (only the first org value moves).

(`enrichment/preprocess.py:1391-1417`) — department-in-street router (UC 16)
12. For each street slot (no break): if `_street_is_department(value)`: if `_name_block_has_department(res)`, clear the street slot as redundant; else move the value to the first empty name slot after applying `_smart_title_case` (leave in place if no slot free).

(`enrichment/preprocess.py:1419-1473`) — UC 7 Pattern A (Attn prefix in a name field)
13. For each slot in (`name1`,`name2`,`name3`,`name4`), skipping `name2` when step 5 handled it: search `_ATTN_RE`; strip trailing junk from the payload with `_strip_contact_trailing_junk`; if the payload matches `_ORG_SIGNAL_RE`, only the "Attn:" prefix is removed (the payload IS the department) and a flag is appended; otherwise remove the Attn clause from the field and set `res.contact` (or flag `"contact-conflict"` if a different contact exists).

(`enrichment/preprocess.py:1475-1482`) — UC 6
14. For each name slot: if `_is_ap_reference(value)`, replace the ENTIRE field value with `"Accounts Payable"` and note UC 6.

(`enrichment/preprocess.py:1484-1517`) — UC 8
15. For each of the 9 name+street slots: find the first email; on a conflicting pre-existing email, flag and leave the source untouched; otherwise capture (if the email field is empty) and strip all email tokens from the source field.

(`enrichment/preprocess.py:1519-1544`) — UC 9
16. For each name slot: `_extract_addresses(value)`; each extracted fragment is discarded if `_duplicates_existing_street(...)`, else written to the first empty street slot (flag `"street-slots-full"` when none); the cleaned residue replaces the name value.

(`enrichment/preprocess.py:1546-1557`) — UC 11
17. For each name slot: `_normalise_dba`; on change, write back, add the field name to `res.dba_fields`, note UC 11.

(`enrichment/preprocess.py:1559-1572`) — UC 17
18. For each name slot: `_normalise_legal_suffix`; on change, write back and note UC 17.

(`enrichment/preprocess.py:1574-1581`) — UC 10 (full-field clearing)
19. For each slot in (`name2`,`name3`,`name4`) — Name 1 is exempt: if `_is_opaque_code(value)`, clear the slot and note UC 10.

(`enrichment/preprocess.py:1583-1631`) — UC 7 (Pattern B1/B2 contact extraction)
20. For each name slot: run `_extract_contact_from_field(val)` (deterministic title-prefix Pattern B1); if nothing extracted, look up the raw value (lowercased, trailing comma stripped) in `llm_person_verdicts`; failing that, look up `_person_candidate(val)` (credentials stripped, "Last, First" reordered). A defensive guard voids any extraction that `_is_ap_reference` matches (`enrichment/preprocess.py:1611-1618`). On extraction: set contact (or flag conflict), write back the remainder; when the slot was `name1` and nothing remains, set `res.name1_was_person = True` (`enrichment/preprocess.py:1628-1631`).

(`enrichment/preprocess.py:1633-1661`) — UC 16 (embedded department split in Name 1)
21. If `name1` matches `_DEPT_PHRASE_RE` and the prefix before the match carries an `_INSTITUTION_PREFIX_RE` keyword: place the department phrase into the first empty of name2/name3/name4 and shorten Name 1 to the prefix; when no slot is free, leave Name 1 intact and flag `"name1-embedded-department"`.

(`enrichment/preprocess.py:1663-1679`) — UC 13 (Name 3/4 junk strip)
22. Apply `_strip_name3_junk` to name3 and name4 (URLs, phone/fax, standalone opaque code tokens).

(`enrichment/preprocess.py:1681-1701`) — UC 13 (street junk + person-in-street)
23. For each street slot: if `_street_person_name(value)` yields a person and Contact is empty, move it to `contact` and clear the slot; if Contact is already populated, note + flag `"contact-conflict"` and keep the slot; otherwise apply `_strip_street_junk` (URLs/phones only — numeric codes are kept so street numbers survive).

(`enrichment/preprocess.py:1703-1747`) — UC 14 (promotion + leftward packing)
24. If `name1_was_person` is set, Name 1 is blank, Name 2 is populated, and Name 2 `_looks_like_institution` or `_looks_like_org_acronym`: promote Name 2 into Name 1 (`enrichment/preprocess.py:1721-1729`). Then pack (`name2`,`name3`,`name4`) leftward, dropping blanks (`enrichment/preprocess.py:1735-1747`).

(`enrichment/preprocess.py:1749-1796`) — UC 12 (duplicate clearing)
25. Six ordered pairwise comparisons (`name3/name4`, `name2/name4`, `name1/name4`, `name2/name3`, `name1/name3`, `name1/name2`) clear the later slot when `_equiv` holds — canonical unit forms equal, or `fuzz.ratio ≥ 92` (`enrichment/preprocess.py:1761-1796`). Later slots are compared first so an all-equal triple collapses cleanly (`enrichment/preprocess.py:1753-1756`).

26. Return `res` (`enrichment/preprocess.py:1798`).

#### 4 Constants

Slot-iteration orders (verbatim tuples): name slots `("name1", "name2", "name3", "name4")` (e.g. `enrichment/preprocess.py:1206`), street slots `("street1", "street2", "street3", "street4", "street5")` (e.g. `enrichment/preprocess.py:1300`). `_first_empty_street_slot` scans street1→street5 (`enrichment/preprocess.py:1801-1805`); `_first_empty_name_slot` scans name2→name4 only, never name1 (`enrichment/preprocess.py:1808-1813`).

#### 5 Complexity

Every stage is a constant number of passes over at most 9 fixed slots; each pass applies a constant set of compiled regexes to a field of length *n*, so the whole procedure is O(*n*) per field, O(Σ*n*) per record, dominated by regex scans. The only super-linear elements are `_collapse_repeated_phrase` (O(*t*·*d*(*t*)) over *t* tokens and divisors, see UC 12 procedure) and the six `fuzz.ratio` calls in UC 12 (O(*n*²/64) each in rapidfuzz's bit-parallel implementation). No I/O of any kind occurs.

#### 6 Worked example

From `tests/test_leading_code_strip.py:31-39` — input `name1="Acme Corp"`, `name2="B800000123 c/o Dr. Mark Adams"`:
- Stage 2 strips the leading code: `"B800000123"` matches `_LEADING_ACCOUNT_CODE_RE` (`^[A-Za-z]{1,4}-?\d{2,}$`, `enrichment/preprocess.py:326`), leaving `name2="c/o Dr. Mark Adams"` and noting UC 10.
- Stage 5 (UC 15) now sees a `c/o` prefix; the payload `"Dr. Mark Adams"` matches `_TITLE_PREFIX_RE` → Case A: `care_of="Dr. Mark Adams"`, `name2=None`.
- Asserted results: `pre.name2 is None`, `pre.care_of == "Dr. Mark Adams"`, and `10 in pre.use_cases and 15 in pre.use_cases` (`tests/test_leading_code_strip.py:37-39`) — demonstrating the documented ordering (code strip before UC 15).

#### 7 Failure modes

- Ordering is fragile by construction: e.g. the comment at `enrichment/preprocess.py:1419-1424` records that running UC 6 before UC 7 Pattern A would destroy the contact in "Accounts Payable - ATTN: Christina Boske".
- The org-in-street router silently leaves the org in the street when no name slot is free (`continue` with no flag, `enrichment/preprocess.py:1376-1377`); by contrast, `_route_org_parts_to_names` raises `"name-slots-full"` in the same situation (`enrichment/preprocess.py:2079-2086`) — an asymmetry in review visibility.
- UC 6 replaces the whole field, so any co-resident content that survived to stage 14 in the same field as an AP token is lost (`enrichment/preprocess.py:1478-1482`).
- All conflict situations (contact/email already populated with a different value) are flagged, never overwritten (`enrichment/preprocess.py:1467-1469`, `1500-1508`, `1690-1692`).

---

### Person-name extraction from name fields — UC 7 (`_extract_contact_from_field`, Pattern A loop, `find_suspicious_plain_names`, `_street_person_name` — enrichment/preprocess.py)

#### 1 Purpose

UC 7 moves a human name that is occupying a name field (or, in the UC 13 variant, a street field) into the `contact` field. Three deterministic patterns and one LLM-verdict pattern exist: Pattern A ("Attn:" prefix inside a name field), Pattern B1 (title prefix such as "Dr."), and Pattern B2 (plain 2–3 capitalised words, resolved by an out-of-band LLM verdict) (`enrichment/preprocess.py:1104-1119`). A special flag `name1_was_person` is raised when Name 1 held only a person, so the orchestrator can run a person-affiliation web lookup (`enrichment/preprocess.py:79-82`, `1628-1631`).

#### 2 Inputs and outputs

- `_extract_contact_from_field(text, allow_llm=False, llm_client=None) -> (contact_or_None, text_after_removal, reason)` (`enrichment/preprocess.py:1104-1149`). Within `preprocess_record` it is always called with defaults, so only Pattern B1 can fire there (`enrichment/preprocess.py:1592`).
- The Pattern A loop and the UC 7 loop mutate `res.contact` and the source name slot (`enrichment/preprocess.py:1432-1473`, `1586-1631`).
- `find_suspicious_plain_names(name1, name2, name3, name4=None) -> list[str]` returns distinct title-cased plain-name candidates for LLM classification (`enrichment/preprocess.py:2215-2287`).
- `_street_person_name(value) -> str | None` returns a title-prefixed person found alone in a street slot (`enrichment/preprocess.py:603-609`).

#### 3 Pseudocode

Pattern A loop (`enrichment/preprocess.py:1432-1473`):
1. For each name slot (skipping `name2` when UC 15 already handled it, `enrichment/preprocess.py:1434-1435`):
2. Search `_ATTN_RE`; no match → next slot.
3. `cleaned = _strip_contact_trailing_junk(payload)` — splits at the first `/`, `|` or `;` and removes a trailing phone-number pattern (`enrichment/preprocess.py:1094-1101`); empty → next slot.
4. Guard: if `cleaned` matches `_ORG_SIGNAL_RE`, the payload is a department label: keep it in the field with only the "Attn:" prefix removed, append an explanatory flag, continue (`enrichment/preprocess.py:1451-1461`).
5. Otherwise remove the entire Attn clause from the field (text before the match is preserved), tidy whitespace/punctuation; if Contact already populated → note + flag `"contact-conflict"`; else `res.contact = cleaned`, note UC 7 with reason `attn-prefix` (`enrichment/preprocess.py:1462-1473`).

`_extract_contact_from_field` (`enrichment/preprocess.py:1104-1149`):
1. Empty text → `(None, text, None)`.
2. Pattern B1: `core = _CREDENTIALS_RE.sub("", stripped)` (credentials stripped first so "Dr. Jane Smith, PhD" still matches, `enrichment/preprocess.py:1125-1129`); if `core` matches `_TITLE_PREFIX_RE` → return `(core, "", "title-prefix")`.
3. Pattern B2 (only when `allow_llm` and a client are supplied — never inside `preprocess_record`): if no `_ORG_SIGNAL_RE` hit and `_PLAIN_NAME_RE` matches, ask the LLM; verdict "person" → `(stripped, "", "llm-person")` (`enrichment/preprocess.py:1131-1147`). The synchronous stub `_llm_classify_person_or_org` unconditionally raises `NotImplementedError` (`enrichment/preprocess.py:1152-1163`).
4. Else `(None, text, None)`.

UC 7 loop verdict fallbacks (`enrichment/preprocess.py:1586-1631`):
1. If B1 produced nothing, try `llm_person_verdicts[val.strip().rstrip(",").strip().lower()]`; verdict "person" → contact = `_titlecase_person(raw value)`, reason `llm-person` (`enrichment/preprocess.py:1598-1603`).
2. Else compute `cand = _person_candidate(val)` (strips credentials, reorders one "Last, First" comma provided the last name has no space and no org signal, rejects org signals and non-plain-name shapes, title-cases the result, `enrichment/preprocess.py:1040-1062`); verdict "person" for `cand.lower()` → extract with reason `llm-person-normalised` (`enrichment/preprocess.py:1604-1609`).
3. AP guard: any extraction matching `_is_ap_reference` is discarded (`enrichment/preprocess.py:1611-1618`).
4. On success: contact set (or conflict flagged), field replaced by remainder; `name1_was_person = True` when name1 emptied (`enrichment/preprocess.py:1620-1631`).

`find_suspicious_plain_names` (orchestrator-side candidate mining, `enrichment/preprocess.py:2215-2287`):
1. For `name2`: strip the c/o/ATTN prefix and parenthetical noise, drop a slash-separated tail, then apply `_maybe_add` (`enrichment/preprocess.py:2268-2276`).
2. For `name1`/`name3`/`name4`: skip attn-prefixed values entirely (legacy behaviour, `enrichment/preprocess.py:2277-2279`); add the raw value and, additionally, the normalised `_person_candidate` (`enrichment/preprocess.py:2280-2286`).
3. `_maybe_add` rejects candidates with a title prefix, an org signal, or a non-plain-name shape; strips a trailing comma; title-cases via `_titlecase_person`; dedupes on the lowercased key (`enrichment/preprocess.py:2235-2256`).

`_street_person_name` (UC 13 variant, `enrichment/preprocess.py:603-609`):
1. If `_STREET_SUFFIX_GUARD_RE` matches at the end of the value (a real street suffix, optionally followed by a direction) → `None` — this keeps person-named streets such as "Dr Martin Luther King Jr Blvd" in the street (`enrichment/preprocess.py:592-600`).
2. Else match `_STREET_PERSON_RE` (title + 1–4 capitalised words, optional "- role" / ", role" suffix); group 1 is the clean person name (`enrichment/preprocess.py:585-591`).

`_titlecase_person` (`enrichment/preprocess.py:1007-1027`): per token, re-case only ALL-CAPS or all-lower tokens via `str.title` (mixed-case preserved); then restore the internal capital of an "Mc" surname via `^(Mc)([a-z])(.+)$` — "Mac" is deliberately left alone (`enrichment/preprocess.py:1020-1026`).

#### 4 Constants

```python
_ATTN_RE = re.compile(r"\b(?:attn|att|attention)\b\s*[:\-]?\s*(.+)", re.IGNORECASE)             # enrichment/preprocess.py:968-971
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:Dr\.?|Prof\.?|Professor|Mr\.?|Mrs\.?|Ms\.?|Mx\.?|Sir|"
    r"Ir\.?|Engr\.?|Rev\.?|Hon\.?)\s+[A-Z][\w\-']+(?:\s+[A-Z][\w\-']+){0,3}\s*$", re.IGNORECASE)  # enrichment/preprocess.py:974-978
_ORG_SIGNAL_RE = re.compile(
    r"\b(?:Inc|Corp|Corporation|Ltd|LLC|LLP|GmbH|AG|SA|Co|Company|"
    r"University|College|Institute|School|Hospital|Centre|Center|"
    r"Department|Dept|Division|Div|Laboratory|Laboratories|Lab|Labs|"
    r"Group|Research|Facility|Facilities|Core|Unit|"
    r"Medical|Clinic|Foundation|Trust|Partners|Associates|"
    r"Services|Systems|Technologies|Sciences|Engineering|"
    r"Office|Desk|Receiving|Shipping|Billing|Accounting|Purchasing|"
    r"Warehouse|Storeroom|Stockroom|Dock|Mailroom|Mail\s*Room)\b", re.IGNORECASE)                # enrichment/preprocess.py:981-991
_NAME_WORD = r"[A-Z][a-z]+(?:[-'][A-Za-z][a-z]*)*"                                              # enrichment/preprocess.py:997
_PLAIN_NAME_RE = re.compile(
    rf"^\s*{_NAME_WORD}\s+(?:[A-Z]\.?\s+)?{_NAME_WORD}(?:\s+{_NAME_WORD})?\s*$", re.IGNORECASE)  # enrichment/preprocess.py:1001-1004
_CREDENTIALS_RE = re.compile(
    r"(?:,?\s*\b(?:Ph\.?\s*D|D\.?\s*Phil|M\.?\s*D|M\.?\s*S\.?c?|M\.?\s*B\.?\s*A|"
    r"M\.?\s*P\.?\s*H|B\.?\s*S\.?c?|B\.?\s*A|D\.?\s*D\.?\s*S|D\.?\s*V\.?\s*M|"
    r"R\.?\s*N|J\.?\s*D|LL\.?\s*[MB]|P\.?\s*E|C\.?\s*P\.?\s*A|Esq|FACS|FRCP)\.?)+\s*$",
    re.IGNORECASE)                                                                              # enrichment/preprocess.py:1032-1037
_MULTI_CONTACT_SEPARATOR_RE = re.compile(r"\s+(?:and|or)\s+|\s*&\s*|[;/]|\s\+\s", re.IGNORECASE) # enrichment/preprocess.py:1065-1068
_STREET_PERSON_RE = re.compile(
    r"^\s*("
    r"(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx|Sir|Rev|Hon)\.?\s+"
    r"[A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,3}"
    r")(?:\s*[-,]\s*.+)?$", re.IGNORECASE)                                                       # enrichment/preprocess.py:585-591
_STREET_SUFFIX_GUARD_RE = re.compile(
    rf"\b(?:{_STREET_SUFFIXES})\b\.?"
    r"(?:\s+(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West))?\s*$", re.IGNORECASE)                 # enrichment/preprocess.py:596-600
```
Note `_PLAIN_NAME_RE` carries `re.IGNORECASE` so ALL-CAPS SAP exports ("ALBERT KAKKIS") are detected; the candidate is title-cased by `_titlecase_person` before reaching the classifier or the Contact field (`enrichment/preprocess.py:998-1000`).

`has_multiple_contacts` (exported for downstream use) reports more than one person when a strong separator matches, or when every comma-separated part has ≥ 2 tokens (`enrichment/preprocess.py:1071-1091`).

#### 5 Complexity

All patterns are single-pass compiled regexes: O(*n*) per field. `find_suspicious_plain_names` is O(Σ*n*) over four fields with a set-based dedupe. No recursion, no I/O.

#### 6 Worked example

From `tests/test_person_in_name1.py:34-37`: input `name1="Dr. Jane Smith, PhD"`, no verdicts.
- UC 7 loop → `_extract_contact_from_field`: `_CREDENTIALS_RE` strips ", PhD" → `core="Dr. Jane Smith"`; this matches `_TITLE_PREFIX_RE` → Pattern B1 returns `("Dr. Jane Smith", "", "title-prefix")`.
- Contact set, name1 remainder `""` → `name1=None`, `name1_was_person=True`.
- Asserted: `r.contact == "Dr. Jane Smith"` and name1 blank.

ALL-CAPS + Mc-surname path from `tests/test_person_in_name1.py:104-107`: `name1="KATHLEEN MCINTYRE"`, verdict `{"kathleen mcintyre": "person"}`. `_PLAIN_NAME_RE` (IGNORECASE) matched during candidate surfacing; in the UC 7 loop the raw-value verdict lookup hits, and `_titlecase_person` produces "Kathleen Mcintyre" → Mc-rule → "Kathleen McIntyre". Asserted: `r.contact == "Kathleen McIntyre"`.

Last-First reorder from `tests/test_person_in_name1.py:44-47`: `name1="Smith, John"`, verdict `{"john smith": "person"}` → `_person_candidate` reorders to "John Smith" (`enrichment/preprocess.py:1053-1056`) → contact `"John Smith"`.

Person-in-street from `tests/test_preprocess_co_attn.py:291-294`: `street2="Dr Sarah Johnson - Lab Director"` → `_STREET_SUFFIX_GUARD_RE` does not match; `_STREET_PERSON_RE` group 1 = "Dr Sarah Johnson" (role suffix dropped) → `contact="Dr Sarah Johnson"`, `street2=None`. Counter-case `tests/test_preprocess_co_attn.py:301-305`: "Dr Martin Luther King Jr Blvd" ends in "Blvd" → guard fires → stays in the street.

⚠ NO FIXTURE COVERAGE — the Pattern A loop itself (Attn inside `name1`/`name3`/`name4`, `enrichment/preprocess.py:1432-1473`), including the org-payload branch at `enrichment/preprocess.py:1451-1461`, is not exercised by any of the assigned test files (Attn on Name 2 is routed by UC 15 instead).

#### 7 Failure modes

- Pattern B2 is inert inside `preprocess_record` (called without `allow_llm`); person detection for plain names depends entirely on the verdict map supplied by the orchestrator — with an empty map, plain names pass through untouched (`enrichment/preprocess.py:1592`, `1598-1609`).
- `_person_candidate` refuses values with any `_ORG_SIGNAL_RE` word, so hybrid strings ("Jane Smith Lab") are never surfaced (`enrichment/preprocess.py:1055-1059`).
- Contact conflicts are flagged, not resolved (`enrichment/preprocess.py:1621-1623`).
- The Mc-rule cannot recover "Mac" surnames by design (`enrichment/preprocess.py:1022`, mirrored at `utils/text_utils.py:261-262`).

---

### c/o + ATTN five-case extraction from Name 2 — UC 15 (`_extract_co_attn_from_name2` — enrichment/preprocess.py)

#### 1 Purpose

When Name 2 carries a "c/o" or "Attn:" prefix (or is itself an email, or a bare job title), the payload is routed to one of four output fields by a five-case priority classifier: D — Email → `email`; C — Department → kept in Name 2 with the prefix stripped; B — Company → `care_of`; A — Person → `care_of` (with title) + `contact` (title stripped) + `trigger_dept_lookup`; E — Fallback → `care_of` (`enrichment/preprocess.py:633-652`). Cases A/B/C/E require a prefix; Case D also fires without one when the whole value is an email; Case E also fires without a prefix for an unambiguous job title (`enrichment/preprocess.py:648-652`).

#### 2 Inputs and outputs

`_extract_co_attn_from_name2(res: PreprocessResult, llm_person_verdicts: dict[str, str]) -> bool` — mutates `res` (name2, care_of, contact, email, trigger_dept_lookup, flags) in place; returns True when Name 2 was rewritten or cleared, so the caller skips the legacy UC 7 Pattern A loop for that field (`enrichment/preprocess.py:851-860`, `1434-1435`).

#### 3 Pseudocode

(`enrichment/preprocess.py:861-873`)
1. If Name 2 blank → return False.
2. `(payload, had_prefix) = _strip_co_attn_prefix(name2)` — prefix stripped at most once (`enrichment/preprocess.py:774-781`); `payload = _strip_parenthetical_noise(payload)` — removes "(guest)"-style noise; trailing periods are preserved because they are load-bearing for "Inc." (`enrichment/preprocess.py:784-789`).
3. Empty payload: if a prefix was present, clear Name 2 and note "prefix only, no payload", return True; else return False.

Case D — Email (`enrichment/preprocess.py:875-888`)
4. If `_EMAIL_RE` finds an email in the payload, and (`had_prefix` or the payload is exactly the email): if a DIFFERENT email is already populated → note + flag `"email-conflict"`; else set `res.email`. In BOTH branches `res.name2 = None`; return True.

No-prefix gate (`enrichment/preprocess.py:890-897`)
5. If no prefix: only Case E for a clear job title fires — `_is_job_title(payload)` (trailing Manager/Director/… word) → `care_of = payload`, clear Name 2, return True. Otherwise return False (the raw Name 2 is the user's department/sub-org and must not be moved).

Case C — Department (`enrichment/preprocess.py:899-903`)
6. If `_is_department_payload(payload)` — dept keyword (`_DEPT_KEYWORDS_RE`), AP reference (`_is_ap_reference`), or trailing "Services" (`_TRAILING_SERVICES_RE`) (`enrichment/preprocess.py:792-801`) — keep the payload in Name 2 (prefix stripped), return True.

Case B — Company (`enrichment/preprocess.py:905-910`)
7. If `_has_legal_suffix(payload)` (`_LEGAL_SUFFIX_RE`) → `care_of = payload`, clear Name 2, return True.

Case A — Person (`enrichment/preprocess.py:912-955`)
8. If the payload contains "/", take the leading segment as `candidate` and remember `flagged_slash` (`enrichment/preprocess.py:915-921`).
9. Compute three signals: `has_title` = `_TITLE_PREFIX_RE.match(candidate)`; `verdict_person` = `llm_person_verdicts[candidate.lower()] == "person"`; `is_plain_name` = `_PLAIN_NAME_RE` matches AND no `_ORG_SIGNAL_RE` AND not `_is_job_title` — with an explicit prefix the plain-name shape alone is sufficient evidence (`enrichment/preprocess.py:923-936`).
10. If any signal holds: `care_of = candidate`; `contact = _strip_title(candidate) or candidate`; a differing pre-existing contact → note + flag `"contact-conflict"` (care_of still written); clear Name 2; `trigger_dept_lookup = True`; on `flagged_slash`, append a manual-review flag about the discarded remainder; return True.

Case E — Fallback (`enrichment/preprocess.py:957-961`)
11. Prefixed payload matching nothing above → `care_of = payload`, clear Name 2, note "unclassified payload", return True.

#### 4 Constants

```python
_CO_ATTN_PREFIX_RE = re.compile(r"^\s*(?:c\s*/\s*o|att?n+(?:ention|tion)?|att)\s*[:\-]?\s*", re.IGNORECASE)  # enrichment/preprocess.py:655-658
_PARENTHETICAL_NOISE_RE = re.compile(r"\s*\([^)]*\)\s*")                                                     # enrichment/preprocess.py:662
_DEPT_KEYWORDS_RE = re.compile(
    r"\b("
    r"Department\s+of|Dept\.?\s+of|Division\s+of|"
    r"School\s+of|College\s+of|Faculty\s+of|"
    r"Accounts?\s+Payable|Acct?s?\.?\s+Payable|"
    r"Purchasing|Procurement|"
    r"Receiving|Shipping|Billing"
    r")\b", re.IGNORECASE)                                                                                   # enrichment/preprocess.py:665-674
_TRAILING_SERVICES_RE = re.compile(r"\bServices\s*$", re.IGNORECASE)                                         # enrichment/preprocess.py:736
_LEGAL_SUFFIX_RE = re.compile(
    r"\b("
    r"L\.?L\.?C\.?|"
    r"Inc\.?|Incorporated|"
    r"Corp\.?|Corporation|"
    r"Ltd\.?|Limited|"
    r"Co\.?|Company|"
    r"L\.?L\.?P\.?|L\.?P\.?|"
    r"GmbH|S\.?A\.?(?:S\.?)?|"
    r"AG|N\.?V\.?|B\.?V\.?|"
    r"PLC|Pty|"
    r"P\.?C\.?"
    r")\.?\b", re.IGNORECASE)                                                                                # enrichment/preprocess.py:739-753
_JOB_TITLE_TAIL_RE = re.compile(
    r"\b("
    r"Manager|Director|Supervisor|Coordinator|Specialist|"
    r"Officer|Lead|Head|Chair|Administrator|Analyst|"
    r"Representative|Executive|Foreman|Assistant|"
    r"Buyer|Planner|Controller"
    r")\s*$", re.IGNORECASE)                                                                                 # enrichment/preprocess.py:757-765
_TITLE_STRIP_RE = re.compile(r"^\s*(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx|Sir|Ir|Engr|Rev|Hon)\.?\s+", re.IGNORECASE)  # enrichment/preprocess.py:768-771
```
The prefix regex's `att?n+(?:ention|tion)?` also catches the misspellings "Atnn:" and "attnn" (`enrichment/preprocess.py:654`).

#### 5 Complexity

A fixed cascade of regex tests over a single field: O(*n*) in the payload length, constant space.

#### 6 Worked example

Case A from `tests/test_uc15_co_attn.py:38-43`: `name1="BioMed Solutions Inc."`, `name2="c/o Dr. Steven Park"`.
- `_strip_co_attn_prefix` → payload `"Dr. Steven Park"`, `had_prefix=True`; no parenthetical noise.
- `_EMAIL_RE`: no match → Case D skipped. Prefix present → Case C: no dept keyword, not AP, no trailing "Services" → skipped. Case B: no legal suffix → skipped.
- Case A: `has_title=True` (matches `_TITLE_PREFIX_RE`); `contact = _strip_title("Dr. Steven Park") = "Steven Park"`.
- Asserted: `care_of == "Dr. Steven Park"`, `contact == "Steven Park"`, `name2 is None`, `trigger_dept_lookup is True`.

Case D without prefix from `tests/test_uc15_co_attn.py:103-108`: `name2="Apinvoices@nchmd.org"` → payload equals the email exactly (`payload_is_just_email=True`) → `email="Apinvoices@nchmd.org"`, `name2=None`, care_of/contact untouched.

Case C from `tests/test_uc15_co_attn.py:88-92`: `name2="c/o: Fabrication Outage Services"` → prefix stripped, `_TRAILING_SERVICES_RE` fires → `name2="Fabrication Outage Services"` kept in place.

Slash-flagged Case A from `tests/test_preprocess_co_attn.py:62-72`: `name2="c/o Yanping Zhang/Synkine NanoString P"`, verdict `{"yanping zhang": "person"}` → candidate = leading segment "Yanping Zhang"; extraction succeeds; flag containing "slash" appended.

#### 7 Failure modes

- On an email conflict (Case D), Name 2 is cleared even though the found email is discarded (`enrichment/preprocess.py:881-888`); the value survives only in the flag text.
- The slash rule keeps only the leading segment; the remainder is dropped from the data and preserved solely in a review flag (`enrichment/preprocess.py:949-953`).
- Case E routes any unclassifiable prefixed payload to `care_of`, which may capture non-care-of content; it is noted but not review-flagged (`enrichment/preprocess.py:957-961`).
- Case B keys on `_LEGAL_SUFFIX_RE`, whose alternates include `Co` and `S.A.` — a person payload containing such a token would be misrouted to Case B before Case A is considered (behavioural consequence of the priority order at `enrichment/preprocess.py:899-910`; ⚠ NO FIXTURE COVERAGE for this collision).

---

### Email extraction — UC 8 (`_find_email` + UC 8 loop — enrichment/preprocess.py)

#### 1 Purpose

Copy the first email address found in any name or street field into the dedicated `email` field, and strip email tokens from the source field so cleaned name/street values are not polluted; a conflicting pre-existing email leaves the source intact and raises a review flag (`enrichment/preprocess.py:1484-1491`). (The `_find_email` docstring describing a non-destructive copy reflects the older business rule; the loop as implemented does strip the token — `enrichment/preprocess.py:120-127` vs `1512-1517`.)

#### 2 Inputs and outputs

`_find_email(text) -> str | None` — first regex match or None (`enrichment/preprocess.py:120-130`). The UC 8 loop reads all nine name+street slots and writes `res.email`, the source slot, `use_cases`, `flags` (`enrichment/preprocess.py:1492-1517`).

#### 3 Pseudocode

(`enrichment/preprocess.py:1492-1517`)
1. For each slot in (`name1`..`name4`, `street1`..`street5`):
2. Skip empty; `email_found = _find_email(val)`; skip when none.
3. If `res.email` is populated and differs case-insensitively → note UC 8 + flag `"email-conflict"`, leave the field untouched, continue.
4. If `res.email` is empty → `res.email = email_found`, note "copied email from {field}".
5. Strip ALL email tokens from the source (`_EMAIL_RE.sub("")`), collapse whitespace, trim `" ,;/|-"`; on change write back (empty → None) and note "removed email from {field}". Note this stripping also runs when the found email merely duplicates the existing one.

#### 4 Constants

```python
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")   # enrichment/preprocess.py:115-117
```

#### 5 Complexity

One regex search plus at most one global substitution per slot: O(*n*) per field.

#### 6 Worked example

From `tests/test_preprocess_co_attn.py:175-187`: input `name3="Research Lab jsmith@acme.com"`, email empty.
- `_find_email` → `"jsmith@acme.com"`; no conflict → `res.email = "jsmith@acme.com"`.
- `_EMAIL_RE.sub("")` leaves `"Research Lab "` → tidied to `"Research Lab"`.
- Asserted: `res.email == "jsmith@acme.com"`, `res.name3 == "Research Lab"`. The email-only variant (`tests/test_preprocess_co_attn.py:188-196`) yields `name3 is None`.
- Conflict variant (`tests/test_preprocess_co_attn.py:198-210`): pre-existing `email="existing@y.com"` and `name3="Research Lab foo@x.com"` → email keeps `"existing@y.com"`, `"foo@x.com" in res.name3`, `"email-conflict" in res.flags`.

#### 7 Failure modes

- Only the FIRST email in a field is captured; all are stripped — additional distinct addresses in one field are lost from the data (`enrichment/preprocess.py:1497`, `1513`).
- The conflict rule is field-order dependent: the first field scanned wins the email slot; later fields with different emails are flagged, not captured (`enrichment/preprocess.py:1492-1508`).

---

### Street-address extraction from name fields and fragment dedupe — UC 9 (`_extract_addresses`, `_duplicates_existing_street` — enrichment/preprocess.py)

#### 1 Purpose

Detect street-address content embedded in a name field (house-number + street-suffix forms, numbered campus buildings, sub-locations, PO boxes), move each fragment to the first empty street slot, and keep the cleaned residue as the name. A fragment that duplicates an already-populated street field — verbatim or as House Number + field — is discarded rather than re-added (`enrichment/preprocess.py:1519-1544`, `218-240`).

#### 2 Inputs and outputs

- `_extract_addresses(text) -> (list_of_fragments, text_with_them_removed)` (`enrichment/preprocess.py:243-257`).
- `_norm_street_key(value) -> str` — lowercase, strip `.,`, canonicalise street-type words per `_STREET_TYPE_NORM`, collapse whitespace (`enrichment/preprocess.py:208-215`).
- `_duplicates_existing_street(fragment, res, house_number) -> bool` (`enrichment/preprocess.py:218-240`).

#### 3 Pseudocode

`_extract_addresses` (`enrichment/preprocess.py:243-257`):
1. For each of the six `_ADDRESS_PATTERNS`, repeatedly search-and-delete matches from the working text (loop until no match), collecting each match stripped of `" ,;.:"`.
2. Collapse whitespace in the残 residue and strip `" ,;/|-"`; return `(found, residue)`.

UC 9 loop (`enrichment/preprocess.py:1522-1544`):
1. For each name slot with a value: extract; skip slot when no fragment found.
2. For each fragment: if `_duplicates_existing_street(addr, res, house_number)` → note UC 9 "duplicates an existing street field — discarded"; else write to `_first_empty_street_slot` (flag `"street-slots-full"` when none).
3. Replace the name value with the cleaned residue (empty → None).

`_duplicates_existing_street` (`enrichment/preprocess.py:218-240`):
1. `nf = _norm_street_key(fragment)`; empty → False.
2. For each populated street slot value `v`: True when `nf == _norm_street_key(v)`, or, with a house number `hn`, when `nf == _norm_street_key(f"{hn} {v}")`.

#### 4 Constants

```python
_STREET_SUFFIXES = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Way|Hwy|Highway|Pl|Place|Pkwy|Parkway|Ct|Court|Ter|Terrace|"
    r"Cir|Circle|Sq|Square")                                                   # enrichment/preprocess.py:138-142
_BUILDING_PLACE_WORDS = (r"Hall|Building|Bldg|Pavilion|Tower|Annex|Wing|Complex")  # enrichment/preprocess.py:147-149
_STREET_TOKEN = r"(?:[A-Z][\w\-]*|\d+(?:st|nd|rd|th))"                         # enrichment/preprocess.py:154
_DIRECTION = r"(?:N\.?W\.?|N\.?E\.?|S\.?W\.?|S\.?E\.?|N\.?|S\.?|E\.?|W\.?)"   # enrichment/preprocess.py:157
```
The six `_ADDRESS_PATTERNS` (`enrichment/preprocess.py:158-197`), in application order:
1. house number + optional direction + street tokens + street suffix + optional trailing direction (`:162-165`);
2. house number + named building/hall word (`:169-172` — "100 Rhines Hall"; the leading number is required so an unnumbered "Rhines Hall" is left for the Building router);
3. building name + trailing room number (`:176-179` — "Rhines Hall 104", `\d+[A-Za-z]?`);
4. bare street name anchored to the whole value (`^…$`) with street suffix (`:182-185` — "NW 2nd Ave");
5. `r"\b(?:Suite|Ste|Unit|Floor|Bldg|Building|Room|Rm)\b\.?\s+[\w\-]+\b"` (`:190`; the word boundary prevents "Unit" matching inside "United" — `enrichment/preprocess.py:186-189`);
6. PO Box family: `r"\b(?:P\.?\s*O\.?\s*Box|Post\s+Office\s+Box|Mail\s*Box|Mailbox|Box)\s+\w+\b"` (`:192-196`).

```python
_STREET_TYPE_NORM = {
    "boulevard": "blvd", "avenue": "ave", "street": "st", "road": "rd",
    "drive": "dr", "lane": "ln", "parkway": "pkwy", "highway": "hwy",
    "court": "ct", "place": "pl", "terrace": "ter", "circle": "cir",
    "square": "sq", "suite": "ste"}                                            # enrichment/preprocess.py:200-205
```

#### 5 Complexity

For each pattern the search-and-delete loop runs once per match: O(*p*·*k*·*n*) with *p* = 6 patterns, *k* matches, text length *n*. The dedupe check is O(5·*n*) per fragment.

#### 6 Worked example

From `tests/test_street_fragment_dedup.py:25-33` (production row 179): `name1="Photon Labs 4200 Research Blvd Suite 210"`, `street1="RESEARCH BLVD"`, `house_number="4200"`.
- Pattern 1 extracts `"4200 Research Blvd"`; pattern 5 extracts `"Suite 210"`; residue `"Photon Labs"`.
- Dedupe for `"4200 Research Blvd"`: `nf = "4200 research blvd"`; existing street1 with house number → `_norm_street_key("4200 RESEARCH BLVD") = "4200 research blvd"` → equal → discarded (`enrichment/preprocess.py:238-239`).
- `"Suite 210"` is not a duplicate → first empty street slot.
- Asserted: `r.name1 == "Photon Labs"`; no street slot equals "4200 Research Blvd"; `"Suite 210"` present in the street slots.
- Negative control (`tests/test_street_fragment_dedup.py:35-37`): `"500 Oak Ave"` from `name1="Acme 500 Oak Ave"` is genuinely different → lands in `street2`.

Numbered-hall routing from `tests/test_named_building.py:107-111`: `name2="100 Rhines Hall"` → pattern 2 → `street1="100 Rhines Hall"`, `name2=None`, `building is None`. Trailing-number form (`tests/test_named_building.py:125-134`): `"Rhines Hall 104"`, `"Rhines Hall 104B"` → pattern 3 → street slot. Org-with-number counter-case (`tests/test_named_building.py:151-155`): `"100 Black Men of America"` has no street/building word → no pattern fires → stays in `name2`.

#### 7 Failure modes

- Pattern 5 will extract any "<marker> <word>" pair (e.g. "Room 12") even when the surrounding field is an org name containing such wording; only the marker's word-boundary guard protects ordinary names (`enrichment/preprocess.py:186-190`).
- Fragments beyond the five street slots are flagged (`"street-slots-full"`) and dropped from the data (`enrichment/preprocess.py:1537-1541`).
- The dedupe is exact after normalisation; a fragment differing by a direction letter or spelling from the populated street is retained as a near-duplicate (`enrichment/preprocess.py:236-239`).

---

### Street-field splitters: pipe, comma, semicolon — UC 16 (`_split_pipe_street`, `_split_comma_street`, `_split_semicolon_street`, `_route_org_parts_to_names` — enrichment/preprocess.py)

#### 1 Purpose

SAP exports sometimes dump an entire org hierarchy plus the address into one delimited street field. The three splitters classify each delimited segment individually and route only organisation/department segments to the Name block, keeping c/o lines, named buildings, addresses, and campus fragments in the street for the late address stage (`enrichment/preprocess.py:1891-1912`). The comma and semicolon splitters carry guards so a plain multi-segment address is never split (`enrichment/preprocess.py:2105-2124`, `2163-2175`).

#### 2 Inputs and outputs

- `_split_pipe_street(value) -> (street_remainder, org_parts) | None` (`enrichment/preprocess.py:1891-1933`).
- `_split_comma_street(value) -> (address, name_parts, other_parts) | None` (`enrichment/preprocess.py:2091-2141`).
- `_split_semicolon_street(value) -> (address, name_parts, other_parts) | None` (`enrichment/preprocess.py:2155-2188`).
- `_route_org_parts_to_names(res, parts, slot) -> None` — mutates the name block (`enrichment/preprocess.py:2059-2088`).

#### 3 Pseudocode

`_split_pipe_street` (`enrichment/preprocess.py:1913-1933`):
1. No `|` → None. Split on `|`, strip `" ,;-"`, drop empties; fewer than 2 parts → None.
2. Per part: a `_CO_ATTN_PREFIX_RE` match or a `_named_building` hit → keep in street; else `_looks_like_name_content(part)` → org part; else keep (address, campus fragment, mail code, residue).
3. No org parts → None (a plain multi-line address keeps its pipes for `process_address`). Else return kept parts rejoined with `", "` and the org parts in order.

`_split_comma_street` (`enrichment/preprocess.py:2110-2141`):
1. No `,` → None. Split on `,`, strip `" ;-"`; < 2 segments → None.
2. Trigger guards: at least one segment must satisfy `_segment_is_org` (`enrichment/preprocess.py:2116-2117`), AND at least one must satisfy `_segment_is_address` or `_named_building` (`enrichment/preprocess.py:2118-2124`) — so "51 Sleeper Street, 7th Floor" (no org) is untouched.
3. Classify each segment: address → `addr_parts`; else `_looks_like_name_content` → `name_parts`; else → `other_parts` (named building, "Lab 576" room code, bare campus fragment).
4. If no `name_parts`, or neither `addr_parts` nor `other_parts` → None. Return `(", ".join(addr_parts) or None, name_parts, other_parts)`.

`_split_semicolon_street` (`enrichment/preprocess.py:2168-2188`):
1. No `;` → None. Split on `;`, strip `" ,-"`; < 2 segments → None.
2. Guard: at least one segment must be address-like (`enrichment/preprocess.py:2174-2175`).
3. Classify: address → `addr_parts`; else `_looks_like_name_content(seg)` OR `_looks_like_org_acronym(seg)` (bare 2–6-capital acronym such as "UCSF", accepted only here) → `name_parts`; else `other_parts`.
4. Require both `addr_parts` and `name_parts`, else None (a semicolon between two address lines is untouched).

Segment classifiers:
- `_segment_is_address` (`enrichment/preprocess.py:1969-1985`): True when `_extract_addresses` finds a fragment, or `_GERMAN_STREET_RE` (street-word suffix + number), `_STATE_ZIP_RE` (US "ST 12345"), or `_UK_POSTCODE_RE` matches, or `_SUBLOC_SEG_RE` matches AND the segment contains a digit.
- `_segment_is_org` (`enrichment/preprocess.py:2040-2056`): False for a c/o line; else, after masking street-institution words (`_mask_street_institution_words`, so "University Road" is not an org — `enrichment/preprocess.py:1963-1966`), True when `looks_like_research_institution(masked)`, `is_unit_construction`, `is_granular_unit`, a `_LEGAL_SUFFIX_RE` hit, or `_is_ap_reference`.
- `_looks_like_name_content` (`enrichment/preprocess.py:2023-2037`): False for c/o lines, named buildings, and `_LAB_ROOM_RE` room codes; True when `_segment_is_org`, `_looks_like_institution`, or a `_DEPT_LEAD_RE`/`_DEPT_TRAILING_RE` match.
- `_looks_like_institution` (`enrichment/preprocess.py:2010-2020`): False when `_DEPT_LEAD_RE` or `_DEPT_TRAILING_RE` marks the segment as a sub-unit; else True when the masked segment matches `_INSTITUTION_RE` or `looks_like_research_institution`.

`_route_org_parts_to_names` (`enrichment/preprocess.py:2059-2088`):
1. Apply `_strip_redundant_acronym` to every routed segment (dash forms such as "MRC - Medical Research Council" are resolved here because the Name-1 dedupe pass already ran, `enrichment/preprocess.py:2064-2068`).
2. If Name 1 is empty: pick the first segment satisfying `_looks_like_institution` (fall back to index 0), pop it into Name 1, note UC 16.
3. Remaining segments fill `_first_empty_name_slot` in order; when no slot is free, append flag `"name-slots-full"` (once) and note each dropped segment for review.

#### 4 Constants

```python
_GERMAN_STREET_RE = re.compile(
    r"\b[\wäöüÄÖÜß\-]+(?:stra(?:ße|sse)|str\.?|weg|platz|allee|gasse|ring|damm)\b\s+\d+", re.IGNORECASE)  # enrichment/preprocess.py:1937-1940
_STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")                                            # enrichment/preprocess.py:1942
_SUBLOC_SEG_RE = re.compile(
    r"\b(?:Floor|Fl|Room|Rm|Suite|Ste|Unit|Bldg|Building|Mail\s*Stop|MS|"
    r"P\.?\s*O\.?\s*Box|Box)\b", re.IGNORECASE)                                                           # enrichment/preprocess.py:1944-1948
_UK_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b", re.IGNORECASE)                     # enrichment/preprocess.py:1950
_INSTITUTION_STREET_RE = re.compile(
    r"\b(?:University|College|Institute|Hospital|Church|Academy|School)\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Way|Hwy|Highway|Pl|Place|Pkwy|Parkway|Ct|Court|Ter|Terrace|"
    r"Cir|Circle|Sq|Square)\b", re.IGNORECASE)                                                            # enrichment/preprocess.py:1954-1960
_DEPT_LEAD_RE = re.compile(
    r"^(?:the\s+)?(?:Division|Office|Department|Dept|Center|Centre|Faculty|Branch|"
    r"School|Section|Unit|Group|Laborator(?:y|ies)|Lab|Program|Programme|Bureau|"
    r"Directorate)\b", re.IGNORECASE)                                                                     # enrichment/preprocess.py:1989-1994
_DEPT_TRAILING_RE = re.compile(
    r"\b(?:Branch|Division|Office|Section|Unit|Group|Laborator(?:y|ies)|Lab)\s*$", re.IGNORECASE)          # enrichment/preprocess.py:1995-1998
_LAB_ROOM_RE = re.compile(r"^Lab\.?\s+\w*\d[\w\-]*$", re.IGNORECASE)                                      # enrichment/preprocess.py:2001
_INSTITUTION_RE = re.compile(
    r"\b(?:University|Universit[äa]t|College|Administration|Agency|Ministry|"
    r"Corporation|Company|Hospital|Clinic|Institute|Institut|Foundation|Society)\b", re.IGNORECASE)        # enrichment/preprocess.py:2003-2007
_ORG_ACRONYM_RE = re.compile(r"^[A-Z]{2,6}$")                                                             # enrichment/preprocess.py:2145
```

#### 5 Complexity

Each splitter is one split plus a constant-regex classification of each segment; `_segment_is_address` re-runs `_extract_addresses`, so a value with *s* segments costs O(*s*·*p*·*n*) with the same regex constants as UC 9. Routing is O(*s*) slot scans.

#### 6 Worked example

Pipe (segment-wise, production row 90000337) from `tests/test_pipe_splitter_inversion.py:27-41`: `street1="MRC - Medical Research Council, | C/O RCUK SSC Ltd, | Polaris House, | North Star House, | North Star Avenue"`, `name1="Acme Corp"`.
- Segments after stripping: `["MRC - Medical Research Council", "C/O RCUK SSC Ltd", "Polaris House", "North Star House", "North Star Avenue"]`.
- "C/O RCUK SSC Ltd" → c/o prefix → kept; "Polaris House"/"North Star House"/"North Star Avenue" → not name content → kept; "MRC - Medical Research Council" → org part.
- Routing: Name 1 occupied ("Acme Corp") → the org (acronym-deduped to "Medical Research Council") lands in `name2`.
- Asserted: `r.name2 == "Medical Research Council"`; `r.street1 == "C/O RCUK SSC Ltd, Polaris House, North Star House, North Star Avenue"`; no pipes remain; name3 empty.

Pipe (FDA hierarchy) from `tests/test_street_org_split.py:45-59`: six segments, `name1=None` → institution-first rule puts "U.S. Food and Drug Administration" into Name 1 (not the first, most granular segment); departments fill name2–name4; `street1="5100 Paint Branch Pkwy"`. With five org segments and four name slots the overflow raises `"name-slots-full"` (`tests/test_street_org_split.py:61-71`).

Comma (German street) from `tests/test_street_org_split.py:75-85`: `street1="Institute of Sustainable and Environmental Chemistry, Faculty of Sustainability, Scharnhorststraße 1 C13.217"`, `name1=None`. `_GERMAN_STREET_RE` marks the last segment as address; the first two are org/dept → `name1="Institute of Sustainable and Environmental Chemistry"`, `name2="Faculty of Sustainability"`, `street1="Scharnhorststraße 1 C13.217"`. The "other" branch appears in `tests/test_street_org_split.py:87-95`: "Queens Campus" → `street2` (a street line, not a Name).

Semicolon from `tests/test_person_org_in_street.py:82-92`: `"UCSF; 600 16th Avenue"` → `name_parts == ["UCSF"]`, `address == "600 16th Avenue"`; whereas `"600 16th Ave; San Francisco, CA 94118"` (two address lines) returns None.

Not-split guard from `tests/test_street_org_split.py:98-107`: `"51 Sleeper Street, 7th Floor"` and `"200 Clarendon Street, Boston, MA 02210"` remain intact.

#### 7 Failure modes

- `_split_pipe_street` returns None when NO segment is an org — a c/o + address dump keeps its pipes for the later address stage (`tests/test_pipe_splitter_inversion.py:43-46`; `enrichment/preprocess.py:1930-1931`).
- The comma splitter cannot recognise a bare acronym as an org (only the semicolon splitter can, `enrichment/preprocess.py:2148-2152`, `2182`).
- Overflow segments beyond Name 4 are removed from the data (retained only in review notes) with the `"name-slots-full"` flag (`enrichment/preprocess.py:2079-2086`).
- The docstring of `_split_comma_street` describes a 2-tuple return but the function returns a 3-tuple `(address, name_parts, other_parts)` (`enrichment/preprocess.py:2101-2109` vs `2140-2141`) — documentation drift only; callers use the 3-tuple.

---

### Organisation- and department-in-street routers — UC 16 (`_street_is_org_name`, `_street_is_department` + routing loops — enrichment/preprocess.py)

#### 1 Purpose

A whole street value that is actually an institution ("University of Miami Hospital") is moved into the name block — becoming Name 1 when the name block has no institution — and a street value that is actually a department ("Department of Neuroscience") is either moved to a name slot or dropped as redundant when the name block already holds a department (`enrichment/preprocess.py:1359-1417`).

#### 2 Inputs and outputs

- `_street_is_org_name(value) -> bool` (`enrichment/preprocess.py:1816-1841`).
- `_street_is_department(value) -> bool` (`enrichment/preprocess.py:1864-1888`), with `_is_functional_dept` (`enrichment/preprocess.py:1854-1861`).
- `_name_block_has_department(res) -> bool` — checks Name 2–4 only, because institution names in Name 1 ("Scripps Research Institute", "Moffitt Cancer Center") themselves read as unit/granular constructions (`enrichment/preprocess.py:2191-2203`).
- Routing loops mutate `name1..name4` and clear street slots (`enrichment/preprocess.py:1367-1389`, `1399-1417`).

#### 3 Pseudocode

`_street_is_org_name` (`enrichment/preprocess.py:1826-1841`):
1. Leading digit → False (address). `_CO_ATTN_PREFIX_RE` match → False (care-of line). `is_logistics_location` → False (distribution centre). `_extract_addresses` finds anything → False. `_UK_POSTCODE_RE` hit → False.
2. Mask street-institution words ("University Road"); True when `looks_like_research_institution(masked)` or `_LEGAL_SUFFIX_RE` matches the unmasked value.

Org routing loop (`enrichment/preprocess.py:1367-1389`):
1. For each street slot: skip unless `_street_is_org_name`.
2. `has_institution = name1 non-blank AND not is_unit_construction(name1)`.
3. If has_institution → move org to first empty name slot (silent skip when none). Else → if Name 1 holds a department, push it to the first empty name slot; set `name1 = org`.
4. Clear the street slot; **break** (first org only).

`_street_is_department` (`enrichment/preprocess.py:1871-1888`):
1. Leading digit → False; `is_logistics_location` → False; `_extract_addresses` finds anything → False.
2. True when `is_unit_construction(v)` or `is_granular_unit(v)` or `_is_ap_reference(v)` or `_is_functional_dept(v)`.

`_is_functional_dept` (`enrichment/preprocess.py:1854-1861`): split on `[/,&]` or the word "and"; True when all parts (lowercased) are in `_FUNCTIONAL_UNIT_WORDS`.

Department routing loop (`enrichment/preprocess.py:1399-1417`, no break):
1. For each street slot: skip unless `_street_is_department`.
2. If `_name_block_has_department(res)` → clear the slot (redundant); else → title-case ALL-CAPS input via `_smart_title_case` and move to the first empty name slot (leave in the street when none).

#### 4 Constants

```python
_FUNCTIONAL_UNIT_WORDS = {
    "finance", "procurement", "accounting", "purchasing", "payroll", "legal",
    "logistics", "operations", "marketing", "sales", "administration", "admin",
    "hr", "human resources", "accounts payable", "accounts receivable",
    "it", "information technology", "shipping", "receiving"}     # 20 items — enrichment/preprocess.py:1846-1851
```
(Other regexes used here are quoted in the splitter procedure above.)

#### 5 Complexity

Constant number of regex tests per street slot; `_street_is_org_name`/`_street_is_department` each re-run `_extract_addresses` → O(*p*·*n*) per slot.

#### 6 Worked example

From `tests/test_org_in_street.py:45-49`: `name1="Department of Cardiology"`, `street1="University of Miami Hospital"`.
- `_street_is_org_name("University of Miami Hospital")`: no leading digit, no c/o, not logistics, no address fragment, no UK postcode; `looks_like_research_institution` matches (both "University" and "Hospital" are signals, `utils/text_utils.py:355-363`) → True.
- `has_institution`: `is_unit_construction("Department of Cardiology")` is True (prefix form, `utils/text_utils.py:497-505`) → False.
- Name 1 holds a department → pushed to name2; `name1="University of Miami Hospital"`; street1 cleared.
- Asserted exactly that (`tests/test_org_in_street.py:47-49`).

Department redundancy from `tests/test_org_in_street.py:78-85`: existing `name2="Department of Cardiology"` → `_name_block_has_department` True → `street1="Department of Neuroscience"` cleared. Move-when-absent (`tests/test_org_in_street.py:96-99`): `name2="Department of Neuroscience"`, street cleared. Title-case interplay (`tests/test_org_in_street.py:123-134`): `name1="Scripps Research Institute"` (unit-like but in Name 1, hence ignored by `_name_block_has_department`) + `street1="CHEMISTRY DEPARTMENT"` → moved as `"Chemistry Department"` via `_smart_title_case`.

Logistics exclusion from `tests/test_org_in_street.py:104-115`: "SOUTHEAST DISTRIBUTION CTR" is neither org nor department (`_LOGISTICS_LOCATION_RE`, `utils/text_utils.py:662-670`) → stays in the street.

UK-address guard from `tests/test_person_org_in_street.py:56-74`: "ASTER HOUSE, 2A UNIVERSITY ROAD, BELFAST BT7 1NH" — the UK postcode makes `_street_is_org_name` False and `_segment_is_address("BELFAST BT7 1NH")` True; nothing lands in a name field.

#### 7 Failure modes

- Only the FIRST org-like street value moves (break at `enrichment/preprocess.py:1389`); a second org value in another street slot stays put.
- When the name block is full, the org is left in the street silently (`enrichment/preprocess.py:1376-1377`) and a department is likewise left with no flag (`enrichment/preprocess.py:1409-1410`).
- `_LEGAL_SUFFIX_RE`'s `Co`/`S.A.` alternates could mark an address line containing such tokens as an org; the leading-digit and address-pattern guards are the only mitigations (`enrichment/preprocess.py:1826-1841`). ⚠ NO FIXTURE COVERAGE for that specific collision.

---

### Named-building and location-fragment routing — UC 9 (`_named_building`, `_location_fragment` — enrichment/preprocess.py)

#### 1 Purpose

A name or street value that IS a named building ("Neil Armstrong Operations and Checkout Building") is a physical location, not a department, and is routed to the dedicated `building` output; a name value made entirely of location descriptor+identifier groups ("Wing C", "Annex D Pod 2") is moved verbatim to a street slot. Both run early so a person-like building prefix ("Neil Armstrong …") is never misread as a contact and the descriptors are never misread as a department (`enrichment/preprocess.py:1254-1290`, `676-701`).

#### 2 Inputs and outputs

- `_named_building(text) -> str | None` — the trimmed value when it is a bare named building (`enrichment/preprocess.py:713-733`).
- `_location_fragment(text) -> str | None` — the whitespace-normalised value when it is purely sub-locations (`enrichment/preprocess.py:704-710`).
- The orchestration loops write `res.building` / a street slot and clear the source (`enrichment/preprocess.py:1264-1271`, `1279-1290`).

#### 3 Pseudocode

`_named_building` (`enrichment/preprocess.py:713-733`):
1. Blank → None. A `,` or `|` in the value → None (multi-segment values are left to the splitters).
2. A leading house number (`^\d+\s`) → None (a street address such as "100 Rhines Hall", handled by UC 9 pattern 2).
3. `_BUILDING_IDENTIFIER_RE` match ("Building 5") → None (identifier form belongs to the address extractor).
4. `_DEPT_KEYWORDS_RE` hit → None.
5. `_NAMED_BUILDING_RE` match (ends in "Building"/"Bldg" with at least one descriptive word before) → return the value; else None.

`_location_fragment` (`enrichment/preprocess.py:704-710`): whitespace-normalise; return the value iff `_LOCATION_FRAGMENT_RE` matches the WHOLE string (one or more descriptor+identifier groups; each identifier must contain a digit or be 1–2 letters, so "Wing Commander" fails).

Routing: named-building loop moves only the first match across (`name2`,`name3`,`name4`,`street1`..`street5`) — name slots checked first, `name1` exempt (`enrichment/preprocess.py:1262-1271`); location-fragment loop processes all of (`name2`,`name3`,`name4`), each fragment kept as ONE unit in one street slot (`enrichment/preprocess.py:1279-1290`).

#### 4 Constants

```python
_NAMED_BUILDING_RE = re.compile(r"\S.*\s(?:Building|Bldg)\.?\s*$", re.IGNORECASE)          # enrichment/preprocess.py:683
_BUILDING_IDENTIFIER_RE = re.compile(r"^(?:Building|Bldg)\.?\s+\w[\w\-]*\s*$", re.IGNORECASE)  # enrichment/preprocess.py:684
_LOC_DESCRIPTORS = (
    r"Wing|Annex|Pod|Bay|Block|Module|Dock|Gate|Level|Hall|Pavilion|Tower|"
    r"Suite|Ste|Unit|Floor|Fl|Room|Rm|Bldg|Building|Mail\s*Stop|MS")                       # enrichment/preprocess.py:692-695
_LOC_ID = r"(?:[\w]*\d[\w]*|[A-Za-z]{1,2})"                                                # enrichment/preprocess.py:696
_LOCATION_FRAGMENT_RE = re.compile(
    rf"^(?:(?:{_LOC_DESCRIPTORS})\.?[\s\-]+{_LOC_ID}\b[\s,]*)+$", re.IGNORECASE)            # enrichment/preprocess.py:699-701
```
The descriptor and identifier may be separated by whitespace OR a hyphen ("Wing-C", "Pod-2") (`enrichment/preprocess.py:697-698`).

#### 5 Complexity

One regex battery per slot: O(*n*) per field.

#### 6 Worked example

From `tests/test_named_building.py:44-49`: `name2="Neil Armstrong Operations and Checkout Building"` (with `name1="HCA Florida University Hospital"`).
- No comma/pipe, no leading number, not the identifier form, no dept keyword; ends in "Building" with descriptors before → `_named_building` returns it.
- Asserted: `pre.building == "Neil Armstrong Operations and Checkout Building"`, `pre.name2 is None`, and `pre.contact is None` — the person-like prefix is not extracted because the value left the name block before UC 7 ran.
- Rejections (`tests/test_named_building.py:34-39`): "Department of Chemistry", "Building 5", "Bldg", "", None → all None.
- Street-slot variant (`tests/test_named_building.py:70-78`): `street2="Engineering Building"` routes to `building`, `street1="100 Main St"` untouched; but `street1="100 Rhines Hall"` (numbered) does NOT route (`tests/test_named_building.py:81-88`).

Location fragments from `tests/test_named_building.py:168-183`: "Wing C", "Annex D Pod 2", "Bay 4", "Floor 3 Room 12", "Suite 400", "Wing-C", "Pod-2", "WING-C" all detected; "Wing Commander", "Department of Chemistry", "Chemistry Lab", "Smith Hall" rejected. Movement (`tests/test_named_building.py:193-198`): `name3="Annex D Pod 2"` with `street1="100 Main St"` → whole fragment lands in `street2` as one unit.

#### 7 Failure modes

- Only ONE building is captured per record (break at `enrichment/preprocess.py:1271`); a second named building in another slot stays where it is.
- An unnumbered hall not ending in "Building/Bldg" ("Rhines Hall", "Carnegie Hall") matches neither detector and remains a name value (`tests/test_named_building.py:137-142`) — routed only if some other stage claims it.
- `_LOCATION_FRAGMENT_RE` accepts 1–2 letter identifiers, so "Hall A" or "MS B" style fragments route to a street slot even when they might be organisational shorthand (`enrichment/preprocess.py:696`). ⚠ NO FIXTURE COVERAGE for that boundary.

---

### Opaque-code detection and clearing — UC 10 (`_is_opaque_code`, `_strip_leading_opaque_code` — enrichment/preprocess.py)

#### 1 Purpose

Values that are clearly internal identifiers, not names, are removed: a name field consisting ONLY of a code is cleared (Name 2–4), and a leading account/customer code prefixed onto real content is stripped so the content (in particular a following c/o clause) is exposed for later stages (`enrichment/preprocess.py:305-314`, `505-516`, `1200-1211`, `1574-1581`).

#### 2 Inputs and outputs

- `_is_opaque_code(text) -> bool` — full-value match (`enrichment/preprocess.py:317-320`).
- `_strip_leading_opaque_code(value) -> str | None` — value with leading code token(s) removed; a value that is only a code is left untouched (`enrichment/preprocess.py:505-526`).

#### 3 Pseudocode

`_strip_leading_opaque_code` (`enrichment/preprocess.py:517-526`):
1. Blank → unchanged.
2. Loop: split off the first whitespace-delimited token; if exactly two parts exist and the first token (stripped of `,;:|/`) matches `_LEADING_ACCOUNT_CODE_RE`, keep only the remainder and repeat; else stop. Because two parts are required, a value that is ONLY a code never strips (left for the full-field clearing).

Full-field clearing (`enrichment/preprocess.py:1577-1581`): for `name2`/`name3`/`name4` only — `name1` is exempt — clear the slot when `_is_opaque_code(value)`.

Related token-level stripping (UC 13): `_strip_name3_junk` removes URLs (`_URL_RE`), phone/fax numbers (`_PHONE_RE`), and standalone tokens matching `_OPAQUE_CODE_TOKEN_RE` from Name 3/4 (`enrichment/preprocess.py:554-564`, applied `1670-1679`); `_strip_street_junk` removes URLs and phones only — numeric codes are retained so street numbers survive (`enrichment/preprocess.py:567-577`).

#### 4 Constants

```python
_OPAQUE_CODE_RE = re.compile(r"^\s*[A-Za-z]{0,4}[-]?\d{5,}\s*$")            # enrichment/preprocess.py:312-314
_LEADING_ACCOUNT_CODE_RE = re.compile(r"^[A-Za-z]{1,4}-?\d{2,}$")            # enrichment/preprocess.py:326
_OPAQUE_CODE_TOKEN_RE = re.compile(r"^(?:\d{4,}|[A-Za-z]{1,4}-?\d{2,})$")    # enrichment/preprocess.py:551
_URL_RE = re.compile(
    r"\b(?:https?://|www\.)\S+"
    r"|(?<!@)\b[A-Za-z0-9][A-Za-z0-9\-]*\."
    r"(?:com|org|net|edu|gov|io|co|us|biz|info|gmbh|de|uk|ca)\b(?:/\S*)?", re.IGNORECASE)  # enrichment/preprocess.py:533-540
_PHONE_RE = re.compile(
    r"(?:\b(?:tel|telephone|phone|fax|ph|cell|mobile|mob)\b\.?[:\s#]*)?"
    r"\+?\(?\d{2,4}\)?(?:[\s.\-]\d{2,4}){2,4}"
    r"(?:\s*(?:ext|x|extension)\.?\s*\d+)?", re.IGNORECASE)                  # enrichment/preprocess.py:543-548
```
`_LEADING_ACCOUNT_CODE_RE` REQUIRES a letter prefix precisely so a leading house number ("10901 Roosevelt Blvd N") or a numeric name component ("3M", "21st Century Fox", "100 Black Men of America") is never stripped (`enrichment/preprocess.py:323-325`, `512-516`).

#### 5 Complexity

Leading-strip: at most one split per stripped token — O(*k*·*n*) for *k* leading codes. Full-field clearing: one anchored regex per slot, O(*n*).

#### 6 Worked example

From `tests/test_leading_code_strip.py:15-28` (parametrised, all asserted):
- `"B800000123 c/o Dr. Mark Adams"` → `"c/o Dr. Mark Adams"` (token "B800000123" matches `^[A-Za-z]{1,4}-?\d{2,}$`).
- `"NT30 Division of Cardiology"` → `"Division of Cardiology"`; `"SAP-42 Purchasing"` → `"Purchasing"`.
- Untouched: `"10901 Roosevelt Blvd N"` (no letter prefix), `"3M Company"` ("3M" = one letter AFTER digits — no match), `"21st Century Fox"`, `"100 Black Men of America"`, `"A1 Plumbing"` ("A1" has only 1 digit, needs ≥ 2), `"B800000123"` alone (single token — the two-part requirement fails; the comment notes "lone code → UC 10 clears it elsewhere", `tests/test_leading_code_strip.py:25`).
- All-slot behaviour (`tests/test_leading_code_strip.py:42-52`): codes stripped from name1/name2/name3 simultaneously.
- House-number regression (`tests/test_leading_code_strip.py:55-63`): `name2="10901 Roosevelt Blvd N"` is not stripped as a code; UC 9 moves the full address to `street1`.

⚠ NO FIXTURE COVERAGE — the full-field clearing loop (`enrichment/preprocess.py:1577-1581`) has no direct end-to-end test in the assigned suite (a lone `name2="B800000123"` through `preprocess_record`); the token-level strip inside Name 3 is covered by `tests/test_preprocess_co_attn.py:230-231` (`"Billing NT30 800000070"` → `"Billing"`).

#### 7 Failure modes

- Name 1 is deliberately never cleared by the full-field rule, so an opaque code occupying Name 1 survives preprocessing (`enrichment/preprocess.py:1577`).
- `_OPAQUE_CODE_RE` requires ≥ 5 digits; 4-digit codes in a full field are not cleared (though the token form `\d{4,}` in Name 3 junk-stripping catches standalone 4-digit tokens — `enrichment/preprocess.py:312-314` vs `551`).
- `_PHONE_RE` can consume digit groups that are not phone numbers (e.g. spaced numeric sequences) since the label is optional (`enrichment/preprocess.py:543-548`).

---

### DBA normalisation — UC 11 (`_normalise_dba` — enrichment/preprocess.py)

#### 1 Purpose

Rewrite every variant of the "doing business as" marker inside a name field to the canonical short form "DBA", preserving the surrounding text, and record which fields were normalised in `dba_fields` so downstream tiers do not strip the marker (finalise re-prepends "DBA " if an LLM canonicalisation drops it) (`enrichment/preprocess.py:260-266`, `70-73`, `1546-1557`).

#### 2 Inputs and outputs

`_normalise_dba(text) -> (normalised_text, changed)` (`enrichment/preprocess.py:283-301`). The UC 11 loop writes the field, adds the field name to `res.dba_fields`, and notes UC 11 (`enrichment/preprocess.py:1549-1557`).

#### 3 Pseudocode

(`enrichment/preprocess.py:290-301`)
1. Blank → `(text, False)`.
2. For each of the five `_DBA_PATTERNS` in order (longest phrases first so "Doing Business As" wins over a partial "D B A" inside it, `enrichment/preprocess.py:264-266`): `subn("DBA")`; record `changed` on any substitution.
3. If changed: collapse whitespace and strip `" ,;/|-"`.
4. Return `(result, changed)`.

#### 4 Constants

```python
_DBA_PATTERNS = [
    re.compile(r"\bdoing\s+business\s+as\b", re.IGNORECASE),
    re.compile(r"\bd\.?\s+business\s+as\b", re.IGNORECASE),
    re.compile(r"\bd\s*\.?\s*/\s*b\s*\.?\s*/\s*a\b\.?", re.IGNORECASE),
    re.compile(r"\bd\.\s*b\.\s*a\.?", re.IGNORECASE),
    re.compile(r"\bd\s*b\s*a\b", re.IGNORECASE),
]                                                                # enrichment/preprocess.py:267-280
```

#### 5 Complexity

Five global substitutions per field: O(*n*).

#### 6 Worked example

⚠ NO FIXTURE COVERAGE — none of the assigned test files (nor any other `tests/` file) exercises `_normalise_dba` or UC 11 through `preprocess_record`; the only DBA-related tests target downstream consumers of `dba_fields` (`tests/test_search_terms_fixes.py:79-81` uses a pre-built `_dba_values` dict; `tests/test_issue_detection.py:203` tests issue detection, not preprocessing).

#### 7 Failure modes

- The final pattern `\bd\s*b\s*a\b` matches the letter sequence "d b a" across word boundaries, so an unrelated token sequence with those single letters would be rewritten; the ordering mitigates but does not remove this (`enrichment/preprocess.py:278-279`).
- `_normalise_dba` marks `changed` even when the input already reads "DBA" in a variant casing handled by pattern 5 (e.g. lowercase "dba" → "DBA"), adding the field to `dba_fields` — intended behaviour per the marker-preservation contract (`enrichment/preprocess.py:70-73`). ⚠ UNVERIFIED — no fixture demonstrates either behaviour.

---

### Duplicate-name clearing (UC 12) and acronym/full-form dedupe (`_collapse_repeated_phrase`, `_strip_redundant_acronym`, UC 12 pairwise loop — enrichment/preprocess.py)

#### 1 Purpose

Three related dedupe mechanisms: (a) a single field that is one phrase repeated back-to-back is collapsed to one occurrence; (b) Name 1 carrying BOTH an abbreviation and its expansion for the same entity keeps only the full form (dash, parenthetical, and adjacent forms); (c) at the end of preprocessing, name slots holding canonically equivalent (or near-identical, fuzz ≥ 92) values have the later slot cleared silently (`enrichment/preprocess.py:329-353`, `444-502`, `1749-1796`).

#### 2 Inputs and outputs

- `_collapse_repeated_phrase(value) -> str | None` (`enrichment/preprocess.py:329-353`).
- `_is_acronym_token(token) -> bool`; `_acronym_matches_phrase(acronym, phrase) -> bool`; `_dash_acronym_full(value) -> (acronym, full, is_verified_initialism) | None`; `_syllabic_dash_abbrev(value) -> str | None` (flag reason); `_strip_redundant_acronym(value) -> str | None` (`enrichment/preprocess.py:364-502`).
- The UC 12 pairwise loop mutates `name2..name4` (`enrichment/preprocess.py:1779-1796`).

#### 3 Pseudocode

`_collapse_repeated_phrase` (`enrichment/preprocess.py:340-353`):
1. Tokenise on whitespace; < 2 tokens → unchanged.
2. For each period `p` from 1 to n//2 dividing n: if the lowercased token list is `n/p` repetitions of its first `p` tokens, return the first `p` original-cased tokens joined. Else unchanged.

`_is_acronym_token` (`enrichment/preprocess.py:364-371`): letters only, 2–8 of them, all upper-case (interior dots/ampersands allowed: "U.C.L.A.", "AT&T").

`_acronym_matches_phrase` (`enrichment/preprocess.py:374-389`): the letter-only acronym (≥ 2 letters) must equal the initials of ALL words of the phrase, or of its significant words (skipping `_ACRONYM_STOPWORDS`) — so both "UCLA" and "IBM" verify.

`_dash_acronym_full` (`enrichment/preprocess.py:403-425`): split on the first dash (`_DASH_ACRONYM_RE`); one side must be a single token of 2–8 letters and the other a phrase of ≥ 3 words (so "Heriot-Watt University" and "Bio-Rad" never split, `enrichment/preprocess.py:392-394`); return `(acro, full, _acronym_matches_phrase(acro, full))`.

`_strip_redundant_acronym` (`enrichment/preprocess.py:456-502`):
1. Blank → unchanged; collapse whitespace.
2. Dash form: if `_dash_acronym_full` fires AND (the initialism is verified OR the dash is spaced (`_SPACED_DASH_RE`)): return the full side with `&` rewritten to " and " (" AND " when the full form is ALL-CAPS so downstream title-casing still applies), stripped of `" ,;-"`. An unspaced, unverified hyphen ("Dana-Farber Cancer Institute") is a proper noun and left intact (`enrichment/preprocess.py:466-475`).
3. Parenthetical form: "Full (ACRO)" → outer kept when the inner token is an acronym verifying against the outer; "ACRO (Full)" → inner kept in the mirror case (`enrichment/preprocess.py:477-488`).
4. Adjacent form (≥ 3 tokens): a leading or trailing acronym token verifying against the rest → the rest (`enrichment/preprocess.py:490-500`).
5. Else unchanged.

`_syllabic_dash_abbrev` (`enrichment/preprocess.py:428-441`): returns a review-flag string when the dash form exists but the abbreviation is NOT a verified initialism ("CALIBR - California Institute for Biomedical Research"); used by the Name 1 pass to emit `"acronym-ambiguous: …"` (`enrichment/preprocess.py:1236-1244`).

UC 12 pairwise loop (`enrichment/preprocess.py:1758-1796`):
1. `_norm(v)`: canonical unit form via `canonicalise_unit_name` (fallback to the raw value), whitespace-collapsed, lowercased (`enrichment/preprocess.py:1761-1765`).
2. `_equiv(a, b)`: both non-empty and (`_norm` values equal OR `fuzz.ratio ≥ 92`) — the threshold chosen so "Physics" vs "Physiology" are not merged (`enrichment/preprocess.py:1768-1776`).
3. Apply in the fixed order name3/name4 → name2/name4 → name1/name4 → name2/name3 → name1/name3 → name1/name2, clearing the later slot on equivalence; no review flag is raised (informational note only, `enrichment/preprocess.py:1756-1757`).

#### 4 Constants

```python
_ACRONYM_STOPWORDS = {
    "of", "and", "the", "for", "at", "in", "on", "de", "la", "le", "du",
    "des", "von", "van", "der", "el", "&", "a"}                       # 18 items — enrichment/preprocess.py:358-361
_DASH_ACRONYM_RE = re.compile(r"^\s*(.+?)\s*[-‐-―]\s*(.+?)\s*$")      # enrichment/preprocess.py:395
_SPACED_DASH_RE = re.compile(r"\s[-‐-―]|[-‐-―]\s")                    # enrichment/preprocess.py:400
```
Fuzz threshold: `92` (`enrichment/preprocess.py:1776`); imported comparator `fuzz.ratio` from rapidfuzz (`enrichment/preprocess.py:32`).

#### 5 Complexity

`_collapse_repeated_phrase`: O(*t*·*d*(*t*)) token comparisons over the divisors of *t*. `_strip_redundant_acronym`: constant regex passes, O(*n*). The UC 12 loop performs at most 6 `_equiv` calls; each `canonicalise_unit_name` is O(*n*) and `fuzz.ratio` is O(*n*·*m*/64) (rapidfuzz bit-parallel Levenshtein similarity).

#### 6 Worked example

Repeated phrase from `tests/test_canonical_dedup.py:15-36`: `"Department of Central Receiving Department of Central Receiving"` (8 tokens) — period p=4: `["department","of","central","receiving"]` repeats twice → collapsed to `"Department of Central Receiving"`; asserted both at helper level and through `preprocess_record` on Name 2. Non-repetitions ("Department of Central Receiving Department of Physics") unchanged.

Canonical-variant dedupe from `tests/test_canonical_dedup.py:46-55`: `name2="Department of Main Receiving"`, `name3` ∈ {"Department of Main Receiving", "Main Receiving Department", "Main Receiving Dept", "MAIN RECEIVING DEPT"}. Intermediate for the abbreviation case: `expand_abbreviations` rewrites "Dept" → "Department" (`utils/text_utils.py:175`, `204-215`); `canonicalise_unit_name` rewrites the suffix form to "Department of Main Receiving" (`utils/text_utils.py:577-595`); `_norm` lowercases both to `"department of main receiving"` → equal → `name3` cleared. Typo case (`tests/test_canonical_dedup.py:64-68`): "Department of Main Receivingt" — canonical forms differ by one character; `fuzz.ratio("department of main receiving", "department of main receivingt")` ≈ 98 ≥ 92 → cleared. Distinct departments ("Physics" vs "Physiology", `tests/test_canonical_dedup.py:71-75`) survive.

Acronym dedupe from `tests/test_acronym_dedupe.py:28-44`: e.g. `"MIT Massachusetts Institute of Technology"` → adjacent form, tokens[0]="MIT" is an acronym token and equals initials-of-significant-words "MIT" → `"Massachusetts Institute of Technology"`. `"University of California, Los Angeles (UCLA)"` → parenthetical form with stopword-skipping initials "UCLA". Dash form `"FDA - Food & Drug Administration"` → `"Food and Drug Administration"` (`&`→"and"). Unrelated tokens ("UC Berkeley", "3M Company", "AT&T", "IT University of Copenhagen", "US Army Corps of Engineers") untouched (`tests/test_acronym_dedupe.py:46-55`). Syllabic case (`tests/test_acronym_dedupe.py:96-103`): `"CALIBR - California Institute for Biomedical Research"` → full form kept AND flag containing "acronym-ambiguous"; verified case "Njit - New Jersey Institute of Technology" → no flag (`tests/test_acronym_dedupe.py:105-112`). ALL-CAPS interplay (`tests/test_acronym_dedupe.py:114-118`): `"FDA - FOOD & DRUG ADMINISTRATION"` → `"FOOD AND DRUG ADMINISTRATION"` → `smart_title_case` → `"Food and Drug Administration"`.

#### 7 Failure modes

- UC 12 clearing is silent (no review flag), by design (`enrichment/preprocess.py:1756-1757`).
- The 92-ratio near-duplicate rule can in principle merge short distinct unit names whose canonical forms are ≥ 92 similar; the chosen threshold is validated only against the Physics/Physiology pair in fixtures (`tests/test_canonical_dedup.py:71-75`).
- `_strip_redundant_acronym`'s spaced-dash branch strips even UNVERIFIED abbreviations ("Tuhh - …"), relying on the flag for human review — an incorrect pairing would still lose the abbreviation from the data (`enrichment/preprocess.py:466-475`, `1236-1242`).

---

### Slot consolidation and promotion — UC 14 (promotion gate + leftward packing in `preprocess_record` — enrichment/preprocess.py)

#### 1 Purpose

The dept-lookup tiers (ROR child match, Tier 2 canonical) operate on Name 2; a record with its department in Name 3 and Name 2 blank would skip those tiers, so gaps in (name2, name3, name4) are closed leftward. Additionally, when Name 1 held only a person (now moved to Contact) and the street→name routing had parked the organisation in Name 2 (because Name 1 was still occupied at routing time), that organisation is promoted into the now-empty Name 1 — gated so a bare department is NOT promoted (the record should instead flag for an org lookup) (`enrichment/preprocess.py:1703-1734`).

#### 2 Inputs and outputs

Operates on `res.name1..name4`, `res.name1_was_person`; notes UC 14 (`enrichment/preprocess.py:1721-1747`).

#### 3 Pseudocode

Promotion (`enrichment/preprocess.py:1721-1729`):
1. If `name1_was_person` AND Name 1 blank AND Name 2 non-blank AND (`_looks_like_institution(name2)` OR `_looks_like_org_acronym(name2)`): note UC 14; `name1 = name2`; `name2 = None`.

Packing (`enrichment/preprocess.py:1735-1747`):
2. Build `current = [stripped-or-None for (name2, name3, name4)]`; `packed = non-None values + None padding to length 3`.
3. If `current != packed`: note UC 14 and assign `name2, name3, name4 = packed`.

#### 4 Constants

The promotion gate reuses `_looks_like_institution` (`enrichment/preprocess.py:2010-2020`) and `_ORG_ACRONYM_RE = re.compile(r"^[A-Z]{2,6}$")` via `_looks_like_org_acronym` (`enrichment/preprocess.py:2145-2152`).

#### 5 Complexity

O(1) — fixed three-slot list operations plus one regex test.

#### 6 Worked example

From `tests/test_person_org_in_street.py:37-44` (production row 90000286): `name1="JOHN F FLOREK,"`, `street1="Tufts University, 15 Ryan Dr"`, verdict `{"john f florek": "person"}`.
- Comma splitter: "Tufts University" is an org segment, "15 Ryan Dr" an address → org routed; Name 1 still holds the person at routing time, and `is_unit_construction("JOHN F FLOREK,")` is False → `has_institution` True → org parked in `name2`; `street1="15 Ryan Dr"`.
- UC 7: verdict extraction (normalised candidate "John F Florek") → `contact="John F Florek"`, `name1=None`, `name1_was_person=True`.
- UC 14 promotion: Name 1 blank, `name2="Tufts University"` matches `_INSTITUTION_RE` ("University") → promoted → `name1="Tufts University"`, `name2=None`.
- Asserted: `contact == "John F Florek"`, `name1 == "Tufts University"`, name2 blank, `street1 == "15 Ryan Dr"`.
- Acronym variant (`tests/test_person_org_in_street.py:46-53`, row 90000029): `"UCSF; 600 16th Avenue"` → semicolon splitter parks "UCSF" in name2; after person extraction the promotion fires via `_looks_like_org_acronym("UCSF")` → `name1="UCSF"`.
- Gating counter-example (`tests/test_person_org_in_street.py:95-107`): `name1="JANE SMITH"`, `name2="Department of Chemistry"` → `_DEPT_LEAD_RE` blocks `_looks_like_institution` → NOT promoted; Name 1 stays empty; `name2` keeps the department.

#### 7 Failure modes

- Packing only runs once, at the end; a slot cleared by UC 12 (which runs after) can leave `name2` blank with `name3` populated in the final result when name1/name2 were duplicates — mitigated by the UC 12 comparison order but not re-packed (`enrichment/preprocess.py:1749-1796` follows `1735-1747`).
- Promotion requires `name1_was_person`; an org parked in Name 2 for any other reason (e.g. Name 1 emptied by an address extraction) is not promoted (`enrichment/preprocess.py:1721-1726`).

---

### Legal-suffix normalisation — UC 17 (`_normalise_legal_suffix` — enrichment/preprocess.py)

#### 1 Purpose

Collapse long-form legal-entity designators to their canonical abbreviations ("Aktiengesellschaft" → "AG", "Incorporated" → "Inc") so a company resolves on the same enrichment path regardless of which legal form the source system recorded; bare ambiguous words ("Limited", "Company") are left alone because they occur as real name components (`enrichment/preprocess.py:808-816`, `827-833`). The production motivation is recorded in the code comment: without it, "Carl Zeiss Aktiengesellschaft" missed ROR, passed the raw suffix into the web search, and resolved to an unrelated site (`enrichment/preprocess.py:814-816`; also `tests/test_legal_suffix_normalisation.py:4-10`).

#### 2 Inputs and outputs

`_normalise_legal_suffix(text) -> (normalised_text, changed)`; `None`/`""` → `("", False)` (`enrichment/preprocess.py:827-840`). The UC 17 loop applies it to all four name slots (`enrichment/preprocess.py:1565-1572`).

#### 3 Pseudocode

(`enrichment/preprocess.py:834-840`)
1. Blank → `("", False)`.
2. Apply each of the six `_LEGAL_LONGFORM_SUBS` substitutions in order; collapse whitespace and strip.
3. `changed = (out != text)`.

#### 4 Constants

```python
_LEGAL_LONGFORM_SUBS = [
    (re.compile(r"\bGesellschaft\s+mit\s+beschr[äa]nkter\s+Haftung\b", re.IGNORECASE), "GmbH"),
    (re.compile(r"\bLimited\s+Liability\s+Company\b", re.IGNORECASE), "LLC"),
    (re.compile(r"\bLimited\s+Liability\s+Partnership\b", re.IGNORECASE), "LLP"),
    (re.compile(r"\bAktiengesellschaft\b", re.IGNORECASE), "AG"),
    (re.compile(r"\bIncorporated\b", re.IGNORECASE), "Inc"),
    (re.compile(r"\bCorporation\b", re.IGNORECASE), "Corp"),
]                                                                # enrichment/preprocess.py:817-824
```
A mirror list `_LONGFORM_LEGAL_SUBS` exists in `utils/text_utils.py:619-626` for the identity guard and finalise (kept in the lower-level module to avoid a circular import, `utils/text_utils.py:615-618`).

#### 5 Complexity

Six global regex substitutions per field: O(*n*).

#### 6 Worked example

From `tests/test_legal_suffix_normalisation.py:39-54` (parametrised, all asserted with `changed is True`): "Carl Zeiss Aktiengesellschaft" → "Carl Zeiss AG"; "Acme Incorporated" → "Acme Inc"; "Globex Corporation" → "Globex Corp"; "Initech Limited Liability Company" → "Initech LLC"; "Initech Limited Liability Partnership" → "Initech LLP"; "Beispiel Gesellschaft mit beschränkter Haftung" (and the unaccented "beschrankter" variant) → "Beispiel GmbH". Unchanged (`tests/test_legal_suffix_normalisation.py:56-69`): "Carl Zeiss AG", "Acme Inc", "Limited Brands", "The Walt Disney Company", "University of Stuttgart". Integration (`tests/test_legal_suffix_normalisation.py:77-89`): through `preprocess_record`, `name1="Carl Zeiss Aktiengesellschaft"` → `"Carl Zeiss AG"` with `17 in use_cases`; the short form does not record UC 17; both forms converge on `"Carl Zeiss AG"`. The `Corporation` rule also fires inside UC 16's test: "United Technologies Corporation" → "United Technologies Corp" (`tests/test_preprocess_co_attn.py:385-390`).

#### 7 Failure modes

- "Corporation" is rewritten even when it is a distinctive part of a name-bearing agency ("… Corporation" as the head noun in a public-body name would still be shortened); only bare "Limited"/"Company" are protected (`enrichment/preprocess.py:822-823`, `830-833`).
- Umlaut handling covers `ä`/`a` but not the transliteration "ae" ("beschraenkter") (`enrichment/preprocess.py:818`). ⚠ NO FIXTURE COVERAGE for that spelling.

---

### Admin-desk recognition — UC 6 (`_is_ap_reference` — enrichment/preprocess.py; `is_admin_unit` — utils/text_utils.py)

#### 1 Purpose

Two related classifiers. In preprocessing, `_is_ap_reference` detects any accounts-payable wording in a name field, and UC 6 replaces the field with the canonical label "Accounts Payable" (`enrichment/preprocess.py:90-108`, `1475-1482`). Downstream, `is_admin_unit` classifies a broader family of administrative/back-office desks (finance, billing, procurement, treasury, …) to drive `search_term_2 = "ADMIN"` and to suppress the department-domain probe before any fetch/SERP occurs (`utils/text_utils.py:990-997`; consumers: `enrichment/orchestrator.py:1020`, `enrichment/search_terms.py:535`). `_is_ap_reference` is also reused inside UC 15 Case C (`enrichment/preprocess.py:797-798`), the UC 7 defensive guard (`enrichment/preprocess.py:1611-1618`), and the street-department detector (`enrichment/preprocess.py:1886`).

#### 2 Inputs and outputs

- `_is_ap_reference(text) -> bool` — True when any of six patterns matches anywhere (`enrichment/preprocess.py:105-108`).
- `is_admin_unit(text) -> bool` — True when the value, after prefix-stripping and cleanup, is exactly a known admin term (English only; German deferred per comment, `utils/text_utils.py:974-976`).

#### 3 Pseudocode

UC 6 loop (`enrichment/preprocess.py:1478-1482`):
1. For each name slot: if `_is_ap_reference(value)` → set the field to the literal `"Accounts Payable"` and note UC 6.

`is_admin_unit` (`utils/text_utils.py:998-1011`):
1. Blank → False. Lowercase and strip.
2. Strip the FIRST matching leading prefix from `_ADMIN_PREFIXES` (so "Office of Finance" reduces to "finance" but "Office of Research" reduces to "research", which is not an admin term).
3. Remove all characters outside `[a-z/& ]`, collapse whitespace.
4. True when the residue is in `_ADMIN_UNIT_TERMS`, or is `"a/p"` or `"a/r"`; else False.

#### 4 Constants

```python
_AP_PATTERNS = [
    re.compile(r"\baccounts?\s+payable\b", re.IGNORECASE),
    re.compile(r"\baccts?\.?\s+payable\b", re.IGNORECASE),
    re.compile(r"\bacct\.?\s+payable\b", re.IGNORECASE),
    re.compile(r"\ba\s*/\s*p\b", re.IGNORECASE),
    re.compile(r"\bap\b(?:\s+invoice|\s+dept|\s+department|\s+div|\s+division)", re.IGNORECASE),
    re.compile(r"\baccounts?\s+pay\b", re.IGNORECASE),
]                                                                # enrichment/preprocess.py:94-102
```

```python
_ADMIN_UNIT_TERMS = {
    "accounts payable", "accounts receivable", "ap", "ar",
    "finance", "financial services", "billing", "invoicing",
    "invoice processing", "purchasing", "procurement", "controlling",
    "treasury", "bursar", "comptroller", "general accounting",
    "shared services"}                                            # 17 items — utils/text_utils.py:977-983
_ADMIN_PREFIXES = (
    "office of ", "department of ", "dept of ", "dept. ", "dept ",
    "division of ", "div of ", "div. ")                           # utils/text_utils.py:984-987
```

#### 5 Complexity

`_is_ap_reference`: up to six regex searches, O(*n*). `is_admin_unit`: O(*n*) string cleanup plus O(1) set membership.

#### 6 Worked example

`_is_ap_reference` via UC 15 Case C, from `tests/test_uc15_co_attn.py:82-86`: `name2="Attn: Accounts Payable"` → prefix stripped; `_is_department_payload` → `_is_ap_reference("Accounts Payable")` matches pattern 1 → Case C keeps `name2="Accounts Payable"` (care_of and contact remain None). Street routing via `_is_ap_reference`, from `tests/test_street_org_split.py:33-41`: `street1="Accounts Payable"` → `_street_is_department` True → moved to `name2`; `"A/P Dept"` (pattern 4) likewise.

`is_admin_unit`, from `tests/test_search_terms_fixes.py:135-143` (parametrised, all asserted): "Accounts Payable" → True; "Accounts Receivable" → True; "Office of Finance" → True (prefix "office of " stripped → "finance" ∈ terms); "Billing", "Procurement", "Treasury", "Shared Services", "AP" → True; "Office of Research" → False ("research" ∉ terms); "Department of Chemistry" → False.

#### 7 Failure modes

- UC 6 replaces the WHOLE field on any AP hit, discarding co-resident content that survived to that stage; earlier stages (UC 15 Case C, UC 7 Pattern A) exist specifically to rescue contacts before UC 6 fires (`enrichment/preprocess.py:1419-1424`, `1478-1482`).
- `is_admin_unit` is exact-match after cleanup; compound values such as "Finance and Administration" do not match (the residue is not a single term) (`utils/text_utils.py:1005-1011`). ⚠ NO FIXTURE COVERAGE for compound admin values.
- The bare-token pattern `\bap\b` requires a following dept/invoice word, so "AP" alone in a name field is NOT an AP reference for UC 6 (`enrichment/preprocess.py:100`), while `is_admin_unit("AP")` IS admin (`utils/text_utils.py:978`, `tests/test_search_terms_fixes.py:139`) — an intentional asymmetry between field rewriting and search-term gating.

---

### smart_title_case (`smart_title_case` — utils/text_utils.py)

#### 1 Purpose

Title-case an ALL-CAPS value while preserving acronyms, connectors, hyphen segments, and Mc-surnames; mixed-case input is returned unchanged so canonical ROR/LLM names are never altered (`utils/text_utils.py:285-298`). Preprocessing binds it as `_smart_title_case` for the street-department router (`enrichment/preprocess.py:2206-2208`, used at `1414`); it is also the first step of `clean_passthrough_org_name` (`utils/text_utils.py:313-327`).

#### 2 Inputs and outputs

`smart_title_case(value: str | None) -> str | None` — the recased string, or the input unchanged when blank or not `str.isupper()` (`utils/text_utils.py:285-310`).

#### 3 Pseudocode

(`utils/text_utils.py:299-310`)
1. If blank or not all-upper → return unchanged.
2. Per whitespace token: if the lowercased token is in `_CASE_EXCEPTIONS` → emit the stored form; else if it contains `-` → case each hyphen segment independently with `_case_segment`; else → `_case_segment(token)`.

`_case_segment` (`utils/text_utils.py:266-282`), in order:
1. No letters → unchanged.
2. Lowercased segment in `_TITLE_CASE_CONNECTORS` → lowercase ("OF" → "of").
3. Upper letters in `_FORCE_TITLE_SHORT` → `capitalize()` + `_mc_name` ("BAY" → "Bay", "INC" → "Inc").
4. Upper letters in `_KEEP_UPPER_ACRONYMS` → unchanged ("NASA", "UCSF", "TUHH").
5. ≤ 3 letters → unchanged (default short tokens to acronyms: "IBM", "MRI", "LLC", "USA", "HCA").
6. ≤ 5 letters with no vowel → unchanged ("MGMT", "PLLC").
7. Else `capitalize()` + `_mc_name` (Mc-surname repair: `^(Mc)([a-z])(.+)$` → restore the internal capital; "Mac" intentionally untouched, `utils/text_utils.py:258-263`).

#### 4 Constants

```python
_TITLE_CASE_CONNECTORS = {"of", "and", "for", "the", "in", "at", "&"}          # utils/text_utils.py:219
_FORCE_TITLE_SHORT = {
    "INC", "LTD", "CO", "BAY", "NEW", "OLD", "SUN", "OAK", "BIG", "RED",
    "SKY", "SEA", "AIR", "SON", "TWO", "ONE", "KEY", "TOP", "BOX"}             # 19 items — utils/text_utils.py:227-230
_KEEP_UPPER_ACRONYMS = {
    "NASA", "NOAA", "NIH", "FDA", "USDA", "EMSL", "IEEE",
    "NIST", "NJIT", "TUHH", "NREL", "SLAC", "CERN", "CNRS", "CSIRO", "CCSF",
    "UCSF", "UCSD", "UCLA", "UCSB", "UCSC", "SUNY", "CUNY", "UMASS",
    "UPENN", "UCONN"}                                                          # 26 items — utils/text_utils.py:232-244
_VOWELS = set("AEIOU")                                                         # utils/text_utils.py:245
_CASE_EXCEPTIONS = {
    "bio-rad": "Bio-Rad", "abx-cro": "ABX-CRO",
    "dana-farber": "Dana-Farber", "at&t": "AT&T"}                              # utils/text_utils.py:250-255
```

#### 5 Complexity

One pass over the tokens with O(1) set lookups per segment: O(*n*).

#### 6 Worked example

From `tests/test_smart_title_case.py:17-34` (parametrised, all asserted): "ABX-CRO" → "ABX-CRO" (case exception); "BIO-RAD" → "Bio-Rad" (exception); "DANA-FARBER" → "Dana-Farber"; "VIRGIN-DOWNEY" → "Virgin-Downey" (each hyphen segment cased); "TECHNOLOGY-NIST" → "Technology-NIST" (segment "NIST" in the keep-upper set); "TUHH" → "TUHH"; "MCINTYRE" → "Mcintyre" via `capitalize()` then Mc-repair → "McIntyre"; "UCSF"/"UCSD"/"UCLA"/"SUNY"/"UMASS" all kept upper. From `tests/test_smart_title_case.py:38-50`: "SOUTH BAY HOSPITAL" → "South Bay Hospital" ("BAY" force-titled); "STERLING INDUSTRY LLC" → "Sterling Industry LLC" (3-letter default keeps "LLC"); "MRI DEPARTMENT" → "MRI Department"; "MACRON" → "Macron" (no Mac-repair); mixed-case "Bio-Rad", "University of Florida", "McIntyre" untouched. From `tests/test_passthrough_name_cleanup.py:29-52`: "SECRETARY OF STATE" → "Secretary of State" (connector); "E.R. SQUIBB AND SONS, L.L.C." → "E.R. Squibb and Sons, L.L.C.".

#### 7 Failure modes

- The all-upper gate means a single lowercase character anywhere disables the routine ("McDONALD" would pass through unchanged since it is not `isupper()`… ⚠ UNVERIFIED — no fixture for partially-cased input beyond the mixed-case pass-through cases cited above).
- Unknown vowel-bearing acronyms of ≥ 4 letters not in `_KEEP_UPPER_ACRONYMS` are down-cased (the comment instructs "Extend as they come up", `utils/text_utils.py:236`, `243`).
- 3-letter real words not in `_FORCE_TITLE_SHORT` stay upper-case (treated as acronyms) (`utils/text_utils.py:279`).

---

### Unit-name canonicalisation and unit classifiers (`canonicalise_unit_name`, `expand_abbreviations`, `is_unit_construction`, `is_granular_unit` — utils/text_utils.py)

#### 1 Purpose

These four routines are the shared vocabulary preprocessing relies on: `expand_abbreviations` rewrites academic abbreviations to full words; `canonicalise_unit_name` normalises unit names to the "Unit of/for Subject" form (used by UC 12's `_norm`, `enrichment/preprocess.py:1761-1765`); `is_unit_construction` recognises any academic-unit construction (used by the org-in-street router's `has_institution` gate and `_street_is_department`/`_segment_is_org`, `enrichment/preprocess.py:1373`, `1884`, `2052`); `is_granular_unit` recognises units below department scope — labs/groups/centres/facilities (used by `_street_is_department`, `_segment_is_org`, `_name_block_has_department`, `enrichment/preprocess.py:1885`, `2053`, `2201`).

#### 2 Inputs and outputs

- `expand_abbreviations(text) -> str | None` (`utils/text_utils.py:204-215`).
- `canonicalise_unit_name(text) -> str | None` (`utils/text_utils.py:540-597`).
- `is_unit_construction(text) -> bool` (`utils/text_utils.py:477-505`).
- `is_granular_unit(text) -> bool` (`utils/text_utils.py:410-474`).

#### 3 Pseudocode

`expand_abbreviations` (`utils/text_utils.py:210-215`): apply the 20 compiled `_ABBREV_MAP` substitutions in declaration order (misspellings of "University" first; "Med Ctr" → "Medical Center" must precede the generic "Med" → "Medicine" rule, `utils/text_utils.py:171-189`).

`canonicalise_unit_name` (`utils/text_utils.py:558-597`):
1. Blank → unchanged. Expand abbreviations; collapse whitespace; strip `" ,;.-"`.
2. Prefix pass: for each `(unit, connector)` in `_UNIT_CANONICAL_FORMS`, an existing "`<Unit>` of/for …" prefix is re-emitted with normalised unit casing and its canonical connector, and returned.
3. Suffix pass: "`<subject>` `<Unit>`" is rewritten to "`<Unit>` `<connector>` `<subject>`" — unless the subject is a single token in `_TRUNCATED_SUBJECTS` ("Biomed Dept" would fabricate the non-existent "Department of Biomed"; original text returned instead, `utils/text_utils.py:586-594`).
4. Else return the cleaned text.

`is_unit_construction` (`utils/text_utils.py:486-505`):
1. Blank → False. Expand abbreviations.
2. Job-title lead-in (`^(?:professor|prof|dr|doctor|lecturer|chair|dean|director)\b`) → False.
3. True when the text matches "`^(unit-word) (of|for) \S+`" or "`^\S.* (unit-word)\b\.?$`" over the nine `_UNIT_CANONICAL_FORMS` unit words.

`is_granular_unit` (`utils/text_utils.py:427-474`):
1. Blank → False. Expand abbreviations, lowercase.
2. In-scope heads are NEVER granular: `^(?:department|division|school|college|faculty)\s+(?:of|for)\s+` → False — so "Department of Pathology, Immunology and Laboratory Medicine" is a department despite containing "laboratory" (`utils/text_utils.py:420-439`).
3. Suffix forms: any of `laboratory, laboratories, lab, facility, facilities, center, centre, core` PLUS `group, unit, program, programme` as the final word after ≥ 1 other token → True (`utils/text_utils.py:447-466`).
4. Prefix forms: `^(laboratory|laboratories|lab|facility|facilities|center|centre|core)\s+(of|for)\s+` → True (`utils/text_utils.py:468-472`). Unit/program prefix forms are deliberately excluded (`utils/text_utils.py:456-461`).

#### 4 Constants

```python
_UNIT_CANONICAL_FORMS = [
    ("Department", "of"), ("Division", "of"), ("School", "of"),
    ("Faculty", "of"), ("College", "of"), ("Institute", "of"),
    ("Center", "for"), ("Centre", "for"), ("Laboratory", "of"),
]                                                                # utils/text_utils.py:334-344
_TRUNCATED_SUBJECTS = {
    "biomed", "anesth", "ortho", "rehab", "neuro", "cardio", "derm",
    "psych", "ophth", "peds", "gastro", "endo", "pulm", "rad"}    # 14 items — utils/text_utils.py:534-537
```
`_ABBREV_MAP`: 20 pattern→replacement entries, first `r"\b(?:Universtiy|Univeristy|Univesity|Universty|University|Univercity)\b" → "University"`, last `r"\bDiv\.?(?=\s|$)" → "Division"` (`utils/text_utils.py:170-196`); compiled case-insensitively at `utils/text_utils.py:198-201`.

#### 5 Complexity

All are constant regex batteries: O(*n*) per call; `canonicalise_unit_name` runs ≤ 2×9 anchored matches.

#### 6 Worked example

Canonicalisation, via UC 12 in `tests/test_canonical_dedup.py:46-55`: "Main Receiving Dept" → expand → "Main Receiving Department" → suffix pass with unit "Department" → subject "Main Receiving" (multi-word, so the truncation guard does not apply) → "Department of Main Receiving". Expansion rules in `tests/test_passthrough_name_cleanup.py:70-81`: "Capital Regional Med Ctr" → "Capital Regional Medical Center" ("Ctr"→"Center" then "Med" before a centre word → "Medical"); "School of Med" → "School of Medicine"; "Universtiy of Florida" → "University of Florida". Unit-construction and granularity are exercised through the street routers: `_street_is_department("Smith Lab") is True` (granular suffix form) and `_street_is_department("Department Dr") is False` (`tests/test_org_in_street.py:67-75`); "Scripps Research Institute"/"Harvard Medical School"/"Moffitt Cancer Center" read as unit/granular constructions yet are ignored by `_name_block_has_department` because they sit in Name 1 (`tests/test_org_in_street.py:123-134`, `enrichment/preprocess.py:2191-2203`).

#### 7 Failure modes

- `canonicalise_unit_name`'s suffix rewrite reorders any "X `<Unit>`" string, so a proper name ending in a unit word is rewritten ("Medicine School" → "School of Medicine" is intended; an org actually named "… Center" is reformatted too, `utils/text_utils.py:577-595`).
- The truncation guard only protects single-token subjects in the fixed 14-item set (`utils/text_utils.py:593-594`).
- `is_granular_unit`'s suffix regex `\b\S+\s+{word}\b\.?$` requires at least one preceding token, so a bare "Laboratory" is not granular (⚠ UNVERIFIED — no fixture for the bare-word case).

---

### Institution and logistics heuristics (`looks_like_research_institution`, `is_logistics_location` — utils/text_utils.py)

#### 1 Purpose

`looks_like_research_institution` is the keyword heuristic behind `_street_is_org_name`, `_segment_is_org` and `_looks_like_institution` (`enrichment/preprocess.py:1841`, `2051`, `2020`); its original role is routing ROR-miss cases away from the company-canonical LLM (`utils/text_utils.py:366-377`). `is_logistics_location` excludes distribution/fulfilment/logistics facilities from the name block so "Southeast Distribution Ctr" is treated as an unloading point, not a department (`utils/text_utils.py:657-670`; used at `enrichment/preprocess.py:1831`, `1877`).

#### 2 Inputs and outputs

Both take `str | None` and return `bool` (`utils/text_utils.py:366-377`, `668-670`).

#### 3 Pseudocode

1. `looks_like_research_institution`: blank → False; else True iff `_RESEARCH_NAME_SIGNALS_RE` matches anywhere.
2. `is_logistics_location`: True iff the value is non-empty and `_LOGISTICS_LOCATION_RE` matches.

#### 4 Constants

```python
_RESEARCH_NAME_SIGNALS_RE = re.compile(
    r"\b(?:University|College|Institute|Hospital|Clinic|Research|"
    r"Medical\s+School|School\s+of|Faculty\s+of|College\s+of|"
    r"Laboratory|Observatory|Academy|"
    r"Health\s+System|Health\s+Center|Regional\s+Health|"
    r"Medical\s+Center|Cancer\s+Center|"
    r"Schule|Universit[aä]t|Université|Universidade)\b", re.IGNORECASE)   # utils/text_utils.py:355-363
_LOGISTICS_LOCATION_RE = re.compile(
    r"\b(?:Distribution|Fulfil?lment|Logistics)\s+(?:Center|Centre|Ctr|Warehouse)\b", re.IGNORECASE)  # utils/text_utils.py:662-665
```

#### 5 Complexity

Single regex search: O(*n*).

#### 6 Worked example

From `tests/test_person_org_in_street.py:69-79`: `_street_is_org_name("ASTER HOUSE, 2A UNIVERSITY ROAD, BELFAST BT7 1NH") is False` — "UNIVERSITY ROAD" is masked by `_INSTITUTION_STREET_RE` before the research-signal test, and the UK postcode blocks the org path; whereas `_street_is_org_name("University of Miami Hospital") is True` (no street suffix follows "University", so masking does not remove it). Logistics from `tests/test_org_in_street.py:104-120`: "SOUTHEAST DISTRIBUTION CTR", "Memphis Distribution Center", "Acme Fulfillment Ctr" all stay in the street; "100 Warehouse Rd" is a street (leading digit), not a logistics facility.

#### 7 Failure modes

- Keyword-based: any company whose name contains "Research" or "Laboratory" reads as a research institution (`utils/text_utils.py:355-363`); preprocessing tolerates this because the affected routers also accept legal-suffix orgs.
- `_LOGISTICS_LOCATION_RE` requires the two-word collocation; "Distribution" alone or "Warehouse" alone does not match (`utils/text_utils.py:662-665`).

---

### Non-determinism notes

Preprocessing is fully deterministic. Verification:

- `enrichment/preprocess.py` imports only `logging`, `re`, `dataclasses`, `rapidfuzz.fuzz`, and six pure functions from `utils.text_utils` (`enrichment/preprocess.py:26-41`); `utils/text_utils.py` imports only `re`, `urllib.parse.urlparse`, and `rapidfuzz.fuzz` (`utils/text_utils.py:1-8`). Neither module imports an HTTP client, an LLM SDK, `random`, or time-dependent APIs.
- The module docstring asserts the contract: "Runs BEFORE any network/LLM call and is entirely pattern-based. … No SerpAPI, no ROR, no LLM on the hot path" (`enrichment/preprocess.py:1-9`).
- The only LLM-shaped code paths inside the module are (a) the synchronous stub `_llm_classify_person_or_org`, which unconditionally raises `NotImplementedError` (`enrichment/preprocess.py:1152-1163`); (b) `_extract_contact_from_field`'s Pattern B2, which is gated on `allow_llm and llm_client is not None` and is called from `preprocess_record` with neither (`enrichment/preprocess.py:1135-1140`, `1592`); and (c) `llm_classify_plain_names_async` (`enrichment/preprocess.py:2308-2332`), an async helper that `preprocess_record` never calls — the orchestrator runs it beforehand and passes the resulting verdict dict in as plain data (`enrichment/preprocess.py:1185-1191`).
- Consequently, for a fixed input tuple (including a fixed `llm_person_verdicts` dict), `preprocess_record` is a pure function of its arguments: all decisions are compiled-regex matches, set lookups, and the deterministic `rapidfuzz.fuzz.ratio` similarity (`enrichment/preprocess.py:1776`). The only non-deterministic element in the wider UC 7/UC 15 behaviour is the content of the verdict map itself, which is produced OUTSIDE this module by an LLM call (high-confidence person verdicts only, `enrichment/preprocess.py:2311-2331`) — variability there changes inputs, not the procedure.


# Part B — Orchestration, tier escalation, and confidence assignment

All paths are relative to `enrichment_api/`. Line numbers refer to the working tree on branch `diag/website-trace` (HEAD 515cc7c). Test outcomes cited below were obtained by executing the repository test suite on 2026-08-17 with `.venv/Scripts/python.exe -m pytest`; three orchestrator tests fail on this revision (documented where relevant).

---

### Batch orchestration and concurrency (`enrich_batch`, `_process_with_semaphore` — enrichment/orchestrator.py)

#### 1 Purpose
Processes a list of records concurrently under a semaphore bound, converts per-record exceptions into failed result rows, and aggregates batch summary statistics.

#### 2 Inputs and outputs
- Inputs: `records: list[EnrichmentRecord]`, `options: EnrichmentOptions` (enrichment/orchestrator.py:783-787). `EnrichmentOptions.max_concurrency: int = Field(default=5, ge=1, le=20)` (api/models.py:289).
- Output: `EnrichmentResponse(results=list[EnrichmentResult], summary=EnrichmentSummary)` (enrichment/orchestrator.py:843).

#### 3 Pseudocode
Source: enrichment/orchestrator.py:783-856.
1. Record batch start time; install the httpx `aclose()` log-noise filter (792).
2. Clear the module-level ROR cache and LEI cache ("fresh cache per batch to avoid stale failures", 793-794); reset per-batch LEI telemetry counters (795).
3. Create `cache = BatchCache(shared_serp=self._serp_cache)` — the SERP sub-cache falls through to a process-level store shared across batches (796; utils/cache.py:48-105).
4. Create `semaphore = asyncio.Semaphore(options.max_concurrency)` (797).
5. Define `_process_with_semaphore(record)`: acquire the semaphore, then `await self._enrich_single(record, options, cache)` (799-801). All records share one semaphore; at most `max_concurrency` records run `_enrich_single` simultaneously.
6. `asyncio.gather(*[...], return_exceptions=True)` over all records (804-807).
7. For each result: if it is an `Exception` instance, log it, build a fresh `_init_result(records[i])` (all original columns carried through), set `enrichment_status="failed"` and `error=str(res)`, and wrap it as `EnrichmentResult` (810-821). Note: this failure row bypasses `finalise()` entirely — `duration_ms` stays 0 and no address stage or passthrough logic runs (compare _init_result defaults at 263-370).
8. Build the summary from the final result list (`_build_summary`, 825-826, 2611-2650), then fold in the five LEI telemetry counters and set `tier1_lei_count = hits_exact + hits_fuzzy` (829-836).
9. Return `EnrichmentResponse(results, summary)` (843).
10. `finally`: call `aclose()` on the LLM client if present; an exception there is logged and swallowed (844-856).

Interaction with `_enrich_single`'s own error handling: `_enrich_single` wraps its whole body in `try/except Exception` (1708, 2599-2609) which sets `enrichment_status="failed"`, records the error, and still runs `_finalise_and_return`. The gather-level `Exception` branch (811-821) therefore only fires when an exception escapes that handler — i.e. an exception raised inside the `except` block itself (`_finalise_and_return`: website resolution, department probe, address stage, `finalise()`, or pydantic validation of `EnrichmentResult`).

#### 4 Constants
- `max_concurrency: int = Field(default=5, ge=1, le=20)` — api/models.py:289.
- `_new_lei_counts()` returns `{"attempts": 0, "hits_exact": 0, "hits_fuzzy": 0, "misses": 0, "errors": 0}` — enrichment/orchestrator.py:762-768.

#### 5 Complexity
One `_enrich_single` invocation per record; at most `max_concurrency` (default 5, hard cap 20) run concurrently. One `BatchCache` per batch; ROR/LEI module caches cleared once per batch (793-794); the SERP cache persists for the process lifetime (756-760; utils/cache.py:26-45). Summary aggregation is one pass over the results (2618-2648).

#### 6 Worked example
tests/test_orchestrator.py:225-246 (`test_batch_processing`): three records (`BATCH_001` MIT/"Department of Physics", `BATCH_002` "Pfizer Inc"/"R&D", `BATCH_003` UCLA/contact "Dr. John Doe") with `EnrichmentOptions(max_concurrency=3)` (241) — semaphore bound 3, all three run concurrently. Assertions: 3 results returned, `summary.total == 3` (244-245). Test passes on this revision.
The fixture tests/fixtures/mixed_batch_10_records.json (10 records, `"options": {"max_concurrency": 3}`, rows 2-104) exercises the same shape, but ⚠ UNVERIFIED — no test file loads this fixture (conftest.py:82-97 defines the loader; the only fixture consumed via conftest is `expected_outcomes`, conftest.py:89-103, and no test references it either).
⚠ NO FIXTURE COVERAGE for the gather-level exception branch (orchestrator.py:811-821): no test injects a client that raises through `_finalise_and_return`. Exercising it would need a mock (e.g. page fetcher or LLM used by the address stage) that raises inside the finalisation path after `_enrich_single`'s own except handler has already run.

#### 7 Failure modes
- A record whose enrichment raises inside `_enrich_single`'s body is still finalised (address cleanup, passthrough, duration) with `enrichment_status="failed"` and the error string (2599-2609).
- A record whose finalisation raises loses all address-stage and passthrough outputs: the gather branch rebuilds the row from `_init_result` originals only (811-821).
- The batch never aborts on a single record: `return_exceptions=True` (806) isolates failures per record.

---

### Record-type classification (assignment sites of `record_type` — enrichment/orchestrator.py)

#### 1 Purpose
Derives whether a record is a `research_institution`, `company`, or `unknown`, primarily from ROR organisation types, with a keyword heuristic and registry fallbacks on ROR miss.

#### 2 Inputs and outputs
- Inputs: the (preprocessed) Name 1 string, the record's country/city/state (passed to the ROR client, enrichment/orchestrator.py:1955-1961), and registry responses.
- Output: `result["record_type"] ∈ {"research_institution", "company", "unknown"}` (initialised `"unknown"`, enrichment/orchestrator.py:352).

#### 3 Pseudocode
Every assignment site of `record_type` in orchestrator.py, in pipeline order (verified exhaustive by pattern search over the file; the eight sites below are the only writes):

Source: enrichment/orchestrator.py:352, 1740.
1. `_init_result` sets `record_type = "unknown"` (352).
2. UC 0 overflow positive → `record_type = "unknown"` and immediate return (1740, within 1730-1751).

Source: enrichment/orchestrator.py:2036-2040; enrichment/tier1_ror.py:29-33, 504-505, 526.
3. ROR match: `record_type = "research_institution" if ror_parent.get("is_research_institution") else "company"` (2036-2040). `is_research_institution` is computed inside the ROR client as `org_types = [t.lower() for t in org.get("types", [])]`; `is_research = any(t in ROR_RESEARCH_TYPES for t in org_types)` (tier1_ror.py:504-505), i.e. classification "derived from ROR org types, not keyword matching" (tier1_ror.py:29).

Source: enrichment/orchestrator.py:2114, 2123-2128; utils/text_utils.py:355-377.
4. ROR miss, keyword fallback: `looks_like_research_institution(name1_cleaned)` (2114) — a regex membership test against `_RESEARCH_NAME_SIGNALS_RE` (text_utils.py:366-377) — if true, `record_type = "research_institution"` with `source="passthrough"`, `confidence="low"`, flag for review (2123-2134). No standalone `classify` function exists in the repo (pattern `def classify` has no matches); this regex heuristic is the only keyword fallback.

Source: enrichment/orchestrator.py:1687, 2057-2060, 2152-2154, 2198-2201.
5. LEI path: `_run_lei_lookup` on a verified GLEIF match sets `record_type = "company"` (1687). Invoked (a) after a ROR match already classified the record as company (2057-2060 — assignment 5 then re-writes "company" over "company"), (b) on ROR miss for non-research-looking names (2152-2154), (c) on the typo re-verify of an LLM-proposed spelling variant (2198-2201).

Source: enrichment/orchestrator.py:2217-2221, 2237-2243.
6. Company-canonical LLM success → `record_type = "company"` (2221).
7. Company-canonical attempted but failed → `record_type = "unknown"` (2243).

Source: enrichment/orchestrator.py:1471-1475.
8. Person-only routing, ROR-confirmed affiliation: `record_type = "research_institution" if confirmed.get("is_research_institution") else "company"` (1471-1475).

No later stage overrides `record_type`: `finalise()` only reads it (579-593), and Tier 2A/Tier 3 application helpers do not touch it (667-722).

#### 4 Constants
- `ROR_RESEARCH_TYPES = {"education", "healthcare", "government", "facility", "nonprofit", "archive", "other"}` — enrichment/tier1_ror.py:30-33.
- `_RESEARCH_NAME_SIGNALS_RE = re.compile(r"\b(?:University|College|Institute|Hospital|Clinic|Research|Medical\s+School|School\s+of|Faculty\s+of|College\s+of|Laboratory|Observatory|Academy|Health\s+System|Health\s+Center|Regional\s+Health|Medical\s+Center|Cancer\s+Center|Schule|Universit[aä]t|Université|Universidade)\b", re.IGNORECASE)` — utils/text_utils.py:355-363.

#### 5 Complexity
At most one ROR API call per record for classification (1955-1961; module-cached per batch, tier1_ror.py:35-41), at most two GLEIF calls (raw name + spelling-variant re-verify, 2152 and 2198), one regex evaluation for the keyword fallback (2114).

#### 6 Worked example
tests/test_orchestrator.py:248-260 (`test_classification_from_ror`, passes): record `CLS_001` "Harvard University" → mock ROR returns `org_types: ["education"]`, `is_research_institution: True` (the mock computes/curates the same shape as tier1_ror; e.g. the MIT entry, tests/mocks/ror_mock.py:21-40) → `"education" ∈ ROR_RESEARCH_TYPES` → `record_type == "research_institution"` (assert at 259). Record `CLS_002` "Novartis" → mock ROR entry classifies as company → `record_type == "company"` (260).
⚠ Stale test: tests/test_orchestrator.py:358-370 (`test_web_search_determines_record_type`) asserts `record_type == "company"` "from domain heuristics", but FAILS on this revision (`AssertionError: assert 'unknown' == 'company'`, observed 2026-08-17). No web-search-based `record_type` derivation exists in the current orchestrator; for "Comet Therapeutics" ROR misses, the keyword regex does not match, LEI misses, the company-canonical mock returns null, so site 7 leaves `"unknown"` (2237-2243). `test_web_search_fallback_for_name1` (314-329) fails identically.

#### 7 Failure modes
- A research institution absent from ROR whose name lacks all regex keywords is classified via the company path; if GLEIF and the LLM also miss, it ends `"unknown"` with `source="passthrough"`, `confidence="low"` (2237-2243).
- A company whose ROR entry carries a type in `ROR_RESEARCH_TYPES` (e.g. `nonprofit`) is classified `research_institution` (tier1_ror.py:504-505); no downstream correction exists.
- The UC 0 branch freezes `record_type` at `"unknown"` even for well-known institutions, by design ("flag only, never auto-correct", enrichment/overflow_check.py:5-6).

---

### Tier escalation (`_enrich_single` — enrichment/orchestrator.py)

#### 1 Purpose
Runs the full per-record decision sequence — UC 0 overflow check, deterministic preprocessing, person-only routing, Tier 1 (ROR / LEI), lab resolver, Tier 2 canonical, Tier 2A contact lookup, Tier 3 LLM inference — with early returns that stop lower tiers from overwriting higher-tier answers.

#### 2 Inputs and outputs
- Inputs: `record: EnrichmentRecord`, `options: EnrichmentOptions`, `cache: BatchCache` (enrichment/orchestrator.py:1698-1703).
- Output: `EnrichmentResult` (via `_finalise_and_return`, 1550-1573, on every path).

#### 3 Pseudocode

Source: enrichment/orchestrator.py:1705-1751 (UC 0).
1. `result = _init_result(record)`; start timer (1705-1706). Everything below runs inside `try:` (1708).
2. Normalise name1/name2 (whitespace-collapsed, lowercased; 1717-1718). If name1 non-blank AND name2 non-blank AND the two normal forms differ (1719-1723): call `run_overflow_check` (LLM; 1724-1729). If `overflow.is_overflow`: copy the stripped originals into `name1_enriched`/`name2_enriched`, set `record_type="unknown"`, `tier_used=1`, `source="pattern_match"`, `confidence=overflow.confidence`, `enrichment_status="unresolved"`, `flag_for_review=True`, `flag_reason="UC 0: possible Name 1 overflow into Name 2 — {reasoning}"`, `use_cases_triggered=[0]`, and RETURN via `_finalise_and_return` (1730-1751).

Source: enrichment/orchestrator.py:1753-1875 (preprocess).
3. `find_suspicious_plain_names` over name1-4; if any, `llm_classify_plain_names_async` (LLM person/organisation verdicts; 1760-1765). Then `preprocess_record(...)` with the verdicts (1767-1781).
4. Record `pre.use_cases` into `use_cases_triggered` (1790-1792). Track per-field preprocessing effects: cleared fields → `_preprocess_cleared`; changed or newly populated fields → written into `{field}_enriched` now (1798-1820). Record `_dba_values` (1826-1830), `_name1_was_person` (1834-1836), care_of/contact/email/street enriched values (1842-1851), `_pp_streets` (1858-1861), `_pp_building` (1866-1867). Downstream tiers use the PREPROCESSED names `pp_name1..pp_name4`, `pp_contact`, `pp_street1` (1870-1875).

Source: enrichment/orchestrator.py:1877-1924 (person branch and stashes).
5. Person-only branch: if `is_blank(pp_name1)` AND `pp_contact` non-blank AND `pre.name1_was_person` → RETURN `_resolve_person_affiliation(...)` (1890-1897). Tier 3 never runs for these records (1885-1889).
6. Stash `_pp_name1` (1901); `_has_dept_signal = bool(pp_name2 or pp_contact)` (1908-1911); `_multi_contact = has_multiple_contacts(pp_contact)` (1912). If any preprocess flag contains `"conflict"`, `"slots-full"`, or `"acronym-ambiguous"` → `flag_for_review=True` with those flags joined as reason (1916-1924).

Source: enrichment/orchestrator.py:1926-2103 (Tier 1 ROR — match arm).
7. If `pp_name1` non-blank: `country_code = country_to_iso_code(record.country)` (1932); `name1_cleaned = strip_address_fragments(pp_name1, ...)` (1939-1945); `ror_parent = await self._ror_client.call(name1_cleaned, country_code, country, city, state)` (1955-1961).
8. If `ror_parent["matched"]`:
   a. Identity guard on ROR's official name: build candidate set {original pp_name1, its abbreviation-expansion, name1_cleaned, its expansion} (2000-2006); if any candidate satisfies `canonical_preserves_identity(c, official)` adopt ROR's name (2007-2008), else keep the standardised input (`clean_passthrough_org_name`) (2009-2023).
   b. Carry `_ror_acronym` (2027-2029); set `ror_id`, `tier_used=1`, `source="ROR"`, `confidence="high"` (2031-2034); `record_type` from `is_research_institution` (2036-2040); `domain` (2041-2043); `website_url` from ROR links (Path A, 2046-2048).
   c. If `record_type == "company"` → `_run_lei_lookup(record, result, name1_cleaned, country_code)` (2057-2060) — on a GLEIF match this overwrites name1 with the legal name, sets `lei_id`, `source="gleif"` (1683-1691).
   d. `enrichment_status="enriched"`, UC 2 and 3 appended (2063-2067).
   e. EARLY RETURN if no `pp_name2` AND no `pp_contact` ("Tier 1 is the final answer", 2069-2078).
   f. Child match: for each of name2/name3/name4 present, expand abbreviations then `_match_child_locally(val, ror_parent["children"])` (rapidfuzz `token_sort_ratio`, threshold `_CHILD_MATCH_THRESHOLD = 70`; 633, 636-662); on a hit write `{field}_enriched = child name` (2081-2103). No return here — control falls through.

Source: enrichment/orchestrator.py:2105-2246 (Tier 1 ROR — miss arm).
9. ROR miss, `looks_like_research_institution(name1_cleaned)` true (2114): passthrough name1, `source="passthrough"`, `confidence="low"`, `tier_used=1`, `record_type="research_institution"`, `enrichment_status="unresolved"`, `flag_for_review=True`, reason "Research-institution name not found in ROR — left unchanged for manual review" (2123-2134). EARLY RETURN if no pp_name2 AND no pp_contact (2137-2142); otherwise `company_res = None` and fall through (2144).
10. ROR miss, not research-looking:
   a. `_run_lei_lookup` on the raw cleaned name (2152-2154). Matched → `company_res = None`; EARLY RETURN if no pp_name2 (2155-2162).
   b. LEI miss → `run_company_canonical` (LLM, with street/postal context; 2164-2178). If it did NOT succeed but returned a `proposed_name` that `canonical_is_spelling_variant(name1_cleaned, proposed)` (2191-2197): re-verify the proposal against GLEIF (`_run_lei_lookup`, 2198-2201); on confirmation `company_res = None`, EARLY RETURN if no pp_name2 (2202-2215). (`run_company_canonical` sets `success=False` and exposes `proposed_name` when its internal `canonical_preserves_identity` guard rejects the LLM's suggestion — enrichment/company_canonical.py:83-96.)
   c. Company canonical success → name1 = suggestion, `source="llm_canonical"`, `confidence="high"`, `record_type="company"`, `tier_used=2`, UC 2/3, `enrichment_status="enriched"`, `flag_for_review=True` reason "LLM canonical company name — verify" (2217-2229); EARLY RETURN if no pp_name2 (2231-2236).
   d. Company canonical attempted and failed → passthrough name1, `source="passthrough"`, `confidence="low"`, `tier_used=1`, `record_type="unknown"` (2237-2243).
11. If `pp_name1` was blank (and the person branch did not fire), the whole Tier 1 block is skipped and control arrives here with `record_type="unknown"`.

Source: enrichment/orchestrator.py:2248-2271 (AP short-circuit).
12. `name2_already_filled = bool(pp_name2)` (2248). If any of `pre.name1/name2/name3` equals "accounts payable" case-insensitively (2255-2258): copy pp_name2/pp_name3 into enriched, `tier_used=2`, `source="pattern_match"`, `confidence="high"`, `enrichment_status="enriched"`, `flag_for_review=True` reason "Accounts Payable record — verify", UC 6, RETURN (2259-2271).

Source: enrichment/orchestrator.py:2273-2355 (lab resolver, UC 13).
13. `ror_child_resolved` = name2_enriched set by child match, differing from pp_name2, and not granular (2284-2290). `can_lab_resolve = record_type == "research_institution" AND pp_name2 non-blank AND is_granular_unit(pp_name2) AND not ror_child_resolved` (2291-2296).
14. If entered: `run_lab_resolver` (SERP + page fetch + LLM; 2298-2308). Success with a parent department: name2 ← parent dept, name3 ← original lab name (only if name3 was empty), `tier_used=2`, `source="dept_search"`, `source_url`, confidence from the resolver, `enrichment_status="enriched"`, `flag_for_review=True` (reason differs by whether name3 was already populated), UC 13, RETURN (2317-2342). Failure: `flag_for_review=True` reason "Lab/group detected in Name 2 but parent department could not be determined", UC 13, FALL THROUGH (2349-2355).

Source: enrichment/orchestrator.py:2357-2441 (Tier 2 canonical, UC 5).
15. `can_canonical = record_type in ("research_institution", "company") AND name1_enriched` (2362-2365). For each of name2/name3/name4: skip if blank, skip if already enriched (child match), skip if not can_canonical; a DBA-marked field is copied verbatim and skipped (2380-2382); otherwise `run_tier2_canonical` (LLM; 2384-2389), setting `any_canonical_ran=True` (2397). Success: reject granular results (`is_granular_unit`) → passthrough original (2401-2407); else adopt and append UC 5 (2408-2411). LLM not confident → passthrough (2412-2414).
16. If `any_canonical_ran AND name2_already_filled`: `tier_used=2`; if any enriched field differs from the record original → `source="llm_canonical"`, `confidence="high"`, `enrichment_status="enriched"`, flag "LLM canonical form — verify"; else `source="passthrough"`, `confidence="low"`, `enrichment_status="unresolved"`, flag "name2/name3 could not be canonicalised with high confidence — left unchanged"; RETURN (2416-2441).

Source: enrichment/orchestrator.py:2443-2534 (Tier 2A).
17. `can_do_contact_lookup = not name2_already_filled AND record_type == "research_institution" AND pp_contact non-blank AND not multi_contact AND institution_domain` (2451-2457). `institution_domain` is only ever non-None when ROR matched (2041, reset to None at 2135 and 2246).
18. If entered: `run_tier2a` in "population" mode (2466-2480). On success: if the answer is granular, log and skip (falls to Tier 3; 2494-2503); else canonicalise it via `run_tier2_canonical` (adopting the canonical form only when non-granular; 2507-2528), `_apply_tier2a` (transfers tier_used=2, tier2_mode, contact_used=True, source, source_url, confidence, flag, status, name2_match; 667-691, called at 2529), UC 4, RETURN (2530-2534). Tier 2A failure or gate false → fall through.

Source: enrichment/orchestrator.py:2536-2597 (Tier 3) and 694-722 (`_apply_tier3`).
19. `run_tier3` (LLM inference over all available fields; 2543-2555). `_apply_tier3` unconditionally sets `tier_used=3`, `source="LLM"`, and copies confidence/flag/status from the Tier 3 result (694-701); on success it applies `name1_suggestion` only if `canonical_preserves_identity(name1_original, suggestion)` (704-717), and writes name2/name3 suggestions, marking `_name2_from_tier3` (718-722).
20. Post-tier rules: (a) name1 fallback to preprocessed original if still empty (2560-2561); (b) if `not has_dept_signal` → `name2_enriched = None` (2566-2567); (c) if the input name2 was non-blank but preprocessing cleared it → `name2_enriched = None` ("do not let Tier 3 fabricate a replacement", 2572-2581); (d) if name2_enriched equals name1_enriched case-insensitively → `name2_enriched = None` (2584-2595). RETURN (2597).
21. `except Exception`: log, `enrichment_status="failed"`, `error=str(exc)`, RETURN via `_finalise_and_return` (2599-2609).

Every return path goes through `_finalise_and_return` (1550-1573), which runs the website resolver Paths B/C (`_maybe_resolve_website_bc`, 1559; body 858-921), derives `domain` from `website_url` when missing (1566-1569), the department-domain probe (1570), the address stage (1571), and `finalise()` (1572).

#### 4 Constants
- `_CHILD_MATCH_THRESHOLD = 70  # rapidfuzz token_sort_ratio minimum` — enrichment/orchestrator.py:633.
- Preprocess flag substrings gating the review flag: `"conflict"`, `"slots-full"`, `"acronym-ambiguous"` — enrichment/orchestrator.py:1917, 1923.
- AP literal: `"accounts payable"` (lowercased comparison) — enrichment/orchestrator.py:2256.
- Granular-unit suffix word list `["laboratory", "laboratories", "lab", "facility", "facilities", "center", "centre", "core"] + ["group", "unit", "program", "programme"]` and the in-scope head pattern `r"^(?:department|division|school|college|faculty)\s+(?:of|for)\s+"` — utils/text_utils.py:435-461.
- `_MULTI_CONTACT_SEPARATOR_RE = re.compile(r"\s+(?:and|or)\s+|\s*&\s*|[;/]|\s\+\s", re.IGNORECASE)`; comma form requires ≥2 comma parts each with ≥2 tokens — enrichment/preprocess.py:1065-1090.
- `lei_lookup_enabled` defaults True (`LEI_LOOKUP_ENABLED` env) — config.py:183-185.
- Name-slot dedup fuzz threshold in `finalise()`: `fuzz.ratio(n, kn) >= 92` — enrichment/orchestrator.py:547.

#### 5 Complexity
Per record: ≤1 overflow LLM call (only when both names present and distinct); ≤1 plain-name-classifier LLM call per suspicious name; 1 ROR call (batch-cached by name+country, tier1_ror.py:35-36); ≤2 GLEIF calls (raw name, spelling-variant re-verify); ≤1 company-canonical LLM call; lab resolver ≤1 SERP query plus page fetches; ≤3 Tier-2-canonical LLM calls (name2/name3/name4) plus ≤1 more to canonicalise a Tier 2A answer; Tier 2A 1 SERP query (2443-2445); Tier 3 1 LLM call; child matching is local fuzzy scoring over the parent's children list (one pass per name field, 2082-2099). `_finalise_and_return` adds ≤1 website SERP query + ≤1 website LLM call (885-921) and the department probe's page fetches / ≤2 SERP queries (1109-1306).

#### 6 Worked example
tests/test_orchestrator.py:115-146 (`test_typod_company_name_recovered_via_gleif_reverify`, record `ORCH_BAYER`, passes on this revision). Input: name1 "Bayr AG", name2 None, street1 "Kaiser-Wilhelm-Allee 1", city Leverkusen, country DE (128-136). Step trace:
1. UC 0 skipped — name2 is None (gate 1719-1723 fails).
2. Person branch skipped — pp_name1 non-blank (1890-1894).
3. Tier 1 ROR: "bayr ag" is not in the mock ROR data (tests/mocks/ror_mock.py:20 ff.) → miss; `looks_like_research_institution("Bayr AG")` → no regex keyword → False (2114; text_utils.py:355-377).
4. `_run_lei_lookup("Bayr AG")`: mock LEI is keyed by substring `"bayer"`, so "bayr ag" misses (tests/mocks/lei_mock.py:40-43, 85-87) → returns False (`misses` counter, orchestrator.py:1674-1676).
5. `run_company_canonical`: the mock LLM's `_COMPANY_TYPO_CORRECTIONS = {"bayr ag": "Bayer AG"}` fires (tests/mocks/openai_mock.py:230-232, 251-258); inside `run_company_canonical` the identity guard rejects "Bayer AG" as not identity-preserving for "Bayr AG", so `success=False`, `proposed_name="Bayer AG"` (enrichment/company_canonical.py:83-96).
6. Re-verify gate: `canonical_is_spelling_variant("Bayr AG", "Bayer AG")` → True (2191-2197); `_run_lei_lookup("Bayer AG")` → mock exact match, `lei_id="3157002JBAOA57BQAT84"`, `legal_name="BAYER AG"` (lei_mock.py:43-51) → writes `name1_enriched="BAYER AG"`, `lei_id`, `record_type="company"`, `tier_used=1`, `source="gleif"`, `confidence="high"`, `enrichment_status="enriched"` (1683-1695).
7. No pp_name2 → EARLY RETURN (2212-2215). `finalise()` title-cases the non-passthrough name: "BAYER AG" → "Bayer AG" (417-422). Assertions: `name1_enriched == "Bayer AG"`, `lei_id == "3157002JBAOA57BQAT84"`, `tier_used == 1`, `source == "gleif"` (140-146).

⚠ Fall-through to Tier 3 after a full Tier 1 resolution: for a record whose name2 the ROR child match resolved and which therefore skips the Tier 2 canonical return (`any_canonical_ran` stays False because the field was `continue`d at 2371-2372) and the Tier 2A gate (`name2_already_filled`), Tier 3 RUNS and `_apply_tier3` overwrites `tier_used/source/confidence/flag/enrichment_status` (694-701). Evidence: tests/test_orchestrator.py:43-59 (`test_tier1_full_resolution`, MIT + "Department of Chemistry") FAILS on this revision with `AssertionError: assert 'medium' == 'high'` (observed 2026-08-17) — the final confidence is the mock Tier 3's "medium" (tests/mocks/openai_mock.py:304-316), not Tier 1's "high".

⚠ Tier 2B is never entered by the current orchestrator. `run_tier2b` exists (enrichment/tier2b_dept.py:63) and is unit-tested standalone (tests/test_tier2b.py:33-53), but orchestrator.py does not import it (imports at 24-85); `tier2_mode == "2B"` appears in orchestrator.py only in summary counting (2640). tests/fixtures/expected_outcomes.json rows 20-46 still expect `"expected_tier2_mode": "2B"` for `BSP_1000003`-`BSP_1000005` — stale relative to the current pipeline, and no test consumes that fixture (only the conftest loader references it, conftest.py:89-103).

The AP short-circuit has end-to-end coverage via tests/test_street_scope_routing.py:58-62 (street "Accounts Payable, 399 Revolution Dr" → preprocessing routes "Accounts Payable" into Name 1 → `any_ap` fires → `name1_enriched == "Accounts Payable"`; passes).

#### 7 Failure modes
- Tier 3 overwriting Tier 1/2 metadata on the fall-through path (see ⚠ above): a fully ROR-resolved institution with a name2 filled by child match reports `tier_used=3`, `source="LLM"` and the LLM's confidence/status.
- ROR returning a different-identity official name: the guard at 2007-2023 keeps the (standardised) input instead; ROR's id/domain/website are still adopted (2031-2048), so the id may not match the emitted name exactly (documented intent, 1984-1996).
- Tier 3 fabrications are bounded by the post-tier rules: no-signal records get `name2_enriched=None` (2566-2567), preprocess-cleared name2 stays empty (2572-2581), and a blank-input name2 filled by Tier 3 at non-high confidence is dropped and flagged in `finalise()` (392-409).
- GLEIF errors never fail a record: exceptions and `error` responses return False and fall to the LLM path (1651-1657, 1671-1673).

---

### Person-only routing (`_resolve_person_affiliation` — enrichment/orchestrator.py)

#### 1 Purpose
For a record whose Name 1 held only a person's name, proposes the person's institution from a grounded web lookup, accepts it only when ROR confirms it in the record's country, and always flags the record.

#### 2 Inputs and outputs
- Inputs: `result: dict`, `record: EnrichmentRecord`, `contact: str` (the person moved out of Name 1), `pp_name2: str | None`, `start: float`, `cache: BatchCache` (enrichment/orchestrator.py:1413-1420).
- Output: `EnrichmentResult` via `_finalise_and_return` (1548). `run_person_affiliation` returns `PersonAffiliation(institution: str | None, department: str | None, confidence: str = "low")` (enrichment/person_affiliation.py:44-49).

#### 3 Pseudocode
Source: enrichment/orchestrator.py:1890-1897 (entry) and 1413-1548 (body).
1. Entry condition (in `_enrich_single`): `is_blank(pp_name1)` AND `pp_contact` non-blank AND `pre.name1_was_person` (1890-1894). The call ALWAYS short-circuits the pipeline — Tier 3 never runs for these records (1429-1432).
2. `affil = await run_person_affiliation(contact, city, region, country, email, search_client, llm_client, settings)` (1433-1442) — one SERP query over the person plus one LLM extraction (person_affiliation.py:110-176).
3. ROR-confirm guard: only if `affil.institution` is set AND `affil.confidence in ("high", "medium")` (1445), call `self._ror_client.call(affil.institution, country_code=country_to_iso_code(record.country), ...)` (1446-1454); any exception → `confirmed = None` (1455-1459).
4. Confirmed branch (`confirmed.get("matched")`, 1461): `name1_enriched` = ROR's official name (1462-1463); `ror_id`; `tier_used=1`; `source="ROR"`; `confidence="medium"` — "capped at medium because the person→org link came from the web, not the registry" (1465-1470); `record_type` from `is_research_institution` (1471-1475); ROR domain/website/acronym (1476-1482).
5. Department: start from `affil.department`; if `pp_name2` is blank and a domain is known, attempt Tier 2A on the confirmed domain (`run_tier2a` with `name2=None`; 1487-1502), preferring its `name2_enriched` (1503-1504; exceptions logged and swallowed 1505-1509). Write the department into `name2_enriched` only when `pp_name2` is blank (1510-1511).
6. Always flag: `flag_for_review=True`, `flag_reason = "Name 1 inferred from contact's web affiliation — verify ({official})"`; `_pp_name1 = official` so the website resolver can run (1513-1518).
7. Fallback branch (no confirmation): `flag_for_review=True`; reason is `"person in Name 1 — web affiliation '{institution}' not confirmed by registry in {country}; manual lookup needed"` when the web proposed an institution, else `"person in Name 1 — affiliation could not be resolved; manual lookup needed"` (1526-1538). `_pp_name1 = None` (1539) — this makes `_maybe_resolve_website_bc` return without any lookup (guard at 878-880), so no website is guessed. Name 1 stays empty.
8. RETURN `_finalise_and_return(result, start, record, cache)` (1548).

#### 4 Constants
- Confidence acceptance set for the web proposal: `("high", "medium")` — enrichment/orchestrator.py:1445.
- Confidence cap on confirmation: `result["confidence"] = "medium"` — enrichment/orchestrator.py:1470.
- `PersonAffiliation.confidence` default `"low"`, coerced into `{"high", "medium", "low"}` — enrichment/person_affiliation.py:49, 157-160.

#### 5 Complexity
Per person-only record: 1 SERP query + 1 LLM extraction (`run_person_affiliation`), ≤1 ROR call, ≤1 Tier 2A lookup (1 SERP query + page fetches + 1 LLM call) when name2 is blank and a domain was confirmed.

#### 6 Worked example
tests/test_person_affiliation_guard.py:96-123 (`test_confirmed_sets_ror_name_and_domain`, passes). Input `A1`: name1 "Dr. Jane Smith", name2 None, Cambridge MA US (110-113). The routing fake LLM proposes `{"institution": "Massachusetts Institute of Technology", "department": "Department of Chemistry", "confidence": "high"}` (105-109); the country-aware mock ROR confirms MIT for `country_code="US"` with `ror_id="https://ror.org/042nb2s44"`, `domain="mit.edu"` (34-41, 52-58). Result: `contact_enriched == "Dr. Jane Smith"`, `name1_enriched == "Massachusetts Institute of Technology"`, `domain == "mit.edu"`, `name2_enriched == "Department of Chemistry"`, `flag_for_review is True`, reason contains "verify" (117-123).
Wrong-country rejection: tests/test_person_affiliation_guard.py:126-151 — person in Belfast GB, web proposes University of Galway (IE); mock ROR's country filter rejects (`cc != country_code`, 56-57) → `name1_enriched is None`, `name2_enriched is None`, flag reason contains "not confirmed" (147-151).
Nothing found: tests/test_person_affiliation_guard.py:154-168 and tests/test_person_in_name1_flag.py:53-66 — `name1_enriched is None`, flagged, reason contains "manual lookup".

#### 7 Failure modes
- Web lookup proposes a plausible but wrong-country institution: rejected by the ROR country filter; the record ships with an empty Name 1 and a manual-lookup flag rather than the wrong entity (1526-1538; test at 126-151).
- Web lookup returns low confidence: the ROR confirm is never attempted (1445) — even a correct proposal is discarded to the flag path.
- Tier 2A failure during department lookup is swallowed; the web-proposed department (or nothing) is used (1505-1509).
- Every outcome is flagged, so no person-only record leaves the pipeline unreviewed (1513, 1527).

---

### Flag-for-review / enrichment-status assignment (`determine_enrichment_status`, `should_flag_for_review` — enrichment/confidence.py)

#### 1 Purpose
Defines rule tables mapping (confidence, name2 match result, tier, source, tier2 mode) to an `enrichment_status` and a `(flag_for_review, flag_reason)` pair.

#### 2 Inputs and outputs
- `determine_enrichment_status(confidence: Literal["high","medium","low","none"], name2_match_result: str, tier_used: int, source: str) -> Literal["enriched","verified","unresolved","failed"]` (enrichment/confidence.py:8-13).
- `should_flag_for_review(confidence, tier_used, tier2_mode: str | None, name2_match_result, source) -> tuple[bool, str | None]` (enrichment/confidence.py:40-46).

#### 3 Pseudocode
Source: enrichment/confidence.py:8-37 (`determine_enrichment_status`).
1. `confidence == "none"` → `"failed"` (22-23).
2. `name2_match_result == "exact"` AND `source in ("contact_lookup_found", "contact_lookup_corrected")` → `"verified"` (26-29).
3. `confidence in ("high", "medium")`: if `tier_used == 3` → `"unresolved"` ("Tier 3 always requires review"), else `"enriched"` (31-34).
4. Otherwise (low confidence) → `"unresolved"` (36-37).

Source: enrichment/confidence.py:40-86 (`should_flag_for_review`).
1. `tier_used == 3`: low confidence → `(True, "LLM low confidence — manual review required")`; else `(True, "LLM inference — requires verification")` (51-55).
2. `tier_used == 1` AND `confidence == "high"` → `(False, None)` (57-59).
3. `tier2_mode in ("2A_population", "2A_verification")` AND match `"exact"` → `(False, None)` (61-63).
4. Same modes with match `"partial"` → `(True, "Partial match — confirm enriched Name 2")` (65-67).
5. `source == "contact_lookup_corrected"` → `(True, "Name 2 corrected — did not match contact page affiliation")` (69-71).
6. `tier2_mode == "2B"` AND `confidence == "low"` → `(True, "Non-official source")` (73-75).
7. `tier2_mode == "2B"` → `(True, "Department search — verify against official records")` (77-79).
8. `confidence == "medium"` → `(True, "Medium confidence — recommend review")` (81-83).
9. Default → `(False, None)` (85-86).

⚠ UNVERIFIED as pipeline behaviour — dead code: a full-repo pattern search for `should_flag_for_review|determine_enrichment_status` matches only the two definition sites (enrichment/confidence.py:8, 40); neither function is imported or called anywhere. The `flag_for_review`/`enrichment_status` values that actually reach the output are set inline at these sites:
- orchestrator.py: UC 0 (1744-1749), preprocess conflict flags (1916-1924), `_run_lei_lookup` (1691), Tier 1 ROR match (2063), research passthrough (2129-2134), company canonical (2227-2229), AP short-circuit (2266-2268), lab resolver (2327-2339, 2349-2353), Tier 2 canonical return (2425-2438), `_apply_tier2a` copying the Tier 2A result's fields (675-677), `_apply_tier3` copying the Tier 3 result's fields (699-701), person routing (1513-1517, 1526-1538), `finalise()` (392-409 Tier-3 name2 guess drop; 579-593 research-institution no-signal/multi-contact flags), `_flag_website_review` (619-628), exception path status "failed" (2605).
- Tier modules producing the copied fields: enrichment/tier2a_contact.py:405-414 (population: high → no flag + "enriched"; medium → flag + "enriched"), 439-477 (verification: exact → "verified"/no flag; partial → "enriched"/flag; no_match → "enriched"/flag); enrichment/tier3_llm.py:104-105 (extraction failure → confidence "none"/status "failed"), 109-160 (high/medium → status "unresolved", flag True; low → originals untouched, "LLM low confidence — manual review required").

#### 4 Constants
All literal strings and threshold sets quoted in §3 are the constants; definition sites as cited (confidence.py:22-86).

#### 5 Complexity
Pure rule tables; constant-time per call. Never invoked in the pipeline (see §3 ⚠).

#### 6 Worked example
⚠ NO FIXTURE COVERAGE — no test imports or exercises either function (repo-wide search, 2026-08-17). Exercising them would require a caller passing a (confidence, match, tier, source) tuple; none exists. The inline logic they mirror is covered indirectly, e.g. tests/fixtures/expected_outcomes.json rows 2-10 encode "2A_population + exact → flag False, status enriched", matching tier2a_contact.py:407-410 — but that fixture is itself unconsumed by any test (conftest.py:89-103 only).

#### 7 Failure modes
Because the module is dead code, any rule drift between confidence.py and the inline assignments is silent. Observable divergence: confidence.py:33 states Tier 3 with high/medium confidence yields status `"unresolved"`, and tier3_llm.py:122 implements the same — but confidence.py:74-79's Tier 2B reasons can never occur (no 2B invocation), and the `"Medium confidence — recommend review"` rule (81-83) has no inline counterpart on the Tier 1 gleif path, where medium-confidence GLEIF results are not flagged (orchestrator.py:1683-1695 sets no flag).

---

### Overflow check invocation (UC 0) (`run_overflow_check` — enrichment/overflow_check.py)

#### 1 Purpose
Detects, with a single LLM call and no SERP, whether Name 1 + Name 2 read as one organisation name split across two fields, and if so flags the record and stops all further enrichment ("flag only, never auto-correct").

#### 2 Inputs and outputs
- Inputs: `record_id: str`, `name1: str`, `name2: str`, `llm_client: OpenAIClient` (enrichment/overflow_check.py:33-38).
- Output: `OverflowCheckResult(is_overflow: bool = False, confidence: str = "none", reasoning: str = "")` (26-30).

#### 3 Pseudocode
Source: enrichment/orchestrator.py:1709-1751 (invocation) and enrichment/overflow_check.py:33-76 (body).
1. Orchestrator entry conditions (the very first step of `_enrich_single`): name1 non-blank AND name2 non-blank AND their whitespace-collapsed lowercase forms differ — equal strings are duplicates handled by UC 12 dedup, not an overflow (orchestrator.py:1714-1723).
2. `run_overflow_check` guard: if either name is blank, return the default (non-overflow) result (overflow_check.py:41-42).
3. Format `OVERFLOW_CHECK_USER_PROMPT_TEMPLATE` with both names and call `llm_client.extract_json(OVERFLOW_CHECK_SYSTEM_PROMPT, user_prompt)` (44-52); any exception → log and return the default result (53-55) — an LLM failure can never flag or fail a record.
4. Parse `is_overflow` (bool), `confidence` (default "low"), `reasoning` (57-59). Flag only when `is_overflow` AND `confidence in ("high", "medium")` — "low confidence is too noisy given the spec explicitly accepts false negatives" (61-66). Otherwise return non-overflow (71-76).
5. On a positive result the orchestrator: copies the stripped originals into `name1_enriched`/`name2_enriched` ("Pass through originals untouched. Flag only."), sets `record_type="unknown"`, `tier_used=1`, `source="pattern_match"`, `confidence=overflow.confidence`, `enrichment_status="unresolved"`, `flag_for_review=True`, `flag_reason="UC 0: possible Name 1 overflow into Name 2 — {reasoning}"`, `use_cases_triggered=[0]`, and returns immediately via `_finalise_and_return` — no other tier runs (orchestrator.py:1730-1751).

#### 4 Constants
- Confidence acceptance set: `("high", "medium")` — enrichment/overflow_check.py:63.
- Defaults: `is_overflow=False`, `confidence="none"`, `reasoning=""` — enrichment/overflow_check.py:26-30; parse default `"low"` — line 58.
- `OVERFLOW_CHECK_SYSTEM_PROMPT = "You detect whether two adjacent customer-master name fields read as one continuous organisation name split across the fields, or as two separate entities. Return valid JSON only."` — llm/prompts.py:10-14.

#### 5 Complexity
Exactly one LLM call per record that satisfies the entry conditions; zero SERP calls (overflow_check.py:3); zero calls for records with a blank name1 or name2 or identical names.

#### 6 Worked example
⚠ NO FIXTURE COVERAGE — no test exercises `run_overflow_check` or the UC 0 branch (repo-wide search for "overflow" in tests/ matches only unrelated name-slot-overflow tests: tests/test_issue_detection.py:104, 264 and tests/test_street_org_split.py:61-62). Records that pass through the UC 0 gate in existing orchestrator tests (e.g. `ORCH_001`, tests/test_orchestrator.py:46-53) hit the mock LLM's unrecognised-prompt fallback `{"confidence": "low", ...}` (tests/mocks/openai_mock.py:124-125), which lacks an `is_overflow` key → never flagged. A fixture exercising the positive branch would need a record with both names populated and distinct, plus a mock LLM returning `{"is_overflow": true, "confidence": "high"|"medium", "reasoning": ...}`.

#### 7 Failure modes
- False negative (accepted by spec, overflow_check.py:62-64): a genuine overflow judged at low confidence is not flagged and proceeds through normal tiers.
- False positive: a medium/high-confidence misjudgement freezes an enrichable record at `"unresolved"` with originals passed through; nothing is auto-corrected (orchestrator.py:1737-1751).
- LLM outage: silently treated as "no overflow" (53-55); the record continues through the pipeline.

---

### Non-determinism notes

External-model (LLM) calls within the documented procedures, in pipeline order:
1. UC 0 overflow check — 1 LLM call (enrichment/overflow_check.py:49-52), gated at orchestrator.py:1719-1723. No cache.
2. Plain-name person/organisation classifier — LLM, one call per suspicious name (orchestrator.py:1760-1765). No cache.
3. Company canonical (`run_company_canonical`) — LLM (orchestrator.py:2164-2178). No cache.
4. Lab resolver (`run_lab_resolver`) — SERP + page fetches + LLM (orchestrator.py:2298-2308).
5. Tier 2 canonical (`run_tier2_canonical`) — LLM, up to three calls per record plus one for the Tier 2A answer (orchestrator.py:2384-2389, 2507-2513).
6. Tier 2A (`run_tier2a`) — 1 SERP query + page fetches + LLM (orchestrator.py:2466-2480; also from person routing at 1490-1502).
7. Tier 3 (`run_tier3`) — LLM (orchestrator.py:2543-2555).
8. Person affiliation (`run_person_affiliation`) — SERP + LLM (orchestrator.py:1433-1442).
9. Website resolver Path B — SERP (`resolve_website_via_serp`, orchestrator.py:885-895); Path C — LLM (`infer_website_via_llm`, 907-915). Runs on every return path via `_finalise_and_return` (1559).
10. Department-domain probe — page fetches plus ≤1 site-restricted SERP query (orchestrator.py:1176-1192), an optional second unrestricted SERP query only when `dept_probe_cross_domain` is enabled (default False, config.py:166-168; gate at orchestrator.py:1277-1283).
11. Address stage — LLM (`process_address`, orchestrator.py:1597-1615), on every return path.
12. Overflow/Tier-3/website LLM calls use `extract_json` with `temperature: float = 0.0` in the mock signature (tests/mocks/openai_mock.py:103); ⚠ UNVERIFIED — the production `OpenAIClient.extract_json` temperature setting was not inspected in this pass (llm/openai_client.py not read).

Deterministic-registry calls: ROR (orchestrator.py:1955-1961, 1448-1454) and GLEIF (1650) are external HTTP APIs — deterministic given a fixed remote state, but results drift with registry updates.

Caching:
- ROR: module-level dict keyed by `(name_lower, country_code)` (enrichment/tier1_ror.py:35-36), cleared at the start of every batch (orchestrator.py:793). LEI: analogous module cache cleared per batch (794; tier1_lei import at 55).
- SERP: two scopes — a per-batch dict inside `BatchCache` and a process-level `SerpCache` shared across all batches of one orchestrator instance; per-batch misses fall through to the shared store and writes propagate to it; keys are lowercased stripped query strings; in-memory only, no file persistence (utils/cache.py:22-105; orchestrator.py:756-760, 796). The department probe reads/writes this cache (orchestrator.py:1178-1192, 1291-1306).
- Resolved-host cache: per-batch memo of the department probe's redirect-resolved institution host, one resolution per institution (utils/cache.py:60-71; orchestrator.py:935-955).
- Consequence: within one process, a repeated SERP query returns the first batch's result even in later batches (utils/cache.py:26-31); LLM calls are never cached, so any procedure step involving an LLM (items 1-9, 11) can differ between runs on identical input.


# Part C — Tier 1 registry lookup and match acceptance (ROR, GLEIF/LEI)

All paths are relative to `enrichment_api/`. Line numbers refer to the repository state on branch `diag/website-trace` (commit 515cc7c).

---

### ROR registry lookup (`call_ror` — enrichment/tier1_ror.py)

#### 1 Purpose
Resolves an organisation name (plus optional city/state/country context) to a ROR v2 registry entry using an affiliation-endpoint-first, query-endpoint-fallback strategy, with local re-scoring and a country guard applied to every candidate (enrichment/tier1_ror.py:536-846).

#### 2 Inputs and outputs
Inputs: `name: str`; `country_code: str | None` (ISO alpha-2 for the query-endpoint filter); `country: str | None`, `city: str | None`, `state: str | None` (raw record text used in the affiliation string); `base_url: str | None` (enrichment/tier1_ror.py:536-543). The orchestrator supplies these via `RORClient.call` (enrichment/tier1_ror.py:856-872), where `base_url = settings.ror_api_base` (enrichment/tier1_ror.py:854).

Output: `dict[str, Any]`. On a match: `{"matched": True, "score": float, ror_id, official_name, acronym, org_types, is_research_institution, domain, website, children, country, country_code, "query_used": name, "country_filter": country_code, "strategy": "affiliation"|"affiliation_acronym"|"query"}` — the affiliation path additionally carries `"affiliation_used"` (enrichment/tier1_ror.py:668-676, 823-830). On a miss: `{"matched": False, "score": float}` (enrichment/tier1_ror.py:593-596). The function never raises: HTTP and generic exceptions return the miss dict (enrichment/tier1_ror.py:838-846).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:566-846.
1. `cache_key ← (name.lower().strip(), country_code)`; if present in the module-level `_ror_cache`, return the cached dict (lines 566-568).
2. `base_url ← env ROR_API_BASE` default `"https://api.ror.org/v2/organizations"` if not supplied (lines 570-571); `threshold ← float(env ROR_CONFIDENCE_THRESHOLD, "0.8")` (line 573).
3. `ror_name ← _expand_state_abbrevs(name)` (line 578). Build `affiliation_string ← join(", ", [ror_name] + [city, state, country if non-blank])` (lines 581-585). `location_tokens ← _extract_location_tokens(city, state, country)` (line 591).
4. Open one `httpx.AsyncClient(timeout=15.0, verify=resolve_tls_verify())` for all requests (lines 607-609).
5. **Strategy A (affiliation)** — `_try_affiliation(aff_str, rescore_names, strategy)` (lines 616-682):
   a. GET `base_url?affiliation=aff_str`; `raise_for_status()` (lines 620-624).
   b. `ch ←` the first item with `chosen is True` (lines 625-628). If no chosen item OR `ch["score"] < threshold` → return None (lines 629-636).
   c. Local re-validation: `local_score ← max(_score_org(n, org, location_tokens) for n in rescore_names)`; if `local_score < threshold` → return None (lines 643-653). (This applies the identifier-token guard ROR's own scorer lacks.)
   d. Country guard: if `not _country_ok(org, country_code)` → return None (lines 659-666), so control falls through to the country-filtered query endpoint.
   e. Accept: `fields ← _extract_org_fields(org)`; result carries ROR's own `ch["score"]` as `score`; cache and return (lines 667-682).
6. `rescore_names ← dedup([name, expand_abbreviations(name) or name, ror_name, expand_abbreviations(ror_name) or ror_name])` (lines 684-691). Run Strategy A with `affiliation_string`; return on success (lines 692-696).
7. **Strategy A2 (institution-acronym retry)**: `acr_name ← _expand_institution_acronyms(name)`; only if `acr_name.lower() ≠ name.lower()`, rebuild the affiliation string from `acr_name` + location parts and retry Strategy A with rescore names `[name, acr_name, expand_abbreviations(acr_name) or acr_name]` and strategy label `"affiliation_acronym"`; return on success (lines 701-714).
8. **Strategy B (query)**: GET `base_url?query=ror_name` plus, when `country_code` is set, `filter=locations.geonames_details.country_code:<country_code>` (lines 724-731).
9. If the filtered query returns zero items and a country filter was applied, retry once with `{"query": ror_name}` and no filter (lines 736-742).
10. Country guard on the candidate set: when `country_code` is set, drop every item failing `_country_ok` before ranking (lines 749-757). If no items remain → miss (lines 759-761).
11. Ranking: `expanded_query ← expand_abbreviations(ror_name) or ror_name` (line 768). For each of the first 10 items, compute rank key `(exact_match, score, -token_diff)` where `score = max(_score_org(expanded_query, item, location_tokens), _score_org(name, item, location_tokens))`, `exact_match = 1` iff any name variant equals `expanded_query` lowercased, and `token_diff = |len(display_name.split()) − len(expanded_query.split())|`; sort descending and take the first (lines 771-806).
12. If `best_score < threshold` → miss carrying the score (lines 815-821). Otherwise build the match dict with `strategy: "query"`, cache, and return (lines 823-836).
13. Exception paths: `httpx.HTTPStatusError` → log and return miss; any other exception → log and return miss (lines 838-846). Both miss returns are written to the cache via `_no_match` (lines 593-596).

#### 4 Constants
- `ROR_RESEARCH_TYPES = {"education", "healthcare", "government", "facility", "nonprofit", "archive", "other"}` (enrichment/tier1_ror.py:30-33).
- `_INSTITUTION_ACRONYMS: dict[str, str] = {"hft": "Hochschule für Technik"}` (enrichment/tier1_ror.py:50-52); `_INSTITUTION_ACRONYM_RE = re.compile(r"\b([A-Za-z]{2,6})\b")` (line 54).
- `_US_STATE_ABBREVS` — see the state-abbreviation section below (enrichment/tier1_ror.py:74-82).
- Default base URL `"https://api.ror.org/v2/organizations"` (enrichment/tier1_ror.py:571; also config.py:171-173).
- Threshold default `"0.8"` from `os.getenv("ROR_CONFIDENCE_THRESHOLD", "0.8")` (enrichment/tier1_ror.py:573). Note: `call_ror` reads the environment variable directly; `Settings.ror_confidence_threshold` (config.py:176-178, same env var and default) is a separate read used by the mock client (tests/mocks/ror_mock.py:276) — the live client does not consume the `Settings` field.
- HTTP timeout `15.0` seconds (enrichment/tier1_ror.py:608).
- Candidate ranking window: `items[:10]` (enrichment/tier1_ror.py:799).

#### 5 Complexity
Per uncached call: at most 4 HTTP requests — one affiliation request, one optional acronym-expanded affiliation request, one filtered query request, one optional unfiltered retry (enrichment/tier1_ror.py:620, 708-714, 730, 740). No retry loop exists for ROR: each request either succeeds or aborts the call via the exception handlers (838-846). Local scoring on the query path runs `_score_org` twice per candidate over at most 10 candidates (line 799), each of which iterates every name variant of the candidate.

#### 6 Worked example
From tests/test_tier1_ror_country.py:99-118 (`test_query_no_filter_retry_wrong_country_rejected`), which exercises the real `call_ror` over `httpx.MockTransport`:
- Input: `call_ror("BASF", country_code="DE", country="Germany")` (line 116).
- Affiliation request returns `{"items": []}` → Strategy A yields None (test line 108; source lines 625-636).
- Filtered query (`filter` present) returns `{"items": []}` (test line 112) → the no-filter retry fires (source lines 736-742) and returns the US BASF org `_org("https://ror.org/002yzpx87", "BASF", "US", "United States")` (test line 113).
- The country guard drops the single wrong-country candidate (`_country_ok` False for US vs requested DE; source lines 749-757), leaving zero items → `_no_match()` (source lines 759-761).
- Assertions: `res["matched"] is False` and both query calls happened (`calls["query"] == 2`) (test lines 117-118).
The accept path is exercised by `test_affiliation_right_country_accepted` (tests/test_tier1_ror_country.py:81-96): a chosen DE "BASF" with ROR score 1.0 passes local rescore and the country guard, and the result carries `ror_id == "https://ror.org/01q8f6705"` and `country_code == "DE"`.

#### 7 Failure modes
- ROR's affiliation scorer can return a confidently-scored wrong org whose name shares a dominant token (e.g. "ASL Analytical" for "EMSL Analytical, Inc."); the local rescore at lines 643-653 rejects it (production bug documented at enrichment/tier1_ror.py:638-642 and tests/test_tier1.py:121-139).
- Wrong-country same-name orgs returned by either strategy are rejected by `_country_ok` (enrichment/tier1_ror.py:659-666, 749-757); the observed failure was a US "BASF" returned for a German record (tests/test_tier1_ror_country.py:1-8).
- Any HTTP error, timeout, or malformed payload converts to `{"matched": False, "score": 0.0}` — the record is not failed, but a transient outage is indistinguishable from a genuine miss in the return value, and the miss is cached for the batch (enrichment/tier1_ror.py:593-596, 838-846).
- The cache key ignores city/state (enrichment/tier1_ror.py:566), so two same-name records in different cities of the same country share one cached result.

---

### ROR local name-match scoring (`_compute_name_score` — enrichment/tier1_ror.py)

#### 1 Purpose
Scores how well a query string matches any of a ROR organisation's name variants on a 0.0–1.0 scale, with guards that prevent city tokens, shared generic words, and mismatched acronyms from producing false perfect scores (enrichment/tier1_ror.py:188-335).

#### 2 Inputs and outputs
Inputs: `query: str`; `org_names: list[dict]` (ROR `names[]` entries with `value` and `types`); `location_tokens: set[str] | None` (enrichment/tier1_ror.py:188-192). Output: `float` in [0, 1] (line 214, 335). `_score_org(query, org, location_tokens)` is a wrapper that passes `org.get("names", [])` (enrichment/tier1_ror.py:389-395).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:212-335.
1. `query_lower ← _normalise_for_tokens(query.strip())`; empty → 0.0 (lines 212-214).
2. Partition variants: for each name entry with a non-empty `value`, normalise it; append to `all_values`; if its `types` intersects `_CANONICAL_NAME_TYPES = {"ror_display", "label"}`, also append to `canonical_values` (lines 218-227, 185).
3. **Step 1 — exact:** if `query_lower` equals any variant in `all_values` (aliases and acronyms included) → 1.0 (lines 229-232).
4. `query_tokens ← set(query_lower.split())`; `significant_query_tokens ← {t : len(t) ≥ 4}`; `distinctive_query_tokens ← significant_query_tokens − location_tokens` (lines 234-240).
5. `scoring_values ← [v in all_values : len(v) ≥ 5]`; if empty → 0.0 (lines 243-245).
6. `q_identifiers ← _extract_identifier_tokens(query)` (line 261).
7. **Steps 2+3 — subset/substring, canonical names only:** for each `val` in `canonical_values`:
   a. Skip if `q_identifiers` is non-empty and not a subset of `val`'s tokens (lines 270-273).
   b. Skip if `significant_query_tokens` is non-empty but `distinctive_query_tokens` is empty (query's only significant tokens are location tokens; lines 274-278).
   c. If `significant_query_tokens` is non-empty and is a subset of `val`'s tokens → 1.0 (lines 279-280).
   d. If length ratio `shorter/longer ≥ 0.9` and one normalised string contains the other → 1.0 (lines 281-284; `_length_ok` defined with default ratio 0.6 at lines 247-250 but invoked here with `ratio=0.9`).
8. **Step 4 — guarded fuzz, canonical names only:** `canonical_scoring ← [v in canonical_values : len(v) ≥ 5]` (line 306). For each, `token_ratio ← fuzz.token_sort_ratio(query_lower, val) / 100.0` (line 309); skip if not an improvement (310-311). Compute `q_distinctive ← {t in query_tokens : len(t) ≥ 5 and t ∉ _COMMON_DOMAIN_WORDS and t ∉ location_tokens}` (lines 316-321). If `q_distinctive` is non-empty and shares no token with the candidate → `token_ratio ← min(token_ratio, 0.7)` (lines 322-325). If `q_identifiers` is non-empty and not a subset of candidate tokens → `token_ratio ← min(token_ratio, 0.7)` (lines 326-328). Track the max (lines 329-330).
9. Return `max(best, _initialism_score(query, canonical_values))` (line 335).

#### 4 Constants
- `_CANONICAL_NAME_TYPES = {"ror_display", "label"}` (enrichment/tier1_ror.py:185).
- Significant-token length floor 4 (line 236); fuzz-variant length floor 5 (lines 243, 306); distinctive-token length floor 5 (line 318); substring length-ratio 0.9 (line 281); guard cap 0.7 (lines 325, 328); match threshold that the cap is designed to stay under: 0.8 (line 573).
- `_COMMON_DOMAIN_WORDS = {"regional", "health", "medical", "center", "centre", "research", "hospital", "clinic", "system", "systems", "services", "care", "university", "college", "institute", "school", "department", "division", "faculty", "laboratory", "group", "company", "inc", "corporation", "corp", "ltd", "llc", "international", "national", "american", "united", "global"}` (enrichment/tier1_ror.py:338-345).

#### 5 Complexity
One pass over all name variants for normalisation and exact matching; one pass over canonical variants for the subset/substring step; one `fuzz.token_sort_ratio` call per canonical variant of length ≥ 5; one `_initialism_score` pass over canonical variants. ROR orgs typically carry a handful of name entries; no API calls are made.

#### 6 Worked example
From tests/test_tier1.py:121-139 (`test_acronym_mismatch_capped`), a documented production bug:
- Query `"EMSL Analytical, Inc."` against org names `[{"value": "ASL Analytical", "types": ["ror_display", "label"]}, {"value": "ASL Analytical, Inc.", "types": ["alias"]}]`.
- Normalisation: query → `"emsl analytical inc"`; canonical value → `"asl analytical"` (`_normalise_for_tokens` strips punctuation and collapses "Inc."→"inc"; enrichment/tier1_ror.py:160-178).
- Step 1: no exact match. `q_identifiers = {"emsl"}` (EMSL is a 4-letter all-caps token; lines 117-133).
- Step 2/3: skipped because `{"emsl"} ⊄ {"asl", "analytical"}` (lines 270-273).
- Step 4: token_sort_ratio is high (the shared "Analytical" dominates — "~0.9" per the test's documentation at tests/test_tier1.py:126-128), but the identifier-token guard caps it at 0.7 (lines 326-328).
- Assertion: `_score_org("EMSL Analytical, Inc.", asl_org) < 0.8` (tests/test_tier1.py:138-139). The symmetric positive case `_score_org("ASL Analytical, Inc.", asl_org) == 1.0` (tests/test_tier1.py:141-151) fires via step 1 (exact alias match after normalisation).
Additional fixture-backed cases: city-token guard — `_score_org("Uni Stuttgart", Marienhospital-Stuttgart, loc) < 0.8` with `loc = _extract_location_tokens("Stuttgart", None, "Germany")`, while `_score_org("University Stuttgart", University-of-Stuttgart, loc) == 1.0` (tests/test_tier1.py:223-251); legal-suffix equivalence — `"Acme Corp."`, `"Acme Corp"`, `"Acme, Inc."`, `"Globex LLC"` etc. all score 1.0 against their long-form canonicals (tests/test_tier1.py:281-298).

#### 7 Failure modes
- A query sharing only generic domain words with a candidate ("Newman Regional Health" vs "Lakeland Regional Health", fuzz 0.83 per the comment at lines 296-299) is capped at 0.7 by the distinctive-token guard.
- A query whose only significant token is the city subset-matches every same-city org; guarded by the location-token exclusion (lines 274-278; bug narrative at lines 143-149 and tests/test_tier1.py:223-244).
- Aliases are excluded from subset/substring and fuzz scoring because they often name parent orgs or historical variants (lines 185-186, 286-291); an org only findable via an alias therefore matches only if the query equals the alias exactly (step 1).

---

### Token normalisation (`_normalise_for_tokens` — enrichment/tier1_ror.py)

#### 1 Purpose
Produces a lowercased, dash/punctuation-normalised, legal-suffix-canonicalised form of a string so legal-form variants of the same organisation compare equal during ROR scoring (enrichment/tier1_ror.py:160-178).

#### 2 Inputs and outputs
Input: `text: str`. Output: `str` (lowercase, single-spaced) (enrichment/tier1_ror.py:160, 173-178).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:173-178.
1. Lowercase; replace hyphen/en-dash/em-dash runs (`_DASH_RE = re.compile(r"[‐-―\-]+")`, line 94) with spaces.
2. Replace `.` and `,` (`_PUNCT_RE = re.compile(r"[.,]")`, line 96) with spaces; collapse whitespace (`_WS_RE = re.compile(r"\s+")`, line 97) and strip.
3. Apply `_LEGAL_SUFFIX_SUBS` in order (multi-word phrases before their constituent words).

#### 4 Constants
`_LEGAL_SUFFIX_SUBS` verbatim (enrichment/tier1_ror.py:105-114):
```
(re.compile(r"\blimited liability company\b"), "llc"),
(re.compile(r"\blimited liability partnership\b"), "llp"),
(re.compile(r"\bl l c\b"), "llc"),   # from "L.L.C." after dot removal
(re.compile(r"\bl l p\b"), "llp"),
(re.compile(r"\bincorporated\b"), "inc"),
(re.compile(r"\bcorporation\b"), "corp"),
(re.compile(r"\bcompany\b"), "co"),
(re.compile(r"\blimited\b"), "ltd"),
```

#### 5 Complexity
Four regex substitutions plus eight suffix substitutions per string; no loops over external data.

#### 6 Worked example
tests/test_tier1.py:307-313: `_normalise_for_tokens("Acme, Inc.") == "acme inc"`; `_normalise_for_tokens("Acme Incorporated") == "acme inc"`; `_normalise_for_tokens("Globex L.L.C.") == "globex llc"`; `_normalise_for_tokens("Globex Limited Liability Company") == "globex llc"`.

#### 7 Failure modes
"company" always collapses to "co" (line 111), so an organisation whose distinctive name contains the word "Company" loses it as a distinguishing token; the guard against unrelated matches then rests on the remaining tokens (tests/test_tier1.py:315-319 confirm "Acme Corp." vs "Globex Corporation" still scores < 0.8).

---

### Identifier-token extraction (`_extract_identifier_tokens` — enrichment/tier1_ror.py)

#### 1 Purpose
Collects short all-caps tokens (acronyms such as "EMSL", "NASA") from a string that act as distinguishing identifiers and must be present in a candidate name before a shortcut or high fuzz score is trusted (enrichment/tier1_ror.py:117-133).

#### 2 Inputs and outputs
Input: `text: str` (raw, pre-normalisation, so casing survives). Output: `set[str]` of lowercased tokens (enrichment/tier1_ror.py:129-133).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:129-133.
1. For each token matched by `_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")` (line 95): if `2 ≤ len(tok) ≤ 5` and `tok.isupper()`, add `tok.lower()` to the result set.

#### 4 Constants
Length bounds 2–5 (line 131).

#### 5 Complexity
One regex scan of the input string.

#### 6 Worked example
In tests/test_tier1.py:121-139, the query `"EMSL Analytical, Inc."` yields `{"emsl"}` — "Analytical" fails `isupper()`, "Inc" fails `isupper()` — which drives the 0.7 cap against "ASL Analytical" (see the `_compute_name_score` example above).

#### 7 Failure modes
Mixed-case short tokens ("Uni") are not captured (documented at tests/test_tier1.py:227-229); that gap is covered separately by the location-token guard.

---

### Location-token extraction (`_extract_location_tokens` — enrichment/tier1_ror.py)

#### 1 Purpose
Collects significant (≥ 4-character) tokens from the record's city/state/country so that address words can be excluded from perfect-score shortcuts — they identify where an org is, not which org it is (enrichment/tier1_ror.py:136-157).

#### 2 Inputs and outputs
Input: `*parts: str | None` (city, state, country). Output: `set[str]` of lowercased tokens normalised via `_normalise_for_tokens` (enrichment/tier1_ror.py:150-157).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:150-157.
1. For each non-empty part, normalise with `_normalise_for_tokens`, split on whitespace, and keep tokens with `len ≥ 4`.

#### 4 Constants
Token length floor 4 (line 155).

#### 5 Complexity
One normalisation pass per part (at most 3 parts as called from `call_ror`, line 591).

#### 6 Worked example
tests/test_tier1.py:242: `loc = _extract_location_tokens("Stuttgart", None, "Germany")` — used to show `_score_org("Uni Stuttgart", Marienhospital-Stuttgart, loc) < 0.8` (line 244) while `_score_org("University Stuttgart", University-of-Stuttgart, loc) == 1.0` (line 251).

#### 7 Failure modes
A city shorter than 4 characters (e.g. "Ulm") produces no location token, so the guard against same-city subset matches does not engage for such records. ⚠ NO FIXTURE COVERAGE for a < 4-character city; a record with such a city plus a generic name would be needed to exercise this.

---

### Initialism fallback (`_initialism_score` — enrichment/tier1_ror.py)

#### 1 Purpose
Recovers organisations referenced only by their initials ("JAH VA Hospital" → "James A. Haley Veterans' Hospital"), which token-sort fuzz alone scores too low to match (enrichment/tier1_ror.py:348-386).

#### 2 Inputs and outputs
Inputs: `query: str` (raw), `canonical_values: list[str]` (already normalised/lowercased). Output: `float` — exactly 1.0 or 0.0 (enrichment/tier1_ror.py:348, 385-386).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:365-386.
1. `acronyms ← {a in _extract_identifier_tokens(query) : len(a) ≥ 3}`; empty → 0.0 (lines 365-367).
2. `q_type_words ← tokens(_normalise_for_tokens(query)) ∩ _COMMON_DOMAIN_WORDS` (lines 368-369).
3. For each canonical value: split into words; skip if fewer than 2 words (lines 371-373); skip if `q_type_words` is non-empty and shares no word with the candidate (lines 374-375; the org-type word — e.g. "hospital" — must match).
4. `initials ← concat(first letter of each word)` (line 376). For each acronym, find it as a contiguous substring of `initials`; the matched run of words must contain at least one word with `len ≥ 4` that is not in `_COMMON_DOMAIN_WORDS`; if so → 1.0 (lines 377-384).
5. Otherwise 0.0 (line 386).

#### 4 Constants
Acronym length floor 3 (line 365); distinctive-word length floor 4 (line 384); `_COMMON_DOMAIN_WORDS` (lines 338-345).

#### 5 Complexity
For each canonical value, one substring search per query acronym.

#### 6 Worked example
tests/test_tier1.py:184-190 (`test_initialism_does_not_match_unrelated_acronym`):
- `cv = [_normalise_for_tokens("James A. Haley Veterans' Hospital")]` → `"james a haley veterans' hospital"`.
- `_initialism_score("JAH VA Hospital", cv) == 1.0`: acronyms `{"jah"}` ("VA" is length 2, filtered out at line 365); `q_type_words = {"hospital"}`, shared with the candidate; initials of `["james", "a", "haley", "veterans'", "hospital"]` = `"jahvh"`; `"jah"` found at position 0; run `["james", "a", "haley"]` contains "james" (≥ 4 chars, not a domain word) → 1.0.
- `_initialism_score("XYZ Hospital", cv) == 0.0`: `"xyz"` does not occur in `"jahvh"`.
The type-word guard is exercised at tests/test_tier1.py:171-182: `"JAH Hospital"` scores < 0.8 against "James A. Haley Veterans Bank".

#### 7 Failure modes
An acronym whose letters coincidentally match a run of initials of common/short words is rejected by the distinctive-word requirement (lines 381-384). An acronym of length 2 can never be recovered by this path (line 365) — such queries must match via a ROR acronym alias (step 1 of `_compute_name_score`).

---

### US state-abbreviation expansion (`_expand_state_abbrevs` — enrichment/tier1_ror.py)

#### 1 Purpose
Expands a traditional US newspaper-style state abbreviation token to the full state name for the ROR query only, so a distinctive geographic token survives ("Fla State Univ" → "Florida State Univ") (enrichment/tier1_ror.py:86-91).

#### 2 Inputs and outputs
Input: `name: str`. Output: `str` with matched tokens replaced (enrichment/tier1_ror.py:86-91). Applied at enrichment/tier1_ror.py:578; the expanded form is used for the affiliation string (581-585), the query parameter (724), and included among the rescore names (688-691) — never written to output names (comment at lines 69-73).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:86-91.
1. For each token matched by `_US_STATE_ABBREV_RE = re.compile(r"\b([A-Za-z]{3,5})\b\.?")` (line 83), replace it with `_US_STATE_ABBREVS[token.lower()]` when present; otherwise leave the token (including any trailing period) unchanged.

#### 4 Constants
`_US_STATE_ABBREVS` verbatim (enrichment/tier1_ror.py:74-82):
```
"fla": "Florida", "calif": "California", "ariz": "Arizona",
"colo": "Colorado", "conn": "Connecticut", "tenn": "Tennessee",
"wisc": "Wisconsin", "minn": "Minnesota", "okla": "Oklahoma",
"nebr": "Nebraska", "mich": "Michigan", "tex": "Texas",
"wash": "Washington", "penn": "Pennsylvania", "ill": "Illinois",
"ind": "Indiana", "mass": "Massachusetts", "miss": "Mississippi",
"ore": "Oregon", "kan": "Kansas", "ark": "Arkansas", "ala": "Alabama",
```
Two-letter postal codes are deliberately excluded as "too collision-prone" (comment, lines 68-73).

#### 5 Complexity
One regex substitution pass over the name.

#### 6 Worked example
tests/test_ror_state_abbrev.py:21-29: `"Fla State Univ" → "Florida State Univ"`, `"Wash State Univ" → "Washington State Univ"`, `"Penn State Univ" → "Pennsylvania State Univ"`, `"Calif Inst of Tech" → "California Inst of Tech"`, `"Tenn Tech Univ" → "Tennessee Tech Univ"`. Non-expansion cases (tests/test_ror_state_abbrev.py:31-38): "Univ of Florida", "Kent State University", "University of Washington", "Massachusetts Institute of Technology" are returned unchanged.

#### 7 Failure modes
Tokens like "mass", "miss", "wash", "ind" are ordinary English words; a name legitimately containing one (e.g. a company named with "Mass") would be rewritten in the query form. The rewrite affects only the ROR query and rescore names, never output (enrichment/tier1_ror.py:69-73), so the harm is limited to a possible wrong or missed registry candidate. ⚠ NO FIXTURE COVERAGE for a false-positive expansion of an ordinary-word token.

---

### Institution-acronym expansion (`_expand_institution_acronyms` — enrichment/tier1_ror.py)

#### 1 Purpose
Replaces known institution acronyms that ROR carries no alias for with their full institutional name, used only to build the fallback affiliation request (enrichment/tier1_ror.py:57-65, applied at 701-714).

#### 2 Inputs and outputs
Input: `name: str`. Output: `str` with known acronym tokens replaced (enrichment/tier1_ror.py:57-65).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:63-65.
1. For each token matched by `_INSTITUTION_ACRONYM_RE = re.compile(r"\b([A-Za-z]{2,6})\b")` (line 54), replace with `_INSTITUTION_ACRONYMS[token.lower()]` when present, else keep.

#### 4 Constants
`_INSTITUTION_ACRONYMS = {"hft": "Hochschule für Technik"}` (enrichment/tier1_ror.py:50-52).

#### 5 Complexity
One regex substitution pass; the retry it feeds adds at most one extra affiliation HTTP request per `call_ror` invocation (lines 701-714).

#### 6 Worked example
tests/test_tier1.py:253-262: `_expand_institution_acronyms("HFT Stuttgart") == "Hochschule für Technik Stuttgart"`; `_expand_institution_acronyms("Acme Corp") == "Acme Corp"` (unknown tokens untouched).

#### 7 Failure modes
The map contains a single entry; any other unaliased institution acronym still fails to resolve (comment at lines 44-49 invites extension). The retry only fires when expansion changes the name case-insensitively (line 702), so it can never regress names that already resolve.

---

### Country guard (`_country_ok`, `_org_country_code` — enrichment/tier1_ror.py)

#### 1 Purpose
Rejects a ROR candidate whose primary-location country differs from the requested ISO alpha-2 country, and rejects candidates with no country code when a country was requested (enrichment/tier1_ror.py:453-464).

#### 2 Inputs and outputs
`_org_country_code(org: dict) -> str | None`: reads `locations[0].geonames_details.country_code` and uppercases it; None when absent (enrichment/tier1_ror.py:440-450). `_country_ok(org: dict, want_country_code: str | None) -> bool` (enrichment/tier1_ror.py:453-464).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:453-464.
1. If `want_country_code` is falsy → True.
2. Otherwise → `_org_country_code(org) == want_country_code.strip().upper()`. An org with no country code therefore fails when a country was requested (docstring, lines 459-464).

#### 4 Constants
None.

#### 5 Complexity
Constant per candidate; applied once to the affiliation-chosen org (line 659) and once per query candidate before ranking (lines 749-757).

#### 6 Worked example
tests/test_tier1_ror_country.py:62-96: a chosen US "BASF" for a DE request is rejected (`matched is False`, `ror_id is None`, lines 76-78); the DE "BASF" is accepted with `country_code == "DE"` (lines 93-96). With `country_code=None` the US org is kept (lines 121-135).

#### 7 Failure modes
Only `locations[0]` is inspected (line 449); a multi-location org whose first listed location is a different country from the requested one is rejected even if a later location matches. ⚠ NO FIXTURE COVERAGE for a multi-location org.

---

### ROR org-field extraction (`_extract_org_fields`, `_strip_ror_country_suffix` — enrichment/tier1_ror.py)

#### 1 Purpose
Converts a matched ROR v2 organisation dict into the flat field set the orchestrator consumes (official name, current acronym, website, domain, research classification, children, country) (enrichment/tier1_ror.py:467-533).

#### 2 Inputs and outputs
Input: `org: dict[str, Any]` (a ROR v2 organisation). Output: `dict` with keys `ror_id, official_name, acronym, org_types, is_research_institution, domain, website, children, country, country_code, org_names` (enrichment/tier1_ror.py:521-533). `call_ror` copies all keys except `org_names` into its result (lines 671, 826).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:467-533.
1. `display_name ←` the value of the first `names[]` entry whose types contain `"ror_display"`; if none, the first name entry's value (lines 470-477).
2. `display_name ← _strip_ror_country_suffix(display_name)` (line 483) — removes a trailing " (Country)" disambiguator matched by `_ROR_COUNTRY_SUFFIX_RE` (a parenthesised country from a fixed list of 40 country names, lines 398-406) and a trailing ", City, ST, Country" address tail matched by `_ROR_ADDRESS_SUFFIX_RE` (lines 411-416); if stripping empties the string, the original is kept (lines 419-424).
3. **Acronym-currency selection:** collect all non-empty values of `names[]` entries typed `"acronym"`; take the first candidate for which `acronym_matches_name(cand, display_name)` is True (letters equal the initials of the current display name); if none match, `acronym ← None` — never the first stale acronym (lines 485-499; rationale in the comment: ROR carries historical acronyms such as "NBS" for NIST, "PHS" for Mass General Brigham).
4. `website ← extract_website_from_ror(org)`; `domain ← extract_domain(website)` when a website exists (lines 501-502).
5. `org_types ←` lowercased `org["types"]`; `is_research ← any(t ∈ ROR_RESEARCH_TYPES)` (lines 504-505; set defined at 30-33).
6. `children ← [{"name": r["label"], "id": r["id"]} for r in relationships where type.lower() == "child"]` (lines 507-511).
7. `country ← locations[0].geonames_details.country_name` when locations exist (lines 513-519); `country_code ← _org_country_code(org)` (line 531).

#### 4 Constants
- `_ROR_COUNTRY_SUFFIX_RE` (enrichment/tier1_ror.py:398-406), verbatim pattern:
```
r"\s*\(\s*(?:United States|USA|United Kingdom|UK|Germany|France|"
r"Japan|China|Canada|Australia|Switzerland|Netherlands|Spain|"
r"Italy|Sweden|Denmark|Norway|Finland|Belgium|Austria|Ireland|"
r"Poland|Israel|Singapore|Brazil|India|Mexico|New Zealand|"
r"South Korea|Russia|Portugal|Czech Republic|Greece|Turkey|"
r"South Africa|Hong Kong|Taiwan)\s*\)\s*$", re.IGNORECASE
```
- `_ROR_ADDRESS_SUFFIX_RE` (enrichment/tier1_ror.py:411-416), verbatim pattern:
```
r",\s*[A-Z][A-Za-z .'-]+,\s*[A-Z]{2},\s*(?:USA|United States|"
r"UK|United Kingdom|Canada|Germany|France|Japan|Australia|"
r"Switzerland|Netherlands|China|India|Brazil|Italy|Spain|Sweden)\s*$", re.IGNORECASE
```
- `ROR_RESEARCH_TYPES` (enrichment/tier1_ror.py:30-33).

#### 5 Complexity
One pass over `names[]` for the display name, one over the acronym candidates (each triggering a `name_initials` computation), one over `links[]`, one over `relationships[]`.

#### 6 Worked example
`_strip_ror_country_suffix("Pfizer (United States)") == "Pfizer"` and campus qualifiers are preserved — `_strip_ror_country_suffix("University of California, Davis") == "University of California, Davis"` (tests/test_ror_name_verbatim.py:50-70). Acronym currency is fixture-backed at the helper level: `acronym_matches_name("NIST", "National Institute of Standards and Technology")` is True while `acronym_matches_name("NBS", …)` and `acronym_matches_name("PHS", "Mass General Brigham")` are False (tests/test_search_terms_fixes.py:104-107). ⚠ NO FIXTURE COVERAGE for `_extract_org_fields` end-to-end with multiple `acronym` entries; a ROR org dict carrying both a historical and a current acronym entry would be needed.

#### 7 Failure modes
- An org whose current acronym is not the exact initials of the display name (e.g. an acronym formed from selected words) is emitted with `acronym = None` (lines 496-499) — a deliberate false-negative preference over shipping a stale acronym.
- A display name that legitimately ends in a parenthesised country from the list would lose it (lines 419-424 keep the original only when stripping empties the string). ⚠ NO FIXTURE COVERAGE for this edge.

---

### ROR website extraction (`extract_website_from_ror` — enrichment/tier1_ror.py)

#### 1 Purpose
Returns the ROR organisation's official homepage: the first `links[]` entry with `type == "website"` (enrichment/tier1_ror.py:427-437).

#### 2 Inputs and outputs
Input: `ror_org: dict`. Output: `str | None` (enrichment/tier1_ror.py:427-437). The orchestrator writes it directly to `website_url` on a ROR match ("Path A: ROR's links[] website is authoritative", enrichment/orchestrator.py:2045-2048); the derived registrable domain (via `extract_domain`, utils/text_utils.py:23-48, including the two-part-TLD list at 37-40) is written to `domain` (enrichment/orchestrator.py:2041-2043).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:434-437.
1. For each `link` in `org.links` (treating None as empty): if `link["type"] == "website"` and `link["value"]` is truthy → return `link["value"]`.
2. Return None.

#### 4 Constants
The literal link type `"website"` (line 435).

#### 5 Complexity
One pass over `links[]`.

#### 6 Worked example
⚠ NO FIXTURE COVERAGE — no test constructs a ROR org dict with a populated `links[]` and runs it through `extract_website_from_ror`; the HTTP-path fixtures set `"links": []` (tests/test_tier1_ror_country.py:37) and `MockRORClient` returns `website` directly from curated data (e.g. `"website": "https://web.mit.edu"`, tests/mocks/ror_mock.py:26, 294) without invoking the extractor. A ROR org dict such as `{"links": [{"type": "website", "value": …}]}` would be needed.

#### 7 Failure modes
If ROR lists no `website`-typed link, the function returns None and the orchestrator leaves `website_url`/`domain` unset from this path (enrichment/orchestrator.py:2041-2048).

---

### ROR name adoption vs keep-input decision (Tier-1 ROR branch — enrichment/orchestrator.py)

#### 1 Purpose
On a ROR match, decides whether to write ROR's official name into `name1_enriched` or keep the (standardised) input name, and writes identifier, classification, domain, website, acronym, and child-department fields (enrichment/orchestrator.py:1930-2103).

#### 2 Inputs and outputs
Inputs: preprocessed `pp_name1` (stashed at line 1901), the record's structured address fields, and the `ror_parent` dict from `RORClient.call` (enrichment/orchestrator.py:1955-1961). Outputs written into the result dict: `name1_enriched`, `_ror_acronym` (transient), `ror_id`, `tier_used = 1`, `source = "ROR"`, `confidence = "high"`, `record_type`, `domain`, `website_url`, `enrichment_status = "enriched"`, use cases 2 and 3, and optionally `name2/3/4_enriched` from child matching (enrichment/orchestrator.py:2008-2103).

#### 3 Pseudocode
Source: enrichment/orchestrator.py:1930-2103 (match branch) and 2105-2246 (miss branch).
1. Guard: only runs when `pp_name1` is non-blank (line 1930). `country_code ← country_to_iso_code(record.country)` (line 1932; map at utils/text_utils.py:119-164, lookup at 900-908).
2. `name1_cleaned ← strip_address_fragments(pp_name1, street=pp_street1 or record.street, city, state, zip)` falling back to `pp_name1.strip()` (lines 1939-1945; procedure at utils/text_utils.py:803-897).
3. `ror_parent ← ror_client.call(name1_cleaned, country_code, country, city, state)` (lines 1955-1961).
4. **If matched** (line 1976):
   a. `off ← official_name.strip()`; `original_name1 ← (pp_name1 or name1_cleaned).strip()`; `candidates ← {original_name1, expand_abbreviations(original_name1), name1_cleaned, expand_abbreviations(name1_cleaned)}` (lines 1999-2006). The guard deliberately compares against the ORIGINAL `pp_name1`, not only the address-stripped query form, because `strip_address_fragments` can remove a campus city equal to the record's City ("UNIVERSITY OF CALIFORNIA, DAVIS" + City "Davis" → "University of California") and ROR restoring the campus must not read as an identity change (comment, lines 1987-1998).
   b. If any candidate satisfies `canonical_preserves_identity(c, off)` → `name1_enriched ← off` (adopt ROR's name; lines 2007-2008). Else → `kept ← original_name1 or name1_cleaned`; `name1_enriched ← clean_passthrough_org_name(kept) or kept` (keep input, standardised: abbreviations expanded, ALL-CAPS title-cased; lines 2009-2023). In both cases ROR's `ror_id`/domain/website are still used — it is the same entity (comment, lines 1981-1986).
   c. Carry `_ror_acronym` when ROR supplied a current acronym (lines 2027-2029). Write `ror_id`, `tier_used = 1`, `source = "ROR"`, `confidence = "high"` (lines 2031-2034). `record_type ← "research_institution"` iff `is_research_institution` else `"company"` (lines 2036-2040). Write `domain` and `website_url` when present (lines 2041-2048).
   d. If `record_type == "company"` → run `_run_lei_lookup(record, result, name1_cleaned, country_code)`; a GLEIF match overwrites `name1` while ROR's domain/website are preserved; a research institution never reaches this call (lines 2050-2060; confirmed by tests/test_tier1_lei.py:401-414 where the mock LEI client records `call_count == 0` for Stanford).
   e. Mark `enrichment_status = "enriched"`, append use cases 2 and 3 (lines 2062-2067). If there is no `pp_name2` and no contact → finalise and return immediately so Tier 3 cannot overwrite the canonical name (lines 2069-2078).
   f. Child matching: for each of (`name2`, `name3`, `name4`) that is non-blank, expand abbreviations and run `_match_child_locally(val, ror_parent["children"])`; on a hit write `{field}_enriched ← child name` (lines 2080-2103).
5. **If ROR missed** (lines 2105-2246):
   a. `looks_research ← looks_like_research_institution(name1_cleaned)` (line 2114; regex at utils/text_utils.py:355-363).
   b. Research-looking → passthrough: `name1_enriched ← name1_cleaned`, `source = "passthrough"`, `confidence = "low"`, `tier_used = 1`, `record_type = "research_institution"`, `enrichment_status = "unresolved"`, `flag_for_review = True` with reason "Research-institution name not found in ROR — left unchanged for manual review"; short-circuit when no name2/contact (lines 2123-2144). The company-canonical LLM is deliberately not called for these (comment, lines 2106-2113).
   c. Otherwise → `_run_lei_lookup` first (deterministic; lines 2146-2154). On a match, return immediately when no name2 (lines 2155-2162). On a miss → `run_company_canonical(...)` with the record's street and postal code (lines 2163-2178); if that fails the identity guard but surfaces a `proposed_name` that `canonical_is_spelling_variant(name1_cleaned, proposed)` accepts, re-verify the proposal against GLEIF via `_run_lei_lookup`; a confirmed entity attaches the corrected legal name and LEI (lines 2179-2215).
   d. Accepted LLM canonical → `name1_enriched`, `source = "llm_canonical"`, `confidence = "high"`, `record_type = "company"`, `tier_used = 2`, `enrichment_status = "enriched"`, `flag_for_review = True` reason "LLM canonical company name — verify" (lines 2217-2236). Attempted-but-failed → passthrough with `source = "passthrough"`, `confidence = "low"`, `tier_used = 1`, `record_type = "unknown"` (lines 2237-2243).

#### 4 Constants
Flag-reason strings verbatim: `"Research-institution name not found in ROR — left unchanged for manual review"` (enrichment/orchestrator.py:2131-2134); `"LLM canonical company name — verify"` (line 2229). All other constants belong to the callees documented elsewhere.

#### 5 Complexity
Per record: one ROR client call (itself ≤ 4 HTTP requests), at most 4 identity-guard evaluations (the candidate set, line 2001-2007), at most one LEI lookup on the match path, and up to three child-match scans (name2/3/4) over the parent's children list. The miss path adds at most: one LEI lookup, one LLM call, and one more LEI lookup (typo re-verify).

#### 6 Worked example
The decision logic is mirrored exactly by `_name1_decision` in tests/test_ror_name_verbatim.py:29-47 (documented as mirroring the orchestrator's decision):
- **Adopt path** (tests/test_ror_name_verbatim.py:78-84): original `"UNIVERSITY OF CALIFORNIA, DAVIS"`, street `"Chemistry Department, | One Shields Ave,"`, city `"Davis"`, state `"California"`, zip `"95616-5270"`, ROR official `"University of California, Davis"`. `strip_address_fragments` yields `"UNIVERSITY OF CALIFORNIA"` (test line 127) — the campus city is stripped. The guard on that stripped form is False (line 128), but the guard on the ORIGINAL is True (lines 129-131), so the candidate set admits the official name: result `"University of California, Davis"`.
- **Keep-and-standardise path** (tests/test_ror_name_verbatim.py:103-111): original `"Stuttgart Univ of Applied Sciences"`, ROR official `"Hochschule für Technik Stuttgart"` (record 42000006 per the test comment). No candidate passes the identity guard (the official shares no distinctive token coverage), so the kept input is standardised via `clean_passthrough_org_name`: result `"Stuttgart University of Applied Sciences"` ("Univ" → "University").
- **Keep-original-on-drop path** (tests/test_ror_name_verbatim.py:94-101): `"USDA Agricultural Research Service"` vs ROR official `"Agricultural Research Service"` — "USDA" is a dropped distinctive token, guard False, original kept.
End-to-end LEI/typo interplay: tests/test_orchestrator.py:115-146 — record `name1="Bayr AG"`, street `"Kaiser-Wilhelm-Allee 1"`, city `"Leverkusen"`, country `"DE"`: ROR and raw-name LEI both miss, the LLM proposes "Bayer AG", the identity guard blocks it, `canonical_is_spelling_variant` passes it, GLEIF re-verify confirms, and the result carries `name1_enriched == "Bayer AG"`, `lei_id == "3157002JBAOA57BQAT84"`, `tier_used == 1`, `source == "gleif"`.

#### 7 Failure modes
- ROR returning a short canonical form that drops a parent qualifier ("Agricultural Research Service" for "USDA Agricultural Research Service") would silently rename the entity; the identity guard keeps the fuller input while still using ROR's id/domain/website (enrichment/orchestrator.py:1981-2023).
- Comparing the guard against the address-stripped query form loses campus qualifiers equal to the record's city; fixed by including `pp_name1` in the candidate set (enrichment/orchestrator.py:1987-2006; regression documented at tests/test_ror_name_verbatim.py:119-131).
- A kept input previously shipped raw (ALL-CAPS/abbreviated); the drop path now standardises it via `clean_passthrough_org_name` (enrichment/orchestrator.py:2014-2018).

---

### Local child matching (`_match_child_locally` — enrichment/orchestrator.py)

#### 1 Purpose
Matches a record's Name 2/3/4 against the ROR parent's `child` relationships with rapidfuzz, avoiding a second ROR API call (enrichment/orchestrator.py:636-662).

#### 2 Inputs and outputs
Inputs: `name2: str`, `children: list[dict]` (each `{"name", "id"}` from `_extract_org_fields`, enrichment/tier1_ror.py:507-511). Output: the best child dict with an added `"score"` key, or None (enrichment/orchestrator.py:639-662).

#### 3 Pseudocode
Source: enrichment/orchestrator.py:646-662.
1. Empty children or blank name → None.
2. For every child, `ratio ← fuzz.token_sort_ratio(name2.strip().lower(), child_name.lower())`; track the best.
3. Return `{**best, "score": best_score}` if `best_score ≥ _CHILD_MATCH_THRESHOLD`, else None.

#### 4 Constants
`_CHILD_MATCH_THRESHOLD = 70` (rapidfuzz token_sort_ratio minimum; enrichment/orchestrator.py:633). The caller expands abbreviations in the field value first (`expand_abbreviations(field_val.strip()) or field_val.strip()`, enrichment/orchestrator.py:2085-2088).

#### 5 Complexity
One `token_sort_ratio` per child per name field; up to three name fields (name2/3/4; enrichment/orchestrator.py:2082).

#### 6 Worked example
⚠ NO FIXTURE COVERAGE for `_match_child_locally` directly. Fixture children exist — e.g. MIT's children list including `{"name": "Department of Chemistry", "id": "https://ror.org/fakechem"}` (tests/mocks/ror_mock.py:31-39), asserted present in the client result at tests/test_tier1.py:44-50 — but no test invokes the child-match scoring itself. A record with a ROR-matched name1 and a name2 near a child label (e.g. "Dept of Chemistry" against MIT's children) would be needed.

#### 7 Failure modes
Threshold 70 on token_sort_ratio accepts moderately different unit names; the result overwrites `name2_enriched` only when a child clears it (enrichment/orchestrator.py:2100-2103). A downstream guard prevents the lab-resolver from overwriting a ROR-resolved non-granular department (enrichment/orchestrator.py:2280-2296).

---

### Identity guard (`canonical_preserves_identity` — utils/text_utils.py)

#### 1 Purpose
Returns True when a canonical name plausibly names the same entity as the original — reformatting, legal-suffix or institution-type additions, abbreviation or acronym expansion — and False on identity replacement (utils/text_utils.py:694-747).

#### 2 Inputs and outputs
Inputs: `original: str | None`, `canonical: str | None`. Output: `bool` (utils/text_utils.py:694). Consumers: ROR name adoption (enrichment/orchestrator.py:2007), Tier 3 name1 suggestions (enrichment/orchestrator.py:710), company canonicalisation (enrichment/company_canonical.py:83).

#### 3 Pseudocode
Source: utils/text_utils.py:717-747.
1. Either side blank → True (permissive; lines 717-718).
2. `o ← _identity_tokens(original)`, `c ← _identity_tokens(canonical)`; either empty → True (lines 719-722). `_identity_tokens` first collapses long-form legal designators via `_LONGFORM_LEGAL_SUBS`, then tokenises `[A-Za-z0-9]+` lowercased and keeps tokens with `len ≥ 2` not in `_GENERIC_COMPANY_WORDS` (utils/text_utils.py:646-654).
3. **Coverage rule:** if every token in `o` is covered by some token in `c` (`_token_covers`: equal, or both ≥ 4 chars and one is a prefix of the other; utils/text_utils.py:686-691), then compute `extras ←` tokens of `c` covered by no token of `o`; return True iff every extra is in `_ORG_TYPE_ADDABLE` (lines 727-729). The canonical may add "University"-style words but never a brand qualifier.
4. **Acronym rule:** `raw ←` alphabetic tokens (`[A-Za-z&]+`) of the original not in `_GENERIC_COMPANY_WORDS`; if exactly one token, all-caps, 2–6 letters: build initials of the canonical's words (regex `[A-Za-z0-9]+`), skipping stopwords `{"the", "of", "and", "for", "de", "la", "le"}`; return True if the initials equal or start with the acronym (lines 735-746).
5. Otherwise False (line 747).

#### 4 Constants
- `_GENERIC_COMPANY_WORDS = {"group", "inc", "incorporated", "llc", "llp", "lp", "corp", "corporation", "company", "co", "ltd", "limited", "holdings", "holding", "plc", "gmbh", "ag", "sa", "nv", "bv", "spa", "srl", "pty", "the", "and", "of", "for"}` (utils/text_utils.py:605-609).
- `_ORG_TYPE_ADDABLE = {"university", "universities", "college", "colleges", "school", "schools", "institute", "institutes", "laboratory", "laboratories", "foundation", "center", "centre", "centers", "hospital", "hospitals", "clinic", "academy", "conservatory", "seminary", "polytechnic"}` (utils/text_utils.py:678-683).
- `_LONGFORM_LEGAL_SUBS` (utils/text_utils.py:619-626): `gesellschaft mit beschränkter haftung → "GmbH"`, `limited liability company → "LLC"`, `limited liability partnership → "LLP"`, `aktiengesellschaft → "AG"`, `incorporated → "Inc"`, `corporation → "Corp"` (all case-insensitive).
- `_token_covers` prefix floor: `min(len(a), len(b)) >= 4` (utils/text_utils.py:691).
- Acronym stopwords `{"the", "of", "and", "for", "de", "la", "le"}` (utils/text_utils.py:741).

#### 5 Complexity
`O(|o| × |c|)` token-pair comparisons; token sets are name-word counts.

#### 6 Worked example
From tests/test_canonical_identity.py:15-45:
- Accepts: `("Iso Group Inc", "ISO Group, Inc.")`, `("Apple", "Apple Inc.")` (legal-suffix add), `("IBM", "International Business Machines")` (acronym rule: initials "ibm"), `("UF", "University of Florida")` (initials skip "of" → "uf"), `("Mass Inst Tech", "Massachusetts Institute of Technology")` (prefix coverage: mass→massachusetts, inst→institute, tech→technology), `("Harvard", "Harvard University")` and `("Mayo", "Mayo Clinic")` (extras in `_ORG_TYPE_ADDABLE`).
- Rejects: `("Iso Group Inc", "CoStar Group")` (no coverage of "iso"), `("Liberty Health Sciences", "Liberty Science Center")` (shares "Liberty" only), `("USDA Agricultural Research Service", "Agricultural Research Service")` ("usda" dropped), `("Precision Instruments Co.", "World Precision Instruments")` (extra "world" not org-type-addable), `("NASA Jet Propulsion Laboratory", "Jet Propulsion Laboratory")`.
- Legal-form equivalence: `("SAP Aktiengesellschaft", "SAP AG")` True in both directions (tests/test_canonical_identity.py:63-67) — both reduce to `{"sap"}`.
- Blank permissiveness: `(None, "Anything")` and `("Anything", None)` both True (tests/test_canonical_identity.py:70-73).

#### 7 Failure modes
- When the original consists entirely of generic words, `o` is empty and the guard returns True by design (utils/text_utils.py:712-715, 721-722) — legitimate reformatting is never blocked, at the cost of not policing such names.
- `_token_covers` treats any ≥ 4-character prefix pair as equivalent ("science"↔"sciences" and equally "internal"↔"international"), so a canonical replacing one long word with an unrelated word sharing a 4-char prefix would pass coverage. ⚠ NO FIXTURE COVERAGE for a false-positive prefix collision.

---

### Spelling-variant gate (`canonical_is_spelling_variant` — utils/text_utils.py)

#### 1 Purpose
Accepts a canonical name only when it is the original modulo a minor spelling correction, gating the GLEIF re-verification of an LLM-proposed name so an entity swap can never be laundered (utils/text_utils.py:772-800).

#### 2 Inputs and outputs
Inputs: `original: str | None`, `canonical: str | None`. Output: `bool` (utils/text_utils.py:772-774). Consumer: enrichment/orchestrator.py:2191-2197.

#### 3 Pseudocode
Source: utils/text_utils.py:786-800.
1. Either side blank → False; empty token sets → False (lines 786-791).
2. If every original token is already covered exactly/by-prefix (`_token_covers`) → False (that case belongs to `canonical_preserves_identity`; a genuine spelling difference is required; lines 792-796).
3. If not every original token is covered by `_fuzzy_token_covers` → False (line 797-798). `_fuzzy_token_covers(a, b)`: `_token_covers(a, b)` OR (`min(len) ≥ 4` and `fuzz.ratio(a, b) ≥ _SPELLING_VARIANT_TOKEN_RATIO`) (utils/text_utils.py:756-769).
4. `extras ←` canonical tokens fuzzily covered by no original token; return True iff all extras are in `_ORG_TYPE_ADDABLE` (lines 799-800).

#### 4 Constants
`_SPELLING_VARIANT_TOKEN_RATIO = 85.0` (utils/text_utils.py:753); fuzzy floor `min(len(a), len(b)) >= 4` (line 767); `_ORG_TYPE_ADDABLE` (utils/text_utils.py:678-683).

#### 5 Complexity
`O(|o| × |c|)` token pairs with one `fuzz.ratio` per non-covered pair.

#### 6 Worked example
tests/test_canonical_identity.py:98-118 — accepts `("Bayr AG", "Bayer AG")`, `("Siemns AG", "Siemens AG")`, `("Microsft Corp", "Microsoft Corporation")`, `("Volkswagon AG", "Volkswagen AG")`; rejects `("Iso Group Inc", "CoStar Group")` (entity swap), `("Bayer AG", "Baker AG")` (fuzz below 85 for a genuinely different word), `("Bayer AG", "Bayer AG")` (identical — step 2 returns False), `("Pfizer", "Pfizer Inc")` (pure suffix add — identity path, not a typo).

#### 7 Failure modes
The gate alone does not accept anything into output: a passing proposal must still be confirmed by GLEIF as an ACTIVE entity in the record's country before it is written (enrichment/orchestrator.py:2185-2215; docstring at utils/text_utils.py:781-784).

---

### Passthrough name standardisation (`clean_passthrough_org_name` — utils/text_utils.py)

#### 1 Purpose
Normalises an org name that passed through enrichment uncanonicalised — title-cases ALL-CAPS input, then expands common abbreviations — so passthrough rows are consistent with ROR-matched rows (utils/text_utils.py:313-327).

#### 2 Inputs and outputs
Input: `name: str | None`. Output: `str | None` (utils/text_utils.py:313). Used on the ROR keep-input path (enrichment/orchestrator.py:2018).

#### 3 Pseudocode
Source: utils/text_utils.py:322-327.
1. Blank → returned unchanged.
2. `cleaned ← smart_title_case(name) or name` — title-case runs FIRST; expanding "CTR"→"Center" first would make the string mixed-case and defeat the ALL-CAPS guard (comment, lines 318-321).
3. `cleaned ← expand_abbreviations(cleaned) or cleaned`; return.

`smart_title_case` (utils/text_utils.py:285-310): returns the value unchanged unless it `isupper()` (line 299 — mixed-case ROR/LLM names are never altered); per word: `_CASE_EXCEPTIONS` whole-token lookup, hyphen segments cased independently, otherwise `_case_segment` (utils/text_utils.py:266-282): connectors lowercased; `_FORCE_TITLE_SHORT` capitalised; `_KEEP_UPPER_ACRONYMS` kept; ≤ 3 letters kept upper (assumed acronym); 4-5 letters with no vowel kept upper; otherwise capitalised with `_mc_name` restoring "Mc" surnames (utils/text_utils.py:258-263).

`expand_abbreviations` (utils/text_utils.py:204-215): applies each compiled pattern of `_ABBREV_MAP` in order, case-insensitively.

#### 4 Constants
- `_ABBREV_MAP` verbatim (utils/text_utils.py:170-196): misspelling class `r"\b(?:Universtiy|Univeristy|Univesity|Universty|University|Univercity)\b" → "University"`; `Dept→Department`, `Univ→University`, `Uni→University`, `Lab→Laboratory`, `Inst→Institute`, `Ctr→Center`, `Chem→Chemistry`, `Biol→Biology`, `Phys→Physics`, `Sci→Science`, `Eng→Engineering`, `Med (before Center/Centre/Ctr)→Medical`, `Med→Medicine`, `Org→Organization`, `Assoc→Association`, `Tech→Technology`, `Natl→National`, `Intl→International`, `Div→Division` — all with pattern shape `r"\bX\.?(?=\s|$)"`.
- `_TITLE_CASE_CONNECTORS = {"of", "and", "for", "the", "in", "at", "&"}` (utils/text_utils.py:219).
- `_FORCE_TITLE_SHORT = {"INC", "LTD", "CO", "BAY", "NEW", "OLD", "SUN", "OAK", "BIG", "RED", "SKY", "SEA", "AIR", "SON", "TWO", "ONE", "KEY", "TOP", "BOX"}` (utils/text_utils.py:227-230).
- `_KEEP_UPPER_ACRONYMS = {"NASA", "NOAA", "NIH", "FDA", "USDA", "EMSL", "IEEE", "NIST", "NJIT", "TUHH", "NREL", "SLAC", "CERN", "CNRS", "CSIRO", "CCSF", "UCSF", "UCSD", "UCLA", "UCSB", "UCSC", "SUNY", "CUNY", "UMASS", "UPENN", "UCONN"}` (utils/text_utils.py:232-244).
- `_VOWELS = set("AEIOU")` (line 245); `_CASE_EXCEPTIONS = {"bio-rad": "Bio-Rad", "abx-cro": "ABX-CRO", "dana-farber": "Dana-Farber", "at&t": "AT&T"}` (utils/text_utils.py:250-255).

#### 5 Complexity
One word-level pass for casing plus one regex pass per abbreviation pattern (20 patterns).

#### 6 Worked example
tests/test_ror_name_verbatim.py:113-117 (`test_allcaps_input_titlecased_on_drop`): input `"STUTTGART UNIV OF APPLIED SCIENCES"` on the keep-input path → title-case (`isupper()` True) → `"Stuttgart Univ of Applied Sciences"` (connector "of" lowercased, 4+-letter vowel-bearing words capitalised) → abbreviation expansion `Univ→University` → `"Stuttgart University of Applied Sciences"`.

#### 7 Failure modes
Casing heuristics can misjudge unusual tokens: any ALL-CAPS 3-letter word not in `_FORCE_TITLE_SHORT` is kept upper as a presumed acronym (utils/text_utils.py:279), and a ≥ 4-letter vowel-bearing acronym not in `_KEEP_UPPER_ACRONYMS` is title-cased (the allowlists exist precisely because of observed misfires — "TUHH" → "Tuhh", "UCSF" → "Ucsf"; comments at 234-243).

---

### Acronym-currency helpers (`name_initials`, `acronym_matches_name` — utils/text_utils.py)

#### 1 Purpose
Determine whether a candidate acronym's letters equal the initials of a name's significant words — used by `_extract_org_fields` to select ROR's current acronym over historical ones (utils/text_utils.py:927-954; consumer at enrichment/tier1_ror.py:496-499).

#### 2 Inputs and outputs
`name_initials(name: str | None) -> str` — uppercase initials of non-stopword words (utils/text_utils.py:927-941). `acronym_matches_name(acronym: str | None, name: str | None) -> bool` (utils/text_utils.py:944-954).

#### 3 Pseudocode
Source: utils/text_utils.py:935-954.
1. `name_initials`: for each token matched by `_INITIALS_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&\-]*")` (line 924), skip stopwords, append the uppercased first letter.
2. `acronym_matches_name`: blank inputs → False; `letters ←` alphabetic characters of the uppercased acronym; return `letters == name_initials(name)` (exact equality, non-empty).

#### 4 Constants
`_INITIALS_STOPWORDS = {"of", "for", "the", "and", "in", "on", "at", "to", "a", "an", "de", "du", "des", "la", "le", "les", "&"}` (utils/text_utils.py:920-923).

#### 5 Complexity
One regex scan of the name.

#### 6 Worked example
tests/test_search_terms_fixes.py:99-107: `name_initials("National Institute of Standards and Technology") == "NIST"`; `name_initials("University of Florida") == "UF"`; `acronym_matches_name("NIST", "National Institute of Standards and Technology")` True; `acronym_matches_name("NBS", …)` False; `acronym_matches_name("UF", "University of Florida")` True; `acronym_matches_name("PHS", "Mass General Brigham")` False.

#### 7 Failure modes
Exact-equality means a genuine current acronym that is not a strict initials sequence (e.g. formed from word fragments) is rejected; `_extract_org_fields` then emits no acronym rather than a stale one (enrichment/tier1_ror.py:496-499).

---

### GLEIF/LEI registry lookup (`call_lei`, `_fuzzy_lookup`, `LEIClient` — enrichment/tier1_lei.py)

#### 1 Purpose
Resolves a company name to its official legal name and Legal Entity Identifier from the GLEIF API, using a precise filtered request first and a fuzzycompletions fallback, with mandatory name-verification and country guards (enrichment/tier1_lei.py:210-380).

#### 2 Inputs and outputs
Inputs: `name: str`, `country_code: str | None`, `base_url: str = "https://api.gleif.org/api/v1"`, `timeout: float = 15.0`, `max_retries: int = 2`, `threshold: float = 88.0` (enrichment/tier1_lei.py:210-217). `LEIClient` wires these from settings: `gleif_api_base`, `gleif_timeout_seconds`, `lei_max_retries`, `lei_name_match_threshold` (enrichment/tier1_lei.py:383-407).

Output (enrichment/tier1_lei.py:219-227): verified match `{"matched": True, "strategy": "exact"|"fuzzy", "confidence": "high"|"medium", "score", lei_id, legal_name, status, country}`; miss `{"matched": False, "strategy", "score"}`; API error `{"matched": False, "error": True}`. Never raises (line 227).

#### 3 Pseudocode
Source: enrichment/tier1_lei.py:229-312 and 315-380.
1. Blank name → `{"matched": False, "strategy": None, "score": 0.0}` (lines 229-230). Cache check on `(name.strip().lower(), country_code)` in the module-level `_lei_cache` (lines 232-234).
2. Open `httpx.AsyncClient(timeout=timeout, verify=resolve_tls_verify(), headers={"Accept": "application/vnd.api+json"})` (lines 251-254).
3. **Strategy A (precise):** GET `<base>/lei-records` with `filter[entity.legalName]=name`, `filter[entity.status]=ACTIVE`, `page[size]=10`, plus `filter[entity.legalAddress.country]=country_code` when known (lines 256-268), via `_get_json` (retrying). GLEIF's legalName filter is fulltext, not exact equality, so verification is mandatory even here (module docstring, lines 13-16).
4. `(fields, best_score) ← _best_verified_candidate(name, records, threshold, country_code)`; on success return (cached) `{"matched": True, "strategy": "exact", "confidence": "high", "score": best_score, **fields}` (lines 271-287).
5. **Strategy B (fuzzy):** `_fuzzy_lookup` (lines 294-302, 315-380):
   a. GET `<base>/fuzzycompletions?field=entity.legalName&q=name` (lines 327-332). No completions → None (a normal miss; lines 333-335).
   b. For the first 5 completions, read `relationships.lei-records.data.id`; skip missing/duplicate LEIs; GET `<records_url>/<lei>` to resolve the full record, skipping individual resolution failures (lines 337-358).
   c. `_best_verified_candidate` over the resolved records; None → miss; else return `{"matched": True, "strategy": "fuzzy", "confidence": "medium", "score": best_score, **fields}` (lines 360-380).
6. Neither strategy verified → cached `{"matched": False, "strategy": "fuzzy", "score": best_score}` (line 302; `best_score` is Strategy A's best).
7. Exceptions: `httpx.HTTPStatusError` → `{"matched": False, "error": True}` (not cached; lines 304-309); any other exception → same (lines 310-312).

#### 4 Constants
- `_GLEIF_ACCEPT = "application/vnd.api+json"` (enrichment/tier1_lei.py:55).
- Defaults: `base_url = "https://api.gleif.org/api/v1"`, `timeout = 15.0`, `max_retries = 2`, `threshold = 88.0` (lines 213-217). Settings equivalents: `GLEIF_API_BASE` default `"https://api.gleif.org/api/v1"` (config.py:186-188), `GLEIF_TIMEOUT_SECONDS` default `"15"` (config.py:189-191), `LEI_NAME_MATCH_THRESHOLD` default `"88"` (config.py:195-197), `LEI_MAX_RETRIES` default `"2"` (config.py:198-200), feature flag `LEI_LOOKUP_ENABLED` default True (config.py:183-185).
- `page[size] = "10"` on the precise request (line 259); fuzzy completion window `completions[:5]` (line 340).

#### 5 Complexity
Per uncached call: 1 precise request; on miss, 1 fuzzycompletions request plus up to 5 lei-record resolutions — at most 7 HTTP GETs, each independently retried up to `max_retries` times on transient failures (see `_get_json`).

#### 6 Worked example
tests/test_tier1_lei.py:181-196 (`test_exact_match`, real `call_lei` over `httpx.MockTransport`): input `("Pfizer AG", country_code="CH")`; the mocked `/lei-records` returns one record `{id: "549300ZZDOU0WGVYS169", legalName "PFIZER AG", status ACTIVE, country CH}` (record builder at tests/test_tier1_lei.py:39-51). Verification: `_name_match_score("Pfizer AG", "PFIZER AG") = 100.0` (case-folded; tests/test_tier1_lei.py:79-81) ≥ 88 → result `matched=True, strategy="exact", confidence="high", lei_id="549300ZZDOU0WGVYS169", legal_name="PFIZER AG"`.
Fuzzy fallback: tests/test_tier1_lei.py:216-240 — exact returns `[]`, fuzzycompletions returns one completion whose relationship resolves to the Pfizer record → `matched=True, strategy="fuzzy", confidence="medium"`. Wrong-country fuzzy candidate (US record for a CH request) → `matched=False, lei_id=None` (tests/test_tier1_lei.py:242-267). 500 → `{"matched": False, "error": True}` (tests/test_tier1_lei.py:295-304); `ConnectTimeout` → same (306-315).

#### 7 Failure modes
- GLEIF's fulltext legalName filter returns superstring entities ("Personalvorsorgestiftung der Pfizer AG in Liquidation" for "Pfizer AG"); the token_sort verification rejects them — a token_set metric would wrongly score the contained substring 100 (enrichment/tier1_lei.py:93-101; tests/test_tier1_lei.py:95-108, 198-214).
- `fuzzycompletions` cannot be country-filtered at the API, so a same-name wrong-country entity can be returned; the post-selection country guard rejects it (module docstring, enrichment/tier1_lei.py:28-33; tests/test_tier1_lei.py:242-267).
- GLEIF's name search is not typo-tolerant: "Bayr AG" misses on the raw name (comment, enrichment/orchestrator.py:2180-2183; mock keyed "bayer" not "bayr", tests/mocks/lei_mock.py:40-42); recovery happens only via the orchestrator's LLM-propose-then-re-verify path.
- API errors return an error dict and are not cached, so a later record with the same name retries (lines 304-312 vs the `_cache` calls at 286, 300, 302).

---

### LEI name verification and candidate selection (`_name_match_score`, `_best_verified_candidate` — enrichment/tier1_lei.py)

#### 1 Purpose
Scores an input name against a GLEIF candidate's legal name (0–100) and selects the best country-valid, threshold-clearing candidate, preferring ACTIVE entities (enrichment/tier1_lei.py:89-174).

#### 2 Inputs and outputs
`_name_match_score(query: str, legal_name: str) -> float` (enrichment/tier1_lei.py:89-111). `_best_verified_candidate(name, records: list[dict], threshold: float, country_code: str | None) -> tuple[dict | None, float]` — `(fields, score)` for the winner, or `(None, best_score)` (enrichment/tier1_lei.py:128-174). `_record_fields` extracts `{lei_id, legal_name, status, country}` from the JSON:API record (enrichment/tier1_lei.py:114-125).

#### 3 Pseudocode
`_name_match_score` (enrichment/tier1_lei.py:104-111):
1. Lowercase and strip both sides; either blank → 0.0.
2. `raw ← fuzz.token_sort_ratio(q, n)`.
3. `nq, nn ← _normalise_legal_name(query), _normalise_legal_name(legal_name)` — lowercase, tokenise on `[a-z0-9]+`, drop `_LEGAL_FORM_TOKENS` (enrichment/tier1_lei.py:72-77); `stripped ← fuzz.token_sort_ratio(nq, nn)` when both non-empty, else 0.0.
4. Return `max(raw, stripped)`. token_sort (not token_set) is deliberate: token_set scores any contained substring 100, which would wrongly accept "Personalvorsorgestiftung der Pfizer AG in Liquidation" for "Pfizer AG"; token_sort scores that pair ~21 (docstring, lines 93-101).

`_best_verified_candidate` (enrichment/tier1_lei.py:148-174):
1. `want_country ← country_code.strip().upper() or None`.
2. For each record: extract fields; skip if `lei_id` or `legal_name` missing; if `want_country` set and the candidate's legal-address country (uppercased) differs → skip (logged rejection).
3. `score ← _name_match_score(name, legal_name)`; track `best_score` across all candidates (including rejected-by-threshold ones); skip if `score < threshold`.
4. Rank by `(is_active, score)` where `is_active = 1` iff `status == "ACTIVE"`; keep the maximum.
5. Return `(best_fields, best_score)`.

#### 4 Constants
`_LEGAL_FORM_TOKENS = {"ag", "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "sa", "sas", "sarl", "nv", "bv", "plc", "spa", "srl", "ab", "oyj", "oy", "as", "kg", "kgaa", "se", "pty", "llp", "lp", "pllc", "pc", "aps", "kk", "ulc"}` (enrichment/tier1_lei.py:62-67) — deliberately only true legal forms, never descriptive words like "products"/"holdings"/"group" which distinguish a subsidiary from its parent (comment, lines 59-62). `_TOKEN_SPLIT_RE = re.compile(r"[a-z0-9]+")` (line 69). Threshold: caller-supplied, default 88.0 (line 217; config.py:195-197). Status literal `"ACTIVE"` (line 169).

#### 5 Complexity
Two `token_sort_ratio` computations per candidate; at most 10 candidates on the precise path (`page[size]=10`) and 5 on the fuzzy path.

#### 6 Worked example
tests/test_tier1_lei.py:83-135:
- `_best_verified_candidate("Pfizer AG", [Personalvorsorgestiftung-record, PFIZER-AG-record], 88.0)` → the PFIZER AG record wins with score 100.0 (lines 83-93); with only the wrong entity present, `(None, score < 88)` is returned (lines 95-108).
- Legal-form strip: `_name_match_score("Novartis", "NOVARTIS AG") ≥ 88.0` and `_name_match_score("Pfizer", "PFIZER INC.") ≥ 88.0` (lines 110-117), while the descriptive token keeps `_name_match_score("Pfizer", "PFIZER PRODUCTS INC.") < 88.0` (lines 119-125).
- ACTIVE preference: with an INACTIVE and an ACTIVE "PFIZER AG", the ACTIVE one (`549300ZZDOU0WGVYS169`) is selected (lines 127-135). Country guard cases at lines 137-174.

#### 7 Failure modes
A candidate that is the same entity under a differently-worded legal name (beyond suffix variation) scores below 88 and is rejected — the design prefers a miss (fall-through to the LLM path) over a fabricated match (module docstring, enrichment/tier1_lei.py:22-26).

---

### GLEIF retry wrapper (`_get_json` — enrichment/tier1_lei.py)

#### 1 Purpose
Performs a GET with JSON parse, retrying transient failures with exponential backoff and raising on final failure so callers can classify error vs. clean miss (enrichment/tier1_lei.py:177-207).

#### 2 Inputs and outputs
Inputs: `client: httpx.AsyncClient`, `url: str`, `params: dict | None`, `max_retries: int`. Output: parsed JSON dict; raises `httpx.TransportError`/`httpx.HTTPStatusError` on final failure (enrichment/tier1_lei.py:177-187).

#### 3 Pseudocode
Source: enrichment/tier1_lei.py:188-207.
1. Loop: GET; `raise_for_status()`; return `resp.json()`.
2. On `TransportError` or `HTTPStatusError`: `status ←` response status if any; `transient ← status is None or status >= 500` (4xx is not retried); increment attempt; if not transient or `attempt > max_retries` → re-raise.
3. `backoff ← 0.5 * (2 ** (attempt - 1))` seconds; sleep; retry.

#### 4 Constants
Backoff base 0.5 s, factor 2 (line 202); transient boundary `status >= 500` or no status (line 198); `max_retries` default 2 via `call_lei` (line 216).

#### 5 Complexity
At most `max_retries + 1` requests per URL; with defaults, backoffs of 0.5 s then 1.0 s.

#### 6 Worked example
⚠ NO FIXTURE COVERAGE for the retry loop itself — the HTTP-path tests all pass `max_retries=0` (e.g. tests/test_tier1_lei.py:191, 302, 313), so retries never fire; the terminal 500 and timeout behaviours (raise → error dict) are covered (tests/test_tier1_lei.py:295-315). A mock transport returning a 5xx then a 200 with `max_retries ≥ 1` would be needed to exercise a successful retry.

#### 7 Failure modes
A persistent 5xx consumes all retries and raises; `call_lei` converts this to `{"matched": False, "error": True}` (enrichment/tier1_lei.py:304-312). A 4xx raises immediately without retry (line 198-201).

---

### LEI orchestrator write path (`_run_lei_lookup` — enrichment/orchestrator.py)

#### 1 Purpose
Runs the GLEIF lookup for a company record and, on a verified match, writes the official legal name, LEI, classification and provenance into the result; on any miss or error it leaves the result untouched so the caller falls through to the LLM path (enrichment/orchestrator.py:1624-1696).

#### 2 Inputs and outputs
Inputs: `record`, mutable `result: dict`, `name: str`, `country_code: str | None` (enrichment/orchestrator.py:1624-1630). Output: `bool` — True only on a verified match. Writes on success: `name1_enriched ← legal_name` (when non-blank), `lei_id`, `record_type = "company"`, `tier_used = 1`, `source = "gleif"`, `confidence ← lei_res confidence` (default "high"), `enrichment_status = "enriched"`, use cases 2 and 3 (enrichment/orchestrator.py:1683-1696). `domain` is intentionally left as-is — GLEIF has no website field, and on a ROR company match ROR's domain must be preserved (docstring, lines 1640-1641).

#### 3 Pseudocode
Source: enrichment/orchestrator.py:1643-1696.
1. Feature flag off (`settings.lei_lookup_enabled` False) → False (lines 1643-1644). Blank name → False (1645-1646).
2. Increment `lei_counts["attempts"]`; call `lei_client.call(name, country_code)`; any raised exception → increment errors, warn, return False — GLEIF must never fail a record (lines 1648-1657).
3. `error` in result → increment errors, False (1671-1673). Not matched → increment misses, False (1674-1676).
4. Count `hits_exact` or `hits_fuzzy` by strategy (1678-1681); write the fields listed above; return True (1683-1696).

Call sites: (a) after a ROR match classified as company (enrichment/orchestrator.py:2057-2060) — LEI overwrites name1, ROR's domain/website preserved, research institutions never reach it (comment, 2050-2056); (b) on a non-research ROR miss, before the LLM (2152-2154); (c) typo re-verify of an LLM-proposed spelling variant (2198-2201).

#### 4 Constants
Feature flag `LEI_LOOKUP_ENABLED` default True (config.py:183-185). Telemetry counter keys `attempts/errors/misses/hits_exact/hits_fuzzy` (enrichment/orchestrator.py:1648, 1652, 1672, 1675, 1679-1681).

#### 5 Complexity
One `LEIClient.call` per invocation (≤ 7 HTTP GETs uncached); at most two invocations per record on the miss path (raw name, then LLM proposal).

#### 6 Worked example
tests/test_tier1_lei.py:337-347 (`test_pfizer_ag_verified_match`, orchestrator end-to-end with `MockLEIClient`): record `LEI_001`, `name1="Pfizer AG"`, city Zurich, country CH → `lei_id == "549300ZZDOU0WGVYS169"`, `record_type == "company"`, `confidence == "high"`, name1 contains "pfizer" (mock data at tests/mocks/lei_mock.py:22-30). Convergence: "Pfizer" without the legal form resolves to the same LEI (tests/test_tier1_lei.py:349-357). Protection: a ROR-matched research institution never triggers LEI (`call_count == 0`; tests/test_tier1_lei.py:401-414). Flag off → no LEI call (tests/test_tier1_lei.py:416-431). Error path: `ErrorCo` → `lei_id is None`, record has no error, `summary.lei_errors == 1` (tests/test_tier1_lei.py:386-399). Telemetry: one exact hit increments `lei_attempts`, `lei_hits_exact`, `tier1_lei_count` (tests/test_tier1_lei.py:433-442).

#### 7 Failure modes
A close-but-wrong GLEIF candidate is reported by the client as a miss (verification guard), so `_run_lei_lookup` returns False and the original name passes through un-fabricated (tests/test_tier1_lei.py:373-384). On the ROR-company path, a GLEIF miss leaves ROR's result (name from the identity-guard decision, ROR id/domain) intact.

---

### Company-name canonicalisation fallback (`run_company_canonical` — enrichment/company_canonical.py)

#### 1 Purpose
Single-LLM-call canonicalisation of a company `name1` (zero SerpAPI calls) that only accepts high-confidence, identity-preserving answers and falls through silently on any uncertainty (enrichment/company_canonical.py:1-5, 34-104).

#### 2 Inputs and outputs
Inputs: `record_id: str`, `name1: str`, `city/state/country: str | None`, `llm_client: OpenAIClient`, `street: str | None = None`, `postal_code: str | None = None` (enrichment/company_canonical.py:34-43). Output: `CompanyCanonicalResult(success: bool = False, name1_enriched: str | None = None, confidence: str = "none", proposed_name: str | None = None)` — `proposed_name` carries a high-confidence proposal the identity guard rejected, for optional GLEIF re-verification; `success` stays False in that case (enrichment/company_canonical.py:22-31).

Entry condition: reached only when `pp_name1` is non-blank, ROR missed, the name does not look like a research institution, and the GLEIF lookup on the raw name did not match (enrichment/orchestrator.py:2105-2178).

#### 3 Pseudocode
Source: enrichment/company_canonical.py:44-104.
1. Blank `name1` → default result (lines 45-46).
2. Format `COMPANY_CANONICAL_USER_PROMPT_TEMPLATE` with name1 and street/postal_code/city/state/country, substituting `"unknown"` for missing fields (lines 48-55).
3. `extraction ← llm_client.extract_json(COMPANY_CANONICAL_SYSTEM_PROMPT, user_prompt)`; any exception → log, return default (lines 57-63).
4. `official_name ← extraction["official_name"]`; `confidence ← (extraction["confidence"] or "low").lower()` (lines 65-66).
5. Reject: empty name (68-69); literal `{"null", "none", "n/a", "na"}` after lowercasing (70-72); `confidence != "high"` (73-78).
6. Identity guard: if `not canonical_preserves_identity(name1, cleaned)` → log warning, set `result.proposed_name ← cleaned`, return with `success = False` (lines 83-94). The orchestrator, not this function, decides whether the proposal is a spelling variant worth re-verifying (comment, tests/test_canonical_identity.py:92-95).
7. Accept: `success = True`, `name1_enriched = cleaned`, `confidence = "high"` (lines 96-104).

#### 4 Constants
- `COMPANY_CANONICAL_SYSTEM_PROMPT = "You normalise user-supplied company names to the canonical registered form the company uses publicly. Return valid JSON only."` (llm/prompts.py:221-224).
- `COMPANY_CANONICAL_USER_PROMPT_TEMPLATE` (llm/prompts.py:226-255): fields `name1/street/postal_code/city/state/country`; JSON schema `{"official_name": "str or null", "confidence": "high|medium|low", "reasoning": "str"}`; rules 1-5 verbatim include: "2. The full street address may identify a well-known corporate headquarters … NEVER replace the given name with a different company just because they share a building — many firms share an address, so the name must still match." and "4. Do not invent companies. Do not resolve acronyms you do not recognise." and "5. confidence=high means you are certain of the exact wording."
- Null-literal set `{"null", "none", "n/a", "na"}` (enrichment/company_canonical.py:71).

#### 5 Complexity
Exactly one LLM call per invocation; no search-API calls (module docstring, enrichment/company_canonical.py:3-4).

#### 6 Worked example
- Accept: with a fake LLM returning `{"official_name": "ISO Group, Inc.", "confidence": "high"}` for `name1="Iso Group Inc"`, the identity guard passes (reformatting) → `success is True`, `name1_enriched == "ISO Group, Inc."` (tests/test_canonical_identity.py:142-155).
- Reject entity swap: fake LLM returns `{"official_name": "CoStar Group", "confidence": "high"}` for `"Iso Group Inc"` → `success is False`, `name1_enriched is None`; `proposed_name` is surfaced but `canonical_is_spelling_variant("Iso Group Inc", proposed)` is False, so the orchestrator's gate blocks re-verification (tests/test_canonical_identity.py:76-95).
- Surface typo proposal: fake LLM returns `{"official_name": "Bayer AG", "confidence": "high"}` for `name1="Bayr AG"` → `success is False`, `proposed_name == "Bayer AG"`, spelling-variant gate True (tests/test_canonical_identity.py:121-139); the full recovery to `lei_id == "3157002JBAOA57BQAT84"` is exercised end-to-end at tests/test_orchestrator.py:115-146.

#### 7 Failure modes
- LLM hallucination of a different entity ("Iso Group Inc" → "CoStar Group") is blocked by the identity guard (enrichment/company_canonical.py:80-94).
- Medium/low-confidence answers and null-literal strings are dropped silently (lines 70-78), producing a passthrough (`source = "passthrough"`, `record_type = "unknown"`, enrichment/orchestrator.py:2237-2243).
- Even an accepted answer is flagged for manual review (`flag_reason = "LLM canonical company name — verify"`, enrichment/orchestrator.py:2228-2229) — LLM output is never treated as registry-grade.

---

### Non-determinism notes

**Live registries.** In production, `call_ror` queries `https://api.ror.org/v2/organizations` (default at enrichment/tier1_ror.py:571; config.py:171-173) and `call_lei` queries `https://api.gleif.org/api/v1` (enrichment/tier1_lei.py:213; config.py:186-188). Both registries evolve (new orgs, renamed entities, LEI status changes), so identical inputs can produce different results over time. ROR's affiliation endpoint additionally applies a server-side scorer whose behaviour is outside the codebase; the pipeline mitigates but does not remove this dependence via local re-scoring (enrichment/tier1_ror.py:643-653) and the country guard. The test suite substitutes deterministic mocks (`MockRORClient`, tests/mocks/ror_mock.py; `MockLEIClient`, tests/mocks/lei_mock.py) or `httpx.MockTransport` (tests/test_tier1_ror_country.py:52-59; tests/test_tier1_lei.py:61-73), so tests do not exercise live-registry variability.

**LLM call.** `run_company_canonical` performs one LLM call (enrichment/company_canonical.py:57-59); its output is model-dependent and non-deterministic across runs. Acceptance is gated deterministically (confidence gate, identity guard, spelling-variant gate plus GLEIF confirmation), so non-determinism is confined to which of {accept, reject, propose-for-re-verify} occurs, never to fabricating an unverified registry identifier.

**Timeouts and retries.** ROR: single 15.0 s client timeout, no retry of failed requests — any exception yields a cached miss (enrichment/tier1_ror.py:608, 838-846). GLEIF: per-request timeout `gleif_timeout_seconds` (default 15 s), transient failures (network/timeout/5xx) retried up to `lei_max_retries` (default 2) with backoff `0.5 * 2**(attempt-1)` s; 4xx never retried; final failure → uncached error dict (enrichment/tier1_lei.py:188-207, 304-312). Under transient outages a record's Tier-1 outcome therefore depends on network conditions at run time.

**Caching.** Three layers are relevant:
1. Module-level `_ror_cache: dict[(name_lower, country_code) → result]` (enrichment/tier1_ror.py:36) and `_lei_cache` with the same key shape (enrichment/tier1_lei.py:81). Both misses and matches are cached (ROR: enrichment/tier1_ror.py:593-596, 677, 831; LEI: 239-241, 286, 300, 302 — LEI error dicts are not cached). Both caches are cleared at the start of every batch by the orchestrator: `clear_ror_cache()` / `clear_lei_cache()` at enrichment/orchestrator.py:793-794 ("fresh cache per batch to avoid stale failures"), so results are deterministic within a batch but a transient failure early in a batch propagates to all same-key records of that batch.
2. `utils/cache.py` `BatchCache` declares a per-batch ROR store (`get_ror`/`set_ror`, utils/cache.py:75-81), but no production code calls these methods — a repository-wide search finds only the definitions (utils/cache.py:75, 79). ⚠ UNVERIFIED — the BatchCache ROR store appears to be dead code; the operative ROR cache is the module-level `_ror_cache` in tier1_ror.py. (`BatchCache`'s SERP and resolved-host stores are used by other stages; utils/cache.py:48-105.)
3. The ROR/LEI cache key omits city and state (enrichment/tier1_ror.py:566; enrichment/tier1_lei.py:232), so within a batch the first record's location context determines the cached answer for all same-name, same-country records.

**TLS trust.** Both clients pass `verify=resolve_tls_verify()` (enrichment/tier1_ror.py:607-609; enrichment/tier1_lei.py:251-252), which resolves to False when `LLM_SSL_VERIFY=false`, else a configured CA bundle (`AZURE_OPENAI_CA_BUNDLE` / `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`), else certifi (llm/openai_client.py:93-113). Environment configuration therefore changes whether registry calls succeed at all on TLS-inspecting networks (rationale comments at enrichment/tier1_ror.py:599-606 and enrichment/tier1_lei.py:244-250).

**Configuration-driven variability.** `ROR_CONFIDENCE_THRESHOLD` is read directly from the environment inside `call_ror` at call time (enrichment/tier1_ror.py:573), independently of the `Settings` object; `LEI_*` values are bound once at `LEIClient` construction from settings (enrichment/tier1_lei.py:387-392). Changing these env vars changes acceptance behaviour without code changes.


# Part D — Tier 2 selection and structured extraction; Tier 3 inference

Two procedures in this part have no reachable production call site, for different reasons.

**Tier 2A Mode B verification (§1) is unreachable by construction, not merely unobserved.** The orchestrator gate admits a record only when `pp_name2` is blank (`not name2_already_filled`, enrichment/orchestrator.py:2451-2457, with `name2_already_filled = bool(pp_name2 and pp_name2.strip())` at :2248), and then passes that same value as `name2=pp_name2` (:2473). The mode selector enters verification only when that value is **populated** — `mode = "2A_population" if is_blank(name2) else "2A_verification"` (enrichment/tier2a_contact.py:80). The gate's admission condition and the mode selector therefore read the same variable with opposite requirements, so the set of qualifying inputs is empty by construction: no record, however unusual, reaches Mode B. The second invocation site is closed the same way, passing `name2=None` explicitly (enrichment/orchestrator.py:1488-1502).

**Tier 2B (§2) has no gate at all** — no call site and no import exist (import block enrichment/orchestrator.py:37-59). Control falls from the Tier 2A block directly to Tier 3 (:2536-2555). The comment at :2347 describing fallthrough "through to existing tier 2 canonical / 2A / 2B / 3" is stale.

Both are documented below as implemented code, with their reachability stated in place. The consequence for the pipeline as a whole: **enrichment currently fills blank `Name 2` values only; it cannot detect or correct an incorrect existing `Name 2` via the contact page.** Three outputs exist in code and can never be produced today — `enrichment_status="verified"` (enrichment/tier2a_contact.py:459), `source="contact_lookup_corrected"` (:479), and the correction branch below `settings.fuzzy_match_threshold` that replaces a mismatched Name 2 with the page version (:470-479). The history of how this arose is recorded in `docs/thesis/09_DECISIONS.md` (D-1).

All paths are relative to `enrichment_api/`. Line numbers refer to the working tree at commit `515cc7c` (branch `diag/website-trace`).

---

### Tier 2A contact lookup (`run_tier2a` — enrichment/tier2a_contact.py)

#### 1 Purpose

Tier 2A locates the record's contact person on the institution's own website and extracts the person's official department, either populating a missing `Name 2` (Mode A) or verifying/correcting an existing one (Mode B) (enrichment/tier2a_contact.py:74-79).

#### 2 Inputs and outputs

Inputs: `record_id: str`, `contact: str`, `institution: str`, `domain: str | None`, `name2: str | None`, `name3: str | None`, plus injected `SearchClient`, `PageFetcher`, `OpenAIClient`, `BatchCache`, `Settings` (enrichment/tier2a_contact.py:61-73).

Output: `Tier2AResult` dataclass with fields `success: bool`, `name2_enriched: str | None`, `name3_enriched: str | None`, `title: str | None`, `mode: str | None` (`"2A_population"` or `"2A_verification"`), `name2_match: str` (`exact|partial|no_match|unknown`, default `"not_applicable"`), `name2_match_score: float`, `confidence: str` (`high|medium|low`, default `"none"`), `source_url: str | None`, `flag_for_review: bool` (default `True`), `flag_reason: str | None`, `enrichment_status: str` (default `"failed"`), `source: str` (default `"none"`) (enrichment/tier2a_contact.py:43-58).

#### 3 Pseudocode

Source: enrichment/tier2a_contact.py:80-107.
1. mode ← `"2A_population"` if `is_blank(name2)` else `"2A_verification"` (:80); `is_blank` is None-or-whitespace (utils/text_utils.py:18-20).
2. Build SERP queries (see step block below) (:86).
3. candidates ← `_search_and_rank(queries, …)` (:89). If empty → return default (failed) result (:90-92).
4. verified ← `_filter_candidates_by_name(candidates, contact)` (:100). If empty → return failed result (:105-106).

Query construction — enrichment/tier2a_contact.py:284-298:
5. clean ← `_clean_contact_name(contact)` or the raw contact: commas normalised to spaces, leading honorifics dropped while the first token is in `_HONORIFICS`, trailing tokens dropped while in `_NAME_SUFFIXES` (:260-281).
6. If `domain` is set → single query `f'"{clean}" site:{domain}'`; otherwise single query `f'"{clean}" "{institution}"'` (:294-298). Exactly one query is issued per record (docstring :287-292).

Search and ranking — enrichment/tier2a_contact.py:301-364:
7. For each query: return cached SERP result from `cache.get_serp(query)` if present, else `search_client.search(query, num_results=5)` and cache it (:325-331). De-duplicate by URL (:333-336).
8. Rank each result: base score = `score_search_result(url, snippet)` — +1 per people-page signal in the URL and +1 per signal in the snippet (utils/text_utils.py:76-87); +100 if the URL, normalised by replacing `[._\-/]+` with spaces, contains both the contact's first name and surname as whole words (:346-355); +20 if the result title starts with `"{first} {last}"` (:356-359).
9. Sort descending, keep top 3 (:362-364).

Name filter — enrichment/tier2a_contact.py:236-257:
10. Extract `(first, last)` from the cleaned contact (first and last remaining tokens; None if fewer than 2 tokens) (:195-207). If None (single-token name), pass all candidates through unfiltered (:248-250).
11. Keep a candidate only if the concatenation `url + title + snippet` contains both first and last as whole words after normalising `[._\-/]+` to spaces (case-insensitive), or contains the concatenated slug `firstlast`/`lastfirst` with spaces removed (:210-233, :252-257).

Fetch–extract loop — enrichment/tier2a_contact.py:109-184:
12. For each of the top 3 verified candidates: fetch `page_fetcher.fetch_page_content(url)`; on None/empty, continue to the next candidate (:109-113).
13. Build a page blob prepending authoritative elements: `URL host`, `URL path`, `Title`, `H1`, `Breadcrumb`, `Body` (:123-132).
14. Call `_extract_affiliation` (LLM; see LLM call). On exception, log and continue (:135-144).
15. If `extraction["person_found"]` is falsy → continue (:147-149). If `extraction["confidence"] == "low"` → continue (:151-154).
16. Record `source_url`, `confidence`; clean `official_dept`, `official_group`, `title` through `_clean_llm_string`, which maps None/empty/`{"null","none","n/a","na","nil","undefined","not recorded"}` to None (:26-40, :157-161).
17. Mode A → `_apply_mode_a` (:163-165); Mode B → `_apply_mode_b` with `settings.fuzzy_match_threshold` (:167-171).
18. If the applied result has `success` → return it (:173-181). Otherwise continue the loop; after all candidates, return the (failed) result (:183-184).

Mode A acceptance — enrichment/tier2a_contact.py:386-416:
19. If `official_dept` is empty → return unchanged (unsuccessful) (:397-398).
20. Else `success=True`, `name2_enriched=official_dept.strip()`, `name3_enriched=official_group.strip()` or None, `source="contact_lookup_found"`, `name2_match="not_applicable"` (:400-405).
21. LLM confidence `high` → `flag_for_review=False`, `enrichment_status="enriched"`; `medium` → `flag_for_review=True`, `flag_reason="Medium confidence — recommend review"`, status `"enriched"` (:407-414).

Mode B acceptance — enrichment/tier2a_contact.py:419-483:
22. If `official_dept` empty → return unsuccessful (:433-434). Else `success=True` (:436).
23. `effective_score` = max(LLM-reported `name2_match_score`, `fuzz.token_sort_ratio(existing_name2, official_dept)`) (:438-449).
24. If `effective_score >= fuzzy_threshold` (settings default 80): `name2_enriched=official_dept`; if score ≥ 95 → `name2_match="exact"`, status `"verified"`, no flag; else `"partial"`, status `"enriched"`, flag `"Partial match — confirm enriched Name 2"`; source `"contact_lookup_found"` (:451-469).
25. Else (< threshold): replace — `name2_enriched=official_dept`, `name2_match="no_match"`, status `"enriched"`, flag `"Name 2 corrected — did not match contact page affiliation"`, source `"contact_lookup_corrected"` (:470-479).
26. `name3_enriched=official_group` (or None) in both branches (:481-483).

Orchestrator entry conditions and scope filtering — enrichment/orchestrator.py:2443-2534:
27. Tier 2A runs only when `name2` is blank AND `record_type == "research_institution"` AND a non-blank contact exists AND the contact field does not hold multiple contacts AND an official `institution_domain` is known (:2451-2457).
28. On success, the answer is rejected if `is_granular_unit(name2_enriched)` (lab/group/centre/facility scope filter, UC 4) (:2490-2503); `is_granular_unit` is defined at utils/text_utils.py:410-474.
29. Otherwise the answer is re-canonicalised through `run_tier2_canonical`; a canonical replacement is adopted only if it is itself not granular (:2504-2528). `_apply_tier2a` transfers the fields into the result dict (sets `tier_used=2`, `tier2_mode`, `contact_used=True`; `name3_enriched` is copied only when the input record originally had a Name 3) (enrichment/orchestrator.py:667-691), UC 4 is appended (:2530-2531), and the pipeline returns.

**Mode B is unreachable by construction.** This is a contradiction between two conditions, not an unobserved input class. The gate admits a record only when `pp_name2` is blank — `not name2_already_filled`, where `name2_already_filled = bool(pp_name2 and pp_name2.strip())` (enrichment/orchestrator.py:2451-2457, :2248) — and then passes that same value as `name2=pp_name2` (:2473). The mode selector enters verification only when the value is populated: `mode = "2A_population" if is_blank(name2) else "2A_verification"` (enrichment/tier2a_contact.py:80). Because the gate requires blank and the selector requires non-blank of the same variable, the qualifying input set is empty; `run_tier2a` invoked from the main tier chain always selects Mode A. The second invocation site (person-affiliation path, enrichment/orchestrator.py:1488-1502) is closed the same way, passing `name2=None` explicitly. Mode B (`2A_verification`) is exercised only by direct calls in tests (tests/test_tier2a_verification.py:36-48).

Consequently three outputs defined in `_apply_mode_b` can never be produced by the running pipeline: `enrichment_status="verified"`, set only on a ≥95 match (enrichment/tier2a_contact.py:456-462); `source="contact_lookup_corrected"`, set only on the below-threshold branch (:470-479); and that correction branch itself, which replaces a mismatched Name 2 with the page version and flags "Name 2 corrected — did not match contact page affiliation" (:471-478). Enrichment therefore fills blank Name 2 values only and cannot detect or correct an incorrect existing Name 2 via the contact page. Reaching Mode B requires a source change — admitting records with a populated `name2` into the gate — not a different input. See `docs/thesis/09_DECISIONS.md` (D-1) for how the gate acquired this condition.

#### LLM call

Prompt location: `TIER2A_SYSTEM_PROMPT` (llm/prompts.py:49-52) and `TIER2A_USER_PROMPT_TEMPLATE` (llm/prompts.py:54-104), formatted at enrichment/tier2a_contact.py:376-383 with `contact`, `institution`, `name2` (or `"not recorded"`), `name3` (or `"not recorded"`), and the page blob as `page_text` (:138-141).

```text
TIER2A_SYSTEM_PROMPT = (
    "Data extraction assistant for MDM pipeline. "
    "Return valid JSON only. No markdown or code fences."
)

TIER2A_USER_PROMPT_TEMPLATE = (
    "Extract affiliation for: {contact}\n"
    "Institution: {institution}\n"
    "Existing Name 2: {name2}\n"
    "Existing Name 3: {name3}\n"
    "Page: {page_text}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "person_found": bool,\n'
    '  "official_dept": "str or null",\n'
    '  "official_group": "str or null",\n'
    '  "title": "str or null",\n'
    '  "name2_match": "exact|partial|no_match|unknown",\n'
    '  "name2_match_score": 0-100,\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. If the page is not about the named person, set "
    "person_found=false and all other fields to JSON null.\n"
    "2. For official_dept, pick the institution's canonical department "
    "name using ALL available signals in the input: URL host, URL "
    "path, page title, H1, breadcrumb, and body. An institution's "
    "URL host often includes a leading subdomain that abbreviates the "
    "department (e.g. a leading token before the main institution "
    "domain) — if so, infer the full canonical department name from "
    "that abbreviation.\n"
    "3. ALWAYS prefer the most specific academic unit available. "
    "Granularity ranking (most to least specific):\n"
    "     a) 'Department of X' or 'Division of X'  -- STRONGLY PREFERRED\n"
    "     b) 'Institute of X' or 'Center for X' (peer-level)\n"
    "     c) 'School of X', 'College of X', 'Faculty of X' (parent units -- FALLBACK ONLY)\n"
    "   If the page mentions BOTH a department and an enclosing school/"
    "college/faculty for this person (e.g. 'Department of Neuroscience, "
    "College of Medicine'), return the DEPARTMENT, never the college. "
    "A faculty member is always in a department within the college; "
    "the college alone is too coarse for downstream lookup. Only "
    "return a school/college/faculty when no department is "
    "identifiable on the page.\n"
    "4. Expand any subdomain abbreviation to the institution's actual "
    "canonical department wording.\n"
    "5. Reject generic role labels such as 'Research', 'Admin', "
    "'Staff', 'Faculty', 'Team', or 'Office'. They describe what the "
    "person does, not the unit they belong to. If the body contains "
    "only a role label, derive the unit from the URL host instead.\n"
    "6. Do not return a bare subject word alone ('Anesthesia', "
    "'Chemistry') and do not return a job title ('Professor of X').\n"
    "7. official_group may be set verbatim from the body when a "
    "specific research group, lab, or centre is clearly named. "
    "Otherwise null. Use JSON null, never the string 'null'."
)
```
(llm/prompts.py:49-104)

Evidence in context: URL host, URL path, title, H1, breadcrumb, and truncated body text of one fetched page (enrichment/tier2a_contact.py:125-132). Expected return: the JSON object above. Parsing: `OpenAIClient.extract_json` strips an optional ``` fence (regex llm/openai_client.py:72), `json.loads`, retries the whole call once on `JSONDecodeError`, then raises `ValueError` (llm/openai_client.py:271-292). Parse failure surfaces as an exception in `run_tier2a`, which logs and moves to the next candidate (enrichment/tier2a_contact.py:142-144).

#### 4 Constants

- `_NULLISH_STRINGS = {"null", "none", "n/a", "na", "nil", "undefined", "not recorded"}` (enrichment/tier2a_contact.py:26).
- `_HONORIFICS = {"dr", "dr.", "prof", "prof.", "professor", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "mx", "mx."}` (enrichment/tier2a_contact.py:187-190).
- `_NAME_SUFFIXES = {"md", "md.", "phd", "phd.", "msc", "msc.", "jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}` (enrichment/tier2a_contact.py:191-192).
- URL-slug normalisation regex `r"[._\-/]+"` (enrichment/tier2a_contact.py:221, :348-350).
- Rank bonuses: `+100` name-in-URL, `+20` title-starts-with-name (enrichment/tier2a_contact.py:355, :359).
- `URL_PEOPLE_SIGNALS = ["people", "faculty", "staff", "person", "profile", "directory", "team", "researcher", "member", "bio"]`; `SNIPPET_PEOPLE_SIGNALS = ["professor", "researcher", "scientist", "department", "phd", "dr.", "principal investigator"]` (utils/text_utils.py:65-73).
- Mode B thresholds: `fuzzy_match_threshold` default `int(os.getenv("FUZZY_MATCH_THRESHOLD", "80"))` (config.py:203-205); exact-match cutoff `95` (enrichment/tier2a_contact.py:456).
- SERP fan-out: `num_results=5` (enrichment/tier2a_contact.py:330); top-3 candidates ranked (:364); top-3 verified candidates fetched (:109).

#### 5 Complexity

One SERP query per record (enrichment/tier2a_contact.py:287-298), ≤ 5 results per query (:330). At most 3 candidates ranked into the shortlist (:364) and at most 3 page fetches + 3 LLM extraction calls per record (:109), with early exit on the first accepted candidate (:173-181). Filtering and ranking are O(candidates).

#### 6 Worked example

From tests/test_tier2a_population.py:34-54 (`test_population_success`): contact `"Dr. Jane Smith"`, institution `"Massachusetts Institute of Technology"`, domain `"mit.edu"`, `name2=None` → Mode A. Honorific stripping yields `"Jane Smith"`, so the single query is `"Jane Smith" site:mit.edu` (enrichment/tier2a_contact.py:294-296). The mock SERP returns two results for `mit.edu`: the profile `https://chemistry.mit.edu/profile/jane-smith/` and the faculty directory `https://web.mit.edu/directory/faculty/` (tests/mocks/serp_mock.py:17-28). Ranking: the profile URL contains both `jane` and `smith` in its slug (+100) plus the `profile` URL signal; the directory page has only generic signals (`directory`, `faculty`) — the profile ranks first (enrichment/tier2a_contact.py:340-364). The name filter retains the profile (first+last present in URL/title/snippet). The mock page fetcher returns the curated Jane Smith profile text (tests/mocks/page_mock.py:15-23), and the mock LLM returns `official_dept="Department of Chemistry"`, `official_group="NMR Spectroscopy Group"`, `title="Professor of Chemistry"`, `confidence="high"` for key `"jane smith|massachusetts institute of technology"` (tests/mocks/openai_mock.py:27-33). Mode A produces `success=True`, `mode="2A_population"`, `name2_enriched="Department of Chemistry"`, `source="contact_lookup_found"`, `confidence="high"` — asserted at tests/test_tier2a_population.py:49-54. Mode B is exercised by tests/test_tier2a_verification.py:34-52 (input `name2="Dept of AI"` corrected against the same fixture, `flag_for_review=True`).

#### 7 Failure modes

- No SERP results, or all candidates fail the first+surname filter → failed result, pipeline falls through (enrichment/tier2a_contact.py:90-92, :105-106; exercised at tests/test_tier2a_population.py:78-94).
- Page fetch returns None/empty for a candidate → next candidate; all failing → failure (:110-113; tests/test_tier2a_population.py:97-112).
- LLM exception (including JSON-parse failure after retry) → next candidate (:142-144).
- `person_found=false` or LLM `confidence="low"` → candidate skipped (:147-154).
- LLM returns literal `"null"`-style strings → neutralised by `_clean_llm_string` (:29-40).
- Orchestrator-side: granular answers rejected by scope filter (enrichment/orchestrator.py:2494-2503).

---

### Tier 2B department search (`run_tier2b` — enrichment/tier2b_dept.py)

#### 1 Purpose

Tier 2B searches the web (biased to the institution's official domain) for a page that names the department/division and has the LLM extract the official unit name from structured page elements, used when the contact-based path is not applicable (enrichment/tier2b_dept.py:1-11).

**Invocation status: no call site and no import exist.** `run_tier2b` is absent from the orchestrator's import block (enrichment/orchestrator.py:37-59), which imports `run_tier2_canonical` (:57), `run_tier2a` (:58) and `run_tier3` (:59) but no `tier2b_dept` symbol; a repository-wide search finds `run_tier2b` only in its own module (enrichment/tier2b_dept.py:50), its tests (tests/test_tier2b.py:13,36,58,78,97), and documentation. Unlike Tier 2A Mode B, Tier 2B is not blocked by a gate condition — there is simply nothing to gate. Where Tier 2B would sit, control falls from the Tier 2A block directly to Tier 3 (enrichment/orchestrator.py:2536-2555).

Two residues of the removed wiring remain in the source. The comment at enrichment/orchestrator.py:2347 still describes falling "through to existing tier 2 canonical / 2A / 2B / 3" and is stale. The batch-summary code still counts `tier2_mode == "2B"` (:2640-2641), but no code path sets that mode: `_apply_tier2a` assigns `result["tier2_mode"] = tier2a.mode` (:670), which is always `"2A_population"`. Tier 2B was wired into the orchestrator in the initial commit and unwired two days later; see `docs/thesis/09_DECISIONS.md` (D-1).

#### 2 Inputs and outputs

Inputs: `record_id: str`, `name1: str`, `name2: str | None`, `record_type: str`, `city: str | None`, `state: str | None`, `domain: str | None`, plus `SearchClient`, `PageFetcher`, `OpenAIClient`, `BatchCache`, `Settings` (enrichment/tier2b_dept.py:50-63).

Output: `Tier2BResult` with `success: bool`, `name2_enriched: str | None`, `name2_match: str` (default `"not_applicable"`), `name2_match_score: float`, `confidence: str` (default `"none"`), `source_url: str | None`, `flag_for_review: bool` (default `True`), `flag_reason: str | None`, `enrichment_status: str` (default `"failed"`), `source: str` (default `"none"`) (enrichment/tier2b_dept.py:31-43).

#### 3 Pseudocode

Query construction — enrichment/tier2b_dept.py:176-209:
1. `name2_expanded ← expand_abbreviations(name2)` when name2 is non-blank (:192); `expand_abbreviations` applies the `_ABBREV_MAP` regexes ("Dept" → "Department" etc., utils/text_utils.py:170-215).
2. If `record_type == "research_institution"`: queries are (a) `"{name1}" "{name2_expanded}"` with ` site:{domain}` appended when a domain exists, (b) `"{name1}" "{name2_expanded}" {city} {state}`, (c) when name2 non-blank, `"{name1}" {name2_expanded} {city}` (:195-204).
3. Otherwise (company): (a) `"{name1}" "{name2_expanded}" division {city} {state}`, (b) `"{name1}" {name2_expanded} official` (:205-207).

Search and ranking — enrichment/tier2b_dept.py:212-240:
4. Each query: SERP cache lookup, else `search(query, num_results=5)`; de-duplicate by URL (:222-233).
5. Rank: score 1 if `domain` occurs in the lowercased URL, else 0; stable sort descending — on-domain results first, no truncation at this stage (:235-240).

Extraction — enrichment/tier2b_dept.py:79-136:
6. If no candidates → failed result (:79-81).
7. For each of the first 3 candidates: fetch structured page content; skip on None/empty (:88-91); LLM-extract (see LLM call), skipping on exception, empty result, or empty `official_name` (:93-106); collect `{official_name, url, on_domain, raw}` where `on_domain = bool(domain and domain in url.lower())` (:108-113).
8. If no extraction succeeded → failed result (:115-117).
9. Deterministic ranking of collected extractions by tuple `(on_domain, fuzz.token_sort_ratio(name2, official) if name2 else 0, len(official), official.lower())`, sorted descending; the best is chosen (:119-136). The comment states this prevents the answer flipping between runs based on fetch order (:83-86).

Acceptance — enrichment/tier2b_dept.py:138-169:
10. `success=True`, `name2_enriched=official_name`, `source_url=chosen_url`, `source="dept_search"` (:138-141).
11. `name2_match`/`name2_match_score` come from the LLM extraction when present; otherwise recomputed as `fuzz.token_sort_ratio`, mapped `>=90 → "exact"`, `>=60 → "partial"`, else `"no_match"` (:143-154).
12. Confidence: `"medium"` with `flag_reason="Extracted by LLM from official domain page"` when the chosen URL is on the official domain; else `"low"` with `flag_reason="Extracted by LLM from non-official source"`. `flag_for_review=True` always; `enrichment_status="enriched"` (:156-163).

#### LLM call

Prompt location: `TIER2B_SYSTEM_PROMPT` (llm/prompts.py:110-113) and `TIER2B_USER_PROMPT_TEMPLATE` (llm/prompts.py:115-137), formatted at enrichment/tier2b_dept.py:259-266. The input `name2` is deliberately **not** passed to the LLM (comment :255-258).

```text
TIER2B_SYSTEM_PROMPT = (
    "Data extraction assistant for MDM pipeline. "
    "Return valid JSON only. No markdown or code fences."
)

TIER2B_USER_PROMPT_TEMPLATE = (
    "Extract the official department or division name that this "
    "page represents.\n"
    "Organisation: {name1}\n\n"
    "Authoritative page elements (use ONLY these as your source):\n"
    "URL path:   {url_path}\n"
    "Title tag:  {page_title}\n"
    "H1 heading: {h1}\n"
    "Breadcrumb: {breadcrumb}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "official_name": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. Extract ONLY from the four authoritative elements above. "
    "Do not invent, reformat, abbreviate, or expand anything.\n"
    "2. Copy the wording verbatim from whichever element clearly "
    "names the unit. Priority order: title tag > H1 > breadcrumb > "
    "URL path.\n"
    "3. If none of the elements clearly name a unit, return null."
)
```
(llm/prompts.py:110-137)

Evidence in context: URL path, title tag, H1, and breadcrumb only (missing elements substituted with `"(none)"`, enrichment/tier2b_dept.py:259-265) — no body text. Return: `official_name`, `confidence`, `reasoning`. Parsing: `extract_json` (fence-strip, `json.loads`, one retry, then `ValueError` — llm/openai_client.py:271-292); exceptions are caught per candidate and the loop continues (enrichment/tier2b_dept.py:97-99).

#### 4 Constants

- Query result fan-out `num_results=5` (enrichment/tier2b_dept.py:227); candidate fetch cap `candidates[:3]` (:88).
- Match-band cutoffs `90` (exact) and `60` (partial) (enrichment/tier2b_dept.py:151-153).
- Abbreviation expansion map `_ABBREV_MAP` (utils/text_utils.py:170-196), e.g. `r"\bDept\.?(?=\s|$)": "Department"` (:175).

#### 5 Complexity

Up to 3 SERP queries for research institutions, 2 for companies (enrichment/tier2b_dept.py:195-207) × 5 results each; all de-duplicated hits are ranked but only the first 3 are fetched, giving ≤ 3 page fetches and ≤ 3 LLM calls per record (:88).

#### 6 Worked example

From tests/test_tier2b.py:34-53 (`test_research_institution_dept_found`): `name1="Stanford University"`, `name2="Chemistry Dept"`, `record_type="research_institution"`, `domain="stanford.edu"`. `expand_abbreviations` rewrites `"Chemistry Dept"` → `"Chemistry Department"` (utils/text_utils.py:175), so the primary query is `"Stanford University" "Chemistry Department" site:stanford.edu` (enrichment/tier2b_dept.py:195-199). The mock SERP returns the Alice Johnson chemistry profile (`https://chemistry.stanford.edu/people/alice-johnson`) and `https://med.stanford.edu/about` (tests/mocks/serp_mock.py:36-47); both contain `stanford.edu`, so both rank on-domain. The mock fetcher has page text only for the chemistry profile (tests/mocks/page_mock.py:32-38) — the med.stanford.edu URL yields no page (tests/mocks/page_mock.py:100-101) — so exactly one extraction is collected. The mock LLM maps the Stanford fragment to `official_name="Department of Chemistry"`, `confidence="medium"` (tests/mocks/openai_mock.py:83, :178-204). Result: `success=True`, `name2_enriched="Department of Chemistry"` (non-None asserted), `source="dept_search"`, `flag_for_review=True` — asserted at tests/test_tier2b.py:50-53.

#### 7 Failure modes

- No SERP candidates → failure (enrichment/tier2b_dept.py:79-81; tests/test_tier2b.py:76-92).
- All fetches fail or every extraction returns empty `official_name` → failure and fall-through to Tier 3 (:115-117; module docstring :8-11).
- Off-domain-only evidence → accepted but demoted to `confidence="low"` and flagged (:159-162; tests/test_tier2b.py:95-112).
- LLM exception per candidate → logged and skipped (:97-99).

---

### Tier 2 canonicalisation (`run_tier2_canonical` — enrichment/tier2_canonical.py)

#### 1 Purpose

A single knowledge-only LLM call (no SERP, no page fetch) rewrites a user-supplied department name into the canonical form the institution itself uses, accepted only at high confidence (enrichment/tier2_canonical.py:1-11, :62).

#### 2 Inputs and outputs

Inputs: `record_id: str`, `institution: str`, `name2: str`, `llm_client: OpenAIClient` (enrichment/tier2_canonical.py:56-61). Output: `Tier2CanonicalResult` with `success: bool`, `name2_enriched: str | None`, `confidence: str` (default `"none"`), `reasoning: str` (:48-53).

#### 3 Pseudocode

Source: enrichment/tier2_canonical.py:56-122.
1. If `institution` or `name2` is falsy → return default (failed) result (:65-66).
2. Format the user prompt; call `extract_json`; any exception → failed result (:68-79).
3. `official_name`, `confidence` (lowercased, default `"low"`), `reasoning` read from the JSON (:81-83).
4. Empty `official_name` → failure (:85-87). Literal `"null"/"none"/"n/a"/"na"` → failure (:90-92).
5. `confidence != "high"` → rejected, failure (:94-101).
6. Unit-prefix downgrade guard: if `_is_prefix_downgrade(name2, cleaned)` → rejected, failure (:103-111). `_is_prefix_downgrade` matches `_UNIT_PREFIX_RE` against the original; it returns True exactly when the candidate equals (case-insensitively) the original with only its leading unit prefix removed (:38-45).
7. Else `success=True`, `name2_enriched=cleaned`, `confidence`, `reasoning` set (:113-116).

Orchestrator invocation — enrichment/orchestrator.py:2357-2441:
8. Runs for each of `name2`, `name3`, `name4` that is non-blank, not already resolved by the ROR child match, when `record_type ∈ {research_institution, company}` and `name1_enriched` exists (:2362-2374); DBA-marked values skip canonicalisation (:2380-2382).
9. A successful canonical answer is rejected if `is_granular_unit(...)` (UC 5 scope filter) — the original value is passed through instead (:2399-2407); otherwise it is adopted and UC 5 recorded (:2408-2411). Non-success → passthrough of the original (:2412-2414).
10. If canonicalisation ran on any field and the input had a name2, the tier short-circuits with `tier_used=2` and either `source="llm_canonical"`, `confidence="high"`, status `"enriched"`, flag `"LLM canonical form — verify"` (when a field changed) or `source="passthrough"`, `confidence="low"`, status `"unresolved"` (:2416-2441). It is also invoked to canonicalise Tier 2A's answer (:2508-2528).

#### LLM call

Prompt location: `TIER2_CANONICAL_SYSTEM_PROMPT` (llm/prompts.py:188-192) and `TIER2_CANONICAL_USER_PROMPT_TEMPLATE` (llm/prompts.py:194-214), formatted at enrichment/tier2_canonical.py:68-71.

```text
TIER2_CANONICAL_SYSTEM_PROMPT = (
    "You normalise user-supplied academic department names to the "
    "canonical wording the institution itself uses on its own website. "
    "Return valid JSON only. No markdown or code fences."
)

TIER2_CANONICAL_USER_PROMPT_TEMPLATE = (
    "Institution (verified): {institution}\n"
    "User-supplied department text: {name2}\n\n"
    "Return the official name of this unit as the institution "
    "documents it on its own website (e.g. 'Department of X', "
    "'Division of X', 'School of X', 'Institute of X').\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "official_name": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. Only return a name if you are confident it is the institution's "
    "actual canonical wording. When in doubt, return null.\n"
    "2. Do not invent units the institution does not have.\n"
    "3. Match the subject the user supplied — if they said 'Biochemistry', "
    "do not return 'Chemistry'.\n"
    "4. confidence=high means you are certain of the exact wording. "
    "Use medium or low if you are guessing the form."
)
```
(llm/prompts.py:188-214)

Evidence in context: only the verified institution name and the user-supplied department text — no web evidence (the model relies on parametric knowledge). Return: `official_name`, `confidence`, `reasoning`. Parsing: `extract_json` as above; exceptions caught and mapped to failure (enrichment/tier2_canonical.py:73-79).

#### 4 Constants

```python
_UNIT_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:Department|Dept\.?|Division|Div\.?|School|Faculty|"
    r"Institute|Center|Centre|College|Office)\s+of\s+",
    re.IGNORECASE,
)
```
(enrichment/tier2_canonical.py:31-35)

Null-string set `{"null", "none", "n/a", "na"}` (enrichment/tier2_canonical.py:91). Acceptance requires `confidence == "high"` (:96).

#### 5 Complexity

Exactly one LLM call per field canonicalised; zero SERP calls and zero page fetches (enrichment/tier2_canonical.py:62; module docstring :6). Per record the orchestrator loop can invoke it up to three times (name2, name3, name4 — enrichment/orchestrator.py:2367).

#### 6 Worked example

From tests/test_tier2_canonical_downgrade.py:44-52 (`test_downgrade_rejected_by_tier`): institution `"Scripps College"`, `name2="Department of Biology"`; a fake LLM returns `{"official_name": "Biology", "confidence": "high"}`. `_UNIT_PREFIX_RE` matches `"Department of "`, the remainder `"Biology"` equals the candidate case-insensitively, so `_is_prefix_downgrade` is True and the tier rejects: `success=False`, `name2_enriched=None` (asserted :51-52). The converse direction (test :55-63): `name2="Biology"`, LLM returns `"Department of Biology"` at high confidence → accepted, `name2_enriched="Department of Biology"`. Parametrised guard cases at :26-41 include `("Dept of Chemistry", "Chemistry") → True` and `("Department of Biology", "Department of Molecular Biology") → False`.

#### 7 Failure modes

- LLM exception, empty/nullish name, or confidence below `"high"` → failure; the caller passes the original value through (enrichment/tier2_canonical.py:73-101; orchestrator passthrough :2412-2414).
- Downgrade guard rejects prefix-stripping answers (:103-111).
- Orchestrator scope filter rejects granular canonical forms even at high confidence (enrichment/orchestrator.py:2399-2407).

---

### Lab resolver UC 13 (`run_lab_resolver` — enrichment/lab_resolver.py)

#### 1 Purpose

When `Name 2` names a granular research unit (lab, group, centre, core, facility, unit, program), this stage finds that unit's parent academic department on the institution's website so the parent can be promoted into `Name 2` (enrichment/lab_resolver.py:1-19).

#### 2 Inputs and outputs

Inputs: `record_id: str`, `institution: str`, `lab_name: str`, `domain: str | None`, plus `SearchClient`, `PageFetcher`, `OpenAIClient`, `BatchCache`, `Settings` (enrichment/lab_resolver.py:48-58). Output: `LabResolverResult` with `success: bool`, `parent_department: str | None`, `confidence: str` (default `"none"`), `source_url: str | None`, `reasoning: str` (:39-45).

#### 3 Pseudocode

Granularity detection (orchestrator gate) — enrichment/orchestrator.py:2273-2296:
1. Runs only when `record_type == "research_institution"`, `pp_name2` is non-blank, `is_granular_unit(pp_name2)` is True, and the ROR child match did not already resolve Name 2 to a non-granular name (:2284-2296).
2. `is_granular_unit` (utils/text_utils.py:410-474): after abbreviation expansion, (a) names starting `^(?:department|division|school|college|faculty)\s+(?:of|for)\s+` are never granular (:435-439); (b) suffix form `\b\S+\s+{word}\b\.?$` for word in `laboratory, laboratories, lab, facility, facilities, center, centre, core, group, unit, program, programme` → granular (:447-466); (c) prefix form `^{word}\s+(?:of|for)\s+` for the non-suffix-only words → granular (:470-472).

Resolution — enrichment/lab_resolver.py:61-170:
3. Empty `lab_name` or `institution` → failed result (:63-64).
4. Query: with domain, `f'"{lab_name}" department site:{domain}'`; without, `f'"{institution}" "{lab_name}" department'` (:71-77).
5. SERP cache lookup, else `search(query, num_results=5)` and cache (:79-84).
6. Candidate filter: with a domain, only results whose URL contains the domain; without, all results in SERP order (:86-91). Empty → failure (:93-98).
7. For the first 3 candidates: fetch structured page content (skip on None/empty); LLM-extract the parent department (see LLM call; skip on exception); skip empty or literal-null `parent_department`; collect `{parent_department, confidence (lowercased, default "low"), reasoning, url}` (:100-137).
8. No extractions → failure (:139-144).
9. Rank extractions by `(confidence_rank, len(parent_department), parent_department.lower())` descending, with `confidence_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}` (:146-157). Best becomes the result: `success=True` plus parent, confidence, url, reasoning (:159-163).

Orchestrator acceptance — enrichment/orchestrator.py:2297-2355:
10. On success: `name2_enriched = parent_department`; if Name 3 was empty, the original lab name moves to `name3_enriched`; `tier_used=2`, `source="dept_search"`, confidence copied from the resolver, status `"enriched"`, always flagged (reason depends on whether Name 3 was occupied), UC 13 recorded, then return (:2317-2342).
11. On failure: record stays in the pipeline with flag `"Lab/group detected in Name 2 but parent department could not be determined"` and falls through to Tier 2 canonical / 2A / 3 (:2343-2355).

#### LLM call

Prompt location: `LAB_PARENT_SYSTEM_PROMPT` (llm/prompts.py:143-148) and `LAB_PARENT_USER_PROMPT_TEMPLATE` (llm/prompts.py:150-182), formatted at enrichment/lab_resolver.py:107-114.

```text
LAB_PARENT_SYSTEM_PROMPT = (
    "Data extraction assistant for MDM pipeline. You identify the "
    "PARENT academic department of a research unit (lab, research "
    "group, centre, core, or facility) from a page on its "
    "institution's website. Return valid JSON only. No markdown."
)

LAB_PARENT_USER_PROMPT_TEMPLATE = (
    "Institution: {name1}\n"
    "Research unit (a lab/group/centre/facility): {lab_name}\n\n"
    "Authoritative page elements (use ONLY these as your source):\n"
    "URL path:   {url_path}\n"
    "Title tag:  {page_title}\n"
    "H1 heading: {h1}\n"
    "Breadcrumb: {breadcrumb}\n\n"
    "Return the parent academic department, division, school, "
    "college, faculty, or institute that this research unit belongs "
    "to.\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "parent_department": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. The parent must be an academic unit at department level or "
    "higher: 'Department of X', 'Division of X', 'School of X', "
    "'College of X', 'Faculty of X', or 'Institute of X'. NEVER "
    "another lab, group, centre, core, or facility.\n"
    "2. Look at: breadcrumb (often 'Home > Chemistry > Groups > NMR "
    "Lab' → parent is 'Department of Chemistry'), URL path "
    "(/chemistry/research/nmr-lab/ → 'Department of Chemistry'), and "
    "the title/H1 if they explicitly name the parent.\n"
    "3. confidence=high: parent is explicitly stated (in breadcrumb "
    "or title). confidence=medium: parent is implied by URL path. "
    "confidence=low: best guess.\n"
    "4. If you cannot identify a clear parent academic department, "
    "return null. Do not invent.\n"
    "5. Use JSON null, never the string 'null'."
)
```
(llm/prompts.py:143-182)

Evidence in context: institution name, lab name, and the four structured page elements (URL path, title, H1, breadcrumb) with `"(none)"` placeholders. Return: `parent_department`, `confidence`, `reasoning`. Parsing: `extract_json`; per-candidate exceptions are caught and skipped (enrichment/lab_resolver.py:115-123).

#### 4 Constants

- Query template literal word `department` appended to bias toward parent-unit pages (enrichment/lab_resolver.py:66-77).
- `num_results=5` (:83); candidate cap `[:3]` (:101).
- Literal-null set `{"null", "none", "n/a", "na"}` (:129).
- `confidence_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}` (:148).
- Granularity word lists: `granular_words = ["laboratory", "laboratories", "lab", "facility", "facilities", "center", "centre", "core"]`; `suffix_words = granular_words + ["group", "unit", "program", "programme"]` (utils/text_utils.py:447-461).

#### 5 Complexity

One SERP query per record, ≤ 5 results, ≤ 3 page fetches and ≤ 3 LLM calls; all successful extractions are collected before a single deterministic ranking pass (enrichment/lab_resolver.py:79-157).

#### 6 Worked example

From tests/test_lab_resolver.py:72-90 (`test_program_keyword_triggers_lookup`), an end-to-end orchestrator run: record `name1="Stanford University"`, `name2="Smith Research Program"`, `name3=None`. `is_granular_unit("Smith Research Program")` is True via the suffix rule for `program` (utils/text_utils.py:461-466; the same word list is directly asserted for 13 granular examples at tests/test_lab_resolver.py:24-40 and 7 non-granular ones at :42-53). The lab resolver's mock LLM maps the Stanford institution fragment to parent `"Department of Chemistry"` at `confidence="high"` (tests/mocks/openai_mock.py:206-225 with `_KNOWN_DEPARTMENTS` :83). The orchestrator promotes the parent and demotes the program: asserts `name2_enriched == "Department of Chemistry"`, `name3_enriched == "Smith Research Program"`, `flag_for_review is True`, `13 in use_cases_triggered` (tests/test_lab_resolver.py:86-90). Companion tests: department-level Name 2 skips UC 13 (:93-108); companies never trigger it (:111-124); an occupied Name 3 is preserved and the record flagged with a reason containing "Name 3" (:127-144).

#### 7 Failure modes

- No usable SERP results (or none on-domain when a domain is known) → failure, record flagged and falls through (enrichment/lab_resolver.py:93-98; enrichment/orchestrator.py:2343-2355).
- Fetch failures/LLM exceptions/null answers per candidate → skipped (:102-131).
- Off-domain fallback (no domain) trusts SERP ordering with the LLM as the only gate — lower precision by design (:74-77, :88-91).
- Occupied Name 3 → parent still adopted but lab name not demoted; flagged for manual verification (enrichment/orchestrator.py:2329-2334).

---

### Person affiliation Stage 2b (`run_person_affiliation` — enrichment/person_affiliation.py)

#### 1 Purpose

For records whose `Name 1` held only a person's name (moved to Contact), this stage proposes the person's current institution and department strictly from web-search snippets, never fabricating; the orchestrator then confirms the proposal against ROR (enrichment/person_affiliation.py:1-16).

#### 2 Inputs and outputs

Inputs (keyword-only): `contact: str`, `city`, `region`, `country`, `email: str | None`, `search_client: SearchClient`, `llm_client: OpenAIClient`, `settings: Settings` (enrichment/person_affiliation.py:99-109). Output: `PersonAffiliation` with `institution: str | None`, `department: str | None`, `confidence: str` (default `"low"`), `source_url: str | None`; institution None means nothing found (:44-50). Never raises (:113-115).

#### 3 Pseudocode

Query construction — enrichment/person_affiliation.py:73-96:
1. If the contact's e-mail domain exists and is not in `_FREEMAIL` → query `f'"{contact}" {dom}'` (strongest disambiguator) (:84-86).
2. If a location string (`city, region, country` joined by `", "`, :69-70) exists → query `f'"{contact}" {loc}'` (:87-88).
3. Always append `f'"{contact}" university OR institute OR company OR hospital'` (:89).
4. De-duplicate preserving order (:90-96).

Search — enrichment/person_affiliation.py:116-135:
5. Blank contact → empty result (:116-117).
6. Try queries in order; each `search(q, num_results=5)`; exceptions logged and the next query tried; the first query with non-empty hits wins and iteration stops (:120-131).
7. No results at all → empty `PersonAffiliation` (:133-135).

Proposal — enrichment/person_affiliation.py:137-178:
8. Build a snippet blob from the top 5 hits: `[i] title \n URL: url \n snippet` joined by blank lines (:137-140).
9. LLM call (see below); exception → empty result (:147-153).
10. Clean `institution` and `department` via `_clean` (None unless a non-empty string not in `{"null", "none", "n/a", "unknown", "not provided"}`, :53-59); normalise `confidence` to one of `high|medium|low`, defaulting `"low"` (:157-160).
11. No institution → `PersonAffiliation(confidence=…)` with institution None (:162-163). Otherwise return institution, department, confidence, and `source_url = results[0].url` (:173-178).

Orchestrator confirmation — enrichment/orchestrator.py:1877-1548:
12. Entry: preprocessed `name1` blank, contact present, and preprocessing marked `name1_was_person` (:1890-1894); the record **always short-circuits** here so Tier 3 never runs for person records (:1887-1889, comment; return at :1895-1897).
13. Only proposals with `confidence ∈ {high, medium}` are sent to ROR for confirmation in the record's country (:1445-1454).
14. On ROR confirmation: `name1_enriched` = ROR official name, `ror_id`, `tier_used=1`, `source="ROR"`, confidence capped at `"medium"` (:1461-1470); domain/website taken from ROR (:1476-1480). Department: if the input name2 was blank and a domain exists, a Tier 2A lookup on the confirmed domain is preferred, falling back to the web-proposed department (:1484-1511). Always flagged: `"Name 1 inferred from contact's web affiliation — verify (…)"` (:1513-1517).
15. On non-confirmation: contact kept, Name 1 left empty, flagged `"person in Name 1 — web affiliation '…' not confirmed by registry in …"` or `"…affiliation could not be resolved; manual lookup needed"` (:1526-1539).

#### LLM call

Prompt location: `PERSON_AFFILIATION_SYSTEM_PROMPT` (llm/prompts.py:381-406) and `PERSON_AFFILIATION_USER_PROMPT_TEMPLATE` (llm/prompts.py:408-417), formatted at enrichment/person_affiliation.py:141-145.

```text
PERSON_AFFILIATION_SYSTEM_PROMPT = (
    "You identify the CURRENT primary employer/institution and department of a "
    "named person from web-search result snippets.\n"
    "\n"
    "Rules:\n"
    "1. Ground every answer in the provided snippets. If the snippets do not "
    "clearly tie THIS person (by full name) to an institution, return "
    "institution=null. Never guess from the name alone.\n"
    "2. institution = the organisation the person works at now (university, "
    "research institute, hospital, or company) — its full proper name, not an "
    "acronym.\n"
    "3. department = the person's sub-unit/department if a snippet states it; "
    "otherwise null.\n"
    "4. Match the person by full name. If the snippets are about a different "
    "person with a similar name, return institution=null.\n"
    "5. confidence: 'high' when a snippet explicitly names this person AND their "
    "institution together; 'medium' when the tie is strongly implied by one "
    "snippet; 'low' when uncertain or conflicting.\n"
    "6. Never output an address, street, city, or postal code in institution or "
    "department. These are name fields, not address fields.\n"
    "7. No fabrication. Prefer institution=null over a plausible guess.\n"
    "\n"
    "Return ONLY JSON: "
    '{"institution": string|null, "department": string|null, '
    '"confidence": "high"|"medium"|"low"}.'
)

PERSON_AFFILIATION_USER_PROMPT_TEMPLATE = (
    "Person: {contact}\n"
    "Known location (from the record): {location}\n"
    "\n"
    "Web search results:\n"
    "{results}\n"
    "\n"
    "Identify this person's current institution and department per the rules. "
    "Return the JSON object only."
)
```
(llm/prompts.py:381-417)

Evidence in context: up to 5 SERP snippets (title, URL, snippet text) plus the record's location string — no fetched pages. Return: `institution`, `department`, `confidence`. Parsing: `extract_json`; exceptions caught → empty `PersonAffiliation` (enrichment/person_affiliation.py:147-153). Note the LLM is not called at all when there are no snippets (tests/test_person_affiliation.py:100-108 asserts `llm.calls == 0`).

#### 4 Constants

```python
_FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "qq.com",
    "163.com", "126.com", "yandex.com", "mail.com", "zoho.com",
}
```
(enrichment/person_affiliation.py:36-41)

Nullish-value set `{"null", "none", "n/a", "unknown", "not provided"}` (:57). Generic query suffix `university OR institute OR company OR hospital` (:89). `num_results=5` (:124); snippet cap `results[:5]` (:139).

#### 5 Complexity

At most 3 SERP queries (email-domain, location, generic), stopping at the first that returns hits; exactly one LLM call when hits exist; zero page fetches (enrichment/person_affiliation.py:120-153). The orchestrator adds one ROR confirmation call and optionally one Tier 2A run (enrichment/orchestrator.py:1448-1502).

#### 6 Worked example

From tests/test_person_affiliation.py:84-97 (`test_proposes_from_snippets`): contact `"Jane Smith"`, city `"Boston"`, region `"MA"`, country `"US"`, no email. Queries (asserted for the builder at :69-79): the location query contains `"Boston, MA, US"`; with `j@mit.edu` the first query is exactly `'"Jane Smith" mit.edu'`; a gmail address is skipped. The fake search returns one hit — title `"Prof. Jane Smith — MIT"`, url `"https://mit.edu/jane"`, snippet `"Jane Smith is a professor in the Department of Chemistry at the Massachusetts Institute of Technology."` (:53-56) — and the fake LLM returns `{"institution": "Massachusetts Institute of Technology", "department": "Department of Chemistry", "confidence": "high"}` (:86-90). Result: `institution="Massachusetts Institute of Technology"`, `department="Department of Chemistry"`, `confidence="high"` (asserted :95-97), with `source_url` set to the first hit's URL (enrichment/person_affiliation.py:177).

#### 7 Failure modes

- Blank contact, no SERP results, SERP exception on every query, LLM exception, or LLM `institution=null` → empty proposal, orchestrator flags for manual lookup (enrichment/person_affiliation.py:116-135, :147-163; tests/test_person_affiliation.py:100-130).
- Wrong-country or hallucinated institutions are rejected downstream by the ROR country-filtered confirmation (enrichment/orchestrator.py:1444-1459; module docstring enrichment/person_affiliation.py:9-16).
- Low-confidence proposals are never sent to ROR (:1445).

---

### Tier 3 LLM inference (`run_tier3` — enrichment/tier3_llm.py)

#### 1 Purpose

Tier 3 is the last-resort stage: a single ungrounded LLM call infers official organisation/department names from the raw record fields, always flagged for manual review (enrichment/tier3_llm.py:1, :78-84).

#### 2 Inputs and outputs

Inputs: `record_id`, `name1`, `name2`, `name3`, `contact`, `street`, `city`, `state`, `zip_code`, `country` (all `str | None` except `record_id`), and `llm_client: OpenAIClient` (enrichment/tier3_llm.py:65-77). Output: `Tier3Result` with `success: bool`, `name1_suggestion/name2_suggestion/name3_suggestion: str | None`, `confidence: str` (default `"none"`), `flag_for_review: bool` (default `True`), `flag_reason: str`, `enrichment_status: str` (default `"unresolved"`), `source: str = "LLM"` (:51-62).

#### 3 Pseudocode

Entry conditions (orchestrator): Tier 3 runs only after Tier 1, UC 13, Tier 2 canonical, and Tier 2A have all declined to short-circuit (enrichment/orchestrator.py:2536-2555). Person-only records never reach it (:1887-1897).

Source: enrichment/tier3_llm.py:85-162.
1. Format the user prompt with all fields (`"not recorded"` for missing names/contact, `""` for missing address parts) (:88-98).
2. LLM call; on exception: `confidence="none"`, `enrichment_status="failed"`, `flag_reason="LLM call failed"`, return (:100-107).
3. `confidence ← extraction.get("confidence", "low")` (:109-110).
4. If confidence ∈ {`high`, `medium`}: `success=True`; each suggestion stripped or None (:112-121); `enrichment_status="unresolved"`, `flag_for_review=True`, `flag_reason="LLM inference — requires verification"` (:122-124).
5. Address-in-name guard: for each of the three suggestions, `_is_address_like_name(value, street)` → set the suggestion to None and record the attribute (:131-135). The guard fires when (a) `_POSTAL_RE` matches, or (b) `_STREET_SUFFIX_RE` matches AND the value contains a digit, or (c) ≥ 50 % of the value's ≥3-letter alphabetic tokens also appear in the record's own street field (:32-48). If any were rejected: `flag_reason = "Tier 3 address-like name rejected (…) — original kept"` (:136-141).
6. Else (low confidence): `success=False`, originals not overwritten, `enrichment_status="unresolved"`, `flag_reason="LLM low confidence — manual review required"` (:154-160).

Orchestrator application and name2 drop:
7. `_apply_tier3` copies confidence/flags and, on success: `name1_suggestion` is adopted only if `canonical_preserves_identity(original, suggestion)` holds (an identity guard against entity swaps); `name2_suggestion` is adopted and marks `_name2_from_tier3=True`; `name3_suggestion` adopted (enrichment/orchestrator.py:694-722).
8. `finalise` drops a Tier-3-populated Name 2 when the input Name 2 was blank and confidence is not `"high"`: `name2_enriched=None`, flagged `"Tier 3 Name 2 guess dropped — input Name 2 blank and not high confidence"` (enrichment/orchestrator.py:388-409).
9. Post-Tier-3 rules in the main flow: `name2_enriched` forced to None when the record has no department signal (no name2 and no contact) (:2563-2567); when preprocessing emptied name2 (:2569-2581); and when it would echo `name1_enriched` case-insensitively (:2583-2595).

#### LLM call

Prompt location: `TIER3_SYSTEM_PROMPT` (llm/prompts.py:319-322) and `TIER3_USER_PROMPT_TEMPLATE` (llm/prompts.py:324-368), formatted at enrichment/tier3_llm.py:88-98.

```text
TIER3_SYSTEM_PROMPT = (
    "Help clean SAP customer master data for scientific "
    "instrument manufacturer. Return valid JSON only."
)

TIER3_USER_PROMPT_TEMPLATE = (
    "Infer official org and dept names from this record.\n"
    "Name 1: {name1}\n"
    "Name 2: {name2}\n"
    "Name 3: {name3}\n"
    "Contact: {contact}\n"
    "Address: {street}, {city}, {state} {zip}, {country}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "name1_suggestion": "str or null",\n'
    '  "name2_suggestion": "str or null",\n'
    '  "name3_suggestion": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str",\n'
    '  "requires_verification": true\n'
    "}}\n"
    "Rules:\n"
    "1. requires_verification is always true. Output is flagged for "
    "manual review either way.\n"
    "2. For name2_suggestion, when the institution is well-known "
    "(e.g. Harvard Medical School, University of Florida) and the "
    "contact's department can be plausibly inferred from public "
    "knowledge, propose a SPECIFIC department-level guess (e.g. "
    "'Department of Neuroscience', 'Department of Genetics'). Use "
    "confidence='medium' for educated guesses, 'low' for shots in "
    "the dark, 'high' only when you are certain. A best-guess "
    "department is more useful than null.\n"
    "3. Strongly prefer 'Department of X' or 'Division of X' over "
    "'School of X' / 'College of X' / 'Faculty of X'. A faculty "
    "member at 'College of Medicine' is always inside a specific "
    "department. Only fall back to school/college/faculty when no "
    "plausible department guess exists.\n"
    "4. Return null for name2_suggestion only when the institution "
    "is unknown to you or the contact has no inferrable affiliation.\n"
    "5. Do NOT return name2_suggestion equal to name1, and do NOT "
    "return a parent of name1 (e.g. name1='Harvard Medical School' "
    "must not yield name2='Harvard University').\n"
    "6. No fabrication of institutions or invented people.\n"
    "7. NEVER put address content in a name field. The street, house "
    "number, postal code, and city provided as context are address "
    "fields — name1_suggestion, name2_suggestion and name3_suggestion "
    "must never contain a street name, house number, postal/ZIP code, "
    "or a city/site string copied from the address. If you cannot infer "
    "a real organisation or department name, return null for that field."
)
```
(llm/prompts.py:319-368)

Evidence in context: only the record's own fields (names, contact, address) — no web evidence. Return: three suggestions, confidence, reasoning, `requires_verification`. Parsing: `extract_json` (fence-strip, retry once, then `ValueError`); the caller catches all exceptions and returns a failed result (enrichment/tier3_llm.py:100-107).

#### 4 Constants

```python
_STREET_SUFFIX_RE = re.compile(
    r"\b(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Pl|Place|Ter|Terrace|Sq|Square|Cir|"
    r"Circle|Platz|Stra(?:ße|sse)|Weg|Allee|Gasse)\b\.?",
    re.IGNORECASE,
)
_POSTAL_RE = re.compile(
    r"\b\d{5}(?:-\d{4})?\b|\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b",
    re.IGNORECASE,
)
```
(enrichment/tier3_llm.py:15-24)

Token-overlap threshold `0.5` over tokens matching `[A-Za-z]{3,}` (enrichment/tier3_llm.py:28-29, :46-47). Confidence acceptance set `("high", "medium")` (:112). Finalise-guard requirement `confidence == "high"` for a blank-input Name 2 (enrichment/orchestrator.py:392-398).

#### 5 Complexity

Exactly one LLM call per record; no SERP calls, no page fetches (enrichment/tier3_llm.py:100-101). Guard checks are O(len(suggestion)) regex scans over ≤ 3 suggestions (:131-134).

#### 6 Worked example

From tests/test_tier3_address_guard.py:47-62 (`test_address_name2_rejected`): a fake LLM returns `name2_suggestion="ASTER HOUSE, 2A University ROAD, BELFAST BT7 1NH"` at `confidence="medium"` for a Queen's University Belfast record with `street="ASTER HOUSE, 2A University ROAD"`, `zip_code="BT7 1NH"`. `_POSTAL_RE` matches the UK postcode `BT7 1NH` (and `_STREET_SUFFIX_RE` matches `ROAD` with digit `2A` present), so the suggestion is rejected: `r.name2_suggestion is None`, `flag_for_review is True`, and `flag_reason` contains `"address-like"` (asserted :60-62). Conversely `"Department of Physics"` for MIT at high confidence is kept (:65-77). The detector's parametrised cases (:27-42) include `("600 N Wolfe St", None) → True` and `("Department of Physics, 100 Science Dr", "100 Science Dr") → True` (street-overlap branch). The Name 2 drop in `finalise` is exercised at tests/test_tier3_address_guard.py:102-135: a Tier-3-sourced `"St. Louis Site"` with blank input Name 2 and medium confidence is dropped, while a high-confidence `"Department of Chemistry"` and a preprocessing-routed value survive.

#### 7 Failure modes

- LLM exception → `enrichment_status="failed"` result, still flagged (enrichment/tier3_llm.py:102-107).
- Low confidence → no overwrites, `unresolved` (:154-160; tests/test_tier3.py:81-99).
- Address-shaped suggestions silently reduced to None with an explanatory flag (:131-145).
- Identity-changing `name1_suggestion` rejected by the orchestrator's `canonical_preserves_identity` check (enrichment/orchestrator.py:703-717).
- Fabricated departments suppressed post-hoc by the no-dept-signal, name2-cleared, name2==name1, and blank-input-medium-confidence rules (enrichment/orchestrator.py:2563-2595, :388-409).

---

### Structured page extraction (`PageFetcher` — search/page_fetcher.py)

#### 1 Purpose

`PageFetcher` retrieves a URL and returns the authoritative structural slices of the page (title tag, first H1, breadcrumb, URL path, truncated body text) so LLM extraction operates on a small deterministic input rather than full prose (search/page_fetcher.py:1-8).

#### 2 Inputs and outputs

Constructor: `timeout: int = 10`, `max_chars: int = 1500` (search/page_fetcher.py:69); the orchestrator constructs it with `settings.page_fetch_timeout_seconds` (default `10`, config.py:211-213) and `settings.max_page_content_chars` (default `1500`, config.py:208-210) (enrichment/orchestrator.py:748-751).

- `fetch_page_content(url) → PageContent | None` (search/page_fetcher.py:85-93); `PageContent` fields: `url`, `url_path`, `page_title`, `h1`, `breadcrumb`, `body_text`; `is_empty()` is True when title, h1, breadcrumb and body are all empty (:52-63).
- `fetch_page_text(url) → str | None` — legacy flat body text (:73-83).
- `subdomain_exists(host, timeout=5) → bool` (:95-109).
- `resolve_final_url(url, timeout=5) → str | None` (:111-121).
- `fetch_outgoing_links(url, base_domain) → list[tuple[str, str]]` (:157-179).

#### 3 Pseudocode

`_sync_fetch_structured` — search/page_fetcher.py:217-258 (called via a thread executor from `fetch_page_content`, :85-93):
1. `requests.get(url, timeout=self._timeout, headers={"User-Agent": "BrukerMDM-Enrichment/1.0"})`; `raise_for_status()` (:218-223). Any exception propagates to `fetch_page_content`, is logged by `_log_fetch_failure` (HTTP errors and timeouts at DEBUG, others at WARNING, :28-49), and None is returned (:90-93).
2. Parse with BeautifulSoup `html.parser` (:225).
3. `page_title` = text of the `<title>` tag (before any element removal) (:227-229).
4. `breadcrumb` = `_extract_breadcrumb(soup)` — evaluated BEFORE `<nav>` removal because breadcrumbs usually live inside `<nav>` (:231-233).
5. `h1` = text of the first `<h1>` (:236-237).
6. `url_path` = `urlparse(url).path` (:239-241).
7. Remove all `REMOVE_TAGS` elements, extract remaining text with `" "` separator, collapse whitespace, truncate to `max_chars` with a trailing `…` (:243-249).
8. Return `PageContent` with `page_title`, `h1`, `breadcrumb` each capped at 300 characters (`[:300]`) (:251-258).

`_extract_breadcrumb` — search/page_fetcher.py:261-290:
9. Candidate elements: any element with `aria-label` matching `breadcrumb` (case-insensitive); elements with `role="navigation"` whose class list contains "breadcrumb"; any element whose class matches `breadcrumb` (:269-279).
10. For the first candidate yielding content: join the texts of its `li`/`a`/`span` descendants with `" › "`, else the element's own text (:281-288). Empty string if nothing matches (:290).

`resolve_final_url` — search/page_fetcher.py:111-142:
11. `requests.head(url, timeout, allow_redirects=True, verify=certifi.where())`; if the status is ≥ 400 (some servers reject HEAD), retry with a streamed GET and return `resp.url`; else return `resp.url`; None on any exception (:123-142). Purpose: follow a stale registry website to the live host (docstring example `dur.ac.uk → durham.ac.uk`, :112-114).

`subdomain_exists` — search/page_fetcher.py:95-109, :144-155:
12. HEAD `https://{host}/` with redirects; True iff `200 <= status < 400`; False on 4xx/5xx/timeout/DNS failure (:95-102, :144-155).

`fetch_outgoing_links` — search/page_fetcher.py:157-215:
13. GET the page (timeout `self._timeout`, `verify=certifi.where()`), parse, and for every `<a href>`: absolutise via `urljoin`, extract the host, strip a leading `www.`, skip links whose host equals the base domain (intra-site), de-duplicate by absolute URL, and emit `(anchor_text[:200], absolute_url)` (:181-215). Subdomains of the base and cross-domain links are both included (docstring :162-167). Empty list on fetch failure (:171-179).

#### 4 Constants

- `REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form", "iframe"}` (search/page_fetcher.py:25).
- Defaults `timeout=10`, `max_chars=1500` (:69); env-overridable via `PAGE_FETCH_TIMEOUT_SECONDS` / `MAX_PAGE_CONTENT_CHARS` (config.py:208-213).
- Probe timeouts default `5` s (`subdomain_exists`, `resolve_final_url`, :95, :111).
- Field caps: title/h1/breadcrumb 300 chars (:254-256); anchor text 200 chars (:213).
- User agent `"BrukerMDM-Enrichment/1.0"` (:127, :150, :190, :221).
- Breadcrumb joiner `" › "` (:284).
- TLS: probes and link fetches pass `verify=certifi.where()` to bypass bogus CA-bundle env vars (:128-129, :150-153, :184-192); ⚠ UNVERIFIED — `_sync_fetch_structured` itself does not pass `verify` (:218-222), so the main content fetch uses the requests default.

#### 5 Complexity

One HTTP GET per `fetch_page_content` call; parsing is O(page size); body text is bounded to `max_chars` (1500 default) characters after extraction (search/page_fetcher.py:248-249). `fetch_outgoing_links` iterates every anchor on one page (:198). Per record, Tier 2A/2B/lab-resolver each cap fetches at 3 pages (see those sections).

#### 6 Worked example

⚠ NO FIXTURE COVERAGE — no test in the repository exercises the real HTML paths (`_sync_fetch_structured`, `_extract_breadcrumb`, `_sync_fetch_outgoing_links`, `_sync_resolve_final_url`). All listed tests substitute `MockPageFetcher`, which overrides `fetch_page_content` to synthesise a `PageContent` whose title/H1 are the first sentence (up to 200 chars) of curated flat text and whose breadcrumb is always empty (tests/mocks/page_mock.py:76-119), and overrides `resolve_final_url` to return the URL unchanged (:82-85). Exercising the real extractor would require an HTML fixture containing a `<title>`, `<h1>`, and a breadcrumb element (e.g. `aria-label="breadcrumb"`), served over HTTP or injected below `requests.get`.

#### 7 Failure modes

- HTTP errors (403/404), DNS failures, and timeouts are expected: logged at DEBUG and converted to `None`/`[]`/`False` so the pipeline degrades gracefully (search/page_fetcher.py:28-49, :90-93, :107-109, :119-121, :175-179).
- Servers rejecting HEAD are retried with streamed GET in `resolve_final_url` only (:130-139); `subdomain_exists` treats a HEAD rejection ≥ 400 as non-existent (:155).
- Pages whose department identity lives only in `<title>` or the URL host are handled by Tier 2A's blob construction, not by the fetcher (enrichment/tier2a_contact.py:114-132).
- The mock used in tests never produces a breadcrumb, so breadcrumb-driven LLM behaviour is untested end-to-end (tests/mocks/page_mock.py:117).

---

### SERP clients (`SerpAPIClient` / `DuckDuckGoClient` — search/serpapi_client.py, search/duckduckgo_client.py)

(Supporting infrastructure for the procedures above.)

- Interface: `SearchClient.search(query, num_results=5) → list[SearchResult]`, where `SearchResult` has `title`, `url`, `snippet` (search/base.py:9-23).
- `SerpAPIClient._sync_search` calls SerpAPI's Google engine with `{"q": query, "num": num_results, "api_key": …, "engine": "google"}`, maps `organic_results[*].{title, link, snippet}`, truncates to `num_results` (search/serpapi_client.py:38-56); exceptions are logged and an empty list returned (:34-36).
- `DuckDuckGoClient._sync_search` uses `DDGS().text(query, max_results=num_results)`, mapping `title/href/body` (search/duckduckgo_client.py:31-42); same empty-list-on-exception behaviour (:27-29).
- Provider selection: SerpAPI when `SERPAPI_KEY` is configured, otherwise DuckDuckGo with a logged quality warning (enrichment/orchestrator.py:771-781; config.py:137-141).

---

### Non-determinism notes

- **SERP ranking volatility.** Both providers return live engine rankings; neither pins result order. Tier 2B mitigates this explicitly by collecting extractions from all top-3 candidates and ranking them with a deterministic tuple `(on_domain, name-similarity, length, alphabetical)` "so the answer does not flip between runs based on which page the fetcher happened to return first" (enrichment/tier2b_dept.py:83-131). The lab resolver applies the same pattern with a confidence-first key (enrichment/lab_resolver.py:146-157). Tier 2A instead relies on its deterministic +100 identity-slug bonus to dominate engine ordering (enrichment/tier2a_contact.py:340-364). Person affiliation takes SERP order as-is for its snippet blob and `source_url` (enrichment/person_affiliation.py:137-140, :177).
- **LLM temperature.** Every extraction call goes through `call_openai`, which hardcodes `temperature=0.0` and `response_format={"type": "json_object"}` (llm/openai_client.py:198-207). `OpenAIClient.extract_json` accepts a `temperature` keyword (llm/openai_client.py:257-263) but does not forward it to `call_openai` (:271-275), so 0.0 applies unconditionally. Temperature 0 reduces but does not eliminate output variation. The deployment defaults to `AZURE_OPENAI_DEPLOYMENT` or `"gpt-5.4"` (llm/openai_client.py:199, :233; config.py:157). Invalid JSON triggers exactly one retry — a second live call whose output may differ from the first (llm/openai_client.py:271-292).
- **Caching.** `SerpCache` is an in-memory, process-level query→results store keyed on the lowercased stripped query, shared across batches for the orchestrator's lifetime (utils/cache.py:26-45; created at enrichment/orchestrator.py:757-760). `BatchCache` is per-batch, with SERP reads falling through to (and writes propagating into) the shared cache (utils/cache.py:48-105). Consequence: within a process, the first live SERP response for a query is frozen and reused (enrichment/tier2a_contact.py:326-331; enrichment/tier2b_dept.py:223-228; enrichment/lab_resolver.py:79-84), so run-to-run variation appears only across process restarts. LLM responses are not cached. `run_person_affiliation` does not consult the SERP cache — its searches go straight to the client (enrichment/person_affiliation.py:122-124).
- **Fixture capture.** Test fixtures are hand-curated lookup tables, not recorded live traffic: SERP results in tests/mocks/serp_mock.py:16-108, page texts in tests/mocks/page_mock.py:14-73, and LLM outputs in tests/mocks/openai_mock.py:19-88. No VCR/cassette-style capture mechanism exists for the SERP, page-fetch, or LLM layers (a repository search for capture/cassette tooling in tests/ finds none for these stages). ⚠ UNVERIFIED — the curated LLM outputs are described in-file as "matching what gpt-4o would return" (tests/mocks/openai_mock.py:7); whether they reproduce actual model outputs was not verifiable from the repository.


# Part E — Website resolution and domain / department-domain selection

All paths are relative to the repository root `enrichment_api/`. Line numbers were verified against the working tree on branch `diag/website-trace` (2026-08-17).

Orchestration context (shared by all procedures below): after any tier finishes, every return path funnels through `Orchestrator._finalise_and_return`, which (i) runs website Paths B/C if no website is set, (ii) derives `domain` from `website_url` when no tier supplied one, (iii) runs the department-domain probe, (iv) runs the address stage, and (v) calls `finalise` (enrichment/orchestrator.py:1550-1573).

---

### Website Path A — ROR links adoption (`extract_website_from_ror` — enrichment/tier1_ror.py)

#### 1 Purpose
Adopts the organisation homepage recorded in the matched ROR record's `links[]` array as the authoritative `website_url`, bypassing SERP and LLM resolution entirely. The module docstring of the resolver states Path A "is handled inline by the orchestrator using `enrichment.tier1_ror.extract_website_from_ror` — no module here" (enrichment/website_resolver.py:15-17).

#### 2 Inputs and outputs
- Input: `ror_org: dict[str, Any]` — a ROR v2 organisation dict with a `links` list of `{type, value}` entries (enrichment/tier1_ror.py:427-434).
- Output: `str | None` — the first `links[]` entry whose `type == 'website'` and whose `value` is truthy; `None` otherwise (enrichment/tier1_ror.py:434-437).
- Adoption site: on a Tier-1 ROR parent match the orchestrator writes `result["website_url"] = ror_website` when `ror_parent.get("website")` is truthy (enrichment/orchestrator.py:2045-2048). The `website` key is populated by `_extract_org_fields`, which calls `extract_website_from_ror(org)` and also derives `domain = extract_domain(website) if website else None` (enrichment/tier1_ror.py:501-502, 527-528). `result["domain"]` is set from `ror_parent.get("domain")` just above (enrichment/orchestrator.py:2041-2043).
- A second adoption site exists on the person-affiliation route: when a web-proposed institution is ROR-confirmed, `result["website_url"] = confirmed.get("website")` (enrichment/orchestrator.py:1479-1480) and `result["domain"] = confirmed.get("domain")` (enrichment/orchestrator.py:1476-1478).

#### 3 Pseudocode
Source: enrichment/tier1_ror.py:427-437.
1. For each `link` in `ror_org.get("links", []) or []`:
   1. If `link.get("type") == "website"` and `link.get("value")` is truthy → return `link["value"]` (first match wins).
2. Return `None`.

Orchestrator adoption (source: enrichment/orchestrator.py:2041-2048):
1. `institution_domain = ror_parent.get("domain")`; if truthy → `result["domain"] = institution_domain`.
2. `ror_website = ror_parent.get("website")`; if truthy → `result["website_url"] = ror_website`.
3. Because `_maybe_resolve_website_bc` returns immediately when `result.get("website_url")` is already set (enrichment/orchestrator.py:876-877), a Path-A website suppresses Paths B and C.

#### 4 Constants
None. The only literal is the link type string `"website"` (enrichment/tier1_ror.py:435).

#### 5 Complexity
One linear pass over `links[]` (typically ≤ ~5 entries in ROR records); no network calls — the ROR record is already in hand.

#### 6 Worked example
From tests/test_website_resolver.py:37-44: input org `{"links": [{"type": "website", "value": "https://www.stanford.edu"}, {"type": "wikipedia", "value": "https://en.wikipedia.org/wiki/Stanford_University"}]}` → returns `"https://www.stanford.edu"` (first `website`-typed link). Order-independence: tests/test_website_resolver.py:46-53 places the Wikipedia link first and still yields `"http://www.ufl.edu"`. End-to-end: record `name1="Stanford University"` through the mock orchestrator produces `website_url == "https://www.stanford.edu"` with no website-related review flag (tests/test_website_resolver.py:344-353).

#### 7 Failure modes
- `links` absent or empty → `None`; a `website`-typed link without a `value` → `None` (tests/test_website_resolver.py:55-61; enrichment/tier1_ror.py:434-437).
- A stale ROR website (dead redirecting host) is still adopted verbatim; staleness is only compensated later by the department-probe base resolution, which follows the redirect chain once (enrichment/orchestrator.py:926-961). The output `website_url` itself is not corrected.

---

### Website Path B — SERP-based resolution (`select_website_from_serp`, `resolve_website_via_serp`, `_build_serp_query` — enrichment/website_resolver.py)

#### 1 Purpose
Finds the official website for any record type that did not obtain one from ROR, by issuing a web search and selecting the best candidate under host-match and blocklist rules (enrichment/website_resolver.py:1-13, 440-457). Invoked by `Orchestrator._maybe_resolve_website_bc` only when `website_url` is empty and a preprocessed Name 1 (`_pp_name1`) exists (enrichment/orchestrator.py:876-895).

#### 2 Inputs and outputs
- `resolve_website_via_serp(record_id: str, name1: str, city: str|None, state: str|None, country: str|None, record_type: str|None, search_client: SearchClient, cache: BatchCache, *, prefetched_results: list[SearchResult]|None = None, trace: bool = False) -> WebsiteResolution` (enrichment/website_resolver.py:440-452).
- `select_website_from_serp(name1: str, results: list[SearchResult], record_type: str|None = None) -> WebsiteResolution` (enrichment/website_resolver.py:350-354).
- Output type `WebsiteResolution(url: str|None = None, confidence: str = "none", source: str = "none")`; confidence semantics: `high` → write with no flag, `low` → write plus review flag, `none` → leave empty (enrichment/website_resolver.py:63-75). The orchestrator applies exactly these semantics (enrichment/orchestrator.py:896-904).
- ⚠ UNVERIFIED — the `prefetched_results` reuse branch (enrichment/website_resolver.py:472-484) has a unit test (tests/test_website_resolver.py:268-283) but no caller in `orchestrator.py` passes `prefetched_results` (grep over enrichment/orchestrator.py finds no use), so the "orchestrator already ran a Tier 2B search" reuse described in the docstring (enrichment/website_resolver.py:459-461) is not exercised by the current orchestrator.

#### 3 Pseudocode

**Query construction** — source: enrichment/website_resolver.py:406-437.
1. `base = f'"{name1}" official website'` when `quoted=True` (default), else `f"{name1} official website"` (line 426).
2. If `record_type == "research_institution"`: append ` {country}` when country is non-blank, else return `base` (lines 427-430).
3. Otherwise (company / unknown): `geo = " ".join` of non-blank `city`, `state`; if `geo` → `f"{base} {geo}"`; elif country non-blank → `f"{base} {country}"`; else `base` (lines 431-437).

**Driver** — source: enrichment/website_resolver.py:440-530.
1. `num_results = 10` (line 468). Guard: blank `name1` → return empty `WebsiteResolution()` (lines 469-470).
2. If `prefetched_results is not None` → select from them directly, emit trace if enabled, return (lines 472-484). No SERP call.
3. Inner `_run(query, attempt)` (lines 486-515): check `cache.get_serp(query)`; on miss call `search_client.search(query, num_results=10)`; on exception log, emit an error trace record, and return empty `WebsiteResolution()` (lines 491-502); on success `cache.set_serp(query, results)` then `select_website_from_serp(...)` (lines 503-504); emit trace record if `trace` (lines 509-514).
4. Build the quoted query and run it (lines 517-518). If a URL was chosen → return (lines 519-520).
5. §8 unquoted retry: build the unquoted query; if it differs from the quoted one, run it once and return its result; otherwise return the (empty) quoted outcome (lines 522-530). One retry maximum; only on a first-pass miss.

**Selection** — source: enrichment/website_resolver.py:350-403.
1. `valid` = results where `sr.url` matches `_URL_RE` (`^https?://`, case-insensitive; line 60), is not blacklisted (`_is_blacklisted`, lines 82-84: registrable domain equals or ends with `"." + bad` for any `DOMAIN_BLACKLIST` entry), and passes `_name_overlap` (lines 98-101: any significant token — lowercased alphanumeric runs of length ≥ 4 from name1 (lines 94-95) — is a substring of `"{url} {title}"` lowercased) (lines 368-373).
2. If `valid` is empty → return empty `WebsiteResolution()` (lines 374-375).
3. Rank each valid candidate 0/1/2 (`_rank`, lines 381-384):
   - `0` if `_has_host_match(name1, sr.url, record_type)` is False;
   - else `1` if `_domain_introduces_foreign_brand(name1, sr.url)`;
   - else `2`.
   `_has_host_match` (lines 150-157): True when a *distinctive* name token (significant tokens minus `_GENERIC_NAME_TOKENS`; lines 116-118) is a substring of the registrable domain (`_distinctive_in_host`, lines 129-136), OR — research institutions only — the host's first label equals the institution's initials (`_acronym_in_host`, lines 139-147, delegating to `acronym_matches_name`, utils/text_utils.py:944-954, which compares uppercase letters of the label to `name_initials(name)`).
   `_domain_introduces_foreign_brand` (lines 160-185): split the host's first label on `[-_]`; a single concatenated part is treated as clean (lines 177-178); otherwise any part of length ≥ 4 that neither prefixes nor is prefixed by a significant name token marks the domain as a sub-brand (lines 179-185).
4. `best = max(valid, key=_rank)` — Python's first-max preserves SERP order on ties (line 386).
5. **Rank-0 rejection**: if `best_rank == 0` (name overlap exists only in the title, never in the host) → return empty `WebsiteResolution()`, deferring to Path C (lines 387-392).
6. Confidence: research institutions — `high` iff `best_rank == 2` AND `_tld(best.url) in _OFFICIAL_TLDS` (§7c: TLD alone never grants high; lines 394-396); companies/unknown — `high` iff `best_rank == 2` (lines 397-398). `_tld` takes the last dot-separated part of the registrable domain (lines 87-91).
7. Return `WebsiteResolution(url=_root_url(best.url), confidence="high"|"low", source="serp")`; `_root_url` reduces to `scheme://netloc` (lines 188-196, 399-403).

#### 4 Constants
- `DOMAIN_BLACKLIST` (enrichment/website_resolver.py:49-54), verbatim:
  ```python
  DOMAIN_BLACKLIST: frozenset[str] = frozenset({
      "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com",
      "x.com", "instagram.com", "youtube.com", "ratemyprofessors.com",
      "glassdoor.com", "yelp.com", "bbb.org", "crunchbase.com",
      "bloomberg.com", "indeed.com", "ziprecruiter.com",
  })
  ```
- `_OFFICIAL_TLDS: frozenset[str] = frozenset({"edu", "gov", "org"})` (enrichment/website_resolver.py:58).
- `_URL_RE = re.compile(r"^https?://", re.IGNORECASE)` (enrichment/website_resolver.py:60).
- Significant-token threshold: `len(t) >= 4` in `_significant_tokens` (enrichment/website_resolver.py:95).
- `_GENERIC_NAME_TOKENS` (enrichment/website_resolver.py:107-113), verbatim:
  ```python
  _GENERIC_NAME_TOKENS: frozenset[str] = frozenset({
      "research", "therapeutics", "diagnostics", "medical", "instruments",
      "sciences", "science", "laboratories", "laboratory", "labs",
      "technologies", "technology", "solutions", "systems", "group", "holdings",
      "international", "global", "pharma", "pharmaceutical", "bio", "biotech",
      "health", "healthcare", "services", "consulting", "partners", "associates",
  })
  ```
- Foreign-brand part-length floor: `len(part) < 4 → continue` (enrichment/website_resolver.py:181-182).
- `num_results = 10` (enrichment/website_resolver.py:468).

#### 5 Complexity
At most 2 SERP calls per record (quoted + one unquoted retry; enrichment/website_resolver.py:517-530), each returning ≤ 10 results (line 468). Filtering and ranking are linear passes over ≤ 10 candidates; each rank evaluation scans the significant-token set (bounded by the token count of name1). No pages are fetched in Path B. Per-batch and process-level SERP caches short-circuit repeat queries (utils/cache.py:85-105).

#### 6 Worked example
Sub-brand preference, from tests/test_website_resolver.py:154-166 ("Siemens AG", record_type `company`): results = [`siemens-healthineers.com` (position 1), `siemens.com` (position 2)]. Both pass URL-shape, blacklist, and overlap (token `siemens`, ≥ 4 chars). Ranking: `siemens-healthineers.com` — distinctive token `siemens` in host → host match; label parts `["siemens", "healthineers"]`, `healthineers` (≥ 4 chars) shares no prefix with `{siemens}` → foreign brand → rank 1. `siemens.com` — single-part label → clean → rank 2. `max` picks rank 2 → `https://www.siemens.com`, company + rank 2 → `high` (asserted lines 165-166). The single-candidate variant returns the sub-brand at `low` (tests/test_website_resolver.py:168-178). Rank-0 rejection: "Bayfront Research" with only `scup.org` (title-only overlap, host has no distinctive token and `scup` ≠ initials `BR`) returns `url is None` (tests/test_website_resolver.py:498-506). Acronym rule: `fit.edu` for "Florida Institute of Technology" resolves `high` (tests/test_website_resolver.py:517-526). Unquoted retry: quoted query for "Atlantic Testing Labs" returns nothing, unquoted retry returns `atlantictesting.com`; exactly 2 calls, first quoted, second not (tests/test_website_resolver.py:552-567).

#### 7 Failure modes
- SERP client exception → logged, empty resolution, Path C fires (enrichment/website_resolver.py:491-502).
- All candidates blacklisted / non-overlapping / rank 0 → empty resolution → Path C (lines 374-375, 387-392).
- Retrieval miss: if the official site never appears in the 10 results, Path B cannot recover it — observed for two of three failing companies in the diagnostic run recorded in `Website_Trace_Findings.pdf` (summarised at docs/thesis/02_ARCHITECTURE.md:512-515).
- Generic-token-only host match is rejected (e.g. `researchgate.net` for "Precision Research"; tests/test_website_resolver.py:489-496), preventing §7a false validation but also blocking legitimately generic-named organisations from Path B.
- The stale-cache hazard: a poisoned/empty cached result for the same normalised query is reused for the whole process lifetime (utils/cache.py:26-45).

---

### Website Path C — LLM inference (`infer_website_via_llm` — enrichment/website_resolver.py)

#### 1 Purpose
Last-resort website inference by a single LLM call when Path B returned nothing usable; results are never trusted — the orchestrator always flags them for manual review (enrichment/website_resolver.py:550-565; enrichment/orchestrator.py:906-921).

#### 2 Inputs and outputs
- `infer_website_via_llm(record_id: str, name1: str, city: str|None, state: str|None, country: str|None, llm_client: OpenAIClient, *, trace: bool = False) -> WebsiteResolution` (enrichment/website_resolver.py:550-559).
- Output: `WebsiteResolution(url=raw, confidence="low", source="llm")` on success — confidence is unconditionally `"low"` (enrichment/website_resolver.py:633); empty `WebsiteResolution()` on any guard failure.
- Entry condition: called only after `serp_res.url` is falsy inside `_maybe_resolve_website_bc` (enrichment/orchestrator.py:896-915); on success the orchestrator writes `website_url` and calls `_flag_website_review(result, "Website inferred by LLM — verify")` (enrichment/orchestrator.py:916-921), which sets `flag_for_review=True` and appends to any existing `flag_reason` (enrichment/orchestrator.py:619-628).

#### 3 Pseudocode
Source: enrichment/website_resolver.py:571-633.
1. Guard: blank `name1` → empty resolution (lines 571-572).
2. Format the user prompt from the template with `name1` and `city/state/country` each defaulting to the literal `"(unknown)"` (lines 574-579).
3. `payload = await llm_client.extract_json(WEBSITE_INFERENCE_SYSTEM_PROMPT, user_prompt)`; on exception → log, emit trace with `llm_error`, return empty resolution (lines 597-607).
4. Parse: `raw_response = payload.get("website_url") if isinstance(payload, dict) else None` (line 609). The `confidence` field the prompt requests is never read.
5. Sentinel guard: if `raw` is a string, strip it; if `raw.lower() in {"", "null", "none", "unknown", "n/a", "na"}` → treat as `None` (lines 611-616).
6. URL-shape guard `_looks_like_url` (lines 537-547): must be a string, match `_URL_RE` (`^https?://`), parse with `urlparse`, and have a netloc containing a dot. Failure → log "no usable URL", emit trace with `url_shape_ok=False`, return empty resolution (lines 618-625).
7. Success → log, emit trace, return `WebsiteResolution(url=raw, confidence="low", source="llm")` (lines 627-633).

#### 4 Constants
Prompts, verbatim from llm/prompts.py:262-287:
```python
WEBSITE_INFERENCE_SYSTEM_PROMPT = (
    "You return the official corporate website URL for a company. "
    "Return valid JSON only. Never guess or hallucinate URLs."
)

WEBSITE_INFERENCE_USER_PROMPT_TEMPLATE = (
    "Given the following company information, provide the official "
    "website URL.\n\n"
    "Company: {name1}\n"
    "City: {city}\n"
    "State: {state}\n"
    "Country: {country}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "website_url": "str or null",\n'
    '  "confidence": "high|medium|low"\n'
    "}}\n\n"
    "Rules:\n"
    "1. Return the official corporate website URL only when you are "
    "confident the company is well-known and the URL is correct.\n"
    "2. If you are not confident or the company is obscure, return "
    "JSON null for website_url.\n"
    "3. Do not guess or hallucinate URLs. Use JSON null, never the "
    'string "null" or "UNKNOWN".\n'
    "4. Format: https://www.example.com (include scheme)."
)
```
Sentinel set, verbatim: `{"", "null", "none", "unknown", "n/a", "na"}` (enrichment/website_resolver.py:614).

#### 5 Complexity
Exactly one LLM call per invocation; no SERP, no page fetch, no retry (enrichment/website_resolver.py:597-599).

#### 6 Worked example
tests/test_website_resolver.py:302-315: `name1="Fisher Scientific Co. LLC"`, mock LLM returns a URL for "fisher scientific" → result `url == "https://www.fishersci.com"`, `confidence == "low"`, `source == "llm"`. Negative case: `"BioMed Solutions Inc."` → mock returns null → `url is None`, `confidence == "none"` (tests/test_website_resolver.py:317-325). End-to-end, the Path C result is written and flagged: `flag_for_review is True` and "website" appears in `flag_reason` (tests/test_website_resolver.py:381-397).

#### 7 Failure modes
- LLM call exception → empty resolution, field stays null (enrichment/website_resolver.py:601-607).
- Non-dict payload, sentinel string, or malformed URL (no scheme / no dotted host) → empty resolution (lines 609-625).
- A confidently wrong LLM URL passes both guards; mitigation is the unconditional `low` confidence + review flag, not detection (lines 560-565, 633).

---

### Registrable domain derivation (`extract_domain` — utils/text_utils.py; precedence in orchestrator)

#### 1 Purpose
Reduces any URL to its registrable domain (`'https://web.mit.edu/path'` → `'mit.edu'`) and establishes the precedence ROR domain → domain-from-`website_url` → domain-from-`source_url` for the output `domain` field, which also gates the department-domain probe (utils/text_utils.py:23-28; enrichment/orchestrator.py:1560-1570, 564-572).

#### 2 Inputs and outputs
- `extract_domain(url: str | None) -> str | None` (utils/text_utils.py:23).
- Companion inverse `strip_tld(host: str | None) -> str | None` removes only the trailing TLD (`'mit.edu'` → `'mit'`, `'example.co.uk'` → `'example'`, `'cs.mit.edu'` → `'cs.mit'`); used downstream in search-term derivation (enrichment/search_terms.py:70-87).

#### 3 Pseudocode
`extract_domain` — source: utils/text_utils.py:23-48.
1. Falsy input → `None`.
2. `urlparse(url).hostname or ""`; split on `"."`.
3. If ≥ 3 parts and the last two parts form a known two-part TLD → return the last three parts joined (lines 41-44).
4. If ≥ 2 parts → return the last two parts joined (line 45); else return the bare hostname (line 46). Any exception → `None` (lines 47-48).

`strip_tld` — source: enrichment/search_terms.py:70-87.
1. Blank → `None`. Lowercase, split on `"."`.
2. If ≥ 3 parts and last two are in `_TWO_PART_TLDS` → join all but the last two (lines 81-84).
3. If ≥ 2 parts → join all but the last (lines 85-86); else return the single part (line 87).

Precedence in result assembly:
1. Path A: ROR supplies `domain` directly (enrichment/orchestrator.py:2041-2043; value computed at enrichment/tier1_ror.py:502); person-affiliation confirmation likewise (enrichment/orchestrator.py:1476-1478).
2. `_finalise_and_return`, after `_maybe_resolve_website_bc` and *before* the department probe: `if not result.get("domain") and result.get("website_url"): result["domain"] = extract_domain(website_url)` when the extraction is truthy (enrichment/orchestrator.py:1560-1569). The comment records the motivating regression: without this the probe "bails at its base-domain gate for every such row and `department_domain` is always empty" (lines 1561-1565).
3. `finalise`, last: `if not result.get("domain") and result.get("source_url"): result["domain"] = extract_domain(source_url)` when truthy (enrichment/orchestrator.py:564-572).

#### 4 Constants
Two-part TLD set inside `extract_domain`, verbatim (utils/text_utils.py:37-40):
```python
known_two_part = {"co.uk", "ac.uk", "org.uk", "ac.jp", "co.jp",
                  "com.au", "edu.au", "org.au", "ac.in", "co.in",
                  "com.br", "org.br", "edu.br", "ac.nz", "co.nz",
                  "ac.za", "co.za"}
```
`_TWO_PART_TLDS` in search_terms mirrors it (enrichment/search_terms.py:56-67).

#### 5 Complexity
Constant-time string operations per call; no I/O.

#### 6 Worked example
tests/test_domain_from_website.py:57-64: seeded result with `website_url="https://www.ufl.edu"`, `domain=None` → after `_finalise_and_return`, `out.domain == "ufl.edu"`. Precedence: with `domain="example.edu"` pre-set, the website-derived value does not overwrite it (tests/test_domain_from_website.py:77-84); with neither website nor source URL, `domain` stays `None` (tests/test_domain_from_website.py:86-93). `strip_tld` unit cases: `"mit.edu"→"mit"`, `"example.co.uk"→"example"`, `"cs.mit.edu"→"cs.mit"`, `"mit"→"mit"`, `""→None` (tests/test_search_terms.py:21-35).

#### 7 Failure modes
- `extract_domain` is a heuristic public-suffix list of 17 entries; hosts under other two-part suffixes (e.g. suffixes not in the set) yield the suffix itself as the "registrable domain".
- A `low`-confidence Path B/C website still feeds `domain`; the record-level review flag is the only mitigation (enrichment/orchestrator.py:898-904, 916-921).

---

### Department-domain probe (`_probe_department_url` — enrichment/orchestrator.py)

#### 1 Purpose
Finds the web home of the record's academic unit (name2) and writes it to `result["department_domain"]` — either a subdomain host (`chem.ufl.edu`) or a full path URL (`clas.ufl.edu/chemistry`) — scoring and content-verifying every candidate so generic administrative hosts and news/archive pages never win (enrichment/orchestrator.py:963-1002).

#### 2 Inputs and outputs
- `_probe_department_url(self, record_id: str, result: dict[str, Any], cache: BatchCache) -> None`; mutates `result["department_domain"]` only (enrichment/orchestrator.py:963-968).
- Gates, in order (enrichment/orchestrator.py:1004-1060): record_type must be `"research_institution"` (1004-1005); `department_domain` not already set (1006-1007); `domain` non-blank (1008-1010); name2 (enriched, else original) non-blank (1011-1016); name2 not an admin unit (`is_admin_unit`, utils/text_utils.py:990; check at 1020-1025); name2 not a pure address/location fragment (1031-1038); name2 not a granular unit (`is_granular_unit`, utils/text_utils.py:410; check at 1039-1044); at least one significant token or an acronym derivable from the cleaned phrase (1050-1060).
- Token preparation: `core = extract_dept_core(name2) or name2` (donor-prefix/unit-suffix strip; enrichment/search_terms.py:196-215), `cleaned = clean_name2_phrase(core) or core` (generic prefix/suffix strip + title case; enrichment/search_terms.py:218-253), `tokens = _significant_dept_tokens(cleaned)` (lowercased alpha words ≥ 3 chars minus `_DEPT_GENERIC_TOKENS`; enrichment/orchestrator.py:172-185), `acronym = derive_acronym(cleaned)` (capitalised-word initials, ≥ 2 letters required, or trailing parenthesised acronym; enrichment/search_terms.py:129-165). (enrichment/orchestrator.py:1050-1053.)
- Output form: `finalise` later prefixes a bare host with `https://` so `department_domain` is emitted as a full URL (enrichment/orchestrator.py:602-606), after `derive_search_terms` has consumed the host form (enrichment/orchestrator.py:595-600).

#### 3 Pseudocode

**Probe-base resolution** (`_resolve_probe_base`) — source: enrichment/orchestrator.py:923-961.
1. `website = result["website_url"]` or `f"https://{registrable}/"` (line 934). Cache lookup `cache.get_resolved_host(website)`; hit → return (935-937).
2. `final = await self._page_fetcher.resolve_final_url(website)` (exception → `None`) (940-943). `resolve_final_url` issues an `HTTP HEAD` with `allow_redirects=True` (timeout 5 s), retrying with a streamed GET when the server answers ≥ 400 to HEAD, and returns the final URL or `None` (search/page_fetcher.py:111-142).
3. If `final`: take its hostname, strip a leading `"www."` or `"web."` (946-948); if the final URL's registrable domain differs from the input `registrable` → base = that new registrable (§5f, redirect landed on a new domain, line 952); else base = the full host (§5e, subdomain-aware, line 954).
4. `cache.set_resolved_host(website, base)`; return base (955-961). The `_resolved_host` dict lives on the per-batch `BatchCache` so the probe costs one resolution per institution (utils/cache.py:60-71).

**Main probe** — source: enrichment/orchestrator.py:1004-1331. After the gates and token prep above:
1. `base = await self._resolve_probe_base(result, base, cache)` (1067). Helper `_host_of(url)` lowercases the hostname, strips `www.`, and returns `None` for the bare base host (1069-1080).
2. **Stage 0 — constructed-subdomain GET-probe** (1082-1123): build candidate labels — the acronym if `2 <= len(acronym) <= 6` (1091-1092); the two longest tokens of length ≥ 4, each followed by its 4-char and 3-char prefixes when longer ("chem" ← "chemistry") (1093-1107). Probe `f"{c}.{base}"` for each candidate concurrently via `asyncio.gather(..., return_exceptions=True)` with `_verify_candidate_host` (1109-1115); the first host (in candidate order) whose result is `True` wins → write and return (1116-1123).
3. **Stage 1 — homepage scrape** (1125-1170): fetch outgoing links of `website_url` (or `https://{base}/`) via `fetch_outgoing_links(homepage, base)`, which returns `(anchor_text, absolute_href)` for every link whose host differs from the base (search/page_fetcher.py:157-215); exceptions → empty list (1127-1136). Score each unique non-base host with `_score_dept_candidate(host, base, path, anchor_text, tokens, acronym)`; keep scores > 0 (1144-1159). Sort descending and verify the top 5 in order; first verified host wins → write and return (1160-1170).
4. **Stage 2 — site-restricted SERP** (1172-1221): query `f"{cleaned} site:{base}"`, `num_results=5`, with per-batch SERP cache get/set (1176-1192). Score/dedupe hosts as in Stage 1, verify top 5, first verified wins (1194-1221).
5. **Stage 2b — on-domain path page, no new SERP call** (1223-1266): reuse the Stage-2 results. For each result whose host equals or is a subdomain of `base` and whose path is non-empty: drop it if `_path_is_generic(path)` (§5b, 1251-1252); require some needle (tokens ∪ acronym) in the hyphen/underscore-flattened path + title haystack (1253-1254); sort remaining candidates by `(_path_canonicality_penalty(path), SERP index)` ascending (1256-1258); verify each full URL with `_verify_candidate_url`; first verified wins → `department_domain = cand_url` (full URL, including path) (1259-1266).
6. **Stage 3 — cross-domain SERP (opt-in)** (1268-1325): if `settings.dept_probe_cross_domain` is False (default) → log and return, leaving the field null (1277-1283). Otherwise query `f"{cleaned} {name1}"` (or `cleaned` alone when name1 is blank), `num_results=5`, cached (1284-1306); for each result host: skip if `_is_third_party_host` (registrable domain in `_THIRD_PARTY_DOMAINS`; enrichment/orchestrator.py:123-132); accept the first host that passes `_verify_candidate_host` (1308-1325).
7. No stage succeeded → log "no host matched" and leave `department_domain` null (1327-1331).

**Candidate scorer** (`_score_dept_candidate`) — source: enrichment/orchestrator.py:206-258.
1. `needles = tokens ∪ {acronym.lower()}` (acronym only if `len(acronym) >= 2`); empty → 0 (229-233).
2. `host_prefix` = host with `"." + base` suffix stripped; `first_seg` = its first dot-label (235-238). If `first_seg in _GENERIC_HOST_PREFIXES` → 0, "capped regardless of signals" (239-240).
3. If no needle satisfies `_seg_matches_needle(first_seg, n)` → 0 — path/title matches alone are never enough (243-246).
4. `score = 3` (host match). Path bonus: `+1` if any needle is a substring of the lowercased path AND `_path_is_generic(path)` is False, then `-= min(2, _path_canonicality_penalty(path))` (248-255). Title bonus: `+1` if any needle in lowercased title (256-257). Return score (258).

**Path helpers** — `_path_is_generic(path)`: True when any `/`-segment lowercases into `_GENERIC_PATH_SEGMENTS` (enrichment/orchestrator.py:152-157). `_path_canonicality_penalty(path)`: `max(0, len(segs) - 1)` (depth), `+5` if any segment is a 4-digit year (`_YEAR_SEG_RE = re.compile(r"^\d{4}$")`, line 149), `+3` if any segment is in `_SUBPAGE_PATH_SEGMENTS` (enrichment/orchestrator.py:160-169).

**Morphological segment match** (`_seg_matches_needle`) — source: enrichment/orchestrator.py:188-203: True when the needle is a substring of the segment ("cs" in "csail"), or both are ≥ 3 chars and one is a leading prefix of the other ("chem" ← "chemistry"). A module-level duplicate with identical logic exists as `seg_matches_needle` in utils/text_utils.py:957-971 for the search-term rule.

**Content verification** (`_verify_candidate_host` / `_verify_candidate_url` / `_needle_hit`) — source: enrichment/orchestrator.py:1333-1411.
1. `_verify_candidate_host(host, …)` delegates to `_verify_candidate_url(f"https://{host}/", …)` (1333-1343).
2. Fetch structured page content (`fetch_page_content` → `PageContent` with `page_title`, `h1`, `breadcrumb`, `body_text`; search/page_fetcher.py:53-63, 85-93); fetch failure or empty page → False (1363-1368).
3. `text` = lowercased join of title + h1 + breadcrumb only (body text is not consulted); blank → False (1370-1376).
4. Phrase pass: if the full `cleaned_phrase` (length ≥ 4) is a substring of `text` → True (1378-1381).
5. Needle pass (§5d): `needles = tokens ∪ {acronym}` (acronym if ≥ 2 chars); none → False (1383-1387). Tokenise `text` into `[a-z]+` words (1395). `_needle_hit(n)`: True if any word satisfies `_seg_matches_needle(w, n)` OR shares a leading character-by-character prefix of ≥ 5 chars with the needle ("physic"al ← "physic"s) (1397-1406).
6. `matches = sum(1 for n in needles if _needle_hit(n))`; pass iff `matches >= 1` when only one needle exists, else `matches >= 2` (1408-1411). This is what rejects `science.mit.edu` ("MIT School of Science") for a "Computer Science" query (1358-1361, 1389-1394).

#### 4 Constants
- `_DEPT_GENERIC_TOKENS` (enrichment/orchestrator.py:94-99), verbatim:
  ```python
  _DEPT_GENERIC_TOKENS = {
      "department", "dept", "school", "institute", "center", "centre",
      "division", "faculty", "office", "group", "lab", "laboratory",
      "of", "for", "the", "and", "in", "on", "at", "to", "a", "an", "&",
      "research", "studies", "programme", "program",
  }
  ```
- `_GENERIC_HOST_PREFIXES` (enrichment/orchestrator.py:103-109), verbatim:
  ```python
  _GENERIC_HOST_PREFIXES = {
      "professorships", "inside", "calendar", "news", "alumni", "admin",
      "hr", "store", "shop", "give", "donate", "support", "events",
      "directory", "library", "libraries", "career", "careers", "jobs",
      "search", "secure", "my", "mail", "email", "wiki", "intranet",
      "media", "press",
  }
  ```
- `_THIRD_PARTY_DOMAINS` (enrichment/orchestrator.py:113-120), verbatim:
  ```python
  _THIRD_PARTY_DOMAINS = {
      "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com",
      "x.com", "youtube.com", "instagram.com", "reddit.com",
      "researchgate.net", "scholar.google.com", "google.com",
      "amazon.com", "indeed.com", "glassdoor.com", "pubmed.gov",
      "ncbi.nlm.nih.gov", "nih.gov", "doi.org", "academia.edu",
      "github.com", "github.io", "medium.com", "substack.com",
  }
  ```
- `_GENERIC_PATH_SEGMENTS` (enrichment/orchestrator.py:137-142), verbatim:
  ```python
  _GENERIC_PATH_SEGMENTS = {
      "news", "news-events", "events", "event", "story", "stories",
      "article", "articles", "blog", "calendar", "archive", "colloquium",
      "seminar", "admin", "hr", "library", "libraries", "careers", "career",
      "directory", "media", "press",
  }
  ```
- `_SUBPAGE_PATH_SEGMENTS` (enrichment/orchestrator.py:145-148), verbatim:
  ```python
  _SUBPAGE_PATH_SEGMENTS = {
      "undergrad", "undergraduate", "graduate", "grad", "people", "faculty",
      "staff", "contact", "admissions", "apply", "courses", "alumni", "giving",
  }
  ```
- `_YEAR_SEG_RE = re.compile(r"^\d{4}$")` (enrichment/orchestrator.py:149); `_TOKEN_RE = re.compile(r"[A-Za-z]{3,}")` (line 172).
- Scoring values: host +3, path +1 (minus `min(2, penalty)`), title +1 (enrichment/orchestrator.py:248-257); canonicality: depth `len(segs)-1`, year +5, subpage +3 (lines 163-168).
- Bounds: acronym subdomain candidate length `2..6` (1091); token prefix lengths `(4, 3)` (1103); top-2 tokens (1096); verify top 5 scored candidates per stage (1161, 1211); `num_results=5` for both SERP calls (1183, 1297); ≥ 5-char shared prefix in `_needle_hit` (1404); needle thresholds ≥ 2 (≥ 1 for a single needle) (1408-1411).

#### 5 Complexity
Per record, worst case: 1 redirect resolution (cached per institution; enrichment/orchestrator.py:935-955) + ≤ 7 constructed-subdomain fetches (1 acronym + 2 tokens × (full, 4-prefix, 3-prefix), concurrent; 1090-1115) + 1 homepage fetch with ≤ 5 verification fetches (1125-1170) + 1 SERP call (5 results) with ≤ 5 verification fetches (1172-1221) + ≤ 5 path-candidate verification fetches from the same SERP results (1237-1266) + (opt-in) 1 more SERP call with ≤ 5 verification fetches (1284-1325). With the default `DEPT_PROBE_CROSS_DOMAIN=false` the probe issues at most one SERP call per record (config.py:161-168; enrichment/orchestrator.py:1273-1283).

#### 6 Worked example
From tests/test_domain_from_website.py:66-75 with the mock page graph (tests/mocks/page_mock.py:66-72): seeded result `name2_enriched="Department of Chemistry"`, `website_url="https://www.ufl.edu"`, `domain=None`. Domain derivation gives `domain="ufl.edu"` (enrichment/orchestrator.py:1566-1569). Token prep: `extract_dept_core` → `"Chemistry"` (enrichment/search_terms.py:203), `clean_name2_phrase` → `"Chemistry"`, `tokens = {"chemistry"}`; `derive_acronym("Chemistry")` returns `None` (fewer than 2 initials; enrichment/search_terms.py:163-164). Probe base: the mock `resolve_final_url` returns the URL unchanged (tests/mocks/page_mock.py:82-85), host `www.ufl.edu` → strip `www.` → `ufl.edu` = registrable → base stays `ufl.edu`. Stage-0 candidates from token `chemistry`: `["chemistry", "chem", "che"]` → hosts `chemistry.ufl.edu`, `chem.ufl.edu`, `che.ufl.edu` (enrichment/orchestrator.py:1093-1109). Verification: only `https://chem.ufl.edu/` matches the mock fragment `"chem.ufl.edu"`, whose synthetic title is "Department of Chemistry University of Florida" (tests/mocks/page_mock.py:66-72, 103-119); the phrase `"chemistry"` (≥ 4 chars) appears in the title → verified (enrichment/orchestrator.py:1378-1381) → `department_domain = "chem.ufl.edu"`; the test asserts `"ufl.edu" in out.department_domain` after `finalise` prefixes `https://` (tests/test_domain_from_website.py:74-75; enrichment/orchestrator.py:602-606). Scorer fixtures: `chem.ufl.edu` for tokens `{"chemistry"}` scores ≥ 3 while `bio.ufl.edu` scores 0 (tests/test_dept_domain_probe.py:29-43); a needle in a `news-events/...` path earns no path bonus (score 3) while the same needle in path `chemistry` scores 4 (tests/test_dept_domain_probe.py:71-80); probe-base fixtures: `dur.ac.uk` → `durham.ac.uk` (redirect), `gc.cuny.edu` kept as full host, `web.mit.edu` → `mit.edu`, second resolution served from cache (tests/test_dept_domain_probe.py:123-161).

#### 7 Failure modes
- Any gate failure (wrong record type, no domain, blank/admin/address/granular name2, no tokens) → silent null with a log line; no SERP or fetch spent (enrichment/orchestrator.py:1004-1060; address-fragment skip tested at tests/test_domain_from_website.py:96-118).
- All stages verify against fetched page title/h1/breadcrumb; network failures during verification make candidates silently unverifiable (return False; enrichment/orchestrator.py:1363-1368).
- Cross-domain departments (e.g. `hopkinsmedicine.org`) are unreachable under the default configuration — the probe stops after Stage 2b (enrichment/orchestrator.py:1273-1283).
- Stage-0 order dependence: candidates are checked in construction order, so an earlier, shorter-prefix host that verifies would win over a later exact host; concurrency (`asyncio.gather`) does not change the deterministic zip-order adjudication (enrichment/orchestrator.py:1109-1123).

---

### WEBSITE_TRACE diagnostic assembly (`_assemble_path_b_trace` — enrichment/website_resolver.py; `scripts/trace_website.py`)

#### 1 Purpose
Emits one structured JSON record per Path B attempt (and one per Path C call) on the dedicated logger `enrichment.trace.website`, attributing each SERP candidate's rejection to the first guard that fired. Read-only by construction: the trace re-evaluates the same pure guards after the resolution is computed and never mutates state (enrichment/website_resolver.py:39-43, 259-266, 462-466).

#### 2 Inputs and outputs
- `_assemble_path_b_trace(*, record_id, name1, record_type, query, num_results, results, chosen, error=None, attempt="quoted") -> dict` (enrichment/website_resolver.py:247-258).
- Emitted only when the caller passes `trace=True` (enrichment/website_resolver.py:479, 509, 581-583); the orchestrator passes `trace=self._settings.website_trace` on both Path B and Path C calls (enrichment/orchestrator.py:894, 914).
- Record fields: `phase="path_b"`, `attempt` (`"quoted"`/`"unquoted_retry"`), `record_id`, `name1`, `record_type`, `query`, `num_results`, `results_returned`, per-candidate list, `chosen_url`, `confidence`, `flagged`, `fell_through_to_path_c`, optional `error` (enrichment/website_resolver.py:330-347). Path C records carry `phase="path_c"`, the inputs, `raw_response`, `treated_as_sentinel`, `url_shape_ok`, `final_value`, optional `llm_error` (enrichment/website_resolver.py:581-595, 605-632).

#### 3 Pseudocode
Source: enrichment/website_resolver.py:267-347.
1. Recompute `valid` and the 0/1/2 ranks exactly as `select_website_from_serp` does, and re-derive `chosen_sr` (the max-rank candidate, unless its rank is 0) so per-candidate `chosen`/`rank` fields match the real decision (269-285).
2. For each SERP result, in position order: record position, URL, lowercased host, title truncated to 120 chars (287-306). Attribute `rejected_by` to the first guard in the real short-circuit order: `"url_shape"` (no URL or `_URL_RE` miss) → `"blacklist"` → `"name_overlap"` (via `_overlap_detail`, which replays `_name_overlap` and reports the matched token and whether it hit host, title, or URL path, scanning tokens in sorted order; lines 203-225) → `"rank_0"`; rank-1 candidates get `foreign_label` re-derived by `_foreign_label` (lines 228-244) (307-325).
3. The chosen candidate carries `chosen=True` and the resolution confidence (325-328).
4. Assemble the top-level record; attach `error` when the SERP call failed (330-347).

Standalone driver `scripts/trace_website.py`: forces `WEBSITE_TRACE=true` before `Settings` is constructed (scripts/trace_website.py:32); exercises only the resolver — "no full pipeline, no enrichment tiers, no ROR — Path A is skipped entirely", mirroring `_maybe_resolve_website_bc` (lines 1-17, 92-109); uses a fresh unshared `BatchCache` per record so every query hits the wire (lines 96-97); captures trace lines to `logs/website_trace.json` and prints a per-candidate table (lines 59-81, 112-149, 164-176); default worklist is three failing records (`Atlantic Testing Labs`, `Fine Organics Limited`, `Verdox, Inc.`) plus three controls (lines 45-56).

#### 4 Constants
Title truncation `[:120]` (enrichment/website_resolver.py:299); logger name `"enrichment.trace.website"` (line 43); trace output file `logs/website_trace.json` (scripts/trace_website.py:166); the FAILING/CONTROLS record lists (scripts/trace_website.py:46-56).

#### 5 Complexity
One pass over the ≤ 10 SERP results, re-running the pure guards per candidate; no additional network calls. One JSON log line per Path B attempt and per Path C call.

#### 6 Worked example
tests/test_website_resolver.py:456-481: with `trace=True` and a single SERP result `https://www.acmeco.com/` for "Acme Co", exactly one line is emitted; the parsed record has `phase == "path_b"`, `record_id == "ON"`, `results_returned == 1`, `candidates[0]["chosen"] is True`, `candidates[0]["rank"] == 2`, `chosen_url == "https://www.acmeco.com"`, `fell_through_to_path_c is False`. With tracing off (default), the same resolution occurs and zero lines are emitted (tests/test_website_resolver.py:436-454); the default itself is asserted at tests/test_website_resolver.py:431-434.

#### 7 Failure modes
- A SERP exception produces a trace record with `results=[]` and `error="serp_call_failed: …"` (enrichment/website_resolver.py:495-502).
- The trace re-derivation is a duplicate of the selection logic; a future divergence between `_rank` (line 381) and the re-computation (lines 279-282) would silently mis-attribute candidates. Both currently share the same helper functions.

---

### Non-determinism notes

- **SERP volatility.** Path B and the department probe depend on externally ranked result sets. The repository's own diagnostic findings record that, in the `WEBSITE_TRACE` run behind `Website_Trace_Findings.pdf`, "for two of three failing companies the company's own site never appeared in the SERP result set (a retrieval miss, not a guard rejection), and the SERP result sets have drifted since the records were characterized" (docs/thesis/02_ARCHITECTURE.md:512-515; the PDF itself is a repository-root artefact listed at docs/thesis/00_INVENTORY.md:26). The provider also varies by configuration: SerpAPI when `SERPAPI_KEY` is set, otherwise DuckDuckGo with an explicit lower-quality warning (config.py:137-145).
- **Caching.** SERP results are cached at two scopes keyed by the lowercased, stripped query string: a per-batch dict and an optional process-level `SerpCache` shared across batches; batch reads fall through to the shared store and writes propagate to it, so an identical query re-issued later in the process lifetime returns the earlier snapshot rather than a fresh SERP (utils/cache.py:22-45, 85-105). Resolved probe-base hosts are cached per batch only (utils/cache.py:60-71; tested at tests/test_dept_domain_probe.py:153-161). Nothing is persisted to disk (utils/cache.py:11-13, 31). The trace script deliberately bypasses both scopes with a fresh unshared `BatchCache` per record (scripts/trace_website.py:96-97).
- **Content fetches.** Department-probe verification depends on live page fetches (title/h1/breadcrumb); timeouts, TLS interception, and page redesigns change verification outcomes between runs (enrichment/orchestrator.py:1363-1376; search/page_fetcher.py:85-93, 111-142). The page-fetch timeout defaults to `"PAGE_FETCH_TIMEOUT_SECONDS": "10"` (config.py:110; config.py:211-213).
- **LLM.** Path C is a single LLM call with no seed control; its output varies run to run and is therefore always `low` confidence and flagged (enrichment/website_resolver.py:560-565, 633; enrichment/orchestrator.py:916-921).
- **Config gates and defaults**, verbatim from config.py:
  - `"DEPT_PROBE_CROSS_DOMAIN": "false"` (config.py:114); Settings field `dept_probe_cross_domain: bool = field(default_factory=lambda: _bool(os.getenv("DEPT_PROBE_CROSS_DOMAIN"), default=False))` (config.py:166-168) — gates the probe's Stage 3 second SERP call (enrichment/orchestrator.py:1277-1283). Note: docs/thesis/02_ARCHITECTURE.md:508-510 flags an earlier claim that this "defaults on" as a discrepancy; the code default is False.
  - `"WEBSITE_TRACE": "false"` (config.py:118); `website_trace: bool = field(default_factory=lambda: _bool(os.getenv("WEBSITE_TRACE"), default=False))` (config.py:247-249) — diagnostic-only; comments at both sites state resolution behaviour is unchanged when enabled (config.py:115-117, 245-246), and tests/test_website_resolver.py:436-454 verifies no records are emitted when off.
  - `_bool` parses `"true"/"1"/"yes"` (lowercased, stripped) as True (config.py:70-73). The `DEFAULTS`-style dict at config.py:83-119 (`OPTIONAL_VARS_WITH_DEFAULTS`) is documentation of defaults; the operative defaults are the `default=` arguments in the Settings fields.
  - No other `WEBSITE_*` or `DEPT_PROBE_*` settings exist in config.py (grep over config.py returns only the four lines cited above).
- **Tie-breaking is deterministic given a fixed result set**: Path B uses first-max over SERP order (enrichment/website_resolver.py:386); the probe's stage lists sort by score (descending) with Python's stable sort and by `(penalty, SERP index)` for path candidates (enrichment/orchestrator.py:1160, 1256-1258). All observed non-determinism therefore enters through the external result sets, fetched pages, and the LLM, not through the selection logic.


# Part F — Search-term construction

Module under documentation: `enrichment/search_terms.py` (586 lines, read in full).
Helpers: `utils/text_utils.py` (bodies read for every helper imported at
`enrichment/search_terms.py:28-34`), `enrichment/preprocess.py:243-257`
(`_extract_addresses`, imported at `enrichment/search_terms.py:27`).
Call site: `enrichment/orchestrator.py:600`.
Tests: `tests/test_search_terms.py`, `tests/test_search_terms_fixes.py`.

---

### Search-term derivation, top level (`derive_search_terms` — enrichment/search_terms.py)

#### 1 Purpose
Computes the pair `(search_term_1, search_term_2)` from a finalised enrichment
result dict: `search_term_1` is a compact institution handle mirroring Name 1,
`search_term_2` a unit/department handle mirroring Name 2, both shaped for
downstream re-querying (Google, internal search, dedup keys) without consumers
re-deriving abbreviations or domains (`enrichment/search_terms.py:1-18`,
`enrichment/search_terms.py:561-586`).

#### 2 Inputs and outputs
**Input:** one `result: dict[str, Any]` (`enrichment/search_terms.py:561-563`). Keys read:
- `_ror_acronym`, `domain`, `name1_enriched`, `name1_original`, `_name1_was_person`, `_search_term_1_original` (`enrichment/search_terms.py:483-502`)
- `name2_enriched`, `name2_original`, `_dba_values`, `department_domain`, `domain` (`enrichment/search_terms.py:509-538`)
- `flag_reason` (read at `enrichment/search_terms.py:528`)

**Output:** `tuple[str | None, str | None]` — each element already terminally
normalised (`enrichment/search_terms.py:583-586`).

**Side effect:** the Search-Term-2 field-swap guard may set
`result["flag_for_review"] = True` and (only when blank) `result["flag_reason"]`
(`enrichment/search_terms.py:527-532`). No other mutation.

**Call site:** `enrichment/orchestrator.py:600` —
`result["search_term_1"], result["search_term_2"] = derive_search_terms(result)`,
executed after all name/domain fields are settled (`enrichment/orchestrator.py:595-600`).
`department_domain` is still in bare-host form at that point; it is rewritten to a
full `https://` URL only afterwards (`enrichment/orchestrator.py:602-606`). The
transient inputs are populated upstream — `_search_term_1_original` from the SAP
record at `enrichment/orchestrator.py:307-309`, `_ror_acronym` (ROR acronym
variant carrier) at `enrichment/orchestrator.py:311-313`, `_name1_was_person`
(UC 7 person signal) at `enrichment/orchestrator.py:1831-1836` — and stripped
before response validation (`enrichment/orchestrator.py:610-615`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:561-586`.
1. `t1 ← _derive_search_term_1(result)` (line 584).
2. `t2 ← _derive_search_term_2(result)` (line 585).
3. **return** `(_normalise_term(t1), _normalise_term(t2))` (lines 583-586).

The full fallback ladders are stated in the function's own docstring
(`enrichment/search_terms.py:564-582`): ST1 = ROR acronym → TLD-stripped domain
→ required handle → None; ST2 = `"ADMIN"` → subdomain acronym → Name 2 phrase
filled to 32 → department-domain host → None, with DBA and field-swap guards.

#### 4 Constants
None at this level (constants live in the sub-procedures below).

#### 5 Complexity
O(L) in the total length of the consulted string fields: each sub-procedure is a
constant number of regex scans and set lookups over its inputs (see per-procedure
bounds below). No recursion, no iteration over collections other than word
tokens of the input strings.

#### 6 Worked example
`tests/test_search_terms.py:134-145` (`test_ror_acronym_preferred`): input
`{"_ror_acronym": "MIT", "domain": "mit.edu", "name1_enriched": "Massachusetts Institute of Technology", "name2_enriched": None, "name2_original": None, "source_url": None}`.
ST1 chain returns the ROR acronym `"MIT"` at rule 1 (`enrichment/search_terms.py:483-485`);
ST2: no name2, no `department_domain` key → `None` (`enrichment/search_terms.py:556-558`).
Normalisation leaves `"MIT"` unchanged (3 chars, already upper). Asserted output:
`("MIT", None)` (`tests/test_search_terms.py:144-145`).

#### 7 Failure modes
- Both elements may be `None`; the function never raises on missing keys (all reads use `.get`, `enrichment/search_terms.py:483-538`).
- The field-swap guard writes review flags as a side effect during what is nominally a derivation call (`enrichment/search_terms.py:527-532`) — callers that treat `derive_search_terms` as pure will miss this mutation.
- Correctness of ST1 rule 1 depends on the upstream currency check of `_ror_acronym` in `tier1_ror` (asserted by the docstring `enrichment/search_terms.py:480-481`; the check itself is exercised at `tests/test_search_terms_fixes.py:109-132`).

---

### Search Term 1 derivation (`_derive_search_term_1` — enrichment/search_terms.py)

#### 1 Purpose
Produces the institution handle: a currency-checked ROR acronym when available,
else the TLD-stripped resolved domain, else a "required handle" built from the
original SAP Search Term 1 or the Name 1 words — but only when Name 1 is usable
(`enrichment/search_terms.py:479-502`).

#### 2 Inputs and outputs
**Input:** `result: dict[str, Any]`; keys `_ror_acronym`, `domain`,
`name1_enriched`, `name1_original`, `_name1_was_person`,
`_search_term_1_original` (`enrichment/search_terms.py:483-499`).
**Output:** `str | None` (pre-normalisation).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:479-502`.
1. `ror_acronym ← strip(result["_ror_acronym"])`; **if** non-empty → **return** it (lines 483-485).
2. `domain ← strip(result["domain"])`; **if** non-empty → **return** `strip_tld(domain)` (lines 486-488).
3. Usability guard (lines 489-498):
   a. `name1_enriched ← strip(result["name1_enriched"])` (line 493).
   b. `name1 ← name1_enriched` if non-empty, else `strip(result["name1_original"])` (line 494).
   c. `was_person ← bool(result["_name1_was_person"])` (line 495).
   d. `usable ← bool(name1) and not (was_person and not name1_enriched)` (line 496) — i.e. a UC 7 person left unresolved (person flag set, no enriched institution) is unusable; a Stage-2b-resolved affiliation with an institution in `name1_enriched` is usable (comment lines 490-492).
   e. **if not** usable → **return** `None` (lines 497-498).
4. `original ← strip(result["_search_term_1_original"])`; **if** non-empty → **return** it (lines 499-501).
5. **return** `_name1_text_handle(name1) or name1` (line 502).

Note: `derive_acronym` is **not** part of this chain; caps-derived acronyms were
removed from ST1 (README.md:765 records the removal; the function survives for the
department probe, `enrichment/orchestrator.py:1053`).

#### 4 Constants
None directly; rule 5 delegates to `_name1_text_handle`, whose constants are
`_ST1_LEGAL_SUFFIXES` and `_TERM2_STOPWORDS` (below).

#### 5 Complexity
O(1) dict reads plus one call each to `strip_tld` (O(|host|)) or
`_name1_text_handle` (O(|name1|)); overall O(|name1| + |domain|).

#### 6 Worked example
`tests/test_search_terms_fixes.py:36` — input `_st(name1_original="Verdox, Inc.")`
(all other keys None/False per the fixture base, `tests/test_search_terms_fixes.py:18-28`).
Rule 1: `_ror_acronym` None → skip. Rule 2: `domain` None → skip. Guard: `name1 =
"Verdox, Inc."`, `was_person = False` → usable. Rule 4: no SAP original → skip.
Rule 5: `_name1_text_handle` tokenises to `["Verdox", "Inc"]`, drops `inc` as a
legal suffix, keeps `"Verdox"` (`enrichment/search_terms.py:378-389`).
Normalisation uppercases → asserted `"VERDOX"` (`tests/test_search_terms_fixes.py:36`).

Person guard example: `tests/test_search_terms_fixes.py:39` — `_st(name1_original="John F Florek", _name1_was_person=True)`: `name1_enriched` empty, flag set → `usable = False` → asserted `None`.

Domain-rule example: `tests/test_search_terms.py:147-157` — `domain="stanford.edu"` with no ROR acronym → `strip_tld("stanford.edu") = "stanford"` → normalised `"STANFORD"`.

Empty-Name-1 example: `tests/test_search_terms_fixes.py:41` — `name1_original=None`, `_search_term_1_original="KS"` → guard fails before the SAP-original rule is reached → asserted `None`.

#### 7 Failure modes
- The SAP original (`_search_term_1_original`) is returned verbatim regardless of quality — a stale SAP term propagates whenever ROR acronym and domain are both absent but Name 1 is usable (`enrichment/search_terms.py:499-501`; exercised at `tests/test_search_terms.py:171-184`).
- A person name that Stage 2b failed to resolve yields ST1 = None by design (`enrichment/search_terms.py:496-498`); consumers must tolerate a null handle.
- `strip_tld` on a bare single-label domain returns the label itself (`enrichment/search_terms.py:87`), so a malformed `domain` value like `"edu"` would become the search term. ⚠ NO FIXTURE COVERAGE for this edge.

---

### Search Term 2 derivation (`_derive_search_term_2` — enrichment/search_terms.py)

#### 1 Purpose
Produces the unit/department handle from Name 2 and the department domain, with
an administrative-desk override and two field-content guards (DBA trade name,
institution-in-Name-2 field swap) (`enrichment/search_terms.py:505-558`).

#### 2 Inputs and outputs
**Input:** `result: dict[str, Any]`; keys `domain`, `name2_enriched`,
`name2_original`, `_dba_values`, `department_domain`, `flag_reason`
(`enrichment/search_terms.py:509-538`).
**Output:** `str | None` (pre-normalisation). **Side effect:** may set
`flag_for_review`/`flag_reason` (`enrichment/search_terms.py:527-532`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:505-558`.
1. `domain ← strip(result["domain"]) or None`; `name2 ← strip(result["name2_enriched"])` (lines 509-510).
2. **Street-address guard** (lines 511-518): if `name2` empty, take `orig ← strip(result["name2_original"])`; if `orig` non-empty, run `_extract_addresses(orig)` (`enrichment/preprocess.py:243-257`); adopt `orig` as `name2` **unless** the extraction found addresses and left an empty remainder — i.e. a Name 2 that was purely a street address (e.g. one moved into a street field by enrichment) never becomes a unit handle (comment lines 514-516).
3. **DBA guard** (lines 521-524): if `name2` non-empty and `"name2" ∈ result["_dba_values"]` → `name2 ← ""` (UC 11 DBA trade name is never a unit handle).
4. **Field-swap guard** (lines 525-532): elif `looks_like_research_institution(name2)` **and not** `_name2_is_unit_phrase(name2)` → set `result["flag_for_review"] = True`; if `flag_reason` blank, set it to `"probable Name 1 / Name 2 field swap — institution in Name 2"` (verbatim, lines 529-531); `name2 ← ""`.
5. **Rule 0 — ADMIN override** (lines 534-536): if `name2` and `is_admin_unit(name2)` → **return** `"ADMIN"`.
6. `dept_domain ← strip(result["department_domain"]) or None` (line 538).
7. **Rule 1 — subdomain acronym** (lines 540-543): `sub ← _subdomain_acronym(dept_domain, domain, name2)`; if truthy → **return** `sub`.
8. **Rule 2 — Name 2 phrase** (lines 545-553): if `name2`:
   a. if `_PAREN_ACRONYM_RE` matches at the end → **return** the captured parenthetical acronym (lines 547-549);
   b. `cleaned ← clean_name2_phrase(name2) or name2` (line 550);
   c. `filled ← _fill_to_width(cleaned, 32)`; if truthy → **return** `filled` (lines 551-553).
9. **Rule 3 — department-domain fallback** (lines 555-557): if `dept_domain` → **return** `_dept_domain_to_search_term(dept_domain, domain)`.
10. **return** `None` (line 558).

#### 4 Constants
- `_PAREN_ACRONYM_RE = re.compile(r"\(([A-Z][A-Z0-9&\-]{1,9})\)\s*$")` (`enrichment/search_terms.py:52`).
- Guard/override word lists live in the helpers: `_NAME2_PREFIXES`/`_NAME2_SUFFIXES` (`enrichment/search_terms.py:93-126`), `_ADMIN_UNIT_TERMS`/`_ADMIN_PREFIXES` (`utils/text_utils.py:977-987`).

#### 5 Complexity
O(L) in the combined length of `name2`, `department_domain`, `domain`: the guards
and rules are each a bounded number of regex scans/prefix comparisons; `_extract_addresses`
iterates its fixed pattern list (`_ADDRESS_PATTERNS`, `enrichment/preprocess.py:158`)
with repeated substitution, linear in `|orig|` per pattern occurrence.

#### 6 Worked example
Rule-1 path — `tests/test_search_terms_fixes.py:56-57`:
`name2_enriched="Electrical Engineering and Computer Science"`,
`department_domain="eecs.mit.edu"`, `domain="mit.edu"`. Guards pass (no DBA; the
name has no research-institution signal). Not admin. `_subdomain_acronym`: prefix
`"eecs"` (4 chars, in 2-6); `clean_name2_phrase` leaves the name unchanged;
tokens `[Electrical, Engineering, Computer, Science]` ("and" is a stopword); no
token shares a ≥3-char prefix with `"eecs"` via `seg_matches_needle`;
`acronym_matches_name("eecs", …)` → letters `EECS` = initials `EECS` → returns
`"EECS"` (`enrichment/search_terms.py:442-476`). Asserted output `"EECS"`.

Rule-2 path with rejected pseudo-acronym — `tests/test_search_terms_fixes.py:54-55`:
`name2_enriched="Department of Chemistry"`, `department_domain="chem.ufl.edu"`,
`domain="ufl.edu"`. `_subdomain_acronym` rejects `"chem"` because
`seg_matches_needle("chem", "Chemistry")` is true (shared ≥3-char leading prefix,
`utils/text_utils.py:966-971`) → falls to rule 2: `clean_name2_phrase` strips
`"department of"` → `"Chemistry"`; `_fill_to_width` keeps it; normalised
`"CHEMISTRY"`. Asserted.

Guard examples: ADMIN — `tests/test_search_terms_fixes.py:52` (`"Accounts Payable"` → `"ADMIN"`); DBA — `tests/test_search_terms_fixes.py:79-82` (`_dba_values={"name2": …}` → `None`); field swap — `tests/test_search_terms_fixes.py:71-77` (`name2_enriched="Tufts University"` → ST2 None, `flag_for_review` True, reason contains "field swap").

Rule-3 path (dept-domain fallback with empty name2): ⚠ NO FIXTURE COVERAGE — every
dept-domain test in the suites also carries a non-empty Name 2, which rule 2 now
wins (see comment `tests/test_search_terms.py:233`).

#### 7 Failure modes
- The field-swap guard's institution detection is heuristic (`_RESEARCH_NAME_SIGNALS_RE`); a department name containing e.g. "Laboratory" is rescued only because `_name2_is_unit_phrase` also fires — names matching neither list pass unflagged.
- The flag reason is only written when `flag_reason` was blank (`enrichment/search_terms.py:528`); an earlier tier's reason masks the swap explanation (the flag bit is still set).
- Rule 3 can emit a marketing-style cross-domain host name (docstring example `'hopkinsmedicine'`, `enrichment/search_terms.py:323-324`) — a host, not a unit name.
- The street-address guard depends on `_ADDRESS_PATTERNS` recall (`enrichment/preprocess.py:158`); an unrecognised address form in `name2_original` becomes a search phrase.

---

### Terminal normalisation (`_normalise_term`, `_truncate_word_boundary` — enrichment/search_terms.py)

#### 1 Purpose
Applies the SAP SORT1/SORT2 field discipline to both terms: trimmed, single-spaced,
uppercase, at most 32 characters cut on a word boundary
(`enrichment/search_terms.py:403-410`).

#### 2 Inputs and outputs
`_normalise_term(term: str | None) -> str | None` (`enrichment/search_terms.py:403`);
`_truncate_word_boundary(s: str, width: int = 32) -> str` (`enrichment/search_terms.py:392`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:403-410`.
1. **if** `term` is None/blank → **return** `None` (lines 407-408).
2. `s ← re.sub(r"\s+", " ", term.strip()).upper()` (line 409).
3. **return** `_truncate_word_boundary(s, 32) or None` (line 410).

`_truncate_word_boundary` (`enrichment/search_terms.py:392-400`):
1. **if** `len(s) ≤ width` → **return** `s` (lines 396-397).
2. `idx ← s.rfind(" ", 0, width+1)` — last space at or before position `width` (line 398).
3. **if** `idx ≤ 0` → **return** `s[:width]` (single over-long word hard-cut, lines 398-399).
4. **return** `s[:idx].rstrip()` (line 400).

#### 4 Constants
`width = 32` — default parameter, documented as "SAP SORT1/SORT2 width"
(`enrichment/search_terms.py:392,405-406`).

#### 5 Complexity
O(|term|): one regex substitution, one uppercase pass, one reverse scan for the cut point.

#### 6 Worked example
`tests/test_search_terms_fixes.py:86-91`: input name2
`"  organic process chemistry and analytical technology development  "`.
Rule 2 of ST2: no prefix/suffix strip applies; `_fill_to_width` accumulates
`organic` (7) → `process` (7+1+7=15) → `chemistry` (15+1+9=25), skips stopword
`and`, stops before `analytical` (25+1+10=36 > 32) → `"organic process chemistry"`.
`_normalise_term` strips/uppercases → `"ORGANIC PROCESS CHEMISTRY"` (25 chars, no
truncation needed). Assertions: exact value, `== strip()`, `== upper()`,
`len ≤ 32` (`tests/test_search_terms_fixes.py:90-91`).
⚠ NO FIXTURE COVERAGE for the truncation branch itself (every fixture's term is
already ≤ 32 after fill; `_truncate_word_boundary`'s >32 path is untested).

#### 7 Failure modes
- Truncation can return an empty string only if `s[:idx].rstrip()` collapses, which line 410's `or None` converts to `None` — a term cannot be emitted as `""`.
- Word-boundary backoff searches only for ASCII space; a 33+-char hyphenated token is hard-cut mid-token (line 399).

---

### TLD stripping (`strip_tld` — enrichment/search_terms.py)

#### 1 Purpose
Inverts `utils.text_utils.extract_domain`: removes the (possibly two-part) TLD
from a host so a registrable domain becomes a bare name
(`enrichment/search_terms.py:56-58,70-87`).

#### 2 Inputs and outputs
`strip_tld(host: str | None) -> str | None` (`enrichment/search_terms.py:70`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:70-87`.
1. **if** blank → **return** `None` (lines 78-79).
2. `parts ← host.strip().lower().split(".")` (line 80).
3. **if** ≥3 parts and the last two joined are in `_TWO_PART_TLDS` → **return** join of `parts[:-2]` or `None` (lines 81-84).
4. **if** ≥2 parts → **return** join of `parts[:-1]` or `None` (lines 85-86).
5. **return** `parts[0] or None` (line 87) — an already-TLD-less label passes through.

#### 4 Constants
`_TWO_PART_TLDS = {"co.uk", "ac.uk", "org.uk", "ac.jp", "co.jp", "com.au",
"edu.au", "org.au", "ac.in", "co.in", "com.br", "org.br", "edu.br", "ac.nz",
"co.nz", "ac.za", "co.za"}` (`enrichment/search_terms.py:59-67`) — stated to
mirror the set inside `extract_domain` (`utils/text_utils.py:37-40`), which is
byte-identical.

#### 5 Complexity
O(|host|).

#### 6 Worked example
`tests/test_search_terms.py:20-35`: `"mit.edu"` → `"mit"` (rule 4);
`"example.co.uk"` → `"example"` (rule 3, two-part TLD);
`"cs.mit.edu"` → `"cs.mit"` (rule 4 — subdomains are kept, only the final label
drops); `"mit"` → `"mit"` (rule 5); `""`/`None` → `None`.

#### 7 Failure modes
- The two-part TLD list is closed; hosts under unlisted two-part suffixes (e.g. any not in the 17-entry set) lose only the final label, leaving the country code in the term.
- Rule 4 applies to *any* dotted string, so a non-host input still gets its last segment removed.

---

### Subdomain acronym (`_subdomain_acronym` — enrichment/search_terms.py)

#### 1 Purpose
Accepts the department-domain subdomain prefix as ST2 only when it is genuinely
an acronym of Name 2 — 2-6 characters, not a truncated word of any Name 2 token,
and letter-for-letter equal to Name 2's initials
(`enrichment/search_terms.py:442-476`).

#### 2 Inputs and outputs
`_subdomain_acronym(dept_domain: str | None, base_domain: str | None, name2: str | None) -> str | None`
(`enrichment/search_terms.py:442-444`). Returns the uppercased prefix or `None`.

#### 3 Pseudocode
Source: `enrichment/search_terms.py:442-476`.
1. **if** `dept_domain` or `name2` missing → **return** `None` (lines 453-454).
2. `host ← lower(strip(dept_domain))`; if it contains `"://"`, reduce to `urlparse(host).hostname` (lines 455-457).
3. Strip a leading `"www."` or `"web."` (lines 458-460).
4. `base ← lower(strip(base_domain)) or None`; **if** no base or host does not end with `"." + base` → **return** `None` (lines 461-463).
5. `prefix ← first dot-separated label of the part before "." + base` (line 464).
6. **if not** `2 ≤ len(prefix) ≤ 6` → **return** `None` (lines 465-466).
7. `core ← clean_name2_phrase(name2) or name2`; `tokens ←` alphabetic tokens of `core` not in `_TERM2_STOPWORDS` (lines 467-471).
8. **if** any token satisfies `seg_matches_needle(prefix, token)` → **return** `None` — the prefix is a truncated word ("chem" ← "chemistry"), not an acronym (lines 472-473).
9. **if not** `acronym_matches_name(prefix, core)` → **return** `None` — letters must equal Name 2's initials (lines 474-475).
10. **return** `prefix.upper()` (line 476).

#### 4 Constants
Bounds `2 ≤ len(prefix) ≤ 6` (line 465); `_TERM2_STOPWORDS` (below); prefix-strip
tuple `("www.", "web.")` (line 458).

#### 5 Complexity
O(|host| + |name2|): tokenisation plus one `seg_matches_needle` per token and one
initials comparison.

#### 6 Worked example
Accept: `tests/test_search_terms_fixes.py:56-57` (`eecs.mit.edu` + "Electrical
Engineering and Computer Science" → `EECS`; trace under ST2 §6 above).
Reject (truncated word): `tests/test_search_terms_fixes.py:54-55` (`chem.ufl.edu`
+ "Department of Chemistry" → `None` at step 8, so ST2 falls to the text phrase
`"CHEMISTRY"`). Reject (letters ≠ initials) is docstring-documented with
`york.cuny.edu` + "Department of Geology" (`enrichment/search_terms.py:451`);
⚠ NO FIXTURE COVERAGE for that specific branch (step 9) in the test suites.

#### 7 Failure modes
- Cross-domain department hosts never yield an acronym (step 4 requires subdomain-of-base).
- A genuine acronym longer than 6 letters is rejected by the length gate.
- `acronym_matches_name` requires an exact initials match against the *cleaned* Name 2; donor-name or reordered names defeat it, causing silent fall-through to the phrase rule.

---

### Width-limited word fill (`_fill_to_width` — enrichment/search_terms.py)

#### 1 Purpose
Greedily packs significant words of the cleaned Name 2 phrase into the 32-char
budget so the phrase survives terminal truncation intact
(`enrichment/search_terms.py:413-428`).

#### 2 Inputs and outputs
`_fill_to_width(text: str | None, width: int = 32) -> str | None`
(`enrichment/search_terms.py:413`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:413-428`.
1. **if** `text` falsy → **return** `None` (lines 417-418).
2. For each token matching `[A-Za-z0-9&\-]+` (line 420):
   a. skip tokens whose lowercase form is in `_TERM2_STOPWORDS` (lines 421-422);
   b. `add ← len(tok) + (1 if out else 0)` (space cost, line 423);
   c. **if** `out` non-empty and `length + add > width` → **break** (lines 424-425) — the first significant word is always included even if longer than `width`;
   d. append and accumulate (lines 426-427).
3. **return** `" ".join(out)` or `None` if nothing was kept (line 428).

#### 4 Constants
`width = 32` default (line 413); `_TERM2_STOPWORDS = {"of", "for", "the", "and",
"in", "on", "at", "to", "a", "an", "de", "du", "des", "la", "le", "les", "&"}`
(`enrichment/search_terms.py:306-309`).

#### 5 Complexity
O(|text|) single pass.

#### 6 Worked example
`tests/test_search_terms_fixes.py:93-95`: name2 = "Institute of Sustainable and
Environmental Chemistry". `clean_name2_phrase` strips prefix `"institute of"` →
"Sustainable and Environmental Chemistry". Fill: `Sustainable` (11); `and`
skipped; `Environmental` (11+1+13 = 25 ≤ 32, kept); `Chemistry` (25+1+9 = 35 >
32, break) → `"Sustainable Environmental"` → normalised
`"SUSTAINABLE ENVIRONMENTAL"`. The test names this an accepted imperfection
(`tests/test_search_terms_fixes.py:93`).

#### 7 Failure modes
- Greedy packing can drop the head noun (as above — "Chemistry" is lost), producing an adjective-only phrase.
- Stopword removal changes meaning-order ("Earth and Planetary Sciences" → "Earth Planetary Sciences", `tests/test_search_terms_fixes.py:65-66`).
- A first word longer than `width` is emitted whole and later hard-cut by `_truncate_word_boundary`.

---

### Unit-phrase test (`_name2_is_unit_phrase` — enrichment/search_terms.py)

#### 1 Purpose
Field-swap counterweight: returns True when Name 2 reads as a department/sub-unit
(unit prefix, unit suffix, or granular unit), so unit names containing
institution-like words are not nulled by the swap guard
(`enrichment/search_terms.py:431-439`).

#### 2 Inputs and outputs
`_name2_is_unit_phrase(name2: str) -> bool` (`enrichment/search_terms.py:431`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:431-439`.
1. `low ← name2.strip().lower()` (line 434).
2. **if** `low` equals or starts with `p + " "` for any `p ∈ _NAME2_PREFIXES` → **return** True (line 435-436).
3. **if** `low` equals or ends with `" " + s` for any `s ∈ _NAME2_SUFFIXES` → **return** True (lines 437-438).
4. **return** `is_granular_unit(name2)` (line 439).

#### 4 Constants
`_NAME2_PREFIXES = ("department of", "dept of", "dept.", "division of", "div of",
"div.", "school of", "institute of", "inst of", "inst.", "center for",
"centre for", "faculty of", "office of", "group of", "laboratory of", "lab of")`
(`enrichment/search_terms.py:93-111`).
`_NAME2_SUFFIXES = ("department", "division", "school", "institute", "centre",
"center", "laboratory", "lab", "group")` (`enrichment/search_terms.py:116-126`).

#### 5 Complexity
O(|name2|) — constant-size list scans plus one `is_granular_unit` call (regex passes).

#### 6 Worked example
Exercised only through the swap guard: `tests/test_search_terms_fixes.py:71-77`
uses `name2_enriched="Tufts University"` — no prefix/suffix match, and
`is_granular_unit("Tufts University")` is False (no granular head construction,
`utils/text_utils.py:461-474`) — so the phrase is *not* a unit, the swap guard
fires, ST2 is None and the record is flagged. ⚠ NO FIXTURE COVERAGE for the
True branches (a unit phrase that also matches the research-institution regex,
e.g. a "School of …" Name 2, is not in either test file).

#### 7 Failure modes
- Prefix matching is exact-string, so "The Department of X" (leading article) misses the prefix branch, though the suffix/granular branches may still catch other forms.
- `is_granular_unit` first expands abbreviations (`utils/text_utils.py:429`), so its verdict depends on the abbreviation table `_COMPILED_ABBREVS` (built near `utils/text_utils.py:204-215`).

---

### Department-domain fallback (`_dept_domain_to_search_term` — enrichment/search_terms.py)

#### 1 Purpose
Last-resort ST2: reduces the department-domain host to a compact handle — the
subdomain prefix relative to the institution base, else the TLD-stripped host
(`enrichment/search_terms.py:312-342`).

#### 2 Inputs and outputs
`_dept_domain_to_search_term(dept_domain: str | None, base_domain: str | None) -> str | None`
(`enrichment/search_terms.py:312-314`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:312-342`.
1. **if** blank → **return** `None` (lines 326-327).
2. `host ← lower(strip(dept_domain))`; if it contains `"://"` (path-based dept page delivered as full URL), reduce to `urlparse(host).hostname`; **if** empty after that → **return** `None` (lines 328-334).
3. Strip one leading `"www."` or `"web."` prefix (lines 335-337).
4. `base ← lower(strip(base_domain)) or None`; **if** `base` and `host` ends with `"." + base` → **return** the stripped prefix (or `None` if empty) (lines 338-341).
5. **return** `strip_tld(host) or host` (line 342) — cross-domain hosts lose only their TLD.

#### 4 Constants
Prefix tuple `("www.", "web.")` (line 335).

#### 5 Complexity
O(|host|).

#### 6 Worked example
⚠ NO FIXTURE COVERAGE for this function *as the deciding rule*: in every suite
fixture with a `department_domain`, Name 2 is present and rule 2 wins first
(explicit comment "name2 text now beats the dept-domain host",
`tests/test_search_terms.py:233`). Behavioural examples exist only in the
docstring (`enrichment/search_terms.py:320-324`): `'cs.mit.edu'`+`'mit.edu'` →
`'cs'`; `'web.astro.princeton.edu'` → `'astro'`; `'hopkinsmedicine.org'`+
`'jhu.edu'` → `'hopkinsmedicine'`.

#### 7 Failure modes
- Prefix stripping tests `"www."` then `"web."` exactly once each, in that fixed order, against the progressively shortened host (lines 335-337). Hence `"www.web.x.edu"` loses both prefixes, but the reverse stacking `"web.www.x.edu"` loses only `"web."` (the `"www."` test has already passed by the time it is exposed). ⚠ NO FIXTURE COVERAGE for stacked prefixes; the single-`web.` case is docstring-documented (`enrichment/search_terms.py:322`).
- A dept "domain" that is only a path on the base domain (e.g. `https://ufl.edu/departments/biology`) reduces to the bare base host, which step 4 strips to empty → `None` returned via the `or None` at line 341? No — `host == base` does not end with `"." + base`, so step 5 returns `strip_tld(base)`, the *institution* name, as the unit handle. ⚠ NO FIXTURE COVERAGE (the analogous fixture `tests/test_search_terms_fixes.py:60-62` resolves via the Name 2 rule instead).

---

### Name 2 phrase cleaning (`clean_name2_phrase` — enrichment/search_terms.py)

#### 1 Purpose
Strips one generic unit prefix and one generic unit suffix from Name 2 and
title-cases the remainder, preserving all-uppercase tokens
(`enrichment/search_terms.py:218-253`).

#### 2 Inputs and outputs
`clean_name2_phrase(name2: str | None) -> str | None` (`enrichment/search_terms.py:218`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:218-253`.
1. **if** blank → **return** `None` (lines 228-229).
2. `core ← name2.strip()`; `lowered ← core.lower()` (lines 231-232).
3. For each `prefix ∈ _NAME2_PREFIXES` in declared order (longer/more-specific first, comment lines 90-92): if `lowered` starts with `prefix + " "` → cut the prefix, re-lower, **break**; if `lowered == prefix` → `core ← ""`, **break** (lines 234-241).
4. For each `suffix ∈ _NAME2_SUFFIXES`: if `lowered` ends with `" " + suffix` → cut it, **break**; if `lowered == suffix` → `core ← ""`, **break** (lines 243-249).
5. **if** `core` empty → **return** `None` (lines 251-252).
6. **return** `_title_case_preserve_acronyms(core)` (line 253) — words of length ≥2 that are all-uppercase are kept verbatim; every other word gets first-letter-upper, rest-lower (`enrichment/search_terms.py:168-181`).

#### 4 Constants
`_NAME2_PREFIXES`, `_NAME2_SUFFIXES` — verbatim under `_name2_is_unit_phrase` §4
(`enrichment/search_terms.py:93-126`).

#### 5 Complexity
O(|name2|) — bounded list scans and one title-case pass.

#### 6 Worked example
`tests/test_search_terms.py:65-101`: `"Department of Computer Science"` →
`"Computer Science"`; `"Theoretical Physics Department"` → `"Theoretical
Physics"` (suffix branch); `"dept of chemistry"` → `"Chemistry"` (title-casing);
`"MRI lab"` → `"MRI"` (suffix cut + acronym preserved); `"Department of"` →
`None` (bare-prefix branch, line 239-240); `"Analytical Sciences"` returned
verbatim (title-cased).

#### 7 Failure modes
- Exactly one prefix and one suffix are stripped ("Department of School of X" keeps the inner construction).
- Suffix stripping is blind to meaning: `"Candelario Lab"` → `"Candelario"` (`tests/test_search_terms.py:84-85`) — a surname becomes the phrase.
- Note the deliberate divergence from `extract_dept_core` (`enrichment/search_terms.py:196-215`): `extract_dept_core` *searches* for "…of" anywhere (donor-name strip), while `clean_name2_phrase` anchors at the string start; only the latter feeds ST2 (line 550); `extract_dept_core` feeds the orchestrator's department probe (`enrichment/orchestrator.py:1050`).

---

### Name 1 text handle (`_name1_text_handle` — enrichment/search_terms.py)

#### 1 Purpose
Builds the required ST1 fallback from Name 1: its first two significant words,
dropping stopwords and legal-entity suffixes (`enrichment/search_terms.py:378-389`).

#### 2 Inputs and outputs
`_name1_text_handle(name1: str) -> str | None` (`enrichment/search_terms.py:378`).

#### 3 Pseudocode
Source: `enrichment/search_terms.py:378-389`.
1. For each token matching `[A-Za-z0-9&]+` in `name1` (line 382):
   a. skip if lowercase form ∈ `_TERM2_STOPWORDS` or ∈ `_ST1_LEGAL_SUFFIXES` (lines 383-385);
   b. append; **break** after 2 kept words (lines 386-388).
2. **return** the joined words, or `None` if none kept (line 389).

#### 4 Constants
`_ST1_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp",
"corporation", "co", "company", "gmbh", "ag", "plc", "lp", "llp", "pllc", "sa",
"srl", "bv", "nv", "se", "pty", "oy", "ab", "as"}`
(`enrichment/search_terms.py:371-375`); comment records that industry words
("Diagnostics", "Biotech") are deliberately kept (lines 369-370).

#### 5 Complexity
O(|name1|).

#### 6 Worked example
`tests/test_search_terms_fixes.py:36-38`: `"Verdox, Inc."` → `"VERDOX"` (suffix
`inc` dropped, single word kept); `"Silverline Biotech"` → `"SILVERLINE
BIOTECH"`; `"Precision Diagnostics"` → `"PRECISION DIAGNOSTICS"`. Also
`tests/test_search_terms.py:332-343`: `"International Business Machines"` →
`"INTERNATIONAL BUSINESS"` (two-word cap drops "Machines").

#### 7 Failure modes
- The two-word cap is positional: distinguishing words beyond position 2 are lost.
- Legal-suffix filtering is token-set membership anywhere in the name, so a company genuinely named e.g. with the bare word "Co" mid-name loses that token.

---

### Shared `utils/text_utils.py` helpers used by this module

#### is_admin_unit (utils/text_utils.py:990-1011)
Returns True when the text names an administrative/back-office desk. Steps:
blank → False (lines 998-999); lowercase-strip (line 1000); strip the *first*
matching prefix from `_ADMIN_PREFIXES` (lines 1001-1004); replace every char
outside `[a-z/& ]` with a space and collapse whitespace (lines 1005-1006);
membership test in `_ADMIN_UNIT_TERMS` (lines 1007-1008); additionally accept
`"a/p"` and `"a/r"` (lines 1009-1010). Constants verbatim:
`_ADMIN_UNIT_TERMS = {"accounts payable", "accounts receivable", "ap", "ar",
"finance", "financial services", "billing", "invoicing", "invoice processing",
"purchasing", "procurement", "controlling", "treasury", "bursar", "comptroller",
"general accounting", "shared services"}` (`utils/text_utils.py:977-983`);
`_ADMIN_PREFIXES = ("office of ", "department of ", "dept of ", "dept. ",
"dept ", "division of ", "div of ", "div. ")` (`utils/text_utils.py:984-987`).
Because the whole residual string must equal a term, "Office of Finance" is admin
but "Office of Research" is not (fixtures `tests/test_search_terms_fixes.py:136-143`;
"Office of Research" also flows to ST2 `"RESEARCH"` at
`tests/test_search_terms_fixes.py:53`). English-only by design (comment
`utils/text_utils.py:974,992`). The same predicate gates the orchestrator's
department-domain probe skip (`enrichment/orchestrator.py:1017-1025`).

#### is_granular_unit (utils/text_utils.py:410-474)
True when the text names a unit below UC 5 scope (labs, groups, centres,
facilities). Steps: blank → False (427-428); `expand_abbreviations` first
(line 429; that helper applies the compiled abbreviation substitutions,
`utils/text_utils.py:204-215`); early False for in-scope heads matching
`^(?:department|division|school|college|faculty)\s+(?:of|for)\s+` (435-439);
suffix form `\b\S+\s+{word}\b\.?$` for each of
`granular_words = ["laboratory", "laboratories", "lab", "facility",
"facilities", "center", "centre", "core"]` plus suffix-only extras
`["group", "unit", "program", "programme"]` (447-466); prefix form
`^{word}\s+(?:of|for)\s+` for `granular_words` only (470-472); else False (474).
Reached from `_name2_is_unit_phrase` (`enrichment/search_terms.py:439`).

#### looks_like_research_institution (utils/text_utils.py:366-377)
True when `_RESEARCH_NAME_SIGNALS_RE` matches anywhere, case-insensitively.
Regex verbatim (`utils/text_utils.py:355-363`):
`\b(?:University|College|Institute|Hospital|Clinic|Research|Medical\s+School|School\s+of|Faculty\s+of|College\s+of|Laboratory|Observatory|Academy|Health\s+System|Health\s+Center|Regional\s+Health|Medical\s+Center|Cancer\s+Center|Schule|Universit[aä]t|Université|Universidade)\b`.
Used by the ST2 field-swap guard (`enrichment/search_terms.py:525`).

#### acronym_matches_name and name_initials (utils/text_utils.py:927-954)
`name_initials`: uppercase initials of the name's tokens (matched by
`_INITIALS_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&\-]*")`,
`utils/text_utils.py:924`), skipping `_INITIALS_STOPWORDS = {"of", "for", "the",
"and", "in", "on", "at", "to", "a", "an", "de", "du", "des", "la", "le", "les",
"&"}` (`utils/text_utils.py:920-923`) and any token whose first char is not
alphabetic (lines 936-941). `acronym_matches_name`: False on blanks; extracts
the acronym's alphabetic chars uppercased and requires exact equality with
`name_initials(name)` (lines 951-954). Fixtures:
`tests/test_search_terms_fixes.py:99-107` ("NIST" matches, historical "NBS" does
not; "UF" matches "University of Florida").

#### seg_matches_needle (utils/text_utils.py:957-971)
Matches a host segment against a token: lowercase both; blank → False; True if
`needle in seg` (substring); else True iff `min(len(seg), len(needle)) >= 3` and
one is a leading prefix of the other (lines 962-971). This is the
truncated-word detector in `_subdomain_acronym` (`enrichment/search_terms.py:472-473`);
docstring notes it is shared with the department probe (`utils/text_utils.py:960`).

#### _extract_addresses (enrichment/preprocess.py:243-257)
Not in text_utils but imported by this module (`enrichment/search_terms.py:27`).
Repeatedly searches each pattern in `_ADDRESS_PATTERNS`
(`enrichment/preprocess.py:158`) against the text, collecting matched fragments
(stripped of `" ,;.:"`) and deleting them, then collapses whitespace and strips
`" ,;/|-"` from the remainder; returns `(fragments, remainder)`
(`enrichment/preprocess.py:243-257`). ST2 uses only the predicate "found
addresses and empty remainder" (`enrichment/search_terms.py:516-518`).

---

### Dead helper: `unit_domain_or_path` (enrichment/search_terms.py:256-303)

`unit_domain_or_path(source_url, base_domain)` derives a unit handle from a
source URL: subdomain prefix when the host is a subdomain of the base
(lines 285-288), TLD-stripped host for a foreign registrable domain
(lines 289-291), else the first non-generic path segment matching
`^[a-z0-9][a-z0-9\-]*$` prefixed with `/` (lines 293-301), skipping
`_GENERIC_PATH_SEGMENTS = {"about", "people", "faculty", "staff", "directory",
"news", "contact", "departments", "dept", "home", "index", "search", "page",
"pages", "en", "us"}` (`enrichment/search_terms.py:45-49`).

It is exported and unit-tested (`tests/test_search_terms.py:104-130`) but has
**no caller in application code**: a repository-wide search finds only its
definition (`enrichment/search_terms.py:256`), the tests, and an inventory note
recording the same finding (`docs/thesis/00_INVENTORY.md:320-321`). Its role in
the ST2 chain is superseded by `_dept_domain_to_search_term`
(`enrichment/search_terms.py:312-342`), which is the live rule-3 fallback
(`enrichment/search_terms.py:555-557`) and works from the probe-written
`department_domain` rather than `source_url`. A second uncalled sibling exists:
`_first_two_significant_words` (`enrichment/search_terms.py:345-365`) likewise
has no caller anywhere in the repository; its role is covered by `_fill_to_width`
(`enrichment/search_terms.py:413-428`).

`extract_dept_core` and `derive_acronym` are *not* dead: both are consumed by the
orchestrator's department-domain probe (`enrichment/orchestrator.py:1050,1053`),
though neither participates in `derive_search_terms` itself.

---

### Non-determinism notes

The search-term subsystem is **fully deterministic**:

- `enrichment/search_terms.py` imports only `re`, `typing.Any`,
  `urllib.parse.urlparse`, `enrichment.preprocess._extract_addresses`, and five
  pure helpers from `utils.text_utils` (`enrichment/search_terms.py:21-34`). No
  HTTP client, no LLM client, no randomness, no clock, and no I/O appear
  anywhere in the file's 586 lines (read in full).
- Every `utils/text_utils.py` helper on the import list is pure string/regex
  computation: `acronym_matches_name` (`utils/text_utils.py:944-954`),
  `is_admin_unit` (990-1011), `is_granular_unit` (410-474, including its call to
  the regex-table `expand_abbreviations`, 204-215),
  `looks_like_research_institution` (366-377), `seg_matches_needle` (957-971).
  `utils/text_utils.py` does import `rapidfuzz.fuzz` at module level
  (`utils/text_utils.py:8`), but none of the five imported helpers calls it —
  fuzzy scoring is used elsewhere in that module only.
- `_extract_addresses` is pure regex over its argument
  (`enrichment/preprocess.py:243-257`).
- The only stateful behaviour is the in-place flag write on the caller's dict
  (`enrichment/search_terms.py:527-532`), which is itself deterministic in the
  inputs.
- Consequently, `(search_term_1, search_term_2)` is a pure function of the
  result-dict fields listed in §2 of `derive_search_terms`; any LLM or network
  influence enters only upstream, through the values of those fields (e.g.
  `_ror_acronym` from the ROR tier, `department_domain` from the probe —
  populated before the single call site at `enrichment/orchestrator.py:600`).
  The acceptance suite states the same premise: "Pure functions, fixed inputs"
  (`tests/test_search_terms_fixes.py:1-2`).


# Part G — Late address stage

All paths are relative to the repository root `enrichment_api/`. The stage is implemented in `enrichment/address_processing.py` (1219 lines), invoked from `enrichment/orchestrator.py`, and uses helpers from `utils/text_utils.py` and `enrichment/preprocess.py`.

---

### Address-stage invocation (_run_address_stage — enrichment/orchestrator.py)

#### 1 Purpose

Runs the deterministic address cleanup (Address Stage 1) on every record's return path — including unresolved and failed name-enrichment records — after website resolution and the department-URL probe, and immediately before `finalise()` (enrichment/orchestrator.py:1550-1573, 1575-1583). Address-stage exceptions are swallowed so the name-enrichment result always surfaces (enrichment/orchestrator.py:1616-1621).

#### 2 Inputs and outputs

**Inputs.** The in-progress result dict and the source `EnrichmentRecord` (enrichment/orchestrator.py:1575-1579). Field sourcing:

- Name and care-of inputs come from `_pick(base)` = `result[f"{base}_enriched"] or result[f"{base}_original"]` (enrichment/orchestrator.py:1584-1585).
- Street inputs come from `_street(base)`: if the preprocessing stage recorded post-preprocess street values in `result["_pp_streets"]` and the slot is present there, that value is used *even when empty*, so a slot cleared by preprocessing stays cleared; otherwise `_pick(base)` (enrichment/orchestrator.py:1587-1595).
- `city`, `state`, `zip`, `country`, `po_box` come directly from the raw record (enrichment/orchestrator.py:1608-1612).
- `llm_client` is the orchestrator's OpenAI client (enrichment/orchestrator.py:1614).

**Outputs.** None directly; on success the returned `AddressResult` is merged into the result dict via `merge_address_into_result` — the import alias for `merge_into_result` (enrichment/orchestrator.py:32-36, 1622). On exception the function logs a warning and returns without modifying the result (enrichment/orchestrator.py:1616-1621).

#### 3 Pseudocode

Source: enrichment/orchestrator.py:1575-1622.

1. Define `_pick(base)` → enriched-or-original value (1584-1585).
2. Read `pp_streets = result.get("_pp_streets")`; define `_street(base)` → `pp_streets[base]` if the key exists, else `_pick(base)` (1590-1595).
3. `try:` call `await process_address(...)` with record_id, names 1-3 via `_pick`, streets 1-5 via `_street`, record city/state/zip/country/po_box, `care_of_enriched=_pick("care_of")`, and the LLM client (1597-1615).
4. `except Exception`: log warning `"Address Stage 1 failed for %s"` and **return early** — the result dict is untouched (1616-1621).
5. `merge_address_into_result(result, addr)` (1622).

Position in the pipeline (`_finalise_and_return`, enrichment/orchestrator.py:1550-1573): website B/C resolution (1559) → domain derivation from website (1566-1569) → department-URL probe (1570) → `_run_address_stage` (1571) → `finalise` (1572).

#### 4 Constants

None defined in this wrapper.

#### 5 Complexity

O(1) beyond the cost of `process_address`; one call per record.

#### 6 Worked example

⚠ NO FIXTURE COVERAGE — no test exercises `_run_address_stage` itself; tests call `process_address` and `merge_into_result` directly (e.g. tests/test_street_in_name.py:88-99).

#### 7 Failure modes

- Any exception raised inside `process_address` is swallowed; the record then carries no address-stage outputs at all (enrichment/orchestrator.py:1616-1621).
- If `_pp_streets` is absent, street inputs fall back to enriched-then-original values, so junk removed only in memory by preprocessing could reappear; `process_address` re-scrubs for this reason (enrichment/address_processing.py:925-935).

---

### Address Stage 1 entry point (process_address — enrichment/address_processing.py)

#### 1 Purpose

Single entry point of the late address stage: cleans the five street slots, extracts structured sub-values (PO Box, c/o, logistics, mail codes, sub-locations), reduces a mixed primary street to one line per concern, cross-checks fields (flag-only), optionally LLM-classifies ambiguous residuals in secondary slots, normalises abbreviations, dedupes and left-packs the street outputs (enrichment/address_processing.py:1-24, 895-920). It issues no network call except the optional LLM residual classification (enrichment/address_processing.py:4-8).

#### 2 Inputs and outputs

**Inputs** (keyword-only, enrichment/address_processing.py:895-913): `record_id`, `name1/name2/name3`, `street`, `street_2`…`street_5`, `city`, `state`, `zip_code`, `country`, `po_box`, `care_of_enriched`, `llm_client` (`OpenAIClient | None`).

**Output.** An `AddressResult` dataclass (enrichment/address_processing.py:84-122) with fields it may write: `street_cleaned`, `street_2_cleaned` … `street_5_cleaned`, `suite`, `building`, `floor`, `room`, `unit`, `mail_stop`, `po_box_extracted`, `care_of_enriched`, `unloading_point`, `mail_code`, `department_addendum`, `city_inferred`, `state_inferred`, `zip_inferred`, `unclear_address_info`, `name_overrides` (dict of name-field rewrites), and `address_issues` (deduplicated list of codes via `issue()`, enrichment/address_processing.py:120-122).

#### 3 Pseudocode

Source: enrichment/address_processing.py:921-935 (init + Step 1).

1. Create `res = AddressResult()`; seed `res.care_of_enriched` from the input when non-blank (921-923).
2. **Step 1 — clean + scrub.** For each of the five street inputs: `slots[k] = _scrub_street(_clean(value))` (929-935).
   - `_clean` (137-156): replace tabs/non-breaking spaces with spaces; collapse `\.{2,}` → `.` (`_STRAY_DOTS_RE`, 129); collapse whitespace (`_MULTI_SPACE_RE`, 130); strip `",;|"`; strip trailing `,`/`;` repeatedly; strip leading/trailing lone dashes; strip a leading connector word via `_LEADING_CONNECTOR_RE` (`^(?:also|and|or|plus|&)\b[\s:,\-]*`, IGNORECASE, 134); return `None` when empty.
   - `_scrub_street` (159-178): return `None` when the whole value is a title-prefixed person reference (`_street_person_name`, enrichment/preprocess.py:603-609, using `_STREET_PERSON_RE` 585-591 with the end-anchored street-suffix guard `_STREET_SUFFIX_GUARD_RE` 596-600 so person-named streets like "Dr Martin Luther King Jr Blvd" are not dropped); else delete URLs, phone/fax numbers, and emails via `_URL_RE` (enrichment/preprocess.py:533-540), `_PHONE_RE` (543-548), `_EMAIL_RE` (115-117), then `_strip_residue` (enrichment/address_processing.py:191-196).

Source: enrichment/address_processing.py:937-965 (Step 1b — street address in a name field).

3. **Step 1b.** For `name2` then `name3` (Name 1 is never touched, 942-943): run `_extract_addresses` (enrichment/preprocess.py:243-257, using the 6-pattern `_ADDRESS_PATTERNS` list, enrichment/preprocess.py:158-197). If fragments were found, place each into the first empty slot in order `s1…s5` (`slots[_target] = _scrub_street(_clean(_addr)) or _addr`, 951-957). Only when *every* fragment found a slot is the name field rewritten: `res.name_overrides[field] = cleaned or None` and the local name variable updated (958-965); otherwise the name is left intact so no address text is silently dropped.

Source: enrichment/address_processing.py:967-978 (Step 2a — full address in one field).

4. **Step 2a.** If `slots["s1"]` matches `_FULL_ADDRESS_RE` = `^(.+?),\s*([A-Za-z\s]+?),?\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$` (435-437) via `_split_full_address` (440-459): keep only the street part in `s1`; set `city_inferred` / `state_inferred` / `zip_inferred` only for record fields that are blank (970-977).

Source: enrichment/address_processing.py:979-998 (Step 2a.5 — scope-table reduction).

5. **Step 2a.5.** Call `_reduce_primary_street(res, slots["s1"], city, state, zip_code)` (988-990; documented as its own procedure below). When it returns a list, rebuild the slots: the returned ordered street lines, followed by the previously populated secondary slots, are written into `s1…s5` in order; any overflow beyond five lines raises issue `G3-ADDR-011` (991-998). When it returns `None`, the slots are unchanged (simple case).

Source: enrichment/address_processing.py:1000-1064 (Step 2b — per-slot extractors).

6. **Step 2b.** For each slot `s1…s5` with a value:
   1. If the whole slot is a named building (`_named_building_value`, below) and `res.building` is still `None`: `res.building = smart_title_case(nb) or nb`, blank the slot, `continue` (1009-1013).
   2. PO Box: `_extract_po_box` (205-213) removes the first `_PO_BOX_RE` match (`\b(?:P\.?\s*O\.?\s*Box|POB|Post\s+Office\s+Box)\s+(\w+)\b`, IGNORECASE, 199-202). If a PO box was already present (input `po_box` or a previous extraction) → issue `G3-ADDR-005`; else store it and mark `po_box_present` (1019-1025).
   3. c/o / Attn: `_extract_care_of` (343-363) searches `_CARE_OF_RE` = `(?:c\s*/\s*o|att?n+(?:ention|tion)?|ATT)\s*[:\-\.]?\s*(.+)` (IGNORECASE, 326-329; `att?n+` also catches "Atnn"/"attnn"). If a street address starts inside the payload (`_STREET_START_RE`, 508-511), only the leading part becomes the c/o value and the street tail is returned to the working string (354-360). Routing (1030-1041): a payload matching `_DEPARTMENT_PAYLOAD_RE` = `\b(?:Department|Dept\.?|Division|Div\.?)\b` (333-336, via `_looks_like_department` 339-340) goes to `department_addendum`; a *different* c/o when one already exists is appended with `" | "`; otherwise it becomes `care_of_enriched`.
   4. Logistics: `_extract_logistics` (377-394) — first a `Deliver to:` payload (`_DELIVER_TO_RE`, 371-374); then a keyword-at-start whole-value match (`_LOGISTICS_KEYWORD_RE` = `^\s*(?:Loading\s+Dock|Dock|Gate|Warehouse|Shipping|Receiving)(?:\s*[:\-]?\s*(.+))?$`, 366-370); then `is_logistics_location` (utils/text_utils.py:668-670) which classifies the whole value. First hit fills `unloading_point` (1044-1046).
   5. Mail code: `_extract_mail_code(work, allow_bare = slot != "s1")` (412-432; call at 1050-1051). Priority: explicit `MAIL CODE: X` (`_MAIL_CODE_EXPLICIT_RE`, 397-400) → complex form `[A-Z]\d-\d{4}` (`_MAIL_CODE_COMPLEX_RE`, 401) → in secondary slots only, a bare all-caps token `[A-Z]{2,4}\d{1,4}` (`_MAIL_CODE_BARE_RE`, 405) that is not one of the 17 street-type abbreviations in `_STREET_TYPE_ABBREVS` (406-409). First hit fills `mail_code` (1052-1053).
   6. Sub-locations: `_extract_sublocations` (documented below); each found target fills the corresponding `AddressResult` field only if still `None`; a bare marker raises `G4-ADDR-008` (1056-1061).
   7. Persist `_strip_residue(work) or None` back to the slot (1064).
7. **Step 3 — cross-field checks** (flag-only), `_cross_field_checks` (611-650, call at 1067-1075): duplicate street 1/2 → `G3-ADDR-012` (621-626); both street 1 and 2 look like real addresses (`_looks_like_street` = house number **and** street-type word, 538-544) → `G3-ADDR-013` (629-630); PO box plus populated street → `G3-ADDR-014` (633-634); a name field matching `_NAME_STREET_LIKE_RE` = `\b\d+\s+\w+\s+(?:St|Ave|Blvd|Rd|Dr|Ln|Hwy|Pkwy)\b\.?` (486-489) → `G1-CROSS-001` (637-640); street 1 containing an org keyword (`_ORG_KEYWORD_RE`, 472-477) without any street-type word, after removing "University Centre" phrases (`_UNIVERSITY_CENTRE_RE`, 482-485) → `G1-CROSS-002` (644-650).
8. **Step 4 — LLM residual classification** on `s2…s5` via `_apply_residual_llm` (702-759, call at 1079-1088). Skipped entirely when `llm_client is None` (715-716). Per non-empty slot that does not already look like a street (`_looks_unambiguous` = `_looks_like_street`, 695-699): `_classify_residual` (660-692) calls `llm_client.extract_json` with `ADDRESS_RESIDUAL_SYSTEM_PROMPT` / `ADDRESS_RESIDUAL_USER_PROMPT_TEMPLATE` (llm/prompts.py:294-299, 301-306+), `max_tokens=200`; any exception → `(None, 0.0)` (677-683). Classification below `_RESIDUAL_CONFIDENCE_THRESHOLD = 0.85` (657) or `None` → issue `G1-ADDR-009` only (726-731). At/above threshold (733-757): `DEPARTMENT` → issue `G1-ADDR-011`, first fills `department_addendum`, slot blanked; `PERSON_NAME` → `G1-CROSS-003`; `ORG_NAME` → `G1-CROSS-002`; `MAIL_CODE` → first fills `mail_code`, slot blanked; `LOGISTICS` → first fills `unloading_point`, slot blanked; `STREET_ADDRESS` → no-op; `UNCLEAR` → `G1-ADDR-009` + first fills `unclear_address_info`; any other label → `G1-ADDR-009`.
9. **Step 4b — qualifier split** (1090-1105): for each populated slot in order, `_split_location_qualifier` (documented below); on a split, the street part replaces the slot and the qualifier goes to the first empty slot; if no slot is free the value is left combined (1101-1105).
10. **Step 5 — normalisation** (1107-1112): each slot passes through `_normalise_street_value` (781-794), which substitutes whole-word street-type tokens per `STREET_TYPE_ABBREVIATIONS` (`_STREET_TYPE_NORMALISE_RE`, 766-768) and directional words per `DIRECTIONAL_ABBREVIATIONS` — directionals only at the start (`_DIRECTIONAL_START_RE`, 771-774) or end (`_DIRECTIONAL_END_RE`, 775-778) of the value.
11. **Step 6 — dedupe** (1114-1130): walking `street_cleaned … street_5_cleaned` in order, a slot whose whitespace-collapsed lowercase form was already seen is blanked and `G3-ADDR-012` is issued.
12. **Step 7 — left-pack** (1132-1140): non-empty street values are compacted upward preserving relative order; trailing slots become `None`.
13. Log a summary dict and return `res` (1142-1152).

#### 4 Constants

`STREET_TYPE_ABBREVIATIONS` — 20 keys → 10 canonical values, verbatim (enrichment/address_processing.py:59-70):

```python
STREET_TYPE_ABBREVIATIONS: dict[str, str] = {
    "STREET": "St", "STR": "St",
    "AVENUE": "Ave", "AVE": "Ave",
    "BOULEVARD": "Blvd", "BLVD": "Blvd",
    "DRIVE": "Dr", "DR": "Dr",
    "ROAD": "Rd", "RD": "Rd",
    "LANE": "Ln", "LN": "Ln",
    "COURT": "Ct", "CT": "Ct",
    "HIGHWAY": "Hwy", "HWY": "Hwy",
    "PARKWAY": "Pkwy", "PKWY": "Pkwy",
    "ROUTE": "Rte", "RT": "Rte",
}
```

`DIRECTIONAL_ABBREVIATIONS` — 8 keys, verbatim (enrichment/address_processing.py:72-77):

```python
DIRECTIONAL_ABBREVIATIONS: dict[str, str] = {
    "NORTHWEST": "NW", "NORTHEAST": "NE",
    "SOUTHWEST": "SW", "SOUTHEAST": "SE",
    "NORTH": "N", "SOUTH": "S",
    "EAST": "E", "WEST": "W",
}
```

`_STREET_TYPE_ABBREVS` — 17 members, verbatim (enrichment/address_processing.py:406-409):

```python
_STREET_TYPE_ABBREVS = {
    "ST", "AVE", "BLVD", "DR", "RD", "LN", "CT", "HWY", "PKWY", "RTE",
    "STR", "PL", "TER", "PKY", "CIR", "SQ", "WAY",
}
```

Other constants: `_RESIDUAL_CONFIDENCE_THRESHOLD = 0.85` (657); `_PO_BOX_RE` (199-202); `_CARE_OF_RE` (326-329); `_DEPARTMENT_PAYLOAD_RE` (333-336); `_LOGISTICS_KEYWORD_RE` (366-370); `_DELIVER_TO_RE` (371-374); `_MAIL_CODE_EXPLICIT_RE` (397-400); `_MAIL_CODE_COMPLEX_RE` = `\b([A-Z]\d-\d{4})\b` (401); `_MAIL_CODE_BARE_RE` = `\b([A-Z]{2,4}\d{1,4})\b` (405); `_FULL_ADDRESS_RE` (435-437); `_LEADING_CONNECTOR_RE` (134).

Issue codes issued by the stage: `G3-ADDR-005`, `G3-ADDR-011`, `G3-ADDR-012`, `G3-ADDR-013`, `G3-ADDR-014`, `G4-ADDR-008`, `G1-ADDR-009`, `G1-ADDR-011`, `G1-CROSS-001`, `G1-CROSS-002`, `G1-CROSS-003` (enrichment/address_processing.py:626-650, 727-757, 891, 998, 1022, 1061, 1128).

#### 5 Complexity

Per record: at most 5 street slots. Per populated slot, Step 2b applies a constant battery of regex passes: 1 PO-box regex, 1 care-of regex (plus 1 street-start regex on its payload), 3 logistics checks, up to 3 mail-code regexes (the bare form iterates matches), and the 13-entry sub-location table where each pattern loops until it stops matching (enrichment/address_processing.py:1000-1064, 299-312). Step 1b runs the 6 preprocessing address patterns over at most 2 name fields (943-965; enrichment/preprocess.py:158-197). `_reduce_primary_street` is linear in the number of comma/pipe segments of street 1, with ≤ 9 branch tests per segment (824-888). The LLM step issues at most 4 sequential model calls per record (one per non-empty ambiguous secondary slot, 718-723). All other steps are linear in total street text length; overall the deterministic part is O(P·L) with P the fixed pattern count and L the field length.

#### 6 Worked example

From tests/test_address_cleanup.py:122-128 (`test_attn_person_then_street_split`): input street 1 = `"Att. Bayard Huck 200 Clarendon Street 22nd Floor"`, city "Boston", state "MA", zip "02210", `llm_client=None` (76-82).

1. `_clean`/`_scrub_street` leave the value unchanged (no junk, not a whole-value person reference).
2. `_split_full_address`: no `, City, ST zip` tail → no change (enrichment/address_processing.py:967-978).
3. `_reduce_primary_street`: no comma/pipe → 1 segment → returns `None` (824-826).
4. `_extract_care_of`: `_CARE_OF_RE` matches at `Att`; payload `"Bayard Huck 200 Clarendon Street 22nd Floor"`; `_STREET_START_RE` finds `"200 Clarendon Street"` at offset > 0, so `care = "Bayard Huck"` and the tail returns to the working string (354-360) → `res.care_of_enriched = "Bayard Huck"` (1041).
5. `_extract_sublocations`: value-before-marker floor pattern (228) matches `"22nd Floor"` → `floor = "22"`; remainder `"200 Clarendon Street"` (1056-1059).
6. `_normalise_street_value`: `Street` → `St` (766-788).

Asserted outputs: `care_of_enriched == "Bayard Huck"`, `street_cleaned == "200 Clarendon St"`, `floor == "22"` (tests/test_address_cleanup.py:126-128).

Step 1b example from tests/test_street_in_name.py:40-46: `name2="104 Rhines Hall"`, `street="549 GALE LEMERAND Dr"` → the numbered-hall pattern (enrichment/preprocess.py:169-172) pulls `"104 Rhines Hall"` out of Name 2 into the first empty slot (`s2`), yielding `street_cleaned == "549 GALE LEMERAND Dr"`, `street_2_cleaned == "104 Rhines Hall"`, `name_overrides == {"name2": None}`.

#### 7 Failure modes

- Step 4 depends on an external LLM: call failures degrade to flag `G1-ADDR-009` and no field action (enrichment/address_processing.py:677-683, 726-728); with `llm_client=None` the step is a no-op (715-716).
- Step 1b drops nothing when slots are full, but then leaves the address text in the name field (`_placed_all` guard, 951-961).
- Overflow beyond 5 street lines after reduction is only flagged (`G3-ADDR-011`), the surplus lines are discarded by the fixed-length slot rebuild (994-998).
- A qualifier split with no free slot leaves the combined value untouched (1101-1103).
- ⚠ NO FIXTURE COVERAGE for Step 4 (`_apply_residual_llm`): every `process_address` test passes `llm_client=None` (tests/test_address_cleanup.py:23,53,69,81; tests/test_street_scope_table.py:33; tests/test_street_qualifier_split.py:41,78; tests/test_street_in_name.py:32).

---

### Primary-street scope-table reduction (_reduce_primary_street — enrichment/address_processing.py)

#### 1 Purpose

Per-segment reduction of a *mixed* primary street value so that each concern lands in its own field and Street 1 carries exactly the main street line. Runs only for complex cases — a c/o line, a named building, or a pipe separator — so simple values keep the per-value extraction path (enrichment/address_processing.py:801-830).

#### 2 Inputs and outputs

Inputs: the `AddressResult` (mutated in place for building / care-of / mail-code / department findings), the cleaned primary street value, and keyword-only `city`, `state`, `zip_code` (enrichment/address_processing.py:801-808). Output: the ordered list of street lines — main street(s) first, then campus fragments, then overflow buildings/residue — or `None` for the simple case (818-820, 892).

#### 3 Pseudocode

Source: enrichment/address_processing.py:821-830 (gate).

1. Return `None` if the value is empty (821-822).
2. Split on `[|,]`, stripping `" ;-"` from each piece and discarding empties; return `None` when fewer than 2 segments (823-826).
3. Compute `has_pipe` (`"|" in primary`), `has_building` (any segment passes `_named_building_value`), `has_care_of` (any segment matches `_CARE_OF_RE.match`); return `None` unless at least one holds (823, 827-830).

Source: enrichment/address_processing.py:832-888 (segment routing — the scope table). For each segment, the **first** matching row wins:

| # | Test (verbatim condition) | Route | Lines |
|---|---|---|---|
| 1 | `re.match(r"^\d{3,4}\s+[A-Z]{2,5}$", seg)` — bare campus mail code ("3120 TAMU") | `res.mail_code` (first only; later ones silently dropped) | 836-840 |
| 2 | `re.match(r"^\d+\s+(.+\b(?:Building|Bldg))\.?$", seg, re.IGNORECASE)` **and** no `_STREET_TYPE_WORD_RE` hit — numbered building ("5045 Emerging Technologies Building"; restricted to Building/Bldg so "104 Rhines Hall" stays a street) | title-cased → `res.building`; a second building → overflow list | 841-852 |
| 3 | `_CARE_OF_RE.match(seg)` — c/o / Attn line | payload stripped of `" ,;-."`; department-looking payload (`_looks_like_department`) → `res.department_addendum` (first only), else → `res.care_of_enriched` (only when not already set) | 853-861 |
| 4 | `_named_building_value(seg)` — named building | title-cased → `res.building` (first); a second building → overflow (→ next free street slot) | 862-868 |
| 5 | `_segment_is_location_only(seg, city, state, zip_code)` — city/region/postcode already carried in its own field | dropped | 870-871 |
| 6 | `_is_campus_fragment(seg)` — campus / science-park fragment | `campuses` list (own street slot) | 872-874 |
| 7 | `_looks_like_department(seg)` — department segment ("Chemistry Dept.") | `res.department_addendum` (first; later ones → overflow) | 875-881 |
| 8 | `_looks_like_street(seg)` **or** `_STREET_TYPE_WORD_RE.search(seg)` — house-numbered street or bare street name | `addresses` list (main street lines) | 883-887 |
| 9 | anything else | overflow | 888 |

4. If ≥ 2 main street lines were collected, issue `G3-ADDR-013` (890-891).
5. Return `addresses + campuses + overflow` (892). The caller writes these into `s1…s5` ahead of previously populated secondary slots and flags `G3-ADDR-011` on overflow past five slots (991-998).

Supporting predicates:

- `_segment_is_location_only` (587-608): false when the segment looks like a street; true when the segment equals the record's own city/state/zip case-insensitively (596-599); otherwise requires a postcode — UK form `_UK_POSTCODE_RE` = `\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b` (549) or US `\b\d{5}(?:-\d{4})?\b` — and that nothing remains after deleting the postcode plus the record's own city/state (600-608).
- `_is_campus_fragment` (582-584): `_CAMPUS_FRAGMENT_RE` = `\b(?:Campus|Science\s+Park|Research\s+Park|Technology\s+Park|Business\s+Park|Innovation\s+Campus|Science\s+&\s+Innovation)\b` (IGNORECASE, 559-563) and not street-like.

#### 4 Constants

Inline regexes verbatim: campus mail code `^\d{3,4}\s+[A-Z]{2,5}$` (837); numbered building `^\d+\s+(.+\b(?:Building|Bldg))\.?$` (845). Shared: `_CARE_OF_RE` (326-329), `_STREET_TYPE_WORD_RE` (466-470), `_CAMPUS_FRAGMENT_RE` (559-563), `_UK_POSTCODE_RE` (549).

#### 5 Complexity

Linear in the number of comma/pipe-separated segments of Street 1 (typically 2-4); each segment is tested against at most 9 branch conditions, each a compiled regex over the segment (enrichment/address_processing.py:823-888). The gate additionally scans all segments once for buildings and c/o (827-828).

#### 6 Worked example

From tests/test_street_scope_table.py:39-49 (`test_row212_pipe_mix`): input `"C/O RCUK SSC Ltd, Polaris House, North Star House, North Star Avenue"`, city "Swindon", country "GB".

- Segments: `["C/O RCUK SSC Ltd", "Polaris House", "North Star House", "North Star Avenue"]`; `has_care_of` is true → the reducer fires (823-830).
- Seg 1 → row 3: payload `"RCUK SSC Ltd"`, not a department → `care_of_enriched = "RCUK SSC Ltd"` (853-861).
- Seg 2 → row 4: `"Polaris House"` ends in "House" (`_BUILDING_SUFFIX_RE`) → `building = "Polaris House"` (862-866; mixed case, `smart_title_case` is a no-op, utils/text_utils.py:299-300).
- Seg 3 → row 4 with `building` already set → overflow (867-868).
- Seg 4 → row 8: no house number but `_STREET_TYPE_WORD_RE` matches "Avenue" → main street line (883-887).
- Return `["North Star Avenue", "North Star House"]` → `s1`, `s2`; Step 5 normalises "Avenue"→"Ave".

Asserted: `care_of_enriched == "RCUK SSC Ltd"`, `building == "Polaris House"`, `"Star Ave" in street_cleaned`, `"Star House" in street_2_cleaned` (tests/test_street_scope_table.py:45-49).

Second example, tests/test_street_scope_table.py:64-72 (`test_building_and_campus_split`): `"The Sherard Bldg, Edmund Halley Rd, Oxford Science Park"` → building `"The Sherard Bldg"` (row 4), street 1 `"Edmund Halley Rd"` (row 8), street 2 `"Oxford Science Park"` (row 6, campus). Third, tests/test_street_scope_table.py:100-105 (`test_tamu_mail_code_and_numbered_building`): `"3120 TAMU | 5045 Emerging Technologies Building"` → `mail_code == "3120 TAMU"` (row 1), `building == "Emerging Technologies Building"` (row 2). Location-drop case, tests/test_street_scope_table.py:52-62: `"ASTER HOUSE, 2A UNIVERSITY ROAD, BELFAST BT7 1NH"` with city "Belfast" → building `"Aster House"` (smart-title-cased from ALL-CAPS), `"BELFAST BT7 1NH"` dropped by row 5, `street_2_cleaned is None`. Negative gate case, tests/test_street_scope_table.py:74-80: `"100 Main St, Suite 400"` has no pipe/building/c-o → reducer returns `None` and the ordinary suite extractor produces `suite == "400"`.

#### 7 Failure modes

- A second bare campus mail code or department segment is silently dropped once the target field is taken (mail code: 838-840 `continue` with no overflow; department: first fills, later go to overflow street slots, 878-881).
- Order-dependence: a numbered building containing a street-type word is *not* routed to Building (row 2's `_STREET_TYPE_WORD_RE` guard, 846) — deliberate, but means "10 Park Building Dr"-style values stay street lines.
- Segments are produced by splitting on commas *and* pipes; a legitimate comma inside a building name is split into separate segments (823-824). ⚠ UNVERIFIED — no fixture exercises a comma-bearing building name.

---

### Sub-location extraction (_extract_sublocations — enrichment/address_processing.py)

#### 1 Purpose

Walks a fixed, ordered pattern table over a street value and pulls every suite / building / floor / room / unit / mail-stop token into its target field, leaving the street residue; detects bare marker words with no value (enrichment/address_processing.py:287-322).

#### 2 Inputs and outputs

Input: the working street string. Output: `(remaining, found, bare_marker)` — `found` maps target field name → extracted value (first match wins per field), `bare_marker` is `True` when a marker word had no value attached (enrichment/address_processing.py:288-294, 318-322).

#### 3 Pseudocode

Source: enrichment/address_processing.py:295-322.

1. Empty input → `(text, {}, False)` (295-296).
2. For each `(pattern, target)` in `_SUITE_PATTERNS`, in table order: loop `pat.search(work)`; on each match take group 1 if the pattern has groups else the whole match (304); if the capture fails `_is_identifier_like` (268-284: accepted iff it contains a digit OR is ≤ 2 characters; alphabetic words of ≥ 3 characters such as "Annex" are rejected) **break out of this pattern's loop** so the phrase stays in the residual for the LLM step (305-309); else record it in `found` under `target` only if that target is not yet present (310-311), remove the matched span from `work`, and `_strip_residue` (312-313).
3. `bare = bool(_BARE_MARKER_RE.search(work))` — a trailing marker word with no value (318). If `_BARE_MARKER_DELETE_RE` matches, delete that marker from the residual ("Building"/"Bldg" is excluded from deletion because it may name a building, e.g. "Research I Bldg") (319-321).
4. Return `(work, found, bare)` (322).

The caller writes each `found[target]` into `AddressResult.<target>` only when still `None` and flags `G4-ADDR-008` on `bare_marker` (enrichment/address_processing.py:1056-1061).

#### 4 Constants

`_SUITE_PATTERNS` — ordered list of **13** `(compiled regex, target-field)` entries; "Order matters: most specific first so 'Mail Stop' wins over a bare 'MS'" (enrichment/address_processing.py:216-250). Verbatim, in order:

```python
_SUITE_PATTERNS = [
    (re.compile(r"\b(Campus\s+Box\s+[\w\-]+)\b", re.IGNORECASE), "mail_stop"),
    (re.compile(r"\b(?:Mail\s+Stop|MS)\s+(\w[\w\-]*)\b", re.IGNORECASE), "mail_stop"),
    (re.compile(r"\b(?:Suite|Ste\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "suite"),
    (re.compile(r"\b(?:Bldg\.?|Building)\s+(\w[\w\-]*)\b", re.IGNORECASE), "building"),
    (re.compile(r"\b(?:Floor|Fl\.?)\s+(\w[\w\-]*)\b", re.IGNORECASE), "floor"),
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)?\s+(?:Floor|Fl)\b\.?", re.IGNORECASE), "floor"),
    (re.compile(r"\b(\d+)F\b"), "floor"),
    (re.compile(r"(?:^|,)\s*(\d+)(?:st|nd|rd|th)\s*$", re.IGNORECASE), "floor"),
    (re.compile(
        r"\b(?:Room|Rm)\b\.?\s*(?:number|no|nr)?\.?\s*[:#]?\s*(\w[\w\-]*)\b",
        re.IGNORECASE,
    ), "room"),
    (re.compile(r"\b(Lab\.?\s+\w*\d[\w\-]*)\b", re.IGNORECASE), "room"),
    (re.compile(r"(?:^|\s)([A-Za-z]\d{2,}(?:\.\d+)?)\s*$"), "room"),
    (re.compile(r"\bUnit\s+(\w[\w\-]*)\b", re.IGNORECASE), "unit"),
    (re.compile(r"#\s*(\w[\w\-]*)\b"), "suite"),
]
```

Kind coverage and source comments (enrichment/address_processing.py:218-250): mail stop ("Campus Box 7212" keeps the full phrase, 220; "Mail Stop"/"MS", 221); suite ("Suite"/"Ste", 222; `#` shorthand, 249); building marker-with-identifier (223); floor in four forms — marker-before-value (225), value-before-marker with optional ordinal suffix, only the number kept (226-228), `\d+F` (229), and a bare trailing ordinal segment anchored so "5th Ave" never matches (230-233); room in three forms — marker with optional filler "number/no/nr" (234-239), "Lab" + numeric id keeping the full phrase while "Smith Lab" is left alone (240-242), and a trailing letter+digits room code ("A104", "C13.217") as final token (243-246); unit (247).

Bare-marker regexes verbatim (enrichment/address_processing.py:252-265):

```python
_BARE_MARKER_RE = re.compile(
    r"\b(?:Suite|Ste|Bldg|Building|Floor|Fl|Room|Rm|Unit|Mail\s+Stop|MS)\b\s*$",
    re.IGNORECASE,
)
_BARE_MARKER_DELETE_RE = re.compile(
    r"\b(?:Suite|Ste|Floor|Fl|Room|Rm|Unit|Mail\s+Stop|MS)\b\s*$",
    re.IGNORECASE,
)
```

#### 5 Complexity

13 patterns per slot value; each pattern re-scans the shrinking string until it fails, so worst case O(13 · m · L) with m matches per pattern and L value length — in practice m ≤ 2 per pattern per street line, and the table is scanned once per populated slot (≤ 5 per record) (enrichment/address_processing.py:299-313, 1001-1056).

#### 6 Worked example

From tests/test_street_scope_table.py:94-98 (`test_campus_box_mail_stop`): input `"2721 Sullivan Dr Campus Box 7212"`. Table entry 1 (Campus Box) matches first, capture `"Campus Box 7212"` (contains digits → identifier-like) → `found = {"mail_stop": "Campus Box 7212"}`; residue `"2721 Sullivan Dr"`. Asserted: `street_cleaned == "2721 Sullivan Dr"`, `mail_stop == "Campus Box 7212"`.

Other fixtures: `"51 Sleeper St, 7th"` → bare trailing ordinal (entry 8) → `floor == "7"` (tests/test_street_scope_table.py:82-86); `"1500 Graduate Ln A104"` → trailing room code (entry 11) → `room == "A104"` (tests/test_street_scope_table.py:88-92); `"Room number: F107, 100 Main Street"` → filler form (entry 9) → `room == "F107"` (tests/test_address_cleanup.py:106-109); bare-marker deletion: `"Pinellas Bus Ctr Ste"` → `"Pinellas Bus Ctr"` and lone `"Ste"` → `None` (tests/test_address_cleanup.py:38-43); identifier gate: `"Pinellas Bus Ctr, Ste 400D"` → `suite == "400D"` (tests/test_address_cleanup.py:63-73).

#### 7 Failure modes

- The non-identifier `break` (305-309) aborts only the *current* pattern; a later, looser pattern may still capture part of the phrase.
- First-match-wins per field: a second suite/floor/room in the same record is removed from the street but its value is discarded (`if target not in found`, 310-311, plus the `None`-guard at 1057-1059).
- `_BARE_MARKER_DELETE_RE` deletes only a *trailing* bare marker; an interior bare marker stays in the residual (262-265, 319-321).

---

### Street qualifier splitting (_split_location_qualifier — enrichment/address_processing.py)

#### 1 Purpose

Splits a street value carrying a trailing access/location qualifier after the street-type word — "300 Tech Park Dr NEAR LOADING DOCK B" — into (street, qualifier), so the qualifier can be moved verbatim to the next empty street slot and kept as a street line rather than being pulled into `unloading_point` (enrichment/address_processing.py:492-535, 1090-1105).

#### 2 Inputs and outputs

Input: one street value (or `None`). Output: `(street, qualifier)` or `None` when the value is not that shape (enrichment/address_processing.py:523-535).

#### 3 Pseudocode

Source: enrichment/address_processing.py:523-535.

1. Return `None` on empty/blank input (526-527).
2. Match `_STREET_QUALIFIER_SPLIT_RE` against the stripped value; no match → `None` (528-530).
3. Strip `" ,;-"` from both captured groups; return `None` when either is empty **or** the street part has no house number (`_HOUSE_NUMBER_RE` = `\b\d+\b`, 471) — this guard rejects "100 Loading Dock Rd"-style values where the "qualifier" word is part of the street name only if no house number precedes; more precisely it requires a numbered street on the left (531-534).
4. Return `(street, qual)` (535).

Caller loop (Step 4b, enrichment/address_processing.py:1095-1105): runs after extraction and the LLM step, over slots `s1…s5` in order; on a split the street part replaces the slot and the qualifier fills the first empty slot; with no free slot the combined value is kept.

#### 4 Constants

Verbatim (enrichment/address_processing.py:500-520):

```python
_STREET_TYPES_ALT = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Way|Pl|Place|Ter|Terrace|Cir|Circle"
)
_STREET_START_RE = re.compile(
    rf"\b\d+\s+.*?\b(?:{_STREET_TYPES_ALT})\b\.?",
    re.IGNORECASE,
)
_LOC_QUALIFIER_LEAD = (
    r"Near|Behind|Beside|Adjacent|Across|Opposite|Next\s+To|In\s+Front\s+Of|"
    r"Gate|Dock|Loading|Unloading|Warehouse|Entrance|Entry|Bay|Ramp|Elevator"
)
_STREET_QUALIFIER_SPLIT_RE = re.compile(
    rf"^(?P<street>.*?\b(?:{_STREET_TYPES_ALT})\b\.?)\s+"
    rf"(?P<qual>(?:{_LOC_QUALIFIER_LEAD})\b.*)$",
    re.IGNORECASE,
)
```

`_LOC_QUALIFIER_LEAD` lists 8 positional prepositions/phrases and 9 access/logistics nouns (17 alternatives).

#### 5 Complexity

One regex match plus one house-number check per populated slot, at most 5 per record (enrichment/address_processing.py:1096-1105).

#### 6 Worked example

From tests/test_street_qualifier_split.py:17-23 (`test_split_detector`): `_split_location_qualifier("300 TECH PARK Dr NEAR LOADING DOCK B")` → `("300 TECH PARK Dr", "NEAR LOADING DOCK B")`; the lazy `.*?` in the street group stops at the first street-type word "Dr", and "NEAR" heads the qualifier group. End-to-end (tests/test_street_qualifier_split.py:45-49): `street_cleaned == "300 TECH PARK Dr"`, `street_2_cleaned == "NEAR LOADING DOCK B"`, `unloading_point is None`. Occupied-slot case (52-57): with Street 2 = "200 Oak Ave" the qualifier lands in Street 3. Negative cases (26-33): `"100 Loading Dock Rd"`, `"300 Main St Suite 5"`, `"LOADING DOCK B"` (no street → handled by `unloading_point`, asserted at 60-62), `"300 Tech Park Dr"` all return `None`.

#### 7 Failure modes

- The qualifier must *immediately* follow a street-type word; a qualifier after a trailing directional or unit token is not split (pattern shape, 516-520). ⚠ UNVERIFIED — no fixture covers e.g. "300 Tech Park Dr NW GATE C".
- Because Step 4b runs after logistics extraction, a bare logistics value has already been consumed by `_extract_logistics`; ordering is what preserves combined values as street lines (comment 1090-1094).

---

### Named-building value extraction, address side (_named_building_value — enrichment/address_processing.py)

#### 1 Purpose

Decides whether a whole segment/slot names a building — in which case it is routed to the `building` output field — as opposed to a street address, a department, or ordinary text (enrichment/address_processing.py:566-579). Used in three places: the reduction gate (827), the reduction routing (862), and the whole-slot check in Step 2b (1009).

#### 2 Inputs and outputs

Input: one segment string (or `None`). Output: the stripped segment when it names a building, else `None` (enrichment/address_processing.py:566-579).

#### 3 Pseudocode

Source: enrichment/address_processing.py:566-579.

1. Empty/blank → `None` (570-571).
2. Leading house number (`^\d+\s`) → `None` — a numbered value is a street address, not a bare building (573-574).
3. `_looks_like_street(s)` → `None` (575-576).
4. `_looks_like_department(s)` → `None` (577-578).
5. Return `s` iff `_BUILDING_SUFFIX_RE.match(s)` — a non-empty phrase whose final word is Building/Bldg/House/Hall/Pavilion/Tower — else `None` (579).

Consumers title-case the value (`smart_title_case(nb) or nb`) and fill `res.building` only when it is still `None`; a second building overflows to the next free street slot in the reducer (862-868) or stays in its street slot in Step 2b (1006-1013, comment "a second building is left in its street slot per the scope table").

#### 4 Constants

Verbatim (enrichment/address_processing.py:550-558):

```python
_BUILDING_SUFFIX_RE = re.compile(
    r"\S.*\s(?:Building|Bldg|House|Hall|Pavilion|Tower)\.?\s*$",
    re.IGNORECASE,
)
```

The source comment (550-554) ties this to the scope table's "House"/"Hall" building forms ("Aster House", "Polaris House") and the "... Building"/"... Bldg" suffix form ("The Sherard Bldg", "Emerging Technologies Building").

#### 5 Complexity

Four regex tests per candidate segment; called once per reduction segment and once per populated slot (enrichment/address_processing.py:827, 862, 1009).

#### 6 Worked example

From tests/test_address_cleanup.py:46-56 (`test_named_building_routed_to_building_field`): Street 2 = `"Research I Bldg"` — no leading number, no house-number+street-type pair, no department word, ends in "Bldg" → routed to `building == "Research I Bldg"`; the slot is blanked and after left-packing `street_cleaned is None`. Scope-table fixtures: `"ASTER HOUSE"` → `building == "Aster House"` (tests/test_street_scope_table.py:52-57); `"Polaris House"` first building, `"North Star House"` second → next street slot (tests/test_street_scope_table.py:39-49).

Note: tests/test_named_building.py:27-39 exercises `_named_building` in `enrichment/preprocess.py` (the *early* preprocessing detector — e.g. "Building 5", "Bldg", "Department of Chemistry" rejected), not `_named_building_value`; on the address side the covering fixtures are the two above. The mechanically analogous rejections for `_named_building_value` (bare "Bldg" fails `\S.*\s` prefix; "Building 5" fails the suffix position) follow from `_BUILDING_SUFFIX_RE` (555-558) but have no direct fixture — ⚠ NO FIXTURE COVERAGE for those specific rejections at this call site.

#### 7 Failure modes

- "104 Rhines Hall" is excluded (leading number → street), which matches preprocessing routing (tests/test_named_building.py:81-88); an unnumbered "Rhines Hall" *would* qualify here.
- A building name not ending in one of the six suffix words (e.g. "Annex", "Wing") is never routed to `building` by this function (555-558); such fragments are handled elsewhere (preprocess `_location_fragment`, tests/test_named_building.py:168-183).

---

### Result merge (merge_into_result — enrichment/address_processing.py)

#### 1 Purpose

Copies an `AddressResult` into the orchestrator's result dict in place, applies name-field rewrites, and places a detected department into the first empty name slot (enrichment/address_processing.py:1159-1219).

#### 2 Inputs and outputs

Inputs: the mutable result dict and the `AddressResult`. Output: none (in-place mutation) (enrichment/address_processing.py:1159-1162).

#### 3 Pseudocode

Source: enrichment/address_processing.py:1169-1219.

1. For each of the 16 scalar fields (`street_cleaned`…`street_5_cleaned`, `suite`, `building`, `floor`, `room`, `unit`, `mail_stop`, `po_box_extracted`, `unloading_point`, `mail_code`, `unclear_address_info`): copy into the dict only when not `None` (1169-1178).
2. `care_of_enriched` is assigned directly when not `None` — it was seeded from the dict at the start of `process_address` and any new c/o was already appended with `" | "` inside the pipeline (1180-1185; seeding at 922-923; append at 1035-1039).
3. For each entry in `name_overrides`: write `result_dict[f"{name_field}_enriched"] = new_val`; when the new value is blank, add the field to the `_preprocess_cleared` set so `finalise()` does not restore the original (1191-1195).
4. If `department_addendum` is set: place it into the first name slot (`name2`, then `name3`) whose enriched **and** original values are both blank; if none is empty, the address_issues flag is the only record (1197-1211).
5. Merge `address_issues` into any existing list, preserving order and skipping duplicates (1213-1219).

#### 4 Constants

The 16-field tuple at enrichment/address_processing.py:1169-1175.

#### 5 Complexity

O(1): fixed field list plus at most two name slots and the issues list.

#### 6 Worked example

From tests/test_street_in_name.py:88-99 (`test_merge_clears_name_field_and_marks_cleared`): after `process_address(name2="104 Rhines Hall", street="549 GALE LEMERAND Dr")`, merging into `{"name2_enriched": "104 Rhines Hall", "name2_original": "104 Rhines Hall"}` yields `name2_enriched is None`, `"name2" in result_dict["_preprocess_cleared"]`, and `street_2_cleaned == "104 Rhines Hall"`.

#### 7 Failure modes

- A department found when both name2 and name3 are occupied is recorded only as an issue code (comment and loop, 1197-1211).
- Scalar copies skip `None` but not empty strings; `AddressResult` fields are normalised to `None` rather than `""` throughout the pipeline (e.g. 1064, 1108-1112), so this does not arise in practice.

---

### Address-fragment stripping (strip_address_fragments — utils/text_utils.py)

#### 1 Purpose

Removes address fragments that leaked into a *name* field, using the record's own structured address fields as the only source of fragments (no hardcoded suffix lists); called by the orchestrator as a defensive second pass on Name 1 before the ROR (Tier 1) query (utils/text_utils.py:803-833; call site enrichment/orchestrator.py:1939-1945).

#### 2 Inputs and outputs

Inputs: `name`, optional `street`, `city`, `state`, `zip_code` (utils/text_utils.py:803-809). Output: the cleaned name, or the original stripped name when cleaning would empty it; `None`/blank input is returned unchanged (834-835, 896-897).

#### 3 Pseudocode

Source: utils/text_utils.py:834-897.

1. Blank name → return as-is (834-835).
2. All fragment removals are whole-word: `\b<escaped fragment>\b`, IGNORECASE (839-841) — so state code "FL" cannot match inside "Florida" (comment 814-815).
3. **Always** strip `street` and `zip_code` when present (unambiguous noise); record `address_like_hit` if anything changed (846-856).
4. Strip standalone digit runs `\b\d{3,}\b` (street numbers not carried in the street field); this also sets `address_like_hit` (859-861).
5. Trailing-suffix peel: repeatedly remove a trailing `", <city>"` or `", <state>"` segment matching the record's own City/State (`,\s*<frag>\s*$`), only when a non-empty prefix remains; interior occurrences are never touched and this does **not** set `address_like_hit` (870-883).
6. Only if `address_like_hit`: strip `city` and `state` as whole words anywhere, but keep the removal only when the residue is non-trivial (886-893).
7. Collapse whitespace, trim `" ,;.-"`; if empty, return the original (896-897).

#### 4 Constants

No module-level constants; inline patterns `\b\d{3,}\b` (859-861) and the trailing-segment regex `,\s*<frag>\s*$` (876-878).

#### 5 Complexity

O(k·L) with k ≤ 4 fragments plus the peel loop (at most one iteration per trailing segment removed); each pass is a single regex substitution over the name (839-893).

#### 6 Worked example

From tests/test_strip_address_fragments.py:23-30 (`test_trailing_city_and_state_suffix_peeled`): `strip_address_fragments("HCA Florida University Hospital, Davie, FL", city="Davie", state="FL")`. No street/zip, no 3+-digit run → `address_like_hit` stays `False`. Peel loop, pass 1: city ",\s*Davie\s*$" does not match (value ends ", FL") but state ",\s*FL\s*$" matches → `"HCA Florida University Hospital, Davie"`; pass 2: city matches → `"HCA Florida University Hospital"`; pass 3: no change → exit. Step 6 skipped. Result: `"HCA Florida University Hospital"`. Safety fixtures: interior "Florida" untouched with state "Florida" (43-48); "University of Florida" untouched with state "FL" (51-55); no-fields no-op (58-62); docstring example "Johns Hopkins Hospital 600 N Wolfe St" + street → "Johns Hopkins Hospital" (utils/text_utils.py:826-828).

#### 7 Failure modes

- The conservatism rule means a city/state leaked *without* street/zip and *not* in trailing-comma position is never removed (820-824, 885-887).
- tests/test_ror_name_verbatim.py:74 documents a historical root cause: over-stripping a campus city; the trailing-segment-only rule (867-869) is the mitigation.

---

### Logistics-location detection (is_logistics_location — utils/text_utils.py)

#### 1 Purpose

Detects a distribution / fulfillment / logistics facility name so it is routed to `unloading_point` rather than treated as an organisation or street (utils/text_utils.py:657-670; consumed by `_extract_logistics`, enrichment/address_processing.py:391-393).

#### 2 Inputs and outputs

Input: any string or `None`; output: `bool` (utils/text_utils.py:668-670).

#### 3 Pseudocode

Source: utils/text_utils.py:668-670.

1. Return `bool(value and _LOGISTICS_LOCATION_RE.search(value))`.

#### 4 Constants

Verbatim (utils/text_utils.py:662-665):

```python
_LOGISTICS_LOCATION_RE = re.compile(
    r"\b(?:Distribution|Fulfil?lment|Logistics)\s+(?:Center|Centre|Ctr|Warehouse)\b",
    re.IGNORECASE,
)
```

#### 5 Complexity

One regex search per call; called at most once per populated street slot (enrichment/address_processing.py:391).

#### 6 Worked example

From tests/test_street_qualifier_split.py:65-68 (`test_distribution_centre_classified_as_unloading_point`): `"SOUTHEAST DISTRIBUTION CTR"` — "DISTRIBUTION CTR" matches → `_extract_logistics` returns `("", value)` → `unloading_point == "SOUTHEAST DISTRIBUTION CTR"`, `street_cleaned is None`.

#### 7 Failure modes

- Matches anywhere in the value, and `_extract_logistics` then consumes the *entire* value (enrichment/address_processing.py:391-393): a street line that merely mentions a distribution centre would be wholly rerouted. ⚠ UNVERIFIED — no fixture combines a real street with an embedded facility phrase.

---

### ALL-CAPS title casing (smart_title_case — utils/text_utils.py)

#### 1 Purpose

Title-cases an ALL-CAPS value while preserving acronyms, connectors, hyphen segments, and "Mc" surnames; mixed-case input is returned unchanged so canonical ROR/LLM names are never altered (utils/text_utils.py:285-310). The address stage applies it to building names before writing `res.building` (enrichment/address_processing.py:847, 864, 1011).

#### 2 Inputs and outputs

Input: string or `None`; output: cased string, or the input unchanged when blank or not `value.isupper()` (utils/text_utils.py:299-300).

#### 3 Pseudocode

Source: utils/text_utils.py:299-310, 266-282, 258-263.

1. Blank or not ALL-CAPS → return unchanged (299-300).
2. Per whitespace token: a whole-token entry in `_CASE_EXCEPTIONS` wins (303-305); a hyphenated token cases each segment independently (306-307); otherwise `_case_segment` (309).
3. `_case_segment` (266-282): letters-only key; connectors (`_TITLE_CASE_CONNECTORS`) → lowercase; members of `_FORCE_TITLE_SHORT` → capitalised (with `_mc_name`); members of `_KEEP_UPPER_ACRONYMS` → unchanged; ≤ 3 letters → unchanged (assume acronym); 4-5 letters with no vowel → unchanged; else capitalised with `_mc_name` (Mc-surname repair, 258-263; "Mac" deliberately untouched).

#### 4 Constants

Verbatim definition sites: `_TITLE_CASE_CONNECTORS = {"of", "and", "for", "the", "in", "at", "&"}` (utils/text_utils.py:219); `_FORCE_TITLE_SHORT` — 19 members: `"INC", "LTD", "CO", "BAY", "NEW", "OLD", "SUN", "OAK", "BIG", "RED", "SKY", "SEA", "AIR", "SON", "TWO", "ONE", "KEY", "TOP", "BOX"` (227-230); `_KEEP_UPPER_ACRONYMS` — 27 members, e.g. `"NASA"`, `"NIST"`, `"TUHH"`, `"UCSF"`, `"SUNY"`, `"UPENN"`, `"UCONN"` (232-244); `_VOWELS = set("AEIOU")` (245); `_CASE_EXCEPTIONS` — 4 entries: `"bio-rad": "Bio-Rad"`, `"abx-cro": "ABX-CRO"`, `"dana-farber": "Dana-Farber"`, `"at&t": "AT&T"` (250-255).

#### 5 Complexity

O(number of tokens); each token does set lookups plus one capitalisation.

#### 6 Worked example

From tests/test_smart_title_case.py:17-46: `"SOUTH BAY HOSPITAL"` → `"South Bay Hospital"` ("BAY" ∈ `_FORCE_TITLE_SHORT`); `"MRI DEPARTMENT"` → `"MRI Department"` (3-letter acronym kept); `"DANA-FARBER"` → `"Dana-Farber"` (exception map); `"MCINTYRE"` → `"McIntyre"` (`_mc_name`); `"UCSF"` → `"UCSF"` (`_KEEP_UPPER_ACRONYMS`); mixed-case `"Bio-Rad"` unchanged (52-54). Address-side effect: `"ASTER HOUSE"` → building `"Aster House"` (tests/test_street_scope_table.py:52-57).

#### 7 Failure modes

- An unlisted vowel-bearing acronym of ≥ 4 letters is title-cased (heuristic default, 278-282); the allowlist comments direct extension "as they come up" (235-243).

---

### Non-determinism notes

The stage is fully deterministic **except** for Step 4, the LLM residual classification:

- Every other step is pure regex/string manipulation over its inputs with fixed pattern tables and fixed iteration order: slot iteration uses the literal tuples `("s1", …, "s5")` (enrichment/address_processing.py:944, 1001, 1095), pattern iteration uses list order (`_SUITE_PATTERNS`, 299; `_ADDRESS_PATTERNS`, enrichment/preprocess.py:249), Step 6's `set` is only membership-tested while iteration runs over the fixed slot-name tuple (enrichment/address_processing.py:1119-1130), and dicts (`slots`, `found`, `secondary`, `name_overrides`) rely on Python's insertion-ordered iteration. No randomness, clock, or environment dependence exists in the module (no `random`/`time` import; imports at 26-50).
- Step 4 (`_apply_residual_llm` → `_classify_residual`, enrichment/address_processing.py:660-759) calls GPT-4o-mini via `llm_client.extract_json` (677-680); model output, and therefore which secondary slots are blanked or which flags are raised, is not deterministic across runs. With `llm_client=None` the step is skipped (715-716) and the whole stage is deterministic — this is the configuration used by every address-stage test (tests/test_street_scope_table.py:33; tests/test_address_cleanup.py:23; tests/test_street_qualifier_split.py:41,78; tests/test_street_in_name.py:32).
- The orchestrator wrapper adds a second, bounded non-determinism: any exception in `process_address` silently drops all address outputs for that record (enrichment/orchestrator.py:1616-1621); with a live LLM client a transient network failure inside Step 4 is caught *within* the stage (677-683) and degrades to flag `G1-ADDR-009`, not a record-level drop.


# Part H — Issue detection, the issue catalogue, and the three issue paths

This part answers specification items (c) — the full rule catalogue with exact declared and emitted counts — and (d) — the normalisation each of the three issue paths applies before detection, and whether they agree. Its procedures are grouped under three intermediate `##` sections — the rule catalogue, the shared normalisation comparison, and the formatting contract.

All paths are relative to `enrichment_api/`. All line numbers were verified by reading the cited files in full (`enrichment/issue_detection.py`, 510 lines; `api/routes.py`, 1118 lines). All behavioural claims carry a citation; regexes and constants are copied verbatim from their definition sites. Worked examples are taken from `tests/test_issue_detection.py` and `tests/test_routes.py` and were re-executed against the installed code to confirm every intermediate value.

---

## PART 1 — Issue-rule catalogue

### 1.1 Catalogue table

The catalogue is the ordered dict `ISSUE_CATALOGUE` at `enrichment/issue_detection.py:75-118`. Detection is dispatched from `detect_issues` (`enrichment/issue_detection.py:488-510`) into five group functions: `_detect_wrong_field` (221-317), `_detect_missing` (324-369), `_detect_duplicate` (376-432), `_detect_format` (439-465), `_detect_naming` (472-481). Every rule in this module is deterministic (regex/string checks only; the module imports no LLM or network client — imports are `re`, `api.models`, `enrichment.preprocess`, `enrichment.address_processing`, `utils.text_utils`, `enrichment/issue_detection.py:31-67`).

"Emitted in code path?" means a `found.add("<code>")` (or the `_REQUIRED_FIELD_CODES` loop) exists in `detect_issues`'s call tree. One emitted code, G2-CONTACT-008, has an add-site that is provably unreachable — see §1.3.

| Code | Group | Detection predicate (plain terms) | Implementing function:line | Deterministic / model-assisted | Emitted in code path? |
|---|---|---|---|---|---|
| G1-CROSS-001 | G1 Data in Wrong Field | Any of Name 1–4 contains an address fragment matched by one of the six `_ADDRESS_PATTERNS` (street with house number, numbered building/hall, building+room, bare street name, sub-location marker+value, PO-box form), via `_extract_addresses` | `_detect_wrong_field`, issue_detection.py:226-229; `_extract_addresses` preprocess.py:243-257; patterns preprocess.py:158-197 | Deterministic | Yes (issue_detection.py:228) |
| G1-CROSS-002 | G1 | Any of Street 1–5 matches the org-word regex `_ORG_IN_STREET_RE` after "University Centre" forms are stripped (`_UNIVERSITY_CENTRE_RE`), AND the street contains no street-type word (`_STREET_TYPE_WORD_RE`) | `_detect_wrong_field`, issue_detection.py:231-243 | Deterministic | Yes (issue_detection.py:242) |
| G1-CROSS-003 | G1 | Any Name or Street field matches an email (`_EMAIL_RE`), phone (`_PHONE_RE`), URL (`_URL_RE`) or c/o-ATTN prefix (`_CO_ATTN_PREFIX_RE`); otherwise any Street that is entirely a title-prefixed person name (`_street_person_name`) and not street-like | `_detect_wrong_field`, issue_detection.py:245-262 | Deterministic | Yes (issue_detection.py:256, 261) |
| G1-ADDR-001 | G1 | Dedicated House Number field blank AND any Street both contains a digit token and a street-type word (`_looks_like_street`) | `_detect_wrong_field`, issue_detection.py:264-269; `_looks_like_street` address_processing.py:538-544 | Deterministic | Yes (issue_detection.py:269) |
| G1-ADDR-003 | G1 | Any Street matches any of the 13 `_SUITE_PATTERNS` (Campus Box / Mail Stop / Suite / Bldg / Floor / room / lab / unit / "#" forms) | `_detect_wrong_field`, issue_detection.py:272-276; patterns address_processing.py:218-250 | Deterministic | Yes (issue_detection.py:275) |
| G1-ADDR-004 | G1 | Any Street matches `_PO_BOX_RE` ("PO Box / POB / Post Office Box" + value) | `_detect_wrong_field`, issue_detection.py:278-282; regex address_processing.py:199-202 | Deterministic | Yes (issue_detection.py:281) |
| G1-ADDR-006 | G1 | `_extract_mail_code(street, allow_bare=True)` returns a mail/drop code (explicit "MAIL CODE:", `[A-Z]\d-\d{4}` complex form, or a bare 2-4-letter+digits token not doubling as a street-type abbreviation) | `_detect_wrong_field`, issue_detection.py:284-288; `_extract_mail_code` address_processing.py:412-432 | Deterministic | Yes (issue_detection.py:287) |
| G1-ADDR-011 | G1 | Any Street matches `_DEPARTMENT_PAYLOAD_RE` (Department/Dept/Division/Div word), via `_looks_like_department` | `_detect_wrong_field`, issue_detection.py:290-294; regex address_processing.py:333-336, helper 339-340 | Deterministic | Yes (issue_detection.py:293) |
| G1-NAME-001 | G1 | Name 1 and Name 2 both non-blank, Name 1 has no legal-entity suffix (`_has_legal_suffix` / `_LEGAL_SUFFIX_RE`), and Name 2 opens with a connector or lowercase word (`_NAME_CONTINUATION_RE`); documented as a conservative heuristic for a rule that "is LLM-only" in its true form | `_detect_wrong_field`, issue_detection.py:296-305; `_has_legal_suffix` preprocess.py:804-805, regex preprocess.py:739-751 | Deterministic (heuristic proxy) | Yes (issue_detection.py:305) |
| G1-NAME-004 | G1 | Name 2 blank while Name 3 populated | `_detect_wrong_field`, issue_detection.py:307-309 | Deterministic | Yes (issue_detection.py:309) |
| G1-NAME-013 | G1 | Any Name field whose whole value is an opaque code (`_OPAQUE_CODE_RE`: ≤4 letters, optional dash, ≥5 digits) | `_detect_wrong_field`, issue_detection.py:311-315; regex preprocess.py:312-314, helper 317-320 | Deterministic | Yes (issue_detection.py:314) |
| G1-ADDR-009 | G1 | Unclassified residual in address — requires the pipeline's LLM residual classifier; intentionally never fired by this module | Catalogue entry issue_detection.py:88; comment issue_detection.py:317 | LLM-only (in the pipeline; see §2.3) | **No** |
| G2-VAL-001 | G2 Missing Required Data | `name_1` blank (and `name_1` column present in file, when `present_fields` given) | `_detect_missing`, issue_detection.py:330-334; mapping issue_detection.py:129-137 | Deterministic | Yes (issue_detection.py:334) |
| G2-VAL-002 | G2 | `postal_code` blank (column-gated as above) | same | Deterministic | Yes |
| G2-VAL-003 | G2 | `tax_jurisdiction` blank (column-gated) | same | Deterministic | Yes |
| G2-VAL-004 | G2 | `region` blank (column-gated) | same | Deterministic | Yes |
| G2-VAL-006 | G2 | `language_key` blank (column-gated) | same | Deterministic | Yes |
| G2-VAL-007 | G2 | `search_term_1` blank (column-gated) | same | Deterministic | Yes |
| G2-VAL-008 | G2 | `country_region_key` blank (column-gated) | same | Deterministic | Yes |
| G2-NAME-009 | G2 | Name 2 is a granular research unit (`is_granular_unit`: lab/group/centre/facility, not an in-scope "Department of …" head) AND neither Name 3 nor Name 4 is a unit construction (`is_specific_unit_construction` / `is_unit_construction`) | `_detect_missing`, issue_detection.py:347-351; helpers text_utils.py:410-439, 477-505, 508-526 | Deterministic | Yes (issue_detection.py:351) |
| G2-NAME-012 | G2 | Name 1 reads as a university/research institute (`looks_like_university_or_research_institute`, gated so clinical orgs are excluded) AND Name 2 blank | `_detect_missing`, issue_detection.py:342-343; helper text_utils.py:395-407, regex text_utils.py:387-392 | Deterministic | Yes (issue_detection.py:343) |
| G2-CONTACT-008 | G2 | Name 2 blank + university/research Name 1 + Contact blank + Care-of blank + G2-NAME-012 not already found | `_detect_missing`, issue_detection.py:364-367 | Deterministic | **Add-site exists (issue_detection.py:367) but is unreachable — see §1.3** |
| G2-CONTACT-009 | G2 | Name 2 blank + university/research Name 1 + Contact non-blank + Contact names exactly one person (`has_multiple_contacts` false) | `_detect_missing`, issue_detection.py:364, 368-369; `has_multiple_contacts` preprocess.py:1071-1091 | Deterministic | Yes (issue_detection.py:369) |
| G3-NAME-003 | G3 Duplicate/Conflicting | Any Name field contains a DBA variant (`_normalise_dba` reports a change against the five `_DBA_PATTERNS`) | `_detect_duplicate`, issue_detection.py:380-384; patterns preprocess.py:267-280, helper 283-301 | Deterministic | Yes (issue_detection.py:383) |
| G3-NAME-005 | G3 | Case/whitespace-folded Name 1 equals Name 2, or Name 2 equals Name 3 (`_norm`) | `_detect_duplicate`, issue_detection.py:386-390; `_norm` issue_detection.py:187-189 | Deterministic | Yes (issue_detection.py:390) |
| G3-ADDR-005 | G3 | Count of PO-box occurrences (`_PO_BOX_RE` per street slot + 1 if dedicated PO Box field populated) ≥ 2 | `_detect_duplicate`, issue_detection.py:392-401 | Deterministic | Yes (issue_detection.py:401) |
| G3-ADDR-012 | G3 | Two street slots share the same order/case-independent (digits-set, sorted-words) signature; the dedicated House Number is folded into Street 1's signature when Street 1 has no inline number (`_street_signature`) | `_detect_duplicate`, issue_detection.py:403-417; `_street_signature` issue_detection.py:192-214 | Deterministic | Yes (issue_detection.py:417) |
| G3-ADDR-013 | G3 | ≥ 2 distinct normalised values among streets that `_looks_like_street` | `_detect_duplicate`, issue_detection.py:419-424 | Deterministic | Yes (issue_detection.py:424) |
| G3-ADDR-014 | G3 | ≥ 1 PO box (as counted above) AND any street `_looks_like_street` | `_detect_duplicate`, issue_detection.py:426-428 | Deterministic | Yes (issue_detection.py:428) |
| G3-CONTACT-007 | G3 | Contact field names more than one person: strong separator (`and`/`or`/`&`/`;`/`/`/` + `) or all comma-separated parts look like full names | `_detect_duplicate`, issue_detection.py:430-432; `has_multiple_contacts` preprocess.py:1071-1091, separator regex preprocess.py:1065-1068 | Deterministic | Yes (issue_detection.py:432) |
| G4-NAME-015 | G4 Invalid Format/Length | Sum of lengths of Name 1–4 exceeds 140 (`_SAP_NAME_LIMIT`) | `_detect_format`, issue_detection.py:440-443; constant issue_detection.py:121 | Deterministic | Yes (issue_detection.py:443) |
| G4-ADDR-008 | G4 | Any street ends in a bare sub-location marker with no value (`_BARE_MARKER_RE`) | `_detect_format`, issue_detection.py:445-449; regex address_processing.py:254-257 | Deterministic | Yes (issue_detection.py:448) |
| G4-ADDR-025 | G4 | Sub-location overflow beyond Street 5 — requires the pipeline's LLM classifier; intentionally never fired here | Catalogue entry issue_detection.py:112; comment issue_detection.py:465 | LLM-only | **No** |
| G4-ADDR-026 | G4 | Postal code non-blank AND country resolves to an ISO code with a registered format (`_POSTAL_FORMATS`: US, CA only) AND the stripped postal code fails that format | `_detect_format`, issue_detection.py:451-456; formats issue_detection.py:167-170 | Deterministic | Yes (issue_detection.py:456) |
| G4-ADDR-027 | G4 | Country field non-blank AND (`country_to_iso_code` returns None OR the raw value upper-cased differs from the ISO code) | `_detect_format`, issue_detection.py:458-463; `country_to_iso_code` text_utils.py:900-908 | Deterministic | Yes (issue_detection.py:463) |
| G5-NAME-001 | G5 Non-Standard Naming | Name 1 contains an abbreviation token (`_ABBREV_TOKEN_RE`: Univ, Dept, Inst, …) | `_detect_naming`, issue_detection.py:473-475 | Deterministic (heuristic; module docstring: "err toward precision", issue_detection.py:26-28) | Yes (issue_detection.py:475) |
| G5-NAME-002 | G5 | Any of Name 2–4 contains an abbreviation token (`_ABBREV_TOKEN_RE`) | `_detect_naming`, issue_detection.py:477-481 | Deterministic (heuristic) | Yes (issue_detection.py:480) |

### 1.2 Exact counts

- **Declared: 37 codes** — counted by hand from the `ISSUE_CATALOGUE` dict literal at `enrichment/issue_detection.py:75-118` (G1: 12 entries, lines 77-88; G2: 11, lines 90-100; G3: 7, lines 102-108; G4: 5, lines 110-114; G5: 2, lines 116-117; 12+11+7+5+2 = 37), and confirmed programmatically (`len(ISSUE_CATALOGUE) == 37`). The repo's own test asserts exactly this: `test_catalogue_has_37_codes` (`tests/test_issue_detection.py:44-45`).
- **The module docstring is stale**: it claims a "36-code Issue Catalogue" and "34 of the 36 catalogue codes are emitted" (`enrichment/issue_detection.py:4, 18`). Both numbers are wrong against the current source; the thesis must use 37/35 (with the §1.3 caveat), not the docstring's figures.
- **Emitted somewhere in `detect_issues`: 35 codes** have an add-site (28 literal `found.add("…")` calls plus the 7 `G2-VAL-*` codes added via the `_REQUIRED_FIELD_CODES` loop, `enrichment/issue_detection.py:330-334`). Verified by regex-scanning the module for every `found.add` and cross-checking against the catalogue keys.
- **Declared but never emitted (no add-site): 2 codes** — `G1-ADDR-009` (catalogue `enrichment/issue_detection.py:88`, comment-only at 317) and `G4-ADDR-025` (catalogue line 112, comment-only at 465). Both are marked `# LLM-only — never emitted` in the catalogue itself, and both have regression tests asserting non-emission (`tests/test_issue_detection.py:121-124`, `290-292`).

### 1.3 G2-CONTACT-008 is emitted in code but unreachable

`G2-CONTACT-008` has a `found.add` site (`enrichment/issue_detection.py:367`) but can never appear in the output. Its outer gate — `name2_blank and looks_like_university_or_research_institute(record.name_1)` (`enrichment/issue_detection.py:364`) — is exactly the condition under which `G2-NAME-012` was unconditionally added earlier in the same function (`enrichment/issue_detection.py:342-343`). The inner guard `if "G2-NAME-012" not in found` (`enrichment/issue_detection.py:366`) is therefore always false. The code comment acknowledges the shared gate and the deliberate suppression (`enrichment/issue_detection.py:359-363`), and the test suite pins the suppressed behaviour (`tests/test_issue_detection.py:154-162`). Consequence: **at most 34 distinct codes can actually be observed in `detect_issues` output** (35 with add-sites, minus the unreachable G2-CONTACT-008), which coincidentally matches the stale docstring's "34" for a different reason.

---

### detect_issues

#### 1 Purpose

Audits a single `EnrichmentRecord` against the Issue Catalogue and returns every code that fires, in catalogue order. Pure and deterministic; it is the engine behind `POST /issues` and `POST /issues/compare` (`enrichment/issue_detection.py:1-5, 488-503`).

#### 2 Inputs and outputs

- **Input**: `record: EnrichmentRecord`; `present_fields: set[str] | None = None` — the set of `EnrichmentRecord` field names whose columns exist in the source file. When given, the `G2-VAL-*` rules fire only for present-but-blank columns; when `None`, every field is assumed present (`enrichment/issue_detection.py:488-503`).
- **Output**: `list[str]` of issue codes, ordered by `ISSUE_CATALOGUE` key order (`enrichment/issue_detection.py:510`).

#### 3 Pseudocode

Source: `enrichment/issue_detection.py:488-510` (dispatcher) and the five group functions cited inline.

1. `found ← ∅` (a set; each rule adds at most once, duplicates collapse). [504]
2. `_detect_wrong_field(record, found)` [505 → 221-317]:
   1. For each Name 1–4: if `_extract_addresses(nm)` finds a fragment → add G1-CROSS-001, stop. [226-229]
   2. For each Street 1–5: strip `_UNIVERSITY_CENTRE_RE`; if `_ORG_IN_STREET_RE` matches the stripped value and `_STREET_TYPE_WORD_RE` does not match the original → add G1-CROSS-002, stop. [231-243]
   3. For each Name and Street: if email/phone/URL/c-o-ATTN regex matches → add G1-CROSS-003, stop. Else (for-else) for each Street: if `_street_person_name(st)` → add G1-CROSS-003, stop. [245-262]
   4. If `house_number` blank: for each Street, if `_looks_like_street` → add G1-ADDR-001, stop. [264-269]
   5. For each Street: any `_SUITE_PATTERNS` hit → G1-ADDR-003 [272-276]; `_PO_BOX_RE` hit → G1-ADDR-004 [278-282]; `_extract_mail_code(st, allow_bare=True)[1]` → G1-ADDR-006 [284-288]; `_looks_like_department(st)` → G1-ADDR-011 [290-294].
   6. If Name 1 and Name 2 non-blank, Name 1 lacks a legal suffix, and Name 2 matches `_NAME_CONTINUATION_RE` → G1-NAME-001. [296-305]
   7. If Name 2 blank and Name 3 non-blank → G1-NAME-004. [307-309]
   8. For each Name: `_is_opaque_code(nm)` → G1-NAME-013, stop. [311-315]
3. `_detect_missing(record, found, present_fields)` [506 → 324-369]:
   1. For each `(field, code)` in `_REQUIRED_FIELD_CODES`: skip if `present_fields` given and field absent; else if `is_blank(getattr(record, field))` → add code. [330-334]
   2. If `looks_like_university_or_research_institute(name_1)` and Name 2 blank → G2-NAME-012. [342-343]
   3. If `is_granular_unit(name_2)` and neither Name 3 nor Name 4 is a unit construction → G2-NAME-009. [347-351]
   4. Under the same university gate as step 2: if contact and care-of blank and G2-NAME-012 not found → G2-CONTACT-008 (unreachable, §1.3); elif contact non-blank and not multi-contact → G2-CONTACT-009. [364-369]
4. `_detect_duplicate(record, found)` [507 → 376-432]: DBA in a name → G3-NAME-003 [380-384]; adjacent duplicate names → G3-NAME-005 [386-390]; PO-box count ≥2 → G3-ADDR-005 [392-401]; duplicate street signature (house number folded into slot 1 only, `idx == 0`) → G3-ADDR-012 [403-417]; ≥2 distinct real streets → G3-ADDR-013 [419-424]; PO box + real street → G3-ADDR-014 [426-428]; multi-contact → G3-CONTACT-007 [430-432].
5. `_detect_format(record, found)` [508 → 439-465]: combined name length > 140 → G4-NAME-015 [440-443]; bare trailing marker → G4-ADDR-008 [445-449]; postal-format mismatch (US/CA only) → G4-ADDR-026 [451-456]; country not canonical ISO-2 → G4-ADDR-027 [458-463].
6. `_detect_naming(record, found)` [509 → 472-481]: abbreviation token in Name 1 → G5-NAME-001; in Name 2–4 → G5-NAME-002.
7. Return `[code for code in ISSUE_CATALOGUE if code in found]` — catalogue-order projection of the set. [510]

Field-presence helpers: `_names` returns `[name_1, name_2, name_3, name_4]` (`enrichment/issue_detection.py:173-174`); `_streets` returns `[street_1 … street_5]` (177-184); `_norm` folds case and whitespace for equality (187-189); `_street_signature` builds the `(frozenset(digit-tokens), sorted word tuple)` pair, folding the dedicated House Number in only when the line has no inline number (192-214); `is_blank(value)` is `value is None or value.strip() == ""` (`utils/text_utils.py:18-20`).

#### 4 Constants (verbatim, with definition sites)

Defined in `enrichment/issue_detection.py`:

```python
_SAP_NAME_LIMIT = 140                                        # line 121

_REQUIRED_FIELD_CODES: list[tuple[str, str]] = [             # lines 129-137
    ("name_1", "G2-VAL-001"),
    ("postal_code", "G2-VAL-002"),
    ("tax_jurisdiction", "G2-VAL-003"),
    ("region", "G2-VAL-004"),
    ("language_key", "G2-VAL-006"),
    ("search_term_1", "G2-VAL-007"),
    ("country_region_key", "G2-VAL-008"),
]

_NAME_CONTINUATION_RE = re.compile(                          # lines 141-143
    r"^\s*(?:and|&|of|for|the|de|der|und|et)\b|^\s*[a-z]",
)

_ABBREV_TOKEN_RE = re.compile(                               # lines 148-152
    r"\b(?:Univ|Dept|Dep|Div|Inst|Natl|Nat'l|Intl|Int'l|Assoc|Assn|Ctr|"
    r"Lab|Labs|Tech|Sch|Mgmt|Engrg|Eng|Sci|Med|Svcs|Svc|Co)\b\.?",
    re.IGNORECASE,
)

_ORG_IN_STREET_RE = re.compile(                              # lines 157-164
    r"\b(?:University|Universit[äa]t|Institute|Institut|College|Faculty|"
    r"School|Hospital|Clinic|Corp(?:oration)?|Inc|Incorporated|LLC|Ltd|"
    r"Limited|Company|GmbH|Technolog(?:y|ies)|Systems|Solutions|"
    r"Laborator(?:y|ies)|Labs|Industries|Sciences|Instruments|"
    r"Pharmaceuticals?|Pharma)\b",
    re.IGNORECASE,
)

_POSTAL_FORMATS: dict[str, re.Pattern[str]] = {              # lines 167-170
    "US": re.compile(r"^\d{5}(?:-\d{4})?$"),
    "CA": re.compile(r"^[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d$"),
}
```

Reused regexes imported at `enrichment/issue_detection.py:38-59`, verbatim from their definition sites:

`enrichment/preprocess.py`:

```python
_EMAIL_RE = re.compile(                                      # lines 115-117
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)

_URL_RE = re.compile(                                        # lines 533-540
    r"\b(?:https?://|www\.)\S+"
    r"|(?<!@)\b[A-Za-z0-9][A-Za-z0-9\-]*\."
    r"(?:com|org|net|edu|gov|io|co|us|biz|info|gmbh|de|uk|ca)\b(?:/\S*)?",
    re.IGNORECASE,
)

_PHONE_RE = re.compile(                                      # lines 543-548
    r"(?:\b(?:tel|telephone|phone|fax|ph|cell|mobile|mob)\b\.?[:\s#]*)?"
    r"\+?\(?\d{2,4}\)?(?:[\s.\-]\d{2,4}){2,4}"
    r"(?:\s*(?:ext|x|extension)\.?\s*\d+)?",
    re.IGNORECASE,
)

_CO_ATTN_PREFIX_RE = re.compile(                             # lines 655-658
    r"^\s*(?:c\s*/\s*o|att?n+(?:ention|tion)?|att)\s*[:\-]?\s*",
    re.IGNORECASE,
)

_OPAQUE_CODE_RE = re.compile(                                # lines 312-314
    r"^\s*[A-Za-z]{0,4}[-]?\d{5,}\s*$",
)

_STREET_PERSON_RE = re.compile(                              # lines 585-591
    r"^\s*("
    r"(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx|Sir|Rev|Hon)\.?\s+"
    r"[A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,3}"
    r")(?:\s*[-,]\s*.+)?$",
    re.IGNORECASE,
)

_STREET_SUFFIX_GUARD_RE = re.compile(                        # lines 596-600
    rf"\b(?:{_STREET_SUFFIXES})\b\.?"
    r"(?:\s+(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West))?\s*$",
    re.IGNORECASE,
)

_STREET_SUFFIXES = (                                         # lines 138-142
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Way|Hwy|Highway|Pl|Place|Pkwy|Parkway|Ct|Court|Ter|Terrace|"
    r"Cir|Circle|Sq|Square"
)

_LEGAL_SUFFIX_RE = re.compile(                               # lines 739-751 (start)
    r"\b("
    r"L\.?L\.?C\.?|"
    r"Inc\.?|Incorporated|"
    r"Corp\.?|Corporation|"
    r"Ltd\.?|Limited|"
    r"Co\.?|Company|"
    r"L\.?L\.?P\.?|L\.?P\.?|"
    r"GmbH|S\.?A\.?(?:S\.?)?|"
    r"AG|N\.?V\.?|B\.?V\.?|"
    r"PLC|Pty|"
    r"P\.?C\.?"
    r")\.?\b",

_DBA_PATTERNS = [                                            # lines 267-280
    re.compile(r"\bdoing\s+business\s+as\b", re.IGNORECASE),
    re.compile(r"\bd\.?\s+business\s+as\b", re.IGNORECASE),
    re.compile(r"\bd\s*\.?\s*/\s*b\s*\.?\s*/\s*a\b\.?", re.IGNORECASE),
    re.compile(r"\bd\.\s*b\.\s*a\.?", re.IGNORECASE),
    re.compile(r"\bd\s*b\s*a\b", re.IGNORECASE),
]

_MULTI_CONTACT_SEPARATOR_RE = re.compile(                    # lines 1065-1068
    r"\s+(?:and|or)\s+|\s*&\s*|[;/]|\s\+\s",
    re.IGNORECASE,
)
```

The six `_ADDRESS_PATTERNS` used by `_extract_addresses` are built from `_STREET_TOKEN = r"(?:[A-Z][\w\-]*|\d+(?:st|nd|rd|th))"` (preprocess.py:154), `_DIRECTION = r"(?:N\.?W\.?|N\.?E\.?|S\.?W\.?|S\.?E\.?|N\.?|S\.?|E\.?|W\.?)"` (preprocess.py:157) and `_BUILDING_PLACE_WORDS = r"Hall|Building|Bldg|Pavilion|Tower|Annex|Wing|Complex"` (preprocess.py:147-149); the full pattern list is at `enrichment/preprocess.py:158-197` (numbered street; number+building; building+room-number; bare street name anchored to the whole value; `\b(?:Suite|Ste|Unit|Floor|Bldg|Building|Room|Rm)\b\.?\s+[\w\-]+\b`; `\b(?:P\.?\s*O\.?\s*Box|Post\s+Office\s+Box|Mail\s*Box|Mailbox|Box)\s+\w+\b`).

`enrichment/address_processing.py`:

```python
_PO_BOX_RE = re.compile(                                     # lines 199-202
    r"\b(?:P\.?\s*O\.?\s*Box|POB|Post\s+Office\s+Box)\s+(\w+)\b",
    re.IGNORECASE,
)

_BARE_MARKER_RE = re.compile(                                # lines 254-257
    r"\b(?:Suite|Ste|Bldg|Building|Floor|Fl|Room|Rm|Unit|Mail\s+Stop|MS)\b\s*$",
    re.IGNORECASE,
)

_STREET_TYPE_WORD_RE = re.compile(                           # lines 466-470
    r"\b(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Way|Pl|Place|Ter|Terrace)\b\.?",
    re.IGNORECASE,
)
_HOUSE_NUMBER_RE = re.compile(r"\b\d+\b")                    # line 471

_UNIVERSITY_CENTRE_RE = re.compile(                          # lines 482-485
    r"\bUniversit(?:y|[äa]t)\s+(?:Centre|Center|Ctr|Ctre|Cntr|Cent)\b\.?",
    re.IGNORECASE,
)

_DEPARTMENT_PAYLOAD_RE = re.compile(                         # lines 333-336
    r"\b(?:Department|Dept\.?|Division|Div\.?)\b",
    re.IGNORECASE,
)

_MAIL_CODE_EXPLICIT_RE = re.compile(                         # lines 397-400
    r"\bMAIL\s*CODE\s*[:\-]?\s*([\w\-]+)\b",
    re.IGNORECASE,
)
_MAIL_CODE_COMPLEX_RE = re.compile(r"\b([A-Z]\d-\d{4})\b")   # line 401
_MAIL_CODE_BARE_RE = re.compile(r"\b([A-Z]{2,4}\d{1,4})\b")  # line 405
_STREET_TYPE_ABBREVS = {                                     # lines 406-409
    "ST", "AVE", "BLVD", "DR", "RD", "LN", "CT", "HWY", "PKWY", "RTE",
    "STR", "PL", "TER", "PKY", "CIR", "SQ", "WAY",
}
```

The 13 `_SUITE_PATTERNS` entries (each a `(compiled regex, target-field)` pair covering Campus Box, Mail Stop/MS, Suite/Ste, Bldg/Building, Floor/Fl (marker-first, value-first, `\d+F`, trailing bare ordinal), Room/Rm with filler, `Lab <id>`, trailing room code, `Unit`, and `#`) are at `enrichment/address_processing.py:218-250`.

`utils/text_utils.py`:

```python
_UNIVERSITY_OR_RESEARCH_SIGNALS_RE = re.compile(             # lines 387-392
    r"\b(?:University|College|College\s+of|Institute|Research|Academy|"
    r"Medical\s+School|School\s+of|Faculty\s+of|"
    r"Schule|Universit[aä]t|Université|Universidade)\b",
    re.IGNORECASE,
)
```

`is_blank` — `utils/text_utils.py:18-20`; `country_to_iso_code` — dictionary lookup of the upper-cased stripped value in `_COUNTRY_TO_ISO`, `utils/text_utils.py:900-908`; `is_granular_unit` — `utils/text_utils.py:410-439` (in-scope "Department/Division/School/College/Faculty of/for …" heads are never granular); `is_unit_construction` — `utils/text_utils.py:477-505`; `is_specific_unit_construction` — `utils/text_utils.py:508-526`; `looks_like_university_or_research_institute` — `utils/text_utils.py:395-407`.

#### 5 Complexity

Let F = 9 scanned free-text fields (4 names + 5 streets) and L = max field length. Each rule is a constant number of regex scans over at most F fields; the module applies a fixed set of ~40 compiled patterns (6 address patterns × loop in `_extract_addresses`, 13 suite patterns, plus single regexes). Per record the cost is O(F · P · L) with P the fixed pattern count — effectively linear in total record text; no rule is super-linear except `_extract_addresses`'s repeat-until-no-match loop, bounded by the number of non-overlapping matches (each iteration removes matched text, `enrichment/preprocess.py:249-255`). The final projection is O(37) (`enrichment/issue_detection.py:510`). Per file, `POST /issues` calls `detect_issues` once per row (`api/routes.py:606`), so total cost is linear in row count.

#### 6 Worked example

From `test_multiple_issues_all_reported` (`tests/test_issue_detection.py:335-344`), using the baseline record of `_record` (`tests/test_issue_detection.py:26-35`) with overrides `Name 1 = "Univ of Florida"`, `Postal Code = ""`, `Street 1 = "PO BOX 115350"`, `Country/Region Key = "USA"` (Name 2 stays "Engineering Department"; Tax Jurisdiction "TX0000000", Region "FL", Language Key "EN", Search Term 1 "ACME"), `present_fields=None`:

- G1-ADDR-004: `_PO_BOX_RE` matches `"PO BOX 115350"` in Street 1 (`enrichment/issue_detection.py:278-282`).
- G2-VAL-002: `postal_code` is blank and `present_fields is None` (`enrichment/issue_detection.py:330-334`).
- G4-ADDR-027: `country_to_iso_code("USA") == "US"` and `"USA".upper() != "US"` (`enrichment/issue_detection.py:458-463`).
- G5-NAME-001: `_ABBREV_TOKEN_RE` matches the token `Univ` in Name 1 (`enrichment/issue_detection.py:473-475`).

Re-executing `detect_issues` on this record returns exactly `['G1-ADDR-004', 'G2-VAL-002', 'G4-ADDR-027', 'G5-NAME-001']` — the four codes the test asserts, in catalogue order (verified by execution; ordering guarantee `enrichment/issue_detection.py:510` and `tests/test_issue_detection.py:52-56`). Note Street 1 = "PO BOX 115350" does not additionally fire G1-ADDR-001 because `_looks_like_street` also requires a street-type word (`enrichment/address_processing.py:538-544`), and "Univ of Florida" does not fire G2-NAME-012 because `_UNIVERSITY_OR_RESEARCH_SIGNALS_RE` requires the full word "University" (`utils/text_utils.py:387-392`); the abbreviated form matches only `_ABBREV_TOKEN_RE`.

#### 7 Failure modes

- **Unreachable rule**: G2-CONTACT-008 can never be returned (§1.3) — a caller counting "codes the detector can emit" over-counts by one.
- **Heuristic proxies**: G1-NAME-001, G5-NAME-001/002, and the G2 department rules are conservative deterministic stand-ins for semantic rules; the module docstring states they favour precision over recall (`enrichment/issue_detection.py:26-28`). E.g. `_ABBREV_TOKEN_RE` includes `Co`, so a legal suffix "Co." in a name fires G5; "Eng" fires but "Engineering" does not.
- **Postal formats cover only US and CA** (`enrichment/issue_detection.py:167-170`): a malformed German or UK postcode never fires G4-ADDR-026.
- **Column gating only protects `G2-VAL-*`**: the non-VAL missing-data rules (G2-NAME-009/012, G2-CONTACT-009) ignore `present_fields`, so a file that genuinely lacks a Contact column can still be judged on `record.contact` being blank (`enrichment/issue_detection.py:342-369`).
- **`_street_signature` folds House Number into Street 1 only** (`idx == 0`, `enrichment/issue_detection.py:409-415`): a house number conventionally paired with Street 2 would not be detected as a duplicate.
- **No exception paths**: all helpers accept `None` and return early (e.g. `enrichment/issue_detection.py:187-189, 196-207`); the function cannot raise on well-typed input.

---

## PART 2 — Shared normalisation across the three issue paths

`api/routes.py` was read in full (1118 lines). `detect_issues` from `enrichment.issue_detection` is imported once (`api/routes.py:44`) and called at exactly two sites: `api/routes.py:606` (`POST /issues`) and `api/routes.py:406` (`_audit_upload`, used twice by `POST /issues/compare`, `api/routes.py:644-645`). The similarly named `detect_issues` imported as `detect_dedup_issues` (`api/routes.py:39`) belongs to the dedup scoring module and is unrelated (used only at `api/routes.py:934`).

### 2.1 Path A — POST /issues (`detect_file_issues`, api/routes.py:580-625)

Pipeline: extension/emptiness guards (592-601) → `_parse_xlsx` (603) → `_rows_to_records` (604) → `_present_fields` (605) → `detect_issues(record, present)` per record (606) → `_build_issues_xlsx` (615).

Normalisation applied before detection:

1. **`_parse_xlsx`** (`api/routes.py:161-224`): the first non-empty row is the header row; header cells become `str(cell).strip()`, `None` cells become `""` (189-197). Every subsequent row becomes a dict keyed by original header; each cell of any type is coerced with `str(cell).strip()` and **empty-after-strip values are omitted from the dict** (207-213); rows with no surviving cells are skipped entirely (214-215). Numeric cells therefore arrive as their `str()` rendering. No data rows → HTTP 400 (219-222).
2. **`_rows_to_records`** (`api/routes.py:227-257`): each raw header is normalised by `_norm_header` — lowercase, keep alphanumerics only (`"".join(ch for ch in str(name).lower() if ch.isalnum())`, 115-123) — and mapped to a model field via `_input_alias_to_field` (126-141), which reverse-maps every `AliasChoices` validation alias plus the field name itself (`populate_by_name`, `api/models.py:40`). So "NAME 1", "Name1", " name 1 " all reach `name_1`. When several headers resolve to one field, the **first non-empty value wins** (241-246). Unrecognised headers pass through under their raw name and are ignored by Pydantic (default `extra` behaviour; `EnrichmentRecord` sets only `populate_by_name`, `api/models.py:40`). Validation failures aggregate into an HTTP 422 (249-256).
3. **`_present_fields`** (`api/routes.py:144-158`): maps the header list through the same alias table into the set of model field names carried by the file; passed to `detect_issues` so `G2-VAL-*` only judges columns that exist (see `enrichment/issue_detection.py:329-334`).

No further coercion occurs: `EnrichmentRecord` declares no validators (no `field_validator`/`model_validator` anywhere in `api/models.py`; verified by search), so values reach `detect_issues` exactly as `_parse_xlsx` left them — stripped, non-empty strings or `None`.

### 2.2 Path C — POST /issues/compare audit (`_audit_upload`, api/routes.py:377-414)

`_audit_upload` (read in full; previously UNVERIFIED) applies **the identical chain**: extension guard (384-389), empty-file guard (391-393), `_parse_xlsx` (395), `_rows_to_records` (396), `_present_fields` (397), then `detect_issues(record, present)` per record (406). The only additions are join bookkeeping, not normalisation:

- Rows whose `record.record_id` is empty are excluded from the map and counted (401-404); `record_id` is the property `(self.customer or self.ecc_customer_number or "").strip()` (`api/models.py:229-231`), where `customer` accepts headers "Customer", "customer", "record_id" (`api/models.py:43-47`).
- For a duplicated id, `issue_map.setdefault(rid, detect_issues(...))` keeps the first occurrence (406; docstring 380-383).

**Paths A and C therefore produce identical issue codes for identical input rows** — same parser, same record construction, same column-presence set, same detector call. The only divergence is coverage, not codes: Path C drops rows without a record id (`api/routes.py:401-404`), which Path A annotates like any other row (`api/routes.py:606` iterates all records; a no-identifier row is accepted, `tests/test_routes.py:303-312`).

### 2.3 Path B — POST /enrich and /enrich/file: no catalogue detection; a divergent internal mechanism whose codes are discarded

- **`detect_issues` is never called in the enrichment path.** `POST /enrich` (`api/routes.py:88-107`) goes straight to `orchestrator.enrich_batch`; `POST /enrich/file` (`api/routes.py:518-577`) parses with the same `_parse_xlsx`/`_rows_to_records` helpers (544-545) but never calls `_present_fields` or `detect_issues`. The response workbook columns come from `RESPONSE_COLUMNS` (`api/routes.py:323-324`), which contains no issues column (verified programmatically against `api/output_columns.py`).
- **The pipeline has its own, overlapping issue mechanism.** `AddressResult.address_issues` with the de-duplicating `issue()` method (`enrichment/address_processing.py:118-122`) is populated at these sites: G3-ADDR-012 (`:626`, exact case-insensitive `street_cleaned == street_2_cleaned`, and `:1128`, duplicate street value across slots), G3-ADDR-013 (`:630`, both of Street/Street 2 `_looks_like_street`; and `:891`, ≥2 address segments after splitting), G3-ADDR-014 (`:634`), G1-CROSS-001 (`:639`, via `_NAME_STREET_LIKE_RE = re.compile(r"\b\d+\s+\w+\s+(?:St|Ave|Blvd|Rd|Dr|Ln|Hwy|Pkwy)\b\.?", re.IGNORECASE)`, `enrichment/address_processing.py:486-489`), G1-CROSS-002 (`:650`, using the narrower `_ORG_KEYWORD_RE`, `enrichment/address_processing.py:472-476`), G1-ADDR-009 (`:727, :730, :753, :757` — emitted from the **LLM residual classifier** `_apply_residual_llm`, `enrichment/address_processing.py:702-759`, threshold `_RESIDUAL_CONFIDENCE_THRESHOLD = 0.85`, `:657`), G1-ADDR-011 (`:734`), G1-CROSS-003 (`:739`), **G3-ADDR-011** (`:998`, "street slots full — content left over"), G3-ADDR-005 (`:1022`), G4-ADDR-008 (`:1061`).
- **Divergences from the catalogue detector**, had the codes surfaced: (i) `G3-ADDR-011` is **not a key of `ISSUE_CATALOGUE`** at all (verified programmatically; catalogue `enrichment/issue_detection.py:75-118`); (ii) the pipeline's G1-CROSS-001/G1-CROSS-002 predicates differ from `detect_issues`' (`_NAME_STREET_LIKE_RE` / `_ORG_KEYWORD_RE` vs `_extract_addresses` / the broader `_ORG_IN_STREET_RE`, compare `enrichment/address_processing.py:486-489, 472-476` with `enrichment/issue_detection.py:226-243`); (iii) the pipeline's G1-ADDR-009 is model-assisted, which is exactly why `detect_issues` declares it never-emitted (`enrichment/issue_detection.py:18-25`).
- **These codes never reach the /enrich response.** They are merged into the orchestrator's result dict under `"address_issues"` (initialised `enrichment/orchestrator.py:351`; merged by `merge_into_result`, `enrichment/address_processing.py:1213-1219`) and logged (`enrichment/address_processing.py:1142-1151`), but the dict is then materialised with `EnrichmentResult(**result)` (`enrichment/orchestrator.py:1573`; failure branch `:821`), and `EnrichmentResult` declares **no** `address_issues` field (full field list, `api/models.py:304-427`). Under Pydantic 2.12.5's default `extra` handling the key is silently discarded — verified by direct execution: `EnrichmentResult(record_id='X', address_issues=['G1-CROSS-001'])` constructs, `hasattr(r, 'address_issues')` is False, and the key is absent from `model_dump()`. `RESPONSE_COLUMNS` likewise has no issues column, so `/enrich/file`'s workbook (`_build_output_xlsx`, `api/routes.py:306-348`) carries none.

**Conclusion:** the three paths do **not** all agree. `/issues` and `/issues/compare` are code-identical per row (§2.1-2.2). `/enrich` performs no catalogue detection and emits no issue codes in any response or file; internally it computes a different, partially overlapping code set (including one code, G3-ADDR-011, outside the catalogue, and one LLM-derived code, G1-ADDR-009) that is dropped before serialisation. Any before/after comparison must therefore run the deterministic detector over the enriched *file* via `/issues/compare` — which is precisely the design stated in the detector's docstring (`enrichment/issue_detection.py:9-12`).

---

## PART 3 — Issue formatting contract

### 3.1 The Issues column (POST /issues)

`_build_issues_xlsx` (`api/routes.py:351-374`) echoes the uploaded sheet and appends one column:

- **Header row**: `[*headers, "Issues"]` — the original headers verbatim, with `Issues` as the last column (`api/routes.py:366`; asserted by `tests/test_routes.py:340-345`).
- **Data rows**: for each `(row_dict, codes)` pair, the original cell values are re-emitted in header order with `""` for absent cells, then `"; ".join(codes)` — the codes joined with the two-character separator `"; "`, empty string when the row is clean (`api/routes.py:368-370`).
- **Order within the cell**: `detect_issues` returns codes in `ISSUE_CATALOGUE` key order (`enrichment/issue_detection.py:510`), so the joined cell is catalogue-ordered (G1 → G5).

Worked example (real test, `test_issues_multiple_codes_semicolon_joined`, `tests/test_routes.py:354-365`): upload headers `["Name 1", "Postal Code", "Country/Region Key"]`, row `["Univ of Florida", "", "USA"]`. `_parse_xlsx` drops the empty Postal Code cell (`api/routes.py:211-213`) but `_present_fields` still records `postal_code` from the header (`api/routes.py:144-158`). Re-executed, `detect_issues` returns `['G2-VAL-002', 'G4-ADDR-027', 'G5-NAME-001']`, so the Issues cell is `"G2-VAL-002; G4-ADDR-027; G5-NAME-001"`; the test asserts these three codes after splitting on `;` (`tests/test_routes.py:363-365`).

### _audit_upload

#### 1 Purpose

Validates, parses, and audits one XLSX upload into a `{record_id: [codes]}` map for the `/issues/compare` join (`api/routes.py:377-383`).

#### 2 Inputs and outputs

- **Input**: `file: UploadFile` (FastAPI multipart upload).
- **Output**: `dict[str, list[str]]` mapping record id → catalogue-ordered issue codes. Raises `HTTPException` 400 for a non-`.xlsx`/`.xlsm` filename, an empty upload, an unparseable workbook, a missing header row, or no data rows; 422 for row validation failures (via `_parse_xlsx`/`_rows_to_records`).

#### 3 Pseudocode

Source: `api/routes.py:377-414`.

1. If `file.filename` does not end (case-insensitively) with `.xlsx` or `.xlsm` → HTTP 400. [384-389]
2. `contents ← await file.read()`; empty → HTTP 400. [391-393]
3. `headers, row_dicts ← _parse_xlsx(contents)` (§2.1 step 1). [395]
4. `records ← _rows_to_records(row_dicts)` (§2.1 step 2). [396]
5. `present ← _present_fields(headers)` (§2.1 step 3). [397]
6. For each record: `rid ← record.record_id`; if empty → increment `excluded`, skip; else `issue_map.setdefault(rid, detect_issues(record, present))` — first occurrence wins for a duplicated id. [399-406]
7. If any rows were excluded, log the count (excluded rows are logged, not silently dropped). [408-413]
8. Return `issue_map`. [414]

#### 4 Constants

None beyond the accepted extensions tuple `(".xlsx", ".xlsm")` (`api/routes.py:385`). All detection constants are those of `detect_issues` (Part 1 §4).

#### 5 Complexity

O(R · C) to parse (R rows × C columns, `api/routes.py:203-215`), O(R) `detect_issues` calls each linear in row text (Part 1 §5), O(R) map insertions. Memory holds the full row list and map — linear in file size.

#### 6 Worked example

From `test_issues_compare_reports_reduction` (`tests/test_routes.py:397-417`). Original upload: headers `["Customer", "Name 1", "Name 2", "Postal Code", "Country/Region Key"]`, row `["R1", "Acme Corp", "10901 Roosevelt Blvd N", "", "US"]`. Traced (re-executed): `present = {customer, name_1, name_2, postal_code, country_region_key}`; `detect_issues` → `['G1-CROSS-001', 'G2-VAL-002']` (address fragment "10901 Roosevelt Blvd N" in Name 2 matched by `_ADDRESS_PATTERNS[0]`; Postal Code present-but-blank). Enriched upload: headers `["record_id", "Name 1", "Name 2", "Country/Region Key"]`, row `["R1", "Acme Corporation", "Sales Department", "US"]`; `record_id` aliases to `customer` (`api/models.py:43-47`), `present` lacks `postal_code`, and `detect_issues` → `[]`. So `before_map = {"R1": ['G1-CROSS-001', 'G2-VAL-002']}`, `after_map = {"R1": []}`. (The test asserts the weaker properties: matched = 1, before > after, G1-CROSS-001 in the Resolved column, `tests/test_routes.py:425-434`.)

#### 7 Failure modes

- A row without any customer identifier is silently excluded from the comparison (logged only, `api/routes.py:401-413`) — issue counts from `/issues` (which includes such rows) and `/issues/compare` can differ on the same file.
- A duplicated record id keeps only the first row's codes (`setdefault`, `api/routes.py:406`); later duplicates are neither audited into the map nor logged.
- Ids are joined after `.strip()` only (`api/models.py:231`); a numeric Customer cell rendered differently across the two files (e.g. "1001" vs "1001.0" from a float-typed cell — rendering follows Python `str()` of whatever openpyxl yields, `api/routes.py:211`) would fail to join. ⚠ NO FIXTURE COVERAGE for float-typed id cells.

### _build_comparison_xlsx

#### 1 Purpose

Builds the before/after issue-reduction report workbook for `POST /issues/compare`: headline totals, a per-code Before/After/Delta table, a per-record breakdown, and a remaining-issues listing (`api/routes.py:417-430`).

#### 2 Inputs and outputs

- **Input**: `before_map`, `after_map` — the two `{record_id: [codes]}` maps from `_audit_upload`.
- **Output**: `bytes` of an XLSX workbook with sheets `Summary`, `Per Record`, `Remaining Issues` (order asserted by `tests/test_routes.py:422`). Returned by the route with filename `issue_reduction_report.xlsx` (`api/routes.py:655-664`).

#### 3 Pseudocode

Source: `api/routes.py:417-515`.

1. Partition ids: `matched_ids` = ids in both maps (insertion order of `before_map`); `only_before`; `only_after`. [433-435]
2. Zero the running totals; create `Counter`s `code_before`, `code_after`. [437-440]
3. For each matched id: `bset, aset ← set(before), set(after)`; `resolved ← [c for c in ISSUE_CATALOGUE if c in bset - aset]` and `introduced ← [c for c in ISSUE_CATALOGUE if c in aset - bset]` — set differences re-ordered by catalogue order for stable output; accumulate `total_before += len(before)`, `total_after += len(after)`, `total_resolved`, `total_introduced`; update the per-code counters with the *sets* (so each code counts once per record); append the per-record row `[rid, "; ".join(before), "; ".join(after), "; ".join(resolved), "; ".join(introduced)]`. [442-463]
4. `net ← total_before - total_after`; `pct ← net / total_before * 100` when `total_before` else `0.0`. [465-466]
5. Sheet 1 "Summary": title row; blank; `Records matched (joined by id)` / `Records only in original` / `Records only in enriched`; blank; `Total issues before` / `Total issues after` / `Issues resolved` / `Issues introduced` / `Net reduction` / `Reduction %` (rounded to 1 dp); blank; then the per-code table with header `["Code", "Name", "Before", "After", "Delta"]` and one row per catalogue code (catalogue order) **only when** `before_count or after_count` is non-zero, with `Delta = after_count - before_count`. [468-491]
6. Sheet 2 "Per Record": header `["record_id", "Issues Before", "Issues After", "Resolved", "Introduced"]`, then the accumulated rows. [493-498]
7. Sheet 3 "Remaining Issues": header `["Code", "Name", "Customer"]`; for every catalogue code (catalogue order), one row per record in `after_map` (matched + enriched-only) still carrying that code, customer ids sorted. [506-511]
8. Save to a `BytesIO` and return the bytes. [513-515]

#### 4 Constants (verbatim)

Summary labels and sheet/table headers as written: `"Issue Reduction Summary"`, `"Records matched (joined by id)"`, `"Records only in original"`, `"Records only in enriched"`, `"Total issues before"`, `"Total issues after"`, `"Issues resolved"`, `"Issues introduced"`, `"Net reduction"`, `"Reduction %"`, `["Code", "Name", "Before", "After", "Delta"]` (`api/routes.py:471-484`); `["record_id", "Issues Before", "Issues After", "Resolved", "Introduced"]` (`api/routes.py:494-496`); `["Code", "Name", "Customer"]` (`api/routes.py:507`); join separator `"; "` (`api/routes.py:459-462`); rounding `round(pct, 1)` (`api/routes.py:482`). The per-code table and Remaining Issues iterate `ISSUE_CATALOGUE.items()` — the 37-entry dict of Part 1 (`api/routes.py:485, 508`).

#### 5 Complexity

O(M · K) for the matched loop (M matched records, K ≤ 37 codes per record; the catalogue-order projections are O(37) each), O(37 · |after_map|) for the Remaining Issues sweep (each of the 37 codes scans every after-map entry, `api/routes.py:508-511`), plus O(|after_map| log |after_map|) per code for the id sort. Linear-ish in records for practical file sizes.

#### 6 Worked example

Continuing §_audit_upload 6 (`tests/test_routes.py:397-449`): `before_map = {"R1": ['G1-CROSS-001', 'G2-VAL-002']}`, `after_map = {"R1": []}`. Traced through the source: `matched_ids = ["R1"]`, `only_before = only_after = []`; `bset - aset = {G1-CROSS-001, G2-VAL-002}` → `resolved = ['G1-CROSS-001', 'G2-VAL-002']` (catalogue order), `introduced = []`; totals: before 2, after 0, resolved 2, introduced 0; `net = 2`, `pct = 100.0`. Summary per-code rows: `["G1-CROSS-001", "Address Content in Name Field", 1, 0, -1]` and `["G2-VAL-002", "Postal Code Missing", 1, 0, -1]` (names from `enrichment/issue_detection.py:77, 91`). Per Record row: `["R1", "G1-CROSS-001; G2-VAL-002", "", "G1-CROSS-001; G2-VAL-002", ""]`. Remaining Issues: header only. The test asserts the containment/inequality forms of these values: matched = 1, before > after, resolved ≥ 1, `"G1-CROSS-001"` in the Resolved column, and `len(remaining rows) == Total issues after` (`tests/test_routes.py:424-449`).

#### 7 Failure modes

- **Asymmetric column sets can masquerade as "resolved" issues**: comparison is column-aware per file, so a rule that fired in the original but was skipped in the enriched file because the enriched export dropped the column counts as resolved for that record. This is the documented intent ("apples-to-apples", `api/routes.py:640-642`) but it means "Issues resolved" conflates fixed-value and column-dropped cases — the §6 example's G2-VAL-002 is resolved by column omission, not by a filled value.
- **Records present in only one file contribute nothing to any total** except the two "Records only in …" counts (`api/routes.py:433-435, 474-475`); Sheet 3 alone also covers enriched-only records (`api/routes.py:503-509`).
- `Reduction %` reports `0.0` when `total_before` is zero even if issues were introduced (`api/routes.py:466`).
- Per-code Before/After counts are per-record (set-based, `api/routes.py:454-455`), while `Total issues before/after` are per-occurrence over the returned lists (`api/routes.py:450-451`); since `detect_issues` never returns duplicates within a row (set-projected, `enrichment/issue_detection.py:504-510`), these agree in practice, but the two aggregations are computed differently.

---

### Non-determinism notes

The `/issues` and `/issues/compare` paths are fully deterministic:

- `detect_issues` and its helpers perform regex/string computation only; the module's imports contain no LLM, HTTP, or file I/O (`enrichment/issue_detection.py:31-67`), matching its stated design constraint ("No enrichment, no LLM call, no network/external I/O", `enrichment/issue_detection.py:8-12, 494-495`).
- The route paths add only openpyxl parsing/writing and logging: `detect_file_issues` (`api/routes.py:580-625`), `_audit_upload` (`api/routes.py:377-414`), `_build_issues_xlsx` (`api/routes.py:351-374`), and `_build_comparison_xlsx` (`api/routes.py:417-515`) call no orchestrator, LLM client, or network function; the `/issues` docstring states "no enrichment, LLM, or external call is made" (`api/routes.py:589-591`) and the code bears it out.
- Output ordering is stable everywhere: codes in catalogue order (`enrichment/issue_detection.py:510`), resolved/introduced re-ordered by catalogue order (`api/routes.py:446-448`), Remaining Issues ordered by catalogue order then sorted customer id (`api/routes.py:508-509`), and dict iteration order (Python insertion order) fixes row order elsewhere.

By contrast, the `/enrich` path's internal `address_issues` mechanism is **not** fully deterministic — G1-ADDR-009 is assigned from an LLM classification with a 0.85 confidence threshold (`enrichment/address_processing.py:657, 702-759`) — but those codes are discarded before any response is serialised (§2.3), so no non-determinism reaches an issue-bearing output.

⚠ Flags carried in this document: the module docstring's "36-code"/"34 emitted" figures are stale (§1.2); G2-CONTACT-008's emission site is unreachable (§1.3); ⚠ NO FIXTURE COVERAGE for float-typed record-id cells in the compare join (_audit_upload §7).


# Part I — Deduplication Step A (signature collapse) and Step B (adjudication)

The address gate that assigns `[Block ID]` is **not** implemented in this repository: the value is precomputed by DATAshaper and read from the request (`docs/thesis/02_ARCHITECTURE.md:142-144`). Section 2 documents the block-key procedures the service does own — `derive_block_id`, the precedence rule that makes a supplied id win, and `cluster_hash`. The external gate's own blocking predicate is ⚠ NOT DOCUMENTABLE FROM THIS REPOSITORY.

Scope: exact-signature collapse, block/cluster key derivation, per-block adjudication (Mode A / Mode B), residue candidate nomination and adjudication, deterministic post-enforcement, row emission, and the LLM call layer. All paths are relative to `enrichment_api/`.

Architectural note on the address gate: the request model states that "Every row in a request already shares the same physical address as the other rows in its block (the address gates ran upstream)" (dedup/models.py:21-24), and the route docstring states rows are "already cleared by DATAshaper's address gates — same country + postal code + street" (api/routes.py:806-808). The adjudicator therefore treats the supplied `block_id` as authoritative; `derive_block_id` is a fallback used only when a row arrives with a null/blank `block_id` (dedup/signatures.py:95-99; verified in code, see the block-id procedure below).

---

### Exact-signature collapse, Step A (`build_signatures` — dedup/signatures.py)

#### 1 Purpose
Collapses a block's raw rows into distinct *signatures* — one per distinct `(norm_name1, norm_name2)` key — so that "100 byte-identical rows collapse to one signature; the LLM only ever works on distinct signatures, never on raw rows" (dedup/signatures.py:3-5). The normalised key is internal only; the LLM always sees the original, un-normalised names (dedup/signatures.py:7-8, 66-69).

#### 2 Inputs and outputs
- Input: `List[DedupRow]` (dedup/signatures.py:111). `DedupRow` is a Pydantic v2 model with fields `row_id: str` (required), `block_id: Optional[str]`, `name1: Optional[str]`, `name2: Optional[str]`, `street/house_no/postal_code/city/country: Optional[str]`, `ror_id: Optional[str]`, `lei_id: Optional[str]`, `enriched_name: Optional[str]` (dedup/models.py:18-47).
- Output: `List[Signature]` in first-appearance order with block-local ids `s1`, `s2`, … (dedup/signatures.py:112-117, 144-147). `Signature` is a dataclass carrying `signature_id`, `norm_name1`, `norm_name2`, original `name1`/`name2`, `ror_id`, `row_ids: List[str]`, `uncertain: bool = False`, `lei_id: Optional[str] = None`, `merge_reasoning: Optional[str] = None`, `merge_confidence: Optional[float] = None` (dedup/signatures.py:59-83).

#### 3 Pseudocode
Normalisation (`normalize_key`, dedup/signatures.py:29-42):
1. If the value is falsy, return `""` (dedup/signatures.py:33-34).
2. Apply Unicode NFKD normalisation and drop combining marks, folding accents ("`Universität` and `Universitat` collapse together") (dedup/signatures.py:31-37).
3. Lowercase and trim (dedup/signatures.py:38).
4. Replace every character matching `[^\w\s]` (Unicode-aware) with a space — "so `u.s.a` -> `u s a`, not `usa`" (dedup/signatures.py:25, 39-40).
5. Collapse runs of whitespace (`\s+`) to a single space and strip (dedup/signatures.py:26, 41).
Legal forms (GmbH, AG, Inc.) are deliberately NOT stripped and abbreviations are NOT expanded at this stage — "that is the LLM's job. The key is a conservative collapse only" (dedup/signatures.py:22-24).

Collapse (`build_signatures`, dedup/signatures.py:111-147):
1. For each row in input order: compute `key = (normalize_key(name1), normalize_key(name2))` (dedup/signatures.py:120-123).
2. If the key is new, create a `Signature` capturing the ORIGINAL (stripped but un-normalised) `name1`/`name2` from the FIRST row that produced the key, plus its `ror_id`/`lei_id` if non-empty (dedup/signatures.py:124-136).
3. Append the row's `row_id` to the signature's `row_ids` (dedup/signatures.py:137).
4. Adopt the first non-empty `ror_id` and first non-empty `lei_id` seen among any row sharing the key (dedup/signatures.py:138-142).
5. After all rows, number signatures `s1`, `s2`, … in first-appearance order (dedup/signatures.py:144-147).

Aggregated metadata per signature: member `row_ids`; first non-empty `ror_id`; first non-empty `lei_id`; representative original names (dedup/signatures.py:130-142).

The derived property `has_name2` is `bool(self.norm_name2)` — whether Name 2 is populated after conservative normalisation; it "drives the deterministic asymmetry rule: an empty-Name 2 signature can never share an entity with a populated-Name 2 signature" (dedup/signatures.py:86-92).

#### 4 Constants
```python
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
```
(dedup/signatures.py:25-26)

#### 5 Complexity
O(R) over rows in the block, with one dictionary lookup and two `normalize_key` calls per row (dedup/signatures.py:120-142). The number of distinct signatures S ≤ R bounds all subsequent LLM work; no LLM calls occur in Step A (module docstring, dedup/signatures.py:1-5).

#### 6 Worked example
- `normalize_key("  University   of  Stuttgart!! ") == "university of stuttgart"` and `normalize_key("Universität") == "universitat"`; `None` and `""` both map to `""` (tests/test_dedup.py:82-87).
- 100 rows of `("Acme GmbH", "Chemistry")` collapse to exactly 1 signature with 100 `row_ids` and `signature_id == "s1"` (tests/test_dedup.py:89-94); end-to-end this yields 1 cluster of 100 rows with 0 LLM calls (tests/test_dedup.py:110-127).
- ror/lei adoption: two "Carl Zeiss AG" rows, only the second carrying `ror_id="ror:zeiss"`, `lei_id="LEI-ZEISS-001"` → one signature with both ids adopted (tests/test_dedup.py:97-107).
- Suffix variants are NOT collapsed by Step A: "Pfizer AG" and "Pfizer Inc." remain two distinct signatures (tests/test_dedup.py:578-586).

#### 7 Failure modes
- None raised: `normalize_key` handles `None`/empty by returning `""` (dedup/signatures.py:33-34). Two rows whose names differ only in punctuation/case/accents/whitespace collapse deterministically — a false collapse is possible only if two genuinely different entities have names identical under this normalisation (conservative by design, dedup/signatures.py:22-24).

---

### Block-id and cluster-key derivation (`derive_block_id` — dedup/signatures.py; `cluster_hash` — dedup/cluster_key.py)

#### 1 Purpose
`derive_block_id` provides a stable fallback block id from the normalised address tuple "when a row arrives without a `block_id`" (dedup/signatures.py:45-50). `cluster_hash` mints the stable, content-addressed cluster id shared by the adjudicator (which mints it) and the scorer (which re-derives it) (dedup/cluster_key.py:1-6).

#### 2 Inputs and outputs
- `derive_block_id(row: DedupRow) -> str` (dedup/signatures.py:45); reads `row.country, row.postal_code, row.street, row.house_no` (dedup/signatures.py:51-54). Output: `"blk-" + sha1(joined)[:12]` (dedup/signatures.py:55-56).
- `resolve_block_id(row: DedupRow) -> str`: returns `row.block_id.strip()` when `block_id` is present and non-blank, otherwise `derive_block_id(row)` (dedup/signatures.py:95-99). This is the ONLY place the choice between supplied and derived id is made; `group_rows_by_block` calls it for every row (dedup/signatures.py:102-108). The model documents the same contract: "When null, derived from the normalized (country, postal_code, street, house_no)" (dedup/models.py:30-36).
- `cluster_hash(row_ids: Iterable[str]) -> str` (dedup/cluster_key.py:16): `"c_"` + first 12 hex chars of sha256 over `";".join(sorted(row_ids))` — "Same membership -> same id across runs, machines, and input orderings; a membership change -> a new id. String end-to-end" (dedup/cluster_key.py:16-23).

#### 3 Pseudocode
`derive_block_id` (dedup/signatures.py:45-56):
1. `joined = "|".join(normalize_key(part) for part in (country, postal_code, street, house_no))`.
2. `digest = sha1(joined.encode("utf-8")).hexdigest()[:12]`.
3. Return `f"blk-{digest}"`.

`group_rows_by_block` (dedup/signatures.py:102-108):
1. For each row in input order, resolve its block id (supplied first, derived fallback) and append the row to that block's list, preserving first-seen block order (`OrderedDict`).

`cluster_hash` (dedup/cluster_key.py:16-23):
1. Sort member row_ids, join with `";"`, sha256, keep first 12 hex chars, prefix `"c_"`.

#### 4 Constants
```python
CLUSTER_ID_PREFIX = "c_"
```
(dedup/cluster_key.py:13); sha1 truncation length 12 and prefix `"blk-"` (dedup/signatures.py:55-56); sha256 truncation length 12 (dedup/cluster_key.py:23).

#### 5 Complexity
O(1) per row for block-id resolution; O(k log k) per cluster for `cluster_hash` (sorting k member row_ids, dedup/cluster_key.py:22).

#### 6 Worked example
- A row with `block_id=None, country="DE", postal_code="70173", street="Hauptstr", house_no="1"` receives a derived block id starting with `"blk-"`, and the request containing it plus an explicit block `"A"` reports `summary.blocks == 2` (tests/test_dedup.py:844-861).
- The cluster id of the {r1, r2} pair is `"c_"`-prefixed, exactly 14 characters (`"c_"` + 12 hex), and identical when the same rows are submitted in reversed order (tests/test_dedup.py:248-271).

#### 7 Failure modes
- Missing address parts normalise to `""` inside the join (dedup/signatures.py:33-34, 51-54), so rows with entirely empty addresses and no `block_id` all derive the SAME block id and are adjudicated together. ⚠ NO FIXTURE COVERAGE for the all-empty-address case.

---

### Route handlers (`dedup_cluster_block`, `dedup_file` — api/routes.py)

#### 1 Purpose
`POST /api/dedup/cluster-block` is the JSON-in/JSON-out Phase-2 "Pass 2" adjudicator endpoint (api/routes.py:802-812). `POST /api/dedup/file` wraps the same algorithm for XLSX upload/download (api/routes.py:832-844).

#### 2 Inputs and outputs
- `dedup_cluster_block(request: DedupRequest) -> DedupResponse` (api/routes.py:803). `DedupRequest.rows: List[DedupRow]` with `min_length=1` (dedup/models.py:49-56). `DedupResponse = {rows: List[DedupResultRow], summary: DedupSummary}` (dedup/models.py:103-107); `DedupResultRow` fields: `row_id, block_id, cluster_id (Optional), routing ∈ {"cluster","unique","manual_review"}, llm_flag, signature_id, confidence (Optional), reasoning (Optional), model, model_version, prompt_version` (dedup/models.py:63-83).
- `dedup_file(file: UploadFile) -> StreamingResponse` returning an `.xlsx` attachment named `<stem>_dedup.xlsx` (api/routes.py:832-888).

#### 3 Pseudocode
`dedup_cluster_block` (api/routes.py:802-829):
1. `settings = get_settings()`; `llm = _get_dedup_llm(settings)` — a `MockDedupLLM` when `settings.mock_external_calls` is set, else `DedupLLM(settings)` (api/routes.py:667-677, 813-814).
2. `return await cluster_blocks(request.rows, llm, settings=settings)` (api/routes.py:819).
3. `finally`: call `llm.aclose()` when present, swallowing exceptions (api/routes.py:820-829).

`dedup_file` (api/routes.py:832-888):
1. Reject non-`.xlsx`/`.xlsm` filenames with HTTP 400 (api/routes.py:845-850); reject empty upload with HTTP 400 (api/routes.py:852-854).
2. Parse the sheet (`_parse_xlsx`) and map each row to a `DedupRow` via `_rows_to_dedup_rows`: headers are normalised to lowercase alphanumerics (`_norm_header`, api/routes.py:115-123) and looked up in `_DEDUP_HEADER_ALIASES`; unmapped columns are dropped; validation errors raise HTTP 422 listing failed rows (api/routes.py:710-737).
3. Run `cluster_blocks` identically to the JSON endpoint (api/routes.py:866-874).
4. Echo the uploaded sheet with the result columns `["Cluster ID", "Routing", "LLM Flag", "Confidence", "Reasoning"]` appended, joined on `row_id`; internal keys (Block ID, Signature ID) go to a separate "Dedup Debug" sheet so "exactly ONE cluster key is exposed" on the main sheet (api/routes.py:740-746, 749-799).

#### 4 Constants
```python
_DEDUP_HEADER_ALIASES: dict[str, str] = {
    "rowid": "row_id",
    "recordid": "row_id",
    "customer": "row_id",
    "blockid": "block_id",
    "name1": "name1",
    "name2": "name2",
    "street": "street",
    "street1": "street",
    "streetcleaned": "street",
    "houseno": "house_no",
    "housenumber": "house_no",
    "postalcode": "postal_code",
    "zip": "postal_code",
    "city": "city",
    "country": "country",
    "countryregionkey": "country",
    "rorid": "ror_id",
    "enrichedname": "enriched_name",
}
```
(api/routes.py:688-707)
```python
_DEDUP_RESULT_COLUMNS = ["Cluster ID", "Routing", "LLM Flag", "Confidence", "Reasoning"]
_DEDUP_DEBUG_SHEET = "Dedup Debug"
_DEDUP_DEBUG_COLUMNS = ["row_id", "Cluster ID", "Block ID", "Signature ID"]
```
(api/routes.py:744-746)

#### 5 Complexity
Endpoint cost is dominated by `cluster_blocks` (see block processing). XLSX join is O(rows) by `row_id` dictionary lookup (api/routes.py:769, 778-792).

#### 6 Worked example
5 identical rows `("Acme GmbH", "Chemistry")` POSTed to `/api/dedup/cluster-block` in mock mode return `distinct_signatures == 1`, `clusters == 1`, `rows_clustered == 5`, every row `routing == "cluster"` and `prompt_version == "p2-dedup-v3"` (tests/test_dedup.py:876-893). An empty `rows` list is rejected with HTTP 422 (tests/test_dedup.py:895-898).

#### 7 Failure modes
- Observation: `_DEDUP_HEADER_ALIASES` contains no alias for `lei_id` (no `"leiid"` key, api/routes.py:688-707), so an XLSX column headed "LEI" or "lei_id" is dropped by `_rows_to_dedup_rows` (api/routes.py:722-726) — LEI hints reach the adjudicator only via the JSON endpoint. ⚠ NO FIXTURE COVERAGE for LEI columns in the file route.
- Rows the adjudicator did not return "(should not happen)" get blank result cells rather than being dropped (api/routes.py:762-763, 780-783).

---

### Block processing (`_process_block` — dedup/adjudicator.py)

#### 1 Purpose
Per-block driver: collapses rows to signatures, selects Mode A or Mode B, runs the residue candidate pass, applies deterministic verdict guards, and emits one output row per input row (dedup/adjudicator.py:831-900). The request-level entry point `cluster_blocks` groups rows into blocks and processes them concurrently under a shared LLM-concurrency semaphore (dedup/adjudicator.py:933-962).

#### 2 Inputs and outputs
`_process_block(block_id: str, rows: List[DedupRow], llm: DedupLLM, threshold: int, semaphore: asyncio.Semaphore, cfg: _CandidateConfig) -> tuple[List[DedupResultRow], BlockStats]` (dedup/adjudicator.py:831-838). `_CandidateConfig` holds `name_threshold`, `token_threshold`, `max_candidates` (dedup/adjudicator.py:43-49). `BlockStats` is the telemetry accumulator (dedup/adjudicator.py:96-118).

#### 3 Pseudocode
`cluster_blocks` (dedup/adjudicator.py:933-1013):
1. `threshold = int(os.getenv("SIG_PARTITION_THRESHOLD", "12"))` when not passed; `concurrency = int(os.getenv("DEDUP_MAX_CONCURRENCY", "5"))` when not passed; `semaphore = asyncio.Semaphore(max(1, concurrency))` (dedup/adjudicator.py:948-952).
2. `cfg = _resolve_candidate_config(settings)` — each knob resolves settings attribute > env var > module default; an unparsable env value logs a warning and falls back to the default (dedup/adjudicator.py:903-926). Attribute/env pairs: `name_candidate_threshold`/`NAME_CANDIDATE_THRESHOLD` (float), `token_candidate_threshold`/`TOKEN_CANDIDATE_THRESHOLD` (float), `max_candidates_per_block`/`MAX_CANDIDATES_PER_BLOCK` (int) (dedup/adjudicator.py:916-926).
3. `blocks = group_rows_by_block(rows)`; run `_process_block` for every block concurrently via `asyncio.gather` (dedup/adjudicator.py:955-962).
4. Aggregate per-block stats into `DedupSummary` and log a `dedup_request` telemetry record (dedup/adjudicator.py:964-1011).

`_process_block` (dedup/adjudicator.py:831-900):
1. `signatures = build_signatures(rows)`; `n = len(signatures)` (dedup/adjudicator.py:841-843).
2. Mode selection (dedup/adjudicator.py:845-854):
   - `n <= 1`: mode "A", no LLM — a single entity `e1` containing the lone signature (identical rows still cluster), or an empty entity list (dedup/adjudicator.py:845-848).
   - `1 < n <= threshold` (default 12): mode "A" — `_mode_a` (dedup/adjudicator.py:849-851).
   - `n > threshold`: mode "B" — `_mode_b` (dedup/adjudicator.py:852-854).
3. Residue widening: `_adjudicate_residue(...)` — runs BEFORE the identity guard "so a bad name/token merge across conflicting ROR/LEI is still split" (dedup/adjudicator.py:856-861).
4. Guard 1: `_enforce_identity_split` — a merge across different non-empty ROR/LEI ids is split to manual_review (dedup/adjudicator.py:863-866).
5. Guard 2: if `_reasoning_disowns_membership(entities)` — a merged entity (≥2 signatures) whose reasoning contains any `_NONMERGE_MARKERS` phrase — mark EVERY signature in the block uncertain, routing the whole block to manual_review "rather than guess toward merging" (dedup/adjudicator.py:241-263, 867-877).
6. `_emit_rows(...)` and a `dedup_block` telemetry log (dedup/adjudicator.py:879-899).

Bucketing by `has_name2` happens inside Mode A (explicit buckets) and Mode B (compatibility filter); see those procedures.

#### 4 Constants
```python
DEFAULT_SIG_PARTITION_THRESHOLD = 12
DEFAULT_DEDUP_MAX_CONCURRENCY = 5
DEFAULT_NAME_CANDIDATE_THRESHOLD = 0.85
DEFAULT_TOKEN_CANDIDATE_THRESHOLD = 0.6
DEFAULT_MAX_CANDIDATES_PER_BLOCK = 50
```
(dedup/adjudicator.py:36-40)
```python
_NONMERGE_MARKERS = (
    "should not be merged",
    "should not merge",
    "must not be merged",
    "not be merged",
    "do not merge",
    "should be split",
    "must be split",
)
```
(dedup/adjudicator.py:241-249)

#### 5 Complexity
Per block with S distinct signatures: Mode A issues at most 2 partition calls (one per non-singleton `has_name2` bucket, dedup/adjudicator.py:291-314); Mode B issues at most S−1 calls (first signature seeds with no call; each later signature at most one call, dedup/adjudicator.py:416-452); the residue pass adds at most `min(|candidates|, cap)` pairwise calls where candidates ≤ U(U−1)/2 over U post-mode entity units (dedup/candidates.py:182-196, dedup/adjudicator.py:585-639). Blocks run concurrently but all LLM calls share one semaphore of size `DEDUP_MAX_CONCURRENCY` (default 5) (dedup/adjudicator.py:950-952).

#### 6 Worked example
- 100 identical rows → mode A, 0 LLM calls, 1 cluster (tests/test_dedup.py:110-127).
- 3 distinct signatures with `threshold=2` → Mode B, ≥2 LLM calls, r1+r2 clustered, r3 unique (tests/test_dedup.py:649-687).
- With `MAX_CANDIDATES_PER_BLOCK=1` and 3 Pfizer variants, the residue pass overflows the cap: `candidate_cap_exceeded_blocks == 1`, every row routes to `manual_review`, and reasoning carries the `candidate_cap_exceeded` marker (tests/test_dedup.py:530-550).
- The self-contradicting-reasoning guard: an LLM entity merging two different-ROR signatures with reasoning "…this should not be merged" ends with both rows in `manual_review`, never one confident cluster (tests/test_dedup.py:168-210).

#### 7 Failure modes
- One bad LLM call never fails a block — signatures involved are marked uncertain and processing continues (see Mode A/B/residue error paths).
- Invalid numeric env overrides are logged and replaced by defaults (dedup/adjudicator.py:910-914).

---

### Mode A partition (`_mode_a` — dedup/adjudicator.py)

#### 1 Purpose
For blocks with 2..threshold distinct signatures: one LLM "partition" call per `has_name2` bucket groups the bucket's signatures into entities (dedup/adjudicator.py:270-282).

#### 2 Inputs and outputs
`_mode_a(signatures: List[Signature], llm: DedupLLM, semaphore: asyncio.Semaphore, stats: BlockStats) -> List[Entity]` (dedup/adjudicator.py:270-275). `Entity` = dataclass with `entity_id`, `signatures`, `institution`, `department`, `confidence`, `reasoning`, `adjudicated: bool = False` (dedup/adjudicator.py:56-70).

#### 3 Pseudocode
(dedup/adjudicator.py:283-393)
1. Split signatures into two buckets: `has_name2 == True` and `False` — "so the empty-vs-populated decision is never sent to the LLM (it is deterministic)" (dedup/adjudicator.py:277-289).
2. Per non-empty bucket:
   a. If the bucket has exactly 1 signature, it becomes its own entity with NO LLM call and `adjudicated` left `False` (dedup/adjudicator.py:294-298).
   b. Otherwise build the payload — per signature: `signature_id`, original `name1`, `name2`, `ror_id` or `"none"`, `lei_id` or `"none"` (dedup/adjudicator.py:300-310) — and the Mode A user prompt (dedup/adjudicator.py:311); call `llm.adjudicate(SYSTEM_PROMPT, user_prompt)` under the semaphore with the default `max_tokens=4000` (dedup/adjudicator.py:313-314; default at dedup/llm.py:161).
   c. Parse with `parse_json_object` only when `call.error is None`; on `None` (unparseable or errored) increment `stats.errors`, log, mark EVERY signature in the bucket `uncertain`, and emit each as its own `adjudicated=True` entity — "Never fail the block" (dedup/adjudicator.py:318-334).
   d. Apply the partition: for each object in `parsed["entities"]`, keep only known, not-yet-assigned `signature_ids`; clamp `confidence` to [0,1] via `_confidence_to_float` (dedup/adjudicator.py:121-133, 339-346); stamp the group's `reasoning`/`confidence` onto every member's `merge_reasoning`/`merge_confidence` (dedup/adjudicator.py:347-352); create an `adjudicated=True` entity with the returned `institution`/`department` (dedup/adjudicator.py:354-363).
   e. Each id in `parsed["uncertain_signature_ids"]` not already assigned → `uncertain=True`, own entity (dedup/adjudicator.py:365-374).
   f. Any signature the LLM dropped from its partition → `uncertain=True`, own entity, "so it surfaces for review rather than vanishing" (dedup/adjudicator.py:377-386).
3. Safety net: `_enforce_name2_split(entities, next_index)` (redundant given the bucketing, but honours the spec's "enforce after the LLM returns") (dedup/adjudicator.py:390-393).

Retry behaviour lives entirely in `llm.adjudicate` (bounded exponential backoff, see the LLM procedure); `_mode_a` itself does not retry a parse failure — the fallback is uncertain-per-signature (dedup/adjudicator.py:318-334).

Prompt (verbatim, built by `build_mode_a_user_prompt`, dedup/prompts.py:42-58):
```python
def build_mode_a_user_prompt(signatures: List[dict]) -> str:
    listing = json.dumps({"signatures": signatures}, ensure_ascii=False, indent=2)
    return (
        "Group the following signatures into entities. "
        "Return STRICT JSON only, no other text:\n"
        '{"entities":[{"signature_ids":["s1","s3"],"institution":"<short label>",'
        '"department":"<short label or empty>","confidence":<0-1>,'
        '"reasoning":"<1-2 sentences>"}],"uncertain_signature_ids":["s7"]}\n'
        "Every input signature_id must appear exactly once, across either "
        "entities[].signature_ids or uncertain_signature_ids.\n\n"
        f"Signatures:\n{listing}"
    )
```
(dedup/prompts.py:42-58)

Evidence in context: the JSON listing of the bucket's signatures — `signature_id`, original `name1`/`name2`, `ror_id`, `lei_id` (dedup/adjudicator.py:302-310). Required return: strict JSON `{"entities":[{signature_ids, institution, department, confidence, reasoning}], "uncertain_signature_ids":[...]}` with every input id appearing exactly once (dedup/prompts.py:52-56). Parsing: `parse_json_object` (dedup/llm.py:75-101). Parse failure: all bucket signatures → uncertain/manual_review (dedup/adjudicator.py:322-334).

Evidence constraint: the system prompt scopes the task — "Your only job is to decide, from the names, which records refer to the SAME real-world customer entity." (dedup/prompts.py:22) — but it also explicitly invites world knowledge: "Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms …, historical renames or restructures, and spelling variants/typos." (dedup/prompts.py:36). There is NO clause restricting the model to only the supplied evidence; ⚠ UNVERIFIED — no prompt text forbids use of external knowledge (verified absent in dedup/prompts.py:19-58).

#### 4 Constants
Bucket threshold for entering Mode A: `n <= threshold` with `DEFAULT_SIG_PARTITION_THRESHOLD = 12` (dedup/adjudicator.py:36, 849-851). Default `max_tokens = 4000` (dedup/llm.py:161).

#### 5 Complexity
≤ 2 LLM calls per block (one per non-singleton bucket); each call carries ≤ threshold (12) signatures (dedup/adjudicator.py:286-314, 849-851). All other work is O(S).

#### 6 Worked example
`("TU München", "Dept of Mechanical Eng")` and `("TU München", "Department of Mechanical Engineering")`: the scripted LLM returns one entity `{"signature_ids": ["s1","s2"], ..., "confidence": 0.95, "reasoning": "abbreviation"}`; result: 1 LLM call, 1 cluster, both rows `routing == "cluster"`, `llm_flag is True`, `confidence == 0.95` (tests/test_dedup.py:278-306). Uncertain path: `uncertain_signature_ids: ["s1"]` for a two-row identical signature → both rows still share a cluster id but route to `manual_review` (tests/test_dedup.py:612-642). Malformed response ("this is not json") → `errors == 1`, all rows `manual_review` (tests/test_dedup.py:724-736).

#### 7 Failure modes
- Unparseable/errored response → per-signature uncertain entities (dedup/adjudicator.py:318-334).
- Unknown or duplicate `signature_ids` in the response are silently filtered (`sid in by_id`, `sid not in assigned`) (dedup/adjudicator.py:339-344).
- Omitted signatures become uncertain (dedup/adjudicator.py:379-386).

---

### Mode B assignment (`_mode_b` — dedup/adjudicator.py)

#### 1 Purpose
For blocks with more distinct signatures than the partition threshold: incremental canonical assignment, one signature at a time, keeping "calls O(signatures) with each prompt bounded" (dedup/adjudicator.py:400-412).

#### 2 Inputs and outputs
`_mode_b(signatures: List[Signature], llm: DedupLLM, semaphore: asyncio.Semaphore, stats: BlockStats) -> List[Entity]` (dedup/adjudicator.py:400-405).

#### 3 Pseudocode
(dedup/adjudicator.py:413-527)
1. For each signature in order:
   a. If no canonicals exist yet → seed a new entity, no LLM call, `adjudicated` stays `False` (dedup/adjudicator.py:417-420).
   b. `compatible = [e for e in canonicals if e.has_name2 == sig.has_name2]`; if empty → new entity deterministically, no LLM call ("never compared across the Name 2 boundary") (dedup/adjudicator.py:422-427).
   c. Build the candidate dict (`signature_id`, `name1`, `name2`, `ror_id` or `"none"`, `lei_id` or `"none"`) and one canonical dict per compatible entity: `entity_id`, `institution` (fallback: first signature's `name1`), `department` (fallback: first signature's `name2`), representative `name1`/`name2` from the entity's first signature, and the first non-empty `ror_id`/`lei_id` across the entity's signatures or `"none"` (dedup/adjudicator.py:430-448).
   d. `call = await llm.adjudicate(SYSTEM_PROMPT, user_prompt, max_tokens=1000)` under the semaphore (dedup/adjudicator.py:451-452).
   e. Parse failure/error → `stats.errors += 1`, signature `uncertain=True`, own entity (dedup/adjudicator.py:456-466).
   f. `decision == "match"`: if `matched_entity_id` names a compatible entity, stamp `merge_reasoning`/`merge_confidence` on the JOINING signature (so each output row carries its own membership rationale), append it, set entity-level `confidence`/`reasoning` (fallback for the seed signature), `adjudicated=True` (dedup/adjudicator.py:473-487). A match to an unknown/incompatible id → warn and treat as a new adjudicated entity (dedup/adjudicator.py:489-500).
   g. `decision == "new"` → new `adjudicated=True` entity with reasoning recorded on the signature (dedup/adjudicator.py:501-510).
   h. Anything else (including "uncertain") → `uncertain=True`, own adjudicated entity with confidence/reasoning (dedup/adjudicator.py:511-525).

Prompt (verbatim, built by `build_mode_b_user_prompt`, dedup/prompts.py:61-79):
```python
def build_mode_b_user_prompt(candidate: dict, canonicals: List[dict]) -> str:
    payload = json.dumps(
        {"candidate": candidate, "entities": canonicals},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Decide whether the candidate signature is the same entity as one of "
        "the listed entities, or a new entity. Return STRICT JSON only:\n"
        '{"decision":"match"|"new"|"uncertain","matched_entity_id":"<id or null>",'
        '"confidence":<0-1>,"reasoning":"<1-2 sentences>"}\n\n'
        f"{payload}"
    )
```
(dedup/prompts.py:61-79)

Evidence in context: one candidate signature plus every compatible canonical entity (representative names and first non-empty ROR/LEI) (dedup/adjudicator.py:430-448). Required return: strict JSON `{"decision", "matched_entity_id", "confidence", "reasoning"}` (dedup/prompts.py:75-78). Parsed by `parse_json_object`; failure path → uncertain signature (dedup/adjudicator.py:456-466). Evidence constraint: same system prompt as Mode A — scoped to names but not explicitly restricted to supplied evidence (dedup/prompts.py:22, 36; see Mode A note).

#### 4 Constants
Mode B trigger: `n > threshold` (default 12) (dedup/adjudicator.py:36, 852-854). `max_tokens=1000` per Mode B call (dedup/adjudicator.py:452).

#### 5 Complexity
At most S−1 LLM calls per block (first signature never calls; incompatible candidates skip the call) (dedup/adjudicator.py:416-427). Prompt size grows with the number of compatible canonicals accumulated so far, bounded by the entity count in the block.

#### 6 Worked example
3 signatures with `threshold=2` ("Helmholtz Zentrum/Institute A", "Helmholtz Centre/Institut A", "Helmholtz Zentrum/Institute B"): scripted LLM answers "match" for s2 (to the first canonical) and "new" otherwise → ≥2 LLM calls; r1/r2 share a cluster with `llm_flag is True`, r3 unique (tests/test_dedup.py:649-687). Name-2 boundary: an empty-Name2 candidate among populated canonicals starts a new entity without an LLM call for that decision (tests/test_dedup.py:689-708).

#### 7 Failure modes
- Errored/unparseable call → uncertain signature, block continues (dedup/adjudicator.py:456-466).
- Hallucinated `matched_entity_id` → demoted to "new" with a warning, never a blind merge (dedup/adjudicator.py:489-500).
- Order dependence: assignment is greedy in signature order; an early wrong "new" cannot be revisited by Mode B itself (only the residue pass or the guards can change the outcome) (dedup/adjudicator.py:416-527). ⚠ UNVERIFIED — no test exercises a Mode B ordering pathology.

---

### Residue candidate nomination (`generate_candidate_pairs` — dedup/candidates.py)

#### 1 Purpose
Modes A/B "already adjudicate every signature pair WITHIN a `has_name2` bucket. What they never compare are pairs the deterministic Name-2 asymmetry rule keeps apart … and a signature alone in its bucket" (dedup/candidates.py:3-7). This module deterministically NOMINATES such residue pairs for LLM adjudication when there is a same-entity signal; "Nomination is candidacy ONLY: it never merges" (dedup/candidates.py:9-12). It is pure — no LLM, no network (dedup/candidates.py:14-16).

#### 2 Inputs and outputs
`generate_candidate_pairs(units: Sequence[CandidateUnit], *, name_threshold: float, token_threshold: float) -> List[Candidate]` (dedup/candidates.py:171-176). `CandidateUnit` = `(index, name, ror_id, lei_id, has_name2, adjudicated)` (dedup/candidates.py:94-103). `Candidate` = `(a, b, rule ∈ {"id","name","token"}, score)` with `a < b` (dedup/candidates.py:106-113).

#### 3 Pseudocode
Eligibility (`_eligible`, dedup/candidates.py:159-168):
1. A pair is NOT eligible iff `x.has_name2 == y.has_name2` AND both are already adjudicated (Mode A/B compared them). Everything else — across the Name-2 boundary, or involving an un-adjudicated singleton — is residue.

Nomination (`nominate`, dedup/candidates.py:130-156), priority id > name > token:
1. Rule "id": `(x.lei_id and x.lei_id == y.lei_id) or (x.ror_id and x.ror_id == y.ror_id)` → `Candidate(rule="id", score=1.0)` (dedup/candidates.py:123-127, 144-145). Different non-empty ids never converge (equality required).
2. Rule "name": Jaro-Winkler similarity (rapidfuzz `JaroWinkler.similarity`) over the two suffix-stripped, token-normalised names; nominate iff `jw >= name_threshold` with `score=jw` (dedup/candidates.py:24, 147-150).
3. Rule "token": Jaccard over token SETS of the suffix-stripped names (word-order-insensitive); nominate iff `jac >= token_threshold` (dedup/candidates.py:82-87, 152-154).
4. Else `None` (dedup/candidates.py:156).

Suffix stripping (`strip_legal_suffix`, dedup/candidates.py:60-79): tokens are lowercased alphanumerics split on `[^0-9a-z]+` (dedup/candidates.py:40-49); trailing legal-form token runs are removed greedily, longest suffix first, repeatedly ("GmbH & Co. KG" → all three go); a non-trailing suffix word is kept; if stripping would empty the name the full normalised name is kept instead (dedup/candidates.py:53-57, 60-79). Stripping is "only for candidate-similarity computation — never from the canonical signature itself" (dedup/candidates.py:26-27).

Generation (dedup/candidates.py:171-196): all O(n²) ordered pairs, filter by `_eligible`, nominate, then sort by `sort_key = (rank{id:0, name:1, token:2}, -score, a, b)` — "id-convergence pairs are retained before name/token pairs when the cap trips" (dedup/candidates.py:116-120, 182-196).

#### 4 Constants
```python
LEGAL_SUFFIXES: Tuple[str, ...] = (
    "AG", "Aktiengesellschaft", "GmbH", "G.m.b.H.", "mbH",
    "Inc", "Inc.", "Incorporated", "Corp", "Corp.", "Corporation",
    "Ltd", "Ltd.", "Limited", "LLC", "PLC",
    "BV", "B.V.", "NV", "N.V.", "SA", "S.A.", "SE", "SAS", "SARL",
    "S.r.l.", "SpA", "S.p.A.", "Oy", "AB", "A/S",
    "KG", "KGaA", "OHG", "e.V.", "Co", "Co.", "& Co.",
)
```
(dedup/candidates.py:30-37)
Thresholds are parameters; their defaults are `DEFAULT_NAME_CANDIDATE_THRESHOLD = 0.85` and `DEFAULT_TOKEN_CANDIDATE_THRESHOLD = 0.6` (dedup/adjudicator.py:38-39), overridable via settings/env (dedup/adjudicator.py:916-922).

#### 5 Complexity
O(n²) pairs over n units per block, "cheap string ops only"; the LLM-call cap is applied by the caller against the ordered list (dedup/candidates.py:180-182).

#### 6 Worked example
(tests/test_candidates.py, thresholds `{"name_threshold": 0.85, "token_threshold": 0.6}`, line 21)
- Suffix stripping: `strip_legal_suffix("Pfizer AG") == "pfizer"`; `"AG Berlin Services"` unchanged (non-trailing); `"Roche g.m.b.h." == "roche"`; `"Muster GmbH & Co. KG" == "muster"`; `"AG" == "ag"` (fallback) (tests/test_candidates.py:33-53).
- Rule id: `("Pfizer AG", lei="L1")` vs `("Pfizer Inc.", lei="L1")` → rule "id", score 1.0, and id wins priority even with near-identical names (tests/test_candidates.py:61-72).
- Rule name: "Pfizer AG" vs "Pfizer Inc." (no ids) → rule "name", score ≥ 0.85 (tests/test_candidates.py:74-76).
- Rule token: "Cancer Research Institute" vs "Institute of Cancer Research" → rule "token", score ≥ 0.6 (tests/test_candidates.py:78-83).
- No signal: "Acme Metals" vs "Globex Systems" → not nominated; different LEIs are not a convergence (tests/test_candidates.py:85-92).
- Eligibility and ordering: same-bucket both-adjudicated pair yields no candidates; cross-boundary and unadjudicated-singleton pairs are eligible; ordering is id, then name, then token; the candidate set is invariant under input shuffles (tests/test_candidates.py:100-158).

#### 7 Failure modes
- Purely deterministic; no exceptions raised on empty names (`_normalize_tokens(None) → []`, similarity 0.0) (dedup/candidates.py:40-49, 147-148). Over-nomination is bounded by the caller's cap; under-nomination (a genuine duplicate below both thresholds and without converging ids) silently stays un-nominated, and both rows keep `reasoning=None` (tests/test_dedup.py:514-527).

---

### Residue adjudication (`_adjudicate_residue` — dedup/adjudicator.py)

#### 1 Purpose
Nominates residue pairs (id / name / token) the bucketed pass never compared, adjudicates each via a pairwise LLM call, and applies the verdicts; "Nomination never merges — the LLM decides. Every nominated pair records reasoning on BOTH sides, including rejects" (dedup/adjudicator.py:556-570).

#### 2 Inputs and outputs
`_adjudicate_residue(block_id: str, entities: List[Entity], llm: DedupLLM, semaphore: asyncio.Semaphore, stats: BlockStats, cfg: _CandidateConfig) -> List[Entity]` (dedup/adjudicator.py:556-563).

#### 3 Pseudocode
(dedup/adjudicator.py:571-714)
1. Early return: fewer than 2 entities → unchanged (dedup/adjudicator.py:572-573).
2. Convert each entity to a `CandidateUnit`: name = first signature's `name1`, first non-empty ror/lei across signatures, `has_name2`, `adjudicated` (dedup/adjudicator.py:534-542, 575).
3. `candidates = generate_candidate_pairs(units, name_threshold, token_threshold)`; record telemetry per rule (dedup/adjudicator.py:576-583).
4. Cap guard: if `len(candidates) > cfg.max_candidates` (default 50), set `candidate_cap_exceeded`, mark ALL entities adjudicated and ALL signatures `uncertain`, stamping the marker string `"candidate_cap_exceeded: {N} candidate pairs exceed the per-block cap of {cap}; block routed to manual review"` into any empty `merge_reasoning`, and return — the whole block routes to manual_review (dedup/adjudicator.py:585-601).
5. Otherwise iterate candidates in deterministic priority order over a union-find on entity indices (path-halving `find`, lowest-index root) (dedup/adjudicator.py:604-616, 621):
   a. Skip a pair already merged transitively — "don't re-ask" (dedup/adjudicator.py:624-625).
   b. Build a Mode-B-style pairwise prompt: the lower-index entity is the single "canonical", the higher-index entity's representative fields are the "candidate" (`build_mode_b_user_prompt` with a one-element canonical list) (dedup/adjudicator.py:545-553, 626-636).
   c. `call = await llm.adjudicate(SYSTEM_PROMPT, user_prompt, max_tokens=1000)`; both entities become `adjudicated=True` (dedup/adjudicator.py:637-645).
   d. Parse failure/error → `stats.errors += 1`, every signature on BOTH sides `uncertain` (manual_review) (dedup/adjudicator.py:647-654).
   e. `decision == "match"` → stamp `"adjudicated vs {canon_name}: merged ({reasoning})"` and the confidence on the candidate entity's signatures; `union(a, b)` (dedup/adjudicator.py:662-668).
   f. `decision in ("new", "distinct")` → record `"adjudicated vs …: distinct ({reasoning})"` notes for BOTH indices; `stats.rejected_with_reasoning += 1`; no merge (dedup/adjudicator.py:669-673).
   g. Anything else → both sides' signatures `uncertain`, reasoning recorded where empty (dedup/adjudicator.py:674-681).
6. Rebuild entities from union-find groups in sorted-root order: singletons keep their entity and receive their distinct-note as reasoning where empty; merged groups form a new entity under the root's `entity_id` with concatenated signatures, first non-None confidence and first non-empty reasoning among members, `adjudicated=True` (dedup/adjudicator.py:684-714).

Prompt: identical template to Mode B (`build_mode_b_user_prompt`, quoted verbatim in the Mode B procedure, dedup/prompts.py:61-79), with exactly one entity in the `entities` list (dedup/adjudicator.py:627-636). Evidence, return contract, parsing and failure path as in Mode B; the parse-failure path here routes BOTH pair members to manual_review (dedup/adjudicator.py:647-654).

#### 4 Constants
`DEFAULT_MAX_CANDIDATES_PER_BLOCK = 50` (dedup/adjudicator.py:40); env override `MAX_CANDIDATES_PER_BLOCK` (dedup/adjudicator.py:923-925); `max_tokens=1000` (dedup/adjudicator.py:638). Cap-marker string template at dedup/adjudicator.py:591-594.

#### 5 Complexity
≤ `min(|candidates|, cap)` LLM calls per block after transitive skipping; union-find operations are near-O(1) amortised; candidate generation O(U²) over U entities (dedup/candidates.py:180-182; dedup/adjudicator.py:621-681).

#### 6 Worked example
- Converging-LEI pair across the Name-2 boundary, LLM "match": `("Pfizer Inc.", "Oncology", LEI-PFE)` and `("Pfizer AG", None, LEI-PFE)` → `candidates_generated == 1`, both rows share one cluster, `routing == "cluster"`, the AG row's reasoning contains "merged" (tests/test_dedup.py:438-456).
- Same pair, LLM "new" with reasoning "HQ vs division": both rows stay unique WITH "distinct" reasoning; `rejected_with_reasoning == 1` (tests/test_dedup.py:458-476).
- A lone "Pfizer AG" signature joins a 3-row "Pfizer Inc./Oncology" signature across the boundary on "match" — all four rows in one cluster (tests/test_dedup.py:479-496).
- Empty-vs-populated Name-2 with a shared name ("Siemens AG" / "Siemens AG, Healthineers Division") is nominated by name similarity and adjudicated (1 LLM call); a "new" verdict leaves both unique with recorded reasoning (tests/test_dedup.py:351-381).
- Determinism: shuffled input gives identical row→cluster maps and identical LLM call counts (tests/test_dedup.py:553-575).

#### 7 Failure modes
- Cap overflow → whole block manual_review, deterministic marker (dedup/adjudicator.py:585-601; tests/test_dedup.py:530-550).
- Ambiguous/unusable pairwise verdict → both sides manual_review (dedup/adjudicator.py:647-654, 674-681).
- Verdicts are applied via union-find, so chained "match" verdicts can merge transitively without further calls (dedup/adjudicator.py:624-625); a wrong early match therefore propagates — mitigated downstream by `_enforce_identity_split`, which runs after this pass (dedup/adjudicator.py:856-866).

---

### Post-enforcement (`_enforce_identity_split`, `_enforce_name2_split`, `_emit_rows` — dedup/adjudicator.py)

#### 1 Purpose
Deterministic verdict guards applied uniformly after adjudication, plus the fan-out from entities back to one output row per input row (dedup/adjudicator.py:863-881, 721-733).

#### 2 Inputs and outputs
- `_enforce_name2_split(entities: List[Entity], next_index: int) -> tuple[List[Entity], int]` (dedup/adjudicator.py:136).
- `_enforce_identity_split(entities: List[Entity], next_index: int) -> tuple[List[Entity], int, bool]` (dedup/adjudicator.py:185-187).
- `_emit_rows(block_id: str, entities: List[Entity], model: str, model_version: str, stats: BlockStats) -> List[DedupResultRow]` (dedup/adjudicator.py:721-727).

#### 3 Pseudocode
`_enforce_name2_split` (dedup/adjudicator.py:136-168):
1. For each entity, partition its signatures into populated-Name2 and empty-Name2.
2. If BOTH are non-empty (an LLM merge violated the asymmetry rule): log a warning, keep the populated signatures in the original entity, and move the empty-Name2 signatures into a NEW entity `e{next_index}` with `department=""` and reasoning `"Split from a mixed-Name2 group (deterministic rule)."` (dedup/adjudicator.py:149-166).
3. Otherwise the entity passes through unchanged.

`_enforce_identity_split` (dedup/adjudicator.py:185-234):
1. For each entity, collect the distinct non-empty `ror_id`s and distinct non-empty `lei_id`s across its signatures (dedup/adjudicator.py:181-182, 201-202).
2. Skip (pass through) if the entity has < 2 signatures OR fewer than 2 distinct values in both id families (dedup/adjudicator.py:203-205).
3. Otherwise the guard fires: "a different non-empty hard identifier means a different institution / legal entity — a strong split signal (ROR/LEI is only ever a split signal here, never a merge trigger)" (dedup/adjudicator.py:191-194). Split the entity into SINGLETON entities — the first keeps the original `entity_id`, the rest get fresh `e{next_index}` ids — mark every signature `uncertain=True` and stamp `merge_reasoning = "Split: different non-empty {ROR|LEI} ids ({ids}) indicate different entities; routed to manual review."`; "we never guess a safe regrouping (the safe outcome is manual_review)" (dedup/adjudicator.py:195-233).

`_reasoning_disowns_membership` (applied at the block seam, documented here for completeness): a merged entity (≥2 signatures) whose reasoning contains any `_NONMERGE_MARKERS` phrase (case-folded substring match) marks the WHOLE block manual_review (dedup/adjudicator.py:241-263, 867-877).

`_emit_rows` (dedup/adjudicator.py:721-796):
1. Per entity: `clustered = len(entity.row_ids) >= 2`; if clustered, `cluster_id = cluster_hash(row_ids)`, else `None` (dedup/adjudicator.py:737-743). Note `row_ids` is the order-preserving union across the entity's signatures (dedup/adjudicator.py:77-87), so identical-row collapses cluster even without any LLM merge.
2. Per signature, per member row — routing precedence: `sig.uncertain` → `"manual_review"`; else `cluster_id is not None` → `"cluster"`; else `"unique"` (dedup/adjudicator.py:745-755).
3. Reasoning is an ADJUDICATION signal: emitted iff `ent.adjudicated or sig.uncertain`, preferring `sig.merge_reasoning` over `ent.reasoning`; "An empty Reasoning therefore means exactly 'never nominated'" (dedup/adjudicator.py:757-769).
4. Confidence is a MERGE signal: emitted iff `ent.llm_merged or sig.uncertain` (where `llm_merged` ⇔ ≥ 2 signatures, dedup/adjudicator.py:89-93), preferring `sig.merge_confidence`; "never for a pure identical-collapse or a distinct verdict, where a spurious confidence would wrongly trip the election confidence gate" (dedup/adjudicator.py:770-781).
5. Each output row carries `llm_flag = ent.llm_merged`, `signature_id`, `model`, `model_version`, `prompt_version=PROMPT_VERSION` (dedup/adjudicator.py:783-795).

#### 4 Constants
`_NONMERGE_MARKERS` (quoted in the block-processing procedure, dedup/adjudicator.py:241-249). Split-reasoning literals at dedup/adjudicator.py:162 and 213-216.

#### 5 Complexity
All three are O(S) over signatures / O(R) over rows per block; no LLM calls.

#### 6 Worked example
- Identity split (ROR): the LLM merges "Max Planck Institute" (ror:AAA) and "Max-Planck-Institut" (ror:BBB) into one 0.96-confidence entity whose reasoning itself argues "this should not be merged"; the guard splits them, both rows route to `manual_review`, never a shared real cluster, and no clustered row carries reasoning containing "should not be merged" (tests/test_dedup.py:168-210). LEI companion: two different non-empty LEIs split identically (tests/test_dedup.py:213-241).
- Name-2 split: a hand-built mixed entity with signatures `("s1","siemens ag","")` and `("s2","siemens ag","healthineers")` splits into two entities `{s1}` and `{s2}` (tests/test_dedup.py:384-402).
- Emission: a singleton row gets `cluster_id=None`, `routing="unique"`, `llm_flag=False`, `confidence=None` (tests/test_dedup.py:593-605); an uncertain identical-pair still shares a cluster id but routes to manual_review (tests/test_dedup.py:612-642).

#### 7 Failure modes
- `_reasoning_disowns_membership` is a coarse phrase match; it can only demote toward manual_review, never merge, "so a coarse phrase match is the safe direction" (dedup/adjudicator.py:237-240). A false positive over-routes a block to review; a phrasing outside the marker list is missed (⚠ UNVERIFIED — no fixture exercises a missed phrasing).
- `_enforce_identity_split` splits to SINGLETONS even when a safe sub-grouping exists, by design (dedup/adjudicator.py:195-197).

---

### LLM adjudicate call (`DedupLLM.adjudicate` — dedup/llm.py)

#### 1 Purpose
Single wrapper for every Phase-2 adjudication call. Reuses the Phase-1 AI Foundry client construction (`get_openai_client`); differences are "a separate deployment (`AOAI_DEPLOYMENT_DEDUP`), `reasoning_effort` instead of temperature (reasoning models may ignore temperature), bounded retries on 429/5xx, and per-call token/latency capture" (dedup/llm.py:1-9).

#### 2 Inputs and outputs
`adjudicate(system_prompt: str, user_prompt: str, *, max_tokens: int = 4000) -> DedupLLMResult` (dedup/llm.py:156-162). `DedupLLMResult` = `{raw: str, prompt_tokens: int, completion_tokens: int, latency_ms: int, model_version: str, error: Optional[str]}` (dedup/llm.py:63-72). "Never raises: on exhausted retries it returns a result with `error` set" (dedup/llm.py:165-168).

#### 3 Pseudocode
Construction (dedup/llm.py:114-136):
1. Deployment: `os.getenv("AOAI_DEPLOYMENT_DEDUP") or os.getenv("AZURE_OPENAI_DEPLOYMENT") or "gpt-5.4"` (dedup/llm.py:117-121).
2. `self._reasoning_effort = os.getenv("DEDUP_REASONING_EFFORT", "low")` (dedup/llm.py:122).
3. `self._max_retries = int(os.getenv("DEDUP_MAX_RETRIES", "3"))` (dedup/llm.py:123).
4. API version: `AOAI_API_VERSION_DEDUP` or `AZURE_OPENAI_API_VERSION` or `DEFAULT_API_VERSION = "2025-04-01-preview"` (dedup/llm.py:112, 124-128).

Call loop (dedup/llm.py:169-220):
1. For `attempt in range(self._max_retries)`: build request params —
   ```python
   params = {
       "model": self._deployment,
       "messages": [
           {"role": "system", "content": system_prompt},
           {"role": "user", "content": user_prompt},
       ],
       "max_completion_tokens": max_tokens,
       "response_format": {"type": "json_object"},
   }
   if self._use_reasoning_effort:
       params["reasoning_effort"] = self._reasoning_effort
   ```
   (dedup/llm.py:174-184). No `temperature`, `top_p`, or `seed` parameter is sent (verified absent from the params dict, dedup/llm.py:174-184).
2. On success: return `raw = response.choices[0].message.content or ""` with usage tokens, latency, and `model_version = response.model` (dedup/llm.py:186-196).
3. On exception:
   a. If the deployment rejects `reasoning_effort` (`_is_unsupported_reasoning_effort`: message mentions the parameter AND looks like a bad-argument/400, or a `TypeError` mentioning it, dedup/llm.py:33-46) → permanently disable the parameter for this client and retry immediately without consuming a backoff (dedup/llm.py:198-207).
   b. If retryable (`_is_retryable`: openai `APIConnectionError`/`APITimeoutError`, HTTP 429, or 5xx, dedup/llm.py:49-60) and attempts remain → sleep `0.5 * (2 ** attempt)` seconds and retry (dedup/llm.py:209-216).
   c. Otherwise break (dedup/llm.py:217-218).
4. After the loop: return `DedupLLMResult(error=last_error or "LLM call failed")` (dedup/llm.py:220).

Response parsing (`parse_json_object`, dedup/llm.py:75-101): strip; extract a ```` ```json ```` fence if present (regex `_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)`, llm/openai_client.py:72); `json.loads`; on failure retry on the outermost `{...}` span; return the dict, or `None` if not a JSON object — "callers treat that as 'uncertain' rather than failing the block" (dedup/llm.py:79-81).

System prompt (verbatim, shared by every adjudication call; dedup/prompts.py:19-39):
```python
SYSTEM_PROMPT = """\
You are an entity-resolution adjudicator for SAP customer master data at Bruker, a scientific-instruments company. Customers are research institutions, universities, hospitals, companies, and their internal departments.

Every record you receive already shares the same physical address (country, postal code, street). Address matching is done. Your only job is to decide, from the names, which records refer to the SAME real-world customer entity.

Identity has TWO levels:
- Name 1 = the institution or company (e.g. "University of Stuttgart", "Siemens AG").
- Name 2 = a department, faculty, institute, or sub-unit within it (may be empty).
An entity is a specific (institution, department) pair.

Rules:
- Same institution AND same department, or both Name 2 empty → SAME entity.
- Same institution but DIFFERENT departments → DIFFERENT entities. Never merge them. Example: "Uni Stuttgart, Dept of Chemistry" and "Uni Stuttgart, Dept of Mechanical Engineering" are two distinct entities.
- Different institutions that happen to share one address (shared campus or building) → DIFFERENT entities.
- A shared ROR ID means same INSTITUTION only. It does not mean same department and never by itself makes two records the same entity — you must still compare Name 2.
- A shared LEI (Legal Entity Identifier) means the records are the same legal entity (typically a company). Treat it like ROR: a strong same-INSTITUTION signal, but it still does not by itself merge records with DIFFERENT Name 2 departments, and you must still compare Name 2. Conversely, DIFFERENT non-empty LEIs are a strong signal of different entities.

Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms ("Dept" = "Department", "Mech Eng" = "Mechanical Engineering"), word reordering, legal-form suffixes (GmbH, AG, Inc., Ltd, e.V.), historical renames or restructures, and spelling variants/typos.

If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome.\
"""
```
(dedup/prompts.py:19-39)

Evidence-constraint statement: the prompt scopes the decision to the supplied names ("Your only job is to decide, from the names…", dedup/prompts.py:22) and to the supplied ROR/LEI hints (dedup/prompts.py:33-34), and mandates "uncertain" over guessing (dedup/prompts.py:38); it does NOT contain an explicit "use only the evidence provided" clause and actively directs the model to use linguistic/world knowledge (translations, historical renames — dedup/prompts.py:36). ⚠ UNVERIFIED — the model is not textually constrained to supplied evidence.

#### 4 Constants
```python
DEFAULT_API_VERSION = "2025-04-01-preview"
```
(dedup/llm.py:112); deployment fallback literal `"gpt-5.4"` (dedup/llm.py:120); `DEDUP_REASONING_EFFORT` default `"low"` (dedup/llm.py:122); `DEDUP_MAX_RETRIES` default `"3"` (dedup/llm.py:123); `max_tokens` default `4000` (dedup/llm.py:161), overridden to `1000` for Mode B and residue calls (dedup/adjudicator.py:452, 638); backoff `0.5 * (2 ** attempt)` seconds (dedup/llm.py:210); `response_format={"type": "json_object"}` (dedup/llm.py:181).
```python
PROMPT_VERSION = "p2-dedup-v3"
```
(dedup/prompts.py:14) — "Bumped whenever the prompt wording changes in a way that could shift decisions. Logged per LLM call and emitted in every result row" (dedup/prompts.py:12-14, dedup/adjudicator.py:794, 822).

#### 5 Complexity
≤ `DEDUP_MAX_RETRIES` (default 3) HTTP attempts per adjudication, plus at most one extra immediate retry when `reasoning_effort` is rejected (that branch `continue`s without a backoff but still consumes loop attempts after the first, dedup/llm.py:172, 198-207). Concurrent calls across all blocks are bounded by the shared semaphore (default 5, dedup/adjudicator.py:37, 950-952).

#### 6 Worked example
- `parse_json_object` accepts `'{"a": 1}'`, a fenced ```` ```json ```` block, and prose-wrapped JSON; returns `None` for non-JSON, empty string, and a JSON array (tests/test_dedup.py:715-721).
- `reasoning_effort` fallback: a fake client that raises `"Unrecognized request argument supplied: reasoning_effort"` on the first attempt causes the parameter to be dropped; the second attempt succeeds with `raw == '{"decision": "new"}'`, `error is None`, and `_use_reasoning_effort` left `False`; the first recorded call contains `reasoning_effort`, the second does not (tests/test_dedup.py:789-837).
- Exhausted-retry path: an LLM stub returning `DedupLLMResult(error="429 rate limited")` yields `summary.errors == 1` and all rows `manual_review` (tests/test_dedup.py:739-759).
- Mock offline client: `MockDedupLLM` never merges — Mode A returns each signature as its own entity, Mode B/residue always answers `{"decision": "new", ..., "confidence": 0.5, "reasoning": "Mock: conservative no-merge default."}` (tests/mocks/dedup_mock.py:23-66).

#### 7 Failure modes
- Non-retryable exceptions (e.g. 400s unrelated to `reasoning_effort`, auth failures) end the loop on first occurrence with `error` set (dedup/llm.py:49-60, 209-218).
- An empty completion (`content` None) returns `raw=""`, which `parse_json_object` maps to `None` → callers mark signatures uncertain (dedup/llm.py:75-84, 190).
- ⚠ UNVERIFIED — whether the configured Azure deployment honours `reasoning_effort="low"` or silently ignores it depends on the deployed model, not on this code.

---

### Non-determinism notes

- Sampling parameters: the adjudication request sends NO `temperature` and NO `seed` — the parameter set is exactly `model`, `messages`, `max_completion_tokens`, `response_format={"type":"json_object"}`, plus `reasoning_effort` (default `"low"`, env `DEDUP_REASONING_EFFORT`) when supported (dedup/llm.py:174-184, 122). The module docstring states this is deliberate: "`reasoning_effort` instead of temperature (reasoning models may ignore temperature)" (dedup/llm.py:6-7). Model outputs are therefore NOT pinned; the deployment's default sampling applies. ⚠ UNVERIFIED — the effective server-side temperature of the deployed model is not controlled by this codebase.
- Caching: LLM outputs are NOT cached anywhere in the dedup path. `DedupLLM` lazily caches only the HTTP client object, not responses (dedup/llm.py:104-106, 142-145), and the route closes the client after every request (api/routes.py:820-829). Every rerun re-issues every adjudication call.
- Deterministic components: Step A collapsing and signature numbering (dedup/signatures.py:111-147), block-id derivation (dedup/signatures.py:45-56), candidate nomination and its ordering ("the same units in any order yield the same candidate list, so the LLM call sequence is stable", dedup/candidates.py:14-16, 177-196), union-find root selection (dedup/adjudicator.py:604-616), all guards, and the cluster id (`cluster_hash`: "Same membership -> same id across runs, machines, and input orderings", dedup/cluster_key.py:17-23). Determinism of the non-LLM layers is fixture-verified under input shuffles (tests/test_dedup.py:553-575, tests/test_candidates.py:131-158).
- What makes reruns differ: (1) LLM verdict variability at unpinned sampling — any changed match/new/uncertain verdict changes cluster membership, and hence the content-hash `cluster_id`; (2) transient failures — a 429/5xx that exhausts the 3 retries flags the affected signatures uncertain (manual_review) on that run only (dedup/llm.py:209-220; dedup/adjudicator.py:318-334, 456-466, 647-654); (3) Mode B's greedy order-dependence interacts with any verdict change on an earlier signature (dedup/adjudicator.py:416-527); (4) configuration drift via env overrides (`SIG_PARTITION_THRESHOLD`, `DEDUP_MAX_CONCURRENCY`, `NAME_CANDIDATE_THRESHOLD`, `TOKEN_CANDIDATE_THRESHOLD`, `MAX_CANDIDATES_PER_BLOCK`, `DEDUP_REASONING_EFFORT`, `AOAI_DEPLOYMENT_DEDUP`) changes mode selection, nomination volume, and model behaviour (dedup/adjudicator.py:948-951, 916-926; dedup/llm.py:117-123). Concurrency (semaphore, `asyncio.gather`) affects call TIMING but not decision inputs, since blocks are independent and per-block call order is sequential within each mode/residue loop (dedup/adjudicator.py:955-962, 416-527, 621-681).
- Provenance stamps on every output row: `model`, `model_version` (the API-reported model of the last call, else the deployment name), and `prompt_version = "p2-dedup-v3"` (dedup/adjudicator.py:783-795, 879-881; dedup/prompts.py:14), so reruns under different models/prompts are distinguishable in the output.


# Part J — Golden-record scoring and leading-code election

Scope: deterministic per-row scoring, golden-record election, issue detection, approval, summary, and the XLSX round-trip, as implemented in `dedup/scoring.py` (1244 lines), `dedup/weights.json`, `dedup/scoring_xlsx.py`, and the route handlers in `api/routes.py`. The module is explicitly LLM-free and network-free (`dedup/scoring.py:1-17`, docstring: "No LLM, no network — ever"). All request/response models for this stage are defined inside `dedup/scoring.py` itself (`ScoringRow` at `dedup/scoring.py:90`, `ScoringResultRow` at `dedup/scoring.py:257`); `dedup/models.py` / `api/models.py` are not consumed by scoring or election.

---

### Weight loading, versioning and coercion (`load_weights` / `weights_version` / `coerce_weights` — dedup/scoring.py)

#### 1 Purpose

Provides the editable points table used by all scoring, a 12-hex fingerprint of that table for drift detection, and an all-or-nothing validator for caller-supplied weight overrides shared by the JSON endpoint body and the XLSX "Weights" sheet (`dedup/scoring.py:610-660`).

#### 2 Inputs and outputs

- `load_weights(path: str | Path | None = None) -> dict` — reads `dedup/weights.json` (default path constant `WEIGHTS_PATH = Path(__file__).parent / "weights.json"`, `dedup/scoring.py:43`) and returns `{criterion: {band label: points}}`, dropping every top-level key starting with `_` (metadata such as `_comment`) (`dedup/scoring.py:618-623`).
- `weights_version(weights: dict) -> str` — first 12 hex characters of sha256 over the canonical JSON serialisation (`sort_keys=True, separators=(",", ":")`) (`dedup/scoring.py:610-615`).
- `coerce_weights(candidate: dict, expected: dict, *, source: str = "Weights") -> Tuple[Optional[dict], Optional[str]]` — `(weights, None)` on acceptance, `(None, reason_string)` on wholesale rejection (`dedup/scoring.py:626-660`).

#### 3 Pseudocode

`coerce_weights` (`dedup/scoring.py:639-660`):

1. `parsed := {}`.
2. For each `(criterion, bands)` in `expected` (i.e. the structure of `dedup/weights.json`):
   1. `crit_in := candidate.get(criterion)`; use it as a mapping only if it is a `dict`, otherwise treat as `{}` (`dedup/scoring.py:641-642`).
   2. For each `band` in `bands`:
      1. **Guard (early return):** if `band not in crit_map` → return `(None, "<source> ignored: missing (criterion, band) pair (criterion, band); using dedup/weights.json")` (`dedup/scoring.py:644-649`).
      2. `points := crit_map[band]`. If `points` is a `bool`, or not an `int`/`float`: attempt `float(str(points).strip())`; on `TypeError`/`ValueError` → return `(None, "<source> ignored: non-numeric Points for (criterion, band); using dedup/weights.json")` (`dedup/scoring.py:650-658`).
      3. `parsed[criterion][band] := int(points)` — float points are truncated to `int` (`dedup/scoring.py:659`).
3. Return `(parsed, None)` (`dedup/scoring.py:660`).

Override semantics at the call sites:

- JSON endpoint `POST /api/dedup/score`: if `request.weights` is not None, `coerce_weights(request.weights, load_weights(), source="Weights payload")` is applied. A rejected override appends the reason to `request_warnings` (surfaced in `summary.warnings`) and scoring proceeds with `weights=None`, i.e. `elect_golden_records` reloads `dedup/weights.json` (`api/routes.py:915-926`; fallback reload at `dedup/scoring.py:1054-1055`).
- XLSX endpoint: a worksheet whose title (stripped, casefolded) equals `"weights"` is parsed row-by-row from row 2 as `(Criterion, Band, Points)` columns into a nested dict and passed through the same `coerce_weights` with `source="Weights sheet"` (`dedup/scoring_xlsx.py:89-107, 199-213`). Acceptance replaces the table wholesale; rejection appends the reason to the summary warnings and falls back to `dedup/weights.json` (`dedup/scoring_xlsx.py:207-213`).

Key properties, verified in the body: validation is keyed on `expected`'s structure, so **extra** criteria or bands in the candidate are silently discarded (only expected pairs are copied into `parsed`, `dedup/scoring.py:640-659`); **any** missing pair or non-numeric points cell rejects the whole candidate ("a half-applied retune is worse than none", docstring `dedup/scoring.py:629-637`).

#### 4 Constants

`dedup/weights.json` in full (verbatim; the `_comment` key is stripped by `load_weights`, `dedup/scoring.py:623`):

```json
{
  "_comment": "Golden-record scoring weights. Editable reference table — the scorer never hardcodes points. Band labels: 'a-b' inclusive range, '>n' strictly greater, 'n+' greater-or-equal, bare number exact, 'X/Y' either literal (case-insensitive). Values with no matching band score 0. UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count tiers, account_group DRIT (transcript said DRID; live SAP shows DRIT).",
  "sales_order_last_used": {
    "2026": 20,
    "2025": 15,
    "2024": 10,
    "2023": 5
  },
  "sales_order_count": {
    "0-5": 5,
    "6-10": 15,
    ">10": 25
  },
  "sales_order_partner_last_used": {
    "2026": 20,
    "2025": 15,
    "2024": 10,
    "2023": 5
  },
  "sales_order_partner_count": {
    "0-5": 5,
    "6-10": 15,
    ">10": 25
  },
  "equipment_count": {
    "0-3": 5,
    "4-8": 12,
    "9-15": 20,
    ">15": 30
  },
  "sleeping_customer": {
    "No": 15,
    "3-4": 5,
    ">5": 0
  },
  "customer_status": {
    "active": 10,
    "blocked": 0
  },
  "account_group": {
    "DRIT": 20,
    "0002/SHIP2": 15,
    "0003": 10,
    "0004": 10,
    "0005/MLIEF": 5
  },
  "company_code_count": {
    "1": 5,
    "2-4": 15,
    "5+": 25
  },
  "combined_presence_bonus": {
    "company code AND sales org": 10
  },
  "salesforce_instance_count": {
    "per instance": 10
  }
}
```

(`dedup/weights.json:1-58`.) The file itself marks three items UNCONFIRMED: the `combined_presence_bonus` value, the `sales_order_partner_count` tiers, and the `DRIT` label (`dedup/weights.json:2`); the same caveats are repeated at `dedup/scoring.py:873, 912` and `tests/test_scoring.py:7-11`.

#### 5 Complexity

`coerce_weights` visits every (criterion, band) pair of the expected table exactly once: O(C·B) with C = 11 criteria and B ≤ 5 bands per criterion — constant (≤ 34 pairs) per request (`dedup/scoring.py:640-659`). `weights_version` is one sha256 over ≤ ~1 KB of JSON (`dedup/scoring.py:614-615`).

#### 6 Worked example

From `tests/test_scoring.py::TestScoreWorkbook`:

- `test_corrupted_weights_sheet_falls_back_wholesale` (`tests/test_scoring.py:925-936`): the fixture drops the single band row `"6-10"` from the Weights sheet; the whole sheet is rejected (`summary.warnings` contains `"Weights sheet ignored"`), and a row with `year=2026` still scores `score_SalesOrderLastUsed = 20` from `dedup/weights.json`.
- `test_weights_sheet_override_applies_wholesale` (`tests/test_scoring.py:938-949`): retuning exactly one cell — `("sales_order_last_used", "2026")` from 20 to 40 — while keeping all other pairs present yields `summary.warnings == []` and `score_SalesOrderLastUsed = 40`.
- `test_weights_retune_flips_winner_and_changes_version` (`tests/test_scoring.py:1164-1187`): two different weight tables produce different winners and different `scored_with_weights_version` fingerprints on every row.

#### 7 Failure modes

- Missing (criterion, band) pair or non-numeric Points → wholesale rejection with a reason string; never a partial merge, never an exception (`dedup/scoring.py:644-658`).
- Boolean Points values are explicitly treated as non-numeric dirt (the `isinstance(points, bool)` guard precedes the int/float check because `bool` is an `int` subclass; `float("True")` then raises and rejects) (`dedup/scoring.py:651-658`).
- `load_weights` propagates `FileNotFoundError`/`json.JSONDecodeError` if `dedup/weights.json` is missing or malformed — there is no guard (`dedup/scoring.py:618-623`). ⚠ NO FIXTURE COVERAGE for a missing/corrupt weights file.

---

### Per-row scoring (`score_row` — dedup/scoring.py)

#### 1 Purpose

Computes the per-criterion points breakdown and coercion warnings for one `ScoringRow` against a weights table, applying the G1 recency-dominance gate to the two sales-order count criteria (`dedup/scoring.py:813-922`). The design contract is permissiveness: a missing or unrecognised value scores 0 (with a warning when a value was present but unrecognised) and never raises (`dedup/scoring.py:9-13`).

#### 2 Inputs and outputs

Input: `row: ScoringRow`, `weights: dict`, `cluster_max_year: Optional[int] = None`, `cluster_max_partner_year: Optional[int] = None` (`dedup/scoring.py:813-818`).

`ScoringRow` scoring fields (all optional, `dedup/scoring.py:90-183`), with their file-column aliases:

| field | alias(es) | type |
|---|---|---|
| `row_id` | `Customer` (required) | `str` (`dedup/scoring.py:105-107`) |
| `cluster_id` | `Cluster ID` | `Optional[str]` (`:108-111`) |
| `confidence` | `Confidence` | `Optional[float]`, dirty values coerced to None (`:112-119, 224-234`) |
| `routing` | `Routing` | `Optional[str]` (`:120-127`) |
| `reasoning` | `Reasoning` | `Optional[str]` (`:128-134`) |
| `last_order_year` | `Sales_Order_Last_Used` | `Scalar = Union[int, float, str, None]` (`:53, 135`) |
| `orders_in_last_used_year` | `Sales_Order_Total_Count` / `orders_in_last_used_year` / legacy `order_count` | `Scalar` (`:141-147`) |
| `partner_last_order_year` | `Sales_Order_Partner_Last_Used` | `Scalar` (`:148-150`) |
| `partner_orders_in_last_used_year` | `Sales_Order_Partner_Total_Count` / legacy `partner_order_count` | `Scalar` (`:153-161`) |
| `equipment_count` | `Equipment_Total_Count` | `Scalar` (`:162`) |
| `sleeping_band` | `SleepingCustomer` | `Optional[str]` (expected "No"/"3-4"/">5") (`:163-164`) |
| `customer_status` | `CustomerStatus` | `Optional[str]` (expected "active"/"blocked") (`:165-166`) |
| `account_group` | `Account group` | `Optional[str]` (`:167`) |
| `company_code_consolidated` | `Company_Code_Consolidated` (";"-delimited) | `Optional[str]` (`:168-170`) |
| `sales_org_consolidated` | `Sales_Org_Consolidated` (";"-delimited) | `Optional[str]` (`:171-173`) |
| `sf1`..`sf8` | flat Salesforce id slots (sf1 = Biosystems, sf2 = AXS) | `Optional[str]` (`:174-183`); a legacy `salesforce_ids` list is spread across sf1..sf8 when no explicit sf* key is present (`:185-200`) |

Non-string cells are stringified rather than rejected (integer-valued floats as `str(int(v))`) (`dedup/scoring.py:202-222`).

Output: `Tuple[Dict[str, int], List[str]]` — a breakdown dict that always carries all 11 criterion keys (0 where nothing matched, "column-stable" audit trail, docstring `dedup/scoring.py:821-823`), plus coercion/suppression warnings.

#### 3 Pseudocode

Coercion helpers used inside (`dedup/scoring.py:667-718`):

1. `_coerce_int(value, field_name, warnings)`: None → None; `bool` → warning `"<field> <v> not numeric -> 0"`, None; `int`/`float` → `int(value)`; else strip string, empty → None, else `int(float(text))`, on `ValueError` → warning, None. Never raises (`dedup/scoring.py:667-688`).
2. `split_consolidated(value)`: non-empty `strip()`ed parts of `str(value).split(";")` (`dedup/scoring.py:698-702`).
3. `derived_counts(row) -> (company_code_count, sales_org_count, salesforce_instance_count)`: lengths of the two consolidated splits plus the count of sf1..sf8 slots that are non-None and non-blank after `strip()` — always derived, never read from the file (`dedup/scoring.py:705-718`).

Band matchers (`dedup/scoring.py:725-785`):

4. `_match_numeric_band(value, bands)`: None → 0. For each `(label, points)` in the band dict, in insertion order: `">n"` → match if `value > n`; `"n+"` → match if `value >= n`; `"a-b"` (a `-` present after stripping a leading sign) → match if `a <= value <= b` (inclusive; label split by the **last** `-` via `rsplit("-", 1)`); bare number → match if equal. First match returns `int(points)`; an unparseable label is logged and skipped; no match → 0 (`dedup/scoring.py:725-751`).
5. `_match_label_band(value, bands, field_name, warnings, *, warn_unknown)`: None or blank → 0 silently ("absence is not activity", `dedup/scoring.py:766-768`); otherwise casefolded comparison against each label, where `"X/Y"` means either literal; a present-but-unrecognised value returns 0 and appends `"<field> '<v>' unrecognized -> 0"` only when `warn_unknown` (`dedup/scoring.py:754-780`).
6. `_single_band_value(bands)`: the points of the first (only) band, 0 for an empty dict (`dedup/scoring.py:783-785`).
7. `_award_count(row_year, cluster_max_year)` — the G1 gate: row year None → False (a row with no year never receives count points); cluster max None → True (context-free scoring, a singleton is trivially its own maximum); otherwise award iff `row_year == cluster_max_year` (`dedup/scoring.py:792-810`).

`score_row` body — components computed in this exact order (`dedup/scoring.py:832-922`):

1. Coerce `last_year`, `order_count`, `partner_year`, `partner_count`, `equipment` with `_coerce_int`; compute `(company_codes, sales_orgs, sf_instances) = derived_counts(row)` (`dedup/scoring.py:835-847`).
2. `breakdown["sales_order_last_used"] = _match_numeric_band(last_year, weights["sales_order_last_used"])` (`:849-851`).
3. `count_pts = _match_numeric_band(order_count, weights["sales_order_count"])`. If `_award_count(last_year, cluster_max_year)` → `breakdown["sales_order_count"] = count_pts`; **else** 0, and only when the loss is genuine (`count_pts > 0` **and** `cluster_max_year is not None`) append the suppression warning `"order count suppressed (G1): last-used year {last_year} is not the cluster's most recent ({cluster_max_year})"` (`:855-869`). Context-free suppressions (lone year-None row) are deliberately not flagged (`:860-865`).
4. `breakdown["sales_order_partner_last_used"] = _match_numeric_band(partner_year, weights["sales_order_partner_last_used"])` (`:870-872`).
5. Partner count: identical G1 gate against `cluster_max_partner_year`, warning text `"partner order count suppressed (G1): partner last-used year {…} is not the cluster's most recent ({…})"` (`:875-889`).
6. `breakdown["equipment_count"] = _match_numeric_band(equipment, weights["equipment_count"])` (`:890-892`).
7. `breakdown["sleeping_customer"] = _match_label_band(clean(sleeping_band), weights["sleeping_customer"], warn_unknown=True)` (`:893-896`).
8. `breakdown["customer_status"] = _match_label_band(clean(customer_status), weights["customer_status"], warn_unknown=True)`. "blocked" scores 0 but remains eligible to win — a differentiator, not an eligibility exclusion; absent status is never defaulted to "active" (`:897-902`).
9. `breakdown["account_group"] = _match_label_band(clean(account_group), weights["account_group"], warn_unknown=False)` — unknown groups (e.g. DBRU) score 0 silently because the table has an explicit anything-else-0 semantics (`:903-908`).
10. `breakdown["company_code_count"] = _match_numeric_band(company_codes, weights["company_code_count"])` (`:909-911`).
11. `breakdown["combined_presence_bonus"] = _single_band_value(weights["combined_presence_bonus"])` iff `company_codes > 0 and sales_orgs > 0`, else 0 (`:913-917`). Sales orgs have no standalone tier (`:912`).
12. `breakdown["salesforce_instance_count"] = sf_instances * _single_band_value(weights["salesforce_instance_count"])` — linear per non-empty instance, uncapped (`:918-920`).
13. Return `(breakdown, warnings)` (`:922`).

The row total is `sum(breakdown.values())`, computed by the caller `_Scored.__init__` (`dedup/scoring.py:975`).

Recency context (`_cluster_year_maxima`, `dedup/scoring.py:982-1001`): per cluster, the maxima of the coerced `last_order_year` and `partner_last_order_year` over all members, ignoring None; `(None, None)` when no member carries the respective year. `elect_golden_records` computes these only for clusters with ≥ 2 members; singletons and unclustered rows are scored context-free with `(None, None)` (`dedup/scoring.py:1069-1082`).

#### 4 Constants

All points come from `dedup/weights.json` (reproduced in full above); `score_row` hardcodes no points (`dedup/weights.json:2`, "the scorer never hardcodes points"). Structural constants: `SF_FIELDS = ("sf1", …, "sf8")` (`dedup/scoring.py:75`); `SCORE_BREAKDOWN_COLUMNS` mapping breakdown keys to the 11 `score_*` output headers (`dedup/scoring.py:59-71`); suppression marker `_COUNT_SUPPRESSED_MARKER = "count suppressed (G1)"` (`dedup/scoring.py:419`).

#### 5 Complexity

Per row: each of the 11 criteria performs at most one linear scan over its band dict (≤ 5 bands), plus string splits over the consolidated fields — O(1) per row for fixed weights (`dedup/scoring.py:725-780, 832-922`). For a request of n rows in c clusters, `_cluster_year_maxima` is O(k) per cluster of k rows, and total scoring is O(n) (`dedup/scoring.py:1069-1082`).

#### 6 Worked example

`test_score_equals_breakdown_sum` (`tests/test_scoring.py:544-557`): a single clustered row with `last_order_year=2026, order_count=12, sleeping_band="No", customer_status="active", account_group="DRIT", company_code_consolidated="1;2;3", sales_org_consolidated="9", salesforce_ids=["a","b"]` scores, per the test's own arithmetic comment (`tests/test_scoring.py:555-557`):

- year 2026 → 20; orders 12 (> 10, year awarded context-free) → 25; partner year/count absent → 0 + 0; equipment absent → 0; sleeping "No" → 15; status "active" → 10; account group "DRIT" → 20; 3 company codes (band "2-4") → 15; codes AND orgs present → bonus 10; 2 non-empty SF ids × 10 → 20.
- Total: 20+25+0+0+0+15+10+20+15+10+20 = **135**, and `r.score == sum(r.score_breakdown.values())` holds.

G1 gate example — `test_bernd_example_2026_three_orders_beats_older_record_with_25` (`tests/test_scoring.py:1251-1261`): cluster {A: year 2019 count 25, B: year 2026 count 3}; cluster max year 2026. A's count band would give 25 but is suppressed to 0 (2019 ≠ 2026); B receives band "0-5" → 5; B is golden. Band-boundary fixtures: years (`tests/test_scoring.py:71-76`), order counts 0→5, 5→5, 6→15, 10→15, 11→25, 100→25 (`:78-87`), equipment 0→5, 3→5, 4→12, 8→12, 9→20, 15→20, 16→30 (`:109-113`), account groups incl. `DBRU→0` (`:127-134`), company codes with the 4-codes-in-the-15-band edge (`:136-146`), SF instances `["a",None,"","  ","b",None,"c",None] → 30` (`:161-165`).

#### 7 Failure modes

- Non-numeric numeric-ish cell → 0 points + warning `"orders_in_last_used_year 'lots' not numeric -> 0"`; never an exception or 422 (`dedup/scoring.py:667-688`; `tests/test_scoring.py:201-206, 700-712`).
- Unrecognised enum value → 0 + warning for `sleeping_band`/`customer_status`; silent 0 for `account_group` (`dedup/scoring.py:893-908`; `tests/test_scoring.py:180-187`).
- All-None row → total 0, no warnings, all 11 breakdown keys present (`tests/test_scoring.py:173-178`).
- A `KeyError` would occur if the weights dict lacked a criterion key entirely (`weights["sales_order_last_used"]` etc., `dedup/scoring.py:849-919`) — impossible for tables passing `coerce_weights` or loaded from the shipped file. ⚠ NO FIXTURE COVERAGE for a criterion-less weights dict passed directly to `score_row`.

---

### Golden-record election (`elect_golden_records` — dedup/scoring.py)

#### 1 Purpose

Scores every row and elects exactly one golden record per cluster, emitting the mapping table Phase 3 consumes; every election over a real cluster is a proposal awaiting human sign-off, never auto-committed (`dedup/scoring.py:1033-1052, 257-269`).

#### 2 Inputs and outputs

Input: `rows: List[ScoringRow]`, `weights: Optional[dict]` (None → `load_weights()`), keyword `confidence_threshold: Optional[float]` (None → env `CONFIDENCE_MERGE_THRESHOLD` → default `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95`, `dedup/scoring.py:48, 1004-1017, 1054-1056`).

Output: `List[ScoringResultRow]` in input order (`dedup/scoring.py:1052, 1126-1152`). Each result carries: `row_id` (alias `Customer`), `cluster_id` (alias `Cluster ID`), `score` (alias `score_final`), the three raw derived counts (aliases `Company_Code_Count`, `Sales_Org_Count`, `Salesforce_Instance_Count`), `is_golden_record`, `golden_record_id`, `proposed_golden_id`, `election_status ∈ {"proposed","manual_review","unique"}`, `approval_status ∈ {"proposed","approved","rejected", None}`, `scored_with_weights_version`, plus the excluded-from-serialisation `score_breakdown` and `warnings` and the 11 computed `score_*` columns (`dedup/scoring.py:257-381`).

Raises: `DuplicateRowIdError` listing the sorted repeated ids (`dedup/scoring.py:78-83, 1059-1063`); mapped to HTTP 400 by the route (`api/routes.py:925-931`).

#### 3 Pseudocode

(`dedup/scoring.py:1053-1152`)

1. If `weights is None`: `weights = load_weights()`. Resolve `threshold`; `wv = weights_version(weights)` (`:1054-1057`).
2. **Guard (exception):** collect row_ids with count > 1 via `Counter`, sorted; if any → raise `DuplicateRowIdError(duplicates)` (`:1059-1063`).
3. Group input rows by non-None `cluster_id` (insertion-ordered dict). For every cluster with ≥ 2 members compute `cluster_maxima[cid] = _cluster_year_maxima(members)`; smaller groups get no entry (`:1069-1077`).
4. Score every row: `scored = [_Scored(row, weights, *cluster_maxima.get(row.cluster_id, (None, None))) for row in rows]`. `_Scored` also caches the tie-break inputs: `total = sum(breakdown.values())`, coerced `last_year`, coerced `equipment`, `company_codes = derived_counts(row)[0]` (`:958-979, 1079-1082`).
5. Re-group the `_Scored` records by non-None `cluster_id` (`:1084-1087`).
6. **Partial-cluster detection:** for each cluster whose id starts with `CLUSTER_ID_PREFIX = "c_"` (`dedup/cluster_key.py:13`), recompute `cluster_hash(member row_ids)` = `"c_" + sha256(";".join(sorted(ids)))[:12]` (`dedup/cluster_key.py:16-23`); a mismatch marks the cluster partial — warn, never fail (`dedup/scoring.py:1089-1098`). Non-hash ids (e.g. "C1") never trip this.
7. **Winner selection**, per cluster with ≥ 2 members (`:1102-1124`):
   1. `numeric_ids = all(_parses_as_int(m.row.row_id) for m in members)` (`:1105`; `_parses_as_int` at `:1200-1205`).
   2. `winner = min(members, key=lambda m: _tiebreak_key(m, numeric_ids))` (`:1106`). `_tiebreak_key` returns, in order (`dedup/scoring.py:939-955`; ordering itself marked UNCONFIRMED in the docstring, `:942-946`):
      1. `-total` (highest total score first);
      2. `-(last_year if not None else -1)` (most recent coerced `last_order_year`; None ranks below any real year);
      3. `-(equipment if not None else -1)` (highest raw equipment count; None below 0… strictly, None ties with year −1 semantics — None maps to −1);
      4. `-company_codes` (highest raw company-code count);
      5. `row_key` = `int(row_id)` when every id in the cluster parses as an integer, else the raw string — **lowest** row_id wins, the final uniqueness guarantee making the winner invariant under input shuffling (`:947-954`).
   3. **manual_review demotion** — the cluster is demoted when any of, in the stated precedence order (`:1108-1124`):
      1. *inherited*: any member's normalised `routing == "manual_review"` (election never upgrades upstream uncertainty; `_norm_routing` at `:934-936`);
      2. *all blocked*: every member's normalised `customer_status == "blocked"` (`_normalized_status` at `:929-931`);
      3. *low confidence*: `_cluster_merge_confidence(members)` — the **minimum** non-None member confidence; all-None returns None and never gates (`dedup/scoring.py:1020-1030`) — is not None and `< threshold`;
      4. *zero signal*: every member's total is 0 (winner decided by tie-break only; "must not look confident", `:1120-1123`).
8. **Result assembly**, in input order (`:1126-1152`):
   1. If the row has no winner entry (unclustered, or a single-member cluster that degraded): status is `"manual_review"` when the row's own routing is manual_review, else `"unique"`; the row is its own proposed winner (`:1129-1140`).
   2. Else status = `"manual_review"` if the cluster was demoted, else `"proposed"`; build the result against the cluster winner (`:1142-1145`).
   3. If the cluster is partial, append warning `"partial_cluster: submitted rows are a subset of {cluster_id}"` (`:1146-1150`).
9. `_build_result` (`dedup/scoring.py:1155-1197`):
   - `election_status == "unique"` → `is_golden_record=True`, `golden_record_id=row_id` (self-reference), `proposed_golden_id=None`, `approval_status=None` (nothing to approve) (`:1174-1184`).
   - proposed / manual_review → `is_golden_record = (row_id == winner_id)`, `golden_record_id = row_id if winner else winner_id`, `proposed_golden_id = winner_id`, `approval_status = "proposed"` (`:1185-1197`). Note the JSON model keeps the computed golden fields even for manual_review; the spec's golden-blanking for manual_review is applied only at the file writeback (docstring `:1165-1169`; `dedup/scoring_xlsx.py:291-298`).
   - Every result is stamped with `scored_with_weights_version = wv` (`:1182, 1195`).

Special case: a **lone** row with `routing="manual_review"` and no cluster becomes `election_status="manual_review"` with itself as `proposed_golden_id` and `approval_status="proposed"` — it is never upgraded to unique (`dedup/scoring.py:1129-1140`; `tests/test_scoring.py:350-365, 382-394`).

#### 4 Constants

`DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` (`dedup/scoring.py:44-48`); env override `CONFIDENCE_MERGE_THRESHOLD`, invalid values logged and ignored (`dedup/scoring.py:1004-1017`); `CLUSTER_ID_PREFIX = "c_"` (`dedup/cluster_key.py:13`). The `None → -1` sentinel inside `_tiebreak_key` (`dedup/scoring.py:951-952`) means a missing year/equipment ranks below any non-negative real value.

#### 5 Complexity

For n rows, c clusters, k rows per cluster: duplicate check O(n); grouping O(n); year maxima O(k) per cluster; scoring O(n); partial-cluster hashing O(k log k) per hash-keyed cluster (sorting member ids, `dedup/cluster_key.py:22`); winner selection is a single `min` pass O(k) per cluster with an O(1) key; result assembly O(n). Total O(n + Σ k log k) ≈ O(n log k_max) per request; memory O(n).

#### 6 Worked example

`test_highest_score_wins` (`tests/test_scoring.py:247-258`): cluster C1 with row "1" (`last_order_year=2026, order_count=20` → 20 + 25 = 45, per the test's inline comment) and row "2" (`last_order_year=2023` → 5). Row "1" is golden and self-references; row "2" has `is_golden_record=False, golden_record_id="1"`; both are `election_status="proposed"`.

Tie-break chain fixtures, one per key component:
- equal score → more recent year wins: 2021 vs 2022, both score 0 (`tests/test_scoring.py:449-456`);
- equal score and year → higher raw equipment: counts 4 and 8 share the 12-point band (`:458-466`);
- equal totals → higher company-code count: 1 code + active = 5+10 = 15 vs 2 codes = 15 (`:468-478`);
- all equal → lowest numeric row_id: "3" beats "20" numerically (`:480-488`); lexical fallback when ids are non-numeric: "BP-10" < "BP-2" (`:490-497`); a mixed numeric/lexical cluster compares lexically, "100" < "DE-0001" (`:1151-1162`).
- Shuffle invariance across 10 seeded permutations of a 20-row payload (`:499-523`).

Demotion fixtures: all-blocked → manual_review but a winner is still elected (`:287-295`); min member confidence 0.80 < 0.95 gates the whole cluster (`:323-330`); confidence None never gates (`:332-339`); inherited manual_review demotes the whole cluster while membership and winner survive (`:367-380`); zero-signal cluster → manual_review + `empty_scoring_payload` issue (`:1141-1149`); env threshold 0.85 lets a 0.90 merge stay proposed (`:341-348`).

#### 7 Failure modes

- Duplicate `row_id` → `DuplicateRowIdError` (HTTP 400 listing the ids) — the one hard error, because scoring a doubled row would double-elect (`dedup/scoring.py:10-13, 1059-1063`; `api/routes.py:927-931`; `tests/test_scoring.py:435-440, 632-638`).
- Partial hash-keyed cluster → per-row `partial_cluster` warning, election proceeds over the visible subset (`dedup/scoring.py:1089-1098, 1146-1150`; `tests/test_scoring.py:1189-1205`).
- Empty input → empty result list (`tests/test_scoring.py:442-443`); the endpoint returns 200 with a zeroed summary (`api/routes.py:905-906`; `tests/test_scoring.py:640-647`).
- Mixed numeric/non-numeric row_ids in one cluster fall back to lexical comparison rather than raising (`dedup/scoring.py:1105, 948`; `tests/test_scoring.py:1151-1162`).

---

### Dedup-side issue detection (`detect_issues` — dedup/scoring.py)

#### 1 Purpose

Derives the "potential inconsistency" list (the reviewer feedback loop) from the scored inputs and results; deterministic and offline (`dedup/scoring.py:454-465`). The declared type registry is `ISSUE_TYPES` (`dedup/scoring.py:403-412`); `missing_building_inconsistency` is declared but never emitted here — it is reserved for the upstream Phase 1 building differentiator (`dedup/scoring.py:399-402`).

#### 2 Inputs and outputs

Input: the original `rows: List[ScoringRow]`, the `results: List[ScoringResultRow]`, keyword `confidence_threshold: Optional[float]` (same resolution chain as election, `dedup/scoring.py:466`). Output: `List[DedupIssue]` with fields `row_id`, `cluster_id`, `issue_type`, `detail` (`dedup/scoring.py:427-433`).

#### 3 Pseudocode

(`dedup/scoring.py:454-535`)

Row-level rules, scanning input rows (`:472-489`):

1. **verdict_contradiction** — `_reasoning_is_contradiction(row.reasoning)` is true when the casefolded reasoning starts with `"split:"` (the deterministic identity-split marker) or contains any of `_CONTRADICTION_MARKERS = ("should not be merged", "should not merge", "must not be merged", "not be merged", "do not merge", "should be split", "must be split")` (`dedup/scoring.py:421-424, 444-451`). Detail = the reasoning, stripped, truncated to 200 characters (`:475-479`).
2. **candidate_cap_exceeded** — the reasoning contains the adjudicator marker string `"candidate_cap_exceeded"` (`_CANDIDATE_CAP_MARKER`, `dedup/scoring.py:416`); deduplicated to one issue per capped block keyed by `cluster_id` (or `row_id` when clusterless) via a seen-set (`:480-489`).

Row-level rule over results (`:491-499`):

3. **count_suppressed_by_recency** — any result warning containing `"count suppressed (G1)"` (`_COUNT_SUPPRESSED_MARKER`, `dedup/scoring.py:419`) becomes one issue per warning, detail = the warning text.

Cluster-level rules — results grouped by non-None `cluster_id`; the issue's `row_id` is the cluster's winner, taken as `proposed_golden_id or golden_record_id` of the **first** member in result order (`:501-510`):

4. **low_confidence_merge** — over the input members' non-None confidences: if any exist and `min(confs) < threshold`, detail `"min merge confidence {min:.2f} < {threshold:.2f}"` (`:512-517`).
5. **all_blocked_cluster** — the member input list is non-empty and every member's normalised status is "blocked"; detail `"every member is blocked"` (`:518-522`).
6. **tiebreak_decided** — ≥ 2 member scores and the top score is shared by ≥ 2 members; detail `"top score {top} shared by {n} members"` (`:523-529`).
7. **empty_scoring_payload** — every member score is 0 (no ≥ 2 guard, so a degraded single-member cluster that kept its cluster_id and scored 0 also qualifies); detail `"all members scored 0 — winner decided by tie-break only"` (`:530-534`).

#### 4 Constants

`ISSUE_TYPES` tuple (`dedup/scoring.py:403-412`); `_CANDIDATE_CAP_MARKER = "candidate_cap_exceeded"` (`:416`); `_COUNT_SUPPRESSED_MARKER = "count suppressed (G1)"` (`:419`); `_CONTRADICTION_MARKERS` seven-string tuple (`:421-424`); the 200-character detail truncation (`:478, 488`).

#### 5 Complexity

Two linear passes over rows and results plus one pass per cluster: O(n + Σk) = O(n) with n = rows in the request; the seen-set makes cap-dedup O(1) per row (`dedup/scoring.py:472-535`).

#### 6 Worked example

`test_detect_issues_covers_each_type` (`tests/test_scoring.py:573-599`): cluster cA (confidences 0.90/0.90, threshold 0.95) yields `low_confidence_merge` with `cluster_id == "cA"`; cluster cB (both blocked, no scoring fields) yields `all_blocked_cluster`, `empty_scoring_payload`, and `tiebreak_decided` simultaneously; cluster cC with reasoning `"Split: different non-empty ROR ids (a, b) indicate different entities."` yields `verdict_contradiction` on row "5" with `"Split:"` in the detail. `test_candidate_cap_exceeded_issue_from_reasoning_marker` (`:601-613`) shows exactly one cap issue for a two-row capped block. `test_suppression_emits_issue` (`:1370-1378`) shows `count_suppressed_by_recency` attributed to the suppressed row "A". A clean confident cluster produces zero issues (`:615-621`).

#### 7 Failure modes

- Contradiction detection is substring/prefix matching on free text — a reasoning phrased outside the seven markers (or "split" not at the start) is not detected (`dedup/scoring.py:444-451`). ⚠ UNVERIFIED — recall of the marker list against real adjudicator phrasing is not measured anywhere in the repo.
- The cluster winner attribution uses the first member in result order; since all members of a cluster share `proposed_golden_id` this is order-insensitive except for degraded unique rows, whose `proposed_golden_id` is None and whose `golden_record_id` (self) is used instead (`dedup/scoring.py:506-509, 1174-1184`).
- Input rows absent from `by_id` are silently skipped in the member-input join (`:510`), which cannot occur when `rows` and `results` come from the same election call.

---

### Approval application (`apply_approval` — dedup/scoring.py)

#### 1 Purpose

Applies a human approve/reject decision to every row of one cluster, promoting the proposed winner into the golden fields on approval so Phase 3 can act uniformly; stateless — persistence is explicitly out of scope (`dedup/scoring.py:550-556, 574-586`; `api/routes.py:948-956`).

#### 2 Inputs and outputs

Input: `rows: List[ScoringResultRow]`, `cluster_id: str`, `decision: str` (route-validated to `Literal["approved","rejected"]`, `dedup/scoring.py:560`). Output: `(all rows with the decision applied, updated row_ids)`; never mutates the inputs (`model_copy`, `dedup/scoring.py:578-586, 596`). Route wrapper: `POST /api/dedup/approve` takes `ApprovalRequest {cluster_id, decision, approver (min_length 1), rows (min_length 1)}` (`dedup/scoring.py:550-561`) and returns `ApprovalResponse {cluster_id, decision, approver, updated_row_ids, rows}` (`dedup/scoring.py:564-571`; `api/routes.py:946-974`).

#### 3 Pseudocode

(`dedup/scoring.py:587-603`)

1. **Guard (exception):** if no row carries `cluster_id` → raise `ClusterNotFoundError(cluster_id)` (`:587-588`; class at `:542-547`); the route maps it to HTTP 404 (`api/routes.py:961-966`).
2. For each row, in order:
   1. Rows of other clusters pass through unchanged (`:593-595`).
   2. Matching rows are copied (`model_copy`); `approval_status := decision` (`:596-597`).
   3. **Approved path only:** if `decision == "approved"` and `proposed_golden_id is not None`: `is_golden_record := (row_id == proposed_golden_id)`; `golden_record_id := proposed_golden_id` — this fills the golden fields a manual_review row had left blank (`:598-600`).
   4. **Rejected path:** golden fields are left exactly as-is; only `approval_status` changes (`:584-585, 597`).
   5. Append the copy; record its `row_id` in `updated` (`:601-602`).
3. Return `(out, updated)` (`:603`).

Round-trip note: `ScoringResultRow` accepts either the internal `score_breakdown` dict or the flat serialized `score_*` columns (reassembled by `_fold_score_columns`, `dedup/scoring.py:309-323`) and either snake_case or file-header aliases (`populate_by_name`, `:275`), so a `/score` JSON output posts back into `/approve` losslessly.

#### 4 Constants

None beyond the `Literal` decision values (`dedup/scoring.py:560, 567`).

#### 5 Complexity

One linear pass: O(n) over the submitted rows, O(k) copies for the k rows of the target cluster (`dedup/scoring.py:590-603`).

#### 6 Worked example

`test_apply_approval_promotes_golden_and_rejects` (`tests/test_scoring.py:413-433`): an all-blocked cluster C1 (rows "1" with `last_order_year=2026`, "2") elects "1" but is manual_review. `apply_approval(results, "C1", "approved")` → `updated == {"1","2"}`, both rows `approval_status="approved"`, row "1" `is_golden_record=True, golden_record_id="1"`, row "2" `is_golden_record=False, golden_record_id="1"`. `apply_approval(results, "C1", "rejected")` sets only `approval_status="rejected"`. `"NOPE"` raises `ClusterNotFoundError`. HTTP-level: `tests/test_scoring.py:650-673` (approve promotes and echoes with file-header keys, e.g. `"Customer"`), `:689-697` (unknown cluster → 404).

#### 7 Failure modes

- Unknown cluster → `ClusterNotFoundError` → HTTP 404 (`dedup/scoring.py:587-588`; `api/routes.py:965-966`).
- Approving a row whose `proposed_golden_id` is None (a unique row, which shares no real cluster_id in practice) changes only `approval_status`; the golden fields stay untouched (`dedup/scoring.py:598-600`).
- No persistence: a second call with stale rows silently re-applies; the drift defence is comparing `scored_with_weights_version` between proposal and approval (`dedup/scoring.py:298-300, 610-615`). ⚠ UNVERIFIED — no code path in the repo actually compares the fingerprint at approval time; it is stamped and carried only.

---

### Summary construction (`build_summary` — dedup/scoring.py)

#### 1 Purpose

Aggregates a result list into the `ScoringSummary` block returned by both endpoints (`dedup/scoring.py:1208-1244`; `api/routes.py:933, dedup/scoring_xlsx.py:264`).

#### 2 Inputs and outputs

Input: `results: List[ScoringResultRow]`, keyword `errors: int = 0` (file rows skipped for a blank Customer), `warnings: Optional[List[str]]` (request-level, e.g. a rejected weights override). Output: `ScoringSummary` with fields `rows_in, clusters, rows_elected, rows_duplicates, rows_unique, rows_manual_review, all_blocked_clusters, rows_with_warnings, errors, warnings` (`dedup/scoring.py:384-396`).

#### 3 Pseudocode

(`dedup/scoring.py:1214-1244`)

1. `rows_in := len(results) + errors`; copy `errors` and `warnings` (`:1215-1219`).
2. For each result:
   1. `rows_with_warnings += 1` if the row has any warnings (`:1223-1224`).
   2. `election_status == "unique"` → `rows_unique += 1`, **continue** (a unique row counts toward nothing else) (`:1225-1227`).
   3. `election_status == "manual_review"` → `rows_manual_review += 1` (`:1228-1229`).
   4. `is_golden_record` → `rows_elected += 1`, else `rows_duplicates += 1` (a lone manual_review row self-elects and thus counts as elected) (`:1230-1237`).
   5. Non-None `cluster_id` → add to `cluster_ids`; if manual_review, also to `manual_review_ids` (`:1238-1241`).
3. `clusters := len(cluster_ids)`; `all_blocked_clusters := len(manual_review_ids)` (`:1242-1243`).

Note the naming/semantics mismatch verified in the body: `all_blocked_clusters` actually counts **all manual_review clusters** (any demotion cause — inherited routing, all-blocked, low confidence, or zero signal), not only all-blocked ones (`dedup/scoring.py:1240-1243`).

#### 4 Constants

None; all counters start at the model defaults of 0 (`dedup/scoring.py:384-396`).

#### 5 Complexity

Single pass: O(n) time, O(c) set memory for c distinct cluster ids (`dedup/scoring.py:1220-1243`).

#### 6 Worked example

`test_summary_counts` (`tests/test_scoring.py:714-732`): five rows — C1 {1: year 2026, 2}, unclustered {3}, C2 {4, 5 both blocked} — give `rows_in=5, clusters=2, rows_elected=2, rows_duplicates=2, rows_unique=1, rows_manual_review=2, all_blocked_clusters=1, errors=0`. `test_manual_review_singleton_not_upgraded_in_summary` (`tests/test_scoring.py:382-394`): a lone manual_review row counts as `rows_manual_review=1` without minting a phantom cluster (`clusters=0, all_blocked_clusters=0`). File-side errors: `test_blank_customer_skipped_and_counted` (`tests/test_scoring.py:951-959`) shows `errors=1` and `rows_in=3` for one blank-Customer row among three.

#### 7 Failure modes

- Per-row warnings are excluded from serialisation (`warnings` has `exclude=True`, `dedup/scoring.py:307`), so the only JSON-visible trace of dirty values is `rows_with_warnings` plus request-level `summary.warnings` (`tests/test_scoring.py:699-712`).
- The `all_blocked_clusters` counter over-counts relative to its name whenever a cluster is manual_review for a non-blocked reason (`dedup/scoring.py:1240-1243`); the JSON docstring alias claims match only in the all-blocked test scenarios.

---

### Scoring XLSX round-trip (`score_workbook` — dedup/scoring_xlsx.py)

#### 1 Purpose

Runs the identical scoring/election/issue pipeline over an uploaded workbook and fills the empty score/derived/election columns **in place** with openpyxl, preserving the Weights sheet and every original column; all columns are located by header name, never index (`dedup/scoring_xlsx.py:1-7, 180-185`).

#### 2 Inputs and outputs

Input: raw workbook bytes. Output: `(workbook bytes, ScoringSummary)`. Raises `ScoringFileError` (unusable file → HTTP 400) and `DuplicateRowIdError` (repeated Customer → HTTP 400) (`dedup/scoring_xlsx.py:71-72, 180-194`; `api/routes.py:1003-1011`). The route additionally rejects non-`.xlsx`/`.xlsm` filenames and empty uploads with 400 (`api/routes.py:992-1001`) and streams the result back as `{stem}_scored.xlsx` (`api/routes.py:1023-1031`).

#### 3 Pseudocode

(`dedup/scoring_xlsx.py:180-319`)

1. **Guards:** import openpyxl (`ScoringFileError` if absent); `load_workbook` over the bytes, any parse failure → `ScoringFileError("Could not read uploaded file as XLSX: …")` (`:186-194`).
2. Data sheet = first worksheet whose header row contains a column normalising to `"customer"` (`_norm` strips all non-alphanumerics and lowercases, `:75-78`); none → `ScoringFileError` (`:114-120`). Build the normalised header→column map, first occurrence wins (`:123-133`).
3. **Weights:** locate a sheet titled `"weights"` (case/space-insensitive); parse and `coerce_weights` it (all-or-nothing, see above); on rejection append the reason to `request_warnings` and keep `dedup/weights.json` (`:199-213`).
4. Resolve input columns via `INPUT_HEADERS` (header → `ScoringRow` field, `:35-53`), the 8 `SF_ID_HEADERS` slots (`:56-59`), the cluster pair, `Confidence`, and `Reasoning` (`:215-227`). **Guard:** no Customer column → `ScoringFileError` (`:219-220`).
   - Cluster pair: prefer the production `("Routing", "Cluster ID")` pair appended by the dedup stage; fall back to the fixture's `("expected_routing", "expected_cluster")`; the pairs are never mixed (`_cluster_columns`, `:147-158`; fixture proof `tests/test_scoring.py:1004-1020`).
   - `_cluster_id_from_cells`: routing text must be `"cluster"` or `"manual_review"` (manual_review keeps cluster membership — only the merge was uncertain) else `cluster_id=None`; blank cluster cell → None; a float integer cluster id (Excel round-trip `3.0`) is normalised through `int` before `str` (`:161-177`).
5. **Row parse loop** over worksheet rows 2..max_row (`:235-261`):
   1. Skip rows where every readable cell is blank (`:240-241`).
   2. Blank Customer → `errors += 1`, log, skip (`:242-246`).
   3. Build `ScoringRow(row_id=str(customer).strip(), cluster_id=…, routing=…, confidence=…, reasoning=…, salesforce_ids=[8 cells], **field payload)`; remember the worksheet row number (`:247-261`).
6. **Same pipeline as JSON:** `results = elect_golden_records(rows, weights)`; `summary = build_summary(results, errors=errors, warnings=request_warnings)`; `issues = detect_issues(rows, results)` (`:263-265`).
7. **Writeback** — ensure (locate or append) every output column by header (`_ensure_column`, `:136-144`): the 11 `score_*` columns from `BREAKDOWN_COLUMNS`, `score_final`, `DERIVED_COLUMNS = ("Company_Code_Count", "Sales_Org_Count", "Salesforce_Instance_Count")`, `ELECTION_COLUMNS = ("is_golden_record", "golden_record_id", "proposed_golden_id", "election_status", "approval_status")`, and `scored_with_weights_version` (`:267-279, 64-68`). Then per parsed row (`:281-303`):
   1. Write each `score_breakdown[key]` into its column and `result.score` into `score_final` — equal to the sum of the written cells by construction (`:282-286`).
   2. Write the three derived counts from `derived_counts(row)` (`:287-290`).
   3. **manual_review blanking:** if `election_status == "manual_review"`, write `None` into `is_golden_record` and `golden_record_id`; the computed winner survives only in `proposed_golden_id` — "nobody filtering is_golden_record alone may act on an unreviewed row" (`:291-298`).
   4. Write `proposed_golden_id`, `election_status`, `approval_status`, `scored_with_weights_version` (`:299-303`).
8. **Issues sheet:** delete any existing `"Issues"` sheet, create it fresh with header `("row_id", "cluster_id", "issue_type", "detail")` and one row per issue (`:305-315, 30-31`).
9. Save to bytes and return with the summary (`:317-319`).

**Column contract** (single source of truth): `SCORE_BREAKDOWN_COLUMNS` in `dedup/scoring.py:59-71` maps every breakdown key to its exact file header (`sales_order_last_used → score_SalesOrderLastUsed`, `sales_order_count → score_SalesOrderCount`, `sales_order_partner_last_used → score_SalesOrderPartnerLastUsed`, `sales_order_partner_count → score_SalesOrderPartnerCount`, `equipment_count → score_EquipmentCount`, `sleeping_customer → score_SleepingCustomer`, `customer_status → score_CustomerStatus`, `account_group → score_AccountGroup`, `company_code_count → score_CompanyCodeCount`, `combined_presence_bonus → score_CombinedPresence`, `salesforce_instance_count → score_SalesforceInstances`). Both consumers import this one dict: the XLSX writer (`dedup/scoring_xlsx.py:15-16, 61-62, 268-271`) and the JSON model, whose computed fields serialize `score_breakdown` under exactly these aliases (`dedup/scoring.py:325-381`) with every other serialized key equal to its file header via field aliases (`Customer`, `Cluster ID`, `score_final`, `Company_Code_Count`, `Sales_Org_Count`, `Salesforce_Instance_Count`; `dedup/scoring.py:271-286`).

**Identity with the JSON path:** both routes execute the same three functions — `elect_golden_records` → `build_summary` → `detect_issues` (`api/routes.py:926-934` vs `dedup/scoring_xlsx.py:263-265`) — on `ScoringRow` objects built through the same model validators, so scores, winners, statuses and issues are byte-identical between paths for identical row data. The one deliberate divergence is representational: the file blanks `is_golden_record`/`golden_record_id` for manual_review rows while the JSON keeps the computed proposal in those fields (`dedup/scoring_xlsx.py:291-298` vs `dedup/scoring.py:1165-1169, 1186-1197`).

#### 4 Constants

`WEIGHTS_SHEET_NAME = "Weights"`, `ISSUES_SHEET_NAME = "Issues"`, `ISSUES_COLUMNS = ("row_id", "cluster_id", "issue_type", "detail")` (`dedup/scoring_xlsx.py:29-31`); `INPUT_HEADERS` 11-entry map (`:35-53`); `SF_ID_HEADERS = ["SF_ID_Biosystems", "SF_ID_AXS", "SF_ID_3", …, "SF_ID_8"]` (`:56-59`); `DERIVED_COLUMNS`, `ELECTION_COLUMNS` (`:64-68`).

#### 5 Complexity

For a workbook of R data rows and H header columns: header map O(H); row parse O(R·F) with F ≈ 23 read cells per row; the election dominates as in the JSON path; writeback O(R·(11+9)) cell writes; issues sheet O(#issues). Overall linear in cells touched, plus openpyxl (de)serialisation of the whole workbook.

#### 6 Worked example

`test_round_trip_preserves_weights_sheet_and_45_columns` (`tests/test_scoring.py:872-923`): three rows (cluster A1: "72000001" with `year=2026, orders=12, sleeping="No", status="active", codes="1001", orgs="2001"`; "72000002" with `year=2023`; unique "72000003"). After the round-trip: the Weights sheet survives with its header row; the 45 original columns are unchanged in place with original cell values intact; `is_golden_record` is a native Excel boolean (`True` row 2, `False` row 3 pointing at `"72000001"`); `election_status` "proposed"/"proposed"/"unique"; for every data row `score_final` is an `int` equal to the sum of the 11 `score_*` cells; derived counts written as plain values (1, 1, 0 for row 2). `test_manual_review_blanks_golden_in_file` (`tests/test_scoring.py:824-850`) shows the blanking: an all-blocked cluster leaves `is_golden_record`/`golden_record_id` empty (`None`) while `proposed_golden_id="1"` and `approval_status="proposed"`; the unique row keeps golden filled with `approval_status=None`. `test_issues_sheet_written_preserving_weights` (`:852-870`) verifies the Issues sheet header and an `all_blocked_cluster` row. End-to-end pipeline preservation across `/enrich/file → /api/dedup/file → /api/dedup/score/file` including a `score_final ≥ 20+25+15+10` check: `tests/test_scoring.py:1057-1135`.

#### 7 Failure modes

- Unreadable/parse-failing upload, missing Customer column/sheet, or missing openpyxl → `ScoringFileError` → HTTP 400 (`dedup/scoring_xlsx.py:186-196, 219-220`; `api/routes.py:1010-1011`).
- Repeated Customer number → `DuplicateRowIdError` → HTTP 400 (`tests/test_scoring.py:981-984, 1038-1047`).
- Blank Customer rows are skipped, counted in `summary.errors`, and their output cells stay empty (`dedup/scoring_xlsx.py:242-246`; `tests/test_scoring.py:951-967`).
- Broken Weights sheet → wholesale fallback to `dedup/weights.json` plus a summary warning; never a partial merge (`dedup/scoring_xlsx.py:207-213`; `tests/test_scoring.py:925-936`).
- Header matching is normalised (case/whitespace/punctuation-insensitive, first occurrence wins), so a duplicated header silently binds to its first column (`dedup/scoring_xlsx.py:123-133`). ⚠ NO FIXTURE COVERAGE for duplicated headers.

---

### Non-determinism notes

Expected and confirmed fully deterministic:

- **No LLM, no network:** the module docstring states it (`dedup/scoring.py:6-7`), and the code imports only `hashlib`, `json`, `logging`, `os`, `collections`, `pathlib`, `typing`, pydantic, and `dedup.cluster_key` (`dedup/scoring.py:19-39`); `dedup/scoring_xlsx.py` adds only `io` and openpyxl (`dedup/scoring_xlsx.py:9-27`). No randomness, clocks, or I/O beyond the weights file and the workbook bytes.
- **Input-order independence of winners:** the tie-break key ends in `row_id` (numeric or lexical), a total order over cluster members, so `min()` is invariant under input permutation (`dedup/scoring.py:939-955, 1106`); verified over 10 seeded shuffles of a 20-row payload (`tests/test_scoring.py:499-523`) and by an identical-bytes double-run of the workbook path (`tests/test_scoring.py:1216-1230`).
- **Dict/iteration ordering:** Python dicts are insertion-ordered; cluster grouping preserves input order (`dedup/scoring.py:1069-1072, 1084-1087`) and results are emitted in input order (`:1126-1152`), so output row order equals input row order by construction. Band matching iterates `weights` bands in JSON/file order with first-match-wins (`dedup/scoring.py:734-751`); the shipped bands are mutually exclusive per criterion (`dedup/weights.json:3-57`), so ordering cannot change points there — however, a caller-supplied override with **overlapping** numeric bands would make points depend on band insertion order (⚠ UNVERIFIED — no fixture supplies overlapping bands). `detect_issues` keys the winner off the first cluster member in result order (`dedup/scoring.py:506-509`); all members of a cluster share `proposed_golden_id` (`dedup/scoring.py:1193`), so this too is order-insensitive.
- **Environment sensitivity (deterministic given a fixed environment):** the manual_review confidence gate reads `CONFIDENCE_MERGE_THRESHOLD` from the environment when no explicit threshold is passed (`dedup/scoring.py:1004-1017`; `tests/test_scoring.py:341-348`), and retuned weights legitimately change results — the `scored_with_weights_version` fingerprint (`dedup/scoring.py:610-615`) exists precisely to make that visible (`tests/test_scoring.py:1164-1187`).
- **Hash stability:** `weights_version` and `cluster_hash` use sha256 over canonicalised inputs (sorted keys / sorted member ids), not Python's seeded `hash()`, so both are stable across processes and machines (`dedup/scoring.py:610-615`; `dedup/cluster_key.py:16-23`).


# Part K — LLM prompt appendix and non-determinism inventory

This part answers specification items (e) — precisely what is non-deterministic in every procedure that calls an LLM or an external search API, and whether caching or fixture capture makes it reproducible — and (f) — the verbatim system and user prompt template for every LLM call, the evidence placed in context, the expected return, the parsing path, and the parse-failure behaviour. Where a prompt does not constrain the model to supplied evidence, that is stated plainly rather than assumed.

All paths are relative to `C:\Users\apoorva.ajay\Downloads\ApoorvaThesis\ApoorvaThesis\enrichment_api`. Every prompt below is reproduced verbatim from the cited constant. Note on brace escaping: the user-prompt templates are Python `str.format` templates, so `{{` / `}}` in the source render as single `{` / `}` in the message actually sent, and `{name}` placeholders are replaced by evidence at the call site (interpolation is documented per call).

---

### K.1 — LLM prompt appendix (sections A.0–A.16)

#### A.0 Shared transport and response parsing

##### A.0.1 Phase-1 transport: `call_openai`

Azure OpenAI is the only LLM backend in every environment (llm/openai_client.py:1-8). `call_openai` sends one chat completion with the request parameters fixed in code (llm/openai_client.py:198-207):

```python
response = await client.chat.completions.create(
    model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    max_completion_tokens=max_tokens,
    temperature=0.0,
    response_format={"type": "json_object"},
)
```

- Deployment: `AZURE_OPENAI_DEPLOYMENT`, default `"gpt-5.4"` (llm/openai_client.py:199; same default in config.py:157 and config.py:84).
- Temperature: hard-coded `temperature=0.0` for every Phase-1 call (llm/openai_client.py:205).
- `response_format={"type": "json_object"}` on every Phase-1 call (llm/openai_client.py:206).
- No `seed` parameter appears anywhere in the request (llm/openai_client.py:198-207); a repository-wide search for `seed` finds no API seed usage in any source file.
- `max_completion_tokens` comes from the caller; `call_openai`'s own default is 500 (llm/openai_client.py:180), but the orchestrator path always goes through `extract_json`, which supplies its own default (see A.0.2).
- API version: `AZURE_OPENAI_API_VERSION` else `DEFAULT_AZURE_OPENAI_API_VERSION = "2024-08-01-preview"` (llm/openai_client.py:78, 149-153).
- HTTP timeouts: read timeout `LLM_HTTP_TIMEOUT` default 60 s, connect timeout `LLM_HTTP_CONNECT_TIMEOUT` default 30 s (llm/openai_client.py:162-168).
- Any transport/API exception is re-raised as `RuntimeError("OpenAI call failed: …")` (llm/openai_client.py:209-210).

##### A.0.2 Phase-1 parsing: `OpenAIClient.extract_json`

`extract_json(system_prompt, user_prompt, *, temperature=0.0, max_tokens=1024)` (llm/openai_client.py:257-264) is the single entry point for all eleven Phase-1 production calls. Behaviour (llm/openai_client.py:270-292):

1. Up to two attempts (`for attempt in range(2)`, llm/openai_client.py:271). Each attempt is a **fresh model call** via `call_openai` — the retry re-generates, it does not re-parse.
2. A `None` response is coerced to `""` (llm/openai_client.py:276-277).
3. Markdown code fences are stripped first: `_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)` (llm/openai_client.py:72), applied at llm/openai_client.py:279-281.
4. The remaining text is parsed with `json.loads` (llm/openai_client.py:283-284).
5. On `JSONDecodeError` at attempt 0 it logs a warning and retries once (llm/openai_client.py:285-288); on failure after the retry it raises `ValueError(f"LLM returned invalid JSON: {raw[:200]}")` (llm/openai_client.py:289-290).

⚠ UNVERIFIED-free observation, but methodologically notable: `extract_json` accepts a `temperature` keyword (default 0.0, llm/openai_client.py:262) yet **does not forward it** — the inner call is `call_openai(system_prompt, user_prompt, max_tokens=max_tokens, client=client)` (llm/openai_client.py:272-275), and `call_openai` hard-codes `temperature=0.0` (llm/openai_client.py:205). Every Phase-1 call therefore runs at temperature 0.0 regardless of what a caller passes.

Each Phase-1 caller wraps `extract_json` in its own `try/except`; the per-call fallback is documented in each section below.

##### A.0.3 Dedup transport: `DedupLLM.adjudicate`

The Phase-2 dedup adjudicator reuses `get_openai_client` but differs in four ways (dedup/llm.py:1-9):

- Deployment: `AOAI_DEPLOYMENT_DEDUP` else `AZURE_OPENAI_DEPLOYMENT` else `"gpt-5.4"` (dedup/llm.py:117-121).
- **No temperature parameter is passed** — the request dict contains only `model`, `messages`, `max_completion_tokens`, `response_format`, and (conditionally) `reasoning_effort` (dedup/llm.py:174-184). With temperature unset, the service default applies (the Azure OpenAI chat-completions API documents a default of 1; reasoning-model deployments ignore temperature). Instead, `reasoning_effort` is sent, from `DEDUP_REASONING_EFFORT` default `"low"` (dedup/llm.py:122, 183-184). If the deployment rejects `reasoning_effort` (detected by `_is_unsupported_reasoning_effort`, dedup/llm.py:33-46), the parameter is disabled for the process lifetime and the call retried (dedup/llm.py:202-207).
- No `seed` parameter is passed (dedup/llm.py:174-184).
- `max_completion_tokens`: caller-supplied; `adjudicate`'s default is 4000 (dedup/llm.py:156-162). `response_format={"type": "json_object"}` (dedup/llm.py:181).
- API version: `AOAI_API_VERSION_DEDUP` else `AZURE_OPENAI_API_VERSION` else `DEFAULT_API_VERSION = "2025-04-01-preview"` (dedup/llm.py:112, 124-128).
- Retries: `DEDUP_MAX_RETRIES` default 3 (dedup/llm.py:123); only transient failures retry (connection/timeout errors, HTTP 429, and 5xx — dedup/llm.py:49-60) with exponential backoff `0.5 * (2 ** attempt)` seconds (dedup/llm.py:209-215). `adjudicate` **never raises**: on exhausted retries it returns `DedupLLMResult(error=…)` so one bad call never fails a block (dedup/llm.py:163-168, 220).
- Telemetry: prompt/completion tokens, latency, and model version are captured per call (dedup/llm.py:186-196).

##### A.0.4 Dedup parsing: `parse_json_object`

`parse_json_object(raw)` (dedup/llm.py:75-101) parses defensively: strip, apply the shared `_FENCE_RE` fence extractor (imported from `llm.openai_client`, dedup/llm.py:21; applied dedup/llm.py:85-87), `json.loads`; on failure, take the outermost `{…}` span (`text.find("{")` … `text.rfind("}")`) and retry (dedup/llm.py:90-100); return `None` when nothing parses or the result is not a dict (dedup/llm.py:100-101). There is **no model-side retry on unparseable JSON** in the dedup path — callers treat `None` as "uncertain" (dedup/llm.py:79-81).

---

#### A.1 LLM call: UC 0 overflow check (Name 1 overflow into Name 2)

Runs first in the pipeline; a flagged record receives no further enrichment ("flag only, never auto-correct") (enrichment/overflow_check.py:1-10). Triggered only when both Name 1 and Name 2 are non-blank (enrichment/overflow_check.py:41-42).

System prompt (llm/prompts.py:10-14):

```
You detect whether two adjacent customer-master name fields read as one continuous organisation name split across the fields, or as two separate entities. Return valid JSON only.
```

User prompt template (llm/prompts.py:16-42):

```
Name 1: {name1}
Name 2: {name2}

Read these two fields together as if they were the full name of one organisation. Does the concatenation 'Name 1 + Name 2' form a single continuous organisation name (an overflow), or do Name 1 and Name 2 describe two distinct entities (e.g. an institution + a department)?

Return JSON:
{{
  "is_overflow": true | false,
  "confidence": "high" | "medium" | "low",
  "reasoning": "str"
}}

Rules:
1. is_overflow=true only when Name 1 + Name 2 reads naturally as ONE organisation name — e.g. 'Adams Air' + 'Hydraulics Inc' → 'Adams Air Hydraulics Inc'.
2. is_overflow=false when Name 2 is a department, division, research group, lab, contact person, or any standalone unit within Name 1.
3. When in doubt, prefer false. The goal is to surface likely overflows, not to flag every case with a shared word.
4. Legal suffixes (Inc, Ltd, LLC, Corp, Co, GmbH, AG) appearing in Name 2 with no department qualifier are a strong overflow signal.
```

- Interpolated evidence: the raw record fields `name1`, `name2` only (enrichment/overflow_check.py:44-47).
- Expected return: JSON object `{is_overflow: bool, confidence: high|medium|low, reasoning: str}` (llm/prompts.py:24-29).
- Parsing: `extract_json` (enrichment/overflow_check.py:50-52; mechanism in A.0.2).
- Parse/call failure: exception is caught, logged, and the default `OverflowCheckResult()` (`is_overflow=False`) is returned — the check silently no-ops (enrichment/overflow_check.py:53-55, 26-30).
- Post-filter: the flag is applied only when `is_overflow` is true **and** confidence is `high` or `medium` (enrichment/overflow_check.py:63-66).
- Evidence grounding: no instruction restricts the model to supplied evidence; the task is a linguistic judgement over the two supplied fields and the prompt contains no sentence forbidding use of parametric knowledge (llm/prompts.py:16-42).
- Request parameters: `extract_json` defaults — deployment `AZURE_OPENAI_DEPLOYMENT`/`gpt-5.4`, temperature 0.0, `max_completion_tokens=1024`, `response_format={"type":"json_object"}`, no seed (llm/openai_client.py:199, 205-206, 264; call site passes no overrides, enrichment/overflow_check.py:50-52).

#### A.2 LLM call: person classifier (preprocess plain-name classification)

Classifies short free-text candidates as person vs organisation during preprocessing. The prompts are defined inline in the preprocessing module, not in `llm/prompts.py`.

System prompt (enrichment/preprocess.py:2290-2293):

```
You classify a short text as either a person's name or an organisation/department/other. Return valid JSON only.
```

User prompt template (enrichment/preprocess.py:2295-2305):

```
Text: {text}

Return JSON:
{{
  "kind": "person" | "organisation" | "other",
  "confidence": "high" | "medium" | "low"
}}

Return 'person' only if you are confident this is a human name. Anything that could plausibly be a company, department, lab, research group, or product → 'organisation' or 'other'.
```

- Interpolated evidence: one candidate string `text` per call; the classifier loops over candidates, one LLM call each (enrichment/preprocess.py:2319-2324).
- Expected return: `{kind: person|organisation|other, confidence: high|medium|low}` (enrichment/preprocess.py:2297-2301).
- Parsing: `extract_json` (enrichment/preprocess.py:2321-2324).
- Failure: exception caught and logged; that candidate is skipped (`continue`) (enrichment/preprocess.py:2325-2327). Only `kind == "person"` with `confidence == "high"` enters the output map; everything else is dropped (enrichment/preprocess.py:2328-2331).
- Evidence grounding: no such instruction exists; the call is a pure parametric-knowledge classification of a short string (enrichment/preprocess.py:2295-2305).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/preprocess.py:2321-2324).

#### A.3 LLM call: person affiliation (Stage 2b)

Used only when Name 1 held just a person's name (moved to Contact), leaving the record with no organisation; the caller later confirms the proposed institution against ROR in the record's country (llm/prompts.py:374-380).

System prompt (llm/prompts.py:381-406):

```
You identify the CURRENT primary employer/institution and department of a named person from web-search result snippets.

Rules:
1. Ground every answer in the provided snippets. If the snippets do not clearly tie THIS person (by full name) to an institution, return institution=null. Never guess from the name alone.
2. institution = the organisation the person works at now (university, research institute, hospital, or company) — its full proper name, not an acronym.
3. department = the person's sub-unit/department if a snippet states it; otherwise null.
4. Match the person by full name. If the snippets are about a different person with a similar name, return institution=null.
5. confidence: 'high' when a snippet explicitly names this person AND their institution together; 'medium' when the tie is strongly implied by one snippet; 'low' when uncertain or conflicting.
6. Never output an address, street, city, or postal code in institution or department. These are name fields, not address fields.
7. No fabrication. Prefer institution=null over a plausible guess.

Return ONLY JSON: {"institution": string|null, "department": string|null, "confidence": "high"|"medium"|"low"}.
```

User prompt template (llm/prompts.py:408-417):

```
Person: {contact}
Known location (from the record): {location}

Web search results:
{results}

Identify this person's current institution and department per the rules. Return the JSON object only.
```

- Interpolated evidence: the contact name; a location string built from city/region/country (`"not provided"` when absent); and `results` — a blob of the top ≤5 SERP hits formatted as `[i] {title}\nURL: {url}\n{snippet}` (enrichment/person_affiliation.py:137-145). Queries are built most-specific-first (corporate/edu e-mail domain, then location, then a generic disambiguator) (enrichment/person_affiliation.py:80-96); the first query with hits is used (enrichment/person_affiliation.py:122-131).
- Expected return: `{institution: string|null, department: string|null, confidence: high|medium|low}` (llm/prompts.py:403-405).
- Parsing: `extract_json` (enrichment/person_affiliation.py:148-150).
- Failure: exception caught and logged; an empty `PersonAffiliation()` (institution=None) is returned — the stage never raises (enrichment/person_affiliation.py:151-153, 110-115).
- Evidence grounding: **yes** — "Ground every answer in the provided snippets. If the snippets do not clearly tie THIS person (by full name) to an institution, return institution=null. Never guess from the name alone." and "No fabrication. Prefer institution=null over a plausible guess." (llm/prompts.py:385-388, 400-401).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/person_affiliation.py:148-150).

#### A.4 LLM call: company canonicalisation (Name 1, LLM-only)

LLM-only canonicalisation of a company Name 1; zero SERP calls; only high-confidence answers accepted (enrichment/company_canonical.py:1-5).

System prompt (llm/prompts.py:221-224):

```
You normalise user-supplied company names to the canonical registered form the company uses publicly. Return valid JSON only.
```

User prompt template (llm/prompts.py:226-255):

```
User-supplied company name: {name1}
Street: {street}
Postal code: {postal_code}
City: {city}
State: {state}
Country: {country}

Return JSON:
{{
  "official_name": "str or null",
  "confidence": "high|medium|low",
  "reasoning": "str"
}}
Rules:
1. Return a confident canonical form only when you are certain it matches the intended company. Use the geographic context to disambiguate.
2. The full street address may identify a well-known corporate headquarters and help you recognise a misspelled or abbreviated form of THAT company's name (e.g. a typo of the company headquartered there). Use it to CORRECT or disambiguate a name that is already a plausible variant of the company at that address. NEVER replace the given name with a different company just because they share a building — many firms share an address, so the name must still match.
3. Return null if you are not sure.
4. Do not invent companies. Do not resolve acronyms you do not recognise.
5. confidence=high means you are certain of the exact wording.
```

- Interpolated evidence: record fields `name1`, `street`, `postal_code`, `city`, `state`, `country` (missing values become the literal string `unknown`) (enrichment/company_canonical.py:48-55).
- Expected return: `{official_name: str|null, confidence, reasoning}` (llm/prompts.py:233-238).
- Parsing: `extract_json` (enrichment/company_canonical.py:58-60).
- Failure: exception caught, logged, empty result returned (enrichment/company_canonical.py:61-63). Post-filters: sentinel strings (`null/none/n/a/na`) rejected (enrichment/company_canonical.py:70-72); non-high confidence rejected (enrichment/company_canonical.py:73-78); a deterministic identity guard `canonical_preserves_identity` rejects a canonical that is a different entity, exposing it only as `proposed_name` for optional GLEIF re-verification (enrichment/company_canonical.py:83-94).
- Evidence grounding: no instruction restricts the model to supplied evidence — by design the canonical form comes from the model's parametric knowledge of the company; the closest constraints are "Do not invent companies. Do not resolve acronyms you do not recognise." and "Return null if you are not sure." (llm/prompts.py:251-253).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/company_canonical.py:58-60).

#### A.5 LLM call: Tier 2 canonical (department, LLM-only)

Runs when Name 2 is present but Tier 1 ROR child matching found no match; zero SERP calls; result used only at confidence=high (enrichment/tier2_canonical.py:1-11).

System prompt (llm/prompts.py:188-192):

```
You normalise user-supplied academic department names to the canonical wording the institution itself uses on its own website. Return valid JSON only. No markdown or code fences.
```

User prompt template (llm/prompts.py:194-214):

```
Institution (verified): {institution}
User-supplied department text: {name2}

Return the official name of this unit as the institution documents it on its own website (e.g. 'Department of X', 'Division of X', 'School of X', 'Institute of X').

Return JSON:
{{
  "official_name": "str or null",
  "confidence": "high|medium|low",
  "reasoning": "str"
}}
Rules:
1. Only return a name if you are confident it is the institution's actual canonical wording. When in doubt, return null.
2. Do not invent units the institution does not have.
3. Match the subject the user supplied — if they said 'Biochemistry', do not return 'Chemistry'.
4. confidence=high means you are certain of the exact wording. Use medium or low if you are guessing the form.
```

- Interpolated evidence: verified institution name and the user-supplied `name2` (enrichment/tier2_canonical.py:68-71).
- Expected return: `{official_name: str|null, confidence, reasoning}` (llm/prompts.py:200-205).
- Parsing: `extract_json` (enrichment/tier2_canonical.py:74-76).
- Failure: exception caught, logged, empty result returned; caller falls through to the next tier (enrichment/tier2_canonical.py:77-79, 8-11). Post-filters: sentinel strings rejected (enrichment/tier2_canonical.py:90-92); only `confidence == "high"` accepted (enrichment/tier2_canonical.py:94-101); a "prefix downgrade" (canonical equal to the input with a unit prefix stripped) is rejected deterministically (enrichment/tier2_canonical.py:31-45, 103-111).
- Evidence grounding: no instruction restricts the model to supplied evidence — the tier deliberately "Uses the LLM's existing knowledge of well-known institutions" (enrichment/tier2_canonical.py:4-6); the guard sentences are "Only return a name if you are confident it is the institution's actual canonical wording. When in doubt, return null." and "Do not invent units the institution does not have." (llm/prompts.py:207-209).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/tier2_canonical.py:74-76).

#### A.6 LLM call: Tier 2A contact-affiliation extraction

Extracts a contact person's department from a fetched web page.

System prompt (llm/prompts.py:49-52):

```
Data extraction assistant for MDM pipeline. Return valid JSON only. No markdown or code fences.
```

User prompt template (llm/prompts.py:54-104):

```
Extract affiliation for: {contact}
Institution: {institution}
Existing Name 2: {name2}
Existing Name 3: {name3}
Page: {page_text}

Return JSON:
{{
  "person_found": bool,
  "official_dept": "str or null",
  "official_group": "str or null",
  "title": "str or null",
  "name2_match": "exact|partial|no_match|unknown",
  "name2_match_score": 0-100,
  "confidence": "high|medium|low",
  "reasoning": "str"
}}
Rules:
1. If the page is not about the named person, set person_found=false and all other fields to JSON null.
2. For official_dept, pick the institution's canonical department name using ALL available signals in the input: URL host, URL path, page title, H1, breadcrumb, and body. An institution's URL host often includes a leading subdomain that abbreviates the department (e.g. a leading token before the main institution domain) — if so, infer the full canonical department name from that abbreviation.
3. ALWAYS prefer the most specific academic unit available. Granularity ranking (most to least specific):
     a) 'Department of X' or 'Division of X'  -- STRONGLY PREFERRED
     b) 'Institute of X' or 'Center for X' (peer-level)
     c) 'School of X', 'College of X', 'Faculty of X' (parent units -- FALLBACK ONLY)
   If the page mentions BOTH a department and an enclosing school/college/faculty for this person (e.g. 'Department of Neuroscience, College of Medicine'), return the DEPARTMENT, never the college. A faculty member is always in a department within the college; the college alone is too coarse for downstream lookup. Only return a school/college/faculty when no department is identifiable on the page.
4. Expand any subdomain abbreviation to the institution's actual canonical department wording.
5. Reject generic role labels such as 'Research', 'Admin', 'Staff', 'Faculty', 'Team', or 'Office'. They describe what the person does, not the unit they belong to. If the body contains only a role label, derive the unit from the URL host instead.
6. Do not return a bare subject word alone ('Anesthesia', 'Chemistry') and do not return a job title ('Professor of X').
7. official_group may be set verbatim from the body when a specific research group, lab, or centre is clearly named. Otherwise null. Use JSON null, never the string 'null'.
```

- Interpolated evidence: contact name; institution; existing Name 2 / Name 3 (or `"not recorded"`); and `page_text` — a structured blob assembled per fetched candidate page as `URL host / URL path / Title / H1 / Breadcrumb / Body` (enrichment/tier2a_contact.py:123-141, 376-383). Body text is truncated to `max_page_content_chars` by the fetcher (search/page_fetcher.py:246-249; effective default 1500, config.py:208-210). Up to 3 name-verified SERP candidates are tried in rank order (enrichment/tier2a_contact.py:109, 100-106); candidate ranking boosts exact name-in-URL (+100) and title-starts-with-name (+20) (enrichment/tier2a_contact.py:340-364).
- Expected return: 8-field JSON object shown above (llm/prompts.py:60-70).
- Parsing: `extract_json` via `_extract_affiliation` (enrichment/tier2a_contact.py:367-383).
- Failure: exception caught per candidate; the loop continues to the next candidate page (enrichment/tier2a_contact.py:135-144). `person_found=false` or `confidence == "low"` also skip the candidate (enrichment/tier2a_contact.py:147-154). Results feed Mode A (populate) or Mode B (verify/correct with rapidfuzz at `fuzzy_match_threshold`) (enrichment/tier2a_contact.py:163-171, 386-426).
- Evidence grounding: partial — Rule 2 constrains the source to "ALL available signals in the input: URL host, URL path, page title, H1, breadcrumb, and body" (llm/prompts.py:74-77), but Rules 2 and 4 also instruct the model to "infer the full canonical department name from that abbreviation" and "Expand any subdomain abbreviation to the institution's actual canonical department wording" (llm/prompts.py:78-80, 93-94), which requires parametric knowledge beyond the page. No sentence forbids outside knowledge.
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/tier2a_contact.py:383).

#### A.7 LLM call: Tier 2B department-search extraction

Extracts the official unit name a fetched page represents, from structured page elements only. The input `name2` is deliberately not shown to the model, "we don't want the model to echo the user's abbreviated input when the page itself uses the canonical form" (enrichment/tier2b_dept.py:255-258).

System prompt (llm/prompts.py:110-113) — identical wording to Tier 2A's system prompt:

```
Data extraction assistant for MDM pipeline. Return valid JSON only. No markdown or code fences.
```

User prompt template (llm/prompts.py:115-137):

```
Extract the official department or division name that this page represents.
Organisation: {name1}

Authoritative page elements (use ONLY these as your source):
URL path:   {url_path}
Title tag:  {page_title}
H1 heading: {h1}
Breadcrumb: {breadcrumb}

Return JSON:
{{
  "official_name": "str or null",
  "confidence": "high|medium|low",
  "reasoning": "str"
}}
Rules:
1. Extract ONLY from the four authoritative elements above. Do not invent, reformat, abbreviate, or expand anything.
2. Copy the wording verbatim from whichever element clearly names the unit. Priority order: title tag > H1 > breadcrumb > URL path.
3. If none of the elements clearly name a unit, return null.
```

- Interpolated evidence: `name1` plus the four structured elements of the fetched page — URL path, title tag, H1, breadcrumb — each replaced by `(none)` when absent (enrichment/tier2b_dept.py:259-265); the elements are extracted deterministically by `PageFetcher._sync_fetch_structured` (search/page_fetcher.py:217-258; each capped at 300 chars, search/page_fetcher.py:254-256).
- Expected return: `{official_name: str|null, confidence, reasoning}` (llm/prompts.py:124-129).
- Parsing: `extract_json` (enrichment/tier2b_dept.py:266).
- Failure: `_extract_department` does not catch; the exception propagates to the Tier 2B caller loop (⚠ handling is in the surrounding tier flow, not in this function — enrichment/tier2b_dept.py:247-266 contains no try/except).
- Evidence grounding: **yes** — "Authoritative page elements (use ONLY these as your source)" (llm/prompts.py:119) and "Extract ONLY from the four authoritative elements above. Do not invent, reformat, abbreviate, or expand anything." (llm/prompts.py:131-132).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/tier2b_dept.py:266).

#### A.8 LLM call: lab/group → parent department resolution (UC 13)

Resolves a granular Name 2 unit (lab/group/centre/core/facility) to its parent academic department from the institution's own pages (enrichment/lab_resolver.py:1-19).

System prompt (llm/prompts.py:143-148):

```
Data extraction assistant for MDM pipeline. You identify the PARENT academic department of a research unit (lab, research group, centre, core, or facility) from a page on its institution's website. Return valid JSON only. No markdown.
```

User prompt template (llm/prompts.py:150-182):

```
Institution: {name1}
Research unit (a lab/group/centre/facility): {lab_name}

Authoritative page elements (use ONLY these as your source):
URL path:   {url_path}
Title tag:  {page_title}
H1 heading: {h1}
Breadcrumb: {breadcrumb}

Return the parent academic department, division, school, college, faculty, or institute that this research unit belongs to.

Return JSON:
{{
  "parent_department": "str or null",
  "confidence": "high|medium|low",
  "reasoning": "str"
}}
Rules:
1. The parent must be an academic unit at department level or higher: 'Department of X', 'Division of X', 'School of X', 'College of X', 'Faculty of X', or 'Institute of X'. NEVER another lab, group, centre, core, or facility.
2. Look at: breadcrumb (often 'Home > Chemistry > Groups > NMR Lab' → parent is 'Department of Chemistry'), URL path (/chemistry/research/nmr-lab/ → 'Department of Chemistry'), and the title/H1 if they explicitly name the parent.
3. confidence=high: parent is explicitly stated (in breadcrumb or title). confidence=medium: parent is implied by URL path. confidence=low: best guess.
4. If you cannot identify a clear parent academic department, return null. Do not invent.
5. Use JSON null, never the string 'null'.
```

- Interpolated evidence: institution, lab name, and the four structured page elements (`(none)` when absent) for each of up to 3 fetched candidate pages (enrichment/lab_resolver.py:101-117). SERP query: `"{lab_name}" department site:{domain}` on-domain, else `"{institution}" "{lab_name}" department` (enrichment/lab_resolver.py:70-77); SERP results cached (enrichment/lab_resolver.py:79-84); on-domain filtering when a domain is known (enrichment/lab_resolver.py:86-91).
- Expected return: `{parent_department: str|null, confidence, reasoning}` (llm/prompts.py:161-166).
- Parsing: `extract_json` (enrichment/lab_resolver.py:115-117).
- Failure: exception caught per candidate; loop continues (enrichment/lab_resolver.py:118-123). Null/sentinel parents skipped (enrichment/lab_resolver.py:125-130). Among successful extractions the best is chosen by confidence rank, then longer name, then alphabetical — a deterministic tie-break (enrichment/lab_resolver.py:146-157).
- Evidence grounding: **yes** — "Authoritative page elements (use ONLY these as your source)" (llm/prompts.py:153) and "If you cannot identify a clear parent academic department, return null. Do not invent." (llm/prompts.py:178-180).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/lab_resolver.py:115-117).

#### A.9 LLM call: website inference (Path C)

Fallback after Path B: asks the model for a company's official website from parametric memory; any produced URL is written with `confidence='low'` and flagged for manual review (enrichment/website_resolver.py:550-566).

System prompt (llm/prompts.py:262-265):

```
You return the official corporate website URL for a company. Return valid JSON only. Never guess or hallucinate URLs.
```

User prompt template (llm/prompts.py:267-287):

```
Given the following company information, provide the official website URL.

Company: {name1}
City: {city}
State: {state}
Country: {country}

Return JSON:
{{
  "website_url": "str or null",
  "confidence": "high|medium|low"
}}

Rules:
1. Return the official corporate website URL only when you are confident the company is well-known and the URL is correct.
2. If you are not confident or the company is obscure, return JSON null for website_url.
3. Do not guess or hallucinate URLs. Use JSON null, never the string "null" or "UNKNOWN".
4. Format: https://www.example.com (include scheme).
```

- Interpolated evidence: `name1`, city, state, country (missing values become `(unknown)`) (enrichment/website_resolver.py:574-579).
- Expected return: `{website_url: str|null, confidence}` (llm/prompts.py:274-278).
- Parsing: `extract_json` (enrichment/website_resolver.py:598-600); sentinel strings (`"", null, none, unknown, n/a, na`) coerced to None (enrichment/website_resolver.py:612-616); a URL-shape check (`_looks_like_url`) gates acceptance (enrichment/website_resolver.py:618-625, 536-547).
- Failure: exception caught, logged, empty `WebsiteResolution()` returned (enrichment/website_resolver.py:601-607). On success the resolution is always `confidence="low"`, `source="llm"` (enrichment/website_resolver.py:633).
- Evidence grounding: no instruction restricts the model to supplied evidence — the URL is expected from parametric memory; the anti-fabrication sentences are "Never guess or hallucinate URLs." (system, llm/prompts.py:264) and "Do not guess or hallucinate URLs. Use JSON null, never the string "null" or "UNKNOWN"." (llm/prompts.py:284-285).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/website_resolver.py:598-600).
- Diagnostics: when `WEBSITE_TRACE=true` a per-call JSON trace is logged; behaviour unchanged (config.py:114-118, 245-249; enrichment/website_resolver.py:581-595).

#### A.10 LLM call: address residual classification (street_2/street_3)

Classifies leftover street-field content after deterministic extractors ran; only ambiguous residuals reach the LLM (a value containing a house number and street-type word is skipped as unambiguous) (enrichment/address_processing.py:695-699, 718-722).

System prompt (llm/prompts.py:294-299):

```
You classify residual values found in street address fields after deterministic extractors have already pulled out PO Box, Suite, Building, Floor, Room, Unit, Mail Stop, c/o, and Attn patterns. Return valid JSON only. No markdown.
```

User prompt template (llm/prompts.py:301-312):

```
Classify this value from a street address field. It was found after PO Box, Suite, Building, Floor, Room, Unit, Mail Stop, c/o, and Attn patterns were already extracted.

Value: "{value}"
Name 1: "{name1}"  Street 1: "{street}"  City: "{city}"  Country: "{country}"

Classify as exactly one of: STREET_ADDRESS, DEPARTMENT, PERSON_NAME, ORG_NAME, LOGISTICS, MAIL_CODE, UNCLEAR
Return JSON: {{"classification": "...", "confidence": 0.0-1.0}}
```

- Interpolated evidence: the residual value plus record context `name1`, `street`, `city`, `country` (empty string when absent) (enrichment/address_processing.py:669-675).
- Expected return: `{classification: <one of 7 labels>, confidence: 0.0-1.0}` (llm/prompts.py:308-311).
- Parsing: `extract_json` with `max_tokens=200` (enrichment/address_processing.py:677-680); confidence coerced to float, classification upper-cased (enrichment/address_processing.py:684-692).
- Failure: exception caught, logged, `(None, 0.0)` returned (enrichment/address_processing.py:681-683); the caller then records issue `G1-ADDR-009` and leaves the slot unchanged (enrichment/address_processing.py:726-728). Verdicts below the threshold `_RESIDUAL_CONFIDENCE_THRESHOLD = 0.85` are not acted on (enrichment/address_processing.py:657, 729).
- Evidence grounding: no explicit instruction restricts the model to supplied evidence; the task is a closed-label classification of the supplied value with record context, and no sentence forbids outside knowledge (llm/prompts.py:301-312).
- Request parameters: temperature 0.0, `max_completion_tokens=200` (explicit override, enrichment/address_processing.py:679), JSON mode, no seed (llm/openai_client.py:205-206).

#### A.11 LLM call: Tier 3 inference (last resort)

Pure parametric inference from the record itself; always flagged for review (enrichment/tier3_llm.py:1, 78-84).

System prompt (llm/prompts.py:319-322):

```
Help clean SAP customer master data for scientific instrument manufacturer. Return valid JSON only.
```

User prompt template (llm/prompts.py:324-368):

```
Infer official org and dept names from this record.
Name 1: {name1}
Name 2: {name2}
Name 3: {name3}
Contact: {contact}
Address: {street}, {city}, {state} {zip}, {country}

Return JSON:
{{
  "name1_suggestion": "str or null",
  "name2_suggestion": "str or null",
  "name3_suggestion": "str or null",
  "confidence": "high|medium|low",
  "reasoning": "str",
  "requires_verification": true
}}
Rules:
1. requires_verification is always true. Output is flagged for manual review either way.
2. For name2_suggestion, when the institution is well-known (e.g. Harvard Medical School, University of Florida) and the contact's department can be plausibly inferred from public knowledge, propose a SPECIFIC department-level guess (e.g. 'Department of Neuroscience', 'Department of Genetics'). Use confidence='medium' for educated guesses, 'low' for shots in the dark, 'high' only when you are certain. A best-guess department is more useful than null.
3. Strongly prefer 'Department of X' or 'Division of X' over 'School of X' / 'College of X' / 'Faculty of X'. A faculty member at 'College of Medicine' is always inside a specific department. Only fall back to school/college/faculty when no plausible department guess exists.
4. Return null for name2_suggestion only when the institution is unknown to you or the contact has no inferrable affiliation.
5. Do NOT return name2_suggestion equal to name1, and do NOT return a parent of name1 (e.g. name1='Harvard Medical School' must not yield name2='Harvard University').
6. No fabrication of institutions or invented people.
7. NEVER put address content in a name field. The street, house number, postal code, and city provided as context are address fields — name1_suggestion, name2_suggestion and name3_suggestion must never contain a street name, house number, postal/ZIP code, or a city/site string copied from the address. If you cannot infer a real organisation or department name, return null for that field.
```

- Interpolated evidence: the raw record fields name1/name2/name3/contact (or `"not recorded"`) and address components (or `""`) (enrichment/tier3_llm.py:88-98).
- Expected return: the 6-field JSON object above (llm/prompts.py:332-339).
- Parsing: `extract_json` (enrichment/tier3_llm.py:101).
- Failure: exception caught; result marked `confidence="none"`, `enrichment_status="failed"`, `flag_reason="LLM call failed"` (enrichment/tier3_llm.py:102-107). Confidence high/medium writes suggestions but keeps `flag_for_review=True`; low confidence never overwrites originals (enrichment/tier3_llm.py:112-124, 154-160). A deterministic post-guard rejects address-like name suggestions (postal codes, street patterns, ≥50 % token overlap with the record's street) (enrichment/tier3_llm.py:15-48, 126-145); the orchestrator additionally rejects a `name1_suggestion` that does not preserve entity identity (enrichment/orchestrator.py:703-717).
- Evidence grounding: no instruction restricts the model to supplied evidence — Rule 2 explicitly invites inference "from public knowledge" (llm/prompts.py:346-347); the anti-fabrication sentence is "No fabrication of institutions or invented people." (llm/prompts.py:361).
- Request parameters: `extract_json` defaults — temperature 0.0, `max_completion_tokens=1024`, JSON mode, no seed (llm/openai_client.py:205-206, 264; no overrides at enrichment/tier3_llm.py:101).

#### A.12 Shared dedup system prompt (Modes A, B, and residue)

All three dedup adjudication calls share one system prompt, versioned as `PROMPT_VERSION = "p2-dedup-v3"` which is logged per call and echoed in every output row (dedup/prompts.py:12-14).

System prompt (dedup/prompts.py:19-39):

```
You are an entity-resolution adjudicator for SAP customer master data at Bruker, a scientific-instruments company. Customers are research institutions, universities, hospitals, companies, and their internal departments.

Every record you receive already shares the same physical address (country, postal code, street). Address matching is done. Your only job is to decide, from the names, which records refer to the SAME real-world customer entity.

Identity has TWO levels:
- Name 1 = the institution or company (e.g. "University of Stuttgart", "Siemens AG").
- Name 2 = a department, faculty, institute, or sub-unit within it (may be empty).
An entity is a specific (institution, department) pair.

Rules:
- Same institution AND same department, or both Name 2 empty → SAME entity.
- Same institution but DIFFERENT departments → DIFFERENT entities. Never merge them. Example: "Uni Stuttgart, Dept of Chemistry" and "Uni Stuttgart, Dept of Mechanical Engineering" are two distinct entities.
- Different institutions that happen to share one address (shared campus or building) → DIFFERENT entities.
- A shared ROR ID means same INSTITUTION only. It does not mean same department and never by itself makes two records the same entity — you must still compare Name 2.
- A shared LEI (Legal Entity Identifier) means the records are the same legal entity (typically a company). Treat it like ROR: a strong same-INSTITUTION signal, but it still does not by itself merge records with DIFFERENT Name 2 departments, and you must still compare Name 2. Conversely, DIFFERENT non-empty LEIs are a strong signal of different entities.

Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms ("Dept" = "Department", "Mech Eng" = "Mechanical Engineering"), word reordering, legal-form suffixes (GmbH, AG, Inc., Ltd, e.V.), historical renames or restructures, and spelling variants/typos.

If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome.
```

Evidence grounding across the dedup calls: the prompt scopes the decision to the supplied names — "Your only job is to decide, from the names, which records refer to the SAME real-world customer entity." (dedup/prompts.py:22) — and mandates abstention: "If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome." (dedup/prompts.py:38). No sentence explicitly forbids the use of parametric world knowledge; indeed the instruction to account for translations, renames, and restructures (dedup/prompts.py:36) requires it.

#### A.13 LLM call: dedup Mode A (partition)

One partition call per Name 2 bucket (signatures pre-split by `has_name2` so the empty-vs-populated decision never reaches the LLM; singleton buckets bypass the LLM entirely) (dedup/adjudicator.py:276-298).

User prompt builder (dedup/prompts.py:42-58) — the message is the following literal text with `{listing}` replaced by `json.dumps({"signatures": signatures}, ensure_ascii=False, indent=2)`:

```
Group the following signatures into entities. Return STRICT JSON only, no other text:
{"entities":[{"signature_ids":["s1","s3"],"institution":"<short label>","department":"<short label or empty>","confidence":<0-1>,"reasoning":"<1-2 sentences>"}],"uncertain_signature_ids":["s7"]}
Every input signature_id must appear exactly once, across either entities[].signature_ids or uncertain_signature_ids.

Signatures:
{listing}
```

- Interpolated evidence: per signature — `signature_id`, original (un-normalised) `name1` and `name2`, and `ror_id`/`lei_id` (`"none"` when absent) (dedup/adjudicator.py:300-311; dedup/prompts.py:44-47).
- Expected return: strict JSON — `entities[]` with `signature_ids`, `institution`, `department`, `confidence` (0-1), `reasoning`, plus `uncertain_signature_ids[]` (dedup/prompts.py:52-56).
- Parsing: `parse_json_object(call.raw)` only when `call.error is None` (dedup/adjudicator.py:318).
- Failure: an unusable response marks every signature in the bucket `uncertain` as its own single-signature entity; the block never fails (dedup/adjudicator.py:319-334). Signatures the model omits from its partition are also treated as uncertain (dedup/adjudicator.py:377-386). A post-LLM `_enforce_name2_split` safety net re-enforces the empty-vs-populated constraint (dedup/adjudicator.py:391-393).
- Request parameters: `adjudicate(SYSTEM_PROMPT, user_prompt)` with the default `max_tokens=4000` (dedup/adjudicator.py:314; default at dedup/llm.py:161); no temperature is passed (dedup/llm.py:174-184); `reasoning_effort` from `DEDUP_REASONING_EFFORT` default `"low"` (dedup/llm.py:122); no seed (dedup/llm.py:174-184); deployment `AOAI_DEPLOYMENT_DEDUP` → `AZURE_OPENAI_DEPLOYMENT` → `gpt-5.4` (dedup/llm.py:117-121); concurrency bounded by an asyncio semaphore (dedup/adjudicator.py:313-314).

#### A.14 LLM call: dedup Mode B (incremental assignment)

One call per candidate signature against the compatible canonicals (same `has_name2`); an incompatible candidate starts a new entity with no call (dedup/adjudicator.py:406-427).

User prompt builder (dedup/prompts.py:61-79) — the message is the following literal text with `{payload}` replaced by `json.dumps({"candidate": candidate, "entities": canonicals}, ensure_ascii=False, indent=2)`:

```
Decide whether the candidate signature is the same entity as one of the listed entities, or a new entity. Return STRICT JSON only:
{"decision":"match"|"new"|"uncertain","matched_entity_id":"<id or null>","confidence":<0-1>,"reasoning":"<1-2 sentences>"}

{payload}
```

- Interpolated evidence: candidate `{signature_id, name1, name2, ror_id, lei_id}` and, per compatible canonical entity, `{entity_id, institution, department, name1, name2, ror_id, lei_id}` (institution/department fall back to the seed signature's names; ROR/LEI take the first non-empty value among members) (dedup/adjudicator.py:430-449).
- Expected return: `{decision: match|new|uncertain, matched_entity_id, confidence: 0-1, reasoning}` (dedup/prompts.py:76-77).
- Parsing: `parse_json_object` when `call.error is None` (dedup/adjudicator.py:456).
- Failure: unparseable/errored response → the signature becomes its own entity flagged `uncertain` (dedup/adjudicator.py:457-466). `decision=="match"` to an unknown entity_id is downgraded to "new" with a warning (dedup/adjudicator.py:489-500); any unrecognised decision is treated as uncertain (dedup/adjudicator.py:511-525).
- Request parameters: `adjudicate(SYSTEM_PROMPT, user_prompt, max_tokens=1000)` (dedup/adjudicator.py:452); otherwise identical to Mode A (no temperature, `reasoning_effort` default low, no seed — dedup/llm.py:174-184, 122).

#### A.15 LLM call: dedup residue pass (pairwise adjudication)

Nominates entity pairs the bucketed pass never compared (suffix-stripped Jaro-Winkler name similarity ≥ `NAME_CANDIDATE_THRESHOLD` 0.85, token-set Jaccard ≥ `TOKEN_CANDIDATE_THRESHOLD` 0.6, or ROR/LEI convergence — config.py:101-109, 229-237) and adjudicates each pair with **the Mode B user-prompt builder** presenting exactly one canonical entity (dedup/adjudicator.py:627-636). "Nomination never merges — the LLM decides." (dedup/adjudicator.py:564-570). The system and user prompt text are therefore those of A.12 and A.14; no separate template exists.

- Interpolated evidence: candidate-entity fields `{signature_id, name1, name2, ror_id, lei_id}` from the seed signature of one entity, and a single canonical entry `{entity_id, institution, department, name1, name2, ror_id, lei_id}` from the other (dedup/adjudicator.py:545-553, 627-635).
- Cap: more than `MAX_CANDIDATES_PER_BLOCK` (default 50, config.py:109, 235-237) nominated pairs routes the whole block to manual review without any LLM call (dedup/adjudicator.py:585-601).
- Parsing: `parse_json_object` when `call.error is None` (dedup/adjudicator.py:647).
- Failure: unusable verdict → both sides of the pair marked `uncertain` (manual review) (dedup/adjudicator.py:648-655). `match` unions the pair (union-find, lowest index as stable root; transitively merged pairs are not re-asked) (dedup/adjudicator.py:603-616, 624-625, 662-668); `new`/`distinct` records a distinct-rationale on both sides (dedup/adjudicator.py:669-673); anything else marks both uncertain with reasoning preserved (dedup/adjudicator.py:674-681).
- Request parameters: `adjudicate(SYSTEM_PROMPT, user_prompt, max_tokens=1000)` (dedup/adjudicator.py:638); no temperature, `reasoning_effort` default low, no seed (dedup/llm.py:174-184, 122).

#### A.16 LLM calls: diagnostic probes

Two HTTP diagnostic endpoints issue one-off LLM calls with literal prompts:

- `GET /diag/llm` (api/routes.py:1034-1063) calls `call_openai(system_prompt="Return valid JSON only.", user_prompt='Return {"ok": true}', max_tokens=50)` (api/routes.py:1051-1055). Raw text is returned unparsed in the HTTP body; failures return the exception type and message (api/routes.py:1056-1063). Parameters: Phase-1 transport, temperature 0.0, JSON mode, `max_completion_tokens=50` (llm/openai_client.py:198-207).
- `GET /diag/dedup-llm` (api/routes.py:1066-1102) calls `llm.adjudicate("Return valid JSON only.", 'Return STRICT JSON: {"ok": true}', max_tokens=200)` (api/routes.py:1085-1089) on a real `DedupLLM` and returns raw text, error, model version, token counts, api version, and whether `reasoning_effort` was used (api/routes.py:1090-1100). No temperature, no seed (dedup/llm.py:174-184).

Neither probe interpolates record evidence; both exist to surface the raw Azure error string (api/routes.py:1036-1040, 1068-1073).

---

### K.2 — Non-determinism inventory (sections B.1–B.4)

#### B.1 Verbatim configuration constants

Deployment and Phase-1 defaults (config.py:83-119, quoted):

```python
OPTIONAL_VARS_WITH_DEFAULTS = {
    "AZURE_OPENAI_DEPLOYMENT": "gpt-5.4",
    "ROR_API_BASE": "https://api.ror.org/v2/organizations",
    "ROR_CONFIDENCE_THRESHOLD": "0.8",
    "LEI_LOOKUP_ENABLED": "true",
    "GLEIF_API_BASE": "https://api.gleif.org/api/v1",
    "GLEIF_TIMEOUT_SECONDS": "15",
    "LEI_NAME_MATCH_THRESHOLD": "88",
    "LEI_MAX_RETRIES": "2",
    "FUZZY_MATCH_THRESHOLD": "80",
    "MAX_PAGE_CONTENT_CHARS": "3000",
    "DEFAULT_MAX_CONCURRENCY": "5",
    "CONFIDENCE_MERGE_THRESHOLD": "0.95",
    "NAME_CANDIDATE_THRESHOLD": "0.85",
    "TOKEN_CANDIDATE_THRESHOLD": "0.6",
    "MAX_CANDIDATES_PER_BLOCK": "50",
    "PAGE_FETCH_TIMEOUT_SECONDS": "10",
    "MOCK_EXTERNAL_CALLS": "false",
    "ENV": "production",
    "LOG_LEVEL": "INFO",
    "DEPT_PROBE_CROSS_DOMAIN": "false",
    "WEBSITE_TRACE": "false",
}
```

(config.py:83-119; comment lines elided, entries verbatim.)

⚠ Discrepancy: `OPTIONAL_VARS_WITH_DEFAULTS` lists `"MAX_PAGE_CONTENT_CHARS": "3000"` (config.py:93) but this dict is **never consumed** anywhere in the codebase (its only occurrence is its definition, config.py:83; `validate_env` reads only `REQUIRED_VARS`, config.py:128). The effective default is `int(os.getenv("MAX_PAGE_CONTENT_CHARS", "1500"))` (config.py:208-210) — i.e. 1500 characters of body text per page, applied in `PageFetcher` (search/page_fetcher.py:246-249; wired at enrichment/orchestrator.py:748-751).

Temperature / max-token / retry settings, verbatim from where they are set:

- `temperature=0.0` — every Phase-1 call (llm/openai_client.py:205). Never overridable in practice (the `extract_json` kwarg is not forwarded, llm/openai_client.py:272-275).
- `max_completion_tokens`: 1024 default via `extract_json` (llm/openai_client.py:264); 200 for address residual (enrichment/address_processing.py:679); 4000 default for dedup `adjudicate` (dedup/llm.py:161), 1000 for Mode B and residue (dedup/adjudicator.py:452, 638); 50 / 200 for the diag probes (api/routes.py:1054, 1088).
- Dedup: `self._reasoning_effort = os.getenv("DEDUP_REASONING_EFFORT", "low")` and `self._max_retries = int(os.getenv("DEDUP_MAX_RETRIES", "3"))` (dedup/llm.py:122-123); backoff `delay = 0.5 * (2 ** attempt)` (dedup/llm.py:210).
- GLEIF: `"GLEIF_TIMEOUT_SECONDS": "15"`, `"LEI_MAX_RETRIES": "2"` (config.py:89, 91; consumed via config.py:189-191, 198-200 and enrichment/tier1_lei.py:390-391); backoff `0.5 * (2 ** (attempt - 1))` on transient errors only (enrichment/tier1_lei.py:194-207).
- Seed: no `seed` parameter exists anywhere in the LLM request construction (llm/openai_client.py:198-207; dedup/llm.py:174-184).

#### B.2 Caching semantics (utils/cache.py)

- `SerpCache`: process-level, in-memory, shared across every batch an orchestrator handles; survives for the process lifetime; not persisted to disk; keyed by the lowercased, stripped query string (utils/cache.py:14, 22-45). One instance per `Orchestrator` (enrichment/orchestrator.py:757-760).
- `BatchCache`: created once per enrichment batch (enrichment/orchestrator.py:796) with `shared_serp` fall-through — a per-batch SERP miss consults the process-level cache and promotes hits; writes propagate to both (utils/cache.py:48-105). Also holds a per-batch resolved-host cache (utils/cache.py:62-71).
- ROR: results are cached in a **module-level dict** `_ror_cache` keyed `(name_lower, country_code)` (enrichment/tier1_ror.py:35-36, 566-568) and cleared at the start of every batch ("fresh cache per batch to avoid stale failures", enrichment/orchestrator.py:793) — effectively per-batch scope. ⚠ `BatchCache.get_ror`/`set_ror` (utils/cache.py:75-81) have **no callers** outside `utils/cache.py` itself — the documented BatchCache ROR slot is dead code; the live cache is the module-level one.
- GLEIF/LEI: module-level dict `_lei_cache` keyed `(name_lower, country_code)`, cleared per batch (enrichment/tier1_lei.py:79-86, 232-240; enrichment/orchestrator.py:794).
- LLM responses: **never cached** — no code path stores or replays an LLM response (llm/openai_client.py, dedup/llm.py contain no cache structures).
- Person-affiliation SERP calls bypass the cache entirely: `search_client.search` is called directly with no `BatchCache` involvement (enrichment/person_affiliation.py:122-131).
- Page fetches: no caching layer (search/page_fetcher.py:85-93 executes a fresh request per call).

#### B.3 Inventory table

| Procedure | External call | Source of non-determinism | Caching | Reproducible? |
|---|---|---|---|---|
| UC 0 overflow check | Azure OpenAI chat completion (llm/openai_client.py:198-207) | temperature=0.0 (llm/openai_client.py:205) but no `seed` (llm/openai_client.py:198-207); provider-side sampling/infrastructure variation and deployment/model drift remain | None — LLM responses are never cached | Approximate: greedy decoding, but not bit-reproducible without a seed; deployment upgrades change outputs |
| Person classifier (preprocess) | Azure OpenAI (enrichment/preprocess.py:2321-2324) | same as above | None | Approximate (as above) |
| Person affiliation (Stage 2b) | SerpAPI/DuckDuckGo then Azure OpenAI (enrichment/person_affiliation.py:122-131, 148-150) | SERP ranking volatility feeds the prompt; temperature=0.0, no seed | SERP: **uncached** for this stage (enrichment/person_affiliation.py:122-131); LLM: none | No — prompt content depends on live SERP results |
| Company canonical | Azure OpenAI (enrichment/company_canonical.py:58-60) | temperature=0.0, no seed; answer drawn from parametric memory → model-version drift dominates | None | Approximate; sensitive to deployment version |
| Tier 2 canonical | Azure OpenAI (enrichment/tier2_canonical.py:74-76) | same as company canonical | None | Approximate; sensitive to deployment version |
| Tier 2A affiliation | SERP + page fetch + Azure OpenAI (enrichment/tier2a_contact.py:330-331, 110, 383) | SERP ranking volatility; live web-page content drift; temperature=0.0, no seed | SERP: per-batch `BatchCache` + process-level `SerpCache`, keyed lowercased query (enrichment/tier2a_contact.py:323-331; utils/cache.py:14, 85-105); pages: none | No — depends on live SERP order and page content |
| Tier 2B department | SERP + page fetch + Azure OpenAI (enrichment/tier2b_dept.py:222-228, 266) | same as Tier 2A | SERP cached as above (enrichment/tier2b_dept.py:223-228); pages: none | No |
| Lab resolver (UC 13) | SERP + page fetch + Azure OpenAI (enrichment/lab_resolver.py:79-84, 102, 115-117) | same as Tier 2A; deterministic tie-break among extractions (enrichment/lab_resolver.py:146-157) mitigates but does not remove | SERP cached as above (enrichment/lab_resolver.py:79-84); pages: none | No |
| Website Path C | Azure OpenAI only (enrichment/website_resolver.py:598-600) | temperature=0.0, no seed; URL from parametric memory → model drift | None | Approximate; sensitive to deployment version |
| Address residual | Azure OpenAI (enrichment/address_processing.py:677-680) | temperature=0.0, no seed | None | Approximate |
| Tier 3 inference | Azure OpenAI (enrichment/tier3_llm.py:101) | temperature=0.0, no seed; fully parametric inference → model drift dominates | None | Approximate; sensitive to deployment version |
| Dedup Mode A | Azure OpenAI reasoning deployment (dedup/adjudicator.py:314) | **no temperature parameter passed** (dedup/llm.py:174-184) — service default applies (API-documented default 1; reasoning deployments ignore temperature); `reasoning_effort="low"` default (dedup/llm.py:122); no seed | None | No — unpinned sampling plus reasoning-model variability |
| Dedup Mode B | Azure OpenAI (dedup/adjudicator.py:452) | same as Mode A; additionally order-dependent: entities accrete in input order, so canonical sets differ with record order (dedup/adjudicator.py:416-449) | None | No |
| Dedup residue | Azure OpenAI (dedup/adjudicator.py:638) | same as Mode A; nomination and union-find ordering are deterministic (dedup/adjudicator.py:570, 603-616), verdicts are not | None | No |
| Diag probes (/diag/llm, /diag/dedup-llm) | Azure OpenAI (api/routes.py:1051-1055, 1085-1089) | as per respective transport | None | Approximate / No respectively |
| ROR lookup (Tier 1) | `https://api.ror.org/v2/organizations` via httpx, timeout 15 s (config.py:86, 171-173; enrichment/tier1_ror.py:607-609, 620-622) | registry drift (records added/renamed); server-side affiliation scoring may change; threshold 0.8 (config.py:86, 176-178) | Module-level per-process dict keyed `(name_lower, country_code)`, cleared per batch (enrichment/tier1_ror.py:35-41, 566-568; enrichment/orchestrator.py:793) | Short-term yes, long-term no (registry drift) |
| GLEIF/LEI lookup (Tier 1) | `https://api.gleif.org/api/v1` via httpx, timeout 15 s, ≤2 transient retries with backoff (config.py:88-91; enrichment/tier1_lei.py:214-215, 251-252, 194-207) | registry drift; fulltext `legalName` filter behaviour may change; fuzzy gate `token_sort_ratio ≥ 88` (config.py:91, 195-197) | Module-level per-process dict keyed `(name_lower, country_code)`, cleared per batch (enrichment/tier1_lei.py:79-86, 232-240; enrichment/orchestrator.py:794) | Short-term yes, long-term no |
| SERP — SerpAPI | Google via SerpAPI, sync in thread executor; no explicit timeout or retry; exceptions → `[]` (search/serpapi_client.py:27-36, 38-56) | SERP ranking volatility (result set and order change between invocations); provider selected at runtime by presence of `SERPAPI_KEY` (enrichment/orchestrator.py:770-779) | Per-batch `BatchCache` + process-level `SerpCache`, keyed lowercased stripped query (utils/cache.py:14, 26-45, 85-105) — within one process a repeated query is stable; across runs it is not | No |
| SERP — DuckDuckGo fallback | `duckduckgo_search.DDGS.text`, sync in executor; no timeout/retry; exceptions → `[]` (search/duckduckgo_client.py:19-42) | same as SerpAPI, with lower result quality (config.py:141-145) — and cross-run provider divergence when the key is absent | Same SERP caches | No |
| Page fetch | `requests.get`, timeout `PAGE_FETCH_TIMEOUT_SECONDS` default 10 s, no retry (search/page_fetcher.py:217-223; config.py:110, 211-213; wiring enrichment/orchestrator.py:748-751); HEAD probes with 5 s default (search/page_fetcher.py:95-155) | live web content drift; redirects; blocking (403/404) treated as expected failures (search/page_fetcher.py:28-49); body truncated at `max_chars` (search/page_fetcher.py:246-249) | None | No |

#### B.4 Fixture capture / replay

No capture-and-replay mechanism exists for live external calls. The test suite substitutes hand-curated in-process mocks: `tests/mocks/` contains `openai_mock.py` (keyword-dispatched curated JSON responses, tests/mocks/openai_mock.py:91-125), `serp_mock.py` (curated result tables keyed by domain/institution fragments, tests/mocks/serp_mock.py:15-16), `dedup_mock.py`, `ror_mock.py`, `lei_mock.py`, and `page_mock.py`. The runtime flag `MOCK_EXTERNAL_CALLS` (config.py:111, 240-242) swaps the same mock clients into the live API (api/routes.py:67; enrichment/orchestrator.py:730-743). A repository-wide search for `vcr`, `cassette`, `replay`, `record_mode`, and `betamax` finds nothing: responses from real Azure OpenAI, ROR, GLEIF, SerpAPI/DuckDuckGo, or fetched pages are never recorded to fixtures, so no run against live services can be replayed exactly.


---

Pass 3 complete. 102 decision procedures and 16 LLM call sites documented across Parts A–K. Stop.
