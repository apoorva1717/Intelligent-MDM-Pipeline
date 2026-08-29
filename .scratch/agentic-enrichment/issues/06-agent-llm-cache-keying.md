# 06 — How is a multi-turn agent LLM call cached?

Type: grilling
Status: open
Blocked by: 04

## Question

The `llm` cache namespace currently keys on "SHA-256 of deployment + API version + sampling params
+ **both prompts**" — a shape built for a single-shot call. A ReAct loop's step *N* depends on the
results of steps 1..N-1, so there is no single "prompt" to key on.

1. **Is keying step N on the full serialised message history correct?** It is still a pure function
   of the request *if* every prior tool result was itself served from cache — which decision 8
   guarantees. Confirm that reasoning holds, or find where it breaks.
2. **What invalidates a trajectory?** Editing the system prompt should invalidate every step
   (correct — the recorded answer answered a different question). Does adding a *seventh tool*
   invalidate trajectories that never called it? Cheap-and-correct says yes; cheap-and-wasteful
   says the tool schema is part of the key.
3. **Does the `tool_call_id` leak into the key?** OpenAI generates a fresh id per turn. If it lands
   in the serialised history it makes every key unique and the cache hit rate goes to zero — the
   exact failure mode the research measured in LangGraph's node cache. This must be normalised out.
4. **Is a partial trajectory usable?** If steps 1-4 are cached and step 5 misses under
   `CACHE_FROZEN`, is that an `evidence-unavailable-frozen` on the record, or does the whole lane
   abort?

## Why it matters

This is the mechanism behind decision 11 — the `run_diff` gate's precondition is
`evidence_network_calls == 0` on a warm run, and an agent whose LLM calls never cache breaks it.
