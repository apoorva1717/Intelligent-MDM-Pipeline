# 17 — Add a legal-form-suffix `record_type` source, ranked above the keyword fallback

Type: task
Status: open
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
