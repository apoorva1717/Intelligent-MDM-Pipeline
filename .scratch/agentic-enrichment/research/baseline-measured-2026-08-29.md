# Measured baseline — current pipeline, 200 labelled records

Source: `docs/results/demo_S2_large_corporate_100_v1 (1)_enriched.xlsx` and
`demo_S3_government_labs_100_v1 (1)_enriched.xlsx`. **Current schema** (all 67 output columns
present, incl. `Flag Codes`, `Flagged Fields`, 7 provenance columns), plus eval metadata
(`record_type_hint`, `expected_issue_codes`, `expected_use_cases`, `defect_evidence`).

| metric | S2 corporate | S3 gov labs |
|---|---|---|
| registry identity (ROR or LEI) | 49% | 50% |
| ROR / LEI | 30% / 30% | 50% / **0%** |
| Domain | 98% | 93% |
| Department Domain | **1%** | **5%** |
| Name 2 populated | 57% | 65% |
| **Contact populated** | **5%** | **4%** |
| flagged for review | 52% | 65% |
| `record_type` = unknown | 39% | 38% |
| **`record_type` exact match vs hint** | **43%** | **0%** |

## 1. `record_type` cannot express `government` — vocabulary mismatch, by design

S3 expects `government`=80, `company`=20. Produced: `research_institution`=62, `unknown`=38.
**Zero exact matches.** `government` never appears in either file's output.

Cause is deliberate: `classifier.py:27-28` maps ROR's org types *education, healthcare,
**government**, facility, nonprofit, archive, other* all onto `research_institution`.
`RESEARCH = "research_institution"` (`:53`). The value is not producible.

So this is a **product gap, not a classifier bug** — the eval set wants a distinction the design
collapses on purpose. Decide whether `government` should be a first-class `record_type`.

## 2. The keyword heuristic overrides a correct COMPANY verdict

`Exxonmobil Research & Engineering` and `Zoetis Ref Laboratory` -> `research_institution`,
expected `company`. `classifier.py:163`: `if verdict is COMPANY and
looks_like_research_institution(ev.name1)` — a name containing "Research"/"Laboratory" flips a
company verdict. 18 of 100 S2 records are wrong this way.

## 3. Name 2 is essentially not enriched — the Tier 2B story, confirmed empirically

Name 2 **provenance** distribution:

| | S2 | S3 |
|---|---|---|
| `(empty)` | 50 | 38 |
| `input:low` (input stood, nothing corroborated) | 43 | 39 |
| `llm:provisional` | 7 | 21 |
| `ror:verified` | 0 | 2 |

Of 57 populated Name 2 values in S2, **43 are the untouched input** and **none** came from a web or
registry source. Department Domain fires on 1% / 5%.

The mechanism is now fully explained:
- **Tier 2B (department web search) is dead code** — see ticket 14.
- **Tier 2A requires a contact, and only 5% / 4% of records have one.**

Both department lanes are unavailable on ~95% of records. Nothing else populates Name 2 from
evidence. This is the strongest, most actionable finding in the set.

## 4. `domain-unverified` is the top flag code (34 S2 / 31 S3)

Domain is populated at 98% / 93%, but a third of records cannot attribute it. Domain provenance
shows a long `web:<host>:low` tail (`web:merck.com:low`=7, `web:va.gov:low`=14). The ownership
guard in `resolve_domain` is refusing a large minority — worth checking whether it is too strict
or the candidates are genuinely bad.

## 5. The flag redesign worked

52% / 65% flagged vs **90%** on the older 500-record file, only 7-8% carry more than one code, and
`flagged_fields` is populated and discriminating (S3: name2=45, name1=38, domain=31). The Fix 8
triage-signal goal is met. This is not where the problem is.

## What this does NOT measure

Per-gate rejection counts inside ROR/GLEIF (ticket 11's actual funnel) — that still needs
instrumentation or a fresh run. This measures *outcomes*, not *where in the lookup they were lost*.
