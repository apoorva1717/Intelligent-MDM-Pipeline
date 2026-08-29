# 18 — Two silent Tier 1 losses: ROR 500 on `/`, and a stripped leading numeral

Type: task
Status: open
Blocked by: —

## The finding (measured live, ticket 11, 300 lookups)

Both are records that die without anything downstream being able to tell they died *differently*
from a genuine registry miss.

**1. ROR returns HTTP 500 on names containing `/`.** 3 occurrences in 300 lookups. The failure is
indistinguishable downstream from "this organisation is not in ROR" — so a transient/​malformed
request is cached and reported as an absence. This is the same class of confusion that
`search.base.SearchUnavailable` exists to prevent on the SERP path: *provider failed* must never
be recorded as *no results*.

**2. `1910 Genetics` reaches ROR as `Genetics`.** Preprocessing strips the leading numeral, and the
resulting one-word query then trips the ambiguity guard. The record is lost to a preprocessing
side-effect, not to a registry gap.

## Why it is worth a ticket

Ticket 11 established that **registry coverage, not the gates, is the binding constraint** — which
makes it important that "not in the registry" actually means that. Every silent loss of this shape
inflates the apparent coverage ceiling and misdirects the tickets that are aimed at it.

Small absolute numbers (4 of 300). Cheap to fix. The value is in the *category* being closed, so
that the coverage measurement can be trusted.

## Questions

1. Should `/` be escaped, or the name split at it and both halves tried? Confirm the 500 is
   reproducible and attributable to the character before choosing.
2. Is an ROR 5xx currently cached as an absence? Check `utils/cache.py` and whether Tier 1 has an
   equivalent of `SearchUnavailable`. If it does not, that is the real fix and it is general.
3. Which preprocessing rule strips the leading numeral, and what was it protecting against?
   A leading numeral is load-bearing in a company name (`1910 Genetics`, `3M`, `23andMe`) and noise
   in an address fragment — establish which population the rule was written for.

## Evidence

`.scratch/agentic-enrichment/research/11-rejection-funnel.md`; probe harness at
`.scratch/agentic-enrichment/scripts/drive_tier1.py`, aggregation at `aggregate_funnel.py`,
instrumentation behind `FUNNEL_PROBE` in `enrichment/funnel_probe.py`.
