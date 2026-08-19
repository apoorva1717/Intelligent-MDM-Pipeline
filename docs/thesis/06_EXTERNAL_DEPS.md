Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 6 — External Dependencies

This document records every network service the enrichment service calls, and every
third-party Python library it depends on. For each service: the endpoints called, the
authentication mechanism and the source of the secret, the request and response shape as the
client constructs and reads it, documented rate limits, the retry and backoff actually
implemented, the timeout, the behaviour when the service fails, and the caching (or its
explicit absence). Every claim cites the client code.

## 0 · Conventions and evidence rules

- **Endpoint** — the URL as it appears in repository code, or, where the URL lives inside a
  vendored SDK rather than in this repository, the SDK file under `.venv/` is cited and
  marked as such. `.venv/` is excluded from the Pass-0 file table as a vendored directory
  (`00_INVENTORY.md:18`); it is cited here only as evidence of what a call actually resolves
  to at runtime.
- **Fail-open / fail-closed / fallback tier** — `fail-open` means the failure is swallowed and
  the pipeline continues with a null/empty value for that stage; `fallback tier` means the
  failure routes the record into a different, named stage; `fail-closed` means the failure
  aborts the unit of work. The unit of work is stated per case (record, block, or request).
- **Rate limits** — only limits *documented in this repository* are reported. Where none
  exists the row reads `⚠ NOT DOCUMENTED IN REPO` and names the source that would supply it.
  Vendor rate limits known from general knowledge are deliberately not stated.
- **Cost** — §3. No monetary figure appears anywhere in this repository; §3 states what is
  evidenced, and for everything else names the measurement that would produce the number.
- Line references are to this commit.

---

## 1 · Service inventory

| # | Service | Purpose | Called from | Auth | Client library |
|---|---------|---------|-------------|------|----------------|
| 1 | **ROR v2** (Research Organization Registry) | Tier 1 institution resolution: official name, ROR id, website, org types, children | `enrichment/tier1_ror.py:607-836` | none | `httpx` |
| 2 | **GLEIF / LEI** | Tier 1 company resolution: official legal name + Legal Entity Identifier | `enrichment/tier1_lei.py:251-380` | none | `httpx` |
| 3 | **SerpAPI** (Google Search Results) | SERP retrieval for website Path B, department-domain probe, Tier 2A/2B, lab resolver, person affiliation | `search/serpapi_client.py:38-56` | API key | `google-search-results` (`serpapi`) |
| 4 | **DuckDuckGo** | SERP fallback when `SERPAPI_KEY` is unset | `search/duckduckgo_client.py:31-42` | none | `duckduckgo-search` |
| 5 | **Azure AI Foundry / Azure OpenAI** — Phase 1 | Every enrichment-tier LLM call (overflow check, plain-name person classification, canonicalisation, Tier 2A/2B extraction, Tier 3, website Path C, person affiliation, address residual classification) | `llm/openai_client.py:129-292` | API key | `openai` (`AsyncAzureOpenAI`) |
| 6 | **Azure AI Foundry / Azure OpenAI** — Phase 2 | Dedup adjudication (Mode A, Mode B, residue) | `dedup/llm.py:104-220` | API key | `openai`, via the shared `get_openai_client` |
| 7 | **Arbitrary third-party web hosts** | Page fetch and HEAD probes for Tier 2A/2B, the lab resolver, and the department-domain probe | `search/page_fetcher.py:123-258` | none | `requests` + `beautifulsoup4` |
| — | **Address validation service** | — | **not present in this repository** — see §2.8 | — | — |

Services 5 and 6 are the same Azure resource reached through the same client-construction
function (`dedup/llm.py:3-8`, `:144`); they are documented separately because they use
different deployments, different REST API versions, different generation parameters, and
different retry policies.

---

## 2 · Per-service detail

### 2.1 · ROR v2

**Endpoints.** One base URL, three distinct request shapes, all `GET`:

| Strategy | Request | Line |
|----------|---------|------|
| A — affiliation | `GET {base}?affiliation=<name, city, state, country>` | `enrichment/tier1_ror.py:620-622` |
| A′ — affiliation, acronym-expanded retry | same, with institution acronyms expanded (`"HFT Stuttgart"` → `"Hochschule für Technik Stuttgart"`) | `enrichment/tier1_ror.py:701-712`, expansion at `:57`, map at `:50-52` |
| B — query with country filter | `GET {base}?query=<name>&filter=locations.geonames_details.country_code:<CC>` | `enrichment/tier1_ror.py:724-730` |
| B′ — query, no filter retry | `GET {base}?query=<name>`, only when B returned zero items **and** a country code was supplied | `enrichment/tier1_ror.py:735-742` |

Base URL: `https://api.ror.org/v2/organizations`, from `ROR_API_BASE`
(`config.py:85`, `:171-173`; direct fallback read at `enrichment/tier1_ror.py:571`). The
`RORClient` wrapper passes `settings.ror_api_base` (`enrichment/tier1_ror.py:854`, `:872`).

**Auth mechanism and secret source.** None. The `httpx.AsyncClient` is constructed with only
`timeout` and `verify` (`enrichment/tier1_ror.py:607-609`); no `headers=` argument, no
`Authorization` header, and no key parameter appears in any of the four request calls
(`:620-622`, `:730`, `:740`). No `User-Agent` is set, so the `httpx` default applies. No
environment variable holding a ROR credential exists (`config.py:78-119` lists no ROR secret).

**Request shape.** The affiliation string is `", ".join([name, city, state, country])` with
empty parts dropped (`enrichment/tier1_ror.py:581-585`); `name` has US state abbreviations
expanded first (`:578`).

**Response shape as read by the client.**

- Affiliation path: `data["items"]` — a list; the client selects the single entry with
  `chosen is True` (`:625-628`) and reads `item["score"]` (`:629`) and
  `item["organization"]` (`:637`).
- Query path: `data["items"]` is a list of organisation objects directly; the client reads
  `item["names"][]` with `value` and `types` (looking for `ror_display` and `acronym`)
  (`:781-788`, `:469-499`), `org["id"]` (`:522`), `org["links"][]` with `type == "website"`
  and `value` (`:434-436`), `org["types"][]` (`:504`), `org["relationships"][]` with
  `type == "child"`, `label`, `id` (`:507-511`), and
  `org["locations"][0]["geonames_details"]["country_code"]` / `["country_name"]`
  (`:446-450`, `:513-519`).

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO. No rate limit, quota, or 429 handling
for ROR appears anywhere in the repository (a repository-wide search for `429`/`rate limit`
returns only dedup-LLM sites and tests). The source would be the ROR public API
documentation at the base URL's host; it is not reproduced in any repository artefact.

**Retry and backoff as implemented.** **None.** There is no retry loop and no backoff in the
ROR client. The only "retry" in the code path is a *semantic* fallback, not a transport
retry: strategy A′ re-queries with an expanded name after a clean miss
(`enrichment/tier1_ror.py:701-714`), and strategy B′ re-queries without the country filter
after a zero-item response (`:735-742`). Neither is triggered by an HTTP error — both HTTP
error classes are caught at the outermost level and end the call (`:838-846`).

**Timeout.** `15.0` seconds, hardcoded, applied to the whole `AsyncClient`
(`enrichment/tier1_ror.py:608`). Not configurable by environment variable.

**Behaviour on failure — fail-open with a fallback tier.** `httpx.HTTPStatusError` is logged
at ERROR with status and response body prefix and converted to `_no_match()`
(`enrichment/tier1_ror.py:838-843`); any other exception is logged with a traceback and also
converted to `_no_match()` (`:844-846`). `_no_match()` returns
`{"matched": False, "score": …}` and *caches that miss* (`:593-596`). The orchestrator sees a
normal miss and escalates the record down the tier chain (`enrichment/orchestrator.py:1955-1974`).
Consequence: a ROR outage is indistinguishable from a genuine miss at the orchestrator
boundary — every affected record escalates to the LLM tiers instead of failing.

**TLS.** `verify=resolve_tls_verify()` (`enrichment/tier1_ror.py:608`), reusing the LLM
client's trust resolution so ROR survives a TLS-inspecting corporate VPN
(`:599-606`; resolver at `llm/openai_client.py:93-126`).

**Caching.** Yes, two layers of the same mechanism:

- Module-level dict `_ror_cache`, keyed `(name.lower().strip(), country_code)`
  (`enrichment/tier1_ror.py:35-36`, read at `:566-568`, written at `:595`, `:677`, `:831`).
  Both hits **and** misses are cached.
- Cleared at the start of every batch — "fresh cache per batch to avoid stale failures"
  (`enrichment/orchestrator.py:793`; `clear_ror_cache` at `enrichment/tier1_ror.py:39-41`).
  Effective scope is therefore one `/enrich` batch.
- ⚠ `BatchCache.get_ror` / `set_ror` (`utils/cache.py:75-81`) exist but have **no callers**
  outside their own definitions — the documented per-batch `BatchCache` ROR slot is dead
  code; the operative cache is the module-level one. Consistent with `03_ALGORITHMS.md:2485`.

---

### 2.2 · GLEIF / LEI

**Endpoints.** Base `https://api.gleif.org/api/v1` from `GLEIF_API_BASE`
(`config.py:88`, `:186-188`; default argument at `enrichment/tier1_lei.py:213`). Three
request shapes, all `GET`:

| Strategy | Request | Line |
|----------|---------|------|
| A — precise | `GET {base}/lei-records?filter[entity.legalName]=<name>&filter[entity.status]=ACTIVE&page[size]=10[&filter[entity.legalAddress.country]=<CC>]` | `enrichment/tier1_lei.py:237`, `:256-262`, `:268` |
| B1 — fuzzy typeahead | `GET {base}/fuzzycompletions?field=entity.legalName&q=<name>` | `enrichment/tier1_lei.py:327-332` |
| B2 — candidate resolution | `GET {base}/lei-records/{lei}` for each of the first 5 completions | `enrichment/tier1_lei.py:340-352` |

**Auth mechanism and secret source.** None. The module docstring states the API is "free …
(no auth, JSON:API format)" (`enrichment/tier1_lei.py:4-6`). The client sets only
`Accept: application/vnd.api+json` (`:55`, `:253`); no credential is sent and no GLEIF
environment variable exists (`config.py:87-91` declares only base URL, timeout, thresholds,
and retry count).

**Response shape as read by the client.** JSON:API. `data[]` on both `lei-records` and
`fuzzycompletions` (`:269`, `:333`), `data` as a single object on the by-id fetch (`:356`).
Per record the client reads `record["id"]` (the LEI) and
`record["attributes"]["entity"]["legalName"]["name"]`, `["status"]`,
`["legalAddress"]["country"]` (`:114-125`). From a fuzzy completion it reads
`completion["relationships"]["lei-records"]["data"]["id"]` (`:341-345`).

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO. No GLEIF rate limit or 429 path appears
in the code; `_get_json` retries only `status is None or status >= 500`
(`enrichment/tier1_lei.py:197-198`), so a 429 would be treated as non-transient and raised.
The source would be the GLEIF API documentation at the base URL's host; it is not reproduced
in any repository artefact.

**Retry and backoff as implemented.** `_get_json` (`enrichment/tier1_lei.py:177-207`):

- Loop bound: `max_retries` = `LEI_MAX_RETRIES`, default `2`
  (`config.py:91`, `:198-200`; default argument `enrichment/tier1_lei.py:215`), consumed at
  `:200`.
- Retryable condition: `status is None` (transport error) or `status >= 500` (`:197-198`).
  Comment: "Only retry transient failures (network, timeout, 5xx). A 4xx is not going to get
  better on retry." (`:195-196`).
- Backoff: `0.5 * (2 ** (attempt - 1))` seconds → 0.5 s, then 1.0 s (`:202`), awaited at
  `:207`.
- On exhaustion the exception is re-raised to the caller (`:200-201`), which classifies it as
  an error rather than a miss (`:304-312`).
- The retry policy applies independently to each of the three request shapes, including each
  of the up-to-five by-id fetches in the fuzzy path (`:350-352`).

**Timeout.** `GLEIF_TIMEOUT_SECONDS`, default `15` (`config.py:89`, `:189-191`; default
argument `enrichment/tier1_lei.py:214`), applied to the `AsyncClient` covering all requests
in one `call_lei` invocation (`:251-254`).

**Behaviour on failure — fail-open with a fallback tier.** Three levels:

1. `httpx.HTTPStatusError` → logged at ERROR, returns `{"matched": False, "error": True}`
   (`enrichment/tier1_lei.py:304-309`). Any other exception → same dict, logged with
   traceback (`:310-312`). Neither is cached.
2. A failed by-id resolution inside the fuzzy path is caught per candidate and skipped
   (`:349-355`), so one bad candidate does not sink the lookup.
3. The orchestrator wraps the call in its own guard, explicitly annotated
   "`# noqa: BLE001 — GLEIF must never fail a record`"
   (`enrichment/orchestrator.py:1651-1655`), increments `self._lei_counts["errors"]`, and
   continues.

The module docstring states the contract: "A GLEIF failure (timeout / 5xx / malformed) must
NEVER fail the record — every error path returns a miss/error dict and the orchestrator falls
through to the existing LLM path unchanged." (`enrichment/tier1_lei.py:35-37`). The fallback
tier is `run_company_canonical` (LLM company canonicalisation), reached at
`enrichment/orchestrator.py:2164`.

**TLS.** `verify=resolve_tls_verify()` (`enrichment/tier1_lei.py:252`), with the same
corporate-VPN rationale as ROR (`:244-250`).

**Caching.** Module-level dict `_lei_cache` keyed `(name.strip().lower(), country_code)`
(`enrichment/tier1_lei.py:81`, read `:232-234`, written through `_cache()` `:239-241`).
Cleared per batch (`clear_lei_cache` `:84-86`, called at
`enrichment/orchestrator.py:794`). **Misses are cached** (`:302`); **errors are not**
(`:309`, `:312` return without going through `_cache`), so a transient GLEIF failure is
retried on the next record with the same name inside the same batch.

**Feature flag.** `LEI_LOOKUP_ENABLED`, default `true` (`config.py:87`, `:183-185`), gating
the whole company branch at `enrichment/orchestrator.py:1643`. When false the company branch
goes straight to the LLM — the documented fallback behaviour (`config.py:181-182`).

---

### 2.3 · SerpAPI

**Endpoint.** Not stated in repository code. `search/serpapi_client.py:45-46` constructs
`GoogleSearch(params)` and calls `.get_dict()`; the URL is inside the vendored SDK:
`BACKEND = "https://serpapi.com"` and `construct_url(path="/search")`
(`.venv/Lib/site-packages/serpapi/serp_api_client.py:32`, `:40-49`), issued as
`requests.get(url, parameter, timeout=self.timeout)` (`:59`). Effective endpoint:
`GET https://serpapi.com/search`.

**Auth mechanism and secret source.** API key passed as the `api_key` query parameter
(`search/serpapi_client.py:42`). The key comes from `SERPAPI_KEY`
(`config.py:160`), read into `Settings.serpapi_key` and handed to the client constructor at
`enrichment/orchestrator.py:773-776`. Sourced from Azure Functions Application Settings in
production and from a git-ignored `.env` locally (`config.py:1-5`); `.env.example:56`
carries a placeholder. `SERPAPI_KEY` is one of the two secrets in the system
(`04_PARAMETERS.md:404-409`).

**Request shape.** `{"q": query, "num": num_results, "api_key": …, "engine": "google"}`
(`search/serpapi_client.py:39-44`). The SDK additionally injects `source: "python"`
(`.venv/Lib/site-packages/serpapi/serp_api_client.py:41`).

**Response shape.** The client reads `data["organic_results"][]` and, per item, `title`,
`link`, `snippet`, mapping them to `SearchResult(title, url, snippet)`
(`search/serpapi_client.py:48-55`; dataclass at `search/base.py:9-14`). The list is truncated
to `num_results` (`:56`).

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO. No quota, credit budget, or plan tier
is recorded in any repository artefact. The source would be the SerpAPI account plan attached
to the key held in `SERPAPI_KEY` (an account-dashboard value, not a repository value).

**Retry and backoff as implemented.** **None.** No retry loop exists in
`search/serpapi_client.py`. The synchronous SDK call is wrapped in a thread executor
(`:27-33`) with a blanket `except Exception` that logs and returns `[]` (`:34-36`).

**Timeout.** No timeout is passed from repository code. The SDK's constructor default applies:
`timeout = 60000` (`.venv/Lib/site-packages/serpapi/serp_api_client.py:35`), forwarded to
`requests.get(..., timeout=self.timeout)` (`:59`). `requests` interprets that value in
**seconds**, i.e. 60 000 s ≈ 16.7 hours — effectively no timeout.
⚠ This is a latent hang risk on the SERP path: nothing in this repository bounds a SerpAPI
call. → record in `08_GAPS.md`.

**Behaviour on failure — fail-open, per call site.** `SerpAPIClient.search` itself never
raises: it logs `logger.exception` and returns `[]` (`search/serpapi_client.py:34-36`), so
callers see "no results" rather than an error. Two of the six SERP consumers additionally
wrap the call in their own `try`/`except` (website Path B,
`enrichment/website_resolver.py:491-502`, returning an empty `WebsiteResolution()`; the
department probe, `enrichment/orchestrator.py:1181-1190` and `:1296-1303`, substituting
`[]`), the person-affiliation proposer catches per query and continues to the next
(`enrichment/person_affiliation.py:123-127`), the lab resolver calls it unguarded
(`enrichment/lab_resolver.py:83`), and Tier 2A / Tier 2B call it unguarded
(`enrichment/tier2a_contact.py:330`; `enrichment/tier2b_dept.py:227`). Because the client
swallows everything, the unguarded sites are unreachable by a SERP transport error in
practice; a failure surfaces as an empty candidate list, which each stage treats as a miss.

**TLS.** Not set by this repository. `serpapi` uses `requests`, which honours
`REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`. `config._sanitize_ssl_env()` exists specifically for
this case: "third-party SDKs we don't control (e.g. `serpapi`) can't be patched the same way.
Overriding the env vars here makes the workaround global." (`config.py:39-43`, executed at
import, `config.py:67`).

**Caching.** Yes — a two-tier query cache, applied by the *callers*, not by the client:

- `BatchCache` per `/enrich` batch, plus a process-level `SerpCache` shared across batches;
  a batch miss falls through to the shared store and a shared hit is promoted into the batch
  store (`utils/cache.py:26-105`; construction at `enrichment/orchestrator.py:760`, `:796`).
- Key: the lowercased, stripped query string (`utils/cache.py:22-23`).
- Applied at: website Path B (`enrichment/website_resolver.py:487`, `:503`), the
  department-domain probe stage 2 (`enrichment/orchestrator.py:1177`, `:1192`) and stage 3
  (`:1291`, `:1306`), Tier 2A (`enrichment/tier2a_contact.py:326-331`), Tier 2B
  (`enrichment/tier2b_dept.py:223-228`), and the lab resolver
  (`enrichment/lab_resolver.py:79-84`).
- **Not applied at:** person affiliation — `search_client.search` is called directly with no
  `BatchCache` involvement (`enrichment/person_affiliation.py:124`). Consistent with
  `03_ALGORITHMS.md:7196`.
- Failures are **not** cached: `cache.set_serp` runs only on the success branch
  (`enrichment/website_resolver.py:491-503`; `enrichment/orchestrator.py:1185-1192` uses
  `try/except/else` so the `else` write is skipped on error).
- No persistence. The `SerpCache` docstring is explicit: "No file I/O — it lives entirely in
  memory." (`utils/cache.py:11-12`), "Not persisted to disk." (`:31`). On an Azure Functions
  cold start the cache is empty.

---

### 2.4 · DuckDuckGo

**Selection.** Used only when `SERPAPI_KEY` is empty or unset:
`Orchestrator._build_search_client` returns `SerpAPIClient(key)` when a non-blank key is
present, otherwise logs a warning and returns `DuckDuckGoClient()`
(`enrichment/orchestrator.py:770-781`). `validate_env` emits the same warning at startup —
"DuckDuckGo returns lower-quality results." (`config.py:141-145`).

**Endpoint.** Not stated in repository code. `search/duckduckgo_client.py:33-34` opens
`DDGS()` and calls `ddgs.text(query, max_results=num_results)`. In the installed version
(`duckduckgo_search 8.1.1`), `text()` declares `backend: str = "auto"` and resolves
`backends = ["html", "lite"]`, then **unconditionally overwrites that with**
`backends = ["bing"]  # temporaly disable html and lite backends`
(`.venv/Lib/site-packages/duckduckgo_search/duckduckgo_search.py:152`, `:180-182`). The
`bing` path issues `GET https://www.bing.com/search` (`:388`).
⚠ Therefore the "DuckDuckGo fallback" in the installed dependency set does not query
DuckDuckGo — it scrapes Bing. This is a property of the pinned library version, not of
repository code. → record in `08_GAPS.md`.

**Auth mechanism and secret source.** None. "no API key required"
(`search/duckduckgo_client.py:17`).

**Request shape.** `ddgs.text(query, max_results=num_results)` only
(`search/duckduckgo_client.py:34`); no region, safesearch, timelimit, or backend argument is
passed, so the library defaults apply
(`.venv/Lib/site-packages/duckduckgo_search/duckduckgo_search.py:146-153`).

**Response shape.** A list of dicts; the client reads `title`, `href`, `body` and maps them
to `SearchResult(title, url, snippet)` (`search/duckduckgo_client.py:35-41`), truncated to
`num_results` (`:42`).

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO. The installed library defines
`RatelimitException` and documents it on `text()` — "raised for exceeding API request rate
limits" (`.venv/Lib/site-packages/duckduckgo_search/exceptions.py:5`;
`.../duckduckgo_search.py:174`) — but no numeric limit is recorded anywhere, and repository
code neither catches that exception by type nor backs off. The source would be the upstream
service's terms; not reproduced in any repository artefact.

**Retry and backoff as implemented.** **None** in repository code. The library itself
iterates its `backends` list and returns on the first success
(`.venv/Lib/site-packages/duckduckgo_search/duckduckgo_search.py:185-195`), but after the
line-182 overwrite that list has exactly one element, so no alternative backend is tried.

**Timeout.** Not passed from repository code. The library's `DDGS.__init__` default applies:
`timeout: int | None = 10` seconds (`.../duckduckgo_search.py:41`, `:68`, `:72`).

**Behaviour on failure — fail-open.** `logger.exception` then `return []`
(`search/duckduckgo_client.py:27-29`) — identical contract to `SerpAPIClient`, so every
downstream stage behaves as described in §2.3.

**Caching.** Identical to §2.3 — caching is performed by the callers against the
`SearchClient` interface (`search/base.py:17-23`), not by the provider implementation, so
switching provider does not change the caching behaviour.

---

### 2.5 · Azure AI Foundry / Azure OpenAI — Phase 1 enrichment

**Endpoint.** `AZURE_OPENAI_ENDPOINT`, passed as `azure_endpoint` to `AsyncAzureOpenAI`
(`llm/openai_client.py:148`, `:169-174`). The operation is Chat Completions:
`client.chat.completions.create(...)` (`llm/openai_client.py:198-207`). The REST path is
constructed by the `openai` SDK from `azure_endpoint`, `api_version`, and the deployment
name; it does not appear in repository code.

- `api_version`: argument → `AZURE_OPENAI_API_VERSION` → `DEFAULT_AZURE_OPENAI_API_VERSION`
  = `2024-08-01-preview` (`llm/openai_client.py:78`, `:149-153`).
- Deployment: `AZURE_OPENAI_DEPLOYMENT`, fallback literal `gpt-5.4`
  (`llm/openai_client.py:199`, and `:233` for the cached-client wrapper).

Deployment location: the AI Foundry deployment runs on the Bruker Azure spoke
(`CONTEXT-EXTERNAL.md:405-408` [AUTHOR]).

**Auth mechanism and secret source.** API key. `api_key = os.getenv("AZURE_OPENAI_API_KEY")`
(`llm/openai_client.py:147`), passed to `AsyncAzureOpenAI(api_key=…)` (`:170`); the SDK sends
it as the `api-key` header. If either key or endpoint is missing the constructor raises
`RuntimeError` with a remediation message (`:154-159`). Startup validation warns but does not
raise, so the app still serves `/health` (`config.py:122-135`). Sourced from Azure Functions
Application Settings in production and a git-ignored `.env` locally (`config.py:1-5`).
`GET /diag/llm` reports only the key's presence and length, never its value
(`api/routes.py:1046-1047`).

**Request shape.** `messages` = one `system` + one `user` turn;
`max_completion_tokens = max_tokens`; `temperature = 0.0`;
`response_format = {"type": "json_object"}` (`llm/openai_client.py:198-207`). Token budgets by
call site: `call_openai` default `500` (`:180`), `OpenAIClient.extract_json` default `1024`
(`:263`), address residual classification `200`
(`enrichment/address_processing.py:677-680`), `GET /diag/llm` probe `50`
(`api/routes.py:1054`).

⚠ `extract_json` accepts a `temperature` parameter (`llm/openai_client.py:262`) that it never
forwards — `call_openai` hardcodes `temperature=0.0` (`:205`). Setting it changes nothing
(`04_PARAMETERS.md:564`).

**Response shape.** `response.choices[0].message.content` — a string
(`llm/openai_client.py:208`). `extract_json` strips a `​```json` fence if present (`:279-281`)
and `json.loads` the remainder (`:284`). **`response.usage` is not read**, so Phase 1 emits no
token telemetry — see §3.

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO. No TPM/RPM figure, no 429 handling, and
no throttling logic exist on the Phase 1 path (`llm/openai_client.py:209-210` converts every
exception, including a 429, into `RuntimeError`). The source would be the Azure AI Foundry
deployment's quota setting for the `gpt-5.4` deployment on the Bruker spoke — an Azure portal
value, not a repository value.

**Retry and backoff as implemented.**

- **No transport retry, no backoff.** `call_openai` has no retry loop; any exception is
  wrapped as `RuntimeError(f"OpenAI call failed: {e}")` and raised (`:209-210`).
- **One parse retry, no delay.** `extract_json` loops `for attempt in range(2)` (`:271`);
  a `json.JSONDecodeError` on the first attempt logs "LLM returned invalid JSON, retrying
  (attempt 1)" and re-issues the *whole* completion (`:285-288`); a second failure logs at
  ERROR and raises `ValueError` (`:289-290`). This is a re-generation on unparseable output,
  not a retry on a network or 429 error.
- ⚠ Asymmetry worth recording: the Phase 2 adjudicator retries 429/5xx with exponential
  backoff (§2.6) while the Phase 1 tiers do not retry transport failures at all.

**Timeout.** `httpx.Timeout(read_timeout, connect=connect_timeout)`
(`llm/openai_client.py:164-168`), with `connect = LLM_HTTP_CONNECT_TIMEOUT` default `30` s
(`:162`) and read = `LLM_HTTP_TIMEOUT` default `60` s (`:163`). The connect value is
deliberately generous: "a VPN tunnel can add real latency to the initial handshake"
(`:160-161`).

**Behaviour on failure — fail-open at every call site; per-record fail-closed only for an
unhandled path.** Each consumer catches independently and degrades:

| Consumer | Catch site | Effect |
|----------|-----------|--------|
| Overflow check (UC 0) | `enrichment/overflow_check.py:53-55` | returns the unmodified result |
| Plain-name person classification | `enrichment/preprocess.py:2325-2327` | `continue` — that candidate gets no verdict, so the field is left untouched (`:2312-2313`) |
| Company canonicalisation | `enrichment/company_canonical.py:61-63` | returns the unmodified result |
| Tier 2 canonicalisation | `enrichment/tier2_canonical.py:77-79` | returns the unmodified result |
| Tier 2A extraction | `enrichment/tier2a_contact.py:142-144` | `continue` — next candidate page |
| Tier 2B extraction | `enrichment/tier2b_dept.py:97-99` | `continue` — next candidate page |
| Lab resolver (UC 13) | `enrichment/lab_resolver.py:118-121` | next candidate |
| Tier 3 | `enrichment/tier3_llm.py:102-105` | `confidence = "none"`, `enrichment_status = "failed"` |
| Website Path C | `enrichment/website_resolver.py:601-604` | no URL produced |
| Person affiliation | `enrichment/person_affiliation.py:147-151` | empty `PersonAffiliation`; the docstring states "Never raises" (`:114`) |
| Address residual classification | `enrichment/address_processing.py:681-683` | `(None, 0.0)` → issue `G1-ADDR-009` (`:726-728`) |

Anything not caught locally reaches the per-record handler
(`enrichment/orchestrator.py:2599-2609`), which sets `enrichment_status = "failed"`, records
`error`, and still runs `_finalise_and_return` so the row round-trips. Above that,
`asyncio.gather(..., return_exceptions=True)` prevents one record from failing the batch
(`enrichment/orchestrator.py:804-819`). **No LLM failure can fail a `/enrich` request.**

**Caching.** **None.** There is no LLM response cache anywhere in the repository:
`BatchCache` holds ROR, SERP, and resolved-host namespaces only (`utils/cache.py:56-63`).
Every LLM call is issued live, including repeated calls with identical prompts within one
batch. What *is* reused is the HTTP connection pool: `OpenAIClient` lazily builds and caches a
single `AsyncAzureOpenAI` for its lifetime (`llm/openai_client.py:222-245`), and one-shot
clients are closed eagerly (`:211-219`).

**TLS.** `verify=resolve_tls_verify()` (`llm/openai_client.py:165`), resolving in order:
`LLM_SSL_VERIFY=false` → verification disabled with a loud warning (`:110-116`); else the
first existing file among `AZURE_OPENAI_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`
(`:83`, `:118-124`); else `certifi.where()` (`:126`). `trust_env=True` so the VPN's
`HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` are honoured (`:167`).

---

### 2.6 · Azure AI Foundry / Azure OpenAI — Phase 2 dedup adjudicator

**Endpoint.** Same Azure resource and same client-construction function — "Reuses the Phase 1
AI Foundry client construction (`get_openai_client` in `llm.openai_client`) — it does NOT
build a new client." (`dedup/llm.py:3-4`), called with an explicit API version
(`dedup/llm.py:142-145`). Operation: `client.chat.completions.create(**params)`
(`dedup/llm.py:186`).

- Deployment: `AOAI_DEPLOYMENT_DEDUP` → `AZURE_OPENAI_DEPLOYMENT` → literal `gpt-5.4`
  (`dedup/llm.py:117-121`), sent as `params["model"]` (`:175`).
- `api_version`: `AOAI_API_VERSION_DEDUP` → `AZURE_OPENAI_API_VERSION` →
  `DEFAULT_API_VERSION = "2025-04-01-preview"` (`dedup/llm.py:112`, `:124-128`). The newer
  version is required because "GPT-5.x reasoning models and the `reasoning_effort` parameter
  require a newer version than the Phase 1 default" (`:108-111`).

**Auth mechanism and secret source.** Identical to §2.5 — the same `AZURE_OPENAI_API_KEY`
resolved inside `get_openai_client` (`llm/openai_client.py:147`). There is no dedup-specific
credential.

**Request shape.** `model`, `messages` (one `system` + one `user`),
`max_completion_tokens`, `response_format={"type": "json_object"}`
(`dedup/llm.py:174-182`), plus `reasoning_effort` when enabled (`:183-184`) from
`DEDUP_REASONING_EFFORT`, default `low` (`:122`). Temperature is deliberately **not** sent —
"reasoning models may ignore temperature" (`:5-7`). `max_tokens` defaults to `4000`
(`:161`) but both application call sites pass `1000`
(`dedup/adjudicator.py:452`, `:638`); the default is reached only by test doubles
(`04_PARAMETERS.md:333-338`).

**Response shape.** `response.choices[0].message.content` (`dedup/llm.py:190`), plus
telemetry the Phase 1 client discards: `response.usage.prompt_tokens`,
`usage.completion_tokens` (`:188-192`), `response.model` (`:194`), and a locally measured
`latency_ms` (`:187`). Parsing is defensive — fenced block, then plain JSON, then the
outermost `{…}` span; unparseable text returns `None`, which callers treat as "uncertain"
rather than an error (`dedup/llm.py:75-101`).

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO — the deployment quota is an Azure
portal value (same source as §2.5). What *is* in the repo is the *handling*: `429` and
`5xx` are classified retryable (`dedup/llm.py:49-60`), documented as "retries 429/5xx with
exponential backoff" (`.env.example:40`; `README.md:1277`, `:1669`).

**Retry and backoff as implemented.** `DedupLLM.adjudicate` (`dedup/llm.py:156-220`):

- Loop bound: `DEDUP_MAX_RETRIES`, default `3` attempts (`:123`, `:172`).
- Retryable set: `APIConnectionError` / `APITimeoutError` (imported defensively at `:27-30`),
  status `429`, or `500 ≤ status < 600` (`:49-60`), tested at `:209`.
- Backoff: `0.5 * (2 ** attempt)` → 0.5 s then 1.0 s (`:210`), awaited at `:215`.
- **Special non-counting retry:** if the deployment rejects `reasoning_effort`
  (`_is_unsupported_reasoning_effort`, `:33-46`), the parameter is disabled process-wide for
  that client instance and the attempt is retried immediately with no delay (`:202-208`) —
  "The parameter is a tuning preference, not a correctness gate." (`:201`).
- Concurrency is bounded by `asyncio.Semaphore(DEDUP_MAX_CONCURRENCY)`, default `5`, across
  all blocks in one request (`dedup/adjudicator.py:37`, `:950-952`, acquired at `:313`).

**Timeout.** Inherited from `get_openai_client` — connect `30` s, read `60` s
(`llm/openai_client.py:162-166`). There is no dedup-specific timeout.

**Behaviour on failure — fail-open at block granularity, degrading to `manual_review`.**
`adjudicate` "Never raises: on exhausted retries it returns a result with `error` set so the
caller can mark the affected signatures uncertain and continue (one bad call never fails a
whole block)." (`dedup/llm.py:165-168`; the error result is built at `:220`). Callers:

- Mode A: `parsed = parse_json_object(call.raw) if call.error is None else None`; on `None`
  every signature in the bucket is marked `uncertain` and made its own adjudicated entity —
  "Never fail the block." (`dedup/adjudicator.py:318-334`).
- Mode B: the same treatment per signature (`dedup/adjudicator.py:463`, `:512-513`).
- Residue: an ambiguous or unusable verdict routes both sides to `manual_review`
  (`dedup/adjudicator.py:649-653`, `:675-681`).
- `sig.uncertain` becomes `routing = "manual_review"` on emission
  (`dedup/adjudicator.py:747-749`), and `stats.errors` is surfaced in the response summary
  (`:983`).

So a total Azure outage during Phase 2 yields a **complete, well-formed response** in which
every row is routed to `manual_review` with `errors > 0` — the failure mode `GET
/diag/dedup-llm` exists to diagnose (`api/routes.py:1068-1072`).

**Caching.** **None.** No adjudication-result cache exists. The only reuse is the connection
pool: `DedupLLM` lazily builds one client and holds it for its lifetime
(`dedup/llm.py:142-145`), closed via `aclose()` (`:147-154`).

---

### 2.7 · Arbitrary third-party web hosts (page fetch)

Not a single service, but a genuine external dependency: the pipeline fetches pages from
whatever hosts SERP returns, plus guessed department subdomains.

**Endpoints.** Four distinct outbound request shapes, all synchronous `requests` calls run in
a thread executor:

| Operation | Request | Line |
|-----------|---------|------|
| Structured page extraction | `GET <url>` | `search/page_fetcher.py:218-222` |
| Outgoing-link harvest | `GET <url>` | `search/page_fetcher.py:187-192` |
| Subdomain existence probe | `HEAD https://<host>/`, redirects followed | `search/page_fetcher.py:146-152` |
| Redirect resolution | `HEAD <url>` with `allow_redirects=True`, falling back to a streamed `GET` when `status_code >= 400` — "Some servers reject HEAD" | `search/page_fetcher.py:125-139` |

**Auth mechanism and secret source.** None. A fixed `User-Agent: BrukerMDM-Enrichment/1.0` is
sent on all four (`search/page_fetcher.py:127`, `:134`, `:150`, `:190`, `:221`).

**Request/response shape.** Response is HTML, parsed with `BeautifulSoup(resp.text,
"html.parser")` (`search/page_fetcher.py:194`, `:225`). The extractor returns a `PageContent`
dataclass — `url`, `url_path`, `page_title`, `h1`, `breadcrumb`, `body_text`
(`search/page_fetcher.py:52-63`, built at `:251-258`); title/h1/breadcrumb are truncated to
300 characters each (`:254-256`) and `body_text` to `max_chars` with a `…` suffix
(`:248-249`). `{script, style, nav, footer, header, aside, form, iframe}` are decomposed
before body extraction (`:25`, `:244-245`), with breadcrumbs captured *before* `<nav>` removal
(`:231-233`). The link harvest returns `(anchor_text[:200], absolute_href)` tuples for hosts
differing from the base domain (`:198-215`).

**Documented rate limits.** ⚠ NOT DOCUMENTED IN REPO, and not applicable in the usual sense —
the targets are arbitrary hosts. No per-host throttle, politeness delay, or `robots.txt`
check exists in `search/page_fetcher.py`. → record in `08_GAPS.md`.

**Retry and backoff as implemented.** **None.** The single exception is the HEAD→GET fallback
on `status_code >= 400` in redirect resolution (`search/page_fetcher.py:130-139`), which is a
method fallback rather than a retry.

**Timeout.** `PAGE_FETCH_TIMEOUT_SECONDS`, default `10` s, for page and link fetches
(`config.py:110`, `:211-213`; `PageFetcher.__init__` `search/page_fetcher.py:69`; applied at
`:189`, `:220`). `5` s default arguments for `subdomain_exists` (`:95`, applied `:148`) and
`resolve_final_url` (`:111`, applied `:126`, `:133`).

**Behaviour on failure — fail-open.** Every entry point returns a neutral value:
`fetch_page_content` → `None` (`search/page_fetcher.py:91-93`), `fetch_outgoing_links` → `[]`
(`:177-179`), `subdomain_exists` → `False` (`:108-109`, `:153-154`),
`resolve_final_url` → `None` (`:120-121`, `:141-142`). Failures are logged without a
traceback and mostly at DEBUG, because "Most fetch failures are *expected* and already handled
by the caller" (`:28-49`). Consumers skip the candidate: Tier 2A `continue`s
(`enrichment/tier2a_contact.py:110-113`), Tier 2B `continue`s
(`enrichment/tier2b_dept.py:89-91`), the department probe logs and moves on
(`enrichment/orchestrator.py:1131-1135`, `:1365-1368`).

**TLS.** Inconsistent across the four call sites: `verify=certifi.where()` is passed
explicitly on redirect resolution (`search/page_fetcher.py:129`, `:136`), the subdomain probe
(`:151`), and the link harvest (`:191`) — "bypass a bogus SSL_CERT_FILE / REQUESTS_CA_BUNDLE
env var" (`:184-186`) — but **not** on `_sync_fetch_structured` (`:218-222`), which therefore
relies on the process-wide sanitisation in `config._sanitize_ssl_env()`
(`config.py:27-67`). → record in `08_GAPS.md`.

**Caching.** **None.** No page cache exists; `BatchCache` has no page namespace
(`utils/cache.py:56-63`). What *is* cached is one derived value: the redirect-resolved
institution host, via `get_resolved_host`/`set_resolved_host`
(`utils/cache.py:65-71`), "so the department probe costs one resolution per institution, not
one per stage" (`:60-62`).

---

### 2.8 · Address validation service — not present in this repository

No address-validation service is called from this repository. Specifically:

- A repository-wide search for `address valid`, `azure maps`, `smarty`, `loqate`, `melissa`,
  `geocod`, and `postal valid` outside `docs/` returns exactly one hit, and it is a statement
  of absence: "It does **not** do address validation, embeddings, golden-record election, or
  file I/O — those are out of scope and handled elsewhere in the pipeline." (`README.md:1129`).
- No address-validation endpoint, key, or client module exists; `config.py:78-119` declares no
  such variable and `search/` contains only the two SERP clients and the page fetcher
  (`00_INVENTORY.md:98-106`).
- The service's own address work is **entirely local and deterministic** — the late address
  stage `process_address` (`enrichment/address_processing.py`), whose only external call is
  the Azure OpenAI residual classification documented in §2.5
  (`enrichment/address_processing.py:676-680`).

Address validation exists in the **surrounding pipeline**, not here: step 6 of the production
workflow is "Address validation; auto write-back above 80% confidence", executed by ADF
(`CONTEXT-EXTERNAL.md:423` [AUTHOR]). That ADF pipeline is **not exported**
(`CONTEXT-EXTERNAL.md:442`, open item 2), so the validating vendor, its endpoint, its auth,
and the comparison operator behind "above 80%" are all unevidenced
(`04_PARAMETERS.md:259`, `:579-582`).

⚠ UNVERIFIED — the identity of the address-validation service is not determinable from any
artefact available to this pass. The source would be the ADF pipeline JSON for step 6,
exported from the Tillit tenant in the same form as the two pipelines already in
`CONTEXT-EXTERNAL.md:39-315`.

---

## 3 · Cost model

### 3.1 · What is evidenced in the repository

No monetary figure, unit price, credit balance, plan tier, or billing reference appears
anywhere in this repository. A search for `cost`, `pricing`, `price`, `$0.`, `per 1k`,
`credits`, and `quota` outside `docs/` returns only qualitative statements and identifier
comments. The three qualitative statements are:

| Service | Statement in repo | Source |
|---------|-------------------|--------|
| Preprocessing | "Zero (no API calls)" | `README.md:84` |
| ROR | "Low (free public API)" | `README.md:85` |
| GLEIF / LEI | "Low (free public API)"; "the free GLEIF API" | `README.md:86`; `enrichment/tier1_lei.py:4-5` |
| DuckDuckGo | "free fallback when no SerpAPI key"; "no API key required" | `search/duckduckgo_client.py:1`, `:17` |
| Tiers 2A / 2B / 3 | "Medium"; Tier 2 canonical "Low-Medium" | `README.md:87-90` |

The `Cost` column of `README.md:82-90` is an **ordinal design-time ranking** (Zero / Low /
Low-Medium / Medium) used to justify the tier-escalation order — "start with the cheapest,
most reliable method and escalate only when cheaper methods fail" (`README.md:80`). It is not
a cost measurement and carries no unit.

**Per-call cost, all services: ⚠ MEASUREMENT REQUIRED.** No per-call cost is derivable from
this repository for any service.

### 3.2 · What would produce each number

| Service | Cost driver | ⚠ MEASUREMENT REQUIRED — source that would give it |
|---------|-------------|-----------------------------------------------------|
| ROR | requests | The ROR service's published terms of use. Not reproduced in any repository artefact; the repo asserts "free public API" (`README.md:85`) without citing a source |
| GLEIF / LEI | requests | As above (`README.md:86`) |
| DuckDuckGo | requests | As above (`search/duckduckgo_client.py:1`) |
| **SerpAPI** | searches | The SerpAPI account plan and usage dashboard for the key held in `SERPAPI_KEY` (`config.py:160`). Neither the plan, the monthly search allowance, nor the per-search price is recorded in this repository. The **call volume** side is measurable from the repo: instrument `SerpAPIClient.search` (`search/serpapi_client.py:22`) or count cache misses via `BatchCache.stats` (`utils/cache.py:109-111`) |
| **Azure OpenAI — Phase 2 dedup** | prompt + completion tokens | **Token counts are already captured.** Per call: `prompt_tokens`, `completion_tokens`, `latency_ms`, `model_version` on `DedupLLMResult` (`dedup/llm.py:63-72`, populated `:188-195`), logged per call as `dedup_llm_call` (`dedup/adjudicator.py:811-824`) and per request as `dedup_request` with `total_prompt_tokens`, `total_completion_tokens`, `total_tokens` (`dedup/adjudicator.py:996-1011`). Multiply by the Azure price for the `AOAI_DEPLOYMENT_DEDUP` deployment, read from Azure Cost Management for the resource at `AZURE_OPENAI_ENDPOINT`. Note the totals are **log-only** — they are not returned in `DedupResponse` (`dedup/adjudicator.py:1013`) |
| **Azure OpenAI — Phase 1** | prompt + completion tokens | **Token counts are not captured at all.** `call_openai` reads only `response.choices[0].message.content` and discards `response.usage` (`llm/openai_client.py:198-208`). A measurement therefore requires either (a) instrumenting that return to record `response.usage`, mirroring `dedup/llm.py:188-192`, or (b) reading the Azure Monitor / Cost Management metrics for the `AZURE_OPENAI_DEPLOYMENT` deployment over a run of known size |
| Page fetch | bandwidth to arbitrary hosts | No cost accrues to this system beyond egress; ⚠ MEASUREMENT REQUIRED if Azure egress is to be attributed — Azure Cost Management for the Function App |
| Address validation | — | Not in this repository (§2.8). The ADF pipeline for step 6 would identify the vendor (`CONTEXT-EXTERNAL.md:442`) |

### 3.3 · Call volume per record — structural bounds

The per-record cost depends on how many external calls a record triggers. These are the call
sites, not measurements; a record's actual count depends on which tier resolves it and on
cache hits.

| Stage | External calls | Site |
|-------|----------------|------|
| Overflow check (UC 0) | 1 LLM | `enrichment/orchestrator.py:1724` |
| Plain-name person classification | 1 LLM **per suspicious plain-name candidate** across Name 1–4 | `enrichment/orchestrator.py:1763`; loop at `enrichment/preprocess.py:2319-2324` |
| Tier 1 ROR | 1–4 HTTP (affiliation, acronym retry, query, no-filter retry) | `enrichment/tier1_ror.py:620`, `:708`, `:730`, `:740` |
| Tier 1 GLEIF | 1–7 HTTP (exact, fuzzycompletions, up to 5 by-id) | `enrichment/tier1_lei.py:268`, `:328`, `:340-352` |
| Company canonicalisation | 1 LLM | `enrichment/orchestrator.py:2164` |
| Lab resolver (UC 13) | 1 SERP + up to 3 page fetches + up to 3 LLM | `enrichment/lab_resolver.py:83`, `:118` |
| Tier 2 canonicalisation | 1 LLM | `enrichment/orchestrator.py:2384`, `:2508` |
| Tier 2A | 1 SERP per query + up to 3 page fetches + up to 3 LLM | `enrichment/tier2a_contact.py:330`, `:110`, `:142` |
| Tier 2B | 1 SERP per query + up to 3 page fetches + up to 3 LLM | `enrichment/tier2b_dept.py:227`, `:89`, `:97` |
| Tier 3 | 1 LLM | `enrichment/orchestrator.py:2543` |
| Website Path B | 1 SERP, +1 on the unquoted retry | `enrichment/website_resolver.py:492`, `:522-529` |
| Website Path C | 1 LLM | `enrichment/orchestrator.py:907` → `enrichment/website_resolver.py:598` |
| Department-domain probe | 1 homepage fetch + subdomain HEAD probes + 1 SERP; **+1 SERP only when `DEPT_PROBE_CROSS_DOMAIN`** | `enrichment/orchestrator.py:1131`, `:1109-1115`, `:1182`, `:1277`, `:1296` |
| Person affiliation (Stage 2b) | up to 1 SERP per query variant + 1 LLM (+1 ROR confirm) | `enrichment/person_affiliation.py:124`, `:148`; `enrichment/orchestrator.py:1455` |
| Address residual classification | 1 LLM **per non-empty secondary street slot** (`street_2`…`street_5`) that is not already unambiguous | `enrichment/address_processing.py:718-724` |

Two configuration choices move this materially and are already flagged as conflicts:
`DEPT_PROBE_CROSS_DOMAIN` doubles SERP calls for unresolved departments when set to `true`,
and `.env.example:61` sets it to `true` against a code default of `false`
(`04_PARAMETERS.md:289-305`); `MAX_PAGE_CONTENT_CHARS` sizes the prompt slice sent to the LLM
and `.env.example:81` sets `3000` against an effective default of `1500`
(`04_PARAMETERS.md:266-287`).

⚠ MEASUREMENT REQUIRED — the observed per-record and per-batch call counts. The instrumentation
that would produce them: the `dedup_request` log record already does it for Phase 2
(`dedup/adjudicator.py:996-1011`); the Phase 1 equivalent does not exist. `BatchCache.stats`
(`utils/cache.py:109-111`) reports ROR and SERP cache entry counts but is not logged anywhere.
Related open item: "Measured per-batch duration for a 50-row `/enrich` call"
(`CONTEXT-EXTERNAL.md:447`).

---

## 4 · Coupling — what breaks when each service is unavailable

The unit of work matters. A `/enrich` request contains up to 50 records
(`CONTEXT-EXTERNAL.md:64`, `:106`); `asyncio.gather(..., return_exceptions=True)` isolates
records from one another (`enrichment/orchestrator.py:804-819`). A dedup request contains
blocks; each block is isolated by the adjudicator's own error handling
(`dedup/adjudicator.py:318-334`).

| Service unavailable | Immediate effect | Pipeline outcome | Evidence |
|---------------------|------------------|------------------|----------|
| **ROR** | Every call returns `_no_match()`, cached as a miss for the batch | **Degrades.** No `ror_id`, no ROR-derived `website_url`/`domain`, no org-type classification, no child matching. Every institution record escalates to Tier 2/Tier 3 → LLM and SERP spend rises for the whole batch. Records complete with lower confidence and are flagged | `enrichment/tier1_ror.py:838-846`, `:593-596`; escalation `enrichment/orchestrator.py:1955-1974` |
| **GLEIF** | `{"matched": False, "error": True}`; orchestrator increments `_lei_counts["errors"]` | **Degrades.** No `lei_id`, no registry-verified legal name. The company branch falls through to LLM canonicalisation — precisely the pre-GLEIF behaviour (`config.py:181-182`). Errors are not cached, so the batch keeps retrying | `enrichment/tier1_lei.py:35-37`, `:304-312`; `enrichment/orchestrator.py:1651-1655` |
| **SerpAPI** (key present but service down) | `search()` returns `[]` | **Degrades.** Website Path B finds nothing → falls to Path C (LLM, always `confidence='low'`, always flagged, `enrichment/website_resolver.py:562-565`). The department-domain probe finds no candidates. Tier 2A and Tier 2B produce no candidate pages → the record escalates to Tier 3. Person affiliation returns empty. Six stages degrade at once | `search/serpapi_client.py:34-36`; consumers listed in §2.3 |
| **SerpAPI key absent** | `DuckDuckGoClient` substituted at construction | **Degrades, silently, by design.** Same six stages run against a lower-quality provider — "DuckDuckGo returns lower-quality results" (`config.py:141-144`). ⚠ Compounded by the installed library scraping Bing rather than DuckDuckGo (§2.4) | `enrichment/orchestrator.py:770-781`; `config.py:137-145` |
| **DuckDuckGo** (when it is the active provider) | `search()` returns `[]` | **Degrades** exactly as the SerpAPI-down row. There is **no third provider** — `_build_search_client` is a two-way choice | `search/duckduckgo_client.py:27-29`; `enrichment/orchestrator.py:770-781` |
| **Page fetch targets** (a host blocks or is down) | `None` / `[]` / `False` | **Degrades per candidate.** Tier 2A and 2B skip that page and try the next of three; the department probe cannot verify a candidate host and leaves the department domain unresolved | `search/page_fetcher.py:91-93`, `:108-109`, `:120-121`, `:177-179`; `enrichment/tier2a_contact.py:110-113`; `enrichment/tier2b_dept.py:89-91` |
| **Azure OpenAI — Phase 1** | Every tier's LLM call raises; each consumer catches (§2.5 table) | **Degrades, heavily.** The deterministic spine survives: preprocessing, ROR, GLEIF, search-term derivation, and the whole address stage except residual classification. Lost: overflow check, all canonicalisation, Tier 2A/2B extraction, Tier 3, website Path C, person affiliation, and residual classification (which instead emits `G1-ADDR-009` per affected slot). Records return with `enrichment_status` from the last successful tier; nothing fails the request | consumer table in §2.5; `enrichment/orchestrator.py:2599-2609`; `:804-819` |
| **Azure OpenAI credentials missing** | `get_openai_client` raises `RuntimeError` on the **first** LLM call, not at startup | **Degrades.** `validate_env` warns only, so the app starts and `/health` answers — "allows the app to start so health checks still work, but LLM calls will fail until the variables are set" | `llm/openai_client.py:154-159`; `config.py:122-135` |
| **Azure OpenAI — Phase 2** | `DedupLLMResult(error=…)` after retries | **Degrades to a well-formed no-op.** Every signature is marked `uncertain`, every row is emitted with `routing = "manual_review"`, `summary.errors > 0`. Deterministic STEP A signature collapse still runs, so exact duplicates are still collapsed. The request returns 200 | `dedup/llm.py:165-168`, `:220`; `dedup/adjudicator.py:318-334`, `:747-749`, `:983`; `api/routes.py:1068-1072` |

**Nothing halts.** Every external dependency in this service is fail-open at the request
boundary: there is no path on which an external outage produces a non-2xx response from
`/enrich`, `/enrich/file`, `/api/dedup/cluster-block`, or `/api/dedup/file`. The three
endpoints that make no external calls at all — `/api/dedup/score`, `/api/dedup/approve`,
`/issues` — are unaffected by any outage (`00_INVENTORY.md:283`, `:287-294`).

**Where the halt actually lives — upstream.** The surrounding ADF pipelines are *not*
fail-open. Every activity carries `retry: 0` (`CONTEXT-EXTERNAL.md:54` and six further sites)
and `ForEach1.isSequential: true` (`:88`), so a single failed `Web1` or `Merge Back` stops the
Enrichment pipeline at that offset and no later offsets are processed
(`04_PARAMETERS.md:245`, `:249`). Because the service returns 200 with degraded content
rather than an error, an outage of ROR, GLEIF, SERP, or Azure OpenAI does **not** trip that
ADF failure path — it silently writes weaker enrichment back to `test_77.Legacy`. ⚠ There is
no quality gate between the two. → record in `08_GAPS.md`.

**Coupling not covered by mock mode.** `MOCK_EXTERNAL_CALLS=true` substitutes mocks for ROR,
search, page fetch, and LLM (`api/routes.py:57-70`) but **not** for GLEIF: `mock_clients` has
no `"lei"` key, so `Orchestrator.__init__` falls back to the real `LEIClient(settings)`
(`enrichment/orchestrator.py:736`). A `tests/mocks/lei_mock.py` exists
(`00_INVENTORY.md:345`) but is not wired into `_get_orchestrator`. Mock mode therefore still
makes live GLEIF calls for company records. → record in `08_GAPS.md`.

---

## 5 · Third-party libraries

### 5.1 · Version provenance

There is **no lock file** in this repository — no `requirements.lock`, `poetry.lock`,
`Pipfile.lock`, `pdm.lock`, `uv.lock`, or `constraints.txt`. Dependencies are declared as
lower bounds only:

- `requirements.txt` (14 lines) — 14 runtime packages, every one pinned with `>=` and no
  upper bound.
- `requirements-dev.txt` (5 lines) — `-r requirements.txt` plus 4 development packages,
  likewise `>=` only.

The **Declared** column below is the constraint from those files. The **Resolved** column is
the version actually installed in the working-tree virtual environment, read from
`.venv/Lib/site-packages/<name>-<version>.dist-info`. `.venv/` is a vendored, git-ignored
directory excluded from the Pass-0 file table (`00_INVENTORY.md:18`); it is the only record of
resolved versions that exists, and it is **not** committed.

⚠ Consequence for reproducibility: nothing in version control fixes the dependency set. A
fresh `pip install -r requirements.txt` at a later date resolves different versions. The
resolved set below is a snapshot of one machine at this commit, not a reproducible artefact.
→ record in `08_GAPS.md`.

Python: `3.13.7` (`.venv/pyvenv.cfg`). `requirements.txt` states no `python_requires`;
`README.md:106` says "Python 3.11+".

### 5.2 · Direct runtime dependencies (declared in `requirements.txt`)

| Package | Declared | Resolved | Used for | Import site |
|---------|----------|----------|----------|-------------|
| `fastapi` | `>=0.110.0` | `0.136.3` | HTTP application, router, `UploadFile`, `StreamingResponse` | `api/app.py:5`; `api/routes.py:11-12` |
| `uvicorn` | `>=0.27.0` | `0.48.0` | ASGI server for local development only | `main.py:6-8` |
| `azure-functions` | `>=1.18.0` | `2.1.0` | Azure Functions v2 binding and `AsgiMiddleware` wrapper for production | `function_app.py:7-8` |
| `httpx` | `>=0.27.0` | `0.28.1` | Async HTTP for ROR and GLEIF; the explicit `AsyncClient` handed to the Azure OpenAI SDK (TLS + proxy control) | `enrichment/tier1_ror.py:21`; `enrichment/tier1_lei.py:47`; `llm/openai_client.py:20`, `:164-168` |
| `requests` | `>=2.31.0` | `2.34.2` | Synchronous page GET, link harvest, HEAD subdomain probe, redirect resolution | `search/page_fetcher.py:20` |
| `beautifulsoup4` | `>=4.12.0` | `4.14.3` | HTML parsing: title, h1, breadcrumb, body text, anchors | `search/page_fetcher.py:21` |
| `openai` | `>=1.30.0` | `2.40.0` | `AsyncAzureOpenAI` client; `APIConnectionError`/`APITimeoutError` for the dedup retry classifier | `llm/openai_client.py:21`; `dedup/llm.py:28` |
| `rapidfuzz` | `>=3.6.0` | `3.14.5` | All fuzzy matching: ROR local rescore, GLEIF name verification, Tier 2A/2B match bands, canonical-name dedupe, residue nomination (`JaroWinkler`) | `enrichment/tier1_ror.py:22`; `enrichment/tier1_lei.py:48`; `enrichment/tier2a_contact.py:13`; `enrichment/tier2b_dept.py:18`; `enrichment/orchestrator.py:22`; `enrichment/preprocess.py:32`; `utils/text_utils.py:8`; `dedup/candidates.py:24` |
| `pydantic` | `>=2.6.0` | `2.13.4` | All request/response schemas and validation | `api/models.py:7`; `api/routes.py:13`; `dedup/models.py:11`; `dedup/scoring.py:29` |
| `python-dotenv` | `>=1.0.0` | `1.2.2` | Unconditional `.env` load at import | `config.py:15`, `:22` |
| `google-search-results` | `>=2.4.2` | `2.4.2` | SerpAPI SDK; provides the `serpapi` module and `GoogleSearch` | `search/serpapi_client.py:9` |
| `duckduckgo-search` | `>=6.0.0` | `8.1.1` | Keyless SERP fallback via `DDGS.text` — ⚠ this version scrapes Bing (§2.4) | `search/duckduckgo_client.py:9` |
| `openpyxl` | `>=3.1.0` | `3.1.5` | XLSX read/write for `/enrich/file`, `/issues`, `/issues/compare`, `/api/dedup/file`, `/api/dedup/score/file`, and the eval harness | `api/routes.py:169,269,321,362,431,767`; `dedup/scoring_xlsx.py:187`; `eval/dedup_eval.py:99` |
| `python-multipart` | `>=0.0.9` | `0.0.32` | Multipart form parsing behind every `UploadFile` endpoint. No direct import — required at runtime by FastAPI's `File`/`UploadFile` | required by `api/routes.py:11` usage; declared `requirements.txt:14` |

### 5.3 · Undeclared direct dependencies

Imported by repository code but absent from `requirements.txt` and `requirements-dev.txt`.
Both resolve today only because a declared package pulls them in transitively; a change in
that package's own dependencies would break the build.

| Package | Resolved | Imported at | Reaches the venv via |
|---------|----------|-------------|----------------------|
| `certifi` | `2026.5.20` | `config.py:14`, `:52`; `llm/openai_client.py:19`, `:126`; `search/page_fetcher.py:19`, `:129,136,151,191` | `requests` and `httpx`/`httpcore` (`Requires-Dist: certifi`) |
| `starlette` | `1.2.1` | `api/middleware.py:13` (`BaseHTTPMiddleware`, `RequestResponseEndpoint`) | `fastapi` (`Requires-Dist: starlette>=0.46.0`) |

→ record in `08_GAPS.md`.

### 5.4 · Development dependencies (declared in `requirements-dev.txt`)

| Package | Declared | Resolved | Used for |
|---------|----------|----------|----------|
| `pytest` | `>=8.0.0` | `9.0.3` | Test runner; configured by `pytest.ini:2-3` (`testpaths = tests`) |
| `pytest-asyncio` | `>=0.23.0` | `1.4.0` | Async test support; `asyncio_mode = strict` (`pytest.ini:2`) |
| `pytest-cov` | `>=5.0.0` | **not installed** | Coverage measurement. ⚠ Absent from `.venv/Lib/site-packages` — the coverage measurement recommended at `00_INVENTORY.md:424-425` (`pytest --cov=. --cov-report=term-missing`) cannot run in this environment without installing it first |
| `httpx` | `>=0.27.0` | `0.28.1` | Also a runtime dependency (§5.2); used in tests as the ASGI test transport (`tests/test_routes.py:12`; `tests/test_dedup.py:17`; `tests/test_scoring.py:25`) |

### 5.5 · Transitive dependencies present in the environment

Not imported by repository code and not declared in either requirements file. "Required by" is
read from the `Requires-Dist` metadata of the installed packages.

| Package | Resolved | Required by |
|---------|----------|-------------|
| `annotated-doc` | `0.0.4` | `fastapi` |
| `annotated-types` | `0.7.0` | `pydantic` |
| `anyio` | `4.13.0` | `starlette`, `httpx`, `openai` |
| `charset-normalizer` | `3.4.7` | `requests` |
| `click` | `8.4.1` | `uvicorn`, `duckduckgo-search` |
| `colorama` | `0.4.6` | `click`, `pytest`, `tqdm`, `pygments` (Windows) |
| `distro` | `1.9.0` | `openai` |
| `et-xmlfile` | `2.0.0` | `openpyxl` |
| `h11` | `0.16.0` | `httpcore`, `uvicorn` |
| `httpcore` | `1.0.9` | `httpx` |
| `idna` | `3.18` | `requests`, `httpx`, `anyio` |
| `iniconfig` | `2.3.0` | `pytest` |
| `jiter` | `0.15.0` | `openai` |
| `lxml` | `6.1.1` | `duckduckgo-search` |
| `markupsafe` | `3.0.3` | `werkzeug` |
| `packaging` | `26.2` | `pytest` |
| `pip` | `25.2` | virtual-environment bootstrap |
| `pluggy` | `1.6.0` | `pytest` |
| `primp` | `1.3.1` | `duckduckgo-search` (its HTTP client) |
| `pydantic-core` | `2.46.4` | `pydantic` |
| `pygments` | `2.20.0` | `pytest` |
| `sniffio` | `1.3.1` | `openai` |
| `soupsieve` | `2.8.4` | `beautifulsoup4` |
| `tqdm` | `4.67.3` | `openai` |
| `typing-extensions` | `4.15.0` | `pydantic`, `beautifulsoup4`, `fastapi` |
| `typing-inspection` | `0.4.2` | `pydantic`, `fastapi` |
| `urllib3` | `2.7.0` | `requests` |
| `werkzeug` | `3.1.8` | `azure-functions` (`Requires-Dist: werkzeug~=3.1.3`) |
| `truststore` | `0.10.4` | ⚠ no installed package declares it — no reverse dependency found among the `Requires-Dist` metadata of the other 46 installed distributions, and no repository module imports it |

### 5.6 · Declared-vs-resolved divergences worth recording

| Package | Declared floor | Resolved | Note |
|---------|----------------|----------|------|
| `azure-functions` | `>=1.18.0` | `2.1.0` | A major-version jump above the declared floor. The production runtime binding depends on it (`function_app.py:7-8`); nothing constrains the major version |
| `duckduckgo-search` | `>=6.0.0` | `8.1.1` | Two major versions above the floor. The resolved version's `text()` bypasses its own backend selection and queries Bing (§2.4) — a behavioural change that an unbounded `>=` admits silently |
| `openai` | `>=1.30.0` | `2.40.0` | A major-version jump. `dedup/llm.py:27-30` already imports `APIConnectionError`/`APITimeoutError` "defensively so a version skew can't break module import" — the code anticipates exactly this |
| `pytest` | `>=8.0.0` | `9.0.3` | A major-version jump on the test runner |
| `google-search-results` | `>=2.4.2` | `2.4.2` | The only package resolved exactly at its declared floor |

---

## 6 · Summary

Seven external services in six classes: two free registries (ROR, GLEIF), two SERP providers
in an either/or fallback relationship (SerpAPI, DuckDuckGo), one LLM resource used through two
distinct configurations (Azure AI Foundry, Phase 1 and Phase 2), and unbounded page fetching
against arbitrary third-party hosts. Two secrets: `AZURE_OPENAI_API_KEY` and `SERPAPI_KEY`,
both from Azure Functions Application Settings in production. No address-validation service is
called from this repository (§2.8).

Retry policy is implemented on two of the seven: GLEIF (2 retries, 0.5/1.0 s backoff, 5xx and
transport only) and the Phase 2 adjudicator (3 attempts, 0.5/1.0 s backoff, 429/5xx and
transport). ROR, SerpAPI, DuckDuckGo, page fetch, and the Phase 1 LLM implement no transport
retry. Caching exists for ROR (per batch, hits and misses), GLEIF (per batch, hits and misses,
not errors), SERP (per batch plus a process-level store, misses not cached), and one derived
value (the redirect-resolved institution host). There is no cache for LLM responses and none
for fetched pages, and no cache is persisted.

Every service is fail-open at the request boundary: no external outage produces a non-2xx
response from any endpoint. The pipeline degrades — it never halts here. The halt lives
upstream, in the ADF activities' `retry: 0` and sequential `ForEach`, which a degraded 200
response does not trip.

Cost: no monetary figure is evidenced anywhere in this repository. Phase 2 token counts are
already captured and logged; Phase 1 token counts are discarded and would need instrumentation
(§3.2).

Libraries: 14 declared runtime packages, 4 declared development packages (one of which,
`pytest-cov`, is not installed), 2 undeclared direct dependencies (`certifi`, `starlette`), 29
transitive packages. No lock file exists; every declaration is an unbounded `>=`, so the
resolved set recorded here is not reproducible from version control.

Pass 6 complete. Stop.
