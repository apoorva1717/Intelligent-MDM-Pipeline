# 05 — The exact entry-gate predicate

Type: grilling
Status: open
Blocked by: 03, 12

## Question

Decision 6 settled the *concept*: a record enters the lane when it has "no confident registry
identity". Turn that into a predicate.

1. **What score band counts as "weak"?** `ROR_CONFIDENCE_THRESHOLD = 0.8`,
   `LEI_NAME_MATCH_THRESHOLD = 88`, `FUZZY_MATCH_THRESHOLD = 80`. A match at exactly 0.80 or 88 is
   currently accepted and written. Is there a comfort band above the threshold below which the
   record should *still* enter the lane — and where is it?
2. **Refused ambiguities.** `REGISTRY_AMBIGUITY_MARGIN = 2.0` throws both candidates away. These
   are the strongest lane candidates (the pipeline found two plausible answers and discarded both).
   Do they always enter, or only when some other signal exists?
3. **Do records with a confident hit but a `registry-location-mismatch` flag enter?**
4. **What about records that never reach Tier 1 at all** — a name preprocessing could not make
   sense of? Those currently die early.
5. **What is the measured population** of the resulting predicate, from 03's baseline? Decision 10
   says gate width is the cost lever, so this number *is* the cost model.

## Notes

Blocked by 03: choosing a band without knowing where the failures actually sit is guessing.
