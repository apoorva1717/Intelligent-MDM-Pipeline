# Map — Agentic enrichment lane

Label: `wayfinder:map`
Tickets: `.scratch/agentic-enrichment/issues/`

## Destination

Find out **where enrichment actually loses records**, and fix the narrowest thing that recovers
them. A bounded agentic lane and a redesign of the registry gate composition are both *candidates*
underneath this — neither is the assumed answer.

Done when: the loss distribution is measured, the cheapest effective fix is identified and its
effect is known, and any remaining decision is about building rather than about choosing.

> **Redrawn 2026-08-29.** The original destination was "replace Tier 3 with a ReAct lane", which
> presumed the conclusion. Two findings forced the change. (1) Ticket 09: a six-tool loop sits in
> the regime where retrieval collapses abstention and where the planner's prior corrupts the query
> *before* the verifier sees it (memorization ratio 3.7%->99.8% on mixed evidence) — so the
> registry-verifier safety argument is sound but not sufficient. (2) The ROR path re-scores ROR's
> own affiliation match with a whole-string rapidfuzz ratio at the same 0.8 threshold, in a purely
> conjunctive gate with no rescue path — which rejects exactly the abbreviation cases ROR gets
> right. "MIT" does not fail for want of reasoning; it fails because a correct answer was
> discarded. An agent cannot fix a discarded correct answer.

## Notes

**Domain.** SAP customer master-data enrichment (Phase 1). See `CLAUDE.md` for the invariants and
`README.md` for the stage-by-stage spec. The glossary for this effort is `CONTEXT.md`.

**Skills every session should consult.** `grilling` + `domain-modeling` by default. `research` for
`wayfinder:research` tickets, `prototype` for `wayfinder:prototype`.

### Standing decisions

Decisions 1-4 and 11-12 are **unconditional** — they hold whatever fix is chosen.
Decisions 5-10 are **conditional**: they describe the agent lane's shape and apply *only if*
ticket 12 concludes an agent lane is justified. Do not build against them before then.

These came out of the charting grill. A ticket may *refine* them; none may reverse one without
saying so explicitly and updating this block.

**Philosophy**

1. **Planner, not author.** Scheme B hard rule 1 (`llm` never reaches `verified`) stays untouched.
   The agent chooses queries and tools; it never authors a final value on its own reasoning.
2. **The guarantee is 0 unverified writes, not 0 hallucinations.** Structurally enforced by the
   `SCOPED_FIELDS` write-lock plus mandatory re-verification. The model may hallucinate freely
   inside the loop; it physically cannot write.
3. **Agent owns retrieval; deterministic checks own verification.** Thresholds do not move —
   queries get better. `registry_match` / `locality` / the thresholds stay unreachable from inside
   the loop, because an LLM that proposes *and* verifies is one source, not two (Scheme B hard
   rule 4, applied to the agent).
4. **Determinism is an evaluation property; correctness is a gate property.** This is what makes
   production nondeterminism harmless.

**Architecture**

5. One lane, replacing Tier 3 and absorbing `grounded_resolver`. Preprocessing (Stage 0/1) and
   direct Tier 1 registry hits are unchanged.
6. Entry gate is **"no confident registry identity"** — a Tier 1 miss, *or* a weak match, *or* an
   ambiguity `registry_match` refused. Not merely "Tier 3 ran".
7. Six tools: `ror_lookup`, `gleif_lookup`, `wikidata_lookup`, `web_search`, `fetch_page`,
   `propose_and_verify`. The deterministic comparators are deliberately **excluded** from the
   tool surface.
8. Six guardrails: step budget · cost ceiling · schema-validated args · mandatory re-verification ·
   an **"insufficient evidence" terminal that is a first-class success** · **no uncached tool, ever**.
9. No RAG / retrieval index in v1. Tool-use only.
10. Hard cap 8 tool-calling steps / ~6 LLM calls per record in the lane. **Gate width is the cost
    lever, not the budget.**
11. `tools/run_diff.py` remains a hard gate. The mechanism is that every tool routes through
    `utils/cache.py`; the gate compares outputs, not trajectories, so a different path to the same
    registry-authored answer passes.
12. **Plain loop over the OpenAI SDK's native tool-calling.** No LangChain, no LangGraph, no
    LangSmith. Trajectory observability comes from the existing `--retry-trace` / `trace.jsonl`
    infrastructure and is **in scope day one**.

**Sequencing.** Ticket 11 (instrument the rejection funnel) is the cheapest and most
informative thing on the board — it reuses the existing `_note_rejection` machinery and converts
"results are poor" into a per-gate loss table. It plus 02/03 (the eval) and 13 (gate composition)
come before any agent-lane work. Ticket 12 is the gate: no agent-lane design ticket starts until
it concludes.

## Decisions so far

<!-- one line per closed ticket: gist + link. -->

- [09 — Grounding and anti-fabrication research](issues/09-grounding-and-anti-fabrication-research.md):
  Retrieval **collapses** abstention (Claude 84.1%->52.0%, Gemini 100%->18.6%); the fix is an
  external sufficiency check, not a prompt. Correctness-checking is structurally blind to
  insufficiency (models are right 35-62% of the time on insufficient context), so the gate has
  **two** jobs. The registry-verifier assumption is sound but **not sufficient** — correlation moves
  upstream to the query (memorization ratio 3.7%->99.8% on mixed evidence), so the error forms
  before the verifier is consulted. Step budget 8 is defensible but generous: anchoring converges
  ~step 4, long context degrades 0.92->0.68 accuracy. Topics 3-4 unresearched (lookups declined).

- [11 — Where do records actually die](issues/11-instrument-the-rejection-funnel.md):
  **The gates are not the constraint; registry coverage is.** Live run over chemspeed_us_100 plus
  a 200-record labelled corpus (300 lookups). Country guard rejected **0**; short-name rule **0**;
  gate 3 (local rescore) fired on **3%** and was right about most of them. The losses are the
  query endpoint returning candidates that are *visibly different companies* (75/100, median local
  score 0.636). Joint registry identity **24/100** — and that is close to a **coverage ceiling on
  this corpus**, which bounds what any registry-verified lane, agentic or not, can recover.
  Dropping location context from the affiliation string raises `chosen>=0.8` from 2 to 7 but
  **6 of the 7 are wrong** — location is suppressing false positives, not causing misses.

- [14 — Tier 2B is dead code](issues/14-tier2b-department-search-is-dead-code.md): **DELETE.**
  The ticket's premise is refuted — `grounded_resolver` is a third, web-backed writer of `name2`
  (proven by probe through the real orchestrator), with registry re-verification and `name2_kind`
  classification Tier 2B never had. Addressable population is **21/200** (74% of unenriched Name 2
  is admin desks, overflow or DBA strings, not departments), and **21/21 already reach the
  grounded lane**. Not a missing-lane problem; a proposal-quality problem. Two real gaps found
  inside grounded instead: `build_query` never receives `domain`, and
  `canonical_preserves_identity` runs on `name1` only.

- [15 — record_type](issues/15-record-type-vocabulary-and-keyword-override.md): two premises
  refuted. `classifier.py:163` **withholds, never flips**, and fired **0 times in 200 records** —
  the misclassifications come from `_from_keyword`, the rank-3 fallback that runs only when no
  registry answered. And **Phase 2 does not read `record_type` at all** (`grep -rn record_type
  dedup/` -> 0; README:1365,1386 is stale). `record_type_hint` is **contaminated** — 99% company in
  S2, 80% government in S3; it tracks the file, not the record — so the 43%/0% headline is partly a
  label defect. `unknown` (77/200) is a **matching** failure, not coverage: >=47 of them are in ROR
  but unmatched. Actionable win extracted to [17](issues/17-legal-suffix-record-type-source.md).

- [02 — Labelling rules](issues/02-eval-metrics-and-labelling-rules.md): **three** metrics off one
  outcome partition — coverage, precision-on-written, and **recall on the resolvable population**
  (its complement is the false-abstention rate; without it a coverage drop cannot be told apart
  from a discarded correct answer). `name1` correctness is set-membership under
  `registry_match.names_agree` at 88, adopted from `consistency.py`, deliberately **not** keyed on
  `record_type`. Q3: **a legible decline is a success and never a free one** — it passes, is
  excluded from *both* denominators, and only counts if legible; a silent empty field is a defect.
  A wrong identifier is strictly worse than none (`adjudicator.py` guards divergent ids and is
  blind to a shared wrong one). Surfaced [16](issues/16-flagged-fields-drops-identifiers.md).

- [01 — Deployment tool-calling spike](issues/01-deployment-tool-calling-spike.md): **BLOCKED,
  HITL.** `.env` holds `.env.example` placeholders byte-for-byte. Spike script written and proven
  against a stub server; needs real credentials and a human run. Established without a call: this
  deployment returns **no `system_fingerprint`**, and two warm runs still differed on 10/100 rows
  *with* the seed sent — so if `seed` is also refused on tool-calling requests, an agent lane has
  no service-side determinism control and 06's evidence cache becomes load-bearing.

- [19 — Query formulation vs registry absence](issues/19-query-formulation-vs-registry-absence.md):
  **Hypothesis refuted; ticket 11's ceiling survives.** Eight query formulations tested. Union
  recovery: **12 of 175 lost records (7%)** as the organisation the record actually names — and five
  of the twelve are one duplicated record, so **8 distinct organisations across 300**. The decisive
  test removed the retrieval confound entirely: of the 52 lost chemspeed records with a resolved
  domain, **0 have any ROR organisation registered on that domain.** Absence, not retrieval failure.
  `expand_abbreviations` never reaching the query is a **real defect with an addressable population
  of 2 names in 300** — both already resolved, 0 of the 175 lost. Search fix moved registry identity
  24/100 -> **26/100**: two records. Restatement owed to ticket 15: ">=47 of 77 unknowns are in ROR"
  is a lower bound on *domain* presence, not *entity* presence, and must not be carried forward as
  evidence the gate rejects real matches. Promotes [23](issues/23-what-is-ror-id-for-parent-vs-child.md).


- [20 — SERP cache key omits the provider](issues/20-serp-cache-key-omits-provider.md): **FIXED
  2026-08-29.** The provider is now a required component of every SERP cache key, derived once in
  `cached_serp` from the client about to answer, under a versioned prefix (`serp2:`) so v1 entries
  become unreachable rather than silently reused. Q2 answered **no**: refusing to cache empties
  would break `tools/run_diff.py`'s `evidence_network_calls == 0` precondition, and is unnecessary
  once the provider is in the key. Suite at baseline (`5 failed, 2821 passed, 5 skipped`).
  **Every existing SERP entry is now cold** — warm before running the reproducibility gate.
- [17 — Legal-form suffix `record_type` source](issues/17-legal-suffix-record-type-source.md):
  **DONE 2026-08-29.** A `legal_form` source now sits between GLEIF and the keyword heuristic —
  the symmetric counterpart to `_from_keyword`, which could only ever say `research_institution`.
  Ticket 15's projection **reproduced through the real classifier, not inherited**: `+21 / -0`,
  S2 exact match 43% -> 64%, S3 unchanged at 0% (still 0 by construction — `government` is not a
  producible type; ticket 15 finding A, open, user's call). The predicate reads a legal form only
  in **final position** of the name or of a comma/DBA-delimited segment: any-position scores the
  same 1.000 precision on these 200 records but claims `Co-operative Research Centre` and
  `Co Down Health Trust` as companies, and the sample is 200 records where the pipeline runs on
  ten thousand. One existing test changed — `unknown` is not a contradiction of `company`, and
  `batch_consensus` already converges the cluster (`batch_consensus.py:143`).
- [24 — Grounded Name 2 identity guard](issues/24-grounded-name2-identity-guard.md): **DONE
  2026-08-29.** Both name slots are identity-checked now. The existing comparator refused the two
  wrong values but ALSO refused `Weapons Div` -> `...Weapons Division` (a `ror:verified` answer) and
  every other abbreviation expansion, because `_token_covers` needs a 4-char prefix - so Name 2 got
  its own predicate: expand abbreviations first, a unit-type word may be added but never dropped or
  changed, and Name 1's words are addable because a department names a unit *of* something. Replayed
  over all 7 live proposals: **kept 4, refused 3 - every refusal wrong, every keep correct, zero
  correct values lost.** An earlier iteration refused 5 of 7; the two false refusals are what forced
  the parent-name rule and a missing `Mgmt` -> `Management` entry, and they were found by running the
  real proposals rather than reasoning about them.
  Original finding: the guard at
  `grounded_resolver.py:588` is `name1`-only, though `originals` already holds the Name 2 value.
  Confirmed shipping wrong departments on live evidence — `Forensic Science Div` ->
  `Forensic Services Laboratory`, `Baytown Refinery Laboratory` -> `Baytown Refinery`. Two of the
  four unclean values among the lane's 8 resolutions are this. **Correctness, not recovery.**
- [25 — `build_query` cannot emit a `site:` term](issues/25-grounded-build-query-cannot-emit-site-term.md):
  `domain` is not a parameter of `build_query`; it reaches the module only inside `_re_verify`.
  **0 of 21 live grounded queries carried `site:`**, while 19 of the 21 records had a domain.
  Tier 2B's one distinguishing capability, recoverable in a signature change plus one
  `parts.append`. Sequenced after 24 so a proposal the guard should refuse is not counted as a win.

## Not yet specified

- **Should the agent eventually run *first*, with the static checks purely as verification?**
  The "other way around" option. Hinges on how often a confident direct Tier 1 hit is actually
  wrong, which nobody knows today. Blocked by the eval; revisit once 03 has numbers.
- **Human-in-the-loop review of a proposed canonical name.** The one capability that would
  justify LangGraph's checkpointing later. Plausible given the reviewer-facing flag system.
  Trigger to watch: a requirement that a proposal outlive the HTTP request.
- **Reasoning-model coupling.** `gpt-5.6`+ rejects `tools` on Chat Completions, and pinned
  `temperature`/`top_p` are unsupported on reasoning models. The agent lane and any model
  upgrade are entangled decisions. Currently on `gpt-5.4`, so not yet biting.
- **`EVIDENCE_CACHE_DIR` does not ship to production** (`tests/fixtures`, and `.funcignore`
  excludes `tests`). Intentional — or a gap? Decides whether the zero-network-calls property is
  eval-only by design.
- **How the agent lane interacts with Stage 6 batch consensus.** Consensus runs after finalise
  and can replace `name1_enriched`; an agent-authored record entering that pass may need
  different treatment. Cannot phrase the question sharply until the tool contract exists.
- **Production cost and latency at 10k records.** Needs the eval's population estimate first.

## Out of scope

<!-- work consciously ruled beyond this destination. Closed, never graduates. -->

- **Phase 2 dedup.** The adjudicator and golden-record election are downstream consumers of
  Phase 1 output and are not touched by this effort.
- **Rewriting preprocessing (Stage 0/1).** Deterministic, zero-cost, high-precision. Decision 5.
