Generated: 2026-08-21 · Commit: d4fc46938514c9a7d249979c4aa9b4ae4cf3e564 · Branch: main · Tree: `docs/thesis/11_DELTA.md` untracked, no other change (`git status --porcelain --untracked-files=all` → `?? docs/thesis/11_DELTA.md`)

# Pass 12 — Design rationale and the structures the thesis must describe

`11_DELTA.md` records *what* changed in `515cc7c..d4fc469`. This document records *why*, and
specifies the structures a reader of the thesis needs in front of them. It does not restate the
delta; where a fact is already in `11_DELTA.md` it is referenced by section, not repeated.

**Provenance: the placeholder is resolved.** `enrichment/provenance.py`'s record model —
`Evidence`, `ProvenanceEvent`, `RejectedCandidate`, `ProvenanceLog`, the confidence scales and the
guards — stands as described. What was "being reimplemented" at this pass's header commit was the
**exported** representation, and it has since landed: the `*_provenance` derived scalars are now
Provenance Scheme B, `source:confidence[+witness]`, with confidence computed by one function from
one table. The grammar, the table and the four hard rules are documented **once**, in README §
"The provenance grammar — Scheme B"; the old→new mapping with counts is in
`provenance_migration_report.md`; the parameter that governs it is in `04_PARAMETERS.md` §1.21.
This document is otherwise unchanged, and the entries below that quote Scheme A strings are
correct for their header commit and are marked where they are not current.

**Method.** Every claim carries a `path:line` citation into the working tree at the header commit.
Rationale is quoted only from module docstrings, code comments and commit messages; where none
exists the entry reads `⚠ RATIONALE NOT IN REPO — author to supply` rather than an inference.
Numbers are either lines of current code or the verbatim output of a named command. Nothing was
carried over from another pass document without re-reading the source.

---

## §1 · Design rationale, per subsystem

### 1(a) · `enrichment/flags.py` — one rebuild from final state, not per-tier accumulation

**Problem.** Recorded in the module docstring and quantified in the commit message. The flag
"answered a different [question] — *which tier ran?* — because each tier appended its own reason as
it executed. That produced a flag on 47 of 50 demo records and a reason text that named a code path
rather than a doubt, so the flag stopped working as a triage signal" (`enrichment/flags.py:4-7`).
Commit `5e423c2` ("Fix 8: flag model redesign") states the same figure and the consequence: "47 of
50 demo records were flagged, so it could not be used to decide what to look at."

**What was done before, and why it was inadequate.** Each tier set `flag_for_review` and appended to
a single `flag_reason` string as it ran, and `_flag_website_review` appended with `"; "` rather than
overwriting (`11_DELTA.md` §2 B-3). Three specific inadequacies are recorded:

1. *The reason described the pipeline, not the record.* A record that reached Tier 3 and was then
   rescued by the Tier 1 retry kept the Tier 3 reason even though it now held a registry identifier
   — "the reason is derived from what the record *holds*, not from what ran" (`flags.py:11-16`).
2. *One string does not scale past one condition.* `api/models.py:419-422` records this directly:
   "A record can carry several; the single concatenated `flag_reason` string it replaced did not
   scale past one condition."
3. *Absence of data was treated as a defect.* "A research institution with no department and no
   contact is not flagged: there is nothing for a reviewer to do" (`flags.py:29-31`).

**Alternatives visible in the history.** `enrichment/confidence.py::should_flag_for_review` was the
declarative rules-table alternative — a pure function from `(confidence, tier_used, tier2_mode,
name2_match_result, source)` to a flag. It was deleted in the same change. `5e423c2` gives the
reason it was not adopted: "It matched the README's Flag Rules table and had no caller, which is how
the code came to contradict its own documented spec on Tier 2 canonicalisation." A rules table that
nothing calls diverges silently; a single authority called from one place cannot.

**Measured effect (from the commit message, not recomputed here).** "Demo batch flag rate 47/50 ->
21/50; 13 reason strings -> 6 codes in use; 9 of 21 flagged records are single-field."

**The cost of the design, recorded in the code.** Rebuilding once assumes every input has settled
when `finalise` runs, and one pass runs later: batch consensus. `retract` exists solely for that,
and is constrained to withdrawal — "it can only ever withdraw — never raise, never re-judge"
(`flags.py:19-22`). Two internal fields (`flag_scopes`, `flag_details`) are kept only so that
withdrawal can re-render the surviving codes with the wording they were raised with
(`flags.py:42-47`; `api/models.py:432-442`).

### 1(b) · `enrichment/batch_consensus.py`

**Problem.** "Every record is enriched in isolation, so two rows naming the same organisation at the
same address can leave the batch carrying different identities: one resolved against a registry and
the other did not" (`enrichment/batch_consensus.py:3-6`). Commit `c4834a5` names the concrete
defects: "four Lockheed Martin rows where only one carried the ROR id, two MIT rows holding one
registry id each and disagreeing on the domain, a Coastal Diagnostics trio that no registry resolved
at all and that shipped three name forms."

**What was done before.** Nothing — there was no such pass (`11_DELTA.md` §2 B-7). The divergence was
left for Phase 2 dedup to adjudicate. The recorded reason for not leaving it there: "it is cheaper
to prevent the divergence here than to have Phase 2 adjudicate it later"
(`batch_consensus.py:7-8`), and the pass "is the safety net for whatever the retry does not catch"
(`:6-7`) — i.e. it is explicitly the second line of defence behind §1(e)'s Tier 1 retry, not a
replacement for it.

**Alternatives visible in the history.** Two are recorded as considered and rejected inside the
module:

- *Merge the records.* Refused by scope: "It never merges, drops or deduplicates records — Phase 2
  remains the only place entities are merged" (`:17-19`).
- *Fold the legal form into the grouping key.* Refused because "Folding it in would group 'Delta
  Analytical Inc' with 'Delta Analytical LLC' at a shared address — potentially distinct legal
  entities, and exactly the judgement Phase 2 exists to make" (`:210-212`).

**Alternative rejected on measurement grounds.** `tier_used` is deliberately not set to 1 on an
inheriting record: "inflating the Tier 1 count would corrupt the tier-distribution figures used in
evaluation" (`api/models.py:507-509`).

### 1(c) · `enrichment/classifier.py` as the single `record_type` authority, and the `routing_type` / `record_type` split

**Problem.** "`record_type` used to be written by whichever tier happened to run last: ROR org types,
then an LEI hit, then company canonicalisation, each overwriting the one before. That produced MIT
classified `company` because it holds an LEI, a hospital classified `company` by the company branch,
and `unknown` on 21 of 50 demo records without anything ever having decided so"
(`enrichment/classifier.py:3-7`). Commit `6038372` states the same three defects.

**Why the split into two fields.** The field was never purely an output — it gates which tiers run.
"The pipeline needs a type before the evidence that decides the final one exists, so this cannot
simply be deferred to the end" (`classifier.py:15-17`). `routing_type` is therefore provisional and
internal (`api/models.py:544-546`); `record_type` is decided once at the end of `finalise` and is
"The only value that reaches the output, the Excel export and Phase 2 dedup"
(`classifier.py:21-23`).

**Evidence of behavioural neutrality on routing.** `6038372` records a regression check: "Which tiers
run for a given record is unchanged: 50 records run through the orchestrator before and after
produce byte-identical `tier_used`, `source`, `department_domain` and `domain`."

**Alternatives visible in the history.** `6038372` records three GLEIF fields evaluated against live
`api.gleif.org` responses and rejected:

- `entity.category` — "is 'GENERAL' for MIT AND Pfizer, and for the overwhelming majority of
  entities", so only five values are kept (`classifier.py:63-74`).
- `entity.subCategory` — "was null on every record sampled". It is not read.
- `entity.legalForm.id` — adopted, via `enrichment/elf_codes.py`. The codes `8888`/`9999` carry no
  meaning, so `legalForm.other` free text is matched separately (`classifier.py:76-89`,
  `:126-141`).

The ELF table is deliberately narrow — `6038372`: "95 non-commercial and 978 commercial of 3,599 —
so 'Nonstock Corporation' is not read as commercial for containing 'Corporation'".

**The LEI guard.** "An LEI hit on its own never sets `company`. An LEI proves legal registration, not
commercial status: universities, hospitals, foundations and government bodies hold LEIs, typically
for bond issuance or derivatives reporting. So a commercial-looking verdict is withheld — not
overridden, withheld" (`classifier.py:146-153`).

**`unknown` as a terminal state.** "deliberately preferred over defaulting to `company`, which would
assert something the pipeline does not know" (`classifier.py:38-40`).

**Recorded limitation, not a gap in rationale.** `routing_type_mismatch` marks a record "whose tiers
were gated on a type the evidence later contradicted — it was routed down the wrong branch and is
NOT re-run" (`api/models.py:541-547`). `6038372` states the reason for not re-running: "Counted, not
re-run." ⚠ RATIONALE NOT IN REPO — author to supply: *why* re-running is out of scope (cost, batch
ordering, or idempotence) is not stated anywhere.

### 1(d) · `utils/domain_resolver.py` and the ownership guard

**Problem, and the defect it demonstrably prevents.** "ROR has a country guard and GLEIF a
name-verification guard, because both upstream scorers return confident wrong answers. The domain
path had neither, so an unrelated company's website could be attached to a customer record and read
as successful enrichment (`delta.com` for 'Delta Analytical')" (`utils/domain_resolver.py:19-23`).
The stated design principle: "Attaching an unrelated company's website is worse than an empty field,
because it reads as successful enrichment" (`:338-339`).

A second, separate defect is recorded in `4645b33`: the exported "Domain" column was bound to
`website_url`, "so every value shipped with a scheme and some with a deep ROR path
(`http://www.uni-stuttgart.de/home/index.en.html`) or a sub-site host
(`https://investors.lockheedmartin.com`). The bare `domain` was already canonical — the export
mapping was the format bug."

**What was done before, and why it was inadequate.** Five direct write sites, each writing the
selected URL's domain with no ownership check (`4645b33`: "Five direct write sites removed").
Precedence was expressed as an ordering (ROR domain → domain-from-`website_url` →
domain-from-`source_url`, `11_DELTA.md` §3.4) rather than as an admissibility question, so a
candidate that won the ordering was written regardless of whether it belonged to the organisation.

**The threshold's derivation is recorded, not assumed.** `DOMAIN_NAME_MATCH_THRESHOLD` default `82`:
"Tuned on the demo batch: the highest wrong-owner pair scores 81.8 ('Acme Biotech' →
aumbiotech.com) and the lowest right-owner pair 82.4 ('Lockheed Martin Corp' → lockheedmartin.com),
so 82 is the smallest value that separates them" (`config.py:208-212`).

**Alternatives visible in the code.** Three narrower designs are recorded as rejected:

- *Segmenting concatenated domain labels.* "concatenated words are deliberately **not** segmented,
  since guessing word boundaries inside `aumbiotech` produces false confidence"
  (`domain_resolver.py:250-252`).
- *Accepting on a single token overlap, as the SERP layer does.* "Every significant Name-1 token must
  appear, not just one: the SERP layer already admits a result on a single ≥4-char overlap, which is
  exactly how a stranger's page ('… Biotech …') slips through" (`:275-278`).
- *Collapsing subdomains everywhere.* Refused for department domains: "Department domains
  legitimately *are* subdomains (`chemistry.stanford.edu`, `be.mit.edu`), so collapsing them would
  destroy the Tier 2B output" (`:125-127`).

**Ordering rationale inside the guard.** Name similarity is checked before email "so a well-matched
candidate is never clobbered by an unrelated address on the record (a distributor's mailbox)"
(`:327-329`), and email *replaces* the candidate rather than merely corroborating it because "a
record holding `ORDERS@MERIDIANLABS.COM` already knows the organisation's domain better than a search
result does (`meridianlabs.ai`)" (`:330-333`).

**The kill switch.** `DOMAIN_OWNERSHIP_GUARD_ENABLED` exists "so the guard can be A/B disabled" and
follows an existing pattern — "see the `LEI_LOOKUP_ENABLED` pattern" (`:30-31`; `config.py:216-222`).
When off, canonicalisation still applies and only the ownership conditions are skipped
(`domain_resolver.py:359-361`).

### 1(e) · `_retry_tier1_after_canonicalisation`

**Problem.** "The pipeline could already work out the right name and then throw the chance away: ROR
misses on 'MASSACHUSETTS INSITUTE OF TECHNOLOGY', company_canonical / Tier 3 / 2A / 2B produce
'Massachusetts Institute of Technology', and nothing ever looks *that* string up — so the record
ends with the correct official name and no registry ID, even though the corrected string resolves in
ROR on the first try" (`enrichment/orchestrator.py:2383-2388`).

**What was done before.** One Tier 1 pass per record. The docstring names the one path that already
did the right thing and the two that did not: "(`person_affiliation` already re-enters Tier 1 this
way; the company canonicalisation and Tier 3 paths were terminal.)" (`:2389-2390`) — so the design is
a generalisation of an existing mechanism rather than a new one.

**Constraints and their reasons.** All recorded in the docstring and body:

- One retry per record, tracked by `tier1_retry_attempted` (`:2392`, `:2403-2404`, `:2421`).
- Skipped when the record already carries `ror_id` or `lei_id` — "nothing to recover" (`:2405-2407`).
- Skipped when `normalize_key(canonical) == normalize_key(original)`, because "a pure
  punctuation/case/accent difference is not a corrected name and must not buy an API call"
  (`:2416-2418`).
- Every guard applies unchanged: "the ROR country guard, the distinctive-token guard and the GLEIF
  name-verification guard all apply unchanged, and a retry that fails one of them is simply a miss"
  (`:2393-2395`). On a miss nothing is written (`:2395-2396`).
- Registry-name coupling: "The retry attaches the identifier, so it must attach the name too. Before
  Fix 4 this path wrote `ror_id` and the domain but left `name1_enriched` as whatever the earlier
  tier produced … a record holding a `ror_id` while displaying a name that is not that ROR record's
  official name" (`:2475-2481`).
- Ordering against the ownership guard: `0f884f1` — "It runs at the top of `_finalise_and_return`,
  before the website paths, so a retry hit supplies a domain with registry provenance — a domain the
  Fix 1 ownership guard had rejected comes back verified." Restated in the body at `:2511-2514`.
- `record_type` is not reassigned: "reconciling it belongs to the record_type fix, not here"
  (`:2398-2401`) — an explicit hand-off to §1(c).

**Known limit, recorded by the author rather than discovered here.** `0f884f1`: "the retry can only
fire when a tier actually writes a changed `name1_enriched`.
`company_canonical.canonical_preserves_identity` rejects a corrected typo ('MASSACHUSETTS INSITUTE OF
TECHNOLOGY') and an expanded abbreviation ('GA Tech', 'FL State Univ'), so rows 23/24/28 still
discard the right answer one gate earlier than this fix reaches. Rows 8 and 18/20/21 do converge."

### 1(f) · The five-slot name block

**Problem the vocabulary module solves.** "Every rule that walks the SAP name block imports its slot
list from here instead of writing a literal `("name1", "name2", ...)` tuple. One definition means a
rule can never silently apply to a subset of the block: adding a slot below extends every rule that
uses these constants at once" (`utils/name_slots.py:3-6`). Raising `NAME_SLOT_COUNT` "is the single
edit that widens the whole name block" (`:30-34`).

**Why three separate lists rather than one.** "because the rules genuinely differ in shape"
(`name_slots.py:8`): `NAME_SLOTS` for rules that treat every slot the same, `DEPT_SLOTS` for rules
that "must not touch Name 1" because "Name 1 holds the organisation; Names 2..N hold departments,
units and overflow" (`:15-19`), and `ADJACENT_NAME_PAIRS` for continuation/duplication rules "rather
than hard-coding the Name 1 / Name 2 pair" (`:21-25`).

**⚠ RATIONALE NOT IN REPO — author to supply: why five and not four or six.** `NAME_SLOT_COUNT = 5`
carries no justification for the value itself. Commit `b8ad102` ("Enhance name handling by adding
support for five name slots") records the change, not the reason. The nearest thing to a rationale is
`enrichment/issue_detection.py:255-257`, which notes that Catalogue v2's `G4-NAME-015` is named "Name
Overflow Beyond Name 4" and that the divergence is "reported for a Notion correction" — i.e. the
catalogue still assumes four. Whether SAP supplies a fifth column, or five was chosen as headroom, is
not determinable from the repository.

**Incomplete generalisation, with the exception now deliberate.** `11_DELTA.md` §6 U-7 recorded the
extent of the sweep as undetermined. It is now determined for the one rule in question: see §9.1.

### 1(g) · The write-lock on the six scoped fields — enforcement only

*(The provenance record itself is out of scope; only the enforcement mechanism is described here.)*

**What is locked.** Six fields — `name1_enriched`, `name2_enriched`, `domain`, `record_type`,
`ror_id`, `lei_id` (`enrichment/provenance.py:53-62`) — on two objects: the pipeline's working
record `EnrichedRecord` (`provenance.py:702-714`) and the finalised `EnrichmentResult`
(`api/models.py:562-568`).

**What `UnattributedWriteError` prevents.** Direct assignment to a scoped field on either object.
The lock is not one gate but five, because a `dict` subclass has five write paths and leaving any
open would make the property advisory:

| Path | Behaviour | Cite |
|---|---|---|
| `record["domain"] = …` | `UnattributedWriteError` | `provenance.py:797-804` |
| `record.setdefault("domain", …)` | `UnattributedWriteError` — refused "whether or not the key happens to be present: `setdefault` states an intent to write, and a reader of the call site cannot tell which branch it will take" | `:806-815` |
| `record.update({...})` | `UnattributedWriteError` naming every blocked key | `:817-824` |
| `record.pop("domain")` | `UnattributedWriteError` — "write-locked and must not be removed" | `:826-831` |
| `EnrichedRecord.initialise(base)` with a non-null scoped value | `UnattributedWriteError` | `:834-852` |
| `result.domain = …` on the finalised model | `UnattributedWriteError` | `api/models.py:562-568` |

The stated purpose of exhausting the paths: "There is no way to write a scoped field that does not
carry evidence — which is what makes the principle a property of the code rather than a convention"
(`provenance.py:219-221`).

**What `MissingEvidenceError` prevents.** Calling the sanctioned write path with nothing usable —
`None`, or an argument of any other type. The message states the rule: "a value whose origin cannot
be reconstructed is not admissible" (`provenance.py:734-743`; the same check on the result model at
`api/models.py:576-580`). So the two errors close the two halves of one hole: `UnattributedWriteError`
stops a write from bypassing the path, `MissingEvidenceError` stops the path from being called
vacuously.

**Why writes were centralised at all.** Two reasons are recorded, both about a *specific*
indistinguishability:

1. On the working record — a `dict` subclass was chosen "so that the ~18k lines of reading code
   (`result.get("name1_enriched")`) are untouched, while every *write* to a scoped key must state its
   evidence" (`provenance.py:705-708`). The design constraint was therefore to add enforcement
   without a rewrite of the read side.
2. On the finalised result — "it matters because the batch consensus pass (Fix 6) writes ROR IDs,
   domains and names onto already-finalised records, and an inherited registry identifier must never
   be indistinguishable from a first-hand one" (`api/models.py:555-560`). The same sentence appears
   at the consensus write site (`enrichment/batch_consensus.py:504-507`): "a reviewer asking 'where
   did this come from' has to be able to land on the record that did."

**Seeding is not writing.** Blank scoped keys arrive through `dict.__init__`, since "a field that is
None has nothing to attribute, and the gate only ever asks about non-null values"
(`provenance.py:719-722`).

**Dependency, named once.** The sanctioned write path also appends to the record's provenance log and
regenerates that field's derived scalar (`api/models.py:570-594`); that log is the subsystem the
thesis carries a placeholder for and is not described here.

---

## §2 · Flag codes

### 2.1 · The eleven codes

`ALL_CODES` (`enrichment/flags.py:81-93`). "Each has exactly one detection rule below and one prose
template in `_REASONS`" (`:65-67`). Every condition below is read out of `compute_flags`
(`:375-520`). "Raised by" names the evidence key or record state that fires it; the tiers that leave
that evidence behind are cited where the key is written.

| # | Code | Meaning | Condition that raises it | Scope (`flagged_fields`) | Retractable? |
|---|---|---|---|---|---|
| 1 | `overflow` | One value split across several SAP fields | `_ev_overflow` truthy — set to `overflow.fields` by the UC-0 detector (`orchestrator.py:2820`), or to bare `True` when preprocessing reports a `slots-full` flag (`:3001-3002`) | The slots named in `_ev_overflow` when it is a list/tuple/set, else the whole block `name1`…`name5` (`flags.py:403-413`) | No |
| 2 | `opaque-code` | Name 1 holds an internal code, not an organisation name | `_is_opaque_code(name1_enriched)` on the *final* value. Preprocessing clears opaque codes from Name 2..N "but never out of Name 1" (`flags.py:415-420`) | `name1` | No |
| 3 | `person-unresolved` | Record holds a person whose organisation could not be resolved | `_ev_person_unresolved` (`orchestrator.py:2325`) | `name1` (`flags.py:422-423`) | No |
| 4 | `no-match` | No source could identify the organisation | `_nothing_was_enriched(result)` **and** no other code fired. "if any other code fired, that code is the actionable one and this would only add noise" (`flags.py:504-509`). `_nothing_was_enriched` is false if status is `enriched`/`verified`, or any of `ror_id`/`lei_id`/`domain`/`department_domain`/`source_url` is set, or any `{slot}_changed` / `contact_changed` / `email_changed` is true (`:221-243`) | `name1` | **Yes** — registry mode only |
| 5 | `unverified-inference` | Value rests on model training data and nothing else | Field is in `_evidence_free_fields(...)`, is not registry-named, is not corroborated by `department_domain`, and `{field}_changed` is true (`flags.py:444-456`). Raised "regardless of the model's confidence: a confident unverifiable claim is the more dangerous case" (`:426-430`) | The field itself (`name1`…`name5`) | **Yes** — registry mode only, and only when scoped to `name1` |
| 6 ‡ | `low-confidence-unchanged` | Left exactly as supplied; canonical form not established | **RETIRED — no longer a code.** At the header commit: field is in `_ev_low_conf_unchanged`, is not registry-named, is not already `unverified-inference`, and `{field}_enriched` is non-empty (`flags.py:465-471`). It now derives from the field's own provenance instead: the condition is exactly `input:low`, and `flag_for_review` follows from that with no code attached. The reason text is unchanged and still rendered in the same position. For `name3`…`name5`, which are outside provenance scope, the `_ev_low_conf_unchanged` marker still supplies the same derived flag | The field itself | **Yes** — by re-derivation, not by naming a code |
| 7 | `dept-via-lab` | Parent department inferred from the lab's own page, not read from a stated department | `_ev_dept_via_lab` (`orchestrator.py:3501`) | `name2` **and** `_ev_demoted_to` (default `name3`) (`flags.py:476-478`) | No |
| 8 | `name3-not-demoted` | Parent department written to Name 2 but every slot below was full, so the lab name could not move down | `_ev_name3_not_demoted` (`orchestrator.py:3502-3506`) | All of `DEPT_SLOTS` = `name2`…`name5` (`flags.py:479-480`) | No |
| 9 | `multiple-contacts` | More than one person in Contact, so the department could not be confirmed against a contact's page | `_multi_contact` (`orchestrator.py:2996`) **and not** `contact_used`. "When Tier 2A ran anyway (`contact_used`), the department is settled and there is nothing outstanding" (`flags.py:483-488`) | `contact`, `name2` | No |
| 10 | `email-conflict` | An email in the record differs from the one on file | `_ev_email_conflict`, set when preprocessing emits an `email-conflict` flag (`orchestrator.py:3003-3004`) | `email` (`flags.py:490-491`) | No |
| 11 | `domain-unverified` | A candidate website was found but nothing tied it to this organisation | `_domain_unverified`, written by `write_domain` on a guard rejection as the rejected domain string, not a bare marker (`utils/domain_resolver.py:464-469`) | `domain` (`flags.py:498-502`) | No |
| 12 † | `entity-superseded` | The organisation the record names no longer exists as a separate entity | `_ev_entity_superseded`, written by `orchestrator._wikidata_crosswalk` when the matched Wikidata item carries `P576` (dissolved) or `P1366` (replaced by). The value is the reason clause itself — `"replaced by <label> (<QID>)"` or `"dissolved <date>"` — and is rendered through `_DETAILED_REASONS`, falling back to the generic wording for a bare `True`. Raised whatever the registry crosswalk then found: "a dissolved entity's LEI record is itself informative", and the flag is about the entity, not about whether the lookup worked | `name1` | No |

† **Post-baseline.** Code 12 was added after the commit pinned in this document's header
(`515cc7c`), by the Stage 2c Wikidata crosswalk lane. Codes 1–11 are the baseline.

**Why the name is not rewritten to the successor.** The lane knows the successor's label and QID
and deliberately declines to write them. Which legal entity a customer record should point at
after a merger depends on contracts and open orders the enrichment service cannot see — it is a
business decision, not a data-quality correction — so the flag hands the reviewer the successor
and stops. This is the same shape as code 11's `_DETAILED_REASONS` entry: name the specific thing
in doubt rather than send the reviewer to rediscover it.

**Emission order** is `_CODE_ORDER` (`flags.py:97-109`), not `ALL_CODES` order: `overflow`,
`opaque-code`, `person-unresolved`, `entity-superseded`, `no-match`, `unverified-inference`,
`low-confidence-unchanged`, `dept-via-lab`, `name3-not-demoted`, `multiple-contacts`,
`email-conflict`, `domain-unverified`. ‡ `low-confidence-unchanged` is retired as a code but keeps
its slot in `_CODE_ORDER`, so the derived low's clause appears where the code's clause used to —
which is what makes "review UX is unchanged" a checkable claim rather than an intention.
Rationale: "most structural first, so the leading clause of a multi-code reason is the one that most
changes what a reviewer does" (`:95-96`). `entity-superseded` sits above `no-match` because "this
organisation no longer exists" is a bigger change to what a reviewer does than "we could not
identify it" — and because raising it suppresses `no-match`, which is correct: the pipeline *did*
establish something about the record.

**Retraction.** The only withdrawal path is `flags.retract(result, codes, field)` (`flags.py:326-372`),
and its only caller is `apply_batch_consensus` (`enrichment/batch_consensus.py:525-527`). It is
constrained four ways:

- Only after a propagated write to `name1_enriched`, so `field` is always `"name1"`.
- Only the codes the write falsified, listed as data in `_RETRACTED_BY_NAME1`
  (`batch_consensus.py:131-134`): under `registry` mode `no-match` and `unverified-inference`;
  under `name_form` mode none. ‡ `low-confidence-unchanged` was in both lists and is in neither
  now: the propagated write goes through `EnrichmentResult.write`, which regenerates the field's
  provenance, so the derived low withdraws itself. `retract` re-derives it and reports the
  withdrawal under the retired code's name, because the statement withdrawn is the one that code
  used to make.
- Withdrawal is per field: "a code scoped to two fields keeps the other one and is dropped only when
  its scope empties" (`flags.py:339-341`).
- "A record-level code (empty scope) is never reached, because no field is in its scope"
  (`flags.py:341-342`).

Why the two modes differ: "only there does something arrive that answers them. `no-match` says no
source identified the organisation and a registry identity now has; `unverified-inference` says the
value rests on no external evidence and it now rests on the donor's registry match … Under
`name_form` neither is answered — electing the batch's modal spelling introduces no evidence"
(`batch_consensus.py:125-130`).

**One dead evidence key.** `_has_dept_signal` is listed in `_EVIDENCE_KEYS` (`flags.py:202`) and is
therefore popped, but `compute_flags` never reads it (`:375-520`). It is stripped, not consumed.

**A twelfth code can reach the `Flag Codes` column.** `unattributed-value` is appended after
`compute_flags` has run by `_raise_unattributed_flag` (`orchestrator.py:611-629`), which states it is
"the only code that can be raised after `compute_flags` has run". It is *not* in `ALL_CODES`, is not
rendered by `render`, and its prose is concatenated onto `flag_reason` directly (`:627-629`). It
originates in the provenance admissibility gate — the subsystem out of scope here — and is named only
because the flag vocabulary is not closed without it.

### 2.2 · The rendered `flag_reason` format, exactly

`render` is "the single place the flag columns are built, so a pass that withdraws a code later
(`retract`) cannot render them differently from the pass that raised it" (`flags.py:286-288`). The
format, verbatim from the source:

**Per code** (`flags.py:309-314`):

```python
prose = (
    _DETAILED_REASONS[code].format(detail=kept[code])
    if code in kept
    else _REASONS[code]
)
reasons.append(f"{_label(fields)}: {prose}" if fields else prose)
```

**The scope clause** `_label` (`flags.py:213-218`):

```python
labels = [FIELD_LABELS[f] for f in fields]
if len(labels) == 1:
    return labels[0]
return f"{', '.join(labels[:-1])} and {labels[-1]}"
```

**The join** (`flags.py:320`): `"; ".join(reasons) if reasons else None`.

So the grammar is:

```
flag_reason := <clause> ( "; " <clause> )*                       | null when no code fired
clause      := <scope> ": " <prose>                              | <prose> when the code is record-level
scope       := <label>                                           | one field
             | <label> ( ", " <label> )* " and " <label>          | two or more, Oxford-comma-free
label       ∈ { "Name 1","Name 2","Name 3","Name 4","Name 5","Domain","Contact","Email","Address" }
```

Field labels are `FIELD_LABELS` (`flags.py:179-185`); the field order inside a clause is `_FIELD_ORDER`
= `name1`…`name5`, `domain`, `contact`, `email`, `address` (`:187-189`), and unknown field names are
dropped (`:208-210`).

**Worked renderings** (`./.venv/Scripts/python.exe`, calling `enrichment.flags.render` directly; the
em dash is `—`, U+2014, as written in `_REASONS`):

One code, one field — `render({LOW_CONFIDENCE_UNCHANGED: {"name1"}})`:

```
Name 1: left exactly as supplied — the canonical form could not be established with enough confidence to rewrite it; confirm the value is correct
```

The detailed variant, which names the value in doubt — `render({DOMAIN_UNVERIFIED: {"domain"}}, {DOMAIN_UNVERIFIED: "delta.com"})`:

```
Domain: a candidate website (delta.com) was found but nothing tied it to this organisation — confirm delta.com before using it
```

Three codes, one of them multi-field — `render({OVERFLOW: {"name1","name2"}, LOW_CONFIDENCE_UNCHANGED: {"name1"}, DOMAIN_UNVERIFIED: {"domain"}}, {DOMAIN_UNVERIFIED: "delta.com"})`:

```
Name 1 and Name 2: one value appears to be split across several SAP fields — confirm the field split before the record is used; Name 1: left exactly as supplied — the canonical form could not be established with enough confidence to rewrite it; confirm the value is correct; Domain: a candidate website (delta.com) was found but nothing tied it to this organisation — confirm delta.com before using it
```

with `flag_codes = ['overflow', 'low-confidence-unchanged', 'domain-unverified']`,
`flagged_fields = ['name1', 'name2', 'domain']`, `flag_for_review = True`.

A record-level code (empty scope) carries no leading clause — `render({NO_MATCH: set()})`:

```
no source could identify this organisation — resolve the name manually
```

No code: `flag_reason` is `None`, `flag_codes` `[]`, `flagged_fields` `[]`, `flag_for_review` `False`.

**Two facts about the boundary crossing.** First, the semicolon is load-bearing in two different
senses: it separates clauses inside `flag_reason`, and it is also the delimiter `_cell` uses to
flatten the list-valued `Flag Codes` and `Flagged Fields` columns for XLSX — "joined into the
semicolon-separated form the other multi-value columns already use" (`api/routes.py:328-338`). A
consumer splitting `Flag Reason` on `"; "` will also split inside a prose clause containing a
semicolon; `low-confidence-unchanged` contains exactly one ("…rewrite it; confirm the value is
correct", `flags.py:118-122`).

Second, the scope is deliberately duplicated into the prose: "The scope is encoded in the reason text
as well as in `flagged_fields`, so a consumer that reads only the two pre-Fix-8 columns still learns
which field is in doubt" (`flags.py:37-40`). That redundancy is what makes the change
backward-compatible for DATAshaper, which — see §8 — never receives `Flagged Fields` at all.

---

## §3 · Issue catalogue: groups G6 and G7

### 3.1 · What distinguishes G6 from G1–G5

G6 is **"Not Resolvable by Enrichment"**. Two properties separate it, and only the second is about the
rules themselves.

**(i) G6 is a regrouping, not a set of new codes.** Its four members keep their original `G2-`
identifiers: `G2-VAL-001`, `G2-VAL-003`, `G2-VAL-006`, `G2-NAME-012`
(`enrichment/issue_detection.py:266-273`). The direct consequence is stated twice in the source and
once in the README: "**The group is an attribute, not a prefix.** … so `code.split("-")[0]` is no
longer a group. Read `ISSUE_CATALOGUE[code].group`" (`:24-27`; restated `:133-137`;
`README.md:1621`). `issue_group()` exists for exactly this and falls back to the prefix only for an
unknown code (`:306-312`).

**(ii) Persistence across the comparison is correct behaviour, not failure.** "Expected to persist
from raw to enriched — that persistence is correct behaviour, not a pipeline failure"
(`:268-269`). `PERSISTENT_GROUP = "G6"` names the group for the comparison report (`:296`). The
report's own docstring gives the operational reason: "no automated path exists to supply the value,
so these codes are *supposed* to survive to the enriched file and be routed to a steward"
(`api/routes.py:469-471`).

That is what distinguishes G6 from G1–G5: a G1–G5 code "present before and absent after = work the
pipeline did" (`api/routes.py:466-468`), so its survival is a shortfall. A G6 code's survival is the
expected outcome, and a G6 code *clearing* would mean the pipeline invented a Tax Jurisdiction, a
Language Key or a Name 1 it had no source for.

**How `G2-NAME-012` came to be in G6 is recorded, and is a consequence of a withdrawal.** Withdrawing
`G2-CONTACT-009` "removed the contact-based (Tier 2A) department recovery path, which is why
G2-NAME-012 now sits in G6 — no automated route to a department remains"
(`issue_detection.py:232-234`; restated `:849-855`). This is a rare case of a group assignment with a
recorded cause: the code did not change, the remediation path did.

G6 is entirely DS-origin (all four entries carry `origin="DS"`, `:270-273`), which is the stated
reason `detect_issues` emits DS-origin codes by default: "the before/after reduction narrative is
defined over the whole G1-G6 set — of which G6 is entirely DS-origin" (`:1064-1068`).

### 3.2 · Why `G7-VERIFY-001` exists, what it is derived from, and why it is a catalogue code

**Why it exists.** "Not a quality issue: raised *by* successful enrichment so DATAshaper can route the
record to a steward through the Category dropdown" (`issue_detection.py:274-277`). The delivery
mechanism is the point: the DATAshaper issues view is the surface where a record is assigned to a
steward (`CONTEXT-EXTERNAL.md:362-366`, `:426`), and that view is driven by the issues column. A flag
that does not appear as an issue code cannot be routed there.

**What it is derived from.** Uniquely in the catalogue, from enrichment *output* rather than record
content: "it fires when the pipeline set `flag_for_review` on the record it produced, which a raw
input record has no way to carry" (`:1018-1026`). The chain is: `compute_flags` sets
`flag_for_review` (§2) → the merge-back binds it into Legacy (§8) → `/issues` reads the `Flag for
Review` cell back off the uploaded sheet via `_flag_for_review` (`api/routes.py:168-180`) →
`flag_for_review_is_set` interprets it (`issue_detection.py:1002-1015`) → `_detect_verification`
raises the code (`:1018-1029`).

The cell interpretation accepts "the real spellings an XLSX round-trip produces": a Python `bool`
from a checkbox cell, `1`/`0` from a numeric one, and the strings `_TRUTHY = {"true","yes","y","x",
"1"}` case-folded; "Everything else — including a blank, `"FALSE"`, `"N"`, `"0"` — is false"
(`:997-1015`).

**Three-valued, not boolean.** `None` and `False` both suppress the code but mean different things:
"`None` means 'this is a raw input file, G7 cannot apply', while `False` means 'this is an enriched
file and the pipeline did not flag this record'. Both suppress G7-VERIFY-001, but only `None` says the
question was never asked" (`api/routes.py:172-175`). `detect_issues`' parameter defaults to `None` so
"a raw audit must never produce it" (`issue_detection.py:1055-1058`).

**Why a catalogue code rather than a separate field.** Two reasons are recorded:

1. *The consumer contract is a single column.* "The `Issues` column's **shape is unchanged** — one
   appended column of semicolon-separated bare codes — so the DATAshaper contract is untouched. Only
   the set of codes that can appear in it changed" (`README.md:1625`). A separate field would have
   required a new column, a new DS mapping and a new validation-rule source; a new code required
   none of those.
2. *Granularity was considered and declined.* "The per-record trigger is in `Flag Reason`,
   deliberately not split into finer codes" (`README.md:1622`). One code carries the routing; the
   eleven flag codes of §2 carry the detail, in a column DS reads separately.

**And why it is nonetheless kept out of every quality figure.** `QUALITY_GROUPS` is `G1`…`G6` — "G7 is
deliberately absent: it is not a quality issue" (`issue_detection.py:286-287`) — and see §4.

### 3.3 · Full per-group membership at HEAD

Generated from `ISSUE_CATALOGUE` (`enrichment/issue_detection.py:186-279`) at the header commit;
38 rows, catalogue order. `Sev` is `IssueDefinition.severity`, derived from `mandatory`
(`:174-177`). Status vocabulary is defined at `:145-153`.

| Code | Group | Status | Field | Sev | Origin | Description |
|---|---|---|---|---|---|---|
| `G1-CROSS-001` | G1 | live | Name 1 | Warning | API | Address Content in Name Field |
| `G1-CROSS-002` | G1 | live | Street | Warning | API | Org Name in Address Field |
| `G1-CROSS-003` | G1 | live | varies | Warning | API | Contact Information in Wrong Field |
| `G1-ADDR-001` | G1 | live | Street | Warning | DS | House Number Embedded in Street |
| `G1-ADDR-003` | G1 | live | Street 2 | Warning | API | Sub-location Embedded in Street |
| `G1-ADDR-004` | G1 | live | Street | Warning | API | PO Box Embedded in Street |
| `G1-ADDR-006` | G1 | live | Street 2 | Warning | API | Mail Code in Street Field |
| `G1-ADDR-011` | G1 | live | Street 2 | Warning | API | Department Label in Street Field |
| `G1-NAME-001` | G1 | live | Name 1 | Warning | API | Name Overflow Across Fields |
| `G1-NAME-004` | G1 | live | Name 2 | Warning | API | Empty field in between populated name fields |
| `G1-NAME-013` | G1 | live | Name 2 | Warning | API | SAP Internal Code in Name Field |
| `G1-ADDR-009` | G1 | **ndd** | Street 2 | Warning | API | Unclassified Residual in Address |
| `G2-VAL-002` | G2 | live | Postal Code | Error | DS | Postal Code Missing |
| `G2-VAL-004` | G2 | live | Region | Error | DS | Region Missing |
| `G2-VAL-007` | G2 | live | Search Term 1 | Error | DS | Search Term 1 Missing |
| `G2-VAL-008` | G2 | live | Country | Error | DS | Country Missing |
| `G2-NAME-009` | G2 | live | Name 2 | Warning | API | Lab Without Department |
| `G2-CONTACT-008` | G2 | **withdrawn** | Name 2 | Warning | API | No Contact and No Department |
| `G2-CONTACT-009` | G2 | **withdrawn** | Name 2 | Warning | API | Department Missing And Enrichable from Contact |
| `G3-NAME-003` | G3 | live | Name 1 | Warning | BOTH | DBA Pattern in Name Field |
| `G3-NAME-005` | G3 | live | Name 2 | Warning | API | Duplicate Name Across Fields |
| `G3-ADDR-005` | G3 | live | PO Box | Warning | API | Multiple PO Boxes on Record |
| `G3-ADDR-012` | G3 | **unlisted** | Street | Warning | API | Duplicate Street Across Fields |
| `G3-ADDR-013` | G3 | live | Street | Warning | API | Two Distinct Street Addresses on Record |
| `G3-ADDR-014` | G3 | live | PO Box | Warning | BOTH | PO Box and Street Both Present |
| `G3-CONTACT-007` | G3 | live | Name 2 | Warning | API | Multiple Contacts on Record |
| `G4-NAME-015` | G4 | live | Name 4 | Error | API | Name Overflow Beyond the Name Block |
| `G4-ADDR-008` | G4 | live | Street 2 | Warning | API | Bare Sub-location Marker Without Value |
| `G4-ADDR-025` | G4 | live | Street 5 | Warning | API | Sub-location Overflow Beyond Street 5 |
| `G4-ADDR-026` | G4 | live | Postal Code | Warning | DS | Postal Code Format Invalid |
| `G4-ADDR-027` | G4 | live | Country | Error | DS | Country Code Not ISO 2-letter |
| `G5-NAME-001` | G5 | live | Name 1 | Warning | API | Organisation Name Not in Official Form |
| `G5-NAME-002` | G5 | live | Name 2-4 | Warning | API | Unit Name Not in Official Form |
| `G2-VAL-001` | **G6** | live | Name 1 | Error | DS | Name 1 Missing |
| `G2-VAL-003` | **G6** | live | Tax Jurisdiction | Error | DS | Tax Jurisdiction Missing |
| `G2-VAL-006` | **G6** | live | Language | Error | DS | Language Missing |
| `G2-NAME-012` | **G6** | live | Name 2 | Warning | DS | Research Institution Missing Department |
| `G7-VERIFY-001` | **G7** | live | Flag for Review | Warning | API | Enriched Record Requires Verification |

Totals, computed from the same dict: **38 declared**; per group `{'G1': 12, 'G2': 7, 'G3': 7,
'G4': 5, 'G5': 2, 'G6': 4, 'G7': 1}`; per status `{'live': 34, 'ndd': 1, 'withdrawn': 2,
'unlisted': 1}`; `len(EMITTED_CODES) == 35` (`live` + `unlisted`, `:282-284`). These agree with the
census output quoted in `11_DELTA.md` §3.0.

**Reasons for the four non-`live` statuses**, quoted from their entries:

- `G1-ADDR-009` (`ndd`): "'Unclassifiable' is defined as the complement of every classifier the
  pipeline runs, so no positive pattern can express it. … The real rule needs the LLM residual
  classifier, which /issues may not call." (`:202-212`)
- `G2-CONTACT-008` (`withdrawn`): "Its gate was identical to G2-NAME-012's, so it could never carry
  information the latter had not already reported." (`:219-226`)
- `G2-CONTACT-009` (`withdrawn`): see §3.1(ii). (`:227-236`)
- `G3-ADDR-012` (`unlisted`): "Implemented and emitting here, but absent from the Catalogue v2 G3
  table. Either it was withdrawn and this detector should stop emitting it, or v2 omits it and Notion
  needs the row added. Left emitting, unchanged, pending that decision." (`:241-250`)

Note that `G4-NAME-015`'s `field` is still `"Name 4"` while its `name` was made slot-agnostic for the
five-slot block — the divergence from Catalogue v2 is recorded in the comment above it
(`:255-257`), but the `field` attribute was not updated with it.

---

## §4 · `REDUCIBLE_GROUPS`

### 4.1 · What the code does

`REDUCIBLE_GROUPS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5")`
(`enrichment/issue_detection.py:293`). `_build_comparison_xlsx` assigns every code to one of three
blocks by `ISSUE_CATALOGUE[code].group` (`api/routes.py:494-501`) and computes the headline
percentage over the `Reduced` block alone:

```python
reduced_before = seg_before["Reduced"]
reduced_after = seg_after["Reduced"]
net = reduced_before - reduced_after
pct = (net / reduced_before * 100) if reduced_before else 0.0
```

(`api/routes.py:539-542`). G6 and G7 counts are reported in their own blocks and enter no reduction
figure.

### 4.2 · The argument that is recorded

The decision **is** recorded, in three places, with a distinct argument for each exclusion.

**For G6 — no remediation path exists, so its persistence is not a shortfall.**

> "G6 is excluded because its codes have no automated remediation path and are *expected* to
> persist" (`enrichment/issue_detection.py:289-291`).

> "**Expected to persist** (G6) — 'Not Resolvable by Enrichment': no automated path exists to supply
> the value, so these codes are *supposed* to survive to the enriched file and be routed to a
> steward. Folding them into the reduction total counts the pipeline down for defects it was never
> able to fix." (`api/routes.py:469-473`)

> "Excluded from the reduction %." (`README.md:1638`)

**For G7 — including it inverts the sign of the metric.**

> "G7 because counting it would inflate the post-pipeline total in proportion to how well enrichment
> performed" (`enrichment/issue_detection.py:291-293`).

> "**Verification** (G7) — raised *by* successful enrichment, not by a defect. Counting it would
> inflate the post-pipeline total in proportion to how well enrichment performed, inverting the
> meaning of the delta. It is reported on its own and never enters any reduction figure."
> (`api/routes.py:474-477`; near-identical wording `README.md:1639`)

The G7 argument is the stronger of the two and is a soundness argument, not a presentation one: G7
fires from `flag_for_review`, which `compute_flags` sets in proportion to how much the pipeline did
and how much doubt it recorded, so a G7 instance is positively correlated with enrichment activity. In
a single undifferentiated total it would appear in `total_after` and never in `total_before`, so the
better the pipeline performs the *worse* the measured reduction — which is what "inverting the
meaning of the delta" states. Excluding it is therefore not a choice of denominator but a
correction of a sign error.

**The G6 argument is weaker, and the alternative the brief names is not addressed anywhere.** The
recorded reason is that folding G6 in "counts the pipeline down for defects it was never able to
fix". That justifies *not folding G6 into the numerator's story*; it does not by itself justify
removing G6 from the **denominator** rather than reporting it as a residual within it. The two
presentations differ:

- *As implemented:* `pct = (G1–G5 before − G1–G5 after) / (G1–G5 before)`. Reports the reduction rate
  over the addressable subset; says nothing about what fraction of the record population's total
  defect load that subset is.
- *As a residual:* `pct = (all before − all after) / (all before)`, with the G6 count reported
  alongside as the irreducible floor. Reports the reduction rate over the whole defect load and makes
  the ceiling explicit.

The report does in fact carry the data for the second form — `seg_before` / `seg_after` are kept per
segment and all three blocks are written to the Summary sheet (`api/routes.py:507-510`, `:554-559`)
— so the residual presentation is one arithmetic step away and was not taken. **⚠ RATIONALE NOT IN
REPO — author to supply:** why the denominator was narrowed rather than the residual reported. The
comments, the route docstring, the README table and commit `8d5f5f9`'s message all argue *that* G6
should not count against the pipeline; none of them argues that it should be absent from the
denominator rather than visible inside it. `8d5f5f9`'s message describes the change purely
mechanically — "Modified the issue comparison report to segment issues into three distinct blocks:
Reduced, Expected to persist, and Verification, improving clarity in the output" — and gives no
metric argument at all.

### 4.3 · The contradiction with the thesis as written

This directly contradicts a metric definition already written into the thesis, and the contradiction
must be resolved before the evaluation chapter is defensible.

`07_EVALUATION.md:93-98` defines M-1's terms against the *baseline* implementation:

| Metric | Definition recorded in `07_EVALUATION.md` | Cite in that document |
|---|---|---|
| `Total issues before` | `Σ len(before_map[rid])` over `matched_ids` — occurrence count over the returned list | `:93` |
| `Net reduction` | `total_before - total_after` | `:97` |
| `Reduction %` | `round(net / total_before * 100, 1)`; `0.0` when `total_before == 0` | `:98` |

`03_ALGORITHMS.md:5400` records the same formula: "`net ← total_before - total_after`;
`pct ← net / total_before * 100` when `total_before` else `0.0`."

At HEAD there is no `total_before` in the reduction path at all — the identifiers are `reduced_before`
/ `reduced_after`, and their population is `REDUCIBLE_GROUPS` (`api/routes.py:539-542`). So:

1. **The metric's denominator changed and shrank.** G6 was inside the baseline's `total_before`; it is
   not inside `reduced_before`. Every G1–G6 figure in the thesis computed under the old definition is
   not comparable to a figure computed at HEAD.
2. **The direction of the change is dataset-dependent and is not determined here.** G6 codes appear in
   *both* `total_before` and `total_after` (they persist by construction), so removing them from both
   raises the ratio when the retained G1–G5 reduction rate exceeds the G6 rate of zero — which it does
   whenever any G1–G5 code clears. The magnitude depends on G6 prevalence in the dataset, and
   `PresentationTestData.xlsx` carries a large one: `G2-VAL-003` alone fires on 325 of 500 records
   (65.0%) and `G2-VAL-001`, `G2-VAL-006` and `G2-NAME-012` add 5, 5 and 25 (see §7's output, §3.3
   block). ⚠ MEASUREMENT REQUIRED to state the two percentages: `POST /issues/compare` over the two
   workbooks at HEAD, against the baseline route's single-total figure on the same input. This is
   `11_DELTA.md` §6 U-2, still open.
3. **The rounding also changed silently.** The recorded definition rounds to one decimal
   (`round(net / total_before * 100, 1)`); the current expression does not round at all
   (`api/routes.py:542`). The test asserts an exact `100.0` (`tests/test_routes.py:532`), which does
   not discriminate.

**Plainly stated:** the exclusion of G7 has a recorded, sound argument. The exclusion of G6 has a
recorded *motivation* but no recorded argument for narrowing the denominator in preference to
reporting a residual, and the thesis currently states the superseded definition. The author must
either supply that argument or restate M-1.

---

## §5 · Batch consensus

### 5.1 · The grouping key

Two components, address first, then name — "Group by address block first, then by canonical name +
legal form" (`enrichment/batch_consensus.py:232-233`).

**Address half** — `_address_block_id` (`:169-190`). Built from `street_cleaned`, `house_number`,
`postal_code`, `city`, `country_region_key` into a `DedupRow` and hashed by
`dedup.signatures.derive_block_id`. The reuse is deliberate: "`dedup.signatures.derive_block_id` is
reused rather than reimplemented, so a batch and its later dedup pass can never disagree about what
'the same address' means" (`:172-174`). Returns `None` when both street and postal code are blank,
because "a blank address would otherwise hash every such record into one block and let a bare name
match propagate an identity across unrelated rows" (`:175-177`). A record with no block id is skipped
entirely (`:246-248`).

**Name half** — `_name_parts` (`:193-224`), returning `(base, legal_form)` and composing three
existing normalisers "each for the one thing it does, rather than a fourth being written"
(`:196-197`): `normalize_key` (lowercase, trim, collapse whitespace, strip punctuation, fold accents),
`tier1_ror._normalise_for_tokens` (canonicalise US legal-entity suffixes — Incorporated→inc,
Corporation→corp, Company→co, Limited→ltd), then `dedup.candidates.strip_legal_suffix`.

**The legal form is returned separately and never folded into the base** — rationale in §1(b).
Compatibility is "identical, or absent on one side" (`:227-229`) and "is not transitive — an absent
form is compatible with every form — so when a block holds two or more DIFFERENT legal forms under one
base name, each form gets its own group and every absent-form row is left on its own"
(`:238-241`). The implementation keys an absent form by the row index, "which no canonical form can
collide with, so it lands in a singleton group and inherits nothing" (`:264-266`).

**The key never leaves the process.** "Both halves of it — the address block id and the canonicalised
name — are dictionary keys and nothing else, under the same contract as Fix 2's cache key: never
written to output, never sent to any API, never placed in an LLM prompt, and never fed to
`_compute_name_score()` or any other scoring path" (`:38-42`).

Only groups with two or more members are processed (`:472`).

### 5.2 · `PROPAGATED_FIELDS` and `NEVER_PROPAGATED`

`PROPAGATED_FIELDS` (`:77-84`) — six fields, on the stated criterion "Organisation-level fields: true
of the legal entity, so they are the same for every row that names that entity at that address"
(`:75-76`):

| Field | Why it is organisation-level |
|---|---|
| `ror_id` | Registry identifier of the entity |
| `lei_id` | Registry identifier of the entity |
| `name1_enriched` | The organisation's name |
| `domain` | The organisation's website |
| `website_url` | Derived from `domain`; "two views of one decision, so they are taken from ONE record and never mixed" (`:381-383`) |
| `record_type` | Classification of the entity |

`NEVER_PROPAGATED` (`:92-109`) — 16 fields. Its existence is itself a design decision: "Listed as data
so the exclusion is readable and testable rather than implied by the absence of a field from
`PROPAGATED_FIELDS`" (`:87-89`). The reason for each exclusion, by class:

| Excluded field(s) | Class | Reason |
|---|---|---|
| `name2_enriched`, `name3_enriched`, `name4_enriched`, `name5_enriched` (`DEPT_ENRICHED_FIELDS`) | Department-level | Two rows at one address can be two different departments of one organisation. The worked case is named in the source: "Rows 12-14 of the demo batch are Stanford at one address with three different departments: they must share Stanford's ROR id and domain and keep their own department name and department domain" (`:89-91`) |
| `department_domain` | Department-level | Same case; it is the department's host, not the organisation's |
| `contact_enriched`, `care_of_enriched`, `email_enriched` | Record-level (person) | A contact belongs to the row, not to the entity. These are also the three fields deliberately outside the write-lock scope — "``contact``, ``care_of`` and ``email`` are deliberately excluded for that reason" (`enrichment/provenance.py:19-24`) — so no personal data enters the propagation path either |
| `search_term_2` | Department-level derived | Mirrors Name 2, which is not propagated. `search_term_1` is *also* absent from `PROPAGATED_FIELDS`, so neither term is propagated; it is not listed in `NEVER_PROPAGATED` either — see the note below |
| `street_cleaned`, `house_number`, `street_2_cleaned`…`street_5_cleaned`, `postal_code`, `city`, `country_region_key`, `region` | Address | These are *inputs to the grouping key*. Propagating them would let one member's address overwrite another's while the group was formed on the assumption they matched, and the key would then no longer describe the data |

⚠ The two lists do not partition the field space, and `NEVER_PROPAGATED` is documentation rather than
enforcement: the propagation loop iterates `PROPAGATED_FIELDS` only (`:496`) and never reads
`NEVER_PROPAGATED`. Its only readers are `tests/test_batch_consensus.py` (imported at `:30`). A field
that is in neither list — `search_term_1`, `po_box_extracted`, the sub-location fields — is not
propagated, but its exclusion is implied by absence, which is the exact property `:87-89` says the
list exists to avoid. Extending `PROPAGATED_FIELDS` without extending `NEVER_PROPAGATED` would not
fail any test that reads only the second list.

### 5.3 · Conflict resolution when members disagree

Three distinct disagreement cases, resolved three different ways (`_consensus_values`, `:324-406`).

**(1) Conflicting registry identities → the group propagates nothing.**

```python
rors = {m.ror_id for m in members if m.ror_id}
leis = {m.lei_id for m in members if m.lei_id}
if len(rors) > 1 or len(leis) > 1:
    return None
```

(`:354-357`). The caller counts it as `telemetry.conflicts`, logs `batch_consensus_conflict` with both
id sets, and moves on (`:478-490`). No partial propagation: two conflicting ROR ids block the name and
the domain as well.

**(2) Exactly one registry identity → `registry` mode; the registry is the authority.** All six fields
propagate. The donor is elected deterministically by `_donor_rank` — "most ids, then earliest tier,
then batch order. Deterministic, so the same batch always elects the same donor" (`:285-289`). The
donor's name "wins outright: after Fix 4 a verified Tier 1 match writes the registry's official name,
which is authoritative for the whole group" (`:370-373`).

**(3) No registry identity → `name_form` mode, "deliberately much weaker"** (`:332`).
`ror_id`/`lei_id` are absent by definition. For `domain`, `website_url` and `record_type` the mode
"never chooses between competing values, it only fills gaps where the group is unanimous"
(`:344-346`) — implemented as `_sole`, which returns the single distinct non-null value or `None`
(`:279-282`). "a member that resolved nothing inherits it and a group that disagrees keeps its
disagreement" (`:349-350`).

`name1_enriched` is the single exception, "and only because its competing values are surface variants
of a name every member already holds — picking one asserts nothing new" (`:346-348`). The winner is
the modal surface form, tie-broken on earliest tier then batch order (`_consensus_name_form`,
`:292-321`), on the stated grounds that "the batch's own majority spelling is evidence and 'which
legal form is more correct' is not a judgement this pass is entitled to make" (`:305-307`).

**Two further resolution rules cut across both modes:**

- *A null never erases a value.* `if new is None or getattr(member, name) == new: continue` (`:497-498`).
  Stated at `:384-386`: "A null donor value never erases a domain another member resolved, and a group
  holding two domains propagates neither."
- *`record_type == "unknown"` is absent, not conflicting.* "'unknown' asserts nothing, so it is treated
  as absent on both sides" (`:397-405`; constant `_UNKNOWN_TYPE` at `:138`). Without this a single
  unresolved record would block the group's type.

**Why `name_form` domain propagation is not a bypass of the ownership guard.** Recorded in the
docstring: "the surviving domain already satisfied the ownership guard on a record whose Name 1 is
equal to the receiving record's after canonicalisation, so the guard's name-similarity condition would
reach the same verdict here — the value is not attributed on weaker evidence than the guard needs, it
is attributed on the same evidence" (`:350-352`).

### 5.4 · `consensus_flags_retracted`

The batch summary counter (`api/models.py:654-656`). Its meaning is exact and narrow:

- It counts **codes withdrawn**, not records: "Counts codes, not records: one record can shed several"
  (`batch_consensus.py:149-152`; the same wording at `api/models.py:654-656`).
- A withdrawal happens only when this pass wrote a *different* `name1_enriched` onto a record
  (`:524-527`), and only for the codes in `_RETRACTED_BY_NAME1[mode]` (§2.1).
- It is a count of `retract`'s return value, which is the codes *actually* removed —
  `telemetry.flags_retracted += len(retract_flags(member, _RETRACTED_BY_NAME1[mode], "name1"))`
  (`:525-527`) — so a code listed for the mode but not present on the record contributes nothing.

The semantics the counter is measuring: "A flag is a statement about a field's value; replacing the
value can make the statement false. Three demo rows spelling one company three ways converge on one
Name 1, and the row whose flag read 'left exactly as supplied' was then not left as supplied — two
identical names, one flagged. So a propagated `name1_enriched` withdraws exactly the codes it
falsified … and nothing else" (`:24-30`). The pass "raises none" (`:23`) — the counter can only ever
describe removals.

### 5.5 · Worked example — two records, before and after

Constructed from the fixture data in `tests/test_batch_consensus.py`: the `TAMPA` address block
(`:44-50`, described there as "The demo batch's Coastal trio address (rows 15-17)") and the donor row
of `test_single_registry_identity_reaches_every_member` (`:74-95`), with the second record carrying
the flags of `test_registry_consensus_answers_no_match_and_unverified_inference` (`:761-774`). Run
through `apply_batch_consensus` at the header commit:

Grouping key, both records:

```
block_id     = blk-a2ffca8e37bf          (both — same street/postal/city/country)
_name_parts  = ('coastal diagnostics', 'inc')   for r15
             = ('coastal diagnostics', '')      for r16
```

Same block, same base, and the legal forms are compatible (`'inc'` vs absent), so one group of two.

**Before:**

| | `r15` | `r16` |
|---|---|---|
| `name1_enriched` | `'Coastal Diagnostics Inc'` | `'Coastal Diagnostics'` |
| `ror_id` | `'ror.org/01abc'` | `None` |
| `domain` | `'coastaldiagnostics.com'` | `None` |
| `record_type` | `'company'` | `'unknown'` |
| `tier_used` | `1` | `3` |
| `source` | `'ROR'` | `'LLM'` |
| `flag_codes` | `[]` | `['no-match', 'low-confidence-unchanged']` |
| `flag_for_review` | `False` | `True` |

**After:**

| | `r15` | `r16` |
|---|---|---|
| `name1_enriched` | `'Coastal Diagnostics Inc'` | `'Coastal Diagnostics Inc'` |
| `ror_id` | `'ror.org/01abc'` | `'ror.org/01abc'` |
| `domain` | `'coastaldiagnostics.com'` | `'coastaldiagnostics.com'` |
| `record_type` | `'company'` | `'company'` |
| `tier_used` | `1` | **`3` — unchanged** |
| `source` | `'ROR'` | `'batch_consensus'` |
| `flag_codes` | `[]` | `[]` |
| `flag_for_review` | `False` | `False` |
| `flag_reason` | `None` | `None` |

Telemetry for the batch:

```
ConsensusTelemetry(groups=1, records_updated=1, conflicts=0,
                   fields_propagated={'ror_id': 1, 'name1_enriched': 1, 'domain': 1,
                                      'website_url': 1, 'record_type': 1},
                   flags_retracted=2)
```

Four things to read off it. `records_updated` is 1, not 2 — the donor inherits nothing and is not
counted (`enrichment/batch_consensus.py:494-528`; asserted at `tests/test_batch_consensus.py:89`,
`:104`). `tier_used` stays `3` on
the record that inherited a Tier 1 identity, per §1(b). `source` becomes `batch_consensus` on the
receiver only. And `flags_retracted` is `2` — `no-match` and `low-confidence-unchanged`, the two codes
present on `r16` that also appear in `_RETRACTED_BY_NAME1["registry"]`
(`enrichment/batch_consensus.py:131-134`). `r16` leaves the batch with no flag at all.

### 5.6 · What this does to per-record independence

**Stated plainly: it ends it.** Before this pass, `/enrich` was a function of a record and its
external lookups. After it, a record's `ror_id`, `lei_id`, `name1_enriched`, `domain`, `website_url`,
`record_type`, `source`, `flag_codes`, `flagged_fields`, `flag_reason` and `flag_for_review` are a
function of the record **and of which other records were submitted in the same batch**. In the §5.5
example, `r16` submitted alone would leave with `ror_id=None`, `domain=None`,
`record_type='unknown'`, and two flag codes; submitted alongside `r15` it leaves with a registry
identity, a domain, a type and no flag. Same input record, two different outputs.

The dependency is not incidental — it is the point of the pass — but it has consequences the thesis
must state:

1. **`/enrich` is not idempotent with respect to batching.** Re-running the same 500 records in
   batches of 50 rather than one batch of 500 can produce different output, because group membership
   is scoped to the call. The ADF Enrichment pipeline drives it in 50-row offsets
   (`CONTEXT-EXTERNAL.md:188-192`), so the production batch boundary is arbitrary with respect to
   organisations: two Stanford rows 60 apart in the Legacy table never meet.
2. **Evaluation figures acquire a batch-composition dependency.** Any metric over per-record output —
   flag rate, registry-id coverage, domain coverage, `record_type` distribution — is now a property of
   the record *set*, not the record. Two evaluations of the same corpus at different batch sizes are
   not comparable.
3. **The counters are the mitigation and they are adequate for reporting, not for control.**
   `consensus_groups`, `consensus_records_updated`, `consensus_conflicts`,
   `consensus_fields_propagated`, `consensus_flags_retracted` (`api/models.py:646-656`) make the
   pass's contribution measurable per batch, and `source="batch_consensus"` marks the affected records
   individually (`:505-510`). So a reader can subtract the pass out of a batch-level figure. Nothing
   turns it off.

**Is it sound?** The argument for soundness that the code makes is a containment argument, and it is
strong on three of four axes:

- *Nothing new is asserted.* In `registry` mode the propagated fact came from a registry that already
  passed its own guard; in `name_form` mode nothing propagates unless the group is unanimous, except a
  surface spelling of a name every member already holds (§5.3). The pass never invents a value.
- *The blast radius is bounded by a conservative key.* An address the pass cannot derive, a base name
  it cannot derive, or two competing legal forms each end in no propagation (§5.1).
- *Disagreement is preserved, not resolved.* Conflicting registry identities propagate nothing at all,
  and are counted (§5.3).
- *But the attribution is only as good as the key, and the key is fuzzy on one side.* The address half
  is a hash of cleaned fields; two genuinely different organisations sharing a business-park address
  and a base name after legal-suffix stripping would group. The code's answer is the legal-form split
  and the singleton fallback (`:238-241`), which is a partial answer: two rows with the *same* legal
  form at the same address and the same base name will group whether or not they are the same entity.
  This is precisely the judgement the module says belongs to Phase 2 (`:211-212`), and in the one case
  where it is unavoidable — same base, same form, same address — the pass makes it anyway.

**The thesis must say all four.** The honest formulation is that batch consensus trades per-record
independence for intra-batch consistency, that the trade is bounded and instrumented, and that the
residual risk is a same-address/same-base-name/same-legal-form collision which the pass resolves
rather than defers. ⚠ MEASUREMENT REQUIRED for the residual: the rate of such collisions on the demo
corpus is not recorded anywhere. `consensus_groups` over a full run of `PresentationTestData.xlsx`,
with each group's member `record_id`s logged (the `batch_consensus_inherit` log line already carries
them, `:533-543`), would give it.

---

## §6 · Tier 2A verification as it now runs

### 6.1 · The gate

Computed once, before the Tier 2 canonical block (`enrichment/orchestrator.py:3529-3534`):

```python
can_do_contact_lookup = (
    result["routing_type"] == "research_institution"
    and bool(pp_contact and pp_contact.strip())
    and not multi_contact
    and bool(institution_domain)
)
```

Four conditions, none of which mentions Name 2. The comment above it states why it is computed here
rather than at the Tier 2A block: "This must be known BEFORE the canonical short-circuit below, because
that short-circuit returns for every record with a populated Name 2 — which is exactly the population
Tier 2A verification mode needs to see. Computed once here and consumed both by the short-circuit and
by the Tier 2A gate further down" (`:3522-3528`).

Note the first condition reads `routing_type`, the provisional type of §1(c) — the gate must run before
`record_type` exists.

**What changed.** At the baseline the same expression carried a fifth clause, `not
name2_already_filled`, and read `result["record_type"]` (`git show 515cc7c:enrichment/orchestrator.py`,
lines 2451-2457). Commit `a9ea79b` states the defect: "the contact-lookup gate at orchestrator.py
required `not name2_already_filled`, while tier2a_contact.py's mode selector requires name2 to be
POPULATED to choose verification. No input satisfied both."

### 6.2 · The mode selector

`enrichment/tier2a_contact.py:90`:

```python
mode = "2A_population" if is_blank(name2) else "2A_verification"
```

One line, one input. Mode A populates a blank Name 2 from the contact's page; Mode B verifies or
corrects a populated one (`:2-6`, `:86-88`). The orchestrator comment restates the split and gives the
authority claim: "population when Name 2 is blank, verification when it is populated (the contact's
page is the authority on which unit they actually sit in)" (`orchestrator.py:3644-3646`).

Both modes share the whole retrieval path — one on-domain quoted-name SERP query (`:295-309`), a
deterministic both-names filter on the SERP result before any fetch (`:104-116`, `:247-268`), top-3
candidates fetched, and an LLM extraction over a merged page blob (`:119-150`). A `low` extraction
confidence causes the candidate to be skipped entirely before either mode runs (`:160-163`), so the
band logic below sees only `high` or `medium` — stated as a precondition in `_apply_mode_b`'s docstring
(`:459-461`).

### 6.3 · The three bands and their constants

`_apply_mode_b` (`enrichment/tier2a_contact.py:442-531`). The score compared against the bands is

```python
effective_score = max(float(llm_score), our_score)
```

(`:491`), where `our_score = fuzz.token_sort_ratio(existing_name2, official_dept)` (`:475`) and
`llm_score` is `extraction["name2_match_score"]` (`:471`).

Two constants, both on a 0–100 scale:

| Constant | Value | Defined at | Consumed at |
|---|---|---|---|
| `fuzzy_threshold` (`Settings.fuzzy_match_threshold`, env `FUZZY_MATCH_THRESHOLD`) | `80` | `config.py:226-228` | `tier2a_contact.py:493` |
| exact-band cut-off | `95`, hardcoded | `tier2a_contact.py:498` | same |

| Band | Test | What it writes | Cite |
|---|---|---|---|
| **Exact** | `effective_score >= 95` | `name2_enriched = official_dept.strip()`; `name2_match = "exact"`; `enrichment_status = "verified"`; `source = "contact_lookup_found"`; `name2_match_score = effective_score` | `:493-502` |
| **Partial** | `fuzzy_threshold <= effective_score < 95` | `name2_enriched = official_dept.strip()` ("Partial match — normalise to the page's wording"); `name2_match = "partial"`; `enrichment_status = "enriched"`; `source = "contact_lookup_found"` | `:493-507` |
| **Sub-threshold** | `effective_score < fuzzy_threshold` | `name2_match = "no_match"`, then splits — see below | `:508-527` |

In every band `name3_enriched` is set from `official_group` when present (`:529-530`), and `title` is
carried from the extraction (`:171`).

### 6.4 · The confidence split below the threshold

The sub-80 branch splits on the *extraction* confidence, not on the score:

| Branch | Condition | What it writes | Cite |
|---|---|---|---|
| **High** | `llm_confidence == "high"` | `name2_enriched = official_dept.strip()` — the record is treated as wrong and replaced; `enrichment_status = "enriched"`; `source = "contact_lookup_corrected"` | `:512-518` |
| **Medium** | otherwise | `name2_enriched` left **unset**; `enrichment_status = "unresolved"`; `source = "contact_lookup_found"`; `low_conf_unchanged.add("name2")` | `:519-527` |

Rationale, from the method docstring: "< 80: the page disagrees substantially with the record. That has
two explanations — the record is wrong, or the wrong page was found — and this is precisely where the
evidence is weakest. Split on the extraction confidence … only a high-confidence extraction is trusted
enough to overwrite. A medium-confidence disagreement is reported and left for a human, with the
record's own value untouched" (`:456-462`).

The medium branch's mechanism is a deliberate use of an existing merge property: "Leave
`name2_enriched` unset — the merge layer's non-blank filter then keeps whatever the record already had
— and surface the disagreement for manual review instead" (`:520-524`). The surfacing is
`low_conf_unchanged`, which is explicitly evidence and not a flag: "Evidence, not a flag. …
`enrichment.flags` turns this into `low-confidence-unchanged`; every other Tier 2A outcome writes a
value backed by `source_url` and needs no flag" (`:62-65`). The orchestrator forwards it at
`orchestrator.py:1287-1290`. This is the one Tier 2A outcome that produces a review flag.

`a9ea79b` records that the status value was a judgement call: "`enrichment_status='unresolved'` on the
medium branch is a judgment call: the instruction did not specify it, 'enriched' would claim an
enrichment that did not happen, and 'failed' would contradict `success=True`."

**A cross-scale defect is documented in place and deliberately not fixed.** The `max()` at `:491`
"ranks one against the other and then thresholds the winner with a single number (`fuzzy_threshold`,
and again at 95), so an LLM saying '92' outranks a measured 88 on no common footing. Left as-is
deliberately … and reported so the decision to fix it can be taken on its own terms" (`:481-490`).
This is `11_DELTA.md` §3.11 G-78.

### 6.5 · Worked example per band

Run against the repository's Tier 2A mocks (`tests/mocks/openai_mock.py`, `serp_mock.py`,
`page_mock.py`) with default `Settings()`, i.e. `fuzzy_match_threshold = 80`. The three inputs are the
ones the test suite uses: `tests/test_tier2a_verification.py:101-124` (exact), `:127-154` (partial),
`:71-98` (sub-80 / high), `:231-262` (sub-80 / medium).

```
fuzzy_match_threshold = 80

exact    in='Department of Chemistry' -> mode=2A_verification score=100.0
         match=exact  out='Department of Chemistry'  status=verified
         source=contact_lookup_found  low_conf=set()

partial  in='Dept of Radiology'       -> mode=2A_verification score=85.0
         match=partial  out='Department of Radiology'  status=enriched
         source=contact_lookup_found  low_conf=set()

sub-80   in='Dept of AI'              -> mode=2A_verification score=48.484848484848484
         match=no_match  out='Department of Chemistry'  status=enriched
         source=contact_lookup_corrected  low_conf=set()
```

Reading them:

- **Exact.** Contact `Dr. Jane Smith` at `mit.edu`, record Name 2 `"Department of Chemistry"`, page
  reports the same. `token_sort_ratio == 100.0 ≥ 95` → the record already agreed with the page, so it
  is marked `verified` and nothing is left for a reviewer.
- **Partial.** Contact `Dr. Robert Chen` at `jhu.edu`, record Name 2 `"Dept of Radiology"`, page
  reports `"Department of Radiology"`. `token_sort_ratio == 85.0`, inside `[80, 95)` → same unit,
  non-canonical wording; the value is normalised to the page's form and the status is `enriched`.
- **Sub-80, high confidence.** Same contact and domain as the exact case, record Name 2 `"Dept of
  AI"`. `token_sort_ratio == 48.48`, below 80 → `no_match`. The curated mock reports
  `confidence="high"` (`tests/mocks/openai_mock.py:22-26`), so the record is treated as wrong and
  overwritten; `source` becomes `contact_lookup_corrected`.
- **Sub-80, medium confidence.** The same input, with the extraction confidence forced to `medium` by
  `_ConfidenceStubOpenAIClient` (`tests/test_tier2a_verification.py:31-64`), because "Every curated
  Tier 2A entry in the shared mock returns `confidence: "high"`, so the medium-confidence half of the
  sub-80 band is unreachable through it" (`:32-38`). Asserted outcome: `name2_enriched is None`,
  `enrichment_status == "unresolved"`, `source == "contact_lookup_found"`,
  `low_conf_unchanged == {"name2"}` (`:252-262`).

### 6.6 · What the `canonical_short_circuit` deferral changed

**Before.** The short-circuit was an unconditional early return: `if any_canonical_ran and
name2_already_filled: return …`. `a9ea79b` states its effect: "even with [the gate clause] dropped, the
Tier 2 canonical short-circuit … returned before the contact-lookup gate was evaluated, so a record
whose Name 2 canonicalised never reached Tier 2A at all." Two independent gates therefore had to be
opened; opening either alone changed nothing.

**After.** The condition gained a deferral clause (`orchestrator.py:3636-3640`):

```python
canonical_short_circuit = any_canonical_ran and name2_already_filled
if canonical_short_circuit and not can_do_contact_lookup:
    return await self._return_canonical_short_circuit(result, start, record, cache)
```

and the body it used to inline was extracted into `_return_canonical_short_circuit`
(`:2337-2375`), which is now called from **two** sites: `:3638` (record ineligible for contact
verification) and `:3741` (Tier 2A ran and produced nothing usable).

**What that buys, and the invariant that makes it safe.** The safety argument is recorded at the call
site: "Deferring is safe: every path out of the Tier 2A block below either returns with a 2A result or
falls back to this same short-circuit, so a record that would have stopped here never reaches Tier 3"
(`:3632-3635`). The method docstring states the same from the other end: "Stopping here is what keeps
Tier 3 from overwriting a canonical unit name with a fabricated one" (`:2346-2348`).

**Measured effect, from `a9ea79b`, not recomputed here:** "Fixture suite before -> after:
2A_verification 0 -> 3 records. BSP_2000005 (JHU) reaches it only because of the second gate. Two
records that previously fell to Tier 3 and were handed a fabricated 'Division of General Research' now
get their real department from the contact's page."

So the deferral changed three things: it made a previously unreachable mode reachable for records
whose Name 2 had just been canonicalised; it converted a hard early return into a fall-through with a
guaranteed rejoin, which is what allows the deferral without a Tier 3 leak; and it replaced two
fabricated departments with page-sourced ones on the fixture suite.

**A scope note.** `a9ea79b` also records something the change deliberately did *not* do: "No issue-
catalogue code expresses 'Name 2 disagrees with an external source'; G5-NAME-002 is about form, not
agreement. Not added here, since the module is deliberately deterministic and record-local." The
disagreement surfaces only as `low-confidence-unchanged` in the flag column, never in `/issues`.

---

## §7 · Chapter 2 figures

### 7.1 · Command and verbatim output

```
$ ./.venv/Scripts/python.exe scripts/ch02_measure.py
```

Exit code **1**. Full stdout+stderr, verbatim, 172 lines:

```
==============================================================================
Chapter 2 measurement run
==============================================================================

##############################################################################
# 1 - FIELD POPULATION (structure evidence)
##############################################################################

dataset : PresentationTestData.xlsx  (500 rows, 53 columns)

--- 1.1 Populated rate of every column the model maps ---
SAP column                 | model field              | populated |      %
---------------------------+--------------------------+-----------+-------
Customer                   | customer                 |       500 | 100.0%
ECC Customer Number        | ecc_customer_number      |         0 |  0.0%
Central Deletion Flag      | central_deletion_flag    |         0 |  0.0%
Comments                   | comments                 |         0 |  0.0%
Account group              | account_group            |       500 | 100.0%
Company Code               | company_code             |         0 |  0.0%
Sales Organization         | sales_organization       |         0 |  0.0%
Distribution Channel       | distribution_channel     |         0 |  0.0%
Division                   | division                 |         0 |  0.0%
Name 1                     | name_1                   |       495 | 99.0%
Name 2                     | name_2                   |       160 | 32.0%
Name 3                     | name_3                   |        13 |  2.6%
Name 4                     | name_4                   |         3 |  0.6%
Contact                    | contact                  |        98 | 19.6%
Street 1                   | street_1                 |       500 | 100.0%
House Number               | house_number             |       221 | 44.2%
Street 2                   | street_2                 |        35 |  7.0%
Street 3                   | street_3                 |         4 |  0.8%
Street 4                   | street_4                 |         4 |  0.8%
Street 5                   | street_5                 |         4 |  0.8%
PO Box                     | po_box                   |         5 |  1.0%
Country/Region Key         | country_region_key       |       495 | 99.0%
Postal Code                | postal_code              |       495 | 99.0%
City                       | city                     |       500 | 100.0%
Region                     | region                   |       482 | 96.4%
Language Key               | language_key             |       495 | 99.0%
Reconciliation acct        | reconciliation_acct      |         0 |  0.0%
Tax Jurisdiction           | tax_jurisdiction         |       175 | 35.0%
Central delivery block     | central_delivery_block   |         0 |  0.0%
Delivery Priority          | delivery_priority        |         0 |  0.0%
Shipping Conditions        | shipping_conditions      |         0 |  0.0%
Delivering Plant           | delivering_plant         |         0 |  0.0%
Created On                 | created_on               |         0 |  0.0%
Created By                 | created_by               |         0 |  0.0%
VAT Registration No.       | vat_registration_no      |       271 | 54.2%
Search Term 1              | search_term_1            |         0 |  0.0%
Search Term 2              | search_term_2            |         0 |  0.0%

--- 1.2 Columns present in the file that the model does not map ---
    (accepted and silently discarded: no extra='forbid' is declared,
     api/models.py:40)
    Terms of Payment
    Sales_Order_Last_Used
    Sales_Order_Total_Count
    Sales_Order_Partner_Last_Used
    Sales_Order_Partner_Total_Count
    Equipment_Total_Count
    SleepingCustomer
    CustomerStatus
    SF_ID_Biosystems
    SF_ID_AXS
    SF_ID_3
    SF_ID_4
    SF_ID_5
    SF_ID_6
    SF_ID_7
    SF_ID_8

##############################################################################
# 3 - ISSUE FREQUENCY
##############################################################################

dataset          : PresentationTestData.xlsx
path             : PresentationTestData.xlsx
sheet            : first (active) sheet, header row 1
columns in file  : 53
model fields seen: 37  (drives G2-VAL-* column gating)

--- 3.1 Totals ---
total records read from the data          : 500
records with >= 1 issue                   : 500  (100.0%)
records with 0 issues                     : 0  (0.0%)
total issue instances (code x record)     : 1454
mean issue codes per record (all records) : 2.91
mean issue codes per affected record      : 2.91

--- 3.1b Same totals excluding G2-VAL-007 (Search Term 1 Missing) ---
records with >= 1 other issue             : 443  (88.6%)
records with no other issue               : 57  (11.4%)
issue instances excluding G2-VAL-007     : 954
mean per record (all)                     : 1.91
mean per affected record                  : 2.15
 codes on record |  records |  % of 500
-----------------+----------+----------
               0 |       57 |    11.4%
               1 |      120 |    24.0%
               2 |      188 |    37.6%
               3 |       93 |    18.6%
              4+ |       42 |     8.4%

--- 3.2 Issue-count distribution per record ---
 codes on record |  records |  % of 500
-----------------+----------+----------
               1 |       57 |    11.4%
               2 |      120 |    24.0%
               3 |      188 |    37.6%
               4 |       93 |    18.6%
               5 |       33 |     6.6%
               6 |        8 |     1.6%
               8 |        1 |     0.2%
-----------------+----------+----------
               1 |       57 |    11.4%
               2 |      120 |    24.0%
               3 |      188 |    37.6%
              4+ |      135 |    27.0%

--- 3.3 Per-code frequency, ranked by records affected ---
rank | code            | grp | records |  % of 500 | name
-----+-----------------+-----+---------+-----------+---------------------------------------------
   1 | G2-VAL-007      | G2  |     500 |   100.0% | Search Term 1 Missing
   2 | G2-VAL-003      | G2  |     325 |    65.0% | Tax Jurisdiction Missing
   3 | G1-ADDR-001     | G1  |     203 |    40.6% | House Number Embedded in Street
   4 | G5-NAME-001     | G5  |     101 |    20.2% | Organisation Name Not in Official Form
   5 | G1-ADDR-003     | G1  |      48 |     9.6% | Sub-location Embedded in Street
   6 | G5-NAME-002     | G5  |      48 |     9.6% | Unit Name Not in Official Form
   7 | G4-ADDR-026     | G4  |      29 |     5.8% | Postal Code Format Invalid
   8 | G2-NAME-012     | G2  |      25 |     5.0% | Research Institution Missing Department
   9 | G1-CROSS-002    | G1  |      18 |     3.6% | Org Name in Address Field
  10 | G2-VAL-004      | G2  |      18 |     3.6% | Region Missing
  11 | G1-ADDR-004     | G1  |      17 |     3.4% | PO Box Embedded in Street
  12 | G1-ADDR-006     | G1  |      13 |     2.6% | Mail Code in Street Field
  13 | G4-ADDR-008     | G4  |      12 |     2.4% | Bare Sub-location Marker Without Value
  14 | G1-ADDR-011     | G1  |       9 |     1.8% | Department Label in Street Field
  15 | G1-CROSS-003    | G1  |       9 |     1.8% | Contact Information in Wrong Field
  16 | G1-NAME-004     | G1  |       7 |     1.4% | Empty field in between populated name fields
  17 | G1-CROSS-001    | G1  |       6 |     1.2% | Address Content in Name Field
  18 | G1-NAME-013     | G1  |       6 |     1.2% | SAP Internal Code in Name Field
  19 | G2-NAME-009     | G2  |       6 |     1.2% | Lab Without Department
  20 | G3-NAME-003     | G3  |       6 |     1.2% | DBA Pattern in Name Field
  21 | G3-NAME-005     | G3  |       6 |     1.2% | Duplicate Name Across Fields
  22 | G4-ADDR-027     | G4  |       6 |     1.2% | Country Code Not ISO 2-letter
  23 | G2-VAL-001      | G2  |       5 |     1.0% | Name 1 Missing
  24 | G2-VAL-002      | G2  |       5 |     1.0% | Postal Code Missing
  25 | G2-VAL-006      | G2  |       5 |     1.0% | Language Missing
  26 | G2-VAL-008      | G2  |       5 |     1.0% | Country Missing
  27 | G3-ADDR-005     | G3  |       5 |     1.0% | Multiple PO Boxes on Record
  28 | G3-CONTACT-007  | G3  |       5 |     1.0% | Multiple Contacts on Record
  29 | G3-ADDR-014     | G3  |       3 |     0.6% | PO Box and Street Both Present
  30 | G4-NAME-015     | G4  |       3 |     0.6% | Name Overflow Beyond the Name Block

codes observed in this dataset : 30 of 38 declared
codes never observed here      : 8
    G1-NAME-001     Name Overflow Across Fields
    G1-ADDR-009     Unclassified Residual in Address
    G2-CONTACT-008  No Contact and No Department
    G2-CONTACT-009  Department Missing And Enrichable from Contact
    G3-ADDR-012     Duplicate Street Across Fields
    G3-ADDR-013     Two Distinct Street Addresses on Record
    G4-ADDR-025     Sub-location Overflow Beyond Street 5
    G7-VERIFY-001   Enriched Record Requires Verification

--- 3.4 Distinct SAP columns implicated per record ---
  !! locator/detector divergence on record '90000312': detector=['G5-NAME-001'] locator=[]
  !! locator/detector divergence on record '90000251': detector=['G5-NAME-001'] locator=[]
  !! locator/detector divergence on record '83000044': detector=['G1-ADDR-006'] locator=[]
  !! locator/detector divergence on record '85000081': detector=['G2-NAME-012'] locator=[]
  !! locator/detector divergence on record '83000046': detector=['G1-ADDR-006'] locator=[]
locator fidelity self-check: 492/500 rows agree with detect_issues
  ABORT: locator does not mirror the detector; field counts are not reportable.
```

### 7.2 · The run does not complete

**The script aborts at §3.4 and exits 1.** The self-check compares the per-code `locate()` mapping
against `detect_issues` on every row; 8 of 500 rows disagree, and on a mismatch the script prints up
to five divergences and calls `sys.exit(1)` (`scripts/ch02_measure.py:442-454`).

The consequence for the thesis is that **three of the six sections never ran**. `main()`
(`:674-707`) is ordered §1 → §3 → §3.5 → §5 → §6, so everything after the abort is absent from the
output above:

| Section | Function | Produced? |
|---|---|---|
| §1 Field population | `measure_field_population` (`:476`) | yes |
| §3.1–§3.3 Issue frequency | `measure_issues` (`:364`) | yes |
| §3.4 Distinct columns implicated | same function, `:442-473` | **no — aborted** |
| §3.5 The workbook's own oracle, compared | `measure_oracle_delta` (`:507`) | **no — never reached** |
| §5 Duplicate prevalence | `measure_duplicates` (`:566`) | **no — never reached** |
| §6 Enrichment → dedup coupling (registry identifiers) | `measure_registry_ids` (`:628`) | **no — never reached** |

(There is no §2 or §4 in the script; the numbering follows the thesis chapter, not the file.) §6 is
the only section that reads the enriched workbook at all (`:703`), so at HEAD **no figure in this run
derives from `PresentationTestData_enriched_checked_v1.xlsx`**.

⚠ The five named divergences are all one-directional — `detector=[…] locator=[]`, i.e. the detector
raises a code the locator cannot attribute to a column. The three codes involved are `G5-NAME-001`,
`G1-ADDR-006` and `G2-NAME-012`. `G2-NAME-012`'s locator was written against the whole-block rule that
d4fc469 reverted (§9.1), which is consistent with at least that one divergence being a direct
consequence of the revert; whether the `G5-NAME-001` and `G1-ADDR-006` divergences share a cause is
not determinable from the output, and the script stops after five. ⚠ MEASUREMENT REQUIRED: raise the
print cap at `:448` and re-run to enumerate all 8.

**These figures supersede `ch02_SOURCE.md`'s §1 and §3.1–§3.3 and nothing else.** `11_DELTA.md` §6 U-1
listed every dataset-derived figure as unverifiable because both workbooks changed. §1 and §3.1–§3.3
above are now re-derived at HEAD against the current raw workbook and can be used. §3.4, §3.5, §5 and
§6 of `ch02_SOURCE.md` remain unverifiable, now for a second and more concrete reason: the script that
produces them does not run to completion.

### 7.3 · The workbook it read

`RAW_WORKBOOK = ROOT / "PresentationTestData.xlsx"` (`scripts/ch02_measure.py:104`); the run's own
header confirms it (`path : PresentationTestData.xlsx`, `sheet : first (active) sheet, header row 1`).
The active sheet is named `TestData_500`.

| File | Size (bytes) | Last modified | Created | Rows × cols (incl. header) |
|---|---:|---|---|---|
| `PresentationTestData.xlsx` | 128 040 | 2026-08-20 18:45:30 | 2026-08-03 08:35:11 | 501 × 53 |
| `PresentationTestData_enriched_checked_v1.xlsx` | 116 238 | 2026-08-20 18:45:30 | 2026-08-09 01:44:14 | 501 × 77 |

(`Get-ChildItem … | Select-Object Name,Length,LastWriteTime,CreationTime`; dimensions from `openpyxl`
`ws.max_row` / `ws.max_column`.) Both sizes match the post-change figures in `11_DELTA.md` §1.14, and
both `LastWriteTime` values fall inside the commit range. `ENRICHED_WORKBOOK` (`:105`) is declared but,
per §7.2, never read in this run.

### 7.4 · Exemplar resolution — all thirteen workbook exemplars still resolve

`03b_EXEMPLARS.md` cites 13 exemplars by sheet row in `PresentationTestData.xlsx` (REC-01…REC-05,
REC-08…REC-15) and 2 by JSON fixture (REC-06 at `:242`, REC-16 at `:481` ff.). REC-07 is not used.
Every workbook exemplar was checked at its cited row against the cited field values.

| Exemplar | Sheet row | Cited Name 1 | Resolves? |
|---|---:|---|---|
| REC-01 | 76 | `Photon Labs 4200 Research Blvd Suite 210` | ✔ |
| REC-02 | 112 | `FDA - FOOD & DRUG ADMINISTRATION` | ✔ |
| REC-03 | 124 | `DENTSPLY DETREY GMBH` | ✔ |
| REC-04 | 57 | *(empty)* | ✔ |
| REC-05 | 69 | `University of Florida` | ✔ |
| REC-08 | 20 | `Suncoast Medical` | ✔ |
| REC-09 | 87 | `Tropical Pharma Inc` | ✔ |
| REC-10 | 50 | `Gulf Coast Labs` | ✔ |
| REC-11 | 244 | `The Regents of the University of California San Francisco` | ✔ |
| REC-12 | 70 | `NovaBio` | ✔ |
| REC-13 | 40 | `TransGlobal Pharma` | ✔ |
| REC-14 | 41 | `Cardinal Labs` | ✔ |
| REC-15 | 22 | `NATIONAL INSTITUTE OF STANDARDS AND TECHNOLOGY-NIST` | ✔ |

Every other raw field the document quotes for those rows also matches: Name 2, Name 3, Name 4, Street
1, Street 2, House Number, PO Box, Postal Code, City, Region, Country/Region Key and Contact were
compared field by field with 0 mismatches. The single apparent discrepancy — REC-08's Contact reads
`"Dr. Jane Smith; Prof. Bob Lee"` in the file against `"Person-A; Person-B"` in the document — is the
document's own anonymisation scheme, which replaces "person names in contact fields by `Person-A` …
`Person-C`" (`03b_EXEMPLARS.md:37-38`), not a data change.

The enriched counterparts also resolve. REC-01 is the only exemplar whose enriched row is cited by
sheet row (`:106-107`); at enriched row 76 the side-by-side columns read `Name 1: ['Photon Labs 4200
Research Blvd Suite 210', 'Photon Labs']`, `Street 1: ['RESEARCH BLVD', 'RESEARCH Blvd']`, `Street 2:
[None, '4200 Research Blvd']`, `Search Term 1: ['photon']`, `Search Term 2: ['Accounts Payable']`,
`Domain: ['https://www.photon.com']` — every value the document's table quotes, in the column layout
`03b_EXEMPLARS.md:63-73` describes. REC-15's enriched row 22 likewise matches (`Name 1: […,
'National Institute of Standards and Technology-nist']`, `Name 2: ['Dept. of Physics', 'Department of
Physics']`).

**No exemplar fails to resolve.** The seven the thesis quotes are safe as cited.

**Two notes that do not affect resolution but affect what the exemplars now demonstrate.**

1. REC-01's enriched `Domain` cell holds `https://www.photon.com` — a full URL with a scheme and a
   `www.` host. That is the *pre*-`4645b33` column semantics (`11_DELTA.md` §2 B-1). The workbook
   therefore predates the domain change, and any statement in `03b_EXEMPLARS.md` about post-pipeline
   Domain values describes a superseded contract. The exemplar still resolves; what it exemplifies has
   changed.
2. REC-06's cited codes include `G2-CONTACT-009`, and its §6.3 text turns on the unreachability of
   `G2-CONTACT-008`. Both are `status="withdrawn"` at HEAD and can no longer be raised
   (`11_DELTA.md` §3.5). The fixture file still exists and the record still resolves; the code list
   does not.

---

## §8 · The merge-back contract gap

### 8.1 · The gap, stated as a contract fact

`RESPONSE_COLUMNS` defines 65 output columns (`api/output_columns.py:22-110`). `dbo.usp_merge_legacy_
enriched` binds 32 of them in its `OPENJSON … WITH` clause and assigns 31 into `dp_legacy.test_77.
Legacy` in the `WHEN MATCHED THEN UPDATE SET` clause (`sql/usp_merge_legacy_enriched.sql`; `Customer`
is bound as the join key and never assigned). Verified by parsing the procedure text against the
mapping: 32 JSON paths, 31 distinct `tgt.[…]` targets, 0 bound names absent from `RESPONSE_COLUMNS`.

Nine **enrichment-derived** columns are emitted and unbound:

| Column | Emitted at | Bound by the procedure? |
|---|---|---|
| `Name 5` | `api/output_columns.py:38` | no |
| `Flag Codes` | `:87` | no |
| `Flagged Fields` | `:88` | no |
| `Name 1 Provenance` | `:104` | no |
| `Name 2 Provenance` | `:105` | no |
| `Domain Provenance` | `:106` | no |
| `Record Type Provenance` | `:107` | no |
| `ROR ID Provenance` | `:108` | no |
| `LEI ID Provenance` | `:109` | no |

**A tenth enrichment-derived column is also unbound and is not named in `11_DELTA.md` §3.7:
`Error`** (`api/output_columns.py:90`). It carries the exception text on a record that failed
(`api/models.py:443`; written at `enrichment/orchestrator.py:1507`). A batch in which some records
raise therefore merges back the surviving fields of the failed rows and drops the only column that
says they failed.

For completeness, 33 of the 65 columns are unbound in total. The other 23 are SAP master-data columns
the API carries through verbatim without modifying — `ECC Customer Number`, `Central Deletion Flag`,
`Comments`, `Account group`, `Company Code`, `Sales Organization`, `Distribution Channel`, `Division`,
`Country/Region Key`, `Postal Code`, `City`, `Region`, `Language Key`, `Reconciliation acct`, `Tax
Jurisdiction`, `Central delivery block`, `Delivery Priority`, `Shipping Conditions`, `Delivering
Plant`, `Created On`, `Created By`, `VAT Registration No.`, `Terms of Payment`. Not writing those back
is correct: Legacy already holds them and the merge would be a no-op. That is why the gap is the ten
derived columns and not the thirty-three.

**One name mismatch worth recording.** `Unloading Point` is bound as `[Unloading] NVARCHAR(100)
'$."Unloading Point"'` and assigned to `tgt.[Unloading]` — so the Legacy column is named `Unloading`,
not `Unloading Point`. The mapping is correct; the names differ across the boundary.

### 8.2 · The precise consequence

**The platform never sees any of the ten.** The merge procedure is the only write path from `/enrich`
into Legacy — ADF's `Merge Back` activity calls it with the whole `/enrich` response as one string
payload, and it is the only enrichment write in the pipeline (`CONTEXT-EXTERNAL.md:190-192`,
`:325`, `:422`). A column the procedure does not bind is parsed out of the payload by `OPENJSON`'s
`WITH` clause and discarded before the `MERGE` runs. Nothing downstream of Legacy can recover it:
DATAshaper's table progression is Import → Legacy → Validation → load file
(`CONTEXT-EXTERNAL.md:343`), so Validation is populated from Legacy and inherits the gap.

Concretely, for each:

| Column | What the platform gets instead |
|---|---|
| `Name 5` | Nothing. `Name 1`–`Name 4` are bound (`sql/usp_merge_legacy_enriched.sql`), so a five-slot record loses its fifth slot at the boundary. Any content preprocessing packed into Name 5 — and `preprocess` does pack leftward into whatever slot is free — is dropped |
| `Flag Codes` | Nothing. The machine-readable triage vocabulary of §2 does not reach the platform at all |
| `Flagged Fields` | Nothing. The field scope does not reach the platform as a column |
| The six `*_provenance` scalars | Nothing |
| `Error` | Nothing. A failed record merges back as a partially-populated row with no indication it failed |

**What *does* reach the platform, and why the flag redesign is therefore not lost.** `Flag for Review`
(BIT) and `Flag Reason` (NVARCHAR(500)) are both bound and assigned unconditionally — note they are
assigned with a bare `= src.[…]`, not the `COALESCE(NULLIF(…),tgt.[…])` non-blank filter every name and
address column uses, so a false/null flag *does* overwrite. This is why §2.2's duplication of the field
scope into the reason prose matters operationally rather than cosmetically: `flags.py:37-40` says the
scope is repeated "so a consumer that reads only the two pre-Fix-8 columns still learns which field is
in doubt", and at the Legacy boundary those two columns are literally all there is. The design
anticipated exactly this gap.

**Two second-order consequences.**

1. **`Flag Reason` is NVARCHAR(500) and `flag_reason` is unbounded.** A multi-code reason concatenates
   full prose clauses joined by `"; "` (§2.2). The three-code example in §2.2 renders to **401
   characters** (measured). Enumerating every code combination with *no* scope prefix — the shortest
   possible rendering of each — gives these bounds over `_REASONS` (`flags.py:113-162`):

   | Codes on a record | Shortest rendering | Longest rendering |
   |---:|---:|---:|
   | 1 | 70 | 181 |
   | 2 | 148 | 330 |
   | 3 | 236 | 473 |
   | 4 | 338 | **612** |
   | 5 | 447 | **724** |

   So **four codes can already exceed NVARCHAR(500)**, and a scope prefix (up to
   `"Name 1, Name 2, Name 3, Name 4 and Name 5: "`, 43 characters) only adds to it. The procedure does
   not truncate — it assigns `src.[Flag Reason]` directly — so an over-length value is a SQL Server
   truncation error or a silent truncation depending on `ANSI_WARNINGS`, not a caught condition.
   ⚠ MEASUREMENT REQUIRED: the observed maximum over a full run of the demo corpus, and how many
   records carry four or more codes. Nothing in the repository records either, and with `Flag Codes`
   unbound there is no shorter column to fall back to.
2. **G7-VERIFY-001 survives the gap, and is the only part of the flag model that does.** §3.2's chain
   runs `flag_for_review` → merge → Legacy → `/issues` reads the cell back. Since `Flag for Review` is
   bound, the chain is intact. Had it been unbound, `G7-VERIFY-001` could never fire on a
   platform-produced file.

### 8.3 · Does any DATAshaper validation rule or view depend on one?

**No dependency is recorded, and the recorded dependencies are on columns that *are* bound.** What the
repository evidences:

- DS validation rules "read the issues column produced by the `/issues` endpoint, and DS additionally
  applies its own rules independent of that column" (`CONTEXT-EXTERNAL.md:359-360`). The issues column
  is a single appended column of semicolon-separated bare codes (`README.md:1625`) and is not one of
  the ten.
- The DS issues view drills "from issue code to affected field to description"
  (`CONTEXT-EXTERNAL.md:364-365`), and the note at `:380-381` states "the issues column must encode
  the affected field, not only the issue code". That is a requirement on the issues column, not on
  `Flagged Fields` — and it is an open question in that document, flagged at `:383-385` as needing to
  be read from the code that builds it.
- The DS deduplication view's columns are `Cluster`, `Code`, `Reason`, `Cluster_ID`, `Block ID`,
  `Signature` (`:389`), populated by `usp_merge_validation_clusters`, which binds all six
  (`sql/usp_merge_validation_clusters.sql`). None of the ten appears.

So: **the gap costs the platform ten columns and breaks no recorded rule or view.** The exposure is
forward-looking rather than current — the DS issues view's field-level drill-down is the one place a
`Flagged Fields` column would be the natural source, and it is currently served from the issues column
instead.

⚠ Whether the gap is an oversight or a decision is not recorded (`11_DELTA.md` §6 U-5, unchanged).
The procedure is a verbatim export of a deployed object, and the three `sql/*.sql` files entered the
repository in commit `ecac51a` ("Stored proceedures backup", 2026-08-19) — *before* `b8ad102` (five
name slots), `5e423c2` (flag model) and `59d3e4d` (provenance), all dated 2026-08-20. The export
therefore predates all nine of the derived columns it does not bind, which is consistent with an
un-updated deployment and is not evidence of intent either way. `Error` is the exception: it predates
this commit range entirely, so its absence has no such explanation.

---

## §9 · The two new test failures

Both were re-run at the header commit:

```
$ ./.venv/Scripts/python.exe -m pytest -q \
    tests/test_name_slot_parity.py::TestIssueDetectionAppliesToEverySlot::test_department_in_a_lower_slot_is_not_reported_missing \
    tests/test_routes.py::TestRoutes::test_issues_compare_segments_g6_and_g7_out_of_the_metric
…
2 failed in 2.72s
```

### 9.1 · `test_department_in_a_lower_slot_is_not_reported_missing` — **the test is wrong**

**`11_DELTA.md` §5 judges this "a genuine unfixed defect, not a stale test". That judgement is
refuted by the git history.**

**The specific condition `G2-NAME-012` tests** (`enrichment/issue_detection.py:830-834`):

```python
if (
    looks_like_university_or_research_institute(record.name_1)
    and is_blank(record.name_2)
):
    found.add("G2-NAME-012")
```

Two conjuncts: Name 1 matches the *narrow* university-or-research signal — deliberately narrower than
the general research-institution test "so clinical orgs (hospitals, clinics, medical centres) — which
routinely have no department — are not flagged" (`:816-818`) — and **Name 2 specifically** is blank.
No other slot is consulted.

**The history.** The whole-block rule the test asserts existed and then was removed:

| Commit | Date | Rule body | Test present? |
|---|---|---|---|
| `b8ad102` | 2026-08-20 | `no_department = all(is_blank(v) for v in dept_values)`; `if looks_like_…(name_1) and no_department` | **added in this commit** |
| `8d5f5f9` … `8a68c77` (6 commits) | 2026-08-20 | unchanged, whole-block | unchanged |
| **`d4fc469`** (HEAD) | 2026-08-20 | `and is_blank(record.name_2)` | unchanged |

(`git show <sha>:enrichment/issue_detection.py`, grepped for the rule body at each revision;
`git log --diff-filter=A -- tests/test_name_slot_parity.py` → `b8ad102`, and that is also the file's
only commit.)

So the sequence is: `b8ad102` generalised the rule to the whole block and added the test asserting the
generalisation; six commits later, **HEAD reverted the rule to Name 2 alone** and did not update the
test. The test encodes the superseded behaviour.

**The revert is deliberate and carries a written rationale** (`enrichment/issue_detection.py:820-829`),
which addresses precisely the case the test constructs:

> "Name 2 alone, per the catalogue definition, and not 'no department anywhere in the block': scanning
> the whole block suppressed the code whenever a department sat in the wrong slot (Yale University with
> Name 2 blank and Name 3 'Department of Chemistry'), which is precisely the record a steward most
> needs to see. The misplacement is a separate fact reported by its own code (G1-NAME-004, 'Empty field
> in between populated name fields'); letting it mask this one loses the report that Name 2 — the slot
> SAP and every downstream consumer reads a department from — is empty. The two codes fire together on
> such a record, which is correct: they state two different things about it."

The test's fixture is that record: `Name 1="Stanford University"`, `Name 2=""`, `Name 3="Department of
Genetics"` (`tests/test_name_slot_parity.py:175-179`). Its docstring — "a department in Name 3 with a
blank Name 2 was reported as missing. It is not missing" (`:173-174`) — is the position the code
explicitly rejects. The failing assertion's own error message confirms the comment's prediction:
`G1-NAME-004` is present in the returned list alongside `G2-NAME-012` (`11_DELTA.md` §5), so the two
codes do fire together, exactly as intended.

**Two further pieces of evidence that the code is the current position and the test is not.** The
catalogue entry's `field` is `"Name 2"` (`:273`), and `G2-CONTACT-008`'s withdrawal reason describes
`G2-NAME-012`'s gate in the singular — "Its gate was identical to G2-NAME-012's" — for a rule whose own
gate is `name_2` blank plus contact absent (`:219-226`).

**Verdict: the test is wrong.** The correct repair is to invert the assertion and rename the test —
`G2-NAME-012` *should* be raised on that record, together with `G1-NAME-004` — or to delete it and
assert the pairing instead. Fixing the code to satisfy the test would re-introduce the masking defect
`d4fc469` removed.

**One caveat on the commit record.** `d4fc469`'s message describes German street types and the
`G2-VAL-004` predicate removal at length and **does not mention `G2-NAME-012` at all**. The rationale
exists only as a code comment. That is why `11_DELTA.md`, which used commit messages "only to locate
changes" (§Method), did not find it.

This also resolves `11_DELTA.md` §6 U-7 for this rule: the Name-2 literal in `G2-NAME-012` is a
deliberate exception to the five-slot generalisation, not an unfinished sweep. Whether the remaining
`Name 2` literals in the module are also deliberate is still not determinable.

### 9.2 · `test_issues_compare_segments_g6_and_g7_out_of_the_metric` — **the test is wrong**

**Failing assertion** (`tests/test_routes.py:529-530`):

```python
# The reduction block sees only the G1 defect: 1 before, 0 after, 100%.
assert summary["Reduced: issues before"] == 1
E       assert 2 == 1
```

**The cause, determined.** `11_DELTA.md` §6 U-3 left this open between two hypotheses: an unanticipated
second G1–G5 code, or a mis-segmentation in `segment()`. It is the first. Running `detect_issues` over
the test's own "before" fixture with the `present_fields` its headers produce:

```
BEFORE codes: ['G1-CROSS-001', 'G5-NAME-001', 'G2-VAL-003', 'G2-VAL-006']
  G1-CROSS-001  G1  Address Content in Name Field           reducible
  G5-NAME-001   G5  Organisation Name Not in Official Form  reducible
  G2-VAL-003    G6  Tax Jurisdiction Missing
  G2-VAL-006    G6  Language Missing
AFTER codes: ['G2-VAL-003', 'G2-VAL-006', 'G7-VERIFY-001']
```

The second reducible code is **`G5-NAME-001`, fired by the fixture's own `Name 1 = "Acme Corp"`**.
`Corp` is an abbreviated legal suffix and those are in the G5 abbreviation set by design. The comment
records the change, its motivation, **and this exact consequence in advance**
(`enrichment/issue_detection.py:428-438`):

> "Abbreviated legal suffixes (Corp, Inc, Ltd) are in the set. They were absent before, while 'Co'
> was present, so 'Smith Co.' fired the rule and 'Smith Corp.' did not — an inconsistency rather than
> a decision. … Note the consequence before reading a count: most commercial customers carry a legal
> suffix, so G5-NAME-001 volume rises substantially on real data. That is the honest reading of the
> rule as written; if the volume is unwanted the fix is to split legal suffixes into their own code,
> not to go back to excluding them silently."

The fixture's "after" Name 1 is `"Acme Corporation"`, the expanded form, so `G5-NAME-001` clears. The
same comment explains §7's `G5-NAME-001` rate of 101/500 (20.2%), the fourth-commonest code in the raw
workbook.

**`segment()` is correct.** Every code lands in the right block: `G1-CROSS-001` (G1) and
`G5-NAME-001` (G5) → `Reduced`; `G2-VAL-003`, `G2-VAL-006` (G6) → `Expected to persist`;
`G7-VERIFY-001` (G7) → `Verification` (`api/routes.py:494-501`). The test's own three
row-classification assertions (`tests/test_routes.py:548-550`) pass on exactly that basis.

**The other assertions in the test are unaffected.** `Reduced: issues after` is 0, so
`Reduction %` is still `100.0` — the reduction the test means to demonstrate is genuinely 100%, over
two codes rather than one. The G6 and G7 assertions (`:534-539`) hold. The only false statement is the
`== 1`, and the comment above it ("The reduction block sees only the G1 defect").

**Verdict: the test is wrong.** The test author chose a fixture Name 1 that itself trips a G5 rule and
did not anticipate it. The repair is one character (`== 2`) plus the comment; changing the code would
mean either removing `Corp` from the abbreviation set — reintroducing the `Smith Co.` / `Smith Corp`
asymmetry the comment records fixing — or mis-segmenting G5 out of `Reduced`, which would contradict
§4.

**Both failures are therefore stale assertions, not regressions.** Neither is a defect in shipped
behaviour, and `11_DELTA.md` §3.11 G-80 ("Two tests that passed at the baseline now fail") should be
restated: both tests were *added* in this range and neither ever passed against the code as it stands
at HEAD. The suite line `5 failed, 1773 passed` (`11_DELTA.md` §5) is unchanged in count; two of the
five are now attributed.

---

## §10 · Orchestration state

`11_DELTA.md` §1.10 records that no ADF pipeline JSON changed in the range and that the three
`sql/*.sql` files are verbatim exports. Both confirmed, and one thing is worth stating first because it
determines every answer below.

**There is no ADF artefact in the repository.** `git ls-files` returns no pipeline, dataset, linked-
service or trigger JSON anywhere in the tree; the only `.json` files are `.vscode/*`, `host.json` and
`dedup/weights.json`. The single representation of either ADF pipeline is the verbatim export quoted
inside `docs/thesis/CONTEXT-EXTERNAL.md` (Enrichment at `:39-186`, Deduplication at `:201-317`), both
carrying `"lastPublishTime": "2026-07-29T12:09:37Z"`. That file entered the tree in commit `3f5a28d`
("Thesis document", 2026-08-19) and has not been modified since:
`git log 515cc7c..HEAD -- docs/thesis/CONTEXT-EXTERNAL.md sql/` returns `ecac51a` and `3f5a28d`, and
the range diff is `451 insertions(+), 0 deletions`, i.e. all four files were *added* and none was
edited.

The five planned amendments are recorded at `CONTEXT-EXTERNAL.md:194-197`: "**[AUTHOR]** This export
predates the freeze. Before 2026-08-21 the pipeline is to be amended to add a group-code predicate to
both Lookups, an `enriched_at` watermark so `Lookup1` selects only unenriched rows, and a retry policy
above 0 on `Web1` and `Merge Back`."

| # | Change the thesis lists as a limitation | Landed? | Evidence |
|---|---|---|---|
| 1 | A group-code predicate on any Lookup | **NOT LANDED** | No Lookup activity exists in the working tree. The only Lookup definitions are the July-29 export in `CONTEXT-EXTERNAL.md`, unmodified in this range; the Deduplication `Lookup1`'s `sqlReaderQuery` reads `SELECT Customer AS row_id, [Block ID] AS block_id, [Name 1] AS name1, …` with no group-code clause, and the Enrichment `Lookup2`/`Lookup1` pair still generates and consumes 50-row offsets (`CONTEXT-EXTERNAL.md:188-189`) |
| 2 | An `enriched_at` column or watermark predicate | **NOT LANDED** | `enriched_at` and `watermark` occur nowhere in any `.py`, `.sql` or `.json` file. Every occurrence in the tree is inside `docs/thesis/*` describing the absence — `06b_CROSSCUTTING.md:465` ("There is no `enriched_at` watermark in the pipeline as exported — it is a planned…"), `05_DATA_MODEL.md:496`, `08_GAPS.md:491-493` (G-32), open items 109 and 112 (`00_OPEN_ITEMS.md:494`, `:497`) |
| 3 | Deduplication batching by block | **NOT LANDED** | The Deduplication pipeline in the export is unchanged, and its `Lookup1` still issues one unbatched `SELECT` over the Validation source. `00_OPEN_ITEMS.md:301` still lists "the unbatched dedup Lookup" among the outstanding items. No batching-by-block construct exists in the tree |
| 4 | A retry policy above 0 | **NOT LANDED** | Every activity policy in both exported pipelines reads `"retry": 0` — three occurrences in the Deduplication pipeline (`Lookup1`, `Web1`, `Merge Back`), each `{"timeout": "0.12:00:00", "retry": 0, "retryIntervalInSeconds": 30, "secureOutput": false, "secureInput": false}`. `CONTEXT-EXTERNAL.md:192` states the same for the Enrichment pipeline: "Every activity has `retry: 0` and a 12-hour timeout" |
| 5 | An `@entity` parameter on any stored procedure | **NOT LANDED** | `grep -oE "@[A-Za-z_]+" sql/*.sql \| sort -u` returns exactly three lines, one per file, all `@payload`. `usp_merge_legacy_enriched`, `usp_merge_validation_clusters` and `usp_merge_validation_scores` each take a single `@payload NVARCHAR(MAX)` and nothing else. `@entity` occurs nowhere in the repository |

**All five: NOT LANDED.** Every limitation the thesis currently states on this list stands, and none
needs removing.

Two things that *did* change in this range and should not be mistaken for any of the five: three
stored-procedure bodies became verbatim in-repo artefacts under `sql/` (commit `ecac51a`), upgrading
`02_ARCHITECTURE.md` §5's "external, unexported" characterisation and `CONTEXT-EXTERNAL.md`'s
`[AUTHOR]` description of the procedures to `[EXPORT]`-grade evidence; and a third procedure,
`usp_merge_validation_scores`, is now in the repository although nothing evidences it being wired in
ADF (`11_DELTA.md` §4, fig-01). Neither is an orchestration change.

⚠ The date in `CONTEXT-EXTERNAL.md:194` — "Before 2026-08-21 the pipeline is to be amended" — is
today. The amendment window closes at the freeze and none of the five has landed. This is a statement
about the artefacts in this repository; whether the deployed factory in the Tillit tenant differs from
its 2026-07-29 export is not determinable from here, and a re-export is the only thing that would
settle it.

---

Pass 12 complete. Design rationale recorded for seven subsystems, with `⚠ RATIONALE NOT IN REPO`
against three specific questions (why five name slots; why `routing_type_mismatch` is not re-run; why
the reduction denominator was narrowed rather than a residual reported) (§1, §4.2). Eleven flag codes
specified with condition, scope and retractability, plus a twelfth that can reach the column, and the
rendered `flag_reason` grammar quoted from source (§2). G6 and G7 defined, with the full 38-row
catalogue at HEAD (§3). `REDUCIBLE_GROUPS`: the G7 exclusion has a recorded soundness argument, the G6
exclusion does not, and the contradiction with `07_EVALUATION.md:93-98` is stated (§4). Batch consensus
specified end to end with a two-record worked example, and the loss of per-record independence stated
with its three consequences and a residual risk (§5). Tier 2A verification specified with its gate,
selector, three bands, confidence split and a worked example per band (§6). `scripts/ch02_measure.py`
run and pasted verbatim — it **aborts at §3.4 with exit 1**, so three of its six sections never ran and
no figure in the run derives from the enriched workbook; all 13 workbook exemplars still resolve at
their cited rows with their cited values (§7). The merge-back gap is **ten** derived columns, not nine
— `Error` is the tenth — and breaks no recorded DATAshaper rule or view (§8). Both new test failures
are **stale assertions, not defects**: `11_DELTA.md`'s judgement on `test_name_slot_parity` is refuted
by `d4fc469`'s deliberate, documented revert (§9). All five orchestration changes are **NOT LANDED**
(§10). No file outside `docs/thesis/12_RATIONALE.md` was modified. Stop.

---

# Pass 13 — Phase 2 clustering v2 (flagged): recorded rationale

Appended during the v2 change (`DEDUP_V2_BLOCKING` / `DEDUP_V2_NAME2` /
`DEDUP_V2_ID_CONFLICT`). Entries are recorded as they are decided; the full delta is
`docs/13_CLUSTERING_DOSSIER.md` § v2 (flagged).

**The entity definition.** Stated once, in full, because every rule below is a consequence of
it:

> The **institution** is the legal entity that would be invoiced.
> An **entity** is *(institution, delivery point, department-if-any)*.
> Logistics, admin, alias, overflow and contact text is **not** a department.
> A shared ROR identifies the **family**, not the entity.
> **Review** means a contradiction or an admitted uncertainty — never "we did not look".

Each clause earns its place against a failure in the 200-row stress batch.

*The institution is the legal entity that would be invoiced.* It is what separates `Utwmc LLC`
from The University of Texas Southwestern Medical Center, and `Covia Corp` from `Covia
Holdings LLC` — the first pair are two legal entities and the second is one written two ways.
Without a definition the question "same institution?" has no answer to be right or wrong
about.

*An entity is (institution, delivery point, department-if-any).* The delivery point is the
door, not the building and not the suite: `Building` is bound as a hint and reaches neither
blocking nor the signature key (`api/routes.py`), because two records in one building are not
thereby one entity and two in different buildings at one street address are not thereby two.

*Logistics, admin, alias, overflow and contact text is not a department* (`dedup/name_slots.py`).
A record whose Name 2 reads "Central Receiving" names no department, and the deterministic
asymmetry rule — which forbids a departmental record from sharing an entity with a bare one —
was being applied correctly to a false premise. The rule was never the defect; the premise was.

*A shared ROR identifies the family, not the entity.* EMD Serono, Inc. and EMD Serono Research
and Development Institute, Inc. carry one ROR and are two companies. So the registry id links
and does not merge, and where it disagrees with the model the pair goes to a human rather than
either source silently winning.

*Review means a contradiction or an admitted uncertainty.* v1 routed 10 rows to review, 5 of
them by exploding a single entity into singletons — which is not a question, it is a shrug
recorded as one. v2 routes 17: nine where deterministic evidence and the model disagree, six
where a cluster rests on an address nobody can verify, two where two registry ids conflict.
Every one of them is a sentence a steward can act on.

**Why a two-word department is not read as a person.** Mistaking a department for a person
destroys a distinction; the reverse only fails to merge. "Fairchild Science" has the shape of
a first and last name and is a Stanford building's department: read as a contact its
department empties and the record merges into the two bare "Stanford University" rows at the
same door. The contact rule therefore requires a separator as evidence — "Emanuela Zacco -
LCA Core" qualifies, a bare two-word value never does — and requires the person-shaped head
to share no token with the record's own Name 1, which is what a unit does and a contact does
not (`dedup/name_slots.py::_is_contact`).

**The rebuilt name is selected from the block, never composed.** When two slots hold pieces
of one institution's name (`institution_split`), the institution written is the full spelling
another record in the block already states — not the two fragments concatenated. A composed
name ("EMD Serono Research Institute, Inc. Research and Development Institute") is a third
spelling that no record states and no registry holds, and it matches nothing. Both original
slot values are kept as **hints**, not aliases: a fragment of a split name is not another
name for the whole institution, and filing it as an alias let "EMD Serono, Inc." — half of the
institute's name, and separately the entire name of a different company at the same address —
be matched against that company, merging the company into its own research arm.

**United States Gypsum Company vs USG Corporation, Inc. — acronym + legal-form evidence,
model merges; address-less, so cluster routes to review.** The pair briefly sat in the
review-link table on the strength of a p2-dedup-v7 hesitation that turned out to be an
artefact of one prompt sentence; with that sentence reverted the model merges it under both
v6 and v8 with consistent reasoning, and the expectation went back to MUST_MERGE. Neither row
names a usable delivery point, so the cluster routes to review by the address-less rule — the
same shape as Lee Memorial Health System.

**Utwmc LLC vs UT Southwestern Medical Center — acronym evidence, different legal-entity
form; steward decision by design.** The acronym rule fires (UTSM/UTWMC, JW 0.83) and the
model declines the merge because an LLC is a real corporate distinction. Both are right. The
pair is therefore a **link for review**: a shared Link ID, `Routing = manual_review`, and no
Cluster ID. It was moved out of MUST_MERGE for that reason — the expectation, not the
behaviour, was wrong. This is the third outcome the output needed: before it, a pair that is
one organisation and two records had to be reported either as a duplicate (overstating) or as
unique (losing the finding).

**Link ID is computed independently of the merge outcome, and across blocks.** Deriving it
from the clustering would make it say nothing the Cluster ID does not already say, and the
pairs worth linking are exactly the ones that did NOT merge. Two signatures share a Link ID
when a registry id is shared, or when a deterministic `evidence` line fires and the model
called the institutions the same or was uncertain. An institution family is not a property of
one delivery point, so the id spans blocks: HGST at Great Oaks Pkwy and HGST at Yerba Buena Rd
are one organisation.

**Review is for disagreement, not for relationship.** `Routing = manual_review` is set when a
deterministic `evidence` line or a shared registry id says one organisation and the model says
"different" — a conflict between two sources that both have standing, which is the one thing a
steward must adjudicate — or when the model itself says "uncertain". Same institution,
different department, model agreeing (Stanford/Fairchild, Army/Devcom, Merck/MRL) is a Link ID
and `unique`: nothing is in doubt, and sending it to a human would bury the four real
questions in this batch under dozens of statements of the obvious.

---

## Added in Pass 14 — Phase 2 scoring: why these six bands moved

`11_DELTA.md` §14 records *what* changed and the measured effect. This records *why*, and
marks the two places where the decision departs from Bernd's literal words.

### 14(a) · The count and equipment bands start at 1, because the source encodes "none" as NULL

**The deviation, stated plainly.** Bernd said "0 to 3 is 5%" (BerndScoring1 19:24). The bands
now start at 1. This also reverses a documented decision: `test_absence_is_not_activity`
previously pinned `equipment_count=0 → 5`, and the dossier described the resulting asymmetry as
intentional.

**Why the data overrides the transcript here.** The click report never writes a zero. Across
all 22,224 rows of `US_Qlic report data_2026-07-30.xlsx` the minimum value is **1** in all
three columns — `Sales Order Total Count` (min 1, max 4,478, 8,042 non-null),
`Sales Order Partner Total Count` (min 1, max 13,389, 15,493 non-null) and
`Equipment Total Count` (min 1, max 1,945, 4,208 non-null) — with **zero occurrences of a
literal 0** in any of them. "None" is encoded as NULL.

Under the old bands the two encodings of the same fact scored differently: a blank scored 0
while a literal `0` scored 5. Nothing in the source distinguishes them, so the five points were
awarded on a spelling. Starting the bands at 1 makes the first band mean *"has activity"*,
which is the only thing the data can actually support. Bernd's "0 to 3" describes a range whose
lower endpoint his own report cannot produce; the change preserves his intent (a low-activity
tier worth 5) and drops an endpoint that only ever fired on dirt.

**What this does not settle.** Whether these counts are lifetime totals or within-year counts
is a different question and still open — P2-21. If they turn out to be lifetime totals the
tiers need recalibrating regardless of where they start.

### 14(b) · `sleeping_customer` becomes two-valued, and `Yes` scores a *known* zero

Retiring the `3-4` and `>5` tiers (BerndScoring1 19:56-20:21) is a model change, not a bug fix,
and it is recorded as such in `00_OPEN_ITEMS.md` (P2-22) pending Bernd's confirmation for
non-US extracts.

The design point worth recording is the zero. `Yes` carries an **explicit** `0` band rather
than being left to fall through unmatched, because `_match_label_band` (`dedup/scoring.py:769`)
appends an `"unrecognized"` warning for any value with no band. A known value scoring a known
zero is not an anomaly, and must be as quiet as `blocked: 0` and the old `>5: 0` were. The
warning channel is reserved for values the table has never heard of; diluting it with routine
zeros is how a warning column stops being read. `test_known_zero_bands_do_not_warn` pins this.

### 14(c) · `MLIEF` → `LIEF`, and why `account_group`'s silence is deliberate

`LIEF` occurs 1,023 times in `CA_EXPORT.xlsx`, 662 in `FL_EXPORT.xlsx` and 284 in
`TX_EXPORT.xlsx` (also 271 MA, 235 NJ, 171 OH, 89 MI). `MLIEF` occurs **nowhere in any file in
the project**. Bernd spelled it "M-L-I-E-F" (BerndScoring1 22:25) — the same class of
transcription slip as DRID/DRIT, which live SAP already settled in favour of `DRIT`.
`_match_label_band` splits a label on `/` and tests every alternative, so `"0005/LIEF/MLIEF"`
keeps `MLIEF` covering a hypothetical non-US extract at no cost.

**`account_group` is the one label field with `warn_unknown=False`** (`dedup/scoring.py:907`),
so it is also the one field where a miss is completely silent — which is exactly how a whole
column can fail on its name alone and never say so. That silence is kept, and it is deliberate:
per Bernd, "give only the values to the ones we already have defined" (BerndScoring2 9:15),
i.e. `DBRU`/`Dios` and anything else parked are *expected* zeros, not anomalies. The cost of
that decision is that the header-binding defect below produced no diagnostic at all.

### 14(d) · Why the ladders became relative rather than being re-instantiated

Bernd described the rule relatively — "sales order last year, last two years, last three years"
(BerndScoring1 15:00) — and only then instantiated it as 2026/2025/2024/2023. The instantiation
was recorded and the description was not, so the table encoded a snapshot of the date it was
written. Re-instantiating it for 2027 would work and would have to be redone every January, by
someone who remembered; the failure mode if nobody does is not an error but a silent, uniform
zero on 90 of the 200 available points. Banding on the offset from a reference year encodes the
rule Bernd actually stated, and the reference year is stamped onto every scored row
(`scored_with_reference_year`) so the anchor a score was computed under is never in question.

The one place this could go quietly wrong is the ordering comparisons: `_cluster_year_maxima`,
the G1 recency gate and tie-break step 2 all compare years, and an offset inverts every one of
them. The offset is therefore computed inside the two ladder lookups only, and
`test_tiebreak_still_orders_on_the_absolute_year` fails if it ever leaks.

### 14(e) · Why a date is a year in two columns and dirt in every other

`_coerce_int` takes an explicit `allow_date` flag, passed `True` only for the two `*_last_used`
fields. The alternative — extracting `.year` from any date, anywhere — is smaller code and the
wrong behaviour: the count columns are plain integers, so a date landing in
`Equipment_Total_Count` is a broken upstream join, and silently reading it as ~2026 would hit
the `>15` band and award 30 points. Trading a silent zero for a silent thirty is not a fix. The
extraction logic lives entirely in `_coerce_int`; the call sites choose only whether a date is
*meaningful* for that field, which is a property of the column, not of the coercion.
