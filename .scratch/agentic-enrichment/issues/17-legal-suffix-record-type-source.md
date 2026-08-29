# 17 — Add a legal-form-suffix `record_type` source, ranked above the keyword fallback

Type: task
Status: DONE 2026-08-29 — implemented and measured
Blocked by: —

## Why (measured, ticket 15, 200 labelled records)

`record_type` is `unknown` on 77/200. Ticket 15 established that this is **not** registry
coverage in the main: 61/77 (79%) carry evidence that is present but *unranked* at `finalise`.
The largest clean slice is a legal-form suffix in Name 1 that nothing currently reads.

Measured over the 200 labelled records:

| signal | hits | hint=company | hint=government | precision |
|---|---|---|---|---|
| `Inc\|LLC\|Corp\|Ltd\|GmbH\|…` suffix | 55 | 55 | 0 | **1.000** |

Exactly one name carries both a legal suffix and a research keyword (`Bio-Rad Laboratory Inc`),
and the proposed ranking gets it right. Projected effect: **+21 correct, −0 wrong**; S2 exact
match 43% → 64%.

Contrast with the existing `_from_keyword` fallback (`classifier.py:168-169`), which decides 24
records at **0/24 exact match** — 10 right / 14 wrong even under a coarse
`research_institution ⊇ government` reading. Removing it is exact-match-neutral; this replaces it
with a signal that is actually predictive.

## The change

1. A suffix predicate in `utils/text_utils.py` (sibling to `looks_like_research_institution`).
2. `_from_legal_suffix(ev)` in `enrichment/classifier.py`.
3. One entry in the ranking tuple, **above** `_from_keyword` and **below** every registry source —
   this is a fallback, never an override. A registry verdict must still win.
4. A fourth `record_type_source` value, plus its mapping in `provenance.py:850-860`.

## Constraints

- `record_type` is decided once, at the end of `finalise`, from ranked evidence
  (`classifier.py` is the single authority). Do not add a second decision point.
- Provenance must stay Scheme B compliant (`source:confidence[+witness]`), and a suffix read off
  the input name is **not** registry-verified — it cannot be `verified`.
- Do **not** rank the `.gov`/`.mil` domain signal in this ticket. 26 of the 28 S3 `.gov` domains
  are `web:*:low` and flagged `domain-unverified`; ranking them would build on an unsettled
  domain-ownership question. The legal-suffix half has no such caveat. See ticket 15 finding C.

## Not in scope

The residual `unknown` population that is a genuine Tier 1 matching failure (ticket 15 finding C
puts ≥47/77 in ROR but unmatched) belongs to tickets 11/13.

## Resolution (implemented 2026-08-29)

**Reproduced, not inherited.** Ticket 15's `+21 / −0` was a projection from a proposed ranking. Run
through the real `classify()` over the same 200 labelled records:

```
records: 200   registry-decided (untouched): 99
newly CORRECT: +21        newly WRONG: -0
S2 exact match: 43% -> 64%      S3: 0% -> 0%   (unchanged, as expected)
```

S3 stays at 0% by construction — `government` is still not a producible `record_type`
(ticket 15 finding A, open, and the user's call). This ticket never claimed to move it.

### The predicate: position, not just token

`utils/text_utils.has_corporate_legal_suffix` reads a legal form only in **final position** — of the
whole name, or of a segment before a comma or a *doing-business-as* marker
(`Value Plastics Inc DBA Nordson Medical`, a real shape in this data).

Three variants were measured. All three score **precision 1.000 on the 200 records**, so this sample
cannot distinguish them:

| predicate | fires | precision | effect |
|---|---:|---:|---|
| last token only | 54 | 1.000 | +20 / −0 |
| **segment-final** | **55** | **1.000** | **+21 / −0** |
| any position | 55 | 1.000 | +21 / −0 |

Segment-final was chosen over any-position **despite the sample scoring them identically**, because
the sample is 200 records and the pipeline runs on ten thousand. Any-position claims
`Co-operative Research Centre`, `AG Research Ltd Kenya Branch` and `Co Down Health Trust` as
companies — `co`, `ag`, `sa`, `nv`, `bv` are ordinary words away from the end of a name. Position is
what makes the short tokens safe, and it is asserted by test, not left to the measurement.

The token set is **deliberately not reused** from `tier1_lei._LEGAL_FORM_TOKENS`. That set exists to
*strip* these tokens before a name comparison, where over-inclusion is nearly free; here it asserts
a record type, where a false positive calls a research institute a company. The two overlap heavily
and are not the same set.

### The change

- `utils/text_utils.py` — `_CORPORATE_LEGAL_FORMS`, `_LEGAL_SEGMENT_SPLIT_RE`,
  `has_corporate_legal_suffix()`, sibling to `looks_like_research_institution`.
- `enrichment/classifier.py` — `SOURCE_LEGAL_FORM = "legal_form"`, `_from_legal_suffix()`, one entry
  in the `classify()` ranking tuple between GLEIF and keyword. Module docstring updated: the ranking
  is documented there and had to stay true.
- `api/models.py` — `record_type_source` Literal widened to five values.
- `enrichment/provenance.py` — the classifier branch already routed a non-registry source to
  `SOURCE_INPUT` with `has_source=True`, so `legal_form` maps correctly with **no logic change**.
  Only the comment changed, to name both sources. Asserted by
  `TestLegalSuffixProvenance::test_it_is_never_registry_verified`: a suffix is what the record
  *claims* to be, so it can never reach `verified`.
- `README.md` — the source-ranking table (now five rows), the module reference, the telemetry line.
- `docs/thesis/11_DELTA.md` — dated supersession note; the derivation is not rewritten.

### One existing test changed, and why it is not a regression

`test_variant_spellings_of_one_org_share_a_type` asserted that three name forms of
"Coastal Diagnostics" come out with **one** type. Two of the three carry a legal suffix and are now
decided `company`; the bare form carries no signal and stays `unknown`.

That is not a contradiction, and the old uniformity was uniformity of *ignorance*. `unknown` asserts
nothing — which is exactly how `batch_consensus` treats it (`_UNKNOWN_TYPE`, `batch_consensus.py:143`,
`:460-468`): the sole decided value in a cluster propagates to members that have none. So the three
now **converge on `company`** in a real batch, where before they converged on nothing. `finalise` is
called directly in that test, upstream of the consensus pass, so the invariant to assert at that
layer is the absence of a contradiction. Renamed to
`test_variant_spellings_of_one_org_never_contradict` and tightened: it now asserts both that at most
one type is decided **and** that the decided one is `company`.

### Verification

`5 failed, 2844 passed, 5 skipped` — the documented baseline's same five pre-existing failures, plus
23 new tests. (Two of the five pre-existing failures are `record_type` assertions expecting
`company`; both names carry no legal form, so this ticket does not touch them.)

### Still open, deliberately

The `.gov`/`.mil` domain signal is **not** ranked here — 26 of 28 S3 `.gov` domains are `web:*:low`
and flagged `domain-unverified`, so ranking them would build on the unsettled domain-ownership
question. Unchanged from the ticket's stated scope, and the reason S3 does not move.
