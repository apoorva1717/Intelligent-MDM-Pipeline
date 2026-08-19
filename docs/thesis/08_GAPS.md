Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 8 — Gaps and Limitations

This document consolidates every gap, discrepancy, unimplemented reference, code marker,
fragility, and recorded scope decision surfaced by Passes 0–9. It supersedes the partial running
list opened during Pass 3; entries `G-1` through `G-5` are retained with their original
identifiers and text so that references to them from `01_TRACEABILITY.md`, `03_ALGORITHMS.md`
and `09_DECISIONS.md` continue to resolve.

**Register.** Each entry is a factual statement about the system as it exists at the commit in
the header. No entry is an apology, and no entry attributes intent that the repository does not
record. Where the repository records a decision, the entry says so; where it records only an
outcome, the entry says that instead. §F sets out how each entry lifts into either the thesis
limitations section or the future-work section.

**Evidence.** Every claim carries a `path/file:LINE` citation for the code side and, where the
claim is a discrepancy, a second citation for the documentation side. Findings inherited from an
earlier pass additionally cite that pass document so the derivation is traceable. Claims that
could not be established from the repository carry the `⚠ UNVERIFIED —` prefix; quantities that
are needed but absent carry `⚠ MEASUREMENT REQUIRED` and name the command or query that would
produce them. No source, test, or configuration file was modified in producing this document.

**Scope of the sweep.** The `TODO`/`FIXME`/`HACK` sweep, the exception-handler sweep, the
commented-out-code sweep, and the `# noqa` / `# pragma` inventory in §C and §D were executed
directly against the working tree at this commit and are reported with their own counts. The
discrepancy, unimplemented-component, fixture-coverage and single-point-of-failure inventories
are consolidations of Passes 0–9, re-cited here.

---

## Index

| ID | Section | One line |
|---|---|---|
| G-1 | B | Tier 2A verification mode cannot be entered from any input |
| G-2 | B | Tier 2B department search has no call site or import |
| G-3 | A | `orchestrator.py:2347` describes fallthrough through Tier 2B |
| G-4 | B | Two batch-summary counters cannot increment |
| G-5 | B | Enrichment cannot correct an incorrect existing Name 2 |
| G-6 | A | README cites `enrichment/classifier.py` as the classification module |
| G-7 | A | README text and both diagrams present Tier 2B as an active tier |
| G-8 | A | `tier2b_dept.py` docstring describes a role the module cannot fill |
| G-9 | A | Issue-detection module docstring states stale catalogue counts |
| G-10 | A | UC 14–17 are defined in code and absent from the README use-case table |
| G-11 | A | UC 13 tags two distinct behaviours; UC 1 is undefined everywhere |
| G-12 | A | `MAX_PAGE_CONTENT_CHARS` — three sources, two values |
| G-13 | A | `DEPT_PROBE_CROSS_DOMAIN` — code `false`, `.env.example` `true`, PDF "defaults on" |
| G-14 | A | `DEFAULT_MAX_CONCURRENCY`'s README description does not match its consumption site |
| G-15 | A | The middleware docstring claims JSON logging that neither idiom emits |
| G-16 | A | The README `cluster_id` passage describes the pre-`efe1379` scheme |
| G-17 | A | The dataset oracle expects a code count the detector cannot produce |
| G-18 | A | `expected_outcomes.json` expects `tier2_mode = "2B"` |
| G-19 | A | UC 12 specifies clearing an identical duplicate name; REC-09 retains it |
| G-20 | A | `smart_title_case` is specified to preserve acronyms; `-NIST` became `-nist` |
| G-21 | A | The inferred city/state/zip docstring describes a merge that never runs |
| G-22 | A | `request_id` is documented "for downstream correlation" and nothing reads it |
| G-23 | A | `Website_Trace_Findings.pdf` says "noted, not applied"; README and code say applied |
| G-24 | A | Pass 0 lists `enrichment/confidence.py` as untested rather than as dead |
| G-25 | A | Pass 6b counts fifteen `# noqa` directives; the count in application modules is sixteen |
| G-26 | B | The preprocessing and ZFI-exclusion script (workflow step 1) is not located |
| G-27 | B | Address validation (workflow step 6) exists in no repository and no export |
| G-28 | B | The `/issues` ADF pipeline is not exported and `/issues` has no JSON variant |
| G-29 | B | No stored procedure writing the Issues column back to Legacy is evidenced |
| G-30 | B | Neither exported ADF pipeline invokes `/api/dedup/score` or `/api/dedup/approve` |
| G-31 | B | No group-code predicate exists on any of the three ADF Lookup activities |
| G-32 | B | No `enriched_at` watermark exists; a rerun re-enriches every row |
| G-33 | B | Deduplication is one unbatched Lookup over the whole Validation table |
| G-34 | B | Phase 3 (Salesforce reconciliation) is specified as a contract and implemented nowhere |
| G-35 | B | `enrichment/confidence.py` has no caller anywhere in the repository |
| G-36 | B | `BatchCache`'s ROR store and `stats` property have no callers |
| G-37 | B | `search_terms.unit_domain_or_path` has no application caller |
| G-38 | B | `resolve_website_via_serp`'s `prefetched_results` branch has no caller |
| G-39 | B | `OPTIONAL_VARS_WITH_DEFAULTS` is never read |
| G-40 | B | Two `Settings` fields are consumed only by the `/tiers` response |
| G-41 | B | `extract_json`'s `temperature` argument is accepted and never forwarded |
| G-42 | B | `DedupLLM.adjudicate`'s `max_tokens` default is never used |
| G-43 | B | `PageFetcher`'s default arguments are never reached from the application |
| G-44 | B | ADF `retryIntervalInSeconds` is inert while every activity has `retry: 0` |
| G-45 | B | Two catalogue codes are declared and never emitted |
| G-46 | B | `G2-CONTACT-008` has an emission site that no input can reach |
| G-47 | B | `missing_building_inconsistency` is a declared issue type election never emits |
| G-48 | B | The dedup file route drops the `LEI ID` column for want of a header alias |
| G-49 | B | `DedupRow.enriched_name` is never supplied by the production caller |
| G-50 | B | No `Leading Code` column is written by any code in this repository |
| G-51 | B | The DATAshaper `broken cluster` indicator has no counterpart in the output schema |
| G-52 | B | The evaluation harness's two ground-truth columns exist in no repository workbook |
| G-53 | B | No CI/CD configuration exists on any ref |
| G-54 | B | Dependencies are floors with no lock file; deployment is a manual UI action |
| G-55 | B | `pytest-cov` is declared and not installed, so no coverage figure can be produced |
| G-56 | B | Two JSON fixtures are loadable by `conftest` and consumed by no test |
| G-57 | B | `functionTimeout` is unset, so an unconfirmed platform default bounds every call |
| G-58 | B | `truststore` is installed with no declaring package and no importer |
| G-59 | C | No `TODO`, `FIXME`, `HACK`, `XXX` or `BUG` marker exists in any application module |
| G-60 | C | `enrichment/classifier.py` preserves removed classification logic inside its docstring |
| G-61 | C | `scripts/debug_ucsf.py` carries a 127-line record list disabled by renaming |
| G-62 | C | Four in-code `UNCONFIRMED` markers name scoring parameters awaiting confirmation |
| G-63 | C | Sixteen `# noqa` and six `# pragma: no cover` directives, none enforced by any gate |
| G-64 | D | Hardcoded values that the deployment cannot change without a code change |
| G-65 | D | Exception paths that convert a failure into an ordinary negative result |
| G-66 | D | Thirty-eight procedures documented as having no fixture coverage |
| G-67 | D | Single points of failure in the production topology |
| G-68 | D | Unguarded failure modes in otherwise deterministic components |
| G-69 | E.1 | ZFI records are excluded upstream by instruction, with no recorded rationale |
| G-70 | E.1 | Salesforce reconciliation is defined as a downstream contract, not built here |
| G-71 | E.1 | Approval persistence is explicitly out of scope |
| G-72 | E.1 | `/api/dedup/cluster-block` explicitly excludes four adjacent concerns |
| G-73 | E.1 | The address gate is consumed from DATAshaper rather than computed |
| G-74 | E.1 | Repairs the enrichment contract deliberately does not attempt |
| G-75 | E.1 | Capabilities removed by a recorded decision |
| G-76 | E.2 | Name-2 correction: documented as active, disabled without a recorded reason |
| G-77 | E.2 | Four ADF changes stated as pre-freeze work and absent from both exports |
| G-78 | E.2 | Five scoring parameters flagged in code as awaiting confirmation |
| G-79 | E.2 | Evaluation labelling, coverage measurement, and CI are named and not done |
| G-80 | E.2 | Two reachable issue codes have no covering record in any repository dataset |

---

# A · Discrepancies — code versus documentation

Every code↔documentation conflict found in Passes 0 through 9, with both sides cited. Where the
two disagree, the code is authoritative (`docs/thesis-doc-prompt.md:22-23`); the documentation
side is recorded so the conflict is visible rather than silently resolved.

## G-3 · Stale comment describing fallthrough through Tier 2B

`enrichment/orchestrator.py:2347` reads:

> `# through to existing tier 2 canonical / 2A / 2B / 3`

The `2B` element of that chain has not existed since `635d5ba` (see G-2). The comment describes
a control flow the file no longer has. The full comment block spans
`enrichment/orchestrator.py:2344-2348`.

## G-6 · README cites `enrichment/classifier.py` as the classification module

`README.md:676` names `enrichment/classifier.py` as the file implementing record classification,
and the repository file tree at `README.md:1366` annotates it
`# Record type classification (research_institution vs company)`. The module is a docstring-only
stub of thirteen lines whose first line reads `"""Record classification — REMOVED (Bug 1 fix).`
(`enrichment/classifier.py:1`). No module imports it: a repository-wide search for
`enrichment.classifier` and `.classifier` returns no import (`00_INVENTORY.md:314-319`).
Classification is derived from ROR organisation types in `enrichment/tier1_ror.py` and
`enrichment/orchestrator.py`, as the stub's own body states
(`enrichment/classifier.py:3-13`).

## G-7 · README text and both diagrams present Tier 2B as an active tier

Tier 2B appears in the README as a live pipeline stage at eight sites: the tier table row
"**Tier 2B** | Department web search (SERP + page fetch + LLM) | Medium | Medium-Low | When no
contact or Tier 2A failed" (`README.md:89`); the architecture diagram, where a `Tier 2B / Dept
Search` box feeds SerpAPI, a page fetcher and an LLM (`README.md:146-167`); the routing
description "Both types: Eligible for Tier 1, Tier 2B, and Tier 3" (`README.md:689`); a full
stage section `#### Tier 2B: Department Search` (`README.md:576-608`); the flag table row
"Tier 2B any result | Yes | …" (`README.md:805`); the module reference section
(`README.md:1459`); and the two textual flow diagrams (`README.md:1927`, `:1946`). No call site
exists — see G-2. `09_DECISIONS.md:305-306` records the same conflict from the history side.

## G-8 · `tier2b_dept.py` docstring describes a role the module cannot fill

`enrichment/tier2b_dept.py:1-11` states the module is

> "Used when Tier 2A is not applicable (no contact, or person not found), for companies (which
> skip 2A entirely), or when name2 is already filled and needs normalization against the
> institution's official source."

The module has no call site and no import (G-2), so none of the three cases it names is served
by any web-evidence path. Recorded in Pass 1 (`01_TRACEABILITY.md:261-264`).

## G-9 · Issue-detection module docstring states stale catalogue counts

`enrichment/issue_detection.py:3-4` describes the module as auditing a record "against the
36-code Issue Catalogue", and `:18` states "Coverage: 34 of the 36 catalogue codes are emitted."
The catalogue at `enrichment/issue_detection.py:75-118` declares **37** codes; 35 have an
emission site; because one of those emission sites is unreachable (G-46), **at most 34 distinct
codes can be observed** in detector output. The derivation and the re-verification by executing
the module are in `03_ALGORITHMS.md:74-82` and Part H §§1.1–1.3. Both docstring figures are
stale against the current source.

## G-10 · UC 14–17 are defined in code and absent from the README use-case table

The README "Use Case Reference Table" (`README.md:655-670`) enumerates UC 0 and UC 2–13. The
code defines four further use cases as section headers and `res.note()` tags —
UC 14 slot consolidation, UC 15 c/o + ATTN extraction, UC 16 name/street splitting, UC 17
legal-suffix normalisation — at `enrichment/preprocess.py:612`, `:633`, `:1560` and `:1704`.
All four are implemented and tested (`01_TRACEABILITY.md:86-89`). The requirement list is the
side that is incomplete.

## G-11 · UC 13 tags two distinct behaviours; UC 1 is undefined everywhere

The number 13 tags lab→parent resolution in the README and in
`enrichment/orchestrator.py:2341,2355`, and separately tags "Name 3 residual junk cleanup" in a
comment at `enrichment/preprocess.py:1664`. UC 1 is defined in neither the README table nor the
code; the sequence skips it on both sides (`01_TRACEABILITY.md:249`).

## G-12 · `MAX_PAGE_CONTENT_CHARS` — three sources, two values

| Source | Value | Line |
|---|---|---|
| `OPTIONAL_VARS_WITH_DEFAULTS` | `"3000"` | `config.py:93` |
| `Settings.max_page_content_chars` | `"1500"` | `config.py:209` |
| `PageFetcher.__init__` default argument | `1500` | `search/page_fetcher.py:69` |
| README environment table | `3000` | `README.md:1622` |
| `.env.example` | `3000` | `.env.example:81` |

The effective value with the variable unset is **1500**: `Settings.max_page_content_chars`
(`config.py:208-210`) is the only source the orchestrator reads
(`enrichment/orchestrator.py:741,750`). `OPTIONAL_VARS_WITH_DEFAULTS` is never read (G-39).
Commit `b19cd1a` changed the `Settings` field from 3000 to 1500 "for better performance" and did
not change the other three sources. A deployment that copies `.env.example` verbatim sets 3000
in the environment, and that wins over both defaults — a cost-bearing divergence, since the
value bounds the body text sent to the LLM on every page fetch
(`04_PARAMETERS.md:266-287`; `06b_CROSSCUTTING.md:1276-1278`).

## G-13 · `DEPT_PROBE_CROSS_DOMAIN` — code `false`, `.env.example` `true`, PDF "defaults on"

| Source | Value | Line |
|---|---|---|
| `OPTIONAL_VARS_WITH_DEFAULTS` | `"false"` | `config.py:114` |
| `Settings.dept_probe_cross_domain` | `default=False` | `config.py:166-168` |
| README environment table | `false` | `README.md:1630` |
| `.env.example` | `DEPT_PROBE_CROSS_DOMAIN=true`, commented "when true (default)" | `.env.example:61` |
| `Domain_DeptDomain_SearchTerm_Logic.pdf` | "defaults on" | via `02_ARCHITECTURE.md:508-510` |

The sole consumer is `enrichment/orchestrator.py:1277`; unset, the effective value is **false**.
A deployment copying `.env.example` enables the unrestricted cross-domain stage-3 SERP call,
doubling SERP calls for every unresolved department relative to the documented default
(`04_PARAMETERS.md:289-305`; `06b_CROSSCUTTING.md:1279-1281`). The commit that flipped the
default (`515cc7c`) states the flip; `.env.example` and the PDF were not updated.

## G-14 · `DEFAULT_MAX_CONCURRENCY`'s README description does not match its consumption site

`README.md:1624` describes `DEFAULT_MAX_CONCURRENCY` as the "Default concurrent record
processing limit". `Settings.default_max_concurrency` (`config.py:216-218`) is read only by the
`/tiers` response (`api/routes.py:1115`). The semaphore that actually bounds concurrency reads
`options.max_concurrency` from the request (`enrichment/orchestrator.py:797`), whose default is
the independent literal `5` at `api/models.py:289` and `api/routes.py:521`. Changing the
environment variable changes what `/tiers` reports and nothing about how many records run
concurrently (`04_PARAMETERS.md:214,566`).

## G-15 · The middleware docstring claims JSON logging that neither idiom emits

`api/middleware.py:1` reads `"""FastAPI middleware for structured JSON logging, request timing,
and error handling."""`. The formatter installed at `api/middleware.py:87-91` renders no
`extra=` key, and neither of the two logging idioms in the codebase emits JSON to the console or
file sinks; every structured field on `request_complete`, `dedup_llm_call`, `dedup_block`,
`dedup_request` and `scoring_request` — including all token counts and all latencies — is absent
from both (`06b_CROSSCUTTING.md:1255-1260`, §b.3).

## G-16 · The README `cluster_id` passage describes the pre-`efe1379` scheme

The README passage on cluster identifiers describes the sequential-integer scheme that
`efe1379` replaced with a content hash over sorted member ids
(`dedup/models.py:68-71`; `dedup/cluster_key.py:13`). `09_DECISIONS.md:1051-1052` records the
passage as stale and quotes it as the clearest surviving statement of the superseded design.

## G-17 · The dataset oracle expects a code count the detector cannot produce

`PresentationTestData.xlsx` sheet `Issue_Counts` is titled "all 36 codes" and `Oracle_Summary`
claims `Distinct issue codes covered: 36/36` (`07_EVALUATION.md:550-553`). The catalogue in code
declares 37 codes with at most 34 observable (G-9). The oracle's expectation is not satisfiable
by the implemented detector, independent of the data in the sheet.

## G-18 · `expected_outcomes.json` expects `tier2_mode = "2B"`

`tests/fixtures/expected_outcomes.json` rows 20–46 carry `"expected_tier2_mode": "2B"` for
records `BSP_1000003`–`BSP_1000005`. No pipeline path can produce that value (G-2, G-4). The
fixture is stale relative to the pipeline and, separately, is consumed by no test — only the
`conftest` loader references it (`tests/conftest.py:89-103`; `03_ALGORITHMS.md:1528`). The
staleness is therefore invisible to the suite.

## G-19 · UC 12 specifies clearing an identical duplicate name; REC-09 retains it

UC 12 specifies silently clearing an identical duplicate name field
(`01_TRACEABILITY.md:84`; implementing loop documented in `03_ALGORITHMS.md` Part A). Record
REC-09 (`PresentationTestData.xlsx` sheet row 87) carries `Tropical Pharma Inc` in both Name 1
and Name 2, and its post-pipeline counterpart in
`PresentationTestData_enriched_checked_v1.xlsx` still carries the duplicate; the detector still
raises `G3-NAME-005` on the enriched row (`03b_EXEMPLARS.md:303-325`).

⚠ MEASUREMENT REQUIRED — the enriched workbook records values, not execution, so it cannot
distinguish "the workbook predates the UC 12 implementation" from "the rule did not fire on this
input". Re-running `POST /enrich` on that single row settles it.

## G-20 · `smart_title_case` is specified to preserve acronyms; `-NIST` became `-nist`

`smart_title_case` is specified to preserve acronyms when title-casing an ALL-CAPS name
(`utils/text_utils.py:285-310`; `03_ALGORITHMS.md` Part A). In record REC-15
(`PresentationTestData.xlsx` sheet row 22) the input
`NATIONAL INSTITUTE OF STANDARDS AND TECHNOLOGY-NIST` appears post-pipeline as
`National Institute of Standards and Technology-nist`: the hyphen-attached acronym segment was
lower-cased (`03b_EXEMPLARS.md:470-476`).

⚠ MEASUREMENT REQUIRED — as with G-19, confirmation requires re-running `POST /enrich` on that
row rather than reading the workbook.

## G-21 · The inferred city/state/zip docstring describes a merge that never runs

`enrichment/address_processing.py:105-106` states that the inferred city, state and zip values
are those the orchestrator "only populates the record's empty slots" with. The three fields are
computed at `enrichment/address_processing.py:970-977` and are never read by the merge
(`:1169-1219`). The `City`, `Region` and `Postal Code` outputs are therefore always the verbatim
input values (`05_DATA_MODEL.md:591`, `:1135-1138`).

## G-22 · `request_id` is documented "for downstream correlation" and nothing reads it

`RequestLoggingMiddleware` sets `request.state.request_id` with the comment "for downstream
correlation" (`api/middleware.py:26`). No code reads it. The identifier appears in three
middleware log lines and one `X-Request-ID` response header (`api/middleware.py:58`) and in no
per-record or per-block log line (`06b_CROSSCUTTING.md:1249-1252`, §b.4.3).

## G-23 · `Website_Trace_Findings.pdf` says "noted, not applied"; README and code say applied

The `WEBSITE_TRACE` diagnostic report records four retrieval hypotheses as "noted, not applied";
the README and the code in the same commit apply two of them
(`09_DECISIONS.md` D-15, `:601-642`, `:1496`). Which of the four were lifted, and by whom, is
recorded on the code side only.

## G-24 · Pass 0 lists `enrichment/confidence.py` as untested rather than as dead

`00_INVENTORY.md:415` lists `enrichment/confidence.py` among untested modules; Pass 3 and Pass 5
establish that neither `determine_enrichment_status` nor `should_flag_for_review` has a caller
anywhere in the repository (`03_ALGORITHMS.md:1608-1615`; `05_DATA_MODEL.md:1139-1141`). The
module belongs in the dead-code list of `00_INVENTORY.md:310-330`, not the untested list. See
G-35. This is a documentation-internal discrepancy between pass documents, recorded so the
Pass 0 statement is not read as a coverage finding.

## G-25 · Pass 6b counts fifteen `# noqa` directives; the count in application modules is sixteen

`06b_CROSSCUTTING.md:1238` states "Fifteen `# noqa` directives". A sweep at this commit counts
**16** in application modules — `api/routes.py` 5, `dedup/llm.py` 3,
`enrichment/orchestrator.py` 3, `llm/openai_client.py` 2, `dedup/scoring_xlsx.py` 1,
`enrichment/address_processing.py` 1, `main.py` 1 — plus 7 in `scripts/` and 9 in `tests/`, for
32 repository-wide. The difference is `main.py:3` (`from api.app import app  # noqa: F401`). The
substantive finding in both documents is unchanged: none of these is enforced by any gate
(G-63).

---

# B · Components referenced but not implemented

For each: the reference and the absence, both cited.

## B.1 · Name-2 correction — the two unreachable procedures

### G-1 · Tier 2A verification mode cannot be entered from any input

`run_tier2a` implements two modes, selected by
`mode = "2A_population" if is_blank(name2) else "2A_verification"`
(`enrichment/tier2a_contact.py:80`). The orchestrator gate admits a record only when its
preprocessed Name 2 is blank — `not name2_already_filled`, where
`name2_already_filled = bool(pp_name2 and pp_name2.strip())`
(`enrichment/orchestrator.py:2451-2457`, `:2248`) — and then passes that same value as
`name2=pp_name2` (`:2473`). The gate requires the value blank; the selector requires it
populated. The set of qualifying inputs is therefore empty by construction, not merely
unobserved in testing. The second invocation site passes `name2=None` explicitly
(`enrichment/orchestrator.py:1488-1502`).

`tests/test_tier2a_verification.py` exercises Mode B by calling `run_tier2a` directly, so the
suite passes and the mode appears covered. Test coverage of a function does not establish that
any pipeline path reaches it.

Entering Mode B requires a source change — admitting records with a populated Name 2 into the
gate — not a different input record.

### G-2 · Tier 2B department search has no call site or import

`enrichment/tier2b_dept.py` (266 lines) implements a complete department-resolution procedure:
record-type-aware SERP query construction (`:176-209`), on-domain result ranking (`:212-240`),
top-3 page fetch with deterministic ranking of the extractions (`:83-131`), and LLM extraction
from structured page elements only (`:247-266`). It is never invoked. The orchestrator's import
block imports `run_tier2_canonical`, `run_tier2a` and `run_tier3` but no `tier2b_dept` symbol
(`enrichment/orchestrator.py:37-59`); a repository-wide search finds `run_tier2b` only in its own
module (`enrichment/tier2b_dept.py:50`), its tests (`tests/test_tier2b.py:13,36,58,78,97`), and
documentation. Where Tier 2B would run, control falls from the Tier 2A block directly to Tier 3
(`enrichment/orchestrator.py:2536-2555`).

The module was wired into the orchestrator in the initial commit (`f77080b`) and unwired in
`635d5ba` two days later; the commit message does not mention the removal
(`09_DECISIONS.md` D-1).

### G-4 · Two batch-summary counters cannot increment

`enrichment/orchestrator.py:2636-2641` accumulates per-record outcomes into the batch summary:

```python
elif r.tier_used == 2:
    if r.tier2_mode == "2A_population":
        summary.tier2a_population_count += 1
    elif r.tier2_mode == "2A_verification":
        summary.tier2a_verification_count += 1
    elif r.tier2_mode == "2B":
        summary.tier2b_count += 1
```

`tier2_mode` is assigned in exactly one place, `_apply_tier2a`, as
`result["tier2_mode"] = tier2a.mode` (`enrichment/orchestrator.py:670`). Because Mode B is
unreachable (G-1), `tier2a.mode` is always `"2A_population"`; because Tier 2B is never called
(G-2), nothing assigns `"2B"`. `tier2a_verification_count` and `tier2b_count` are therefore
always zero in every response.

Note for evaluation: a reader of the batch summary cannot distinguish "no record needed
verification" from "verification cannot run". Any metric derived from these counters is
uninformative rather than merely zero.

### G-5 · Enrichment cannot correct an incorrect existing Name 2

Both procedures that would compare a populated Name 2 against retrieved evidence are unreachable
(G-1, G-2). The pipeline fills blank Name 2 values only.

Three outputs are defined in the source and can never be produced:

| Value | Defined at | Condition that would produce it |
|---|---|---|
| `enrichment_status = "verified"` | `enrichment/tier2a_contact.py:459` | Mode B match score ≥ 95 |
| `source = "contact_lookup_corrected"` | `enrichment/tier2a_contact.py:479` | Mode B match score below `settings.fuzzy_match_threshold` (default `80`, `config.py:203-205`) |
| Name 2 correction branch | `enrichment/tier2a_contact.py:470-479` | As above — replaces the record's Name 2 with the contact page's department and flags "Name 2 corrected — did not match contact page affiliation" |

⚠ Scope note, stated so it is not overread: an existing Name 2 **is** still normalised to
official wording by `run_tier2_canonical` (`enrichment/orchestrator.py:2384`, reached for a
populated field at `:2367-2374`). That path works from the name alone via an LLM. What is absent
is verification or correction against *retrieved web evidence* — the contact's page or the
institution's site. A Name 2 that is well-formed but factually wrong for the record will be
tidied, not caught.

⚠ MEASUREMENT REQUIRED — the share of input records carrying a populated Name 2, which bounds
how much of the corpus this gap applies to, is not in the repository. It is obtainable by
counting non-blank `Name 2` cells in the source workbook, or with
`SELECT COUNT(*) FROM test_77.Legacy WHERE LTRIM(RTRIM([Name 2])) <> ''`.

## B.2 · Workflow components referenced in the design and absent from every artefact

The twelve-step production workflow is tabulated at `CONTEXT-EXTERNAL.md:416-429` and
`02_ARCHITECTURE.md:168-181`. Four of its steps have no artefact in this or any exported
repository.

### G-26 · The preprocessing and ZFI-exclusion script (workflow step 1) is not located

Step 1 — "Preprocess source file to processable schema; exclude ZFI records" — is attributed to
a script (`CONTEXT-EXTERNAL.md:418`). The artefact is not located
(`CONTEXT-EXTERNAL.md:444` open item 4; `02_ARCHITECTURE.md:170`). No code in this repository
performs it, and the input contract of `/enrich` therefore begins downstream of an unversioned
transformation.

### G-27 · Address validation (workflow step 6) exists in no repository and no export

Step 6 — "Address validation; auto write-back above 80% confidence" — has no exported ADF
pipeline (`CONTEXT-EXTERNAL.md:423,442`) and no code path in the service. The `80%` threshold is
[AUTHOR]-stated only; the identity of the validating service, the comparison operator, and the
value itself are unevidenced by any artefact (`04_PARAMETERS.md:259`, `:579-582`;
`06_EXTERNAL_DEPS.md:608-637`). The only `0.8` literals in the codebase belong to
`ROR_CONFIDENCE_THRESHOLD` (`config.py:86,177`; `enrichment/tier1_ror.py:573`).

### G-28 · The `/issues` ADF pipeline is not exported and `/issues` has no JSON variant

Step 7 — "Call `/issues`; write issues column back to Legacy" — has no exported ADF pipeline
(`CONTEXT-EXTERNAL.md:424,443`). Separately, the only `/issues` endpoint the service exposes
consumes a multipart XLSX upload (`detect_file_issues`, `api/routes.py:580-581`), not JSON, so
how an ADF Web activity would invoke it is unverified
(`02_ARCHITECTURE.md:133-137`; `05_DATA_MODEL.md:984`). The two facts are consistent with one
another: the step as designed has no callable JSON contract.

### G-29 · No stored procedure writing the Issues column back to Legacy is evidenced

The Issues column is an external integration contract consumed by the DATAshaper validation step
(`02_ARCHITECTURE.md:295-337`). No stored procedure writing it is evidenced anywhere
(`CONTEXT-EXTERNAL.md:441` open item 1; `05_DATA_MODEL.md:494-495`, `:918`), unlike the two
merge-back procedures `usp_merge_legacy_enriched` and `usp_merge_validation_clusters`, which are
at least named by the exports.

### G-30 · Neither exported ADF pipeline invokes `/api/dedup/score` or `/api/dedup/approve`

The deduplication pipeline's only Web activity targets `/api/dedup/cluster-block`
(`CONTEXT-EXTERNAL.md:253-255`) and the enrichment pipeline's targets `/enrich`
(`CONTEXT-EXTERNAL.md:135`). Golden-record election (workflow step 12) is therefore not
triggered by either exported pipeline (`02_ARCHITECTURE.md:374-378`). Whether it runs from a
further pipeline, a DATAshaper process, or a manual call is `CONTEXT-EXTERNAL.md:445` open
item 5.

### G-31 · No group-code predicate exists on any of the three ADF Lookup activities

A group code identifies one import within the entity, and the `Legacy` and `Validation` tables
hold records from all group codes under the entity (`CONTEXT-EXTERNAL.md:20-29`), which makes
group code the required scoping predicate for per-import processing. As exported
(`lastPublishTime` 2026-07-29T12:09:37Z), the enrichment Lookups read `test_77.Legacy`
unfiltered (`CONTEXT-EXTERNAL.md:64`, `:106`) and the deduplication Lookup reads
`test_77.Validation` unfiltered (`CONTEXT-EXTERNAL.md:226`). A run therefore spans all imports
under entity `test_77` rather than the intended one
(`02_ARCHITECTURE.md:45-53`; `06b_CROSSCUTTING.md:1313-1315`).

### G-32 · No `enriched_at` watermark exists; a rerun re-enriches every row

The enrichment `Lookup1` selects rows by offset with no enrichment watermark
(`CONTEXT-EXTERNAL.md:106`). Because every activity has `retry: 0`
(`CONTEXT-EXTERNAL.md:54,96,126,154`) and `ForEach1` is sequential
(`CONTEXT-EXTERNAL.md:88`), a failure at batch *N* leaves batches 1…*N*−1 committed to `Legacy`
and stops the loop; a rerun re-selects and re-enriches those already-merged rows, repeating
their LLM and SERP spend, and the non-deterministic tiers may return different answers on the
second pass (`02_ARCHITECTURE.md:218-231`, `:423`; `06b_CROSSCUTTING.md:1282-1284`). The
enrichment merge-back is consequently not idempotent as exported.

### G-33 · Deduplication is one unbatched Lookup over the whole Validation table

The deduplication pipeline issues a single Lookup over the entire `test_77.Validation` table with
`firstRowOnly: false` and no batching (`CONTEXT-EXTERNAL.md:224-236`), followed by one `Web1`
call carrying all rows (`:253-264`). ADF bounds a Lookup activity to 5,000 rows and 4 MB of
output, so the activity truncates or fails once Validation grows past that
(`02_ARCHITECTURE.md:489`; `04_PARAMETERS.md:251`).

⚠ MEASUREMENT REQUIRED — the Validation row count is not in the repository;
`SELECT COUNT(*) FROM test_77.Validation` locates the pipeline against the cap.

### G-34 · Phase 3 (Salesforce reconciliation) is specified as a contract and implemented nowhere

The repository specifies the contract Phase 3 consumes — "consume ONLY rows with
`approval_status == "approved"` or `election_status == "unique"`"
(`dedup/scoring.py:266-268`; restated `api/routes.py:954-955` and `README.md:1103`) — and
describes what Phase 3 does with it: "Phase 3 Case A matches Salesforce records against golden
rows; a unique SAP customer is a valid match target" (`dedup/scoring.py:1043-1045`). No
Salesforce matching code exists in this repository; the eight `SF_ID_*` slots are read only as a
scoring input (`dedup/scoring.py:73-77,176-186`; `dedup/scoring_xlsx.py:55-58`). See G-70 for
the scope framing.

## B.3 · Code declared and never reached

Recorded because a thesis reproduction would otherwise assume these take effect.

### G-35 · `enrichment/confidence.py` has no caller anywhere in the repository

Neither `determine_enrichment_status` (`enrichment/confidence.py:40`) nor
`should_flag_for_review` (`:8`) is imported or called; a full-repository pattern search matches
only the two definition sites (`03_ALGORITHMS.md:1608-1615`; `05_DATA_MODEL.md:640`,
`:1139-1141`). The `flag_for_review` and `enrichment_status` values that reach the output are
set inline at the tier call sites. No test imports either function
(`03_ALGORITHMS.md:1619`).

### G-36 · `BatchCache`'s ROR store and `stats` property have no callers

`BatchCache.get_ror` / `set_ror` (`utils/cache.py:75-81`) are called from nowhere outside
`utils/cache.py` itself; the operative ROR cache is the module-level `_ror_cache` in
`enrichment/tier1_ror.py:35-36`, cleared at batch start
(`enrichment/orchestrator.py:793`). `BatchCache.stats` (`utils/cache.py:109-111`) is never
called, so cache effectiveness — the main determinant of SERP spend — is unmeasured
(`03_ALGORITHMS.md:2485`, `:7193`; `06_EXTERNAL_DEPS.md:128`;
`06b_CROSSCUTTING.md:1263-1264`).

### G-37 · `search_terms.unit_domain_or_path` has no application caller

The function is defined at `enrichment/search_terms.py:256` and exercised by
`tests/test_search_terms.py:106-130`; no application module calls it. It is superseded by
`_dept_domain_to_search_term` (`00_INVENTORY.md:320-322`;
`03_ALGORITHMS.md:4312`).

### G-38 · `resolve_website_via_serp`'s `prefetched_results` branch has no caller

The branch at `enrichment/website_resolver.py:472-484` reuses search results a caller has
already obtained, and its docstring describes the case as "the orchestrator already ran a
Tier 2B search" (`:459-461`). It has a unit test (`tests/test_website_resolver.py:268-283`) and
no caller in `enrichment/orchestrator.py` passes `prefetched_results`
(`03_ALGORITHMS.md:3338`). The branch is a residue of the Tier 2B wiring removed in `635d5ba`
(G-2).

### G-39 · `OPTIONAL_VARS_WITH_DEFAULTS` is never read

The dictionary at `config.py:83-119` documents defaults for optional environment variables.
Nothing reads it: its only occurrence is its definition, and `validate_env` reads only
`REQUIRED_VARS` and `SERPAPI_KEY` (`config.py:128,137`). Every default actually applied comes
from a `Settings` field's own `os.getenv(..., "…")` call, which is how the
`MAX_PAGE_CONTENT_CHARS` divergence in G-12 arises (`04_PARAMETERS.md:565`;
`03_ALGORITHMS.md:7179`).

### G-40 · Two `Settings` fields are consumed only by the `/tiers` response

`Settings.default_max_concurrency` (`config.py:216-218`) and
`Settings.ror_confidence_threshold` (`config.py:176-178`) are read only at
`api/routes.py:1115` and `:1111` respectively. The ROR matching decision reads the environment
variable directly at `enrichment/tier1_ror.py:573`, and the semaphore reads the request option
(G-14). Both currently agree with the value that is used, because both read the same variable
with the same literal default; a future change to one default alone would make `/tiers` report a
threshold the matcher does not apply (`04_PARAMETERS.md:307-320`, `:566-567`).

### G-41 · `extract_json`'s `temperature` argument is accepted and never forwarded

`OpenAIClient.extract_json` declares `temperature: float = 0.0`
(`llm/openai_client.py:262`) and calls `call_openai(system_prompt, user_prompt,
max_tokens=max_tokens, client=client)` without it (`:272-275`); `call_openai` hardcodes
`temperature=0.0` in the request body (`:205`). Every Phase-1 call runs at temperature 0.0
regardless of what a caller passes (`03_ALGORITHMS.md:6589`; `06_EXTERNAL_DEPS.md:390`;
`04_PARAMETERS.md:564`).

### G-42 · `DedupLLM.adjudicate`'s `max_tokens` default is never used

The declared default is `4000` (`dedup/llm.py:161`); both application call sites pass `1000`
(`dedup/adjudicator.py:452`, `:638`). The `4000` default is reached only by test doubles
(`tests/test_dedup.py:54,750`) (`04_PARAMETERS.md:333-338`).

### G-43 · `PageFetcher`'s default arguments are never reached from the application

`PageFetcher.__init__` declares `timeout=10, max_chars=1500`
(`search/page_fetcher.py:69`); the orchestrator always constructs it with both values passed
explicitly from `Settings` (`enrichment/orchestrator.py:739-742,748-751`). The defaults are
reached only by tests (`tests/mocks/page_mock.py:80`) (`04_PARAMETERS.md:569`).

### G-44 · ADF `retryIntervalInSeconds` is inert while every activity has `retry: 0`

All seven activities across both pipelines set `retryIntervalInSeconds: 30` and `retry: 0`
(`CONTEXT-EXTERNAL.md:54-57,96-99,126-129,154-157,216-219,246-249,274-277`). There is no retry
to space out (`04_PARAMETERS.md:246`, `:570`).

## B.4 · Values declared in a schema and never produced

### G-45 · Two catalogue codes are declared and never emitted

`G1-ADDR-009` "Unclassified Residual in Address" (`enrichment/issue_detection.py:88`) and
`G4-ADDR-025` "Sub-location Overflow Beyond Street 5" (`:112`) are both annotated
`# LLM-only — never emitted` at their catalogue entries, and the module docstring states the
reason: they "genuinely require the pipeline's LLM residual classifier and cannot be decided
deterministically from raw input" (`enrichment/issue_detection.py:18-24`). They are therefore
never present in the Issues column (`02_ARCHITECTURE.md:335-337`;
`01_TRACEABILITY.md:145-147`). This is a recorded decision — see G-74 — but it is also a
declared-and-absent value, and both readings matter for the traceability table.

### G-46 · `G2-CONTACT-008` has an emission site that no input can reach

`G2-CONTACT-008` "No Contact and No Department" is emitted at
`enrichment/issue_detection.py:367`, under a gate that also adds `G2-NAME-012` under the
identical condition, so its own guard can never pass. The unreachability proof is
`03_ALGORITHMS.md` Part H §1.3 (`:5042-5047`), and it is demonstrated concretely on fixture
`tests/fixtures/research_missing_name2_with_contact.json` at
`03b_EXEMPLARS.md:252-260`. Unlike G-45 this is not annotated in the source as intentional.

### G-47 · `missing_building_inconsistency` is a declared issue type election never emits

`ISSUE_TYPES` at `dedup/scoring.py:403-412` declares `missing_building_inconsistency`; the
comment above it states it "is reserved for the upstream building differentiator (Phase 1); it
is a declared type here but not emitted from election (no building signal at this stage)"
(`dedup/scoring.py:399-402`) (`05_DATA_MODEL.md:1159-1161`).

### G-48 · The dedup file route drops the `LEI ID` column for want of a header alias

`_DEDUP_HEADER_ALIASES` (`api/routes.py:688-707`) contains no `leiid` key, so an XLSX column
headed `LEI` or `LEI ID` — which `/enrich/file` produces — is dropped by `_rows_to_dedup_rows`
(`api/routes.py:722-726`). The JSON endpoint accepts `lei_id` (`dedup/models.py:45`), so LEI
identity hints reach the adjudicator on one route and not the other
(`03_ALGORITHMS.md:5599`; `05_DATA_MODEL.md:441`, `:1142-1144`).

### G-49 · `DedupRow.enriched_name` is never supplied by the production caller

The field is accepted by the model (`dedup/models.py:46`) and is absent from the ADF Validation
projection that constitutes the production request (`CONTEXT-EXTERNAL.md:226`)
(`05_DATA_MODEL.md:891`, `:1145-1146`).

### G-50 · No `Leading Code` column is written by any code in this repository

The DATAshaper deduplication view presents a `Leading Code` and an `Apply Leading Code` action
(`CONTEXT-EXTERNAL.md:395-399`). No column of that name is written anywhere in this repository;
the service's equivalents are `proposed_golden_id` (the machine proposal) and, after approval,
`golden_record_id` (`dedup/scoring.py:1181`, `:1193`, `:598-600`)
(`05_DATA_MODEL.md:714`, `:1150-1151`).

### G-51 · The DATAshaper `broken cluster` indicator has no counterpart in the output schema

The DS deduplication view carries a `broken cluster` indicator
(`Datashaper-Tutorial-Part3.txt:128-131`); no field of the service's output schema corresponds
to it (`05_DATA_MODEL.md:542`, `:1152-1153`).

## B.5 · Evaluation and engineering infrastructure referenced and absent

### G-52 · The evaluation harness's two ground-truth columns exist in no repository workbook

`eval/dedup_eval.py` computes precision, recall and F1 from `expected_cluster` and
`expected_routing` columns (`eval/dedup_eval.py:150-166`). Neither column exists in any workbook
in the repository, verified against the header rows (`07_EVALUATION.md:441-442`, `:531-532`).
Executed at this commit,
`.venv\Scripts\python.exe -m eval.dedup_eval PresentationTestData.xlsx --out <scratch>\eval_report.json`
exits 0, reports `rows evaluated: 500`, and returns `precision 0.00 recall 0.00 F1 0.00`,
`TP 0 FP 0 FN 0 (GT pairs 0, predicted 0)` — the zero-guard behaviour of `:171-177`, not a
measurement (`07_EVALUATION.md:439-456`). The cluster-level expectations that do exist are in the
`Dedup_Scoring_Oracle` sheet in a different shape and would have to be projected onto per-row
columns.

⚠ MEASUREMENT REQUIRED — producing any non-trivial value of the dedup metrics requires first
authoring the two columns, which is a labelling task rather than a command.

### G-53 · No CI/CD configuration exists on any ref

No workflow, pipeline, or automation artefact of any kind exists in the repository; nothing runs
on push or on pull request (`06b_CROSSCUTTING.md:1234-1235`, §a.1). In consequence the suite's
state is ungated: it is red at `HEAD` — `3 failed, 1019 passed, 12 warnings in 28.44s`, all three
failures in `tests/test_orchestrator.py` (`00_INVENTORY.md:336-343`) — and no gate consumes that
result (`06b_CROSSCUTTING.md:1236-1237`). The sixteen `# noqa` directives suppress a linter the
repository does not configure and no gate runs (G-63).

### G-54 · Dependencies are floors with no lock file; deployment is a manual UI action

All 14 runtime dependencies are declared as `>=` floors (`requirements.txt:1-14`) with no lock
file, and the build is performed remotely at deploy time (`.vscode/settings.json:3`), so two
deployments of the same commit can install different library versions
(`06b_CROSSCUTTING.md:1240-1243`; `06_EXTERNAL_DEPS.md:779`). Deployment is a manual VS Code UI
action with no scripted artefact, no staging slot, and no rollback procedure
(`06b_CROSSCUTTING.md:1244-1245`, §a.5). Three of the five deployed components — ADF,
DATAshaper, and the stored procedures — have no deployment artefact in any repository, so a
contract change cannot be released atomically (`06b_CROSSCUTTING.md:1246-1248`).

### G-55 · `pytest-cov` is declared and not installed, so no coverage figure can be produced

`requirements-dev.txt` declares `pytest-cov>=5.0.0`; the package is absent from
`.venv/Lib/site-packages` (`06_EXTERNAL_DEPS.md:825`). The coverage measurement recommended at
`00_INVENTORY.md:424-425` cannot run in this environment without installing it first, and no
coverage figure is committed (`htmlcov/` and `.coverage` are git-ignored, `.gitignore:17-18`).

⚠ MEASUREMENT REQUIRED — `pytest --cov=. --cov-report=term-missing`, after installing
`pytest-cov`.

### G-56 · Two JSON fixtures are loadable by `conftest` and consumed by no test

`tests/conftest.py:82-103` defines `load_fixture`, `load_expected_outcomes` and the
`expected_outcomes` pytest fixture. No test file references `expected_outcomes` or
`mixed_batch_10_records`; a repository-wide search over `tests/` matches only the `conftest`
definitions (`03_ALGORITHMS.md:1378`, `:1619`, `:1652`). Both fixtures encode expectations —
including the stale `"2B"` expectation of G-18 — that nothing asserts.

### G-57 · `functionTimeout` is unset, so an unconfirmed platform default bounds every call

`host.json:1-20` sets no `functionTimeout`, so the platform default for the hosting plan applies.
The hosting plan is `CONTEXT-EXTERNAL.md:446` open item 6 and is not evidenced in the repository
(`06b_CROSSCUTTING.md:170`). The ADF `Web1` activity's own 12-hour timeout
(`CONTEXT-EXTERNAL.md:126`) far exceeds any Functions ceiling, so it is the Functions plan
ceiling — not the ADF timeout — that bounds a single `/enrich` call
(`02_ARCHITECTURE.md:279-284`; `04_PARAMETERS.md:234`).

⚠ The ceiling must not be stated until the plan is confirmed from the Function App resource
blade.

### G-58 · `truststore` is installed with no declaring package and no importer

`truststore 0.10.4` is present in the environment; no reverse dependency was found among the
`Requires-Dist` metadata of the other 46 installed distributions, and no repository module
imports it (`06_EXTERNAL_DEPS.md:863`).

---

# C · Code markers

`TODO`, `FIXME`, `HACK`, and commented-out blocks, quoted with location. The sweep covered
`*.py`, `*.md`, `*.json`, `*.ini`, `*.txt` and `.env.example` across the working tree, excluding
`.venv/`, `__pycache__/` and `docs/thesis/`.

## G-59 · No `TODO`, `FIXME`, `HACK`, `XXX` or `BUG` marker exists in any application module

The sweep returns three matches repository-wide and none is a code marker:

- `docs/thesis-doc-prompt.md:193` — the pass specification's own instruction to look for them.
- `tests/test_scoring.py:131` — the string literal `("XXXX", 0)`, a test input for an
  unrecognised account-group value.
- `README.md:1101` — `"Persistence is out of scope for now."`, the single deferral marker in the
  documentation; the corresponding code comment at `api/routes.py:953-954` reads "Persistence is
  intentionally out of scope — a durable approval store is a future step" (see G-71).

The absence is itself a finding: deferred work in this repository is recorded in prose in the
README and in module docstrings, not in inline markers, so a marker sweep alone would report a
clean codebase. The three mechanisms that do carry deferred work are G-60, G-61 and G-62.

## G-60 · `enrichment/classifier.py` preserves removed classification logic inside its docstring

The module's entire content is a docstring recording logic that was deleted, in a form that is
neither executable nor commented-out code but reads as a specification
(`enrichment/classifier.py:1-13`):

```python
"""Record classification — REMOVED (Bug 1 fix).

Classification is now derived from the ROR API response org types,
not from keyword matching on name1. See enrichment/tier1_ror.py
and enrichment/orchestrator.py for the new approach.

ROR_RESEARCH_TYPES = {"education", "healthcare", "government",
                      "facility", "nonprofit", "archive", "other"}

- ROR matched AND org type in ROR_RESEARCH_TYPES → research_institution
- ROR matched AND org type not in ROR_RESEARCH_TYPES → company
- ROR did not match → unknown
"""
```

The file is 13 lines, has no importer (G-6), and is still listed in the README file tree as the
classification module.

## G-61 · `scripts/debug_ucsf.py` carries a 127-line record list disabled by renaming

`scripts/debug_ucsf.py:113` opens `_unused_full_records = [` and the list closes at `:239` — 127
lines of `EnrichmentRecord` constructions annotated with the use cases they exercise
(`BSP_I_title_contact`, `BSP_J_email_in_name`, `BSP_K_pobox`, `BSP_L_company_abbr`,
`BSP_M_company_pfizer`, `BSP_N_bare_name1` and others). The block is syntactically live but bound
to a name nothing reads — the underscore prefix is the only thing marking it as disabled. This is
the largest disabled block in the repository and is functionally equivalent to commented-out
code while remaining invisible to a comment sweep.

## G-62 · Four in-code `UNCONFIRMED` markers name scoring parameters awaiting confirmation

These are the repository's actual deferred-work markers. All four concern the golden-record
scoring model and all four name the same external authority.

| Location | Quoted marker |
|---|---|
| `dedup/weights.json:2` | `"UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count tiers, account_group DRIT (transcript said DRID; live SAP shows DRIT)."` |
| `dedup/scoring.py:873` | `# UNCONFIRMED: partner count tiers mirror sales order count. CONFIRM w/ Bernd.` |
| `dedup/scoring.py:912` | `# UNCONFIRMED bonus value; sales org has no standalone tier.` |
| `dedup/scoring.py:942-943` | `UNCONFIRMED ordering (confirm with Bernd): total score, most recent last_order_year, equipment_count, company_code_count, then LOWEST row_id` |

Of the 33 documented scoring-weight bands, 2 are evidenced as agreed with the industry
supervisor, 5 are flagged UNCONFIRMED by these markers, and 26 carry no recorded agreement at
all (`04_PARAMETERS.md:602-606`, §4.1–4.2). See G-78.

## G-63 · Sixteen `# noqa` and six `# pragma: no cover` directives, none enforced by any gate

`# noqa` in application modules (16 total): `api/routes.py` ×5 (`:178, :273, :828, :873, :1057`),
`dedup/llm.py` ×3 (`:29, :152, :197`), `enrichment/orchestrator.py` ×3
(`:855, :1616, :1651`), `llm/openai_client.py` ×2 (`:218, :253`),
`dedup/scoring_xlsx.py` ×1 (`:193`), `enrichment/address_processing.py` ×1 (`:681`),
`main.py` ×1 (`:3`). A further 7 appear in `scripts/` and 9 in `tests/`, for 32 repository-wide.
Fifteen of the sixteen suppress `BLE001` (blind-except) on the handlers catalogued in G-65.

`# pragma: no cover` (6): `api/routes.py:170`, `dedup/llm.py:27`,
`dedup/scoring_xlsx.py:188`, `eval/dedup_eval.py:307`, `llm/openai_client.py:292`,
`tests/mocks/dedup_mock.py:68`.

No linter is configured in the repository and no gate runs one (G-53), so both directive
families annotate an enforcement that does not exist. The `# pragma` directives likewise annotate
a coverage measurement that cannot currently run (G-55).

---

# D · Fragility

## G-64 · Hardcoded values that the deployment cannot change without a code change

Every value below is a literal in source, with no environment variable, `Settings` field, or
request parameter that overrides it. The `Rationale` column is filled only from a code comment,
commit message or config docstring; `⚠ UNDOCUMENTED` where the repository records none
(`04_PARAMETERS.md` §1, conventions at `:22`).

| Value | Literal | Defined at | Consequence of it being fixed |
|---|---|---|---|
| Phase-1 chat-completion temperature | `0.0` | `llm/openai_client.py:205` | No deployment can raise or lower sampling entropy; the `extract_json` kwarg that appears to offer it does not (G-41) |
| ROR HTTP timeout | `15.0` s | `enrichment/tier1_ror.py:608` | A slow-registry episode cannot be tolerated by configuration; records fall through to the next tier |
| SERP results per query, six call sites | `5` | `enrichment/orchestrator.py:1183`, `:1297`; `enrichment/tier2a_contact.py:330`; `enrichment/tier2b_dept.py:227`; `enrichment/lab_resolver.py:83`; `enrichment/person_affiliation.py:124` | Retrieval breadth is fixed per stage; a correct candidate ranked 6th is never seen. `README.md:745` documents the value for one site without a rationale |
| Scored candidates verified per probe stage | `scored[:5]` | `enrichment/orchestrator.py:1161`, `:1211` | Same, for the department probe |
| Candidate subdomain acronym band | `2 ≤ len ≤ 6` | `enrichment/orchestrator.py:1091-1092` | Longer institutional acronyms are never probed as subdomains |
| Candidate tokens probed | top `2` tokens of length `≥ 4` | `enrichment/orchestrator.py:1093-1096` | Fixed probe budget per record |
| Dept-probe title bonus / path-penalty cap | `+1` / `min(2, penalty)` | `enrichment/orchestrator.py:257`, `:255` | The relative weight of title evidence against path depth cannot be retuned |
| Verification phrase-length gate | `≥ 4` characters | `enrichment/orchestrator.py:1380` | Fixed |
| Significant-token minimum (website Path B) | `4` characters | `enrichment/website_resolver.py:95` | Fixed; the guard that stops a short generic word validating a stranger's domain |
| Tier 3 token minimum | `3` characters | `enrichment/tier3_llm.py:29` | Fixed overlap denominator |
| Tier 2B match bands | `exact ≥ 90`, `partial ≥ 60` | `enrichment/tier2b_dept.py:152` | Fixed (and unreachable — G-2) |
| Address residual confidence threshold | `0.85` | `enrichment/address_processing.py:657` | The one LLM decision in the address stage cannot be made stricter or looser per deployment |
| GLEIF page size / fuzzy resolution cap | `"10"` / `completions[:5]` | `enrichment/tier1_lei.py:259`, `:340` | A correct company match beyond rank 10, or beyond the 5 fuzzy completions resolved, is never verified |
| Page-slice truncation (title / h1 / breadcrumb) | `300` characters each | `search/page_fetcher.py:254-256` | Fixed evidence budget per authoritative slice |
| Anchor-text truncation | `200` characters | `search/page_fetcher.py:213` | Fixed |
| HTTP `User-Agent` | `BrukerMDM-Enrichment/1.0` | `search/page_fetcher.py:127,134,150,190,221` | A host that blocks this agent cannot be worked around by configuration |
| Subdomain HEAD / redirect timeouts | `5` s each | `search/page_fetcher.py:95`, `:111` | Fixed |
| Request-ID length | `8` characters of a UUID4 | `api/middleware.py:22` | Collision probability is fixed |
| Log rotation | `10 MB` × `5` backups | `api/middleware.py:105-107` | Retention is size-bounded only, with no time-based expiry (G-67) |
| uvicorn host / port / reload | `0.0.0.0` / `8000` / `True` | `main.py:8` | Local development only |
| ADF enrichment page size | `50`, twice | `CONTEXT-EXTERNAL.md:64`, `:106` | The offset generator and the page fetch are two unbound literals; changing one without the other silently skips or re-processes rows (`04_PARAMETERS.md:340-345`) |
| Dedup residue knobs read from env with no `Settings` field | `SIG_PARTITION_THRESHOLD`, `DEDUP_MAX_CONCURRENCY` | `dedup/adjudicator.py:36-37`, `:948-951` | Configurable, but outside the `Settings` object and outside `OPTIONAL_VARS_WITH_DEFAULTS`, so they are invisible to `/tiers` and to the documented variable table (`04_PARAMETERS.md:347-352`) |

The 147 parameter rows of `04_PARAMETERS.md` §1 carry `⚠ UNDOCUMENTED — author to supply` in the
rationale column for the large majority of the values above; two values (`SIG_PARTITION_THRESHOLD`
= 12, `MAX_CANDIDATES_PER_BLOCK` = 50) and one threshold (`CONFIDENCE_MERGE_THRESHOLD` = 0.95)
are additionally recorded in `09_DECISIONS.md:1501-1504` as decisions whose magnitude the history
does not justify.

## G-65 · Exception paths that convert a failure into an ordinary negative result

The working tree carries **98 `except` clauses** — 88 in application modules, 10 in `scripts/`,
and none in `tests/` — of which **60 of the application-module handlers catch bare `Exception`**
(counted at this commit over `api/`, `dedup/`, `enrichment/`, `eval/`, `llm/`, `search/`,
`utils/`, `config.py`, `main.py`, `function_app.py`). Exactly one re-raises
(`llm/openai_client.py:209-210`, wrapping the failure in a `RuntimeError` that the calling tier
then catches). Three grades are distinguishable, and only the first is a genuinely silent
swallow; the distinction matters because the second grade is equally consequential for any metric
computed downstream.

**Grade 1 — no log, no marker in the result.** A failure is indistinguishable from a negative
answer both in the logs and in the output.

| Site | Handler | What is lost |
|---|---|---|
| `search/page_fetcher.py:104-109` | `subdomain_exists` → `False` | A network failure reads as "the subdomain does not exist"; the department probe drops the candidate |
| `search/page_fetcher.py:116-121`, `:141-142` | `resolve_final_url` → `None` | An unresolved redirect chain reads as "no redirect"; the probe keys off the stale base host |
| `search/page_fetcher.py:145-154` | `_sync_subdomain_exists` → `False` | As above, at the transport layer |
| `enrichment/orchestrator.py:940-943` | `resolve_final_url` wrapper → `final = None` | The probe base falls back to the registrable domain with no record of why |
| `enrichment/orchestrator.py:1363-1366` | `_verify_candidate_url` → `False` on fetch failure | A candidate host that could not be fetched is scored identically to one whose page contradicted the needle |
| `utils/text_utils.py:47-48` | `extract_domain` → `None` | A malformed host yields an empty `domain` column with no signal |
| `enrichment/website_resolver.py:190-196` | `_root_url` → the input URL unchanged | A parse failure silently leaves a path-bearing URL where a root was intended |
| `enrichment/website_resolver.py:543-546` | `_looks_like_url` → `False` | A parse failure reads as "not a URL" |
| `enrichment/search_terms.py:273-276` | `urlparse` failure → `None` | The ST2 dept-domain fallback silently declines |
| `enrichment/orchestrator.py:1070-1073`, `:1150-1153`, `:1200-1203`, `:1239-1242` | URL parse failures → `None`, `""`, or `continue` | Individual candidates vanish from the ranking without a count |
| `api/routes.py:271-274` | lenient re-parse of an already-parsed upload | Annotated `# noqa: BLE001 - the upload already parsed once; be lenient` |
| `llm/openai_client.py:216-219`, `:251-254` | client `close()` failures → `pass` | Deliberate: annotated as avoiding a Python 3.13 / httpx `aclose()` error storm (`llm/openai_client.py:212-214`) |

**Grade 2 — logged, but the caller receives an ordinary negative.** The log line exists; the
value the pipeline acts on carries no distinction, so no downstream count or metric can separate
"failed" from "found nothing".

| Site | Handler | Result the caller sees |
|---|---|---|
| `search/serpapi_client.py:34-36` | `logger.exception` → `return []` | Empty result set — a transient SERP failure is indistinguishable from a zero-hit query in every downstream count (`07_EVALUATION.md:797-799`) |
| `search/duckduckgo_client.py:27-29` | `logger.exception` → `return []` | As above |
| `enrichment/tier1_ror.py:838-846` | `logger.error` / `logger.exception` → `_no_match()` | A ROR outage reads as "no ROR match" and the record escalates to the next tier |
| `enrichment/tier1_lei.py:304-312` | `logger.error` / `logger.exception` → `{"matched": False, "error": True}` | The `error: True` flag is carried, but the record still escalates as an unmatched company |
| `enrichment/tier1_lei.py:349-355` | `logger.info` → `continue` | One fuzzy candidate is dropped from verification |
| `enrichment/orchestrator.py:1131-1136` | `logger.info` → `links = []` | The dept probe's homepage-scrape stage silently contributes no candidates |
| `enrichment/website_resolver.py:493-501` | `logger.info` → empty result, trace records `serp_call_failed` | The `WEBSITE_TRACE` diagnostic is the only place the distinction survives, and it is off by default |
| `enrichment/website_resolver.py:601-607` | `logger.info` → `WebsiteResolution()` | Path C failure reads as "the LLM declined" |
| `enrichment/company_canonical.py:61-63`, `enrichment/tier2_canonical.py:77-79`, `enrichment/overflow_check.py:53-55`, `enrichment/lab_resolver.py:118-123`, `enrichment/tier2a_contact.py:142-144`, `enrichment/person_affiliation.py:125-127`, `:151-153`, `enrichment/preprocess.py:1143-1145`, `:2325-2327`, `enrichment/address_processing.py:681-683` | `logger.info` / `logger.exception` → unchanged result, `continue`, or a default | Every LLM-backed stage treats an LLM failure as "no answer". The record proceeds with the pre-stage value and no failure marker in the output |
| `enrichment/orchestrator.py:1616-1621` | `logger.warning` → `return` | The entire late address stage is skipped; the record keeps its unprocessed address |
| `api/middleware.py:109-113` | `logger.warning` → console-only logging | An unwritable log path silently reduces the sinks to one |

**Grade 3 — logged and marked in the output.** The failure is visible to a consumer of the
result.

| Site | Handler | Marker |
|---|---|---|
| `enrichment/orchestrator.py:2599-2605` | `logger.error` → `result["enrichment_status"] = "failed"` | The record-level catch-all; the only place a Phase-1 failure reaches the response |
| `enrichment/tier3_llm.py:102-107` | `logger.exception` → `confidence="none"`, `enrichment_status="failed"`, `flag_reason="LLM call failed"` | Fully marked |
| `enrichment/orchestrator.py:1649-1657` | `logger.warning` → `self._lei_counts["errors"] += 1`, `return False` | Counted in the batch summary; annotated `# noqa: BLE001 — GLEIF must never fail a record` |
| `dedup/llm.py:197-215` | bounded exponential-backoff retry → `DedupLLMResult(error=…)` | The adjudicator marks the affected signatures uncertain; the docstring states the contract explicitly, "one bad call never fails a whole block" (`dedup/llm.py:163-167`) |

The Grade-3 design is stated as intentional in the modules that implement it. The consequence
that follows for the thesis is confined to Grades 1 and 2: **the fail-open design means an
external-service outage produces the same output shape as a clean miss**, so any measured tier
distribution, contact-lookup rate, or issue-reduction figure silently blends the two
(`06_EXTERNAL_DEPS.md:716-758`).

## G-66 · Thirty-eight procedures documented as having no fixture coverage

Pass 3 marks each procedure it could not illustrate from a repository fixture with
`⚠ NO FIXTURE COVERAGE` in place rather than constructing a hypothetical example
(`03_ALGORITHMS.md:19-21`, `:104-106`). The marker appears 41 times in that document, of which
two are the method statement (`:19`, `:104`) and one is a restatement in a part summary
(`:5437`), leaving **38 in-place records**. Each names the input that would be required.

| Part | Procedure or branch | Marker at |
|---|---|---|
| A | UC 7 Pattern A loop, including its org-payload branch | `03_ALGORITHMS.md:365` |
| A | Case A / Case B priority collision on `Co` / `S.A.` in a person payload | `:474` |
| A | `_LEGAL_SUFFIX_RE` marking an address line as an org | `:767` |
| A | 1–2 letter location fragments ("Hall A", "MS B") | `:828` |
| A | UC 10 full-field opaque-code clearing loop | `:883` |
| A | UC 11 `_normalise_dba` through `preprocess_record` | `:929` |
| A | Umlaut transliteration "ae" in legal-suffix normalisation | `:1093` |
| A | Compound admin values ("Finance and Administration") | `:1157` |
| A | Partially-cased input to `smart_title_case` | `:1215` |
| A | Bare-word granularity ("Laboratory" alone) | `:1281` |
| B | Gather-level exception branch (`orchestrator.py:811-821`) | `:1379` |
| B | `determine_enrichment_status` / `should_flag_for_review` | `:1619` |
| B | `run_overflow_check` and the UC 0 branch | `:1652` |
| C | Cities shorter than 4 characters in the ROR location guard | `:1887` |
| C | False-positive state-abbreviation expansion of an ordinary word | `:1957` |
| C | Multi-location ROR org whose first location is the wrong country | `:2010` |
| C | `_extract_org_fields` with multiple `acronym` entries | `:2054` |
| C | A display name legitimately ending in a parenthesised country | `:2058` |
| C | `extract_website_from_ror` against a populated `links[]` | `:2082` |
| C | `_match_child_locally` invoked directly | `:2156` |
| C | `_token_covers` false-positive prefix collision | `:2198` |
| C | GLEIF retry loop (all HTTP tests pass `max_retries=0`) | `:2397` |
| D | Real HTML paths in `PageFetcher` — every test substitutes `MockPageFetcher` | `:3254` |
| F | `strip_tld` on a bare single-label domain | `:3818` |
| F | ST2 Rule-3 dept-domain fallback with empty Name 2 | `:3883` |
| F | Terminal-normalisation truncation branch | `:3934` |
| F | `_subdomain_acronym` step 9 | `:4023` |
| F | ST2 unit-phrase guard firing to `None` | `:4111` |
| F | `_name2_is_unit_phrase` as the deciding rule | `:4147` |
| F | Stacked `www.`/`web.` prefixes; dept-domain-as-path reduction | `:4156`, `:4157` |
| G | `_run_address_stage` itself | `:4417` |
| G | `_apply_residual_llm` — every test passes `llm_client=None` | `:4545` |
| G | Comma-bearing building names | `:4616` |
| G | Qualifier after a trailing directional token | `:4756` |
| G | `_named_building_value` rejections at the address call site | `:4804` |
| G | A real street with an embedded facility phrase | `:4930` |
| H | Float-typed record-id cells in the `/issues/compare` join | `:5380` |
| I | All-empty-address rows deriving one shared block id | `:5536` |
| I | LEI columns on the dedup file route (G-48) | `:5599` |
| J | Missing or corrupt `dedup/weights.json` | `:6154` |
| J | A criterion-less weights dict passed directly to `score_row` | `:6250` |
| J | Duplicated headers in the scoring workbook | `:6526` |

Two of these bear directly on the reachability findings: the `determine_enrichment_status` /
`should_flag_for_review` pair (G-35) and `run_overflow_check` (UC 0) have no test at all, so
neither the dead code nor the untested-but-live branch is visible to the suite.

## G-67 · Single points of failure in the production topology

Identified in Pass 2 and Pass 6b. Each is a component or property whose failure or absence stops,
corrupts, or silently degrades the whole run.

| # | Single point | Evidence | Failure behaviour |
|---|---|---|---|
| 1 | The sequential `ForEach1` with `retry: 0` | `CONTEXT-EXTERNAL.md:88`, `:54,96,126,154` | One failed `Web1` or `Merge Back` stops the loop at that iteration; earlier batches are already committed and later offsets are never processed (`02_ARCHITECTURE.md:212-225`) |
| 2 | The absence of an enrichment watermark | `CONTEXT-EXTERNAL.md:106` | A rerun after (1) re-enriches every already-merged row, re-paying LLM and SERP spend and possibly writing different values (G-32) |
| 3 | The unbatched deduplication Lookup | `CONTEXT-EXTERNAL.md:224-236` | One activity carries the whole Validation table against a 5,000-row / 4 MB ceiling (G-33) |
| 4 | The cross-tenant public endpoint | `CONTEXT-EXTERNAL.md:135-139`, `:255-259` | Every ADF→service call traverses Tillit→Bruker over the public internet through `AutoResolveIntegrationRuntime`; there is one endpoint and no alternate path (`02_ARCHITECTURE.md:402-409`) |
| 5 | `ANONYMOUS` auth on all 13 routes | `function_app.py:12` | No application-layer authentication, CORS policy, rate limit, or request-size limit. Two unauthenticated GET routes make a billable Azure OpenAI call per request and disclose endpoint, deployment names, API version and key length (`api/routes.py:1043-1055`, `:1085-1089`) (`06b_CROSSCUTTING.md:1285-1289`). Whether Azure inbound restrictions compensate is ⚠ NOT EVIDENCED and is the highest-value open item of Pass 6b (`06b_CROSSCUTTING.md:1331`) |
| 6 | A single long-lived Azure OpenAI API key | `config.py:78-84` | No Key Vault, no managed identity, no rotation mechanism, on a topology where workload identity is available (`06b_CROSSCUTTING.md:1290-1291`) |
| 7 | `LLM_SSL_VERIFY=false` as an Application Setting | `config.py:27-67` | Disables TLS verification for the calls that carry personal data, with no code change required and no indicator on `/health` or `/tiers` (`06b_CROSSCUTTING.md:1292-1294`) |
| 8 | `configure_logging` calling `basicConfig(force=True)` | `api/middleware.py:118` | Discards pre-existing root handlers. ⚠ UNVERIFIED whether this displaces the Azure Functions worker's Application Insights handler; if it does, the deployed app ships no application telemetry at all (`06b_CROSSCUTTING.md:1265-1268`) |
| 9 | `GET /health` returning a literal | `api/routes.py:80` | No dependency check; reports `"healthy"` on an app whose LLM credentials are absent (`06b_CROSSCUTTING.md:1272-1273`) |
| 10 | `[Block ID]` computed outside the repository | `CONTEXT-EXTERNAL.md:309-310` | The blocking predicate that bounds dedup recall is a DATAshaper rule configuration, not a repository artefact; it is ⚠ NOT DOCUMENTABLE FROM THIS REPOSITORY (`03_ALGORITHMS.md:5442`) and cannot be changed, tested, or versioned here |
| 11 | The approval identity | `dedup/scoring.py:560`, `:574-578` | `approver` is required by the model, is not a parameter of `apply_approval`, is written to no row field, is never authenticated, and is never compared to any other actor — the four-eyes property is a decision *structure* in code and an *identity* nowhere (`06b_CROSSCUTTING.md:1300-1302`) |
| 12 | Statelessness of `/api/dedup/approve` | `api/routes.py:950-955` | No durable audit trail of any approval exists; the sole record is one line in a size-rotated file (`06b_CROSSCUTTING.md:1303-1304`). An unauthenticated caller can obtain promoted golden fields for an arbitrary row set (`:1305-1307`) |
| 13 | Log retention and redaction | `api/middleware.py:105-107` | Size-bounded only, no time-based expiry, and no redaction anywhere: full person names reach both the console and the rotating file through at least four sites (`enrichment/person_affiliation.py:126,134,152`; `enrichment/preprocess.py:2326`; `search/serpapi_client.py:35`; `search/duckduckgo_client.py:28`) (`05_DATA_MODEL.md:1154-1158`) |
| 14 | ADF activity payload retention | `CONTEXT-EXTERNAL.md:129-130`, `:249-250` | `secureInput`/`secureOutput` are `false` on both Web activities, so full request and response bodies containing personal data are retained in cleartext in ADF monitoring on the Tillit tenant. `Lookup1` issues `SELECT *` (`:106`), so every column of every row crosses the tenant boundary regardless of what enrichment needs (`06b_CROSSCUTTING.md:1295-1299`) |
| 15 | Application Insights sampling | `host.json:5-8` | Sampling is enabled with only `Request` excluded, so the trace stream carrying all 178 application log statements is sampled while the request stream carrying almost no information is retained in full (`06b_CROSSCUTTING.md:1269-1271`) |
| 16 | Phase-1 token accounting | `llm/openai_client.py:198-208` | `response.usage` is discarded in Phase 1 and captured in Phase 2 (`dedup/llm.py:188-195`), so the more expensive phase is the unmeasured one (`06b_CROSSCUTTING.md:1261-1262`) |

## G-68 · Unguarded failure modes in otherwise deterministic components

Recorded separately from G-65 because these are paths where an exception is *not* caught and
would propagate, in components documented as deterministic and safe.

- `load_weights` propagates `FileNotFoundError` / `json.JSONDecodeError` if `dedup/weights.json`
  is missing or malformed; there is no guard (`dedup/scoring.py:618-623`). The module docstring
  states scoring "NEVER raises or fails the batch" (`dedup/scoring.py:9-13`), which holds for row
  values but not for the weights file itself. No fixture covers it (`03_ALGORITHMS.md:6154`).
- A `KeyError` occurs if a caller-supplied weights dict lacks a criterion key
  (`dedup/scoring.py:849-919`) — impossible for tables passing `coerce_weights` or loaded from
  the shipped file, and reachable by a direct `score_row` call (`03_ALGORITHMS.md:6250`).
- `_extract_department` in Tier 2B contains no `try`/`except`; an exception propagates to the
  caller loop (`enrichment/tier2b_dept.py:247-266`; `03_ALGORITHMS.md:6889`). Unreachable in
  practice (G-2).
- The SerpAPI client sets no timeout and no retry (`search/serpapi_client.py:27-56`), so nothing
  in this repository bounds how long a SerpAPI call may hang — a latent hang risk on the SERP
  path (`06_EXTERNAL_DEPS.md:256`).
- Header matching in the scoring workbook is normalised and first-occurrence-wins, so a
  duplicated header silently binds to its first column (`dedup/scoring_xlsx.py:123-133`); the
  same property, on the enriched evaluation workbook, is what makes its "after" side resolve to
  the last non-empty duplicate (`03b_EXEMPLARS.md:63-73`; `07_EVALUATION.md:642-668`).
- Rows with entirely empty addresses and no supplied `block_id` all derive the same block id and
  are adjudicated together (`dedup/signatures.py:33-34,51-54`; `03_ALGORITHMS.md:5536`).
- The installed `duckduckgo-search 8.1.1` scrapes Bing rather than DuckDuckGo, so the documented
  "DuckDuckGo fallback" does not query the service its name implies
  (`06_EXTERNAL_DEPS.md:316`, `:802`, `:729`).

---

# E · Scoped out

Work deliberately excluded, separated from work intended and not done. The distinction is
load-bearing: the first set is a design boundary the repository records; the second is a gap
between what the documentation asserts and what the code does. They support different thesis
claims and must not be conflated.

## E.1 · Decided not to do — evidenced as a scope decision

### G-69 · ZFI records are excluded upstream by instruction, with no recorded rationale

Two artefacts record the decision. `dedup/scoring.py:15-16` states: *"ZFIS is deliberately
absent: it is a separate upstream gate that runs before enrichment; those records never reach
dedup."* `CONTEXT-EXTERNAL.md:434` records the instruction: *"[AUTHOR] ZFI records are excluded
from processing on Bernd Schnurrer's instruction."* The exclusion is therefore a decision, made
by a named person, applied at workflow step 1 (`CONTEXT-EXTERNAL.md:418`) — and the *reason* is
recorded nowhere (`CONTEXT-EXTERNAL.md:435`; `02_ARCHITECTURE.md:473-475`;
`04_PARAMETERS.md:547-550`; `09_DECISIONS.md:1512-1515`). The script performing it is not
located (G-26), so neither the predicate nor the volume excluded is knowable from the
repository.

⚠ RATIONALE NOT IN REPO — author to supply.

⚠ MEASUREMENT REQUIRED — the share of the SAP extract removed by this gate, which bounds the
population the whole system applies to, is not in the repository.

### G-70 · Salesforce reconciliation is defined as a downstream contract, not built here

The repository is explicit that Salesforce matching is Phase 3's work and that this codebase's
responsibility ends at producing the mapping table Phase 3 consumes: *"Semantics (the output is
Phase 3's mapping table) … Phase 3 Case A matches Salesforce records against golden rows; a
unique SAP customer is a valid match target"* (`dedup/scoring.py:1041-1045`), with the consumption
rule stated as a contract at `dedup/scoring.py:266-268`, `api/routes.py:954-955` and
`README.md:1103`. The eight Salesforce id slots are read solely as a scoring criterion
(`dedup/scoring.py:73-77`, `:176-186`, `:918-919`; `dedup/weights.json:55-57`;
`dedup/scoring_xlsx.py:55-58`).

This is a boundary decision, not an omission: the interface is specified, the input fields are
carried, and the consumer is named. What the repository does not record is where Phase 3 lives
or whether it exists.

### G-71 · Approval persistence is explicitly out of scope

*"Persistence is intentionally out of scope — a durable approval store is a future step."*
(`api/routes.py:953-954`; restated `README.md:1101` as "Persistence is out of scope for now",
and `dedup/scoring.py:553-556`). The endpoint applies the decision to the submitted rows and
echoes them back; nothing is stored (`02_ARCHITECTURE.md:274`, `:372`).

This item is deliberately in **both** halves of §E: the decision to defer is recorded, and the
work is named as a future step. What is not recorded is why persistence was deferred or where
the approval record is expected to live (`09_DECISIONS.md:1263`, `:1505`). Its consequences are
G-67 items 11 and 12.

### G-72 · `/api/dedup/cluster-block` explicitly excludes four adjacent concerns

*"It does **not** do address validation, embeddings, golden-record election, or file I/O — those
are out of scope and handled elsewhere in the pipeline. The orchestrator (ADF/DATAshaper) handles
file ↔ JSON conversion; this endpoint is strictly JSON in / JSON out."* (`README.md:1129`). The
election exclusion is the boundary independently argued in code: *"Separate from
/api/dedup/cluster-block on purpose: clustering and election have different inputs, cadences, and
cost profiles — election is pure arithmetic over dedup/weights.json and can be re-run on retuned
weights without paying for LLM adjudication again"* (`api/routes.py:900-903`; restated
`dedup/scoring.py:3-7`) (`02_ARCHITECTURE.md:454-459`; `09_DECISIONS.md` D-28).

Of the four, the embeddings exclusion is the one the history states without argument: one
sentence names it, and whether embeddings were evaluated, and against what, is not recorded
(`09_DECISIONS.md:901`, `:1500`).

### G-73 · The address gate is consumed from DATAshaper rather than computed

`[Block ID]` is precomputed by the DATAshaper address gate and read from the request, not
derived by the service (`CONTEXT-EXTERNAL.md:309-310`; `02_ARCHITECTURE.md:142-144`). The
service owns only the fallback block-key derivation and the precedence rule that makes a supplied
id win (`dedup/signatures.py`; `03_ALGORITHMS.md` Part I §2). The vendor states the recall
ceiling of postal-code-exact blocking plainly in the onboarding transcript;
`09_DECISIONS.md:1499` records that no artefact shows that ceiling being weighed when the input
contract was adopted, and `:930-934` records that whether the fallback scheme is intended for
testing or for production is likewise unrecorded.

The consequence is stated once and not repeated: **the recall ceiling of the entire
deduplication phase is set by a component this repository neither contains nor can test.**

### G-74 · Repairs the enrichment contract deliberately does not attempt

Each is evidenced as a boundary rather than a defect. Grouped because they share one principle,
stated at `README.md:94`: *"Never fabricate data. If confidence is low, return the original values
and flag for human review."*

| Excluded repair | Evidence | Observed consequence |
|---|---|---|
| Splitting a multi-contact field into two records | `03b_EXEMPLARS.md:292-296` | `G3-CONTACT-007` persists after enrichment on REC-08 |
| Rewriting a malformed postal code or a non-ISO country key | `03b_EXEMPLARS.md:412-422` | `G4-ADDR-026` and `G4-ADDR-027` persist on REC-13 |
| Resolving a name set over the 140-character SAP limit | `03b_EXEMPLARS.md:368-377` | `G4-NAME-015` persists on REC-11; the resolution is a steward decision |
| Inventing an organisation name where Name 1 is blank | `03b_EXEMPLARS.md:208-217` | `G2-VAL-001` persists on REC-04 — the clearest exemplar of the pipeline correctly declining |
| Emitting `G1-ADDR-009` and `G4-ADDR-025` deterministically | `enrichment/issue_detection.py:18-24, 88, 112` | The two codes never appear in the Issues column (G-45) |
| Passing the input Name 2 to the Tier 2B extraction LLM | `enrichment/tier2b_dept.py:255` — *"The input name2 is intentionally NOT passed to the LLM"* | Moot while Tier 2B is unwired (G-2) |
| Using `token_set_ratio` for LEI verification | `README.md:425`; `09_DECISIONS.md` D-6 | *"`token_set_ratio` is deliberately **not** used: it scores any contained substring 100 and would accept that wrong entity"* |
| Expanding two-letter postal codes in the ROR query | `enrichment/tier1_ror.py:73` | *"Two-letter postal codes are intentionally excluded (too collision-prone)"* |
| Modifying the source string during email extraction (UC 8) | `enrichment/preprocess.py:123` | *"The source string is intentionally NOT modified — UC 8 copies"*; `G1-CROSS-003` consequently persists on REC-03 (`03b_EXEMPLARS.md:180-184`) |

### G-75 · Capabilities removed by a recorded decision

Distinct from G-74: these existed and were deleted. All are documented in `09_DECISIONS.md`;
each row states what the history records and what it does not.

| Removed | Commit / decision | What the history records | What is missing |
|---|---|---|---|
| Keyword-based record classification | `enrichment/classifier.py:1-13` | "REMOVED (Bug 1 fix)"; replaced by ROR org types | — (rationale stated) |
| The `dry_run` request option | D-37, `09_DECISIONS.md:1355-1375` | "to streamline functionality and focus on core processing" | Whether it was unused, misleading, or superseded |
| `*_original` / `*_changed` response fields | D-33, `:1270-1294` | "Removed unnecessary original and changed fields to streamline the model" | Why they were unnecessary, given they carried the what-changed signal |
| The direct-OpenAI backend | D-39, `:1417-1439` | Removed as "dead", four months after introduction | When and why it stopped being used |
| `search_terms.derive_department_domain` | D-17, `:683-707`; `README.md:1995` | "Dead code removed — superseded by `_probe_department_url`" | Why the two co-existed for eleven weeks, and whether the deleted one was ever live |
| `derive_acronym` in the Search Term 1 chain | D-18, `:708-727` | The removal from the chain | — |
| The separate bare `domain` output column | D-36, `:1333-1354` | "for clarity" | Why the merged column carries the URL under the name `Domain` |
| `search_terms.unit_domain_or_path` from the live path | `00_INVENTORY.md:320-322` | Superseded by `_dept_domain_to_search_term` | Whether the retained definition and its tests are intentional (G-37) |

## E.2 · Intended but not done

Each item below is asserted somewhere — in the README, in a commit, in a code marker, or by the
author in `CONTEXT-EXTERNAL.md` — as work that is part of the system, and is absent from the code
or the exports at this commit. This is a different statement from §E.1 and should be read as
such.

### G-76 · Name-2 correction: documented as active, disabled without a recorded reason

This is the sharpest case in the document and the one that most needs the distinction §E draws.
Both Name-2 correction paths were wired and reachable in the initial commit `f77080b`: the
Tier 2A gate carried no Name-2 condition, and Tier 2B ran as a fallback after Tier 2A
(`09_DECISIONS.md:86`, citing `f77080b:enrichment/orchestrator.py:476-480,500,506,531,540`).
Commit `635d5ba`, two days later, added the gate condition and removed the Tier 2B call
(`635d5ba:enrichment/orchestrator.py:440,488-493`). Its commit body is an itemised list that
describes refining both modules and **does not mention either change**
(`09_DECISIONS.md:210`, `:1494`).

The README was not updated: Tier 2B is still presented as an active tier at eight sites (G-7),
and the Tier 2A module still implements both modes (G-1). This is therefore not a scope
decision the repository records; it is an outcome the repository records without a decision.
Whether the two paths were judged redundant, one judged unreliable, or the gate was an
unintended consequence is not determinable from any artefact
(`09_DECISIONS.md:1494`).

⚠ RATIONALE NOT IN REPO — author to supply.

Consequences: G-1, G-2, G-4, G-5, G-7, G-8, G-18, G-38.

### G-77 · Four ADF changes stated as pre-freeze work and absent from both exports

`CONTEXT-EXTERNAL.md:194-197` and `:312-314` state four amendments to be made before the
2026-08-21 code freeze. None is present in the exports at `lastPublishTime`
2026-07-29T12:09:37Z. Each is carried in `02_ARCHITECTURE.md` behind a
`<!-- VERIFY-BY-FREEZE: … -->` comment so the claims can be grepped and confirmed against the
artefacts at freeze.

| Intended change | Absent at | Gap entry |
|---|---|---|
| Group-code predicate on all three Lookup activities | `CONTEXT-EXTERNAL.md:64`, `:106`, `:226` | G-31 |
| Retry policy above 0 on `Web1` and `Merge Back` | `CONTEXT-EXTERNAL.md:54,96,126,154` | G-67 item 1 |
| `enriched_at` watermark so `Lookup1` selects only unenriched rows | `CONTEXT-EXTERNAL.md:106` | G-32 |
| Deduplication batched by `block_id` through a ForEach | `CONTEXT-EXTERNAL.md:224-236` | G-33 |

### G-78 · Five scoring parameters flagged in code as awaiting confirmation

The four `UNCONFIRMED` markers of G-62 name five open parameters:
`sales_order_partner_count` tiers, the `combined_presence_bonus` value, the `account_group`
`DRIT` label (the transcript says `DRID`; live SAP shows `DRIT`), and the tie-break ordering
(`dedup/weights.json:2`; `dedup/scoring.py:873`, `:912`, `:942`). A sixth reconciliation is open
without a code marker: the DATAshaper prototype of the same model bands sales-order recency by
**months since the run date** while `dedup/weights.json` uses **absolute calendar years**, the DS
top band is 25 points against this repository's 20, and the two transcript passages disagree with
each other on the middle band's lower bound (10 vs 20 months)
(`04_PARAMETERS.md:527-543`; `09_DECISIONS.md:1503`). Which model is authoritative, and whether
the year tiers were re-agreed, is not evidenced anywhere in the repository.

⚠ RATIONALE NOT IN REPO — author to supply.

### G-79 · Evaluation labelling, coverage measurement, and CI are named and not done

Three pieces of work are referenced by artefacts that exist and cannot run without them.

- **The dedup ground-truth labelling.** `eval/dedup_eval.py` is written against
  `expected_cluster` and `expected_routing`; neither column exists in any workbook (G-52). The
  harness runs and returns zeros.
- **Coverage measurement.** `00_INVENTORY.md:421-425` recommends `pytest --cov`; `pytest-cov` is
  declared and not installed (G-55).
- **CI/CD.** No automation artefact exists on any ref, and the suite is red and ungated (G-53).

### G-80 · Two reachable issue codes have no covering record in any repository dataset

Across both workbooks, the subset, and every JSON fixture, the detector raises 32 of the 37
declared codes. Three of the five it never raises cannot be exercised by any input (G-45, G-46).
The remaining two are genuine data gaps rather than code gaps
(`03b_EXEMPLARS.md:79-98`):

| Code | Name | Record that would be required |
|---|---|---|
| `G1-NAME-001` | Name Overflow Across Fields | A Name 1 carrying no legal-entity suffix, followed by a Name 2 opening with a connector or a lowercase word (`enrichment/issue_detection.py:296-305`) |
| `G3-ADDR-013` | Two Distinct Street Addresses on Record | Two street slots holding two *different* values that both satisfy `_looks_like_street` (`enrichment/issue_detection.py:419-424`) |

Separately, one code is exercised **only** by the enriched workbook and by no pre-enrichment
record: `G3-ADDR-012` (Duplicate Street Across Fields), which the pipeline introduces on REC-01
(`03b_EXEMPLARS.md:96-98`, `:129-135`).

---

# F · Framing

Every entry above is stated as a property of the system with its evidence, in the present tense,
without an evaluative adjective. That form was chosen so each entry can be lifted into either of
the two thesis sections without rewriting the claim, only its tense and its modal:

- **Limitations** — the entry as written, with the scope of the affected population stated where
  it is known and marked `⚠ MEASUREMENT REQUIRED` where it is not. Example, from G-5: *"The
  pipeline fills blank Name 2 values only; an existing Name 2 is normalised to official wording
  from the name alone but is not verified against retrieved web evidence."*
- **Future work** — the same entry prefixed by what would remove it, which every entry names.
  Example, from G-1: *"Entering Mode B requires admitting records with a populated Name 2 into the
  orchestrator gate at `enrichment/orchestrator.py:2451-2457`."*

Three cautions for the write-up, each of which the evidence above supports and none of which it
permits overstating.

1. **Reachability is not coverage, and coverage is not reachability.** Tier 2A Mode B and Tier 2B
   both pass their tests (G-1, G-2). A green test does not establish that any pipeline path
   reaches the function under test. Conversely, 38 procedures have no fixture at all (G-66) while
   running in production on every record.
2. **A decision recorded without a reason is not a decision the thesis can argue.**
   `09_DECISIONS.md:1486-1517` lists seventeen such items, and §E.1 above carries their scope
   analogues. Where the repository records only *what* changed, the correct statement is that the
   change is evidenced and the reason is not — never a reconstructed rationale.
3. **Two limitations bound every quantitative claim the thesis can make.** The fail-open design
   makes an external-service outage produce the same output shape as a clean miss (G-65
   Grades 1–2), and there is one dataset with no split, calibrated to the model it would be used
   to evaluate (`07_EVALUATION.md:695-720`). Any accuracy, reduction, or tier-distribution figure
   is bounded by both, and both are properties of the design rather than of the measurement.

**Pass 8 complete.** 80 entries: 21 code↔documentation discrepancies (§A), 37 components
referenced and not implemented (§B), 5 code-marker findings including a nil result for
`TODO`/`FIXME`/`HACK` (§C), 5 fragility inventories covering 22 classes of hardcoded value, 98
`except` clauses classified into three grades of failure visibility, 38 procedures without
fixture coverage, and 16 single points of failure (§D), and 12 scope entries separated into 7
decided-not-to-do and 5 intended-but-not-done (§E). Stop.
