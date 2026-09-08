Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 04

# Pass 04 — Parameters

Every tunable the pipeline reads at this commit: environment variables, module
constants, LLM sampling parameters, the scoring weights table, the tunable word
lists, and the parameters carried by the infrastructure (ADF pipelines, the
Function App host, the merge procedures, the HTTP request bodies).

`Value` is the literal in the file cited. `Who sets it` names the mechanism that
can change it without a code edit: `env` (an environment variable), `request`
(an HTTP request field), `ADF` (a pipeline JSON property), `weights.json`, `CLI`
(a script flag), or `code` (a module constant with no external override).

## 0. Method

The tree at this commit carries modifications to `docs/thesis/00_INVENTORY.md`,
`01_TRACEABILITY.md`, `02_ARCHITECTURE.md`, `03_ALGORITHMS.md` and
`03b_EXEMPLARS.md` — the outputs of Passes 00–03b in this same set. No source,
configuration, SQL or ADF file is modified, so `86d173b` is the state every
citation below refers to.

```
$ git status --porcelain
 M docs/thesis/00_INVENTORY.md
 M docs/thesis/01_TRACEABILITY.md
 M docs/thesis/02_ARCHITECTURE.md
 M docs/thesis/03_ALGORITHMS.md
 M docs/thesis/03b_EXEMPLARS.md
$ git rev-parse HEAD
86d173b8a4d715a619b0a2656986c145da7fa81e
$ git rev-parse --abbrev-ref HEAD
feature/llm-fixes
$ date -I
2026-09-07
```

---

## 1. Environment variables

### 1.1 Credentials and endpoints

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `AZURE_OPENAI_API_KEY` | `""` | `config.py:213`, `llm/openai_client.py:249` | Azure OpenAI key. Required; absence is warned at startup and raises at client construction. | env |
| `AZURE_OPENAI_ENDPOINT` | `""` | `config.py:214`, `llm/openai_client.py:250` | Azure OpenAI resource endpoint. Required. | env |
| `AZURE_OPENAI_DEPLOYMENT` | `"gpt-5.4"` | `config.py:215`, `llm/openai_client.py:381` | Deployment name for every Phase 1 enrichment call. | env |
| `AZURE_OPENAI_API_VERSION` | unset → `DEFAULT_AZURE_OPENAI_API_VERSION` | `llm/openai_client.py:253` | REST API version for Phase 1 calls. | env |
| `AZURE_OPENAI_CA_BUNDLE` | unset | `config.py:53`, `llm/openai_client.py:163` | Corporate CA bundle path. First entry of `_CA_BUNDLE_ENV_VARS`; also the replacement value when `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` point at a non-existent path. | env |
| `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE` | unset | `config.py:57`, `llm/openai_client.py:163` | CA bundle fallbacks. A value naming a non-existent file is overwritten at import by `_sanitize_ssl_env`. | env |
| `LLM_SSL_VERIFY` | `true` | `llm/openai_client.py:201` | `false` disables TLS verification for LLM calls. | env |
| `SERPAPI_KEY` | `""` | `config.py:218`, `config.py:195` | SerpAPI key. Empty selects the DuckDuckGo fallback. | env |
| `AOAI_DEPLOYMENT_DEDUP` | unset → `AZURE_OPENAI_DEPLOYMENT` → `"gpt-5.4"` | `dedup/llm.py:143-147` | Deployment for adjudication calls, separate from the enrichment deployment. | env |
| `AOAI_API_VERSION_DEDUP` | unset → `AZURE_OPENAI_API_VERSION` → `DedupLLM.DEFAULT_API_VERSION` | `dedup/llm.py:150-154` | REST API version for adjudication calls. | env |

### 1.2 Registry lanes (Tier 1)

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `ROR_API_BASE` | `"https://api.ror.org/v2/organizations"` | `config.py:230`; also read directly at `enrichment/tier1_ror.py:934`, `:1654`, `enrichment/liveness.py:263` | ROR v2 endpoint. | env |
| `ROR_CONFIDENCE_THRESHOLD` | `"0.8"` | `config.py:235`; read directly at `enrichment/tier1_ror.py:936` | Minimum locally-rescored ROR match score for acceptance. One threshold for all record types. | env |
| `LEI_LOOKUP_ENABLED` | `"true"` | `config.py:300` | When false the company branch skips GLEIF entirely. | env |
| `GLEIF_API_BASE` | `"https://api.gleif.org/api/v1"` | `config.py:303`; defaulted again at `enrichment/tier1_lei.py:567`, `:803` | GLEIF endpoint. | env |
| `GLEIF_TIMEOUT_SECONDS` | `"15"` | `config.py:306`; defaulted again at `enrichment/tier1_lei.py:568`, `:804` | Per-request GLEIF timeout. | env |
| `LEI_NAME_MATCH_THRESHOLD` | `"88"` | `config.py:312`; read at import at `enrichment/orchestrator.py:1776`, again at `:3100`; defaulted again at `enrichment/tier1_lei.py:570`, `:806` | rapidfuzz `token_sort_ratio` (0–100) a GLEIF candidate's legal name must reach against the supplied name. GLEIF's `legalName` filter is fulltext, so a candidate below this is rejected. | env |
| `LEI_MAX_RETRIES` | `"2"` | `config.py:315`; defaulted again at `enrichment/tier1_lei.py:569`, `:805` | Retries on transient GLEIF failures. Backoff `0.5 × 2^(attempt−1)` seconds (`enrichment/tier1_lei.py:556`). | env |

### 1.3 Name acceptance and department eligibility

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `LLM_FALLBACK_AUTHORITATIVE` | `True` | `config.py:241-245` | "Write unless disproven". False reverts every acceptance gate to the legacy `confidence=="high"` floors, the binary identity guard and the established-nothing passthrough. | env |
| `UNDECIDABLE_WRITES` | `True`, accepting `on`/`off` as well as `true`/`false` | `config.py:249-255` | Whether an `undecidable` verdict writes (flagged `unverified-inference`) or is held back as a suggestion. | env |
| `DEPT_SPLIT_CANONICALISES` | `True` | `config.py:290-294` | Tier 2 eligibility by slot origin: a slot whose value preprocessing split, lifted or moved is eligible for canonicalisation; a value an authority produced is settled. | env |
| `FUZZY_MATCH_THRESHOLD` | `"80"` | `config.py:363`; consumed at `enrichment/tier2a_contact.py:180`, `:497` | Score a Tier 2A contact-page department must reach before it is written. | env |

### 1.4 Domain and search

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `DOMAIN_NAME_MATCH_THRESHOLD` | `"82"` | `config.py:328` | rapidfuzz `token_sort_ratio` Name 1 must reach against a candidate's domain label before a web-derived domain is attributed. Tuned on the demo batch: highest wrong-owner pair 81.8, lowest right-owner pair 82.4 (`config.py:318-326`). Registry provenance, email evidence and on-domain search evidence bypass it. | env |
| `DOMAIN_OWNERSHIP_GUARD_ENABLED` | `True` | `config.py:333-337` | Off skips the ownership conditions; candidates are still canonicalised to the registrable domain. | env |
| `DOMAIN_COUNTRY_GATE_ENABLED` | `True` | `config.py:345-349` | Refuses a candidate whose ccTLD places it in a country other than the record's. | env |
| `SERP_COUNTRY_LOCALISATION_ENABLED` | `True` | `config.py:355-359` | Sends the record's country to the provider as SerpAPI `gl` (`search/serpapi_client.py:72`). | env |
| `DEPT_PROBE_CROSS_DOMAIN` | `False` | `config.py:224-226` | Off holds the department-domain probe at one site-restricted SERP call; on adds the cross-domain fallback query. | env |
| `ACCEPTED_DOMAIN_CITY_WITHDRAWAL_ENABLED` | `"false"` | `config.py:430-434` | Withdrawal of an accepted domain on a name mismatch paired with a city-level location contradiction. The rule and its cascade are built; the trigger is disabled. | env |

### 1.5 Page fetching and corroboration

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `MAX_PAGE_CONTENT_CHARS` | `"1500"` | `config.py:368` | Characters of page text passed downstream (`PageFetcher(max_chars=…)`, `enrichment/orchestrator.py:4133`, `:4146`). ⚠ `config.py:121` and `.env.example:105` both state `3000` — see §9.1. | env |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `"10"` | `config.py:371` | Per-request page fetch timeout (`enrichment/orchestrator.py:4132`, `:4145`). | env |
| `PAGE_CORROBORATION_ENABLED` | `True` | `config.py:406-410` | Off: the corroboration step does not run and no page is fetched for it. | env |
| `PAGE_NAME_MATCH_THRESHOLD` | `"88"` | `config.py:436` | rapidfuzz `token_sort_ratio` an extracted page name must reach against Name 1. Reuses `enrichment.tier1_lei._name_match_score` and inherits `LEI_NAME_MATCH_THRESHOLD`'s value as a separate knob (`config.py:411-422`). | env |
| `PAGE_READ_TIMEOUT_SECONDS` | `"8"` | `config.py:443-445` | Hard per-request timeout for a corroboration fetch; shorter than `PAGE_FETCH_TIMEOUT_SECONDS` because up to five requests may be issued per domain. | env |
| `PAGE_FIXTURE_DIR` | `_cache_dir("PAGE_FIXTURE_DIR", "page_reads")` → `tests/fixtures/page_reads` | `config.py:451-453`, `config.py:76-90` | Page-read recording directory. `""` is memory-only. | env |
| `PAGE_FIXTURE_REPLAY_ONLY` | `False` | `config.py:457-461` | Refuses to fetch anything not recorded; a miss surfaces as `fetch_unavailable`. | env |
| `PAGE_EXTRACT_FEEDS_RETRY` | `False` | `config.py:467-471` | Offers a page-extracted legal name to the Stage 5 Tier 1 retry as a lookup candidate. | env |

### 1.6 Evidence cache

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `EVIDENCE_CACHE_DIR` | `"tests/fixtures"` | `config.py:390`, `config.py:89` | Root of the shared evidence cache; namespaces `page_reads/`, `wikidata/`, `serp/`, `registry/`, `fetch/`, `llm/` sit under it. `""` is memory-only. | env / `--cache-dir` |
| `CACHE_FROZEN` | `False` | `config.py:399-401` | A cache miss becomes an error rather than a network call; the miss is recorded per record as `evidence-unavailable-frozen` on `enrichment.trace.cache` and counted in the batch summary, and the record proceeds without that evidence. Applies to every namespace at once. | env / `--frozen` |
| `DEDUP_FIXTURE_CACHE_DIR` | unset (cache off) | `dedup/cache.py:49`, `:184` | Record/replay store for adjudicator calls. Unset means no cache. | env |
| `DEDUP_FIXTURE_CACHE_MODE` | `"record"` | `dedup/cache.py:50`, `:179`, `:187` | `record` writes a miss down; `replay` refuses it. Reported in the output workbook via `current_mode()`. | env |

### 1.7 Wikidata crosswalk lane

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `WIKIDATA_ENABLED` | `True` | `config.py:479-481` | Off: the lane does not run and no Wikidata call is made. | env / `--no-wikidata` |
| `WIKIDATA_DOMAIN_CORROBORATION` | `True` | `config.py:498-502` | On a registry-resolved record the lane runs corroboration-only, retaining the item's `P856` website claim; nothing is written and no pointer is followed. | env |
| `WIKIDATA_API_BASE` | `"https://www.wikidata.org/w/api.php"` | `config.py:507-511` | MediaWiki Action API. The SPARQL endpoint is not used in this lane. | env |
| `WIKIDATA_TIMEOUT_SECONDS` | `"10"` | `config.py:516`, consumed at `enrichment/wikidata.py:701` | Per-request timeout. | env |
| `WIKIDATA_MAX_RETRIES` | `"2"` | `config.py:519`, consumed at `enrichment/wikidata.py:702` | Retries; `enrichment/wikidata.py:743`. | env |
| `WIKIDATA_SEARCH_LIMIT` | `"5"` | `config.py:526`, consumed at `enrichment/wikidata.py:703`, `:792`, `:802` | `wbsearchentities` candidates. The gauntlet runs over all of them in one batched entity call so two survivors register as an ambiguity. | env |
| `WIKIDATA_FIXTURE_DIR` | `_cache_dir("WIKIDATA_FIXTURE_DIR", "wikidata")` → `tests/fixtures/wikidata` | `config.py:533-535` | Recording directory, one JSON file per key. | env |
| `WIKIDATA_FIXTURE_REPLAY_ONLY` | `False` | `config.py:538-542` | A missing fixture surfaces as `wikidata_unavailable`. | env |
| `WIKIDATA_TRACE` | `False` | `config.py:549-551` | One JSON line per lane invocation on `enrichment.trace.wikidata`. Batch-summary counters are maintained regardless. | env / `--wikidata-trace` |

### 1.8 Liveness lane

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `LIVENESS_ENABLED` | `True` | `config.py:560-562` | Off: no probe, no redirect check; the lane can only ever add an `entity-superseded` flag. | env |
| `LIVENESS_ROR_PROBE_ENABLED` | `True` | `config.py:568-572` | The ROR half — one query per distinct (name, country) in the batch, re-asked with `all_status=`. | env |
| `LIVENESS_REDIRECT_CHECK_ENABLED` | `True` | `config.py:575-579` | The redirect half; reuses the department probe's resolution and its `BatchCache` entry. | env |
| `LIVENESS_REDIRECT_NAME_THRESHOLD` | `"60"` | `config.py:585-589`, consumed at `enrichment/liveness.py:328-358` | Above it, a cross-domain redirect reads as a move and is ignored; below it, the landing domain names a different organisation. Measured gap 16.7 / 66.7, dated 2026-08-26 (`enrichment/liveness.py:350-357`). | env |

### 1.9 Clustering and scoring

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `SIG_PARTITION_THRESHOLD` | `12` | `dedup/adjudicator.py:39`, `:1466` | Signatures per block above which the block is partitioned. | env / request |
| `DEDUP_MAX_CONCURRENCY` | `5` | `dedup/adjudicator.py:40`, `:1468` | In-flight adjudication calls across all blocks (one shared semaphore, `dedup/adjudicator.py:1469`). | env / request |
| `NAME_CANDIDATE_THRESHOLD` | `0.85` | `config.py:606`, `dedup/adjudicator.py:41`, `:1434-1436` | Jaro-Winkler suffix-stripped name similarity at which a signature pair becomes an adjudication candidate. Nomination never merges. | env / settings |
| `TOKEN_CANDIDATE_THRESHOLD` | `0.6` | `config.py:609`, `dedup/adjudicator.py:42`, `:1437-1439` | Token-set Jaccard alternative for the same nomination. | env / settings |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | `config.py:612`, `dedup/adjudicator.py:43`, `:1440-1442` | Cap on adjudication calls per block; over the cap the block routes to `manual_review`. | env / settings |
| `CONFIDENCE_MERGE_THRESHOLD` | `0.95` | `config.py:600`, `dedup/scoring.py:50`, `:1126` | A merge below this confidence keeps its cluster membership and enters election as `manual_review`. Retuning reads the confidence persisted by clustering and never re-runs the LLM. | env / request arg |
| `DEDUP_REASONING_EFFORT` | `"low"` | `dedup/llm.py:148` | `reasoning_effort` sent on adjudication calls. Empty disables it, which is what re-enables `temperature`. | env |
| `DEDUP_MAX_RETRIES` | `"3"` | `dedup/llm.py:149`, `:206`, `:281-287` | Bounded retries on retryable adjudication failures; delay `0.5 × 2^attempt` seconds. | env |
| `DEDUP_V2_BLOCKING` | unset → off | `dedup/flags.py:31`, `:40-42` | Delivery-point blocking (`dedup/address.py`) in place of the raw `country\|postal\|street\|house` hash. | env |
| `DEDUP_V2_NAME2` | unset → off | `dedup/flags.py:32`, `:45-47` | Classifying the text below Name 1 before it is treated as a department. | env |
| `DEDUP_V2_ID_CONFLICT` | unset → off | `dedup/flags.py:33`, `:50-52` | Routing an ROR/LEI conflict to review instead of splitting the entity. | env |

`_TRUTHY` for the three v2 flags is `{"1", "true", "yes", "on"}` (`dedup/flags.py:29`); the value is `.strip().lower()`-ed, and anything else reads as off. Any v2 flag on adds the `Link ID` column (`dedup/flags.py:55-63`).

### 1.10 Runtime, transport and diagnostics

| Name | Value (verbatim default) | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `ENV` | `"production"` | `config.py:619` | Environment label. `.env` is loaded unconditionally at import (`config.py:22`). | env |
| `LOG_LEVEL` | `"INFO"` | `config.py:620` | Log level. | env |
| `LOG_FILE` | unset | `config.py:635`, `api/middleware.py:98` | `None` selects `configure_logging`'s default (`logs/enrichment_api.log`); `""` disables file logging. | env |
| `MOCK_EXTERNAL_CALLS` | `False` | `config.py:616-618`, `api/routes.py:76`, `:1091` | Substitutes mock clients for the external lanes. | env |
| `DEFAULT_MAX_CONCURRENCY` | `"5"` | `config.py:592-593` | ⚠ Reported by `/config` (`api/routes.py:1656`) and read nowhere else — see §9.2. | env |
| `LLM_HTTP_CONNECT_TIMEOUT` | `"30"` | `llm/openai_client.py:264` | httpx connect timeout for the Azure OpenAI client. | env |
| `LLM_HTTP_TIMEOUT` | `"60"` | `llm/openai_client.py:265` | httpx read timeout for the Azure OpenAI client. | env |
| `NAME_FIELD_WIDTH` | `"40"` | `enrichment/name_repack.py:50`, `:107`, `:140` | SAP name-column width used by `chunk_name` and the repack rules. | env |
| `WEBSITE_TRACE` | `False` | `config.py:623-625` | Per-candidate JSON trace of the Path B / Path C website resolver on `enrichment.trace.website`. Resolution behaviour is unchanged. | env / `--website-trace` |
| `RETRY_TRACE` | `False` | `config.py:630-632` | One JSON line per finalised record on `enrichment.trace.retry` covering the Stage 5 Tier 1 re-lookup. Retry behaviour is unchanged. | env / `--retry-trace` |

### 1.11 CLI flags that set environment variables

`scripts/run_batch.py` sets process environment before `Settings` is constructed
(`scripts/run_batch.py:87-99`).

| Flag | file:line | Sets |
|---|---|---|
| `--retry-trace` | `scripts/run_batch.py:68`, `:88-89` | `RETRY_TRACE=true` |
| `--website-trace` | `scripts/run_batch.py:70`, `:90-91` | `WEBSITE_TRACE=true` |
| `--wikidata-trace` | `scripts/run_batch.py:72`, `:92-93` | `WIKIDATA_TRACE=true` |
| `--no-wikidata` | `scripts/run_batch.py:74`, `:94-95` | `WIKIDATA_ENABLED=false` |
| `--frozen` | `scripts/run_batch.py:76`, `:96-97` | `CACHE_FROZEN=true` |
| `--cache-dir` | `scripts/run_batch.py:80`, `:98-99` | `EVIDENCE_CACHE_DIR=<value>` |
| `--concurrency` (default `5`) | `scripts/run_batch.py:82` | `EnrichmentOptions.max_concurrency` |
| `--limit` (default `None`) | `scripts/run_batch.py:83` | Rows enriched |
| `--trace-out` (default `logs/trace.jsonl`) | `scripts/run_batch.py:66` | Trace file path |

---

## 2. Module constants

These have no environment override. Changing one is a code edit.

### 2.1 Enrichment — registry and identity guards

| Name | Value | file:line | Effect |
|---|---|---|---|
| `_DISTINCTIVE_TOKEN_MIN_LEN` | `4` | `enrichment/tier1_ror.py:593` | Minimum query-token length counting as distinctive in the step-4 ROR guard. At five, "Acme Biotech" scored 0.87 against ROR's "AUM BioTech" on the shared "biotech" alone and was written as verified; at four "acme" is distinctive, "aum" does not cover it, and the candidate caps at 0.7 (`enrichment/tier1_ror.py:572-592`). |
| `_FUZZY_RESOLVE_LIMIT` | `5` | `enrichment/tier1_lei.py:95` | `fuzzycompletions` candidates resolved to their full lei-record. A call budget; the five taken are those with the smallest LEI. |
| `page[size]` | `"10"` | `enrichment/tier1_lei.py:634` | GLEIF exact-filter page size (Strategy A). |
| `REGISTRY_AMBIGUITY_MARGIN` | `2.0` | `enrichment/registry_match.py:63` | Score gap below which two registry candidates count as ambiguous. The Wikidata lane has no numeric margin — its ambiguity rule is "more than one candidate survived the gauntlet". |
| `_ACRONYM_MAX_LEN` | `5` | `enrichment/registry_match.py:83` | A single all-caps token this long or shorter reads as an acronym. |
| `_SHORT_NAME_MAX_LEN` | `4` | `enrichment/registry_match.py:87` | A name with this many significant characters or fewer is collision-prone whatever its case. |
| `_LEI_NAME_THRESHOLD` | `float(os.getenv("LEI_NAME_MATCH_THRESHOLD", "88"))` | `enrichment/orchestrator.py:1776` | Module-level capture of the GLEIF name threshold, read once at import. |
| `MAX_REJECTIONS_PER_FIELD` | `3` | `enrichment/provenance.py:421` | Rejections retained per field per record; beyond it only the count is kept. |
| `GUARDS` | 6 entries | `enrichment/provenance.py:415-418` | `ror_country`, `distinctive_token`, `identifier_token`, `domain_ownership`, `gleif_name`, `page_identity`. |

### 2.2 Enrichment — web, department and page evidence

| Name | Value | file:line | Effect |
|---|---|---|---|
| `_DOMAIN_MAX_ATTEMPTS` | `3` | `enrichment/orchestrator.py:279` | SERP candidates the domain lane may try for one record: the ranker's pick plus two runners-up. |
| `num_results` (website resolver) | `10` | `enrichment/website_resolver.py:911` | Organic results requested for website resolution. |
| `num_results` (Tier 2A/2B, person affiliation, lab resolver, dept probe) | `5` | `enrichment/tier2b_dept.py:220`, `enrichment/tier2a_contact.py:337`, `enrichment/person_affiliation.py:133`, `enrichment/lab_resolver.py:80`, `enrichment/orchestrator.py:4904`, `:5037` | Organic results requested per SERP call. |
| `MAX_FETCHES` | `3` | `enrichment/grounded_resolver.py:83` | Pages the grounded resolver fetches. Past the third organic result the pages stop being about the organisation. |
| `NUM_RESULTS` | `5` | `enrichment/grounded_resolver.py:88`, consumed at `:374` | Organic results requested by the grounded resolver — wider than `MAX_FETCHES` because a snippet is evidence too. |
| `_CHILD_MATCH_THRESHOLD` | `70` | `enrichment/orchestrator.py:3632` | rapidfuzz `token_sort_ratio` minimum for matching Name 2 against a parent org's children list. |
| `_MIN_CONTENT_CHARS` | `120` | `enrichment/page_corroborator.py:123` | Below this much page text the corroboration LLM call is skipped. |
| `_FOOTER_MAX_CHARS` | `600` | `search/page_fetcher.py:35` | Footer text kept, taken from the tail. |
| `PageFetcher(timeout=…, max_chars=…)` | `10`, `1500` | `search/page_fetcher.py:134-135` | Constructor defaults; the orchestrator overrides both from `Settings` (`enrichment/orchestrator.py:4131-4135`, `:4144-4148`). |
| `subdomain_exists(timeout=…)` | `5` | `search/page_fetcher.py:284` | Subdomain probe timeout. |
| `resolve_final_url(timeout=…)` | `5` | `search/page_fetcher.py:308` | Redirect-resolution timeout. |
| `SERP_LABEL_RELATION_MIN` | `60.0` | `enrichment/unchanged_state.py:122` | How closely a `serp`-accepted domain's label must resemble Name 1 for that acceptance to count as tying the domain to the name. Below the ownership threshold on purpose: it separates an organisation's own site from a page about it. |
| `RATIO_THRESHOLD` | `92` | `enrichment/dept_block.py:112` | Department-unit equivalence. "Physics" and "Physiology" score 82 and stay two units; a one-character typo is one. |
| `fuzz.ratio(...) >= 92` | `92` | `enrichment/preprocess.py:2661` | The same equivalence bar inside UC 12 slot dedup. |
| `score >= 90` / `>= 60` | `90`, `60` | `enrichment/tier2b_dept.py:150` | Tier 2B `name2_match` bands: `exact` / `partial` / `no_match`. |
| `effective_score >= 95` | `95` | `enrichment/tier2a_contact.py:502` | Tier 2A near-exact band; above it `enrichment_status="verified"`, below `"enriched"`. |
| `_RESIDUAL_CONFIDENCE_THRESHOLD` | `0.85` | `enrichment/address_processing.py:751` | Minimum LLM confidence for a residual address classification. |
| `_SAP_NAME_LIMIT` | `140` | `enrichment/issue_detection.py:465`, consumed at `:1389` | Combined SAP name-block length limit. |
| `NAME_SLOT_COUNT` | `5` | `utils/name_slots.py:35` | SAP name columns carried through the pipeline; `NAME_SLOTS` and `RECORD_NAME_FIELDS` derive from it. |
| `_SIGNIFICANT_TOKEN_LEN` | `4` | `utils/domain_resolver.py:80` | Minimum token length counting as significant when checking whether a page title or H1 names the organisation. |
| `_SUBSTANTIVE_MIN_LEN` | `5` | `utils/name_identity.py:167` | Below this length a token with no dictionary standing reads as a code, and its absence from a proposal is not evidence against it. |
| `_SPELLING_VARIANT_TOKEN_RATIO` | `85.0` | `utils/text_utils.py:1435` | Minimum per-token fuzz ratio for two tokens to count as spelling variants. |
| `_RATE_LIMIT_BACKOFF_SECONDS` | `5.0` | `enrichment/wikidata.py:655`, consumed at `:681` | Base backoff for a 429 from Wikidata. An order of magnitude longer than the GLEIF client's 0.5 s; the first live 100-row run at concurrency 3 took `HTTPStatusError:429` on 28 of 68 invocations under GLEIF's schedule. |
| `_MAX_RETRY_AFTER_SECONDS` | `30.0` | `enrichment/wikidata.py:660` | Cap on a server-supplied `Retry-After`; past it the lane reports `wikidata_unavailable`. |
| non-429 backoff base | `0.5` | `enrichment/wikidata.py:681` | Base backoff for other transient Wikidata failures. |

### 2.3 Clustering

| Name | Value | file:line | Effect |
|---|---|---|---|
| `STREET_NAME_THRESHOLD` | `0.85` | `dedup/address.py:87` | Street cores this close count as the same street. |
| `ZIP_EDIT_TOLERANCE` | `1` | `dedup/address.py:91` | Edit distance two postal codes may drift and still be one delivery point, transposition included. |
| `ACRONYM_MAX_LEN` | `6` | `dedup/candidates.py:151` | Longest string still read as an acronym. |
| `ACRONYM_MIN_LEN` | `3` | `dedup/candidates.py:158` | Shortest. Two characters is a coincidence — "HP" matches the initials of every two-word name beginning H, P. |
| `ACRONYM_THRESHOLD` | `0.8` | `dedup/candidates.py:159` | Similarity an initialism must reach against a candidate acronym. |
| `CROSS_SLOT_THRESHOLD` | `0.85` | `dedup/candidates.py:160` | Similarity for a cross-slot name match. |
| `NAME_VARIANT_MAX_EXTRA_TOKENS` | `1` | `dedup/candidates.py:279`, consumed at `:302-304` | Extra words a superset name may carry and still be one name. One is a variant; four is a different organisation. |
| `JaroWinkler.similarity(a, b) >= 0.85` | `0.85` | `dedup/candidates.py:298` | Name-variant similarity floor. |
| `OVERFLOW_THRESHOLD` | `0.92` | `dedup/name_slots.py:109` | How close a rebuilt Name 1 must be to a real one in the block to count as overflow. Higher than the candidate threshold: the rule ends with two rows sharing a signature with no model in between. |
| `INSTITUTION_THRESHOLD` | `0.85` | `dedup/name_slots.py:112` | How close the Name 2 of an opaque-coded row must be to a real Name 1. |
| `INSTITUTION_SPLIT_THRESHOLD` | `0.92` | `dedup/name_slots.py:118` | How close a rebuilt name must be to a real institution before the two slots count as one name split in half. |
| `DEFAULT_SIG_PARTITION_THRESHOLD` | `12` | `dedup/adjudicator.py:39` | Default for `SIG_PARTITION_THRESHOLD`. |
| `DEFAULT_DEDUP_MAX_CONCURRENCY` | `5` | `dedup/adjudicator.py:40` | Default for `DEDUP_MAX_CONCURRENCY`. |
| `DEFAULT_NAME_CANDIDATE_THRESHOLD` | `0.85` | `dedup/adjudicator.py:41` | Default for `NAME_CANDIDATE_THRESHOLD`. |
| `DEFAULT_TOKEN_CANDIDATE_THRESHOLD` | `0.6` | `dedup/adjudicator.py:42` | Default for `TOKEN_CANDIDATE_THRESHOLD`. |
| `DEFAULT_MAX_CANDIDATES_PER_BLOCK` | `50` | `dedup/adjudicator.py:43` | Default for `MAX_CANDIDATES_PER_BLOCK`. |

Resolution order for the three residue knobs is settings attribute > environment
variable > module default (`dedup/adjudicator.py:1420-1443`); an unparseable
environment value logs a warning and falls through to the default (`:1429-1430`).

### 2.4 Scoring

| Name | Value | file:line | Effect |
|---|---|---|---|
| `WEIGHTS_PATH` | `Path(__file__).parent / "weights.json"` → `dedup/weights.json` | `dedup/scoring.py:45` | The weights table's location. It is at `dedup/weights.json`, not at the repository root. |
| `DEFAULT_CONFIDENCE_MERGE_THRESHOLD` | `0.95` | `dedup/scoring.py:50` | Default for `CONFIDENCE_MERGE_THRESHOLD`. |
| `weights_version` digest length | `12` hex characters of `sha256` over canonical JSON | `dedup/scoring.py:641-646` | Written onto every scored row as `scored_with_weights_version` so a proposal and its later approval can be checked for score drift. |
| tie detection | `len(scores) >= 2 and scores.count(top) >= 2` | `dedup/scoring.py:556` | When the top score is held by two or more members the tie-break runs. |
| `_cluster_merge_confidence` | minimum of non-`None` member confidences | `dedup/scoring.py:1138-1145` | A cluster is gated if any member joined below threshold. All-`None` returns `None` and never gates. |

Election `election_status` takes `proposed` / `manual_review` / `unique`
(`dedup/scoring.py:318`) and carries no numeric threshold other than
`CONFIDENCE_MERGE_THRESHOLD`.

---

## 3. LLM parameters

### 3.1 Sampling — Phase 1 enrichment

| Name | Value | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `LLM_TEMPERATURE` | `0.0` | `llm/openai_client.py:102` | Sampling temperature for every Phase 1 call. A module constant rather than an environment knob: a reproducibility control that can be changed per environment is not a control (`llm/openai_client.py:88-92`). | code |
| `LLM_TOP_P` | `1.0` | `llm/openai_client.py:103` | Removes nucleus truncation as a second source of variation. | code |
| `LLM_SEED` | `42` | `llm/openai_client.py:108` | Fixed request seed. The value is arbitrary; only its fixity matters. | code |
| `_SEED_SUPPORTED` | `True` | `llm/openai_client.py:114`, `:448` | Set to `False` once, process-wide, the first time the deployment rejects `seed`; the parameter is then not sent again. | code (runtime one-shot) |
| `call_openai(max_tokens=…)` | `500` | `llm/openai_client.py:282` | Default budget for the bare function. | code |
| `OpenAIClient.extract_json(max_tokens=…)` | `1024` | `llm/openai_client.py:411` | Budget for every enrichment call except the two below. | code |
| `DEFAULT_AZURE_OPENAI_API_VERSION` | `"2024-08-01-preview"` | `llm/openai_client.py:81` | Phase 1 REST API version when `AZURE_OPENAI_API_VERSION` is unset. | code |
| JSON retry attempts | `2` | `llm/openai_client.py:462` | One retry when the first response is not valid JSON. | code |

Per-call budgets that differ from the `1024` default:

| Call site | `max_tokens` | file:line |
|---|---|---|
| Address residual classification | `200` | `enrichment/address_processing.py:773` |
| `/diagnostics` LLM smoke call | `50` | `api/routes.py:1595` |
| `/diagnostics` dedup smoke call | `200` | `api/routes.py:1629` |

Every other `extract_json` caller passes no budget and takes `1024`:
`enrichment/tier2b_dept.py:264`, `enrichment/tier2a_contact.py:415`,
`enrichment/tier3_llm.py:120`, `enrichment/tier2_canonical.py:198`,
`enrichment/company_canonical.py:81`, `enrichment/page_corroborator.py:363`,
`enrichment/grounded_resolver.py:596`, `enrichment/website_resolver.py:1062`,
`enrichment/person_affiliation.py:163`, `enrichment/lab_resolver.py:112`,
`enrichment/overflow_check.py:124`, `enrichment/preprocess.py:3345`.
`enrichment/grounded_resolver.py:597` is the one site that passes
`temperature=0.0` explicitly; it equals the default.

The LLM answer is itself cached under a digest of deployment, API version,
temperature, `top_p`, seed, `max_tokens` and both prompts
(`llm/openai_client.py:440-452`); under `replay_only` a miss raises
`LLMUnavailableFrozen` (`:456-459`).

### 3.2 Sampling — Phase 2 adjudication

| Name | Value | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `DedupLLM.TEMPERATURE` | `0.0` | `dedup/llm.py:138` | Sent when `reasoning_effort` is not in play. Fixed rather than configurable for the same reason as Phase 1. | code |
| `DedupLLM.DEFAULT_API_VERSION` | `"2025-04-01-preview"` | `dedup/llm.py:132` | Adjudicator REST API version; GPT-5.x reasoning models and `reasoning_effort` require a newer version than the Phase 1 default. | code |
| `reasoning_effort` | `"low"` | `dedup/llm.py:148` | Reasoning budget. While it is active, `temperature` is suppressed — on a reasoning deployment the two are mutually exclusive (`dedup/llm.py:9-15`, `:166-169`). | env |
| `LLM_SEED`, `LLM_TOP_P` | `42`, `1.0` | `dedup/llm.py:30-31` | Imported from the Phase 1 client; each is dropped once, process-wide, if the deployment rejects it (`dedup/llm.py:157-160`, `:260`, `:270`, `:279`). | code |
| `adjudicate(max_tokens=…)` | `4000` | `dedup/llm.py:195` | Signature default. | code |
| adjudication call budget | `1000` | `dedup/adjudicator.py:642`, `:911` | What the two adjudication call sites actually pass. | code |
| retry delay | `0.5 * (2 ** attempt)` | `dedup/llm.py:282` | Exponential backoff between bounded retries. | code |

---

## 4. `dedup/weights.json`

The golden-record scoring weights. The file is at `dedup/weights.json`, not at
the repository root (`dedup/scoring.py:45`). The scorer holds no points in code;
`load_weights` reads this file and drops keys beginning `_` as metadata
(`dedup/scoring.py:649-654`).

Last change to the file:

```
$ git log -1 --format='%H %ad %s' --date=short -- dedup/weights.json
f5c8d8df9db1a6dc3d93f912781849c49383af72 2026-09-05 Dates are not years: four sales-order rules scored 0 on every record
$ git log -1 --format=%h -- dedup/weights.json
f5c8d8d
```

Fingerprint written onto every scored row at this commit:

```
$ PYTHONPATH=. python3 -W ignore -c "
from dedup.scoring import load_weights, weights_version, WEIGHTS_PATH
w = load_weights()
print('path      :', WEIGHTS_PATH.relative_to(WEIGHTS_PATH.parent.parent))
print('criteria  :', len(w))
print('version   :', weights_version(w))
"
path      : dedup/weights.json
criteria  : 11
version   : 3147cac47910
```

`weights_version` is computed over the metadata-stripped mapping
(`dedup/scoring.py:641-646`, `:1172-1175`), so `_comment` does not enter the
digest.

### 4.1 Full contents

```json
{
  "_comment": "Golden-record scoring weights. Editable reference table — the scorer never hardcodes points. Band labels: 'a-b' inclusive range, '>n' strictly greater, 'n+' greater-or-equal, bare number exact, 'X/Y' either literal (case-insensitive). Values with no matching band score 0. The two *_last_used ladders are banded on the OFFSET from the election's reference year (0 = this year, 1 = last year, ...), not on absolute years — a negative offset (future-dated order) matches nothing and scores 0. The three count ladders start at 1, not 0: the source report encodes 'none' as NULL and never as a literal 0, so the first band means 'has activity'. UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count tiers, account_group DRIT (transcript said DRID; live SAP shows DRIT).",
  "sales_order_last_used": {
    "0": 20,
    "1": 15,
    "2": 10,
    "3": 5
  },
  "sales_order_count": {
    "1-5": 5,
    "6-10": 15,
    ">10": 25
  },
  "sales_order_partner_last_used": {
    "0": 20,
    "1": 15,
    "2": 10,
    "3": 5
  },
  "sales_order_partner_count": {
    "1-5": 5,
    "6-10": 15,
    ">10": 25
  },
  "equipment_count": {
    "1-3": 5,
    "4-8": 12,
    "9-15": 20,
    ">15": 30
  },
  "sleeping_customer": {
    "No": 15,
    "Yes": 0
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
    "0005/LIEF/MLIEF": 5
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

### 4.2 Band grammar and maxima

Band-label grammar, stated in `_comment` (`dedup/weights.json:2`): `a-b` is an
inclusive range, `>n` strictly greater, `n+` greater-or-equal, a bare number is
exact, `X/Y` is either literal case-insensitively, and a value matching no band
scores 0.

| Criterion | Bands | Maximum points |
|---|---|---|
| `sales_order_last_used` | `0`/`1`/`2`/`3` (offset from the reference year) | 20 |
| `sales_order_count` | `1-5`, `6-10`, `>10` | 25 |
| `sales_order_partner_last_used` | `0`/`1`/`2`/`3` (offset) | 20 |
| `sales_order_partner_count` | `1-5`, `6-10`, `>10` | 25 |
| `equipment_count` | `1-3`, `4-8`, `9-15`, `>15` | 30 |
| `sleeping_customer` | `No`, `Yes` | 15 |
| `customer_status` | `active`, `blocked` | 10 |
| `account_group` | `DRIT`, `0002/SHIP2`, `0003`, `0004`, `0005/LIEF/MLIEF` | 20 |
| `company_code_count` | `1`, `2-4`, `5+` | 25 |
| `combined_presence_bonus` | `company code AND sales org` | 10 |
| `salesforce_instance_count` | `per instance` (multiplied by instance count) | unbounded |

The two `*_last_used` ladders band on the offset from the election's reference
year, not on absolute years; a negative offset matches nothing and scores 0. The
three count ladders start at 1, not 0, because the source report encodes "none"
as NULL and never as a literal 0.

⚠ UNVERIFIED — the file's own `_comment` marks three entries as unconfirmed:
`combined_presence_bonus`'s value, `sales_order_partner_count`'s tiers, and
`account_group`'s `DRIT` key (the transcript reads `DRID`; live SAP shows
`DRIT`). Verification is with Bernd (`dedup/weights.json:2`).

### 4.3 Weights overrides at request time

An override is all-or-nothing: every (criterion, band) pair in
`dedup/weights.json` must be present with a numeric Points value, or the
override is rejected (`dedup/scoring.py:657-665`). The rule is shared by the
JSON `/api/dedup/score` body and the `/api/dedup/score/file` Weights sheet.

---

## 5. Tunable vocabularies

Word lists and mappings whose membership changes acceptance decisions. Contents
are enumerated in Pass 16; sizes here are measured, not stated.

```
$ PYTHONPATH=. python3 -W ignore scratchpad/vocab_count.py
enrichment.tier1_ror._COMMON_DOMAIN_WORDS	38
enrichment.tier1_ror._CONNECTOR_WORDS	43
enrichment.tier1_ror.ROR_RESEARCH_TYPES	7
enrichment.tier1_ror._LEGAL_SUFFIX_SUBS	8
enrichment.tier1_lei._LEGAL_FORM_TOKENS	34
enrichment.registry_match._LEGAL_FORM_ALIASES	4
enrichment.issue_detection._NONCANON_TOKENS	32
enrichment.issue_detection._POSTAL_FORMATS	3
enrichment.page_corroborator.IMPRINT_PATHS	4
enrichment.page_corroborator._PARKING_MARKERS	15
enrichment.page_corroborator._CHALLENGE_MARKERS	10
enrichment.liveness.GLEIF_DEAD_ENTITY_STATUS	1
enrichment.liveness.GLEIF_DEAD_REGISTRATION_STATUS	2
enrichment.liveness.ROR_DEAD_STATUS	1
dedup.candidates.LEGAL_SUFFIXES	38
dedup.candidates.CORPORATE_STRUCTURE_WORDS	16
dedup.candidates._ACRONYM_STOPWORDS	5
dedup.name_slots.LOGISTICS_TERMS	15
dedup.name_slots._CONTINUATION_NOUNS	31
dedup.name_slots._DANGLING_CONNECTORS	8
dedup.address.STREET_TYPES	23
dedup.address.DIRECTIONALS	9
search.page_fetcher.REMOVE_TAGS	8
```

The script is reproduced verbatim at the end of this section.

| Vocabulary | Entries | file:line | Effect |
|---|---|---|---|
| `_COMMON_DOMAIN_WORDS` | 38 | `enrichment/tier1_ror.py:562` | Tokens that describe an organisation's type rather than name it; excluded from the distinctive-token guard so a four-letter legal form cannot cap a candidate. |
| `_CONNECTOR_WORDS` | 43 | `enrichment/tier1_ror.py:619` | Articles, prepositions and conjunctions. A connector never says which organisation, so it cannot cap a candidate however long it is. |
| `ROR_RESEARCH_TYPES` | 7 | `enrichment/tier1_ror.py:52` | ROR organisation types the research branch accepts. |
| `_LEGAL_SUFFIX_SUBS` | 8 | `enrichment/tier1_ror.py:219` | Legal-suffix substitutions applied before comparison. |
| `_WORDLIKE_POSTAL_CODES` | `{"hi","in","or","ok","me","la","de"}` | `enrichment/tier1_ror.py:171` | Two-letter tokens that read as words as well as postal codes. |
| `_LEGAL_FORM_TOKENS` | 34 | `enrichment/tier1_lei.py:82` | Legal-form tokens dropped before a GLEIF name is measured. Also the source for `registry_match._legal_form_tokens()` (`enrichment/registry_match.py:70-73`). |
| `_LEGAL_FORM_ALIASES` | 4 | `enrichment/registry_match.py:237` | Legal-form aliases folded together. |
| `_NONCANON_TOKENS` | 32 | `enrichment/issue_detection.py:759` | Tokens marking a non-canonical unit name. |
| `_UNIT_EXEMPT_TOKENS` | `{"dept","div","inst"}` | `enrichment/issue_detection.py:777` | Unit abbreviations exempt from the non-canonical rule. |
| `_ORG_EXEMPT_TOKENS` | `{"inst"}` | `enrichment/issue_detection.py:778` | Organisation-level exemption. |
| `_POSTAL_FORMATS` | 3 | `enrichment/issue_detection.py:953` | Country postal-code patterns validated. |
| `_TRUTHY` (issue detection) | `{"true","yes","y","x","1"}` | `enrichment/issue_detection.py:1456` | Values read as set in a boolean input column. |
| `IMPRINT_PATHS` | 4 | `enrichment/page_corroborator.py:89` | `/impressum`, `/legal`, `/about`, `/contact` — the imprint probe's paths. |
| `_PARKING_MARKERS` | 15 | `enrichment/page_corroborator.py:103` | Markers identifying a parked domain. |
| `_CHALLENGE_MARKERS` | 10 | `enrichment/page_corroborator.py:113` | Markers identifying a bot-challenge interstitial. |
| `ACTIONABLE_LOCATION_SCOPES` | `{"region","country"}` | `enrichment/page_corroborator.py:479` | Location-contradiction scopes that act. |
| `GLEIF_DEAD_ENTITY_STATUS` | `{"INACTIVE"}` | `enrichment/liveness.py:107` | GLEIF entity status counting as dead. |
| `GLEIF_DEAD_REGISTRATION_STATUS` | `{"RETIRED","MERGED"}` | `enrichment/liveness.py:112` | GLEIF registration status counting as dead. |
| `ROR_DEAD_STATUS` | `{"inactive"}` | `enrichment/liveness.py:116` | ROR status counting as dead. |
| `LEGAL_SUFFIXES` | 38 | `dedup/candidates.py:30` | Suffixes stripped before name comparison in nomination. |
| `CORPORATE_STRUCTURE_WORDS` | 16 | `dedup/candidates.py:268` | Words marking a corporate-structure difference. |
| `_ACRONYM_STOPWORDS` | 5 | `dedup/candidates.py:147` | `of`, `the`, `and`, `for`, `&` — dropped before initials are taken, so "University of Texas" initialises to UT. |
| `LOGISTICS_TERMS` | 15 | `dedup/name_slots.py:67` | Logistics/admin markers stripped from a name slot. |
| `_CONTINUATION_NOUNS` | 31 | `dedup/name_slots.py:93` | Nouns marking a Name 2 that continues Name 1. |
| `_DANGLING_CONNECTORS` | 8 | `dedup/name_slots.py:104` | Words a truncated Name 1 ends on. |
| `STREET_TYPES` | 23 | `dedup/address.py:54` | Street-type normalisation map. |
| `DIRECTIONALS` | 9 | `dedup/address.py:71` | Directional normalisation map. `STREET_SUFFIXES` is their union (`dedup/address.py:78`). |
| `REMOVE_TAGS` | 8 | `search/page_fetcher.py:27` | HTML tags stripped before text extraction. |

The counting script, written for this pass and not committed:

```python
import importlib
TARGETS = [
    ("enrichment.tier1_ror", "_COMMON_DOMAIN_WORDS"),
    ("enrichment.tier1_ror", "_CONNECTOR_WORDS"),
    ("enrichment.tier1_ror", "ROR_RESEARCH_TYPES"),
    ("enrichment.tier1_ror", "_LEGAL_SUFFIX_SUBS"),
    ("enrichment.tier1_lei", "_LEGAL_FORM_TOKENS"),
    ("enrichment.registry_match", "_LEGAL_FORM_ALIASES"),
    ("enrichment.issue_detection", "_NONCANON_TOKENS"),
    ("enrichment.issue_detection", "_POSTAL_FORMATS"),
    ("enrichment.page_corroborator", "IMPRINT_PATHS"),
    ("enrichment.page_corroborator", "_PARKING_MARKERS"),
    ("enrichment.page_corroborator", "_CHALLENGE_MARKERS"),
    ("enrichment.liveness", "GLEIF_DEAD_ENTITY_STATUS"),
    ("enrichment.liveness", "GLEIF_DEAD_REGISTRATION_STATUS"),
    ("enrichment.liveness", "ROR_DEAD_STATUS"),
    ("dedup.candidates", "LEGAL_SUFFIXES"),
    ("dedup.candidates", "CORPORATE_STRUCTURE_WORDS"),
    ("dedup.candidates", "_ACRONYM_STOPWORDS"),
    ("dedup.name_slots", "LOGISTICS_TERMS"),
    ("dedup.name_slots", "_CONTINUATION_NOUNS"),
    ("dedup.name_slots", "_DANGLING_CONNECTORS"),
    ("dedup.address", "STREET_TYPES"),
    ("dedup.address", "DIRECTIONALS"),
    ("search.page_fetcher", "REMOVE_TAGS"),
]
for mod, name in TARGETS:
    print(f"{mod}.{name}\t{len(getattr(importlib.import_module(mod), name))}")
```

---

## 6. Request, orchestration and database parameters

### 6.1 HTTP request options

| Name | Value | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `EnrichmentOptions.max_concurrency` | `5`, constrained `ge=1, le=20` | `api/models.py:306` | Per-request concurrency for `/enrich`. Consumed at `api/routes.py:121`, `:742`. | request |
| `EnrichmentOptions.serp_provider` | `"serpapi"`, one of `serpapi` \| `duckduckgo` | `api/models.py:307` | Search provider for the request. | request |
| `EnrichmentOptions.skip_tier` | `None` | `api/models.py:308` | Skips one tier. | request |
| `EnrichmentRequest.records` | `min_length=1` | `api/models.py:313` | At least one record per request. | request |
| `/enrich/file` `max_concurrency` | `5`, `ge=1, le=20` | `api/routes.py:703` | Query parameter equivalent for the file endpoint. | request |
| `/enrich/file` `serp_provider` | `"serpapi"` | `api/routes.py:704` | Query parameter equivalent. | request |
| `/enrich/file` `skip_tier` | `None` | `api/routes.py:705` | Query parameter equivalent. | request |

### 6.2 ADF pipeline parameters and activity policies

Four pipelines are exported (`adf/enrichment_pipeline.json`,
`adf/issues_pipeline.json`, `adf/deduplication_pipeline.json`,
`adf/scoring_pipeline.json`). All four take the same two parameters and carry
identical activity policies.

| Name | Value | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `chrEntity` | `string`, no default | `adf/enrichment_pipeline.json:157-159`, `adf/issues_pipeline.json:107-109`, `adf/deduplication_pipeline.json:107-109`, `adf/scoring_pipeline.json:107-109` | DS entity the run targets. | ADF |
| `chrGroupCode` | `string`, no default | `adf/enrichment_pipeline.json:160-162`, `adf/issues_pipeline.json:110-112`, `adf/deduplication_pipeline.json:110-112`, `adf/scoring_pipeline.json:110-112` | Group code the run targets. | ADF |
| activity `timeout` | `"0.12:00:00"` | `adf/enrichment_pipeline.json:10`, `:57`, `:92`, `:128`; `adf/issues_pipeline.json:10`, `:45`, `:81`; `adf/deduplication_pipeline.json:10`, `:45`, `:81`; `adf/scoring_pipeline.json:10`, `:45`, `:81` | 12 hours per activity. | ADF |
| activity `retry` | `0` | same lines +1 | No activity-level retry. | ADF |
| activity `retryIntervalInSeconds` | `30` | same lines +2 | Interval that would apply if `retry` were raised. | ADF |
| `secureInput` / `secureOutput` | `false` | same lines +3, +4 | Activity inputs and outputs are logged. | ADF |
| `httpRequestTimeout` | `"00:10:00"` | `adf/enrichment_pipeline.json:104`, `adf/issues_pipeline.json:57`, `adf/deduplication_pipeline.json:57`, `adf/scoring_pipeline.json:57` | 10 minutes per Web activity call. | ADF |
| Lookup `firstRowOnly` | `false` | `adf/enrichment_pipeline.json:30`, `:77`; `adf/issues_pipeline.json:30`; `adf/deduplication_pipeline.json:30`; `adf/scoring_pipeline.json:30` | Lookups return every row. | ADF |
| `ForEach1.isSequential` | `true` | `adf/enrichment_pipeline.json:50` | The enrichment pipeline's batch loop runs one iteration at a time. The other three pipelines have no ForEach. | ADF |
| Web activity URL | `https://mdm-pipeline-api.azurewebsites.net/enrich` | `adf/enrichment_pipeline.json:105` | Enrichment endpoint. | ADF |
| Web activity URL | `https://mdm-pipeline-api.azurewebsites.net/issues/json` | `adf/issues_pipeline.json:58` | Issues endpoint. | ADF |
| Web activity URL | `https://mdm-pipeline-api.azurewebsites.net/api/dedup/cluster-block` | `adf/deduplication_pipeline.json:58` | Clustering endpoint. | ADF |
| Web activity URL | `https://mdm-pipeline-api.azurewebsites.net/api/dedup/score` | `adf/scoring_pipeline.json:58` | Scoring endpoint. | ADF |

### 6.3 Function App host

| Name | Value | file:line | Effect | Who sets it |
|---|---|---|---|---|
| `version` | `"2.0"` | `host.json:2` | Functions runtime major version. | code |
| `logging.applicationInsights.samplingSettings.isEnabled` | `true` | `host.json:6` | App Insights sampling on. | code |
| `samplingSettings.excludedTypes` | `"Request"` | `host.json:7` | Requests are exempt from sampling. | code |
| `extensions.http.routePrefix` | `""` | `host.json:13` | No `/api` route prefix; routes are as declared. | code |
| `extensionBundle.version` | `"[4.*, 5.0.0)"` | `host.json:18` | Extension bundle range. | code |

`host.json` declares no `functionTimeout`, so the platform default for the plan
applies. ⚠ MEASUREMENT REQUIRED — the effective function timeout is a
deployment setting and is not in the repository.

### 6.4 Merge-procedure parameters

| Procedure | Parameters | file:line |
|---|---|---|
| `[Mapping].[usp_MergeLegacyEnriched]` | `@chrEntity SYSNAME`, `@chrGroupCode NVARCHAR(50)`, `@payload NVARCHAR(MAX)` | `sql/usp_merge_legacy_enriched.sql:1-4` |
| `[Mapping].[usp_MergeLegacyIssues]` | `@chrEntity SYSNAME`, `@chrGroupCode NVARCHAR(50)`, `@payload NVARCHAR(MAX)`, `@target_column SYSNAME = N'Issues'` | `sql/usp_merge_legacy_issues.sql:1-5` |
| `[Mapping].[usp_MergeValidationClusters]` | `@chrEntity SYSNAME`, `@chrGroupCode NVARCHAR(50)`, `@payload NVARCHAR(MAX)` | `sql/usp_merge_validation_clusters.sql:1-4` |
| `[Mapping].[usp_MergeValidationScores]` | `@chrEntity SYSNAME`, `@chrGroupCode NVARCHAR(50)`, `@payload NVARCHAR(MAX)` | `sql/usp_merge_validation_scores.sql:1-4` |

`@target_column` is the one parameter carrying a default: `N'Issues'`
(`sql/usp_merge_legacy_issues.sql:5`). Derived values inside the procedures —
the group-code guard pattern and the target table name — are computed, not
tunable:

| Derived value | Expression | file:line |
|---|---|---|
| Group-code pattern | `LTRIM(RTRIM(@chrGroupCode)) + N'\_%'` | `sql/usp_merge_legacy_enriched.sql:27`, `sql/usp_merge_legacy_issues.sql:36`, `sql/usp_merge_validation_clusters.sql:31`, `sql/usp_merge_validation_scores.sql:31` |
| Legacy target | `N'dp_legacy.' + QUOTENAME(@chrEntity) + N'.Legacy'` | `sql/usp_merge_legacy_enriched.sql:29`, `sql/usp_merge_legacy_issues.sql:38` |
| Validation target | `QUOTENAME(@db) + N'.' + QUOTENAME(@chrEntity) + N'.Validation'` | `sql/usp_merge_validation_clusters.sql:33`, `sql/usp_merge_validation_scores.sql:33` |

---

## 7. Run-time-derived parameters

Values fixed per run rather than configured. They change the output and are not
settable.

| Name | Derivation | file:line | Effect |
|---|---|---|---|
| Election reference year | `datetime.date.today().year`, resolved once per election | `dedup/scoring.py:1181` | The origin of both `*_last_used` offset ladders. Not resolved at import (a warm Function App instance alive across New Year would score one batch under two ladders) and not per row (a batch straddling midnight on 31 December would band its first and last rows differently). Recorded per row as `scored_with_reference_year` (`dedup/scoring.py:331`, `:1309`, `:1323`). |
| `scored_with_weights_version` | `weights_version(weights)` | `dedup/scoring.py:1175` | 12-hex digest of the weights table in force. `3147cac47910` at this commit. |
| Cluster year maxima | `max` over cluster members | `dedup/scoring.py:1197-1200` | Count criteria are cluster-context-dependent; single-member and unclustered rows are scored context-free. |
| Dedup cache mode | `off` \| `record` \| `replay` | `dedup/cache.py:170-179` | Written into the output workbook so a run served from a recording is distinguishable from one that called the deployment. |
| `_SEED_SUPPORTED` | `True` until the deployment rejects `seed` | `llm/openai_client.py:114` | Once dropped, `seed` is omitted for the rest of the process and byte-identical re-runs are no longer claimed (`llm/openai_client.py:339-341`). |

---

## 8. Local configuration not in the commit

`.env` is git-ignored (`.gitignore:9`) and is not part of `86d173b`; only
`.env.example` is tracked. The values below are the local run configuration on
the machine this pass was produced on, and are recorded because two of them
differ from the code defaults and because the deployment name appears nowhere in
the repository. Secrets are redacted.

⚠ NOT IN COMMIT — the following is read from an untracked file.

| Variable | Local value | Code default | file:line |
|---|---|---|---|
| `AZURE_OPENAI_API_KEY` | `<redacted>` | `""` | `.env:7` |
| `AZURE_OPENAI_ENDPOINT` | `https://aif-bbio-marketing-reg-sweden.cognitiveservices.azure.com/` | `""` | `.env:8` |
| `AZURE_OPENAI_DEPLOYMENT` | `MDM-Apoorva-gpt-5.4` | `gpt-5.4` | `.env:9` |
| `SERPAPI_KEY` | `<redacted>` | `""` | `.env:12` |
| `ROR_API_BASE` | `https://api.ror.org/v2/organizations` | same | `.env:15` |
| `ROR_CONFIDENCE_THRESHOLD` | `0.8` | same | `.env:16` |
| `FUZZY_MATCH_THRESHOLD` | `80` | same | `.env:19` |
| `MAX_PAGE_CONTENT_CHARS` | `3000` | `1500` | `.env:20` |
| `DEFAULT_MAX_CONCURRENCY` | `5` | same | `.env:21` |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `10` | same | `.env:22` |
| `MOCK_EXTERNAL_CALLS` | `false` | same | `.env:25` |
| `ENV` | `local` | `production` | `.env:26` |
| `LOG_LEVEL` | `INFO` | same | `.env:27` |
| `DEDUP_V2_BLOCKING` | `true` (trailing space; `_TRUTHY` test strips) | unset → off | `.env:31` |
| `DEDUP_V2_NAME2` | `true` (trailing space) | unset → off | `.env:32` |
| `DEDUP_V2_ID_CONFLICT` | `true` (trailing space) | unset → off | `.env:33` |

Every other variable in §1 takes its code default locally. In particular the
three v2 clustering flags, which are off by default in code, are on in this
configuration.

---

## 9. Discrepancies and unknowns

Collected for Pass 08.

**9.1 `MAX_PAGE_CONTENT_CHARS` has two stated defaults.**
`config.py:121` lists `"MAX_PAGE_CONTENT_CHARS": "3000"` and `.env.example:105`
states `MAX_PAGE_CONTENT_CHARS=3000`, while the field that is actually read
defaults to `1500` (`config.py:368`). `OPTIONAL_VARS_WITH_DEFAULTS` is not
consumed by any code path — `grep -rn 'OPTIONAL_VARS_WITH_DEFAULTS'` matches
only its own definition at `config.py:100` — so the effective default is `1500`
and the two documented `3000` values are inert. The local `.env` sets `3000`
(§8), so a run on that machine uses 3000 characters.

**9.2 `DEFAULT_MAX_CONCURRENCY` is reported and never applied.**
`settings.default_max_concurrency` (`config.py:592-593`) is read at exactly one
site, the `/config` diagnostics response (`api/routes.py:1656`, model field
`api/models.py:890`). The concurrency the pipeline actually uses comes from
`EnrichmentOptions.max_concurrency`, whose default is the literal `5` at
`api/models.py:306` and `api/routes.py:703`. Raising `DEFAULT_MAX_CONCURRENCY`
changes the diagnostics output and nothing else.

**9.3 `dept_split_canonicalises` — comment and default disagree.**
The comment block states "Phase 5 — origin-based Tier 2 eligibility. OFF by
default." and "Off by default" (`config.py:257`, `:268`); the field defaults to
`True` (`config.py:290-294`). The same block records that a flip to `True` "was
gated and REVERTED" while the code at this commit has it on. Code wins: the lane
is on at `86d173b`.

**9.4 `UNDECIDABLE_WRITES` parsing is substring-based.**
`(os.getenv("UNDECIDABLE_WRITES") or "").replace("on", "true").replace("off",
"false")` (`config.py:251-252`) rewrites the substring `on` anywhere in the
value, not only the whole token. A value that contains `on` for another reason
is rewritten before `_bool` sees it.

**9.5 Thresholds read from the environment outside `Settings`.**
`ROR_CONFIDENCE_THRESHOLD` (`enrichment/tier1_ror.py:936`), `ROR_API_BASE`
(`enrichment/tier1_ror.py:934`, `:1654`, `enrichment/liveness.py:263`) and
`LEI_NAME_MATCH_THRESHOLD` (`enrichment/orchestrator.py:1776`, `:3100`) are
read from `os.getenv` directly rather than through the `Settings` snapshot. The
`enrichment/orchestrator.py:1776` read happens at import, so a variable set
after the module is imported does not reach `_LEI_NAME_THRESHOLD`.

**9.6 GLEIF defaults are stated in two places.**
`enrichment/tier1_lei.py:567-570` and `:803-806` repeat `base_url`, `timeout`,
`max_retries` and `threshold` as function-signature defaults alongside the
`Settings` values the `LEIClient` passes (`enrichment/tier1_lei.py:919-924`).
Both sets agree at this commit.

**9.7 Environment variables read by code but absent from `.env.example`.**
`AZURE_OPENAI_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`,
`LLM_SSL_VERIFY`, `LLM_HTTP_CONNECT_TIMEOUT`, `LLM_HTTP_TIMEOUT`,
`LLM_FALLBACK_AUTHORITATIVE`, `UNDECIDABLE_WRITES`, `DEPT_SPLIT_CANONICALISES`,
`ACCEPTED_DOMAIN_CITY_WITHDRAWAL_ENABLED`, `NAME_FIELD_WIDTH`, `LOG_FILE`,
`WEBSITE_TRACE`, `RETRY_TRACE`, `DEDUP_V2_BLOCKING`, `DEDUP_V2_NAME2`,
`DEDUP_V2_ID_CONFLICT`, `DEDUP_FIXTURE_CACHE_DIR`, `DEDUP_FIXTURE_CACHE_MODE`.
A reader configuring from `.env.example` alone cannot reach them.

**9.8 `weights.json` carries three unconfirmed values.**
See §4.2. `combined_presence_bonus`, `sales_order_partner_count` and the
`account_group` `DRIT` key are marked UNCONFIRMED in the file itself
(`dedup/weights.json:2`).

**9.9 Function timeout is not in the repository.**
`host.json` sets no `functionTimeout` (`host.json:1-20`). ⚠ MEASUREMENT
REQUIRED — the value in force is an Azure Function App application setting; the
Azure portal or `az functionapp config appsettings list` would supply it.

**9.10 ADF activity retry is 0 on every activity.**
`retry: 0` on all thirteen policy-bearing activities across the four exported
pipelines — four in `adf/enrichment_pipeline.json` and three in each of the
other three (§6.2) — with `retryIntervalInSeconds: 30` present but unreachable. A transient failure
in a Lookup, Web or stored-procedure activity fails the pipeline run.

---

Pass 04 complete: 73 environment-variable rows (§1.1–§1.10) plus 9 CLI flags
that set them (§1.11), 55 module constants (§2), 18 LLM sampling and budget
entries (§3), the full `dedup/weights.json` — last changed at `f5c8d8d`, version
`3147cac47910` (§4), 28 tunable vocabularies with measured sizes (§5), 32
request / ADF / host / SQL parameters (§6), 5 run-time-derived values (§7), the
16 local `.env` settings that are not in the commit (§8), and 10 discrepancies
for Pass 08 (§9).
