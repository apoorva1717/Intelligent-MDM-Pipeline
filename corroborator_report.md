# Fix 3 — page-read corroborator: results

**Observed.** The pipeline finds a candidate domain for most records and decides
whether to keep it **without opening it**. 20 records in the baseline carry
`domain-unverified` — a candidate was found, nothing on the record tied it to
the organisation, and it was discarded with the one source that could have tied
them never consulted.

**Built.** `enrichment/page_corroborator.py`: fetch the candidate, have the LLM
read what the page *states*, compare with the record, act.

**Rule, enforced by test:** a page is a witness, never an author. Nothing here
writes `name1_enriched`; an extracted identity goes to the new `operating_name`
field.

---

## Results (run F, 100 records, live APIs)

**47 records had a candidate domain and no registry identity**, so 47 page reads
were attempted (one fetch sequence per domain; every read served from the
recorded fixture store, so the run is reproducible).

| Outcome | Count | Consequence |
|---|---|---|
| `corroborated` | **16** | `operating_name` written; `domain-unverified` withdrawn where it stood (1 row) |
| `no_identity` | **19** | none — the page states no organisation identity |
| `name_mismatch` | **6** | reason annotated; 0 domains withdrawn (see below) |
| `contradicted` | **3** | noted, not acted on |
| `fetch_unavailable` | **3** | none |
| `parked` | **0** | — |

Fetch shape: 17 domains answered on the root alone; 14 also yielded `/about`,
5 `/legal`, 5 `/contact`, 3 `/impressum`; 3 were unreachable. Per-row table with
the extracted evidence for all 47: `logs/runs/page_rows.md`.

**Net effect on the batch** (against the traced baseline, run A):

| | before | after |
|---|---|---|
| `operating_name` written | 0 | **16** |
| `domain-unverified` | 19 | **16** |
| records flagged | 54 | **34** |
| domains accepted | 57 | 60 |
| domains withdrawn | 0 | 0 |

`no_identity` at 19 of 47 is the conservative prompt doing its job: most US SMB
homepages are marketing copy with a logo and no statement of who operates the
site. Returning the brand from the logo would have corroborated every domain,
which is worth nothing.

### What corroboration looks like

| Name 1 | Domain | Page states | Location |
|---|---|---|---|
| `21st Century Biochemicals, LLC` | 21stcenturybio.com | `21st Century Biochemicals, Inc.` | neutral |
| `20/15 Visioneers` | 20visioneers15.com | `20/15 Visioneers` | neutral |
| `AccuVasc LLC` | accuvasc.com | `AccuVasc, LLC` | neutral |
| `Admix, Inc.` | admix.com | `Admix` | neutral |
| `Aesir Technologies` | aesirtec.com | `Æsir Technologies, Inc.` | neutral |
| `Anresco Laboratories` | anresco.com | `Anresco Laboratories` | consistent (San Francisco) |

Twelve of these 16 also became the corroborating evidence for
[`unchanged-verified`](./unchanged_split_report.md) — the page read is the
single largest evidence source for that state.

---

## The withdrawal rule was wrong, and the run proved it

The first implementation withdrew an accepted domain whenever the page name
scored below `PAGE_NAME_MATCH_THRESHOLD`. On the batch that **withdrew four
correct domains**:

| Record | Withdrew | Page actually said | Score |
|---|---|---|---|
| `Analytical Sales` | analytical-sales.com | `Analytical Sales and Services, Inc.` | 71.1 |
| `Applied Catalysts` | appliedcatalysts.com | `Applied Catalysts + Technologies, LLC (AC+T)` | 65.4 |
| `AquaPhoenix Scientific, Inc.` | aquaphoenixsci.com | `AquaPhoenix` | 66.7 |
| `Armor Industrial` | armorfab.com | `Armor Industrial Fabricators, Inc` | 72.7 |

Every one is the same company under its fuller legal name or its shorter brand.
`token_sort_ratio` is length-sensitive **by design** — that is exactly what makes
it safe as GLEIF's guard, where it keeps "Personalvorsorgestiftung der Pfizer
AG" away from "Pfizer AG" — and a brand-vs-legal-name variant is precisely the
shape it scores low. There is no threshold that separates them either: the worst
false positive scores 72.7 and a genuine wrong-entity pair ("Acme Biotech" vs
"Aum Biotech") scores 74.1.

**The rule now requires two independent disagreements**, and the geographic one
at region or country granularity: withdraw only when the page names a different
organisation **and** places it in a different state or country. Derived from the
data, and it separates the batch cleanly:

| Record | Page states | Name | Location | Withdrawn |
|---|---|---|---|---|
| `Analytical Sales` | Analytical Sales and Services, Inc. | 71.1 | consistent (city) | no ✅ |
| `AquaPhoenix Scientific` | AquaPhoenix | 66.7 | consistent (city) | no ✅ |
| `Andrews-Cooper Industries` | Andrews Cooper | 71.8 | neutral | no ✅ |
| `Applied Catalysts` | Applied Catalysts + Technologies, LLC | 65.4 | neutral | no ✅ |
| `Armor Industrial` | Armor Industrial Fabricators, Inc | 72.7 | contradicted (**city**: Houston/Baytown, same state) | no ✅ |
| `Apollo Organic Synthesis` | **Apollo Olive Oil** | 55.0 | contradicted (**region**: NY / Northern California) | *would be* ✅ |

City alone is not enough — a plant and a head office in one state are one
company. Region or country is.

**Zero domains were withdrawn on this batch, and that is the correct result.**
The one row satisfying both conditions, `Apollo Organic Synthesis`, had
`apollooliveoil.com` **already rejected** by the ownership guard, so there was
nothing to take back. Its flag was kept and its reason annotated:

> Domain: a candidate website (apollooliveoil.com) was found but nothing tied it
> to this organisation — confirm apollooliveoil.com before using it — **its page
> states 'Apollo Olive Oil'**

That is the appended-note mechanism working: a reviewer now opens that row
already knowing what they will find.

### The AB Controls row does not reproduce

The brief names `johnsoncontrols.com` accepted for "AB Controls, Inc." as the
case a page read would catch. **It does not occur in any of my runs**: the
website resolver returns `ab-controls.com` for that record in both the traced
baseline and run F, and the ownership guard accepts it on name similarity. The
wrong-entity domain in the supplied workbook was itself run-to-run variance in
SERP resolution.

The withdrawal mechanism is therefore exercised by test rather than by this
batch — `test_an_accepted_wrong_entity_domain_is_withdrawn` runs exactly the
brief's scenario (page states Johnson Controls International plc, Milwaukee WI;
record says Irvine CA) and asserts the domain reverts to empty, `domain-unverified`
is raised naming the withdrawn domain, and a `page_identity` guard rejection is
logged. `test_a_name_difference_alone_never_withdraws` pins the four false
positives above so the loose rule cannot come back.

---

## Two further defects the runs exposed

**1. Every fetch failed behind the corporate VPN.** The first run recorded
`SSLError` on **47 of 54** domains: the new fetch pinned `verify=certifi.where()`,
which the TLS-inspecting VPN's certificates do not chain to. `requests` now
honours `REQUESTS_CA_BUNDLE` (pointed at the corp bundle by
`config._sanitize_ssl_env`), which is what the pre-existing
`_sync_fetch_structured` has always done. The 47 poisoned fixtures were deleted
and re-recorded; after the fix, 3 of 47 are unavailable.

**2. `CA` did not equal `California`.** `Anresco Laboratories` — page says
San Francisco, **California**; record says San Francisco, **CA** — was reported
as a location contradiction. Region comparison now normalises through ROR's
existing `_US_POSTAL_CODES` map. With that fixed the row corroborates.

**3. A corroborated record still shipped `no-match`.** `American Art Clay
Company` had `amaco.com` read, corroborated, and written to `operating_name`,
and then `compute_flags` reported "no source could identify this organisation" —
because `_nothing_was_enriched` did not know about the new field. Fixed;
`operating_name` now counts as a source alongside `domain` and `source_url`.

---

## Schema: what changed, and what Bernd/Bert must change

Two **nullable additive** columns, at the end of the name block:

| Field | Column | Type | Content |
|---|---|---|---|
| `operating_name` | `Operating Name` | `NVARCHAR(255)` NULL | the organisation name the site states about itself |
| `operating_name_provenance` | `Operating Name Provenance` | `NVARCHAR(255)` NULL | `web:{domain}:extracted:{YYYY-MM-DD}` |

Done on this side: `api/models.py` (`EnrichmentResult`), `api/output_columns.py`
(`RESPONSE_COLUMNS` — so the JSON response and the XLSX carry them under the
same names automatically).

**Needed on the SQL / ADF side — not attempted here:**

1. `dp_legacy.test_77.Legacy` — add two nullable columns `[Operating Name]
   NVARCHAR(255) NULL` and `[Operating Name Provenance] NVARCHAR(255) NULL`.
2. `sql/usp_merge_legacy_enriched.sql` — add both to the `OPENJSON … WITH`
   column list (`'$."Operating Name"'`, `'$."Operating Name Provenance"'`) and
   to the `WHEN MATCHED THEN UPDATE SET` block. `operating_name` should follow
   the `COALESCE(NULLIF(...),'')` pattern the name columns use (never blank an
   existing value); the provenance column can be assigned directly, as
   `[Record Type]` and `[ROR ID]` are.
3. The DATAshaper mapping — decide whether `Operating Name` is surfaced to
   stewards as a read-only reference column. It is **not** a substitute for
   `Name 1` and must not be mapped onto it. Recommend: display-only, no
   validation rule.
4. ADF's column allow-list, if the copy activity enumerates columns explicitly.

Nothing downstream breaks if these are not done: both columns are additive and
nullable, and a consumer that ignores them sees the schema it saw before.

---

## Open items

**The `domain` field is not written on a corroboration.** When a page read
corroborates a candidate the ownership guard rejected, the flag is withdrawn but
`domain` stays empty — the guard's decision stands. A page read is arguably a
*stronger* ownership condition than the four the guard has (the site states its
own identity, and the record's location does not contradict it), so the natural
next step is a fifth condition, `verified_by="page"`. **Not done**: that is a
change to the ownership guard, which is outside this fix's scope. On this batch
it affects the one corroborated row whose domain had been rejected. Recommend
adding it, as its own change, with its own before/after.

**`PAGE_EXTRACT_FEEDS_RETRY` is off.** Built, wired to Stage 5 with its own
once-per-record budget and every retry guard applying unchanged, and left off.
Fix 1's trace showed Stage 5's yield on this population is bounded by GLEIF's
coverage of private US SMBs rather than by the supply of candidate names, so
turning it on buys API calls before it buys identifiers. **Your decision**, now
that Fix 1's findings are in.

**`no_identity` at 40% of reads.** Not a defect, but the ceiling on this
mechanism for US SMBs. If it needs lifting, the lever is the fetch (a rendered
DOM rather than static HTML, or a `/privacy` / `/terms` page, which carry legal
identity more often than `/about`) — not the prompt, which is deliberately
conservative and should stay so.

**Page freshness.** A fixture records what a site said on the day it was read
and never expires. Fine for a thesis re-run; for production a TTL or a
re-record cadence has to be decided. Not defaulted here.

---

> **\reviewnote summary.** Reading the candidate website — one fetch sequence per
> domain, root plus the first imprint page that answers, then a constrained LLM
> reader that is required to return null rather than fill a gap from memory —
> produced a usable statement of organisational identity for 28 of 47 records
> with a candidate domain on the 100-row chemspeed batch, corroborating 16 of
> them and supplying the largest single evidence source (12 of 24) for Fix 2's
> `unchanged-verified` state, with 16 `operating_name` values written and Name 1
> never touched. The most valuable result was negative: withdrawing an accepted
> domain on a below-threshold name score alone destroyed four correct domains,
> because `token_sort_ratio` is length-sensitive by design and a brand-vs-legal-
> name variant is exactly the shape it scores low — so withdrawal now requires
> the page to name a different organisation *and* place it in a different state
> or country, which on this batch separates the one true wrong-entity domain
> (Apollo Olive Oil for Apollo Organic Synthesis) from all four false ones, and
> withdraws nothing it should not.
