# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI service that cleans and enriches SAP customer master-data name/address
records (Bruker DATAshaper MDM pipeline). Two phases behind one app:

- **Phase 1 — enrichment** (`enrichment/`): takes `Name1`/`Name2`/`Name3` + address,
  resolves the organisation against registries and the web, and returns canonical
  names, `domain`, `record_type`, `ror_id`, `lei_id`, plus review flags and
  per-field provenance.
- **Phase 2 — dedup** (`dedup/`): clusters already-enriched rows into entities
  (LLM adjudicator) and elects a golden record per cluster (deterministic scoring).

`README.md` (3,700 lines) is the authoritative spec — pipeline stages, use-case
table, flag/issue catalogues, env vars, changelog. Read the relevant section
before changing behaviour; it explains *why* almost every guard exists.
`docs/thesis/` holds the derivations (`04_PARAMETERS.md` for every threshold),
and the root `*_findings.md` / `*_report.md` files are measurement write-ups.

## Commands

The venv is `.venv/` (Python 3.14 here; 3.11+ supported). On Windows PowerShell,
`.venv\Scripts\python.exe` works without activating.

```powershell
pip install -r requirements-dev.txt      # includes requirements.txt

# Run locally
$env:ENV="local"; python main.py                                   # uvicorn :8000, reload
$env:ENV="local"; $env:MOCK_EXTERNAL_CALLS="true"; python main.py  # no API keys needed

# Tests (fully mocked, ~37s for the whole suite)
pytest tests/ -q
pytest tests/test_orchestrator.py -v
pytest "tests/test_orchestrator.py::TestOrchestrator::test_tier1_full_resolution" -v
pytest tests/ --cov=enrichment --cov-report=term-missing
```

Most tests live inside a `TestX` class — a bare `file.py::test_name` node id will
not resolve.

**Known pre-existing failures (verified against a clean tree): 5 failed, 2815
passed, 5 skipped.** The five are `test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing`,
three in `test_orchestrator.py::TestOrchestrator` (`test_tier1_full_resolution`,
`test_web_search_fallback_for_name1`, `test_web_search_determines_record_type`),
and `test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric`.
Compare against that baseline, not against zero.

### Offline batch runs and the reproducibility gate

```powershell
python scripts/run_batch.py docs/thesis/chemspeed_us_100.xlsx `
  --out logs/runs/run.xlsx --json logs/runs/run.json --concurrency 5 `
  --retry-trace --trace-out logs/runs/trace.jsonl

python tools/run_diff.py logs/determinism/run1.json logs/determinism/run2.json
python scripts/test_local.py --mock          # JSON-fixture integration runner
```

`run_batch.py` drives the real orchestrator without the API, reusing the same
`_parse_xlsx` / `_rows_to_records` / `_build_output_xlsx` helpers as
`POST /enrich/file`. `tools/run_diff.py` is **the** reproducibility gate: it joins
two run artefacts on `(name1_original, city)` and diffs every column in
`api/output_columns.py`, exiting non-zero on any difference. Its precondition is
`evidence_network_calls == 0` on the second run — warm the evidence cache first
(`--frozen` sets `CACHE_FROZEN=true`, turning a cache miss into a recorded
`evidence-unavailable-frozen` rather than a network call).

Two run artefacts in different provenance schemes are not comparable; `run_diff`
detects that and refuses. Use `tools/provenance_invariance.py` to compare across
a scheme migration.

### The golden set

`docs/SAMPLE_DATA/` holds 99 records paired with the enriched row a reviewer
certified for each, plus the rules for grading every column.

```powershell
python scripts/eval_golden.py --out-dir logs/golden      # run + grade
python scripts/eval_golden.py --check-original           # the column-shift check
python scripts/golden_root_cause.py --eval logs/golden/golden_eval.json
python scripts/golden_root_cause.py --bucket abbreviation-direction
```

`tools/golden_eval.py` is the grader and is pure — it reads the reference,
applies `Match Rules` then `Cell Notes` (most specific wins) and never touches
the orchestrator, so `tests/test_golden_eval.py` runs it offline.

Two things to hold on to when reading a score from it:

- **It grades the deterministic layer only.** ROR, LEI, Domain, Record Type,
  Search Terms and every Flag/Provenance column are declared `skip` by the
  reference itself — 40 of 67 columns are graded. A `skip` is *no claim*, and is
  excluded from the denominator rather than counted as a pass.
- **The reference is one reviewer's work, and its Method sheet says so.** Its
  street and legal-form columns encode conventions the thesis does not specify
  and that the pipeline deliberately contradicts (`STREET_TYPE_ABBREVIATIONS`
  abbreviates; the reference expects `OLDEN STREET`). Check a disagreement
  against the documented rule before treating it as a defect —
  `golden_eval_report.md` works through all 244 from the 2026-08-29 run.

**Do not feed `test-all-100-original.xlsx` to the pipeline**: 75 of its 99 rows
have shifted columns (City holds an issue-code list and a date, Region holds the
eval-set label). The reference's own INPUT rows are the repaired ones and are
what `eval_golden.py` uses.

## Architecture

`main.py` (uvicorn) and `function_app.py` (Azure Functions v2 ASGI catch-all)
both wrap the *same* `api/app.py` FastAPI instance — never duplicate route or
pipeline logic between them. Because a single catch-all function fronts the app,
all endpoints share one auth level; there is no per-route auth in app code.

Routes (`api/routes.py`): `/health`, `/tiers`, `/enrich`, `/enrich/file`,
`/issues`, `/issues/compare`, `/api/dedup/cluster-block`, `/api/dedup/file`,
`/api/dedup/score`, `/api/dedup/score/file`, `/api/dedup/approve`, `/diag/llm`,
`/diag/dedup-llm`.

### Phase 1 pipeline

`enrichment/orchestrator.py` (~6,600 lines) is the controller. Records in a batch
run concurrently under an `asyncio.Semaphore`; each record walks a **tier
escalation** — cheapest and most deterministic first, escalate only on failure:

```
Stage 0  overflow_check      UC 0 — is Name1+Name2 one split name?
Stage 1  preprocess          UC 6-12, 14, 15 — regex only, no network/LLM
Stage 2  tier1_ror           institutions: ROR API
         tier1_lei           companies: GLEIF/LEI registry
Stage 2c wikidata            crosswalk lane — pointer or single witness, never an authority
Stage 2b person_affiliation  contact -> employer
Stage 3  tier2a_contact / tier2_canonical / tier2b_dept / lab_resolver
Stage 4  grounded_resolver   SERP + one grounded LLM read + registry re-verification
         tier3_llm           last resort; anything it writes is `unverified-inference`
Stage 5  tier1 re-lookup after canonicalisation
Stage 5b page_corroborator   open the candidate site and see if it names this record
Stage 5c liveness            does this organisation still exist?
         finalise()          the convergence point (see below)
Stage 6  batch_consensus     one identity per organisation per address, across the batch
```

`address_processing.py` runs as its own stage after name enrichment.

### Invariants — the part that matters most

Nearly every subtle bug this codebase has fixed was "two places wrote the same
field". The response is a set of **single authorities**. Do not add a second
writer; extend the authority.

- **`enrichment/provenance.py` — write-locked fields.** `_init_result` returns an
  `EnrichedRecord` (dict subclass) on which six keys are write-locked:
  `name1_enriched`, `name2_enriched`, `domain`, `record_type`, `ror_id`, `lei_id`
  (`SCOPED_FIELDS`). `record["domain"] = x` and `record.update(...)` raise
  `UnattributedWriteError`. The only write path is
  `record.write(field, value, evidence)`. An admissibility gate drops values whose
  origin cannot be reconstructed.
- **`enrichment/confidence.py` — Provenance Scheme B.** The exported grammar is
  `source:confidence[+witness]`, validated by `PROVENANCE_PATTERN` and asserted in
  `finalise` for all seven provenance columns. `compute_confidence()` is the only
  thing that assigns a confidence. Hard rules: `llm` never reaches `verified`; a
  witness-less `verified` belongs to a registry alone. **`web:acme.com:provisional`
  contains two colons — call `confidence.parse()`, never `split(":")`.**
- **`enrichment/flags.py` — flags are rebuilt, never appended.** `compute_flags()`
  runs *once*, from `finalise`, from the record's final state. Tiers record
  evidence as transient `_ev_*` keys; they never write a flag. `retract()` is the
  only post-finalise recourse (batch consensus), and it can only withdraw.
- **`enrichment/classifier.py` — `record_type` vs `routing_type`.** `routing_type`
  is provisional, internal, never serialised, and gates behaviour during the run.
  `record_type` is decided once, at the end of `finalise`, from ranked evidence.
- **`utils/domain_resolver.py` — the only writer of `domain` / `website_url`.**
  `canonicalise_domain()` reduces to the registrable domain; `canonicalise_host()`
  keeps the subdomain for department domains. `resolve_domain()` is the ownership
  guard — a candidate needs registry provenance, a name match, corroborating
  email, or on-domain search evidence, else `domain` stays empty and the record
  gets `domain-unverified`.
- **`enrichment/registry_match.py` — candidate selection, shared by ROR + GLEIF.**
  Total order `(score DESC, canonical registry id ASC)`; a near-tie inside
  `REGISTRY_AMBIGUITY_MARGIN` returns *no* match; a collision-prone short name
  needs a second independent signal. The same ordering shape governs SERP
  selection in `website_resolver`, Tier 2A/2B and the department probes.
- **`enrichment/locality.py` — the one locality comparator**, shared by the page
  read, ROR, GLEIF and Wikidata. Silence is not evidence; only a *stated*
  differing place is a contradiction.
- **`enrichment/consistency.py` — the cross-source gate**, run once in `finalise`
  before `compute_flags`. Registries agreeing is a double-witness confirmation
  (keep both, no flag); disagreeing means keep one, null the other, flag
  `source-conflict`.
- **`api/output_columns.py` — one output schema.** The mapping is both the XLSX
  header and the JSON serialization alias, so `/enrich` and `/enrich/file` can
  never drift. Rename a column here and nowhere else.

`finalise()` in `orchestrator.py` is where all of this converges: cross-source
gate → `record_type` classification → `compute_flags` → search-term derivation →
provenance validation. Behaviour that must see the record's *settled* state
belongs there, not in a tier.

### Determinism

Two runs of the same 101-row batch on the same code once produced 7 different
records. The controls that followed:

- **Sampling params are module constants, not env knobs** — `LLM_TEMPERATURE=0.0`,
  `LLM_TOP_P=1.0`, `LLM_SEED=42` in `llm/openai_client.py` (and `TEMPERATURE` in
  `dedup/llm.py`). A reproducibility control that varies per environment is not a
  control. Do not make them configurable.
- **No prompt may interpolate a clock, run id or record id**, and every evidence
  list injected into a prompt must be sorted by a stable key before rendering.
  `tests/test_determinism.py` asserts both structurally.
- **`utils/cache.py` — the evidence cache.** Every external answer (SERP, page
  fetch, Wikidata, ROR, GLEIF, *and the LLM*) is recorded under a key that is a
  pure function of the request — no run/batch/record ids, no dates. Entries are
  immutable. `utils.cache.cached_serp` and `PageFetcher(store=…)` are the only
  ways to issue a search or a fetch. `search.base.SearchUnavailable` distinguishes
  "the provider failed" from "no results" so a dropped TLS handshake is never
  cached as "this organisation has no web presence".

## Testing conventions

`tests/conftest.py` forces `MOCK_EXTERNAL_CALLS=true`,
`WIKIDATA_FIXTURE_REPLAY_ONLY=true`, and roots every cache namespace in a
throwaway temp dir, with an autouse fixture clearing the process-global evidence
cache between tests. Nothing in the suite reaches a live API, and no test reads a
committed page/Wikidata recording — they all inject a double from `tests/mocks/`.

Because the six scoped fields are write-locked, tests build records through
conftest helpers rather than dict literals:

- `make_record(**fields)` — an `EnrichedRecord` in a stated post-tier state for
  `finalise()` tests.
- `seed(record, evidence=None, **values)` — the `result.update(...)` equivalent,
  routing scoped keys through `write`.
- `fixture_evidence()` — for a value the test wrote but is not testing (renders
  `input:verified+web`). `tier3_evidence()` — when the test is standing in for
  Tier 3, so the `unverified-inference` flag derives correctly.

Injecting a partial `mock_clients` dict is normal; the missing lanes report
unavailable rather than calling out.

## Configuration

`config.py` loads a frozen `Settings` dataclass from env (via `python-dotenv`
locally, Azure Application Settings in production). Every threshold, timeout and
feature flag is centralised there — add new ones to `Settings`, not to
`os.getenv` at a call site. `.env` is gitignored; `.env.example` is the template.
Four evidence-cache namespaces (`serp/`, `registry/`, `fetch/`, `llm/` under
`tests/fixtures/`) are gitignored by default; `page_reads/` and `wikidata/` are
committed on purpose because a thesis re-run must reproduce the decisions they
drove.
