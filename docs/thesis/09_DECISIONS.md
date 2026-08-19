Generated: 2026-08-17 · Commit: 515cc7c1a84f55f817d63b4f3f094ce47d57f7fd · Branch: diag/website-trace

# Pass 9 — Decision Log

## 0 · Method, sources, and evidence conventions

This pass mines the repository's history for design decisions and, for each one, records what
the history shows about the alternatives that were written and removed, and about the reason —
where a reason exists in the record at all.

Sources mined:

- The full `git log` reachable from `HEAD` (51 commits), including every commit message body and
  the diffs of the commits that restructure a subsystem.
- Pickaxe searches (`git log -S`) for symbols, configuration keys, and dependencies that appear
  and then disappear. Each such search is quoted in the entry that relies on it, so the claim is
  reproducible.
- Substantive code comments and module docstrings. A docstring that states a constraint or names
  a rejected option is treated as evidence; a docstring that only restates the function name is
  not.
- `README.md`, in particular its `## Changelog` (`README.md:1980-2068`), which records several
  rejections in the author's own words.
- `Domain_DeptDomain_SearchTerm_Logic.pdf` and `Website_Trace_Findings.pdf` at the repository
  root, both added in `515cc7c`.
- The DATAshaper vendor onboarding transcripts (`Datashaper-Tutorial-Part1.txt`,
  `Part2`, `Part3`), which are the only record of the pre-existing system that several decisions
  in this repository replace or defer to. These are an internal recorded call, cited here for
  traceability, not a publishable source.

**No ADRs exist.** There is no `docs/adr`, `doc/adr`, `decisions/`, or equivalent directory in
any commit; `git log --all --diff-filter=A --name-only` produces no file matching `adr` or
`decision`. The decision record is therefore commit messages, code comments, and the README —
nothing more.

Evidence conventions follow Passes 0–8. Current-tree claims cite `path/file.py:LINE`. Historical
file states cite `<commit>:<path>:<line>` and are recoverable with `git show <commit>:<path>`.
Commit messages are quoted verbatim in block quotes.

**Honesty marker.** Where the history establishes *what* changed and *when* but not *why*, the
entry says so with ⚠ **RATIONALE NOT IN REPO — author to supply** and stops. No reason is
reconstructed from the shape of the change. A decision with an unrecorded reason is a question
for the author, not a gap for this document to close. Section 10 collects every such case in one
table; seventeen are listed there, spanning sixteen of the forty-one entries below.

**Correction to an earlier statement in this file.** The partial version of this document stated
that eight commits are reachable from `HEAD`. That figure was the count of commits touching one
path, not the repository. `git rev-list --count HEAD` returns **51**.

---

## 1 · Shape of the history

The history is linear — `git log --merges` returns nothing — and runs from `f77080b`
(2026-04-09, "Initial Enrichment Code") to `515cc7c` (2026-08-12). Two authors appear: `Ajay`
(`Apoorva.Ajay@bruker.com`) and `Suzu`. Fourteen commits carry a
`Co-Authored-By: Claude Opus 4.8` trailer (`git log --pretty=format:'%b' | grep -c "Co-Authored-By"`);
those commits also carry the most detailed message
bodies, and are consequently where most of the recorded rationale in this document comes from.
Commits authored without that trailer are frequently one-line ("Updated rules", "Deduplication
endpoint", "Phase 2 thesis", "Added changes to handle more cases") and carry no rationale at all.

Author dates are not monotonic along the parent chain: `b9f772a` (author date 2026-06-29) is an
ancestor of `600823c` (author date 2026-06-19). Ordering claims in this document therefore use
the parent chain, and dates are quoted as recorded.

Three branches exist: `main` (at `8d07acb`), `enrichment-resolution-fixes` (at `b910dfe`, an
ancestor of `main`), and `diag/website-trace` (at `515cc7c`, one commit ahead of `main`). No
branch contains work that is not reachable from `HEAD`; `git log --all` and `git log HEAD` return
the same 51 commits.

Four commits do the structural work and account for most of this document:

| Commit | Date | Files changed | What it establishes |
|---|---|---|---|
| `f77080b` | 2026-04-09 | 54 added | The tiered pipeline, all four tiers, the search/page-fetch/LLM stack |
| `13a1274` | 2026-06-17 | 11 (+1860 lines) | The whole `dedup/` package and `/api/dedup/cluster-block` |
| `efe1379` | 2026-07-22 | — | Scoring, election, approval, the stable cluster key |
| `515cc7c` | 2026-08-12 | 48 (+5055 / −333) | The Phase-1 overhaul: search terms, website/domain/dept-domain, street and person routing |

---

## 2 · Summary table

| ID | Decision | Date and commit | Alternatives visible in history | Why the chosen option | Evidence |
|---|---|---|---|---|---|
| D-1 | Restrict Tier 2A to populating a blank Name 2, and unwire Tier 2B, disabling both Name-2 correction paths | 2026-04-11 · `635d5ba` | Both paths were wired and reachable in `f77080b`: the Tier 2A gate carried no Name-2 condition, and Tier 2B ran as a fallback after Tier 2A | ⚠ **NOT RECORDED** — the commit body describes refining both modules but does not mention removing the Tier 2B call or adding the gate condition | `f77080b:enrichment/orchestrator.py:476-480,500,506,531,540`; `635d5ba:enrichment/orchestrator.py:440,488-493`; `enrichment/orchestrator.py:2451-2457,2473`; `enrichment/tier2a_contact.py:80` |
| D-2 | Tiered escalation ordered cheapest-and-most-deterministic first | 2026-04-09 · `f77080b` | None — present in the first commit; no flatter or differently-ordered arrangement appears in any commit | Recorded in the README as a cost/confidence trade-off, principle 2 "Deterministic before probabilistic" | `README.md:78-98`; `enrichment/orchestrator.py:2384,2468,2543` |
| D-3 | Insert the GLEIF/LEI registry as a Tier 1 company step *before* the LLM company-canonical fallback | 2026-06-29 · `6d1805e` | Before this commit companies had no registry step: a ROR miss went straight to the LLM | Recorded: a free registry lookup precedes the paid probabilistic one, and supplies an identifier Phase 2 can converge on | `enrichment/tier1_lei.py:1-8`; `enrichment/orchestrator.py:2058,2152,2198`; `README.md:78-98` |
| D-4 | Extract from structured page elements (URL path, title, H1, breadcrumb) rather than free-form body text | 2026-04-09 · `f77080b`, tightened 2026-04-11 · `635d5ba` | `635d5ba` also cut `MAX_PAGE_CONTENT_CHARS` from 3000 to 1500, reducing the body text available | Recorded as design principle 3; the tier-2B prompt states the input Name 2 is withheld so the model cannot echo it | `README.md:94-98`; `enrichment/tier2b_dept.py:247-266`, `:252-258` |
| D-5 | Flag rather than infer: never fabricate, return originals and flag when confidence is low | 2026-04-09 · `f77080b`, extended through `515cc7c` | None removed — the rule is additive across the history, with new short-circuits added rather than the rule relaxed | Recorded as design principle 1 and enforced at six separate sites | `README.md:94`; `enrichment/confidence.py:33,51-55`; `enrichment/orchestrator.py:390,1888,2417,2571`; `llm/prompts.py:361,401` |
| D-6 | Verify LEI candidates with `token_sort_ratio`, not `token_set_ratio` | 2026-06-29 · `6d1805e` | `token_set_ratio` is named in the README as considered and rejected; it does not appear in any commit's code | Recorded: `token_set_ratio` "scores any contained substring 100" | `README.md:2013`; `enrichment/tier1_lei.py:22-27` |
| D-7 | Reject a registry candidate whose country contradicts the record's, turning it into a clean miss | 2026-06-29 · `6d1805e` | Before this commit both registries accepted the best name match regardless of country | Recorded: "a wrong-country id is worse than none — it would wrongly converge distinct legal entities in Phase 2 dedup" | `README.md:2061-2064`; `enrichment/tier1_lei.py:28-34` |
| D-8 | Exclude the record's own location tokens from ROR's subset-match shortcut | 2026-07-03 · `1496fd4` | The prior scorer counted city tokens toward a subset match, returning a false 1.0 | Recorded in the commit body with the failing case | `1496fd4` message; `enrichment/tier1_ror.py:45`; `tests/test_tier1.py:232` |
| D-9 | Adopt ROR's official name for an abbreviated institution rather than keeping the input | 2026-07-03 · `d9ea45a` | The prior rule kept the user's Name 1 whenever ROR's form appeared to drop a distinctive token | Recorded: the 4-char token floor treated "Uni" as not covering "University"; convergence in dedup is named as a second reason | `d9ea45a` message; `README.md:778` |
| D-10 | Recover a misspelled company name by re-querying GLEIF on the LLM's proposed correction | 2026-07-03 · `7ea0376` | The identity guard previously blocked the correction outright; the address signal alone produced no result | Recorded at length, including why the two changes are one commit | `7ea0376` message |
| D-11 | Collapse long-form legal suffixes on output and inside the identity guard, not only in preprocessing | 2026-07-03 · `5c3b8ee`, extending 2026-06-17 · `6f9abed` | `6f9abed` placed the collapse in preprocessing only; `5c3b8ee` records that this was insufficient | Recorded: a downstream tier could still surface a long form; the guard compared "SAP AG" and "SAP Aktiengesellschaft" as different entities | `5c3b8ee` message; `utils/text_utils.py:648` |
| D-12 | Website precedence ROR → SERP → LLM, with SERP/LLM skipped once `website_url` is set | 2026-05-14 · `86e265a` | None removed | ⚠ **RATIONALE NOT IN REPO** for the ordering itself; the "first non-empty wins" mechanism is documented but not argued | `Domain_DeptDomain_SearchTerm_Logic.pdf` §1; `README.md:706-712` |
| D-13 | Rank a clean root domain above a subsidiary host for companies | 2026-07-03 · `0e725b6` | The prior selector took the first host merely containing a name token | Recorded with the failing case ("siemens-healthineers.com" for "Siemens AG") | `0e725b6` message; `enrichment/website_resolver.py:377-387` |
| D-14 | Reject a title-only SERP match outright instead of emitting it at low confidence | 2026-07-03 · `b910dfe`, generalised 2026-08-12 · `515cc7c` | The removed line is visible in the diff: `confidence = "high" if _rank(best) == 2 else "low"` — rank 0 was previously emitted | Recorded with the failing case and the intent to defer to Path C | `b910dfe` diff of `enrichment/website_resolver.py:188-196`; current `:388-394` |
| D-15 | Widen Path B retrieval: `num_results` 5 → 10 plus one unquoted retry | 2026-08-12 · `515cc7c` | `num_results=5` was set in `86e265a` and is the value the diagnostic PDF characterises as "shallow" | Recorded in the README §8 entry; the same commit adds the PDF that lists the change as a hypothesis "noted, not applied" — see the entry | `README.md:722,1991`; `enrichment/website_resolver.py:468,522-529`; `Website_Trace_Findings.pdf` §4 |
| D-16 | Flip `DEPT_PROBE_CROSS_DOMAIN` from `true` to `false` | 2026-08-12 · `515cc7c`, reversing 2026-06-05 · `eee57b7` | `eee57b7` introduced the flag "defaulting to true for broader coverage"; `515cc7c` flips it | Recorded: "matches the documented intent"; the PDF flags the contradiction between the default and the in-code comments | `eee57b7` message; `515cc7c:config.py` diff; `config.py:166`; `README.md:1993`; `Domain_DeptDomain_SearchTerm_Logic.pdf` caveat 1 |
| D-17 | Delete `derive_department_domain` as dead code | 2026-08-12 · `515cc7c`, superseding 2026-05-25 · `bb3cae8` | The function and its test existed from `bb3cae8` and were superseded, not used, by `_probe_department_url` added in the same commit | Recorded: superseded by `_probe_department_url`; the PDF names the drift risk | `README.md:1995`; `Domain_DeptDomain_SearchTerm_Logic.pdf` caveat 2; `git log -S 'derive_department_domain'` → `bb3cae8`, `515cc7c` |
| D-18 | Remove `derive_acronym` from the Search Term 1 chain | 2026-08-12 · `515cc7c` | The acronym step was the third link of the chain from `bb3cae8` until this commit | Recorded: it "produced evidence-free initials (`VI`/`SB`/`JFF`)"; the function is kept for the department probe | `README.md:765,1986`; `enrichment/search_terms.py:129` |
| D-19 | Invert Search Term 2 precedence so Name 2 text beats the department-domain host | 2026-08-12 · `515cc7c` | The prior order put the department-domain host first | Recorded with the junk handles it produced (`scrippscollege`, `leuphana`, `uwm`) | `README.md:774,1988`; `enrichment/search_terms.py:507,551` |
| D-20 | Keep issue detection separate from enrichment, pure and deterministic | 2026-06-04 · `25f89d2` | None removed — the module is pure from its first commit | Recorded in the module docstring as a product-owner constraint, with the before/after count delta as the reason | `enrichment/issue_detection.py:1-30` (present verbatim in `25f89d2`) |
| D-21 | Declare two catalogue codes that the deterministic detector never emits | 2026-06-04 · `25f89d2` | None | Recorded: they "genuinely require the pipeline's LLM residual classifier and cannot be decided deterministically from raw input" | `enrichment/issue_detection.py:18-24,88,112` |
| D-22 | Split deduplication into an exact-signature collapse (no LLM) and an LLM adjudication step | 2026-06-17 · `13a1274` | None removed — the split is present in the first dedup commit; no whole-row-to-LLM variant exists in any commit | Recorded: STEP A is "the blow-up guard"; the LLM never sees raw rows | `dedup/signatures.py:1-9`; `README.md:1219-1226` |
| D-23 | Consume the address block from DATAshaper rather than computing the blocking key in the service | 2026-06-17 · `13a1274` | The vendor's own fuzzy clustering rules are the visible alternative, recorded in the transcripts; embeddings are named in the README as out of scope | Recorded in part: the endpoint "takes the address-gated rows"; the efficiency argument for exact-match blocking is the vendor's, in the transcript | `README.md:1127-1129,1131`; `dedup/signatures.py:45-57,95-99`; `CONTEXT-EXTERNAL.md:309-310`; `Datashaper-Tutorial-Part2.txt:1626-1641` |
| D-24 | Decide the Name-2 asymmetry rule in code and never delegate it to the LLM | 2026-06-17 · `13a1274` | None removed | Recorded: it is an institution-level vs department-level distinction, enforced pre-call in Mode A and by candidate filtering in Mode B, with a post-LLM safety net | `README.md:1248-1256`; `dedup/signatures.py:87-93`; `dedup/adjudicator.py:136,392` |
| D-25 | Select Mode A (partition) or Mode B (incremental) by signature count | 2026-06-17 · `13a1274` | None removed | Recorded: Mode B "keeps per-call prompt size bounded for large blocks while still producing N-way clusters" | `README.md:1237-1246`; `dedup/adjudicator.py:36,949` |
| D-26 | Nominate and adjudicate the residue pairs the bucketed pass never compared | 2026-07-23 · `929492b` | Reverses the earlier state in which those pairs "bypass the LLM entirely and default to `unique` with no reasoning" | Recorded in the module docstring and the commit body; the cap routes an over-nominating block to `manual_review` rather than truncating | `dedup/candidates.py:1-16`; `dedup/adjudicator.py:556,586-592,859-861`; `929492b` message |
| D-27 | Replace the sequential integer `cluster_id` with a content hash of the member row ids | 2026-07-22 · `efe1379` | The removed code is visible in the diff: `cluster_id: Optional[int] = cluster_n` plus a `local_to_global` remap loop | Recorded: same membership → same id across runs; the scorer re-derives it to detect a split cluster | `efe1379` diff of `dedup/adjudicator.py`; `dedup/cluster_key.py:1-24` |
| D-28 | Keep clustering and election as separate endpoints | 2026-07-22 · `efe1379` | None removed — election is a new endpoint, never part of `/api/dedup/cluster-block` | Recorded in code twice, in the same words: different inputs, cadences and cost profiles; weights can be retuned without re-paying for adjudication | `api/routes.py:900-903`; `dedup/scoring.py:3-7` |
| D-29 | Elect the golden record with a point-based model over an editable weights table | 2026-07-22 · `efe1379` | The pre-existing DATAshaper implementation is the visible alternative: same criteria, but recency scored on months since `GETDATE()` (0–9 → 25, 10–24 → 15, else 5) rather than absolute year | Recorded in part: the weights file states the scorer "never hardcodes points"; the criteria originate in a spec by Bernd, per the transcript | `dedup/weights.json:1-2`; `Datashaper-Tutorial-Part2.txt:1853-1868,1885-1895`; `README.md:2040` |
| D-30 | Award count points only when the row owns its cluster's most recent year, then restrict the resulting warning to genuine losses | 2026-07-23 · `c18921d`, corrected same day by `994fb3b` | `c18921d` removes the unconditional count scoring and renames the fields; `994fb3b` removes the context-free suppression warning `c18921d` introduced | Recorded: the rule is attributed to Bernd; the correction prevents an older record outscoring a more recent one, and then prevents a false warning | `c18921d`, `994fb3b` messages; `dedup/scoring.py:141-157,792,837-844,497` |
| D-31 | Demote a merge below the confidence threshold to `manual_review` at election time | 2026-07-22 · `efe1379` | None removed | Recorded: gating at election "never re-runs the LLM"; the cluster's confidence is the lowest member's, "conservative on purpose" | `dedup/scoring.py:47-48,1021-1031`; `README.md:2042` |
| D-32 | Make every election a proposal and record human approval through a separate stateless endpoint | 2026-07-22 · `efe1379` | None removed | Recorded: the machine proposes and a human owns the commit; persistence is explicitly out of scope | `dedup/scoring.py:551-556,1046-1047`; `api/routes.py:948-955`; `CONTEXT-EXTERNAL.md:398-399` |
| D-33 | Slim `EnrichmentResult`: drop `*_original` / `*_changed`, exclude internals from the response | 2026-06-03 · `3ce5e94` | The removed fields are visible in the diff (18 field declarations) | ⚠ **RATIONALE NOT IN REPO** beyond "to keep the output lean" in the added comment | `3ce5e94` diff of `api/models.py` |
| D-34 | Re-expose `ror_id` (and add `lei_id`) in the response after excluding it | 2026-06-29 · `6d1805e`, reversing part of `3ce5e94` | `3ce5e94` set `ror_id: … = Field(default=None, exclude=True)`; `6d1805e` removes the exclusion | Recorded: "so the dedup phase can converge on a shared identifier" | `README.md:2015`; `3ce5e94` diff |
| D-35 | Align the `/enrich` JSON response with the file column schema and drop `domain` from it | 2026-07-14 · `701ebd0` | The response previously carried its own key names and the internal `domain` field | Recorded: "the JSON body and the file share one schema — same columns, same names, same order" | `701ebd0` message; `README.md:697-704` |
| D-36 | Merge the "Domain" and "Website URL" output columns into one column carrying the URL | 2026-06-05 · `eee57b7` | The removed mapping is visible in the diff: `"domain": "Domain"` and `"website_url": "Website URL"` | ⚠ **RATIONALE NOT IN REPO** — the commit body lists the change ("for clarity") without a reason | `eee57b7` diff of `api/output_columns.py` |
| D-37 | Remove the `dry_run` request option | 2026-06-05 · `eee57b7` | The option existed from `f77080b` on both the model and the route | Recorded only as "to streamline functionality and focus on core processing" | `eee57b7` message and diff; `git log -S 'dry_run'` → `f77080b`, `847f92e`, `86e265a`, `b19cd1a`, `eee57b7` |
| D-38 | Replace the hardcoded `verify=certifi.where()` with a resolver honouring a corporate CA bundle | 2026-06-19 · `600823c`, extended to ROR/GLEIF 2026-06-29 · `6d1805e` | The removed approach and *its* rationale are both in the diff: the pin existed to defend against a bogus `SSL_CERT_FILE` placeholder | Recorded in a 12-line docstring: a TLS-inspecting VPN fails the handshake against certifi's bundle | `600823c` diff of `llm/openai_client.py`; `README.md:2056-2057` |
| D-39 | Make Azure OpenAI the only LLM backend and delete the direct-OpenAI configuration | 2026-08-12 · `515cc7c` | `OPENAI_API_KEY` and `OPENAI_MODEL=gpt-4o` existed from `f77080b` | Recorded only as "the dead direct-OpenAI config … was removed" | `README.md:2008`; `git log -S 'OPENAI_API_KEY' -- config.py llm/openai_client.py` → `f77080b`, `9938596`, `515cc7c` |
| D-40 | Pin the dedup client to a newer API version and drop `reasoning_effort` on rejection rather than failing | 2026-06-17 · `13a1274` | None removed | Recorded: reasoning models require the newer version; the parameter is "a tuning preference, not a correctness gate" | `dedup/llm.py:109-112,199-207`; `README.md:2052` |
| D-41 | Keep DuckDuckGo as a keyless search fallback | 2026-04-09 · `f77080b` | Never removed; both clients are still wired | Recorded only as a quality warning at the fallback point | `config.py:137-143`; `enrichment/orchestrator.py:778-781`; `search/duckduckgo_client.py:1` |

---

## 3 · Enrichment pipeline shape and tier ordering

### D-1 · Name-2 correction was removed from the pipeline by a single commit

*(Entry produced during Pass 3 and reproduced here unchanged; it is referenced from
`08_GAPS.md` G-2 and `01_TRACEABILITY.md`.)*

#### The question this entry answers

`enrichment/tier2b_dept.py:1-11` states the module is used "when Tier 2A is not applicable (no
contact, or person not found), for companies (which skip 2A entirely), or when name2 is already
filled and needs normalization against the institution's official source." That last case — a
populated Name 2 needing normalisation — is precisely what Tier 2A Mode B adjudicates
(`enrichment/tier2a_contact.py:419-482`). Pass 3 found both procedures unreachable
(`03_ALGORITHMS.md` Part D). This entry establishes which came first, whether one supersedes the
other, and whether either was ever wired in and later removed.

#### Which was written first

**Neither.** Both modules were created in the same commit, `f77080b` (2026-04-09, "Initial
Enrichment Code"), which is the repository's first commit. `git log -- enrichment/tier2a_contact.py`
returns `f77080b`, `635d5ba`, `b47a89c`; `git log -- enrichment/tier2b_dept.py` returns
`f77080b`, `635d5ba`. There is therefore no creation-order evidence that either supersedes the
other, and no commit has touched `enrichment/tier2b_dept.py` since 2026-04-11.

#### Whether either was ever wired in and later removed

**Both were, and both were disabled in the same commit.**

In `f77080b` the Tier 2A gate carried **no** Name-2 condition:

```python
can_do_2a = (
    result["record_type"] == "research_institution"
    and bool(record.contact and record.contact.strip())
    and bool(institution_domain)
)
```
(`f77080b:enrichment/orchestrator.py:476-480`)

and the mode was selected from the record's own Name 2, which was passed through to the tier:
`mode = "population" if not record.name2 else "verification"`
(`f77080b:enrichment/orchestrator.py:500`), with `name2=record.name2` at `:506`. Verification
mode was therefore reachable: any research institution with a contact, a known domain, and a
populated Name 2 entered it.

Tier 2B was wired in the same file as a fallback after the Tier 2A block, guarded only by
`if not is_blank(record.name1):` (`f77080b:enrichment/orchestrator.py:531`) and invoked at
`:540`, with an `_apply_tier2b` result-transfer function at `:163` and the summary counter at
`:638`. The import was `from enrichment.tier2b_dept import Tier2BResult, run_tier2b`
(`f77080b:enrichment/orchestrator.py:32`).

`635d5ba` (2026-04-11) changed both. It replaced `can_do_2a` with `can_do_contact_lookup`,
adding the blocking condition as its first term:

```python
can_do_contact_lookup = (
    not name2_already_filled
    and result["record_type"] == "research_institution"
    and bool(record.contact and record.contact.strip())
    and bool(institution_domain)
)
```
(`635d5ba:enrichment/orchestrator.py:488-493`, with
`name2_already_filled = bool(record.name2 and record.name2.strip())` at `:440`)

and removed the `run_tier2b` import, the invocation, and `_apply_tier2b` — `git show
635d5ba:enrichment/orchestrator.py | grep -c run_tier2b` returns 0. A pickaxe search confirms
the removal is confined to that commit: `git log --all -S "run_tier2b" -- enrichment/orchestrator.py`
returns exactly `f77080b` (added) and `635d5ba` (removed), and
`git log --all -S "can_do_contact_lookup"` returns only `635d5ba`.

The current tree preserves this state. The gate is at `enrichment/orchestrator.py:2451-2457`
with `name2_already_filled` at `:2248`; the mode selector moved into the tier module at
`enrichment/tier2a_contact.py:80`, where it now reads `mode = "2A_population" if is_blank(name2)
else "2A_verification"`. Because the gate requires the value blank and the selector requires it
populated, the qualifying input set is empty by construction.

#### Why — not recorded

⚠ **RATIONALE NOT IN REPO — author to supply.** The commit body of `635d5ba` is an itemised
change list. Two of its bullets touch these modules:

> - Updated `tier2a_contact.py` to include defensive name verification and improved extraction logic.
> - Refined `tier2b_dept.py` to prioritize official sources and structured page content extraction.

Neither mentions removing the Tier 2B call site nor adding `not name2_already_filled` to the
Tier 2A gate. Both bullets describe work *inside* the tier modules; the orchestrator bullet
("Enhanced `orchestrator.py` to clean and canonicalize academic unit names") describes a
different change. A search of all commit messages for `tier2b`, `2B`, or `verification` in this
connection returns only these two bullets and unrelated uses of the word "verification"
(TLS verification, morphological verification, steward verification). No ADR directory exists in
the repository, and no code comment at either site explains the removal.

The history therefore shows **what** changed and **when**, but not **why**. Whether the two
capabilities were judged redundant, whether Tier 2B was found unreliable, whether the gate
condition was a deliberate scope reduction or an unintended consequence of refactoring the
Name-2 handling, is not determinable from the repository. This must not be inferred; it is a
question for the author.

One observation is on the record and is not an inference: the change is self-consistent as a
scope decision. `635d5ba` disabled **both** paths that act on a populated Name 2 and left intact
every path that fills a blank one. Whether that consistency was intended or coincidental is
exactly what the history does not say.

#### What the two would do to the same record

For a record with a populated Name 2 — the case both procedures claim — they are not
interchangeable. The values written may coincide, since both replace Name 2 with the name found
on the page; the surrounding metadata diverges systematically.

| Aspect | Tier 2A Mode B (`_apply_mode_b`) | Tier 2B (`run_tier2b`) |
|---|---|---|
| Evidence | The contact person's page on the institution domain; requires a contact (`enrichment/tier2a_contact.py:163-171`) | Institution website pages found by SERP; needs only Name 1, with the domain used to bias the query (`enrichment/tier2b_dept.py:176-209`) |
| Input Name 2 shown to the LLM | Yes — `name2` and `name3` are interpolated into the prompt (`enrichment/tier2a_contact.py:376-381`) | No — withheld by design, so the model cannot "echo the user's abbreviated input when the page itself uses the canonical form" (`enrichment/tier2b_dept.py:252-258`) |
| Match score | `max(LLM name2_match_score, rapidfuzz token_sort_ratio)` (`:438-449`) | LLM `name2_match` when supplied (`:143-148`), else `rapidfuzz token_sort_ratio` (`:149-154`) |
| Thresholds | `settings.fuzzy_match_threshold`, default `80` (`config.py:203-205`); exact at `≥95` (`:456`) | exact at `≥90`, partial at `≥60` (`:152-153`) |
| `enrichment_status` | `"verified"` at ≥95, else `"enriched"` (`:459, 466, 476`) | always `"enriched"` (`:163`) |
| `flag_for_review` | `False` at ≥95; `True` otherwise (`:460, 467, 477`) | always `True` (`:162`) |
| `source` | `"contact_lookup_found"`, or `"contact_lookup_corrected"` below threshold (`:462, 469, 479`) | `"dept_search"` (`:141`) |
| `confidence` | Not set by `_apply_mode_b`; carried from the extraction | `"medium"` on-domain, `"low"` otherwise (`:156-161`) |
| Name 3 | Writes `name3_enriched` (`:482`) | No Name-3 field exists on `Tier2BResult` (`:31-43`) |

They disagree on identical evidence in two score bands. At a score of 60–79 Tier 2A is below its
threshold and takes the correction branch — Name 2 replaced, labelled `"no_match"`, source
`"contact_lookup_corrected"` (`enrichment/tier2a_contact.py:470-479`) — while Tier 2B is above
its partial cutoff and reports `"partial"` with an ordinary `"enriched"` status
(`enrichment/tier2b_dept.py:152-153`). At 90–94 the order reverses: Tier 2A is below its exact
cutoff and reports `"partial"` with the review flag set, while Tier 2B reports `"exact"`. Tier 2A
is additionally the only path that can clear the review flag; Tier 2B sets
`flag_for_review = True` unconditionally.

#### Consequence for the current system

Enrichment fills blank Name 2 values only. No code path detects or corrects an incorrect
existing Name 2 against either the contact's page or the institution's website. Three outputs
defined in the source can never be produced: `enrichment_status="verified"`,
`source="contact_lookup_corrected"`, and the below-threshold correction branch. Two summary
counters cannot increment (`enrichment/orchestrator.py:2636-2641`). These are carried in
`08_GAPS.md`, and the affected requirement rows are downgraded in `01_TRACEABILITY.md`.

---

### D-2 · The tiered escalation design and its ordering

**Date and commit.** 2026-04-09 · `f77080b`. The four tiers, the preprocessing stage, and the
escalation order are all present in the first commit; the repository contains no earlier state.

**Alternatives visible in history.** None for the design itself. No commit contains a flat
single-pass design, an LLM-first design, or a differently-ordered escalation. The only ordering
change in 51 commits is the *insertion* of a step (D-3), never a reordering or removal of one.
This is a limit of the record, not evidence that no alternative was considered: the pipeline
arrived fully formed in the initial commit, so any deliberation preceding it left no trace here.

**Why the chosen option.** Recorded, in the README's Solution Strategy table and the design
principles beneath it (`README.md:78-98`):

> The API uses a **tiered escalation approach**: start with the cheapest, most reliable method
> and escalate only when cheaper methods fail. Each tier has progressively higher cost and lower
> confidence

and principle 2, "Deterministic before probabilistic. Regex-based preprocessing runs before any
API or LLM call" (`README.md:95`). The table assigns each tier an explicit cost and confidence
band — preprocessing "Zero (no API calls) / Deterministic", Tier 1 "Low (free public API) /
High", Tier 2A "Medium / Medium-High", Tier 3 "Medium / Low (always flagged)" — so the ordering
is an argument about the joint monotonicity of cost and confidence, and escalation stops as soon
as a tier succeeds.

**What the ordering is in code.** The escalation is a sequence of guarded blocks inside
`enrichment/orchestrator.py`, not a table-driven dispatcher: Tier 2 canonical at `:2384`, Tier 2A
at `:2468`, Tier 3 at `:2543`, with the Tier 1 registry lookups reached earlier via
`_run_lei_lookup` at `:2058`, `:2152` and `:2198`. Tier 2B has no call site (D-1, `08_GAPS.md`
G-2), so the ordering realised at runtime is preprocessing → Tier 1 → {Tier 2 canonical, Tier 2A}
→ Tier 3, one tier shorter than the README's table describes.

**⚠ Discrepancy.** The README table and the architecture diagram (`README.md:119-172`) both list
Tier 2B as an active tier. This is carried in `08_GAPS.md` G-2/G-3 as a code↔doc discrepancy; it
is repeated here only because it means the documented rationale for the *ordering* describes a
five-tier chain that the code does not have.

### D-3 · GLEIF/LEI inserted as a Tier 1 company step before the LLM fallback

**Date and commit.** 2026-06-29 · `6d1805e`, "Tier 1 LEI (GLEIF) lookup + ROR TLS/scoring/acronym
fixes". Documented in `3d3c95a` the same day.

**Alternatives visible in history.** Before `6d1805e` there was no company registry step at all.
`enrichment/tier1_lei.py` does not exist in any earlier commit
(`git log --all -- enrichment/tier1_lei.py` returns `6d1805e`, which adds it, and `611c348`),
and a company that missed on ROR went directly to the LLM company-canonical prompt. The alternative visible in the
history is therefore the prior arrangement: probabilistic canonicalisation with no deterministic
registry step for companies, and no company identifier in the output.

**Why the chosen option.** Recorded in the module docstring:

> For company-type records this resolves the official legal name and a Legal Entity Identifier
> from the free GLEIF API (no auth, JSON:API format) and uses it as the canonical `name1`
> BEFORE the LLM company canonicalization fallback.
> (`enrichment/tier1_lei.py:4-7`)

The placement follows D-2's cost ordering — a free registry before a paid inference — and the
README adds the downstream reason for emitting the identifier: "so the dedup phase can converge
on a shared identifier" (`README.md:2015`). The commit also records the failure mode the
docstring makes mandatory: GLEIF's `legalName` filter is fulltext rather than exact, so
"Pfizer" returns "PFIZER AG" and "PFIZER INC." and the verification guard is required even on
the precise path (`enrichment/tier1_lei.py:13-16`).

**Scope constraint, recorded.** LEI runs on the company branch only and "never runs on or
overwrites a ROR-matched research institution", and "A GLEIF failure never fails the record"
(`README.md:2014`; `enrichment/tier1_lei.py:36-38`).

### D-4 · Structured-element extraction over free-form generation

**Date and commit.** 2026-04-09 · `f77080b`; tightened 2026-04-11 · `635d5ba`.

**Alternatives visible in history.** The tightening is the visible alternative. `635d5ba` reduced
the amount of free-form body text available to the prompts by more than half:

```
-        default_factory=lambda: int(os.getenv("MAX_PAGE_CONTENT_CHARS", "3000"))
+        default_factory=lambda: int(os.getenv("MAX_PAGE_CONTENT_CHARS", "1500"))
```

The commit body records the change as "Adjusted `max_page_content_chars` in `config.py` from 3000
to 1500 for better performance" — a performance reason, not an accuracy one. The same commit
"Enhanced `page_fetcher.py` to return structured page content", which is the change that makes
the smaller body budget workable.

**Why the chosen option.** Recorded as design principle 3: "Structured extraction over free-form
generation. LLM prompts extract from structured page elements (URL path, title, H1, breadcrumb)
rather than interpreting free-form body text" (`README.md:96`). A second, narrower reason is
recorded at the Tier 2B prompt, which withholds the record's own Name 2 from the model so it
cannot "echo the user's abbreviated input when the page itself uses the canonical form"
(`enrichment/tier2b_dept.py:252-258`).

### D-5 · Flag rather than infer when confidence is low

**Date and commit.** 2026-04-09 · `f77080b`, extended without reversal through `515cc7c`.

**Alternatives visible in history.** None removed. Every commit that touches this rule tightens
it; no commit relaxes it. The extensions are visible as additions: `enrichment/orchestrator.py:2571`
("if preprocessing cleared Name 2 as an AP reference, do not let Tier 3 fabricate a
replacement"), `:1888` and `enrichment/person_affiliation.py:15` (a person record "always
short-circuits … so Tier 3 cannot fabricate/overwrite", added in `515cc7c`), and `:390`
("return null rather than emit a fabricated department").

**Why the chosen option.** Recorded as design principle 1: "**Never fabricate data.** If
confidence is low, return the original values and flag for human review" (`README.md:94`), and
principle 5, transparency: every result carries `tier_used`, `source`, `confidence`,
`flag_for_review` and `flag_reason` "so humans can audit the pipeline's decisions"
(`README.md:98`).

**How it is realised.** The rule is not a single check but a policy distributed across four
layers, each of which can be cited separately:

| Layer | Mechanism | Evidence |
|---|---|---|
| Prompt | "No fabrication of institutions or invented people"; "No fabrication. Prefer institution=null over a plausible guess." | `llm/prompts.py:361,401` |
| Tier gate | Tier 2 canonical accepts only high-confidence answers | `README.md:88` |
| Status derivation | Tier 3 always yields `unresolved`, whatever its confidence | `enrichment/confidence.py:33` |
| Flagging | Tier 3 always flags; medium confidence from any tier flags; a low-confidence website write flags | `enrichment/confidence.py:51-55`; `README.md:721,795-812` |

The one place where the policy is expressed as a *refusal to write* rather than a flag is website
Path B rank 0 (D-14), which returns nothing so a later path can try.

---

## 4 · Registry acceptance and name identity

### D-6 · `token_sort_ratio` over `token_set_ratio` for LEI verification

**Date and commit.** 2026-06-29 · `6d1805e`.

**Alternative visible in history.** `token_set_ratio` never appears in committed code — the
pickaxe `git log --all -S 'token_set_ratio'` returns only `6d1805e` and its README follow-up
`3d3c95a`, both of which mention it in prose. The alternative is therefore visible in the record
as a *named rejection*, not as removed code.

**Why the chosen option.** Recorded in the README changelog:

> RapidFuzz `token_sort_ratio` (case-folded, legal-form-suffix-aware) with
> `LEI_NAME_MATCH_THRESHOLD` (default 88). Rejects statistically-close wrong entities; never
> accepts an unverified hit. `token_set_ratio` was rejected as unsafe (it scores any contained
> substring 100).
> (`README.md:2013`)

The module docstring states the same requirement without naming the alternative: "GLEIF fuzzy is
statistical; without this guard it fabricates matches" (`enrichment/tier1_lei.py:26-27`).

### D-7 · A wrong-country registry candidate becomes a clean miss

**Date and commit.** 2026-06-29 · `6d1805e`, applied to both ROR and GLEIF.

**Alternatives visible in history.** Before this commit neither registry filtered on country;
selection was by name score alone. The commit adds the filter on both registries and on all four
paths (ROR affiliation, ROR query-with-retry, GLEIF precise, GLEIF fuzzy). The GLEIF fuzzy path
records why an API-side filter was not sufficient: "`fuzzycompletions` can't be country-filtered
at the API, so the post-filter is mandatory" (`README.md:2063`).

**Why the chosen option.** Recorded, including the alternative that was rejected — accepting the
match and letting a later stage sort it out:

> A wrong-country id is worse than none — it would wrongly converge distinct legal entities in
> Phase 2 dedup — so a rejection becomes a clean miss that falls through to the LLM path.
> (`README.md:2064`)

with the concrete failures named: a US "BASF" (`ror.org/002yzpx87`) for a German BASF record, and
a Norwegian "Siemens AS" LEI for a German Siemens record (`README.md:2061`).

### D-8 · Location tokens excluded from ROR's subset-match shortcut

**Date and commit.** 2026-07-03 · `1496fd4`.

**Alternative visible in history.** The prior scorer, in which a city token counted toward the
subset match. The commit body states the consequence:

> A query whose only significant name token is its city (e.g. "Uni Stuttgart", where the 3-char
> "Uni" is dropped) subset-matched every same-city org and returned a false 1.0, so "Uni
> Stuttgart" resolved to a random Stuttgart hospital instead of the university.

**Why the chosen option.** Recorded in the same body: the record's city/state/country are
threaded into the scorer as location tokens and excluded from both the subset-1.0 shortcut and
the fuzz distinctive-token guard, "so an address token can never on its own justify a name
match." The rule survives as a comment at `enrichment/tier1_ror.py:45` and a regression test at
`tests/test_tier1.py:232`.

### D-9 · ROR's official name adopted for abbreviated institutions

**Date and commit.** 2026-07-03 · `d9ea45a`.

**Alternative visible in history.** The rule this replaces is stated in the commit body: "On a
ROR match the code kept the user's input name when ROR's official form appeared to drop a
distinctive token". The mechanism that caused it is also recorded — the identity guard's
per-token 4-character floor treated "Uni" as not covering "University".

**Why the chosen option.** Two reasons are recorded, one local and one downstream: the
abbreviated institution adopts ROR's fuller official name, "which also makes such rows converge
in dedup."

**Later qualification.** `515cc7c` adds the complementary case: when the guard *does* keep the
input name over a divergent ROR form, the kept name is still standardised
(`clean_passthrough_org_name`, so "Stuttgart Univ of Applied Sciences" → "University"), on the
recorded ground that it should be cleaned "exactly as a ROR-miss passthrough is cleaned"
(`README.md:778,1996`).

### D-10 · GLEIF re-verify to recover a misspelled company name

**Date and commit.** 2026-07-03 · `7ea0376`. This is the most fully argued commit body in the
repository and is quoted at length because the argument is the decision.

**Alternatives visible in history.** Two, both stated as having been tried and blocked:

> GLEIF's name search is not typo-tolerant, so a misspelled company ("Bayr AG") misses on ROR and
> the raw-name LEI lookup, and the company-canonical LLM's correction ("Bayer AG") is blocked by
> the identity guard (a typo is not a prefix/acronym of the original).

So the first alternative — accept the LLM's correction directly — was rejected by an existing
guard, and the second — feed the address to the LLM and use its answer — is recorded as
insufficient on its own: "the address signal only yields a result through the GLEIF re-verify."

**Why the chosen option.**

> When the LLM's proposal is a genuine spelling variant (not an entity swap), re-query GLEIF on
> it: a confirmed ACTIVE entity in the right country proves the correction real and attaches the
> LEI. The spelling-variant gate keeps this from laundering a hallucination.

and, on why the two changes are one commit rather than two:

> These two paths are combined because the address signal only yields a result through the GLEIF
> re-verify.

The commit also records the guard on the address signal: "the same gate ensures the address can
only correct a close name, never replace an unrelated one that merely shares a building." This is
D-5 applied to a specific evidence source.

### D-11 · Legal-suffix collapse moved from preprocessing-only to output and identity guard

**Dates and commits.** 2026-06-17 · `6f9abed` (preprocessing and scoring); 2026-07-03 ·
`5c3b8ee` (output and identity guard).

**Alternative visible in history.** The `6f9abed` arrangement — collapse at preprocessing only —
is the alternative, and `5c3b8ee` records why it was insufficient rather than replacing it:

> Preprocess UC 17 collapses long-form legal suffixes on the input, but a downstream tier
> (ROR/GLEIF/LLM) or a comparison against name1_original could still surface or mis-handle a long
> form.

**Why the chosen option.** Two hardening changes are recorded with their consequences:
`finalise()` collapses long forms on `name1..name4_enriched` "so the short form is guaranteed on
output regardless of source", and `_identity_tokens` collapses them before tokenising "so 'SAP
Aktiengesellschaft' and 'SAP AG' compare as the same entity instead of differing on a spurious
'aktiengesellschaft' token (which previously made the guard reject the pair)". The reasoning
survives as a comment at `utils/text_utils.py:648`.

---

## 5 · Website, domain, department-domain, and search terms

This subsystem carries more recorded reversals than any other part of the repository. Three of
its parameters were set, characterised by a diagnostic run, and then changed.

### D-12 · Website precedence: ROR, then SERP, then LLM

**Date and commit.** 2026-05-14 · `86e265a`, "Add website URL handling and inference logic to
enrichment process".

**Alternatives visible in history.** None. Paths A, B and C arrive together, and the precedence
is implemented as a write-once rule rather than a comparison: `_maybe_resolve_website_bc` returns
early when `website_url` is already set, so ROR always wins
(`Domain_DeptDomain_SearchTerm_Logic.pdf` §1; `README.md:708`).

**Why the chosen option.** ⚠ **RATIONALE NOT IN REPO — author to supply.** The mechanism is
documented in detail in both the README and the PDF, and the ordering is consistent with D-2's
cost/confidence argument, but no commit message, comment, or document states *why* a registry
link outranks a search result outranks an inference. The consistency with D-2 is an observation
about the two documents, not a recorded justification, and is not treated as one here.

### D-13 · Clean root domain preferred over a subsidiary host

**Date and commit.** 2026-07-03 · `0e725b6`.

**Alternative visible in history.** The prior selector: "For companies the SERP selector took the
first host merely containing a name token, so 'siemens-healthineers.com' (a subsidiary) was
accepted as the site for 'Siemens AG'."

**Why the chosen option.** Recorded, including the regression it guards against:

> Rank candidates so a host whose label introduces a foreign brand word absent from name1 loses
> to the clean root ("siemens.com") even when the subsidiary ranks higher; single concatenated
> labels ("thermofisher") stay clean to avoid regressions.

The ranking function this introduces is at `enrichment/website_resolver.py:377-387`.

### D-14 · Title-only SERP matches rejected rather than emitted at low confidence

**Date and commit.** 2026-07-03 · `b910dfe`; generalised to both branches 2026-08-12 · `515cc7c`.

**Alternative visible in history — the removed line.** The diff shows the previous behaviour
exactly:

```diff
-    confidence = "high" if _rank(best) == 2 else "low"
+    best_rank = _rank(best)
+    if best_rank == 0:
+        return WebsiteResolution()
+    confidence = "high" if best_rank == 2 else "low"
```
(`b910dfe` diff of `enrichment/website_resolver.py:188-196`)

A rank-0 candidate — one whose only overlap with the name is a word in the title or snippet —
was previously emitted as the website with `low` confidence and a review flag. It is now not
emitted at all.

**Why the chosen option.** Recorded in the commit body with the failing case, and in the
retained code comment:

> A SERP result whose HOST shares no token with the company name — its only overlap being a word
> in the title/snippet — was still emitted as the website at low confidence, so "Sign A Rama USA"
> picked up a neighbour's "universitysurgical.com". Require a name token in the host; when none
> qualifies, return nothing so Path C (LLM) can try instead of writing a stranger's domain.

This is the one place in Phase 1 where D-5's policy is realised as silence rather than a flag: a
flagged wrong domain was judged worse than an empty field, because the empty field lets a later
path run.

**Generalisation.** `515cc7c` extends the same 0/1/2 ranking to the research-institution branch,
adds a distinctive-token requirement so generic industry words do not count as a host match, adds
an acronym-in-host exception so `fit.edu` still resolves for "Florida Institute of Technology",
and makes an authoritative TLD grant `high` only with a clean host match — "a bare `.org` never
grants high confidence on its own" (`README.md:715-721,1990`;
`enrichment/website_resolver.py:377-398`).

### D-15 · Path B retrieval widened after the `WEBSITE_TRACE` diagnostic

**Date and commit.** 2026-08-12 · `515cc7c`.

**Alternative visible in history.** `num_results=5`, set in `86e265a` and unchanged until this
commit (`git log --all -S 'num_results' -- enrichment/website_resolver.py` returns `86e265a` and
`515cc7c`), together with exact-phrase-only querying.

**Why the chosen option.** Recorded twice, and the two records do not agree on whether the change
had been made at the time each was written. Both artefacts were added by the same commit.

`Website_Trace_Findings.pdf` §4 reports the diagnostic run and lists the change as a hypothesis
that was deliberately *not* applied:

> Hypotheses for the retrieval miss (noted, not applied — per the hard constraint) … Exact-phrase
> quoting "Atlantic Testing Labs" can exclude a site that brands itself slightly differently
> ("…Laboratories"). … `num_results=5` is shallow — a real homepage at position 6+ is never seen.
> … None of these are guard bugs; the guards did their job on the candidates they were given.

The README changelog in the same commit reports both hypotheses as implemented:

> **Website Path B retrieval (§8)** — `num_results` 5 → 10, plus one **unquoted retry** when the
> exact-phrase query finds nothing (recovers `Atlantic Testing Labs` → `atlantictesting.com`,
> `Fine Organics Limited` → `fineorganics.com`). Logged in `WEBSITE_TRACE`. Tests `TestPathBRetry`.
> (`README.md:1991`)

The code agrees with the README: `num_results = 10` at `enrichment/website_resolver.py:468`, and
the unquoted retry at `:522-529` with the comment "one unquoted retry when the exact-phrase query
found no valid [candidate] … Only runs on a first-pass miss; one retry maximum."

**What the record does and does not establish.** It establishes that the diagnostic was run
under a constraint forbidding behaviour changes, that the diagnostic identified retrieval — not
the guards — as the cause of two of three failures, and that the two named remedies were
implemented. It does **not** record who lifted the constraint, or on what basis the two
hypotheses were promoted to changes while the other two (city/state geo, Path C's own miss) were
not. ⚠ That decision point is **NOT RECORDED — author to supply.**

**A caveat the PDF itself states**, relevant to any evaluation built on it: "The SERP result sets
have drifted since these records were characterized, so the 'currently succeeds / fails' table no
longer reproduces exactly." Three of the six records behaved differently in the live run from the
earlier snapshot the record set was chosen against.

### D-16 · `DEPT_PROBE_CROSS_DOMAIN` default flipped from `true` to `false`

**Dates and commits.** Introduced 2026-06-05 · `eee57b7`; flipped 2026-08-12 · `515cc7c`. The
pickaxe `git log --all -S 'DEPT_PROBE_CROSS_DOMAIN'` returns exactly these two commits.

**Alternative visible in history — the deleted default.** `eee57b7`'s commit body:

> Added `DEPT_PROBE_CROSS_DOMAIN` configuration option to control cross-domain SERP probing
> behavior, defaulting to true for broader coverage.

and `515cc7c`'s diff:

```diff
-    "DEPT_PROBE_CROSS_DOMAIN": "true",
+    "DEPT_PROBE_CROSS_DOMAIN": "false",
-        default_factory=lambda: _bool(os.getenv("DEPT_PROBE_CROSS_DOMAIN"), default=True)
+        default_factory=lambda: _bool(os.getenv("DEPT_PROBE_CROSS_DOMAIN"), default=False)
```
(current state: `config.py:166`)

**Why the chosen option.** The flip is recorded as reconciling the default with the code's own
comments rather than as a fresh judgement: "**`DEPT_PROBE_CROSS_DOMAIN` default → `false`** (§6)
— matches the documented intent; the unrestricted cross-domain stage-3 SERP is now opt-in"
(`README.md:1993`).

The contradiction it resolves is itself documented, in the PDF written from a source trace:

> `DEPT_PROBE_CROSS_DOMAIN` **defaults to** `True` despite in-code comments calling stage 3
> "opt-in / off by default." So an unrestricted second SERP call normally runs when the
> on-domain stages fail — a cost + wrong-domain-risk lever worth confirming as intended.
> (`Domain_DeptDomain_SearchTerm_Logic.pdf`, "Accuracy caveats surfaced by the trace", item 1)

So the record shows a flag introduced with one intent stated in its commit message ("broader
coverage"), documented in code comments with the opposite intent, the contradiction found by a
trace, and the default changed to match the comments. What the record does not settle is which of
the two intents was correct at the time `eee57b7` was written. The README's guidance for the
remaining use — "enable only for split-domain academic medical centres" (`README.md:747`) — is
recorded, and the two costs named in the PDF (a second unrestricted SERP call; wrong-domain risk)
are the recorded downside.

### D-17 · `derive_department_domain` deleted as dead code

**Dates and commits.** Added 2026-05-25 · `bb3cae8`; deleted 2026-08-12 · `515cc7c`. The pickaxe
`git log --all -S 'derive_department_domain'` returns exactly these two commits, as does
`-S '_probe_department_url'` — both the superseded function and its replacement were introduced
in `bb3cae8`.

**Alternative visible in history.** The function itself, and the two-implementation state that
persisted for eleven weeks. The record shows the replacement was written in the same commit as
the function it replaced, and that the older one was left in place, with a test, until `515cc7c`.

**Why the chosen option.** Recorded as supersession, and — before the deletion — as a drift risk:

> `derive_department_domain` in `search_terms.py` **is dead code** — the live probe is
> `_probe_department_url`. If you don't want two implementations drifting, that function (and its
> test) could be removed.
> (`Domain_DeptDomain_SearchTerm_Logic.pdf`, caveat 2)

> **Dead code removed** — `search_terms.derive_department_domain` (superseded by
> `_probe_department_url`) and its test.
> (`README.md:1995`)

⚠ What is **not recorded** is why the two co-existed from `bb3cae8`, or whether
`derive_department_domain` was ever the live path.

### D-18 · `derive_acronym` removed from the Search Term 1 chain

**Date and commit.** 2026-08-12 · `515cc7c`.

**Alternative visible in history.** The removed chain link. From `bb3cae8` until this commit,
Search Term 1 fell back to initials derived from Name 1 when neither a ROR acronym nor a domain
was available.

**Why the chosen option.** Recorded with the outputs that motivated it:

> `derive_acronym` was **removed** from this chain (§1) — it produced initials with no
> corroborating evidence (`VI`, `SB`, `JFF`). It remains for internal use by the department probe.
> (`README.md:765`; also `:1987`)

This is D-5 applied to a derived field: an evidence-free handle is treated as fabrication, and
the field is left to a fallback with a source (the original SAP Search Term 1, else the first two
significant words of Name 1) rather than being synthesised. The function survives for the
department probe, where its output is checked against a fetched page before use
(`enrichment/search_terms.py:129`).

### D-19 · Search Term 2 precedence inverted

**Date and commit.** 2026-08-12 · `515cc7c`.

**Alternative visible in history.** The prior order, in which the department-domain host outranked
the Name 2 text. The README states the inversion explicitly and names its outputs:

> This **inverts** the old precedence — Name 2 text now beats the department-domain host, which
> had produced junk handles (`scrippscollege`, `leuphana`, `uwm`).
> (`README.md:774`)

**Why the chosen option.** The three quoted handles are the recorded reason: a host-derived
handle names the institution or the campus, not the department, which is the field Search Term 2
is defined to mirror. The new chain — `ADMIN` → subdomain acronym → Name 2 phrase filled to 32
characters → department-domain host → `None` — keeps the host as a last resort rather than
removing it (`enrichment/search_terms.py:507,551`).

**Associated constants.** Both terms pass a terminal normalisation to ≤32 characters on a word
boundary, uppercased, recorded as the SAP SORT1/SORT2 field width
(`README.md:757`; `enrichment/search_terms.py:392,403-410`).

---

## 6 · Issue detection

### D-20 · `/issues` kept separate from `/enrich`, pure and deterministic

**Date and commit.** 2026-06-04 · `25f89d2`, "Add SAP master-data fields and issue detection
enhancements". The docstring quoted below is present verbatim in that commit
(`git show 25f89d2:enrichment/issue_detection.py | head -20`).

**Alternatives visible in history.** None removed. There is no commit in which issue detection
runs inside the enrichment pipeline, calls the LLM, or performs network I/O. The pickaxe
`git log --all -S 'def detect_issues'` returns `25f89d2` (added) and `efe1379` (a same-named
function added for dedup scoring, a different module).

**Why the chosen option.** This is one of the few decisions attributed in code to a named
authority, and the reason given is measurement:

> Design constraints (per product owner):
>
> * **Pure and deterministic** — regex / string checks only. No enrichment, no LLM call, no
>   network/external I/O. The same rule set can therefore be run on a raw input file *and* on a
>   post-pipeline output file, and the count delta is the story the catalogue is built around.
> * **Reuse, don't reinvent** — wherever the enrichment pipeline already ships a deterministic
>   detector (PO-box / sub-location / opaque-code / DBA / ISO-country …) we import and reuse its
>   compiled patterns so detection stays consistent with what the pipeline actually does.
> (`enrichment/issue_detection.py:7-17`)

The first constraint is what makes the before/after comparison valid: if detection were
LLM-backed or enrichment-dependent, the delta between a raw file and a processed file would
confound the detector's non-determinism with the pipeline's effect. The second constraint records
the cost accepted in exchange — the detector is coupled to the pipeline's pattern modules rather
than independent of them.

`02_ARCHITECTURE.md` §9 records the same boundary from the deployment side: different cadence,
zero external spend, no network failure domain.

### D-21 · Two catalogue codes declared but never emitted

**Date and commit.** 2026-06-04 · `25f89d2`.

**Alternative visible in history.** None; both codes are marked from the outset.

**Why the chosen option.** Recorded as a consequence of D-20 rather than an oversight:

> Coverage: 34 of the 36 catalogue codes are emitted. Two are intentionally never emitted because
> they genuinely require the pipeline's LLM residual classifier and cannot be decided
> deterministically from raw input — they are listed in `ISSUE_CATALOGUE` for completeness but
> documented as `# LLM-only` below:
> `G1-ADDR-009` Unclassified Residual in Address; `G4-ADDR-025` Sub-location Overflow Beyond Street 5
> (`enrichment/issue_detection.py:18-24`; markers at `:88,112`)

The docstring also records the precision/recall stance taken for the semantic rules that *are*
emitted: "They err toward precision (few false positives) over recall"
(`enrichment/issue_detection.py:27-28`) — a recorded trade-off, and the counterpart to D-5 in the
detection path.

---

## 7 · Phase 2 — clustering

### D-22 · Deduplication split into exact-signature collapse and LLM adjudication

**Date and commit.** 2026-06-17 · `13a1274`, which adds the entire `dedup/` package in one
commit (11 files, +1860 lines).

**Alternatives visible in history.** None inside this repository — the split is present in the
first dedup commit, and no commit contains a variant that sends raw rows to the model. The
alternative that *is* on the record is external and is treated separately in D-23: DATAshaper's
own clustering, which compares every row against every other row within a block using fuzzy
string rules and no model at all.

**Why the chosen option.** Recorded in the module docstring as a scaling guard:

> A *signature* is a distinct `(norm_name1, norm_name2)` key within a block. 100 byte-identical
> rows collapse to one signature; the LLM only ever works on distinct signatures, never on raw
> rows. This is the blow-up guard.
>
> The normalized key is internal only — it never reaches the LLM, which always sees the original
> (un-normalized) name1/name2.
> (`dedup/signatures.py:1-9`)

Two design constraints follow from this and are recorded separately.

**What STEP A deliberately does *not* do.** The normalisation is bounded, and the boundary is
argued in a comment:

> Strip anything that is not a letter, digit, or whitespace. We deliberately do NOT strip legal
> forms (GmbH, AG, Inc.) or expand abbreviations here — that is the LLM's job. The key is a
> conservative collapse only.
> (`dedup/signatures.py:22-25`)

So the split is not "cheap step first, expensive step after" alone; it is a division of
responsibility in which the deterministic step is restricted to changes that cannot be wrong
(case, whitespace, punctuation, accent folding) and everything requiring judgement is deferred.
The normalised key is withheld from the model so the model's judgement is not anchored on it.

**What the split buys downstream.** A cluster formed purely by identical-signature collapse
carries no merge confidence, because no merge decision was made:

> Merge confidence: set only for a genuine LLM merge (>=2 distinct signatures) or an uncertain
> row; null for a unique row AND for a pure identical-signature collapse (deterministic, no merge
> decision).
> (`dedup/models.py:75-79`)

and `llm_flag` distinguishes the two kinds of cluster in the output (`README.md:1293-1295`). The
split is therefore also an auditability decision: a reader of the output can tell which clusters
a model produced.

### D-23 · Address-gate blocking consumed from DATAshaper rather than computed

**Date and commit.** 2026-06-17 · `13a1274`.

**Alternatives visible in history.** Two, both on the record, neither in this repository's code.

*The vendor's own clustering.* The DATAshaper product already implements deduplication over the
same data, using fuzzy rules rather than a model. The transcript records the exact configuration:

> here we have a rule to find the duplicate based on the name and the address. Then we're saying
> here from our validation table, our name concat needs to be fuzzy matching 85% … And then our
> streets needs to have a fuzzy match of 80%. And then our US [postal] code prefix needs to be an
> exact match … Our country, it needs to match exactly 100%, and our region needs to match 100% …
> And then we have this matching score, but this means the average of all the fuzzy matching needs
> to be at least 75.
> (`Datashaper-Tutorial-Part2.txt:1589-1600`)

The same passage records the vendor's own reason for gating on exact-match fields first, and the
recall cost it accepts:

> The fuzzy matching, it's quite heavy to process because it needs to compare the strings. Exact
> matching is much faster … in the background, this will always start with doing the exact
> matches. And then what is still left, it will apply the fuzzy matching … That's why here we have
> the US postal code and the region. So then, of course, we are a bit limited because if it's a
> different postal code, it will never find it as duplicates. But we only need to compare the
> names and the fuzzy match within the same postal code and region.
> (`Datashaper-Tutorial-Part2.txt:1626-1641`)

and the limitation of the rule-based verdict that the LLM pass exists to address:

> It's not because all the rules say it's a duplicate that it's always a duplicate.
> (`Datashaper-Tutorial-Part2.txt:1901`)

> that's always the tricky part in the clustering. If we make the rules more strict, we will find
> more duplicates, but we will also have more false duplicates.
> (`Datashaper-Tutorial-Part3.txt:152`)

*Embeddings.* Named in the README as considered and excluded:

> It does **not** do address validation, embeddings, golden-record election, or file I/O — those
> are out of scope and handled elsewhere in the pipeline.
> (`README.md:1129`)

⚠ Whether embeddings were evaluated or excluded on principle is **not recorded**; the sentence
names the exclusion without an argument.

**Why the chosen option.** Recorded in part. The README states the division of labour and the
question the service is left to answer:

> after enrichment runs and **DATAshaper applies its address gates** (grouping records that share
> the same country + postal code + street), there is still a question left over: *within a block
> of records at the same address, which ones are genuine duplicates of the same organizational
> entity, and which are distinct units that merely share a building?*
> (`README.md:1127-1128`)

So the recorded rationale is a scoping one: the address gate already exists upstream and is cheap
and exact; what it cannot decide is organisational identity within a shared address, and that is
what the model is asked. The service reads `[Block ID]` rather than deriving it
(`CONTEXT-EXTERNAL.md:309-310`).

**The fallback, and what it implies.** The service does not require the block id. When a row
arrives without one, it derives a compact hash of the normalised
`(country, postal_code, street, house_no)` tuple:

```python
def derive_block_id(row: DedupRow) -> str:
    """Derive a stable block id from the normalized address tuple.
    Used when a row arrives without a ``block_id``. ..."""
```
(`dedup/signatures.py:45-57`, resolved at `:95-99`)

The derived key is an exact match on all four fields, which is stricter than the vendor's gate
(fuzzy street at 80%, postal *prefix* only). ⚠ Whether the fallback is intended for testing, for
callers other than DATAshaper, or as a hedge is **not recorded**; the docstring states the trigger
condition, not the purpose.

**⚠ Recall consequence, not recorded as considered.** Both blocking schemes are exact on country
and postal code. Two records for the same entity with different postal codes are never compared,
by either the vendor's gate or the fallback — the vendor states this plainly
(`Datashaper-Tutorial-Part2.txt:1638-1640`). No artefact in this repository records that ceiling
being weighed when the address-gated input contract was adopted.

### D-24 · The Name-2 asymmetry rule is decided in code, never by the LLM

**Date and commit.** 2026-06-17 · `13a1274`.

**Alternatives visible in history.** None removed. The rule is deterministic from the first dedup
commit, and both modes are built around it rather than checked against it afterwards.

**Why the chosen option.** Recorded as a categorical distinction rather than a similarity
judgement:

> *A signature with an empty Name 2 can **never** share an entity with a signature that has a
> populated Name 2.* This is an institution-level vs department-level distinction and is **never
> delegated to the LLM**
> (`README.md:1250-1252`)

**How it is enforced — three mechanisms, recorded separately.** Mode A partitions signatures into
an empty-Name-2 bucket and a populated-Name-2 bucket *before* any call, "so the two are never
compared by the model"; Mode B "only ever presents canonicals whose `has_name2` matches the
candidate", and an incompatible candidate "starts a new entity with no LLM call"
(`README.md:1254-1256`). A post-LLM safety net splits any entity that mixes the two anyway,
recorded as insurance against a future prompt change: `_enforce_name2_split`
(`dedup/adjudicator.py:136,392`). The property that drives all three carries its own docstring
(`dedup/signatures.py:87-93`).

**The cost of this decision, and its later partial reversal.** Bucketing means the pairs the rule
separates are never adjudicated at all — which is exactly the residue D-26 addresses.

### D-25 · Mode A / Mode B selected by signature count

**Date and commit.** 2026-06-17 · `13a1274`. Threshold `SIG_PARTITION_THRESHOLD`, default 12
(`dedup/adjudicator.py:36`, read at `:949`).

**Alternatives visible in history.** None removed; both modes arrive together.

**Why the chosen option.** Recorded as a prompt-size bound: Mode A issues "One partition call per
populated-Name 2 bucket", Mode B "One call per signature, comparing it against the current
canonical entities", and "Mode B keeps per-call prompt size bounded for large blocks while still
producing N-way clusters" (`README.md:1239-1246`). The cost columns of the same table record the
trade being made: Mode A is one or two calls per block; Mode B is O(signatures) calls with each
prompt bounded.

⚠ **The value 12 is not justified anywhere.** No commit message, comment, or document explains
why the boundary sits at 12 signatures. `04_PARAMETERS.md` carries it as undocumented.

### D-26 · Residue candidate nomination

**Date and commit.** 2026-07-23 · `929492b`; documented in the README 2026-08-03 · `8d07acb`.

**Alternative visible in history — the prior behaviour, stated as a defect.** The module
docstring describes what the system did before this commit:

> Mode A (bucket partition) and Mode B (incremental) already adjudicate every signature pair
> WITHIN a `has_name2` bucket. What they never compare are pairs the deterministic Name-2
> asymmetry rule keeps apart: an empty-Name2 signature vs a populated-Name2 one, and a signature
> alone in its bucket. Those pairs bypass the LLM entirely and default to `unique` with no
> reasoning.
> (`dedup/candidates.py:1-7`)

and the commit body: "Updated the adjudicator to nominate residue pairs for LLM adjudication,
ensuring that pairs previously skipped are now evaluated, with reasoning recorded for decisions."

This is a partial reversal of a consequence of D-24 — not of the rule itself, which still holds,
but of the coverage gap the bucketing created.

**Why the chosen option.** Three constraints are recorded, each of which is a decision in its own
right:

1. *Nomination is candidacy only.* "Nomination is candidacy ONLY: it never merges. The LLM verdict
   and the two-level identity rule still decide" (`dedup/candidates.py:11-12`). The nomination
   rules — converging ROR/LEI, then suffix-stripped Jaro-Winkler ≥ 0.85, then token Jaccard ≥ 0.6
   — select what to ask about, not what to merge.
2. *Nomination is deterministic and pure.* "the same units in any order yield the same candidate
   list, so the LLM call sequence is stable" (`dedup/candidates.py:14-15`) — a reproducibility
   requirement placed on the one new component that could have introduced ordering dependence.
3. *Ordering relative to the identity guard.* The residue pass runs before the guard, and the
   comment states why: "Runs BEFORE the identity guard so a bad name/token merge across
   conflicting ROR/LEI is still split" (`dedup/adjudicator.py:855-858`).

**The cap, and what it chooses.** When a block nominates more than `MAX_CANDIDATES_PER_BLOCK`
(default 50), the whole block is routed to `manual_review` and a `candidate_cap_exceeded` issue is
emitted (`dedup/adjudicator.py:40,586-592`; `README.md:1265`). The alternative — process the top
N and drop the rest — is visible as rejected in the design of the message, which reports the
count rather than truncating silently, and in the README's note that "deterministic ordering
already put id-convergence pairs first". ⚠ The value 50 itself is **not justified** in any
artefact.

### D-27 · `cluster_id` changed from a sequential integer to a content hash

**Date and commit.** 2026-07-22 · `efe1379`.

**Alternative visible in history — the removed code.** The diff shows the whole prior mechanism
being deleted: a per-block counter, a response-level remap table, and the integer type.

```diff
-            cluster_id: Optional[int] = cluster_n
+            cluster_id: Optional[str] = cluster_hash(row_ids)
-            if row.cluster_id is not None:
-                if row.cluster_id not in local_to_global:
-                    local_to_global[row.cluster_id] = global_cluster_n
-                row.cluster_id = local_to_global[row.cluster_id]
```
(`efe1379` diff of `dedup/adjudicator.py`)

The prior scheme's limitation is documented in the README section that still describes it:

> **Note on `cluster_id`:** it is a plain sequential integer (`1, 2, 3, …`) running globally
> across the response, so it is unique within one response. If you split a file across multiple
> calls, each call restarts at 1 — offset the ids caller-side, or send all of a file's blocks in
> one call, if you need file-wide uniqueness.
> (`README.md:1213`)

⚠ That README passage is stale — it describes the pre-`efe1379` scheme. This is a code↔doc
discrepancy for `08_GAPS.md`; it is quoted here because it is the clearest surviving statement of
what the alternative could not do.

**Why the chosen option.** Recorded in the new module's docstring:

> `c_` + first 12 hex of sha256 over the sorted member row_ids. Same membership -> same id across
> runs, machines, and input orderings; a membership change -> a new id. String end-to-end (never
> an int/float).
> (`dedup/cluster_key.py:18-22`)

and the second reason, which is why the key got its own module:

> Kept in its own tiny, dependency-free module so `dedup.scoring` can import it without pulling in
> the LLM stack that `dedup.adjudicator` carries.
> (`dedup/cluster_key.py:4-6`)

The scorer re-derives the hash to detect a cluster whose members were split across score calls
(`README.md:2045`) — a check the sequential scheme could not support, since the id carried no
information about membership. The decision is therefore doubly motivated: run-to-run stability for
the merge-back, and a shared, cheaply-importable key that couples the two dedup endpoints without
coupling their dependencies.

---

## 8 · Phase 2 — election and approval

### D-28 · Clustering and election kept as separate endpoints

**Date and commit.** 2026-07-22 · `efe1379`.

**Alternatives visible in history.** None removed — election arrives as a new endpoint, and no
commit extends `/api/dedup/cluster-block` to elect. The alternative is recorded as a rejection in
prose rather than as deleted code, and it is one of the few decisions argued in the same words in
two places.

**Why the chosen option.** In the route handler:

> Separate from /api/dedup/cluster-block on purpose: clustering and election have different
> inputs, cadences, and cost profiles — election is pure arithmetic over dedup/weights.json and
> can be re-run on retuned weights without paying for LLM adjudication again. No LLM is involved.
> (`api/routes.py:900-903`)

and in the module:

> Separate from the LLM adjudicator on purpose: clustering and election have different inputs,
> cadences, and cost profiles. Election is pure arithmetic over an editable weights table
> (`dedup/weights.json`), so it can be re-run on retuned weights without paying for LLM
> adjudication again. No LLM, no network — ever.
> (`dedup/scoring.py:3-7`)

The operative claim is that weight retuning is expected to be iterative, and folding election into
clustering would price every iteration at an LLM adjudication of the whole batch.

**A second recorded property of the same boundary.** The scorer's error policy is stated as a
deliberate consequence of the data:

> The real CRM extract is ~half empty and dirty. Scoring is therefore permissive: a missing or
> unrecognised value scores 0 (with a warning when the value was present but unrecognised) and
> NEVER raises or fails the batch. The one hard error is a duplicated row_id in a single request —
> that means a broken upstream join, and scoring it would double-elect.
> (`dedup/scoring.py:9-14`)

One hard error is admitted, and the reason for admitting exactly that one is given.

### D-29 · A point-based model over an editable weights table

**Date and commit.** 2026-07-22 · `efe1379`.

**Alternative visible in history — the pre-existing DATAshaper implementation.** The same
criteria were already implemented in the vendor's system, in SQL, before this repository scored
anything. The transcript records its provenance, its content, and its author's own reservation:

> Actually, I also put some logic to automate, and that was the scores. But it's still a bit
> tricky because, I mean, we can always put logic, but even if all the logic says that it's a
> duplicate, it might not be a duplicate.
>
> But that was the purpose of the score fields. … I think in his original spec, Burn [Bernd] gave
> me some rules. And he said, for example, on the status, if it's an active customer, you need to
> score it, give it 10 points, or else 0 points. If it's a sleeping customer as well, and then
> depending on how many sales orders there are, you need to give it this many.
> (`Datashaper-Tutorial-Part2.txt:1853-1859`)

> we have a score for the status. We have a score for when was a last sales order, how many sales
> orders there are, if there was a partner that did the sales order, how many equipments are
> linked to the customer, if it's a sleeping customer, and if it's a customer status. And then we
> count all these together to get the final score. … And then I had also a script to say, "Okay,
> assign the golden record to the one with the highest score in the group."
> (`Datashaper-Tutorial-Part2.txt:1891-1898`)

The criteria list matches `dedup/weights.json` almost exactly. **One criterion is scored
differently, and the difference is the substantive alternative visible in the record.** The
DATAshaper implementation scored recency *relative to the run date*:

> we used a dates_difference function that counts the difference in months between the source
> date, the sales order last used, and get_date, the current date. So that means when it's between
> 0 and 9, then it's 25. When it's between 10 and 24, then it's 15. Else it's 5.
> (`Datashaper-Tutorial-Part2.txt:1877-1882`)

`dedup/weights.json` instead scores absolute years: `"2026": 20, "2025": 15, "2024": 10,
"2023": 5`. The relative form is stable as time passes but makes a score irreproducible — the same
row scores differently on a later run; the absolute form is reproducible but requires the table to
be edited each year. ⚠ **The change from months-since-now to absolute years is not recorded
anywhere in this repository, and no commit message mentions it. Whether it was a deliberate choice
for reproducibility or an artefact of transcription is a question for the author.**

**Why the chosen option — recorded in part.** The mechanism is argued; the criteria are not. The
weights file states the constraint the design serves:

> Golden-record scoring weights. Editable reference table — the scorer never hardcodes points.
> (`dedup/weights.json:2`)

which is what makes D-28's retuning claim true. The same comment records the parts that were not
confirmed at the time of writing:

> UNCONFIRMED (verify with Bernd): combined_presence_bonus value, sales_order_partner_count tiers,
> account_group DRIT (transcript said DRID; live SAP shows DRIT).
> (`dedup/weights.json:2`)

This is a decision recorded together with its own confidence, and it is the only parameter file in
the repository that does so.

**Version stamping.** Every scored row carries `scored_with_weights_version`, a 12-hex fingerprint
of the table, and the evaluation harness flags a workbook that mixes versions
(`README.md:2047`) — the auditing counterpart to making the table editable.

### D-30 · The year-priority rule, and its same-day correction

**Dates and commits.** 2026-07-23 · `c18921d`, then 2026-07-23 · `994fb3b`.

**Alternatives visible in history.** Both directions, within one day.

`c18921d` removes the prior unconditional count scoring and renames the fields to record what the
count now means:

> Renamed `order_count` to `orders_in_last_used_year` and `partner_order_count` to
> `partner_orders_in_last_used_year` to clarify their context-specific meaning.
> Updated scoring functions to ensure count points are awarded only when the row's last-used year
> is the most recent in its cluster, preventing older records from outscoring more recent ones.
> Introduced a suppression warning for cases where count points are not awarded due to recency
> rules.

`994fb3b` then removes part of what `c18921d` added — the warning fired in cases where nothing had
actually been lost:

> Updated the scoring logic to only flag genuine recency losses for sales order and partner
> counts, ensuring that context-free suppressions (where no recent competitor exists) do not
> trigger warnings.

**Why the chosen option.** The first commit attributes the rule to a person: "Refactor scoring
logic to align with Bernd's year-priority rule", and states the failure it prevents — an older
record with many orders outscoring a more recent one. The second states the failure *it* prevents
— a warning on every row whose year is simply the only year present, which would have made the
warning uninformative. The rule survives at `dedup/scoring.py:792` (`_award_count`), the fields at
`:141-157`, and the issue type `count_suppressed_by_recency` at `:410,497`.

The pair is worth recording together: it shows a rule adopted from an external spec, and a
by-product of that adoption corrected within the same day, before either reached a release.

### D-31 · Confidence-gated demotion to `manual_review`

**Date and commit.** 2026-07-22 · `efe1379`, introducing `CONFIDENCE_MERGE_THRESHOLD`, default
`0.95` (`dedup/scoring.py:48`).

**Alternatives visible in history.** None removed — before this commit a clustered row was elected
on regardless of the confidence the adjudicator returned.

**Why the chosen option.** Two reasons are recorded, and they are separable.

*Where the gate is applied.* At election, not at clustering: "gating here never re-runs the LLM"
(`dedup/scoring.py:47`). Raising or lowering the threshold is a data retune, consistent with D-28 —
"a pure data retune that never re-runs the LLM" (`README.md:1087`).

*How a cluster's confidence is computed.* The lowest member value, with the reason stated:

> The cluster's merge confidence = the LOWEST non-None member confidence.
> Conservative on purpose: if any member joined below threshold the whole merge is gated.
> All-None (a deterministic identical-collapse, no LLM merge) returns None and never gates.
> (`dedup/scoring.py:1021-1027`)

The last clause is D-22 showing through: a cluster that no model produced cannot be gated on model
confidence, because there is none.

⚠ **The value 0.95 is not justified** in any commit message, comment, or document.

### D-32 · Election proposes; a separate stateless endpoint records approval

**Date and commit.** 2026-07-22 · `efe1379`.

**Alternatives visible in history.** None removed. Auto-commit never existed: "Every election is a
PROPOSAL, never auto-committed" is in the function's docstring from the commit that introduced it
(`dedup/scoring.py:1046-1047`).

**Why the chosen option.** The control is stated as intent on the DATAshaper side — "This is the
human approval step: the system proposes, a steward confirms" (`CONTEXT-EXTERNAL.md:398-399`) —
and enforced on the service side by a data invariant rather than by convention: a `manual_review`
row leaves `is_golden_record` and `golden_record_id` empty, so nothing acting on
`is_golden_record` alone can touch an unreviewed row (`dedup/scoring.py:262-264`), and the
downstream contract filters on `approval_status`/`election_status`, "never is_golden alone"
(`dedup/scoring.py:266-268,1168`).

The vendor transcript records the same conclusion reached independently about the pre-existing
implementation: an automated highest-score assignment exists, "But yeah, as I said, it's still a
bit tricky. … It's not because all the rules say it's a duplicate that it's always a duplicate"
(`Datashaper-Tutorial-Part2.txt:1895-1901`).

**A scope decision recorded alongside it.** The approval endpoint is stateless and says so:

> Stateless: the caller submits the scored rows, the decision is applied to [them] … Persistence is
> out of scope for now.
> (`api/routes.py:948-953`; `dedup/scoring.py:551-556`)

⚠ Why persistence is deferred, and where the approval record is expected to live, is **NOT
RECORDED — author to supply**. `02_ARCHITECTURE.md` §6 carries the same finding.

---

## 9 · Contracts, configuration, and infrastructure

### D-33 · `EnrichmentResult` slimmed

**Date and commit.** 2026-06-03 · `3ce5e94`, "Refactor EnrichmentResult model and initialization
logic".

**Alternative visible in history — the removed fields.** The diff deletes eighteen field
declarations: `name1_original`…`name3_original`, `name1_changed`…`name3_changed`,
`care_of_original`/`_changed`, `contact_original`/`_changed`, `email_original`/`_changed`,
`street1..3_original`/`_enriched`/`_changed`, plus `unclear_address_info` and `address_issues`.
Twelve further fields are retained but marked `exclude=True` (`tier_used`, `tier2_mode`,
`confidence`, `ror_id`, `source_url`, `contact_used`, `name2_match_result`,
`use_cases_triggered`, `enrichment_status`, `duration_ms`, and two more).

**Why the chosen option.** Recorded only as terseness, in the comment the commit adds:

> NOTE: the fields marked `exclude=True` below are still populated and used internally (tier
> logic, batch summary counts, tests) — they are just omitted from the serialised API response to
> keep the output lean.

⚠ **RATIONALE NOT IN REPO — author to supply** for the deletion of the `*_original`/`*_changed`
pairs. The commit body says "Removed unnecessary original and changed fields to streamline the
model", which asserts the conclusion rather than the reason. Note that this removes the response's
ability to report *what changed* for those fields — a capability relevant to evaluation — and no
artefact records that being weighed.

### D-34 · `ror_id` re-exposed, and `lei_id` added

**Date and commit.** 2026-06-29 · `6d1805e`, reversing part of `3ce5e94`.

**Alternative visible in history.** `3ce5e94`'s own change, three weeks earlier:
`ror_id: Optional[str] = Field(default=None, exclude=True)`.

**Why the chosen option.** Recorded, with the downstream consumer named:

> `lei_id` added to `EnrichmentResult`, and `ror_id` is **no longer `exclude=True`** — both now
> appear in the JSON `/enrich` response and as **"ROR ID" / "LEI ID"** columns in `/enrich/file`
> …, so the dedup phase can converge on a shared identifier.
> (`README.md:2015`)

This is a clean instance of a leanness decision (D-33) being reversed once a consumer for the
field existed. The dedup side treats both ids as hints, never as cluster keys
(`README.md:1140-1147`; `dedup/signatures.py:73-76`).

### D-35 · `/enrich` JSON aligned to the file column schema

**Date and commit.** 2026-07-14 · `701ebd0`.

**Alternative visible in history.** The prior arrangement, in which the JSON response used its own
field names and carried the internal `domain` field.

**Why the chosen option.** Recorded:

> Serialise EnrichmentResult with the exact /enrich/file column headers as JSON keys via
> RESPONSE_COLUMNS, and drop the internal-only domain field from the response, so the JSON body
> and the file share one schema — same columns, same names, same order.

The same principle is applied again to the scoring endpoints in `8f2bb6b` (2026-08-03), which
makes `/api/dedup/score` and `/api/dedup/score/file` "functionally identical and expose the exact
same input/output column names", flattens `score_breakdown` into eleven `score_*` columns, and
replaces the Salesforce id list with eight flat `sf1`…`sf8` columns while keeping the list
accepted for backward compatibility. Both commits state the reason as one schema across two
transports (`701ebd0`, `8f2bb6b` messages; `README.md:1089-1093`).

### D-36 · "Domain" and "Website URL" merged into one output column

**Date and commit.** 2026-06-05 · `eee57b7`.

**Alternative visible in history — the removed mapping.** The diff:

```diff
-    "domain": "Domain",
-    "website_url": "Website URL",
+    "website_url": "Domain",
```

The registrable `domain` becomes internal-only from this commit; the public column named "Domain"
carries the homepage URL. `Domain_DeptDomain_SearchTerm_Logic.pdf` §1 opens by warning readers of
exactly this: "The public 'Domain' output column is actually `website_url`; the bare registrable
`domain` is internal."

**Why the chosen option.** ⚠ **RATIONALE NOT IN REPO — author to supply.** The commit body records
the change as "Adjusted output columns to merge 'Domain' and 'Website URL' into a single 'Domain'
column for clarity", which names an aim without an argument, and the resulting name/value mismatch
is significant enough that two later documents open by correcting the reader's expectation.

### D-37 · The `dry_run` option removed

**Date and commit.** 2026-06-05 · `eee57b7`. The pickaxe `git log --all -S 'dry_run'` returns
`f77080b`, `847f92e`, `86e265a`, `b19cd1a`, `eee57b7`.

**Alternative visible in history — the deleted option.** It existed on both the request model and
the query string from the first commit:

```diff
-    dry_run: bool = False
-    dry_run: bool = Query(default=False),
-        dry_run=dry_run,
```

**Why the chosen option.** Recorded only as "Removed the `dry_run` option from enrichment endpoints
and tests to streamline functionality and focus on core processing." ⚠ Whether it was unused,
misleading, or superseded by the mock layer (`MOCK_EXTERNAL_CALLS`, `tests/mocks/`) is **not
recorded**. The consequence is on the record elsewhere: there is now no way to exercise the
enrichment route without incurring external spend other than the test mocks
(`07_EVALUATION.md` §3).

### D-38 · Corporate-VPN TLS: a pin replaced by a resolver

**Dates and commits.** 2026-06-19 · `600823c` (LLM client); 2026-06-29 · `6d1805e` (ROR and GLEIF
clients).

**Alternative visible in history — the removed approach *and* its own recorded rationale.** This
is the clearest case in the repository of a deliberate decision being reversed, because the
docstring being deleted argues for the thing being deleted:

> The httpx client is constructed explicitly with `verify=certifi.where()` so that a bogus
> `SSL_CERT_FILE` env var (a common gotcha when a .env file contains a placeholder corp-CA path
> that no longer exists) cannot break TLS context construction.
> (removed in `600823c`, from `llm/openai_client.py`)

**Why the chosen option.** The replacement docstring records the failure the pin caused, and
preserves the original concern as a fallback rather than discarding it:

> Corporate VPNs frequently terminate TLS with their own root CA (SSL inspection / MITM proxy).
> When that happens, verifying the connection against certifi's public bundle fails the handshake
> and every LLM call hangs or errors out the moment the VPN is connected.
> (`600823c`, added to `llm/openai_client.py`)

The resolver's three-step order is itself the reconciliation: `LLM_SSL_VERIFY=false` first,
"Insecure — a last resort … Logged loudly"; then a CA bundle from
`AZURE_OPENAI_CA_BUNDLE`/`REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` **only if the file exists** — which
is what defuses the bogus-placeholder problem the pin was defending against; then certifi. The
dedicated variable is first "so the LLM client can be pointed at a corp CA without disturbing
other tooling."

**Why it was extended.** `6d1805e` applies the same resolver to ROR and GLEIF, with the failure
recorded:

> Both previously hardcoded `verify=certifi.where()`, so on a TLS-inspecting VPN every ROR/GLEIF
> call failed the handshake — `ror_id`/`lei_id`/`domain` came back empty and every record fell
> through to the LLM.
> (`README.md:2057`)

That last clause records a systemic consequence worth noting for evaluation: an environment-level
TLS failure did not surface as an error but as a silent, total escalation to Tier 3 — the tiering
of D-2 failing open.

### D-39 · Azure OpenAI as the only LLM backend

**Date and commit.** 2026-08-12 · `515cc7c`.

**Alternative visible in history — the deleted configuration.** `OPENAI_API_KEY` and
`OPENAI_MODEL` (default `gpt-4o`) existed from `f77080b`; the pickaxe
`git log --all -S 'OPENAI_API_KEY' -- config.py llm/openai_client.py` returns `f77080b`,
`9938596` and `515cc7c`, and `-S 'gpt-4o'` additionally returns `847f92e` and `600823c`. So a
direct-OpenAI path was configurable for four months.

**Why the chosen option.** Recorded as removal of dead configuration rather than as a choice
between backends:

> **Azure-only LLM backend** — the dead direct-OpenAI config (`OPENAI_API_KEY` /
> `OPENAI_MODEL=gpt-4o`) was removed; Azure OpenAI is the only backend in every environment (the
> `openai_client.py` docstring was corrected).
> (`README.md:2008`)

⚠ When the direct path stopped being used — and whether it ever was — is **not recorded**. The
word "dead" asserts it, and `9938596` ("Search term and azure deployment", 2026-05-31) is the
commit that introduced the Azure deployment configuration, but no commit records the switchover
itself.

### D-40 · Dedup LLM pinned to a newer API version, with `reasoning_effort` dropped on rejection

**Date and commit.** 2026-06-17 · `13a1274` (the `dedup/llm.py` module); the shared client factory
was parameterised so Phase 1 was not affected.

**Alternatives visible in history.** None removed. The alternative recorded is a failure mode
rather than a prior implementation.

**Why the chosen option.** Two decisions with recorded reasons:

*The version pin.* "reasoning models … and the `reasoning_effort` parameter require a newer
version than the Phase 1 default; override with AOAI_API_VERSION_DEDUP if your resource [differs]"
(`dedup/llm.py:109-112`, default `2025-04-01-preview` at `:112`). The README records the incident
this prevents: it "was the root cause of an early failure mode where every dedup row came back as
`manual_review` with `errors > 0`" (`README.md:2052`), and `GET /diag/dedup-llm` exists to surface
it (`README.md:1109-1117`).

*The parameter fallback.* If the deployment rejects `reasoning_effort`, it is dropped and the call
retried, on the recorded ground that "the parameter is a tuning preference, not a correctness
gate" (`README.md:1278`; implemented at `dedup/llm.py:199-207`, detector at `:33-38`). The same
distinction governs the block-level error policy: "A single bad LLM call **never** fails a whole
block" (`README.md:1281`; `dedup/llm.py:80`).

### D-41 · DuckDuckGo retained as a keyless search fallback

**Date and commit.** 2026-04-09 · `f77080b`; never removed.

**Alternatives visible in history.** None — this entry records a decision *not* taken. Both
clients have been present since the first commit (`search/serpapi_client.py`,
`search/duckduckgo_client.py`), `duckduckgo-search>=6.0.0` has been in `requirements.txt`
throughout (the only dependency changes in 51 commits are the two additions in `b19cd1a`,
`openpyxl>=3.1.0` and `python-multipart>=0.0.9`; nothing has ever been removed), and the fallback
is still wired at `enrichment/orchestrator.py:778-781`.

**Why the chosen option.** Recorded only as a quality caveat at the point of fallback:

> SERPAPI_KEY is not set — falling back to DuckDuckGo. DuckDuckGo returns lower-quality results.
> (`config.py:142-143`; the same warning at `enrichment/orchestrator.py:778`)

⚠ Whether the fallback is intended for production or only for local development is **not
recorded**; `06_EXTERNAL_DEPS.md` carries the coupling consequence. The provider actually used is
reported per request as `serp_provider` (`api/routes.py:1116`), so the choice is observable in the
output even though the policy is not documented.

---

## 10 · Decisions whose reason the repository does not record

Collected here so the author can address them in one pass. Each is a decision the history proves
was made, on a date, in a commit — with no recorded reason. These are prompts for the author, not
defects in the code.

| ID | The decision | What the record contains | What is missing |
|---|---|---|---|
| D-1 | Both Name-2 correction paths disabled in `635d5ba` | An itemised commit body that does not mention the gate condition or the removed call site | Whether the two were judged redundant, one unreliable, or the gate was an unintended consequence |
| D-12 | ROR outranks SERP outranks LLM for `website_url` | Full mechanical documentation in the README and the PDF | Any statement of why registry beats search beats inference |
| D-15 | Two of four retrieval hypotheses promoted to changes | The PDF says "noted, not applied"; the README and code in the same commit say applied | Who lifted the constraint, and why these two and not the other two |
| D-16 | `DEPT_PROBE_CROSS_DOMAIN` introduced `true`, flipped `false` | "broader coverage" (introduction) vs "matches the documented intent" (flip) | Which intent was correct when the flag was written |
| D-17 | `derive_department_domain` and `_probe_department_url` co-existed for eleven weeks | Both added in `bb3cae8`; one deleted in `515cc7c` | Why both were written, and whether the deleted one was ever live |
| D-23 | Postal-code-exact blocking accepted, with its recall ceiling | The vendor states the ceiling plainly in the transcript | Any record of that ceiling being weighed when the input contract was adopted |
| D-23 | Embeddings excluded | One sentence naming the exclusion | Whether they were evaluated, and against what |
| D-25 | `SIG_PARTITION_THRESHOLD` = 12 | The value and its effect | Why 12 |
| D-26 | `MAX_CANDIDATES_PER_BLOCK` = 50 | The value, and that exceeding it routes the block to review | Why 50 |
| D-29 | Recency scored on absolute years, not months since the run date | Both forms are on the record — the DS form in the transcript, this one in `weights.json` | Whether the change was deliberate (reproducibility) or transcription |
| D-31 | `CONFIDENCE_MERGE_THRESHOLD` = 0.95 | The value, the gate, and that it is a data retune | Why 0.95 |
| D-32 | Approval persistence deferred | "Persistence is out of scope for now" | Where the approval record is expected to live |
| D-33 | `*_original` / `*_changed` fields deleted from the response | "Removed unnecessary original and changed fields to streamline the model" | Why they were unnecessary, given they carried the what-changed signal |
| D-36 | "Domain" and "Website URL" merged, leaving a name/value mismatch | "for clarity" | Why the merged column takes the URL under the name `Domain` |
| D-37 | `dry_run` removed | "to streamline functionality" | Whether it was unused, misleading, or superseded |
| D-39 | Direct-OpenAI backend removed as "dead" | The removal, four months after introduction | When and why it stopped being used |
| D-41 | DuckDuckGo kept as a fallback | A quality warning at the fallback point | Whether it is a production path or a development convenience |

Two further items belong to this list but are recorded outside the repository and are carried in
`CONTEXT-EXTERNAL.md` rather than here: the exclusion of ZFI records, stated as Bernd Schnurrer's
instruction with the rationale not recorded (`CONTEXT-EXTERNAL.md:434-435`; also
`dedup/scoring.py:15-17`, which records only that ZFIS is "a separate upstream gate that runs
before enrichment"), and why address validation and the `/issues` call are separate ADF pipelines
rather than folded into enrichment (`02_ARCHITECTURE.md` §9).

Stop.
