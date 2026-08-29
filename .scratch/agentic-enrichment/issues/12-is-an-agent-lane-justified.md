# 12 — Is an agent lane justified at all?

Type: grilling
Status: open
Blocked by: 03, 11, 13

## Question

The decision this whole map now turns on. Given the funnel (11), the eval baseline (03), and the
measured effect of the gate redesign (13): **does a bounded agentic lane still earn its place?**

1. **What is left unrecovered** after the cheap fixes? Decompose the residual into:
   - *query formulation* failures — the registry holds the entity, the query never found it.
     **Agent-shaped.**
   - *registry coverage* failures — the entity is genuinely in no registry. **Not agent-shaped;
     no amount of reasoning conjures a record that does not exist.**
   - *threshold calibration* failures — fixed by tuning, already addressed by 13.
2. **Is the agent-shaped residual large enough to justify the cost?** Cost is not only tokens: it
   is the ticket-09 risks — abstention collapse under retrieval, and a planner prior that corrupts
   the query before any verifier sees it.
3. **If yes, is it a ReAct loop or a bounded retry?** Ticket 09 found anchoring converges around
   step 4, long context degrades accuracy 0.92 -> 0.68, and ReAct's own budget was 7/5 with <1.5%
   of correct trajectories using it. A 2-3 attempt bounded retry over the *existing* single-shot
   `grounded_resolver` may capture most of the value at a fraction of the risk.
4. **If no**, close 04-08 and 10 as out of scope and record why.

## Notes

This ticket may end the map. That is a legitimate outcome, not a failure.
