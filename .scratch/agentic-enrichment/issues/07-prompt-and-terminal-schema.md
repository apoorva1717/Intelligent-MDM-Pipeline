# 07 — The prompt and the terminal schema

Type: prototype
Status: open
Blocked by: 04

## Question

Write a rough, concrete first version of the system prompt, the six tool descriptions, and the
terminal output schema — something to react to, not something to ship.

The question the prototype answers is **how the lane should behave**, specifically:

1. **How is the "insufficient evidence" terminal made attractive?** Decision 8 makes it a
   first-class success. If the schema or the prompt makes it feel like failure, the model will
   fabricate to satisfy the shape — the single biggest driver of agent fabrication in practice.
2. **Does the terminal require citing which evidence supports the proposal?** `grounded_resolver`
   already requires an `evidence_index`. Carry that forward, extend it, or replace it?
3. **How much of the record does the agent see?** All slots, the address, the contact? More context
   means better queries and more ways to anchor on the wrong field.
4. **How is the model told that its proposal will be re-verified?** Stating the gate explicitly
   tends to change behaviour — it should propose *checkable* names rather than plausible ones.

## Deliverable

A prototype file linked from this ticket, plus 3-5 hand-picked hard records from the chemspeed
batch walked through it by hand.
