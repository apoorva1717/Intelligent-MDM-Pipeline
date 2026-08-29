# 25 — `build_query` cannot emit a `site:` term — `domain` is not even a parameter

Type: task
Status: open
Blocked by: 24 (order, not capability — see "Why after 24")
Source: ticket 14, gap 1 — predicted from code, confirmed firing on the live run

## The finding (verified against the working tree, 2026-08-29)

`enrichment/grounded_resolver.py:277`:

```python
def build_query(
    name1: str | None,
    name2: str | None,
    city: str | None,
    state: str | None,
) -> str:
```

and the single call site, `:505`:

```python
result.query = build_query(name1, name2, city, state)
```

`domain` reaches this module **only inside `_re_verify`** — after the proposal exists. It is
therefore structurally impossible for the lane's one SERP query to be scoped to the organisation's
own site, no matter what the record knows.

**Confirmed on the live run: 0 of 21 grounded queries carried a `site:` term.** Example, verbatim:

```
"Naval Air Warfare Center" Weapons Div Ridgecrest CA
```

**19 of the 21 addressable records have a resolved domain** sitting in the record while that query
goes out unscoped.

This was Tier 2B's one distinguishing capability. Ticket 14 deletes Tier 2B on the grounds that
`grounded_resolver` owns the job — which is true, and this is the part of the job it cannot
currently do.

## The change

A signature change plus one `parts.append`. That is the whole of the mechanism; the design work is
entirely in **when** to emit it.

## Questions — all of which need measurement, not argument

1. **Scoped query, or a second query?** A `site:` query is narrower and can return nothing where the
   unscoped one returned something usable. Options: replace, or issue the scoped query first and
   fall back. A second query doubles the lane's SERP cost — quantify it before choosing.
2. **When is the domain trustworthy enough to scope by?** `domain` may be `web:*:low` and flagged
   `domain-unverified`. Scoping a query to an unverified domain and then treating what it says as
   evidence is a **circularity**: the page confirms a name on a domain we only believed because of
   a name. Ticket 17 declined to rank the `.gov` domain signal for this exact reason. Decide the
   provenance floor (registry-sourced only? any non-`low`?) and state it.
3. **Does `site:` reach both providers?** DuckDuckGo and SerpAPI honour it differently. Post-ticket-20
   the provider is in the cache key, so the two are at least no longer conflated — but a measurement
   taken on one does not transfer to the other, and must not be reported as if it did.
4. **What is the before/after?** On the 21 addressable records, and reporting Name 2 values
   **gained, changed, and lost**. A scoped query that recovers 5 and loses 3 is not a win.

## Constraints

- Every search still goes through `utils.cache.cached_serp` — no lane may issue its own.
- The query string is part of the cache key. Changing query construction **invalidates the lane's
  existing SERP entries**; expect a cold run and do not read the resulting network calls as a
  regression.
- Do not interpolate anything clock-, run- or record-dependent into the query
  (`tests/test_determinism.py` asserts this structurally).

## Why after 24

24 stops the lane shipping wrong Name 2 values; 25 makes the lane propose more. Running them in the
other order scales a known defect before fixing it, and contaminates 25's before/after — a "gained"
value that the identity guard should have refused would be counted as a win.
