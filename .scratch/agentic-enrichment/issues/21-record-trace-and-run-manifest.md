# 21 — A per-record trace you can read, and a run manifest that says what was actually live

Type: task
Status: DONE 2026-08-29 - A, B and C implemented
Blocked by: —

## Why (all of this was measured today, 2026-08-29)

Three unrelated failures this session produced **downstream evidence that was byte-identical to
"this organisation does not exist"**, and none of them was visible in any log:

1. `SERPAPI_KEY` was duplicated in `.env`; the placeholder shadowed the real key, so every search
   silently fell back to DuckDuckGo. The logs said nothing.
2. `serp_disk_key` omits the provider (ticket 20), so 251 empty DuckDuckGo results replayed as
   "no web presence" — no warning, no network call.
3. `expand_abbreviations` is applied to the ROR **scorer** but never to the ROR **query**
   (ticket 19), so `Mass Inst of Tech` goes out verbatim and the expanded form — already computed
   and sitting in a local variable — is never sent. Nothing logs the string that went out.

Two more of the same shape: ROR returns HTTP 500 on names containing `/` (3 in 300, ticket 18),
indistinguishable downstream from a miss; and `1910 Genetics` reaches ROR as `Genetics` because
preprocessing strips the leading numeral.

The cost was not theoretical. A measured conclusion — "~24-25% is a coverage ceiling" — was drawn
from a run whose retrieval was never observed, and reported as a finding.

## What already exists (extend it; do not build a parallel system)

- `enrichment.trace.retry` / `.website` / `.wikidata` logger names, captured by
  `scripts/run_batch.py --retry-trace --website-trace --wikidata-trace --trace-out`
- `EvidenceCache.network_calls` / `.hits`, exported as `evidence_network_calls` /
  `evidence_cache_hits`
- `ProvenanceLog.reject` — but capped at `MAX_REJECTIONS_PER_FIELD = 3`, so it cannot be used for
  counting
- `enrichment/funnel_probe.py` (ticket 11) — `FUNNEL_PROBE`-gated, counting-only, off by default.
  **This is the pattern to follow.**

## The gap

Nothing records the **external request layer**: the query string actually sent, to which provider,
whether it was served from cache or the network, and what came back. That is precisely the layer
where all five failures above live.

## Deliverable A — the record trace

For one record, on demand, a readable trace of every external call:

```
[ror] affiliation  q="Mass Inst of Tech, Cambridge, MA, US"   cache=MISS  200  items=5 chosen=None
[ror] query        q="Mass Inst of Tech"                      cache=MISS  200  items=8 best=0.41 REJECT<0.8
[serp] serpapi     q="\"Mass Inst of Tech\" Cambridge"        cache=HIT   results=0
```

Requirements:
- The **verbatim outbound string**, not the record's input name. The difference between those two
  is where three of today's bugs live.
- **Which provider** answered (SerpAPI vs DuckDuckGo vs unavailable) — never just "serp".
- **cache HIT vs network MISS**, per call.
- The **outcome and its reason**, using a vocabulary that distinguishes: provider failed /
  provider returned zero / returned candidates but none retrieved the right entity / candidate
  retrieved but rejected by gate *X* at score *S*. Today those collapse to one indistinguishable
  "no match". This vocabulary is the point of the ticket.
- Human-readable by default; JSON-lines alongside for aggregation.

## Deliverable B — the run manifest

One header block per run recording what was **actually** live, not what was configured:
resolved SERP provider, whether the LLM was reachable, `MOCK_EXTERNAL_CALLS`, `CACHE_FROZEN`,
`EVIDENCE_CACHE_DIR` and its per-namespace entry counts, deployment and API version, and the
git SHA. Every one of today's three failures would have been caught on sight by this block.

## Deliverable C — a small-batch entry point

The stated need: *run a handful of examples and see what happened.* An input of 5-20 rows should
produce the per-record trace plus a one-line-per-record summary, without the full batch machinery.
`scripts/run_batch.py` already parses xlsx via `_parse_xlsx` / `_rows_to_records` — reuse it.

## Constraints

- **Off by default, and never load-bearing.** Follow `funnel_probe.py`: env-gated, read once at
  import, and deleting every call site must leave behaviour bit-identical. No decision may read a
  trace value.
- **Never writes to a record**, a flag, a provenance entry, or a scoped field.
- **Must not defeat determinism.** No clock, run id or record id may reach a cache key or a prompt
  (`tests/test_determinism.py` asserts this structurally). The trace is an output artefact only.
- Secrets never appear in a trace line — keys, tokens, full auth headers.
- Trace volume is bounded; a 100-record batch must not emit an unreadable file.

## Not in scope

Changing any retrieval or gate behaviour. This ticket only makes the existing behaviour legible.
Ticket 19 decides what to change; this decides how anyone can see it.

## Resolution (implemented 2026-08-29)

All three deliverables land. `enrichment/call_trace.py` follows `funnel_probe.py` exactly: `ENABLED`
read once at import from `CALL_TRACE`, off by default, and deleting every call site leaves behaviour
bit-identical.

### A - the record trace

Two call sites, because every lane already routes through them: `utils.cache.cached_serp` and
`utils.cache.cached_registry_get`. Nothing else needed instrumenting, which is itself a result - the
"only way to issue a search or a fetch" invariant paid for itself here.

Real output, `python scripts/explain.py docs/thesis/chemspeed_us_100.xlsx --limit 3`:

```
[ror]   https://api.ror.org/v2/organizations affiliation="Genetics, Boston, MA, US"    cache=HIT -> ok candidates=10 (row-2)
[ror]   https://api.ror.org/v2/organizations query="Genetics"                          cache=HIT -> ok candidates=20 (row-2)
[gleif] https://api.gleif.org/api/v1/fuzzycompletions q="Genetics"                     cache=HIT -> ok candidates=10 (row-2)
[ror]   https://api.ror.org/v2/organizations affiliation="1910 Genetics, Boston, MA, US" cache=HIT -> ok candidates=10 (row-2)
[serp]  serpapi q=""1910 Genetics" official website Boston MA"                         cache=HIT -> ok results=9  (row-2)
```

**Ticket 18's second finding is legible in two lines** - the numeral-stripped `Genetics` goes to ROR
*first*, and the record ships as `Genetics` with domain `1910.ai`. That is sharper than ticket 18
had it: the unstripped form is also tried, later, so the loss is an ordering/selection problem, not
purely a preprocessing one.

The outcome vocabulary is the substance of the ticket, and it is now the only place these four are
distinguishable: `provider_failed` / `empty` / `no_candidate_matched` / `rejected_by_gate`, plus
`ok` and `frozen`. `cached_serp` traces `SearchUnavailable` as `provider_failed` **before** it
returns `[]`, which is the exact distinction that class exists to preserve, now visible rather than
merely honoured. `cached_registry_get` traces a raised exception as `provider_failed` and re-raises
unchanged - ROR's HTTP 500 on names containing `/` (ticket 18) stops being indistinguishable from a
miss.

Human-readable to `CALL_TRACE_OUT`, the same event as JSON to `CALL_TRACE_JSON`.

### B - the run manifest

`describe_run()` reads the **resolved objects**, never the configuration meant to produce them:
`serp_provider` comes from `provider_id_of(orchestrator._search_client)`, so a configured SerpAPI key
that never reached a SerpAPI client reports `duckduckgo`, which is what actually happened. Asserted
by `test_it_reports_the_resolved_provider_not_the_configuration`.

```
== run manifest ==
  serp_provider        serpapi
  mock_external_calls  True
  cache_frozen         False
  evidence_cache_dir   tests/fixtures
  llm_deployment       MDM-Apoorva-gpt-5.4
  llm_api_version      2024-08-01-preview
  llm_endpoint         https://...cognitiveservices.azure.com/
  llm_key_present      True
  cache[serp]          180
  cache[registry]      697
  cache[fetch]         240
  cache[llm]           241
  cache[page_reads]    175
  cache[wikidata]      733
  git_sha              86b8474
```

**It found a defect on its first run.** `llm_deployment` reported `<unset>` on a correctly configured
machine: the Settings field is `openai_model`, not `azure_openai_deployment`. Exactly the class of
"configured is not the same as live" the block exists to expose - and a reminder that the manifest
has to be read, not just printed. The key itself is never rendered; only `llm_key_present`.

Not gated on `CALL_TRACE`: a run that cannot say what it was talking to is the problem, and fifteen
lines cost nothing.

### C - the small-batch entry point

`scripts/explain.py`. Reuses `_parse_xlsx` / `_rows_to_records` from `api/routes.py`, so there is no
second idea of what a row is. Prints the manifest, then one line per record, then the trace paths.
Concurrency defaults to **1** so the trace reads in record order.

One wrinkle worth recording: `record_id` is a read-only property derived from the `Customer` column
(`api/models.py:243`), and the thesis workbooks leave it blank - so every trace line was attributed
to `""` and the per-record grouping was lost. `explain.py` seeds the column where it is empty. That
is safe **there and nowhere else**: it writes no workbook and no JSON artefact, so a synthetic id
cannot escape into data. `run_batch.py` must never do this, and the comment in the source says so.

### Constraints, each asserted

- **Off by default** - `TestOffByDefault`.
- **Never load-bearing** - `test_no_trace_value_reaches_a_cache_key` reads the source of all six key
  builders by name (`inspect.getsource`) and requires that none can see the trace. Named explicitly
  rather than by slicing the file, because `cached_serp` and `cached_registry_get` legitimately trace
  and sit between them.
- **Secrets never appear** - `_redact()` replaces any credential-shaped parameter name; six
  parametrised cases assert the value never reaches either file.
- **Cannot break a request it describes** - `_result_count` reports 0 on a shape it does not
  recognise rather than raising.
- **Renders on a Windows console** - the manifest is ASCII; cp1252 cannot encode box-drawing
  characters, which is not a hypothetical (the first run crashed on it).

### Verification

`5 failed, 2879 passed, 5 skipped` - the documented baseline's same five pre-existing failures, plus
21 new tests.

### Not done

`NO_CANDIDATE_MATCHED` and `REJECTED_BY_GATE` are defined and available but not yet emitted: both
belong at the *selection* layer (`registry_match`, the Tier 1 gates), not at the request layer this
ticket instruments. `funnel_probe` already counts those rejections; wiring it into this vocabulary is
a follow-up, and the vocabulary was defined now so that follow-up has one place to write to.
