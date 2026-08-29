# 24 — The grounded resolver's identity guard is `name1`-only, so Name 2 proposals ship unchallenged

Type: task
Status: open
Blocked by: —
Source: ticket 14, gap 2 — surfaced by the live re-measurement, not predicted from code

## The finding (verified against the working tree, 2026-08-29)

`enrichment/grounded_resolver.py:588`:

```python
if field == "name1" and not canonical_preserves_identity(
    originals.get(field), value,
):
    result.dropped[field] = "identity_not_preserved"
    del proposals[field]
```

`originals` is built two lines above as `{"name1": name1, "name2": name2}` — the Name 2 original is
**already in hand**. The guard simply never asks about it. So every `name2` the model proposes
reaches the write path without any check that it still denotes the same unit.

The comment directly above the guard states its own purpose:

> a proposal the identity guard refuses must not be used as a REGISTRY QUERY either […] which is
> how a wrong entity acquires a real identifier, which is the one outcome worse than not resolving.

That reasoning is not `name1`-specific. A Name 2 proposal is re-verified against ROR the same way,
so a drifted department name can acquire a real `ror_id` by exactly the route the comment describes.

## Confirmed shipping wrong values on live evidence

Both from the ticket-14 live run (real search results, not the poisoned cache):

| record | input Name 2 | shipped Name 2 | what changed |
|---|---|---|---|
| S3_16 | `Forensic Science Div` | `Forensic Services Laboratory` | the **unit type** — a division became a laboratory |
| S2_02 | `Baytown Refinery Laboratory` | `Baytown Refinery` | the unit word **dropped entirely** — now names the site, not the lab |

A Name-2-scoped identity check refuses both. These are not near-misses; S2_02 changes what kind of
thing the record is about.

Of the 21 addressable records the lane resolves 8, of which only 4 are clean. **Two of the four
unclean ones are this defect.**

## The change

Extend the guard to `name2`. The open question is whether `canonical_preserves_identity` is the
right comparator *as-is* for a department string, or whether Name 2 needs its own predicate.

Reasons to think it needs its own:

- Name 1 identity is about the organisation's *name*; Name 2 identity is substantially about the
  **unit-type word** (`Division` / `Laboratory` / `Department` / `Institute` / `Center`). Both
  observed failures are unit-type failures, and a whole-string comparator may score them as similar.
- `clean_name2_phrase` already exists and deliberately strips structural words for *querying*.
  A guard must not reuse it: the words it strips are the ones that carry the identity here.

## Questions

1. Does `canonical_preserves_identity` already refuse S3_16 and S2_02? **Measure before writing a
   new predicate** — if it does, this is a one-line change and nothing more.
2. If it does not: is the rule "the unit-type word may not change or disappear", or something
   broader? Derive it from the observed failures, not from taste.
3. What does a refusal cost? A dropped `name2` proposal leaves the input value in place. Confirm
   that is the behaviour (it is what `name1` refusal does) and that no downstream lane treats the
   absence as a clear.
4. Does the refusal need to be visible? `result.dropped[field]` is logged but the ticket-21 trace
   vocabulary should be able to say *proposal refused by guard* rather than *lane declined*.

## Constraints

- The guard runs **before** the registry re-verification on purpose. Keep it there — a value the
  guard would refuse must never become a registry query.
- Flags are rebuilt in `finalise`; this writes no flag.
- Before/after must be measured on the same 21 addressable records, reporting **both** the values
  it refuses and any correct value it costs.

## Why this one first

It is a **correctness** fix, not a recovery one — the pipeline ships wrong departments today, and
the count of wrong values can only go down. Ticket 25 (`site:` term) increases what the lane
proposes; doing 25 first would scale this defect before fixing it.
