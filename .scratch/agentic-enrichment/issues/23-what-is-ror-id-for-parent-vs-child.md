# 23 — What is `ror_id` on a SAP customer record *for*? Parent, or the site you actually ship to?

Type: grilling
Status: open
Blocked by: —

## Why this is now the central question

Two independent measurements, from two different tickets, land on the same thing — and **no
retrieval change fixes either of them.**

**Ticket 11, gate 3.** Every one of the 9 local-rescore rejections had ROR score exactly 1.000
against a local score of 0.21-0.47. Six were ROR offering the **parent VISN network** in place of
the specific VA medical center (`Vamc Miami Visn 8` -> `VA Sunshine Healthcare Network`).

**Ticket 19, domain-first.** The strongest recovery strategy found 50 hits — but only **3 of 50**
have a ROR name that agrees with the record's name under the pipeline's own comparator. Of the 50:
8 are the same entity, 14 are the corporate parent, and 15 share **no distinctive token at all** —
twelve VA medical centres in different states all resolving to `United States Department of
Veterans Affairs`; `SLAC National Accelerator` -> `Stanford University` (SLAC has its own ROR
record); `US Department of Energy` -> `Naval Nuclear Laboratory`.

So the registries *do* hold an organisation reachable from these records. It is just **not the
organisation the record names.** The gate is not wrong to refuse it — under the current definition.
The question is whether the definition is right.

## The question

For a **customer master record** — a shipping and billing address for a specific site — what should
`ror_id` / `lei_id` identify?

1. **The exact entity named**, refusing a parent (today's behaviour). Precise, and leaves ~74% with
   no identifier.
2. **The nearest registered ancestor**, recorded as such. Higher coverage, but `ror_id` stops
   meaning "this record's organisation" and Phase 2 must not merge two sites on a shared parent id.
3. **Both**, in separate fields — `ror_id` for the exact match, an explicit parent/ancestor field
   beside it. More schema, no ambiguity.

## What has to be decided alongside it

- **Phase 2 blast radius.** Ticket 02 established that `adjudicator.py` guards *divergent* ids and
  is **blind to a shared wrong id** — the convergent case is a silent wrong merge with no guard.
  Option 2 would put twelve VA medical centres on one identifier. That is the failure mode.
  (Note ticket 15 measured that Phase 2 does not currently read `record_type`; it *does* read ids.)
- **Provenance.** A parent match is not the same evidence as an exact match and must not reach the
  same confidence. Scheme B has no vocabulary for "ancestor of" today.
- **What the business does with it.** If `ror_id` feeds deduplication and hierarchy roll-up, the
  answer may differ per consumer — which argues for option 3.

## What this is not

Not a retrieval problem, and not fixable by querying differently. Ticket 19 tested eight query
formulations; the union recovered 12 of 175 lost records strictly (8 distinct organisations across
300). The records reach a registry entry. The disagreement is about **granularity**, and it is a
definition question, not a matching one.
