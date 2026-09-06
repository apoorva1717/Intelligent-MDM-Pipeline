# Phase 2 scoring & golden-record election dossier — `/api/dedup/score`, `/api/dedup/score/file`

Scope: the code path from a scored row's input columns to `score_final`, the 11 `score_*`
breakdown columns, and the `is_golden_record` / `golden_record_id` / `proposed_golden_id` /
`election_status` / `approval_status` election columns. Read-only audit; **no code was changed**.

Tree state at time of writing: `HEAD = 4e33b52` (`feature/llm-fixes`), 2026-09-05.
Every `file:line` reference below is against that tree.

Pipeline: `dedup_score` (api/routes.py:1318) **or** `dedup_score_file` (api/routes.py:1399) →
`score_workbook` (dedup/scoring_xlsx.py:180) → `ScoringRow` parse (dedup/scoring.py:90) →
`elect_golden_records` (dedup/scoring.py:1033) → `_cluster_year_maxima` (dedup/scoring.py:982) →
`_Scored` / `score_row` (dedup/scoring.py:958 / :813) → `_tiebreak_key` (dedup/scoring.py:939) →
`_build_result` (dedup/scoring.py:1155) → `build_summary` (dedup/scoring.py:1208) →
`detect_issues` (dedup/scoring.py:454) → writeback (dedup/scoring_xlsx.py:267-315).

**Source-of-truth note.** The task named `docs/BerndScoring1.txt` as the business model to check
against. **That file does not exist in this repository** (`find . -iname '*bernd*'` returns
nothing). The business model used for the comparison in §3.4 is instead the Notion page
**"Phase 2 — Deduplication Approaches" → "Confirmed Scoring Model — Reference Table"**
(`https://app.notion.com/p/35d109a5c4618156a185f0ad8104d18f`, last edited 2026-07-06), which
states it was *"Finalized with Bernd (BerndScoring1 + BerndScoring2)"*. Where this dossier says
"Bernd's model" it means that table. It is not a repository artefact and is **not**
version-controlled alongside the code.

---

## 1. ENTRY POINTS

### 1.1 Route inventory — which routes score or elect

| Route | Handler | Scores? | Elects? | Request model | Response model |
|---|---|---|---|---|---|
| `POST /api/dedup/score` | `dedup_score` (api/routes.py:1317-1364) | **yes** | **yes** | `ScoringRequest` (dedup/scoring.py:237-254) | `ScoringResponse` (dedup/scoring.py:436-441) |
| `POST /api/dedup/score/file` | `dedup_score_file` (api/routes.py:1398-1452) | **yes** | **yes** | `UploadFile` (`.xlsx`/`.xlsm`) | `StreamingResponse` — the workbook, filled in place |
| `POST /api/dedup/approve` | `dedup_approve` (api/routes.py:1367-1395) | no | **promotes** an existing election | `ApprovalRequest` (dedup/scoring.py:550-561) | `ApprovalResponse` (dedup/scoring.py:564-571) |
| `POST /api/dedup/cluster-block` | `dedup_cluster_block` (api/routes.py:1223-1250) | **no** | **no** | `DedupRequest` (dedup/models.py) | `DedupResponse` |
| `POST /api/dedup/file` | `dedup_file` (api/routes.py:1253-1309) | **no** | **no** | `UploadFile` | `StreamingResponse` (clustered workbook) |
| `POST /enrich`, `/enrich/file`, `/issues*` | — | no | no | — | — |

**`/api/dedup/cluster-block` does not score.** Its handler calls `cluster_blocks` and returns
its result unchanged (api/routes.py:1240); nothing in `dedup/adjudicator.py` imports
`dedup.scoring`. Its output columns are fixed at api/routes.py:1143 (v1) and :1151-1153 (v2):

```python
_DEDUP_RESULT_COLUMNS = ["Cluster ID", "Routing", "LLM Flag", "Confidence", "Reasoning"]
_DEDUP_RESULT_COLUMNS_V2 = [
    "Cluster ID", "Link ID", "Routing", "LLM Flag", "Confidence", "Reasoning",
]
```

No `score_*` column appears there. `/api/dedup/file` does, however, **carry the scoring
`Weights` sheet through** so the next stage sees it (api/routes.py:1174-1176).

**ADF-facing endpoint: there is none that is scoring-specific.** ADF is described as the
orchestrator that calls the same routes (dedup/models.py:3), and only `/health`
(api/routes.py:89-100) names ADF: *"Health check endpoint for ADF and monitoring."* Per
docs/thesis/06b_CROSSCUTTING.md:1131-1133, **neither exported ADF pipeline currently calls
`/api/dedup/score` or `/api/dedup/approve`** — that is recorded there as open item 5.

### 1.2 `dedup_score` — api/routes.py:1330-1364 (verbatim body)

```python
    request_start = time.perf_counter()
    logger.info("Scoring request received: %d rows", len(request.rows))

    weights = None
    request_warnings: list[str] = []
    if request.weights is not None:
        weights, reason = coerce_weights(
            request.weights, load_weights(), source="Weights payload"
        )
        if weights is None:
            request_warnings.append(reason)
            logger.warning("scoring request: %s", reason)

    try:
        results = elect_golden_records(request.rows, weights)
    except DuplicateRowIdError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate row_id(s) in request: ...",
        ) from exc

    summary = build_summary(results, warnings=request_warnings)
    issues = detect_dedup_issues(request.rows, results)
    ...
    return ScoringResponse(rows=results, summary=summary, issues=issues)
```

Note `elect_golden_records(request.rows, weights)` is called **without** a
`confidence_threshold` argument (api/routes.py:1347), so the threshold resolves from the
environment — see §3.1.

### 1.3 The alias table — JSON route (`ScoringRow`, dedup/scoring.py:105-183, verbatim)

`model_config = ConfigDict(populate_by_name=True)` (dedup/scoring.py:103) means every field may
be supplied **either** by the file column header (the alias) **or** by the snake_case field name.

```python
    row_id: str = Field(
        ..., alias="Customer", description="SAP Customer / BP number, join key."
    )
    cluster_id: Optional[str] = Field(default=None, alias="Cluster ID", ...)
    confidence: Optional[float] = Field(default=None, alias="Confidence", ...)
    routing: Optional[str] = Field(default=None, alias="Routing", ...)
    reasoning: Optional[str] = Field(default=None, alias="Reasoning", ...)
    last_order_year: Scalar = Field(default=None, alias="Sales_Order_Last_Used")
    orders_in_last_used_year: Scalar = Field(
        default=None,
        validation_alias=AliasChoices(
            "Sales_Order_Total_Count", "orders_in_last_used_year", "order_count"
        ),
        serialization_alias="Sales_Order_Total_Count",
    )
    partner_last_order_year: Scalar = Field(
        default=None, alias="Sales_Order_Partner_Last_Used"
    )
    partner_orders_in_last_used_year: Scalar = Field(
        default=None,
        validation_alias=AliasChoices(
            "Sales_Order_Partner_Total_Count",
            "partner_orders_in_last_used_year",
            "partner_order_count",
        ),
        serialization_alias="Sales_Order_Partner_Total_Count",
    )
    equipment_count: Scalar = Field(default=None, alias="Equipment_Total_Count")
    # expected "No" | "3-4" | ">5"
    sleeping_band: Optional[str] = Field(default=None, alias="SleepingCustomer")
    # expected "active" | "blocked"
    customer_status: Optional[str] = Field(default=None, alias="CustomerStatus")
    account_group: Optional[str] = Field(default=None, alias="Account group")
    company_code_consolidated: Optional[str] = Field(
        default=None, alias="Company_Code_Consolidated"  # ";"-delimited
    )
    sales_org_consolidated: Optional[str] = Field(
        default=None, alias="Sales_Org_Consolidated"  # ";"-delimited
    )
    sf1: Optional[str] = Field(default=None, description="Salesforce id slot 1 (Biosystems).")
    sf2: Optional[str] = Field(default=None, description="Salesforce id slot 2 (AXS).")
    sf3: Optional[str] = None
    sf4: Optional[str] = None
    sf5: Optional[str] = None
    sf6: Optional[str] = None
    sf7: Optional[str] = None
    sf8: Optional[str] = None
```

A legacy `salesforce_ids` **list** is also accepted and spread across `sf1..sf8`, but only when
no explicit `sf*` key is present (`_unpack_salesforce_ids`, dedup/scoring.py:185-200).

### 1.4 The alias table — file route (`INPUT_HEADERS`, dedup/scoring_xlsx.py:35-53, verbatim)

```python
INPUT_HEADERS: Dict[str, str] = {
    "Customer": "row_id",
    "Account group": "account_group",  # note the space in the column name
    "Sales_Order_Last_Used": "last_order_year",
    # G1: despite the "_Total_" header, this is interpreted as the count of
    # sales orders WITHIN the last-used year (Bernd's year-priority rule). The
    # count only differentiates records sharing the most-recent year in a
    # cluster. OPEN ITEM P2-21: confirm the click-report column layout actually
    # supplies a within-year count here (not a lifetime total) before go-live.
    "Sales_Order_Total_Count": "orders_in_last_used_year",
    "Sales_Order_Partner_Last_Used": "partner_last_order_year",
    # G1: within-year partner count (same P2-21 caveat as above).
    "Sales_Order_Partner_Total_Count": "partner_orders_in_last_used_year",
    "Equipment_Total_Count": "equipment_count",
    "SleepingCustomer": "sleeping_band",
    "CustomerStatus": "customer_status",
    "Company_Code_Consolidated": "company_code_consolidated",
    "Sales_Org_Consolidated": "sales_org_consolidated",
}

# The 8 Salesforce id slots, in order.
SF_ID_HEADERS: List[str] = [
    "SF_ID_Biosystems", "SF_ID_AXS",
    "SF_ID_3", "SF_ID_4", "SF_ID_5", "SF_ID_6", "SF_ID_7", "SF_ID_8",
]
```

Plus four columns bound outside `INPUT_HEADERS`: `Routing` and `Cluster ID` (via
`_cluster_columns`, dedup/scoring_xlsx.py:147-158), `Confidence` (:225) and `Reasoning` (:227).

Headers are matched **tolerantly** by `_norm` (dedup/scoring_xlsx.py:75-78), which lowercases
and strips every non-alphanumeric character — so `Account group`, `account_group` and
`ACCOUNTGROUP` all bind. **First occurrence wins** on a duplicated header
(dedup/scoring_xlsx.py:131-132); that is untested (open item 168, §7.2).

### 1.5 Columns of the SAP extract that are read for scoring, and those ignored

**Read (15 named + 8 SF slots = 23 columns):** `Customer`, `Account group`,
`Sales_Order_Last_Used`, `Sales_Order_Total_Count`, `Sales_Order_Partner_Last_Used`,
`Sales_Order_Partner_Total_Count`, `Equipment_Total_Count`, `SleepingCustomer`,
`CustomerStatus`, `Company_Code_Consolidated`, `Sales_Org_Consolidated`, `Routing`,
`Cluster ID`, `Confidence`, `Reasoning`, `SF_ID_Biosystems`, `SF_ID_AXS`, `SF_ID_3`…`SF_ID_8`.

Of those, 11 named columns + the 8 SF slots feed points; `Routing` / `Cluster ID` /
`Confidence` / `Reasoning` feed **routing and issue detection only**, never points.

**Ignored — every other column in the workbook**, including several that look like they should
score. Verified by `grep -n "Company Code\|Sales Organization" dedup/scoring.py
dedup/scoring_xlsx.py` → **no matches**:

| Column present in the extract | Read for scoring? | Consequence |
|---|---|---|
| `Company Code` (the raw single-value SAP column) | **no** | only `Company_Code_Consolidated` binds. A workbook carrying `Company Code` but not the consolidated column scores `company_code_count = 0` and forfeits the combined-presence bonus. See §9 for this firing on real fixture data. |
| `Sales Organization` (raw) | **no** | same — only `Sales_Org_Consolidated` binds. |
| `Link ID` (v2 clustering column, api/routes.py:1151) | **no** | see §1.7. |
| `Created On`, `Created By` | **no** | creation date is not a signal (§2.3). |
| Last-changed date | **no such column anywhere** | not a signal. |
| Partner functions | **no** | not a signal; the "partner" fields are *sales-order partner* fields, not SAP partner functions. |
| `Contact`, `Email`, `Care Of` | **no** | contact count is not a signal, though it was on Bernd's question list (§3.4). |
| `Central Deletion Flag`, `Central delivery block` | **no** | blocked status comes only from `CustomerStatus`. |
| `Name 1..5`, `Domain`, `ROR ID`, `LEI ID`, address block, `Flag*`, provenance columns | **no** | carried through the workbook untouched. |

### 1.6 Where scoring runs relative to clustering

**After clustering, always.** Scoring consumes `Cluster ID` / `Routing` / `Confidence` /
`Reasoning` as **inputs** that clustering already wrote. The production chain is
`/enrich/file` → `/api/dedup/file` → `/api/dedup/score/file` (dedup/scoring_xlsx.py:150-152).

**Granularity: per row for points, per cluster for election, with one cluster-level dependency
in the middle.** `elect_golden_records` (dedup/scoring.py:1069-1082) computes each real
cluster's year maxima **before** scoring any row, then scores every row against its own
cluster's maxima:

```python
    rows_by_cluster: Dict[str, List[ScoringRow]] = {}
    for row in rows:
        if row.cluster_id is not None:
            rows_by_cluster.setdefault(row.cluster_id, []).append(row)
    cluster_maxima: Dict[str, Tuple[Optional[int], Optional[int]]] = {
        cid: _cluster_year_maxima(members)
        for cid, members in rows_by_cluster.items()
        if len(members) >= 2
    }

    scored = [
        _Scored(row, weights, *cluster_maxima.get(row.cluster_id, (None, None)))
        for row in rows
    ]
```

So **9 of the 11 criteria are pure per-row functions**; the two sales-order *count* criteria are
cluster-context-dependent (the G1 gate, §3.3).

### 1.7 Rows with no Cluster ID, Link ID only, or manual_review routing

| Input state | `cluster_id` seen by the scorer | Outcome | Cited |
|---|---|---|---|
| No `Cluster ID` cell, routing `unique` | `None` | scored; `election_status="unique"`, `is_golden_record=True`, self-referencing `golden_record_id`, `proposed_golden_id=None`, `approval_status=None` | dedup/scoring.py:1130-1140, :1174-1184 |
| No `Cluster ID`, routing `manual_review` | `None` | scored; `election_status="manual_review"`, **its own** `proposed_golden_id`, `approval_status="proposed"` — never upgraded to unique | dedup/scoring.py:1134-1140; tests/test_scoring.py:382 |
| `Cluster ID` present but only **one** member submitted | the id | **degrades to unique** (`if len(members) < 2: continue`) — status `unique`, `proposed_golden_id=None`, even though `cluster_id` is still emitted | dedup/scoring.py:1103-1104, :1129-1140; tests/test_scoring.py:269 |
| `Cluster ID` present, ≥2 members | the id | real election — winner + losers | dedup/scoring.py:1102-1124 |
| Routing is `unique` **but** a `Cluster ID` cell is filled (file route) | `None` | the cluster key is discarded: `_cluster_id_from_cells` only honours routing `cluster` or `manual_review` | dedup/scoring_xlsx.py:161-177 |
| Routing `manual_review` **with** a `Cluster ID` | the id | membership is kept (only the *merge* was uncertain), and the whole cluster is demoted | dedup/scoring_xlsx.py:164-166; dedup/scoring.py:1114-1124; tests/test_scoring.py:969 |
| **`Link ID` present, no `Cluster ID`** | `None` | **the Link ID is never read.** `grep -n "Link ID\|link_id" dedup/scoring*.py` → no matches. A linked-but-not-merged row is scored and elected as a plain **unique** row. Two rows of one institution family each become their own golden record; nothing in the scoring output records that they are related. | absence across dedup/scoring.py and dedup/scoring_xlsx.py; the semantics it ignores are at dedup/models.py:97-101 |

`_cluster_id_from_cells` — dedup/scoring_xlsx.py:170-177 (verbatim):

```python
    routing_text = str(routing).strip().casefold() if routing is not None else ""
    if routing_text not in ("cluster", "manual_review"):
        return None
    if _is_blank(cluster):
        return None
    if isinstance(cluster, float) and cluster.is_integer():
        cluster = int(cluster)
    return str(cluster).strip()
```

**manual_review clusters are elected anyway, not held.** A winner is computed for every cluster
of ≥2 regardless of status (dedup/scoring.py:1102-1107); the demotion only changes
`election_status` and, on the **file** path, blanks the golden columns (§4.3).

---

## 2. INPUT SIGNALS

### 2.1 Signal table

`file:line` for "where it is read" is the `score_row` line that consumes the value.

| # | Signal | Source column(s) | Type on the wire | Parsing / normalisation | Blank cell | Unparseable cell | Read at |
|---|---|---|---|---|---|---|---|
| 1 | Sales-order recency | `Sales_Order_Last_Used` | `Scalar` = `int\|float\|str\|None` | `_coerce_int` → `int(float(text))`; matched as an **exact numeric band** (`"2026"`…`"2023"`) | `None` → **0 pts, no warning** | 0 pts **+ warning** `last_order_year 'n/a' not numeric -> 0` | dedup/scoring.py:835, :849-851 |
| 2 | Sales-order count | `Sales_Order_Total_Count` | `Scalar` | `_coerce_int`; range bands `0-5 / 6-10 / >10`; **then the G1 recency gate** | `None` → 0 pts (gate denies: no year ⇒ no count) | 0 pts + warning | dedup/scoring.py:836-838, :855-869 |
| 3 | Partner-order recency | `Sales_Order_Partner_Last_Used` | `Scalar` | as (1) | as (1) | as (1) | dedup/scoring.py:839-841, :870-872 |
| 4 | Partner-order count | `Sales_Order_Partner_Total_Count` | `Scalar` | as (2), gated on the **partner** year maximum | as (2) | as (2) | dedup/scoring.py:842-845, :875-889 |
| 5 | Equipment count | `Equipment_Total_Count` | `Scalar` | `_coerce_int`; range bands `0-3 / 4-8 / 9-15 / >15` | `None` → **0 pts** (not 5) | 0 pts + warning | dedup/scoring.py:846, :890-892 |
| 6 | Sleeping-customer status | `SleepingCustomer` | `Optional[str]`, stringified | `_clean_str` + **case-insensitive literal label** match against `"No"`, `"3-4"`, `">5"` | `None`/empty → 0 pts, **no warning** | 0 pts **+ warning** (`warn_unknown=True`) | dedup/scoring.py:893-896 |
| 7 | Active / blocked | `CustomerStatus` | `Optional[str]` | literal label match `"active"` / `"blocked"`, case-insensitive, whitespace-stripped | `None` → 0 pts, no warning — **never defaulted to "active"** | 0 pts + warning | dedup/scoring.py:899-902 |
| 8 | Account group | `Account group` | `Optional[str]` | literal label match; `"X/Y"` = either literal | `None` → 0 pts | 0 pts, **silently** (`warn_unknown=False`) | dedup/scoring.py:905-908 |
| 9 | Company-code count | `Company_Code_Consolidated` | `Optional[str]`, `";"`-delimited | `split_consolidated` → count of non-empty parts; range bands `1 / 2-4 / 5+` | `None` → count 0 → **no band matches** → 0 pts | n/a (a string split cannot fail) | dedup/scoring.py:847, :909-911 |
| 10 | Combined presence | `Company_Code_Consolidated` **and** `Sales_Org_Consolidated` | both `";"`-delimited strings | boolean `company_codes > 0 and sales_orgs > 0` | either blank → 0 pts | n/a | dedup/scoring.py:913-917 |
| 11 | Salesforce instance count | `SF_ID_Biosystems`, `SF_ID_AXS`, `SF_ID_3..8` | 8 `Optional[str]` slots | count of slots that are non-`None` **and** non-whitespace; **multiplied**, not banded | all blank → count 0 → 0 pts | n/a | dedup/scoring.py:713-717, :918-920 |
| — | Merge confidence | `Confidence` | `Optional[float]` | `_floatify_confidence`; blank or dirty → `None` | `None` → **never gates** | `None` → never gates | dedup/scoring.py:224-234, :1020-1030, :1119 |
| — | Incoming routing | `Routing` | `Optional[str]` | `_norm_routing` (strip + casefold) | `None` → not manual_review | any unrecognised string → not manual_review | dedup/scoring.py:934-936, :1114-1116 |
| — | Adjudication reasoning | `Reasoning` | `Optional[str]` | substring / prefix marker match | `None` → no issue | — | dedup/scoring.py:444-451, :474-489 |

### 2.2 Blank / unparseable behaviour — the three distinct outcomes

The module docstring states the policy (dedup/scoring.py:9-13):

> The real CRM extract is ~half empty and dirty. Scoring is therefore permissive: a missing or
> unrecognised value scores 0 (with a warning when the value was present but unrecognised) and
> NEVER raises or fails the batch.

`_coerce_int` — dedup/scoring.py:674-688 (verbatim):

```python
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; a bool here is dirt
        warnings.append(f"{field_name} {value!r} not numeric -> 0")
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        warnings.append(f"{field_name} {text!r} not numeric -> 0")
        return None
```

`_match_label_band` — dedup/scoring.py:769-780 (verbatim tail):

```python
    if value is None:
        return 0
    needle = value.strip().casefold()
    if not needle:
        return 0
    for label, points in bands.items():
        alternatives = [alt.strip().casefold() for alt in label.split("/")]
        if needle in alternatives:
            return int(points)
    if warn_unknown:
        warnings.append(f"{field_name} {value.strip()!r} unrecognized -> 0")
    return 0
```

**In every case the result is 0 points — never a penalty, never `missing` as a distinct state,
never an exception.** The only difference between "blank" and "unparseable" is whether a warning
string is appended. Warnings are counted into `summary.rows_with_warnings`
(dedup/scoring.py:1223-1224) but the `warnings` field is `exclude=True` (dedup/scoring.py:307),
so **per-row warnings never reach the JSON response body or the workbook cells** — except the G1
suppression warning, which is re-surfaced as an `Issues`-sheet row (dedup/scoring.py:492-499).

Probed against the live code (`python -c` over `score_row`, weights.json unmodified):

| Input | Points awarded | Warning |
|---|---|---|
| `account_group="0002"` | `account_group: 15` | — |
| `account_group=2` *(Excel stripped the leading zeros)* | **0 pts** | **none** (silent) |
| `account_group="SHIP2"` | `account_group: 15` | — |
| `account_group="DRID"` *(Bernd's transcript spelling)* | **0 pts** | **none** (silent) |
| `account_group="DBRU"` *(parked code)* | 0 pts | none |
| `sleeping_band="No"` | `sleeping_customer: 15` | — |
| `sleeping_band="3-4"` | `sleeping_customer: 5` | — |
| `sleeping_band=">5"` | 0 pts (the band's own value is 0) | — |
| `sleeping_band=4` *(numeric years)* | **0 pts** | `sleeping_band '4' unrecognized -> 0` |
| `sleeping_band="3-4 years"` | **0 pts** | `sleeping_band '3-4 years' unrecognized -> 0` |
| `customer_status="ACTIVE "` | `customer_status: 10` | — |
| `customer_status="X"` *(SAP block indicator)* | 0 pts | `customer_status 'X' unrecognized -> 0` |
| `last_order_year=2026.0` *(Excel float)* | `sales_order_last_used: 20` | — |
| `last_order_year=2022` | 0 pts | none (a genuine "older" value) |
| `last_order_year="n/a"` | 0 pts | `last_order_year 'n/a' not numeric -> 0` |
| `equipment_count=0` | **`equipment_count: 5`** — band `"0-3"` includes zero | — |
| `equipment_count=None` | **0 pts** | — |
| `orders_in_last_used_year=0` **+** `last_order_year=2026` | `sales_order_count: 5` — band `"0-5"` includes zero | — |
| `orders_in_last_used_year=0`, no year | **0 pts** (G1 gate) | — |
| `sales_org_consolidated="2451"` only | 0 pts (no standalone sales-org tier, and no bonus) | — |
| `sf3="a", sf8="b"` | `salesforce_instance_count: 20` | — |

Three of these are worth a reviewer's attention:

1. **`account_group` fails silently.** It is the one label field with `warn_unknown=False`
   (dedup/scoring.py:907, justified at :903-904 as "an explicit anything-else=0 band"). An Excel
   round-trip that turns `0002` into the integer `2` therefore costs 15 points with no warning,
   no issue row, and nothing in the summary.
2. **`SleepingCustomer` is label-matched, not numerically banded.** `"3-4"` and `">5"` are
   compared as *strings* (dedup/scoring.py:774-777), unlike the numerically-banded criteria. If
   SAP emits a number of years, or `"3-4 years"`, nothing matches and the record forfeits up to
   15 points. There is no test for a numeric `SleepingCustomer`.
3. **Zero is a scoring value; absence is not.** `equipment_count=0` earns 5 points; a blank
   `Equipment_Total_Count` earns 0. Same shape for `sales_order_count`. This is deliberate —
   `test_absence_is_not_activity` (tests/test_scoring.py:208) pins it — but it means a record
   whose extract simply *lacks* the equipment column scores *below* one with a genuine zero.

### 2.3 Signals the code does not read at all

The `ScoringRow` field list (dedup/scoring.py:105-183) is exhaustive. **None** of the following
is read anywhere in `dedup/scoring.py` or `dedup/scoring_xlsx.py`:

- **creation date** (`Created On`) and **created-by** — present in the extract, never bound.
- **last-changed date** — no such column exists in the pipeline at all.
- **SAP partner functions** — not read. The four "partner" fields above are *sales-order
  partner* recency/count, a different thing.
- **contact count / `Contact` / `Email`** — not read. Contacts were on Bernd's criteria question
  list but do not appear in his confirmed table either (§3.4).
- **`Central Deletion Flag` / `Central delivery block`** — not read; blocked status is taken only
  from `CustomerStatus`.
- **`Link ID`** — not read (§1.7).
- **ZFIS** — deliberately absent, per the module docstring (dedup/scoring.py:15-16):
  *"ZFIS is deliberately absent: it is a separate upstream gate that runs before enrichment;
  those records never reach dedup."* Bernd's model lists ZFIS as an override
  ("always survives, never merged or blocked"); see §3.4.

---

## 3. POINT MODEL

### 3.1 Where the weights live, and how they are loaded

`dedup/weights.json` — **verbatim, all 58 lines**:

```json
{
  "_comment": "Golden-record scoring weights. Editable reference table — the scorer never hardcodes points. Band labels: 'a-b' inclusive range, '>n' strictly greater, 'n+' greater-or-equal, bare number exact, 'X/Y' either literal (case-insensitive). Values with no matching band score 0. UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count tiers, account_group DRIT (transcript said DRID; live SAP shows DRIT).",
  "sales_order_last_used": {
    "2026": 20,
    "2025": 15,
    "2024": 10,
    "2023": 5
  },
  "sales_order_count": {
    "0-5": 5,
    "6-10": 15,
    ">10": 25
  },
  "sales_order_partner_last_used": {
    "2026": 20,
    "2025": 15,
    "2024": 10,
    "2023": 5
  },
  "sales_order_partner_count": {
    "0-5": 5,
    "6-10": 15,
    ">10": 25
  },
  "equipment_count": {
    "0-3": 5,
    "4-8": 12,
    "9-15": 20,
    ">15": 30
  },
  "sleeping_customer": {
    "No": 15,
    "3-4": 5,
    ">5": 0
  },
  "customer_status": {
    "active": 10,
    "blocked": 0
  },
  "account_group": {
    "DRIT": 20,
    "0002/SHIP2": 15,
    "0003": 10,
    "0004": 10,
    "0005/MLIEF": 5
  },
  "company_code_count": {
    "1": 5,
    "2-4": 15,
    "5+": 25
  },
  "combined_presence_bonus": {
    "company code AND sales org": 10
  },
  "salesforce_instance_count": {
    "per instance": 10
  }
}
```

**Loader** — `load_weights`, dedup/scoring.py:618-623 (verbatim):

```python
def load_weights(path: Union[str, Path, None] = None) -> dict:
    """Load the scoring weights table (criterion -> {band label: points})."""
    with open(path or WEIGHTS_PATH, encoding="utf-8") as f:
        weights = json.load(f)
    # Metadata keys (e.g. "_comment") are not criteria.
    return {k: v for k, v in weights.items() if not k.startswith("_")}
```

`WEIGHTS_PATH = Path(__file__).parent / "weights.json"` (dedup/scoring.py:43). Note there is
**no guard**: a missing or malformed `weights.json` propagates the exception, contradicting the
docstring's "NEVER raises" (open item 165, §7.2).

**Overrides — three sources, all all-or-nothing.**

| Source | Where | Semantics |
|---|---|---|
| Request body `weights` | `ScoringRequest.weights` (dedup/scoring.py:245-254) → `coerce_weights` (api/routes.py:1338-1344) | valid → applied **wholesale**; malformed → **ignored wholesale**, reason appended to `summary.warnings`. Never a hard error. |
| A `Weights` worksheet in the upload | `_parse_weights_sheet` (dedup/scoring_xlsx.py:89-107), invoked at :204-213 | identical semantics; sheet is matched case-insensitively on the title `weights` (:205). Columns are positional: `Criterion, Band, Points` (:99-101). |
| `CONFIDENCE_MERGE_THRESHOLD` env var | `_resolve_confidence_threshold` (dedup/scoring.py:1004-1017) | not a *weight* — it gates manual_review demotion. Precedence: explicit argument > env > `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` (:48). |

`coerce_weights` — dedup/scoring.py:639-660 (verbatim), the single shared rule:

```python
    parsed: dict = {}
    for criterion, bands in expected.items():
        crit_in = candidate.get(criterion)
        crit_map = crit_in if isinstance(crit_in, dict) else {}
        parsed[criterion] = {}
        for band in bands:
            if band not in crit_map:
                return None, (
                    f"{source} ignored: missing (criterion, band) pair "
                    f"({criterion!r}, {band!r}); using dedup/weights.json"
                )
            points = crit_map[band]
            if isinstance(points, bool) or not isinstance(points, (int, float)):
                try:
                    points = float(str(points).strip())
                except (TypeError, ValueError):
                    return None, (
                        f"{source} ignored: non-numeric Points for "
                        f"({criterion!r}, {band!r}); using dedup/weights.json"
                    )
            parsed[criterion][band] = int(points)
    return parsed, None
```

Every `(criterion, band)` pair in `weights.json` must be present with a numeric `Points`, else
the **whole** candidate is rejected — "a half-applied retune is worse than none" (:635-636).
Points are coerced with `int(points)`, so **fractional weights are silently truncated**
(`0.5` → `0`); nothing warns about this.

**⚠ Note on `CONFIDENCE_MERGE_THRESHOLD` plumbing.** `Settings.confidence_merge_threshold`
exists at config.py:599-601 with the same default, but `grep -n "confidence_merge_threshold"`
across the repo returns **only that definition** — no caller. Both routes call
`elect_golden_records` without the argument (api/routes.py:1347; dedup/scoring_xlsx.py:263), so
the value actually used comes from `os.getenv` inside the scorer (dedup/scoring.py:1008). The
`Settings` field is dead configuration; the env var is live.

### 3.2 Frozen? versioned? hashed?

**Not frozen and not versioned — but hashed into the output.** `weights_version`,
dedup/scoring.py:610-615 (verbatim):

```python
def weights_version(weights: dict) -> str:
    """Stable 12-hex fingerprint of the weights table (sha256 of the canonical
    JSON). Written onto every scored row so a proposal and its later approval
    can be checked for score drift when weights were retuned in between."""
    canonical = json.dumps(weights, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
```

- Computed once per election (dedup/scoring.py:1057) and stamped on **every** row as
  `scored_with_weights_version` (dedup/scoring.py:1182, :1195).
- The fingerprint covers only the criteria dict — `"_comment"` is stripped by `load_weights`, so
  editing the comment does **not** change the version.
- Current value for the checked-in table: **`0a52a681bbff`** (computed live at HEAD 4e33b52).
- **⚠ The drift defence is stamped but never checked.** No code path compares
  `scored_with_weights_version` between a proposal and its approval; `apply_approval`
  (dedup/scoring.py:574-603) does not read the field. This is open item 167 (§7.2).

### 3.3 Value → points, per signal

Two matchers do all the work.

`_match_numeric_band` — dedup/scoring.py:732-751 (verbatim):

```python
    if value is None:
        return 0
    for label, points in bands.items():
        label = label.strip()
        try:
            if label.startswith(">"):
                if value > int(label[1:]):
                    return int(points)
            elif label.endswith("+"):
                if value >= int(label[:-1]):
                    return int(points)
            elif "-" in label.lstrip("-"):
                low, high = label.rsplit("-", 1)
                if int(low) <= value <= int(high):
                    return int(points)
            elif value == int(label):
                return int(points)
        except ValueError:
            logger.warning("Unparseable weights band label %r — skipped", label)
    return 0
```

Bands are tested in **JSON insertion order** and the **first match wins**. With the checked-in
table the bands are disjoint, so order is immaterial; with a caller-supplied overlapping table
it would decide the result (open item 169, §7.2).

`_single_band_value` — dedup/scoring.py:783-785: `int(next(iter(bands.values()), 0))` — takes the
**first** value of a single-band criterion, so the band *label* is decorative for
`combined_presence_bonus` and `salesforce_instance_count`.

| Criterion | Matcher | Mapping (value → points) | Cap / shape |
|---|---|---|---|
| `sales_order_last_used` | numeric, exact | `2026`→20, `2025`→15, `2024`→10, `2023`→5, anything else (incl. `2027`, `2022`, blank)→0 | max 20; **not** a decay function — a hard-coded year ladder |
| `sales_order_count` | numeric, ranges, **G1-gated** | `0–5`→5, `6–10`→15, `>10`→25, negative→0 | max 25; zeroed unless the row owns the cluster's max year |
| `sales_order_partner_last_used` | numeric, exact | same ladder as above | max 20 |
| `sales_order_partner_count` | numeric, ranges, **G1-gated** | `0–5`→5, `6–10`→15, `>10`→25 | max 25; gated on the **partner** year maximum |
| `equipment_count` | numeric, ranges | `0–3`→5, `4–8`→12, `9–15`→20, `>15`→30 | max 30; ungated |
| `sleeping_customer` | **label**, case-insensitive | `"No"`→15, `"3-4"`→5, `">5"`→0, anything else→0 + warning | max 15 |
| `customer_status` | **label** | `"active"`→10, `"blocked"`→0, anything else→0 + warning | max 10; blocked stays **eligible to win** (:897-898) |
| `account_group` | **label**, `X/Y` = either | `DRIT`→20, `0002`\|`SHIP2`→15, `0003`→10, `0004`→10, `0005`\|`MLIEF`→5, anything else→0 **silently** | max 20 |
| `company_code_count` | numeric, ranges, on the **derived** count | `0`→**0** (no band), `1`→5, `2–4`→15, `5+`→25 | max 25 |
| `combined_presence_bonus` | boolean | `company_codes > 0 AND sales_orgs > 0` → 10, else 0 | flat 10; **this is the combined-presence bonus** |
| `salesforce_instance_count` | **multiplier** | `count_of_non_empty_sf_slots × 10` | **uncapped** — 8 slots → up to 80 |

**The G1 recency-dominance gate** — `_award_count`, dedup/scoring.py:792-810 (verbatim):

```python
def _award_count(row_year: Optional[int], cluster_max_year: Optional[int]) -> bool:
    """G1 recency-dominance gate for a sales-order count component.

    Bernd's rule: the count is always "in relation to the year" — it only
    differentiates records sharing the most-recent year, and "does not define
    what is the golden record, it just adds something".

    - A row with no year (None) never receives count points.
    - cluster_max_year is None means context-free scoring (a singleton / unique
      row is trivially its own max) → award (given the row has a year).
    - In a cluster, award only when the row's year equals the cluster maximum.
      (If every member's year is None the maximum is None, but the first guard
      already denies all of them — nobody receives count points.)
    """
    if row_year is None:
        return False
    if cluster_max_year is None:
        return True
    return row_year == cluster_max_year
```

Applied at dedup/scoring.py:855-869 (sales) and :875-889 (partner). When it denies points that
*would* have scored, and a strictly more recent competitor exists, it appends a warning that
becomes a `count_suppressed_by_recency` issue (:865-869, :492-499). The guard at :865 and :884
suppresses the warning for context-free denials, which would only be noise.

### 3.4 Comparison against Bernd's confirmed model

Source: the Notion "Confirmed Scoring Model — Reference Table" (see the note at the top).

| Criterion | Bernd's model | `weights.json` | Match? |
|---|---|---|---|
| Sales order last used | 2026/25/24/23 → 20/15/10/5; **older or empty → 0** | identical (0 by absence of a band) | ✅ |
| Sales order count | `0–5`/`6–10`/`>10` → 5/15/25, described as **"within year, additive booster"** | identical, plus the G1 gate implementing "within year" | ✅ |
| Sales order **partner** last use | "same tiers as sales order last used" → 20/15/10/5/0 | identical | ✅ |
| Sales order **partner count** | **not in the confirmed table.** The table's own footer says: *"'Sales order partner last use' applies the recency tiers; confirm with Bernd if the count tier was intended instead."* | **implemented** as `0-5/6-10/>10` → 5/15/25 | ⚠ **implemented but not in Bernd's model** — flagged `UNCONFIRMED` at dedup/scoring.py:873 and dedup/weights.json:2. Worth up to **25 points**, i.e. 12.5% of the non-Salesforce maximum. |
| Equipment count | `0–3`/`4–8`/`9–15`/`>15` → 5/12/20/30 | identical | ✅ |
| Sleeping customer | `No`→15, `3–4 years`→5, `>5 years`→0 | identical **labels**, but matched as literal strings — Bernd's wording is "3–4 years" / ">5 years" (§2.2 item 2) | ⚠ band labels agree; the *cell format* they expect is unconfirmed |
| Active / blocked | active→10, blocked→0; "blocked stays eligible to be proposed golden" | identical, and eligibility is preserved (dedup/scoring.py:897-898) | ✅ |
| Account group | **`DRID`**→20, `002 (SHIP2)`→15, `0003`→10, `0004`→10, `0005 (MLIEF)`→5 | **`DRIT`**→20, rest identical | ⚠ **DRIT vs DRID.** weights.json:2 records the reason: *"transcript said DRID; live SAP shows DRIT"*. The live fixture data confirms `DRIT` is what SAP emits (see §9); a record carrying `DRID` scores **0 silently**. |
| Company code count | `1`→5, `2–4`→15, `5+`→25 | identical | ✅ |
| Sales org | "combined-presence only (no standalone tier)" | no standalone tier | ✅ |
| **Combined-presence rule** | *"a record with both a company code and a sales org entry **ranks above** company-code-only. One rule, not double-counted."* — stated **ordinally**, with **no point value** | implemented as a **flat +10 additive bonus** | ⚠ **Semantics differ.** Bernd specifies a rank ordering; the code buys it with 10 points, which a large enough company-code or equipment difference can outweigh. The value is flagged `UNCONFIRMED` at dedup/scoring.py:912 and weights.json:2. |
| Salesforce instance count | "+10 each per instance referencing the SAP ID"; illustrated as biosystems only→10, biosystems+AXS→20 | `count × 10` over **all 8** slots | ✅ for the rule; the code generalises past Bernd's two illustrated slots |
| **ZFIS override** | *"always survives, never merged or blocked, no standardization applied"* | **not implemented in scoring** — declared out of scope as an upstream gate (dedup/scoring.py:15-16) | ⚠ **in Bernd's model, not in the scorer.** Whether the upstream gate exists is not verifiable from this repository. |
| **Contact count** | on Bernd's *question* list ("equipment, sales orders last year, sales orders last 3 years, **contacts**, company codes, sales orgs"); **absent** from the confirmed table | not implemented | ✅ consistent with the confirmed table |
| **"Sales orders last 3 years"** | on the question list; the confirmed table replaced it with the year ladder | not implemented as a separate criterion | ✅ consistent |
| DBRU / Dios account groups | "parked for now" | not scored; the silent-zero path covers them (dedup/scoring.py:903-904) | ✅ |

**Signals implemented but not in Bernd's model:** `sales_order_partner_count` (up to 25 pts).
**Signals in Bernd's model but not implemented:** the ZFIS override; the combined-presence rule
as an *ordinal* rather than an additive bonus.

### 3.5 Combination rule

**A plain unweighted sum of the 11 per-criterion point values.** No weighted sum, no
lexicographic ordering at the *scoring* stage (lexicographic ordering appears only in the
tie-break, §4.2), and **no normalisation to 0–100**.

`_Scored.__init__` — dedup/scoring.py:971-975:

```python
        self.row = row
        self.breakdown, self.warnings = score_row(
            row, weights, cluster_max_year, cluster_max_partner_year
        )
        self.total = sum(self.breakdown.values())
```

`score_final` is that sum (dedup/scoring.py:1176, :1187), and the workbook writes it as a plain
value that "equals the sum of the written `score_*` cells by construction"
(dedup/scoring_xlsx.py:284-286). `test_score_equals_breakdown_sum`
(tests/test_scoring.py:544-557) pins the identity.

**Bonus for combined presence:** yes, the one flat `+10` at dedup/scoring.py:913-917 — the only
cross-signal interaction in the model. Bernd's table notes it is deliberately "not
double-counted" (sales org has no tier of its own), and the code honours that.

**Theoretical maximum** with the checked-in table:
`20 + 25 + 20 + 25 + 30 + 15 + 10 + 20 + 25 + 10 = 200`, plus `10 × n_salesforce_slots`
(up to 80) = **280**. Bernd's own note records that he *"said '%' throughout but retracted it: a
score, not a percentage"* — consistent with the absence of normalisation. A reviewer comparing
`score_final` values across records should note the scale is **unbounded in practice** because
the Salesforce term is a multiplier, not a band.

---

## 4. GOLDEN RECORD ELECTION

### 4.1 `elect_golden_records` — dedup/scoring.py:1054-1152 (verbatim body)

```python
    if weights is None:
        weights = load_weights()
    threshold = _resolve_confidence_threshold(confidence_threshold)
    wv = weights_version(weights)

    duplicates = sorted(
        rid for rid, n in Counter(r.row_id for r in rows).items() if n > 1
    )
    if duplicates:
        raise DuplicateRowIdError(duplicates)

    # G1: count criteria are cluster-context-dependent, so compute each real
    # cluster's most-recent year BEFORE scoring, then score every row against
    # its cluster's maxima. Single-member clusters (which degrade to unique) and
    # uncluster rows are scored context-free (their own year is the max).
    rows_by_cluster: Dict[str, List[ScoringRow]] = {}
    for row in rows:
        if row.cluster_id is not None:
            rows_by_cluster.setdefault(row.cluster_id, []).append(row)
    cluster_maxima: Dict[str, Tuple[Optional[int], Optional[int]]] = {
        cid: _cluster_year_maxima(members)
        for cid, members in rows_by_cluster.items()
        if len(members) >= 2
    }

    scored = [
        _Scored(row, weights, *cluster_maxima.get(row.cluster_id, (None, None)))
        for row in rows
    ]

    clusters: Dict[str, List[_Scored]] = {}
    for s in scored:
        if s.row.cluster_id is not None:
            clusters.setdefault(s.row.cluster_id, []).append(s)

    # A content-hash cluster_id whose present members don't reproduce the hash
    # is a PARTIAL cluster: some members were submitted in a different score
    # call (e.g. split across blocks). Warn — never fail — and elect within
    # what's seen. (Test/JSON ids like "C1" aren't hashes and never trip this.)
    partial_clusters: set[str] = set()
    for cid, members in clusters.items():
        if cid.startswith(CLUSTER_ID_PREFIX) and cluster_hash(
            m.row.row_id for m in members
        ) != cid:
            partial_clusters.add(cid)

    winner_by_cluster: Dict[str, str] = {}
    manual_review_clusters: set[str] = set()
    for cluster_id, members in clusters.items():
        if len(members) < 2:
            continue  # single-member cluster degrades to unique below
        numeric_ids = all(_parses_as_int(m.row.row_id) for m in members)
        winner = min(members, key=lambda m: _tiebreak_key(m, numeric_ids))
        winner_by_cluster[cluster_id] = winner.row.row_id
        # A cluster is demoted to manual_review when, in precedence order:
        #   1. clustering already routed a member to manual_review (INHERITED —
        #      election can never upgrade upstream uncertainty),
        #   2. every member is blocked (a human confirms before a block), or
        #   3. the merge confidence is below threshold.
        # Any one is sufficient; election never leaves such a cluster proposed.
        inherited_mr = any(
            _norm_routing(m.row.routing) == "manual_review" for m in members
        )
        all_blocked = all(_normalized_status(m.row) == "blocked" for m in members)
        merge_conf = _cluster_merge_confidence(members)
        low_confidence = merge_conf is not None and merge_conf < threshold
        # A zero-signal election (every member scored 0) has no basis to pick a
        # winner beyond the tie-break — it must not look confident.
        zero_signal = all(m.total == 0 for m in members)
        if inherited_mr or all_blocked or low_confidence or zero_signal:
            manual_review_clusters.add(cluster_id)

    results: List[ScoringResultRow] = []
    for s in scored:
        cluster_id = s.row.cluster_id
        winner_id = winner_by_cluster.get(cluster_id) if cluster_id else None
        if winner_id is None:
            # No cluster, or a degraded single-member cluster. Normally unique —
            # but a row clustering flagged manual_review stays manual_review
            # (election never upgrades uncertainty into a confident unique).
            lone_status = (
                "manual_review"
                if _norm_routing(s.row.routing) == "manual_review"
                else "unique"
            )
            # A lone row is its own proposed winner.
            result = _build_result(s, lone_status, s.row.row_id, wv)
        else:
            status = (
                "manual_review" if cluster_id in manual_review_clusters else "proposed"
            )
            result = _build_result(s, status, winner_id, wv)
        if cluster_id in partial_clusters:
            result.warnings = [
                *result.warnings,
                f"partial_cluster: submitted rows are a subset of {cluster_id}",
            ]
        results.append(result)
    return results
```

Four independent demotion triggers, each sufficient on its own: **inherited manual_review**,
**all-blocked**, **low confidence**, **zero signal**. Results are returned in input order
(:1126-1152).

### 4.2 Tie-break ordering

`_tiebreak_key` — dedup/scoring.py:939-955 (verbatim):

```python
def _tiebreak_key(scored: "_Scored", numeric_ids: bool):
    """Sort key: best candidate first, deterministic and order-independent.

    UNCONFIRMED ordering (confirm with Bernd): total score, most recent
    last_order_year, equipment_count, company_code_count, then LOWEST row_id
    — compared numerically when every row_id in the cluster parses as an
    integer, else lexically. row_id is the final uniqueness guarantee, so
    the winner is invariant under input shuffling.
    """
    row_key = int(scored.row.row_id) if numeric_ids else scored.row.row_id
    return (
        -scored.total,
        -(scored.last_year if scored.last_year is not None else -1),
        -(scored.equipment if scored.equipment is not None else -1),
        -scored.company_codes,
        row_key,
    )
```

The winner is `min(members, key=...)` (dedup/scoring.py:1106) — negation makes "highest first".

| Step | Field | Direction | Missing value | Cited |
|---|---|---|---|---|
| 1 | `score_final` (the summed total) | highest wins | never missing (0 at worst) | :950 |
| 2 | `last_order_year` (raw, **not** the points) | most recent wins | `None` → treated as **−1**, so it sorts **behind every real year including 0** | :951, :977 |
| 3 | `equipment_count` (raw count, **not** the points) | highest wins | `None` → **−1**, sorts last | :952, :978 |
| 4 | `company_code_count` (derived count, **not** the points) | highest wins | always an int ≥ 0 — a blank consolidated cell is 0, indistinguishable from a genuine 0 | :953, :979 |
| 5 | `row_id` | **LOWEST wins** | never missing (required field) | :948, :954 |

**Step 5 is a total order, so a full tie is impossible.** `row_id` is `Field(...)` — required
(dedup/scoring.py:105) — and a duplicate `row_id` in one request raises `DuplicateRowIdError`
before election (:1059-1063). The winner is therefore **deterministic and independent of input
order**, which `test_winner_invariant_under_shuffle` (tests/test_scoring.py:499) pins.

**Numeric vs lexical `row_id`:** decided per cluster by
`numeric_ids = all(_parses_as_int(m.row.row_id) for m in members)` (dedup/scoring.py:1105). A
cluster mixing `"13000001"` and `"BP-7"` falls back to **lexical** comparison for the whole
cluster — so `"13000001" < "BP-7"` by string order. `test_mixed_numeric_and_lexical_row_ids_no_raise`
(tests/test_scoring.py:1151) pins that it does not raise; it does not pin which ordering a
business reviewer would expect.

**⚠ The whole ordering is marked `UNCONFIRMED` in the code** (dedup/scoring.py:942). Bernd's
proposed ordering was posed as a *question*, not confirmed: *"Tie-break when two records score
equal: preferred rule (most recent activity, then most company codes, then lowest BP number)?"*
The implemented order inserts **`equipment_count` as step 3**, which is not in that proposal.

**Tie-breaks are observable.** When ≥2 members share the top score, `detect_issues` emits a
`tiebreak_decided` issue naming the count (dedup/scoring.py:523-529) — note it fires on a shared
*top score* whether or not the tie-break actually changed the outcome.

### 4.3 How the golden fields are derived

`_build_result` — dedup/scoring.py:1170-1197 (verbatim):

```python
    row_id = s.row.row_id
    company_code_count, sales_org_count, salesforce_instance_count = derived_counts(
        s.row
    )
    if election_status == "unique":
        return ScoringResultRow(
            row_id=row_id, cluster_id=s.row.cluster_id, score=s.total,
            company_code_count=company_code_count,
            sales_org_count=sales_org_count,
            salesforce_instance_count=salesforce_instance_count,
            is_golden_record=True, golden_record_id=row_id,
            proposed_golden_id=None, election_status="unique",
            approval_status=None, scored_with_weights_version=wv,
            score_breakdown=s.breakdown, warnings=s.warnings,
        )
    is_winner = row_id == winner_id
    return ScoringResultRow(
        row_id=row_id, cluster_id=s.row.cluster_id, score=s.total,
        company_code_count=company_code_count,
        sales_org_count=sales_org_count,
        salesforce_instance_count=salesforce_instance_count,
        is_golden_record=is_winner,
        golden_record_id=row_id if is_winner else winner_id,
        proposed_golden_id=winner_id,
        election_status=election_status,  # "proposed" | "manual_review"
        approval_status="proposed", scored_with_weights_version=wv,
        score_breakdown=s.breakdown, warnings=s.warnings,
    )
```

| Row kind | `is_golden_record` | `golden_record_id` | `proposed_golden_id` | `election_status` | `approval_status` |
|---|---|---|---|---|---|
| Unique (no cluster, or degraded single-member) | `True` | **self** | `None` | `unique` | `None` |
| Cluster winner, confident | `True` | **self** | self | `proposed` | `proposed` |
| Cluster loser, confident | `False` | **winner's id** | winner's id | `proposed` | `proposed` |
| Cluster member, demoted — **JSON** | computed (`True`/`False`) | computed | winner's id | `manual_review` | `proposed` |
| Cluster member, demoted — **workbook** | **blank** | **blank** | winner's id | `manual_review` | `proposed` |
| Lone row routed manual_review upstream | `True` | self | **self** | `manual_review` | `proposed` |

**The JSON/file divergence is deliberate**, and is documented in the docstring at
dedup/scoring.py:1165-1169. The blanking happens only in the writeback,
dedup/scoring_xlsx.py:291-298 (verbatim):

```python
        # A manual_review row leaves is_golden_record / golden_record_id EMPTY —
        # nobody filtering is_golden_record alone may act on an unreviewed row.
        # The computed winner survives in proposed_golden_id.
        is_mr = result.election_status == "manual_review"
        ws.cell(row=ws_row, column=golden_col,
                value=None if is_mr else result.is_golden_record)
        ws.cell(row=ws_row, column=golden_id_col,
                value=None if is_mr else result.golden_record_id)
```

**Consumption contract** (dedup/scoring.py:266-268):

> PHASE 3 CONTRACT: consume ONLY rows with `approval_status == "approved"` or
> `election_status == "unique"`. Everything else is a proposal awaiting human sign-off.

**Promotion on approval** — `apply_approval`, dedup/scoring.py:590-603 (verbatim):

```python
    out: List[ScoringResultRow] = []
    updated: List[str] = []
    for r in rows:
        if r.cluster_id != cluster_id:
            out.append(r)
            continue
        r2 = r.model_copy()
        r2.approval_status = decision  # type: ignore[assignment]
        if decision == "approved" and r2.proposed_golden_id is not None:
            r2.is_golden_record = r2.row_id == r2.proposed_golden_id
            r2.golden_record_id = r2.proposed_golden_id
        out.append(r2)
        updated.append(r2.row_id)
    return out, updated
```

On `"rejected"` the golden fields are left untouched — only `approval_status` changes. The
function never mutates its inputs, and raises `ClusterNotFoundError` (→ HTTP 404,
api/routes.py:1386-1387) when no row carries the id.

### 4.4 `survivor` / `merge_into` columns

**There are none.** `grep -rn "survivor\|merge_into\|survivorship" api/ dedup/ sql/ tests/`
returns only a docstring (dedup/scoring.py:261) and a test comment
(tests/test_scoring.py:538). The survivor relationship is expressed **entirely** through
`golden_record_id` / `proposed_golden_id` pointing from loser to winner.

### 4.5 Attribute survivorship

**Not implemented.** Election is purely id-level: it decides *which row wins*, never *which
field values the winner ends up with*. There is no per-field rule, no field composition across
cluster members, and no code that copies an attribute from a loser onto a winner —
`_build_result` (dedup/scoring.py:1155-1197) writes only `row_id`, `cluster_id`, the score, the
three derived counts, the election/approval fields and the breakdown. The loser rows are
returned unchanged apart from their own election columns.

The workbook consequence: after `/api/dedup/score/file`, the winner row still carries **only its
own** `Name 1`, address, `Company_Code_Consolidated`, and so on. Bernd's model anticipates the
opposite for at least two fields — the Notion notes describe "extending the Golden Record to all
the obsolete record's sales orgs and company codes" as a downstream step, and that page's §5
explicitly asks which of those steps are in thesis scope. **Nothing in this repository performs
that extension.**

---

## 5. OUTPUT

### 5.1 Columns emitted

The column names are defined **once** and shared by both consumers —
`SCORE_BREAKDOWN_COLUMNS`, dedup/scoring.py:59-71 (verbatim):

```python
SCORE_BREAKDOWN_COLUMNS: Dict[str, str] = {
    "sales_order_last_used": "score_SalesOrderLastUsed",
    "sales_order_count": "score_SalesOrderCount",
    "sales_order_partner_last_used": "score_SalesOrderPartnerLastUsed",
    "sales_order_partner_count": "score_SalesOrderPartnerCount",
    "equipment_count": "score_EquipmentCount",
    "sleeping_customer": "score_SleepingCustomer",
    "customer_status": "score_CustomerStatus",
    "account_group": "score_AccountGroup",
    "company_code_count": "score_CompanyCodeCount",
    "combined_presence_bonus": "score_CombinedPresence",
    "salesforce_instance_count": "score_SalesforceInstances",
}
```

plus dedup/scoring_xlsx.py:64-68:

```python
DERIVED_COLUMNS = ("Company_Code_Count", "Sales_Org_Count", "Salesforce_Instance_Count")
ELECTION_COLUMNS = (
    "is_golden_record", "golden_record_id", "proposed_golden_id",
    "election_status", "approval_status",
)
```

All 21 columns, with a real example row (dumped live from `ScoringResultRow.model_dump(by_alias=True)`
at HEAD 4e33b52 for a fully-populated record):

| # | Column | Python type | SQL type (`usp_merge_validation_scores`) | Example |
|---|---|---|---|---|
| — | `Customer` | `str` | `NVARCHAR(100)` | `"13343787"` |
| — | `Cluster ID` | `Optional[str]` | (written by the clusters proc) | `"c_44a6d199e643"` |
| 1 | `score_final` | `int` | **`FLOAT`** | `147` |
| 2 | `Company_Code_Count` | `int` | `INT` | `2` |
| 3 | `Sales_Org_Count` | `int` | `INT` | `1` |
| 4 | `Salesforce_Instance_Count` | `int` | `INT` | `2` |
| 5 | `is_golden_record` | `bool` | `BIT` | `true` (blank for manual_review in the workbook) |
| 6 | `golden_record_id` | `Optional[str]` | `NVARCHAR(100)` | `"13343787"` |
| 7 | `proposed_golden_id` | `Optional[str]` | `NVARCHAR(100)` | `null` for unique |
| 8 | `election_status` | `Literal["proposed","manual_review","unique"]` | `NVARCHAR(30)` | `"unique"` |
| 9 | `approval_status` | `Optional[Literal["proposed","approved","rejected"]]` | `NVARCHAR(30)` | `null` for unique |
| 10 | `scored_with_weights_version` | `Optional[str]` (12 hex) | `NVARCHAR(50)` | `"0a52a681bbff"` |
| 11 | `score_SalesOrderLastUsed` | `int` | **`FLOAT`** | `20` |
| 12 | `score_SalesOrderCount` | `int` | **`FLOAT`** | `25` |
| 13 | `score_SalesOrderPartnerLastUsed` | `int` | **`FLOAT`** | `0` |
| 14 | `score_SalesOrderPartnerCount` | `int` | **`FLOAT`** | `0` |
| 15 | `score_EquipmentCount` | `int` | **`FLOAT`** | `12` |
| 16 | `score_SleepingCustomer` | `int` | **`FLOAT`** | `15` |
| 17 | `score_CustomerStatus` | `int` | **`FLOAT`** | `10` |
| 18 | `score_AccountGroup` | `int` | **`FLOAT`** | `20` |
| 19 | `score_CompanyCodeCount` | `int` | **`FLOAT`** | `15` |
| 20 | `score_CombinedPresence` | `int` | **`FLOAT`** | `10` |
| 21 | `score_SalesforceInstances` | `int` | **`FLOAT`** | `20` |

Note the type asymmetry: the scorer only ever produces **integers** (`int(points)` at
dedup/scoring.py:659, `int(...)` throughout `_match_numeric_band`), but the SQL procedure
declares every score column `FLOAT`. Harmless today; it means a fractional weight retune would
survive the SQL round-trip while being truncated in Python (§3.1).

**The JSON and the workbook carry identical column names.** The JSON model's `@computed_field`
aliases (dedup/scoring.py:328-381) flatten `score_breakdown` into exactly the `score_*` headers
the file writes, and `ScoringResultRow` uses the file headers as field aliases
(dedup/scoring.py:277-300) — the docstring at :271-274 states this is the point.

### 5.2 The SQL merge procedures

| Procedure | Target | Reads scoring columns? |
|---|---|---|
| `dbo.usp_merge_validation_scores` (sql/usp_merge_validation_scores.sql:1) | `test_77.Validation` | **yes — all 21**, via `OPENJSON(@payload, '$.rows')`, matched `ON tgt.Customer = src.Customer` |
| `dbo.usp_merge_validation_clusters` (sql/usp_merge_validation_clusters.sql:1) | `test_77.Validation` | no — writes `Block ID`, `Cluster ID`, `Routing`, `Signature ID`, `Confidence`, `Reasoning` |
| `dbo.usp_merge_legacy_enriched` (sql/usp_merge_legacy_enriched.sql:1) | `dp_legacy.test_77.Legacy` | **no.** Its `OPENJSON` `WITH` clause binds only the Phase 1 enrichment fields (`Name 1..4`, `Domain`, `Department Domain`, `Search Term 1/2`, `Care Of`, `Contact`, `Email`, the address block, `Record Type`, `ROR ID`, `LEI ID`, `Flag for Review`, `Flag Reason`). **No `score_*`, no `is_golden_record`, no `election_status`.** |

So the task's phrase "the `usp_merge_legacy_*` inputs" does not apply: **scoring output never
reaches `usp_merge_legacy_enriched`.** It lands in `test_77.Validation` via
`usp_merge_validation_scores`. `usp_merge_validation_scores` is also an unconditional
`WHEN MATCHED THEN UPDATE` with no `WHEN NOT MATCHED` branch — a scored `Customer` absent from
`Validation` is silently dropped.

### 5.3 Which columns the four-eyes approval reads

**Not determinable from this repository.** Per docs/thesis/06b_CROSSCUTTING.md:1089-1136, the
four-eyes control is enforced *structurally* in code but the human step lives in DATAshaper:

> **Where the control actually lives: in DATAshaper, by process.** The human step is the DS
> deduplication view's `Leading Code` selector and `Apply Leading Code` action […] which is where
> a steward reviews the proposed cluster and the adjudicator's free-text `Reason`.
> `/api/dedup/approve` is the API counterpart of that button. Whatever identity, authorisation,
> and audit exist for the approval are **DATAshaper's**, in the Tillit tenant, and are not
> repository artefacts — ⚠ NOT EVIDENCED.

What the API-side control **does** enforce, all cited in that section:

| Control | Mechanism | Cited |
|---|---|---|
| Election never auto-commits | winner goes to `proposed_golden_id`, not to the golden fields | dedup/scoring.py:1046-1047, :1100-1119 |
| Unreviewed rows are structurally inert | manual_review leaves `is_golden_record` / `golden_record_id` **empty** in the workbook | dedup/scoring.py:262-264; dedup/scoring_xlsx.py:291-298 |
| Promotion only on explicit approval | `apply_approval` promotes only when `decision == "approved"` | dedup/scoring.py:597-600 |
| An approver must be named | `approver: str = Field(..., min_length=1)` | dedup/scoring.py:560 |

And what it does **not** (same source): the approver is unauthenticated, **is not used by the
logic at all** (never passed to `apply_approval`, never written to a row field — only logged at
api/routes.py:1378-1381 and echoed at :1392), separation of duties is not checked, no approval is
persisted, and therefore no audit trail exists. A reviewer should treat `approval_status` in the
output as *"someone asserted this"*, not as an authenticated record.

The columns a steward would need in order to review — and which the workbook does supply — are
`Cluster ID`, `proposed_golden_id`, `score_final`, the 11 `score_*` columns, plus the
clustering-stage `Confidence` and `Reasoning`.

### 5.4 Score explanation

**Yes — a full per-signal breakdown is emitted, in three places.**

1. **11 `score_*` columns**, one per criterion, on every row. The breakdown always carries
   every criterion key, 0 where nothing matched, so the columns are stable
   (dedup/scoring.py:820-822: *"The breakdown always carries every criterion key (0 where
   nothing matched) so the audit trail and the file writeback are column-stable"*). Written at
   dedup/scoring_xlsx.py:282-283; serialized in JSON via the computed fields at
   dedup/scoring.py:328-381. `score_final` is their sum by construction.
2. **An `Issues` sheet** (dedup/scoring_xlsx.py:305-315), rebuilt fresh on every run, with
   columns `row_id, cluster_id, issue_type, detail` (:31). In the JSON response the same list
   is `ScoringResponse.issues` (dedup/scoring.py:441). The eight recognised types
   (dedup/scoring.py:403-412):

   | `issue_type` | Level | Meaning | Emitted at |
   |---|---|---|---|
   | `verdict_contradiction` | row | persisted `Reasoning` argues against its own merge | :474-479 |
   | `candidate_cap_exceeded` | block | the adjudicator's residue pass blew the per-block cap | :480-489 |
   | `count_suppressed_by_recency` | row | **the G1 gate zeroed a count component** — the per-signal explanation for a lost count | :492-499 |
   | `low_confidence_merge` | cluster | `min` member confidence below threshold | :512-517 |
   | `all_blocked_cluster` | cluster | every member blocked | :518-522 |
   | `tiebreak_decided` | cluster | ≥2 members share the top score | :523-529 |
   | `empty_scoring_payload` | cluster | every member scored 0 — "winner decided by tie-break only" | :530-534 |
   | `missing_building_inconsistency` | — | **declared but never emitted** — reserved for the Phase 1 building differentiator (:399-402) |
3. **`scored_with_weights_version`** on every row, so a breakdown can be tied back to the exact
   weights table that produced it (§3.2).

**What is *not* emitted:** the per-row coercion `warnings` list. It is `exclude=True`
(dedup/scoring.py:307) and survives only as the `summary.rows_with_warnings` counter
(:1223-1224). So a record that lost 15 points because its `Account group` arrived as the integer
`2` (§2.2) produces *no* row-level artefact at all — not even a warning, since `account_group`
uses `warn_unknown=False`.

---

## 6. DETERMINISM

### 6.1 Date-relative signals: there are none

**No signal is relative to "today".** `grep -n "datetime\|today\|now()\|date\.\|random\|uuid"
dedup/scoring.py dedup/scoring_xlsx.py` returns **two false positives only** — the substrings
inside the words "candidate" (:641) and "Overridable" (:46). Neither module imports `datetime`,
`time`, or `random`.

Recency is expressed as a **hard-coded calendar-year ladder** in the weights table
(`"2026": 20, "2025": 15, "2024": 10, "2023": 5`), matched by exact equality against the
record's `Sales_Order_Last_Used` value (`_match_numeric_band`, dedup/scoring.py:747-748). It is
**not** a decay function and **not** a "months since" computation.

Consequences a reviewer should note:

- **Two runs on different days produce identical scores and identical winners.** There is
  nothing to freeze or inject, because nothing is read from the clock.
- **The ladder does not roll forward.** As of 2027 every record scores **0** on both recency
  criteria until someone edits `weights.json` — and because a count is gated on owning the
  cluster's most recent year (not on scoring points), the count criteria would keep working
  while both recency criteria flatlined. The table is a manual retune, and the fingerprint
  (§3.2) is the only signal that it changed.
- **A future year is worth nothing.** `2027` matches no band → 0 points, silently, exactly like
  `2019`.

Because the ladder is data rather than code, this is a `weights.json` maintenance obligation,
not a code change — which is the stated design intent (dedup/scoring.py:4-6: *"pure arithmetic
over an editable weights table … so it can be re-run on retuned weights"*).

### 6.2 Other determinism properties

| Property | Status | Cited |
|---|---|---|
| Winner independent of input row order | **yes** — `row_id` is the total-order tie-break | dedup/scoring.py:939-955; tests/test_scoring.py:499 |
| Results returned in input order | yes | dedup/scoring.py:1126-1152 (`for s in scored`) |
| Cluster iteration order affects anything? | no — winners are computed per cluster independently, and `min` over a total order | :1102-1107 |
| Band iteration order affects anything? | **only with an overlapping caller-supplied table**; the checked-in bands are disjoint | :734-748; open item 169 |
| Re-running the same workbook | byte-stable scoring/election cells | tests/test_scoring.py:1216 `test_rerun_is_deterministic_end_to_end` |
| Weights retune changes the outcome | yes, and changes the fingerprint | tests/test_scoring.py:1164 `test_weights_retune_flips_winner_and_changes_version` |
| Environment-dependent behaviour | **one**: `CONFIDENCE_MERGE_THRESHOLD` changes which clusters are demoted to manual_review — it never changes scores or winners | dedup/scoring.py:1004-1017, :1119 |

### 6.3 LLM involvement in scoring: none

**Confirmed by grep.** `grep -n "llm\|LLM\|openai\|Azure\|http\|requests" dedup/scoring.py
dedup/scoring_xlsx.py` returns **five matches, all of them prose in docstrings or comments**
(dedup/scoring.py:3, :6, :47, :1024; dedup/scoring_xlsx.py:224) explaining that the LLM is *not*
used here. There is **no import** of `dedup.llm`, `llm.openai_client`, `openai`, `httpx` or
`requests` in either module, and no network call of any kind.

The module docstring states the boundary (dedup/scoring.py:3-7):

> Separate from the LLM adjudicator on purpose: clustering and election have different inputs,
> cadences, and cost profiles. Election is pure arithmetic over an editable weights table
> (`dedup/weights.json`), so it can be re-run on retuned weights without paying for LLM
> adjudication again. **No LLM, no network — ever.**

The scorer *consumes* two LLM-derived values — `Confidence` and `Reasoning`, written earlier by
the adjudicator — but only to route (`manual_review` demotion, dedup/scoring.py:1118-1119) and to
raise issues (`verdict_contradiction`, :474-479). **Neither ever contributes a point.** No
counterpart to the clustering dossier's §4.6 (prompts, sampling parameters, model id) exists or
is needed here.

---

## 7. KNOWN GAPS

### 7.1 In-code markers

Every `TODO` / `UNCONFIRMED` / "confirm with Bernd" note in the scoring modules
(`grep -n "UNCONFIRMED\|Bernd\|TODO\|⚠\|OPEN ITEM"`):

| Location | Marker (verbatim) |
|---|---|
| dedup/weights.json:2 | `"UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count tiers, account_group DRIT (transcript said DRID; live SAP shows DRIT)."` |
| dedup/scoring.py:873 | `# UNCONFIRMED: partner count tiers mirror sales order count. CONFIRM w/ Bernd.` |
| dedup/scoring.py:912 | `# UNCONFIRMED bonus value; sales org has no standalone tier.` |
| dedup/scoring.py:942-946 | `UNCONFIRMED ordering (confirm with Bernd): total score, most recent last_order_year, equipment_count, company_code_count, then LOWEST row_id …` |
| dedup/scoring_xlsx.py:42-43 | `# OPEN ITEM P2-21: confirm the click-report column layout actually supplies a within-year count here (not a lifetime total) before go-live.` |
| dedup/scoring_xlsx.py:46 | `# G1: within-year partner count (same P2-21 caveat as above).` |
| dedup/scoring.py:399-402 | `missing_building_inconsistency` "is reserved for the upstream building differentiator (Phase 1); it is a declared type here but not emitted from election" |

**P2-21 is the largest single risk in the model.** The column is named
`Sales_Order_Total_Count` but is *interpreted* as a within-year count. If the click report
actually supplies a lifetime total, the G1 gate still fires correctly (it keys on the year, not
the count), but the *band* a record lands in would be wrong — a long-lived customer with 40
lifetime orders and 2 orders this year would score `>10` → 25 instead of `0-5` → 5. **There is
no assertion, no validation, and no warning anywhere that checks this.**

### 7.2 Open items in `docs/thesis/00_OPEN_ITEMS.md` that touch scoring

| # | Status | Item | Line |
|---|---|---|---|
| 81 | ⚠ UNVERIFIED | Recall of the seven contradiction markers (dedup/scoring.py:444-451) against real adjudicator phrasing | 00_OPEN_ITEMS.md:455 |
| 165 | ⚠ NO FIXTURE COVERAGE | A missing or corrupt `weights.json`; `load_weights` propagates with no guard, against a docstring promising scoring "NEVER raises" | :568 |
| 166 | ⚠ NO FIXTURE COVERAGE | A criterion-less weights dict passed directly to `score_row`, raising `KeyError` | :569 |
| 167 | ⚠ UNVERIFIED (not implemented) | No code path compares `scored_with_weights_version` at approval time — the documented drift defence does not run | :570 |
| 168 | ⚠ NO FIXTURE COVERAGE | Duplicated headers in the scoring workbook, which silently bind to the first column | :571 |
| 169 | ⚠ UNVERIFIED (no fixture) | Caller-supplied weights with overlapping numeric bands, where points depend on insertion order | :572 |

**Correction to the task's premise.** The four items the task names — combined-presence bonus,
partner-count tiers, tie-break ordering, DRIT vs DRID — are **not** in `00_OPEN_ITEMS.md`
(`grep -n "combined_presence\|tie-break\|tiebreak\|partner_count"` over that file returns
nothing). They are tracked in **two other places**:

- **In code**, as the `UNCONFIRMED` markers in §7.1 above.
- **In Notion**, as one action item on the "Monthly Update — August 2026" page
  (`app.notion.com/p/3ca109a5c461816eba79f747939ee6a6`, dated 2026-08-28): *"Sign-off on the four
  open scoring values — Bernd — Combined presence bonus amount, sales order partner count tiers,
  tie-break ordering, and DRIT vs. DRID."*

They are also documented in the thesis chapters (docs/thesis/04_PARAMETERS.md:827, :878, :995;
05_DATA_MODEL.md:412-418; 07_EVALUATION.md:708; 08_GAPS.md:806, :1212; 09_DECISIONS.md:1167).
So: **tracked, but not in the file the task expected.**

### 7.3 Gaps visible in code but not in any doc

1. **`Link ID` is invisible to scoring** (§1.7). No open item covers it; the column was added by
   the v2 clustering work (api/routes.py:1151) and `dedup/scoring_xlsx.py` was not extended.
2. **`Settings.confidence_merge_threshold` is dead configuration** (config.py:599-601, no
   reader) while the live value comes from `os.getenv` inside the scorer (§3.1). A deployment
   that sets the value through `Settings` rather than the environment would silently get 0.95.
3. **`account_group` mismatches are completely silent** (§2.2) — no warning, no issue, no
   summary counter.
4. **`SleepingCustomer` is string-matched**, so a numeric or "3-4 years" cell forfeits points
   with only an internal warning that is never emitted (§2.2, §5.4).
5. **Fractional weights are truncated** by `int(points)` (dedup/scoring.py:659) with no warning.
6. **`usp_merge_validation_scores` has no `WHEN NOT MATCHED` branch** — a scored `Customer`
   absent from `test_77.Validation` is silently dropped (§5.2).

### 7.4 Tests that pin current behaviour — `tests/test_scoring.py` (1418 lines, 88 tests)

**Bands and coercion — `TestBands` (:70), `TestCoercion` (:172), `TestDerivedCounts` (:219)**

| Test | Intent |
|---|---|
| `test_sales_order_last_used` :75 | year ladder → 20/15/10/5/0 |
| `test_sales_order_count` :81 | count bands 0-5/6-10/>10 |
| `test_partner_last_used` :92 | partner year ladder |
| `test_partner_count` :101 | partner count bands |
| `test_equipment_count` :112 | equipment bands 0-3/4-8/9-15/>15 |
| `test_sleeping_bands` :118 | No/3-4/>5 → 15/5/0 |
| `test_customer_status` :124 | active→10, blocked→0 |
| `test_account_group` :133 | DRIT/0002/SHIP2/0003/0004/0005/MLIEF |
| `test_company_code_count` :143 | 1/2-4/5+ bands off the split |
| `test_combined_presence_bonus` :149 | +10 only when both codes and orgs present |
| `test_salesforce_instances_x10_non_empty_only` :161 | ×10 per non-empty slot |
| `test_all_none_scores_zero_no_exception` :173 | an all-blank row scores 0 and never raises |
| `test_unrecognized_enums_warn_not_422` :180 | a dirty enum warns instead of 422-ing |
| `test_status_whitespace_case_variants` :190 / `test_sleeping_case_variants` :194 | case/whitespace tolerance |
| `test_excel_float_hits_year_band` :197 | `2026.0` matches the `2026` band |
| `test_non_numeric_scores_zero_with_warning` :201 | unparseable numeric → 0 + warning |
| `test_absence_is_not_activity` :208 | blank ≠ zero — absence earns nothing |
| `test_company_code_split` :225 / `test_sales_org_and_sf_counts` :229 | `";"` splitting and slot counting |

**Election — `TestElection` (:246)**

| Test | Intent |
|---|---|
| `test_highest_score_wins` :247 | the basic election |
| `test_unique_row_self_references` :260 | unique → golden, self-referencing |
| `test_single_member_cluster_degrades_to_unique` :269 | a lone cluster member becomes unique |
| `test_blocked_scores_zero_but_can_win` :276 | blocked is a differentiator, not an exclusion |
| `test_all_blocked_cluster_manual_review` :287 | all-blocked → manual_review |
| `test_low_confidence_merge_demoted_to_manual_review` :297 | below-threshold merges keep membership but demote |
| `test_confident_merge_stays_proposed` :313 | at/above threshold is a normal proposal |
| `test_lowest_member_confidence_gates_the_cluster` :323 | cluster confidence = `min` member |
| `test_none_confidence_never_gates` :332 | a deterministic collapse never gates |
| `test_confidence_threshold_from_env` :341 | env override without re-running the LLM |
| `test_inherited_manual_review_survives_confident_neighbours` :350 | uncertainty never upgrades |
| `test_inherited_manual_review_demotes_whole_cluster` :367 | one uncertain member demotes all |
| `test_manual_review_singleton_not_upgraded_in_summary` :382 | a lone manual_review row is not counted unique |
| `test_approval_and_proposed_golden_fields` :396 | `proposed_golden_id` + `approval_status="proposed"` |
| `test_apply_approval_promotes_golden_and_rejects` :413 | approve promotes; reject leaves golden untouched |
| `test_duplicate_row_id_raises` :435 | duplicate `row_id` → `DuplicateRowIdError` |
| `test_empty_rows` :442 | empty request is valid |
| `test_tiebreak_recent_year_wins_on_equal_score` :449 | **tie-break step 2** |
| `test_tiebreak_equipment_on_equal_score_and_year` :458 | **tie-break step 3** |
| `test_tiebreak_company_codes_on_equal_score` :468 | **tie-break step 4** |
| `test_tiebreak_lowest_numeric_row_id_last` :480 | **tie-break step 5, numeric** |
| `test_tiebreak_lexical_when_non_numeric_ids` :490 | **tie-break step 5, lexical** |
| `test_winner_invariant_under_shuffle` :499 | order-independence |
| `test_table_invariant` :525 | the golden/duplicate table invariant holds |
| `test_score_equals_breakdown_sum` :544 | `score_final == sum(score_*)` |

**Issues, endpoints, workbook, edge cases, G1**

| Test | Intent |
|---|---|
| `test_detect_issues_covers_each_type` :573 | each issue type is reachable |
| `test_candidate_cap_exceeded_issue_from_reasoning_marker` :601 | the cap marker becomes an issue |
| `test_no_issues_on_clean_confident_cluster` :615 | no false-positive issues |
| `test_duplicate_row_id_400_lists_ids` :632 | duplicate → HTTP 400 naming the ids |
| `test_empty_rows_200_zeroed_summary` :641 | empty → 200 with a zeroed summary |
| `test_approve_endpoint_promotes_and_echoes` :650 | score → approve round trip |
| `test_score_endpoint_returns_issues` :676 | the JSON route returns `issues` |
| `test_approve_unknown_cluster_404` :690 | unknown cluster → 404 |
| `test_dirty_values_do_not_422` :700 | a dirty extract never 422s |
| `test_summary_counts` :715 | summary arithmetic |
| `test_manual_review_blanks_golden_in_file` :824 | **the workbook blanking rule** |
| `test_issues_sheet_written_preserving_weights` :852 | Issues sheet added, Weights sheet survives |
| `test_round_trip_preserves_weights_sheet_and_45_columns` :872 | in-place edit preserves every column |
| `test_corrupted_weights_sheet_falls_back_wholesale` :925 | broken override ignored wholesale |
| `test_weights_sheet_override_applies_wholesale` :938 | valid override applies wholesale |
| `test_blank_customer_skipped_and_counted` :951 | blank `Customer` skipped, counted in `errors` |
| `test_manual_review_routing_keeps_cluster_membership` :969 | manual_review keeps its cluster key |
| `test_duplicate_customer_raises` :981 | duplicate `Customer` in the file |
| `test_production_cluster_columns` :986 / `test_production_pair_beats_expected_pair` :1004 | `Routing`+`Cluster ID` preferred over the fixture pair |
| `test_file_endpoint_end_to_end` :1023 / `..._duplicate_customer_400` :1039 | the HTTP file route |
| `test_enrich_dedup_score_chain` :1062 | no pipeline stage drops a CRM field |
| `test_zero_signal_cluster_is_manual_review` :1141 | all-zero cluster must not look confident |
| `test_mixed_numeric_and_lexical_row_ids_no_raise` :1151 | mixed id types do not raise |
| `test_weights_retune_flips_winner_and_changes_version` :1164 | retune changes winner **and** fingerprint |
| `test_partial_cluster_warns_but_does_not_fail` :1189 / `test_non_hash_cluster_id_never_warns_partial` :1207 | partial-cluster detection |
| `test_rerun_is_deterministic_end_to_end` :1216 | **determinism** |
| `test_scored_with_weights_version_written_to_file` :1232 | fingerprint reaches the workbook |
| `TestG1CountRecency` :1241 — 12 tests, :1251-1417 | Bernd's year-priority rule: `bernd_example` :1251, `discovered_failure` :1263, `same_year_count_differentiates` :1273, the three partner mirrors :1286/:1298/:1309, `singleton_receives_count_points` :1322, `all_none_year_cluster_no_count_no_exception` :1330, `context_free_year_none_suppression_is_not_flagged` :1345, `max_year_record_with_none_count_still_wins_on_recency` :1359, `suppression_emits_issue` :1370, and the two invariants `sales/partner_component_never_contradicts_recency` :1382/:1402 |

**Coverage verdict: every one of the 11 criteria has a band test** (`TestBands` covers all 11),
and all five tie-break steps are pinned. **What has no test:**

| Untested behaviour | Why it matters |
|---|---|
| `account_group` arriving as an **integer** (`2` for `0002`) | 15 points lost silently; the most likely real-world Excel failure |
| `account_group="DRID"` | Bernd's own spelling scores 0 |
| **numeric or "3-4 years" `SleepingCustomer`** | up to 15 points lost |
| `Company Code` / `Sales Organization` bound instead of the consolidated columns | up to 35 points lost (§9 shows this flipping a winner) |
| A row with a **`Link ID` and no `Cluster ID`** | linked rows silently elected as separate uniques |
| Missing / corrupt `weights.json` | open item 165 |
| `score_row` with a criterion-less weights dict | open item 166 |
| `scored_with_weights_version` drift at approval | open item 167 — the check does not exist |
| Duplicated headers in the workbook | open item 168 |
| Overlapping caller-supplied bands | open item 169 |
| **Fractional weights** (`0.5` → `0`) | silent truncation |
| A **year beyond the ladder** (`2027`) | scores 0; nothing warns |

---

## 8. RECENT CHANGES

`git log --oneline -30 -- dedup/scoring.py dedup/scoring_xlsx.py dedup/weights.json api/routes.py`:

```
60e0b51 A link is not a merge, and an id conflict is not a reason to lose the pair
8868908 Enhance deduplication logic with v2 features and address handling
28eeef1 Refactor flag handling and enhance provenance logic for low-confidence records
96dd528 Update README and codebase to enhance issue detection and flag handling
600d729 Issues endpoiint added
0c057bc Suppress specific G6 and G7 issue codes from the `/issues` audit column
8d5f5f9 Update issue detection and reporting for enriched data
b8ad102 Enhance name handling by adding support for five name slots
5e423c2 Fix 8: flag model redesign
7399df8 3.3 Bind the LEI column on the dedup file upload path
8f2bb6b Align /api/dedup/score JSON with the score/file column contract
929492b Implement residue candidate nomination and adjudication process
994fb3b Enhance scoring logic to prevent false recency suppression warnings
c18921d Refactor scoring logic to align with Bernd's year-priority rule
efe1379 Enhance deduplication process with confidence-based election and manual review
611c348 Phase 2 thesis
b9f772a Add /api/dedup/file endpoint for XLSX uploads
13a1274 Deduplication endpoint
eee57b7 Enhance configuration and address processing for department-domain probing
2bb9d23 Add Remaining Issues sheet to XLSX comparison output
25f89d2 Add SAP master-data fields and issue detection enhancements
b19cd1a Add XLSX file handling and enrichment logic
9938596 Search term and azure deployment
f77080b Initial Enrichment Code
```

Restricted to the three scoring modules only
(`git log --oneline -12 --date=short -- dedup/scoring.py dedup/weights.json dedup/scoring_xlsx.py`):

```
8f2bb6b 2026-08-03 Align /api/dedup/score JSON with the score/file column contract
929492b 2026-07-23 Implement residue candidate nomination and adjudication process
994fb3b 2026-07-23 Enhance scoring logic to prevent false recency suppression warnings
c18921d 2026-07-23 Refactor scoring logic to align with Bernd's year-priority rule
efe1379 2026-07-22 Enhance deduplication process with confidence-based election and manual review
611c348 2026-07-11 Phase 2 thesis
```

**What changed since the last evaluation run.** The last evaluation artefacts under `eval/out/`
are keyed `f57782f`. Four commits have touched scoring since the model was first landed in
`611c348` (2026-07-11), and they fall into two groups.

The substantive group is 2026-07-22/23. `efe1379` introduced the confidence-based demotion path
— the `CONFIDENCE_MERGE_THRESHOLD` gate, `_cluster_merge_confidence`, and the
`manual_review` / `proposed` / `unique` split with `proposed_golden_id` — so before it, every
cluster produced a committed-looking golden record. `c18921d` then rewrote the sales-order count
criteria to implement **G1**, Bernd's year-priority rule: the two count components became
cluster-context-dependent (`_award_count`, `_cluster_year_maxima`), which is the only place in
the model where a row's points depend on its neighbours. `994fb3b`, the same day, narrowed the
G1 suppression *warning* so it fires only on genuine recency losses rather than on context-free
year-`None` rows. `929492b` added the `candidate_cap_exceeded` issue type, sourced from a marker
the adjudicator's residue pass writes into `Reasoning`.

The later group is contract-only. `8f2bb6b` (2026-08-03) aligned the JSON `/api/dedup/score`
output with the file endpoint's column names — the `@computed_field` aliases and
`SCORE_BREAKDOWN_COLUMNS` as a shared constant — without touching a single point value.
`7399df8`, `8868908` and `60e0b51` touched `api/routes.py` for the LEI binding and the v2
clustering work (including the `Link ID` column) but **changed nothing in the scoring modules**;
`60e0b51` is the commit that introduced `Link ID` without extending `dedup/scoring_xlsx.py` to
read it (§7.3 item 1).

**`dedup/weights.json` has not been modified since `611c348`** — no point value has changed
since the model was first landed, and the current fingerprint is `0a52a681bbff`.

---

## 9. WORKED EXAMPLE

### 9.1 Choosing a cluster — and a caveat about the fixture

`tests/fixtures/dedup_v2_stress_200.json` contains 200 rows and, in its recorded `v1` output,
exactly **two clusters with ≥3 members**: `c_44a6d199e643` and `c_04029e90502f`.

**⚠ The fixture carries almost none of the scoring signals.** Its 62 distinct row keys include
`Account group`, `Company Code` and `Sales Organization`, but **not** `Sales_Order_Last_Used`,
`Sales_Order_Total_Count`, `Sales_Order_Partner_Last_Used`,
`Sales_Order_Partner_Total_Count`, `Equipment_Total_Count`, `SleepingCustomer`,
`CustomerStatus`, `Company_Code_Consolidated`, `Sales_Org_Consolidated`, or any `SF_ID_*`
column. It was built for the **clustering** evaluation (`tools/build_dedup_v2_fixture.py`), not
the scoring one. Of the 11 criteria, **only `account_group` binds**; and per §1.5 the raw
`Company Code` / `Sales Organization` columns do **not** bind, because only the `_Consolidated`
headers are in `INPUT_HEADERS`.

This is itself a finding: **there is no scoring fixture over real cluster data in the
repository.** The worked example below is therefore run twice — once exactly as the code binds
the fixture today (9.2), and once with `Company Code` / `Sales Organization` manually bound to
the consolidated fields (9.3) to show what the ignored columns are worth.

Cluster **`c_04029e90502f`** is used because it exercises the most machinery: three members, a
genuine top-score tie, an inherited `manual_review`, and a below-threshold confidence.

Cluster input (from the fixture's `rows` and recorded `v1`, in `order` sequence):

| `Customer` | `Name 1` | `Account group` | `Company Code` | `Sales Organization` | `Routing` | `Confidence` |
|---|---|---|---|---|---|---|
| `13135468` | EMD Serono Research & Development | `DRIT` | `1140` | *(blank)* | `cluster` | `0.94` |
| `13353599` | EMD Serono, Inc. | `0002` | *(blank)* | *(blank)* | `cluster` | `0.94` |
| `13364185` | EMD Serono Research and Development Institute, Inc | `DRIT` | `1543` | `5431` | `manual_review` | `0.97` |

### 9.2 As the code actually scores it today

Run live at HEAD 4e33b52 through `elect_golden_records` with the checked-in
`weights.json` (`weights_version = 0a52a681bbff`).

Per-member, per-signal:

| Signal | `13135468` | `13353599` | `13364185` |
|---|---|---|---|
| `sales_order_last_used` | blank → **0** | blank → **0** | blank → **0** |
| `sales_order_count` | blank + no year (G1) → **0** | **0** | **0** |
| `sales_order_partner_last_used` | blank → **0** | **0** | **0** |
| `sales_order_partner_count` | blank + no year (G1) → **0** | **0** | **0** |
| `equipment_count` | blank → **0** | **0** | **0** |
| `sleeping_customer` | blank → **0** | **0** | **0** |
| `customer_status` | blank → **0** | **0** | **0** |
| `account_group` | `DRIT` → **20** | `0002` → **15** | `DRIT` → **20** |
| `company_code_count` | column not bound → count 0 → **0** | **0** | **0** |
| `combined_presence_bonus` | codes 0, orgs 0 → **0** | **0** | **0** |
| `salesforce_instance_count` | 0 slots × 10 → **0** | **0** | **0** |
| **`score_final`** | **20** | **15** | **20** |

Election:

| `Customer` | score | `_tiebreak_key` | `is_golden_record` | `proposed_golden_id` | `election_status` |
|---|---|---|---|---|---|
| `13135468` | 20 | `(-20, 1, 1, 0, 13135468)` | `True` *(blank in the workbook)* | `13135468` | `manual_review` |
| `13353599` | 15 | `(-15, 1, 1, 0, 13353599)` | `False` *(blank)* | `13135468` | `manual_review` |
| `13364185` | 20 | `(-20, 1, 1, 0, 13364185)` | `False` *(blank)* | `13135468` | `manual_review` |

**Elected winner: `13135468`, decided by tie-break step 5.** Walking the key
(dedup/scoring.py:949-954) for the two members tied on score:

| Step | Field | `13135468` | `13364185` | Outcome |
|---|---|---|---|---|
| 1 | `-total` | `-20` | `-20` | **tie** |
| 2 | `-(last_year or -1)` | both years blank → `1` | `1` | **tie** |
| 3 | `-(equipment or -1)` | both blank → `1` | `1` | **tie** |
| 4 | `-company_codes` | `0` | `0` | **tie** |
| 5 | `row_id` (numeric — all three parse as ints) | `13135468` | `13364185` | **`13135468` wins** (lowest) |

`13353599` never reaches the tie-break: it loses at step 1 on score (15 < 20).

**Status: `manual_review` for the whole cluster**, from **two** independent triggers
(dedup/scoring.py:1114-1124):

- `inherited_mr` — `13364185` arrived with `Routing = manual_review`;
- `low_confidence` — `_cluster_merge_confidence` takes the **lowest** member confidence,
  `min(0.94, 0.94, 0.97) = 0.94 < 0.95`.

(`all_blocked` is false — no member has a status at all; `zero_signal` is false — two members
scored 20.)

Issues emitted (`detect_issues`, run live):

| `issue_type` | `row_id` | `detail` |
|---|---|---|
| `low_confidence_merge` | `13135468` | `min merge confidence 0.94 < 0.95` |
| `tiebreak_decided` | `13135468` | `top score 20 shared by 2 members` |

Note the winner is **not** the record the clustering adjudicator treated as the anchor
(`13364185`, whose `Reasoning` reads *"adjudicated vs EMD Serono Research & Development:
merged"*), nor the one with the longest, most complete legal name. On the signals the code can
actually see, `13135468` and `13364185` are indistinguishable, and the lowest BP number decides.

### 9.3 The same cluster with `Company Code` / `Sales Organization` bound

Identical inputs, except `Company Code` → `company_code_consolidated` and
`Sales Organization` → `sales_org_consolidated`:

| Signal | `13135468` | `13353599` | `13364185` |
|---|---|---|---|
| `account_group` | `DRIT` → **20** | `0002` → **15** | `DRIT` → **20** |
| `company_code_count` | `1140` → 1 code → **5** | blank → **0** | `1543` → 1 code → **5** |
| `combined_presence_bonus` | codes 1, orgs 0 → **0** | **0** | codes 1, orgs 1 → **10** |
| **`score_final`** | **25** | **15** | **35** |

| `Customer` | score | `_tiebreak_key` | `is_golden_record` | `proposed_golden_id` |
|---|---|---|---|---|
| `13135468` | 25 | `(-25, 1, 1, -1, 13135468)` | `False` | `13364185` |
| `13353599` | 15 | `(-15, 1, 1, 0, 13353599)` | `False` | `13364185` |
| `13364185` | **35** | `(-35, 1, 1, -1, 13364185)` | `True` | `13364185` |

**The winner flips to `13364185`, decided at tie-break step 1 — no tie-break needed.** The
`tiebreak_decided` issue disappears; `low_confidence_merge` remains, and the cluster is still
`manual_review` (the inherited routing is independent of score).

**This is the practical consequence of §1.5.** Whether the extract supplies
`Company_Code_Consolidated` / `Sales_Org_Consolidated` or the raw `Company Code` /
`Sales Organization` columns changes the elected golden record on real data — and the code
gives no warning either way, because a blank consolidated column is indistinguishable from a
genuine zero (§2.2 item 3). Confirming which headers the production click report emits is, on
this evidence, as important as resolving any of the four open point values.

### 9.4 Second cluster, for contrast — `c_44a6d199e643`

Three members, all named "Marian Regional Medical Center", all `Routing = cluster`, all
`Confidence = null` (a deterministic identical-signature collapse, so nothing gates):

| `Customer` | `Account group` | `Company Code` | `Sales Organization` | `score_final` (as bound today) | Outcome |
|---|---|---|---|---|---|
| `13333500` | `0002` | *(blank)* | *(blank)* | 15 | duplicate → `13343787` |
| `13335926` | `0002` | *(blank)* | *(blank)* | 15 | duplicate → `13343787` |
| `13343787` | `DRIT` | `1245` | `2451` | **20** | **golden** |

`election_status = proposed` for all three, `approval_status = proposed`,
`scored_with_weights_version = 0a52a681bbff`. **Winner decided at tie-break step 1** (20 > 15) —
no tie-break step fired. Summary: `rows_in=3, clusters=1, rows_elected=1, rows_duplicates=2,
rows_manual_review=0`. **No issues** — the two losers tie at 15, but `tiebreak_decided` fires
only on a shared *top* score (dedup/scoring.py:523-525), and 15 is not the top.

With `Company Code` / `Sales Organization` bound, `13343787` rises to 35 (20 + 5 + 10) and the
winner is unchanged — this cluster is robust to the §1.5 binding question, unlike `c_04029e90502f`.
