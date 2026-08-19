Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 3b — Worked exemplars per issue-catalogue group

This document presents real records drawn from repository data for each of the five
issue-catalogue groups defined at `enrichment/issue_detection.py:75-118`. For each record it
gives the source citation, the raw field values relevant to the issue, the issue codes the
deterministic detector actually raised, and the post-pipeline values where an enriched
counterpart exists. It accompanies Part H of `docs/thesis/03_ALGORITHMS.md`, which specifies the
detection rules themselves.

Every record below is real repository data. No record was constructed, completed, or adjusted
for illustration. Where a group or a code has no covering record, that is stated rather than
filled.

## 1 · Data sources

| Source | Rows | Role |
|---|---:|---|
| `PresentationTestData.xlsx` | 500 data rows | Pre-enrichment records; the "raw" column values below |
| `PresentationTestData_enriched_checked_v1.xlsx` | 500 data rows | Side-by-side original/enriched sheet; the "post-pipeline" values below |
| `PresentationTestData_subset.xlsx` | — | Subset of the above; used only in the coverage census (§4) |
| `tests/fixtures/research_missing_name2_with_contact.json` | 1 record | JSON fixture, `records[0]` |
| `tests/fixtures/mixed_batch_10_records.json` | 10 records | JSON fixture, `records[1]` used here |

⚠ UNVERIFIED — whether any row of `PresentationTestData.xlsx` derives from a production SAP
extract. The workbook is committed at the repository root and is described in
`docs/thesis/00_INVENTORY.md:25-27` as spreadsheet test data. Many rows share identical filler
values (`MAIN ST`, house number `100`, tax jurisdiction `1200000000`), which is consistent with
a demonstration dataset rather than a production extract; other rows (the FDA, Pellissippi
State, NIST and Dentsply records below) carry realistic addresses. The provenance of the file is
not recorded anywhere in the repository. This matters for external validity and is carried into
`08_GAPS.md`.

## 2 · Anonymisation scheme

Customer identifiers are replaced by stable placeholders `REC-01` … `REC-16`, and person names
in contact fields by `Person-A` … `Person-C`. The same original always maps to the same
placeholder across this document. Public organisation names (the FDA, the University of
Florida, NIST, UCSF, Pellissippi State Community College, Dentsply, UCLA, MIT) are retained
verbatim: they identify public institutions, not individuals, and the enrichment behaviour under
discussion is only interpretable with the real name present. Role-based e-mail addresses
(`orders@…`, `contact@…`) are retained for the same reason — they identify a function, not a
person.

The placeholder → original mapping is **not** in this file. It is written to
`docs/thesis/exemplar_id_map.local.md`, which is git-ignored (`.gitignore:24-25`) and must not
be committed or published.

⚠ Note on the person names in this dataset: the contact values that map to `Person-A` …
`Person-C` are themselves evidently placeholder names already present in the source data. They
are anonymised here regardless, so that the scheme is uniform and no judgement about which
names are real is embedded in the document.

## 3 · How the issue codes were obtained

The codes below are the detector's actual output, not a prediction. `enrichment.issue_detection.detect_issues`
was executed over each source through the same record-construction path the `/issues` endpoint
uses — `_parse_xlsx` → `_rows_to_records` → `_present_fields` → `detect_issues(record, present)`
(`api/routes.py:603-606`) — and the returned lists are reproduced verbatim. Codes appear in
catalogue order, as `detect_issues` guarantees (`enrichment/issue_detection.py:510`).

**Reading the enriched workbook.** `PresentationTestData_enriched_checked_v1.xlsx` is a
side-by-side comparison sheet: it repeats six header names, carrying the original value in the
earlier column and the enriched value in the later one — `Name 1` at columns 10 and 12, `Name 2`
at 11 and 13, `Street 1` at 21 and 24, `House Number` at 22 and 25, `Street 2` at 23 and 26.
`_parse_xlsx` keys each row dict by header name and assigns only non-empty values
(`api/routes.py:207-213`), so a duplicated header resolves to its **last non-empty** occurrence.
The post-pipeline values below are therefore the enriched columns — but by dict overwrite rather
than by design, and with one consequence worth stating: where an enriched column is empty and
its original twin is not, the original value survives into the parsed record. Any evaluation
built on this workbook must account for that. This is a property of the workbook layout and the
parser, not of the enrichment pipeline.

---

## 4 · Coverage census

Across all repository data — both workbooks, the subset, and every JSON fixture — the detector
raises **32 of the 37 declared codes**. The five it never raises:

| Code | Name | Why not exercised |
|---|---|---|
| `G1-ADDR-009` | Unclassified Residual in Address | Declared but never emitted by the deterministic detector (`enrichment/issue_detection.py:88,317`) — LLM-only |
| `G4-ADDR-025` | Sub-location Overflow Beyond Street 5 | Declared but never emitted (`enrichment/issue_detection.py:112,465`) — LLM-only |
| `G2-CONTACT-008` | No Contact and No Department | Has an emission site but it is unreachable (`enrichment/issue_detection.py:364-367`; proof in `03_ALGORITHMS.md` Part H §1.3) |
| `G1-NAME-001` | Name Overflow Across Fields | ⚠ NO FIXTURE COVERAGE — reachable, but no repository record satisfies it |
| `G3-ADDR-013` | Two Distinct Street Addresses on Record | ⚠ NO FIXTURE COVERAGE — reachable, but no repository record satisfies it |

The first three cannot be exercised by any input. The last two are genuine data gaps: a record
would be needed with, for `G1-NAME-001`, a Name 1 carrying no legal-entity suffix followed by a
Name 2 opening with a connector or a lowercase word (`enrichment/issue_detection.py:296-305`);
and for `G3-ADDR-013`, two street slots holding two *different* values that both satisfy
`_looks_like_street` (`enrichment/issue_detection.py:419-424`).

One code is exercised **only** by the enriched workbook and by no pre-enrichment record:
`G3-ADDR-012` (Duplicate Street Across Fields). It is introduced by the pipeline, not present in
the input — see REC-01 in §5.1, where it is the exemplar case.

---

## 5 · Group G1 — Data in the wrong field

### 5.1 REC-01 — address text inside Name 1, and an issue introduced by enrichment

Source: `PresentationTestData.xlsx`, sheet row 76 (data row 75); enriched counterpart at the
same sheet row of `PresentationTestData_enriched_checked_v1.xlsx`.

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `Photon Labs 4200 Research Blvd Suite 210` | `Photon Labs` |
| Name 2 | `Accounts Payable` | `Accounts Payable` |
| Street 1 | `RESEARCH BLVD` | `RESEARCH Blvd` |
| Street 2 | *(empty)* | `4200 Research Blvd` |
| House Number | `4200` | `4200` |
| Search Term 1 | *(empty)* | `photon` |
| Search Term 2 | *(empty)* | `Accounts Payable` |
| Domain | *(not a column)* | `https://www.photon.com` |
| Contact | `orders@ap.photon.com` | `orders@ap.photon.com` |

Codes raised on the raw record: **`G1-CROSS-001`, `G2-VAL-007`, `G5-NAME-001`.**
Codes raised on the post-pipeline record: **`G3-ADDR-012`, `G5-NAME-001`.**

`G1-CROSS-001` fires because `_extract_addresses` matches the street fragment
`4200 Research Blvd Suite 210` inside Name 1 (`enrichment/issue_detection.py:226-229`). The
pipeline resolves it: the address text is removed from Name 1 and Search Term 1 is populated,
clearing both `G1-CROSS-001` and `G2-VAL-007`.

Two observations the enriched column makes visible. First, `G5-NAME-001` survives: `Labs` is an
abbreviation token under `_ABBREV_TOKEN_RE` (`enrichment/issue_detection.py:148-152, 473-475`),
and the pipeline does not expand it. Second, and more consequentially, the pipeline **introduces**
`G3-ADDR-012`: the extracted fragment is written to Street 2 as `4200 Research Blvd` while
Street 1 holds `RESEARCH Blvd` with House Number `4200`, and `_street_signature` folds the
dedicated house number into Street 1's signature (`enrichment/issue_detection.py:403-417`),
making the two slots identical. This is the only repository record exercising `G3-ADDR-012`.

### 5.2 REC-02 — department label and house number embedded in a pipe-delimited street

Source: `PresentationTestData.xlsx`, sheet row 112 (data row 111).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `FDA - FOOD & DRUG ADMINISTRATION` | `FDA - Food & Drug Administration` |
| Name 2 | *(empty)* | `Bioanalytical Methods Branch` |
| Street 1 | `Bioanalytical Methods Branch \| Division of Bioanalytical Chemistry \| Office of Regulatory Science (HFS-717) \| Center for Food Safety and Applied Nutrition \| U.S. Food and Drug Administration \| 5100 Paint Branch Parkway` | `… \| 5100 Paint Branch Pkwy` (same six segments, terminal type abbreviated) |
| Search Term 1 | *(empty)* | `fda` |
| Search Term 2 | *(empty)* | `Bioanalytical Methods` |

Codes raised on the raw record: **`G1-ADDR-001`, `G1-ADDR-011`, `G2-VAL-003`, `G2-VAL-007`.**
Codes raised on the post-pipeline record: **`G1-ADDR-001`, `G1-ADDR-011`, `G2-VAL-003`.**

`G1-ADDR-011` fires because `_DEPARTMENT_PAYLOAD_RE` matches `Division` in the street
(`enrichment/issue_detection.py:290-294`); `G1-ADDR-001` because the House Number column is
blank while the street carries both a digit token and a street-type word
(`enrichment/issue_detection.py:264-269`). The pipeline promotes the leading segment
`Bioanalytical Methods Branch` into the empty Name 2 and derives both search terms, clearing
`G2-VAL-007`.

⚠ Both address codes persist: the six pipe-delimited segments remain in Street 1 and the House
Number column is still empty, so the raw street was not reduced to a single line here. The
scope-table reduction that would do so is specified in `03_ALGORITHMS.md` Part G; why it did not
apply to this row is not determinable from the workbook alone, which records values and not
execution — ⚠ MEASUREMENT REQUIRED: re-run `/enrich` on this single record with
`WEBSITE_TRACE`-style logging to capture the address stage's decisions.

### 5.3 REC-03 — a care-of address routed out of the street field

Source: `PresentationTestData.xlsx`, sheet row 124 (data row 123).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `DENTSPLY DETREY GMBH` | `DENTSPLY DETREY GmbH` |
| Street 1 | `c/o Dentsply Services Sàrl CH-1400 Yverdon Schweiz` | *(unchanged)* |
| Care Of | *(not a column)* | `Dentsply Services Sàrl CH-1400 Yverdon Schweiz` |
| Search Term 1 | *(empty)* | `dentsplysirona` |

Codes raised on the raw record: **`G1-CROSS-003`, `G2-VAL-003`, `G2-VAL-007`.**
Codes raised on the post-pipeline record: **`G1-CROSS-003`, `G2-VAL-003`.**

`G1-CROSS-003` fires on the `c/o` prefix via `_CO_ATTN_PREFIX_RE`
(`enrichment/issue_detection.py:245-262`). The pipeline extracts the care-of party into the
dedicated `Care Of` column and normalises the legal suffix `GMBH` → `GmbH` (UC 17,
`03_ALGORITHMS.md` Part A). `G1-CROSS-003` nonetheless persists, because the extraction is
non-destructive — the `c/o` text remains in Street 1, so the predicate still matches.

Note the Search Term 1 value `dentsplysirona`, which corresponds to neither the input name nor
the care-of party: it reflects the corporate parent. This is a Tier-1/website-derived handle
(`03_ALGORITHMS.md` Parts C and F) and is recorded here as observed output, not explained by
this document.

---

## 6 · Group G2 — Missing required data

### 6.1 REC-04 — Name 1 missing entirely

Source: `PresentationTestData.xlsx`, sheet row 57 (data row 56).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | *(empty)* | *(empty)* |
| Name 2 | `Accounts Payable` | `Accounts Payable` |
| Street 1 | `MAIN ST` | `MAIN ST` |
| House Number | `100` | `100` |
| Search Term 1 | *(empty)* | *(empty)* |
| Search Term 2 | *(empty)* | `Accounts Payable` |

Codes raised on the raw record: **`G2-VAL-001`, `G2-VAL-007`.**
Codes raised on the post-pipeline record: **`G2-VAL-001`, `G2-VAL-007`.**

`G2-VAL-001` fires from the required-field loop because `name_1` is present as a column but
blank (`enrichment/issue_detection.py:129-137, 330-334`). Both codes persist: the record carries
an administrative desk in Name 2 and no organisation name, and the pipeline does not invent one.
Search Term 2 is populated from the admin desk (UC 6 / ST2 ADMIN branch,
`03_ALGORITHMS.md` Part F), but Search Term 1 depends on a resolvable Name 1 and so remains
empty, keeping `G2-VAL-007`. This record is the clearest exemplar of the pipeline correctly
declining to fabricate a value.

### 6.2 REC-05 — a laboratory with no parent department

Source: `PresentationTestData.xlsx`, sheet row 69 (data row 68).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `University of Florida` | `University of Florida` |
| Name 2 | `Smith Lab` | `Smith Lab` |
| Search Term 1 | *(empty)* | `UF` |
| Search Term 2 | *(empty)* | `Smith` |

Codes raised on the raw record: **`G2-VAL-007`, `G2-NAME-009`, `G5-NAME-002`.**
Codes raised on the post-pipeline record: **`G2-NAME-009`, `G5-NAME-002`.**

`G2-NAME-009` fires because `is_granular_unit("Smith Lab")` holds while neither Name 3 nor
Name 4 carries a unit construction (`enrichment/issue_detection.py:347-351`). The lab resolver
(UC 13, `03_ALGORITHMS.md` Part D §4) is the stage that would promote a parent department into
Name 2 and move the lab to Name 3; it did not fire for this row, so `G2-NAME-009` persists.
`G5-NAME-002` likewise persists on the token `Lab`. `G2-VAL-007` is cleared by the derived
acronym `UF`.

### 6.3 REC-06 — research institution with a contact but no department (JSON fixture)

Source: `tests/fixtures/research_missing_name2_with_contact.json`, `records[0]`.

| Field | Raw value |
|---|---|
| Name 1 | `Massachusetts Institute of Technology` |
| Name 2 | *(null)* |
| Contact | `Person-A` |
| Street | `77 Massachusetts Ave` |
| City / State / ZIP / Country | `Cambridge` / `MA` / `02139` / `US` |

Codes raised: **`G1-ADDR-001`, `G2-NAME-012`, `G2-CONTACT-009`.**

This is the exemplar pair for the two department-completeness rules. `G2-NAME-012` fires because
Name 1 reads as a university or research institute and Name 2 is blank
(`enrichment/issue_detection.py:342-343`); `G2-CONTACT-009` fires under the same gate because a
single contact is present, marking the department as enrichable from that contact
(`enrichment/issue_detection.py:364-369`). It also demonstrates the unreachability of
`G2-CONTACT-008` concretely: that code requires the same gate with the contact **absent**, but
`G2-NAME-012` has already been added under the identical condition, so its guard can never pass.

Column gating is visible on this fixture. With `present_fields` derived from the JSON keys the
detector returns the three codes above; assuming every field present it returns six —
additionally `G2-VAL-003`, `G2-VAL-006`, `G2-VAL-007` — because the fixture carries no tax
jurisdiction, language key, or search term columns at all. The gating rule is
`enrichment/issue_detection.py:329-334`; only the `G2-VAL-*` family is gated, which is the
failure mode recorded in `03_ALGORITHMS.md` Part H §7.

⚠ No enriched counterpart exists for this fixture: `tests/fixtures/expected_outcomes.json` is
the assertion file for the pipeline tests and is not a post-pipeline record dump. Post-pipeline
values for REC-06 are therefore not shown.

---

## 7 · Group G3 — Duplicate and conflicting data

### 7.1 REC-08 — two contacts in one field

Source: `PresentationTestData.xlsx`, sheet row 20 (data row 19).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `Suncoast Medical` | `Suncoast Medical` |
| Name 2 | *(empty)* | `Department of Medicine` |
| Contact | `Person-A; Person-B` | `Person-A; Person-B` |
| Search Term 1 | *(empty)* | `suncoastmedicalsupply` |
| Search Term 2 | *(empty)* | `Medicine` |

Codes raised on the raw record: **`G2-VAL-007`, `G3-CONTACT-007`.**
Codes raised on the post-pipeline record: **`G3-CONTACT-007`.**

`G3-CONTACT-007` fires because `has_multiple_contacts` finds the strong separator `;`
(`enrichment/issue_detection.py:430-432`; separator regex `enrichment/preprocess.py:1065-1068`).
The pipeline populates Name 2 and both search terms but leaves the contact field untouched, so
the code persists — splitting one contact field into two records is outside the enrichment
contract.

Note that `G2-CONTACT-009` does **not** fire on the raw record despite the blank Name 2: that
rule requires exactly one contact (`enrichment/issue_detection.py:368`), and it also requires
Name 1 to read as a university or research institute, which `Suncoast Medical` does not
(`utils/text_utils.py:387-392`).

### 7.2 REC-09 — Name 1 duplicated into Name 2

Source: `PresentationTestData.xlsx`, sheet row 87 (data row 86).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `Tropical Pharma Inc` | `Tropical Pharma Inc` |
| Name 2 | `Tropical Pharma Inc` | `Tropical Pharma Inc` |
| Search Term 1 | *(empty)* | `tropical-pharma` |
| Search Term 2 | *(empty)* | `Tropical Pharma` |

Codes raised on the raw record: **`G2-VAL-007`, `G3-NAME-005`.**
Codes raised on the post-pipeline record: **`G3-NAME-005`.**

`G3-NAME-005` fires on the case- and whitespace-folded equality of Name 1 and Name 2
(`enrichment/issue_detection.py:386-390`). ⚠ The duplicate is **not** cleared by the pipeline,
although UC 12 ("silently clear an identical duplicate name field",
`docs/thesis/01_TRACEABILITY.md:52`) specifies exactly this case and
`03_ALGORITHMS.md` Part A documents the implementing loop. The post-pipeline row still carries
the duplicate and the detector still raises the code. This is a code↔behaviour discrepancy on a
real record and is carried into `08_GAPS.md`; establishing whether the enriched workbook predates
the UC 12 implementation, or whether the rule failed on this input, requires re-running `/enrich`
on the row — ⚠ MEASUREMENT REQUIRED.

### 7.3 REC-10 — two PO boxes on one record

Source: `PresentationTestData.xlsx`, sheet row 50 (data row 49).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `Gulf Coast Labs` | `Gulf Coast Labs` |
| Street 1 | `PO BOX 4500` | `PO BOX 4500` |
| Street 2 | `PO Box 6789` | `PO Box 6789` |
| PO Box | `4500` | *(empty)* |
| Search Term 1 | *(empty)* | `GCL` |

Codes raised on the raw record: **`G1-ADDR-004`, `G2-VAL-007`, `G3-ADDR-005`, `G5-NAME-001`.**
Codes raised on the post-pipeline record: **`G1-ADDR-004`, `G3-ADDR-005`, `G5-NAME-001`.**

`G3-ADDR-005` counts PO-box occurrences across the street slots plus the dedicated column and
fires at two or more (`enrichment/issue_detection.py:392-401`); on the raw record the count is
three. `G1-ADDR-004` fires because a street slot matches `_PO_BOX_RE`
(`enrichment/issue_detection.py:278-282`). Both persist after enrichment, and the dedicated
PO Box column has been **cleared** while both street slots retain their PO-box text — so the
count falls from three to two but stays above the threshold. `G3-ADDR-014` does not fire because
neither street satisfies `_looks_like_street`, which requires a street-type word
(`enrichment/address_processing.py:538-544`).

---

## 8 · Group G4 — Invalid format or length

### 8.1 REC-11 — name overflow beyond the SAP length limit

Source: `PresentationTestData.xlsx`, sheet row 244 (data row 243).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `The Regents of the University of California San Francisco` | *(unchanged)* |
| Name 2 | `Department of Microbiology and Immunology` | `Department of Microbiology & Immunology` |
| Name 3 | `Division of Experimental Virology and Genomics` | *(unchanged)* |
| Name 4 | `Gladstone Institute Virology Research Lab` | *(unchanged)* |
| Search Term 1 | *(empty)* | `UCSF` |
| Search Term 2 | *(empty)* | `Microbiology Immunology` |

Codes raised on the raw record: **`G2-VAL-007`, `G4-NAME-015`, `G5-NAME-002`.**
Codes raised on the post-pipeline record: **`G4-NAME-015`, `G5-NAME-002`.**

`G4-NAME-015` fires because the four name fields total 185 characters, above the
`_SAP_NAME_LIMIT` of 140 (`enrichment/issue_detection.py:121, 440-443`). The pipeline shortens
Name 2 by two characters (`and` → `&`), which does not bring the total under the limit, so the
code persists — as it must, since the overflow is inherent to the record's content and its
resolution is a data-steward decision, not an enrichment one. `G5-NAME-002` persists on the
token `Inst` in Name 4. Note the derived Search Term 1 `UCSF`, an acronym present in none of the
four name fields.

### 8.2 REC-12 — a bare sub-location marker, fully resolved

Source: `PresentationTestData.xlsx`, sheet row 70 (data row 69).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `NovaBio` | `NovaBio` |
| Street 1 | `2200 LAKE BLVD STE` | `2200 LAKE Blvd` |
| House Number | `2200` | `2200` |
| Search Term 1 | *(empty)* | `Novabio` |

Codes raised on the raw record: **`G2-VAL-007`, `G4-ADDR-008`.**
Codes raised on the post-pipeline record: **none.**

`G4-ADDR-008` fires because the street ends in the bare marker `STE` with no value following it
(`_BARE_MARKER_RE`, `enrichment/address_processing.py:254-257`; rule at
`enrichment/issue_detection.py:445-449`). The address stage strips the valueless marker and
title-cases the street type, and Search Term 1 is derived — clearing both codes. This is the only
exemplar in this document that reaches a fully clean post-pipeline state, and it is the clearest
demonstration of the intended before/after reduction.

### 8.3 REC-13 — invalid postal code and non-ISO country

Source: `PresentationTestData.xlsx`, sheet row 40 (data row 39).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `TransGlobal Pharma` | `TransGlobal Pharma` |
| Postal Code | `3360` | `3360` |
| City / Region | `SEATTLE` / `WA` | *(unchanged)* |
| Country/Region Key | `USA` | `USA` |
| Search Term 1 | *(empty)* | `transglobalus` |

Codes raised on the raw record: **`G2-VAL-007`, `G4-ADDR-026`, `G4-ADDR-027`.**
Codes raised on the post-pipeline record: **`G4-ADDR-026`, `G4-ADDR-027`.**

`G4-ADDR-026` fires because the country resolves to `US`, which has a registered format
`^\d{5}(?:-\d{4})?$`, and the four-digit `3360` fails it (`enrichment/issue_detection.py:167-170,
451-456`). `G4-ADDR-027` fires because `country_to_iso_code("USA")` returns `US` while the raw
value upper-cased is `USA`, so the field is not in canonical ISO-2 form
(`enrichment/issue_detection.py:458-463`). Both persist: neither the postal code nor the country
key is rewritten by the pipeline. This record also illustrates the rule's coverage limit — only
US and CA formats are registered, so an equivalently malformed German or UK postcode would raise
nothing.

---

## 9 · Group G5 — Non-standard naming

### 9.1 REC-14 — abbreviation token in Name 1

Source: `PresentationTestData.xlsx`, sheet row 41 (data row 40).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `Cardinal Labs` | `Cardinal Labs` |
| Street 1 | `100 MAIN ST` | `100 MAIN ST` |
| Street 2 | `Central Receiving` | `Central Receiving` |
| Search Term 1 | *(empty)* | `CL` |

Codes raised on the raw record: **`G2-VAL-007`, `G5-NAME-001`.**
Codes raised on the post-pipeline record: **`G5-NAME-001`.**

`G5-NAME-001` fires on the token `Labs` under `_ABBREV_TOKEN_RE`
(`enrichment/issue_detection.py:148-152, 473-475`). The name is not expanded, so the code
persists; only `G2-VAL-007` is cleared, by the derived initialism `CL`. This record shows the
rule's precision-first character: `Labs` is flagged as an abbreviation even where it is the
organisation's established written form.

### 9.2 REC-15 — abbreviated department expanded by the pipeline

Source: `PresentationTestData.xlsx`, sheet row 22 (data row 21).

| Field | Raw value | Post-pipeline value |
|---|---|---|
| Name 1 | `NATIONAL INSTITUTE OF STANDARDS AND TECHNOLOGY-NIST` | `National Institute of Standards and Technology-nist` |
| Name 2 | `Dept. of Physics` | `Department of Physics` |
| Street 1 | `325 Broadway` | `325 Broadway` |
| Search Term 1 | *(empty)* | `nist` |
| Search Term 2 | *(empty)* | `nist` |
| Contact | `contact@physics.national.edu` | `contact@physics.national.edu` |

Codes raised on the raw record: **`G2-VAL-003`, `G2-VAL-007`, `G5-NAME-002`.**
Codes raised on the post-pipeline record: **`G2-VAL-003`.**

This is the exemplar of `G5-NAME-002` being genuinely resolved: `Dept.` is expanded to
`Department`, so `_ABBREV_TOKEN_RE` no longer matches and the code clears
(`enrichment/issue_detection.py:477-481`). Both search terms are derived, clearing
`G2-VAL-007`; `G2-VAL-003` persists because the record has no tax jurisdiction and none is
derivable.

⚠ One side effect visible in the enriched columns is worth recording. The ALL-CAPS Name 1 is
title-cased, but the trailing acronym is damaged: `-NIST` becomes `-nist`, where
`smart_title_case` is specified to preserve acronyms (`03_ALGORITHMS.md` Part A;
`utils/text_utils.py:285-310`). The hyphen-attached form appears not to be recognised as an
acronym segment. This is carried into `08_GAPS.md` as a candidate defect; it is not asserted to
be one here, because the workbook records values only and not the execution that produced them —
⚠ MEASUREMENT REQUIRED to confirm by re-running `/enrich` on this row.

The contact e-mail is retained unchanged in the `Contact` column and the dedicated `Email`
column is empty (verified directly against the workbook, columns 19 and 20).

### 9.3 REC-16 — abbreviated department, JSON fixture

Source: `tests/fixtures/mixed_batch_10_records.json`, `records[1]`.

| Field | Raw value |
|---|---|
| Name 1 | `UCLA` |
| Name 2 | `Dept of Chemistry` |
| Contact | `Person-C` |
| City / State / Country | `Los Angeles` / `CA` / `US` |

Codes raised: **`G5-NAME-002`.**

The single code confirms the rule fires on `Dept` without a trailing period, complementing
REC-15's `Dept.` form. `G2-NAME-012` does **not** fire here even though Name 2 is populated —
and would not fire even if it were blank, because `UCLA` as a bare acronym does not match
`_UNIVERSITY_OR_RESEARCH_SIGNALS_RE`, which requires a spelled-out signal word
(`utils/text_utils.py:387-392`). With all fields assumed present the detector additionally
returns `G2-VAL-002`, `G2-VAL-003`, `G2-VAL-006` and `G2-VAL-007`, which the JSON key gating
suppresses.

⚠ No enriched counterpart exists for this fixture; post-pipeline values are not shown.

---

## 10 · Summary of what the exemplars show

| Group | Exemplars | Codes cleared by the pipeline | Codes persisting |
|---|---|---|---|
| G1 | REC-01, REC-02, REC-03 | `G1-CROSS-001` (REC-01) | `G1-ADDR-001`, `G1-ADDR-011` (REC-02); `G1-CROSS-003` (REC-03) |
| G2 | REC-04, REC-05, REC-06 | none of the G2 rules; `G2-VAL-007` cleared on REC-05 | `G2-VAL-001` (REC-04), `G2-NAME-009` (REC-05), `G2-NAME-012` + `G2-CONTACT-009` (REC-06) |
| G3 | REC-08, REC-09, REC-10 | none | `G3-CONTACT-007`, `G3-NAME-005`, `G3-ADDR-005`, `G1-ADDR-004` |
| G4 | REC-11, REC-12, REC-13 | `G4-ADDR-008` (REC-12) | `G4-NAME-015` (REC-11), `G4-ADDR-026` + `G4-ADDR-027` (REC-13) |
| G5 | REC-14, REC-15, REC-16 | `G5-NAME-002` (REC-15) | `G5-NAME-001` (REC-14), `G5-NAME-002` (REC-16, no counterpart) |

Across these fifteen records the pipeline clears `G1-CROSS-001`, `G4-ADDR-008`, `G5-NAME-002`
and `G2-VAL-007` (widely, through search-term derivation), and introduces `G3-ADDR-012` once
(REC-01). Codes expressing a data-steward decision — a missing organisation name, an over-length
name set, two contacts in one field, a malformed postal code — persist by design. This
distribution is illustrative of these fifteen records only; it is **not** a measurement of
pipeline effectiveness. The reduction metric itself is defined and computed in Pass 7
(`docs/thesis/07_EVALUATION.md`) over the full dataset via `/issues/compare`, and no aggregate
figure should be drawn from this document.

Stop.
