# SAP Customer Master Data Name Enrichment API

An intelligent, multi-tier enrichment service built for Bruker Corporation's Master Data Management (MDM) pipeline. It resolves incomplete, abbreviated, misspelled, or incorrectly formatted SAP customer master data records — specifically institution and company names — through a pipeline that combines deterministic preprocessing, API lookups, web search, contact verification, and LLM inference.

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Solution Strategy](#solution-strategy)
3. [Technology Stack](#technology-stack)
4. [Architecture Overview](#architecture-overview)
5. [The Enrichment Pipeline: Stage by Stage](#the-enrichment-pipeline-stage-by-stage)
   - [Stage 0: Name1 Overflow Check (UC 0)](#stage-0-name1-overflow-check-uc-0)
   - [Stage 1: Preprocessing (UC 6-12)](#stage-1-preprocessing-uc-6-10)
   - [Stage 2: Tier 1 — ROR API Lookup](#stage-2-tier-1--ror-api-lookup)
   - [Stage 3: Tier 2 — Multi-Mode Canonicalization](#stage-3-tier-2--multi-mode-canonicalization)
   - [Stage 4: Tier 3 — LLM Inference (Last Resort)](#stage-4-tier-3--llm-inference-last-resort)
   - [Finalization](#finalization)
6. [Use Case Reference Table](#use-case-reference-table)
7. [Record Classification Logic](#record-classification-logic)
8. [Confidence, Flags, and Enrichment Status](#confidence-flags-and-enrichment-status)
9. [Data Models](#data-models)
10. [API Endpoints](#api-endpoints)
11. [Project Structure](#project-structure)
12. [Module-by-Module Reference](#module-by-module-reference)
13. [External Services and APIs](#external-services-and-apis)
14. [Configuration and Environment Variables](#configuration-and-environment-variables)
15. [Setup and Installation](#setup-and-installation)
16. [Running Locally](#running-locally)
17. [Testing](#testing)
18. [Azure Function Deployment](#azure-function-deployment)
19. [ADF Integration and DATAshaper Mapping](#adf-integration-and-datashaper-mapping)
20. [Complete Data Flow Diagram](#complete-data-flow-diagram)

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
| **Tier 1** | ROR API (Research Organization Registry) | Low (free public API) | High | Institutions, companies |
| **Tier 2A** | Contact person web lookup (SERP + page fetch + LLM) | Medium | Medium-High | When contact is available and Tier 1 matched the parent org |
| **Tier 2 Canonical** | LLM canonicalization (no web search) | Low-Medium | High (only accepts high-confidence answers) | When Name2/3 present but no ROR child match |
| **Tier 2B** | Department web search (SERP + page fetch + LLM) | Medium | Medium-Low | When no contact or Tier 2A failed |
| **Tier 3** | Pure LLM inference | Medium | Low (always flagged) | Last resort, all other tiers failed |

**Key design principles:**

1. **Never fabricate data.** If confidence is low, return the original values and flag for human review.
2. **Deterministic before probabilistic.** Regex-based preprocessing runs before any API or LLM call.
3. **Structured extraction over free-form generation.** LLM prompts extract from structured page elements (URL path, title, H1, breadcrumb) rather than interpreting free-form body text.
4. **Scope filtering.** The pipeline distinguishes department-level units (acceptable) from granular units like individual labs, groups, or facilities (rejected per UC 4/5 scope rules).
5. **Transparency.** Every result includes `tier_used`, `source`, `confidence`, `domain`, `flag_for_review`, and `flag_reason` so humans can audit the pipeline's decisions.

---

## Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Web Framework** | FastAPI (Python 3.11+) | REST API with async support |
| **Serverless Runtime** | Azure Functions v2 (ASGI wrapper) | Production deployment |
| **LLM** | OpenAI API / Azure OpenAI | Extraction, canonicalization, inference |
| **Organization Registry** | ROR API v2 | Institution/organization lookup and classification |
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

**Problem:** Sometimes a single organization name is split across Name1 and Name2 because the name exceeds SAP's field length limit. For example:

| Name1 | Name2 | Actual Organization |
|-------|-------|--------------------|
| Adams Air | Hydraulics Inc | Adams Air Hydraulics Inc |
| Brigham and Women's | Hospital | Brigham and Women's Hospital |

**Trigger:** Both Name1 and Name2 are non-blank.

**Logic:**
1. Concatenate Name1 + " " + Name2
2. Ask the LLM: "Does this read as a single continuous organization name?"
3. LLM returns `is_overflow` (boolean), `confidence` (high/medium/low), and `reasoning`

**Outcome:**
- If overflow detected with medium or high confidence: the record is **immediately flagged** and returned. No further tiers run. The flag reason explains the overflow so a human can correct the SAP field split.
- If not overflow: pipeline continues normally.

**Why stop early?** If Name1 + Name2 is one org name, running Tier 1 on just Name1 would match the wrong entity (e.g., searching ROR for "Adams Air" instead of "Adams Air Hydraulics Inc").

---

### Stage 1: Preprocessing (UC 6-12)

**File:** `enrichment/preprocess.py`

Preprocessing runs entirely on regex patterns with **no network calls** (except one optional LLM call for ambiguous plain-name classification). It cleans misplaced data out of name fields before any enrichment begins.

#### UC 6 — Accounts Payable Normalization

**Detects patterns like:** "Accounts Payable", "A/P", "AP Dept", "AP Invoice", "Attn AP"

**Action:** Normalizes to "Accounts Payable" and flags the field. The orchestrator handles AP records specially — they are often not real organization names but payment routing labels.

#### UC 7 — Contact Person Extraction

Detects person names stored in Name1/Name2/Name3 fields and moves them to the `contact` field.

Three detection patterns:
- **Pattern A:** Explicit prefix — `Attn: Jane Smith`, `c/o Dr. Robert Lee`
- **Pattern B1:** Title-prefixed names — `Dr. Jane Smith`, `Prof. John Doe`, `Mr. Robert Lee`
- **Pattern B2:** Plain capitalized names (2-3 words, no title) — `Jane Smith`, `Robert Alan Lee`
  - This pattern is ambiguous: "Jane Smith" is a person, but "Bell Labs" is not
  - B2 triggers an **LLM classification call** (`llm_classify_plain_names_async()`) only when `allow_llm=True`
  - Names with organization signals (Inc, Corp, Department, University, etc.) are rejected before the LLM call

**Guard:** Any name containing organization keywords (Inc, Corp, LLC, Department, University, Hospital, Institute, Laboratory, etc.) is never classified as a person.

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

#### Name Scoring Logic

The scoring system (`_compute_name_score()`) is carefully designed to prevent false matches:

1. **Exact match** -> score 1.0 (any ROR name variant)
2. **Token-subset match** -> score 1.0 (all 4+ character tokens from the query appear in a canonical ROR name)
3. **Substring match** -> score 1.0 (query is >90% the length of a canonical name and is contained within it)
4. **Fuzzy token sort ratio** (0.0 to 1.0):
   - Only compared against canonical names (not short aliases, which cause false positives)
   - **Distinctive-token guard:** When the query contains generic domain words (regional, health, medical, center, national, general, community, memorial), the scorer requires at least one **distinctive token** (5+ characters) to be shared between query and match
   - Example: "Newman Regional Health" has distinctive token "newman". "Lakeland Regional Health" does not contain "newman", so the fuzzy score is capped at 0.7 even if the generic words produce a high fuzzy ratio.
   - This prevents the common false-positive pattern where organizations with similar generic names (many hospitals, regional health systems, community colleges) match each other.

#### Child Matching

Once Tier 1 matches a parent organization, it attempts to match Name2 and Name3 against the ROR **children list** (related organizations of type "child"):

- This is done **locally** — no second API call
- Uses `rapidfuzz.fuzz.token_sort_ratio()` with a threshold of 70%
- If a child matches, the official child name replaces the input Name2/Name3
- Example: Name2 = "Dept of Chem" matches child "Department of Chemistry" at the parent "Stanford University"

#### Classification from ROR Types

Rather than using keyword heuristics ("University" -> research institution), the pipeline derives `record_type` from the ROR organization's declared types:

```
ROR types: education, healthcare, government, facility, nonprofit, archive, other
  -> record_type = "research_institution"

ROR type: company
  -> record_type = "company"
```

If Tier 1 misses (no ROR match), classification falls back to keyword detection via `looks_like_research_institution()`.

#### Caching

A module-level ROR cache (`_ror_cache`) prevents duplicate API calls within a batch. For example, if a batch contains three records for "MIT", only one ROR API call is made. The cache is cleared between batches.

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

**Manual-review guard:** Research institutions where Tier 2A is not actionable — i.e. (a) no department AND no contact, or (b) the contact field names more than one person — are flagged for manual review regardless of which tier ultimately runs. See the [Flag Rules table](#flag-rules) for exact reasons.

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
- If input Name3 was already populated, the lab is **not** demoted (data-loss avoidance) and the record is flagged with a "Name 3 already populated" warning.
- `tier_used = 2`, `source = "dept_search"`, `source_url` = the page used, `flag_for_review = True`, `use_cases_triggered` includes `13`.

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

**Note:** Tier 2B results are always flagged for human review because web search evidence is inherently less reliable than ROR or contact page verification.

---

### Stage 4: Tier 3 — LLM Inference (Last Resort)

**File:** `enrichment/tier3_llm.py`

**Trigger:** All previous tiers failed or were not applicable.

**Process:** A single LLM call receives ALL available fields — Name1, Name2, Name3, contact, email, city, state, country, street — and attempts to infer the correct organization and department names.

**Confidence handling:**
- **High or medium confidence:** Write LLM suggestions to enriched fields, but mark status as `unresolved` (always requires human review)
- **Low confidence:** Do NOT overwrite originals — return originals unchanged, mark as `unresolved`

**Why always flagged?** Tier 3 has no external evidence — it relies entirely on the LLM's training data. This makes it useful as a starting point for human review but not reliable enough for automatic application.

---

### Finalization

**Function:** `finalise()` in `enrichment/orchestrator.py`

After all tiers have run, the finalization step applies a set of deterministic rules:

1. **Empty string guard:** Enriched fields must be either `None` or a non-empty string. Empty strings (`""`) are converted to `None`.

2. **Unit canonicalization:** Academic unit names are normalized to standard forms:
   - "Dept of Chemistry" -> "Department of Chemistry"
   - "Chem Division" -> "Division of Chemistry"
   - Exception: Granular units (labs, groups, facilities) are NOT canonicalized

3. **Passthrough logic:** If no tier enriched a field AND preprocessing didn't clear it, the original value is retained. The pipeline never blanks out a field that it couldn't improve.

4. **Changed flags:** `name1_changed`, `name2_changed`, etc. are set to `True` only when `enriched != original AND enriched is not None`. This allows consumers to know exactly which fields were modified.

5. **Deduplication rules:**
   - **Rule 1:** If Name2 was blank in input AND no tier populated it AND no contact was available, set `name2_enriched = None` (don't fabricate)
   - **Rule 2:** If preprocessing stripped Name2 (e.g., it was an email address), don't let Tier 3 fabricate a replacement
   - **Rule 3:** If `name2_enriched == name1_enriched`, drop Name2 (no echo of the parent org name)

---

## Use Case Reference Table

The pipeline tracks which "use cases" fired for each record. These are reported in the `use_cases_triggered` array in each result:

| UC | Name | Stage | Trigger | Action |
|----|------|-------|---------|--------|
| 0 | Name1 Overflow Detection | Stage 0 | Both Name1 + Name2 non-blank | LLM checks if it's one split name; flags if yes |
| 2 | Institution ROR Resolution | Tier 1 | ROR match found | Enriches Name1 with official ROR name |
| 3 | Company Name Canonicalization | Tier 1/2 | ROR miss + looks like a company | LLM canonicalizes company name with geographic context |
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

**File:** `enrichment/classifier.py`

Records are classified as either `research_institution` or `company`. Classification determines which tiers and modes are available:

| Classification Source | Method | Example |
|----------------------|--------|---------|
| **ROR org types** (primary) | If ROR type is `education`, `healthcare`, `government`, `facility`, `nonprofit`, `archive`, or `other` -> `research_institution`. If `company` -> `company`. | ROR says "MIT" is type `education` -> `research_institution` |
| **Keyword heuristics** (fallback, when ROR misses) | `looks_like_research_institution()` checks for keywords: University, College, Hospital, Medical, Institute, Research, School, Academy, etc. | "Newman Regional Health" contains "Health" -> `research_institution` |
| **Default** | If neither method can classify -> `company` | "Acme Widget Co" -> `company` |

**Impact on pipeline routing:**
- `research_institution`: Eligible for Tier 2A (contact lookup) and Tier 2 Canonical
- `company`: Routes to company canonicalization (LLM), then Tier 2B if Name2 exists
- Both types: Eligible for Tier 1, Tier 2B, and Tier 3

---

## Domain Resolution

Each result carries a `domain` field — the registrable domain (e.g. `mit.edu`, `example.co.uk`) for the organization in Name1. It is populated from two sources, in priority order:

1. **ROR website** (primary). When Tier 1 matches an ROR record, its declared website link is parsed by `extract_domain()` and written to `domain`. This covers the vast majority of research institutions.
2. **`source_url` host** (fallback). When ROR didn't match but a successful Tier 2A or Tier 2B run produced a `source_url`, `finalise()` derives the domain from that URL's host. Tier 2A URLs are on-domain by construction (the contact's faculty page); Tier 2B URLs may or may not be on the institution's own site, so use this value cautiously when `source != "ROR"`.

If neither is available, `domain` is `null`.

---

## Confidence, Flags, and Enrichment Status

**File:** `enrichment/confidence.py`

### Enrichment Status Values

| Status | Meaning | Human Action Required |
|--------|---------|----------------------|
| `enriched` | Name1 and/or Name2 enriched with sufficient confidence | None (auto-applied) |
| `verified` | Name2 exactly matched against contact's faculty page (Tier 2A Mode B) | None (confirmed correct) |
| `unresolved` | Enrichment attempted but confidence insufficient for auto-application | Manual review needed |
| `failed` | Pipeline error or all tiers returned nothing | Investigation needed |

### Flag Rules

| Scenario | Flagged? | Flag Reason |
|----------|----------|-------------|
| Tier 1 ROR match, high confidence | No | — |
| Tier 2A exact match (>=95% fuzzy) | No | — |
| Tier 2A partial match (60-95%) | Yes | "Partial match — confirm enriched Name 2" |
| Tier 2A correction (no match) | Yes | "Name 2 corrected via contact lookup" |
| Research institution with no dept and no contact | Yes | "Research institution with no department and no contact — manual review required" |
| Research institution with multiple contacts (e.g. "John Smith and Jane Doe") | Yes | "Research institution with multiple contacts — manual review required" |
| Tier 2B any result | Yes | "Department search — verify enriched Name 2" |
| Tier 2 Canonical high confidence | No | — |
| Tier 3 any result | Yes | "LLM inference — requires verification" |
| Medium confidence from any tier | Yes | "Medium confidence — recommend review" |
| UC 0 overflow detected | Yes | "Name1 overflow — Name1+Name2 appear to be one name" |

---

## Data Models

**File:** `api/models.py`

### Request

```json
{
  "records": [
    {
      "record_id": "BSP_001",
      "name1": "MIT",
      "name2": "Dept of AI",
      "name3": null,
      "contact": "Dr. Jane Smith",
      "email": "jsmith@mit.edu",
      "street": null,
      "street1": null,
      "street2": null,
      "street3": null,
      "city": "Cambridge",
      "state": "MA",
      "zip": "02139",
      "country": "US"
    }
  ],
  "options": {
    "max_concurrency": 5,
    "serp_provider": "serpapi",
    "skip_tier": null
  }
}
```

### Response

```json
{
  "results": [
    {
      "record_id": "BSP_001",
      "name1_original": "MIT",
      "name1_enriched": "Massachusetts Institute of Technology",
      "name1_changed": true,
      "name2_original": "Dept of AI",
      "name2_enriched": "Department of Electrical Engineering and Computer Science",
      "name2_changed": true,
      "name3_original": null,
      "name3_enriched": null,
      "name3_changed": false,
      "contact_original": "Dr. Jane Smith",
      "contact_enriched": "Dr. Jane Smith",
      "contact_changed": false,
      "email_original": "jsmith@mit.edu",
      "email_enriched": "jsmith@mit.edu",
      "email_changed": false,
      "street1_original": null,
      "street1_enriched": null,
      "street1_changed": false,
      "street2_original": null,
      "street2_enriched": null,
      "street2_changed": false,
      "street3_original": null,
      "street3_enriched": null,
      "street3_changed": false,
      "record_type": "research_institution",
      "tier_used": 2,
      "tier2_mode": "2A_verification",
      "confidence": "high",
      "source": "contact_lookup",
      "ror_id": "https://ror.org/042nb2s44",
      "source_url": "https://www.eecs.mit.edu/people/jane-smith",
      "domain": "mit.edu",
      "contact_used": true,
      "name2_match_result": "partial",
      "use_cases_triggered": [5, 8],
      "flag_for_review": true,
      "flag_reason": "Partial match — confirm enriched Name 2",
      "enrichment_status": "enriched",
      "duration_ms": 2340,
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
      {"record_id": "BSP_005", "name1": "UCLA", "city": "Los Angeles", "state": "CA", "country": "US"}
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
      {"record_id": "BSP_001", "name1": "Massachusetts Institute of Technology", "name2": null, "contact": "Dr. Jane Smith", "city": "Cambridge", "state": "MA", "country": "US"}
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
      {"record_id": "BSP_002", "name1": "Massachusetts Institute of Technology", "name2": "Dept of AI", "contact": "Dr. Jane Smith", "city": "Cambridge", "state": "MA", "country": "US"}
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
      {"record_id": "BSP_003", "name1": "Stanford University", "name2": "Chemistry Department", "city": "Stanford", "state": "CA", "country": "US"}
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
      {"record_id": "B1", "name1": "MIT", "name2": "Department of Physics"},
      {"record_id": "B2", "name1": "Pfizer Inc", "name2": "R&D"},
      {"record_id": "B3", "name1": "UCLA", "contact": "Dr. John Doe"}
    ],
    "options": {"max_concurrency": 3}
  }'
```

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
│   ├── routes.py                 # Route definitions: /health, /enrich, /tiers
│   ├── models.py                 # Pydantic v2 request/response schemas
│   └── middleware.py             # Request logging, timing, error handling
│
├── enrichment/                   # Core enrichment pipeline
│   ├── orchestrator.py           # Main pipeline controller (tier escalation, finalization)
│   ├── preprocess.py             # Deterministic cleanup: UC 6-12 (regex-based)
│   ├── classifier.py             # Record type classification (research_institution vs company)
│   ├── overflow_check.py         # UC 0: Name1+Name2 overflow detection
│   ├── tier1_ror.py              # Tier 1: ROR API client, scoring, child matching
│   ├── tier2a_contact.py         # Tier 2A: Contact person lookup (Modes A & B)
│   ├── tier2b_dept.py            # Tier 2B: Department web search
│   ├── tier2_canonical.py        # Tier 2 Canonical: LLM-only department normalization
│   ├── lab_resolver.py           # UC 13: granular unit → parent department resolver
│   ├── tier3_llm.py              # Tier 3: Pure LLM inference (last resort)
│   ├── company_canonical.py      # Company name canonicalization via LLM
│   └── confidence.py             # Scoring rules, flag logic, status assignment
│
├── llm/                          # LLM integration layer
│   ├── openai_client.py          # AsyncOpenAI wrapper (JSON mode, retries)
│   ├── prompts.py                # All LLM prompt templates as module constants
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
│   ├── cache.py                  # Per-batch in-memory cache (ROR + SERP deduplication)
│   └── __init__.py
│
├── tests/                        # Test suite
│   ├── conftest.py               # Fixtures and mock injection
│   ├── test_*.py                 # Unit tests per module
│   ├── mocks/                    # Mock client implementations
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

Defines three endpoints: `/health`, `/tiers`, `/enrich`. The `/enrich` endpoint instantiates the Orchestrator, runs `enrich_batch()`, and returns the aggregated response.

### `api/models.py` — Pydantic Schemas

Defines `EnrichmentRecord`, `EnrichmentOptions`, `EnrichmentRequest`, `EnrichmentResult`, `BatchSummary`, and `EnrichmentResponse` using Pydantic v2 with strict validation.

### `api/middleware.py` — Request Middleware

Adds request logging with unique IDs, timing headers (`X-Request-ID`, `X-Duration-MS`), and structured JSON log output. Catches unhandled exceptions and returns clean 500 responses.

### `enrichment/orchestrator.py` — Pipeline Controller

The heart of the system. The `Orchestrator` class coordinates the full pipeline for each record: overflow check -> preprocessing -> Tier 1 -> Tier 2 -> Tier 3 -> finalization. Manages async concurrency via `asyncio.Semaphore`. Contains the `finalise()` function with all post-processing rules.

### `enrichment/preprocess.py` — Deterministic Cleanup

Pattern-matching engine for UC 6-10. Runs before any network call. Returns `PreprocessResult` with cleaned fields and tracking of which use cases fired.

### `enrichment/tier1_ror.py` — ROR Client

Async ROR API client with hybrid lookup (affiliation + query), sophisticated name scoring with distinctive-token guards, local child matching, and organization type extraction for classification.

### `enrichment/tier2a_contact.py` — Contact Lookup

SERP search for contact person pages, name verification filtering, page fetching, and LLM-based affiliation extraction. Supports population (Mode A) and verification (Mode B).

### `enrichment/tier2b_dept.py` — Department Search

SERP search for department pages, candidate ranking (on-domain priority), structured element extraction, and LLM-based official name extraction from URL/title/H1/breadcrumb only.

### `enrichment/tier2_canonical.py` — LLM Canonicalization

Single LLM call to normalize department names to official wording. No web search — relies on LLM knowledge. Conservative: only accepts high-confidence results.

### `enrichment/tier3_llm.py` — LLM Inference

Last-resort LLM call using all available fields. Always flagged for review. High/medium confidence suggestions are written; low confidence preserves originals.

### `enrichment/company_canonical.py` — Company Canonicalization

Specializes in normalizing company names with geographic context. Used when Tier 1 misses and the record doesn't look like a research institution.

### `enrichment/overflow_check.py` — Overflow Detection

LLM-based check for Name1+Name2 being a single split organization name. Early-exit mechanism that prevents mis-enrichment of overflow records.

### `enrichment/confidence.py` — Scoring and Flags

Centralizes all flag-for-review logic and enrichment status assignment. Ensures consistent flagging rules across all tiers.

### `llm/openai_client.py` — OpenAI Client

Async wrapper around `openai.AsyncOpenAI` (or `AsyncAzureOpenAI` for production). Enforces JSON response format, strips code fences, retries once on parse failure. Temperature fixed at 0.0 for deterministic output.

### `llm/prompts.py` — Prompt Templates

All LLM prompt templates as Python constants. Includes system prompts and user prompt templates for: overflow check, Tier 2A affiliation extraction, Tier 2B department extraction, Tier 2 canonical normalization, company canonicalization, Tier 3 inference, and plain-name classification.

### `search/base.py` — Search Interface

Abstract `SearchClient` base class and `SearchResult` dataclass. Defines the contract that both SerpAPI and DuckDuckGo clients implement.

### `search/serpapi_client.py` — SerpAPI Client

Google Search via SerpAPI. Runs synchronously in a thread executor (async wrapper). Requires `SERPAPI_KEY`.

### `search/duckduckgo_client.py` — DuckDuckGo Client

Free search fallback using the `duckduckgo-search` library. No API key required. Used when `SERPAPI_KEY` is not configured.

### `search/page_fetcher.py` — Page Fetcher

HTTP page fetcher with BeautifulSoup parsing. Extracts structured elements: URL path, page title, H1, breadcrumb navigation, and truncated body text. Detects breadcrumbs via `aria-label`, `role="navigation"`, and class patterns. User-Agent: "BrukerMDM-Enrichment/1.0".

### `utils/text_utils.py` — Text Utilities

- `country_to_iso_code()`: Maps country names/codes to ISO alpha-2 (60+ countries)
- `expand_abbreviations()`: "Dept" -> "Department", "Univ" -> "University", etc.
- `canonicalise_unit_name()`: Normalizes to "Department/Division/School/Faculty of X" form
- `is_granular_unit()`: Detects lab/group/centre/facility units for scope filtering
- `looks_like_research_institution()`: Keyword-based fallback classification
- `extract_domain()`: URL -> registrable domain (handles two-part TLDs)
- `score_search_result()`: Heuristic scoring for people/faculty page detection

### `utils/cache.py` — Batch Cache

Per-batch in-memory cache with separate ROR and SERP namespaces. Keyed on lowercased query strings. Prevents duplicate API calls within a single batch. Created fresh for each `/enrich` request.

---

## External Services and APIs

| Service | Purpose | Authentication | Fallback |
|---------|---------|---------------|----------|
| **ROR API v2** (`api.ror.org`) | Organization/institution lookup | None (free, public) | No fallback — if ROR misses, escalate to Tier 2/3 |
| **OpenAI API** / **Azure OpenAI** | All LLM calls (extraction, canonicalization, inference, classification) | `OPENAI_API_KEY` or Azure credentials | No fallback — records fall to `unresolved` |
| **SerpAPI** (`serpapi.com`) | Google Search results for Tier 2A/2B | `SERPAPI_KEY` | DuckDuckGo |
| **DuckDuckGo** | Free web search | None | N/A (is itself the fallback) |
| **Institution websites** | HTML pages for structured extraction | None | Tier 3 fallback if page fetch fails |

---

## Configuration and Environment Variables

Copy `.env.example` to `.env` and configure:

### Required

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (for local development) |

### Optional (with defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_MODEL` | `gpt-4o` | LLM model to use |
| `ROR_API_BASE` | `https://api.ror.org/v2/organizations` | ROR API endpoint |
| `ROR_CONFIDENCE_THRESHOLD` | `0.8` | Minimum score to accept a ROR match |
| `FUZZY_MATCH_THRESHOLD` | `80` | RapidFuzz threshold for name matching |
| `MAX_PAGE_CONTENT_CHARS` | `3000` | Maximum body text extracted per page |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `10` | HTTP timeout for page fetching |
| `DEFAULT_MAX_CONCURRENCY` | `5` | Default concurrent record processing limit |
| `SERPAPI_KEY` | *(none)* | SerpAPI key; if absent, DuckDuckGo is used |
| `MOCK_EXTERNAL_CALLS` | `false` | Use mock clients (no real API calls) |
| `ENV` | `production` | Set to `local` for development (enables dotenv loading) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Production (Azure OpenAI)

For Bruker production deployment, replace `OPENAI_API_KEY` with Azure OpenAI credentials:

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | e.g., `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name (e.g., `gpt-4o`) |

And swap `AsyncOpenAI` for `AsyncAzureOpenAI` in `llm/openai_client.py`.

---

## Setup and Installation

### Prerequisites

- Python 3.11 or higher
- An OpenAI API key (or Azure OpenAI credentials)
- (Optional) A SerpAPI key for Google Search

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
```

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

The application deploys as an **Azure Function v2** using ASGI integration. The `function_app.py` entry point wraps the same FastAPI app that runs locally:

```python
from azure.functions import AsgiFunctionApp
from api.app import app

azure_app = AsgiFunctionApp(app=app, http_auth_level=func.AuthLevel.FUNCTION)
```

**Deployment flow:**
```
Azure Function Runtime
  └── function_app.py (ASGI wrapper)
       └── api/app.py (shared FastAPI instance)
            └── enrichment/orchestrator.py (pipeline)
```

Environment variables are configured via **Azure Application Settings** (not `.env` files).

---

## ADF Integration and DATAshaper Mapping

In production, the enrichment API is called by Azure Data Factory (ADF) as part of the DATAshaper MDM pipeline:

```
SAP Source  →  DATAshaper Stored Procedure (extract batch)
                         ↓
            ADF Web Activity: POST /enrich
                         ↓
            Enrichment API processes batch
                         ↓
            ADF receives JSON response
                         ↓
            DATAshaper Stored Procedure (write back enriched values + issues)
```

### Status-to-Severity Mapping

The `enrichment_status` field maps to DATAshaper issue severities:

| enrichment_status | DATAshaper Issue Severity | Action |
|---|---|---|
| `enriched` | No issue (auto-applied) | Values written directly to master data |
| `verified` | Info issue (confirmed correct) | Values confirmed, logged for audit |
| `unresolved` | Warning issue (manual review) | Flagged for data steward review |
| `failed` | Error issue (process failed) | Requires investigation |

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
  │    ├─ LLM: "Is Name1+Name2 one continuous org name?"
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
  │    │   ├─ Write official ROR name to name1_enriched
  │    │   ├─ Classify from ROR org types → research_institution | company
  │    │   ├─ Try child matching for Name2/Name3 (local, no 2nd API call)
  │    │   └─ If no Name2 signal and no contact → RETURN (Tier 1 final)
  │    └─ If MISS:
  │         ├─ If looks like research institution → passthrough, may escalate
  │         └─ If looks like company → try LLM company canonicalization
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
       └─ RETURN EnrichmentResult
  │
  ▼
Aggregate batch → BatchSummary (counts by tier, status, type)
  │
  ▼
RETURN EnrichmentResponse (JSON)
```
