# 14 — Tier 2B (department web search) is dead code. Revive it, or delete it honestly.

Type: grilling
Status: decided — DELETE; implementation not started
Blocked by: —

## The finding (verified against current `main`, 2026-08-29)

`enrichment/tier2b_dept.py::run_tier2b` is **defined and never called.** Verified:

- No import and no call site anywhere in application code. The only non-test references are the
  `def` itself and a prompt-version registration at `llm/prompts.py:631`.
- `tier2_mode == "2B"` is **compared** at `orchestrator.py:6565` and **never assigned** anywhere.
  So `summary.tier2b_count` is structurally always 0 (this is 08_GAPS G-4's "counter that cannot
  increment", still true).
- The README presents Tier 2B as a live tier: the tier strategy table (`README.md:102`), the
  architecture diagram (`:159`), the fall-through description (`:758`) and a dedicated section
  (`:765`).

**How it died.** Commit `635d5ba` ("Update environment variables, enhance page fetching, and
improve enrichment logic") removed the import, the whole `# TIER 2B: DEPT SEARCH` block, and
`_apply_tier2b`. The commit message does not mention removing a tier.

**Why it probably died.** `_apply_tier2b` wrote results the old way:

```python
result["tier2_mode"] = "2B"
result["flag_for_review"] = tier2b.flag_for_review
result["flag_reason"]     = tier2b.flag_reason
result["name2_enriched"]  = tier2b.name2_enriched.strip()
```

Every one of those is now illegal: flags are rebuilt once in `finalise` and never written by a
tier (`flags.py`), and `name2_enriched` is a write-locked scoped field reachable only via
`record.write(field, value, evidence)` (`provenance.py`). So Tier 2B was very likely dropped as
**collateral of the flag/provenance refactor** rather than removed on its merits — nobody migrated
it, so the call went away and the docs were never updated.

## Question

1. **Revive or delete?** If revived, `_apply_tier2b` must be rewritten against the write/flag
   authorities — it cannot be restored as it was.
2. **If revived, where does it sit** now that the lane order has changed (`grounded_resolver`,
   `lab_resolver` and the department probe all post-date it)? Does it still have a job, or has
   `grounded_resolver` absorbed it in practice?
3. **If deleted**, the README's tier table, architecture diagram and Tier 2B section all have to
   go with it, plus `tier2b_count`, the `"2B"` literal in `api/models.py:533`, and the prompt
   registration. A documented tier that does not exist is worse than no tier.
4. **What is the measured Name 2 impact either way?** This must be answered with numbers, not
   argument — see below.

## Why this matters

The user named **Name 2 / department** as a top pain point. In the 500-record
`PresentationTestData_enriched_checked_v1.xlsx`, Name 2 is present on **160 records on input and
165 on output** — the pipeline nets **+5 department values across 500 records** — and 90 records
carry "name2/name3 could not be canonicalised with high confidence, left unchanged". With Tier 2B
dead, the only things that can populate Name 2 are `tier2_canonical` (LLM, no web) and Tier 2A
(needs a contact). That is a coherent explanation for the observed near-zero department enrichment.

**Caveat:** that workbook predates the current pipeline (it has no provenance columns, no
`Flag Codes`/`Flagged Fields`, and old-style tier-narrating flag reasons). The +5 figure is
indicative, not current. Ticket 11/03 must confirm it on today's code.

---

## Findings

Investigated 2026-08-29. Every claim below is backed by an executed probe, a test run, or a
grep with output. Probe scripts: `.scratch/agentic-enrichment/tmp/probe_addressable.py`,
`probe_lanes.py`, `probe_grounded_name2.py`, `probe_reach.py`, `probe_expand.py`,
`probe_live21.py`.

> ### CORRECTION - sections 1 and 3 were first measured on degraded search. Re-measured live.
>
> The first pass ran with `MOCK_EXTERNAL_CALLS=true`, and the shared evidence cache at
> `tests/fixtures/serp/` had been filled while `.env` carried `SERPAPI_KEY` twice - the real key at
> line 28 and the `.env.example` placeholder at line 88. python-dotenv keeps the last occurrence, so
> the placeholder won and every search fell through to DuckDuckGo. Measured:
> **all 251 cached SERP entries hold an empty payload (251/251, 100%)**, and the cache key is
> `serp:<country>:q:<query>` with **no provider component** - so a live run would have been served
> those empty results as "this organisation has no web presence".
>
> Section 3 has been **replaced** by a live re-measurement (S3-LIVE below). Its earlier conclusion -
> *"they reach the lane but the grounded LLM does not propose"* - **is refuted**: on live SerpAPI the
> model proposes on 7 of 21, and four records end at `ror:verified` with a real ROR identifier.
> Section 1 (the structural finding) is **re-confirmed on live evidence**, unchanged.
> Section 2 (the 21/200 addressable population) was measured on static files and is **unaffected**.
>
> The decision is unchanged - **DELETE** - but it now rests on a stronger result, and the follow-up
> gap list has grown from two items to four. See the Decision section.

### 0. The dead-code finding re-verified — and two residues the ticket missed

`grep -rn "run_tier2b"` over the tree: the `def` (`enrichment/tier2b_dept.py:48`), `tests/test_tier2b.py`
(4 tests, **all passing** — `pytest tests/test_tier2b.py -q` gives `4 passed`), and docs. No production
call site. `tier2_mode` is assigned in exactly one place, `orchestrator.py:2225`
(`result["tier2_mode"] = tier2a.mode`), so `"2B"` is never assigned and `summary.tier2b_count`
(`api/models.py:819`) is structurally always 0. Confirmed.

Two residues the ticket does not list:

- **`TIER2B_PROMPT_VERSION` is a dead import.** `enrichment/orchestrator.py:178` imports it;
  `src.count("TIER2B_PROMPT_VERSION") == 1` in that file — the import and nothing else.
- **`tests/test_flags.py:257` passes `tier2_mode="2B"` into a flags test, and it is inert.**
  `grep -n tier2_mode enrichment/flags.py enrichment/confidence.py` returns no hits. Nothing in the
  flag authority has read `tier2_mode` since the refactor.

### 1. `grounded_resolver` HAS absorbed Tier 2B's job — measured, not argued

**The ticket's premise is refuted.** "With Tier 2B dead, the only things that can populate Name 2 are
`tier2_canonical` (LLM, no web) and Tier 2A (needs a contact)" is **false**. There is a third writer,
and it is web-backed.

`_apply_grounded` (`orchestrator.py:3582-3612`) iterates `(grounded.name1, grounded.name2)` and
`_write`s each into `f"{field}_enriched"`. Executed end-to-end through the real `Orchestrator` with a
SERP stub, a page stub and an LLM stub that returns a department read off that page
(`probe_grounded_name2.py`), on three records drawn from the addressable population:

```
G_NAVY  name2_enriched='Weapons Division'  (repacked to 'Division of Weapons')
   name2_provenance='llm:provisional'  source=SERP+LLM  tier=3
   name2 writes: [('Weapons Division', 'grounded:name2-canonical', ('serp','fetch','llm_grounded'))]
   flag_codes=['unverified-inference','domain-unverified']  flagged_fields=['name1','name2','domain']
G_DPD   name2_enriched='Division of Forensic Services'   prov='llm:provisional' src=SERP+LLM
   name2 writes: [('Forensic Services Division','grounded:name2-canonical',('serp','fetch','llm_grounded'))]
G_JEOL  name2_enriched='Technology Center'               prov='llm:provisional' src=SERP+LLM
   name2 writes: [('Technology Center','grounded:name2-canonical',('serp','fetch','llm_grounded'))]
```

That is Tier 2B's exact shape — SERP, page fetch, LLM extraction from **structured page elements
only** (`EvidenceItem.render` deliberately withholds body text, the same decision D-4 that shaped
Tier 2B's prompt), same fan-out constants (`NUM_RESULTS = 5`, `MAX_FETCHES = 3` against Tier 2B's
`num_results=5`, `candidates[:3]`) — with **three capabilities Tier 2B never had**:

1. **Registry re-verification.** `_re_verify` takes the model's proposed unit name back to ROR/GLEIF;
   a hit is written as the registry's official name (`grounded:registry-reverified-canonical`) and,
   for a Name 2 that is its own registered body, records `name2_registry_id`.
2. **`name2_kind` classification.** `WRITEABLE_KINDS = {department, sub_entity}`,
   `CLEARING_KINDS = {alias_of_name1, noise}`, plus a `person` no-op. Tier 2B could not tell a
   department from Name-1 overflow — it would have "resolved" one.
3. **Scheme B compliance by construction** — writes through `_write` / `llm_evidence`, flags rebuilt
   in `finalise`, `llm` capped at `provisional`.

Complete list of lanes that can write `name2_enriched` today, from
`grep -rn name2_enriched enrichment/ api/ utils/ llm/`, with the precondition each needs:

| lane | call site | precondition | evidence attached |
|---|---|---|---|
| Tier 2A (contact) | `orchestrator.py:2245` | a contact **and** an official domain | `("serp","fetch","llm_tier2a")`, `tier2a:<mode>` |
| person-affiliation | `:3515` | Name 2 blank, contact-only record, ROR-confirmed employer | `("serp","fetch","llm_tier2a")`, `person-affiliation:department` |
| **grounded (name2)** | **`:3596` via `_apply_grounded`** | **reaches Stage 4; SERP non-empty; at least one page fetched; LLM returns `name2_canonical`; kind in WRITEABLE_KINDS or absent; not address-like** | **`("serp","fetch","llm_grounded")`, `grounded:name2-canonical`** |
| grounded (clear) | `:3667` | kind in `{alias_of_name1, noise}` | `grounded:name2-<kind>-cleared` |
| grounded (registry) | `_write_registry_name` from `:3616` | the above, plus a ROR/GLEIF re-verification hit | `grounded:registry-reverified-canonical` |
| lab resolver (UC 13) | `:6041` | Name 2 is a lab/group and a parent department is extracted | `("serp","fetch","llm_lab_parent")`, `uc13:parent-department-from-lab-page` |
| tier2_canonical | `:6203` | LLM-only, no web; rejects granular units | `("llm_canonical",)`, `uc5:tier2-canonical` |

**Re-confirmed on live evidence (2026-08-29).** The block above was first produced with stubbed
SERP/page/LLM clients. `probe_live21.py` re-ran it against live SerpAPI, live `gpt-5.4` and live
ROR/GLEIF, and the structural finding holds unchanged - grounded is a genuine third, web-backed writer
of `name2_enriched`. Live write records, taken verbatim from the run:

```
S3_17  writes: [('Supply Chain Management Department', 'grounded:name2-canonical',
                 ('serp','fetch','llm_grounded'))]                      -> llm:provisional
S3_20  writes: [('Ames Research Center', 'grounded:name2-canonical', ('serp','fetch','llm_grounded')),
                ('Ames Research Center', 'grounded:registry-reverified-canonical', ('ror',))]
                                                                        -> ror:verified
S2_04  writes: [(None, 'grounded:name2-noise-cleared', ('pipeline',))]   -> slot cleared
```

All three grounded write paths named in the table above fire on live data: the canonical write, the
registry-re-verified write, and the clearing write. Nothing about the structural claim depended on the
search provider.

**Two gaps grounded genuinely has, both far smaller than a tier:**

- **No `site:` bias.** `build_query(name1, name2, city, state)` never receives `domain`;
  `grep -n domain enrichment/grounded_resolver.py` shows it only at `:360/406/414/479/619`, all inside
  `_re_verify`. Observed query: `"Naval Air Warfare Center" Weapons Div China Lake CA` — open web, no
  domain restriction. `site:{domain}` was Tier 2B's one distinguishing feature.
- **Name 2 proposals skip the identity guard.** The guard reads
  `if field == "name1" and not canonical_preserves_identity(...)` (`grounded_resolver.py:~592`), so a
  Name 2 proposal is never identity-checked. Observed live: the model proposed
  `"Forensic Services Division"` against an input of `"Forensic Science Div"` and it was written
  unchallenged. Not a Tier 2B question, but it deserves its own ticket.

### 2. The addressable population — measured

Both current-schema labelled files, 200 records, classified with **the pipeline's own predicates**
(`has_no_canonical_form`, `is_unit_construction`, `is_granular_unit` — not by eye):

| | S2 corporate | S3 gov labs | total |
|---|---|---|---|
| Name 2 blank on output | 50 | 38 | 88 |
| Name 2 written by a lane (`llm` 28 / `ror` 2) | 7 | 23 | 30 |
| **Name 2 unchanged input (`input:low`)** | **43** | **39** | **82** |
| — excluded: `has_no_canonical_form` (admin desk / names nothing) | 22 | 4 | 26 |
| — excluded: not a unit construction (Name-1 overflow, site, DBA, legal suffix) | 15 | 20 | 35 |
| — **unit-shaped and unresolved = ADDRESSABLE** | **6** | **15** | **21** |

**The addressable population is 21 / 200 = 10.5%.** 19 of the 21 have a domain; **0 of the 21 have a
contact**, so Tier 2A is ineligible on every one — this is exactly the population the ticket named.

Of the 82 unchanged Name 2 values, **61 (74%) are not a department-resolution problem at all**: 26 are
back-office desks or phrases that name nothing (`Accounts Payable` x17, `REF#`, `Email To:`), and 35
are a *different* defect — Name-1 overflow that Stage 0 missed (`Engineering Solutions of Sandia,` x6,
`and Human Services` x2, `Washington, Inc.`, `Engineering Co`), site/address values, or DBA strings.
A revived Tier 2B would not have touched any of them.

### 3-LIVE. Re-measured on live SerpAPI + live Azure OpenAI (supersedes the mock section 3)

`probe_live21.py` replays the same 21 addressable rows through the **real** `Orchestrator` with
`MOCK_EXTERNAL_CALLS=false`, live SerpAPI (`serpapi_key` len 64), live `MDM-Apoorva-gpt-5.4`, and live
ROR/GLEIF. The warm `registry/`, `page_reads/` and `wikidata/` caches were copied into an isolated
store; **`serp/` and `fetch/` were left empty on purpose** so no DuckDuckGo-era entry could be reused.

```
reached the grounded lane : 21/21
degraded                  : 3/21  {'serp_empty': 3}
GOT A GROUNDED name2 WRITE: 8/21
final name2 provenance    : {'input:low': 13, 'llm:provisional': 3, 'ror:verified': 4, '(none)': 1}
```

A second run with `CACHE_FROZEN=true` reproduced this **identical per record with zero new network
calls** (`diff` of both logs on record id / reached / OUT / writes: no differences), so the numbers are
replayable rather than a single sample of a live index.

The 8 writes are 7 `grounded:name2-canonical` plus 1 `grounded:name2-noise-cleared`. Graded by what
actually shipped, not by counting writes:

| rec | Name 2 in | grounded proposed | shipped | provenance | verdict |
|---|---|---|---|---|---|
| S3_17 | `Department of Supply Chain Mgmt` | `Supply Chain Management Department` | `Department of Supply Chain Management` | `llm:provisional` | **clean win** |
| S3_18 | `Carl R. Darnall Army Medical Center` | same, ROR re-verified | same | **`ror:verified`** `ror.org/02zda6x08` | **clean win** (provenance upgrade) |
| S3_20 | `Ames Research Center` | same, ROR re-verified | same | **`ror:verified`** `ror.org/02acart68` | **clean win** |
| S3_21 | `Ames Research Center` | same, ROR re-verified | same | **`ror:verified`** `ror.org/02acart68` | **clean win** |
| S3_15 | `Weapons Div` | `Weapons Division` -> ROR `Naval Air Warfare Center Weapons Division` | `Division of Naval Air Warfare Center Weapons` | `ror:verified` `ror.org/03cap2a49` | correct entity, **mangled downstream by the repack** |
| S3_16 | `Forensic Science Div` | `Forensic Services Laboratory` | `Forensic Services Laboratory` | `llm:provisional` | **unverified type change** (Division -> Laboratory) |
| S2_02 | `Baytown Refinery Laboratory` | `Baytown Refinery` | `Baytown Refinery` | `llm:provisional` | **regression** - "Laboratory" dropped |
| S2_04 | `Center for 3M` | classified `noise`, slot cleared | `Center for 3M` | `input:low` | clear undone by a later passthrough |

**4 clean wins, 1 correct-but-mangled, 1 unverified type change, 1 regression, 1 no-op.**

Two things this settles that the mock run could not:

1. **Four records now carry a registry-verified Name 2 with a real ROR identifier.** Tier 2B was
   *architecturally incapable* of this outcome - it had no registry re-verification step at all. The
   best it could ever return was `confidence="medium"` off an on-domain page.
2. **The residual 13 `input:low` are not "no lane covers this".** Three degraded `serp_empty`, and all
   three are records whose *Name 1* is SAP junk (`Vamc Temple Visn 17`, `Vamc Iron Mountain Visn12`,
   `Vamc Martinez Visn 21`) - live SerpAPI genuinely returns nothing for those, and Tier 2B, which
   built its query from the same Name 1, would have returned nothing either. The rest are the model
   declining to propose on evidence it did receive.

So the population is not uncovered, and it is no longer even unserved: the lane that covers it is
**actively resolving a third of it**, including to registry-verified identifiers.

### 4. A cheaper partial fix, for the record

`expand_abbreviations` — already in `utils/text_utils.py`, deterministic, zero cost — rewrites
**8 of the 21** addressable values on its own (`Weapons Div` to `Weapons Division`,
`Forensic Science Div` to `Forensic Science Division`, `Tech Ctr` to `Technology Center`,
`Baytown Refinery Lab` to `Baytown Refinery Laboratory`, `Orange County Water Lab` to `... Laboratory`
x2, `Veterans Admin MED CTR` to `Veterans Admin Medical Center`, `Vet CTR` to `Vet Center`). Whether
the department slot should be expanded deterministically before any web lane runs is a separate and
much cheaper ticket than either branch of this one.

---

## Decision

**DELETE.** `enrichment/tier2b_dept.py` goes, and every artefact that documents it as a live tier goes
with it. **The live re-measurement strengthens this rather than changing it.**

The evidence that decides it, in one line: **on live search, 21/21 of the addressable population
reaches `grounded_resolver`, and 8/21 get a `name2` written by it - four of them landing at
`ror:verified` with a real ROR identifier, an outcome Tier 2B could not produce at all** because it
had no registry re-verification step. The ticket's premise (that only `tier2_canonical` and Tier 2A
can populate Name 2) is refuted by executed probe: grounded is a third, web-backed,
registry-re-verifying writer, it is already positioned in front of 100% of the records Tier 2B would
have served, and it is already resolving a third of them.

The measured addressable population is **21 / 200 records (10.5%)** - and it is *not* the gap. 74% of
unresolved Name 2 values are admin desks, phrases naming nothing, or Name-1 overflow, none of which
Tier 2B addressed.

**What the live run changes about the earlier reasoning, stated plainly rather than reconciled:** the
first write-up concluded that the grounded lane reaches these records but its model never proposes a
department. That was measured on mock SERP data, against a cache in which 251/251 search results were
empty. It is wrong. The model proposes on 7 of 21 given real search results. The argument for deletion
never depended on the lane failing - it depends on the lane *owning the job*, which it does, and now
demonstrably does well enough to produce registry-verified departments.

### What replaces it - four gaps, all inside lanes that already exist

None is a tier. All are small changes to code that already runs, and each should be its own ticket -
not part of this deletion. Gaps 1 and 2 were predicted from code and are now **confirmed firing on
live evidence**; gaps 3 and 4 are new, and only became visible once the lane actually resolved things.

1. **`build_query` cannot emit a `site:` term - `domain` is not even a parameter.**
   `grounded_resolver.py:277` declares `build_query(name1, name2, city, state)`; the call site at
   `:505` passes four arguments; `domain` reaches the module only inside `_re_verify`. Confirmed on
   the live run: **0 of 21 grounded queries carry a `site:` term** - e.g.
   `"Naval Air Warfare Center" Weapons Div Ridgecrest CA`. This was Tier 2B's one distinguishing
   capability, and recovering it is a signature change plus one `parts.append`. 19 of the 21
   addressable records have a domain. Needs its own before/after measurement.
2. **The identity guard is `name1`-only.** `grounded_resolver.py:588` reads
   `if field == "name1" and not canonical_preserves_identity(...)`, so a Name 2 proposal is never
   identity-checked. Confirmed firing on live data: S3_16 shipped `Forensic Science Div` ->
   `Forensic Services Laboratory` - a changed *unit type* - unchallenged, and S2_02 shipped
   `Baytown Refinery Laboratory` -> `Baytown Refinery`, dropping the unit word entirely. A
   Name-2-scoped guard would have refused both.
3. **The name-block repack mangles a correct registry answer (new).** S3_15's Name 2 was re-verified
   against ROR as `Naval Air Warfare Center Weapons Division` - the right entity, real identifier
   `ror.org/03cap2a49` - and shipped as `Division of Naval Air Warfare Center Weapons`. S3_11 shipped
   `For Medical` with a **null provenance** via `uc0:name-block-repacked`. A registry-verified value
   should not be re-worded after the fact.
4. **A `grounded:name2-*-cleared` decision can be silently undone (new).** S2_04's slot was cleared as
   `noise`, then re-populated by a later `passthrough:input-retained` write. Either the clear is the
   lane's decision or it is not; today it is overwritten downstream.

### Deletion checklist — every line reference verified with `sed -n`/`grep`

Production code and config:

- [ ] `enrichment/tier2b_dept.py` — delete the file (264 lines).
- [ ] `enrichment/orchestrator.py:178` — remove the `TIER2B_PROMPT_VERSION` import (**dead import**:
      one occurrence in the whole file, verified).
- [ ] `enrichment/orchestrator.py:6565-6566` — remove the
      `elif r.tier2_mode == "2B": summary.tier2b_count += 1` branch (verified at those exact lines).
- [ ] `enrichment/provenance.py:287` — docstring example cites `("serp","fetch","llm_tier2b")`;
      re-point at `llm_grounded` or `llm_tier2a`.
- [ ] `api/models.py:533` — drop `"2B"` from the `tier2_mode` `Literal` (verified).
- [ ] `api/models.py:819` — delete `tier2b_count: int = 0` (verified). **This changes the batch-summary
      schema**; README's sample response at `:2164` must change with it.
- [ ] `llm/prompts.py:121-137` — `TIER2B_SYSTEM_PROMPT`, `TIER2B_USER_PROMPT_TEMPLATE`.
- [ ] `llm/prompts.py:630-633` — the `prompt_version("tier2b_dept", "v1", ...)` registration (the
      ticket says `:631`; the call spans 630-633).

README (each verified by `sed -n`):

- [ ] `:102` — the Tier 2B row of the tier strategy table.
- [ ] `:157-161` — the architecture diagram: the `| Tier 2B |` / `| Search |` box and the arrow above
      it (the ticket says `:159`; the box spans three lines).
- [ ] `:758` — "Falls through to existing Tier 2 canonical / Tier 2A / **Tier 2B** / Tier 3".
- [ ] `:765` onwards — the `#### Tier 2B: Department Search` section (file reference at `:767`).
- [ ] `:2164` — `"tier2b_count": 0` in the sample batch summary.
- [ ] `:2624` — the `tier2b_dept.py` entry in the file tree.
- [ ] `:2741` — the `### enrichment/tier2b_dept.py — Department Search` module-reference section.

Tests (all currently passing; deleting them is a **net -4 tests**, not a regression against the
5-failure baseline):

- [ ] `tests/test_tier2b.py` — delete (115 lines, `TestTier2B`, 4 tests, `4 passed` today).
- [ ] `tests/mocks/openai_mock.py:112, :178-204` — the `_mock_tier2b` dispatch branch and method.
- [ ] `tests/test_flags.py:249-257` — remove the inert `tier2_mode="2B"` kwarg (flags has not read
      `tier2_mode` since the refactor; verified by grep). Keep the test and rename it: what it asserts
      (a stated department with a `source_url` is not flagged) is still live behaviour, now via
      `source="dept_search"`.
- [ ] `tests/test_provenance.py:161-187` — two tests use `("serp","fetch","llm_tier2b")` and
      `prompt_version="tier2b_dept/v1:abc"` as a producer-chain example. Re-point at `llm_grounded`;
      the assertions are about the chain mechanism, not about Tier 2B.
- [ ] `tests/fixtures/expected_outcomes.json` — 3 rows carry `"expected_tier2_mode": "2B"`
      (08_GAPS G-18). Only `scripts/test_local.py:115` and the conftest loader consume it; no test
      asserts on it.

Thesis docs (`docs/thesis/`) are dated measurement write-ups, not live spec. G-2, G-8, G-18, G-77, D-1
and open items 19/35 all describe the dead tier and become historically-correct-but-closed once this
lands; record the closure in `08_GAPS.md` / `11_DELTA.md` rather than editing the derivations.

### What is explicitly NOT claimed here

This does not claim Name 2 enrichment is fine. It is not: 82/200 Name 2 values ship as untouched
input, and of the 21 addressable ones the live lane resolves 8, of which only 4 are clean. It claims
the cause is **not** a missing tier. The fix lives inside `grounded_resolver` (the four gaps above,
plus whatever tickets 11/13 find about why the model declines on the remaining records) and in Stage 0
overflow detection (35 of the 82 are Name-1 overflow) and Name-1 quality (3 of the 21 return
`serp_empty` because Name 1 is SAP junk) - not in reviving a
lane that was superseded before anyone noticed it had been unwired.
