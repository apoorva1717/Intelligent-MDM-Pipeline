# 08 — How does a trajectory become provenance and trace?

Type: grilling
Status: open
Blocked by: 04

## Question

Decision 12 puts trajectory observability in scope from day one. Decide what gets recorded, where.

1. **Provenance events.** `grounded_resolver` records the model's query as an event and attributes
   the final value to the registry. Does *every* agent step become a provenance event, or only the
   terminal proposal plus the confirming registry call? (217 events for 50 records is the current
   volume; an 8-step lane could multiply that.)
2. **What does the `Name 1 Provenance` column say** for a value the agent proposed and ROR
   confirmed? Decision 1 says this must render `ror:verified` — the registry authored it, the
   agent only supplied the query. Confirm the column cannot leak the agent's involvement, and that
   the *event log* still records it.
3. **Trace lines.** What shape is an agent step on the `trace.jsonl`? What does
   `retry_trace_report.py`'s sibling render — one row per record with the step count, or one row
   per step?
4. **What is recorded when the agent refuses?** A refusal is a first-class outcome and needs to be
   as legible as a success, or nobody will be able to tell "declined honestly" from "crashed".

## Notes

Consumers: `tools/run_diff.py` (compares every `RESPONSE_COLUMNS` field), the finalisation
assertion in `finalise` (raises on an invalid provenance string), and the thesis measurements.
