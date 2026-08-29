# Before / after — the same 100 records, 2026-08-29

**before** `.scratch/agentic-enrichment/tmp/run100b.json` — 12:07 today, after the search fix,
before tickets 17 / 20 / 21 / 24.
**after** `logs/compare/after.json` — current `enrichment-spike` (`a3d7621`).
Batch: `docs/thesis/chemspeed_us_100.xlsx`, 100 records, joined on `(name1_original, city)`,
100/100 matched.

## The headline

| metric | before | after | delta |
|---|---:|---:|---:|
| `record_type` decided | 27 | **72** | **+45** |
| domain populated | 11 | **80** | **+69** |
| registry identity (ROR or LEI) | 24 | 26 | +2 |
| flagged "Name 1 left exactly as supplied" | 73 | **9** | **−64** |
| fell through to Tier 3 | 73 | 50 | −23 |
| page reads attempted | 0 | 54 | +54 |
| page corroborated | 0 | 21 | +21 |
| evidence network calls | 286 | 246 | −40 |

**No regressions.** 0 ROR ids lost, 0 LEI ids lost, 0 domains lost, 0 records whose `record_type`
went from decided back to `unknown`.

## Attribution — the deltas are bigger than the four tickets, and most of it is not them

The raw diff mixes three causes. Only one of them is separable cleanly, and it is separated here
rather than claimed.

### Ticket 17 in isolation: **+28 of 100**

`classify()` is a pure function of the record's evidence, so replaying the **before** run's own
names and own registry evidence through the **current** classifier holds retrieval fixed:

```
record_type changed on 28 of 100   —  all 28 decided by `legal_form`
record_type decided: 27 -> 55  attributable to ticket 17
record_type decided: 27 -> 72  observed end to end
=> the remaining +17 comes from better NAMES, not from the classifier
```

Chemspeed is a US corporate list, so the legal-suffix source fires far more here than on the
labelled S2/S3 set (where it was +21/200). Consistent, not contradictory.

Two records flipped `research_institution` -> `company`: **`1st Source Research, Inc`** and
**`Analytical Laboratory Services, Inc`**. Both are correct, and both are exactly the defect ticket
15 finding B named — the keyword heuristic reading "Research" / "Laboratory" and overruling what the
name plainly says.

### Retrieval — the `.env` repair plus ticket 20's cache purge: the rest

```
domain_from_serp       0 -> 37
domain_from_page       0 -> 7
domain_from_witness    0 -> 2
page_reads_attempted   0 -> 54
tier3_count           73 -> 50
```

The page-read lane was **entirely dead** before — not degraded, dead — because SERP handed it no
candidates to open. That is the single largest behavioural change on this batch, and it is a
consequence of fixing the environment and purging the poisoned cache, not of any of the four tickets
except insofar as ticket 20 is what makes the purge permanent.

Network calls *fell* (286 -> 246) while the pipeline did far more work: the caches are healthy
rather than poisoned.

### Tickets 24 and 21 are not measurable on this batch

Chemspeed has **no Name 2 values** — 0 populated before, 0 after — so the Name 2 identity guard is
unexercised here. Its evidence remains the replay over the 7 live S2/S3 proposals (kept 4, refused
3, zero correct values lost). Ticket 21 is the instrument, not a metric.

## What is NOT yet good — the domain gain is not all real

80 domains, but by confidence: **13 verified, 44 provisional, 23 low**.

Eight are directory or aggregator sites, not the organisation's own:

| record | domain |
|---|---|
| 3BC, Inc. | facebook.com |
| Adaptive Innovations Corp. | facebook.com |
| Adello Biologics, LLC | linkedin.com |
| The Alfieri-McBee Corp. | thebluebook.com |
| American Coatings Association | american-coatings-show.com |
| Apollodyne LLC | sbir.gov |
| Advansix Inc. | advansix.com *(low, but correct)* |
| Allnex USA Inc | allnex.com *(low, but correct)* |

**All eight carry `domain-unverified`.** The ownership guard is doing its job — it is accepting the
candidate and telling a reviewer not to trust it, which is the designed behaviour, not a silent
error. But roughly a third of the domain gain is sitting at `low` confidence and a reviewer has to
look at it.

Flag codes went **up**, correctly: `domain-unverified` 0 -> 24, `unverified-inference` 0 -> 13. What
fell is the *failure* flag — "Name 1 left exactly as supplied because the canonical form could not
be determined", 73 -> 9. Fewer records the pipeline could say nothing about; more records where it
says something and marks how far to trust it.

## Caveats

- **Not a controlled A/B.** The before-run predates both the environment repair and the tickets.
  Ticket 17 is isolated by replay; everything else is attributed to "retrieval" as a bundle and is
  not split further, because nothing available separates the `.env` repair from the cache purge
  after the fact.
- **`decided` is not `correct`.** Chemspeed carries no `record_type_hint`, so this batch can only
  show that the legal-form source *fired*, not that it was right. Its precision (1.000 on 55 of 200
  labelled records) comes from the labelled set, and is not re-established here.
- The `after` run is warm on registry/page/wikidata and was cold on SERP, which is the expected
  state immediately following ticket 20. `tools/run_diff.py` needs a second warm run before its
  `evidence_network_calls == 0` precondition can hold.
