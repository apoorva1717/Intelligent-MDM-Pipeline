# 27 — A wrong domain at `provisional` confidence ships with no flag at all

Type: task
Status: open
Blocked by: —
Source: measured on the S2 sample, 2026-08-29

## The finding

```
name1 = 'Exxonmobil Research &'   name2 = 'Engineering Co'
domain = 'nlrb.gov'   provenance = 'web:nlrb.gov:provisional'   flag_codes = []
```

Two records carry `nlrb.gov` — the **National Labor Relations Board** — as ExxonMobil's domain. One
of the two ships with **no flag of any kind**.

Almost certainly a SERP result about an NLRB case *involving* ExxonMobil: the page names the
organisation, so the ownership guard's name-match test passes, and nothing else contradicts it.

## Why this is the worst class of error the pipeline can make

Every other failure mode is either visible (`domain-unverified`, `unverified-inference`) or an
absence (`unknown`, an empty slot). This one is **wrong and silent**. It reaches the SAP record as
an ordinary value with an ordinary-looking provenance.

`domain-unverified` did not fire because the value landed at **`provisional`**, not `low`. That
threshold is doing more work than it was designed for: `provisional` currently means "a web source
named this organisation on this domain", which a court filing, a news article, a regulator's docket
and a supplier directory all satisfy.

## Questions

1. **Is a `.gov` domain for a company always wrong?** A cheap, high-precision guard: a record
   classified `company` whose domain is `.gov` or `.mil` is almost certainly reading a regulator,
   not the company. Measure the population before writing it — the reverse case
   (`government` record on a `.com`) is common and legitimate.
2. **Should a page that names the organisation only in a *third-party* context count as
   corroboration?** `utils/domain_resolver.resolve_domain` accepts "a name match on the page". A
   docket page naming ExxonMobil is a name match. Distinguishing "this site IS the organisation"
   from "this site MENTIONS the organisation" is the real question, and it is the same distinction
   `page_corroborator` already tries to draw — check whether it ran on these records and what it
   said.
3. **Is the `provisional` band too wide?** If a single naming web page yields `provisional`, then
   `provisional` cannot mean what a reviewer assumes. Consider whether corroboration from a page
   that is not the candidate domain itself should cap at `low`.
4. **Would a known-aggregator list help?** Eight directory domains were accepted on the chemspeed
   run (`facebook.com`, `linkedin.com`, `thebluebook.com`, `sbir.gov`, `american-coatings-show.com`)
   — all eight *were* flagged, so the guard works there. `nlrb.gov` is not an aggregator, so a list
   would not catch this one. Note it and move on; a list is not the fix.

## Related

Both affected records are also ticket 28's overflow (`Exxonmobil Research &` + `Engineering Co`).
The record whose name is whole — `ExxonMobil Research & Engineering` — resolves to
`exxonmobil.com` at `verified+domain`. **A truncated name is what sent the search somewhere else.**
Fixing 28 may remove this instance without addressing the class, which is why they are separate
tickets.

## Evidence

`logs/compare/s2_now.json`; `logs/compare/enriched_samples_200.xlsx`.
