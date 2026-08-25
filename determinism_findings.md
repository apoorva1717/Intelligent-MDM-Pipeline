# Determinism and cross-source consistency — findings

**Batch:** `docs/thesis/chemspeed_us_100.xlsx` (100 records)
**Tool:** `tools/run_diff.py`
**Artefacts:** `logs/determinism/` (run1, run2, frozen3, frozen4, gate.json, gate_frozen.json)

---

## 1 · The measurement that started this

Two runs of the identical batch, on the identical codebase, produced **7
substantively different records**. Before this work the claim rested on an
eyeball comparison; it is now a measurement anyone can repeat, on the two run
artefacts this repository still holds:

```bash
python tools/run_diff.py logs/runs/E_final.json logs/runs/F_final.json
```

> rows compared 100 · **rows differing 7** · cell differences 30

| Column | Differing cells |
|---|---|
| Name 1 | 4 |
| Name 1 Provenance | 4 |
| Flag Codes / Flagged Fields / Flag Reason | 3 each |
| Domain Provenance | 2 |
| Flag for Review | 2 |
| Operating Name | 2 |
| Operating Name Provenance | 2 |
| Domain · ROR ID · LEI ID · LEI ID Provenance · Record Type | 1 each |

Four of the seven changed **Name 1 itself**. Four changed who is on record as
having written it — `input:1:verified` on one run against
`llm_tier3:3:self_medium` on the other, which is the Tier 3 `confidence`
self-report flipping between `self_high` and `self_medium`. That is not
cosmetic: `finalise` drops a Tier 3 department guess unless the confidence is
high, so the same record ships a department on one run and none on the next.
One row gained an LEI on one run and not the other.

**A scope note, stated plainly.** The two silent wrong-entity acceptances the
brief describes — ATI Trading, BIC Corp — are **not** among these seven, and
are not in this batch at all. See §6: the runs that produced them were made
against a larger Chemspeed extract that is not in this repository. What is
reproduced above is the 7-record/30-cell noise floor on the batch that *is*
here; the named cases are reproduced as fixtures instead.

---

## 2 · What was nondeterministic, and where

### 2.1 · The LLM calls (Fix A)

| Site | What was wrong |
|---|---|
| `llm/openai_client.py::call_openai` | `temperature=0.0` hardcoded; **no `top_p`, no `seed`**. The service default for `top_p` was in force and unstated. |
| `llm/openai_client.py::OpenAIClient.extract_json` | Accepted a `temperature` argument and **silently dropped it** — every caller that passed one was configuring nothing. |
| `dedup/llm.py::DedupLLM.adjudicate` | Same: no `top_p`, no `seed`. |
| `enrichment/orchestrator.py` (person-affiliation provenance) | Recorded `"temperature": 0.0` as a **literal**, free to disagree with the request it described. |
| `enrichment/person_affiliation.py` | Injected SERP snippets into the prompt **in the order the search API returned them**. Two runs that retrieved the same five results in a different order built two different prompts. |

Prompt templates themselves were clean — no clock, run id or record id — and
that is now pinned structurally rather than assumed.

### 2.2 · Cache keying and scope (Fix B)

The keys were never the problem: `lookup_key` / `serp_key` were already pure
functions of the request. **Scope and lifetime** were.

| Lane | Before |
|---|---|
| SERP | In-memory only, `SerpCache`; cleared with the process. |
| ROR / GLEIF | Module-level dicts, **cleared per batch** by design. |
| Person affiliation | Called `search_client.search` **directly** — never cached at all. |
| `PageFetcher` (dept-probe subdomain HEADs, link scrape, candidate verification, Tier 2A profile pages, Tier 2B department pages) | **Not cached at all.** Only the corroborator's root read was recorded, and only at the *domain* level. |
| Page reads | Recorded — the one lane that already worked. |

So a "re-run against a warm cache" re-gathered most of its evidence. Eleven of
the 30 differing cells were `Operating Name Provenance` and nothing else,
because `operating_name_provenance()` stamped `date.today()` whether or not the
page had been re-read.

### 2.3 · Candidate selection (Fix C)

Every one of these picked its winner with a **stable sort on the score alone**,
or with `max()`, which returns the *first* maximum — so every tie was broken by
the API's response order:

- `enrichment/tier1_lei.py::_best_verified_candidate` — `rank > best_rank` over the response as returned;
- `enrichment/tier1_ror.py::call_ror` — `sorted(items[:10], key=…, reverse=True)`, and the `[:10]` truncation was itself response-ordered;
- `enrichment/website_resolver.py::select_website_from_serp` — `max(valid, key=_rank)`, commented "first max preserves SERP order on ties";
- `enrichment/tier2a_contact.py::_search_and_rank`, `enrichment/tier2b_dept.py::_search_and_rank`;
- `enrichment/orchestrator.py::_probe_department_url` — two `scored.sort(reverse=True)` scans and a path scan tie-broken on the SERP index.

### 2.4 · The consistency gap (Fix D)

Not nondeterminism — a defect the nondeterminism exposed. Nothing anywhere
compared what two sources said the organisation was called. `_run_lei_lookup`
runs **after** a ROR company match and overwrites `name1`, leaving ROR's id,
domain and acronym on the record. Every individual guard had passed.

---

## 3 · What changed

| # | Change | Files |
|---|---|---|
| A1 | `temperature=0`, `top_p=1`, `seed=42` on every decision-gating call, as module constants | `llm/openai_client.py`, `dedup/llm.py` |
| A2 | One-shot, process-wide fallback if the deployment rejects `seed` | `llm/openai_client.py`, `dedup/llm.py` |
| A3 | `extract_json` forwards its `temperature`; provenance records the constant | `llm/openai_client.py`, `enrichment/orchestrator.py` |
| A4 | Injected evidence sorted by a stable key before rendering | `enrichment/person_affiliation.py` |
| B1 | One `EVIDENCE_CACHE_DIR` with six namespaces; keys pure functions of the request | `utils/cache.py`, `config.py` |
| B2 | Every lane routed through it — `cached_serp`, `PageFetcher(store=…)`, `cached_registry_get` | `utils/cache.py`, `search/page_fetcher.py`, `enrichment/*` |
| B3 | Entries immutable and dated; `Operating Name Provenance` stamps the **fetch** date | `utils/cache.py`, `enrichment/page_corroborator.py` |
| B4 | `CACHE_FROZEN` — a miss is a recorded `evidence-unavailable-frozen`, not a call | `utils/cache.py`, `config.py`, `scripts/run_batch.py` |
| B5 | A failed search is no longer recorded as an empty one | `search/base.py`, `search/serpapi_client.py`, `search/duckduckgo_client.py` |
| B6 | The **model** is a recorded source too — see §5 | `llm/openai_client.py` |
| C1 | One total order everywhere: `(stronger discriminator, score DESC, canonical id ASC)` | `enrichment/registry_match.py` + all six selection points |
| C2 | `REGISTRY_AMBIGUITY_MARGIN = 2.0` — a near-tie is a no-match | `enrichment/registry_match.py` |
| C3 | Short-name guard: ≤4 significant chars, or a single all-caps token ≤5, needs a second signal | `enrichment/registry_match.py` |
| D1 | Cross-source gate — no record ships two contradictory identities | `enrichment/consistency.py` |
| D2 | The page read's locality comparator applied to registry matches — against **every** address the registry publishes, and raised as a flag only below exact name tier | `enrichment/locality.py`, `enrichment/registry_match.py`, `enrichment/consistency.py` |
| D3 | Search Term 1's acronym link must be an acronym of the final `name1_enriched` | `enrichment/search_terms.py` |
| — | The gate itself | `tools/run_diff.py`, `tests/test_determinism.py` (61 tests) |

Two flag codes were added and no others: **`source-conflict`** and
**`registry-location-mismatch`**. `tests/test_determinism.py::TestTheFlagVocabularyGrewByExactlyTwo`
pins the whole vocabulary at 14 codes.

---

## 4 · The double-run diff — zero

Two warm runs, then two frozen runs, all on `chemspeed_us_100.xlsx`:

```
$ python tools/run_diff.py logs/determinism/run1.json logs/determinism/run2.json
rows in run 1      : 100
rows in run 2      : 100
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 1
```

```
$ python tools/run_diff.py logs/determinism/frozen3.json logs/determinism/frozen4.json
rows compared      : 100
rows differing     : 0
cell differences   : 0

PASS — the two runs are identical across every enrichment column.

run 2 network calls: 0
```

| Run | Frozen | Network calls | Cache hits | Wall clock |
|---|---|---|---|---|
| cold (cache empty) | no | 536 | 39 | 5 550 s |
| run 1 | no | 1 | 1 081 | 1.8 s |
| run 2 | no | 1 | 1 081 | 1.8 s |
| frozen 3 | **yes** | **0** | 1 081 | 1.9 s |
| frozen 4 | **yes** | **0** | 1 081 | 1.8 s |

**The one remaining call, named.** A warm unfrozen run still makes exactly one
request:

```
"step": "evidence-unavailable-frozen", "namespace": "ror",
"key": "https://api.ror.org/v2/organizations?filter=locations.geonames_details.country_code:us&query=20/15 visioneers"
```

ROR answers that query with **HTTP 500**, and an error is deliberately never
recorded — a bad afternoon must not become permanent evidence, the same rule
that stops a dropped TLS handshake being recorded as "this organisation has no
web presence". So it is re-attempted every run, and under `CACHE_FROZEN` it is
not attempted at all and is recorded as an unavailability instead. **The warm
and frozen runs are byte-identical** (`run_diff run2 frozen4` → 0 differences),
because a 500 and a frozen miss both produce the same clean ROR miss.

`evidence_network_calls == 0` on the frozen pair is the gate's precondition
met exactly; on the warm pair it is met but for that one un-recordable error,
which is reported rather than papered over.

---

## 4a · "Did you just cache it?" — the two experiments that answer it

A zero-diff between two runs against one frozen cache **cannot distinguish a
reproducible pipeline from a replay**. That is a fair objection and it is
testable, so it was tested. The answer is: partly cached, partly genuinely
fixed, and the two are separable by experiment.

### Experiment 1 — invert the order of every recorded candidate list

`tools/shuffle_evidence.py` builds a second cache whose **content is identical
and whose order is inverted**: the same SERP results, the same ROR `items`, the
same GLEIF `data`, the same Wikidata `search` hits — every list reversed,
nothing added, removed or edited (verified: same multiset, different sequence).
That is exactly the perturbation a live API makes between two runs, and it is
what made two runs disagree before Fix C.

```bash
python tools/shuffle_evidence.py tests/fixtures _cache_shuffled
python scripts/run_batch.py … --json logs/determinism/order_base.json
python scripts/run_batch.py … --cache-dir _cache_shuffled --json logs/determinism/order_rev.json
python tools/run_diff.py logs/determinism/order_base.json logs/determinism/order_rev.json
```

> rows compared 100 · **rows differing 0** · cell differences 0

Both runs made the same **1 090 cache hits** and the same single network call.
Identical hit counts matter as much as the identical output: they mean the
reversed run asked for the *same entries*, so no request and **no prompt**
changed shape when the evidence was reordered — which is Fix A(3) verified from
the outside as well as Fix C.

A replay cannot pass this test. The pipeline is genuinely order-independent.

**And the test found a real bug the double-run diff could not see.** On the
first attempt it reported **1 differing row** — AkzoNobel, which resolved to
`5493005O6OYDDANSQG95` on one ordering and to no LEI at all on the other. Cause:
`_fuzzy_lookup` took `completions[:5]` **in the order GLEIF returned them**, so
reversing the list handed it a different five candidates to resolve. Same class
of defect as ROR's `items[:10]`, which had already been removed; this one was
missed because it is a *truncation*, not a sort, and a truncation is still a
selection. ROR's cap could simply be dropped (local scoring is free); GLEIF's is
a real call budget, so it is now taken as the five smallest **LEIs** rather than
the first five returned. Pinned by
`TestTruncationIsNotAHidingPlace::test_gleif_resolves_the_same_five_completions_either_way`.
With that fixed, the experiment reports zero.

### Experiment 2 — freeze every source EXCEPT the model

Two runs against two caches that hold every recorded page, SERP result, registry
response and Wikidata item, and **no `llm/` namespace** — so the model answers
live in both, with `temperature=0`, `top_p=1` and an accepted `seed`.

> 239 and 237 live model calls · rows compared 100 · **rows differing 6** ·
> cell differences 22

| Record | What moved |
|---|---|
| 21st Century Biochemicals | Tier 3 wrote Name 1 on one run, not the other |
| Akemi Capital | Tier 3 wrote on one run; `low-confidence-unchanged` on the other |
| **Aldrich APL** | the person classifier said *organisation* on one run and *person* on the other — Name 1 emptied into Contact, Search Term 1 lost, `domain-unverified` became `person-unresolved` |
| Allchemy Inc | Tier 3 inferred "Allchemy, Inc." on one run, nothing on the other |
| Allied Automation | Tier 3 wrote on one run, not the other |
| AmeriQual Foods | company-canonical answered on one run, Tier 3 on the other |

**All six are model decisions. None is a selection, keying or gate difference.**
That is the clean attribution: with the evidence held identical and the
pipeline fixed, everything that still moves comes out of the model.

### What that means

| Claim | Status |
|---|---|
| The pipeline is a deterministic function of its evidence | **Yes** — proven by inverting the evidence order (0 rows) |
| Prompts do not depend on evidence order | **Yes** — 0 LLM cache misses under inversion |
| Candidate selection no longer depends on arrival order | **Yes** — and the test found the last violation |
| The *model* is reproducible | **No.** 6 of 100 rows still move with it live |
| The *web and registries* are reproducible | **No, and they cannot be** — you cannot re-derive what a site said in August |
| A re-run of a recorded batch is reproducible | **Yes** — 0 rows, 0 network calls |

So the caching is not a trick for the web and the registries: freezing is the
only correct answer there, and `PAGE_FIXTURE_DIR` had already made that argument
for page reads before this work. For the **model** the cache is genuinely
masking residual nondeterminism rather than eliminating it, and that is stated
as such: "reproducible" for the LLM tiers means *reproducible against a recorded
model*, not *the model is deterministic*. The honest headline is that the
pipeline's own contribution to the noise floor is now **zero**, and the model's
is **6 of 100**, recorded rather than re-asked.

---

## 5 · Where determinism actually had to come from

The most useful result of this work is a negative one.

**`temperature=0` is not determinism.** It selects the arg-max token; a tie
between two equally-likely tokens is still broken server-side, and batching and
MoE routing perturb the logits between requests.

**Nor is `temperature=0` + `top_p=1` + `seed`.** The deployment
(`MDM-Apoorva-gpt-5.4`, model `gpt-5.4-2026-03-05`) *accepts* `seed` on every
API version probed — `2024-08-01-preview`, `2024-10-21`, `2025-01-01-preview`,
`2025-04-01-preview` — and returns **no `system_fingerprint`**, so a backend
change cannot even be detected. With all three parameters sent and accepted,
two warm runs of this batch still differed on **10 of 100 rows**, every one an
LLM decision (`Name 1 Provenance` flipping between `input:1:rule` and
`llm_tier3:3:self_medium`; `low-confidence-unchanged` appearing and
disappearing). Re-measured cleanly after the rest of the work landed — every
non-model source frozen, the model live — it is **6 of 100** (§4a, Experiment
2).

So the last of it could not be closed from the client side, and the answer was
not to try harder at the request — it was to stop treating the model
differently from every other source. **An LLM answer is evidence the pipeline
reads**, exactly as a page read or a SERP result is, and `PAGE_FIXTURE_DIR`
already made the argument: a claim about what a source said on a day has to be
recorded, or a re-run re-litigates it. `OpenAIClient.extract_json` now records
its parsed response under a SHA-256 of the deployment, the API version, all
three sampling parameters, `max_tokens` and **both prompts verbatim**. Editing
a prompt template invalidates every entry that used it, which is correct: the
recorded answer answered a different question.

That closed the last 10 rows, and it is what took the batch from 5 550 s to
1.8 s.

### A related correction: evidence, not conclusions

The registry namespace originally recorded the **result dict** the client had
already computed. That cached the *code's conclusion*, not the registry's
answer — and a change to the selection rules then had no effect on a warm
cache, which is the opposite of what an evaluation freeze is for. Freezing
`dedup/weights.json` fixes the *inputs* so a change to the *logic* can be
measured against them. `cached_registry_get` now records the raw HTTP body,
keyed on URL + query parameters, and every guard, score and tiebreak is
re-decided on every run. Pinned by
`TestTheCacheHoldsEvidenceNotConclusions::test_a_registry_response_is_recorded_and_re_decided`.

---

## 6 · The named cases

**The three named rows are not in this repository's batch.**
`docs/thesis/chemspeed_us_100.xlsx` runs from "1st Source Research, Inc." to
"ATC Automation" — 100 alphabetically-consecutive US records. ATI Trading
(Elizabeth NJ), BIC Corp (Milford CT) and BHS (Nampa ID) do not appear in it,
in `logs/runs/E_final.json` or in `logs/runs/F_final.json`. The two diffed runs
that produced them were made against a larger Chemspeed extract that is not
here.

They are therefore asserted as **fixtures**, spelled as reported, rather than
as assertions on this batch's output. Each is a test that fails if the
behaviour regresses:

| Case | Required outcome | Test |
|---|---|---|
| **ATI Trading** rejects `american-trading.com` with the location-contradiction reason present | domain withdrawn; note names "American Trading International, Inc." **and Torrance** **and** "different state or country"; the reason survives into `flag_reason`; and the withdrawn domain never promotes Name 1 to `…:verified` | `TestATITradingRejectsTheWrongOwnerDomain` (3 tests) |
| **BIC Corp** carries the GLEIF identity only, `source-conflict` flagged | `lei_id` kept, `ror_id` **and** the ROR-supplied domain nulled, `source-conflict` in `flag_codes`, reason naming **both** "BIC CORPORATION" and "Centene Corporation" | `TestNoRecordShipsTwoIdentities::test_bic_keeps_gleif_nulls_ror_and_flags_the_conflict` |
| **BIC Corp** Search Term from the GLEIF name | `search_term_1` starts "BIC" and contains no trace of the ROR candidate | `…::test_the_bic_search_term_comes_from_the_surviving_identity` |
| **BHS** resolves to no-match | two plausible expansions, both wrong-city → `matched is False`, `ror_id is None` — neither, not the first and not the higher score | `TestTheShortNameGuard::test_bhs_resolves_to_neither_expansion` |

Two counter-tests guard against over-refusal: a short name **is** accepted when
the registry's city agrees (`test_a_short_name_IS_accepted_when_the_city_agrees`)
or when the candidate's own website is the record's domain
(`test_a_short_name_is_accepted_when_the_domain_agrees`), and two agreeing
sources are left alone (`test_two_agreeing_sources_are_left_alone`).

The batch does contain the *shapes*. `3M (Detroit)` is a two-character name
whose GLEIF match (3M COMPANY, registered Saint Paul MN) contradicts the
record's city — refused, exactly as BHS is. `ABB Inc`, `AOC LLC`, `ALZA
Corporation` and `Apex 974 LLC` are the same rule. `AB Controls, Inc.` is the
ATI shape.

---

## 7 · The unauthorised `no-match` code

**Origin: commit `5e423c2`, "Fix 8: flag model redesign", 2026-08-20, author
Suzu.** That commit introduced the whole machine-readable flag vocabulary —
`git log -S 'NO_MATCH = "no-match"' -- enrichment/flags.py` returns exactly one
commit, and `git log -S '"no-match"' --all` returns the same one. It is not a
later accretion: `no-match` was one of the eleven codes the redesign shipped
with, alongside `low-confidence-unchanged`, `overflow`, `opaque-code` and the
rest.

Its rule (`enrichment/flags.py`) is a total miss: raised only when **no other
code fired** and `_nothing_was_enriched(result)` — no registry identifier, no
domain, no evidence URL, no `operating_name`, no changed field, and the
unchanged-Name-1 state not `confirmed`/`verified`.

**Behaviour left exactly as found.** It appears on 4 of 100 records in the
current run (2 and 1 in the two baseline runs). Its fate is yours.

---

## 8 · Behavioural delta, honestly

The two baselines and the current runs are on the same 100 records, but **not
on the same evidence** — months apart, against a live web and live registries.
The counts are comparable in shape, not row by row.

| | E_final | F_final | now |
|---|---|---|---|
| ROR ids | 15 | 15 | **11** |
| LEI ids | 23 | 24 | **19** |
| Domains | 60 | 60 | **54** |
| Flagged for review | 32 | 34 | **39** |
| `registry-location-mismatch` | — | — | **1** (19 before §8.1) |
| `low-confidence-unchanged` | 18 | 18 | 20 |
| `domain-unverified` | 15 | 16 | 21 |
| `no-match` | 2 | 1 | 4 |
| `unverified-inference` | 1 | 3 | 3 |

**Fewer identifiers is the intended direction** — every fix here refuses more
and accepts nothing new. The refusals are individually attributable in
`provenance_rejected`:

- `short_name_uncorroborated` — `3M (Detroit)`, `ABB Inc`, `ALZA Corporation`, `Apex 974 LLC`. Every one is a ≤4-character or acronym name whose registry match placed it in another city. (`AOC LLC` was on this list until §8.1: GLEIF's *headquarters* address is Collierville TN, the record's own city, so the second signal Fix C(3) asks for is there and always was — the check had only been shown the Delaware legal address.)
- `registry_ambiguity` — `1910 Genetics` (Baylor Genetics vs Myriad Genetics), `Aldrich APL` (APL vs Appleton Public Library), `ATC Automation` (ATC vs Anoka Technical College). All three pairs are wrong for the record; refusing both is the correct answer, not a loss.

**Three things to weigh, none of which this work decided for you:**

1. ~~**`registry-location-mismatch` fires on 19 of the 25 records that hold a
   registry identifier.**~~ **Answered in §8.1** — the trigger was wrong, not
   the volume. It now fires once.

2. **`3M (Detroit)` losing 3M Company's LEI** is the short-name guard and the
   locality check combining exactly as specified. It is also, on the facts, a
   3M site in Detroit that would have been correct to link. The rule is right
   for BHS and costly here; whether ≤4 characters is the right cut is a tuning
   question the batch can now answer, because the batch is reproducible.

3. **`AB Controls, Inc.` lost `ab-controls.com`** between the baseline and now —
   **not a code regression.** The SERP for that query returns only directory
   sites today (zoominfo, yelp, thomasnet, dnb); the company's own domain is not
   in the result set. That is the live web moving under a months-old baseline,
   and it is precisely the confound that Fix B's frozen cache exists to remove
   from future comparisons.

Two false-signal bugs in the new code were found by running it and fixed:

- The registry locality check reported **six ROR matches as contradicting their own record**, because ROR states `country_name` ("United States") and the SAP record carries the ISO code ("US"). `normalise_country` now folds them; pinned by `TestTheLocalityComparatorDoesNotInventDisagreements`.
- The reason text rendered `"states region DE"`, which on a US batch reads as Germany and means Delaware. It now renders `DE (Delaware)`.

---

---

## 8.1 · The corrected `registry-location-mismatch` trigger

§8 left the flag firing on 19 of the 25 records that carry a registry
identifier, and asked whether a Delaware carve-out was the answer. It was not:
a carve-out would have hard-coded one country's corporate-registration habit
into a comparator that is supposed to be about evidence. Two things were
actually wrong, and both are answered without naming a state.

### Half the flag was reading the wrong address

GLEIF publishes two addresses per entity — `Entity.LegalAddress`, where the
entity is *incorporated*, and `Entity.HeadquartersAddress`, where it *is*. The
check read only the first. So it compared US records against a registered-agent
address in Wilmington and reported the difference as a finding:

| Record | GLEIF legal | GLEIF headquarters | Record |
|---|---|---|---|
| AdvanSix Inc. | Wilmington **DE** | Parsippany **NJ** | **NJ** |
| Air Products and Chemicals | Wilmington **DE** | Allentown **PA** | **PA** |
| Albany International Corp. | Wilmington **DE** | Rochester **NH** | **NH** |
| Altria Group, Inc. | Suffolk **VA** | Richmond **VA** | Richmond **VA** |

In every row the registry agrees with the record, on the address it publishes
for exactly that purpose. `enrichment.locality.compare_registry_addresses` now
takes the **set** of addresses, and the aggregation is deliberately asymmetric:

* **corroborated** — the record agrees with *any* registered address. The other
  address naming a different place is not evidence against it; the two are not
  competing claims about one place, they are two true statements about one
  entity.
* **contradicted** — the record agrees with *none* and disagrees with at least
  one.
* **neutral** — every address was silent.

ROR publishes one primary location and goes through the same function, so the
two registries cannot drift apart on the rule.

Granularity travels with it. Two cities inside one *agreeing* region are one
organisation's plant and head office (Altria: Suffolk on the legal address,
Richmond on the headquarters, both Virginia). The region agreeing is the
corroboration; the city differing inside it is recorded in the trace and acted
on nowhere. `compare_locality` itself is untouched — the page-read corroborator
asks a different question (one witness, one stated place) and its withdrawal
rule already reads `scope`.

### The other half was firing without asking how the match was made

A contradicted address means two different things depending on what identified
the organisation in the first place, and the flag was treating them alike:

* the record states the registry's name **verbatim** — the entity is identified
  *by its name*. A disagreeing address is then a fact about the organisation's
  geography, not a doubt about which organisation it is. Arkema Inc. is
  registered in King of Prussia PA and the chemspeed record names its North
  Carolina site; both are true.
* the match was reached any weaker way — a fuzzy score, a collision-prone short
  name, or a crosswalk that followed a Wikidata pointer instead of a name.
  There the address is the second opinion on an identification with no anchor,
  and its disagreeing is precisely the doubt the flag exists to raise.

`enrichment.registry_match.name_match_tier` names the four tiers — `exact`,
`fuzzy`, `short_name`, `crosswalk` — and only `exact` suppresses the advisory.
Exactness forgives case, punctuation and a legal form one side omits ("Arkema"
against "ARKEMA INC."), and forgives nothing else: `Smith Inc` and `Smith LLC`
are two legal entities and the register is the authority that says so. A
collision-prone name is never exact however it compares — "BHS" equals "BHS"
and identifies nothing, which is the premise C3 already rests on. A missing
tier is not exact either: silence about the strength of a match is not a claim
that it was strong.

So the trigger is a **conjunction**: contradicted **and** below exact tier.
Where it is exact and contradicted, the match stands, the row carries no flag,
and `registry_location_unconfirmed` goes to the consistency trace with a batch
counter behind it (`consistency.registry_location_unconfirmed_count`). The
observation is kept; it is just not put in front of a reviewer as a doubt.

### What it did to the batch

Same 100 records, same frozen evidence cache, before and after:

| | before | after |
|---|---|---|
| `registry-location-mismatch` | 19 | **1** |
| `registry_location_unconfirmed` (trace only) | — | 7 |
| `registry_location_note` (trace only) | — | 3 |
| Flagged for review | 55 | **39** |
| LEI ids | 18 | **19** |
| ROR ids · Domains · every other flag code | 11 · 54 · unchanged | 11 · 54 · unchanged |

The one survivor is **AstraZeneca → "AstraZeneca Foundation"** (ROR, North
Carolina, against a record in Massachusetts): a fuzzy match to a *different
legal entity* that happens to share a brand, with an address that does not fit.
That is the case the code was written for, and it is now the only thing on the
sheet wearing the code.

Of the eighteen cleared, **eleven** had no contradiction left once the whole
address set was read — eight because the headquarters address agrees with the
record outright, three because the disagreement was a city inside an agreeing
region and the region agreeing is the answer. Those three are the granularity
rule's entire footprint on this batch, and all three are the case it was
written for: AdvanSix's headquarters is in *Parsippany* and the record says
*Parsippany-Troy Hills*; Altria's legal address is *Suffolk* VA and the record
says *Richmond* VA; ROR puts Arvinas in *New Haven* CT and the record says
*East Haven* CT. Each is logged as a `registry_location_note`, so a rule that
forgives still leaves something to audit.

The other **seven** are exact-tier names whose every registered address is an
incorporation or head-office address elsewhere — including Allergan, Inc.,
where GLEIF states Delaware on the legal address and New Jersey on the
headquarters and the record says California. Both contradict, the name is
stated verbatim, and the row is unflagged with the trace counter incremented.

Three of the eighteen took a second correction to get right: ROR appends a
bracketed disambiguator to its display name ("Sekisui XenoTech **(United
States)**", "Abcam **(United States)**"), the rest of the pipeline strips it as
ROR keyspace rather than as part of the name, and the tier check had not been.
Left in, it reported three verbatim matches as fuzzy.

One row moved beyond the flags. **`AOC LLC`** now carries GLEIF's LEI. Nothing
about the short-name guard changed — `AOC` is still collision-prone and still
requires the second signal C3 demands. The signal is simply there: GLEIF's
headquarters address is Collierville TN, which is the record's own city. The
check had only ever been shown the Delaware legal address.

The crosswalk lane gained the check it never had: `call_ror_by_id` compared no
locality at all, which left the one route that picks an organisation *without
reading its name* as the one route whose address was never checked. It now
compares, and at `crosswalk` tier — so a stale pointer with a wrong address is
flagged rather than silently accepted. No row in this batch is affected; the
single Wikidata→GLEIF crosswalk hit is unchanged.

Pinned by `tests/test_determinism.py::TestTheRegistryLocationTriggerIsAConjunction`
(14 tests), including the Aurora pair — one record in Aurora **Colorado**,
ROR answering with an organisation in Aurora **Illinois**, flagged when ROR's
name is "Aurora University Foundation" and unflagged when it is the "Aurora
University" the record states, with everything else held identical.

---

## 9 · Test suite

`pytest -q` → **2 009 passed, 5 failed**, of which **all 5 pre-date this work**.
Verified by stashing every change and re-running at `HEAD`, where the same five
fail identically:

| Test | Failure |
|---|---|
| `test_orchestrator.py::test_tier1_full_resolution` | `assert 'medium' == 'high'` (confidence on a ROR match) |
| `test_orchestrator.py::test_web_search_fallback_for_name1` | pre-existing |
| `test_orchestrator.py::test_web_search_determines_record_type` | pre-existing |
| `test_name_slot_parity.py::test_department_in_a_lower_slot_is_not_reported_missing` | `G2-NAME-012` reported when it should not be |
| `test_routes.py::test_issues_compare_segments_g6_and_g7_out_of_the_metric` | pre-existing |

`tests/test_determinism.py` adds **61 tests** across the four fixes. Existing
tests were edited in four places, each because the behaviour they pinned
changed by design:

- `test_tier1_lei.py` — `_best_verified_candidate` returns a third value (the refusal reason).
- `test_tier1_ror_country.py` — "BASF" is a four-character name, so the two tests that expect an accept now supply the registered city that corroborates it; the country guard is still what they test.
- eight test-local ROR/LEI stubs — accept the new `city` / `state` / `record_domain` record context.
- `conftest.py` — an autouse fixture resets the process-global evidence cache between tests, and the writable namespaces are rooted in a temp directory. Without it the suite would write into `tests/fixtures/page_reads/` (as it always did, harmlessly while entries were overwritable) and, now that entries are immutable, the first test to record `acme.com` would pin that payload for every later test.

---

## 10 · One paragraph, thesis-ready

Two executions of an identical pipeline over an identical 100-record batch
produced seven substantively different records — four of them differing in the
customer's name itself — and, on a larger extract of the same source, two
silent wrong-entity acceptances. That makes every downstream measurement in
this thesis unreadable, because a 7% noise floor is larger than most of the
effects being measured. Diagnosis found nondeterminism at three levels and a
design gap that the nondeterminism merely exposed: sampling parameters left at
service defaults on calls that gate field writes; a cache that did not survive
the process, so a "re-run" re-gathered most of its evidence from a web and
registries that had moved; candidate selection that broke every score tie by
the order an API happened to answer in; and no comparison anywhere between what
two sources said an organisation was called, so one record could ship GLEIF's
LEI beside a different company's ROR identifier with every individual guard
satisfied. Fixing them produced three design rules that generalise beyond this
pipeline. **First, determinism has to reach the inputs before it is worth
anything at the decision points** — and that includes the model: with
`temperature=0`, `top_p=1` and an accepted `seed`, ten of a hundred rows still
moved, because a seed is a best-effort request and this deployment returns no
`system_fingerprint`, so the only reliable answer was to treat an LLM answer as
what it is, evidence to be recorded like a page read. **Second, ordering a tie
deterministically only makes the wrong answer reproducible**; where the
evidence genuinely does not distinguish two candidates the honest output is no
match, which is why a near-tie and a name too short to identify an entity now
resolve to neither rather than to whichever the registry listed first.
**Third, a cache used as an evaluation control must freeze evidence, not
conclusions** — an early version recorded the finished match decision, which
would have frozen the code under test along with its inputs and made the
experiment unable to measure the thing it existed to measure. With all three
applied, two warm runs of the batch are identical across every one of the 67
output columns, the second makes zero network calls, and the batch runs in 1.8
seconds instead of 92 minutes — so the reproducibility harness that made the
defect visible is also what makes every subsequent experiment affordable. That
last result is not self-certifying, because a zero-diff against one frozen
recording cannot distinguish a reproducible pipeline from a replay; the
distinguishing experiment is to re-run against the *same* evidence in the
*opposite* order, which leaves the output bit-identical and which caught the
final order-dependent truncation that the double-run diff could not see. The
residual is then attributable and bounded: with every other source frozen and
the model live, six of a hundred records still move, and all six are model
decisions — so the pipeline's own contribution to the noise floor is zero, and
the model's is recorded rather than re-asked.
