Generated: 2026-09-08 · Commit: 86d173b8a4d715a619b0a2656986c145da7fa81e · Branch: feature/llm-fixes · Pass: 16

# Pass 16 — Rulesets

Four rulesets, one section each. Every row states a rule as a predicate a reader can evaluate
against a record without running the code, and cites the line that implements it. Where a rule is
a threshold, the constant is copied verbatim.

Scope boundary carried from Pass 15: `ISSUE_CATALOGUE` and `DedupIssue` are two disjoint
vocabularies with no mapping between them (`docs/thesis/15_ISSUES_DOSSIER.md` §15.9). §16.2 is
`ISSUE_CATALOGUE` alone — the 33 live codes. The `DedupIssue` predicates are election-stage
output and sit in §16.4.

Working-tree note (Rule 1): `git status --porcelain` is non-empty at this commit and every entry
is a file under `docs/thesis/` produced by this documentation run. No source file, SQL file, ADF
export, fixture or workbook is modified.

---

## 16.1 Standardisation and enrichment rules

### 16.1.1 Deterministic preprocessing — step order

`preprocess_record` (`enrichment/preprocess.py:1810–2800`) is one pass with a fixed step order.
The order is load-bearing and each step's comment states why. Steps in execution order:

| # | Step | Predicate / action | Lines |
|---|---|---|---|
| 0 | leading opaque-code strip | `_strip_leading_opaque_code(slot) != slot` → slot takes the stripped value; runs first so a following `c/o` becomes a prefix UC 15 can route | `:1853–1862` |
| 1 | repeated-phrase collapse | slot is a whole-number repetition of a shorter token sequence → keep one occurrence (`_collapse_repeated_phrase` `:734`) | `:1861–1875` |
| 2 | parent-organisation split | Name 1 carries a parent phrase → move to the dept block | `:1877–1913` |
| 3 | acronym / full-form dedupe on Name 1 | Name 1 states one organisation twice, once as an acronym → keep one | `:1915–1936` |
| 4 | **UC 15** c/o + ATTN five-case classifier | §16.1.3 | `:1938–1941` |
| 5 | **UC 7** Pattern A (Attn prefix) | an `ATTN` clause anywhere in a name slot → contact out, remainder kept | `:2160–2213` |
| 6 | **UC 6** Accounts Payable | §16.1.2 | `:2216–2251` |
| 7 | **UC 8** email extraction | `_EMAIL_RE` matches in a name or street slot → address to `Email`, slot keeps the residue; a second distinct address raises `email-conflict` | `:2253–2300` |
| 8 | **UC 9** street + delivery-instruction split | a dept slot holds a street line *and* an access qualifier → both halves move, **and nothing moves unless every half has somewhere to go** | `:2302–2345` |
| 9 | **UC 9** address extraction | `_extract_addresses(slot)` returns fragments → each to `_first_empty_street_slot`; no free slot → flag `street-slots-full` | `:2347–2372` |
| 10 | **UC 12** bracketed span / site-qualifier strip | leading bracketed org rescued; trailing site qualifier split off (recording `_ev_name_site_conflict`); any remaining parenthetical stripped | `:2374–2434` |
| 11 | **UC 11** DBA normalisation | any `_DBA_PATTERNS` variant → literal `DBA`; changed slots recorded in `res.dba_fields` | `:2435–2446` |
| 12 | **UC 17** legal-suffix normalisation | legal form rewritten to its canonical spelling | `:2448–2461` |
| 13 | **UC 10** opaque-code clearing | `_is_opaque_code(slot)` → slot cleared, **department slots only**; Name 1 is excluded because clearing it would leave the record with no name | `:2463–2470` |
| 14 | **UC 7** contact extraction | a person shape in a name slot → `Contact`, slot cleared | `:2472–2520` |
| 15 | **UC 16** institution + embedded department split | Name 1 matches `_INSTITUTION_PREFIX_RE` **and** ends in `_DEPT_PHRASE_RE` → the trailing phrase moves to the dept block; a jurisdiction prefix ("Florida Department of Health") carries no institution keyword and is left intact | `:2522–2549`, regexes `:1113–1129` |
| 16 | **UC 13** dept-slot residual junk cleanup | — | `:2551–2586` |
| 17 | **UC 14** slot consolidation / leftward pack | Name 1 held only a person **and** `_looks_like_institution(name2)` or `_looks_like_org_acronym(name2)` → promote Name 2 to Name 1; then pack the dept block leftward | `:2588–2633` |
| 18 | **UC 12** duplicate-slot clearing | for each slot from the last upward, `_equiv(upper, lower)` → clear the lower | `:2635–2680` |
| 19 | street-sourced and slot-origin resolution | bookkeeping only | `:2682–2727` |

`_equiv(a, b)` is canonical-form equality **or** `fuzz.ratio ≥ 92` over
`collapse_ws(canonicalise_unit_name(v) or v).lower()` (`:2647–2663`). 92 is stated as strict
enough to keep "Physics" and "Physiology" apart.

Every rule in this stage is a pure function of the record's own fields except the UC 7 Pattern B2
person verdicts, which come from an LLM call made outside `preprocess_record` and passed in as a
dict (`enrichment/orchestrator.py:7831–7833`, consumed at `enrichment/preprocess.py:1826`,
`:1844`).

### 16.1.2 Admin-desk and unit-word rules

| Rule | Predicate | Data | file:line |
|---|---|---|---|
| UC 6 — org + desk in one field | `_split_ap_suffix(val)` or `_trailing_ap_phrase(val)` returns an organisation → that organisation stays in the slot and `"Accounts Payable"` is written to `_first_empty_name_slot`; **no free slot → the desk is left in place** | `_AP_PATTERNS` `enrichment/preprocess.py:184–195` | `:2229–2240` |
| UC 6 — desk-only field | `_is_ap_reference(val)` → slot rewritten to the literal `"Accounts Payable"` | `_BARE_AP_SEGMENTS = {"ap","a/p","a.p.","acctpay","acctspay"}` `:207`; delimiter `_AP_DELIM_RE = r"\s*[|,;]\s*\|\s+[-–—]\s+\|\s*/\s*"` `:220` | `:2249–2250` |
| AP short-circuit | any name slot casefolds to `"accounts payable"` → the record ends at Tier 2 with `source="pattern_match"`, `confidence="high"`, `enrichment_status="enriched"` | — | `enrichment/orchestrator.py:8607–8624` |
| `classify(value)` — what a dept-block value names | `empty` (blank) · `admin` (`is_admin_unit`) · `identifies_nothing` (`identifies_nothing`) · `granular` (`is_granular_unit`) · `unit` (everything else). The first two are the two halves of `has_no_canonical_form` | — | `enrichment/dept_block.py:207–243`; `has_no_canonical_form` `enrichment/search_terms.py:678–708` |
| `same_unit(a, b)` | canonical forms equal, **or** `fuzz.ratio ≥ 92`, **or** one is the other with the tail cut off (`is_truncation_of`) | `RATIO_THRESHOLD = 92` `enrichment/dept_block.py:112` | `:170–184` |
| `is_truncation_of(short, long)` | every token of *short* is a prefix of the token at the same position in *long*, and *long* is strictly longer. A **designator** is therefore not a truncation: "Dept A" / "Dept B" differ at a position where neither is a prefix of the other | — | `:141–167` |
| department-block normalisation | four steps in order: **empty** (whitespace-only slot → empty) · **dedup** (a value that `same_unit`s one already kept is dropped, unless it is the fuller form of it, in which case it replaces the fragment) · **pack** (survivors move up into the holes, origins travelling with them) · **order** (among ranked constructions only, a division is written above a department; stable sort) | `UNIT_SLOT_RANK` `utils/text_utils.py` | `enrichment/dept_block.py:246–340` |
| `_is_fuller(value, kept)` | `is_truncation_of(kept, value)` **and** `kept` is not an admin desk — an admin desk has no fuller form, because "Accounts Payable Dept" accounts for every word of "Accounts Payable" and adds one of its own | — | `:189–204` |

### 16.1.3 Care-of decomposition — UC 15, five cases

`_extract_co_attn_from_slot` (`enrichment/preprocess.py:1439–1567`), run over every slot in
`DEPT_SLOTS`; Name 1 is excluded because it holds the organisation (`:1450–1454`).
`payload, had_prefix = _strip_co_attn_prefix(val)` then
`payload = _strip_parenthetical_noise(payload)` (`:1460–1461`).

| Case | Predicate | Outcome | Lines |
|---|---|---|---|
| prefix-only | `payload` empty **and** `had_prefix` | slot cleared | `:1463–1468` |
| **D** email | `_EMAIL_RE.search(payload)` **and** (`had_prefix` or the payload is only the address) | address to `Email`; a second distinct address raises `email-conflict`; slot cleared | `:1470–1490` |
| no prefix | `not had_prefix` | only Case E fires: `_is_job_title(payload)` → `care_of := payload`, slot cleared; otherwise nothing | `:1494–1500` |
| **C** department | `_is_department_payload(payload)` | prefix stripped, **value kept in the slot** | `:1503–1506` |
| **B** company | `_has_legal_suffix(payload)` | `care_of := payload`, slot cleared | `:1509–1512` |
| **A** person | `_TITLE_PREFIX_RE.match(candidate)` **or** (`_PLAIN_NAME_RE` matches **and** no `_ORG_SIGNAL_RE` **and** not a job title) **or** the LLM verdict for `candidate.lower()` is `person` | `contact := _strip_title(candidate)` (or flag `contact-conflict`), `care_of := candidate`, slot cleared; when the slot was Name 2, `res.trigger_dept_lookup = True` | `:1518–1553` |
| **E** fallback | none of the above, prefix present | `care_of := payload`, slot cleared | `:1563–1565` |

Marker: `_CO_ATTN_MARKER = r"\b(?:c\s*/\s*o|att?n+(?:ention|tion)?|att)\b"`
(`enrichment/preprocess.py:1168`). `_set_care_of` (`:1569–1586`) enforces first-writer-wins across
slots: Name 2 is scanned before Name 3, and a second, different payload is discarded with the flag
`care-of-conflict` rather than overwriting.

⚠ Case B does not fire on the only `C/O <company>` rows in the evaluation corpus:
`_has_legal_suffix` (`:1392–1403`) tests `_LEGAL_SUFFIX_RE` (`:1295`) and "Parallon Business
Solutions" carries no legal form, so the value leaves the name block via Case E instead. The
routing outcome is identical; the classification recorded in the note differs
(`docs/thesis/08_GAPS.md`).

### 16.1.4 Registry acceptance criteria, per tier

**Tier 1 — ROR.** `call_ror` (`enrichment/tier1_ror.py:891–1607`). Acceptance threshold
`threshold = float(os.getenv("ROR_CONFIDENCE_THRESHOLD", "0.8"))` (`:936`).

| Rule | Predicate for acceptance | Constant | file:line |
|---|---|---|---|
| local rescore | `_compute_name_score(query, candidate) ≥ threshold`, applied even to a candidate ROR itself chose — ROR's own affiliation scorer lacks the identifier-token guard | `0.8` | `:1281–1300`, scorer `:378–556` |
| exact arm | `query_lower == variant` for **any** variant, acronyms included → score `1.0` | — | `:424–426` |
| subset / substring arm | canonical names (`ror_display`, `label`) only; skipped when the query's identifier tokens are not a subset of the candidate's, and skipped when every significant query token is a location token | length ratio `0.9`; `_CANONICAL_NAME_TYPES = {"ror_display","label"}` `:351` | `:464–480` |
| distinctive-token cap | a distinctive query token (`len ≥ 4`, not a common domain word, not a connector, not a location token) covered by **no** candidate token → `token_ratio = min(token_ratio, 0.7)` | `_DISTINCTIVE_TOKEN_MIN_LEN = 4` `:593`; cap `0.7` `:541` | `:508–541` |
| identifier-token cap | the query's identifier tokens are not a subset of the candidate's → `token_ratio = min(token_ratio, 0.7)` | cap `0.7` `:545` | `:543–546` |
| country guard | `_country_ok(item, want)` — every wrong-country candidate dropped before ranking, **and again on the no-filter retry** | — | `:745–757`, `:1436–1451` |
| ambiguity guard | `sorted(scores, desc)[0] − [1] < scaled_margin` over peers neither `_is_exact` nor `_token_diff` separates → **no match** | `REGISTRY_AMBIGUITY_MARGIN = 2.0` on a 0–100 scale (`enrichment/registry_match.py:63`), rescaled for ROR's 0–1 | `:1514–1540`, `registry_match.py:150–161` |
| short-name guard | `is_collision_prone(name)` → a `second_signal` is required: locality verdict `CONSISTENT`, or the candidate's own host equal to the record's domain. A `neutral` verdict is not agreement | `_SHORT_NAME_MAX_LEN = 4`, `_ACRONYM_MAX_LEN = 5` (`registry_match.py:87`, `:83`) | `:1567–1568`, `registry_match.py:99–186` |
| `chosen: false` path | accepted only when a name variant equals the query verbatim once `+ / – — -` fold to spaces. Periods and apostrophes are deliberately **not** in the fold set | `_SEPARATORS` `:1109` | `:1189–1245` |
| write on acceptance | unconditional — there is no second threshold. A fuzzy match the gate reads as a different organisation is demoted to a miss **before** the branch is taken, so the identifier and the name are never separated | — | `enrichment/orchestrator.py:8140–8180` |

The 0.7 cap is set below the 0.8 threshold, so a capped candidate cannot be accepted (`:531–533`).
The cap value, the threshold and the distinctive-token floor are three separate constants and
changing one does not move the others.

**Tier 1 — GLEIF / LEI.** `call_lei` (`enrichment/tier1_lei.py:564–704`). Name-verification
threshold is a parameter default, not an environment variable: `threshold: float = 88.0` (`:570`).

| # | Rule | Predicate for acceptance | file:line |
|---|---|---|---|
| 1 | country guard | the candidate has a registered address in the requested country; **both** `legalAddress` and `headquartersAddress` count, and agreement with either is agreement | `:352–380` |
| 2 | name verification | `_name_match_score(query, name) ≥ 88.0`, where the score is `max(token_sort_ratio(q,n), token_sort_ratio(strip_legal(q), strip_legal(n)))`. `token_set_ratio` is explicitly rejected: it scores any contained substring 100 and would accept "Personalvorsorgestiftung der Pfizer AG in Liquidation" for "Pfizer AG" | `:132–155`, `:381–402` |
| 3 | total order | survivors sorted by `rank_key(score, lei_id, -is_active, -region_agrees)` — ACTIVE first, then region agreement, then score, then LEI ascending. `_region_agrees` returns `False` when the record states no region: silence is not agreement | `:265–282`, `:403–420` |
| 4 | ambiguity | winner vs the best other country-passing candidate **within the same registration status** and excluding candidates region already separated, within the margin → no match | `:422–465` |
| 5 | locality | `compare_registry_addresses` over both addresses; **carried, never acted on** — written to `location_verdict` / `location_detail` / `location_scope` / `location_notes` | `:467–481` |
| 6 | collision-prone name | `is_collision_prone(name)` requires a `second_signal`; GLEIF publishes no website field, so `candidate_domain=None` and only locality or the record's own domain can corroborate | `:489–518` |

`_FUZZY_RESOLVE_LIMIT = 5` (`:95`) bounds how many `fuzzycompletions` candidates are resolved to
full lei-records, and the five taken are the five with the smallest LEI, not the first five
returned. Every failure mode returns a dict rather than raising — the stated contract is "never
raises — a GLEIF failure must not fail the record" (`:585`).

**Grounded resolver — registry re-verification.** `_re_verify`
(`enrichment/grounded_resolver.py:404–524`) chooses which registry to ask by routing type:
`research_institution` → `("ROR",)`; `company` → `("GLEIF",)`; `unknown` and
`looks_like_research_institution(name)` → `("ROR","GLEIF")`; otherwise `("GLEIF","ROR")`
(`:443–452`). A *decided* routing type asks exactly one registry. Two re-verification-specific
refusals on top of the clients' own guards:

- the registry's stated country and the record's, once normalised, both present and different →
  refuse (`:493–506`);
- for a Name 2, the identifier is Name 1's own → refuse; the registry has matched the institution
  again, which the record already knows (`:508–521`).

### 16.1.5 Tier 2 canonicalisation and the corroboration rule

**Gate** (`enrichment/orchestrator.py:8792–8794`): `routing_type in ("research_institution",
"company")` **and** `name1_enriched` set. Then, per slot in `DEPT_SLOTS`, four skips before any
call:

| Skip | Predicate | Lines |
|---|---|---|
| blank | slot empty | `:8782–8783` |
| already resolved | slot has a value **and** its `_slot_origin ∈ dept_block.RESOLVED_ORIGINS` | `:8791–8796` |
| UC 11 DBA | slot is in `_dba_values` — the model strips the marker | `:8797–8806` |
| no canonical form | `has_no_canonical_form(pp_val, result)` — an admin desk, or a phrase built entirely of facility functions and scope qualifiers | `:8823–8840` |

`run_tier2_canonical` (`enrichment/tier2_canonical.py:179–265`) makes one LLM call, no SERP, no
page fetch. **Confidence is not a write gate** (`:225–231`). Two deterministic refusals:

- `cleaned.lower() in {"null","none","n/a","na"}` → failure (`:214–216`);
- `_is_prefix_downgrade(name2, cleaned)` → failure (`:41–48`, applied `:247–252`): the canonical
  direction is bare → `"Department of X"`, never `"Department of Biology"` → `"Biology"`.

Three post-conditions on a successful answer, in order (`enrichment/orchestrator.py:8878–8985`):
the proposal **echoes Name 1** → rejected, slot retains its own value, proposal handed to the
suggestion machinery; the proposal is a **granular unit** → rejected, passthrough; the proposal is
the **same value folded** → recorded as a `transform`, not a re-attribution, so the slot keeps its
existing origin. Otherwise written with `llm_evidence(..., tier=2)`. A failed call is a `transform`
back to the input value, not an input write — the origin follows the value.

**The corroboration rule for Tier 2A.** `run_tier2a` (`enrichment/tier2a_contact.py:74–193`).
Gate, computed early so the canonical short-circuit does not steal the population
(`enrichment/orchestrator.py:8778–8783`): `routing_type == "research_institution"` **and**
`pp_contact` non-blank **and** not `multi_contact` **and** `institution_domain` is not None.

| Rule | Predicate | file:line |
|---|---|---|
| one query | `f'"{clean}" site:{domain}'` when a domain is known, else `f'"{clean}" "{institution}"'` — exactly one, stated as policy | `:296–306` |
| deterministic name filter | **both** the first name and the surname appear as whole words in the URL, title or snippet; `. - _ /` normalise to spaces and the concatenated slugs `firstlast` / `lastfirst` are also accepted | `:222–268` |
| ranking | `score_search_result` **+100** when both names appear in the URL path, **+20** when the title starts with the full name; sorted `(-rank, url)` so equally-ranked candidates do not inherit SERP order | `:344–373` |
| page acceptance | `extraction["person_found"]` must be true **and** `extraction["confidence"] != "low"`; otherwise the candidate is skipped and the next of at most three is tried | `:156–163` |
| Mode A (Name 2 blank) | `name2_enriched := official_dept`, `name3_enriched := official_group`, status `enriched` at either confidence; `source_url` records the page, so **no review flag is raised** | `:418–444` |
| Mode B (Name 2 populated) | `effective_score = max(llm_score, fuzz.token_sort_ratio(existing_name2, official_dept))`; `≥ fuzzy_threshold` → write, status `verified` at `≥ 95` else `enriched`; below threshold → write only when the model's confidence is `high`, otherwise leave Name 2 untouched, status `unresolved`, and add `name2` to `low_conf_unchanged` | `:469–524` |

⚠ `effective_score` compares two numbers on different scales — the model's self-reported match
score against a RapidFuzz `token_sort_ratio` — and thresholds the winner with one number. The code
records this as a known defect left in place (`:476–487`), and notes that the provenance event
carries `llm_self_reported`, which is the scale of whichever number won.

**Domain corroboration.** `_corroborate_domain` (`enrichment/orchestrator.py:7126–7353`) opens the
candidate site through `read_page` (`enrichment/page_corroborator.py:346–380`), covering `/` plus
the first `IMPRINT_PATHS` entry that returns 2xx; text below `_MIN_CONTENT_CHARS` returns `None`
without an LLM call. **Only a region- or country-level contradiction may withdraw a domain**
(`enrichment/page_corroborator.py:417–419`). Otherwise `_ship_unverified_domain`
(`enrichment/orchestrator.py:1922–2002`) keeps the candidate in the `domain` column at
`web:{domain}:low` and raises `domain-unverified` rather than blanking the column.

### 16.1.6 Tier 3 flag rule

Tier 3 is the grounded resolver's **degraded mode**, entered on exactly four conditions:
`no_names` (`enrichment/grounded_resolver.py:551–558`), `serp_empty` (`:363–364`),
`all_fetches_failed` (`:389–397`) and `llm_failed` (`:595–601`); dispatch at
`enrichment/orchestrator.py:9176–9198`.

| Rule | Predicate | file:line |
|---|---|---|
| grounded containment guard | `_flatten_for_containment(value) in evidence_haystack` — a **substring** test, not a fuzzy one. Recasing, repunctuating and rewrapping are the three permitted differences and there is no fourth. `evidence_haystack` is built from the same rendered string the model was shown, so the two cannot drift | `enrichment/grounded_resolver.py:271–302`, `:580–582`, `:642` |
| address-like guard | value carries a postal code, **or** a street-suffix word **and** a digit, **or** ≥ 50 % token overlap with the record's own street field → dropped. Imported, never reimplemented, by the grounded resolver and the name gate | `enrichment/tier3_llm.py:33–49`; imports `grounded_resolver.py:63–65`, `name_gate.py:39` |
| identity guard | for `name1`, `classify_name_change(...) == DIFFERENT` → dropped `identity_not_preserved` | `grounded_resolver.py:667–680` |
| confidence is not a write gate | `run_tier3` accepts every confidence band under `authoritative=True` (the default) and records the model's own confidence as `self_reported` provenance | `enrichment/tier3_llm.py:88`, `:130–136` |
| three grounded outcomes | registry hit → origin `ror`/`lei`, the **registry's** official name and identifier, **no flag** · no registry hit but evidence-backed → origin `serp`, flagged with a URL · no registry hit and no evidence → origin `llm`, flagged `unresolved` | `grounded_resolver.py:18–31` |
| fourth, non-writing outcome | the proposal reproduces the value the record already held and no registry improved on it → `confirmed`, **nothing is written**, because writing the same string back costs the record its `input:verified+web` attribution and buys nothing | `:243–262`, `:749–760` |
| no department signal | the input carried neither a department nor a contact → **every** department slot is cleared, whatever any tier produced | `enrichment/orchestrator.py:9207–9225` |
| preprocessing cleared it | a slot preprocessing emptied (contact, email, address, AP reference) may not be refilled by Tier 3 | `:9227–9258` |

Budgets: `MAX_FETCHES = 3` (`enrichment/grounded_resolver.py:83`), `NUM_RESULTS = 5` (`:88`). The
page **body** is deliberately withheld from the model — only URL host, URL path, title tag, H1 and
breadcrumb, plus the SERP title and snippet, are rendered (`:131–152`).

### 16.1.7 Website and department-URL acceptance

**Website, Path B (SERP).** Query from `_build_serp_query`
(`enrichment/website_resolver.py:831–873`): `base = f'"{name1}" official website'` (`:851`), then
`city state country` for a research institution, else `city state`, else `country`.

Eligibility, all four required (`_evaluate_candidates`, `:529–535`): the URL matches `_URL_RE`;
the host is not blacklisted; `_name_overlap(name1, sr)`; and `not _wrong_country(sr.url, country)`.

| Rank | Predicate | Lines |
|---|---|---|
| **2** | a distinctive/acronym host match with no foreign brand word, **or** a rank-0 institution candidate restored by locality evidence (`_geo_rescues_host_miss`) | `:539–546`, `:359` |
| **1** | a host match whose label adds a foreign brand (`_domain_introduces_foreign_brand`) | `:379` |
| **0** | the name overlaps the title only, not the host → the caller defers to Path C | `:552–555` |

`_sort_key` is a six-component total order (`:488–497`):
`(-rank, 0 if research_institution and tld ∈ {edu,gov,org} else 1, 0 if _geo_corroborated else 1,
_host_depth(url), SERP position, _candidate_key(sr))`. The docstring records why the last two exist:
an alphabetical-only tiebreak made `texaslonghorns.com` beat `utexas.edu` deterministically for
every University of Texas record in the batch.

Confidence: for an institution, `high` requires rank 2 **and** an official TLD **and** either the
institution's name phrase in the SERP title or its acronym in the host (`:792–802`); for a
company or unknown, `high` is rank 2 (`:803–804`). A rank reached on locality evidence rather than
on the host is never `high` (`:805–811`). **Path C (LLM) is always `low`** (`:1109`), and the
country gate applies on that path too.

**Department-URL probe.** `_probe_department_url` (`enrichment/orchestrator.py:4656–5082`). Nine
gates, all before any network call, in this order: `department_domain` already set; no
institution domain; no Name 2; Name 2 is a Tier 3 guess finalise will drop
(`_is_droppable_tier3_guess`); `is_admin_unit(name2)`; `identifies_nothing(name2, result)`; Name 2
is an address or pure location fragment; `is_granular_unit(name2)`; Name 2 names a legal entity
(`_names_a_legal_entity`: a trailing legal form — LLC, Inc, Co, Corp, Ltd, GmbH… — or a DBA
marker). Any organisation type: the probe looks up the web home of the unit Name 2 already
names, so what gates it is what Name 2 holds, not what kind of organisation Name 1 is.

Scoring, per candidate host (`_score_dept_candidate`, `:542–596`):

| Points | Predicate |
|---|---|
| **+3** | a significant token appears in `host_prefix` (host minus the institution base) |
| **+1** | a significant token appears in the URL path |
| **+1** | a significant token appears in the link text / title |
| **0 forced** | a generic admin host ("professorships", "inside", "news", …) |

Highest score `> 0` wins; ties go to the first encountered; no usable candidate leaves
`department_domain` null (`:4688–4691`). The probe clears exactly one flag and not the other:
`department_domain` answers *does this unit exist here*, which is what `unverified-inference` asks,
so it clears that code (`enrichment/flags.py:1170–1180`); it does not clear the derived
`low-confidence-unchanged`, because a matching host says the unit is real, not that the record
spells it the way the institution does.

### 16.1.8 Address-stage rules

`_run_address_stage` (`enrichment/orchestrator.py:7570–7619`) reads the **post-preprocess** street
values from `result["_pp_streets"]` rather than the raw originals, so a slot preprocessing emptied
stays empty, and swallows any exception — the name enrichment result must still surface.

`process_address` (`enrichment/address_processing.py:989–1290`), in order:

| # | Rule | file:line |
|---|---|---|
| 1 | clean + scrub every street slot — `_scrub_street` removes URLs, phones, emails and stray person references | `:143`, `:165`, `:1024–1031` |
| 2 | pull a street a later tier wrote into a name field; a bare campus/site label goes through `_site_fragment`; **the name field is rewritten only when every fragment found a home** | `:1033–1073` |
| 3 | elect the primary street (`_reduce_primary_street`) — which slot a line arrived in says nothing about what it is | `:895–987` |
| 4 | extract sub-locations: PO Box (`_PO_BOX_RE` `:225`), Suite/Building/Floor/Room/Unit (`_SUITE_PATTERNS` `:258`), Mail Stop (`_MAIL_STOP_RE` `:245`), c/o (`:389`), logistics (`:423`), mail code (`:458`) | `:329` |
| 5 | cross-field checks raising `G1-*` codes | `:706–744` |
| 6 | LLM residual classification on whatever remains in `street_2 … street_5` | `:796–858` |

Sub-location acceptance: `_is_identifier_like(token)` — the value must contain a digit **or** be
one to two characters (`:310–320`). Alphabetic words of three or more characters ("Annex",
"Penthouse") are rejected and left in the residual.

Residual step (`_apply_residual_llm`, `:796–858`), the only LLM call in the address stage:
`_looks_unambiguous(current)` is `_looks_like_street` — a house number **and** a street-type word —
and skips the call entirely; a `None` classification or a confidence below
`_RESIDUAL_CONFIDENCE_THRESHOLD = 0.85` (`:751`) raises `G1-ADDR-009`; `DEPARTMENT` raises
`G1-ADDR-011` and sets `department_addendum`. Vocabulary: `STREET_ADDRESS, DEPARTMENT, PERSON_NAME,
ORG_NAME, LOGISTICS, MAIL_CODE, UNCLEAR` (`llm/prompts.py:412–424`).

⚠ `G1-ADDR-009` is raised here but is **withdrawn** in `ISSUE_CATALOGUE` at this commit
(`enrichment/issue_detection.py:283–287`) and can never be emitted by `/issues`. The address stage
and the issue catalogue therefore disagree about whether the code exists; see §16.2's scope note.

`_looks_like_street(value)` is `_HOUSE_NUMBER_RE.search(value) and _has_street_type(value)`
(`enrichment/address_processing.py:633–640`), where `_has_street_type` accepts a standalone English
token ("Main **St**") **or** a German compound suffix ("Schelling**str**"), the latter guarded so
that English gerunds ending `-ring` are excluded by requiring a consonant before the suffix
(`:517–563`).

### 16.1.9 The origin invariant

`_slot_origin` is a per-slot map over the name block holding one of seven values
(`enrichment/dept_block.py:81–105`): `input`, `preprocess:split`, `preprocess:street`,
`preprocess:moved`, `registry`, `llm`, `grounded`. `RESOLVED_ORIGINS = {registry, llm, grounded}`
(`:96–99`) means an authority has already answered for the slot; `ORIGINS` (`:102–107`) exists to
reject a typo'd origin at the door rather than let it silently read as "not resolved".

| Rule | Statement | file:line |
|---|---|---|
| the invariant | "an origin may change only when the VALUE changes" — verbatim | `enrichment/orchestrator.py:1731–1732` |
| enforcement | the origin is recorded at one funnel, `_write`, rather than at each of the department block's 27 write sites, so a new lane that writes a department slot records its origin by construction | `:1711`, guard `:1750–1753` |
| origin travels with the value | never with the slot, so a packed or reordered block still says what produced each value | `enrichment/dept_block.py:77–79`, `:319` |
| the write lock | `SCOPED_FIELDS = ("name1_enriched","name2_enriched","domain","record_type","ror_id","lei_id")`; direct assignment raises `UnattributedWriteError`, and `setdefault` is refused whether or not the key is present | `enrichment/provenance.py:70–77`, `:1159–1173` |
| admissibility | a non-null scoped field carrying no provenance event is **reverted to its input value**, the record is flagged, and `inadmissible_value_reverted` is logged. The record is not failed | `:1227–1256` |
| grammar assertion | every provenance string the record will ship is validated at finalisation and an invalid one **raises**, because a provenance column that does not parse is worse than an empty one — a consumer reads it as an attribution | `enrichment/orchestrator.py:3278–3280`, reason `:3270–3274` |

⚠ `_slot_origin` is popped before serialisation (`enrichment/orchestrator.py:3287`). No response
field, workbook column or table carries it, so the invariant's effect is unauditable from any
output (`docs/thesis/08_GAPS.md` G-100).

### 16.1.10 The batch-consensus rule

`apply_batch_consensus` (`enrichment/batch_consensus.py:611`) runs once, after every record has
been finalised and before serialisation. It never merges, drops or deduplicates: the record count
in equals the record count out (`:19–22`).

**Grouping key**, two halves, both internal — never written to output, never sent to any API,
never placed in an LLM prompt (`:39–43`):

| Half | Rule | file:line |
|---|---|---|
| address block | `_address_block_id` reuses `dedup.signatures.derive_block_id`, the same function Phase 2 uses, so a batch and its later dedup pass cannot disagree about what "the same address" means. Neither street nor postal code → `None`, and the record joins no group | `:177–199` |
| name + legal form | `canon = _normalise_for_tokens(normalize_key(name))`; `base = strip_legal_suffix(canon)`; the legal form is returned **separately and never folded into the base** — folding would group "Delta Analytical Inc" with "Delta Analytical LLC" at a shared address | `:201–236` |

**Two keys per record**, the name it *ships* and the name it was *supplied* (`:252–262`). A record
joins a bucket when **either** of its keys matches **either** key of a member already there, and
bridged buckets are unioned rather than the record being assigned to one of them (`:275–303`).
Legal-form compatibility is **not transitive** — an absent form is compatible with every form
(`:235–238`) — so when a block holds two or more different legal forms under one base name, each
form gets its own group and every absent-form row is left in a singleton (`:310–327`).

**Two propagation modes** (`_consensus_values`, `:452–536`). Conflicting registry identities in one
group abort the group entirely (`:521–524`).

| Field | `registry` mode (group holds exactly one registry identity) | `name_form` mode (group holds none) |
|---|---|---|
| `ror_id` / `lei_id` | the single identity | absent by definition |
| `name1_enriched` | the donor's name, outright | the group's **modal** surface form |
| `domain` / `website_url` | the donor's, if it has one; else the group's single distinct domain | the group's single distinct domain only |
| `record_type` | the donor's, unless `"unknown"` | the group's single distinct non-`unknown` value |

`PROPAGATED_FIELDS` is exactly those six (`:80–93`). `NEVER_PROPAGATED` (`:95–115`) is stated as
data rather than implied by absence and includes every department slot, `department_domain`,
`contact_enriched`, `care_of_enriched`, `email_enriched`, `search_term_2` and every address field.

`name_form` mode is strictly weaker: it never chooses between competing values, it only fills gaps
where the group is already unanimous (`:508–513`). `name1_enriched` is the single exception, because
its competing values are surface variants of a name every member already holds (`:513–516`).
`_consensus_name_form` (`:400–450`) elects the modal form, breaking ties on closeness to the
**supplied** names (`_input_affinity`, `:384–398`), then tier, then batch order. `domain` and
`website_url` are taken from one record and never mixed (`:518–531`).

**Flags.** The pass raises none, and a record that inherits keeps every flag it earned (`:23–32`).
The one exception is `_RETRACTED_BY_NAME1` (`:116–143`): a propagated `name1_enriched` withdraws
`no-match` and `unverified-inference` under `registry` mode (`:140`) and **nothing** under
`name_form` mode (`:141`) — electing the batch's modal spelling introduces no evidence.

---

## 16.2 Issue detection rules

The live catalogue is the 33 codes below. `ISSUE_CATALOGUE` also declares 10 withdrawn entries that
are never emitted; they are the audit trail and are documented in
`docs/thesis/15_ISSUES_DOSSIER.md` §15.4, not here.

Two reading rules. **Group is the declared `group` attribute, never the code prefix** — of the live
codes every prefix happens to agree, but the attribute is what `issue_group()` returns and what
every consumer reads. **Remedy class** is `rule` (a deterministic rule can fix it), `enrichment`
(the pipeline can fix it) or `steward` (no automated path; a human decides); the 18 codes with
`remedy ∈ ("rule","enrichment")` are the reduction set (`api/routes.py:559–561`, enumerated at
`tests/test_issue_detection.py:182–190`, size asserted at `:218`).

**DQ dimension** is the one the predicate actually tests. `judgement` marks an assignment that is
not mechanically derivable from the predicate — the predicate tests a proxy, and naming the
dimension is a reading of what the proxy stands for.

Shared helpers used by the predicates below, defined once and reused:
`_names(record)` is Name 1…5 (`enrichment/issue_detection.py:963–965`); `_streets(record)` is
Street 1…5 (`:967–975`); `_norm(v)` is whitespace-collapsed, case-folded (`:977–980`);
`is_blank` is `utils.text_utils.is_blank`.

| Code | Group | Name | Predicate | Field(s) | Remedy | DQ dimension | Detection site |
|---|---|---|---|---|---|---|---|
| `G1-CROSS-001` | G1 | Address Content in Name Field | any name slot for which `_extract_addresses(nm)[0]` is non-empty **or** `_has_site_qualifier(nm)`. `_has_site_qualifier` is three shapes: the spaced-dash form `split_site_suffix` strips; the two-comma form `^.+?,\s*<city>\s*,\s*<region>$` with the region in `_REGION_SPELLINGS`; and a bare state+zip tail `\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b` | Name 1 | rule | placement | `:1054–1057`; helper `:624–652` |
| `G1-CROSS-002` | G1 | Org Name in Address Field | any street slot where `_ORG_IN_STREET_RE` matches after `_UNIVERSITY_CENTRE_RE` is substituted out, **and** `_STREET_TYPE_WORD_RE` does **not** match the raw slot | Street | enrichment | placement | `:1062–1071`; regexes `enrichment/issue_detection.py:823–831`, `enrichment/address_processing.py:517–521`, `:561–564` |
| `G1-CROSS-003` | G1 | Contact Information in Wrong Field | any name **or** street slot where `_is_contact_content(v)` — `_EMAIL_RE`, `_PHONE_RE`, `_URL_RE` or the unanchored c/o‑ATTN marker matches; failing that, any street slot for which `_street_person_name(st)` is not None | varies | rule | placement | `:1075–1083`; helper `:661–677` |
| `G1-ADDR-001` | G1 | House Number Embedded in Street | `is_blank(record.house_number)` **and** some street slot satisfies `_looks_like_street` (a bare digit run **and** a street-type word) | Street | rule | placement | `:1087–1091` |
| `G1-ADDR-003` | G1 | Sub-location Embedded in Street | any street slot matched by some `_SUITE_PATTERNS` entry (Campus Box, Mail Stop, Suite/Ste, Bldg/Building, Floor/Fl, `\d+F`, trailing ordinal, Room/Rm, `Lab <id>`, trailing room code, Unit, bare `#`) **or** by `_has_detection_only_sublocation` — `\b(?:Gate\|Wing)\s+(\w[\w\-]*)\b` with an identifier-like value | Street 2 | rule | placement | `:1094–1100`; table `enrichment/address_processing.py:258–290`; detection-only `enrichment/issue_detection.py:853–869` |
| `G1-ADDR-004` | G1 | PO Box Embedded in Street | any street slot matching `_PO_BOX_RE` = `\b(?:P\.?\s*O\.?\s*Box\|POB\|Post\s+Office\s+Box)\s+(\w+)\b` | Street | rule | placement | `:1103–1106`; regex `enrichment/address_processing.py:225–228` |
| `G1-ADDR-006` | G1 | Mail Code in Street Field | for each street slot in order: an explicit mail code (`_extract_mail_code(st, allow_bare=False)`) **or** `_has_mail_code(st)` fires on any slot; **plus**, for Street 2…5 only, the bare form `[A-Z]{2,4}\d{1,4}` when that value is not already claimed by a sub-location marker. Street 1 never fires on the bare form, because the pipeline extracts it only in street_2..5 | Street 2 | rule | placement | `:1124–1135`; `_MAIL_CODE_MARKER_RE` `:908–913`, `_has_mail_code` `:930–941` |
| `G1-ADDR-011` | G1 | Department Label in Street Field | any street slot where `_looks_like_department(st)` — `_DEPARTMENT_PAYLOAD_RE` matches | Street 2 | rule | placement | `:1138–1141`; helper `enrichment/address_processing.py:385–386` |
| `G1-NAME-004` | G1 | Empty field in between populated name fields | some index `i ∈ [1, 4)` where Name *i+1* is blank **and** at least one slot above it is populated **and** at least one slot below it is populated. Name 1 blank with Name 2 populated is a missing organisation name (`G2-VAL-001`), not a gap | Name 2 | rule | placement | `:1150–1158` |
| `G1-NAME-013` | G1 | SAP Internal Code in Name Field | any name slot where `_is_opaque_code(nm)`: `^\s*[A-Za-z]{0,4}[-]?\d{5,}\s*$` on the raw value, or on the value with a leading label (`ref`, `no`, `nbr`, `num`, `acct`, `account`, `id`, `code`, `po` + `[#:.\-]*`) removed. Also raised by the flag `opaque-code` | Name 2 | steward | placement | `:1161–1164`; helper `enrichment/preprocess.py:714–726`; flag join `:1527` |
| `G2-VAL-001` | G2 | Name 1 Missing | `"name_1"` is in the file's `present_fields` **and** `is_blank(record.name_1)` | Name 1 | steward | completeness | `:1189–1215` ← `_REQUIRED_FIELD_CODES` `:497` |
| `G2-VAL-002` | G2 | Postal Code Missing | `"postal_code"` present in the file **and** blank | Postal Code | steward | completeness | `:1189–1215` ← `:498` |
| `G2-VAL-004` | G2 | Region Missing | `"region"` present in the file **and** blank | Region | steward | completeness | `:1189–1215` ← `:499` |
| `G2-VAL-007` | G2 | Search Term 1 Missing | `"search_term_1"` present in the file **and** blank | Search Term 1 | rule | completeness | `:1189–1215` ← `:500` |
| `G2-VAL-008` | G2 | Country Missing | `"country_region_key"` present in the file **and** blank | Country | rule | completeness | `:1189–1215` ← `:501` |
| `G2-NAME-009` | G2 | Lab Without Department | `looks_like_university_or_research_institute(Name 1)` **and** some slot in Name 2…5 for which `is_granular_unit(v)` **and** no *other* slot in Name 2…5 satisfies `is_specific_unit_construction` or `is_unit_construction` | Name 2 | enrichment | completeness | `:1258–1267` |
| `G2-NAME-012` | G2 | Research Institution Missing Department | `looks_like_university_or_research_institute(record.name_1)` **and** `classify(record.name_2 or "") ∈ ("empty","admin")`. `granular` and `identifies_nothing` deliberately do **not** fire | Name 2 | steward | completeness | `:1250–1254` |
| `G3-NAME-003` | G3 | DBA Pattern in Name Field | any name slot for which `_normalise_dba(nm)[1]` is true — one of five variants matched: `doing business as`, `d[.]? business as`, `d/b/a`, `d.b.a.`, `d b a` | Name 1 | rule | uniqueness · **judgement** (the predicate tests a marker; the defect the catalogue names is a legal name and a trading name occupying one field) | `:1287–1290`; patterns `enrichment/preprocess.py:626–639` |
| `G3-NAME-005` | G3 | Duplicate Name Across Fields | some pair in `ADJACENT_RECORD_NAME_PAIRS` where `_norm(upper)` is non-empty and equals `_norm(lower)` | Name 2 | rule | uniqueness | `:1294–1298` |
| `G3-NAME-006` | G3 | Site Qualifier in Name Conflicts With Address | the record's `Flag Codes` column contains `name-states-another-site`. **Flag path only** — no content detector; can never fire on a raw file | Name 1 | steward | consistency · **judgement** | `FLAG_CODE_ISSUES` `:1530` |
| `G3-ADDR-005` | G3 | Multiple PO Boxes on Record | `po_box_count ≥ 2`, where the count is the number of street slots matching `_PO_BOX_RE` plus one if the dedicated `PO Box` field is non-blank | PO Box | steward | uniqueness | `:1301–1309` |
| `G3-ADDR-012` | G3 | Duplicate Street Across Fields | the list of non-null `_street_signature` values over the five street slots contains a repeat. Street 1's signature is computed with the House Number folded in; Streets 2–5 without, since SAP pairs House Number with Street 1 only | Street | rule | uniqueness | `:1317–1325`; `_street_signature` `:1003–1035` |
| `G3-ADDR-013` | G3 | Two Distinct Street Addresses on Record | at least two **distinct** `_street_signature` values among the slots that `_looks_like_street` accepts, Street 1 tested with its House Number folded back in. Distinctness is `-012`'s own helper called on the raw slot, which is what keeps the two codes mutually exclusive | Street | steward | consistency | `:1341–1351` |
| `G3-ADDR-014` | G3 | PO Box and Street Both Present | `po_box_count ≥ 1` **and** some street slot satisfies `_looks_like_street` | PO Box | steward | consistency · **judgement** (co-presence is legal in SAP; the catalogue reads it as a conflicting delivery instruction) | `:1354–1355` |
| `G3-CONTACT-007` | G3 | Multiple Contacts on Record | `has_multiple_contacts(record.contact)`: a strong separator (`and`, `or`, `&`, `;`, `/`, ` + `) is present, **or** the field splits on commas into ≥ 2 parts of ≥ 2 tokens each — comma alone is ambiguous because `Last, First` is one person. Also raised by the flag `multiple-contacts` | Name 2 | steward | uniqueness | `:1358–1359`; helper `enrichment/preprocess.py:1711–1733`; flag join `:1528` |
| `G3-CONTACT-010` | G3 | Multiple Email Addresses on Record | the case-folded set of `_EMAIL_RE` matches pooled over `Email`, all five name slots and all five street slots has size ≥ 2. Deliberately independent of `-007`. Also raised by the flag `email-conflict` | Email | steward | uniqueness | `:1372–1379`; flag join `:1529` |
| `G4-NAME-015` | G4 | Name Overflow Beyond the Name Block | `sum(len(nm) for nm in _names(record) if nm) > _SAP_NAME_LIMIT`, `_SAP_NAME_LIMIT = 140`. Also raised by the flag `overflow` | Name 4 | steward | validity | `:1388–1390`; constant `:465`; flag join `:1554` |
| `G4-ADDR-026` | G4 | Postal Code Format Invalid | `Postal Code` non-blank **and** `country_to_iso_code(country_region_key)` is in `_POSTAL_FORMATS` **and** the stripped code fails that pattern. Coverage is **US** `^\d{5}(?:-\d{4})?$`, **CA** `^[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d$`, **DE** `^\d{5}$` and nothing else — a code from any other country is *unchecked*, not valid | Postal Code | steward | validity | `:1393–1397`; table `:953–961` |
| `G4-ADDR-027` | G4 | Country Code Not ISO 2-letter | `Country` non-blank **and** (`country_to_iso_code(raw)` is None **or** `raw.upper() != iso`) | Country | rule | validity | `:1400–1404` |
| `G5-NAME-001` | G5 | Organisation Name Not in Official Form | `"name_1"` not in `_overflow_slots(record)` **and** `_is_non_canonical_name(name_1, _NONCANON_TOKENS_ORG)` — an abbreviation token from the 34-word lexicon minus `{inst}`, with `\b` anchors and an optional trailing period, **or** a dotted acronym `\b[A-Za-z](?:\.[A-Za-z]){1,}\.?(?![A-Za-z])` | Name 1 | enrichment | accuracy · **judgement** (the predicate tests abbreviation marks, a proxy for "not the official spelling") | `:1434–1437`; lexicon `:759–781` |
| `G5-NAME-002` | G5 | Unit Name Not in Official Form | some slot in Name 2…5 not in `_overflow_slots(record)` where `_is_non_canonical_name(nm, _NONCANON_TOKENS_UNIT)` — the same lexicon minus `{dept, div, inst}`, which are accepted SAP unit forms | Name 2-4 | enrichment | accuracy · **judgement** | `:1442–1447` |
| `G6-CONFIRM-001` | G6 | Enriched Value Requires Confirmation | the record's `Flag Codes` contains any of `domain-unverified`, `unverified-inference`, `dept-via-lab`, `dept-via-contact`, `relocated-unverified`. **Flag path only** | Flag Codes | steward | accuracy · **judgement** (five flags about evidence strength collapse onto one queue) | `:1535–1539` |
| `G7-UNCHANGED-001` | G7 | Enrichment Left the Value Unestablished | the record's `Flag Codes` contains any of `low-confidence-unchanged`, `no-match`, `person-unresolved`. **Flag path only** | Flag Codes | steward | completeness · **judgement** | `:1556–1558` |

**Emission order and set semantics.** `detect_issues` (`:1654–1710`) runs the five content
detectors then the flag detector in fixed order (`:1700–1705`), accumulates into a `set`, and
returns codes in catalogue order (`:1710`). A record reaching one code by both a content path and a
flag path reports it once. Detection is pure and deterministic — regex and string checks only, no
enrichment, no LLM call, no network I/O (`:9–12`) — which is what lets the same rule set run over a
raw input file and a post-pipeline output file.

**Column gating on the `G2-VAL-*` family.** The five required-field rules fire only when the column
exists in the file **and** is blank. When the column is absent from the file the rule is skipped, so
an enriched export that simply does not carry Postal Code is not reported as missing it
(`:467–473`). `present_fields` is built by `api.routes._present_fields` (`api/routes.py:162–176`)
from the file's header row. `_REQUIRED_FIELD_CODES` carries an optional third element, a per-record
predicate; **no entry carries one at this commit** (`:474–477`), and
`_validate_required_field_mapping` (`:525–554`) warns at import when a rule is keyed on a field that
is neither on `EnrichmentRecord` nor carries an input alias — the failure mode in which a rule can
never fire and reads as a clean run.

**Severity.** Seven live codes carry `mandatory=True` and block the SAP load (DATAshaper *Error*):
`G2-VAL-001`, `G2-VAL-002`, `G2-VAL-004`, `G2-VAL-007`, `G2-VAL-008`, `G4-ADDR-027`,
`G4-NAME-015`. Six of the seven are DS-origin. Everything else is a *Warning*
(`IssueDefinition.severity` `:243–246`).

**Reduction set — 18 codes.** `remedy ∈ ("rule","enrichment")`: `G1-CROSS-001`, `G1-CROSS-002`,
`G1-CROSS-003`, `G1-ADDR-001`, `G1-ADDR-003`, `G1-ADDR-004`, `G1-ADDR-006`, `G1-ADDR-011`,
`G1-NAME-004`, `G2-VAL-007`, `G2-VAL-008`, `G2-NAME-009`, `G3-NAME-003`, `G3-NAME-005`,
`G3-ADDR-012`, `G4-ADDR-027`, `G5-NAME-001`, `G5-NAME-002`.

**Dimension totals over the 33 live codes** (counting the primary dimension only; `judgement`
marks the eight assignments that are not mechanical):

| Dimension | Codes | Count |
|---|---|---|
| placement | the ten G1 codes | 10 |
| completeness | `G2-VAL-001/002/004/007/008`, `G2-NAME-009`, `G2-NAME-012`, `G7-UNCHANGED-001` | 8 |
| uniqueness | `G3-NAME-003`, `G3-NAME-005`, `G3-ADDR-005`, `G3-ADDR-012`, `G3-CONTACT-007`, `G3-CONTACT-010` | 6 |
| consistency | `G3-NAME-006`, `G3-ADDR-013`, `G3-ADDR-014` | 3 |
| validity | `G4-NAME-015`, `G4-ADDR-026`, `G4-ADDR-027` | 3 |
| accuracy | `G5-NAME-001`, `G5-NAME-002`, `G6-CONFIRM-001` | 3 |

The mapping is one-to-one onto codes and does not partition the groups: G3 splits across
uniqueness and consistency, and G2's `G7-UNCHANGED-001` neighbour sits in completeness while its
own group is a verification group. Group and dimension answer different questions and Pass 17 draws
both edges.

---

## 16.3 Deduplication rules

Three environment flags, all default-false, read on every call rather than captured at import
(`dedup/flags.py:36–37`): `DEDUP_V2_BLOCKING` (`:31`), `DEDUP_V2_NAME2` (`:32`),
`DEDUP_V2_ID_CONFLICT` (`:33`). Truthiness is exact —
`_TRUTHY = frozenset({"1","true","yes","on"})` (`:29`), lowercased and stripped; anything else,
including `"0"`, `"no"`, `"off"`, the empty string and an unset variable, is off, so a typo fails
safe to v1. `v2_any()` (`:55–63`) gates the `Link ID` column and the request-level linking pass.

### 16.3.1 Blocking key

`parse_address` (`dedup/address.py:160–198`) reduces a row to a `ParsedAddress` of `country`,
`zip5`, `house`, `street_core`, `city_norm`, `house_hint`.

| Component | Rule | file:line |
|---|---|---|
| `country` | stripped, upper-cased | `:162` |
| `zip5` | first five digits when `country.upper() ∈ ("US","USA")` and ≥ 5 digits are present; otherwise `normalize_key` of the raw value | `:123–136` |
| `house` | `house_no` with everything but `[0-9a-z]` removed — `45A`→`45a`, `47-111`→`47111` | `:118–120` |
| `house` recovery | when `house_no` is empty, the first or last street token matching `^\d+[a-z]?$` — **only if** a street core remains beside it | `:170–189` |
| `house_hint` | the recovered number when no street remains (a street line of `38`); shown, never used as a house | `:108–110`, `:186–189` |
| `street_core` | tokens canonicalised through `STREET_SUFFIXES`, stopping at the first `STREET_TYPES` word and dropping what follows. `DIRECTIONALS` maps 9 compass spellings but deliberately never terminates the core, or terminating on the leading `E` of `E 11 Mile Rd` would leave nothing at all | `:139–157`, `:54–76` |

`block_keys` (`dedup/address.py:205–222`) emits, per row:

| Condition | Keys |
|---|---|
| `house_less` (no usable house) | `f:{country}\|{zip5}` — the fallback key, only |
| house present | `z:{country}\|{zip5}\|{house}` |
| house present **and** `zip5` **and** `city_norm` | additionally `c:{country}\|{city_norm}\|{house}` |
| caller supplied `block_id` | overrides all of it: the single key `g:{block_id}` (`dedup/signatures.py:224–228`) |

`_v2_blocks` (`dedup/signatures.py:193–257`) unions each row's keys with iterative union-find and
path compression, takes each connected component as a block, and names it `blk-` + the first 12 hex
of sha1 over the component's root key (`:248–250`). Two determinism guarantees are load-bearing:
the union is driven off `keys[0]` per row and roots are chosen by string order (`:217–221`), and
each block's rows are sorted by `row_id` (`:252–254`), so signature ids, bucket order and every LLM
call sequence are independent of the input's row order.

**Unverified block.** A component whose root key starts with `f:` holds only house-less rows and is
marked `unverified` (`:242–245`). The key spaces cannot mix, so this is a property of the component
rather than a vote among its members. Any cluster such rows form is demoted to `manual_review`
(`dedup/adjudicator.py:1225–1228`).

**Pairwise compatibility.** `address_compatible` (`dedup/address.py:276–304`) returns
`exact` / `fuzzy` / `partial` / `incompatible`; only `incompatible` is load-bearing and it means
"do not even ask the model about this pair". It fires when:

| Test | Threshold | file:line |
|---|---|---|
| both houses present and different | — | `:283–284` |
| both zips present, different, and Damerau-Levenshtein distance `> ZIP_EDIT_TOLERANCE` | `ZIP_EDIT_TOLERANCE = 1` (`:91`) | `:285–291` |
| both street cores present and `streets_compatible` is false | — | `:292–293` |

`streets_compatible` (`:233–273`) is a numeric veto plus two positive tests: identical numeric token
sets are required first, because "11 mile road" against "13 mile road" is one character, which
Jaro-Winkler reads at 0.94; then Jaro-Winkler `≥ STREET_NAME_THRESHOLD = 0.85` (`:87`), **or** a
token rule requiring `1 if len(shorter) == 1 else max(2, ceil(len(shorter)/2))` matches (`:264–273`).

`street_match` (`:307–321`) is a deliberately different vocabulary shown to the model —
`exact` / `fuzzy` / `differs` / `unknown` — because telling a model `incompatible` about a pair it
is being asked to judge invites it to reject on an address question that has already been decided.

### 16.3.2 Signature construction

A signature is a distinct `(norm_name1, norm_name2)` within a block (`dedup/signatures.py:79–85`,
key built at `:322–326`). It is the blow-up guard: 100 byte-identical rows collapse to one
signature, and the LLM only ever works on distinct signatures, never on raw rows (`:3–5`).

`normalize_key` (`:35–48`) folds NFKD accents, lower-cases, replaces every non-word character with
a space and collapses whitespace. It deliberately does **not** strip legal forms or expand
abbreviations — that is the LLM's job; the key is a conservative collapse only (`:28–30`). The
normalised key is internal; the model always sees the un-normalised names (`:12–13`).

Under v1, `department_text` (`:65–76`) joins every populated slot below Name 1 with `" / "`. Under
v2, `_resolve_slots` (`:275–299`) routes to `classify_slots` instead and the signature's `name1` /
`name2` hold the *classified* institution and department (`:90–94`). A v2 signature accumulates
aliases, hints and the five Phase 1 columns across every row behind it (`:357–375`), because two
records that collapsed to the same institution may each carry a different other-name for it.

Signature ids are `s1`, `s2`, … assigned after order is known (`:377–379`) and are **block-local** —
they restart in every block, which is why the linker keys on position rather than on signature id.

### 16.3.3 Strip lists and slot-classification rules

| Vocabulary | Contents | Used by | file:line |
|---|---|---|---|
| `LOGISTICS_TERMS` | 14 terms: `receiving, shipping, warehouse, distribution, central supply, stores, dock, purchasing, procurement, accounts payable, a/p, invoicing, billing, mailroom, materials management` | the `logistics` slot rule | `dedup/name_slots.py:67–71` |
| `_CONTINUATION_NOUNS` | 26 generic organisational nouns that continue a name but cannot start one: `institute(s), center(s), centre(s), laboratory, laboratories, labs, lab, university, college, hospital, foundation, association, society, trust, holdings, industries, technologies, systems, solutions, group, company, corporation, partners, ventures, international, worldwide, works, research` | the `overflow` rule | `:91–100` |
| `_DANGLING_CONNECTORS` | `{for, of, and, the, &, at, in, de}` | the `overflow` rule's third arm | `:104` |
| `CORPORATE_STRUCTURE_WORDS` | 16 words: `company, holdings, holding, group, corporation, corp, inc, co, llc, ltd, limited, incorporated, plc, gmbh, ag, sa` | `suffix_only` evidence, `_strip_corporate_structure` | `dedup/candidates.py:268–271` |
| `_TRADING_AS_RE` / `_A_COMPANY_RE` | `\btrading\s+as\b` · `^an?\s+.+\s+company$` | the `alias` rule | `dedup/name_slots.py:80–81` |
| `_OPAQUE_NAME1_RE` | `^[A-Z0-9]{3,6}(?: LLC\| Inc\.?)?$` | the `institution` rule | `:85` |
| `_TRAILING_SITE_RE` | `\s*(?:[-–—]\s*[^-–—()]+\|\([^()]+\))\s*$` | the `alias` rule's fourth arm | `:88` |

The Phase 1 detectors are imported rather than re-implemented, because two answers to any of those
questions is one answer too many (`:30–35`, imports `:50–55`): `has_no_canonical_form`
(`enrichment/search_terms.py:678`), `_normalise_dba` / `_DBA_PATTERNS`
(`enrichment/preprocess.py:642` / `:626–639`), `_CO_ATTN_PREFIX_RE` (`:1171`) and
`_person_candidate` (`:1680`).

**Slot classes.** `SlotKind` is eight values (`dedup/name_slots.py:57–60`), asked in a fixed order:

| Order | Kind | Predicate | Threshold | file:line |
|---|---|---|---|---|
| 1 | `institution` | Name 1 matches `_OPAQUE_NAME1_RE` **and** the best Jaro-Winkler of Name 2 against another Name 1 in the block is `≥ INSTITUTION_THRESHOLD` | `0.85` (`:112`) | `:325–330` |
| 2 | `institution_split` | a rebuild (`name1 name2` or `name2 name1`, suffixes stripped) is JW `≥ INSTITUTION_SPLIT_THRESHOLD` to another Name 1 in the block, **or** neither slot introduces a token that other name lacks | `0.92` (`:118`) | `:254–322` |
| 3 | `overflow` | Name 1 does **not** end in a legal suffix, **and** (`name1 name2` is JW `≥ OVERFLOW_THRESHOLD` to another Name 1 in the block, **or** Name 2 is only continuation nouns, **or** Name 1's last token is in `_DANGLING_CONNECTORS`) | `0.92` (`:109`) | `:231–247` |
| 4a | `alias` | `_normalise_dba` reports a change, **or** `_TRADING_AS_RE`, **or** `_A_COMPANY_RE`, **or** Name 2 minus a trailing site is JW `≥ 0.85` to Name 1 | `0.85` | `:333–342` |
| 4b | `logistics` | a `LOGISTICS_TERMS` word present as a whole token, **or** `has_no_canonical_form(value)` | — | `:184–185` |
| 4c | `contact` | a c/o or ATTN prefix, **or** a two-word head before a **separator** that `_person_candidate` accepts and that shares no token with Name 1 | — | `:154–181` |
| 4d | `department` | none of the above | — | `:452–459` |
| — | `none` | no populated slot below Name 1, or every slot was consumed by a rule above | — | `:375–376`, `:443–448` |

The separator in 4c is required as evidence, and the reason is an explicit asymmetric-error
argument: "Fairchild Science" has the shape of a first and last name and is a Stanford building's
department; mistaking a department for a person destroys a real distinction, while mistaking a
person for a department only fails to merge (`:161–168`).

The returned institution in rules 1–3 is **selected from the block, never composed** (`:284–289`) —
concatenating two slots would invent a third spelling that no record states and no registry holds.
An `institution_split` files both original slot values as **hints**, not aliases, because filed as
an alias one of them said "this institute is also called EMD Serono, Inc.", the `cross_slot` rule
matched it, and the company was swallowed by its own research arm (`:400–404`). Hints are shown to
the model and never matched on.

Slots below the first are classified on their own terms, so a delivery desk in Name 3 is dropped
from the department just as one in Name 2 is (`:368–369`, loop `:424–439`). The reported `kind`
describes the **outcome**: any surviving department text makes this a departmental record, whatever
the first slot happened to be (`:441–448`).

⚠ The code has no class named *ancestor* and no rule that says "this names a parent organisation".
The `A <something> Company` form a subsidiary uses to state its parent is filed as an **alias**
(`:81`, routed `:334`), an alias is defined as another name for the *same* institution, and aliases
**are** matched on by the `cross_slot` nomination and evidence rules
(`dedup/name_slots.py:126–129`, `dedup/candidates.py:192–213`). A parent's name stated in Name 2 is
therefore available as a bridge from a subsidiary to its parent. The mechanism is present and
unguarded.

### 16.3.4 The Name 2 asymmetry rule

`has_name2` is `bool(self.norm_name2)` (`dedup/signatures.py:142–155`) — under v2 a statement about
the department the classifier found, not about whether a cell below Name 1 was populated. The rule:
**a signature with no department can never share an entity with one that has any.** It is enforced
in three places:

| Site | Enforcement | file:line |
|---|---|---|
| Mode A | the block's signatures are split by `has_name2` into two buckets *before any call*, so the empty-vs-populated decision is never sent to the LLM | `dedup/adjudicator.py:451–463` |
| Mode B | only canonicals whose `has_name2` matches the candidate are presented; an incompatible candidate starts a new entity with **no LLM call at all** | `:608–614` |
| post-LLM | `_enforce_name2_split` — an entity holding both populated- and empty-`has_name2` signatures is split, the empty ones forming an institution-level entity | `:139–171`, applied `:578` |

### 16.3.5 Mode A / Mode B selection

| n distinct signatures in the block | Mode | LLM calls | file:line |
|---|---|---|---|
| `n ≤ 1` | A (degenerate) | 0 — identical rows still cluster | `:1343–1346` |
| `2 ≤ n ≤ threshold` | A — one partition call per non-singleton `has_name2` bucket | ≤ 2 | `:1347–1349`, `:444–579` |
| `n > threshold` | B — incremental canonical assignment | O(n) | `:1350–1352`, `:586–720` |

`threshold` is `SIG_PARTITION_THRESHOLD`, default `DEFAULT_SIG_PARTITION_THRESHOLD = 12`
(`dedup/adjudicator.py:39`, resolved `:1465–1466`).

Mode A acceptance: a singleton bucket becomes an entity with no call (`:468–472`); every signature
id must appear exactly once across `entities[].signature_ids` or `uncertain_signature_ids`
(`dedup/prompts.py:186–189`), and a signature the model drops is **forced to `uncertain`** so it
surfaces for review rather than vanishing (`:563–572`); an unparseable response marks the whole
bucket uncertain and never fails the block (`:492–508`).

Mode B acceptance: `match` to an unknown or incompatible `entity_id` is treated as `new`, with a
warning (`:682–693`); anything other than `match` / `new` — including `uncertain` — becomes its own
flagged entity (`:704–718`). Mode B's calls are bounded at `max_tokens=1000` (`:642`); Mode A uses
the client default of 4000 (`dedup/llm.py:195`).

### 16.3.6 Residue nomination

Mode A and Mode B adjudicate every pair *within* a `has_name2` bucket. What they never compare are
the pairs the asymmetry rule keeps apart and a signature alone in its bucket; those bypass the LLM
entirely and default to `unique` with no reasoning (`dedup/candidates.py:1–13`). The residue pass
nominates such pairs and adjudicates each with one pairwise call
(`dedup/adjudicator.py:814–991`). **Nomination never merges; the LLM verdict decides** (`:10–12`).

**Eligibility** (`dedup/candidates.py:346–369`): a pair is skipped when the v2 address gate says its
delivery points are `incompatible` (`:365–366`), and when both units share a `has_name2` value *and*
both already went through the LLM (`:367–368`).

| Rank | Rule | Predicate | Threshold | file:line |
|---|---|---|---|---|
| 0 | `id` | equal non-empty `lei_id`, **or** equal non-empty `ror_id` | — | `:138–142`, `:238–239` |
| 1 | `name` | Jaro-Winkler over suffix-stripped institution names | `name_threshold`, default `0.85` | `:241–244` |
| 2 | `acronym` | either side's initials (stopwords dropped) against the other, short, name | `ACRONYM_THRESHOLD = 0.8` (`:159`); short side length 3–6 (`ACRONYM_MIN_LEN = 3` `:158`, `ACRONYM_MAX_LEN = 6` `:151`) | `:147–189`, `:246–249` |
| 3 | `cross_slot` | best Jaro-Winkler of one side's aliases / `operating_name` / `suggested_name` against the other's institution | `CROSS_SLOT_THRESHOLD = 0.85` (`:160`) | `:192–213`, `:250–252` |
| 4 | `token` | Jaccard over suffix-stripped token sets | `token_threshold`, default `0.6` | `:82–87`, `:254–256` |

Ranks 2 and 3 are `extra_rules`, enabled only when `v2_name2()` **and** the block was large enough
for Mode B (`dedup/adjudicator.py:1359–1361`): a small block's signatures are already compared in
one Mode A partition call, so nominating them again would buy a second opinion on a question
already asked, at one LLM call each (`dedup/candidates.py:230–234`).

`ACRONYM_MIN_LEN = 3` exists for one trap: two characters is not an initialism at all, it is a
coincidence — `HP` matches the initials of every two-word name beginning H, P, including
`Hewlett Packard Enterprise`, a different company at the same address (`:153–158`).

**Cap.** `generate_candidate_pairs` sorts by `Candidate.sort_key` — rule rank, then descending
score, then `(a, b)` (`:126–135`) — and the caller applies the cap against that ordered list, so
id-convergence pairs survive it (`:382–385`). Exceeding `max_candidates`, default
`DEFAULT_MAX_CANDIDATES_PER_BLOCK = 50` (`dedup/adjudicator.py:43`), routes the **whole block** to
`manual_review` with the marker `candidate_cap_exceeded: …` (`:847–863`).

**Deterministic evidence.** `pair_evidence` (`dedup/candidates.py:308–343`) is computed for every
pair in a prompt, ungated by block size, and rendered as an `evidence:` block
(`dedup/prompts.py:144–163`). It emits `id`, `acronym`, `suffix_only`, `name_variant`,
`cross_slot`. `suffix_only` strips trailing `CORPORATE_STRUCTURE_WORDS` and fires when the
remainders are equal but the raw names are not (`:331–335`). `name_variant` accepts Jaro-Winkler
`≥ 0.85`, **or** token containment with at most `NAME_VARIANT_MAX_EXTRA_TOKENS = 1` extra token
(`:279`, `:294–305`) — one extra word is a variant, four is a different organisation. Pairs with no
rules are not printed, because an absent line means the deterministic rules had nothing to say, not
that the two records differ (`dedup/prompts.py:148–152`).

### 16.3.7 LLM adjudication acceptance rule

Residue verdict application (`dedup/adjudicator.py:925–958`):

| Parsed decision | Effect | file:line |
|---|---|---|
| unparseable / `None` | both sides' signatures marked `uncertain` → `manual_review` | `:925–932` |
| `match` | `union(a, b)` under union-find whose lowest index stays root; the note records the nominating rule and the confidence | `:939–946` |
| `new` or `distinct` | a rationale is recorded on **both** sides and `rejected_with_reasoning` is incremented; nothing merges | `:946–950` |
| anything else, `uncertain` included | both sides marked `uncertain`, reasoning recorded | `:952–958` |

A pair already merged transitively is not re-asked (`:886–887`). The nominating rule is written into
the reasoning so a reviewer can tell an id convergence from a guess at an acronym — the two deserve
very different amounts of trust (`:918–920`).

### 16.3.8 Identity hard rules and the deterministic guard order

`_process_block` applies the guards in a fixed order and the order is documented as load-bearing
(`dedup/adjudicator.py:1354–1393`).

| # | Guard | Condition | Outcome | file:line |
|---|---|---|---|---|
| 0 | Name 2 asymmetry | an entity holds both populated- and empty-`has_name2` signatures | split; the empty ones form an institution-level entity | `:139–171`, applied `:578` |
| 1 | address split | an entity's non-first signature is `incompatible` with **every** row of the first | split off, `uncertain`, `manual_review` | `:367–423`, applied `:1367–1370` |
| 2 | identity split / conflict | **two different non-empty ROR ids, or two different non-empty LEI ids, in one entity** | v1: split to singletons, each flagged; v2 (`DEDUP_V2_ID_CONFLICT`): the cluster id stands, every signature is marked `uncertain`, reasoning reads `id conflict: ROR <a> vs <b>` in signature order | `:188–247`, `:309–349`, applied `:1375` |
| 3 | reasoning disowns membership | a merged entity's reasoning contains one of seven non-merge markers — `should not be merged`, `should not merge`, `must not be merged`, `not be merged`, `do not merge`, `should be split`, `must be split` | the **whole block** to `manual_review` | `:356–364`, `:426–437`, applied `:1379–1386` |
| 4 | institution conflict | deterministic evidence or a shared registry id vs a model verdict of `different` | the disagreeing signature to `manual_review`; **the link stands** | `:1172–1182`, applied `:1391–1393` |

Guard 1 splits the members incompatible with the entity's **first** signature, so the outcome does
not depend on which member the LLM happened to name first (`:379–381`). Guard 3's markers are read
"ONLY to demote toward manual_review — never to merge" (`:352–355`).

ROR/LEI is only ever a **split** signal at guard 2, never a merge trigger (`:194–196`). Under v2 the
ordering of the two conflicting ids is defended twice: `_ordered_distinct` preserves
first-appearance order rather than iterating a set (`:250–262`), and `_signature_order` re-sorts by
numeric signature id because an entity's signature list is in the order the model happened to write
the ids, which is not a property of the data (`:265–274`). `_inferred_from_short_name` (`:282–306`)
appends a cause when provenance says the id was not registry-verified **and** the name it came from
is one or two tokens — a one- or two-token Name 1 (`Scripps`, `Takeda`) is a brand, not an
organisation. It is silent when provenance says verified and silent when provenance says nothing,
because an absent provenance is not evidence of inference.

### 16.3.9 Emission and the `Link ID` rule

`_emit_rows` (`dedup/adjudicator.py:1187–1283`) writes one output row per input row.

| Field | Rule | file:line |
|---|---|---|
| `cluster_id` | `c_` + first 12 hex of sha256 over the sorted member `row_id`s, when the entity holds ≥ 2 rows; else null | `dedup/cluster_key.py:17–24`, applied `:1211–1217` |
| `link_id` | set by the request-level pass; null when linked to nothing | `:1273`, `:1513–1525` |
| `routing` | `manual_review` if the signature is uncertain; else `manual_review` if clustered in an unverified block; else `cluster` if clustered; else `unique` | `:1221–1234` |
| `llm_flag` | `entity.llm_merged` — membership came from ≥ 2 distinct signatures | `:1275`, `:92–96` |
| `confidence` | only for a genuine merge (≥ 2 signatures) or an uncertain row; **null** for a pure identical-collapse and for a distinct verdict, where a spurious confidence would wrongly trip the election confidence gate | `:1249–1260` |
| `reasoning` | surfaced for any entity the LLM decided — merged, rejected or uncertain. An empty Reasoning therefore means exactly "never nominated" | `:1236–1248` |

An unverified-block cluster has its reasoning prefixed with
`UNVERIFIED_DELIVERY_POINT = "unverified delivery point"` (`:999`, applied `:1262–1267`). A
singleton in an unverified block is left alone: there is no claim in it to qualify (`:1205–1206`).

**`Link ID`.** `l_` + first 12 hex of sha256 over the sorted member `row_id`s
(`dedup/cluster_key.py:27–39`) — the same shape as `Cluster ID` and deliberately a different
prefix, so a reader glancing at a cell can tell a "same record" id from a "same organisation" id
without consulting a legend. The two are computed **independently**: deriving the link from the
merge outcome would make it say nothing the `Cluster ID` does not already say, and the cases worth
linking are exactly the ones that did **not** merge (`dedup/adjudicator.py:1033–1036`).

`_institution_links(entities)` (`:1026–1117`) runs union-find over signature **positions**, never
over signature ids, which restart at `s1` in every block. A pair is unioned when either arm holds
(`:1086–1098`):

| Arm | Condition | file:line |
|---|---|---|
| registry | equal non-empty LEI, **or** equal non-empty ROR (`_ids_converge_pair`) | `:1165–1169` |
| evidence | `pair_evidence(left, right)` is non-empty | `:1091` |

The model's `institution_relation` is then read **only to decide routing, not whether to link**:
`same` links, routing unchanged; `uncertain` links, routing unchanged; `different` links **and**
routes the disagreeing signature to `manual_review`; `None` (never asked) links, routing unchanged
(`:1095–1103`). An absent or malformed value leaves the relation `None`, which the linker reads as
"never asked" rather than "different", because a field the model forgot must not become a silent
assertion that two organisations are unrelated (`:1005–1023`); accepted values are exactly
`("same","different","uncertain")` (`:1002`).

**Cross-block linking** restricts the pair test to the **registry arm alone**
(`:1087–1090`): the model only ever compares within a block, so across blocks a shared ROR or LEI is
the only evidence there is, and the within-block links then join those families transitively
(`:1055–1058`). `_merge_link_maps` (`:1120–1162`) unions the within-block map with the across-block
map — neither overrides the other — and re-hashes each resulting family. Conflicts, by contrast,
**are** block-local: a conflict is a disagreement about a comparison the model actually made
(`:1388–1389`).

`Link ID` is written if and only if `v2_any()` (`api/routes.py:1281–1283`, `:1310`, `:1318`) and
sits immediately beside `Cluster ID` (`:1209–1214`).

### 16.3.10 Clustering parameters

| Name | Value | Set by | file:line |
|---|---|---|---|
| `SIG_PARTITION_THRESHOLD` | `12` | env | `dedup/adjudicator.py:39`, `:1465–1466` |
| `DEDUP_MAX_CONCURRENCY` | `5` | env | `:40`, `:1467–1469` |
| `NAME_CANDIDATE_THRESHOLD` | `0.85` | settings > env > default | `:41`, `config.py:605–607` |
| `TOKEN_CANDIDATE_THRESHOLD` | `0.6` | settings > env > default | `:42`, `config.py:608–610` |
| `MAX_CANDIDATES_PER_BLOCK` | `50` | settings > env > default | `:43`, `config.py:611–613` |
| `ACRONYM_THRESHOLD` | `0.8` | constant | `dedup/candidates.py:159` |
| `ACRONYM_MIN_LEN` / `ACRONYM_MAX_LEN` | `3` / `6` | constant | `:158`, `:151` |
| `CROSS_SLOT_THRESHOLD` | `0.85` | constant | `:160` |
| `NAME_VARIANT_MAX_EXTRA_TOKENS` | `1` | constant | `:279` |
| `OVERFLOW_THRESHOLD` | `0.92` | constant | `dedup/name_slots.py:109` |
| `INSTITUTION_THRESHOLD` | `0.85` | constant | `:112` |
| `INSTITUTION_SPLIT_THRESHOLD` | `0.92` | constant | `:118` |
| `STREET_NAME_THRESHOLD` | `0.85` | constant | `dedup/address.py:87` |
| `ZIP_EDIT_TOLERANCE` | `1` | constant | `:91` |
| `TEMPERATURE` | `0.0` | constant, deliberately not an env knob | `dedup/llm.py:134–138` |
| `LLM_TOP_P` / `LLM_SEED` | `1.0` / `42` | constant | `llm/openai_client.py:103`, `:108` |

`_resolve_candidate_config` resolves the three residue knobs as settings attribute > env var >
module default, warning and falling back on an unparseable env value (`dedup/adjudicator.py:1420–1443`).

---

## 16.4 Scoring and election rules

### 16.4.1 Weights

`dedup/weights.json`. The `_comment` key at `:2` is metadata and is stripped by `load_weights`
(`dedup/scoring.py:649–655`), which drops every key prefixed `_`.

| Criterion | Band → points | file:line |
|---|---|---|
| `sales_order_last_used` | `0`→20, `1`→15, `2`→10, `3`→5 | `dedup/weights.json:3–8` |
| `sales_order_count` | `1-5`→5, `6-10`→15, `>10`→25 | `:9–13` |
| `sales_order_partner_last_used` | `0`→20, `1`→15, `2`→10, `3`→5 | `:14–19` |
| `sales_order_partner_count` | `1-5`→5, `6-10`→15, `>10`→25 | `:20–24` |
| `equipment_count` | `1-3`→5, `4-8`→12, `9-15`→20, `>15`→30 | `:25–30` |
| `sleeping_customer` | `No`→15, `Yes`→0 | `:31–34` |
| `customer_status` | `active`→10, `blocked`→0 | `:35–38` |
| `account_group` | `DRIT`→20, `0002/SHIP2`→15, `0003`→10, `0004`→10, `0005/LIEF/MLIEF`→5 | `:39–45` |
| `company_code_count` | `1`→5, `2-4`→15, `5+`→25 | `:46–50` |
| `combined_presence_bonus` | `company code AND sales org`→10 | `:51–53` |
| `salesforce_instance_count` | `per instance`→10 | `:54–56` |

Maximum attainable total is 200 points (20+25+20+25+30+15+10+20+25+10), plus 10 per Salesforce
instance, unbounded above.

**Band grammar.** `a-b` inclusive range (`dedup/scoring.py:833–836`); `>n` strictly greater
(`:827–829`); `n+` greater or equal (`:830–832`); a bare number is exact equality (`:837–838`);
`X/Y` is either literal, case-insensitive (`:865–867`); an unparseable label is logged and skipped
(`:839–840`). **No match scores 0** — the table's implicit else band (`:822–823`, `:841`). Bands are
tested in dictionary insertion order and the first match returns, so overlapping bands resolve to
the earlier one.

**Overrides.** Both entry points accept a wholesale override under identical all-or-nothing
semantics, implemented by `coerce_weights` (`:657–692`): every `(criterion, band)` pair present in
`dedup/weights.json` must be present with a numeric `Points` value; one missing pair or one
non-numeric cell rejects the whole candidate (`:677–689`). A rejection is a warning in
`summary.warnings`, never an error. Points are cast with `int(points)` (`:690`), so a fractional
override truncates toward zero. Sources: the `weights` field of the `POST /api/dedup/score` body
(`api/routes.py:1456–1465`), or a worksheet named `Weights`, case-insensitive, with columns
`Criterion, Band, Points` (`dedup/scoring_xlsx.py:99–117`, `:210–224`).

`weights_version` is 12 hex characters of `sha256` over the canonically serialised table
(`dedup/scoring.py:641–647`).

### 16.4.2 Per-row score formula

`score_row(row, weights, cluster_max_year, cluster_max_partner_year, *, current_year)`
(`dedup/scoring.py:903–1031`). `total = sum(breakdown.values())`. The breakdown always carries all
eleven keys, 0 where nothing matched, so the output columns are stable.

| Criterion | Input field (file header) | Derivation | Cluster-dependent | file:line |
|---|---|---|---|---|
| `sales_order_last_used` | `Sales_Order_Last_Used` | banded on `current_year − year`, **not** on the year | no | `:957–959` |
| `sales_order_count` | `Sales_Order_Total_Count` | banded on the count, **gated by G1** | yes | `:963–977` |
| `sales_order_partner_last_used` | `Sales_Order_Partner_Last_Used` | offset band, as above | no | `:978–981` |
| `sales_order_partner_count` | `Sales_Order_Partner_Total_Count` | count band, **gated by G1** | yes | `:984–998` |
| `equipment_count` | `Equipment_Total_Count` | count band | no | `:999–1001` |
| `sleeping_customer` | `SleepingCustomer` | label band, warns on an unrecognised value | no | `:1002–1005` |
| `customer_status` | `CustomerStatus` | label band, warns on an unrecognised value | no | `:1008–1011` |
| `account_group` | `Account group` / `Customer Account Group` | label band, **silent** on an unrecognised value (`warn_unknown=False`, `:1016`) | no | `:1014–1017` |
| `company_code_count` | `Company_Code_Consolidated` | count of delimited parts, then band | no | `:1018–1020` |
| `combined_presence_bonus` | `Company_Code_Consolidated` + `Sales_Org_Consolidated` | flat bonus when both counts are `> 0` | no | `:1022–1026` |
| `salesforce_instance_count` | `sf1`…`sf8` | count of non-blank slots × per-instance points; unbounded | no | `:1027–1030` |

`Sales_Org_Consolidated` has no standalone tier: it contributes only through
`combined_presence_bonus`. **No criterion reads a name field** — the ballot is name-blind.

Coercion (`_coerce_int`, `:714–761`): int, float (truncated) and numeric strings accepted; blank and
`None` become `None` and score 0 **silently**; a present-but-unparseable value scores 0 and warns;
`bool` is rejected as dirt (`:738–740`). `allow_date=True` is set for the two `*_last_used` fields
only (`:939–948`), extracting `.year` through `_iso_year` (`:698–712`, years 1900–2200). The count
columns keep `allow_date=False`, so a date landing in `Equipment_Total_Count` warns rather than
scoring as a year. `split_consolidated` (`:780–793`) splits on both `,` and `;`; `derived_counts`
(`:795–809`) always recomputes `Company_Code_Count`, `Sales_Org_Count` and
`Salesforce_Instance_Count` from the consolidated cells and the eight slots — the file's own values
for those columns are overwritten, never read.

### 16.4.3 The year-maxima rule (G1 recency gate)

`_cluster_year_maxima` (`dedup/scoring.py:1097–1119`) computes `(max last_order_year, max
partner_last_order_year)` per cluster, **only for clusters of ≥ 2 members**.
`_award_count(row_year, cluster_max_year)` (`:882–900`) then decides whether a count component is
awarded at all:

| `row_year` | `cluster_max_year` | Count points | file:line |
|---|---|---|---|
| `None` | any | 0 | `:896–897` |
| present | `None` (context-free: a unique row, or a cluster of one) | awarded | `:898–899` |
| present | present, equal | awarded | `:900` |
| present | present, older | 0 | `:900` |

A suppression emits a warning only when it is a genuine recency loss — points that would have
scored, suppressed because a strictly more recent member exists (`:963–977`, `:984–998`). A
context-free suppression produces no warning. The warning text carries the marker
`count suppressed (G1)` (`:450`), which `detect_issues` re-reads to raise
`count_suppressed_by_recency`.

The gate applies to the two count criteria only. Every other criterion is unconditional, so a record
that lost its count points can still win on company-code breadth, equipment or account group.

The two `*_last_used` ladders band on the offset from the election's reference year
(`_year_offset`, `:763–771`), never on an absolute year; a future-dated order yields a negative
offset, matches no band and scores 0. The absolute year is retained for the G1 gate, the cluster
maxima and the tie-break (`:964`, `:987`, `:1090–1092`, `:1097–1119`). The reference year is
`datetime.date.today().year`, resolved **once per election** — not at import and not per row
(`:1181`).

### 16.4.4 Election, tie-break and merge confidence

`elect_golden_records` (`dedup/scoring.py:1151–1277`), in order: resolve weights → resolve the
confidence threshold → fingerprint the weights → resolve the reference year → **reject a duplicate
`row_id` with `DuplicateRowIdError`, the module's only hard failure** (`:1183–1187`, mapped to HTTP
400 at `api/routes.py:1466–1472`, `:1544–1550`) → compute cluster year maxima → score every row →
detect partial clusters → elect a winner per cluster → apply demotions → build result rows.

The winner is `min(members, key=_tiebreak_key)` over clusters of ≥ 2 members (`:1227–1231`).
Single-member clusters do not elect: they carry no entry in `winner_by_cluster` and fall through to
the lone-row branch (`:1228–1229`, `:1255–1265`).

**Tie-break order** (`_tiebreak_key`, `:1048–1065`):

| Rank | Key | Direction | Missing value |
|---|---|---|---|
| 1 | `total` score | highest first | — |
| 2 | `last_order_year` (absolute) | most recent first | treated as −1, i.e. last |
| 3 | `equipment_count` | highest first | treated as −1, i.e. last |
| 4 | `company_code_count` | highest first | always an integer ≥ 0 |
| 5 | `row_id` | **lowest** first | — |

`row_id` is the final uniqueness guarantee, so the winner is invariant under input shuffling. It is
compared numerically when **every** `row_id` in that cluster parses as an integer, and lexically
otherwise (`:1230`, `_parses_as_int` `:1328–1333`); the check is per cluster, so one non-numeric
identifier changes the comparison for its own cluster only. The docstring marks the ordering
`UNCONFIRMED` pending confirmation with the business owner (`:1051–1057`).

**Merge-confidence formula.** `_cluster_merge_confidence(members)` (`:1138–1149`) is
`min(m.row.confidence for m in members if m.row.confidence is not None)`, or `None` when every
member's confidence is null. It is conservative on purpose: if any member joined below threshold the
whole merge is gated. All-null — a deterministic identical collapse with no LLM merge — returns
`None` and **never gates**.

The threshold is `_resolve_confidence_threshold` (`:1122–1136`): explicit argument > env
`CONFIDENCE_MERGE_THRESHOLD` > `DEFAULT_CONFIDENCE_MERGE_THRESHOLD = 0.95` (`:50`). An unparseable
env value logs a warning and falls back to the default.

### 16.4.5 Demotion and `election_status` thresholds

Four demotion conditions are evaluated per cluster of ≥ 2 members (`:1239–1249`). All four are
computed before the disjunction at `:1248`; any one is sufficient.

| Condition | Test | file:line |
|---|---|---|
| inherited | any member's incoming `Routing` normalises to `manual_review` — uncertainty propagates, and election never upgrades it | `:1239–1241` |
| all blocked | every member's `CustomerStatus` casefolds to `blocked`. `blocked` scores 0 but stays eligible to win | `:1242` |
| low confidence | `merge_conf is not None and merge_conf < threshold` | `:1243–1244` |
| zero signal | every member scored 0 — a tie-break with no scoring basis must not read as confident | `:1247` |

A lone row (no cluster, or a cluster of one) is demoted only by the **inherited** condition
(`:1255–1263`); the other three do not apply to it.

| `election_status` | Set when | file:line |
|---|---|---|
| `unique` | no cluster, or a cluster that degraded to one member, and the incoming routing is not `manual_review` | `:1255–1263` |
| `proposed` | a cluster of ≥ 2 members with no demotion condition met | `:1267–1269` |
| `manual_review` | a cluster meeting any demotion condition above, or a lone row whose incoming routing is `manual_review` | `:1259–1269` |

Field settlement per status (`_build_result`, `:1280–1325`), and the divergence between the JSON
model and the workbook:

| Field | `unique` | `proposed` | `manual_review` (model) | `manual_review` (workbook) |
|---|---|---|---|---|
| `is_golden_record` | `true` | winner `true`, loser `false` | winner `true`, loser `false` | **blank** |
| `golden_record_id` | own `row_id` | own id / winner's id | own id / winner's id | **blank** |
| `proposed_golden_id` | `null` | winner's id | winner's id | winner's id |
| `approval_status` | `null` | `proposed` | `proposed` | `proposed` |

The blanking is applied at the file writeback only (`dedup/scoring_xlsx.py:309–316`), not in the
model (`dedup/scoring.py:1300–1311`): a consumer filtering on `is_golden_record` alone cannot reach
an unreviewed row. The JSON route keeps the proposal so that summary accounting and
promotion-on-approval have it. A `manual_review` lone row is its own proposed winner (`:1264–1265`),
so `proposed_golden_id` self-references. The Phase 3 contract is stated on the model (`:291–293`):
consume only rows with `approval_status == "approved"` or `election_status == "unique"`.

A `c_`-prefixed cluster id whose present members do not reproduce `cluster_hash` is marked partial;
a **warning** is attached, never an error (`:1218–1223`, `:1271–1275`).

Election is scoped to `Cluster ID` alone. `ScoringRow` (`:98–259`) declares no `Link ID` field and
`dedup/scoring_xlsx.INPUT_HEADERS` (`:41–60`) does not read that column, so the institution-family
link never widens or narrows an election scope. Scores are never compared across clusters.

### 16.4.6 Issue codes raised by scoring — the `DedupIssue` vocabulary

`ISSUE_TYPES` is eight declared types (`dedup/scoring.py:434–443`); seven are emitted by
`detect_issues` (`:485–566`). This is a separate vocabulary from `ISSUE_CATALOGUE`: one object per
**row or cluster**, in a list, repeatable, carrying `{row_id, cluster_id, issue_type, detail}`
(`:458–464`). Every type is advisory — the term used is "potential inconsistency" — and there is no
severity model. Cluster-level types key on the proposed winner's `row_id`.

| Type | Level | Predicate | Emission site |
|---|---|---|---|
| `verdict_contradiction` | row | `_reasoning_is_contradiction(row.reasoning)` — the reasoning starts `split:` or contains one of six phrases | `:505–510`; markers `:452–455`, `:475–482` |
| `candidate_cap_exceeded` | row, deduplicated per cluster | `"candidate_cap_exceeded" in row.reasoning` — one issue per capped block | `:511–520`; marker `:447` |
| `count_suppressed_by_recency` | row | `"count suppressed (G1)" in warning` on any result warning | `:523–530`; marker `:450` |
| `low_confidence_merge` | cluster | `min(member confidences) < threshold` | `:543–548` |
| `all_blocked_cluster` | cluster | every member's normalised status is `blocked` | `:549–553` |
| `tiebreak_decided` | cluster | ≥ 2 members and the top score is shared by ≥ 2 of them | `:554–560` |
| `empty_scoring_payload` | cluster | every member scored 0 — "winner decided by tie-break only" | `:561–565` |
| `missing_building_inconsistency` | — | **declared and never emitted.** Reserved for the upstream building differentiator (Phase 1); no building signal exists at election stage | none (`:430–433`) |

⚠ No `DedupIssue` reaches the database: `usp_MergeValidationScores` parses `$.rows` only
(`sql/usp_merge_validation_scores.sql:49`) and no column receives `$.issues`
(`docs/thesis/08_GAPS.md` G-30). The vocabulary surfaces on the `Issues` sheet of the scored
workbook (`dedup/scoring_xlsx.py:30–31`, `:328–334`) and in the `issues` field of the
`POST /api/dedup/score` response (`dedup/scoring.py:472`) and nowhere else.

The identifier `detect_issues` and the surface name `Issues` are shared with `ISSUE_CATALOGUE`. The
collision is in the names only; `api/routes.py:52` imports the deduplication one under the alias
`detect_dedup_issues` to keep the two apart in one module. **The two counts must never be summed.**

---

## 16.5 Items raised by this pass

| # | Item |
|---|---|
| ⚠-1 | `G1-ADDR-009` is raised by the address stage (`enrichment/address_processing.py:817–822`) but is `status="withdrawn"` in `ISSUE_CATALOGUE` (`enrichment/issue_detection.py:283–287`) and can never be emitted by `/issues`. The two subsystems disagree about whether the code exists. §16.1.8, §16.2. |
| ⚠-2 | The `alias` slot class files a parent organisation's name (`A <something> Company`) as another name for the *same* institution, and `cross_slot` nomination and evidence match on aliases. A subsidiary can therefore bridge to its parent. Present and unguarded. §16.3.3. |
| ⚠-3 | Tier 2A Mode B thresholds `max(llm_self_reported_score, rapidfuzz_token_sort_ratio)` — two numbers on different scales — with one threshold. Recorded in the code as a known defect. §16.1.5. |
| ⚠-4 | `G4-ADDR-026` checks postal-code format for US, CA and DE only. A clean count reads as "no defect found in three countries", never as "the postal codes are good". §16.2. |
| ⚠-5 | Eight of the 33 DQ-dimension assignments in §16.2 are marked `judgement`: the predicate tests a proxy and the dimension is a reading of what the proxy stands for. Pass 17 must not treat those edges as mechanically derived. |
| ⚠-6 | The `_slot_origin` origin invariant governs a field that is popped before serialisation, so its effect cannot be audited from any output. §16.1.9, `docs/thesis/08_GAPS.md` G-100. |

---

**Pass 16 summary.** `docs/thesis/16_RULESETS.md` written: four rulesets — 10 sub-tables of
standardisation and enrichment rules (§16.1), the 33 live issue codes as predicates with field,
remedy class and DQ dimension (§16.2), 10 sub-tables of deduplication rules from blocking key to
`Link ID` (§16.3), and the weights, per-row formula, year-maxima gate, tie-break, thresholds and
`DedupIssue` predicates (§16.4), plus six items raised for Pass 08.
