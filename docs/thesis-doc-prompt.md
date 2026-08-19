# Claude Code Prompt — Thesis Documentation Extraction

Save this into the repo as `docs/thesis-doc-prompt.md`, then in Claude Code say:

> Read `docs/thesis-doc-prompt.md` and execute Pass 0. Stop when done.

Run one pass per session. Approve each before moving on.

---

## PROMPT (paste from here if not using the file)

You are producing **thesis-grade source documentation** for this repository. The output
feeds a written Master's thesis. It is not a user-facing README and not marketing copy.

### Non-negotiable rules

1. **Evidence or nothing.** Every behavioural claim ends with a citation `path/file.py:LINE`
   or `path/file.py:LINE-LINE`. If you cannot cite it, do not write it.
2. **Read bodies, never infer from names.** A function called `validate_address` does not
   tell you what it validates. Open it.
3. **Code is ground truth.** Where code contradicts `README.md`, docstrings, Notion, or
   comments, the code wins — and you record the discrepancy in `08_GAPS.md` with both sides.
4. **No invented numbers.** No accuracy figures, record counts, runtimes, or costs unless
   you read them out of a file in this repo (test fixture, results CSV, log, config). If a
   number is needed but absent, write `⚠ MEASUREMENT REQUIRED` and state exactly which
   script or query would produce it.
5. **Constants verbatim.** Copy thresholds, weights, temperatures, timeouts exactly as
   written. Do not round, normalise, or "clean up".
6. **Mark unknowns loudly.** Use `⚠ UNVERIFIED —` prefix rather than smoothing over a gap.
   Silence about an unimplemented component is a defect; naming it is correct.
7. **Register.** Present tense, third person, no first person, no "we", no adjectives like
   "robust", "powerful", "seamless". Write like a methods section.
8. **Do not modify source code.** Documentation only.

### Output location

`docs/thesis/` — one file per pass, in order. Do not start a later pass early.

---

## Pass 0 — Inventory and call graph → `docs/thesis/00_INVENTORY.md`

Walk the whole repo. Produce:

- **File table:** path | LOC | one-line purpose | last-touched commit date.
  Exclude vendored/generated dirs; list what you excluded and why.
- **Entry points:** every HTTP route, CLI command, Azure Function binding, scheduled trigger.
  For each: method, path, request model, response model, handler `file:line`.
- **Call graph:** for each entry point, the ordered chain of functions it invokes down to
  the external-call or DB boundary. Mermaid `flowchart TD`, one per entry point.
- **Dead or unreferenced code:** modules/functions with no inbound call. List them; do not
  delete them.
- **Test inventory:** test file | what it covers | which source module | passing?
  Run the suite and paste the real summary line.

End with: "Pass 0 complete. N files, M entry points, K untested modules." Stop.

---

## Pass 1 — Requirements traceability → `docs/thesis/01_TRACEABILITY.md`

Build one table mapping every requirement to its implementation and its evidence:

| ID | Requirement (one line) | Implemented in | Test | Status |
|----|------------------------|----------------|------|--------|

- Source the IDs from whatever the repo already uses (use-case numbers, issue-catalogue
  rule codes and their group codes, endpoint contracts). List where you got them.
- Status ∈ `implemented` / `partial` / `not implemented` / `superseded`. For `partial`,
  state precisely which sub-behaviour is missing.
- A requirement with no test gets `Test: none`. Do not leave it blank.
- Add a second table for **behaviour present in code but not in any requirement list** —
  undocumented features. These matter for the thesis; they are usually where the real
  engineering happened.

Stop.

---

## Pass 2 — Architecture → `docs/thesis/02_ARCHITECTURE.md`

- **Component diagram** (Mermaid): services, data stores, external APIs, orchestration.
  Annotate each edge with protocol and payload shape.
- **Deployment topology:** what runs where, which resource, which config binds them.
  Cite the deployment/config files.
- **Runtime sequence** (Mermaid `sequenceDiagram`) for each major flow, from trigger to
  persisted result, including the failure/fallback branches — not just the happy path.
- **Boundary rationale:** for each component boundary, why it exists (separate cadence,
  separate scaling, separate failure domain, separate ownership). If the rationale is not
  evidenced in code, comments, or commit messages, write
  `⚠ RATIONALE NOT IN REPO — author to supply`.
- **State and idempotency:** what is persisted at each step, what happens on re-run,
  what is not idempotent.

Stop.

---

## Pass 3 — Algorithms → `docs/thesis/03_ALGORITHMS.md`

For **every decision procedure** in the codebase — escalation logic, blocking, candidate
generation, collapse rules, adjudication, scoring, tie-breaking, flagging — produce:

1. **Numbered pseudocode**, language-agnostic, matching the real control flow including
   early returns and exception paths. Cite the source range above each block.
2. **Inputs / outputs** with types.
3. **Complexity** in terms of the real loop bounds (records per block, candidates per
   record), not hand-waved.
4. **Worked example** using a real fixture or test case from the repo — cite the fixture
   file. Show intermediate values at each step. Do not construct a hypothetical example.
5. **Failure modes:** what input makes this produce a wrong answer, and what the code does
   about it.

This is the single most reusable pass for the thesis. Be exhaustive rather than readable.

Stop.

---

## Pass 4 — Parameters → `docs/thesis/04_PARAMETERS.md`

Every tunable value in the system, in one table:

| Parameter | Value | Type | Defined at | Consumed at | Effect if raised | Effect if lowered | Rationale |
|-----------|-------|------|-----------|-------------|------------------|-------------------|-----------|

Sweep: similarity thresholds, confidence cut-offs, scoring weights and their JSON/config
files, model deployment names, temperature, max tokens, retry counts, backoff, timeouts,
batch/page sizes, rate limits, feature flags, environment variables.

- Include env vars with their defaults and what breaks when unset.
- `Rationale` is filled **only** from code comments, commit messages, or config docstrings.
  Otherwise `⚠ UNDOCUMENTED — author to supply`. Do not reason a rationale into existence.
- Flag any parameter defined in more than one place with different values.

Stop.

---

## Pass 5 — Data model → `docs/thesis/05_DATA_MODEL.md`

- **Schemas:** every table, view, and Pydantic model — column/field, type, nullability,
  key, default. Cite DDL or model definition.
- **Lineage:** for each derived or written column, the exact code that computes it and the
  inputs it reads. One row per column; no gaps.
- **ER diagram** (Mermaid) covering real foreign-key or logical-join relationships.
- **Lifecycle:** for a single record, the sequence of states it passes through and which
  component writes each transition.
- **Retention and PII:** which fields are personal data, where they are logged, whether
  they are redacted. Cite the logging code.

Stop.

---

## Pass 6 — External dependencies → `docs/thesis/06_EXTERNAL_DEPS.md`

Per external service: endpoint(s) called, auth mechanism and where the secret comes from,
request/response shape, documented rate limits, retry/backoff implemented, timeout,
behaviour on failure (fail-open / fail-closed / fallback tier), caching (or explicitly
none), and cost model if evidenced in repo. Cite the client code for each.

Add a table of every third-party library with version from the lock/requirements file and
what it is used for — thesis appendices need this.

Stop.

---

## Pass 7 — Evaluation harness → `docs/thesis/07_EVALUATION.md`

- Locate every script, notebook, or test that produces a metric.
- For each metric: **its exact definition as computed in code** (numerator, denominator,
  what is excluded), cited. Not the definition you would expect — the one implemented.
- The **exact commands** to reproduce each number end to end, including required env vars
  and input data paths.
- The dataset(s) used: file path, row count read from the file, how the sample was selected
  if that is evidenced anywhere.
- **Threats to validity visible from the code:** leakage between tuning and evaluation
  data, metrics computed on a filtered subset, non-deterministic components (LLM calls)
  without fixed seeds or cached responses.
- Do **not** populate results. Leave a table with metric names and empty value cells.

Stop.

---

## Pass 8 — Gaps and limitations → `docs/thesis/08_GAPS.md`

- Components referenced in README/config/docs but **not implemented** — with the reference
  and the absence both cited.
- `TODO`, `FIXME`, `HACK`, commented-out blocks — quoted with location.
- Hardcoded values that should be configurable.
- Error paths that swallow exceptions silently.
- Every code↔doc discrepancy found in Passes 0–7.
- Explicitly scoped-out work, if scope decisions are recorded anywhere in the repo.

Frame each as a factual statement, not an apology. This becomes the thesis limitations
section and the future-work section.

Stop.

---

## Pass 9 — Decision log → `docs/thesis/09_DECISIONS.md`

Mine `git log` (full history, including messages and diffs of significant refactors),
ADRs if present, and substantive code comments. For each design decision:

| Decision | Date/commit | Alternatives visible in history | Why the chosen option | Evidence |

- "Alternatives visible in history" means: code that was written then removed, a config
  option that was deleted, a dependency added then dropped. Cite the commit.
- Where history shows *what* changed but not *why*, say so explicitly. A thesis needs the
  why, and you must not manufacture it.

Stop.

---

## Pass 10 — Figures → `docs/thesis/figures/`

Extract every Mermaid diagram produced above into its own `.mmd` file, named
`fig-NN-short-name.mmd`, plus `figures/INDEX.md` listing figure number, caption (one
sentence, thesis style), and which document it belongs to. Keep captions self-contained —
a reader should understand the figure without the body text.

Stop.

---

## Maintenance

After code freeze, re-run affected passes only. Each document must begin with:

```
Generated: <date> · Commit: <full sha> · Branch: <name>
```

If the commit in a document does not match `HEAD`, treat that document as stale.
