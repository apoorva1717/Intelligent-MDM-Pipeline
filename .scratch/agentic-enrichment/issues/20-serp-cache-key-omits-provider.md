# 20 — The SERP cache key omits the provider, so one provider's silence replays as another's answer

Type: task
Status: FIXED 2026-08-29 — implemented, suite at baseline
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

## Resolution (implemented 2026-08-29)

**Q1 — add the provider to the key, or refuse to cache on the fallback? → Add it to the key.**
Smaller change, and it preserves the pure-function-of-request property rather than trading it for a
conditional. The provider is not incidental to the request: *"what does SerpAPI say about X"* and
*"what does DuckDuckGo say about X"* are two questions, and the key now says which was asked.

**Q2 — is an empty SERP result safe to cache at all? → Yes, and it must be.**
Refusing to record empties would trade a correctness bug for a reproducibility one: a query that
legitimately returns zero results would re-issue on every run, so `evidence_network_calls` could
never reach 0 on a warm second run — and that is the stated **precondition of `tools/run_diff.py`**,
the reproducibility gate. The empty would also still be uncacheable *after* the provider is in the
key, i.e. after the thing that actually caused the incident is gone. With the provider in the key a
fallback provider's silence is filed under that provider and can never be served to another; that is
the whole of the defect. Covered by
`test_a_fallback_providers_empty_is_not_replayed_to_the_primary` and
`test_the_same_provider_is_still_served_from_cache` — the second exists to keep this decision honest.

**Q3 — does the namespace need a version prefix? → Yes.**
`SERP_KEY_VERSION = "serp2"`. Old-shape (`serp:`) entries are, as the ticket says, not
distinguishable from correct ones by inspection — so they are retired by being made *unreachable*
rather than by a migration anyone would have to trust. Asserted by `test_the_key_shape_is_versioned`.

### The change

- `search/base.py` — `SearchClient.provider_id` (ClassVar) + `provider_id_of(client)`. An
  undeclared client is derived from its class name, never defaulted to a shared constant: two
  undeclared providers colliding with *each other* is the same bug wearing a different hat.
  Asserted by `test_an_undeclared_client_does_not_collide_with_another`.
- `search/serpapi_client.py` → `"serpapi"`; `search/duckduckgo_client.py` → `"duckduckgo"`.
- `utils/cache.py` — `provider` is a **required keyword-only** argument on `serp_key`,
  `legacy_serp_key`, `serp_disk_key`, `SerpCache.get/set` and `BatchCache.get_serp/set_serp`.
  Required, not defaulted: a caller that forgets must get a `TypeError`, not the collision back.
  New `SerpCacheKey` alias (4-tuple).
- `cached_serp` derives the provider **once**, from the client about to answer. Callers were not
  given the argument on purpose — all seven lanes already route through this function, and a value
  seven call sites each have to remember is a value that will be forgotten.
- `legacy_serp_key` carries the provider too, so `normalised_hits` stays per-provider rather than
  conflating two providers into one counter.

### Verification

`5 failed, 2821 passed, 5 skipped` — the documented baseline's same five pre-existing failures,
plus the 6 new tests. No behaviour change outside the key.

### Consequence to expect

Every existing SERP entry is now unreachable (v1 shape). **The next run is cold on the SERP lane** —
that is the cost of the fix, and it is the intended one. Warm it before running `tools/run_diff.py`,
or the gate's `evidence_network_calls == 0` precondition will fail for a reason that is not a
regression.
