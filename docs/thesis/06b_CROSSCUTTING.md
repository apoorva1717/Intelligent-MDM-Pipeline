Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 06b

# Pass 06b — Cross-cutting concerns

Six concerns that belong to no single stage: what the pipeline records about itself, what makes
a re-run reproduce a prior run, what it does when something fails, whether a merge can be run
twice, how a written value is attributed, and how a confidence is arrived at.

Scope is the v2 pass specification (`docs/thesis-doc-prompt-v2.md:132–135`). CI/CD, cost and
network security are **not** in this pass at v2; the deployment surface is in
`02_ARCHITECTURE.md` §2.6, external-service cost is in `06_EXTERNAL_DEPS.md` §7–§8.

## 6b.0 Method

Counts in this pass are produced by walking the Python AST of every tracked `.py` file outside
`tests/`, not by grep. The walk, and its verbatim output, are in Appendix A; every count below
is reproducible from it. "Service path" means `api/`, `enrichment/`, `dedup/`, `llm/`,
`search/`, `utils/`, `config.py` and `function_app.py` — the code that runs behind an HTTP
endpoint. `scripts/`, `tools/` and `eval/` are the harness and are counted separately.

    $ git status --porcelain
     M docs/thesis/00_INVENTORY.md
     M docs/thesis/01_TRACEABILITY.md
     M docs/thesis/02_ARCHITECTURE.md
     M docs/thesis/03_ALGORITHMS.md
     M docs/thesis/03b_EXEMPLARS.md
     M docs/thesis/04_PARAMETERS.md
     M docs/thesis/05_DATA_MODEL.md
     M docs/thesis/06_EXTERNAL_DEPS.md
     M docs/thesis/07_EVALUATION.md
     M docs/thesis/08_GAPS.md
     M docs/thesis/09_DECISIONS.md

Every modified path is a `docs/thesis/*.md` output of this documentation run; no source, SQL,
ADF or fixture file is modified, so the commit every citation addresses is intact. This pass
regenerates the file `08_GAPS.md` D-2 records as missing from the set.

---

## 6b.1 Logging and telemetry

### 6b.1.1 The apparatus

| Element | Value | Evidence |
|---|---|---|
| Configuration entry point | `configure_logging(log_level, log_file)` | `api/middleware.py:74` |
| Level | `LOG_LEVEL`, default `INFO` | `config.py:620`, declared `config.py:165` |
| Handlers | `StreamHandler` always; `RotatingFileHandler` when a log file resolves | `api/middleware.py:93`, `:102–106` |
| File path | argument → `LOG_FILE` env → `logs/enrichment_api.log`; `LOG_FILE=""` disables | `api/middleware.py:97–100`, documented `:80–83` |
| Rotation | 10 MB, 5 backups, UTF-8 | `api/middleware.py:104` |
| Format string | `"%(asctime)s %(levelname)s %(name)s [%(funcName)s] %(message)s"` | `api/middleware.py:87–91` |
| Root configuration | `logging.basicConfig(level=level, handlers=handlers, force=True)` | `api/middleware.py:117` |
| uvicorn loggers | handlers cleared, `propagate = True`, so access lines land in the same file | `api/middleware.py:122–125` |
| Quietened libraries | `httpx`, `httpcore`, `openai`, `urllib3` set to `WARNING` | `api/middleware.py:130–133` |
| Request correlation | `request_id = str(uuid.uuid4())[:8]`, set on `request.state` and returned as `X-Request-ID` | `api/middleware.py:22`, `:27`, `:58` |
| Request timing | `X-Duration-MS` response header | `api/middleware.py:59` |

`logs/` is excluded from the repository (`.gitignore:21`), so no log record is a committed
artefact — the constraint `08_GAPS.md` G-12 records.

### 6b.1.2 Three logging idioms, and what the formatter renders

The service uses three different call shapes. The count is over every `logger.<level>(…)` call
in the service path and the harness (Appendix A.2):

| Idiom | Shape | Calls | Where |
|---|---|---|---|
| (a) event name + `extra=` | `logger.info("request_complete", extra={…})` | 10 | `api/routes.py` ×4, `api/middleware.py` ×3, `dedup/adjudicator.py` ×3 |
| (b) dict as the message | `logger.info({"record_id": …, "step": …})` | 86 | `enrichment/orchestrator.py` ×68, `grounded_resolver.py` ×8, `name_gate.py` ×4, `flags.py` ×3, `address_processing.py`, `person_affiliation.py`, `provenance.py` ×1 each |
| (c) printf-style | `logger.info("…%s", value)` | 208 | everywhere else |

The formatter renders `%(message)s` and nothing else. A `LogRecord` attribute supplied through
`extra=` is attached to the record and is **not** in the format string, so **idiom (a) emits the
event name and discards every structured field it carries**. Idiom (b) survives, because the
dict *is* the message — rendered by `str()`, so it emits a Python dict repr with single quotes
and `None`, not JSON. Demonstrated by running the repository's own `configure_logging`
(Appendix A.3):

    2026-09-07 23:05:16,187 INFO demo [<module>] request_complete
    2026-09-07 23:05:16,187 INFO demo [<module>] {'record_id': '13162559', 'step': 'tier1_ror_miss', 'reason': 'no_candidate'}

The first line is the whole of what a `request_complete` record emits: `request_id`, `method`,
`path`, `status` and `duration_ms` (`api/middleware.py:62–69`) are all dropped. `api/middleware.py:1`
describes the middleware as "structured JSON logging"; neither half of that is true of what is
written — see §6b.7, G-93.

The ten idiom-(a) records are the ones that carry the request correlation id and the Phase 2
token counts. All ten lose their payload.

### 6b.1.3 The event vocabulary

Idiom (b) is the pipeline's real telemetry. Every one of the 86 records carries a `step` key;
85 of 86 carry `record_id`. There are **73 distinct `step` values** and 123 distinct keys
across the set. The most frequent keys (Appendix A.2):

| Key | Records carrying it |
|---|---|
| `step` | 86 |
| `record_id` | 85 |
| `field` | 22 |
| `registry` | 17 |
| `value` | 10 |
| `reason` | 9 |
| `query`, `name1`, `confidence`, `candidate` | 7 each |
| `supplied`, `lei_id`, `qid` | 6 each |
| `official_name`, `domain`, `ror_id` | 5 each |

The one record with no `record_id` is `enrichment/person_affiliation.py:180`, whose keys are
`step`, `contact`, `query`, `institution`, `department`, `confidence`. It is the only telemetry
record that writes a person's name to the log, and it is the only one that cannot be joined
back to the record it describes — see §6b.7, G-101.

Representative `step` values by stage, each cited at its emission site:

| Stage | `step` values | First emission site |
|---|---|---|
| Preprocessing / UC 0 | `preprocess`, `uc0_overflow_merged`, `uc0_repack_dropped` | `enrichment/orchestrator.py:7855`, `:7779`, `:2378` |
| Tier 1 ROR | `tier1_name1_cleaned`, `tier1_ror_parent`, `tier1_ror_miss`, `tier1_ror_match_demoted_different_entity` | `enrichment/orchestrator.py:8088`, `:8114`, `:8334`, `:8154` |
| Tier 1 GLEIF | `tier1_lei`, `tier1_lei_typo_recovered` | `enrichment/orchestrator.py:7660`, `:8485` |
| Tier 1 retry | `tier1_retry`, `tier1_retry_hit`, `tier1_retry_type_conflict` | `enrichment/orchestrator.py:5908`, `:6097`, `:5931` |
| Wikidata crosswalk | `wikidata_crosswalk_hit`, `wikidata_crosswalk_ror_miss`, `wikidata_crosswalk_lei_miss`, `wikidata_entity_superseded`, `wikidata_website_retained`, `wikidata_domain_check` | `enrichment/orchestrator.py:6315`, `:6266`, `:6360`, `:6192`, `:6506`, `:6551` |
| Name gate | `name_gate_different_entity`, `name_gate_country_conflict`, `name_gate_no_country_fuzzy_refused`, `name_gate_registry_verdict_deferred_no_geography` | `enrichment/name_gate.py:295`, `:220`, `:234`, `:285` |
| Grounded lane | `grounded_start`, `grounded_result`, `grounded_adopted`, `grounded_confirmed_input`, `grounded_degraded`, `grounded_guard_dropped`, `grounded_registry_hit`, `grounded_registry_country_mismatch`, `grounded_registry_same_entity_as_name1`, `grounded_fallthrough`, `grounded_proposal_refused` | `enrichment/orchestrator.py:9136`; `enrichment/grounded_resolver.py:795`, `:785`, `:763`, `:574`, `:683`, `:745`, `:500`, `:514`; `enrichment/orchestrator.py:7009`, `:5626` |
| Domain / page | `unverified_domain_refused_by_page`, `unverified_domain_shipped`, `domain_witness_revoked`, `domain_withdrawn_by_page_read`, `page_extract_feeds_retry`, `dept_domain_from_registry` | `enrichment/orchestrator.py:1852`, `:1980`, `:1913`, `:7512`, `:7392`, `:999` |
| Department block | `dept_block_normalised`, `dept_input_confirmed`, `dept_fallthrough`, `dept_registry_match_unanchored`, `dept_registry_match_region_contradicted`, `dept_unit_construction_restored` | `enrichment/orchestrator.py:2991`, `:5592`, `:6851`, `:3724`, `:3751`, `:3836` |
| Contact / Tier 2A | `tier_contact_decision`, `tier_contact_result`, `tier_contact_rejected_scope`, `tier_contact_canonicalised`, `tier_contact_canonical_rejected_scope` | `enrichment/orchestrator.py:9033`, `:9056`, `:9073`, `:9097`, `:9091` |
| Person affiliation | `person_affiliation`, `person_affiliation_confirmed`, `person_affiliation_unresolved` | `enrichment/person_affiliation.py:180`; `enrichment/orchestrator.py:5316`, `:5326` |
| Tier 3 | `tier3_start` | `enrichment/orchestrator.py:9168` |
| Address | `address_stage1` | `enrichment/address_processing.py:1274` |
| Flags | `flags_computed`, `flag_raised_late`, `flags_retracted` | `enrichment/flags.py:1432`, `:964`, `:1049` |
| Provenance | `inadmissible_value_reverted` | `enrichment/provenance.py:1250` |
| Failure | `orchestrator_error` | `enrichment/orchestrator.py:9264` |

### 6b.1.4 The batch summary

`EnrichmentSummary` (`api/models.py:697`) carries **80 counters** and ships in the `/enrich`
response body. It is the only aggregate telemetry that is not a log line, and the only place a
per-run call volume is recorded. Grouped by field-name prefix (Appendix A.4):

| Group | Counters | First field |
|---|---|---|
| `wikidata_*` | 16 | `wikidata_queried` `api/models.py:753` |
| `page_*` | 11 | `page_reads_attempted` `:729` |
| `liveness_*` | 6 | `liveness_checked` `:772` |
| `lei_*` | 5 | `lei_attempts` `:709` |
| `evidence_*` | 5 | `evidence_cache_frozen` `:806` |
| `domain_from_*` | 5 | `domain_from_registry` `:834` |
| `consensus_*` | 5 | `consensus_groups` `:858` |
| `tier1_retry_*` | 3 | `tier1_retry_attempts` `:717` |
| `unchanged_*` | 3 | `unchanged_verified` `:724` |
| `tier1_*` | 2 | `tier1_resolved` `:706` |
| `registry_*` | 2 | `registry_location_unconfirmed` `:822` |
| `tier2a_*` | 2 | `tier2a_population_count` `:848` |
| `contact_lookup_*` | 2 | `contact_lookup_attempted` `:852` |
| `tier2b_count`, `tier3_count`, `domain_rejected_unverified` | 1 each | `:850`, `:851`, `:847` |
| ungrouped | 10 | `total` `:699`, `enriched` `:700`, `verified` `:701`, `unresolved` `:702`, `failed` `:703`, `research_institution_count` `:704`, `company_count` `:705`, `routing_type_mismatch_count` `:795`, `cache_hits_after_normalisation` `:798`, `processing_time_ms` `:865` |

The five evidence-cache counters are the determinism instrumentation and are read by
`tools/run_diff.py` (§6b.2.3): `evidence_cache_frozen` (`:806`), `evidence_network_calls`
(`:807`), `evidence_network_calls_by_namespace` (`:810`), `evidence_frozen_misses` (`:813`),
`evidence_cache_hits` (`:814`).

`summary.tier2b_count` (`:850`) is structurally always zero — `08_GAPS.md` G-18.

### 6b.1.5 What is not recorded

| Absent | Evidence |
|---|---|
| Phase 1 token usage | `llm/openai_client.py:345` returns `response.choices[0].message.content` only; `response.usage` is discarded. Phase 2 captures it (`dedup/llm.py:244–245`). `08_GAPS.md` G-57 |
| Any correlation between the HTTP `request_id` and a per-record telemetry event | `request_id` lives on `request.state` (`api/middleware.py:27`) and appears in no idiom-(b) record; the 86 records key on `record_id` instead, and the one record that emits `request_id` (`api/middleware.py:62–69`) drops it at the formatter (§6b.1.2) |
| Any custom Application Insights telemetry | `host.json:3–10` enables platform App Insights logging with sampling on (`"isEnabled": true`, `"excludedTypes": "Request"`), so the host ships the stdout/stderr stream and its own request traces. No application code emits a custom metric or event — a case-insensitive search for `applicationinsights`, `opencensus`, `azure.monitor`, `opentelemetry` and `instrumentation_key` across the tree hits `host.json:4` and nothing else (command and output in Appendix A.4). `enrichment/provenance.py:36–38` states the policy — "nothing here writes to Application Insights — App Insights stays operational monitoring" |
| Which of the three `DEDUP_V2_*` flags were set on a run | not written to any output column (`08_GAPS.md` G-79) |

---

## 6b.2 Determinism

### 6b.2.1 What the repository claims, and where the claim is pinned

`tests/test_determinism.py:1–23` states the problem the machinery exists for: "Two runs of the
identical 101-row chemspeed batch, on the identical codebase, produced seven substantively
different records." Four properties are pinned, one class per fix, 90 tests, all passing at
this commit (Appendix A.5):

| Fix | Property | Enforced at |
|---|---|---|
| A | Every decision-gating LLM call is sent at `temperature=0.0`, `top_p=1.0`, `seed=42` | `llm/openai_client.py:102`, `:103`, `:108`; request built `:308–322` |
| B | Cache keys are pure functions of the request; entries are immutable and dated; `CACHE_FROZEN` turns a miss into a recorded unavailability | `utils/cache.py:1–60` |
| C | Candidate selection is a total order independent of API response order; a near-tie is a no-match | `dedup`/`enrichment` rank keys, catalogued in `03_ALGORITHMS.md` §3.18 |
| D | No record ships two contradictory identities; `Search Term 1` comes from the identity that survived | `enrichment/search_terms.py`, consistency checks |

### 6b.2.2 The evidence cache

One directory per source, one JSON file per key (`utils/cache.py:25–37`):

| Namespace | Directory | Key |
|---|---|---|
| `page` | `page_reads/` | registrable domain |
| `wikidata` | `wikidata/` | `search:<normalised query>` / `entity:<QID>` |
| `serp` | `serp/` | normalised query + quoted-flag + country |
| `fetch` | `fetch/` | the URL (or host) requested |
| `llm` | `llm/` | digest of deployment + sampling params + both prompts |
| `ror` | `registry/` | normalised name + country |
| `gleif` | `registry/` | normalised name + country |

The property that makes a second run hit is stated at `utils/cache.py:42–45`: "**No key contains
a run id, a batch id, a date or a record id** — that is the property that makes a second run hit
rather than miss, and `tests/test_determinism.py` asserts it structurally rather than by
inspection." The normaliser is `dedup.signatures.normalize_key`, reused rather than
reimplemented (`utils/cache.py:57–60`), and deliberately does not strip legal forms.

`CACHE_FROZEN` (`utils/cache.py:15–21`) turns a miss into a traced `evidence-unavailable-frozen`
rather than a network call, "the analogue of freezing `dedup/weights.json` before an
evaluation". Per-namespace instrumentation is at `utils/cache.py:428–432`
(`_entries`, `_memory_hits`, `_disk_hits`, `_recorded`, `_frozen_misses`) and the batch roll-up
at `api/models.py:806–814`.

Two limits, both already in the gap register:

- The switch covers Phase 1 only. `dedup/llm.py` participates in no namespace and has its own
  opt-in record/replay store (`dedup/cache.py:1–33`), gated on `DEDUP_FIXTURE_CACHE_DIR` and
  **off by default** — `08_GAPS.md` G-55.
- Four of the six namespaces a frozen replay needs are gitignored and absent —
  `08_GAPS.md` G-13.

`dedup/cache.py:19–24` defines two modes: `record` (serve a hit, else call and write) and
`replay` (serve a hit, else **refuse, loudly**) — "a miss means the prompt changed, and silently
calling the model would quietly re-measure something else while reporting it under the old run's
name". Errored calls are never cached (`:26–28`), and concurrent duplicate writes are left
unlocked deliberately (`:30–33`).

### 6b.2.3 `tools/run_diff.py` — the reproducibility gate

| Property | Value | Evidence |
|---|---|---|
| Inputs | two `scripts/run_batch.py --json` artefacts. The enriched XLSX is "deliberately NOT accepted — it carries only the *output* Name 1, and the join key has to be input-side" | `tools/run_diff.py:14–18` |
| Join key | `(name1_original, city)`, normalised for case and whitespace only | `tools/run_diff.py:75`, `:78–83` |
| Why not `Search Term 1` | it is pipeline-written, so "two runs that disagree about a record can fail to line that record up at all" | `tools/run_diff.py:24–32` |
| How the input name is recovered | `EnrichmentResult` marks `*_original` `exclude=True`, so the artefact carries a parallel `inputs` array joined by position within one run | `tools/run_diff.py:86–99` |
| Columns compared | every column of `api.output_columns.RESPONSE_COLUMNS` — nothing excluded, whitelisted or normalised away | `tools/run_diff.py:34–41`, `:246` |
| Folding applied | `None` ≡ `""`, and lists compared by contents. Nothing else: "a case change, a spacing change or a reordered list is a real difference and this tool exists to see it" | `tools/run_diff.py:124–138` |
| Excluded | `duration_ms` — not in the schema, "the one output that SHOULD differ" | `tools/run_diff.py:43–45` |
| Grammar guard | refuses to compare a Scheme A artefact against a Scheme B one, exit `2`, and names `tools/provenance_invariance.py` instead | `tools/run_diff.py:196–228`, `:340–366` |
| Warm-run evidence | prints `summary2["evidence_network_calls"]`, because "a second run that went to the network is not comparing the same evidence the first one saw" | `tools/run_diff.py:378–383` |
| Exit codes | `0` identical; `1` any differing row or any row present in one run only; `2` incomparable grammars | `tools/run_diff.py:386–393`, `:344`, `:366` |
| Encoding | stdout/stderr reconfigured to UTF-8 — "a diff that crashes on the character set of its own evidence is not a gate" | `tools/run_diff.py:65–72` |

The gate has no test of its own (`08_GAPS.md` G-71), and `tools/run_diff.py:203–205` states
"Seven of the sixty-seven columns are provenance" while `RESPONSE_COLUMNS` holds **69** at this
commit — see §6b.7, G-95.

### 6b.2.4 The two companion tools

| Tool | Purpose | Evidence |
|---|---|---|
| `tools/provenance_invariance.py` | compares behaviour across the Scheme A → Scheme B provenance migration by partitioning the columns instead of refusing | 248 LOC; named as the escape hatch at `tools/run_diff.py:363–365` |
| `tools/shuffle_evidence.py` | reorders recorded evidence to test that selection does not depend on the order a source answered in (Fix C) | 115 LOC |

### 6b.2.5 Residual non-determinism

Per-stage determinism is tabulated in `03_ALGORITHMS.md` §3.18 and is not repeated here. Three
cross-cutting residuals belong to this pass:

1. **The seed can be dropped silently, process-wide.** If the deployment rejects `seed`,
   `_SEED_SUPPORTED` is set `False` once "for the life of the process", a `WARNING` is logged and
   every later call goes out without it (`llm/openai_client.py:296–298`, `:324–344`). The
   warning's own text concedes the consequence: "Byte-identical re-runs are no longer guaranteed
   by the service." Nothing records the latch in the response, the summary or any output column,
   so a run that lost its seed is indistinguishable from one that kept it — see §6b.7, G-96.
   `dedup/llm.py:66`, `:265–270` carries the same latch for `seed` and for `temperature`.
2. **Phase 2 sampling is conditional.** `dedup/llm.py:223–237` sends `top_p` always, `seed` when
   supported, and `temperature` only when `reasoning_effort` is not in play — "gpt-5.4 rejects
   any temperature but its default" (`dedup/llm.py:9–14`).
3. **The cache is the determinism mechanism, and it is not in the repository.** G-13.

---

## 6b.3 Error handling — every fail-open `except`

### 6b.3.1 Method and counts

A handler is **fail-open** here when no `raise` statement appears anywhere inside its body: the
exception is absorbed and control continues. A handler that re-raises, or that converts to an
`HTTPException`, is not fail-open and is excluded. Derived by AST walk (Appendix A.6):

| Measure | Count |
|---|---|
| `except` handlers in production code (all tracked `.py` outside `tests/`) | 150 |
| …containing a `raise` anywhere in the handler | 20 |
| **…fail-open** | **130** |
| of those, in the service path | 115 |
| of those, in `scripts/` · `tools/` · `eval/` | 15 |
| fail-open handlers whose body is exactly `pass` | 11 |
| fail-open handlers that log anything at all | 64 |

**Sixty-six of the 130 fail-open handlers log nothing.** Of the 115 in the service path, **81
catch bare `Exception`**:

| Exception caught | Handlers (service path) |
|---|---|
| `Exception` | 81 |
| `(TypeError, ValueError)` | 7 |
| `ValueError` | 6 |
| `RegistryUnavailableFrozen` | 5 |
| `httpx.HTTPStatusError` | 4 |
| `WikidataUnavailable` | 3 |
| `ValidationError` | 2 |
| `(json.JSONDecodeError, ValueError)` | 2 |
| `ProvenanceGrammarError` | 2 |
| `OSError`, `RuntimeError`, `SearchUnavailable` | 1 each |

The `disposition` column in the tables below is the mechanical shape of the handler body, in
statement order: `pass`, `continue`, `log`, `assign`, `return <expr>`, `call`, `if`, `try`,
`loop`.

### 6b.3.2 The 115 fail-open handlers in the service path

| Site | Exception | Enclosing function | Disposition |
|---|---|---|---|
| `api/middleware.py:39` | `Exception` | `dispatch` | assign → log → return <expr> |
| `api/middleware.py:109` | `OSError` | `configure_logging` | log |
| `api/routes.py:315` | `ValidationError` | `_rows_to_records` | call |
| `api/routes.py:339` | `Exception` | `_copy_extra_sheets` | return None |
| `api/routes.py:1181` | `ValidationError` | `_rows_to_dedup_rows` | call |
| `api/routes.py:1356` | `Exception` | `dedup_cluster_block` | log |
| `api/routes.py:1401` | `Exception` | `dedup_file` | log |
| `api/routes.py:1598` | `Exception` | `diag_llm` | return <expr> |
| `dedup/adjudicator.py:130` | `(TypeError, ValueError)` | `_confidence_to_float` | return None |
| `dedup/adjudicator.py:1429` | `ValueError` | `pick` | log |
| `dedup/llm.py:42` | `Exception` | `<module>` | assign |
| `dedup/llm.py:78` | `(TypeError, ValueError)` | `_is_retryable` | return False |
| `dedup/llm.py:110` | `(json.JSONDecodeError, ValueError)` | `parse_json_object` | assign → assign → if |
| `dedup/llm.py:117` | `(json.JSONDecodeError, ValueError)` | `parse_json_object` | return None |
| `dedup/llm.py:186` | `Exception` | `aclose` | pass |
| `dedup/llm.py:250` | `Exception` | `adjudicate` | assign → if → if → if → if → log → break |
| `dedup/scoring.py:258` | `(TypeError, ValueError)` | `_floatify_confidence` | return None |
| `dedup/scoring.py:685` | `(TypeError, ValueError)` | `coerce_weights` | return <expr> |
| `dedup/scoring.py:755` | `ValueError` | `_coerce_int` | pass |
| `dedup/scoring.py:839` | `ValueError` | `_match_numeric_band` | log |
| `dedup/scoring.py:1130` | `ValueError` | `_resolve_confidence_threshold` | log |
| `dedup/scoring.py:1332` | `ValueError` | `_parses_as_int` | return False |
| `enrichment/address_processing.py:775` | `Exception` | `_classify_residual` | log → return (None, 0.0) |
| `enrichment/address_processing.py:782` | `(TypeError, ValueError)` | `_classify_residual` | assign |
| `enrichment/company_canonical.py:84` | `Exception` | `run_company_canonical` | log → return result |
| `enrichment/grounded_resolver.py:324` | `(TypeError, ValueError)` | `_index` | return None |
| `enrichment/grounded_resolver.py:389` | `Exception` | `_gather_evidence` | log → assign |
| `enrichment/grounded_resolver.py:474` | `Exception` | `_re_verify` | log → continue |
| `enrichment/grounded_resolver.py:599` | `Exception` | `run_grounded_resolver` | log → assign → assign → return result |
| `enrichment/issue_detection.py:1619` | `ProvenanceGrammarError` | `provenance_is_low` | return False |
| `enrichment/lab_resolver.py:115` | `Exception` | `run_lab_resolver` | log → continue |
| `enrichment/liveness.py:282` | `RegistryUnavailableFrozen` | `probe_ror_status` | log → return (None, None, 0.0) |
| `enrichment/liveness.py:285` | `Exception` | `probe_ror_status` | log → return (None, None, 0.0) |
| `enrichment/liveness.py:305` | `Exception` | `probe_ror_status` | continue |
| `enrichment/orchestrator.py:4440` | `Exception` | `enrich_batch` | log |
| `enrichment/orchestrator.py:4607` | `Exception` | `_resolve_final_url_cached` | assign |
| `enrichment/orchestrator.py:4783` | `Exception` | `_host_of` | return None |
| `enrichment/orchestrator.py:4851` | `Exception` | `_probe_department_url` | log → assign |
| `enrichment/orchestrator.py:4872` | `Exception` | `_probe_department_url` | assign |
| `enrichment/orchestrator.py:4906` | `Exception` | `_probe_department_url` | log → assign |
| `enrichment/orchestrator.py:4921` | `Exception` | `_probe_department_url` | assign |
| `enrichment/orchestrator.py:4962` | `Exception` | `_probe_department_url` | continue |
| `enrichment/orchestrator.py:5039` | `Exception` | `_probe_department_url` | log → assign |
| `enrichment/orchestrator.py:5115` | `Exception` | `_verify_candidate_url` | return False |
| `enrichment/orchestrator.py:5206` | `Exception` | `_resolve_person_affiliation` | log → assign |
| `enrichment/orchestrator.py:5293` | `Exception` | `_resolve_person_affiliation` | log |
| `enrichment/orchestrator.py:5760` | `Exception` | `_site_qualifier_retry` | log → assign |
| `enrichment/orchestrator.py:5950` | `Exception` | `_retry_tier1_after_canonicalisation` | log → assign |
| `enrichment/orchestrator.py:6045` | `Exception` | `_retry_tier1_after_canonicalisation` | assign → assign → log → return None |
| `enrichment/orchestrator.py:6156` | `Exception` | `_wikidata_crosswalk` | assign → log → return False |
| `enrichment/orchestrator.py:6258` | `Exception` | `_crosswalk_to_ror` | log → return False |
| `enrichment/orchestrator.py:6352` | `Exception` | `_crosswalk_to_gleif` | log → return False |
| `enrichment/orchestrator.py:6493` | `Exception` | `_retain_wikidata_website` | assign → log → return None |
| `enrichment/orchestrator.py:6589` | `Exception` | `_check_liveness` | log |
| `enrichment/orchestrator.py:6884` | `Exception` | `_dept_fallthrough` | log → continue |
| `enrichment/orchestrator.py:7041` | `Exception` | `_grounded_fallthrough` | log → return None |
| `enrichment/orchestrator.py:7551` | `ProvenanceGrammarError` | `_emit_retry_trace` | assign |
| `enrichment/orchestrator.py:7613` | `Exception` | `_run_address_stage` | log → return None |
| `enrichment/orchestrator.py:7652` | `Exception` | `_run_lei_lookup` | assign → log → return False |
| `enrichment/orchestrator.py:9263` | `Exception` | `_enrich_single` | log → assign → assign → return <expr> |
| `enrichment/overflow_check.py:127` | `Exception` | `run_overflow_check` | log → return result |
| `enrichment/page_corroborator.py:364` | `Exception` | `read_page` | log → return None |
| `enrichment/person_affiliation.py:134` | `Exception` | `run_person_affiliation` | log → continue |
| `enrichment/person_affiliation.py:166` | `Exception` | `run_person_affiliation` | log → return PersonAffiliation() |
| `enrichment/preprocess.py:1783` | `Exception` | `_extract_contact_from_field` | log → assign |
| `enrichment/preprocess.py:3349` | `Exception` | `llm_classify_plain_names_async` | log → continue |
| `enrichment/search_terms.py:320` | `Exception` | `unit_domain_or_path` | return None |
| `enrichment/tier1_lei.py:688` | `RegistryUnavailableFrozen` | `call_lei` | log → return <expr> |
| `enrichment/tier1_lei.py:695` | `httpx.HTTPStatusError` | `call_lei` | log → return <expr> |
| `enrichment/tier1_lei.py:701` | `Exception` | `call_lei` | log → return <expr> |
| `enrichment/tier1_lei.py:767` | `Exception` | `_fuzzy_lookup` | log → continue |
| `enrichment/tier1_lei.py:856` | `RegistryUnavailableFrozen` | `call_lei_by_id` | log → return <expr> |
| `enrichment/tier1_lei.py:860` | `httpx.HTTPStatusError` | `call_lei_by_id` | assign → if → log → return <expr> |
| `enrichment/tier1_lei.py:871` | `Exception` | `call_lei_by_id` | log → return <expr> |
| `enrichment/tier1_ror.py:1593` | `RegistryUnavailableFrozen` | `call_ror` | log → return <expr> |
| `enrichment/tier1_ror.py:1599` | `httpx.HTTPStatusError` | `call_ror` | log → return _no_match() |
| `enrichment/tier1_ror.py:1605` | `Exception` | `call_ror` | log → return _no_match() |
| `enrichment/tier1_ror.py:1690` | `RegistryUnavailableFrozen` | `call_ror_by_id` | log → return <expr> |
| `enrichment/tier1_ror.py:1693` | `httpx.HTTPStatusError` | `call_ror_by_id` | log → return <expr> |
| `enrichment/tier1_ror.py:1698` | `Exception` | `call_ror_by_id` | log → return <expr> |
| `enrichment/tier2_canonical.py:201` | `Exception` | `run_tier2_canonical` | log → return result |
| `enrichment/tier2a_contact.py:151` | `Exception` | `run_tier2a` | log → continue |
| `enrichment/tier2b_dept.py:95` | `Exception` | `run_tier2b` | log → continue |
| `enrichment/tier2b_dept.py:145` | `(TypeError, ValueError)` | `run_tier2b` | assign |
| `enrichment/tier3_llm.py:121` | `Exception` | `run_tier3` | log → assign → assign → return result |
| `enrichment/website_resolver.py:563` | `Exception` | `_root_url` | pass |
| `enrichment/website_resolver.py:651` | `Exception` | `_assemble_path_b_trace` | assign |
| `enrichment/website_resolver.py:948` | `Exception` | `_run` | log → if → return WebsiteResolution() |
| `enrichment/website_resolver.py:1002` | `Exception` | `_looks_like_url` | return False |
| `enrichment/website_resolver.py:1065` | `Exception` | `infer_website_via_llm` | log → call → return WebsiteResolution() |
| `enrichment/wikidata.py:674` | `Exception` | `_backoff` | assign |
| `enrichment/wikidata.py:679` | `ValueError` | `_backoff` | pass |
| `enrichment/wikidata.py:899` | `WikidataUnavailable` | `resolve` | assign → return _finish(outcome) |
| `enrichment/wikidata.py:908` | `WikidataUnavailable` | `resolve` | assign → return _finish(outcome) |
| `enrichment/wikidata.py:963` | `WikidataUnavailable` | `resolve` | assign → return _finish(outcome) |
| `llm/openai_client.py:53` | `RuntimeError` | `install_httpx_aclose_noise_filter` | return None |
| `llm/openai_client.py:355` | `Exception` | `call_openai` | pass |
| `llm/openai_client.py:401` | `Exception` | `aclose` | pass |
| `llm/test_connection.py:26` | `Exception` | `test` | log |
| `search/page_fetcher.py:205` | `Exception` | `_live` | call → assign |
| `search/page_fetcher.py:242` | `Exception` | `_sync_fetch_result` | call → return <expr> |
| `search/page_fetcher.py:273` | `Exception` | `_live` | call → assign |
| `search/page_fetcher.py:298` | `Exception` | `_live` | assign |
| `search/page_fetcher.py:318` | `Exception` | `_live` | assign |
| `search/page_fetcher.py:346` | `Exception` | `_sync_resolve_final_url` | return None |
| `search/page_fetcher.py:358` | `Exception` | `_sync_subdomain_exists` | return False |
| `search/page_fetcher.py:384` | `Exception` | `_live` | call → assign |
| `search/page_fetcher.py:420` | `Exception` | `_sync_fetch_outgoing_links` | continue |
| `utils/cache.py:334` | `Exception` | `get_entry` | call → return None |
| `utils/cache.py:345` | `Exception` | `get_entry` | assign |
| `utils/cache.py:422` | `Exception` | `set` | pass |
| `utils/cache.py:701` | `Exception` | `_serp_geo_enabled` | return True |
| `utils/cache.py:753` | `SearchUnavailable` | `cached_serp` | return [] |
| `utils/domain_resolver.py:137` | `Exception` | `canonicalise_host` | return None |
| `utils/text_utils.py:52` | `Exception` | `extract_domain` | return None |
### 6b.3.3 The 15 fail-open handlers in the harness

Not on any request path. Listed for completeness; a swallowed failure here mis-reports a
measurement rather than shipping a wrong value.

| Site | Exception | Enclosing function | Disposition |
|---|---|---|---|
| `eval/dedup_eval.py:93` | `ValueError` | `_as_float` | return None |
| `scripts/fix_reports.py:75` | `ProvenanceGrammarError` | `_state` | return None |
| `scripts/test_local.py:66` | `requests.ConnectionError` | `wait_for_health` | pass |
| `scripts/test_local.py:92` | `requests.RequestException` | `run_fixture` | return <expr> |
| `scripts/trace_website.py:74` | `Exception` | `emit` | pass |
| `scripts/trace_website.py:192` | `Exception` | `_main` | pass |
| `scripts/verify_fixes.py:44` | `Exception` | `run` | log → assign |
| `scripts/verify_fixes.py:89` | `Exception` | `run` | log → assign |
| `scripts/verify_fixes.py:117` | `Exception` | `run` | log → assign |
| `scripts/verify_fixes.py:138` | `Exception` | `run` | log → assign |
| `scripts/verify_fixes.py:167` | `Exception` | `run` | log → assign |
| `scripts/verify_fixes.py:223` | `Exception` | `run` | log → log → assign |
| `scripts/wikidata_lane_report.py:106` | `ValueError` | `_traces` | continue |
| `scripts/wikidata_warm_fixtures.py:79` | `Exception` | `_main` | log → continue |
| `tools/run_diff.py:71` | `Exception` | `<module>` | pass |
### 6b.3.4 What the pattern means

Of the 115 service-path fail-open handlers, **34 carry a `# noqa: BLE001` marker on the `except`
line and 35 carry any inline comment at all**; the remaining 80 catch and continue with no note
on the line (Appendix A.6). The commented ones read as a deliberate policy with a stated reason
— `utils/cache.py:334` "a corrupt fixture is a miss", `utils/cache.py:422` "a fixture we cannot
write is not fatal", `api/routes.py:1356` and `enrichment/orchestrator.py:4440` releasing an
HTTP client on the way out. The uncommented ones are not distinguishable, from the line alone,
from an oversight.

Four handlers set the shape of the whole pipeline's failure behaviour:

| Site | What it absorbs | What ships instead |
|---|---|---|
| `enrichment/orchestrator.py:9263` | any exception from any stage of one record's enrichment | the record is not dropped and the batch is not failed: `enrichment_status = "failed"`, `error = str(exc)`, and the record still goes through `_finalise_and_return` (`:9264–9273`). It is counted in `EnrichmentSummary.failed` by the `else` branch at `api/models.py`-side aggregation (`enrichment/orchestrator.py:9290`). This is the deliberate per-record bulkhead, and it is the reason a single bad record cannot take down a 100-row batch |
| `enrichment/provenance.py` admissibility gate | not an `except` — the same policy expressed as a check: a scoped field with no provenance event has its value **reverted to the input** and the record is flagged, rather than the batch being failed (`enrichment/provenance.py:1227–1234`) | "shipping the original input is strictly better than failing the batch, and strictly better than shipping an unattributable value" |
| `enrichment/issue_detection.py:1619` | a `ProvenanceGrammarError` on a cell that is not a provenance string | `provenance_is_low` returns `False` — the audit "reports what it can read and never guesses" (`:1610–1612`). A malformed provenance cell therefore raises no issue code |
| `utils/cache.py:753` | `SearchUnavailable` from a SERP call | `return []` — an empty candidate list, indistinguishable downstream from a search that legitimately found nothing |

The counterweight is that failure is not silent at the record level: `orchestrator_error`
(`enrichment/orchestrator.py:9264`) is one of the 73 telemetry steps, and `enrichment_status`
and `error` are both output columns. Failure **inside** a stage is a different matter — a
swallowed registry call, page fetch or LLM call leaves the record looking merely unresolved,
and the 66 fail-open handlers that log nothing leave no record that anything was attempted.
The tier-route question this raises is `08_GAPS.md` G-12.

---

## 6b.4 Idempotency of the merge procedures

All four procedures are `UPDATE`-only. There is no `INSERT`, no `DELETE`, no `WHEN NOT MATCHED`
clause of either kind, no `OUTPUT` clause and no explicit transaction anywhere in `sql/`
(Appendix A.7). Nothing accumulates: no column is written as `col = col + …`, and every
assignment is a total function of the payload and the incumbent.

**Result: all four are idempotent under repetition of an identical payload.** Running the same
merge twice writes the same values the second time as the first.

| Procedure | Assignment form | Idempotent under repeat | Convergent (target made equal to payload) |
|---|---|---|---|
| `usp_MergeLegacyEnriched` | 31 assignments: 26 as `COALESCE(NULLIF(LTRIM(RTRIM(src.[c])), SPACE(0)), tgt.[c])`, and 5 unconditional — `Record Type`, `ROR ID`, `LEI ID` (`LTRIM(RTRIM(src.[c]))`) plus `Flag for Review` and `Flag Reason` assigned directly (`sql/usp_merge_legacy_enriched.sql:86`; counts in Appendix A.7) | yes | no |
| `usp_MergeLegacyIssues` | one column, `tgt.<@target_column> = src.[issues_csv]` where `issues_csv` is `STRING_AGG(x.[value], N'; ') WITHIN GROUP (ORDER BY CAST(x.[key] AS INT))` (`sql/usp_merge_legacy_issues.sql:52–61`, `:66`) | yes — the aggregate is ordered, so the joined string is stable | no |
| `usp_MergeValidationClusters` | six columns assigned directly from `#src` (`sql/usp_merge_validation_clusters.sql:63`) | yes | no |
| `usp_MergeValidationScores` | 21 columns assigned directly from `#src` (`sql/usp_merge_validation_scores.sql:78`) | yes | no |

Five properties qualify that result.

**1. Unmatched payload rows vanish.** The join is `ON tgt.Customer = src.<id> AND tgt.[code] LIKE
@pat ESCAPE @esc` and there is no `WHEN NOT MATCHED BY TARGET`, so a payload row whose customer
number is not in the group code's slice is dropped with no error and no count
(`sql/usp_merge_legacy_enriched.sql:86`, `usp_merge_legacy_issues.sql:66`,
`usp_merge_validation_clusters.sql:63`, `usp_merge_validation_scores.sql:78`). `08_GAPS.md`
G-86 is the special case where the id is empty.

**2. Stale target rows are never cleared.** There is no `WHEN NOT MATCHED BY SOURCE`, so a row
in the group that the payload does not mention keeps whatever a previous run wrote. The
procedures are idempotent but not convergent: after two runs the target holds the union of both,
per column, not the second payload.

**3. `usp_MergeValidationScores` overwrites a recorded approval.** `tgt.[approval_status] =
src.approval_status` is unconditional (`sql/usp_merge_validation_scores.sql:78`), and
`elect_golden_records` sets `approval_status` to exactly `"proposed"` for every member of a
`proposed`/`manual_review` cluster and `None` for a `unique` row (`dedup/scoring.py:1308`,
`:1322`). The only value that can carry `"approved"` or `"rejected"` comes from
`apply_approval` (`dedup/scoring.py:628`), reached through `POST /api/dedup/approve`, which is
stateless and which no pipeline calls (`08_GAPS.md` G-29). **Any re-run of the scoring pipeline
therefore resets `Validation.[approval_status]` to `proposed` or `NULL`, discarding a steward
decision recorded in that column.** See §6b.7, G-97.

**4. The guards are outside any transaction, and so is the merge.** Each procedure runs three or
four guards, then `SELECT … INTO #src`, then one `EXEC sp_executesql` (`… clusters.sql:14–41`,
`:46–58`, `:63–64`). The `MERGE` is a single statement and is therefore atomic on its own, but
nothing wraps guard + parse + merge, there is no `TRY`/`CATCH`, and every ADF activity has
`retry: 0` (`08_GAPS.md` G-72). The enrichment merge runs **inside** the pipeline's `ForEach`
(`adf/enrichment_pipeline.json:136`), so a mid-run failure leaves earlier pages committed and
later pages not, with no compensating action.

**5. `#src` is re-entrant.** Each procedure opens with `IF OBJECT_ID(N'tempdb..#src') IS NOT NULL
DROP TABLE #src;` and closes with `DROP TABLE #src;` (`…clusters.sql:46`, `:65`). A failure
between the two skips the closing drop, and the opening drop makes the next call safe.

The guards themselves are idempotent and are the same four in every procedure, raising with
`THROW` rather than `RAISERROR`: `50000` target column not permitted (issues only,
`usp_merge_legacy_issues.sql:16`), `50001` entity schema absent (`…clusters.sql:20`), `50002`
group code missing (`:28`), `50003` group code has no rows in the entity (`:40`).

---

## 6b.5 Provenance, origin, and the origin invariant

Two distinct mechanisms share the word "origin" and must not be conflated:

- **Provenance** — *which source produced this value, and under what warrant.* Six write-locked
  fields, an event log, seven shipped columns. `enrichment/provenance.py`, `enrichment/confidence.py`.
- **`_slot_origin`** — *how did this value get into this slot.* Seven values, name-block slots
  only, working state that never ships. `enrichment/dept_block.py:81–105`.

The **origin invariant** governs the second.

### 6b.5.1 The write lock

`enrichment/provenance.py:1–39` states the principle: "Every value the system writes must be
attributable after the fact to the source that produced it and the confidence under which it was
produced. A written value whose origin cannot be reconstructed is not admissible."

The enforcement is structural rather than conventional. Six fields are **write-locked**:

    SCOPED_FIELDS = ("name1_enriched", "name2_enriched", "domain",
                     "record_type", "ror_id", "lei_id")
                                            — enrichment/provenance.py:70-77

`EnrichedRecord.__setitem__` raises `UnattributedWriteError` on any direct assignment to one
(`enrichment/provenance.py:1159–1166`), and `setdefault` is refused whether or not the key is
present, "because `setdefault` states an intent to write, and a reader of the call site cannot
tell which branch it will take" (`:1168–1173`). The only route in is
`EnrichedRecord.write(field, value, evidence)`, which requires a structured `Evidence`
(`enrichment/provenance.py:283`). `api.models.EnrichmentResult` carries the same guard for the
post-finalisation stage — `__setattr__` raising `UnattributedWriteError` at `api/models.py:656–658`, with `write` recording the event at `:676–681`, which is what covers batch consensus.

Scope is six fields and not more for a stated reason: they are "the fields where a wrong value
causes a wrong merge in Phase 2, and they carry no personal data, which keeps the provenance
store clear of a data-protection question. `contact`, `care_of` and `email` are deliberately
excluded for that reason" (`enrichment/provenance.py:19–24`).

### 6b.5.2 The admissibility gate

`enforce_admissibility` (`enrichment/provenance.py:1227`) checks that every non-null scoped
field carries at least one provenance event. A field that does not is **reverted to its input
value** — `INPUT_VALUE_KEYS` (`:93–100`) for `name1`/`name2`, `INPUT_VALUE_DEFAULTS` (`:102–107`)
for the other four — the record is flagged, and `inadmissible_value_reverted` is logged with the
dropped and restored values (`:1250–1256`). The record is not failed: "shipping the original
input is strictly better than failing the batch, and strictly better than shipping an
unattributable value" (`:1230–1233`). `assert_admissible` (`:1261`) is the same condition as a
hard assertion for tests.

### 6b.5.3 What ships

| Artefact | Provenance content | Evidence |
|---|---|---|
| `/enrich` JSON response | the full event log `provenance`, the guard-refused candidates `provenance_rejected`, and the per-field over-cap counts `provenance_rejected_omitted` | `api/models.py:550`, `:556`, `:557` |
| The enriched XLSX / `RESPONSE_COLUMNS` | the **seven derived scalars only** | `api/output_columns.py:116–121`, `:46` |
| `dp_legacy.<entity>.Legacy` via `usp_MergeLegacyEnriched` | **nothing** — `grep -ci provenance sql/usp_merge_legacy_enriched.sql` returns `0`, and no `sql/*.sql` file mentions provenance at all (Appendix A.8) | — |

`api/models.py:546–549` states the boundary deliberately: the event log is "NOT a file column …
It is part of the API response, not telemetry: Application Insights stays operational
monitoring, and ADF decides what, if anything, to store." What ADF in fact stores is nothing:
the whole attribution apparatus dies at the write-back boundary, alongside `Flag Codes`
(`08_GAPS.md` G-04), the scoring diagnostics (G-30) and `link_id` (G-32). See §6b.7, G-99.

The seven columns are `PROVENANCE_COLUMNS` (`enrichment/orchestrator.py:2051–2054`) — the six
`DERIVED_SCALAR_FIELDS` (`enrichment/provenance.py:110–117`) plus `operating_name_provenance`.
They are regenerated from the event log on every write by `_scoped_scalars`
(`enrichment/orchestrator.py:2057`), never assigned.

**The grammar assertion.** At finalisation, every provenance string the record will ship is
validated and an invalid one **raises**:

    validate_provenance_strings(
        result.get(column) for column in PROVENANCE_COLUMNS
    )                              — enrichment/orchestrator.py:3278-3280

The reason is at `:3270–3274`: "An invalid string is raised, not logged: a provenance column
that does not parse is worse than an empty one, because a consumer reads it as an attribution."
`operating_name` is in scope even though it is not write-locked, "the grammar is a property of
the COLUMN, not of the write path" (`:3275–3277`).

### 6b.5.4 The origin invariant

`_slot_origin` is a per-slot map over the name block, holding one of seven values
(`enrichment/dept_block.py:81–95`):

| Origin | Meaning |
|---|---|
| `input` | the record stated it, in this slot, and nothing has rewritten it |
| `preprocess:split` | UC 16 lifted it out of Name 1 |
| `preprocess:street` | the street→name router lifted it out of a street slot |
| `preprocess:moved` | preprocessing moved it within the name block (UC 14 leftward pack, or any slot shift) |
| `registry` | ROR or GLEIF spelled it |
| `llm` | a model produced it (Tier 2 canonicalisation, Tier 3 suggestion) |
| `grounded` | the grounded resolver produced it |

Two derived sets: `RESOLVED_ORIGINS = {registry, llm, grounded}` — "an AUTHORITY has already
answered for this slot" (`enrichment/dept_block.py:96–99`) — and `_RELOCATED_ORIGINS =
{preprocess:street, preprocess:split, preprocess:moved}` (`enrichment/orchestrator.py:1654–1657`).
`ORIGINS` (`dept_block.py:102–107`) exists "to reject a typo'd origin at the door rather than let
it silently read as 'not resolved'".

The origin is recorded in one place, `_write` (`enrichment/orchestrator.py:1711`), rather than at
each of the department block's 27 write sites, and the reason is stated as a completeness
argument: "Recording the origin HERE makes completeness structural: a new lane that writes a
department slot records its origin by construction, and one that bypasses the funnel is the
single exception, called out where it happens" (`:1702–1709`).

**The invariant itself**, verbatim:

    # THE ORIGIN INVARIANT: an origin may change only when the VALUE
    # changes.
                                       — enrichment/orchestrator.py:1731-1732

and the guard that enforces it:

    if origin is not None and not _same_value_folded(value, incumbent):
        result.setdefault("_slot_origin", {})[
            field[: -len("_enriched")]
        ] = origin
                                       — enrichment/orchestrator.py:1750-1753

The argument for it, at `:1733–1750`: `_origin_for` answers "who performed this write", and for a
write that changes nothing that is a different question from where the value came from — "a
passthrough that declares `producer="input"` was overwriting the record of where the value came
from with a claim about who last touched it". The measured effect is quoted in the code:
"seven records lost `relocated-unverified` this way, five of them dropping out of review
entirely, with their Name 2 byte-identical on both sides (13333471, 13335858, 13140896,
13333600, 13335245, 13335676, 13340639). The doubt was not answered — the fact it was derived
from was destroyed."

The equality used is `_same_value_folded` (`enrichment/orchestrator.py:1756`): whitespace runs
and case only, and **deliberately not** `normalize_key`, "which folds legal forms and would read
'Delta Analytical Inc' and 'Delta Analytical LLC' as one value". Two values differing by a
period or comma are different values here and rightly re-attribute.

A transform is exempt by construction: `_origin_for` returns `None` for
`evidence.kind == "transform"` (`enrichment/orchestrator.py:1244–1245`), because "casing,
abbreviation expansion and the packing rules reshape or relocate a value, they do not produce
one, and the origin follows the VALUE" (`:1238–1243`).

**What the invariant is for.** `_stated_name` (`enrichment/orchestrator.py:1659`, the test at `:1704`) uses
`_RELOCATED_ORIGINS` to decide what the record *states* for a slot, so that the name gate judges
a candidate against the value the slot holds rather than a value preprocessing removed. The
worked case is in its docstring at `:1687–1697`: record `13336873` supplied "ALLEGIANCE HEALTH"
in both Name 1 and Name 2; UC 12 deleted the Name 2 duplicate; the street router refilled the
slot from Street 2; and without the origin the gate judged the grounded lane's proposal against
the deleted duplicate and returned `different_entity` — "right about the two strings and wrong
about the question".

**`_slot_origin` never ships.** It is popped before pydantic validation
(`enrichment/orchestrator.py:3287`), along with the other transient keys. So the invariant
governs a field that appears in no response, no workbook and no table, and its effect is
observable only through the values and flags it changes. See §6b.7, G-100.

---

## 6b.6 Confidence fields, and how they are computed

Three different quantities are called confidence in this repository, on three different scales,
computed in three different places. They are not comparable and the code says so.

### 6b.6.1 Phase 1 — the shipped provenance confidence

**The grammar.** `enrichment/confidence.py:33–37`, verbatim:

    provenance := source ":" confidence ( "+" witness )?
    source     := "input" | "ror" | "gleif" | "wikidata" | "web:" domain | "llm"
    confidence := "verified" | "provisional" | "low"
    witness    := "web" | "wikidata" | "llm" | "registry" | "domain" | "dba"

compiled as one anchored expression at `enrichment/confidence.py:113–119`. The confidence
vocabulary is exactly three tokens (`CONFIDENCES`, `:56`), the witness vocabulary exactly six
(`WITNESSES`, `:97–100`).

**The one decision.** `compute_confidence(evidence) -> (confidence, witness | None)`
(`enrichment/confidence.py:187`) is the sole authority: "Every lane's provenance passes through
here and nothing else assigns a confidence, which is what makes the column mean the same thing
in every row of it" (`:190–193`). Its input is an `EvidenceSituation`
(`enrichment/confidence.py:133`), a frozen dataclass of eight booleans and one optional witness
that "deliberately contains no lane names, no tiers and no scores" (`:143–146`).

The table, in the precedence the code applies (`enrichment/confidence.py:228–245`):

| Order | Situation | Confidence | Witness |
|---|---|---|---|
| 1 | `contradicted` **or** `ambiguous` | `low` | never |
| 2 | `registry_authored` | `verified` | `+wikidata` iff `via_wikidata_crosswalk`, else none |
| 3 | a witness that is not in `NON_CORROBORATING_WITNESSES` | `verified` | that witness, required |
| 4 | `has_source` **and** `canonical_proposal_equals_input` | `provisional` | `+llm` |
| 5 | `has_source` | `provisional` | none |
| 6 | otherwise (no source) | `low` | never |

Contradiction and ambiguity are checked **first**, before the registry row, and the reason is
stated at `:220–224`: "A registry hit that a consistency check refused is not a verified value
that happens to be flagged — it is a value the pipeline decided against".

**Two hard rules, enforced in the function and re-checked on the way out** (`validate`,
`enrichment/confidence.py:290`):

1. `llm` as source or witness can never produce or contribute to `verified`
   (`NON_CORROBORATING_WITNESSES = {llm}`, `:104`; raised at `:300–310`). This "is the rule the
   old scheme's `self_high` band quietly broke" (`:210–212`).
2. A witness-less `verified` is legal only for `REGISTRY_SOURCES = {ror, gleif, wikidata}`
   (`:73–76`; raised at `:313–318`).

Hard rule 3 — rejected evidence never appears in provenance — "is not checkable from the string
alone … it is enforced at the adapter and asserted by the per-state fixtures"
(`enrichment/confidence.py:294–296`).

`render` validates on the way out rather than only at finalisation, "so an invalid combination
fails at the site that built it, where the stack trace still names the lane"
(`enrichment/confidence.py:260–264`). `parse` (`:274`) exists because `web:acme.com:provisional`
contains two colons and "the naive split puts the domain in the confidence slot".

### 6b.6.2 Phase 1 — the nine confidence *scales* on the event

The shipped column carries a three-token confidence. The **event** carries the raw number and
the scale it is on, because "0.85 from a ROR rescore, 0.85 from a RapidFuzz ratio and 0.85 from
a model's assertion about its own output mean three different things, and thresholding them with
one number is not sound" (`enrichment/provenance.py:121–127`).

| Scale | Constant | Range / meaning | Evidence |
|---|---|---|---|
| `ror_local` | `ROR_LOCAL` | ROR's local rescore, `token_sort_ratio`, normalised 0.0–1.0 | `enrichment/provenance.py:131` |
| `fuzzy_ratio` | `FUZZY_RATIO` | RapidFuzz similarity 0–100 | `:134` |
| `llm_self_reported` | `LLM_SELF_REPORTED` | "a model's assertion about its own output. Not a probability of anything … must never be read as a measurement" | `:135–138` |
| `deterministic` | `DETERMINISTIC` | 1.0 by construction: "this rule matched", not "100% likely" | `:139–142` |
| `registry_exact` | `REGISTRY_EXACT` | "not scored, they are returned" | `:143–145` |
| `inherited` | `INHERITED` | copied from a batch donor; "only as good as the donor's scale" | `:146–149` |
| `input_corroborated` | `INPUT_CORROBORATED` | input kept **and** independently corroborated; 1.0 by construction | `:150–156` |
| `input_self_consistent` | `INPUT_SELF_CONSISTENT` | input kept **and** an independent canonicalisation proposal reproduced it under `normalize_key` | `:157–163` |
| `none` | `NO_SCALE` | no confidence attaches to this write | `:164–165` |

`comparable(a, b)` is "the whole of Step 3 in one function" and returns true only when the two
scales are equal (`enrichment/provenance.py:188–197`): "A caller that needs to rank across
scales has to rank the KIND of evidence, not the floats." The self-report rendering
`{"high": 0.9, "medium": 0.7, "low": 0.4, "none": 0.0}` (`:176–179`) is "Documented, fixed, and
NOT a calibration" — it exists so the event carries a sortable number beside the label, which is
preserved verbatim in `evidence_ref["self_reported"]` (`:173–175`).

`confidence_band(scale, value)` (`enrichment/provenance.py:214`) renders one `(scale, value)`
pair into a scale-namespaced band. It is **off the export path**: "Nothing in the pipeline calls
it, and nothing should call it to decide anything" (`:210–212`). It was the third component of
the old derived scalar, and was removed because "a slot holding `self_high` for one producer and
`exact` for another is not a confidence, it is three vocabularies sharing a column"
(`:201–205`). It is retained for diagnostics and for the tests that pin its thresholds.

### 6b.6.3 Phase 2 — merge confidence

Phase 2 uses the opposite convention, and this is the sharpest cross-cutting contrast in the
system.

| Property | Value | Evidence |
|---|---|---|
| Source | the adjudicating model's self-reported merge confidence | `dedup/adjudicator.py:233`, `:527`, `:673`, `:708`, `:943` |
| Coercion | `float(value)`, clamped to `[0.0, 1.0]`, `None` on failure | `dedup/adjudicator.py:124–136` |
| When surfaced | only for a genuine merge (≥2 signatures) or an uncertain row — "never for a pure identical-collapse or a distinct verdict, where a spurious confidence would wrongly trip the election confidence gate" | `dedup/adjudicator.py:1249–1260` |
| Cluster roll-up | the **lowest** non-`None` member confidence; all-`None` returns `None` and never gates | `dedup/scoring.py:1138–1148` |
| Threshold | `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95`, overridable by `CONFIDENCE_MERGE_THRESHOLD` | `dedup/scoring.py:50`, resolved `:1122–1135` |

So Phase 1 forbids a model's self-report from carrying a value to `verified` (hard rule 1) and
refuses to compare it against any other scale (`comparable`), while Phase 2 takes the same kind
of number, clamps it, takes a per-cluster minimum and thresholds it at `0.95` to gate a merge.
Both are defensible in their own terms — Phase 2's is a conservative gate on a decision a human
still reviews — but the word `confidence` names two incommensurable quantities across the two
phases, and nothing in the code maps one onto the other. This is the same shape of problem as
the two issue vocabularies (`08_GAPS.md` G-23). See §6b.7, G-102.

`03_ALGORITHMS.md` §3.8 records the separate Tier 2A Mode B defect, where two scales are
compared **within** Phase 1 (`08_GAPS.md` G-36).

### 6b.6.4 The record-level triple

`result["confidence"]` (assigned at `enrichment/orchestrator.py:3861`, `:3920`, `:5254`, `:5636`, `:5652`, `:5655` among others) is a third
thing: a record-level `"high"`/`"medium"`/`"low"` label carried alongside `source` and
`tier_used`. `enrichment/provenance.py:9–16` records why it is not sufficient — it "collapses a
record whose Name 1 came from ROR, whose Name 2 came from a SERP→fetch→LLM chain and whose
department domain came from Tier 2B into a single label, and it cannot represent a field that
was written twice". It survives as an output column; the per-field scalars are what a consumer
should read.

---

## 6b.7 Discrepancies raised by this pass

`08_GAPS.md` numbers the collected set `G-01 … G-92` and records at D-2 that this pass was
missing from it. The items below are new and continue that sequence as **`G-93 … G-102`**; each
names both sides. Items this pass merely re-evidences (G-04, G-12, G-13, G-18, G-23, G-29,
G-30, G-32, G-36, G-55, G-57, G-71, G-72, G-79, G-86) are cited in place above and not
re-raised.

| # | Sev | Statement | Side A — the code at `86d173b` | Side B — the artefact that contradicts it, or the absence |
|---|---|---|---|---|
| **G-93** | high | The logging formatter renders none of the structured fields the middleware attaches, and the module docstring calls it structured JSON. | `api/middleware.py:87–91` sets the format to `"%(asctime)s %(levelname)s %(name)s [%(funcName)s] %(message)s"`. Demonstrated by executing the repository's own `configure_logging`: a `logger.info("request_complete", extra={"request_id": …, "status": 200, "duration_ms": 42})` emits `… request_complete` and nothing else (Appendix A.3). | `api/middleware.py:1`: "FastAPI middleware for structured JSON logging, request timing, and error handling." All ten idiom-(a) call sites lose their payload, including the three that carry the request correlation id (`api/middleware.py:30–34`, `:41–48`, `:62–69`) and the three that carry the Phase 2 token counts (`dedup/adjudicator.py:1301`, `:1402`, `:1534`). The surviving idiom-(b) records are a Python `dict` repr, not JSON. |
| **G-94** | medium | The HTTP request id and the per-record telemetry cannot be joined. | `request_id` is generated and stored at `api/middleware.py:22`, `:27` and returned as `X-Request-ID` (`:58`). | None of the 86 idiom-(b) telemetry records carries it (Appendix A.2); they key on `record_id`. The one place it is logged is an idiom-(a) record, so it is dropped at the formatter (G-93). A batch cannot be traced from an HTTP call to the records it processed. |
| **G-95** | medium | `tools/run_diff.py` states a column count that is two short. | `len(api.output_columns.RESPONSE_COLUMNS)` is **69** at this commit (Appendix A.4); `PROVENANCE_COLUMNS` is 7. | `tools/run_diff.py:203–205`: "Seven of the sixty-seven columns are provenance". The number is stale; the ratio the argument rests on is unaffected. |
| **G-96** | medium | A dropped `seed` degrades reproducibility silently and permanently, and leaves no trace in any artefact. | `llm/openai_client.py:324–344`: on a deployment rejecting `seed`, `_SEED_SUPPORTED = False` "for the life of the process", a `WARNING` is logged, and every later call omits it. `dedup/llm.py:66`, `:265–270` carries the same latch for `seed` and `temperature`. | The warning's own text: "Byte-identical re-runs are no longer guaranteed by the service." The latch is not in `EnrichmentSummary` (`api/models.py:697–865`), not in `RESPONSE_COLUMNS`, and not in any provenance event, so a run that lost its seed is indistinguishable from one that kept it — including to `tools/run_diff.py`, whose whole purpose is to decide whether two runs agree. |
| **G-97** | high | Re-running the scoring pipeline discards a steward decision recorded in `Validation.[approval_status]`. | `sql/usp_merge_validation_scores.sql:78` assigns `tgt.[approval_status] = src.approval_status` with no guard. `elect_golden_records` sets that field to `"proposed"` for every member of a `proposed`/`manual_review` cluster (`dedup/scoring.py:1322`) and `None` for a `unique` row (`:1308`). | The only source of `"approved"`/`"rejected"` is `apply_approval` (`dedup/scoring.py:628`) behind `POST /api/dedup/approve`, which is stateless and which no ADF pipeline calls (`08_GAPS.md` G-29). The column the four-eyes control depends on is therefore writable by an unattended re-run, and the twenty other columns in the same statement are legitimately overwritten by a rescore, so the fix cannot be to skip the statement. |
| **G-98** | medium | The merge procedures are idempotent but not convergent, and silently drop payload rows that do not join. | No `WHEN NOT MATCHED BY TARGET` and no `WHEN NOT MATCHED BY SOURCE` in any of the four (Appendix A.7); the `MERGE` is `UPDATE`-only in all four (`usp_merge_legacy_enriched.sql:86`, `usp_merge_legacy_issues.sql:66`, `usp_merge_validation_clusters.sql:63`, `usp_merge_validation_scores.sql:78`). | A payload row whose customer number is outside the group-code slice is discarded with no error and no count, and a target row the payload does not mention keeps whatever a previous run wrote. Neither outcome is reported to the caller: the procedures return no row count and ADF's activity has no output assertion. |
| **G-99** | medium | The whole provenance apparatus is dropped at the write-back boundary. | Six write-locked fields, an event log, a guard-rejection log and seven validated columns are produced for every record (`enrichment/provenance.py:70–77`, `api/models.py:550–557`, `enrichment/orchestrator.py:2051–2054`, `:3278–3280`). | `grep -ci provenance` over each of the four merge procedures returns `0` (Appendix A.8). `api/models.py:546–549` anticipates this — "ADF decides what, if anything, to store" — and the answer at this commit is nothing. On the ADF path a value in Legacy carries no attribution at all, which is exactly the condition `enrichment/provenance.py:5–8` declares inadmissible. |
| **G-100** | low | The origin invariant governs a field that never ships, so its effect is unauditable from any output. | `_slot_origin` is maintained at one funnel (`enrichment/orchestrator.py:1711`, invariant at `:1731–1732`, guard at `:1750–1753`) and popped before serialisation (`:3287`). | No response field, workbook column or table carries it. The seven records the invariant's own comment names as having lost `relocated-unverified` (`:1743–1747`) could only be identified by instrumenting a run, not by reading an output. Related to `08_GAPS.md` G-12. |
| **G-101** | low | One telemetry record writes a person's name and cannot be joined to a record. | `enrichment/person_affiliation.py:180` logs `{"step", "contact", "query", "institution", "department", "confidence"}`. | It is the only one of the 86 idiom-(b) records with no `record_id` (Appendix A.2), and the only one that puts a contact name in the log. `enrichment/provenance.py:19–24` states the opposite policy for the provenance store: `contact`, `care_of` and `email` are excluded from scope precisely so the store carries no personal data. |
| **G-102** | medium | Phase 1 forbids thresholding a model's self-report and Phase 2 thresholds one, and nothing maps the two onto each other. | Phase 1: hard rule 1 bars `llm` from `verified` in both `compute_confidence` (`enrichment/confidence.py:236–238`) and `validate` (`:300–310`); `comparable(a, b)` returns true only for equal scales (`enrichment/provenance.py:188–197`); `llm_self_reported` "must never be read as a measurement" (`:135–138`). | Phase 2: the adjudicator's self-reported merge confidence is coerced to `[0, 1]` (`dedup/adjudicator.py:124–136`), reduced per cluster to the member minimum (`dedup/scoring.py:1138–1148`) and thresholded at `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` (`dedup/scoring.py:50`) to gate a merge. Both are defensible in their own terms; the shipped word `confidence` names two incommensurable quantities across the two phases, and no code maps one onto the other — the shape of `08_GAPS.md` G-23. |

**A defect in an earlier pass, found here.** `01_TRACEABILITY.md:185` states of the four merge
procedures: "Each file is one physical line, so no statement inside them is separately citable".
At this commit each file is 66–89 lines (`wc -l sql/*.sql`, Appendix A.7) — commit `86d173b`'s
own message is "sql: reformat merge procs; adf: parameterised pipelines". The same pass records
at `:229` that the `86d173b` diff includes "four `sql/*.sql` (whitespace-only reformatting)",
so the two statements contradict each other within one document. Every SQL citation in this pass
is to a line number in the reformatted files. Pass 01 needs the sentence removed;
`08_GAPS.md` D-3 already records that Pass 01 was re-headed rather than re-derived.

---

## 6b.8 Summary

| Concern | State at `86d173b` |
|---|---|
| Logging | one formatter, three call idioms, and the most structured of the three loses its structure (G-93). 73 telemetry event types, 80 batch counters, none of it committed (G-12) |
| Determinism | four properties pinned by 90 passing tests; the cache that implements them is not in the repository (G-13), covers Phase 1 only (G-55), and the seed can be dropped without trace (G-96) |
| Error handling | 130 fail-open handlers, 115 on the service path, 81 catching bare `Exception`, 66 logging nothing. The per-record bulkhead at `enrichment/orchestrator.py:9263` is deliberate and reports itself; stage-level absorption does not |
| Merge idempotency | all four `UPDATE`-only and idempotent under repeat; none convergent; unmatched rows dropped silently (G-98); a rescore overwrites a steward's approval (G-97) |
| Provenance | structurally enforced at the write — locked fields, required evidence, an admissibility gate, a grammar assertion that raises — and dropped entirely at write-back (G-99) |
| Origin invariant | one funnel, one rule ("an origin may change only when the VALUE changes"), enforced on every department-slot write, observable in no output (G-100) |
| Confidence | one authority and three tokens on the export path; nine scales on the event, never compared across; and a fourth, incommensurable quantity in Phase 2 thresholded at 0.95 (G-102) |

---

## Appendix A — commands and verbatim output

All read-only over local files. No command in this pass writes to Azure, DATAshaper or SQL.

### A.1 Commit and branch

    $ git rev-parse HEAD
    86d173b8a4d715a619b0a2656986c145da7fa81e

    $ git rev-parse --abbrev-ref HEAD
    feature/llm-fixes

    $ date -I
    2026-09-07

### A.2 The logging idioms and the telemetry vocabulary

The walk classifies every `logger.<level>(…)` call in tracked `.py` files outside `tests/` by
its first positional argument and whether it passes `extra=`.

    $ python3 - <<'PY'
    import ast, subprocess
    from collections import Counter
    files=[f for f in subprocess.check_output(['git','ls-files','*.py']).decode().split()
           if not f.startswith('tests/')]
    dictcall=[]; extracall=[]; pctcall=[]
    for f in files:
        tree=ast.parse(open(f,encoding='utf-8').read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ('debug','info','warning','error','exception','critical')
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id in ('logger','logging','log')):
                has_extra = any(k.arg=='extra' for k in n.keywords)
                first = n.args[0] if n.args else None
                if isinstance(first, ast.Dict): dictcall.append((f,n.lineno))
                elif has_extra:                 extracall.append((f,n.lineno))
                else:                           pctcall.append((f,n.lineno))
    print('idiom (b) dict-as-message :', len(dictcall))
    for f,c in Counter(f for f,_ in dictcall).most_common(): print(f'    {c:4d}  {f}')
    print('idiom (a) event + extra=  :', len(extracall))
    for f,c in Counter(f for f,_ in extracall).most_common(): print(f'    {c:4d}  {f}')
    print('idiom (c) printf-style    :', len(pctcall))
    PY
    idiom (b) dict-as-message : 86
          68  enrichment/orchestrator.py
           8  enrichment/grounded_resolver.py
           4  enrichment/name_gate.py
           3  enrichment/flags.py
           1  enrichment/address_processing.py
           1  enrichment/person_affiliation.py
           1  enrichment/provenance.py
    idiom (a) event + extra=  : 10
           4  api/routes.py
           3  api/middleware.py
           3  dedup/adjudicator.py
    idiom (c) printf-style    : 208

The key census over the 86 idiom-(b) records, and the record that lacks `record_id`:

    distinct dict keys across the 86 records: 123
        86  step
        85  record_id
        22  field
        17  registry
        10  value
         9  reason
         7  query
         7  name1
         7  confidence
         7  candidate
         6  supplied
         6  lei_id
         6  qid
         5  official_name
         5  domain
         5  ror_id

    distinct "step" values: 73

    (records with no record_id)
    enrichment/person_affiliation.py:180  keys=['step', 'contact', 'query', 'institution', 'department', 'confidence']

### A.3 What the formatter actually renders

    $ python3 - <<'PY'
    import logging, io, sys
    sys.path.insert(0,'.')
    from api.middleware import configure_logging
    buf=io.StringIO()
    configure_logging("INFO", log_file="")
    root=logging.getLogger()
    root.handlers=[logging.StreamHandler(buf)]
    root.handlers[0].setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(funcName)s] %(message)s"))
    log=logging.getLogger("demo")
    log.info("request_complete", extra={"request_id":"abc12345","status":200,"duration_ms":42})
    log.info({"record_id":"13162559","step":"tier1_ror_miss","reason":"no_candidate"})
    print(buf.getvalue())
    PY
    2026-09-07 23:05:16,187 INFO demo [<module>] request_complete
    2026-09-07 23:05:16,187 INFO demo [<module>] {'record_id': '13162559', 'step': 'tier1_ror_miss', 'reason': 'no_candidate'}

The format string is copied verbatim from `api/middleware.py:87–91`; `configure_logging` is the
repository's own.

### A.4 Schema sizes, summary counters, and the App Insights search

    $ python3 -c "
    import sys; sys.path.insert(0,'.')
    from api.output_columns import RESPONSE_COLUMNS
    from enrichment.orchestrator import PROVENANCE_COLUMNS
    print('RESPONSE_COLUMNS:', len(RESPONSE_COLUMNS))
    print('PROVENANCE_COLUMNS:', len(PROVENANCE_COLUMNS), PROVENANCE_COLUMNS)
    from enrichment.provenance import GUARDS, SCOPED_FIELDS, CONFIDENCE_SCALES
    print('GUARDS:', len(GUARDS), GUARDS)
    print('SCOPED_FIELDS:', len(SCOPED_FIELDS))
    print('CONFIDENCE_SCALES:', len(CONFIDENCE_SCALES))
    from enrichment.confidence import CONFIDENCES, WITNESSES, REGISTRY_SOURCES, NON_CORROBORATING_WITNESSES
    print('CONFIDENCES:', CONFIDENCES); print('WITNESSES:', WITNESSES)
    print('REGISTRY_SOURCES:', sorted(REGISTRY_SOURCES))
    print('NON_CORROBORATING:', sorted(NON_CORROBORATING_WITNESSES))
    "
    RESPONSE_COLUMNS: 69
    PROVENANCE_COLUMNS: 7 ('name1_provenance', 'name2_provenance', 'domain_provenance', 'record_type_provenance', 'ror_id_provenance', 'lei_id_provenance', 'operating_name_provenance')
    GUARDS: 6 ('ror_country', 'distinctive_token', 'identifier_token', 'domain_ownership', 'gleif_name_verification', 'page_identity')
    SCOPED_FIELDS: 6
    CONFIDENCE_SCALES: 9
    CONFIDENCES: ('verified', 'provisional', 'low')
    WITNESSES: ('web', 'wikidata', 'llm', 'registry', 'domain', 'dba')
    REGISTRY_SOURCES: ['gleif', 'ror', 'wikidata']
    NON_CORROBORATING: ['llm']

`EnrichmentSummary`, counted from the AST and grouped by field-name prefix:

    total fields: 80
       16  wikidata_
       11  page_
       10  (ungrouped)
        6  liveness_
        5  lei_
        5  evidence_
        5  domain_from
        5  consensus_
        3  tier1_retry
        3  unchanged_
        2  tier1_
        2  registry_
        2  tier2a_
        2  contact_lookup
        1  domain_rejected
        1  tier2b_
        1  tier3_

    ungrouped: ['total', 'enriched', 'verified', 'unresolved', 'failed',
                'research_institution_count', 'company_count',
                'routing_type_mismatch_count', 'cache_hits_after_normalisation',
                'processing_time_ms']

Custom telemetry to Application Insights:

    $ grep -rn -i 'applicationinsights\|opencensus\|azure.monitor\|opentelemetry\|instrumentation_key\|APPINSIGHTS' \
        --include='*.py' --include='*.txt' --include='*.json' .
    host.json:4:    "applicationInsights": {

The single hit is the Function host's own logging configuration, not application code.

### A.5 The determinism and provenance suites

    $ python3 -m pytest tests/test_determinism.py -q
    ........................................................................ [ 80%]
    ..................                                                       [100%]
    90 passed, 1 warning in 0.19s

    $ python3 -m pytest tests/test_provenance.py tests/test_provenance_scheme_b.py -q
    ............................................sssss......                  [100%]
    122 passed, 5 skipped, 1 warning in 0.43s

(The one warning in each is `urllib3`'s `NotOpenSSLWarning` about the local LibreSSL build; it is
unrelated to the suites.)

### A.6 The fail-open `except` walk

A handler counts as fail-open when `ast.walk` over its body finds no `ast.Raise`.

    $ python3 - <<'PY'
    import ast, subprocess
    from collections import Counter
    files=[f for f in subprocess.check_output(['git','ls-files','*.py']).decode().split()
           if not f.startswith('tests/')]
    rows=[]
    for f in files:
        tree=ast.parse(open(f,encoding='utf-8').read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body=node.body
                has_raise=any(isinstance(s, ast.Raise)
                              for s in ast.walk(ast.Module(body=body, type_ignores=[])))
                logs=any('log' in ast.unparse(s) for s in body)
                only_pass=len(body)==1 and isinstance(body[0], ast.Pass)
                rows.append((f, node.lineno,
                             'BARE' if node.type is None else ast.unparse(node.type),
                             has_raise, logs, only_pass))
    print('total except handlers in production code:', len(rows))
    print('with any raise anywhere in the handler:', sum(1 for r in rows if r[3]))
    fo=[r for r in rows if not r[3]]
    print('FAIL-OPEN:', len(fo))
    print('  body is exactly `pass`:', sum(1 for r in fo if r[5]))
    print('  log something:', sum(1 for r in fo if r[4]))
    PY
    total except handlers in production code: 150
    with any raise anywhere in the handler: 20
    FAIL-OPEN: 130
      body is exactly `pass`: 11
      log something: 64

Split by path, and the exception types on the service path:

    service path (api/ enrichment/ dedup/ llm/ search/ utils/ config.py function_app.py): 115
    scripts/ tools/ eval/                                                               :  15

        81  Exception
         7  (TypeError, ValueError)
         6  ValueError
         5  RegistryUnavailableFrozen
         4  httpx.HTTPStatusError
         3  WikidataUnavailable
         2  ValidationError
         2  (json.JSONDecodeError, ValueError)
         2  ProvenanceGrammarError
         1  OSError
         1  RuntimeError
         1  SearchUnavailable

How many carry a marker on the `except` line:

    service-path fail-open handlers: 115
      whose except line carries a `# noqa` marker: 34
      whose except line carries any inline comment: 35
      with a bare except line, no comment: 80

The per-handler tables in §6b.3.2 and §6b.3.3 are emitted by the same walk, with the enclosing
function resolved as the innermost `FunctionDef`/`AsyncFunctionDef` whose span contains the
handler.

### A.7 The merge procedures

    $ wc -l sql/*.sql
          89 sql/usp_merge_legacy_enriched.sql
          69 sql/usp_merge_legacy_issues.sql
          66 sql/usp_merge_validation_clusters.sql
          81 sql/usp_merge_validation_scores.sql
         305 total

    $ grep -n -i 'when not matched\|insert\|delete\|begin tran\|commit\|rollback\|try' sql/*.sql
    (no output — the only matches for `output` are the sp_executesql OUTPUT parameters of the
     row-count guards, listed below)

    $ grep -n -i 'OUTPUT' sql/*.sql
    sql/usp_merge_legacy_issues.sql:41:    EXEC sp_executesql @chk, N'@pat NVARCHAR(60), @esc NCHAR(1), @c INT OUTPUT', ...
    sql/usp_merge_validation_clusters.sql:16:    EXEC sp_executesql @schk, N'@e SYSNAME, @x INT OUTPUT', ...
    sql/usp_merge_validation_clusters.sql:36:    EXEC sp_executesql @chk, N'@pat NVARCHAR(60), @esc NCHAR(1), @c INT OUTPUT', ...
    sql/usp_merge_legacy_enriched.sql:32:    EXEC sp_executesql @chk, N'@pat NVARCHAR(60), @esc NCHAR(1), @c INT OUTPUT', ...
    sql/usp_merge_validation_scores.sql:16:    EXEC sp_executesql @schk, N'@e SYSNAME, @x INT OUTPUT', ...
    sql/usp_merge_validation_scores.sql:36:    EXEC sp_executesql @chk, N'@pat NVARCHAR(60), @esc NCHAR(1), @c INT OUTPUT', ...

Assignment counts inside each `WHEN MATCHED THEN UPDATE SET`:

    sql/usp_merge_legacy_enriched.sql     31 assignments, 26 COALESCE-guarded
      unconditional: ['Record Type', 'ROR ID', 'LEI ID', 'Flag for Review', 'Flag Reason']
    sql/usp_merge_validation_scores.sql   21 assignments
    sql/usp_merge_validation_clusters.sql  6 assignments
    sql/usp_merge_legacy_issues.sql        1 assignment (the spliced @target_column)

### A.8 Provenance at the write-back boundary

    $ grep -ci provenance sql/usp_merge_legacy_enriched.sql
    0

    $ grep -i -l provenance sql/*.sql
    (no output)
