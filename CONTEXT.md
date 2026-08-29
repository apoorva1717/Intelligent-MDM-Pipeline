# Customer Master Data Enrichment

Cleans and enriches SAP customer master-data records — resolving the organisation a record names
against public registries and the web, and recording what may be believed about each value. The
vocabulary below is the project's, not general engineering vocabulary.

## Language

### Identity and evidence

**Registry**:
A public register of organisations that answers with an identifier — ROR, GLEIF, or Wikidata in
its crosswalked role. The only kind of source that can author a `verified` value.
_Avoid_: database, provider, API

**Witness**:
An independent source that agrees with a value another source produced. Independence means a
different evidence system: a page fetched from the domain it corroborates is one source, not two.
_Avoid_: confirmation, second opinion

**Confidence**:
How much weight a value carries, on exactly three levels — `verified`, `provisional`, `low`.
A property of the evidence, never of a model's self-report.
_Avoid_: score, certainty, high/medium/low

**Provenance**:
The record of which source produced a value and under what confidence, expressed as
`source:confidence[+witness]`. A value whose provenance cannot be reconstructed is not admissible.
_Avoid_: audit trail, lineage, history

**Scoped field**:
One of the six fields where a wrong value causes a wrong merge downstream, and which therefore
cannot be assigned — only written with evidence.
_Avoid_: protected field, tracked field

### Classification

**Record type**:
The final, single answer to what kind of organisation a record names. Decided once, from ranked
evidence, at the end of the run.
_Avoid_: type, category, org type

**Routing type**:
The provisional working answer used to decide which lane runs. Internal; never reaches the output.
_Avoid_: interim type, guess

### The agent lane

**Agent lane**:
The single bounded loop that handles records no registry lookup could settle, in which a model
plans retrieval over tools.
_Avoid_: Tier 3, LLM fallback, agent tier

**Planner**:
The agent's role — it chooses queries and tools. It has no authority to settle what a record says.
_Avoid_: resolver, decider

**Author**:
The source a written value is attributed to. A registry or a page can be an author; the model
never is.
_Avoid_: writer, origin

**Unverified write**:
A value reaching a scoped field without an independent source confirming it. The thing the system
guarantees cannot happen — distinct from a hallucination, which may occur freely inside the loop
and simply never reaches a field.
_Avoid_: hallucination, bad write

**Retrieval**:
Finding and ranking candidate answers. The agent's job.
_Avoid_: search, matching

**Verification**:
Deciding whether a candidate may be written. Never the agent's job, because a source that both
proposes and checks is one source.
_Avoid_: validation, confirmation

**Entry gate**:
The predicate deciding which records the agent lane sees — those with no confident registry
identity, whether by miss, weak match, or refused ambiguity.
_Avoid_: trigger, fallback condition

**Insufficient evidence**:
A terminal outcome in which the agent declines to propose. A success, not a failure.
_Avoid_: no result, failure, give up

### Measurement

**Reproducibility gate**:
The check that two runs of one batch produce identical output. It compares results, not execution
paths — a different route to the same registry-authored answer passes.
_Avoid_: determinism test, regression check

**Evidence cache**:
The record of what each external source answered, keyed on a pure function of the request. It
stores responses, never decisions.
_Avoid_: cache, store

**Coverage**:
The share of records leaving with a registry-authored identity. Distinct from precision, which
asks whether what was written is right.
_Avoid_: hit rate, success rate
