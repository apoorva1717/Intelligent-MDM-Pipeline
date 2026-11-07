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
- **Scraping Impressum pages (missing-VAT lookup, Mode A).** The lookup fetches company web
  pages with the existing `search.page_fetcher.PageFetcher`. That fetcher identifies itself as
  `BrukerMDM-Enrichment/1.0` and **does not consult robots.txt**. This is the existing
  behaviour and was kept unchanged. Whether automated retrieval of Impressum pages is
  acceptable belongs in the same legal/compliance review as the VIES terms above.

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

### VIES ignores trader fields for Germany (confirmed live)

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
| `VAT_GROUNDED_FALLBACK` | false |
| `VAT_LOG_COLUMNS` | true (research log + `VAT_Found_Entity_Status`, see 3b) |

`VIES_REQUESTER_VAT` is optional and never hardcoded. When it is set, VIES issues a
consultation number. A malformed value fails loudly (HTTP 500 from the endpoint) instead of
failing every row as `INVALID_REQUESTER_INFO`.

`VAT_GROUNDED_FALLBACK` controls the grounded lookup (SerpAPI + page read). When it is false
(the default) that lookup never runs, and every missing-VAT lookup is `llm_web`. Asking for
`lookup_missing=grounded` or `with_grounded=true` returns 400. When it is true, both are allowed,
and `llm_web` falls back to the grounded lookup for a customer whose web search could not run
(`LOOKUP_INCOMPLETE` with `LLM_UNAVAILABLE`). Such a row has `VAT_Lookup_Mode` GROUNDED and the
flag `GROUNDED_FALLBACK`. A `NOT_FOUND` from `llm_web` is final.

### Usage

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

### Re-running

SERVICE_UNAVAILABLE and ERROR answers are never cached, so re-running the runner with the same
input and the same cache directory asks VIES again for **only** the numbers listed in that
table. Every VALID and INVALID answer is replayed from the cache, with no call and no pacing
delay.

On 2026-10-18 the first run had 24 lookups (26 HTTP calls) and 1 SERVICE_UNAVAILABLE
(`FR45775662257`). The re-run made 1 HTTP call, replayed the other 23 answers from cache, and
ended with 0 SERVICE_UNAVAILABLE.

### Missing-VAT lookup: search ranking varies day to day

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

## 3a. Missing-VAT lookup experiment (fixture, 2026-10-20)

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

### Results per customer

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

### Totals (15 customers)

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

### What the results show

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

### Cost

| | Mode A | Mode B | Audit |
|---|---|---|---|
| SerpAPI queries (network) | 32 (32) | – | – |
| Page attempts / fetched / recorded | 53 / 53 / 34 | – | – |
| LLM calls (network) | 13 reads | 16 asks | – |
| VIES checks | 7 | 7 | 2 |
| Cache write failures | 0 | 0 | 0 |
| Wall time | 371 s | 38 s | – |

### Reproducibility (frozen re-run of the same command)

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

## 3b. Alignment with the design paper (2026-10-27)

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

### Decision rules, issue codes and source classes (2026-11-02)

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

### Hybrid search (2026-11-04, `VAT_WEB_QUERY_MODE=hybrid`, the default)

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

### Step AF: the hybrid baseline (2026-11-05)

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

### Step AF-2: closing the baseline (2026-11-05)

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

### Research log columns and entity status (2026-11-06, `vat/lookup_log.py`)

With `VAT_LOG_COLUMNS=true` (the default), the endpoint (`/api/validate/vat`,
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

### Cost accounting (2026-11-05, `vat/costs.py`)

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
