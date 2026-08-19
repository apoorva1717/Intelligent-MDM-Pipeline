Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Chapter 2 source packet — Problem Description

Source material for the thesis chapter that states the problem: what an SAP customer-master
record is, what is wrong with these records, how often, how they were corrected before, and why
deduplication depends on enrichment.

**Evidence rules.** `docs/thesis-doc-prompt.md:18-34` applies in full. Every claim carries a
`path/file:LINE` citation. Where code contradicts a docstring, a README, or a workbook, the code
wins and the conflict is recorded. No figure in §3, §5 or §6 was estimated: each was produced by
`scripts/ch02_measure.py`, whose verbatim output is reproduced below alongside the command that
produced it. Quantities that are needed and absent carry `⚠ MEASUREMENT REQUIRED`; recorded
outcomes with no recorded reason carry `⚠ RATIONALE NOT IN REPO — author to supply`.

**Read for this packet.** `00_INVENTORY.md`, `03_ALGORITHMS.md` (Part H), `03b_EXEMPLARS.md`,
`05_DATA_MODEL.md`, `08_GAPS.md`, `CONTEXT-EXTERNAL.md`, plus the primary sources they cite:
`api/models.py`, `api/routes.py`, `enrichment/issue_detection.py`, `dedup/signatures.py`, and
the three DATAshaper onboarding transcripts.

---

## 1 · SAP customer master structure

### 1.1 What the pipeline treats as the record

The service does not read SAP directly. Its input contract is `EnrichmentRecord`
(`api/models.py:22-284`), whose fields "mirror the SAP customer-master export columns
one-to-one", each accepting the exact spreadsheet header as its JSON key
(`api/models.py:24-27`). In production those rows arrive from the DATAshaper `Legacy` table via
`SELECT *` (`CONTEXT-EXTERNAL.md:106`), posted as `{"records": …}` to `/enrich`
(`CONTEXT-EXTERNAL.md:141`).

Two structural properties govern everything below.

**No field is mandatory — including the identifier.** Every field is `Optional[str]` with default
`None`, and the model states this explicitly: *"No field is mandatory: every column is optional,
including the customer identifier. When absent, `record_id` falls back to an empty string"*
(`api/models.py:220-223`; table at `05_DATA_MODEL.md:55-93`). Mandatoriness is therefore **not**
enforced at the service boundary. It is expressed downstream in two other places: the
`G2-VAL-*` rules, which flag a required column that exists but is blank
(`enrichment/issue_detection.py:129-137`), and the DATAshaper validation rules, where a rule is
"mandatory (an issue) or non-mandatory (a warning)" and a record without mandatory issues is
what reaches the load file (`CONTEXT-EXTERNAL.md:354-357`;
`Datashaper-Tutorial-Part3.txt:575`).

**Every field is free text at the service boundary.** All **40** declared fields are typed
`Optional[str]` without exception. No field is an enum, a date, a number, or a
pattern-validated string: the class body contains no `Literal`, no `constr`, no
`field_validator` or `model_validator`, and no `pattern`, `max_length`, `min_length`, `ge` or
`le` constraint (verified against `api/models.py:22-284`). Codedness is therefore a property of
SAP and of the data, never of this contract. Unknown columns are accepted and silently
discarded: no `extra="forbid"` is declared (`api/models.py:40`; `05_DATA_MODEL.md:51-53`).

### 1.2 The fields, by role

Column names are as they appear in the source workbook and in the model aliases. **Populated %**
is measured over the 500 rows of `PresentationTestData.xlsx` (run output §1.1 below). **Coded**
distinguishes fields SAP itself constrains to a value set or a format from fields that accept
any string.

| SAP column | Model field | Meaning | Type at the boundary | Mandatory? | Coded or free text |
|---|---|---|---|---|---|
| `Customer` | `customer` | Customer number; the primary key, used as `record_id` | `Optional[str]` | not enforced (`api/models.py:220-223`); 100% populated | Coded — SAP-assigned number |
| `ECC Customer Number` | `ecc_customer_number` | Predecessor-system key; the `record_id` fallback (`api/models.py:229-231`) | `Optional[str]` | no; 0% populated | Coded |
| `Central Deletion Flag` | `central_deletion_flag` | SAP deletion marker | `Optional[str]` | no; 0% | Coded — SAP flag |
| `Comments` | `comments` | Free note | `Optional[str]` | no; 0% | Free text |
| `Account group` | `account_group` | SAP account group; drives dedup scoring bands (`dedup/weights.json`) | `Optional[str]` | no; 100% | Coded — the `DRIT`/`DRID` value is one of the open confirmations (`dedup/weights.json:2`) |
| `Company Code`, `Sales Organization`, `Distribution Channel`, `Division` | same | SAP org units | `Optional[str]` | no; 0% each | Coded |
| `Name 1` | `name_1` | Organisation or company name | `Optional[str]` | flagged by `G2-VAL-001` when blank; 99% | Free text |
| `Name 2` | `name_2` | Department / sub-unit | `Optional[str]` | no; 32% | Free text |
| `Name 3`, `Name 4` | `name_3`, `name_4` | Further name overflow slots | `Optional[str]` | no; 2.6% / 0.6% | Free text |
| `Street 1` | `street_1` | Primary street line | `Optional[str]` | no; 100% | Free text |
| `House Number` | `house_number` | Dedicated house number | `Optional[str]` | no; 44.2% | Free text (numeric by convention only) |
| `Street 2` … `Street 5` | `street_2`…`street_5` | Additional address lines | `Optional[str]` | no; 7.0% / 0.8% / 0.8% / 0.8% | Free text |
| `PO Box` | `po_box` | Dedicated PO-box slot | `Optional[str]` | no; 1.0% | Free text |
| `Country/Region Key` | `country_region_key` | Country | `Optional[str]` | `G2-VAL-008`; 99% | **Coded — ISO 3166-1 alpha-2**, enforced only by `G4-ADDR-027` (`enrichment/issue_detection.py:459-463`) |
| `Postal Code` | `postal_code` | Postal code | `Optional[str]` | `G2-VAL-002`; 99% | **Coded per country**, checked only for `US` and `CA` (`enrichment/issue_detection.py:167-170`) |
| `City` | `city` | City | `Optional[str]` | no; 100% | Free text |
| `Region` | `region` | State / region | `Optional[str]` | `G2-VAL-004`; 96.4% | Coded in SAP; unvalidated here. DS can bind a rule to a reference table of US state codes (`CONTEXT-EXTERNAL.md:354-357`) |
| `Language Key` | `language_key` | SAP language key | `Optional[str]` | `G2-VAL-006`; 99% | Coded |
| `Reconciliation acct` | `reconciliation_acct` | GL reconciliation account | `Optional[str]` | no; 0% | Coded |
| `Tax Jurisdiction` | `tax_jurisdiction` | Tax jurisdiction code | `Optional[str]` | `G2-VAL-003`; 35% | Coded |
| `Central delivery block`, `Delivery Priority`, `Shipping Conditions`, `Delivering Plant` | same | SAP logistics control | `Optional[str]` | no; 0% each | Coded |
| `Created On` | `created_on` | Creation date | `Optional[str]` | no; 0% | Coded (date) — carried as a string, never parsed |
| `Created By` | `created_by` | SAP user id; personal data (`05_DATA_MODEL.md:1063`) | `Optional[str]` | no; 0% | Coded |
| `VAT Registration No.` | `vat_registration_no` | VAT number | `Optional[str]` | no; 54.2% | Coded per country; unvalidated here |
| `Search Term 1` | `search_term_1` | SAP SORT1, ≤32 chars | `Optional[str]` | `G2-VAL-007`; **0%** | Free text with a length convention (`enrichment/search_terms.py:392,403-410`) |
| `Search Term 2` | `search_term_2` | SAP SORT2, ≤32 chars | `Optional[str]` | no; **0%** | As above |
| `Terms of Payment Contact` | `terms_of_payment_contact` | Payment terms | `Optional[str]` | no | Coded — **but see §1.4, the workbook column is named `Terms of Payment` and does not bind** |
| `Care Of` / `c/o` | `care_of` | Care-of party | `Optional[str]` | no | Free text; **no SAP column** (`api/models.py:200-206`) |
| `Contact` | `contact` | Contact person | `Optional[str]` | no; 19.6% | Free text; **no SAP column** (`api/models.py:200-206`) |
| `Email` | `email` | Contact e-mail | `Optional[str]` | no | Free text; **no SAP column** (`api/models.py:200-206`) |

### 1.3 Fields SAP constrains versus fields that accept anything

The distinction that matters for Chapter 2 is not what SAP permits but what survives to this
pipeline. Three tiers are evidenced:

1. **Coded in SAP and checked here (2 fields).** `Country/Region Key` and `Postal Code` are the
   only fields any rule tests for conformance, by `G4-ADDR-027` and `G4-ADDR-026`
   (`enrichment/issue_detection.py:451-463`). The postal check covers `US` and `CA` only
   (`:167-170`), so an equivalently malformed German or British postcode raises nothing —
   demonstrated on REC-13 (`03b_EXEMPLARS.md:420-422`).
2. **Coded in SAP and carried unchecked (14 fields).** `Account group`, `Company Code`,
   `Sales Organization`, `Distribution Channel`, `Division`, `Language Key`,
   `Reconciliation acct`, `Tax Jurisdiction`, `Central delivery block`, `Delivery Priority`,
   `Shipping Conditions`, `Delivering Plant`, `Created On`, `Created By`. Blankness is flagged
   for `Language Key` and `Tax Jurisdiction`; the *value* is never validated against a code list
   anywhere in this repository.
3. **Free text (the rest).** The four name slots, the five street slots, `PO Box`, `City`,
   `Comments`, both search terms, and the three auxiliary contact fields. These carry the entire
   data-quality problem: §3.4 measures that `Street 1`, `Name 1`, `Name 2` and `House Number`
   are implicated on 46.0%, 14.4%, 18.6% and 36.8% of records respectively.

### 1.4 Fields whose SAP semantics differ from how the data uses them

Six divergences are evidenced. Each is a case where the column's declared meaning and its
observed content do not agree.

| Field | SAP / contract semantics | How the data actually uses it | Evidence |
|---|---|---|---|
| `Street 1` | One street line | Carries a six-segment pipe-delimited department hierarchy ending in the street: `Bioanalytical Methods Branch \| Division of … \| … \| 5100 Paint Branch Parkway` | `03b_EXEMPLARS.md:145` (REC-02) |
| `House Number` | Dedicated numeric slot paired with `Street 1` | Blank on 55.8% of rows; the number sits inline in the street instead, which is exactly what `G1-ADDR-001` detects on 36.8% of records | run output §1.1, §3.3; rule at `enrichment/issue_detection.py:264-269` |
| `Name 1` | Organisation or company name | Holds address text (`G1-CROSS-001`, 6 records), an opaque internal code (`G1-NAME-013`, 6 records), and — per the pipeline's own UC 7 — sometimes a natural person, extracted out into `Contact` and recorded by `_name1_was_person` | run output §3.3; `05_DATA_MODEL.md:1065`; `enrichment/orchestrator.py:1831-1836` |
| `Name 2` | Department / sub-unit (`dedup/models.py:38`) | On 6 records it repeats `Name 1` verbatim (`G3-NAME-005`); on REC-04 it carries an administrative desk (`Accounts Payable`) where no organisation name exists at all | run output §3.3; `03b_EXEMPLARS.md:202,309-310` |
| `Contact`, `Email`, `Care Of` | The model states plainly: *"The SAP export has no dedicated contact-person / email / c-o column"* (`api/models.py:200-206`) | The source workbook nevertheless ships a `Contact` column, populated on 19.6% of rows, and the enriched workbook adds `Care Of` and `Email` columns | run output §1.1; workbook headers, run output §6 |
| `Terms of Payment` | The model declares `terms_of_payment_contact`, alias `Terms of Payment Contact` (`api/models.py:195-198`) | The workbook column is named `Terms of Payment`. Header normalisation strips case and non-alphanumerics only (`api/routes.py:115-122`), so `termsofpayment` ≠ `termsofpaymentcontact` and **the column is silently dropped** — it is not rejected, because no `extra="forbid"` is declared | run output §1.2 |

The `Terms of Payment` mismatch is a finding not previously recorded in Passes 0–9 and is
reported here rather than carried into `08_GAPS.md`, which this packet does not edit. Fifteen
further workbook columns are likewise unmapped, but those are by design: they are the Phase-2
scoring inputs (`Sales_Order_*`, `Equipment_Total_Count`, `SleepingCustomer`, `CustomerStatus`,
`SF_ID_*`), consumed by `/api/dedup/score` through `ScoringRow`, not by `/enrich`
(`dedup/scoring.py:73-77`). The full list is in run output §1.2.

---

## 2 · Taxonomy by data-quality dimension

The catalogue is declared at `enrichment/issue_detection.py:75-118` and is organised there by
group code `G1`–`G5`. Below it is re-presented by data-quality dimension. Group is retained as a
column so both orderings resolve.

### 2.1 Counts — stated from the source, not the docstring

The module docstring at `enrichment/issue_detection.py:1-29` states "36-code Issue Catalogue"
and "Coverage: 34 of the 36 catalogue codes are emitted." **Both figures are stale against the
source in the same file** (`08_GAPS.md:170-178`, G-9). The figures below are counted from
`ISSUE_CATALOGUE` and from the emission sites directly.

| Quantity | Value | How established |
|---|---|---|
| Codes **declared** | **37** | Keys of `ISSUE_CATALOGUE` (`enrichment/issue_detection.py:75-118`) |
| Codes with a **live emission site** | **35** | Every declared code except the two annotated `# LLM-only — never emitted` (`:88`, `:112`) |
| Codes **observable in practice** | **at most 34** | One of the 35 sites is unreachable — see below |
| Codes **observed in this dataset** | **31** | Measured, run output §3.3 |

**The two codes with no emission site** (declared for completeness, decidable only by the
pipeline's LLM residual classifier, `enrichment/issue_detection.py:18-24`):

- `G1-ADDR-009` Unclassified Residual in Address (`:88`, comment at `:317`)
- `G4-ADDR-025` Sub-location Overflow Beyond Street 5 (`:112`, comment at `:465`)

**The one code whose emission site cannot be reached:**

- `G2-CONTACT-008` No Contact and No Department (`:367`). Its guard is
  `if "G2-NAME-012" not in found`, but `G2-NAME-012` is added at `:342-343` under the *identical*
  gate — `looks_like_university_or_research_institute(name_1) and name2_blank` — so by the time
  the guard is evaluated the condition it tests is always false. Proof at `03_ALGORITHMS.md`
  Part H §1.3 (`:5042-5047`); demonstrated concretely on
  `tests/fixtures/research_missing_name2_with_contact.json` (`03b_EXEMPLARS.md:252-260`). The
  code comment at `:360-363` states the suppression as intended; unlike the two LLM-only codes it
  is not annotated as never-emitted at its catalogue entry (`08_GAPS.md:623-630`, G-46).

**The three codes reachable but absent from this dataset:** `G1-NAME-001`, `G3-ADDR-012`,
`G3-ADDR-013` (run output §3.3). `G3-ADDR-012` is raised by no *pre-enrichment* record but is
introduced by the pipeline on REC-01 (`03b_EXEMPLARS.md:96-98,129-135`). The inputs that would
exercise the other two are named at `03b_EXEMPLARS.md:90-94`.

### 2.2 The five dimensions

Dimension assignment is this packet's; it is derived from what each rule body tests, and the
rule's line range is cited so the assignment can be checked. **Det.** = deterministic (regex /
string only, per the module's purity constraint at `enrichment/issue_detection.py:9-12`);
**LLM** = model-assisted. **Live?** = has a reachable emission site.

#### Cross-field placement — 13 codes

Data present on the record but sitting in the wrong column.

| Code | Group | Description | Det./LLM | Live? | Rule |
|---|---|---|---|---|---|
| `G1-CROSS-001` | G1 | A name field contains street, sub-location, or PO-box text | Det. | yes | `:226-229` |
| `G1-CROSS-002` | G1 | A street field contains an organisation name with no street-type word to anchor it | Det. | yes | `:234-243` |
| `G1-CROSS-003` | G1 | A name or street field contains an e-mail, phone, URL, c/o-ATTN prefix, or person name | Det. | yes | `:247-262` |
| `G1-ADDR-001` | G1 | The house number is inside the street while the dedicated column is blank | Det. | yes | `:266-270` |
| `G1-ADDR-003` | G1 | A sub-location (Suite / Floor / Bldg / Room) is inside the street | Det. | yes | `:273-276` |
| `G1-ADDR-004` | G1 | A PO-box pattern is inside a street field | Det. | yes | `:279-282` |
| `G1-ADDR-006` | G1 | A mail or drop code is inside a street field | Det. | yes | `:285-288` |
| `G1-ADDR-011` | G1 | A department label is inside a street field | Det. | yes | `:291-294` |
| `G1-NAME-001` | G1 | Name 1 and Name 2 read as one continuous organisation name split across slots | Det. (heuristic) | yes | `:299-305` |
| `G1-NAME-004` | G1 | Name 2 is blank while Name 3 is populated — the hierarchy skips a level | Det. | yes | `:308-309` |
| `G1-NAME-013` | G1 | A name field's entire value is an internal or opaque code | Det. | yes | `:312-315` |
| `G1-ADDR-009` | G1 | Address residual that no deterministic rule can classify | **LLM** | **no** | declared `:88`; `:317` |
| `G4-ADDR-025` | G4 | Sub-location content with no remaining street slot to hold it | **LLM** | **no** | declared `:112`; `:465` |

#### Completeness — 11 codes

A required or expected value is absent.

| Code | Group | Description | Det./LLM | Live? | Rule |
|---|---|---|---|---|---|
| `G2-VAL-001` | G2 | Name 1 blank | Det. | yes | `:129-137, 330-334` |
| `G2-VAL-002` | G2 | Postal Code blank | Det. | yes | as above |
| `G2-VAL-003` | G2 | Tax Jurisdiction blank | Det. | yes | as above |
| `G2-VAL-004` | G2 | Region blank | Det. | yes | as above |
| `G2-VAL-006` | G2 | Language Key blank | Det. | yes | as above |
| `G2-VAL-007` | G2 | Search Term 1 blank | Det. | yes | as above |
| `G2-VAL-008` | G2 | Country/Region Key blank | Det. | yes | as above |
| `G2-NAME-009` | G2 | A granular research group in Name 2 with no parent department in Name 3 or Name 4 | Det. | yes | `:347-351` |
| `G2-NAME-012` | G2 | Name 1 reads as a university or research institute and Name 2 is blank | Det. | yes | `:342-343` |
| `G2-CONTACT-008` | G2 | As above, with no contact to enrich from | Det. | **site unreachable** | `:364-367` |
| `G2-CONTACT-009` | G2 | As above, with exactly one contact — the department is recoverable from it | Det. | yes | `:364-369` |

The seven `G2-VAL-*` rules are **column-gated**: a rule fires only when the column exists in the
file and is blank; a column absent from the file entirely is skipped rather than reported missing
(`enrichment/issue_detection.py:123-128, 330-332`). This is why the same record yields different
code sets from an XLSX and from a JSON fixture (`03b_EXEMPLARS.md:262-267`).

#### Uniqueness — 7 codes

The same thing recorded more than once, or two things recorded in one place.

| Code | Group | Description | Det./LLM | Live? | Rule |
|---|---|---|---|---|---|
| `G3-NAME-003` | G3 | A "doing business as" pattern packs two identities into one name field | Det. | yes | `:381-384` |
| `G3-NAME-005` | G3 | Two adjacent name fields hold the same value | Det. | yes | `:387-390` |
| `G3-ADDR-005` | G3 | Two or more PO boxes across the street slots and the dedicated column | Det. | yes | `:393-401` |
| `G3-ADDR-012` | G3 | The same street address appears in more than one slot, including SAP's house-number split | Det. | yes | `:409-417` |
| `G3-ADDR-013` | G3 | Two *distinct* real street addresses on one record | Det. | yes | `:420-424` |
| `G3-ADDR-014` | G3 | A PO box and a real street both present | Det. | yes | `:427-428` |
| `G3-CONTACT-007` | G3 | More than one contact in the Contact field | Det. | yes | `:431-432` |

These are **intra-record** uniqueness only. Cross-record duplication — two customer numbers for
one organisation — is not in this catalogue at all; it is Phase 2's subject and is measured
separately in §5.

#### Representational consistency — 6 codes

The value is present and in the right place but not in its canonical form.

| Code | Group | Description | Det./LLM | Live? | Rule |
|---|---|---|---|---|---|
| `G4-NAME-015` | G4 | Name 1–4 combined exceed the SAP 140-character limit | Det. | yes | `:441-443`, limit `:121` |
| `G4-ADDR-008` | G4 | A bare sub-location marker (`Ste`, `Floor`) with no value after it | Det. | yes | `:446-449` |
| `G4-ADDR-026` | G4 | The postal code does not match its country's registered format (`US`, `CA` only) | Det. | yes | `:451-456` |
| `G4-ADDR-027` | G4 | The country is not in canonical ISO 3166-1 alpha-2 form | Det. | yes | `:458-463` |
| `G5-NAME-001` | G5 | Name 1 carries an abbreviation token rather than the official form | Det. (heuristic) | yes | `:472-475` |
| `G5-NAME-002` | G5 | A unit name (Name 2–4) carries an abbreviation token | Det. (heuristic) | yes | `:477-481` |

#### Accuracy — 0 codes

**No code in the catalogue measures accuracy**, and this is a structural consequence of the
module's first design constraint, not an omission. Issue detection is required to be *"pure and
deterministic — regex / string checks only. No enrichment, no LLM call, no network/external
I/O"*, so that the same rule set can run on a raw file and on a post-pipeline file and "the count
delta is the story the catalogue is built around" (`enrichment/issue_detection.py:7-12`; recorded
as decision D-20 at `09_DECISIONS.md:764-781`). A rule with no external reference can test
*conformance* — does this postal code match the pattern its country registers — but never
*correctness*: whether the record's postal code is the organisation's actual postal code.

`G4-ADDR-026` and `G4-ADDR-027` are conformance rules and are classified above as
representational consistency for that reason.

Accuracy is addressed by a different mechanism entirely: the enrichment tiers, which resolve a
name against the ROR registry (`enrichment/tier1_ror.py`), a company against GLEIF
(`enrichment/tier1_lei.py`), and a department against retrieved web evidence. Those produce no
issue code. The consequence for the thesis is that **the before/after issue count measures
conformance improvement, never accuracy improvement** — the point `07_EVALUATION.md:712-718`
records as construct circularity in M-1.

### 2.3 Dimension totals

| Dimension | Codes | Deterministic | Model-assisted | Live emission site |
|---|---:|---:|---:|---:|
| Cross-field placement | 13 | 11 | 2 | 11 |
| Completeness | 11 | 11 | 0 | 10 (`G2-CONTACT-008` unreachable) |
| Uniqueness | 7 | 7 | 0 | 7 |
| Representational consistency | 6 | 6 | 0 | 6 |
| Accuracy | 0 | — | — | — |
| **Total** | **37** | **35** | **2** | **34 reachable of 35 declared sites** |

---

## 3 · Frequency evidence — measured

### 3.1 Dataset used

| Property | Value |
|---|---|
| File | `PresentationTestData.xlsx` |
| Path | repository root, committed at this commit |
| Sheet | `TestData_500` (the active sheet) |
| Rows read | **500**, read from the data by the parser — not taken from any summary |
| Columns | 53, of which 37 bind to `EnrichmentRecord` fields |
| Sample or full extract? | **Neither is established.** ⚠ UNVERIFIED — whether any row derives from a production SAP extract. No sampling frame, extraction query, date, or source system is recorded anywhere in the repository (`03b_EXEMPLARS.md:26-33`; `07_EVALUATION.md:540-548`). Many rows share filler values (`MAIN ST`, house number `100`, tax jurisdiction `1200000000`); the workbook's own summary claims 283 of the 500 are "Records using REAL source data" but no artefact identifies which. |

This is the only dataset in the repository against which the detector can be run over a
realistic record population. `PresentationTestData_subset.xlsx` holds 23 data rows and is used
only for the coverage census (`03b_EXEMPLARS.md:22`); the eight JSON fixtures hold at most ten
records each.

**The external-validity limit must be stated wherever these figures are used.** The figures
below are exact for this file. They are not established as representative of the production
`Legacy` table, whose row count is itself not in the repository — ⚠ MEASUREMENT REQUIRED:
`SELECT COUNT(*) FROM test_77.Legacy` (`02_ARCHITECTURE.md:491-493`).

### 3.2 Method

`scripts/ch02_measure.py` runs the deterministic detector over every data row through the same
record-construction path the `POST /issues` endpoint uses — `_parse_xlsx` → `_rows_to_records` →
`_present_fields` → `detect_issues(record, present)` (`api/routes.py:603-606`). Nothing is
mocked, no external call is made, and no file is modified.

Field attribution (§3.4) uses per-code *locators* that mirror each rule body and return the
specific column that made the rule fire. The script asserts, per row, that the set of codes its
locators fire on equals the set `detect_issues` returns, and exits non-zero on any divergence.
The run below reports **500/500 rows agree**, so the field counts are attributable to the
detector's own logic rather than to a re-implementation of it.

### 3.3 Command

```
cd c:\Users\apoorva.ajay\Downloads\ApoorvaThesis\ApoorvaThesis\enrichment_api
.venv\Scripts\python.exe -m scripts.ch02_measure
```

### 3.4 Verbatim output

```text
==============================================================================
Chapter 2 measurement run
==============================================================================

##############################################################################
# 1 - FIELD POPULATION (structure evidence)
##############################################################################

dataset : PresentationTestData.xlsx  (500 rows, 53 columns)

--- 1.1 Populated rate of every column the model maps ---
SAP column                 | model field              | populated |      %
---------------------------+--------------------------+-----------+-------
Customer                   | customer                 |       500 | 100.0%
ECC Customer Number        | ecc_customer_number      |         0 |  0.0%
Central Deletion Flag      | central_deletion_flag    |         0 |  0.0%
Comments                   | comments                 |         0 |  0.0%
Account group              | account_group            |       500 | 100.0%
Company Code               | company_code             |         0 |  0.0%
Sales Organization         | sales_organization       |         0 |  0.0%
Distribution Channel       | distribution_channel     |         0 |  0.0%
Division                   | division                 |         0 |  0.0%
Name 1                     | name_1                   |       495 | 99.0%
Name 2                     | name_2                   |       160 | 32.0%
Name 3                     | name_3                   |        13 |  2.6%
Name 4                     | name_4                   |         3 |  0.6%
Contact                    | contact                  |        98 | 19.6%
Street 1                   | street_1                 |       500 | 100.0%
House Number               | house_number             |       221 | 44.2%
Street 2                   | street_2                 |        35 |  7.0%
Street 3                   | street_3                 |         4 |  0.8%
Street 4                   | street_4                 |         4 |  0.8%
Street 5                   | street_5                 |         4 |  0.8%
PO Box                     | po_box                   |         5 |  1.0%
Country/Region Key         | country_region_key       |       495 | 99.0%
Postal Code                | postal_code              |       495 | 99.0%
City                       | city                     |       500 | 100.0%
Region                     | region                   |       482 | 96.4%
Language Key               | language_key             |       495 | 99.0%
Reconciliation acct        | reconciliation_acct      |         0 |  0.0%
Tax Jurisdiction           | tax_jurisdiction         |       175 | 35.0%
Central delivery block     | central_delivery_block   |         0 |  0.0%
Delivery Priority          | delivery_priority        |         0 |  0.0%
Shipping Conditions        | shipping_conditions      |         0 |  0.0%
Delivering Plant           | delivering_plant         |         0 |  0.0%
Created On                 | created_on               |         0 |  0.0%
Created By                 | created_by               |         0 |  0.0%
VAT Registration No.       | vat_registration_no      |       271 | 54.2%
Search Term 1              | search_term_1            |         0 |  0.0%
Search Term 2              | search_term_2            |         0 |  0.0%

--- 1.2 Columns present in the file that the model does not map ---
    (accepted and silently discarded: no extra='forbid' is declared,
     api/models.py:40)
    Terms of Payment
    Sales_Order_Last_Used
    Sales_Order_Total_Count
    Sales_Order_Partner_Last_Used
    Sales_Order_Partner_Total_Count
    Equipment_Total_Count
    SleepingCustomer
    CustomerStatus
    SF_ID_Biosystems
    SF_ID_AXS
    SF_ID_3
    SF_ID_4
    SF_ID_5
    SF_ID_6
    SF_ID_7
    SF_ID_8

##############################################################################
# 3 - ISSUE FREQUENCY
##############################################################################

dataset          : PresentationTestData.xlsx
path             : PresentationTestData.xlsx
sheet            : first (active) sheet, header row 1
columns in file  : 53
model fields seen: 37  (drives G2-VAL-* column gating)

--- 3.1 Totals ---
total records read from the data          : 500
records with >= 1 issue                   : 500  (100.0%)
records with 0 issues                     : 0  (0.0%)
total issue instances (code x record)     : 1360
mean issue codes per record (all records) : 2.72
mean issue codes per affected record      : 2.72

--- 3.1b Same totals excluding G2-VAL-007 (Search Term 1 Missing) ---
records with >= 1 other issue             : 442  (88.4%)
records with no other issue               : 58  (11.6%)
issue instances excluding G2-VAL-007     : 860
mean per record (all)                     : 1.72
mean per affected record                  : 1.95
 codes on record |  records |  % of 500
-----------------+----------+----------
               0 |       58 |    11.6%
               1 |      156 |    31.2%
               2 |      188 |    37.6%
               3 |       71 |    14.2%
              4+ |       27 |     5.4%

--- 3.2 Issue-count distribution per record ---
 codes on record |  records |  % of 500
-----------------+----------+----------
               1 |       58 |    11.6%
               2 |      156 |    31.2%
               3 |      188 |    37.6%
               4 |       71 |    14.2%
               5 |       21 |     4.2%
               6 |        5 |     1.0%
               7 |        1 |     0.2%
-----------------+----------+----------
               1 |       58 |    11.6%
               2 |      156 |    31.2%
               3 |      188 |    37.6%
              4+ |       98 |    19.6%

--- 3.3 Per-code frequency, ranked by records affected ---
rank | code            | grp | records |  % of 500 | name
-----+-----------------+-----+---------+-----------+---------------------------------------------
   1 | G2-VAL-007      | G2  |     500 |   100.0% | Search Term 1 Missing
   2 | G2-VAL-003      | G2  |     325 |    65.0% | Tax Jurisdiction Missing
   3 | G1-ADDR-001     | G1  |     184 |    36.8% | House Number Embedded in Street
   4 | G1-ADDR-003     | G1  |      48 |     9.6% | Sub-location Embedded in Street
   5 | G5-NAME-002     | G5  |      42 |     8.4% | Unit Name Not in Official Form
   6 | G2-NAME-012     | G2  |      33 |     6.6% | Research Institution Missing Department
   7 | G5-NAME-001     | G5  |      28 |     5.6% | Organisation Name Not in Official Form
   8 | G4-ADDR-026     | G4  |      27 |     5.4% | Postal Code Format Invalid
   9 | G1-CROSS-002    | G1  |      18 |     3.6% | Org Name in Address Field
  10 | G2-VAL-004      | G2  |      18 |     3.6% | Region Missing
  11 | G1-ADDR-004     | G1  |      17 |     3.4% | PO Box Embedded in Street
  12 | G4-ADDR-008     | G4  |      12 |     2.4% | Bare Sub-location Marker Without Value
  13 | G1-ADDR-006     | G1  |       9 |     1.8% | Mail Code in Street Field
  14 | G1-ADDR-011     | G1  |       9 |     1.8% | Department Label in Street Field
  15 | G1-CROSS-003    | G1  |       9 |     1.8% | Contact Information in Wrong Field
  16 | G1-NAME-004     | G1  |       7 |     1.4% | Name 2 Empty With Name 3 Populated
  17 | G1-CROSS-001    | G1  |       6 |     1.2% | Address Content in Name Field
  18 | G1-NAME-013     | G1  |       6 |     1.2% | SAP Internal Code in Name Field
  19 | G2-NAME-009     | G2  |       6 |     1.2% | Lab Without Department
  20 | G3-NAME-003     | G3  |       6 |     1.2% | DBA Pattern in Name Field
  21 | G3-NAME-005     | G3  |       6 |     1.2% | Duplicate Name Across Fields
  22 | G4-ADDR-027     | G4  |       6 |     1.2% | Country Code Not ISO 2-letter
  23 | G2-VAL-001      | G2  |       5 |     1.0% | Name 1 Missing
  24 | G2-VAL-002      | G2  |       5 |     1.0% | Postal Code Missing
  25 | G2-VAL-006      | G2  |       5 |     1.0% | Language Missing
  26 | G2-VAL-008      | G2  |       5 |     1.0% | Country Missing
  27 | G3-ADDR-005     | G3  |       5 |     1.0% | Multiple PO Boxes on Record
  28 | G3-CONTACT-007  | G3  |       5 |     1.0% | Multiple Contacts on Record
  29 | G3-ADDR-014     | G3  |       3 |     0.6% | PO Box and Street Both Present
  30 | G4-NAME-015     | G4  |       3 |     0.6% | Name Overflow Beyond Name 4
  31 | G2-CONTACT-009  | G2  |       2 |     0.4% | Department Missing And Enrichable from Contact

codes observed in this dataset : 31 of 37 declared
codes never observed here      : 6
    G1-NAME-001     Name Overflow Across Fields
    G1-ADDR-009     Unclassified Residual in Address
    G2-CONTACT-008  No Contact and No Department
    G3-ADDR-012     Duplicate Street Across Fields
    G3-ADDR-013     Two Distinct Street Addresses on Record
    G4-ADDR-025     Sub-location Overflow Beyond Street 5

--- 3.4 Distinct SAP columns implicated per record ---
locator fidelity self-check: 500/500 rows agree with detect_issues
mean distinct columns implicated, all records      : 3.10
mean distinct columns implicated, affected records : 3.10
max distinct columns implicated on one record      : 8
 columns |  records
---------+---------
       1 |       58
       2 |      147
       3 |       78
       4 |      156
       5 |       30
       6 |       28
       7 |        1
       8 |        2

most-implicated columns (records in which the column is named by >=1 code):
    Search Term 1           500  (100.0%)
    Tax Jurisdiction        325  (65.0%)
    Street 1                230  (46.0%)
    House Number            184  (36.8%)
    Name 2                   93  (18.6%)
    Name 1                   72  (14.4%)
    Country/Region Key       34  ( 6.8%)
    Postal Code              32  ( 6.4%)
    Name 3                   19  ( 3.8%)
    Street 2                 18  ( 3.6%)
    Region                   18  ( 3.6%)
    Name 4                    9  ( 1.8%)
    Contact                   7  ( 1.4%)
    PO Box                    5  ( 1.0%)
    Language Key              5  ( 1.0%)

##############################################################################
# 3.5 - THE WORKBOOK'S OWN ORACLE, COMPARED
##############################################################################

Sheets read: 'Issue_Counts', 'Oracle_Summary' of the same workbook.
The workbook describes these as a ground-truth answer key
(Oracle_Summary row 2). They are compared here, never used as a source.

--- 3.5a Headline claims vs measured ---
metric                           |   oracle |  measured
---------------------------------+----------+----------
Total records                    |      500 |       500
Records with >=1 issue           |      400 |       500
Clean records                    |      100 |         0
Total issue instances            |      573 |      1360
Distinct issue codes covered     |    36/36 |     31/37

--- 3.5b Per-code: oracle count vs measured count ---
code            |  oracle |  measured |   delta | note
----------------+---------+-----------+---------+-----------------------------------
G1-CROSS-001    |       6 |         6 |      +0 | 
G1-CROSS-002    |       6 |        18 |     +12 | disagree
G1-CROSS-003    |      20 |         9 |     -11 | disagree
G1-ADDR-001     |     177 |       184 |      +7 | disagree
G1-ADDR-003     |      41 |        48 |      +7 | disagree
G1-ADDR-004     |      11 |        17 |      +6 | disagree
G1-ADDR-006     |       6 |         9 |      +3 | disagree
G1-ADDR-011     |       6 |         9 |      +3 | disagree
G1-NAME-001     |       7 |         0 |      -7 | disagree
G1-NAME-004     |       6 |         7 |      +1 | disagree
G1-NAME-013     |       6 |         6 |      +0 | 
G1-ADDR-009     |       6 |         0 |      -6 | disagree
G2-VAL-001      |       5 |         5 |      +0 | 
G2-VAL-002      |       5 |         5 |      +0 | 
G2-VAL-003      |       6 |       325 |    +319 | disagree
G2-VAL-004      |       2 |        18 |     +16 | disagree
G2-VAL-006      |       5 |         5 |      +0 | 
G2-VAL-007      |       9 |       500 |    +491 | disagree
G2-VAL-008      |       5 |         5 |      +0 | 
G2-NAME-009     |       6 |         6 |      +0 | 
G2-NAME-012     |      73 |        33 |     -40 | disagree
G2-CONTACT-008  |       6 |         0 |      -6 | disagree
G2-CONTACT-009  |       6 |         2 |      -4 | disagree
G3-NAME-003     |       6 |         6 |      +0 | 
G3-NAME-005     |       6 |         6 |      +0 | 
G3-ADDR-005     |       5 |         5 |      +0 | 
G3-ADDR-012     |       - |         0 |       - | absent from the oracle sheet
G3-ADDR-013     |       5 |         0 |      -5 | disagree
G3-ADDR-014     |       2 |         3 |      +1 | disagree
G3-CONTACT-007  |       5 |         5 |      +0 | 
G4-NAME-015     |       6 |         3 |      -3 | disagree
G4-ADDR-008     |       6 |        12 |      +6 | disagree
G4-ADDR-025     |       4 |         0 |      -4 | disagree
G4-ADDR-026     |       6 |        27 |     +21 | disagree
G4-ADDR-027     |       6 |         6 |      +0 | 
G5-NAME-001     |      79 |        28 |     -51 | disagree
G5-NAME-002     |      11 |        42 |     +31 | disagree

codes in the declared catalogue      : 37
codes listed in the oracle sheet     : 36
declared codes absent from the oracle: ['G3-ADDR-012']
oracle codes not in the catalogue    : []
codes where oracle == measured       : 12 of 37

##############################################################################
# 5 - DUPLICATE PREVALENCE
##############################################################################

dataset : PresentationTestData.xlsx  (500 rows)
method  : STEP A only - deterministic block + exact-signature collapse
          (dedup/signatures.py). No LLM adjudication was run.
blocking: fallback derive_block_id over normalised
          (country, postal_code, street, house_no) - the file carries no Block ID column.

--- 5.1 Address blocks ---
distinct derived blocks          : 384
rows in a block of size 1        : 356
rows in a block of size > 1      : 144  (28.8%)
largest block                    : 20 rows
block size |  blocks |   rows
-----------+---------+-------
         1 |     356 |    356
         2 |      14 |     28
         3 |       4 |     12
         4 |       3 |     12
         7 |       1 |      7
         9 |       1 |      9
        12 |       1 |     12
        14 |       1 |     14
        15 |       2 |     30
        20 |       1 |     20

--- 5.2 Exact-signature collapse within blocks ---
distinct signatures across all blocks       : 455
signatures covering > 1 row (exact dupes)   : 31
rows inside such a signature                : 76  (15.2%)
blocks holding >1 distinct signature        : 15
  (these are the blocks an LLM adjudication would be asked to decide;
   whether their signatures merge is NOT determined by this run.)
rows/signature | signatures
---------------+-----------
             1 |        424
             2 |         20
             3 |          8
             4 |          3

##############################################################################
# 6 - ENRICHMENT -> DEDUP COUPLING (registry identifiers)
##############################################################################

pre-enrichment  : PresentationTestData.xlsx
post-enrichment : PresentationTestData_enriched_checked_v1.xlsx

column 'ROR ID'             in pre-enrichment file : False
column 'LEI ID'             in pre-enrichment file : False
column 'Domain'             in pre-enrichment file : False
column 'Department Domain'  in pre-enrichment file : False
column 'Record Type'        in pre-enrichment file : False

rows in the enriched workbook            : 500
rows with a non-empty ROR ID             : 194  (38.8%)
rows with a non-empty LEI ID             : 40  (8.0%)
rows with either identifier              : 219  (43.8%)
rows with both identifiers               : 15  (3.0%)
distinct ROR ID values                   : 129
distinct LEI ID values                   : 37

--- 6.1 Registry ids shared by more than one row (the dedup hint) ---
identifier values carried by >1 row      : 21
rows carrying such a shared identifier   : 89  (17.8%)
kind    |  rows | value
--------+-------+-----------------------------------------
ROR ID  |    37 | https://ror.org/02y3ad647
ROR ID  |     5 | https://ror.org/02dgjyy92
ROR ID  |     5 | https://ror.org/05g3dte14
ROR ID  |     4 | https://ror.org/032db5x82
ROR ID  |     3 | https://ror.org/00dmfq477
ROR ID  |     3 | https://ror.org/039gdg280
ROR ID  |     3 | https://ror.org/043mz5j54
ROR ID  |     3 | https://ror.org/049pfb863
LEI ID  |     2 | 3912006KG4HA68NWAZ84
LEI ID  |     2 | 529900TESWV2RIPW4B41
LEI ID  |     2 | 765LHXWGK1KXCLTFYQ30
ROR ID  |     2 | https://ror.org/000pfrh90
ROR ID  |     2 | https://ror.org/008a2q193
ROR ID  |     2 | https://ror.org/00pwykz63
ROR ID  |     2 | https://ror.org/01xdqrp08
ROR ID  |     2 | https://ror.org/01xsqw823
ROR ID  |     2 | https://ror.org/02gz6gg07
ROR ID  |     2 | https://ror.org/02w5vdf29
ROR ID  |     2 | https://ror.org/03m1g2s55
ROR ID  |     2 | https://ror.org/042nb2s44
ROR ID  |     2 | https://ror.org/0533brp61

==============================================================================
end of run
==============================================================================
```

### 3.5 Reading the frequency figures

**Every record in the dataset carries at least one issue — but one code explains it.**
`G2-VAL-007` (Search Term 1 Missing) fires on 500 of 500 because `Search Term 1` is blank on
every row (run output §1.1) and it is a *derived* output the pipeline populates, not an input a
data-entry clerk omits. Reported without that qualification, "100% of records are defective"
would mislead. Excluding it, **442 records (88.4%) carry at least one issue and 58 (11.6%) are
clean**. Both figures should appear in the chapter, in that order.

**The problem is multi-issue, not single-issue.** Excluding `G2-VAL-007`, 188 records carry two
codes, 71 carry three, and 27 carry four or more; the single worst record carries seven codes
including `G2-VAL-007`. This is what makes rule-by-rule remediation expensive: a record is
rarely fixed by one edit.

**The problem is concentrated in three columns.** `Street 1` is implicated on 46.0% of records,
`House Number` on 36.8%, `Name 2` on 18.6%. The mean record has **3.10 distinct SAP columns**
implicated. `G1-ADDR-001` alone — the house number sitting inside the street text rather than in
its dedicated column — accounts for 184 records, and is the structural defect the address stage
(`03_ALGORITHMS.md` Part G) exists to reverse.

**The two heaviest codes are absence, not corruption.** `G2-VAL-007` (100%) and `G2-VAL-003`
(65%) are both blank required columns. Of the 860 non-`G2-VAL-007` issue instances, the
completeness dimension still dominates. This matters for the chapter's argument: the dominant
problem in this data is *missing* information that must be *found*, which is what motivates a
retrieval-based pipeline over a rule-based cleanser.

**The dataset's own answer key does not match the implemented detector — 12 of 37 codes agree.**
The workbook ships `Oracle_Summary` and `Issue_Counts` sheets that it describes as a
"ground-truth answer key". Measured against the detector they diverge on 24 codes and on every
headline figure: the oracle claims 400 records with ≥1 issue against 500 measured, 573 issue
instances against 1,360, and "36/36 distinct codes covered" against 31 of 37 observed. Two
oracle rows count codes the deterministic detector *cannot emit at all* — `G1-ADDR-009` (6) and
`G4-ADDR-025` (4) are LLM-only, and `G2-CONTACT-008` (6) has an unreachable site. One declared
code, `G3-ADDR-012`, is absent from the oracle sheet entirely, which is the arithmetic behind
its "36" against the catalogue's 37.

The oracle was therefore authored against a specification of the catalogue, not against the
implemented rules. This extends `08_GAPS.md` G-17 (`:257-262`), which records the count
mismatch, with the per-code deltas. **No figure in this chapter should be sourced from those
sheets**, and any evaluation built on them inherits the divergence — the caution
`07_EVALUATION.md:550-556` states for M-1.

---

## 4 · Manual effort baseline

### 4.1 What is evidenced

The pre-pipeline correction process is evidenced qualitatively in the DATAshaper vendor
onboarding transcripts and in the production workflow table. Four mechanisms are recorded.

**Per-cell manual editing through the Excel add-in.** The vendor distinguishes the two routes
explicitly. Asked whether the add-in is where issues are resolved, the answer separates them:

> **Speaker 1:** "So basically, any resolution validation rule that I want to add, I will do it
> here. And the Excel add-in is just to view what my issues are. That's not where I resolve it."
> **Speaker 2:** "Yes. Actually, in the Excel add-in, I could say if for this one, I know the
> title would be misted, for example, I could do like this." … "So this is manual specific, and
> here it's more rule-based."
> (`Datashaper-Tutorial-Part2.txt:653-668`)

So two correction routes coexist: a rule authored in DATAshaper that applies to a class of
records, and a manual edit in the Excel add-in that applies to one cell of one record.
DATAshaper Studio later provides the same view inside the product
(`Datashaper-Tutorial-Part3.txt:35`).

**Issue assignment to a named person.** Each validation rule can carry a responsible person:

> "So this one is actually to say who needs to solve that. But for now, I will leave it empty.
> But if you have a team that's working on it, you can actually assign a responsible person."
> (`Datashaper-Tutorial-Part2.txt:809`)

The `Show mandatory only` filter and the code → field → description drill-down in the DS issues
view are the steward's working surface (`CONTEXT-EXTERNAL.md:362-386`).

**Reference tables extended by whoever is looking at the data.** Corrections accumulate into
shared reference tables rather than being applied once:

> "So this someone can it's looking at the data can actually expand." … "And then, yeah,
> everything they find, they can add. And after a while, I think we get to a better dataset."
> (`Datashaper-Tutorial-Part2.txt:1293-1298`)

**Golden-record selection was a manual review.** Asked directly whether identifying the golden
record is manual:

> **Speaker 1:** "And this cluster, when Burns says that we need to identify the golden record,
> so that is done with manual review?"
> **Speaker 2:** "Yes. Yes. Yes. Actually, I also put some logic to automate, and that was the
> scores. But it's still a bit tricky because, I mean, we can always put logic, but even if all
> the logic says that it's a duplicate, it might not be a duplicate."
> (`Datashaper-Tutorial-Part2.txt:1850-1856`)

That partial automation — the scoring model attributed to a specification by Bernd — is the
direct antecedent of `dedup/weights.json` and is recorded as decision D-29
(`09_DECISIONS.md:1120-1155`).

### 4.2 Who performs it

Three actor classes are evidenced, and no individual is named as a data steward anywhere:

| Actor | Role | Evidence |
|---|---|---|
| Data steward(s) | Review issues in the DS issues view, resolve per-cell in the Excel add-in / DS Studio, confirm cluster proposals and apply a leading code | `CONTEXT-EXTERNAL.md:412-436` steps 9, 11, 12 (all "human in loop: yes"); `Datashaper-Tutorial-Part2.txt:653-668` |
| A responsible person per rule | Assigned against a validation rule | `Datashaper-Tutorial-Part2.txt:809` |
| The SAP team | Granted change access to the DS reference views | `Datashaper-Tutorial-Part2.txt:1406` |
| Bernd Schnurrer | Author of the golden-record scoring specification; source of the ZFI exclusion instruction | `Datashaper-Tutorial-Part2.txt:1857-1859`; `CONTEXT-EXTERNAL.md:434-435` |

Three of the twelve production workflow steps remain human-in-the-loop after this pipeline
exists — steps 9, 11 and 12 — plus step 1 and step 2, the preprocessing and import
(`CONTEXT-EXTERNAL.md:416-429`). The pipeline therefore replaces the *bulk correction* work, not
the review work.

### 4.3 Throughput and effort — not evidenced

**⚠ RATIONALE NOT IN REPO — author to supply.** No throughput figure, effort figure, headcount,
elapsed duration, or per-record correction time appears in the transcripts, the README, any
commit message, `CONTEXT-EXTERNAL.md`, or any workbook. A sweep of all three transcripts for
`hours`, `per day`, `per week`, `FTE`, `full-time` and `how long` returns only scheduling
discussion of the onboarding calls themselves (`Datashaper-Tutorial-Part1.txt:2036-2081`).

Per the instruction, **no per-record correction time is estimated here.** The specific questions
the author must answer are:

1. How many records were corrected manually before this pipeline existed, over what period?
2. How many people performed that work, and at what allocation?
3. Was any per-record or per-batch duration ever recorded — in a ticket, a project plan, or a
   status report — that could be cited?
4. What was the throughput of the DS Excel add-in route specifically, since that is the process
   the pipeline displaces?

Without at least one of these the thesis can state that the prior process was manual,
per-cell, and assigned to named responsible people, but cannot quantify what the pipeline saves.

---

## 5 · Duplicate prevalence

### 5.1 What was run, and what it is not

Two things must not be conflated, and the repository provides only the first.

| | Available | Reported below |
|---|---|---|
| **Exact-signature collapse (STEP A)** | Yes — deterministic, no LLM, `dedup/signatures.py` | **Yes**, measured |
| **LLM-adjudicated clusters (STEP B)** | No — requires an Azure OpenAI call per block; no adjudicated run output is committed anywhere in the repository | **No** |

The run reported in §3.4 above under `# 5` is **STEP A only**. It collapses rows whose
normalised `(name1, name2)` pair is identical within an address block
(`dedup/signatures.py:111-147`). It makes no judgement about whether two *different* names refer
to the same entity — that is precisely the question `/api/dedup/cluster-block` sends to the
model, and it was not run.

**A second qualification on the blocking.** The source workbook carries no `Block ID` column, so
every block id below is the service's *fallback* derivation — a hash over the normalised
`(country, postal_code, street, house_no)` tuple (`dedup/signatures.py:45-56`). In production
the block id is precomputed by the DATAshaper address gate and read from the request, never
derived (`CONTEXT-EXTERNAL.md:309-310`; `08_GAPS.md:1116-1128`, G-73). The fallback is *stricter*
than the vendor's gate, which fuzzy-matches the street at 80% and keys on the postal-code prefix
rather than the full code (`Datashaper-Tutorial-Part2.txt:1589-1600`). The block counts below are
therefore a lower bound on what the production gate would group.

### 5.2 Measured — `PresentationTestData.xlsx`, 500 rows

| Quantity | Value |
|---|---|
| Distinct derived address blocks | 384 |
| Rows alone in their block | 356 (71.2%) |
| **Rows in a block of size > 1** | **144 (28.8%)** |
| Largest block | 20 rows |
| Distinct signatures across all blocks | 455 |
| Signatures covering more than one row | 31 |
| **Rows inside an exact-duplicate signature** | **76 (15.2%)** |
| Blocks holding more than one distinct signature | 15 |

Signature-size distribution: 424 signatures cover one row, 20 cover two, 8 cover three, 3 cover
four.

### 5.3 Reading these figures

**15.2% of rows are exact duplicates of another row, deterministically.** Seventy-six rows fall
into one of 31 signatures that each cover two or more rows — same normalised organisation name,
same normalised department, same derived address block. No model judgement is involved; these
collapse by string equality after case-folding, accent-folding and punctuation stripping
(`dedup/signatures.py:29-42`). This is the floor of the duplication problem.

**A further 15 blocks hold more than one distinct signature.** These are exactly the blocks the
adjudicator would be asked to decide: several organisations at one address, which may be one
entity written several ways or several genuinely distinct units sharing a building. Whether they
merge is **not** determined by this run, and the repository contains no run that determines it.

**The workbook's own expectation, for comparison only.** `Dedup_Scoring_Oracle` declares 12
clusters of size > 1 (sizes 5, 4, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2 — 31 rows) plus 10 singleton
guardrails that "must NOT merge", and `Oracle_Summary` declares a routing split of 332 unique /
135 manual_review / 33 cluster. Given §3.5's finding that the same workbook's issue oracle
agrees with the implemented detector on only 12 of 37 codes, these figures are recorded as the
dataset author's intent and **must not be cited as a measurement**.

### 5.4 What is not measured

⚠ MEASUREMENT REQUIRED — the adjudicated cluster count, the adjudicated cluster-size
distribution, and the proportion of records the model places in a cluster of size > 1. Producing
them requires one `POST /api/dedup/cluster-block` run over the 500 rows against a live Azure
OpenAI deployment, which incurs external spend and is not reproducible offline. The exact call:

```
POST {API}/api/dedup/cluster-block
{"rows": [ {"row_id": …, "name1": …, "name2": …, "street": …,
            "house_no": …, "postal_code": …, "city": …, "country": …}, … ]}
```

then count `routing == "cluster"` and group by `cluster_id` in the response
(`dedup/models.py:63-80`).

⚠ MEASUREMENT REQUIRED — duplicate prevalence in the production `Validation` table, which is the
figure the thesis actually wants:
`SELECT COUNT(*) FROM test_77.Validation` and
`SELECT COUNT(DISTINCT [Block ID]) FROM test_77.Validation`
(`02_ARCHITECTURE.md:491-493`; `06b_CROSSCUTTING.md:754-757`).

---

## 6 · Coupling evidence — enrichment supplies what deduplication consumes

### 6.1 The claimed mechanism

The claim is that Phase 1 attaches a registry identifier that Phase 2 then uses as a same-entity
hint. Both ends are evidenced in code:

- **Phase 1 produces it.** `ror_id` was re-exposed in the response and `lei_id` added, explicitly
  *"so the dedup phase can converge on a shared identifier"* (`README.md:2015`; decision D-34,
  `09_DECISIONS.md:1295-1311`). Both appear as `ROR ID` / `LEI ID` output columns
  (`api/output_columns.py:87-88`).
- **Phase 2 consumes it.** `DedupRow` accepts `ror_id` — "ROR id from Phase 1, if resolved
  (institution hint)" — and `lei_id` — "GLEIF LEI from Phase 1, if resolved (company legal-entity
  hint)" (`dedup/models.py:42-43`). A `Signature` adopts the first non-empty `ror_id` / `lei_id`
  any of its rows carries (`dedup/signatures.py:138-142`), and the residue nomination rule ranks
  converging ROR/LEI first among its candidate criteria (`dedup/candidates.py:1-16`; decision
  D-26, `09_DECISIONS.md:984-1024`).
- **The production wiring exists.** The ADF deduplication Lookup projects `[ROR ID]` → `ror_id`
  and `[LEI ID]` → `lei_id` from the `Validation` table (`CONTEXT-EXTERNAL.md:226`;
  `05_DATA_MODEL.md:514-515`).

Both documents are explicit that the identifiers are hints, never deterministic cluster keys
(`README.md:1140-1147`; `dedup/signatures.py:70-77`).

### 6.2 Measured — how many records gained an identifier

The pre-enrichment workbook carries **no** `ROR ID` or `LEI ID` column at all (run output §6:
both report `False`). Every populated cell in the enriched workbook is therefore an identifier
the pipeline supplied, and the "did not have one before" count is the full 500.

| Quantity | Value |
|---|---|
| Rows in the enriched workbook | 500 |
| Rows that gained a **ROR ID** | **194 (38.8%)** |
| Rows that gained an **LEI ID** | **40 (8.0%)** |
| **Rows that gained either identifier** | **219 (43.8%)** |
| Rows carrying both | 15 (3.0%) |
| Distinct ROR ID values | 129 |
| Distinct LEI ID values | 37 |

### 6.3 Measured — how much of that is usable as a merge hint

An identifier is only a deduplication signal when more than one row carries the same value.

| Quantity | Value |
|---|---|
| Identifier values carried by more than one row | 21 |
| **Rows carrying a shared identifier** | **89 (17.8%)** |
| Largest single convergence | 37 rows on `https://ror.org/02y3ad647` |

Eighteen of the 21 shared values are ROR ids and three are LEIs. The distribution is long-tailed:
one value covers 37 rows, three cover 3–5 rows each, and thirteen cover exactly two.

**The coupling is real and measurable at this scale: enrichment gave 43.8% of records a registry
identifier they did not have, and 17.8% of records a registry identifier that at least one other
record also carries.** For comparison, §5.2 measured that only 15.2% of rows are exact-signature
duplicates — so the registry hint reaches a slightly larger population than deterministic
collapse does, which is the argument for the coupling.

### 6.4 What is not measured

⚠ MEASUREMENT REQUIRED — **in how many adjudicated clusters a shared registry identifier was
among the evidence.** This cannot be answered from the enriched workbook, because no adjudicated
run exists in the repository (§5.1) and because the adjudicator's per-signature `reasoning`
string is the only place the evidence used is recorded (`dedup/models.py:79`;
`dedup/adjudicator.py:811-824`).

The query that would answer it, after one `POST /api/dedup/cluster-block` run over the 500 rows
with `ror_id` / `lei_id` populated from the enriched workbook:

```python
# over the DedupResponse rows
clusters = defaultdict(list)
for r in response["rows"]:
    if r["cluster_id"]:
        clusters[r["cluster_id"]].append(r)
# a cluster where a shared registry id was available as evidence:
shared = sum(
    1 for members in clusters.values()
    if len({row_ror_id[m["row_id"]] for m in members if row_ror_id[m["row_id"]]}) == 1
       and sum(1 for m in members if row_ror_id[m["row_id"]]) > 1
)
```

and, against production, the same figure over `test_77.Validation` after a merge-back:

```sql
SELECT COUNT(*) FROM (
  SELECT Cluster_ID
  FROM test_77.Validation
  WHERE Cluster_ID IS NOT NULL AND [ROR ID] IS NOT NULL
  GROUP BY Cluster_ID
  HAVING COUNT(DISTINCT [ROR ID]) = 1 AND COUNT(*) > 1
) t;
```

⚠ Note a structural limit on any such measurement: the prompt does not require the model to state
which evidence it used, and `03_ALGORITHMS.md:5710` records that no prompt clause restricts the
model to the supplied evidence at all. A shared ROR id being *present* in a merged cluster is
therefore evidence of availability, not of use.

---

## 7 · Exemplar shortlist

Eight records selected from `03b_EXEMPLARS.md` to span the four populated dimensions of §2, with
preference for records exhibiting more than one dimension at once. Anonymisation follows
`03b_EXEMPLARS.md:35-53`: customer identifiers are the stable placeholders `REC-01` … `REC-16`,
person names are `Person-A` … `Person-C`, public institution names are retained verbatim, and
the placeholder → original map lives only in the git-ignored
`docs/thesis/exemplar_id_map.local.md`.

Codes are the detector's actual output, obtained through the `/issues` construction path
(`03b_EXEMPLARS.md:55-61`).

### 7.1 REC-10 — four dimensions on one record

Source: `PresentationTestData.xlsx`, sheet row 50 (`03b_EXEMPLARS.md:327-349`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `Gulf Coast Labs` | `Gulf Coast Labs` |
| Street 1 | `PO BOX 4500` | `PO BOX 4500` |
| Street 2 | `PO Box 6789` | `PO Box 6789` |
| PO Box | `4500` | *(empty)* |
| Search Term 1 | *(empty)* | `GCL` |

Raw: **`G1-ADDR-004`, `G2-VAL-007`, `G3-ADDR-005`, `G5-NAME-001`** — placement, completeness,
uniqueness and representational consistency simultaneously. Post-pipeline: `G1-ADDR-004`,
`G3-ADDR-005`, `G5-NAME-001`. The PO-box count falls from three to two because the dedicated
column is cleared while both street slots keep their text, so the code persists.

### 7.2 REC-01 — placement resolved, uniqueness introduced

Source: sheet row 76 (`03b_EXEMPLARS.md:104-135`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `Photon Labs 4200 Research Blvd Suite 210` | `Photon Labs` |
| Street 1 | `RESEARCH BLVD` | `RESEARCH Blvd` |
| Street 2 | *(empty)* | `4200 Research Blvd` |
| House Number | `4200` | `4200` |
| Search Term 1 | *(empty)* | `photon` |
| Domain | *(not a column)* | `https://www.photon.com` |

Raw: **`G1-CROSS-001`, `G2-VAL-007`, `G5-NAME-001`**. Post-pipeline: **`G3-ADDR-012`,
`G5-NAME-001`**. The address text is lifted out of Name 1 — placement resolved — but writing the
fragment to Street 2 while Street 1 holds the same street with the number in its dedicated column
makes `_street_signature` fold the two into one signature (`enrichment/issue_detection.py:403-417`),
so the pipeline **introduces** a uniqueness issue. This is the only repository record exercising
`G3-ADDR-012`, and it is the clearest single illustration that remediation is not monotone.

### 7.3 REC-02 — the department hierarchy inside a street

Source: sheet row 112 (`03b_EXEMPLARS.md:137-164`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `FDA - FOOD & DRUG ADMINISTRATION` | `FDA - Food & Drug Administration` |
| Name 2 | *(empty)* | `Bioanalytical Methods Branch` |
| Street 1 | `Bioanalytical Methods Branch \| Division of Bioanalytical Chemistry \| Office of Regulatory Science (HFS-717) \| Center for Food Safety and Applied Nutrition \| U.S. Food and Drug Administration \| 5100 Paint Branch Parkway` | same six segments, terminal type abbreviated |
| Search Term 1 / 2 | *(empty)* | `fda` / `Bioanalytical Methods` |

Raw: **`G1-ADDR-001`, `G1-ADDR-011`, `G2-VAL-003`, `G2-VAL-007`**. Post-pipeline:
`G1-ADDR-001`, `G1-ADDR-011`, `G2-VAL-003`. The leading segment is promoted into the empty
Name 2 and both search terms derive, but the six-segment street survives and House Number stays
blank. ⚠ MEASUREMENT REQUIRED — the workbook records values, not execution; why the scope-table
reduction did not apply requires re-running `/enrich` on this row
(`03b_EXEMPLARS.md:159-164`).

### 7.4 REC-06 — the completeness case that motivates retrieval

Source: `tests/fixtures/research_missing_name2_with_contact.json`, `records[0]`
(`03b_EXEMPLARS.md:240-271`).

| Field | Raw |
|---|---|
| Name 1 | `Massachusetts Institute of Technology` |
| Name 2 | *(null)* |
| Contact | `Person-A` |
| Street | `77 Massachusetts Ave` |
| City / State / ZIP / Country | `Cambridge` / `MA` / `02139` / `US` |

Codes: **`G1-ADDR-001`, `G2-NAME-012`, `G2-CONTACT-009`**. The exemplar pair for the two
department-completeness rules, and the record type Tier 2A exists to serve: the department is
absent but recoverable from the named contact's page. It also demonstrates `G2-CONTACT-008`'s
unreachability concretely — that code needs the same gate with the contact *absent*, but
`G2-NAME-012` has already fired under the identical condition (`03b_EXEMPLARS.md:254-260`).

### 7.5 REC-05 — a granular unit with no parent

Source: sheet row 69 (`03b_EXEMPLARS.md:219-238`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `University of Florida` | `University of Florida` |
| Name 2 | `Smith Lab` | `Smith Lab` |
| Search Term 1 / 2 | *(empty)* | `UF` / `Smith` |

Raw: **`G2-VAL-007`, `G2-NAME-009`, `G5-NAME-002`**. Post-pipeline: `G2-NAME-009`,
`G5-NAME-002`. A laboratory sits in the department slot with no parent department anywhere in
the name block. The lab resolver (UC 13) is the stage that would promote a parent into Name 2
and move the lab to Name 3; it did not fire, so the completeness code persists.

### 7.6 REC-09 — intra-record duplication

Source: sheet row 87 (`03b_EXEMPLARS.md:303-325`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `Tropical Pharma Inc` | `Tropical Pharma Inc` |
| Name 2 | `Tropical Pharma Inc` | `Tropical Pharma Inc` |
| Search Term 1 / 2 | *(empty)* | `tropical-pharma` / `Tropical Pharma` |

Raw: **`G2-VAL-007`, `G3-NAME-005`**. Post-pipeline: `G3-NAME-005`. The organisation name is
copied verbatim into the department slot. ⚠ The duplicate is **not** cleared, although UC 12
specifies exactly this case. Whether the workbook predates the implementation or the rule failed
on this input requires re-running `/enrich` — ⚠ MEASUREMENT REQUIRED
(`03b_EXEMPLARS.md:318-325`; `08_GAPS.md:272-283`, G-19).

### 7.7 REC-11 — length overflow, a steward decision

Source: sheet row 244 (`03b_EXEMPLARS.md:355-377`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `The Regents of the University of California San Francisco` | *(unchanged)* |
| Name 2 | `Department of Microbiology and Immunology` | `Department of Microbiology & Immunology` |
| Name 3 | `Division of Experimental Virology and Genomics` | *(unchanged)* |
| Name 4 | `Gladstone Institute Virology Research Lab` | *(unchanged)* |
| Search Term 1 / 2 | *(empty)* | `UCSF` / `Microbiology Immunology` |

Raw: **`G2-VAL-007`, `G4-NAME-015`, `G5-NAME-002`**. Post-pipeline: `G4-NAME-015`,
`G5-NAME-002`. The four name fields total 185 characters against the SAP limit of 140
(`enrichment/issue_detection.py:121`). The pipeline saves two characters (`and` → `&`), which
cannot bring it under — the overflow is inherent to the record's content and its resolution is a
steward decision, not an enrichment one. Note the derived Search Term 1 `UCSF`, an acronym
present in none of the four name fields.

### 7.8 REC-13 — conformance, not accuracy

Source: sheet row 40 (`03b_EXEMPLARS.md:400-422`).

| Field | Raw | Post-pipeline |
|---|---|---|
| Name 1 | `TransGlobal Pharma` | `TransGlobal Pharma` |
| Postal Code | `3360` | `3360` |
| City / Region | `SEATTLE` / `WA` | *(unchanged)* |
| Country/Region Key | `USA` | `USA` |
| Search Term 1 | *(empty)* | `transglobalus` |

Raw: **`G2-VAL-007`, `G4-ADDR-026`, `G4-ADDR-027`**. Post-pipeline: `G4-ADDR-026`,
`G4-ADDR-027`. The four-digit postal code fails the registered `US` format and `USA` is not
canonical ISO-2. Both persist: neither is rewritten. This is the exemplar for §2's accuracy
argument — the rules establish that these values do not *conform*, and no rule anywhere
establishes whether `3360` is or is not this organisation's postal code. It also shows the
rule's coverage limit: only `US` and `CA` formats are registered.

### 7.9 Dimension coverage of the shortlist

| Record | Cross-field placement | Completeness | Uniqueness | Repr. consistency | Codes |
|---|:-:|:-:|:-:|:-:|---:|
| REC-10 | ● | ● | ● | ● | 4 |
| REC-01 | ● | ● | ● (introduced) | ● | 3 raw + 2 post |
| REC-02 | ● | ● | | | 4 |
| REC-06 | ● | ● | | | 3 |
| REC-05 | | ● | | ● | 3 |
| REC-09 | | ● | ● | | 2 |
| REC-11 | | ● | | ● | 3 |
| REC-13 | | ● | | ● | 3 |

Two further exemplars were considered and not selected, and are worth a sentence in the chapter
if space allows: **REC-12** (sheet row 70) is the only record in `03b_EXEMPLARS.md` that reaches
a **fully clean** post-pipeline state, clearing both `G2-VAL-007` and `G4-ADDR-008`
(`03b_EXEMPLARS.md:379-398`); **REC-03** (sheet row 124) is the care-of case, where the party is
extracted into a dedicated `Care Of` column but `G1-CROSS-003` persists because the extraction is
non-destructive and the `c/o` text remains in Street 1 (`03b_EXEMPLARS.md:166-189`).

No exemplar illustrates the accuracy dimension, for the reason given in §2.2: no code measures
it.

---

## 8 · What is measured and what is not

| # | Item | Status |
|---|---|---|
| 1 | SAP customer master structure | **Measured / cited.** Field list, types, mandatoriness and coded-vs-free-text from `api/models.py`; populated rates measured over 500 rows; six semantics-vs-practice divergences evidenced, one of them (`Terms of Payment` not binding) newly found here |
| 2 | Taxonomy by quality dimension | **Established from source.** 37 declared, 35 with emission sites, ≤34 observable, 31 observed. Counted from `ISSUE_CATALOGUE` and the emission sites, not from the stale docstring. Accuracy is reported as a **zero-code dimension**, with the design constraint that makes it so |
| 3 | Frequency evidence | **Measured.** 500 records; 500 (100%) with ≥1 issue, 442 (88.4%) excluding the universal `G2-VAL-007`; per-record distribution, ranked per-code frequency, and 3.10 mean distinct columns implicated. Locator fidelity self-check passed 500/500. Dataset provenance itself remains ⚠ UNVERIFIED |
| 3b | The dataset's own oracle | **Measured, as a comparison.** Agrees with the implemented detector on 12 of 37 codes and on none of the four headline figures |
| 4 | Manual effort baseline — process and actors | **Evidenced qualitatively** from the DATAshaper transcripts and the workflow table |
| 4b | Manual effort baseline — throughput or effort figure | **⚠ RATIONALE NOT IN REPO — author to supply.** Nothing in any artefact. Four specific questions listed at §4.3; no per-record time estimated |
| 5 | Duplicate prevalence — exact-signature collapse | **Measured.** 384 derived blocks; 144 rows (28.8%) in a multi-row block; 76 rows (15.2%) inside an exact-duplicate signature; 15 blocks would require adjudication |
| 5b | Duplicate prevalence — adjudicated clusters | **⚠ MEASUREMENT REQUIRED.** No adjudicated run exists in the repository; the call that would produce it is given at §5.4. The `Dedup_Scoring_Oracle` sheet is intent, not measurement |
| 6 | Coupling — identifiers gained | **Measured.** 219 of 500 rows (43.8%) gained a ROR or LEI identifier the input file had no column for; 89 rows (17.8%) carry an identifier at least one other row also carries |
| 6b | Coupling — clusters where a shared identifier was among the evidence | **⚠ MEASUREMENT REQUIRED.** Requires an adjudicated run; the Python and SQL that would answer it are given at §6.4, with the caveat that presence ≠ use |
| 7 | Exemplar shortlist | **Selected and cited.** Eight records spanning the four populated dimensions; anonymisation consistent with `03b_EXEMPLARS.md` |

**Reproducing everything in §3, §5 and §6:**

```
cd c:\Users\apoorva.ajay\Downloads\ApoorvaThesis\ApoorvaThesis\enrichment_api
.venv\Scripts\python.exe -m scripts.ch02_measure
```

The script is `scripts/ch02_measure.py`, added by this packet. It is read-only, makes no external
call, and exits non-zero if its field-attribution locators diverge from `detect_issues` on any
row.

**Stop.**
