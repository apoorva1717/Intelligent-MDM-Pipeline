Generated: 2026-09-07 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 06

# Pass 06 — External dependencies

Every network destination the service reaches, the credentials and transport it
reaches them with, what governs a retry, how the answer is recorded, and the
role the answer is permitted to play in an output value.

## 0 · Conventions

| Marker | Meaning |
| --- | --- |
| `⚠ UNVERIFIED` | Not decidable from an artefact in this repository at this commit. |
| `⚠ MEASUREMENT REQUIRED` | A number that exists only in a live run or an external console. The command or console path that produces it is named. |
| `⚠ NOT PRESENT` | A component referenced outside the repository with no artefact inside it. |

**Role vocabulary.** Three roles are used throughout, and they are properties of
the code, not descriptions:

- **Authority** — the source authored the value written to an output field, and
  the identifier it returned is the evidence. `enrichment/confidence.py:74-76`
  names exactly three: `ror`, `gleif`, `wikidata`, and the third only in its
  crosswalked-registry role.
- **Witness** — the source may corroborate a value another source produced, and
  may raise a value's confidence, but may not author a name.
  `enrichment/confidence.py:95-98` enumerates the six.
- **Crosswalk** — the source supplies a *lookup key* to an authority and nothing
  else. Wikidata alone occupies this role (`enrichment/wikidata.py:11-19`).

**Auth.** Where a service is marked "none", the client constructs its HTTP
client with no credential of any kind; the requests are anonymous.

---

## 1 · Service inventory

| # | Service | Client module | Auth | Transport | Cache namespace | Role |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | ROR v2 | `enrichment/tier1_ror.py:1083`, `enrichment/liveness.py:272` | none | `httpx.AsyncClient` | `ror` → `registry/` | authority |
| 2 | GLEIF v1 | `enrichment/tier1_lei.py:626` | none | `httpx.AsyncClient` | `gleif` → `registry/` | authority |
| 3 | Wikidata Action API | `enrichment/wikidata.py:725` | none (User-Agent only) | `httpx.AsyncClient` | `wikidata` | crosswalk, else witness |
| 4 | SerpAPI (Google) | `search/serpapi_client.py:73` | `api_key` query parameter | `serpapi.GoogleSearch`, sync, thread executor | `serp` | evidence supply |
| 5 | DuckDuckGo | `search/duckduckgo_client.py:53` | none | `duckduckgo_search.DDGS`, sync, thread executor | `serp` | evidence supply |
| 6 | Arbitrary third-party web hosts | `search/page_fetcher.py:229`, `:330`, `:337`, `:351`, `:405`, `:436` | none (User-Agent only) | `requests`, sync, thread executor | `fetch` (URL) above `page` (domain) | witness |
| 7 | Azure OpenAI — Phase 1 enrichment | `llm/openai_client.py:266-276` | `AZURE_OPENAI_API_KEY` | `openai.AsyncAzureOpenAI` over an injected `httpx.AsyncClient` | `llm` | inference, never authority |
| 8 | Azure OpenAI — Phase 2 dedup adjudicator | `dedup/llm.py:178`, `:239` | `AZURE_OPENAI_API_KEY` | same client construction | none by default; opt-in `dedup/cache.py` | adjudicator |
| 9 | Address-validation service (DS workflow step 6) | — | — | — | — | `⚠ NOT PRESENT` (§2.9) |

Four ADF pipelines are exported, and every outbound URL in them addresses the
service's own Function App rather than a third party:
`https://mdm-pipeline-api.azurewebsites.net/enrich`, `/issues/json`,
`/api/dedup/cluster-block`, `/api/dedup/score`
(`adf/enrichment_pipeline.json`, `adf/issues_pipeline.json`,
`adf/deduplication_pipeline.json`, `adf/scoring_pipeline.json`). No exported
pipeline calls an external service directly.

---

## 2 · Per-service detail

### 2.1 · ROR v2

| Property | Value | Evidence |
| --- | --- | --- |
| Base URL | `https://api.ror.org/v2/organizations` | `config.py:102`, `:230`; re-read from `ROR_API_BASE` at each call site `enrichment/tier1_ror.py:934`, `:1654`, `enrichment/liveness.py:263` |
| Auth | none — no header, no key, no `mailto` parameter | `enrichment/tier1_ror.py:1083-1085` (client takes `timeout` and `verify` only) |
| User-Agent | not set; the httpx default is sent | as above — no `headers=` argument on any ROR client |
| Endpoints used | `?affiliation=…`; `?query=…[&filter=locations.geonames_details.country_code:<cc>]`; `/{ror_id}`; `?query=…&all_status=` | `:1168`, `:1409-1414`, `:1667` (`by_id_url`), `enrichment/liveness.py:265` |
| Timeout | `15.0` s, hardcoded, whole-request | `enrichment/tier1_ror.py:1084`, `:1670`; `enrichment/liveness.py:228` (`timeout: float = 15.0`) |
| Retry | **none.** No backoff, no attempt counter, no 429 branch | `:1593-1607` — the only handlers are `RegistryUnavailableFrozen`, `HTTPStatusError`, `Exception`, each returning a miss |
| Query retries (not transport) | one un-filtered re-query when the country-filtered query returns zero items; one acronym-expanded affiliation attempt | `:1427-1432`; `:1092` |
| Rate limit as configured | none declared. The only throttle is batch concurrency (§5.3) | — |
| Cache | namespace `ror`, directory `registry/`, key = URL + sorted query parameters | `utils/cache.py:457-465`, `:174-185`, `:601` |
| What is cached | the raw response body, never the selection decision | `utils/cache.py:584-594`, `:611` |
| `CACHE_FROZEN` | a miss raises `RegistryUnavailableFrozen`; the client returns `{"matched": False, "score": 0.0, "guard_rejections": []}` and does **not** memory-cache it | `utils/cache.py:606-607`; `enrichment/tier1_ror.py:1593-1598`, `:1690-1692`; `enrichment/liveness.py:282-284` |
| Failure mode | fail-open: every exception path returns a miss; the record proceeds to the next tier | `:1599-1607` |
| Role | **authority** | `enrichment/confidence.py:62`, `:74-76`; a ROR-authored name reaches `verified` without a witness at `enrichment/confidence.py:229-234`, and hard rule 2 (`:312-318`) permits a witness-less `verified` only for `REGISTRY_SOURCES` |

Two ROR paths exist beside the name lookup, and both are subject to the same
transport rules:

- **By identifier** (`call_ror_by_id`, `:1618`) — the only caller is the
  Wikidata crosswalk. There is no name score on this path because there is no
  candidate set to rank; the country guard still runs (`_country_ok` at
  `:1705`). A `404` is recorded as an
  empty body rather than an error, because a 404 is the registry's answer
  (`:1676-1680`).
- **Liveness status probe** (`enrichment/liveness.py:265`) — re-asks with
  `all_status=` because ROR's default index omits non-active organisations
  (`config.py:563-567`). One query per distinct `(name, country)` in the batch,
  memoised in `BatchCache` (`utils/cache.py:796-798`, `:822-829`).

### 2.2 · GLEIF / LEI v1

| Property | Value | Evidence |
| --- | --- | --- |
| Base URL | `https://api.gleif.org/api/v1` | `config.py:105`, `:303`; default parameter `enrichment/tier1_lei.py:567`, `:803` |
| Auth | none | `enrichment/tier1_lei.py:626-629` — client carries `timeout`, `verify`, `Accept` only |
| Headers | `Accept: application/vnd.api+json` | `:75`, `:628` |
| Endpoints used | `/lei-records` (filtered), `/lei-records/{lei}`, `/fuzzycompletions` | `:606`, `:765`, `:723` |
| Precise-path filters | `filter[entity.legalName]`, `filter[entity.status]=ACTIVE`, `page[size]=10`, and `filter[entity.legalAddress.country]` when a country is known | `:631-637` |
| Fuzzy-path parameters | `field=entity.legalName`, `q=<name>` | `:726` |
| Timeout | `GLEIF_TIMEOUT_SECONDS`, default `15` s | `config.py:106`, `:305-307`; applied `enrichment/tier1_lei.py:627` via `LEIClient` `:922`, `:947` |
| Retry | `LEI_MAX_RETRIES`, default `2`, exponential backoff `0.5 × 2^(attempt−1)` s | `config.py:108`, `:314-316`; `enrichment/tier1_lei.py:540-561`, backoff at `:556` |
| Retry classification | transport error, or HTTP status `≥ 500`, or a status that cannot be read. **A 429 is not retried** — `transient = status is None or status >= 500` | `:551-552` |
| Call budget | at most `1 + 1 + 5` requests per name: one precise, one `fuzzycompletions`, and up to `_FUZZY_RESOLVE_LIMIT = 5` candidate resolutions | `:95`, `:643`, `:724`, `:762` |
| Fuzzy candidate order | the five **smallest LEIs**, not the first five returned — the truncation is made order-independent | `:749-762` |
| Cache | namespace `gleif`, directory `registry/`, shared with ROR; key = URL + sorted parameters | `utils/cache.py:457-465`, `:601` |
| Retry interaction with cache | retries wrap the cached call, so a recorded response costs no attempts | `enrichment/tier1_lei.py:543-547` |
| `CACHE_FROZEN` | a miss raises `RegistryUnavailableFrozen`; `call_lei` returns `{"matched": False, "strategy": None, "score": 0.0, "guard_rejections": []}`, not memory-cached | `:688-694` |
| Failure mode | fail-open: `HTTPStatusError` and any other exception return `{"matched": False, "error": True}` | `:695-703`; stated as a contract at `:35-37` |
| Role | **authority** | `enrichment/confidence.py:63`, `:74-76` |

The GLEIF `legalName` filter is fulltext rather than exact, so the name
verification guard is mandatory on both paths (`:12-16`, `:22-26`), and the
country guard is mandatory because `fuzzycompletions` cannot be
country-filtered at the API (`:28-33`).

### 2.3 · Wikidata (MediaWiki Action API)

| Property | Value | Evidence |
| --- | --- | --- |
| Base URL | `https://www.wikidata.org/w/api.php` | `config.py:152`, `:507-511` |
| SPARQL endpoint | deliberately not used anywhere in the lane | `config.py:503-506`; `enrichment/wikidata.py:47-50` |
| Auth | none | `enrichment/wikidata.py:725-729` |
| User-Agent | `BrukerMDM-EnrichmentAPI/1.0 (Wikidata crosswalk lane)` | `:697` |
| Calls per record | two: one `wbsearchentities`, one batched `wbgetentities` over every search hit; plus a conditional, batch-shared third call resolving referenced items (`P159` headquarters, `P1366` successor) | `:70-82`, `:784-794`, `:834-842` |
| Search parameters | `action=wbsearchentities`, `language=en`, `uselang=en`, `type=item`, `limit=WIKIDATA_SEARCH_LIMIT` (default `5`), `format=json` | `:786-794`; `config.py:155`, `:525-527` |
| Entity parameters | `action=wbgetentities`, `ids=<up to 50, pipe-joined>`, `props=labels|aliases|claims`, `languages=en`, `format=json` | `:834-842` |
| Timeout | `WIKIDATA_TIMEOUT_SECONDS`, default `10` s | `config.py:153`, `:515-517`; applied `:726` |
| Retry | `WIKIDATA_MAX_RETRIES`, default `2` | `config.py:154`, `:518-520`; `:743` |
| Retry classification | transport error, status `≥ 500`, **or `429`** — the only client in the repository that treats a 429 as transient | `:741` |
| Backoff | `Retry-After` header when present, capped at `_MAX_RETRY_AFTER_SECONDS = 30.0` s; otherwise `base × 2^(attempt−1)` with `base = _RATE_LIMIT_BACKOFF_SECONDS = 5.0` for a 429 and `0.5` otherwise | `:663-682`, `:655`, `:660` |
| Rate-limit derivation | the 5.0 s base is measured, not assumed: a 100-row run at concurrency 3 took `HTTPStatusError:429` on 28 of 68 invocations under GLEIF's 0.5 s schedule | `:648-654` |
| Malformed body | not retried | `:748-751` |
| Cache | namespace `wikidata`, directory from `WIKIDATA_FIXTURE_DIR` (default `tests/fixtures/wikidata`); keys `search:<normalised query>` and `entity:<QID>`, each QID cached separately | `config.py:156`, `:533-535`; `enrichment/orchestrator.py:4117-4121`; `enrichment/wikidata.py:785`, `:823`, `:854` |
| Recorded form | entities are recorded **pruned**, and the in-memory value is the pruned one too, so a fixture hit and a live fetch feed the gauntlet identical input | `:850-855` |
| `CACHE_FROZEN` | a miss raises `WikidataUnavailable("replay_only_miss")`; the lane counts `unavailable` and returns `False` | `:760-763`, `:832-833`; `enrichment/orchestrator.py:6171-6173` |
| Failure mode | fail-closed *as a lane*, fail-open *for the record*: a timeout, non-200 or malformed body is `UNAVAILABLE`, counted apart from a real miss, and never fails the record | `:89-93`, `:224`; `enrichment/orchestrator.py:6156-6162` |
| Counters | `calls` (HTTP requests including retries) and `operations` (logical API calls) are kept apart | `:705-714` |
| Role | **crosswalk**, else **witness** — never authority | see below |

**Role enforcement, cited.** Three separate guards hold the line:

1. The lane runs only on a record holding no registry identifier; the
   precondition is re-checked at the call site rather than trusted —
   `enrichment/orchestrator.py:6136-6137`.
2. On a pointer (`P6782` / `P1278`) the lane re-queries ROR or GLEIF *by that
   identifier* and the registry writes the values, with the registry's own
   provenance; nothing in the record names Wikidata
   (`enrichment/orchestrator.py:6208-6219`, `:6240-6247`). An item that carried
   a pointer the registry then refused is **not** promoted to a witness
   (`:6222-6227`).
3. Without a pointer, `_write_wikidata_witness` writes at most
   `operating_name` and a corroboration marker; `name1_enriched` is not written
   (`enrichment/orchestrator.py:6405-6434`, explicitly at `:6415-6416`).

`SOURCE_WIKIDATA` appears in `REGISTRY_SOURCES` (`enrichment/confidence.py:74-76`)
only in the crosswalked role, and `compute_confidence` never returns a
witness-less `verified` for a `wikidata` source that did not route to a registry
(`enrichment/confidence.py:70-76`, `:229-234`).

A second, separately-flagged mode exists: on a record that *already* holds a
registry identity, the lane runs corroboration-only and retains nothing but the
`P856` website claim — one search plus one entity fetch, gated on
`WIKIDATA_DOMAIN_CORROBORATION` (`config.py:151`, `:482-502`;
`enrichment/orchestrator.py:6436-6505`).

### 2.4 · SerpAPI

| Property | Value | Evidence |
| --- | --- | --- |
| Library | `google-search-results>=2.4.2`, imported as `serpapi.GoogleSearch` | `requirements.txt:11`; `search/serpapi_client.py:9` |
| Auth | `SERPAPI_KEY`, sent as the `api_key` **query parameter** | `config.py:218`; `search/serpapi_client.py:65` |
| Request parameters | `q`, `num`, `api_key`, `engine=google`, and `gl=<alpha-2 lowercase>` when a country resolved | `:62-72` |
| `hl` (language) | deliberately not sent | `:37-40` |
| Geo localisation switch | `SERP_COUNTRY_LOCALISATION_ENABLED`, default true, read per call rather than captured at import | `config.py:355-359`; `utils/cache.py:690-702` |
| Transport | synchronous library run in the default thread executor | `search/serpapi_client.py:44-49` |
| Timeout | none set by this code; whatever the library defaults to | `:73-74` — `GoogleSearch(params).get_dict()` takes no timeout |
| Retry | none | `:50-57` — any exception becomes `SearchUnavailable` |
| Rate limit as configured | none in code. The account plan is the limit and is not in the repository | `⚠ MEASUREMENT REQUIRED` — SerpAPI account dashboard for the key in `SERPAPI_KEY` |
| Cache | namespace `serp`, key = normalised query + quoted-flag + country | `utils/cache.py:150-160`, `:168-171`; wired `enrichment/orchestrator.py:4178-4180` |
| Single entry point | `utils.cache.cached_serp` is the only way a search is issued; nine call sites route through it | `utils/cache.py:705-762`; call sites `enrichment/tier2b_dept.py:220`, `enrichment/lab_resolver.py:79`, `enrichment/tier2a_contact.py:337`, `enrichment/person_affiliation.py:133`, `enrichment/website_resolver.py:944`, `enrichment/grounded_resolver.py:372`, `enrichment/orchestrator.py:4902`, `:5036` |
| `CACHE_FROZEN` | a miss returns `[]` and issues no call | `utils/cache.py:744-745`; `BatchCache.serp_frozen` `:839-842` |
| Failure semantics | a `SearchUnavailable` returns `[]` and records **nothing** — a dropped connection must not become a durable "this organisation has no web presence" | `search/base.py:9-22`; `utils/cache.py:753-759` |
| Provider selection | SerpAPI when `SERPAPI_KEY` is non-empty after stripping, else DuckDuckGo | `enrichment/orchestrator.py:4277-4288`; warned at startup `config.py:195-203` |
| Role | **evidence supply.** A SERP result is a candidate URL and a snippet; it authors no output field. What a search result may become is decided by `utils.domain_resolver.resolve_domain` | `utils/domain_resolver.py:518-577` |

**Role enforcement, cited.** A domain that arrives from a search result is not
attributed to the organisation unless it clears one of five ranked conditions:
registry provenance, an independent witness stating the same website, name
similarity at `DOMAIN_NAME_MATCH_THRESHOLD` (default `82`), the record's own
non-generic email domain, on-domain search evidence, or page identity — and
page identity accepts at `provisional` only, never `verified`, because a page
fetched from the domain it vouches for is one source and not two
(`utils/domain_resolver.py:538-568`; threshold `config.py:327-329`). A ccTLD
country conflict disqualifies before any scored condition is consulted
(`utils/domain_resolver.py:529-536`; gate flag `config.py:345-349`).

### 2.5 · DuckDuckGo

| Property | Value | Evidence |
| --- | --- | --- |
| Library | `duckduckgo-search>=6.0.0`, imported as `duckduckgo_search.DDGS` | `requirements.txt:12`; `search/duckduckgo_client.py:9` |
| Auth | none | `:53` — `DDGS()` takes no credential |
| Request | `ddgs.text(query, max_results=num_results)` | `:54` |
| `country` | accepted and **not used**: DDG's control is `region`, a country-language pair, and the language half would have to be invented | `:28-35` |
| Timeout / retry | none set by this code | `:51-61` |
| Rate limit as configured | none | — |
| Cache, freeze, failure semantics | identical to SerpAPI — the same `cached_serp` seam and the same `serp` namespace | `utils/cache.py:705-762` |
| When used | only when `SERPAPI_KEY` is empty | `enrichment/orchestrator.py:4280-4288` |
| Role | **evidence supply**, as §2.4 | — |

### 2.6 · Arbitrary third-party web hosts (page fetch)

The only dependency whose set of hosts is not fixed: the pipeline fetches
whatever domain a registry, a search result or a probe produced.

| Property | Value | Evidence |
| --- | --- | --- |
| Library | `requests>=2.31.0`, synchronous, run in the default thread executor | `requirements.txt:5`; `search/page_fetcher.py:20`, `:202`, `:270`, `:295`, `:315`, `:380` |
| Auth | none | — |
| User-Agent | `BrukerMDM-Enrichment/1.0` on every request | `:232`, `:332`, `:339`, `:355`, `:408`, `:439` |
| `robots.txt` | not read anywhere in the repository | `grep -rn "robots" --include="*.py" .` returns zero hits |
| Request kinds | `GET` with redirects (`:229`), `HEAD` probe with a streamed-`GET` fallback (`:330`, `:337`), `HEAD` subdomain existence probe (`:351`), link-scrape `GET` (`:405`), structured-content `GET` (`:436`) |
| Timeout | `PAGE_FETCH_TIMEOUT_SECONDS`, default `10` s, overridable per call; `5` s for the two HEAD probes; `PAGE_READ_TIMEOUT_SECONDS`, default `8` s, for a corroboration fetch | `config.py:138`, `:370-372`; `search/page_fetcher.py:284`, `:308`; `config.py:142`, `:443-445` |
| Retry | none | every `_sync_*` method catches and returns a failure value |
| TLS | **inconsistent by design.** The two HEAD probes and the link scrape pin `verify=certifi.where()` (`:333`, `:340`, `:356`, `:409`); `_sync_fetch_result` (`:229-241`) and `_sync_fetch_structured` (`:436-440`) pass **no** `verify` and therefore honour `REQUESTS_CA_BUNDLE`, which `config._sanitize_ssl_env` points at the corporate bundle. The comment at `:234-240` records the measurement behind the split: pinning certifi failed 47 of 54 domains on the chemspeed batch behind a TLS-inspecting VPN |
| Content extraction | title, H1, breadcrumb, body text truncated at `max_chars`, and the `<footer>` tail kept separately at `_FOOTER_MAX_CHARS = 600` | `:444-491`, `:35` |
| Cache — two layers | `fetch` namespace keyed on the URL plus an operation prefix (`result:`, `content:`, `head:`, `final:`, `links:`); above it the corroborator's `page` namespace keyed on the registrable **domain** | `search/page_fetcher.py:221`, `:279`, `:303`, `:322`, `:390`; `enrichment/orchestrator.py:4134`, `:4167-4171`; `enrichment/page_corroborator.py:272-281` |
| `CACHE_FROZEN` | each entry point returns its own "could not look" value and issues no request: `PageFetchResult(error="frozen_miss")`, `None`, `False`, `[]`; the corroborator returns `error: "replay_only_miss"` | `search/page_fetcher.py:157-158`, `:222`, `:281`, `:305`, `:396`; `enrichment/page_corroborator.py:283-288` |
| Per-domain request budget | one root read plus at most one imprint page — the loop stops at the first 2xx over `IMPRINT_PATHS = ("/impressum", "/legal", "/about", "/contact")`, so at most five requests reach a domain | `enrichment/page_corroborator.py:89`, `:290-327` |
| Feature flag | `PAGE_CORROBORATION_ENABLED`, default true; off means the step does not run and no page is fetched | `config.py:140`, `:406-410` |
| Failure semantics | `PageFetchResult` keeps the status, so "could not look" (401/403/429/451, or a transport error) is distinguishable from a 404 | `search/page_fetcher.py:90-112` |
| Role | **witness** | `enrichment/confidence.py:78` (`WITNESS_WEB`), `:250-257` (`web:<domain>` source token) |

**Role enforcement, cited.** A page-derived name is written to
`operating_name`, not to `name1_enriched`
(`enrichment/orchestrator.py:6410-6411` describes the field's purpose), and the
name it states must reach `PAGE_NAME_MATCH_THRESHOLD` (default `88`) against
Name 1 before the page counts as naming the organisation at all
(`config.py:141`, `:435-437`). The threshold is a separate knob from
`LEI_NAME_MATCH_THRESHOLD` and deliberately identical to it, reusing
`enrichment.tier1_lei._name_match_score` verbatim (`config.py:411-422`).

### 2.7 · Azure OpenAI — Phase 1 enrichment

| Property | Value | Evidence |
| --- | --- | --- |
| Backend | Azure OpenAI is the only LLM backend; there is no direct-OpenAI or local path | `llm/openai_client.py:3-8` |
| Endpoint | `AZURE_OPENAI_ENDPOINT`, required, no default | `config.py:95-98`; `llm/openai_client.py:250`, raising at `:256-261` |
| Endpoint value | not in the repository | `⚠ MEASUREMENT REQUIRED` — Azure portal, or the Function App's Application Settings |
| Deployment | `AZURE_OPENAI_DEPLOYMENT`, default `gpt-5.4` | `config.py:101`, `:215`; `llm/openai_client.py:310`, `:381` |
| Deployment in use | `MDM-Apoorva-gpt-5.4`, model `gpt-5.4-2026-03-05` | `determinism_findings.md:289` — a repository artefact recording a live probe, not a code constant |
| Auth | `AZURE_OPENAI_API_KEY`, passed to `AsyncAzureOpenAI` | `llm/openai_client.py:249`, `:271-276` |
| REST API version | `AZURE_OPENAI_API_VERSION`, else `DEFAULT_AZURE_OPENAI_API_VERSION = "2024-08-01-preview"` | `:81`, `:251-255` |
| Connect timeout | `LLM_HTTP_CONNECT_TIMEOUT`, default `30` s | `:264`, `:268` |
| Read timeout | `LLM_HTTP_TIMEOUT`, default `60` s | `:265`, `:268` |
| Proxy | `trust_env=True`, so `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` are honoured | `:269`, documented `:238-241` |
| Transport retry | none set by this code. `AsyncAzureOpenAI` is constructed without `max_retries`, so the SDK default applies — `openai==2.30.0` in the local environment reports `DEFAULT_MAX_RETRIES = 2` | `:271-276`; `python3 -c "from openai._constants import DEFAULT_MAX_RETRIES; print(DEFAULT_MAX_RETRIES)"` → `2`. `⚠ UNVERIFIED` against the deployed environment's resolved version |
| Application retry | one, and only for malformed JSON: `extract_json` retries once, then raises `ValueError` | `:462`, `:476-483` |
| Sampling parameters | `temperature = LLM_TEMPERATURE = 0.0`, `top_p = LLM_TOP_P = 1.0`, `seed = LLM_SEED = 42`, `response_format = {"type": "json_object"}`, `max_completion_tokens = max_tokens` | `:102-108`, `:308-322` |
| Why module constants | a reproducibility control that can be changed per environment is not a control | `:88-92` |
| `seed` fallback | if the deployment rejects `seed`, it is dropped **once, process-wide**, logged, and the call retried without it; never re-probed | `:110-114`, `:329-344`; `reset_seed_support` is test-only `:122-125` |
| Rate limit as configured | none in code. The deployment's TPM/RPM quota is the limit and is not in the repository | `⚠ MEASUREMENT REQUIRED` — Azure AI Foundry deployment blade for `MDM-Apoorva-gpt-5.4` |
| Cache | namespace `llm`; key = SHA-256 over deployment, API version, `temperature`, `top_p`, `seed`, `max_tokens` and **both prompts verbatim** | `utils/cache.py:188-218`; taken `llm/openai_client.py:440-452` |
| Why the model is cached at all | measured: with `seed` accepted and sent, two warm runs of the chemspeed batch still differed on 10 of 100 rows, every one an LLM decision; the deployment returns no `system_fingerprint` | `llm/openai_client.py:424-433`; `determinism_findings.md:288-298` |
| What is stored | the parsed response plus a 160-character system-prompt head and a 400-character user-prompt head, for legibility only | `llm/openai_client.py:485-494` |
| `CACHE_FROZEN` | a miss raises `LLMUnavailableFrozen`; every caller of `extract_json` already treats an exception as "the model did not answer" | `:359-367`, `:456-459` |
| Prompt-edit semantics | editing a prompt template invalidates every entry that used it, by construction | `utils/cache.py:205-207` |
| Role | **inference, never authority** | see below |

**Role enforcement, cited.** Two rules, both mechanical:

- Hard rule 1 — `llm` as a source can never be `verified`, and `+llm` as a
  witness can never carry a value to `verified`.
  `enrichment/confidence.py:299-310` raises `ProvenanceGrammarError` on either,
  and `render` validates on the way out, at the site that built the string
  (`:260-271`). `NON_CORROBORATING_WITNESSES` is a set rather than an `is llm`
  check so an added model-ish witness must be classified deliberately (`:100-103`).
- Every Name 1 / Name 2 candidate, whatever produced it, passes one write gate
  that recomputes its checks from the record rather than trusting the producing
  lane — `enrichment/name_gate.evaluate` (`enrichment/name_gate.py:171-200`),
  including the identity verdict, which stays enabled on every path where a
  model proposed the text (`:191-199`).

### 2.8 · Azure OpenAI — Phase 2 dedup adjudicator

| Property | Value | Evidence |
| --- | --- | --- |
| Client construction | reuses `llm.openai_client.get_openai_client`; it does not build a new client | `dedup/llm.py:3-4`, `:178` |
| Deployment | `AOAI_DEPLOYMENT_DEDUP`, else `AZURE_OPENAI_DEPLOYMENT`, else `gpt-5.4` | `:143-147` |
| REST API version | `AOAI_API_VERSION_DEDUP`, else `AZURE_OPENAI_API_VERSION`, else `DEFAULT_API_VERSION = "2025-04-01-preview"` | `:132`, `:150-154` |
| Why a newer version | GPT-5.x reasoning models and `reasoning_effort` require it | `:128-131` |
| Auth, timeouts, proxy, TLS | identical to §2.7 — the same `get_openai_client` | `llm/openai_client.py:262-276` |
| Sampling parameters | `max_completion_tokens` (caller default `4000`), `response_format={"type":"json_object"}`, `top_p = LLM_TOP_P`, `seed = LLM_SEED`, `reasoning_effort = DEDUP_REASONING_EFFORT` (default `low`) | `:195`, `:208-227`, `:148` |
| Temperature | `TEMPERATURE = 0.0`, sent **only when `reasoning_effort` is not in play** — the two are mutually exclusive on a reasoning deployment | `:138`, `:226-237`, rationale `:9-15`, `:228-235` |
| Runtime parameter fallbacks | `reasoning_effort`, `temperature` and `seed` are each dropped once, at the first rejection, and the call retried | `:255-280` |
| Application retry | `DEDUP_MAX_RETRIES`, default `3` attempts, exponential backoff `0.5 × 2^attempt` s | `:149`, `:281-288` |
| Retry classification | `APIConnectionError` / `APITimeoutError`, `429`, or `5xx` | `:69-80` |
| Failure mode | never raises: an exhausted retry returns `DedupLLMResult(error=…)` and the caller marks the affected signatures uncertain | `:197-201`, `:292` |
| Telemetry captured | `prompt_tokens`, `completion_tokens`, `latency_ms`, `model_version` from `response.usage` and `response.model` | `:83-92`, `:240-249` |
| Concurrency | `DEDUP_MAX_CONCURRENCY`, default `5`, one semaphore shared across all blocks in a request | `dedup/adjudicator.py:40`, `:1468-1469` |
| Cache | **not the evidence cache.** Phase 2 is outside `CACHE_FROZEN` entirely | `dedup/llm.py` imports nothing from `utils.cache` |
| Opt-in cache | `dedup/cache.py`, off unless `DEDUP_FIXTURE_CACHE_DIR` is set; modes `record` (default) and `replay` | `dedup/cache.py:16-32`, `:49-50`, `:170-189`; wired `api/routes.py:1089`, `:1097` |
| Replay semantics | a `replay`-mode miss raises `ReplayMiss` rather than calling the model | `dedup/cache.py:53-54`, `:132-136` |
| Errors not cached | a 429 or a socket timeout is a fact about the afternoon, not about the question | `dedup/cache.py:27-28` |
| Mode reported in output | `current_mode()` returns `off` / `record` / `replay` and is written to the output workbook | `dedup/cache.py:170-179`; `api/routes.py:1237` |
| Role | **adjudicator** — the verdict decides a merge; nomination never merges | `config.py:129-137` (nomination is nominate-only), `:603-613` |

### 2.9 · Address-validation service — not present in this repository

DS workflow step 6 performs address validation with auto write-back above 80%
confidence (`docs/thesis/CONTEXT-EXTERNAL.md:423`). The ADF pipeline is not
exported (`:423`, `:442`), and the service is not reachable from any code in
this repository: the address stage in `enrichment/address_processing.py` makes
exactly one outbound call, to the LLM (`enrichment/address_processing.py:771`),
and no other.

- Identity of the service — `⚠ UNVERIFIED`.
- The `80%` threshold, and whether the comparison is `>` or `≥` — `⚠ UNVERIFIED`.
- Both are settled by the same artefact: an export of the ADF pipeline for
  workflow step 6 from the Tillit tenant, in the form of the two pipelines at
  `docs/thesis/CONTEXT-EXTERNAL.md:39-315`.

---

## 3 · The role hierarchy, and the code that enforces it

| Source | May author a name? | May reach `verified`? | Enforcing code |
| --- | --- | --- | --- |
| ROR | yes | yes, witness-less | `enrichment/confidence.py:74-76`, `:229-234`, `:312-318` |
| GLEIF | yes | yes, witness-less | as above |
| Wikidata — with a registry pointer | no; the **registry** authors, from the pointer | yes, as `ror` / `gleif` with witness `wikidata` | `enrichment/orchestrator.py:6208-6219`, `:6240-6247`; `enrichment/confidence.py:233` |
| Wikidata — without a pointer | no; `operating_name` only | no | `enrichment/orchestrator.py:6405-6434`, esp. `:6415-6416` |
| Page read | no; `operating_name` only | as a witness (`web`), yes; as the sole source vouching for its own domain, `provisional` only | `enrichment/confidence.py:78`, `:250-257`; `utils/domain_resolver.py:559-568` |
| SERP result | no | no — it supplies a candidate, and `resolve_domain` decides | `utils/domain_resolver.py:518-577` |
| LLM | yes, under the gate | **never** | `enrichment/confidence.py:299-310`; `enrichment/name_gate.py:171-200` |
| Record input | — | only with a witness | `enrichment/confidence.py:312-318` |

`validate()` is the single mechanical check: it parses the provenance string
and raises unless the grammar and hard rules 1–2 both hold
(`enrichment/confidence.py:290-318`). Hard rule 3 — rejected evidence never
appears — is not checkable from the string and is enforced at the adapter
(`:293-295`).

---

## 4 · `CACHE_FROZEN` — one switch, seven namespaces

`CACHE_FROZEN` (default `false`, `config.py:145`, `:392-401`) turns a cache
miss into a recorded error rather than a network call. It is set once, on the
`EvidenceCache`, and reaches every namespace at once — a lane may freeze itself,
but nothing can un-freeze a frozen run (`utils/cache.py:467-469`, `:492-505`).

| Namespace | Directory | Key | Frozen-miss behaviour | Evidence |
| --- | --- | --- | --- | --- |
| `page` | `page_reads/` | registrable domain | payload with `error: "replay_only_miss"` | `enrichment/page_corroborator.py:283-288` |
| `wikidata` | `wikidata/` | `search:<normalised query>` / `entity:<QID>` | raises `WikidataUnavailable("replay_only_miss")` | `enrichment/wikidata.py:760-763`, `:832-833` |
| `serp` | `serp/` | normalised query + quoted-flag + country | returns `[]`, no call | `utils/cache.py:744-745` |
| `fetch` | `fetch/` | URL, prefixed by operation | method-specific "could not look" value | `search/page_fetcher.py:157-158` |
| `llm` | `llm/` | SHA-256 of deployment + API version + sampling params + both prompts | raises `LLMUnavailableFrozen` | `llm/openai_client.py:456-459` |
| `ror` | `registry/` | URL + sorted query parameters | raises `RegistryUnavailableFrozen` → clean miss | `utils/cache.py:606-607`; `enrichment/tier1_ror.py:1593-1598` |
| `gleif` | `registry/` | as `ror` | as `ror` | `enrichment/tier1_lei.py:688-694` |

Layout at `utils/cache.py:457-465`. Root directory `EVIDENCE_CACHE_DIR`, default
`tests/fixtures`; an empty string means memory-only, and the empty string and
`None` are deliberately different answers (`config.py:76-90`, `:389-391`;
`utils/cache.py:497-502`).

Four properties hold across every namespace:

1. **No key contains a run id, a batch id, a date or a record id**
   (`utils/cache.py:43-46`). Normalisation is
   `dedup.signatures.normalize_key`, reused rather than reimplemented
   (`:55-59`), and country is part of every key (`:71-74`).
2. **The normalised key is a lookup key only.** The value sent to ROR, GLEIF or
   the SERP provider is always the original, unnormalised string
   (`utils/cache.py:61-69`; pinned by `tests/test_cache_normalisation.py`).
3. **Responses are recorded, not decisions.** `cached_registry_get` records the
   raw body, so a change to the selection rules is re-applied on every run
   instead of being frozen with the evidence (`utils/cache.py:584-594`). The
   registry clients' own `_ror_cache` / `_lei_cache` hold *decisions*, are
   memory-only and die with the batch (`enrichment/tier1_ror.py:978-988`;
   `enrichment/tier1_lei.py:608-616`).
4. **Entries are immutable.** `DiskCache.set` is a no-op when the key is already
   on disk, so `fetched_at` keeps naming the day the evidence was gathered
   (`utils/cache.py:76-83`, `:385-401`).

**Coverage boundary.** `CACHE_FROZEN` governs Phase 1 only. Phase 2
adjudication has its own, separately-switched record/replay store
(`dedup/cache.py`, §2.8), which is off unless `DEDUP_FIXTURE_CACHE_DIR` is set.
Freezing a Phase 1 run does not freeze a `/api/dedup/cluster-block` run.

**Instrumentation.** `EvidenceCache.note_network_call` counts every point at
which the pipeline reached a source rather than a recording
(`utils/cache.py:520-524`), called from `cached_registry_get`
(`:608`), `cached_serp` (`:746`), `PageFetcher._cached` (`search/page_fetcher.py:159`),
`WikidataClient._get` (`enrichment/wikidata.py:733`) and
`OpenAIClient.extract_json` (`llm/openai_client.py:463`). The batch summary
carries `evidence_network_calls`, `evidence_network_calls_by_namespace`,
`evidence_frozen_misses`, `evidence_cache_hits` and `evidence_cache_frozen`
(`api/models.py:806-814`; set at `enrichment/orchestrator.py:4411-4421`). A
warm second run is expected to report `evidence_network_calls == 0`
(`enrichment/orchestrator.py:4407-4410`).

---

## 5 · Transport, credentials and throttling

### 5.1 · TLS trust resolution

One function resolves `verify` for the LLM client and is reused by ROR, GLEIF,
Wikidata and the liveness probe: `llm.openai_client.resolve_tls_verify`
(`llm/openai_client.py:184-228`), imported at `enrichment/tier1_ror.py:33`,
`enrichment/tier1_lei.py:63`, `enrichment/wikidata.py:114`.

Order of resolution (`:201-228`):

1. `LLM_SSL_VERIFY=false` disables verification entirely, logged at WARNING.
2. Otherwise the first existing file among `AZURE_OPENAI_CA_BUNDLE`,
   `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE` (`:163`).
3. Otherwise `certifi.where()`.

Built `SSLContext` objects are cached by resolved path (`:181`, `:219-227`),
because rebuilding one per lookup was measured at 124 s against 1.7 s on a fully
warm 100-record batch (`:173-180`).

Separately, `config._sanitize_ssl_env` runs at import and overrides
`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` when they point at a non-existent path,
preferring a configured corporate bundle and falling back to certifi
(`config.py:27-43` for the rationale, `:52-64` for the replacement logic,
invoked unconditionally at `config.py:67`). This is what makes the SerpAPI
library — which cannot be passed a `verify` — survive the same environment
(`config.py:41-43`).

`certifi` is imported directly by `config.py:14`, `llm/openai_client.py:20` and
`search/page_fetcher.py:19` but is **not declared** in `requirements.txt`; it
arrives transitively through `requests` / `httpx`. Recorded for Pass 08.

### 5.2 · Credential handling

| Credential | Source | How it travels | Evidence |
| --- | --- | --- | --- |
| `AZURE_OPENAI_API_KEY` | environment | SDK header | `llm/openai_client.py:249`, `:272` |
| `SERPAPI_KEY` | environment | **URL query parameter** `api_key` | `search/serpapi_client.py:65` |
| ROR, GLEIF, Wikidata, page fetch | — | anonymous | §2.1–§2.3, §2.6 |

`AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` are the only required
variables; `validate_env` warns rather than raising, so the app starts and
health checks answer while LLM calls fail (`config.py:95-98`, `:180-193`). A
missing `SERPAPI_KEY` is a warning and a silent downgrade to DuckDuckGo
(`config.py:195-203`; `enrichment/orchestrator.py:4284-4288`). `.env` is
git-ignored (`.gitignore:9`), so no live endpoint or key is in the repository.

The SerpAPI key appearing in a query string means it is present in any
URL-level log or proxy record on the path. Recorded for Pass 08.

### 5.3 · Throttling as configured

No client in the repository implements a rate limiter, a token bucket or a
request budget per unit time. Three concurrency caps are the only throttle:

| Cap | Default | Bounds | Evidence |
| --- | --- | --- | --- |
| `EnrichmentOptions.max_concurrency` | `5` | `ge=1, le=20` | `api/models.py:306`; passed `api/routes.py:121`, `:742` |
| `DEFAULT_MAX_CONCURRENCY` | `5` | — | `config.py:122` (declared), `:592-594` |
| `DEDUP_MAX_CONCURRENCY` | `5` | `max(1, n)` | `dedup/adjudicator.py:40`, `:1468-1469` |

Two per-record call budgets are set in code rather than by a limiter:

| Budget | Value | Evidence |
| --- | --- | --- |
| GLEIF fuzzy candidate resolutions | `_FUZZY_RESOLVE_LIMIT = 5` | `enrichment/tier1_lei.py:95`, `:762` |
| Wikidata API calls per record | 2, plus one conditional batch-shared reference call | `enrichment/wikidata.py:70-82` |
| Department-domain probe SERP calls | 1 unless `DEPT_PROBE_CROSS_DOMAIN` is on, which adds the cross-domain fallback query | `config.py:166`, `:219-226` |
| Adjudicator calls per block | `MAX_CANDIDATES_PER_BLOCK = 50`; over the cap the block routes to `manual_review` | `config.py:137`, `:611-613` |

---

## 6 · Coupling — what happens when each service is unavailable

Every lane is fail-open for the record: no external failure fails a record.

| Service | Immediate behaviour | Consequence for the output |
| --- | --- | --- |
| ROR | miss dict returned from any exception path (`enrichment/tier1_ror.py:1599-1607`) | no `ror_id`; the record falls to the next tier; institution identities go unresolved |
| GLEIF | `{"matched": False, "error": True}` (`enrichment/tier1_lei.py:695-703`) | no `lei_id`; the company branch falls through to LLM canonicalisation, as `:35-37` states it must |
| Wikidata | `UNAVAILABLE`, counted apart from a real miss (`enrichment/wikidata.py:89-93`); the lane returns `False` (`enrichment/orchestrator.py:6171-6173`) | no crosswalk, no witness; the record continues down the waterfall unchanged |
| SerpAPI / DuckDuckGo | `SearchUnavailable` → `[]`, and **nothing recorded** (`utils/cache.py:753-759`) | no web candidates; website and department resolution lose their input |
| Page fetch | status-bearing failure result; 401/403/429/451 read as "could not look" (`search/page_fetcher.py:110-112`) | no corroboration; a domain that would have accepted on page identity is refused instead |
| Azure OpenAI — Phase 1 | `call_openai` raises `RuntimeError` (`llm/openai_client.py:346-347`); each tier catches and escalates | Tier 2/3 produce no value; the record ships its input, flagged |
| Azure OpenAI — Phase 2 | `DedupLLMResult(error=…)` after retries (`dedup/llm.py:292`) | affected signatures are marked uncertain; the block is not failed |

The single-point dependency is Azure OpenAI: it is the only service with no
alternative provider and no rule-based fallback. Search has two providers; the
registries each cover a different population; Wikidata and the page read are
both optional evidence on paths that have an answer without them.

---

## 7 · Call volume

### 7.1 · Structural per-record bounds, from code

These are ceilings on a cold cache, not observed means. A record does not
reach every tier — the ladder escalates only on a miss — so the column may not
be summed to a per-record figure.

| Source | Bound per record | Evidence |
| --- | --- | --- |
| ROR | 1 affiliation + 1 acronym-expanded affiliation + 1 filtered query + 1 unfiltered query = 4; plus 1 by-id per crosswalk; plus 1 liveness probe per distinct `(name, country)` in the batch | `enrichment/tier1_ror.py:1092`, `:1409-1432`, `:1663`; `enrichment/liveness.py:265` with `BatchCache` memo `utils/cache.py:796-798` |
| GLEIF | 1 precise + 1 fuzzycompletions + 5 candidate resolutions = 7 | `enrichment/tier1_lei.py:643`, `:724`, `:762` |
| Wikidata | 2, plus 1 conditional batch-shared reference call | `enrichment/wikidata.py:70-82` |
| SERP | 1 per distinct query across nine call sites; the department probe is 1 unless `DEPT_PROBE_CROSS_DOMAIN` is on | `utils/cache.py:705`; `config.py:219-226` |
| Page fetch | 5 per domain in the corroborator (root + first-2xx imprint probe over 4 paths), plus the probe/scrape reads of the website and department lanes | `enrichment/page_corroborator.py:89`, `:290-327` |
| LLM Phase 1 | one per tier invoked; the count is a property of which tiers the record reached | Pass 03 |

### 7.2 · Observed volume

`⚠ MEASUREMENT REQUIRED.` The instrumentation now exists and no code change is
needed: run one batch of known `N` and read `evidence_network_calls` and
`evidence_network_calls_by_namespace` off the response summary
(`api/models.py:807-812`, populated at `enrichment/orchestrator.py:4412`,
`:4419-4421`). The same run yields `evidence_cache_hits` and, under
`CACHE_FROZEN=true`, `evidence_frozen_misses`. A warm second run of the same
batch should report zero (`enrichment/orchestrator.py:4407-4410`).

### 7.3 · Cost

No unit price for any external service is present in this repository, and none
is derivable from it. Each requires one external source:

| Cost driver | Source that supplies it |
| --- | --- |
| SerpAPI per search | `⚠ MEASUREMENT REQUIRED` — the SerpAPI account plan and usage dashboard for the key in `SERPAPI_KEY` |
| Azure OpenAI per 1K input / output tokens, Phase 1 deployment | `⚠ MEASUREMENT REQUIRED` — Azure pricing for the deployment at `AZURE_OPENAI_ENDPOINT` |
| Azure OpenAI per 1K tokens, Phase 2 deployment | `⚠ MEASUREMENT REQUIRED` — as above for `AOAI_DEPLOYMENT_DEDUP`; token counts per call are already captured (`dedup/llm.py:244-245`) |
| ROR, GLEIF, Wikidata, DuckDuckGo | free public APIs; the applicable constraint is a published terms-of-use rate limit, not a price. `⚠ MEASUREMENT REQUIRED` — the ROR, GLEIF and Wikimedia published terms |
| Egress for arbitrary-host page fetches | `⚠ MEASUREMENT REQUIRED` — Azure Cost Management → the `mdm-pipeline-api` Function App → Bandwidth meter, over a run of known `N` |

Phase 2 is the cheaper phase to cost because it already records
`prompt_tokens`, `completion_tokens` and `latency_ms` per call
(`dedup/llm.py:83-92`, `:240-249`). Phase 1 records no token counts: `call_openai`
returns only `response.choices[0].message.content` and discards `response.usage`
(`llm/openai_client.py:345`). Costing Phase 1 from repository evidence
therefore requires capturing `usage` first.

---

## 8 · Client libraries

Declared direct dependencies that reach an external service
(`requirements.txt`):

| Library | Constraint | Used for | Evidence |
| --- | --- | --- | --- |
| `httpx` | `>=0.27.0` | ROR, GLEIF, Wikidata, and the LLM client's injected transport | `requirements.txt:4`; `enrichment/tier1_ror.py:21`, `enrichment/tier1_lei.py:47`, `enrichment/wikidata.py:104`, `llm/openai_client.py:21` |
| `requests` | `>=2.31.0` | every page fetch and probe | `requirements.txt:5`; `search/page_fetcher.py:20` |
| `beautifulsoup4` | `>=4.12.0` | page parsing | `requirements.txt:6`; `search/page_fetcher.py:21` |
| `openai` | `>=1.30.0` | both LLM phases | `requirements.txt:7`; `llm/openai_client.py:22` |
| `google-search-results` | `>=2.4.2` | SerpAPI | `requirements.txt:11`; `search/serpapi_client.py:9` |
| `duckduckgo-search` | `>=6.0.0` | DuckDuckGo fallback | `requirements.txt:12`; `search/duckduckgo_client.py:9` |
| `python-dotenv` | `>=1.0.0` | `.env` loading at import | `requirements.txt:10`; `config.py:15`, `:22` |
| `rapidfuzz` | `>=3.6.0` | every name-verification guard on a registry response | `requirements.txt:8`; `enrichment/tier1_lei.py:48` |

Undeclared direct dependency: **`certifi`**, imported at `config.py:14`,
`llm/openai_client.py:20`, `search/page_fetcher.py:19`. It is the TLS trust
fallback for every outbound call and is pinned by nothing in this repository.

Every constraint is a lower bound (`>=`); no upper bound or lockfile is
present, so the resolved versions in any given environment are an environment
fact rather than a repository fact. The local environment reports
`openai 2.30.0` (`python3 -c "import openai; print(openai.__version__)"`).
`⚠ UNVERIFIED` for the deployed Function App.

---

## 9 · Discrepancies raised by this pass

For collection in Pass 08.

| # | Discrepancy | Both sides |
| --- | --- | --- |
| 1 | `MAX_PAGE_CONTENT_CHARS` has two different defaults | `config.py:121` declares `"MAX_PAGE_CONTENT_CHARS": "3000"` in `OPTIONAL_VARS_WITH_DEFAULTS`; `config.py:368` reads `int(os.getenv("MAX_PAGE_CONTENT_CHARS", "1500"))`. The dataclass is what runs, so the effective default is `1500` and the declared table is wrong |
| 2 | TLS verification is inconsistent across page-fetch entry points | `search/page_fetcher.py:333`, `:340`, `:356`, `:409` pin `verify=certifi.where()`; `:229-241` and `:436-440` pass no `verify` and use the `requests` default. The comment at `:234-240` records the reason and the measurement, so the split is deliberate — but it means two fetches of the same host can differ in trust store behind a TLS-inspecting VPN |
| 3 | A 429 is retried by exactly one client | `enrichment/wikidata.py:741` treats `429` as transient; `enrichment/tier1_lei.py:551-552` does not (`status >= 500` only); ROR, SerpAPI, DuckDuckGo and page fetch have no retry at all |
| 4 | ROR and GLEIF send no `User-Agent` | `enrichment/tier1_ror.py:1083-1085` and `enrichment/tier1_lei.py:626-629` construct clients with no `headers` beyond GLEIF's `Accept`, against `enrichment/wikidata.py:695-697`, which records that an anonymous bulk caller is the one most likely to be rate-limited |
| 5 | `CACHE_FROZEN` does not cover Phase 2 | `utils/cache.py:446-452` describes one switch across every namespace; `dedup/llm.py` participates in none of them and has a separate, separately-switched store (`dedup/cache.py:16-32`) |
| 6 | The SerpAPI key travels in the URL | `search/serpapi_client.py:65` places `api_key` in the query parameters |
| 7 | `certifi` is an undeclared direct dependency | imported at three sites (§8); absent from `requirements.txt` |
| 8 | Phase 1 discards `response.usage` | `llm/openai_client.py:345` returns only the message content, while `dedup/llm.py:244-245` captures token counts. Phase 1 cost cannot be computed from any repository artefact until this is captured |
| 9 | The address-validation service is undocumented | `docs/thesis/CONTEXT-EXTERNAL.md:423` names the step and the 80% threshold; no code, no ADF export, no endpoint exists in the repository (§2.9) |

---

## 10 · Summary

Nine external destinations, of which eight are reachable from code in this
repository and one (address validation) exists only in a DS workflow step whose
pipeline is not exported. Two are authorities and are the only sources
permitted to author a name without a witness; one is a crosswalk that buys a
lookup key and writes nothing; four supply evidence that can corroborate but
never author; and one — the model — is barred from `verified` by a grammar check
that raises at the site that built the provenance string.

Every Phase 1 answer, including the model's, is recorded under a key that is a
pure function of the request, and `CACHE_FROZEN` turns a miss into a counted,
traced absence rather than a network call. Phase 2 sits outside that switch and
has its own opt-in record/replay store.

No client declares a rate limit; concurrency caps and per-record call budgets
are the whole of the throttling. No unit price for any service is present in
this repository, and the instrumentation needed to turn one instrumented run
into a per-record call count is already in the batch summary.
