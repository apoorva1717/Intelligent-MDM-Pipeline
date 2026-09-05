# Phase 2 clustering dossier — `/api/dedup/file`

Scope: the code path from an XLSX upload to the `Cluster ID` / `Routing` / `LLM Flag` /
`Confidence` / `Reasoning` columns. Read-only audit; no code was changed.

Tree state at time of writing: `HEAD = b8d62f8` (`feature/llm-fixes`), 2026-09-03.
Every line reference below is against that tree.

Pipeline: `dedup_file` (api/routes.py:1163) → `_parse_xlsx` (api/routes.py:232) →
`_rows_to_dedup_rows` (api/routes.py:1023) → `cluster_blocks` (dedup/adjudicator.py:933)
→ `group_rows_by_block` (dedup/signatures.py:127) → `_process_block`
(dedup/adjudicator.py:831) → `build_signatures` (STEP A, dedup/signatures.py:136) →
`_mode_a` / `_mode_b` (STEP B, dedup/adjudicator.py:270 / :400) → `_adjudicate_residue`
(dedup/adjudicator.py:556) → guards (dedup/adjudicator.py:866, :870) → `_emit_rows`
(STEP C, dedup/adjudicator.py:721) → `_build_dedup_xlsx` (api/routes.py:1080).

---

## 1. ENTRY POINTS

### 1.1 `dedup_file` — api/routes.py:1163-1219

```python
@router.post("/api/dedup/file")
async def dedup_file(
    file: UploadFile = File(..., description="XLSX file of address-gated candidate rows"),
) -> StreamingResponse:
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Uploaded file must be an .xlsx (or .xlsm) workbook.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    headers, row_dicts = _parse_xlsx(contents)
    rows = _rows_to_dedup_rows(row_dicts)
    settings = get_settings()
    llm = _get_dedup_llm(settings)
    ...
    response = await cluster_blocks(rows, llm, settings=settings)
    ...
    output_bytes = _build_dedup_xlsx(headers, row_dicts, rows, response, source_contents=contents)
```

`dedup_cluster_block` (api/routes.py:1133-1160) is the JSON twin: same
`cluster_blocks(request.rows, llm, settings=settings)` call (api/routes.py:1150), no file
parsing, and it returns the full `DedupResponse` — including `model`, `model_version`,
`prompt_version`, `block_id`, `signature_id`, and `summary`, **none of which the file
route writes to the main sheet** (§5).

### 1.2 `_norm_header` — api/routes.py:128-136 (verbatim)

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

Everything non-alphanumeric is deleted: `"Country/Region Key"` → `countryregionkey`,
`"House Number"` → `housenumber`, `"LEI ID"` → `leiid`.

### 1.3 `_DEDUP_HEADER_ALIASES` — api/routes.py:992-1020 (verbatim)

```python
_DEDUP_HEADER_ALIASES: dict[str, str] = {
    "rowid": "row_id",
    "recordid": "row_id",
    "customer": "row_id",
    "blockid": "block_id",
    "name1": "name1",
    "name2": "name2",
    "name3": "name3",
    "name4": "name4",
    "name5": "name5",
    "street": "street",
    "street1": "street",
    "streetcleaned": "street",
    "houseno": "house_no",
    "housenumber": "house_no",
    "postalcode": "postal_code",
    "zip": "postal_code",
    "city": "city",
    "country": "country",
    "countryregionkey": "country",
    "rorid": "ror_id",
    # "LEI ID" is what /enrich/file writes (api.output_columns.RESPONSE_COLUMNS),
    # and it normalises to the same key as "lei id" / "lei_id" / "LEI_ID".
    # Without this the column bound to nothing and the adjudicator ran without
    # the company legal-entity signal the JSON route carries.
    "leiid": "lei_id",
    "lei": "lei_id",
    "enrichedname": "enriched_name",
}
```

### 1.4 Which `/enrich/file` columns bind, and which are silently dropped

`/enrich/file` emits exactly `RESPONSE_COLUMNS` (api/output_columns.py:22-121). Joining
that list against the alias table:

| Output column (api/output_columns.py) | `_norm_header` | DedupRow field |
|---|---|---|
| `Customer` (:24) | `customer` | `row_id` |
| `Name 1` (:34) | `name1` | `name1` |
| `Name 2` (:35) | `name2` | `name2` |
| `Name 3` (:36) | `name3` | `name3` |
| `Name 4` (:37) | `name4` | `name4` |
| `Name 5` (:38) | `name5` | `name5` |
| `Street 1` (:62) | `street1` | `street` |
| `House Number` (:63) | `housenumber` | `house_no` |
| `Country/Region Key` (:78) | `countryregionkey` | `country` |
| `Postal Code` (:79) | `postalcode` | `postal_code` |
| `City` (:80) | `city` | `city` |
| `ROR ID` (:103) | `rorid` | `ror_id` |
| `LEI ID` (:104) | `leiid` | `lei_id` |

**Silently dropped** — every other `RESPONSE_COLUMNS` entry binds to no field and is
discarded by api/routes.py:1043-1046. The ones that matter for clustering:

| Dropped column | Why it matters |
|---|---|
| `Street 2` … `Street 5` (:64-67) | not in the block key; a second address line cannot separate blocks |
| `PO Box`, `Suite`, `Building`, `Floor`, `Room`, `Unit`, `Mail Stop`, `Unloading Point`, `Mail Code` (:68-76) | **two records in different buildings/suites at one street address land in the same block** |
| `Domain`, `Department Domain` (:55-56) | no domain signal reaches adjudication |
| `Operating Name`, `Suggested Name` (:45,:47) | alternate names never reach the LLM |
| `Record Type` (:101) | institution-vs-company is never told to the model as a field |
| `Region` (:81) | not in the block key |
| all `* Provenance` (:116-121), `Flag *` (:96-99) | not read by dedup |

There is **no `Block ID` column** in `RESPONSE_COLUMNS`, so on the `/enrich/file →
/api/dedup/file` path every `block_id` is *derived* (§2.4).

There is **no `Enriched Name` column** either, so `enriched_name` is always `None` on the
file path — and it is dead weight regardless: `enriched_name` is declared at
dedup/models.py:52 and aliased at api/routes.py:1019, but **no code in `dedup/` ever
reads it** (verified: the only occurrences are the declaration and the alias).

Unrecognised headers are logged once, at WARNING, and carried through to the output
sheet unchanged (api/routes.py:1031-1035, :1055-1061):

```python
    if unrecognised:
        logger.warning(
            "dedup file: %d column header(s) matched no DedupRow field and "
            "were not used for adjudication (passed through to the output "
            "unchanged): %s",
            len(unrecognised), ", ".join(repr(h) for h in unrecognised),
        )
```

### 1.5 Blank / None cells

`_parse_xlsx` (api/routes.py:274-293):

```python
    row_dicts: list[dict[str, str]] = []
    for raw_row in rows:
        if raw_row is None:
            continue
        row_dict: dict[str, str] = {}
        for header, cell in zip(headers, raw_row):
            if not header or cell is None:
                continue
            value = str(cell).strip()
            if value:
                row_dict[header] = value
        if row_dict:  # skip entirely blank rows
            row_dicts.append(row_dict)
```

Consequences, in order:

1. A `None` cell or a whitespace-only cell produces **no key** in `row_dict`
   (api/routes.py:280-284) → no key in `normalised` (api/routes.py:1041-1049) → the
   Pydantic default `None` applies (dedup/models.py:37-52).
2. An entirely blank spreadsheet row is **dropped** (api/routes.py:285-286). It is
   therefore absent from `row_dicts`, absent from `rows`, and absent from the output
   sheet — the output can be shorter than the input.
3. **Every cell is stringified** (`str(cell).strip()`, api/routes.py:282). A customer id
   held as a spreadsheet number is `"12345"`; one held as a float is `"12345.0"`. `row_id`
   is the join key for the whole output (`result_by_id`, api/routes.py:1100), so a numeric
   type change between stages changes the key.
4. The header row is the **first non-empty row** (api/routes.py:258-268); leading blank
   rows are skipped, and only `workbook.active` is read (api/routes.py:255) — a second
   sheet is never parsed as data.
5. Blank `Name 2`…`Name 5` → `department_text` returns `""` → `has_name2` is False →
   the deterministic asymmetry rule applies (§4.3).

Validation failure raises 422 for the whole file (api/routes.py:1063-1067); rows are
never partially dropped. `row_id` is the only required field.

### 1.6 `DedupRow` — dedup/models.py:18-52 (verbatim)

```python
class DedupRow(BaseModel):
    """A single address-gated candidate row.

    Every row in a request already shares the same physical address as the
    other rows in its block (the address gates ran upstream). The adjudicator
    only decides, from the names, which rows refer to the same entity.
    """

    # Accept snake_case aliases too, so the caller can use either casing.
    model_config = ConfigDict(populate_by_name=True)

    row_id: str = Field(..., description="Caller's stable key, echoed back verbatim.")
    block_id: Optional[str] = Field(
        default=None,
        description=(
            "Address block. When null, derived from the normalized "
            "(country, postal_code, street, house_no)."
        ),
    )
    name1: Optional[str] = Field(default=None, description="Institution / company.")
    name2: Optional[str] = Field(default=None, description="Department / sub-unit (may be empty).")
    # The rest of the SAP name block. A record's unit can sit in any of these
    # — Name 3 as readily as Name 2 — so the signature key reads all of them.
    # Defaulted last so existing positional/keyword construction is unchanged.
    name3: Optional[str] = Field(default=None, description="Further sub-unit (may be empty).")
    name4: Optional[str] = Field(default=None, description="Further sub-unit (may be empty).")
    name5: Optional[str] = Field(default=None, description="Further sub-unit (may be empty).")
    street: Optional[str] = None
    house_no: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    ror_id: Optional[str] = Field(default=None, description="ROR id from Phase 1, if resolved (institution hint).")
    lei_id: Optional[str] = Field(default=None, description="GLEIF LEI from Phase 1, if resolved (company legal-entity hint).")
    enriched_name: Optional[str] = Field(default=None, description="Phase 1 official name, if resolved.")
```

**There are no validators** — no `@field_validator`, no `@model_validator`, no
constraints beyond `row_id: str` being required. No trimming, no case folding, no
emptiness check at the model layer. `row_id=""` is accepted. `city` is accepted and
**never read by any dedup code** (the only address fields consumed are `country`,
`postal_code`, `street`, `house_no`, at dedup/signatures.py:59).

---

## 2. BLOCKING

### 2.1 `derive_block_id` — dedup/signatures.py:51-62 (verbatim)

```python
def derive_block_id(row: DedupRow) -> str:
    """Derive a stable block id from the normalized address tuple.

    Used when a row arrives without a ``block_id``. The hash keeps the
    derived id compact and safe to embed in a ``cluster_id``.
    """
    joined = "|".join(
        normalize_key(part)
        for part in (row.country, row.postal_code, row.street, row.house_no)
    )
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]
    return f"blk-{digest}"
```

### 2.2 `resolve_block_id` — dedup/signatures.py:120-124 (verbatim)

```python
def resolve_block_id(row: DedupRow) -> str:
    """The row's block id, or a derived one when absent/blank."""
    if row.block_id and row.block_id.strip():
        return row.block_id.strip()
    return derive_block_id(row)
```

### 2.3 `group_rows_by_block` — dedup/signatures.py:127-133 (verbatim)

```python
def group_rows_by_block(rows: List[DedupRow]) -> "OrderedDict[str, List[DedupRow]]":
    """Group rows by (resolved) block id, preserving first-seen order."""
    blocks: "OrderedDict[str, List[DedupRow]]" = OrderedDict()
    for row in rows:
        block_id = resolve_block_id(row)
        blocks.setdefault(block_id, []).append(row)
    return blocks
```

### 2.4 The block key, exactly

| Property | Value | Cite |
|---|---|---|
| Key fields | `country`, `postal_code`, `street`, `house_no` — **in that order** | dedup/signatures.py:59 |
| Normalisation per field | `normalize_key` (§3.1): NFKD accent fold, lowercase, strip, punctuation→space, whitespace collapse | dedup/signatures.py:58 |
| Join | a literal pipe character (`\|`) between the four normalised parts | dedup/signatures.py:57 |
| Digest | `sha1(...)[:12]`, prefixed `blk-` | dedup/signatures.py:61-62 |
| **Not** in the key | `city`, `region`, `name*`, `ror_id`, `lei_id`, building/suite/floor | dedup/signatures.py:59 |

**All-empty-address behaviour.** A missing part normalises to `""` (dedup/signatures.py:39-40)
and is still joined, so a row with no country, postal code, street or house number
produces a `joined` string of three bare separators and always the same digest. **Every
address-less row without a `block_id` therefore lands in one shared block and is
adjudicated together.** This is the highest-leverage misclustering mechanism on the file
path, because the block is the only thing bounding what the LLM may merge. Documented as
untested at docs/thesis/03_ALGORITHMS.md:5536 (`⚠ NO FIXTURE COVERAGE for the
all-empty-address case`); still no test exercises it.

Note also that all four parts are hashed *jointly*: any two rows agreeing on all four
normalised parts block together regardless of how those parts were spelt.

### 2.5 Is `block_id` ever supplied by the caller on the file path?

Yes — `"blockid"` is an alias (api/routes.py:996), so a column headed `Block ID`,
`block_id`, `BlockID` or `BLOCK ID` binds to `DedupRow.block_id` and **wins over the
derived id** (dedup/signatures.py:122-123). But:

- `/enrich/file` does **not** emit such a column (api/output_columns.py:22-121), so on the
  standard pipeline the id is always derived.
- `/api/dedup/file` writes `Block ID` only to the **`Dedup Debug` sheet**
  (api/routes.py:1077, :1123), never to the active sheet, and `_parse_xlsx` reads only
  `workbook.active` (api/routes.py:255). Re-feeding a dedup output therefore **does not**
  recover the previous block ids.
- The address gate that produces `Block ID` in the DATAshaper/ADF deployment is not in this
  repository (docs/thesis/03_ALGORITHMS.md:5442).

---

## 3. STEP A — SIGNATURES

### 3.1 `normalize_key` — dedup/signatures.py:28-48 (verbatim, with the module's own comment)

```python
# Strip anything that is not a letter, digit, or whitespace. We deliberately
# do NOT strip legal forms (GmbH, AG, Inc.) or expand abbreviations here —
# that is the LLM's job. The key is a conservative collapse only.
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_key(value: Optional[str]) -> str:
    """Conservative normalized key: lowercase, trim, collapse internal
    whitespace, strip punctuation. Unicode-aware (accents folded to base
    letters so ``Universität`` and ``Universitat`` collapse together)."""
    if not value:
        return ""
    # Fold accents (NFKD) so visually-equivalent spellings collapse.
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    # Replace punctuation with a space so "u.s.a" -> "u s a", not "usa".
    text = _NON_WORD.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text
```

### 3.2 `department_text` — dedup/signatures.py:65-76 (verbatim)

```python
def department_text(row: DedupRow) -> str:
    """The row's whole department block joined into one string.

    Every populated slot below Name 1, in block order, separated by " / ".
    This is what both the signature key and the LLM see as "Name 2" — the
    unit the row names, wherever the SAP entry happened to put it.
    """
    parts = [
        str(getattr(row, slot, None) or "").strip()
        for slot in DEPT_SLOTS
    ]
    return " / ".join(p for p in parts if p)
```

`DEPT_SLOTS == ("name2", "name3", "name4", "name5")` — derived as `NAME_SLOTS[1:]` from
`NAME_SLOT_COUNT = 5` (utils/name_slots.py:35-40, :53).

### 3.3 `build_signatures` — dedup/signatures.py:136-173 (verbatim)

```python
def build_signatures(rows: List[DedupRow]) -> List[Signature]:
    """Collapse a block's rows into distinct signatures (STEP A).

    Signatures are returned in first-appearance order; their ids are
    ``s1``, ``s2`` … local to the block. Each signature accumulates the
    row_ids that share its key and adopts the first non-empty ror_id / lei_id
    seen.
    """
    by_key: "OrderedDict[tuple[str, str], Signature]" = OrderedDict()
    for row in rows:
        n1 = normalize_key(row.name1)
        departments = department_text(row)
        n2 = normalize_key(departments)
        key = (n1, n2)
        sig = by_key.get(key)
        if sig is None:
            sig = Signature(
                signature_id="",  # assigned below, once order is known
                norm_name1=n1,
                norm_name2=n2,
                name1=(row.name1 or "").strip(),
                name2=departments,
                ror_id=(row.ror_id or None),
                row_ids=[],
                lei_id=(row.lei_id or None),
            )
            by_key[key] = sig
        sig.row_ids.append(row.row_id)
        # Adopt the first non-empty ror_id / lei_id any row in the signature carries.
        if not sig.ror_id and row.ror_id:
            sig.ror_id = row.ror_id
        if not sig.lei_id and row.lei_id:
            sig.lei_id = row.lei_id

    signatures = list(by_key.values())
    for index, sig in enumerate(signatures, start=1):
        sig.signature_id = f"s{index}"
    return signatures
```

### 3.4 Confirmations requested

| Question | Answer | Cite |
|---|---|---|
| Which name fields are in the key? | `name1` alone forms the first half; `name2`+`name3`+`name4`+`name5` joined by `" / "` form the second. **All five slots participate.** | dedup/signatures.py:145-149, :72-76, utils/name_slots.py:53 |
| Key shape | `(normalize_key(name1), normalize_key(department_text(row)))` — a 2-tuple, block-local | dedup/signatures.py:145-149 |
| What is NOT normalised | Legal forms (`GmbH`, `AG`, `Inc.`) are **not** stripped; abbreviations are **not** expanded. Only accents, case, punctuation and whitespace are touched. | dedup/signatures.py:28-31 (comment), :35-48 (code) |
| Effect | `Pfizer AG` and `Pfizer Inc.` are two distinct signatures at Step A — pinned by `test_step_a_does_not_collapse_suffix_variants` (tests/test_dedup.py:579) | dedup/signatures.py:29-30 |
| Slot-position sensitivity | `Uni X` + `Chemistry` in Name 2 and `Uni X` + `Chemistry` in Name 3 both yield department text `"Chemistry"` → **same** signature. But `Name2="Dept"`+`Name3="Chem"` yields `"Dept / Chem"`, which does **not** equal `Name2="Dept Chem"` → **different** signatures. | dedup/signatures.py:72-76 |
| `ror_id` / `lei_id` adoption rule | The signature takes the value from the **first row that created it**, then the **first non-empty value any later member carries**, never overwriting a value already set. Conflicting ids among rows of one signature are silently discarded. | dedup/signatures.py:152-168 |
| Does the LLM see the normalised key? | No. `Signature.name1` / `.name2` hold the original strings, and only those are put in the prompt payload. | dedup/signatures.py:82-84, dedup/adjudicator.py:301-310 |

`Signature.has_name2` — dedup/signatures.py:109-117:

```python
    @property
    def has_name2(self) -> bool:
        """Whether the row names any department at all (after conservative
        normalization of the whole block below Name 1).

        Drives the deterministic asymmetry rule: a signature with no
        department can never share an entity with one that has any.
        """
        return bool(self.norm_name2)
```

---

## 4. STEP B — ADJUDICATION

### 4.1 Mode selection — dedup/adjudicator.py:843-854 (verbatim)

```python
    n = len(signatures)

    if n <= 1:
        # Single signature (or empty) — no LLM. Identical rows still cluster.
        stats.mode = "A"
        entities = [Entity(entity_id="e1", signatures=signatures)] if signatures else []
    elif n <= threshold:
        stats.mode = "A"
        entities = await _mode_a(signatures, llm, semaphore, stats)
    else:
        stats.mode = "B"
        entities = await _mode_b(signatures, llm, semaphore, stats)
```

`threshold` resolution — dedup/adjudicator.py:948-951:

```python
    if threshold is None:
        threshold = int(os.getenv("SIG_PARTITION_THRESHOLD", str(DEFAULT_SIG_PARTITION_THRESHOLD)))
    if concurrency is None:
        concurrency = int(os.getenv("DEDUP_MAX_CONCURRENCY", str(DEFAULT_DEDUP_MAX_CONCURRENCY)))
    semaphore = asyncio.Semaphore(max(1, concurrency))
```

### 4.2 All thresholds and knobs, with effective values

`.env` in this tree sets **none** of the dedup variables (only
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT=MDM-Apoorva-gpt-5.4`, TLS/proxy paths,
`ROR_*`, `FUZZY_MATCH_THRESHOLD`, `EVIDENCE_CACHE_DIR`). So every value below is the
**default in effect right now**.

| Knob | Default | Env var | `Settings` field | Precedence | Cite |
|---|---|---|---|---|---|
| `SIG_PARTITION_THRESHOLD` | **12** | `SIG_PARTITION_THRESHOLD` | — (env only) | arg > env > default | dedup/adjudicator.py:36, :949 |
| `DEDUP_MAX_CONCURRENCY` | **5** | `DEDUP_MAX_CONCURRENCY` | — (env only) | arg > env > default | dedup/adjudicator.py:37, :951 |
| name candidate threshold (Jaro-Winkler) | **0.85** | `NAME_CANDIDATE_THRESHOLD` | `name_candidate_threshold` | **settings > env > default** | dedup/adjudicator.py:38, :905-919; config.py:605-607 |
| token candidate threshold (Jaccard) | **0.6** | `TOKEN_CANDIDATE_THRESHOLD` | `token_candidate_threshold` | settings > env > default | dedup/adjudicator.py:39, :920-922; config.py:608-610 |
| max candidates per block | **50** | `MAX_CANDIDATES_PER_BLOCK` | `max_candidates_per_block` | settings > env > default | dedup/adjudicator.py:40, :923-925; config.py:611-613 |
| `DEDUP_REASONING_EFFORT` | **`"low"`** | `DEDUP_REASONING_EFFORT` | — | env > default | dedup/llm.py:148 |
| `DEDUP_MAX_RETRIES` | **3** | `DEDUP_MAX_RETRIES` | — | env > default | dedup/llm.py:149 |
| `MOCK_EXTERNAL_CALLS` | **False** | `MOCK_EXTERNAL_CALLS` | `mock_external_calls` | env > default | config.py:616-618 |
| `CONFIDENCE_MERGE_THRESHOLD` | 0.95 | `CONFIDENCE_MERGE_THRESHOLD` | `confidence_merge_threshold` | env > default | config.py:599-601 |

**Caution on the `settings`-first precedence** (dedup/adjudicator.py:903-914): the three
candidate knobs are `float`/`int` fields with non-`None` defaults on `Settings`
(config.py:605-613), so `getattr(settings, attr, None) is not None` is **always true**
when a `Settings` is passed — which both routes do (api/routes.py:1150, :1198). The env
branch inside `pick` is therefore **unreachable on the HTTP paths**; the env var is read
by `Settings` itself instead (config.py:606), so the value is the same, but the
`_resolve_candidate_config` env fallback only ever fires for `settings=None` callers.
`CONFIDENCE_MERGE_THRESHOLD` is an **election-time** knob (Pass 3), not a clustering knob.

```python
def _resolve_candidate_config(settings: Any) -> _CandidateConfig:
    """Residue knobs: settings attrs > env vars > module defaults."""
    def pick(attr: str, env: str, cast, default):
        if settings is not None and getattr(settings, attr, None) is not None:
            return getattr(settings, attr)
        raw = os.getenv(env)
        ...
```
(dedup/adjudicator.py:903-914)

### 4.3 The deterministic Name 2 asymmetry rule (verbatim)

There are **three** enforcement points, and they are not equivalent.

**(a) Mode A pre-bucketing — the decision never reaches the LLM** (dedup/adjudicator.py:283-298):

```python
    entities: List[Entity] = []
    next_index = 1

    buckets = {
        True: [s for s in signatures if s.has_name2],
        False: [s for s in signatures if not s.has_name2],
    }

    for has_name2, bucket in buckets.items():
        if not bucket:
            continue
        if len(bucket) == 1:
            sig = bucket[0]
            entities.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            continue
```

The populated-Name2 bucket is always processed first (dict literal insertion order), so
`e1` belongs to it whenever it is non-empty.

**(b) Mode B candidate filtering — no LLM call across the boundary**
(dedup/adjudicator.py:422-428):

```python
        compatible = [e for e in canonicals if e.has_name2 == sig.has_name2]
        if not compatible:
            # Deterministically a new entity — never compared across the
            # Name 2 boundary.
            canonicals.append(Entity(entity_id=f"e{next_index}", signatures=[sig]))
            next_index += 1
            continue
```

**(c) Post-LLM safety net `_enforce_name2_split`** — dedup/adjudicator.py:136-168 (verbatim):

```python
def _enforce_name2_split(entities: List[Entity], next_index: int) -> tuple[List[Entity], int]:
    """Deterministic safety net for the Name 2 asymmetry rule.

    A signature with an empty Name 2 can NEVER share an entity with a
    populated-Name 2 signature. If an LLM-returned entity mixes the two, split
    it so the empty-Name 2 signatures form their own (institution-level)
    entity. Returns the (possibly expanded) entity list and the next free id
    index.
    """
    result: List[Entity] = []
    for ent in entities:
        populated = [s for s in ent.signatures if s.has_name2]
        empty = [s for s in ent.signatures if not s.has_name2]
        if populated and empty:
            logger.warning(
                "Dedup: LLM merged empty- and populated-Name2 signatures in "
                "entity %s; splitting deterministically", ent.entity_id,
            )
            ent.signatures = populated
            result.append(ent)
            split = Entity(
                entity_id=f"e{next_index}",
                signatures=empty,
                institution=ent.institution,
                department="",
                confidence=ent.confidence,
                reasoning="Split from a mixed-Name2 group (deterministic rule).",
            )
            next_index += 1
            result.append(split)
        else:
            result.append(ent)
    return result, next_index
```

> **The rule is NOT absolute.** `_enforce_name2_split` is called **only from inside
> `_mode_a`** (dedup/adjudicator.py:392) — it is *not* called from `_mode_b`, and it is
> *not* re-applied after the residue pass in `_process_block` (dedup/adjudicator.py:856-877).
> `candidates._eligible` deliberately makes every cross-boundary pair eligible
> (dedup/candidates.py:159-168), and on an LLM `"match"` the residue pass unions them
> (dedup/adjudicator.py:662-668). **So an empty-Name2 signature CAN end up clustered with a
> populated-Name2 one — via the residue pass, on the LLM's word.** This is the intended
> "Option A" design, pinned by `test_name2_asymmetry_pair_is_nominated_and_adjudicated`
> (tests/test_dedup.py:353-359). If a real output shows a bare institution row clustered
> with a departmental row, **this is the path that did it**, and the `Reasoning` cell will
> read `adjudicated vs <name>: merged (...)` (dedup/adjudicator.py:663).

### 4.4 Other hard pre-/post-LLM constraints

**ROR/LEI identity guard — split only, never a merge shortcut.**
`_enforce_identity_split`, dedup/adjudicator.py:185-234 (verbatim head):

```python
def _enforce_identity_split(
    entities: List[Entity], next_index: int
) -> tuple[List[Entity], int, bool]:
    """Deterministic guard: an entity may never hold two DIFFERENT non-empty
    ROR ids, nor two different non-empty LEI ids.

    A different non-empty hard identifier means a different institution / legal
    entity — a strong split signal (ROR/LEI is only ever a split signal here,
    never a merge trigger). When an LLM merge violates this, split the entity
    into singletons and flag each for human review; we never guess a safe
    regrouping (the safe outcome is manual_review). Returns the (expanded)
    entity list, the next free id index, and whether any split fired.
    """
    result: List[Entity] = []
    fired = False
    for ent in entities:
        rors = _distinct_nonempty(s.ror_id for s in ent.signatures)
        leis = _distinct_nonempty(s.lei_id for s in ent.signatures)
        if len(ent.signatures) < 2 or (len(rors) < 2 and len(leis) < 2):
            result.append(ent)
            continue
        fired = True
        kind = "ROR" if len(rors) >= 2 else "LEI"
```

Every split signature gets `uncertain = True` and the reason string
`"Split: different non-empty {kind} ids (...) indicate different entities; routed to
manual review."` (dedup/adjudicator.py:213-233).

**There is no ROR/LEI identity *shortcut*.** A shared ROR or LEI never merges anything
deterministically — it only (a) *nominates* a residue pair (`_ids_converge`,
dedup/candidates.py:123-127, :144-145) and (b) appears in the prompt as a hint
(dedup/prompts.py:33-34). The system prompt says so explicitly (§4.6).

**Contradiction guard — whole-block demotion.** dedup/adjudicator.py:241-263 (verbatim):

```python
# Explicit non-merge assertions. Read ONLY to demote toward manual_review —
# never to merge — so a coarse phrase match is the safe direction (spec: if a
# verdict is ambiguous, route the whole block to manual_review, never guess
# toward merging).
_NONMERGE_MARKERS = (
    "should not be merged",
    "should not merge",
    "must not be merged",
    "not be merged",
    "do not merge",
    "should be split",
    "must be split",
)


def _reasoning_disowns_membership(entities: List[Entity]) -> bool:
    """True when a MERGED entity (>=2 signatures) carries reasoning that
    explicitly asserts a non-merge — a self-contradicting verdict. The INVARIANT
    (asserted at the block seam): a record's stored reasoning may never assert
    non-merge of a signature it belongs to."""
    for ent in entities:
        if len(ent.signatures) < 2 or not ent.reasoning:
            continue
        text = ent.reasoning.casefold()
        if any(marker in text for marker in _NONMERGE_MARKERS):
            return True
    return False
```

When it fires, **every signature in the block** is marked uncertain
(dedup/adjudicator.py:870-877) — so one badly-worded rationale routes an entire block to
`manual_review`.

**Suffix rules.** `strip_legal_suffix` (dedup/candidates.py:60-79) exists **only** for
candidate similarity, never for the canonical signature (dedup/candidates.py:26-28). The
suffix list is data at dedup/candidates.py:30-37, greedily stripped longest-first and
repeatedly from the tail only (`"GmbH & Co. KG"` → all three go), with a guard that a
name consisting solely of a legal form is kept whole (dedup/candidates.py:79).

**Guard ordering** — dedup/adjudicator.py:856-877:

```python
    # Residue widening: nominate + adjudicate the pairs the bucketed pass never
    # compared (cross-Name2-boundary, lone-bucket). Runs BEFORE the identity
    # guard so a bad name/token merge across conflicting ROR/LEI is still split.
    entities = await _adjudicate_residue(
        block_id, entities, llm, semaphore, stats, cfg
    )

    # Deterministic verdict guards, applied uniformly to both modes' output.
    # 1) A merge across different non-empty ROR/LEI ids is split to
    #    manual_review (a hard identifier conflict is a strong split signal).
    entities, _, _ = _enforce_identity_split(entities, _next_entity_index(entities))
    # 2) INVARIANT: a merged entity's reasoning may never assert non-merge of a
    #    member. If it does, the verdict is self-contradictory — route the whole
    #    block to manual_review rather than guess toward merging.
    if _reasoning_disowns_membership(entities):
```

### 4.5 Mode B partitioning logic — dedup/adjudicator.py:400-527

Mode B is **incremental greedy assignment**, not a partition:

1. Signatures are walked in `build_signatures` order (`s1`, `s2`, … = block first-appearance
   order) — dedup/adjudicator.py:416.
2. The first signature seeds `e1` with **no LLM call** and `adjudicated=False`
   (dedup/adjudicator.py:417-420).
3. For each later signature, canonicals are filtered to matching `has_name2`
   (dedup/adjudicator.py:422); if none, a new entity, no call (:423-428).
4. Otherwise **one** LLM call presents the candidate against **all** compatible canonicals
   (dedup/adjudicator.py:430-452), `max_tokens=1000`.
5. `"match"` appends the signature to the named canonical (dedup/adjudicator.py:473-488);
   `"new"` starts a new entity (:501-510); anything else — including an unparseable
   response (:456-466) and an unrecognised decision string (:511-525) — starts its own
   entity and sets `uncertain`.

**Can entities span partitions?** There are no partitions in Mode B — there is one growing
canonical list per block, so every signature in a block can in principle reach every
compatible canonical. In **Mode A** there are exactly two partitions (the `has_name2`
buckets), each adjudicated by one independent call, and **entities cannot span them at
that stage**.

**Merge-across-partition step:** yes — `_adjudicate_residue` (dedup/adjudicator.py:556-714).
It runs for **both** modes (dedup/adjudicator.py:859), nominates cross-bucket and
un-adjudicated-singleton pairs, adjudicates each pairwise, and unions the accepted ones:

```python
    # Union-find over entity indices; lowest index stays root for a stable id.
    parent = list(range(len(entities)))
    ...
    for c in candidates:
        nominated.add(c.a)
        nominated.add(c.b)
        if find(c.a) == find(c.b):
            continue  # already merged transitively — don't re-ask
```
(dedup/adjudicator.py:603-625)

Transitivity is implicit: `A~B` and `B~C` produce one three-way entity **without ever
asking about `A~C`** (dedup/adjudicator.py:624-625). Candidate ordering is deterministic —
`sort_key = (rule_rank, -score, a, b)` with `id < name < token` (dedup/candidates.py:115-120,
:195).

**Cap behaviour** (dedup/adjudicator.py:585-601): more than `max_candidates` nominations →
the **whole block** goes to `manual_review`, every signature marked uncertain with the
marker `candidate_cap_exceeded: N candidate pairs exceed the per-block cap of M; block
routed to manual review`. No merges are attempted at all in that case.

Eligibility (dedup/candidates.py:159-168, verbatim):

```python
def _eligible(x: CandidateUnit, y: CandidateUnit) -> bool:
    """A residue pair worth considering: one the bucketed adjudication skipped.

    Same-``has_name2`` pairs where BOTH units already went through the LLM were
    compared in Mode A/B — no need to re-adjudicate. Everything else (across the
    Name-2 boundary, or involving an un-adjudicated singleton) is residue.
    """
    if x.has_name2 == y.has_name2 and x.adjudicated and y.adjudicated:
        return False
    return True
```

Nomination (dedup/candidates.py:130-156, verbatim):

```python
def nominate(
    x: CandidateUnit,
    y: CandidateUnit,
    *,
    name_threshold: float,
    token_threshold: float,
) -> Optional[Candidate]:
    """Nominate the pair (x, y) if any rule fires, else None.

    Priority when several fire: id-convergence > name similarity > token
    overlap (a merge is never implied — this only picks the LLM candidate).
    """
    a, b = (x, y) if x.index < y.index else (y, x)

    if _ids_converge(x, y):
        return Candidate(a.index, b.index, "id", 1.0)

    sa, sb = strip_legal_suffix(x.name), strip_legal_suffix(y.name)
    jw = JaroWinkler.similarity(sa, sb) if sa and sb else 0.0
    if jw >= name_threshold:
        return Candidate(a.index, b.index, "name", jw)

    jac = _jaccard(sa.split(), sb.split())
    if jac >= token_threshold:
        return Candidate(a.index, b.index, "token", jac)

    return None
```

Nomination compares **`name1` only** — `CandidateUnit.name` is
`ent.signatures[0].name1` (dedup/adjudicator.py:534-542). Department text plays no part in
nomination.

### 4.6 The prompts — `prompt_version = "p2-dedup-v3"` (dedup/prompts.py:14)

**System prompt, shared by both modes — dedup/prompts.py:19-39, verbatim:**

```
You are an entity-resolution adjudicator for SAP customer master data at Bruker, a scientific-instruments company. Customers are research institutions, universities, hospitals, companies, and their internal departments.

Every record you receive already shares the same physical address (country, postal code, street). Address matching is done. Your only job is to decide, from the names, which records refer to the SAME real-world customer entity.

Identity has TWO levels:
- Name 1 = the institution or company (e.g. "University of Stuttgart", "Siemens AG").
- Name 2 = a department, faculty, institute, or sub-unit within it (may be empty).
An entity is a specific (institution, department) pair.

Rules:
- Same institution AND same department, or both Name 2 empty → SAME entity.
- Same institution but DIFFERENT departments → DIFFERENT entities. Never merge them. Example: "Uni Stuttgart, Dept of Chemistry" and "Uni Stuttgart, Dept of Mechanical Engineering" are two distinct entities.
- Different institutions that happen to share one address (shared campus or building) → DIFFERENT entities.
- A shared ROR ID means same INSTITUTION only. It does not mean same department and never by itself makes two records the same entity — you must still compare Name 2.
- A shared LEI (Legal Entity Identifier) means the records are the same legal entity (typically a company). Treat it like ROR: a strong same-INSTITUTION signal, but it still does not by itself merge records with DIFFERENT Name 2 departments, and you must still compare Name 2. Conversely, DIFFERENT non-empty LEIs are a strong signal of different entities.

Judge names accounting for: cross-language translations (German↔English etc.), abbreviations and acronyms ("Dept" = "Department", "Mech Eng" = "Mechanical Engineering"), word reordering, legal-form suffixes (GmbH, AG, Inc., Ltd, e.V.), historical renames or restructures, and spelling variants/typos.

If you cannot decide with reasonable confidence, return uncertain. Do not guess — uncertain routes to a human reviewer, which is the safe outcome.
```

**Mode A user prompt builder — dedup/prompts.py:42-58, verbatim:**

```python
def build_mode_a_user_prompt(signatures: List[dict]) -> str:
    """Mode A (partition) user message.

    ``signatures`` is a list of dicts with keys: signature_id, name1, name2,
    ror_id, lei_id. The LLM always sees the original (un-normalized) names.
    """
    listing = json.dumps({"signatures": signatures}, ensure_ascii=False, indent=2)
    return (
        "Group the following signatures into entities. "
        "Return STRICT JSON only, no other text:\n"
        '{"entities":[{"signature_ids":["s1","s3"],"institution":"<short label>",'
        '"department":"<short label or empty>","confidence":<0-1>,'
        '"reasoning":"<1-2 sentences>"}],"uncertain_signature_ids":["s7"]}\n'
        "Every input signature_id must appear exactly once, across either "
        "entities[].signature_ids or uncertain_signature_ids.\n\n"
        f"Signatures:\n{listing}"
    )
```

**Mode B / residue user prompt builder — dedup/prompts.py:61-79, verbatim:**

```python
def build_mode_b_user_prompt(candidate: dict, canonicals: List[dict]) -> str:
    """Mode B (incremental assignment) user message.

    ``candidate`` is a dict with keys signature_id, name1, name2, ror_id, lei_id.
    ``canonicals`` is a list of dicts with keys entity_id, institution,
    department, name1, name2, ror_id, lei_id (example name1/name2 of the entity).
    """
    payload = json.dumps(
        {"candidate": candidate, "entities": canonicals},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "Decide whether the candidate signature is the same entity as one of "
        "the listed entities, or a new entity. Return STRICT JSON only:\n"
        '{"decision":"match"|"new"|"uncertain","matched_entity_id":"<id or null>",'
        '"confidence":<0-1>,"reasoning":"<1-2 sentences>"}\n\n'
        f"{payload}"
    )
```

**Exactly what the model sees per signature** — Mode A payload, dedup/adjudicator.py:301-310:

```python
        payload = [
            {
                "signature_id": s.signature_id,
                "name1": s.name1,
                "name2": s.name2,
                "ror_id": s.ror_id or "none",
                "lei_id": s.lei_id or "none",
            }
            for s in bucket
        ]
```

Mode B candidate + canonicals, dedup/adjudicator.py:430-448:

```python
        candidate = {
            "signature_id": sig.signature_id,
            "name1": sig.name1,
            "name2": sig.name2,
            "ror_id": sig.ror_id or "none",
            "lei_id": sig.lei_id or "none",
        }
        canonical_payload = [
            {
                "entity_id": e.entity_id,
                "institution": e.institution or e.signatures[0].name1,
                "department": e.department or e.signatures[0].name2,
                "name1": e.signatures[0].name1,
                "name2": e.signatures[0].name2,
                "ror_id": next((s.ror_id for s in e.signatures if s.ror_id), "none"),
                "lei_id": next((s.lei_id for s in e.signatures if s.lei_id), "none"),
            }
            for e in compatible
        ]
```

Five fields per signature. **Not shown to the model:** any address field, `city`,
`enriched_name`, `record_type`, domain, row count, `row_id`, block id, or the number of
records behind a signature. A canonical entity is represented by its **first signature
only** (`e.signatures[0]`, dedup/adjudicator.py:441-443) — later members are invisible.
`"none"` is the literal string used for a missing id.

**Response parsing — `parse_json_object`, dedup/llm.py:95-121 (verbatim):**

````python
def parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    """Defensively parse a model response as a JSON object.

    Handles plain JSON, fenced ```json blocks, and surrounding prose.
    Returns ``None`` when the text cannot be read as a JSON object — callers
    treat that as "uncertain" rather than failing the block.
    """
    if not raw:
        return None
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Last resort: grab the outermost {...} span and retry.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            return None
    return obj if isinstance(obj, dict) else None
````

`_FENCE_RE` is the fenced-code regex at llm/openai_client.py:75. The request also asks for
`response_format={"type": "json_object"}` (dedup/llm.py:215).

**There is no schema validation.** Beyond `isinstance(obj, dict)`, no key is type-checked;
`confidence` is coerced by `_confidence_to_float` and clamped to `[0,1]`, returning `None`
on anything non-numeric (dedup/adjudicator.py:121-133).

**Malformed / unknown / omitted — the complete fallback table:**

| Situation | Mode A | Mode B | Residue pass |
|---|---|---|---|
| `call.error` set (retries exhausted) | `parsed=None` path: `stats.errors += 1`, **every signature in the bucket** → own entity + `uncertain` (dedup/adjudicator.py:318-334) | same for the one signature (:456-466) | **both** sides of the pair → `uncertain` (:647-655) |
| Unparseable JSON | identical to above | identical | identical |
| Entity references an **unknown** `signature_id` | filtered out by `if sid in by_id` (:340) — silently ignored | n/a | n/a |
| `matched_entity_id` unknown/incompatible | n/a | logged WARNING, **treated as `"new"`** (:489-500) | falls to the `else` branch → both sides `uncertain` (:674-681) |
| A signature claimed by **two** entities | first entity wins; `if sid not in assigned` (:341) | n/a | n/a |
| A signature **omitted** from the response | `decision_counts["missing"]`, own entity + `uncertain` (:379-386) | n/a (one signature per call) | n/a |
| Unrecognised `decision` string | n/a | `else` → own entity + `uncertain` (:511-525) | `else` → both sides `uncertain` (:674-681) |
| `uncertain_signature_ids` entry already assigned | ignored (`if sig is None or sid in assigned`, :367) | n/a | n/a |

Mode A omission handling (dedup/adjudicator.py:377-386, verbatim):

```python
        # Any signature the LLM dropped from its partition: treat as uncertain
        # so it surfaces for review rather than vanishing.
        for sig in bucket:
            if sig.signature_id not in assigned:
                decision_counts["missing"] += 1
                sig.uncertain = True
                entities.append(Entity(
                    entity_id=f"e{next_index}", signatures=[sig], adjudicated=True,
                ))
                next_index += 1
```

### 4.7 `confidence`, `llm_flag`, and `manual_review` semantics

`_emit_rows`, dedup/adjudicator.py:745-795 (verbatim core):

```python
        for sig in ent.signatures:
            for rid in sig.row_ids:
                if sig.uncertain:
                    routing = "manual_review"
                    stats.rows_manual_review += 1
                elif cluster_id is not None:
                    routing = "cluster"
                    stats.rows_clustered += 1
                else:
                    routing = "unique"
                    stats.rows_unique += 1

                # REASONING is an ADJUDICATION signal: surface it for any entity
                # the LLM decided (merged, rejected, or uncertain) so a rejected
                # candidate still records why. An empty Reasoning therefore means
                # exactly "never nominated" (a deterministic collapse / lone
                # bucket that never reached the LLM).
                if ent.adjudicated or sig.uncertain:
                    reasoning = (
                        sig.merge_reasoning
                        if sig.merge_reasoning is not None
                        else ent.reasoning
                    )
                else:
                    reasoning = None
                # CONFIDENCE is a MERGE signal: surface it only for a genuine
                # merge (>=2 signatures) or an uncertain row — never for a pure
                # identical-collapse or a distinct verdict, where a spurious
                # confidence would wrongly trip the election confidence gate.
                if ent.llm_merged or sig.uncertain:
                    confidence = (
                        sig.merge_confidence
                        if sig.merge_confidence is not None
                        else ent.confidence
                    )
                else:
                    confidence = None
```

| Column | Rule | Cite |
|---|---|---|
| `routing` | `manual_review` iff **`sig.uncertain`** — checked **first**, so it wins over `cluster` | dedup/adjudicator.py:747-755 |
| `llm_flag` | `ent.llm_merged` = `len(ent.signatures) >= 2`. **Per-entity, not per-row.** A 100-identical-row cluster is one signature → `llm_flag = False` even though `routing = "cluster"` | dedup/adjudicator.py:89-93, :788 |
| `confidence` | `None` unless the entity has ≥2 signatures **or** the row is uncertain. Per-signature value preferred; entity value is the fallback | dedup/adjudicator.py:770-781 |
| `reasoning` | Emitted iff `ent.adjudicated or sig.uncertain`. **An empty `Reasoning` means exactly "this signature was never nominated to the LLM"** | dedup/adjudicator.py:757-769 |
| `cluster_id` | `cluster_hash(ent.row_ids)` iff the entity holds ≥2 **rows** (not signatures); else `None` | dedup/adjudicator.py:737-743 |

**A block becomes `manual_review` when** any of:

1. `sig.uncertain` set by an LLM `"uncertain"` verdict (Mode A :371, Mode B :513, residue :677).
2. Unparseable/errored LLM response (Mode A :328-329, Mode B :463, residue :651-653).
3. LLM omitted the signature from its Mode A partition (:382).
4. `_enforce_identity_split` fired — conflicting non-empty ROR or LEI (:218).
5. **Candidate cap exceeded → the entire block** (:595-600).
6. **`_reasoning_disowns_membership` → the entire block** (:870-877).

A row can carry a **non-null `Cluster ID` and `Routing = manual_review` simultaneously**
(dedup/adjudicator.py:737-749) — the cluster id is computed from entity membership before
routing is decided. `test_uncertain_signature_routes_manual_review_but_still_clusters`
(tests/test_dedup.py:614) pins this.

### 4.8 `MockDedupLLM` — tests/mocks/dedup_mock.py

Selected via `_get_dedup_llm` when `MOCK_EXTERNAL_CALLS` is truthy (api/routes.py:971-981):

```python
    if settings.mock_external_calls:
        from tests.mocks.dedup_mock import MockDedupLLM
        logger.info("Mock mode enabled — using mock dedup LLM")
        return MockDedupLLM()
    return DedupLLM(settings)
```

Behaviour (tests/mocks/dedup_mock.py:23-66) — **it never merges anything**:

- Mode B / residue (detected by the literal `"Decide whether the candidate"` in the user
  prompt, :37): always `{"decision": "new", "matched_entity_id": null, "confidence": 0.5,
  "reasoning": "Mock: conservative no-merge default."}`.
- Mode A: one entity per signature, `institution: "mock"`, `department: ""`,
  `confidence: 0.5`, same reasoning string (:46-57).
- Telemetry is all zeros; `model = "mock-dedup"`, `model_version = "mock-dedup"` (:26, :64).

**How to tell from the output that the mock ran:**

| Surface | Signal |
|---|---|
| `/api/dedup/cluster-block` JSON | `model == "mock-dedup"` and `model_version == "mock-dedup"` (dedup/adjudicator.py:880, :792-793) |
| **`/api/dedup/file` XLSX** | **No model column is written at all** (`_DEDUP_RESULT_COLUMNS`, api/routes.py:1075; `_DEDUP_DEBUG_COLUMNS`, :1077). The only tell is the literal `Reasoning` string **`Mock: conservative no-merge default.`**, plus `Confidence = 0.5` everywhere and zero multi-signature clusters |
| Logs | `Mock mode enabled — using mock dedup LLM` (api/routes.py:979) |

`MOCK_EXTERNAL_CALLS` is **not set in `.env`**, so the default `False` (config.py:616-618)
applies to a normal local run.

---

## 5. STEP C — EMISSION

### 5.1 `cluster_hash` — dedup/cluster_key.py:13-23 (verbatim)

```python
CLUSTER_ID_PREFIX = "c_"


def cluster_hash(row_ids: Iterable[str]) -> str:
    """``c_`` + first 12 hex of sha256 over the sorted member row_ids.

    Same membership -> same id across runs, machines, and input orderings; a
    membership change -> a new id. String end-to-end (never an int/float).
    """
    joined = ";".join(sorted(row_ids))
    return CLUSTER_ID_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
```

Minted at dedup/adjudicator.py:736-743:

```python
    for ent in entities:
        row_ids = ent.row_ids
        clustered = len(row_ids) >= 2
        if clustered:
            cluster_id: Optional[str] = cluster_hash(row_ids)
            stats.clusters += 1
        else:
            cluster_id = None
```

`ent.row_ids` de-duplicates while preserving order (dedup/adjudicator.py:77-87); the hash
then sorts, so **input order cannot change a cluster id** — pinned by
`test_cluster_id_is_stable_hash_independent_of_input_order` (tests/test_dedup.py:250).

### 5.2 Row → column mapping

`_DEDUP_RESULT_COLUMNS` (api/routes.py:1075-1077) and the write (api/routes.py:1109-1123, verbatim):

```python
_DEDUP_RESULT_COLUMNS = ["Cluster ID", "Routing", "LLM Flag", "Confidence", "Reasoning"]
_DEDUP_DEBUG_SHEET = "Dedup Debug"
_DEDUP_DEBUG_COLUMNS = ["row_id", "Cluster ID", "Block ID", "Signature ID"]
```

```python
    for row_dict, parsed in zip(row_dicts, rows):
        values = [row_dict.get(header, "") for header in headers]
        res = result_by_id.get(parsed.row_id)
        if res is None:
            ws.append([*values, *([""] * len(_DEDUP_RESULT_COLUMNS))])
            continue
        ws.append([
            *values,
            res.cluster_id,
            res.routing,
            res.llm_flag,
            res.confidence,
            res.reasoning,
        ])
        debug_ws.append([res.row_id, res.cluster_id, res.block_id, res.signature_id])
```

| Sheet column | `DedupResultRow` field | Type in the cell | Cite |
|---|---|---|---|
| `Cluster ID` | `cluster_id` | `c_<12 hex>` string, or **empty** for a unique/singleton row | api/routes.py:1117; dedup/models.py:74-77 |
| `Routing` | `routing` | `"cluster"` / `"unique"` / `"manual_review"` | api/routes.py:1118; dedup/models.py:78 |
| `LLM Flag` | `llm_flag` | Python `bool` → `TRUE`/`FALSE` in Excel | api/routes.py:1119 |
| `Confidence` | `confidence` | float or empty | api/routes.py:1120 |
| `Reasoning` | `reasoning` | string or empty (empty ⇒ never nominated) | api/routes.py:1121 |

**Not written anywhere in the workbook:** `model`, `model_version`, `prompt_version`, and
the whole `DedupSummary` (`blocks`, `distinct_signatures`, `llm_calls`, `errors`,
`candidates_generated`, `rejected_with_reasoning`, `candidate_cap_exceeded_blocks`) —
they exist only in the JSON response (dedup/models.py:86-106) and in the structured logs
`dedup_llm_call` / `dedup_block` / `dedup_request` (dedup/adjudicator.py:811-824, :883-899,
:996-1011). **For an external reviewer holding only the XLSX, the summary counters and the
model identity are unrecoverable.**

### 5.3 The `Dedup Debug` sheet, and how to join it

Columns: `row_id`, `Cluster ID`, `Block ID`, `Signature ID` (api/routes.py:1077).

- **Join key: `row_id`** — the debug sheet's `row_id` equals the main sheet's `Customer`
  column value after `_parse_xlsx` stringification (api/routes.py:1123, joined via
  `result_by_id[parsed.row_id]`, :1100, :1111).
- Row order matches the main sheet, minus any row the adjudicator failed to return (those
  get blank result cells on the main sheet and **no debug row at all**,
  api/routes.py:1112-1114).
- `Signature ID` is `s1`, `s2`, … **block-local**: `s1` in one block is unrelated to `s1`
  in another (dedup/signatures.py:171-172). **Always join `Block ID` + `Signature ID`
  together**; `Signature ID` alone is meaningless across blocks.
- `Block ID` is `blk-<12 hex sha1>` when derived (dedup/signatures.py:62), or the caller's
  verbatim trimmed value when supplied (dedup/signatures.py:122-123).

### 5.4 File-level vs block-level id restart caveats

| Caveat | Detail | Cite |
|---|---|---|
| `cluster_id` — **no restart, no renumbering** | It is a content hash of member `row_id`s, globally unique by construction; `cluster_blocks` explicitly performs no cross-block renumbering | dedup/adjudicator.py:972-974, :731-733 |
| `signature_id` — **restarts at `s1` per block** | `enumerate(signatures, start=1)` inside `build_signatures`, which is called once per block | dedup/signatures.py:171-172, dedup/adjudicator.py:841 |
| `entity_id` (`e1`, `e2`, …) — **restarts per block, and is never emitted** | Mode A/B each start `next_index = 1`; `entity_id` appears in no output column and no log record | dedup/adjudicator.py:284, :414 |
| Duplicate `row_id`s in the input | `result_by_id` is a dict keyed on `row_id` so the last result wins for every duplicate; `cluster_hash` sorts a list that may contain the id twice, changing the digest | api/routes.py:1100; dedup/cluster_key.py:22 |
| Re-running `/api/dedup/file` on its own output | The five result columns are unrecognised headers → re-appended a second time; `_copy_extra_sheets` (:331-353) copies the existing `Dedup Debug` sheet in, and because a sheet of that name was already created at :1106, openpyxl renames the copy to **`Dedup Debug1`** (verified) | api/routes.py:1104-1107, :1125-1126 |
| Extra sheets | Every non-active sheet of the upload is copied values-only into the output, after the result sheets | api/routes.py:331-353 |

---

## 6. DETERMINISM & CACHING

### 6.1 Caching: there is none

**No fixture layer, no response cache, and no replay mode exists for dedup LLM calls.**
`DedupLLM.adjudicate` calls `client.chat.completions.create(**params)` directly
(dedup/llm.py:239) with no cache lookup, no key derivation and no persistence. A grep for
`cache`/`fixture` across `dedup/*.py` returns exactly one hit, an unrelated docstring
(dedup/scoring_xlsx.py:152). `EVIDENCE_CACHE_DIR` in `.env` belongs to the **Phase 1
enrichment** evidence cache and is never consulted by Phase 2. **Every `/api/dedup/file`
run makes fresh model calls.**

The only stand-ins are test doubles: `ScriptedLLM` (tests/test_dedup.py:41-66) and
`MockDedupLLM` (tests/mocks/dedup_mock.py:23) — neither is a recorder.

### 6.2 Sampling parameters actually sent — dedup/llm.py:206-237 (verbatim)

```python
        for attempt in range(self._max_retries):
            start = time.perf_counter()
            params: dict[str, Any] = {
                "model": self._deployment,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
            ...
            params["top_p"] = LLM_TOP_P
            if self._use_seed:
                params["seed"] = LLM_SEED
            if self._use_reasoning_effort:
                params["reasoning_effort"] = self._reasoning_effort
            ...
            elif self._use_temperature:
                params["temperature"] = self.TEMPERATURE
```

| Parameter | Value | Sent? | Cite |
|---|---|---|---|
| `top_p` | `1.0` | **always** | dedup/llm.py:223; llm/openai_client.py:103 |
| `seed` | `42` | always, until the deployment rejects it once | dedup/llm.py:224-225; llm/openai_client.py:108 |
| `reasoning_effort` | `"low"` | when `DEDUP_REASONING_EFFORT` is non-empty (it defaults to `"low"`) | dedup/llm.py:148, :226-227 |
| `temperature` | `0.0` | **only when `reasoning_effort` is inactive** — mutually exclusive | dedup/llm.py:138, :228-237 |
| `max_completion_tokens` | 1000 at both application call sites; the method default 4000 is unreachable from the app | dedup/llm.py:195, :214; dedup/adjudicator.py:452, :638 |
| `response_format` | `{"type": "json_object"}` | always | dedup/llm.py:215 |

Because `DEDUP_REASONING_EFFORT` defaults to `"low"` and `.env` does not override it,
**on the current configuration `temperature` is NOT sent** — the deployment's default
sampling temperature applies, with `top_p=1.0` and `seed=42` as the only pins.
`docs/thesis/03_ALGORITHMS.md:6026` still claims no `seed` and no `top_p` are sent; that
claim is **stale** — commits `3527585` (2026-08-18) and `55b9e33` (2026-08-25) added them.

Each of the three parameters has a one-shot runtime disable on rejection
(dedup/llm.py:255-280), and the flags live on the `DedupLLM` **instance** — a fresh
instance is built per HTTP request (api/routes.py:1191), so a rejection is re-probed on
every request.

### 6.3 Candidate & call ordering

| Stage | Order | Deterministic? | Cite |
|---|---|---|---|
| Block iteration | `OrderedDict` first-seen | yes | dedup/signatures.py:129-133 |
| Blocks executed | `asyncio.gather(...)` — results returned in submission order | yes (results); call interleaving is not, but blocks share no state | dedup/adjudicator.py:957-962 |
| Signatures within a block | first appearance | yes | dedup/signatures.py:144, :170-172 |
| Mode A buckets | `True` (populated Name 2) then `False` | yes | dedup/adjudicator.py:286-291 |
| Mode B assignment | sequential `for` with `await` | yes, but **order-dependent and greedy** | dedup/adjudicator.py:416 |
| Residue candidates | `sort_key = (rule_rank, -score, a, b)`; `id < name < token` | yes | dedup/candidates.py:115-120, :195 |
| Union-find roots | lowest index stays root; groups emitted in `sorted(groups)` | yes | dedup/adjudicator.py:612-616, :689 |
| Output rows | entity order within block, block order across the file | yes; the sheet re-imposes input order via the `row_id` join | dedup/adjudicator.py:736-746; api/routes.py:1109-1111 |

### 6.4 Is a rerun on the same file byte-identical?

**No — not guaranteed.** Everything in this repository is deterministic; the model is not.

Deterministic by construction: signature building, block derivation, bucket order, candidate
nomination and ordering, union-find, cluster hashing, sheet assembly.
`test_residue_determinism_under_input_shuffle` (tests/test_dedup.py:555) pins identical
assignments and identical LLM-call counts under a shuffled input.

What would break byte-identity:

1. **Model non-determinism.** `seed=42` and `top_p=1.0` are requests, not guarantees; on a
   reasoning deployment `temperature` is not sent at all (§6.2), so the effective
   temperature is the deployment default. Any verdict flip changes membership → a new
   `cluster_hash` → new `Cluster ID` values.
   `docs/thesis/00_OPEN_ITEMS.md:454` (item 80) records this as unmeasured.
2. **Free-text `Reasoning`.** Even with identical clustering, the model's prose differs
   between runs; it is written verbatim to the sheet (api/routes.py:1121).
3. **Runtime parameter fallbacks.** A transient 400 on `seed`/`temperature`/`reasoning_effort`
   disables that parameter for the remainder of the request only (dedup/llm.py:255-280),
   so run N and run N+1 can send different parameter sets.
4. **Retries.** Up to 3 attempts with 0.5·2ⁿ backoff on 429/5xx (dedup/llm.py:281-288); an
   exhausted retry converts a decision into `uncertain` (:456-466), changing routing.
5. **Cap tripping.** A block near `MAX_CANDIDATES_PER_BLOCK = 50` flips the entire block to
   `manual_review` if the nomination count crosses the cap — deterministic given identical
   entities, but the entities themselves come from LLM verdicts (dedup/adjudicator.py:585-601).
6. **Input typing.** A `row_id` that changes string form between exports (`"12345"` vs
   `"12345.0"`, api/routes.py:282) changes both the join key and every `cluster_hash` it
   participates in.
7. **Config drift.** `SIG_PARTITION_THRESHOLD` changes the mode (A vs B) and therefore the
   whole call structure; the candidate thresholds change which pairs are ever asked about.

---

## 7. KNOWN FAILURE MODES

### 7.1 In-code warnings

`dedup/*.py` contains **no `TODO`, `FIXME`, `XXX`, `HACK` or `⚠` markers** (verified by
grep). The hazards are stated as docstrings and log warnings instead:

| Hazard | Behaviour | Cite |
|---|---|---|
| LLM mixes empty/populated Name 2 in Mode A | `logger.warning("Dedup: LLM merged empty- and populated-Name2 signatures…")`, deterministic split | dedup/adjudicator.py:150-153 |
| Conflicting ROR/LEI in one entity | `logger.warning("Dedup: entity %s merged conflicting %s ids %s; splitting to manual_review")` | dedup/adjudicator.py:209-212 |
| Candidate cap exceeded | `logger.warning("… routing the whole block to manual_review")` | dedup/adjudicator.py:587-590 |
| Mode B match to unknown entity id | `logger.warning("… treating as new")` | dedup/adjudicator.py:490-493 |
| Reasoning contradicts membership | `logger.warning("… routing the whole block to manual_review")` | dedup/adjudicator.py:871-874 |
| Unusable LLM response (Mode A / B) | `logger.error(...)`, `stats.errors += 1` | dedup/adjudicator.py:323-327, :459-462 |
| Unrecognised XLSX header | `logger.warning("dedup file: %d column header(s) matched no DedupRow field…")` | api/routes.py:1055-1061 |
| Coarse phrase matching is deliberate | `"Read ONLY to demote toward manual_review — never to merge — so a coarse phrase match is the safe direction"` | dedup/adjudicator.py:237-240 |
| `_resolve_candidate_config` env branch shadowed by `Settings` defaults | see §4.2 — env fallback unreachable on the HTTP paths | dedup/adjudicator.py:903-914; config.py:605-613 |

### 7.2 Documented `⚠` items for the clustering path — and their current truth

From `docs/thesis/` (**verified against `HEAD = b8d62f8`**, since several are stale):

| Item | Claim | Status at HEAD |
|---|---|---|
| 03_ALGORITHMS.md:5536 / OPEN_ITEMS #159 | `⚠ NO FIXTURE COVERAGE` — all-empty-address rows share one derived block | **Still true.** No test in tests/test_dedup.py exercises it |
| 03_ALGORITHMS.md:5599 / OPEN_ITEMS #160 | `⚠` — `_DEDUP_HEADER_ALIASES` has no `leiid` key, so LEI is dropped on the file route | **STALE — fixed** by `7399df8` (2026-08-19); `"leiid"` and `"lei"` are present at api/routes.py:1017-1018. Fixture coverage for the file route is still absent |
| 03_ALGORITHMS.md:6026 / OPEN_ITEMS #80 | `⚠` — the request sends no `temperature` and no `seed` | **STALE.** `top_p=1.0` and `seed=42` are now always sent (dedup/llm.py:223-225); `temperature=0.0` is sent only when `reasoning_effort` is inactive. The underlying concern — the *effective server-side* temperature is unmeasured — stands |
| 03_ALGORITHMS.md:5710, :5996 / OPEN_ITEMS #161 | `⚠ UNVERIFIED` — no prompt clause restricts the model to supplied evidence; dedup/prompts.py:36 invites world knowledge | **Still true.** Verified absent across dedup/prompts.py:19-39 |
| 03_ALGORITHMS.md:5780 | `⚠ UNVERIFIED` — Mode B is greedy in signature order; an early wrong `"new"` can only be undone by the residue pass or the guards | **Still true.** No test exercises a Mode B ordering pathology |
| 03_ALGORITHMS.md:5925 / OPEN_ITEMS #163 | `⚠ UNVERIFIED (no fixture)` — a non-merge phrasing outside `_NONMERGE_MARKERS` is missed | **Still true.** 7 markers, dedup/adjudicator.py:241-249; no fixture for a missed phrasing |
| OPEN_ITEMS #79 | `⚠ UNVERIFIED` — whether the deployment honours `reasoning_effort="low"` | Still open; a rejection is caught at dedup/llm.py:255-261 but silence is not proof of honouring |
| OPEN_ITEMS #7, #8 | `⚠ UNDOCUMENTED` — why `max_tokens` defaults to 4000 while both call sites pass 1000 | Confirmed: dedup/llm.py:195 vs dedup/adjudicator.py:452, :638. A verdict JSON exceeding 1000 completion tokens is truncated → unparseable → `uncertain` |
| OPEN_ITEMS #172 | `⚠ MEASUREMENT REQUIRED` — `expected_cluster` / `expected_routing` ground truth exists in no repository workbook, so `eval/dedup_eval.py` returns its zero-guard values | Still true for tracked workbooks; the untracked `docs/thesis/dedup_STRESS_200_v1-verified.xlsx` is not in git |
| 03_ALGORITHMS.md:5442 | The upstream address gate that assigns `Block ID` is `⚠ NOT DOCUMENTABLE FROM THIS REPOSITORY` | Still true |

### 7.3 Failure modes visible in code but not in any doc

1. **The Name 2 asymmetry rule is bypassable by the residue pass** (§4.3). The strongest
   deterministic rule in the system does not survive Step B's widening.
2. **A canonical entity is represented to the LLM by its first signature only**
   (dedup/adjudicator.py:441-443, :546-553). In Mode B, once an entity has three members,
   the model still judges against member #1 — a drift path for chained merges.
3. **Residue merges are transitive without transitive verification**
   (dedup/adjudicator.py:624-625): `A~B` + `B~C` yields `{A,B,C}` with no `A~C` call.
4. **Building/suite/floor columns never reach blocking** (§1.4), so a multi-tenant building
   with one street address is a single block.
5. **`enriched_name` and `city` are accepted and never read** (dedup/models.py:48, :52).
6. **Conflicting `ror_id`s among rows of one signature are silently dropped** — the first
   non-empty wins (dedup/signatures.py:165-168), so `_enforce_identity_split` cannot see
   an intra-signature conflict.
7. **Blank spreadsheet rows are dropped**, so the output row count can be lower than the
   input (api/routes.py:285-286).

### 7.4 Tests that pin current behaviour — tests/test_dedup.py

Driven by `ScriptedLLM` (tests/test_dedup.py:41-66) against `cluster_blocks` directly.

| Test (line) | Pins |
|---|---|
| `test_normalize_key_collapses_punctuation_and_whitespace` (:83) | punctuation → space, whitespace collapse, lowercase |
| `test_build_signatures_collapses_identical_rows` (:90) | byte-identical rows → one signature |
| `test_build_signatures_captures_ror_and_lei` (:98) | first non-empty `ror_id`/`lei_id` adoption |
| `test_100_identical_rows_one_cluster_no_llm` (:112) | 100 rows → 1 signature → 1 cluster of 100, **zero LLM calls** |
| `test_same_institution_different_department_not_merged` (:136) | same Name 1 + same ROR + different Name 2 → two entities |
| `test_conflicting_ror_not_merged_verdict_guard` (:170) | `_enforce_identity_split` on two different non-empty RORs |
| `test_conflicting_lei_not_merged_verdict_guard` (:215) | same guard for LEI |
| `test_cluster_id_is_stable_hash_independent_of_input_order` (:250) | `c_`-prefixed content hash, order-independent |
| `test_cross_language_abbreviation_merge` (:280) | `Dept of Mechanical Eng` ≡ `Department of Mechanical Engineering` on an LLM merge |
| `test_lei_and_ror_hints_passed_to_llm` (:315) | both ids appear in the outgoing prompt |
| `test_name2_asymmetry_pair_is_nominated_and_adjudicated` (:353) | **cross-boundary pairs are nominated and the LLM verdict wins** (Option A) |
| `test_name2_asymmetry_split_when_llm_violates_within_bucket` (:386) | in-code split when a Mode A entity mixes empty/populated Name 2 |
| `test_residue_same_lei_cross_boundary_merges_on_match` (:440) | converging LEI across the boundary merges on `"match"` |
| `test_residue_distinct_records_reasoning_on_reject` (:460) | a rejected nomination stays unique **but carries reasoning** |
| `test_residue_singleton_joins_multi_member_signature` (:481) | a lone signature joins a 3-row signature by LEI convergence |
| `test_mode_a_reject_now_carries_reasoning` (:501) | same-bucket singletons rejected by Mode A still carry reasoning |
| `test_no_signal_pair_not_nominated_reason_empty_ok` (:516) | **empty `Reasoning` is legitimate only for a never-nominated pair** |
| `test_candidate_cap_routes_block_to_manual_review` (:532) | over the cap → whole block `manual_review`, id-pairs ordered first |
| `test_residue_determinism_under_input_shuffle` (:555) | shuffled input → identical assignments and identical call count |
| `test_step_a_does_not_collapse_suffix_variants` (:579) | `Pfizer AG` ≠ `Pfizer Inc.` at Step A |
| `test_singleton_is_unique_no_cluster` (:595) | one row → `unique`, `cluster_id` null |
| `test_uncertain_signature_routes_manual_review_but_still_clusters` (:614) | **`manual_review` coexists with a non-null `Cluster ID`** |
| `test_mode_b_canonical_assignment_produces_correct_clusters` (:651) | N just above the threshold → Mode B, correct N-way clusters |
| `test_mode_b_respects_name2_boundary_without_llm` (:691) | Mode B never compares across the boundary — no call for that decision |
| `test_parse_json_object_variants` (:716) | plain JSON, fenced blocks, prose-wrapped |
| `test_malformed_llm_response_marks_signatures_uncertain` (:726) | a bad response never fails the block |
| `test_llm_error_result_marks_uncertain` (:741) | exhausted retries → flagged, block completes |
| `test_is_unsupported_reasoning_effort_detection` (:812) / `test_adjudicate_drops_reasoning_effort_and_recovers` (:824) | one-shot `reasoning_effort` fallback |
| `test_is_unsupported_temperature_detection` (:873) / `test_adjudicate_drops_temperature_and_recovers` (:931) | one-shot `temperature` fallback |
| `test_temperature_not_sent_while_reasoning_effort_is_active` (:896) | the mutual exclusion in §6.2 |
| `test_temperature_sent_when_reasoning_effort_disabled` (:917) | `temperature=0.0` sent once `reasoning_effort` is off |
| `test_blocks_processed_independently_and_block_id_derived` (:952) | derived block ids; blocks independent |
| `test_route_cluster_block_identical_rows` (:984) / `test_route_empty_rows_rejected` (:1003) | HTTP wiring for the JSON route |

**No test in this file drives `/api/dedup/file`.** The XLSX path — alias binding,
`_parse_xlsx` cell handling, the `Dedup Debug` join, `_build_dedup_xlsx` — is
**unexercised by tests/test_dedup.py**. `tests/test_dedup_eval.py` (105 lines) tests only
the offline metric harness `eval/dedup_eval.py`, over a synthetic in-memory workbook — no
adjudicator involvement.

`tests/KNOWN_FAILURES.md` lists 8 stable pre-existing failures (`8 failed, 3311 passed,
7 skipped`); **none is in the dedup cluster** — they are Tier 2A gate (3), mock-path
classification (2), and 3 singles in orchestrator / name-slot-parity / issues-compare.

---

## 8. RECENT CHANGES

### 8.1 `git log --oneline -30 -- dedup/ api/routes.py` (verbatim output)

```
96dd528 Update README and codebase to enhance issue detection and flag handling
600d729 Issues endpoiint added
0c057bc Suppress specific G6 and G7 issue codes from the `/issues` audit column
55b9e33 Changes
8d5f5f9 Update issue detection and reporting for enriched data
b8ad102 Enhance name handling by adding support for five name slots
5e423c2 Fix 8: flag model redesign
7399df8 3.3 Bind the LEI column on the dedup file upload path
3527585 3.2 Send temperature=0.0 on dedup calls where the deployment allows it
8f2bb6b Align /api/dedup/score JSON with the score/file column contract
929492b Implement residue candidate nomination and adjudication process
994fb3b Enhance scoring logic to prevent false recency suppression warnings
c18921d Refactor scoring logic to align with Bernd's year-priority rule
efe1379 Enhance deduplication process with confidence-based election and manual review
611c348 Phase 2 thesis
600823c LLM changes with vpn
b9f772a Add /api/dedup/file endpoint for XLSX uploads
13a1274 Deduplication endpoint
eee57b7 Enhance configuration and address processing for department-domain probing
2bb9d23 Add Remaining Issues sheet to XLSX comparison output
25f89d2 Add SAP master-data fields and issue detection enhancements
b19cd1a Add XLSX file handling and enrichment logic
9938596 Search term and azure deployment
f77080b Initial Enrichment Code
```

(24 commits — the path has fewer than 30.)

### 8.2 Which of those touched clustering, and when

| Commit | Date | Files under the clustering path | Clustering-relevant? |
|---|---|---|---|
| `96dd528` | 2026-09-03 | api/routes.py only (+75/−12) | No — `/issues` audit column |
| `600d729` | 2026-09-03 | api/routes.py only | No — issues endpoint |
| `0c057bc` | 2026-08-28 | api/routes.py only | No — G6/G7 suppression |
| `55b9e33` | 2026-08-25 | **dedup/llm.py** (+32/−23) | **Yes** — added `top_p` and `seed` to the adjudication call |
| `8d5f5f9` | 2026-08-20 | api/routes.py only | No |
| `b8ad102` | 2026-08-20 | **dedup/signatures.py** (+46/−10), **dedup/models.py** (+6), api/routes.py (+3) | **Yes** — `name3`/`name4`/`name5` added; `department_text` began reading the whole block |
| `5e423c2` | 2026-08-20 | api/routes.py only | No — flag model |
| `7399df8` | 2026-08-19 | api/routes.py (+25/−1) | **Yes** — `"leiid"`/`"lei"` aliases on the file route |
| `3527585` | 2026-08-18 | **dedup/llm.py** (+74/−11) | **Yes** — `temperature=0.0` with the mutual-exclusion rule |
| `8f2bb6b` | 2026-08-03 | dedup/scoring* | No — Pass 3 |
| `929492b` | 2026-07-23 | **dedup/adjudicator.py** (+327), **dedup/candidates.py** (+196, new) | **Yes** — the residue nomination + adjudication pass |
| `efe1379` | 2026-07-22 | **dedup/adjudicator.py** (+184), **dedup/cluster_key.py** (new), models, signatures | **Yes** — content-hash `cluster_id`, confidence/manual-review semantics |

### 8.3 What changed since the last evaluation run — summary

**The last recorded evaluation runs are Phase-1 enrichment runs, not clustering runs.**
`eval/out/RUNS.md:1-40` documents strata S1/S4/S5 scored against the issue catalogue at
commits `d3a3cfc` → `f57782f` → `327ee53`; there is no dedup/clustering evaluation among
them, and `eval/dedup_eval.py` cannot produce one because no tracked workbook carries
`expected_cluster` / `expected_routing` (docs/thesis/00_OPEN_ITEMS.md:580, item 172).
Note also that `d3a3cfc`, `f57782f` and `327ee53` **do not resolve in this working tree**
(`git log` → `bad revision`), so a `327ee53..HEAD` range cannot be computed here; the
comparison below is by date.

Since the most recent evaluation artefact (`f292bfa`, "Evaluation rerun at 327ee53",
2026-09-02), **no commit has touched `dedup/` at all** — the commits after it
(`e396722`, `a17a2e0`, `96dd528`, `5a8c3c5`, `b8d62f8`) are registry-anchoring,
department-matching and issue-detection work in the Phase-1 enrichment path plus the
`/issues` audit column in `api/routes.py`. The clustering algorithm has been frozen since
**`55b9e33` (2026-08-25)**.

Taking the whole span the artefacts cover, four changes altered clustering behaviour and
would each shift a `Cluster ID` distribution:

1. **`929492b` (2026-07-23) — the residue pass.** The largest behavioural change in the
   module. It introduced cross-`has_name2` and singleton nomination
   (dedup/candidates.py:159-168), pairwise adjudication, union-find merging
   (dedup/adjudicator.py:603-714) and the per-block candidate cap. **It made the Name 2
   asymmetry rule non-absolute** (§4.3) and made "empty `Reasoning` ⇒ never nominated" the
   contract.
2. **`b8ad102` (2026-08-20) — five name slots.** `department_text` now joins Names 2-5 with
   `" / "` (dedup/signatures.py:72-76). Rows whose unit text sits in Name 3 or Name 4
   previously read as `has_name2 == False` and now read as `True` — which changes the
   Mode A bucket a signature lands in, changes signature identity itself, and therefore
   changes both clustering and the `s<N>` ids on the debug sheet. **Any clustering
   baseline taken before 2026-08-20 is not comparable to a current run.**
3. **`7399df8` (2026-08-19) — LEI binding on the file route.** Before it, `LEI ID` was
   dropped from XLSX uploads, so `_ids_converge` (dedup/candidates.py:123-127) never fired
   on LEI and `_enforce_identity_split` never saw an LEI conflict on the file path. Both
   now fire — new merges *and* new `manual_review` splits relative to any pre-August file run.
4. **`3527585` + `55b9e33` (2026-08-18 / 08-25) — sampling parameters.** `top_p=1.0` and
   `seed=42` are now always sent, `temperature=0.0` when `reasoning_effort` is inactive
   (dedup/llm.py:223-237). Intended to tighten reproducibility; it also means verdicts
   from before those commits were produced under a different sampling regime.

---

# v2 (flagged)

Three env flags, all default-false, each gating one change; they work
independently and together. With all three off the output is byte-identical to
v1, which `tests/test_dedup_v2_flags_off.py` asserts against a recorded run of
the 200-row stress batch.

| Flag | Change | Module | Reader |
|---|---|---|---|
| `DEDUP_V2_BLOCKING` | delivery-point blocking | dedup/address.py (new) | dedup/flags.py:40 |
| `DEDUP_V2_NAME2` | classify the text below Name 1 | dedup/name_slots.py (new) | dedup/flags.py:45 |
| `DEDUP_V2_ID_CONFLICT` | route an ROR/LEI conflict | dedup/adjudicator.py:309 | dedup/flags.py:50 |
| *(any of the three)* | the `Link ID` column | dedup/adjudicator.py:1026 | dedup/flags.py:55 |

---

## v2 · B — delivery-point blocking (`DEDUP_V2_BLOCKING`)

v1 blocks on `sha1(country \| postal_code \| street \| house_no)` with each part
only case- and punctuation-folded (§2.1). Two records at one door fall into
different blocks whenever the address was typed differently, and **nothing
downstream can merge across blocks** (§2.4). That is the largest single source
of missed duplicates in the stress batch: 15 of the 35 MUST_MERGE groups were
`unique` in v1 purely because their rows never met.

| Piece | Where | What it does |
|---|---|---|
| `parse_address` | dedup/address.py:160 | `(country, zip5, house, street_core, city_norm, house_hint)` |
| `block_keys` | dedup/address.py:205 | `z:country\|zip5\|house`, `c:country\|city\|house`, `f:country\|zip5` |
| `_v2_blocks` | dedup/signatures.py:193 | union-find over the keys; rows sorted by `Customer` |
| `address_compatible` | dedup/address.py:276 | `exact` / `fuzzy` / `partial` / `incompatible` |
| `streets_compatible` | dedup/address.py:233 | JW ≥ 0.85 **or** the token rule, with a numeric veto |
| `street_match` | dedup/address.py:307 | the model-facing label: `exact\|fuzzy\|differs\|unknown` |
| `_address_gate` | dedup/adjudicator.py:746 | residue eligibility (B.4) |
| `_enforce_address_split` | dedup/adjudicator.py:367 | splits an entity spanning incompatible points |

**The house number is only a house number if a street survives beside it**
(dedup/address.py:180-190). A `Street 1` of `38` leaves nothing once the number
is taken out, so the row is house-less and `38` is kept as `house_hint`.
Treating it as a door would have blocked that PAVIR record against every real
house 38 in 94304.

**House-less rows never enter a house-bearing block.** They block only with
each other, in the `f:` fallback space, and any cluster they form routes to
`manual_review` with reason `unverified delivery point`
(dedup/adjudicator.py:999). An address nobody can verify is not evidence that
two records share a door. Their relationship to the verified blocks is carried
by the `Link ID`, never by the `Cluster ID` — which is what makes
`13120409` (Name 1/2/3 byte-identical to `13036862`'s) stay out of that
building's cluster.

**The city key pays for itself.** `c:country|city|house` survives a zip typo —
UCSF at `94103` and `94143`, one door on Folsom St — and `_enforce_address_split`
is what stops that widening also licensing a merge across two genuinely
different doors that share a house number in one city.

**The street check never reaches the model as "incompatible".** Two records only
reach one prompt after zip and house already matched, so a failed string
comparison of the street is not evidence they are at different addresses;
`street_match` says `differs`, and the prompt says so explicitly.

---

## v2 · C — Name-2 slot classification (`DEDUP_V2_NAME2`)

v1 treats every populated slot below Name 1 as a department (§3.2), and the
deterministic asymmetry rule then forbids a departmental record from ever
sharing an entity with a bare one (§4.3). The rule is right. The premise was
not: in this batch that slot also holds delivery desks, trading names, Name 1's
own tail, the institution itself, and people.

`classify_slots` (dedup/name_slots.py:345) returns
`(institution, department, aliases, kind, hints)` with `kind` one of
`none / logistics / alias / overflow / institution / institution_split /
contact / department`. Precedence is load-bearing and documented at the
function. Phase 1's detectors are imported, never re-implemented:
`has_no_canonical_form` (enrichment/search_terms.py:678), the DBA regexes
(enrichment/preprocess.py:613), `_CO_ATTN_PREFIX_RE` and `_person_candidate`.

| Kind | Count in the batch | Example |
|---|---|---|
| `none` | 135 | no text below Name 1 |
| `department` | 43 | `Fairchild Science` |
| `logistics` | 7 | `Central Receiving`, `Accounts Payable` |
| `institution_split` | 6 | `EMD Serono, Inc.` + `Research and Development Institute` |
| `alias` | 4 | `DBA Lee Health`, `A Kimball Electronics Company` |
| `overflow` | 2 | `American School of Classical Studies at` + `Athens` |
| `institution` | 2 | Name 1 `GHW23`, Name 2 `Case Western Reserve University` |
| `contact` | 1 | `Emanuela Zacco - LCA Core` |

The signature key becomes `(normalize_key(institution), normalize_key(department))`
and `has_name2` becomes a statement about the department found
(dedup/signatures.py:302, :131). Six columns the file route used to drop are now
bound (api/routes.py:1082); `Building` is bound as a **hint only** and reaches
neither blocking nor the key.

**`institution_split` selects the name from the block, never composes it**
(dedup/name_slots.py:254). Concatenating two slots invents a third spelling that
no record states and no registry holds. Both original slot values are kept as
**hints** — not aliases: a fragment of a split name is not another name for the
whole institution, and filing it as an alias let `EMD Serono, Inc.` (half of the
institute's name, and separately a different company's entire name) be matched
against that company.

**Two thresholds are tighter than the change request stated**, both raised and
each for one counterexample — see § C.5 similarity thresholds below.

**Prompt.** `p2-dedup-v8` (dedup/prompts.py:22, :66). Per record the model sees
institution, department, aliases, operating_name, suggested_name, record_type,
ror_id, lei_id, street_match and hints (dedup/adjudicator.py:763), plus an
`evidence:` block of deterministic same-institution signals computed for every
pair in the call (dedup/candidates.py:308, rendered at dedup/prompts.py:144).
The response carries a required `institution_relation` per signature — a
separate question from the entity decision, and the one the `Link ID` reads.

---

## v2 · D — id-conflict routing (`DEDUP_V2_ID_CONFLICT`)

v1 answers a hard-identifier conflict by exploding the entity into singletons
and flagging each (§4.4). That is the one outcome that cannot be right: either
the records are the same and the split is wrong, or they are different and the
ids did their job — and in both cases what a steward needs is the **pair**, with
both ids named. Split apart they become two unremarkable unique rows.

`_route_identity_conflict` (dedup/adjudicator.py:309) keeps the entity and its
`Cluster ID`, marks every member uncertain, and writes
`id conflict: ROR <a> vs <b>`. Two determinism traps were closed on the way:
`_distinct_nonempty` (dedup/adjudicator.py:184) returns a **set**, so the ids
were rendered in hash order — `_ordered_distinct` (:250) preserves first
appearance; and an entity's `signatures` list is in the order the model wrote
the ids, so the pair is re-sorted off the block's signature ids (`_signature_order`,
:266). `_inferred_from_short_name` (:282) appends a provenance note when the
column says an id was **not** registry-verified and the name it came from is one
or two tokens; it is silent when the column says verified and silent when the
column says nothing.

---

## v2 · Link ID

The third outcome. Before it the file had two — a `Cluster ID`, or nothing — so
a pair that is one organisation and two records had to be reported either as a
duplicate (overstating) or as unique (losing the finding).

`Link ID` answers **same organisation?**; `Cluster ID` answers **same record?**
The two are computed independently on purpose: deriving the link from the merge
outcome would make it say nothing the cluster does not already say, and the
pairs worth linking are exactly the ones that did NOT merge.

Two signatures are one family when any of these holds (dedup/adjudicator.py:1026):

1. they share a ROR or LEI — a registry says so;
2. a deterministic `evidence` line fires and the model called the institutions
   the **same** on either side;
3. a line fires and the model was **uncertain**;
4. a line fires and the model said **different** — a conflict between two
   sources that both have standing. It links *and* routes to review: a flag
   with no connection is useless to whoever opens the workbook.

Families are joined across blocks (`_merge_link_maps`, dedup/adjudicator.py:1120)
because an institution family is not a property of one delivery point. Within a
block the model's verdicts apply; across blocks the shared registry id is the
only evidence there is, and the within-block families join through it.

**Derivation and stability.** `link_hash` (dedup/cluster_key.py) is
`l_` + sha256 over the **sorted member row_ids** — membership-derived, exactly
like `cluster_hash`, with a different prefix so a reader can tell the two apart
at a glance. It is deliberately *not* keyed on `(institution, country)`: a
family spans several spellings by construction (`HGST Inc` / `Hitachi Global
Storage Technologies`), and electing one of them as the id's basis would be an
arbitrary vote that changes whenever the elected row changes. Membership-derived
means the id survives a re-run and a re-ordered export and changes when a row
joins or leaves — the same contract `Cluster ID` already carries. Pinned by
`test_link_ids_are_stable_across_runs_and_input_order` and
`test_link_ids_do_not_collapse_across_unrelated_blocks`
(tests/test_dedup_v2_blocking.py). The second exists because the cross-block
union-find was first keyed on `signature_id`, which restarts at `s1` in every
block (§5.4) — every block's first signature was unioned with every other
block's and the entire file came out carrying **one** Link ID.

The column is written only while a v2 flag is on (api/routes.py:1151-1157), to
the main sheet beside `Cluster ID` and to the `Dedup Debug` sheet.

---

## v2 · fixture outcomes

35 MUST_MERGE groups, 17 MUST_NOT_MERGE entries, 9 MUST_LINK groups, 3
link-for-review entries, 1 xfail. v1 column from the recorded run in the
workbook; v2 column from `tools/dedup_v2_real_model_run.py` replaying the
committed cache (`gpt-5.4-2026-03-05`, temperature 0, `p2-dedup-v8`).

| Group | v1 | v2 | Rule that changed it |
|---|---|---|---|
| UTSA, MedicalCity, UTRGV, Covia, GES_Qume, Merck_Rahway, Bruker, PAVIR_Miranda, UCSF_Folsom | unique | clustered | B — one delivery point, one block |
| JFK, Marian, Methodist, Hoag, StElizabeth | split across blocks | clustered | B — house number recovered from `Street 1` |
| CWRU2080, CWRU2109 | unique | clustered | C — `institution` (Name 1 was `GHW23` / `KMB3 LLC`) |
| GES_Hellyer, GES_Qume | unique | clustered | C — `alias` (`A Kimball Electronics Company`) |
| UTSA | unique | clustered | C — `logistics` (`Central Receiving`) |
| UCSF_Folsom | unique | clustered | C — `logistics` + `contact` |
| Shell, PAVIR_Miranda | unique | clustered | C — `overflow` / `institution_split` |
| EMD_RDI | split | clustered | C — `institution_split` |
| USC_Norris, Army_ACC, VA_Dallas | unique | clustered | B + the model |
| Lee, USG | unique / clustered | clustered, `manual_review` | B — address-less cluster |
| Stanford_family, Army_family, Merck_MRL | split | linked, not clustered | Link ID — same institution, different department |
| HGST, Merck, Scripps, NASA_Ames, PAVIR | split | linked across blocks | Link ID — shared registry id |
| UTSW | unique | linked, `manual_review` | Link ID arm 4 — evidence vs the model |
| EMD_family | split | linked, `manual_review` | Link ID arm 4 — shared ROR vs the model |
| Scripps_Activity | split to singletons | clustered, `manual_review` | D — id conflict routed, not exploded |
| NIST | unique | unique | unchanged — Phase 1 overflow, upstream |

Every MUST_NOT_MERGE entry holds in both v1 and v2; v1 held them by being
conservative, v2 holds them on the delivery point, the department rule and the
distinctive-token rule.

### The 17 `manual_review` rows

| Reason category | Rows | Which |
|---|---|---|
| conflict (evidence or shared id vs the model's "different") | 9 | 5 EMD Serono, 2 UTSW, 2 Contra Costa |
| address-less (cluster at an unverifiable delivery point) | 6 | 2 Lee, 2 USG, 2 PAVIR |
| id conflict (ROR/LEI disagreement, change D) | 2 | Scripps at 9060 Activity Rd |
| admitted uncertainty (the model said so) | 0 | — |

v1 routed 10 rows to review, 5 of them by exploding one EMD entity into
singletons. **`manual_review` now means a contradiction or an admitted
uncertainty — never "we did not look".**

---

## v2 (flagged) — C.5 similarity thresholds

Two bounds in `dedup/candidates.py` are tighter than the change request stated. Both were
raised, never lowered, and each exists because of one counterexample in the stress batch.

| Constant | Value | Cite | The counterexample |
|---|---|---|---|
| `ACRONYM_MIN_LEN` | **3** | dedup/candidates.py | `HP Inc` vs `Hewlett Packard Enterprise Company` |
| `NAME_VARIANT_MAX_EXTRA_TOKENS` | **1** | dedup/candidates.py | `EMD Serono, Inc.` vs `EMD Serono Research and Development Institute, Inc.` |

**`ACRONYM_MIN_LEN = 3`.** The spec bounds an acronym only from above (`≤ 6` chars after
suffix strip). Unbounded below, `initials("Hewlett Packard Enterprise Company")` = `hpec`
scores JW 0.83 against `hp` — over the 0.8 threshold. HP Inc and Hewlett Packard Enterprise
are different companies at 1501 Page Mill Rd and are a MUST_NOT_MERGE pair; an `acronym`
evidence line there would have handed the model support for the merge the batch exists to
forbid. A two-letter string matches the initials of every two-word name beginning with those
letters, which is a coincidence rather than an initialism. Every real acronym in the batch is
three characters or longer: GES, USG, UCSF, NIST, UTWMC.

**`NAME_VARIANT_MAX_EXTRA_TOKENS = 1`.** The spec's containment arm is "one token set ⊂ the
other". Unbounded, `{emd, serono}` ⊂ `{emd, serono, research, and, development, institute}`
fires, telling the model that EMD Serono, Inc. and its own research institute are the same
institution — and with neither naming a department, the same-entity rule then merges a
company into its research arm. One extra token is a variant (`St Elizabeth Hospital` /
`St Elizabeth Community Hospital`, +1, which the rule must catch); four is a different
organisation. The Jaro-Winkler arm separates them independently — 0.907 for St Elizabeth,
0.844 for EMD — so the cap only removes cases the other arm already declined.

**C.5's `acronym` and `cross_slot` nomination rules are unit-tested only.** They are gated to
blocks with more than `SIG_PARTITION_THRESHOLD` (12) signatures, and no block in any stratum
run so far has exceeded that: the largest block in the 200-row stress batch holds 4
signatures. The rules are exercised by `tests/test_dedup_v2_name2.py` against constructed
units; no end-to-end run has yet fired either of them. The same two similarity functions DO
run end-to-end, ungated, as `evidence` lines in the prompt (C.3), which is where their
behaviour on real data is observable.

---

## Appendix — fastest diagnostic route for a wrong cluster

Given a `/api/dedup/file` output workbook:

1. **`Cluster ID` empty, `Routing = unique`, `Reasoning` empty** → the pair was *never
   nominated*. Look at `_eligible` / `nominate` (dedup/candidates.py:159, :130): either
   both units were already adjudicated in the same bucket, or JW < 0.85 **and** Jaccard <
   0.6 **and** no shared ROR/LEI. Nomination reads `name1` only.
2. **Two rows that should cluster sit in different `Block ID`s** (debug sheet) → the block
   key disagreed on one of `country`/`postal_code`/`street`/`house_no`
   (dedup/signatures.py:59). Nothing downstream can merge across blocks.
3. **Rows that should NOT cluster share a `Block ID`** → check whether the address fields
   were empty (all-empty → one shared block, §2.4) or whether the distinguishing detail
   was in a dropped column (`Building`, `Suite`, `Floor`, §1.4).
4. **Same `Block ID`, wrong merge, `Reasoning` starts `adjudicated vs …: merged`** → the
   residue pass did it (dedup/adjudicator.py:663). Check whether the pair crossed the
   Name 2 boundary; if so the deterministic rule was bypassed by design (§4.3).
5. **`Routing = manual_review` for a whole block** → one of: candidate cap
   (`Reasoning` begins `candidate_cap_exceeded:`), the contradiction guard
   (dedup/adjudicator.py:870), or an ROR/LEI split (`Reasoning` begins `Split: different
   non-empty …`).
6. **`LLM Flag = FALSE` on a large cluster** → expected: one signature, deterministic
   collapse, no merge decision (dedup/adjudicator.py:89-93). `Confidence` will be empty too.
7. **Cross-check `Signature ID` on the debug sheet always together with `Block ID`** — it
   restarts at `s1` in every block (§5.4).
8. **The workbook cannot tell you which model ran.** Model, model version, prompt version
   and every summary counter exist only in the JSON route's response and in the
   `dedup_block` / `dedup_request` logs (§5.2). If mock mode is suspected, look for the
   literal `Mock: conservative no-merge default.` in `Reasoning` (§4.8).
