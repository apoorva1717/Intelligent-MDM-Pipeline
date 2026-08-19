Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 6b — Cross-cutting Concerns: CI/CD, Observability, Cost, Security

Scope: the four concerns that cut across every component documented in Passes 0–6 — how the
code reaches the Function App, what can be seen while it runs, what a run costs, and what
protects it. Sources are this repository at the commit above, plus the external-system context
recorded in `CONTEXT-EXTERNAL.md`.

---

## 0 · Conventions and evidence rules

- Every behavioural claim carries `path/file:LINE`. Claims about systems outside this
  repository cite `CONTEXT-EXTERNAL.md`, which records its own provenance as `[EXPORT]`,
  `[OBSERVED]`, or `[AUTHOR]`.
- **Absence is a finding and is evidenced like any other claim.** Where this document states
  that a control or artefact does not exist, the evidence is a reproducible search whose
  command is given, not an assertion.
- `⚠ MEASUREMENT REQUIRED` marks a number that is needed but not present in the repository;
  each occurrence names the script, query, or dashboard that would produce it.
- `⚠ NOT EVIDENCED` marks a fact that exists somewhere (typically in Azure resource
  configuration) but is not a repository artefact and therefore cannot be cited.
- **The branch is not `main`.** `HEAD` (`515cc7c`) is one commit ahead of `main`, whose tip is
  `8d07acb` (`git log --oneline main -3`). Every citation in this document is to the working
  tree at `515cc7c`.

---

# (a) CI/CD

## a.1 · The finding, stated first

**There is no continuous-integration and no continuous-deployment configuration in this
repository.** No workflow file, no pipeline definition, no build script, no container
definition, no infrastructure-as-code file, and no git hook exists. Nothing runs on push.
Nothing runs on pull request. There is no test gate and no lint gate. Deployment is manual,
performed from a developer workstation.

This is not an inference from missing documentation; it is the result of the following
searches, all run at `515cc7c`:

| Search | Result |
|--------|--------|
| `ls .github` | `No such file or directory` |
| `git ls-files` | 74 tracked files; none under `.github/`, none named `*.yml` or `*.yaml` |
| `find . -maxdepth 2 \( -name '*.yml' -o -name '*.yaml' -o -name 'Dockerfile*' -o -name '*.bicep' -o -name '*.tf' -o -name 'Makefile' -o -name '*.sh' -o -name '*.ps1' -o -name '*.toml' -o -name '*.cfg' -o -name '*.ini' \) -not -path './.venv/*' -not -path './.git/*'` | one hit: `./pytest.ini` |
| `git log --all --oneline -- .github azure-pipelines.yml` | empty — no such file has ever been committed on any ref |
| `ls .git/hooks \| grep -v '.sample'` | empty — no active git hook |

The complete set of tracked build-adjacent configuration is therefore five files:
`pytest.ini` (3 lines), `host.json` (20 lines), `.funcignore` (20 lines), `requirements.txt`
(14 lines), and `requirements-dev.txt` (5 lines), plus four `.vscode/*.json` files.

The README lists "CI/CD pipelines" as a use case for mock mode (`README.md:1749`). That is the
only occurrence of the term in the repository, and it refers to a hypothetical consumer of
`MOCK_EXTERNAL_CALLS`, not to a pipeline that exists.

## a.2 · What runs on push and on pull request

| Trigger | What runs | Evidence |
|---------|-----------|----------|
| `git push` to any branch | nothing | no workflow file (§a.1); no server-side automation artefact in the repository |
| pull request opened / updated | nothing | as above; no `.github/workflows/`, no required status check artefact |
| merge to `main` | nothing | as above |
| local commit | nothing | `.git/hooks` contains only the unmodified `*.sample` files (§a.1) |

The remote is a GitHub repository (`git remote -v` → `https://github.com/apoorva1717/Intelligent-MDM-Pipeline.git`).
GitHub-side settings — branch protection rules, required reviewers, required status checks —
are account configuration rather than repository artefacts and cannot be read from the working
tree. ⚠ NOT EVIDENCED. What *can* be read from the repository is the consequence: the history
contains **zero merge commits across all refs** (`git log --all --merges --oneline | wc -l` →
`0`) over 51 commits (`git rev-list --count --all` → `51`), so no pull request has ever been
merged into any branch of this repository. See §d.5.

## a.3 · Test gate

There is no gate. There is a test suite, invoked manually.

**Configuration.** `pytest.ini:1-3` is the whole of it:

```
[pytest]
asyncio_mode = strict
testpaths = tests
```

`asyncio_mode = strict` requires every async test to carry an explicit `@pytest.mark.asyncio`;
`testpaths = tests` scopes collection to `tests/`. No coverage threshold, no `--strict-markers`,
no minimum-version pin, no `addopts` of any kind.

**Dependencies.** `requirements-dev.txt:1-5` layers the test tooling over the runtime
requirements: `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`, `pytest-cov>=5.0.0`, `httpx>=0.27.0`.
`pytest-cov` is installed but no coverage invocation is configured anywhere.

**Documented invocation.** `README.md:1758-1759` gives `pytest tests/ -v`. `README.md:1749`
notes mock mode (`MOCK_EXTERNAL_CALLS=true`) as the way to run tests without API keys.

**Suite status at this commit.** Run with the project virtualenv
(`.venv/Scripts/python.exe -m pytest -q`):

```
3 failed, 1019 passed, 12 warnings in 29.97s
```

The three failures are in `tests/test_orchestrator.py`
(`test_tier1_full_resolution`, `test_web_search_fallback_for_name1`,
`test_web_search_determines_record_type`) and match the failures recorded in Pass 0
(`00_INVENTORY.md:336-343`), so they are stable and pre-existing rather than introduced by the
current branch. **A red suite is therefore the steady state at `HEAD`** — which is possible
precisely because no gate consumes the result.

The 54 test modules and what each covers are tabulated in `00_INVENTORY.md:350-408`; that
inventory is not repeated here.

**Deployment does not run the tests.** `.funcignore:11-12` excludes `tests` and `scripts` from
the deployment package, and `.funcignore:20` excludes `requirements-dev.txt`, so the deployed
Function App has neither the tests nor pytest installed. The test suite cannot run
post-deployment even as a smoke check.

## a.4 · Lint gate

There is no lint gate and no linter configuration.
`grep -rln "ruff|flake8|black|mypy|pylint"` over `*.txt`, `*.ini`, `*.toml`, `*.cfg` outside
`.venv` returns nothing, and §a.1 established that no `pyproject.toml`, `setup.cfg`,
`.flake8`, or `ruff.toml` exists.

The application code nonetheless contains **15 `# noqa` suppression directives** across
`api/`, `dedup/`, `enrichment/`, `llm/`, `search/`, and `utils/`
(`grep -rn "noqa" --include=*.py … | wc -l` → `15`), predominantly `# noqa: BLE001`
(blind-except), for example `api/routes.py:828`, `api/routes.py:873`,
`api/routes.py:1057`, `dedup/llm.py:197`, `llm/openai_client.py:209`-adjacent handlers.
`BLE001` is a Ruff rule code. The code is therefore written against a linter that the
repository does not configure and no gate runs — the suppressions are inert.

## a.5 · Deployment mechanism to the Function App

**Mechanism: the VS Code Azure Functions extension, invoked by hand.** The evidence is the
committed workspace configuration.

`.vscode/settings.json:1-9`:

| Key | Value | Meaning |
|-----|-------|---------|
| `azureFunctions.deploySubpath` | `"."` | the repository root is the deployment package root |
| `azureFunctions.scmDoBuildDuringDeployment` | `true` | dependencies are built **remotely**, on the Function App's Kudu/Oryx build service, from `requirements.txt` — the local `.venv` is not shipped |
| `azureFunctions.pythonVenv` | `".venv"` | the local virtualenv used by the `func: host start` task |
| `azureFunctions.projectLanguage` | `"Python"` | |
| `azureFunctions.projectRuntime` | `"~4"` | Functions host runtime v4 |
| `azureFunctions.projectLanguageModel` | `2` | Python programming model v2 (decorator-based), matching `function_app.py:12-19` |

`.vscode/extensions.json:3-4` recommends `ms-azuretools.vscode-azurefunctions` and
`ms-python.python`. `.vscode/tasks.json:4-11` defines only a local-run task
(`func: host start`, depending on a `pip install -r requirements.txt` task at `:12-25`), and
`.vscode/launch.json:4-13` only attaches a debugger to that local host on port 9091. **No task
or launch configuration performs a deployment** — the deploy action is the extension's own
command, driven from the VS Code UI. There is consequently no scripted, reviewable, or
repeatable deployment artefact in the repository.

**What is deployed.** `.funcignore:1-20` defines the exclusions: `.git*`, `.vscode`, `.venv`,
`__pycache__`, `.pytest_cache`, `tests`, `scripts`, `htmlcov`, `.coverage`,
`local.settings.json`, `.env`, `.env.example`, `*.md`, `README*`, `requirements-dev.txt`. The
exclusion of `local.settings.json:15` and `.env:16` is the mechanism that keeps development
secrets out of the deployment package (§d.1).

**What is deployed to.** The Function App is `mdm-pipeline-api`, reached at
`https://mdm-pipeline-api.azurewebsites.net` (`CONTEXT-EXTERNAL.md:135`, `:255` — both
`[EXPORT]` from the ADF pipeline JSON), running on the Bruker Azure spoke
(`CONTEXT-EXTERNAL.md:405-408` `[AUTHOR]`). The Azure resource group, subscription, and
hosting plan are ⚠ NOT EVIDENCED in the repository; the hosting plan specifically is
`CONTEXT-EXTERNAL.md:446` open item 6.

**Runtime shape of the deployed unit.** `function_app.py:12` creates
`func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)`; `function_app.py:15-19`
registers a single catch-all route `{*route}` that hands every request to
`AsgiMiddleware(fastapi_app)`. `host.json:11-15` sets `routePrefix` to `""`, so FastAPI paths
are served verbatim with no injected `/api` segment. All 13 routes (`00_INVENTORY.md:153-167`)
are therefore one Azure Function. The consequences for observability are in §b.6 and for
authentication in §d.4.

**Configuration delivery.** Environment variables reach the deployed app as Azure Application
Settings, not files: `config.py:1-5` ("In production (Azure Functions), environment variables
are set via Application Settings"), restated at `README.md:1836`. `load_dotenv()` is called
unconditionally at `config.py:22` and silently no-ops when `.env` is absent, which it is in the
deployment package (`.funcignore:16`).

**Is deployment manual or automated? Manual.** Three independent pieces of evidence: no
automation artefact exists (§a.1); the only deployment configuration is a VS Code UI-driven
extension setting (`.vscode/settings.json:2-3`); and no deployment credential, publish profile,
service principal, or federated-credential reference appears anywhere in the repository
(§d.1). There is no staging slot, no environment promotion, and no rollback procedure evidenced.

## a.6 · The other half of the pipeline: ADF change control

The Function App is one of five deployed components (`02_ARCHITECTURE.md:384-391`). The
orchestration that calls it is deployed separately and is **not** version-controlled in this
repository.

- The two ADF pipelines are published from ADF Studio on the Tillit tenant. Both carry
  `"lastPublishTime": "2026-07-29T12:09:37Z"` (`CONTEXT-EXTERNAL.md:182`, `:299`), which is the
  ADF publish action, not a git operation.
- Their JSON exists in this repository only as a transcribed `[EXPORT]` inside
  `CONTEXT-EXTERNAL.md:43-186` and `:205-303`, for documentation. It is not deployable
  artefact: no ARM template, no `factory.json`, no linked-service or dataset definition is
  tracked.
- Whether the ADF factory is git-integrated on the Tillit side is ⚠ NOT EVIDENCED.
- The DATAshaper configuration — table mappings, processes, validation rules — has **no file
  export at all**; it is configured through a SaaS web interface
  (`CONTEXT-EXTERNAL.md:337-339`). It cannot be version-controlled from this side.

**Consequence for the deployed system.** A change to `/enrich`'s response contract is deployed
by one manual action (VS Code publish) while the ADF activity and the stored procedure that
consume it are changed by two other manual actions in a different tenant, with no shared
version, no atomic release, and no automated compatibility check. Three of the five components
in `02_ARCHITECTURE.md:384-391` have no deployment artefact in any repository.

## a.7 · CI/CD summary

| Concern | State | Evidence |
|---------|-------|----------|
| Build | remote, on deploy, from `requirements.txt` | `.vscode/settings.json:3` |
| On push | nothing | §a.1 |
| On pull request | nothing | §a.1 |
| Test gate | none; suite is manual and currently red (3 failed / 1019 passed) | `pytest.ini:1-3`; `README.md:1758-1759`; run at §a.3 |
| Lint gate | none; 15 `noqa` directives with no configured linter | §a.4 |
| Type checking | none | §a.4 |
| Security scanning | none | §a.1 (no workflow exists to host one) |
| Dependency pinning | none — all 14 runtime requirements are `>=` floors | `requirements.txt:1-14` |
| Deployment | manual, VS Code Azure Functions extension | `.vscode/settings.json:1-9` |
| Environments | one; no staging slot evidenced | ⚠ NOT EVIDENCED |
| Rollback | none evidenced | ⚠ NOT EVIDENCED |
| ADF / DATAshaper change control | outside this repository | §a.6 |

---

# (b) Observability

## b.1 · The logging apparatus

One configuration function serves both deployments. `api/app.py:11-12` reads settings and
calls `configure_logging(settings.log_level, settings.log_file)` at import time, before the
`FastAPI` object is constructed — so it is in force for the local `uvicorn` entry point
(`main.py:8`) and the Azure Functions ASGI entry point (`function_app.py:10`) alike.

`configure_logging` (`api/middleware.py:75-135`) does five things:

1. Resolves the level from the `log_level` argument, defaulting to `INFO` when the string does
   not name a level (`api/middleware.py:85`). The value comes from `LOG_LEVEL`, default
   `"INFO"` (`config.py:113`, `:244`).
2. Installs a `StreamHandler` (console) and, unless disabled, a `RotatingFileHandler`
   (`api/middleware.py:93`, `:105-107`).
3. Resolves the file path: explicit argument → `LOG_FILE` env var → `logs/enrichment_api.log`
   under the project root; `LOG_FILE=""` disables file logging entirely
   (`api/middleware.py:97-102`; documented `.env.example:89-92`).
4. Re-parents `uvicorn`, `uvicorn.access`, and `uvicorn.error` onto the root handlers
   (`api/middleware.py:123-126`), so local access lines land in the same file.
5. Raises `httpx`, `httpcore`, `openai`, and `urllib3` to `WARNING`
   (`api/middleware.py:132-135`).

Rotation is 10 MB × 5 backups, UTF-8 (`api/middleware.py:105-107`). Handler installation uses
`logging.basicConfig(level=level, handlers=handlers, force=True)` (`api/middleware.py:118`) —
`force=True` discards any handler the Azure Functions worker installed before the app module
was imported. ⚠ UNVERIFIED — whether this displaces the worker's own App Insights handler in
the deployed app is not determinable from the repository; it is the single highest-value item
to verify against a live run (§b.7).

**Step 5 is load-bearing for what is *not* observable.** Every outbound HTTP call in the
system goes through `httpx`, `requests` (`urllib3`), or the `openai` SDK. Raising those three
loggers to `WARNING` means no successful outbound request to ROR, GLEIF, SerpAPI, an arbitrary
web host, or Azure OpenAI produces a log line of its own. External-call visibility exists only
where the application code logs it explicitly (§b.3).

## b.2 · What is logged, and at what level

Counted from source at `515cc7c`, excluding `tests/` and `scripts/`
(per-file counts of `logger.debug|info|warning|error|exception`):

| Module | debug | info | warning | error | exception | total |
|--------|------:|-----:|--------:|------:|----------:|------:|
| `enrichment/orchestrator.py` | 0 | 41 | 4 | 2 | 2 | 49 |
| `api/routes.py` | 0 | 15 | 1 | 0 | 0 | 16 |
| `enrichment/tier1_ror.py` | 0 | 11 | 0 | 1 | 1 | 13 |
| `dedup/adjudicator.py` | 0 | 3 | 6 | 2 | 0 | 11 |
| `enrichment/tier1_lei.py` | 0 | 8 | 0 | 1 | 1 | 10 |
| `enrichment/website_resolver.py` | 0 | 10 | 0 | 0 | 0 | 10 |
| `enrichment/tier2a_contact.py` | 0 | 8 | 0 | 0 | 1 | 9 |
| `config.py` | 0 | 1 | 4 | 0 | 0 | 5 |
| `enrichment/tier2_canonical.py` | 0 | 5 | 0 | 0 | 0 | 5 |
| `enrichment/tier2b_dept.py` | 0 | 5 | 0 | 0 | 0 | 5 |
| `enrichment/tier3_llm.py` | 0 | 4 | 0 | 0 | 1 | 5 |
| `llm/openai_client.py` | 0 | 2 | 2 | 1 | 0 | 5 |
| `dedup/llm.py` | 0 | 1 | 2 | 1 | 0 | 4 |
| `enrichment/company_canonical.py` | 0 | 3 | 1 | 0 | 0 | 4 |
| `enrichment/lab_resolver.py` | 0 | 4 | 0 | 0 | 0 | 4 |
| `enrichment/person_affiliation.py` | 0 | 2 | 0 | 0 | 2 | 4 |
| `api/middleware.py` | 0 | 2 | 0 | 0 | 1 | 3 |
| `enrichment/overflow_check.py` | 0 | 3 | 0 | 0 | 0 | 3 |
| `search/page_fetcher.py` | 2 | 0 | 1 | 0 | 0 | 3 |
| `dedup/scoring.py` | 0 | 0 | 2 | 0 | 0 | 2 |
| `dedup/scoring_xlsx.py` | 0 | 0 | 2 | 0 | 0 | 2 |
| `enrichment/address_processing.py` | 0 | 2 | 0 | 0 | 0 | 2 |
| `enrichment/preprocess.py` | 0 | 2 | 0 | 0 | 0 | 2 |
| `search/duckduckgo_client.py` | 0 | 0 | 0 | 0 | 1 | 1 |
| `search/serpapi_client.py` | 0 | 0 | 0 | 0 | 1 | 1 |
| **Total** | **2** | **132** | **25** | **8** | **11** | **178** |

Reproduce with:
`for f in $(git ls-files "*.py" | grep -vE "^(tests|scripts)/"); do grep -c "logger\.info" "$f"; done`
(and the analogous counts per level).

**Level discipline.** The system is effectively single-level. 132 of 178 call sites are `INFO`
(74%), and only two are `DEBUG` — both in `search/page_fetcher.py`. Per-record tier decisions,
per-candidate website scoring, and every department-probe step are all `INFO`
(`enrichment/orchestrator.py:1021-1024`, `:1119-1122`, `:1166-1169`, `:1216-1220`,
`:1262-1265`, `:1320-1323`, `:1327-1331`). Lowering `LOG_LEVEL` to `WARNING` therefore removes
essentially all pipeline visibility at once — there is no intermediate verbosity. Conversely,
`LOG_LEVEL=DEBUG` adds only the two page-fetch lines.

**Level semantics.** `WARNING` is used both for genuine degradation (`config.py:141-145`
SerpAPI key absent; `dedup/adjudicator.py:587-590` candidate cap exceeded;
`llm/openai_client.py:111-115` TLS verification disabled) and for ordinary rejections
(`enrichment/orchestrator.py:713-717` Tier 3 identity-guard rejection). `ERROR` and
`EXCEPTION` are used for caught-and-continued failures, never for request failure — every
external dependency is fail-open at the request boundary (`06_EXTERNAL_DEPS.md:748-753`). An
alert rule keyed on `ERROR` would fire on normal degraded operation.

## b.3 · The two logging idioms, and what the formatter destroys

The codebase uses two mutually incompatible structured-logging idioms, and the formatter
supports only one of them.

**The formatter.** `api/middleware.py:87-91`:

```
"%(asctime)s %(levelname)s %(name)s [%(funcName)s] %(message)s"
```

It renders five fields. It does **not** render any key passed via `extra=`.

**Idiom 1 — `extra=` keyword dictionary.** Used by the request middleware and by all Phase 2
and scoring telemetry:

| Site | Message | Keys passed via `extra=` |
|------|---------|--------------------------|
| `api/middleware.py:28-35` | `request_start` | `request_id`, `method`, `path` |
| `api/middleware.py:61-70` | `request_complete` | `request_id`, `method`, `path`, `status`, `duration_ms` |
| `api/middleware.py:41-49` | `request_error` | `request_id`, `method`, `path`, `duration_ms` |
| `dedup/adjudicator.py:812-824` | `dedup_llm_call` | `block_id`, `mode`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `decisions`, `model_version`, `prompt_version` |
| `dedup/adjudicator.py:883-899` | `dedup_block` | `block_id`, `rows_in`, `distinct_signatures`, `mode`, `llm_calls`, `clusters`, `rows_manual_review`, `errors`, `candidates_generated`, `candidates_by_rule`, `rejected_with_reasoning`, `candidate_cap_exceeded` |
| `dedup/adjudicator.py:995-1011` | `dedup_request` | `summary`, `total_prompt_tokens`, `total_completion_tokens`, `total_tokens`, `total_latency_ms`, `prompt_version`, `candidates_generated`, `candidates_by_rule`, `rejected_candidates_with_reasoning`, `candidate_cap_exceeded_blocks` |
| `api/routes.py:935-942` | `scoring_request` | `summary`, `issues`, `total_latency_ms` |
| `api/routes.py:1013-1021` | `scoring_request` | `summary`, `upload_name`, `total_latency_ms` |

**In the console and in the rotating log file, every one of these keys is dropped.** The
rendered line is the bare message — `dedup_llm_call` with no block id, no latency, no token
count; `request_complete` with no status and no duration. The richest telemetry in the system,
including the only token accounting that exists anywhere (§c.4), is invisible in the file
handler that `api/middleware.py:105-107` installs.

The fields survive only if a handler that serialises `LogRecord` attributes consumes them —
i.e. the Application Insights path (§b.6). ⚠ UNVERIFIED — whether these `extra` keys arrive as
App Insights `customDimensions` is a property of the Azure Functions Python worker's logging
integration, not of this repository, and must be confirmed against a live run.

**Idiom 2 — dict-as-message.** Phase 1 passes a dictionary as the log *message* itself, so
`%(message)s` renders `str(dict)` and the content survives the formatter. Examples:
`enrichment/orchestrator.py:1519-1525` (`person_affiliation_confirmed`),
`:1540-1546` (`person_affiliation_unresolved`), `:1659-1669` (`tier1_lei`),
`:1731-1736` (`uc0_overflow_flagged`), `:1784-1789` (`preprocess`),
`:1948-1953` (`tier1_name1_cleaned`), `:1963-1974` (`tier1_ror_parent`),
`:2092-2099` (`tier1_child_local_match_*`), `:2116-2121` (`tier1_ror_miss`),
`:2203-2210` (`tier1_lei_typo_recovered`), `:2309-2316` (`uc13_lab_resolver_result`),
`:2390-2396` (`tier2_canonical_result_*`).

The output is a Python `repr` of a dict — single-quoted keys, `None` rather than `null` — not
JSON. It is not machine-parseable as JSON without a repair step, and it is not indexable as
App Insights custom dimensions because the payload is inside the message string.

**Net effect.** Phase 1's structured records are readable in the file but not queryable; Phase
2's structured records are queryable in App Insights but absent from the file. No single sink
holds both. The module docstring at `api/middleware.py:1` calls this "structured JSON logging";
neither idiom emits JSON. Code wins → recorded in §e.

## b.4 · Identifiers, and the end-to-end traceability chain

### b.4.1 What identifiers exist

| Identifier | Generated / sourced at | Scope | Appears in |
|---|---|---|---|
| `request_id` | `str(uuid.uuid4())[:8]` — `api/middleware.py:22` | one HTTP request | three middleware log lines (`:31`, `:44`, `:64`); response headers `X-Request-ID` (`:58`) |
| `X-Duration-MS` | `api/middleware.py:59` | one HTTP request | response header only |
| `record_id` | `EnrichmentRecord.record_id` = `customer or ecc_customer_number` — `api/models.py:230-231`; alias-bound to the SAP `Customer` column — `api/models.py:43-47` | one record | Phase 1 log lines (`enrichment/orchestrator.py:399-403`, `:812-815`, and the dict-message sites in §b.3); response field `record_id` (`api/models.py:324`) |
| `block_id` | read from the request row (`[Block ID]`, precomputed by the DATAshaper address gate — `CONTEXT-EXTERNAL.md:309-310`) | one dedup block | `dedup_llm_call` (`dedup/adjudicator.py:813`), `dedup_block` (`:884`), and the cap warning (`:587-590`) |
| `row_id` | ← `Customer` in the ADF Validation projection (`CONTEXT-EXTERNAL.md:226`) | one dedup row | response rows; **not logged** |
| `cluster_id` | assigned by clustering; opaque `c_`-prefixed hash (`CONTEXT-EXTERNAL.md:392-393`) | one cluster | response rows; logged only by `dedup_approve` (`api/routes.py:957-960`) |
| `signature_id` | `dedup/signatures.py` | one signature | response rows; Mode B warnings (`dedup/adjudicator.py:460-462`, `:491-493`) |
| `prompt_version` | `dedup/prompts.py` `PROMPT_VERSION` | build | `dedup_llm_call`, `dedup_request` |
| `model_version` | `getattr(response, "model", …)` — `dedup/llm.py:194` | one LLM call | `dedup_llm_call` |

### b.4.2 The one chain that actually closes

**`Customer` is the only identifier that traverses the whole system.** It is the DS `code`
(group-code prefix plus source key) that DATAshaper carries unchanged across Import → Legacy →
Validation → load file (`02_ARCHITECTURE.md:415-417`, citing
`Datashaper-Tutorial-Part1.txt:1379-1403`), and it is the idempotency key for every merge-back.
Its path:

```
test_77.Legacy.Customer
  → ADF Lookup1: SELECT * FROM test_77.Legacy … FETCH NEXT 50   (CONTEXT-EXTERNAL.md:106)
  → /enrich request body, JSON key "Customer"                    (api/models.py:43-47)
  → EnrichmentRecord.record_id                                   (api/models.py:230-231)
  → Phase 1 log lines "[record_id] …" / {"record_id": …}         (orchestrator.py:1519-1525 et al.)
  → EnrichmentResult.record_id in the response                   (api/models.py:324)
  → usp_merge_legacy_enriched(payload)                           (CONTEXT-EXTERNAL.md:161-170)
  → test_77.Legacy row, keyed by code
```

For Phase 2 the analogous chain is `Validation.Customer → row_id → response row`
(`CONTEXT-EXTERNAL.md:226`), but `row_id` is never written to a log line, so the Phase 2 chain
closes through the **response payload only**, not through logs. `block_id` is logged and
therefore is the only Phase 2 log-side join key — one level coarser than the row.

### b.4.3 Where the chain breaks

**Break 1 — `request_id` is generated and then abandoned.** `api/middleware.py:26` sets
`request.state.request_id` with the comment "Attach request_id for downstream correlation". A
repository-wide search for the symbol
(`grep -rn "request_id" --include=*.py`, excluding `tests/`) returns **seven hits, all inside
`api/middleware.py`** (`:22`, `:26`, `:31`, `:44`, `:58`, `:64`). No route handler, no
orchestrator method, and no dedup function ever reads `request.state.request_id`. The
correlation hook is dead code.

The consequence is precise: given a `dedup_llm_call` line, nothing identifies which HTTP
request produced it; given a `[TEST8_41000009] tier1_ror_parent` line, nothing identifies which
of the ~N/50 `/enrich` batches it belonged to. Within a single-concurrency run the batch can be
recovered by timestamp ordering, but `/enrich` fans records out with
`asyncio.gather` at `DEFAULT_MAX_CONCURRENCY=5` (`config.py:216-218`) and the dedup adjudicator
runs blocks at `DEDUP_MAX_CONCURRENCY=5` (`.env.example:38-39`), so lines from different
records and different blocks interleave with no key to separate them.

**Break 2 — no inbound correlation is accepted.** `RequestLoggingMiddleware.dispatch`
(`api/middleware.py:21-72`) reads `request.method` and `request.url.path` and nothing else from
the inbound request. It does not read `traceparent`, `Request-Id`, `x-ms-correlation-request-id`,
or any custom header. The ADF pipeline run id and activity run id are therefore not carried
into the service under any name, and the ADF `Web1` activity sends only
`{"Content-Type": "application/json"}` (`CONTEXT-EXTERNAL.md:134`, `:254` `[EXPORT]`) — it
supplies no correlation header either. **An ADF run cannot be joined to a service log line by
any identifier.** ⚠ Whether the Azure Functions host injects and honours W3C `traceparent`
independently of application code is platform behaviour, not a repository artefact —
⚠ UNVERIFIED.

**Break 3 — the return path is header-only.** `request_id` and `duration_ms` leave the service
as response headers `X-Request-ID` / `X-Duration-MS` (`api/middleware.py:58-59`), not in the
response body. The ADF `Merge Back` activity passes `@string(activity('Web1').output)` to the
stored procedure (`CONTEXT-EXTERNAL.md:165-166`). ⚠ UNVERIFIED — whether ADF's Web-activity
`output` object includes `ADFWebActivityResponseHeaders`, and therefore whether `X-Request-ID`
reaches `usp_merge_legacy_enriched` inside the payload string, is not determinable from the
export; it would be settled by inspecting one activity run's output JSON in ADF monitoring.

**Break 4 — the database side is opaque.** The stored procedures
`dbo.usp_merge_legacy_enriched` and `dbo.usp_merge_validation_clusters` are known only by name
and signature; their bodies are not exported (`CONTEXT-EXTERNAL.md:318-333`). Whether they log,
whether they record a run stamp, and what they do with a partially-formed payload are all
unknown. There is no `enriched_at` watermark in the pipeline as exported — it is a planned
change (`CONTEXT-EXTERNAL.md:194-197` `[AUTHOR]`), so a row currently carries no evidence of
*when* or *whether* it was enriched.

### b.4.4 Traceability verdict

| Question an operator would ask | Answerable? | Why |
|---|---|---|
| Which records did this ADF run process? | **no** | no run id anywhere in the service (Break 2) |
| What did the service decide for customer `TEST8_41000009`? | **yes** | `record_id` in Phase 1 logs and in the response (§b.4.2) |
| Which HTTP request produced this log line? | **no** for every line except the three middleware lines (Break 1) |
| How long did record X take? | **no** | only the batch total is timed (`orchestrator.py:838-841`) |
| How many tokens did record X cost? | **no** for Phase 1 (§c.4); **yes per block** for Phase 2 (`dedup/adjudicator.py:812-824`) |
| Did this row get written back to Legacy? | **no** | no watermark, no merge-side logging (Break 4) |
| Which prompt version produced this cluster? | **yes** | `prompt_version` on `dedup_llm_call` / `dedup_request` |

## b.5 · Health and diagnostic surfaces

`GET /health` (`api/routes.py:75-85`) returns `status="healthy"` **as a literal**
(`api/routes.py:80`) together with `version`, `env`, `mock_mode`, and `tiers_available=[1,2,3]`.
It performs no dependency check: it does not call ROR, GLEIF, SERP, or Azure OpenAI, and it does
not verify that `AZURE_OPENAI_API_KEY` is set. It returns `healthy` from an app whose LLM calls
will all fail — `validate_env` only warns (`config.py:122-135`), by design, "allows the app to
start so health checks still work". As a monitoring signal it reports process liveness only.

`GET /diag/llm` (`api/routes.py:1034-1063`) and `GET /diag/dedup-llm`
(`api/routes.py:1066-1102`) are the real diagnostic surface, and they exist because the log
sink was not trusted: "Use this on Azure when you can't see logs — the actual exception string
is returned in the HTTP response body" (`api/routes.py:1038-1039`). Each makes one live LLM
call and returns the raw outcome plus an environment snapshot. Their security and cost
consequences are in §d.4 and §c.6.

`GET /tiers` (`api/routes.py:1105-1118`) returns the running threshold configuration —
`ror_confidence_threshold`, `fuzzy_match_threshold`, `max_page_content_chars`,
`page_fetch_timeout_seconds`, `default_max_concurrency`, the resolved `serp_provider`, and
`mock_mode`. It is the only way to confirm from outside which configuration a deployed instance
actually loaded, which matters given the `.env.example`-vs-code default divergences recorded in
`04_PARAMETERS.md:266-305`.

## b.6 · What reaches Application Insights

**Enablement.** `host.json:3-10` is the only Application Insights configuration in the
repository:

```json
"logging": { "applicationInsights": { "samplingSettings": {
    "isEnabled": true, "excludedTypes": "Request" } } }
```

**No SDK.** `grep -i "applicationinsights|opencensus|opentelemetry|azure.monitor"` over the
repository excluding `.venv/` and `docs/` returns exactly one hit: `host.json:4`.
`requirements.txt:1-14` declares no telemetry package. Telemetry therefore travels the Azure
Functions host's built-in logging integration only — stated in the code at
`dedup/adjudicator.py:8-9` ("Azure Functions ships them to the `mdm-pipeline-insights`
Application Insights instance") and at `README.md:1836`, `README.md:1299`,
`README.md:2034`. The instance name `mdm-pipeline-insights` appears only in prose; the
connection string / instrumentation key is an Application Setting, ⚠ NOT EVIDENCED.

**Sampling.** `isEnabled: true` turns on adaptive sampling; `excludedTypes: "Request"` exempts
Request telemetry from it. Traces (all 178 application log lines), dependencies, and exceptions
are therefore **subject to being sampled away under load**, while request telemetry is retained
in full. This is the opposite of what the system's own diagnostics need: the request lines carry
almost nothing (§b.3), and the traces carry everything.

**Operation granularity.** Because `function_app.py:15` registers one catch-all route for all
13 endpoints, the Function App has exactly one function name, `http_app_func`
(`function_app.py:16`), and `host.json:13` strips the route prefix. ⚠ UNVERIFIED — whether App
Insights Request telemetry consequently reports a single operation name for `/enrich`,
`/issues`, and `/api/dedup/cluster-block` alike is platform behaviour; if it does, the
per-endpoint latency and failure breakdown that Requests would normally give is lost, and the
`path` field on `request_complete` (`api/middleware.py:66`) becomes the only per-route
discriminator — a field the console formatter drops (§b.3).

**Retention.** ⚠ NOT EVIDENCED — the App Insights retention setting is Azure resource
configuration. Local file retention is bounded by rotation only: 10 MB × 5 backups, with **no
time-based expiry and no deletion policy** (`api/middleware.py:105-107`;
`05_DATA_MODEL.md:1104-1105`). Given that the same files carry unredacted person names
(§d.6), this is a data-protection finding, not only an operations one.

## b.7 · What is not observable

Stated as a list, because each item is a specific blind spot rather than a general shortfall.

1. **Phase 1 token consumption — at all.** `call_openai` returns
   `response.choices[0].message.content` and discards `response.usage`
   (`llm/openai_client.py:198-208`). Every Phase 1 LLM call — overflow check, plain-name
   classification, company canonicalisation, Tier 2 canonicalisation, Tier 2A, Tier 2B, Tier 3,
   website Path C, person affiliation, address residual classification — is unmeasured. Phase 2
   does capture it (`dedup/llm.py:188-195`), so the asymmetry is a two-line omission, not a
   design constraint.
2. **Per-record latency and per-record cost in Phase 1.** Only the batch total is timed
   (`enrichment/orchestrator.py:838-841`: total, enriched, failed, `batch_ms`). No per-record
   or per-tier timing exists.
3. **Which tier resolved a record, as a queryable field.** The escalation path is reconstructible
   only by reading the sequence of dict-message lines for that `record_id` (§b.3, idiom 2);
   there is no `tier_resolved` counter, dimension, or summary field.
4. **Cache effectiveness.** `BatchCache.stats` exists (`utils/cache.py:109-111`, returning
   `ror_entries` / `serp_entries`) and is never called by any logging site. Cache hit rate —
   which directly determines SERP spend (§c.3) — is unmeasured.
5. **Structured fields in the file and console sinks.** All `extra=` keys are dropped by the
   formatter (§b.3), including every token count and every latency.
6. **The correlation between an ADF run and a service log line** (§b.4.3, Break 2).
7. **Whether a Legacy row was enriched, and when.** No watermark exists in the pipeline as
   exported (`CONTEXT-EXTERNAL.md:194-197`).
8. **The database and DATAshaper side.** No stored-procedure body, no DS process log, no
   validation-run record is available to this system (`CONTEXT-EXTERNAL.md:318-333`,
   `:337-339`).
9. **Sampled-away traces.** Under load, an unknown fraction of application logs never reaches
   App Insights (§b.6), and the fraction itself is not recorded on the surviving records.
10. **External dependency health.** No circuit breaker, no failure counter, no availability
    metric. A total ROR outage manifests as every record silently escalating to Tier 2/3 with
    higher spend and lower confidence (`06_EXTERNAL_DEPS.md:723`) — visible only by reading
    individual `tier1_ror_miss` lines (`enrichment/orchestrator.py:2116-2121`).
11. **Any metric, counter, or gauge.** The system emits logs only. There is no custom metric,
    no percentile, and no dashboard definition in the repository.
12. **Alerting.** No alert rule, action group, or notification artefact exists in the repository.

**⚠ MEASUREMENT REQUIRED — the observability items a live run would settle**, with the exact
means for each:

| Unknown | How to obtain it |
|---|---|
| Whether `extra=` keys arrive as App Insights `customDimensions` | run one `/api/dedup/cluster-block` against the deployed app, then query `traces \| where message == "dedup_llm_call" \| project customDimensions` |
| Whether `basicConfig(force=True)` (`api/middleware.py:118`) displaces the worker's App Insights handler | same query — an empty `traces` table for application messages is the positive result |
| Whether App Insights reports one operation name for all 13 routes | `requests \| summarize count() by name, url` |
| Effective sampling rate | `traces \| summarize sum(itemCount), count()` — `itemCount > 1` quantifies what was sampled away |
| Whether `X-Request-ID` reaches the merge-back payload | inspect one ADF `Web1` activity run's output JSON in ADF monitoring |
| App Insights retention (days) | the Application Insights resource blade for `mdm-pipeline-insights` |
| Per-batch `/enrich` duration | already logged as `batch_ms` (`enrichment/orchestrator.py:838-841`) — read it from one 50-row run; this is `CONTEXT-EXTERNAL.md:447` open item 7 |

---

# (c) Cost

## c.1 · What the repository contains, and what it does not

**No monetary figure, unit price, plan tier, credit balance, or billing reference appears
anywhere in this repository.** This was established in Pass 6 by searching for `cost`,
`pricing`, `price`, `$0.`, `per 1k`, `credits`, and `quota` outside `docs/`
(`06_EXTERNAL_DEPS.md:640-651`), and it holds at this commit. The `Cost` column of
`README.md:82-90` (`Zero` / `Low` / `Low-Medium` / `Medium`) is an ordinal design-time ranking
used to justify the tier-escalation order — "start with the cheapest, most reliable method and
escalate only when cheaper methods fail" (`README.md:80`) — and carries no unit
(`06_EXTERNAL_DEPS.md:653-657`).

**Every unit price in §c.5 is therefore a symbol, and every symbol is ⚠ MEASUREMENT REQUIRED.**
What the repository *does* determine is the *structure* of the cost: which stages can incur
charges at all, and how many chargeable calls each can make. That structure is what this
section establishes.

## c.2 · Free and deterministic versus paid

A stage is **free and deterministic** here if it makes no network call and its output is a pure
function of its input — the same input yields the same output, at zero marginal cost, on every
re-run.

### c.2.1 Free and deterministic

| Stage | Endpoint(s) | Evidence |
|---|---|---|
| Issue detection | `POST /issues`, `POST /issues/compare` | regex and string checks only, no enrichment, LLM, or network I/O (`enrichment/issue_detection.py:9-16`); confirmed no external calls (`00_INVENTORY.md:290-294`) |
| Golden-record election | `POST /api/dedup/score`, `/score/file` | "pure arithmetic over `dedup/weights.json` … can be re-run on retuned weights without paying for LLM adjudication again" (`api/routes.py:900-903`); `elect_golden_records` (`dedup/scoring.py:1033-1052`) |
| Approval application | `POST /api/dedup/approve` | `apply_approval` copies rows and sets fields (`dedup/scoring.py:574-603`); no external call |
| STEP A signature collapse | inside `/api/dedup/cluster-block` | deterministic; survives a total LLM outage — "Deterministic STEP A signature collapse still runs, so exact duplicates are still collapsed" (`06_EXTERNAL_DEPS.md:745`) |
| Residue candidate **nomination** | inside `/api/dedup/cluster-block` | similarity arithmetic in `dedup/candidates.py`; "Nomination never merges — the LLM verdict decides" (`config.py:104-106`). Nomination is free; the adjudication it triggers is not (§c.4) |
| Search-term derivation | inside `/enrich` | deterministic string derivation (`enrichment/search_terms.py`); no call site in the external-call inventory (`06_EXTERNAL_DEPS.md:679-700`) |
| Address processing **except** residual classification | inside `/enrich` | the address stage survives a total LLM outage apart from residual classification (`06_EXTERNAL_DEPS.md:740-742`) |
| Workbook I/O | all `/…/file` endpoints | `openpyxl` parse and emit; no network |

Three of the system's ten POST endpoints — `/issues`, `/api/dedup/score`,
`/api/dedup/approve` — make **no external call whatsoever** and are consequently both free and
immune to any outage (`06_EXTERNAL_DEPS.md:753-756`, citing `00_INVENTORY.md:283`, `:287-294`).
The architectural rationale for that separation is recorded in code: election is a separate
endpoint from clustering specifically so weights can be retuned without re-paying for LLM
adjudication (`api/routes.py:900-903`).

**A deterministic stage is not automatically re-runnable for free at the pipeline level.** As
the Enrichment pipeline is currently exported, `Lookup1` re-selects all rows
(`CONTEXT-EXTERNAL.md:106`), so a re-run re-enriches every row and re-incurs the *paid* Phase 1
cost — the merge-back is not idempotent in cost terms (`02_ARCHITECTURE.md:423`). The
`enriched_at` watermark that would fix this is planned, not present
(`CONTEXT-EXTERNAL.md:194-197`).

### c.2.2 Free but networked (non-deterministic, unpriced)

| Service | Repository's claim | Cited |
|---|---|---|
| ROR v2 | "Low (free public API)" | `README.md:85` |
| GLEIF / LEI | "Low (free public API)"; "the free GLEIF API" | `README.md:86`; `enrichment/tier1_lei.py:4-5` |
| DuckDuckGo | "free fallback when no SerpAPI key"; "no API key required" | `search/duckduckgo_client.py:1`, `:17` |
| Arbitrary web hosts (page fetch) | no charge to this system beyond egress | `06_EXTERNAL_DEPS.md:672` |

These cost nothing but are **not deterministic**: a re-run may return different results, and
none of the three publishes an enforceable free-tier bound inside this repository. Their
"free" status is a repository assertion, not a cited term of use
(`06_EXTERNAL_DEPS.md:665-668`).

### c.2.3 Paid

| Cost driver | Unit | Where it is incurred |
|---|---|---|
| **SerpAPI** | one search | six Phase 1 stages (§c.3) |
| **Azure OpenAI — Phase 1** | prompt + completion tokens on `AZURE_OPENAI_DEPLOYMENT` | ten Phase 1 stages (§c.3) |
| **Azure OpenAI — Phase 2** | prompt + completion tokens on `AOAI_DEPLOYMENT_DEDUP` | dedup adjudication (§c.4) |
| **Azure Functions compute** | plan-dependent | every request; plan unknown (`CONTEXT-EXTERNAL.md:446` open item 6) |
| **Azure egress** | GB | page fetches to arbitrary hosts (`06_EXTERNAL_DEPS.md:672`) |
| **Azure Data Factory** | activity runs + integration-runtime hours | `Lookup2` + N/50 × (`Lookup1` + `Web1` + `Merge Back`) per enrichment run (`CONTEXT-EXTERNAL.md:43-186`) — outside this repository, Tillit tenant |
| **Azure SQL Managed Instance / DATAshaper** | licence and instance cost | outside this repository |

## c.3 · Phase 1 call volume — the structural bounds

Call *sites* are known exactly; call *counts* per record depend on which tier resolves the
record and on cache hits. The table below is transcribed from the call-site inventory in Pass 6
(`06_EXTERNAL_DEPS.md:679-700`), which cites each site directly.

| Stage | SERP calls | LLM calls | Page fetches | Site |
|---|---|---|---|---|
| Overflow check (UC 0) | — | 1 | — | `enrichment/orchestrator.py:1724` |
| Plain-name person classification | — | 1 **per suspicious plain-name candidate** across Name 1–4 | — | `enrichment/orchestrator.py:1763`; loop `enrichment/preprocess.py:2319-2324` |
| Tier 1 ROR | — | — | 1–4 HTTP (free) | `enrichment/tier1_ror.py:620`, `:708`, `:730`, `:740` |
| Tier 1 GLEIF | — | — | 1–7 HTTP (free) | `enrichment/tier1_lei.py:268`, `:328`, `:340-352` |
| Company canonicalisation | — | 1 | — | `enrichment/orchestrator.py:2164` |
| Lab resolver (UC 13) | 1 | ≤3 | ≤3 | `enrichment/lab_resolver.py:83`, `:118` |
| Tier 2 canonicalisation | — | 1 | — | `enrichment/orchestrator.py:2384`, `:2508` |
| Tier 2A (contact) | 1 **per query** | ≤3 | ≤3 | `enrichment/tier2a_contact.py:330`, `:110`, `:142` |
| Tier 2B (department) | 1 **per query** | ≤3 | ≤3 | `enrichment/tier2b_dept.py:227`, `:89`, `:97` |
| Tier 3 | — | 1 | — | `enrichment/orchestrator.py:2543` |
| Website Path B | 1, +1 on the unquoted retry | — | — | `enrichment/website_resolver.py:492`, `:522-529` |
| Website Path C | — | 1 | — | `enrichment/orchestrator.py:907` → `website_resolver.py:598` |
| Department-domain probe | 1, **+1 only when `DEPT_PROBE_CROSS_DOMAIN`** | — | 1 homepage + subdomain HEAD probes | `enrichment/orchestrator.py:1131`, `:1109-1115`, `:1182`, `:1277`, `:1296` |
| Person affiliation (Stage 2b) | ≤1 **per query variant** | 1 (+1 free ROR confirm) | — | `enrichment/person_affiliation.py:124`, `:148`; `orchestrator.py:1455` |
| Address residual classification | — | 1 **per non-empty secondary street slot** (`street_2`…`street_5`, so ≤4) | — | `enrichment/address_processing.py:718-724` |

Two observations that bear directly on the formula:

- **The tiers are an escalation ladder, not a sum.** A record resolved by ROR at Tier 1 never
  reaches Tier 2A, 2B, or 3. Adding the column is therefore a ceiling that no single record
  attains, and the mean is the quantity that matters. ⚠ MEASUREMENT REQUIRED.
- **Three counts in the table are unbounded by any constant in the repository**: SERP calls "per
  query" in Tier 2A and Tier 2B, and "per query variant" in person affiliation. The number of
  queries is determined at runtime by the query-construction code, not by a configured cap.

**Two configuration values move the total materially, and both are set inconsistently:**

| Parameter | Code default | `.env.example` | Effect |
|---|---|---|---|
| `DEPT_PROBE_CROSS_DOMAIN` | `"false"` (`config.py:114`) | `true` (`.env.example:61`) | doubles the SERP calls of the department probe for unresolved departments (`04_PARAMETERS.md:289-305`) |
| `MAX_PAGE_CONTENT_CHARS` | effective `1500` (`config.py:209`) | `3000` (`.env.example:81`) | doubles the page slice pasted into every Tier 2A / 2B / lab-resolver prompt, i.e. doubles those prompt-token counts (`04_PARAMETERS.md:266-287`) |

`config.py:93` lists `MAX_PAGE_CONTENT_CHARS` as `"3000"` in `OPTIONAL_VARS_WITH_DEFAULTS`
while the dataclass field at `config.py:209` defaults to `"1500"` — the dataclass is what
executes. Both divergences are cost-bearing and both are recorded in
`04_PARAMETERS.md`.

## c.4 · Phase 2 call volume — the one stage the code already measures

Per block `b` with `n_b` input rows collapsing to `g_b` distinct signatures after the free
STEP A pass:

| Regime | Condition | LLM calls |
|---|---|---|
| Mode A | `g_b ≤ SIG_PARTITION_THRESHOLD` (default `12`, `dedup/adjudicator.py:36`; `.env.example:37`) | ≤ 2 — one partition call per `has_name2` bucket that holds ≥2 signatures; a singleton bucket becomes an entity with no call (`dedup/adjudicator.py:286-298`, `:315`) |
| Mode B | `g_b > SIG_PARTITION_THRESHOLD` | ≤ `g_b − 1` — one incremental assignment call per signature after the first; a signature with no `has_name2`-compatible canonical starts a new entity with **no** call (`dedup/adjudicator.py:416-428`, `:453`) |
| Residue | both modes | one call per nominated candidate pair, skipping pairs already merged transitively (`dedup/adjudicator.py:621-639`) |

The residue term has a hard cap with an important cost property: when
`len(candidates) > MAX_CANDIDATES_PER_BLOCK` (default `50`, `dedup/adjudicator.py:40`), the
whole block is routed to `manual_review` and the function **returns before making any candidate
call** (`dedup/adjudicator.py:585-601`). Exceeding the cap therefore costs *zero* extra tokens,
not `50`. So `c_b ≤ 50`, and `c_b = 0` whenever the cap is exceeded.

**This is the only stage in the system whose token cost is already instrumented.**
`DedupLLMResult` carries `prompt_tokens`, `completion_tokens`, `latency_ms`, and `model_version`
(`dedup/llm.py:188-195`); they are accumulated per block (`dedup/adjudicator.py:803-809`),
logged per call as `dedup_llm_call` (`:812-824`), per block as `dedup_block` (`:883-899`), and
per request as `dedup_request` with `total_prompt_tokens`, `total_completion_tokens`, and
`total_tokens` (`:995-1011`). Note two limits: the totals are **log-only** — they are not
returned in `DedupResponse` (`dedup/adjudicator.py:1014`) — and, as §b.3 establishes, they are
dropped by the console/file formatter, so they are readable **only** in Application Insights,
and only when not sampled away (§b.6).

## c.5 · The cost formula for a full run over N records

### c.5.1 Symbols

*Volumes (from the repository):*

| Symbol | Meaning | Source |
|---|---|---|
| `N` | records in the run = `COUNT(*) FROM test_77.Legacy` for the group code | ⚠ MEASUREMENT REQUIRED — `02_ARCHITECTURE.md:491-493` |
| `B` | enrichment batches = `⌈N / 50⌉` | `FETCH NEXT 50 ROWS ONLY` (`CONTEXT-EXTERNAL.md:106`) |
| `M` | rows entering deduplication = `COUNT(*) FROM test_77.Validation` | ⚠ MEASUREMENT REQUIRED — `02_ARCHITECTURE.md:491-493` |
| `K` | distinct `block_id` values over those `M` rows | ⚠ MEASUREMENT REQUIRED |
| `s_i` | SERP calls for record `i` | §c.3 — bounded by call sites, unbounded per query |
| `t^{in}_i`, `t^{out}_i` | Phase 1 prompt / completion tokens for record `i` | **not captured** — `llm/openai_client.py:198-208` |
| `L_b` | Phase 2 LLM calls for block `b` | §c.4 |
| `τ^{in}_b`, `τ^{out}_b` | Phase 2 prompt / completion tokens for block `b` | **captured** — `dedup/adjudicator.py:995-1011` |

*Unit prices (all ⚠ MEASUREMENT REQUIRED — none appears in this repository):*

| Symbol | Meaning | Where the number lives |
|---|---|---|
| `p_serp` | price per SerpAPI search | the SerpAPI account plan and usage dashboard for the key in `SERPAPI_KEY` (`config.py:160`) — neither plan, allowance, nor price is recorded here (`06_EXTERNAL_DEPS.md:669`) |
| `p^{in}_1`, `p^{out}_1` | price per 1k prompt / completion tokens on `AZURE_OPENAI_DEPLOYMENT` | Azure pricing for the deployment at `AZURE_OPENAI_ENDPOINT` (`config.py:156-157`) |
| `p^{in}_2`, `p^{out}_2` | price per 1k prompt / completion tokens on `AOAI_DEPLOYMENT_DEDUP` | as above for `.env.example:28` |
| `p_fn` | Function App compute price per unit | plan unknown — `CONTEXT-EXTERNAL.md:446` open item 6 |
| `p_adf` | ADF activity-run and IR price | Tillit tenant, outside this repository |
| `p_egress` | Azure egress price per GB | Azure Cost Management for the Function App |

### c.5.2 The formula

```
C_total(N)  =  C_enrich(N)  +  C_issues(N)  +  C_dedup(M)  +  C_score(M)
                            +  C_compute    +  C_egress    +  C_orchestration

C_issues(N)  =  0                       (free, deterministic — §c.2.1)
C_score(M)   =  0                       (free, deterministic — §c.2.1)
C_approve    =  0                       (free, deterministic — §c.2.1)

                 N
C_enrich(N)  =   Σ  [ s_i · p_serp  +  (t^in_i / 1000) · p^in_1  +  (t^out_i / 1000) · p^out_1 ]
                i=1

              =  N · [ s̄ · p_serp  +  (t̄^in / 1000) · p^in_1  +  (t̄^out / 1000) · p^out_1 ]

                 K
C_dedup(M)   =   Σ  [ (τ^in_b / 1000) · p^in_2  +  (τ^out_b / 1000) · p^out_2 ]
                b=1

              =  (T^in / 1000) · p^in_2  +  (T^out / 1000) · p^out_2
                 where T^in, T^out are the request totals already logged as
                 total_prompt_tokens / total_completion_tokens
                 (dedup/adjudicator.py:1000-1002)

C_compute    =  p_fn · (execution units over B enrichment requests + ⌈K/…⌉ dedup requests)
                                                             ⚠ plan unknown

C_egress     =  p_egress · (bytes fetched from arbitrary hosts, bounded per page by
                            MAX_PAGE_CONTENT_CHARS only after fetch, not before)

C_orchestration = p_adf · (1 Lookup2 + B · (Lookup1 + Web1 + Merge Back)
                           + dedup: 1 Lookup1 + 1 Web1 + 1 Merge Back)
                                       (CONTEXT-EXTERNAL.md:43-186, :205-303)
```

`C_enrich` is **linear in N** with a constant of proportionality that the repository cannot
supply. `C_dedup` is **not** linear in `M`: it is driven by `K` and by the signature
distribution within each block, and Mode B makes it super-linear in `g_b` for large blocks
(§c.4). `C_issues`, `C_score`, and `C_approve` are exactly zero and stay zero on every re-run —
which is the practical payoff of the boundary rationale recorded at `api/routes.py:900-903`.

### c.5.3 The per-record means, and how to obtain them

Reducing `C_enrich` to `N · (…)` requires three means. None exists; each has a precise remedy.

| Quantity | Status | ⚠ MEASUREMENT REQUIRED — how to obtain it |
|---|---|---|
| `s̄` (mean SERP calls per record) | not captured | instrument `SerpAPIClient.search` (`search/serpapi_client.py:22`) with a counter, or read the SerpAPI dashboard's search count for a run of known `N`. `BatchCache.stats` (`utils/cache.py:109-111`) already exposes `serp_entries` but is never logged — logging it at the end of `enrich_batch` (`enrichment/orchestrator.py:838`) gives distinct-query volume per batch for free |
| `t̄^in`, `t̄^out` (mean Phase 1 tokens per record) | **not captured at all** | (a) record `response.usage` in `call_openai` (`llm/openai_client.py:198-208`), mirroring `dedup/llm.py:188-195` — a two-line change; or (b) read Azure Monitor / Cost Management metrics for the `AZURE_OPENAI_DEPLOYMENT` deployment over a run of known `N` |
| `T^in`, `T^out` (Phase 2 tokens) | **already captured** | read `total_prompt_tokens` / `total_completion_tokens` from the `dedup_request` App Insights trace (`dedup/adjudicator.py:1000-1002`); note §b.3 — these are not in the log file |
| `N`, `M`, `K` | not in the repository | `SELECT COUNT(*) FROM test_77.Legacy`; `SELECT COUNT(*) FROM test_77.Validation`; `SELECT COUNT(DISTINCT [Block ID]) FROM test_77.Validation` |
| Function App compute | plan unknown | confirm the hosting plan (`CONTEXT-EXTERNAL.md:446` open item 6), then Azure Cost Management for `mdm-pipeline-api` |
| Per-batch duration (drives compute cost) | unmeasured | `batch_ms` is already logged (`enrichment/orchestrator.py:838-841`); read it from one 50-row run — `CONTEXT-EXTERNAL.md:447` open item 7 |

**A single instrumented run over a known `N` settles every Phase 1 unknown at once**, provided
`response.usage` is captured first. That is the smallest experiment that makes the formula
numeric.

## c.6 · Cost controls present in the code

The repository does contain deliberate spend controls, and they are worth recording as such:

| Control | Value | Effect | Cited |
|---|---|---|---|
| Tier escalation ordering | — | cheapest, most reliable method first; escalate only on failure | `README.md:80` |
| `BatchCache` | per-batch, ROR + SERP | de-duplicates repeated ROR and SERP queries within one 50-row batch | `utils/cache.py:101-111` |
| `DEPT_PROBE_CROSS_DOMAIN=false` | code default | caps the department probe at one SERP call per record — "the common case stays at one SERP call per record" | `config.py:161-168` |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | over the cap the block is routed to manual review **with zero LLM calls** | `dedup/adjudicator.py:40`, `:585-601` |
| `SIG_PARTITION_THRESHOLD` | `12` | small blocks use ≤2 calls instead of `g_b − 1` | `dedup/adjudicator.py:36` |
| Transitive-merge skip | — | a candidate pair already unified is not re-asked | `dedup/adjudicator.py:624-625` |
| `has_name2` deterministic split | — | the empty-vs-populated Name 2 decision is never sent to the LLM | `dedup/adjudicator.py:278-281`, `:422-428` |
| Election separated from clustering | — | weights can be retuned without re-paying for adjudication | `api/routes.py:900-903` |
| `MOCK_EXTERNAL_CALLS` | `false` | substitutes mocks for ROR, search, page fetch, LLM — **but not GLEIF** | `api/routes.py:57-70`; gap at `06_EXTERNAL_DEPS.md:757-763` |

**Two cost exposures run the other way:**

1. **`GET /diag/dedup-llm` makes a real, billable LLM call on every request**
   (`api/routes.py:1085-1089`) and is reachable **without authentication** (§d.4). So is
   `GET /diag/llm` (`api/routes.py:1051-1055`). Both are GET requests, so any crawler,
   link-preview fetcher, or scanner that reaches the public hostname spends Azure OpenAI
   tokens. There is no rate limit anywhere in the application.
2. **Re-running the Enrichment pipeline re-pays for every row.** `Lookup1` selects all rows
   with no watermark (`CONTEXT-EXTERNAL.md:106`), so a re-run is a full re-spend, and the
   non-deterministic tiers may return different answers on the second pass
   (`02_ARCHITECTURE.md:423`).

---

# (d) Security and Compliance

## d.1 · Secret handling

### d.1.1 What the secrets are, and where they are read

| Secret | Read at | Consumed by |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | `config.py:155`; `llm/openai_client.py:147` | `AsyncAzureOpenAI(api_key=…)` — `llm/openai_client.py:170` |
| `AZURE_OPENAI_ENDPOINT` | `config.py:156`; `llm/openai_client.py:148` | `AsyncAzureOpenAI(azure_endpoint=…)` — `llm/openai_client.py:171` |
| `SERPAPI_KEY` | `config.py:160` | `GoogleSearch({"api_key": …})` — `search/serpapi_client.py:20`, `:42` |

Every secret is read from the process environment. No secret is hard-coded in tracked source:
`AZURE_OPENAI_API_KEY` and `SERPAPI_KEY` appear in tracked files only as `os.getenv` lookups
and as placeholders in `.env.example:2` (`your-azure-key-here`) and `.env.example:56`
(`your-serpapi-key-here`).

ROR, GLEIF, DuckDuckGo, and page fetch are unauthenticated public endpoints — no credential
exists for them (`06_EXTERNAL_DEPS.md:32-51`).

### d.1.2 Delivery

| Environment | Mechanism | Evidence |
|---|---|---|
| Local development | `.env`, loaded by `load_dotenv()` unconditionally at import | `config.py:17-22` |
| Local Functions host | `local.settings.json` `Values` block | `local.settings.json:3-25` |
| Deployed Function App | Azure Application Settings | `config.py:1-5`; `README.md:1836`, `:1639` |

Both local files are excluded from version control (`.gitignore:9` `.env`; `.gitignore:23`
`local.settings.json`) **and** from the deployment package (`.funcignore:16` `.env`;
`.funcignore:15` `local.settings.json`). Corporate CA bundles are excluded too
(`.gitignore:10-12`: `certs/`, `*.pem`).

**The exclusion has held.** `git log --all --oneline --name-only --diff-filter=A -- .env
local.settings.json certs` returns nothing: neither file has ever been added on any ref, so no
secret needs rotating on account of git history.

**But the working tree holds live credentials in plaintext.** `local.settings.json:8` and
`:12` contain what are, by their form and length, real Azure OpenAI and SerpAPI keys rather
than placeholders, alongside the live endpoint at `:9` and deployment name at `:10`. They are
correctly ignored and correctly func-ignored, so this is a workstation-hygiene exposure, not a
repository one — but it is an unencrypted secret at rest on a developer machine, and
`.env` (untracked, present) is the same situation.

### d.1.3 What is not used

**No Azure Key Vault, no managed identity, no `azure-identity`.** A repository-wide search
(`keyvault|key_vault|DefaultAzureCredential|ManagedIdentity|azure.identity`, case-insensitive,
excluding `.venv/` and `docs/`) returns **no matches**. `requirements.txt:1-14` declares no
identity package. Authentication to Azure OpenAI is therefore a long-lived API key
(`llm/openai_client.py:170`) rather than a workload identity, with no rotation mechanism, no
expiry, and no per-caller attribution — despite the Function App and the AI Foundry deployment
sitting on the same Bruker spoke (`CONTEXT-EXTERNAL.md:406-407`), which is the topology in
which managed identity is available.

### d.1.4 Secret leakage surfaces

| Surface | Leaks? | Detail |
|---|---|---|
| Log lines | **no key value** | `config.py:137-145` logs only whether `SERPAPI_KEY` is set; `config.py:128-135` logs only the *names* of missing variables |
| `GET /diag/llm` response | **metadata, unauthenticated** | returns `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY_present`, and `AZURE_OPENAI_API_KEY_length` (`api/routes.py:1043-1048`). The value is never returned; **the length is**, which narrows a brute-force space and confirms which credential form is in use |
| `GET /diag/dedup-llm` response | **metadata, unauthenticated** | returns endpoint, `AOAI_DEPLOYMENT_DEDUP`, `AOAI_API_VERSION_DEDUP`, `DEDUP_REASONING_EFFORT`, key presence (`api/routes.py:1076-1082`), plus `llm._api_version` and `llm._use_reasoning_effort` (`:1097-1098`) |
| `GET /diag/*` on error | **provider error strings** | returns `error_type` and `error_message` verbatim from the exception (`api/routes.py:1058-1062`) — by design, "the actual exception string is returned in the HTTP response body" (`:1038-1039`) |
| `GET /tiers` | **configuration** | thresholds and the active search provider (`api/routes.py:1110-1117`) |
| `500` handler | no | returns a fixed `{"detail":"Internal server error"}` body; the traceback goes to the log, not the response (`api/middleware.py:41-55`) |

The three GET endpoints together disclose the Azure OpenAI resource hostname, both deployment
names, the API version, the reasoning-effort setting, the credential length, and the running
thresholds — to any unauthenticated caller (§d.4).

### d.1.5 The TLS escape hatch

`resolve_tls_verify()` (`llm/openai_client.py:93-127`) resolves the outbound `verify` setting
in three steps: `LLM_SSL_VERIFY=false` disables certificate verification entirely; otherwise a
configured CA bundle (`AZURE_OPENAI_CA_BUNDLE` / `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`) is
used if the file exists; otherwise certifi.

Step 1 is a deliberate, documented insecure mode. It is guarded by a loud warning —
"TLS certificate verification is DISABLED for LLM calls. This is insecure"
(`llm/openai_client.py:111-115`) — and `.env.example:16-17` labels it "Last resort only
(insecure — disables certificate verification for LLM calls)". Two properties matter for
compliance: **it is env-var controlled**, so it can be enabled in the deployed Function App
through an Application Setting with no code change and no review; and **the only trace it
leaves is a `WARNING` log line**, which is not surfaced by `/health` (§b.5) or `/tiers`
(`api/routes.py:1110-1117`), so an operator cannot tell from outside whether a running
instance is verifying certificates on the path that carries personal data to Azure OpenAI
(§d.6).

A related, milder mechanism runs at import: `_sanitize_ssl_env()` (`config.py:27-67`) rewrites
`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` when they point at a non-existent path, substituting the
corp bundle if configured and certifi otherwise, and logs a warning (`config.py:61-64`). This
*restores* verification rather than removing it.

## d.2 · Tenant boundaries between Tillit and Bruker

Component placement, from `02_ARCHITECTURE.md:384-391`, itself sourced from
`CONTEXT-EXTERNAL.md:405-408` `[AUTHOR]`:

| Component | Tenant | Evidence |
|---|---|---|
| DATAshaper (SaaS) | **Tillit** | `CONTEXT-EXTERNAL.md:405-406`; SaaS with no file export (`:337-339`) |
| Azure Data Factory | **Tillit** | `CONTEXT-EXTERNAL.md:405-406` |
| Azure SQL Managed Instance (`test_77.Legacy`, `test_77.Validation`) | ⚠ **not stated** | reached by ADF linked services `ls_sqlmi_legacy` / `ls_sqlmi_validation` (`CONTEXT-EXTERNAL.md:172`, `:292`), which places it Tillit-side on the network, but the hosting tenant is not recorded (`02_ARCHITECTURE.md:407-409`) |
| Function App `mdm-pipeline-api` | **Bruker** spoke | `CONTEXT-EXTERNAL.md:405-408` |
| AI Foundry / Azure OpenAI | **Bruker** spoke | `CONTEXT-EXTERNAL.md:406-407`; endpoint from `AZURE_OPENAI_ENDPOINT` (`config.py:156`) |
| Application Insights | Azure — tenant ⚠ not stated | `host.json:3-10`; instance named only in prose (`README.md:1836`) |

**Bruker's customer master data crosses the tenant boundary on every batch.** The flow is:
Tillit-side SQL MI → ADF (Tillit) → HTTPS POST → Function App (Bruker) → Azure OpenAI (Bruker)
and SerpAPI (third party, public internet) → HTTPS response → ADF (Tillit) → stored procedure →
Tillit-side SQL MI.

That crossing carries personal data. `05_DATA_MODEL.md:1060-1069` enumerates it: `Contact`,
`Email`, `Care Of`, `Name 1`–`Name 4` (which can hold a natural person — UC 7 extracts one out
of Name 1 into Contact), `Created By` (an SAP user id), and the full postal address. There is
no field-level filter, minimisation, or pseudonymisation at the boundary: the ADF `Lookup1`
issues `SELECT * FROM test_77.Legacy` (`CONTEXT-EXTERNAL.md:106`), so **every column of every
row** is posted to the Bruker-side service, whether the enrichment needs it or not.

**Controls at the boundary — what exists:**

- Transport is HTTPS to `https://mdm-pipeline-api.azurewebsites.net`
  (`CONTEXT-EXTERNAL.md:135`, `:255`).
- The service is stateless with respect to record data: it holds no database, and the only
  persistence it performs is the rotating log file (`api/middleware.py:105-107`).

**Controls at the boundary — what does not exist:**

- No authentication (§d.4).
- No IP allow-list, private endpoint, or VNet integration evidenced (§d.3).
- No field-level minimisation (above).
- No data-processing agreement, transfer record, or DPIA artefact in the repository —
  ⚠ NOT EVIDENCED.
- No group-code scoping on the current pipeline: `Lookup1` and `Lookup2` have no group-code
  predicate as exported (`CONTEXT-EXTERNAL.md:64`, `:106`), so a run over `test_77.Legacy`
  spans **all imports under the entity**, not just the intended one. Adding that predicate is a
  planned pre-freeze change (`CONTEXT-EXTERNAL.md:194-197`). Until then, the blast radius of a
  run is the whole entity.

## d.3 · Network path from ADF to the Function App

Read from the `[EXPORT]` pipeline JSON (`CONTEXT-EXTERNAL.md:119-145` for enrichment,
`:238-265` for deduplication):

| Property | Value | Line |
|---|---|---|
| Activity type | `WebActivity` | `CONTEXT-EXTERNAL.md:120`, `:240` |
| Method | `POST` | `:133`, `:253` |
| Headers | `{"Content-Type": "application/json"}` — **and nothing else** | `:134`, `:254` |
| URL | `https://mdm-pipeline-api.azurewebsites.net/enrich` and `…/api/dedup/cluster-block` | `:135`, `:255` |
| Integration runtime | `AutoResolveIntegrationRuntime` | `:136-139`, `:256-259` |
| Authentication | **absent from the activity definition** | `:132-144`, `:252-264` |
| Timeout | `0.12:00:00` (12 hours) | `:126`, `:246` |
| Retry | `0` | `:127`, `:247` |
| `secureInput` / `secureOutput` | `false` / `false` | `:129-130`, `:249-250` |

**The path is the public internet.** `AutoResolveIntegrationRuntime` is the Azure-managed,
multi-tenant runtime: it egresses from Microsoft-owned address space in an auto-selected
region, not from a customer network. There is no self-hosted integration runtime, no managed
private endpoint, and no VNet-integrated runtime in either export. On the receiving side, the
Function App is addressed by its default `*.azurewebsites.net` hostname, which is
internet-facing by default. Whether inbound access restrictions (an IP allow-list, a private
endpoint, or `WEBSITE_*` networking settings) are configured on the Azure resource is
⚠ NOT EVIDENCED — no such artefact exists in the repository, and none would; it is resource
configuration. **It is the single most consequential unknown in this section**, because it is
the only thing that could compensate for §d.4.

Two further properties of the path bear recording:

- **`secureInput: false` and `secureOutput: false`** mean ADF records the activity's input and
  output — the full 50-record request body and the full enriched response, both containing
  personal data — in ADF's own monitoring store in cleartext, retained under ADF's retention
  policy, and visible to anyone with ADF monitoring access on the Tillit tenant.
  Setting both to `true` is the ADF-side control that suppresses this; it is not set.
- **`retry: 0` with `isSequential: true`** (`CONTEXT-EXTERNAL.md:88`) means one transient
  failure stops the enrichment run at that offset and no later offsets are processed
  (`06_EXTERNAL_DEPS.md:766-773`). Because the service is fail-open and returns 200 with
  degraded content on any external outage (`06_EXTERNAL_DEPS.md:748-753`), the ADF failure path
  does **not** trip on quality degradation — only on transport failure. There is no quality gate
  between the two.

## d.4 · Authentication on the service endpoints

**There is none.**

`function_app.py:12`:

```python
azure_app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
```

`function_app.py:15-19` registers one catch-all route `{*route}` that forwards everything to
the FastAPI app, and `host.json:13` sets `routePrefix` to `""`. Consequently **all 13 routes
share the single `ANONYMOUS` auth level** — the README says so explicitly and names the
remedy: "all endpoints — including `POST /api/dedup/cluster-block` — share the same auth level
(`ANONYMOUS` here; switch to `FUNCTION` and supply a function key to require one). There is no
per-route auth in application code" (`README.md:1825`).

Confirmed independently at the application layer: a search of `api/` for
`Depends(|HTTPBearer|Security(|verify_token|authenticat|x-functions-key|api[_-]?key.*header`
returns **no matches**. `api/routes.py` declares no security dependency on any of its 13
route decorators (`api/routes.py:75`, `:88`, `:518`, `:580`, `:628`, `:802`, `:832`, `:896`,
`:946`, `:977`, `:1034`, `:1066`, `:1105`).

What is reachable without a credential by anyone who knows the hostname:

| Route | Effect of an anonymous call |
|---|---|
| `POST /enrich` | submits up to 50 arbitrary records for enrichment; spends SERP and LLM budget; returns enriched data |
| `POST /enrich/file`, `/issues`, `/issues/compare`, `/api/dedup/file`, `/api/dedup/score/file` | accept arbitrary XLSX uploads |
| `POST /api/dedup/cluster-block` | spends adjudicator LLM budget |
| `POST /api/dedup/approve` | **records an approval decision under any `approver` string** (§d.5) |
| `GET /diag/llm`, `GET /diag/dedup-llm` | spend LLM budget per call and disclose configuration (§d.1.4, §c.6) |
| `GET /tiers`, `GET /health` | disclose configuration |

There is additionally **no CORS policy, no rate limit, no request-size limit, and no
input-size cap** in the application: `api/app.py:17-29` adds exactly one middleware
(`RequestLoggingMiddleware`) and the router. `EnrichmentRequest` places no maximum on
`records` — the 50-row batching is an ADF convention (`CONTEXT-EXTERNAL.md:106`), not a service
constraint.

**Enforced in code:** nothing. **Enforced by platform configuration:** unknown — an Azure-side
IP restriction would change this picture entirely, and is ⚠ NOT EVIDENCED (§d.3). **The
remedy is one enum:** `AuthLevel.ANONYMOUS` → `AuthLevel.FUNCTION` at `function_app.py:12`,
plus the function key on the ADF Web activity headers.

## d.5 · The four-eyes approval control on merges

The request names "merges", which in this system denotes two distinct things. Both are
documented, because the control has a different status in each.

### d.5.1 Record merges — the duplicate-merge approval gate

This is the four-eyes control the system was designed around: *the machine proposes, a human
confirms* (`CONTEXT-EXTERNAL.md:398-399`; `02_ARCHITECTURE.md:366-372`).

**Enforced in code:**

| Control | Mechanism | Cited |
|---|---|---|
| Election never auto-commits | every election is a proposal; the winner's `row_id` goes to `proposed_golden_id`, not to the golden fields | `dedup/scoring.py:1046-1047`, `:1100-1119` |
| Unreviewed rows cannot be acted on by accident | a `manual_review` row leaves `is_golden_record` and `golden_record_id` **empty**, so nothing keyed on `is_golden_record` alone can touch it | `dedup/scoring.py:262-264` |
| Low-confidence merges are demoted | a merge whose adjudication confidence is below `CONFIDENCE_MERGE_THRESHOLD` (`0.95`) enters election as `manual_review` | `config.py:100`, `:223-225`; `dedup/scoring.py:1100-1119` |
| Blocked and uncertain clusters are demoted | `manual_review` when clustering flagged uncertainty or every member is blocked | `dedup/scoring.py:1100-1119` |
| Candidate-cap blow-outs are demoted | over `MAX_CANDIDATES_PER_BLOCK` the **whole block** routes to manual review | `dedup/adjudicator.py:585-601` |
| Promotion happens only on an explicit approval | `apply_approval` promotes `proposed_golden_id` into the golden fields **only** when `decision == "approved"`; on `"rejected"` the golden fields are untouched | `dedup/scoring.py:597-600`, `:584` |
| Approval is a separate endpoint from election | `POST /api/dedup/approve` ≠ `POST /api/dedup/score` — a distinct call, distinct input, distinct handler | `api/routes.py:946-947` vs `:896-897` |
| The downstream contract is explicit | Phase 3 consumes **only** `approval_status == "approved"` or `election_status == "unique"` | `api/routes.py:954-955`; `dedup/scoring.py:266-268`; `README.md:1103` |
| An approver must be named | `approver: str = Field(..., min_length=1)` — a non-empty string is required to submit | `dedup/scoring.py:560` |

**Not enforced in code — enforced by process, or not at all:**

| Gap | Detail | Cited |
|---|---|---|
| The approver is not authenticated | the endpoint is `ANONYMOUS` (§d.4); `approver` is a free-text string with no identity backing it. Any caller can post `approver: "anyone"` | `function_app.py:12`; `dedup/scoring.py:560` |
| The approver is **not used by the logic at all** | `apply_approval(rows, cluster_id, decision)` does not take `approver` as a parameter. It is read in the route only to be logged (`api/routes.py:957-960`) and echoed in the response (`:971`). It is written to **no row field** | `dedup/scoring.py:574-578`; `api/routes.py:962-964` |
| **Separation of duties is not checked** | nothing compares the approver against the person who ran the election, imported the group code, or approved the sibling cluster. There is no proposer/approver distinction in code — the "four eyes" are two roles that the code cannot tell apart | absence across `dedup/scoring.py:574-603` |
| No approval is persisted | the endpoint is stateless: the caller submits the rows, the decision is applied in memory, the rows are echoed back. "Persistence is intentionally out of scope — a durable approval store is a future step" | `api/routes.py:950-955`; `dedup/scoring.py:553-555` |
| Therefore no audit trail exists | the only record that an approval happened is one `INFO` log line (`api/routes.py:957-960`) with cluster, decision, approver, and row count — in a file rotated at 10 MB × 5 backups (`api/middleware.py:105-107`) and in App Insights subject to sampling (§b.6). There is no immutable, queryable approval record | as cited |
| The decision can be replayed or forged wholesale | because the caller supplies **both** the decision and the rows it applies to, a caller can submit any row set with `decision="approved"` and receive promoted golden fields back | `dedup/scoring.py:574-603` |

**Where the control actually lives: in DATAshaper, by process.** The human step is the
DS deduplication view's `Leading Code` selector and `Apply Leading Code` action
(`CONTEXT-EXTERNAL.md:389-399` `[OBSERVED]`), which is where a steward reviews the proposed
cluster and the adjudicator's free-text `Reason`. `/api/dedup/approve` is the API counterpart of
that button (`02_ARCHITECTURE.md:357-364`). Whatever identity, authorisation, and audit exist
for the approval are **DATAshaper's**, in the Tillit tenant, and are not repository artefacts —
⚠ NOT EVIDENCED. Whether ADF even invokes the scoring and approval endpoints is open item 5
(`CONTEXT-EXTERNAL.md:445`): neither exported pipeline calls `/api/dedup/score` or
`/api/dedup/approve` (`02_ARCHITECTURE.md:374-378`).

**Verdict.** The *decision structure* is enforced in code and enforced well: nothing is
auto-committed, unreviewed rows are structurally inert, and the downstream consumption contract
is explicit. The *four-eyes property itself* — that a second, distinct, identified human
approved — is enforced nowhere in this repository. It rests entirely on the DATAshaper UI and
on process.

### d.5.2 Code merges — pull-request review

Read literally as source-control merges, the answer is that no such control is evidenced:

| Control | State | Evidence |
|---|---|---|
| Merge commits | **zero**, across all refs | `git log --all --merges --oneline \| wc -l` → `0`, over 51 commits (`git rev-list --count --all`) |
| Pull-request template | absent | `git ls-files \| grep -icE "codeowners\|pull_request\|contributing\|SECURITY"` → `0` |
| `CODEOWNERS` | absent | as above |
| Required status checks | impossible — no CI exists to be required | §a.1 |
| Committer identities | two: `Suzu <spoorvaaajay@gmail.com>` (27 commits) and `Ajay <Apoorva.Ajay@bruker.com>` (24) | `git shortlog -sne --all` |
| Commit signing | not evidenced | no `.gitattributes`, no signing configuration tracked |
| Branch state | `HEAD` = `diag/website-trace` is one commit ahead of `main` | `git log --oneline main -3` |

Every commit reached its branch directly. Whether GitHub-side branch protection requires review
on `main` is account configuration, ⚠ NOT EVIDENCED — but the absence of any merge commit in
51 commits shows that no reviewed pull request has been merged in this repository's history.
Combined with §a.5 (deployment is a manual VS Code action from a workstation), **there is no
point in the path from a code change to production at which a second person is required.**

## d.6 · Compliance: personal data, redaction, and retention

Pass 5 established the data-protection position in detail (`05_DATA_MODEL.md:1056-1128`). The
compliance-relevant consequences:

**No redaction exists anywhere.** A repository-wide search for
`redact|mask|anonymi|pii|GDPR` returns no logging filter, formatter, or scrubber; the only
`mask` hits are an unrelated text heuristic (`05_DATA_MODEL.md:1076-1079`, citing
`enrichment/preprocess.py:1963`). Log records are emitted verbatim.

**Full person names reach the logs at four or more sites** — `enrichment/person_affiliation.py:126`,
`:134`, `:152`; `enrichment/preprocess.py:2326`; `search/serpapi_client.py:35` and
`search/duckduckgo_client.py:28` (the query embeds the quoted name); plus Tier 3 identity-guard
rejections (`enrichment/orchestrator.py:713-717`) and every website-resolution line
(`enrichment/website_resolver.py:474-477`, `:494`, `:505-508`, `:602-604`, `:619-621`) when
Name 1 holds a person (`05_DATA_MODEL.md:1081-1094`).

**Those logs have no retention policy.** Rotation bounds *size* — 10 MB × 5 backups
(`api/middleware.py:105-107`) — not *age*. There is no expiry and no deletion procedure
(`05_DATA_MODEL.md:1104-1105`). App Insights retention is ⚠ NOT EVIDENCED (§b.6).

**Personal data leaves the trust boundary without minimisation.** SERP providers receive the
person's full name in quotes (`enrichment/person_affiliation.py:86-89`); Azure OpenAI receives
the name, the location, and SERP snippets in the affiliation prompt (`:141-150`), and the
original name1/name2 pairs in the dedup prompts (`dedup/adjudicator.py:642-646`). Neither call
site applies minimisation or pseudonymisation (`05_DATA_MODEL.md:1116-1127`). The email address
itself is *not* sent — only its domain, and only when it is not a freemail domain
(`enrichment/person_affiliation.py:62-66`, `:83-86`) — which is the one deliberate minimisation
in the system and is worth citing as such.

**The request bodies themselves are never logged** (`api/middleware.py:28-70` logs method,
path, status, and duration only), which materially limits the exposure — the leakage is via
specific per-field log statements, not via a blanket body dump.

**No compliance artefact exists in the repository:** no data-processing agreement, no
records-of-processing entry, no DPIA, no retention schedule, no `SECURITY.md`, no
`CONTRIBUTING.md` (§d.5.2). ⚠ NOT EVIDENCED — if these exist they live outside version control.

## d.7 · Enforced in code versus enforced by process

The summary the request asks for, stated as a single table.

| Control | Enforced in code | Enforced by process / platform | Not enforced |
|---|---|---|---|
| Secrets kept out of git | ✅ `.gitignore:9,23`; verified clean across all history (§d.1.2) | | |
| Secrets kept out of the deployment package | ✅ `.funcignore:15-16` | | |
| Secrets read from environment only | ✅ `config.py:155-160`; `llm/openai_client.py:147-148` | | |
| Secret rotation / vaulting | | | ❌ no Key Vault, no managed identity (§d.1.3) |
| Secret values never logged or returned | ✅ `config.py:137-145`; key value never in a response (§d.1.4) | | ⚠ key *length* is returned (`api/routes.py:1047`) |
| TLS on outbound LLM calls | ✅ default (`llm/openai_client.py:118-127`) | | ❌ defeatable by `LLM_SSL_VERIFY=false` App Setting, no external indicator (§d.1.5) |
| TLS on the ADF→service hop | ✅ HTTPS URL (`CONTEXT-EXTERNAL.md:135`) | | |
| Authentication on service endpoints | | ⚠ unknown — Azure inbound restrictions ⚠ NOT EVIDENCED | ❌ `ANONYMOUS`, all 13 routes (`function_app.py:12`) |
| Rate limiting / request-size limits | | | ❌ none (`api/app.py:17-29`) |
| Tenant-boundary data minimisation | | | ❌ `SELECT *` crosses whole (`CONTEXT-EXTERNAL.md:106`) |
| Group-code scoping of a run | | ⚠ planned pre-freeze (`CONTEXT-EXTERNAL.md:194-197`) | ❌ absent from the exported pipelines |
| ADF activity input/output masking | | | ❌ `secureInput/secureOutput: false` (`CONTEXT-EXTERNAL.md:129-130`) |
| Nothing auto-commits a duplicate merge | ✅ `dedup/scoring.py:1046-1047` | | |
| Unreviewed rows structurally inert | ✅ `dedup/scoring.py:262-264` | | |
| Downstream consumption contract | ✅ `api/routes.py:954-955`; `dedup/scoring.py:266-268` | | |
| An approver must be named | ✅ `dedup/scoring.py:560` (non-empty string) | | |
| The approver is a *real, distinct* person | | ⚠ DATAshaper UI (`CONTEXT-EXTERNAL.md:395-399`) — ⚠ NOT EVIDENCED | ❌ not authenticated, not compared, not persisted (§d.5.1) |
| Approval audit trail | | | ❌ stateless; one rotating log line (`api/routes.py:950-955`) |
| Four-eyes on code merges | | ⚠ GitHub branch protection ⚠ NOT EVIDENCED | ❌ zero merge commits in 51 (§d.5.2) |
| Four-eyes on deployment | | | ❌ manual VS Code publish (§a.5) |
| Log redaction of personal data | | | ❌ none (`05_DATA_MODEL.md:1076-1079`) |
| Log retention limit | ✅ size only — 10 MB × 5 (`api/middleware.py:105-107`) | ⚠ App Insights retention ⚠ NOT EVIDENCED | ❌ no time-based expiry |
| Email minimisation before SERP | ✅ domain only, non-freemail only (`enrichment/person_affiliation.py:62-66`) | | |

---

# (e) Findings for `08_GAPS.md`

Factual statements, each cited above, for the limitations and future-work sections.

1. No CI/CD configuration of any kind exists in the repository; nothing runs on push or on
   pull request (§a.1).
2. The test suite is red at `HEAD` — 3 failed, 1019 passed — and no gate consumes the result
   (§a.3).
3. Fifteen `# noqa` directives suppress a linter that the repository does not configure and no
   gate runs (§a.4).
4. All 14 runtime dependencies are declared as `>=` floors with no lock file
   (`requirements.txt:1-14`), and the build is performed remotely at deploy time
   (`.vscode/settings.json:3`), so two deployments of the same commit can install different
   library versions.
5. Deployment is a manual VS Code UI action with no scripted artefact, no staging slot, and no
   rollback procedure (§a.5).
6. Three of the five deployed components — ADF, DATAshaper, and the stored procedures — have no
   deployment artefact in any repository, so a contract change cannot be released atomically
   (§a.6).
7. `RequestLoggingMiddleware` sets `request.state.request_id` "for downstream correlation"
   (`api/middleware.py:26`) and nothing ever reads it; the identifier appears in three
   middleware lines and one response header, and in no per-record or per-block log line
   (§b.4.3).
8. No inbound correlation header is read, and ADF sends none, so an ADF pipeline run cannot be
   joined to any service log line (§b.4.3).
9. The log formatter (`api/middleware.py:87-91`) renders no `extra=` key, so every structured
   field on `request_complete`, `dedup_llm_call`, `dedup_block`, `dedup_request`, and
   `scoring_request` — including all token counts and all latencies — is absent from the
   console and file sinks (§b.3).
10. `api/middleware.py:1` describes the middleware as "structured JSON logging"; neither
    logging idiom in the codebase emits JSON (§b.3). Code↔doc discrepancy.
11. Phase 1 discards `response.usage` (`llm/openai_client.py:198-208`) while Phase 2 captures it
    (`dedup/llm.py:188-195`), so the more expensive phase is the unmeasured one (§b.7, §c.5.3).
12. `BatchCache.stats` (`utils/cache.py:109-111`) is never called, so cache effectiveness — the
    main determinant of SERP spend — is unmeasured (§b.7).
13. `configure_logging` calls `logging.basicConfig(…, force=True)` (`api/middleware.py:118`),
    which discards pre-existing root handlers; whether this displaces the Azure Functions
    worker's App Insights handler is ⚠ UNVERIFIED and would, if true, mean the deployed app
    ships no application telemetry at all (§b.1, §b.7).
14. App Insights sampling is enabled with only `Request` excluded (`host.json:5-7`), so the
    trace stream that carries all 178 application log statements is sampled while the request
    stream that carries almost no information is retained in full (§b.6).
15. `GET /health` returns the literal `"healthy"` (`api/routes.py:80`) with no dependency check
    and will report healthy on an app whose LLM credentials are absent (§b.5).
16. No monetary figure exists anywhere in the repository; the README `Cost` column is an ordinal
    design ranking, not a measurement (§c.1).
17. `MAX_PAGE_CONTENT_CHARS` is `"3000"` in `config.py:93` and `1500` in the executing dataclass
    field `config.py:209`, with `.env.example:81` setting `3000` — a cost-bearing three-way
    divergence (§c.3).
18. `DEPT_PROBE_CROSS_DOMAIN` defaults to `false` in code (`config.py:114`) and `true` in
    `.env.example:61`, doubling SERP calls for unresolved departments when the example file is
    copied as-is (§c.3).
19. Re-running the Enrichment pipeline re-pays for every row: `Lookup1` has no watermark
    predicate (`CONTEXT-EXTERNAL.md:106`) and the non-deterministic tiers may return different
    answers on the second pass (§c.6).
20. Every endpoint is `ANONYMOUS` (`function_app.py:12`) with no application-layer
    authentication, no CORS policy, no rate limit, and no request-size limit (§d.4).
21. Two unauthenticated GET endpoints make a billable Azure OpenAI call per request
    (`api/routes.py:1051-1055`, `:1085-1089`) and disclose endpoint, deployment names, API
    version, and the API key's length (§d.1.4, §c.6).
22. No Key Vault and no managed identity: Azure OpenAI is reached with a long-lived API key with
    no rotation mechanism, on a topology where workload identity is available (§d.1.3).
23. `LLM_SSL_VERIFY=false` disables TLS verification for the calls that carry personal data,
    is settable as an Application Setting with no code change, and leaves no indicator on
    `/health` or `/tiers` (§d.1.5).
24. Both ADF Web activities set `secureInput: false` and `secureOutput: false`
    (`CONTEXT-EXTERNAL.md:129-130`, `:249-250`), so full request and response bodies containing
    personal data are retained in cleartext in ADF monitoring on the Tillit tenant (§d.3).
25. The ADF `Lookup1` issues `SELECT *` (`CONTEXT-EXTERNAL.md:106`), so every column of every
    row crosses the Tillit→Bruker tenant boundary regardless of what enrichment needs (§d.2).
26. `approver` is required by the model (`dedup/scoring.py:560`) but is not a parameter of
    `apply_approval` (`:574-578`), is written to no row field, is never authenticated, and is
    never compared to any other actor — the four-eyes property is not enforced in code (§d.5.1).
27. `/api/dedup/approve` is stateless (`api/routes.py:950-955`), so no durable audit trail of
    any approval exists; the sole record is one log line in a size-rotated file (§d.5.1).
28. Because the approve endpoint accepts both the decision and the rows it applies to, an
    unauthenticated caller can obtain promoted golden fields for an arbitrary row set
    (§d.4, §d.5.1).
29. The repository history contains zero merge commits across 51 commits, and no `CODEOWNERS`,
    pull-request template, or `SECURITY.md` exists — no four-eyes control on code changes is
    evidenced (§d.5.2).
30. No log redaction exists anywhere, and the rotating log file has no time-based retention or
    deletion policy while carrying unredacted person names (§d.6).
31. Neither exported ADF pipeline carries a group-code predicate, so a run spans all imports
    under entity `test_77` rather than the intended import (`CONTEXT-EXTERNAL.md:64`, `:106`;
    §d.2).

---

# (f) Open items this pass could not close

Each requires an artefact or a live run outside this repository. Items 1–4 extend
`CONTEXT-EXTERNAL.md:439-448`; items 5–9 are new to this pass.

| # | Item | What would settle it |
|---|------|----------------------|
| 1 | Azure Functions hosting plan and HTTP timeout ceiling | the Function App resource blade — `CONTEXT-EXTERNAL.md:446` open item 6 |
| 2 | Measured per-batch duration for a 50-row `/enrich` call | already logged as `batch_ms` (`enrichment/orchestrator.py:838-841`) — read one run; `CONTEXT-EXTERNAL.md:447` open item 7 |
| 3 | Whether ADF invokes `/api/dedup/score` and `/api/dedup/approve` | the ADF pipeline list on the Tillit tenant — `CONTEXT-EXTERNAL.md:445` open item 5 |
| 4 | The stored-procedure bodies (`usp_merge_legacy_enriched`, `usp_merge_validation_clusters`) | SQL MI — `CONTEXT-EXTERNAL.md:441` open item 1 |
| 5 | Whether GitHub branch protection requires review on `main` | the repository's GitHub settings |
| 6 | Whether Azure inbound access restrictions constrain who can reach `mdm-pipeline-api` | the Function App networking blade — **the highest-value item in this document**, since it is the only thing that could compensate for §d.4 |
| 7 | Whether `extra=` keys arrive as App Insights `customDimensions`, and whether `basicConfig(force=True)` suppresses telemetry entirely | one deployed request, then the two KQL queries in §b.7 |
| 8 | Application Insights retention period for `mdm-pipeline-insights` | the App Insights resource blade |
| 9 | Whether DATAshaper authenticates and records the steward who presses `Apply Leading Code` | the DS Studio administration interface — determines whether the four-eyes control exists anywhere at all |

---

**Pass 6b complete.** CI/CD: absent — no automation artefact exists on any ref, deployment is a
manual VS Code publish, the suite is red at `HEAD` and ungated. Observability: 178 log
statements, 74% at `INFO`, split across two idioms of which the formatter renders only one;
`Customer` is the sole identifier that closes end to end, and no ADF run can be joined to a
service log line. Cost: structurally determined, numerically empty — every unit price is
⚠ MEASUREMENT REQUIRED, and the one already-instrumented quantity (Phase 2 tokens) is readable
only in Application Insights. Security: secrets are correctly excluded from git and from the
deployment package and are never logged, but every endpoint is anonymous, two of them spend
money per unauthenticated call, and the four-eyes control on merges is enforced as a decision
*structure* in code and as an *identity* nowhere in this repository.
