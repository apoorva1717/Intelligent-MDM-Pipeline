Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 4 — Parameters

This document enumerates every tunable value in the system: thresholds, weights, model
deployment names, generation parameters, retry and backoff policies, HTTP timeouts, batch and
page sizes, feature flags, environment variables, and the Azure Data Factory activity policies
that govern the two exported pipelines. Values are copied verbatim from the defining artefact;
none are rounded or normalised.

## Conventions

- **Defined at** — the artefact and line where the literal appears. Where a value is an
  environment variable, both the default-declaration site and the `Settings` field site are
  cited.
- **Consumed at** — the line where the value actually changes behaviour (a comparison, a
  request parameter, a loop bound). A parameter that is defined but never reaches a decision
  is recorded in §5.
- **Rationale** — filled only from a code comment, a config docstring, a commit message, the
  `README.md` configuration table, `.env.example`, or the DATAshaper tutorial transcripts, with
  the source cited. Where no such evidence exists the cell reads
  `⚠ UNDOCUMENTED — author to supply`. No rationale is inferred from the value itself.
- **Effect if raised / lowered** — the mechanical consequence read from the consuming code
  path, not a predicted quality outcome. Where the parameter is a bounded enum or a
  non-orderable string, the cells state the substitution effect instead.
- Parameters outside this repository (ADF, DATAshaper) cite `CONTEXT-EXTERNAL.md` and respect
  its provenance markers ([EXPORT] ground truth, [OBSERVED], [AUTHOR]).

---

## 1 · Parameter table

### 1.1 · LLM — Azure OpenAI (Phase 1 enrichment tiers)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.4` | str (env) | `config.py:84`; `config.py:157`; fallback literal `llm/openai_client.py:199,233` | `llm/openai_client.py:199` (`model=`) | n/a — names a deployment; substituting changes which model answers every Phase-1 tier | n/a | "Deployment for Phase 1 enrichment (and Phase 2 dedup unless `AOAI_DEPLOYMENT_DEDUP` below overrides it)" (`.env.example`); `README.md:1665` area env table |
| `AZURE_OPENAI_API_VERSION` | *(unset)* → `2024-08-01-preview` | str (env) | `llm/openai_client.py:78` (`DEFAULT_AZURE_OPENAI_API_VERSION`) | `llm/openai_client.py:149-153` | n/a | n/a | "Default Azure OpenAI REST API version for the Phase 1 enrichment tiers. Reasoning models (GPT-5.x) and the `reasoning_effort` parameter need a newer version" (`llm/openai_client.py:75-77`) |
| `temperature` (Phase 1 chat completions) | `0.0` | float (hardcoded) | `llm/openai_client.py:205` | `llm/openai_client.py:198-207` (request body) | Higher sampling entropy in every tier's JSON extraction | Already at the floor | ⚠ UNDOCUMENTED — author to supply |
| `max_tokens` — `call_openai` default | `500` | int | `llm/openai_client.py:180` | `llm/openai_client.py:204` (`max_completion_tokens`) | Longer completions permitted; higher per-call token cost | Truncated completions → invalid JSON → the one retry at `llm/openai_client.py:271-288`, then `ValueError` | ⚠ UNDOCUMENTED — author to supply |
| `max_tokens` — `OpenAIClient.extract_json` default | `1024` | int | `llm/openai_client.py:263` | `llm/openai_client.py:272-275` | As above | As above | ⚠ UNDOCUMENTED — author to supply |
| `max_tokens` — address residual classification | `200` | int | `enrichment/address_processing.py:679` | same call | As above | Truncation → `_classify_residual` returns `(None, 0.0)` → issue `G1-ADDR-009` (`enrichment/address_processing.py:726-728`) | ⚠ UNDOCUMENTED — author to supply |
| `max_tokens` — `GET /diag/llm` probe | `50` | int | `api/routes.py:1054` | same call | Larger diagnostic probe | Smaller probe | ⚠ UNDOCUMENTED — author to supply |
| `max_tokens` — `GET /diag/dedup-llm` probe | `200` | int | `api/routes.py:1088` | same call | Larger diagnostic probe | Smaller probe | ⚠ UNDOCUMENTED — author to supply |
| JSON-parse retry count | `2` attempts (1 retry) | int (loop bound) | `llm/openai_client.py:271` (`range(2)`) | `llm/openai_client.py:286-290` | More retries on unparseable JSON; more token spend per record | `0` retries — a single malformed response raises `ValueError` to the tier | "Retries once if the first response is not valid JSON" (`llm/openai_client.py:267-268`) |
| `LLM_HTTP_CONNECT_TIMEOUT` | `30` (seconds) | float (env) | `llm/openai_client.py:162` | `llm/openai_client.py:166` (`httpx.Timeout(connect=)`) | Slower failure on an unreachable endpoint; tolerates slower VPN handshake | Handshakes over a slow tunnel fail before completing | "Connect timeout is generous because a VPN tunnel can add real latency to the initial handshake" (`llm/openai_client.py:160-161`); "Handshake/read timeouts (seconds) — bump if the tunnel is slow" (`.env.example`) |
| `LLM_HTTP_TIMEOUT` | `60` (seconds) | float (env) | `llm/openai_client.py:163` | `llm/openai_client.py:166` (`httpx.Timeout` read) | Long-running completions tolerated; a hung call blocks the record's tier for longer | Long completions abort as timeouts and the tier escalates | Same `.env.example` comment as above |
| `LLM_SSL_VERIFY` | `true` | bool (env) | `llm/openai_client.py:110` (`_env_bool(..., default=True)`) | `llm/openai_client.py:110-116`, returned into `httpx.AsyncClient(verify=)` at `:165` | n/a | `false` disables TLS certificate verification for all LLM calls | "`LLM_SSL_VERIFY=false` disables verification entirely. Insecure — a last resort for locked-down machines where the corp CA cannot be installed. Logged loudly." (`llm/openai_client.py:101-103`) |
| `AZURE_OPENAI_CA_BUNDLE` | *(unset)* | path (env) | `config.py:53`; `llm/openai_client.py:83` | `config.py:54-60`; `llm/openai_client.py:118-124` | n/a | n/a | "On a TLS-inspecting corporate VPN, certifi alone is not enough — the inspected hosts present certs signed by the corp CA" (`config.py:45-50`) |

### 1.2 · LLM — Azure OpenAI (Phase 2 dedup adjudicator)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `AOAI_DEPLOYMENT_DEDUP` | *(unset)* → `AZURE_OPENAI_DEPLOYMENT` → `gpt-5.4` | str (env) | `dedup/llm.py:117-121` | `dedup/llm.py:175` (`params["model"]`) | n/a | n/a | "Prefer a dedup-specific deployment; otherwise reuse the Phase 1 deployment so a single configured deployment works for both phases" (`dedup/llm.py:115-116`); "AI Foundry deployment for the full GPT-5.4 model used by the adjudicator" (`.env.example`) |
| `AOAI_API_VERSION_DEDUP` | *(unset)* → `AZURE_OPENAI_API_VERSION` → `2025-04-01-preview` | str (env) | `dedup/llm.py:112` (`DEFAULT_API_VERSION`), resolved `:124-128` | `dedup/llm.py:144` (`get_openai_client(api_version=)`) | n/a | n/a | "GPT-5.x reasoning models and the `reasoning_effort` parameter require a newer version than the Phase 1 default" (`dedup/llm.py:108-111`); `README.md:1665` |
| `DEDUP_REASONING_EFFORT` | `low` | str (env) | `dedup/llm.py:122` | `dedup/llm.py:183-184` (`params["reasoning_effort"]`) | Higher effort tiers cost more tokens/latency per adjudication | n/a — `low` is the lowest tier used | "Reasoning effort for the adjudicator (reasoning models may ignore temperature, so temperature is not sent)" (`README.md:1666`); `dedup/llm.py:5-8` |
| `DEDUP_MAX_RETRIES` | `3` | int (env) | `dedup/llm.py:123` | `dedup/llm.py:172` (loop bound), `:209` | More attempts per adjudication call on 429/5xx; longer worst-case block latency | Fewer attempts; a transient 429 sooner yields `DedupLLMResult(error=…)` → the block's signatures are marked uncertain | "Max attempts per adjudicator call (retries 429/5xx with exponential backoff)" (`.env.example`; `README.md:1669`) |
| Dedup retry backoff | `0.5 * (2 ** attempt)` seconds → 0.5 s, 1.0 s | float (formula) | `dedup/llm.py:210` | `dedup/llm.py:215` (`asyncio.sleep`) | Longer waits between attempts | Retries pile onto a rate-limited endpoint faster | "bounded exponential-backoff retries" (`dedup/llm.py:163`) |
| Retryable status set | `429` or `500 ≤ code < 600`, plus `APIConnectionError`/`APITimeoutError` | set (hardcoded) | `dedup/llm.py:49-60` | `dedup/llm.py:209` | n/a | n/a | "Retry only transient failures: connection/timeout, 429, and 5xx." (`dedup/llm.py:50`) |
| `max_tokens` — `DedupLLM.adjudicate` default | `4000` | int | `dedup/llm.py:161` | `dedup/llm.py:180` | Longer adjudication JSON permitted | Truncated verdict → `parse_json_object` returns `None` → treated as uncertain (`dedup/llm.py:79-81`) | ⚠ UNDOCUMENTED — author to supply |
| `max_tokens` — actual Mode A / residue call sites | `1000` | int | `dedup/adjudicator.py:452`; `dedup/adjudicator.py:638` | same calls | As above | As above | ⚠ UNDOCUMENTED — author to supply. ⚠ Note the default of `4000` (`dedup/llm.py:161`) is never used by application code — see §5 |
| `PROMPT_VERSION` | `p2-dedup-v3` | str | `dedup/prompts.py:14` | `dedup/adjudicator.py:822`; emitted per result row | n/a | n/a | "Bumped whenever the prompt wording changes in a way that could shift decisions. Logged per LLM call and emitted in every result row." (`dedup/prompts.py:12-13`) |

### 1.3 · Tier 1 — ROR (research-organisation registry)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `ROR_API_BASE` | `https://api.ror.org/v2/organizations` | str (env) | `config.py:85`; `config.py:172`; fallback `enrichment/tier1_ror.py:571` | `enrichment/tier1_ror.py:621` | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| `ROR_CONFIDENCE_THRESHOLD` | `0.8` | float (env) | `config.py:86`; `config.py:177`; read directly at `enrichment/tier1_ror.py:573` | `enrichment/tier1_ror.py:629` (ROR's own score), `:646` (local rescore), `:815` (query-endpoint score) | Fewer Tier-1 matches accepted → more records escalate to Tier 2/3 (LLM/SERP spend rises) | More Tier-1 matches accepted, including ROR affiliation-scorer false positives the local rescore was added to catch (`enrichment/tier1_ror.py:638-642`) | "FIX(Bug 1): single confidence threshold for all record types. Was: separate 0.8 for institutions, 0.9 for companies." (`config.py:174-175`) |
| ROR HTTP timeout | `15.0` (seconds) | float (hardcoded) | `enrichment/tier1_ror.py:608` | same `httpx.AsyncClient` | Slow ROR responses tolerated longer | ROR calls abort sooner; the record falls through to the next tier | ⚠ UNDOCUMENTED — author to supply |
| Substring length guard — default | `ratio = 0.6` | float | `enrichment/tier1_ror.py:247` | `enrichment/tier1_ror.py:250` | Shorter/longer name pairs rejected more often | More length-mismatched substring hits score `1.0` | "Length-guarded substring match (shorter side ≥60% of longer) against canonical names only." (`enrichment/tier1_ror.py:203-204`) |
| Substring length guard — canonical-name call | `ratio = 0.9` | float | `enrichment/tier1_ror.py:281` | same expression | Even tighter substring acceptance | "a short canonical name [matches] a longer query that merely contains it — e.g. 'Regional Health' inside 'LAKELAND REGIONAL HEALTH'" (the failure the tightening prevents) | "The substring rule is tight (≥90% length similarity) to prevent a short canonical name from matching a longer query that merely contains it" (`enrichment/tier1_ror.py:264-267`) |
| Distinctive/identifier-guard score cap | `0.7` | float | `enrichment/tier1_ror.py:325`, `:328` | `enrichment/tier1_ror.py:329-330` | Above `0.8` the cap would stop suppressing unguarded fuzzy hits | Stronger suppression of guard-failing candidates | "No distinctive token shared — cap at 0.7 so it cannot cross the 0.8 match threshold." (`enrichment/tier1_ror.py:323-324`) |
| Significant-token minimum length | `4` characters | int | `enrichment/tier1_ror.py:236` | `enrichment/tier1_ror.py:274-279` | Fewer tokens count as significant; the subset shortcut fires less | Short/common words gain subset-matching power | "every significant (≥4-char) query token appears as a whole word → 1.0" (`enrichment/tier1_ror.py:198`) |
| Distinctive-token minimum length | `5` characters | int | `enrichment/tier1_ror.py:318`; scoring-variant filter `:243`, `:306` | `enrichment/tier1_ror.py:322` | Fewer distinctive tokens → the `0.7` cap fires more often | Common short words can rescue a weak fuzzy score | "A fuzzy match ≥0.8 is trustworthy only if the matched variant also shares a DISTINCTIVE token (length ≥5, not a common domain word)" (`enrichment/tier1_ror.py:295-298`) |
| Initialism acronym minimum length | `3` letters | int | `enrichment/tier1_ror.py:365` | `enrichment/tier1_ror.py:377-385` | Fewer initialism rescues | Two-letter coincidences could score `1.0` | "the acronym must be ≥3 letters, must map to a run that includes a distinctive (non-common, ≥4-letter) word" (`enrichment/tier1_ror.py:360-362`) |
| Initialism run distinctive-word length | `4` characters | int | `enrichment/tier1_ror.py:384` | same condition | Stricter initialism acceptance | An acronym satisfied purely by short/common words | Same comment as above |
| `_CHILD_MATCH_THRESHOLD` | `70` | int (rapidfuzz `token_sort_ratio`) | `enrichment/orchestrator.py:633` | `enrichment/orchestrator.py:660` | Fewer Name-2 values matched to a ROR parent's children list | More false child matches accepted without a second ROR call | "rapidfuzz token_sort_ratio minimum" (`enrichment/orchestrator.py:633`); "This avoids a second ROR API [call]" (`enrichment/orchestrator.py:643`) |

### 1.4 · Tier 1 — GLEIF / LEI (company registry)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `LEI_LOOKUP_ENABLED` | `true` | bool (env) | `config.py:87`; `config.py:184` | `enrichment/orchestrator.py:1643` | n/a | `false` skips GLEIF; the company branch goes straight to the LLM | "Feature flag so the lookup can be A/B tested or disabled cheaply; when off the company branch behaves exactly as before (straight to the LLM)." (`config.py:181-182`) |
| `GLEIF_API_BASE` | `https://api.gleif.org/api/v1` | str (env) | `config.py:88`; `config.py:187`; default arg `enrichment/tier1_lei.py:213` | `enrichment/tier1_lei.py:236-237`, `:327` | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| `GLEIF_TIMEOUT_SECONDS` | `15` | float (env) | `config.py:89`; `config.py:190`; default arg `enrichment/tier1_lei.py:214` | `enrichment/tier1_lei.py:252` (`httpx.AsyncClient(timeout=)`) via `:390,404` | Slow GLEIF responses tolerated longer | Calls abort sooner; `{"matched": False, "error": True}` returned (`enrichment/tier1_lei.py:225`) | ⚠ UNDOCUMENTED — author to supply |
| `LEI_NAME_MATCH_THRESHOLD` | `88` | float, 0–100 (env) | `config.py:90`; `config.py:196`; default arg `enrichment/tier1_lei.py:216` (`88.0`) | `enrichment/tier1_lei.py:167` (`if score < threshold`) via `:271,361` | Fewer GLEIF candidates accepted; more company records fall to LLM canonicalisation | More fabricated matches accepted — the named failure is "Personalvorsorgestiftung der Pfizer AG" for "Pfizer AG" | "rapidfuzz token_sort_ratio (0-100). GLEIF's legalName filter is fulltext, not exact, so a candidate below this is rejected to avoid fabricated matches" (`config.py:192-194`) |
| `LEI_MAX_RETRIES` | `2` | int (env) | `config.py:91`; `config.py:199`; default arg `enrichment/tier1_lei.py:215` | `enrichment/tier1_lei.py:200` | More attempts on transient GLEIF errors | A single 5xx/network error fails the lookup | "Max retries (exponential backoff) on transient GLEIF errors" (`README.md:1620`) |
| GLEIF retry backoff | `0.5 * (2 ** (attempt - 1))` seconds → 0.5 s, 1.0 s | float (formula) | `enrichment/tier1_lei.py:202` | `enrichment/tier1_lei.py:207` | Longer waits between attempts | Faster re-hits on a failing endpoint | "retrying transient errors with backoff" (`enrichment/tier1_lei.py:183`) |
| GLEIF retryable condition | status is `None` or `>= 500` | predicate | `enrichment/tier1_lei.py:198` | `enrichment/tier1_lei.py:200` | n/a | n/a | "Only retry transient failures (network, timeout, 5xx). A 4xx is not going to get better on retry." (`enrichment/tier1_lei.py:195-196`) |
| GLEIF `page[size]` | `"10"` | str (API param) | `enrichment/tier1_lei.py:259` | request params at `:268` | More candidate records verified per exact query | Fewer candidates; a correct match beyond rank 10 is never seen | ⚠ UNDOCUMENTED — author to supply |
| Fuzzy-completions resolution cap | `5` (`completions[:5]`) | int | `enrichment/tier1_lei.py:340` | `enrichment/tier1_lei.py:340-358` | More per-LEI record fetches (more GLEIF calls per record) | Fewer fuzzy candidates resolved and verified | ⚠ UNDOCUMENTED — author to supply |

### 1.5 · Search (SERP) and page fetching

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `SERPAPI_KEY` | *(unset)* | str, **secret** (env) | `config.py:160` | `enrichment/orchestrator.py:773-781` (provider selection); `config.py:137-145` (startup warning) | n/a | Unset → `DuckDuckGoClient` is used instead of `SerpAPIClient` | "SERPAPI_KEY is not set — falling back to DuckDuckGo. DuckDuckGo returns lower-quality results." (`config.py:141-144`); "SerpAPI key; if absent, DuckDuckGo is used" (`README.md:1629`) |
| `num_results` — `SearchClient.search` default | `5` | int | `search/base.py:21`; `search/serpapi_client.py:22`; `search/duckduckgo_client.py:19` | `search/serpapi_client.py:41,56`; `search/duckduckgo_client.py:34,42` | Larger SERP result set per query | Fewer candidates to rank | ⚠ UNDOCUMENTED — author to supply |
| `num_results` — website Path B | `10` | int | `enrichment/website_resolver.py:468` | `enrichment/website_resolver.py:492` | More candidates scanned for a host match | Reverting toward `5` re-introduces the retrieval misses the change was made to fix | "Path B distinctive/acronym-in-host ranking …; num_results 5->10 + one unquoted retry" (commit `515cc7c`); "**Website Path B retrieval (§8)** — `num_results` 5 → 10, plus one **unquoted retry** … (recovers `Atlantic Testing Labs` → `atlantictesting.com`, `Fine Organics Limited` → `fineorganics.com`)" (`README.md:1991`) |
| Path B unquoted retry count | `1` (only when the quoted query returned nothing) | int | `enrichment/website_resolver.py:522-530` | `:529` | n/a | `0` retries — differently-branded sites stay unresolved and fall to Path C | "§8: one unquoted retry when the exact-phrase query found no valid candidate — the site may brand itself slightly differently ('…Labs' vs '…Laboratories'). Only runs on a first-pass miss; one retry maximum." (`enrichment/website_resolver.py:522-524`) |
| `num_results` — department probe SERP (site-restricted) | `5` | int | `enrichment/orchestrator.py:1183` | same call | More candidate hosts scored | Fewer candidate hosts | `README.md:745` documents the value; no rationale given — ⚠ UNDOCUMENTED — author to supply |
| `num_results` — department probe SERP (cross-domain) | `5` | int | `enrichment/orchestrator.py:1297` | same call | As above | As above | ⚠ UNDOCUMENTED — author to supply |
| `num_results` — Tier 2A contact lookup | `5` | int | `enrichment/tier2a_contact.py:330` | same call | More contact-page candidates fetched | Fewer candidates | ⚠ UNDOCUMENTED — author to supply |
| `num_results` — Tier 2B department search | `5` | int | `enrichment/tier2b_dept.py:227` | same call | More department-page candidates | Fewer candidates | ⚠ UNDOCUMENTED — author to supply |
| `num_results` — lab resolver (UC 13) | `5` | int | `enrichment/lab_resolver.py:83` | same call | More parent-department candidates | Fewer candidates | ⚠ UNDOCUMENTED — author to supply |
| `num_results` — person affiliation (Stage 2b) | `5` | int | `enrichment/person_affiliation.py:124` | same call | More affiliation snippets | Fewer snippets | ⚠ UNDOCUMENTED — author to supply |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `10` | int (env) | `config.py:110`; `config.py:212`; `PageFetcher` default `search/page_fetcher.py:69` | `search/page_fetcher.py:189`, `:220` via `enrichment/orchestrator.py:740,749` | Slow pages tolerated; per-record latency rises | Fetches abort sooner and return `None`/`[]` (`search/page_fetcher.py:91-93`) | "HTTP timeout for page fetching" (`README.md:1623`) |
| `MAX_PAGE_CONTENT_CHARS` | **conflicting: `3000` / `1500`** — see §2 | int (env) | `config.py:93` (`"3000"`); `config.py:209` (`"1500"`); `PageFetcher` default `search/page_fetcher.py:69` (`1500`) | `search/page_fetcher.py:248-249` (body-text truncation) | Larger page slices sent to the LLM; higher prompt-token cost | Body text truncated earlier with a `…` suffix | "Adjusted `max_page_content_chars` in `config.py` from 3000 to 1500 for better performance." (commit `b19cd1a`) |
| `subdomain_exists` HEAD timeout | `5` (seconds) | int (default arg) | `search/page_fetcher.py:95` | `search/page_fetcher.py:148` | Slow candidate subdomains tolerated longer | Probes give up sooner and return `False` | ⚠ UNDOCUMENTED — author to supply |
| `resolve_final_url` redirect timeout | `5` (seconds) | int (default arg) | `search/page_fetcher.py:111` | `search/page_fetcher.py:126`, `:133` | As above | Redirect chain unresolved → the probe keys off the stale base | ⚠ UNDOCUMENTED — author to supply |
| Redirect-follow HEAD→GET fallback trigger | `status_code >= 400` | int | `search/page_fetcher.py:130` | `search/page_fetcher.py:131-139` | n/a | n/a | "Some servers reject HEAD — retry with a streamed GET." (`search/page_fetcher.py:131`) |
| `subdomain_exists` accept band | `200 ≤ status < 400` | range | `search/page_fetcher.py:155` | same | n/a | n/a | "Returns `True` for any 2xx/3xx response, `False` for 4xx/5xx/timeout/DNS failure." (`search/page_fetcher.py:96-97`) |
| Page-slice truncation — title / h1 / breadcrumb | `300` characters each | int | `search/page_fetcher.py:254-256` | same | More text per authoritative slice | Slices cut shorter | ⚠ UNDOCUMENTED — author to supply |
| Anchor-text truncation (outgoing links) | `200` characters | int | `search/page_fetcher.py:213` | same | More link text scored | Less link text | ⚠ UNDOCUMENTED — author to supply |
| HTTP `User-Agent` | `BrukerMDM-Enrichment/1.0` | str | `search/page_fetcher.py:127,134,150,190,221` | same requests | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| Stripped page elements | `{script, style, nav, footer, header, aside, form, iframe}` | set | `search/page_fetcher.py:25` | `search/page_fetcher.py:244-245` | n/a | n/a | "The extractor pulls out the parts of the page that institutions actually use to name themselves … not the full body prose" (`search/page_fetcher.py:3-7`) |

### 1.6 · Website resolution (Paths B and C)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `DOMAIN_BLACKLIST` | `wikipedia.org, linkedin.com, facebook.com, twitter.com, x.com, instagram.com, youtube.com, ratemyprofessors.com, glassdoor.com, yelp.com, bbb.org, crunchbase.com, bloomberg.com, indeed.com, ziprecruiter.com` | frozenset (15 entries) | `enrichment/website_resolver.py:49-54` | `enrichment/website_resolver.py:84` via `:371` | More directories excluded | Directory/social results become eligible website candidates | "Domains to exclude from SERP candidate selection — directories, social networks, review sites, employment aggregators. The official institution / company site is never one of these." (`enrichment/website_resolver.py:47-49`) |
| `_OFFICIAL_TLDS` | `{edu, gov, org}` | frozenset | `enrichment/website_resolver.py:58` | `enrichment/website_resolver.py:396` | More TLDs grant `high` confidence to institutions | Fewer institution results reach `high`; more get flagged for review | "TLDs we treat as authoritative for research-institution / public bodies. An on-blacklist match is rejected before this is consulted." (`enrichment/website_resolver.py:56-57`); "an authoritative TLD (`.edu`/`.gov`/`.org`) grants `high` **only** with a clean (rank-2) host match" (`README.md:721`) |
| Candidate rank scale | `0` (title-only) / `1` (host match with foreign brand label) / `2` (clean host match) | int | `enrichment/website_resolver.py:381-384` | `enrichment/website_resolver.py:386-398` | n/a | n/a | "2 = distinctive/acronym host match, no foreign brand word (clean); 1 = host match but the label adds a foreign brand (sub-brand); 0 = name only overlaps the title, not the host → rejected" (`enrichment/website_resolver.py:377-380`) |
| Significant-token minimum length (Name 1) | `4` characters | int | `enrichment/website_resolver.py:95` | `enrichment/website_resolver.py:101`, `:126`, `:179` | Fewer tokens qualify → fewer candidates pass overlap | Short generic words can validate a stranger's domain | ⚠ UNDOCUMENTED — author to supply |
| `_GENERIC_NAME_TOKENS` | 28 industry words (`research, therapeutics, diagnostics, medical, instruments, sciences, science, laboratories, laboratory, labs, technologies, technology, solutions, systems, group, holdings, international, global, pharma, pharmaceutical, bio, biotech, health, healthcare, services, consulting, partners, associates`) | frozenset | `enrichment/website_resolver.py:107-113` | `enrichment/website_resolver.py:118` → `:136` | More words treated as non-distinctive; stricter host matching | "a stranger's domain must not be validated just because it shares one of these" — the guard weakens | "Generic industry words that do NOT distinctively identify an organisation … Mirrors the distinctive-token guard ROR applies upstream." (`enrichment/website_resolver.py:104-106`) |
| Foreign-brand label minimum length | `4` characters | int | `enrichment/website_resolver.py:181` (and the read-only mirror `:240`) | `enrichment/website_resolver.py:182-184` | Fewer label parts inspected → fewer sub-brand rejections | Short connectors ("of", "and") would be treated as foreign brand words | "short connector ('of', 'and') — never distinctive" (`enrichment/website_resolver.py:182`) |
| Path C sentinel values | `"", null, none, unknown, n/a, na` (case-insensitive) | set | `enrichment/website_resolver.py:614` | `enrichment/website_resolver.py:615-616` | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| Path C confidence | always `low` | str (literal) | `enrichment/website_resolver.py:633` | `enrichment/orchestrator.py:894,914` (write + flag) | n/a | n/a | "Result is always returned as `confidence='low'` when a URL is produced — the orchestrator writes it to `website_url` and flags the record for manual review." (`enrichment/website_resolver.py:562-565`) |
| `WEBSITE_TRACE` | `false` | bool (env) | `config.py:118`; `config.py:247` | `enrichment/orchestrator.py:894`, `:914` → `enrichment/website_resolver.py:478,509,582` | `true` emits one JSON trace line per candidate on `enrichment.trace.website` | n/a | "Diagnostic-only: when true, the Path B / Path C website resolver emits a structured per-candidate JSON trace … Purely additive — resolution behaviour is unchanged. Default off." (`config.py:115-118`) |

### 1.7 · Department-domain probe (`_probe_department_url`)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `DEPT_PROBE_CROSS_DOMAIN` | `false` | bool (env) | `config.py:114`; `config.py:166-168` | `enrichment/orchestrator.py:1277` | `true` runs a second, unrestricted SERP call per unresolved department | n/a | "When False (default) the department-domain probe issues at most one SERP call (the site-restricted query). The cross-domain fallback query — which catches departments hosted on a separate brand domain (e.g. hopkinsmedicine.org) — only runs when this is enabled, so the common case stays at one SERP call per record." (`config.py:161-165`); "`DEPT_PROBE_CROSS_DOMAIN` default → `false` (§6) — matches the documented intent" (`README.md:1993`). ⚠ `.env.example` contradicts this — see §2 |
| Host-prefix match score | `+3` | int | `enrichment/orchestrator.py:248` | `enrichment/orchestrator.py:1154`, `:1204` | n/a — the host match is mandatory for any positive score | n/a | "Strict rule: the host prefix MUST contain a significant token or the acronym (substring match) for any positive score. Path / title matches alone aren't enough — that's how parent hosts like `fas.harvard.edu` (FAS) or `krieger.jhu.edu` (umbrella school) were sneaking in." (`enrichment/orchestrator.py:216-221`) |
| Path match bonus | `+1` | int | `enrichment/orchestrator.py:254` | same scoring function | Path signal outweighs title signal further | Path evidence contributes nothing | "only reward a path match on a real department path — a generic (news/events) path earns no bonus and a deep/dated path is penalised" (`enrichment/orchestrator.py:251-252`) |
| Title match bonus | `+1` | int | `enrichment/orchestrator.py:257` | same | Title signal weighs more | Title evidence contributes nothing | ⚠ UNDOCUMENTED — author to supply |
| Path-penalty cap | `min(2, penalty)` | int | `enrichment/orchestrator.py:255` | same | A deep path could drive the score below the host baseline | Penalty stops discriminating deep paths | ⚠ UNDOCUMENTED — author to supply |
| Canonicality penalty — depth | `max(0, len(segments) - 1)` | int (formula) | `enrichment/orchestrator.py:164` | `enrichment/orchestrator.py:255`, `:1257` | n/a | n/a | "a penalty (≥0) for deep / dated / sub-page paths, so a department landing page outranks an archived or sub-section page at the same host" (`enrichment/orchestrator.py:161-162`) |
| Canonicality penalty — dated path | `+5` (any 4-digit year segment) | int | `enrichment/orchestrator.py:165-166` | as above | Dated pages pushed further down the ranking | Archived pages can tie a landing page | "dated / archive content" (`enrichment/orchestrator.py:165`); "so an archived event URL no longer ties a landing page" (`enrichment/orchestrator.py:1236`) |
| Canonicality penalty — sub-page segment | `+3` | int | `enrichment/orchestrator.py:167-168` | as above | Sub-pages pushed further down | "…/chemistry/undergrad/" can outrank the landing page | "sub-pages of a department (penalised, not rejected — the landing page is preferred over '…/chemistry/undergrad/')" (`enrichment/orchestrator.py:143-144`) |
| `_GENERIC_HOST_PREFIXES` | 28 subdomains (`professorships, inside, calendar, news, alumni, admin, hr, store, shop, give, donate, support, events, directory, library, libraries, career, careers, jobs, search, secure, my, mail, email, wiki, intranet, media, press`) | set | `enrichment/orchestrator.py:103-109` | `enrichment/orchestrator.py:239-240` (forced score `0`) | More hosts excluded outright | `professorships.jhu.edu` (the named regression) can win the probe | "Subdomains that are administrative/cross-cutting, never a department home — pre-empt the SERP probe from latching onto them." (`enrichment/orchestrator.py:101-102`); named failures at `:975-976` |
| `_GENERIC_PATH_SEGMENTS` | 22 segments (`news, news-events, events, event, story, stories, article, articles, blog, calendar, archive, colloquium, seminar, admin, hr, library, libraries, careers, career, directory, media, press`) | set | `enrichment/orchestrator.py:137-142` | `enrichment/orchestrator.py:152-157` → `:253`, `:1251` | More paths rejected as non-department | News/event pages accepted as department homes | "§5b: path segments that are non-department content (news, events, archived stories, calendars). A candidate whose path contains one of these is not a department landing page." (`enrichment/orchestrator.py:134-136`) |
| `_SUBPAGE_PATH_SEGMENTS` | 13 segments (`undergrad, undergraduate, graduate, grad, people, faculty, staff, contact, admissions, apply, courses, alumni, giving`) | set | `enrichment/orchestrator.py:145-148` | `enrichment/orchestrator.py:167` | More paths penalised as sub-pages | Sub-pages compete with landing pages | See canonicality-penalty rationale above |
| `_THIRD_PARTY_DOMAINS` | 23 registrable domains (`wikipedia.org, linkedin.com, facebook.com, twitter.com, x.com, youtube.com, instagram.com, reddit.com, researchgate.net, scholar.google.com, google.com, amazon.com, indeed.com, glassdoor.com, pubmed.gov, ncbi.nlm.nih.gov, nih.gov, doi.org, academia.edu, github.com, github.io, medium.com, substack.com`) | set | `enrichment/orchestrator.py:113-120` | `enrichment/orchestrator.py:123-132` | More platforms excluded | A third-party platform could be written as a department domain | "Registrable domains that are third-party platforms — never represent a department's web home. Used to filter no-site SERP results." (`enrichment/orchestrator.py:111-112`) |
| Candidate-subdomain acronym length band | `2 ≤ len ≤ 6` | int range | `enrichment/orchestrator.py:1091` | `enrichment/orchestrator.py:1092` | Longer acronyms probed | Fewer acronym subdomains probed | ⚠ UNDOCUMENTED — author to supply |
| Candidate tokens probed | top `2` longest tokens of length `≥ 4` | int | `enrichment/orchestrator.py:1093-1096` | `enrichment/orchestrator.py:1109-1115` | More HEAD/GET probes per record | Fewer subdomain candidates verified | ⚠ UNDOCUMENTED — author to supply |
| Abbreviated-subdomain prefix lengths | `(4, 3)` | tuple | `enrichment/orchestrator.py:1103` | `enrichment/orchestrator.py:1104-1107` | More prefix candidates probed | "chem" ← "chemistry" style subdomains not probed | "Departments often use an abbreviated subdomain ('chem' ← 'chemistry', 'phys' ← 'physics', 'math' ← 'mathematics'). Probe short prefixes of the token too." (`enrichment/orchestrator.py:1100-1102`) |
| Scored candidates verified per stage | top `5` (`scored[:5]`) | int | `enrichment/orchestrator.py:1161`, `:1211` | same loops | More page fetches per record | A correct host ranked 6th is never verified | ⚠ UNDOCUMENTED — author to supply |
| Segment/needle shared-prefix minimum | `3` characters | int | `enrichment/orchestrator.py:201` | `enrichment/orchestrator.py:200-203` | Stricter abbreviation matching | Two-character coincidences match | "Shared leading prefix of ≥3 chars — abbreviation either direction." (`enrichment/orchestrator.py:199`) |
| Verification — phrase length gate | `≥ 4` characters | int | `enrichment/orchestrator.py:1380` | same condition | Longer phrases required for the fast-path accept | Very short phrases accept trivially | ⚠ UNDOCUMENTED — author to supply |
| Verification — morphological prefix | `≥ 5` shared leading characters | int | `enrichment/orchestrator.py:1404` | `enrichment/orchestrator.py:1397-1406` | Stricter variant matching | "physic"al ← "physic"s style variants no longer verify | "§5d: accept morphological variants, not just the literal token, so physics.nist.gov ('Physical Measurement Laboratory') verifies for a 'Physics' needle." (`enrichment/orchestrator.py:1388-1392`) |
| Verification — needle-count threshold | `≥ 2` needles, or `≥ 1` when only one needle exists | int | `enrichment/orchestrator.py:1408-1411` | same | Fewer pages verify; more departments left unresolved | "science.mit.edu is the School of Science, not the Computer Science department" — the rejection the threshold exists for | "at least 2 significant needles (tokens + acronym) appear there (or 1 needle, when only one is available). This rejects pages that resolve but don't describe the dept" (`enrichment/orchestrator.py:1356-1361`); "The needle-count thresholds are unchanged (≥2, or ≥1 for a single needle), so science.mit.edu still fails a Computer Science query." (`enrichment/orchestrator.py:1393-1394`) |
| Significant department-token minimum length | `3` characters (regex `[A-Za-z]{3,}`) | int | `enrichment/orchestrator.py:172` | `enrichment/orchestrator.py:181-185` | Fewer tokens qualify | Two-letter fragments become department tokens | "Lowercased alpha words ≥3 chars, minus generic descriptors. The result is what we expect to see in a real department URL." (`enrichment/orchestrator.py:178-179`) |

### 1.8 · Tiers 2A, 2B, 3

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `FUZZY_MATCH_THRESHOLD` | `80` | int, 0–100 (env) | `config.py:92`; `config.py:204` | `enrichment/tier2a_contact.py:451` via `:170,425` | Fewer existing Name-2 values accepted as matching the contact page → more are replaced wholesale | More weak matches normalised to the page's official form | "RapidFuzz threshold for name matching" (`README.md:1621`); "≥ 80 exact/partial: normalise to official format; < 80: name2 is wrong, replace with page version" (`enrichment/tier2a_contact.py:430-431`) |
| Tier 2A near-exact cut-off | `95` | int, 0–100 | `enrichment/tier2a_contact.py:456` | same | Fewer results marked `exact`/`verified`; more marked `partial` and flagged for review | Weaker matches marked `exact` and pass unflagged | "Near-exact match" (`enrichment/tier2a_contact.py:457`) |
| Tier 2B match-band cut-offs | `exact ≥ 90`, `partial ≥ 60`, else `no_match` | int, 0–100 | `enrichment/tier2b_dept.py:152` | same expression | Stricter labels | Weaker matches labelled `exact` | ⚠ UNDOCUMENTED — author to supply |
| Tier 2B confidence assignment | on-domain → `medium`, off-domain → `low`; `flag_for_review` always `True` | str | `enrichment/tier2b_dept.py:156-162` | same | n/a | n/a | "Extracted by LLM from official domain page" / "Extracted by LLM from non-official source" (`enrichment/tier2b_dept.py:158,161`) |
| Tier 3 address-in-name overlap gate | `≥ 0.5` token overlap with the record's street | float | `enrichment/tier3_llm.py:46` | same condition | Fewer Tier-3 name suggestions rejected as address content | More legitimate names rejected as address-like | "True when a Tier 3 NAME suggestion is actually address content — a postal code, a house-number + street-type pattern, or a high token overlap with the record's own street field (i.e. copied wholesale from the street)." (`enrichment/tier3_llm.py:33-35`) |
| Tier 3 token minimum length | `3` characters (regex `[A-Za-z]{3,}`) | int | `enrichment/tier3_llm.py:29` | `enrichment/tier3_llm.py:45` | Fewer tokens in the overlap denominator | Short words inflate the overlap ratio | ⚠ UNDOCUMENTED — author to supply |

### 1.9 · Preprocessing, text normalisation, address stage, issue detection

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| Canonical-name dedupe fuzz ratio | `92` | int, 0–100 | `enrichment/preprocess.py:1776`; `enrichment/orchestrator.py:547` | same conditions | Fewer near-identical names collapsed | Genuinely distinct units collapsed together | "The 92 threshold is [set so that] … ('Department of Main Receiving' vs 'Department of Main Receivingt')" (`enrichment/preprocess.py:1769`) — a typo-tolerance gate |
| `_SPELLING_VARIANT_TOKEN_RATIO` | `85.0` | float, 0–100 | `utils/text_utils.py:753` | `utils/text_utils.py:768` | Fewer token pairs treated as spelling variants | Unrelated tokens treated as typos of each other | "…`_SPELLING_VARIANT_TOKEN_RATIO` — i.e. one is a typo of the other." (`utils/text_utils.py:761`) |
| Acronym letter-count band | `2 ≤ len ≤ 8` | int range | `enrichment/preprocess.py:368`, `:414` | `enrichment/preprocess.py:419-421` | Longer strings admitted as acronyms | Fewer acronym/full-form pairs detected | ⚠ UNDOCUMENTED — author to supply |
| Acronym full-form minimum words | `≥ 3` | int | `enrichment/preprocess.py:419`, `:421` | same | Fewer pairs qualify | "proper-noun hyphenations ('Heriot-Watt University'…)" become acronym candidates | "side must have >=3 words so proper-noun hyphenations ('Heriot-Watt University'…)" (`enrichment/preprocess.py:393`) |
| `_RESIDUAL_CONFIDENCE_THRESHOLD` | `0.85` | float, 0–1 | `enrichment/address_processing.py:657` | `enrichment/address_processing.py:729` | Fewer LLM residual classifications acted on; more rows carry `G1-ADDR-009` | Low-confidence LLM classifications move content out of street slots | ⚠ UNDOCUMENTED — author to supply |
| `_SAP_NAME_LIMIT` | `140` | int | `enrichment/issue_detection.py:121` | `enrichment/issue_detection.py:442` (`G4-NAME-015`) | Fewer records flagged as over-length | More records flagged | "SAP name-field length limit (Name 1–4 combined)." (`enrichment/issue_detection.py:120`) |
| Search-term width (SORT1/SORT2) | `32` characters | int | `enrichment/search_terms.py:392` (default arg), `:410`, `:413`, `:551` | `enrichment/search_terms.py:409-410` (terminal normalisation), `:551` (fill) | Terms exceed the SAP field width | Terms truncated earlier on a word boundary | "truncate to 32 chars on a word boundary (SAP SORT1/SORT2 width)" (`enrichment/search_terms.py:405-406`); "trimmed, internal-whitespace-collapsed, uppercased, and truncated to **32 chars** on a word boundary (SAP SORT1/SORT2 width)" (`README.md:1989`) |
| Issue catalogue size | 36 codes, G1–G5 | dict | `enrichment/issue_detection.py:75-118` | `enrichment/issue_detection.py:504-510` | n/a | n/a | Two codes (`G1-ADDR-009`, `G4-ADDR-025`) are marked "LLM-only — never emitted" (`enrichment/issue_detection.py:88,112`) |

### 1.10 · Phase 2 — dedup clustering (`/api/dedup/cluster-block`)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `SIG_PARTITION_THRESHOLD` | `12` | int (env) | `dedup/adjudicator.py:36` (`DEFAULT_SIG_PARTITION_THRESHOLD`), resolved `:948-949` | `dedup/adjudicator.py:849` (`elif n <= threshold`) | More blocks use Mode A (one partition call per Name-2 bucket) with larger prompts | More blocks use Mode B — one LLM call per signature, i.e. O(signatures) calls | "Signature count at/below which a block uses one partition call (Mode A); above it, incremental canonical assignment (Mode B)." (`.env.example`; `README.md:1667`); mode-cost table `README.md:1239-1243` |
| `DEDUP_MAX_CONCURRENCY` | `5` | int (env) | `dedup/adjudicator.py:37` (`DEFAULT_DEDUP_MAX_CONCURRENCY`), resolved `:950-951` | `dedup/adjudicator.py:952` (`asyncio.Semaphore(max(1, concurrency))`) | More in-flight adjudicator calls; faster wall-clock, higher 429 risk | Serialised adjudication; longer request latency | "Max in-flight adjudicator LLM calls across all blocks in a request." (`.env.example`; `README.md:1668`) |
| `NAME_CANDIDATE_THRESHOLD` | `0.85` | float, 0–1 (Jaro-Winkler) | `config.py:107`; `config.py:230`; `dedup/adjudicator.py:38`; resolved `:917-919` | `dedup/candidates.py:149` | Fewer residue pairs nominated → fewer LLM calls, more missed merges | More nominations → more LLM calls per block, closer to the `MAX_CANDIDATES_PER_BLOCK` cap | "A pair of signatures becomes an LLM adjudication candidate when suffix-stripped name similarity (Jaro-Winkler) reaches NAME_CANDIDATE_THRESHOLD … Nomination never merges — the LLM verdict decides." (`config.py:102-106`) |
| `TOKEN_CANDIDATE_THRESHOLD` | `0.6` | float, 0–1 (token-set Jaccard) | `config.py:108`; `config.py:233`; `dedup/adjudicator.py:39`; resolved `:920-922` | `dedup/candidates.py:153` | Fewer token-overlap nominations | More nominations, more LLM calls | Same `config.py:102-106` block |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | int (env) | `config.py:109`; `config.py:236`; `dedup/adjudicator.py:40`; resolved `:923-925` | `dedup/adjudicator.py` residue pass (cap applied to the ordered candidate list; over the cap the block routes to `manual_review`) | More LLM calls permitted per block before the manual-review escape | Blocks route to `manual_review` sooner; `candidate_cap_exceeded` is emitted (`dedup/scoring.py:416,480-489`) | "MAX_CANDIDATES_PER_BLOCK caps LLM calls per block; over the cap the block routes to manual_review." (`config.py:105-106`); "Over MAX_CANDIDATES_PER_BLOCK LLM calls the block routes to manual_review." (`.env.example`) |
| Candidate nomination priority | `id-convergence > name similarity > token overlap` | ordering | `dedup/candidates.py:139-156`; sort key `:120` | `dedup/candidates.py:195` | n/a | n/a | "Priority when several fire: id-convergence > name similarity > token overlap (a merge is never implied — this only picks the LLM candidate)." (`dedup/candidates.py:139-140`); "the LLM-call cap is applied by the caller against this ordered list, so id-convergence pairs are retained before name/token pairs when the cap trips" (`dedup/candidates.py:179-181`) |
| `CLUSTER_ID_PREFIX` | `c_` | str | `dedup/cluster_key.py:13` | cluster-id derivation; matches the DS view's observed `c_22b1a6e41a78` etc. (`CONTEXT-EXTERNAL.md:392-393`) | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |

### 1.11 · Phase 2 — golden-record election (`/api/dedup/score`)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `CONFIDENCE_MERGE_THRESHOLD` | `0.95` | float, 0–1 (env) | `config.py:100`; `config.py:224`; `dedup/scoring.py:48` (`DEFAULT_CONFIDENCE_MERGE_THRESHOLD`), resolved `:1004-1017` | `dedup/scoring.py:1119` (election demotion); `:513` (`low_confidence_merge` issue) | More clusters demoted to `manual_review`; more human review, fewer auto-proposals | Lower-confidence merges pass as `proposed` without human confirmation | "a duplicate merge whose adjudication confidence is below this keeps its cluster membership but enters election as manual_review (a human confirms before anything is blocked). Retuning it never re-runs the LLM — election reads the confidence persisted by clustering." (`config.py:95-99`) |
| Cluster confidence aggregation | `min(confidences)` over the cluster | function | `dedup/scoring.py:513`; `_cluster_merge_confidence` `dedup/scoring.py:1020-1023` | `dedup/scoring.py:1119` | n/a | n/a | "Conservative on purpose: if any member joined below threshold the whole [cluster is demoted]" (`dedup/scoring.py:1023`) |
| Tie-break ordering | `-total`, `-last_order_year`, `-equipment_count`, `-company_code_count`, lowest `row_id` | tuple key | `dedup/scoring.py:939-955` | `elect_golden_records` (`dedup/scoring.py:1033`+) | n/a | n/a | "UNCONFIRMED ordering (confirm with Bernd): total score, most recent last_order_year, equipment_count, company_code_count, then LOWEST row_id — compared numerically when every row_id in the cluster parses as an integer, else lexically. row_id is the final uniqueness guarantee, so the winner is invariant under input shuffling." (`dedup/scoring.py:941-946`) — **explicitly not agreed with the industry supervisor** |
| Weights-override rule | all-or-nothing over every `(criterion, band)` pair | policy | `dedup/scoring.py:626-660` | `api/routes.py:917-923`; `dedup/scoring_xlsx.py` Weights sheet | n/a | n/a | "all-or-nothing. Every (criterion, band) pair in `expected` (dedup/weights.json) must be present with a numeric Points value, else the WHOLE candidate is rejected — a half-applied retune is worse than none." (`dedup/scoring.py:632-635`) |
| `weights_version` fingerprint length | `12` hex characters (sha256 prefix) | int | `dedup/scoring.py:615` | written onto every scored row | Longer fingerprint | Higher collision probability across weight tables | "Stable 12-hex fingerprint of the weights table (sha256 of the canonical JSON). Written onto every scored row so a proposal and its later approval can be checked for score drift when weights were retuned in between." (`dedup/scoring.py:611-613`) |
| Duplicate `row_id` policy | hard error → HTTP 400 | policy | `dedup/scoring.py:78-83`; `api/routes.py:927-931` | same | n/a | n/a | "The one hard error is a duplicated row_id in a single request — that means a broken upstream join, and scoring it would double-elect." (`dedup/scoring.py:11-13`) |
| Unmatched-value policy | score `0`, never raise | policy | `dedup/scoring.py:725-780` | `score_row` (`dedup/scoring.py:813`) | n/a | n/a | "The real CRM extract is ~half empty and dirty. Scoring is therefore permissive: a missing or unrecognised value scores 0 (with a warning when the value was present but unrecognised) and NEVER raises or fails the batch." (`dedup/scoring.py:9-11`) |

### 1.12 · Concurrency, request shape, and logging

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `EnrichmentOptions.max_concurrency` | `5` (bounded `ge=1, le=20`) | int (request field) | `api/models.py:289` | `enrichment/orchestrator.py:797` (`asyncio.Semaphore`) | More records enriched in parallel per batch; higher concurrent load on ROR/GLEIF/SERP/LLM | Records processed more serially; longer batch wall-clock | ⚠ UNDOCUMENTED — author to supply |
| `/enrich/file` `max_concurrency` query parameter | `5` (bounded `ge=1, le=20`) | int (query) | `api/routes.py:521` | `api/routes.py:547` → orchestrator | As above | As above | ⚠ UNDOCUMENTED — author to supply |
| `DEFAULT_MAX_CONCURRENCY` | `5` | int (env) | `config.py:94`; `config.py:217` | `api/routes.py:1115` (`/tiers` response only) — **not** the semaphore; see §5 | No runtime effect on processing | No runtime effect on processing | "Default concurrent record processing limit" (`README.md:1624`) — ⚠ the README description does not match the consumption site |
| `EnrichmentRequest.records` minimum | `min_length=1` | int | `api/models.py:296` | Pydantic validation | Larger minimum batch | Empty batches accepted | ⚠ UNDOCUMENTED — author to supply |
| `LOG_LEVEL` | `INFO` | str (env) | `config.py:113`; `config.py:244` | `api/middleware.py:85` → `logging.basicConfig` | (Toward `DEBUG`) more volume | (Toward `ERROR`) less volume | ⚠ UNDOCUMENTED — author to supply |
| `LOG_FILE` | *(unset)* → `logs/enrichment_api.log` | path (env) | `config.py:252`; `api/middleware.py:98-100` | `api/middleware.py:102-107` | n/a | Empty string (`LOG_FILE=""`) disables file logging | "Log file path. None => configure_logging uses its default (logs/enrichment_api.log); set LOG_FILE=\"\" to disable file logging." (`config.py:250-251`) |
| Log rotation size | `10 * 1024 * 1024` bytes | int | `api/middleware.py:106` | `RotatingFileHandler` | Larger files before rotation | More frequent rotation | "The file rotates at ~10 MB, keeping 5 backups, so it never grows unbounded." (`api/middleware.py:83`) |
| Log backup count | `5` | int | `api/middleware.py:106` | `RotatingFileHandler` | More history retained on disk | Less history retained | Same comment as above |
| Suppressed library log levels | `httpx`, `httpcore`, `openai`, `urllib3` → `WARNING` | level | `api/middleware.py:132-135` | same | n/a | n/a | "Quiet noisy libraries" (`api/middleware.py:131`) |
| Request-ID length | `8` characters (UUID4 prefix) | int | `api/middleware.py:22` | `X-Request-ID` header (`api/middleware.py:58`) | Lower collision probability | Higher collision probability | ⚠ UNDOCUMENTED — author to supply |

### 1.13 · Runtime, hosting, and deployment configuration

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `MOCK_EXTERNAL_CALLS` | `false` | bool (env) | `config.py:111`; `config.py:241` | `api/routes.py:58`, `:673`, `:83`, `:1117` | `true` substitutes mock clients — no real API calls | n/a | "Use mock clients (no real API calls)" (`README.md:1632`) |
| `ENV` | `production` | str (env) | `config.py:112`; `config.py:243` | `Settings.env` | n/a | n/a | "Set to `local` for development (enables dotenv loading)" (`README.md:1633`). ⚠ Code note: `load_dotenv()` is now unconditional (`config.py:17-22`), so `ENV` no longer gates it — "The old conditional `if os.getenv(\"ENV\") == \"local\"` was a chicken-and-egg bug" (`config.py:19-21`) |
| uvicorn host / port / reload (local dev) | `0.0.0.0` / `8000` / `True` | str, int, bool | `main.py:8` | `uvicorn.run` | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| Azure Functions HTTP auth level | `ANONYMOUS` | enum | `function_app.py:12` | `func.FunctionApp(http_auth_level=)` | n/a | A key-based level would require ADF to present a function key | ⚠ UNDOCUMENTED — author to supply |
| Functions route prefix | `""` (empty) | str | `host.json:13` | Azure Functions host | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| Functions extension bundle | `[4.*, 5.0.0)` | version range | `host.json:18` | Azure Functions host | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| Application Insights sampling | `isEnabled: true`, `excludedTypes: "Request"` | bool / str | `host.json:5-8` | Azure Functions host | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| `functionTimeout` | *(not set)* | — | absent from `host.json:1-20` | — | n/a | n/a | Platform default for the (unconfirmed) hosting plan applies; the plan is `CONTEXT-EXTERNAL.md:446` open item 6 — ⚠ the ceiling must not be stated until the plan is confirmed (`02_ARCHITECTURE.md:279-284`) |
| `pytest` async mode / testpaths | `strict` / `tests` | str | `pytest.ini:2-3` | pytest | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |

### 1.14 · Azure Data Factory activity policies [EXPORT]

Both pipelines were published `2026-07-29T12:09:37Z` and are reproduced verbatim in
`CONTEXT-EXTERNAL.md`. Every activity in both pipelines carries the identical policy block.

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| Activity `timeout` (all 7 activities) | `0.12:00:00` (12 hours) | ADF timespan | `CONTEXT-EXTERNAL.md:53,95,125,153` (Enrichment); `:215,245,273` (Deduplication) | ADF activity runtime | Longer before an activity is abandoned | Long `/enrich` batches or long-running Lookups abort | ⚠ UNDOCUMENTED — author to supply. Note the Azure Functions plan ceiling, not this timeout, bounds a single `/enrich` call (`02_ARCHITECTURE.md:282-284`) |
| Activity `retry` (all 7 activities) | `0` | int | `CONTEXT-EXTERNAL.md:54,96,126,154,216,246,274` | ADF activity runtime | Transient `Web1`/`Merge Back` failures retried instead of stopping the sequential ForEach | Already at the floor | ⚠ UNDOCUMENTED — author to supply. [AUTHOR] "Before 2026-08-21 the pipeline is to be amended to … [set] a retry policy above 0 on `Web1` and `Merge Back`." (`CONTEXT-EXTERNAL.md:194-197`) |
| `retryIntervalInSeconds` (all 7 activities) | `30` | int | `CONTEXT-EXTERNAL.md:55,97,127,155,217,247,275` | ADF activity runtime | Longer pause between retries | Faster retries | ⚠ UNDOCUMENTED — author to supply. **Inert while `retry: 0`** |
| `secureOutput` / `secureInput` (all 7 activities) | `false` / `false` | bool | `CONTEXT-EXTERNAL.md:56-57,98-99,128-129,156-157,218-219,248-249,276-277` | ADF activity logging | `true` would redact activity payloads from ADF run history | n/a | ⚠ UNDOCUMENTED — author to supply |
| Enrichment page size | `50` rows | int (in T-SQL) | `CONTEXT-EXTERNAL.md:64` (`rn * 50 AS offset`); `:106` (`FETCH NEXT 50 ROWS ONLY`) | ADF `Lookup2` / `Lookup1` | Fewer, larger `/enrich` calls — a single call must finish inside the Functions timeout | More, smaller calls — more round trips, more `Merge Back` invocations | ⚠ UNDOCUMENTED — author to supply. The two `50` literals must be changed together — see §2 |
| `ForEach1.isSequential` | `true` | bool | `CONTEXT-EXTERNAL.md:88` | ADF `ForEach1` | n/a | `false` would run offsets in parallel (default batch count applies) | ⚠ UNDOCUMENTED — author to supply. Consequence documented: "the sequential ForEach stops at the failing iteration and does not process subsequent offsets" (`02_ARCHITECTURE.md:220-223`) |
| `firstRowOnly` (all Lookups) | `false` | bool | `CONTEXT-EXTERNAL.md:73,115,235` | ADF Lookup activities | n/a | `true` would return only the first row | ⚠ UNDOCUMENTED — author to supply |
| Deduplication batching | none — one unbatched Lookup over all of `test_77.Validation` | — | `CONTEXT-EXTERNAL.md:224-236` | ADF `Lookup1` | n/a | n/a | ⚠ At risk against ADF's 5,000-row / 4 MB Lookup ceiling (`02_ARCHITECTURE.md:489`). [AUTHOR] "to be amended … to iterate distinct `block_id` values through a ForEach" (`CONTEXT-EXTERNAL.md:312-314`) |
| Integration runtime | `AutoResolveIntegrationRuntime` | str | `CONTEXT-EXTERNAL.md:137,257` | ADF `Web1` | n/a | n/a | ⚠ UNDOCUMENTED — author to supply |
| Service endpoint (both pipelines) | `https://mdm-pipeline-api.azurewebsites.net` | URL | `CONTEXT-EXTERNAL.md:135,255` | ADF `Web1` `url` | n/a | n/a | Cross-tenant public hop, Tillit → Bruker spoke (`02_ARCHITECTURE.md:402-405`) |

### 1.15 · Parameters outside this repository

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| Address-validation auto-write-back confidence | `80%` | percentage | `CONTEXT-EXTERNAL.md:423` [AUTHOR] — ⚠ the ADF pipeline implementing step 6 is not exported (`CONTEXT-EXTERNAL.md:442`) | ⚠ not in this repository — no code path in the service reads or applies it (verified: the only `0.8` literals in the codebase belong to `ROR_CONFIDENCE_THRESHOLD`, `config.py:86,177`, `enrichment/tier1_ror.py:573`) | Fewer addresses written back automatically; more left for a steward | More validated addresses committed without review | ⚠ UNDOCUMENTED — author to supply. ⚠ UNVERIFIED — the value, the validating service, and the comparison operator (`>` vs `≥`) are all unevidenced by any artefact; only the author's statement "auto write-back above 80% confidence" exists |
| DATAshaper validation-rule weights | rule with the highest weight decides; weight `1` is the default fall-back rule | int | ⚠ configured in the DATAshaper SaaS interface; no file export (`CONTEXT-EXTERNAL.md:337-339`) | DS validation engine | A rule overrides lower-weighted rules | The default weight-1 rule wins | "we have multiple rules, but it's simple because there's the weights. If we have multiple rules that are valid, then it's the rule with the highest weight that is the one that is the decider." (`Datashaper-Tutorial-Part2.txt:56`); "the rule with the weight 1, that's the default rule. And that one should be configured that it always applies." (`Datashaper-Tutorial-Part2.txt:65`) |

### 1.16 · Fix 2 — the three unchanged-Name-1 states (`enrichment/unchanged_state.py`)

The split introduces **no numeric threshold**. Every decision it takes reuses an existing
comparison: `normalize_key` equality (the same predicate Stage 5 uses to decide a "correction"
is not one) and the domain ownership guard's own `verified_by` verdict. That is deliberate — a
new state whose boundary is a new number would need its own derivation and its own tuning
batch; a new state whose boundary is an existing guard inherits both.

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `NAME_TYING_OWNERSHIP_CONDITIONS` | `{name, serp, registry}` | frozenset | `enrichment/unchanged_state.py` | `unchanged_state.resolve` | Adding `email` would treat a non-generic address on the record as corroborating the *name*; more rows become `unchanged-verified` and lose their flag | Removing `serp` drops on-domain-title evidence; those rows fall back to `unchanged-unresolved` and are flagged | "`email` is excluded on purpose. A non-generic address on the record says which organisation the record belongs to; it says nothing about whether the Name 1 *string* is that organisation's name, which is the question here. `unguarded` is excluded because the guard was switched off, so nothing was checked at all." (`enrichment/unchanged_state.py`). Measured — of 24 verified rows in run F, 9 came in on `name` and 3 on `serp` (`unchanged_split_report.md`) |
| `unchanged-confirmed` equality predicate | `normalize_key(proposal) == normalize_key(input)` | function | `enrichment/unchanged_state.py` (`resolve`) | same | n/a — not orderable | n/a | "the same equality Stage 5 uses to decide that a 'correction' is punctuation and not a new name" (`enrichment/unchanged_state.py`); `normalize_key` reused from `dedup/signatures.py`, never reimplemented |
| Registry near-match as corroboration | **not implemented** | — | — | — | n/a | n/a | Evidence-based decision, not an omission: the only qualifying candidate across the 41 retained-Name-1 rows in run A was `TORAY ADVANCED COMPOSITES USA INC.` at **80.7 / 88** against "Advanced Composites Inc" — a different legal entity (`unchanged_split_report.md`). Recorded as an open item rather than silently defaulted |
| `enrichment_status` for verified / confirmed | `verified` | enum | `enrichment/unchanged_state.enrichment_status_for` | `enrichment/orchestrator._resolve_unchanged_name1` | n/a — bounded enum. Substituting `enriched` would claim the pipeline changed the value, which it did not | Substituting `unresolved` restores the "Warning — manual review" severity on a record the pipeline has just declined to flag, which is the contradiction the mapping exists to avoid | "`verified` \| Info issue (confirmed correct) \| Values confirmed, logged for audit" (`README.md`, Status-to-Severity Mapping); "`enriched` is never downgraded: a record whose Name 1 was retained can still have had Name 2 or the domain genuinely enriched" (`enrichment/unchanged_state.py`) |

### 1.17 · Fix 3 — page-read corroborator (`enrichment/page_corroborator.py`)

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|
| `PAGE_CORROBORATION_ENABLED` | `true` | bool (env) | `config.py` (`Settings.page_corroboration_enabled`); `.env.example` | `enrichment/orchestrator._corroborate_domain` | n/a | `false` → no page is fetched, no `operating_name` written, no `domain-unverified` cleared; behaviour reverts exactly to pre-Fix-3 | Feature flag following the `LEI_LOOKUP_ENABLED` / `DOMAIN_OWNERSHIP_GUARD_ENABLED` pattern so the step can be A/B disabled cheaply (`config.py`) |
| `PAGE_NAME_MATCH_THRESHOLD` | `88` | float (env) | `config.py` (`Settings.page_name_match_threshold`); `.env.example` | `page_corroborator.compare` | Fewer pages count as naming this organisation → fewer corroborations, more `name_mismatch` | More pages accepted as corroborating → an unrelated site can clear a `domain-unverified` flag | **DERIVATION:** a supplied-name-vs-stated-legal-name comparison — the same shape as GLEIF's name-verification guard — reusing that guard's scorer verbatim (`enrichment/tier1_lei._name_match_score`: `token_sort_ratio`, max of raw and legal-form-stripped). It therefore inherits `LEI_NAME_MATCH_THRESHOLD`'s derivation and its value. A **separate knob** rather than a reference so retuning the registry guard does not silently retune what counts as a corroborating page, and vice versa; the default is deliberately identical (`config.py`) |
| Withdrawal rule — required location scope | `{region, country}` | frozenset (inline) | `enrichment/orchestrator._corroborate_domain` | same | Adding `city` restores the behaviour that withdrew four correct domains | Removing both disables withdrawal entirely; a wrong-entity accepted domain is only annotated | **DERIVED FROM THE BATCH.** Name score alone withdrew 4 correct domains in run D (`AquaPhoenix` 66.7, `Applied Catalysts` 65.4, `Analytical Sales` 71.1, `Armor Industrial` 72.7 — all brand-vs-legal-name variants) and no threshold separates them: a genuine wrong-entity pair ("Acme Biotech" vs "Aum Biotech") scores 74.1. Requiring a second, region-or-country-level disagreement leaves the one true positive standing (`Apollo Organic Synthesis` NY vs `Apollo Olive Oil` Northern California) and drops all four false ones. City alone is excluded because a plant and a head office in one state (Houston / Baytown, TX) are one company (`corroborator_report.md`) |
| `PAGE_READ_TIMEOUT_SECONDS` | `8` | int (env) | `config.py` (`Settings.page_read_timeout_seconds`); `.env.example` | `search/page_fetcher.PageFetcher.fetch_page_result` via `page_corroborator.fetch_pages` | Slow hosts tolerated; per-record latency rises, and up to 5 requests per domain multiply it | Fetches abort sooner → `fetch_unavailable`, which changes nothing about the record | "Shorter than `PAGE_FETCH_TIMEOUT_SECONDS` because this step is optional evidence on a path that already has an answer: up to five requests may be issued per domain (root plus the imprint probe), and a slow host must not dominate the record's latency." (`config.py`) |
| `IMPRINT_PATHS` | `(/impressum, /legal, /about, /contact)` — first 2xx wins | tuple | `enrichment/page_corroborator.py` | `page_corroborator.fetch_pages` | More paths probed per domain → more requests, more chance of finding a legal identity | Fewer probes; a site stating its identity only on `/contact` returns `no_identity` | "`/impressum` first because a German-law imprint is the single most reliable statement of legal identity any site carries; the English-language equivalents follow in decreasing order of how formal they usually are." (`enrichment/page_corroborator.py`). Measured on run F: root alone 17, `+/about` 14, `+/legal` 5, `+/contact` 5, `+/impressum` 3 |
| `_MIN_CONTENT_CHARS` | `120` | int | `enrichment/page_corroborator.py` | `page_corroborator.read_page` | Short pages skipped without an LLM call | More near-empty pages sent to the reader, which can only return nulls | "Not a tuned threshold: it is the length below which the LLM has no sentence to read, and the purpose is to skip an LLM call that can only return nulls." (`enrichment/page_corroborator.py`) |
| `_PARKING_MARKERS` | 15 phrases (`this domain is for sale`, `hugedomains`, `sedo`, …) | tuple | `enrichment/page_corroborator.py` | `page_corroborator._looks_parked` | More parked pages detected as `parked` (no evidence) | A parking placeholder reaches the reader and is scored as a page | "These are the phrases the parking services themselves render; a real company page does not carry them." (`enrichment/page_corroborator.py`). Fired 0 times on run F |
| `_CHALLENGE_MARKERS` | 10 phrases (`checking your browser`, `verify you are human`, `captcha`, …) | tuple | `enrichment/page_corroborator.py` | `page_corroborator._looks_challenged` | More interstitials treated as `fetch_unavailable` | A bot-challenge page reaches the reader and its text is scored as the organisation's own statement | "The server answered 200, so the status code alone does not reveal that we were refused." (`enrichment/page_corroborator.py`) |
| Blocked-status set | `{401, 403, 429, 451}` | inline | `search/page_fetcher.PageFetchResult.blocked` | `page_corroborator.corroborate` | n/a | n/a | "a 403 or a bot-challenge means *we could not look*, and must never be read as evidence for or against the record, whereas a 404 on `/impressum` simply means try the next path." (`search/page_fetcher.py`) |
| Postal-code comparison prefix | first `5` alphanumerics | int | `page_corroborator._postal_matches` | `page_corroborator.compare_location` | Longer prefix → ZIP+4 no longer matches a bare 5-digit ZIP | Shorter prefix → distinct ZIPs collide | "'12345-6789' and '12345' are the same place written two ways, and a 5-digit ZIP is the part both sides always carry." (`enrichment/page_corroborator.py`) |
| `PAGE_FIXTURE_DIR` | `tests/fixtures/page_reads` | str (env) | `config.py` (`Settings.page_fixture_dir`); `.env.example` | `utils.cache.PageCache` via `Orchestrator.__init__` | n/a | Empty string → memory-only; a re-run re-fetches and its corroboration decisions may differ from the recorded run | "a page read is a claim about what a site said on a given day, so it is kept on disk and not only in memory: re-running a thesis batch must reproduce its corroboration decisions rather than re-litigate them against today's web." (`config.py`) |
| `PAGE_FIXTURE_REPLAY_ONLY` | `false` | bool (env) | `config.py` (`Settings.page_fixture_replay_only`); `.env.example` | `page_corroborator.fetch_pages` | `true` → a missing fixture returns `fetch_unavailable` and no network call is made | n/a | "what an offline re-analysis or a CI run wants" (`config.py`) |
| `PAGE_EXTRACT_FEEDS_RETRY` | `false` | bool (env) | `config.py` (`Settings.page_extract_feeds_retry`); `.env.example` | `enrichment/orchestrator._maybe_feed_retry_from_page` | `true` → a page-extracted legal name carrying a legal form and differing from Name 1 under `normalize_key` is offered to Stage 5, spending its own once-per-record budget; every retry guard still applies | n/a | "OFF by default and deliberately so — Fix 1's trace shows Stage 5's yield is bounded by GLEIF's coverage of private US SMBs, not by the supply of candidate names, so this buys API calls before it buys identifiers." (`config.py`, `retry_trace_findings.md`). **Open item — author to decide** |
| `RETRY_TRACE` | `false` | bool (env) | `config.py` (`Settings.retry_trace`); `.env.example` | `enrichment/orchestrator._emit_retry_trace` | `true` → one JSON line per finalised record on `enrichment.trace.retry` | n/a | "Purely additive — retry behaviour is unchanged." (`config.py`); diagnostic-only, mirrors `WEBSITE_TRACE` |
| Region normalisation source | `tier1_ror._US_POSTAL_CODES` (50 two-letter codes) | dict | `enrichment/tier1_ror.py`; consumed in `page_corroborator._norm_region` | `page_corroborator.compare_location` | n/a | Without it, `San Francisco, California` on a page contradicts `San Francisco, CA` on the record — measured as a false contradiction on `Anresco Laboratories` in run D | Reuses the existing map rather than a second table. The map is ROR-local because expanding those codes inside a *name* is ambiguous ("IN Laboratories"); here the value is a Region field, where a bare two-letter token can only be the state, and the result is used for a comparison and never written (`enrichment/page_corroborator.py`) |

---

## 2 · Conflicts — parameters defined in more than one place

### 2.1 · `MAX_PAGE_CONTENT_CHARS` — three sources, two values

| Source | Value | Line |
|--------|-------|------|
| `OPTIONAL_VARS_WITH_DEFAULTS` | `"3000"` | `config.py:93` |
| `Settings.max_page_content_chars` | `"1500"` | `config.py:209` |
| `PageFetcher.__init__` default arg | `1500` | `search/page_fetcher.py:69` |
| `README.md` env table | `3000` | `README.md:1622` |
| `.env.example` | `3000` | `.env.example` (Pipeline section) |

**Which wins at runtime.** `Settings.max_page_content_chars` (`config.py:208-210`) — it is the
only one the orchestrator reads (`enrichment/orchestrator.py:741,750`). With the variable
unset the effective value is **`1500`**. `OPTIONAL_VARS_WITH_DEFAULTS` is a documentation-only
dictionary: nothing reads it to supply a default (verified — the dict name appears only at its
definition, `config.py:83`). The `PageFetcher` default arg (`1500`) is never reached from the
application because the orchestrator always passes the setting explicitly. If a deployment
copies `.env.example` verbatim, the environment sets `3000` and that wins over both defaults.

**Provenance of the divergence.** Commit `b19cd1a` states "Adjusted `max_page_content_chars` in
`config.py` from 3000 to 1500 for better performance." The `Settings` field was changed; the
`OPTIONAL_VARS_WITH_DEFAULTS` entry, `README.md`, and `.env.example` were not. → record in
`08_GAPS.md`.

### 2.2 · `DEPT_PROBE_CROSS_DOMAIN` — code says `false`, `.env.example` says `true`

| Source | Value | Line |
|--------|-------|------|
| `OPTIONAL_VARS_WITH_DEFAULTS` | `"false"` | `config.py:114` |
| `Settings.dept_probe_cross_domain` | `default=False` | `config.py:166-168` |
| `README.md` env table | `false` | `README.md:1630` |
| `.env.example` | `DEPT_PROBE_CROSS_DOMAIN=true`, commented "when true (default)" | `.env.example` (Search section) |

**Which wins at runtime.** `Settings.dept_probe_cross_domain` (`config.py:166-168`) is the sole
consumer (`enrichment/orchestrator.py:1277`); unset, the effective value is **`false`**. But a
deployment that copies `.env.example` sets the variable to `true` and the cross-domain stage-3
SERP call runs — doubling SERP spend for unresolved departments relative to the documented
default. The commit that flipped the default states "`DEPT_PROBE_CROSS_DOMAIN` default →
`false`" (commit `515cc7c`) and the README records "matches the documented intent"
(`README.md:1993`); `.env.example` was not updated. `02_ARCHITECTURE.md:509-510` flags the same
discrepancy from the `Domain_DeptDomain_SearchTerm_Logic.pdf` side. → record in `08_GAPS.md`.

### 2.3 · `ROR_CONFIDENCE_THRESHOLD` — read twice, from two places

| Source | Value | Line |
|--------|-------|------|
| `Settings.ror_confidence_threshold` | `float(os.getenv(..., "0.8"))` | `config.py:177` |
| Direct env read in the client | `float(os.getenv(..., "0.8"))` | `enrichment/tier1_ror.py:573` |
| `OPTIONAL_VARS_WITH_DEFAULTS` | `"0.8"` | `config.py:86` |

**Which wins at runtime.** `enrichment/tier1_ror.py:573` — the matching decision at `:629`,
`:646` and `:815` uses the value read there, not the `Settings` field. The `Settings` field is
consumed **only** by the `/tiers` response (`api/routes.py:1111`). Both read the same variable
with the same literal default, so the two never diverge at runtime; the duplication is
structural, and a future change to one default alone would make `/tiers` report a threshold
the matcher does not use.

### 2.4 · Residue-nomination thresholds — three definition sites each

`NAME_CANDIDATE_THRESHOLD`, `TOKEN_CANDIDATE_THRESHOLD`, and `MAX_CANDIDATES_PER_BLOCK` are
each declared in `config.py` (`:107-109`, `:229-237`), in `dedup/adjudicator.py` (`:38-40`),
and in `.env.example`. **All three sites carry identical values** (`0.85`, `0.6`, `50`), so
there is no value conflict. The resolution order is explicit and settings-first:
`_resolve_candidate_config` takes the `Settings` attribute if non-`None`, else the environment
variable, else the module default (`dedup/adjudicator.py:903-926`). The `Settings` fields are
themselves environment-derived, so in practice **`Settings` wins** whenever a `settings` object
is passed to `cluster_blocks`; the `dedup/adjudicator.py` defaults only apply when it is not.

### 2.5 · Dedup adjudication `max_tokens` — default never used

`DedupLLM.adjudicate` declares `max_tokens: int = 4000` (`dedup/llm.py:161`), but both
application call sites pass `max_tokens=1000` (`dedup/adjudicator.py:452`, `:638`). The
effective value in production is **`1000`**; the `4000` default is reached only by test doubles
(`tests/test_dedup.py:54,750`).

### 2.6 · Enrichment page size `50` — two coupled literals

The batch size appears twice in the Enrichment pipeline's T-SQL: the offset generator
`rn * 50 AS offset` (`CONTEXT-EXTERNAL.md:64`) and the page fetch `FETCH NEXT 50 ROWS ONLY`
(`CONTEXT-EXTERNAL.md:106`). They are not bound to a shared parameter. Changing one without
the other silently skips or re-processes rows; neither is derived from the other.

### 2.7 · `SIG_PARTITION_THRESHOLD` / `DEDUP_MAX_CONCURRENCY` — env only, no `Settings` field

Unlike the residue knobs, these two are read directly from the environment inside
`cluster_blocks` (`dedup/adjudicator.py:948-951`) with module-level defaults
(`dedup/adjudicator.py:36-37`); they have no `Settings` field and no entry in
`OPTIONAL_VARS_WITH_DEFAULTS`. The env value wins; absent it, the module default.

---

## 3 · Environment variables

Every environment variable read anywhere in the repository — 45 rows covering 47 variables (the
final row groups the three standard proxy variables). The seven added by Fixes 1 and 3
(`RETRY_TRACE`, `PAGE_*`) are all optional with working defaults; none breaks anything when unset. "Breaks when unset" is read from the code
path that consumes it. **Secret** marks values that authenticate to an external service.

| Variable | Default | Secret | Sourced from | What breaks when unset | Defined / read at |
|----------|---------|--------|--------------|------------------------|-------------------|
| `AZURE_OPENAI_API_KEY` | *(none — required)* | **yes** | Azure Functions Application Settings in production; `.env` locally (`config.py:1-5`); AI Foundry on the Bruker spoke (`02_ARCHITECTURE.md:389`) | `validate_env` logs a warning at startup but does **not** raise (`config.py:122-135`); `get_openai_client` raises `RuntimeError` on the first LLM call (`llm/openai_client.py:154-159`). Every LLM tier and the dedup adjudicator fail | `config.py:79`, `:155`; `llm/openai_client.py:147` |
| `AZURE_OPENAI_ENDPOINT` | *(none — required)* | no (but resource-identifying) | as above | Same as `AZURE_OPENAI_API_KEY` — both are checked together (`llm/openai_client.py:154`) | `config.py:80`, `:156`; `llm/openai_client.py:148` |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.4` | no | Application Settings / `.env` | Nothing — falls back to the literal `gpt-5.4` at three sites (`llm/openai_client.py:199,233`; `dedup/llm.py:121`). Fails at call time if no such deployment exists | `config.py:84`, `:157` |
| `AZURE_OPENAI_API_VERSION` | `2024-08-01-preview` (Phase 1) / `2025-04-01-preview` (dedup) | no | Application Settings / `.env` | Nothing — per-phase defaults apply | `llm/openai_client.py:151`; `dedup/llm.py:126` |
| `AOAI_DEPLOYMENT_DEDUP` | falls back to `AZURE_OPENAI_DEPLOYMENT`, then `gpt-5.4` | no | Application Settings / `.env` | Nothing — the adjudicator reuses the Phase 1 deployment | `dedup/llm.py:118` |
| `AOAI_API_VERSION_DEDUP` | falls back to `AZURE_OPENAI_API_VERSION`, then `2025-04-01-preview` | no | Application Settings / `.env` | Nothing. ⚠ A too-old version makes every block route to `manual_review` with `errors > 0` (`README.md:1675`) | `dedup/llm.py:125` |
| `DEDUP_REASONING_EFFORT` | `low` | no | Application Settings / `.env` | Nothing. An empty value disables the parameter entirely (`dedup/llm.py:131`) | `dedup/llm.py:122` |
| `DEDUP_MAX_RETRIES` | `3` | no | Application Settings / `.env` | Nothing | `dedup/llm.py:123` |
| `SIG_PARTITION_THRESHOLD` | `12` | no | Application Settings / `.env` | Nothing | `dedup/adjudicator.py:949` |
| `DEDUP_MAX_CONCURRENCY` | `5` | no | Application Settings / `.env` | Nothing | `dedup/adjudicator.py:951` |
| `NAME_CANDIDATE_THRESHOLD` | `0.85` | no | Application Settings / `.env` | Nothing | `config.py:107`, `:230`; `dedup/adjudicator.py:918` |
| `TOKEN_CANDIDATE_THRESHOLD` | `0.6` | no | Application Settings / `.env` | Nothing | `config.py:108`, `:233`; `dedup/adjudicator.py:921` |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | no | Application Settings / `.env` | Nothing | `config.py:109`, `:236`; `dedup/adjudicator.py:924` |
| `CONFIDENCE_MERGE_THRESHOLD` | `0.95` | no | Application Settings / `.env` | Nothing. A non-numeric value logs "Invalid CONFIDENCE_MERGE_THRESHOLD %r; using %.2f" and falls back (`dedup/scoring.py:1013-1015`) | `config.py:100`, `:224`; `dedup/scoring.py:1008` |
| `SERPAPI_KEY` | *(none)* | **yes** | `.env` / Application Settings | The service falls back to `DuckDuckGoClient` — "DuckDuckGo returns lower-quality results" (`config.py:141-144`); every SERP-dependent stage (website Path B, department probe, Tier 2A/2B, lab resolver, person affiliation) degrades | `config.py:137`, `:160` |
| `DEPT_PROBE_CROSS_DOMAIN` | `false` | no | `.env` / Application Settings | Nothing — the probe stops after one SERP call (`enrichment/orchestrator.py:1277-1283`). ⚠ Conflicting `.env.example` value (§2.2) | `config.py:114`, `:167` |
| `ROR_API_BASE` | `https://api.ror.org/v2/organizations` | no | `.env` / Application Settings | Nothing | `config.py:85`, `:172`; `enrichment/tier1_ror.py:571` |
| `ROR_CONFIDENCE_THRESHOLD` | `0.8` | no | `.env` / Application Settings | Nothing | `config.py:86`, `:177`; `enrichment/tier1_ror.py:573` |
| `LEI_LOOKUP_ENABLED` | `true` | no | `.env` / Application Settings | Nothing — GLEIF lookup stays on | `config.py:87`, `:184` |
| `GLEIF_API_BASE` | `https://api.gleif.org/api/v1` | no | `.env` / Application Settings | Nothing | `config.py:88`, `:187` |
| `GLEIF_TIMEOUT_SECONDS` | `15` | no | `.env` / Application Settings | Nothing | `config.py:89`, `:190` |
| `LEI_NAME_MATCH_THRESHOLD` | `88` | no | `.env` / Application Settings | Nothing | `config.py:90`, `:196` |
| `RETRY_TRACE` | `false` | no | `.env` / Application Settings | Nothing — Stage 5 behaves identically; only the `enrichment.trace.retry` JSON lines are not emitted | `config.py` (`OPTIONAL_VARS_WITH_DEFAULTS`, `Settings.retry_trace`); `enrichment/orchestrator._emit_retry_trace` |
| `PAGE_CORROBORATION_ENABLED` | `true` | no | `.env` / Application Settings | Nothing — the page-read step stays on | `config.py` (`Settings.page_corroboration_enabled`); `enrichment/orchestrator._corroborate_domain` |
| `PAGE_NAME_MATCH_THRESHOLD` | `88` | no | `.env` / Application Settings | Nothing | `config.py` (`Settings.page_name_match_threshold`); `enrichment/page_corroborator.compare` |
| `PAGE_READ_TIMEOUT_SECONDS` | `8` | no | `.env` / Application Settings | Nothing | `config.py` (`Settings.page_read_timeout_seconds`); `search/page_fetcher.PageFetcher.fetch_page_result` |
| `PAGE_FIXTURE_DIR` | `tests/fixtures/page_reads` | no | `.env` / Application Settings | Nothing — the default path is used. An **empty** value disables the disk layer, so page reads live only in memory and a re-run may reach different corroboration verdicts | `config.py` (`Settings.page_fixture_dir`); `utils.cache.PageCache` |
| `PAGE_FIXTURE_REPLAY_ONLY` | `false` | no | `.env` / Application Settings | Nothing — live fetching stays enabled | `config.py` (`Settings.page_fixture_replay_only`); `enrichment/page_corroborator.fetch_pages` |
| `PAGE_EXTRACT_FEEDS_RETRY` | `false` | no | `.env` / Application Settings | Nothing — the optional Stage 5 feed stays off | `config.py` (`Settings.page_extract_feeds_retry`); `enrichment/orchestrator._maybe_feed_retry_from_page` |
| `LEI_MAX_RETRIES` | `2` | no | `.env` / Application Settings | Nothing | `config.py:91`, `:199` |
| `FUZZY_MATCH_THRESHOLD` | `80` | no | `.env` / Application Settings | Nothing | `config.py:92`, `:204` |
| `MAX_PAGE_CONTENT_CHARS` | `1500` effective (⚠ `3000` documented — §2.1) | no | `.env` / Application Settings | Nothing | `config.py:93`, `:209` |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `10` | no | `.env` / Application Settings | Nothing | `config.py:110`, `:212` |
| `DEFAULT_MAX_CONCURRENCY` | `5` | no | `.env` / Application Settings | Nothing — and nothing changes when it *is* set, except the `/tiers` response (§5) | `config.py:94`, `:217` |
| `MOCK_EXTERNAL_CALLS` | `false` | no | `.env` (local dev) | Nothing — real clients are used | `config.py:111`, `:241` |
| `ENV` | `production` | no | `.env` | Nothing — `load_dotenv()` is unconditional (`config.py:17-22`) | `config.py:112`, `:243` |
| `LOG_LEVEL` | `INFO` | no | `.env` / Application Settings | Nothing — an unrecognised level silently falls back to `INFO` (`api/middleware.py:85`) | `config.py:113`, `:244` |
| `LOG_FILE` | *(unset)* → `logs/enrichment_api.log` | no | `.env` / Application Settings | Nothing. An unwritable path logs a warning and keeps console-only logging (`api/middleware.py:109-113`) | `config.py:252`; `api/middleware.py:98` |
| `WEBSITE_TRACE` | `false` | no | `.env`; forced to `"true"` by `scripts/trace_website.py:32` | Nothing — the diagnostic trace is simply not emitted | `config.py:118`, `:248` |
| `AZURE_OPENAI_CA_BUNDLE` | *(unset)* | no (a certificate path) | `.env` / Application Settings; the repo ships a corporate CA bundle under `certs/` (`00_INVENTORY.md:26-27`) | Nothing on a normal network. On a TLS-inspecting corporate VPN, ROR / GLEIF / SerpAPI / page fetch / LLM calls fail the handshake with `CERTIFICATE_VERIFY_FAILED` (`enrichment/tier1_ror.py:600-606`; `enrichment/tier1_lei.py:244-250`) | `config.py:53`; `llm/openai_client.py:83` |
| `REQUESTS_CA_BUNDLE` | *(unset)* | no | OS / corporate image | Nothing. If set to a **non-existent** path it is overwritten at import with the corp bundle or certifi (`config.py:57-64`) | `config.py:57`; `llm/openai_client.py:83` |
| `SSL_CERT_FILE` | *(unset)* | no | OS / corporate image | Same as `REQUESTS_CA_BUNDLE` | `config.py:57`; `llm/openai_client.py:83` |
| `LLM_SSL_VERIFY` | `true` | no | `.env` | Nothing. `false` disables TLS verification for LLM calls and logs a loud warning (`llm/openai_client.py:111-115`) | `llm/openai_client.py:110` |
| `LLM_HTTP_CONNECT_TIMEOUT` | `30` | no | `.env` | Nothing | `llm/openai_client.py:162` |
| `LLM_HTTP_TIMEOUT` | `60` | no | `.env` | Nothing | `llm/openai_client.py:163` |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | *(unset)* | no | Corporate VPN client | Nothing off-VPN. Honoured automatically because `trust_env=True` (`llm/openai_client.py:167`); documented in `.env.example` | consumed by `httpx` via `trust_env` (`llm/openai_client.py:167`) |

**Secrets summary.** Two variables are secrets: `AZURE_OPENAI_API_KEY` and `SERPAPI_KEY`. Both
are sourced from Azure Functions Application Settings in production and from a git-ignored
`.env` locally (`config.py:1-5`); `.env.example` carries placeholders only, and commit `1ce16bd`
("Remove real API keys from .env.example") records that real keys were once committed there.
`GET /diag/llm` deliberately reports only the key's presence and length, never its value
(`api/routes.py:1046-1047`).

---

## 4 · Golden-record scoring weights

The election model is a headline contribution and is documented here in full so it is
reproducible from the thesis alone.

### 4.1 · Provenance and agreement status

The weights table is `dedup/weights.json` (58 lines, 11 criteria). Its own header comment
records the agreement status verbatim:

> "Golden-record scoring weights. Editable reference table — the scorer never hardcodes points.
> Band labels: 'a-b' inclusive range, '>n' strictly greater, 'n+' greater-or-equal, bare number
> exact, 'X/Y' either literal (case-insensitive). Values with no matching band score 0.
> UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count
> tiers, account_group DRIT (transcript said DRID; live SAP shows DRIT)."
> — `dedup/weights.json:2`

The industry supervisor is named in code as **Bernd** (Bernd Schnurrer, also named at
`CONTEXT-EXTERNAL.md:434`). The transcripts record that the scoring specification originated
with him: "in his original spec, Burn gave me some rules. And he said, for example, on the
status, if it's an active customer, you need to score it, give it 10 points, or else 0 points.
If it's a sleeping customer as well, and then depending on how many sales orders there are, you
need to give it this many." (`Datashaper-Tutorial-Part2.txt:1856`), and "then I had some scores
for all the factors I got the spec from … Bert" (`Datashaper-Tutorial-Part3.txt:530`).
("Burn"/"Bert" are transcription variants of the same name.) The transcript also confirms the
aggregation rule — "we count all these together to get the final score"
(`Datashaper-Tutorial-Part2.txt:1889`) — and the election rule — "assign the golden record to
the one with the highest score in the group" (`Datashaper-Tutorial-Part2.txt:1898`).

⚠ The transcripts do **not** enumerate the per-band point values other than
`customer_status: active = 10`. For every other criterion the transcript establishes only that a
specification exists and that the criterion is in it, not the numbers. The numbers below are
therefore evidenced by `dedup/weights.json` alone.

### 4.2 · The weights table, verbatim

Every value below is copied from `dedup/weights.json`. "Agreed with supervisor" is `yes` only
where a repository artefact states the agreement; `⚠ marked UNCONFIRMED in code` where the file
or the scorer explicitly flags it; `⚠ not stated` otherwise.

| Criterion | Band | Points | Source line | Agreed with industry supervisor? |
|-----------|------|-------:|-------------|----------------------------------|
| `sales_order_last_used` | `2026` | `20` | `dedup/weights.json:4` | ⚠ not stated — the criterion is in the spec (`Datashaper-Tutorial-Part2.txt:1886`); these year tiers are not |
| `sales_order_last_used` | `2025` | `15` | `dedup/weights.json:5` | ⚠ not stated |
| `sales_order_last_used` | `2024` | `10` | `dedup/weights.json:6` | ⚠ not stated |
| `sales_order_last_used` | `2023` | `5` | `dedup/weights.json:7` | ⚠ not stated |
| `sales_order_count` | `0-5` | `5` | `dedup/weights.json:10` | ⚠ not stated |
| `sales_order_count` | `6-10` | `15` | `dedup/weights.json:11` | ⚠ not stated |
| `sales_order_count` | `>10` | `25` | `dedup/weights.json:12` | ⚠ not stated |
| `sales_order_partner_last_used` | `2026` | `20` | `dedup/weights.json:15` | ⚠ not stated |
| `sales_order_partner_last_used` | `2025` | `15` | `dedup/weights.json:16` | ⚠ not stated |
| `sales_order_partner_last_used` | `2024` | `10` | `dedup/weights.json:17` | ⚠ not stated |
| `sales_order_partner_last_used` | `2023` | `5` | `dedup/weights.json:18` | ⚠ not stated |
| `sales_order_partner_count` | `0-5` | `5` | `dedup/weights.json:21` | ⚠ **marked UNCONFIRMED in code** — `dedup/weights.json:2`; "UNCONFIRMED: partner count tiers mirror sales order count. CONFIRM w/ Bernd." (`dedup/scoring.py:873`) |
| `sales_order_partner_count` | `6-10` | `15` | `dedup/weights.json:22` | ⚠ **marked UNCONFIRMED in code** (as above) |
| `sales_order_partner_count` | `>10` | `25` | `dedup/weights.json:23` | ⚠ **marked UNCONFIRMED in code** (as above) |
| `equipment_count` | `0-3` | `5` | `dedup/weights.json:26` | ⚠ not stated — the criterion is in the spec ("how many equipments are linked to the customer", `Datashaper-Tutorial-Part2.txt:1886`); the tiers are not |
| `equipment_count` | `4-8` | `12` | `dedup/weights.json:27` | ⚠ not stated |
| `equipment_count` | `9-15` | `20` | `dedup/weights.json:28` | ⚠ not stated |
| `equipment_count` | `>15` | `30` | `dedup/weights.json:29` | ⚠ not stated |
| `sleeping_customer` | `No` | `15` | `dedup/weights.json:32` | ⚠ not stated — the criterion is in the spec ("If it's a sleeping customer as well", `Datashaper-Tutorial-Part2.txt:1856`); the points are not |
| `sleeping_customer` | `3-4` | `5` | `dedup/weights.json:33` | ⚠ not stated |
| `sleeping_customer` | `>5` | `0` | `dedup/weights.json:34` | ⚠ not stated |
| `customer_status` | `active` | `10` | `dedup/weights.json:37` | **yes** — "on the status, if it's an active customer, you need to score it, give it 10 points, or else 0 points" (`Datashaper-Tutorial-Part2.txt:1856`) |
| `customer_status` | `blocked` | `0` | `dedup/weights.json:38` | **yes** — "or else 0 points" (as above) |
| `account_group` | `DRIT` | `20` | `dedup/weights.json:41` | ⚠ **marked UNCONFIRMED in code** — "account_group DRIT (transcript said DRID; live SAP shows DRIT)" (`dedup/weights.json:2`) |
| `account_group` | `0002/SHIP2` | `15` | `dedup/weights.json:42` | ⚠ not stated |
| `account_group` | `0003` | `10` | `dedup/weights.json:43` | ⚠ not stated |
| `account_group` | `0004` | `10` | `dedup/weights.json:44` | ⚠ not stated |
| `account_group` | `0005/MLIEF` | `5` | `dedup/weights.json:45` | ⚠ not stated |
| `company_code_count` | `1` | `5` | `dedup/weights.json:48` | ⚠ not stated |
| `company_code_count` | `2-4` | `15` | `dedup/weights.json:49` | ⚠ not stated |
| `company_code_count` | `5+` | `25` | `dedup/weights.json:50` | ⚠ not stated |
| `combined_presence_bonus` | `company code AND sales org` | `10` | `dedup/weights.json:53` | ⚠ **marked UNCONFIRMED in code** — "UNCONFIRMED (verify with Bernd): combined_presence_bonus value" (`dedup/weights.json:2`); "UNCONFIRMED bonus value; sales org has no standalone tier." (`dedup/scoring.py:912`) |
| `salesforce_instance_count` | `per instance` | `10` | `dedup/weights.json:56` | ⚠ not stated |

**Theoretical maximum.** Summing the highest band of each criterion — 20 + 25 + 20 + 25 + 30 +
15 + 10 + 20 + 25 + 10 = **200 points**, plus `salesforce_instance_count` at 10 points **per
instance** across up to 8 slots (`dedup/scoring.py:75`, `:918-920`), i.e. up to 80 further
points. ⚠ This arithmetic is derived from the table above, not read from any file; no code or
document states a maximum.

### 4.3 · How the weights are applied

| Mechanism | Behaviour | Source |
|-----------|-----------|--------|
| Table loading | `load_weights()` reads `dedup/weights.json` and drops keys beginning `_` as metadata | `dedup/scoring.py:43`, `:618-623` |
| Numeric band matching | `"a-b"` inclusive range; `">n"` strictly greater; `"n+"` greater-or-equal; a bare number is an exact match. No match (including `None`) scores 0 | `dedup/scoring.py:725-751` |
| Label band matching | Case-insensitive (`casefold`); `"X/Y"` matches either literal. `None` scores 0 silently; a present-but-unrecognised value scores 0 with a warning when `warn_unknown` | `dedup/scoring.py:754-780` |
| Single-band criteria | `combined_presence_bonus` and `salesforce_instance_count` take the sole band's value | `dedup/scoring.py:783-785` |
| `combined_presence_bonus` condition | Awarded only when `company_codes > 0 AND sales_orgs > 0` | `dedup/scoring.py:913-917` |
| `salesforce_instance_count` | `sf_instances * 10` — 10 points **per** non-empty Salesforce id slot, across `sf1…sf8` | `dedup/scoring.py:75`, `:713-717`, `:918-920` |
| Derived counts | `company_code_count`, `sales_org_count`, `salesforce_instance_count` are always derived from the consolidated `";"`-delimited fields / id slots, never read from the file | `dedup/scoring.py:698-718` |
| Total | `sum(breakdown.values())` — no normalisation, no weighting of criteria against each other | `dedup/scoring.py:975` |
| G1 recency-dominance gate | A sales-order **count** component scores 0 unless the row's last-used year equals the cluster maximum; a row with no year never receives count points; a `None` cluster maximum (singleton) awards | `dedup/scoring.py:792-810`, `:852-889` |
| Election | One winner per cluster by `_tiebreak_key`; every election is a **proposal**, never auto-committed | `dedup/scoring.py:939-955`, `:1033-1052`, `:1046-1047` |
| Drift detection | `weights_version` (12-hex sha256 of the canonical JSON) is written onto every scored row | `dedup/scoring.py:610-615` |

**G1 — the one weighting rule with a recorded rationale.** The recency gate is the only part of
the model whose *reason* is documented rather than only its value:

> "Bernd's rule: the count is always 'in relation to the year' — it only differentiates records
> sharing the most-recent year, and 'does not define what is the golden record, it just adds
> something'." — `dedup/scoring.py:795-797`

> "G1: count only 'adds something' when this row owns the cluster's most recent year —
> otherwise an older, higher-volume record could out-score a more recent one, which Bernd said
> must never happen." — `dedup/scoring.py:852-854`

Commit `c18921d` ("Refactor scoring logic to align with Bernd's year-priority rule") records
when this was implemented; commit `994fb3b` ("Enhance scoring logic to prevent false recency
suppression warnings") records the follow-up that limits the suppression warning to genuine
recency losses (`dedup/scoring.py:860-869`).

### 4.4 · ⚠ Divergence from the DATAshaper-side implementation

The DATAshaper prototype of the same scoring model uses a **month-difference** banding for
sales-order recency, not the calendar-year tiers in `dedup/weights.json`:

> "when there's never been a sales order and it's zero, when it's between 0 and 9 months, then
> it's 25. Between 20 and 24, it's 15. Else it's 5."
> — `Datashaper-Tutorial-Part3.txt:527` (restated at `Datashaper-Tutorial-Part2.txt:1880` as
> "when it's between 0 and 9, then it's 25. When it's between 10 and 24, then it's 15. Else it's
> 5.")

Three differences follow: (a) the DS bands are relative to the current date, the repository's
are absolute calendar years; (b) the DS top band is **25** points, `dedup/weights.json:4` gives
**20**; (c) the two transcript passages disagree with each other on the middle band's lower
bound (`10` vs `20` months). ⚠ Which of the two models is authoritative for the thesis, and
whether the repository's year tiers were re-agreed, is not evidenced anywhere in the repository
— author to supply. → record in `08_GAPS.md`.

### 4.5 · Fields the model deliberately excludes

`ZFIS` is absent from the criteria by design: "ZFIS is deliberately absent: it is a separate
upstream gate that runs before enrichment; those records never reach dedup."
(`dedup/scoring.py:15-16`). This matches the workflow's step 1 ZFI exclusion
(`CONTEXT-EXTERNAL.md:418,434`), whose own rationale is ⚠ not recorded.

`blocked` customers score 0 on `customer_status` but remain **eligible to win**: "'blocked'
scores 0 but stays ELIGIBLE to win — a differentiator, not an eligibility exclusion. Absent
status is never defaulted to 'active'." (`dedup/scoring.py:897-898`).

---

## 5 · Parameters defined but not consumed

Recorded because a thesis reproduction would otherwise assume they take effect.

| Parameter | Declared at | Why it has no effect |
|-----------|-------------|----------------------|
| `OpenAIClient.extract_json(temperature=0.0)` | `llm/openai_client.py:262` | The argument is accepted but never forwarded — `call_openai` is invoked without it (`llm/openai_client.py:272-275`) and hardcodes `temperature=0.0` in the request body (`llm/openai_client.py:205`). Setting a non-zero temperature through this parameter changes nothing |
| `OPTIONAL_VARS_WITH_DEFAULTS` | `config.py:83-119` | A documentation dictionary. `validate_env` reads only `REQUIRED_VARS` and `SERPAPI_KEY` (`config.py:128,137`); every default actually applied comes from the `Settings` field's `os.getenv(..., "…")` call. The dictionary's `MAX_PAGE_CONTENT_CHARS` entry therefore documents a value the code does not use (§2.1) |
| `Settings.default_max_concurrency` | `config.py:216-218` | Consumed only by the `/tiers` response (`api/routes.py:1115`). The actual semaphore uses `options.max_concurrency` from the request (`enrichment/orchestrator.py:797`), whose default is the independent literal `5` at `api/models.py:289` and `api/routes.py:521`. Changing `DEFAULT_MAX_CONCURRENCY` does not change how many records run concurrently |
| `Settings.ror_confidence_threshold` | `config.py:176-178` | Consumed only by `/tiers` (`api/routes.py:1111`); the matching decision reads the environment variable directly (`enrichment/tier1_ror.py:573`) — see §2.3 |
| `DedupLLM.adjudicate` default `max_tokens=4000` | `dedup/llm.py:161` | Both application call sites override it with `1000` — see §2.5 |
| `PageFetcher` default args `timeout=10, max_chars=1500` | `search/page_fetcher.py:69` | The orchestrator always constructs `PageFetcher` with both values passed explicitly from `Settings` (`enrichment/orchestrator.py:739-742,748-751`); the defaults are reached only by tests (`tests/mocks/page_mock.py:80`) |
| ADF `retryIntervalInSeconds: 30` | `CONTEXT-EXTERNAL.md:56` and six further sites | Inert while `retry: 0` on every activity — there is no retry to space out |

---

## 6 · Parameters required before the code freeze

Enumerated so the thesis records what is not yet fixed. All are open items already tracked in
`CONTEXT-EXTERNAL.md:439-448`.

1. **Address-validation confidence threshold** (`80%`) — the value is [AUTHOR]-stated only
   (`CONTEXT-EXTERNAL.md:423`); the implementing ADF pipeline is not exported
   (`CONTEXT-EXTERNAL.md:442`) and no repository code applies it. ⚠ The comparison operator and
   the validating service are unevidenced.
2. **Azure Functions hosting plan and its HTTP timeout ceiling** — `host.json` sets no
   `functionTimeout` (`host.json:1-20`), so the platform default for an unconfirmed plan bounds
   every `/enrich` call (`CONTEXT-EXTERNAL.md:446`).
3. **ADF retry policy above 0** on the Enrichment `Web1` and `Merge Back` activities
   (`CONTEXT-EXTERNAL.md:194-197`).
4. **Group-code predicate** on all three Lookup activities (`CONTEXT-EXTERNAL.md:194-197`,
   `:312-314`) — a parameterisation, currently absent from both exports.
5. **`enriched_at` watermark** so the Enrichment `Lookup1` selects only unenriched rows
   (`CONTEXT-EXTERNAL.md:194-197`).
6. **Deduplication `block_id` batching parameter** replacing the whole-table Lookup
   (`CONTEXT-EXTERNAL.md:312-314`).
7. **`sales_order_partner_count` tiers, `combined_presence_bonus` value, and `account_group`
   `DRIT`** — flagged UNCONFIRMED in `dedup/weights.json:2` and `dedup/scoring.py:873,912`.
8. **Tie-break ordering** — flagged "UNCONFIRMED ordering (confirm with Bernd)"
   (`dedup/scoring.py:942`).
9. **Reconciliation of the year-tier vs month-difference recency banding** (§4.4).

---

Pass 4 complete. 147 parameter rows across 15 subsystem groups (§1); 7 conflicts recorded (§2);
40 environment variables enumerated, of which 2 are secrets and 2 are required (§3); 33
scoring-weight bands documented, of which 2 are evidenced as agreed with the industry
supervisor, 5 are explicitly flagged UNCONFIRMED in code, and 26 carry no recorded agreement
(§4); 7 parameters defined but not consumed (§5); 9 parameters required before the freeze (§6).
Stop.
