# Phase 1 issue-detection dossier — `/issues` and everything that feeds `Flag Codes`

Scope: the code path from a record (raw XLSX row, JSON object, or enriched export) to the
`Issues` column, and the separate path from enrichment evidence to the `Flag Codes` /
`Flag Reason` / `Flag for Review` / `Flagged Fields` columns. Read-only audit; **no code was
changed**.

Tree state at time of writing: `HEAD = 27768b7` (`feature/llm-fixes`), committed 2026-09-05.
Every `file:line` below is against that tree.

**Two independent paths, joined at one table.** They are not the same mechanism and the
dossier keeps them apart throughout:

```
PATH A — the detector (pure, deterministic, no I/O)
  record content ──► detect_issues (enrichment/issue_detection.py:1231)
                     ├─ _detect_wrong_field   (:681)   G1
                     ├─ _detect_missing       (:811)   G2 + G6 content codes
                     ├─ _detect_duplicate     (:902)   G3
                     ├─ _detect_format        (:967)   G4
                     ├─ _detect_naming        (:1018)  G5
                     ├─ _detect_enrichment_flags (:1190) G6/G7/G8 from `Flag Codes`
                     └─ _detect_verification  (:1213)  G7 from `Flag for Review`
                                        │
                                        ▼
                                `Issues` column (api/routes.py:515)

PATH B — the flag authority (runs inside enrichment, once per record)
  tiers leave `_ev_*` evidence ──► compute_flags (enrichment/flags.py:1059)
                                   called once from finalise (enrichment/orchestrator.py:3173)
                                        │
                                   render (enrichment/flags.py:797)
                                        │
                                        ▼
             flag_codes / flagged_fields / flag_for_review / flag_reason
                                        │
                                        ▼
                        `Flag Codes` column (api/output_columns.py:97)
                                        │
                                        └──► re-read by PATH A as `flag_codes=`
```

`FLAG_CODE_ISSUES` (`enrichment/issue_detection.py:1099-1121`) is the only join between them.

---

## 1. ENTRY POINTS

### 1.1 Route inventory — every route that emits issues or flags

| Route | Handler | `file:line` | Request model | Response |
|---|---|---|---|---|
| `POST /issues` | `detect_file_issues` | api/routes.py:913 | `UploadFile` (`.xlsx`/`.xlsm`) | `StreamingResponse` — the uploaded sheet echoed with one appended `Issues` column |
| `POST /issues/json` | `detect_json_issues` | api/routes.py:960 | `IssueDetectionRequest` (api/models.py:899) | `IssueDetectionResponse` (api/models.py:932) |
| `POST /issues/compare` | `compare_file_issues` | api/routes.py:993 | two `UploadFile`s (`original`, `enriched`) | `StreamingResponse` — `issue_reduction_report.xlsx` |
| `POST /enrich` | `enrich_records` | api/routes.py:112 | `EnrichmentRequest` (api/models.py:311) | `EnrichmentResponse` (api/models.py:868) — carries `flag_codes` as a **JSON list** |
| `POST /enrich/file` | `enrich_file` | api/routes.py:751 | `UploadFile` + 3 query params | `StreamingResponse` — `Flag Codes` as a **`"; "`-joined string** (api/routes.py:446) |
| `GET /health` | `health_check` | api/routes.py:99 | — | `HealthResponse`; no issue or flag content |

**ADF-facing endpoints.** No route in this repository is named or decorated as ADF-specific.
The production wiring is documented prose only, at README.md:3436-3460: ADF Web Activities
call `POST /enrich` (Phase 1) and `POST /api/dedup/cluster-block` (Phase 2), with
`/api/preprocess/consolidate/file` first. `/issues` is run as a **separate ADF pipeline**,
and *why* it is separate is an open question — `docs/thesis/00_OPEN_ITEMS.md:424` item 59,
`⚠ RATIONALE NOT IN REPO`. `GET /health` is described as the ADF/monitoring probe
(api/routes.py:101).

### 1.2 Header alias table (verbatim), from `EnrichmentRecord`

Headers reach fields through `_norm_header` (api/routes.py:139-147, verbatim):

```python
def _norm_header(name: str) -> str:
    """Normalise a column header for tolerant matching.

    Real spreadsheet exports vary in case, surrounding/internal whitespace,
    and punctuation (e.g. "Name 1" vs "name1" vs "NAME 1 "). Collapsing
    those differences lets enriched values land back on the right original
    column instead of being appended as a duplicate.
    """
    return "".join(ch for ch in str(name).lower() if ch.isalnum())
```

and then `_input_alias_to_field` (api/routes.py:150-165), which reverse-maps every declared
`validation_alias` plus the field's own name (`populate_by_name` is on, api/models.py:49).
The declared aliases, verbatim from `api/models.py`:

| `file:line` | Field | `validation_alias=AliasChoices(...)` |
|---|---|---|
| api/models.py:52 | `customer` | `"Customer", "customer", "record_id"` |
| api/models.py:57 | `ecc_customer_number` | `"ECC Customer Number", "ecc_customer_number"` |
| api/models.py:61 | `central_deletion_flag` | `"Central Deletion Flag", "central_deletion_flag"` |
| api/models.py:65 | `comments` | `"Comments", "comments"` |
| api/models.py:69 | `account_group` | `"Account group", "account_group"` |
| api/models.py:73 | `company_code` | `"Company Code", "company_code"` |
| api/models.py:77 | `sales_organization` | `"Sales Organization", "sales_organization"` |
| api/models.py:81 | `distribution_channel` | `"Distribution Channel", "distribution_channel"` |
| api/models.py:85 | `division` | `"Division", "division"` |
| api/models.py:91 | `name_1` | `"Name 1", "name_1", "name1"` |
| api/models.py:95 | `name_2` | `"Name 2", "name_2", "name2"` |
| api/models.py:99 | `name_3` | `"Name 3", "name_3", "name3"` |
| api/models.py:103 | `name_4` | `"Name 4", "name_4", "name4"` |
| api/models.py:107 | `name_5` | `"Name 5", "name_5", "name5"` |
| api/models.py:113 | `street_1` | `"Street 1", "street_1", "street1", "street"` |
| api/models.py:118 | `house_number` | `"House Number", "house_number"` |
| api/models.py:122 | `street_2` | `"Street 2", "street_2", "street2"` |
| api/models.py:126 | `street_3` | `"Street 3", "street_3", "street3"` |
| api/models.py:130 | `street_4` | `"Street 4", "street_4", "street4"` |
| api/models.py:134 | `street_5` | `"Street 5", "street_5", "street5"` |
| api/models.py:138 | `po_box` | `"PO Box", "po_box"` |
| api/models.py:142 | `country_region_key` | `"Country/Region Key", "country_region_key", "country"` |
| api/models.py:146 | `postal_code` | `"Postal Code", "postal_code", "zip"` |
| api/models.py:150 | `city` | `"City", "city"` |
| api/models.py:154 | `region` | `"Region", "region", "state"` |
| api/models.py:160 | `language_key` | `"Language Key", "language_key"` |
| api/models.py:164 | `reconciliation_acct` | `"Reconciliation acct", "reconciliation_acct"` |
| api/models.py:168 | `tax_jurisdiction` | `"Tax Jurisdiction", "tax_jurisdiction"` |
| api/models.py:172 | `central_delivery_block` | `"Central delivery block", "central_delivery_block"` |
| api/models.py:176 | `delivery_priority` | `"Delivery Priority", "delivery_priority"` |
| api/models.py:180 | `shipping_conditions` | `"Shipping Conditions", "shipping_conditions"` |
| api/models.py:184 | `delivering_plant` | `"Delivering Plant", "delivering_plant"` |
| api/models.py:188 | `created_on` | `"Created On", "created_on"` |
| api/models.py:192 | `created_by` | `"Created By", "created_by"` |
| api/models.py:196 | `vat_registration_no` | `"VAT Registration No.", "vat_registration_no"` |
| api/models.py:200 | `search_term_1` | `"Search Term 1", "search_term_1"` |
| api/models.py:204 | `search_term_2` | `"Search Term 2", "search_term_2"` |
| api/models.py:208 | `terms_of_payment_contact` | `"Terms of Payment Contact", "terms_of_payment_contact"` |
| api/models.py:220 | `care_of` | `"care_of", "Care Of", "c/o"` |
| api/models.py:224 | `contact` | `"contact", "Contact"` |
| api/models.py:228 | `email` | `"email", "Email"` |

Two headers are read **outside** the model, by direct `_norm_header` comparison, and have no
alias at all:

| Header | Read by | `file:line` | Absent ⇒ |
|---|---|---|---|
| `Flag for Review` | `_flag_for_review` | api/routes.py:185-197 | returns `None`, G7-VERIFY-001 unreachable |
| `Flag Codes` | `_flag_codes` | api/routes.py:265-268 | `codes` stays `None` unless a provenance column is present |
| `Name 1 Provenance` / `Name 2 Provenance` | `_flag_codes` | api/routes.py:205, :270-289 | provenance fallback does not run |

### 1.3 Which SAP columns the detector reads, and which it ignores

Read by at least one detector (`enrichment/issue_detection.py`):

| Field | Read at | For |
|---|---|---|
| `name_1`..`name_5` | `_names`, :633-634 | G1-CROSS-001/-003, G1-NAME-001/-004/-013, G2-NAME-009/-012, G3-NAME-003/-005, G4-NAME-015, G5-NAME-001/-002 |
| `street_1`..`street_5` | `_streets`, :637-644 | G1-CROSS-002/-003, G1-ADDR-001/-003/-004/-006/-011, G3-ADDR-012/-013/-014, G4-ADDR-008/-025 |
| `house_number` | :726, :941 | G1-ADDR-001, G3-ADDR-012 |
| `po_box` | :924 | G3-ADDR-005, G3-ADDR-014 |
| `contact` | :959 | G3-CONTACT-007 |
| `postal_code` | :980-984 | G2-VAL-002, G4-ADDR-026 |
| `country_region_key` | :981, :987-991 | G2-VAL-008, G4-ADDR-026, G4-ADDR-027 |
| `region` | `_REQUIRED_FIELD_CODES` :395 | G2-VAL-004 |
| `tax_jurisdiction` | :394 | G2-VAL-003 |
| `language_key` | :396 | G2-VAL-006 |
| `search_term_1` | :397 | G2-VAL-007 |
| `record_id` | :848 (log only) | — |

**Read but only through the model, never by a detector** — i.e. accepted, validated, and
ignored for issue purposes: `ecc_customer_number`, `central_deletion_flag`, `comments`,
`account_group`, `company_code`, `sales_organization`, `distribution_channel`, `division`,
`city`, `reconciliation_acct`, `central_delivery_block`, `delivery_priority`,
`shipping_conditions`, `delivering_plant`, `created_on`, `created_by`, `vat_registration_no`,
`search_term_2`, `terms_of_payment_contact`, `care_of`, `email`.

Two of those *are* consulted, but only in `api/routes._name2_has_no_canonical_form`
(api/routes.py:226-231) — `city`, `region`, `country_region_key` — to reproduce the
pipeline's Name 2 exemption. That is the provenance fallback, not a detector.

Columns unknown to the model are carried through verbatim by `_build_issues_xlsx`
(api/routes.py:513-515) and never validated.

### 1.4 Blank / `None` handling

| Layer | Rule | `file:line` |
|---|---|---|
| XLSX parse | an empty cell is **dropped from the row dict**, so blank and absent are the same thing to the detector; an all-empty row is skipped | api/routes.py:902-909 |
| JSON parse | same stringify-and-strip, empty values dropped — but an **all-empty record is kept** so the positional response stays aligned | api/routes.py:885-909 |
| `_present_fields` | the *header* is what makes a column present, never the value | api/routes.py:168-182 |
| Detectors | `is_blank(v)` = `v is None or v.strip() == ""` | utils/text_utils.py:23-25 |
| Required-field rules | fire only when the column is **present in the file and blank**; a column absent from the file is skipped, silently, and DEBUG-logged | enrichment/issue_detection.py:825-851 |
| `_flag_for_review` | `None` = "raw file, G7 cannot apply"; `False` = "enriched, not flagged". Both suppress G7-VERIFY-001, only `None` says the question was never asked | api/routes.py:185-197 |
| `_flag_codes` | same `None` vs `[]` distinction for G6/G7-CONFIRM/G8 | api/routes.py:239-245 |
| `detect_issues(present_fields=None)` | assumes **every** field present — the isolated-record default | enrichment/issue_detection.py:1244-1249 |

### 1.5 Order of execution relative to enrichment

`detect_issues` runs each detector unconditionally, in this fixed order
(enrichment/issue_detection.py:1276-1282):

```python
    found: set[str] = set()
    _detect_wrong_field(record, found)
    _detect_missing(record, found, present_fields)
    _detect_duplicate(record, found)
    _detect_format(record, found)
    _detect_naming(record, found)
    _detect_enrichment_flags(found, flag_codes)
    _detect_verification(found, flag_for_review)
```

Order within the function is **immaterial to the result** — every detector writes into the
same `set`, and the return is re-sorted into catalogue order (`:1287`). What matters is which
*inputs* each stage can see:

| Stage | Computed from | Can fire on a raw input file? |
|---|---|---|
| `_detect_wrong_field`, `_detect_missing`, `_detect_duplicate`, `_detect_format`, `_detect_naming` | **record content only** — the same rules run over raw and enriched alike, which is what makes the before/after delta meaningful | yes |
| `_detect_enrichment_flags` | the enriched export's `Flag Codes` column, after ROR / GLEIF / SERP / LLM / Wikidata have all run and `compute_flags` has settled | **no** — `flag_codes` is `None` for a raw audit |
| `_detect_verification` | the enriched export's `Flag for Review` column | **no** — `flag_for_review` is `None` for a raw audit |
| **provenance fallback** | `Name 1 / Name 2 Provenance` columns, parsed through the grammar; synthesises the `low-confidence-unchanged` token when `Flag Codes` does not carry it | **no** — needs a provenance column |

On the enrichment side (PATH B) the ordering is strict and stated in the code:
`compute_flags` is called **once**, from `finalise`, at enrichment/orchestrator.py:3173 —
after `_ship_unverified_domain` (`:3163`, the last thing that can raise `_domain_unverified`)
and before `_registry_name_fields` is stripped. Two passes run later and are the only two
that may touch a flag afterwards:

* `raise_after` — enrichment/flags.py:917, called once at enrichment/orchestrator.py:2383
  for UC 0's `overflow` after the name-block repack;
* `retract` — enrichment/flags.py:974, called from batch consensus at
  enrichment/batch_consensus.py:674-675, which runs after the **whole batch** is finalised.
  It can only ever withdraw (`enrichment/flags.py:20-22`).

---

## 2. FLAG CATALOGUE

### 2.1 Two vocabularies, not one

The reviewer sees two token sets, and the dossier must not conflate them:

* **`enrichment.flags.ALL_CODES`** (enrichment/flags.py:196-214) — 17 codes, the pipeline's
  own vocabulary, shipped in the `Flag Codes` column.
* **`enrichment.issue_detection.ISSUE_CATALOGUE`** (enrichment/issue_detection.py:201-312) —
  41 declared Issue-Catalogue entries, shipped in the `Issues` column.

### 2.2 `Flag Codes` vocabulary — every code the pipeline can emit

Group is the Issue-Catalogue group each maps into via `FLAG_CODE_ISSUES`
(enrichment/issue_detection.py:1099-1121). "Blocks" is the DATAshaper effect of the mapped
issue code: every one of G6-RESOLVE-001, G7-CONFIRM-001 and G8-VERIFY-001 is declared
`mandatory=False`, so **no flag code blocks the SAP load** — they all annotate.

| Flag code | Constant | Raised at (`file:line`) | Trigger (verbatim / exact rule) | Maps to | Effect | Emitted as |
|---|---|---|---|---|---|---|
| `overflow` | `OVERFLOW` :127 | flags.py:1089-1099 from `_ev_overflow` (orchestrator.py:8030); late via `raise_after` (orchestrator.py:2383) | `overflow_fields = evidence.get("_ev_overflow")` — truthy | **nothing** (`UNMAPPED_FLAG_CODES` :1129) | annotates | token + prose |
| `opaque-code` | `OPAQUE_CODE` :128 | flags.py:1113-1118 | `if value and _is_opaque_code(str(value))` for each of `NAME_SLOTS` | G6-RESOLVE-001 | annotates | token + prose |
| `person-unresolved` | `PERSON_UNRESOLVED` :126 | flags.py:1120-1121 from orchestrator.py:5324 | `if evidence.get("_ev_person_unresolved")` | G8-VERIFY-001 | annotates | token + prose |
| `entity-superseded` | `ENTITY_SUPERSEDED` :158 | flags.py:1129-1133 from wikidata.py:326-338 / liveness.py:147 | `superseded = evidence.get("_ev_entity_superseded")` — Wikidata `P576` (dissolved) or `P1366` (replaced by), or ROR `status=inactive` / GLEIF `RETIRED`,`MERGED` | **nothing** (:1132) | annotates | token + prose |
| `source-conflict` | `SOURCE_CONFLICT` :166 | flags.py:1139-1145 from consistency.py:291 | `conflict = evidence.get("_ev_source_conflict")` — raised **only when the pipeline acted** and removed the losing source's fields | **nothing** (:1133) | annotates (the *removal* already happened) | token + prose |
| `no-match` | `NO_MATCH` :93 | flags.py:1420-1421 | `if not codes and not low_confidence and _nothing_was_enriched(result)` | G8-VERIFY-001 | annotates | token + prose |
| `unverified-inference` | `UNVERIFIED_INFERENCE` :151 | flags.py:1241-1251 (undecidable arm) and :1254-1282 (evidence-free arm) | undecidable arm: `verdicts.get(field) == "undecidable"` **and** `result.get(f"{field}_changed")`. Evidence-free arm: `_evidence_free_fields` minus `registry_named`, `corroborated`, unchanged, `_ev_pure_repair` | G7-CONFIRM-001 | annotates | token + prose |
| `relocated-unverified` | `RELOCATED_UNVERIFIED` :149 | flags.py:1236-1237 via `relocated_unverified_fields` (:727-794) | slot origin in `{"preprocess:street","preprocess:split","preprocess:moved"}`, slot ≠ `name1`, value non-blank, not in `_ev_input_confirmed`, provenance starts `input:` and carries no `+` witness, `department_domain` empty | G7-CONFIRM-001 | annotates | token + prose |
| `low-confidence-unchanged` | `LOW_CONFIDENCE_UNCHANGED` :111 | **DERIVED ONLY** — flags.py:872-880 in `render`, from `low_confidence=`; never raiseable (`ValueError` at :835-844) | union of `low_confidence_core_fields` (:655-718) and `marker_low` (:1304-1315), filtered by `_still_as_supplied` and `opaque_fields` (:1414-1419) | G8-VERIFY-001 | annotates | token + prose |
| `name-states-another-site` | `NAME_STATES_ANOTHER_SITE` :191 | flags.py:1158-1161 from orchestrator.py:8038 | `site_conflict = evidence.get("_ev_name_site_conflict")` — a site qualifier taken off a name field naming a city/state the address block contradicts | G6-RESOLVE-001 | annotates | token + prose |
| `registry-location-mismatch` | `REGISTRY_LOCATION_MISMATCH` :175 | flags.py:1149-1152 from consistency.py:406 | `location_mismatch = evidence.get("_ev_registry_location_mismatch")` | **nothing** (:1131) | annotates; **advisory** — does not queue | token + prose |
| `dept-via-lab` | `DEPT_VIA_LAB` :112 | flags.py:1321-1322 from orchestrator.py:8729 | `if evidence.get("_ev_dept_via_lab")` | G7-CONFIRM-001 | annotates | token + prose |
| `dept-via-contact` | `DEPT_VIA_CONTACT` :125 | flags.py:1323-1332 from orchestrator.py:3881 | `if evidence.get("_ev_dept_via_person")` | G7-CONFIRM-001 | annotates | token + prose |
| `name3-not-demoted` | `NAME3_NOT_DEMOTED` :143 | flags.py:1333-1334 from orchestrator.py:8734 | `if evidence.get("_ev_name3_not_demoted")` | **nothing** (:1130) | annotates | token + prose |
| `multiple-contacts` | `MULTIPLE_CONTACTS` :144 | flags.py:1341-1342 from orchestrator.py:8024 | `if evidence.get("_multi_contact") and not result.get("contact_used")` | G6-RESOLVE-001 | annotates | token + prose |
| `email-conflict` | `EMAIL_CONFLICT` :142 | flags.py:1344-1345 from orchestrator.py:8032 | `if evidence.get("_ev_email_conflict")` | G6-RESOLVE-001 | annotates | token + prose |
| `domain-unverified` | `DOMAIN_UNVERIFIED` :137 | flags.py:1352-1364 from `_domain_unverified` | `rejected_domain = evidence.get("_domain_unverified")` — truthy | G7-CONFIRM-001 | annotates; **advisory** — does not queue | token + prose |

`ADVISORY_CODES` (enrichment/flags.py:249-252), verbatim:

```python
ADVISORY_CODES: frozenset[str] = frozenset({
    REGISTRY_LOCATION_MISMATCH,
    DOMAIN_UNVERIFIED,
})
```

An advisory code emits its token, its scope and its prose; what it does **not** do is set
`flag_for_review` (enrichment/flags.py:906-908):

```python
        "flag_for_review": (
            bool(set(ordered) - ADVISORY_CODES) or bool(low)
        ),
```

### 2.3 `Issues` catalogue — every code the detector can emit

`EMITTED_CODES` (enrichment/issue_detection.py:315-317) = every entry whose `status` is
`"live"` or `"unlisted"`. Group is `ISSUE_CATALOGUE[code].group` — **never the prefix**
(:346-352: G6 holds four `G2-` codes). `mandatory=True` ⇒ DATAshaper *Error*, blocks the SAP
load (:189-192, README.md:3495-3499).

| Code | Group | Name | Field | Mandatory / effect | Origin | Set at | Trigger |
|---|---|---|---|---|---|---|---|
| G1-CROSS-001 | G1 | Address Content in Name Field | Name 1 | No / Warning | API | :686-689 | `nm and _extract_addresses(nm)[0]` on any name slot |
| G1-CROSS-002 | G1 | Org Name in Address Field | Street | No / Warning | API | :694-703 | `_ORG_IN_STREET_RE.search(without_centre) and not _STREET_TYPE_WORD_RE.search(st)` |
| G1-CROSS-003 | G1 | Contact Information in Wrong Field | varies | No / Warning | API | :707-722 | email / phone / URL / c-o-ATTN in a name or street; else `_street_person_name(st)` |
| G1-ADDR-001 | G1 | House Number Embedded in Street | Street | No / Warning | **DS** | :726-730 | `is_blank(record.house_number)` **and** `_looks_like_street(st)` |
| G1-ADDR-003 | G1 | Sub-location Embedded in Street | Street 2 | No / Warning | API | :733-739 | any `_SUITE_PATTERNS` match, or `_has_detection_only_sublocation(st)` |
| G1-ADDR-004 | G1 | PO Box Embedded in Street | Street | No / Warning | API | :742-745 | `_PO_BOX_RE.search(st)` |
| G1-ADDR-006 | G1 | Mail Code in Street Field | Street 2 | No / Warning | API | :748-753 | `_extract_mail_code(st, allow_bare=True)[1] or _has_mail_code(st)` |
| G1-ADDR-011 | G1 | Department Label in Street Field | Street 2 | No / Warning | API | :756-759 | `_looks_like_department(st)` |
| G1-NAME-001 | G1 | Name Overflow Across Fields | Name 1 | No / Warning | API | :767-777 | upper and lower both populated, upper has no legal suffix, `_NAME_CONTINUATION_RE.search(lower)` |
| G1-NAME-004 | G1 | Empty field in between populated name fields | Name 2 | No / Warning | API | :786-794 | a blank slot with something populated both above and below |
| G1-NAME-013 | G1 | SAP Internal Code in Name Field | Name 2 | No / Warning | API | :797-800 | `_is_opaque_code(nm)` |
| G1-ADDR-009 | G1 | Unclassified Residual in Address | Street 2 | No / Warning | API | **`ndd`, never emitted** :217-227 | — |
| G2-VAL-002 | G2 | Postal Code Missing | Postal Code | **Yes / Error** | DS | :825-843 | column present and `is_blank` |
| G2-VAL-004 | G2 | Region Missing | Region | **Yes / Error** | DS | :825-843 | column present and `is_blank` — unconditional; the US-only predicate was removed, see :374-390 |
| G2-VAL-007 | G2 | Search Term 1 Missing | Search Term 1 | **Yes / Error** | DS | :825-843 | column present and `is_blank` |
| G2-VAL-008 | G2 | Country Missing | Country | **Yes / Error** | DS | :825-843 | column present and `is_blank` |
| G2-NAME-009 | G2 | Lab Without Department | Name 2 | No / Warning | API | :878-887 | `is_granular_unit(value)` in a dept slot with no `is_specific_unit_construction` / `is_unit_construction` sibling |
| G2-CONTACT-008 | G2 | No Contact and No Department | Name 2 | No | API | **`withdrawn`** :234-241 | — |
| G2-CONTACT-009 | G2 | Department Missing And Enrichable from Contact | Name 2 | No | API | **`withdrawn`** :242-251 | — |
| G3-NAME-003 | G3 | DBA Pattern in Name Field | Name 1 | No / Warning | BOTH | :907-910 | `_normalise_dba(nm)[1]` |
| G3-NAME-005 | G3 | Duplicate Name Across Fields | Name 2 | No / Warning | API | :914-918 | `upper_norm and upper_norm == _norm(lower)` at any adjacent pair |
| G3-ADDR-005 | G3 | Multiple PO Boxes on Record | PO Box | No / Warning | API | :928-929 | `po_box_count >= 2` |
| G3-ADDR-012 | G3 | Duplicate Street Across Fields | Street | No / Warning | API | **`unlisted`** :256-265; emitted :937-945 | `len(street_sigs) != len(set(street_sigs))` |
| G3-ADDR-013 | G3 | Two Distinct Street Addresses on Record | Street | No / Warning | API | :948-952 | `len(set(real_streets)) >= 2` |
| G3-ADDR-014 | G3 | PO Box and Street Both Present | PO Box | No / Warning | BOTH | :955-956 | `po_box_count >= 1 and any(_looks_like_street(st))` |
| G3-CONTACT-007 | G3 | Multiple Contacts on Record | Name 2 | No / Warning | API | :959-960 | `has_multiple_contacts(record.contact)` |
| G4-NAME-015 | G4 | Name Overflow Beyond the Name Block | Name 4 | **Yes / Error** | API | :969-971 | `sum(len(nm) for nm in _names(record) if nm) > 140` |
| G4-ADDR-008 | G4 | Bare Sub-location Marker Without Value | Street 2 | No / Warning | API | :974-977 | `_BARE_MARKER_RE.search(st)` |
| G4-ADDR-025 | G4 | Sub-location Overflow Beyond Street 5 | Street 5 | No / Warning | API | :1003-1011 | `len(sublocations) > 4` distinct `(kind, value)` pairs |
| G4-ADDR-026 | G4 | Postal Code Format Invalid | Postal Code | No / Warning | DS | :980-984 | postal present, country in `_POSTAL_FORMATS`, regex does not match |
| G4-ADDR-027 | G4 | Country Code Not ISO 2-letter | Country | **Yes / Error** | DS | :987-991 | `iso is None or raw.upper() != iso` |
| G5-NAME-001 | G5 | Organisation Name Not in Official Form | Name 1 | No / Warning | API | :1023-1024 | `_is_non_canonical_name(record.name_1)` |
| G5-NAME-002 | G5 | Unit Name Not in Official Form | Name 2-4 | No / Warning | API | :1027-1030 | `_is_non_canonical_name(nm)` for any of Name 2..5 |
| G2-VAL-001 | **G6** | Name 1 Missing | Name 1 | **Yes / Error** | DS | :825-843 | column present and `is_blank` |
| G2-VAL-003 | **G6** | Tax Jurisdiction Missing | Tax Jurisdiction | **Yes / Error** | DS | :825-843 | column present and `is_blank` |
| G2-VAL-006 | **G6** | Language Missing | Language | **Yes / Error** | DS | :825-843 | column present and `is_blank` |
| G2-NAME-012 | **G6** | Research Institution Missing Department | Name 2 | No / Warning | DS | :870-874 | `looks_like_university_or_research_institute(name_1) and is_blank(name_2)` |
| G6-RESOLVE-001 | G6 | Enrichment Could Not Resolve the Record | Flag Codes | No / Warning | API | :1207-1210 | any of `opaque-code`, `email-conflict`, `multiple-contacts`, `name-states-another-site` |
| G7-VERIFY-001 | G7 | Enriched Record Requires Verification | Flag for Review | No / Warning | API | :1223-1224 | `if flag_for_review:` |
| G7-CONFIRM-001 | G7 | Enriched Value Requires Confirmation | Flag Codes | No / Warning | API | :1207-1210 | any of `domain-unverified`, `unverified-inference`, `dept-via-lab`, `dept-via-contact`, `relocated-unverified` |
| G8-VERIFY-001 | G8 | Enrichment Left the Value Unestablished | Flag Codes | No / Warning | API | :1207-1210 | any of `low-confidence-unchanged`, `no-match`, `person-unresolved` |

**Counts, derived from the source and pinned by test.** 41 declared (`ISSUE_CATALOGUE`
:201-312); 38 emittable (`EMITTED_CODES`); 2 withdrawn; 1 `ndd`; 1 `unlisted`. Asserted by
`tests/test_issue_detection.py::test_docstring_counts_match_the_catalogue` (:145) — adding or
retiring a code fails the suite until the module docstring is updated. Nine emittable codes
are `mandatory=True` and block the load: G2-VAL-001/-002/-003/-004/-006/-007/-008,
G4-NAME-015, G4-ADDR-027 (README.md:3501).

### 2.4 Emission channel: token vs prose

**Every `Flag Codes` code is emitted in both channels.** `render` (enrichment/flags.py:872-891)
walks `_CODE_ORDER` once and appends to `ordered` (the token list) and `reasons` (the prose)
in the same iteration; there is no branch that adds one without the other. So there is no
code that is prose-only or token-only.

**`Issues` is codes only, never prose** — `_build_issues_xlsx` writes `"; ".join(codes)`
(api/routes.py:515) with no name lookup, even though `issue_name` exists (:340-343).

### 2.5 The three accounting questions asked in the brief

**(a) Codes defined in `enrichment/flags.py` but never emitted — none.** All 17 members of
`ALL_CODES` have a raise site (§2.2). `LOW_CONFIDENCE_UNCHANGED` is the only one no *caller*
may raise (`ValueError`, :835-844) — but `render` emits it itself (:872-880), so it does
reach the column. Confirmed by `tests/test_flags.py::TestFlagFieldsStayConsistent`.

**(b) Codes emitted but not in the constant list — none.** The vocabulary is partitioned by
construction and by test: every member of `ALL_CODES` is either a key of `FLAG_CODE_ISSUES`
or a member of `UNMAPPED_FLAG_CODES`, pinned by
`tests/test_flag_issue_alignment.py::TestEveryFlagCodeIsAccountedFor::test_the_vocabulary_is_partitioned`
(:367). The five deliberately-unmapped, verbatim (enrichment/issue_detection.py:1128-1134):

```python
UNMAPPED_FLAG_CODES: frozenset[str] = frozenset({
    "overflow",
    "name3-not-demoted",
    "registry-location-mismatch",
    "entity-superseded",
    "source-conflict",
})
```

The reasons are stated at enrichment/issue_detection.py:1083-1098: `overflow` and
`name3-not-demoted` are already reported from record content as G1-NAME-001 and G4-NAME-015
(double-counting); `registry-location-mismatch` is advisory and a queue entry would
contradict that; `entity-superseded` and `source-conflict` ask a *business* question — which
legal entity a record should point at after a merger — that no catalogue code carries the
meaning for.

**(c) Notion Issue Catalogue entries with no code counterpart.** The Notion source
(`355109a5c46181498a76ee02e7c7c220`, last edited 2026-08-20, per eval/out/RUNS.md:59) **is not
in this repository**, and neither is the `claude_ISSUE-CATALOGUE-FLAG-GROUPS.md` file the
brief names — `find / -iname "*ISSUE-CATALOGUE*"` returns nothing. What the tree *does*
carry is the code's own record of the divergence, which is the honest substitute:

| Situation | Code | Recorded at |
|---|---|---|
| In v2, no deterministic rule possible | `G1-ADDR-009` (`ndd`) | enrichment/issue_detection.py:217-227 |
| In v2, struck through | `G2-CONTACT-008`, `G2-CONTACT-009` | :234-251 |
| **Emitted here, absent from v2** | `G3-ADDR-012` | :256-265 — "Either it was withdrawn and this detector should stop emitting it, or v2 omits it and Notion needs the row added. Left emitting, unchanged, pending that decision" |
| Name divergence from v2 | `G4-NAME-015` — v2 says "Name Overflow Beyond Name 4"; the block is five slots wide | :270-273 |
| Scope change from v1 | `G1-NAME-004` — v2's rename widened it from the Name 2/Name 3 pair to any gap | :212-215 |

I cannot enumerate "Notion rows with no code counterpart" beyond these, because the Notion
extract is not on this machine. That is a gap in this dossier, not a finding of completeness.

---

## 3. DETECTORS

Common contract: every detector takes `(record, found: set[str])`, mutates `found`, returns
`None`. Because `found` is a **set** and every loop `break`s on its first hit, **no detector
can raise the same code twice for one row** — the "fires more than once per row" column below
is therefore `no` for every content detector, and the question only has force for the
per-slot loops, which still add one code.

### 3.1 `_detect_wrong_field` — enrichment/issue_detection.py:681-804

Signature: `_detect_wrong_field(record: EnrichmentRecord, found: set[str]) -> None`.
Inputs: `_names(record)` (:682), `_streets(record)` (:683), `record.house_number` (:726).

| Code | Check (verbatim) | Blank input | Twice? |
|---|---|---|---|
| G1-CROSS-001 | `if nm and _extract_addresses(nm)[0]:` (:687) | `nm` falsy ⇒ skipped | no (`break` :689) |
| G1-CROSS-002 | `_ORG_IN_STREET_RE.search(without_centre) and not _STREET_TYPE_WORD_RE.search(st)` (:699-700), after `_UNIVERSITY_CENTRE_RE.sub(" ", st)` (:697) | `if not st: continue` (:695) | no (:703) |
| G1-CROSS-003 | `_EMAIL_RE / _PHONE_RE / _URL_RE / _CO_ATTN_PREFIX_RE .search(field)` (:711-714); `for…else` fallback to `_street_person_name(st)` (:719-722) | `if not field: continue` (:708) | no (:717/:722) |
| G1-ADDR-001 | `if is_blank(record.house_number):` then `if _looks_like_street(st):` (:726-729) | blank house number is the **precondition**; a blank street returns False from `_looks_like_street` | no (:730) |
| G1-ADDR-003 | `any(pat.search(st) for pat, _ in _SUITE_PATTERNS) or _has_detection_only_sublocation(st)` (:735-736) | `st` falsy ⇒ skipped | no (:739) |
| G1-ADDR-004 | `if st and _PO_BOX_RE.search(st):` (:743) | skipped | no (:745) |
| G1-ADDR-006 | `_extract_mail_code(st, allow_bare=True)[1] or _has_mail_code(st)` (:750) | skipped | no (:753) |
| G1-ADDR-011 | `if _looks_like_department(st):` (:757) | `_looks_like_department(None)` is falsy | no (:759) |
| G1-NAME-001 | `not is_blank(upper_val) and not is_blank(lower_val) and not _has_legal_suffix(upper_val or "") and _NAME_CONTINUATION_RE.search(lower_val or "")` (:771-774) | either slot blank ⇒ no fire | no (:777) |
| G1-NAME-004 | `not populated[idx] and any(populated[:idx]) and any(populated[idx + 1:])` (:789-791) | this rule **is** the blank rule; a leading blank (Name 1) is excluded by `range(1, len-1)` (:787) so it reports as G2-VAL-001 instead | no (:794) |
| G1-NAME-013 | `if nm and _is_opaque_code(nm):` (:798) | skipped | no (:800) |

Lexicons and thresholds used here, verbatim:

`_ORG_IN_STREET_RE` (:508-515) —
```python
_ORG_IN_STREET_RE = re.compile(
    r"\b(?:University|Universit[äa]t|Institute|Institut|College|Faculty|"
    r"School|Hospital|Clinic|Corp(?:oration)?|Inc|Incorporated|LLC|Ltd|"
    r"Limited|Company|GmbH|Technolog(?:y|ies)|Systems|Solutions|"
    r"Laborator(?:y|ies)|Labs|Industries|Sciences|Instruments|"
    r"Pharmaceuticals?|Pharma)\b",
    re.IGNORECASE,
)
```

`_NAME_CONTINUATION_RE` (:458-460) —
```python
_NAME_CONTINUATION_RE = re.compile(
    r"^\s*(?:and|&|of|for|the|de|der|und|et)\b|^\s*[a-z]",
)
```

`_DETECTION_ONLY_SUBLOCATION_RE` (:542-545) — detection-only, deliberately not folded into
`_SUITE_PATTERNS` (which decides *which SAP column* a value lands in):
```python
_DETECTION_ONLY_SUBLOCATION_RE = re.compile(
    r"\b(?:Gate|Wing)\s+(\w[\w\-]*)\b",
    re.IGNORECASE,
)
```
Corpus witnesses named in the comment (:529-531): `Gate` — 40000008 "4500 SAN PABLO RD S GATE C";
`Wing` — 41000007. Dock / Bay / Annex / Block / Entrance were considered and **left out** for
want of a corpus witness (:532-534).

`_MAIL_CODE_MARKER_RE` (:593-597) —
```python
_MAIL_CODE_MARKER_RE = re.compile(
    r"\b(?P<marker>Mail\s*Stop|Mailstop|Mail\s*Code|M\s*[./]\s*S|MS)\b\.?"
    r"\s*[:#\-]?\s*(?P<value>[A-Za-z0-9][\w\-]*)\b",
    re.IGNORECASE,
)
```
with two guards applied to the `MS` spelling only (`_has_mail_code`, :600-611): a five-digit
value is a ZIP ("Jackson MS 39201"), and the value must be `_is_identifier_like` — which is
what separates "MS K-12" from "Ms Johnson Way". A bare `MS` with no value never fires.

### 3.2 `_detect_missing` — enrichment/issue_detection.py:811-895

Signature: `_detect_missing(record, found, present_fields: set[str] | None) -> None`.

The required-field table, verbatim (:391-399) — note **no entry carries a predicate**:
```python
_REQUIRED_FIELD_CODES: list[tuple[str, str, Callable[[EnrichmentRecord], bool] | None]] = [
    ("name_1", "G2-VAL-001", None),
    ("postal_code", "G2-VAL-002", None),
    ("tax_jurisdiction", "G2-VAL-003", None),
    ("region", "G2-VAL-004", None),
    ("language_key", "G2-VAL-006", None),
    ("search_term_1", "G2-VAL-007", None),
    ("country_region_key", "G2-VAL-008", None),
]
```

The loop's three-way verdict (:825-851) is DEBUG-traced precisely because a silent skip and a
clean result are indistinguishable in the output:

```python
        if not column_present:
            verdict = "skipped — column absent from file"
        elif condition is not None and not condition(record):
            verdict = "skipped — code condition not met for this record"
        elif is_blank(getattr(record, field_name)):
            found.add(code)
            verdict = "FIRED — column present and blank"
        else:
            verdict = "no issue — column populated"
```

An import-time guard, `_validate_required_field_mapping` (:422-451, result at :454), warns
when a rule is keyed on a field the model does not declare or that carries no header alias —
the failure mode that let G2-VAL-004 sit permanently dark (:374-390). Pinned by
`tests/test_issue_detection.py::test_required_field_rules_all_have_a_reachable_column_mapping`
(:774).

| Code | Check | Blank input | Twice? |
|---|---|---|---|
| G2-VAL-001/-002/-003/-004/-006/-007/-008 | above | **blankness is the trigger**, gated on `present_fields` | no (set) |
| G2-NAME-012 | `looks_like_university_or_research_institute(record.name_1) and is_blank(record.name_2)` (:870-873) | blank Name 2 is the trigger; blank Name 1 ⇒ no fire | no |
| G2-NAME-009 | `is_granular_unit(value)` in a dept slot with no `is_specific_unit_construction(x) or is_unit_construction(x)` sibling (:878-887) | `is_granular_unit(None)` falsy ⇒ skipped | no (`break` :887) |

G2-NAME-012 reads **Name 2 alone**, deliberately, not "no department anywhere in the block":
scanning the block suppressed the code whenever a department sat in the wrong slot (Yale with
Name 2 blank and Name 3 "Department of Chemistry"), which is precisely the record a steward
most needs (:860-869). G1-NAME-004 and G2-NAME-012 fire together on such a row, and that is
correct — they state two different things.

### 3.3 `_detect_duplicate` — enrichment/issue_detection.py:902-960

| Code | Check (verbatim) | Blank input | Twice? |
|---|---|---|---|
| G3-NAME-003 | `if nm and _normalise_dba(nm)[1]:` (:908) | skipped | no (:910) |
| G3-NAME-005 | `if upper_norm and upper_norm == _norm(getattr(record, lower, None)):` (:916) | `_norm(None)` = `""`, and `upper_norm` truthiness gate means **two blank slots never match** | no (:918) |
| G3-ADDR-005 | `if po_box_count >= 2:` (:928); count = street slots matching `_PO_BOX_RE` + 1 if `not is_blank(record.po_box)` (:921-925) | blanks contribute 0 | no |
| G3-ADDR-012 | `if len(street_sigs) != len(set(street_sigs)):` (:944); `_street_signature` returns `None` for a blank (:663-664) and folds `house_number` into Street 1 only (:941) | blanks excluded from the signature list | no |
| G3-ADDR-013 | `if len(set(real_streets)) >= 2:` (:951) | `_looks_like_street(None)` falsy | no |
| G3-ADDR-014 | `if po_box_count >= 1 and any(_looks_like_street(st) for st in streets):` (:955) | as above | no |
| G3-CONTACT-007 | `if has_multiple_contacts(record.contact):` (:959) | blank contact ⇒ falsy | no |

`_street_signature` (:652-674) is an order- and case-independent `(frozenset(numbers),
tuple(sorted(words)))` pair, which is what catches SAP's house-number split: Street 1
"Innovation Blvd" + House Number "500" produces the same signature as Street 2 "500
Innovation Blvd".

### 3.4 `_detect_format` — enrichment/issue_detection.py:967-1011

| Code | Check | Blank input | Twice? |
|---|---|---|---|
| G4-NAME-015 | `combined = sum(len(nm) for nm in _names(record) if nm)` > `_SAP_NAME_LIMIT` = **140** (:356, :969-971) | blanks contribute 0 | no |
| G4-ADDR-008 | `if st and _BARE_MARKER_RE.search(st):` (:975) | skipped | no (:977) |
| G4-ADDR-026 | `if not is_blank(record.postal_code):` then country lookup then `not fmt.match(...)` (:980-984) | blank postal ⇒ **no fire** (that is G2-VAL-002's job) | no |
| G4-ADDR-027 | `if iso is None or raw.upper() != iso:` (:990) | blank country ⇒ no fire (G2-VAL-008's job) | no |
| G4-ADDR-025 | `if len(sublocations) > _SUBLOCATION_SLOTS:` where `_SUBLOCATION_SLOTS = 4` (:360, :1010) | blank streets skipped (:1005-1006) | no |

`_POSTAL_FORMATS`, verbatim (:623-630) — **coverage is US, CA, DE and nothing else**:
```python
_POSTAL_FORMATS: dict[str, re.Pattern[str]] = {
    "US": re.compile(r"^\d{5}(?:-\d{4})?$"),
    "CA": re.compile(r"^[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d$"),
    # Exactly five digits — no separator, no country prefix. The "D-70174"
    # form still in circulation is pre-1993 and not the SAP-canonical value,
    # so it is reported rather than accepted.
    "DE": re.compile(r"^\d{5}$"),
}
```
Read a clean G4-ADDR-026 count as "no defect found in three countries", never as "the postal
codes are good" (:616-622). A French, British or Japanese record is *unchecked*, not valid.

G4-ADDR-025 counts distinct `(kind, value)` pairs from the pipeline's own
`_extract_sublocations`, which **consumes** each match as it goes — so an overlapping pattern
("Bldg 4 Floor" matching both the building rule and the value-before-marker floor rule) cannot
inflate the count (:994-1002).

### 3.5 `_detect_naming` — enrichment/issue_detection.py:1018-1030

Field attribution is **by slot and nothing else**: an abbreviation in Name 1 is `-001`, one in
Name 2..N is `-002`; a record carrying one only below Name 1 raises `-002` alone (:1020-1022).

`_ABBREV_TOKEN_RE` (:483-488) —
```python
_ABBREV_TOKEN_RE = re.compile(
    r"\b(?:Univ|Uni|Dept|Dep|Div|Inst|Natl|Nat'l|Intl|Int'l|Assoc|Assn|Ctr|"
    r"Lab|Labs|Tech|Sch|Mgmt|Engrg|Eng|Sci|Med|Svcs|Svc|Co|"
    r"Corp|Inc|Ltd|Mfg|Hosp|Grp|Fla)\b\.?",
    re.IGNORECASE,
)
```
`_DOTTED_ACRONYM_RE` (:495) —
```python
_DOTTED_ACRONYM_RE = re.compile(r"\b[A-Za-z](?:\.[A-Za-z]){1,}\.?(?![A-Za-z])")
```
Single letters are required throughout, which keeps "St. Louis" and "Ave. B" out (:491-494).

Volume warning stated in the source (:472-478): abbreviated legal suffixes (Corp, Inc, Ltd)
are deliberately **in** the set, so G5-NAME-001 volume rises substantially on commercial data.
That is the honest reading of the rule as written; the stated fix, if the volume is unwanted,
is to split legal suffixes into their own code, not to exclude them silently.

**Declared out of scope, permanently** (module docstring :72-86): a *misspelled* name
("Universiteat Stuttgart") will never be raised here. Detecting it means recognition against
world knowledge, not pattern matching; any deterministic proxy is a false-positive generator.
The same applies to a name that is complete and correctly spelled but not the legal one
("Lockheed Martin" for "Lockheed Martin Corporation").

Blank input: `_is_non_canonical_name(None)` returns `False` at :501-502. Cannot fire twice
(`found` is a set; `-002` `break`s at :1030).

### 3.6 `_detect_enrichment_flags` — enrichment/issue_detection.py:1190-1210

```python
    for code in flag_codes or ():
        issue = FLAG_CODE_ISSUES.get(code.strip().lower())
        if issue:
            found.add(issue)
```

Blank input: `flag_codes=None` ⇒ the loop body never runs ⇒ none of G6-RESOLVE-001 /
G7-CONFIRM-001 / G8-VERIFY-001 can fire. An unmapped token raises nothing.
Cannot fire twice: three flags mapping to one issue raise it once — pinned by
`tests/test_issue_detection.py::test_several_flags_mapping_to_one_issue_raise_it_once` (:658).

### 3.7 `_detect_verification` — enrichment/issue_detection.py:1213-1224

```python
    if flag_for_review:
        found.add("G7-VERIFY-001")
```
Blank input: `None` and `False` both suppress; only `None` says the question was never asked
(api/routes.py:188-192). Cannot fire twice.

### 3.8 Flag-origin detectors outside `issue_detection.py`

**`enrichment/dept_block.py` raises no flag.** It is a pure-function authority over the
department block — "It writes nothing, calls no model and does no I/O: `result` is read only
for `has_no_canonical_form`'s address-token comparison, and may be None"
(enrichment/dept_block.py:19-21). Its `classify` (:215-244) returns
`empty | admin | identifies_nothing | granular | unit`; the first two are the halves of
`has_no_canonical_form`, the predicate that `name2_needs_no_verification`
(enrichment/flags.py:599-652) consults to *withhold* both Name 2 doubts. Its influence on
flags is entirely suppressive. Blank input: `classify("")` ⇒ `"empty"` (:230-231);
`name2_needs_no_verification` returns `False` for a blank Name 2 (enrichment/flags.py:639-640).

**`enrichment/grounded_resolver.py` raises no flag either** — it produces the *verdicts and
proposals* `compute_flags` reads. `GroundedProposal.verdict` is `same | undecidable`
(:186-190); `different` never becomes a proposal — it is refused at :678-681 and travels in
`dropped` / `suggestions` so the flag can name it. The three guards, in order (:649-681):
`_is_address_like_name` ⇒ `dropped["address_like"]`; `not _appears_in(value,
evidence_haystack)` ⇒ `dropped["not_in_evidence"]` (a composed name is not a grounded answer);
`classify_name_change(...) == DIFFERENT` ⇒ `dropped["identity_not_preserved"]`. A proposal
that reproduces the record's own value is recorded as `confirmed` rather than written
(:756-769) — deliberately *not* a write, because writing the same string back costs the record
its `input:verified+web` attribution (:229-241).

**`api/routes.py`** raises exactly one flag code, and only in the audit direction:
`DERIVED_LOW_FLAG_CODE` at :287-288. See §4.

**`name2_needs_no_verification`** — enrichment/flags.py:599-652. Consulted by **both** name2
doubts (:1214-1215 for `unverified-inference`, :711-712 inside `low_confidence_core_fields`),
which is what makes "clears both" true rather than merely intended. Blank input: `False`
(:639-640). Note the documented degradation at :645-651: when `retract` reaches it with an
`EnrichmentResult` (no `.get`), the predicate answers without the record's address tokens —
the conservative direction, since the exemption is withheld rather than granted.

**`relocated_unverified_fields`** — enrichment/flags.py:727-794. Returns `[]` immediately when
`_slot_origin` is empty (:752-753), when there is no provenance log (:755-756), or when
`department_domain` is populated (:772-773). Fires **once per slot**, so unlike the content
detectors this one genuinely can contribute several scope entries for one row (:1236-1237
loops and calls `raise_flag` per label) — but still only one `relocated-unverified` code.

---

## 4. G6 / G7 / G8 DERIVATION

### 4.1 The two paths, side by side

Both live in `api/routes._flag_codes` (api/routes.py:234-290). They are **not** an
either/or — the second runs unconditionally after the first.

| | Path 1 — the `Flag Codes` token path | Path 2 — the provenance-prose fallback |
|---|---|---|
| Code | api/routes.py:265-268 | api/routes.py:270-289 |
| Reads | the `Flag Codes` column | `Name 1 Provenance`, `Name 2 Provenance` (`_CORE_PROVENANCE_HEADERS`, :205) |
| Parser | `split_flag_codes` (issue_detection.py:1146-1162), splitting on `[;,]` and accepting a JSON list | `provenance_is_low` (issue_detection.py:1165-1187), parsing through the **grammar**, not by string prefix |
| Produces | any of the 17 flag tokens → G6 / G7-CONFIRM / G8 via `FLAG_CODE_ISSUES` | exactly one token: `low-confidence-unchanged` → **G8-VERIFY-001 only** |
| Runs when | the file has a `Flag Codes` column | the file has either provenance column |
| Column absent ⇒ | `codes` stays `None` | `codes` is coerced to `[]` at :274-275 — which itself flips the "raw file" signal |

Verbatim (api/routes.py:264-290):

```python
    codes: list[str] | None = None
    for header in headers:
        if _norm_header(header) == _norm_header("Flag Codes"):
            codes = split_flag_codes(row.get(header))
            break

    for header in headers:
        norm = _norm_header(header)
        if not any(norm == _norm_header(c) for c in _CORE_PROVENANCE_HEADERS):
            continue
        if codes is None:
            codes = []
        if not provenance_is_low(row.get(header)):
            continue
        if (
            norm == _norm_header("Name 2 Provenance")
            and record is not None
            and _name2_has_no_canonical_form(record)
        ):
            # The pipeline looked at this slot and decided there was nothing
            # to ask. Keep looking at the other column rather than stopping:
            # a Name 1 doubt on the same row still stands.
            continue
        if DERIVED_LOW_FLAG_CODE not in codes:
            codes.append(DERIVED_LOW_FLAG_CODE)
        break
    return codes
```

Three mechanics worth pinning:

1. **Header order decides which column is consulted.** The loop `break`s on the first
   provenance column that reads `low`. `RESPONSE_COLUMNS` puts `Name 1 Provenance` before
   `Name 2 Provenance` (api/output_columns.py:116-117), so on an `/enrich/file` export the
   Name 1 doubt always wins and the Name 2 exemption is never consulted for that row.
2. **The Name 2 exemption is applied on Path 2 only**, via `_name2_has_no_canonical_form`
   (api/routes.py:208-231) → `enrichment.flags.name2_needs_no_verification`. Without it the
   two halves of the function would contradict each other (:256-262).
3. **The `not in` check** (:287) is the only thing preventing a row that carries the token in
   both places from saying so twice.

### 4.2 Which one is authoritative today

**Path 1 is authoritative in principle; Path 2 is what actually produces G8 on every export
in this tree.** The pipeline emits the `low-confidence-unchanged` token again
(enrichment/flags.py:44-55, :872-880), so a *current* export names it. Every enriched workbook
in this repository predates that change: measured across all four `327ee53` strata plus the
99-row thesis export, **not one row carries `low-confidence-unchanged` in `Flag Codes`**, and
every G8-VERIFY-001 on them comes from Path 2. That matches eval/out/RUNS.md:317-327, whose
per-stratum code mix lists no such token.

Path 2 "still runs" in three situations:

* an export taken while the token was withdrawn (all in-tree artefacts);
* a file with provenance columns but **no** `Flag Codes` column at all;
* a current export, where it is a no-op thanks to the `not in` guard.

### 4.3 The 26-row G8 over-emission

**The file.** `docs/thesis/test-all-100-original_enriched (4).xlsx`, 99 data rows. It is the
99-row export README.md:2311 refers to. Its raw input is in-tree as
`docs/thesis/test-all-100-original.xlsx`, which is what makes the analysis below checkable
rather than asserted.

**The count.** 26 rows carry `G8-VERIFY-001`. **26 of 26 come from Path 2**; zero come from a
`Flag Codes` token. Reproduced with:

```
detect_issues(record, _present_fields(headers),
              flag_for_review=_flag_for_review(row, headers),
              flag_codes=_flag_codes(row, headers, record))
```

**The condition.** Path 2 asks one question — *does this provenance scalar parse to `low`* —
and `compute_flags` asks four more before it emits the same token
(enrichment/flags.py:1414-1419):

```python
    low_confidence = _sorted_fields(
        {
            f for f in (set(low_confidence_core_fields(result)) | marker_low)
            if _still_as_supplied(f) and f not in opaque_fields
        },
    )
```

`_still_as_supplied` (:1391-1412) is false when the field was **rewritten** (`{field}_changed`
and not `_ev_pure_repair`) or **confirmed** (`_ev_input_confirmed`). Neither state is visible
in the provenance column: `input:low` attributes the *source*, not whether the slot was later
written. `low_confidence_core_fields` adds two more Path 2 cannot see — an empty field
(:704-705) and the admin-desk exemption (:711-712, which Path 2 *does* reproduce, but only
for Name 2 and only when the loop reaches it).

**Which rows.** Joining the 26 against the raw input on `Customer` and applying the
`*_changed` test (`differs from input by more than case`, enrichment/orchestrator.py:2371-2375),
**10 of the 26 sit on a slot the pipeline rewrote** — states in which `compute_flags` would
have withheld the code:

| Customer | Provenance column read | Raw value | Enriched value |
|---|---|---|---|
| 13337493 | Name 2 `input:low` | *(empty)* | `Lot 20 Princeton Neuroscience` |
| 13164629 | Name 2 `input:low` | `Co` | `Clinton Twp Facility` |
| 13164376 | Name 2 `input:low` | `Co` | `Clinton Twp Facility` |
| 13011411 | Name 1 `input:low` | `Exxonmobil Research & Engineering` | `Exxonmobil Research & Engineering Co` |
| 13140330 | Name 2 `input:low` | `Baytown Refinery Lab` | `Baytown Refinery Laboratory` |
| 13343017 | Name 1 `input:low` | `Zoetis Ref Lab Cincinnati` | `Zoetis Ref Laboratory Cincinnati` |
| 13337029 | Name 1 `input:low` | `Zoetis Ref Lab Cincinnati` | `Zoetis Ref Laboratory Cincinnati` |
| 13189969 | Name 1 `input:low` | `Texas A&M System Health Science Ctr` | `Scott & White Hospital Modul C` |
| 13343777 | Name 2 `input:low` | *(empty)* | `Zale Receiving - Labs` |
| 13335676 | Name 2 `input:low` | *(empty)* | `Center for Davie Medical` |

The remaining 16 are correct: the slot really was left as supplied (13345790 `PAVIR`→`Pavir`,
13128613 `Ames Research Center`→`Ames Research Center`, and so on — case-only differences,
which `*_changed` correctly treats as unchanged).

**Why.** The mechanism is named in the source, on the exact row: enrichment/flags.py:1383-1386
records that "one of them [sat] on a slot that arrived EMPTY and shipped 'Lot 20 Princeton
Neuroscience', reported as left exactly as supplied" — customer 13337493, row 1 of the table
above. `compute_flags` was fixed to filter that case out (`_still_as_supplied`, added with the
`_ev_pure_repair` / `_ev_input_confirmed` machinery); the route's Path 2 re-derivation was
not, because it re-derives from a column that cannot express the distinction.

**Two readings, both true, stated separately so a reviewer can pick.** Under the strict
reading — "G8 raised by an audit path the pipeline's own flag decision does not stand behind"
— it is 26 of 26 rows on that export. Under the narrow reading — "G8 raised where
`compute_flags` had actively withheld it" — it is 10 of those 26.

**Cross-check on the current strata.** The same query over `eval/out/327ee53/`:

| Export | rows | G6 | G7-CONFIRM | G8 | of which Path 2 only | on a row the pipeline did **not** flag |
|---|---|---|---|---|---|---|
| S1_enriched.xlsx | 100 | 0 | 37 | 15 | 15 | 0 |
| S4_enriched.xlsx | 100 | 0 | 38 | 33 | 33 | 1 (13189969) |
| S5_enriched.xlsx | 100 | 1 | 46 | 34 | 31 | 0 |
| t100_enriched.xlsx | 99 | 0 | 40 | 19 | 19 | 3 |
| `docs/thesis/test-all-100-original_enriched (4).xlsx` | 99 | 0 | 35 | **26** | **26** | 0 |

S5's 34 − 31 = 3 is exactly its `no-match` (1) + `person-unresolved` (2) tokens
(eval/out/RUNS.md:323-325) — the only two G8 flag codes any in-tree export carries.

**Note on README.md:2311.** It records this measurement as "40 of the 40 rows carrying
`G8-VERIFY-001`" on a 99-row export of that vintage. The tree today gives 26 for that file.
The two are not reconcilable from the artefacts present: the README figure predates the Name 2
admin-desk exemption on the provenance path (api/routes.py:278-286), and no intermediate export
in the tree reproduces 40. Treat the README number as stale and the 26 above as the current
one — it is reproducible from two files that are both in the repository.

### 4.4 The follow-on fix

Not implemented; stating what it would be, since the brief asks. The route's Path 2 needs the
one input it lacks — whether the slot was rewritten. Two shapes:

1. **Withhold Path 2 when `Flag Codes` is present.** The column being present means
   `compute_flags` ran and reached a decision; re-deriving over the top of it is what produces
   the divergence. This would reduce Path 2 to its stated purpose (old exports, and files with
   no `Flag Codes` column) at the cost of the `not in` de-duplication becoming dead code.
2. **Make the withheld state legible in the export.** `flag_low_confidence` is
   `exclude=True` (enrichment/flags.py:57-62) and never ships, so an auditor cannot see which
   fields `compute_flags` decided about. Shipping it — or shipping `*_changed` — would let
   Path 2 apply `_still_as_supplied` itself.

Option 1 is the smaller change and needs no schema addition; option 2 is a column change and
therefore lands in the same "pending Bert" queue as `Flag Codes` (§6.3).

---

## 5. PROVENANCE COUPLING

### 5.1 Which provenance values imply which flags

Exactly **one** flag is derived by parsing a provenance string rather than from a detector or
an evidence marker: `low-confidence-unchanged`. It reaches the column by two distinct
derivations, and neither is a tier raising a code.

| Derivation | Where | Reads | Emits |
|---|---|---|---|
| Inside the pipeline | `low_confidence_core_fields` (enrichment/flags.py:655-718) → `render(low_confidence=)` (:872-880) | the record's **provenance log**, via `derived_scalar` (enrichment/provenance.py:985-1000), for `CORE_PROVENANCE_FIELDS = ("name1_enriched", "name2_enriched")` (:596) | `low-confidence-unchanged`, scoped per field |
| Inside the audit | `_flag_codes` Path 2 (api/routes.py:270-289) | the **exported scalar columns** `Name 1 / Name 2 Provenance`, via `provenance_is_low` (issue_detection.py:1165-1187) | the same token → G8-VERIFY-001 |

The rule is stated at enrichment/flags.py:44-49 and :94-110: `low-confidence-unchanged` says
"left exactly as supplied — the canonical form could not be established", which is the
definition of `input:low` on the field, so the record's own provenance decides it. A caller
that passes it in `scopes` still gets a `ValueError` (:835-844) — the marker a tier remembers
to leave and the fact derived from the write history are exactly the two things that could
once disagree.

`low_confidence_core_fields` reads the **log**, not the exported column, for a stated reason
(:672-673): `compute_flags` runs before those columns are projected, and a flag that read a
column it also helps produce could drift from it.

One further flag reads provenance, but as a *filter* rather than a source:
`relocated_unverified_fields` (enrichment/flags.py:788-792) requires
`str(scalar).startswith("input:")` and `"+" not in str(scalar)` — a slot an authority answered
for is no longer input-class, and a slot carrying a witness on the input is excluded.

Two flags are derived from the log by a different projection, not by string parsing:
`unverified-inference` via `_evidence_free_fields` (:562-583) → `weak_fields`
(enrichment/provenance.py:1033-1049), which reads `event.producer_chain[-1] in
EVIDENCE_FREE_PRODUCERS`.

`EVIDENCE_FREE_PRODUCERS` (enrichment/provenance.py:1020-1028) and `REGISTRY_PRODUCERS`
(:1031), verbatim:
```python
EVIDENCE_FREE_PRODUCERS: frozenset[str] = frozenset(
    {"llm_tier3", "llm_grounded"},
)
REGISTRY_PRODUCERS: frozenset[str] = frozenset({"ror", "gleif"})
```

### 5.2 The closed vocabulary, verbatim

Grammar (enrichment/confidence.py:30-33):
```
    provenance := source ":" confidence ( "+" witness )?
    source     := "input" | "ror" | "gleif" | "wikidata" | "web:" domain | "llm"
    confidence := "verified" | "provisional" | "low"
    witness    := "web" | "wikidata" | "llm" | "registry" | "domain" | "dba"
```

Constants (enrichment/confidence.py:53-103):
```python
VERIFIED = "verified"
PROVISIONAL = "provisional"
LOW = "low"

CONFIDENCES: tuple[str, ...] = (VERIFIED, PROVISIONAL, LOW)

SOURCE_INPUT = "input"
SOURCE_ROR = "ror"
SOURCE_GLEIF = "gleif"
SOURCE_WIKIDATA = "wikidata"
SOURCE_LLM = "llm"
SOURCE_WEB_PREFIX = "web:"

REGISTRY_SOURCES: frozenset[str] = frozenset(
    {SOURCE_ROR, SOURCE_GLEIF, SOURCE_WIKIDATA},
)

WITNESS_WEB = "web"
WITNESS_WIKIDATA = "wikidata"
WITNESS_LLM = "llm"
WITNESS_REGISTRY = "registry"
WITNESS_DOMAIN = "domain"
WITNESS_DBA = "dba"

WITNESSES: tuple[str, ...] = (
    WITNESS_WEB, WITNESS_WIKIDATA, WITNESS_LLM,
    WITNESS_REGISTRY, WITNESS_DOMAIN, WITNESS_DBA,
)

NON_CORROBORATING_WITNESSES: frozenset[str] = frozenset({WITNESS_LLM})
```

The single compiled definition (enrichment/confidence.py:108-119):
```python
_DOMAIN = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"

PROVENANCE_PATTERN = (
    rf"^(?P<source>input|ror|gleif|wikidata|llm|web:{_DOMAIN})"
    rf":(?P<confidence>verified|provisional|low)"
    rf"(?:\+(?P<witness>web|wikidata|llm|registry|domain|dba))?$"
)

PROVENANCE_RE = re.compile(PROVENANCE_PATTERN)
```

### 5.3 The validator

`validate` — enrichment/confidence.py:290-318, verbatim:
```python
def validate(provenance: str) -> None:
    """Raise unless *provenance* matches the grammar AND hard rules 1–2.

    Hard rule 3 (rejected evidence never appears) is not checkable from the
    string alone — it is a property of what the pipeline chose to record, and
    it is enforced at the adapter and asserted by the per-state fixtures.
    """
    source, confidence, witness = parse(provenance)

    # Hard rule 1 — llm can never produce or contribute to `verified`.
    if confidence == VERIFIED:
        if source == SOURCE_LLM:
            raise ProvenanceGrammarError(
                f"{provenance!r}: `llm` as source can never be `verified` "
                "(hard rule 1)",
            )
        if witness in NON_CORROBORATING_WITNESSES:
            raise ProvenanceGrammarError(
                f"{provenance!r}: `+{witness}` can never carry a value to "
                "`verified` (hard rule 1)",
            )

    # Hard rule 2 — a witness-less `verified` is legal only for a registry.
    if confidence == VERIFIED and witness is None:
        if source not in REGISTRY_SOURCES:
            raise ProvenanceGrammarError(
                f"{provenance!r}: `verified` without a witness is legal only "
                f"for {sorted(REGISTRY_SOURCES)} (hard rule 2)",
            )
```

Enforcement points: `render` validates on the way out, "so an invalid combination fails at the
site that built it, where the stack trace still names the lane" (:265-266); `validate_all`
(:321-328) is the finalisation assertion's one call, and treats an empty column as always
legal.

**The audit's consumption is deliberately non-throwing.** `provenance_is_low`
(enrichment/issue_detection.py:1181-1187) catches `ProvenanceGrammarError` and returns
`False` — "a cell that is not provenance at all is not low — an audit reports what it can read
and never guesses" (:1178-1179). It parses through the grammar rather than by string prefix
because `web:acme.com:low` contains two colons and a naive split puts the domain in the
confidence slot (:1176-1178).

---

## 6. OUTPUT

### 6.1 Issue-related columns emitted

**`/issues` and `/issues/json`** emit exactly one issue column:

| Column | Type | Format | Example | Written at |
|---|---|---|---|---|
| `Issues` | string | `"; "`-joined bare codes; empty string when clean | `G1-ADDR-001; G3-NAME-003; G7-CONFIRM-001; G8-VERIFY-001` | api/routes.py:511, :515 |

The JSON twin returns the same list unjoined, as `IssueDetectionResult.issues`
(api/models.py:926-929). There is **no** `Issue Count`, `Needs Review`, or severity column on
either endpoint — the brief names them, and they do not exist in this tree. `IssueDefinition.severity`
(enrichment/issue_detection.py:189-192) is derivable per code but is never projected into any
output. `/issues/compare` emits its own report workbook (`_build_comparison_xlsx`,
api/routes.py:569) with Before/After/Delta per code; that is a report, not a record-grain column.

**`/enrich` and `/enrich/file`** emit the four flag columns, plus the two provenance columns
the audit's Path 2 reads (api/output_columns.py:95-121):

| Column | Result field | Type | Format | Example |
|---|---|---|---|---|
| `Flag for Review` | `flag_for_review` | bool | `TRUE`/`FALSE` | `True` |
| `Flag Codes` | `flag_codes` | list → string | `"; "`-joined (api/routes.py:446); JSON list on `/enrich` | `relocated-unverified; domain-unverified` |
| `Flagged Fields` | `flagged_fields` | list → string | `"; "`-joined | `name2; domain` |
| `Flag Reason` | `flag_reason` | string \| null | clauses joined `"; "` (enrichment/flags.py:909) | see §7 examples |
| `Name 1 Provenance` | `name1_provenance` | string \| null | `source:confidence[+witness]` | `input:low` |
| `Name 2 Provenance` | `name2_provenance` | string \| null | same | `ror:verified` |

`flag_scopes`, `flag_details`, `flag_notes` and `flag_low_confidence` are internal
(`exclude=True`) and never ship (enrichment/flags.py:57-62).

### 6.2 Ordering rule for multiple values

| List | Rule | `file:line` |
|---|---|---|
| `Issues` | **catalogue order**, never insertion or alphabetical: `return [code for code in ISSUE_CATALOGUE if code in found]` | enrichment/issue_detection.py:1287 |
| `Flag Codes` | **`_CODE_ORDER`**, "most structural first, so the leading clause of a multi-code reason is the one that most changes what a reviewer does" | enrichment/flags.py:254-282, applied at :872 |
| `Flag Reason` | same `_CODE_ORDER`, built in the same loop — the prose and the tokens can never disagree about order | enrichment/flags.py:872-891 |
| `Flagged Fields` | `_FIELD_ORDER` = `(*NAME_SLOTS, "domain", "contact", "email", "address")`, unknown names dropped | enrichment/flags.py:446-448, :497-499 |

`_CODE_ORDER`, verbatim (enrichment/flags.py:256-282):
```python
_CODE_ORDER: tuple[str, ...] = (
    OVERFLOW,
    OPAQUE_CODE,
    PERSON_UNRESOLVED,
    ENTITY_SUPERSEDED,
    SOURCE_CONFLICT,
    NO_MATCH,
    UNVERIFIED_INFERENCE,
    RELOCATED_UNVERIFIED,
    LOW_CONFIDENCE_UNCHANGED,
    NAME_STATES_ANOTHER_SITE,
    REGISTRY_LOCATION_MISMATCH,
    DEPT_VIA_LAB,
    DEPT_VIA_CONTACT,
    NAME3_NOT_DEMOTED,
    MULTIPLE_CONTACTS,
    EMAIL_CONFLICT,
    DOMAIN_UNVERIFIED,
)
```
(comments elided for width; they are on the original lines). Neither list is sorted
alphabetically and neither preserves insertion order.

### 6.3 What DATAshaper reads, and the binding state of `Flag Codes`

**`usp_merge_legacy_issues` does not exist in this repository.** `sql/` contains three
procedures and no issues merge:

```
sql/usp_merge_legacy_enriched.sql
sql/usp_merge_validation_clusters.sql
sql/usp_merge_validation_scores.sql
```

A repo-wide search for `usp_merge_legacy_issues` returns nothing. The `Issues` column's
write-back path is therefore not represented in this tree at all — that is a genuine gap, not
an omission from this dossier.

**`Flag Codes` is NOT bound in the one merge procedure that exists.**
`sql/usp_merge_legacy_enriched.sql` declares in its `OPENJSON … WITH` list:

```sql
[Flag for Review] BIT           '$."Flag for Review"',
[Flag Reason]     NVARCHAR(500) '$."Flag Reason"'
```

and updates exactly those two:

```sql
tgt.[Flag for Review] = src.[Flag for Review],
tgt.[Flag Reason]     = src.[Flag Reason];
```

`Flag Codes` and `Flagged Fields` appear nowhere in the procedure. So the machine-readable
vocabulary this whole dossier is about **does not currently reach the database** — a
DATAshaper rule can key on the boolean or parse the prose, and nothing else.

**Binding state, honestly:** *pending, and not yet raised with Bert as such.* The repository
records three pending-Bert schema items, and `Flag Codes` is not among them:

* `Operating Name` / `Operating Name Provenance` — README.md:3466-3477, with the explicit
  three-step instruction to add them to `OPENJSON` and `WHEN MATCHED THEN UPDATE SET`;
* the six provenance scalar columns, 59 → 65 — "**Whether DATAshaper's column-typed
  validation model accepts 65 columns needs confirming externally before rollout**"
  (README.md:3707; also provenance_migration_report.md:286, "Open from the earlier Fix 10 work
  and **still open**");
* `Suggested Name` / `Suggestion Source` — "**Still pending Bernd's answer on whether
  DATAshaper passes unmapped columns through**" (eval/out/RUNS.md:314-316,
  docs/change-task-template.md:156-157).

That last item is the one that determines what `Flag Codes` does today: if DATAshaper passes
unmapped columns through, the column survives to the sheet unread; if not, it is dropped. The
answer is not in the repository.

**The severity axis that *is* bound.** `enrichment_status` maps to DATAshaper severity
(README.md:3482-3491): `enriched` → no issue, `verified` → Info, `unresolved` → Warning,
`failed` → Error. Per-code severity is the separate `mandatory` axis (README.md:3493-3501),
carried in `IssueDefinition` and never exported.

---

## 7. DETERMINISM

### 7.1 The detector is unconditionally deterministic

`enrichment/issue_detection` is "**Pure and deterministic** — regex / string checks only. No
enrichment, no LLM call, no network/external I/O" (module docstring, :9-12). It imports only
compiled patterns and pure predicates. `/issues` and `/issues/json` therefore make **zero**
external calls. `/issues/compare` calls `_audit_upload` (api/routes.py:522-566), which calls
`detect_issues` and never `enrich_batch` — this settles open item 67
(`docs/thesis/00_OPEN_ITEMS.md:441`, `⚠ UNVERIFIED`), which asks exactly that question.

The one non-determinism the detector *could* carry is input-shaped, not I/O-shaped:
`present_fields` depends on the file's header row, so the same record audited from two
differently-shaped files can produce different G2-VAL-* results. That is by design
(:1244-1249).

### 7.2 Flags that depend on external calls

Every code below is a `Flag Codes` code, hence reaches `Issues` only through
`FLAG_CODE_ISSUES`.

| Flag code | External dependency | Behaviour on cache miss / timeout / firewall block |
|---|---|---|
| `domain-unverified` | SERP (SerpAPI or DuckDuckGo) + page fetch | The evidence key is `_domain_unverified`, set only when a candidate domain **was found and the ownership guard rejected it**. No search result ⇒ no candidate ⇒ **no flag** — the record falls through to `no-match` or the derived low instead. The appended page note (`_domain_page_note`, enrichment/orchestrator.py:7345) is dropped when the fetch fails, and `render` emits the generic `_REASONS` wording (enrichment/flags.py:884-890). |
| `unverified-inference` | LLM (Tier 3 / grounded lane) | Derived from `weak_fields`, i.e. from *who wrote last*. If the LLM never wrote, the field is not weak and the code cannot fire. A failed grounded call sets `degraded=True` (grounded_resolver.py:204-207) and the caller falls back to `run_tier3`. |
| `relocated-unverified` | none directly — but **suppressed by** `department_domain` (a probe result) and by `_ev_input_confirmed` (a grounded page read) | A blocked probe leaves `department_domain` empty, which **removes a suppressor**: the code fires on a firewalled run where it would not on a warm one. Directional, and the direction is toward more flags. |
| `entity-superseded` | Wikidata (`P576`/`P1366`), ROR `all_status=`, GLEIF status | Fires only on a positive finding. A blocked lane yields no `LivenessFinding` ⇒ no flag. `probe_ror_status` takes `timeout: float = 15.0` (liveness.py:230). |
| `source-conflict`, `registry-location-mismatch` | ROR / GLEIF responses, compared in `enrichment/consistency.py` (:291, :406) | Both require two sources to have answered. One source unreachable ⇒ no comparison ⇒ no flag. |
| `dept-via-lab`, `dept-via-contact` | SERP + page read (Tier 2B / Tier 2A) | Fire only when the lane produced a department. A blocked lane produces none. |
| `no-match` | inverse dependence — fires when **nothing** resolved | The one code a firewall makes *more* likely: `_nothing_was_enriched` (flags.py:510-549) is satisfied by a run in which every lane failed. Guarded by `if not codes and not low_confidence` (:1420) so a more specific doubt always wins. |
| `low-confidence-unchanged` | inverse, same shape — `input:low` is what a field carries when no source answered | Same direction as `no-match`, and takes precedence over it. The guard at :1420 exists because retiring this code without moving the guard "silently promoted eleven rows of the chemspeed batch from 'confirm this value is correct' to 'no source could identify this organisation' — measured, not hypothesised" (:1372-1378). |
| `opaque-code`, `overflow`, `email-conflict`, `multiple-contacts`, `name3-not-demoted`, `name-states-another-site`, `person-unresolved` | **none** — all deterministic preprocessing / regex | unaffected |

**Net effect of a firewall block on the `Issues` column:** G7-CONFIRM-001 falls (its five
feeder flags all need a successful external call) and G8-VERIFY-001 rises (`no-match` and the
derived low are what a record carries when nothing answered). The *count* of issues barely
moves; *which* issue is reported moves a great deal.

### 7.3 Date dependence

**No flag reads the clock.** `grep -n "date.today\|datetime.now\|utcnow\|time.time()"` over
`enrichment/flags.py`, `enrichment/issue_detection.py`, `enrichment/consistency.py`,
`enrichment/liveness.py`, `enrichment/wikidata.py` and `api/routes.py` returns nothing.

Two near-misses worth naming so a reviewer does not go looking:

* `entity-superseded` **renders** a date into its reason when only `P576` is present —
  `f"dissolved {self.item.dissolved}"` (enrichment/wikidata.py:338). That is a value read from
  Wikidata, not a comparison against today; the flag fires on the *presence* of the claim
  (`superseded` = `bool(self.dissolved or self.replaced_by)`, :265-266), never on its age.
* The retired provenance scheme carried `extracted:{date}`, which **did** decay — "It made the
  column irreproducible for eleven rows of a hundred before Fix B pinned it to the fetch date"
  (enrichment/confidence.py:22-25). Scheme B removed it from the shipped column; it lives in
  the evidence cache and the trace.

Determinism is measured, not assumed: `logs/runs/determinism_S1.json`, two runs of S1 at
`d3a3cfc` against the same cache — 100 rows compared, 0 rows differing, 0 cell differences,
0 network calls on run 2 (eval/out/RUNS.md:41-52).

---

## 8. KNOWN GAPS

### 8.1 TODO / ⚠ markers in the issue modules

A literal `grep -n "TODO\|⚠\|FIXME\|XXX"` over `enrichment/issue_detection.py`,
`enrichment/flags.py`, `enrichment/dept_block.py`, `enrichment/grounded_resolver.py`,
`api/routes.py` and `enrichment/confidence.py` returns **nothing**. The modules carry no TODO
markers. What they carry instead is `status`/`reason` fields on catalogue entries and long
justification comments, which is where the open decisions actually live:

| Gap | Recorded at | State |
|---|---|---|
| `G3-ADDR-012` emitted but absent from Catalogue v2 | issue_detection.py:256-265 | "Left emitting, unchanged, pending that decision — see docs/thesis/00_OPEN_ITEMS.md" |
| `G1-ADDR-009` cannot be expressed deterministically | issue_detection.py:217-227 | Declared `ndd`; needs the LLM residual classifier, which `/issues` may not call |
| `G4-NAME-015` name diverges from v2 | issue_detection.py:270-273 | "the divergence is reported for a Notion correction" |
| G5 misspellings permanently out of scope | issue_detection.py:72-86 | Declared limit, with the reason |
| `_POSTAL_FORMATS` covers three countries | issue_detection.py:616-622 | "Adding a country here is what converts its rows from unchecked to checked" |
| `_ev_low_conf_unchanged` marker still read for Name 3..5 | flags.py:1290-1303 | "When Name 3..5 enter provenance scope, this half deletes itself and nothing else changes" |
| `_evidence_free_fields` two-branch shape | flags.py:574-576 | "Extending the scope removes the second branch" |

### 8.2 The follow-on fix for the G8 over-emission

See §4.4. Not implemented, not ticketed anywhere in the tree, and not present in
`docs/thesis/00_OPEN_ITEMS.md`.

### 8.3 Open items touching flags

`docs/thesis/00_OPEN_ITEMS.md`, filtered to entries that touch the flag or issue path:

| # | Line | State | Item |
|---|---|---|---|
| — | :36-65 | corrected | D-5 "Flag rather than infer" cited `enrichment/confidence.py:51-55` for a four-layer flagging realisation; a full-repo search matches only the two definition sites |
| 129 | :532 | `⚠ UNVERIFIED (dead code)` | `determine_enrichment_status` / `should_flag_for_review` documented as pipeline behaviour and **have no caller** — decide: wire them, or delete `enrichment/confidence.py` and re-cite D-5 to the inline sites |
| 67 | :441 | `⚠ UNVERIFIED` | Whether `_audit_upload` invokes the full pipeline, i.e. whether `/issues/compare` makes external calls. **Settled by §7.1 above: it calls `detect_issues` only.** |
| 59 | :424 | `⚠ RATIONALE NOT IN REPO` | Why address validation and the `/issues` call are separate ADF pipelines |
| 148 | :551 | `⚠ NO FIXTURE COVERAGE` | The ST2 unit-phrase guard firing to `None` on its True branches — a Name 2 that is both a unit phrase and matches the research-institution regex, asserting ST2 is `None` **and the record is flagged** |
| 158 | :561 | `⚠ NO FIXTURE COVERAGE` | Float-typed record-id cells in the `/issues/compare` join (`"1001"` vs `"1001.0"`) |
| 170 | :578 | `⚠ NO FIXTURE COVERAGE` → **closed** | `G1-NAME-001` reachable with no repository record satisfying it |
| 171 | :579 | `⚠ NO FIXTURE COVERAGE` → **closed** | `G3-ADDR-013` reachable with no repository record satisfying it |
| — | :129-141 | corrected | An earlier count claim ("34 of the 36") disagreed with the source; the current counts are asserted by test |

Items 170 and 171 are closed by `tests/test_issue_catalogue_coverage.py` (its docstring says
so at :9-11); the entries in `00_OPEN_ITEMS.md` have not been struck.

Also open, and material to §6.3: whether DATAshaper passes unmapped columns through
(eval/out/RUNS.md:314-316); whether its column-typed validation accepts 65 columns
(README.md:3707); the `enrichment_status` severity change moving 30 chemspeed records from
Warning to Info, "the one item in these three fixes worth confirming with Bernd/Bert before
rollout" (README.md:3491).

### 8.4 Tests that pin flag behaviour

All four suites pass at `HEAD` — `513 passed` in 1.71s.

| Suite | Tests | Purpose |
|---|---|---|
| `tests/test_issue_detection.py` | 179 | The detector, code by code |
| `tests/test_flags.py` | 242 | `compute_flags` / `render` / `retract` / `raise_after` |
| `tests/test_flag_issue_alignment.py` | 13 | The `FLAG_CODE_ISSUES` join |
| `tests/test_issue_catalogue_coverage.py` | 79 | Fixture coverage as data (38 codes × positive + near-miss, plus 3 invariants) |

Selected, with intent:

| Test | `file:line` | Pins |
|---|---|---|
| `test_docstring_counts_match_the_catalogue` | test_issue_detection.py:145 | Adding or retiring a code fails the suite until the module docstring is updated |
| `test_group_is_an_attribute_not_a_prefix` | :83 | `code.split("-")[0]` is a bug; G6 holds four `G2-` codes |
| `test_mandatory_maps_to_datashaper_severity` | :103 | `mandatory` → Error/Warning |
| `test_reduction_groups_exclude_g6_g7_and_g8` | :137 | The reduction metric is G1–G5 only |
| `test_withdrawn_codes_are_declared_but_never_emitted` | :74 | The audit trail for the two struck codes |
| `test_the_eleven_ds_only_live_codes_are_exactly_catalogue_v2s` | :756 | The DS/API origin split |
| `test_ds_only_codes_are_suppressed_for_a_datashaper_facing_feed` | :746 | `origins=("API","BOTH")` yields a non-duplicating feed |
| `test_required_field_rules_all_have_a_reachable_column_mapping` | :774 | The failure mode that let G2-VAL-004 sit dark |
| `test_g2_val_004_fires_for_a_blank_region_whatever_the_country` | :331 | The removed US-only predicate stays removed |
| `test_flag_derived_codes_absent_from_a_raw_input_audit` | :666 | G6/G7/G8 cannot fire on raw input |
| `test_several_flags_mapping_to_one_issue_raise_it_once` | :658 | Many-to-one is idempotent |
| `test_an_unmapped_flag_code_raises_nothing` | :681 | The five unmapped stay unmapped |
| `test_g8_covers_the_retired_low_confidence_token` | :713 | The derived-low → G8 join |
| `test_provenance_is_low_reads_the_grammar` | :709 | `web:acme.com:low` is not split naively |
| `TestFlagFieldsStayConsistent::test_boolean_matches_the_codes_and_the_derived_low` | test_flags.py:153 | The four columns can never disagree |
| `TestAdvisoryCodesStateWithoutQueueing::test_a_populated_reason_with_the_boolean_false_is_a_valid_row` | :612 | "this is now a state DATAshaper must expect" (:614) |
| `TestRenderAndRetract::test_the_derived_code_cannot_be_raised_as_a_code` | :804 | The `ValueError` guard |
| `TestRenderAndRetract::test_retract_keeps_the_wording_of_the_codes_it_keeps` | :782 | A withdrawal does not silently re-word survivors |
| `TestUndecidableAlwaysRaises` | :1170 | The `undecidable` rule does not share the loop's guards |
| `TestAnAdminDeskInName2NeedsNoVerification::test_the_low_confidence_half_is_cleared_too` | :921 | The exemption clears **both** name2 doubts |
| `TestEveryFlagCodeIsAccountedFor::test_the_vocabulary_is_partitioned` | test_flag_issue_alignment.py:367 | Mapped ∪ unmapped = `ALL_CODES`, disjointly |
| `TestTheDerivedLowReachesTheCatalogue::test_an_admin_desk_left_as_supplied_carries_neither` | :203 | The exemption survives the join to G8 |
| `test_every_emittable_code_has_a_fixture` | test_issue_catalogue_coverage.py:55 | Every `EMITTED_CODES` member has a positive **and** a near-miss fixture |

### 8.5 Detectors with no test

**Every emittable code has both a positive and a negative fixture, by construction** —
`test_every_emittable_code_has_a_fixture` asserts `covered == set(EMITTED_CODES)` and
`test_negative_case_does_not_raise_its_code` (:80) runs the near-miss. So no *code* is
untested.

Three things in the flag path have no direct test:

1. **`api/routes._flag_codes` Path 2 against a `Flag Codes` column that disagrees with the
   provenance column.** `tests/test_routes.py` has no case where both are present and the
   pipeline withheld the token — which is exactly the §4.3 over-emission. This is the gap that
   let the divergence ship.
2. **`_validate_required_field_mapping`'s warning branch** (issue_detection.py:439-448) — the
   test asserts the table is *intact* (`:774`), not that a broken table warns.
3. **Header-order dependence of Path 2** (§4.1 mechanic 1) — no test fixes the relative order
   of `Name 1 Provenance` and `Name 2 Provenance`, so a column reorder in
   `RESPONSE_COLUMNS` would silently change which slot's doubt is reported.

Repository-wide, `tests/KNOWN_FAILURES.md` pins eight pre-existing failures as a **set**
rather than a count (eval/out/RUNS.md:379-397); none is in the four suites above.

---

## 9. RECENT CHANGES

`git log --oneline -30 -- enrichment/issue_detection.py enrichment/flags.py
enrichment/dept_block.py api/routes.py`, at `HEAD = 27768b7`:

```
6cf4639 Implement customer grain consolidation endpoints for SAP extracts
21f0a4f Say which configuration produced a dedup workbook
60e0b51 A link is not a merge, and an id conflict is not a reason to lose the pair
8868908 Enhance deduplication logic with v2 features and address handling
28eeef1 Refactor flag handling and enhance provenance logic for low-confidence records
96dd528 Update README and codebase to enhance issue detection and flag handling
64076cd Enhance address processing and casing logic for acronyms and street identification
48e7e83 Enhance address processing and flag logic for relocated slots
d4977ac Foote package: comparisons run against what the slot holds, and relocated slots are flagged
f3588c8 Implement department split canonicalization and enhance documentation
299784a Enhance enrichment result model and batch consensus logic
f895753 Refine accounts payable normalization and enhance test coverage
600d729 Issues endpoiint added
20b3050 Route every name candidate through one write gate
0c057bc Suppress specific G6 and G7 issue codes from the `/issues` audit column
9da5ae8 Ship unverified domains and split advisory codes out of the review queue
da587ee Enhance flagging logic for unverifiable names in enrichment pipeline
31e2a4d Implement UC 0 name block merging and repacking logic
9d15193 Fixes
55b9e33 Changes
75cfcad Web domain between ROR and LLM
d4fc469 Enhance address processing and issue detection for German street types
8a68c77 Enhance batch consensus logic to manage flag retraction
59d3e4d Implement per-field provenance tracking and enhance evidence handling
8d5f5f9 Update issue detection and reporting for enriched data
b8ad102 Enhance name handling by adding support for five name slots
5e423c2 Fix 8: flag model redesign
4bc0882 3.4 Remove the unreachable issue code G2-CONTACT-008
7399df8 3.3 Bind the LEI column on the dedup file upload path
8f2bb6b Align /api/dedup/score JSON with the score/file column contract
```

### Flag changes since the last evaluation run

The current evaluation artefacts were produced at **`327ee53`** (eval/out/RUNS.md:1-4, with
`f57782f` and the bare `d3a3cfc` artefacts marked SUPERSEDED). `327ee53` is not in the log
above because it touched none of these four files; the flag-bearing commits bracketing it are
`d4977ac` / `48e7e83` (before) and `28eeef1` (after).

Two changes land after the measured run, and both bear on this dossier. `28eeef1` ("Refactor
flag handling and enhance provenance logic for low-confidence records") **re-emits the
`low-confidence-unchanged` token** — the rationale is at enrichment/flags.py:44-55 and
:100-110: a consumer reading `flag_codes` cannot see a confidence column it was not given, and
`G8-VERIFY-001` is defined over that vocabulary. The derivation rule stays closed (a tier
passing the code still gets a `ValueError`), and the three shipped columns do not move —
README.md:1689 records `Flag Reason`, `Flag for Review` and `Flagged Fields` as byte-identical
either side of the change on all four demo strata. **The direct consequence for §4.3 is that
every enriched workbook in this tree predates the token's return**, which is why all their G8
comes from the provenance fallback and why the fallback's divergence is currently invisible in
any measured number.

Earlier in the same window, `d4977ac` / `48e7e83` introduced `relocated-unverified`, which
fires on 20 rows across the 399 measured and whose survival the `327ee53` origin invariant
protects — before that fix "seven records lost it while their data stayed byte-identical"
(eval/out/RUNS.md:308-311). `9da5ae8` split `ADVISORY_CODES` out of the review queue, creating
the state a populated `Flag Reason` beside a false `Flag for Review` — "this is now a state
DATAshaper must expect" (tests/test_flags.py:614). `0c057bc` created
`_ISSUES_SUPPRESSED_CODES`. `5e423c2` (Fix 8) is the redesign the whole flag module exists to
implement: before it, the flag answered "which tier ran?" and fired on 47 of 50 demo records
(enrichment/flags.py:4-7).

**Everything in §4.3 and §7 above is measured against the `327ee53` artefacts and the in-tree
thesis export, not against `HEAD`.** No enrichment run exists at `HEAD`.

---

## 10. WORKED EXAMPLES

All four rows are verbatim from `eval/out/327ee53/`. `Issues` is what `POST /issues` writes
for that row (`_ISSUES_SUPPRESSED_CODES` applied).

### 10.1 Clean — S1 / customer 13213370

| Column | Value |
|---|---|
| Name 1 | `Princeton University` |
| Name 2 | `Lewis-Sigler Institute for Integrative` |
| Name 3 | `Genomics` |
| Street 1 | `S Dr` |
| House Number | *(blank)* |
| Postal Code / City / Region / Country | `08544` / `Princeton` / `NJ` / `US` |
| Domain | `princeton.edu` |
| ROR ID | `https://ror.org/00hx57361` |
| Name 1 Provenance | `ror:verified` |
| Name 2 Provenance | `llm:provisional` |
| Flag for Review | `False` |
| Flag Codes | *(empty)* |
| Flag Reason | *(empty)* |

**Detector verdicts.** G1-ADDR-001 does not fire: `is_blank(house_number)` is True, so the
precondition holds, but `_looks_like_street("S Dr")` is False — no house number in the line.
G1-NAME-001 does not fire: `_NAME_CONTINUATION_RE` does not match `Genomics` (capitalised, not
a connector). G2-NAME-012 does not fire — Name 2 is populated. G5-NAME-001/-002 do not fire —
no abbreviation token, no dotted acronym. `_detect_enrichment_flags` receives `flag_codes=[]`
(the `Flag Codes` column exists and is empty; the provenance loop coerces `None`→`[]` at
api/routes.py:274-275 and neither column reads `low`). `_detect_verification` receives `False`.

**`Flag Codes` token string:** *(empty)* · **G-group:** none · **`Issues`:** *(empty)*
· **`Issues` prose:** none.

**What DATAshaper would do.** Nothing. `Flag for Review` merges as `0`
(sql/usp_merge_legacy_enriched.sql), `Flag Reason` as NULL, and the record's
`enrichment_status` routes it to no-issue or Info (README.md:3484-3487). It loads.

### 10.2 A single G-group flag — S4 / customer 13141073

`Issues = G7-CONFIRM-001` · `Flag Codes = dept-via-lab`.

**Trace.** Tier 2B read the lab's own page and inferred the parent department, leaving
`_ev_dept_via_lab` (enrichment/orchestrator.py:8729). `compute_flags` raises `DEPT_VIA_LAB`
scoped to `("name2", demoted_to)` (enrichment/flags.py:1321-1322, with `demoted_to` defaulting
to `"name3"` at :1320). `render` places it at its `_CODE_ORDER` position (:276) and emits the
prose from `_REASONS[DEPT_VIA_LAB]` (:300-304): *"parent department was inferred from the lab's
own page, not read from a stated department — confirm the department is the right parent for
this lab"*. It is not advisory, so `flag_for_review` is True.

**The audit.** `_flag_codes` returns `["dept-via-lab"]`; `_detect_enrichment_flags` maps it to
`G7-CONFIRM-001` (enrichment/issue_detection.py:1114). `_detect_verification` receives `True`
and raises `G7-VERIFY-001`, which `_ISSUES_SUPPRESSED_CODES` (api/routes.py:834-836) then
withholds from the column — it says only *that* the record was flagged, which `Flag for Review`
already carries. No content detector fires. **G-group: G7.**

**What DATAshaper would do.** `Flag for Review = 1` and the reason prose reach the Legacy
table and route the record to a steward through the Category dropdown
(enrichment/issue_detection.py:297-299). Severity is Warning — `G7-CONFIRM-001` is
`mandatory=False` (:306), so the record **loads**. The `dept-via-lab` token itself does not
reach the database: `Flag Codes` is unbound (§6.3).

### 10.3 Three-plus flags including a G8 — S4 / customer 13343777

| Column | Value |
|---|---|
| Name 1 | `UT Southwestern Medical Center University Hospitals` |
| Name 2 | `Zale Receiving - Labs` |
| Street 1 / House Number | `Harry Hines Blvd` / `5151` |
| Postal Code / City / Region | `75390` / `Dallas` / `TX` |
| Search Term 1 / 2 | `UT SOUTHWESTERN` / `ZALE` |
| Domain | `utswmed.org` |
| ROR ID / LEI ID | *(both blank)* |
| Name 1 Provenance | `llm:provisional` |
| Name 2 Provenance | `input:low` |
| Domain Provenance | `web:utswmed.org:low` |
| Flag for Review | `True` |
| Flag Codes | `relocated-unverified; domain-unverified` |
| Flagged Fields | `name2; domain` |
| Flag Reason | `Name 2: moved here from the address block — confirm it names this record's unit or site; Domain: the domain shown (utswmed.org) was found on the web but nothing independently tied it to this organisation — confirm it — its page states 'The University of Texas Southwestern Medical Center' in Dallas` |

**Detector verdicts, one by one.**

| Detector | Verdict |
|---|---|
| `_detect_wrong_field` | G1-ADDR-001 **no** — `house_number` is `5151`, so the `is_blank` precondition fails. Nothing else matches. |
| `_detect_missing` | G2-VAL-003/-006 fire (Tax Jurisdiction, Language Key blank) but are **suppressed** from the column (api/routes.py:834-836). G2-NAME-012 no — Name 2 populated. |
| `_detect_duplicate` | nothing — one street, no PO box, no DBA, no duplicate slot. |
| `_detect_format` | nothing — 71 chars combined, `75390` matches `^\d{5}(?:-\d{4})?$`, `US` is canonical ISO. |
| `_detect_naming` | **G5-NAME-002** — `_ABBREV_TOKEN_RE` matches `Labs` in `Zale Receiving - Labs` (enrichment/issue_detection.py:485). Name 1 has no abbreviation token, so `-001` does not fire; attribution is by slot (:1020-1022). |
| `_detect_enrichment_flags` | `relocated-unverified` → **G7-CONFIRM-001** (:1116); `domain-unverified` → **G7-CONFIRM-001** (:1112) — same code, raised once. Then the **provenance fallback**: `Name 1 Provenance` is `llm:provisional`, not low, so the loop continues to `Name 2 Provenance` = `input:low`; `_name2_has_no_canonical_form` is False (`Zale Receiving - Labs` is a real unit, not an admin desk); `low-confidence-unchanged` is appended → **G8-VERIFY-001** (:1118). |
| `_detect_verification` | `True` → G7-VERIFY-001, suppressed from the column. |

**`Flag Codes` token string as shipped:** `relocated-unverified; domain-unverified` —
`_CODE_ORDER` puts `RELOCATED_UNVERIFIED` (position 8) before `DOMAIN_UNVERIFIED` (position
17), which is why the Name 2 clause leads the reason.

**`Flag Codes` as the audit reconstructs it:** `["relocated-unverified", "domain-unverified",
"low-confidence-unchanged"]` — the third element exists only in the audit's memory, appended
by api/routes.py:288.

**`Issues`:** `G5-NAME-002; G7-CONFIRM-001; G8-VERIFY-001` (catalogue order,
enrichment/issue_detection.py:1287). **G-groups: G5, G7, G8.**

**The G8 on this row is one of the §4.3 population.** The S4 raw input
(`~/Downloads/demo_S4_hospital_health_100_v1 (1).xlsx`, per eval/out/RUNS.md:32) holds:

```
Name 1       = 'UTSW Medical Center Univ Hosp'
Name 2       = (blank)
Street 1     = 'HARRY HINES BLVD'
Street 2     = 'Zale Receiving - Labs'
House Number = '5151'
```

`Name 2` arrived **empty** and shipped `Zale Receiving - Labs` — preprocessing lifted it out of
**Street 2**, which is literally what `relocated-unverified` says on the same row. `compute_flags` therefore
withheld `low-confidence-unchanged`: `_still_as_supplied("name2")` is False because
`name2_changed` is True (enrichment/flags.py:1409-1412). The provenance column still reads
`input:low` — correctly, since `input` is the *source* — and the audit re-raises the doubt.
The row is self-contradicting as shipped: `Flag Reason` says the value was *moved here*, and
the `Issues` column says it was *left exactly as supplied*.

**What DATAshaper would do.** `Flag for Review = 1` and the two-clause reason merge into
Legacy and queue the record. All three issue codes are `mandatory=False`, so the severity is
Warning and the record **loads** — G5-NAME-002 (:280), G7-CONFIRM-001 (:306), G8-VERIFY-001
(:311). The steward is asked three things, of which the third is spurious: confirm the unit
name's official form, confirm the two moved/unverified values, and establish a Name 2 that was
in fact just written.

### 10.4 Counter-example — S5 / customer 13333689, where the G8 is correct

`Name 1 = CALM/UCSD`, `Name 1 Provenance = input:low`, `Flag Codes = domain-unverified`,
`Flag Reason = "Name 1: left exactly as supplied — the canonical form could not be established
with enough confidence to rewrite it; confirm the value is correct; Domain: the domain shown
(ucsd.edu) …"`.

`Issues = G1-ADDR-001; G7-CONFIRM-001; G8-VERIFY-001`. G1-ADDR-001 fires because
`house_number` is blank and `_looks_like_street("10300 Campus Point Dr")` is True. The G8 here
is **right**, and the row proves it from its own `Flag Reason`: the pipeline's prose already
carries the `low-confidence-unchanged` clause (enrichment/flags.py:291-295) even though the
token is absent from `Flag Codes` — this export predates `28eeef1`. That is the vintage the
provenance fallback was built for, working exactly as designed. Contrast it with §10.3, where
the same fallback produces a claim the pipeline's own reason text contradicts.
