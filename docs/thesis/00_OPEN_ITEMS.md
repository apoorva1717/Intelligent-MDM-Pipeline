Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Open Items — consolidation across `docs/thesis/`

Two independent consolidations, produced without modifying any other document or any source file.

- **Part 1** — a consistency check between `08_GAPS.md` (which feeds the thesis limitations
  section) and `09_DECISIONS.md` (which feeds the design-rationale sections). Report only; neither
  file was edited.
- **Part 2** — a register of every open-item marker in `docs/thesis/`, with a specific resolution
  for each, grouped by resolution type.

Both parts follow the evidence rules of `docs/thesis-doc-prompt.md:18-34`: every claim carries a
`path/file:LINE` citation, and where the two pass documents disagree the working tree at the
header commit decides.

---

# Part 1 · Consistency check — `08_GAPS.md` ↔ `09_DECISIONS.md`

Both documents were read in full (1,288 and 1,518 lines). Twelve items are reported. They are
ordered by how visible the conflict would be to an examiner reading the limitations chapter and
the rationale chapter together, not by document order.

Three are **direct contradictions** where one document is factually wrong (§1.1). Nine are
**framing or evidence divergences** where both documents are individually defensible but read
inconsistently side by side (§1.2). §1.3 records one structural asymmetry that is neither.

## 1.1 · Direct contradictions

### X-1 · `09_DECISIONS.md` cites `enrichment/confidence.py` as a live enforcement layer; `08_GAPS.md` proves it dead

This is the most consequential item in Part 1, because it is the rationale chapter citing
unreachable code as the mechanism that realises the system's stated first design principle.

**`09_DECISIONS.md` side.** D-5 ("Flag rather than infer") presents a four-layer realisation
table, two of whose rows cite this module (`09_DECISIONS.md:384-389`):

> | Layer | Mechanism | Evidence |
> |---|---|---|
> | Status derivation | Tier 3 always yields `unresolved`, whatever its confidence | `enrichment/confidence.py:33` |
> | Flagging | Tier 3 always flags; medium confidence from any tier flags; a low-confidence website write flags | `enrichment/confidence.py:51-55`; `README.md:721,795-812` |

The summary-table row for D-5 carries the same evidence (`09_DECISIONS.md:90`).

**`08_GAPS.md` side.** G-35 (`08_GAPS.md:528-535`):

> Neither `determine_enrichment_status` (`enrichment/confidence.py:40`) nor
> `should_flag_for_review` (`:8`) is imported or called; a full-repository pattern search matches
> only the two definition sites … The `flag_for_review` and `enrichment_status` values that reach
> the output are set inline at the tier call sites.

**Which reading the code supports: `08_GAPS.md`.** A repository-wide search over `*.py`
(excluding `.venv/`) for `should_flag_for_review|determine_enrichment_status|enrichment.confidence`
returns exactly two lines — `enrichment/confidence.py:8` and `enrichment/confidence.py:40`, both
`def` statements. No import, no call, no test. Two of D-5's four cited layers therefore do not
execute.

**What survives.** D-5's *conclusion* is unaffected — the flag-rather-than-infer policy is real,
and `08_GAPS.md:533-534` says so ("set inline at the tier call sites"). It is the *evidence* that
fails. If the rationale chapter is written from D-5 as it stands, it will cite line numbers an
examiner can check and find dead.

**Secondary defect, same entry.** `08_GAPS.md:530-531` transposes the two line numbers: the source
has `determine_enrichment_status` at `enrichment/confidence.py:8` and `should_flag_for_review` at
`:40`, which is the order `03_ALGORITHMS.md:1587-1588` records correctly. G-35 reverses them.

### X-2 · The two documents attribute the `MAX_PAGE_CONTENT_CHARS` change to different commits

**`08_GAPS.md` side.** G-12 (`08_GAPS.md:209-210`):

> Commit `b19cd1a` changed the `Settings` field from 3000 to 1500 "for better performance" and did
> not change the other three sources.

**`09_DECISIONS.md` side.** D-4 (`09_DECISIONS.md:344-354`, and the summary row at `:89`):

> `635d5ba` reduced the amount of free-form body text available to the prompts by more than half:
> ```
> -        default_factory=lambda: int(os.getenv("MAX_PAGE_CONTENT_CHARS", "3000"))
> +        default_factory=lambda: int(os.getenv("MAX_PAGE_CONTENT_CHARS", "1500"))
> ```
> The commit body records the change as "Adjusted `max_page_content_chars` in `config.py` from 3000
> to 1500 for better performance"

**Which reading the code supports: `09_DECISIONS.md`.** `git show 635d5ba -- config.py` contains
exactly that hunk and exactly that commit-body line. `git show b19cd1a -- config.py` contains no
`max_page_content` hunk at all. `git log --all -S 'MAX_PAGE_CONTENT_CHARS' -- config.py` returns
only `f77080b` (the pickaxe does not fire on `635d5ba` because the string is present on both
sides of the change).

G-12's substantive finding — three sources, two values, effective value 1500 — is unaffected. Only
the commit attribution is wrong, and it is wrong in the document an examiner would read for
limitations while the correct attribution sits in the document read for rationale.

### X-3 · `09_DECISIONS.md` records a settled decision to keep DuckDuckGo; `08_GAPS.md` establishes the installed library does not query DuckDuckGo

**`09_DECISIONS.md` side.** D-41 (`09_DECISIONS.md:1463-1482`, summary row `:126`) is titled "Keep
DuckDuckGo as a keyless search fallback" and states:

> **Alternatives visible in history.** None — this entry records a decision *not* taken. Both
> clients have been present since the first commit … and the fallback is still wired at
> `enrichment/orchestrator.py:778-781`.
> **Why the chosen option.** Recorded only as a quality caveat at the point of fallback:
> "SERPAPI_KEY is not set — falling back to DuckDuckGo. DuckDuckGo returns lower-quality results."

**`08_GAPS.md` side.** G-68, final bullet (`08_GAPS.md:1042-1044`):

> The installed `duckduckgo-search 8.1.1` scrapes Bing rather than DuckDuckGo, so the documented
> "DuckDuckGo fallback" does not query the service its name implies.

**Which reading the code supports: `08_GAPS.md`.** `search/duckduckgo_client.py:9` imports `DDGS`
from `duckduckgo_search`; the installed distribution is `duckduckgo_search-8.1.1`, and
`.venv/Lib/site-packages/duckduckgo_search/duckduckgo_search.py:182` reads
`backends = ["bing"]  # temporaly disable html and lite backends`, with `:388` issuing
`GET https://www.bing.com/search`.

D-41 is not wrong that a decision was taken to retain a keyless fallback — that decision is real
and is the entry's actual content. What fails is the entry's *subject*: as written, the rationale
chapter will name DuckDuckGo as a system dependency that the limitations chapter denies. The
decision should be restated as "retain a keyless search fallback", with the provider identity
carried as a property of the pinned library version rather than of the decision.

## 1.2 · Framing or evidence divergences

### X-4 · D-21 quotes as evidence a docstring G-9 establishes as stale

**`09_DECISIONS.md` side.** D-21 (`09_DECISIONS.md:792-799`) reproduces the docstring unqualified:

> Coverage: 34 of the 36 catalogue codes are emitted. Two are intentionally never emitted because
> they genuinely require the pipeline's LLM residual classifier …
> (`enrichment/issue_detection.py:18-24`; markers at `:88,112`)

**`08_GAPS.md` side.** G-9 (`08_GAPS.md:170-178`):

> `enrichment/issue_detection.py:3-4` describes the module as auditing a record "against the
> 36-code Issue Catalogue", and `:18` states "Coverage: 34 of the 36 catalogue codes are emitted."
> The catalogue at `enrichment/issue_detection.py:75-118` declares **37** codes … **at most 34
> distinct codes can be observed** … Both docstring figures are stale against the current source.

**Which reading the code supports: `08_GAPS.md` on the counts, `09_DECISIONS.md` on the decision.**
The catalogue declares 37; the derivation is at `03_ALGORITHMS.md:74-82`. The second sentence of
the docstring — that two codes are LLM-only by design — is accurate and is D-21's actual subject.
The first sentence carries two stale numbers. As it stands the rationale chapter reproduces
"34 of the 36" as a quoted fact while the limitations chapter marks the same figures stale; an
examiner reading both will see the rationale chapter quoting a source the limitations chapter has
discredited. D-21 should quote only the LLM-only clause, or annotate the counts inline.

### X-5 · `Domain_DeptDomain_SearchTerm_Logic.pdf` is a stale conflicting source (G-13) or the superseded diagnostic that motivated the change (D-16)

**`08_GAPS.md` side.** G-13 (`08_GAPS.md:215-229`) lists the PDF as one of five divergent sources
in a discrepancy table — "`Domain_DeptDomain_SearchTerm_Logic.pdf` | "defaults on" | via
`02_ARCHITECTURE.md:508-510`" — and closes: "The commit that flipped the default (`515cc7c`)
states the flip; `.env.example` and the PDF were not updated."

**`09_DECISIONS.md` side.** D-16 (`09_DECISIONS.md:668-681`) treats the same passage as the
*evidence for* the decision:

> The contradiction it resolves is itself documented, in the PDF written from a source trace:
> > `DEPT_PROBE_CROSS_DOMAIN` **defaults to** `True` despite in-code comments calling stage 3
> > "opt-in / off by default." …
> So the record shows a flag introduced with one intent … the contradiction found by a trace, and
> the default changed to match the comments.

**Which reading the code supports: both, for different artefacts.** `config.py:166-168` sets
`default=False`; `.env.example:61` sets `DEPT_PROBE_CROSS_DOMAIN=true` and comments it "when true
(default)". `.env.example` is a live deployment template and is a genuine, cost-bearing conflict —
G-13's reading. The PDF is a dated diagnostic that describes the pre-flip state truthfully and was
the input to the change — D-16's reading; it is not a specification and listing it as a fifth
divergent "source of the value" overstates it. The G-13 table should separate the live template
from the superseded diagnostic.

### X-6 · D-29 presents one transcript reading of the DATAshaper recency bands as settled; G-78 records that the two transcript passages disagree

**`09_DECISIONS.md` side.** D-29 (`09_DECISIONS.md:1145-1148`) quotes a single passage:

> we used a dates_difference function that counts the difference in months … So that means when
> it's between 0 and 9, then it's 25. When it's between 10 and 24, then it's 15. Else it's 5.
> (`Datashaper-Tutorial-Part2.txt:1877-1882`)

and the D-29 summary row states the DS model flatly as "recency scored on months since
`GETDATE()` (0–9 → 25, 10–24 → 15, else 5)" (`09_DECISIONS.md:114`).

**`08_GAPS.md` side.** G-78 (`08_GAPS.md:1213-1219`):

> the two transcript passages disagree with each other on the middle band's lower bound (10 vs 20
> months) (`04_PARAMETERS.md:527-543`; `09_DECISIONS.md:1503`).

**Which reading the source supports: `08_GAPS.md`.** `Datashaper-Tutorial-Part2.txt:1880` reads
"when it's between 10 and 24, then it's 15"; `Datashaper-Tutorial-Part3.txt:527` reads "Between 20
and 24, it's 15" — both quoted side by side at `04_PARAMETERS.md:531-536`. D-29 cites only the
Part 2 form and does not record that the vendor's own account is inconsistent. Since D-29's
argument turns on comparing the DS banding with `dedup/weights.json`, the comparison rests on a
figure the transcripts do not agree on.

### X-7 · D-1 is filed as a decision; G-76 states the repository records no decision

**`09_DECISIONS.md` side.** The summary row titles D-1 "Restrict Tier 2A to populating a blank
Name 2, and unwire Tier 2B, disabling both Name-2 correction paths" (`09_DECISIONS.md:86`), and
the §10 preamble states of every row in that table, D-1 included: "Each is a decision the history
proves was made, on a date, in a commit — with no recorded reason" (`:1488-1490`).

**`08_GAPS.md` side.** G-76 (`08_GAPS.md:1184-1187`):

> This is therefore not a scope decision the repository records; it is an outcome the repository
> records without a decision. Whether the two paths were judged redundant, one judged unreliable,
> or the gate was an unintended consequence is not determinable from any artefact.

**Which reading the code supports: `08_GAPS.md`, and `09_DECISIONS.md`'s own body agrees.**
`09_DECISIONS.md:224-233` says exactly what G-76 says — "the history therefore shows **what**
changed and **when**, but not **why** … Whether that consistency was intended or coincidental is
exactly what the history does not say." The conflict is between the two documents' *framing
devices*, not their findings: a decision log that assigns an identifier, a title in the imperative
("Restrict…, and unwire…"), and a place in a table of "decisions the history proves was made"
asserts an intent that the same entry then declines to attribute. The history proves an *edit*;
it does not prove a *decision*. If the rationale chapter is written from the D-1 title and the
limitations chapter from G-76, the two chapters will contradict each other on whether the system's
single largest scope reduction was chosen.

### X-8 · `LLM_SSL_VERIFY=false` — "logged loudly" (D-38) against "no indicator" (G-67 item 7)

**`09_DECISIONS.md` side.** D-38 (`09_DECISIONS.md:1398-1400`): "The resolver's three-step order is
itself the reconciliation: `LLM_SSL_VERIFY=false` first, 'Insecure — a last resort … Logged
loudly'".

**`08_GAPS.md` side.** G-67 item 7 (`08_GAPS.md:1007`): "Disables TLS verification for the calls
that carry personal data, with no code change required and no indicator on `/health` or `/tiers`".

**Which reading the code supports: both, on different surfaces.** `llm/openai_client.py:110-112`
does emit a warning when the variable is false, at client construction. `api/routes.py:75-84`
returns `HealthResponse(status="healthy", version, env, mock_mode, tiers_available)` — a literal
with no TLS field. A log line is not a health indicator, so neither statement is wrong; but
"logged loudly" in the rationale chapter reads as a mitigation that the limitations chapter denies.
Both entries should name their surface explicitly.

### X-9 · Deliberate silence (D-14) and failure-as-silence (G-65) produce the same observable, and neither document says so

**`09_DECISIONS.md` side.** D-14 (`09_DECISIONS.md:588-592`) presents the empty return as the
design: "Require a name token in the host; when none qualifies, return nothing so Path C (LLM) can
try instead of writing a stranger's domain… a flagged wrong domain was judged worse than an empty
field, because the empty field lets a later path run."

**`08_GAPS.md` side.** G-65 Grade 2 (`08_GAPS.md:916`): "`enrichment/website_resolver.py:601-607` |
`logger.info` → `WebsiteResolution()` | Path C failure reads as 'the LLM declined'". The same table
records `:493-501` as a SERP failure returning an empty result whose only trace is the
`WEBSITE_TRACE` diagnostic, "off by default".

**Which reading the code supports: both, and they compose.** An empty `Domain` column has at least
three causes — a rank-0 rejection by design (D-14), a Path C decline, and a Path C or SERP failure
(G-65) — and the output distinguishes none of them. This bears directly on any website-resolution
rate the evaluation chapter reports, and it is the specific instance of the general warning at
`08_GAPS.md:1277-1281`. Neither document states the composition; the thesis should, once.

### X-10 · G-74 and D-6 cite different README passages for the same rejection

`08_GAPS.md:1144` cites `README.md:425` for "`token_set_ratio` is deliberately **not** used: it
scores any contained substring 100 and would accept that wrong entity". `09_DECISIONS.md:409-413`
cites `README.md:2013` for "`token_set_ratio` was rejected as unsafe (it scores any contained
substring 100)".

**Both citations are correct.** `README.md:425` is in the GLEIF verification-guard section;
`README.md:2013` is in the changelog. They are two distinct statements of the same rejection.
Recorded because the two chapters will appear to quote the same sentence with different wording and
different line numbers, which reads as a transcription error unless the thesis cites both.

### X-11 · G-45 and G-47 are structurally identical and filed differently

G-45 (`08_GAPS.md:612-621`) — `G1-ADDR-009` and `G4-ADDR-025`, declared and never emitted, annotated
`# LLM-only — never emitted` in source — is cross-listed into §E.1 through G-74 and carries a
decision entry, D-21. Its own text says so: "This is a recorded decision — see G-74 — but it is
also a declared-and-absent value, and both readings matter."

G-47 (`08_GAPS.md:632-637`) — `missing_building_inconsistency` — is the same shape: declared in
`ISSUE_TYPES` at `dedup/scoring.py:403-412`, with the comment immediately above it
(`dedup/scoring.py:399-402`) stating it "is reserved for the upstream building differentiator
(Phase 1); it is a declared type here but not emitted from election (no building signal at this
stage)". It sits only in §B.4, has no §E.1 counterpart, and has no D-entry.

**Which reading the code supports: they should be treated alike.** Both carry a source comment
stating the omission is intentional. As filed, the limitations chapter presents one as a design
boundary and the other as an unfilled slot.

### X-12 · G-75 lists as "removed by a recorded decision" a capability that was neither removed nor decided

The G-75 table is titled "Capabilities removed by a recorded decision" and its preamble reads
"Distinct from G-74: these existed and were deleted. All are documented in `09_DECISIONS.md`"
(`08_GAPS.md:1148-1153`). Its final row is "`search_terms.unit_domain_or_path` from the live path |
`00_INVENTORY.md:320-322`".

**Which reading the code supports: the function was not removed.** G-37 in the same document
(`08_GAPS.md:547-552`) establishes it is still defined at `enrichment/search_terms.py:256` and
still exercised by `tests/test_search_terms.py:106-130`. It is also the only row of the table whose
"Commit / decision" cell cites a pass document rather than a D-entry: `09_DECISIONS.md` has D-17
for the twin that *was* deleted (`derive_department_domain`) and nothing for this one. The row
belongs in §B.3 with G-37, or the table title must be qualified.

## 1.3 · Structural asymmetry — neither document contradicts the other

`09_DECISIONS.md` mines only this repository's git history, code comments, the README and the two
root PDFs (`09_DECISIONS.md:11-33`). It therefore contains **no entry for any ADF or DATAshaper
design choice** — no D-entry addresses the sequential `ForEach`, the retry policy, the absent
watermark, the unbatched dedup Lookup, `secureInput`/`secureOutput`, or the blocking rule
configuration.

`08_GAPS.md` records all of these as gaps: G-31, G-32, G-33, G-44, G-77, and G-67 items 1, 2, 3,
10 and 14.

The consequence for the thesis is that the limitations chapter will criticise a deployment topology
for which the design-rationale chapter offers no rationale at all — not because none was sought,
but because Pass 9's declared source set excludes the artefacts that would carry it. This is not a
contradiction and needs no correction to either document, but it needs one sentence in the thesis
scoping the decision log to the service repository, or an examiner will read the silence as a
failure to look.

---

# Part 2 · Open items register

Every occurrence of the seven marker strings across `docs/thesis/`, swept at the header commit:

```
⚠ UNDOCUMENTED · ⚠ RATIONALE NOT IN REPO · ⚠ MEASUREMENT REQUIRED · ⚠ UNVERIFIED
⚠ NO FIXTURE COVERAGE · ⚠ ARTEFACT NOT IN REPO · VERIFY-BY-FREEZE
```

**191 occurrences across 10 files.** `05_DATA_MODEL.md` and `09_DECISIONS.md` contain none —
`09_DECISIONS.md` uses the unmarked bold form "⚠ **RATIONALE NOT IN REPO — author to supply**" with
the same words but marks its open items in §10 instead, and those are reached through
`08_GAPS.md:1069`, `:1189` and `:1221` below. **`⚠ ARTEFACT NOT IN REPO` occurs zero times**; the
equivalent findings are carried as `⚠ UNVERIFIED` or as prose in `08_GAPS.md` §B.2.

Per-file counts: `03_ALGORITHMS.md` 65 · `04_PARAMETERS.md` 60 · `08_GAPS.md` 18 ·
`06b_CROSSCUTTING.md` 16 · `02_ARCHITECTURE.md` 11 · `03b_EXEMPLARS.md` 6 · `07_EVALUATION.md` 6 ·
`06_EXTERNAL_DEPS.md` 5 · `00_INVENTORY.md` 3 · `01_TRACEABILITY.md` 1.

Grouped by resolution type:

| Group | Resolution type | Count |
|---|---|---|
| **(a)** | Author knowledge only — the answer exists nowhere but with the author | **66** |
| **(b)** | A measurement or query — a command, SQL, KQL, dashboard read, or live run | **41** |
| **(c)** | An artefact export — a file that exists outside this repository must be obtained | **8** |
| **(d)** | A repository change — source, test, fixture, or dataset | **57** |
| **(e)** | Convention statement or restatement — not a separate open item | **19** |
| | **Total** | **191** |

---

## Group (a) · Author knowledge only — 66 items

The answer is not obtainable from any artefact, command, or export. Each row states the exact
question the author must answer.

### (a.1) Parameter rationales — `04_PARAMETERS.md` §1, 58 items

All 58 carry `⚠ UNDOCUMENTED — author to supply` in the rationale column. All affect the same
thesis section: **Parameters and configuration (Pass 4 §1)**, with the named secondary section in
brackets where one applies.

| # | Marker | Source file:line | What is missing | What would resolve it |
|---|---|---|---|---|
| 1 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:39` | Why Phase-1 `temperature` is `0.0` (`llm/openai_client.py:205`) | Answer: was 0.0 chosen for reproducibility of JSON extraction, or is it the never-revisited SDK default? |
| 2 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:40` | Why `call_openai` defaults `max_tokens=500` (`llm/openai_client.py:180`) | Answer: which Phase-1 extraction schema is the longest, and was 500 sized against it? |
| 3 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:41` | Why `extract_json` defaults `max_tokens=1024` (`llm/openai_client.py:263`) | Answer: why does the wrapper default to double its callee's 500 — which value is the intended Phase-1 ceiling? |
| 4 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:42` | Why address-residual classification uses `max_tokens=200` (`enrichment/address_processing.py:679`) | Answer: what is the longest residual-classification response observed, and does 200 bound it? |
| 5 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:43` | Why `GET /diag/llm` probes with `max_tokens=50` (`api/routes.py:1054`) | Answer: is 50 sized to prove connectivity only, or to return a usable probe answer? |
| 6 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:44` | Why `GET /diag/dedup-llm` probes with `max_tokens=200` (`api/routes.py:1088`) | Answer: why 4× the Phase-1 probe budget — to accommodate a reasoning-model preamble? |
| 7 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:61` | Why `DedupLLM.adjudicate` defaults `max_tokens=4000` (`dedup/llm.py:161`) | Answer: was 4000 sized for a worst-case block, and is it intentional that no application caller reaches it (G-42)? |
| 8 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:62` | Why both application call sites pass `max_tokens=1000` (`dedup/adjudicator.py:452,638`) | Answer: what block size was 1000 sized against, and what is the intended behaviour when a verdict JSON exceeds it? |
| 9 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:69` | Why `ROR_API_BASE` pins `/v2/organizations` (`config.py:85,172`) | Answer: was v2 chosen over v1 deliberately, and is the pin intended to survive ROR's versioning policy? |
| 10 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:71` | Why the ROR HTTP timeout is `15.0` s (`enrichment/tier1_ror.py:608`) | Answer: was 15 s derived from observed ROR latency, and over what sample? |
| 11 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:86` | Why `GLEIF_API_BASE` pins `/api/v1` (`config.py:88,187`) | Answer: is the v1 pin deliberate, and does a v2 exist that was evaluated and rejected? |
| 12 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:87` | Why `GLEIF_TIMEOUT_SECONDS` is `15` (`config.py:89,190`) | Answer: was 15 s measured against GLEIF, or copied from the ROR timeout? |
| 13 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:92` | Why GLEIF `page[size]` is `"10"` (`enrichment/tier1_lei.py:259`) | Answer: over what sample was rank 10 established as the point past which candidates never verify? |
| 14 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:93` | Why fuzzy completions are capped at `5` (`enrichment/tier1_lei.py:340`) | Answer: is 5 a GLEIF per-record call budget, or an observed sufficiency rank? |
| 15 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:100` | Why `SearchClient.search` defaults `num_results=5` (`search/base.py:21`) | Answer: is 5 the intended house default for every SERP consumer, or the first call site's value generalised? |
| 16 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:103` | Why the site-restricted department probe uses `num_results=5` (`enrichment/orchestrator.py:1183`); `README.md:745` states the value without a reason | Answer: what makes 5 sufficient for an on-domain department probe? |
| 17 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:104` | Why the cross-domain department probe also uses `num_results=5` (`enrichment/orchestrator.py:1297`) | Answer: should an unrestricted whole-web probe use the same breadth as a site-restricted one? |
| 18 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:105` | Why Tier 2A contact lookup uses `num_results=5` (`enrichment/tier2a_contact.py:330`) | Answer: was 5 validated against how deep a correct contact page typically ranks? |
| 19 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:106` | Why Tier 2B department search uses `num_results=5` (`enrichment/tier2b_dept.py:227`) | Answer: same question for department pages — noting the value is moot while Tier 2B is unwired (G-2) |
| 20 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:107` | Why the lab resolver (UC 13) uses `num_results=5` (`enrichment/lab_resolver.py:83`) | Answer: was 5 validated for parent-department resolution, which searches a narrower corpus? |
| 21 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:108` | Why person affiliation uses `num_results=5` (`enrichment/person_affiliation.py:124`) | Answer: was 5 validated *per query variant*, given this stage issues several per record? |
| 22 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:111` | Why `subdomain_exists` uses a `5` s HEAD timeout (`search/page_fetcher.py:95`) | Answer: is 5 s an observed p99 for a HEAD against an academic subdomain, or an estimate? |
| 23 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:112` | Why `resolve_final_url` uses a `5` s redirect timeout (`search/page_fetcher.py:111`) | Answer: was redirect-chain depth considered in setting the same value as the single-HEAD timeout? |
| 24 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:115` | Why title/H1/breadcrumb slices truncate at `300` chars (`search/page_fetcher.py:254-256`) | Answer: what is the longest slice the extraction prompt must receive intact? |
| 25 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:116` | Why anchor text truncates at `200` chars (`search/page_fetcher.py:213`) | Answer: why 200 against 300 for slices — is anchor text held to be less informative, and on what basis? |
| 26 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:117` | Why the `User-Agent` is `BrukerMDM-Enrichment/1.0` (`search/page_fetcher.py:127` et al.) | Answer: is the identifying UA a deliberate attribution/politeness choice, and was host-side blocking on it considered? |
| 27 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:127` | Why the significant-token minimum is `4` chars (`enrichment/website_resolver.py:95`) | Answer: was 4 tuned against a name corpus, and which short institution tokens does it deliberately exclude? |
| 28 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:130` | Why the Path C sentinel set is `"", null, none, unknown, n/a, na` (`enrichment/website_resolver.py:614`) | Answer: is this the observed refusal vocabulary of the deployed model, or an a-priori list? |
| 29 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:141` | Why the dept-probe title bonus is `+1` (`enrichment/orchestrator.py:257`) | Answer: what relative weight was intended between title evidence and path depth? |
| 30 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:142` | Why the path penalty is capped at `min(2, penalty)` (`enrichment/orchestrator.py:255`) | Answer: at what path depth does further depth stop being informative, and is 2 that point? |
| 31 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:150` | Why the candidate-subdomain acronym band is `2 ≤ len ≤ 6` (`enrichment/orchestrator.py:1091`) | Answer: which real acronyms set the 6-character ceiling, and was a 7+ case observed and rejected? |
| 32 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:151` | Why the top `2` tokens of length `≥ 4` are probed (`enrichment/orchestrator.py:1093-1096`) | Answer: is 2 a per-record HEAD-probe cost budget, or an observed sufficiency? |
| 33 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:153` | Why `scored[:5]` candidates are verified per stage (`enrichment/orchestrator.py:1161,1211`) | Answer: is 5 a page-fetch cost budget, and what rank did correct hosts occupy in the `WEBSITE_TRACE` runs? |
| 34 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:155` | Why the verification phrase-length gate is `≥ 4` chars (`enrichment/orchestrator.py:1380`) | Answer: what false accept did the 4-character floor prevent? |
| 35 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:166` | Why Tier 2B bands are `exact ≥ 90`, `partial ≥ 60` (`enrichment/tier2b_dept.py:152`) | Answer: where do 90 and 60 come from, and why do they differ from Tier 2A's 95 / `fuzzy_match_threshold` 80? |
| 36 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:169` | Why the Tier 3 token minimum is `3` chars (`enrichment/tier3_llm.py:29`) | Answer: why 3 here against 4 in the website resolver — is the difference intentional? |
| 37 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:177` | Why the acronym letter-count band is `2 ≤ len ≤ 8` (`enrichment/preprocess.py:368,414`) | Answer: which acronyms set the 8-letter ceiling? |
| 38 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:179` | Why `_RESIDUAL_CONFIDENCE_THRESHOLD` is `0.85` (`enrichment/address_processing.py:657`) | Answer: was 0.85 calibrated against labelled residual classifications, and if so how many? |
| 39 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:194` | Why `CLUSTER_ID_PREFIX` is `c_` (`dedup/cluster_key.py:13`) | Answer: is `c_` required by the DATAshaper deduplication view, or a free choice made here? |
| 40 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:212` | Why `EnrichmentOptions.max_concurrency` is `5`, bounded `ge=1, le=20` (`api/models.py:289`) | Answer: what sets the ceiling of 20 — an external-API rate limit, Functions memory, or neither? |
| 41 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:213` | Why the `/enrich/file` `max_concurrency` query default is the independent literal `5` (`api/routes.py:521`) | Answer: is the duplicated literal intended to track `api/models.py:289`, and which is authoritative? |
| 42 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:215` | Why `EnrichmentRequest.records` sets `min_length=1` (`api/models.py:296`) | Answer: should an empty batch be a 422, or a 200 with an empty summary? |
| 43 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:216` | Why `LOG_LEVEL` defaults to `INFO` (`config.py:113,244`) | Answer: is INFO the intended production level, given 74% of the 178 log statements are INFO (`06b_CROSSCUTTING.md` §b)? |
| 44 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:221` | Why the request ID is `8` hex chars of a UUID4 (`api/middleware.py:22`) | Answer: was collision probability computed against the expected request volume? |
| 45 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:229` | Why uvicorn runs `0.0.0.0:8000` with `reload=True` (`main.py:8`) | Answer: is `main.py` intended as a development-only entry point, given the deployed host is Azure Functions? |
| 46 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:230` | Why the Functions HTTP auth level is `ANONYMOUS` (`function_app.py:12`) | Answer: was ANONYMOUS chosen because ADF cannot present a function key, or is it a placeholder? (governs G-67 item 5) |
| 47 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:231` | Why the Functions route prefix is `""` (`host.json:13`) | Answer: was the prefix stripped to keep the ADF URLs stable across a hosting change? |
| 48 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:232` | Why the extension bundle is pinned `[4.*, 5.0.0)` (`host.json:18`) | Answer: is the upper bound deliberate, or the `func init` template default? |
| 49 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:233` | Why App Insights sampling is enabled with only `Request` excluded (`host.json:5-8`) | Answer: was it intended to exclude Request rather than Trace, given the trace stream carries all 178 log statements (G-67 item 15)? |
| 50 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:235` | Why `pytest` sets `asyncio_mode = strict`, `testpaths = tests` (`pytest.ini:2-3`) | Answer: deliberate, or scaffold default? |
| 51 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:244` | Why all 7 ADF activities set `timeout` `0.12:00:00` (`CONTEXT-EXTERNAL.md:53` et al.) | Answer: is 12 h the ADF default left in place, or chosen against an expected batch duration? |
| 52 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:245` | Why all 7 ADF activities set `retry: 0` (`CONTEXT-EXTERNAL.md:54` et al.) | Answer: the freeze note says retry will be raised — to what value, and on which activities? (pairs with item 92) |
| 53 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:246` | Why `retryIntervalInSeconds` is `30` while retry is 0 (`CONTEXT-EXTERNAL.md:55` et al.) | Answer: what interval is intended once retry > 0? |
| 54 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:247` | Why `secureInput`/`secureOutput` are `false` on all 7 activities (`CONTEXT-EXTERNAL.md:56-57` et al.) | Answer: was cleartext payload retention in ADF monitoring weighed against the personal data in those payloads (G-67 item 14)? |
| 55 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:248` | Why the enrichment page size is `50`, written as two independent literals (`CONTEXT-EXTERNAL.md:64,106`) | Answer: what bounded 50 — the Functions timeout, the Lookup ceiling, or per-call LLM spend? |
| 56 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:249` | Why `ForEach1.isSequential` is `true` (`CONTEXT-EXTERNAL.md:88`) | Answer: was sequential chosen to bound external-API concurrency, or to make failures resumable? |
| 57 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:250` | Why `firstRowOnly` is `false` on all Lookups (`CONTEXT-EXTERNAL.md:73,115,235`) | Answer: confirm this is forced by the multi-row design, in which case the rationale cell closes as *n/a* rather than *undocumented* |
| 58 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:252` | Why `AutoResolveIntegrationRuntime` is used for the cross-tenant call (`CONTEXT-EXTERNAL.md:137,257`) | Answer: were a managed-VNet or self-hosted IR considered for the Tillit→Bruker hop (G-67 item 4)? |

### (a.2) Recorded outcomes with no recorded reason — 8 items

| # | Marker | Source file:line | What is missing | What would resolve it | Thesis section affected |
|---|---|---|---|---|---|
| 59 | ⚠ RATIONALE NOT IN REPO | `02_ARCHITECTURE.md:473` | Why address validation (step 6) and the `/issues` call (step 7) are separate ADF pipelines rather than folded into enrichment | Answer: were they separated for cadence, for cost, because a different team owns them, or because the validating vendor is called from ADF only? | Architecture — pipeline decomposition (Pass 2 §9) |
| 60 | ⚠ RATIONALE NOT IN REPO | `02_ARCHITECTURE.md:475` | Why ZFI records are excluded at workflow step 1; recorded only as Bernd Schnurrer's instruction (`CONTEXT-EXTERNAL.md:434-435`) | Answer: what property of ZFI records makes them out of scope — a different master-data owner, a different lifecycle, or a known-bad extract? | Architecture — scope boundary (Pass 2 §9) |
| 61 | ⚠ RATIONALE NOT IN REPO | `08_GAPS.md:1069` | Same as item 60, restated as G-69 with the added observation that the excluding script is not located (G-26) | Answer as item 60; additionally name the predicate the script applies, so the excluded population can be characterised | Limitations — scoped out (Pass 8 §E.1) |
| 62 | ⚠ RATIONALE NOT IN REPO | `08_GAPS.md:1189` | Why both Name-2 correction paths were disabled in `635d5ba` (G-76 / D-1) | Answer one of three: were the two judged redundant, was one judged unreliable, or was the Tier 2A gate condition an unintended consequence of refactoring the Name-2 handling? | Limitations — intended but not done (Pass 8 §E.2) + design rationale (Pass 9 D-1) |
| 63 | ⚠ RATIONALE NOT IN REPO | `08_GAPS.md:1221` | Which sales-order recency model is authoritative — DATAshaper's months-since-run-date or `dedup/weights.json`'s absolute years — and whether the year tiers were re-agreed | Answer: was the switch to absolute years a deliberate reproducibility choice, or a transcription of the DS bands? And were the year values re-agreed with Bernd Schnurrer after the switch? | Limitations — intended but not done (Pass 8 §E.2) + design rationale (Pass 9 D-29) |
| 64 | ⚠ UNVERIFIED | `03b_EXEMPLARS.md:26` | Whether any row of `PresentationTestData.xlsx` derives from a production SAP extract; no sampling frame, extraction query, date, or source system is recorded | Answer: state the provenance of the 500 rows — extracted from `test_77.Legacy` (with the query and date), hand-authored, or a mixture, and which rows are which | Evaluation — dataset provenance (Pass 7 §4.1) and external validity (Pass 7 §6) |
| 65 | ⚠ UNVERIFIED | `07_EVALUATION.md:546` | Same question as item 64, reached independently from the `Oracle_Summary` sheet's "calibrated to the US Qlic report distribution" note | Answer as item 64; additionally state what "the US Qlic report distribution" is and where it is recorded | Evaluation — dataset provenance (Pass 7 §4.1) |
| 66 | ⚠ UNVERIFIED | `07_EVALUATION.md:710` | Whether the `dedup/weights.json` band values were fitted to `PresentationTestData.xlsx`; explicitly "unfalsifiable from the repository" | Answer: were any band values chosen or adjusted after looking at this dataset? A yes makes every M-2/M-3 figure in-sample and must be declared | Evaluation — threats to validity (Pass 7 §6) |

---

## Group (b) · A measurement or query — 41 items

Each row names the exact command, SQL, KQL, or artefact read that closes the item.

| # | Marker | Source file:line | What is missing | What would resolve it | Thesis section affected |
|---|---|---|---|---|---|
| 67 | ⚠ UNVERIFIED | `00_INVENTORY.md:299` | Whether `_audit_upload` invokes the full enrichment pipeline, and therefore whether `/issues/compare` makes external calls | Read the body of `_audit_upload` in `api/routes.py` (called from `compare_file_issues`, `api/routes.py:628`) and record whether it calls `enrich_batch` or only `detect_issues` | System inventory — call graph (Pass 0 §3.5); bears on cost model (Pass 6b §c) |
| 68 | ⚠ UNVERIFIED | `00_INVENTORY.md:327` | A full function-level unreferenced-symbol sweep; only targeted `grep` candidates were confirmed | `.venv\Scripts\python.exe -m pip install vulture` then `.venv\Scripts\vulture.exe api dedup enrichment eval llm search utils config.py main.py function_app.py --min-confidence 60`, and reconcile the output against the G-35…G-40 dead-code list | System inventory — dead code (Pass 0 §4) |
| 69 | ⚠ MEASUREMENT REQUIRED | `00_INVENTORY.md:424` | Line-level test coverage; no coverage figure is committed | `.venv\Scripts\python.exe -m pip install pytest-cov` then `.venv\Scripts\python.exe -m pytest --cov=. --cov-report=term-missing`. Note the suite is red at this commit (3 failures, G-53), so record the coverage figure together with the failures | System inventory — test coverage (Pass 0 §5); evaluation threats (Pass 7 §6) |
| 70 | ⚠ UNVERIFIED | `01_TRACEABILITY.md:234` | Whether Table 2's cross-cutting behaviour list (X-1…X-31) is complete; it was compiled before the Pass 3 algorithm walk | Diff the procedure headings of `03_ALGORITHMS.md` Parts A–J against the X-items of `01_TRACEABILITY.md:198-233` and back-fill any procedure with no X-row | Requirements traceability (Pass 1 Table 2) |
| 71 | ⚠ MEASUREMENT REQUIRED | `02_ARCHITECTURE.md:287` | The wall-clock duration of one 50-row `/enrich` batch, which bounds the Functions-plan question (G-57) | `time curl -s -X POST "$API/enrich" -H 'Content-Type: application/json' -d @batch50.json -o /dev/null`, or read the `batch_ms` field the orchestrator already logs at `enrichment/orchestrator.py:838-841` from one production run | Architecture — timeouts and batching (Pass 2 §5); cost model (Pass 6b §c.5) |
| 72 | ⚠ MEASUREMENT REQUIRED | `02_ARCHITECTURE.md:491` | The `Legacy` and `Validation` row counts, which locate both pipelines against the ADF 5,000-row / 4 MB Lookup ceiling | `SELECT COUNT(*) FROM test_77.Legacy;` and `SELECT COUNT(*) FROM test_77.Validation;` | Architecture — scale limits (Pass 2 §11); limitations G-33 |
| 73 | ⚠ MEASUREMENT REQUIRED | `03b_EXEMPLARS.md:163` | Whether the address scope-table reduction fired on REC-02; the workbook records values, not execution | Re-run `POST /enrich` on the single REC-02 record with `LOG_LEVEL=DEBUG` and capture the address stage's decisions | Worked examples (Pass 3b §5.2); implementation Part G |
| 74 | ⚠ MEASUREMENT REQUIRED | `03b_EXEMPLARS.md:325` | Whether UC 12 (clear an identical duplicate Name 2) failed to fire on REC-09, or the workbook predates the implementation | Re-run `POST /enrich` on the REC-09 row (`Tropical Pharma Inc` in both Name 1 and Name 2) and inspect `use_cases_triggered` for `UC12` | Worked examples (Pass 3b §5.9); limitations G-19 |
| 75 | ⚠ MEASUREMENT REQUIRED | `03b_EXEMPLARS.md:476` | Whether `smart_title_case` lower-cased the hyphen-attached acronym on REC-15, or the workbook predates the rule | Re-run `POST /enrich` on `NATIONAL INSTITUTE OF STANDARDS AND TECHNOLOGY-NIST` and read `name1_enriched` | Worked examples (Pass 3b §5.15); limitations G-20 |
| 76 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:1675` | The production `OpenAIClient.extract_json` temperature setting; `llm/openai_client.py` was not read at that point in the pass | **Already settled inside the same document** at `03_ALGORITHMS.md:6589` and by G-41: `extract_json` accepts `temperature` and never forwards it; `call_openai` hardcodes `0.0` (`llm/openai_client.py:205`). The marker is stale and should be replaced by a cross-reference | Implementation — Part B orchestration (Pass 3) |
| 77 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:3246` | Whether `_sync_fetch_structured` passes `verify=` — the probe and link paths do, the main content fetch appears not to | Read `search/page_fetcher.py:218-222` and record whether `requests.get` receives a `verify` argument; if not, note that the main content fetch uses the `requests` default while D-38's resolver governs only the LLM/ROR/GLEIF clients | Implementation — Part D page fetching (Pass 3); external deps TLS (Pass 6 §2) |
| 78 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:3281` | Whether the curated mock LLM outputs reproduce actual model outputs; they are described in-file as "matching what gpt-4o would return" (`tests/mocks/openai_mock.py:7`) | Replay the prompts behind `tests/mocks/openai_mock.py:19-88` against the deployed `AZURE_OPENAI_DEPLOYMENT` and diff the returned JSON against the curated values; report the per-prompt agreement rate | Evaluation — threats to validity (Pass 7 §6); implementation Part D |
| 79 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:6020` | Whether the configured Azure deployment honours `reasoning_effort="low"` or silently ignores it | `GET /diag/dedup-llm` against the deployed app and inspect the response for the `reasoning_effort` rejection path (`dedup/llm.py:199-207`, detector at `:33-38`); a rejection proves it is dropped, a success does not prove it is honoured — confirm the model family from the Azure OpenAI deployment blade | Implementation — Part J dedup LLM (Pass 3); design rationale D-40 |
| 80 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:6026` | The effective server-side sampling temperature of the deployed dedup model; the request sends no `temperature` and no `seed` | Read the model and version from the Azure OpenAI deployment blade for `AOAI_DEPLOYMENT_DEDUP`, and measure determinism directly: issue the same block 10× and count distinct verdict sets | Implementation — Part J reproducibility (Pass 3); evaluation threats (Pass 7 §6) |
| 81 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:6376` | The recall of the seven contradiction markers (`dedup/scoring.py:444-451`) against real adjudicator phrasing | Collect the `reasoning` strings from one production `/api/dedup/cluster-block` run, hand-label which express a split, and compute the marker list's recall against that label set | Implementation — Part J election (Pass 3); evaluation (Pass 7) |
| 82 | ⚠ MEASUREMENT REQUIRED | `06_EXTERNAL_DEPS.md:660` | Per-call cost for every external service; none is derivable from the repository | Read four sources: the SerpAPI account plan/usage dashboard for the key in `SERPAPI_KEY`; Azure pricing for the deployment at `AZURE_OPENAI_ENDPOINT`; the ROR and GLEIF published terms of use | Cost model (Pass 6 §3.1) |
| 83 | ⚠ MEASUREMENT REQUIRED | `06_EXTERNAL_DEPS.md:665` | The per-service cost-driver table header — the source that would give each number | As item 82, one source per table row; the table at `06_EXTERNAL_DEPS.md:665-674` already names each source precisely | Cost model (Pass 6 §3.2) |
| 84 | ⚠ MEASUREMENT REQUIRED | `06_EXTERNAL_DEPS.md:673` | Azure egress cost attributable to arbitrary-host page fetches | Azure Cost Management → the `mdm-pipeline-api` Function App → Bandwidth meter, over a run of known `N` | Cost model (Pass 6 §3.2) |
| 85 | ⚠ MEASUREMENT REQUIRED | `06_EXTERNAL_DEPS.md:707` | Observed per-record and per-batch external-call counts | Add a Phase-1 equivalent of the `dedup_request` log record (`dedup/adjudicator.py:996-1011`), or log `BatchCache.stats` (`utils/cache.py:109-111`) at the end of `enrich_batch` (`enrichment/orchestrator.py:838`) and read `serp_entries` per batch — see also item 141 | Cost model (Pass 6 §3.3); limitations G-36 |
| 86 | ⚠ UNVERIFIED | `06b_CROSSCUTTING.md:263` | Whether `configure_logging`'s `basicConfig(force=True)` (`api/middleware.py:118`) displaces the Azure Functions worker's Application Insights handler — "the single highest-value item to verify against a live run" | Issue one request against the deployed app, then in Application Insights run `traces \| where timestamp > ago(15m) \| where message has "request_complete"`. An empty result is the positive finding: the deployed app ships no application telemetry | Observability (Pass 6b §b.1); limitations G-67 item 8 |
| 87 | ⚠ UNVERIFIED | `06b_CROSSCUTTING.md:361` | Whether `extra=` keys arrive as App Insights `customDimensions` — the only path on which token counts and latencies survive | `traces \| where message == "dedup_llm_call" \| project customDimensions` after one `/api/dedup/cluster-block` against the deployed app | Observability (Pass 6b §b.3); cost model (Pass 6b §c.4) |
| 88 | ⚠ UNVERIFIED | `06b_CROSSCUTTING.md:451` | Whether the Azure Functions host injects and honours W3C `traceparent` independently of application code | `requests \| project operation_Id, operation_ParentId, name \| take 20` on the deployed app, and check whether an ADF-originated call carries a parent operation id | Observability — correlation (Pass 6b §b.5) |
| 89 | ⚠ UNVERIFIED | `06b_CROSSCUTTING.md:456` | Whether ADF's Web-activity `output` object includes `ADFWebActivityResponseHeaders`, and therefore whether `X-Request-ID` reaches `usp_merge_legacy_enriched` | Open one `Web1` activity run in ADF monitoring and inspect its Output JSON for an `ADFWebActivityResponseHeaders` key | Observability — correlation (Pass 6b §b.5) |
| 90 | ⚠ UNVERIFIED | `06b_CROSSCUTTING.md:531` | Whether App Insights reports one operation name for all 13 routes, because `function_app.py:15` registers one catch-all | `requests \| summarize count() by name, url` — a single distinct `name` confirms the loss of per-endpoint latency and failure breakdown | Observability — request telemetry (Pass 6b §b.6) |
| 91 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:582` | Six observability unknowns that only a live run settles (table header) | Run the six queries listed verbatim at `06b_CROSSCUTTING.md:585-591`; items 86–90 above are five of the six, the sixth is App Insights retention, read from the `mdm-pipeline-insights` resource blade | Observability (Pass 6b §b.7) |
| 92 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:610` | Every unit price in the cost model; the README's Cost column is an ordinal design-time ranking with no unit | As item 82 | Cost model (Pass 6b §c.1) |
| 93 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:702` | The *mean* per-record external-call count; the per-tier table is a ceiling no single record attains, because the tiers are an escalation ladder | Instrument one run of known `N` (see item 141), then compute the mean per record rather than summing the tier column | Cost model (Pass 6b §c.3) |
| 94 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:754` | `N` — records in a run | `SELECT COUNT(*) FROM test_77.Legacy WHERE [group code] = '<the run''s group code>';` | Cost model — symbols (Pass 6b §c.5.1) |
| 95 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:756` | `M` — rows entering deduplication | `SELECT COUNT(*) FROM test_77.Validation;` | Cost model — symbols (Pass 6b §c.5.1) |
| 96 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:757` | `K` — distinct `block_id` values over those `M` rows | `SELECT COUNT(DISTINCT [Block ID]) FROM test_77.Validation;` | Cost model — symbols (Pass 6b §c.5.1) |
| 97 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:763` | The four unit-price symbols `p_serp`, `p^in_1`, `p^out_1`, and the Phase-2 pair (table header) | As item 82; the table at `06b_CROSSCUTTING.md:765-770` names the exact source for each symbol | Cost model — unit prices (Pass 6b §c.5.1) |
| 98 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:820` | The six quantities that reduce `C_enrich` to a per-record mean (table header) | Run the six remedies listed verbatim at `06b_CROSSCUTTING.md:822-827`. The document's own conclusion: "A single instrumented run over a known `N` settles every Phase 1 unknown at once, provided `response.usage` is captured first" (`:829-831`) — see item 141 | Cost model (Pass 6b §c.5.3) |
| 99 | ⚠ MEASUREMENT REQUIRED | `07_EVALUATION.md:60` | Any Phase-1 accuracy figure; no component computes precision, recall, accuracy, or error rate for enrichment | Author a per-field labelled answer key over a sample of `PresentationTestData.xlsx` — minimally `name1_enriched`, `name2_enriched`, `website_url`, `record_type` — then compare `/enrich` output against it. The `Oracle_*` sheets hold aggregate expectations and a cluster-level key, not per-field labels | Evaluation — metrics (Pass 7 §2); results (Pass 7 §7) |
| 100 | ⚠ MEASUREMENT REQUIRED | `07_EVALUATION.md:456` | Any non-trivial value of M-3; the harness returns zeros because its two ground-truth columns do not exist | Same labelling task as item 172; once the columns exist, `.venv\Scripts\python.exe -m eval.dedup_eval PresentationTestData.xlsx --out eval_report.json` produces the figure | Evaluation — M-3 (Pass 7 §3.4) |
| 101 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:281` | Whether UC 12 fired on REC-09 — duplicate of item 74, reached from the gaps side | As item 74 | Limitations G-19 (Pass 8 §A) |
| 102 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:294` | Whether `smart_title_case` lower-cased `-NIST` on REC-15 — duplicate of item 75 | As item 75 | Limitations G-20 (Pass 8 §A) |
| 103 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:426` | The share of input records carrying a populated Name 2, which bounds how much of the corpus the Name-2 gap (G-5) applies to | `SELECT COUNT(*) FROM test_77.Legacy WHERE LTRIM(RTRIM([Name 2])) <> '';` against `SELECT COUNT(*) FROM test_77.Legacy;` — or count non-blank `Name 2` cells in the source workbook | Limitations G-5 (Pass 8 §B.1) — this figure sizes the single largest limitation |
| 104 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:510` | The `Validation` row count, locating the unbatched dedup Lookup against the 5,000-row cap — duplicate of item 72 | `SELECT COUNT(*) FROM test_77.Validation;` | Limitations G-33 (Pass 8 §B.2) |
| 105 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:712` | A coverage figure — duplicate of item 69 | `pytest --cov=. --cov-report=term-missing`, after `pip install pytest-cov` | Limitations G-55 (Pass 8 §B.5) |
| 106 | ⚠ UNVERIFIED | `08_GAPS.md:1008` | Whether `basicConfig(force=True)` discards the App Insights handler — duplicate of item 86 | As item 86 | Limitations G-67 item 8 (Pass 8 §D) |
| 107 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:1071` | The share of the SAP extract removed by the ZFI gate, which bounds the population the whole system applies to | The excluding script is not located (G-26), so the count must come from the source system: `SELECT COUNT(*) FROM <SAP extract source> WHERE <ZFI predicate>;` against the unfiltered count — which requires the predicate from item 61 first | Limitations G-69 (Pass 8 §E.1); external validity (Pass 7 §6) |

---

## Group (c) · An artefact export — 8 items

Each requires obtaining a file that exists outside this repository. Six are the
`VERIFY-BY-FREEZE` claims, which resolve as a single export-and-diff exercise at the
2026-08-21 code freeze.

| # | Marker | Source file:line | What is missing | What would resolve it | Thesis section affected |
|---|---|---|---|---|---|
| 108 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:47` | Confirmation that a group-code predicate was added to all three Lookup activities (Enrichment `Lookup1`+`Lookup2`, Deduplication `Lookup1`) | Re-export both ADF pipeline JSONs from the Tillit tenant after the freeze and grep each Lookup's `sqlReaderQuery` for a group-code predicate; compare against `CONTEXT-EXTERNAL.md:64,106,226` | Architecture — scoping predicate (Pass 2 §2); limitations G-31, G-77 |
| 109 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:229` | Confirmation that an `enriched_at` watermark exists on `Legacy` and that enrichment `Lookup1` filters to unenriched rows | Re-export the enrichment pipeline JSON and check `Lookup1`'s query for a watermark predicate; separately obtain the `test_77.Legacy` DDL and confirm the column exists | Architecture — idempotence (Pass 2 §4.1); limitations G-32, G-77 |
| 110 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:231` | Confirmation that `retry > 0` was set on the enrichment `Web1` and `Merge Back` activities | Re-export the enrichment pipeline JSON and read the `retry` field of each activity's `policy` block; compare against `CONTEXT-EXTERNAL.md:54,96,126,154` | Architecture — failure handling (Pass 2 §4.1); limitations G-44, G-67 item 1, G-77 |
| 111 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:255` | Confirmation that deduplication is batched by `block_id` through a ForEach, replacing the whole-table Lookup | Re-export the deduplication pipeline JSON and check for a `ForEach` over distinct `block_id` values in place of the single unfiltered Lookup at `CONTEXT-EXTERNAL.md:224-236` | Architecture — dedup batching (Pass 2 §5); limitations G-33, G-77 |
| 112 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:423` | Confirmation that the `enriched_at` watermark makes the enrichment merge-back idempotent and resumable | Same export as item 109, plus one deliberate mid-run failure: rerun the pipeline and confirm batches 1…*N*−1 are not re-enriched | Architecture — merge-back semantics (Pass 2 §8); limitations G-32 |
| 113 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:497` | Confirmation that the `block_id` ForEach keeps each dedup Lookup under the 5,000-row / 4 MB ceiling | Same export as item 111, plus `SELECT MAX(c) FROM (SELECT COUNT(*) c FROM test_77.Validation GROUP BY [Block ID]) t;` to confirm the largest block is under 5,000 rows | Architecture — scale limits (Pass 2 §11); limitations G-33 |
| 114 | ⚠ UNDOCUMENTED / ⚠ UNVERIFIED | `04_PARAMETERS.md:259` | The address-validation auto-write-back threshold: the `80%` value, the validating service, and the comparison operator (`>` vs `≥`) are all `[AUTHOR]`-stated only | Export the ADF pipeline JSON for workflow step 6 from the Tillit tenant, in the same form as the two pipelines at `CONTEXT-EXTERNAL.md:39-315`. The export gives the service, the endpoint, and the operator; the *choice* of 80% remains an author question | Parameters (Pass 4 §1); limitations G-27; external deps (Pass 6 §2.8) |
| 115 | ⚠ UNVERIFIED | `06_EXTERNAL_DEPS.md:631` | The identity of the address-validation service — not determinable from any artefact available to this pass | Same export as item 114 | External dependencies (Pass 6 §2.8); limitations G-27 |

---

## Group (d) · A repository change — 57 items

Source, test, fixture, or dataset. Fifty-four are `⚠ NO FIXTURE COVERAGE` or the equivalent
`⚠ UNVERIFIED — no fixture` in `03_ALGORITHMS.md`; each already names the input required, and the
resolution below states it as a concrete test to write. Two are reachable-but-unexercised issue
codes, one is the dedup ground-truth labelling.

### (d.1) Uncovered procedures and branches — `03_ALGORITHMS.md`, 54 items

Thesis section for all 54: **Implementation (Pass 3, part as shown)**, with **Evaluation — test
coverage (Pass 7 §6)** as the secondary section, since `08_GAPS.md` G-66 lifts the whole set into
the limitations chapter as "38 procedures documented as having no fixture coverage".

| # | Marker | Source file:line | What is missing | What would resolve it | Part |
|---|---|---|---|---|---|
| 116 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:365` | The UC 7 Pattern A loop (`enrichment/preprocess.py:1432-1473`) and its org-payload branch (`:1451-1461`) | A `preprocess_record` test with `Attn:` inside `name1`, `name3` or `name4` (not `name2`, which UC 15 routes) — one case with a person payload, one with an org payload | A |
| 117 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:474` | The Case A / Case B priority collision when a person payload contains `Co` or `S.A.` | A record whose c/o payload is a person name containing a `_LEGAL_SUFFIX_RE` alternate, e.g. `c/o Jean Co Martin`, asserted to route to Case A | A |
| 118 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:767` | `_LEGAL_SUFFIX_RE` marking an address line as an org (`enrichment/preprocess.py:1826-1841`) | A street value containing `Co` or `S.A.` as a word, e.g. `100 Co Op Lane`, asserted to remain in the street slot | A |
| 119 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:828` | `_LOCATION_FRAGMENT_RE` accepting 1–2 letter identifiers (`enrichment/preprocess.py:696`) | A record with `Hall A` or `MS B` in a name slot, asserted for which slot it lands in | A |
| 120 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:883` | The UC 10 full-field opaque-code clearing loop (`enrichment/preprocess.py:1577-1581`) | A `preprocess_record` test with `name2="B800000123"` (the whole field an opaque code), asserted cleared | A |
| 121 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:929` | `_normalise_dba` / UC 11 through `preprocess_record`; existing DBA tests target downstream consumers only | A `preprocess_record` test with a DBA marker variant in a name field, asserting both the normalised value and membership in `dba_fields` | A |
| 122 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:934` | Whether `_normalise_dba` marks `changed` for a casing-only variant (lowercase `dba` → `DBA`) | Extend the item 121 test with a lowercase-`dba` input, asserting `changed is True` and the field in `dba_fields` | A |
| 123 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1093` | Umlaut transliteration `ae` in legal-suffix normalisation (`enrichment/preprocess.py:818`) | A record with `Gesellschaft mit beschraenkter Haftung` (transliterated, not `beschränkter`), asserted to collapse to `GmbH` | A |
| 124 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1157` | Compound admin values against `is_admin_unit` (`utils/text_utils.py:1005-1011`) | A record with `Finance and Administration` in Name 2, asserting whether it is treated as an admin unit | A |
| 125 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:1215` | Partially-cased input to `smart_title_case`; the all-upper gate disables the routine on any lowercase character | A test with `McDONALD` asserting pass-through unchanged, and a second with a genuinely mixed-case name | A |
| 126 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:1281` | `is_granular_unit` on a bare word; the suffix regex requires a preceding token | A test asserting `is_granular_unit("Laboratory") is False` and `is_granular_unit("Chemistry Laboratory") is True` | A |
| 127 | ⚠ UNVERIFIED (unconsumed) | `03_ALGORITHMS.md:1378` | `tests/fixtures/mixed_batch_10_records.json` is loadable by `conftest` and referenced by no test | Either write a batch test that loads it and asserts the 10 per-record outcomes, or delete the fixture and the loader (`tests/conftest.py:82-97`) — see G-56 | B |
| 128 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1379` | The gather-level exception branch (`enrichment/orchestrator.py:811-821`) | A test injecting a mock (page fetcher or address-stage LLM) that raises inside `_finalise_and_return`, i.e. after `_enrich_single`'s own handler has run | B |
| 129 | ⚠ UNVERIFIED (dead code) | `03_ALGORITHMS.md:1608` | `determine_enrichment_status` / `should_flag_for_review` are documented as pipeline behaviour and have no caller | Decide and act: either wire them at the tier call sites in place of the inline logic, or delete `enrichment/confidence.py` and re-cite `09_DECISIONS.md` D-5 to the inline sites — see **X-1** in Part 1 | B |
| 130 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1619` | No test imports or exercises either `confidence.py` function | Subsumed by item 129; if the module is retained, add a table-driven test over the `(confidence, match, tier, source)` tuple | B |
| 131 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1652` | `run_overflow_check` and the UC 0 branch; existing orchestrator records hit the mock's unrecognised-prompt fallback, which lacks `is_overflow` | A record with both names populated and distinct, plus a mock LLM entry returning `{"is_overflow": true, "confidence": "high", "reasoning": …}` added to `tests/mocks/openai_mock.py` | B |
| 132 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1887` | Cities shorter than 4 characters in the ROR location guard | A record with city `Ulm` and a generic Name 1, asserting the same-city subset guard does not engage | C |
| 133 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:1957` | False-positive state-abbreviation expansion of an ordinary word (`mass`, `miss`, `wash`, `ind`) | A company named e.g. `Mass Analytics` asserted for what ROR query form is produced (`enrichment/tier1_ror.py:69-73`) | C |
| 134 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2010` | A multi-location ROR org whose `locations[0]` is the wrong country | A mock ROR org with two locations — first in the wrong country, second matching — asserting the current reject behaviour | C |
| 135 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2054` | `_extract_org_fields` end-to-end with multiple `acronym` entries | A mock ROR org dict carrying both a historical and a current acronym entry, asserting which is selected | C |
| 136 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2058` | A display name legitimately ending in a parenthesised country | A mock ROR org named e.g. `Bank of America (United States)` asserting whether the suffix is stripped | C |
| 137 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2082` | `extract_website_from_ror` against a populated `links[]`; HTTP fixtures set `"links": []` and `MockRORClient` returns `website` directly | A ROR org dict `{"links": [{"type": "website", "value": "https://…"}]}` passed through `extract_website_from_ror` | C |
| 138 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2156` | `_match_child_locally` invoked directly; fixture children exist but no test scores them | A record with a ROR-matched Name 1 and a Name 2 near a child label, e.g. `Dept of Chemistry` against MIT's children (`tests/mocks/ror_mock.py:31-39`) | C |
| 139 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2198` | `_token_covers` false-positive prefix collision (`internal` ↔ `international`) | A canonical/input pair differing only by an unrelated word sharing a 4-character prefix, asserting the guard's verdict | C |
| 140 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:2397` | The GLEIF retry loop; all HTTP tests pass `max_retries=0` | A mock transport returning 5xx then 200, with `max_retries ≥ 1`, asserting the successful retry | C |
| 141 | ⚠ UNVERIFIED (dead code) | `03_ALGORITHMS.md:2485` | `BatchCache.get_ror` / `set_ror` (`utils/cache.py:75-81`) have no production caller; the operative cache is `_ror_cache` in `tier1_ror.py` | Decide and act: delete the ROR store from `BatchCache`, or route `tier1_ror` through it. Separately, log `BatchCache.stats` at `enrichment/orchestrator.py:838` — that one line also closes items 85, 93 and 98 | D |
| 142 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:3254` | The real HTML paths (`_sync_fetch_structured`, `_extract_breadcrumb`, `_sync_fetch_outgoing_links`, `_sync_resolve_final_url`); every test substitutes `MockPageFetcher` | An HTML fixture containing a `<title>`, an `<h1>` and a breadcrumb element (`aria-label="breadcrumb"`), served over a local HTTP stub or injected below `requests.get` | D |
| 143 | ⚠ UNVERIFIED (no caller) | `03_ALGORITHMS.md:3338` | The `prefetched_results` reuse branch (`enrichment/website_resolver.py:472-484`) has a unit test and no orchestrator caller — residue of the Tier 2B removal | Decide and act: delete the branch and its test, or re-wire it if Tier 2B is restored (item 62) | F |
| 144 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:3818` | `strip_tld` on a bare single-label domain (`enrichment/search_terms.py:87`) | A test passing `domain="edu"` and asserting the resulting search term | F |
| 145 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:3883` | The ST2 Rule-3 dept-domain fallback with an empty Name 2; every dept-domain test also carries a non-empty Name 2, which Rule 2 now wins | A record with `department_domain` set and `name2` empty, asserting ST2 derives from the host | F |
| 146 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:3934` | The terminal-normalisation truncation branch; every fixture's term is already ≤ 32 chars after fill | A Name 2 phrase exceeding 32 characters after `_fill_to_width`, asserting the word-boundary cut | F |
| 147 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4023` | `_subdomain_acronym` step 9 (letters ≠ initials); documented only in a docstring | A test with `york.cuny.edu` + `Department of Geology`, asserting rejection at step 9 (`enrichment/search_terms.py:451`) | F |
| 148 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4111` | The ST2 unit-phrase guard firing to `None` on its True branches | A Name 2 that is both a unit phrase and matches the research-institution regex, e.g. `School of Public Health`, asserting ST2 is `None` and the record is flagged | F |
| 149 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4147` | `_name2_is_unit_phrase` *as the deciding rule*; in every fixture Rule 2 wins first | A record with `department_domain` set and a Name 2 that is not a unit phrase, forcing the function to decide | F |
| 150 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4156` | Stacked `www.`/`web.` prefixes; the reverse stacking `web.www.x.edu` loses only `web.` | A test asserting both `www.web.x.edu` and `web.www.x.edu` outcomes | F |
| 151 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4157` | Dept-domain-as-path reduction, where the host equals the base and step 5 returns the *institution* name as the unit handle | A record with `department_domain = "https://ufl.edu/departments/biology"` and base `ufl.edu`, asserting the returned handle | F |
| 152 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4417` | `_run_address_stage` itself; tests call `process_address` and `merge_into_result` directly | An orchestrator-level test asserting `_run_address_stage` is entered and its result merged | G |
| 153 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4545` | `_apply_residual_llm` (address step 4); every `process_address` test passes `llm_client=None` | A `process_address` test passing a mock LLM client returning a residual classification above and below `0.85` | G |
| 154 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:4616` | Comma-bearing building names; segments split on commas and pipes | A street value such as `Smith, Jones and Co Building` asserting whether it survives segmentation | G |
| 155 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:4756` | A qualifier after a trailing directional or unit token | A street value `300 Tech Park Dr NW GATE C` asserting the qualifier is not split | G |
| 156 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:4804` | `_named_building_value` rejections at the address call site (bare `Bldg`, `Building 5`) | Two direct `_named_building_value` tests asserting both rejections against `_BUILDING_SUFFIX_RE` | G |
| 157 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:4930` | A real street with an embedded facility phrase, which `_extract_logistics` consumes wholly | A street value such as `500 Distribution Center Rd` asserting whether the whole value is rerouted | G |
| 158 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:5380` | Float-typed record-id cells in the `/issues/compare` join (`"1001"` vs `"1001.0"`) | Two XLSX fixtures whose Customer column is text in one and numeric in the other, asserting the join outcome | H |
| 159 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:5536` | All-empty-address rows deriving one shared block id and being adjudicated together | A dedup request with several rows carrying no address parts and no `block_id`, asserting they share a derived block | I |
| 160 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:5599` | LEI columns on the dedup file route; `_DEDUP_HEADER_ALIASES` has no `leiid` key so `LEI` / `LEI ID` is dropped | An XLSX fixture with a `LEI ID` column through `/api/dedup/file`, asserting the value reaches `DedupRow.lei_id`. Fixing it is a one-line alias addition at `api/routes.py:688-707` (G-48) | I |
| 161 | ⚠ UNVERIFIED (unconstrained) | `03_ALGORITHMS.md:5710` | No prompt clause restricts the dedup model to the supplied evidence; `dedup/prompts.py:36` actively invites world knowledge | Decide and act: add an evidence-only clause to `dedup/prompts.py:19-58`, or state in the thesis that world knowledge is deliberately in scope and measure its effect by ablating the `:36` sentence over one block set | J |
| 162 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:5780` | A Mode B ordering pathology; assignment is greedy in signature order and an early wrong "new" is not revisited | A Mode B test whose signature order forces a wrong early assignment, asserting whether the residue pass or a guard corrects it | J |
| 163 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:5925` | A `_reasoning_disowns_membership` phrasing outside the marker list | A mock adjudicator reasoning that expresses a split in unlisted wording, asserting the missed demotion | J |
| 164 | ⚠ UNVERIFIED (unconstrained) | `03_ALGORITHMS.md:5996` | Duplicate of item 161, reached from the Mode B section | As item 161 | J |
| 165 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:6154` | A missing or corrupt `dedup/weights.json`; `load_weights` propagates the exception with no guard, against a docstring promising scoring "NEVER raises" | A test pointing `load_weights` at a missing path and at malformed JSON, asserting the current propagation — then a guard, if the docstring is to hold | J |
| 166 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:6250` | A criterion-less weights dict passed directly to `score_row`, raising `KeyError` | A direct `score_row` call with a weights dict missing `sales_order_last_used`, asserting the current behaviour | J |
| 167 | ⚠ UNVERIFIED (not implemented) | `03_ALGORITHMS.md:6423` | No code path compares `scored_with_weights_version` at approval time; it is stamped and carried only, so the documented drift defence does not run | Implement the comparison in `apply_approval` (`dedup/scoring.py:551-615`), rejecting an approval whose fingerprint differs from the proposal's — or remove the drift-defence claim | J |
| 168 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:6526` | Duplicated headers in the scoring workbook, which silently bind to the first column | An XLSX fixture with a repeated header through `dedup/scoring_xlsx.py`, asserting first-occurrence-wins | J |
| 169 | ⚠ UNVERIFIED (no fixture) | `03_ALGORITHMS.md:6536` | Caller-supplied weights with overlapping numeric bands, where points would depend on insertion order | A `score_row` call with two overlapping bands for one criterion, asserting which band wins | J |

### (d.2) Dataset and issue-code gaps — 3 items

| # | Marker | Source file:line | What is missing | What would resolve it | Thesis section affected |
|---|---|---|---|---|---|
| 170 | ⚠ NO FIXTURE COVERAGE | `03b_EXEMPLARS.md:87` | `G1-NAME-001` "Name Overflow Across Fields" is reachable but no repository record satisfies it | Add one record to `PresentationTestData.xlsx` (or a JSON fixture) with a Name 1 carrying no legal-entity suffix followed by a Name 2 opening with a connector or lowercase word, per `enrichment/issue_detection.py:296-305` | Worked examples — code coverage census (Pass 3b §4); limitations G-80 |
| 171 | ⚠ NO FIXTURE COVERAGE | `03b_EXEMPLARS.md:88` | `G3-ADDR-013` "Two Distinct Street Addresses on Record" is reachable but no repository record satisfies it | Add one record with two street slots holding two *different* values that both satisfy `_looks_like_street`, per `enrichment/issue_detection.py:419-424` | Worked examples — code coverage census (Pass 3b §4); limitations G-80 |
| 172 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:682` | The `expected_cluster` and `expected_routing` ground-truth columns exist in no repository workbook, so `eval/dedup_eval.py` returns its zero-guard values | Author both columns on `PresentationTestData.xlsx`: for each of the 500 rows, the expected cluster label and the expected routing verdict. The `Dedup_Scoring_Oracle` sheet holds cluster-level expectations in a different shape and can be projected onto per-row columns as a starting point. This is a labelling task, not a command; once done, item 100's command produces the figure | Evaluation — M-3 (Pass 7 §3.4); limitations G-52, G-79 |

---

## Group (e) · Convention statement or restatement — 19 items

These matched the sweep but are not separate open items: twelve define the marker conventions,
six restate an item already counted above, and one is a negative use of the string. They are listed
so the sweep reconciles to 191 and so no reader treats them as unresolved work.

| # | Marker | Source file:line | Why it is not a separate item |
|---|---|---|---|
| 173 | VERIFY-BY-FREEZE | `02_ARCHITECTURE.md:28` | Defines the `<!-- VERIFY-BY-FREEZE: … -->` convention used by items 108–113 |
| 174 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:19` | Method statement — declares the marker convention for Pass 3 |
| 175 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:21` | Method statement — declares the `⚠ UNVERIFIED —` prefix convention |
| 176 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:104` | Method statement — restates the in-place marking rule |
| 177 | ⚠ NO FIXTURE COVERAGE | `03_ALGORITHMS.md:5437` | Part-H summary restating item 158 (`:5380`) |
| 178 | ⚠ UNVERIFIED | `03_ALGORITHMS.md:6589` | **Negative use** — reads "⚠ UNVERIFIED-free observation"; the finding (G-41) is verified, not open. Matched only because the string is a prefix |
| 179 | ⚠ UNDOCUMENTED | `04_PARAMETERS.md:22` | Convention statement — declares `⚠ UNDOCUMENTED — author to supply` for the rationale column |
| 180 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:20` | Convention statement — declares the marker for Pass 6b |
| 181 | ⚠ UNVERIFIED | `06b_CROSSCUTTING.md:1267` | Pass summary restating item 86 (`:263`) |
| 182 | ⚠ MEASUREMENT REQUIRED | `06b_CROSSCUTTING.md:1343` | Pass summary restating items 92–98 |
| 183 | ⚠ MEASUREMENT REQUIRED | `07_EVALUATION.md:827` | Threats-to-validity restatement of item 69 (coverage) |
| 184 | ⚠ MEASUREMENT REQUIRED | `07_EVALUATION.md:830` | Threats-to-validity restatement of items 82 and 92–98 (cost) |
| 185 | ⚠ UNVERIFIED | `08_GAPS.md:20` | Convention statement — declares the `⚠ UNVERIFIED —` prefix for Pass 8 |
| 186 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:21` | Convention statement — declares the marker for Pass 8 |
| 187 | ⚠ UNDOCUMENTED | `08_GAPS.md:840` | Convention statement inside G-64 — declares the rationale-column marker |
| 188 | ⚠ UNDOCUMENTED | `08_GAPS.md:868` | Aggregate restatement of the 58 `04_PARAMETERS.md` rows (items 1–58) |
| 189 | ⚠ NO FIXTURE COVERAGE | `08_GAPS.md:940` | Restates the Pass 3 marking method behind items 116–169 |
| 190 | VERIFY-BY-FREEZE | `08_GAPS.md:1198` | Restates the convention behind items 108–113, inside G-77 |
| 191 | ⚠ MEASUREMENT REQUIRED | `08_GAPS.md:1259` | Framing example in §F showing how a marked entry lifts into the limitations section |

---

## Reading the register

Three observations follow from the counts and are stated once.

**The largest group is author knowledge, and it is concentrated.** 66 of 191 items (35%) cannot be
closed by any command, export, or code change. 58 of those 66 are parameter rationales in one
table (`04_PARAMETERS.md` §1). They are homogeneous — each asks "why this value" — so they are
answerable in one sitting, and until they are, `08_GAPS.md:1273-1276` applies: a decision recorded
without a reason is not a decision the thesis can argue.

**A single instrumented run closes a disproportionate share of group (b).** Items 85, 93, 94–98 and
141 all reduce to one `/enrich` run over a known `N` with `response.usage` captured in
`call_openai` (`llm/openai_client.py:198-208`) and `BatchCache.stats` logged at
`enrichment/orchestrator.py:838` — a two-line change, as `06b_CROSSCUTTING.md:823` states.
Items 86–91 all reduce to one deployed request plus six Application Insights queries. Two
experiments therefore close 15 of the 41 measurement items.

**Group (d) is not uniform.** 51 of its 57 items are missing tests for code that runs in
production, which is the ordinary reading. The other six are decisions disguised as coverage gaps —
items 129 (`confidence.py` dead), 141 (`BatchCache` ROR store dead), 143 (`prefetched_results`
orphaned), 161/164 (the dedup prompt's evidence constraint), and 167 (the weights-fingerprint drift
defence never implemented). Each of those six is closed by deleting or wiring code, not by writing
a test, and each currently supports a documentation claim that the code does not.

**Stop.**
