# Golden-set evaluation — 99 records against the solved reference

**Run:** `enrichment-spike`, 2026-08-29, live registries and LLM, 99 records in 322s.
**Inputs:** `docs/SAMPLE_DATA/testall100_SOLVED_REFERENCE_v1 (1) (1).xlsx`
**Artefacts:** `logs/golden2/` (`golden_input.xlsx`, `golden_enriched.xlsx`, `golden_eval.json`, `golden_eval.md`)
**Harness:** `scripts/eval_golden.py`, `tools/golden_eval.py`, `scripts/golden_root_cause.py`, `tests/test_golden_eval.py`

---

## Headline

| | |
|---|---|
| **records fully passing** | **6 / 99 (6.1%)** |
| **cells matching** | **3684 / 3928 (93.8%)** |
| graded columns | 40 of 67 — the reference declares the other 27 `skip` |
| failing cells | 244 |

**The 6.1% is the more alarming number and the less informative one.** A record
passes only if all ~40 of its graded cells pass, so one systematic defect that
touches many rows caps the record score no matter what else is right. Two such
defects (§1, §2) touch **57 of the 99 records** and account for **54 of the 244
failing cells** — but only **8 records fail on those alone**, so fixing both
takes the record score from 6 to 14, not to 63. The cell score is the one that
tracks quality; the record score mostly measures how many defects a row can
accumulate.

Records that passed cleanly: `13119937`, `13140331`, `13141073`, `13213520`,
`13223481`, `13335208`.

---

## Which file was run, and why

`docs/SAMPLE_DATA/test-all-100-original.xlsx` was **not** used as the input.
**75 of its 99 rows have shifted columns** — City holds an issue-code list and a
date, Region holds the eval-set label:

```
$ python scripts/eval_golden.py --check-original
{"rows": 99, "shifted": 75, "clean": 24,
 "examples": ["City='2025-07-02 00:00:00' Region='S2'", ...]}
```

The reference's own Method sheet says its INPUT rows were rebuilt from the
source corpus for exactly this reason, and that claim is independently
confirmed by the check above. The run therefore feeds the reference's INPUT
rows. `--input-workbook` overrides this if the original is ever repaired.

---

## Where the failures are

| column | failed | graded | accuracy |
|---|---:|---:|---:|
| Name 1 | 47 | 98 | 52% |
| Terms of Payment | 43 | 99 | 57% |
| Name 2 | 34 | 94 | 64% |
| Street 1 | 32 | 96 | 67% |
| PO Box | 18 | 98 | 82% |
| Name 3 | 16 | 98 | 84% |
| Building | 15 | 95 | 84% |
| Street 2 | 10 | 93 | 89% |
| Room | 9 | 97 | 91% |
| Name 4 | 6 | 99 | 94% |
| Email, Mail Code | 4 each | 99 | 96% |
| Care Of | 3 | 97 | 97% |
| House Number, Suite, Unloading Point | 1 each | 99 | 99% |

By the *shape* of the disagreement (`scripts/golden_root_cause.py`):

| bucket | n | dominant columns |
|---|---:|---|
| produced nothing where a value is expected | 80 | Terms of Payment(43), PO Box(11), Building(10), Room(6) |
| produced a value the reference says is blank | 34 | Name 2(8), Street 2(8), Name 4(5) |
| same entity, different form | 44 | Name 1(15), Name 2(9), Name 3(7) |
| abbreviation direction | 24 | Street 1(20) |
| different value entirely | 19 | Name 2(8), Name 1(5) |
| produced is a prefix of expected | 18 | Name 1(14), Name 2(4) |
| produced continues past expected | 9 | Name 1(4) |
| punctuation or spacing only | 7 | Email(4) |
| casing only | 5 | Name 1(3) |
| legal form only | 4 | Name 1(4) |

---

# Root causes

## 1. `Terms of Payment` is never read — 43 records

The largest single cause, and a two-line fix. Every one of the 43 is
`expected='NT30'`, `produced=''`.

```python
orchestrator.py:706   "terms_of_payment": record.terms_of_payment_contact,
models.py:208-210     terms_of_payment_contact: ...
                      validation_alias=AliasChoices("Terms of Payment Contact",
                                                    "terms_of_payment_contact")
output_columns.py:92  "terms_of_payment": "Terms of Payment",
```

The output column is **`Terms of Payment`**. The only input aliases are
**`Terms of Payment Contact`** and `terms_of_payment_contact`. A workbook whose
column is named `Terms of Payment` populates nothing:

```python
>>> _rows_to_records([{... 'Terms of Payment': 'NT30'}])[0].terms_of_payment_contact
None
>>> _rows_to_records([{... 'Terms of Payment Contact': 'NT30'}])[0].terms_of_payment_contact
'NT30'
```

**`/enrich/file` is not idempotent on this column.** Feed the pipeline its own
output and the value empties. The fix is to add `"Terms of Payment"` to the
`AliasChoices`, and the guard is a round-trip test: every column in
`RESPONSE_COLUMNS` should be readable back as an input alias.

## 2. An input `PO Box` is used as a flag and then discarded — 11 records

```python
address_processing.py:1044   po_box_present = bool(po_box and po_box.strip())
address_processing.py:1086   if res.po_box_extracted or po_box_present:
                                 res.issue("G3-ADDR-005")     # conflict
                             else:
                                 res.po_box_extracted = pob   # only from a STREET
```

`res.po_box_extracted` is written **only** when a PO Box is found inside a
street field. A record that arrives with its PO Box already in the PO Box
column, and no PO Box in any street, ships with the column **empty**. The input
value is consulted only to decide whether a *second* PO Box is a conflict.

```
13045733  IN PO Box='750314'  ->  OUT PO Box=''
13147626  IN PO Box='750162'  ->  OUT PO Box=''
13084068  IN PO Box='6043'    ->  OUT PO Box=''
```

Same class as (1): a value the record already carried is lost by passing
through the pipeline.

## 3. Content loss, measured

Auditing every input cell against the whole output row, token by token:

| column | value fully lost | partly lost |
|---|---:|---:|
| Terms of Payment | 43 | 0 |
| PO Box | 11 | 0 |
| Street 1 | 1 | 3 |
| Name 2 | 1 | 1 |
| Street 2 | 0 | 5 |
| Street 3 | 0 | 1 |
| **total** | **56** | **10** |

The 10 partial losses are label tokens the pipeline correctly consumes as
markers (`ms`, `rm`, `unit`, `bldg`). **The 56 are real.** 54 of them are
causes (1) and (2).

## 4. Sub-location routing — 39 cells, and it is mostly *routing*, not extraction

Of the 39 `Building` / `Room` / `Unit` / `Suite` / `Unloading Point` /
`Street 2` values the reference expects but the run left blank:

| where the value actually is | n |
|---|---:|
| nowhere — dropped | 15 |
| `Street 2` | 7 |
| `Name 2` | 6 |
| `Street 1` | 5 |
| `Mail Code` | 3 |
| other | 3 |

So **24 of 39 are present but in a different column.** The clearest cases:

```
13185613  expected Building='Dow'  Room='268'      ->  produced Mail Code='Dow 268'
13336363  expected Building='Spieth Hall' Room='1229' -> produced Mail Code='1229 Spieth'
13033507  expected Building='Brooks Hall' Room='314'  -> both still in Street 1
13332345  expected Building='Equad'                ->  produced Name 2='Equad'
```

A campus sub-location is being routed to `Mail Code` or left in `Street 1`
where the reference expects `Building` + `Room`. This is one rule, not 39
defects — and it is the same rule that leaves residue in the name block
(§6 below), so it is counted twice by a per-cell score.

## 5. Name 1 canonicalisation — 47 cells, three distinct causes

**(a) `canonical_preserves_identity` rejects legitimate expansions.** The run
log shows the guard refusing exactly the canonicalisations the reference
expects:

```
[13208652] REJECTED 'US Environmental Protection Agency'
                  -> 'United States Environmental Protection Agency'
[13119937] REJECTED 'Orange County Public Health Lab'
                  -> 'Orange County Public Health Laboratory'
[13337029] REJECTED 'Zoetis Ref Lab Cincinnati' -> 'Zoetis Reference Laboratories'
[13223481] REJECTED '3M Corporate' -> '3M Company'
```

This is the **documented known limit** at `README.md:3631` — the guard "rejects
a corrected typo or an expanded abbreviation, so those records still discard the
right answer". The golden set now quantifies it.

**(b) Casing of acronyms is inconsistent in both directions.**

```
IDEXX Reference Laboratories  ->  Idexx Reference Laboratories   (acronym lost)
Southwest Gas Corporation     ->  Southwest GAS Corp             (word made an acronym)
UT Southwestern Medical Ctr   ->  Utsw Medical Center
VAMC West LA VISN 22          ->  'Vamc West la Visn 22' / 'VA MC West la Visn 22'
The Dow Chemical Company      ->  'the DOW Chemical Company'      (leading lower case)
```

The last two matter most: `the DOW` ships a lower-case leading article, and two
records of the *same* organisation cased differently in the same batch.

**(c) Column width.** 14 Name 1 failures are the produced value being a prefix
of the expected one. See §7 — this is partly the reference's doing.

## 6. Four records where the output is simply wrong

Not systematic, but the most serious individually:

```
13364399  expected Name 1='Wyss Institute'          produced 'Accounts Payable'
13189969  expected 'Texas A&M System Health Science Ctr'  produced 'Scott & White Hospital Modul C'
13348274  produced name block:
          'University of Texas Galveston - University of Texas Medical
           THE University of Texas M Mary Moody Northern Code:'
13345790  produced 'Palo Alto Veterans Institute for Research' TWICE in one block
```

`13364399` is the worst class the pipeline can produce: an admin desk promoted
into Name 1, so the record now names a department instead of an organisation.
`13345790` is a duplicate the UC 12 dedup rule should have cleared.

## 7. The name block, compared as a whole

Per-slot scoring over-counts one boundary disagreement. Joining Name 1–5 and
comparing the whole:

| | n |
|---|---:|
| name block identical | 39 |
| same words, different slots or casing | 3 |
| **name content correct** | **42 / 99 (42%)** |
| genuinely different content | 57 |
| *(per-slot cell failures on the same data)* | *103* |

**103 cell failures collapse to 57 records.** Example — every slot "fails",
and the content is identical:

```
13131947 expected: ['ExxonMobil Technology and Engineering', 'Company']
         produced: ['ExxonMobil Technology', 'and Engineering Company']
         joined:    identical
```

The reference writes name values of **34–38 characters** into Name 1
(`University of California, Riverside`, `California Department of Public Health`,
`Lewis-Sigler Institute for Integrative`). `NAME_FIELD_WIDTH` is 32, against a
real 35-character SAP column. **The reference asserts no width limit at all**,
so it can never agree with any cut the pipeline makes. This is a disagreement
about the schema, not about the name.

## 8. A regression this evaluation caught in my own earlier change

The connector-aware cut added to `chunk_name` earlier in this session moves a
lower-case connector to the head of the next slot. `normalise_case(mode="name")`
capitalises the first token of a name field, so the output shipped:

```
'ExxonMobil Technology' + 'And Engineering Company'      <-- capital A, mid-name
'Lewis-Sigler Institute' + 'For Integrative Genomics'
```

**Fixed** (`utils/text_utils.normalise_case(continuation=…)`, set from
`_uc0_continuation_slots` recorded by the repack): a continuation piece is the
middle of a name, not the start of one, so its leading token is cased as any
other. 6 cells corrected; the score did not move, because those cells were
already failing on the slot boundary. Regression test:
`TestAContinuationPieceIsNotTheStartOfAName`.

---

# Where the reference, not the pipeline, is the problem

The reference's own Method sheet invites this check: *"If your output disagrees
on an un-noted cell, check the record by hand before assuming the reference is
right: this file was authored by one reviewer."*

**~32 of the 244 failures are the reference contradicting documented, tested
pipeline behaviour.** Each is citable:

**Street abbreviation — 20 cells.** The pipeline abbreviates street types and
directionals by design (`address_processing.py:62-81`,
`STREET_TYPE_ABBREVIATIONS`, `DIRECTIONAL_ABBREVIATIONS`; README §output casing).
The reference expects the unabbreviated word:

```
expected 'OLDEN STREET'       produced 'Olden St'
expected 'EAST OTTAWA COURT'  produced 'E Ottawa Ct'
expected 'N TORREY PINES ROAD' produced 'N Torrey Pines Rd'
```

**Name abbreviation — 4 cells, and the reference contradicts itself.** Its
Method sheet says it asserts "abbreviation expansion (MICHIGAN TECH UNIVERSITY
-> Michigan Technological University)", which the pipeline does. But it then
expects `Lab` to stay abbreviated:

```
expected Name 2='Baytown Refinery Lab'  produced 'Baytown Refinery Laboratory'
expected Name 1='Zoetis Ref Lab Cincinnati'  produced 'Zoetis Ref Laboratory Cincinnati'
```

`README.md:830` — Name 1–5 are run through the global `expand_abbreviations()`
map precisely so no output name ships a bare `Lab`, `Univ` or `Dept`.

**Email casing — 4 cells.** The pipeline lower-cases addresses (`orchestrator.py:1057`,
"case-insensitive by RFC and lower case by convention"). The reference keeps the
input casing and grades `Email` as `exact`. The column rule should be `exact_ci`.

**Legal forms — 4 cells, applied inconsistently.** The reference expands
`Corp` → `Corporation` for Genzyme and Avient, but expects `Dow Chemical Co`
(not `Company`); it *adds* `Inc` to `Brigham and Women's Hospital` and *removes*
`LLC` from `Paper Money Guaranty`.

**House numbers — the reference is internally inconsistent.** 42 of its expected
rows extract `House Number`; 31 leave the number inside `Street 1`; **none do
both**. The same street appears three ways across three records:
`'1400 TOWNSEND DR'`, `'1400 TOWNSEND DRIVE'`, `'1400 TOWNSEND DR.'`.

**None of this makes the reference bad.** It is careful work — 87 cell notes
widen or skip exactly the cells one reviewer could not certify, and 0 of them
are orphaned. But its street and legal-form columns encode conventions the
thesis does not specify, and grading against them measures disagreement about
convention rather than quality.

---

# Recommended actions, in order of value

| # | action | cells recovered | confidence |
|---|---|---:|---|
| 1 | Add `"Terms of Payment"` to the input `AliasChoices` | 43 | certain |
| 2 | Write an input `PO Box` through to `po_box_extracted` when no street PO Box is found | 11 | certain |
| 3 | Add a round-trip test: every `RESPONSE_COLUMNS` header must be a valid input alias | prevents 1 and 2 recurring | certain |
| 4 | Route campus sub-locations to `Building`/`Room` rather than `Mail Code`/`Street` | up to 24 | needs a rule decision |
| 5 | Fix acronym casing (`IDEXX`, `Gas`, leading `the`) | ~5, plus batch consistency | high |
| 6 | Revisit `canonical_preserves_identity` for pure expansions (README's own known limit) | ~10 | needs a design decision |
| 7 | Investigate `13364399` (`Accounts Payable` in Name 1) and `13348274` | 2 records | real defects |
| 8 | **Reference changes:** `Email` → `exact_ci`; `Street *` → `skip` or restate against the abbreviation rule; decide one house-number convention | ~32 | reference-side |

Actions 1–3 are unambiguous, cheap, and worth doing before the next measured
run: together they are **54 of the 244 failing cells**, touching 57 records.
They take the record score from **6 to 14** — only 8 records fail on these
alone, which is the point §7 makes about per-cell scoring: most rows carry more
than one defect, so no single fix moves the record number far.

---

# Reproducing this

```powershell
python scripts/eval_golden.py --out-dir logs/golden          # run + grade
python scripts/eval_golden.py --check-original               # the column-shift check
python scripts/golden_root_cause.py --eval logs/golden/golden_eval.json
python scripts/golden_root_cause.py --bucket abbreviation-direction
pytest tests/test_golden_eval.py -q                          # 31 tests, no network
```

The grader is pure and offline (`tools/golden_eval.py`): it reads the reference,
applies `Match Rules` then `Cell Notes` (most specific wins), and never touches
the orchestrator. A `skip` is excluded from the denominator rather than counted
as a pass, and a record the run did not produce is reported as missing rather
than scoring zero mismatches — both pinned by tests, because a grader that
flatters a run is worse than no grader.
