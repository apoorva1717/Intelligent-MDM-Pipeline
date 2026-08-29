# 03 — Build the Phase 1 eval and get the baseline

Type: task
Status: open
Blocked by: 02

## Question

Build `eval/enrich_eval.py` on the pattern of `eval/dedup_eval.py`, label ground truth for
`docs/thesis/chemspeed_us_100.xlsx` (100 rows, already the batch every other measurement uses),
and produce the **baseline numbers**.

Deliverables:

- Labelled ground truth committed as a fixture.
- `eval/enrich_eval.py` scoring the metrics 02 settled.
- A baseline report answering the three questions nobody can currently answer:
  - What fraction of records reach a registry-authored identity today?
  - Of the values the pipeline writes, what fraction are wrong?
  - **What fraction of records would enter the agent lane** under the decision-6 entry gate
    (Tier 1 miss OR weak match OR refused ambiguity)?

## Why it matters beyond measurement

The third number is the input to the cost model (decision 10) and to the entry-gate predicate (05).
There are currently **no run artefacts on disk**, so this is the only way to get it.

## Notes

Ground truth labelling is the expensive part and is HITL — the agent can propose labels from
registry lookups, but a human confirms. Consider labelling a stratified subset first if 100 rows
proves slow.
