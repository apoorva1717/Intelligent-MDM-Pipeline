# 20 — The SERP cache key omits the provider, so one provider's silence replays as another's answer

Type: task
Status: open
Blocked by: —

## The finding (verified 2026-08-29)

`utils/cache.py:171`:

```python
def serp_disk_key(query, country=None) -> str:
    return f"serp:{country_part or '-'}:{'q' if quoted else 'u'}:{normalised}"
```

**No provider component.** The key is a pure function of the *request*, which is the documented
design — but the provider is part of the request's meaning, and it is missing. A SerpAPI query and
a DuckDuckGo query for the same string collide on one key.

**Observed consequence.** `tests/fixtures/serp/` held **251 entries, 251 of them with an empty
payload** — recorded while `SERPAPI_KEY` was shadowed by a duplicate placeholder in `.env` and every
search silently fell back to DuckDuckGo. Any run pointing `EVIDENCE_CACHE_DIR` at `tests/fixtures`
replayed DuckDuckGo's silence as **"this organisation has no web presence"** — no warning, no
network call, indistinguishable from a real negative.

Fixing `.env` did not fix this. The entries have been quarantined to
`logs/quarantine/serp-duckduckgo-poisoned-20260829/` (moved, not deleted); `tests/fixtures/serp/`
is now empty.

## Why this is the same bug class the codebase already solved once

`search.base.SearchUnavailable` exists precisely so that "the provider failed" is never cached as
"no results" — a dropped TLS handshake must not become evidence of absence. This is that failure
one level up: *a different provider answered* is being cached as *this provider found nothing*.

## Questions

1. Add the provider to `serp_disk_key`, or refuse to cache at all when running on the fallback
   provider? Adding it to the key is the smaller change and preserves the pure-function-of-request
   property. Note `legacy_serp_key` / the in-memory `SerpCache` keys (`cache.py:150-175`, `:775`)
   have the same shape and must move together.
2. **Is an empty SERP result safe to cache at all?** An empty result from a working provider is
   real evidence; from a degraded fallback it is noise. Consider whether `cached_serp` should
   record empties only when the primary provider answered.
3. Migration: entries written under the old key shape are not distinguishable from correct ones by
   inspection. Does the namespace need a version prefix so a key-shape change invalidates cleanly
   rather than silently reusing stale entries?

## Blast radius

Any measurement taken today that consulted the SERP lane is suspect. Ticket 11's funnel is **not**
affected — it is ROR/GLEIF only, and those need no key. Ticket 14's first pass **was** affected and
has been re-measured (0/21 -> 8/21 grounded `name2` writes on live search).
