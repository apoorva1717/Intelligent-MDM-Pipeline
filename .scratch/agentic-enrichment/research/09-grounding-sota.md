# Grounding, Abstention and Verification: State of the Evidence

Research note for the agent-lane design (bounded ReAct loop, 6 retrieval tools,
"the model is a planner, never an author", explicit insufficient-evidence
terminal).

Compiled 2026-08-29.

**Evidence tags.** Every substantive claim carries one:

- **[measured]** — I opened a primary source in this session and read the number.
- **[argued]** — a reasoned proposal in a primary source, with no controlled
  measurement of its effect that I could find.
- **[inferred]** — my reasoning from a [measured] result, extended to this
  design. Not something the cited authors claim.
- **[unverified]** — recollection. I did **not** open a primary source for this
  in this session. Treat as a lead to check, not as a fact.

**Coverage warning, stated up front.** Topics 1, 2 and 5 are researched against
primary sources. **Topics 3 and 4 are not** — the background research tasks
covering them failed to return, and I was correctly instructed not to re-run the
lookups. Those two sections contain what I can support by reasoning from topics
1–2, plus clearly-tagged recollection, and their gaps are named individually in
the final section.

**A second-order caveat on topic 5.** It comes from a delegated research pass
that did open primary sources (40 source lookups). I did **not** personally
re-open those sources. I have preserved that agent's own evidence tagging and its
own list of things it could not verify. Treat topic 5 as one step further from
the primary literature than topics 1–2 — good enough to design against, worth
spot-checking before it goes in a thesis.

---

## Bottom line — 5 recommendations

### 1. Sufficiency judgement must sit outside the planner — and that means your gate has two jobs, not one

*Sufficient Context* (Joren, Zhang, Ferng, Juan, Taly & Rashtchian; UCSD / Duke /
Google; [arXiv:2411.06037](https://arxiv.org/abs/2411.06037), ICLR 2025) measured
that adding retrieved context **collapses** abstention: **[measured]**

| Model | Abstention w/o RAG | Abstention w/ RAG |
|---|---|---|
| Claude 3.5 Sonnet | 84.1% | 52.0% |
| Gemini 1.5 Pro | 100% | 18.6% |
| GPT-4o | 34.4% | 31.2% |

And when context is genuinely insufficient, models hallucinate rather than
abstain a large fraction of the time: Gemini 1.5 Pro 40.4% hallucinate vs 50.0%
abstain; Claude 3.5 Sonnet 36.5% vs 53.8%; GPT-4o 15.4% vs 61.5%. **[measured]**

Your lane is retrieval-heavy by construction — six tools all pushing evidence
into the context — so it operates squarely in the regime where this degradation
was measured.

**What counteracts it.** Not a prompt. The paper's remedy is a **separate
sufficient-context autorater** — a classifier judging whether the retrieved
context alone admits *any* plausible answer — combined with the model's
self-rated confidence via a small logistic regression, gating whether the system
answers at all. Measured gain: **2–10 percentage points** in accuracy among
answered queries, >10 points for Gemma 27B in high-accuracy regions on HotpotQA.
**[measured]**

Their sufficiency predicate is directly reusable and, importantly, does not
reference ground truth: *context is sufficient iff there exists a plausible answer
derivable from the context alone.* A component can evaluate that without knowing
the right answer.

#### On your sharper reading — is it over-extended?

You proposed: the gate has two jobs — *is this value correct*, and *was there ever
enough evidence to support any value*. **The direction is supported. The strength
is slightly over-extended. Here is the precise version the paper supports.**

Supported:

- Abstention degrades under retrieval, so the planner's own willingness to say
  "insufficient" is actively suppressed by the evidence you feed it. **[measured]**
- Their intervention is a sufficiency judgement made by a component that is *not*
  the answer generator, and it measurably improves the answer/abstain decision.
  **[measured]**
- Correctness-checking and sufficiency-checking are genuinely distinct
  judgements: models answer **correctly 35–62% of the time even with insufficient
  context**, via parametric knowledge and partial-context bridging. **[measured]**
  This is the strongest single argument for your two-jobs framing — a correct
  answer is *not* evidence that the context supported it, so a correctness gate
  structurally cannot detect insufficiency. Two jobs, because one check cannot
  do both.

Where you are over-extending:

- Their autorater is **itself an LLM**, and their gate **combines it with the
  generating model's own self-rated confidence**. So it is separate-from-the-
  generator, not independent-of-the-model. The paper does not establish that
  sufficiency must sit outside the model family — only outside the generation
  step. Your architecture can be *more* independent than what was measured; do
  not claim the measurement licenses the stronger design.
- Their setting is question answering, not field-writing into a locked schema.
  The transfer is plausible but untested.

**Net:** adopt the two-jobs framing. It is well-motivated by the 35–62%
correct-despite-insufficient result. But describe it as a design decision
supported by an analogous measurement, not as a finding.

### 2. The abstain clause costs ~7.4 points on answerable questions — a cost to choose deliberately

Madhusudhan, Madhusudhan, Yadav & Hashemi (ServiceNow;
[arXiv:2407.16221](https://arxiv.org/abs/2407.16221), Sep 2024; COLING 2025):
adding an explicit abstain clause moved GPT-4 Turbo on CommonsenseQA from
**40.2% → 32.8%** answerable accuracy — a **7.4-point drop** — while
substantially improving unanswerable-question handling (Mixtral 8x22b on MMLU:
27.3% → 65.1% unanswerable accuracy). **[measured]**

Record this as a cost being paid, not a footnote.

**Framings the literature supports for reducing it:**

- The degradation is **domain-dependent**. The same paper found Pop-QA (simple
  factoid lookups) showed *minimal* answerable-accuracy loss; the 7.4-point hit
  landed on CommonsenseQA, a harder task. **[measured]** Organisation-name
  resolution against a registry is far closer to Pop-QA's factoid character. The
  7.4 figure is plausibly a **worst case for your workload, not a central
  estimate** — but that is **[inferred]**, and you should measure it on your own
  data before relying on it.
- **CoT before the answer/abstain decision** improved abstention without the same
  penalty: Pop-QA GPT-4 Turbo unanswerable accuracy **83.6% → 95.8%**.
  **[measured]**
- **Put abstention in the output space, not only in prose.** Zhou, Zhang, Poon &
  Chen ([arXiv:2303.11315](https://arxiv.org/abs/2303.11315), EMNLP 2023
  Findings) added an explicit "I don't know" option to the answer set: RealTime QA
  unanswerable-subset accuracy **30.6% → 87.8%**. **[measured]**

### 3. Treat `evidence_index` as an audit trail, not a correctness guarantee

ALCE (Gao, Yen, Yu & Chen, EMNLP 2023,
[arXiv:2305.14627](https://arxiv.org/abs/2305.14627)) benchmarks exactly your
citation format — an integer index into a numbered evidence list. Best measured
citation recall / precision: ASQA ChatGPT 73.6% / 72.5%, GPT-4 73.0% / 76.5%;
QAMPARI GPT-4 27.4% / 28.5%; ELI5 ChatGPT+rerank 69.3% / 67.8%. The authors'
own words: *"on the ELI5 dataset, around 50% generations of our ChatGPT and GPT-4
baselines are not fully supported by the cited passages."* **[measured]**

A model emitting `evidence_index: 3` is wrong about that pointer somewhere
between a quarter and a half of the time depending on task difficulty. Your
independent re-verification is not belt-and-braces; it is the thing carrying the
load. This is also the number to quote when anyone proposes trusting the index.

### 4. Restrict the generator to selected spans — that has a measured effect; instructing it to "cite first" does not

*Attribute First, then Generate* (Slobodkin, Hirsch, Cattan, Schuster & Dagan,
ACL 2024, [arXiv:2403.17104](https://arxiv.org/abs/2403.17104)) decomposes into
content selection → sentence planning → generation conditioned on the selected
spans. Measured: **0% unattributed sentences** vs an end-to-end ALCE baseline's
**26.9%** on long-form QA (3.4% on multi-document summarisation); citations ~45×
shorter (2153.3 → 48.2 tokens); human verification time roughly halved (35s vs
59s LFQA). **[measured]**

The mechanism doing the work is restricting *what the generator can condition
on*. A 2025 result goes the other way when "attribution-first" means only
emitting inline markers while generating freely — see topic 1; the ordering
question is genuinely contested and the two camps test different interventions
under one name.

### 5. Give abstention an arithmetic payoff; do not expect a bigger or reasoning-tuned model to abstain better

Kalai, Nachum, Vempala & Zhang (OpenAI / Georgia Tech,
[arXiv:2509.04664](https://arxiv.org/abs/2509.04664), 8 Sep 2025) argue
hallucination persists because grading is binary and abstention scores the same
zero as a wrong answer. Their proposed prompt device, verbatim:

> "Answer only if you are >t confident, since mistakes are penalized t/(1−t)
> points, while correct answers receive 1 point, and an answer of 'I don't know'
> receives 0 points."

with t = 0.5 (penalty 1), 0.75 (penalty 2), 0.9 (penalty 9). **[argued]** — I
found no controlled measurement in that paper of how much it raises abstention.
Cheap and testable; not yet established.

Against it: AbstentionBench (Kirichenko, Ibrahim, Chaudhuri & Bell, Meta FAIR,
[arXiv:2506.09038](https://arxiv.org/abs/2506.09038), 10 Jun 2025; 20 datasets,
20 frontier LLMs) measured that **scaling does not help** abstention and that
**reasoning fine-tuning degrades abstention by 24% on average**, including in
maths and science where those models were explicitly trained. System prompts help
in practice but do not fix the underlying inability to reason about uncertainty.
**[measured]**

Practical reading: the system prompt is your lever; model size is not; and
choosing a reasoning model for the abstention terminal may actively hurt.

### Direct answer on the step budget

**8 is defensible, not arbitrary — and if anything generous.** ReAct itself used
7 (HotpotQA) and 5 (FEVER) because *"more steps will not improve ReAct
performance"*, with **<1.5%** of correct trajectories using the full budget;
AgentBench's *completed* trajectories run **median 6.0 / mean 7.95** rounds;
EpiBench's 5/10/15 ablation shows gains 5→10 and *"little to no additional gain"*
10→15. **[measured]**

Two caveats that push *downward*, not up: anchoring converges around **step 4**
(so half an 8-step budget is spent after the decision is effectively made), and
context degradation is measurable from **250→3,000 tokens** — inside your loop's
normal accumulation. Keep 8 as a ceiling, expect useful work to finish by 3–5,
and prefer **wider retrieval per step over more steps** (top-k 4→8 bought +3.2 to
+5.2 points where extra reasoning length did not). Full detail and sources in
topic 5. **No study designed specifically to answer "what max_turns?" appears to
exist** — this is assembled from incidental reports.

---

## 1. Evidence citation and attribution

### Measured

**Index-pointer citation is not self-validating.** ALCE numbers in Bottom Line #3.
The single most relevant measured result to your existing `evidence_index`
component. **[measured]**

**Attribution-then-answer, when it restricts the generator.** *Attribute First,
then Generate* numbers in Bottom Line #4. AutoAIS on LFQA 78.7% (basic) / 89.3%
(CoT) vs ALCE 49.8%; human AIS 94.4%. **[measured]**

Caveat the authors supply themselves: their human analysis found **~42% of
sentences that automatic metrics labelled "unsupported" were actually partially
supported.** AutoAIS-style metrics overstate the problem, so effect sizes
computed with them are noisy in both directions. **[measured]**

**...and a 2025 result pointing the other way.** Saxena, Bommireddy, Padia &
Gaur, "Generation-Time vs. Post-hoc Citation"
([arXiv:2509.21557](https://arxiv.org/abs/2509.21557), v2 Dec 2025) found
**post-hoc** citation (answer, *then* attribute) beat generation-time: 78% vs 69%
answer correctness, 37% vs 41% citation hallucination; on ALCE 75% coverage /
42% correctness vs 37% / 21%. On FEVER the paradigms split (G-Cite 94%
correctness but 27% coverage; P-Cite 75% / 74%). Their overall conclusion: **the
dominant factor is retrieval augmentation itself**, larger than the citation
paradigm. **[measured]**

**These two do not reconcile at face value and I will not pretend they do.** My
reading — **[inferred]**, not a claim either paper makes — is that they test
different interventions under one name: Slobodkin et al. *architecturally
restrict* the generator to pre-selected spans; the "generation-time" arm in
Saxena et al. is a model emitting inline markers while generating freely. If that
is right, the transferable lesson is **restriction, not ordering**. Treat the
ordering question as **contested**.

**Verbatim quote extraction before answering.** LLMQuoter (Bezerra & Weigang,
[arXiv:2501.05554](https://arxiv.org/abs/2501.05554), Jan 2025), quote-first-then-
answer on a 15k HotpotQA subset (600 test): LLaMA-1B 24.4% → 62.2%; LLaMA-3B
57.7% → 83.0%; GPT-3.5-Turbo 75.8% → 88.5%. **[measured]**

Three caveats that bite for your design:

- The gain is **answer accuracy under long distractor contexts** — largely a
  denoising effect. Not a demonstration that field-level fabrication fell.
- The quoter itself scores **precision 71.0%, recall 68.0%, F1 69.1%**. A
  68%-recall extractor discards roughly a third of relevant evidence. In an
  abstention-first design that converts into **false abstention** — which your
  design counts as success and is therefore least instrumented to detect.
  **[inferred]**
- Single dataset, small models, non-peer-reviewed preprint.

**Context-restriction framings, large measured effect on older models.** Zhou et
al. ([arXiv:2303.11315](https://arxiv.org/abs/2303.11315)): opinion-based
reframing — `Bob said, '{context}' Q: {question} in Bob's opinion? Options:
{options} A:` — plus counterfactual demonstrations. GPT-3.5 memorisation ratio on
Natural Questions **35.2% → 11.0%**; RealTime QA unanswerable-subset accuracy
**30.6% → 87.8%**. **[measured]** Best evidence here is 2023 and
pre-GPT-4-class; needs re-validation. One of very few prompt framings with a
large measured effect on *both* faithfulness and abstention.

**Grounding metrics are gameable by hedging.** FACTS Grounding (Google DeepMind,
[arXiv:2501.03200](https://arxiv.org/abs/2501.03200), Jan 2025; 1,719 examples;
a response counts accurate only if *all* claims are grounded). They added a
disqualification filter because *"metrics that focused on evaluating factuality
of generated text ... can be circumvented by ignoring the intent behind the user
request. By giving shorter responses that evade conveying comprehensive
information ... it is possible to achieve a high factuality score while not
providing a helpful response."* Disqualification cost **1–5 percentage points**
and reordered the leaderboard. Top scores as of 6 Jan 2025: Gemini 2.0 Flash
Experimental 83.6%, Gemini 1.5 Flash 82.9%, Gemini 1.5 Pro 80.0%. **[measured]**

Two lessons: even the best models leave ~16% of responses with at least one
ungrounded claim in a *pure grounding* task; and **your abstention rate and your
grounding rate must be scored separately**, or the lane will learn to abstain in
order to look grounded. **[inferred]**

**Structurally-constrained citation.** Anthropic's Citations API (announced
23 Jan 2025; [blog](https://claude.com/blog/introducing-citations-api),
[docs](https://platform.claude.com/docs/en/build-with-claude/citations)) chunks
documents into sentences and has the model reference those chunks, so it
structurally cannot cite absent text. Reported: *"increasing recall accuracy by
up to 15%"* vs custom prompt implementations; customer Endex *"reduced source
hallucinations and formatting issues from 10% to 0%."* **[vendor — no published
methodology, not independently replicated.]** The **mechanism** is the
transferable part, and it matches your design rule: a constrained reference into
a system-held evidence store, not a free-text string the model authors.

### Widely repeated, weakly measured

- **"Ask it to extract quotes first"** as a general anti-hallucination rule.
  Anthropic's official guidance
  ([Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations))
  recommends it for documents >20k tokens: *"ask Claude to extract word-for-word
  quotes first before performing its task. This grounds its responses in the
  actual text, reducing hallucinations."* **No effect size given.** The page
  carries an honest disclaimer: *"while these techniques significantly reduce
  hallucinations, they don't eliminate them entirely."* The nearest primary
  measurement I found is LLMQuoter, which measures accuracy under distraction,
  not fabrication rate. **[argued]**
- **"Answer only from the provided context."** Universally recommended. The
  strongest primary measurement I found is context-faithful prompting's
  memorisation-ratio result, which is a *specific* framing (opinion reframing),
  not the generic instruction. I found no clean controlled measurement of the
  bare instruction on a modern model. **[argued]**
- **"Make it cite its sources and hallucination goes away."** Directly
  contradicted by ALCE: the citations are themselves unsupported 25–50% of the
  time. Citation buys auditability, and auditability pays only if something
  audits. **[measured, as refutation]**

---

## 2. Making abstention attractive

*Best-researched section; also the most recent and most internally consistent
literature.*

### The structural problem

Kalai et al. ([arXiv:2509.04664](https://arxiv.org/abs/2509.04664), 8 Sep 2025).
Their Table 2 surveys benchmark grading for IDK credit:

| Benchmark | Credit for "I don't know" |
|---|---|
| GPQA | None |
| MMLU-Pro | None |
| IFEval | None |
| Omni-MATH | None |
| BBH | None |
| MATH (L5 split) | None |
| MuSR | None |
| SWE-bench | None |
| HLE | None |
| WildBench | Partial |

Of ten surveyed, only WildBench gives any credit for uncertainty. The survey is
**[measured]**; the causal claim about hallucination is **[argued]**.

### That the abstention operating point is achievable

GPT-5 System Card (OpenAI, 13 Aug 2025), SimpleQA table:

| Model | Accuracy | Hallucination rate | Implied abstention |
|---|---|---|---|
| gpt-5-thinking | 0.55 | 0.40 | ~0.05 |
| OpenAI o3 | 0.54 | 0.46 | ~0.00 |
| gpt-5-thinking-mini | 0.22 | 0.26 | ~0.52 |
| OpenAI o4-mini | 0.24 | 0.75 | ~0.01 |

Accuracy and hallucination columns **[measured]**. The abstention column was
**not printed** in the table I read — I derived it as `1 − accuracy −
hallucination`. **[inferred]** Verify before quoting externally.

The mini pair is the existence proof: **2 points of accuracy given up for a
49-point reduction in wrong answers.** That is exactly the trade your design
makes, and it is achievable at the frontier. The card also states models were
trained *"to fail gracefully when posed with tasks that it cannot solve —
including impossibly large tasks or when missing key requirements."*

### That the capability is weak, and weakest in your regime

- **AbstentionBench** — scaling doesn't help; reasoning fine-tuning degrades
  abstention 24% on average. **[measured]**
- **Sufficient Context** — retrieval collapses abstention. Bottom Line #1.
  **[measured]** The most consequential result in this note for your lane.
- **RefusalBench** (Muhamed, Ribeiro, Dreyer, Smith & Diab,
  [arXiv:2510.10390](https://arxiv.org/abs/2510.10390), 14 Oct 2025) — selective
  refusal in *grounded* settings, i.e. yours: **[measured]**
  - Best on RefusalBench-NQ: **Claude-4-Sonnet 73.0%** refusal accuracy. Best on
    RefusalBench-GaRAGe: **DeepSeek-R1 47.4%**.
  - **Multi-document halves it**: Claude-4-Sonnet **73.0% → 36.1%**. Your lane
    aggregates evidence from six tools — this *is* the multi-document regime.
  - Detection and categorisation are distinct sub-skills: GPT-4o reaches high
    detection F1 by **refusing 60%+ of answerable questions** while scoring only
    54.1% category accuracy — it "can identify but not understand informational
    flaws."
  - Scaling is capability-specific: Qwen answer accuracy jumps 13.0% → 56.1%
    between 4B and 7B while refusal accuracy stays **<17% at every size**.
  - Refusal is **trainable and alignment-sensitive**; DPO beats SFT, largest gain
    3.4× at 7B.

  Consequence: a bare refusal *rate* is a gameable metric — GPT-4o's 60%
  over-refusal proves it. Measure "refused" and "refused for the right reason"
  separately, and instrument false abstention explicitly. **[inferred]**

### What actually moves abstention, ranked by evidence strength

1. **An external sufficiency check gating the write.** Sufficient Context, 2–10pp.
   **[measured]** Strongest, and architecturally compatible with "planner, never
   author".
2. **Abstention as a member of the output space, not prose.** RealTime QA
   30.6% → 87.8%; Mixtral on MMLU 27.3% → 65.1%. **[measured]**
3. **CoT before the answer/abstain decision.** 83.6% → 95.8% unanswerable
   accuracy. **[measured]**
4. **An explicit arithmetic payoff for abstaining** (Kalai et al.). **[argued]**
5. **Self-consistency / conformal thresholds.** Yadkori et al. (DeepMind),
   "Mitigating LLM Hallucinations via Conformal Abstention"
   ([arXiv:2405.01563](https://arxiv.org/abs/2405.01563), May 2024): sample
   multiple responses, have the LLM self-evaluate their similarity, apply
   conformal prediction to obtain a **rigorous theoretical bound on the
   hallucination rate**, experimentally holding on closed-book open-domain QA.
   **[measured]** Costs k samples per record, and is self-consistency-based —
   see topic 4 on why consistency is not correctness.

**Not levers:** model scale; reasoning fine-tuning (−24%).

**Out of scope but noted:** R-Tuning (Zhang et al., NAACL 2024 Outstanding Paper,
[arXiv:2311.09677](https://arxiv.org/abs/2311.09677)) — refusal-aware instruction
tuning; refusal behaves as a task-agnostic meta-skill. I did not extract effect
sizes. Relevant only if fine-tuning enters scope.

**Survey for orientation, no effect sizes:** Wen et al., "Know Your Limits: A
Survey of Abstention in Large Language Models"
([arXiv:2407.18418](https://arxiv.org/abs/2407.18418), TACL).

---

## 3. Structured outputs vs tool-calling for a final answer

**INCOMPLETE.** The background task covering this returned nothing. What follows
is partly derivable from topics 1–2 and partly recollection. The central question
you asked — *does a required field coerce confabulation?* — I **could not answer
from a primary source**, and it is the most important gap in this note.

### What I can support

**Schema conformance is not factual correctness, and providers say so.** OpenAI's
Structured Outputs (strict mode) and Anthropic's tool-use schemas guarantee that
output *parses* and *validates* against the schema. Neither guarantees the values
are true. **[unverified — I did not open the provider docs this session]**, though
the distinction is not seriously contested and follows from what constrained
decoding does: it restricts the token space to grammar-legal continuations, which
is orthogonal to whether the content is supported.

**The abstention-compatible schema recommendation is well-supported — from topic
2, not from schema literature.** The measured result that adding an explicit "I
don't know" *option to the answer set* moved RealTime QA unanswerable accuracy
from **30.6% → 87.8%** (Zhou et al., **[measured]**) is the strongest evidence in
this note for schema design. Its lesson transfers directly: **make
`insufficient_evidence` a first-class member of a result enum, not a convention
signalled in prose or by leaving a field null.** A model asked to choose among
enum members, one of which is "insufficient evidence", is doing a
constrained-choice task; a model asked to omit a field or emit prose caveats is
not.

Corollary, **[inferred]**: any field that a value must be written into should be
**unreachable** unless the terminal enum member is the "resolved" one. Encode the
abstention terminal as a discriminated union — `{"outcome": "resolved", value,
evidence_index}` vs `{"outcome": "insufficient_evidence", reason}` — rather than a
flat object with nullable value fields. In a flat object with a required-ish
`value`, the abstention path requires the model to *decline to fill something*,
which is the harder behaviour; in a union, abstention is a *positive selection*,
which is the easier one. This matches the codebase's existing instinct that
`domain` stays empty and the record gets `domain-unverified`, rather than a
best-guess domain plus a caveat.

**Format restriction may cost reasoning quality, and the claim is contested.** I
recall Tam et al., "Let Me Speak Freely? A Study on the Impact of Format
Restrictions on Performance of Large Language Models"
([arXiv:2408.02442](https://arxiv.org/abs/2408.02442), EMNLP 2024 Industry
Track), reporting that strict JSON-mode constraints degrade reasoning benchmark
performance relative to free-form output, and I recall published rebuttals
(notably from the Outlines / `.txt` team) arguing the comparison was confounded
by prompt differences rather than isolating constrained decoding. **[unverified]**
— I did not open either. **Do not cite an effect size from me here; I would be
inventing it.** If format-restriction cost matters to your decision, this pair is
the thing to read.

I also recall work arguing constrained decoding *distorts* the output
distribution relative to the true conditional — "grammar-aligned decoding"
([arXiv:2405.21047](https://arxiv.org/abs/2405.21047)) is the reference I
half-remember. **[unverified]**

### The gap that matters most

**Does making a field required cause the model to fill it with plausible junk
rather than decline?** This is the single question in this note whose answer
would most change your design, and I have **no primary source for it in either
direction**. It is widely asserted in practitioner writing. I could not verify it.

It is also **cheap for you to measure in-house**, and you are better placed than
the literature to do so: run the agent lane over records with known-unresolvable
organisations under (a) a flat schema with a required `value`, (b) the
discriminated union above, and compare the rate at which a value gets written.
That experiment would produce a number specific to your model, your prompt and
your task, which is worth more than any published effect size transferred from a
QA benchmark. Treat it as a design-validation task, not a literature question.

---

## 4. Verifier-in-the-loop / generate-then-verify

**INCOMPLETE.** The background task covering this returned nothing. The general
literature below is **[unverified]** recollection. **The registry-specific
analysis that follows it is the part I would actually stand behind**, because it
reasons from results I verified in topics 1–2.

### General literature — leads to check, not facts

I recall, and did **not** verify in this session:

- Huang et al., "Large Language Models Cannot Self-Correct Reasoning Yet"
  ([arXiv:2310.01798](https://arxiv.org/abs/2310.01798), DeepMind) — *intrinsic*
  self-correction, without external feedback, does not help and can degrade
  performance.
- Stechly, Valmeekam & Kambhampati ([arXiv:2310.12397](https://arxiv.org/abs/2310.12397),
  [arXiv:2402.08115](https://arxiv.org/abs/2402.08115)) — self-critique *hurts*
  on planning tasks, while an **external sound verifier helps**; and the
  LLM-Modulo framing ([arXiv:2402.01817](https://arxiv.org/abs/2402.01817)),
  where an LLM generates candidates and an external sound verifier gates them.
  This is architecturally the closest published framing to your design.
- Panickssery et al. ([arXiv:2404.13076](https://arxiv.org/abs/2404.13076)) —
  LLM evaluators recognise and favour their own generations (self-preference
  bias); and the self-enhancement bias analysis in MT-Bench / "Judging
  LLM-as-a-Judge" ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685)).
- SelfCheckGPT ([arXiv:2303.08896](https://arxiv.org/abs/2303.08896)),
  Chain-of-Verification ([arXiv:2309.11495](https://arxiv.org/abs/2309.11495)),
  FactScore ([arXiv:2305.14251](https://arxiv.org/abs/2305.14251)), SAFE
  ([arXiv:2403.18802](https://arxiv.org/abs/2403.18802)).

All **[unverified]**. I am listing titles and IDs as reading leads. I am
deliberately not attaching effect sizes to any of them, because I would be
reconstructing numbers from memory and this note would then exhibit the exact
failure it is about.

### Verified results in this note that bear on verification

- **Verifiers are themselves fallible.** Even in a pure grounding task with the
  document supplied, the best models leave ~16% of responses containing at least
  one ungrounded claim (FACTS Grounding). **[measured]** An LLM verifier is not a
  sound verifier.
- **A separate sufficiency judge measurably improves the answer/abstain
  decision** — 2–10pp (Sufficient Context). **[measured]** This is the closest
  thing in this note to a clean measurement of what a verifier-in-the-loop buys.
- **The generator's own citation is unreliable** — 25–50% unsupported (ALCE).
  **[measured]** So the verifier must re-derive the evidence, not trust the
  pointer it was handed.

---

## 4b. What changes when the verifier is a REGISTRY, not a model

You asked for this stress-tested rather than confirmed. **Your architecture does
sidestep the specific failure mode of a shared-model verifier — that part of the
assumption holds.** A ROR or GLEIF lookup does not share weights, a prompt, a
sampling seed or a training distribution with the planner, so self-preference
bias and shared-blind-spot correlation genuinely do not apply.

**But correlation does not disappear. It moves upstream, from the check to the
query.** Six ways this bites, in rough order of how much I would worry:

**1. The planner chooses what gets verified — so the verifier inherits the
planner's error.** This is the sharpest one. The registry answers *"is this a
real entity?"*, not *"is this THE entity for this record."* If the planner
anchors on a wrong candidate — "Dept of AI" → "Delft University of Technology" —
and queries the registry for that, the registry confirms a real, correctly-
identified organisation, and the gate passes a value that is verified and wrong.
Independence of the *checker* buys nothing when the *question* is corrupted.
**[inferred]**, but it follows directly from what a lookup API does, and it is the
failure your existing `registry_match.py` ambiguity margin and second-independent-
signal rule already exist to catch. **The agent lane must inherit those rules,
not re-derive them.**

Topic 5 supplies the measured mechanism that *produces* the corrupted query. Xie
et al. measured that once a mixed evidence set contains anything consistent with
the model's prior, memorization ratio jumps **3.7% → 99.8%** (ChatGPT) /
**8.9% → 99.8%** (GPT-4) **[measured]** — and a six-tool loop returning
heterogeneous evidence is precisely that regime. So the planner latches onto a
prior-consistent candidate, queries the registry for it, and the registry confirms
a real organisation. **Independence of the verifier does not help, because the
error was fully formed before the verifier was consulted.**

**2. A registry cannot judge sufficiency at all — this is why the gate needs two
jobs.** A lookup can confirm a proposed value; it is structurally incapable of
telling you whether the evidence ever supported proposing *any* value. The
Sufficient Context result that models answer **correctly 35–62% of the time even
with insufficient context** **[measured]** is exactly this: a correctness check
passes, and the record is still unsupported. Your registry gate is a correctness
gate. It is not, and cannot be made into, a sufficiency gate. This is
independent confirmation of your point 1 from a different direction.

**3. Silence is ambiguous, and a registry miss is not evidence of absence.** A
failed lookup could mean the entity is not in the registry, or that the query
string was wrong. Your codebase already encodes the right instinct in
`locality.py` — *"Silence is not evidence; only a stated differing place is a
contradiction."* The agent lane needs the same asymmetry: a registry **hit** is
strong positive evidence; a registry **miss** is weak and must not by itself
drive either a write or a confident abstention. **[inferred]**

**4. Dense candidate spaces defeat high per-check accuracy.** ROR and GLEIF
contain many similarly-named entities. A verifier with excellent per-check
accuracy still yields many false accepts when the candidate set is dense with
near-misses — a base-rate effect, not a verifier-quality effect. This is precisely
what `REGISTRY_AMBIGUITY_MARGIN` and the collision-prone-short-name rule guard
against, and "MIT" is the canonical case. **[inferred]**

**5. Goodhart pressure toward verifiable-but-wrong values.** If the loop learns
(within a run, via its own visible reward structure) that a value only survives
when a registry confirms it, the cheapest path to a non-abstention outcome is to
propose whatever the registry *will* confirm — biasing toward large, well-known,
well-registered organisations over the actual small or messy record in front of
it. I have **no direct measurement of this in an agent loop** — it is
**[inferred]**. But the mechanism has a measured analogue: FACTS Grounding had to
add a disqualification filter precisely because models shortened and hedged
responses to score better on a grounding metric while being less useful
**[measured]**. Optimising against a checkable proxy reshapes behaviour toward
what the proxy accepts. Worth an explicit test: measure whether the lane's
resolution rate is higher for large/well-known organisations than a
human-adjudicated baseline says it should be.

**6. The registry is itself imperfect.** Deterministic is not the same as correct.
Registries carry stale records, duplicates, merged and dissolved entities. A
deterministic gate gives you *reproducibility* of the check — which matters a
great deal for your run-diff reproducibility gate — but reproducibility of a
check is not correctness of the check. Your existing `liveness` and
`consistency` stages already acknowledge this; the agent lane should not be
allowed to treat a registry hit as more authoritative than the rest of the
pipeline does. **[inferred]**

**One further mismatch worth designing against.** ALCE **[measured]** shows the
model's cited `evidence_index` is wrong 25–50% of the time. If the verifier
re-derives its own evidence (a fresh registry lookup) rather than checking the
item the model pointed at, then **the model's citation and the verifier's
evidence can diverge without either component noticing.** The value passes, the
audit trail is wrong, and your provenance is a fiction that survives review.
Consider asserting agreement between the cited evidence item and the evidence the
verifier actually used — a cheap consistency check that catches a class of error
neither component catches alone. **[inferred]**

**Summary judgement on the load-bearing assumption:** an independent registry
verifier is genuinely stronger than an LLM self-verifier and the design is right
to rely on it. It defeats shared-model correlation. It does **not** defeat
shared-*query* correlation, it cannot judge sufficiency, and it degrades exactly
where the candidate space is dense — which is where messy MDM records live. The
assumption is sound; the claim that it is *sufficient* is not.

---

## 5. ReAct loop anti-patterns and the step budget

*Researched via a delegated pass over primary sources; see the second-order
caveat at the top of this note.*

### The step-budget question, answered directly

**8 is defensible — and if anything generous. It is not arbitrary, and it is not
contradicted.** Every published budget ablation I have found clusters below or
around it: **[measured]**

| Source | Budget finding |
|---|---|
| ReAct (Yao et al., [arXiv:2210.03629](https://arxiv.org/abs/2210.03629), ICLR 2023) | Used **7 steps** for HotpotQA, **5** for FEVER, *"as we find more steps will not improve ReAct performance."* Of trajectories with correct answers, only **0.84%** / **1.33%** used the full budget. |
| AgentBench ([arXiv:2308.03688](https://arxiv.org/abs/2308.03688), ICLR 2024) | *Completed* trajectories: **median 6.0, mean 7.95** rounds. Timed-out ones average 25.5. |
| EpiBench ([arXiv:2604.05557](https://arxiv.org/abs/2604.05557)) | Budget ablation 5/10/15: consistent gain 5→10, *"little to no additional gain"* 10→15. Closest published ablation to your number. |
| AppWorld ([arXiv:2407.18901](https://arxiv.org/abs/2407.18901), ACL 2024) | Tested 10/15/20 turn caps; *"found the performance to saturate at 15."* |
| SWE-agent ([arXiv:2405.15793](https://arxiv.org/abs/2405.15793)) | Successes finish at **median 12 steps**; failures **mean 21**. **93.0%** of resolved instances submit before exhausting budget. Authors: *"increasing the maximum budget or token limit are unlikely to substantially increase performance."* |

The consistent shape: **gains concentrate around 5–10 steps and then flatten, and
failures — not successes — expand to fill whatever budget you give them.** Your
task (registry lookup over six tools) is structurally simpler than SWE-agent's,
so the low end of that range is the relevant one.

Two independent lines converge on the same conclusion from your side of the
problem:

- **More steps actively damages your abstention terminal.** RefusalBench measured
  refusal accuracy roughly **halving** in the multi-document regime
  (Claude-4-Sonnet **73.0% → 36.1%**) **[measured]**, and Sufficient Context
  measured retrieval *suppressing* abstention outright **[measured]**. Every
  additional step pushes the loop further into both. **[inferred]**
- **Context degradation starts far earlier than "long context" implies.** FLenQA
  ([arXiv:2402.14848](https://arxiv.org/abs/2402.14848), ACL 2024) measured
  average accuracy falling **0.92 → 0.68 between 250 and 3,000 input tokens** —
  well inside an 8-step loop's normal accumulation. CoT does not mitigate it.
  **[measured]**

**Net: keep 8 as a ceiling, and expect the useful work to finish by 3–5.**
Instrument the step at which the final answer was actually determined. If the
tail from 5 to 8 produces no additional correct resolutions — only more context
and more chances to anchor — tighten it. Anthropic's own practitioner heuristic
for this exact shape of task is *"simple fact-finding requires just 1 agent with
3–10 tool calls"* ([How we built our multi-agent research
system](https://www.anthropic.com/engineering/built-multi-agent-research-system),
13 Jun 2025), introduced because overinvestment on simple queries was *"a common
failure mode in our early versions."* **[argued]** — a heuristic, not a curve.

**On six tools: you are in the safe zone.** Meta's *How Many Tools Should an LLM
Agent See?* ([arXiv:2605.24660](https://arxiv.org/abs/2605.24660)) finds ~**7
tools** near-optimal on BFCL (90.3% at K≈7 vs 90.8% at K=50). Degradation studies
concern catalogues of 100+. **[measured]** The widely-repeated "accuracy degrades
past 10–15 tools" traces only to SEO aggregators — **folklore, do not cite.**

### The anti-patterns that actually threaten this design

**1. Grounding trades hallucination for looping — this is ReAct's own finding.**
Yao et al. hand-labelled 200 HotpotQA trajectories. Failure modes, ReAct vs CoT:
hallucination **0% vs 56%**, but reasoning error (*"wrong reasoning trace; fails
to recover from repetitive steps"*) **47% vs 16%**. **[measured]** Grounding kills
fabrication and replaces it with a looping failure ~3× larger than CoT's. The
authors name it explicitly: *"the model repetitively generates the previous
thoughts and actions."*

This is the single most important framing for your design: **you are not removing
a failure, you are exchanging one for another.** Your architecture already handles
the hallucination side well. The looping side is currently unguarded.

**2. Thrashing is measurable and common.** AgentBench's "Task Limit Exceeded"
category is **24.9%** of commercial-API-model runs and **36.9%** of open-source
runs; **>90% of TLE trajectories are demonstrably repetitive** (Rouge-L ≥ 0.8
between any two of the last 10 responses). **[measured]** Note their methodology:
they compare across a **10-response window**, not adjacent pairs, because agents
cycle through multi-state loops — **a naive "same action twice" detector will miss
most of it.** WebArena's shipped guardrail is keyed on `(action, observation)`
pairs: halt if the same action repeats 3× *on the same observation*, or on 3
consecutive invalid actions. **[measured]** Both are cheap to implement.

Counterweight: loop-bounding and premature-quitting are the same dial. WebArena
measured GPT-4 *"erroneously identifies **54.9%** of feasible tasks as
impossible."* **[measured]** In a design where abstention is a success terminal,
that is your false-abstention risk with a number on it.

**3. Mixed evidence flips the knowledge-conflict picture — and this is your
regime.** The popular framing (models stubbornly prefer their prior over
retrieval) is **backwards for frontier models on single passages**: ClashEval
(Wu, Wu & Zou, [arXiv:2404.10198](https://arxiv.org/abs/2404.10198)) measured
prior bias at only **2–4%** against context bias (over-deferring to *wrong*
context) at **16–31%** — Claude Opus 15.7% context / 2.1% prior; GPT-4o 30.4% /
2.1%. **[measured]**

But with a **mixed** evidence set — several sources, some agreeing with the
model's prior — Xie et al. (*Adaptive Chameleon or Stubborn Sloth*,
[arXiv:2305.13300](https://arxiv.org/abs/2305.13300), ICLR 2024) measured the
memorization ratio jumping from **3.7% → 99.8% (ChatGPT)** and **8.9% → 99.8%
(GPT-4)**. Order matters too: a **38.6–82.8%** swing depending on which evidence
comes first. **[measured]**

**A 6-tool loop returning heterogeneous evidence is exactly the mixed-evidence
regime, not ClashEval's single-passage one.** Practical consequence: the moment
one tool returns something matching the model's prior, the model will latch onto
it and effectively ignore the rest — and your registry gate will happily confirm
that the latched-onto entity is real. This compounds directly with failure 4b.1.

Related: ToolFailBench ([arXiv:2607.04686](https://arxiv.org/abs/2607.04686))
measures a *Result-Ignore Rate* — right tool called, return not reflected in the
answer: Llama-3.1-8B **30.4%**, Llama-3.1-70B **11.2%**, Qwen2.5-72B **2.0%**.
Strongly model-size dependent. **[measured]**

**4. Anchoring is real, early, and undetectable from the outside.** *When Agents
Commit Too Soon* ([arXiv:2606.22936](https://arxiv.org/abs/2606.22936)) found
Llama-3.1-70B running ReAct on HotpotQA shows **hidden-state convergence at
step 4** predicting downstream trajectory consistency (r = −0.35; StrategyQA
r = −0.83). Critically it is **correctness-agnostic** — committed-wrong and
committed-correct trajectories are indistinguishable. **[measured]** *Where Do
Deep-Research Agents Go Wrong?*
([arXiv:2606.02060](https://arxiv.org/abs/2606.02060)) puts stage-normalized error
rates at decision-making **60.5%**, finalization **51.8%**, retrieval **2.9%** —
*"the loop's retrieval is not the problem, its commitment is"* — with unsupported
commitments *"later reused as if they were established facts"* without
revalidation. **36.9% of *successful* trajectories also contain process errors.**
**[measured]**

Given a step-4 commitment point and an 8-step budget, **half your budget is spent
after the decision is effectively made.** That is an argument for tightening, and
for spending early steps on breadth rather than depth.

**5. What structure actually predicts success: gather before committing.** A
9,374-trajectory study across 19 agents
([arXiv:2604.02547](https://arxiv.org/abs/2604.02547)) found delaying the first
commit correlates **ρ = +0.68** with success, and front-loading action in the
first 10 steps **ρ = −0.78**. **[measured]** It also demolishes a tempting
metric: **trajectory length is not a valid failure signal** — within-agent
failures are longer, but per-task the effect *reverses* (resolved trajectories
longer on 63% of tasks, 44.0 vs 39.6 steps, p=1.9e-9). Simpson's paradox via task
difficulty. Do not build a "too many steps ⇒ probably failing" heuristic.

Consistent with this: SMTL ([arXiv:2602.22675](https://arxiv.org/abs/2602.22675))
found widening retrieval breadth (top-k 4→8) gave **+3.2 to +5.2 points**,
described as *"a more efficient scaling axis for long-horizon search than merely
increasing reasoning length."* **[measured]** **Prefer wider retrieval per step
over more steps.**

**6. Self-conditioning: pruning failed steps is a correctness intervention, not a
cost one.** *The Illusion of Diminishing Returns*
([arXiv:2509.09677](https://arxiv.org/abs/2509.09677), ICLR 2026) measured
per-step accuracy degrading with step index *beyond* what context length explains,
because the context accumulates the agent's own prior errors: Qwen3-32B at turn
100 scores ~95% with 0% injected self-errors, **~40% with 100%**. **[measured]**
Reported horizon lengths (H₀.₅): GPT-5 2176 steps, Claude-4 Sonnet 432, Gemini
2.5 Pro 120.

For your lane: **drop failed tool calls and empty registry misses from the
context** rather than carrying them forward. That is not a token-budget
optimisation; it measurably protects accuracy.

**7. Evaluate with repeats, report the floor.** Laban et al., *LLMs Get Lost in
Multi-Turn Conversation* ([arXiv:2505.06120](https://arxiv.org/abs/2505.06120),
Salesforce + MSR, May 2025; 200k+ simulations, 15 LLMs, 6 tasks): **average 39%
drop** multi-turn vs single-turn. The decomposition is the actionable part —
aptitude (P90) drops **16%** while unreliability (P90−P10) rises **112%**. A
concatenated-input control recovers ~95.1%, so **turn structure, not information
loss, is the cause**. Degradation begins at **2 shards**. Reasoning models degrade
identically; lowering temperature does not help. **[measured]**

Consequence for your evaluation harness: **single-run evals will not surface
this.** The ceiling barely moves while the floor collapses. Run N repeats and
report the 10th percentile. This sits well with the codebase's existing
determinism controls — but note those controls fix *sampling* variance, not the
turn-structure variance Laban et al. measured, which persists at temperature 0.

Also relevant: τ-bench ([arXiv:2406.12045](https://arxiv.org/abs/2406.12045))
measured gpt-4o retail **pass^1 61.2% → pass^8 <25%** **[measured]** — consistency
collapse under repetition, which is what a production MDM pipeline actually
experiences.

### One design-specific anti-pattern, from topic 1

**Anchoring on the first candidate is the failure your design is least equipped
to see**, because the registry gate cannot detect it (4b.1) and the model's own
account of which evidence drove the choice is unreliable 25–50% of the time
(ALCE, **[measured]**). If you instrument one loop pathology, instrument whether
the final answer equals the first candidate proposed, and whether that candidate
appeared before the loop had seen evidence from more than one tool. **[inferred]**

---

## Claims I could not verify from a primary source

**Whole topics not researched.** Topics 3 and 4 had no primary-source research
performed. Everything in them tagged **[unverified]** is recollection and should
be treated as a reading list. Specifically not verified:

- **Every effect size in topics 3 and 4.** I deliberately attached none.
- Tam et al. "Let Me Speak Freely?" ([arXiv:2408.02442](https://arxiv.org/abs/2408.02442))
  and its rebuttals — whether strict schema constraints degrade reasoning, and by
  how much. Contested; I read neither side.
- Whether **required fields coerce confabulation**. No source found in either
  direction. **The most important gap in this note**, and the one most cheaply
  closed by an in-house experiment (see topic 3).
- Provider documentation language on schema-conformance-is-not-correctness
  (OpenAI Structured Outputs, Anthropic tool use, Gemini structured output). Not
  opened this session.
- All self-verification and self-preference literature listed in topic 4:
  Huang et al. 2310.01798; Stechly et al. 2310.12397 / 2402.08115; LLM-Modulo
  2402.01817; Panickssery et al. 2404.13076; MT-Bench 2306.05685; SelfCheckGPT;
  CoVe; FactScore; SAFE.
**Topic 5 — second-hand from a delegated pass.** Sources were opened by a
research subagent, not by me. Its own list of things it could **not** verify,
carried forward verbatim in substance:

- ReAct's step limits for ALFWorld / WebShop — only HotpotQA (7) and FEVER (5)
  are stated in the paper.
- τ-bench's pass^2…pass^7 series (figure-bound), and its max-turn limit — the
  paper states none; the 30-step default lives in the repo, not the publication.
- Longpre et al.'s actual memorization-ratio percentages — figure-bound; only the
  definition and directional findings are extractable.
- Chroma's *Context Rot* report (Jul 2025) — deliberately chart-only, **no
  extractable percentages**. Methodologically strong but uncitable for numbers.
- Laban et al.'s quantification of premature answers, answer bloat and
  loss-of-middle-turns — all qualitative in Appendix F. An internal inconsistency
  was noted (GPT-4o sharded 61.3 vs 59.1), and the venue is unconfirmed — treat
  as preprint.
- SWE-agent's per-category failure percentages, including the edit-loop share.
- AgentBench's per-model outcome breakdown — the API-vs-OSS aggregate is the
  finest published granularity.
- **GAIA has no failure taxonomy at all** (authors: *"GAIA does not evaluate the
  trace leading to the answer"*). Any GAIA failure breakdown circulating is from a
  downstream paper.
- AppWorld's repeat-work rate — asserted, no percentage or sample size.
- The overthinking paper's Y-axis units (inferred from a figure, never stated)
  and an internally inconsistent "~3×" claim (its table says 1.57×).
- Anthropic's "90.2% better than single-agent" — a relative delta on an
  undescribed internal eval; no absolute scores, no n.
- **A clean `max_turns ∈ {2,4,8,16}` ablation table does not appear to exist**
  anywhere in that form. The budget evidence in topic 5 is assembled from
  incidental reports, not from a study designed to answer the question.
- "Accuracy degrades past 10–15 tools" and "7–85% loss as tool catalogue grows" —
  traced only to SEO aggregators. **Folklore.**
- A "15.7% step repetition" figure attributed to AgentAtlas — unverified, and that
  paper's trajectory set is synthetic by its own admission. **Do not cite.**
- LangGraph's default recursion limit — docs say 1000 since v1.0.6; the
  widely-quoted "25" describes an older version, unconfirmed from primary docs.
- Mind2Web is **structurally incapable** of showing loop or non-termination
  failures (teacher-forced, ground-truth action history injected per step).
  Including it in a looping-evidence list would be a category error.

Additionally, several topic-5 sources are very recent preprints (2026 arXiv IDs)
with correspondingly thin replication. The load-bearing budget claims — ReAct's
own 7/5 limits, AgentBench's median-6 completed trajectories, SWE-agent's
median-12 successes — are the older and better-established ones, which is
fortunate, since they are the ones the recommendation rests on.

**Specific unverified items inside the researched topics:**

- **AttributionBench** (Li et al., 2024) — that a fine-tuned GPT-3.5 reaches only
  ~80% macro-F1 on binary "does this evidence support this claim". Read in a
  secondary summary only. Directly relevant to 4b (your verifier is fallible);
  worth confirming.
- **Anthropic Citations "up to 15% recall accuracy" and Endex "10% → 0%"** —
  vendor and customer reported, no published methodology, not independently
  replicated.
- **The Kalai et al. confidence-target prompt's effect size** — the device is
  specified verbatim in the paper; a controlled measurement of how much it raises
  abstention is not something I located, and may not exist.
- **GPT-5 System Card abstention column** — derived by me as
  `1 − accuracy − hallucination`; not printed as such.
- **My reconciliation of Attribute-First vs post-hoc citation** (that they test
  restriction vs ordering under one name) is my interpretation, not a claim
  either paper makes.
- **The 7.4-point abstain-clause cost being a worst case for registry-lookup
  workloads** — extrapolated from the paper's Pop-QA vs CommonsenseQA domain
  split. Plausible, unmeasured on your task.

**Two lookups begun and not completed** (declined mid-session, correctly not
retried): Self-RAG's citation-precision figures, and the per-prompt abstention
rates in "When Not to Answer: Evaluating Prompts on GPT Models for Effective
Abstention in Unanswerable Math Word Problems"
([arXiv:2410.13029](https://arxiv.org/abs/2410.13029)). The latter is the most
directly on-point missing item for topic 2 — a controlled comparison of
abstention *prompt framings*, which is exactly the gap left by Kalai et al. being
**[argued]** rather than **[measured]**.
