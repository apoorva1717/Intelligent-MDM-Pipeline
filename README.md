# SAP Customer Master Data Name Enrichment API / 20/06/26

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
   - [Stage 1: Preprocessing (UC 6-12)](#stage-1-preprocessing-uc-6-10)
   - [Stage 2: Tier 1 — ROR API Lookup](#stage-2-tier-1--ror-api-lookup)
   - [Stage 2 (Company): Tier 1 — GLEIF / LEI Registry Lookup](#stage-2-company-tier-1--gleif--lei-registry-lookup)
   - [Stage 3: Tier 2 — Multi-Mode Canonicalization](#stage-3-tier-2--multi-mode-canonicalization)
   - [Stage 4: Tier 3 — LLM Inference (Last Resort)](#stage-4-tier-3--llm-inference-last-resort)
   - [Finalization](#finalization)
6. [Use Case Reference Table](#use-case-reference-table)
7. [Record Classification Logic](#record-classification-logic)
8. [Confidence, Flags, and Enrichment Status](#confidence-flags-and-enrichment-status)
9. [Data Models](#data-models)
10. [API Endpoints](#api-endpoints)
11. [Phase 2 — Deduplication Adjudicator](#phase-2--deduplication-adjudicator)
    - [Why a Separate Pass](#why-a-separate-pass)
    - [The Two-Level Identity Model](#the-two-level-identity-model)
    - [Critical Identity Rules](#critical-identity-rules)
    - [Endpoint Contract](#endpoint-contract)
    - [The Per-Block Algorithm](#the-per-block-algorithm)
    - [Mode A vs Mode B](#mode-a-vs-mode-b)
    - [The Deterministic Name 2 Asymmetry Rule](#the-deterministic-name-2-asymmetry-rule)
    - [LLM Call Details](#llm-call-details)
    - [Routing, Clusters, and the llm_flag](#routing-clusters-and-the-llm_flag)
    - [Telemetry](#telemetry)
    - [Chaining Enrichment → Dedup](#chaining-enrichment--dedup)
    - [Dedup Diagnostics](#dedup-diagnostics)
12. [Project Structure](#project-structure)
13. [Module-by-Module Reference](#module-by-module-reference)
14. [External Services and APIs](#external-services-and-apis)
15. [Configuration and Environment Variables](#configuration-and-environment-variables)
16. [Setup and Installation](#setup-and-installation)
17. [Running Locally](#running-locally)
18. [Testing](#testing)
19. [Azure Function Deployment](#azure-function-deployment)
20. [ADF Integration and DATAshaper Mapping](#adf-integration-and-datashaper-mapping)
21. [Complete Data Flow Diagram](#complete-data-flow-diagram)
22. [Changelog](#changelog)

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
5. **Legal-form normalization:** before scoring, `_normalise_for_tokens()` strips `.`/`,` and canonicalizes legal-entity suffixes (Incorporated→inc, Corporation→corp, Company→co, Limited→ltd, "L.L.C."→llc, "Limited Liability Company"→llc, …) **symmetrically** on the query and every ROR name variant. So "Acme Corp.", "Acme Corp" and "Acme Corporation" all compare equal, and two SAP rows that differ only by legal form don't diverge (one matching ROR, the other missing).
6. **Identifier-token (acronym) guard:** short all-caps acronyms in the query (e.g. "HFT", "EMSL", "ASL") must appear in the candidate before the exact/subset/substring shortcuts can score 1.0. Without it, "HFT Stuttgart" would subset-match *any* "… Stuttgart" org on the shared city token alone (Marienhospital Stuttgart, Stuttgart Observatory) and produce a confidently wrong match.

#### Institution Acronym Expansion

Some institutions are referenced by an acronym that ROR does **not** carry as an alias — e.g. "HFT Stuttgart" (ROR has no "HFT" alias, so the bare query returns unrelated same-city orgs). A small ROR-local map (`_INSTITUTION_ACRONYMS`, e.g. `HFT → Hochschule für Technik`) drives an **additive** affiliation retry: when the raw name misses, the affiliation endpoint is tried once more with the acronym expanded ("HFT Stuttgart" → "Hochschule für Technik Stuttgart"). It is kept out of the global `expand_abbreviations` map so it never affects search terms or output names, and names that already resolve never reach it. Extend the map as new institution acronyms come up.

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

**Verification guard (required):** every candidate's `legalName` is scored against the input with RapidFuzz `token_sort_ratio` (case-folded — GLEIF returns names UPPERCASE; legal-form suffixes like AG/Inc/Ltd/GmbH stripped so "Novartis" verifies against "NOVARTIS AG"). Candidates below `LEI_NAME_MATCH_THRESHOLD` (default 88) are rejected. GLEIF fuzzy is statistical — without this guard it fabricates matches (e.g. "Personalvorsorgestiftung der Pfizer AG in Liquidation" for "Pfizer AG"). `token_set_ratio` is deliberately **not** used: it scores any contained substring 100 and would accept that wrong entity.

**On a verified match:**
- `name1_enriched` ← official GLEIF `legalName`
- `lei_id` ← the LEI · `record_type = "company"` · `source = "gleif"` · `tier_used = 1`
- `confidence = high` (precise filter) / `medium` (fuzzy)
- `domain` stays `null` — GLEIF has no website field. Downstream web-search tiers that need a domain simply won't have one for these; that's acceptable.

**On miss / below-threshold / timeout / API error:** nothing is fabricated — the record falls through to the existing LLM company-canonical path unchanged. **A GLEIF failure never fails the record.**

**Feature flag:** `LEI_LOOKUP_ENABLED` (default `true`) disables the whole step for cheap A/B testing — behaviour then reverts to LLM-only, identical to before.

**Telemetry:** `lei_attempts`, `lei_hits_exact`, `lei_hits_fuzzy`, `lei_misses`, `lei_errors`, and `tier1_lei_count` in the batch `summary`.

**Caching & TLS:** a module-level `_lei_cache` (keyed on name + country) dedupes calls within a batch, cleared per batch like the ROR cache. The client uses the shared `resolve_tls_verify()` so it survives a TLS-inspecting corporate VPN.

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

**File:** `enrichment/classifier.py`

Records are classified as either `research_institution` or `company`. Classification determines which tiers and modes are available:

| Classification Source | Method | Example |
|----------------------|--------|---------|
| **ROR org types** (primary) | If ROR type is `education`, `healthcare`, `government`, `facility`, `nonprofit`, `archive`, or `other` -> `research_institution`. If `company` -> `company`. | ROR says "MIT" is type `education` -> `research_institution` |
| **Keyword heuristics** (fallback, when ROR misses) | `looks_like_research_institution()` checks for keywords: University, College, Hospital, Medical, Institute, Research, School, Academy, etc. | "Newman Regional Health" contains "Health" -> `research_institution` |
| **Default** | If neither method can classify -> `company` | "Acme Widget Co" -> `company` |

**Impact on pipeline routing:**
- `research_institution`: Eligible for Tier 2A (contact lookup) and Tier 2 Canonical
- `company`: Routes to **Tier 1 GLEIF/LEI registry lookup** first, then company canonicalization (LLM) if LEI misses, then Tier 2B if Name2 exists
- Both types: Eligible for Tier 1, Tier 2B, and Tier 3

---

## Domain Resolution

Each result carries a `domain` field — the registrable domain (e.g. `mit.edu`, `example.co.uk`) for the organization in Name1. It is populated from two sources, in priority order:

1. **ROR website** (primary). When Tier 1 matches an ROR record, its declared website link is parsed by `extract_domain()` and written to `domain`. This covers the vast majority of research institutions.
2. **`source_url` host** (fallback). When ROR didn't match but a successful Tier 2A or Tier 2B run produced a `source_url`, `finalise()` derives the domain from that URL's host. Tier 2A URLs are on-domain by construction (the contact's faculty page); Tier 2B URLs may or may not be on the institution's own site, so use this value cautiously when `source != "ROR"`.

If neither is available, `domain` is `null`. **Companies resolved via Tier 1 LEI carry `domain = null`** — GLEIF has no website field — unless a later web-search tier supplies one.

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
      "department_domain": "eecs.mit.edu",
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
      "website_url": "https://www.mit.edu",
      "flag_for_review": true,
      "flag_reason": "Partial match — confirm enriched Name 2",
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

> A company resolved by Tier 1 LEI would instead show `"record_type": "company"`, a populated `"lei_id"`, `"domain": null`, and `lei_hits_exact`/`lei_hits_fuzzy` incremented in the summary.

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

### POST /issues/compare

Takes two uploads (`original`, `enriched`), runs the issue detector over both, joins rows by record id, and returns an `.xlsx` issue-reduction report (summary + per-record + remaining-issues sheets).

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

Same clustering as `/api/dedup/cluster-block`, but accepts an `.xlsx`/`.xlsm` upload and returns an `.xlsx` with the cluster-assignment columns appended. It accepts the human SAP headers emitted by `/enrich/file` ("Customer", "Name 1", "Street 1", …) or the snake_case `DedupRow` field names. Multipart form field: `file`.

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
      "ror_id": "string|null",       // from Phase 1, if resolved
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
- A **signature** is a distinct `(norm_name1, norm_name2)` key. Each signature records: the list of `row_id`s that share it, the original (un-normalized) `name1`/`name2` from the first row, and the `ror_id` if any row in it carries one.
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

### LLM Call Details

| Aspect | Value |
|---|---|
| **Model** | GPT-5.4 (full, not mini/nano), AI Foundry deployment from env `AOAI_DEPLOYMENT_DEDUP` |
| **Client** | Reuses the Phase 1 AI Foundry client factory (`llm/openai_client.py::get_openai_client`) — no new client is written |
| **API version** | `AOAI_API_VERSION_DEDUP` (default `2025-04-01-preview`) — newer than Phase 1's default because reasoning models / `reasoning_effort` require it |
| **Reasoning effort** | `low` (`DEDUP_REASONING_EFFORT`). Temperature is **not** sent — reasoning models may ignore it |
| **Response format** | `{"type": "json_object"}`, parsed defensively (plain JSON, fenced ```json blocks, or embedded objects) |
| **Concurrency** | Bounded by `DEDUP_MAX_CONCURRENCY` (default 5) via a shared semaphore across all blocks |
| **Retries** | 429/5xx and connection/timeout errors retried with exponential backoff, max `DEDUP_MAX_RETRIES` (default 3) |
| **Resilience** | If the deployment rejects `reasoning_effort`, it is dropped and the call retried (the parameter is a tuning preference, not a correctness gate) |
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
| `lei_id` | `lei_id` *(included in the `/enrich` response for GLEIF-matched companies. Available to pass through, but `DedupRow`/`signatures.py` do not consume it yet — wiring dedup to converge on a shared LEI, like it does for `ror_id`, is a follow-up)* |

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
├── dedup/                        # Phase 2: deduplication adjudicator
│   ├── models.py                 # Pydantic schemas: DedupRow/Request/ResultRow/Summary/Response
│   ├── signatures.py             # STEP A: normalization, block derivation, signature collapsing
│   ├── prompts.py                # System + Mode A/B prompts, PROMPT_VERSION
│   ├── llm.py                    # DedupLLM (reuses get_openai_client), defensive JSON parsing
│   └── adjudicator.py            # STEP B/C: entity grouping, modes, clustering, telemetry
│
├── enrichment/                   # Core enrichment pipeline
│   ├── orchestrator.py           # Main pipeline controller (tier escalation, finalization)
│   ├── preprocess.py             # Deterministic cleanup: UC 6-12 (regex-based)
│   ├── classifier.py             # Record type classification (research_institution vs company)
│   ├── overflow_check.py         # UC 0: Name1+Name2 overflow detection
│   ├── tier1_ror.py              # Tier 1: ROR API client, scoring, child matching, acronym expansion
│   ├── tier1_lei.py              # Tier 1 (company): GLEIF/LEI registry client + verification guard
│   ├── tier2a_contact.py         # Tier 2A: Contact person lookup (Modes A & B)
│   ├── tier2b_dept.py            # Tier 2B: Department web search
│   ├── tier2_canonical.py        # Tier 2 Canonical: LLM-only department normalization
│   ├── lab_resolver.py           # UC 13: granular unit → parent department resolver
│   ├── tier3_llm.py              # Tier 3: Pure LLM inference (last resort)
│   ├── company_canonical.py      # Company name canonicalization via LLM
│   └── confidence.py             # Scoring rules, flag logic, status assignment
│
├── llm/                          # LLM integration layer
│   ├── openai_client.py          # AsyncAzureOpenAI wrapper (JSON mode, retries, api_version param)
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

Pattern-matching engine for UC 6-10. Runs before any network call. Returns `PreprocessResult` with cleaned fields and tracking of which use cases fired.

### `enrichment/tier1_ror.py` — ROR Client

Async ROR API client with hybrid lookup (affiliation + query), sophisticated name scoring with distinctive-token guards, legal-form suffix normalization, an identifier-acronym guard, local child matching, and organization type extraction for classification. Includes `_INSTITUTION_ACRONYMS` + an additive acronym-expanded affiliation retry (e.g. "HFT Stuttgart" → "Hochschule für Technik Stuttgart"). Uses `resolve_tls_verify()` for corporate-VPN TLS.

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

### `enrichment/company_canonical.py` — Company Canonicalization

Specializes in normalizing company names with geographic context. Used when Tier 1 misses and the record doesn't look like a research institution.

### `enrichment/overflow_check.py` — Overflow Detection

LLM-based check for Name1+Name2 being a single split organization name. Early-exit mechanism that prevents mis-enrichment of overflow records.

### `enrichment/confidence.py` — Scoring and Flags

Centralizes all flag-for-review logic and enrichment status assignment. Ensures consistent flagging rules across all tiers.

### `dedup/models.py` — Dedup Schemas

Pydantic v2 models for the Phase 2 endpoint: `DedupRow`, `DedupRequest`, `DedupResultRow`, `DedupSummary`, `DedupResponse`. `cluster_id` is a nullable integer; `routing` is a `Literal["cluster", "unique", "manual_review"]`.

### `dedup/signatures.py` — STEP A (Signature Collapsing)

Conservative normalization (`normalize_key`: lowercase, trim, collapse whitespace, strip punctuation, fold accents), block-id derivation (`derive_block_id`, a SHA-1 of the normalized `country|postal_code|street|house_no`), row grouping, and `build_signatures` which collapses rows into distinct `(norm_name1, norm_name2)` signatures with stable `s1, s2, …` ids. The normalized key is internal only and never reaches the LLM.

### `dedup/prompts.py` — Dedup Prompts

The shared system prompt (entity-resolution adjudicator with the two-level identity model), the Mode A partition prompt builder, the Mode B assignment prompt builder, and `PROMPT_VERSION = "p2-dedup-v3"`.

### `dedup/llm.py` — Dedup LLM Client

`DedupLLM` **reuses** `llm/openai_client.py::get_openai_client` (it does not write a new client) but calls with the dedup deployment, a newer API version, `reasoning_effort=low`, JSON response format, and bounded exponential-backoff retries. Never raises — returns a `DedupLLMResult` carrying raw text, token counts, latency, model version, and an `error` field. Includes `parse_json_object` (defensive JSON parsing of plain / fenced / embedded objects) and a `reasoning_effort`-rejection fallback.

### `dedup/adjudicator.py` — Block Algorithm

The core of Phase 2. `cluster_blocks` is the request entry point; `_process_block` runs STEP A → B → C per block; `_mode_a`/`_mode_b` implement the two grouping strategies; `_enforce_name2_split` is the deterministic Name 2 safety net; `_emit_rows` assigns clusters and routing; the global cluster-id remap and all telemetry logging live here.

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
| `SERPAPI_KEY` | *(none)* | SerpAPI key; if absent, DuckDuckGo is used |
| `MOCK_EXTERNAL_CALLS` | `false` | Use mock clients (no real API calls) |
| `ENV` | `production` | Set to `local` for development (enables dotenv loading) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Azure OpenAI / AI Foundry (Phase 1)

The same Azure credentials are used locally and in production — the only difference is delivery: a local `.env` file versus **Azure Application Settings** in the deployed Function App. The client is `AsyncAzureOpenAI`, constructed in `llm/openai_client.py::get_openai_client`; its TLS `verify` is resolved by `resolve_tls_verify()` (corporate CA bundle → certifi), the same helper now used by the ROR and GLEIF clients. See [TLS and Corporate VPN](#tls-and-corporate-vpn).

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
| `DEDUP_REASONING_EFFORT` | `low` | Reasoning effort for adjudication calls (reasoning models may ignore temperature, so temperature is not sent) |
| `SIG_PARTITION_THRESHOLD` | `12` | Distinct-signature count at/below which a block uses one partition call (Mode A); above it, incremental canonical assignment (Mode B) |
| `DEDUP_MAX_CONCURRENCY` | `5` | Max in-flight adjudicator LLM calls across all blocks in a request |
| `DEDUP_MAX_RETRIES` | `3` | Max attempts per adjudicator call (retries 429/5xx with exponential backoff) |

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
       └─ RETURN EnrichmentResult
  │
  ▼
Aggregate batch → EnrichmentSummary (counts by tier, status, type)
  │
  ▼
RETURN EnrichmentResponse (JSON)
```

---

## Changelog

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
- **Routing** — `cluster` / `unique` / `manual_review` per row, with `llm_flag` distinguishing LLM-merged clusters from pure identical-row collapses.
- **Resilience** — a single bad LLM call never fails a block; affected signatures route to `manual_review` and `summary.errors` is incremented.
- **Telemetry** — per-block, per-LLM-call, and per-request structured logs to the `mdm-pipeline-insights` Application Insights instance (reuses the existing logging integration; no new SDK).
- **Mock & tests** — `tests/mocks/dedup_mock.py` (conservative offline LLM) and `tests/test_dedup.py` (algorithm + route coverage, see [Testing](#testing)).
- **Diagnostics** — `GET /diag/dedup-llm` surfaces the real adjudicator LLM error, API version, and reasoning-effort state.

### Shared client change

- **`llm/openai_client.py::get_openai_client(api_version=None)`** was parameterized. It now resolves the API version as `api_version` arg → `AZURE_OPENAI_API_VERSION` env → `DEFAULT_AZURE_OPENAI_API_VERSION` (`2024-08-01-preview`). **Phase 1 behaviour is unchanged** (its callers pass nothing). The Phase 2 adjudicator passes a newer version (`AOAI_API_VERSION_DEDUP`, default `2025-04-01-preview`) because GPT-5.x reasoning models and the `reasoning_effort` parameter require it — this was the root cause of an early failure mode where every dedup row came back as `manual_review` with `errors > 0`.

### Corporate VPN / TLS fix

- **`get_openai_client` no longer hardcodes `verify=certifi.where()`.** A new `resolve_tls_verify()` helper honors a corporate CA bundle (`AZURE_OPENAI_CA_BUNDLE` / `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`) so a TLS-inspecting VPN no longer breaks LLM calls, supports `LLM_SSL_VERIFY=false` (insecure last resort), and falls back to certifi otherwise. The httpx client keeps `trust_env=True` (honors `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`) and exposes `LLM_HTTP_CONNECT_TIMEOUT` / `LLM_HTTP_TIMEOUT`. Fixes both phases (the dedup client reuses the factory). See [TLS and Corporate VPN](#tls-and-corporate-vpn).
- **ROR and GLEIF clients now reuse `resolve_tls_verify()` too.** Both previously hardcoded `verify=certifi.where()`, so on a TLS-inspecting VPN every ROR/GLEIF call failed the handshake — `ror_id`/`lei_id`/`domain` came back empty and every record fell through to the LLM. Now fixed.

### New environment variables

`AOAI_DEPLOYMENT_DEDUP`, `AOAI_API_VERSION_DEDUP`, `DEDUP_REASONING_EFFORT`, `SIG_PARTITION_THRESHOLD`, `DEDUP_MAX_CONCURRENCY`, `DEDUP_MAX_RETRIES`, `AZURE_OPENAI_API_VERSION`, and the Tier 1 LEI vars (`LEI_LOOKUP_ENABLED`, `GLEIF_API_BASE`, `GLEIF_TIMEOUT_SECONDS`, `LEI_NAME_MATCH_THRESHOLD`, `LEI_MAX_RETRIES`) — all documented in [Configuration and Environment Variables](#configuration-and-environment-variables) and `.env.example`.
