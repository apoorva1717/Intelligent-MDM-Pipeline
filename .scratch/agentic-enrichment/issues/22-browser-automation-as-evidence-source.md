# 22 — Browser automation instead of (or beside) the search providers?

Type: grilling
Status: open
Blocked by: —

## The proposal (user, 2026-08-29)

Drive a real browser to gather web evidence rather than depending on search-provider APIs.

## First, the honest framing

**The motivating symptom was not a provider problem.** SerpAPI appeared to fail all day; the cause
was three CA-bundle variables in `.env` pointing at a path on a *different machine*
(`C:\Users\apoorva.ajay\...`). `requests` honours `REQUESTS_CA_BUNDLE`, so every SerpAPI call died
at TLS setup and raised `SearchUnavailable`; DuckDuckGo logged the same error but fell back to
native roots and kept working. With the variables removed, SerpAPI returns correct results —
including for `Vamc Temple Visn 17`, the query a measurement had concluded "genuinely returns
nothing".

So browser automation would have fixed **none** of today's failures. This ticket should not be
justified by them.

## The real case for it

1. **Pages that refuse programmatic fetches.** `PageFetcher` gets a 403 or a bot wall where a
   browser gets the page. This bears directly on `domain-unverified`, the top flag code
   (34/100 and 31/100 on the labelled corpora), and on Stage 5b page corroboration.
2. **JS-rendered organisation pages.** An org page whose name/address only exist after hydration is
   invisible to a plain fetch. Unknown how large this population is — **measure it before building
   anything**: sample the current fetch failures and classify 403 / JS-only / genuinely dead.
3. **A last-resort lane** for the residual records nothing else resolves.

## The two constraints that decide the shape

**Determinism.** The architecture rests on every external answer being cached under a key that is a
pure function of the request (`utils/cache.py`), and on `tools/run_diff.py` requiring
`evidence_network_calls == 0` on the second run. A live browser is non-deterministic by nature —
personalisation, ads, layout drift, timing. A browser lane is only admissible if it records through
the same store with the same discipline, the way `page_reads/` already does. **The recording
discipline is the hard part; the automation is not.**

**Deployment.** `function_app.py` targets Azure Functions; a headless browser does not fit a
consumption-plan Function. So this is plausibly an *offline enrichment/backfill* capability, not an
online request-path one — which is a different product shape and should be decided explicitly.

## Questions

1. **Replacement or addition?** Replacing SERP means owning result parsing, ranking and blocking
   behaviour that SerpAPI currently absorbs — and scraping a search engine directly is a terms-of-
   service question SerpAPI exists to answer. Recommendation to argue against: keep SERP for
   *discovery*, consider a browser only for *reading* a specific known URL.
2. **How large is the population that actually needs it?** Classify current `PageFetcher` failures
   before writing any automation. If 403/JS pages are a handful of records, this is not worth the
   operational weight.
3. **What does a browser read record as provenance?** Scheme B says a page fetched from the domain
   it corroborates is one source, not two — a browser does not change that. It must not become a
   route to a stronger confidence than a plain fetch would earn.
4. **Online or offline?** See the deployment constraint. Decide before designing.

## Not to be conflated with

Ticket 20 (SERP cache key omits the provider) and the CA-bundle fix. Both are plumbing defects with
their own fixes; neither is an argument for a browser.
