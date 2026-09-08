Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 15

# Pass 15 — Issues dossier

Scope: the Issue Catalogue as the code declares and emits it, and the second, disjoint issue
vocabulary the deduplication scorer emits. Implementation:
`enrichment/issue_detection.py` (1710 lines), consumed by `POST /issues`
(`api/routes.py:837`), `POST /issues/json` (`api/routes.py:883`) and
`POST /issues/compare` (`api/routes.py:916`); and `dedup/scoring.py:458–566`, consumed by
`POST /api/dedup/score` (`api/routes.py:1437`) and `POST /api/dedup/score/file`
(`api/routes.py:1518`).

Detection is pure and deterministic — regex and string checks only, no enrichment, no LLM call,
no network I/O (`enrichment/issue_detection.py:9–12`). The same rule set therefore runs over a
raw input file and over a post-pipeline output file, which is what makes a before/after count
meaningful.

Working-tree note (Rule 1): `git status --porcelain` returns 45 entries at this commit and every
one is a file under `docs/thesis/` produced by this documentation run. No source file, SQL file,
ADF export or fixture is modified.

---

## 15.1 Catalogue shape

`ISSUE_CATALOGUE` (`enrichment/issue_detection.py:260–423`) is an explicit `dict[str,
IssueDefinition]` in catalogue order. `IssueDefinition` (`:224–246`) carries `code`, `group`,
`name`, `field`, `mandatory`, `origin`, `raised`, `remedy`, `status` and `reason`.

| Attribute | Values | Meaning | Declared |
|---|---|---|---|
| `group` | `G1`…`G7` | Catalogue v2 group. **An attribute, not the code prefix** — `G2-VAL-003` and `G2-VAL-006` are declared `group="G6"` (`:380`, `:385`). Read `.group` or `issue_group()` (`:455–461`). | `:229` |
| `mandatory` | `True` / `False` | DATAshaper severity: `True` blocks the SAP load (*Error*), `False` is a *Warning*. Exposed as `IssueDefinition.severity` (`:243–246`). | `:232` |
| `origin` | `DS` / `API` / `BOTH` | Which side raises the rule. A DS-origin rule raised here produces a duplicate issue in DATAshaper. | `:233` |
| `raised` | `raw` / `enriched` / `both` | Which file the code can fire on. `None` only on a withdrawn entry. | `:238` |
| `remedy` | `rule` / `enrichment` / `steward` | Who can fix it. `None` only on a withdrawn entry. | `:239` |
| `status` | `live` / `withdrawn` / `ndd` / `unlisted` | `live` = emitted; `withdrawn` = struck through in v2, declared for the audit trail, never emitted; `ndd` = not deterministically detectable; `unlisted` = emitted but absent from v2. | `:240` |
| `reason` | free text | Required for every non-`live` status. | `:241` |

Group names (`:33–35`): G1 Data in Wrong Field · G2 Missing Required Data · G3 Duplicate or
Conflicting Data · G4 Invalid Format or Length · G5 Non-Standard Naming · G6 Enriched — Confirm ·
G7 Left Unchanged — Verify.

`QUALITY_GROUPS = ("G1", "G2", "G3", "G4", "G5")` (`:432`); `VERIFICATION_GROUPS = ("G6", "G7")`
(`:446`). G6 and G7 are raised **by** enrichment, are reported separately and never enter a
reduction figure (`:441–445`). The group set was renumbered on 2026-09-06 — the old G6 ("Not
Resolvable by Enrichment") was dissolved, old G7 became G6 and old G8 became G7 (`:259`).

`REDUCIBLE_GROUPS` and `PERSISTENT_GROUP` were deleted in the same rework and their absence is
pinned by `tests/test_issue_detection.py:174–175`. The before/after segmentation now reads
`raised` and `remedy` per code — see §15.6.

---

## 15.2 Census

Derived by `scripts/issue_catalogue_census.py` (invocation and verbatim output in §15.11.A).

| Figure | Value | Definition |
|---|---|---|
| declared | **43** | entries in `ISSUE_CATALOGUE` |
| live | **33** | `status="live"` |
| unlisted | **0** | `status="unlisted"` — emitted here, absent from Catalogue v2 |
| withdrawn | **10** | `status="withdrawn"` |
| not deterministically detectable (`ndd`) | **0** | `status="ndd"` |
| deterministically emitted | **33** | `EMITTED_CODES` (`:426–428`) = live + unlisted |
| live quality codes (G1–G5) | **31** | `status="live"` and `group in QUALITY_GROUPS` |
| fixture-covered | **33 of 33** | positive + negative case in `tests/fixtures/issue_catalogue_coverage.json` |

Emitted equals live: nothing withdrawn is reachable and nothing live is unreachable
(`enrichment/issue_detection.py:53–55`).

**Declared entries per group** — counted on the declared `group` attribute, which is not the
code prefix:

| G1 | G2 | G3 | G4 | G5 | G6 | G7 | total |
|---|---|---|---|---|---|---|---|
| 12 | 9 | 9 | 5 | 2 | 4 | 2 | 43 |

**Live entries per group**, with the `raised` and `remedy` split inside each group:

| Group | Live | `raised` | `remedy` |
|---|---|---|---|
| G1 | 10 | raw 9, both 1 | rule 8, enrichment 1, steward 1 |
| G2 | 7 | raw 7 | rule 2, enrichment 1, steward 4 |
| G3 | 9 | raw 6, both 2, enriched 1 | rule 3, steward 6 |
| G4 | 3 | raw 2, both 1 | rule 1, steward 2 |
| G5 | 2 | raw 2 | enrichment 2 |
| G6 | 1 | enriched 1 | steward 1 |
| G7 | 1 | enriched 1 | steward 1 |
| **total** | **33** | raw 26, both 4, enriched 3 | rule 14, enrichment 4, steward 15 |

**Origin**, live entries: API 18, DS 8, BOTH 7. Restricted to live quality codes (G1–G5): API 16,
DS 8, BOTH 7. Restricted further to live quality codes that can fire on a raw file
(`raised != "enriched"`, i.e. excluding `G3-NAME-006`): 30 codes — DS 8, API 15, BOTH 7, pinned by
`tests/test_issue_detection.py:152–159`.

**Withdrawn per group**: G1 2, G2 2, G3 0, G4 2, G5 0, G6 3, G7 1.

---

## 15.3 Per-code table — the 33 live codes

`raised` reads: `raw` = can fire on a raw input file; `enriched` = only after the pipeline has
run; `both` = either. `remedy` reads: `rule` = a deterministic rule can fix it; `enrichment` =
the pipeline can fix it; `steward` = no automated path, a human decides. "In reduction set" is
`remedy in ("rule", "enrichment")` — see §15.5 for why that set holds 18 codes and not 19.

| Code | Group | Name | Origin | Raised | Remedy | Mandatory? | In reduction set? | Detection site |
|---|---|---|---|---|---|---|---|---|
| `G1-CROSS-001` | G1 | Address Content in Name Field | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1054–1057` |
| `G1-CROSS-002` | G1 | Org Name in Address Field | API | raw | enrichment | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1062–1071` |
| `G1-CROSS-003` | G1 | Contact Information in Wrong Field | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1075–1083` |
| `G1-ADDR-001` | G1 | House Number Embedded in Street | DS | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1087–1091` |
| `G1-ADDR-003` | G1 | Sub-location Embedded in Street | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1094–1100` |
| `G1-ADDR-004` | G1 | PO Box Embedded in Street | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1103–1106` |
| `G1-ADDR-006` | G1 | Mail Code in Street Field | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1124–1135` |
| `G1-ADDR-011` | G1 | Department Label in Street Field | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1138–1141` |
| `G1-NAME-004` | G1 | Empty field in between populated name fields | API | raw | rule | no | **yes** | `_detect_wrong_field` `enrichment/issue_detection.py:1150–1158` |
| `G1-NAME-013` | G1 | SAP Internal Code in Name Field | BOTH | both | steward | no | no | `_detect_wrong_field` `enrichment/issue_detection.py:1161–1164`; `FLAG_CODE_ISSUES` `:1527` |
| `G2-VAL-001` | G2 | Name 1 Missing | DS | raw | steward | **yes** | no | `_detect_missing` `enrichment/issue_detection.py:1189–1215` ← `_REQUIRED_FIELD_CODES` `:497` |
| `G2-VAL-002` | G2 | Postal Code Missing | DS | raw | steward | **yes** | no | `_detect_missing` `enrichment/issue_detection.py:1189–1215` ← `_REQUIRED_FIELD_CODES` `:498` |
| `G2-VAL-004` | G2 | Region Missing | DS | raw | steward | **yes** | no | `_detect_missing` `enrichment/issue_detection.py:1189–1215` ← `_REQUIRED_FIELD_CODES` `:499` |
| `G2-VAL-007` | G2 | Search Term 1 Missing | DS | raw | rule | **yes** | **yes** | `_detect_missing` `enrichment/issue_detection.py:1189–1215` ← `_REQUIRED_FIELD_CODES` `:500` |
| `G2-VAL-008` | G2 | Country Missing | DS | raw | rule | **yes** | **yes** | `_detect_missing` `enrichment/issue_detection.py:1189–1215` ← `_REQUIRED_FIELD_CODES` `:501` |
| `G2-NAME-009` | G2 | Lab Without Department | API | raw | enrichment | no | **yes** | `_detect_missing` `enrichment/issue_detection.py:1258–1267` |
| `G2-NAME-012` | G2 | Research Institution Missing Department (Name 2 blank or holds only an administrative desk) | BOTH | raw | steward | no | no | `_detect_missing` `enrichment/issue_detection.py:1250–1254` |
| `G3-NAME-003` | G3 | DBA Pattern in Name Field | BOTH | raw | rule | no | **yes** | `_detect_duplicate` `enrichment/issue_detection.py:1287–1290` |
| `G3-NAME-005` | G3 | Duplicate Name Across Fields | API | raw | rule | no | **yes** | `_detect_duplicate` `enrichment/issue_detection.py:1294–1298` |
| `G3-NAME-006` | G3 | Site Qualifier in Name Conflicts With Address | API | enriched | steward | no | no | `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1530` **only** |
| `G3-ADDR-005` | G3 | Multiple PO Boxes on Record | API | raw | steward | no | no | `_detect_duplicate` `enrichment/issue_detection.py:1301–1309` |
| `G3-ADDR-012` | G3 | Duplicate Street Across Fields | API | raw | rule | no | **yes** | `_detect_duplicate` `enrichment/issue_detection.py:1317–1325` |
| `G3-ADDR-013` | G3 | Two Distinct Street Addresses on Record | API | raw | steward | no | no | `_detect_duplicate` `enrichment/issue_detection.py:1341–1351` |
| `G3-ADDR-014` | G3 | PO Box and Street Both Present | BOTH | raw | steward | no | no | `_detect_duplicate` `enrichment/issue_detection.py:1354–1355` |
| `G3-CONTACT-007` | G3 | Multiple Contacts on Record | BOTH | both | steward | no | no | `_detect_duplicate` `enrichment/issue_detection.py:1358–1359`; `FLAG_CODE_ISSUES` `:1528` |
| `G3-CONTACT-010` | G3 | Multiple Email Addresses on Record | BOTH | both | steward | no | no | `_detect_duplicate` `enrichment/issue_detection.py:1372–1379`; `FLAG_CODE_ISSUES` `:1529` |
| `G4-NAME-015` | G4 | Name Overflow Beyond the Name Block | BOTH | both | steward | **yes** | no | `_detect_format` `enrichment/issue_detection.py:1388–1390`; `FLAG_CODE_ISSUES` `:1554` |
| `G4-ADDR-026` | G4 | Postal Code Format Invalid | DS | raw | steward | no | no | `_detect_format` `enrichment/issue_detection.py:1393–1397` |
| `G4-ADDR-027` | G4 | Country Code Not ISO 2-letter | DS | raw | rule | **yes** | **yes** | `_detect_format` `enrichment/issue_detection.py:1400–1404` |
| `G5-NAME-001` | G5 | Organisation Name Not in Official Form | API | raw | enrichment | no | **yes** | `_detect_naming` `enrichment/issue_detection.py:1434–1437` |
| `G5-NAME-002` | G5 | Unit Name Not in Official Form | API | raw | enrichment | no | **yes** | `_detect_naming` `enrichment/issue_detection.py:1442–1447` |
| `G6-CONFIRM-001` | G6 | Enriched Value Requires Confirmation | API | enriched | steward | no | no | `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1535–1539` **only** |
| `G7-UNCHANGED-001` | G7 | Enrichment Left the Value Unestablished | API | enriched | steward | no | no | `FLAG_CODE_ISSUES` `enrichment/issue_detection.py:1556–1558` **only** |

`detect_issues` (`:1654–1710`) runs the five content detectors then the flag detector in fixed
order (`:1700–1705`), accumulates into a `set`, and returns codes in catalogue order (`:1710`).
A record reaching one code by both a content path and a flag path reports it once.

### 15.3.1 The `mandatory` (Error) codes

Seven live codes block the SAP load. Six of the seven are DS-origin.

| Code | Group | Origin | Remedy |
|---|---|---|---|
| `G2-VAL-001` Name 1 Missing | G2 | DS | steward |
| `G2-VAL-002` Postal Code Missing | G2 | DS | steward |
| `G2-VAL-004` Region Missing | G2 | DS | steward |
| `G2-VAL-007` Search Term 1 Missing | G2 | DS | rule |
| `G2-VAL-008` Country Missing | G2 | DS | rule |
| `G4-ADDR-027` Country Code Not ISO 2-letter | G4 | DS | rule |
| `G4-NAME-015` Name Overflow Beyond the Name Block | G4 | BOTH | steward |

Two further declared entries carry `mandatory=True` and are withdrawn — `G2-VAL-003`
(`:380–384`) and `G2-VAL-006` (`:385–389`) — so the declared mandatory count is 9 and the live
count is 7.

### 15.3.2 Column gating on the `G2-VAL-*` family

The five required-field rules fire only when the column exists in the file **and** is blank
(`:1189–1215`). When the column is absent from the file the rule is skipped, so an enriched
export that simply does not carry, say, Postal Code is not reported as missing it
(`:467–473`). `present_fields` is built by `api.routes._present_fields` (`api/routes.py:162–176`)
from the file's header row.

`_REQUIRED_FIELD_CODES` (`:496–502`) carries an optional third element, a per-record predicate.
**No entry carries one at this commit** (`:474–477`). `G2-VAL-004` previously carried
`lambda r: _is_us(r)`; the reasoning for its removal is recorded verbatim at `:479–495` and as
item N-2 of `docs/thesis/08_GAPS.md` §8.9.

`_validate_required_field_mapping` (`:525–554`) runs at import and warns when a rule is keyed on
a field that is not on `EnrichmentRecord` or that carries no input alias — the failure mode where
a rule can never fire and reads as a clean run. Its result is bound to
`_REQUIRED_FIELD_MAPPING_PROBLEMS` (`:557`) so a test can assert the table is intact.

---

## 15.4 Withdrawn codes — the audit trail

Ten declared entries are never emitted. Each carries its `reason`; none carries `raised` or
`remedy`, which is pinned by `tests/test_issue_detection.py:206–208`.

| Code | Declared group | Name | Origin | Declaration | Reason (verbatim) |
|---|---|---|---|---|---|
| `G1-NAME-001` | G1 | Name Overflow Across Fields | API | `enrichment/issue_detection.py:270–274` | withdrawn 2026-09-07; the deterministic heuristic was a proxy for a rule that is LLM-only |
| `G1-ADDR-009` | G1 | Unclassified Residual in Address | API | `:283–287` | ndd, never emitted; residual classifier not called by /issues |
| `G2-CONTACT-008` | G2 | No Contact and No Department | API | `:305–312` | Struck through in Catalogue v2. Its gate was identical to G2-NAME-012's, so it could never carry information the latter had not already reported. |
| `G2-CONTACT-009` | G2 | Department Missing And Enrichable from Contact | API | `:313–322` | Struck through in Catalogue v2. Withdrawing it removed the contact-based (Tier 2A) department recovery path, which is why G2-NAME-012 now sits in G6 — no automated route to a department remains. |
| `G4-ADDR-008` | G4 | Bare Sub-location Marker Without Value | API | `:353–361` | a bare marker cannot be separated from a named building ('810 R L Smith Bldg') without a building-name source; false positives on campus addresses outweigh the true hits |
| `G4-ADDR-025` | G4 | Sub-location Overflow Beyond Street 5 | API | `:362–366` | >4 sub-locations, 0/500 observed; `overflow` covers spill |
| `G2-VAL-003` | G6 | Tax Jurisdiction Missing | DS | `:380–384` | SAP-derived field, 65% blank, not master-data scope |
| `G2-VAL-006` | G6 | Language Missing | DS | `:385–389` | 99% populated, no defect class |
| `G6-RESOLVE-001` | G6 | Enrichment Could Not Resolve the Record | API | `:390–395` | group G6 (not resolvable) dissolved; members re-homed in Step D |
| `G7-VERIFY-001` | G7 | Enriched Record Requires Verification | API | `:410–415` | routing now carried by group membership (already decided 2026-09-02) |

Three of these carry a group label that no longer names the group it names today: `G2-VAL-003`,
`G2-VAL-006` and `G6-RESOLVE-001` were declared under the dissolved "Not Resolvable by
Enrichment" G6 (`:375–379`). `G7-VERIFY-001` keeps its pre-renumber identifier and is not the
same code as today's G7 (`:38–41`). Nothing reads a withdrawn entry's group.

`G4-ADDR-008` and `G4-ADDR-025` are the two codes named in the S1 exemplar's
`expected_issue_codes` mismatch (`docs/thesis/08_GAPS.md` G-44): the workbook expects
`G4-ADDR-025`, the detector emits `G4-ADDR-008` — both withdrawn at this commit, so neither can
be emitted now.

`flag_for_review` is still accepted by `detect_issues` (`:1658`) and still read from the file by
`api.routes._flag_for_review` (`api/routes.py:179–193`), but **no detector reads it**: the one
code it drove, `G7-VERIFY-001`, is withdrawn (`:1674–1678`).

---

## 15.5 The reduction set — 18 codes, not 19

The pass specification asks for an "in 19-code reduction set?" column. **The set holds 18 codes
at this commit.** It is defined as `remedy in ("rule", "enrichment")` (`api/routes.py:559–561`),
written out longhand as `REDUCTION_METRIC_CODES` in `tests/test_issue_detection.py:182–190`, and
its size is asserted at `tests/test_issue_detection.py:218` — `assert len(actual) == 18`. The
reference table is written out rather than derived "so a change to a code's remedy has to be made
here too, in front of a reader, instead of quietly moving a code in or out of the headline
percentage" (`tests/test_issue_detection.py:178–181`).

The 18:

| Group | Codes |
|---|---|
| G1 | `G1-CROSS-001`, `G1-CROSS-002`, `G1-CROSS-003`, `G1-ADDR-001`, `G1-ADDR-003`, `G1-ADDR-004`, `G1-ADDR-006`, `G1-ADDR-011`, `G1-NAME-004` (9) |
| G2 | `G2-VAL-007`, `G2-VAL-008`, `G2-NAME-009` (3) |
| G3 | `G3-NAME-003`, `G3-NAME-005`, `G3-ADDR-012` (3) |
| G4 | `G4-ADDR-027` (1) |
| G5 | `G5-NAME-001`, `G5-NAME-002` (2) |

⚠ The 19th code is `G1-NAME-001` ("Name Overflow Across Fields"), withdrawn on 2026-09-07
(`enrichment/issue_detection.py:270–274`) — one commit before this one. Nineteen is the count as
of the commit before that withdrawal; eighteen is the count at `86d173b`. Any thesis text quoting
19 is quoting a superseded state. ⚠ UNVERIFIED — no artefact in this repository states the
19-code set explicitly, so the identification of `G1-NAME-001` as the removed member is inferred
from its withdrawal date and its `remedy` having been dropped to `None`, not read from a
enumerated 19-code list.

---

## 15.6 What a before/after comparison does with each code

`POST /issues/compare` (`api/routes.py:916–952`) audits two files, joins on record id, and
segments every code into one of three blocks. The segmentation is `segment()`
(`api/routes.py:540–561`) and it reads `raised` and `remedy`, **never the group**:

```
if entry.raised == "enriched":   return "Verification"
if entry.remedy == "steward":    return "Expected to persist"
return "Reduced"
```

| Segment | Membership at `86d173b` | Count | What it means |
|---|---|---|---|
| Verification | `G3-NAME-006`, `G6-CONFIRM-001`, `G7-UNCHANGED-001` | 3 | Raised by enrichment's own output; absent before and present after is the normal case. Never enters a reduction figure. |
| Expected to persist | the 12 remaining `remedy="steward"` codes | 12 | No automated path; supposed to survive to the enriched file and route to a steward. |
| Reduced | the 18 codes of §15.5 | 18 | The headline reduction percentage is computed over these alone. |

The two tests do not overlap: no code with a `rule` or `enrichment` remedy is
`raised="enriched"`, so taking the verification test first cannot pull anything out of the
reduction metric (`api/routes.py:549–552`).

The decision to segment on `raised`/`remedy` rather than on group is recorded as D-43 in
`docs/thesis/09_DECISIONS.md:99` and `:1055–1062`: G2 holds codes a rule fixes (`G2-VAL-007`)
beside codes only a steward can (`G2-VAL-001`), so a single group could no longer name the
persistent set.

⚠ The docstring of `_build_comparison_xlsx` (`api/routes.py:509–523`) still describes the
segmentation under the pre-renumber group scheme — "**Reduced** (G1-G5)", "**Expected to
persist** (G6) — 'Not Resolvable by Enrichment'", "**Verification** (G7)". The code below it does
not read groups at all. Related to `docs/thesis/08_GAPS.md` G-24, which records the same
pre-renumber G6 description in `detect_issues`.

---

## 15.7 The flag-code join — enrichment output into the catalogue

Three of the 33 live codes are derived from enrichment **output** and can never fire on a raw
input file: `G3-NAME-006`, `G6-CONFIRM-001` and `G7-UNCHANGED-001` (`:70–72`). Four more —
`G1-NAME-013`, `G3-CONTACT-007`, `G3-CONTACT-010` and `G4-NAME-015` — have a content detector
*and* a flag path, and are declared `raised="both"` (`:77–80`).

`FLAG_CODE_ISSUES` (`:1515–1559`) is the join between the pipeline's own flag vocabulary
(`enrichment.flags.ALL_CODES`, 17 codes, `enrichment/flags.py:196–214`) and the catalogue. It is
many-to-one on purpose: "three flags that all mean 'no automated path exists, a human must supply
the value' are one queue, not three" (`:1480–1481`).

| Flag code | Catalogue code | Declared |
|---|---|---|
| `opaque-code` | `G1-NAME-013` | `:1527` |
| `multiple-contacts` | `G3-CONTACT-007` | `:1528` |
| `email-conflict` | `G3-CONTACT-010` | `:1529` |
| `name-states-another-site` | `G3-NAME-006` | `:1530` |
| `domain-unverified` | `G6-CONFIRM-001` | `:1535` |
| `unverified-inference` | `G6-CONFIRM-001` | `:1536` |
| `dept-via-lab` | `G6-CONFIRM-001` | `:1537` |
| `dept-via-contact` | `G6-CONFIRM-001` | `:1538` |
| `relocated-unverified` | `G6-CONFIRM-001` | `:1539` |
| `overflow` | `G4-NAME-015` | `:1554` |
| `low-confidence-unchanged` | `G7-UNCHANGED-001` | `:1556` |
| `no-match` | `G7-UNCHANGED-001` | `:1557` |
| `person-unresolved` | `G7-UNCHANGED-001` | `:1558` |

Four flag codes map to **nothing**, declared explicitly in `UNMAPPED_FLAG_CODES` (`:1566–1571`)
"so the pairing is a decision a test can hold the vocabulary to: a code added to
`enrichment.flags.ALL_CODES` is either mapped above or named here, and never silently neither"
(`:1561–1565`).

| Unmapped flag code | Reason, per `:1499–1514` |
|---|---|
| `name3-not-demoted` | already reported as `G4-NAME-015` from the record's own content; raising it again would count one defect twice |
| `registry-location-mismatch` | advisory in the pipeline (`enrichment/flags.py:249–252`) — a registered address differing from an operating site is ordinary and asks nobody for anything |
| `entity-superseded` | asks a business question, not a data-quality one; ships in `Flag Codes` with the successor named in the reason |
| `source-conflict` | same — which of two disagreeing registries is the identity of record is a decision about contracts and open orders |

13 mapped + 4 unmapped = 17 = `len(ALL_CODES)`; no flag code is unaccounted for.

`_detect_enrichment_flags` (`:1623–1647`) is the emission site. `flag_codes` is `None` for a raw
audit — the file has no such column — and nothing is raised from here on one (`:1635–1636`).
`api.routes._flag_codes` (`api/routes.py:196–224`) reads the `Flag Codes` column **as-is** and
consults nothing else; the second path that re-derived `low-confidence-unchanged` from the
`Name 1 / Name 2 Provenance` columns is gone, and the reasoning is recorded at
`api/routes.py:205–219`.

⚠ `provenance_is_low` (`:1598–1620`) remains defined and exported for that retired path. The
module docstring still describes the caller supplying `low-confidence-unchanged` from the
provenance columns (`:1493–1497`, `:1684–1687`), which `api.routes` no longer does.

---

## 15.8 Endpoints and write-back

| Endpoint | Handler | What it returns |
|---|---|---|
| `POST /issues` | `api/routes.py:837–880` | the uploaded sheet echoed with one appended `Issues` column of `"; "`-joined codes (`_build_issues_xlsx` `:430–453`) |
| `POST /issues/json` | `api/routes.py:883–913` | one `{record_id, issues}` entry per record in request order, same detection path (`_audit_rows` `:765–788`) |
| `POST /issues/compare` | `api/routes.py:916–952` | a three-sheet `issue_reduction_report.xlsx` (`_build_comparison_xlsx` `:503`) |

Orchestration. `adf/issues_pipeline.json` is the only exported ADF pipeline that touches issues:
`Lookup1` reads `dp_legacy.[<chrEntity>].Legacy` filtered on `[code] LIKE '<chrGroupCode>\_%'`
(`adf/issues_pipeline.json:6–33`), `Web1` POSTs the rows to `/issues/json`
(`:58`, body at `:64`), and `Merge Back` calls `Mapping.usp_MergeLegacyIssues` (`:89`) passing
`payload` only.

`usp_MergeLegacyIssues` (`sql/usp_merge_legacy_issues.sql`) flattens each record's `issues` array to `"; "`-joined text —
`OPENJSON(@payload, N'$.results')` with `[issues] … N'$."issues"' AS JSON` (`:51–61`) and `MERGE`s it onto `Legacy` keyed on `Customer = record_id`
(`:66`). Its `@target_column` parameter admits `N'Issues Before'` or `N'Issues'` and defaults to
`N'Issues'` (`:5`, `:14–16`). Because the ADF activity passes only `payload`, the parameter
always takes its default: **no exported pipeline ever writes `Issues Before`**, so no ADF path
produces a before/after pair. This is `docs/thesis/08_GAPS.md` G-03; `/issues/compare` is a
two-file multipart endpoint no pipeline calls.

---

## 15.9 Two vocabularies by design — `ISSUE_CATALOGUE` and `DedupIssue`

`ISSUE_CATALOGUE` and `DedupIssue` are separate vocabularies with separate consumers, separate
emission sites and no mapping between them. No code maps a scoring issue onto a catalogue code
and no catalogue code is declared for a scoring defect (`docs/thesis/08_GAPS.md` G-23). A
thesis-level "issue count" is not well defined across the two and the two must never be summed.

| | `ISSUE_CATALOGUE` | `DedupIssue` |
|---|---|---|
| Declared | `enrichment/issue_detection.py:260–423` | `dedup/scoring.py:458–464`; type vocabulary `ISSUE_TYPES` `:434–443` |
| Vocabulary size | 43 declared, 33 emittable | 8 declared types, 7 emitted |
| Unit | one code per **record**, in a set | one object per **row or cluster**, in a list, repeatable |
| Payload | a code string | `{row_id, cluster_id, issue_type, detail}` |
| Emitted by | `detect_issues` `:1654–1710` | `detect_issues` `dedup/scoring.py:485–566` |
| Consumers | `/issues`, `/issues/json`, `/issues/compare` (`api/routes.py:837`, `:883`, `:916`) | `/api/dedup/score` (`api/routes.py:1475`, response field `dedup/scoring.py:472`), `/api/dedup/score/file` (`dedup/scoring_xlsx.py:279`) |
| Output surface | `Issues` column on the echoed sheet; `issues` array in JSON | `Issues` sheet of the scored workbook, columns `row_id, cluster_id, issue_type, detail` (`dedup/scoring_xlsx.py:30–31`, `:328–334`) |
| Write-back | `Mapping.usp_MergeLegacyIssues` → `Legacy.[Issues]` | **none** — `usp_MergeValidationScores` parses `$.rows` only (`sql/usp_merge_validation_scores.sql:49`); no column receives `$.issues` (`docs/thesis/08_GAPS.md` G-30) |
| Determinism | pure, no I/O (`enrichment/issue_detection.py:9–12`) | "Deterministic and offline" (`dedup/scoring.py:495`) |
| Severity model | `mandatory` → DATAshaper Error/Warning (`:243–246`) | none — every type is advisory; the term used is "potential inconsistency" (`dedup/scoring.py:459`, `:491`) |

The two answer different questions. The catalogue answers *what is wrong with this record's
fields, and who fixes it*; `DedupIssue` answers *what about this election is worth a reviewer's
attention*. Neither is a subset of the other, and the field-level defects that would make a
cluster suspicious are not the ones the catalogue names.

### 15.9.1 The `DedupIssue` types

| Type | Level | Predicate | Emission site |
|---|---|---|---|
| `verdict_contradiction` | row | `_reasoning_is_contradiction(row.reasoning)` — the reasoning starts `split:` or contains one of six phrases (`dedup/scoring.py:452–455`, `:475–482`) | `dedup/scoring.py:505–510` |
| `candidate_cap_exceeded` | row, deduplicated per cluster | `"candidate_cap_exceeded" in row.reasoning` (`:447`), one issue per capped block | `:511–520` |
| `count_suppressed_by_recency` | row | `"count suppressed (G1)" in warning` on any result warning (`:450`) | `:523–530` |
| `low_confidence_merge` | cluster, keyed on the proposed winner | `min(member confidences) < threshold` | `:543–548` |
| `all_blocked_cluster` | cluster | every member's normalised status is `blocked` | `:549–553` |
| `tiebreak_decided` | cluster | ≥ 2 members and the top score is shared by ≥ 2 of them | `:554–560` |
| `empty_scoring_payload` | cluster | every member scored 0 — "winner decided by tie-break only" | `:561–565` |
| `missing_building_inconsistency` | — | **declared and never emitted.** Reserved for the upstream building differentiator (Phase 1); no building signal exists at election stage (`dedup/scoring.py:430–433`) | none |

The confidence threshold is `_resolve_confidence_threshold` (`dedup/scoring.py:497`): explicit
argument > env `CONFIDENCE_MERGE_THRESHOLD` > `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` — see
`docs/thesis/14_SCORING_DOSSIER.md` §1.

Both vocabularies happen to use the identifier `detect_issues` and both surface under a column or
sheet named `Issues`. The collision is in the names only; `api/routes.py:52` imports the
deduplication one under the alias `detect_dedup_issues` to keep the two apart in one module.

---

## 15.10 Notion reconciliation

**No Notion export exists in this repository at `86d173b`.** `git ls-files | grep -ci notion`
returns `0` (§15.11.C), confirming `docs/thesis/08_GAPS.md` §8.9. Notion is referenced by URL
only. **Notion is therefore the author's authority for group membership**, and everything in
§15.1–§15.8 above is the code side alone: what `ISSUE_CATALOGUE` declares and what
`detect_issues` emits. A Notion-vs-code reconciliation cannot be performed from repository
evidence.

Three places in the code state a Notion-side fact in their own comments; these are the whole of
the Notion-vs-code evidence available here, and they are recorded in full as N-1…N-3 in
`docs/thesis/08_GAPS.md` §8.9. Their state at this commit:

| # | Item | State |
|---|---|---|
| N-1 | `G4-NAME-015` is declared "Name Overflow Beyond the Name Block" (`:352`) while "v2 names this 'Name Overflow Beyond Name 4'" (`:348–351`); the name block is five slots wide, so the slot-agnostic wording is kept and the divergence "reported for a Notion correction" | **open** — the correction is on the Notion side and cannot be verified here |
| N-2 | The `G2-VAL-004` US-only gate was justified by a sentence that "appears nowhere except the comment that asserted it and the measurement script that copied it — no catalogue extract, no Notion row, no README table states it" (`:479–495`) | **resolved in code** — the predicate is gone (`:499`) |
| N-3 | `G3-ADDR-012` "held this status [`unlisted`] while its absence from the Catalogue v2 G3 table was open; it was resolved live on 2026-09-06" (`:51–52`) | **resolved** — the code is `live` (`:334–337`) and the census reads 0 unlisted |

The direction that cannot be checked from here is the other one: Notion rows with no code
counterpart. Whether the ten local withdrawals of §15.4 are reflected in Notion is
⚠ UNVERIFIED.

### 15.10.1 Discrepancies between the module's own prose and its code

Recorded here per Rule 3; the code wins in each case.

| # | Prose | Code | Consequence |
|---|---|---|---|
| 15-1 | `detect_issues`: "The default emits every origin, including the **11 DS-only codes**" (`:1691`) | DS-origin entries: 8 live, 10 declared (§15.2) | The figure matches neither the live nor the declared count. Any thesis text quoting 11 DS-only codes is wrong at this commit. |
| 15-2 | `detect_issues`: "the before/after reduction narrative is defined over the whole G1-G6 set — of which G6 is entirely DS-origin" (`:1693–1695`) | The one live G6 code, `G6-CONFIRM-001`, is API-origin (`:402–405`); the reduction set is defined on `remedy`, not on groups | Already recorded as `docs/thesis/08_GAPS.md` G-24. |
| 15-3 | `_build_comparison_xlsx`: segments described as "Reduced (G1-G5) / Expected to persist (G6) / Verification (G7)" (`api/routes.py:512–523`) | `segment()` reads `raised` and `remedy` and never reads a group (`api/routes.py:540–561`) | The docstring describes the pre-2026-09-06 scheme. §15.6 states the live rule. |
| 15-4 | Module docstring: the caller supplies `low-confidence-unchanged` from the provenance columns (`:1493–1497`, `:1684–1687`) | `api.routes._flag_codes` reads `Flag Codes` as-is and consults nothing else (`api/routes.py:205–219`) | `provenance_is_low` (`:1598–1620`) has no production caller at this commit. |
| 15-5 | Catalogue comment on `G2-CONTACT-009`: "which is why G2-NAME-012 now sits in **G6**" (`:319–320`) | `G2-NAME-012` is declared `group="G2"` (`:299–304`) | The comment predates the 2026-09-06 dissolution of the old G6 that returned the code to G2 (`:289–292`). |
| 15-6 | Pass specification: "19-code reduction set" | 18 codes; `assert len(actual) == 18` (`tests/test_issue_detection.py:218`) | §15.5. |

The counts quoted in the module docstring (`:46–89`) are otherwise asserted against the source by
`tests/test_issue_detection.py::test_docstring_counts_match_the_catalogue` (`:221`), so adding or
retiring a code fails the suite until the docstring is updated. Items 15-1 and 15-2 are prose the
test does not cover.

### 15.10.2 Declared limits

**A misspelled name is out of scope for this module and always will be** (`:97`). "Universiteat
Stuttgart" is not in official form, so `G5-NAME-001` is the right code for it, and no regex here
will raise it: detecting it means knowing the string is a corruption of a real name, which is
recognition against world knowledge, not pattern matching (`:95–110`). Any deterministic proxy —
edit distance to a dictionary, vowel-cluster heuristics — fires on correctly-spelled names. The
LLM layer owns the class: the pipeline resolves such a name through ROR/GLEIF and rewrites it,
and the before/after comparison is where the correction shows up. The same applies to a name that
is complete and correctly spelled but not the legal one ("Lockheed Martin" for "Lockheed Martin
Corporation").

Several G1-NAME / G2-NAME / G5 rules are inherently semantic and are detected with conservative
deterministic heuristics that err toward precision over recall (`:91–93`).

---

## 15.11 Appendix — commands and verbatim output

### A. Catalogue census

```
$ python3 scripts/issue_catalogue_census.py
====================================================================
Issue Catalogue census — derived from enrichment/issue_detection.py
====================================================================
  declared                     43
  live                         33
  unlisted (not in v2)         0
  deterministically emitted    33   (live + unlisted)
  withdrawn                    10
  not det. detectable          0
  fixture-covered              33 of 33
  live quality codes (G1-G6)   31
  origin of those              {'API': 16, 'DS': 8, 'BOTH': 7}
  entries per group            {'G1': 12, 'G2': 9, 'G3': 9, 'G4': 5, 'G5': 2, 'G6': 4, 'G7': 2}
```

(The label "live quality codes (G1-G6)" is the script's own; the set it counts is
`group in QUALITY_GROUPS`, which is G1–G5 — `scripts/issue_catalogue_census.py:56–59`.)

### B. Derived cross-tabulations

```
$ PYTHONPATH=. python3 -c "
from collections import Counter
from enrichment.issue_detection import ISSUE_CATALOGUE
live=[e for e in ISSUE_CATALOGUE.values() if e.status=='live']
print('live', len(live))
print('raised live', dict(Counter(e.raised for e in live)))
print('remedy live', dict(Counter(e.remedy for e in live)))
print('origin live', dict(Counter(e.origin for e in live)))
print('mandatory live', sorted(e.code for e in live if e.mandatory))
red=sorted(c for c,e in ISSUE_CATALOGUE.items() if e.remedy in ('rule','enrichment'))
print('reduction set size', len(red))
"
live 33
raised live {'raw': 26, 'both': 4, 'enriched': 3}
remedy live {'rule': 14, 'enrichment': 4, 'steward': 15}
origin live {'API': 18, 'DS': 8, 'BOTH': 7}
mandatory live ['G2-VAL-001', 'G2-VAL-002', 'G2-VAL-004', 'G2-VAL-007', 'G2-VAL-008', 'G4-ADDR-027', 'G4-NAME-015']
reduction set size 18
```

### C. Notion export search

```
$ git ls-files | grep -ci notion
0
```

### D. Test result

```
$ python3 -m pytest -q tests/test_issue_detection.py tests/test_issue_catalogue_coverage.py
...........................                                              [100%]
387 passed, 1 warning in 0.34s
```

(The warning is `NotOpenSSLWarning` from `urllib3` at collection time and is unrelated to the
issue modules.)

---

## 15.12 Open items carried forward

| Ref | Item | Owner pass |
|---|---|---|
| 15-1 … 15-6 | Prose-against-code discrepancies, §15.10.1 | Pass 08 `08_GAPS.md` |
| 19 vs 18 reduction codes | §15.5 — the specification's figure is one commit stale | Pass 18 must compute the reduction percentage over the 18 |
| No ADF path writes `Issues Before` | §15.8, G-03 | Pass 18 — the before/after pair has no orchestrated source |
| `DedupIssue` never reaches SQL | §15.9, G-30 | Pass 18 — report the two vocabularies in separate tables, never summed |
| `expected_issue_codes` ≠ emitted `Issues` on every exemplar examined | G-44 | Pass 18 — the workbook column is a design expectation, not ground truth |
| Notion rows with no code counterpart | §15.10 — not answerable from this repository | author |
