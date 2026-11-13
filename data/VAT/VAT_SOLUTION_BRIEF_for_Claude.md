# German VAT ID lookup: complete strategy, implementation and results brief

Version: 2026-11-11. Code state: branch `vat-hybrid-baseline` (last commit `ab203fe`, plus the
uncommitted 2026-11-10 invalid-number fix). Results: the full run over the DE e-invoicing export,
submitted as `DE_VAT_FINAL_einvoicing_ALL.xlsx`.

---

## 0. How to use this file (read first)

This file is written for an AI assistant (Claude) that will help **analyse the results and
rework the solution**. It is self-contained:

| Part | Content |
|---|---|
| §1–2 | The business problem, scope and the non-negotiable ground rules |
| §3 | The end-to-end pipeline, step by step, with the module that implements each step |
| §4 | Matching, decision and confidence rules in full detail (the core logic) |
| §5 | Output columns the reviewer sees, and what each value means |
| §6 | Infrastructure: throughput, jobs, caching, costs, configuration |
| §7 | The full run: batches, costs, result counts and distributions |
| §8 | Accuracy estimates and how they were made |
| §9 | Known error patterns, with counts and diagnosis |
| §10 | Limitations |
| §11 | Improvement ideas already analysed (with the reasoning and costs) and open decisions |
| §12 | Suggested questions / tasks for the assistant |
| Appendix A | Aggregate statistics per decision sheet (computed from the final workbook) |
| Appendix B | The prompts, verbatim (`vat/prompts.py` and the agent-pass prompt) |
| Appendix C | The full development log (`docs/VAT_VALIDATION.md`), history included |

**Precedence when sections disagree:** §1–§11 describe the current state. Appendix C is a
chronological log: a later dated section there overrides an earlier one, and §3–§5 here
override Appendix C.

**Data note:** this brief contains only aggregate numbers and public example companies. The
result workbooks themselves hold customer data. They must not be committed to git and should be
shared only as needed.

---

## 1. The problem and the scope

### 1.1 Business context
- **SAP customer master (KNA1)**, German customers (`Country/Region Key = DE`).
- **Field:** `VAT Registration No.` (KNA1-STCEG), the customer's German USt-IdNr.
- **Why it matters:** e-invoicing in Germany needs a correct VAT ID per business partner.
  An e-invoicing preparation export (`copy-DE_einvoicing_preparartion_2026-11-06.xlsb`) marked
  every partner whose VAT field is unusable as **`VAT = ISSUE`**.
- **Task:** for each ISSUE row, propose the correct VAT ID with evidence, or say why none was
  found. **Nothing is written to SAP.** All output is proposals for human review / controlled
  update.

### 1.2 Scope of the full run
- **7,358 ISSUE rows** from both sheets of the export:
  - **with email:** 3,171 rows;
  - **without email:** 4,187 rows.
- **6,772 distinct companies** (rows are de-duplicated to legal entities; see §3.3).
- One row = one SAP customer record in one company code / sales org, so the same customer can
  appear on several rows.

### 1.3 Input columns read
- **Identity and address:** `Customer`, `Country/Region Key`, `Name` (Name 1), `Name 2`–`Name 4`,
  `Street` (the second Street column, which holds street + number), `House Number`,
  `Postal Code`, `City`.
- **Optional, read when present:** `Email` / `Website` (a non-mailbox email domain is used as a
  website hint), `Commercial Register Number`, `Central Deletion Flag`.
- **VAT:** `VAT Registration No.` (in this export: `VAT Original`, plus
  `VAT Reference (Tax Number Long)`).
- Every other column is passed through unchanged.

### 1.4 Known data characteristics (German SAP master data)
- **Names cut at 35 characters** (SAP field length), often mid-word ("… Gmb", "Textilforschungsinst").
- **Name 1 is not always the legal entity.** It may hold a department, institute, plant,
  a contact person, a brand, an old name, or text like "Rechnungseingang".
- **Name 2–4** hold continuations, departments, contact persons or notes.
- **Abbreviations:** Inst., Fak., Abt., TU, LMU, RWTH, MPI, St., Gebr., u., f. …
- **Addresses:** street usually already contains the house number (22,290 of 25,801 records in
  the full DE export), "//" building notes, comma notes, old postcodes, typos in town names.
- **No standardised German company register data** is joined to the master data, so the tool
  must infer the legal entity from free text.

---

## 2. Ground rules (design constraints)

1. **A number must come from a page that shows it.** The model never supplies a number from
   memory. The page is fetched by our own code, and the number must be on it.
2. **Exact legal entity.** Parent, subsidiary, sister, branch-with-own-entity, predecessor and
   successor numbers are not the record's number, unless the rules say otherwise (a department
   or plant of an organisation uses the organisation's number).
3. **Germany's VIES does not disclose name or address** (`name: "---"`), and ignores the trader
   fields (tested live 2026-10-19). VIES therefore proves only that a number **exists**, never
   **whose** it is. The page's attribution is the only ownership link.
4. **Deterministic controls around the model.** The model searches, ranks and reads; code
   decides (gates, matching, decision, tiers).
5. **Nothing written to SAP.** `sap_update_eligible` = AUTO_ACCEPT **and** VIES VALID.
6. **Speed-ups must never change results** (the same queries, prompts, models and rules).
   Verified by cached replays: concurrency 1 vs 20 and job vs direct gave cell-identical output.
7. **Reproducibility:** every search, page and LLM answer is cached. A frozen replay
   (`CACHE_FROZEN`) reproduces a run at ~$0.
8. **Security:** no API keys in code or output; corporate CA bundle `certs/corp-ca-bundle.pem`,
   never `verify=False`.

---

## 3. The pipeline, end to end

```
upload (xlsx) ──► read & resolve columns ──► per row: normalise + format check of existing VAT
      │
      ▼
scope: rows whose VAT is missing / unusable (ISSUE)          (scope=all: every DE row, for evaluation)
      │
      ▼
entity key (Name 1 core + legal form + city) ──► one lookup per legal entity
      │
      ▼
HYBRID LOOKUP (per entity, vat/lookup_web.py)
  1 name preparation (name_prep + Name-2 classifier)
  2 search 1 (query built by code; Azure web search tool runs it)
  3 ranking of results (helper model) + heuristic order
  4 own-host recognition and page selection (web_pages)
  5 fetch pages (our code), host check, link follows, /impressum probe, blocked-host retry, archive
  6 reader model reads the legal-notice sections → number + quote + attributed name + address
  7 page checks (quote on page, number on page, extraction by pattern)
  8 search 2 (by the model) only if no own host found
  9 Peppol directory query (in parallel)
 10 fallbacks: no-VAT statement, aggregator site search, NOT_FOUND_NO_VAT_EXPECTED
      │
      ▼
entity match (vat/entity_match.py): name, legal form, address, source class, issue codes
      │
      ▼
decision (vat/lookup.py): AUTO_ACCEPT / MANUAL_REVIEW / CONFLICT / NOT_FOUND + review tier
      │
      ▼
invalid-number rejection (2026-11-10): stdnum check digit / length / foreign prefix → NOT_FOUND
      │
      ▼
AGENT PASS (vat/lookup_agent.py) on NOT_FOUND / LOOKUP_INCOMPLETE entities:
  model searches and opens pages itself (5 searches, 8 opens); our code re-fetches and re-judges
      │
      ▼
copy entity result to every record (own address match per record)
      │
      ▼
reviewer columns (vat/review_columns.py) + cost columns ──► workbook
      │
      ▼
VIES queue (vat/vies_retry.py): validity check of proposals, applied to the workbook
```

### 3.1 Entry points
| Entry point | Use |
|---|---|
| `POST /api/validate/vat` (JSON) | small batches |
| `POST /api/validate/vat/file` (XLSX) | one workbook, synchronous |
| `POST /api/validate/vat/file/jobs` (+ GET status / result, resume, DELETE) | large uploads, chunked, resumable (§6.2). **Used for the full run.** |
| `tools/vat_validate_xlsx.py` | CLI runner; drains the VIES queue |
| `tools/vat_lookup_compare.py` | evaluation: blanks known VAT IDs and measures recall |

All produce the same workbook via `vat.xlsx.validate_workbook` → `vat.validate.validate_rows`.

### 3.2 Normalisation and format check (`vat/normalize.py`)
- Remove spaces, dots, dashes; uppercase. `GR`→`EL`. A prefix is added from the country key only
  when the value has no leading letters (`PREFIX_FROM_COUNTRY_KEY`).
- python-stdnum (`stdnum.de.vat`: 9 digits, ISO 7064 Mod 11,10 check digit) decides
  `FORMAT_OK` / `FORMAT_ERROR:<reason>` (InvalidChecksum, InvalidLength …), `MISSING`, `NOT_EU`.
- Flags: `PREFIX_COUNTRY_MISMATCH` (e.g. NL number on a DE record), `NON_EU_PREFIX` (e.g. GB).

### 3.3 One lookup per legal entity (`lookup.entity_key`, `run_lookups`)
- **Key:** normalised Name 1 core + legal form (Name 1's, else Name 1–4's) + folded city.
  Name prep (§3.4) decides which entity Name 1–4 describe.
- The entity's result is copied to all its records (`for_record`); each record keeps its own
  address match against the same page, its own `DEPARTMENT` and `INPUT_INCOMPLETE` flags. The
  decision is the entity's.
- Exception: a number found only via the aggregator fallback is **not** shared to other records
  of the entity (`AGGREGATOR_NOT_SHARED`).
- Effect in the full DE export: 9,484 missing-VAT records → 7,202 keys; in this run 7,358 rows →
  6,772 companies.
- **Weakness:** the key includes the city, so one company with records in two towns is looked
  up twice and can get two different answers (see §9.7, "same company, different numbers").

### 3.4 Name preparation (`vat/name_prep.py`, `vat/name2_classifier.py`)
- **Search name:** Name 1 without "c/o …", "z. Hd. …", without a site word and what follows
  (Werk, Niederlassung, Zweigniederlassung, Standort, Betriebsstätte, Filiale), without a
  department word and what follows when ≥ 2 words remain, without a fragment left by the
  35-character cut; standalone "+" / "u." → "&".
- **Name 2 joined** when it carries the legal form ("& Co. KG", "GmbH") or continues a cut
  Name 1 (`CONTINUATION_TRUNCATED`: at the 35-character limit, a trailing connector like
  "für" / "des", or Name 2 supplying the institution noun). Never a department, brand or note.
- **Name-2 classifier** (helper model, 10 hand-labelled examples, threshold 0.8): classifies
  Name 2 as CONTINUATION / DEPARTMENT / SUB_ENTITY / OTHER; a confident non-continuation undoes
  the join. Evaluated on 936 records ($0.83).
- **Abbreviation expansion** (both SAP and page side): Inst.→Institut, Fak.→Fakultät,
  Abt./Abtlg.→Abteilung, Dez.→Dezernat, f.→für, u.→und, TU/TH/FH/HS/Uni, LMU, RWTH, MPI, MPG,
  St.→Sankt, Gebr., Ges., Nachf. …
- **Acronyms:** a leading acronym of the rest ("BSH Bosch und Siemens Hausgeräte") may be
  missing on the page; a Name 1 that is only the acronym of Name 1–4 matches the full name.
- **Not built:** detection of a **private person's name** in Name 1 (see §9.4, §11.3).

### 3.5 Search 1 (`vat/web_query.py`, hybrid mode)
- Query built by code: `<search name> <city>`, nothing else. Quoted only when the name is
  complete and ≥ 2 words. City in its usual spelling (`HATTERSHEIMAMMAIN` → Hattersheim,
  `MUNSTER` → Münster), never quoted.
- The model (`MDM_gpt-6-sol`, Azure Responses API with the web-search tool) only runs the query
  (one tool call) and lists results with titles, and for a directory entry the website it names.
  `QUERY_NOT_RUN_AS_GIVEN` is flagged when the model changed the query.
- **The SAP street and postcode are not in the query.** That is why namesakes in other towns
  are found (§9.1, §11.2).

### 3.6 Ranking (`VAT_WEB_RANKING=model`)
- The helper model (`gpt-5.4-mini`) orders search 1's results: at most 3, with
  `belongs_to_entity`, ≤ 250 output tokens, titles only (no snippets).
- A result whose host or path contains legal / impressum / imprint / legal-notice is put first
  among the entity's results. `heuristic_rank` orders the rest and is the fallback.
- On the baseline: model ranking found 35 vs 33 (50 records) and 46 vs 45 (53) for ~$0.0016
  per entity.

### 3.7 Own-host recognition and page selection (`vat/web_pages.py`)
- **Only the entity's own hosts are fetched.** A host is OWN when:
  - a distinctive name word (≥ 4 letters; not GmbH, Deutschland, International, Group,
    Institut, Universität, Hochschule, Klinikum …) is in the domain;
  - or the name's initials (3+ letters; compound words split: "Universitätsmedizin
    Göttingen" → umg);
  - or a domain prefix with 6+ letters in common;
  - the city identifies a host only beside the record's own type prefix (uni-, tu-, hs-, uk-)
    or for a Stadt / Gemeinde / Kreis record;
  - or a directory entry names it as the website;
  - or institutional parent domains (`uni-*.de`, `*.mpg.de`, `*.fraunhofer.de`, `kit.edu` …)
    for a department (kind INSTITUTION).
- **Unverified hosts** (CANDIDATE: the ranking model vouches for it, `domain_hint`; VERIFY:
  another host's Impressum) count as own only once an Impressum-type page there names the entity.
- **Never fetched:** directories (pointers only), marketplaces, social networks, benefit portals,
  tender aggregators, imprint generators, tax-advice sites.
- **Page order per host:** Impressum / legal paths first, then tax, procurement / AGB, contact /
  about; the German page before its `/en/` twin. One page per host.
- **Budget per entity:** 2 searches, `VAT_WEB_MAX_FETCHES` 4 fetches (6 when search 1 shows ≥ 3
  own hosts), 2 link follows, 1 probe (`/impressum`, else `/imprint`), 1 Peppol query, 1 archive
  snapshot. PDFs on an own host: ≤ 2 MB, ≤ 20 pages.

### 3.8 Fetching, host check, follows, blocked hosts
- **Host check** (`host_check`): a host is rejected only when its page names the entity with
  another legal form, or, on a legal / contact / tax page, names neither the entity nor its town.
  **Another town alone never rejects** (head offices); it sends the number to review.
- **Link following:** on an own / parent host whose page has no VAT ID, follow the next
  Impressum / Legal notice / Steuern / Lieferanten / Einkauf / AGB link, else `/impressum`.
- **Probe:** only on an OWN host that answered and showed no Impressum-type page.
- **Blocked host** (401 / 403 / 429 / 5xx / timeout): one retry with a browser User-Agent, then
  the budget moves to the next own host; an Internet Archive snapshot only when every own host
  is blocked. Result `NOT_FOUND:OWN_SITE_BLOCKED` → reviewer reason `WEBSITE_BLOCKED_US`.
- **Script-only pages** (JavaScript renders the Impressum; no headless browser):
  `WEBSITE_NOT_READABLE`.
- **Text cleaning** (`entity_match.clean_text`): soft hyphens, zero-width spaces, mojibake
  ("UniversitÃ¤t") repaired before every comparison.

### 3.9 Reader and extraction
- The reader (`MDM_gpt-6-sol`, no tools) sees the page head plus ±1,500 characters around every
  USt-IdNr / VAT / Impressum / "Angaben gemäß" mention (≤ 12,000 characters per page), plus every
  `DE` + 9-digit string on the page.
- It returns the number, the evidence quote, the company the number is attributed to, and the
  published address. When it cannot tell which company a number belongs to (several companies
  on one Impressum), it lists all numbers with their companies: the best name match is proposed
  with `AMBIGUOUS_SAME_NAME:<all numbers>` → **CONFLICT**.
- **Pattern extraction** (`vat_candidates`: `\bDE[ ]?[0-9]{9}\b`, widened to grouped digits,
  excluding the W-IdNr. suffix `-NNNNN`): used only when the page carries exactly one number
  (`EXTRACTED_BY_PATTERN`, review). Several numbers and no reader answer → CONFLICT.
- **`number_holder`:** when code picks a number, the attributed name is the first
  "<name> <legal form>" in the legal notice before it (so LEGAL_FORM_DIFFERS / parent can fire).
- **Quote check:** the quote and the digits must be on the page as fetched by our code, else
  `EVIDENCE_NOT_VERIFIED` (blocking).
- Azure content filter stop: one retry, then `LOOKUP_INCOMPLETE:CONTENT_FILTER`.

### 3.10 Search 2, Peppol, fallbacks
- **Search 2 (model-written query):** only when search 1 showed no own, directory-linked or
  parent host with a real Impressum-type page. Uses the current / full legal name, an acronym's
  expansion, a department's parent, plus Impressum / USt-IdNr terms. Also returns a
  `domain_hint` (model knowledge; fetched as CANDIDATE).
- **Peppol Directory** (`vat/peppol.py`), for every entity, in parallel: scheme 9930 = DE VAT
  ID; scheme 0204 Leitweg-ID whose address part holds a VAT ID is read too. Source class A.
- **No-VAT statement:** the entity's own page says it has no USt-IdNr (Kleinunternehmer etc.)
  → `NOT_FOUND_NO_VAT_EXPECTED:STATED` → `PAGE_SAYS_NO_VAT_ID`.
- **Defunct statement** on the page → `PAGE_SAYS_COMPANY_CLOSED`.
- **Aggregator fallback** (`VAT_AGGREGATOR_FALLBACK`): one site search on
  unternehmensverzeichnis.org, ≤ 2 entry fetches; used when the entry heading matches EXACT, or
  PARTIAL with the first distinctive word. Always MANUAL_REVIEW with `SECONDARY_SOURCE`
  (reviewer flag `FROM_DIRECTORY`). Former-name entries → `SUCCESSOR_CANDIDATE`.
- **Public body inferred:** a school / authority and nothing found →
  `NOT_FOUND_NO_VAT_EXPECTED:INFERRED` → `PUBLIC_BODY_NO_VAT` (an assumption).
- **Deleted records** (Central Deletion Flag): one archive snapshot of a 404 Impressum.
- **Grounded fallback** (SerpAPI) exists but is **off**: in a test it found 12 numbers, 10 of
  them wrong (unrelated sites' Impressum pages).

### 3.11 Invalid-number rejection (2026-11-10, `lookup.reject_invalid_number`)
- Applied in `validate_rows` (and inside the agent pass), not in `run_lookups`.
- A candidate is rejected when stdnum rejects it for the record's country (wrong check digit,
  wrong length), or it has another country's prefix, or a non-EU prefix.
- Result: `NOT_FOUND:INVALID_NUMBER`, flag `INVALID_NUMBER:<number>:<why>`; reviewer reason
  `INVALID_VAT_NUMBER`; `VAT_Why` names the rejected number and why ("wrong check digit",
  "wrong length", "another country's number", "not an EU VAT ID"). The source URL is kept.
- The rejected row is agent-eligible (the agent pass looks again); an invalid number from the
  agent never replaces the lookup.
- In the full run these 22 rows were moved by script (the fix came after the run): 7 foreign
  parent numbers, 3 placeholders (`DE123456789`), 12 typos / wrong check digits on the source
  page. Tests: `tests/test_vat_invalid_number.py` (12 tests). The evaluation tool keeps the old
  behaviour (`reject_invalid=False`).

### 3.12 Agent pass (Step AG, `vat/lookup_agent.py`)
- **Who:** entities with NOT_FOUND (any reason) or LOOKUP_INCOMPLETE. Never NO_VAT_EXPECTED
  rows, never candidates.
- **How:** the model (`MDM_gpt-6-sol`, reasoning effort medium, ≤ 12,000 output tokens)
  searches and opens pages itself: ≤ 5 searches, ≤ 8 opens. It gets the full record and the
  pipeline's context (Name-2 class, search name, segment, the A–F "no number" class and why,
  hosts already tried and why they failed, sibling records' VAT IDs as context only), and is
  told to use its own knowledge of the organisation (domain, renamed, parent, successor) to plan.
  Output JSON: `vat_id, source_url, evidence_quote, attributed_to, published_address,
  confidence, relation (same_entity|renamed|successor|parent|not_found), pages_opened, notes`.
- **Gates after the agent:** our code fetches the cited URL itself, runs the host check, the
  reader, address match, source class and the decision.
  - Host not accepted → judged on the page as fetched, `AGENT_HOST_NOT_ACCEPTED`, never AUTO.
  - Page not fetchable or number not on it → `EVIDENCE_NOT_VERIFIED` (review, LOW).
  - Renamed / successor with a different page name → `SUCCESSOR_CANDIDATE` (review).
- **Budget:** `VAT_AGENT_BUDGET_USD` (8 per chunk by default), concurrency 10. Over the cap:
  `AGENT_SKIPPED_BUDGET` → `Agent_Pass = Skipped (cost cap)`.
- **Results in the full run:** Found on 41 auto-accept, 342 review, 12 conflict and 2 not-found
  rows; "Tried, nothing found" on 1,612 not-found rows; skipped (cost cap) on 19.

### 3.13 VIES (`vat/vies_client.py`, `vat/vies_retry.py`)
- REST API `check-vat-number`; concurrency 1, ≥ 500 ms between calls, 3 attempts; only VALID /
  INVALID answers are cached; DE available 05:00–23:00.
- **Out of the lookup path** (`VAT_VIES_IN_LOOKUP=false`): proposals leave the lookup as
  `PENDING` and are queued; a queue drain writes the answers into the workbook:
  VALID → VERIFIED / tier recomputed; INVALID → MANUAL_REVIEW, LOW, `VIES_INVALID`; no answer →
  retried hourly 24 h, then daily for 7 days.
- **In the full run `vies_after` was false in every job, so all proposals are "Pending"**
  (CONFLICT rows are "Not checked"; they are not queued). Running VIES is free and is the first
  recommended next step: ~3,701 distinct numbers.

---

## 4. Matching, decision and confidence rules (the core)

### 4.1 Address match (`entity_match.address_match`) – four values
| Value | Meaning | Reviewer label |
|---|---|---|
| `EXACT` | street + house number with postcode **or** town match | `SAME_ADDRESS` |
| `SAME_TOWN` | town (city or postcode) matches; street / number differ or missing | `OTHER_STREET_SAME_TOWN` |
| `DIFFERENT_TOWN` | the page states an address in another town | `OTHER_TOWN` |
| `NO_ADDRESS` | no address on the page | `NO_ADDRESS_ON_PAGE` |

- A city counts only inside a `<postcode> <town>` block when the page has one (so
  "Amtsgericht Stuttgart" is not read as the company's town).
- Street folding: Hauptstr. 5 = Hauptstrasse 5 = Haupt-Straße 5; linking "s" ignored; house
  number not added twice; "//" notes and comma notes dropped; English town names (Munich,
  Cologne …) = German.
- **Why SAME_TOWN may auto-accept:** in the full DE export, records sharing name, legal form and
  town carry different VAT IDs in 2.9 % of groups vs 1.9 % for same street, so the town is
  nearly as discriminating as the street.

### 4.2 Name and legal form (`entity_match.name_match`)
- **Levels:** `EXACT`, `PARTIAL`, `MISMATCH` (plus flags).
- Long and short legal forms folded (Aktiengesellschaft = AG, GmbH variants, gGmbH, "GmbH + Co.
  KG" = "GmbH & Co. KG", e.V., SE & Co. KGaA, AöR, KöR, e.K., e.Kfr., PartG(mbB)).
- SAP's 35-character cut read back ("… Gmb" → GmbH; "… GmbH & C" → GmbH & Co. KG).
- **Legal form on both sides and different** → `PARTIAL`, `LEGAL_FORM_DIFFERS:<sap>/<page>`
  (blocking). GmbH vs GmbH & Co. KG is the key case: separate companies, often at one address.
- **Form missing in SAP** → `EXACT`, `LEGAL_FORM_MISSING_IN_SAP` (not blocking), unless the page
  shows a second entity of that name with another form → `SISTER_ENTITY_ON_PAGE` (blocking).
- **Site words stripped** → `EXACT` + `BRANCH` (not blocking: a German branch has no own VAT ID).
- **Department words in Name 2–4** (Institut, Lehrstuhl, Fakultät, Klinik für, Abteilung,
  Zentrum, Labor, Professur, Dezernat …) don't change the match; the record gets `DEPARTMENT`.
- **NAME_CONTAINED** (2026-11-07): every word of SAP's name core appears, in order, in the page
  name (e.g. "LMU Klinikum" in "LMU Klinikum München, AöR"). Counts as matched for AUTO_ACCEPT
  only with source class A, address EXACT / SAME_TOWN, no other VAT IDs on the page, not
  extracted by pattern. Extra page words that name another company (Holding, Group, Gruppe,
  Konzern, Verwaltung, Beteiligung, Management) disqualify. Otherwise it's `NAME_PARTIAL`.
- **GENERIC_NAME / NAME_ABBREVIATED** (common name or initials match): AUTO only with address
  EXACT; SAME_TOWN → review (`ADDRESS_NOT_EXACT`). Reviewer flag `NAME_SHARED_BY_OTHERS`.
- **In liquidation** ("i.L.", "in Abwicklung"): `IN_LIQUIDATION`, blocking.

### 4.3 Source class (`VAT_Found_Source_Class`)
| Class | Source types | Meaning |
|---|---|---|
| A | `COMPANY_IMPRESSUM`, `COMPANY_WEBSITE`, `PEPPOL_DIRECTORY` | the entity itself (own domain incl. AGB, supplier info, PDFs; its Peppol entry) |
| B | `PROCUREMENT_PROFILE`, `OFFICIAL_FILING` | Vergabeportal buyer profile; Bundesanzeiger / Unternehmensregister filing |
| C | `AGGREGATOR`, `WEB_ARCHIVE` | directories (northdata, firmenwissen …), unrelated pages; archive snapshots |

### 4.4 Issue codes (`VAT_Found_Issues`; blocking ones force review)
| Group | Codes | Blocking |
|---|---|---|
| Address | `ADDRESS_DIFFERS` (SAME_TOWN), `ADDRESS_MISMATCH` (DIFFERENT_TOWN), `ADDRESS_NOT_ON_PAGE` | last two |
| Name / entity | `LEGAL_FORM_DIFFERS`, `LEGAL_FORM_MISSING_IN_SAP`, `BRANCH`, `NAME_PARTIAL`, `NAME_CONTAINED`, `SISTER_ENTITY_ON_PAGE`, `DEPARTMENT`, `IN_LIQUIDATION` | `LEGAL_FORM_DIFFERS`, `NAME_PARTIAL`, `SISTER_ENTITY_ON_PAGE`, `IN_LIQUIDATION` |
| Source / verification | `SECONDARY_SOURCE`, `OTHER_VAT_IDS_ON_PAGE`, `EXTRACTED_BY_PATTERN`, `VIES_INVALID`, `VIES_UNAVAILABLE`, `VIES_PENDING`, `CONFLICTING_SOURCES`, `REGISTER_MISMATCH`, `EVIDENCE_NOT_VERIFIED`, `ADDRESS_NOT_EXACT` | all except `OTHER_VAT_IDS_ON_PAGE`, `EXTRACTED_BY_PATTERN`, `VIES_UNAVAILABLE`, `VIES_PENDING` |
| Entity relation | `SUB_ENTITY`, `PARENT_ENTITY`, `PARENT_ENTITY_CANDIDATE`, `SUCCESSOR_CANDIDATE` | last two |
| SAP record | `SAP_VAT_OUTDATED` (scope all only) | no |

Invariant (tested): every MANUAL_REVIEW / CONFLICT row has ≥ 1 blocking code; no AUTO_ACCEPT row
has one.

### 4.5 Decision (`lookup.decide`)
- **NOT_FOUND:** no candidate (NOT_FOUND, NO_VAT_EXPECTED, LOOKUP_INCOMPLETE, invalid number).
- **CONFLICT:** `CONFLICTING_SOURCES` or `AMBIGUOUS_SAME_NAME` (several numbers on the page and
  the reader could not attribute one, or another primary source names the exact entity with a
  different number).
- **MANUAL_REVIEW:** any other blocking issue.
- **AUTO_ACCEPT** needs **all** of:
  1. source class A or B;
  2. name EXACT (branch fine) or NAME_CONTAINED under its conditions;
  3. address EXACT or SAME_TOWN (EXACT only, for generic / abbreviated names);
  4. VIES VALID or not yet answered (PENDING);
  5. the number on a page our own code fetched, with a verified quote;
  6. none of REGISTER_MISMATCH, CONFLICTING_SOURCES, SISTER_ENTITY_ON_PAGE, IN_LIQUIDATION.

### 4.6 Review tier (`VAT_Found_Confidence` of a MANUAL_REVIEW row), checked in order
| Tier | When |
|---|---|
| LOW | NAME_PARTIAL with ADDRESS_MISMATCH, SECONDARY_SOURCE or OTHER_VAT_IDS_ON_PAGE; EXTRACTED_BY_PATTERN without a quote; VIES_INVALID; EVIDENCE_NOT_VERIFIED |
| MEDIUM | any of SECONDARY_SOURCE, LEGAL_FORM_DIFFERS, PARENT_ENTITY_CANDIDATE, SUCCESSOR_CANDIDATE, ADDRESS_NOT_ON_PAGE, VIES_UNAVAILABLE, **VIES_PENDING** |
| HIGH | name EXACT, address EXACT / SAME_TOWN, class A / B |
| MEDIUM | anything else |

Because every proposal is VIES_PENDING in this run, **no Review row is HIGH**: Review is 1,471
MEDIUM + 793 LOW. After a VIES run, tiers are recomputed and some MEDIUM rows become HIGH.

### 4.7 Match score
`VAT_Found_Match_Score` (0–100): name 30, street 25, postcode 15, city 10, domain 10, register 10;
missing elements leave the denominator. **Informational only; no rule uses it.** (It could be
used for sorting the review queue.)

---

## 5. Output: the reviewer's workbook

### 5.1 Final submission file
`data/VAT/DE_VAT_FINAL_einvoicing_ALL.xlsx`, 8 sheets: {With email, Without email} ×
{Auto-accept, Review, Conflict, Not found}. All input columns, then the reviewer columns, then 7
cost columns and `VAT_Found_vs_SAP`. 148 columns (with email) / 146 (without email).

### 5.2 Reviewer columns (`vat/review_columns.py`, `VAT_OUTPUT_COLUMNS=review`)
| Column | Values / meaning |
|---|---|
| `VAT_Proposed` | the number found |
| `VAT_Decision` | Auto-accept / Review / Conflict / Not found |
| `VAT_Found_Confidence` | VERIFIED / HIGH / MEDIUM / LOW_CONFIDENCE / CONFLICT / NOT_FOUND |
| `VAT_Found_Match_Score` | 0–100, informational |
| `VAT_Why` | 1–2 template sentences explaining the decision |
| `Found_Name` | the company the page gives the number to |
| `Found_Address` | the address on the page |
| `Name_Check` | `SAME_NAME` / `SAP_NAME_PLUS_MORE` (NAME_CONTAINED) / `SIMILAR_NAME` (partial) / `DIFFERENT_NAME` |
| `Address_Check` | `SAME_ADDRESS` / `OTHER_STREET_SAME_TOWN` / `OTHER_TOWN` / `NO_ADDRESS_ON_PAGE` |
| `Issue_Flags` | see 5.3 |
| `Not_Found_Reason` | see 5.4 |
| `Agent_Pass` | Found / Tried, nothing found / Skipped (cost cap) / blank |
| `VIES` | Valid / Invalid / Pending / Unavailable / Not checked |
| `VAT_Found_Source_URL`, `_Source_Type`, `_Source_Class`, `_Page_Title`, `_Evidence`, `_Agent_Version` | provenance |

### 5.3 `Issue_Flags` (each one a question for the reviewer)
| Flag | From | Reviewer question |
|---|---|---|
| `IN_LIQUIDATION` | page name says i.L. | still trading? |
| `SEVERAL_NUMBERS_ON_PAGE` | other numbers on the page, AMBIGUOUS_* | is the proposed one this company's? |
| `OTHER_SOURCE_OTHER_NUMBER` | CONFLICTING_SOURCES | which source is right? |
| `FROM_DIRECTORY` | source class C (not archive) | confirm on the company's own site |
| `FROM_OLD_ARCHIVE` | archive snapshot | still current? |
| `NUMBER_NOT_QUOTED` | no quote, EXTRACTED_BY_PATTERN, EVIDENCE_NOT_VERIFIED | find the number on the page |
| `MAYBE_PARENT_COMPANY` | PARENT_ENTITY(_CANDIDATE) | does the customer have its own number? |
| `MAYBE_RENAMED_OR_SUCCESSOR` | SUCCESSOR_CANDIDATE | still the same legal entity? |
| `SISTER_COMPANY_ON_PAGE` | SISTER_ENTITY_ON_PAGE | the right group company? |
| `LEGAL_FORM_DIFFERS` | LEGAL_FORM_DIFFERS | GmbH vs AG / e.K.: which is the customer? |
| `REGISTER_NO_DIFFERS` | REGISTER_MISMATCH | another company? |
| `DEPARTMENT_OR_UNIT` | DEPARTMENT, SUB_ENTITY | the organisation's central number is expected |
| `SITE_OR_PLANT` | BRANCH | the head office's number is expected |
| `NAME_SHARED_BY_OTHERS` | GENERIC_NAME, NAME_ABBREVIATED | street / HRB must match too |

### 5.4 `Not_Found_Reason`
| Value | From |
|---|---|
| `NO_WEBSITE_FOUND` | no own host found |
| `WEBSITE_HAS_NO_VAT_ID` | own site read, no number |
| `WEBSITE_NOT_READABLE` | script-only page |
| `WEBSITE_BLOCKED_US` | 403 / 5xx / timeout on all own hosts |
| `ONLY_PARENT_SITE_FOUND` | only a parent's site answered |
| `PAGE_SAYS_COMPANY_CLOSED` | defunct statement |
| `PAGE_SAYS_NO_VAT_ID` | no-VAT statement on own page |
| `PUBLIC_BODY_NO_VAT` | inferred: public body, nothing found |
| `INVALID_VAT_NUMBER` | number failed stdnum / foreign prefix (§3.11) |
| `NO_NUMBER_FOUND` | other |
| `MODEL_SERVICE_FAILED`, `MODEL_ANSWER_UNREADABLE`, `CONTENT_FILTER_STOPPED`, `NOT_IN_REPLAY`, `COST_CAP_REACHED` | search incomplete |

### 5.5 Extra analysis file
`data/VAT/DE_VAT_FINAL_different_name_other_town.xlsx`: the 689 Review + Conflict rows with
`Name_Check` ∈ {SIMILAR_NAME, DIFFERENT_NAME} and `Address_Check = OTHER_TOWN` (Conflict first),
plus two computed columns:
- `Check_Postcode_Region`: Same region (first 2 postcode digits equal) 90 / Other region 545 /
  No postcode to compare 54;
- `Check_Email_Domain`: No email 438 / Differs from source site 182 / Matches source site 55 /
  Generic mailbox 14.

---

## 6. Infrastructure

### 6.1 Models and prices (`config/vat_prices.yaml`)
| Deployment | Use | USD per 1M tokens input / cached / output |
|---|---|---|
| `MDM_gpt-6-sol` | search 1/2, reader, agent pass | 2.00 / 0.10 / 10.00 |
| `MDM-Apoorva-gpt-5.4-mini` (`VAT_HELPER_MODEL`) | ranking, Name-2 classifier | 0.75 / 0.075 / 4.50 |

Web search tool: $0.014 per call. Own fetches, VIES, Peppol, Wayback: free.

### 6.2 Throughput and jobs
- Per company ~24 s (57 % web search, 10 % reading, 33 % downloads / ranking / Peppol).
- `vat/rate_limit.py`: one token bucket per deployment (TPM and RPM, 85 % of the quota read from
  Azure's headers); 429 / 5xx retried up to 6 times honouring Retry-After; a 429 pauses the whole
  deployment.
- 20 companies at once, Peppol in parallel, first page per host prefetched (≤ 20 downloads).
- **Jobs** (`vat/jobs.py`): upload → 202; chunks of ~200 rows cut between linked row groups;
  each chunk saved to `logs/vat_jobs/<id>/chunks/NNN.json`; lease + timer trigger resume;
  budget checked between chunks.
- **Assembly step:** after the chunks, `validate_workbook` runs on the whole upload "from cache"
  to produce the result. **Known defect:** in each large batch this changed 15–84 rows vs the
  chunk results (cache gaps from Windows MAX_PATH on long cache paths, plus new live Azure
  failures). The submitted file restores the chunk ("run") values except where the run itself
  had an Azure failure and the rebuild a proper answer. Fix pending: assemble from the chunk
  JSONs instead of re-running, and use a short cache directory.
- **Observed speed:** ~4 companies per minute (Azure quota-bound); full run ~29.9 h.

### 6.3 Caching
- Every external answer cached under a key of its own request (prompt text and version are part
  of the key). `CACHE_FROZEN` replays only.
- `DiskCache.set` silently skips paths > 260 characters on Windows; `vat/cache_guard.py` detects
  and counts `cache_write_failures`. Use a short `EVIDENCE_CACHE_DIR`.

### 6.4 Key configuration (`.env.example` defaults)
| Setting | Default |
|---|---|
| `VAT_WEB_QUERY_MODE` | hybrid |
| `VAT_WEB_MAX_SEARCHES` / `_MAX_FETCHES` / `_MAX_FETCHES_MANY_HOSTS` / `_MANY_OWN_HOSTS` / `_MAX_FOLLOWS` | 2 / 4 / 6 / 3 / 2 |
| `VAT_WEB_SECTION_WINDOW` / `VAT_WEB_SECTIONS_CHARS` | 1500 / 12000 |
| `VAT_WEB_BUDGET_USD` | 0 (none) |
| `VAT_PEPPOL_ENABLED` | true |
| `VAT_GROUNDED_FALLBACK` | false |
| `VAT_VIES_IN_LOOKUP` | false |
| `VAT_AGENT_PASS` | false in .env.example (was on for the full run) |
| `VAT_AGENT_BUDGET_USD` / `VAT_AGENT_CONCURRENCY` | 8 / 10 |
| `VAT_LOOKUP_CONCURRENCY` / `PAGE_FETCH_MAX_CONCURRENCY` | 20 / 20 |
| `VAT_AZURE_QUOTA_SHARE` / `VAT_AZURE_MAX_RETRIES` | 0.85 / 6 |
| `VAT_JOB_CHUNK_ROWS` / `VAT_JOB_LEASE_SECONDS` | 200 / 900 |
| `VAT_OUTPUT_COLUMNS` / `VAT_LOG_COLUMNS` | review / false |
| `VIES_MAX_CONCURRENCY` / `VIES_MIN_INTERVAL_MS` / `VIES_MAX_ATTEMPTS` | 1 / 500 / 3 |

### 6.5 Code map
| Module | Lines | Role |
|---|---|---|
| `vat/lookup_web.py` | 1,851 | hybrid lookup orchestration per entity |
| `vat/lookup_grounded.py` | 1,023 | page fetch, reader, `_judge` page check |
| `vat/entity_match.py` | 951 | name / legal form / address / source / issue codes |
| `vat/web_pages.py` | 819 | host kinds, page selection, host check, number holder |
| `vat/lookup.py` | 768 | data types, decision, tiers, entity key, run_lookups, invalid number |
| `vat/llm_cache.py` | 575 | Azure calls, cache, retries |
| `vat/validate.py` | 552 | row logic, scope, agent pass wiring, summary |
| `vat/prompts.py` | 500 | all prompts (Appendix B) |
| `vat/jobs.py` | 496 | job endpoints worker |
| `vat/name_prep.py` | 493 | search name, Name-2 joins |
| `vat/review_columns.py` | 473 | reviewer columns, VAT_Why |
| `vat/lookup_agent.py` | 444 | agent pass |
| `vat/lookup_log.py` | 421 | research log columns |
| `vat/vies_client.py`, `vat/vies_retry.py` | 407, 264 | VIES + queue |
| `vat/costs.py`, `vat/rate_limit.py`, `vat/peppol.py`, `vat/web_query.py` | | costs, limiter, Peppol, search-1 query |
| `vat/name2_classifier.py`, `vat/no_number_class.py`, `vat/normalize.py`, `vat/name_match.py`, `vat/xlsx.py` | | helpers |

Tests: `tests/test_vat_*.py` (1,077 VAT tests passing at 2026-11-10).

---

## 7. The full run: batches, costs, results

### 7.1 Batches
| Batch | Rows | Distinct companies | Azure cost (USD) | Run time (h) | Notes |
|---|---|---|---|---|---|
| Sample 100 (both sheets) | 100 | 99 | 8.97 | 1.0 | ran on an earlier code version; re-processed from cache at $0 with the final rules (one row kept its original value) |
| Sample 500 (with email) | 500 | 481 | 44.34 | 4.0 | |
| Rest, with email | 2,628 | 2,427 | 220.10 | 10.2 | |
| Batch 2,000, without email | 2,000 | 1,830 | 171.99 | 6.9 | assembly changed rows; restored to run values |
| Rest, without email | 2,130 | 1,935 | 182.12 | 7.8 | ~30 companies hit Azure "model service failed" mid-run; 35 rows took the rebuild's answer, 49 restored to run values |
| **Total** | **7,358** | **6,772** | **627.52** | **29.9** | ~$0.093 per company, ~$0.11 per proposed number |

Job estimates were ~30 % below actual cost (e.g. $122 estimated vs $182 actual).

### 7.2 Result counts (final file)
| Decision | With email | Without email | Total | Share |
|---|---|---|---|---|
| Auto-accept | 1,124 | 1,305 | 2,429 | 33 % |
| Review | 920 | 1,344 | 2,264 | 31 % |
| Conflict | 324 | 485 | 809 | 11 % |
| Not found | 803 | 1,053 | 1,856 | 25 % |
| **Total** | **3,171** | **4,187** | **7,358** | 100 % |

- Proposed numbers: 5,502 rows (75 %), ~3,701 distinct numbers.
- With and without email performed the same within a few points.

### 7.3 Headline distributions (full tables in Appendix A)
- **Auto-accept:** 96.5 % from a company Impressum; Name_Check SAME_NAME 84 % / SAP_NAME_PLUS_MORE
  16 %; Address SAME_ADDRESS 57 % / OTHER_STREET_SAME_TOWN 43 %; 25.6 % are departments or units
  (universities, hospitals, cities).
- **Review:** source AGGREGATOR (directory) 22.8 %; Address OTHER_TOWN 42.7 %; Name SIMILAR 37 %,
  DIFFERENT 11.5 %; agent pass found 15 % of them; LOW tier 35 %.
- **Conflict:** 99.4 % SEVERAL_NUMBERS_ON_PAGE, 90 % NUMBER_NOT_QUOTED, 27 % LEGAL_FORM_DIFFERS,
  19 % MAYBE_PARENT_COMPANY; Address OTHER_TOWN 55 %; Name DIFFERENT 35.5 %.
- **Not found:** NO_WEBSITE_FOUND 47 %, WEBSITE_HAS_NO_VAT_ID 19 %, WEBSITE_NOT_READABLE 10 %,
  PUBLIC_BODY_NO_VAT 10 %, WEBSITE_BLOCKED_US 10 %, COMPANY_CLOSED 1.6 %, INVALID_VAT_NUMBER
  1.2 %, technical failures 4 rows.

---

## 8. Accuracy estimates

**There is no ground truth.** Numbers come from hand-checked samples (≈ 500 rows overall),
comparing the name and address on the source page with SAP. Read them as ranges.

| Decision | Hand-checked | Result | Estimate |
|---|---|---|---|
| Auto-accept | 85 random + 57 highest-risk | 0 wrong, 1 doubtful | ~99 % correct |
| Review | 95 | 15 wrong, 9 doubtful | ~70–80 % correct (per batch 73–85 %) |
| Conflict | 65 | ~15 right, 10 doubtful | ~25–40 % correct |
| Not found | 105 | ~35 % looked like findable companies | ~60–70 % correctly empty |
| **Overall** | | | **77–81 % of rows have the correct outcome** (right number, or correctly empty); ~5,670–5,960 rows |

Earlier evidence from development:
- Fixture of 15 large companies (2026-10-20): the strict attribution gate produced **0 wrong
  verified** numbers; LLM-only answers from memory were wrong 7 of 13 times, all failing the
  check digit, 6 of them stated with "high" confidence → the model's self-reported confidence
  carries no signal; never take a number from memory.
- AD 50 / FINAL 50 (records with a known SAP number, blanked): all AUTO_ACCEPTs equal SAP's
  number (one case where SAP itself was wrong).

---

## 9. Known error patterns (diagnosis)

### 9.1 Namesake in another town (largest error source)
- **689 rows** in Review + Conflict with Name_Check SIMILAR / DIFFERENT and Address OTHER_TOWN
  (301 Review, 388 Conflict). Plus 512 Review rows with SAME_NAME + OTHER_TOWN.
- Cause: search 1 is `name + city`; when the real company has no findable website, the search
  returns a same- or similar-named company elsewhere, and "another town alone never rejects"
  (a deliberate rule, to keep head offices).
- Typical wrong: small firms and sole traders sharing a common name; old names of companies that
  no longer exist (e.g. Deutsche BP matched to Deutsche Bank; Hoechst AG).
- Typical right despite OTHER_TOWN: head office elsewhere (Siemens, Bosch), renamed companies
  (HeidelbergCement → Heidelberg Materials; Dow AgroSciences → Corteva), research centres
  (DZNE site → DZNE Bonn).
- Free signals tested on the 689: postcode region differs 545 (likely wrong), same region 90,
  no postcode 54; customer email domain matches the source site 55 (very likely right), differs
  182 (suspicious), no email 438.
- **SAME_NAME + OTHER_TOWN (512 Review rows)** is usually head office vs branch (right), wrong for
  pharmacies, sole traders and small firms with common names.

### 9.2 Several numbers on one page (Conflict)
- 804 of 809 Conflict rows. Pages listing several group companies (city administrations with
  their utilities, hospital groups, holding Impressums). The reader could not attribute a number,
  or code picked by pattern (`NUMBER_NOT_QUOTED` 90 %).
- Likely right: SAME_NAME + SAME_ADDRESS (≈ 70 rows), e.g. cities and hospitals whose page also
  lists subsidiaries' numbers.

### 9.3 Parent / group number
- MAYBE_PARENT_COMPANY: 194 Review, 155 Conflict, 32 Auto-accept. E.g. Siemens Healthcare GmbH
  given the Siemens AG number; TÜV SÜD Product Service given TÜV SÜD AG's; Fresenius Health
  Services given Fresenius SE's.

### 9.4 Private persons / sole traders in Name 1
- **Exactly 84 rows** (36 Review, 48 Conflict; 40 with email, 44 without) where Name 1 is only
  a person's name (optionally with a title) and Name 2–4 hold no company. Hand-verified.
- Excluded on purpose: 19 contact-person rows (person in Name 1, company in Name 2 — the tool
  already searches the company, mostly right) and 13 borderline businesses named after a person.
- Typical wrong: a company that only shares the surname (a person "X Hohmann" → "Hohmann GmbH";
  "X Maier" → "MAIER Heizung Sanitär").
- Right only when the page names that same person as owner (Inhaber) — a sole trader. All 6
  person-named Auto-accepts were checked and are right (pharmacy owner, owner-named companies).
- The existing free first-name check (~200 German first names + surname, no legal form) flags
  159 rows but misses non-German names; a broad shape rule flags 564 but is too noisy.

### 9.5 Directory (aggregator) source
- FROM_DIRECTORY: 517 Review rows. Directory data can be old or belong to a namesake.

### 9.6 Different name but right
- 551 DIFFERENT_NAME rows overall. With the same address / town, more often right than wrong:
  Name 1 holds a department, office, fire brigade, museum, institute, brand or spelling variant,
  and the page names the legal entity behind it (Landratsamt → Landkreis; Feuerwache → Stadt;
  institute → Universität; museum → its operating gGmbH; pharmacy → owner e.K.). Of 40 read with
  same address / town, ~25 looked right.

### 9.7 Same company, different numbers across batches
- The entity key includes the city and is computed per job, so the same company can get
  different answers in different batches or towns (e.g. one engineering firm got its own number
  in one batch and its group parent's in another). A cross-batch consistency pass is not built.

### 9.8 Not found that are real misses (~550–730 rows)
- **370 blocked or unreadable sites** (178 WEBSITE_BLOCKED_US: 61 with email / 117 without;
  192 WEBSITE_NOT_READABLE: 71 / 121). The company has a site; a person opening the Impressum
  usually finds the number. Fix: headless browser / different retry strategy.
- **283 NO_WEBSITE_FOUND rows whose name has a legal form** (GmbH, AG, KG, e.K. …): SAP name
  additions ("Rechnungseingang", department text) or old names mislead the search.
- **98 WEBSITE_HAS_NO_VAT_ID with a legal form:** number may be on invoices, AGB, PDFs.
- **12 typo numbers** among the 22 invalid ones: a real number exists; get it from an invoice.
- Mostly correctly empty: PUBLIC_BODY_NO_VAT (189), PAGE_SAYS_NO_VAT_ID (14), NO_WEBSITE_FOUND
  for names without a legal form (583: private persons, doctors, fire brigades, Bundeswehr units,
  embassies).

### 9.9 Technical
- Azure failures: 4 rows left (2 MODEL_SERVICE_FAILED, 2 CONTENT_FILTER_STOPPED).
- Assembly rebuild changed rows (§6.2).
- 19 rows skipped by the agent cost cap.

---

## 10. Limitations

| Limitation | Effect on the results | What would improve it |
|---|---|---|
| **Short test window.** Azure access and other approvals came late; ~2 days to test, evaluate and fix before the full run | Rules tuned on samples of 100 and 500; some patterns (person names, invalid numbers, rebuild gaps) only showed in the full 7,358 rows | A further test-and-fix round on the patterns now known |
| **No standardised name and address data for Germany.** Names cut at 35 chars, mixed with departments, contacts, abbreviations, typos; incomplete addresses | The tool must guess the legal entity; main reason for "similar name" and "other town" | Cleaned / enriched master data (standard legal names, complete addresses) before the lookup |
| **Known improvements not yet built** | Avoidable errors remain in Review and Conflict | Person detection + owner match; search tied to SAP's address; retry blocked sites; same number for the same company everywhere |
| **No ground truth.** Accuracy from ~500 hand-checked rows | 77–81 % is an estimate | A reviewed sample with confirmed numbers |
| **VIES checks validity, not ownership.** DE VIES returns no name; not yet run | A valid number can belong to another company | Run VIES; confirm ownership via invoices / the partner |
| **Many companies have no usable website.** Small firms, sole traders, persons; some sites block bots | ~370 likely misses, and namesake matches | Retry blocked sites; numbers from invoices / Accounts Payable |
| **Azure rate limit.** ~4 companies per minute | 30 h per full run; few re-test cycles | Higher quota |

---

## 11. Improvement ideas already analysed

### 11.1 Free sorting of the 689 "different name, other town" rows (code only, $0)
Combine postcode region and email-domain signals into a "likely right / likely wrong" mark.
Roughly 140 likely right, ~550 likely wrong. **Do not auto-reject on this alone** (head offices,
renamed companies are right with another region).

### 11.2 Targeted re-run for the "likely wrong" rows (~500 rows, ~$45; test 30 rows first, ~$3)
Proposed rules (not built; ~half a day of code):
1. **Customer's own website first:** if the email has a company domain different from the source
   site, open that domain's Impressum directly.
2. **Search with SAP's address:** Name 1 + street + postcode + town.
3. **Exclude the previous answer** (old number and its site), unless rule 1 points to it.
4. **The page must show SAP's postcode or town.** Same region, other town → Review only.
5. **Name must match and the legal form must not contradict SAP** (e.K. vs GmbH rejected unless
   the page says successor).
6. **Directories count only with SAP's address.**
7. **Invalid-number check applies.**

Outcomes: same number at SAP's address → confirmed (HIGH or Auto if all rules pass); different
number at SAP's address → new Review proposal, old dropped; nothing at SAP's address → old number
marked "not confirmed at SAP address" → **open decision:** keep in Review / Conflict, or move to
Not found (recommended).
Honest expectation: the re-run will **mostly remove wrong numbers, not find right ones**,
because the real company usually has no findable website. Go / no-go: if fewer than ~1 in 5 of
the 30 test rows get a correct number, skip the rest.

### 11.3 Person-name detection + owner rule
- Classify Name 1 as person / organisation with the helper model (< $2 for all rows; recognises
  non-German names). Until then, the free first-name check catches 159.
- For a person in Name 1: accept a number only if the page names **that same person** as owner
  (Inhaber / Inhaberin) at SAP's address; reject companies that only share the surname; if
  nothing, Not found with a new reason `PRIVATE_PERSON`. Never auto-accept without the owner match.
- Offered: a `Looks_Like_Person` column marking the 84 rows.

### 11.4 Other items
| Idea | Notes |
|---|---|
| Run VIES on all proposals | free; ~3,701 numbers; recomputes tiers; catches expired numbers |
| Cross-batch consistency pass | one number per company across all rows; flag disagreements |
| Retry blocked / unreadable sites | 370 rows; headless browser was judged not worth it at 4 % blocked share on samples, but 370 rows is now a real volume |
| Fix job assembly | assemble from chunk JSON; short cache directory |
| Re-run Not found groups (blocked, legal-form-no-website, website-no-VAT) | ~750 rows, ~$70, after the fixes |
| Use `Match_Score` / signals to sort the review queue | no rule uses it today |
| Search query includes the street | today only name + city |
| Entity key without city, or a company-level merge | fixes 9.7, but risks merging namesakes; needs a second signal (email domain, register number) |
| BZSt eVatR qualified check | needs Bruker's own USt-IdNr as requester; checks name / place for non-DE EU numbers |
| Handelsregister / Unternehmensregister | confirms legal name and HRB (no VAT IDs); terms of use to check |
| Invoices / Accounts Payable data | strongest source for the remaining rows; outside this tool |

### 11.5 Open decisions for the user
1. Old numbers when nothing is found at SAP's address: keep in Review / Conflict, or Not found?
2. Whether to build person detection now.
3. Whether to spend ~$3 on the 30-row re-run test.
4. When to run VIES and how VIES-VALID auto-accepts are written to SAP (controlled update).

---

## 12. Suggested tasks for the assistant

- Using Appendix A, find which flag / check combinations concentrate the errors, and propose
  a review order that maximises correct numbers per reviewer hour.
- Propose rule changes that would move SAME_NAME + OTHER_TOWN head-office cases to a higher tier
  without letting namesakes through (e.g. require email-domain match or register number).
- Design the person-in-Name-1 classifier and owner-match rule in detail (prompt, schema, gates,
  tests).
- Redesign search 1 to use SAP's street / postcode and estimate the effect on the 689 rows.
- Propose a fix for the entity key so one company gets one number across towns and batches.
- Propose how to resolve SEVERAL_NUMBERS_ON_PAGE conflicts automatically (e.g. matching the
  number to the company block nearest SAP's name and address).
- Review whether "another town never rejects" should stay, and under which conditions to
  reject.
- Suggest an evaluation design with ground truth (a confirmed sample) to replace the estimates.
- Check the prompts (Appendix B) for ambiguities that could cause the observed errors.


---

## Appendix A. Aggregate statistics per decision (final workbook, 7,358 rows)

Computed from `DE_VAT_FINAL_einvoicing_ALL.xlsx` (both email sheets combined). Percentages are of the rows in that decision. Issue_Flags: a row can carry several.

#### Auto-accept (2429 rows)

**VAT_Found_Confidence**

| Value | Rows | % |
|---|---|---|
| VERIFIED | 2429 | 100.0 |

**Name_Check**

| Value | Rows | % |
|---|---|---|
| SAME_NAME | 2034 | 83.7 |
| SAP_NAME_PLUS_MORE | 395 | 16.3 |

**Address_Check**

| Value | Rows | % |
|---|---|---|
| SAME_ADDRESS | 1387 | 57.1 |
| OTHER_STREET_SAME_TOWN | 1042 | 42.9 |

**Agent_Pass**

| Value | Rows | % |
|---|---|---|
| (blank) | 2388 | 98.3 |
| Found | 41 | 1.7 |

**VAT_Found_Source_Type**

| Value | Rows | % |
|---|---|---|
| COMPANY_IMPRESSUM | 2343 | 96.5 |
| COMPANY_WEBSITE | 71 | 2.9 |
| PEPPOL_DIRECTORY | 11 | 0.5 |
| PROCUREMENT_PROFILE | 4 | 0.2 |

**VAT_Found_Source_Class**

| Value | Rows | % |
|---|---|---|
| A | 2425 | 99.8 |
| B | 4 | 0.2 |

**VIES**

| Value | Rows | % |
|---|---|---|
| Pending | 2429 | 100.0 |

**Issue_Flags (a row can have several)**

| Value | Rows | % |
|---|---|---|
| DEPARTMENT_OR_UNIT | 623 | 25.6 |
| NAME_SHARED_BY_OTHERS | 46 | 1.9 |
| SEVERAL_NUMBERS_ON_PAGE | 34 | 1.4 |
| MAYBE_PARENT_COMPANY | 32 | 1.3 |
| SITE_OR_PLANT | 11 | 0.5 |

#### ALL (7358 rows)

**VAT_Found_Source_Type**

| Value | Rows | % |
|---|---|---|
| COMPANY_IMPRESSUM | 4602 | 62.5 |
| (blank) | 1942 | 26.4 |
| AGGREGATOR | 536 | 7.3 |
| COMPANY_WEBSITE | 160 | 2.2 |
| PEPPOL_DIRECTORY | 83 | 1.1 |
| WEB_ARCHIVE | 29 | 0.4 |
| PROCUREMENT_PROFILE | 6 | 0.1 |

**Issue_Flags (a row can have several)**

| Value | Rows | % |
|---|---|---|
| NUMBER_NOT_QUOTED | 1055 | 14.3 |
| DEPARTMENT_OR_UNIT | 1013 | 13.8 |
| SEVERAL_NUMBERS_ON_PAGE | 899 | 12.2 |
| FROM_DIRECTORY | 536 | 7.3 |
| MAYBE_PARENT_COMPANY | 382 | 5.2 |
| LEGAL_FORM_DIFFERS | 378 | 5.1 |
| NAME_SHARED_BY_OTHERS | 211 | 2.9 |
| MAYBE_RENAMED_OR_SUCCESSOR | 90 | 1.2 |
| SITE_OR_PLANT | 83 | 1.1 |
| FROM_OLD_ARCHIVE | 29 | 0.4 |
| SISTER_COMPANY_ON_PAGE | 9 | 0.1 |
| OTHER_SOURCE_OTHER_NUMBER | 5 | 0.1 |
| IN_LIQUIDATION | 4 | 0.1 |

#### Review (2264 rows)

**VAT_Found_Confidence**

| Value | Rows | % |
|---|---|---|
| MEDIUM_CONFIDENCE | 1471 | 65.0 |
| LOW_CONFIDENCE | 793 | 35.0 |

**Name_Check**

| Value | Rows | % |
|---|---|---|
| SAME_NAME | 905 | 40.0 |
| SIMILAR_NAME | 840 | 37.1 |
| DIFFERENT_NAME | 260 | 11.5 |
| SAP_NAME_PLUS_MORE | 258 | 11.4 |
| (blank) | 1 | 0.0 |

**Address_Check**

| Value | Rows | % |
|---|---|---|
| OTHER_TOWN | 966 | 42.7 |
| SAME_ADDRESS | 695 | 30.7 |
| OTHER_STREET_SAME_TOWN | 520 | 23.0 |
| NO_ADDRESS_ON_PAGE | 79 | 3.5 |
| (blank) | 4 | 0.2 |

**Agent_Pass**

| Value | Rows | % |
|---|---|---|
| (blank) | 1922 | 84.9 |
| Found | 342 | 15.1 |

**VAT_Found_Source_Type**

| Value | Rows | % |
|---|---|---|
| COMPANY_IMPRESSUM | 1470 | 64.9 |
| AGGREGATOR | 517 | 22.8 |
| (blank) | 108 | 4.8 |
| COMPANY_WEBSITE | 73 | 3.2 |
| PEPPOL_DIRECTORY | 67 | 3.0 |
| WEB_ARCHIVE | 27 | 1.2 |
| PROCUREMENT_PROFILE | 2 | 0.1 |

**VAT_Found_Source_Class**

| Value | Rows | % |
|---|---|---|
| A | 1610 | 71.1 |
| C | 544 | 24.0 |
| (blank) | 108 | 4.8 |
| B | 2 | 0.1 |

**VIES**

| Value | Rows | % |
|---|---|---|
| Pending | 2264 | 100.0 |

**Issue_Flags (a row can have several)**

| Value | Rows | % |
|---|---|---|
| FROM_DIRECTORY | 517 | 22.8 |
| DEPARTMENT_OR_UNIT | 325 | 14.4 |
| NUMBER_NOT_QUOTED | 305 | 13.5 |
| MAYBE_PARENT_COMPANY | 194 | 8.6 |
| LEGAL_FORM_DIFFERS | 152 | 6.7 |
| NAME_SHARED_BY_OTHERS | 115 | 5.1 |
| MAYBE_RENAMED_OR_SUCCESSOR | 83 | 3.7 |
| SEVERAL_NUMBERS_ON_PAGE | 58 | 2.6 |
| SITE_OR_PLANT | 48 | 2.1 |
| FROM_OLD_ARCHIVE | 27 | 1.2 |
| SISTER_COMPANY_ON_PAGE | 9 | 0.4 |
| IN_LIQUIDATION | 4 | 0.2 |

**Name_Check x Address_Check**

| Name | Address | Rows |
|---|---|---|
| SAME_NAME | OTHER_TOWN | 512 |
| SIMILAR_NAME | SAME_ADDRESS | 328 |
| SIMILAR_NAME | OTHER_STREET_SAME_TOWN | 283 |
| SIMILAR_NAME | OTHER_TOWN | 224 |
| SAME_NAME | SAME_ADDRESS | 210 |
| SAP_NAME_PLUS_MORE | OTHER_TOWN | 153 |
| SAME_NAME | OTHER_STREET_SAME_TOWN | 111 |
| DIFFERENT_NAME | SAME_ADDRESS | 94 |
| DIFFERENT_NAME | OTHER_STREET_SAME_TOWN | 85 |
| DIFFERENT_NAME | OTHER_TOWN | 77 |
| SAME_NAME | NO_ADDRESS_ON_PAGE | 71 |
| SAP_NAME_PLUS_MORE | SAME_ADDRESS | 63 |
| SAP_NAME_PLUS_MORE | OTHER_STREET_SAME_TOWN | 41 |
| SIMILAR_NAME | NO_ADDRESS_ON_PAGE | 4 |
| DIFFERENT_NAME | NO_ADDRESS_ON_PAGE | 3 |
| SAP_NAME_PLUS_MORE | NO_ADDRESS_ON_PAGE | 1 |
| None | None | 1 |
| DIFFERENT_NAME | None | 1 |
| SAME_NAME | None | 1 |
| SIMILAR_NAME | None | 1 |

#### Conflict (809 rows)

**VAT_Found_Confidence**

| Value | Rows | % |
|---|---|---|
| CONFLICT | 809 | 100.0 |

**Name_Check**

| Value | Rows | % |
|---|---|---|
| SIMILAR_NAME | 389 | 48.1 |
| DIFFERENT_NAME | 287 | 35.5 |
| SAP_NAME_PLUS_MORE | 68 | 8.4 |
| SAME_NAME | 65 | 8.0 |

**Address_Check**

| Value | Rows | % |
|---|---|---|
| OTHER_TOWN | 444 | 54.9 |
| SAME_ADDRESS | 194 | 24.0 |
| OTHER_STREET_SAME_TOWN | 162 | 20.0 |
| NO_ADDRESS_ON_PAGE | 9 | 1.1 |

**Agent_Pass**

| Value | Rows | % |
|---|---|---|
| (blank) | 797 | 98.5 |
| Found | 12 | 1.5 |

**VAT_Found_Source_Type**

| Value | Rows | % |
|---|---|---|
| COMPANY_IMPRESSUM | 778 | 96.2 |
| AGGREGATOR | 12 | 1.5 |
| COMPANY_WEBSITE | 12 | 1.5 |
| PEPPOL_DIRECTORY | 5 | 0.6 |
| WEB_ARCHIVE | 2 | 0.2 |

**VAT_Found_Source_Class**

| Value | Rows | % |
|---|---|---|
| A | 795 | 98.3 |
| C | 14 | 1.7 |

**VIES**

| Value | Rows | % |
|---|---|---|
| Not checked | 755 | 93.3 |
| Pending | 54 | 6.7 |

**Issue_Flags (a row can have several)**

| Value | Rows | % |
|---|---|---|
| SEVERAL_NUMBERS_ON_PAGE | 804 | 99.4 |
| NUMBER_NOT_QUOTED | 728 | 90.0 |
| LEGAL_FORM_DIFFERS | 221 | 27.3 |
| MAYBE_PARENT_COMPANY | 155 | 19.2 |
| DEPARTMENT_OR_UNIT | 64 | 7.9 |
| NAME_SHARED_BY_OTHERS | 48 | 5.9 |
| SITE_OR_PLANT | 24 | 3.0 |
| FROM_DIRECTORY | 12 | 1.5 |
| MAYBE_RENAMED_OR_SUCCESSOR | 7 | 0.9 |
| OTHER_SOURCE_OTHER_NUMBER | 5 | 0.6 |
| FROM_OLD_ARCHIVE | 2 | 0.2 |

**Name_Check x Address_Check**

| Name | Address | Rows |
|---|---|---|
| SIMILAR_NAME | OTHER_TOWN | 222 |
| DIFFERENT_NAME | OTHER_TOWN | 166 |
| SIMILAR_NAME | SAME_ADDRESS | 98 |
| SIMILAR_NAME | OTHER_STREET_SAME_TOWN | 68 |
| DIFFERENT_NAME | OTHER_STREET_SAME_TOWN | 66 |
| DIFFERENT_NAME | SAME_ADDRESS | 54 |
| SAP_NAME_PLUS_MORE | OTHER_TOWN | 39 |
| SAP_NAME_PLUS_MORE | SAME_ADDRESS | 21 |
| SAME_NAME | SAME_ADDRESS | 21 |
| SAME_NAME | OTHER_STREET_SAME_TOWN | 20 |
| SAME_NAME | OTHER_TOWN | 17 |
| SAP_NAME_PLUS_MORE | OTHER_STREET_SAME_TOWN | 8 |
| SAME_NAME | NO_ADDRESS_ON_PAGE | 7 |
| DIFFERENT_NAME | NO_ADDRESS_ON_PAGE | 1 |
| SIMILAR_NAME | NO_ADDRESS_ON_PAGE | 1 |

#### Not found (1856 rows)

**VAT_Found_Confidence**

| Value | Rows | % |
|---|---|---|
| NOT_FOUND | 1856 | 100.0 |

**Name_Check**

| Value | Rows | % |
|---|---|---|
| (blank) | 1834 | 98.8 |
| SIMILAR_NAME | 10 | 0.5 |
| SAME_NAME | 7 | 0.4 |
| DIFFERENT_NAME | 4 | 0.2 |
| SAP_NAME_PLUS_MORE | 1 | 0.1 |

**Address_Check**

| Value | Rows | % |
|---|---|---|
| (blank) | 1834 | 98.8 |
| NO_ADDRESS_ON_PAGE | 8 | 0.4 |
| SAME_ADDRESS | 7 | 0.4 |
| OTHER_TOWN | 4 | 0.2 |
| OTHER_STREET_SAME_TOWN | 3 | 0.2 |

**Not_Found_Reason**

| Value | Rows | % |
|---|---|---|
| NO_WEBSITE_FOUND | 866 | 46.7 |
| WEBSITE_HAS_NO_VAT_ID | 352 | 19.0 |
| WEBSITE_NOT_READABLE | 192 | 10.3 |
| PUBLIC_BODY_NO_VAT | 189 | 10.2 |
| WEBSITE_BLOCKED_US | 178 | 9.6 |
| PAGE_SAYS_COMPANY_CLOSED | 29 | 1.6 |
| INVALID_VAT_NUMBER | 22 | 1.2 |
| PAGE_SAYS_NO_VAT_ID | 14 | 0.8 |
| NO_NUMBER_FOUND | 10 | 0.5 |
| MODEL_SERVICE_FAILED | 2 | 0.1 |
| CONTENT_FILTER_STOPPED | 2 | 0.1 |

**Agent_Pass**

| Value | Rows | % |
|---|---|---|
| Tried, nothing found | 1612 | 86.9 |
| (blank) | 223 | 12.0 |
| Skipped (cost cap) | 19 | 1.0 |
| Found | 2 | 0.1 |

**VAT_Found_Source_Type**

| Value | Rows | % |
|---|---|---|
| (blank) | 1834 | 98.8 |
| COMPANY_IMPRESSUM | 11 | 0.6 |
| AGGREGATOR | 7 | 0.4 |
| COMPANY_WEBSITE | 4 | 0.2 |

**VAT_Found_Source_Class**

| Value | Rows | % |
|---|---|---|
| (blank) | 1834 | 98.8 |
| A | 15 | 0.8 |
| C | 7 | 0.4 |

**Issue_Flags (a row can have several)**

| Value | Rows | % |
|---|---|---|
| NUMBER_NOT_QUOTED | 22 | 1.2 |
| FROM_DIRECTORY | 7 | 0.4 |
| LEGAL_FORM_DIFFERS | 5 | 0.3 |
| SEVERAL_NUMBERS_ON_PAGE | 3 | 0.2 |
| NAME_SHARED_BY_OTHERS | 2 | 0.1 |
| DEPARTMENT_OR_UNIT | 1 | 0.1 |
| MAYBE_PARENT_COMPANY | 1 | 0.1 |

---

## Appendix B. Prompts, verbatim

### B.1 `vat/prompts.py` (search, reader, hybrid, Name-2 classifier, LLM-only experiment)

```python
"""Prompts for the missing-VAT lookups. Versioned like ``llm/prompts.py``.

The grounded reader follows the constrained-reader pattern of
``llm.prompts.PAGE_READ_SYSTEM_PROMPT``: the model reads ONE fetched page and
reports what that page literally states. It is never shown the SAP record's
name or address, so it cannot be steered towards an answer; whether the ID it
reads belongs to the customer is decided afterwards by deterministic gates
(``vat/lookup_grounded.py``), not by the model.
"""

from __future__ import annotations

from llm.prompts import prompt_version

IMPRESSUM_READ_SYSTEM_PROMPT = (
    "You read the text of one web page (usually a German Impressum / legal "
    "notice) and report ONLY the VAT identification number it literally "
    "states. You are a reader, not a source of knowledge: nothing you know "
    "about any company may appear in your answer. Never infer, complete, "
    "correct or reformat digits. Return valid JSON only. No markdown."
)

IMPRESSUM_READ_USER_PROMPT_TEMPLATE = (
    "Page URL: {url}\n"
    "Page title: {title}\n"
    "Page text:\n"
    "---\n"
    "{text}\n"
    "---\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "published_address": "str or null",\n'
    '  "other_vat_ids_on_page": ["str"]\n'
    "}}\n"
    "Rules:\n"
    "1. vat_id is the Umsatzsteuer-Identifikationsnummer / USt-IdNr. / VAT "
    "ID / VAT number exactly as written on this page, including its country "
    "prefix if the page shows one. Copy it character for character. If the "
    "page shows no VAT ID, vat_id is null.\n"
    "2. A Steuernummer, tax number, Wirtschafts-Identifikationsnummer "
    "(W-IdNr., DE + 9 digits + a 5-digit suffix), Handelsregister number "
    "(HRB/HRA), LEI, GLN, D-U-N-S number, WEEE number, EORI number, phone or "
    "fax number is NOT a VAT ID. Never return one of those as vat_id.\n"
    "3. If the page lists several VAT IDs, vat_id is the one the page gives "
    "for the organisation the page is the legal notice of (the provider / "
    "Diensteanbieter / operator). Every other VAT ID on the page goes in "
    "other_vat_ids_on_page, each exactly as written. Otherwise that list is "
    "empty.\n"
    "4. attributed_to is the organisation name, exactly as written on the "
    "page, that the page states vat_id belongs to. null if the page does "
    "not say whose number it is.\n"
    "5. evidence_quote is one short passage copied VERBATIM from the page "
    "text above that contains vat_id (at most 200 characters). Do not "
    "paraphrase, translate, shorten or fix it. null when vat_id is null.\n"
    "6. Never complete a partial number and never guess a missing digit. "
    "If the number on the page is incomplete or unreadable, vat_id is null.\n"
    "7. published_address is the postal address the page gives for the "
    "organisation in attributed_to (street, house number, postal code, "
    "city), copied from the page; separate its lines with \", \". null if "
    "the page gives none."
)

# ── LLM_WEB — the model searches the web; never answers from memory. ────────
# See vat/lookup_web.py. The model is given the SAP name and address (it needs
# them to search) and reports what ONE page it visited states. Whether that
# page really says so, and whether the number is this customer's, is decided
# afterwards by the code: it fetches source_url itself and runs the grounded
# gates on it. A number without a visited source is never used.
LLM_WEB_SYSTEM_PROMPT = (
    "You find a German company's VAT identification number (USt-IdNr.) by "
    "searching the web and reading the pages you find, the way a careful "
    "researcher would. You never answer from memory: a number you did not "
    "read on a page you opened or found in this search must not appear in "
    "your answer. If no page you found states it, say so with null. Return "
    "valid JSON only. No markdown."
)

# Static instructions first and the customer last, so every lookup shares the
# same prefix and it is served from the prompt cache.
LLM_WEB_USER_PROMPT_TEMPLATE = (
    "Search the web for a page where the organisation at the end of this "
    "message itself states its USt-IdNr. and report it.\n"
    "Budget: at most {max_searches} web searches, and at most {max_opens} "
    "pages opened on top of them; opening a page is not a search. A further "
    "call is refused. Plan the calls: search for the organisation's own "
    "Impressum (or procurement page) first. When the budget is used up, "
    "answer with what you have; if no page you saw states the number, every "
    "field is null.\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "source_url": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "published_address": "str or null"\n'
    "}}\n"
    "Rules:\n"
    "1. Sources, in this order: (a) the organisation's own website: its "
    "legal notice (Impressum / Legal Notice) first; when that shows no "
    "USt-IdNr., its other pages and PDFs on the same domain, such as "
    "supplier information (Lieferanteninformationen), purchasing / "
    "procurement (Einkauf, Beschaffung), invoicing address "
    "(Rechnungsanschrift), terms and conditions (AGB) and order forms. "
    "Universities, hospitals and public bodies usually publish it on the "
    "procurement page, not in the Impressum. (b) The Peppol Directory "
    "(directory.peppol.eu): a participant under scheme 9930 is identified "
    "by its German VAT ID. (c) The organisation's buyer profile on a public "
    "procurement portal (bund.de, DTVP, evergabe, subreport) or its filing "
    "in the Bundesanzeiger / Unternehmensregister. (d) Company directories "
    "(northdata, firmenwissen, companyhouse, implisense, wer-zu-wem, …) only "
    "point you to the organisation's own website; their copy of a number is "
    "not a source. (e) Web archive snapshots (web.archive.org) only when the "
    "record status below says deleted. The Handelsregister confirms the "
    "legal name but publishes no VAT IDs. Never use marketplaces or social "
    "networks, and never a search result snippet: open the page.\n"
    "2. The number must belong to exactly this legal entity: the same name "
    "and the same legal form (GmbH, AG, SE, KG …). The number of a parent, "
    "subsidiary, sister company, branch, predecessor or successor that "
    "shares the brand is not this company's number. Several companies can "
    "also share this name; the right one is at the street / postal code / "
    "city below. If you cannot tell which page belongs to this legal "
    "entity at that address, return null rather than pick one. When the "
    "name below is a site of a company (Werk, Niederlassung, Standort, "
    "Filiale …) or a department in Name 2–4 (Institut, Lehrstuhl, Klinik "
    "für …), report the number of the legal entity it belongs to: a branch "
    "or department has no VAT ID of its own.\n"
    "3. vat_id is the USt-IdNr. exactly as written on the page (DE + 9 "
    "digits). A Steuernummer, Wirtschafts-Identifikationsnummer (W-IdNr., "
    "with a 5-digit suffix), Handelsregister (HRB/HRA), LEI, GLN, D-U-N-S, "
    "WEEE, EORI, phone or fax number is not a VAT ID.\n"
    "4. source_url is the URL of the page that states vat_id, exactly as "
    "you visited it.\n"
    "5. evidence_quote is one short passage copied VERBATIM from that page "
    "that contains vat_id (at most 200 characters).\n"
    "6. attributed_to is the organisation name, exactly as written on that "
    "page, that the page says the number belongs to.\n"
    "7. published_address is the postal address that page gives for that "
    "organisation, copied from the page; separate its lines with \", \".\n"
    "8. Never complete, correct, derive or guess digits, and never fill in "
    "a number you remember. If no page states it, every field is null.\n\n"
    "Organisation (from our customer master data):\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "Street: {street}\n"
    "Postal Code: {postal_code}\n"
    "City: {city}\n"
    "Country: {country}\n"
    "Website / email domain (optional): {domain}\n"
    "Commercial register number (optional): {register_number}\n"
    "Record status: {record_status}"
)

#: Structured output for the LLM_WEB answer (strict JSON schema).
LLM_WEB_ANSWER_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["vat_id", "source_url", "evidence_quote", "attributed_to", "published_address"],
    "properties": {k: {"type": ["string", "null"]} for k in (
        "vat_id", "source_url", "evidence_quote", "attributed_to", "published_address")},
}

# ── Mode B — LLM_ONLY. EXPERIMENT ONLY. ─────────────────────────────────────
# Asks the model from memory, with no search and no page, to MEASURE how
# unreliable that is. Never used on a production path; its answers are never
# written to SAP. See vat/lookup_llm_only.py.
LLM_ONLY_SYSTEM_PROMPT = (
    "You answer questions about companies. Return valid JSON only. No markdown."
)

LLM_ONLY_USER_PROMPT_TEMPLATE = (
    "Company:\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "Street: {street}\n"
    "Postal Code: {postal_code}\n"
    "City: {city}\n"
    "Country: {country}\n\n"
    "What is this company's German VAT identification number (USt-IdNr.)? "
    'Answer JSON {{"vat_id": "str", "confidence": "high|medium|low"}}. '
    "vat_id is never null: this is a research experiment that measures how "
    "accurate a model's recall is, so a wrong number is a valid answer and an "
    "empty one is not. If you are not sure, give your best candidate number "
    '(DE followed by 9 digits) and set confidence to "low".'
)

LLM_ONLY_PROMPT_VERSION = prompt_version(
    # v2 — always a best guess (confidence "low" when unsure) instead of null:
    # the experiment measures what the model proposes, including when unsure.
    # v3 — no null escape: gpt-6-sol took v2's "null only if you have no
    # candidate" on 31 of 38 rows; with it removed it guesses, at "low".
    "vat_llm_only_experiment", "v3",
    LLM_ONLY_SYSTEM_PROMPT, LLM_ONLY_USER_PROMPT_TEMPLATE,
)

LLM_WEB_PROMPT_VERSION = prompt_version(
    # v2 — design paper alignment: domain and register inputs, the source
    # hierarchy, the exact-legal-entity rule (legal form; no parent /
    # subsidiary / branch), W-IdNr. / LEI / GLN / D-U-N-S excluded, and the
    # published address.
    # v3 — sources by who publishes the number: own domain (Impressum, then
    # supplier / procurement pages and PDFs) → Peppol Directory → Vergabeportal
    # buyer profile / Bundesanzeiger → directories as pointers only → archive
    # snapshots for deleted records only; the record status input. The
    # search budget sentence; static instructions first, customer data last.
    # v4 — the budget counts searches only; page opens have their own cap.
    "vat_llm_web", "v4", LLM_WEB_SYSTEM_PROMPT, LLM_WEB_USER_PROMPT_TEMPLATE,
)

IMPRESSUM_READ_PROMPT_VERSION = prompt_version(
    # v2 — published_address; W-IdNr. / LEI / GLN / D-U-N-S excluded.
    "vat_impressum_read", "v2",
    IMPRESSUM_READ_SYSTEM_PROMPT, IMPRESSUM_READ_USER_PROMPT_TEMPLATE,
)


# ── LLM_WEB "hybrid" (vat/lookup_web.py, vat/web_query.py) ───────────────────
# Search 1 is built by code and only RUN by the model; search 2 is the
# model's own query. Neither call opens pages: our code fetches the results,
# cuts them to the VAT / Impressum sections and gives those to the reader.
LLM_WEB_RUN_SYSTEM_PROMPT = (
    "You run web searches for a research pipeline. You run exactly the "
    "search you are told to, once, and open no pages. Return valid JSON "
    "only. No markdown."
)

LLM_WEB_RUN_USER_PROMPT_TEMPLATE = (
    "Run exactly one web search with this query, character for character, "
    "and nothing else. Do not open any result. Then return JSON with "
    '"query_run" (the query you ran) and "results". ' + '"results" lists every result the search returned, in order: "url" exactly as shown, "title" as shown, and "website": when the result is a company-directory or register entry whose title or snippet names the organisation\'s own website, that website\'s URL; else null.' + "\n"
    "Query: {query}"
)

#: One search result as the model saw it (the API itself reports URLs only).
_RESULT_ITEM: dict = {
    "type": "object", "additionalProperties": False, "required": ["url", "title", "website"],
    "properties": {"url": {"type": "string"}, "title": {"type": ["string", "null"]},
                   "website": {"type": ["string", "null"]}},
}

LLM_WEB_RUN_SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["query_run", "results"],
    "properties": {"query_run": {"type": "string"},
                   "results": {"type": "array", "items": _RESULT_ITEM}},
}

LLM_WEB_SECOND_SYSTEM_PROMPT = (
    "You find a German organisation's VAT identification number (USt-IdNr.). "
    "A first web search did not find the organisation's own website. You make "
    "ONE better web search and open no pages; the pipeline reads the results. "
    "Return valid JSON only. No markdown."
)

# Static instructions first and the customer last (prompt cache).
LLM_WEB_SECOND_USER_PROMPT_TEMPLATE = (
    "Make one web search that is more likely than the first to return this "
    "organisation's own website, ideally its Impressum / legal notice or, for "
    "universities, hospitals, research institutes and public bodies, its "
    "procurement, supplier-information or tax-number page (Einkauf, "
    "Lieferanten, Steuernummern). Rules:\n"
    "1. You may use the organisation's current or full legal name if you "
    "know it, the expansion of an acronym, or the parent organisation of a "
    "department, institute or clinic; and the terms Impressum, USt-IdNr or "
    "Steuernummer.\n"
    "2. Do not repeat the first search, or make one that differs only in "
    "word order or quotes.\n"
    "3. Do not include the city.\n"
    "4. One query only: run it once and open no result.\n"
    "{zero_results_rule}"
    "Then return JSON with: query (the query you ran, exactly), name_used "
    "(the organisation name in it, as you wrote it), why_this_query (one "
    "sentence: why this finds the website when the first search did not), "
    "domain_hint (the organisation's official website domain, e.g. "
    "\"uni-heidelberg.de\", only if you know it; else null; our pipeline "
    "checks it and never takes it on trust), "
    "and results. " + '"results" lists every result the search returned, in order: "url" exactly as shown, "title" as shown, and "website": when the result is a company-directory or register entry whose title or snippet names the organisation\'s own website, that website\'s URL; else null.' + "\n\n"
    "First search: {query_1}\n"
    "What it returned (none of it is this organisation's own website):\n{results_1}\n\n"
    "Organisation (from our customer master data):\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "City: {city}\n"
    "Website / email domain (optional): {domain}"
)

LLM_WEB_SECOND_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["query", "name_used", "why_this_query", "domain_hint", "results"],
    "properties": {**{k: {"type": "string"} for k in ("query", "name_used", "why_this_query")},
                   "domain_hint": {"type": ["string", "null"]},
                   "results": {"type": "array", "items": _RESULT_ITEM}},
}

# ── Helper calls on the small model (Step AF): no tools, reasoning "none" ───

LLM_WEB_RANK_SYSTEM_PROMPT = (
    "You rank web search results for a data-quality pipeline that looks up a "
    "German organisation's legal notice (Impressum). You only order and label "
    "the results; the pipeline opens the pages and checks them itself. "
    "Return valid JSON only. No markdown."
)

LLM_WEB_RANK_USER_PROMPT_TEMPLATE = (
    "Pick at most 3 results, best first, that are most likely to lead to this "
    "organisation's own Impressum / legal notice: its Impressum page, its own "
    "website, a company-directory entry that names its website, or its parent "
    "organisation's site (a university for a clinic or institute, the EU for "
    "an EU body). For each: url exactly as listed; type: impressum, own_site, "
    "directory_pointer, parent_site or other; belongs_to_entity: yes, likely, "
    "no or unknown (is the site this organisation's own or its parent's?); "
    "reason: at most 12 words. Results of other organisations with a similar "
    "name are not this organisation's (belongs_to_entity no). A result whose "
    "host or path contains legal, impressum, imprint or legal-notice comes "
    "before every other result of the organisation. Pick none when none "
    "fits.\n\n"
    "Organisation (from our customer master data):\n"
    "Search name: {search_name}\n"
    "Acceptable names: {acceptable}\n"
    "City: {city}\n"
    "Segment: {segment}\n\n"
    "Results:\n{results}"
)

LLM_WEB_RANK_SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["ranked"],
    "properties": {"ranked": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["url", "type", "belongs_to_entity", "reason"],
        "properties": {
            "url": {"type": "string"},
            "type": {"type": "string",
                     "enum": ["impressum", "own_site", "directory_pointer", "parent_site", "other"]},
            "belongs_to_entity": {"type": "string", "enum": ["yes", "likely", "no", "unknown"]},
            "reason": {"type": "string"},
        }}}},
}

NAME2_SYSTEM_PROMPT = (
    "You read German customer master data. SAP cuts Name 1 at 35 characters and "
    "the rest of a name often runs on in Name 2-4; Name 2-4 may also hold a "
    "department, a goods-in desk, a site or a separate legal entity. You say "
    "which, and for a name that runs on, write the full legal name. You never "
    "look anything up. Return valid JSON only. No markdown."
)

#: Ten records from the DE export (CMD_DE_2026-10-21), labelled by hand.
NAME2_EXAMPLES = (
    ("Chemisches und Veterinaer | Untersuchungsamt Sigmaringen | SIGMARINGEN",
     "CONTINUATION", "Chemisches und Veterinäruntersuchungsamt Sigmaringen", 0.95,
     "Name 2 completes the authority's name"),
    ("Landesuntersuchungsanstalt für das | Gesundheits-und Veterinärwesen | Standort Chemnitz | CHEMNITZ",
     "CONTINUATION", "Landesuntersuchungsanstalt für das Gesundheits- und Veterinärwesen", 0.9,
     "Name 1 ends on 'für das'; Name 3 is a site"),
    ("Polizeiinspektion Zentrale Dienste | Sachsen-Anhalt | MAGDEBURG",
     "CONTINUATION", "Polizeiinspektion Zentrale Dienste Sachsen-Anhalt", 0.85,
     "the state is part of the authority's name"),
    ("Pfizer Manufacturing Deutschland | Grundbesitz GmbH & Co KG | FREIBURG",
     "CONTINUATION", "Pfizer Manufacturing Deutschland Grundbesitz GmbH & Co. KG", 0.95,
     "Name 2 ends the company name with its legal form"),
    ("Eberhard Karls Universität Tübingen | Proteom Centrum Tübingen | TÜBINGEN",
     "DEPARTMENT", None, 0.9, "a research centre of the university"),
    ("Charité-Universitätsmedizin Berlin | Medizintechnik | BERLIN",
     "DEPARTMENT", None, 0.9, "the hospital's medical-technology department"),
    ("WIKA Alexander Wiegand SE & Co. KG | Wika Retoure für Wareneingang WIKA | WÖRTH",
     "DEPARTMENT", None, 0.95, "a returns / goods-in desk of the company"),
    ("Gemeinschaftslabor Nanomaterialien | FZ Karlsruhe / TU Darmstadt | DARMSTADT",
     "OTHER", None, 0.6, "a joint lab of two institutions; no single legal entity"),
    ("NMI Naturwissenschaftliches und | und Medizinisches Institut an der | Universität Tübingen | REUTLINGEN",
     "CONTINUATION", "NMI Naturwissenschaftliches und Medizinisches Institut an der Universität Tübingen", 0.95,
     "the institute's name runs on over Name 2 and 3"),
    ("Albert-Ludwigs-Universität Freiburg | Dez 4 Bau und Infrastruktur | D4.5 Zentrale Beschaffung | FREIBURG",
     "DEPARTMENT", None, 0.95, "a Dezernat and its procurement office"),
)


def _name2_example(record, cls, name, conf, why) -> str:
    import json

    answer = json.dumps({"class": cls, "legal_name": name, "confidence": conf, "reason": why}, ensure_ascii=False)
    # Braces doubled: the template is filled with str.format.
    return f"Record: {record}\nAnswer: {answer}".replace("{", "{{").replace("}", "}}")


NAME2_USER_PROMPT_TEMPLATE = (
    "class: CONTINUATION (Name 2-4 carry on the legal entity's name), "
    "DEPARTMENT (a department, institute, desk or site of the Name 1 entity), "
    "SUB_ENTITY (Name 2-4 name a separate legal entity under Name 1) or OTHER. "
    "legal_name: CONTINUATION only, the full legal name with a word cut at 35 "
    "characters repaired; else null. confidence: 0 to 1. reason: at most 15 "
    "words.\n\nExamples:\n"
    + "\n\n".join(_name2_example(*e) for e in NAME2_EXAMPLES)
    + "\n\nRecord: {record}\nAnswer:"
)

NAME2_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["class", "legal_name", "confidence", "reason"],
    "properties": {
        "class": {"type": "string", "enum": ["CONTINUATION", "DEPARTMENT", "SUB_ENTITY", "OTHER"]},
        "legal_name": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}

NAME2_PROMPT_VERSION = prompt_version(
    # v1 — Step AF rule 10b: ten hand-labelled export records as few-shot examples.
    "vat_name2_classifier", "v1", NAME2_SYSTEM_PROMPT, NAME2_USER_PROMPT_TEMPLATE,
)

#: Search 2's extra rule when search 1 returned nothing.
LLM_WEB_ZERO_RESULTS_RULE = (
    "5. The first search returned no results at all: leave out the quotes and "
    "the legal form (GmbH, AG, KG …).\n"
)

LLM_WEB_READ_SYSTEM_PROMPT = (
    "You read extracts of web pages our pipeline fetched and report ONLY "
    "the VAT identification number (USt-IdNr.) they literally state for one "
    "organisation. You are a reader, not a source of knowledge: nothing you "
    "know about any company may appear in your answer. Never infer, "
    "complete, correct or reformat digits. Return valid JSON only. No "
    "markdown."
)

LLM_WEB_READ_USER_PROMPT_TEMPLATE = (
    "Each page below is given as its URL, the strings on the whole page "
    "that look like a German VAT ID (DE + 9 digits; found by pattern, "
    "possibly someone else's), and extracts: the page head and the text "
    "around every USt-IdNr. / VAT / Impressum mention.\n"
    "Rules:\n"
    "1. vat_id is the USt-IdNr. a page states for the organisation named at "
    "the end, or for the legal entity it belongs to (its company for a "
    "site, its university or hospital for an institute or clinic), exactly "
    "as written, with its DE prefix. source_url is that page's URL as "
    "listed. A Steuernummer, W-IdNr. (with a 5-digit suffix), HRB/HRA, LEI, "
    "GLN, D-U-N-S, EORI, phone or fax number is not a VAT ID.\n"
    "2. When a page lists several organisations' numbers, take the one the "
    "page gives for this organisation; list every other VAT ID on that page "
    "in other_vat_ids_on_page. If you cannot tell which number is this "
    "organisation's, vat_id is null.\n"
    "2a. candidates lists every VAT ID the pages state, each with source_url "
    "and the organisation name written next to it (attributed_to), whether "
    "or not you chose one: our pipeline puts them before a person when it "
    "cannot tell either.\n"
    "3. evidence_quote is a passage copied VERBATIM from the extract that "
    "contains vat_id (at most 200 characters). attributed_to is the "
    "organisation name, as written on the page, the number is given for. "
    "published_address is the postal address the page gives for it.\n"
    "4. no_vat_statement: when a page says this organisation has no "
    "USt-IdNr., is not liable to VAT or is a Kleinunternehmer (§ 19 UStG), "
    "copy that sentence verbatim; else null.\n"
    "5. Nothing stated → vat_id, source_url, evidence_quote, attributed_to "
    "and published_address are null. Never complete or guess digits.\n\n"
    "Organisation: {name}\n\n"
    "{pages}"
)

_CANDIDATE: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["vat_id", "source_url", "attributed_to"],
    "properties": {"vat_id": {"type": "string"}, "source_url": {"type": ["string", "null"]},
                   "attributed_to": {"type": ["string", "null"]}},
}

LLM_WEB_READ_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["vat_id", "source_url", "evidence_quote", "attributed_to", "published_address",
                 "other_vat_ids_on_page", "no_vat_statement", "candidates"],
    "properties": {
        **{k: {"type": ["string", "null"]} for k in (
            "vat_id", "source_url", "evidence_quote", "attributed_to", "published_address",
            "no_vat_statement")},
        "other_vat_ids_on_page": {"type": "array", "items": {"type": "string"}},
        "candidates": {"type": "array", "items": _CANDIDATE},
    },
}

LLM_WEB_HYBRID_PROMPT_VERSION = prompt_version(
    # v1 — Step AA: search 1 by code, search 2 by the model (name_used,
    # why_this_query), the reader on code-fetched page sections.
    # v2 — Step AB: search 1 is name + city; both searches list their
    # results (title, a directory's website link); search 2 looks for the
    # entity's own website and may use Impressum / USt-IdNr / Steuernummer.
    # v3 — Step AC: the reader lists every VAT ID with its company (candidates).
    # v4 — Step AF: search 2 returns domain_hint; the ranking call on the helper model.
    "vat_llm_web_hybrid", "v4",
    LLM_WEB_RUN_SYSTEM_PROMPT, LLM_WEB_RUN_USER_PROMPT_TEMPLATE,
    LLM_WEB_SECOND_SYSTEM_PROMPT, LLM_WEB_SECOND_USER_PROMPT_TEMPLATE,
    LLM_WEB_READ_SYSTEM_PROMPT, LLM_WEB_READ_USER_PROMPT_TEMPLATE,
    LLM_WEB_RANK_SYSTEM_PROMPT, LLM_WEB_RANK_USER_PROMPT_TEMPLATE,
)

```

### B.2 Agent pass prompt (`vat/lookup_agent.py`, lines 85-188)

```python

#: Flags.
AGENT = "AGENT"
AGENT_NO_ANSWER = "AGENT_NO_ANSWER"
#: The cited page was read, but the host check did not accept its host.
AGENT_HOST_NOT_ACCEPTED = "AGENT_HOST_NOT_ACCEPTED"
#: The cited page could not be fetched by our code.
AGENT_SOURCE_UNREADABLE = "AGENT_SOURCE_UNREADABLE"
#: Our reader read another number on the cited page than the agent reported.
AGENT_VALUE_DIFFERS = "AGENT_VALUE_DIFFERS"

AGENT_SYSTEM_PROMPT = (
    "You find a German organisation's VAT identification number (USt-IdNr.) "
    "by searching the web and opening pages, the way a careful researcher "
    "would. Use what you know about the organisation (its own domain, its "
    "current name if it was renamed, its parent organisation or successor) "
    "to decide where to look. A number may only come from a page you opened "
    "in this search, never from memory. Return valid JSON only. No markdown."
)

_V3_RULES = LLM_WEB_USER_PROMPT_TEMPLATE[
    LLM_WEB_USER_PROMPT_TEMPLATE.index("Rules:\n"):
    LLM_WEB_USER_PROMPT_TEMPLATE.index("Organisation (from our customer master data):")]
_V3_RECORD = LLM_WEB_USER_PROMPT_TEMPLATE[
    LLM_WEB_USER_PROMPT_TEMPLATE.index("Organisation (from our customer master data):"):]

AGENT_USER_PROMPT_TEMPLATE = (
    "Find a page where the organisation at the end of this message itself "
    "states its USt-IdNr. and report it. Our automated pipeline searched for "
    "it and failed; what it tried is listed at the end.\n"
    "Budget: at most {max_searches} web searches, and at most {max_opens} "
    "pages opened on top of them; opening a page is not a search. A further "
    "call is refused. When the budget is used up, answer with what you "
    "have; if no page you opened states the number, vat_id and source_url "
    "are null.\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "source_url": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "published_address": "str or null",\n'
    '  "confidence": "high | medium | low",\n'
    '  "relation": "same_entity | renamed | successor | parent | not_found",\n'
    '  "pages_opened": ["str"],\n'
    '  "notes": "str or null"\n'
    "}}\n"
    + _V3_RULES.rstrip("\n") + "\n"
    "9. Use your own knowledge of this organisation to plan: its own domain "
    "(open it directly), its current name if it was renamed or merged, the "
    "legal entity a department, institute, clinic or site belongs to (rule "
    "2: report that entity's number), its parent. A renamed company is the "
    "same legal entity: its number under the new name is the right one "
    "(relation renamed; attributed_to is the name the page gives). A "
    "successor that took over the business is another entity: report its "
    "number only when the page says it continues this one (relation "
    "successor). relation parent: the number is the parent's, as rule 2 "
    "allows for a department or site.\n"
    "10. The pipeline context below lists the hosts already tried and why "
    "they failed: try something else first (another host, a deeper page, "
    "the procurement or invoicing page, a PDF). VAT IDs on our other records "
    "of the same entity are context only, never a source: report a number "
    "only from a page you opened that states it.\n"
    "11. confidence: high when the page is the organisation's own legal "
    "notice and names it; medium when it is another page of its own or its "
    "parent's site; low otherwise. pages_opened: every URL you opened, in "
    "order. notes: at most 30 words on how you found it or why not.\n\n"
    + _V3_RECORD + "\n\n"
    "Pipeline context:\n"
    "Name-2 class: {name_class}\n"
    "Search name used: {search_name}\n"
    "Segment: {segment}\n"
    "Why the pipeline found nothing (class {af_class}): {af_why}\n"
    "Hosts already tried:\n{hosts_tried}\n"
    "VAT IDs on our other records of this entity: {sibling_vat_ids}"
)

AGENT_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["vat_id", "source_url", "evidence_quote", "attributed_to", "published_address",
                 "confidence", "relation", "pages_opened", "notes"],
    "properties": {
        **{k: {"type": ["string", "null"]} for k in (
            "vat_id", "source_url", "evidence_quote", "attributed_to", "published_address", "notes")},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "relation": {"type": "string", "enum": ["same_entity", "renamed", "successor", "parent", "not_found"]},
        "pages_opened": {"type": "array", "items": {"type": "string"}},
    },
}


class AgentLookup(WebSearchLookup):
    """The agentic pass. *context*: per customer, the pipeline's knowledge
    (``name_class``, ``search_name``, ``af_class``, ``af_why``,
    ``hosts_tried`` as lines, ``sibling_vat_ids``)."""

    def __init__(self, settings: Any, vies: Any, *, context: dict[str, dict[str, Any]] | None = None,
                 **kw: Any) -> None:
        super().__init__(replace(settings, vat_web_query_mode="hybrid"), vies, **kw)
        self.context = context or {}
        self._agent = replace(self.limits, max_searches=MAX_SEARCHES, max_opens=MAX_OPENS,
                              max_output_tokens=MAX_OUTPUT_TOKENS, schema=AGENT_SCHEMA, cache_key="vat_agent")

    @property

```

---

## Appendix C. Development log: `docs/VAT_VALIDATION.md` (verbatim)

Chronological. Later dated sections override earlier ones; sections 1-11 of this brief override this log. Headings are shifted down two levels.

### VAT ID validation (VIES)

Status: implemented in `eae30f5` (2026-10-18). Standalone: **not** wired into `/enrich`,
ADF or DATAshaper, and nothing in enrichment, issue detection, dedup or scoring reads its
output.

| Piece | Location |
|---|---|
| Normaliser + offline format check | `vat/normalize.py` |
| VIES client | `vat/vies_client.py` |
| Name comparison | `vat/name_match.py` |
| Row logic, fan-out, summary | `vat/validate.py` |
| Endpoints `POST /api/validate/vat` (JSON), `POST /api/validate/vat/file` (XLSX) | `api/vat_routes.py` (registered in `api/app.py`) |
| Workbook handling (shared by the file endpoint and the runner) | `vat/xlsx.py` |
| Excel runner (CLI) | `tools/vat_validate_xlsx.py` |
| Fixture | `tests/fixtures/vat/VAT_TEST_EXPORT_30.xlsx` (sheets `Data`, `Test_Notes`) |
| Tests | `tests/test_vat_*.py`, `tests/mocks/vies_mock.py` |

---

#### 1. What VIES provides

Source: the European Commission's VIES REST API, contract `swagger_publicVAT.yaml`
("Vies on-the-Web Endpoint" 1.0.0), the SOAP WSDL's fault descriptions, and the VIES web
application's own message texts. Field names below were read from those documents and
checked against live responses on 2026-10-15. Nothing is taken from memory.

Base URL `https://ec.europa.eu/taxation_customs/vies/rest-api` (`VIES_API_BASE`).

| Endpoint | Used | Notes |
|---|---|---|
| `POST /check-vat-number` | yes | The lookup. |
| `POST /check-vat-test-service` | tests only (shapes) | Fixed answers per `vatNumber`: `100` valid, `200` invalid, `201`–`503` each error code. |
| `GET /check-status` | no | Per member state `availability`: `Available` / `Unavailable` / `Monitoring Disabled`. |
| `GET /ms/{cc}/vat/{n}` | **no** | Works, but is **not in the contract** and uses other field names (`isValid`, `userError`). |

**Request (`CheckVatRequest`).** Fields: `countryCode`, `vatNumber`, `requesterMemberStateCode`,
`requesterNumber`, `traderName`, `traderStreet`, `traderPostalCode`, `traderCity`,
`traderCompanyType`. We send the first two, plus the requester pair when `VIES_REQUESTER_VAT`
is set. The trader fields are not sent.

**Answer (`CheckVatResponse`).** Fields: `countryCode`, `vatNumber`, `requestDate`
(date-time), `valid` (boolean), `requestIdentifier`, `name`, `address`, `trader*` and
`trader*Match` (`VALID` / `INVALID` / `NOT_PROCESSED`). `requestIdentifier` is what the VIES
web app labels the **Consultation Number**. It is empty unless a requester is sent.

**Failure (`CommonResponse`).** Fields: `actionSucceed: false` and `errorWrappers[]` of
`{error, message}`. **Errors arrive with HTTP 200**, so the body decides the outcome, never
the status code.

| `error` | Official meaning | Our state | Retried |
|---|---|---|---|
| `SERVICE_UNAVAILABLE` | network or web-application error, try again later | SERVICE_UNAVAILABLE | yes |
| `MS_UNAVAILABLE` | the member state's application is not replying | SERVICE_UNAVAILABLE | yes |
| `TIMEOUT` | no reply within the allocated time | SERVICE_UNAVAILABLE | yes |
| `GLOBAL_MAX_CONCURRENT_REQ`, `…_TIME` | too many concurrent requests overall (in the current timeslot) | SERVICE_UNAVAILABLE | yes |
| `MS_MAX_CONCURRENT_REQ`, `…_TIME` | same, for this member state | SERVICE_UNAVAILABLE | yes |
| `INVALID_INPUT` | invalid country code or empty VAT number | ERROR | no |
| `INVALID_REQUESTER_INFO` | invalid requester VAT number | ERROR | no |
| `VAT_BLOCKED` | the VAT number is currently blocked | ERROR | no |
| `IP_BLOCKED` | test service only; no published text | ERROR | no |

##### Limits

- **No published rate limit.** Limits show up only as the `*_MAX_CONCURRENT_REQ*` errors. The
  FAQ answers "Can I make batch requests?" with *"No. The system is designed for single
  requests."* The very first live FR request on 2026-10-15 came back
  `MS_MAX_CONCURRENT_REQ`. The client therefore defaults to:
  - concurrency 1 (`VIES_MAX_CONCURRENCY`);
  - a 500 ms minimum gap between the starts of any two HTTP calls, retries included
    (`VIES_MIN_INTERVAL_MS`);
  - at most 3 attempts (`VIES_MAX_ATTEMPTS`), with backoff of 1 s, then 2 s
    (`VIES_BACKOFF_SECONDS`).

  Pacing does not prevent per-member-state limits, because other VIES users share them. In
  the fixture run on 2026-10-18, `FR45775662257` returned `MS_MAX_CONCURRENT_REQ` on all three
  attempts even at 500 ms spacing. A re-run then recovered it (see §3, *Re-running*).
- **Maintenance windows per member state** (VIES help text). DE: *"Available from 05:00 AM
  to 11:00 PM"*. NL: unavailable Saturday 21:50 to Sunday 21:40. IT: unavailable from 20:00
  for 30–60 minutes, Monday to Saturday. A lookup outside a window is SERVICE_UNAVAILABLE.
  That says nothing about the data.
- **Validity is tied to a date.** INVALID means *"not valid at least on the date
  specified"*, so every result carries `requestDate`.
- **Terms of use, an open question for Bruker legal/compliance.** The VIES disclaimer forbids
  *"any other use and any extraction and use of the data which is not in conformity with the
  objective of this site"* and *"any retransmission of the contents … other than as far as
  is necessary to support the activity of a legitimate user (for example: to draw up their
  own invoices)"*. Checking our own customers' VAT IDs fits that purpose. Whether the returned
  name and address may be stored in MDM (and in the evidence cache) needs sign-off.
- **Scraping Impressum pages (missing-VAT lookup, Mode A).** The lookup fetches company web
  pages with the existing `search.page_fetcher.PageFetcher`. That fetcher identifies itself as
  `BrukerMDM-Enrichment/1.0` and **does not consult robots.txt**. This is the existing
  behaviour and was kept unchanged. Whether automated retrieval of Impressum pages is
  acceptable belongs in the same legal/compliance review as the VIES terms above.

##### The DE / ES name gap

For a valid German or Spanish number VIES returns `name: "---"` and `address: "---"`. In
VIES's words: *"if the name and/or address are replaced by '---', this means that the Member
State that issued the VAT number does not allow to disclose this information."* That is a
**correct, complete answer**, not a failed lookup:

- `VAT_VIES_Status = VALID`, `VAT_VIES_Name` / `VAT_VIES_Address` blank,
  `VAT_Name_Disclosed = FALSE`, `VAT_Name_Match = NOT_AVAILABLE` (`NAME_MATCH:NOT_DISCLOSED`).
- The `"---"` placeholder is never written out as a name.

On the fixture, all 17 valid DE rows and both ES rows came back this way. IE, FR, LU, SE and
AT returned names.

##### VIES ignores trader fields for Germany (confirmed live)

`CheckVatRequest` accepts `traderName`, `traderPostalCode`, `traderCity` (and street and company
type), and the answer has a `trader*Match` field for each. On 2026-10-19, three DE fixture
numbers (Bosch `DE811128135`, BASF `DE149145247`, Porsche `DE147799625`) were each sent twice:
once with the correct SAP name, postal code and city, and once with the name "Muster Handels
GmbH".

- **All six answers** were `valid: true`, with **every** `trader*Match = NOT_PROCESSED` and the
  trader fields echoed as `"---"`. The wrong name got exactly the same answer as the right one.
- No requester VAT ID was sent. Whether Germany processes the match for an authenticated
  requester is untested; that belongs with the BZSt eVatR follow-up.

**Consequence.** For a German VAT ID, VIES proves only that the number exists, never whose it
is. The client therefore does not send trader fields. In the missing-VAT lookup, the
Impressum's attribution (gate d) is the **only** link between a number and the customer's name,
which is why that gate is strict.

---

#### 2. Input format and the customer-level rule

The input has the same layout as the US SAP exports (`CA_EXPORT.xlsx` …): sheet `Data`, the 28
export columns, plus `VAT Registration No.` (KNA1-STCEG) as the last column. The validator reads
three columns: `Customer`, `Country/Region Key` and `VAT Registration No.`. It also reads `Name`
and `Name 2`–`Name 4` for the name comparison. Every other column is passed through unchanged.

- **Header matching** uses the upload reader's own rule (`api.routes._norm_header`: case,
  spacing and punctuation ignored) and the column names `EnrichmentRecord` already accepts.
  On top of that it accepts the export's bare `Name` header, which `EnrichmentRecord` does
  not.
- **The runner** opens sheet `Data`. If there is no such sheet it falls back to the active
  sheet and logs a warning.

##### Fan-out

The extract has one row per company code and sales organisation, but the VAT ID belongs to the
customer.

1. Every row is normalised and format-checked.
2. VIES is asked **once per unique normalised VAT across the whole batch**.
3. That answer is written to every row carrying the number. For a consistent customer, that
   is every row of the customer.

On the fixture, 30 rows and 26 FORMAT_OK rows produced 24 lookups. Robert Bosch's two rows
share one number, and `IE6388047V` appears on two customers.

##### Conflict

If one Customer's rows do not all carry the same normalised VAT, every row of that customer is
flagged `CUSTOMER_VAT_CONFLICT`, and **each row keeps the result for its own value. Nothing is
picked.** A blank on one row next to a value on another counts as a conflict. The same number
spelled differently (`DE136…` and `de 136 695 976`) does not.

Customer values are grouped exactly as read: whitespace-trimmed, with no zero-stripping or
padding, so `0099000006` and `99000006` are two customers.

---

#### 3. Result columns and states

Ten columns are appended after the input columns, in this order: `VAT_Normalized`,
`VAT_Format_Status`, `VAT_VIES_Status`, `VAT_VIES_Name`, `VAT_VIES_Address`,
`VAT_Name_Disclosed`, `VAT_Name_Match`, `VAT_Request_Date`, `VAT_Consultation_No`,
`VAT_Flags`.

##### `VAT_Format_Status`: offline (python-stdnum syntax + checksum)

| State | Meaning |
|---|---|
| `FORMAT_OK` | EU prefix, and stdnum accepts the syntax and checksum. The **only** state sent to VIES. |
| `FORMAT_ERROR` | EU prefix but stdnum rejects it (`FORMAT_ERROR:<reason>` in flags), **or** the value starts with letters that are not an EU prefix while the key is an EU country (`NON_EU_PREFIX`). |
| `MISSING` | No value, and the Country/Region Key is a VIES state (or blank). |
| `NOT_EU` | No EU prefix and the key is outside VIES, including a blank value for a non-EU customer. |

How values are normalised:

- **Separators.** Spaces, dots and dashes are removed and the value is uppercased.
- **Greece.** `GR` (as a prefix or as the key) becomes `EL`.
- **Northern Ireland.** A value that already says `XI`, or the key `XI`, is checked as
  Northern Ireland. A `GB` key never turns a bare number into `XI`.
- **Prefix from the Country/Region Key** is added **only when the value has no leading
  letters at all**. It is recorded as `PREFIX_FROM_COUNTRY_KEY`.
- **Prefix ≠ key** is flagged `PREFIX_COUNTRY_MISMATCH` and never rewritten.
- **Austria's `U`** is kept (`ATU…`) and never added.
- **The raw value** stays in `VAT Registration No.`.

##### `VAT_VIES_Status`

| State | Meaning |
|---|---|
| `VALID` | VIES answered `valid: true`. |
| `INVALID` | VIES answered `valid: false`. This is the **only** route to INVALID. |
| `SERVICE_UNAVAILABLE` | VIES could not answer: a transient code from §1, a network error or timeout, HTTP 5xx/429, or (`CACHE_FROZEN`) no recording. **Never reported as INVALID.** |
| `ERROR` | Not retryable: `INVALID_INPUT`, `INVALID_REQUESTER_INFO`, `VAT_BLOCKED`, `IP_BLOCKED`, an unknown code, a malformed body, or another 4xx. |
| `NOT_CHECKED` | The row was not FORMAT_OK and was never sent. |

The VIES error code, if any, goes to flags as `VIES:<code>`. A frozen-cache miss is
`VIES:FROZEN_CACHE_MISS`.

##### `VAT_Name_Match`: deterministic, no LLM

`VAT_VIES_Name` is compared with `Name` alone and with `Name` + `Name 2`–`Name 4` joined,
and the better result is kept. Both sides are normalised the same way, in this order:

1. German transliteration (ä→ae …).
2. `dedup.signatures.normalize_key`.
3. Dotted single letters re-joined (`S.à r.l.` → `sarl`).
4. Legal forms dropped wherever they stand. The lists come from the GLEIF name check and the
   dedup suffix list, plus a few EU forms neither has.

| State | Rule (`NAME_MATCH:<rule>` in flags) |
|---|---|
| `MATCH` | `EXACT` (Name alone) or `EXACT_WITH_NAME_2_4`: same word set, in any order. |
| `PARTIAL` | `TOKEN_SUBSET` (one side's words contain the other's) or `FUZZY` (token_sort_ratio ≥ 88, the `LEI_NAME_MATCH_THRESHOLD` value). |
| `MISMATCH` | `NO_MATCH`. Acronyms are not expanded: `BMW AG` vs `Bayerische Motoren Werke AG` is a MISMATCH. |
| `NOT_AVAILABLE` | `NOT_DISCLOSED` (DE/ES `---`), `VIES_NOT_VALID` (any status other than VALID, including NOT_CHECKED), or `NO_INPUT_NAME`. |

PARTIAL and MISMATCH are prompts for a steward. Nothing rewrites a name. On the fixture, all 8
disclosed names (IE ×3, FR, LU ×2, SE, AT) were `MATCH`.

##### `VAT_Flags`

The flags are joined with `"; "`, the same separator Flag Codes uses elsewhere in the pipeline.
They are column values, **not** issue codes:

`PREFIX_FROM_COUNTRY_KEY`, `PREFIX_COUNTRY_MISMATCH`, `NON_EU_PREFIX`, `NO_PREFIX_NO_COUNTRY`,
`FORMAT_ERROR:<reason>`, `VIES:<code>`, `NAME_MATCH:<rule>`, `CUSTOMER_VAT_CONFLICT`.

##### Caching and frozen mode

- **What is recorded.** Only a VALID or INVALID response body is recorded. The raw body is
  stored, not the decision, which is the same practice as the ROR/GLEIF clients.
- **Where.** Answers go to `<EVIDENCE_CACHE_DIR>/vies/`, keyed on the normalised VAT (plus the
  requester when one is configured), e.g. `vies_de811569869.json`. Entries are never
  overwritten.
- **What is never recorded.** Every other outcome is raised before the store is touched, so a
  "member state unavailable" is never replayed.
- **A separate store.** The client uses its own `EvidenceCache` instance, not the process-wide
  one, so a VAT request cannot swap the store a concurrent `/enrich` is using.
- **Frozen mode.** `CACHE_FROZEN` (`--frozen` on the runner) replays recordings only. A miss
  makes no network call.

##### Configuration (`config.py`, `.env.example`)

| Setting | Default |
|---|---|
| `VIES_API_BASE` | `https://ec.europa.eu/taxation_customs/vies/rest-api` |
| `VIES_TIMEOUT_SECONDS` | 15 |
| `VIES_MAX_ATTEMPTS` | 3 |
| `VIES_BACKOFF_SECONDS` | 1.0 |
| `VIES_MAX_CONCURRENCY` | 1 |
| `VIES_MIN_INTERVAL_MS` | 500 (0 disables pacing; cache replays are never paced) |
| `VIES_REQUESTER_VAT` | empty |
| `VAT_GROUNDED_FALLBACK` | false |
| `VAT_LOG_COLUMNS` | false (research log + `VAT_Found_Entity_Status`, see 3b; on, after the reviewer's columns) |
| `VAT_OUTPUT_COLUMNS` | `review` (the reviewer's columns, see "Output columns for the reviewer"); `full` = the technical columns as before 2026-11-08 |
| `VAT_LOOKUP_CONCURRENCY` | 20 (was 5; see "Throughput and large uploads") |
| `VAT_AZURE_TPM` / `VAT_AZURE_RPM` | empty: from Azure's `x-ratelimit-limit-*` headers. Per deployment: `VAT_AZURE_TPM_<NAME>` |
| `VAT_AZURE_QUOTA_SHARE` | 0.85 |
| `VAT_AZURE_MAX_RETRIES` | 6 (HTTP 429 / 5xx; all waits together at most 180 s) |
| `VAT_WEB_RETRY_BACKOFF_SECONDS` | 5 (first wait without a Retry-After; doubled per retry, at most 60 s) |
| `PAGE_FETCH_MAX_CONCURRENCY` | 20 |
| `VAT_AGENT_CONCURRENCY` | 10 (was 1). At `VAT_AGENT_BUDGET_USD`, calls already under way finish: the spend can pass the cap by up to this many agent calls |
| `VAT_JOB_DIR` | `logs/vat_jobs` (on Azure: a folder under `/home`) |
| `VAT_JOB_CHUNK_ROWS` | 200 |
| `VAT_JOB_LEASE_SECONDS` | 900 |

`VIES_REQUESTER_VAT` is optional and never hardcoded. When it is set, VIES issues a
consultation number. A malformed value fails loudly (HTTP 500 from the endpoint) instead of
failing every row as `INVALID_REQUESTER_INFO`.

`VAT_GROUNDED_FALLBACK` controls the grounded lookup (SerpAPI + page read). When it is false
(the default) that lookup never runs, and every missing-VAT lookup is `llm_web`. Asking for
`lookup_missing=grounded` or `with_grounded=true` returns 400. When it is true, both are allowed,
and `llm_web` falls back to the grounded lookup for a customer whose web search could not run
(`LOOKUP_INCOMPLETE` with `LLM_UNAVAILABLE`). Such a row has `VAT_Lookup_Mode` GROUNDED and the
flag `GROUNDED_FALLBACK`. A `NOT_FOUND` from `llm_web` is final.

##### Usage

```
lookup_missing (query or body) = llm_web (default) | none | grounded (needs VAT_GROUNDED_FALLBACK)

POST /api/validate/vat   {"rows": [{"Customer": "...", "Country/Region Key": "DE", "VAT Registration No.": "...", ...}]}
→ {"rows": [... input columns + VAT_* ...], "columns": [...], "summary": {...}}

POST /api/validate/vat/file   multipart form field "file" = export.xlsx (.xlsx / .xlsm)
→ export_vat.xlsx   headers: X-VAT-Rows, X-VAT-Service-Unavailable (unique VAT IDs VIES did not answer)
  400: wrong extension / empty / unreadable file    422: no header row, no data rows, or no VAT column

POST /api/validate/vat/llm-only-experiment        JSON, as /api/validate/vat with lookup_missing=llm_only
POST /api/validate/vat/llm-only-experiment/file   XLSX, as /api/validate/vat/file with lookup_missing=llm_only
→ export_vat_llm_only_experiment.xlsx   (EXPERIMENT ONLY — not for production use, never written to SAP)
  ?with_grounded=true (default false; needs VAT_GROUNDED_FALLBACK) also runs the grounded lookup (SerpAPI + page read) on the same rows:
  VAT_Found_* = LLM only · VAT_Grounded_Value/_Status/_Source_URL/_Attributed_To/_Evidence/_Date/_Flags
  = SERP + read · VAT_LLM_vs_Grounded = SAME | DIFFERENT | LLM_ONLY | GROUNDED_ONLY | NEITHER
  (normalised VAT IDs compared). ?with_grounded=false (default): LLM only, no SerpAPI cost.
  ?scope=all (default on the experiment endpoints): every DE row is looked up, VAT ID or not, and
  each proposal is scored against the SAP value — VAT_Found_vs_SAP / VAT_Grounded_vs_SAP =
  MATCHES_SAP | DIFFERS_FROM_SAP | NO_PROPOSAL | SAP_BLANK. The lookups never see the SAP value
  (name and address only). ?scope=missing: only rows with no VAT ID (the default elsewhere).
  LLM-only prompt v2: the model always gives its best candidate, with confidence "low" when unsure;
  null only when it has no candidate at all.

python tools/vat_validate_xlsx.py export.xlsx [--out export_vat.xlsx] [--frozen] [--cache-dir DIR]
```

The file endpoint and the runner produce the same workbook, because both call
`vat.xlsx.validate_workbook`.

The file endpoint and the runner write a copy of the workbook with the `VAT_*` columns appended and a `Summary` sheet.
It never modifies the input file. The Summary sheet holds:
- counts per state and per Country/Region Key;
- unique customers against rows;
- the number of unique VAT IDs and rows that came back `SERVICE_UNAVAILABLE`;
- a table of every VAT ID VIES did not answer (`SERVICE_UNAVAILABLE` or `ERROR`), with its
  error code and row count.

##### Re-running

SERVICE_UNAVAILABLE and ERROR answers are never cached, so re-running the runner with the same
input and the same cache directory asks VIES again for **only** the numbers listed in that
table. Every VALID and INVALID answer is replayed from the cache, with no call and no pacing
delay.

On 2026-10-18 the first run had 24 lookups (26 HTTP calls) and 1 SERVICE_UNAVAILABLE
(`FR45775662257`). The re-run made 1 HTTP call, replayed the other 23 answers from cache, and
ended with 0 SERVICE_UNAVAILABLE.

##### Missing-VAT lookup: search ranking varies day to day

Mode A's result depends on which pages SerpAPI ranks first, and that ranking changes from one
day to the next. For the identical query "Robert Bosch GmbH Gerlingen-Schillerhoehe Impressum":

- **2026-10-19:** the first Impressum read was `bosch-home.com`, the Impressum of a different
  Bosch company (Robert Bosch Hausgeräte GmbH, `DE129274235`).
- **2026-10-20:** the first result was `bosch.de/impressum` (Robert Bosch GmbH, `DE811128135`).

Two mechanisms deal with this:

1. **The frozen cache makes a run reproducible.** Every search, page and LLM answer is
   recorded, so a `CACHE_FROZEN` re-run reads exactly what the original run saw, whatever the
   search engine ranks today. The 2026-10-19 results were *not* reproducible: they were lost
   to the cache path problem (§5, follow-up #9), which is what the write guard now detects.
2. **Continuing past `ATTRIBUTION_PARTIAL` makes the result robust to ranking.** The lookup
   stops only at a verified exact attribution. A subsidiary's Impressum ranked first is read,
   rejected as PARTIAL and listed as a candidate, and the lookup moves on to the next result
   URL (within the 6-fetch cap). Whether the right page is reached therefore depends much less
   on whether it was ranked first.

---

#### 3a. Missing-VAT lookup experiment (fixture, 2026-10-20)

**Setup.** Code at `4bd61d1`, run live on 2026-10-20:

```
python tools/vat_lookup_compare.py tests/fixtures/vat/VAT_TEST_EXPORT_30.xlsx --rows 2-18 --exclude 9 --cache-dir <short path>
```

- **Rows:** 16 DE rows with a confirmed VAT ID (rows 2–18, without the Bayer typo row 9) were
  blanked. That is 15 customers, since Bosch has two rows. Universität Stuttgart (row 20, blank
  in the export) was looked up as the "genuinely missing" case.
- **Totals count each customer once.**
- **Ground truth came from the companies' Impressum pages** ("Impressum confirmed" in
  Test_Notes). Mode A searches for those same pages, so its recall here is **not an
  independent measure**. It measures whether the pipeline finds and reads the page, not whether
  the page is right.
- Mode B (LLM only) is an **experiment, not for production use**; its answers are never written
  to SAP.

##### Results per customer

| Row | Customer | Ground truth | Mode A value | Mode A status / reason | Mode A outcome | Mode B value (confidence) | Mode B outcome |
|---|---|---|---|---|---|---|---|
| 2 | Google Germany GmbH | DE811569869 | IE6388047V | UNVERIFIED, `ATTRIBUTION_PARTIAL` ("Google Ireland Limited") | wrong, VIES VALID | – (low) | null |
| 3 | Siemens Aktiengesellschaft | DE129274202 | DE813775002 | UNVERIFIED, `ATTRIBUTION_PARTIAL` ("Siemens Finance & Leasing GmbH") | wrong, VIES VALID | DE129274202 (high) | correct |
| 4–5 | Robert Bosch GmbH | DE811128135 | DE811128135 | VERIFIED, MATCH | correct | DE147867203 (high) | wrong, check digit fails |
| 6 | Zalando SE | DE260543043 | – | NOT_FOUND, `NO_IMPRESSUM_PAGE` | null | DE260543043 (high) | correct |
| 7 | BASF SE | DE149145247 | DE149145247 | VERIFIED, MATCH | correct | DE143558497 (high) | wrong, check digit fails |
| 8 | Bayer AG | DE123659859 | – | NOT_FOUND, `NO_IMPRESSUM_PAGE` | null | DE123456789 (low) | wrong, check digit fails |
| 10 | Bayerische Motoren Werke AG | DE129273398 | – | NOT_FOUND, `NO_IMPRESSUM_PAGE` | null | DE129273398 (high) | correct |
| 11 | Merck KGaA | DE811850788 | – | NOT_FOUND, `NO_IMPRESSUM_PAGE` | null | DE111671602 (high) | wrong, check digit fails |
| 12 | Henkel AG & Co. KGaA | DE119429301 | DE119429301 | VERIFIED, MATCH | correct | DE119429301 (high) | correct |
| 13 | Boehringer Ingelheim International GmbH | DE811138149 | – | NOT_FOUND, `NO_VAT_ID_ON_PAGE` | null | – (low) | null |
| 14 | adidas AG | DE132490588 | DE132490588 | VERIFIED, MATCH | correct | DE132273796 (high) | wrong, check digit fails |
| 15 | Continental AG | DE115645799 | DE115645799 | VERIFIED, MATCH | correct | DE115648799 (high) | wrong, check digit fails |
| 16 | Dr. Ing. h.c. F. Porsche AG | DE147799625 | – | NOT_FOUND, `NO_VAT_ID_ON_PAGE` | null | DE147799625 (high) | correct |
| 17 | QIAGEN GmbH | DE121386819 | DE121386819 | UNVERIFIED, `ATTRIBUTION_PARTIAL` ("QIAGEN GmbH - Germany") | correct | DE121386819 (high) | correct |
| 18 | Fresenius SE & Co. KGaA | DE114152311 | DE114152311 | VERIFIED, MATCH | correct | DE811119184 (high) | wrong, check digit fails |
| 20 | Universität Stuttgart | (none in SAP) | DE147794196 | VERIFIED, MATCH | no truth | DE147794196 (medium) | no truth |

Mode A read exactly 1 candidate page per customer wherever it read one.

##### Totals (15 customers)

| Outcome | Mode A (grounded) | Mode B (LLM only) |
|---|---|---|
| Correct | 7 | 6 |
| Wrong but passes format | 2 | 0 |
| … of which VIES VALID (someone else's real number) | **2** | 0 |
| Wrong, fails check digit | 0 | **7** |
| Null (no value proposed) | 6 | 2 |
| FOUND_VERIFIED | 6 | n/a (never verified) |
| **FOUND_VERIFIED but wrong** | **0** | n/a |

**Mode B correctness by self-reported confidence**

| Confidence | Customers | Correct | Wrong, VIES VALID | Wrong, bad check digit | Null |
|---|---|---|---|---|---|
| high | 12 | 6 | 0 | 6 | 0 |
| medium | 0 | – | – | – | – |
| low | 3 | 0 | 0 | 1 (`DE123456789`, a placeholder) | 2 |

##### What the results show

- **Mode A's strict attribution gate did its job.** Both wrong-but-VIES-valid numbers Mode A
  read stayed `FOUND_UNVERIFIED`. In both cases the search ranked a page that names a
  *different* legal entity first:
  - **Google:** google.de's own Impressum names Google Ireland Limited, `IE6388047V`.
  - **Siemens:** the Impressum of Siemens Finance & Leasing GmbH.

  None of the 6 verified results is wrong. Without the strict gate (the original MATCH-or-PARTIAL
  rule), both wrong numbers would have reached VIES, been called VALID and been reported as
  verified.
- **The strict gate also cost one correct result.** QIAGEN's page attributes the number to
  "QIAGEN GmbH - Germany". The extra country word makes it PARTIAL, so a correct ID stays
  unverified.
- **Every one of Mode B's wrong answers failed the check digit.** That's 7 of 7, and 6 of them
  were stated with **high** confidence. None was VIES-valid, because none was even format-valid.
  The check digit alone caught all of them. Its 6 correct answers are all large, well-known
  companies. Self-reported confidence carries no usable signal: "high" was right exactly half
  the time (6 of 12).
- **Universität Stuttgart is not a "no answer" case.** Its Impressum
  (uni-stuttgart.de/impressum) states *"Umsatzsteuer-Identifikationsnummer DE 1477 94 196"*.
  - Mode A read and verified it: attribution MATCH, VIES VALID.
  - Mode B gave the same number at medium confidence, and VIES says VALID.
  - The SAP record lacks the number, but the university has one. The fixture's expectation that
    "nothing found" is the right answer does not hold.
- **Why Mode A found nothing for six companies.** None of these is a reader or gate failure:
  - **Bot protection.** bayer.com, merckgroup.com and porsche.com answered **403** (Akamai and
    similar). Both BMW sites **timed out**.
  - **JavaScript-only pages.** Boehringer Ingelheim's Impressum pages returned 200 with **no
    text**.
  - **Search noise plus URL selection.** For Zalando, the "…Impressum" query returned Berlin city
    pages. The correct Impressum URLs came only from the "…Imprint" query, and the lookup takes
    the first 3 URLs of the merged list, so it never reached them. For Bayer, the first result was
    the football club's shop, whose fallback probes used up the fetch budget.

##### Cost

| | Mode A | Mode B | Audit |
|---|---|---|---|
| SerpAPI queries (network) | 32 (32) | – | – |
| Page attempts / fetched / recorded | 53 / 53 / 34 | – | – |
| LLM calls (network) | 13 reads | 16 asks | – |
| VIES checks | 7 | 7 | 2 |
| Cache write failures | 0 | 0 | 0 |
| Wall time | 371 s | 38 s | – |

##### Reproducibility (frozen re-run of the same command)

The frozen re-run made **0** network calls of any kind. Mode A replayed 34 pages and 13 LLM
answers, and Mode B replayed 16 LLM answers. The totals, the confidence table, every Mode B row
and 12 of 16 Mode A rows were identical.

**4 Mode A rows differ** (Bayer, BMW, Merck, Porsche): live `NOT_FOUND`, frozen
`LOOKUP_INCOMPLETE` / `FROZEN_CACHE_MISS`. These are the customers whose pages returned 403 or
timed out. Such answers are not recorded (only 2xx and 404/410 are, by design), so the frozen run
had nothing to replay.

The frozen label is the accurate one: the live run reported "blocked / timed out" as *not
found*, when it should have said *could not look*. This is an open fix (§5, follow-up 10).

---

#### 3b. Alignment with the design paper (2026-10-27)

The design paper *Designing an Agent to Identify German VAT Identification Numbers*
proposes a Copilot Studio agent. Here the same design runs in code: an LLM with web
search (`llm_web`) or SerpAPI plus a page reader (`grounded`), with deterministic
controls around it. Where the code and the paper differed, the code was changed to
follow the paper.

| Paper section | Where in the code |
|---|---|
| §2 inputs | `LookupInput`. Mandatory: name, street, postal code, city, country. A missing one is flagged `INPUT_INCOMPLETE:<fields>`; the lookup still runs, but an address it cannot match cannot be AUTO_ACCEPT. Optional, read when the workbook has them: `Website` (or a non-mailbox `Email` domain) and `Commercial Register Number` (header aliases in `vat/validate.py`). `Customer` is the Business Partner ID. |
| §3 Find, Match, Verify | Find: search or web search plus page read. Match: gates a–f and `vat/entity_match.py`. Verify: VIES. Unchanged. |
| §4 source hierarchy | `VAT_Found_Source_Type`: `COMPANY_IMPRESSUM` / `COMPANY_WEBSITE` (tier 1), `OFFICIAL_REGISTER` (tier 2: handelsregister, unternehmensregister, bundesanzeiger, bzst, e-justice), `SECONDARY` (tier 4: directories, or a page nothing ties to the company). **Changed:** `unternehmensregister` was on the directory list; it is now tier 2. Secondary sources are never AUTO_ACCEPT. |
| §5 entity resolution | **Legal form preserved (changed):** a name match whose legal form differs ("Example GmbH" / "Example AG", "… GmbH" / "… GmbH & Co. KG") is `ATTRIBUTION_PARTIAL` with `LEGAL_FORM_DIFFERS:<record>/<page>`. Before this change legal forms were dropped and such names matched. Parent, subsidiary and branch handling is unchanged (PARTIAL / QUALIFIER); a branch (`VAT_Found_Name_Match = BRANCH`) is no longer eligible for acceptance. **New:** street + house number check (`str.` / `Straße` folded), address level `EXACT` / `STRONG` / `PARTIAL` / `MISMATCH` / `UNKNOWN`, domain and register-number checks (`DOMAIN_*`, `REGISTER_*` flags), and `VAT_Found_Match_Score` using the paper's weights (name 30, street 25, postal code 15, city 10, domain 10, register 10; elements the record lacks are left out of the denominator). The score is reported only; rules decide. |
| §6 extraction | `entity_match.vat_candidates`: the paper's `\bDE[ ]?[0-9]{9}\b`, widened to grouped digits ("DE 1477 94 196"); a trailing `-NNNNN` (W-IdNr.) is excluded. Every other ID on the page is listed in `OTHER_VAT_IDS_ON_PAGE`. The prompts now also exclude the W-IdNr., LEI, GLN and D-U-N-S number. |
| §8 decision model | `vat.lookup.decide` → `VAT_Found_Decision`. **AUTO_ACCEPT**: VIES VALID, tier PROPOSED, name EXACT (not branch or partial, same legal form), address EXACT or STRONG, primary source, cited page actually visited, no `REGISTER_MISMATCH`. **CONFLICT**: `AMBIGUOUS_SAME_NAME` or the new `CONFLICTING_SOURCES` (another gate-passing primary source naming the exact entity, at an address that isn't someone else's, gives a different number). **NOT_FOUND**: no value (NOT_FOUND or LOOKUP_INCOMPLETE). **MANUAL_REVIEW**: every other candidate. `VAT_Found_Confidence`: `VERIFIED` / `HIGH_CONFIDENCE` / `MEDIUM_CONFIDENCE` / `LOW_CONFIDENCE` / `NOT_FOUND` / `CONFLICT` (the review tiers of Step AF-2; `NOT_VERIFIED` before). AUTO_ACCEPT only marks a candidate as *eligible*; nothing writes `VAT Registration No.`. |
| §9 agent instructions | `vat/prompts.py`, `vat_llm_web` **v2** and `vat_impressum_read` **v2**: source order, exact legal entity (legal form; no parent, subsidiary, sister, branch, predecessor or successor), identifier exclusions, never guess or derive, null when nothing is found. |
| §10 audit trail | New columns: `VAT_Found_Page_Title`, `VAT_Found_Published_Address` (kept only when it appears on the page), `VAT_Found_Name_Match`, `VAT_Found_Address_Match`, `VAT_Found_Validation_Status` and `VAT_Found_Validation_Date` (VIES's answer and `requestDate`; `NOT_CHECKED` when a gate failed), `VAT_Found_Agent_Version` (mode, prompt version, model). Existing columns: source URL, legal name found (`Attributed_To`), evidence, research date. The side-by-side `VAT_Grounded_*` set carries the same columns. KPIs (summary `lookup.kpis` and the Summary sheet), counted per customer: discovery, verification, auto-accept, manual-review and not-found rates, conflicts, conflicts by source type, average seconds per customer. |

**Not in code, by design.** The paper's human reviewer and steward decision are a
steward's own columns. The workbook passes them through untouched. The pipeline doesn't
add them, because a re-run overwrites every result column it owns. SAP write-back after
approval (§7, §12.8) stays out of scope.

**Prompt v2 and frozen caches.** The prompt text is part of the LLM cache key. Recordings
made with v1 (the 2026-10-20 experiment, §3a) do not replay under v2: a `CACHE_FROZEN`
re-run of that experiment needs the code at `4bd61d1`.

##### Decision rules, issue codes and source classes (2026-11-02)

These replace the §5 address levels, the §4 source tiers and the §8 decision rules in the
table above. Nothing here writes to SAP.

**Address match** (`VAT_Found_Address_Match`, `entity_match.address_match`). Exactly four
values. Street folding is unchanged (Hauptstr. 5 = Hauptstrasse 5 = Haupt-Straße 5).

| Value | Meaning |
|---|---|
| `EXACT` | street, house number, postcode and city all match |
| `SAME_TOWN` | the town matches (city, or postcode); street, house number or postcode differ, SAP lacks street or postcode, or the page shows only a PO box with that town |
| `DIFFERENT_TOWN` | the page states an address in another town |
| `NO_ADDRESS` | no address on the page |

A city match counts only inside a `<postcode> <town>` block when the page has one, so
"Amtsgericht Stuttgart" (the registry court) is not read as the company's town.

**Why SAME_TOWN is allowed.** In the full DE export (22,259 in-scope records), records
that share name, legal form and town carry different VAT IDs in 2.9 % of groups, against
1.9 % for name, legal form and street. A same-name company in the same town is nearly as
rare as one at the same street, so the town is the test. The street still decides between
`EXACT` and `SAME_TOWN`, and `SAME_TOWN` is reported as `ADDRESS_DIFFERS`.

**Name and legal form** (`entity_match.name_match`).

* Spelled-out and abbreviated forms are folded before comparing: Aktiengesellschaft = AG,
  Gesellschaft mit beschränkter Haftung = GmbH, gemeinnützige GmbH = gGmbH,
  "GmbH + Co. KG" = "GmbH & Co KG" = "GmbH & Co. KG", "e. V." = "e.V.", "SE & Co. KGaA".
* SAP's 35-character cut is read back: "… Gm" / "… Gmb" → GmbH; "… GmbH & Co" /
  "… GmbH & C" → GmbH & Co. KG; "… A" is ambiguous and counts as no form.
* Legal form on both sides and different → `PARTIAL`, `LEGAL_FORM_DIFFERS:<sap>/<page>`.
  GmbH vs GmbH & Co. KG is the key case: separate companies, often at the same address.
* Legal form missing in SAP, stated on the page → `EXACT`, `LEGAL_FORM_MISSING_IN_SAP`.
  If the page shows a second entity of the same name with another form, or names the
  GmbH as the Komplementär of a KG → `SISTER_ENTITY_ON_PAGE` (forces review).
* Site words (Werk, Niederlassung, Zweigniederlassung, Standort, Betriebsstätte, Filiale)
  are stripped; a match of the rest is `EXACT` with `BRANCH`. A branch of a German company
  has no VAT ID of its own, so `BRANCH` does not block.
* Department words in Name 2–4 (Institut, Lehrstuhl, Fakultät, Fachbereich, Klinik für,
  Abteilung, Zentrum, Labor, Professur, Arbeitsgruppe) do not change the match: the legal
  entity is Name 1. Those records carry `DEPARTMENT`.

**Source class** (`VAT_Found_Source_Class`; `VAT_Found_Source_Type` keeps the detail),
by who published the number:

| Class | Source types | What |
|---|---|---|
| A | `COMPANY_IMPRESSUM`, `COMPANY_WEBSITE`, `PEPPOL_DIRECTORY` | the entity on its own domain (Impressum, and any other page or PDF there: AGB, Lieferanteninformationen, Einkauf, Rechnungsanschrift, order forms); its Peppol Directory entry (scheme 9930 = DE VAT ID, `vat/peppol.py`) |
| B | `PROCUREMENT_PROFILE`, `OFFICIAL_FILING` | its buyer profile on a Vergabeportal (bund.de, DTVP, evergabe, subreport); a Bundesanzeiger / Unternehmensregister filing |
| C | `AGGREGATOR`, `WEB_ARCHIVE` | directories (northdata, firmenwissen, companyhouse, implisense, wer-zu-wem …) and pages nothing ties to the entity; archive snapshots, used only for records with the Central Deletion Flag (snapshot date in `VAT_Found_Date`) |

The Handelsregister confirms the legal name and HRB but publishes no VAT IDs. Marketplaces
and social networks are never a source (`REJECTED_SOURCE`); neither is an archive snapshot
for an active record (`ARCHIVE_NOT_USED`). A page off the customer's own domain counts as
its website when the domain is named after it (a distinctive name word, or its initials:
`uni-stuttgart.de`, `kit.edu`).

**Decision** (`vat/lookup.py`). AUTO_ACCEPT / VERIFIED needs ALL of: source class A or B;
name `EXACT` (a branch is fine); address `EXACT` or `SAME_TOWN`; VIES VALID or VIES
unavailable; the number on a page our own code fetched; none of `REGISTER_MISMATCH`,
`CONFLICTING_SOURCES`, `SISTER_ENTITY_ON_PAGE`. Every other candidate is MANUAL_REVIEW;
`CONFLICTING_SOURCES` is CONFLICT; no candidate is NOT_FOUND. A source the search did not
list as visited is recorded (`SOURCE_NOT_VISITED`) but no longer blocks: our own fetch of
the page is the visit.

| | Before | Now |
|---|---|---|
| Address | EXACT or STRONG (postcode + street or city) | EXACT or SAME_TOWN |
| Branch ("… GmbH Werk Hamburg") | MANUAL_REVIEW | AUTO_ACCEPT, `BRANCH` |
| Form missing in SAP | AUTO_ACCEPT, no note | AUTO_ACCEPT, `LEGAL_FORM_MISSING_IN_SAP`; review with a sister entity |
| "Gesellschaft mit beschränkter Haftung" vs GmbH | MANUAL_REVIEW (bug) | AUTO_ACCEPT |
| SAP name cut at 35 characters | MANUAL_REVIEW | read back; AUTO_ACCEPT when the rest matches |
| VIES not answered yet | MANUAL_REVIEW | AUTO_ACCEPT, `VIES_PENDING`, queued; never written to SAP until VALID (Step AF-2) |
| Source on the own domain, not the Impressum | only with a website column | also when the domain is named after the entity |
| Peppol Directory, Vergabeportal | not used | class A / class B |
| Source not in the search's visited list | MANUAL_REVIEW | recorded, not blocking |

MANUAL_REVIEW confidence: the review tier (Step AF-2, below): HIGH, MEDIUM or LOW.

**Issue codes** (`VAT_Found_Issues`, `VAT_Grounded_Issues` side by side). Semicolon-
separated, on every result including AUTO_ACCEPT; empty = clean match. Additive: the
decision never suppresses one.

| Group | Codes | Blocking |
|---|---|---|
| Address (at most one) | `ADDRESS_DIFFERS` (SAME_TOWN), `ADDRESS_MISMATCH` (DIFFERENT_TOWN), `ADDRESS_NOT_ON_PAGE` (NO_ADDRESS) | the last two |
| Name / entity | `LEGAL_FORM_DIFFERS:<sap>/<page>`, `LEGAL_FORM_MISSING_IN_SAP`, `BRANCH`, `NAME_PARTIAL`, `SISTER_ENTITY_ON_PAGE`, `DEPARTMENT` | `LEGAL_FORM_DIFFERS`, `NAME_PARTIAL`, `SISTER_ENTITY_ON_PAGE` |
| Source / verification | `SECONDARY_SOURCE`, `OTHER_VAT_IDS_ON_PAGE`, `EXTRACTED_BY_PATTERN`, `VIES_INVALID`, `VIES_UNAVAILABLE`, `VIES_PENDING`, `CONFLICTING_SOURCES`, `REGISTER_MISMATCH`, `EVIDENCE_NOT_VERIFIED` | all but `OTHER_VAT_IDS_ON_PAGE`, `EXTRACTED_BY_PATTERN`, `VIES_UNAVAILABLE` and `VIES_PENDING` |
| Entity relation (Step AF) | `SUB_ENTITY`, `PARENT_ENTITY` (umbrella list), `PARENT_ENTITY_CANDIDATE`, `SUCCESSOR_CANDIDATE` | the last two |
| SAP record | `SAP_VAT_OUTDATED`: SAP had a VAT ID, the lookup found a different, VIES-valid one from a class A/B source naming the exact entity (scope `all` only) | no |

`NAME_PARTIAL` also covers a name with nothing in common. Two codes were added to the
specified list so the invariant below can hold: `REGISTER_MISMATCH` (it blocks, so it must
be visible) and `EVIDENCE_NOT_VERIFIED` (the quote or digits are not on the page, the
number is malformed, the page could not be fetched, or there is no page). The invariant,
tested: every MANUAL_REVIEW or CONFLICT row carries at least one blocking code; no
AUTO_ACCEPT row carries one. The run KPIs count each code per decision
(`summary.lookup.kpis.issues`, and the Summary sheet).

**One lookup per legal entity.** Key: normalised Name 1 + legal form + city
(`lookup.entity_key`). The result is copied to every record with that key; each record
keeps its own address match (EXACT or SAME_TOWN against the same page), but the decision
is the entity's. The 9,484 missing-VAT records of the DE export are 7,202 such keys
(1,602 university records → 338 universities).

**VIES queue** (`vat/vies_retry.py`; out of the lookup path since Step AF-2, below). No
candidate is sent to VIES while it is looked up. It leaves the lookup with
`VAT_Found_Validation_Status = PENDING` and `VIES_PENDING`, keeps its decision, and is
queued in `<EVIDENCE_CACHE_DIR>/vies_retry/queue.json`: due at once, then, while VIES gives
no answer, hourly for 24 h, then daily for 7 days, then expired (the row stays PENDING).

**Deferred: domain rules.** Accepting a page on the customer's own domain with no or a
different address waits for a website or email field in the export. Today the only domains
are free text in Comments (166 of 9,484 records), sometimes a holding's. `DOMAIN_MATCH` stays
informational.

**Prompt v3 and frozen caches.** `vat_llm_web` v3 adds the source order above and the record
status (deleted or not); every v2 rule is kept. The prompt text is part of the cache key, so
recordings made with v1 or v2, including the 2026-10-20 experiment, do not replay under v3.

##### Hybrid search (2026-11-04, `VAT_WEB_QUERY_MODE=hybrid`, the default)

Searches and page opens are counted apart: at most `VAT_WEB_MAX_SEARCHES` (2) web searches
and `VAT_WEB_MAX_FETCHES` page fetches per entity (3 here; 4 since Step AF, 6 for an entity
with three or more own hosts since Step AF-2), all fetches made by our code. The rules below
are as of 2026-11-04; Steps AF and AF-2 change several of them (next section).

1. **Search 1, built by code** (`vat/web_query.py`): `<name for search> <city>`, nothing
   else. The name is Name 1 without "c/o …" / "z. Hd. …", without a site word and what
   follows (Werk, Niederlassung …), without a department word and what follows when ≥ 2
   words remain, without a fragment left by SAP's 35-character cut; a standalone "+" / "u."
   is written "&". Name 2 is appended when it carries the legal form ("& Co. KG", "GmbH",
   "Deutschland GmbH & Co KG", which replaces Name 1's form) or continues a Name 1 that
   ends mid-word or on "für" / "des" ("IBQ - Institut für Baustoff-" + "Qualitätssicherung
   GmbH"); never a department, brand or note. The name is quoted only when complete and
   at least two words. The city is in its usual spelling (`HATTERSHEIMAMMAIN` →
   Hattersheim, `MUNSTER` → Münster) and never quoted. The model only runs the query (one
   tool call) and lists the results with their titles and, for a directory entry, the
   website it names; the query it ran is logged (`QUERY_NOT_RUN_AS_GIVEN` when it differs).
2. **Entity-first page selection** (`vat/web_pages.py`). Only the entity's own hosts are
   fetched: a distinctive name word (≥ 4 letters; not GmbH, Deutschland, International,
   Group, Institut, Universität, Hochschule, Klinikum …) or the name's initials in the
   domain; the website a directory entry names; a result titled with the full name on a
   legal-notice path. For a department, an official parent host (`uni-*.de`, `th-*.de`,
   `*.mpg.de`, `*.fraunhofer.de`, `kit.edu` …). Nothing else is fetched, even with
   `/impressum` in its path; directories are pointers only; benefit portals,
   industryarena, studi.info, tender aggregators, imprint generators and tax-advice sites
   are never fetched. One page per host (Impressum / legal paths first, then tax, then
   procurement / AGB, then contact / about; the German page before its `/en/` twin), at
   most `VAT_WEB_MAX_FETCHES` hosts. PDFs on an own host: at most 2 MB, 20 pages. Last, an
   Impressum page on another host (not a directory or blocked host) is fetched to verify:
   its host counts as own only when that page names the entity (J&M Analytik on its
   parent's pharma-test.de).
3. **Link following.** On an own or parent host whose page states no VAT ID, the next
   Impressum / Legal notice / Rechtliche Hinweise / Steuern / Lieferanten / Einkauf / AGB
   link (from the page or the results), else `/impressum`; `VAT_WEB_MAX_FOLLOWS` (2) per
   entity, on top of the fetches. When no Impressum with content was reached on an own
   host (none found, or one rendered by script with no address and no VAT / register
   mention, as at TIB Chemicals), `/impressum` and `/imprint` are tried as well (as many
   again). An own host that refuses a page (401 / 403 / 429 / 5xx) gets one retry with a
   browser's User-Agent, then one Internet Archive snapshot of that URL (class C, review);
   when nothing is found the status is `LOOKUP_INCOMPLETE:HTTP_<code>`, not NOT_FOUND.
4. **Host check** (`web_pages.host_check`). A host is rejected only when its page names
   the entity with another legal form, or, on a legal-notice / contact / tax page, names
   neither the entity nor its town. Another town alone never rejects: the number is read
   and the address issue sends it to review (head offices: Grillo-Werke, INGEDE). A
   homepage is not name-checked. "Names the entity" is the matcher's: its distinctive
   words, a name the matcher calls EXACT (branch / department variants included), its
   initials ("HP Deutschland GmbH" for Hewlett-Packard Deutschland GmbH: flag
   `NAME_ABBREVIATED`, review), or the name the model searched with in search 2. A parent
   host is not checked (a department's number is the parent's).
5. **The reader** (no tools) is shown the page head and ±1,500 characters around every
   USt-IdNr / VAT / Impressum / "Angaben gemäß" mention (at most 12,000 characters per
   page), plus every DE + 9-digit string on the whole page, and names the number and the
   page, or lists every number with the company written next to it when it cannot tell
   (Hochschulen Fresenius: three companies on one Impressum): then the best name match is
   proposed with `AMBIGUOUS_SAME_NAME:<all numbers>` and the decision is CONFLICT, for a
   person to pick. The number is then held to every gate on the page as fetched here. A
   call stopped by Azure's content filter is retried once, then
   `LOOKUP_INCOMPLETE:CONTENT_FILTER`.
6. **Search 2, by the model**, only when search 1 showed no own, directory-linked or parent
   host at all (it found nothing in 22 runs when it also ran after rejected hosts): the current or full legal name, an acronym's expansion, the parent of a
   department, and the terms Impressum / USt-IdNr / Steuernummer; not search 1 again, no
   city, one query; without quotes and legal form when search 1 returned nothing.
   `query`, `name_used` and `why_this_query` are logged. Pages as in 2–5.
7. **Deleted records** whose own Impressum is 404: one Internet Archive snapshot of it.
8. **Peppol** for every entity; whether it had a hit is logged. Scheme 9930 entries carry
   the VAT ID; public bodies are listed under 0204 (Leitweg-ID), and when its fine address
   is a DE VAT ID (`0204:08222000-de143845342-36`, Universität Mannheim) that is read too
   (`PEPPOL_LEITWEG_ID`). Universitätsklinikum Tübingen and "Bundesministerium" have no
   entry at all (checked 2026-11-04).
9. **Grounded fallback** (only with `VAT_GROUNDED_FALLBACK=true`): an entity still NOT_FOUND
   gets one SerpAPI search with search 1 and the same fetch limit. `ENGINE:<engine>` flag.
   On the Step AA run it found 12 numbers, 10 of them on unrelated sites' Impressum pages:
   keep it off.
10. **`NOT_FOUND_NO_VAT_EXPECTED`** (decision NOT_FOUND), split in two:
   `:STATED`: the entity's own page says it has no USt-IdNr. (none, not liable to VAT,
   a Kleinunternehmer statement; the quote is the evidence); a third party's page never
   counts. `:INFERRED`: a school / authority and nothing found: an assumption, counted
   as not found.

The "model" mode (the model searches and opens pages itself) stays available: there the API
caps searches + opens together (`VAT_WEB_MAX_SEARCHES` + `VAT_WEB_MAX_OPENS`), the prompt
states the split, and `SEARCH_LIMIT_EXCEEDED` / `OPEN_LIMIT_REACHED` are set from the usage.
Prompt `vat_llm_web` v4 (budget wording); hybrid prompts `vat_llm_web_hybrid` v3. Each
search call has its own output schema, and the schema's fields are part of the cache key.

##### Step AF: the hybrid baseline (2026-11-05)

The model only ranks results and classifies names; our code opens pages, matches and
decides. The AUTO gates are unchanged. Helper calls (ranking, Name-2 classifier) run on
`VAT_HELPER_MODEL`; search and reading stay on `MDM_gpt-6-sol`. Every call goes through the
cost ledger.

**Budget per entity.** 2 searches, `VAT_WEB_MAX_FETCHES` (4) fetches, `VAT_WEB_MAX_FOLLOWS`
(2) link follows, 1 probe, 1 Peppol query, 1 archive snapshot.

* **Probe** (one fetch, `/impressum`, else `/imprint`): only on an OWN host that answered
  (HTTP 200, or a stale link's 404: the host is up; never a host that refused us, timed
  out or is down) and showed no Impressum-type page.
* **Blocked host** (403 / 5xx / timeout): one retry as a browser, then the budget moves to
  the next own host. An archive snapshot only when every own host is blocked;
  `LOOKUP_INCOMPLETE` (`ALL_OWN_HOSTS_BLOCKED`) only when nothing was found then. Blocked
  share on the baseline: 4.0 % (50) and 3.8 % (53), under the 5 % at which a headless
  browser would be worth it.

**Own-host recognition** (`vat/web_pages.py`). Initials of 3+ letters (compound words split:
"Universitätsmedizin Göttingen" → umg); a domain prefix with 6+ letters in common
(bundespolizei.de); the city never identifies a host except beside the record's own type
prefix (uni-, tu-, hs-, uk-) or for a Stadt / Gemeinde / Kreis record; institutional
domains (`institutional_kind`). A host the code cannot place but the ranking model vouches
for, an institution's parent domain, or another host's Impressum is unverified
(CANDIDATE / INSTITUTION / VERIFY): it counts as own only once an Impressum-type page on it
names the entity (the strict check for a GENERIC name).

**Ranking.** `VAT_WEB_RANKING=model` (default): the helper model orders search 1's results
(at most 3, `belongs_to_entity`, at most 250 output tokens; no snippets, so the cached
searches stay valid); the heuristic order (`heuristic_rank`) for the rest and when the call
fails. `heuristic`: the rule order alone. On the baseline, model ranking found 35 vs 33 (50)
and 46 vs 45 (53) for about 0.0016 USD per entity. Search 2 runs only when no own host
showed a real Impressum-type page (`is_gate_page`).

**Name preparation** (`vat/name_prep.py`, `vat/name2_classifier.py`). Name 1 cut by SAP and
continued in Name 2 (`CONTINUATION_TRUNCATED`: at the 35-character limit, or visibly cut: a
trailing connector, or Name 2 supplying the institution noun) is joined; a join at the limit
is never quoted (it may hold a broken word). Department and site words added ("Zentrale
Dienste", "Standort"). The Name-2 classifier (`VAT_NAME2_CLASSIFIER`, ten hand-labelled
examples, threshold 0.8) settles OTHER and CONTINUATION_TRUNCATED records: a confident
DEPARTMENT / SUB_ENTITY / OTHER undoes the join; a CONTINUATION is used only when every word
of its legal name comes from the record. Evaluated on 936 records (`tools/vat_name2_eval.py`,
`logs/name2_eval.xlsx`, 0.83 USD).

**Model knowledge.** Search 2 also returns a `domain_hint`, fetched as a CANDIDATE host
(`DOMAIN_HINT` flag). On the baseline it was given 23 times and was the source of no number;
kept as is (Step AF-2).

**Extraction and the page check.** `EXTRACTED_BY_PATTERN` only when the page carries exactly
one number; several numbers and no reader answer is CONFLICT. The name check runs on the
legal-notice block (`legal_block`); another company named there makes the host
`PARENT_CANDIDATE` and the number `PARENT_ENTITY_CANDIDATE` (review). A legal form is
compared only when both sides state one ("Stiftung" written before the name counts): SAP
without a form is `LEGAL_FORM_MISSING_IN_SAP`, never a failure; a differing form is a
candidate with `LEGAL_FORM_DIFFERS` (review), not a rejected host.

**Fallbacks.** Peppol unchanged. The aggregator fallback (`VAT_AGGREGATOR_FALLBACK`): one
site search on unternehmensverzeichnis.org and at most 2 entry fetches, after the no-VAT
statement and before INFERRED. An entry is used when its heading matches EXACT, or PARTIAL
with the record's first distinctive word; always MANUAL_REVIEW with `SECONDARY_SOURCE`, never
shared to the entity's other records (`AGGREGATOR_NOT_SHARED`). An entry listing the record's
name as a former name is `NAME_PARTIAL` + `SUCCESSOR_CANDIDATE` (LX International for LG
International). `NOT_FOUND_NO_VAT_EXPECTED:INFERRED` is reported apart, not as correct.

**No-number classes** (`vat/no_number_class.py`), judged from the usage record and SAP's
number: A no own host answered (none in the results, or none reachable); B an own host was
in the results but not fetched (fetch limit) and no own Impressum-type page was read; C
SAP's number is on a fetched page, outside the sections the reader saw; D it is in those
sections, not extracted; E an own Impressum-type page was fetched and read, and SAP's number
is on no fetched page; F SAP's number was read and dropped, or is on a rejected host's page.
A fetched and read own Impressum is only C, D or E.

**Baseline** (cached AF results, model ranking):

| | The 50 | The 53 |
|---|---|---|
| Found (Step AD → AF) | 27 → 35 | 31 → 46 |
| AUTO_ACCEPT | 17, all = SAP | 17; 6 of the 7 with a SAP number = SAP, Kappler (SAP wrong) the 7th |
| Cost per entity, full price | 0.057 USD | 0.055 USD |
| Still no number | 12 (A 4, B 4, E 4) | 7 (A 2, B 1, E 4) |

No AUTO value changed from Step AD.

##### Step AF-2: closing the baseline (2026-11-05)

**1. VIES out of the lookup path.** `VAT_VIES_IN_LOOKUP=false` (default): the page check
(`GroundedLookup._judge`) and the Peppol result no longer call VIES. A candidate past gates
a–f is `FOUND_UNVERIFIED:VIES_PENDING`; `run_lookups` marks every candidate without a VIES
answer `PENDING` with `VIES_PENDING` (`lookup.vies_pending`), including one reached by name
reconciliation after a gate stopped it before VIES (Universitätsmedizin Göttingen, Konstanz,
UK Tübingen, which before had no VIES status at all). A CONFLICT is not queued. The
decision stays as it is: `VIES_PENDING` is not blocking, so an AUTO_ACCEPT stays one, but
`lookup.sap_update_eligible` (AUTO_ACCEPT **and** VIES VALID) is what a controlled update
must check: nothing is written to SAP while PENDING. The run KPIs count both
(`sap_update_eligible`, `vies_pending`).

The queue verifies PENDING rows in batches (`ViesRetryQueue.process`, one `check_many`
call: the client's concurrency and pacing). `tools/vat_validate_xlsx.py` drains it at
start-up (numbers left from earlier runs) and at the end of every run, and writes the
answers into the output workbook (`vies_retry.apply_to_workbook`):

| VIES answer | Row |
|---|---|
| VALID | `VIES_PENDING` cleared; validation status VALID and date set; `FOUND_VERIFIED`; a review row's tier recomputed |
| INVALID | MANUAL_REVIEW, LOW, `VIES_INVALID` (a CONFLICT stays CONFLICT) |
| no answer | stays PENDING; asked again hourly for 24 h, then daily for 7 days |

`--no-vies-queue` skips both drains; `tools/vat_vies_retry.py [--workbook out.xlsx]` runs the
queue alone (a scheduler, hourly). A frozen run never drains it. The queue is kept only
under an evidence cache root: without one the end-of-run drain finds nothing and the rows
stay PENDING. `VAT_VIES_IN_LOOKUP=true` restores the inline check.

**2. Review tiers** (`lookup.review_tier`, `VAT_Found_Confidence` of a MANUAL_REVIEW row;
AUTO_ACCEPT stays `VERIFIED`, CONFLICT keeps its own status). Checked in this order:

| Tier | When |
|---|---|
| LOW | `NAME_PARTIAL` together with `ADDRESS_MISMATCH`, `SECONDARY_SOURCE` or `OTHER_VAT_IDS_ON_PAGE`; `EXTRACTED_BY_PATTERN` without a quote; `VIES_INVALID`; `EVIDENCE_NOT_VERIFIED` (a number not checked on a page we read) |
| MEDIUM | any of `SECONDARY_SOURCE`, `LEGAL_FORM_DIFFERS`, `PARENT_ENTITY_CANDIDATE`, `SUCCESSOR_CANDIDATE`, `ADDRESS_NOT_ON_PAGE`, `VIES_UNAVAILABLE`, `VIES_PENDING` |
| HIGH | name EXACT (an ABBREVIATED or BRANCH match is EXACT with a flag), address EXACT or SAME_TOWN, source class A or B |
| MEDIUM | anything else (a partial name alone, another town alone) |

`EVIDENCE_NOT_VERIFIED` as LOW and the last MEDIUM row are additions to the specified lists:
with them the tier is monotone (another issue never raises it). Recomputed on the cached AF
results (VIES answers as recorded then):

| Tier | The 50: candidates / with a SAP number / = SAP | The 53 |
|---|---|---|
| AUTO_ACCEPT | 17 / 17 / 17 | 17 / 7 / 6 (+ Kappler, SAP wrong) |
| HIGH | 1 / 1 / 1 | 0 |
| MEDIUM | 9 / 9 / 6 | 13 / 8 / 5 |
| LOW | 4 / 4 / 2 | 9 / 5 / 2 |
| CONFLICT | 4 / 4 / 0 | 7 / 2 / 1 |

**3. Fetch budget.** An entity whose search-1 results show `VAT_WEB_MANY_OWN_HOSTS` (3) or
more of its own (OWN) hosts gets `VAT_WEB_MAX_FETCHES_MANY_HOSTS` (6) page fetches; the
others keep `VAT_WEB_MAX_FETCHES` (4). Link follows (2) and the probe (1) are unchanged. The
usage JSON records `own_hosts_1` and `max_fetches`. The ranking prompt now puts a result
whose host or path contains legal, impressum, imprint or legal-notice before every other
result of the entity (the hybrid prompt version changes, so ranking calls are not replayed
from the AF cache). Both take effect on the next live run; nothing was re-run.

**4.** `domain_hint` stays as it is.

##### NAME_CONTAINED: a contained name may be AUTO_ACCEPT (2026-11-07)

Most MANUAL_REVIEW rows in the AD 50 and FINAL 50 reviews were right, and the largest group
had one blocking issue: `NAME_PARTIAL`, where the page's company name holds SAP's name plus
more words. Examples: "LMU Klinikum" vs "LMU Klinikum München, Anstalt des öffentlichen
Rechts"; "Universität Bielefeld" vs "Universität Bielefeld, Centrum für Biotechnologie".

- **Definition** (`entity_match.name_contained`): every word of SAP's name core appears, in
  order, among the page name's words.
  - Comparison is after folding: case, ß/ss, umlauts, hyphen/space.
  - A plural "s" doesn't matter.
  - SAP's last word may be cut at 35 characters, from 6 letters ("Textilforschungsinst").
  - Legal forms aren't part of a core; a different legal form still raises
    `LEGAL_FORM_DIFFERS`.
  - Extra page words are fine unless one names another company (Holding, Group, Gruppe,
    Konzern, Verwaltung, Beteiligung, Management). That keeps "Vitesco Technologies Group AG"
    and "Refratechnik Holding GmbH" out.
  - When it holds, the matcher writes `NAME_CONTAINED` instead of `NAME_PARTIAL`; the match
    level stays `PARTIAL`.
- **Gate** (`lookup.name_contained_ok`): a contained name counts as matched for AUTO_ACCEPT
  only when all of these hold:
  - source class A (the organisation's own site);
  - address EXACT or SAME_TOWN;
  - no `OTHER_VAT_IDS_ON_PAGE`;
  - not `EXTRACTED_BY_PATTERN`.

  Otherwise it is reported as `NAME_PARTIAL`, exactly as before. Every other blocking code
  still blocks (parent candidate, successor, legal form, sister entity, secondary source).
  The SAP update still needs VIES VALID.
- **Measured:**
  - AD 50 replayed from cache ($0.002); FINAL 50 re-scored from its 2026-11-06 output.
  - AUTO_ACCEPT went from 37 to 46 and MANUAL_REVIEW from 35 to 27, of 100 records.
  - The 9 new auto-accepts: TU Chemnitz, Kronen-Apotheke Marxen, Helmholtz Zentrum für
    Infektionsforschung, TCS Terminal Chemical Services, Sächsisches
    Textilforschungsinstitut, Universität Bielefeld, Universität Flensburg, LMU Klinikum, NMI.
  - 8 of the 9 carry SAP's own number.
  - NMI carries DE146484816, while SAP's record has DE222612959. The found number is on 4
    other NMI records in SAP, so SAP itself is inconsistent there.
  - Staatliches Bauamt Erlangen should also qualify in a fresh run; SAP has no number to
    check it against.
  - Reh-Apotheke stays in review, correctly: SAP names the owner Dirk Voss e.K. and the page
    names Marc Happ e.K., so it is a different sole trader.

##### Output columns for the reviewer (2026-11-08, `vat/review_columns.py`)

With `VAT_OUTPUT_COLUMNS=review` (the default), the endpoint and the batch runner write every
input column as it came, then these, then the 7 cost columns. Nothing else: the technical
columns come back with `full`. No call and no decision changes; everything is read from the
lookup's result.

| Column | Values |
|---|---|
| `VAT_Proposed` | the number found |
| `VAT_Decision` | Auto-accept / Review / Conflict / Not found |
| `VAT_Found_Confidence` | VERIFIED / HIGH_CONFIDENCE / MEDIUM_CONFIDENCE / LOW_CONFIDENCE / CONFLICT / NOT_FOUND (unchanged) |
| `VAT_Found_Match_Score` | 0–100 weighted name/address similarity (informational; no rule uses it) |
| `VAT_Why` | one or two plain sentences, from templates; no codes |
| `Found_Name` | the company name the page gives the number to |
| `Found_Address` | the address the reader copied; else the page's address block with SAP's postcode or town |
| `Name_Check` | SAME_NAME / SAP_NAME_PLUS_MORE / SIMILAR_NAME / DIFFERENT_NAME |
| `Address_Check` | SAME_ADDRESS / OTHER_STREET_SAME_TOWN / OTHER_TOWN / NO_ADDRESS_ON_PAGE |
| `Issue_Flags` | what else to check (below) |
| `Not_Found_Reason` | NO_WEBSITE_FOUND / WEBSITE_HAS_NO_VAT_ID / WEBSITE_NOT_READABLE / WEBSITE_BLOCKED_US / ONLY_PARENT_SITE_FOUND / PAGE_SAYS_COMPANY_CLOSED / PAGE_SAYS_NO_VAT_ID / PUBLIC_BODY_NO_VAT / NO_NUMBER_FOUND / INVALID_VAT_NUMBER (2026-11-10: the number found fails the format or check digit, or carries another country's prefix; never proposed, the agent pass looks again, and VAT_Why names the rejected number). A search that could not finish says why (from the lookup's flags, 2026-11-08): MODEL_SERVICE_FAILED (`LLM_UNAVAILABLE`; Azure's status and message are in the server log as "VAT model call … failed") / MODEL_ANSWER_UNREADABLE / CONTENT_FILTER_STOPPED / NOT_IN_REPLAY / COST_CAP_REACHED; SEARCH_INCOMPLETE only when none applies |
| `Agent_Pass` | what the agent pass did on the row (2026-11-08): Found (the proposal is the agent's) / Tried, nothing found / Skipped (cost cap); blank when it did not run there (off, or the lookup already had a number or expects none). From `VAT_Found_Flags` (`AGENT_TRIED`, `AGENT_SKIPPED_BUDGET`) |
| `VIES` | Valid / Invalid / Pending / Unavailable / Not checked |
| `VAT_Found_Source_URL`, `_Source_Type`, `_Source_Class`, `_Page_Title`, `_Agent_Version` | as before |
| `VAT_Found_Evidence` | the reader's quote; without one, the page text around the number, marked "(text around the number)" |

**`Issue_Flags`**, each one a question for the reviewer:

| Flag | Check | From |
|---|---|---|
| `SEVERAL_NUMBERS_ON_PAGE` | is the proposed one the right company's? | another number on the page (not the proposed one spaced differently), AMBIGUOUS_* |
| `OTHER_SOURCE_OTHER_NUMBER` | which source is right? | CONFLICTING_SOURCES |
| `FROM_DIRECTORY` | confirm on the company's own site | source class C (not an archive) |
| `FROM_OLD_ARCHIVE` | still current? | ARCHIVE_SOURCE / WEB_ARCHIVE |
| `NUMBER_NOT_QUOTED` | find the number on the page | no quote, QUOTE_NOT_IN_PAGE, EXTRACTED_BY_PATTERN, EVIDENCE_NOT_VERIFIED |
| `MAYBE_PARENT_COMPANY` | does the customer have its own number? | PARENT_ENTITY_CANDIDATE, PARENT_ENTITY |
| `MAYBE_RENAMED_OR_SUCCESSOR` | still the same company? | SUCCESSOR_CANDIDATE |
| `SISTER_COMPANY_ON_PAGE` | the right company of the group? | SISTER_ENTITY_ON_PAGE |
| `LEGAL_FORM_DIFFERS` | GmbH vs AG: which is the customer? | LEGAL_FORM_DIFFERS |
| `REGISTER_NO_DIFFERS` | HRB differs: another company? | REGISTER_MISMATCH |
| `DEPARTMENT_OR_UNIT` | the central number is expected | DEPARTMENT, SUB_ENTITY |
| `SITE_OR_PLANT` | the head office's number is expected | BRANCH |
| `NAME_SHARED_BY_OTHERS` | the street (or HRB) must match too | GENERIC_NAME, NAME_ABBREVIATED |

The VIES re-check at the end of a batch run updates `VIES`, `VAT_Decision`,
`VAT_Found_Confidence` and `VAT_Why` on the written rows (`review_columns.apply_vies`), as it
updates the technical columns with `full`.

##### Research log columns and entity status (2026-11-06, `vat/lookup_log.py`)

With `VAT_LOG_COLUMNS=true` (off by default since 2026-11-08), the endpoint (`/api/validate/vat`,
`/api/validate/vat/file`) and `tools/vat_validate_xlsx.py` add these columns after the cost
columns. They hold what the run reports used to show from the usage JSON. Every cell is read
from the entity's usage record and the upload's other records, so they cost nothing and make
no call. A row that was not looked up has them blank.

| Column | Content |
|---|---|
| `VAT_Found_Entity_Status` | What the lookup learned about the entity, one `CODE: detail` per line (below) |
| `VAT_Found_Name_vs_SAP` | The name the source gives the number to (`VAT_Found_Attributed_To`) against SAP's Name 1–4: `EXACT` / `PARTIAL` / `MISMATCH; SAP: …; found: …; legal form gmbh → ag; only in SAP: …; only found: …` |
| `VAT_Found_Evidence_Snippet` | The page text around the number (240 characters before, 80 after) as our code fetched it, with the URL; also when the code picked the number and `VAT_Found_Evidence` (the model's quote) is blank. Sources without a fetched page (aggregator, Peppol) show their own quote. Display only: it never counts as an evidence quote for gate a |
| `VAT_Log_Name_Class` | Name-prep class (and the Name-2 classifier's verdict when it changed the plan) |
| `VAT_Log_Search_Name` | Search name, the entity name if it differs, search 2's name, the accepted names |
| `VAT_Log_Queries` | Search 1, search 2, the site search and the aggregator query, as run |
| `VAT_Log_Ranking` | URLs picked per search (ranking model / heuristic) and the domain hint |
| `VAT_Log_Hosts` | Each host: kind, then answered / rejected (why) / blocked (code); operator |
| `VAT_Log_Pages` | Every page fetched: HTTP status, how it was reached, numbers on it, rejected, script-only, no-VAT or defunct statement |
| `VAT_Log_Not_Fetched` | Results not fetched (fetch limit) |
| `VAT_Log_Dropped` | Numbers read and dropped, with the reason |
| `VAT_Log_Operator` | The operator followed (name, URL, the page that named it, read or not) |
| `VAT_Log_Peppol` | Peppol entries and whether one matched |
| `VAT_Log_Aggregator` | The aggregator's entries |
| `VAT_Log_No_Number_Class` | A–F class and why (rows without a number; C/D/F need scope `all`) |
| `VAT_Log_Agent` | The agent pass: relation, confidence, value, source, notes, searches, opens |
| `VAT_Log_Counts` | Searches, fetches, follows, probes, PDF / archive fetches, seconds |

`VAT_Found_Published_Address` (the address on the number's page) is a lookup column and is
always written.

**`VAT_Found_Entity_Status` codes.** These describe what was seen, not a registry check. A
company that none of the fetched pages discusses is not reported as dissolved.

- `INSOLVENT`, `IN_LIQUIDATION`, `DISSOLVED`, `MERGED`, `RENAMED`: the SAP name says so ("in
  Insolvenz", "i. L."), or a fetched page does near the entity's name (quote and URL).
- `SUCCESSOR`: the agent judged the number's page to be a renamed or successor entity, or the
  page names another entity (`SUCCESSOR_CANDIDATE`).
- `VAT_FROM_PARENT`: the umbrella parent's number was accepted (`PARENT_ENTITY`), or the agent
  says it is the parent's.
- `VAT_MAY_BE_PARENTS`: the page names a parent or group, not the record, or the number was
  read on a parent or institution site, or the page gives the number to a holding / group name
  ("Refratechnik Holding GmbH" for Refratechnik Ceramics GmbH).
- `OTHER_ENTITY_HOLDS_VAT`: the page gives the number to a company whose name is not the
  record's (not just spelling: name cores under 90% similar), e.g. "CPT GmbH" for CPT Zwei GmbH.
- `VAT_FROM_OPERATOR`: the number came from the operator's site. `OPERATED_BY`: the operator's
  site was followed, with no number for the record.
- `ONLY_PARENT_SITE`: no number; only a parent's site answered.
- `SUB_UNIT_OF`: the record is a department, branch or sub-entity; the legal entity was looked
  up.
- `SISTER_ENTITY_ON_PAGE`: the number's page also names a sister company.
- `LEGAL_FORM_DIFFERS`: the legal form on the page differs from SAP's (possibly a conversion).
- `SAME_AS_SIBLING`: the number equals the one on another SAP record with the same name and
  town. `SIBLING_HAS_OTHER_VAT`: that record has a different number. `SIBLING_HAS_VAT`: no
  number was found, but that record has one.
- `NO_VAT_STATED` (the quote), `PUBLIC_BODY_NO_VAT_INFERRED` (an assumption),
  `SAP_VAT_OUTDATED`, `DELETED_IN_SAP`, `FROM_ARCHIVE_SNAPSHOT`.
- `PARENT_HINT`, `SUCCESSOR_HINT`, `DEFUNCT_HINT` (marked "search result, unverified"): the
  ranking model's one-line reason for a search result. It saw only the title and snippet. At
  most 2 per kind.

**The name of a number no model attributed (2026-11-06, `web_pages.number_holder`).** When the
code picks the number itself (`EXTRACTED_BY_PATTERN`, a candidate from a page with several
numbers, a block-selected number), `VAT_Found_Attributed_To` used to be blank, so the matcher
had no name to compare and could not raise `LEGAL_FORM_DIFFERS` or `PARENT_ENTITY_CANDIDATE`.
It is now the first "<name> <legal form>" in the legal notice before the number (since the
previous number, at most 1,500 characters back; a Komplementärin is skipped). On the reviewed
FINAL 50 file this filled all 7 `CONFLICT` rows the manual review marked as a parent's or another company's:
Vitesco Technologies Group AG (gmbh → ag), FSKZ e. V. (gmbh → ev), Refratechnik Holding GmbH,
DECHEMA Gesellschaft für Chemische Technik und Biotechnologie e.V., LMB - Laser-
Materialbearbeitungs GmbH, CPT GmbH, and Kranich-Apotheke Matthias Schwartz e.Kfm. The
decision is unchanged: these numbers have no evidence quote, so they stay in review.

On the saved AH 19, AF 53 and AG 19 logs, no fetched page carried a defunct statement. Veritas
was flagged `INSOLVENT` from its SAP name, with 2 `DEFUNCT_HINT`s. The parent hints covered
Allessa → WeylChem, VAMED → Fresenius, Infracor → Evonik and Hewlett-Packard → HPE.

##### Cost accounting (2026-11-05, `vat/costs.py`)

**Price table.** `config/vat_prices.yaml` (`VAT_PRICES_FILE`) is loaded at the start of every
run. Its version and a hash of the file go into every entity's `costs` in the usage JSON and
into the run's ledger row; changing any price changes the hash, and the next ledger row notes
the change.

| Deployment | Use | Input / cached input / output, USD per 1M tokens | Type |
|---|---|---|---|
| `MDM_gpt-6-sol` (GPT-6 Sol) | search and reading | 2.00 / 0.10 / 10.00 | global, ×1.0 |
| `MDM-Apoorva-gpt-5.4-mini` (`gpt-5.4-mini-2026-03-17`, `VAT_HELPER_MODEL`) | ranking call, Name-2 classifier (reasoning `none`: the deployment rejects `minimal`) | 0.75 / 0.075 / 4.50 | global, ×1.0 |

Tools: web search 0.014 per call (14 USD per 1,000), a page the model opens itself 0.014
(model mode only; 0 in hybrid mode), SerpAPI 0.01 per query (grounded engine, off by
default). Free: our own fetches, link follows, probes, VIES, Peppol, Wayback. Calls are
priced as `VAT_PRICE_DEPLOYMENT`, by default the LLM deployment; one the table has no price
for stops the run with an error (under `MOCK_EXTERNAL_CALLS` it is priced at zero, as
nothing is billed). Data Zone EU deployments would take multiplier 1.2 (Sol) / 1.1 (mini).

**Per call.** Each API call is listed on the entity (`calls` in the usage JSON) with its raw
counts: deployment, `call_kind` (search / reader / ranking / name2_classifier /
grounded_reader / agent), input, cached, output and reasoning tokens, `web_search_calls`,
`page_open_calls`, `serpapi_queries`, HTTP status, seconds and `cache_hit`. Its cost:

    tokens = ((input - cached) * input_per_1m + cached * cached_input_per_1m
              + output * output_per_1m) / 1e6 * multiplier
    search = web_search_calls * web_search_per_call
    opens  = page_open_calls * page_open_per_call
    serp   = serpapi_queries * serpapi_per_query

Reasoning tokens are part of the output tokens. Prompt-cache reads come from
`usage.input_tokens_details.cached_tokens` (Responses API; chat completions:
`prompt_tokens_details.cached_tokens`) and bill at the cached rate; cache writes have no
separate charge. A response without the count is logged once and its input priced at the
full rate. In the AD 50 run 37.5 % of the billed input was a cache read: every search call
read its ~4,100-token static prefix from the cache (40.6 % of search input), the reader
calls none (their fixed prefix is below the 1,024-token minimum).

**Lines, kept apart.** Per entity (the `VAT_Found_*_USD` columns of the output workbook and
`costs` in the usage JSON), per run (the Summary sheet's Cost section, the usage JSON and the
log line):

| Line | What |
|---|---|
| `Azure_LLM_Tokens_USD` | tokens, also split by call kind and by deployment |
| `Azure_WebSearch_USD` | search calls × 0.014 |
| `Azure_PageOpen_USD` | pages the model opened (model mode) |
| `SerpAPI_USD` | grounded engine queries |
| `Other_USD` | always 0, so the split is complete |
| `Total_USD` | the sum; the usage JSON's `cost_usd` is the same number |
| `New_Spend_USD` | what the run billed: Total minus the cost of calls replayed from the cache |

**Stop limit.** `VAT_WEB_BUDGET_USD` is checked against the run's spend in `Total_USD` terms
(tokens + search calls + opens + SerpAPI), not tokens alone; reaching it is logged, and
every later entity is `LOOKUP_INCOMPLETE` / `BUDGET_EXCEEDED`.

**Ledger.** `tools/vat_validate_xlsx.py` appends one row per run (live, replay, smoke test,
evaluation, regression) to `logs/vat_cost_ledger.csv` (`--ledger`; `--step` or
`VAT_RUN_STEP` labels it) and prints the total spent to date. A row holds the run id,
timestamp, step, input file, engine, entities, calls by kind and deployment, cache hits,
tokens by type, search / open / SerpAPI counts, every line both as recorded and billed only
(`New_*`, `billed_*`), the price table's version and hash, and `backfilled` / `estimated`.
`tools/vat_cost_summary.py` prints the total spent, by step, month, deployment and line, and
tokens and search calls billed to date, and says what the ledger leaves out.

**Backfill.** `tools/vat_cost_backfill.py` re-priced every usage JSON in `logs/` with the
table and appended it with its file time and `backfilled = yes`; running it again adds
nothing (a run id already in the ledger is skipped). Older records are read as they are:
Step X (model mode) has no per-call list, so the entity's usage is its one `agent` call; a
missing search count is estimated from `searches`. Runs that kept no usage JSON are rebuilt
from their LLM cache, where each recorded answer is one billed call with its own usage (the
X smoke test, AF-1 and AF-1b, the first AA smoke test, earlier AB attempts). Estimated, and
marked so: Step AA's 27 SerpAPI queries (one per grounded run; the live JSON logged no
counter) and its 24 grounded-reader answers, recorded without usage (2,500 input + 200 output
tokens assumed each, 0.17 USD). Runs before Step X (cache `run_fluffy_please_19`, 184 answers
recorded without usage) are not in the ledger.

Spent to date, Steps X to AF-1b: **14.31 USD**; tokens 7.07, web search 5.61, page opens
1.36 (Step X), SerpAPI 0.27 (Step AA). 573 of 1,176 calls were replays. With Step AF (Name-2
evaluation 0.83, focused 10-row run 0.44, baseline runs 2.39): **17.95 USD** over 32 runs.
Step AF-2 ran nothing.

##### Name and address rules from the full DE export (2026-11-08)

The rules below come from the 27,442 records of `CMD_DE_2026-10-21.xlsx`, not from a test sample.
The counts are from that export. All rules are in `vat/entity_match.py`.

**Address.**

- The export's `Street` already ends with the house number in 22,290 of 25,801 records
  ("Im Altenschemel 49" with House Number "49"). The check used to look for "… 49 49" on
  the page, so nearly every same address came out `OTHER_STREET_SAME_TOWN`. The number is
  now not added a second time.
- What follows "//" in a street is a building or floor note ("Amalienbadstrasse 41 // Bau
  53", 15 records). It is ignored.
- The export has two `Street` columns. The first holds the street without a number, the
  second the street with it; the second is the one read.

**Names.** Words are read written out wherever a name is split into words, on the SAP side
and the page side alike:

| Written | Read as | Records |
|---|---|---|
| Inst., Fak., Abt./Abtlg., Dez., Zentr. | Institut, Fakultät, Abteilung, Dezernat, Zentrum | 2,683 / 619 / 397 / 174 / 7 |
| f., u. | für, und | 352 / 492 |
| TU, TH, FH, HS, Uni/Univ | Technische Universität, Technische Hochschule, Fachhochschule, Hochschule, Universität | 114 / 16 / 6 / 7 / 14 |
| LMU, RWTH, MPI, MPG | Ludwig-Maximilians-Universität, Rheinisch-Westfälische Technische Hochschule, Max-Planck-Institut, Max-Planck-Gesellschaft | 152 / 178 / 8 / 3 |
| St., Gebr., Ges., Nachf., techn., angew., Intl | Sankt, Gebrüder, Gesellschaft, Nachfolger, technische, angewandte, International | 99 / 27 / 43 / 2 / 9 / 6 / – |

Legal forms:

- AöR and KöR are the long forms (about 170 records).
- e.K. and e.Kfr., gAG and PartG(mbB) are now recognised forms.
- A whole word cut at 35 characters ("Max-Planck-Ges") is not read as a cut legal form.

Departments:

- "Abt. …", "Inst. f. …" and "Dez. …" in Name 2–4 are recognised as departments. That is
  2,878 more records, where Name 1 is the legal entity.
- "Dezernat" is a department word.

Acronyms:

- A first word that is the initials of the rest ("BSH Bosch und Siemens Hausgeräte",
  132 records) may be missing on the page.
- Name 1 that is only the acronym of Name 1–4 ("ZOEK gGmbH" + "Zentrum für Organische
  Elektronik" + "Köln", 23 records) matches the full name on the page.

Effect on the export:

- 17,886 entities become 17,820. 64 groups now join records of one organisation spelled
  two ways ("TU Dresden" / "Technische Universität Dresden"), which are then looked up once.
- Replays of AD 50 and Test50 (replay-only, $0) change no search query.
- On Test50, both fixes together: 3 more correct auto-accepts, no new wrong ones.

##### Fixes from the 100-row sample of missing-VAT rows (2026-11-08)

The sample (`data/VAT/DE_VAT_SAMPLE_100_einvoicing.xlsx`, 99 companies, $8.97 with the
agent pass) gave 38 auto-accept, 27 review, 13 conflict and 22 not found. Five things put
good rows in Review or hid a check:

1. **Text that is not plain text.** Soft hyphens ("Wasser&shy;technik", bwt.com),
   zero-width spaces ("Umsatzsteuer-&#8203;ID", uniklinik-ulm.de) and UTF-8 read as
   Windows-1252 ("UniversitÃ¤t", lmu.de). `entity_match.clean_text` drops and repairs
   them. It is applied in `fold` (every name and address comparison), in the quote check
   (`lookup_grounded._ws`) and in Found_Name, Found_Address and VAT_Found_Evidence.
   Prompts and page text sent to the model are unchanged, so cached answers still replay.
2. **English town names.** "Munich", "Cologne", "Nuremberg", "Hanover" and "Brunswick"
   count as the German town.
3. **Same address written another way:**
   - Street plus number with the postcode **or** the town is `EXACT`. The other one may
     be SAP's typo ("VILLINGEN SCHWENNIGEN"), an old postcode or a district name.
   - The linking "s" before "straße" is ignored ("UniversitätStr" = "Universitätsstraße").
   - A note after a comma in Street is dropped ("Im Neuenheimer Feld 280, Stockwerk").
4. **Agent rows our fetch could not confirm** (`_unverified`) now get Name_Check and
   Address_Check from the agent's own answer. `EVIDENCE_NOT_VERIFIED` still keeps them in
   Review. "Anstalt/Körperschaft öffentlichen Rechts" without "des" is AöR/KöR.
5. **Companies in liquidation.** A page name with "i.L.", "in Liquidation", "in
   Abwicklung" or "i. Abw." adds `IN_LIQUIDATION`. It is blocking (Review), shows as an
   Issue_Flag and has its own VAT_Why sentence.

Replay of the sample ($0) after the fixes:

- 4 rows move from Review to Auto-accept: LMU ×2, BWT and in.vent.
- 1 row moves from Auto-accept to Review (in liquidation).
- 10 Address_Checks become SAME_ADDRESS.

On Test50, no decision changes.

##### Throughput and large uploads (2026-11-08)

**Why.** 10,000 records at the AD 50 live pace take too long. One company took about 24 s
(median 23, slowest 87): 57% web search calls, 10% model page reads, 33% page downloads,
ranking and Peppol. At the old 5 companies at once that is about 13 h; at 1, about 67 h.

Running more at once used to break rows. A model call that got HTTP 429 had one retry
after 10 s, then the company became `LOOKUP_INCOMPLETE` / `LLM_UNAVAILABLE`.

**Requirement: results do not change.** Every change below affects only *when* things
happen: the same queries, prompts, models, effort, pages, rules and decisions.

Checked on the AD 50 cached run: concurrency 1 vs 20, and a job vs a direct `/file` run,
give cell-identical output (cost columns aside).

| Change | Where | Why it cannot change a result |
|---|---|---|
| Calls paced to the Azure quota (one limiter per deployment for the process, tokens and requests per minute, about 85% of the quota) | `vat/rate_limit.py`, `VatLLM.search` | It only delays a call. A cached replay never waits. |
| HTTP 429 / 5xx retried up to 6 times, after Azure's Retry-After (else 5, 10, 20 … 60 s). A 429 pauses the whole deployment | `vat/llm_cache.py` | A row that used to break gets its normal answer. |
| 20 companies at once | `VAT_LOOKUP_CONCURRENCY` | Each lookup is independent; the output keeps input order. |
| The Peppol query runs alongside the web lookup | `vat/lookup_web.py::_lookup` | Same query, judged in the same place. |
| The first page of each picked host is downloaded at once, at most 20 downloads in the process | `_prefetch`, `GroundedLookup.fetch_network` | Pages are read in the same order by the same rules. A page the rules would not have read is dropped uncounted. Nothing is cached until it is read. |

The limiter learns the quota from Azure's response headers (logged once per deployment:
"Azure quota of …"), unless `VAT_AZURE_TPM` / `VAT_AZURE_RPM` set it.

Throughput is the smaller of two limits: companies at once ÷ 24 s, and the quota ÷ about
32k tokens per company. At 1M tokens a minute that is about 30 companies a minute
(10,000 in about 5.5 h); at 300k, about 9 a minute (about 18 h). Cost per company is
unchanged, about 0.063 USD.

**Large uploads as jobs** (`vat/jobs.py`). These endpoints take the same upload and
options as `/file`:

| Endpoint | What it does |
|---|---|
| `POST /api/validate/vat/file/jobs` (202) | Answers at once with `id`, `rows`, `unique_companies`, `chunks_total` and `estimated_cost_usd`. |
| `GET …/jobs/{id}` | Status (`QUEUED`, `RUNNING`, `ASSEMBLING`, `DONE`, `STOPPED_BUDGET`, `CANCELLED`, `FAILED`), chunks and rows done, companies per minute, `http_retries`, `rate_limit_wait_s`, `spent_usd`, `estimated_seconds_left`. |
| `GET …/jobs/{id}/result` | The workbook, as `/file` returns it. 409 until it is done; `?partial=true` gives the rows of the chunks finished so far. |
| `POST …/jobs/{id}/resume[?max_cost_usd=]` | Continues a stopped job from its next unfinished chunk. |
| `DELETE …/jobs/{id}` | Cancels after the current chunk. `?purge=true` deletes a stopped job's files. |

How a job works:

1. **Chunks.** About `VAT_JOB_CHUNK_ROWS` rows each, cut only between groups of rows
   linked by a Customer or a legal entity (`vat.validate.row_groups`). An entity's first
   record, its siblings and its one lookup are therefore the same as in one request. Each
   chunk is saved before the next starts.
2. **Assembly.** `validate_workbook` on the whole upload, which is exactly `/file`, now
   answered from the cache. What is not cached (a failed call, a timed-out page) is asked
   again: the retry pass.
3. **VIES** (`vies_after`, on by default). Asks VIES for the proposals and writes the
   answers in, as the batch tool does. This is the only intended difference from `/file`;
   with `vies_after=false` the result is identical.

**Failures.** Job state is files in `VAT_JOB_DIR/<id>/` (`job.json`, `input.xlsx`,
`chunks/`, `result.xlsx`); there is no database.

- **Lease.** A worker holds a lease in `job.json`, renewed every `lease/3`.
- **Restart, recycle or deployment.** The lease runs out. The Function's timer trigger
  (`function_app.py`, every minute) continues the job from its next unfinished chunk.
  Calls already answered replay from the cache at $0, so a crash loses at most the calls
  in flight.
- **A failing chunk** is tried 3 times, then the job is `FAILED`. It can be resumed.
- **One job at a time per process**, so one limiter paces everything. Uploads made
  meanwhile wait in order.
- **Locally (uvicorn, no timer):** `POST …/resume` continues a job after a restart.

**Budget.** `max_cost_usd` (else `VAT_WEB_BUDGET_USD`; 0 = none) is checked between
chunks, never inside one, so no row is cut off with `BUDGET_EXCEEDED`. A chunk can pass
the cap by its own cost (200 rows at 0.063 USD is about 12.6 USD). The job then stops as
`STOPPED_BUDGET`.

**To check on Azure:**

- `VAT_JOB_DIR` and `EVIDENCE_CACHE_DIR` must point at storage that survives a restart:
  `/home` on Windows, Premium or Dedicated plans. Otherwise a restart redoes paid calls.
  On a Linux Consumption plan there is no such disk; jobs and the cache would need Blob
  Storage, which is not built.
- Always On should be enabled, so background work is not frozen between requests.
- Before a 10k run, one live job of 200–500 rows measures companies per minute and 429s
  against the real quota.

---

#### 4. Proposed issue codes (proposal only)

The Notion catalogue is the source of truth. **None of these exist in the catalogue or in the
code.** The numbers are placeholders for the catalogue owner to assign. Groups follow catalogue
v2 (G2 missing, G3 conflict, G4 format, G6 needs confirmation).

| Proposed code | Group | Title | Raised from | Remedy | Mandatory |
|---|---|---|---|---|---|
| `G2-VAT-0xx` | G2 | VAT ID Missing (EU customer) | `VAT_Format_Status = MISSING` | steward: obtain the customer's VAT ID | no (until the policy for EU customers is set) |
| `G4-VAT-0xx` | G4 | VAT ID Format or Checksum Invalid | `FORMAT_ERROR` (incl. `NON_EU_PREFIX`) | steward: correct the value from the customer's documents; never auto-fixed | no |
| `G4-VAT-0xx` | G4 | VAT Prefix Does Not Match Country | `PREFIX_COUNTRY_MISMATCH` | steward: confirm which is wrong, the VAT ID or the Country/Region Key | no |
| `G3-VAT-0xx` | G3 | Conflicting VAT IDs Across a Customer's Rows | `CUSTOMER_VAT_CONFLICT` | steward: pick the correct value; the pipeline does not choose | no |
| `G4-VAT-0xx` | G4 | VAT ID Not Registered (VIES invalid) | `VAT_VIES_Status = INVALID` | steward: check with the customer (VIES FAQ: confirm the number first) | candidate |
| `G6-VAT-0xx` | G6 | Registered Name Differs From Record | `VAT_Name_Match = MISMATCH` (and possibly `PARTIAL`) | steward: compare the VIES name with Name 1–4 | no |

**Deliberately no code** for `SERVICE_UNAVAILABLE`, `ERROR` or `NOT_DISCLOSED`. They describe
the lookup, not the record. They should be re-run or reported as run health, never shown to a
steward as a data defect.

---

#### 5. Follow-ups (not implemented)

1. **BZSt eVatR qualified check** (Bundeszentralamt für Steuern) for non-German EU customers.
   It confirms name, place, postal code and street against the issuing state. It needs Bruker's
   own German USt-IdNr. as the requester.
2. **German name/address corroboration** via Handelsregister / Unternehmensregister, to close
   the DE name gap. Check the terms of use first (automated retrieval and storage).
3. **ADF / DATAshaper wiring.** Today this runs only through the endpoint and the runner.
4. **VAT ID as a dedup signal.** The same VAT on two customers is strong evidence of one legal
   entity. The fixture already shows it: `IE6388047V` appears on customers 99000018 and
   99000019. It is not read by dedup today.
5. **TTL for cached VALID results.** Cache entries are immutable, which is right for a frozen
   evaluation and stale for production, since validity is tied to a date.
6. **Terms-of-use sign-off** for storing VIES names and addresses (§1).
7. **Open rule decisions**, left as implemented and pinned by tests:
   - A bare Spanish CIF with leading letters (`A28015865`, key ES) is `FORMAT_ERROR` /
     `NON_EU_PREFIX`, not given `ES`. This follows the "no leading letters" rule.
   - A frozen-cache miss is reported as `SERVICE_UNAVAILABLE` (`VIES:FROZEN_CACHE_MISS`), not
     as a separate `NOT_CHECKED` state.
8. **`tests/KNOWN_FAILURES.md` is out of date.** Found during this change's test-suite gate;
   not fixed here.
   - Its header says 12 failures but its table lists 13.
   - Four listed tests now pass: the three Tier 2A orchestrator tests and
     `test_address_cleanup…test_street_1_keeps_the_value_and_sets_no_building`.
   - `test_provenance_scheme_b.py::…::test_no_enrichment_value_moved` fails: it expects 56
     value columns, and the actual count is 59.
   - With this change's four edited files stashed, it still fails with 59, so this change
     didn't cause it.
   - Commit `3256571` ("Added enriched column") explains one of the extra columns. The other two
     are unexplained.
9. **`DiskCache.set` silently skips files it cannot write.** On Windows that includes any path
   over 260 characters.
   - Filenames are capped at about 120 characters, so the limit is reached once the cache
     directory itself is longer than about 140.
   - The longest existing path is 236 characters (a GLEIF fuzzy-search entry under
     `logs/cache/…`). That is about 24 characters of headroom, and SERP and fetch entries are
     close behind (233, 231).
   - A deeper checkout or a longer `EVIDENCE_CACHE_DIR` loses recordings with no error. The
     first VIES cache key (URL-based) hit exactly this.
   - Hit again on 2026-10-19 by the missing-VAT lookup. Under a 189-character cache directory,
     every `vat_serp` and `vat_llm` entry and every long-URL `vat_pages` entry was dropped, so a
     frozen re-run would have had nothing to replay.
   - **Workaround in the VAT code only** (`vat/cache_guard.py`): after each write the VAT code
     checks the file exists. A missing file is logged as a warning and counted as
     `cache_write_failures` in the summary, the Summary sheet and the Cost sheet. This
     **detects** the problem; it does not fix `DiskCache`. The fix is still this follow-up.
     Runs of the experiment use a short cache path (the scratchpad's 8.3 form, 81 characters).
10. **Mode A issues found by the 2026-10-20 experiment** (§3a). Open; not fixed in the
    results commit.
    - **A blocked or timed-out page is reported as `NOT_FOUND` in a live run.** It should be
      `LOOKUP_INCOMPLETE` with a reason (for example `PAGE_UNAVAILABLE:403`), the same as the
      frozen run's `FROZEN_CACHE_MISS`. Live and frozen output would then agree for these rows,
      and "could not look" would never read as "nothing there".
    - **URL selection takes the first 3 of the merged result list.** One noisy query (Zalando's
      "…Impressum" returning Berlin city pages) can crowd out the other query's good results.
      Alternating between the two queries' results, or preferring result URLs whose path looks
      like an Impressum, would fix that.
    - **A country word in the attributed name** ("QIAGEN GmbH - Germany") makes an exact
      attribution PARTIAL. Treating country names as a qualifier would recover it.
    - **Bot protection (403) and JavaScript-only Impressum pages** are a limit of plain HTTP
      fetching, not of the reader.
    - **Character encoding.** Some page text is stored with mis-decoded umlauts ("gegrÃ¼ndete"):
      the existing `PageFetcher` takes the encoding `requests` guesses. The quote and ID gates
      still work, because they compare within the same text, but the stored evidence reads
      badly.
