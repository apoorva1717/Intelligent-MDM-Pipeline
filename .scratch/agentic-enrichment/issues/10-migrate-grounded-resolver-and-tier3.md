# 10 — What happens to `grounded_resolver` and `tier3_llm`?

Type: grilling
Status: open
Blocked by: 04, 05

## Question

Decision 5 says the lane replaces Tier 3 and absorbs `grounded_resolver`. Decide what that means
for the code and its tests.

1. **`grounded_resolver.py` (30 KB)** — is it deleted, or does it become the lane's
   `propose_and_verify` implementation? Its `_re_verify` is the exact invariant the lane needs, and
   it already has country-agreement and entity-distinctness gates. Rewriting it would be throwing
   away working guard logic.
2. **`tier3_llm.py`** — deleted outright, or kept as a fallback when the agent lane is disabled by
   feature flag? Every comparable change in this repo shipped behind a flag
   (`LEI_LOOKUP_ENABLED`, `PAGE_CORROBORATION_ENABLED`, `WIKIDATA_ENABLED`). Should this one?
3. **The tests.** `test_grounded_resolver.py` (35 KB) and the `test_tier3*.py` family encode real
   behaviour. Which assertions carry over to the lane, which describe a mechanism that no longer
   exists, and which are actually specifying the invariant and must survive verbatim?
4. **The `unverified-inference` flag.** It exists because Tier 3 writes unverifiable values. If
   nothing unverifiable can be written any more (decision 2), does the flag become dead — and if it
   does, is that a *result to celebrate* or a signal that the lane is silently declining more?

## Notes

Blocked by 04 and 05: the migration shape depends on the tool contract and on which records the
lane actually sees.
