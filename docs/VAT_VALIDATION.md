# VAT ID validation (VIES)

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

## 1. What VIES provides

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

### Limits

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

### The DE / ES name gap

For a valid German or Spanish number VIES returns `name: "---"` and `address: "---"`. In
VIES's words: *"if the name and/or address are replaced by '---', this means that the Member
State that issued the VAT number does not allow to disclose this information."* That is a
**correct, complete answer**, not a failed lookup:

- `VAT_VIES_Status = VALID`, `VAT_VIES_Name` / `VAT_VIES_Address` blank,
  `VAT_Name_Disclosed = FALSE`, `VAT_Name_Match = NOT_AVAILABLE` (`NAME_MATCH:NOT_DISCLOSED`).
- The `"---"` placeholder is never written out as a name.

On the fixture, all 17 valid DE rows and both ES rows came back this way. IE, FR, LU, SE and
AT returned names.

---

## 2. Input format and the customer-level rule

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

### Fan-out

The extract has one row per company code and sales organisation, but the VAT ID belongs to the
customer.

1. Every row is normalised and format-checked.
2. VIES is asked **once per unique normalised VAT across the whole batch**.
3. That answer is written to every row carrying the number. For a consistent customer, that
   is every row of the customer.

On the fixture, 30 rows and 26 FORMAT_OK rows produced 24 lookups. Robert Bosch's two rows
share one number, and `IE6388047V` appears on two customers.

### Conflict

If one Customer's rows do not all carry the same normalised VAT, every row of that customer is
flagged `CUSTOMER_VAT_CONFLICT`, and **each row keeps the result for its own value. Nothing is
picked.** A blank on one row next to a value on another counts as a conflict. The same number
spelled differently (`DE136…` and `de 136 695 976`) does not.

Customer values are grouped exactly as read: whitespace-trimmed, with no zero-stripping or
padding, so `0099000006` and `99000006` are two customers.

---

## 3. Result columns and states

Ten columns are appended after the input columns, in this order: `VAT_Normalized`,
`VAT_Format_Status`, `VAT_VIES_Status`, `VAT_VIES_Name`, `VAT_VIES_Address`,
`VAT_Name_Disclosed`, `VAT_Name_Match`, `VAT_Request_Date`, `VAT_Consultation_No`,
`VAT_Flags`.

### `VAT_Format_Status`: offline (python-stdnum syntax + checksum)

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

### `VAT_VIES_Status`

| State | Meaning |
|---|---|
| `VALID` | VIES answered `valid: true`. |
| `INVALID` | VIES answered `valid: false`. This is the **only** route to INVALID. |
| `SERVICE_UNAVAILABLE` | VIES could not answer: a transient code from §1, a network error or timeout, HTTP 5xx/429, or (`CACHE_FROZEN`) no recording. **Never reported as INVALID.** |
| `ERROR` | Not retryable: `INVALID_INPUT`, `INVALID_REQUESTER_INFO`, `VAT_BLOCKED`, `IP_BLOCKED`, an unknown code, a malformed body, or another 4xx. |
| `NOT_CHECKED` | The row was not FORMAT_OK and was never sent. |

The VIES error code, if any, goes to flags as `VIES:<code>`. A frozen-cache miss is
`VIES:FROZEN_CACHE_MISS`.

### `VAT_Name_Match`: deterministic, no LLM

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

### `VAT_Flags`

The flags are joined with `"; "`, the same separator Flag Codes uses elsewhere in the pipeline.
They are column values, **not** issue codes:

`PREFIX_FROM_COUNTRY_KEY`, `PREFIX_COUNTRY_MISMATCH`, `NON_EU_PREFIX`, `NO_PREFIX_NO_COUNTRY`,
`FORMAT_ERROR:<reason>`, `VIES:<code>`, `NAME_MATCH:<rule>`, `CUSTOMER_VAT_CONFLICT`.

### Caching and frozen mode

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

### Configuration (`config.py`, `.env.example`)

| Setting | Default |
|---|---|
| `VIES_API_BASE` | `https://ec.europa.eu/taxation_customs/vies/rest-api` |
| `VIES_TIMEOUT_SECONDS` | 15 |
| `VIES_MAX_ATTEMPTS` | 3 |
| `VIES_BACKOFF_SECONDS` | 1.0 |
| `VIES_MAX_CONCURRENCY` | 1 |
| `VIES_MIN_INTERVAL_MS` | 500 (0 disables pacing; cache replays are never paced) |
| `VIES_REQUESTER_VAT` | empty |

`VIES_REQUESTER_VAT` is optional and never hardcoded. When it is set, VIES issues a
consultation number. A malformed value fails loudly (HTTP 500 from the endpoint) instead of
failing every row as `INVALID_REQUESTER_INFO`.

### Usage

```
POST /api/validate/vat   {"rows": [{"Customer": "...", "Country/Region Key": "DE", "VAT Registration No.": "...", ...}]}
→ {"rows": [... input columns + VAT_* ...], "columns": [...], "summary": {...}}

POST /api/validate/vat/file   multipart form field "file" = export.xlsx (.xlsx / .xlsm)
→ export_vat.xlsx   headers: X-VAT-Rows, X-VAT-Service-Unavailable (unique VAT IDs VIES did not answer)
  400: wrong extension / empty / unreadable file    422: no header row, no data rows, or no VAT column

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

### Re-running

SERVICE_UNAVAILABLE and ERROR answers are never cached, so re-running the runner with the same
input and the same cache directory asks VIES again for **only** the numbers listed in that
table. Every VALID and INVALID answer is replayed from the cache, with no call and no pacing
delay.

On 2026-10-18 the first run had 24 lookups (26 HTTP calls) and 1 SERVICE_UNAVAILABLE
(`FR45775662257`). The re-run made 1 HTTP call, replayed the other 23 answers from cache, and
ended with 0 SERVICE_UNAVAILABLE.

---

## 4. Proposed issue codes (proposal only)

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

## 5. Follow-ups (not implemented)

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
