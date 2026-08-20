# SAP Customer Master Data Name Enrichment API / 09/08/26

An intelligent, multi-tier enrichment service built for Bruker Corporation's Master Data Management (MDM) pipeline. It resolves incomplete, abbreviated, misspelled, or incorrectly formatted SAP customer master data records — specifically institution and company names — through a pipeline that combines deterministic preprocessing, API lookups, web search, contact verification, and LLM inference.

The service now spans **two phases** of the MDM pipeline:

- **Phase 1 — Enrichment** (`POST /enrich`): cleans and canonicalizes individual records' name/address fields through the tiered escalation pipeline described in most of this document.
- **Phase 2 — Deduplication Adjudicator** (`POST /api/dedup/cluster-block`): runs *after* enrichment and *after* DATAshaper's address gates. It receives address-gated candidate records (same country + postal code + street) and decides which of them are true duplicates, producing clusters. See [Phase 2 — Deduplication Adjudicator](#phase-2--deduplication-adjudicator).

Both phases share the same FastAPI app, configuration system, Azure Functions deployment, and AI Foundry (Azure OpenAI) client.

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Solution Strategy](#solution-strategy)
3. [Technology Stack](#technology-stack)
4. [Architecture Overview](#architecture-overview)
5. [The Enrichment Pipeline: Stage by Stage](#the-enrichment-pipeline-stage-by-stage)
   - [Stage 0: Name1 Overflow Check (UC 0)](#stage-0-name1-overflow-check-uc-0)
   - [Stage 1: Preprocessing (UC 6-12)](#stage-1-preprocessing-uc-6-12)
   - [Stage 2: Tier 1 — ROR API Lookup](#stage-2-tier-1--ror-api-lookup)
   - [Stage 2 (Company): Tier 1 — GLEIF / LEI Registry Lookup](#stage-2-company-tier-1--gleif--lei-registry-lookup)
   - [Stage 2b: Person Affiliation Lookup](#stage-2b-person-affiliation-lookup)
   - [Stage 3: Tier 2 — Multi-Mode Canonicalization](#stage-3-tier-2--multi-mode-canonicalization)
   - [Stage 4: Tier 3 — LLM Inference (Last Resort)](#stage-4-tier-3--llm-inference-last-resort)
   - [Stage 5: Tier 1 re-lookup after canonicalisation](#stage-5-tier-1-re-lookup-after-canonicalisation)
   - [Stage 6: Batch consensus](#stage-6-batch-consensus)
   - [Finalization](#finalization)
     - [Rule 7 — Output casing normalisation](#rule-7--output-casing-normalisation)
     - [Why casing does not set a changed flag](#why-casing-does-not-set-a-changed-flag)
     - [Registry names are authoritative](#registry-names-are-authoritative)
   - [Website, Domain, Department-Domain & Search-Term Resolution](#website-domain-department-domain--search-term-resolution)
6. [Use Case Reference Table](#use-case-reference-table)
7. [Record Classification Logic](#record-classification-logic)
8. [Confidence, Flags, and Enrichment Status](#confidence-flags-and-enrichment-status)
9. [Per-Field Provenance and Admissibility](#per-field-provenance-and-admissibility)
10. [Data Models](#data-models)
11. [API Endpoints](#api-endpoints)
12. [Phase 2 — Deduplication Adjudicator](#phase-2--deduplication-adjudicator)
    - [Why a Separate Pass](#why-a-separate-pass)
    - [The Two-Level Identity Model](#the-two-level-identity-model)
    - [Critical Identity Rules](#critical-identity-rules)
    - [Endpoint Contract](#endpoint-contract)
    - [The Per-Block Algorithm](#the-per-block-algorithm)
    - [Mode A vs Mode B](#mode-a-vs-mode-b)
    - [The Deterministic Name 2 Asymmetry Rule](#the-deterministic-name-2-asymmetry-rule)
    - [Residue Candidate Nomination](#residue-candidate-nomination)
    - [LLM Call Details](#llm-call-details)
    - [Routing, Clusters, and the llm_flag](#routing-clusters-and-the-llm_flag)
    - [Telemetry](#telemetry)
    - [Chaining Enrichment → Dedup](#chaining-enrichment--dedup)
    - [Dedup Diagnostics](#dedup-diagnostics)
13. [Project Structure](#project-structure)
14. [Module-by-Module Reference](#module-by-module-reference)
15. [External Services and APIs](#external-services-and-apis)
16. [Configuration and Environment Variables](#configuration-and-environment-variables)
17. [Setup and Installation](#setup-and-installation)
18. [Running Locally](#running-locally)
19. [Testing](#testing)
20. [Azure Function Deployment](#azure-function-deployment)
21. [ADF Integration and DATAshaper Mapping](#adf-integration-and-datashaper-mapping)
22. [Complete Data Flow Diagram](#complete-data-flow-diagram)
23. [Changelog](#changelog)

---

## Problem Statement

SAP customer master data records at Bruker contain institution and company names spread across three fields: `Name1`, `Name2`, and `Name3`. These fields frequently contain:

- **Abbreviations** — "MIT", "UCLA", "Univ of Florida"
- **Misspellings** — "Masschusetts Institute of Technology"
- **Informal formatting** — "Dept of AI", "Chemistry Dept", "Chem Division"
- **Misplaced data** — email addresses, street addresses, contact person names, accounts payable references, or internal SAP codes stored in name fields
- **Overflow** — a single organization name split across Name1 and Name2 (e.g., "Adams Air" + "Hydraulics Inc")
- **Incomplete records** — Name2 (department/division) is blank, even when a contact person is known

These quality issues propagate into downstream systems — reporting, invoicing, compliance — and must be resolved. Manual cleanup is not scalable across tens of thousands of records.

---

## Solution Strategy

The API uses a **tiered escalation approach**: start with the cheapest, most reliable method and escalate only when cheaper methods fail. Each tier has progressively higher cost and lower confidence:

| Tier | Method | Cost | Confidence | When Used |
|------|--------|------|------------|-----------|
| **Preprocessing** | Regex patterns (deterministic) | Zero (no API calls) | Deterministic | Always runs first |
| **Tier 1 (ROR)** | ROR API (Research Organization Registry) | Low (free public API) | High | Institutions (and some companies ROR happens to carry) |
| **Tier 1 (LEI)** | GLEIF API (Legal Entity Identifier registry) | Low (free public API) | High (verified) / Medium (fuzzy) | Companies — deterministic step before the LLM fallback |
| **Tier 2A** | Contact person web lookup (SERP + page fetch + LLM) | Medium | Medium-High | When contact is available and Tier 1 matched the parent org |
| **Tier 2 Canonical** | LLM canonicalization (no web search) | Low-Medium | High (only accepts high-confidence answers) | When Name2/3 present but no ROR child match |
| **Tier 2B** | Department web search (SERP + page fetch + LLM) | Medium | Medium-Low | When no contact or Tier 2A failed |
| **Tier 3** | Pure LLM inference | Medium | Low (anything it writes is flagged `unverified-inference`) | Last resort, all other tiers failed |

**Key design principles:**

1. **Never fabricate data.** If confidence is low, return the original values and flag for human review.
2. **Deterministic before probabilistic.** Regex-based preprocessing runs before any API or LLM call.
3. **Structured extraction over free-form generation.** LLM prompts extract from structured page elements (URL path, title, H1, breadcrumb) rather than interpreting free-form body text.
4. **Scope filtering.** The pipeline distinguishes department-level units (acceptable) from granular units like individual labs, groups, or facilities (rejected per UC 4/5 scope rules).
5. **Transparency.** Every result includes `tier_used`, `source`, `confidence`, `domain`, and the four review fields — `flag_for_review`, `flag_codes`, `flagged_fields`, `flag_reason` — so humans can audit the pipeline's decisions and see which field a doubt attaches to.

---

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Web Framework** | FastAPI (Python 3.11+) | REST API with async support |
| **Serverless Runtime** | Azure Functions v2 (ASGI wrapper) | Production deployment |
| **LLM** | Azure OpenAI / AI Foundry (GPT-5.4) | Extraction, canonicalization, inference, dedup adjudication |
| **Organization Registry** | ROR API v2 | Institution/organization lookup and classification |
| **Company Registry** | GLEIF API v1 | Company legal-name + Legal Entity Identifier (LEI) lookup (Tier 1 company branch) |
| **Web Search** | SerpAPI (primary) / DuckDuckGo (fallback) | Finding faculty pages, department pages |
| **HTML Parsing** | BeautifulSoup4 | Extracting structured elements from web pages |
| **Fuzzy Matching** | RapidFuzz | Name comparison (token sort ratio, partial ratio) |
| **Validation** | Pydantic v2 | Request/response schema enforcement |
| **HTTP Client** | httpx (async) / requests (sync, in thread executor) | External API calls |

---

## Architecture Overview

```
                          POST /enrich
                               |
                               v
                     +------------------+
                     |   FastAPI Routes  |
                     +------------------+
                               |
                               v
                     +------------------+
                     |   Orchestrator    |  <-- Controls the entire pipeline
                     +------------------+
                               |
            +------------------+------------------+
            |                  |                  |
            v                  v                  v
    +-------------+    +-------------+    +-------------+
    | Preprocessor|    |  Tier 1     |    |   Tier 3    |
    | (regex, det)|    |  (ROR API)  |    |   (LLM)     |
    +-------------+    +-------------+    +-------------+
                               |
                    +----------+----------+
                    |          |          |
                    v          v          v
             +----------+ +--------+ +----------+
             | Tier 2A  | | Tier 2 | | Tier 2B  |
             | Contact  | | Canon. | | Dept     |
             | Lookup   | | (LLM)  | | Search   |
             +----------+ +--------+ +----------+
                    |                      |
                    v                      v
             +----------+          +----------+
             | SerpAPI/ |          | SerpAPI/ |
             | DDG      |          | DDG      |
             +----------+          +----------+
                    |                      |
                    v                      v
             +----------+          +----------+
             | Page     |          | Page     |
             | Fetcher  |          | Fetcher  |
             +----------+          +----------+
                    |                      |
                    v                      v
             +----------+          +----------+
             | LLM      |          | LLM      |
             | Extract  |          | Extract  |
             +----------+          +----------+
```

The Orchestrator processes each record through a sequential pipeline (Stage 0 -> Preprocessing -> Tier 1 -> Tier 2 -> Tier 3), but multiple records within a batch run **concurrently** using `asyncio.Semaphore` for rate limiting.

---

## The Enrichment Pipeline: Stage by Stage

### Stage 0: Name1 Overflow Check (UC 0)

**File:** `enrichment/overflow_check.py`

**Problem:** Sometimes a single organization name is split across two adjacent Name fields because the name exceeds SAP's field length limit. For example:

| Upper slot | Lower slot | Actual Organization |
|-------|-------|--------------------|
| Adams Air | Hydraulics Inc | Adams Air Hydraulics Inc |
| Brigham and Women's | Hospital | Brigham and Women's Hospital |
| Department of Molecular | Biology and Genetics | Department of Molecular Biology and Genetics |

**Trigger:** Every adjacent pair in the name block — Name1/Name2, Name2/Name3, Name3/Name4, Name4/Name5 — where both fields are non-blank and not equal. The split can land at any slot boundary, so the check is not specific to the Name1/Name2 pair.

**Logic:** for each such pair,
1. Concatenate upper + " " + lower
2. Ask the LLM: "Does this read as a single continuous organization name?"
3. LLM returns `is_overflow` (boolean), `confidence` (high/medium/low), and `reasoning`

**Outcome:**
- If overflow detected with medium or high confidence: the record is **immediately flagged** and returned. No further tiers run. The flag reason explains the overflow so a human can correct the SAP field split.
- If not overflow: pipeline continues normally.

**The early return still finalises.** "No further tiers run" means no *enrichment* runs — it never meant the record skips finalisation. The return goes through `_finalise_and_return`, the single funnel, so an overflow record gets the same address stage and the same [finalisation rules](#finalization) as every other row: the empty-string guard, abbreviation expansion, unit canonicalisation and — since Fix 5 — [output casing](#rule-7--output-casing-normalisation). Its Name 1 and Name 2 are still the input values, split exactly as SAP had them; they are just cased. Row 33 of the demo batch ("Adams Air" + "HYDRAULICS INC") ships as "Adams Air" + "Hydraulics Inc", flagged, with no `ror_id` and no tier attempted.

Normalisation is applied here for the same reason it is applied everywhere else: a reviewer comparing an overflow row against the rest of the workbook should not have to discount a casing difference that says nothing about the record. Casing adds and removes nothing, so it cannot obscure the split the reviewer is being asked to fix.

**Why stop early?** If Name1 + Name2 is one org name, running Tier 1 on just Name1 would match the wrong entity (e.g., searching ROR for "Adams Air" instead of "Adams Air Hydraulics Inc").

---

### Stage 1: Preprocessing (UC 6-12)

**File:** `enrichment/preprocess.py`

Preprocessing runs entirely on regex patterns with **no network calls** (except one optional LLM call for ambiguous plain-name classification). It cleans misplaced data out of name fields before any enrichment begins.

#### UC 6 — Accounts Payable Normalization

**Detects patterns like:** "Accounts Payable", "A/P", "AP Dept", "AP Invoice", "Attn AP"

**Action:** Normalizes to "Accounts Payable" and flags the field. The orchestrator handles AP records specially — they are often not real organization names but payment routing labels. An AP reference that lands in a **street field** is treated as a department and routed to the first empty Name slot (usually Name 2) — see [Organisation/department content in a street field](#organisationdepartment-content-in-a-street-field).

#### UC 7 — Contact Person Extraction

Detects person names stored in Name1/Name2/Name3 fields and moves them to the `contact` field.

Detection patterns:
- **Pattern A:** Explicit prefix — `Attn: Jane Smith`, `c/o Dr. Robert Lee`
- **Pattern B1:** Title-prefixed names — `Dr. Jane Smith`, `Prof. John Doe`, `Mr. Robert Lee`. Trailing academic/professional credentials are stripped first, so `Dr. Jane Smith, PhD` still matches → contact `Dr. Jane Smith`.
- **Pattern B2:** Plain capitalized names (2-3 words, no title) — `Jane Smith`, `Robert Alan Lee`
  - This pattern is ambiguous: "Jane Smith" is a person, but "Bell Labs" is not
  - B2 triggers an **LLM classification call** (`llm_classify_plain_names_async()`) only when `allow_llm=True`
  - Names with organization signals (Inc, Corp, Department, University, etc.) are rejected before the LLM call
  - **Normalised candidates** — before classification, `find_suspicious_plain_names` also surfaces a normalised form of each name so credentialed and reversed formats reach the LLM: `Smith, John` → `John Smith` (reordered), `John Anderson, PhD` / `Jane Smith MD` → the bare name. When the verdict is "person", the normalised name is written to `contact`.

**Guard:** Any name containing organization keywords (Inc, Corp, LLC, Department, University, Hospital, Institute, Laboratory, etc.) is never classified as a person.

**Person in Name 1 → affiliation lookup.** When the person is extracted from **Name 1** (the institution slot), Name 1 is left empty and `PreprocessResult.name1_was_person` is set. The orchestrator then runs a **person-affiliation lookup** (`enrichment/person_affiliation.py`) that discovers the institution + department from the web and **confirms it against ROR in the record's country** before accepting it — so the record is not left with just a contact and no organisation, and a wrong-country guess is never written. See [Stage 2b](#stage-2b-person-affiliation-lookup).

#### UC 8 — Email Copy (Non-Destructive)

**Pattern:** Standard email regex: `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`

**Scans:** Name1, Name2, Name3, Street1, Street2, Street3.

**Action:** When an email is found, **copies** it to the `email` field. The source field (name or address) is **preserved as-is** — no characters are stripped. If `email` is already populated and matches, no action; if it's populated with a different address, the record is flagged for review (`email-conflict`).

#### UC 9 — Address Extraction

**Detects:** Street addresses in name fields.
- Street suffixes: St, Ave, Blvd, Rd, Dr, Ln, Way, Ct, Pl, Terr, Pkwy, Hwy, etc.
- Direction prefixes: N, S, E, W, NE, NW, SE, SW
- Unit identifiers: Suite, Ste, Unit, Apt, Room, Rm, Bldg, Floor, Fl
- PO Box patterns

**Action:** Extracts address fragments to `street1`/`street2`/`street3` fields. Residual text retained in name field.

#### Name 1 Acronym + Full-Form Dedupe

When Name 1 carries **both** an acronym and its expansion for the same entity, only the full form is kept (`_strip_redundant_acronym`). All four positions are handled — leading/trailing adjacent, and either side of a parenthetical:

- `MIT Massachusetts Institute of Technology` → `Massachusetts Institute of Technology`
- `Massachusetts Institute of Technology (MIT)` → `Massachusetts Institute of Technology`
- `MIT (Massachusetts Institute of Technology)` → `Massachusetts Institute of Technology`
- `University of California, Los Angeles (UCLA)` → `University of California, Los Angeles`

It fires only when the acronym is the **verified initialism** of the full form (initials of the significant words, skipping stopwords — so `UCLA` = University **of** California Los Angeles matches). Unrelated tokens are left untouched: `UC Berkeley`, `3M Company`, `AT&T`, `IT University of Copenhagen`, `US Army Corps of Engineers`.

#### Organisation/Department Content in a Street Field

SAP exports sometimes place organisation/department names in a Street field (with or without the real address). These are routed to the Name block, keeping only the address in the street:

- **Pipe-delimited** (`_split_pipe_street`) — `Bioanalytical Methods Branch | Division of Bioanalytical Chemistry | … | U.S. Food and Drug Administration | 5100 Paint Branch Pkwy` → the address segment stays in Street 1; each org/department segment is routed to a Name slot.
- **Comma-delimited, mixed** (`_split_comma_street`) — fires only when a value contains **both** a recognised org/department **and** an address (incl. German streets like `Scharnhorststraße 1`), so a plain address (`51 Sleeper Street, 7th Floor`; `200 Clarendon Street, Boston, MA 02210`) is never split. Example: `Institute of … Chemistry, Faculty of Sustainability, Scharnhorststraße 1 C13.217` → Name 1/2 = the org units, Street 1 = the German address.
- **Single-value org/department** (`_street_is_org_name` / `_street_is_department`) — an institution or department alone in a street field ("University of Miami Hospital", "Department of Neuroscience", "Accounts Payable") is moved to the Name block.

Routing rules:
- The **institution** always takes Name 1 when Name 1 is empty (`_looks_like_institution`); sub-units (`Division of…`, `… Branch`, `Center for…`) fill Name 2+.
- A **bare location fragment** that is neither an address nor an org (e.g. "Queens Campus") goes to the next empty **street** slot, not a Name field.
- **Overflow is flagged, never silently dropped** — when org segments exceed the five Name slots (or location fragments exceed the street slots), a `name-slots-full` / `street-slots-full` signal is raised, which finalisation turns into the `overflow` flag code. It is the same defect UC 0 detects from the other end: content the SAP field split could not place.

#### Address Sub-Location Extraction (Floor / Room / c-o)

The address stage (`enrichment/address_processing.py`) pulls sub-locations out of street values into their own fields:

- **Floor** — both `Floor 3` (marker-before-value) and `7th Floor` / `22nd Floor` (value-before-marker) are parsed → `floor`, and the orphan is no longer left dangling in the street.
- **Room** — `Room 12`, `Rm. 5`, `Room number: F107`, `Room No. 3`, `Room #4` (filler words `number`/`No.`/`#`/`:` are skipped) → `room`.
- **c/o and Attn** — the capture stops at the start of a street address, so `Att. Bayard Huck 200 Clarendon Street 22nd Floor` → `care_of` = `Bayard Huck`, Street 1 = `200 Clarendon St`, `floor` = `22` (rather than swallowing the whole thing into `care_of`).

#### UC 12 — Duplicate Name Field Clearing

**Detects:** Identical (case-insensitive, whitespace-tolerant) values across adjacent name fields:
- `Name1 == Name2` → clears Name2
- `Name2 == Name3` → clears Name3

Runs **last** in preprocessing so it sees post-normalization values — e.g. inputs `Name1="Accounts Payable Dept"` and `Name2="Accounts Payable"` are both collapsed to "Accounts Payable" by UC 6 and then deduped here.

**Action:** Silently clears the duplicate. No flag for review is added — duplicate clearing is informational only and recorded in `use_cases_triggered = [12]`.

#### UC 11 — DBA Normalization

**Detects:** "Doing Business As" markers in any form inside Name1/Name2/Name3.

Recognized variants (case-insensitive):
- Full phrase: `Doing Business As`
- Truncated/typo'd phrase: `D Business As`, `D. Business As`
- Acronym with dots: `D.B.A.`, `D. B. A.`
- Acronym with slashes: `D/B/A`, `D / B / A`
- Plain or spaced acronym: `DBA`, `dba`, `D B A`

**Action:** Replaces the matched variant in-place with the canonical `DBA`. The rest of the field is preserved. Examples:
- `Acme LLC Doing Business As Wonder Widgets` → `Acme LLC DBA Wonder Widgets`
- `ABC Inc D/B/A Pinnacle` → `ABC Inc DBA Pinnacle`
- `D.B.A. Smith Trading` → `DBA Smith Trading`

#### UC 10 — Opaque Code Detection

**Detects:** Internal SAP codes or ID numbers stored as names.
- Pure digit strings (5+ digits): `800000070`
- Letter prefix + digits: `SAP-123456`, `B800000070`
- Alphanumeric codes: `CUST-2024-00145`

**Action:** Flags the field as containing an opaque code, not a valid organization name. Prevents the pipeline from trying to "enrich" a code number into an organization name.

---

### Stage 2: Tier 1 — ROR API Lookup

**File:** `enrichment/tier1_ror.py`

The [Research Organization Registry (ROR)](https://ror.org/) is a free, open registry of research organizations worldwide. Tier 1 queries ROR to find the official name for the organization in Name1.

#### Hybrid Lookup Strategy

ROR offers two endpoints with different strengths:

1. **Affiliation endpoint** (`?affiliation=...`) — first choice
   - Query: `{name1}, {city}, {state}, {country}`
   - Strengths: Handles abbreviations ("MIT" -> "Massachusetts Institute of Technology"), misspellings, partial names
   - Weaknesses: Sometimes returns low-confidence matches or misses entirely

2. **Query endpoint** (`?query=...`) — fallback
   - Query: `{name1}` with country filter
   - Full-text search with local scoring
   - Used when affiliation endpoint has no confident hit

The client tries the affiliation endpoint first. If the best score is below the confidence threshold (default 0.8), it falls back to the query endpoint.

#### Caching

Lookups are cached in the module-level `_ror_cache` (cleared per batch), keyed by `utils.cache.lookup_key(name, country_code)` — the name normalised (lowercase, trim, collapse whitespace, strip punctuation, fold accents) plus the country filter. So `Coastal Diagnostics, Inc.` and `Coastal Diagnostics Inc` are one entry and one API call, while `Bruker GmbH` / `Bruker AG` and `Uni Stuttgart` / `University of Stuttgart` stay apart. **The key is a dictionary key only** — the unnormalised `name` is what reaches ROR and what `_compute_name_score()` sees. See [`utils/cache.py`](#utilscachepy--cache-keys--batch-cache) for the full contract. `ror_normalised_hits()` reports how many lookups the normalised key saved.

#### Re-lookup after canonicalisation

ROR runs before the pipeline knows the organisation's real name. When ROR misses on the input spelling and a later tier works the name out — `company_canonical`, Tier 3, Tier 2A or Tier 2B writing a new `name1_enriched` — `orchestrator._retry_tier1_after_canonicalisation` looks **that** name up, once. Without it a record ended with the correct official name sitting in `name1_enriched` and no registry id attached, even though the corrected string resolves in ROR on the first try. (`person_affiliation` already re-entered Tier 1 this way; the company-canonicalisation and Tier 3 paths were terminal.) Full description in [Stage 5: Tier 1 re-lookup after canonicalisation](#stage-5-tier-1-re-lookup-after-canonicalisation).

#### Country Guard

When the record's ISO country is known, a candidate whose ROR location country (`locations[0].geonames_details.country_code`) doesn't match it is **rejected**, on **both** strategies. This is required because:

- The **affiliation** endpoint's scorer often ignores the country context in the affiliation string and returns a confident same-name org from the wrong country — e.g. for "BASF SE, Ludwigshafen, DE" it returns the **US** "BASF" (`ror.org/002yzpx87`, score 1.0). The guard rejects it and the lookup falls through to the country-filtered query endpoint.
- The **query** endpoint applies a server-side country filter, but on 0 results it retries **without** the filter — so the guard re-filters the candidate set (before ranking) to keep that retry from admitting a wrong-country org.

A wrong-country ROR id is worse than none: it would wrongly converge distinct entities (e.g. BASF Germany and BASF US) in Phase 2 dedup. On a rejection the record misses and falls through to the LLM path, exactly like any other miss. The comparison is case-insensitive; with no resolvable country, no country filtering is applied.

#### Name Scoring Logic

The scoring system (`_compute_name_score()`) is carefully designed to prevent false matches:

1. **Exact match** -> score 1.0 (any ROR name variant)
2. **Token-subset match** -> score 1.0 (all 4+ character tokens from the query appear in a canonical ROR name)
3. **Substring match** -> score 1.0 (query is >90% the length of a canonical name and is contained within it)
4. **Fuzzy token sort ratio** (0.0 to 1.0):
   - Only compared against canonical names (not short aliases, which cause false positives)
   - **Distinctive-token guard:** the scorer requires **every distinctive token** of the query (`_DISTINCTIVE_TOKEN_MIN_LEN` = **4+ characters**, not a generic domain word such as regional/health/medical/center/research/services, not a city/state token) to appear in the matched variant. Any that is missing caps the fuzzy score at 0.7, below the 0.8 match threshold.
   - Example: "Newman Regional Health" has distinctive token "newman". "Lakeland Regional Health" does not contain "newman", so the fuzzy score is capped at 0.7 even if the generic words produce a high fuzzy ratio.
   - **The floor is 4, not 5.** At five, every organisation whose distinguishing word is four letters — Acme, Duke, Yale, Mayo, Ohio, Iowa — was exempt from the guard *entirely*: the discriminating token was invisible to the distinctive set, so nothing was left to check and the raw fuzzy ratio stood on the shared generic word alone. "Acme Biotech" (Tampa FL) scored **0.87** against ROR's **"AUM BioTech"** (`ror.org/0106fnq84`, Philadelphia PA) on `biotech` alone and was written as a verified Tier 1 match — Name 1, `ror_id` and `aumbiotech.com` together, unflagged, because a [registry-supplied domain bypasses the ownership guard](#domain-ownership-guard) that was tuned to reject that exact domain on the web path. Four matches the `4+` used by the token-subset rule and by `_extract_location_tokens`; the guard was the only length test in the module that disagreed. Dropping it pulls generic four-letter tokens into scope, so legal forms (`gmbh`, `kgaa`, `sarl`) and the abbreviations coverage cannot bridge because they are not prefixes of their expansion (`labs` ↛ `laboratories`, `intl` ↛ `international`) are named in `_COMMON_DOMAIN_WORDS`; ones that *are* prefixes (`univ`, `inst`, `hosp`, `dept`, `tech`) need no entry.
   - Sharing *one* distinctive token is not enough. "Coastal Analytical Services" and "Analytical Services" (ANSER, `ror.org/04g2rbh88`, Falls Church VA) share `analytical` and token-sort to ~0.83, yet the query's leading `Coastal` — the token that says *which* organisation — appears nowhere in the candidate. Likewise "Belharra Therapeutics" was matching "Carrick Therapeutics" (`ror.org/021n8pt68`) on the shared trade word. This is the non-acronym twin of the identifier-token guard below; "EMSL"/"ASL" are caught there because they are short and capitalised, "Coastal" and "Belharra" are not.
   - "Covered" is `_fuzzy_token_covers`, **not** exact set membership, so the guard does not undo what fuzzy matching is for. A prefix (`univ` ↔ `university`) and a typo (`insitute` ↔ `institute`, `Lüneborg` ↔ `Lüneburg`) still count as covered; only a token with no counterpart at all caps the score. This matters more since the [name write became unconditional](#registry-names-are-authoritative): a false-positive match now overwrites Name 1 rather than merely adding an id, so the match guard is the only thing standing between a wrong candidate and a wrong output name.
   - This prevents the common false-positive pattern where organizations with similar generic names (many hospitals, regional health systems, community colleges) match each other.
5. **Legal-form normalization:** before scoring, `_normalise_for_tokens()` strips `.`/`,` and canonicalizes legal-entity suffixes (Incorporated→inc, Corporation→corp, Company→co, Limited→ltd, "L.L.C."→llc, "Limited Liability Company"→llc, …) **symmetrically** on the query and every ROR name variant. So "Acme Corp.", "Acme Corp" and "Acme Corporation" all compare equal, and two SAP rows that differ only by legal form don't diverge (one matching ROR, the other missing).
6. **Identifier-token (acronym) guard:** short all-caps acronyms in the query (e.g. "HFT", "EMSL", "ASL") must appear in the candidate before the exact/subset/substring shortcuts can score 1.0. Without it, "HFT Stuttgart" would subset-match *any* "… Stuttgart" org on the shared city token alone (Marienhospital Stuttgart, Stuttgart Observatory) and produce a confidently wrong match.

#### Institution Acronym Expansion

Some institutions are referenced by an acronym that ROR does **not** carry as an alias — e.g. "HFT Stuttgart" (ROR has no "HFT" alias, so the bare query returns unrelated same-city orgs). A small ROR-local map (`_INSTITUTION_ACRONYMS`, e.g. `HFT → Hochschule für Technik`) drives an **additive** affiliation retry: when the raw name misses, the affiliation endpoint is tried once more with the acronym expanded ("HFT Stuttgart" → "Hochschule für Technik Stuttgart"). It is kept out of the global `expand_abbreviations` map so it never affects search terms or output names, and names that already resolve never reach it. Extend the map as new institution acronyms come up.

Keys may be **multi-token** (`GA Tech → Georgia Institute of Technology`); the match regex is built from the map's own keys, longest first, so only known acronyms can fire. `GA Tech` is deliberately owned here rather than by the [bounded two-letter pattern](#us-state-abbreviation-expansion-ror-local) below: that pattern would produce "Georgia Tech", which ROR resolves to **Georgia Tech Foundation** (`ror.org/00adhzq59`) — a different legal entity from the university (`ror.org/01zkghx44`). Mapping the whole phrase to the full official name resolves the institute directly. `VA Tech` needs no entry: `Virginia Tech` *is* ROR's display name for `ror.org/02smfhw86`, so the bounded pattern resolves it correctly on its own.

Whatever the map expands is **never** what ships. The expansion exists to find the ROR record; once found, [the registry's official name is what is written](#registry-names-are-authoritative) — "HFT Stuttgart" outputs ROR's official name, not "Hochschule für Technik Stuttgart" assembled locally.

#### US State-Abbreviation Expansion (ROR-local)

A US state-name abbreviation in the query is expanded before the ROR lookup (`_expand_state_abbrevs`, `_US_STATE_ABBREVS`): `Fla State Univ` → `Florida State Univ`, `Wash State Univ` → `Washington State Univ`, `Penn State Univ` → `Pennsylvania State Univ`. Without this, the distinctive geographic token is lost and the query `… State University` matches **any** "_ State University" — e.g. `Fla State Univ` was resolving to **Kent State University** (whose only shared tokens are the generic `State`/`University`). Like the acronym map, this is applied **only** when building the ROR affiliation string / query / local rescore — never in the global `expand_abbreviations`, so output names and search terms are untouched.

**Two-letter postal codes: bounded, not general.** Bare `FL`, `IN`, `OR`, `ME` remain excluded from `_US_STATE_ABBREVS`, and that general exclusion still stands — on their own these are ordinary English words, and expanding them wholesale turns "IN Laboratories" into "Indiana Laboratories" and "OR Diagnostics" into "Oregon Diagnostics". What was added instead is a **closed context**: a two-letter code in `_US_POSTAL_CODES` is expanded only when the tokens *immediately* following it are one of

| Context | Example |
|---|---|
| `State Univ` | `FL State Univ` → `Florida State Univ` |
| `State University` | `IN State University` → `Indiana State University` |
| `Tech` | `TX Tech` → `Texas Tech`, `VA Tech` → `Virginia Tech` |
| `Institute of Technology` | `NJ Institute of Technology` → `New Jersey Institute of Technology` |

Inside that context the collision the exclusion guards against does not arise: "IN State Univ" and "OR State Univ" are unambiguous in a way that bare "IN" and "OR" are not. Outside it nothing fires — `IN Laboratories`, `OR Diagnostics`, `State Univ of IN` and a bare `OR` all pass through untouched.

Two carve-outs inside the context:

- **Word-like codes** (`HI`, `IN`, `OR`, `OK`, `ME`, `LA`, `DE`) are allowed before `State Univ…` — "Hi State University" names nothing — but held back from the bare `Tech` contexts, where "Hi Tech" and "In Tech" are real company names.
- A phrase owned by [`_INSTITUTION_ACRONYMS`](#institution-acronym-expansion) is left to that map's retry, which expands it to an exact official name. This is why `GA Tech` does *not* become "Georgia Tech" here.

This stays ROR-local exactly as the rest of the map does: affiliation string, query and local rescore only, never an output name and never a search term.

#### Child Matching

Once Tier 1 matches a parent organization, it attempts to match Name2 and Name3 against the ROR **children list** (related organizations of type "child"):

- This is done **locally** — no second API call
- Uses `rapidfuzz.fuzz.token_sort_ratio()` with a threshold of 70%
- If a child matches, the official child name replaces the input Name2/Name3
- Example: Name2 = "Dept of Chem" matches child "Department of Chemistry" at the parent "Stanford University"

#### Classification from ROR Types

Rather than using keyword heuristics ("University" -> research institution), classification starts from the ROR organization's declared types:

```
ROR types: education, healthcare, government, facility, nonprofit, archive, other
  -> research_institution

ROR type: company
  -> company
```

ROR sets `routing_type` immediately, so the rest of the run is gated correctly, and records the same verdict as the top-ranked evidence for the final decision. If Tier 1 misses, classification falls through to GLEIF entity metadata and then the keyword heuristic — see [Record Classification Logic](#record-classification-logic). The ROR mapping itself is unchanged.

#### Caching

A module-level ROR cache (`_ror_cache`) prevents duplicate API calls within a batch. For example, if a batch contains three records for "MIT", only one ROR API call is made. The cache is cleared between batches.

#### TLS

The ROR client uses the shared `resolve_tls_verify()` helper (corporate CA bundle → certifi fallback) so it survives a TLS-inspecting corporate VPN. Before this, ROR hardcoded `verify=certifi.where()`, so on such a VPN **every** ROR call failed the handshake and silently returned no match — leaving `ror_id`/`domain` null and pushing every record to the LLM. See [TLS and Corporate VPN](#tls-and-corporate-vpn).

---

### Stage 2 (Company): Tier 1 — GLEIF / LEI Registry Lookup

**File:** `enrichment/tier1_lei.py`

ROR is the registry for *research institutions* and has no good coverage of ordinary companies. The [GLEIF API](https://www.gleif.org/) (Global Legal Entity Identifier Foundation — free, no auth, JSON:API) is the **company counterpart**: for company-type records it resolves the official legal name and a **Legal Entity Identifier (LEI)** *before* falling back to LLM company canonicalization. This is what lets the Phase 2 dedup converge records on a shared `lei_id` (e.g. "Pfizer AG" / "Pfizer").

**When it runs (company branch only):**
- ROR miss **and** the name doesn't look like a research institution, **or**
- ROR matched the record as a company.
- It **never** runs on, or overwrites, a record ROR confidently matched as a research institution — ROR's institution result wins; LEI is the company counterpart, not a competitor.

**Order on the company branch:** ROR → **LEI (new)** → LLM company canonicalization (existing fallback).

**Lookup strategy (mirrors the ROR client):**

1. **Precise filter:** `lei-records?filter[entity.legalName]=<name>&filter[entity.status]=ACTIVE&filter[entity.legalAddress.country]=<ISO2>`. The record's ISO country (from `country_to_iso_code`) narrows results. Note GLEIF's `legalName` filter is *fulltext*, not exact — "Pfizer" returns "PFIZER AG", "PFIZER INC.", etc. — so the verification guard below is mandatory even on this "precise" path.
2. **Fuzzy fallback:** `fuzzycompletions?field=entity.legalName&q=<name>`, then resolve each candidate to its full `lei-record`. Best-effort — GLEIF's typeahead frequently returns nothing, which is a normal miss.

**Field mapping:** `data[].id` → LEI · `data[].attributes.entity.legalName.name` → official name · `…entity.status` (ACTIVE) · `…entity.legalAddress.country` (ISO alpha-2).

**Caching:** the module-level `_lei_cache` (cleared per batch) mirrors the ROR client's and shares its key builder — `utils.cache.lookup_key(name, country_code)`. This namespace already carried the country; what it gained is the punctuation/accent collapse, so `Lockheed Martin Corp.` and `Lockheed Martin Corp` cost one GLEIF call rather than two. The key never reaches `_name_match_score()` — the verification guard below always scores the original string.

**Verification guard (required):** every candidate's `legalName` is scored against the input with RapidFuzz `token_sort_ratio` (case-folded — GLEIF returns names UPPERCASE; legal-form suffixes like AG/Inc/Ltd/GmbH stripped so "Novartis" verifies against "NOVARTIS AG"). Candidates below `LEI_NAME_MATCH_THRESHOLD` (default 88) are rejected. GLEIF fuzzy is statistical — without this guard it fabricates matches (e.g. "Personalvorsorgestiftung der Pfizer AG in Liquidation" for "Pfizer AG"). `token_set_ratio` is deliberately **not** used: it scores any contained substring 100 and would accept that wrong entity.

**Country guard (required):** when the record's ISO country is known, candidates whose `legalAddress.country` does not match it are rejected during selection — on **both** the precise and fuzzy paths. This matters because GLEIF's `filter[entity.legalAddress.country]` only constrains the *precise* request, and the `fuzzycompletions` typeahead **cannot be country-filtered at the API at all** — so without this post-filter the fuzzy path would happily return a same-name company from the wrong country (e.g. a US "PFIZER AG" for a Swiss record), which is a fundamentally different legal entity. The comparison is case-insensitive; when the record has no resolvable country, no country filtering is applied.

**On a verified match:**
- `name1_enriched` ← official GLEIF `legalName`
- `lei_id` ← the LEI · `source = "gleif"` · `tier_used = 1` · `routing_type = "company"`
  - **A LEI hit does NOT set `record_type = "company"`.** It records `entity.category` / `entity.legalForm` as classification evidence and lets `finalise` decide; an LEI proves legal registration, not commercial status. See [the LEI guard](#the-lei-guard).
- `confidence = high` (precise filter) / `medium` (fuzzy)
- `domain` stays `null` — GLEIF has no website field. Downstream web-search tiers that need a domain simply won't have one for these; that's acceptable.

**On miss / below-threshold / timeout / API error:** nothing is fabricated — the record falls through to the existing LLM company-canonical path unchanged. **A GLEIF failure never fails the record.**

**Feature flag:** `LEI_LOOKUP_ENABLED` (default `true`) disables the whole step for cheap A/B testing — behaviour then reverts to LLM-only, identical to before.

**Telemetry:** `lei_attempts`, `lei_hits_exact`, `lei_hits_fuzzy`, `lei_misses`, `lei_errors`, and `tier1_lei_count` in the batch `summary`.

**Caching & TLS:** a module-level `_lei_cache` (keyed on name + country) dedupes calls within a batch, cleared per batch like the ROR cache. The client uses the shared `resolve_tls_verify()` so it survives a TLS-inspecting corporate VPN.

---

### Stage 2b: Person Affiliation Lookup

**File:** `enrichment/person_affiliation.py`

**Trigger:** Name 1 held **only a person's name** (moved to `contact` by [UC 7](#uc-7--contact-person-extraction), leaving Name 1 empty — `PreprocessResult.name1_was_person`). Runs right after preprocessing, **before** Tier 1.

**Why:** the normal tiers need an institution to enrich — Tier 1 (ROR) needs a name, Tier 2A (contact lookup) needs a *known* institution + domain. A record that is just a person name has neither, so without this step the person moves to `contact` and the organisation is never fetched. Fetching the contact's institution + department is a core deliverable.

**How (with hard reliability guards):**
1. **Propose** — one grounded web lookup: SERP on the person (`"Jane Smith" mit.edu` when the email domain is corporate/edu, else `"Jane Smith" <city, region, country>`), then a single LLM extraction over the result **snippets only** (`PERSON_AFFILIATION_*`) returning `{institution, department, confidence}`. The prompt is grounded — it must return `institution=null` rather than guess from the name alone. `enrichment/person_affiliation.py` never writes any field.
2. **Confirm** — the proposed institution is verified against **ROR in the record's country**. ROR's country filter rejects a wrong-country match (e.g. an Irish university proposed for a Belfast/GB address).
3. **Accept** — only on a ROR match: Name 1 = **ROR's official name**, and the **id / domain / website come from ROR** (never a website-resolver guess). Department = a Tier 2A lookup on the *confirmed* domain, falling back to the web-proposed department. `source = "ROR"`, `confidence = "medium"`, and **no flag**: the proposal went through ROR's own country and distinctive-token guards, so this is a verified Tier 1 match like any other, and it ships a registry id and domain a reviewer can audit.
4. **Fail safe** — no proposal, low confidence, or ROR does not confirm → the contact is kept, Name 1 is left **empty**, and the record is flagged `person-unresolved` (scoped to `name1`).

In **all** cases the pipeline **short-circuits after Stage 2b** (Tier 3 never runs for person-only records), so Tier 3 can neither fabricate an institution nor overwrite the ROR-confirmed one. Cost is ~1 SERP + 1 LLM (+ Tier 2A on accept) and is incurred **only** for person-only Name 1 records.

---

### Stage 3: Tier 2 — Multi-Mode Canonicalization

Tier 2 has three sub-modes that handle different scenarios for enriching Name2 (department/division). The orchestrator selects the appropriate mode based on what data is available.

#### Tier 2A: Contact Person Lookup

**File:** `enrichment/tier2a_contact.py`

**Trigger conditions (all must be true):**
- Tier 1 matched the parent organization (we know the domain)
- Contact person is available
- Institution has a known domain (from ROR)
- Contact field names exactly **one** person (multi-contact strings like "John Smith and Jane Doe" skip Tier 2A and flag the record for manual review)

**Manual-review guard (multi-contact only):** when the contact field names more than one person, Tier 2A cannot pick a page to verify against, and the record carries `multiple-contacts` (scoped to `contact` + `name2`) unless some later step settles the department anyway. A research institution with **no department and no contact** is *not* flagged: an absent department is not a defect, and there is nothing for a reviewer to do. That blanket rule was 20 % of all flags on the demo batch and was removed in Fix 8 — see the [Flag Rules table](#flag-rules).

**Two modes:**

| Mode | Trigger | Goal | Example |
|------|---------|------|---------|
| **Mode A (Population)** | Name2 is null/blank | Discover the contact's department from their faculty page | Contact "Dr. Jane Smith" at MIT -> find she's in CSAIL |
| **Mode B (Verification)** | Name2 already exists | Verify or correct Name2 against the contact's page | Name2="Dept of AI" + Contact "Dr. Jane Smith" -> verify against her actual affiliation |

**Process flow:**

1. **Build SERP queries:**
   - `"Jane Smith" site:mit.edu`
   - Variations with/without department name, abbreviated department

2. **Search and rank candidates:**
   - Score results for "people page" signals in URL and snippet
   - URL signals: `people`, `faculty`, `staff`, `person`, `profile`, `directory`, `team`, `researcher`, `member`, `bio`
   - Snippet signals: `professor`, `researcher`, `scientist`, `department`, `phd`, `principal investigator`
   - Top 3 candidates proceed to page fetch

3. **Name verification filter:**
   - Every candidate must contain BOTH the first name and surname of the contact in its URL, title, or snippet
   - Handles slugified forms: `sarah-chen`, `sarah_chen`, `sarahchen`
   - This prevents false matches — e.g., a page about "Jane Doe" doesn't match contact "Jane Smith"

4. **Page fetch and structured extraction:**
   - Fetch the HTML page
   - Extract: URL host, URL path, page title, H1 heading, breadcrumb navigation, body text
   - Prepend structured elements to the body so the LLM sees them first (canonical names are often in titles and breadcrumbs)

5. **LLM extraction** (`_extract_affiliation()`):
   - The LLM extracts: `person_found`, `official_dept`, `official_group`, `title`, `confidence`
   - Rules enforced in the prompt:
     - Expand URL abbreviations to full forms
     - Prefer "Department of X" construction
     - Reject generic roles (Research, Admin, Staff)

6. **Post-processing:**
   - **Canonicalize** bare subject names: "Anesthesia" -> "Department of Anesthesia"
   - **Scope filter (UC 4):** Reject granular units (individual labs, research groups, facilities). The pipeline targets department-level or higher.
     - Accepted: "Department of Chemistry", "Division of Engineering", "School of Medicine"
     - Rejected: "Smith Laboratory", "Center for Quantum Computing", "Imaging Facility"
     - Exception: "Department of Pathology, Laboratory Medicine" is accepted because "Laboratory Medicine" is a discipline, not a physical lab

7. **Mode-specific output:**
   - **Mode A:** Populates `name2_enriched` with the discovered department
   - **Mode B:** Compares input Name2 against extracted department:
     - Fuzzy match >= 95% -> `match_result = "exact"`, `status = "verified"`
     - Fuzzy match >= 60% -> `match_result = "partial"`, `status = "enriched"`, flagged for review
     - Fuzzy match < 60% -> `match_result = "no_match"`, flagged for review with correction

#### Tier 2 Canonical (LLM-only, no web search)

**File:** `enrichment/tier2_canonical.py`

**Trigger:** Name2 or Name3 is present, Tier 1 matched the parent, but no ROR child match was found.

**Purpose:** Normalize informal department names to the institution's official wording using the LLM's knowledge — without making any web search calls.

**Examples:**
- "Dept of Chem" at "Stanford University" -> "Department of Chemistry"
- "EE" at "MIT" -> "Department of Electrical Engineering and Computer Science"
- "Ortho" at "Johns Hopkins Hospital" -> "Department of Orthopaedic Surgery"

**Confidence handling:** Only accepts `high` confidence answers from the LLM. Medium or low confidence results are discarded (the original Name2 passes through unchanged). This conservative approach avoids hallucinated department names.

**Scope filter (UC 5):** Same granular-unit rejection as Tier 2A.

#### Tier 2 — UC 13: Lab → Parent Department Resolution

**File:** `enrichment/lab_resolver.py`

**Trigger (all must be true):**
- Tier 1 matched the parent organization (we know the institution and its domain)
- `record_type == "research_institution"`
- Input `Name2` is a granular unit (lab, research group, centre, core, or facility) per `is_granular_unit()`

**Why:** A granular unit is too low in the academic hierarchy for MDM purposes — but the institution's own website almost always documents the **parent department** in the URL path, breadcrumb, or page title of the lab's page.

**Process flow:**
1. **SERP query (on-domain only):** `"<lab name>" site:<institution domain>` — only on-domain results are considered.
2. **Page fetch:** Top 3 on-domain candidates → fetch URL path, title, H1, breadcrumb.
3. **LLM extraction:** Asks for the parent academic department, with the prompt restricting valid answers to "Department of X", "Division of X", "School of X", "College of X", "Faculty of X", or "Institute of X" — never another lab/group/centre.
4. **Best-pick:** Highest confidence (high > medium > low), then longer name, then alphabetical.

**Outcome (success):**
- `Name2_enriched` ← parent department (e.g. `"Department of Chemistry"`)
- `Name3_enriched` ← original lab name (e.g. `"NMR Spectroscopy Group"`) **only when input Name3 was empty**
- If input Name3 was already populated, the lab is demoted into the next free slot below it (Name 4, then Name 5) so no value is overwritten. Only when **every** slot below Name 2 is occupied is the lab **not** demoted (data-loss avoidance), and the record then additionally carries `name3-not-demoted`.
- `tier_used = 2`, `source = "dept_search"`, `source_url` = the page used, `use_cases_triggered` includes `13`, and the record carries `dept-via-lab` scoped to `name2` + `name3` — the parent department was *inferred from the lab's own page*, not read from a stated department, which is what makes it a claim a reviewer has to check.

**Outcome (failure):** Falls through to existing Tier 2 canonical / Tier 2A / Tier 2B / Tier 3, whose existing scope filters keep the original granular Name2.

**Examples:**
- Input `Name1="MIT", Name2="NMR Spectroscopy Group"` → Output `Name2="Department of Chemistry", Name3="NMR Spectroscopy Group"`.
- Input `Name1="UCLA", Name2="Smith Laboratory"` → Output `Name2="Department of Chemistry and Biochemistry", Name3="Smith Laboratory"`.
- Input `Name1="Stanford", Name2="Bio-X Center", Name3="Existing Group"` → Output `Name2="Department of Chemistry", Name3="Existing Group"` (lab not demoted; flagged).

#### Tier 2B: Department Search

**File:** `enrichment/tier2b_dept.py`

**Trigger:** Name2 is present AND (no contact available, or Tier 2A failed, or record is a company).

**Process flow:**

1. **Build SERP queries:**
   - Research institution: `"Stanford University" "Chemistry Department" site:stanford.edu`
   - Company: `"Pfizer" "Analytical Sciences"`
   - Variations with expanded abbreviations ("Dept" -> "Department")

2. **Rank candidates:**
   - **On-domain first** (results from the institution's own website get highest priority)
   - External authoritative sources second (government, education)
   - Non-authoritative sources last

3. **Page fetch and structured extraction:**
   - Extract: URL path, page title, H1, breadcrumb
   - Same `PageFetcher` as Tier 2A

4. **LLM extraction (structured elements only):**
   - The LLM extracts `official_name` from ONLY: URL path, title, H1, breadcrumb
   - The prompt explicitly forbids interpreting body text — this is a key design decision to prevent hallucination from noisy web page content

5. **Deterministic best-selection:**
   - Collect successful extractions from top-3 candidates
   - Rank by: (on_domain, fuzzy_match_to_input, name_length, alphabetical)
   - Pick highest-ranked result
   - Assign confidence: `medium` if source is on-domain, `low` if external

**Note:** a Tier 2B result that read a **stated** department off a page on the organisation's own domain is **not** flagged. `source_url` names the exact page, so the evidence is auditable — which is the whole test the flag model applies. (Before Fix 8 every Tier 2B result was flagged on the general principle that web evidence is weaker than ROR; that principle is now expressed through `confidence` — `medium` on-domain, `low` off-domain — rather than through a flag that gave a reviewer nothing specific to do.)

---

### Stage 4: Tier 3 — LLM Inference (Last Resort)

**File:** `enrichment/tier3_llm.py`

**Trigger:** All previous tiers failed or were not applicable.

**Process:** A single LLM call receives ALL available fields — Name1, Name2, Name3, contact, email, city, state, country, street — and attempts to infer the correct organization and department names.

**Confidence handling:**
- **High or medium confidence:** Write LLM suggestions to enriched fields, mark status as `unresolved`
- **Low confidence:** Do NOT overwrite originals — return originals unchanged, mark as `unresolved`

**Why is anything Tier 3 writes flagged?** Tier 3 has no external evidence — it relies entirely on the LLM's training data. So **every value Tier 3 writes carries `unverified-inference`, regardless of the confidence it reported**: a confident unverifiable claim is the more dangerous case, not the safer one. (The demo batch bears this out — rows 41, 44, 47 and 50 are Tier 3 outputs that were confident and wrong.) Two things narrow it, and both are evidence rather than provenance:

- a field a later authority overwrote is no longer Tier 3's claim — Fix 2's Tier 1 retry writing the registry's official name is the common case;
- a Name 2 the department probe went on to locate on the organisation's own web presence is corroborated by `department_domain`, a column a reviewer can open.

Where Tier 3 **leaves** a value unchanged there is no new claim, and the record reads as `low-confidence-unchanged` (or `no-match`) instead. Tier 3 itself raises no flag: it reports its suggestions and its confidence, and `enrichment/flags.py` decides what that means once the record is final.

---

### Finalization

**Function:** `finalise()` in `enrichment/orchestrator.py`

After all tiers have run, the finalization step applies a set of deterministic rules:

1. **Empty string guard:** Enriched fields must be either `None` or a non-empty string. Empty strings (`""`) are converted to `None`.

2. **Abbreviation expansion on output names:** Name 1 through Name 5 are run through the **global** `expand_abbreviations()` map, so no output name ships a bare `Univ`, `Dept`, `Grp`, `Svcs` or `Inst`:
   - "FL State Univ" -> "FL State University"
   - "Cardinal Research GRP" -> "Cardinal Research Group"
   - "Coastal Analytical Svcs" -> "Coastal Analytical Services"
   - **Exception — a name written from a registry is skipped.** See [Registry names are authoritative](#registry-names-are-authoritative) below.
   - Only the global map is used here. The ROR-local [`_INSTITUTION_ACRONYMS`](#institution-acronym-expansion) / [`_US_STATE_ABBREVS`](#us-state-abbreviation-expansion-ror-local) maps exist to improve ROR *resolution* and never touch an output name or a search term.

3. **Unit canonicalization:** Academic unit names are normalized to standard forms:
   - "Dept of Chemistry" -> "Department of Chemistry"
   - "Chem Division" -> "Division of Chemistry"
   - Exception: Granular units (labs, groups, facilities) are NOT canonicalized

4. **Passthrough logic:** If no tier enriched a field AND preprocessing didn't clear it, the original value is retained. The pipeline never blanks out a field that it couldn't improve.

5. **Changed flags:** `name1_changed`, `name2_changed`, etc. are set to `True` only when `enriched != original AND enriched is not None`. This allows consumers to know exactly which fields were modified. A registry name write goes through this same rule — it is recorded by the flag, never gated by it.

   **A casing-only difference is not a modification.** Rule 7 cases every free-text output field on every record, so counting casing here would set the flag on most rows and reduce it to "this field is non-empty". The comparison therefore ignores letter case: `Mayo Clinic FLA` → `Mayo Clinic in Florida` is `True`; `GAINESVILLE MEDICAL` → `Gainesville Medical` is `False`. The flag keeps its documented meaning — *this field was enriched*. See [Why casing does not set a changed flag](#why-casing-does-not-set-a-changed-flag).

   Thirteen fields carry a changed flag: Name 1–5, Care Of, Contact, Email and Street 1–5. They are **internal** — `EnrichmentResult` does not declare them, so they are dropped at the model boundary and appear in neither the JSON response nor the file export. Their consumers are the test fixtures and `scripts/test_local.py`. City and PO Box have no changed flag at all.

6. **Deduplication rules:**
   - **Rule 1:** If Name2 was blank in input AND no tier populated it AND no contact was available, set `name2_enriched = None` (don't fabricate)
   - **Rule 2:** If preprocessing stripped Name2 (e.g., it was an email address), don't let Tier 3 fabricate a replacement
   - **Rule 3:** If `name2_enriched == name1_enriched`, drop Name2 (no echo of the parent org name)

7. **Output casing normalisation:** every free-text output field is cased token by token. See [Rule 7](#rule-7--output-casing-normalisation) below — it is the longest of these rules and has its own section.

#### Rule 7 — Output casing normalisation

**Function:** `normalise_output_fields()` in `enrichment/orchestrator.py`, over `normalise_case()` in `utils/text_utils.py`.

Before this rule, casing was applied by whatever happened to touch a field. `smart_title_case()` ran on Name 1 and nowhere else, and it is a **whole-string** rule — it refuses any value that is not entirely upper-case. Everything else was cased by accident or not at all. On a 500-record run, **342 output values shipped fully upper-case**: 230 cities ("GAINESVILLE"), 107 streets ("MAIN ST"), a PO box. Values the street-suffix map had *partly* corrected shipped half-cased — `STREET_TYPE_ABBREVIATIONS` rewrote "DR" to "Dr" and left the rest, giving "500 TECH Dr MS-4". The same run after the rule leaves **4**, and two of those are deliberate acronyms (`ABX-CRO`, `UCSF`).

**One function, every exit path.** A record can leave the orchestrator four ways: the normal return, the [UC 0 overflow early return](#stage-0-name1-overflow-check-uc-0), the `_enrich_single` error path, and the batch-level fail-safe in `enrich_batch` that builds a result for a record whose task raised outright. The first three funnel through `_finalise_and_return` → `finalise`, which calls the normaliser last. The fourth never reaches `finalise` — it is the one path that was genuinely skipping finalisation — and calls the normaliser directly.

**Where it runs in `finalise`.** Last, after the changed flags, after `derive_search_terms`, after `_classify_record`, and before the transient `_`-prefixed keys are stripped. Those three consumers therefore see exactly the values they saw before this rule existed: casing decides nothing, flags nothing, and changes no tier's behaviour. It runs before the strip because `_registry_name_fields` is what tells it which names not to touch.

**Token level, not string level.** Each whitespace-delimited token is judged on its own, which is what finishes "500 TECH Dr MS-4" instead of skipping it:

| Token | Rule |
|---|---|
| contains a digit | untouched — `MS-4`, `3M`, `450`, `24TH` |
| already mixed case | untouched — `Dr`, `GmbH`, `McDonald`; mixed case is intentional |
| all-upper or all-lower | title-cased, subject to the tables below |

**Tables, in the order they are consulted.**

| Table | Contents |
|---|---|
| `_CANONICAL_TOKEN_FORMS` | Emitted exactly as written. Legal forms (`Inc`, `LLC`, `Ltd`, `GmbH`, `AG`, `SE`, `BV`, `NV`, `KG`, `SA`, `SpA`, `AB`, `AS`, `Oy`), acronyms (`MIT`, `UCLA`, `NMR`, `IT`, `AI`, `US`, `USA`, `UK`, `PO`, `R&D`), directional street prefixes (`N`, `S`, `E`, `W`, `NE`, `NW`, `SE`, `SW`), and the vowel-less street types and personal titles (`St`, `Ave`, `Blvd`, `Dr`, `Rd`, `Mr`, `Prof`, …) that the acronym guard would otherwise leave upper-case. |
| `_ROMAN_NUMERALS` | `II` through `XX`, enumerated. A general Roman-numeral regex accepts ordinary words — `MIX` parses as 1009 — so the numerals are listed rather than matched. |
| `_LOWERCASE_PARTICLES` | Kept lower-case *mid-value* only; leading the value, a connector is a word. The English connectors (`of`, `and`, `for`, `the`, `in`, `at`) plus the European particles that keep an institution or a surname readable (`von`, `van`, `der`, `de`, `du`, `la`, `für`, `und`, …). |
| `_KEEP_UPPER_ACRONYMS` | The existing set — `NASA`, `NIST`, `UCSF`, `SUNY`, `TUHH`, … — reused, not duplicated. |

**Structural handling.** `Mc` prefixes restore the internal capital (`MCDONALD` → `McDonald`); `Mac` is deliberately left alone, since capitalising after it mangles ordinary words (`MACRON` → `Macron`, not `MacRon`). Hyphen- and ampersand-joined tokens are cased on each side under the full rule set, so an acronym on either side survives (`TECHNOLOGY-NIST`, `ICB&DD`). Apostrophes are handled explicitly, never by `str.title()` — that produces `Women'S`. A single letter after an apostrophe is a possessive or elision and stays lower-case (`WOMEN'S` → `Women's`); a longer run is a name segment and is cased (`O'BRIEN` → `O'Brien`).

**Casing changes letter case and nothing else.** No apostrophe, comma, period, ampersand or hyphen is ever added, removed or rewritten, and whitespace runs are preserved exactly. `normalise_case` checks the invariant itself — casing cannot change a string's length, so a length change means a bug, and the input is returned untouched.

**Field coverage.**

| Fields | Treatment |
|---|---|
| Name 1–5 | Cased in **name mode**: a ≤3-letter upper-case token defaults to an acronym (`HCA`, `UCI`, `MRI`), which is `smart_title_case`'s long-standing behaviour. |
| Care Of, Contact, Street 1–5, City, PO Box | Cased in **text mode**: a ≤3-letter upper-case token defaults to a *word* (`WAY`, `OAK`, `DR`), because in a street, a city or a person's name that is what it almost always is. |
| Email | Lower-cased entirely. `ORDERS@MERIDIANLABS.COM` is never the right output form. |
| Country/Region Key, Region, Language Key, Postal Code, Account group, Customer | Untouched. These are codes; their case is meaningful. |
| A name written from a registry | **Skipped**, exactly as [abbreviation expansion skips it](#registry-names-are-authoritative). Title-casing "Massachusetts Institute of Technology" would give "Massachusetts Institute Of Technology". |

Diacritics are **not** restored. "Hochschule fuer Technik Stuttgart" gets its correct form from the ROR record ([registry names are authoritative](#registry-names-are-authoritative)), never from a transliteration rule here — casing has no basis for deciding that `ue` was once `ü`.

The street changed-flags (`street1_changed` …) compare `street1_enriched`, the internal passthrough value, not the exported `street_cleaned` column that this rule cases. They are unaffected either way.

#### Why casing does not set a changed flag

Rule 5 was a real fork, because a changed flag is read as evidence of enrichment. Three options, measured on the 500-record set:

| Option | Changed flags set | Effect |
|---|---|---|
| **(a)** casing counts as a modification | **490** | Honest, but `name1_changed` goes 65 → 193 and `contact_changed` 3 → 94. A consumer that writes back only changed fields would triple its writeback volume for zero content change. |
| **(b) chosen** — only substantive changes count | **270** | The flag keeps its meaning. The output differs from the input in a way no flag records — see below. |
| **(c)** a separate normalised-fields signal | 270 + a new field | Would require a new response field, which is a contract change this fix is not permitted to make. Invisible otherwise: the changed flags themselves are dropped at the model boundary. |

On a 50-record slice the same comparison is 41 against 23.

**(b) is chosen.** The cost is real and worth stating plainly: after this rule the output differs from the input in a way no flag records. That cost is acceptable because the alternative is worse — under (a) the flag stops distinguishing "we enriched this" from "we cased this", and the enrichment signal is the one downstream actually needs. The normalisation is also deterministic and lossless: any consumer can reproduce it from the input, and no consumer needs to be told a field was cased in order to trust it.

#### Registry names are authoritative

**A verified registry match writes the registry's official name. There is no second threshold.**

If a match was good enough to attach `ror_id` / `lei_id`, it is good enough to attach the name. Holding a verified registry identifier while displaying the abbreviated SAP input is never correct — a record carrying `ror.org/03zzw1w08` must read "Mayo Clinic in Florida", not "Mayo Clinic FLA".

The verification is the tier's own match guard, and nothing else:

| Registry | Guard that verifies the match |
|---|---|
| ROR | [country guard](#country-guard) + [distinctive-token / identifier-token guards](#name-scoring-logic) |
| GLEIF | `token_sort_ratio` name-verification guard |

Every path that takes a name from a registry writes it through `_write_registry_name()` and marks the field registry-owned for the rest of `finalise`:

| Path | Writes |
|---|---|
| Tier 1 ROR direct match | `name1_enriched` |
| Tier 1 ROR [child matching](#child-matching) | `name2`/`name3`/`name4_enriched` |
| Tier 1 GLEIF match | `name1_enriched` (legal name) |
| [Tier 1 re-lookup](#stage-5-tier-1-re-lookup-after-canonicalisation) hit, ROR or GLEIF | `name1_enriched` |

**A registry-owned name is never abbreviation-expanded.** ROR and GLEIF are the authority on their own spelling; re-processing a verified official name could only corrupt it. If ROR's display name for an organisation is "Inst Pasteur", that is what ships. (UC 5 unit canonicalisation on Name 2–5 is a separate, older rule and is unchanged — it still rewrites a "`<Unit>` of X" construction whatever its source.)

> **Why there is no identity gate here.** The ROR write used to run through `canonical_preserves_identity()`, which keeps the input whenever the registry's name appears to drop a distinctive token. That guard is right for the **LLM** canonicalisation paths (`company_canonical`, the Tier 3 suggestion path) — an LLM can substitute a different company outright — but a registry match is *verified*, not suggested. Against a registry name the guard mostly fired on abbreviations: "Mayo Clinic FLA" vs "Mayo Clinic in Florida" reads as a dropped `fla`, so it suppressed exactly the writes that mattered. It remains in force on the LLM paths and is gone from the ROR path.
>
> **Parent substitution is not a risk on this path.** The name1 match is scored directly against Name 1, and the local rescore requires every distinctive/identifier token of Name 1 to be covered before a candidate can reach the threshold — so a parent that drops the child's distinguishing tokens cannot match in the first place. Local child matching writes Name 2–5 only, never Name 1.

---

### Stage 5: Tier 1 re-lookup after canonicalisation

**File:** `enrichment/orchestrator.py` · **Entry point:** `Orchestrator._retry_tier1_after_canonicalisation`

Tier 1 runs before the pipeline knows the organisation's real name, so it is queried with whatever the record happened to say. When it misses and a later tier then works the name out, the corrected name used to go nowhere: the record ended with the right official name in `name1_enriched` and no `ror_id` / `lei_id`, even though the corrected string resolves on the first try. `person_affiliation` already re-entered Tier 1 with its discovered institution; the `company_canonical`, Tier 3, Tier 2A and Tier 2B paths were terminal.

**Where it runs.** At the top of `_finalise_and_return` — the single funnel every return path passes through — and *before* the website paths, so a registry hit supplies the domain (with registry provenance) rather than a SERP guess, and before `finalise` decides whether to raise `domain-unverified`.

**Trigger.** `name1_enriched` differs from `_tier1_query_name` (the string Tier 1 was actually queried with) under `normalize_key` — a pure case/punctuation/accent difference is not a corrected name and must not buy an API call. Skipped entirely when the record already carries a `ror_id`/`lei_id`, or when Tier 1 never ran (person path, skipped tier).

**Rules.**

| | |
|---|---|
| **Once per record** | Guarded by `tier1_retry_attempted`, set *before* the call. A retry can never trigger another retry. |
| **No guard is relaxed** | Runs the full normal path — ROR's country guard and distinctive-token guard, GLEIF's name verification. A retry that fails a guard is simply a miss. |
| **Branch rules unchanged** | ROR first; GLEIF only on the company branch, i.e. only when `looks_like_research_institution(canonical)` is false — a research name is never sent to a company registry. |
| **On a hit** | Writes the registry's **official name** to `name1_enriched`, plus `ror_id`/`lei_id`, `tier_used = 1`, `source` (`ROR`/`gleif`), and the marker `tier1_retry_hit` (`"ROR"`/`"gleif"`) that separates a retry hit from a first-pass Tier 1 hit. Registry provenance then satisfies [ownership condition 1](#2b--ownership-guard-domain_ownership_guard_enabled-default-on), so a record that lost its domain to the guard regains a verified one. The name write is the same unconditional rule as the first pass — the retry runs the identical guards, so a hit here is equally verified. See [Registry names are authoritative](#registry-names-are-authoritative). |
| **On a miss** | Nothing is written. The record keeps whatever the earlier tier produced. |
| **Cost** | The retry consults the Tier 1 caches, so a canonical name already resolved for another row in the batch costs no API call. |
| **`record_type`** | Not written here. The retry records ROR's verdict as evidence and [`classifier`](#record-classification-logic) ranks it first in `finalise`. Where the verdict contradicts the branch the record was routed down, that is logged as `tier1_retry_type_conflict` and counted as `routing_type_mismatch`. |

> ⚠️ **The retry can only fire if a tier actually *writes* a changed `name1_enriched`.** `company_canonical.canonical_preserves_identity` rejects a suggestion that changes a distinctive token — including a corrected typo (`MASSACHUSETTS INSITUTE OF TECHNOLOGY` → `Massachusetts Institute of Technology`). For those records the pipeline still discards the right answer, one gate earlier than this fix reaches. Variants the guard accepts (`Universität Stuttgart` → `University of Stuttgart`, `Lockheed Martin Corp` → `Lockheed Martin Corporation`) do reach the retry and converge.
>
> `GA Tech` and `FL State Univ` used to sit in that trap. They no longer reach the retry at all: the [bounded two-letter pattern](#us-state-abbreviation-expansion-ror-local) and the [acronym map](#institution-acronym-expansion) resolve them on the **first** Tier 1 call, which is the cheaper fix and the reason `tier1_retry_attempts` should fall.

**Telemetry.** `tier1_retry_attempts`, `tier1_retry_hits_ror`, `tier1_retry_hits_lei` on the batch summary, alongside `cache_hits_after_normalisation`.

### Stage 6: Batch consensus

**File:** `enrichment/batch_consensus.py` · **Entry point:** `apply_batch_consensus` · **Called from:** `Orchestrator.enrich_batch`

Every record is enriched in isolation, so two rows naming the same organisation at the same address can leave the batch carrying different identities — one resolved against a registry, the other not. [Stage 5](#stage-5-tier-1-re-lookup-after-canonicalisation) recovers most of these at source. This stage is the safety net for whatever the retry does not catch, and it is cheaper to prevent the divergence here than to have [Phase 2](#phase-2--deduplication-adjudicator) adjudicate it later.

> **This is field propagation, not deduplication.** No record is merged, dropped or deduplicated: the batch that comes out is the same length, in the same order, as the batch that went in. Phase 2 remains the only place entities are merged.

**Where it runs.** After every record has been finalised — so every value it moves has already been through the [empty-string guards, abbreviation expansion, unit canonicalisation and casing](#finalization) — and before serialisation and the batch summary, so the counts describe what actually ships. It is the last thing `enrich_batch` does to a result.

#### The grouping key

Address first, then name. Both halves are **internal grouping keys** under the same contract as [Fix 2's cache key](#utilscachepy--cache-keys--batch-cache): used only as a dictionary key, never written to output, never sent to any API, never placed in an LLM prompt, and never fed to `_compute_name_score()` or any other scoring path.

| Half | Derivation |
|---|---|
| **Address** | `dedup.signatures.derive_block_id` — the SHA-1 of the normalised `country\|postal_code\|street\|house_no`, [reused rather than reimplemented](#dedupsignaturespy--step-a-signature-collapsing). Fed the *finalised* address (`street_cleaned`, `house_number`, `postal_code`, `country_region_key`). A record with neither a street nor a postal code has no address signal and never groups — a blank address would otherwise hash every such record into one block and let a bare name match carry an identity across unrelated rows. |
| **Name** | `dedup.signatures.normalize_key` (lowercase, trim, collapse whitespace, strip punctuation, fold accents) → `tier1_ror._normalise_for_tokens` (canonicalise US legal-entity suffixes: Incorporated→inc, Corporation→corp, Company→co, Limited→ltd) → `dedup.candidates.strip_legal_suffix` (remove the trailing legal-form token run, against the `LEGAL_SUFFIXES` table already there). Three existing normalisers composed, each for the one thing it does; a fourth is not written. |

#### The legal-form compatibility rule

`normalize_key` deliberately does not strip legal forms, so the demo batch's Coastal trio produces `coastal diagnostics inc`, `coastal diagnostics` and `coastal diagnostics inc` — the middle row falls into its own group and gains nothing. **Stripping legal forms from the key is the wrong fix:** it would group `Delta Analytical Inc` with `Delta Analytical LLC` at a shared address, which are potentially distinct legal entities and exactly the judgement Phase 2 exists to make.

The base name and the legal form are therefore kept **separate**. Within one address block, two rows group together when their base names are equal **and** their legal forms are compatible — identical after canonicalisation, or absent on one side.

| Pair | Verdict |
|---|---|
| `Coastal Diagnostics Inc` + `Coastal Diagnostics` | compatible — absent on one side |
| `Coastal Diagnostics Inc` + `Coastal Diagnostics, Inc.` | compatible — identical after canonicalisation |
| `Lockheed Martin Corp` + `Lockheed Martin Corporation` | compatible — `_normalise_for_tokens` maps both to `corp` |
| `Delta Analytical Inc` + `Delta Analytical LLC` | **not** compatible — separate groups |

Compatibility is **not transitive**: an absent form is compatible with every form. So when one base name in a block carries two or more *different* legal forms, each form gets its own group and every absent-form row is left alone in its own. Assigning the bare row to one of the competing forms would be a guess, and guessing which legal entity a row belongs to is Phase 2's call.

#### What propagates

Six fields are eligible. Which of them actually move depends on whether the group has a registry behind it — and the two rules are deliberately not equally strong.

**Registry consensus.** The group holds **exactly one** distinct non-null registry identity — at most one `ror_id` and at most one `lei_id`, with at least one present. The registry-carrying member with the most ids (then the earliest `tier_used`, then the earliest batch position) is the **donor**, elected deterministically, and the donor's values win outright. The registry is the authority; a majority of unresolved rows does not outvote it.

**Name-form consensus.** The group holds **no** registry identity. Here the pass **never chooses between competing values — it only fills gaps where the group is already unanimous.** A field moves only when the members hold exactly one distinct non-null value for it; two rows holding two different domains keep both, and the third keeps its null.

`name1_enriched` is the single exception to that, and only because its competing values are not competing facts: every member already carries the same organisation name — that is what grouped them — and they differ only in how the legal form is spelled, punctuated or cased. The **modal** surface form wins, ties breaking on the earliest tier then batch order. The batch's own majority spelling is evidence; "which legal form is more correct" is not a judgement this pass is entitled to make.

> **Does the weaker rule smuggle a domain past the [ownership guard](#2b--ownership-guard-domain_ownership_guard_enabled-default-on)?** No. The domain being filled in already satisfied that guard on a record whose Name 1 is *equal to the receiving record's after canonicalisation* — that equality is the grouping criterion. The guard's name-similarity condition would therefore reach the same verdict on the receiving record. The value is not attributed on weaker evidence than the guard requires; it is attributed on the same evidence, and only where nothing in the group contradicts it.

| Propagated (organisation-level) | Never propagated (department- or record-level) |
|---|---|
| `ror_id`, `lei_id` | `name2_enriched`, `name3_enriched`, `name4_enriched` |
| `name1_enriched` | `department_domain` |
| `domain`, `website_url` | `contact_enriched`, `care_of_enriched`, `email_enriched` |
| `record_type` | `search_term_2` |
| | `street`, `house_number`, and every other address field |

**The department distinction is the point of that second column.** Rows 12–14 of the demo batch are Stanford at one address with three different departments (chemistry, chemistry, physics). They must share Stanford's `ror_id` and `domain` and keep their own `name2_enriched` and `department_domain`. Grouping is on Name 1 only: Name 2 is not in the key, and differing Name 2 values never prevent propagation.

Three details of the copy itself:

- **Copied verbatim.** A propagated value comes from an already-finalised record, so it is never re-run through [abbreviation expansion](#finalization) or [output casing](#rule-7--output-casing-normalisation) — doing so would corrupt a registry-owned name.
- **A null donor value never erases a resolved one.** Where the donor holds no `domain`, the group's single distinct domain (if it has exactly one) is used instead, and `domain` / `website_url` are always taken from the *same* record so the two cannot diverge.
- **`record_type = "unknown"` is treated as absent,** not as a value that could conflict — consistent with [`unknown` being "no source produced an answer"](#unknown-is-a-real-fourth-state).

#### Conflicts, provenance and what is left alone

A group holding two or more conflicting registry identities propagates **nothing** and every member is left exactly as it was. **No flag is written** — the flagging model is being redesigned separately — and the group is recorded in telemetry only.

On a record that inherited at least one field:

| | |
|---|---|
| **`source`** | set to `"batch_consensus"` |
| **`tier_used`** | **kept**, never set to 1. Inflating the Tier 1 count would corrupt the tier-distribution figures used in evaluation. |
| **review fields** | untouched by the pass itself. Consensus runs *after* every record is finalised, so a record keeps the flags it earned on its own; inheriting a field never adds or clears one. |
| **`record_type_source`** | untouched, and therefore still names the evidence *that record's own* classification came from. The classification authority is [`classifier`](#record-classification-logic); this stage moves a decided value, it does not decide one. |

A record whose fields already agree with the consensus is not counted as updated and keeps its own `source`. The donor inherits nothing from itself.

**Known limits.** Three, all measured on the demo batch:

- **A group with no registry identity cannot break a tie.** Name-form consensus fills unanimous gaps only, so a group whose members resolved two *different* domains keeps both — there is no authority in the group to prefer one, and inventing one would be guessing. Such a group is not a conflict either (nothing conflicts about the identity itself), so it is not counted in `consensus_conflicts`; it simply converges less. A registry hit upstream is what turns it into the stronger rule.
- **A synonym is not a spelling variant.** The name key folds accents but translates nothing, so a `Universität Stuttgart` still spelled that way at the end of the pipeline would not group with `University of Stuttgart` — the same conservatism, and the same consequence, that [`normalize_key` carries in Phase 2](#dedupsignaturespy--step-a-signature-collapsing). In practice these rows converge before they reach this stage, because [Stage 5](#stage-5-tier-1-re-lookup-after-canonicalisation) rewrites Name 1 to the registry's official form; that is where a synonym belongs, not in a grouping key.
- **The address key splits `street` and `house_no`,** so two rows whose house number was pulled out of the street on one and left in it on the other land in different blocks. Both rows go through the same address stage, so identical inputs split identically; a batch that spells one address two ways can still miss.

**Measured on the 50-record demo batch** (live run, after Fixes 1-5): 7 groups, 7 records updated, 0 conflicts, `{ror_id: 4, lei_id: 1, domain: 2, website_url: 2, name1_enriched: 2}`. Registry consensus carries rows 18/20/21, which inherit Lockheed's `ror_id` from row 19, and rows 5 and 24, which exchange the ROR id and the LEI each was missing (row 24 also gains `mit.edu`). Name-form consensus carries rows 15 and 16, which take the trio's modal `Coastal Diagnostics, Inc.` and fill the one domain the group agreed on. Stanford, Yale and both Stuttgart groups were already converged by Fixes 2 and 4 and are left untouched. The tier distribution is identical either side of the pass, no flag moves, and no excluded field moves.

**Telemetry.** `consensus_groups` (groups with 2+ members), `consensus_records_updated` (records that inherited at least one field), `consensus_conflicts` (groups holding conflicting registry identities), and `consensus_fields_propagated` (counts per field) on the batch summary. Per-record `batch_consensus_inherit` and per-group `batch_consensus_conflict` structured log lines carry the detail.


## Use Case Reference Table

The pipeline tracks which "use cases" fired for each record. These are reported in the `use_cases_triggered` array in each result:

| UC | Name | Stage | Trigger | Action |
|----|------|-------|---------|--------|
| 0 | Name1 Overflow Detection | Stage 0 | Both Name1 + Name2 non-blank | LLM checks if it's one split name; flags if yes |
| 2 | Institution ROR Resolution | Tier 1 | ROR match found | Enriches Name1 with official ROR name |
| 3 | Company Name Canonicalization | Tier 1/2 | ROR miss + looks like a company, or ROR matched a company | GLEIF/LEI registry lookup first (official legal name + `lei_id`); LLM canonicalization with geographic context as the fallback when LEI misses |
| 4 | Contact Lookup with Scope Filter | Tier 2A | Contact present, ROR hit, domain known | Discovers/verifies Name2 from contact's faculty page |
| 5 | Department Canonicalization | Tier 2 | Name2 present, ROR hit, no child match | LLM normalizes department name to official wording |
| 6 | Accounts Payable Recognition | Preprocessing | AP pattern detected | Flags as accounts payable for special handling |
| 7 | Contact Person Extraction | Preprocessing | Person name in name fields | Moves person name to `contact` field |
| 8 | Email Copy (Non-Destructive) | Preprocessing | Email address in name or address fields | Copies email to `email` field; source preserved |
| 9 | Address Extraction | Preprocessing | Street address in name fields | Moves address to `street1`/`street2`/`street3` fields |
| 10 | Opaque Code Detection | Preprocessing | Internal code/ID in name fields | Flags as non-name data |
| 11 | DBA Normalization | Preprocessing | "Doing Business As" variant in name fields | Rewrites variant to canonical "DBA" |
| 12 | Duplicate Name Clearing | Preprocessing | Name1==Name2 or Name2==Name3 (case/whitespace insensitive) | Silently clears the duplicate field |
| 13 | Lab → Parent Department Resolution | Tier 2 | Name2 is a granular unit (lab/group/centre/core/facility) at a research institution with a known domain | SERP + page fetch + LLM extracts the parent academic department; parent → Name2, lab → Name3 (when Name3 empty) |

---

## Record Classification Logic

**File:** `enrichment/classifier.py` (+ `enrichment/elf_codes.py`)

### Two fields, not one

`record_type` used to be written by whichever tier ran last — ROR org types, then an LEI hit, then company canonicalisation — and the last writer won. It is also not purely an output: it gates behaviour *during* the run, and the pipeline needs a type before the evidence that decides the final one exists. So the concept is split:

| Field | Lifetime | Role |
|---|---|---|
| **`routing_type`** | Provisional, updated as tiers report | Selects the Tier 1 branch (ROR vs GLEIF) and gates Tier 2A / Tier 2B / lab resolution. **Internal only** — never serialised, never in the response or the export. |
| **`record_type`** | Final, decided **once** in `finalise()` | The only value that reaches the output, the Excel export and Phase 2 dedup. |

Tier gating reads `routing_type` everywhere. No tier decides `record_type`.

### Evidence ranking

`classifier.classify()` takes the first source that yields an answer. An ambiguous or absent field yields *nothing* and falls through, rather than guessing.

| # | Source | Yields | `record_type_source` |
|---|---|---|---|
| 1 | **ROR org types** | `education`, `healthcare`, `government`, `facility`, `nonprofit`, `archive`, `other` → `research_institution`; `company` → `company` | `ror` |
| 2 | **GLEIF entity metadata** | `entity.category` and `entity.legalForm.id` (ISO 20275) from the `lei-records` response already fetched — see below | `gleif` |
| 3 | **Keyword heuristic** | `looks_like_research_institution()` → `research_institution`. It can *only* yield that: a name not looking institutional is not evidence of a company. | `keyword` |
| 4 | **Nothing** | `unknown` | `unresolved` |

**Tier 3 contributes no evidence and never had any** — it is a last-resort name guesser with no classification signal. A record that reached Tier 3 is classified from whatever other evidence exists, never from having been there.

### `unknown` is a real fourth state

`unknown` means **"no tier resolved the type with confidence"**. It does not mean "not yet computed", "error", or "empty". It is a legitimate terminal outcome, and deliberately preferred over the old `company` default, which asserted something the pipeline does not know.

> ⚠️ **Phase 2 scoring consumes `record_type` and needs a weight assigned for `unknown`.** `dedup/weights.json` is untouched here — that decision belongs to the scoring model owner.

### The LEI guard

**An LEI hit on its own never sets `company`.** An LEI proves legal registration, not commercial status: universities, hospitals, foundations and government bodies hold LEIs, typically for bond issuance or derivatives reporting. So a commercial verdict from GLEIF is *withheld* — not overridden — whenever the name reads as a research institution, and the keyword source answers instead. The two facts are not in conflict: a university with an LEI is a university that issues bonds, and it keeps its `lei_id` either way.

### What GLEIF metadata actually looks like

Checked against live `api.gleif.org` responses rather than assumed:

| Entity | `category` | `legalForm.id` | `legalForm.other` | `subCategory` |
|---|---|---|---|---|
| MIT (`DLZO3A31IADZ27B62557`) | `GENERAL` | `8888` | `INSTITUTE` | `null` |
| Pfizer Inc. | `GENERAL` | `XTIQ` (Corporation) | `null` | `null` |
| Yale University | `GENERAL` | `7W53` (Nonstock Corporation) | `null` | `null` |
| Brigham and Women's Hospital | `GENERAL` | `8888` | `Hospital` | `null` |
| Siemens AG | `GENERAL` | `6QQB` (Aktiengesellschaft) | `null` | `null` |

Two consequences the design had to absorb:

- **`category` is nearly useless.** It is `GENERAL` for MIT *and* Pfizer — for the overwhelming majority of entities. Only `SOLE_PROPRIETOR` / `FUND` / `BRANCH` (commercial) and `RESIDENT_GOVERNMENT_ENTITY` / `INTERNATIONAL_ORGANIZATION` (not) carry a signal. **`subCategory` was `null` on every record sampled.**
- **`legalForm.id` does the work**, via the code table in `enrichment/elf_codes.py`. The response carries only the code, never its name, so the name→character decision is made once at development time from the GLEIF ELF registry — a lookup table over fields already fetched, not a new service. Codes `8888` ("other") and `9999` ("not on the list") carry no meaning; the free text in `legalForm.other` is matched separately for those.

The table is deliberately narrow: 95 non-commercial and 978 commercial forms out of 3,599, with everything else absent so it falls through. "Nonstock Corporation" and "Corporation (Nonprofit)" must not read as commercial merely for containing "Corporation"; "For-Profit Public Benefit Corporation", "Savings and Loan Association", "Business Trust" and credit unions must not read as non-commercial for containing charitable-sounding words.

> Note the taxonomy only has two values, so GLEIF's non-commercial signal means *"not a commercial entity"* and is recorded as `research_institution`. A non-profit that is not a research body (a church, a community association) would land there too. In this customer master the non-commercial population is overwhelmingly universities, hospitals and institutes, and ROR — which ranks above GLEIF — already resolves most of them.

**Impact on pipeline routing** (all driven by `routing_type`):
- `research_institution`: Eligible for Tier 2A (contact lookup) and Tier 2 Canonical
- `company`: Routes to **Tier 1 GLEIF/LEI registry lookup** first, then company canonicalization (LLM) if LEI misses, then Tier 2B if Name2 exists
- Both types: Eligible for Tier 1, Tier 2B, and Tier 3

**Routing mismatch telemetry.** Where `routing_type` disagrees with the final `record_type`, the record ran down the wrong branch — routed as a company, so Tier 2B never ran, then finally classified `research_institution`. Those records are **not** re-run; the batch summary counts them (`routing_type_mismatch_count`) so the size of the problem is visible and a later fix can decide whether re-routing is worth it.

---

## Website, Domain, Department-Domain & Search-Term Resolution

Four related output columns are produced during finalisation. They are distinct and computed by different subsystems:

| Output column | Internal field | What it is | Owner |
|---|---|---|---|
| **Domain** | `domain` | The registrable domain (`mit.edu`, `example.co.uk`) — also feeds search terms and the dept probe | `domain_resolver.resolve_domain()` |
| *(internal, excluded from response)* | `website_url` | The org homepage, always `https://<domain>` | `domain_resolver.resolve_domain()` |
| **Department Domain** | `department_domain` | The department's subdomain (`chem.ufl.edu`) | `orchestrator._probe_department_url` |
| **Search Term 1 / 2** | `search_term_1` / `search_term_2` | Compact search handles for the institution / department | `search_terms.derive_search_terms` |

> ⚠️ The public **"Domain"** column carries the bare registrable `domain` — never a URL. `website_url` is derived from it (`https://mit.edu`) and is internal-only (`api/models.py`); the candidate URL a tier actually found is kept only for the duration of the record, as `_website_raw`, because the department probe needs the real host. `department_domain` is a separate column, emitted as a full `https://…` URL.
>
> Both fields are written in exactly one place — `utils/domain_resolver.resolve_domain()` (§2). No other module writes either one.

### 1 · Institution website — Path A / B / C

Resolution follows a strict precedence; the first source that fills the domain wins (it is only ever written while empty). All of Paths B/C live in `enrichment/website_resolver.py`, driven by `orchestrator._maybe_resolve_website_bc` (which returns early if a website is already set — so ROR always wins — or if Name 1 is blank).

Every path produces a **candidate URL**, never a field value: the candidate goes to `resolve_domain()` (§2), which canonicalises it and decides whether it may be attributed to this organisation at all. A path that finds a candidate the guard rejects writes nothing.

**Path A — ROR (highest precedence).** When Tier 1 matches, ROR's first `links[]` entry of `type == "website"` is the candidate, carrying `registry="ROR"` provenance — the match already passed ROR's country guard. It is still canonicalised: ROR routinely stores a deep link (`http://www.uni-stuttgart.de/home/index.en.html` → `uni-stuttgart.de`). The person-affiliation path (Stage 2b) does the same after a ROR-confirmed match. For companies matched via GLEIF/LEI, ROR's domain survives — LEI has no website field, and can overwrite `name1` but never the domain.

**Path B — SERP resolution** (`resolve_website_via_serp`, any record type):
- **Query.** `"{name1}" official website` plus geo — research institutions append the country; companies/unknown append `city state` (else country, else nothing). `num_results = 10`.
- **Valid filter.** Each result must (a) be a real `http(s)://` URL, (b) not be on the `DOMAIN_BLACKLIST` (wikipedia, linkedin, facebook, twitter/x, instagram, youtube, ratemyprofessors, glassdoor, yelp, bbb, crunchbase, bloomberg, indeed, ziprecruiter — host or subdomain), and (c) pass **name-overlap** (a ≥4-char significant Name-1 token appears in the URL or title).
- **Ranking (both branches — §7b).** Every valid candidate is ranked 0/1/2:
  - **2** — a *distinctive* Name-1 token (not on the generic blocklist) is in the **host**, with no foreign-brand label. Clean match.
  - **1** — host match but a foreign-brand label is introduced (`siemens-healthineers.com` for "Siemens AG"). Sub-brand.
  - **0** — the name only overlaps the **title**, not the host → **rejected** (defers to Path C). This is how `scup.org` for "Bayfront Research" is now blocked.
  - **Distinctive-token requirement (§7a).** Generic industry words (`research, therapeutics, diagnostics, medical, sciences, laboratories, technologies, solutions, systems, group, holdings, international, global, pharma, bio, biotech, health, services, consulting, partners, associates`, …) do **not** count as a host match on their own — mirrors ROR's upstream distinctive-token guard.
  - **Acronym-in-host (research institutions only).** An institution whose host is its acronym (`fit.edu` ↔ "Florida Institute of Technology", `mit.edu` ↔ "MIT") counts as a host match even without a name word in the host, so acronym-domain institutions still resolve while `scup.org` (≠ "BR") is rejected.
- **Confidence (§7c).** Company rank 2 → `high`, rank 1 → `low`. For research institutions, an authoritative TLD (`.edu`/`.gov`/`.org`) grants `high` **only** with a clean (rank-2) host match — a bare `.org` never grants high confidence on its own.
- **Unquoted retry (§8).** If the exact-phrase query yields no valid candidate, one **unquoted** retry runs (`{name1} official website …`) — a site that brands itself slightly differently ("…Laboratories" vs input "…Labs") is then reachable. One retry maximum, only on a first-pass miss. Both attempts are logged in the `WEBSITE_TRACE` output (`attempt: "quoted" | "unquoted_retry"`).

**Path C — LLM inference** (`infer_website_via_llm`, only when Path B found nothing). One LLM call with Name 1 + city/state/country; `""/null/none/unknown/n-a` are treated as no result; the URL shape is validated. Always returned `low` confidence.

**Confidence → write semantics.** `high` → write. `low` → write. `none` → leave empty. Provenance alone no longer raises a flag: the old "inferred by LLM — verify" reason said *where the URL came from*, not whether it is right, and Fix 8 replaced it with the evidence-based `domain-unverified` — raised when the ownership guard (§2b) can tie the candidate to nothing, in which case nothing is written either. Path B's chosen SERP title rides along on `WebsiteResolution.title` as read-only evidence for the guard's on-domain condition; it never influences selection.

**`WEBSITE_TRACE` diagnostic** (`config.WEBSITE_TRACE`, default off). When on, `resolve_website_via_serp` / `infer_website_via_llm` emit one structured JSON line per attempt on the `enrichment.trace.website` logger — per-candidate `rejected_by` (`url_shape`/`blacklist`/`name_overlap`/`rank_0`), matched token, foreign label, rank, chosen, confidence, and the outcome. Read-only: it never changes resolution. Driver script: `scripts/trace_website.py` (six records, writes `logs/website_trace.json`).

### 2 · `domain` — the single write path (`utils/domain_resolver.py`)

**File:** `utils/domain_resolver.py` · **Entry point:** `resolve_domain(candidate_url, evidence)`

Every value `domain` and `website_url` ever take is produced here. Callers pass the candidate URL they found plus the evidence the record carries; they never assign the fields themselves. `orchestrator._apply_domain` is the thin wrapper that hands a candidate over and writes the outcome.

Candidate order is unchanged, first-non-empty-wins: **ROR** (Path A, during Tier 1) → **website-derived** (Paths B/C) → **`source_url`-derived** (a Tier 2A faculty page or the lab resolver, in `finalise`). A later candidate never overwrites an accepted domain. Companies resolved via LEI carry `domain = null` unless a web tier supplies a website.

#### 2a · Canonical form

`canonicalise_domain()` reduces any candidate to the registrable domain, reusing `extract_domain()`: scheme, `www.`, path, query, fragment and trailing slash dropped, subdomains collapsed. `website_url` is then rebuilt as `https://<domain>`.

| Candidate | `domain` | `website_url` |
|---|---|---|
| `http://www.uni-stuttgart.de/home/index.en.html` | `uni-stuttgart.de` | `https://uni-stuttgart.de` |
| `https://www.mayoclinic.org/patient-visitor-guide/florida` | `mayoclinic.org` | `https://mayoclinic.org` |
| `https://investors.lockheedmartin.com` | `lockheedmartin.com` | `https://lockheedmartin.com` |
| `https://admission.gatech.edu` | `gatech.edu` | `https://gatech.edu` |

So a `domain` can never contain a scheme, path, query string, trailing slash or subdomain, and `website_url` can never be a deep link or a sub-site. An investor-relations or admissions sub-site is not the organisation's domain.

The raw candidate is kept for the record's lifetime as the transient `_website_raw` key, because the department probe needs the real host — §5e's `site:gc.cuny.edu` must not become `site:cuny.edu`, and §5f follows the original link's redirect chain.

#### 2b · Ownership guard (`DOMAIN_OWNERSHIP_GUARD_ENABLED`, default on)

ROR has a country guard and GLEIF a name-verification guard because both upstream scorers return confident wrong answers. The domain path had neither, so an unrelated company's website could be attached to a customer record — `delta.com` for "Delta Analytical", `cardinalhealth.com` for "Cardinal Instruments" — and read as successful enrichment. That is worse than an empty field.

A canonicalised candidate is accepted only when at least one holds:

1. **Registry provenance.** The candidate came from a ROR record that passed the country guard, or a GLEIF record that passed name verification. Sufficient alone, no further check.
2. **Name similarity.** RapidFuzz `token_sort_ratio` between Name 1 (normalised by `tier1_ror._normalise_for_tokens`) and the domain label (split on hyphens only) reaches **`DOMAIN_NAME_MATCH_THRESHOLD`**. Concatenated words are deliberately *not* segmented — guessing word boundaries inside `aumbiotech` produces false confidence.
3. **Email domain match.** The record carries an email whose registrable domain is not a consumer provider (gmail, googlemail, outlook, hotmail, live, yahoo, aol, icloud, gmx, web.de, t-online.de, protonmail, …). When the candidate could not be verified on its own this **replaces** it: a record holding `ORDERS@MERIDIANLABS.COM` already knows the domain better than a search result does (`meridianlabs.ai`).
4. **On-domain search evidence.** The candidate came from a SERP result *on that domain* whose page title or H1 contains the Name 1 tokens — **all** significant (≥4-char) tokens, not just one. The SERP layer already admits a result on a single overlap, which is exactly how a stranger's "… Biotech …" page slips through.

Precedence is registry → name → email → on-domain evidence; the first hit wins and names the accepting condition. Name similarity is consulted *before* the email so a well-matched candidate is never clobbered by an unrelated address on the record (a distributor's mailbox).

**None holds** → `domain = null`, no `website_url`, and the record carries the `domain-unverified` code, scoped to the `domain` field. Nothing can mask it: flags are no longer appended as tiers run but computed once, from final state, at the end of `finalise` (see [Flag Rules](#flag-rules)), and a record simply carries every code that applies to it.

Two records both reading "Cardinal Instruments" (Tampa and Boston) fail name similarity against `cardinalhealth.com` and `cardinalguitars.com` alike, and both end with no domain. That is the intended outcome.

**Threshold tuning.** `DOMAIN_NAME_MATCH_THRESHOLD` defaults to **82**, tuned on the demo batch: the highest-scoring *wrong-owner* pair is `Acme Biotech → aumbiotech.com` at 81.8, and the lowest-scoring *right-owner* pair is `Lockheed Martin Corp → lockheedmartin.com` at 82.4. Every other wrong-owner pair scores ≤ 75. The margin between those two is thin by construction — a metric that cannot segment concatenated labels has no room there — so the other three conditions, not the threshold, are what carry most accepts.

**Known cost.** Domains that are *contractions* of the name (`fishersci.com` for "Fisher Scientific", `massgeneralbrigham.org` for "Brigham and Women's") and bare acronym hosts (`ufl.edu`) are unreachable by name similarity. They still resolve through registry provenance, an on-domain SERP title, or the record's email — but a Path C (LLM) guess with none of those is now dropped rather than shipped.

**Telemetry.** Batch summary: `domain_from_registry`, `domain_from_email`, `domain_from_serp` (every web-derived accept — name similarity or on-domain evidence), `domain_rejected_unverified`. Each rejection also logs one line naming the candidate and Name 1.

Set `DOMAIN_OWNERSHIP_GUARD_ENABLED=false` to A/B disable the guard; canonicalisation (§2a) still applies.

### 3 · `department_domain` — the department probe

Resolved by `orchestrator._probe_department_url` (writes `result["department_domain"]` directly; never touches `website_url`). Runs on every return path.

**Preconditions (all must hold, else `None`):** `routing_type == "research_institution"` (the provisional type — the probe runs during the pipeline, before `record_type` is decided); `department_domain` not already set; the institution **`domain`** (base) is present; a usable Name 2 that is **not** an admin desk (`is_admin_unit` — §5a, skipped before any fetch/SERP), **not** an address/location fragment, and **not** a granular unit (lab/group/core/facility); and at least one significant token or acronym derives from the cleaned Name 2.

**Base resolution (§5e/§5f).** Before the stages run, the base host is resolved once (cached per batch): the institution website's redirect chain is followed once (`PageFetcher.resolve_final_url`) so a stale ROR site is corrected (`dur.ac.uk` → `durham.ac.uk`, §5f); and when the institution host is itself a subdomain the **full host** is used (`gc.cuny.edu`, so `site:gc.cuny.edu` doesn't leak other CUNY campuses, §5e). `www.`/`web.` prefixes are stripped (`web.mit.edu` → base `mit.edu`).

**Stages (first verified winner short-circuits):**
0. **Constructed subdomain** — build prefixes (acronym if 2–6 chars; the two longest ≥4-char tokens; plus their 4/3-char prefixes, `chem`←`chemistry`), form `{prefix}.{base}`, fetch concurrently, first that verifies wins.
1. **Homepage link scrape** — fetch the homepage, score outgoing off-base links, verify the top few.
2. **Site-restricted SERP** — `"{cleaned} site:{base}"` (`num_results=5`), score → top-5 → verify.
2b. **On-domain path page** (reuses stage-2 results, no new SERP) — for departments hosted at a *path* (`clas.ufl.edu/chemistry`). Candidates whose path is non-department content (**§5b** generic-path blocklist: `news, news-events, events, story, article, blog, calendar, archive, colloquium, seminar, …`) are dropped; the rest are ranked by **canonicality (§5c)** — a shallow landing page beats a deep, dated (`/2020/`), or sub-page (`/undergrad`, `/people`, `/faculty`, …) URL — then verified best-first. Stores the full URL.
3. **Cross-domain SERP** (`DEPT_PROBE_CROSS_DOMAIN`, **default `false` — §6**) — `"{cleaned} {name1}"`; skips third-party hosts; first verified host wins (`hopkinsmedicine.org` for a JHU dept). A second, unrestricted SERP call; enable only for split-domain academic medical centres.

**Scoring — `_score_dept_candidate`** (host-based stages). `needles = tokens ∪ {acronym}`. `first_seg` (the subdomain prefix) is dropped if it's a generic admin host, and **must** match a needle (`_seg_matches_needle`: substring or shared ≥3-char leading prefix, `chem`↔`chemistry`), else score 0 — path/title matches alone are never enough. Base `3` for the host match, `+1` for a needle in a **non-generic** path (§5b) minus its canonicality penalty (§5c), `+1` for a needle in the title.

**Verification — `_verify_candidate_url` (§5d).** Fetches the page; passes if the cleaned phrase appears verbatim, or ≥2 needles match (≥1 for a single needle). A needle matches a page word via `_seg_matches_needle` **or** a ≥5-char common prefix — so `physics.nist.gov` ("Physical Measurement Laboratory") verifies for a Physics query, while `science.mit.edu` still fails a Computer Science query. (`_seg_matches_needle` alone uses full-prefix and does not bridge `physics`↔`physical`; the common-prefix rule covers that.)

**Output.** Bare-host winners are stored as `chem.ufl.edu`; stage-2b path winners as a full URL. In `finalise` — **after** search terms are derived (they need the stored form) — the stored value is run through `domain_resolver.canonicalise_host()` and prefixed: `https://chem.ufl.edu`.

`canonicalise_host()` is **not** `canonicalise_domain()`. It strips the path, query, fragment, trailing slash and a leading `www.`, but **keeps the subdomain** — a department domain legitimately *is* a subdomain (`chemistry.stanford.edu`, `be.mit.edu`, `physics.yale.edu`), and collapsing it would destroy the Tier 2B output. So `https://medschool.umich.edu/departments/radiation-oncology` is emitted as `https://medschool.umich.edu`.

> ⚠️ This does mean a stage-2b **path** winner loses its path: `clas.ufl.edu/chemistry` is emitted as `clas.ufl.edu`, which names the college rather than the department. That is the documented trade-off for never shipping a deep link in this column; stage 2b's path ranking still decides *which* host wins.

### 4 · Search terms (`search_term_1`, `search_term_2`)

Computed by `search_terms.derive_search_terms(result)` once in `finalise`. `search_term_1` mirrors Name 1 (institution); `search_term_2` mirrors Name 2 (department). Both pass a **terminal normalisation (§4)**: trim → collapse internal whitespace → uppercase → truncate to **32 chars** on a word boundary (SAP SORT1/SORT2 width).

> **Derived after enrichment, from enriched values only.** Every input to both chains is a settled post-enrichment value — the enriched Name 1/2, the resolved `domain`, the probed `department_domain`, the registry acronym. The record's **own pre-enrichment SAP Search Term 1/2 are never consulted**: they are customer-maintained free text (stale abbreviations, typos, person initials), and echoing them would ship a stale handle for a record whose name enrichment has just corrected. Derivation sits near the end of `finalise` for exactly this reason — after the name canonicalisation, slot dedup/packing, department passthrough and the domain fallback have all run.
>
> **Both terms read only the enriched name slots.** `search_term_1` reads `name1_enriched`, `search_term_2` reads `name2_enriched`; neither falls back to its `*_original`. Without this a record could ship a search term for a name it does not carry — `ATTN CHARLES FARBER / MIT` came back with `Name 1: null` (preprocessing moved the person to Contact and no institution survived) and `Search Term 1: "ATTN CHARLES FARBER / MIT"`, the raw input string. `finalise` has already retained the input value in the enriched slot wherever no tier changed it, so a **blank** enriched slot at derivation time means enrichment deliberately emptied the field (an address, an email, a contact name lifted out by preprocessing) — a value that must not be mined for a handle.

**`search_term_1` chain** (first non-empty wins):
1. **ROR acronym, currency-checked (§2).** ROR may carry several acronym entries (current + historical); the one whose letters are the initials of the *current* official name is chosen (`NIST` ✓, `NBS` ✗ for "National Institute of Standards and Technology"). If none matches, no ROR acronym.
2. **`strip_tld(domain)`** verbatim — hyphens kept (`uni-tuebingen.de` → `UNI-TUEBINGEN`).
3. **Name-derived handle**, from the **enriched Name 1 and only from it** — never `name1_original`. `name1_enriched` *is* the Name 1 column of the response (`finalise` has already backfilled it from the preprocessed input wherever no tier changed it), so a blank one means the record ships no institution at all and there is nothing to hand a handle for → `None`. This subsumes the UC 7 person guard: a person lifted out of Name 1 leaves the slot blank, while a Stage-2b-resolved affiliation puts a real institution there and *is* used. Legal-entity suffixes are dropped (`Inc`, `Ltd`, `GmbH`, …), then **the whole enriched name is kept when it fits the 32-char field**, because the connecting words are what make the handle searchable — `University of Florida` is a query, `University Florida` is not. Only a name that overflows the field drops its stopwords and is filled greedily to the boundary.
   `Verdox, Inc.` → `VERDOX` · `Applied Thin Films, Inc.` → `APPLIED THIN FILMS` · `University of Florida` → `UNIVERSITY OF FLORIDA` · `Massachusetts Institute of Technology` → `MASSACHUSETTS INSTITUTE`.
4. `None`.

`derive_acronym` was **removed** from this chain (§1) — it produced initials with no corroborating evidence (`VI`, `SB`, `JFF`). It remains for internal use by the department probe.

**`search_term_2` chain** (first non-empty wins):
0. **`ADMIN`** — if Name 2 is an administrative desk (`is_admin_unit`: accounts payable/receivable, finance, billing, invoicing, purchasing, procurement, controlling, treasury, bursar, comptroller, general accounting, shared services — English only). `Office of Research` is **not** admin → resolves to `RESEARCH`.
1. **Subdomain acronym** — the `department_domain` subdomain prefix, only when it genuinely is an acronym: 2–6 chars, not a leading prefix of any Name 2 token, and its letters match the initials of Name 2's significant words (`eecs.mit.edu` + "Electrical Engineering and Computer Science" → `EECS`; `chem.ufl.edu` + "Chemistry" → rejected as a truncated word → falls through to `CHEMISTRY`).
2. **Name 2 phrase** — `clean_name2_phrase` (strip `Department of`, `School of`, … prefix and unit suffix), then **strip every structural unit word** (`_strip_unit_keywords`), then **fill to 32 chars** on a word boundary (`Earth and Planetary Sciences` → `EARTH PLANETARY SCIENCES`; `Organic Process Chemistry and Analytical Technology Development` → `ORGANIC PROCESS CHEMISTRY`).
3. **Department-domain host** — subdomain prefix, else TLD-stripped host, unless that segment is itself a structural or generic word (`dept.example.edu` → no handle).
4. `None`.

**Structural unit words are never part of `search_term_2`.** `department` / `departments` / `dept` / `depts`, `division` / `div`, `section`, `unit`, `branch`, `school`, `college`, `faculty`, `office`, `institute` / `inst`, `center` / `centre` / `ctr`, `laboratory` / `lab` / `labs`, `group` / `grp` are dropped **wherever they appear**, not just at the edges — `clean_name2_phrase` only strips a leading prefix or a trailing suffix, so a mid-string `Dept`/`Div` used to survive. `Chemistry Dept` and `Department of Chemistry` both search as `CHEMISTRY`; `Chemistry Dept Analytical Div` → `CHEMISTRY ANALYTICAL`; `Materials Science Lab Group` → `MATERIALS SCIENCE`. A phrase made of **nothing but** structural words (`Laboratory`, `Division`) names no unit, so the keyword is not shipped — the chain falls through to the department domain, else `None`.

> This **inverts** the old precedence — Name 2 text now beats the department-domain host, which had produced junk handles (`scrippscollege`, `leuphana`, `uwm`).

**Field-content guards on Name 2.** If UC 11 flagged Name 2 as a **DBA** trade name, or Name 2 is an **institution** in the department slot (`looks_like_research_institution` and not a unit phrase → probable field swap, e.g. `John F Florek` / `Tufts University`), Name 2 is not used for a handle. Search-term derivation raises no review flag of its own — `enrichment/flags.py` is the single flag authority.

**Name-1 standardisation on a kept ROR name.** When Tier 1 matches but the identity guard keeps *your* Name 1 over ROR's divergent official form (e.g. ROR's German `Hochschule für Technik Stuttgart` vs input `Stuttgart Univ of Applied Sciences`), the kept name is still run through `clean_passthrough_org_name` — so `Univ` → `University` and ALL-CAPS is title-cased, exactly as a ROR-miss passthrough is cleaned.

---

## Confidence, Flags, and Enrichment Status

**Files:** `enrichment/confidence.py` (status) · `enrichment/flags.py` (flags)

### Enrichment Status Values

| Status | Meaning | Human Action Required |
|--------|---------|----------------------|
| `enriched` | Name1 and/or Name2 enriched with sufficient confidence | None (auto-applied) |
| `verified` | Name2 exactly matched against contact's faculty page (Tier 2A Mode B) | None (confirmed correct) |
| `unresolved` | Enrichment attempted but confidence insufficient for auto-application | Manual review needed |
| `failed` | Pipeline error or all tiers returned nothing | Investigation needed |

### Flag Rules

**File:** `enrichment/flags.py`

The flag answers one question for a reviewer: *is there something here for me to do, and to which field?* It used to answer a different one — *which tier ran?* — because each tier appended its own reason as it executed. That put a flag on 47 of the 50 demo records and made it useless as a triage signal. Fix 8 replaced the model.

**Three properties hold by construction.**

1. **Rebuilt, never appended.** `compute_flags` is called **once**, from `finalise`, after every name, domain and contact field has settled. Tiers record *evidence*; they never write a flag. A record that reached Tier 3 and was then rescued by Fix 2's Tier 1 retry ends with a registry identifier and **no** Tier 3 reason, because the reason is derived from what the record *holds*, not from what ran.
2. **Field-scoped.** `flagged_fields` names the output fields the flag concerns. A record with a verified ROR ID and an uncertain department scopes to `name2` alone — which is what tells a reviewer a one-field check from a full record review.
3. **`flag_for_review` is true if and only if `flag_codes` is non-empty**, and `flag_reason` is the prose rendering of the same codes.

#### Output fields

| Field | Column | Meaning |
|---|---|---|
| `flag_for_review` | Flag for Review | boolean; true iff `flag_codes` is non-empty |
| `flag_codes` | Flag Codes | machine-readable codes from the table below; a record may carry several |
| `flagged_fields` | Flagged Fields | which output fields the codes concern (`name1`, `name2`, `name3`, `name4`, `domain`, `contact`, `email`, `address`) |
| `flag_reason` | Flag Reason | human-readable prose, one clause per code, each prefixed with its own field scope |

The scope is encoded in the reason text **as well as** in `flagged_fields`, so a consumer reading only the two pre-Fix-8 columns still learns which field is in doubt.

#### The codes

| Code | Raised when | Scope |
|---|---|---|
| `no-match` | Every tier failed: no identifier, no domain, no evidence URL, no field changed. Suppressed when any other code applies — it means "nothing to go on at all" | `name1` |
| `low-confidence-unchanged` | Canonicalisation was attempted, came back below threshold, and the input value was left in place | the field(s) left as supplied |
| `dept-via-lab` | UC 13 fired: Name 2 was a granular unit and the parent department was **inferred from the lab's page**, not read from a stated department | `name2`, `name3` |
| `name3-not-demoted` | UC 13 fired but every slot below Name 2 was already populated, so the lab name could not be moved down | `name2`…`name5` |
| `person-unresolved` | A person was detected in Name 1 and their affiliation could not be resolved | `name1` |
| `overflow` | UC 0, or preprocessing ran out of name/street slots — one value split across several SAP fields | the overflowing pair (e.g. `name3`, `name4`); the whole name block when preprocessing ran out of slots |
| `opaque-code` | UC 10: Name 1 holds an internal code, not a name (preprocessing clears these from Name 2-5 but never from Name 1) | `name1` |
| `domain-unverified` | The candidate website failed every ownership condition, so nothing was written — see [§2b](#2b--ownership-guard-domain_ownership_guard_enabled-default-on) | `domain` |
| `email-conflict` | An email found in the record differs from a populated email field | `email` |
| `multiple-contacts` | The contact field names more than one person and Tier 2A could not act | `contact`, `name2` |
| `unverified-inference` | Tier 3 **wrote** a value, at any confidence — see [Tier 3](#stage-4-tier-3--llm-inference-last-resort) | the field(s) Tier 3 wrote |

#### What is deliberately NOT flagged

- Any **Tier 1 ROR or LEI match that passed its verification guard** — including the person-affiliation path, which re-enters Tier 1 through the same guards.
- **Tier 2A verified or exact match**, and any Tier 2A outcome that wrote a value backed by a `source_url`.
- **Tier 2 canonicalisation at high confidence.** This was always the documented rule; before Fix 8 the code contradicted it and shipped `"LLM canonical form — verify"` / `"LLM canonical company name — verify"` on 8 of 50 demo records.
- **Tier 2B department search that read a STATED department off an on-domain page.** There is a `source_url` to audit. This replaces the old blanket "Tier 2B results are always flagged".
- **A research institution having no department, no contact, or neither.** An absent department is not a defect and gives a reviewer nothing to do. This rule alone was a fifth of all flags on the demo batch.
- **Any deterministic normalisation** — casing, abbreviation expansion, unit canonicalisation, legal-suffix collapse, a Name 2 dropped because it duplicated Name 1 (Rule 3).
- **Batch consensus inheritance** (Fix 6) — see the note in [Batch Consensus](#batch-consensus).
- **An empty input field that stayed empty.** In particular a blank Name 2 that Finalization Rule 1 leaves blank: nothing was dropped, so nothing is flagged.

---

## Per-Field Provenance and Admissibility

**File:** `enrichment/provenance.py`

One principle, made mechanical:

> Every value the system writes must be attributable after the fact to the source that produced it and the confidence under which it was produced. A written value whose origin cannot be reconstructed is not admissible.

The record-level `tier_used` / `source` / `confidence` triple could not carry that. Row 5 of the demo batch has Name 1 from ROR, Name 2 from a SERP → fetch → LLM chain and a department domain from the probe; one label per record collapses all three. Nor can a record-level label represent a field written **twice**, which is exactly what [Fix 2's Tier 1 re-lookup](#stage-5-tier-1-re-lookup-after-canonicalisation) does to `name1` — an LLM writes it, ROR overwrites it, and the final value alone does not show that an LLM wrote first. A final-state map cannot show that; a log can.

### Phase 1 scope

Six fields: `name1_enriched`, `name2_enriched`, `domain`, `record_type`, `ror_id`, `lei_id`.

These are the fields where a wrong value causes a **wrong merge in Phase 2**, so this is where "not admissible" has consequences. They also carry no personal data, which keeps the provenance store clear of a data-protection question — `contact`, `care_of` and `email` are deliberately excluded for that reason. The write API is general; extending the scope is a change to `SCOPED_FIELDS` and the input-value map beside it.

### The six fields cannot be assigned

Recording provenance is easy to add and easy to bypass, so the enforcement is the point. `_init_result` returns an `EnrichedRecord` — a `dict` subclass on which the six scoped keys are **write-locked**:

```python
record.write("domain", "mit.edu", registry_evidence("ror", ror_id))   # the only way
record["domain"] = "mit.edu"                                          # UnattributedWriteError
record.update({"domain": "mit.edu"})                                  # UnattributedWriteError
```

`evidence` is a required, structured argument; `None` or a bare string raises `MissingEvidenceError`. Reads are unchanged — it is still a dict, so `result.get("name1_enriched")` works everywhere it did. `EnrichmentResult` carries the same lock past finalisation, because [batch consensus](#stage-6-batch-consensus) writes onto already-finalised records.

This generalises what [Fix 1](#2b--ownership-guard-domain_ownership_guard_enabled-default-on) already did for `domain` through `resolve_domain`: that path is now one caller of `record.write` (`utils.domain_resolver.write_domain`) rather than a parallel mechanism.

### The event model

One event per **write**, not per record:

| Field | Meaning |
|---|---|
| `seq` | Monotonic per record, across all fields — so the *interleaving* of writes is reconstructable, not just the per-field order |
| `field` | One of the six |
| `kind` | `write` (a producer decided this value) · `transform` (a deterministic rule restyled a value already present — casing, abbreviation expansion, slot packing) · `revert` (the admissibility gate) |
| `old_value` / `new_value` | `old_value` is null on the first write |
| `producer_chain` | The ordered tools that produced this **one** value, e.g. `["serp", "fetch", "llm_tier2a"]`. A chain is not competing sources; competing sources are separate events on separate `seq` numbers |
| `evidence_ref` | The thing a reviewer opens: a `ror_id` / `lei_id`, `{source_url, retrieved_at}`, `{deployment, prompt_version, temperature}` for an LLM write, `{donor_record_id, …}` for an inheritance |
| `confidence_scale` / `confidence_value` | See below |
| `rule_id` | The use case or guard identifier where one applies |
| `tier` | The tier that ran |

A `transform` is recorded but never becomes the attribution: output casing did not decide that Name 1 is "Massachusetts Institute of Technology", ROR did.

**LLM writes record the deployment, the prompt version and the temperature.** A value produced by a model deployment is not reproducible without them, and deployments are not permanent. The prompt **text** is never logged — `llm/prompts.py` exposes a version identifier per prompt (`tier3_llm/v1:1043574c`), a declared major plus a digest of the prompt pair that shipped, so an edit nobody thought was semantic still moves the version.

### Confidence is not one scale

The single `confidence` float was fed by numbers that are not commensurable:

| Scale | What it is |
|---|---|
| `ror_local` | ROR's local rescore against the record's name variants, 0.0–1.0 |
| `fuzzy_ratio` | RapidFuzz string similarity, 0–100 — Tier 2A/2B matching, child matching, GLEIF's name guard, the domain ownership guard |
| `llm_self_reported` | A model's assertion about its own output. **Not a probability of anything** |
| `registry_exact` | A registry returned an identifier it owns. Not scored, returned |
| `deterministic` | A rule fired. `1.0` means "this rule matched", not "100% likely" |
| `inherited` | Copied from another record in the batch; only as good as the donor's own scale, which travels with it |

0.85 from the first three means three different things, and thresholding them with one number is not sound. Every event therefore carries its scale, and `provenance.comparable(a, b)` is false unless the scales are equal.

The record-level `confidence` field is **kept for backward compatibility and is a coarse projection, not a measurement** — see the note on `api.models.EnrichmentResult.confidence`.

### Guard-rejected candidates

A candidate is logged as a rejection only when a **guard** refused it — the ROR country guard, the distinctive-token guard, the identifier-token guard, Fix 1's domain-ownership guard, GLEIF's name verification. Those are the decision-relevant refusals: the pipeline had a confident answer and deliberately declined it, which is the case most worth being able to defend afterwards. The full candidate list from every lookup is **not** logged — it multiplies volume for little value. Rejections are capped at 3 per field per record and anything beyond the cap is counted in `provenance_rejected_omitted`, never silently dropped.

### The admissibility gate

At the end of `finalise`, every non-null scoped field must carry at least one provenance event. One that does not is inadmissible: the value is **reverted to the input value** and the field is flagged `unattributed-value`. The record is **not** failed — shipping the original input is strictly better than failing the batch, and strictly better than shipping an unattributable value. In tests the same condition is a hard assertion (`assert_admissible`).

### Response shape

Two projections of the same data, both in the `/enrich` JSON:

```jsonc
"provenance": [
  {"seq": 1, "field": "name1", "kind": "write",
   "old_value": null, "new_value": "Massachusetts Institute of Technology",
   "producer_chain": ["ror"], "evidence_ref": "https://ror.org/042nb2s44",
   "confidence_scale": "registry_exact", "confidence_value": 1.0,
   "rule_id": "tier1-ror:parent-match", "tier": 1},
  {"seq": 4, "field": "name2", "kind": "write",
   "old_value": null, "new_value": "MIT Department of Chemical Engineering",
   "producer_chain": ["serp", "fetch", "llm_lab_parent"],
   "evidence_ref": {"deployment": "…", "prompt_version": "lab_parent/v1:cb2174d6",
                    "temperature": 0.0, "self_reported": "high",
                    "source_url": "https://langerlab.mit.edu/…"},
   "confidence_scale": "llm_self_reported", "confidence_value": 0.9,
   "rule_id": "uc13:parent-department-from-lab-page", "tier": 2}
]
```

…plus `provenance_rejected` and `provenance_rejected_omitted`, and **six derived scalar columns** — one per scoped field, format `producer:tier:confidence_band`:

| Column | Example |
|---|---|
| `Name 1 Provenance` | `ror:1:exact` |
| `Name 2 Provenance` | `llm_lab_parent:2:self_high` |
| `Domain Provenance` | `ror:1:exact` |
| `Record Type Provenance` | `classifier:-:rule` |
| `ROR ID Provenance` | `ror:1:exact` |
| `LEI ID Provenance` | `batch_consensus:-:inherited` |

Bands are namespaced by scale (`self_high`, never a bare `high`) precisely so two scalars cannot read as comparable just because both say "high". They are **regenerated from the events** on every write and never maintained separately, so the column and the log cannot drift.

The XLSX output goes from **59 to 65 columns**. `/enrich/file` cannot carry the nested array and emits the six derived columns only; the events ship in the `/enrich` JSON response.

### What this is not

- **Not telemetry.** The log is part of the API response. Application Insights remains operational monitoring and receives none of this.
- **Not persisted.** The API stays stateless per batch and gains no database dependency. ADF decides what to store.
- **Not a behaviour change.** This records what happens; it does not alter it. On the 50-record demo batch, all six scoped fields are byte-identical to the pre-fix run, live and mocked, as are `flag_codes`, `flagged_fields`, `tier_used` and `source`.

**Volume** — 217 events for 50 records (4.34/record; 196 writes, 21 transforms), 53 logged rejections and 34 beyond the cap. Projected at ~43,400 events per 10,000 records. Tests `test_provenance.py`.

---

## Data Models

**File:** `api/models.py`

### Request

The canonical request body mirrors the SAP customer-master export columns one-to-one — each field's JSON key is the exact spreadsheet header (`"Name 1"`, `"Country/Region Key"`, `"Postal Code"`, …):

```json
{
  "records": [
    {
      "Customer": "BSP_001",
      "Name 1": "MIT",
      "Name 2": "Dept of AI",
      "Street 1": "77 Massachusetts Ave",
      "Postal Code": "02139",
      "City": "Cambridge",
      "Region": "MA",
      "Country/Region Key": "US",
      "contact": "Dr. Jane Smith",
      "email": "jsmith@mit.edu"
    }
  ],
  "options": {
    "max_concurrency": 5,
    "serp_provider": "serpapi",
    "skip_tier": null
  }
}
```

**Aliases:** every field also accepts the older snake-case keys for backwards compatibility, so `name1`, `name2`, `zip`, `country`, `state`, and `record_id` (→ `Customer`) all still validate. Header matching is case- and whitespace-tolerant (`"NAME 1"`, `"name1"`, `" Name 1 "` all map to the same field). `contact`, `email`, and `care_of` are auxiliary inputs with no SAP column. Every field is optional; `record_id` falls back to `Customer` → `ECC Customer Number` → empty string.

### Response

The result mirrors every original SAP column (carried through verbatim) plus the enriched name/address fields. The response is intentionally lean: a number of internal fields used by the pipeline (`tier_used`, `confidence`, `source`, `source_url`, `contact_used`, `name2_match_result`, `use_cases_triggered`, `enrichment_status`, `duration_ms`) are marked `exclude=True` in `EnrichmentResult` and therefore **do not appear in the JSON** — they are available only in logs and the batch summary counts.

The two **registry identifiers are deliberately included** in the JSON so the Phase 2 dedup can converge records on a shared id: `ror_id` (institutions, and ROR-matched companies) and `lei_id` (GLEIF-matched companies). A record may carry both if ROR matched it as a company and GLEIF also resolved it.

```json
{
  "results": [
    {
      "record_id": "BSP_001",
      "ecc_customer_number": null,
      "central_deletion_flag": null,
      "comments": null,
      "account_group": null,
      "company_code": null,
      "sales_organization": null,
      "distribution_channel": null,
      "division": null,
      "country_region_key": "US",
      "postal_code": "02139",
      "city": "Cambridge",
      "region": "MA",
      "language_key": null,
      "reconciliation_acct": null,
      "tax_jurisdiction": null,
      "central_delivery_block": null,
      "delivery_priority": null,
      "shipping_conditions": null,
      "delivering_plant": null,
      "created_on": null,
      "created_by": null,
      "vat_registration_no": null,
      "terms_of_payment": null,
      "name1_enriched": "Massachusetts Institute of Technology",
      "name2_enriched": "Department of Electrical Engineering and Computer Science",
      "name3_enriched": null,
      "name4_enriched": null,
      "search_term_1": "MIT",
      "search_term_2": "EECS",
      "department_domain": "https://eecs.mit.edu",
      "care_of_enriched": null,
      "contact_enriched": "Dr. Jane Smith",
      "email_enriched": "jsmith@mit.edu",
      "street_cleaned": "Massachusetts Avenue",
      "house_number": "77",
      "street_2_cleaned": null,
      "street_3_cleaned": null,
      "street_4_cleaned": null,
      "street_5_cleaned": null,
      "suite": null,
      "building": null,
      "floor": null,
      "room": null,
      "unit": null,
      "mail_stop": null,
      "po_box_extracted": null,
      "unloading_point": null,
      "mail_code": null,
      "record_type": "research_institution",
      "ror_id": "https://ror.org/042nb2s44",
      "lei_id": null,
      "domain": "mit.edu",
      "flag_for_review": true,
      "flag_codes": ["low-confidence-unchanged"],
      "flagged_fields": ["name2"],
      "flag_reason": "Name 2: left exactly as supplied — the canonical form could not be established with enough confidence to rewrite it; confirm the value is correct",
      "error": null
    }
  ],
  "summary": {
    "total": 1,
    "enriched": 1,
    "verified": 0,
    "unresolved": 0,
    "failed": 0,
    "research_institution_count": 1,
    "company_count": 0,
    "tier1_resolved": 0,
    "tier1_lei_count": 0,
    "lei_attempts": 0,
    "lei_hits_exact": 0,
    "lei_hits_fuzzy": 0,
    "lei_misses": 0,
    "lei_errors": 0,
    "domain_from_registry": 1,
    "domain_from_email": 0,
    "domain_from_serp": 0,
    "domain_rejected_unverified": 0,
    "tier2a_population_count": 0,
    "tier2a_verification_count": 1,
    "tier2b_count": 0,
    "tier3_count": 0,
    "contact_lookup_attempted": 1,
    "contact_lookup_success": 1,
    "processing_time_ms": 2340
  }
}
```

> A company resolved by Tier 1 LEI would instead show a populated `"lei_id"`, `"domain": null`, and `lei_hits_exact`/`lei_hits_fuzzy` incremented in the summary. Its `"record_type"` is `"company"` only if the *evidence* supports it — GLEIF's legal form, or ROR — not because an LEI was found: a research institution that holds an LEI keeps `"record_type": "research_institution"` alongside its `"lei_id"`.

---

## API Endpoints

### GET /health

Returns service health and feature availability.

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "env": "local",
  "mock_mode": false,
  "tiers_available": [1, 2, 3]
}
```

### GET /tiers

Returns current threshold and provider configuration.

```bash
curl http://localhost:8000/tiers
```

```json
{
  "ror_confidence_threshold": 0.8,
  "fuzzy_match_threshold": 80,
  "max_page_content_chars": 3000,
  "page_fetch_timeout_seconds": 10,
  "default_max_concurrency": 5,
  "serp_provider": "serpapi",
  "mock_mode": false
}
```

### POST /enrich

Main enrichment endpoint. Accepts a batch of records and returns enriched results.

**Example — Acronym resolution (Tier 1):**
```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"Customer": "BSP_005", "Name 1": "UCLA", "City": "Los Angeles", "Region": "CA", "Country/Region Key": "US"}
    ],
    "options": {"max_concurrency": 1}
  }'
```

**Example — Contact lookup, Mode A (Tier 2A):**
```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"Customer": "BSP_001", "Name 1": "Massachusetts Institute of Technology", "City": "Cambridge", "Region": "MA", "Country/Region Key": "US", "contact": "Dr. Jane Smith"}
    ],
    "options": {"max_concurrency": 1}
  }'
```

**Example — Department verification, Mode B (Tier 2A):**
```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"Customer": "BSP_002", "Name 1": "Massachusetts Institute of Technology", "Name 2": "Dept of AI", "City": "Cambridge", "Region": "MA", "Country/Region Key": "US", "contact": "Dr. Jane Smith"}
    ],
    "options": {"max_concurrency": 1}
  }'
```

**Example — Department search, no contact (Tier 2B):**
```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"Customer": "BSP_003", "Name 1": "Stanford University", "Name 2": "Chemistry Department", "City": "Stanford", "Region": "CA", "Country/Region Key": "US"}
    ],
    "options": {"max_concurrency": 1}
  }'
```

**Example — Mixed batch:**
```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"Customer": "B1", "Name 1": "MIT", "Name 2": "Department of Physics"},
      {"Customer": "B2", "Name 1": "Pfizer Inc", "Name 2": "R&D"},
      {"Customer": "B3", "Name 1": "UCLA", "contact": "Dr. John Doe"}
    ],
    "options": {"max_concurrency": 3}
  }'
```

> The snake-case form (`{"record_id": "B1", "name1": "MIT", "name2": "..."}`) is still accepted via field aliases, but the SAP headers above are the canonical input and match what `POST /enrich/file` reads from spreadsheet columns.

### POST /enrich/file

Same enrichment as `/enrich`, but accepts an `.xlsx`/`.xlsm` upload (SAP column headers) and returns an enriched `.xlsx`. Multipart form field: `file`. Query params: `max_concurrency`, `serp_provider`, `skip_tier`. The output columns are defined in `api/output_columns.py` and mirror the JSON response one-to-one — including the **"ROR ID"** and **"LEI ID"** columns.

### POST /issues

Audits an uploaded `.xlsx` against the deterministic Issue Catalogue (`enrichment/issue_detection.py`) and returns the same sheet with one appended `Issues` column. Pure audit — no enrichment, LLM, or external calls.

The catalogue is aligned to **Issue Catalogue v2**: 38 declared entries, of which 35 can be emitted. Each entry carries an explicit `group` (G1–G7), `name`, `field`, `mandatory`, `origin` and `status`. Three things are worth knowing before consuming the output:

- **The group is an attribute, not the code prefix.** v2's **G6 — Not Resolvable by Enrichment** is a *regrouping* of four codes that keep their original `G2-` identifiers (`G2-VAL-001`, `G2-VAL-003`, `G2-VAL-006`, `G2-NAME-012`). Read `ISSUE_CATALOGUE[code].group`; do not split the code string.
- **`G7-VERIFY-001` is not a quality issue.** It is raised *by* successful enrichment, so a steward can be assigned the record through DATAshaper's Category dropdown, and it fires only when the uploaded sheet carries a truthy `Flag for Review` cell. A raw input file has no such column and can never receive it. The per-record trigger is in `Flag Reason`, deliberately not split into finer codes.
- **`origin` records who raises a rule** — `DS` (native DATAshaper rule), `API` (this service), or `BOTH`. All 11 DS-only codes are raised here by default, which duplicates them in DATAshaper; that is deliberate, because `/issues` is also run standalone on a raw workbook. Pass `origins=("API", "BOTH")` to `detect_issues` for a feed that must not duplicate a native DS rule.

The `Issues` column's **shape is unchanged** — one appended column of semicolon-separated bare codes — so the DATAshaper contract is untouched. Only the set of codes that can appear in it changed: `G2-CONTACT-008` and `G2-CONTACT-009` are withdrawn and no longer appear; `G7-VERIFY-001` newly can, on enriched files only.

Run `python3 scripts/issue_catalogue_census.py` for the derived counts and the full per-code table.

### POST /issues/compare

Takes two uploads (`original`, `enriched`), runs the issue detector over both, joins rows by record id, and returns an `.xlsx` issue-reduction report (summary + per-record + remaining-issues sheets).

The report is **segmented into three blocks**, because a single "issues remaining" total conflates three different things:

| Block | Groups | Meaning |
|---|---|---|
| **Reduced** | G1–G5 | Codes with a remediation path. Present before and absent after = work the pipeline did. **The headline reduction % is computed over these groups alone.** |
| **Expected to persist** | G6 | No automated path exists to supply the value. These are *supposed* to survive to the enriched file and be routed to a steward; their persistence is correct behaviour, not a pipeline failure. Excluded from the reduction %. |
| **Verification** | G7 | Raised by successful enrichment. Counting it would inflate the post-pipeline total in proportion to how well enrichment performed — inverting the meaning of the delta. Reported separately, never in any reduction figure. |

### POST /api/dedup/cluster-block

**Phase 2** deduplication adjudicator. Accepts JSON candidate rows grouped into address blocks and returns cluster assignments (JSON in / JSON out). This is documented in depth in [Phase 2 — Deduplication Adjudicator](#phase-2--deduplication-adjudicator).

```bash
curl -X POST http://localhost:8000/api/dedup/cluster-block \
  -H "Content-Type: application/json" \
  -d '{
    "rows": [
      {"row_id": "r1", "block_id": "b1", "name1": "Uni Stuttgart", "name2": "Inst. f. Chemie"},
      {"row_id": "r2", "block_id": "b1", "name1": "University of Stuttgart", "name2": "Department of Chemistry"}
    ]
  }'
```

### POST /api/dedup/file

Same clustering as `/api/dedup/cluster-block`, but accepts an `.xlsx`/`.xlsm` upload and returns an `.xlsx` with the cluster-assignment columns appended. It accepts the human SAP headers emitted by `/enrich/file` ("Customer", "Name 1", "Street 1", …) or the snake_case `DedupRow` field names. Multipart form field: `file`. Exactly **one** cluster key is exposed on the main sheet — `Cluster ID`, a stable content hash (`c_` + 12 hex of sha256 over the sorted member row_ids); internal keys (`Block ID`, `Signature ID`) are written to a separate `Dedup Debug` sheet.

### POST /api/dedup/score and /api/dedup/score/file

Deterministic golden-record election over the clustered rows (no LLM). Each duplicate cluster elects a proposed winner; losers point at it. A merge is demoted to `manual_review` when clustering already flagged it uncertain, when every member is blocked, or when the adjudication confidence is below `CONFIDENCE_MERGE_THRESHOLD` (default `0.95`, env-overridable — a pure data retune that never re-runs the LLM). A `manual_review` row leaves `is_golden_record`/`golden_record_id` **empty** in the file; its computed winner survives in `proposed_golden_id`.

**Identical columns, two transports.** The JSON `/api/dedup/score` endpoint and the XLSX `/api/dedup/score/file` endpoint are functionally identical and use the **exact same column names**, so a caller can move between them without remapping fields:

- **Input columns** (both) — `Customer`, `Cluster ID`, `Routing`, `Confidence`, `Reasoning`, `Sales_Order_Last_Used`, `Sales_Order_Total_Count`, `Sales_Order_Partner_Last_Used`, `Sales_Order_Partner_Total_Count`, `Equipment_Total_Count`, `SleepingCustomer`, `CustomerStatus`, `Account group`, `Company_Code_Consolidated`, `Sales_Org_Consolidated`, and the eight flat Salesforce id columns `sf1`…`sf8` (`sf1` = Biosystems, `sf2` = AXS, `sf3`…`sf8`). The JSON body accepts these exact keys; snake_case names (`row_id`, `last_order_year`, …) and a legacy `salesforce_ids` list still validate for backward compatibility (`populate_by_name`).
- **Output columns** (both) — `score_final` (total) and the eleven per-criterion point columns (`score_SalesOrderLastUsed`, `score_SalesOrderCount`, `score_SalesOrderPartnerLastUsed`, `score_SalesOrderPartnerCount`, `score_EquipmentCount`, `score_SleepingCustomer`, `score_CustomerStatus`, `score_AccountGroup`, `score_CompanyCodeCount`, `score_CombinedPresence`, `score_SalesforceInstances`), the derived counts `Company_Code_Count` / `Sales_Org_Count` / `Salesforce_Instance_Count`, and the election columns `is_golden_record`, `golden_record_id`, `proposed_golden_id`, `election_status`, `approval_status`, `scored_with_weights_version`. The JSON serializes these exact keys (the per-criterion points, exposed as a `score_breakdown` dict internally, are flattened to the `score_*` columns on output).
- **Weights override** (both) — a caller may supply a custom weights table (same structure as `dedup/weights.json`): the file endpoint reads it from an optional `Weights` sheet, and the JSON endpoint accepts an optional `weights` object in the request body. Both use the same all-or-nothing rule (`coerce_weights`): a valid override applies wholesale; a malformed one is ignored wholesale with a warning in `summary.warnings` (never a hard error). Omit it to use `dedup/weights.json`.

Both endpoints emit a **potential-inconsistency list** (the reviewer feedback loop): the file gets a second `Issues` sheet (`row_id, cluster_id, issue_type, detail`; the `Weights` sheet and all originals are preserved), and the JSON response returns the same list under `issues`. Issue types: `low_confidence_merge`, `verdict_contradiction`, `all_blocked_cluster`, `tiebreak_decided`, `empty_scoring_payload`, `count_suppressed_by_recency` (a sales-order count zeroed by the G1 recency gate), `candidate_cap_exceeded` (a block routed to manual_review for blowing the residue-candidate cap), and a reserved `missing_building_inconsistency`.

An offline **evaluation harness** scores a workbook against its `expected_*` fixture columns: `python -m eval.dedup_eval <scored.xlsx>` prints pairwise precision/recall/F1 plus the named business-risk counts (`wrongful_block_candidates`, `competing_goldens`, `uncertainty_upgrades`) with offending row_ids, and writes `eval_report.json`.

### POST /api/dedup/approve

Records a human's approve/reject decision on one cluster. Stateless: submit the scored rows plus `{"cluster_id", "decision": "approved"|"rejected", "approver"}`; the decision is applied to that cluster (on `approved` the proposed winner is promoted into the golden fields) and the updated rows are echoed back. Persistence is out of scope for now.

> **Phase 3 contract:** Phase 3 consumes **only** rows with `approval_status == "approved"` or `election_status == "unique"`. Everything else is a proposal awaiting human sign-off — filtering on `is_golden_record` alone must never be used to act on unreviewed rows.

### GET /diag/llm

Diagnostic for **Phase 1** LLM connectivity. Makes one real LLM call and returns the raw outcome plus an environment snapshot. Useful on Azure when logs are not visible — the actual exception string is returned in the HTTP body.

### GET /diag/dedup-llm

Diagnostic for the **Phase 2** adjudicator client. Makes one real dedup adjudication call and returns the raw response, error string (if any), the resolved `api_version`, and whether `reasoning_effort` is in use. Use this when `/api/dedup/cluster-block` returns everything as `manual_review` with `errors > 0` — the per-block handler swallows LLM errors to keep the batch alive, so this is how you surface the real Azure error (e.g. an unsupported API version or a rejected `reasoning_effort` parameter).

```bash
curl http://localhost:8000/diag/dedup-llm
```

---

## Phase 2 — Deduplication Adjudicator

> **Files:** `dedup/` package (`models.py`, `signatures.py`, `prompts.py`, `llm.py`, `adjudicator.py`), wired into `api/routes.py`.
> **Endpoint:** `POST /api/dedup/cluster-block`
> **Mock:** `tests/mocks/dedup_mock.py` · **Tests:** `tests/test_dedup.py`

### Why a Separate Pass

Phase 1 (`/enrich`) cleans each record in isolation. It cannot tell whether two *different* records are actually the same customer — that is a cross-record decision. In the Bruker MDM pipeline, after enrichment runs and **DATAshaper applies its address gates** (grouping records that share the same country + postal code + street), there is still a question left over: *within a block of records at the same address, which ones are genuine duplicates of the same organizational entity, and which are distinct units that merely share a building?*

Phase 2 answers exactly that. It takes the address-gated rows, decides which refer to the same real-world `(institution, department)` entity, and emits **clusters**. It does **not** do address validation, embeddings, golden-record election, or file I/O — those are out of scope and handled elsewhere in the pipeline. The orchestrator (ADF/DATAshaper) handles file ↔ JSON conversion; this endpoint is strictly JSON in / JSON out.

### The Two-Level Identity Model

Identity is modelled at **two levels**:

- **Name 1 = the institution / company** (e.g. "University of Stuttgart", "Siemens AG").
- **Name 2 = a department / sub-unit within it** (e.g. "Department of Chemistry") — *may be empty*.

An **entity** is a specific `(institution, department)` combination. Two records are duplicates **only when they map to the same entity**. A cluster is a set of rows that all map to one entity *and* contains at least two rows.

### Critical Identity Rules

These rules are enforced consistently by the algorithm (some deterministically in code, some by the LLM):

| Situation | Decision |
|---|---|
| Same institution **+** same department (or both Name 2 empty) | **Same entity** → may cluster |
| Same institution **+** *different* department | **Different entities** → never merged (e.g. "Uni Stuttgart, Dept of Chemistry" vs "Uni Stuttgart, Dept of Mechanical Eng" — two entities even though both resolve to the same ROR ID) |
| One Name 2 empty, the other populated | **Different entities** (institution-level vs department-level record). Deterministic — enforced in code, never sent to the LLM |
| Two records share a **ROR ID** | Means same **institution only**. It is a *hint*, never a cluster decision by itself, and never overrides the Name 2 comparison |
| Two records share a **LEI** | Means same **legal entity** (typically a company). Treated like ROR — a strong same-institution hint, but still does not merge records with different Name 2 departments, and never overrides the Name 2 comparison. Different non-empty LEIs are a strong *different*-entity signal |
| An entity has only **one** row | **No cluster** (`cluster_id = null`, routing `unique`). Singleton clusters are never minted |

### Endpoint Contract

**Route:** `POST /api/dedup/cluster-block`
**Auth:** inherited from the Azure Function App (same key/function-auth pattern as every other endpoint — there is no per-route auth).
**Body:** one or more address blocks in a single call; each block is processed independently.

**Input schema** (`dedup/models.py::DedupRow`):

```json
{
  "rows": [
    {
      "row_id": "string",            // caller's stable key, echoed back
      "block_id": "string|null",     // address block; if null, derived from normalized (country, postal_code, street, house_no)
      "name1": "string",             // institution / company
      "name2": "string|null",        // department / sub-unit
      "street": "string|null",
      "house_no": "string|null",
      "postal_code": "string|null",
      "city": "string|null",
      "country": "string|null",
      "ror_id": "string|null",       // ROR id from Phase 1 (institution hint)
      "lei_id": "string|null",       // GLEIF LEI from Phase 1 (company legal-entity hint)
      "enriched_name": "string|null" // Phase 1 official name, if resolved
    }
  ]
}
```

Unknown extra keys are silently ignored (Pydantic default), so you can pass through additional columns without error — but only the fields above influence the decision.

**Output schema** (`dedup/models.py::DedupResponse`):

```json
{
  "rows": [
    {
      "row_id": "string",
      "block_id": "string",
      "cluster_id": 1,                 // integer, sequential & globally unique across the response; null when not clustered
      "routing": "cluster | unique | manual_review",
      "llm_flag": true,
      "signature_id": "s1",
      "confidence": 0.95,              // entity confidence if clustered/uncertain, else null
      "reasoning": "string|null",
      "model": "gpt-5.4",
      "model_version": "gpt-5.4-2025-...",
      "prompt_version": "p2-dedup-v3"
    }
  ],
  "summary": {
    "blocks": 2, "rows_in": 8,
    "distinct_signatures": 6,
    "clusters": 3, "rows_clustered": 7,
    "rows_unique": 1, "rows_manual_review": 0,
    "llm_calls": 2, "errors": 0
  }
}
```

> **Note on `cluster_id`:** it is a plain sequential integer (`1, 2, 3, …`) running globally across the response, so it is unique within one response. If you split a file across multiple calls, each call restarts at 1 — offset the ids caller-side, or send all of a file's blocks in one call, if you need file-wide uniqueness.

### The Per-Block Algorithm

Rows are grouped by `block_id` (deriving one from the normalized address when absent), then each block runs through three steps.

#### STEP A — Collapse to distinct signatures (no LLM)

This is the **blow-up guard**. The endpoint must never send thousands of raw rows to the LLM.

- A **conservative normalized key** is computed per row on *both* `name1` and `name2`: lowercase, trim, collapse internal whitespace, strip punctuation, fold accents (`Universität` → `universitat`). It does **not** strip legal forms or expand abbreviations — that is the LLM's job. The key is internal only and never shown to the LLM.
- A **signature** is a distinct `(norm_name1, norm_name2)` key. Each signature records: the list of `row_id`s that share it, the original (un-normalized) `name1`/`name2` from the first row, and the `ror_id` / `lei_id` if any row in it carries one (the first non-empty value seen).
- Result: 100 byte-identical rows collapse to **1** signature; 100 rows spread across 8 departments collapse to ~8 signatures. The LLM only ever works on distinct signatures.

#### STEP B — Group signatures into entities (LLM)

Let `N` = number of distinct signatures in the block. The mode is chosen by `N` relative to `SIG_PARTITION_THRESHOLD` (env, default 12). See [Mode A vs Mode B](#mode-a-vs-mode-b). A hard deterministic Name 2 constraint applies in both modes (see [below](#the-deterministic-name-2-asymmetry-rule)).

#### STEP C — Emit clusters and fan out

- Each entity's row set is the union of `row_id`s across its signatures.
- An entity with **≥ 2 rows** gets a `cluster_id`; every row in it carries that id. An entity with one row gets `cluster_id = null`.
- Per-row routing is assigned (see [Routing](#routing-clusters-and-the-llm_flag)), and the decision is fanned back out to every original row.

### Mode A vs Mode B

| | **Mode A — partition** | **Mode B — incremental canonical assignment** |
|---|---|---|
| **When** | `N ≤ SIG_PARTITION_THRESHOLD` (default 12) | `N > threshold` |
| **LLM calls** | One partition call per populated-Name 2 bucket (singleton buckets need no call) | One call per signature, comparing it against the current canonical entities |
| **Cost** | One (or two) calls per block | O(signatures), each prompt bounded |
| **Returns** | Entities + `uncertain_signature_ids` | `match` / `new` / `uncertain` per signature |

**Mode A** sends all signatures (within a Name 2 bucket) and asks the LLM to partition them into entities. **Mode B** keeps a growing list of canonical entities; each new signature is compared against the compatible canonicals, and is either merged into one, started as a new entity, or marked uncertain. Mode B keeps per-call prompt size bounded for large blocks while still producing N-way clusters.

### The Deterministic Name 2 Asymmetry Rule

> *A signature with an empty Name 2 can **never** share an entity with a signature that has a populated Name 2.*

This is an institution-level vs department-level distinction and is **never delegated to the LLM**:

- **Mode A** partitions signatures into an empty-Name 2 bucket and a populated-Name 2 bucket *before* any call, so the two are never compared by the model. A post-LLM safety net (`_enforce_name2_split`) additionally splits any entity that ever mixes the two, in case a future prompt change lets it slip through.
- **Mode B** only ever presents canonicals whose `has_name2` matches the candidate; an incompatible candidate starts a new entity with no LLM call.

### Residue Candidate Nomination

Mode A/B only compare signatures *within* a `has_name2` bucket. Two kinds of pair therefore slip through untouched and default to `unique` with no reasoning: an **empty-Name 2** signature vs a **populated-Name 2** one, and a signature **alone in its bucket**. After Mode A/B, `_adjudicate_residue` (backed by `dedup/candidates.py`) widens coverage over exactly this residue:

1. **Nominate** each residue pair that carries a same-entity signal, in priority order — converging **ROR/LEI id**, then suffix-stripped **name similarity** (Jaro-Winkler ≥ `NAME_CANDIDATE_THRESHOLD`, default `0.85`), then **token-set overlap** (Jaccard ≥ `TOKEN_CANDIDATE_THRESHOLD`, default `0.6`). Legal-form suffixes (AG/GmbH/Inc/…) are stripped for the similarity math only, never from the signature. Nomination is deterministic and pure — same units in any order yield the same ordered candidate list.
2. **Adjudicate** each nominated pair with a pairwise LLM call. Nomination never merges — the verdict + the two-level identity rule decide, and the residue pass runs **before** the identity guard so a bad name/token merge across conflicting ROR/LEI is still split. Every nominated pair records reasoning on both sides, including rejects.
3. **Cap:** if a block nominates more than `MAX_CANDIDATES_PER_BLOCK` pairs (default `50`), the whole block is routed to `manual_review` (deterministic ordering already put id-convergence pairs first) and a `candidate_cap_exceeded` issue is emitted.

Telemetry adds `candidates_generated`, `candidates_by_rule`, `rejected_with_reasoning`, and `candidate_cap_exceeded` per block and per request.

### LLM Call Details

| Aspect | Value |
|---|---|
| **Model** | GPT-5.4 (full, not mini/nano), AI Foundry deployment from env `AOAI_DEPLOYMENT_DEDUP` |
| **Client** | Reuses the Phase 1 AI Foundry client factory (`llm/openai_client.py::get_openai_client`) — no new client is written |
| **API version** | `AOAI_API_VERSION_DEDUP` (default `2025-04-01-preview`) — newer than Phase 1's default because reasoning models / `reasoning_effort` require it |
| **Reasoning effort** | `low` (`DEDUP_REASONING_EFFORT`) |
| **Temperature** | `0.0`, but sent **only when `reasoning_effort` is not in use**. The two are mutually exclusive on a reasoning deployment: gpt-5.4 answers a request carrying both with `400 Unsupported value: 'temperature' does not support 0.0 with this model. Only the default (1) value is supported.` On the default config (`reasoning_effort=low`) temperature is therefore not sent, and adjudication output is **not** bit-reproducible. `seed` **is** accepted by the deployment alongside `reasoning_effort` and is the stronger control, but is not yet sent |
| **Response format** | `{"type": "json_object"}`, parsed defensively (plain JSON, fenced ```json blocks, or embedded objects) |
| **Concurrency** | Bounded by `DEDUP_MAX_CONCURRENCY` (default 5) via a shared semaphore across all blocks |
| **Retries** | 429/5xx and connection/timeout errors retried with exponential backoff, max `DEDUP_MAX_RETRIES` (default 3) |
| **Resilience** | If the deployment rejects `reasoning_effort` — or `temperature`, where it is sent — the offending parameter is dropped and the call retried (both are tuning preferences, not correctness gates) |
| **Prompt version** | `PROMPT_VERSION = "p2-dedup-v3"`, logged on every decision and echoed in every output row |

A single bad LLM call **never fails a whole block**. The affected signature(s) are marked uncertain (→ `manual_review`) and processing continues; the error is logged with the `block_id` and offending `signature_id`s and counted in `summary.errors`.

The LLM always sees the **original** `name1`/`name2`, never the normalized key.

### Routing, Clusters, and the llm_flag

Each output row carries a `routing` value (priority order):

1. **`manual_review`** — the row's signature was returned uncertain by the LLM (an unresolved possible merge). Takes priority. The row still keeps whatever certain `cluster_id` it already belongs to (its identical rows still cluster); only the *merge* with another entity is uncertain.
2. **`cluster`** — the row is in a confirmed duplicate cluster with no open question.
3. **`unique`** — no cluster, no uncertainty.

`llm_flag` is `true` whenever a row's cluster membership depended on an **LLM merge across distinct signatures**; it is `false` when the cluster is a pure identical-signature collapse from STEP A (no LLM involved). A unique row is always `false`.

`confidence` and `reasoning` are populated for clustered or uncertain rows (the entity's values) and are `null` for plain unique rows.

### Telemetry

Telemetry is emitted as structured logs; Azure Functions ships them to the **`mdm-pipeline-insights`** Application Insights instance (enabled in `host.json`). No new telemetry SDK is added — it reuses the existing logging integration.

- **Per block:** `block_id`, `rows_in`, `distinct_signatures`, `mode` (A/B), `llm_calls`, `clusters`, `rows_manual_review`, `errors`.
- **Per LLM call:** `latency_ms`, prompt/completion tokens, decision counts, `model_version`, `prompt_version`.
- **Per request:** the full summary object plus total tokens and total latency.

### Chaining Enrichment → Dedup

In production, DATAshaper supplies `block_id`. When testing the two endpoints by hand, map a `/enrich` **response** onto a `/api/dedup/cluster-block` **request** like this:

| dedup `DedupRow` field | from `/enrich` result field |
|---|---|
| `row_id` | `record_id` |
| `name1` | `name1_enriched` |
| `name2` | `name2_enriched` |
| `street` | `street_cleaned` |
| `house_no` | `house_number` |
| `postal_code` | `postal_code` |
| `city` | `city` |
| `country` | `country_region_key` |
| `enriched_name` | `name1_enriched` |
| `block_id` | *(leave null to derive, or assign explicitly — see note)* |
| `ror_id` | `ror_id` *(now included in the `/enrich` response — populated for institutions and ROR-matched companies)* |
| `lei_id` | `lei_id` *(included in the `/enrich` response for GLEIF-matched companies; now consumed by dedup as an LLM hint, exactly like `ror_id`)* |

> **`block_id` caveat:** deriving the block id from the enriched address only groups rows correctly if their cleaned `street`/`house_no`/`postal_code` come out identical. If enrichment cleans the same address inconsistently across rows, assign an explicit `block_id` (as DATAshaper does) so duplicates land in the same block.

### Dedup Diagnostics

If `/api/dedup/cluster-block` returns all rows as `manual_review` with `errors > 0`, the LLM calls are failing and being swallowed by the per-block safety net. Hit **`GET /diag/dedup-llm`** to see the real Azure error, the resolved API version, and whether `reasoning_effort` is active. The most common cause is an API version too old for the GPT-5.4 reasoning deployment (fixed by setting `AOAI_API_VERSION_DEDUP`).

---

## Project Structure

```
enrichment_api/
├── main.py                       # Local uvicorn entry point
├── function_app.py               # Azure Function v2 ASGI entry point
├── config.py                     # Environment variable loading (Settings dataclass)
│
├── api/                          # FastAPI application layer
│   ├── app.py                    # FastAPI app instance (shared between local + Azure)
│   ├── routes.py                 # Route definitions: enrichment, issues, dedup, diagnostics
│   ├── models.py                 # Pydantic v2 request/response schemas (Phase 1)
│   ├── output_columns.py         # Response-field → XLSX column mapping
│   └── middleware.py             # Request logging, timing, error handling
│
├── dedup/                        # Phase 2: deduplication adjudicator + election
│   ├── models.py                 # Pydantic schemas: DedupRow/Request/ResultRow/Summary/Response
│   ├── signatures.py             # STEP A: normalization, block derivation, signature collapsing
│   ├── prompts.py                # System + Mode A/B prompts, PROMPT_VERSION
│   ├── llm.py                    # DedupLLM (reuses get_openai_client), defensive JSON parsing
│   ├── candidates.py             # Residue candidate nomination (ID / name / token) for STEP B widening
│   ├── cluster_key.py            # Stable content-hash cluster id (shared by adjudicator + scorer)
│   ├── adjudicator.py            # STEP B/C: entity grouping, modes, residue pass, clustering, telemetry
│   ├── scoring.py                # Pass 3: deterministic scoring + golden-record election (no LLM)
│   ├── scoring_xlsx.py           # XLSX in-place scoring/election writeback (openpyxl, Issues sheet)
│   └── weights.json              # Editable scoring weights table (criterion → band → points)
│
├── eval/                         # Offline evaluation harness (thesis metrics)
│   ├── dedup_eval.py             # Pairwise P/R/F1 + named business-risk counts vs expected_* columns
│   └── __init__.py
│
├── enrichment/                   # Core enrichment pipeline
│   ├── orchestrator.py           # Main pipeline controller (tier escalation, finalization)
│   ├── preprocess.py             # Deterministic cleanup: UC 6-12 (regex-based)
│   ├── classifier.py             # THE record_type authority — ranked evidence, decided once in finalise
│   ├── elf_codes.py              # ISO 20275 legal-form codes split by commercial character (generated)
│   ├── overflow_check.py         # UC 0: adjacent-name-pair overflow detection
│   ├── tier1_ror.py              # Tier 1: ROR API client, scoring, child matching, acronym expansion
│   ├── tier1_lei.py              # Tier 1 (company): GLEIF/LEI registry client + verification guard
│   ├── tier2a_contact.py         # Tier 2A: Contact person lookup (Modes A & B)
│   ├── tier2b_dept.py            # Tier 2B: Department web search
│   ├── tier2_canonical.py        # Tier 2 Canonical: LLM-only department normalization
│   ├── lab_resolver.py           # UC 13: granular unit → parent department resolver
│   ├── tier3_llm.py              # Tier 3: Pure LLM inference (last resort)
│   ├── company_canonical.py      # Company name canonicalization via LLM
│   ├── batch_consensus.py        # Stage 6: propagate one identity across a batch's same-org/same-address rows
│   ├── flags.py                  # THE flag authority — codes, scopes and reasons, rebuilt once in finalise
│   ├── provenance.py             # Write-locked record + per-field provenance log + admissibility gate
│   └── confidence.py             # Status assignment (the flag logic that lived here is dead — see flags.py)
│
├── llm/                          # LLM integration layer
│   ├── openai_client.py          # AsyncAzureOpenAI wrapper (JSON mode, retries, api_version param)
│   ├── prompts.py                # All LLM prompt templates as module constants + prompt version ids
│   └── test_connection.py        # Connection test utility
│
├── search/                       # Web search abstraction
│   ├── base.py                   # Abstract SearchClient interface + SearchResult dataclass
│   ├── serpapi_client.py         # SerpAPI implementation (Google Search)
│   ├── duckduckgo_client.py      # DuckDuckGo fallback (no API key needed)
│   └── page_fetcher.py           # HTTP fetch + BeautifulSoup structured extraction
│
├── utils/                        # Shared utilities
│   ├── text_utils.py             # Cleaning, normalization, country codes, domain extraction
│   ├── domain_resolver.py        # Single write path for `domain` / `website_url` + ownership guard
│   ├── cache.py                  # Cache-key builders (normalised name + country) + per-batch SERP cache
│   └── __init__.py
│
├── tests/                        # Test suite
│   ├── conftest.py               # Fixtures and mock injection
│   ├── test_*.py                 # Unit tests per module
│   ├── test_dedup.py             # Phase 2 dedup adjudicator tests (algorithm + route)
│   ├── mocks/                    # Mock client implementations
│   │   ├── lei_mock.py           # Deterministic GLEIF/LEI client for tests
│   │   └── dedup_mock.py         # Conservative offline dedup LLM (never invents merges)
│   └── fixtures/                 # JSON test data for various scenarios
│
├── scripts/                      # Development and debugging tools
│   ├── test_local.py             # Integration test runner (--mock / --live / --fixture)
│   ├── debug_ucsf.py             # Debugging utility
│   └── verify_fixes.py           # Post-fix verification script
│
├── requirements.txt              # Production dependencies
├── requirements-dev.txt          # Dev/test dependencies (pytest, etc.)
├── host.json                     # Azure Functions host configuration
├── .env.example                  # Environment variable template
└── pytest.ini                    # Pytest configuration
```

---

## Module-by-Module Reference

### `config.py` — Configuration

Loads environment variables into a frozen `Settings` dataclass at startup. Uses `python-dotenv` for local `.env` file loading. All thresholds, timeouts, and feature flags are centralized here.

### `api/app.py` — FastAPI Application

Creates the shared FastAPI app instance with middleware attached. This single app is used by both `main.py` (local uvicorn) and `function_app.py` (Azure Functions ASGI wrapper).

### `api/routes.py` — Route Definitions

Defines all endpoints: `/health`, `/tiers`, `/enrich`, `/enrich/file`, `/issues`, `/issues/compare`, the Phase 2 `/api/dedup/cluster-block` and `/api/dedup/file`, and the `/diag/llm` + `/diag/dedup-llm` diagnostics. The `/enrich` endpoint instantiates the Orchestrator and runs `enrich_batch()`; the dedup endpoint builds a `DedupLLM` (or its mock in mock mode) and runs `cluster_blocks()`, closing the client cleanly in a `finally` block.

### `api/models.py` — Pydantic Schemas

Defines `EnrichmentRecord`, `EnrichmentOptions`, `EnrichmentRequest`, `EnrichmentResult`, `EnrichmentSummary`, `EnrichmentResponse`, `HealthResponse`, and `TierConfigResponse` using Pydantic v2. Input fields accept SAP column headers as primary aliases with snake-case as secondary aliases (`populate_by_name=True`). The Phase 2 dedup schemas live separately in `dedup/models.py`.

### `api/middleware.py` — Request Middleware

Adds request logging with unique IDs, timing headers (`X-Request-ID`, `X-Duration-MS`), and structured JSON log output. Catches unhandled exceptions and returns clean 500 responses.

### `enrichment/orchestrator.py` — Pipeline Controller

The heart of the system. The `Orchestrator` class coordinates the full pipeline for each record: overflow check -> preprocessing -> Tier 1 -> Tier 2 -> Tier 3 -> finalization. Manages async concurrency via `asyncio.Semaphore`. Contains the `finalise()` function with all post-processing rules.

### `enrichment/preprocess.py` — Deterministic Cleanup

Pattern-matching engine for UC 6-12. Runs before any network call. Returns `PreprocessResult` with cleaned fields and tracking of which use cases fired. Also handles: contact-person extraction (incl. credentialed/`Last, First` normalisation and the `name1_was_person` signal), Name 1 acronym/full-form dedupe (`_strip_redundant_acronym`), and routing organisation/department content out of street fields into the Name block (`_split_pipe_street`, `_split_comma_street`, `_street_is_org_name`, `_street_is_department`) with institution→Name 1 ordering and `name-slots-full` overflow flagging.

### `enrichment/tier1_ror.py` — ROR Client

Async ROR API client with hybrid lookup (affiliation + query), sophisticated name scoring with distinctive-token guards, legal-form suffix normalization, an identifier-acronym guard, local child matching, and organization type extraction for classification. Includes `_INSTITUTION_ACRONYMS` + an additive acronym-expanded affiliation retry (e.g. "HFT Stuttgart" → "Hochschule für Technik Stuttgart"), and `_US_STATE_ABBREVS` state-abbreviation expansion applied ROR-locally to the query ("Fla State Univ" → "Florida State Univ", so it resolves to Florida State rather than Kent State). Uses `resolve_tls_verify()` for corporate-VPN TLS.

### `enrichment/classifier.py` — Record Type Authority

The single place `record_type` is decided. `classify(TypeEvidence)` returns `(record_type, record_type_source)` from ranked evidence — ROR org types, then GLEIF entity metadata, then the keyword heuristic, then `unknown` — with the LEI guard that stops an LEI alone from asserting `company`. Called once, at the end of `finalise`. Every tier before that writes `routing_type` instead, which gates which tiers run and never leaves the pipeline. Full detail in [Record Classification Logic](#record-classification-logic).

### `enrichment/elf_codes.py` — ISO 20275 Legal Forms (generated)

`NON_COMMERCIAL_ELF` (95 codes) and `COMMERCIAL_ELF` (978) — the subset of GLEIF's 3,599 active Entity Legal Forms whose *names* state a commercial character outright. Generated from the GLEIF ELF registry at development time, because a `lei-records` response carries only `legalForm.id` and never its name; nothing is looked up at runtime. An unlisted code means "no evidence", not "company".

### `enrichment/tier1_lei.py` — GLEIF / LEI Client (company Tier 1)

Async GLEIF client (`call_lei` + `LEIClient`), structured like the ROR client: precise `legalName`+country+ACTIVE filter, then `fuzzycompletions` fallback, retries/backoff, a module-level cache, and `resolve_tls_verify()` TLS. Enforces the RapidFuzz `token_sort_ratio` verification guard (legal-form-suffix-aware) so unverified/fabricated hits are rejected. Returns a match dict (LEI, legal name, country, strategy, confidence) or a clean miss/error — never raises, so a GLEIF failure can't fail the record.

### `enrichment/tier2a_contact.py` — Contact Lookup

SERP search for contact person pages, name verification filtering, page fetching, and LLM-based affiliation extraction. Supports population (Mode A) and verification (Mode B).

### `enrichment/tier2b_dept.py` — Department Search

SERP search for department pages, candidate ranking (on-domain priority), structured element extraction, and LLM-based official name extraction from URL/title/H1/breadcrumb only.

### `enrichment/tier2_canonical.py` — LLM Canonicalization

Single LLM call to normalize department names to official wording. No web search — relies on LLM knowledge. Conservative: only accepts high-confidence results.

### `enrichment/tier3_llm.py` — LLM Inference

Last-resort LLM call using all available fields. Always flagged for review. High/medium confidence suggestions are written; low confidence preserves originals.

### `enrichment/person_affiliation.py` — Person Affiliation Lookup

`run_person_affiliation`: when Name 1 held only a person's name, PROPOSES the institution + department from web-search snippets (one SERP + one grounded LLM extraction). It only proposes — the orchestrator ([Stage 2b](#stage-2b-person-affiliation-lookup)) confirms the institution against ROR in the record's country and uses ROR's official name/domain before writing anything; otherwise the record is flagged for a manual lookup. Never fabricates.

### `enrichment/company_canonical.py` — Company Canonicalization

Specializes in normalizing company names with geographic context. Used when Tier 1 misses and the record doesn't look like a research institution.

### `enrichment/overflow_check.py` — Overflow Detection

LLM-based check for Name1+Name2 being a single split organization name. Early-exit mechanism that prevents mis-enrichment of overflow records.

### `enrichment/website_resolver.py` — Website Resolution (Paths B/C)

`resolve_website_via_serp` (Path B — SERP, with the distinctive/acronym-in-host ranking, generic-token guard, TLD-needs-host-match confidence rule, and the unquoted retry) and `infer_website_via_llm` (Path C — LLM fallback). `select_website_from_serp` holds the pure ranking logic; `_assemble_path_b_trace` builds the read-only `WEBSITE_TRACE` diagnostic. Path A (ROR's `links[]` website) is handled inline in the orchestrator. All three paths return a *candidate*: `orchestrator._apply_domain` hands it to `domain_resolver.resolve_domain()`, which owns the write. See [Website, Domain, Department-Domain & Search-Term Resolution](#website-domain-department-domain--search-term-resolution).

### `enrichment/search_terms.py` — Search-Term Derivation

`derive_search_terms(result)` computes `search_term_1` (institution) and `search_term_2` (department) and applies terminal normalisation (uppercase, trimmed, ≤32 chars on a word boundary). Also exposes the shared helpers `strip_tld`, `clean_name2_phrase`, `extract_dept_core`, `derive_acronym`, and `_dept_domain_to_search_term`. See [Website, Domain, Department-Domain & Search-Term Resolution](#website-domain-department-domain--search-term-resolution).

### `enrichment/batch_consensus.py` — Batch Consensus (Stage 6)

`apply_batch_consensus` converges organisation-level fields across a finalised batch, in place. Groups by address block (`derive_block_id`, reused from `dedup/signatures.py`) then by canonicalised Name 1 plus a compatible legal form. A group with exactly one registry identity propagates `ror_id`, `lei_id`, `name1_enriched`, `domain`, `website_url` and `record_type` from a deterministically elected donor; a group with none falls back to `_consensus_name_form` (modal Name 1 spelling) plus unanimous-gap-fill on the remaining fields, never choosing between competing values. Inheriting records are marked `source = "batch_consensus"`. `PROPAGATED_FIELDS` and `NEVER_PROPAGATED` are module-level data so the exclusion of department-level fields is readable and testable. Never merges, drops or reorders a record; never touches `tier_used` or any flag — it runs after every record is finalised, so the flags are already settled. Full description in [Stage 6: Batch consensus](#stage-6-batch-consensus).

### `enrichment/confidence.py` — Enrichment Status

Derives `enrichment_status` from confidence, match result and tier. Flag rules used to live here too, in a `should_flag_for_review` function that **nothing ever called** — every tier set `flag_for_review` inline as it ran, which is how the code came to contradict its own documented rules. Fix 8 removed it; flags now live in `enrichment/flags.py`.

### `enrichment/flags.py` — Review Flags

The single flag authority. `compute_flags` is called once, from `finalise`, and rebuilds `flag_for_review`, `flag_codes`, `flagged_fields` and `flag_reason` from the record's final state. Tiers record evidence (`_ev_*` transient keys, stripped here) and never write a flag, so no reason can name a tier that ran and no reason can mask another. Holds the code vocabulary, the detection rule for each code, the field-scope vocabulary and the reason prose. Full description in [Flag Rules](#flag-rules). Tests `test_flags.py`.

### `enrichment/provenance.py` — Per-Field Provenance

`EnrichedRecord` (the write-locked working record), the `Evidence` / `ProvenanceEvent` / `RejectedCandidate` model, the confidence-scale vocabulary and its bands, the derived-scalar projection, and the admissibility gate. Everything the pipeline writes to one of the six scoped fields passes through `EnrichedRecord.write`; there is no other way in, and direct assignment raises. Full description in [Per-Field Provenance and Admissibility](#per-field-provenance-and-admissibility). Tests `test_provenance.py`.

### `dedup/models.py` — Dedup Schemas

Pydantic v2 models for the Phase 2 endpoint: `DedupRow`, `DedupRequest`, `DedupResultRow`, `DedupSummary`, `DedupResponse`. `cluster_id` is a nullable integer; `routing` is a `Literal["cluster", "unique", "manual_review"]`.

### `dedup/signatures.py` — STEP A (Signature Collapsing)

Conservative normalization (`normalize_key`: lowercase, trim, collapse whitespace, strip punctuation, fold accents), block-id derivation (`derive_block_id`, a SHA-1 of the normalized `country|postal_code|street|house_no`), row grouping, and `build_signatures` which collapses rows into distinct `(norm_name1, norm_name2)` signatures with stable `s1, s2, …` ids. The normalized key is internal only and never reaches the LLM.

`derive_block_id` has a consumer outside Phase 2 too: [Stage 6 — batch consensus](#stage-6-batch-consensus) groups a finalised batch by it. **Using the same block derivation in both phases is deliberate.** Phase 1's consensus pass and Phase 2's adjudicator must not be able to disagree about what "the same address" means — if they did, a group Phase 1 converged could land in two Phase 2 blocks, or vice versa. There is one address key in this codebase and this is it.

`normalize_key` has a second consumer outside Phase 2: [`utils/cache.py`](#utilscachepy--cache-keys--batch-cache) builds the ROR / LEI / SERP cache keys with it, for the same reason it exists here — it collapses spelling variants of one entity without stripping legal forms or expanding abbreviations, which is the right conservatism for a cache key too. It is reused rather than reimplemented so the two never drift apart. Note what that conservatism means in practice: `Universität Stuttgart`, `University of Stuttgart` and `Uni Stuttgart` remain **three** distinct keys — the accent folds, the synonym does not.

### `dedup/prompts.py` — Dedup Prompts

The shared system prompt (entity-resolution adjudicator with the two-level identity model), the Mode A partition prompt builder, the Mode B assignment prompt builder, and `PROMPT_VERSION = "p2-dedup-v3"`.

### `dedup/llm.py` — Dedup LLM Client

`DedupLLM` **reuses** `llm/openai_client.py::get_openai_client` (it does not write a new client) but calls with the dedup deployment, a newer API version, `reasoning_effort=low`, JSON response format, and bounded exponential-backoff retries. Never raises — returns a `DedupLLMResult` carrying raw text, token counts, latency, model version, and an `error` field. Includes `parse_json_object` (defensive JSON parsing of plain / fenced / embedded objects) and a `reasoning_effort`-rejection fallback.

### `dedup/adjudicator.py` — Block Algorithm

The core of Phase 2. `cluster_blocks` is the request entry point; `_process_block` runs STEP A → B → C per block; `_mode_a`/`_mode_b` implement the two grouping strategies; `_adjudicate_residue` runs the [residue candidate pass](#residue-candidate-nomination) (nominate + pairwise-adjudicate the pairs bucketing skipped); `_enforce_name2_split` is the deterministic Name 2 safety net; `_emit_rows` assigns clusters and routing. Cluster ids are the content hash from `cluster_key.py`; residue-candidate telemetry (`candidates_generated`, `candidates_by_rule`, `candidate_cap_exceeded`) is logged here.

### `dedup/candidates.py` — Residue Candidate Nomination

Deterministic, pure (no LLM/network) nomination of the residue pairs Mode A/B never compared — an empty-Name 2 signature vs a populated one, or a signature alone in its bucket. `generate_candidate_pairs` nominates a pair when there is a same-entity signal: converging ROR/LEI (`id`), suffix-stripped Jaro-Winkler name similarity (`name`), or token-set Jaccard overlap (`token`), ordered by that priority. `strip_legal_suffix` removes trailing legal-form tokens (AG/GmbH/Inc/…) for similarity only — never from the canonical signature. Nomination is candidacy only; it never merges (the LLM verdict + identity rule decide).

### `dedup/cluster_key.py` — Stable Cluster Id

A tiny, dependency-free module (so `dedup.scoring` can import it without the LLM stack). `cluster_hash` returns `c_` + first 12 hex of sha256 over the sorted member `row_id`s — the same membership yields the same id across runs, machines, and input orderings; the scorer re-derives it to detect a *partial* cluster (members split across score calls).

### `dedup/scoring.py` — Golden-Record Election (Pass 3)

Pure-arithmetic scoring + election over an editable weights table (`weights.json`), separate from the LLM adjudicator so it can be re-run on retuned weights without paying for adjudication again. `elect_golden_records` scores every row (`score_row`) and elects one winner per cluster; `_award_count` implements **G1 (Bernd's year-priority rule)** — a sales-order count only "adds something" when the row owns its cluster's most-recent year. Demotes a cluster to `manual_review` when clustering already flagged it, every member is blocked, the merge confidence is below `CONFIDENCE_MERGE_THRESHOLD`, or the whole cluster scored 0. `detect_issues` derives the potential-inconsistency list; `apply_approval` promotes a proposed winner into the golden fields on human sign-off; `weights_version` fingerprints the weights for drift detection. Permissive throughout — a missing/dirty value scores 0 and never fails the batch; the one hard error is a duplicated `row_id` (broken upstream join).

### `dedup/scoring_xlsx.py` — Scoring Workbook I/O

`score_workbook` fills the empty `score_*` / election columns of an uploaded scoring workbook **in place** with openpyxl (never round-trips through pandas, so the `Weights` sheet and every original column survive). Locates all columns by header name; reads an optional `Weights` sheet as an all-or-nothing weights override; rebuilds a second `Issues` sheet each run. A `manual_review` row is written with `is_golden_record`/`golden_record_id` blank (winner kept in `proposed_golden_id`).

### `eval/dedup_eval.py` — Evaluation Harness

Offline, no-LLM evaluation of a *scored* workbook that still carries its `expected_cluster`/`expected_routing` fixture columns. `python -m eval.dedup_eval <scored.xlsx>` prints pairwise precision/recall/F1, the three named business-risk counts (`wrongful_block_candidates`, `competing_goldens`, `uncertainty_upgrades`) with offending `row_id`s, and election/tie-break counts, then writes `eval_report.json`. Flags a workbook that mixes multiple weights versions (score drift).

### `llm/openai_client.py` — OpenAI Client

Async wrapper around `AsyncAzureOpenAI`. Enforces JSON response format, strips code fences, retries once on parse failure (Phase 1 path). `get_openai_client(api_version=None)` was **parameterized**: callers may pass an `api_version`, falling back to the `AZURE_OPENAI_API_VERSION` env var and then `DEFAULT_AZURE_OPENAI_API_VERSION` (`2024-08-01-preview`, unchanged for Phase 1). The Phase 2 adjudicator passes a newer version that supports `reasoning_effort` and GPT-5.x. Phase 1 callers pass nothing and keep the historical version and behaviour.

TLS verification is resolved by `resolve_tls_verify()`: it honors a corporate CA bundle (`AZURE_OPENAI_CA_BUNDLE` / `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`) so a TLS-inspecting VPN doesn't break LLM calls, supports `LLM_SSL_VERIFY=false` as an insecure last resort, and otherwise uses certifi. The httpx client keeps `trust_env=True` so the VPN's `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` are honored; connect/read timeouts are configurable (`LLM_HTTP_CONNECT_TIMEOUT`, `LLM_HTTP_TIMEOUT`). This applies to both phases since the dedup client reuses this factory.

### `llm/prompts.py` — Prompt Templates

All LLM prompt templates as Python constants. Includes system prompts and user prompt templates for: overflow check, Tier 2A affiliation extraction, Tier 2B department extraction, Tier 2 canonical normalization, company canonicalization, Tier 3 inference, and plain-name classification.

### `search/base.py` — Search Interface

Abstract `SearchClient` base class and `SearchResult` dataclass. Defines the contract that both SerpAPI and DuckDuckGo clients implement.

### `search/serpapi_client.py` — SerpAPI Client

Google Search via SerpAPI. Runs synchronously in a thread executor (async wrapper). Requires `SERPAPI_KEY`.

### `search/duckduckgo_client.py` — DuckDuckGo Client

Free search fallback using the `duckduckgo-search` library. No API key required. Used when `SERPAPI_KEY` is not configured.

### `search/page_fetcher.py` — Page Fetcher

HTTP page fetcher with BeautifulSoup parsing. Extracts structured elements: URL path, page title, H1, breadcrumb navigation, and truncated body text. Detects breadcrumbs via `aria-label`, `role="navigation"`, and class patterns. `resolve_final_url()` follows a URL's redirect chain once (HEAD, GET fallback) so the department probe can key off the live host (`dur.ac.uk` → `durham.ac.uk`). User-Agent: "BrukerMDM-Enrichment/1.0".

### `utils/text_utils.py` — Text Utilities

- `country_to_iso_code()`: Maps country names/codes to ISO alpha-2 (60+ countries)
- `expand_abbreviations()`: "Dept" -> "Department", "Univ" -> "University", "Grp" -> "Group", "Svcs" -> "Services", etc. This is the **global** map — the one map that reaches output name fields (see [Finalization](#finalization)). The ROR-local acronym / state maps in `tier1_ror.py` are deliberately separate and never merged into it
- `canonicalise_unit_name()`: Normalizes to "Department/Division/School/Faculty of X" form
- `is_granular_unit()`: Detects lab/group/centre/facility units for scope filtering
- `looks_like_research_institution()`: Keyword-based fallback classification
- `extract_domain()`: URL -> registrable domain (handles two-part TLDs). It already strips the scheme/path/query/fragment and collapses subdomains — `utils/domain_resolver.py` wraps it rather than reimplementing it, adding only bare-host tolerance and lowercasing
- `score_search_result()`: Heuristic scoring for people/faculty page detection
- `name_initials()` / `acronym_matches_name()`: initials of a name; whether an acronym matches them (ROR acronym currency check + subdomain-acronym search term)
- `seg_matches_needle()`: host/subdomain-vs-token match (substring or shared ≥3-char leading prefix) — shared by the department probe and the search-term rules
- `is_admin_unit()`: detects administrative / back-office desks (accounts payable, finance, billing, procurement, treasury, …) — drives `search_term_2 = "ADMIN"` and department-probe suppression
- `clean_passthrough_org_name()`: title-cases ALL-CAPS + expands abbreviations for names that pass through un-canonicalised. It is no longer the only route by which `expand_abbreviations` reaches an output name — [Finalization](#finalization) expands Name 1–5 on every non-registry path
- `smart_title_case()`: ALL-CAPS → title case while preserving acronyms (`MRI`, `NIST`, `UCSF`), `Mc` surnames, and hyphen segments. A **whole-string** rule — it refuses any value that is not entirely upper-case, which is why a half-corrected value like "500 TECH Dr MS-4" kept its uppercase "TECH". Still used for Name 1 inside `clean_passthrough_org_name`; `normalise_case()` is what finishes the job
- `normalise_case()`: the token-level caser behind [Finalization Rule 7](#rule-7--output-casing-normalisation). Each token is judged on its own — digit-bearing and already-mixed-case tokens untouched, everything else title-cased against the legal-form / acronym / directional / Roman-numeral tables — with explicit `Mc`, hyphen, ampersand and apostrophe handling (`WOMEN'S` → `Women's`, never `str.title()`'s `Women'S`). `mode="name"` defaults a short upper-case token to an acronym; `mode="text"` defaults it to a word. Changes letter case and nothing else, and checks that invariant before returning

### `utils/domain_resolver.py` — Domain Write Path & Ownership Guard

The single point where `domain` and `website_url` are decided; no other module writes either field. `canonicalise_domain()` reduces a candidate URL to the registrable domain (reusing `extract_domain()`), `canonicalise_host()` does the same for a department domain but keeps the subdomain, and `resolve_domain()` applies the four ownership conditions — registry provenance, name similarity, email domain, on-domain search evidence — returning a `DomainDecision` the caller writes or a rejection it flags `domain-unverified`. Also exposes `email_domain()` / `is_generic_email_domain()` (consumer-provider blocklist) and `name_similarity()`. See [Website, Domain, Department-Domain & Search-Term Resolution §2](#2--domain--the-single-write-path-utilsdomain_resolverpy).

### `utils/cache.py` — Cache Keys & Batch Cache

Home of the **cache-key builders** (`lookup_key`, `serp_key`) and of `BatchCache` / `SerpCache`.

`BatchCache` holds the SERP and resolved-host namespaces (`get/set_resolved_host` caches the department probe's redirect-resolved base so it costs one request per institution). It is created fresh for each `/enrich` request; an optional process-level `SerpCache` lets overlapping SERP queries reuse results across batches. There is **no ROR namespace here** — `get_ror`/`set_ror` existed but had no callers anywhere in the codebase, and were removed rather than left implying a layer that never ran. ROR and LEI lookups consult the module-level `_ror_cache` / `_lei_cache` in `enrichment/tier1_ror.py` and `enrichment/tier1_lei.py`.

**Keys.** Every namespace keys on `normalize_key(query)` (reused from [`dedup/signatures.py`](#dedupsignaturespy--step-a-signature-collapsing): lowercase, trim, collapse whitespace, strip punctuation, fold accents) **plus the country**. Lowercasing alone collapses `MIT` / `mit` but not `Coastal Diagnostics, Inc.` against `Coastal Diagnostics Inc`, so one organisation was looked up under several keys inside a single batch, got several outcomes, and the batch emitted contradictory records for it. Country is in the key because a name-only key lets two genuinely distinct organisations that share a name in different countries share an entry. (It does **not** separate a same-country name collision — two US "Cardinal Instruments" still collide.)

> ⚠️ Three conditions hold for every key, and the first is what makes the punctuation stripping safe:
> 1. The normalised key is used **only** as a dictionary key for cache lookup.
> 2. The value **sent** to ROR / GLEIF / SERP is always the original, unnormalised string. The key decides *whether* a call is made; it is never the payload. Pinned by `tests/test_cache_normalisation.py::TestUnnormalisedQueryReachesTheAPI`.
> 3. The key never reaches output, never reaches an LLM prompt, and never enters `_compute_name_score()` or any other scoring path.

`serp_key` carries one extra component: **whether the query was quoted**. `normalize_key` strips quote characters, which would make an exact-phrase query and its unquoted retry ([website resolution §8](#website-domain-department-domain--search-term-resolution)) collide — the retry would be served the very results it exists to get away from. Quoting changes what is searched, so it is part of the query's identity rather than noise to fold away.

`legacy_lookup_key` / `legacy_serp_key` reproduce the old lowercase-only key. They are never used to store or retrieve a value — only to count `cache_hits_after_normalisation`, the lookups the normalised key served that the old key would have missed.

---

## External Services and APIs

| Service | Purpose | Authentication | Fallback |
|---------|---------|---------------|----------|
| **ROR API v2** (`api.ror.org`) | Organization/institution lookup | None (free, public) | No fallback — if ROR misses, escalate to Tier 2/3 |
| **GLEIF API v1** (`api.gleif.org`) | Company legal-name + LEI lookup (Tier 1, company branch) | None (free, public) | No fallback — if GLEIF misses/errors, escalate to LLM company canonicalization. A GLEIF failure never fails the record |
| **Azure OpenAI / AI Foundry** | All Phase 1 LLM calls + Phase 2 dedup adjudication | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` | No fallback — Phase 1 records fall to `unresolved`; Phase 2 signatures fall to `manual_review` |
| **SerpAPI** (`serpapi.com`) | Google Search results for Tier 2A/2B | `SERPAPI_KEY` | DuckDuckGo |
| **DuckDuckGo** | Free web search | None | N/A (is itself the fallback) |
| **Institution websites** | HTML pages for structured extraction | None | Tier 3 fallback if page fetch fails |

---

## Configuration and Environment Variables

Copy `.env.example` to `.env` and configure:

### Required

`config.py` validates these at startup (it warns rather than crashes, so health checks still work, but all LLM calls fail until they are set):

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI / AI Foundry key |
| `AZURE_OPENAI_ENDPOINT` | e.g., `https://your-resource.openai.azure.com/` |

### Optional (with defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.4` | Phase 1 model deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-08-01-preview` | REST API version for Phase 1 (and default fallback for Phase 2) |
| `ROR_API_BASE` | `https://api.ror.org/v2/organizations` | ROR API endpoint |
| `ROR_CONFIDENCE_THRESHOLD` | `0.8` | Minimum score to accept a ROR match |
| `LEI_LOOKUP_ENABLED` | `true` | Enable the Tier 1 GLEIF/LEI company lookup. `false` → company branch goes straight to the LLM (pre-LEI behaviour) |
| `GLEIF_API_BASE` | `https://api.gleif.org/api/v1` | GLEIF API base URL |
| `GLEIF_TIMEOUT_SECONDS` | `15` | HTTP timeout for GLEIF calls |
| `LEI_NAME_MATCH_THRESHOLD` | `88` | RapidFuzz `token_sort_ratio` (0-100) a candidate `legalName` must reach to be accepted |
| `LEI_MAX_RETRIES` | `2` | Max retries (exponential backoff) on transient GLEIF errors |
| `FUZZY_MATCH_THRESHOLD` | `80` | RapidFuzz threshold for name matching |
| `MAX_PAGE_CONTENT_CHARS` | `3000` | Maximum body text extracted per page |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `10` | HTTP timeout for page fetching |
| `DEFAULT_MAX_CONCURRENCY` | `5` | Default concurrent record processing limit |
| `CONFIDENCE_MERGE_THRESHOLD` | `0.95` | Election: a merge below this adjudication confidence is demoted to `manual_review` (pure data retune, no LLM re-run) |
| `NAME_CANDIDATE_THRESHOLD` | `0.85` | Residue nomination: suffix-stripped Jaro-Winkler name similarity to nominate a pair for LLM adjudication |
| `TOKEN_CANDIDATE_THRESHOLD` | `0.6` | Residue nomination: token-set Jaccard overlap to nominate a pair |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | Residue nomination: over this many candidate pairs, the block routes to `manual_review` |
| `DOMAIN_NAME_MATCH_THRESHOLD` | `82` | Domain ownership guard: rapidfuzz `token_sort_ratio` Name 1 must reach against the candidate's domain label before a web-derived domain is attributed to the organisation. Tuned on the demo batch — the highest wrong-owner pair scores 81.8, the lowest right-owner pair 82.4 |
| `DOMAIN_OWNERSHIP_GUARD_ENABLED` | `true` | Domain ownership guard on/off (A/B switch). When off, candidates are still canonicalised to the registrable domain; only the ownership conditions are skipped |
| `SERPAPI_KEY` | *(none)* | SerpAPI key; if absent, DuckDuckGo is used |
| `DEPT_PROBE_CROSS_DOMAIN` | `false` | Department probe stage 3 (unrestricted cross-domain SERP). Off by default — one SERP call per unresolved department. Enable for split-domain academic medical centres (e.g. `hopkinsmedicine.org`) at the cost of a second SERP call and off-domain-result risk |
| `WEBSITE_TRACE` | `false` | Diagnostic-only: emit a per-candidate JSON trace of Path B / Path C website resolution on the `enrichment.trace.website` logger. No behaviour change |
| `MOCK_EXTERNAL_CALLS` | `false` | Use mock clients (no real API calls) |
| `ENV` | `production` | Set to `local` for development (enables dotenv loading) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `LOG_FILE` | `logs/enrichment_api.log` | Log file path. Records go to **both** the console and this rotating file (~10 MB, 5 backups); uvicorn access/error lines are captured too. Set `LOG_FILE=` (empty) for console-only |

### Azure OpenAI / AI Foundry (Phase 1)

Azure OpenAI is the **only** LLM backend, in every environment — there is no direct-OpenAI / "local" path (a legacy `OPENAI_API_KEY` / `OPENAI_MODEL=gpt-4o` pair was dead config and has been removed). The same Azure credentials are used locally and in production — the only difference is delivery: a local `.env` file versus **Azure Application Settings** in the deployed Function App. The client is `AsyncAzureOpenAI`, constructed in `llm/openai_client.py::get_openai_client`; its TLS `verify` is resolved by `resolve_tls_verify()` (corporate CA bundle → certifi), the same helper now used by the ROR and GLEIF clients. See [TLS and Corporate VPN](#tls-and-corporate-vpn).

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_API_KEY` | *(none, required)* | Azure OpenAI / AI Foundry key |
| `AZURE_OPENAI_ENDPOINT` | *(none, required)* | e.g., `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.4` | Phase 1 model deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-08-01-preview` | REST API version used by Phase 1 (and the default fallback for Phase 2) |

### Tier 1 — GLEIF / LEI (company registry)

The company counterpart to ROR. For company-type records, resolves the official legal name + Legal Entity Identifier from the free GLEIF API before the LLM fallback. See [Stage 2 (Company): Tier 1 — GLEIF / LEI Registry Lookup](#stage-2-company-tier-1--gleif--lei-registry-lookup).

| Variable | Default | Description |
|----------|---------|-------------|
| `LEI_LOOKUP_ENABLED` | `true` | Master switch. `false` reverts the company branch to LLM-only |
| `GLEIF_API_BASE` | `https://api.gleif.org/api/v1` | GLEIF JSON:API base URL |
| `GLEIF_TIMEOUT_SECONDS` | `15` | Per-call HTTP timeout |
| `LEI_NAME_MATCH_THRESHOLD` | `88` | `token_sort_ratio` (0-100) verification threshold; below it a candidate is rejected (no fabrication) |
| `LEI_MAX_RETRIES` | `2` | Retries on transient (5xx/network) GLEIF errors, exponential backoff |

### Phase 2 — Dedup Adjudicator (`POST /api/dedup/cluster-block`)

| Variable | Default | Description |
|----------|---------|-------------|
| `AOAI_DEPLOYMENT_DEDUP` | `gpt-5.4` | AI Foundry deployment for the full GPT-5.4 adjudicator model |
| `AOAI_API_VERSION_DEDUP` | `2025-04-01-preview` | REST API version for the adjudicator. GPT-5.x reasoning models and `reasoning_effort` need a newer version than the Phase 1 default. Override to match what your resource exposes |
| `DEDUP_REASONING_EFFORT` | `low` | Reasoning effort for adjudication calls. While set, `temperature` is not sent — a reasoning deployment rejects any temperature but its default. Set it empty to fall back to a plain sampled call with `temperature=0.0` |
| `SIG_PARTITION_THRESHOLD` | `12` | Distinct-signature count at/below which a block uses one partition call (Mode A); above it, incremental canonical assignment (Mode B) |
| `DEDUP_MAX_CONCURRENCY` | `5` | Max in-flight adjudicator LLM calls across all blocks in a request |
| `DEDUP_MAX_RETRIES` | `3` | Max attempts per adjudicator call (retries 429/5xx with exponential backoff) |
| `NAME_CANDIDATE_THRESHOLD` | `0.85` | Residue pass: suffix-stripped Jaro-Winkler name similarity to nominate a pair |
| `TOKEN_CANDIDATE_THRESHOLD` | `0.6` | Residue pass: token-set Jaccard overlap to nominate a pair |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | Residue pass: candidate-pair cap per block; over it the block → `manual_review` |
| `CONFIDENCE_MERGE_THRESHOLD` | `0.95` | Election (`/api/dedup/score`): demote a below-threshold merge to `manual_review`; never re-runs the LLM |

> The adjudicator resolves its API version as `AOAI_API_VERSION_DEDUP` → `AZURE_OPENAI_API_VERSION` → `2025-04-01-preview`. If everything routes to `manual_review` with `errors > 0`, the API version or deployment name is almost always the cause — confirm with `GET /diag/dedup-llm`.

### TLS and Corporate VPN

A corporate VPN that performs **TLS inspection** (SSL interception) presents its own root CA on outbound HTTPS. Verifying against certifi's public bundle then fails the handshake, so when the VPN is connected **every outbound HTTPS call hangs or errors**. To fix it, point the clients at the corporate root CA via the variables below.

This affects all outbound HTTPS, not just the LLM: the **OpenAI client (both phases), the ROR client, and the GLEIF/LEI client** all resolve their `verify` setting through the shared `resolve_tls_verify()` helper. (ROR and GLEIF previously hardcoded `verify=certifi.where()`, which is exactly why, on the VPN, `ror_id`/`lei_id`/`domain` came back empty and every record fell through to the LLM.)

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_CA_BUNDLE` | *(none)* | Path to a corporate root CA `.pem`. Checked first for TLS verification |
| `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` | *(none)* | Also honored as CA-bundle sources (in that order) if they point to an existing file |
| `LLM_SSL_VERIFY` | `true` | Set to `false` to disable certificate verification entirely. **Insecure** — last resort only, logged loudly |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | *(none)* | Standard proxy vars; honored automatically (`trust_env=True`) so the VPN's proxy is used |
| `LLM_HTTP_CONNECT_TIMEOUT` | `30` | Connect timeout (s) — raise if the tunnel adds handshake latency |
| `LLM_HTTP_TIMEOUT` | `60` | Read timeout (s) |

**How to get the CA `.pem`:** export your corporate root CA from the OS trust store (e.g. on Windows, `certmgr.msc` → Trusted Root Certification Authorities → export the corp CA as Base-64 `.cer`/`.pem`), then set `AZURE_OPENAI_CA_BUNDLE` to that file. Resolution order is: `LLM_SSL_VERIFY=false` (disable) → first existing file among `AZURE_OPENAI_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE` → certifi. A bogus/placeholder `SSL_CERT_FILE` that doesn't exist is ignored (it falls back to certifi), so a stale corp path can't break startup.

---

## Setup and Installation

### Prerequisites

- Python 3.11 or higher (the project is developed and tested on 3.13)
- Azure OpenAI / AI Foundry credentials (`AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`) — or run in `MOCK_EXTERNAL_CALLS=true` mode with none
- (Optional) A SerpAPI key for Google Search; without it, DuckDuckGo is used
- For Phase 2 (dedup), a GPT-5.4 deployment (`AOAI_DEPLOYMENT_DEDUP`) on an API version that supports `reasoning_effort` (`AOAI_API_VERSION_DEDUP`, default `2025-04-01-preview`)

### Installation

```bash
cd enrichment_api

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# For development/testing
pip install -r requirements-dev.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

---

## Running Locally

### With API Keys

```bash
ENV=local python main.py
# or
ENV=local uvicorn api.app:app --reload --port 8000
```

The server starts at `http://localhost:8000`. The `--reload` flag enables hot-reloading during development.

### Mock Mode (No API Keys Needed)

```bash
ENV=local MOCK_EXTERNAL_CALLS=true python main.py
```

Mock mode replaces all external clients (ROR, OpenAI, SerpAPI, page fetcher) with deterministic mock implementations. Useful for:
- Running tests without API keys
- Frontend development against stable responses
- CI/CD pipelines

---

## Testing

### Unit Tests (pytest)

```bash
# Run all tests
pytest tests/ -v

# With coverage report
pytest tests/ --cov=enrichment --cov-report=term-missing

# Single test file
pytest tests/test_orchestrator.py -v

# Single test
pytest tests/test_tier1.py::test_acronym_resolution -v

# Phase 2 dedup adjudicator only
pytest tests/test_dedup.py -v

# Phase 2 scoring / election, residue nomination, and eval harness
pytest tests/test_scoring.py tests/test_candidates.py tests/test_dedup_eval.py -v

# Stage 6 batch consensus (grouping boundaries, propagation, invariants)
pytest tests/test_batch_consensus.py -v
```

### Phase 2 Dedup Test Coverage

`tests/test_dedup.py` drives the adjudicator directly with a **scripted fake LLM** (so the deterministic logic and the LLM-merge paths are exercised precisely), plus route-level tests in mock mode. Covered scenarios:

- **STEP A collapse:** 100 identical rows → 1 signature → 1 cluster of 100, routing `cluster`, **no LLM call**.
- **Two-level identity:** same Name 1, different Name 2 (Chemistry vs Mechanical Eng, same ROR) → two entities, not merged.
- **Cross-language / abbreviation merge:** "Dept of Mechanical Eng" vs "Department of Mechanical Engineering" → one cluster, `llm_flag = true`.
- **Name 2 asymmetry:** empty vs populated → never merged (deterministic, no LLM), even when the LLM is mocked to say merge; plus the `_enforce_name2_split` safety net.
- **Singleton:** → `cluster_id = null`, routing `unique`.
- **Uncertain signature:** → routing `manual_review`, while its identical rows still cluster among themselves.
- **Mode switch:** `N` just above the threshold triggers canonical assignment (Mode B) and still produces correct N-way clusters; Mode B respects the Name 2 boundary without an LLM call.
- **Response parser:** valid JSON, fenced JSON, embedded JSON, malformed → uncertain; LLM error result → uncertain (block still completes).
- **`reasoning_effort` fallback:** a deployment that rejects the parameter does not sink the call — it is dropped and the retry succeeds.
- **Block derivation & multi-block independence**, and **route wiring** (200, summary shape, 422 on empty `rows`).

### Local Integration Tests

```bash
# Mock mode (no API keys)
python scripts/test_local.py --mock

# Live mode (real APIs — requires keys)
python scripts/test_local.py --live

# Single fixture file
python scripts/test_local.py --fixture acronym_name1.json
```

Integration tests use JSON fixture files from `tests/fixtures/` that represent real-world SAP record scenarios.

---

## Azure Function Deployment

The application deploys as an **Azure Function v2** using ASGI integration. The `function_app.py` entry point wraps the same FastAPI app that runs locally, exposing every route through a single catch-all function:

```python
import azure.functions as func
from azure.functions import AsgiMiddleware
from api.app import app as fastapi_app

azure_app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@azure_app.route(route="{*route}")
async def http_app_func(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    return await AsgiMiddleware(fastapi_app).handle_async(req, context)
```

Because a single catch-all function fronts the whole FastAPI app, **all endpoints — including `POST /api/dedup/cluster-block` — share the same auth level** (`ANONYMOUS` here; switch to `FUNCTION` and supply a function key to require one). There is no per-route auth in application code. The HTTP route prefix is set to `""` in `host.json`, so routes are served at their FastAPI paths (`/enrich`, `/api/dedup/cluster-block`, …) with no extra `/api` segment injected by the host.

**Deployment flow:**
```
Azure Function Runtime
  └── function_app.py (catch-all route → AsgiMiddleware)
       └── api/app.py (shared FastAPI instance)
            ├── enrichment/orchestrator.py (Phase 1 pipeline)
            └── dedup/adjudicator.py (Phase 2 dedup)
```

Environment variables are configured via **Azure Application Settings** (not `.env` files). Application Insights (`mdm-pipeline-insights`) is enabled in `host.json` and captures the structured logs emitted by both phases.

---

## ADF Integration and DATAshaper Mapping

In production, the enrichment API is called by Azure Data Factory (ADF) as part of the DATAshaper MDM pipeline:

```
SAP Source  →  DATAshaper Stored Procedure (extract batch)
                         ↓
            ADF Web Activity: POST /enrich            (Phase 1)
                         ↓
            Enrichment API processes batch
                         ↓
            ADF receives JSON response
                         ↓
            DATAshaper Stored Procedure (write back enriched values + issues)
                         ↓
            DATAshaper address gates (group by country + postal + street)
                         ↓
            ADF Web Activity: POST /api/dedup/cluster-block   (Phase 2)
                         ↓
            Dedup adjudicator returns cluster assignments
                         ↓
            DATAshaper writes back cluster_id + routing per row
```

Phase 2 runs **after** Phase 1 and the address gates: enrichment first canonicalizes each record's names, DATAshaper then groups records by shared address, and the dedup adjudicator decides which of the same-address records are true duplicates. The orchestrator handles the file ↔ JSON conversion on both sides; the dedup endpoint is JSON in / JSON out.

### Status-to-Severity Mapping

The `enrichment_status` field maps to DATAshaper issue severities:

| enrichment_status | DATAshaper Issue Severity | Action |
|---|---|---|
| `enriched` | No issue (auto-applied) | Values written directly to master data |
| `verified` | Info issue (confirmed correct) | Values confirmed, logged for audit |
| `unresolved` | Warning issue (manual review) | Flagged for data steward review |
| `failed` | Error issue (process failed) | Requires investigation |

**Issue-code severity** is a separate axis, carried per catalogue code rather than per record. Catalogue v2's `Mandatory` attribute maps onto the same severities and is exposed as `IssueDefinition.severity`, so it is derivable rather than restated:

| Catalogue `Mandatory` | `IssueDefinition.severity` | DATAshaper effect |
|---|---|---|
| Yes | `Error` | Blocks the SAP load |
| No | `Warning` | Loads, flagged for review |

Nine of the 35 emittable codes are mandatory: `G2-VAL-001`, `G2-VAL-002`, `G2-VAL-003`, `G2-VAL-004`, `G2-VAL-006`, `G2-VAL-007`, `G2-VAL-008`, `G4-NAME-015`, `G4-ADDR-027`.

---

## Complete Data Flow Diagram

```
POST /enrich request (batch of records)
  │
  ▼
Route Handler → Orchestrator.enrich_batch()
  │
  ▼
For each record (async, concurrency-limited via Semaphore):
  │
  ├──► STAGE 0: UC 0 — Overflow Check
  │    ├─ LLM, per adjacent name pair: "Are these one continuous org name?"
  │    └─ If YES (medium/high confidence) → FLAG and RETURN (no further tiers)
  │
  ├──► STAGE 1: Preprocessing (deterministic regex, no network)
  │    ├─ UC 6: Accounts Payable normalization
  │    ├─ UC 7: Contact person extraction (Pattern A, B1, B2)
  │    │   └─ B2 only: optional LLM call to classify ambiguous plain names
  │    ├─ UC 8: Email copy (name + address fields, non-destructive)
  │    ├─ UC 9: Address extraction
  │    ├─ UC 10: Opaque code detection
  │    ├─ UC 11: DBA variant normalization
  │    └─ UC 12: Duplicate name field clearing (silent)
  │
  ├──► STAGE 2: Tier 1 — ROR API Lookup
  │    ├─ Try affiliation endpoint: ?affiliation={name1},{city},{state},{country}
  │    ├─ If no confident hit → fallback: ?query={name1} + country filter
  │    ├─ Score matches with distinctive-token guard
  │    ├─ If MATCH:
  │    │   ├─ Write official ROR name to name1_enriched (unconditional — no gate above the match threshold)
  │    │   ├─ Classify from ROR org types → research_institution | company
  │    │   ├─ If company → Tier 1 GLEIF/LEI lookup (overwrites name1, sets lei_id)
  │    │   ├─ Try child matching for Name2/Name3 (local, no 2nd API call)
  │    │   └─ If no Name2 signal and no contact → RETURN (Tier 1 final)
  │    └─ If MISS:
  │         ├─ If looks like research institution → passthrough, may escalate
  │         └─ If looks like company → Tier 1 GLEIF/LEI lookup (verified → name1 + lei_id);
  │              on miss/error → LLM company canonicalization (never fabricates)
  │
  ├──► STAGE 3: Tier 2 — Multi-Mode Canonicalization
  │    │
  │    ├─► UC 13: Lab → Parent Department Resolution (FIRST in Tier 2 path)
  │    │   (if: research institution + domain known + Name2 is granular)
  │    │   ├─ SERP "<lab>" site:<domain> (on-domain only)
  │    │   ├─ Fetch top-3 on-domain pages
  │    │   ├─ LLM extracts parent academic department
  │    │   ├─ On success: parent → Name2, lab → Name3 (when Name3 empty)
  │    │   └─ On failure: fall through to canonical / 2A / 2B / 3
  │    │
  │    ├─► Tier 2A: Contact Person Lookup
  │    │   (if: ROR hit + contact present + domain known)
  │    │   ├─ Build SERP query: "FirstName LastName" site:domain.edu
  │    │   ├─ Filter candidates by name verification (both names must appear)
  │    │   ├─ Fetch top-3 pages → extract URL/title/H1/breadcrumb/body
  │    │   ├─ LLM extracts: official_dept, official_group, title, confidence
  │    │   ├─ Canonicalize bare subjects: "Anesthesia" → "Dept of Anesthesia"
  │    │   ├─ UC 4 scope filter: reject granular units (labs, groups, facilities)
  │    │   ├─ Mode A (Name2 null): populate Name2 from discovered dept
  │    │   └─ Mode B (Name2 exists): verify/correct against extracted dept
  │    │
  │    ├─► Tier 2 Canonical (LLM-only, no web search)
  │    │   (if: Name2/3 present + ROR hit + no child match)
  │    │   ├─ Single LLM call: normalize dept name to official wording
  │    │   ├─ Only accept HIGH confidence results
  │    │   └─ UC 5 scope filter: reject granular units
  │    │
  │    └─► Tier 2B: Department Search
  │        (if: no contact, or Tier 2A failed, or company)
  │        ├─ Build SERP query: "Org Name" "Dept Name" site:domain
  │        ├─ Rank: on-domain > external authoritative > other
  │        ├─ Fetch top-3 pages → extract URL/title/H1/breadcrumb
  │        ├─ LLM extracts official_name from structured elements ONLY
  │        ├─ Deterministic best-selection (on_domain, fuzzy_match, length)
  │        └─ Confidence: medium (on-domain), low (external)
  │
  ├──► STAGE 4: Tier 3 — LLM Inference (last resort)
  │    ├─ Single LLM call with ALL available fields
  │    ├─ High/medium confidence: write suggestions, mark "unresolved"
  │    ├─ Low confidence: preserve originals, mark "unresolved"
  │    └─ ALWAYS flagged for human review
  │
  └──► FINALIZATION
       ├─ Empty string guard (must be None or non-empty)
       ├─ Canonicalize academic unit names (except granular)
       ├─ Passthrough originals if no tier enriched the field
       ├─ Compute changed flags (enriched ≠ original AND enriched not None)
       ├─ Rule 1: No Name2 signal → name2_enriched = None
       ├─ Rule 2: Preprocessing stripped Name2 → don't let Tier 3 fabricate
       ├─ Rule 3: name2_enriched == name1_enriched → drop Name2 (no echo)
       ├─ Derive search terms · classify record_type
       ├─ Rule 7: output casing — Name 1-5, Care Of, Contact, Street 1-5,
       │          City, PO Box (token level); Email lower-cased;
       │          registry names and code fields skipped
       └─ RETURN EnrichmentResult
  │
  ▼
Stage 6: batch consensus — group by address block + canonical name + legal form;
         one registry identity → propagate ror_id / lei_id / name1_enriched /
         domain / website_url / record_type from the registry donor;
         no registry identity → modal name form + unanimous-gap-fill only
         (source = "batch_consensus"; tier_used, flags and record count untouched)
  │
  ▼
Aggregate batch → EnrichmentSummary (counts by tier, status, type)
  │
  ▼
RETURN EnrichmentResponse (JSON)
```

---

## Changelog

### Per-field provenance and admissibility (newest)

The response carried one `tier_used` / `source` / `confidence` triple per record. Row 5 has Name 1 from ROR, Name 2 from a SERP → fetch → LLM chain and a department domain from the probe — one label collapses all three — and no record-level label can represent a field written twice, which is exactly what [Fix 2's retry](#stage-5-tier-1-re-lookup-after-canonicalisation) does to `name1`. Row 4's symptom, a verified ROR ID shipping next to an LLM-uncertainty flag, was that sequence going unrecorded. Full description in [Per-Field Provenance and Admissibility](#per-field-provenance-and-admissibility).

- **The six scoped fields cannot be assigned.** `_init_result` returns an `EnrichedRecord` on which `name1_enriched`, `name2_enriched`, `domain`, `record_type`, `ror_id` and `lei_id` are write-locked: `record["domain"] = x` raises, and the only way in is `record.write(field, value, evidence)` with a required structured `evidence`. `EnrichmentResult` carries the same lock past finalisation, because batch consensus writes onto finalised records. Recording provenance is easy to add and easy to bypass, so the enforcement is the point — the phase-1 scope is six fields because those are the ones whose wrong value causes a wrong merge in Phase 2, and because none of them is personal data.
- **One event per write, not per record.** `seq` is monotonic across the whole record so the interleaving is reconstructable; `producer_chain` names the tools that produced *one* value in sequence (`["serp","fetch","llm_tier2a"]`); a field written twice is two events. LLM writes record the deployment, the prompt version and the temperature — a value produced by a model deployment is not reproducible without them — and never the prompt text.
- **Confidence is no longer one number.** `ror_local`, `fuzzy_ratio` and `llm_self_reported` are not commensurable: 0.85 from each means three different things. Every event carries `confidence_scale` beside `confidence_value`, and derived bands are namespaced by scale (`self_high`, never a bare `high`). The record-level `confidence` is kept for backward compatibility and documented as a coarse projection, not a measurement. Two cross-scale comparison sites are reported and left unchanged: `tier2a_contact.py`'s `max(llm_score, our_score)` and the (dead) `determine_enrichment_status`.
- **Guard rejections are logged; candidate lists are not.** The five guards — ROR country, distinctive-token, identifier-token, Fix 1's domain ownership, GLEIF name verification — record what they refused, capped at 3 per field per record with the overflow counted rather than silently dropped. On the demo batch: 22 GLEIF name-verification, 18 distinctive-token, 13 domain-ownership, 3 identifier-token, 2 GLEIF country. (The distinctive-token count rose from 16 when `_DISTINCTIVE_TOKEN_MIN_LEN` [dropped from 5 to 4](#name-scoring-logic) — the two added refusals are Customer 40000015's `AUM BioTech` and `Best Biotech`.)
- **An unattributable value is not shipped.** At the end of `finalise` every non-null scoped field must have an event; one that does not is reverted to the input value and flagged `unattributed-value`. The record is never failed — the original input beats both a failed batch and an unattributable value.
- **Six new columns, 59 → 65.** `producer:tier:confidence_band` per scoped field, regenerated from the events and never maintained separately. The nested events array ships in the `/enrich` JSON only; `/enrich/file` emits the six columns. **Whether DATAshaper's column-typed validation model accepts 65 columns needs confirming externally before rollout.**
- **`flagged_fields` is now derived from the log.** "Is this value an unverified inference" is a question about who wrote it last, and the log is that record — so the `unverified-inference` scope follows from provenance rather than from a marker a tier remembered to set, and a field a registry overwrote is no longer the LLM's claim without needing a second check.
- **Not telemetry, not persisted, not a behaviour change.** The log is in the API response; App Insights stays operational monitoring; the API remains stateless and gains no database. On the demo batch all six scoped values are byte-identical to the pre-fix run — live and mocked — as are `flag_codes`, `flagged_fields`, `tier_used` and `source`. 217 events for 50 records, ~43,400 projected for 10,000. Tests `test_provenance.py`.

### Flag model redesign — triage signal, not an execution trace

47 of the 50 demo records were flagged, so the flag could not be used to decide what to look at. Four structural causes, all fixed here. Full description in [Flag Rules](#flag-rules).

- **The code contradicted the documented spec.** `enrichment/confidence.py` held a `should_flag_for_review` function that matched the README's Flag Rules table — and **nothing ever called it**. Every tier set `flag_for_review` inline instead, which is how 8 of 50 records shipped `"LLM canonical form — verify"` / `"LLM canonical company name — verify"` against a table that had always said *Tier 2 Canonical high confidence → No flag*. The dead function is gone and the documented rule is now enforced.
- **Rebuilt from final state, not appended as tiers run.** `compute_flags` is called once, from `finalise`. Tiers record evidence; finalisation decides what it means. A record rescued by [Fix 2's Tier 1 re-lookup](#stage-5-tier-1-re-lookup-after-canonicalisation) ends with a registry identifier and no earlier-tier reason — row 4 held `ror.org/0106fnq84` next to `"LLM low confidence — manual review required"`, and now carries no flag at all.
- **Field-scoped.** New `flagged_fields` column names the output fields a flag concerns, and the scope is repeated in the reason prose. Rows 14 / 36 / 38 hold verified ROR IDs and an uncertain department: they now scope to `name2` alone, so a reviewer can tell a one-field check from a full record review. 9 of the 21 flagged records are single-field.
- **New `flag_codes` column** — a machine-readable list, because a record can hold several conditions and the single concatenated `flag_reason` string did not scale. `flag_for_review` is true **iff** `flag_codes` is non-empty; `flag_reason` is prose only, and states what is uncertain and what to do rather than which tier ran.
- **Absence of data is not a defect.** "Research institution with no department and no contact" fired on 10 of 50 records — all resolved by ROR with verified identifiers, and none of them actionable. Removed, along with the blanket "Tier 2B results are always flagged" (a stated department read off an on-domain page has a `source_url` to audit) and the provenance-based "Website inferred by LLM — verify" (superseded by the evidence-based `domain-unverified`).
- **Tier 3 is flagged on what it wrote, not on the fact that it ran.** Anything Tier 3 writes carries `unverified-inference` regardless of its confidence — a confident unverifiable claim is the more dangerous case. Where it leaves a value unchanged the record reads `low-confidence-unchanged` or `no-match` instead, and a blank Name 2 that Finalization Rule 1 leaves blank carries nothing at all: nothing was dropped.
- **Measured on the demo batch** (live run) — flag rate **47/50 → 21/50**, and one of the 21 is the workbook's trailing phantom row. 13 distinct reason strings collapse to 6 codes in use: `low-confidence-unchanged` 15, `domain-unverified` 12, and one each of `person-unresolved`, `dept-via-lab`, `overflow`, `no-match`. Tests `test_flags.py`.

### Batch consensus — one identity per organisation per address

Rows that share an organisation *and* an address could still leave the batch with different identities: each record is enriched in isolation, so one resolved against a registry and another did not. Four groups in the demo batch — the Coastal Diagnostics trio, four Lockheed Martin rows, two MIT rows, three Stuttgart rows. [Canonical cache keys + the Tier 1 re-lookup](#stage-5-tier-1-re-lookup-after-canonicalisation) fix most of this at source; this is the safety net for the rest, and it is cheaper than having Phase 2 adjudicate the divergence later. Full detail in [Stage 6: Batch consensus](#stage-6-batch-consensus).

- **Field propagation, never a merge.** The batch out is the same length and the same order as the batch in. Phase 2 remains the only place entities are merged.
- **One address key in the codebase.** Grouping reuses `dedup.signatures.derive_block_id`, so Phase 1's consensus pass and Phase 2's adjudicator cannot disagree about what "the same address" means. Both halves of the grouping key are dictionary keys and nothing else — never output, never sent to an API, never in an LLM prompt, never in a scoring path.
- **Legal-form compatibility instead of legal-form stripping.** `normalize_key` leaves legal forms alone by design, so `Coastal Diagnostics` and `Coastal Diagnostics Inc` do not collapse. The base name and the legal form are kept separate and two rows group when the bases match and the forms are compatible (identical after canonicalisation, or absent on one side). Stripping the form would have grouped `Delta Analytical Inc` with `Delta Analytical LLC`, which is exactly Phase 2's judgement to make. Compatibility is not transitive, so a bare name between two competing forms is left on its own.
- **Organisation-level fields only.** `ror_id`, `lei_id`, `name1_enriched`, `domain`, `website_url`, `record_type` propagate. `name2_enriched`, `department_domain`, contact/email/care-of, `search_term_2` and every address field never do — rows 12–14 share Stanford's ROR id and domain and keep `chemistry.stanford.edu` / `chemistry.stanford.edu` / `physics.stanford.edu`.
- **Two tiers, deliberately unequal.** With one registry identity in the group, the registry-resolved donor's values win outright. With none, the pass never picks between competing values — it fills gaps only where the group is already unanimous, and the sole exception is the name form, whose variants are spellings of a name every member already holds (the modal one wins). That is what converges the Coastal trio, which no registry resolved. It smuggles nothing past the [ownership guard](#2b--ownership-guard-domain_ownership_guard_enabled-default-on): a filled-in domain already satisfied that guard on a record whose Name 1 equals the receiving record's after canonicalisation, so the guard would reach the same verdict.
- **Conflicts propagate nothing and are not flagged.** A group holding two or more conflicting registry identities is left entirely alone and recorded in telemetry; the flagging model is being redesigned separately.
- **New `source` value `"batch_consensus"`,** and `tier_used` is deliberately **kept** on an inheriting record — setting it to 1 would inflate the Tier 1 count and corrupt the tier-distribution figures used in evaluation. Flags are untouched. Propagated values are copied verbatim: they come from an already-finalised record and must not meet Fix 4's expansion or Fix 5's casing a second time.
- **Measured on the demo batch** (live run) — 7 groups, 7 records updated, 0 conflicts, `{ror_id: 4, lei_id: 1, domain: 2, website_url: 2, name1_enriched: 2}`. Rows 18/20/21 inherit Lockheed's `ror_id` from row 19; rows 5 and 24 exchange the ROR id and the LEI each was missing; rows 15/16 take the Coastal trio's modal name form and its one agreed domain. Stanford, Yale and both Stuttgart groups were already converged by Fixes 2 and 4. The tier distribution is identical either side of the pass. The legal-form rule created exactly one group `normalize_key` alone would not — the Coastal trio.
- **Telemetry** — `consensus_groups`, `consensus_records_updated`, `consensus_conflicts`, `consensus_fields_propagated` on the batch summary. Tests `test_batch_consensus.py`.

### One classification authority for `record_type` (newest)

`record_type` was written by whichever tier ran last, so MIT came out `company` because it holds an LEI, a hospital came out `company` because it took the company branch, and `unknown` — undocumented, and the modal value — sat on 21 of 50 demo records without anything having decided so.

- **`routing_type` / `record_type` split.** The field was never purely an output: it gates which tiers run, and the pipeline needs a type before the evidence that decides the final one exists, so "compute it once in `finalise`" is not implementable as stated. Tiers now write and read `routing_type` (provisional, internal, never serialised); `record_type` is decided once in `finalise`. **Which tiers run for a given record is unchanged** — every gating site moved to the field the tiers still write at exactly the same points.
- **Ranked evidence** — ROR org types → GLEIF entity metadata → keyword heuristic → `unknown`, first answer wins, ambiguity falls through instead of guessing. `enrichment/classifier.py`, revived from the tombstone left when keyword classification was removed.
- **The LEI guard** — an LEI hit on its own never sets `company`. MIT keeps its LEI *and* its `research_institution`.
- **GLEIF metadata, checked live rather than assumed** — `entity.category` is `GENERAL` for MIT and Pfizer alike and decides almost nothing; `subCategory` was `null` on every record sampled. `entity.legalForm.id` (ISO 20275) is the field that discriminates, via the generated table in `enrichment/elf_codes.py`, with `legalForm.other` covering the `8888`/`9999` catch-alls that MIT and Pfizer Canada both use.
- **Tier 3 contributes no classification evidence** — and never did in this codebase: it writes no `record_type` at all. The `company` values attributed to it came from the company-canonicalisation branch.
- **`unknown` documented as a real fourth state** — "no tier resolved the type with confidence", preferred over a `company` default that asserts what the pipeline does not know.
- **Telemetry** — `record_type_source` (`ror` | `gleif` | `keyword` | `unresolved`) per record, and `routing_type_mismatch_count` per batch for records that ran down the wrong branch. Those records are surfaced, not re-run. Tests `test_record_type_authority.py`.

### Canonical cache keys + Tier 1 re-lookup (newest)

Identical entities produced different output depending on how the input happened to be spelled — four "Lockheed Martin Corp" rows where only one carried the LEI, three Stuttgart rows where one had no ROR id, "Coastal Diagnostics" resolving to two domains and two record types. Two independent causes, both fixed here.

- **Cache keys collapse spelling variants.** ROR, LEI and SERP lookups key on `normalize_key(query)` + country (reused from `dedup/signatures.py`, not reimplemented) instead of a lowercased string, so `Coastal Diagnostics, Inc.` and `Coastal Diagnostics Inc` are one entry and one API call. The key is a dictionary key and nothing else: the **unnormalised** string is what reaches the API and every scoring path, pinned by a test. Country is in the key so two same-named orgs in different countries cannot share an entry. See [`utils/cache.py`](#utilscachepy--cache-keys--batch-cache).
- **`BatchCache.get_ror`/`set_ror` deleted** — the README described a ROR namespace there, but it had no callers in the whole codebase. ROR lookups have always consulted `tier1_ror._ror_cache`, which is where the normalisation had to go.
- **The SERP key keeps the quoting distinction.** `normalize_key` strips quote characters, which would have made an exact-phrase query and its unquoted retry (website resolution §8) collide — the retry would have been served the very results it exists to escape. `serp_key` therefore carries a "was quoted" component alongside the normalised text.
- **Tier 1 is re-run once after canonicalisation.** ROR runs before the pipeline knows the real name; when a later tier works it out, `orchestrator._retry_tier1_after_canonicalisation` looks that name up — ROR first, GLEIF on the company branch, one retry per record, every guard intact, nothing written on a miss. A retry hit also gives the record registry provenance, so a domain the [ownership guard](#2b--ownership-guard-domain_ownership_guard_enabled-default-on) had rejected comes back verified. See [Stage 5](#stage-5-tier-1-re-lookup-after-canonicalisation).
- **Known limit** — the retry only fires when a tier actually *writes* a changed `name1_enriched`. `canonical_preserves_identity` rejects a corrected typo (`MASSACHUSETTS INSITUTE OF TECHNOLOGY`) or an expanded abbreviation (`GA Tech`, `FL State Univ`), so those records still discard the right answer one gate earlier than this fix reaches.
- **`record_type` untouched** — a retry hit whose registry type contradicted the record logged `tier1_retry_type_conflict` and left the value alone. *(Superseded: `record_type` is now decided once in `finalise` from ranked evidence, with the retry's registry verdict ranked first. The log line remains, reporting the branch the record actually ran down.)*
- **Telemetry** — `tier1_retry_attempts`, `tier1_retry_hits_ror`, `tier1_retry_hits_lei`, `cache_hits_after_normalisation`. Tests `test_cache_normalisation.py`.

### Domain: one write path + an ownership guard (newest)

Full detail in [§2 · `domain` — the single write path](#2--domain--the-single-write-path-utilsdomain_resolverpy).

- **Single chokepoint** — `utils/domain_resolver.resolve_domain()` is now the only place `domain` / `website_url` are decided; every tier hands it a *candidate* URL and the evidence the record carries. `orchestrator._apply_domain` is the wrapper. Previously five call sites wrote the fields directly.
- **The "Domain" column now carries `domain`, not `website_url`** — the exported column shipped the raw URL, which is why every value in the demo export had a scheme and some had a deep path (`http://www.uni-stuttgart.de/home/index.en.html`) or a sub-site host (`https://investors.lockheedmartin.com`). The bare `domain` was already canonical; the export mapping was the format bug. `website_url` is now derived (`https://<domain>`) and internal-only. **Same column names, same column order** — only the value changes, so the DATAshaper / ADF schema is untouched.

- **Two new review columns** — `Flag Codes` and `Flagged Fields`, inserted between `Flag for Review` and `Flag Reason`. Both are lists in the JSON response and semicolon-joined strings in the XLSX. Existing columns keep their names, order and meaning, so a consumer that ignores the two new headers behaves exactly as before — and because the field scope is repeated inside `Flag Reason`, such a consumer still sees which field is in doubt.
- **Ownership guard (§2b)** — a candidate is attributed to the organisation only with registry provenance, name similarity at `DOMAIN_NAME_MATCH_THRESHOLD` (82), a non-generic email domain on the record, or an on-domain SERP title naming it. Otherwise `domain = null` + flag `domain-unverified`. This is the domain-path counterpart to ROR's country guard and GLEIF's name verification; without it `delta.com` was attached to "Delta Analytical" and `cardinalhealth.com` to "Cardinal Instruments", which reads as successful enrichment.
- **Email evidence is used, not discarded** — a record holding `ORDERS@MERIDIANLABS.COM` now yields `meridianlabs.com`, outranking the search result's `meridianlabs.ai`.
- **`department_domain` is host-canonicalised, never subdomain-collapsed** — path/query/fragment stripped (`medschool.umich.edu/departments/radiation-oncology` → `medschool.umich.edu`) while `chemistry.stanford.edu` / `be.mit.edu` keep their subdomains. Stage-2b path winners now emit the host rather than the full URL (§3).
- **Telemetry** — `domain_from_registry` / `domain_from_email` / `domain_from_serp` / `domain_rejected_unverified` on the batch summary; `DOMAIN_OWNERSHIP_GUARD_ENABLED=false` A/B disables the guard.
- **Cost** — a contraction domain (`fishersci.com` for "Fisher Scientific") proposed by Path C with no registry, email or search corroboration is now dropped instead of written. Tests `test_domain_resolver.py`.

### Search terms, website / domain / department-domain resolution

Full detail in [Website, Domain, Department-Domain & Search-Term Resolution](#website-domain-department-domain--search-term-resolution).

- **Both search terms are derived after enrichment, from enriched values only** — the record's own pre-enrichment SAP Search Term 1 is out of the ST1 chain (`_search_term_1_original` removed end to end), and ST2 no longer falls back to `name2_original`. A stale customer-maintained handle can no longer outlive the name it described. Tests `test_search_terms_fixes.py::TestDerivedAfterEnrichmentOnly`.
- **Search Term 1 rewrite** — chain is now ROR-acronym → `strip_tld(domain)` → a handle derived from the **enriched Name 1** (legal suffixes dropped; the whole name kept when it fits 32 chars — `University of Florida` → `UNIVERSITY OF FLORIDA`, `Applied Thin Films, Inc.` → `APPLIED THIN FILMS` — else stopwords dropped and filled to the boundary) → `None`. `derive_acronym` was **removed** from ST1 (it produced evidence-free initials `VI`/`SB`/`JFF`). The handle comes from `name1_enriched` **only** — never `name1_original` — so a record whose Name 1 output is null emits no search term (person rows, and the `ATTN CHARLES FARBER / MIT` case where no institution survived preprocessing). The `_name1_was_person` result carrier is gone with it: a blank enriched slot already says the same thing. Tests `test_search_terms_fixes.py`.
- **ROR acronym currency selection** — ROR may carry several `acronym` entries (current + historical); `_extract_org_fields` now selects the one whose letters are the initials of the *current* official name (`NIST` ✓ / `NBS` ✗), else none. `name_initials` / `acronym_matches_name` added to `text_utils`.
- **Search Term 2 rewrite** — chain is `ADMIN` (admin desk via `is_admin_unit`) → subdomain acronym (only when genuinely an acronym) → **Name 2 phrase, structural unit words stripped, filled to 32 chars** → department-domain host → `None`. `dept`/`div`/`school`/`institute`/`centre`/`lab`/`office`/`group`/`section`/`unit` and their variants are dropped **wherever they appear** in the phrase (`Chemistry Dept` → `CHEMISTRY`, `Chemistry Dept Analytical Div` → `CHEMISTRY ANALYTICAL`); a phrase of nothing but structural words ships no handle, and a generic host segment (`dept.example.edu`) is refused as one. This **inverts** the old precedence (Name 2 text now beats the department-domain host, which had produced `scrippscollege`/`leuphana`/`uwm`). Guards: a UC 11 **DBA** trade name and an **institution in the Name 2 slot** (probable field swap) are not used for a handle.
- **Terminal normalisation** — both search terms are trimmed, internal-whitespace-collapsed, uppercased, and truncated to **32 chars** on a word boundary (SAP SORT1/SORT2 width).
- **Website Path B guards (§7)** — a *distinctive* (non-generic) Name-1 token must appear in the **host** (or, for research institutions, the acronym — `fit.edu` ↔ "Florida Institute of Technology"); both branches now rank 0/1/2 and **reject rank 0** (title-only matches like `scup.org` for "Bayfront Research"); an authoritative TLD grants `high` **only** with a clean host match. Tests `test_website_resolver.py::TestPathBGuards`.
- **Website Path B retrieval (§8)** — `num_results` 5 → 10, plus one **unquoted retry** when the exact-phrase query finds nothing (recovers `Atlantic Testing Labs` → `atlantictesting.com`, `Fine Organics Limited` → `fineorganics.com`). Logged in `WEBSITE_TRACE`. Tests `TestPathBRetry`.
- **Department probe fixes (§5)** — admin rows skip the probe entirely (`is_admin_unit`, no fetch/SERP); the generic blocklist now applies to **path segments** (`news`/`events`/`archive`) and paths are ranked by canonicality (shallow landing page beats deep/dated/sub-page); verification accepts **morphological variants** (`physics.nist.gov` ↔ "Physical Measurement Laboratory") while still rejecting `science.mit.edu` for a Computer Science query; the base is **subdomain-aware** (`gc.cuny.edu`) and **redirect-resolved** (`dur.ac.uk` → `durham.ac.uk`, one HEAD per institution, cached per batch). Tests `test_dept_domain_probe.py`.
- **`DEPT_PROBE_CROSS_DOMAIN` default → `false`** (§6) — matches the documented intent; the unrestricted cross-domain stage-3 SERP is now opt-in.
- **`WEBSITE_TRACE` diagnostic flag** — off by default; when on, emits a read-only per-candidate JSON trace of Path B/C resolution on `enrichment.trace.website`. Driver: `scripts/trace_website.py`. Tests `TestWebsiteTraceFlag`.
- **Dead code removed** — `search_terms.derive_department_domain` (superseded by `_probe_department_url`) and its test.
- **A verified registry match writes the official name** — Tier 1 used to run ROR's official name through an identity guard and keep *your* Name 1 whenever the registry's form looked like it dropped a distinctive token. Against a registry name that mostly fired on abbreviations, so a record could hold `ror.org/03zzw1w08` and still read "Mayo Clinic FLA". The write is now unconditional on a verified match, on the first pass, on child matches, on GLEIF and on the [Tier 1 re-lookup](#stage-5-tier-1-re-lookup-after-canonicalisation) (which previously wrote no name at all). A registry name is never abbreviation-expanded afterwards; every other output name is. Tests `test_registry_name_authority.py`, `test_ror_name_verbatim.py`. See [Registry names are authoritative](#registry-names-are-authoritative).

### Name / address routing, person affiliation & scoring column contract

- **Person in Name 1 → Contact + affiliation lookup** — UC 7 now moves more person formats out of Name 1 to `contact`: ALL-CAPS names (case-insensitive, title-cased, `Mc`/hyphen preserved), title + credentials (`Dr. Jane Smith, PhD`), `Last, First` (reordered to `John Smith`), and `Name, credentials` / `Name MD` (surfaced to the LLM classifier via a normalised candidate). When the person was the whole of Name 1, Stage 2b (`enrichment/person_affiliation.py`) discovers the institution + department from the web and **confirms it against ROR in the record's country** before writing anything — Name 1/id/domain come from ROR, the department from a Tier 2A lookup on the confirmed domain, everything flagged `verify`. A wrong-country or unconfirmed proposal is rejected (Name 1 stays empty, flagged for manual lookup), and Tier 3 is always short-circuited so it can never fabricate or overwrite. See [Stage 2b](#stage-2b-person-affiliation-lookup). Tests `test_person_in_name1.py`, `test_person_affiliation.py`, `test_person_affiliation_guard.py`, `test_person_in_name1_flag.py`.
- **Organisation/department content in a street field → Name block** — a pipe-delimited org hierarchy (`Dept | Div | … | U.S. FDA | 5100 Paint Branch Pkwy`) or a comma-delimited mix of org + address (incl. **German streets** like `Scharnhorststraße 1`) is split: org/department segments go to the Name fields, the address stays in the street. The **institution** always takes Name 1; sub-units fill Name 2+; a bare location fragment ("Queens Campus") goes to the next street slot. Guarded so a plain address is never split. Overflow raises `name-slots-full` / `street-slots-full` → the `overflow` flag code (never a silent drop). "Accounts Payable" in a street field routes to Name 2. Tests `test_street_org_split.py`.
- **Street reduced to one line — full scope table** (items 3 & 4) — the late `process_address` stage now reduces a mixed street to a single main street line, routing every other segment to its own field: **Building** (named buildings too — `Aster House`, `Polaris House`, `The Sherard Bldg`, `Emerging Technologies Building`; a *second* building → next free street slot), **Floor** (bare ordinal `7th`), **Room** (`A104`, `Lab 576`), **Mail Stop** (`Campus Box 7212`), **Mail Code** (`3120 TAMU`), **Care Of** (per-segment, incl. the misspelled `Atnn:`), campus/science-park → next street slot, and city/region/postcode already in their own fields → dropped. The pipe splitter is no longer inverted: each pipe segment is classified individually, only org/dept routes to a Name (acronym-deduped), and the source street is cleaned (`|` dropped). A functional desk (`Finance/Procurement`) routes to a Name. Preprocessing owns the street values end-to-end (`_pp_streets`), so a slot it empties never reappears from the raw original. Tests `test_street_scope_table.py`, `test_street_scope_routing.py`, `test_pipe_splitter_inversion.py`, `test_person_org_in_street.py`.
- **Name 1 acronym/full-form dedupe** — when Name 1 carries both an acronym and its expansion (`MIT Massachusetts Institute of Technology`, `… (MIT)`, `MIT (…)`, and dash forms like `MRC - Medical Research Council`), only the verified full form is kept; unrelated tokens (`UC Berkeley`, `3M Company`, `AT&T`) are untouched. University acronyms (`UCSF`, `UCLA`, `SUNY`, …) keep their casing. Tests `test_acronym_dedupe.py`, `test_smart_title_case.py`.
- **Address sub-location fixes** ([address_processing.py](enrichment/address_processing.py)) — value-before-marker floors (`7th Floor`, `22nd Floor`) now populate `floor`; `Room number: F107` / `Room No. 3` now populate `room` (filler words skipped); a `c/o` / `Attn` capture stops at the start of a street address so `Att. Bayard Huck 200 Clarendon Street 22nd Floor` splits into contact + street + floor. Tests `test_address_cleanup.py`.
- **ROR US state-abbreviation expansion** — `Fla State Univ` was resolving to **Kent State University** (only the generic `State`/`University` tokens matched). A ROR-local `_US_STATE_ABBREVS` map now expands the state abbreviation for the ROR query only (`Fla` → `Florida`), so it resolves to Florida State. Kept out of the global `expand_abbreviations`. Extended with a **bounded** two-letter postal pattern (`FL State Univ`, `TX Tech`, `NJ Institute of Technology`) that fires only in four closed contexts — the general exclusion on bare `FL`/`IN`/`OR` still stands. Tests `test_ror_state_abbrev.py`, `test_registry_name_authority.py`. See [US State-Abbreviation Expansion](#us-state-abbreviation-expansion-ror-local).
- **`/api/dedup/score` ↔ `/api/dedup/score/file` column contract** — the JSON and XLSX scoring endpoints are now functionally identical and use the **exact same input/output column names** (`Customer`, `Sales_Order_Last_Used`, `score_final`, the `score_*` point columns, `sf1`…`sf8`, the derived `*_Count` columns, …). The JSON `/score` endpoint gained an optional `weights` override (same all-or-nothing semantics as the file's `Weights` sheet) and the derived-count outputs; Salesforce ids are eight flat `sf1`…`sf8` columns instead of a list. snake_case keys still validate for backward compatibility. See [POST /api/dedup/score](#post-apidedupscore-and-apidedupscorefile).
- **File logging** — logs now write to **both** the console and a rotating file (`LOG_FILE`, default `logs/enrichment_api.log`, ~10 MB × 5 backups), including uvicorn access/error lines. See [Configuration](#optional-with-defaults).
- **Azure-only LLM backend** — the dead direct-OpenAI config (`OPENAI_API_KEY` / `OPENAI_MODEL=gpt-4o`) was removed; Azure OpenAI is the only backend in every environment (the `openai_client.py` docstring was corrected).

### Phase 1 — Tier 1 LEI (GLEIF) company lookup (new)

- **New `enrichment/tier1_lei.py`** — a GLEIF/LEI registry client (`call_lei` + `LEIClient`), the company counterpart to ROR. For company-type records it resolves the official legal name + Legal Entity Identifier *before* the LLM company-canonical fallback. Precise `legalName`+country+ACTIVE filter, then `fuzzycompletions` fallback; module-level cache; retries/backoff.
- **Verification guard** — RapidFuzz `token_sort_ratio` (case-folded, legal-form-suffix-aware) with `LEI_NAME_MATCH_THRESHOLD` (default 88). Rejects statistically-close wrong entities; never accepts an unverified hit. `token_set_ratio` was rejected as unsafe (it scores any contained substring 100).
- **Orchestrator integration** — LEI runs on the company branch only (ROR-miss-company before the LLM, and ROR-matched-company where it overwrites `name1`); it never runs on or overwrites a ROR-matched research institution. A GLEIF failure never fails the record.
- **Registry ids in the response** — `lei_id` added to `EnrichmentResult`, and `ror_id` is **no longer `exclude=True`** — both now appear in the JSON `/enrich` response and as **"ROR ID" / "LEI ID"** columns in `/enrich/file` (`api/output_columns.py`), so the dedup phase can converge on a shared identifier.
- **ROR scoring fix + acronym expansion** — the identifier-token guard now also gates the subset/substring shortcut, so a query acronym (e.g. "HFT") can no longer false-match a same-city org on the shared city token. A ROR-local `_INSTITUTION_ACRONYMS` map drives an additive acronym-expanded affiliation retry ("HFT Stuttgart" → "Hochschule für Technik Stuttgart"). (Composes with the merged-in legal-suffix normalization in `_normalise_for_tokens`.)
- **Telemetry** — `lei_attempts`, `lei_hits_exact`, `lei_hits_fuzzy`, `lei_misses`, `lei_errors`, `tier1_lei_count` in the batch `summary`.
- **Config** — `LEI_LOOKUP_ENABLED`, `GLEIF_API_BASE`, `GLEIF_TIMEOUT_SECONDS`, `LEI_NAME_MATCH_THRESHOLD`, `LEI_MAX_RETRIES`.
- **Mock & tests** — `tests/mocks/lei_mock.py` + `tests/test_tier1_lei.py` (verification guard, exact/fuzzy/miss/error HTTP paths via `httpx.MockTransport`, orchestrator integration, feature-flag regression).

### Phase 2 — Deduplication Adjudicator (new)

- **New endpoint `POST /api/dedup/cluster-block`** — a "Pass 2" deduplication adjudicator that takes address-gated candidate rows and emits duplicate clusters. JSON in / JSON out; one or more address blocks per call, each processed independently. Auth is inherited from the Azure Function App (same pattern as every other route).
- **New `dedup/` package:**
  - `models.py` — `DedupRow` / `DedupRequest` / `DedupResultRow` / `DedupSummary` / `DedupResponse`. `cluster_id` is a sequential integer (globally unique within a response), `null` when not clustered.
  - `signatures.py` — STEP A: conservative normalization (accent-folding included), block-id derivation, and signature collapsing (the blow-up guard — identical rows collapse to one signature with no LLM call).
  - `prompts.py` — shared system prompt, Mode A/B prompt builders, `PROMPT_VERSION = "p2-dedup-v3"`.
  - `llm.py` — `DedupLLM`, which **reuses** `get_openai_client` rather than writing a new client; defensive JSON parsing; bounded retries; `reasoning_effort` fallback.
  - `adjudicator.py` — the per-block algorithm (Mode A partition / Mode B incremental assignment), the deterministic Name 2 asymmetry rule, cluster emission, global cluster-id remap, and structured telemetry.
- **Two-level identity model** — `(institution, department)`. Same institution + different department → distinct entities (never merged), even when they share a ROR ID. ROR id is a hint only. Empty vs populated Name 2 is decided deterministically in code, never by the LLM.
- **Registry-id hints** — both `ror_id` and `lei_id` (Phase 1 outputs) flow into dedup as soft LLM hints in the signature/candidate payloads: a shared ROR = same institution, a shared LEI = same legal entity. Neither is a deterministic cluster key, and neither overrides the Name 2 comparison (the system prompt enforces this). `DedupRow.lei_id` and `Signature.lei_id` carry the value; the first non-empty id per signature is used.
- **Routing** — `cluster` / `unique` / `manual_review` per row, with `llm_flag` distinguishing LLM-merged clusters from pure identical-row collapses.
- **Resilience** — a single bad LLM call never fails a block; affected signatures route to `manual_review` and `summary.errors` is incremented.
- **Telemetry** — per-block, per-LLM-call, and per-request structured logs to the `mdm-pipeline-insights` Application Insights instance (reuses the existing logging integration; no new SDK).
- **Mock & tests** — `tests/mocks/dedup_mock.py` (conservative offline LLM) and `tests/test_dedup.py` (algorithm + route coverage, see [Testing](#testing)).
- **Diagnostics** — `GET /diag/dedup-llm` surfaces the real adjudicator LLM error, API version, and reasoning-effort state.

### Phase 2 — Golden-record election, residue nomination & evaluation harness (new)

- **Golden-record election (Pass 3)** — `dedup/scoring.py` + `dedup/weights.json`: deterministic, no-LLM scoring over an editable weights table, electing one proposed winner per cluster. Re-runnable on retuned weights without re-adjudicating. Implements **G1 (Bernd's year-priority rule)** — a sales-order count only scores when the row owns its cluster's most-recent year (`_award_count`). New endpoints `POST /api/dedup/score` + `/api/dedup/score/file` (workbook I/O in `dedup/scoring_xlsx.py`, filled in place so the `Weights` sheet and originals survive).
- **Approval lifecycle** — `POST /api/dedup/approve` applies a human approve/reject to one cluster (stateless; on approve the proposed winner is promoted into the golden fields). A `manual_review` row leaves `is_golden_record`/`golden_record_id` blank — the winner lives in `proposed_golden_id`. **Phase 3 contract:** consume only `approval_status == "approved"` or `election_status == "unique"`.
- **Confidence-gated merges** — a merge below `CONFIDENCE_MERGE_THRESHOLD` (default `0.95`), an all-blocked cluster, an inherited clustering `manual_review`, or a zero-signal cluster is demoted to `manual_review` at election time (never re-runs the LLM).
- **Potential-inconsistency list** — `detect_issues` emits `low_confidence_merge`, `verdict_contradiction`, `all_blocked_cluster`, `tiebreak_decided`, `empty_scoring_payload`, `count_suppressed_by_recency`, `candidate_cap_exceeded` to a second `Issues` sheet and the JSON `issues` field (reviewer feedback loop).
- **Residue candidate nomination** — `dedup/candidates.py` + `_adjudicate_residue`: nominates the pairs Mode A/B never compared (empty-vs-populated Name 2, lone-bucket signatures) via converging ROR/LEI, suffix-stripped Jaro-Winkler name similarity, or token-set Jaccard, then pairwise-adjudicates each. Nomination never merges; runs before the identity guard. Capped by `MAX_CANDIDATES_PER_BLOCK` (default 50). New telemetry: `candidates_generated`, `candidates_by_rule`, `rejected_with_reasoning`, `candidate_cap_exceeded`.
- **Stable cluster id** — `dedup/cluster_key.py`: `cluster_hash` (`c_` + 12 hex sha256 over sorted member row_ids). The adjudicator mints it; the scorer re-derives it to detect a partial cluster (members split across score calls).
- **Evaluation harness** — `eval/dedup_eval.py`: `python -m eval.dedup_eval <scored.xlsx>` reports pairwise precision/recall/F1, the named business-risk counts (`wrongful_block_candidates`, `competing_goldens`, `uncertainty_upgrades`) with offending row_ids, and election/tie-break counts against the `expected_*` fixture columns; writes `eval_report.json`.
- **Weights drift detection** — every scored row carries `scored_with_weights_version` (12-hex fingerprint of the weights); the eval harness flags a workbook that mixes versions.
- **Tests** — `tests/test_scoring.py`, `tests/test_candidates.py`, `tests/test_dedup_eval.py`.

### Shared client change

- **`llm/openai_client.py::get_openai_client(api_version=None)`** was parameterized. It now resolves the API version as `api_version` arg → `AZURE_OPENAI_API_VERSION` env → `DEFAULT_AZURE_OPENAI_API_VERSION` (`2024-08-01-preview`). **Phase 1 behaviour is unchanged** (its callers pass nothing). The Phase 2 adjudicator passes a newer version (`AOAI_API_VERSION_DEDUP`, default `2025-04-01-preview`) because GPT-5.x reasoning models and the `reasoning_effort` parameter require it — this was the root cause of an early failure mode where every dedup row came back as `manual_review` with `errors > 0`.

### Corporate VPN / TLS fix

- **`get_openai_client` no longer hardcodes `verify=certifi.where()`.** A new `resolve_tls_verify()` helper honors a corporate CA bundle (`AZURE_OPENAI_CA_BUNDLE` / `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`) so a TLS-inspecting VPN no longer breaks LLM calls, supports `LLM_SSL_VERIFY=false` (insecure last resort), and falls back to certifi otherwise. The httpx client keeps `trust_env=True` (honors `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`) and exposes `LLM_HTTP_CONNECT_TIMEOUT` / `LLM_HTTP_TIMEOUT`. Fixes both phases (the dedup client reuses the factory). See [TLS and Corporate VPN](#tls-and-corporate-vpn).
- **ROR and GLEIF clients now reuse `resolve_tls_verify()` too.** Both previously hardcoded `verify=certifi.where()`, so on a TLS-inspecting VPN every ROR/GLEIF call failed the handshake — `ror_id`/`lei_id`/`domain` came back empty and every record fell through to the LLM. Now fixed.

### Registry country guards (ROR + GLEIF)

- Both Tier 1 registry lookups now **reject a candidate whose registered country doesn't match the record's country** when it is known. This stops same-name, wrong-country entities from being attached — e.g. a US "BASF" (`ror.org/002yzpx87`) for a German BASF record, or a Norwegian "Siemens AS" LEI for a German Siemens record.
- **ROR** ([tier1_ror.py](enrichment/tier1_ror.py)): applied on both the affiliation path (whose scorer ignores the affiliation-string country) and the query path (covering the no-filter retry). Country is compared via `locations[0].geonames_details.country_code`.
- **GLEIF/LEI** ([tier1_lei.py](enrichment/tier1_lei.py)): applied in `_best_verified_candidate` on both the precise and fuzzy paths (`fuzzycompletions` can't be country-filtered at the API, so the post-filter is mandatory).
- A wrong-country id is worse than none — it would wrongly converge distinct legal entities in Phase 2 dedup — so a rejection becomes a clean miss that falls through to the LLM path. Case-insensitive; no filtering when the record has no resolvable country. Covered by `tests/test_tier1_ror_country.py` and the country tests in `tests/test_tier1_lei.py`.

### New environment variables

`AOAI_DEPLOYMENT_DEDUP`, `AOAI_API_VERSION_DEDUP`, `DEDUP_REASONING_EFFORT`, `SIG_PARTITION_THRESHOLD`, `DEDUP_MAX_CONCURRENCY`, `DEDUP_MAX_RETRIES`, `AZURE_OPENAI_API_VERSION`, the Phase 2 election / residue-nomination vars (`CONFIDENCE_MERGE_THRESHOLD`, `NAME_CANDIDATE_THRESHOLD`, `TOKEN_CANDIDATE_THRESHOLD`, `MAX_CANDIDATES_PER_BLOCK`), and the Tier 1 LEI vars (`LEI_LOOKUP_ENABLED`, `GLEIF_API_BASE`, `GLEIF_TIMEOUT_SECONDS`, `LEI_NAME_MATCH_THRESHOLD`, `LEI_MAX_RETRIES`) — all documented in [Configuration and Environment Variables](#configuration-and-environment-variables) and `.env.example`.
