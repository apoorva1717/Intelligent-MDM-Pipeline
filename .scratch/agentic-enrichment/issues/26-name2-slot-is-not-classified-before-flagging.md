# 26 — "could not be canonicalised" is reported for slots that hold nothing to canonicalise

Type: task
Status: open
Blocked by: —
Source: measured on the merged 200-record sample, 2026-08-29

## The finding

**111 of 200 records (56%) are flagged for review.** That is the number that reads as "something is
terribly off", and roughly a third of it is noise the reviewer cannot act on.

The 57 flags whose reason begins `Name 2:` or `Name 1 and Name 2:` — *"left exactly as supplied, the
canonical form could not be established"* — classified by **what the Name 2 slot actually holds**:

| what is in the slot | n | example |
|---|---:|---|
| **E. a genuine department, unresolved** | **23** | `Baytown Refinery Laboratory` under `ExxonMobil` |
| F. a company name, not a department | 17 | `Edata Solutions` under `Economic Policy Institute` |
| A. Name 1 overflow *(amended: 6 of 8 are ticket-28 part B, see below)* | 8 | `Engineering Solutions of Sandia` under `National Technology &` |
| C. a trading name / fragment | 5 | `DBA Community Fuels` under `American Biodiesel Inc` |
| B. an admin or form label | 3 | `Ref#` under `Genzyme Corp`, `Email To:` under `Celanese Corp` |
| D. a duplicate of Name 1 | 1 | `Veracyte, Inc. - South San Francisco, CA` under `Veracyte, Inc.` |

**Only 23 of 57 are a department the pipeline failed to canonicalise.** The other **34 — 30% of the
entire review load — are slots with no department in them at all.**

## Why it is wrong, precisely

The flag conflates two different facts:

- *"I could not establish the canonical form of this department."* — actionable. A reviewer opens
  `Baytown Refinery Laboratory` and can supply or confirm the right form.
- *"This slot does not contain a department."* — **not** actionable as written. A reviewer opens
  `Ref#` or `Email To:` and there is nothing to canonicalise; the message is false on its face.

Ticket 14 measured the same population from the other side and reached the same number: *74% of
unresolved Name 2 values are admin desks, phrases naming nothing, or Name-1 overflow.* That finding
was used to argue Tier 2B should not be revived. It applies equally here, and nothing acted on it.

## The compounding effect: 8 of the 34 are a defect telling on itself

> **Amended 2026-08-29.** The classification below is right that these 8 are not a
> department. It is wrong about *why*, because the probe read post-repack slot values
> against pre-repack flags. See `research/uc0-repack-recreates-the-split.md`.
>
> **6 of the 8 are the ticket-28 part-B defect: the flag does not name the string the
> slot shows.** UC 0 detected the Sandia split, merged it (`is_overflow=true`, `high`),
> and `compute_flags` then ran against the *merged* block, where Name 2 held `LLC`:
>
> ```
> after UC0 merge  name1='National Technology & Engineering Solutions of Sandia'  name2='LLC'
>                  compute_flags() flags name2 -> 'LLC'
> after repack     name2='Engineering Solutions of Sandia'
> ```
>
> The flag was raised about `LLC` — a legal suffix stranded in a name slot, which *is*
> a real finding. It is displayed against `Engineering Solutions of Sandia`, which is
> not. So this row belongs under **C. a trading name / fragment**, not A, and the
> reviewer-facing defect is the mislabelling, not the classification.
>
> The remaining 2 (`Expeditors International of`, `DOH - Bureau of`) are as described.
>
> **These 8 are therefore not removable by "fixing Stage 0" — Stage 0 is not broken.**
> Ticket 28 part B removes 6 of them; the other 2 remain this ticket's to classify.

The `A. Name 1 overflow` rows are the ticket-28 defect surfacing as a Name 2 complaint. `National
Technology &` + `Engineering Solutions of Sandia` is **one name split across two slots**. The
pipeline is reporting "the department could not be canonicalised" about the second half of an
organisation's name. Fixing Stage 0 removes these 8 flags *at the source* and fixes the names.

## Questions

1. **Is a distinct issue code the right answer, or silence?** A slot holding `Ref#` is arguably a
   data-quality finding worth reporting — but as *"Name 2 does not contain a department"*, not as
   *"the canonical form could not be established"*. Check the `/issues` G-code catalogue before
   inventing a code; UC 7 / UC 8 already cover contact and reference material landing in name slots.
2. **Where does the classification belong?** It must not be a second writer. `compute_flags()` runs
   once from `finalise` and rebuilds from the record's settled state, so the *classification* has to
   be available to it — as evidence recorded by whichever stage already reads the slot
   (`preprocess` handles UC 6-12/14/15 and already recognises several of these shapes), never as a
   flag written by a tier.
3. **Does `F. a company name in the Name 2 slot` (17) belong here at all?** `Edata Solutions` under
   `Economic Policy Institute` and `CVG International America` under `CVG Ferrominera Orinocco CA`
   look like two organisations in one record, which is a different and more interesting defect than
   a department that would not resolve.
4. **What is the target?** State it before changing anything: what fraction of 200 *should* be
   flagged, and what does a reviewer do with each remaining code.

## What is NOT claimed

The other 66 flags (`Name 1: ...`) are not covered here, and most look legitimate: 34 "left exactly
as supplied" with no registry identity, 13 `entity-superseded` (real and useful — Merck Sharp &
Dohme names a merged entity), 4 `unverified-inference`. **14 of the 66 already carry a ROR or LEI
id**, which is worth a second look but is not this ticket.

Nor does this claim the flags should simply be suppressed. A record whose Name 2 is `Ref#` **has** a
data problem. It is the wrong problem being reported.

## Evidence

`logs/compare/enriched_samples_200.xlsx` (200 rows, S2+S3 merged);
classification probe re-run after correcting a `\b`-after-prefix bug in the first pass that
misfiled `Laboratory` as unclassified.
