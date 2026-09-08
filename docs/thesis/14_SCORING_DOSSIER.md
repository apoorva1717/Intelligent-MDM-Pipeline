Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 14

# Pass 14 — Scoring and golden-record election dossier

Scope: the code path from a clustered row to `score_final`, the eleven `score_*` components,
the five election columns, and the human approval decision. Entry points
`POST /api/dedup/score` (`api/routes.py:1437-1484`), `POST /api/dedup/score/file`
(`api/routes.py:1518-1572`) and `POST /api/dedup/approve` (`api/routes.py:1487-1515`).
Implementation: `dedup/scoring.py` (1372 lines), `dedup/scoring_xlsx.py` (339 lines),
`dedup/weights.json`.

Election contains no LLM call and no network call (`dedup/scoring.py:1-17`). It is arithmetic
over an editable weights table, so a retune re-runs without re-running adjudication.

Exemplars are drawn from `data/eval/stress_200_scored.xlsx` — the run of the 200-record
clustering stress set through `/api/dedup/score/file`. ⚠ The pass specification names
`dedup_STRESS_200_v1_*_scored*.xlsx`; no file of that name exists in the repository. The file
used here is `data/eval/stress_200_scored.xlsx`, identified as the scored artefact by its
`score_*` and election columns and by its `Run` sheet, which records the clustering run that
produced its `Cluster ID` column (`prompt_version p2-dedup-v8`, `dedup_v2_active true`,
`rows_in 183`).

---

## 1. Election procedure

`elect_golden_records(rows, weights, *, confidence_threshold=None)`
(`dedup/scoring.py:1151-1277`) executes the following steps in order.

| # | Step | Code | Effect |
|---|------|------|--------|
| 1 | Resolve weights | `:1172-1173` | `weights=None` loads `dedup/weights.json` via `load_weights` (`:649-655`), which drops keys prefixed `_`. |
| 2 | Resolve confidence threshold | `:1174` → `_resolve_confidence_threshold` `:1122-1136` | explicit argument > env `CONFIDENCE_MERGE_THRESHOLD` > `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` (`:50`). An unparseable env value logs a warning and falls back to the default. |
| 3 | Fingerprint weights | `:1175` → `weights_version` `:641-647` | 12 hex characters of `sha256` over the canonically serialised table. |
| 4 | Resolve the reference year | `:1181` | `datetime.date.today().year`, resolved once per election — not at import and not per row. |
| 5 | Reject duplicate `row_id` | `:1183-1187` | `DuplicateRowIdError`; the only hard failure in the module. Mapped to HTTP 400 (`api/routes.py:1466-1472`, `api/routes.py:1544-1550`). |
| 6 | Compute cluster year maxima | `:1193-1201` → `_cluster_year_maxima` `:1097-1119` | `(max last_order_year, max partner_last_order_year)` per cluster, computed only for clusters of ≥ 2 members. |
| 7 | Score every row | `:1203-1207` → `_Scored` `:1067-1094` → `score_row` `:903-1031` | Eleven criteria; `total = sum(breakdown.values())`. |
| 8 | Detect partial clusters | `:1218-1223` | A `c_`-prefixed cluster id whose present members do not reproduce `cluster_hash` is marked partial; a warning is attached at `:1271-1275`, never an error. |
| 9 | Elect a winner per cluster | `:1227-1231` | `min(members, key=_tiebreak_key)` over clusters of ≥ 2 members. |
| 10 | Apply demotions | `:1239-1249` | Four independent conditions, any one sufficient (§5). |
| 11 | Build result rows | `:1251-1275` → `_build_result` `:1280-1325` | Golden / proposal / approval fields per row, returned in input order. |

Single-member clusters do not elect: they carry no entry in `winner_by_cluster` (`:1228-1229`) and
fall through to the lone-row branch at `:1255-1265`, becoming `unique` — or `manual_review`
when clustering routed them that way.

Election is scoped to `Cluster ID` alone. `ScoringRow` (`dedup/scoring.py:98-259`) declares no
`Link ID` field, and `dedup/scoring_xlsx.INPUT_HEADERS` (`:41-60`) does not read that column,
so the institution-family link the adjudicator emits (`dedup/adjudicator.py:1114-1116`,
`:1514-1525`) never widens or narrows an election scope. Scores are never compared across
clusters.

---

## 2. Weights

`dedup/weights.json` verbatim (`:3-56`; the `_comment` key at `:2` is metadata and is stripped
by `load_weights`).

| Criterion | Band → points | File |
|---|---|---|
| `sales_order_last_used` | `0`→20, `1`→15, `2`→10, `3`→5 | `dedup/weights.json:3-8` |
| `sales_order_count` | `1-5`→5, `6-10`→15, `>10`→25 | `:9-13` |
| `sales_order_partner_last_used` | `0`→20, `1`→15, `2`→10, `3`→5 | `:14-19` |
| `sales_order_partner_count` | `1-5`→5, `6-10`→15, `>10`→25 | `:20-24` |
| `equipment_count` | `1-3`→5, `4-8`→12, `9-15`→20, `>15`→30 | `:25-30` |
| `sleeping_customer` | `No`→15, `Yes`→0 | `:31-34` |
| `customer_status` | `active`→10, `blocked`→0 | `:35-38` |
| `account_group` | `DRIT`→20, `0002/SHIP2`→15, `0003`→10, `0004`→10, `0005/LIEF/MLIEF`→5 | `:39-45` |
| `company_code_count` | `1`→5, `2-4`→15, `5+`→25 | `:46-50` |
| `combined_presence_bonus` | `company code AND sales org`→10 | `:51-53` |
| `salesforce_instance_count` | `per instance`→10 | `:54-56` |

Maximum attainable total is 200 points (20+25+20+25+30+15+10+20+25+10, plus 10 per Salesforce
instance, unbounded above).

### 2.1 Band grammar

Numeric bands are matched by `_match_numeric_band` (`dedup/scoring.py:815-841`); label bands by
`_match_label_band` (`:844-871`); single-band criteria by `_single_band_value` (`:873-875`).

| Label form | Meaning | Code |
|---|---|---|
| `a-b` | inclusive range | `:833-836` |
| `>n` | strictly greater | `:827-829` |
| `n+` | greater or equal | `:830-832` |
| bare number | exact equality | `:837-838` |
| `X/Y` | either literal, case-insensitive | `:865-867` |
| unparseable | logged and skipped, scores 0 | `:839-840` |

No match scores 0 — the table's implicit else band (`:822-823`, `:841`). Bands are tested in dictionary
insertion order and the first match returns, so overlapping bands resolve to the earlier one.

### 2.2 Weights override

Both entry points accept a wholesale override under identical all-or-nothing semantics, both
implemented by `coerce_weights` (`dedup/scoring.py:657-692`):

| Entry point | Override source | Code |
|---|---|---|
| `POST /api/dedup/score` | `weights` field of the request body | `api/routes.py:1456-1465` |
| `POST /api/dedup/score/file` | a worksheet named `Weights` (case-insensitive), columns `Criterion, Band, Points` | `dedup/scoring_xlsx.py:99-117`, `:210-224` |

Every `(criterion, band)` pair present in `dedup/weights.json` must be present with a numeric
`Points` value; one missing pair or one non-numeric cell rejects the whole candidate
(`dedup/scoring.py:677-689`). A rejection is a warning in `summary.warnings`, never an error.
Points are cast with `int(points)` (`:690`), so a fractional override truncates toward zero.

`data/eval/stress_200_scored.xlsx` carries no `Weights` sheet, and its
`scored_with_weights_version` column holds `3147cac47910` on every row — the fingerprint of the
current `dedup/weights.json` (§14, measurement M4). The exemplars below are therefore scored
against the table in §2.

---

## 3. Per-criterion scoring

`score_row(row, weights, cluster_max_year, cluster_max_partner_year, *, current_year)`
(`dedup/scoring.py:903-1031`). The breakdown always carries all eleven keys, 0 where nothing
matched, so the output columns are stable.

| Criterion | Input field (file header) | Derivation | Cluster-dependent | Code |
|---|---|---|---|---|
| `sales_order_last_used` | `Sales_Order_Last_Used` | banded on `current_year − year`, not on the year | no | `:957-959` |
| `sales_order_count` | `Sales_Order_Total_Count` | banded on the count, **gated by G1** | yes | `:963-977` |
| `sales_order_partner_last_used` | `Sales_Order_Partner_Last_Used` | offset band, as above | no | `:978-981` |
| `sales_order_partner_count` | `Sales_Order_Partner_Total_Count` | count band, **gated by G1** | yes | `:984-998` |
| `equipment_count` | `Equipment_Total_Count` | count band | no | `:999-1001` |
| `sleeping_customer` | `SleepingCustomer` | label band, warns on an unrecognised value | no | `:1002-1005` |
| `customer_status` | `CustomerStatus` | label band, warns on an unrecognised value | no | `:1008-1011` |
| `account_group` | `Account group` / `Customer Account Group` | label band, silent on an unrecognised value | no | `:1014-1017` |
| `company_code_count` | `Company_Code_Consolidated` | count of delimited parts, then band | no | `:1018-1020` |
| `combined_presence_bonus` | `Company_Code_Consolidated` + `Sales_Org_Consolidated` | flat bonus when both counts are > 0 | no | `:1022-1026` |
| `salesforce_instance_count` | `sf1`…`sf8` | count of non-blank slots × per-instance points; unbounded | no | `:1027-1030` |

`Sales_Org_Consolidated` has no standalone tier: it contributes only through
`combined_presence_bonus` (`:1022-1026`).

### 3.1 Coercion

`_coerce_int` (`dedup/scoring.py:714-761`) accepts int, float (truncated), and numeric strings;
blank and `None` become `None` and score 0 silently; a present-but-unparseable value scores 0
and emits a warning. `bool` is rejected as dirt (`:738-740`). `allow_date=True` is set for the
two `*_last_used` fields only (`:939-941`, `:945-948`), extracting `.year` from a
`datetime`/`date` and from an ISO-ish string through `_iso_year` (`:698-712`, years 1900–2200,
leading four digits followed by `-/. ` or nothing). The count columns keep `allow_date=False`
so a date landing in `Equipment_Total_Count` warns rather than scoring as a year.

`split_consolidated` (`:780-793`) splits on both `,` and `;`. `derived_counts` (`:795-809`)
always recomputes `Company_Code_Count`, `Sales_Org_Count` and `Salesforce_Instance_Count` from
the consolidated cells and the eight slots; the file's own values for those columns are
overwritten, never read.

`_match_label_band` distinguishes absence from an unrecognised value: `None` and blank score 0
silently (`:859-863`), a present unknown scores 0 and warns when `warn_unknown` is set
(`:868-869`). `account_group` passes `warn_unknown=False` (`:1016`) because its table has an
explicit anything-else-is-0 semantics; `sleeping_band` and `customer_status` pass `True`.

### 3.2 Relative year ladders

The two `*_last_used` ladders band on the offset from the election's reference year
(`_year_offset`, `:763-771`), never on an absolute year. Offset 0 is the reference year, 1 the
year before. A future-dated order yields a negative offset, matches no band, and scores 0.
The absolute year is retained for every other use — the G1 gate, the cluster maxima and the
tie-break all compare years (`:964`, `:987`, `:1090-1092`, `:1097-1119`).

---

## 4. The G1 recency gate

`_award_count(row_year, cluster_max_year)` (`dedup/scoring.py:882-900`) decides whether a
sales-order count component is awarded at all.

| `row_year` | `cluster_max_year` | Count points | Code |
|---|---|---|---|
| `None` | any | 0 | `:896-897` |
| present | `None` (context-free: unique row, or a cluster of one) | awarded | `:898-899` |
| present | present, equal | awarded | `:900` |
| present | present, older | 0 | `:900` |

A suppression emits a warning only when it is a genuine recency loss — points that would have
scored, suppressed because a strictly more recent member exists (`:963-977`, `:984-998`). A
context-free suppression produces no warning. The warning text carries the marker
`count suppressed (G1)` (`:450`), which `detect_issues` re-reads to raise
`count_suppressed_by_recency` (`:522-530`).

The gate applies to the two count criteria only. Every other criterion is unconditional, so a
record that lost its count points can still win on company-code breadth, equipment or account
group.

### Exemplar — G1 decides an election

Cluster `c_fa5b11d02c61` in `data/eval/stress_200_scored.xlsx`, three records all named
`Merck & Co., Inc.`:

| Customer | `Sales_Order_Last_Used` | `Sales_Order_Total_Count` | `Company_Code_Count` | `score_SalesOrderCount` | `score_final` | Outcome |
|---|---|---|---|---|---|---|
| 13347414 | 2026 | 3 | 1 | 5 | **142** | winner |
| 13189884 | 2025 | 2 | 13 | **0** (suppressed) | 140 | duplicate |
| 13334413 | — | — | 0 | 0 | 40 | duplicate |

The cluster maximum is 2026, so 13189884's count is gated to 0. Re-scoring that row
context-free returns 145 (§14, measurement M5) — above the winner's 142. The record with
thirteen company codes and eleven sales organisations loses to the record with one of each, by
two points, because its most recent order is one year older. `Issues` row
`13189884 · c_fa5b11d02c61 · count_suppressed_by_recency` records the suppression; nothing in
the workbook records that it changed the winner.

Twelve rows carry a `count_suppressed_by_recency` issue in this file — two on the sales-order
pair, ten on the partner pair (§14, measurement M2).

---

## 5. Demotion to `manual_review`

Four conditions are evaluated per cluster of ≥ 2 members (`dedup/scoring.py:1239-1249`). Any
one is sufficient; they are not ordered by precedence in the code, all four being computed
before the disjunction at `:1248`.

| Condition | Test | Code | Note |
|---|---|---|---|
| Inherited | any member's incoming `Routing` normalises to `manual_review` | `:1239-1241` | Uncertainty propagates; election never upgrades it. |
| All blocked | every member's `CustomerStatus` casefolds to `blocked` | `:1242` | `blocked` scores 0 but stays eligible to win (`:1006-1011`). |
| Low confidence | the cluster's minimum non-null `Confidence` is below the threshold | `:1243-1244`, `_cluster_merge_confidence` `:1138-1149` | All-null confidence (a deterministic identical collapse) returns `None` and never gates. |
| Zero signal | every member scored 0 | `:1247` | A tie-break with no scoring basis must not read as confident. |

A lone row (no cluster, or a cluster of one) is demoted only by the inherited condition
(`:1255-1263`); the other three do not apply to it.

In `data/eval/stress_200_scored.xlsx`: 9 of 42 clusters and 3 lone rows are `manual_review`,
25 rows in total. Five clusters carry a `low_confidence_merge` issue, at minimum confidences
0.83, 0.91, 0.94, 0.94 and 0.94 against the 0.95 threshold. No cluster in the file is all-blocked and
none is zero-signal — the two `Blocked` rows (13222790, 13361617) sit in clusters and singletons
with active members, and the lowest `score_final` in the file is 15 (§14, measurements M2, M3, M6).

---

## 6. Tie-break

`_tiebreak_key` (`dedup/scoring.py:1048-1065`) returns a sort key; the winner is the `min`
(`:1231`).

| Rank | Key | Direction | Missing value |
|---|---|---|---|
| 1 | `score` (`total`) | highest first | — |
| 2 | `last_order_year` (absolute) | most recent first | treated as −1, i.e. last |
| 3 | `equipment_count` | highest first | treated as −1, i.e. last |
| 4 | `company_code_count` | highest first | always an integer ≥ 0 |
| 5 | `row_id` | **lowest** first | — |

`row_id` is the final uniqueness guarantee, so the winner is invariant under input shuffling.
It is compared numerically when every `row_id` in that cluster parses as an integer, and
lexically otherwise (`:1230`, `_parses_as_int` `:1328-1333`). The check is per cluster, so one
non-numeric identifier changes the comparison for its own cluster only. `data/eval/
stress_200_scored.xlsx` contains one non-numeric identifier, `IC1280` in cluster
`c_aa173c8aa466` (§14, measurement M7).

The docstring marks the ordering `UNCONFIRMED` pending confirmation with the business owner
(`:1051-1057`).

### Exemplars — the three tie-break clusters

`detect_issues` raises `tiebreak_decided` when the top score is shared (`:554-560`). Three
clusters in the stress file qualify; each is decided at a different rank.

| Cluster | Members | Shared score | Decided at | Winner |
|---|---|---|---|---|
| `c_7b0f5107ff65` | 13348869, 13359026 — both `Stanford University`, both `Sales_Order_Partner_Last_Used` 2026, both `Equipment_Total_Count` blank, both `Company_Code_Count` 0 | 85 | rank 5, lowest `row_id` | 13348869 |
| `c_2759839874b4` | 13210816 `KMB3 LLC` (`Sales_Order_Last_Used` 2026), 13337284 `Case Western Reserve University` (blank) | 85 | rank 2, recency | 13210816 |
| `c_cb3fed562da7` | 13113215 `Covia Corp`, 13128534 `Covia Holdings LLC` — every year and count blank | 30 | rank 5, lowest `row_id` | 13113215 |

`c_2759839874b4` shows that the ballot is name-blind: the elected golden record is `KMB3 LLC`,
an opaque SAP code carrying the real institution name in Name 2, chosen over a record whose
Name 1 spells the institution out. No criterion in §2 reads a name field.

---

## 7. `election_status` semantics

Three values, declared as a `Literal` on `ScoringResultRow` (`dedup/scoring.py:318`).

| Value | Set when | Code |
|---|---|---|
| `unique` | no cluster, or a cluster that degraded to one member, and the incoming routing is not `manual_review` | `:1255-1263` |
| `proposed` | a cluster of ≥ 2 members with no demotion condition met | `:1267-1269` |
| `manual_review` | a cluster meeting any demotion condition of §5, or a lone row whose incoming routing is `manual_review` | `:1259-1263`, `:1267-1269` |

Field settlement per status (`_build_result`, `:1280-1325`), and the divergence between the
JSON model and the workbook:

| Field | `unique` | `proposed` | `manual_review` (model) | `manual_review` (workbook) |
|---|---|---|---|---|
| `is_golden_record` | `true` | winner `true`, loser `false` | winner `true`, loser `false` | **blank** |
| `golden_record_id` | own `row_id` | own id / winner's id | own id / winner's id | **blank** |
| `proposed_golden_id` | `null` | winner's id | winner's id | winner's id |
| `approval_status` | `null` | `proposed` | `proposed` | `proposed` |

The blanking is applied at the file writeback only (`dedup/scoring_xlsx.py:309-316`), not in the
model (`dedup/scoring.py:1300-1311`). Its purpose is stated at `dedup/scoring_xlsx.py:309-311`:
a consumer filtering on `is_golden_record` alone cannot reach an unreviewed row. The JSON route
keeps the proposal so that summary accounting and promotion-on-approval have it.

The Phase 3 contract is stated on the model (`dedup/scoring.py:291-293`): consume only rows with
`approval_status == "approved"` or `election_status == "unique"`.

A `manual_review` lone row is its own proposed winner (`:1264-1265`), so `proposed_golden_id`
self-references. Three such rows exist in the stress file: 13213081, 13334236, 13033988.

---

## 8. Approval lifecycle

`apply_approval(rows, cluster_id, decision)` (`dedup/scoring.py:605-634`), reached through
`POST /api/dedup/approve` (`api/routes.py:1487-1515`).

| Aspect | Behaviour | Code |
|---|---|---|
| Scope | every row carrying the named `cluster_id` | `dedup/scoring.py:623-626` |
| Unknown cluster | `ClusterNotFoundError` → HTTP 404 | `:618-619`, `api/routes.py:1506-1507` |
| `approved` | `approval_status="approved"`; the proposal is promoted — `is_golden_record = (row_id == proposed_golden_id)`, `golden_record_id = proposed_golden_id` | `dedup/scoring.py:627-631` |
| `rejected` | `approval_status="rejected"`; golden fields left as they stand | `:627-628` |
| Mutation | inputs are never mutated; rows are copied | `:627` |
| Persistence | none — the caller submits the scored rows and receives them back | `:584-586`, `api/routes.py:1489-1497` |
| Approver | `approver` is required and non-empty, echoed in the response, and written to no row field | `dedup/scoring.py:591`, `:595-603` |

Approval is therefore the only way a `manual_review` row acquires a non-blank
`golden_record_id`. Rejection leaves the cluster in the state it was elected in; no code path
re-elects a rejected cluster or removes its `proposed_golden_id`.

⚠ There is no durable approval store. A decision exists only in the response body of the call
that made it; a subsequent `/api/dedup/score` run over the same rows re-proposes from scratch.

---

## 9. Drift stamps

Two per-row stamps let a proposal and a later approval be checked for divergence.

| Column | Value | Detects | Code |
|---|---|---|---|
| `scored_with_weights_version` | 12 hex characters of `sha256` over the canonical weights JSON | the weights table was retuned between proposal and approval | `dedup/scoring.py:641-647`, written `:1308`, `:1322` |
| `scored_with_reference_year` | the reference year the ladders were anchored to | the relative `*_last_used` ladders shifted across a New Year | `:1181`, written `:1309`, `:1323` |

Both are stamped on every row including `unique` ones. In `data/eval/stress_200_scored.xlsx`
every row carries `3147cac47910` and `2026`.

⚠ `scored_with_reference_year` is not persisted to SQL: `Mapping.usp_MergeValidationScores`
parses and merges `scored_with_weights_version` (`sql/usp_merge_validation_scores.sql:61`,
`:78`) but has no column for the reference year. Ladder drift is detectable in the workbook and
in the JSON response, and not in the validation database.

---

## 10. Issues emitted at election

`detect_issues` (`dedup/scoring.py:485-566`) is deterministic and offline. Eight types are
declared in `ISSUE_TYPES` (`:434-443`); seven are emitted from this module.

| Type | Level | Raised when | Code |
|---|---|---|---|
| `verdict_contradiction` | row | the persisted `Reasoning` starts `split:` or contains one of seven non-merge markers | `:505-510`, `_reasoning_is_contradiction` `:475-482`, markers `:452-456` |
| `candidate_cap_exceeded` | block | `Reasoning` carries the adjudicator's cap marker; one issue per capped block | `:511-520` |
| `count_suppressed_by_recency` | row | a result warning carries `count suppressed (G1)` | `:522-530` |
| `low_confidence_merge` | cluster | the minimum member `Confidence` is below the threshold | `:543-548` |
| `all_blocked_cluster` | cluster | every member's status is `blocked` | `:549-553` |
| `tiebreak_decided` | cluster | ≥ 2 members share the top score | `:554-560` |
| `empty_scoring_payload` | cluster | every member scored 0 | `:561-565` |
| `missing_building_inconsistency` | — | declared, never emitted here; reserved for the Phase 1 building differentiator | `:437`, `:428-433` |

Cluster-level issues key their `row_id` on the cluster's proposed winner (`:538-540`). Issues
are written to a rebuilt `Issues` worksheet on the file route (`dedup/scoring_xlsx.py:325-335`)
and returned in the `issues` array on the JSON route (`api/routes.py:1484`). The `Issues` sheet
of `data/eval/stress_200_scored.xlsx` holds 20 rows: 12 `count_suppressed_by_recency`, 5
`low_confidence_merge`, 3 `tiebreak_decided`.

Scoring warnings that are not one of these types — an unrecognised `SleepingCustomer` band, a
non-numeric count, a partial cluster — reach `summary.rows_with_warnings` only
(`dedup/scoring.py:1351-1352`). That counter is logged (`api/routes.py:1476-1483`, `api/routes.py:1554-1562`) and is
written to no column and no sheet.

---

## 11. Output surface

### 11.1 Workbook

`score_workbook` (`dedup/scoring_xlsx.py:191-339`) edits the upload in place with openpyxl; the
`Weights` sheet and every original column survive. Columns are located by normalised header name
and appended when absent (`_ensure_column`, `:147-155`). Input headers accept alternative
spellings, first present wins (`INPUT_HEADERS` `:41-60`, resolution `:227-232`).

Written columns: the eleven `score_*` components (`SCORE_BREAKDOWN_COLUMNS`,
`dedup/scoring.py:67-79`), `score_final`, the three derived counts (`DERIVED_COLUMNS`
`dedup/scoring_xlsx.py:74`), the five election columns (`ELECTION_COLUMNS` `:75-78`),
`scored_with_weights_version` and `scored_with_reference_year` (`:293-297`).

The data sheet is the first worksheet carrying a `Customer` or `Customer No.` header
(`:124-131`). Cluster membership is read from the `Routing` + `Cluster ID` pair, falling back to
`expected_routing` + `expected_cluster`; the pairs are never mixed (`_cluster_columns`
`:158-169`). Only `cluster` and `manual_review` routings carry a cluster id into election —
`unique` and any unrecognised routing map to no cluster (`_cluster_id_from_cells` `:172-188`).
A row with a blank `Customer` is skipped and counted in `summary.errors` (`:256-260`).

### 11.2 SQL

`Mapping.usp_MergeValidationScores` (`sql/usp_merge_validation_scores.sql`) parses the
`/api/dedup/score` response with `OPENJSON` over `$.rows` (`:49-73`) and merges 21 columns into
`<db>.<entity>.Validation` on `Customer` plus a `code LIKE '<groupcode>_%'` scope (`:78`). The
merge is update-only — there is no `WHEN NOT MATCHED` branch, so a `Customer` absent from the
target is silently dropped. Two guards precede it: the entity schema must exist (`:14-21`) and
the group code must be non-empty and match existing rows (`:26-41`). The target database is
`dp_validation`, marked `-- <<< confirm` in the source (`:8`).

Invoked from `adf/scoring_pipeline.json:89` as the third activity of a three-step pipeline
(`Lookup1` → `Web1` → `Merge Back`), parameterised on `chrEntity` and `chrGroupCode`.

### 11.3 Summary

`build_summary` (`dedup/scoring.py:1336-1372`) aggregates the result list. For
`data/eval/stress_200_scored.xlsx`, re-running the scorer over the workbook reproduces its
stored columns exactly and returns (§14, measurement M3):

| Field | Value | Composition |
|---|---|---|
| `rows_in` | 183 | |
| `clusters` | 42 | 35 of size 2, 6 of size 3, 1 of size 4 |
| `rows_elected` | 45 | 33 proposed winners + 9 `manual_review` cluster winners + 3 lone `manual_review` rows |
| `rows_duplicates` | 50 | 37 proposed losers + 13 `manual_review` losers |
| `rows_unique` | 88 | |
| `rows_manual_review` | 25 | 22 in clusters + 3 lone |
| `all_blocked_clusters` | 9 | |
| `rows_with_warnings` | 20 | |
| `errors` | 0 | |

⚠ `all_blocked_clusters` does not count all-blocked clusters. `build_summary` populates it from
`manual_review_ids`, which collects every cluster whose `election_status` is `manual_review`
regardless of cause (`dedup/scoring.py:1366-1369`, `:1371`). The stress file contains no
all-blocked cluster and the field reports 9. The field name and the quantity disagree; the field
name matches `ISSUE_TYPES`' `all_blocked_cluster`, which is a different and correctly-computed
signal (§10).

---

## 12. Failure classes

### 12.1 Continuation split

A name that overflows from Name 1 into Name 2 is a continuation of one institution name, not an
institution plus a department. `dedup/name_slots.py` recognises three arms:
`_is_continuation` (`:225-229`) accepts a Name 2 built only from `_CONTINUATION_NOUNS` (31
entries, `:93-102`) and legal forms; `_is_overflow` (`:231-248`) also accepts a rebuilt name that
is Jaro-Winkler ≥ `OVERFLOW_THRESHOLD` 0.92 (`:109`) to another Name 1 in the block, or a Name 1
ending on a `_DANGLING_CONNECTORS` token (`:104`); `_institution_split` (`:254-324`) covers
the case where both slots hold overlapping pieces of one name.

When none of the three fires, the record keys on institution + department, takes a different
signature from its twins, and lands in a different cluster or no cluster at all. Election then
runs separately on each side. The result is two or more golden records for one entity, neither
marked as a proposal about the other, and no issue type in §10 detects it: `detect_issues` never
compares across clusters.

**Exemplar — Hoag (ground-truth duplicate group D053).** Three records at one address in Newport
Beach.

| Customer | Raw Name 1 | Raw Name 2 | Enriched Name 2 | Signature | Cluster | `score_final` | Outcome |
|---|---|---|---|---|---|---|---|
| 13334046 | `Hoag Memorial Hospital Presbyterian` | — | — | s1 | `c_9ff82ee12730` | 145 | winner |
| 13335012 | `Hoag Memorial Hospital` | — | — | s2 | `c_9ff82ee12730` | 85 | duplicate |
| 13336374 | `Hoag Memorial Hospital` | `Presbyterian` | `Presbyterian Intercommunity Hospital` | s3 | — | 85 | `unique`, golden, self-referencing |

`Presbyterian` is not in `_CONTINUATION_NOUNS`, and enrichment expanded it into
`Presbyterian Intercommunity Hospital` — the name of a different hospital — before the signature
was taken. The third signature is the consequence. All three rows share `Link ID`
`l_869c2dc04f44`, so the institution family is recorded; election does not read it (§1), and
13336374 elects itself. One entity, two golden records, no issue raised.

**Exemplar — EMD Serono (ground-truth duplicate group D066).** Five records at 45A Middlesex
Turnpike, Billerica, sharing `Link ID` `l_98c476241085` and one block, resolving to three
signatures and three golden proposals.

| Customer | Raw Name 1 | Raw Name 2 | Signature | Cluster | `score_final` | Outcome |
|---|---|---|---|---|---|---|
| 13135468 | `EMD Serono Research &` | `Development Institute, Inc.` | s2 | `c_04029e90502f` | 85 | winner (`manual_review`) |
| 13353599 | `EMD Serono` | `Research and Development Inst` | s2 | `c_04029e90502f` | 70 | duplicate |
| 13364185 | `EMD Serono Research and Development Inst` | — | s2 | `c_04029e90502f` | 55 | duplicate |
| 13138597 | `EMD Serono Research Inst Inc` | `Research & Dev Inst` | s3 | — | 125 | `unique`, golden |
| 13033988 | `EMD Serono` | — | s1 | — | 120 | `manual_review`, self-proposed |

13138597 is the exact record `_institution_split`'s docstring names as the arm requiring the
token-containment test (`dedup/name_slots.py:254-324`), and it is elected as an independent
golden record with the highest score in the group. ⚠ UNVERIFIED — whether the rule was active in
the run that produced this file cannot be established from the repository: the `Run` sheet of
`data/eval/stress_200_scored.xlsx` records feature flags and a prompt version but no commit sha.

The election-side consequence is exact: the highest-scoring member of D066 (13138597, 125) and
the second-highest (13033988, 120) are both outside the elected cluster, whose winner scores 85.
Score comparison is per cluster, so no arithmetic in `dedup/scoring.py` can notice.

### 12.2 Cross-cluster name inconsistency

Because election is cluster-scoped and name-blind, records that state the same name in different
clusters elect different golden records, and nothing in the output states that the two goldens
name the same organisation.

**Exemplar — Merck.** Eight records in the stress file carry the enriched Name 1
`Merck & Co., Inc.` or `Merck Research Labs`. All eight share `Link ID` `l_fa56b257f84f`. They
resolve to three clusters and one unclustered row, and to four golden records.

| Customer | Enriched Name 1 | Name 2 | Cluster | `score_final` | `election_status` | Golden proposed |
|---|---|---|---|---|---|---|
| 13347414 | `Merck & Co., Inc.` | — | `c_fa5b11d02c61` | 142 | proposed | **13347414** |
| 13189884 | `Merck & Co., Inc.` | — | `c_fa5b11d02c61` | 140 | proposed | 13347414 |
| 13334413 | `Merck & Co., Inc.` | — | `c_fa5b11d02c61` | 40 | proposed | 13347414 |
| 13118369 | `Merck & Co., Inc.` | — | `c_d2aab5dcfa8f` | 115 | proposed | **13118369** |
| 13359185 | `Merck & Co., Inc.` | — | `c_d2aab5dcfa8f` | 85 | proposed | 13118369 |
| 13348301 | `Merck & Co., Inc.` | `Merck Research Laboratories` | `c_bfecb3d6ea82` | 140 | manual_review | **13348301** |
| 13364371 | `Merck Research Labs` | `Cambridge Exploratory Science Center` | `c_bfecb3d6ea82` | 90 | manual_review | 13348301 |
| 13348052 | `Merck & Co., Inc.` | `Merck Research Laboratories - Kenilworth` | — | 60 | unique | **13348052** (self) |

Some of that separation is what the ground truth calls for: `gt_entity_id` E061 and E064 are
different sites, and D107 and D114 are duplicate groups distinct from D106 and D113. One split is
not. 13118369, 13359185 and 13348301 all carry `gt_dup_group` D106, they land in two clusters, and
they elect two golden records — 13118369 and 13348301 — whose enriched Name 1 values are
byte-identical.

Two goldens with identical names and no relation recorded between them is the failure the
`Link ID` was introduced to make visible (`dedup/adjudicator.py:1031-1060`), and the election
output does not carry that column: `ELECTION_COLUMNS` (`dedup/scoring_xlsx.py:75-78`) and the
merge proc's `OPENJSON` list (`sql/usp_merge_validation_scores.sql:49-73`) both omit it.

Across the whole file, 15 of the 125 ground-truth duplicate groups — records the ground truth
says should collapse into one record — hold more than one distinct golden id, and 38 of 68
`gt_entity_id` groups do (§14, measurement M8). The entity figure counts correct
link-not-collapse separations as well; the duplicate-group figure does not.

---

## 13. Observed deviations between the weights table and the data

### 13.1 `SleepingCustomer` vocabulary

`weights.json` gives `sleeping_customer` two bands, `No` and `Yes` (`:31-34`). Ten rows in
`data/eval/stress_200_scored.xlsx` carry `3-4` (6 rows) or `>5` (4 rows). Each scores 0 with an
`unrecognized -> 0` warning (`dedup/scoring.py:868-869`) rather than 15, and the warning reaches
no column and no `Issues` row (§10).

The fixture's `Dummy_Fill` sheet states the intent: *"QLIC is 'No' on all 22,224 rows. 6 rows set
'3-4', 4 rows set '>5' so all three weight bands are exercised."* The weights table has two
bands, not three, and neither is `3-4` or `>5`.

Two elections turn on the gap (§14, measurement M9):

| Cluster | Members | As scored | With `No` scored at 15 |
|---|---|---|---|
| `c_42e94fea3725` | 13344194 (`No`, 110), 13145924 (`3-4`, 100) | 13344194 wins | 13145924 at 115 wins — the election reverses |
| `c_88e8e1d4a7a8` | 13118081 (`No`, 105), 13057667 (`3-4`, 90) | 13118081 wins | 105 each — a tie decided by §6 |

⚠ MEASUREMENT REQUIRED — whether the production QLIC extract emits banded sleeping values at all
cannot be determined from this repository; the reference extract named in the fixture,
`US_Qlic_report_data_2026-07-30.xlsx`, is not present.

### 13.2 `account_group` values outside the table

`account_group` scores unknowns silently by design (`dedup/scoring.py:1014-1017`). Two rows in
the stress file carry values absent from the table: `DIUS` (13079821) and `DBRU` (IC1280), both
scoring 0 against a maximum of 20. `DBRU` is named in the code comment as deliberately parked
(`:1012-1013`). `LIEF` (4 rows) matches through the `0005/LIEF/MLIEF` alternation and scores 5.

The table's `DRIT` band is itself marked `UNCONFIRMED` in `weights.json:2` — *"transcript said
DRID; live SAP shows DRIT"* — and 116 of 183 rows in this file carry `DRIT`, scoring the band's
full 20 points each (§14, measurement M6).

### 13.3 Criteria unexercised by the evaluation data

`salesforce_instance_count` scores 0 on all 183 rows: `sf1`…`sf8` are empty throughout, stated in
both the `QLIC_Merge` sheet (*"no source in QLIC - empty for all rows"*) and the `Dummy_Fill`
sheet's third deviation note. ⚠ The one criterion whose points are unbounded above has no
evaluation evidence in this repository.

`equipment_count` scores non-zero on 31 rows, `company_code_count` on 131, and
`combined_presence_bonus` on 129 (§14, measurement M6).

### 13.4 Threshold read twice

`config.py:599-601` defines `Settings.confidence_merge_threshold` from
`CONFIDENCE_MERGE_THRESHOLD`, and `.env` defaults list it at `config.py:128`. Election does not
read it: `dedup/scoring.py` imports no configuration module (`:19-41`) and
`_resolve_confidence_threshold` reads `os.getenv` directly (`:1127`). The Settings field is
referenced nowhere else in the repository. The two readers agree on the default of 0.95 today;
nothing enforces that they continue to.

---

## 14. Measurement provenance

Every count in this document comes from one of the following, run at commit
`86d173b8a4d715a619b0a2656986c145da7fa81e` from the repository root.

**M1 — Test suite.**

```
$ python3 -m pytest tests/test_scoring.py -q
........................................................................ [ 34%]
........................................................................ [ 68%]
..................................................................       [100%]
210 passed, 1 warning in 0.69s
```

**M2, M6–M9 — one script over the two workbooks.** Written to `m.py` and run from the
repository root.

```python
import collections, openpyxl

def rows(path, sheet):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]; it = ws.iter_rows(values_only=True)
    hdr = list(next(it)); idx = {}
    for i, h in enumerate(hdr):
        if h is not None and h not in idx: idx[h] = i
    out = [{h: (r[i] if i < len(r) else None) for h, i in idx.items()} for r in it]
    wb.close(); return out

S = rows('data/eval/stress_200_scored.xlsx', 'Sheet')
I = rows('data/eval/stress_200_scored.xlsx', 'Issues')
G = {str(r['Customer']).strip(): r for r in rows('data/eval/stress_200_pre.xlsx', 'Data')}

print('M2 issue_type:', dict(collections.Counter(r['issue_type'] for r in I)))
print('M6 CustomerStatus:', dict(collections.Counter(r['CustomerStatus'] for r in S)))
print('M6 SleepingCustomer:', dict(collections.Counter(r['SleepingCustomer'] for r in S)))
print('M6 Account group:', dict(collections.Counter(r['Account group'] for r in S)))
print('M6 score_final min/max:', min(int(r['score_final']) for r in S), max(int(r['score_final']) for r in S),
      '| rows at 0:', sum(1 for r in S if int(r['score_final']) == 0))
for c in ('score_EquipmentCount', 'score_CompanyCodeCount', 'score_CombinedPresence', 'score_SalesforceInstances'):
    print('M6', c, 'non-zero rows:', sum(1 for r in S if int(r[c]) > 0))
print('M7 non-numeric Customer:', [(r['Customer'], r['Cluster ID']) for r in S if not str(r['Customer']).strip().isdigit()])

def spread(key):
    d = collections.defaultdict(set)
    for r in S:
        g = G[str(r['Customer']).strip()].get(key)
        if g: d[g].add(r['proposed_golden_id'] or r['golden_record_id'])
    return sum(1 for v in d.values() if len(v) > 1), len(d), sorted(k for k, v in d.items() if len(v) > 1)
n, tot, _ = spread('gt_entity_id'); print('M8 gt_entity_id groups with >1 golden:', n, 'of', tot)
n, tot, names = spread('gt_dup_group'); print('M8 gt_dup_group groups with >1 golden:', n, 'of', tot, names)

cl = collections.defaultdict(list)
for r in S:
    if r['Cluster ID']: cl[r['Cluster ID']].append(r)
print('M9 clusters holding an unrecognised sleeping band:')
for cid, ms in cl.items():
    if any(m['SleepingCustomer'] not in ('No', 'Yes') for m in ms):
        now = max(ms, key=lambda m: int(m['score_final']))
        adj = {m['Customer']: int(m['score_final']) + (15 if m['SleepingCustomer'] not in ('No', 'Yes') else 0) for m in ms}
        top = max(adj.values())
        print('  ', cid, 'as scored ->', now['Customer'],
              '| adjusted top', top, 'held by', sorted(k for k, v in adj.items() if v == top))
```

```
$ python3 m.py
M2 issue_type: {'count_suppressed_by_recency': 12, 'tiebreak_decided': 3, 'low_confidence_merge': 5}
M6 CustomerStatus: {'Active': 181, 'Blocked': 2}
M6 SleepingCustomer: {'No': 173, '3-4': 6, '>5': 4}
M6 Account group: {'DRIT': 116, '0005': 6, '0002': 53, 'LIEF': 4, 'DIUS': 1, '0004': 2, 'DBRU': 1}
M6 score_final min/max: 15 175 | rows at 0: 0
M6 score_EquipmentCount non-zero rows: 31
M6 score_CompanyCodeCount non-zero rows: 131
M6 score_CombinedPresence non-zero rows: 129
M6 score_SalesforceInstances non-zero rows: 0
M7 non-numeric Customer: [('IC1280', 'c_aa173c8aa466')]
M8 gt_entity_id groups with >1 golden: 38 of 68
M8 gt_dup_group groups with >1 golden: 15 of 125 ['D001', 'D005', 'D013', 'D021', 'D028', 'D035', 'D048', 'D053', 'D064', 'D066', 'D083', 'D087', 'D088', 'D104', 'D106']
M9 clusters holding an unrecognised sleeping band:
   c_442cbdd87877 as scored -> 13017986 | adjusted top 110 held by ['13017986']
   c_40fcb6768074 as scored -> 13222790 | adjusted top 105 held by ['13222790']
   c_42e94fea3725 as scored -> 13344194 | adjusted top 115 held by ['13145924']
   c_c9aa10bc7fbc as scored -> 13145693 | adjusted top 155 held by ['13145693']
   c_88e8e1d4a7a8 as scored -> 13118081 | adjusted top 105 held by ['13057667', '13118081']
   c_12859a74751b as scored -> 13104512 | adjusted top 120 held by ['13104512']
   c_04029e90502f as scored -> 13135468 | adjusted top 85 held by ['13135468']
```

Reading of that output:

- **M2** — the `Issues` sheet holds 20 rows: 12 `count_suppressed_by_recency` (2 on the
  sales-order pair, 10 on the partner pair, by their `detail` text), 5 `low_confidence_merge`
  (minimum confidences 0.91, 0.94, 0.94, 0.83, 0.94), 3 `tiebreak_decided` (top scores 85, 85, 30).
- **M6** — column coverage over the 183 scored rows.
- **M7** — one `Customer` value in the file does not parse as an integer.
- **M8** — 38 of 68 `gt_entity_id` groups and 15 of 125 `gt_dup_group` groups hold more than one
  distinct golden id. The `gt_entity_id` figure counts correct link-not-collapse separations as
  well as splits; the `gt_dup_group` figure counts only groups the ground truth says should
  collapse into one record: D001, D005, D013, D021, D028, D035, D048, D053, D064, D066, D083,
  D087, D088, D104, D106.
- **M9** — of the seven clusters holding a row with an unrecognised sleeping band, scoring that
  band at the `No` value of 15 would reverse `c_42e94fea3725` (13145924 rises to 115, above
  13344194's 110) and tie `c_88e8e1d4a7a8` at 105. The other five keep their winner.

**M3 — Summary, reproduced by re-running the scorer over the stored workbook.**

```
$ python3 -c "
from dedup.scoring_xlsx import score_workbook
data = open('data/eval/stress_200_scored.xlsx','rb').read()
out, summary = score_workbook(data)
print(summary.model_dump_json(indent=2))
"
{
  "rows_in": 183,
  "clusters": 42,
  "rows_elected": 45,
  "rows_duplicates": 50,
  "rows_unique": 88,
  "rows_manual_review": 25,
  "all_blocked_clusters": 9,
  "rows_with_warnings": 20,
  "errors": 0,
  "warnings": []
}
```

**M4 — Weights fingerprint.**

```
$ python3 -c "
from dedup.scoring import load_weights, weights_version
w = load_weights()
print('weights_version(dedup/weights.json) =', weights_version(w))
"
weights_version(dedup/weights.json) = 3147cac47910
```

Every row of `data/eval/stress_200_scored.xlsx` carries `scored_with_weights_version`
`3147cac47910` and `scored_with_reference_year` `2026`.

**M5 — G1 counterfactual on 13189884.**

```
$ python3 -c "
from dedup.scoring import load_weights, score_row, ScoringRow
w = load_weights()
r = ScoringRow(row_id='13189884', last_order_year='2025', orders_in_last_used_year='2',
               partner_last_order_year='2026', partner_orders_in_last_used_year='24',
               equipment_count=None, sleeping_band='No', customer_status='Active',
               account_group='DRIT',
               company_code_consolidated='1101,1140,1166,1201,1207,1240,1501,1504,1505,1506,1507,1543,1569',
               sales_org_consolidated='1021,1100,1401,1661,2011,2071,2401,5011,5060,5071,5431')
gated, warn = score_row(r, w, 2026, 2026, current_year=2026)
free,  _    = score_row(r, w, None, None, current_year=2026)
print('gated  total', sum(gated.values()), gated)
print('warnings', warn)
print('context-free total', sum(free.values()))
"
gated  total 140 {'sales_order_last_used': 15, 'sales_order_count': 0, 'sales_order_partner_last_used': 20, 'sales_order_partner_count': 25, 'equipment_count': 0, 'sleeping_customer': 15, 'customer_status': 10, 'account_group': 20, 'company_code_count': 25, 'combined_presence_bonus': 10, 'salesforce_instance_count': 0}
warnings ["order count suppressed (G1): last-used year 2025 is not the cluster's most recent (2026)"]
context-free total 145
```

---

## 15. Open items raised by this pass

| # | Item | Severity | Evidence |
|---|---|---|---|
| 1 | `summary.all_blocked_clusters` counts every `manual_review` cluster, not all-blocked ones | defect | `dedup/scoring.py:1366-1369`, `:1371`; M3 reports 9 with no all-blocked cluster present |
| 2 | `sleeping_customer` has no band for the `3-4` / `>5` values the evaluation data carries; the loss is silent and flips one election | defect | `dedup/weights.json:31-34`, `dedup/scoring.py:868-869`, M9 |
| 3 | `scored_with_reference_year` is written to the workbook and dropped at the SQL sink | gap | `sql/usp_merge_validation_scores.sql:49-73` |
| 4 | `Link ID` reaches the workbook but not election, the merge proc, or any cross-cluster check | gap | `dedup/scoring.py:98-259`, `dedup/scoring_xlsx.py:41-60`, `sql/usp_merge_validation_scores.sql:49-73` |
| 5 | No durable approval store; a decision survives only in its own response body | gap | `dedup/scoring.py:584-586` |
| 6 | `Settings.confidence_merge_threshold` is defined and never read; election reads the env var directly | discrepancy | `config.py:599-601`, `dedup/scoring.py:1127` |
| 7 | `salesforce_instance_count` — the one unbounded criterion — has no evaluation evidence | ⚠ MEASUREMENT REQUIRED | M6; `QLIC_Merge` and `Dummy_Fill` sheets |
| 8 | Tie-break ordering, `combined_presence_bonus` value, partner-count tiers and the `DRIT` account-group code are all marked `UNCONFIRMED` in source | ⚠ UNVERIFIED | `dedup/scoring.py:1051-1057`, `dedup/weights.json:2` |
| 9 | `_institution_split` names 13138597 as a case it handles; that record is nonetheless elected as an independent golden in the stress run | ⚠ UNVERIFIED | `dedup/name_slots.py:254-324`; §12.1 — no commit sha recorded in the workbook's `Run` sheet |
| 10 | The pass specification's exemplar filename `dedup_STRESS_200_v1_*_scored*.xlsx` does not exist | discrepancy | `data/eval/` holds `stress_200_scored.xlsx` |
