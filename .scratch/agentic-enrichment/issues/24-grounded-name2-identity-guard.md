# 24 — The grounded resolver's identity guard is `name1`-only, so Name 2 proposals ship unchallenged

Type: task
Status: DONE 2026-08-29 - implemented and measured on the 21
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

## Resolution (implemented 2026-08-29)

**Q1 - does `canonical_preserves_identity` already refuse the two observed failures? Yes - and it
also refuses the lane's best work, so it could not be used as-is.**

Measured before writing anything:

| proposal | raw comparator | correct verdict |
|---|---|---|
| `Forensic Science Div` -> `Forensic Services Laboratory` | refuse | refuse OK |
| `Baytown Refinery Laboratory` -> `Baytown Refinery` | refuse | refuse OK |
| `Weapons Div` -> `Weapons Division` | **refuse** | accept WRONG |
| `Dept of Chemistry` -> `Department of Chemistry` | **refuse** | accept WRONG |
| `Mech Eng Dept` -> `Department of Mechanical Engineering` | **refuse** | accept WRONG |

`_token_covers` requires a prefix relation of length >= 4, so `div`/`division` and `dept`/
`department` read as **distinctive-token mismatches** rather than as the same word. A department
string is abbreviated far more often than an organisation name is, which is why this bites Name 2
and not Name 1.

**Q2 - what is the rule?** Three things, each forced by a measured failure rather than chosen:

1. **Expand abbreviations on both sides first.** `department_preserves_identity` runs
   `expand_abbreviations` before delegating. This is ticket 19's finding in mirror image: the
   function exists and was not reaching the place that needed it.
2. **A unit-type word may be ADDED but never dropped or changed** (`_UNIT_TYPE_ADDABLE`, kept
   separate from `_ORG_TYPE_ADDABLE` so Name 1 is untouched). Gaining a unit type states what the
   slot left implicit; losing one changes what the value names. The asymmetry is the whole guard.
3. **Name 1's words are addable** (`parent_name=`). A department names a unit *of* something, so a
   proposal that spells out the parent has stated context, not changed the unit. Without this the
   guard refuses `Weapons Div` -> `Naval Air Warfare Center Weapons Division` - a **registry-verified
   answer carrying `ror.org/03cap2a49`** whose four "new" words are Name 1, in the same record.

One abbreviation was missing outright: `Mgmt` was absent from `_ABBREV_MAP`, so
`Department of Supply Chain Mgmt` -> `...Management` read as a token swap. Added beside `Grp`, `Svcs`
and `Div` - the same class, and the table's own stated test ("neither token has a competing
expansion in an organisation name") holds.

**Q3 - what does a refusal cost?** Measured on the real population rather than argued. All 7 records
of the 21 whose Name 2 the live run actually changed, replayed through the guard:

```
REFUSE  S2_02  'Baytown Refinery Laboratory'     -> 'Baytown Refinery'                            llm:provisional
REFUSE  S3_11  'Center for Medical'              -> 'For Medical'                                 (null provenance)
REFUSE  S3_16  'Forensic Science Div'            -> 'Forensic Services Laboratory'                llm:provisional
KEEP    S3_13  'Orange County Water Lab'         -> 'Orange County Water Laboratory'              input:low
KEEP    S3_14  'Orange County Water Lab'         -> 'Orange County Water Laboratory'              input:low
KEEP    S3_15  'Weapons Div'                     -> 'Naval Air Warfare Center Weapons Division'   ror:verified
KEEP    S3_17  'Department of Supply Chain Mgmt' -> 'Department of Supply Chain Management'       llm:provisional

kept 4   refused 3
```

**All three refusals are the wrong values. All four keeps are correct, including the
registry-verified one. Zero correct values lost.** An earlier iteration of the predicate refused 5
of 7 - the two false refusals are what forced rule (3) and the `Mgmt` entry, and they were found by
running the real proposals, not by reasoning about them.

Ticket 14 recorded that of the lane's 8 resolutions only 4 were clean. Both of the unclean ones this
ticket named are now refused.

A refusal leaves the record's own input value in place (same as the Name 1 guard) and is recorded as
`result.dropped[field] = "identity_not_preserved"`, already logged at `grounded_guard_dropped`.

**Q4 - visibility.** Left to ticket 21. The drop is logged today; what is missing is a trace
vocabulary that says *proposal refused by guard* rather than *lane declined*, and that is 21's job.

### The change

- `utils/text_utils.py` - `_UNIT_TYPE_ADDABLE`, `department_preserves_identity()`, an optional
  `extra_addable=` on `canonical_preserves_identity` (default empty, so no existing caller changes),
  and `Mgmt` -> `Management` in `_ABBREV_MAP`.
- `enrichment/grounded_resolver.py` - the guard branches per field; Name 1 keeps the comparator it
  had, byte for byte. Module docstring updated: it enumerates the guards and had to stay true.
- `tests/test_canonical_identity.py` - 13 tests, every case a real proposal from the live run.
  Includes `TestTheName1GuardIsUnchanged`, which pins that neither the expansion nor the widened
  vocabulary leaks into Name 1.

### One existing test changed

`test_a_name2_matching_name1s_own_id_is_refused` passed `name2="NASA HQ"` with a proposal of NASA's
full name. The new guard refuses that outright - dropping `HQ` is the same failure as `Baytown
Refinery Laboratory` -> `Baytown Refinery` - so the proposal never reached the own-entity check the
test is about. Changed to the bare acronym, which passes the guard as a legitimate expansion, so the
condition under test still fires. The test's own assertions are unchanged.

### Verification

`5 failed, 2858 passed, 5 skipped` - the documented baseline's same five pre-existing failures.

### Noted, not done

`HQ` -> `Headquarters` is still not expanded, so `NASA HQ` -> `NASA Headquarters` would be refused.
A false refusal costs a missed improvement, not a wrong value - the input survives - and no record
in the measured 21 hits it. Belongs with any future widening of `_ABBREV_MAP`, not here.

Applying the expansion to the **Name 1** guard may well be right for the same reasons. It changes
settled behaviour on a different population and is deliberately not done here.
