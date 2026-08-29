# 15 — `record_type` is wrong on 57% of labelled records, for two distinct reasons

Type: grilling
Status: decided — A hold, B no-change, C -> ticket 17
Blocked by: —

## Question

Measured on 200 labelled records (`research/baseline-measured-2026-08-29.md`): `record_type`
exact-matches `record_type_hint` on **43%** of corporate records and **0%** of government-lab
records.

**A. Should `government` be a first-class `record_type`?**
It is currently not producible at all: `classifier.py:27-28` folds ROR's *government* org type into
`research_institution`, and `RESEARCH` is the only institutional value. The S3 eval set expects
`government` on 80 of 100 records, so it scores 0% by construction. Either the eval set's
vocabulary is wrong, or the pipeline's is. Decide which — and note Phase 2 dedup and the DATAshaper
mapping both consume `record_type`, so widening the vocabulary is not free.

**B. Should the keyword heuristic be able to override a registry COMPANY verdict?**
`classifier.py:163` flips a COMPANY verdict to `research_institution` when
`looks_like_research_institution(name1)` fires. That misclassifies `Exxonmobil Research &
Engineering` and `Zoetis Ref Laboratory` — 18 of 100 S2 records. A corporate R&D arm is a company
with "Research" in its name; the heuristic reads the token, not the entity.

**C. Why is `unknown` 38-39%?** `Record Type Provenance` is `input:low` on those records — nothing
decided. Is that ROR/GLEIF missing (coverage), or evidence present but unranked?

## Notes

(A) and (B) are independent and can be answered separately. (B) looks like a narrow, testable fix;
(A) is a product decision with downstream consumers.

## Findings

All numbers below come from executed probes over the two 100-record eval files
(`docs/results/demo_S2_large_corporate_100_v1 (1)_enriched.xlsx`,
`demo_S3_government_labs_100_v1 (1)_enriched.xlsx`), from live `api.ror.org` /
`api.gleif.org` reads, and from `grep` / `git log` over the tree. Probe scripts:
`.scratch/agentic-enrichment/tmp/probe_*.py`. No production code was changed.

### Provenance to classifier-source mapping, established first

`Record Type Provenance` is a faithful read-out of `record_type_source`
(`enrichment/provenance.py:850-860`), so the exported column partitions the
population by which classifier rule fired:

| provenance | source | S2 | S3 |
|---|---|---:|---:|
| `ror:verified` | `_from_ror` | 30 | 50 |
| `gleif:verified` | `_from_gleif` | 19 | 0 |
| `input:provisional` | `_from_keyword` | 12 | 12 |
| `input:low` | unresolved -> `unknown` | 39 | 38 |

---

### A. Should `government` be a first-class `record_type`?

**A1 - the current vocabulary is three values, and only one place would break.**
`RESEARCH` / `COMPANY` / `UNKNOWN` (`classifier.py:53-55`). The only hard blocker
to a fourth value is `api/models.py:479`:
`record_type: Literal["research_institution", "company", "unknown"]`. A
`government` value fails pydantic validation there and nowhere else.

**A2 - the downstream consumers, enumerated by grep, not by memory.**

| consumer | what it does with `record_type` | effect of a new value |
|---|---|---|
| `api/models.py:479` | `Literal[...]` validation | **breaks** - one-line widening required |
| `api/output_columns.py:99` | header/alias `"Record Type"` | none |
| `enrichment/batch_consensus.py:85,143,463-468` | propagates the donor value inside an address+name group; `unknown` treated as absent | none - a new value propagates like any other |
| `enrichment/orchestrator.py:6553-6555` | `research_institution_count` / `company_count` batch counters | a `government` record counts in **neither**; the batch summary silently under-reports |
| `enrichment/provenance.py:96,103` | scoped-field registration, default `"unknown"` | none |
| `sql/usp_merge_legacy_enriched.sql` | `[Record Type] NVARCHAR(100)`, straight assignment, no CHECK | none |
| **Phase 2 dedup (`dedup/`)** | **nothing** | **none** |

`grep -rn "record_type" dedup/` returns **zero hits** (exit 1). `DedupRow`
(`dedup/models.py:29-52`) carries `ror_id`, `lei_id`, `enriched_name` - never
`record_type`; `dedup/weights.json` does not mention it either. `CLAUDE.md`,
`README.md:1365` ("the only value that reaches ... Phase 2 dedup") and
`README.md:1386` ("Phase 2 scoring consumes `record_type`") are **stale**; the
originating commit `6038372` says so in its own body: *"the current Phase 2 code
does not read record_type at all, so this is a forward-looking flag rather than a
live gap."* **Widening the vocabulary does not touch Phase 2.**

`website_resolver.py` and `tier2b_dept.py` compare against
`"research_institution"` in nine places, but the value they receive is
`routing_type`, not `record_type` - measured at `orchestrator.py:2753`
(`rec_type = result.get("routing_type")`), the only `record_type=` argument passed
into a tier anywhere in `enrichment/`. Tier gating is therefore untouched by a
vocabulary change. (`tier2b_dept` is not called from the orchestrator at all -
ticket 14.)

**Cost of widening, made explicit: one `Literal` edit, one batch-summary counter,
and a re-baselining of the tests that assert the three-value vocabulary. Zero
Phase 2 impact, zero SQL impact, zero tier-routing impact.**

**A3 - does ROR's taxonomy distinguish government cleanly enough to key on?
Precisely, yes; completely, no.** Live `api.ror.org/v2` org types were pulled for
all 80 ROR-matched records. Keying `record_type = "government"` off
`"government" in ROR types`, over all 200:

```
TP = 28   FP = 1   FN = 53      precision 0.966   recall 0.346
```

The single FP is **Centers for Disease Control and Prevention**, which the eval
set labels `company` - the FP is a label error, not a taxonomy error. On the
labelled-government population ROR actually says:

| ROR types | n |
|---|---:|
| (no ROR match at all) | 34 |
| `funder, government` | 25 |
| `facility, funder` | **12** (LBNL, LLNL, JPL, SLAC, AFRL) |
| `healthcare` | 3 |
| `archive` | 3 (county libraries) |
| `facility,government` / `government` / `facility,funder,government` / `funder,nonprofit` | 4 |

**ROR is highly precise but covers only ~35% of what the eval set calls
government.** The largest miss is systematic and *defensible*: the US national
labs are FFRDCs, contractor-operated (LBNL by UC, LLNL by LLNS LLC, Sandia by
"National Technology and Engineering Solutions of Sandia, LLC." - literally the
`operating_name` the page read returned), so ROR types them `facility`, not
`government`. The remaining 34 have no ROR match at all, so no taxonomy helps them.

**A4 - is the eval set's `government` label a business distinction or an artefact?
Measurably contaminated by the eval-set name.** The label is 99% `company` in S2
and 80% `government` in S3 - it tracks the file, not the record. Ten of 200
records carry a label an authority contradicts:

- labelled `company`, ROR says otherwise: Economic Policy Institute (`nonprofit`),
  Dana-Farber Cancer Institute (`facility`), Scripps Research Institute
  (`nonprofit`), Stanford Medicine (`healthcare`), Florida Cancer Specialists &
  Research Institute (`healthcare`), **Centers for Disease Control and
  Prevention** (`government`), **United States Air Force Research Laboratory**
  (`facility`), SLAC National Accelerator Laboratory x2 (`facility`);
- labelled `government`: **City of Hope** (a private nonprofit cancer centre; ROR
  `healthcare`).

CDC labelled `company` while a county library is labelled `government` is not a
coherent business rule. **The label as written mixes three notions - legal entity
type, funding source, and ownership - and is not a usable target without
re-adjudication.**

**A5 - what widening actually buys.** Keying `government` off ROR types moves S3
exact-match from **0% to 28%** and the 200-record total from **21.5% to 35.5%**.
It cannot go higher from ROR alone, because 34 of 81 labelled-government records
never matched ROR (see C).

---

### B. Should the keyword heuristic override a registry COMPANY verdict?

**B1 - the branch the ticket names never fired. Not once, in 200 records.**

`classifier.py:163` (`if verdict is COMPANY and looks_like_research_institution(ev.name1)`)
lives inside `_from_gleif`, whose first statement is `if not ev.lei_id: return None`.
Measured:

```
S2: keyword-decided = 12, of which carry an LEI = 0
    records with an LEI = 30, of which looks_like_research_institution(Name 1) = 0
S3: keyword-decided = 12, of which carry an LEI = 0
    records with an LEI =  0
```

Every keyword-decided record has **neither a ROR id nor an LEI**. The guard is
unreachable on this data, and **the baseline's attribution of the ExxonMobil /
Zoetis misclassifications to `classifier.py:163` is wrong.** The rule that
actually fired is `_from_keyword` (`classifier.py:168-169`) - the rank-3 fallback
that runs *only* when no registry answered. Nothing is overriding a registry
verdict; there is no registry verdict to override.

The baseline's "18 of 100 S2 records" also decomposes differently than stated: of
the 18 S2 `research_institution` records, **12** are the keyword fallback and
**5** are ROR's own verdict (Dana-Farber `facility`, Scripps `nonprofit`, EPI
`nonprofit`, Stanford Medicine `healthcare`, Florida Cancer Specialists
`healthcare`) - all five label errors per A4 - plus 1 whose hint is `government`.

**B2 - what the guard was for, from `git log -p`.** Introduced in `6038372`
("Fix 3: single classification authority for record_type"). Its stated case:
*"an LEI proves legal registration, not commercial status - universities,
hospitals and foundations hold them for bond issuance"*, pinned by
`tests/test_record_type_authority.py::TestLEIGuard::test_commercial_form_is_withheld_for_an_institution_name`
("Riverside University" with a commercial ELF). A live GLEIF probe over 22 real
institutions and corporate labs found **no case where the guard is load-bearing**:
every genuine institution sampled either carried a non-commercial ELF (Yale
`7W53`, Johns Hopkins `ZVS9`, Duke `358I`, Cleveland Clinic `7VK5`, Mayo `9I4Y`,
Princeton `T4M6`, MIT `8888`/`INSTITUTE`, Brigham `8888`/`Hospital`) or fell
through to `None` (Karolinska, ETH Zurich, Max Planck, Sloan Kettering). The one
record that reached a commercial verdict *and* tripped the guard was **"Stanford
University Equity Partnership, L.P."** (`9999` / `LIMITED PARTNERSHIP`) - where
the guard's withholding is *wrong*: an LP is a company. **The guard is
speculative, not empirically grounded, and its only observed effect is a false
negative.**

**B3 - the keyword source's actual score, measured in both directions.**

24 records decided by `_from_keyword` across both files:

| | n | exact-match correct | correct if `research_institution` is accepted for `government` |
|---|---:|---:|---:|
| S2 (all hint `company`) | 12 | 0 | 0 |
| S3 (10 hint `government`, 2 hint `company`) | 12 | 0 | 10 |
| **total** | **24** | **0/24** | **10 right / 14 wrong** |

Removing the source turns all 24 into `unknown`. Under exact match that is
**neutral** (0 -> 0); under the coarse reading it costs 10 and saves 14.
**Net-harmful, but mildly - and it is an abstention-vs-assertion problem, not an
accuracy problem.**

**B4 - no narrower lexical predicate separates the two populations.** Tested
`looks_like_university_or_research_institute` (P1, already in
`utils/text_utils.py`) and "P0 and no corporate legal suffix" (P2):

| predicate | fires on the 24 | hint=gov | hint=company | precision(gov) over all 200 |
|---|---:|---:|---:|---:|
| P0 (current) | 22 | 10 | 12 | 0.55 |
| P1 (narrower) | 4 | 1 | 3 | **0.33** |
| P2 (no legal suffix) | 21 | 10 | 11 | 0.56 |

P1 cuts the S2 harm 11->2 but cuts the S3 help 10->1, and its precision for
`government` is *worse* than the current predicate. The reason is visible in the
name shapes: the harmful and helpful populations are lexically identical -
`ExxonMobil Research & Engineering`, `Ford Research and Engineering`,
`Zoetis Ref Laboratory Cincinnati` on one side; `HCA Public Health Laboratory`,
`Orange County Public Health Laboratory`, `Utmb Galveston National Laboratory` on
the other. Both are `<proper-noun head> <institutional noun>`. **The
distinguishing evidence is not in the name.** No failing test was written: the
precondition (net-harmful override *and* an expressible narrower predicate) fails
on both halves.

**B5 - the asymmetry is the real defect, and it is cheap to fix.** The keyword
source can only ever say `research_institution` ("the name not looking like an
institution is not evidence of a company", `classifier.py:32-34`). But a corporate
legal-form suffix **is** evidence of a company, and it is unambiguous on this data:

```
Name 1 carries Inc|LLC|Corp|Corporation|Company|Co|Ltd|LP|LLP|PLC|GmbH|AG|NV|BV|SA|Pty
  -> 55 records, hint=company 55, hint=government 0     precision 1.000
```

Only **one** of the 200 names carries both signals (`Bio-Rad Laboratory Inc`,
hint `company`, currently `research_institution`) - so suffix-before-keyword is
also the right order for it. Projected effect of a symmetric
`legal suffix -> company` source ranked above `_from_keyword`, restricted to the
records the keyword tier currently decides:

```
newly correct: 21    newly wrong: 0
```

S2 exact-match **43% -> 64%**; 200-record total **21.5% -> 32%**.

---

### C. Why is `unknown` 38-39%?

**C0 - the necessary condition.** All 39 S2 and all 38 S3 `unknown` records carry
an empty `ROR ID` **and** an empty `LEI ID` (verified `True` for both files).
`_from_ror` and `_from_gleif` both return `None` at their first guard,
`_from_keyword` returns `None`, terminal `unknown`. So the question is entirely
"why did no registry identity attach", plus "what else was on the record that
`classify()` cannot read".

**C1 - partition by evidence present at `finalise` that the classifier does not
rank.**

| bucket | S2 | S3 |
|---|---:|---:|
| **2 - unranked evidence present** | **33** | **28** |
| - resolved `.gov` / `.mil` domain | 1 | 28 |
| - corporate legal-form suffix in Name 1 | 20 | 0 |
| - page-read `operating_name` only | 12 | 0 |
| **1 - nothing rankable on the record** | **6** | **10** |

**61 of the 77 `unknown` records (79%) carry a signal the classifier is
structurally blind to.** Examples: `US Department of Energy` -> `energy.gov` ->
`unknown`; `US Environmental Protection Agency` -> `epa.gov` -> `unknown`;
`Vamc Miami Visn 8` -> `va.gov` -> `unknown`; `Harrington Industrial Plastics LLC`,
`Neptune-Benson, LLC`, `Microsemi Corp` -> `unknown`.

**Caveat on the `.gov` half.** 26 of the 38 S3 unknowns are flagged
`domain-unverified`, and the `.gov` domains break down `web:va.gov:low` x14,
`web:energy.gov:low` x4, `web:sandia.gov:provisional` x6,
`web:epa.gov:provisional` x1, `web:michigan.gov:low` x1, `web:navy.mil:low` x1,
`web:detroitmi.gov:low` x1. A `.gov` TLD is registrar-restricted to US public
bodies, but a domain the ownership guard refused to attribute says nothing about
*this record*. Ranking it would import a `:low` value into a decided field - a
Scheme-B question, not a classifier question. The legal-suffix half (S2) carries
no such caveat: it reads the record's own input and scores precision 1.000.

**C2 - the upstream cause is the Tier 1 match gate, not registry coverage.** For
each `unknown` record ROR was searched by name, and a hit counted only when a
returned ROR organisation's own registered domain/link equals the record's
resolved registrable domain - a strict, objective identity criterion:

```
S2: a ROR record with the record's own domain exists for 26/39 unknowns (67%)
S3:                                                      21/38 unknowns (55%)
overall                                                  47/77          (61%)
```

`3M Company` -> `3M (United States)`; `Amazon` -> `Amazon`;
`McKesson Medical-Surgical Inc.` x4 -> `McKesson`; `Idexx Reference Labs` x4 ->
`IDEXX Laboratories`; `US Department of Defense` -> `United States Department of
Defense` (`funder, government`); `US Environmental Protection Agency` ->
`Environmental Protection Agency`; `MI Department Of Health & Human Services` ->
exact. This is a **lower bound** - the criterion misses cases like
`Bayer Pharmaceuticals` where ROR returns country subsidiaries whose links are
`bayer.<cc>`.

The 13 S2 / 17 S3 residual are the genuine coverage gap and are exactly what you
would expect: small private firms (`Harrington Industrial Plastics LLC`,
`Neptune-Benson, LLC`, `Marine Reef International`, `Titan Florida LLC`) and
unresolvable input fragments (`DOH - Bureau of`, `Slac/su_mcculsimes`,
`APCT-Orange County DBA Cartel`).

**So: `unknown` is ~61% a Tier 1 matching failure against an organisation that is
in ROR, ~39% genuine absence - and orthogonally, 79% of the population carries an
unranked signal at `finalise`.** Same finding as tickets 11/13: the loss is in the
registry gate, not in the classifier. The classifier is a symptom reporter here.

## Decision

**A - recommend: yes, but not from this eval set, and not yet.**
`government` is cheap to add (one `Literal` in `api/models.py:479`, one batch
counter in `orchestrator.py:6553-6555`; **zero** Phase 2, SQL or tier-routing
impact - the "Phase 2 dedup consumes it" premise is measurably false, and
`CLAUDE.md` / `README.md:1365,1386` should be corrected either way), and ROR's
`government` org type keys it at **precision 0.966** (1.000 once CDC's label is
fixed). But recall is **0.346**, the US national labs land in `facility` for a
defensible reason, and the S3 label is **not trustworthy**: it tracks the eval
file's name, contradicts an authority on 10/200 records, and calls CDC a company.
Order of operations: **re-adjudicate `record_type_hint` per record first**
(ticket 02's labelling rules), decide whether the business needs *legal entity
type* or *sector / ownership* - they are different fields - and only then widen.
Widening now buys 0% -> 28% on S3 against a label that is partly an artefact.

**B - recommend: leave `classifier.py:163` alone; it is not the bug.**
It fired zero times in 200 records and cannot fire without an LEI. It is
speculative (no live GLEIF case supports it; the one case it would touch, an
"Equity Partnership, L.P.", it gets wrong), but it is also harmless, and removing
it is not measurably justified. **No failing test written** - the ticket's
precondition (net-harmful *and* a narrower predicate) fails on both halves: no
narrower lexical predicate exists, because the harmful and the helpful names are
the same shape.

The change that *is* justified is a different one: **add a symmetric
`legal-form suffix -> company` source ranked above `_from_keyword`.** Precision
1.000 on 55/200 records, **+21 correct / -0 wrong**, S2 43% -> 64%. Code: one
predicate in `utils/text_utils.py`, one `_from_legal_suffix` in `classifier.py`,
one entry in the `classify()` ranking tuple, plus a fourth `record_type_source`
value (`legal_form`) and its `provenance.py:850-860` mapping. That is a new
ticket, not this one.

**C - diagnosis: not coverage, and not one problem.**
`unknown` = 39/38 records, every one with no ROR id and no LEI. Two orthogonal
partitions:

1. **Unranked evidence at `finalise`: 61/77 (79%)** - 29 records with a resolved
   `.gov`/`.mil` domain, 20 with a corporate legal suffix, 12 with a page-read
   `operating_name`. Only 16/77 have nothing to rank.
2. **Upstream: 47/77 (61%, lower bound)** are organisations that *are* in ROR
   under the record's own domain. The Tier 1 gate rejected a match that exists.

The cheap half of (1) - the legal suffix - is B's recommendation above and is
worth doing on its own merits. The `.gov` half should **not** be ranked until the
domain-ownership question (baseline section 4, `domain-unverified` at 34/31) is
settled, because 26 of those 28 domains are `:low` and unattributed. (2) belongs
to tickets 11/13 - instrument the ROR rejection funnel - and is where the actual
39% lives.
