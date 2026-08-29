# 09 — What is actually state of the art in grounding an agent against fabrication?

Type: research
Status: resolved
Blocked by: —

## Question

The lane's whole value rests on the model proposing *checkable* answers and honestly declining when
it cannot. `grounded_resolver` already does one thing right — it requires the model to cite an
`evidence_index` pointing at the evidence item its answer came from. What else is established
practice, as of 2026, with primary sources?

Specifically:

1. **Evidence citation / attribution schemes** beyond a bare index — span-level grounding, quote
   extraction, "answer only from the provided context" framings. What measurably reduces
   fabrication versus what merely sounds rigorous?
2. **Making abstention attractive.** Published techniques for getting a model to reliably return
   "insufficient evidence" rather than a confident guess. Schema design, prompt framing, calibration.
3. **Structured outputs vs tool-calling for the terminal step** — does forcing a strict schema
   measurably reduce fabrication, or just reshape it?
4. **Verifier-in-the-loop patterns.** Where an independent check gates the write (this lane's
   design). Any published evidence on how much it actually buys, and its known failure modes.
5. **Anti-patterns.** Documented ways ReAct loops go wrong: step-count thrashing, tool-result
   ignoring, anchoring on the first candidate, self-consistency theatre.

## Deliverable

A findings file on a throwaway `research/grounding-sota` branch, linked here. Flag any claim not
backed by a primary source rather than asserting it.

## Answer

Findings: [`.scratch/agentic-enrichment/research/09-grounding-sota.md`](../research/09-grounding-sota.md) (929 lines).
Topics 1, 2, 5 researched against primary sources. **Topics 3 and 4 were not** — those lookups were
declined and not retried; both sections say so and carry only `[unverified]` recollection with no
fabricated effect sizes. Every claim is tagged `[measured]` / `[argued]` / `[inferred]` /
`[unverified]` / `[vendor]`.

**1. Retrieval collapses abstention.** *Sufficient Context* (ICLR 2025, arXiv:2411.06037):
Claude 3.5 Sonnet 84.1% -> 52.0%, Gemini 1.5 Pro 100% -> 18.6%. With genuinely insufficient
context, models hallucinate rather than abstain a large fraction of the time. The remedy is an
external sufficiency check, **not** a better prompt.

**2. Correctness-checking cannot detect insufficiency.** Models answer correctly **35-62% of the
time even with insufficient context**. So a gate that only asks "is this value right?" is
structurally blind to "was there ever enough evidence here?" These are two jobs, not one.
*Caveat on our reading:* the paper's autorater is itself an LLM blended with the generator's own
self-confidence, so it licenses "outside the generation step", not "outside the model family".

**3. The registry-verifier assumption is sound but NOT sufficient.** It does defeat shared-model
correlation — but **correlation moves upstream, from the check to the query**. A registry answers
"is this a real entity?", never "is this THE entity for this record." Xie et al. measured
memorization ratio jumping **3.7% -> 99.8%** once a *mixed* evidence set contains anything matching
the model's prior — and a six-tool loop is exactly that regime. The planner latches onto a prior,
queries on it, and the registry confirms a real organisation. **The error is fully formed before
the verifier is ever consulted.**

**4. Step budget: 8 is defensible but generous.** ReAct used 7/5 because "more steps will not
improve performance", with <1.5% of correct trajectories using the full budget. AgentBench completed
trajectories: median **6.0**. Anchoring converges around **step 4**. FLenQA measured accuracy
falling **0.92 -> 0.68 between 250 and 3,000 tokens** — inside a loop's normal context range.
Guidance: prefer **wider retrieval per step over more steps**. No study designed to answer
"what max_turns?" appears to exist.

**5. Put abstention in the output space**, not only in prose instruction: 30.6% -> 87.8% on
RealTime QA's unanswerable subset (Zhou et al., EMNLP 2023 Findings). The abstain clause costs
~**7.4 points** on answerable questions (COLING 2025), but that damage is domain-dependent and
factoid lookup showed minimal loss — registry resolution is closer to the cheap end. `[inferred]`

**6. Cited evidence indices are wrong 25-50% of the time** (ALCE). If the verifier re-derives its
own evidence, the model's citation and the verifier's evidence can diverge with neither noticing.
Assert agreement between them.
