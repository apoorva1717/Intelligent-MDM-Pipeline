# 21 — A per-record trace you can read, and a run manifest that says what was actually live

Type: task
Status: open
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
