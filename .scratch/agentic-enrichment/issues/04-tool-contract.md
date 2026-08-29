# 04 — The tool contract

Type: grilling
Status: open
Blocked by: 01, 12

## Question

Define the six tools precisely enough to implement: name, argument schema, return shape, failure
shape, and the cache namespace each routes through.

1. **Signatures.** What does `ror_lookup` take — a name, or a name plus country plus city? Does the
   agent get to pass a raw query string, or a structured argument set? (Looser input = more agent
   freedom = more ways to waste a step.)
2. **Return shape.** Does a lookup return the raw registry payload, the scored candidate list, or a
   trimmed summary? Raw is honest but expensive in tokens; trimmed is cheaper but is a *decision*
   made outside the agent's view, which is exactly what decision 3 wants — so where is the line?
3. **Failure shape.** What does a tool return when the provider is down vs when there are genuinely
   no results? `search.base.SearchUnavailable` already draws this distinction and the agent must
   see it, or it will read an outage as "this organisation does not exist".
4. **`propose_and_verify`.** Is the terminal a *tool* the agent calls, or a structured final message?
   If it is a tool, the agent can see the verification verdict and retry with a better query —
   which is the whole point of the lane. Confirm that and define what the verdict returns on refusal.
5. **Cache routing.** Each tool maps to an existing namespace (`ror`/`gleif`/`wikidata`/`serp`/
   `fetch`). Confirm every one goes through `utils.cache` with a key that is a pure function of the
   tool's arguments — decision 8, and the `run_diff` precondition.

## Notes

Blocked by 01 because whether `response_format` composes with `tools` changes how the terminal is
expressed.
