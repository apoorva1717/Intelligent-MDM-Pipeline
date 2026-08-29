# 16 — `flagged_fields` can never name `ror_id`, `lei_id` or `record_type`

Type: grilling
Status: open
Blocked by: —

## The finding (verified 2026-08-29, surfaced by ticket 02)

`enrichment/flags.py::_FIELD_ORDER` is:

```
('name1', 'name2', 'name3', 'name4', 'name5', 'domain', 'contact', 'email', 'address')
```

`_sorted_fields` filters against it and **silently drops** anything absent. Probed on current `main`:

```
_sorted_fields({'ror_id','lei_id','record_type','name1'})  ->  ['name1']
render({'source-conflict': ['ror_id','name1']})['flagged_fields']  ->  ['name1']
```

So the three fields a reviewer most needs pointed at — the two registry identifiers and the
record type — **cannot appear in `flagged_fields` at all**, even when a flag was raised precisely
about them. `source-conflict` is the clearest case: it exists to say the registries disagreed,
and the column that says *about what* discards the answer.

## Question

1. **Is the omission deliberate?** `_FIELD_ORDER` reads as a name/address slot order — the
   identifiers may simply never have been considered, or may have been excluded because they are
   not slots a human edits in SAP. Check the history and the README flag catalogue.
2. **Silent drop, or raise?** Filtering unknown names without complaint is what let this sit
   undetected. If a field name reaches `_sorted_fields` that the order does not know, that is
   either a typo or a gap — both worth failing loudly in tests.
3. **What is the blast radius of adding them?** `flagged_fields` is an exported column
   (`api/output_columns.py`) that downstream consumers and the `/issues` audit read. Widening the
   vocabulary changes output for existing records.

## Why it matters now

Ticket 03's scorer classifies a wrong value as *silent* when the field is absent from
`flagged_fields`. Under current behaviour **every identifier error scores as silent** — the eval
would report a fabricated defect class. 03 must special-case these fields or this must be fixed
first.

Ticket 02's decision 5 defines `silent_error` (wrong AND unflagged, target zero) as one of only
two flag numbers scored. That metric is unmeasurable for identifiers until this is resolved.
